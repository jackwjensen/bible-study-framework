#!/usr/bin/env python3
"""Tests for sync.py's sign-in and its refusals, against sync_testserver.py.

The three-way cases are in test_sync.py; this file is everything about *who*
may sync and *where*: PKCE on loopback, token refresh and rotation, a revoked
sign-in, a server pointing the sign-in elsewhere, and the refusals that stop a
run once (consent, sync not configured). Runs under `python test_sync_login.py`
or `pytest`, like test_sync.py.
"""

from __future__ import annotations

import contextlib
import io
import sys
import threading
import urllib.parse
import urllib.request

import sync
import sync_state
from sync_http import SyncError, check_server_url, client_name_for, host_label, login
from test_sync import Env, finding, run_all, synced, with_env


@with_env
def test_login_uses_pkce_on_loopback_and_keeps_tokens_out_of_the_corpus(env: Env) -> None:
    env.store.delete(env.server.url)

    def browser(url: str) -> None:
        params = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        assert params["code_challenge_method"] == "S256" and params["scope"] == "study"
        assert params["resource"] == env.server.url + "/api/sync/v1"
        assert params["redirect_uri"].startswith("http://127.0.0.1:")
        code = env.server.issue_code(params)
        callback = params["redirect_uri"] + "?" + urllib.parse.urlencode(
            {"code": code, "state": params["state"]})
        threading.Thread(target=lambda: urllib.request.urlopen(callback, timeout=10).read(),
                         daemon=True).start()

    with contextlib.redirect_stdout(io.StringIO()):
        login(env.server.url, env.store, client_name="test", open_browser=browser, timeout=10)
    assert env.store.load(env.server.url)["access_token"] == env.server.access
    raw = (env.temp / "tokens").glob("*.json").__next__().read_text()
    assert env.server.access not in raw or sys.platform != "win32"    # sealed on Windows
    for path in env.root.rglob("*"):
        if path.is_file():
            assert env.server.refresh not in path.read_text(encoding="utf-8")


def approving_browser(env: Env, printed: io.StringIO | None = None, seen: list | None = None):
    """A browser that approves at once; records what was printed before it opened."""
    def browser(url: str) -> None:
        if printed is not None and seen is not None:
            seen.append(printed.getvalue())
        params = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        callback = params["redirect_uri"] + "?" + urllib.parse.urlencode(
            {"code": env.server.issue_code(params), "state": params["state"]})
        threading.Thread(target=lambda: urllib.request.urlopen(callback, timeout=10).read(),
                         daemon=True).start()
    return browser


@with_env
def test_login_names_this_computer_before_the_browser_opens(env: Env) -> None:
    env.store.delete(env.server.url)
    printed, seen = io.StringIO(), []
    with contextlib.redirect_stdout(printed):
        login(env.server.url, env.store, client_name=client_name_for("laptop"),
              open_browser=approving_browser(env, printed, seen), timeout=10)
    assert "list this computer as: bible-study-framework sync (laptop)" in seen[0]
    assert env.server.registered == ["bible-study-framework sync (laptop)"]
    assert env.store.load(env.server.url)["client_name"] == client_name_for("laptop")


@with_env
def test_a_new_name_registers_afresh_and_revokes_the_old_sign_in(env: Env) -> None:
    env.store.delete(env.server.url)
    with contextlib.redirect_stdout(io.StringIO()):
        login(env.server.url, env.store, client_name=client_name_for("a"),
              open_browser=approving_browser(env), timeout=10)
        old = env.store.load(env.server.url)
        login(env.server.url, env.store, client_name=client_name_for("a"),
              open_browser=approving_browser(env), timeout=10)
        assert len(env.server.registered) == 1                     # same name: reused
        login(env.server.url, env.store, client_name=client_name_for("b"),
              open_browser=approving_browser(env), timeout=10)
    assert env.server.registered[-1] == client_name_for("b") and len(env.server.registered) == 2
    assert env.store.load(env.server.url)["client_id"] != old["client_id"]
    assert old["refresh_token"] in env.server.revoked


@with_env
def test_a_record_from_before_names_were_kept_is_reused_under_the_host_name(env: Env) -> None:
    assert "client_name" not in env.store.load(env.server.url)      # Env's record is old-style
    with contextlib.redirect_stdout(io.StringIO()):
        login(env.server.url, env.store, client_name=client_name_for(host_label()),
              open_browser=approving_browser(env), timeout=10)
    assert env.server.registered == [] and env.server.revoked == []


@with_env
def test_the_name_survives_a_token_refresh(env: Env) -> None:
    record = env.store.load(env.server.url)
    env.store.save(env.server.url, {**record, "client_name": client_name_for("kept")})
    env.server.access = "access-9"                   # forces a refresh on the next call
    synced(env, "a")
    stored = env.store.load(env.server.url)
    assert stored["refresh_token"] == "refresh-2"                   # a refresh did happen
    assert stored["client_name"] == client_name_for("kept")


@with_env
def test_sync_login_name_flag_sets_the_label(env: Env) -> None:
    calls: list[dict] = []
    original = sync.http_login
    sync.http_login = lambda server, store, **kwargs: calls.append(kwargs)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            assert sync.main(["sync.py", "login", "st", "--name", " study laptop ", "--no-browser"],
                             root=env.root, store=env.store) == 0
            assert sync.main(["sync.py", "login", "st", "--no-browser"],
                             root=env.root, store=env.store) == 0
    finally:
        sync.http_login = original
    assert calls[0]["client_name"] == "bible-study-framework sync (study laptop)"
    assert calls[1]["client_name"] == client_name_for(host_label())


@with_env
def test_login_refuses_metadata_pointing_at_another_server(env: Env) -> None:
    env.server.foreign_metadata = True
    try:
        login(env.server.url, env.store, client_name="t", open_browser=lambda u: None, timeout=1)
    except SyncError as error:
        assert "refusing" in str(error)
    else:
        raise AssertionError("must not sign in at another origin")


@with_env
def test_a_rejected_access_token_is_refreshed_and_the_rotation_stored(env: Env) -> None:
    env.server.access = "access-9"                   # the server no longer takes ours
    env.server.refresh = "refresh-1"
    synced(env, "a")
    stored = env.store.load(env.server.url)
    assert stored["refresh_token"] == env.server.refresh == "refresh-2"


@with_env
def test_a_revoked_sign_in_asks_for_login(env: Env) -> None:
    env.server.access, env.server.refresh = "access-9", "refresh-9"
    env.write("a", finding("a"))
    try:
        env.run()
    except SyncError as error:
        assert "login" in str(error)
    else:
        raise AssertionError("a revoked sign-in must stop the run")


@with_env
def test_missing_consent_stops_the_run_once_and_moves_no_cursor(env: Env) -> None:
    env.server.consent = False
    for slug in ("a", "b", "c"):
        env.write(slug, finding(slug))
    try:
        env.run()
    except SyncError as error:
        assert "study storage" in str(error)
        assert "https://bible.example/?view=findings" in str(error)   # where to give it
    else:
        raise AssertionError("a consent refusal must stop the run")
    assert env.server.text("finding/a") is None
    env.server.consent = True
    code, outcome, _ = env.run()
    assert code == 0 and outcome.counts == {"created": 3}


@with_env
def test_a_server_without_sync_stops_the_run_and_writes_nothing(env: Env) -> None:
    env.server.available = False
    env.write("a", finding("a"))
    try:
        env.run()
    except SyncError as error:
        assert "not set up" in str(error)
    else:
        raise AssertionError("sync_unavailable must stop the run")
    assert not (env.root / ".sync" / "st.state.jsonl").exists()


def test_slugs_follow_the_protocol_rule() -> None:
    assert sync_state.slug_ok("a-b-2")
    for bad in ("-a", "a-", "a--b", "A", "a_b", "a" * 161, ""):
        assert not sync_state.slug_ok(bad), bad


def test_plain_http_only_to_this_computer() -> None:
    assert check_server_url("http://localhost:8081/") == "http://localhost:8081"
    assert check_server_url("https://sourcetext.ai") == "https://sourcetext.ai"
    for bad in ("http://sourcetext.ai", "https://sourcetext.ai/api", "ftp://x"):
        try:
            check_server_url(bad)
        except SyncError:
            continue
        raise AssertionError(bad)


if __name__ == "__main__":
    sys.exit(run_all(globals()))

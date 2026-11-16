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
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import sync
import sync_page
import sync_state
import user_secrets
from sync_http import (Client, NeedLogin, SyncError, check_server_url, client_name_for,
                       host_label, login)
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
def test_an_abandoned_sign_in_is_retried_without_registering_again(env: Env) -> None:
    before = env.store.load(env.server.url)
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(2):                                          # two sign-ins left unanswered
            try:
                login(env.server.url, env.store, client_name=client_name_for("new"),
                      open_browser=lambda url: None, timeout=1)
            except SyncError:
                pass
            else:
                raise AssertionError("an unanswered sign-in must fail")
        assert env.server.registered == [client_name_for("new")]   # registered once, not twice
        assert env.store.load(env.server.url)["access_token"] == before["access_token"]  # old works
        login(env.server.url, env.store, client_name=client_name_for("new"),
              open_browser=approving_browser(env), timeout=10)
    stored = env.store.load(env.server.url)
    assert env.server.registered == [client_name_for("new")]
    assert stored["client_id"] == "client-1" and stored["client_name"] == client_name_for("new")
    assert "pending_client" not in stored


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


def browser_that_reads_the_page(env: Env, pages: list, answer: dict | None = None):
    """A browser that answers the sign-in and keeps the page the listener sends back."""
    def browser(url: str) -> None:
        params = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        reply = answer if answer is not None else {"code": env.server.issue_code(params)}
        callback = params["redirect_uri"] + "?" + urllib.parse.urlencode(
            {**reply, "state": params["state"]})
        reader = threading.Thread(target=lambda: pages.append(
            urllib.request.urlopen(callback, timeout=10).read().decode("utf-8")), daemon=True)
        readers.append(reader)
        reader.start()
    readers: list[threading.Thread] = []
    browser.wait = lambda: [reader.join(10) for reader in readers]   # type: ignore[attr-defined]
    return browser


@with_env
def test_the_browser_is_told_signed_in_only_once_the_sign_in_is_stored(env: Env) -> None:
    env.store.delete(env.server.url)
    pages: list[str] = []
    browser = browser_that_reads_the_page(env, pages)
    with contextlib.redirect_stdout(io.StringIO()):
        login(env.server.url, env.store, client_name=client_name_for("desk"),
              open_browser=browser, timeout=10)
    browser.wait()
    assert env.store.load(env.server.url)["access_token"] == env.server.access
    page = pages[0]
    assert "You are signed in" in page and "bible-study-framework sync (desk)" in page
    assert "<!doctype html>" in page and "prefers-color-scheme:dark" in page
    assert env.server.access not in page and "code=" not in page


@with_env
def test_a_denied_sign_in_says_so_in_the_browser_and_in_the_terminal(env: Env) -> None:
    env.store.delete(env.server.url)
    pages: list[str] = []
    browser = browser_that_reads_the_page(env, pages, {"error": "access_denied"})
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            login(env.server.url, env.store, client_name="t", timeout=10, open_browser=browser)
        except SyncError as error:
            assert "not approved" in str(error) and "access_denied" in str(error)
        else:
            raise AssertionError("a denied sign-in must fail")
    browser.wait()
    assert "Sign-in was not approved" in pages[0] and "You are signed in" not in pages[0]
    assert not env.store.load(env.server.url).get("access_token")


@contextlib.contextmanager
def environment(**values: str | None):
    """Set (or, with None, remove) environment variables for the block, then restore them."""
    before = {name: os.environ.get(name) for name in values}
    try:
        for name, value in values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_secrets_are_kept_where_an_app_cannot_redirect_them() -> None:
    # An MSIX app (the Claude desktop app, a Store app) redirects %APPDATA% and
    # %LOCALAPPDATA% for every program it starts; the profile root it leaves alone.
    with environment(XDG_CONFIG_HOME=None):
        folder = user_secrets.user_dir("sync")
    assert folder == Path.home() / ".config" / "bible-study-framework" / "sync"
    for redirected in ("APPDATA", "LOCALAPPDATA"):
        if os.environ.get(redirected):
            assert not folder.is_relative_to(os.environ[redirected])


@with_env
def test_a_finished_sign_in_is_adopted_once_from_the_old_location(env: Env) -> None:
    record = env.store.load(env.server.url)
    old = sync_state.TokenStore(env.temp / "old")
    old.save(env.server.url, record)
    store = sync_state.TokenStore(env.temp / "new", legacy_directory=env.temp / "old")
    assert store.load(env.server.url)["access_token"] == record["access_token"]
    assert not list((env.temp / "old").glob("*.json"))             # moved, not copied
    assert sync_state.TokenStore(env.temp / "new").load(env.server.url) == store.load(env.server.url)


@with_env
def test_an_unfinished_old_sign_in_is_not_adopted_and_the_refusal_says_what_happened(
        env: Env) -> None:
    roaming, local = env.temp / "Roaming", env.temp / "Local"
    old_dir = roaming / "bible-study-framework" / "sync"
    started = time.mktime((2026, 11, 13, 15, 5, 0, 0, 0, -1))
    sync_state.TokenStore(old_dir).save(env.server.url, {"pending_client": {
        "client_id": "c-9", "client_name": "bible-study-framework sync (desk)",
        "started_at": started}})
    # The redirected copy an app keeps of that file — what its programs read instead.
    shadow = (local / "Packages" / "Some.App_abc123" / "LocalCache" / "Roaming"
              / "bible-study-framework" / "sync")
    shadow.mkdir(parents=True)
    for path in old_dir.glob("*.json"):
        (shadow / path.name).write_bytes(path.read_bytes())
    store = sync_state.TokenStore(env.temp / "new", legacy_directory=old_dir)
    with environment(APPDATA=str(roaming), LOCALAPPDATA=str(local)):
        try:
            Client(env.server.url, "bible", store)
        except NeedLogin as error:
            message = str(error)
        else:
            raise AssertionError("a sign-in that never finished must not count as one")
    assert not (env.temp / "new").exists()                         # nothing adopted
    assert '"bible-study-framework sync (desk)"' in message and "13 Nov 2026 15:05" in message
    assert "never stored here" in message and "Nothing is wrong on the server" in message
    assert "Some.App_abc123" in message and "sync.py run --dry-run" in message
    assert str(env.temp / "new") in message and message.endswith("python sync.py login")


@with_env
def test_never_signed_in_still_says_so_plainly(env: Env) -> None:
    store = sync_state.TokenStore(env.temp / "new", legacy_directory=env.temp / "none")
    assert store.why_not_signed_in(env.server.url) == (
        "not signed in to this server; run: python sync.py login")


@with_env
def test_an_abandoned_sign_in_records_when_it_started(env: Env) -> None:
    env.store.delete(env.server.url)
    before = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            login(env.server.url, env.store, client_name="t", open_browser=lambda url: None,
                  timeout=1)
        except SyncError:
            pass
    started = env.store.load(env.server.url)["pending_client"]["started_at"]
    assert before <= started <= time.time()
    assert "A sign-in as \"t\" was started" in env.store.why_not_signed_in(env.server.url)


def test_the_page_escapes_what_it_is_given() -> None:
    page = sync_page.render(sync_page.failed('<script>alert("x")</script>')).decode("utf-8")
    assert "<script>alert" not in page and "&lt;script&gt;" in page


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

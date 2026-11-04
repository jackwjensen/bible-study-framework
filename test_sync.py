#!/usr/bin/env python3
"""Tests for sync.py against an in-memory server (sync_testserver.py).

Runs two ways, like test_study_text.py: `python test_sync.py` with nothing but
the standard library, and `pytest` where a runner exists.

Every three-way case has a test, and each one checks BOTH sides afterwards —
a sync bug that loses an edit usually leaves the side it was looking at fine.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

import sync
import sync_state
from sync_http import Client, SyncError, check_server_url, login
from sync_plan import same_claim
from sync_state import TokenStore
from sync_testserver import CORPUS, FakeServer


def finding(slug: str, body: str = "The claim.", title: str = "A title") -> bytes:
    return (f"---\nid: {slug}\ntitle: \"{title}\"\nstatus: hypothesis\nclaim_type: asserted\n"
            f"x_unknown_key: survives\n---\n\n# {title}\n\n{body}\n").encode()


class Env:
    """A corpus folder, a token folder, and a running fake server."""

    def __init__(self):
        self.temp = Path(tempfile.mkdtemp(prefix="sync-test-"))
        self.root = self.temp / "corpus"
        for folder in sync_state.KIND_DIRS.values():
            (self.root / folder).mkdir(parents=True)
        self.store = TokenStore(self.temp / "tokens")
        self.server = FakeServer()
        sync_state.save_remotes(self.root, {"st": {"url": self.server.url, "corpus": CORPUS}})
        self.store.save(self.server.url, {
            "client_id": "client-1", "token_endpoint": self.server.url + "/token",
            "revocation_endpoint": self.server.url + "/revoke",
            "access_token": self.server.access, "refresh_token": self.server.refresh,
            "expires_at": None})

    def close(self) -> None:
        self.server.stop()
        shutil.rmtree(self.temp, ignore_errors=True)

    def path(self, slug: str, kind: str = "finding") -> Path:
        return sync_state.item_path(self.root, kind, slug)

    def write(self, slug: str, data: bytes) -> None:
        self.path(slug).write_bytes(data)

    def run(self, *, dry_run: bool = False) -> tuple[int, sync.Outcome, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            client = Client(self.server.url, CORPUS, self.store, sleep=lambda _: None)
            outcome = sync.Syncer(self.root, "st", client, dry_run=dry_run).run()
            code = sync.report(self.root, "st", outcome, dry_run=dry_run)
        return code, outcome, out.getvalue()


def with_env(test):
    def wrapped():
        env = Env()
        try:
            test(env)
        finally:
            env.close()
    wrapped.__name__ = test.__name__
    return wrapped


def synced(env: Env, slug: str, body: str = "The claim.") -> None:
    """Put one item on both sides, in agreement."""
    env.write(slug, finding(slug, body))
    code, _, _ = env.run()
    assert code == 0


# ── one side changed ─────────────────────────────────────────────────────────

@with_env
def test_first_sync_pushes_local_and_pulls_remote(env: Env) -> None:
    env.write("mine", finding("mine"))
    env.server.edit("finding/theirs", finding("theirs"))
    code, outcome, _ = env.run()
    assert code == 0, outcome
    assert outcome.counts == {"created": 1, "pulled": 1}
    assert env.server.received["finding/mine"] == finding("mine")      # byte for byte
    assert env.path("theirs").read_bytes() == finding("theirs")
    assert env.path("mine").read_bytes() == finding("mine")            # never rewritten
    assert env.run()[1].counts == {}                                   # and then quiet


@with_env
def test_local_edit_is_pushed(env: Env) -> None:
    synced(env, "a")
    env.write("a", finding("a", "Edited here."))
    code, outcome, _ = env.run()
    assert code == 0 and outcome.counts == {"pushed": 1}
    assert b"Edited here." in env.server.text("finding/a")


@with_env
def test_remote_edit_is_pulled(env: Env) -> None:
    synced(env, "a")
    env.server.edit("finding/a", finding("a", "Edited there."))
    code, outcome, _ = env.run()
    assert code == 0 and outcome.counts == {"pulled": 1}
    assert env.path("a").read_bytes() == finding("a", "Edited there.")


@with_env
def test_unknown_keys_survive_a_round_trip(env: Env) -> None:
    env.server.edit("finding/a", finding("a"))
    env.run()
    assert b"x_unknown_key: survives" in env.path("a").read_bytes()


@with_env
def test_line_endings_are_not_an_edit(env: Env) -> None:
    synced(env, "a")
    env.write("a", finding("a").replace(b"\n", b"\r\n"))
    assert env.run()[1].counts == {}


# ── deletions ────────────────────────────────────────────────────────────────

@with_env
def test_local_delete_deletes_on_server(env: Env) -> None:
    synced(env, "a")
    env.path("a").unlink()
    code, outcome, _ = env.run()
    assert code == 0 and outcome.counts == {"deleted on server": 1}
    assert env.server.text("finding/a") is None


@with_env
def test_remote_delete_deletes_here(env: Env) -> None:
    synced(env, "a")
    env.server.remove("finding/a")
    code, outcome, _ = env.run()
    assert code == 0 and outcome.counts == {"deleted here": 1}
    assert not env.path("a").exists()


@with_env
def test_delete_with_dependents_is_reported_not_forced(env: Env) -> None:
    synced(env, "a")
    env.server.dependents["finding/a"] = ["b"]
    env.path("a").unlink()
    code, outcome, _ = env.run()
    assert code == 1 and "b" in outcome.errors[0]
    assert env.server.text("finding/a") is not None


# ── both sides changed ───────────────────────────────────────────────────────

@with_env
def test_same_edit_on_both_sides_converges(env: Env) -> None:
    synced(env, "a")
    env.write("a", finding("a", "Same."))
    env.server.edit("finding/a", FakeServer.canonical(finding("a", "Same.")))
    code, outcome, _ = env.run()
    assert code == 0 and outcome.counts == {"already the same": 1}
    assert env.run()[1].counts == {}


@with_env
def test_different_edits_conflict_and_touch_neither_side(env: Env) -> None:
    synced(env, "a")
    env.write("a", finding("a", "Mine."))
    env.server.edit("finding/a", finding("a", "Theirs."))
    code, outcome, _ = env.run()
    assert code == 1 and len(outcome.conflicts) == 1
    assert env.path("a").read_bytes() == finding("a", "Mine.")
    assert env.server.text("finding/a") == finding("a", "Theirs.")
    remote_copy = sync_state.conflict_path(env.root, "finding", "a")
    assert remote_copy.read_bytes() == finding("a", "Theirs.")
    assert (env.root / ".sync" / "st.report.md").exists()
    assert env.run()[0] == 1                                   # still open, still non-zero

    env.write("a", finding("a", "Mine and theirs."))           # a person resolves it
    remote_copy.unlink()
    code, outcome, _ = env.run()
    assert code == 0 and outcome.counts == {"pushed": 1}
    assert b"Mine and theirs." in env.server.text("finding/a")
    assert not (env.root / ".sync" / "st.report.md").exists()


@with_env
def test_conflict_resolved_by_taking_the_server_copy(env: Env) -> None:
    synced(env, "a")
    env.write("a", finding("a", "Mine."))
    env.server.edit("finding/a", finding("a", "Theirs."))
    env.run()
    remote_copy = sync_state.conflict_path(env.root, "finding", "a")
    env.write("a", remote_copy.read_bytes())
    remote_copy.unlink()
    code, outcome, _ = env.run()
    assert code == 0 and outcome.counts == {}
    assert env.server.text("finding/a") == finding("a", "Theirs.")


@with_env
def test_edit_here_against_delete_there_is_a_conflict(env: Env) -> None:
    synced(env, "a")
    env.write("a", finding("a", "Mine."))
    env.server.remove("finding/a")
    code, outcome, _ = env.run()
    assert code == 1 and "deleted on the server" in outcome.conflicts[0]
    assert env.path("a").read_bytes() == finding("a", "Mine.")
    sync_state.conflict_path(env.root, "finding", "a").unlink()  # keep mine
    code, outcome, _ = env.run()
    assert code == 0 and outcome.counts == {"created": 1}
    assert b"Mine." in env.server.text("finding/a")


@with_env
def test_delete_here_against_edit_there_is_a_conflict(env: Env) -> None:
    synced(env, "a")
    env.path("a").unlink()
    env.server.edit("finding/a", finding("a", "Theirs."))
    code, outcome, _ = env.run()
    assert code == 1 and "deleted here" in outcome.conflicts[0]
    assert env.server.text("finding/a") == finding("a", "Theirs.")
    sync_state.conflict_path(env.root, "finding", "a").unlink()  # accept my deletion
    code, outcome, _ = env.run()
    assert code == 0 and outcome.counts == {"deleted on server": 1}


@with_env
def test_server_moving_during_the_push_is_a_conflict(env: Env) -> None:
    synced(env, "a")
    env.write("a", finding("a", "Mine."))
    env.server.before_put = lambda key: env.server.edit(key, finding("a", "Raced."))
    code, outcome, _ = env.run()
    assert code == 1 and len(outcome.conflicts) == 1
    assert env.server.text("finding/a") == finding("a", "Raced.")
    assert env.path("a").read_bytes() == finding("a", "Mine.")


@with_env
def test_create_on_both_sides_with_one_slug(env: Env) -> None:
    env.write("a", finding("a", "Mine."))
    env.server.edit("finding/a", finding("a", "Theirs."))
    code, outcome, _ = env.run()
    assert code == 1 and len(outcome.conflicts) == 1


# ── resuming, cursors, dry runs ──────────────────────────────────────────────

class Interrupted(Exception):
    pass


def _interrupt_after_first_change(env: Env) -> None:
    original = sync_state.SyncState.save
    sync_state.SyncState.save = lambda self: (_ for _ in ()).throw(Interrupted())
    try:
        env.run()
    except Interrupted:
        pass
    finally:
        sync_state.SyncState.save = original


@with_env
def test_an_interrupted_push_resumes_without_a_conflict(env: Env) -> None:
    synced(env, "a")
    env.write("a", finding("a", "Mine."))
    _interrupt_after_first_change(env)                      # pushed, base never saved
    code, outcome, _ = env.run()
    assert code == 0 and outcome.counts == {"already the same": 1}
    assert env.server.items["finding/a"]["version"] == 2    # pushed once, not twice


@with_env
def test_an_interrupted_pull_resumes_without_a_conflict(env: Env) -> None:
    synced(env, "a")
    env.server.edit("finding/a", finding("a", "Theirs."))
    _interrupt_after_first_change(env)                      # written, base never saved
    code, outcome, _ = env.run()
    assert code == 0 and outcome.counts == {"already the same": 1}


@with_env
def test_a_forgotten_cursor_falls_back_to_a_full_comparison(env: Env) -> None:
    synced(env, "a")
    synced(env, "b")
    env.server.forget_cursors = True
    env.server.edit("finding/b", finding("b", "Theirs."))
    code, outcome, text = env.run()
    assert code == 0 and outcome.counts == {"pulled": 1}, text
    assert env.path("a").exists() and env.server.text("finding/a") is not None


@with_env
def test_dry_run_changes_nothing(env: Env) -> None:
    env.write("mine", finding("mine"))
    env.server.edit("finding/theirs", finding("theirs"))
    code, _, text = env.run(dry_run=True)
    assert code == 0 and "push" in text and "pull" in text
    assert not env.path("theirs").exists()
    assert env.server.text("finding/mine") is None
    assert not (env.root / ".sync" / "st.state.json").exists()


# ── signing in ───────────────────────────────────────────────────────────────

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
        import threading
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


def test_plain_http_only_to_this_computer() -> None:
    assert check_server_url("http://localhost:8081/") == "http://localhost:8081"
    assert check_server_url("https://sourcetext.ai") == "https://sourcetext.ai"
    for bad in ("http://sourcetext.ai", "https://sourcetext.ai/api", "ftp://x"):
        try:
            check_server_url(bad)
        except SyncError:
            continue
        raise AssertionError(bad)


# ── "the same claim" ─────────────────────────────────────────────────────────

def test_same_claim_ignores_serialisation_but_not_content() -> None:
    mine = finding("a")
    theirs = (b"---\ntitle: A title\nid: a\nstatus: hypothesis\nclaim_type: asserted\n"
              b"x_unknown_key: \"survives\"\nupdated: 2026-11-04\n---\n\n# A title\n\nThe claim.\n")
    assert same_claim(mine, theirs, "a")
    assert not same_claim(mine, theirs.replace(b"hypothesis", b"developing"), "a")
    assert not same_claim(mine, theirs.replace(b"The claim.", b"The claim!"), "a")
    assert same_claim(b"---\nthemes: [x, y]\n---\nB", b"---\nthemes:\n  - x\n  - y\n---\nB", "a")


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"ok    {test.__name__}")
        except Exception as error:  # noqa: BLE001 (report every failure, not the first)
            failed += 1
            print(f"FAIL  {test.__name__}: {error!r}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

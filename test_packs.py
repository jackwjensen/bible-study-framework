#!/usr/bin/env python3
"""Tests for packs.py's feeds, and the per-user store that keeps their keys.

Runs two ways, like test_sync.py: `python test_packs.py` with nothing but the
standard library, and `pytest` where a runner exists. It does not import
test_sync.py: the sync modules are optional in a corpus, and packs.py is not.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import packs
import packs_archive
import packs_follow
from packs_feed import FeedError, GuardedRedirects, fetch, parse_link
from packs_testserver import Feed, FeedServer
from user_secrets import FeedKeyStore

FEED = "https://feeds.example/feeds/abc123/feed.json"
KEY = "k-7c1e9f-private"


def finding(slug: str) -> str:
    return f"---\nid: {slug}\ntitle: \"{slug}\"\nstatus: hypothesis\n---\n\n# {slug}\n\nA claim.\n"


class Env:
    """A corpus folder, a key folder, a feed server, and packs.py pointed at them."""

    def __init__(self):
        self.temp = Path(tempfile.mkdtemp(prefix="packs-test-"))
        self.root = self.temp / "corpus"
        (self.root / "findings").mkdir(parents=True)
        self.keys = FeedKeyStore(self.temp / "keys")
        self.server = FeedServer()
        self.output = ""                    # everything packs.py printed, all runs
        self._patched = [
            (packs_archive, "FINDINGS_DIR", self.root / "findings"),
            (packs_archive, "IMPORTED_DIR", self.root / "imported"),
            (packs_follow, "SUBSCRIPTIONS_FILE", self.root / "subscriptions.json"),
        ]
        self._saved = [(module, name, getattr(module, name)) for module, name, _ in self._patched]
        for module, name, value in self._patched:
            setattr(module, name, value)

    def close(self) -> None:
        for module, name, value in self._saved:
            setattr(module, name, value)
        self.server.close()
        shutil.rmtree(self.temp, ignore_errors=True)

    def run(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = packs.main(["packs.py", *args], keys=self.keys)
            except SystemExit as stop:
                code = int(stop.code or 0)
        self.output += out.getvalue()
        return code, out.getvalue()

    def subscriptions_text(self) -> str:
        path = self.root / "subscriptions.json"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def private_feed(self, code: str = "abc123", **extra) -> str:
        return self.server.add(Feed(f"/feeds/{code}", "Ann Author", "ann", key=KEY,
                                    title="Ann's notes", packs={"ann-gifts": ("2", {
                                        "gift-a": finding("gift-a")})}, **extra))

    def public_feed(self, prefix: str = "/feeds/pub1", author_id: str = "bo") -> str:
        return self.server.add(Feed(prefix, "Bo Writer", author_id, packs={
            f"{author_id}-all": ("1", {"b-one": finding("b-one")})}))


def with_env(test):
    def wrapped():
        env = Env()
        try:
            test(env)
        finally:
            env.close()
    wrapped.__name__ = test.__name__
    return wrapped


def with_temp(test):
    def wrapped():
        temp = Path(tempfile.mkdtemp(prefix="packs-test-"))
        try:
            test(temp)
        finally:
            shutil.rmtree(temp, ignore_errors=True)
    wrapped.__name__ = test.__name__
    return wrapped


# ── the key store ────────────────────────────────────────────────────────────

@with_temp
def test_a_feed_key_is_stored_and_read_back_by_address(temp: Path) -> None:
    store = FeedKeyStore(temp)
    assert store.get(FEED) is None
    store.put(FEED, "k-secret-1")
    assert FeedKeyStore(temp).get(FEED) == "k-secret-1"
    assert FeedKeyStore(temp).get(FEED.replace("abc123", "other")) is None


@with_temp
def test_a_feed_key_is_sealed_on_disk_on_windows(temp: Path) -> None:
    FeedKeyStore(temp).put(FEED, "k-secret-1")
    raw = (temp / "keys.json").read_text(encoding="utf-8")
    assert FEED in raw
    assert ("k-secret-1" not in raw) or sys.platform != "win32"


@with_temp
def test_removing_a_feed_key_forgets_only_that_feed(temp: Path) -> None:
    store = FeedKeyStore(temp)
    other = FEED.replace("abc123", "other")
    store.put(FEED, "k-1")
    store.put(other, "k-2")
    assert store.remove(FEED) is True
    assert store.remove(FEED) is False
    assert store.get(FEED) is None and store.get(other) == "k-2"


# ── links ────────────────────────────────────────────────────────────────────

def test_a_link_splits_into_an_address_and_a_key() -> None:
    link = parse_link(FEED + "#key=" + KEY)
    assert link.address == FEED and link.key == KEY
    assert parse_link(FEED + "#key=a+b/c%3D").key == "a+b/c="       # '+' is not a space
    assert parse_link(FEED + "#other").key is None
    assert parse_link(FEED + "#other").address == FEED


def test_a_link_is_refused_when_not_https_or_its_key_is_empty() -> None:
    for bad in ("http://feeds.example/feeds/a/feed.json", "ftp://x/feed.json", FEED + "#key="):
        try:
            parse_link(bad)
        except FeedError as error:
            assert KEY not in str(error)
            continue
        raise AssertionError(bad)
    assert parse_link("http://127.0.0.1:8081/feeds/a/feed.json").key is None


def test_only_a_feed_on_this_computer_may_lead_to_this_computer() -> None:
    remote = "https://feeds.example/feeds/a/feed.json"
    for local in ("http://127.0.0.1:9/x.zip", "https://localhost/x.zip", "http://[::1]:9/x.zip",
                  "https://127.0.0.2/x.zip"):
        try:
            fetch(local, feed=remote)                    # refused before any connection
        except FeedError as error:
            assert "this computer" in str(error), error
            continue
        raise AssertionError(local)
    request = urllib.request.Request(remote)
    request.feed_address = remote
    try:
        GuardedRedirects().redirect_request(request, None, 302, "Found", {},
                                             "http://127.0.0.1:9/feed.json")
    except FeedError as error:
        assert "this computer" in str(error)
    else:
        raise AssertionError("a remote feed must not redirect to this computer")


# ── feeds ────────────────────────────────────────────────────────────────────

@with_env
def test_a_public_feed_is_followed_and_pulled_without_a_key(env: Env) -> None:
    address = env.public_feed()
    assert env.run("subscribe", address)[0] == 0
    code, out = env.run("check")
    assert code == 0 and "not imported" in out
    assert env.run("pull", "--all", "-y")[0] == 0
    assert (env.root / "imported" / "bo" / "findings" / "b-one.md").exists()
    assert set(env.server.auth_seen("")) == {None}
    assert '"private"' not in env.subscriptions_text()


@with_env
def test_a_private_feed_is_followed_with_its_key_and_the_key_stays_out_of_the_corpus(env: Env) -> None:
    address = env.private_feed()
    code, out = env.run("subscribe", address + "#key=" + KEY)
    assert code == 0, out
    assert "Ann's notes" in out
    entry = json.loads(env.subscriptions_text())["feeds"][0]
    assert entry == {"url": address, "author": "Ann Author", "private": True}
    assert env.keys.get(address) == KEY
    assert env.run("pull", "--all", "-y")[0] == 0
    assert (env.root / "imported" / "ann" / "findings" / "gift-a.md").exists()
    assert env.server.auth_seen("/feed.json")[-1] == f"Bearer {KEY}"
    assert env.server.auth_seen("/ann-gifts.zip") == [f"Bearer {KEY}"]
    for path in env.root.rglob("*"):
        if path.is_file():
            assert KEY not in path.read_text(encoding="utf-8", errors="replace"), path
    assert KEY not in env.output


@with_env
def test_a_wrong_key_says_the_link_may_be_revoked_and_stores_nothing(env: Env) -> None:
    address = env.private_feed()
    code, out = env.run("subscribe", address + "#key=wrong-key")
    assert code == 1 and "revoked" in out and "wrong-key" not in out
    assert env.subscriptions_text() == "" and env.keys.get(address) is None


@with_env
def test_a_key_is_never_printed_even_by_an_argument_error(env: Env) -> None:
    address = env.private_feed()
    code, out = env.run("subscribe", address + "#key=" + KEY, "stray-argument")
    assert code != 0 and "stray-argument" in out
    assert KEY not in out


@with_env
def test_a_key_with_header_breaking_characters_is_refused_without_printing_it(env: Env) -> None:
    address = env.private_feed()
    for bad in ("SECRET%0D%0AX-Evil:%201", "SECRET%20two", "SECRET%00"):
        code, out = env.run("subscribe", address + "#key=" + bad)
        assert code == 1 and "SECRET" not in out, out
        assert "key" in out
    assert env.server.requests == [] and env.subscriptions_text() == ""
    try:                                     # a bad key already stored reaches fetch() directly
        fetch(address, feed=address, key="SECRET\r\nX-Evil: 1")
    except FeedError as error:
        assert "SECRET" not in str(error)
    else:
        raise AssertionError("a header-breaking key must be refused")


@with_env
def test_a_redirect_to_another_host_is_refused_while_a_key_is_in_play(env: Env) -> None:
    address = env.private_feed()
    env.server.redirects["/feeds/moved/feed.json"] = address.replace(env.server.url,
                                                                    env.server.other_host)
    code, out = env.run("subscribe", env.server.url + "/feeds/moved/feed.json#key=" + KEY)
    assert code == 1 and "another host" in out and KEY not in out
    assert env.server.auth_seen("/feeds/abc123/feed.json") == []    # never followed
    assert env.subscriptions_text() == ""


@with_env
def test_a_pack_on_another_host_is_fetched_without_the_key(env: Env) -> None:
    address = env.private_feed(pack_hosts={"ann-gifts": env.server.other_host})
    assert env.run("subscribe", address + "#key=" + KEY)[0] == 0
    env.run("pull", "--all", "-y")
    assert env.server.auth_seen("/ann-gifts.zip") == [None]


@with_env
def test_a_private_feed_on_a_second_computer_asks_for_its_link_and_the_run_goes_on(env: Env) -> None:
    private = env.private_feed()
    public = env.public_feed()
    assert env.run("subscribe", private + "#key=" + KEY)[0] == 0
    assert env.run("subscribe", public)[0] == 0
    env.keys.remove(private)                                         # the other computer
    before = env.subscriptions_text()
    code, out = env.run("check")
    assert code == 1
    assert "needs its link on this computer" in out and "python packs.py subscribe" in out
    assert "Bo Writer" in out                                        # the other feed still read
    code, out = env.run("subscribe", private + "#key=" + KEY)
    assert code == 0 and env.keys.get(private) == KEY
    assert env.subscriptions_text() == before                       # nothing else changed


@with_env
def test_a_missing_keyless_feed_says_it_does_not_exist(env: Env) -> None:
    code, out = env.run("subscribe", env.server.url + "/feeds/nothing/feed.json")
    assert code == 1 and "no feed at" in out and "not valid JSON" not in out


@with_env
def test_a_legacy_packs_address_still_works_without_a_key(env: Env) -> None:
    address = env.public_feed(prefix="/packs/old-name", author_id="old")
    assert address.endswith("/packs/old-name/feed.json")
    assert env.run("subscribe", address)[0] == 0
    assert env.run("pull", "--all", "-y")[0] == 0
    assert (env.root / "imported" / "old" / "findings" / "b-one.md").exists()


@with_env
def test_unsubscribe_removes_the_entry_and_the_key_and_keeps_the_findings(env: Env) -> None:
    address = env.private_feed()
    env.run("subscribe", address + "#key=" + KEY)
    env.run("pull", "--all", "-y")
    code, out = env.run("unsubscribe", address + "#key=" + KEY)
    assert code == 0 and KEY not in out
    assert json.loads(env.subscriptions_text())["feeds"] == []
    assert env.keys.get(address) is None
    assert (env.root / "imported" / "ann" / "findings" / "gift-a.md").exists()
    assert env.run("unsubscribe", address)[0] == 1


def _no_manifest_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("findings/x.md", finding("x"))
    return buffer.getvalue()


@with_env
def test_a_broken_pack_is_reported_and_the_feeds_after_it_are_still_pulled(env: Env) -> None:
    for prefix, author_id, raw in (("/feeds/bad1", "bad1", b"this is not a zip"),
                                   ("/feeds/bad2", "bad2", _no_manifest_zip())):
        address = env.server.add(Feed(prefix, "Broken", author_id, packs={
            f"{author_id}-all": ("1", {})}, raw_packs={f"{author_id}-all": raw}))
        env.run("subscribe", address)
    env.run("subscribe", env.public_feed())
    code, out = env.run("pull", "--all", "-y")
    assert code == 1, out
    assert "bad1-all" in out and "bad2-all" in out
    assert (env.root / "imported" / "bo" / "findings" / "b-one.md").exists()
    assert "1 pack(s) updated" in out


@with_env
def test_pulling_your_own_work_warns_and_still_imports(env: Env) -> None:
    (env.root / "findings" / "b-one.md").write_text(finding("b-one"), encoding="utf-8")
    env.run("subscribe", env.public_feed())
    code, out = env.run("pull", "--all", "-y")
    assert code == 0 and "looks like your own work" in out and "sync.py" in out
    assert (env.root / "imported" / "bo" / "findings" / "b-one.md").exists()


def run_all(namespace: dict) -> int:
    """The no-runner path: every test_* in a module, every failure reported."""
    tests = [value for name, value in sorted(namespace.items())
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
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all(globals()))

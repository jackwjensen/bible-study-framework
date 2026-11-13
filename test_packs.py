#!/usr/bin/env python3
"""Tests for packs.py's feeds, and the per-user store that keeps their keys.

Runs two ways, like test_sync.py: `python test_packs.py` with nothing but the
standard library, and `pytest` where a runner exists. It does not import
test_sync.py: the sync modules are optional in a corpus, and packs.py is not.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from user_secrets import FeedKeyStore

FEED = "https://feeds.example/feeds/abc123/feed.json"


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

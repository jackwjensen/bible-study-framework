"""Cases for standards_deps_cache: an hour for registry answers, forever for notes.

The cache is a convenience, so the cases that matter are the ones where it could pretend to be
more: an answer older than the hour must not be served, and an unreadable cache file must
behave as an empty one rather than stopping a push.

Run: python test_standards_deps_cache.py   (or pytest)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_deps_cache import (  # noqa: E402
    LOOKUP_TTL_SECONDS,
    add_note,
    cached_lookup,
    notes_for,
    store_lookup,
)
from standards_selftest import run_module_tests  # noqa: E402


def test_an_answer_is_served_only_within_the_hour() -> None:
    with tempfile.TemporaryDirectory() as tree:
        path = Path(tree) / "cache.json"
        store_lookup("pypi:ruff", {"newest": "0.16.10"}, now=1000.0, path=path)
        assert cached_lookup("pypi:ruff", now=1000.0 + LOOKUP_TTL_SECONDS - 1, path=path)["newest"] == "0.16.10"
        assert cached_lookup("pypi:ruff", now=1000.0 + LOOKUP_TTL_SECONDS, path=path) is None


def test_notes_accumulate_per_release_and_do_not_expire() -> None:
    with tempfile.TemporaryDirectory() as tree:
        path = Path(tree) / "cache.json"
        add_note("pypi:ruff", "0.17.0", {"by": "Jack", "text": "formatter changes"}, path=path)
        add_note("pypi:ruff", "0.17.0", {"by": "Claude", "text": "two new checks"}, path=path)
        assert [note["by"] for note in notes_for("pypi:ruff", "0.17.0", path=path)] == ["Jack", "Claude"]
        assert notes_for("pypi:ruff", "0.16.10", path=path) == []


def test_an_unreadable_cache_is_an_empty_cache() -> None:
    with tempfile.TemporaryDirectory() as tree:
        path = Path(tree) / "cache.json"
        path.write_text("{ torn", encoding="utf-8")
        assert cached_lookup("pypi:ruff", path=path) is None
        store_lookup("pypi:ruff", {"newest": "0.16.10"}, path=path)
        assert cached_lookup("pypi:ruff", path=path)["newest"] == "0.16.10"


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "dependency cache"))

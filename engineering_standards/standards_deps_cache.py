#!/usr/bin/env python3
"""The per-machine cache behind the dependency gate: registry answers and investigation notes.

A CONVENIENCE, NEVER AN AUTHORITY (self-contained-repo.md). Nothing a repository needs lives
here: a fresh clone on a machine with no cache asks the registries itself, and the decisions
that let a push through live in the repository's own `.standards-dependencies.json`. What the
cache buys is speed and shared knowledge -- one registry lookup an hour per package across every
repo on the machine, and an investigation of a release done once rather than once per repo.

Two kinds of entry, deliberately different in lifetime:

* A registry answer ("ruff's newest is 0.16.10") expires after LOOKUP_TTL_SECONDS. It is a fact
  about the world that goes stale, and the gate's whole promise is that it judged against a
  recent answer. An ERROR is never cached: a failed lookup must be retried, not remembered.
* A note ("0.17 changes formatter output for X") does not expire. It is a fact about a release,
  and a release does not change after it is published.

Written atomically (temp file + replace), so two repos pushing at once cannot leave a torn file;
a cache that cannot be read is treated as empty rather than as an error, because losing it costs
only a repeated lookup.

Source of truth: engineering-standards/engineering_standards/standards_deps_cache.py
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

CACHE_ENVIRONMENT = "STANDARDS_DEPS_CACHE"
LOOKUP_TTL_SECONDS = 3600  # decision 7 of the 2026-10-02 plan: one hour
CACHE_FILENAME = "dependency-cache.json"


def cache_path() -> Path:
    """Where this machine keeps the cache: an explicit override, else the platform's cache home."""
    override = os.environ.get(CACHE_ENVIRONMENT)
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    base = Path(local) if local else Path.home() / ".cache"
    return base / "engineering-standards" / CACHE_FILENAME


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {"lookups": {}, "notes": {}}
    data.setdefault("lookups", {})
    data.setdefault("notes", {})
    return data


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".cache-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=1, sort_keys=True)
    Path(temporary).replace(path)


def cached_lookup(key: str, now: float | None = None, path: Path | None = None) -> dict | None:
    """A registry answer younger than the TTL, or None."""
    entry = _load(path or cache_path())["lookups"].get(key)
    current = time.time() if now is None else now
    if entry and current - entry.get("fetched", 0) < LOOKUP_TTL_SECONDS:
        return entry
    return None


def store_lookup(key: str, answer: dict, now: float | None = None, path: Path | None = None) -> None:
    """Remember a SUCCESSFUL registry answer; errors are never stored."""
    target = path or cache_path()
    data = _load(target)
    data["lookups"][key] = {**answer, "fetched": time.time() if now is None else now}
    _save(target, data)


def notes_for(key: str, version: str, path: Path | None = None) -> list[dict]:
    """Investigation notes recorded on this machine for one release of one package."""
    return list(_load(path or cache_path())["notes"].get(f"{key}@{version}", []))


def add_note(key: str, version: str, note: dict, path: Path | None = None) -> None:
    """Append a note about one release; notes are shared by every repo on the machine."""
    target = path or cache_path()
    data = _load(target)
    data["notes"].setdefault(f"{key}@{version}", []).append(note)
    _save(target, data)

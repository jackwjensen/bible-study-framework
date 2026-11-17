#!/usr/bin/env python3
"""What a repository's pnpm configuration says about the versions it may install. Pure reading.

Two facts the dependency gate needs, both living in pnpm's own settings rather than in a
manifest's dependency blocks:

* THE RELEASE-AGE GUARD. `minimumReleaseAge` (minutes) makes pnpm refuse any version published
  more recently than that -- the defence against a hijacked maintainer account publishing a
  malicious patch, which is nearly always caught and unpublished within hours. pnpm 11 turns it
  on by default at 1440 minutes; pnpm 10 shipped it off. A release inside the window cannot be
  installed, so the gate must not block a push on it (Jack, 2026-11-11: "It is stupid to block
  push on something that we can not change"). `minimumReleaseAgeExclude` lists what the repo
  deliberately let through: a name, a glob (`@myorg/*`), `name@1.2.3`, or `name@1.2.3 || 1.2.4`.
* OVERRIDES. `overrides` forces a version on every copy of a package, so it is a declaration
  site exactly like a `package.json` entry. Unread, it let `package.json` say graphql 16.15.0
  while the override kept 16.14.2 installed, and `record ... updated` accepted it
  (allegro-it-services, 2026-11-11).

pnpm 11 reads its settings from `pnpm-workspace.yaml` only; pnpm 10 also read the `pnpm` field of
`package.json`, and npm and Yarn read `overrides` / `resolutions` there. All are read.

No YAML library: the pack is standard-library only. `pnpm-workspace.yaml` is read for exactly
the shapes pnpm documents for these keys -- a top-level scalar, a block or flow list, a block
mapping -- and anything else under them is a ValueError, said out loud by the caller rather than
read as "no guard".

Source of truth: engineering-standards/engineering_standards/standards_deps_pnpm.py
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path

WORKSPACE_FILE = "pnpm-workspace.yaml"
# pnpm's documented default from the major that turned the guard on by default.
DEFAULT_MINIMUM_AGE_MINUTES = {11: 1440}
PACKAGE_MANAGER = re.compile(r"^pnpm@(?P<major>\d+)\.")
TOP_LEVEL_KEY = re.compile(r"^(?P<key>['\"]?[\w@/.-]+['\"]?)\s*:\s*(?P<value>.*)$")
NESTED_ENTRY = re.compile(r"^\s+(?P<key>'[^']+'|\"[^\"]+\"|[^\s:'\"][^:]*?)\s*:\s*(?P<value>.+)$")
LIST_ITEM = re.compile(r"^\s*-\s+(?P<value>.+)$")  # YAML allows a block list at the key's own column


@dataclass(frozen=True)
class Override:
    """One `overrides` entry that names a literal version: a declaration site."""

    name: str
    version: str
    path: Path  # repo-relative
    line: int


@dataclass(frozen=True)
class ReleaseAgeGuard:
    """pnpm's minimum release age for this repository, and what it lets through."""

    minutes: int
    excluded: tuple[str, ...]
    source: str  # where the number came from, said in every notice

    def guards(self, name: str, version: str) -> bool:
        """Whether pnpm applies the age check to this release, or the repo excluded it."""
        return self.minutes > 0 and not any(_excludes(entry, name, version) for entry in self.excluded)


def _excludes(entry: str, name: str, version: str) -> bool:
    entry = entry.strip()
    at = entry.rfind("@")
    if at > 0:  # `name@1.2.3` or `name@1.2.3 || 1.2.4`; position 0 is a scope's own `@`
        versions = {part.strip() for part in entry[at + 1 :].split("||")}
        return fnmatch.fnmatchcase(name, entry[:at]) and version in versions
    return fnmatch.fnmatchcase(name, entry)


def _scalar(raw: str) -> str:
    """A YAML scalar as written, without a trailing comment or its quotes."""
    value = raw.strip()
    if value[:1] in {"'", '"'}:
        closing = value.find(value[0], 1)
        return value[1:closing] if closing > 0 else value[1:]
    return value.split(" #", 1)[0].strip()


def _sections(text: str) -> dict[str, tuple[int, str, list[tuple[int, str]]]]:
    """Top-level keys -> (line number, inline value, the indented lines beneath it)."""
    sections: dict[str, tuple[int, str, list[tuple[int, str]]]] = {}
    current: list[tuple[int, str]] | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t-":
            if current is not None:
                current.append((number, line))
            continue
        found = TOP_LEVEL_KEY.match(line)
        current = None
        if found:
            current = []
            sections[_scalar(found["key"])] = (number, found["value"], current)
    return sections


def _list(inline: str, lines: list[tuple[int, str]], key: str) -> tuple[str, ...]:
    flow = _scalar(inline)
    if flow.startswith("[") and flow.endswith("]"):
        return tuple(_scalar(item) for item in flow[1:-1].split(",") if item.strip())
    if flow:
        raise ValueError(f"`{key}` is not a list")
    items = []
    for _, line in lines:
        found = LIST_ITEM.match(line)
        if not found:
            raise ValueError(f"`{key}` holds a line that is not a list item: {line.strip()}")
        items.append(_scalar(found["value"]))
    return tuple(items)


def _mapping(inline: str, lines: list[tuple[int, str]], key: str) -> list[tuple[int, str, str]]:
    if _scalar(inline) not in {"", "{}"}:
        raise ValueError(f"`{key}` is not a block mapping")
    entries = []
    for number, line in lines:
        found = NESTED_ENTRY.match(line)
        if not found:
            raise ValueError(f"`{key}` holds a line that is not `name: version`: {line.strip()}")
        entries.append((number, _scalar(found["key"]), _scalar(found["value"])))
    return entries


def override_target(selector: str) -> str:
    """The package an override selector forces: `a>b@^2` -> `b`, `@s/p@1` -> `@s/p`."""
    target = selector.rsplit(">", 1)[-1]
    at = target.find("@", 1)
    return target[:at] if at > 0 else target


def _overrides(entries: list[tuple[int, str, str]], path: Path) -> list[Override]:
    """Literal versions only: `-` removes a package and `$name` / `npm:` defer to another site."""
    return [
        Override(override_target(selector), value, path, number)
        for number, selector, value in entries
        if value and value != "-" and not value.startswith(("$", "npm:", "link:", "file:", "workspace:"))
    ]


def workspace_overrides(path: Path, text: str) -> list[Override]:
    """The overrides one `pnpm-workspace.yaml` declares."""
    section = _sections(text).get("overrides")
    return _overrides(_mapping(section[1], section[2], "overrides"), path) if section else []


def package_json_overrides(path: Path, data: dict, text: str) -> list[Override]:
    """`pnpm.overrides` (pnpm 10), `overrides` (npm) and `resolutions` (Yarn, pnpm) in one manifest."""
    blocks = [data.get("overrides"), data.get("resolutions")]
    if isinstance(data.get("pnpm"), dict):
        blocks.append(data["pnpm"].get("overrides"))
    entries = []
    for block in blocks:
        for selector, value in (block if isinstance(block, dict) else {}).items():
            if isinstance(value, str):
                offset = max(text.find(json.dumps(selector)), 0)
                entries.append((text.count("\n", 0, offset) + 1, selector, value))
    return _overrides(entries, path)


def _pnpm_major(package: dict) -> int | None:
    found = PACKAGE_MANAGER.match(str(package.get("packageManager", "")))
    return int(found["major"]) if found else None


def release_age_guard(repo_root: Path) -> ReleaseAgeGuard | None:
    """pnpm's release-age guard for this repository, or None where pnpm is not its manager.

    The pnpm version comes from `packageManager`, which a self-contained repo declares. Without
    it the default cannot be known, so only an explicit setting counts -- the gate then blocks
    where pnpm might not, which errs toward looking at a release rather than skipping it.
    """
    manifest = repo_root / "package.json"
    workspace = repo_root / WORKSPACE_FILE
    package = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
    major = _pnpm_major(package)
    if major is None and not workspace.is_file() and not (repo_root / "pnpm-lock.yaml").is_file():
        return None
    settings: dict[str, object] = dict(package.get("pnpm") or {}) if major is None or major < 11 else {}
    source = "package.json `pnpm`"
    if workspace.is_file():
        sections = _sections(workspace.read_text(encoding="utf-8"))
        if "minimumReleaseAge" in sections:
            settings["minimumReleaseAge"] = _scalar(sections["minimumReleaseAge"][1])
            source = WORKSPACE_FILE
        if "minimumReleaseAgeExclude" in sections:
            _, inline, lines = sections["minimumReleaseAgeExclude"]
            settings["minimumReleaseAgeExclude"] = _list(inline, lines, "minimumReleaseAgeExclude")
    configured = settings.get("minimumReleaseAge")
    if configured is not None:
        minutes = int(str(configured))
    else:
        minutes = next((m for since, m in sorted(DEFAULT_MINIMUM_AGE_MINUTES.items()) if (major or 0) >= since), 0)
        source = f"pnpm {major}'s default" if major else "pnpm (no `packageManager`, so no default assumed)"
    excluded = tuple(str(entry) for entry in settings.get("minimumReleaseAgeExclude") or ())
    return ReleaseAgeGuard(minutes, excluded, f"pnpm minimumReleaseAge {minutes} min ({source})")

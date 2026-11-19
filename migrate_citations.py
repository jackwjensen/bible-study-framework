#!/usr/bin/env python3
"""
migrate_citations.py — rewrite a study from the old `scripture:` field to `citations:`.

    python migrate_citations.py <study-root> [--check]

Every entry of a finding's `scripture:` list (inline or block form) becomes a corpus-qualified
entry of `citations:`, using the corpus `study.md` names: `John 3:16` becomes `bible:John 3:16`.
An entry that already names a corpus is kept as it is. The new line takes the old line's place
and nothing else in the file moves, so the diff is one line per finding.

The list is written the way the framework's canonical form writes it: inline, every entry
double-quoted (`"` and `\\` escaped), sorted. An empty list stays `citations: []`.

`--check` lists the files that would change, writes nothing, and exits 1 if there are any.
Running it again on a migrated study changes nothing, so it is safe to re-run.

Standard library only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from study_corpus import split_citation
from study_file import StudyFileError, load_study
from study_frontmatter import normalize_list, unquote_scalar

_FENCE = re.compile(r"^---\s*$")
_SCRIPTURE = re.compile(r"^scripture:(.*)$")
_BLOCK_ITEM = re.compile(r"^\s+-\s+(.+)$")


class MigrationError(Exception):
    """A file cannot be migrated safely. The message names what is wrong."""


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _qualify(entry: str, corpus: str) -> str:
    try:
        split_citation(entry)
    except ValueError:
        return f"{corpus}:{entry}"
    return entry


def _line_ending(line: str) -> str:
    return line[len(line.rstrip("\r\n")):]


def migrate_file(text: str, corpus: str) -> str:
    """The file with `scripture:` rewritten as `citations:`; any other text comes back unchanged."""
    lines = text.splitlines(keepends=True)
    if not lines or not _FENCE.match(lines[0].rstrip("\r\n")):
        return text
    end = next((i for i in range(1, len(lines)) if _FENCE.match(lines[i].rstrip("\r\n"))), None)
    if end is None:
        return text
    front = [line.rstrip("\r\n") for line in lines[1:end]]
    if any(line.startswith("citations:") for line in front) and any(
            line.startswith("scripture:") for line in front):
        raise MigrationError("has both 'scripture:' and 'citations:'; remove one by hand")

    start = next((i for i in range(1, end) if _SCRIPTURE.match(lines[i].rstrip("\r\n"))), None)
    if start is None:
        return text
    rest = _SCRIPTURE.match(lines[start].rstrip("\r\n")).group(1).strip()
    stop = start + 1
    if rest != "":
        entries = normalize_list(rest)
    else:
        items: list[str] = []
        while stop < end and _BLOCK_ITEM.match(lines[stop].rstrip("\r\n")):
            items.append(unquote_scalar(_BLOCK_ITEM.match(lines[stop].rstrip("\r\n")).group(1)))
            stop += 1
        entries = normalize_list(items)

    qualified = sorted({_qualify(entry, corpus) for entry in entries})
    new_line = "citations: [" + ", ".join(_quote(entry) for entry in qualified) + "]"
    lines[start:stop] = [new_line + _line_ending(lines[start])]
    return "".join(lines)


def main(argv: list[str]) -> int:
    check = "--check" in argv
    roots = [arg for arg in argv if arg != "--check"]
    if len(roots) != 1:
        print("usage: python migrate_citations.py <study-root> [--check]", file=sys.stderr)
        return 2
    root = Path(roots[0])
    try:
        corpus = load_study(root).corpus
    except StudyFileError as problem:
        print(f"{problem}", file=sys.stderr)
        return 2

    changed: list[Path] = []
    failed = 0
    for path in sorted((root / "findings").glob("*.md")):
        original = path.read_bytes().decode("utf-8")
        try:
            migrated = migrate_file(original, corpus)
        except MigrationError as problem:
            print(f"{path.name}: {problem}", file=sys.stderr)
            failed += 1
            continue
        if migrated == original:
            continue
        changed.append(path)
        if not check:
            path.write_bytes(migrated.encode("utf-8"))
    verb = "would change" if check else "changed"
    for path in changed:
        print(f"{verb}: {path.name}")
    print(f"{len(changed)} file(s) {verb}, {failed} refused")
    if failed:
        return 2
    return 1 if check and changed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

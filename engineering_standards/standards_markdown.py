#!/usr/bin/env python3
"""Markdown and tree primitives shared by the documentation-dimension modules.

standards_docs (structure), standards_symbols (stale symbols), standards_coverage
(uncovered elements) and standards_userdocs (the shipped user-docs contract) all read the
same docs tree the same way. These are the primitives they share -- split out when
standards_docs crossed the file-length limit, because "how markdown is read" is a
different reason to change than any one rule family. A leaf module: imports nothing from
its consumers, so no import cycle can form.

Source of truth: engineering-standards/engineering_standards/standards_markdown.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from standards_core import warn_unreadable
from standards_tree import walk_repository

CODE_FENCE = re.compile(r"^\s*(```|~~~)")

# An inline code span: `token`. Fenced blocks hold examples and examples legitimately use
# invented names, so rules judge spans and skip fences -- see content_lines.
INLINE_CODE_SPAN = re.compile(r"`([^`\n]+)`")

# Directories never walked when building a repository inventory -- vendored, generated, or
# the repository plumbing itself. Mirrors standards_scope's exclusions as directory NAMES,
# because these walks prune while descending rather than testing whole paths.
SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "vendor",
        "obj",
        "bin",
        "dist",
        "build",
        "__pycache__",
        ".next",
        "coverage",
        "staticfiles",
    }
)


def documentation_pages(repo_root: Path) -> list[Path]:
    """Every markdown page in the docs tree, sorted for deterministic reports."""
    docs = repo_root / "docs"
    if not docs.is_dir():
        return []
    return sorted(
        Path(directory) / name
        for directory, _, names in walk_repository(docs)
        for name in names
        if name.endswith(".md")
    )


def frontmatter_block(lines: list[str]) -> list[str] | None:
    """The raw lines inside the leading `---` block, or None when there is no closed one.

    WHERE the block is, only -- never what it means. Two callers read the same block for
    different value shapes: standards_docs wants flat casefolded scalars (`audience: dev`),
    standards_rules wants a case-preserving reason and a multi-line YAML list (`paths:`).
    Casefolding in a shared parser would destroy the first; teaching one parser both shapes
    would give each caller the other's edge cases.

    So the boundary is the primitive and the interpretation is not, which is the split that
    keeps this DRY without inventing a YAML dependency the pack does not ship.
    """
    if not lines or lines[0].strip() != "---":
        return None
    block: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            return block
        block.append(line)
    return None


def read_lines(path: Path) -> list[str]:
    # `utf-8-sig` drops a leading byte-order mark, which would otherwise make line 1 read
    # "﻿---" and hide the page's whole frontmatter block.
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError as error:
        # The empty list is what makes this dangerous: an unreadable page becomes an EMPTY
        # page, and an empty page passes the frontmatter, broken-link and stale-symbol rules
        # without any of them having seen it. See warn_unreadable.
        warn_unreadable(path, error, "the documentation rules could not examine this page")
        return []


def content_lines(lines: list[str]) -> Iterator[tuple[int, str]]:
    """(line_number, line) for prose lines -- fenced code blocks are skipped whole.

    A fence holds examples, and examples legitimately contain invented links and invented
    names; checking them would punish exactly the pages that document things properly.
    """
    in_fence = False
    for line_number, line in enumerate(lines, start=1):
        if CODE_FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield line_number, line

#!/usr/bin/env python3
"""The repository's tree, as the whole-tree rules must see it: what git holds, not the folder.

Every repo-level rule needs to walk the repository, and every one of them used to do it with
its own `os.walk(repo_root)`. That reads the FOLDER, and a folder holds more than its
repository: a developer's ignored `.env`, build output nobody pruned by name, and -- the case
that forced this module, 2026-11-03 -- other git worktrees nested inside it. Claude Code
sessions create theirs under `.claude/worktrees/`, gitignored, never tracked, never pushed;
jacks_corner's push was refused by `[tests-uncovered-module]` on a PHP file in one of them,
and the only way past was `SKIP_STANDARDS_GATE=1`, which skips every gate. Each walker had
learned some of this on its own (two of them asked git for ignored files, the rest did not),
so one door now answers it for all of them.

WHAT IS PRUNED, and why each:
  * `.git` -- the repository's own database, never content.
  * a directory holding a `.git` entry -- another repository: a linked worktree (`.git`
    file), a nested clone or a submodule (`.git` directory or file). Git itself never
    descends into one, ignored or not, so its files cannot be this repository's. Recognised
    by a stat rather than by asking git, so it holds even when git cannot be asked.
  * whatever git reports as untracked AND ignored -- `standards_git.ignored_entries`, where
    a wholly ignored directory arrives as one entry and is pruned without being entered.

WHY A WALK AND NOT `git ls-files`. A flat file list would answer the same question, but the
callers are walks: they prune by directory name, cap depth, and look at sibling directories,
and rewriting each into a list filter is a second implementation of every one of those. So
this yields exactly what `os.walk` yields -- the SAME `subdirectories` list object, already
pruned -- and a caller's own `subdirectories[:] = ...` keeps working unchanged.

When git cannot be asked, nothing beyond the first two is pruned: an unreadable repository
scans more rather than less, for the reason `ignored_entries` gives.

Source of truth: engineering-standards/engineering_standards/standards_tree.py
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from standards_git import ignored_entries

GIT_ENTRY = ".git"


def _foreign_directory(path: Path, ignored: set[Path]) -> bool:
    """True for a directory whose contents are not this repository's (see the module doc)."""
    return path.name == GIT_ENTRY or path in ignored or (path / GIT_ENTRY).exists()


def walk_repository(repo_root: Path) -> Iterator[tuple[str, list[str], list[str]]]:
    """`os.walk(repo_root)`, top-down, limited to what the repository holds.

    Same triple as `os.walk`, so the call sites change by one name. The ignored set is asked
    for ONCE, before the walk: per directory it would be one subprocess per folder.
    """
    ignored = ignored_entries(repo_root)

    for directory, subdirectories, names in os.walk(repo_root):
        base = Path(directory)
        subdirectories[:] = [name for name in subdirectories if not _foreign_directory(base / name, ignored)]
        names[:] = [name for name in names if base / name not in ignored]
        yield directory, subdirectories, names


def repository_holds(repo_root: Path, paths: list[Path]) -> list[Path]:
    """The members of `paths` that `walk_repository` would reach, without walking.

    For an explicit path list (`--files`, the staged set), where walking the whole tree to
    answer for a handful of files would make a pre-commit run as slow as a push. A path is
    held when neither it nor any directory between it and the root is pruned.
    """
    ignored = ignored_entries(repo_root)
    held: list[Path] = []
    for path in paths:
        if path in ignored:
            continue
        directories = path.relative_to(repo_root).parents
        if not any(_foreign_directory(repo_root / directory, ignored) for directory in directories if directory.parts):
            held.append(path)
    return held

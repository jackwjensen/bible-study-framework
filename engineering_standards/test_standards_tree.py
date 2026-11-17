#!/usr/bin/env python3
"""Cases for `walk_repository`: the whole-tree scan reads the repository, not the folder.

Written on 2026-11-03 after a push of unrelated content to jacks_corner was refused by
`[tests-uncovered-module]` on `.claude/worktrees/php-2shell-patch-8f0ed5/lib/news-visuals.php`
-- a file in ANOTHER session's git worktree, nested inside the repo folder, gitignored, never
tracked, never pushed. Every repo-level rule walked the filesystem with its own `os.walk`, so
anything sitting in the folder was judged as though the repository held it.

Every case builds a real repository, because the subject IS git's answer -- a mocked `git`
would test the mock. Git config is isolated (`GIT_CONFIG_GLOBAL`, `GIT_CONFIG_NOSYSTEM`, an
explicit empty `core.excludesFile`) so each repo answers to its own `.gitignore` alone: a
developer's global ignore file would otherwise make the fixture mean something different on
each machine, which `testing.md` names and `test_standards_secrets.py` learned the hard way.

Run: pytest test_standards_tree.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_tree import walk_repository  # noqa: E402

TEST_RULE = "tests-uncovered-module"

# A module with a definition and no test anywhere: what `tests-uncovered-module` exists for.
UNTESTED_MODULE = "<?php\n\ndeclare(strict_types=1);\n\nfunction news_visuals(): string\n{\n    return '';\n}\n"


def have_git() -> bool:
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
        return True
    except OSError, subprocess.CalledProcessError:
        return False


needs_git = pytest.mark.skipif(not have_git(), reason="git is not installed on this machine")


def _isolated_git_env(tree: Path) -> dict[str, str]:
    no_excludes = tree / "no-excludes"
    no_excludes.write_text("", encoding="utf-8")
    return {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(no_excludes),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.excludesFile",
        "GIT_CONFIG_VALUE_0": str(no_excludes),
    }


@contextmanager
def isolated_git() -> Iterator[Path]:
    """A temp directory, with the process's git config pinned to it for the duration.

    `walk_repository` runs git in-process, so the isolation has to reach this process's
    environment, not only the fixture's subprocesses -- otherwise the fixture is built under
    one config and walked under another.
    """
    with tempfile.TemporaryDirectory() as tree, pytest.MonkeyPatch.context() as patch:
        for key, value in _isolated_git_env(Path(tree)).items():
            patch.setenv(key, value)
        yield Path(tree)


def _git(repo: Path, *arguments: str) -> None:
    # check=True: a fixture step that fails must fail HERE, not as an unrelated assertion.
    subprocess.run(["git", *arguments], cwd=repo, capture_output=True, text=True, check=True)


def _repository(tree: Path, gitignore: str = "") -> Path:
    repo = tree / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text(gitignore, encoding="utf-8")
    return repo


def _nested_worktree(directory: Path) -> Path:
    """A folder shaped like a linked git worktree: a `.git` FILE pointing elsewhere.

    A real `git worktree add` would do, but the shape is the subject -- git sees a `.git`
    entry and treats the folder as another repository -- and this keeps the fixture free of
    a second repository's history.
    """
    directory.mkdir(parents=True)
    (directory / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n", encoding="utf-8")
    return directory


def _walked(repo: Path) -> set[str]:
    return {
        (Path(directory) / name).relative_to(repo).as_posix()
        for directory, _, names in walk_repository(repo)
        for name in names
    }


def _scan(repo: Path) -> str:
    limits = Path(__file__).resolve().parent / "check-source-limits.py"
    done = subprocess.run(
        [sys.executable, str(limits), "--root", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout + done.stderr


# ---- the incident, end to end ----------------------------------------------------------


@needs_git
def test_an_untested_module_in_an_ignored_nested_worktree_is_not_reported():
    """The jacks_corner push, reproduced: the module is in the folder, not in the repository."""
    with isolated_git() as tree:
        repo = _repository(tree, ".claude/*\n")
        worktree = _nested_worktree(repo / ".claude" / "worktrees" / "php-patch")
        (worktree / "lib").mkdir()
        (worktree / "lib" / "news-visuals.php").write_text(UNTESTED_MODULE, encoding="utf-8")

        output = _scan(repo)
        assert "news-visuals.php" not in output, output


@needs_git
def test_the_same_module_inside_the_repository_is_still_reported():
    """The half that must not regress: moving the walk to git must not lose real files."""
    with isolated_git() as tree:
        repo = _repository(tree, ".claude/*\n")
        (repo / "lib").mkdir()
        (repo / "lib" / "news-visuals.php").write_text(UNTESTED_MODULE, encoding="utf-8")

        output = _scan(repo)
        assert TEST_RULE in output and "news-visuals.php" in output, output


# ---- walk_repository -------------------------------------------------------------------


@needs_git
def test_an_ignored_file_is_not_walked_and_its_tracked_neighbour_is():
    with isolated_git() as tree:
        repo = _repository(tree, ".env\n")
        (repo / ".env").write_text("SECRET=x\n", encoding="utf-8")
        (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
        assert _walked(repo) == {".gitignore", "app.py"}


@needs_git
def test_a_tracked_file_under_an_ignore_pattern_is_still_walked():
    """`--others` means UNTRACKED: a force-added file is in the repository, so it is read.

    This keeps `committed-credential` firing on a `.env` somebody committed despite the
    ignore line -- the case that rule exists for.
    """
    with isolated_git() as tree:
        repo = _repository(tree, ".env\n")
        (repo / ".env").write_text("SECRET=x\n", encoding="utf-8")
        _git(repo, "add", "--force", ".env")
        assert ".env" in _walked(repo)


@needs_git
def test_an_ignored_directory_is_pruned_whole():
    with isolated_git() as tree:
        repo = _repository(tree, "build/\n")
        (repo / "build" / "deep").mkdir(parents=True)
        (repo / "build" / "deep" / "out.py").write_text("x = 1\n", encoding="utf-8")
        assert not any(path.startswith("build/") for path in _walked(repo))


@needs_git
def test_a_nested_repository_that_is_not_ignored_is_still_pruned():
    """Git never descends into a folder holding its own `.git`, ignored or not -- nor do we.

    Its files belong to another repository; the most this one can hold is a gitlink.
    """
    with isolated_git() as tree:
        repo = _repository(tree)
        worktree = _nested_worktree(repo / "sibling")
        (worktree / "module.py").write_text("x = 1\n", encoding="utf-8")
        assert not any(path.startswith("sibling/") for path in _walked(repo))


@needs_git
def test_the_repositorys_own_git_directory_is_not_walked():
    with isolated_git() as tree:
        repo = _repository(tree)
        assert not any(path.startswith(".git/") for path in _walked(repo))


@needs_git
def test_a_callers_own_pruning_still_applies():
    """The walker yields os.walk's own list, so a caller's `subdirectories[:] =` still prunes."""
    with isolated_git() as tree:
        repo = _repository(tree)
        (repo / "keep").mkdir()
        (repo / "keep" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (repo / "drop").mkdir()
        (repo / "drop" / "b.py").write_text("x = 1\n", encoding="utf-8")

        seen: list[str] = []
        for directory, subdirectories, names in walk_repository(repo):
            subdirectories[:] = [name for name in subdirectories if name != "drop"]
            seen.extend((Path(directory) / name).relative_to(repo).as_posix() for name in names)
        assert "keep/a.py" in seen and "drop/b.py" not in seen


# ---- one door: no shipped module walks a tree any other way ------------------------------

# The spellings of a recursive walk. A shallow `glob("*.sln")` is not one: it cannot reach a
# nested worktree, so it is left alone.
RECURSIVE_WALKS = ("os.walk(", ".rglob(")


def test_no_shipped_module_walks_a_tree_except_through_walk_repository():
    """The incident was a dozen walkers each deciding for itself; this keeps it at one.

    Enforced here rather than written into CLAUDE.md, because a scanner-checkable rule
    belongs in a check. Tests and `dev/` are outside it: tests walk their own fixtures, and
    `dev/` tools walk the pack's source, never a consuming repository.
    """
    pack = Path(__file__).resolve().parent
    offenders = [
        f"{source.name}:{number}"
        for source in sorted(pack.glob("*.py"))
        if not source.name.startswith("test_") and source.name != "standards_tree.py"
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1)
        if any(walk in line for walk in RECURSIVE_WALKS) and not line.lstrip().startswith("#")
    ]
    assert offenders == [], f"walk through standards_tree.walk_repository instead: {offenders}"


def test_outside_a_repository_every_file_is_walked_but_nested_repositories_are_not():
    """No git answer means scan MORE, never less -- but a `.git`-bearing folder needs no git
    to recognise, so it is pruned either way."""
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        (root / "app.py").write_text("x = 1\n", encoding="utf-8")
        (root / ".env").write_text("SECRET=x\n", encoding="utf-8")
        _nested_worktree(root / "other")
        (root / "other" / "module.py").write_text("x = 1\n", encoding="utf-8")
        assert _walked(root) == {"app.py", ".env"}


if __name__ == "__main__":
    from standards_selftest import run_module_tests

    raise SystemExit(run_module_tests(sys.modules[__name__]))

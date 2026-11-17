#!/usr/bin/env python3
"""Cases for the git seam: what the scanner asks git, and what it does when git cannot say.

Written on 2026-09-01 after `tests-uncovered-module` was tightened. This module had passed
the loose version on a bare-name match, which is the false negative that tightening removed.

The distinction this module exists to keep is **"cannot tell" versus "nothing"**, and it is
the one a test protects best, because collapsing the two is invisible and changes what the
whole scanner examines. `staged_files` returns None when git cannot be asked and [] when the
commit is genuinely empty; `ignored_entries` deliberately returns an empty set on failure, so a
repository it cannot read scans MORE rather than less. Those two answers point in opposite
directions on purpose, and either one silently flipping would be a live defect: the first
would scan nothing at commit time, the second would stop reporting a developer's `.env`.

Every case builds a real repository, because the subject IS git's behaviour -- a mocked
`git` would test the mock, which is the anti-pattern `testing.md` names.

Run: pytest test_standards_git.py
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

from standards_git import (  # noqa: E402
    files_changed_since,
    ignored_entries,
    repository_root,
    staged_files,
)


def git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True, env=isolated_git_environment())


def isolated_git_environment() -> dict[str, str]:
    """Git as the repository alone defines it: no user or system config, no global excludes."""
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.excludesFile",
        "GIT_CONFIG_VALUE_0": os.devnull,
    }


def have_git() -> bool:
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
        return True
    except OSError, subprocess.CalledProcessError:
        return False


needs_git = pytest.mark.skipif(not have_git(), reason="git is not installed on this machine")


@contextmanager
def repository() -> Iterator[Path]:
    # os.environ itself is patched, not only the fixture's git calls: the functions under test
    # spawn git too, and a developer's global excludes would change what "untracked" means.
    saved = os.environ.copy()
    os.environ.update(isolated_git_environment())
    try:
        with tempfile.TemporaryDirectory() as tree:
            root = Path(tree)
            git(root, "init", "--quiet")
            git(root, "config", "user.email", "test@example.invalid")
            git(root, "config", "user.name", "Test")
            yield root
    finally:
        os.environ.clear()
        os.environ.update(saved)


@contextmanager
def plain_directory() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tree:
        yield Path(tree)


# ---- repository_root --------------------------------------------------------------------


@needs_git
def test_a_path_inside_a_repository_resolves_to_its_root():
    with repository() as root:
        (root / "lib").mkdir()
        found = repository_root(root / "lib")
        assert found is not None and found.resolve() == root.resolve()


@needs_git
def test_a_path_outside_any_repository_is_none():
    """None, not the directory itself: "not a repo" must not read as "a repo here"."""
    with plain_directory() as directory:
        assert repository_root(directory) is None


# ---- staged_files: None means "could not ask", [] means "nothing staged" ----------------


@needs_git
def test_a_staged_file_is_listed():
    with repository() as root:
        (root / "a.py").write_text("x = 1\n", encoding="utf-8")
        git(root, "add", "a.py")
        found = staged_files(root)
        assert found is not None
        assert [path.name for path in found] == ["a.py"]


@needs_git
def test_an_empty_index_is_an_empty_list_not_none():
    """The distinction the caller depends on: nothing staged is not the same as no answer."""
    with repository() as root:
        assert staged_files(root) == []


@needs_git
def test_a_deleted_file_is_not_offered_for_scanning():
    """The filter is ACMR: a path the commit REMOVES cannot be read, so it is not listed."""
    with repository() as root:
        (root / "gone.py").write_text("x = 1\n", encoding="utf-8")
        git(root, "add", "gone.py")
        git(root, "commit", "--quiet", "-m", "add")
        git(root, "rm", "--quiet", "gone.py")
        assert staged_files(root) == []


def test_outside_a_repository_the_answer_is_none_not_empty():
    with plain_directory() as directory:
        assert staged_files(directory) is None


# ---- files_changed_since: what a branch changed, measured from where it forked ----------


def commit_all(root: Path, message: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "--quiet", "-m", message)


def branch_after_base(root: Path) -> None:
    """Two tracked files on a base commit, then a branch; `base` marks where it forked."""
    (root / "kept.py").write_text("x = 1\n", encoding="utf-8")
    (root / "edited.py").write_text("x = 1\n", encoding="utf-8")
    (root / "gone.py").write_text("x = 1\n", encoding="utf-8")
    (root / "moved.py").write_text("moved = 'a body long enough to be recognised as a rename'\n" * 5, encoding="utf-8")
    commit_all(root, "base")
    git(root, "tag", "base")
    git(root, "checkout", "--quiet", "-b", "feature")


def names_since(root: Path) -> list[str] | None:
    found = files_changed_since(root, "base")
    return None if found is None else sorted(path.relative_to(root).as_posix() for path in found)


@needs_git
def test_a_file_committed_on_the_branch_is_listed_and_an_untouched_one_is_not():
    with repository() as root:
        branch_after_base(root)
        (root / "edited.py").write_text("x = 2\n", encoding="utf-8")
        commit_all(root, "edit")
        assert names_since(root) == ["edited.py"]


@needs_git
def test_a_deleted_file_is_not_listed_as_changed_since():
    with repository() as root:
        branch_after_base(root)
        git(root, "rm", "--quiet", "gone.py")
        commit_all(root, "delete")
        assert names_since(root) == []


@needs_git
def test_an_untracked_new_file_is_listed_as_changed_since():
    with repository() as root:
        branch_after_base(root)
        (root / "fresh.py").write_text("x = 1\n", encoding="utf-8")
        assert names_since(root) == ["fresh.py"]


@needs_git
def test_a_renamed_file_is_listed_at_its_new_path():
    with repository() as root:
        branch_after_base(root)
        git(root, "mv", "moved.py", "renamed.py")
        commit_all(root, "rename")
        assert names_since(root) == ["renamed.py"]


@needs_git
def test_work_the_trunk_gained_after_the_fork_is_not_this_branch_s_change():
    """Measured from the merge-base: a trunk that moved on must not make the branch look like it undid it."""
    with repository() as root:
        branch_after_base(root)
        git(root, "checkout", "--quiet", "-")
        trunk = subprocess.run(
            ["git", "branch", "--show-current"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()
        (root / "trunk_only.py").write_text("x = 1\n", encoding="utf-8")
        commit_all(root, "trunk moves on")
        git(root, "checkout", "--quiet", "feature")
        (root / "edited.py").write_text("x = 2\n", encoding="utf-8")
        commit_all(root, "edit")
        found = files_changed_since(root, trunk)
        assert found is not None
        assert sorted(path.name for path in found) == ["edited.py"]


@needs_git
def test_an_unknown_ref_is_none_not_an_empty_list():
    with repository() as root:
        (root / "a.py").write_text("x = 1\n", encoding="utf-8")
        commit_all(root, "first")
        assert files_changed_since(root, "origin/does-not-exist") is None


def test_since_outside_a_repository_is_none_not_empty():
    with plain_directory() as directory:
        assert files_changed_since(directory, "HEAD") is None


# ---- ignored_entries: the one place "cannot tell" deliberately means "nothing" -----------


@needs_git
def test_an_ignored_untracked_file_is_reported_as_ignored():
    """The case this exists for: a developer's .env must not read as "being committed"."""
    with repository() as root:
        (root / ".gitignore").write_text(".env\n", encoding="utf-8")
        (root / ".env").write_text("SECRET=x\n", encoding="utf-8")
        assert (root / ".env").resolve() in {path.resolve() for path in ignored_entries(root)}


@needs_git
def test_a_wholly_ignored_directory_is_one_entry():
    """`--directory`: the walker prunes the folder instead of being handed every file in it."""
    with repository() as root:
        (root / ".gitignore").write_text("build/\n", encoding="utf-8")
        (root / "build" / "deep").mkdir(parents=True)
        (root / "build" / "deep" / "out.py").write_text("x = 1\n", encoding="utf-8")
        assert {path.resolve() for path in ignored_entries(root)} == {(root / "build").resolve()}


@needs_git
def test_a_tracked_file_is_never_in_the_ignored_set():
    """`--others` means UNTRACKED, so a committed .env stays flagged by the credential rule.

    This is the half that keeps the fix from becoming a hole: the rule loses the case it was
    never about (an ignored file) and keeps the case it exists for (a committed one).
    """
    with repository() as root:
        (root / ".gitignore").write_text(".env\n", encoding="utf-8")
        (root / ".env").write_text("SECRET=x\n", encoding="utf-8")
        git(root, "add", "--force", ".env")
        git(root, "commit", "--quiet", "-m", "oops")
        assert (root / ".env").resolve() not in {path.resolve() for path in ignored_entries(root)}


def test_outside_a_repository_nothing_is_ignored_so_everything_is_scanned():
    """Empty set on failure, deliberately: for a credential rule, noisy is the safe direction."""
    with plain_directory() as directory:
        assert ignored_entries(directory) == set()


if __name__ == "__main__":
    from standards_selftest import run_module_tests

    raise SystemExit(run_module_tests(sys.modules[__name__]))

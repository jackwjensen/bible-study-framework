#!/usr/bin/env python3
"""`check-source-limits.py --since REF`: the scanner scoped to what a branch changed.

The pre-push hook uses it so a push checks the branch rather than re-reading a whole tree it
did not touch; CI keeps the whole tree. Unlike `--staged` it scopes only the per-file rules:
the repository-level rules (tracked secrets, SQL dumps, .gitignore) still run over the whole tree.

Every case drives the real CLI against a real repository, because the subject is the seam
between git's answer and the scanner's scope. Git is isolated from user and system config.

Run: pytest test_standards_since.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PACK_DIRECTORY = Path(__file__).resolve().parent
LIMITS = PACK_DIRECTORY / "check-source-limits.py"

OVERSIZED_LINES = 900


def isolated_environment() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.excludesFile",
        "GIT_CONFIG_VALUE_0": os.devnull,
    }


def git(repo: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=repo, check=True, capture_output=True, env=isolated_environment())


def scan(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LIMITS), "--root", str(repo), *extra],
        capture_output=True,
        text=True,
        check=False,
        env=isolated_environment(),
    )


def write_oversized(repo: Path, name: str) -> None:
    body = "\n".join(f"VALUE_{n} = {n}" for n in range(OVERSIZED_LINES))
    (repo / name).write_text(body + "\n", encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo whose base commit already holds one oversized file; `base` marks the fork."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    (root / ".gitignore").write_text(".env\n.env.*\n", encoding="utf-8")
    write_oversized(root, "old_big.py")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    git(root, "tag", "base")
    git(root, "checkout", "-q", "-b", "feature")
    return root


def test_since_reports_a_violation_in_a_changed_file_and_not_in_an_unchanged_one(repo: Path) -> None:
    write_oversized(repo, "new_big.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add a long file")

    result = scan(repo, "--since", "base")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "new_big.py" in result.stdout
    assert "old_big.py" not in result.stdout


def test_since_still_runs_the_repository_rules_over_the_whole_tree(repo: Path) -> None:
    """Only per-file rules are scoped. A branch push puts the tree on a remote, so a .gitignore
    gap must be reported at push -- unlike --staged, which skips it (Jack/controller, 2026-11-17)."""
    (repo / ".gitignore").unlink()
    (repo / "small.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "drop the ignore file")

    control = scan(repo)
    scoped = scan(repo, "--since", "base")

    assert "gitignore" in control.stdout + control.stderr, "the control must report the finding"
    assert "gitignore" in scoped.stdout + scoped.stderr
    assert "old_big.py" not in scoped.stdout, "per-file rules stay scoped"


def test_staged_still_skips_the_repository_rules(repo: Path) -> None:
    (repo / ".gitignore").unlink()
    git(repo, "add", "-A")

    staged = scan(repo, "--staged")

    assert "gitignore" not in staged.stdout + staged.stderr


def test_since_an_unknown_ref_says_why_and_scans_the_whole_tree(repo: Path) -> None:
    """Git unable to answer must widen the scan, never narrow it to nothing."""
    result = scan(repo, "--since", "origin/does-not-exist")

    assert "old_big.py" in result.stdout, result.stdout + result.stderr
    assert result.returncode == 1
    assert "could not say what changed since origin/does-not-exist" in result.stdout


@pytest.mark.parametrize("other", [["--staged"], ["--files", "old_big.py"]])
def test_since_cannot_be_combined_with_the_other_scopes(repo: Path, other: list[str]) -> None:
    result = scan(repo, "--since", "base", *other)

    assert result.returncode == 2


def test_since_cannot_be_combined_with_write_baseline(repo: Path) -> None:
    result = scan(repo, "--write-baseline", "--since", "base")

    assert result.returncode == 2
    assert "--since" in result.stderr

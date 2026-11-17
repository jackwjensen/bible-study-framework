"""A gate is verified by making it fail: a violating commit must be REFUSED.

This automates the sandbox procedure the pack CLAUDE.md has always prescribed as prose
("prove the hook still blocks -- in a throwaway repo"). Every silent-allow bug the pack has
had -- the BOM in the old hook payload, the dispatcher's swallowed `;` -- passed a "does a
clean commit work?" test perfectly, which is why the probe here asserts the refusal first
and the clean pass second.

Run: python test_hook_blocks.py   (or pytest; needs git on PATH)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACK_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(PACK_DIRECTORY))

from standards_selftest import run_module_tests  # noqa: E402  (path set above)

# ONE layout, in the pack and in every repo that adopts it. This used to resolve whichever of
# `githooks/` (the pack) or `.githooks/` (a consumer) existed, because the two disagreed --
# and assuming the pack's layout had broken this test in DonorLink on its very first run
# there, which is exactly the context-assumption bug it exists to catch. The 2026-09-02 move
# removed the disagreement rather than continuing to paper over it, so there is nothing left
# to resolve: the hooks sit at engineering_standards/hooks/ wherever this file is running.
HOOKS_DIRECTORY = PACK_DIRECTORY / "hooks"


def git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *arguments], cwd=repo, capture_output=True, text=True, check=False)


def make_wired_repo(tmp: Path) -> Path:
    """A sandbox repo with the pack's modules and hooks installed and git pointed at them."""
    repo = tmp / "sandbox"
    (repo / "engineering_standards/hooks").mkdir(parents=True)
    for source in PACK_DIRECTORY.glob("*.py"):
        shutil.copy2(source, repo / "engineering_standards" / source.name)
    assert HOOKS_DIRECTORY.is_dir(), f"the pack's hooks are missing from {HOOKS_DIRECTORY}"
    for source in HOOKS_DIRECTORY.iterdir():
        if source.is_file():
            shutil.copy2(source, repo / "engineering_standards/hooks" / source.name)
    (repo / ".gitignore").write_text(".env*\n", encoding="utf-8")
    # Every adopting repo is seeded with the pack's .gitattributes, and `gitattributes-eol`
    # (a repo-level rule the push stage runs) refuses hooks that do not resolve eol=lf.
    # Without it this sandbox failed the push probe on that rule, not on the one under test.
    # Read beside the package, which ships to every repo: the pack root's `templates/` does
    # not, so reading it there broke this test in every consumer (2026.10.11-3).
    shutil.copy2(PACK_DIRECTORY / "templates" / ".gitattributes", repo / ".gitattributes")

    git(repo, "init", "-q")
    git(repo, "config", "user.name", "probe")
    git(repo, "config", "user.email", "probe@example.invalid")
    git(repo, "config", "core.hooksPath", "engineering_standards/hooks")
    # The infrastructure commit deliberately bypasses the hook: the probe below must test
    # the gate against ONE staged file, not against the pack's whole script set.
    git(repo, "add", "-A")
    setup = git(repo, "commit", "--no-verify", "-q", "-m", "wire the gate")
    assert setup.returncode == 0, setup.stdout + setup.stderr
    return repo


def test_violating_commit_is_refused_then_the_fix_commits() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_wired_repo(Path(tmp))

        (repo / "Bad.cs").write_text("public class Thing { public bool Active { get; set; } }\n", encoding="utf-8")
        git(repo, "add", "Bad.cs")
        refused = git(repo, "commit", "-m", "probe")
        combined = refused.stdout + refused.stderr
        assert refused.returncode != 0, (
            "a commit staging a bare-adjective bool MUST be refused -- a hook that lets it "
            "through is doing nothing, which is indistinguishable from working until now"
        )
        assert "bool-prefix" in combined, f"the refusal must name the rule; got: {combined}"

        (repo / "Bad.cs").write_text("public class Thing { public bool IsActive { get; set; } }\n", encoding="utf-8")
        git(repo, "add", "Bad.cs")
        accepted = git(repo, "commit", "-m", "probe fixed")
        assert accepted.returncode == 0, (
            "the corrected file must commit -- otherwise the refusal above proves a broken "
            "environment, not a working gate:\n" + accepted.stdout + accepted.stderr
        )


def verify(repo: Path) -> subprocess.CompletedProcess[str]:
    """The push-stage run, invoked exactly as the pre-push hook invokes it."""
    return subprocess.run(
        [sys.executable, str(repo / "engineering_standards" / "verify.py"), "--root", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_docs_violations_refuse_a_push_stage_run() -> None:
    """The documentation gate blocks: no index, no frontmatter -> the push run fails.

    Commit-stage runs never see these rules (repo-level checks run on whole-tree scans
    only), so the refusal to probe is the push one -- which in this estate is the deploy
    boundary, the moment the docs dimension is supposed to guard.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_wired_repo(Path(tmp))
        (repo / "docs").mkdir()
        (repo / "docs" / "setup.md").write_text("# Setup, sans frontmatter\n", encoding="utf-8")

        result = verify(repo)
        combined = result.stdout + result.stderr
        assert result.returncode != 0, (
            "a push-stage run over a docs tree with no index and no frontmatter MUST fail:\n" + combined
        )
        assert "docs-missing" in combined, f"the refusal must name docs-missing; got: {combined}"
        assert "docs-frontmatter" in combined, f"the refusal must name docs-frontmatter; got: {combined}"


def test_write_baseline_grandfathers_docs_findings() -> None:
    """Adoption's ratchet covers the docs dimension: baselined findings stop blocking."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_wired_repo(Path(tmp))
        (repo / "docs").mkdir()
        (repo / "docs" / "setup.md").write_text("# Setup, sans frontmatter\n", encoding="utf-8")

        wrote = subprocess.run(
            [
                sys.executable,
                str(repo / "engineering_standards" / "check-source-limits.py"),
                "--root",
                str(repo),
                "--write-baseline",
                "--baseline-consent",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert wrote.returncode == 0, wrote.stdout + wrote.stderr

        baseline = (repo / ".standards-baseline.json").read_text(encoding="utf-8")
        assert "docs-missing" in baseline, "the docs findings must land in the baseline"

        result = verify(repo)
        assert result.returncode == 0, (
            "after --write-baseline the same tree must pass -- the ratchet grandfathers "
            "existing debt:\n" + result.stdout + result.stderr
        )


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "hook blocks"))

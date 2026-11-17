#!/usr/bin/env python3
"""Cases for `gitattributes-eol`: every file a Unix shell reads must resolve eol=lf.

Every case builds a real repository, because the subject IS git's attribute resolution --
precedence, nested files, what counts as versioned -- and a mocked git would test the mock.
Two cases are pinned harder than the rest, because each is a way the rule could pass while
the clone it protects still breaks:

* A LOCAL attributes file does not count. One that lives on this PC only (core.attributesFile)
  makes the path resolve LF here and CRLF in every other clone -- the incident itself.
* THE PACK'S OWN TREE IS QUIET. The pack lints itself with no baseline, so if this fails the
  fix is the pack's root `.gitattributes`, never the rule.

Run: python test_standards_gitattributes.py   (or pytest)
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
from contextlib import contextmanager, redirect_stderr
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_gitattributes import (  # noqa: E402
    BASELINE,
    BASELINE_FILE,
    GITATTRIBUTES_RULE,
    check_gitattributes,
    suggested_line,
)
from standards_selftest import run_module_tests  # noqa: E402

# The repository the package sits in -- the pack itself, or a repo that adopted it.
PACK_ROOT = Path(__file__).resolve().parent.parent

# Read beside the package, never from the pack root: the root's `templates/` does not ship,
# so this line once failed collection in every consuming repo (2026.10.11-3).
SEED = BASELINE_FILE.read_text(encoding="utf-8")
SCRIPT = "#!/bin/sh\nset -eu\n"


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


@contextmanager
def repository(files: dict[str, str]) -> Iterator[Path]:
    """A throwaway git repository holding `files`, untracked -- the rule must see those too."""
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
        _write(root, files)
        yield root


def flagged(root: Path) -> dict[str, str]:
    """Offending path -> message, read back from the quoted path that leads each message."""
    findings = list(check_gitattributes(root))
    assert all(finding.rule == GITATTRIBUTES_RULE for finding in findings)
    assert all(finding.path == root / ".gitattributes" for finding in findings)
    return {finding.message.split("'")[1]: finding.message for finding in findings}


def test_a_shell_script_with_no_gitattributes_fires() -> None:
    with repository({"scripts/deploy.sh": SCRIPT}) as root:
        found = flagged(root)
    assert list(found) == ["scripts/deploy.sh"]
    assert "*.sh text eol=lf" in found["scripts/deploy.sh"], "the finding names the line to add"
    assert BASELINE in found["scripts/deploy.sh"], "and where the baseline is"


def test_the_baseline_a_finding_names_is_the_file_beside_the_package() -> None:
    """The message points the reader at BASELINE; it must be the file the tests read, and both
    must exist wherever the package runs -- in the pack and in every repo it is synced into."""
    assert BASELINE_FILE.is_file()
    assert BASELINE_FILE.as_posix().endswith(BASELINE)


def test_hooks_without_the_hooks_line_fire_even_when_sh_is_covered() -> None:
    """The extensionless hooks are the case `*.sh` cannot reach -- the 2026-08-27 one."""
    files = {
        ".gitattributes": "*.sh text eol=lf\n",
        "engineering_standards/hooks/pre-commit": SCRIPT,
        "engineering_standards/hooks/_python.sh": SCRIPT,
    }
    with repository(files) as root:
        found = flagged(root)
    assert list(found) == ["engineering_standards/hooks/pre-commit"]
    assert "engineering_standards/hooks/* text eol=lf" in found["engineering_standards/hooks/pre-commit"]


def test_every_dockerfile_spelling_fires_when_uncovered() -> None:
    files = {
        "Dockerfile": "FROM alpine\n",
        "docker/Dockerfile.prod": "FROM alpine\n",
        "build/app.Dockerfile": "FROM alpine\n",
    }
    with repository(files) as root:
        found = flagged(root)
    assert sorted(found) == ["Dockerfile", "build/app.Dockerfile", "docker/Dockerfile.prod"]
    assert "'Dockerfile.* text eol=lf'" in found["docker/Dockerfile.prod"]
    assert "'*.Dockerfile text eol=lf'" in found["build/app.Dockerfile"]


def test_an_explicit_crlf_fires() -> None:
    """A wrong answer stated on purpose is still wrong; only lf passes."""
    with repository({".gitattributes": "*.sh text eol=crlf\n", "run.sh": SCRIPT}) as root:
        found = flagged(root)
    assert "eol=crlf" in found["run.sh"]


def test_the_seed_covers_everything_the_rule_asks_for() -> None:
    files = {
        ".gitattributes": SEED,
        "scripts/deploy.sh": SCRIPT,
        "engineering_standards/hooks/pre-push": SCRIPT,
        "Dockerfile": "FROM alpine\n",
        "docker/Dockerfile.dev": "FROM alpine\n",
        "app.Dockerfile": "FROM alpine\n",
    }
    with repository(files) as root:
        assert flagged(root) == {}


def test_a_repo_wide_lf_default_is_enough() -> None:
    """ellengaard-dk's own line: `* text=auto eol=lf` resolves eol=lf for every path."""
    files = {
        ".gitattributes": "* text=auto eol=lf\n",
        "run.sh": SCRIPT,
        "engineering_standards/hooks/pre-commit": SCRIPT,
        "Dockerfile": "FROM alpine\n",
    }
    with repository(files) as root:
        assert flagged(root) == {}


def test_a_nested_gitattributes_is_honoured() -> None:
    """Git's own resolution, not a reading of the root file: a subdirectory may cover itself."""
    files = {"tools/.gitattributes": "*.sh text eol=lf\n", "tools/build.sh": SCRIPT}
    with repository(files) as root:
        assert flagged(root) == {}


def test_a_repo_with_no_shell_files_is_quiet() -> None:
    with repository({"README.md": "# x\n", "app.py": "x = 1\n", "run.bat": "@echo off\n"}) as root:
        assert flagged(root) == {}


def test_an_ignored_script_is_not_the_repos_business() -> None:
    files = {".gitignore": "node_modules/\n", "node_modules/pkg/install.sh": SCRIPT}
    with repository(files) as root:
        assert flagged(root) == {}


def test_an_attributes_file_on_this_pc_only_does_not_count() -> None:
    """core.attributesFile travels with no clone, so the answer must ignore it."""
    with repository({"run.sh": SCRIPT}) as root:
        local = root.parent / f"{root.name}-local-attributes"
        local.write_text("*.sh text eol=lf\n", encoding="utf-8")
        try:
            subprocess.run(["git", "config", "core.attributesFile", str(local)], cwd=root, check=True)
            assert list(flagged(root)) == ["run.sh"]
        finally:
            local.unlink()


def test_a_tree_that_is_not_a_repository_is_quiet_and_says_nothing() -> None:
    """No clone, no checkout, nothing to convert -- and no warning either, since git did not fail."""
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        _write(root, {"run.sh": SCRIPT})
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            assert list(check_gitattributes(root)) == []
    assert stderr.getvalue() == ""


def test_the_classifier_and_the_suggestion_agree() -> None:
    assert suggested_line("a/b/run.sh") == "*.sh text eol=lf"
    assert suggested_line("engineering_standards/hooks/_verify.sh") == "engineering_standards/hooks/* text eol=lf"
    assert suggested_line("Dockerfile") == "Dockerfile text eol=lf"
    for not_shell in ("README.md", "run.bat", "docs/dockerfile-notes.md", "shell.py"):
        assert suggested_line(not_shell) is None, not_shell


def test_the_packs_own_tree_is_quiet() -> None:
    assert flagged(PACK_ROOT) == {}


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "gitattributes-eol cases"))

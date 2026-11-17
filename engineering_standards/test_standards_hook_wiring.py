#!/usr/bin/env python3
"""Cases for the hook-wiring warning, and the pack's own wiring asserted rather than assumed.

THE FAILURE THIS FILE IS ABOUT is a gate that does not exist looking identical to one that
passes. `core.hooksPath` is one command per clone that nothing repeats and nothing verified;
a clone where it was never run, or where it points at a directory that is not there, commits
freely and sees exactly what a compliant clone sees. Found in the B3D pack on 2026-09-04 --
in the repo that ships the doctrine -- and copied here 2026-09-05.

Run: python test_standards_hook_wiring.py   (or pytest)
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_hook_wiring import HOOK_DIRECTORIES, warning  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = "engineering_standards/hooks"

# Git records one permission bit per file, and refuses to run a hook without it -- silently.
EXECUTABLE_MODE = "100755"


def repo_with_hooks(hooks_path: str | None = None) -> Path:
    """A throwaway repo carrying the hooks directory, optionally wired to `hooks_path`."""
    root = Path(tempfile.mkdtemp())
    (root / HOOKS).mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True, check=False)
    if hooks_path is not None:
        subprocess.run(["git", "config", "core.hooksPath", hooks_path], cwd=root, capture_output=True, check=False)
    return root


def test_an_unwired_clone_is_told_so() -> None:
    message = warning(repo_with_hooks(), environment={})
    assert message is not None
    assert "NOT wired" in message
    assert f"git config core.hooksPath {HOOKS}" in message


def test_a_hookspath_pointing_nowhere_is_told_so() -> None:
    """THE ACTUAL BUG: the setting exists, `git config` echoes it back, and the directory does
    not. Everything looks configured and nothing runs."""
    message = warning(repo_with_hooks("does-not-exist"), environment={})
    assert message is not None
    assert "does not exist here" in message
    assert "passes unchecked" in message


def test_a_correctly_wired_clone_is_silent() -> None:
    assert warning(repo_with_hooks(HOOKS), environment={}) is None


def test_a_repo_with_no_hooks_at_all_is_silent() -> None:
    """A repo that has not adopted the hook model is not misconfigured; it has chosen
    something else, and nagging it would train people past the message."""
    root = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True, check=False)
    assert warning(root, environment={}) is None


def test_ci_is_silent_even_when_unwired() -> None:
    """CI is SUPPOSED to have no hooks -- it runs verify directly. Warning there would be
    noise on every run in every repo, which is how a warning stops being read."""
    assert warning(repo_with_hooks(), environment={"CI": "true"}) is None


def test_this_repo_asks_for_the_path_it_uses_itself() -> None:
    """The pack gating itself, asserted rather than assumed. The B3D pack ended up with an
    inert gate by asking every adopted repo for one hooks path while using another; if
    somebody moves these hooks, this fails here instead of in a commit that quietly was not
    checked."""
    assert (REPO_ROOT / HOOKS / "pre-commit").is_file()
    assert HOOK_DIRECTORIES == (HOOKS,), "the pack must ask for, and use, exactly one hooks path"


def test_the_hooks_are_committed_executable() -> None:
    """THE BIT THIS MACHINE CANNOT FEEL. Git on Windows executes a hook whatever mode it
    carries, so hooks committed 100644 run perfectly here and are INERT in every Linux and
    macOS clone -- not a failing gate: no gate. sync_pack carries the bit into adopters
    (7078762); this asserts the pack's own copies carry it too, through git rather than the
    filesystem, because on Windows the filesystem has no answer."""
    completed = subprocess.run(
        ["git", "ls-files", "-s", f"{HOOKS}/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return  # not a git checkout, or the hooks are not tracked here -- nothing to assert

    # "<mode> <sha> <stage>\tpath": the path is what follows the tab.
    wrong = [
        line.split("\t", 1)[-1]
        for line in completed.stdout.strip().splitlines()
        if not line.startswith(EXECUTABLE_MODE)
    ]
    assert not wrong, (
        "these hooks are not committed executable, so git will not run them on Linux or "
        f"macOS -- silently: {', '.join(wrong)}.\n"
        "Fix with: git update-index --chmod=+x " + " ".join(wrong)
    )


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "hook wiring"))

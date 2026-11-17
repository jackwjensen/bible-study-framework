#!/usr/bin/env python3
"""_python.sh must select an interpreter that RUNS, not one that merely resolves.

The bug this guards against is Windows-specific and silent in exactly the wrong way. The
first `python` on PATH is very often the Microsoft Store alias stub: `command -v` finds it
and reports success, but executing it prints an install advert and exits non-zero. The
hook then ran that, and the developer got a Store advertisement where a verification
result belonged -- while `python3` or `py`, both working, sat further down the same list.

`python` is probed FIRST on purpose (a virtualenv should win), which is the same name the
stub occupies, so the ordering that makes the script useful is also what exposes it to the
stub. That is why the fix has to be "execute the candidate", not "reorder the list".

The stub is simulated rather than mocked: a shell script named `python` that behaves the
way the real one does. A test that patched `command -v` would have passed against the
broken script too.

Run: python test_python_probe.py   (or pytest; needs sh on PATH)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACK_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(PACK_DIRECTORY))

from standards_selftest import run_module_tests  # noqa: E402  (path set above)

# One layout everywhere since 2026-09-02, so there is no longer a pair of candidate hook
# directories to try -- see the note in test_hook_blocks.py.
PROBE = PACK_DIRECTORY / "hooks" / "_python.sh"

# What the Microsoft Store alias stub does: says its piece, exits non-zero, runs nothing.
STORE_STUB = (
    "#!/bin/sh\necho 'Python was not found; run without arguments to install from the Microsoft Store'\nexit 9009\n"
)

# A working interpreter, minus the cost of a real one: it only has to answer `-c 'pass'`.
WORKING_PYTHON = "#!/bin/sh\nexit 0\n"


# Resolved from the AMBIENT PATH, once, before any test restricts it. A restricted PATH is
# the whole point of run_probe -- it is what leaves the fake interpreters as the only ones
# the probe can find -- but the executable lookup for `sh` ITSELF reads that same restricted
# PATH, so a bare "sh" killed every test in this file with `FileNotFoundError: 'sh'` before
# the probe ran at all. It failed identically on Linux and on Windows, which is what makes
# it worth a comment: the suite was red wherever it ran, so the file was testing nothing.
#
# None means there is no POSIX shell to run the probe under -- a Windows terminal that is
# not Git Bash. These tests then have no subject and say so by skipping, the same way they
# already skip when PROBE is absent. Failing instead would report a missing shell as a
# broken hook, which is the false alarm that trains people to ignore a red suite.
SHELL = shutil.which("sh")


def run_probe(path_directories: list[Path]) -> str:
    """_python.sh's choice, with PATH restricted to the directories given."""
    environment = dict(os.environ)
    environment["PATH"] = os.pathsep.join(str(directory) for directory in path_directories)
    completed = subprocess.run(
        [SHELL, str(PROBE)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    return completed.stdout.strip()


def make_executable(directory: Path, name: str, body: str) -> None:
    script = directory / name
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)


def test_skips_a_stub_that_resolves_but_does_not_run() -> None:
    """The regression itself: `python` is a Store stub, `python3` works -> pick python3."""
    if PROBE is None or SHELL is None:
        return
    with tempfile.TemporaryDirectory() as tmp:
        binaries = Path(tmp)
        make_executable(binaries, "python", STORE_STUB)
        make_executable(binaries, "python3", WORKING_PYTHON)

        chosen = run_probe([binaries])

        assert chosen == "python3", (
            "a candidate that resolves but exits non-zero must be skipped; the probe "
            f"chose {chosen!r}. This is the Microsoft Store alias stub, and selecting it "
            "makes the hook report a Store advert instead of a verification result."
        )


def test_prefers_the_first_candidate_that_actually_works() -> None:
    """Ordering is still honoured: a working `python` beats python3, so a venv wins."""
    if PROBE is None or SHELL is None:
        return
    with tempfile.TemporaryDirectory() as tmp:
        binaries = Path(tmp)
        make_executable(binaries, "python", WORKING_PYTHON)
        make_executable(binaries, "python3", WORKING_PYTHON)

        assert run_probe([binaries]) == "python", (
            "with both working, `python` must still win -- that is what lets an active "
            "virtualenv take precedence over the system interpreter."
        )


def test_prints_nothing_when_no_candidate_runs() -> None:
    """Every candidate a stub -> print nothing, so callers can decide what that means."""
    if PROBE is None or SHELL is None:
        return
    with tempfile.TemporaryDirectory() as tmp:
        binaries = Path(tmp)
        for name in ("python", "python3", "py"):
            make_executable(binaries, name, STORE_STUB)

        assert run_probe([binaries]) == "", (
            "no working interpreter must print nothing at all; printing a name that does "
            "not run is worse than printing none, because the caller cannot tell."
        )


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "python probe"))

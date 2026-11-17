"""Skip classification -- the half of the gate where a wrong answer is invisible.

A skip is invisible in the exit code, so a pattern that matches too eagerly turns a real
failure into a pass -- the exact shape of bug this pack exists to prevent. Every case here is
one of two kinds: a phrase that MUST read as "the tool is not here", and a phrase that looks
similar and MUST NOT. The second kind is the negative control, and it is the one that matters.

Moved out of `test_standards_gate.py` on 2026-09-05 with the code it tests; the stage
assignment cases stay there.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from standards_gate_outcomes import (
    MISSING_COMMAND_PATTERN,
    MODULE_MISSING_PATTERN,
    UNREACHABLE_TOOLCHAIN_PATTERN,
)
from standards_gates import Gate, Outcome, run_gate


@pytest.mark.parametrize(
    "output",
    [
        "'tsc' is not recognized as an internal or external command,",
        "sh: eslint: command not found",
        "sh: 1: tsc: not found",
        "/bin/sh: 1: vitest: not found",
        "Could not execute because the specified command or program was not found.",
        "Could not execute because the specified command or file was not found.",
        'Run "dotnet tool restore" to make the "dotnet-ef" command available.',
    ],
)
def test_a_missing_tool_behind_a_package_manager_is_a_skip(output: str) -> None:
    """`npm run lint` with no node_modules exits non-zero, and npm itself exists -- so
    FileNotFoundError never fires and this was reported as a lint FAILURE. The developer
    then goes looking for a lint error that was never there. The last two are the dotnet
    driver's phrasings for an absent dotnet-ef -- same shape, one level down."""
    assert MISSING_COMMAND_PATTERN.search(output)


@pytest.mark.parametrize(
    "output",
    [
        "src/app.ts:3:1 - error TS2307: Cannot find module './missing'.",
        "Module not found: Error: Can't resolve './missing' in '/src'",
        "ERROR in ./src/index.js  Module not found: Can't resolve 'react'",
        "error: file not found",
        "Changes have been made to the model since the last migration. Add a new migration.",
    ],
)
def test_a_genuine_broken_import_is_never_skipped(output: str) -> None:
    """The reason MISSING_COMMAND_PATTERN is anchored to shell phrasing rather than to a
    bare "not found". Every bundler and tsc says "not found" about a real broken import,
    and skipping THAT would turn the failure this gate exists to catch into a green run."""
    assert not MISSING_COMMAND_PATTERN.search(output)


def test_a_missing_python_module_is_a_skip() -> None:
    assert MODULE_MISSING_PATTERN.search("No module named 'ruff'")


def test_a_lint_finding_is_not_mistaken_for_a_missing_module() -> None:
    assert not MODULE_MISSING_PATTERN.search("F401 `os` imported but unused")


@pytest.mark.parametrize(
    "output",
    [
        'service "backend" is not running',
        "no such service: backend",
        "Cannot connect to the Docker daemon at npipe:////./pipe/dockerDesktopLinuxEngine.",
        "error during connect: this error may indicate that the docker daemon is not running",
    ],
)
def test_an_unreachable_declared_toolchain_is_a_skip(output: str) -> None:
    """A DECLARED gate (see standards_gate_config) whose environment is a container that is
    not up right now. Same direction as a missing module, and for the same reason: a
    developer with the stack stopped must still be able to push, and CI is the backstop.
    Failing instead would make the honest fix -- declaring the real command -- worse than the
    escape hatch it exists to replace."""
    assert UNREACHABLE_TOOLCHAIN_PATTERN.search(output)


@pytest.mark.parametrize(
    "output",
    [
        "FAILED tests/test_billing.py::test_wallet_debits - AssertionError",
        "E   django.core.exceptions.ImproperlyConfigured: Requested setting INSTALLED_APPS",
        "2 failed, 806 passed in 271.02s",
    ],
)
def test_a_real_test_failure_inside_the_container_is_never_skipped(output: str) -> None:
    """The dangerous direction. A skip is invisible in the exit code, so a pattern that
    matched a genuine suite failure would turn every red push green -- including the
    ImproperlyConfigured that started all of this."""
    assert not UNREACHABLE_TOOLCHAIN_PATTERN.search(output)


# The one shape the three above could not cover, because it is not about the PATTERN -- the
# pattern matches it, correctly, for a container command. It is about WHICH gates the pattern
# may judge. Windows spells errno 2 "The system cannot find the file specified", so an
# ordinary FileNotFoundError raised by a genuinely failing test prints an unreachable-
# toolchain phrase on the estate's own development platform. These two drive `run_gate` end
# to end rather than the regex, because the defect lived in the caller: the regex was right
# and was being asked about a gate it had no business judging. Found in the B3D pack.
WINDOWS_MISSING_FILE = "E   FileNotFoundError: [WinError 2] The system cannot find the file specified: 'x.csv'"


def _gate_failing_with(output: str, declared: bool) -> Gate:
    """A gate that really runs, really fails, and really prints `output`."""
    return Gate(
        "pytest",
        [sys.executable, "-c", f"import sys; sys.stderr.write({output!r}); sys.exit(1)"],
        Path.cwd(),
        declared=declared,
    )


def test_an_undeclared_gate_is_never_excused_by_a_container_phrase() -> None:
    """The regression this scoping exists for. `pytest` is not a container command, so the
    only thing that can print these words is the code under test -- and calling that a skip
    turns a red suite green while changing no exit code."""
    result = run_gate(_gate_failing_with(WINDOWS_MISSING_FILE, declared=False))

    assert result.outcome is Outcome.FAILED, "a plain pytest failure mentioning a missing file must FAIL, not skip"


def test_a_declared_gate_keeps_the_unreachable_skip() -> None:
    """The other direction, and the reason the scoping is a guard rather than a deletion: a
    declared `docker compose exec` whose container is down must still skip, or the honest fix
    (declaring the real command) becomes worse than the escape hatch it replaces."""
    result = run_gate(_gate_failing_with('service "backend" is not running', declared=True))

    assert result.outcome is Outcome.SKIPPED
    assert "not reachable" in result.detail


def test_a_passing_gate_keeps_its_notices_and_nothing_else() -> None:
    """The dependency gate passes BECAUSE of a dated deferral, and that deferral must be seen
    on every run -- so a pass keeps its `notice:` lines, and only those (build noise stays out)."""
    script = "print('restore complete'); print('notice: pypi:ruff covered: deferred until 2026-10-20')"
    result = run_gate(Gate("notices", [sys.executable, "-c", script], Path.cwd()))

    assert result.outcome is Outcome.PASSED
    assert result.notices == ("pypi:ruff covered: deferred until 2026-10-20",)

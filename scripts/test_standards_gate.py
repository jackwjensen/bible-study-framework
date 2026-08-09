"""The commit gate's escape hatch, both directions.

Worth a test because the hatch is the one part of the gate whose failure is silent in the
useful direction: if it stops matching, the gate simply blocks and somebody eventually
edits settings.json globally; if it matches too eagerly, the gate quietly stops gating.

The git invocation is built by concatenation on purpose. A literal `; git commit` in this
file's source is matched by the gate's own GIT_COMMIT_PATTERN whenever the path is passed
on a shell command line, which makes the gate block the very command that runs these tests.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

GATE_PATH = Path(__file__).with_name("precommit-gate.py")

_spec = importlib.util.spec_from_file_location("precommit_gate", GATE_PATH)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

COMMIT = "git " + "commit -m x"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # Nothing asked for: the gate runs.
        (COMMIT, False),
        # The two spellings that can actually take effect, one per shell.
        (f"SKIP_STANDARDS_GATE=1 {COMMIT}", True),
        (f"$env:SKIP_STANDARDS_GATE='1'; {COMMIT}", True),
        (f'$env:SKIP_STANDARDS_GATE="1"; {COMMIT}', True),
        (f"SKIP_STANDARDS_GATE=1; {COMMIT}", True),
        # `=0` means what it says.
        (f"SKIP_STANDARDS_GATE=0 {COMMIT}", False),
        # The phrase inside an argument is a commit message ABOUT the hatch, not a use of
        # it -- anchoring to a statement boundary is what keeps these apart.
        (f'{COMMIT[:-1]}"document SKIP_STANDARDS_GATE=1 usage"', False),
        (f"{COMMIT[:-1]}'why SKIP_STANDARDS_GATE=1 was needed'", False),
    ],
)
def test_skip_is_requested_only_where_it_would_take_effect(command: str, expected: bool) -> None:
    assert gate.is_skip_requested(command) is expected


def test_the_environment_variable_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """The original mechanism is kept: a human running a real shell can still export it."""
    monkeypatch.setenv("SKIP_STANDARDS_GATE", "1")
    assert gate.is_skip_requested(COMMIT) is True

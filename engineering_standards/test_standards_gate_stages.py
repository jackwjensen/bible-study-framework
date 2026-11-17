"""Which scan and which dependency gate each stage gets: COMMIT, PUSH (fast), CI (full).

Jack, 2026-11-17: work moves to branches, a branch push deploys nothing, and CI gates the
deploy -- so the dependency gate (58 s of a 99 s push on InvoTrack) and the whole-tree scan
(25 s) moved from PUSH to CI; pay-down stays at PUSH and CI repeats it. The
stage-assignment tests that predate CI are in `test_standards_gate.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from standards_gates import Stage, build_gates


def make_repo(root: Path, **files: str) -> Path:
    (root / "engineering_standards").mkdir(parents=True, exist_ok=True)
    (root / "engineering_standards" / "check-source-limits.py").write_text("", encoding="utf-8")
    for name, content in files.items():
        path = root / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def gate_names(root: Path, stage: Stage) -> list[str]:
    return [gate.name for gate in build_gates(root, stage)]


def test_the_dependency_gate_runs_in_ci_and_only_where_adopted(tmp_path: Path) -> None:
    """It needs the network, so never at commit or push; and it blocks, so never in a repo
    that has not opted in -- a pack sync must not start refusing deploys in a repo nobody
    converted to pins."""
    repo = make_repo(tmp_path, **{"engineering_standards__deps.py": ""})
    assert "dependencies" not in gate_names(repo, Stage.CI)

    (repo / ".standards-dependencies.json").write_text('{"format": 1, "dependencies": {}}', encoding="utf-8")
    assert "dependencies" in gate_names(repo, Stage.CI)
    assert "dependencies" not in gate_names(repo, Stage.PUSH)
    assert "dependencies" not in gate_names(repo, Stage.COMMIT)


def test_the_dependency_gate_is_strict_in_ci(tmp_path: Path) -> None:
    """`verify.py --ci` on a machine without `CI=true` must still fail on an unreachable
    registry; `--ci` is the `check` subcommand's flag, so it follows `check`."""
    repo = make_repo(tmp_path, **{"engineering_standards__deps.py": ""})
    (repo / ".standards-dependencies.json").write_text('{"format": 1, "dependencies": {}}', encoding="utf-8")
    command = next(gate for gate in build_gates(repo, Stage.CI) if gate.name == "dependencies").command

    assert command[-2:] == ["check", "--ci"]


def test_push_stage_scans_what_changed_since_trunk_and_pays_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pay-down stays at push: cheap, and the author is still at the keyboard."""
    monkeypatch.setattr("standards_gates.trunk_ref", lambda _root: "origin/master")
    command = build_gates(make_repo(tmp_path), Stage.PUSH)[0].command

    assert "--staged" not in command
    assert command[command.index("--since") + 1] == "origin/master"
    assert command[command.index("--paydown") + 1] == "origin/master"


@pytest.mark.parametrize("stage", [Stage.PUSH, Stage.CI])
def test_without_a_trunk_the_scan_is_whole_tree_and_has_no_paydown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: Stage
) -> None:
    """Never an empty scope: a repo whose trunk cannot be resolved is checked in full, and
    has no recorded debt to move."""
    monkeypatch.setattr("standards_gates.trunk_ref", lambda _root: None)
    command = build_gates(make_repo(tmp_path), stage)[0].command

    assert "--since" not in command
    assert "--staged" not in command
    assert "--paydown" not in command


def test_ci_stage_scans_the_whole_tree_and_pays_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("standards_gates.trunk_ref", lambda _root: "origin/master")
    command = build_gates(make_repo(tmp_path), Stage.CI)[0].command

    assert "--since" not in command
    assert "--staged" not in command
    assert command[command.index("--paydown") + 1] == "origin/master"


def test_every_push_gate_also_runs_in_ci(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, **{"app.sln": "", "pyproject.toml": "[tool.ruff]\n[tool.pytest.ini_options]\n"})

    assert set(gate_names(repo, Stage.PUSH)) <= set(gate_names(repo, Stage.CI))

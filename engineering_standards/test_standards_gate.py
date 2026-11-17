"""The gate's two genuinely dangerous decisions: what runs when, and what counts as a skip.

Replaces the escape-hatch regex tests. That hatch was a regex over the command STRING,
because a `PreToolUse` hook inspects a command that has not run yet; the gate is a git hook
now, so `SKIP_STANDARDS_GATE=1 git commit` sets a real environment variable and the whole
pattern -- along with its "is this a use of the hatch or a commit message about it?"
ambiguity -- is gone. `verify.py` reads `os.environ` and that is the entire implementation.

What replaces it is the pair of judgements that can now fail silently:

* Stage assignment. If an expensive gate leaks into the commit stage, commits get slow and
  slow gates get bypassed. If a gate vanishes from BOTH stages, nothing ever runs it.
* Skip classification. A skip is invisible in the exit code, so a pattern that matches too
  eagerly turns a real failure into a pass -- the exact shape of bug this pack exists to
  prevent. Those cases live in `test_standards_gate_outcomes.py`, beside the classifier.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from standards_gates import (
    DEFAULT_GATE_TIMEOUT_SECONDS,
    Gate,
    Outcome,
    Stage,
    build_gates,
    js_runner,
    python_gates,
    run_gate,
)
from standards_projects import declared_tools


def make_repo(root: Path, **files: str) -> Path:
    """A repo skeleton: the scanner plus whatever stack markers a test needs."""
    (root / "engineering_standards").mkdir(parents=True, exist_ok=True)
    (root / "engineering_standards" / "check-source-limits.py").write_text("", encoding="utf-8")
    for name, content in files.items():
        path = root / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def gate_names(root: Path, stage: Stage) -> list[str]:
    return [gate.name for gate in build_gates(root, stage)]


# --------------------------------------------------------------------------------------
# Stage assignment
# --------------------------------------------------------------------------------------


def test_commit_stage_scopes_the_scanner_to_the_staged_files(tmp_path: Path) -> None:
    """The whole point of the commit stage: check the commit, not the repository."""
    repo = make_repo(tmp_path)
    commit_gates = build_gates(repo, Stage.COMMIT)

    assert len(commit_gates) == 1
    assert "--staged" in commit_gates[0].command


def test_the_expensive_gates_never_run_at_commit(tmp_path: Path) -> None:
    """A slow commit gate is a bypassed commit gate.

    Named individually rather than asserted as a count, so that adding a new cheap gate to
    the commit stage does not fail this test while adding a slow one still does.
    """
    repo = make_repo(
        tmp_path,
        **{
            "app.sln": "",
            "app__Migrations__0001_Init.cs": "",
            "package.json": '{"scripts":{"lint":"eslint .","type-check":"tsc"}}',
            "pyproject.toml": "[tool.ruff]\n[tool.pytest.ini_options]\n",
        },
    )

    committed = gate_names(repo, Stage.COMMIT)
    for expensive in ("dotnet build", "dotnet test", "type-check", "lint", "pytest", "ef model sync (app)"):
        assert expensive not in committed

    pushed = gate_names(repo, Stage.PUSH)
    for expensive in ("dotnet build", "type-check", "lint", "pytest", "ef model sync (app)"):
        assert expensive in pushed


def test_the_ef_gate_runs_in_the_project_directory_after_the_build(tmp_path: Path) -> None:
    """`dotnet ef` resolves its project from the working directory, and this estate keeps
    the .csproj one level down -- run at the repo root it fails with "No project was found"
    (measured in InvoTrack, 2026-08-14). And it is ordered after `dotnet build` because
    --no-build reuses that gate's output, same as `dotnet test`."""
    repo = make_repo(tmp_path, **{"app.sln": "", "app__Migrations__0001_Init.cs": ""})

    gates = build_gates(repo, Stage.PUSH)
    names = [gate.name for gate in gates]
    ef_gate = gates[names.index("ef model sync (app)")]

    assert ef_gate.working_directory == repo / "app"
    assert "--no-build" in ef_gate.command
    assert names.index("dotnet build") < names.index("ef model sync (app)")


def test_a_solution_without_migrations_gets_no_ef_gate(tmp_path: Path) -> None:
    """A repo with a .sln and no Migrations/ has no snapshot to drift -- `dotnet ef` there
    would only error, and under --strict a pointless failure blocks a real deploy."""
    repo = make_repo(tmp_path, **{"app.sln": ""})
    assert not any(name.startswith("ef model sync") for name in gate_names(repo, Stage.PUSH))


def test_every_commit_gate_also_runs_at_push(tmp_path: Path) -> None:
    """COMMIT is a subset of PUSH. A gate that ran at commit and not at push would mean a
    developer using the escape hatch once never has it checked again."""
    repo = make_repo(tmp_path, **{"pyproject.toml": "[tool.ruff]\n"})

    commit_subjects = {name.replace(" (staged)", "") for name in gate_names(repo, Stage.COMMIT)}
    push_subjects = set(gate_names(repo, Stage.PUSH))

    assert commit_subjects <= push_subjects


def test_a_repo_with_no_scanner_gets_no_gates(tmp_path: Path) -> None:
    """A repo outside the pack must be a silent no-op, not an error."""
    assert build_gates(tmp_path, Stage.COMMIT) == []


# --------------------------------------------------------------------------------------
# The hang guard: per-repo, stamped once, enforced for real
# --------------------------------------------------------------------------------------


def test_a_declared_timeout_reaches_every_gate(tmp_path: Path) -> None:
    """`gateTimeoutSeconds` is stamped in build_gates, the one door every caller uses, so a
    repo whose honest suite outgrows the default tunes ONE key rather than one call site per
    gate. (The reason the weakening owes is the scanner's to collect, not this builder's.)"""
    repo = make_repo(tmp_path, **{"pyproject.toml": "[tool.ruff]\n[tool.pytest.ini_options]\n"})
    (repo / ".standards.json").write_text('{"gateTimeoutSeconds": 1800}', encoding="utf-8")

    gates = build_gates(repo, Stage.PUSH)
    assert gates, "the fixture must produce gates for the stamp to be observable"
    assert all(gate.timeout_seconds == 1800 for gate in gates)


def test_an_undeclared_timeout_is_the_default(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert all(gate.timeout_seconds == DEFAULT_GATE_TIMEOUT_SECONDS for gate in build_gates(repo, Stage.PUSH))


def test_a_gate_that_outlives_its_timeout_fails_and_names_the_knob(tmp_path: Path) -> None:
    """Driven end to end with a real sleeping process, because the timeout is enforced by
    subprocess.run, not by our code -- a test that mocked it would pin the mock. The detail
    names `gateTimeoutSeconds` so an honestly slow suite gets tuned deliberately instead of
    re-pushed on hope (three sourcetext.ai pushes, 2026-11-11)."""
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    result = run_gate(Gate("slow", command, tmp_path, timeout_seconds=1))

    assert result.outcome is Outcome.FAILED
    assert "timed out after 1s" in result.detail
    assert "gateTimeoutSeconds" in result.detail


# --------------------------------------------------------------------------------------
# run_gate outcomes, against real processes
# --------------------------------------------------------------------------------------


def test_a_clean_command_passes(tmp_path: Path) -> None:
    result = run_gate(Gate("ok", [sys.executable, "-c", "pass"], tmp_path))
    assert result.outcome is Outcome.PASSED
    assert not result.is_failure


def test_run_gate_records_duration(tmp_path: Path) -> None:
    result = run_gate(Gate("slowish", [sys.executable, "-c", "import time; time.sleep(0.2)"], tmp_path))
    assert result.seconds >= 0.2


def test_a_skipped_gate_records_the_time_it_spent(tmp_path: Path) -> None:
    result = run_gate(Gate("absent", ["standards-no-such-binary-xyz"], tmp_path))
    assert result.outcome is Outcome.SKIPPED
    assert result.seconds > 0.0


def test_a_nonzero_exit_fails(tmp_path: Path) -> None:
    result = run_gate(Gate("bad", [sys.executable, "-c", "raise SystemExit(1)"], tmp_path))
    assert result.outcome is Outcome.FAILED
    assert result.is_failure


def test_a_failing_gate_reports_utf8_output_rather_than_crashing(tmp_path: Path) -> None:
    """The runner must decode a child's UTF-8 output, not the machine's locale codec.

    `text=True` on its own decodes with `locale.getencoding()` -- cp1252 on a Danish Windows
    box -- and that fails two ways. Quietly: an em dash comes back as 'a-euro-"', so the
    report is wrong but still a report. Silently: a byte cp1252 leaves undefined raises
    UnicodeDecodeError on subprocess's internal reader thread, which kills the thread while
    `run` returns normally with stdout=None -- and `run_gate`'s `or ""` turns that into a
    FAILED gate carrying NO detail at all. Nothing surfaces but an unhandled-thread traceback.

    Both are only reachable when a gate FAILS, so they hid for as long as everything passed
    and then ate the one report that mattered. The payload covers both halves, so a half-fix
    cannot pass this.

    The child writes explicit BYTES: no literal crosses a shell boundary, so the only thing
    under test is how the parent decodes them.
    """
    # Em dash and ae/oe/aa (all mappable in cp1252, so these mojibake), then the finish-flag
    # emoji F0 9F 8F 81 -- lint-staged really does print it, and its 0x8F is one of the five
    # bytes cp1252 leaves undefined, which is the half that raises rather than corrupts.
    payload = bytes([0xE2, 0x80, 0x94, 0x20, 0xC3, 0xA6, 0xC3, 0xB8, 0xC3, 0xA5, 0x20, 0xF0, 0x9F, 0x8F, 0x81])
    command = [
        sys.executable,
        "-c",
        f"import sys; sys.stdout.buffer.write(bytes({list(payload)})); raise SystemExit(1)",
    ]

    result = run_gate(Gate("noisy", command, tmp_path))

    assert result.outcome is Outcome.FAILED
    assert payload.decode("utf-8") in result.detail


def test_an_absent_executable_skips_rather_than_blocking(tmp_path: Path) -> None:
    """Fail-open is deliberate: a machine with no dotnet SDK must still be able to commit a
    README, and CI is authoritative for anything that reaches the remote. What must NOT
    happen is that the skip looks like a pass -- hence the named outcome."""
    result = run_gate(Gate("absent", ["standards-no-such-binary-xyz"], tmp_path))
    assert result.outcome is Outcome.SKIPPED
    assert not result.is_failure
    assert result.detail


# --------------------------------------------------------------------------------------
# Toolchain discovery
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lockfile", "expected"),
    [("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("package-lock.json", "npm")],
)
def test_the_lockfile_picks_the_package_manager(tmp_path: Path, lockfile: str, expected: str) -> None:
    """Hardcoding pnpm made an npm repo's lint and type-check vanish silently: pnpm is
    absent, FileNotFoundError fires, the gate skips, and nothing says so."""
    (tmp_path / lockfile).touch()
    assert js_runner(tmp_path).startswith(expected)


def test_no_lockfile_falls_back_to_npm(tmp_path: Path) -> None:
    assert js_runner(tmp_path).startswith("npm")


def test_a_comment_mentioning_a_tool_does_not_enable_its_gate(tmp_path: Path) -> None:
    """The discovery used to substring-match the raw pyproject.toml text, so a comment
    SAYING there is no ruff config switched the ruff gate on. Caught when this repo's own
    pyproject.toml said "Deliberately NO [tool.ruff] section" and the build then failed on
    ruff findings it had never opted into."""
    repo = make_repo(
        tmp_path,
        **{
            "pyproject.toml": (
                "# Deliberately NO [tool.ruff] section.\n[tool.pytest.ini_options]\ntestpaths = ['scripts']\n"
            )
        },
    )

    names = gate_names(repo, Stage.PUSH)
    assert "ruff" not in names
    assert "pytest" in names


def test_a_declared_tool_does_enable_its_gate(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, **{"pyproject.toml": "[tool.ruff]\nline-length = 100\n"})
    assert "ruff" in gate_names(repo, Stage.PUSH)


def test_a_malformed_pyproject_says_so_rather_than_silently_dropping_gates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(tmp_path, **{"pyproject.toml": "[tool.ruff\nbroken = \n"})

    assert "ruff" not in gate_names(repo, Stage.PUSH)
    assert "will NOT run" in capsys.readouterr().err


# --------------------------------------------------- declared gate commands (containers)


def _repo_with(tmp_path, config):
    """A minimal repo: one Python project declaring pytest, plus a .standards.json."""
    import json  # noqa: PLC0415  (local by design: imported after this test builds its tree)

    (tmp_path / ".standards.json").write_text(json.dumps(config), encoding="utf-8")
    project = tmp_path / "packages" / "backend"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]", encoding="utf-8")
    return tmp_path


def test_a_declared_command_replaces_the_discovered_interpreter(tmp_path):
    """The pack assumes a project's tools run on this machine. For a project whose
    environment lives in a container that is false, and the discovered host interpreter
    fails on the application's own imports rather than on anything anyone wrote."""
    root = _repo_with(
        tmp_path,
        {
            "gateCommands": {
                "packages/backend": {"pytest": {"command": ["docker", "compose", "exec", "-T", "backend", "pytest"]}}
            }
        },
    )

    gate = next(g for g in python_gates(root) if g.name.startswith("pytest"))

    assert gate.command == ["docker", "compose", "exec", "-T", "backend", "pytest"]


def test_run_from_is_repo_relative_not_project_relative(tmp_path):
    """A container command usually has to run where the compose file is, not where the
    code is — so without this the gate would run in packages/backend and find no stack."""
    root = _repo_with(
        tmp_path,
        {
            "gateCommands": {
                "packages/backend": {
                    "pytest": {"command": ["docker", "compose", "exec", "backend", "pytest"], "runFrom": "."}
                }
            }
        },
    )

    gate = next(g for g in python_gates(root) if g.name.startswith("pytest"))

    assert gate.working_directory == root


def test_an_undeclared_gate_still_uses_the_discovered_interpreter(tmp_path):
    """The override is opt-in per project and per tool; every other repo is unaffected."""
    root = _repo_with(tmp_path, {})

    gate = next(g for g in python_gates(root) if g.name.startswith("pytest"))

    assert gate.command[1:] == ["-m", "pytest", "-q", "-x"]


def test_a_repo_with_no_config_at_all_is_unaffected(tmp_path):
    """.standards.json is optional, and an absent one must not break gate discovery."""
    project = tmp_path / "packages" / "backend"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]", encoding="utf-8")

    gates = python_gates(tmp_path)

    assert any(g.name.startswith("pytest") for g in gates)


# ------------------------------------------- keeping a consumer's gates off the pack's files


def _consumer_with_an_excluded_directory(tmp_path: Path) -> Path:
    """A consuming repo whose OWN ruff config excludes a directory holding a finding.

    `generated/` stands in for `migrations/`, which is what `ruff-standards.toml` excludes
    and where this was found. The unused import is F401, in ruff's default rule set, so the
    fixture needs no rule selection of its own.
    """
    return make_repo(
        tmp_path,
        **{
            "pyproject.toml": '[tool.ruff]\nexclude = ["generated"]\n',
            "generated/legacy.py": "import os\n",
        },
    )


def _ruff_check_gate(repo: Path) -> Gate:
    return next(gate for gate in python_gates(repo, ruff_only=True) if gate.name == "ruff")


# The three tests below run REAL ruff, and run_gate rightly SKIPS a tool that is not
# installed -- so without this they assert PASSED against a correct SKIPPED wherever ruff is
# absent. That was every .NET consumer whose CI installs only pytest: InvoTrack's deploy was
# blocked from 2026-09-28 by these three alone. The verdict follows the same declaration the
# gate itself follows: a checkout whose pyproject.toml declares [tool.ruff] (the pack does)
# must have ruff, so its absence FAILS rather than skips -- otherwise the pack's own CI losing
# its `pip install ruff` would turn these into a silent skip, the exact shape of bug the pack
# exists to prevent. A checkout that never declared ruff gets a named skip.
CHECKOUT_ROOT = Path(__file__).resolve().parents[1]


def ruff_verdict(installed: bool, declared: bool) -> str:
    """`run`, `skip` or `fail` for a test that needs a real ruff."""
    if installed:
        return "run"
    return "fail" if declared else "skip"


def _checkout_declares_ruff() -> bool:
    # Existence first: declared_tools() reports an unreadable pyproject.toml loudly, and a
    # consumer with no Python project of its own is not a defect worth a line on stderr.
    return (CHECKOUT_ROOT / "pyproject.toml").is_file() and "ruff" in declared_tools(CHECKOUT_ROOT)


@pytest.fixture
def real_ruff() -> None:
    verdict = ruff_verdict(importlib.util.find_spec("ruff") is not None, _checkout_declares_ruff())
    if verdict == "fail":
        pytest.fail(f"ruff is declared in {CHECKOUT_ROOT / 'pyproject.toml'} but not installed for {sys.executable}")
    if verdict == "skip":
        pytest.skip("ruff is not installed, and this checkout does not declare [tool.ruff]")


@pytest.mark.parametrize(
    ("installed", "declared", "expected"),
    [(True, True, "run"), (True, False, "run"), (False, True, "fail"), (False, False, "skip")],
)
def test_a_missing_ruff_skips_only_where_nobody_declared_it(installed: bool, declared: bool, expected: str) -> None:
    assert ruff_verdict(installed, declared) == expected


@pytest.mark.usefixtures("real_ruff")
def test_the_pack_exclusion_leaves_the_repo_s_own_ruff_excludes_standing(tmp_path: Path) -> None:
    """ruff's `--exclude` REPLACES the configured exclude list; only `--extend-exclude` adds.

    So the flag that keeps a consumer's gate off the pack's files was also un-excluding
    everything `ruff-standards.toml` excludes -- `**/migrations/**`, `.venv`, `node_modules`,
    `staticfiles` -- for the duration of every gate run. Found in sourcetext.ai on 2026-09-09
    as three S608 findings inside `migrations/versions/`, reachable ONLY under the gate and
    not under a direct `ruff check .`: a finding that exists in one invocation and not the
    other is the worst shape a finding can take, because it makes the gate the suspect.
    """
    gate = _ruff_check_gate(_consumer_with_an_excluded_directory(tmp_path))

    assert run_gate(gate).outcome is Outcome.PASSED


@pytest.mark.usefixtures("real_ruff")
def test_the_pack_s_own_files_are_still_excluded_from_a_consumer_s_ruff(tmp_path: Path) -> None:
    """The other half, asserted separately so a fix cannot trade one for the other.

    Every finding in the pack's directory is unfixable in a consuming repo -- the next sync
    overwrites the file -- which is the whole reason the flag exists.
    """
    repo = _consumer_with_an_excluded_directory(tmp_path)
    (repo / "engineering_standards" / "standards_thing.py").write_text("import os\n", encoding="utf-8")

    assert run_gate(_ruff_check_gate(repo)).outcome is Outcome.PASSED


@pytest.mark.usefixtures("real_ruff")
def test_the_format_gate_gets_the_same_additive_exclusion(tmp_path: Path) -> None:
    """`ruff format` is handed the identical list, so it fails the identical way.

    Asserted on its own because "the same list" is a property of today's code, not a
    guarantee: the day someone gives the formatter its own arguments, this is what says
    they must still be additive.
    """
    repo = _consumer_with_an_excluded_directory(tmp_path)
    (repo / "generated" / "unformatted.py").write_text("x  =  1\n", encoding="utf-8")

    gate = next(gate for gate in python_gates(repo, ruff_only=True) if gate.name == "ruff format")

    assert run_gate(gate).outcome is Outcome.PASSED

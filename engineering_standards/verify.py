#!/usr/bin/env python3
"""Verify this repository. The one command the hooks, CI, and a human all run.

    python engineering_standards/verify.py --staged                # what a commit changes  (pre-commit)
    python engineering_standards/verify.py                         # the fast push stage: changes since trunk  (pre-push, you)
    python engineering_standards/verify.py --ci --strict --format github    # the full gate  (CI, every branch push)
    python engineering_standards/verify.py --list                  # say what would run, run nothing

With neither --staged nor --ci, the environment variable CI=true (set by every CI service)
selects the CI stage, so a workflow still calling plain `verify.py --strict` keeps the full gate.

WHY THIS EXISTS. "How do I check this repo?" had three answers that could disagree: the
commit hook's own discovery logic, a hardcoded step list per stack in `ci/check-*.yml`,
and a verify command written out in prose in every repo's CLAUDE.md. The pack's central
rule is that a standard restated in two layers will drift -- and the thing being restated
here was the command that checks the standards. Now `standards_gates.build_gates` is the
single definition and everything else calls it.

REPORTING SKIPS IS THE POINT. A gate whose toolchain is missing is skipped, not failed
(see run_gate for why that direction is right). What was wrong before is that a skip was
invisible: a machine with no dotnet SDK printed exactly what a fully verified one printed.
This command always says which gates ran and which did not, so "it passed" is a claim you
can read rather than one you have to trust.

Exit codes: 0 = clean, 1 = a gate failed, 2 = bad invocation.

Source of truth: engineering-standards/engineering_standards/verify.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# BEFORE EVERY OTHER PACK IMPORT, and that order is the whole point: the modules below are
# written for the floor this repo declares, so an interpreter beneath it cannot even parse
# them. Checked here the answer is a sentence; checked one line later it is a SyntaxError
# traceback from a hook that was supposed to be reporting on somebody's commit.
from standards_runtime import require_supported_python  # noqa: E402  (must precede the rest)

require_supported_python(Path(__file__).resolve().parent)

from standards_gates import (  # noqa: E402  (path must be set before the import)
    GateResult,
    Outcome,
    Stage,
    build_gates,
    run_gates,
)
from standards_git import repository_root  # noqa: E402  (same reason)
from standards_hook_wiring import warning as hook_wiring_warning  # noqa: E402  (same reason)
from standards_paydown import has_baseline, paydown_gap  # noqa: E402  (same reason)

SKIP_VARIABLE = "SKIP_STANDARDS_GATE"


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run this repository's verification gates.")
    stages = parser.add_mutually_exclusive_group()
    stages.add_argument(
        "--staged",
        action="store_true",
        help="Commit stage: fast checks scoped to the staged files (default is the push stage)",
    )
    stages.add_argument(
        "--ci",
        action="store_true",
        help="CI stage: the full gate, including the dependency gate and the whole-tree scan "
        "(selected by CI=true when no stage flag is given)",
    )
    parser.add_argument("--root", default=None, help="Repository root (default: discover from cwd)")
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_only",
        help="Print the gates that would run and exit",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat a skipped gate as a failure (for CI, where every toolchain must be present)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "github"),
        default="text",
        help="Reporting format for the source scanner; 'github' emits workflow annotations",
    )
    return parser.parse_args(argv)


def select_stage(arguments: argparse.Namespace) -> Stage:
    """The stage: an explicit flag wins; otherwise CI=true (every CI service sets it) means CI."""
    if arguments.staged:
        return Stage.COMMIT
    if arguments.ci or os.environ.get("CI", "").lower() == "true":
        return Stage.CI
    return Stage.PUSH


def resolve_root(explicit: str | None) -> Path | None:
    if explicit is not None:
        root = Path(explicit).resolve()
        return root if root.is_dir() else None
    return repository_root(Path.cwd())


def summarise(results: list[GateResult]) -> str:
    counts = dict.fromkeys(Outcome, 0)
    for result in results:
        counts[result.outcome] += 1

    parts = [f"{counts[Outcome.PASSED]} passed"]
    if counts[Outcome.FAILED]:
        parts.append(f"{counts[Outcome.FAILED]} failed")
    if counts[Outcome.SKIPPED]:
        parts.append(f"{counts[Outcome.SKIPPED]} skipped")
    return ", ".join(parts)


def report(results: list[GateResult]) -> None:
    """Name every gate and what became of it.

    Skips are printed on stdout even for a fully clean run, because the whole reason they
    are named is that a silently skipped gate looks exactly like a passing one.
    """
    for result in results:
        if result.outcome is Outcome.PASSED:
            print(f"  ok      {result.gate.name} ({result.seconds:.1f} s)")
            for notice in result.notices:
                print(f"            {notice}")
        elif result.outcome is Outcome.SKIPPED:
            print(f"  skipped {result.gate.name} ({result.seconds:.1f} s) -- {result.detail}")

    for result in results:
        if result.is_failure:
            print(f"\nFAILED: {result.gate.name} ({result.seconds:.1f} s)\n{result.detail}", file=sys.stderr)


def report_paydown_gap(root: Path, stage: Stage, strict: bool) -> bool:
    """Name a pay-down check CI could not run; True when --strict makes that a failure.

    The pay-down check is an argument on the source-limits gate, so when CI cannot resolve a
    trunk it simply is not passed -- and the gate still prints `ok`. Named here so "it passed"
    and "it never ran" stay apart. Under --strict it fails only where there is debt to pay
    down: a repo with no baseline loses nothing by the check not running.
    """
    gap = paydown_gap(root) if stage is Stage.CI else None
    if gap is None:
        return False
    print(f"  notice  {gap}")
    if not (strict and has_baseline(root)):
        return False
    print(
        "\nverify: --strict, and this repo has a baseline, so a pay-down check that "
        "could not run counts as a failure. Check out with full history.",
        file=sys.stderr,
    )
    return True


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)

    if os.environ.get(SKIP_VARIABLE) == "1":
        print(f"verify: skipped entirely ({SKIP_VARIABLE}=1)")
        return 0

    root = resolve_root(arguments.root)
    if root is None:
        print("error: not inside a git repository, and no valid --root given", file=sys.stderr)
        return 2

    stage = select_stage(arguments)
    gates = build_gates(root, stage, arguments.format)

    if arguments.list_only:
        print(f"verify: {len(gates)} gate(s) at the {stage.value} stage")
        for gate in gates:
            where = gate.working_directory.relative_to(root).as_posix() or "."
            print(f"  {gate.name}\n      {' '.join(gate.command)}   [in {where}]")
        return 0

    if not gates:
        print(f"verify: no gates apply to this repository at the {stage.value} stage")
        return 0

    results = run_gates(gates)
    report(results)

    failures = [result for result in results if result.is_failure]
    skipped = [result for result in results if result.outcome is Outcome.SKIPPED]

    # A skip is tolerable on a developer machine and never in CI. Locally it means "you do
    # not have the .NET SDK, commit your README anyway, CI will catch it" -- but that whole
    # argument rests on CI actually catching it. If CI skipped for the same reason (a failed
    # `pnpm install`, a missing interpreter), the backstop is not a backstop and the run
    # goes green having checked nothing.
    strict_failure = arguments.strict and bool(skipped)
    if strict_failure:
        print(
            "\nverify: --strict, so a skipped gate counts as a failure. "
            "These toolchains must be installed here:\n"
            + "\n".join(f"  {result.gate.name} -- {result.detail}" for result in skipped),
            file=sys.stderr,
        )

    # Called first so its notice always prints, whatever the skips already decided.
    strict_failure = report_paydown_gap(root, stage, arguments.strict) or strict_failure

    # Beyond the commit stage only, and not a gate: holding an unwired clone is a state of
    # somebody's working copy, not a finding about the code, so it changes no exit code. "Your
    # commit gate is not running" cannot be said at the commit stage, because if it is true
    # then no commit-stage check runs to say it -- push is the one moment the message reaches
    # anybody, whether the hook or a human invoked this. Still asked at the CI stage because a
    # developer can run `--ci` locally, exactly as the old whole-repo default was run; silent
    # under a real CI run, where no hooks is correct.
    if stage is not Stage.COMMIT:
        wiring = hook_wiring_warning(root)
        if wiring is not None:
            print(wiring, file=sys.stderr)

    if not failures and not strict_failure:
        print(f"verify: {summarise(results)} ({stage.value} stage)")
        return 0

    print(
        f"\nverify: {summarise(results)} ({stage.value} stage). "
        f"Fix the above, then try again.\n"
        f"(Set {SKIP_VARIABLE}=1 only when the failure is provably unrelated.)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

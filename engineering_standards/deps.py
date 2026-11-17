#!/usr/bin/env python3
"""Keep every pinned dependency current, deliberately: the commands behind the dependency gate.

    python engineering_standards/deps.py init                  adopt the gate in this repository
    python engineering_standards/deps.py status                every dependency and its verdict
    python engineering_standards/deps.py check [--ci]          the gate: exit 1 if anything blocks
    python engineering_standards/deps.py investigate <name>    what changed between pin and newest
    python engineering_standards/deps.py note <name> <version> "<text>" --by <who>
    python engineering_standards/deps.py record <name> <version> updated --by <who>
    python engineering_standards/deps.py record <name> <version> defer --until YYYY-MM-DD --reason "..." --by <who>
    python engineering_standards/deps.py record <name> <version> reject --reason "..." --by <who>

ONE PATH FOR PEOPLE AND FOR CLAUDE (plan 2026-10-02, decision 9). These are plain commands; the
`/update-deps` skill calls exactly them and has no route of its own, so whatever a person can do
at a terminal is what the AI does, and the gate cannot tell -- and need not care -- who typed.

THE GATE BLOCKS (decision 2). Every pinned dependency is compared with its registry in CI
(verify's CI stage, not push since 2026-11-17), through a one-hour per-machine cache; a newer
release no decision covers stops the deploy until somebody has looked at it. The escape hatch is `record ... defer`, always dated and never longer
than 30 days. A deferral or a rejection is a HUMAN decision: Claude proposes one, never records
it. An unreachable registry warns on a developer machine and fails in CI (`--ci`, which verify's
CI stage always passes, or the `CI` variable every CI service sets), where CI is the backstop.

`<name>` is a package name (`ruff`) or, where two ecosystems share a name, `ecosystem:name`
(`pypi:ruff`, `nuget:xunit`, `docker:mysql`, `github-action:actions/checkout`).

Exit codes: 0 clean, 1 blocked or refused, 2 bad invocation.

Source of truth: engineering-standards/engineering_standards/deps.py
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_deps_cache import add_note, notes_for  # noqa: E402  (path must be set before the import)
from standards_deps_declared import Dependency, declared_dependencies  # noqa: E402  (same reason)
from standards_deps_installable import installable  # noqa: E402  (same reason)
from standards_deps_lifecycle import lifecycle, product_for  # noqa: E402  (same reason)
from standards_deps_pnpm import ReleaseAgeGuard, release_age_guard  # noqa: E402  (same reason)
from standards_deps_record import (  # noqa: E402  (same reason)
    DEFERRED,
    REJECTED,
    UPDATED,
    Decision,
    RecordError,
    add_decision,
    has_record,
    load_record,
    record_path,
    save_record,
)
from standards_deps_registry import Fetch, NotFound, http_fetch, lookup  # noqa: E402  (same reason)
from standards_deps_verdict import (  # noqa: E402  (same reason)
    CURRENT,
    UNREACHABLE,
    Verdict,
    display,
    judge,
    with_waiting,
)
from standards_gate_outcomes import NOTICE_PREFIX  # noqa: E402  (same reason)
from standards_git import repository_root  # noqa: E402  (same reason)

COMMAND = "python engineering_standards/deps.py"
ACTIONS = {"updated": UPDATED, "defer": DEFERRED, "reject": REJECTED}


def now() -> datetime:
    return datetime.now(UTC)


def today() -> date:
    return now().date()


def install_guard(root: Path) -> ReleaseAgeGuard | None:
    """The package manager's release-age guard, or None. A configuration the gate cannot read is
    said out loud and treated as no guard -- the gate then blocks where pnpm might not, never the
    other way round."""
    try:
        return release_age_guard(root)
    except (OSError, ValueError) as error:
        print(
            f"warning: pnpm's release-age settings could not be read ({error}); judging without them", file=sys.stderr
        )
        return None


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Keep pinned dependencies current, deliberately.")
    parser.add_argument("--root", type=Path, help="repository root (default: the git root here)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="adopt the dependency gate in this repository")
    commands.add_parser("status", help="every dependency and its verdict")
    check = commands.add_parser("check", help="the gate: exit 1 when anything blocks")
    check.add_argument("--ci", action="store_true", help="an unreachable registry fails instead of warning")
    investigate = commands.add_parser("investigate", help="what changed between the pin and the newest release")
    investigate.add_argument("name")
    note = commands.add_parser("note", help="remember a finding about one release, for every repo here")
    note.add_argument("name")
    note.add_argument("version")
    note.add_argument("text")
    note.add_argument("--by", required=True)
    record = commands.add_parser("record", help="record a decision about one release")
    record.add_argument("name")
    record.add_argument("version")
    record.add_argument("action", choices=sorted(ACTIONS))
    record.add_argument("--until", help="deferrals only: YYYY-MM-DD, at most 30 days out")
    record.add_argument("--reason", default="", help="required for defer and reject")
    record.add_argument("--by", required=True, help="who decided -- a deferral or rejection is a person's call")
    return parser.parse_args(argv)


def resolve(dependencies: list[Dependency], name: str) -> list[Dependency]:
    """The declarations `name` refers to: an exact key, else every package with that bare name."""
    exact = [dependency for dependency in dependencies if dependency.key == name]
    if exact:
        return exact
    return [dependency for dependency in dependencies if dependency.name.lower() == name.lower()]


def _sites(dependencies: list[Dependency]) -> str:
    return ", ".join(f"{dependency.path.as_posix()}:{dependency.line}" for dependency in dependencies)


def verdict_for(dependency: Dependency, record: dict, fetch: Fetch, guard: ReleaseAgeGuard | None) -> Verdict:
    """Releases from the registry, cut to those the repo can install today, the line's support
    dates from endoflife.date, then the judgement.

    A lifecycle that cannot be fetched is UNREACHABLE, never quietly "no lifecycle": falling back
    would judge a database by the package rule and could call a dying line healthy.
    """
    cycles = None
    lifecycle_error = None
    product = product_for(dependency)
    if product:
        try:
            cycles = lifecycle(product, fetch)
        except (OSError, ValueError, KeyError, TypeError, NotFound) as error:
            # standards: technical-error-shown exempt -- read by the developer running the gate,
            # for whom the network or parse error IS the actionable detail; no end user sees it.
            lifecycle_error = f"{product}: {error}"
    answer, waiting = installable(dependency, lookup(dependency, fetch), guard, now())
    return with_waiting(judge(dependency, answer, cycles, record, today(), lifecycle_error), waiting)


def verdicts(root: Path, fetch: Fetch) -> list[tuple[Verdict, list[Dependency]]]:
    """One verdict per distinct (dependency, pinned version), with every site that declares it."""
    grouped: dict[tuple[str, str], list[Dependency]] = {}
    for dependency in declared_dependencies(root):
        grouped.setdefault((dependency.key, dependency.version), []).append(dependency)
    record = load_record(root)
    guard = install_guard(root)
    return [(verdict_for(sites[0], record, fetch, guard), sites) for sites in grouped.values()]


def _print_verdicts(results: list[tuple[Verdict, list[Dependency]]], everything: bool) -> None:
    for verdict, sites in results:
        if verdict.state == CURRENT and not everything:
            continue
        label = "BLOCKED" if verdict.blocks else verdict.state
        print(f"  {label:<11} {verdict.dependency.key}  ({_sites(sites)})  {verdict.message}")


def _print_gate_lines(results: list[tuple[Verdict, list[Dependency]]]) -> None:
    """The gate's own output, shaped for verify.py.

    A blocked dependency is a FINDING -- `path:line: [dependency-<state>] ...` -- which is the
    shape verify's failure detail keeps, and each carries its own next command, because verify
    shows at most fifteen lines and a refusal must say what to do even when it is cut short.
    Everything that passes but must still be SEEN -- a deferral, a rejection, an unreachable
    registry -- is a `notice:` line, which verify prints under the gate's `ok` (decision 3:
    every active deferral is visible on every run).
    """
    for verdict, sites in results:
        key = verdict.dependency.key
        if verdict.blocks:
            first = sites[0]
            print(
                f"{first.path.as_posix()}:{first.line}: [dependency-{verdict.state}] {key}: {verdict.message}"
                f" -- next: {COMMAND} investigate {key}"
            )
        elif verdict.state != CURRENT:
            print(f"{NOTICE_PREFIX}{key} {verdict.state} ({_sites(sites)}): {verdict.message}")


def command_check(root: Path, fetch: Fetch, ci: bool) -> int:
    if not has_record(root):
        print(f"dependency gate: not adopted here (no {record_path(root).name}); `{COMMAND} init` adopts it")
        return 0
    results = verdicts(root, fetch)
    blocked = [verdict for verdict, _ in results if verdict.blocks]
    unreachable = [verdict for verdict, _ in results if verdict.state == UNREACHABLE]
    _print_gate_lines(results)
    print(
        f"dependency gate: {len(blocked)} blocked, {len(unreachable)} unreachable, "
        f"{len(results) - len(blocked) - len(unreachable)} current or covered"
    )
    if blocked:
        print(
            f"\nEach blocked release must be looked at before this can deploy:\n"
            f"  {COMMAND} investigate <name>          what changed\n"
            f"  then pin it and: {COMMAND} record <name> <version> updated --by <you>\n"
            f"  or ask Claude to run /update-deps.\n"
            f"Not taking it is a person's decision, always dated:\n"
            f'  {COMMAND} record <name> <version> defer --until YYYY-MM-DD --reason "..." --by <you>\n'
            f'  {COMMAND} record <name> <version> reject --reason "..." --by <you>',
            file=sys.stderr,
        )
        return 1
    if unreachable and ci:
        print("dependency gate: a registry could not be reached, and in CI that is a failure", file=sys.stderr)
        return 1
    return 0


def command_investigate(root: Path, fetch: Fetch, name: str) -> int:
    matches = resolve(declared_dependencies(root), name)
    if not matches:
        print(f"error: nothing in this repository declares {name}", file=sys.stderr)
        return 2
    record = load_record(root)
    guard = install_guard(root)
    for dependency in {(match.key, match.version): match for match in matches}.values():
        verdict = verdict_for(dependency, record, fetch, guard)
        position = verdict.position
        print(f"{dependency.key}")
        pinned = f"{dependency.version}  # {dependency.label}" if dependency.label else dependency.version
        print(f"  pinned:   {pinned}  ({_sites([m for m in matches if m.version == dependency.version])})")
        if position is not None:
            support = position.support_end.isoformat() if position.support_end else "no published end"
            shipped = position.last_shipped()
            lifecycle_note = (
                f"supported until {support}" if position.has_lifecycle else f"last shipped {shipped or '?'}"
            )
            print(f"  line:     {position.line} ({lifecycle_note})")
            print(f"  in line:  {display(position.in_line)}")
            print(f"  newest:   {display(position.newest)}{'  (a later line)' if position.newer_line else ''}")
        print(f"  verdict:  {verdict.state} -- {verdict.message}")
        for decision in record.get(dependency.key, []):
            print(
                f"  decided:  {decision.date} {decision.version} {decision.decision} by {decision.by} {decision.reason}".rstrip()
            )
        for note in notes_for(dependency.key, verdict.target or ""):
            print(f"  note:     {note.get('date')} by {note.get('by')}: {note.get('text')}")
    return 0


def command_record(root: Path, arguments: argparse.Namespace) -> int:
    matches = resolve(declared_dependencies(root), arguments.name)
    keys = {match.key for match in matches}
    if len(keys) != 1:
        found = ", ".join(sorted(keys)) or "nothing"
        print(f"error: {arguments.name} names {found}; give ecosystem:name", file=sys.stderr)
        return 2
    key = keys.pop()
    action = ACTIONS[arguments.action]
    stale = [match for match in matches if match.release != arguments.version]
    if action == UPDATED and stale:
        print(f"refused: {key} is still pinned at {stale[0].version} in {_sites(stale)}", file=sys.stderr)
        return 1
    decision = Decision(
        version=arguments.version,
        decision=action,
        date=today().isoformat(),
        by=arguments.by,
        reason=arguments.reason,
        until=arguments.until,
    )
    try:
        add_decision(root, key, decision, today())
    except (RecordError, ValueError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    print(f"recorded: {key} {arguments.version} {action}")
    return 0


def command_init(root: Path, _arguments: argparse.Namespace, _fetch: Fetch) -> int:
    if not has_record(root):
        save_record(root, {})
    print(f"dependency gate adopted: {record_path(root).name}")
    return 0


def command_status(root: Path, _arguments: argparse.Namespace, fetch: Fetch) -> int:
    _print_verdicts(verdicts(root, fetch), everything=True)
    return 0


def command_note(root: Path, arguments: argparse.Namespace, _fetch: Fetch) -> int:
    key = next((match.key for match in resolve(declared_dependencies(root), arguments.name)), arguments.name)
    add_note(key, arguments.version, {"date": today().isoformat(), "by": arguments.by, "text": arguments.text})
    print(f"noted: {key} {arguments.version}")
    return 0


HANDLERS = {
    "init": command_init,
    "status": command_status,
    "check": lambda root, arguments, fetch: command_check(
        root, fetch, arguments.ci or os.environ.get("CI", "").lower() == "true"
    ),
    "investigate": lambda root, arguments, fetch: command_investigate(root, fetch, arguments.name),
    "note": command_note,
    "record": lambda root, arguments, _fetch: command_record(root, arguments),
}


def main(argv: list[str], fetch: Fetch = http_fetch) -> int:
    arguments = parse_arguments(argv)
    root = arguments.root or repository_root(Path.cwd())
    if root is None or not root.is_dir():
        print("error: not inside a git repository, and no valid --root given", file=sys.stderr)
        return 2
    return HANDLERS[arguments.command](root, arguments, fetch)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

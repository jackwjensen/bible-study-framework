#!/usr/bin/env python3
"""The dependency gate's judgement of one declared dependency. Pure.

A declaration, a registry answer, its lifecycle, the record and a date in; a verdict out. No
network, no filesystem -- which is what lets every state below be pinned by a test.

| State | Why | Blocks? |
|---|---|---|
| CURRENT         | the newest release in its line, and no later line worth naming | no |
| MAJOR_AVAILABLE | a later line exists; the pinned one has over a year of support | no -- a notice |
| COVERED         | the blocking release is named by an active deferral or rejection | no, printed |
| NEWER           | a newer release IN THE PINNED LINE nobody has looked at (see below) | YES |
| MAJOR_DUE       | a later line exists; the pinned one has a year or less of support | YES |
| UNSUPPORTED     | the pinned line is past its end of support | YES |
| EXPIRED         | the deferral that covered the blocking release has run out | YES |
| UNPINNED        | a range, a floating tag, a branch -- a version nobody chose | YES |
| UNREACHABLE     | the registry or the lifecycle source did not answer | locally no; in CI yes |
| WAITING         | a newer release exists, but the package manager will not install it yet | no -- a notice |

WAITING is decided before judging, by `standards_deps_installable`: the gate is shown only the
releases the repository can install today, so a release inside pnpm's release-age guard -- or one
its maintainer withdrew -- can never be the release a push is blocked on. `with_waiting` then
turns a non-blocking verdict into WAITING when such a release sits above the pin in its line, so
it is seen on every run until it is installable and judged like any other.

Decision 14 of the 2026-10-02 plan is the split between NEWER and the major states: a release in
the pinned line always blocks; a new major is only mandatory once the pinned line's support runs
under a year (standards_deps_lines). THE PINNED LINE is the lifecycle cycle where endoflife.date
publishes one -- so NEWER is a patch there (MySQL 8.4.7 -> 8.4.8) -- else the semantic major (or
`0.minor` below 1.0), so for most packages NEWER includes MINOR releases too: QuestPDF 2026.9.1
-> 2026.9.2 and -> 2026.10.0 are both NEWER, being one line `2026`.

An `updated` record for a release the manifest does not pin is still blocking: the record cannot
claim a move the code never made.

Source of truth: engineering-standards/engineering_standards/standards_deps_verdict.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from standards_deps_declared import ACTION, Dependency
from standards_deps_installable import Waiting
from standards_deps_lifecycle import Cycle
from standards_deps_lines import Position, assess, is_current, major_is_due, pinned_name
from standards_deps_record import DEFERRED, REJECTED, Decision, covering_decision
from standards_deps_registry import Answer, Release, semver_line, version_key

CURRENT = "current"
MAJOR_AVAILABLE = "major-available"
COVERED = "covered"
NEWER = "newer"
MAJOR_DUE = "major-due"
UNSUPPORTED = "unsupported"
EXPIRED = "expired"
UNPINNED = "unpinned"
UNREACHABLE = "unreachable"
WAITING = "waiting"

BLOCKING = frozenset({NEWER, MAJOR_DUE, UNSUPPORTED, EXPIRED, UNPINNED})


@dataclass(frozen=True)
class Verdict:
    dependency: Dependency
    state: str
    message: str
    target: str | None = None  # the release a decision would have to name (an Action: its SHA)
    decision: Decision | None = None
    position: Position | None = None

    @property
    def blocks(self) -> bool:
        return self.state in BLOCKING


def target_of(dependency: Dependency, release: Release) -> str:
    """What a decision names: an Action's commit (its pin), otherwise the version."""
    if dependency.ecosystem == ACTION and release[2]:
        return release[2]
    return release[0]


def display(release: Release | None) -> str:
    """How a person names a release: an Action's tag with its commit."""
    if release is None:
        return "?"
    return f"{release[0]} ({release[2][:12]})" if release[2] else release[0]


def _decided(
    dependency: Dependency, state: str, release: Release, message: str, record: dict, today: date, position: Position
) -> Verdict:
    """A blocking state, unless a decision about exactly this release covers it."""
    target = target_of(dependency, release)
    decision = covering_decision(record, dependency.key, target)
    if decision is None or decision.decision not in (DEFERRED, REJECTED):
        return Verdict(dependency, state, message, target, decision, position)
    if not decision.is_active(today):
        expired = f"deferral of {display(release)} expired {decision.until}: {decision.reason}"
        return Verdict(dependency, EXPIRED, expired, target, decision, position)
    until = f" until {decision.until}" if decision.decision == DEFERRED else ""
    covered = f"{display(release)} {decision.decision}{until} by {decision.by}: {decision.reason}"
    return Verdict(dependency, COVERED, covered, target, decision, position)


def _support(position: Position) -> str:
    if position.support_end:
        return f"line {position.line} supported until {position.support_end.isoformat()}"
    shipped = position.last_shipped()
    return f"line {position.line} last shipped {shipped.isoformat()}" if shipped else f"line {position.line}"


def judge(
    dependency: Dependency,
    answer: Answer,
    cycles: list[Cycle] | None,
    record: dict[str, list[Decision]],
    today: date,
    lifecycle_error: str | None = None,
) -> Verdict:
    """One dependency's verdict. See the module docstring for the states."""
    unjudgeable = _cannot_be_judged(dependency, answer, lifecycle_error)
    if unjudgeable is not None:
        return unjudgeable
    position = assess(dependency, answer, cycles)
    if not dependency.is_pinned:
        suggestion = position.in_line or position.newest
        hint = f"; newest pin in its line is {display(suggestion)}" if suggestion else ""
        return Verdict(dependency, UNPINNED, f"`{dependency.version}` is not a pin{hint}", position=position)
    return _within_and_beyond_the_line(dependency, position, record, today)


def with_waiting(verdict: Verdict, waiting: tuple[Waiting, ...]) -> Verdict:
    """A verdict that does not block, made WAITING when a newer release in the pin's line is held
    back by the package manager -- so it is printed until it can be taken, and then judged."""
    if verdict.blocks or verdict.state == UNREACHABLE or not waiting:
        return verdict
    pin = pinned_name(verdict.dependency)
    ahead = [
        held
        for held in waiting
        if semver_line(held.release[0]) == semver_line(pin) and version_key(held.release[0]) > version_key(pin)
    ]
    if not ahead:
        return verdict
    first = max(ahead, key=lambda held: version_key(held.release[0]))
    message = (
        f"{display(first.release)} is out, but {first.guard} installs it only from "
        f"{first.installable_from:%Y-%m-%d %H:%M} UTC; it is judged from then"
    )
    return Verdict(verdict.dependency, WAITING, message, first.release[0], verdict.decision, verdict.position)


def _cannot_be_judged(dependency: Dependency, answer: Answer, lifecycle_error: str | None) -> Verdict | None:
    """The verdicts that need no position: no data to judge against."""
    if answer.error:
        if not dependency.is_pinned:
            return Verdict(dependency, UNPINNED, f"`{dependency.version}` is not a pin")
        return Verdict(dependency, UNREACHABLE, answer.error)
    if lifecycle_error:
        return Verdict(dependency, UNREACHABLE, f"support dates unavailable: {lifecycle_error}")
    return None


def _within_and_beyond_the_line(
    dependency: Dependency, position: Position, record: dict[str, list[Decision]], today: date
) -> Verdict:
    """Decision 14, in order: an ended line, a patch in the line, then a later line."""
    newest = position.newest
    if position.support_end and position.support_end < today and newest is not None:
        message = f"{_support(position)} -- it has ended; move to {display(newest)}"
        return _decided(dependency, UNSUPPORTED, newest, message, record, today, position)
    if position.in_line is not None and not is_current(dependency, position.in_line):
        message = f"{dependency.release} -> {display(position.in_line)} (same line) has not been looked at"
        return _decided(dependency, NEWER, position.in_line, message, record, today, position)
    if position.newer_line and newest is not None:
        if major_is_due(position, today):
            message = f"{display(newest)} is a new major and {_support(position)} -- under a year left"
            return _decided(dependency, MAJOR_DUE, newest, message, record, today, position)
        message = f"new major {display(newest)} exists; {_support(position)}"
        return Verdict(dependency, MAJOR_AVAILABLE, message, target_of(dependency, newest), position=position)
    return Verdict(
        dependency, CURRENT, f"{display(position.in_line)} is the newest in line {position.line}", position=position
    )

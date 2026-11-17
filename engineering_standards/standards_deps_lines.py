#!/usr/bin/env python3
"""Where a pin stands within its release line, and against the newest line. Pure.

Decision 14 of the 2026-10-02 plan separates two questions the gate used to ask as one:

* Is there a newer release IN THE PINNED LINE? A patch -- security and bug fixes -- and the
  gate blocks until it is taken or deferred (decisions 2 and 3).
* Is there a newer LINE? A major. A notice while the pinned line has more than a year of
  support left; mandatory with a year or less; blocking outright past its end of support.

THE LINE is the lifecycle cycle where endoflife.date publishes one (standards_deps_lifecycle),
else the semantic major -- or `0.minor` below 1.0, where every minor is a breaking release.

WITHOUT A LIFECYCLE, support is read off the vendor's behaviour (Jack, 2026-10-02): a newer
major is a notice while the pinned line still ships; once it has had no release for a year while
a newer line exists, the vendor has stopped supporting it in every way that matters, and the
upgrade is mandatory. Without that, "notice" could quietly become "never".

Source of truth: engineering-standards/engineering_standards/standards_deps_lines.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from standards_deps_declared import ACTION, Dependency
from standards_deps_lifecycle import Cycle, cycle_of
from standards_deps_registry import Answer, Release, semver_line, version_key

SUPPORT_HORIZON_DAYS = 365  # decision 14: "less than one year"


@dataclass(frozen=True)
class Position:
    line: str  # how a person names the pinned line: "8.4", ".NET 10", "v7"
    in_line: Release | None  # the newest release in the pinned line
    newest: Release | None  # the newest release overall (an image's LTS where it names one)
    newer_line: bool  # whether `newest` belongs to a later line than the pin
    support_end: date | None  # the pinned line's end of support, where a lifecycle says
    has_lifecycle: bool

    def last_shipped(self) -> date | None:
        """When the pinned line last published a release, where the registry dates it."""
        return date.fromisoformat(self.in_line[1]) if self.in_line and self.in_line[1] else None


def pinned_name(dependency: Dependency) -> str:
    """The release a pin names: an Action's tag comment, else the version as written -- less any
    range operator, so an unpinned `^10.0.33` sits in line 10 and its hint names a 10.x pin
    rather than the newest release of any major."""
    if dependency.ecosystem == ACTION and dependency.label:
        return dependency.label
    return dependency.release.lstrip("^~=<> ")


def is_current(dependency: Dependency, release: Release) -> bool:
    """Whether the pin IS this release -- by commit for an Action, by version otherwise."""
    if dependency.ecosystem == ACTION:
        return release[2] == dependency.version
    return release[0] == dependency.release


def assess(dependency: Dependency, answer: Answer, cycles: list[Cycle] | None) -> Position:
    """The pin's line, the newest release in it, and the newest line -- from data already fetched."""
    name = pinned_name(dependency)
    newest = answer.newest
    cycle = cycle_of(name, cycles) if cycles else None
    if cycle is not None:
        same = [release for release in answer.releases if cycle_of(release[0], cycles) == cycle]
        newer_line = newest is not None and cycle_of(newest[0], cycles) != cycle
        label, end = cycle.name, cycle.end
    else:
        line = semver_line(name)
        same = [release for release in answer.releases if semver_line(release[0]) == line]
        newer_line = (
            newest is not None and semver_line(newest[0]) != line and version_key(newest[0]) > version_key(name)
        )
        label, end = ".".join(str(part) for part in line), None
    in_line = max(same, key=lambda release: version_key(release[0]), default=None)
    return Position(label, in_line, newest, newer_line, end, cycle is not None)


def major_is_due(position: Position, today: date) -> bool:
    """Decision 14: a newer line is mandatory once the pinned one has a year or less of support."""
    if position.support_end is not None:
        return (position.support_end - today).days <= SUPPORT_HORIZON_DAYS
    if position.has_lifecycle:
        return False  # a lifecycle line with no announced end is supported; the vendor said so
    shipped = position.last_shipped()
    return shipped is not None and (today - shipped).days >= SUPPORT_HORIZON_DAYS

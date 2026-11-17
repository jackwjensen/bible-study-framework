#!/usr/bin/env python3
"""Which of a registry's releases the repository can actually install today. Pure.

The gate blocks the deploy (it runs in CI, before the deploy job) until a newer release is
taken or deferred. That is only fair when the
release CAN be taken (Jack, 2026-11-11: "The upgrade requirement should check if there is a
VALID ACTIVE upgrade"). Two kinds of release cannot:

* A release INSIDE THE PACKAGE MANAGER'S RELEASE-AGE GUARD. pnpm refuses a version younger than
  `minimumReleaseAge` (standards_deps_pnpm), so on the day vite 8.3.8 shipped the gate demanded
  a version `pnpm add` would not install -- and the only ways out were a deferral for a release
  nobody had a reason to defer, or an exclusion that switches the supply-chain guard off. Such a
  release is WAITING: printed with the moment it becomes installable, never blocking, and judged
  normally once that moment passes.
* A WITHDRAWN release: an npm version its maintainer deprecated (`@testing-library/jest-dom`
  6.10.0, "incorrect minor release with breaking changes"). PyPI's yanked files never reach this
  far -- the registry lookup leaves them out.

The PIN ITSELF is never removed, whatever its age or state: the gate judges where the repository
stands, and a pin taken through a deliberate exclusion is still the pin.

A release with no publish instant (a cached answer from before the instant was kept, a registry
that dates releases by day only) is treated as installable: the gate then asks for it exactly as
it did before this module existed, which errs toward looking rather than skipping.

Source of truth: engineering-standards/engineering_standards/standards_deps_installable.py
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from standards_deps_declared import NPM, Dependency
from standards_deps_pnpm import ReleaseAgeGuard
from standards_deps_registry import Answer, Release


@dataclass(frozen=True)
class Waiting:
    """A newer release the package manager will not install yet."""

    release: Release
    installable_from: datetime
    guard: str  # the guard's own description, e.g. "pnpm minimumReleaseAge 1440 min (...)"


def _instant(timestamp: str) -> datetime:
    """A publish instant. Validated where the registry answer was read (`_npm`), so a malformed
    one has already been reported as a failed lookup and never reaches here."""
    return datetime.fromisoformat(timestamp)


def installable(
    dependency: Dependency, answer: Answer, guard: ReleaseAgeGuard | None, now: datetime
) -> tuple[Answer, tuple[Waiting, ...]]:
    """The answer with only the releases that can be installed at `now`, and those that wait."""
    if answer.error or not answer.releases:
        return answer, ()
    pin = dependency.release
    withdrawn = set(answer.withdrawn) - {pin}
    published = dict(answer.published_at)
    kept: list[Release] = []
    waiting: list[Waiting] = []
    for release in answer.releases:
        version = release[0]
        if version in withdrawn:
            continue
        instant = _instant(published[version]) if version in published else None
        if (
            dependency.ecosystem == NPM
            and guard is not None
            and version != pin
            and instant is not None
            and guard.guards(dependency.name, version)
        ):
            ready = instant + timedelta(minutes=guard.minutes)
            if ready > now:
                waiting.append(Waiting(release, ready, guard.source))
                continue
        kept.append(release)
    preferred = answer.preferred if any(release[0] == answer.preferred for release in kept) else None
    return replace(answer, releases=tuple(kept), preferred=preferred), tuple(waiting)

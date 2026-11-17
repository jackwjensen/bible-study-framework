#!/usr/bin/env python3
"""Is the commit gate actually wired in this clone? Asked at push, reported never enforced.

WHY THIS EXISTS. The git hooks are the layer that makes the gates fire for every client --
terminal, IDE button, GUI, coding agent -- and they are wired per CLONE, with one command
that nothing repeats and nothing verifies:

    git config core.hooksPath engineering_standards/hooks

A clone where that was never run has no gate. Not a failing gate: no gate. Every commit
passes, the developer sees exactly what a compliant developer sees, and the first anyone
hears of it is CI rejecting a branch's worth of work -- or not, if CI is informational.

THE CASE THAT PROVED IT, in the B3D pack on 2026-09-04: the repo that SHIPS the fail-closed
doctrine had `core.hooksPath` pointing at a directory that did not exist there, so the setting
looked right, `git config` echoed it back, and git silently ran nothing on every commit for
the life of a branch. Found by eye while committing, not by any check -- which is the whole
argument for this module. Copied here 2026-09-05; this pack's own hooks are wired, and the
test that says so is what keeps that from being a claim.

WHY A WARNING AND NOT A VIOLATION, decided rather than defaulted. A Violation fails the gate,
and this is a property of somebody's WORKING COPY, not of the repository's content:

  * CI has no hooks and never should -- it runs verify directly. A Violation would fail every
    CI run in every adopted repo, and the rule would be deleted the same week.
  * A contractor or a reviewer with a fresh clone must be able to commit and push. Refusing
    would make the pack's first act in a new clone a refusal.

So it prints, on stderr, and changes no exit code. It is the same instrument
`standards_pack_update` uses for "your copy is behind" and `warn_unreadable` uses for "I
could not look": the scanner reporting a limit on its own reach rather than a finding about
the code.

WHY PUSH AND NOT COMMIT. At commit stage this would be self-defeating -- if the hooks are not
wired, no commit-stage check runs at all, so the one moment it could speak is the moment it
cannot. Push is where verify is also invoked directly by CI and by a human, so the message
reaches somebody either way.

Source of truth: engineering-standards/engineering_standards/standards_hook_wiring.py
"""

from __future__ import annotations

import os
from pathlib import Path

from standards_git import _run_git
from standards_pack_identity import PACK_DIRECTORY

# Where the hooks live -- one path, in every repo including this one, because the pack and its
# consumers hold the same package directory (see standards_pack_identity). A repo carrying
# this directory has adopted the hook model, which is what makes "not wired" a finding rather
# than a preference.
HOOK_DIRECTORIES = (f"{PACK_DIRECTORY}/hooks",)

# CI sets this; GitHub Actions, GitLab and most others do. A CI checkout is SUPPOSED to have
# no hooks -- it runs verify.py directly -- so warning there would be noise that teaches
# people to ignore the warning, which is worse than not having it.
CI_ENVIRONMENT_VARIABLE = "CI"


def available_hooks(repo_root: Path) -> str | None:
    """The hooks directory this repo ships, or None if it carries none."""
    for candidate in HOOK_DIRECTORIES:
        if (repo_root / candidate).is_dir():
            return candidate
    return None


def configured_hooks_path(repo_root: Path) -> str | None:
    """`core.hooksPath` for this clone, or None when it is unset."""
    value = _run_git(repo_root, ["config", "--get", "core.hooksPath"])
    return value.strip() if value and value.strip() else None


def warning(repo_root: Path, environment: dict | None = None) -> str | None:
    """What to say about this clone's hook wiring, or None when it is fine.

    `environment` is injected rather than read from os.environ directly, so the CI branch can
    be tested without a test having to mutate the process it runs in.
    """
    env = os.environ if environment is None else environment
    if env.get(CI_ENVIRONMENT_VARIABLE):
        return None

    shipped = available_hooks(repo_root)
    if shipped is None:
        return None  # this repo does not use the hook model; nothing to be wired

    configured = configured_hooks_path(repo_root)

    if configured is None:
        return (
            "standards: the commit gate is NOT wired in this clone -- core.hooksPath is unset,\n"
            "           so `git commit` here runs no checks at all. Wire it once:\n"
            "\n"
            f"               git config core.hooksPath {shipped}\n"
            "\n"
            "           (informational -- CI still enforces the same gates on every branch push)"
        )

    if not (repo_root / configured).is_dir():
        return (
            f"standards: core.hooksPath is set to '{configured}', which does not exist here --\n"
            "           so git silently runs no hook and every commit passes unchecked. The\n"
            f"           hooks this repo ships are in '{shipped}':\n"
            "\n"
            f"               git config core.hooksPath {shipped}\n"
            "\n"
            "           (informational -- CI still enforces the same gates on every branch push)"
        )

    return None

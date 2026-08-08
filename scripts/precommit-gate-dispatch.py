#!/usr/bin/env python3
"""Run whichever repo's commit gate belongs to the commit being attempted.

WHY THIS EXISTS. The per-repo hook in `<repo>/.claude/settings.json` only loads when the
session's project directory IS that repo. A session rooted somewhere else -- working across
repos, or rooted in engineering-standards while committing to InvoTrack -- gets no gate at
all, silently. That happened: a commit landed with a `baseline-grew` violation, and nothing
objected, because the hook that would have objected was never loaded.

A gate that protects some sessions is worse than one that protects none, because it is
trusted. So this dispatcher is installed ONCE at the user level (`~/.claude/settings.json`)
and works out the target repo from the commit itself rather than from the session:

    1. take the working directory of the command being run (falling back to the process cwd),
    2. walk up to its git root,
    3. if that root has `scripts/precommit-gate.py`, run it there.

A repo with no gate script is a silent no-op, which is what keeps this safe to install
globally: repos outside the standards pack -- B3D, air2trust, anything else on the machine --
are unaffected because they have no gate to find.

The per-repo hook stays in place. Both firing is harmless (the second run finds the same
clean tree); either one alone is enough.

Source of truth: engineering-standards/scripts/precommit-gate-dispatch.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# Matches the same commit invocations the per-repo gate cares about. `git commit` anywhere in
# a compound command counts -- `cd x; git commit ...` is still a commit.
GIT_COMMIT = re.compile(r"\bgit\s+(?:-\S+\s+|--\S+(?:=\S+)?\s+)*commit\b")

# A `cd <path>` prefix tells us which repo the command means, which is not necessarily the
# process cwd -- the model routinely writes `cd <repo>` into the command itself.
#
# The unquoted alternative must stop at a shell separator. `\S+` looked right and was not:
# on `cd C:\repos\InvoTrack; git commit`, it captured the trailing `;` too, the path failed
# to resolve, and the dispatcher quietly fell back to the SESSION's repo -- which was clean,
# so it returned 0 and let the commit through. That is the exact failure this file exists to
# prevent, reproduced inside the fix for it.
LEADING_CD = re.compile(r"^\s*cd\s+(?P<path>\"[^\"]+\"|'[^']+'|[^\s;&|]+)")

GATE_RELATIVE = Path("scripts") / "precommit-gate.py"


def read_payload() -> dict | None:
    """Read the hook payload as BYTES and decode explicitly.

    Text-mode stdin decodes with the process locale (cp1252 here) and does not strip a UTF-8
    BOM, which is how the per-repo gate once failed open on every commit.
    """
    raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def command_of(payload: dict) -> str:
    tool_input = payload.get("tool_input") or {}
    return tool_input.get("command") or ""


def target_directory(command: str) -> Path:
    """Where the command means to run, which may not be the process cwd."""
    match = LEADING_CD.match(command)
    if not match:
        return Path.cwd()

    candidate = Path(match.group("path").strip("\"'"))
    return candidate if candidate.is_dir() else Path.cwd()


def git_root(start: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    return Path(root) if root else None


def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0

    command = command_of(payload)
    if not GIT_COMMIT.search(command):
        return 0

    root = git_root(target_directory(command))
    if root is None:
        return 0

    gate = root / GATE_RELATIVE
    if not gate.is_file():
        # Not a standards-pack repo. Nothing to enforce, and nothing to say about it.
        return 0

    # Hand the payload back to the repo's own gate on its own terms, from its own root, so
    # its relative paths and .standards.json resolve the way they do when it runs itself.
    completed = subprocess.run(
        [sys.executable, str(gate)],
        input=json.dumps(payload),
        text=True,
        cwd=str(root),
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())

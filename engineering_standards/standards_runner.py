#!/usr/bin/env python3
"""`runner-image`: CI runs on the Ubuntu release the server runs.

Rule here: runner-image. The number it compares against is SERVER_UBUNTU in standards_versions.py,
beside the floors, because that file is where a human looks when a version moves.

THREE QUESTIONS ABOUT A RUNNER LABEL, AND THIS IS ONLY THE THIRD (Jack, 2026-11-01):

  Is it a pin?          `dependency-unpinned`, through `is_exact` -- `ubuntu-latest` moved from
                        24.04 to 26.04 in November 2026 under every workflow that named it.
  Is its OS supported?  the dependency gate, from endoflife.date, like any other dependency.
  Is it the server's?   here. A pinned, supported `ubuntu-24.04` passes the first two and still
                        checks the code on a different OS from the one it deploys to -- a
                        different Python, OpenSSL and coreutils (26.04 ships uutils and sudo-rs)
                        -- which is the "it works in test" gap the estate's staging rule forbids.

ONE PARSE. The labels come from `runner_labels` in standards_deps_declared, the reader the other
two questions use, so the three cannot disagree about what a workflow runs on.

Only Ubuntu labels are judged: the server is Ubuntu, and a `windows-2025` job builds a desktop
release that never runs there. A workflow whose Ubuntu job deploys somewhere else -- a customer's
own host on another release -- exempts the line and names that host.

Source of truth: engineering-standards/engineering_standards/standards_runner.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from standards_core import Violation
from standards_deps_declared import runner_labels
from standards_exemptions import line_exemption_reason
from standards_versions import SERVER_UBUNTU

RUNNER_RULE = "runner-image"


def check_runner_image(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a pinned Ubuntu runner label that is not the server's release.

    A floating label is `dependency-unpinned`'s finding, not this one's: reporting `ubuntu-latest`
    twice would say the same thing in two voices.
    """
    for label in runner_labels(path, "\n".join(lines)):
        if label.name != "ubuntu" or not label.is_pinned or label.version == SERVER_UBUNTU:
            continue
        if line_exemption_reason(lines, label.line - 1, RUNNER_RULE):
            continue
        yield Violation(
            path=path,
            line=label.line,
            rule=RUNNER_RULE,
            message=(
                f"this job runs on Ubuntu {label.version}, but the server runs Ubuntu {SERVER_UBUNTU}, "
                f"so CI checks the code on a different OS from the one it deploys to. Use "
                f"`ubuntu-{SERVER_UBUNTU}`. If this job deploys to another host on another release, "
                f"exempt the line and name that host."
            ),
        )

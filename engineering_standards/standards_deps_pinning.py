#!/usr/bin/env python3
"""Every declared dependency is pinned to exactly one release.

Rule here: dependency-unpinned.

THE POLICY (Jack, 2026-10-02): pin everything, and make every new release be looked at. The
second half is the dependency gate (`deps.py check`, in CI, needing the network); this is the
first half, at COMMIT and offline, so an unpinned declaration is refused the moment it is
staged rather than discovered in CI.

ONE DEFINITION OF "PINNED", shared with the gate: the readers and `is_exact` in
standards_deps_declared. The gate and this rule ask the same question of the same parse, so they
cannot disagree about what a pin is -- a second regex here would be a second answer.

ESTATE-WIDE, NOT ONLY WHERE THE GATE IS ADOPTED. Jack, 2026-10-02: "blocking is the pack doing
its job." A repo with floating versions goes red at its next sync, and the fix is to pin them.

NEVER BASELINED, like package-wildcard: a floating version is not reproducible today, and a
baseline entry would only record that. Line-exemptable for the declaration that genuinely cannot
name one release (a vendor that publishes only a floating tag), with the reason beside it.

PACK-OWNED COPIES ARE THE PACK'S. A root file carrying the pack's source-of-truth footer is
judged in the pack, which pins it; the scanner's scope already keeps `engineering_standards/`
out of a consumer's run.

Source of truth: engineering-standards/engineering_standards/standards_deps_pinning.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from standards_core import Violation, warn_unreadable
from standards_deps_declared import PACK_FOOTER, reader_for
from standards_exemptions import line_exemption_reason
from standards_pack_identity import is_the_pack_itself

UNPINNED_RULE = "dependency-unpinned"
COMMAND = "python engineering_standards/deps.py"


def check_unpinned_dependencies(path: Path, lines: list[str], repo_root: Path) -> Iterable[Violation]:
    """A finding for every declaration in this file that names a range, a floating tag or a branch."""
    try:
        relative = path.relative_to(repo_root)
    except ValueError:
        relative = path
    pack_itself = is_the_pack_itself(repo_root)
    reader = reader_for(path.name, relative, pack_itself)
    if reader is None:
        return
    text = "\n".join(lines)
    if not pack_itself and PACK_FOOTER in text:
        return
    try:
        declared = list(reader(relative, text))
    except ValueError as error:
        warn_unreadable(relative, error, f"{UNPINNED_RULE} could not check its declarations")
        return
    for dependency in declared:
        if dependency.is_pinned or line_exemption_reason(lines, dependency.line - 1, UNPINNED_RULE):
            continue
        yield Violation(
            path=path,
            line=dependency.line,
            rule=UNPINNED_RULE,
            message=(
                f"{dependency.key} `{dependency.version}` is not a pin. Every dependency is pinned to "
                f"exactly one release and moved deliberately through the dependency gate; "
                f"`{COMMAND} investigate {dependency.key}` names the newest pin in its line."
            ),
        )

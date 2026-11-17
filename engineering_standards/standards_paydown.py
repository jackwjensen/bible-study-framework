#!/usr/bin/env python3
"""The rule that makes baselined debt actually move.

WHY THIS EXISTS. The ratchet stops debt getting WORSE: a baselined 900-line file fails at
901. It says nothing about the file staying at 900 forever, and that is exactly what
happens -- `refactoring.md` has said "a baseline is a starting point, not a resting place"
since 2026-08-21, and the strongest thing it could say after that was "when you touch a
repo with baselined debt, pay some of it down. Do not ask whether you are allowed to; you
are." That grants permission. It requires nothing.

Permission is not enough, and the reason is structural rather than moral: an optional
refactor loses every race against a deadline, so it is always the thing that gets dropped,
and a rule that costs nothing to skip is not a rule. This module is that policy with a
gate behind it.

THE RULE. If your branch changes a baselined file, that file leaves the branch smaller
than it arrived, and the baseline records the new size.

TWO CONDITIONS, because either alone leaks:

  1. Current size is below what the baseline recorded AT THE BASE REF -- proof this branch
     paid something down.
  2. The baseline in the working tree records the NEW size -- proof it was regenerated.
     Without this the recorded number never moves, so the next branch to touch the file
     clears condition 1 for free and the ratchet quietly stops ratcheting.

Comparing against the base ref rather than the working tree is what keeps the two from
deadlocking: regenerating the baseline (which condition 2 demands) would otherwise
immediately make the branch look like it had paid nothing.

WHEN IT RUNS. Push and CI, never commit. Being blocked on your first commit into a big
file would only teach people --no-verify, and the unit of work this judges is the branch.

WHAT IT DOES NOT TOUCH. A file carrying a valid file-length exemption is exempt here too.
An exemption means "looked at; nothing to fix", so demanding it shrink anyway would be the
pack arguing with a decision it already accepted -- and would push people to delete the
marker, which is the one thing that makes the decision visible.

Source of truth: engineering-standards/engineering_standards/standards_paydown.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from standards_baseline import BASELINE_FILE
from standards_core import Violation, warn_unreadable
from standards_exemptions import file_length_exemption

# THE ONE GIT RUNNER, rather than the second one that used to live here. Two of them is the
# duplication the pack's own DRY rule forbids, and the drift it produced was not cosmetic:
# this module asked for `--name-only` without `-z`, which standards_git's own docstring
# explains git answers by QUOTING and escaping any path holding a space or a non-ASCII
# character. A quoted path does not resolve, so `absolute.is_file()` was false and the file
# dropped silently out of the pay-down check -- in an estate whose filenames carry æ, ø and å.
# Found in the B3D pack on 2026-09-04, copied here 2026-09-05.
from standards_git import _paths_from_nul_list, _run_git, trunk_ref
from standards_pack_identity import PACK_DIRECTORY

PAYDOWN_RULE = "paydown-required"
STALE_RULE = "baseline-stale"
UNKNOWN_RULE = "paydown-unknown"


def changed_files(root: Path, base_ref: str) -> list[str] | None:
    """Repo-relative paths this branch changes against `base_ref`, or None if git cannot say.

    Three-dot form, so the answer is what the pull request shows -- everything since this
    branch left the base -- rather than everything that has happened on the base meanwhile.
    A branch must not be asked to pay down a file somebody else touched on master.

    `-z`, for the reason standards_git records: without it git quotes and escapes any path
    with a space or a non-ASCII character in it, and a quoted path matches nothing on disk.
    """
    output = _run_git(root, ["diff", "--name-only", "-z", f"{base_ref}...HEAD"])
    if output is None:
        return None
    return [path.relative_to(root).as_posix() for path in _paths_from_nul_list(root, output)]


def baseline_at_ref(root: Path, base_ref: str, baseline_name: str) -> dict[str, int]:
    """Recorded oversized-file sizes as of `base_ref`.

    An absent or unreadable baseline there means no recorded debt there, which is the right
    reading: a file that was not baselined at the base ref is either new or already compliant,
    and neither owes a pay-down.
    """
    output = _run_git(root, ["show", f"{base_ref}:{baseline_name}"])
    if output is None:
        return {}
    try:
        return json.loads(output).get("oversizedFiles", {})
    except json.JSONDecodeError as error:
        # An empty mapping means "this branch owes no pay-down", which is what a COMPLIANT
        # branch looks like -- so a baseline that will not parse silently excuses the branch
        # from the gate. Said out loud rather than blocking: the file is at the base ref, so
        # whoever is pushing may not have written it and cannot fix it from here.
        print(
            f"warning: the baseline at {base_ref} could not be parsed ({error}) -- "
            f"the pay-down gate has nothing to measure against and is not enforcing",
            file=sys.stderr,
        )
        return {}


def check_paydown(
    root: Path,
    base_ref: str,
    baseline_name: str,
    working_baseline: dict[str, int],
    max_file_lines: int,
) -> list[Violation]:
    """Findings for every baselined file this branch touched without paying debt down."""
    touched = changed_files(root, base_ref)
    if touched is None:
        return [
            Violation(
                path=root,
                line=1,
                rule=UNKNOWN_RULE,
                message=(
                    f"Could not diff against '{base_ref}', so the pay-down rule could not be "
                    f"checked. Fetch the base ref and retry -- a gate that cannot run is not a "
                    f"gate that passed."
                ),
            )
        ]

    base_oversized = baseline_at_ref(root, base_ref, baseline_name)
    findings: list[Violation] = []

    for relative in sorted(touched):
        absolute = root / relative
        recorded_at_base = base_oversized.get(relative)
        if recorded_at_base is None or not absolute.is_file():
            continue

        try:
            lines = absolute.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as error:
            # Every other reader in the pack names the file it could not open; this one
            # raised instead, so an unreadable file turned the push gate into a traceback
            # rather than a finding. The silent-skips sweep could not see it -- it parses
            # handlers, and a bare call has none. Skipping is the direction the rest of the
            # pack takes, and the consequence is worth stating: a file that drops out here
            # owes no pay-down, which makes the branch look MORE compliant than it is.
            warn_unreadable(absolute, error, "this file's pay-down could not be checked")
            continue

        if file_length_exemption(lines) is not None:
            continue

        line_count = len(lines)
        if line_count >= recorded_at_base:
            findings.append(
                Violation(
                    path=absolute,
                    line=1,
                    rule=PAYDOWN_RULE,
                    message=(
                        f"This branch changes a baselined file that is still {line_count} "
                        f"lines (baselined at {recorded_at_base}). A touched baselined file "
                        f"must leave the branch smaller than it arrived -- take a slice of the "
                        f"debt, then re-run --write-baseline. See "
                        f".claude/rules/refactoring.md, which is gate-scoped: read it here "
                        f"rather than assuming you have it."
                    ),
                )
            )
            continue

        recorded_now = working_baseline.get(relative)
        # Absent from the working baseline is the BEST outcome: the file came all the way
        # under the limit, so its debt is gone rather than merely reduced. Only a file that
        # is absent while STILL oversized means the baseline was never regenerated.
        is_stale = line_count > max_file_lines if recorded_now is None else recorded_now != line_count
        if is_stale:
            findings.append(
                Violation(
                    path=absolute,
                    line=1,
                    rule=STALE_RULE,
                    message=(
                        f"Paid down to {line_count} lines, but the baseline still records "
                        f"{recorded_now if recorded_now is not None else recorded_at_base}. "
                        f"Re-run --write-baseline and commit it, or the recorded debt never "
                        f"moves and the next branch to touch this file pays nothing."
                    ),
                )
            )

    return findings


def paydown_gap(root: Path) -> str | None:
    """Why the CI stage could not run the pay-down check here, or None when it ran.

    For the CI stage only (verify.py asks it there): at push an unresolvable trunk is ordinary,
    a repo before its first push. In CI it means a shallow checkout or a missing remote -- a
    setup fault that used to drop the pay-down check without a word, so a branch could reach
    master owing a pay-down. Only a repo carrying the scanner is asked; without it there is no
    pay-down to run.
    """
    if not (root / PACK_DIRECTORY / "check-source-limits.py").is_file() or trunk_ref(root) is not None:
        return None
    return (
        "the pay-down check did not run: no trunk resolves here (origin/master, origin/main or an "
        "upstream) -- a shallow checkout (`fetch-depth: 0` missing) or no remote"
    )


def has_baseline(root: Path) -> bool:
    """Whether the repo carries recorded debt -- the only case a missing pay-down can hide."""
    return (root / BASELINE_FILE).is_file()

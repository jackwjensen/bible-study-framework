#!/usr/bin/env python3
"""`pack-no-debt`: the supplier carries none of the debt it lets its consumers carry.

A CONSUMING repo is allowed a baseline. That is the whole point of the ratchet -- the pack
has to be able to land in a repo with a wall of existing violations without a wall of red,
or it never lands at all. `refactoring.md` then says pay it down as work touches the repo.

THE PACK IS NOT ONE OF THOSE REPOS. Every rule in the estate is authored in these ~100
modules, and a rule its own author does not run is a rule nobody has tested against real
code. The pack shipped exactly that until 2026-09-02: `python/ruff-standards.toml` went to
nine repositories while this repo's pyproject.toml carried a comment declining to adopt it --
"a separate decision with its own findings to work through". That is the definition of debt,
written down and left. Adopting it surfaced 134 findings, one of which (`B021`) was three
docstrings Python had been discarding at runtime for months.

So the asymmetry is deliberate and it runs one way only: consumers get the ratchet, the
supplier does not. This rule fires ONLY in the pack -- `is_the_pack_itself` -- because in a
consuming repo every one of these files is legitimate.

WHAT THIS RULE DOES NOT DO is re-check ruff's findings. Declaring ruff in pyproject.toml is
what makes `standards_gates.python_gates` build a ruff gate, so the findings already block a
push on their own. Checking them twice would be the two-layer drift this pack forbids. What
cannot enforce itself is the DECLARATION -- deleting the `[tool.ruff]` block removes the gate
and turns the push green, which is the one edit that must never look like an improvement.

Source of truth: engineering-standards/engineering_standards/standards_pack_debt.py
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterator
from pathlib import Path

from standards_baseline import BASELINE_FILE
from standards_core import Violation, warn_unreadable
from standards_pack_identity import is_the_pack_itself

RULE = "pack-no-debt"

# The shipped standard, and the pack's own copy of it. Ruff cannot read the former directly
# (it uses `[tool.ruff]` keys, which are only valid inside a pyproject.toml), so the pack
# holds a copy -- and a copy that nothing compares is a copy that drifts.
SHIPPED_RUFF_STANDARD = Path("python") / "ruff-standards.toml"
PYPROJECT = Path("pyproject.toml")

# Artefacts that exist only to carry violations forward. Each is legitimate in a consuming
# repo and is debt here.
DEBT_ARTEFACTS = (
    (BASELINE_FILE, "grandfathered source-limit violations"),
    ("Directory.Build.debt.props", "grandfathered analyzer violations"),
)


def _ruff_table(raw: dict) -> dict:
    return raw.get("tool", {}).get("ruff", {})


def check_pack_debt(repo_root: Path) -> Iterator[Violation]:
    """Every way the pack could quietly grant itself the ratchet it denies its own rules."""
    if not is_the_pack_itself(repo_root):
        return

    for name, what in DEBT_ARTEFACTS:
        if (repo_root / name).exists():
            yield Violation(
                str(name),
                1,
                RULE,
                f"'{name}' carries {what} forward. A consuming repo may hold one so the pack "
                "can land without a wall of red; the pack itself may not -- these files are "
                "where the rules are written, so a violation here is a rule its own author "
                "does not follow. Fix the code instead.",
            )

    yield from _check_tuning(repo_root)
    yield from _check_ruff_adoption(repo_root)


def _check_tuning(repo_root: Path) -> Iterator[Violation]:
    """A relaxed setting in .standards.json is debt wearing a config file's clothes."""
    config = repo_root / ".standards.json"
    if not config.is_file():
        return
    try:
        raw = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        # Returning here means "this pack declares no weakenings", which is exactly what a
        # compliant pack looks like -- so a config that will not parse silently excuses the
        # supplier from the rule it applies to everybody else.
        warn_unreadable(config, error, "pack-no-debt could not read this pack's own config")
        return
    weakened = [key for key, value in raw.items() if key.startswith("check") and value is False]
    if weakened:
        yield Violation(
            ".standards.json",
            1,
            RULE,
            f"turns off {', '.join(sorted(weakened))}. The pack does not get to switch off a "
            "rule it ships; a rule that is wrong should be changed or withdrawn, where every "
            "repo sees the change, not silenced in the one repo that authors it.",
        )


def _check_ruff_adoption(repo_root: Path) -> Iterator[Violation]:
    """The pack lints itself with the very file it publishes -- by reference, not by copy.

    This checked that a pasted copy still equalled the shipped one, which was DRY's problem
    restated rather than solved. `python/ruff-standards.toml` is now written in ruff's own
    format and `extend`ed, so there is one copy and nothing to compare; what remains checkable
    is that the pointer is still there.
    """
    pyproject = repo_root / PYPROJECT
    shipped = repo_root / SHIPPED_RUFF_STANDARD
    if not pyproject.is_file() or not shipped.is_file():
        return

    try:
        mine = _ruff_table(tomllib.loads(pyproject.read_text(encoding="utf-8")))
    except (OSError, tomllib.TOMLDecodeError) as error:
        warn_unreadable(pyproject, error, "pack-no-debt could not compare the ruff tables")
        return

    if not mine:
        yield Violation(
            str(PYPROJECT),
            1,
            RULE,
            "declares no [tool.ruff], so no ruff gate is built for this repo and the pack "
            "ships a lint standard it does not run on itself. Deleting this is the one edit "
            "that makes the push gate greener while checking less.",
        )
        return

    extended = mine.get("extend")
    if extended != SHIPPED_RUFF_STANDARD.as_posix():
        yield Violation(
            str(PYPROJECT),
            1,
            RULE,
            f"its [tool.ruff] does not extend {SHIPPED_RUFF_STANDARD.as_posix()} "
            f"(found extend={extended!r}). The pack must lint itself with the exact file it "
            "publishes; settings written out here instead would be a second copy of the "
            "standard, free to drift from the one every other repo receives.",
        )

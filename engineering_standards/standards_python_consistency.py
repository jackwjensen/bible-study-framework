#!/usr/bin/env python3
"""What is Python-specific about Python's consistency rule: PEP 440 ranges.

The comparison itself -- environments agree, constraints admit, matrices cover, one finding --
lives in standards_toolchain_consistency.py and is shared with Node, PHP and .NET. This module
holds only the two things that are genuinely Python's: how to read a PEP 440 range, and the
`Toolchain` descriptor that binds it to `python-support`'s readers.

WHY THE RANGE READER CANNOT BE SHARED. Composer and PEP 440 spell overlapping operators with
DIFFERENT meanings, which is the whole reason `admits` is per-toolchain rather than one clever
function. `~=3.11.1` in PEP 440 is `>=3.11.1, ==3.11.*` -- a minor lock. `~8.3.1` in Composer
is `>=8.3.1 <8.4.0`, and `~8.3` is `>=8.3 <9.0` -- the number of components changes which level
is locked, and it changes it differently from PEP 440's `~=`. A single shared parser would be
right for one ecosystem and quietly wrong for the other.

Source of truth: engineering-standards/engineering_standards/standards_python_consistency.py
"""

from __future__ import annotations

import re

from standards_declarations import Toolchain, Version
from standards_python_support import declarations_in

CONSISTENCY_RULE = "python-consistency"

# Upper bounds. `python-support` deliberately reads only LOWER bounds -- a floor has no opinion
# about a ceiling -- but a ceiling is exactly what breaks agreement, so this rule needs them.
#
# `~=` locks the MINOR only with three components: `~=3.11.1` means `>=3.11.1, ==3.11.*`, while
# `~=3.11` means `>=3.11, ==3.*` and leaves the minor free. Reading the two the same way would
# invent a ceiling that is not there.
TILDE_MINOR_LOCK = re.compile(r"~=\s*(\d+)\.(\d+)\.\d+")
STAR_MINOR_LOCK = re.compile(r"==\s*(\d+)\.(\d+)\.\*")
LESS_THAN = re.compile(r"<(?!=)\s*(\d+)\.(\d+)")
LESS_OR_EQUAL = re.compile(r"<=\s*(\d+)\.(\d+)")
LESS_THAN_MAJOR = re.compile(r"<(?!=)\s*(\d+)(?![\d.])")
LOWER_BOUND = re.compile(r"(?:>=|==|~=|\^|>)\s*v?(\d+)\.(\d+)")


def spell(version: Version) -> str:
    return f"{version[0]}.{version[1]}"


def constraint_admits(constraint: str, version: Version) -> bool:
    """Whether a `requires-python` range accepts `version`.

    Unparseable constraints return True. A rule that guessed would fail repos for shapes it
    merely does not understand, and this rule has enough authority without inventing verdicts --
    the floor rule reads the same text and judges its lower bound separately.
    """
    bounds = [(int(f.group(1)), int(f.group(2))) for f in LOWER_BOUND.finditer(constraint)]
    if bounds and version < min(bounds):
        return False

    for pattern in (TILDE_MINOR_LOCK, STAR_MINOR_LOCK):
        locked = pattern.search(constraint)
        if locked and version != (int(locked.group(1)), int(locked.group(2))):
            return False

    ceiling = LESS_THAN.search(constraint)
    if ceiling and version >= (int(ceiling.group(1)), int(ceiling.group(2))):
        return False

    inclusive = LESS_OR_EQUAL.search(constraint)
    if inclusive and version > (int(inclusive.group(1)), int(inclusive.group(2))):
        return False

    major = LESS_THAN_MAJOR.search(constraint)
    return not (major and version[0] >= int(major.group(1)))


PYTHON_TOOLCHAIN = Toolchain(
    name="Python",
    rule=CONSISTENCY_RULE,
    noun="interpreter",
    readers=declarations_in,
    spell=spell,
    admits=constraint_admits,
)

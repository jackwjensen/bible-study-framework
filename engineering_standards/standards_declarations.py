#!/usr/bin/env python3
"""The shapes a toolchain version rule is written in. Data only, no logic, no imports.

Added 2026-08-25, when the Python consistency rule was generalised to Node, PHP and .NET. Four
toolchains declare their version in different files with different syntax, but they declare the
SAME KIND OF THING, and the comparison -- do these agree with each other? -- is identical. This
module is the vocabulary that lets one engine ask it four times instead of four engines asking
it once each and drifting.

IT IMPORTS NOTHING ON PURPOSE. Every other module in the family imports this one; if this ever
imported back, the engine and its toolchains would be a cycle.

Source of truth: engineering-standards/engineering_standards/standards_declarations.py
"""

from __future__ import annotations

from typing import Callable, Iterable, NamedTuple, Optional

# A version, normalised to the granularity its toolchain actually compares at. Python and PHP
# compare (major, minor) -- 3.14, 8.5. Node and .NET compare the major alone -- (24,), (10,).
#
# A TUPLE, NEVER A FLOAT AND NEVER A STRING, and the reason generalises past Python: `3.9 > 3.14`
# is true for floats and true for strings, and false only for tuples. The same trap is waiting
# in `8.9 > 8.10` and in `net9.0` vs `net10.0`, so every reader normalises before comparing.
Version = tuple[int, ...]


class Declared(NamedTuple):
    """One version declaration found on one line of one file.

    `versions` EMPTY means a declaration naming nothing comparable -- `python:3`, `node:latest`,
    a `3.x` wildcard. That is a finding for the floor rule, never a pass: nothing about it can
    be shown to clear anything, and treating it as clean is how a floor gets routed around.

    `label` names the SITE for a human ("CI", "Dockerfile", "requires-python"). `constraint`
    carries the raw text when the declaration is a RANGE rather than a version -- Python's
    `requires-python`, Composer's `require.php`. A range is never required to equal anything;
    it must merely admit what runs, which is a different comparison and needs the original text.
    """

    index: int
    source: str
    versions: list[Version]
    label: str = "declaration"
    constraint: Optional[str] = None


class Toolchain(NamedTuple):
    """Everything the consistency engine needs to judge one toolchain.

    `readers` is the same function the toolchain's FLOOR rule uses, which is the point: two
    readers would let "is it above the floor?" and "do they agree?" disagree about what a file
    even says, and the second question would then quietly pass a repo the first one failed.

    `admits` is None for a toolchain with no range syntax among its declaration sites. Node and
    .NET are both None -- a `.nvmrc`, a TFM and an image tag are all exact -- and that is not a
    gap to fill later but a fact about those toolchains.
    """

    name: str
    rule: str
    noun: str
    readers: Callable[..., Iterable[Declared]]
    spell: Callable[[Version], str]
    admits: Optional[Callable[[str, Version], bool]] = None

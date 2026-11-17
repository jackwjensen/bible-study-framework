#!/usr/bin/env python3
"""The razor-var rule: `var` in a Blazor component, where IDE0008 cannot reach.

Its own module rather than a section of standards_checks.py, because it is the only rule
about a TEMPLATE language. Every other rule there reads a declaration in one language;
this one reads a file that is markup and C# at the same time, and most of its weight is
the reasoning about telling those two apart. That reasoning belongs beside the pattern.

Rules here: razor-var.

Source of truth: engineering-standards/engineering_standards/standards_razor.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import Violation, iter_code_lines

# `var` in a Blazor @code block. IDE0008 owns this for .cs, but it is off-by-default and
# so never reaches .razor's generated syntax trees -- the same gap that costs .razor
# S138/S1541/S3776. Verified on SDK 10.0.203: zero IDE0008 diagnostics inside @code.
#
# The declared NAME is captured, not just the keyword, because the baseline grandfathers a
# violation under `file::rule::quoted-member`. Quoting the literal word "var" would give
# every declaration in a file one shared key, so baselining one would silently grandfather
# every future `var` added to that file -- a hole straight through the ratchet.
#
# A .razor file is markup AND C#, and this rule reads every line of it -- so the pattern has
# to be able to tell a declaration from a page's own prose. It does that by requiring what C#
# requires: `var` is only legal where something initialises it, either an assignment
# (`var x = ...`), a foreach header (`var item in ...`), or a declaration expression
# (`out var x`, `is var x`, `case var x`). There is no such thing as a bare `var x;`.
#
# That is strictly more precise, not a loosening: every legal declaration still matches, and
# prose stops matching. It was Danish that exposed it -- "var" is the past tense of "to be",
# so a lawnote reading "hvis faktorerne var dokumenteret" was reported as a variable named
# `dokumenteret` (ligelon-compliance, 2026-08-20). English markup has the same shape wherever
# a sentence contains "var" as a word.
#
# The tuple branch captures `(` and nothing more, exactly as the previous pattern did. That
# is not cosmetic: the baseline grandfathers a finding under `file::rule::quoted-member`, so
# widening the capture to `(first, second)` would stop every already-baselined tuple
# declaration in the estate from matching its own entry and re-report it as new code.
RAZOR_VAR_DECLARATION = re.compile(
    r"(?<![A-Za-z0-9_.$])"
    r"(?P<pattern>(?:out|is|case)\s+)?"
    r"var\s+(?:"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<initialiser>\s*(?:=(?!=)|\bin\b))?"
    r"|"
    r"(?P<tuple>\()[A-Za-z_][A-Za-z0-9_]*\s*,[^)\n]*\)\s*=(?!=)"
    r")"
)

# An anonymous type has no name that could be written instead, so `var` is mandatory there
# and the rule does not apply. Matching `new {` also excludes the LINQ projection forms
# (`.Select(c => new { c.Name }).ToList()`), which is the intent -- IDE0008 makes exactly
# the same carve-out for .cs.
ANONYMOUS_TYPE_MARKER = re.compile(r"new\s*\{")


def check_razor_var(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag `var` inside a component, which IDE0008 cannot reach."""
    for line_number, line in iter_code_lines(lines, path.suffix):
        # Whole-line guard, deliberately: an anonymous type makes `var` mandatory, and
        # pairing each `var` with its own initialiser is more than a regex can do. Skipping
        # the line under-reports where a line mixes the two, which is the safe direction for
        # a rule this size -- razor-var is the largest baselined rule in the estate.
        if ANONYMOUS_TYPE_MARKER.search(line):
            continue

        for var_match in RAZOR_VAR_DECLARATION.finditer(line):
            # A tuple deconstruction is a declaration by its shape; anything else needs an
            # initialiser or a declaration expression to be one at all. Without that test the
            # word "var" in the page's own prose reads as a variable.
            is_declaration = (
                var_match.group("tuple") is not None
                or var_match.group("initialiser") is not None
                or var_match.group("pattern") is not None
            )
            if not is_declaration:
                continue

            declared = var_match.group("name") or var_match.group("tuple")

            yield Violation(
                path=path,
                line=line_number,
                rule="razor-var",
                message=(
                    f"'{declared}' is declared with var, which hides its type. "
                    f"Write the real type, so a reader can tell a Car from a List<Car> without "
                    f"navigating away. (IDE0008 enforces this in .cs but cannot see .razor.)"
                ),
            )

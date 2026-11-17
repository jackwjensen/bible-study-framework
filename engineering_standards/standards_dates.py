#!/usr/bin/env python3
"""Persisted dates: sentinels, and years outside a plausible window.

Split out of standards_checks.py on 2026-08-10, on the seam the pack already uses for
every other rule family (sentinels, query, compose, encoding). Dates are one subject
across five languages -- "persisted dates declare a sensible range" appears in every
rules file -- and they were the last family still inlined in the dispatcher.

THE TWO SHAPES, and why both are needed in every language that has them:

  * A SENTINEL is a real date standing in for "no date": `DateTime.MinValue`, `date.min`,
    `new Date(0)`. Once stored it cannot be told apart from a date somebody chose, which
    is the same defect as a zero-like identity -- see standards_sentinels.py.
  * An OUT-OF-RANGE year is the hand-built version of the same thing: `1899-12-30` (the
    Excel/Delphi epoch), `0000-00-00` (MySQL's zero date), `new DateTime(1, 1, 1)`.

MIN_PLAUSIBLE_YEAR is 1900 and the comparison is `>=`, so 1900 itself PASSES in every
language. That is deliberate and is the single most common source of "why did this not
fire?": the bound exists to catch epoch sentinels, not to enforce the tighter
business-date window the rules files describe (roughly 2000..today+1y), which only the
field's own validator and CheckConstraint can know.

A date reaches code in three forms and each needs its own detector -- a constructor
(`new DateTime(1899, ...)`, `date(1899, ...)`), a quoted ISO string ('1899-12-30'), and a
named sentinel. Languages differ only in which forms they can express, never in whether
the rule applies: the string form is checked in C#, Python, PHP and the whole JS family
precisely because a quoted date is a data shape rather than a language idiom.

Source of truth: engineering-standards/engineering_standards/standards_dates.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import MIN_PLAUSIBLE_YEAR, Violation, iter_code_lines

# `x = DateTime.MinValue` -- an assignment, not `==`, `=>`, or a `?? DateTime.MinValue`
# comparison key. Only stored sentinels are a problem; sort selectors are fine.
CSHARP_DATE_SENTINEL = re.compile(
    r"(?<![=!<>+\-*/%&|^~])=(?![=>])\s*DateTime(?:Offset)?\.(?P<name>MinValue|MaxValue)\b"
)

# `new DateTime(1, 1, 1)` and friends -- a literal before MIN_PLAUSIBLE_YEAR.
CSHARP_DATE_LITERAL = re.compile(r"new\s+DateTime(?:Offset)?\s*\(\s*(?P<year>\d{1,4})\s*,")

# A minimum/maximum date stored to mean "not set".
#
# The optional dotted qualifier is load-bearing: this used to require the receiver to be
# exactly `date` or `datetime`, so it saw `from datetime import date; x = date.min` and was
# blind to `import datetime; x = datetime.date.min` and to `import datetime as dt; x =
# dt.min` -- and the second spelling is at least as common as the first.
PYTHON_DATE_SENTINEL = re.compile(r"(?<![=!<>])=(?!=)\s*(?:[A-Za-z_][\w.]*\.)?(?:date|datetime)\.(?P<name>min|max)\b")

# `date(1899, 12, 30)` -- the constructor form of an implausible year, which the string
# rule cannot see because there are no quotes. The C# side has had this since the
# beginning as CSHARP_DATE_LITERAL; Python had only the `.min`/`.max` sentinel, so a
# hand-built epoch passed. The optional qualifier covers `datetime.date(...)`.
PYTHON_DATE_LITERAL = re.compile(r"\b(?:[A-Za-z_][\w.]*\.)?(?:date|datetime)\s*\(\s*(?P<year>\d{1,4})\s*,")

# A quoted ISO date whose year is implausible: '0000-00-00', "1899-12-30". Checked in every
# language -- a date carried as a string is a data shape, not a language idiom.
DATE_STRING_LITERAL = re.compile(r"['\"](?P<year>\d{4})-\d{2}-\d{2}")

# `new Date(0)` -- the Unix epoch pressed into service as "not set".
JS_DATE_EPOCH = re.compile(r"new\s+Date\s*\(\s*0\s*\)")


def _out_of_range(path: Path, line_number: int, year: str, subject: str) -> Violation:
    """The one message, so the five call sites cannot drift apart in wording."""
    return Violation(
        path=path,
        line=line_number,
        rule="date-out-of-range",
        message=(
            f"{subject} '{year}' is before {MIN_PLAUSIBLE_YEAR}. If the field really stores "
            f"historic dates, say so where it is declared; otherwise this is a sentinel or "
            f"a typo."
        ),
    )


def check_date_string_literals(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a quoted ISO date whose year predates MIN_PLAUSIBLE_YEAR.

    This is how the bad rows actually looked in InvoTrack: a '0000-00-00' in an import
    script, which aborts the statement the moment it reaches a typed intermediate.
    """
    # An import script's bad rows arrive as a LIST of literals on one line, which is exactly
    # where search stopped after the first one.
    for line_number, line in iter_code_lines(lines, path.suffix):
        for literal_match in DATE_STRING_LITERAL.finditer(line):
            if int(literal_match.group("year")) >= MIN_PLAUSIBLE_YEAR:
                continue
            yield _out_of_range(path, line_number, literal_match.group("year"), "Date literal year")


def check_csharp_dates(path: Path, lines: list[str]) -> Iterable[Violation]:
    # finditer for the same reason as the member rules: `From = DateTime.MinValue; To =
    # DateTime.MaxValue;` on one line is two sentinels, and search reported one.
    for line_number, line in iter_code_lines(lines, path.suffix):
        for sentinel_match in CSHARP_DATE_SENTINEL.finditer(line):
            yield Violation(
                path=path,
                line=line_number,
                rule="date-sentinel",
                message=(
                    f"'DateTime.{sentinel_match.group('name')}' assigned as a stored value. "
                    f"Use null for 'not set' -- a sentinel date is indistinguishable from a "
                    f"real one once persisted."
                ),
            )

        for literal_match in CSHARP_DATE_LITERAL.finditer(line):
            if int(literal_match.group("year")) >= MIN_PLAUSIBLE_YEAR:
                continue
            yield _out_of_range(path, line_number, literal_match.group("year"), "Year")


def check_python_dates(path: Path, lines: list[str]) -> Iterable[Violation]:
    """`date.min` as a stored value, and a constructor with an implausible year."""
    for line_number, line in iter_code_lines(lines, path.suffix):
        for sentinel_match in PYTHON_DATE_SENTINEL.finditer(line):
            yield Violation(
                path=path,
                line=line_number,
                rule="date-sentinel",
                message=(
                    f"A minimum/maximum date ('{sentinel_match.group('name')}') is assigned as "
                    f"a stored value. Use None for 'not set'."
                ),
            )

        for literal_match in PYTHON_DATE_LITERAL.finditer(line):
            if int(literal_match.group("year")) >= MIN_PLAUSIBLE_YEAR:
                continue
            yield _out_of_range(path, line_number, literal_match.group("year"), "Year")


def check_js_dates(path: Path, lines: list[str]) -> Iterable[Violation]:
    for line_number, line in iter_code_lines(lines, path.suffix):
        if JS_DATE_EPOCH.search(line):
            yield Violation(
                path=path,
                line=line_number,
                rule="date-sentinel",
                message=(
                    "'new Date(0)' is the epoch used as 'not set'. Use null -- 1970-01-01 is "
                    "a real date and cannot be told apart from a chosen one once stored."
                ),
            )

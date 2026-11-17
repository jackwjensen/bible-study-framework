#!/usr/bin/env python3
"""A persistence write that can be refused answers whether it saved; no caller discards it.

    write-result-discarded   a declared must-read write called as a bare statement

standards: write-result-discarded exempt -- this module NAMES the discarded-call shape in
order to detect it; every call quoted below is the subject of a regex, never a write.

THE INCIDENT (Payvisia, 2026-09-28). `SessionState.Write(Action<CompanyDataset>)` caught the
store's refusals -- a concurrency conflict, a text too long for its column -- parked them for
the page to toast, and returned `void`. A sweep of its 57 call sites found about thirty
handlers that, once `Write` returned, toasted success, cleared the form, closed an editor,
navigated away, returned "no problem", or read a count/flag/id assigned inside the change
delegate -- all over a write the store had refused. The user saw a success toast beside a
refusal toast, with the typed text already gone. The fix was two halves: `Write` returns
`bool`, and a source scan fails on any call that throws the answer away. This module is that
scan, generalised.

WHY A DECLARATION. Which call returns a must-read result is a fact about ONE repo's
persistence layer -- `Session.Write` there, `$repo->save` or `store.commit` elsewhere -- and
nothing in a call site says so. A scanner that guessed ("every `.Save(` is a write") would
report every fire-and-forget ORM save in the estate, most of which raise rather than return.
So the repo names its own entry points in `.standards.json`:

    "mustReadResults": ["Session.Write", "store.commit"]

and the rule is silent in a repo that declares nothing, like `userDocs`. An entry is dotted:
the last segment is the method, matched exactly; the segments before it name the receiver,
matched as the END of the receiver's name with its first letter in either case -- so
`Session.Write` reaches `Session.Write(`, `session.Write(`, `_session.Write(`,
`this.Session.Write(`, `$this->session->Write(` and `CurrentSession.Write(`. That is the
reach of Payvisia's own regex, which had already met every spelling a real codebase uses. An
entry with no dot is a bare function or method name with any receiver or none.

WHAT COUNTS AS DISCARDED: a code line that BEGINS with the call (after an optional `await`),
where the previous code line does not leave an expression open. That is a statement nobody
reads. Every shape that reads the answer puts something in front of the call -- `if (!`,
`bool saved =`, `return`, `Assert.True(` -- and so never matches.

  * `_ =` IS THE EXPLICIT DISCARD, and it passes by the same token: the line begins with `_`,
    not with the call. It says "nothing after this depends on it", in a form a reviewer
    sees. A bare call is how every one of Payvisia's thirty was written. (JavaScript's
    `void`, PHP's `$_ =` and Python's `_ =` pass for the same reason.)
  * A PREVIOUS LINE ENDING IN `=>` makes the call an expression body that RETURNS the value,
    which is Payvisia's own exemption. So do `(`, `,`, `=`, `&&`, `||`, `?`, `return` and
    the other continuations in `_CONTINUATIONS`: the call is an argument or an operand, not a
    statement. `:` is deliberately absent -- Python's `if saved:` ends a header, and the line
    after it IS a statement.

WHAT IT CANNOT SEE, stated so nobody reads a clean run as more than it is:

  * A lambda written on ONE line -- `@onclick="() => Session.Write(x)"` -- converted to a
    `void` delegate discards the result, and the line begins with something else. The type
    decides whether `=>` returns or discards, and a line scanner has no types. Payvisia's
    test has the same blind spot. In C#, CA1806 with `additional_use_results_methods` is the
    type-aware complement -- see docs/data-integrity.md.
  * A value assigned INSIDE the change delegate and read after a refused write. The call site
    reads the answer, then reads the captured variable regardless -- which is a review
    question, and the rules file names it.

LINE-SCOPED, like the concurrency rules: one handler legitimately discards a write whose
outcome truly changes nothing afterwards, beside another that must not. The file-scoped
marker exists only for a file whose subject IS the shape -- this detector and its fixtures.

Source of truth: engineering-standards/engineering_standards/standards_write_results.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import Violation, iter_code_lines
from standards_exemptions import exemption_reason, line_exemption_reason
from standards_scope import SOURCE_SUFFIXES

RULE = "write-result-discarded"

# How a member is reached, across the languages in scope: `.` (C#, Python, JS/TS), `?.` (null-
# conditional), `->` (PHP), `::` (PHP static, C++-style).
_SEPARATOR = r"(?:\?\.|\.|->|::)"

# Anything that can stand in front of the declared receiver on the same identifier chain:
# `this.`, `$this->`, `self.`, `_`, `Current`. Deliberately permissive -- the method name and
# the receiver's own suffix are what make a match specific.
_CHAIN_PREFIX = r"[\w$.>:?-]*?"

# A previous line ending in one of these leaves an expression open, so the call on the next
# line is an operand or an argument and its value IS read. See the module docstring for why
# `:` is not here. `+` and `!` are not here either: `count++` ends a statement in a language
# without semicolons, and treating it as open would hide the bare call on the next line.
_CONTINUATIONS = (
    "=>",
    "(",
    "[",
    ",",
    "=",
    "&&",
    "||",
    "??",
    "?",
    "\\",
    " and",
    " or",
    " not",
    "return",
    "await",
)

_LEAD = "this call to `{call}` is a bare statement, so whether it saved is thrown away"
_TAIL = (
    ". It is declared in `.standards.json` `mustReadResults` because the store can refuse it -- a "
    "conflict, a value too long for its column -- and a caller that goes on to confirm, clear "
    "the form, close the editor or navigate away does so over a write that did not happen. "
    "Branch on the answer (`if (!{call}(...)) return;`), or, when nothing afterwards depends on "
    "it, discard it visibly with `_ = {call}(...)`. The judgment half is "
    "`.claude/rules/data-integrity.md` and `ui-standards.md`."
)


def call_pattern(entry: str) -> re.Pattern[str]:
    """The regex for a declared entry called as the first thing on a line.

    `Session.Write` -> the receiver `Session` (first letter either case, any prefix on the
    identifier chain) then a separator then `Write(`. An entry with no dot matches the name
    after any receiver or none.
    """
    *receiver, method = entry.split(".")
    if receiver:
        first, *rest = receiver
        head = f"[{re.escape(first[0].lower())}{re.escape(first[0].upper())}]{re.escape(first[1:])}"
        chain = _SEPARATOR.join([head, *(re.escape(part) for part in rest)])
        target = f"{_CHAIN_PREFIX}{chain}{_SEPARATOR}"
    else:
        target = rf"(?:{_CHAIN_PREFIX}{_SEPARATOR})?"
    return re.compile(rf"^\s*(?:await\s+)?{target}{re.escape(method)}\s*\(")


def _leaves_expression_open(previous: str) -> bool:
    """Whether the previous code line ends mid-expression, making the next line an operand."""
    stripped = previous.rstrip()
    return stripped.endswith(_CONTINUATIONS)


def check_write_results(path: Path, lines: list[str], declared: tuple[str, ...]) -> Iterable[Violation]:
    """One finding per bare-statement call to a declared must-read write in this file."""
    if not declared or path.suffix not in SOURCE_SUFFIXES:
        return
    if exemption_reason(lines, RULE) is not None:
        return

    patterns = [(entry, call_pattern(entry)) for entry in declared]
    previous = ""

    for line_number, line in iter_code_lines(lines, path.suffix):
        if not line.strip():
            continue
        opened = _leaves_expression_open(previous)
        previous = line
        if opened:
            continue

        for entry, pattern in patterns:
            if not pattern.search(line):
                continue
            if line_exemption_reason(lines, line_number - 1, RULE) is not None:
                break
            yield Violation(
                path=path,
                line=line_number,
                rule=RULE,
                message=_LEAD.format(call=entry) + _TAIL.format(call=entry),
            )
            break

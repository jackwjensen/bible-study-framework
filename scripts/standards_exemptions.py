#!/usr/bin/env python3
"""How a file declares that a smoke-alarm rule does not apply to it.

The size rules and the query-shape rule flag a SIGNAL, not a defect -- most of the time the
signal is right, and sometimes the file is long, or the query broad, for a reason the
suggested fix cannot improve. This module is the one mechanism for saying so: a tagged
marker in the file header, carrying a reason long enough to be an argument.

Split out of standards_core when query-shape became the second rule to use it. One marker
syntax, one parser, one reason floor, for every rule that needs an escape hatch -- adding a
third rule means passing a new tag, never writing a second parser.

Imports nothing from standards_core on purpose: core calls INTO this (check_file_length uses
file_length_exemption), so a dependency the other way would be a cycle.

Source of truth: engineering-standards/scripts/standards_exemptions.py
"""

from __future__ import annotations

import re
from typing import Optional


# Parameterised by rule tag so every smoke-alarm rule shares one marker syntax, one
# parser, and one reason floor. `file-length` was the first; `query-shape` the second.
# Adding a third means passing a new tag, never writing a second parser.
EXEMPTION_MARKER = re.compile(
    r"standards:\s*(?P<tag>[a-z][a-z0-9-]*)\s+exempt\s*[-–—:]{1,2}\s*(?P<reason>.*)",
    re.IGNORECASE,
)

# Openers and closers that end the reason: the marker's prose stops at the end of its
# comment block, never at the first thing that happens to follow it.
REASON_TERMINATORS = ('"""', "'''", "*/", "-->", "*@")

# Comment syntax stripped from a continuation line before it is appended to the reason.
REASON_LINE_PREFIXES = ("#", "//", "*", "--")

# Only the header is searched. A marker buried at line 900 would be invisible to anyone
# opening the file, which defeats the point of putting it in the file at all.
HEADER_SCAN_LINES = 40

# Shorter than this is not an argument. Tuned so "declarative schema" alone fails and
# "declarative ORM schema; length tracks table count, not responsibility" passes.
MIN_EXEMPTION_REASON_LENGTH = 30

# How far above a flagged line a line-scoped marker may start. Only ever crossed by
# CONTIGUOUS comment lines, so this bounds a comment block, not a search radius.
MAX_LINE_MARKER_LOOKBEHIND = 10


def _is_comment(line: str) -> bool:
    """True when the line is entirely a comment, in any of the languages scanned."""
    stripped = line.strip()
    return bool(stripped) and stripped.startswith(REASON_LINE_PREFIXES)


def file_length_exemption(lines: list[str]) -> Optional[str]:
    """The stated reason this file is allowed to exceed the length limit, or None.

    Thin wrapper over `exemption_reason`; see that function for how the marker works.

    The size limit is a SMOKE ALARM, not a spec: it exists to find files that have
    accumulated a second responsibility. Some files are long for a reason that
    refactoring cannot improve -- a declarative schema whose length tracks the number of
    entities, a lookup table, a translation map, a parser whose grammar is one thing.
    Splitting those scatters one subject across two files that must always be edited
    together, which refactoring.md is explicit is WORSE than the long file.

    This is deliberately NOT a baseline. A baseline is silent debt that someone intends
    to pay down; this is a decision, and it differs in three ways that matter:

      * it lives IN the file, so it is read by anyone opening it and cannot drift from
        the code the way a separate ledger does;
      * it REQUIRES a reason, long enough to be an argument rather than a shrug;
      * the scanner counts it and prints the total, so an exemption is never invisible.

    Write it anywhere in the file's first HEADER_SCAN_LINES lines -- the module
    docstring or the top comment block:

        standards: file-length exempt -- declarative ORM schema; length tracks the
        number of tables, not accumulated responsibility.

    If you cannot write a reason you would defend in review, that is the rule working.
    Split the file.
    """
    return exemption_reason(lines, "file-length")


def line_exemption_reason(lines: list[str], index: int, tag: str) -> Optional[str]:
    """The stated reason THIS LINE is exempt from the `tag` rule, or None.

    Same marker syntax and same reason floor as the file-scoped form, scoped to one line:
    written as a trailing comment on the flagged line, or on the line immediately above it.

        senderUserId: channel.senderUserId ?? '',  // standards: unset-not-zero exempt --
        // form state bound to a text input, which has no absent value in HTML.

    WHY A SECOND SCOPE EXISTS. The file-scoped marker suits a rule whose subject IS the file
    -- length, or a query that is the file's whole point. It is the wrong instrument for a
    rule that fires per expression, because one file routinely holds both a genuine defect
    and a legitimate exception, and a header marker would silently cover both. That is not
    hypothetical: allegro-it-services' `settings.component.tsx` reaches a real
    `tenantId ?? ''` at line 28 and a correct form-state blank at line 64. A file-scoped
    exemption there would have hidden the defect while excusing the blank.

    The 40-line header rule does not apply -- a line marker is by definition read together
    with the code it excuses, which is the property the header limit exists to guarantee.
    """
    if not 0 <= index < len(lines):
        return None

    # The flagged line itself (a trailing marker), then up through the comment block
    # attached to it. Walking the whole block rather than one line matters because the
    # reason floor actively pushes a reason onto a second line, and it is the marker's
    # FIRST line that carries the tag -- checking only `index - 1` would find the
    # continuation and miss the marker. A code line ends the block, so a marker separated
    # from the flagged line by real code never carries.
    starts = [index]
    probe = index - 1
    while probe >= 0 and index - probe <= MAX_LINE_MARKER_LOOKBEHIND and _is_comment(lines[probe]):
        starts.append(probe)
        probe -= 1

    wanted = tag.casefold()
    for start in starts:
        match = EXEMPTION_MARKER.search(lines[start])
        if not match or match.group("tag").casefold() != wanted:
            continue
        index = start
        parts = [match.group("reason")]
        # The reason routinely wraps onto the following comment lines, exactly as it does
        # in a header block -- measure the whole argument, not its first line.
        for follower in lines[index + 1:]:
            stripped = follower.strip()
            if not stripped or any(end in stripped for end in REASON_TERMINATORS):
                break
            for prefix in REASON_LINE_PREFIXES:
                if stripped.startswith(prefix):
                    stripped = stripped[len(prefix):].strip()
                    break
            else:
                break  # a code line: the comment block ended
            parts.append(stripped)
        reason = " ".join(" ".join(parts).split())
        if len(reason) >= MIN_EXEMPTION_REASON_LENGTH:
            return reason
    return None


def exemption_reason(lines: list[str], tag: str) -> Optional[str]:
    """The stated reason this file is exempt from the `tag` rule, or None.

    The smoke-alarm rules -- file length, query shape -- flag a SIGNAL, not a defect.
    Most of the time the signal is right and the code should change. Sometimes the file
    is long, or the query is broad, for a reason the suggested fix cannot improve: a
    declarative schema whose length tracks the number of entities, a one-off report
    nothing else consumes. Forcing the "fix" there makes the code worse.

    This is deliberately NOT a baseline. A baseline is silent debt that someone intends
    to pay down; this is a decision, and it differs in three ways that matter:

      * it lives IN the file, so it is read by anyone opening it and cannot drift from
        the code the way a separate ledger does;
      * it REQUIRES a reason, long enough to be an argument rather than a shrug;
      * the scanner counts it and prints the total, so an exemption is never invisible.

    Write it anywhere in the file's first HEADER_SCAN_LINES lines -- the module
    docstring or the top comment block:

        standards: file-length exempt -- declarative ORM schema; length tracks the
        number of tables, not accumulated responsibility.

        standards: query-shape exempt -- one-off migration report, run once and never
        consumed elsewhere; a view would outlive its only caller.

    If you cannot write a reason you would defend in review, that is the rule working.
    """
    header = lines[:HEADER_SCAN_LINES]
    wanted = tag.casefold()

    def marker_for_tag(line: str) -> Optional[re.Match[str]]:
        match = EXEMPTION_MARKER.search(line)
        return match if match and match.group("tag").casefold() == wanted else None

    marker_index = next(
        (index for index, line in enumerate(header) if marker_for_tag(line)),
        None,
    )
    if marker_index is None:
        return None

    match = marker_for_tag(header[marker_index])
    parts = [match.group("reason")] if match else []

    # A reason routinely wraps. Keep taking lines until the comment block ends, so the
    # length floor measures the whole argument rather than only its first line.
    for line in header[marker_index + 1:]:
        stripped = line.strip()
        if not stripped or any(end in stripped for end in REASON_TERMINATORS):
            break
        for prefix in REASON_LINE_PREFIXES:
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):].strip()
                break
        parts.append(stripped)

    reason = " ".join(" ".join(parts).split())
    return reason if len(reason) >= MIN_EXEMPTION_REASON_LENGTH else None

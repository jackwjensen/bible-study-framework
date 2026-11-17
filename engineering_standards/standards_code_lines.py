#!/usr/bin/env python3
"""Which lines of a file are CODE, and which are prose the scanner must not read.

THE MOST LOAD-BEARING PRIMITIVE IN THE PACK. Nearly every per-file rule walks
`iter_code_lines`, so a line it declines to yield is a line NO rule can see -- and it fails
silently by construction: the rule reports nothing for the region and the run prints `clean`.
That is the pack's own worst failure class, and it was living here.

WHY IT IS ITS OWN MODULE. It was ~30 lines inside `standards_core.py`, the shared vocabulary.
Fixing it correctly meant a real scanner -- block regions, string prefixes, short strings,
line comments -- and that took core past the 500-line limit it ships to everyone else. The
pack's instruction for that moment is explicit: give the new concern its own module, never
trim a comment to fit. "Is this line code" is a concern with its own rules, its own suite
(`test_standards_code_lines.py`) and its own reason to change.

`standards_core` re-exports the names, for the reason its own docstring already gives about
`CheckConfig`: the split is an internal seam, not an interface change, and moving eighteen
importers would turn a length fix into a rename with nothing to gain.

Source of truth: engineering-standards/engineering_standards/standards_code_lines.py
"""

from __future__ import annotations

from typing import Iterable

# Openers whose comment runs to the end of the line, in every language the walk reads.
#
# `*` USED TO BE HERE AND MUST NOT COME BACK. It was included so a ` * more prose` line inside
# a `/* */` block would be skipped -- but those lines are inside a region the walk already
# tracks, so it bought nothing, while in Python it made every `*unpacked_argument` at the
# start of a line read as a comment. `check-source-limits.py` lost six lines of its own
# violation assembly to it, which no rule could then see.
LINE_COMMENT_PREFIXES = ("//", "#")

# Multi-line comment and docstring regions, per language, as (opener, closer) pairs.
# Prose inside them routinely contains code-shaped illustrations -- the docstrings in
# standards_checks.py spell out `date.min` and a falsy-coalesce example -- so the region
# must be skipped wholesale, not line by line.
BLOCK_COMMENT_DELIMITERS = {
    ".py": (('"""', '"""'), ("'''", "'''")),
    ".cs": (("/*", "*/"),),
    ".razor": (("/*", "*/"), ("@*", "*@"), ("<!--", "-->")),
    ".ts": (("/*", "*/"),),
    ".tsx": (("/*", "*/"),),
    ".js": (("/*", "*/"),),
    ".jsx": (("/*", "*/"),),
    ".mjs": (("/*", "*/"),),
    ".cjs": (("/*", "*/"),),
    ".php": (("/*", "*/"),),
}


# Characters that may sit between an identifier boundary and a quote as a STRING PREFIX --
# `r`, `f`, `b`, `u` and their pairs. They are stepped over so `r"""(?x)` is recognised as
# opening a region; without that the `r` hid the opener, which is the defect below.
STRING_PREFIX_CHARACTERS = "rRbBuUfF"

# Quotes that open a short (non-triple) string. Scanned past rather than into, because a
# BLOCK DELIMITER INSIDE A STRING IS NOT A DELIMITER -- and this pack contains the proof:
# `standards_exemptions.REASON_TERMINATORS = ('\"\"\"', "'''", "*/", "-->", "*@")` is a tuple of
# the delimiters themselves, and reading its first element as a docstring opener blinded every
# rule for the remaining 400 lines of that module.
SHORT_STRING_QUOTES = "\"'"


def _delimiter_at(line: str, position: int, delimiters: tuple[tuple[str, str], ...]) -> tuple[str, str, int]:
    """The block delimiter starting at `position`, as (opener, closer, resume), else ("", "", 0).

    `resume` is where scanning continues past the opener, which is not `position + len(opener)`
    when a string prefix is glued to the front of it.
    """
    start = position
    while start < len(line) and line[start] in STRING_PREFIX_CHARACTERS:
        start += 1

    for opener, closer in delimiters:
        if line.startswith(opener, start):
            return opener, closer, start + len(opener)
    return "", "", 0


def _end_of_short_string(line: str, position: int) -> int:
    """Where the short string opening at `position` ends, or the end of the line."""
    quote = line[position]
    index = position + 1
    while index < len(line):
        if line[index] == "\\":
            index += 2
            continue
        if line[index] == quote:
            return index + 1
        index += 1
    return len(line)


def _code_outside_prose(line: str, awaiting_closer: str, delimiters: tuple[tuple[str, str], ...]) -> tuple[str, str]:
    """The part of `line` that is neither comment nor string, and the region open at its end.

    SCANNED LEFT TO RIGHT RATHER THAN TESTED AT THE START, and that distinction is the whole
    defect this replaced. The old walk asked whether a line's stripped form STARTED WITH a
    delimiter, which cannot see `r\"\"\"(?x)` -- a prefixed string, and the shape every regex
    constant in this pack is written in. The opener was therefore invisible while its CLOSER,
    a bare `\"\"\"` on its own line, matched the test perfectly and was read as an opener.
    Parity inverted at that line and stayed inverted for the rest of the file.

    That is not a cosmetic mis-parse. Measured on 2026-09-09: `standards_client_address.py`
    skipped `def _appears_near(...)` at line 201 as prose while yielding its docstring as
    code, then swallowed everything after line 253 of 303; across the pack, 382 lines of real
    code were invisible to every rule that walks this function -- which is nearly all of them.
    A rule reports nothing for a region it cannot see and the run prints `clean`, so this was
    the pack's own worst failure class, sitting inside the pack's most-used primitive.

    THE ORDER OF THE THREE TESTS IS LOAD-BEARING. A block delimiter is looked for first, so
    `'''` is not read as an empty short string. A line comment next, so a `\"\"\"` written inside
    one cannot open a region -- the same swallowing by another door, and the reason the old
    walk skipped whole-line comments before looking for delimiters at all. A short string
    last, and scanning past it is what keeps `https://x` from reading as a comment and
    `REASON_TERMINATORS` from reading as a docstring.

    A region opened mid-line is equally visible now, so `SPLITS = re.compile(r\"\"\"...` yields
    the declaration -- a constant is a constant whichever quotes hold it, and the rules that
    read declarations were never meant to lose one to its punctuation.
    """
    code: list[str] = []
    position = 0

    while position < len(line):
        if awaiting_closer:
            found = line.find(awaiting_closer, position)
            if found == -1:
                break
            position = found + len(awaiting_closer)
            awaiting_closer = ""
            continue

        opener, closer, resume = _delimiter_at(line, position, delimiters)
        if opener:
            position, awaiting_closer = resume, closer
            continue

        if line.startswith(LINE_COMMENT_PREFIXES, position):
            break

        if line[position] in SHORT_STRING_QUOTES:
            # KEPT, not dropped. A short string is scanned past so the delimiters and comment
            # openers inside it are not misread -- but its TEXT is content, and a line whose
            # only content is a string is still a line of code. Dropping it took 636 lines of
            # the pack out of every rule's view on the first attempt: the continuation halves
            # of multi-line messages and, more to the point, of multi-line SQL.
            end = _end_of_short_string(line, position)
            code.append(line[position:end])
            position = end
            continue

        code.append(line[position])
        position += 1

    return "".join(code), awaiting_closer


def iter_code_lines(lines: list[str], suffix: str) -> Iterable[tuple[int, str]]:
    """Yield (line_number, line) for lines that are actually code.

    Comments and docstrings are skipped, including multi-line regions. Without this the
    scanner reports its own documentation: prose explaining a rule necessarily contains an
    example of the thing the rule forbids.

    THE WHOLE LINE IS YIELDED, not the code part of it. Rules match against source text and
    have always been handed it intact; `_code_outside_prose` decides IF a line is code, never
    what a rule gets to read. What it costs is that a rule still sees the opening fragment of
    a pattern on a declaration line, which is how it has always been for a single-line one.

    A BLANK LINE OUTSIDE A REGION IS STILL YIELDED, which reads like an oversight and is not.
    Four rules materialise this sequence and reason about ADJACENCY in it -- concurrency,
    cors, tls and sentinels -- so whether blank lines occupy a slot changes how far their
    proximity windows reach. Dropping them would have moved two of those rules in OPPOSITE
    directions at once: `standards_tls` looks ahead for the verdict of a wrapped assignment,
    where reaching further finds MORE, while `standards_sentinels` looks both ways for a
    guard, where reaching further suppresses more. Neither shift is part of fixing a
    blindness defect, and a shipped primitive is the wrong place to make one by accident.
    Changing it is a decision about those four rules, to be taken with their tests in hand.
    """
    delimiters = BLOCK_COMMENT_DELIMITERS.get(suffix, ())
    awaiting_closer = ""

    for line_number, line in enumerate(lines, start=1):
        opened_inside = bool(awaiting_closer)
        code, awaiting_closer = _code_outside_prose(line, awaiting_closer, delimiters)
        if code.strip() or (not opened_inside and not line.strip()):
            yield line_number, line

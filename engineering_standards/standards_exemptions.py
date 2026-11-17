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

Source of truth: engineering-standards/engineering_standards/standards_exemptions.py
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
# "<!--" is here for markdown: a line-scoped marker in a docs page is written as an HTML
# comment on the line above the flagged one, and without this the lookbehind in
# `line_exemption_reason` would not recognise that line as a comment at all -- the marker
# would be silently inert, which is the loud-vs-quiet direction this module always avoids.
REASON_LINE_PREFIXES = ("#", "//", "*", "--", "<!--", "@*")

# Comment openers with no closer, so the comment ends at the end of its line. A marker
# introduced by one of these is delimited by the first code line, not by a terminator --
# see the delimiter choice in `exemption_reason`. `*` is absent deliberately: it opens
# nothing, it continues a `/* */` block, and that block's terminator does the work.
LINE_COMMENT_OPENERS = ("//", "#", "--")

# Openers that begin a comment running to a matching terminator on a later line. Needed
# only to tell them apart from the line openers above, which `<!--` in particular is easy
# to confuse with (`--` is a substring of it, so position, not membership, decides).
BLOCK_COMMENT_OPENERS = ('"""', "'''", "/*", "<!--", "@*")

# Which closer ends which opener. The two tuples above answer "is this a block comment?";
# this answers "which block", which the header walk needs so a `/*` is not closed by a `"""`
# further down the file.
BLOCK_COMMENT_PAIRS = {'"""': '"""', "'''": "'''", "/*": "*/", "<!--": "-->", "@*": "*@"}

# Statements a file may OPEN with that do not end its documentation region. In three of the
# five languages here the header block does not start on line 1: PHP opens with `<?php` and
# usually `declare(strict_types=1)`, a Razor page with its directives, a rules page with YAML
# frontmatter (handled separately, since its delimiter is not a comment). Treating any of
# those as the end of the header would make a file-scoped marker impossible to write in
# exactly the files most likely to need one.
HEADER_PREAMBLE = re.compile(
    r"""(?xi)
    ^(?:
        <\?php\b
      | declare \s* \(
      | (?: from \s+ \S+ \s+ )? import \b
      | (?: global \s+ )? using \b
      | namespace \b
      | package \b
      | ['"] use \s+ strict ['"]
      | @ (?: page | using | model | inherits | inject | namespace
            | attribute | implements | layout | rendermode ) \b
    )"""
)

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


def file_header_end(lines: list[str]) -> int:
    """How many leading lines are the file's DOCUMENTATION region -- its docstring or top
    comment block, plus any language preamble in front of it.

    WHY THIS EXISTS, and it is the difference between a rule working and a rule being
    silently switched off. `exemption_reason` used to search the first HEADER_SCAN_LINES
    lines flat, with no regard for whether code had started. A LINE-scoped marker written
    inside that window -- `standards: client-address exempt` on the peer read inside
    `client_ip()`, which sits at line ~30 of every `lib/request.php` in this estate -- was
    therefore ALSO a valid file-scoped marker, and silenced the rule for the whole file.
    Nothing said so. The one line the author meant to excuse became every line, including
    the careless reads that joined it later, which is precisely the outcome the line scope
    was introduced to prevent and which `line_exemption_reason` promises in writing.

    Six rules take a marker at both scopes -- `client-address`, `cors`, `tls`,
    `sql-injection` and concurrency's two -- so this was not one rule's bug.

    The region ends at the first line that is not blank, not a comment, not inside a block
    comment or docstring, and not one of the preamble statements above. That is the
    documented contract ("the module docstring or the top comment block") finally enforced:
    a marker below it addresses a statement, not the file.
    """
    index, limit = 0, len(lines)

    # YAML frontmatter opens a rules page and a docs page, and its delimiter is not a
    # comment in any language -- so it is stepped over here rather than in the walk below,
    # where the `--` line-comment opener would otherwise swallow the `---` as SQL.
    while index < limit and not lines[index].strip():
        index += 1
    if index < limit and lines[index].strip() == "---":
        index += 1
        while index < limit and lines[index].strip() != "---":
            index += 1
        index = min(index + 1, limit)

    while index < limit:
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue

        # Block openers are tested BEFORE line openers because `<!--` and `@*` are in both
        # tuples, and they are blocks: reading `<!-- standards: ... ` as a line comment ends
        # the header on the marker's own line, so a reason wrapping onto the next line is
        # cut off and falls under the floor. The marker then goes silently inert -- the
        # exact failure `_truncated_at_terminator` was written to fix, arriving by a
        # different route.
        opener = next((o for o in BLOCK_COMMENT_OPENERS if stripped.startswith(o)), None)
        if opener is not None:
            terminator = BLOCK_COMMENT_PAIRS[opener]
            if terminator in stripped[len(opener) :]:  # opened and closed on one line
                index += 1
                continue
            index += 1
            while index < limit and terminator not in lines[index]:
                index += 1
            index = min(index + 1, limit)
            continue

        if stripped.startswith(REASON_LINE_PREFIXES) or HEADER_PREAMBLE.match(stripped):
            index += 1
            continue

        break

    return index


def _opens_a_line_comment(text_before_marker: str) -> bool:
    """True when the comment the marker sits in is a closerless LINE comment.

    Decided by which opener comes FIRST, because that is the one that actually started
    the comment; everything after it is prose and must not be read as syntax. A bare
    "does the text contain `//`?" test is what this replaces, and it misread two shapes
    that occur in real reasons -- a URL in a docstring (`\"\"\"See https://x ...`) and this
    codebase's own prose dash (`\"\"\"The tables -- notes. standards: ...`). Both made a
    valid docstring marker read as a line comment, so the reason stopped at the first
    unprefixed line, fell under the length floor, and the marker went silently inert.

    No opener at all means the marker is on a continuation line of a comment that began
    earlier -- bare prose under a docstring, or a ` * ` line inside `/* */` -- which is a
    block comment, so its terminator does the delimiting.
    """
    first_opener_index: Optional[int] = None
    is_line_comment = False

    for opener, opens_line_comment in (
        *((text, True) for text in LINE_COMMENT_OPENERS),
        *((text, False) for text in BLOCK_COMMENT_OPENERS),
    ):
        index = text_before_marker.find(opener)
        if index != -1 and (first_opener_index is None or index < first_opener_index):
            first_opener_index, is_line_comment = index, opens_line_comment

    return is_line_comment


def _truncated_at_terminator(text: str) -> tuple[str, bool]:
    """`text` cut at the earliest comment closer inside it, and whether one was there.

    WHY THIS EXISTS. `wrapped_reason` decided where a reason ends from the comment style:
    a line comment stops at the first code line, a block comment stops at its closer. Both
    branches looked only at the lines BELOW the marker, which silently assumed a block
    comment always closes on a later line. Two of the three block syntaxes make that true
    in practice -- a `\"\"\"` docstring and a `/* */` block are conventionally written
    across several lines -- so the assumption held for years.

    HTML is the counter-example, and Razor's `@* *@` is the other one: `<!-- ... -->` is
    normally written entirely on ONE line. Nothing then stopped the walk, so the reason ran
    on into the markup beneath it and borrowed its length. Measured, in the prototypes repo:

        <!-- standards: prototype-js exempt -- because -->
        <title>Testudkast</title>
        <meta name="viewport" ...>

    absorbed the `<title>` and `<meta>` lines and cleared the 30-character floor on a
    seven-character reason -- the floor met by text the author never wrote as an argument.
    That is the same defect this module's docstring already records fixing for `//`
    markers, arriving by the one route that fix did not cover.

    Returning the flag as well as the text matters: "closed here" and "no closer at all"
    are different instructions to the caller, and collapsing them would leave a marker with
    no terminator anywhere unable to wrap onto its continuation lines.

    The EARLIEST closer wins, not the first one in REASON_TERMINATORS order -- otherwise a
    reason containing two different closers would cut at whichever the list happened to
    name first rather than where the comment actually ended.
    """
    positions = [text.find(terminator) for terminator in REASON_TERMINATORS]
    present = [index for index in positions if index != -1]
    if not present:
        return text, False
    return text[: min(present)], True


def wrapped_reason(lines: list[str], marker_index: int, match: "re.Match[str]") -> str:
    """The marker's whole reason, following it onto the continuation lines it wraps to.

    THE ONE walker, called by both scopes. It exists as a function because the two scopes
    each had their own copy and the copies drifted: the line-scoped one stopped at a code
    line, the file-scoped one did not, so a `//` header marker ran on into the file body
    and `-- lookup` (6 chars) cleared the 30-char floor on the strength of the `namespace`
    line beneath it. That is the pack's own DRY rule failing in the pack's own source --
    one behaviour, two implementations, fixed in one of them.

    Where the reason ends depends on how the marker was written, because the two comment
    styles end in different places:

      * A LINE comment (`//`, `#`, `--`) has no closer, so it ends at the first line that
        is not also a line comment -- a code line STOPS the reason.
      * A docstring or `/* */` block has a closer, and its continuation lines carry no
        prefix at all (bare prose under `\"\"\"`). There the terminator does the work, and
        treating an unprefixed line as code would truncate every Python exemption to its
        first line.

    The style is read from which opener INTRODUCES the marker (see
    `_opens_a_line_comment`), rather than from whether its line is wholly a comment, so a
    trailing marker (`namespace Foo;  // standards: ...`) is delimited like the line
    comment it is. Misjudging in this direction is the safe one: a reason cut short falls
    under the floor and the file gets reported, which is loud, where one that runs long is
    silent.

    A block comment may also CLOSE on the marker's own line, which is the third case and
    was missing until an HTML marker found it -- see `_truncated_at_terminator`.
    """
    stops_at_code = _opens_a_line_comment(lines[marker_index][: match.start()])

    # The closer may be on the marker's OWN line, in which case the reason ends there and
    # there is nothing to wrap onto. Checked before the walk, because the walk only ever
    # examined FOLLOWING lines for a terminator.
    first_line_reason, closed_on_marker_line = _truncated_at_terminator(match.group("reason"))
    if closed_on_marker_line:
        return " ".join(first_line_reason.split())

    parts = [first_line_reason]
    for follower in lines[marker_index + 1 :]:
        stripped = follower.strip()
        if not stripped or any(end in stripped for end in REASON_TERMINATORS):
            break
        # A second marker ends the first one's reason. Without this a file declaring two
        # exemptions in one comment block gave the first the text of both, which is not
        # cosmetic: the reason floor is a length test, so a shrug could clear it on the
        # strength of the argument written for a different rule. Seen in
        # standards_concurrency.py, whose bulk marker had swallowed the retry marker.
        if EXEMPTION_MARKER.search(follower):
            break
        for prefix in REASON_LINE_PREFIXES:
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix) :].strip()
                break
        else:
            if stops_at_code:
                break  # a code line: the comment block ended
        parts.append(stripped)

    return " ".join(" ".join(parts).split())


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
      * `collect_exemptions` NAMES it in the scanner's summary, so it is never invisible.
        That third property is what keeps the other two honest, and until 2026-09-01 it
        was true of three rules and claimed of all of them -- every other file-scoped
        marker silenced its rule and appeared in no output anywhere. `header_exemptions`
        below now reads every tag it finds rather than a hand-kept list, so a rule cannot
        add an exemption that nobody sees.

    Write it in the file's HEADER -- the module docstring or the top comment block, within
    the first HEADER_SCAN_LINES lines and ABOVE the first line of code (see
    `file_header_end`; a marker below that addresses the statement it sits on, and is read
    by `line_exemption_reason` instead):

        standards: file-length exempt -- declarative ORM schema; length tracks the
        number of tables, not accumulated responsibility.

    If you cannot write a reason you would defend in review, that is the rule working.
    Split the file.
    """
    return exemption_reason(lines, "file-length")


def header_exemptions(lines: list[str]) -> list[tuple[str, str]]:
    """Every valid file-scoped marker in this file, as (tag, reason), in the order written.

    Reads whatever tag it finds rather than being asked about one, and that is the whole
    point: `collect_exemptions` could only name the three rules it had been told to ask
    about, so a rule that grew an exemption grew an invisible one -- seventeen of them had,
    by 2026-09-01. An exemption nobody sees is just a baseline with better manners, and the
    argument for putting the marker IN the file is that somebody reviewing the repo can
    read it and disagree. That requires it to appear somewhere they look.

    Applies the same header bound and the same reason floor as `exemption_reason`, so a
    marker this returns is one those two properties would honour.

    THAT IS NOT THE SAME AS A RULE HONOURING IT, and this docstring claimed it was until
    2026-09-09. Reading whatever tag it finds is right -- a hand-kept list is what hid
    seventeen rules' markers -- but it means the tag may name a rule that reads no exemption,
    or reads one only at the other scope. Deciding that is `standards_exemption_scope`'s job:
    its registry says which tags are real at which scope, `collect_exemptions` filters this
    list through it before printing, and `exemption-inert` reports the rest as the defects
    they are. A list that disagreed with what the scanner did would be worse than no list,
    and for a while this was one.
    """
    header = lines[: min(HEADER_SCAN_LINES, file_header_end(lines))]
    found: list[tuple[str, str]] = []

    for index, line in enumerate(header):
        match = EXEMPTION_MARKER.search(line)
        if not match:
            continue
        reason = wrapped_reason(header, index, match)
        if len(reason) >= MIN_EXEMPTION_REASON_LENGTH:
            found.append((match.group("tag").casefold(), reason))

    return found


def line_marker_starts(lines: list[str], index: int) -> list[int]:
    """Every line a LINE-scoped read of `index` looks at: the flagged line itself (where a
    trailing marker sits), then up through the comment block attached to it.

    Walking the whole block rather than one line matters because the reason floor actively
    pushes a reason onto a second line, and it is the marker's FIRST line that carries the
    tag -- checking only `index - 1` would find the continuation and miss the marker. A code
    line ends the block, so a marker separated from the flagged line by real code never
    carries.

    PUBLIC because `standards_exemption_scope` needs this reach WITHOUT the reason floor: it
    asks whether a marker sits anywhere a rule would ever look, and a marker whose reason is
    too short is precisely the case it must still report. Two implementations of this walk
    would disagree eventually, and the disagreement would be invisible in both.
    """
    if not 0 <= index < len(lines):
        return []

    starts = [index]
    probe = index - 1
    while probe >= 0 and index - probe <= MAX_LINE_MARKER_LOOKBEHIND and _is_comment(lines[probe]):
        starts.append(probe)
        probe -= 1
    return starts


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
    wanted = tag.casefold()
    for start in line_marker_starts(lines, index):
        match = EXEMPTION_MARKER.search(lines[start])
        if not match or match.group("tag").casefold() != wanted:
            continue
        # The reason routinely wraps onto the following comment lines, exactly as it does
        # in a header block -- measure the whole argument, not its first line.
        reason = wrapped_reason(lines, start, match)
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
      * `collect_exemptions` NAMES it in the scanner's summary, so it is never invisible.
        That third property is what keeps the other two honest, and until 2026-09-01 it
        was true of three rules and claimed of all of them -- every other file-scoped
        marker silenced its rule and appeared in no output anywhere. `header_exemptions`
        below now reads every tag it finds rather than a hand-kept list, so a rule cannot
        add an exemption that nobody sees.

    Write it in the file's HEADER -- the module docstring or the top comment block, within
    the first HEADER_SCAN_LINES lines and ABOVE the first line of code (see
    `file_header_end`; a marker below that addresses the statement it sits on, and is read
    by `line_exemption_reason` instead):

        standards: file-length exempt -- declarative ORM schema; length tracks the
        number of tables, not accumulated responsibility.

        standards: query-shape exempt -- one-off migration report, run once and never
        consumed elsewhere; a view would outlive its only caller.

    If you cannot write a reason you would defend in review, that is the rule working.
    """
    header = lines[: min(HEADER_SCAN_LINES, file_header_end(lines))]
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
    if match is None:  # unreachable: marker_index came from marker_for_tag
        return None

    # The reason wraps onto the lines below it; `wrapped_reason` owns where it stops, for
    # both scopes, so the two can never again disagree about it. Bounded to the header, so
    # a reason cannot run past the window that makes the marker readable in the first place.
    reason = wrapped_reason(header, marker_index, match)
    return reason if len(reason) >= MIN_EXEMPTION_REASON_LENGTH else None

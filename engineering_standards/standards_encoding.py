"""Detect text that was decoded as a single-byte codepage and re-encoded as UTF-8.

Rule here: mojibake.

WHY THIS EXISTS. On 2026-08-08 an editing pass through PowerShell's `Get-Content -Raw` +
`WriteAllText` mangled every non-ASCII character in nine InvoTrack files. Get-Content read the
UTF-8 bytes as the system ANSI codepage, so a single "ø" became the two characters its UTF-8
bytes happen to mean in that codepage, and WriteAllText wrote THAT back as UTF-8. One file went
through twice and ended up triple-encoded, with Danish doc titles garbled in a user-facing
sidebar.

The build stayed green. The tests stayed green. A compiler does not care what is inside a
string literal, so nothing in the toolchain noticed -- which is exactly why this belongs in the
scanner rather than in review. It was caught by eye, and only because the damage happened to be
visible in a tool result.

WHY IT IS NOT A GREP FOR "Ã". That was the first proposal and it is a Latin-only guard. The
leading character of mojibake is decided by the FIRST byte of the original UTF-8 sequence, so
every script produces a different signature:

    Latin-1 (æøå)     C3, C2  ->  capital A-tilde or A-circumflex, then punctuation
    Hebrew            D6, D7  ->  capital O-diaeresis or a multiplication sign, then punctuation
    Arabic            D8-DB   ->  slashed capital O, U-grave, U-acute, U-circumflex, then …
    Greek / Cyrillic  CE-D1   ->  capital I-circumflex, I-diaeresis, D-stroke, N-tilde, then …
    CJK, emoji        E0-F4   ->  a lowercase accented vowel, then TWO or THREE continuations

    (Described rather than shown: a table of literal mojibake would make this file trip its own
    rule, and an exemption marker for it would be indistinguishable from one covering a defect.)

A pattern listing Latin mojibake would pass a corrupted Hebrew or Arabic file as clean. So this
matches on STRUCTURE instead: a character in the range a UTF-8 lead byte decodes to, followed by
the right NUMBER of characters in the range a continuation byte decodes to. That is
script-agnostic by construction, and it needs no list of scripts to be kept up to date.

WHY THE CONTINUATION COUNT MATTERS. Requiring one continuation after a 2-byte lead but two
after a 3-byte lead is what keeps ordinary prose quiet. "café'" is an accented letter followed
by a curly apostrophe; é sits in the 3-byte lead range, so a one-continuation rule would flag
it. Real 3-byte mojibake always brings two continuations, so the stricter count costs nothing
and removes the whole false-positive class.

WHAT STAYS QUIET. Genuine Hebrew (U+0590+), Arabic (U+0600+), Greek, Cyrillic and CJK live far
above the Latin-1 block this rule inspects, so correctly-encoded text in any of them never
matches. Danish is safe for a subtler reason: Ø and Å are legitimate letters AND lead
characters, but what follows them in Danish is an ASCII letter, never a continuation character.

A NARROWING THAT WAS PROPOSED AND REJECTED (2026-08-10). The residual false positive is an
uppercase accented letter followed IMMEDIATELY by CP1252 punctuation -- an all-caps Danish
word ending in Å and then an ellipsis, say. The suggested fix was to drop the
pure-punctuation continuations from the ONE-continuation branch. Do not do it: it is exactly
backwards, because those punctuation continuations are what MANGLED DANISH LOOKS LIKE. Å is
C3 85 in UTF-8, and 0x85 is the CP1252 ellipsis; Ø is C3 98 (small tilde), Æ is C3 86
(dagger), É is C3 89 (per-mille). So the narrowing would blind the rule to the precise
corruption this codebase is likeliest to suffer, in order to silence a hypothetical.
Measured on this repo the same day: 25 files contain Danish letters and the rule matches
nothing in any of them, while it did catch a real double-encoding introduced that morning.
Round-tripping cannot separate the two cases either -- a legitimate Å-then-ellipsis
re-encodes to a valid U+0145, so it "recovers" as convincingly as real damage does. The
escape hatch for a genuine false positive is a line-scoped `mojibake` exemption.

Note this paragraph names the byte values rather than showing the mangled glyphs. Writing
them out put real double-encoded text into the file and the rule flagged its own
documentation -- correctly, since it cannot tell an example from an accident.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import Violation
from standards_exemptions import line_exemption_reason

RULE = "mojibake"

# What a UTF-8 CONTINUATION byte (0x80-0xBF) decodes to under the codepages that cause this.
# Latin-1 maps 0x80-0x9F to the C1 control block; CP1252 maps most of the same range to
# punctuation instead (0x80 -> EUR, 0x92 -> a right single quote, ...). Both are listed,
# because which one you get depends on the tool. 0xA0-0xBF is identical in both.
#
# THE C1 CONTROLS ARE NOT PADDING -- they are what makes Hebrew detectable. CP1252 leaves five
# byte values undefined (0x81, 0x8D, 0x8F, 0x90, 0x9D), and Windows' own CP1252 decoder passes
# those through as U+0081, U+008D, U+008F, U+0090, U+009D rather than failing. Hebrew needs
# exactly that: aleph is U+05D0 = D7 90, so a mangled aleph is the multiplication sign followed
# by U+0090. Drop the C1 range and the rule goes blind to the first letter of the Hebrew
# alphabet. (Python's own cp1252 codec is stricter and raises on those five, which is why the
# tests simulate the accident with latin-1 -- see test_standards_encoding.py.)
_CONTINUATION = "-¿€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ"

# A lead character announces how many continuations follow, and we demand exactly that many.
# U+00C0/U+00C1 are excluded: 0xC0/0xC1 are illegal UTF-8 lead bytes, so a Latin capital
# A-grave or A-acute followed by punctuation is ordinary text, not corruption.
#
# The trailing + groups a RUN of mangled characters into one finding. A corrupted Hebrew or
# Arabic word is mangled end to end, and reporting it per character would bury the file in
# near-identical findings that all share one fix.
_MOJIBAKE = re.compile(f"(?:[Â-ß][{_CONTINUATION}]|[à-ï][{_CONTINUATION}]{{2}}|[ð-ô][{_CONTINUATION}]{{3}})+")


def _recover(mangled: str) -> str | None:
    """The text this fragment probably started as, or None if it will not round-trip.

    Reversing the damage is the same trip backwards: re-encode to the byte values the
    characters came from, then read those bytes as UTF-8. It is best-effort and only used to
    make the message concrete -- CP1252 leaves five byte values undefined, so a fragment that
    passed through them cannot be recovered and simply is not quoted.
    """
    for codepage in ("cp1252", "latin-1"):
        try:
            recovered = mangled.encode(codepage).decode("utf-8")
        except UnicodeEncodeError, UnicodeDecodeError:
            continue
        if recovered != mangled:
            return recovered
    return None


def check_mojibake(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag double-encoded text anywhere in a file, comments and strings included.

    Deliberately NOT restricted to code lines. A mangled comment is still mangled, and a
    mangled Danish string literal is the case that actually shipped.
    """
    for index, line in enumerate(lines):
        match = _MOJIBAKE.search(line)
        if not match:
            continue
        if line_exemption_reason(lines, index, RULE):
            continue

        mangled = match.group(0)
        recovered = _recover(mangled)
        became = f" It should read {recovered!r}." if recovered else ""
        yield Violation(
            path=path,
            line=index + 1,
            rule=RULE,
            message=(
                f"{mangled!r} is double-encoded text: UTF-8 bytes that were read as a "
                f"single-byte codepage and written back as UTF-8.{became} Restore the file "
                f"from git and redo the edit with a UTF-8-safe editor -- do not try to "
                f"un-mangle it in place. Most often caused by PowerShell "
                f"`Get-Content -Raw` + `WriteAllText`; never edit source that way."
            ),
        )

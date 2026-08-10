#!/usr/bin/env python3
"""Tests for the exemption marker, both scopes.

Written when `line_exemption_reason` was added, and the case that motivated it is the
second test: a file that holds BOTH a real defect and a legitimate exception must be able
to excuse only the second. A file-scoped marker cannot express that, and using one would
have silently hidden a genuine `tenantId ?? ''` in allegro-it-services.

The reason floor gets its own tests because it is the only thing standing between an
exemption and a shrug, and a floor that silently accepted everything would look exactly
like a working one. That failure mode has already hit this pack twice.

Run: python test_standards_exemptions.py
"""

from __future__ import annotations

import sys

from standards_exemptions import (
    MIN_EXEMPTION_REASON_LENGTH,
    exemption_reason,
    line_exemption_reason,
)
from standards_core import ZeroCoalescePatterns  # noqa: F401  (import-cycle canary)


LONG = "form state bound to a text input, which has no absent value in HTML"
assert len(LONG) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the floor"


def _lines(*text: str) -> list[str]:
    return list(text)


CASES: list[tuple[str, bool, list[str], int]] = [
    (
        "trailing marker on the flagged line",
        True,
        _lines(
            "const a = 1;",
            f"  senderUserId: x.senderUserId ?? '',  // standards: unset-not-zero exempt -- {LONG}",
        ),
        1,
    ),
    (
        "marker on the line immediately above",
        True,
        _lines(
            f"  // standards: unset-not-zero exempt -- {LONG}",
            "  senderUserId: x.senderUserId ?? '',",
        ),
        1,
    ),
    (
        "marker two lines above does not carry",
        False,
        _lines(
            f"  // standards: unset-not-zero exempt -- {LONG}",
            "  const unrelated = 1;",
            "  senderUserId: x.senderUserId ?? '',",
        ),
        2,
    ),
    (
        "a marker for a DIFFERENT rule does not excuse this one",
        False,
        _lines(f"  x = y ?? '';  // standards: query-shape exempt -- {LONG}"),
        0,
    ),
    (
        "reason under the floor is rejected",
        False,
        _lines("  x = y ?? '';  // standards: unset-not-zero exempt -- form state"),
        0,
    ),
    (
        "a wrapped reason is measured whole, so it clears the floor",
        True,
        _lines(
            "  // standards: unset-not-zero exempt -- form state bound to",
            "  // a text input, which has no absent value in HTML.",
            "  senderUserId: x.senderUserId ?? '',",
        ),
        2,
    ),
    (
        "wrapping stops at a code line rather than swallowing it",
        False,
        _lines(
            "  // standards: unset-not-zero exempt -- form state",
            "  senderUserId: x.senderUserId ?? '',",
        ),
        1,
    ),
    (
        "no marker at all",
        False,
        _lines("  senderUserId: x.senderUserId ?? '',"),
        0,
    ),
    (
        "index out of range is not an error",
        False,
        _lines("  x = 1;"),
        99,
    ),
]


def main() -> int:
    failures: list[str] = []

    for label, should_exempt, lines, index in CASES:
        got = line_exemption_reason(lines, index, "unset-not-zero")
        if bool(got) != should_exempt:
            failures.append(
                f"line_exemption_reason: {label}\n"
                f"    expected {'an exemption' if should_exempt else 'none'}, got {got!r}"
            )

    # The file-scoped form must keep working, and must NOT be satisfied by a marker that
    # sits past the header window -- that limit is the whole reason it is readable.
    header_ok = exemption_reason(
        [f"# standards: file-length exempt -- {LONG}"] + ["code"] * 50, "file-length"
    )
    if not header_ok:
        failures.append("exemption_reason: a header marker stopped being honoured")

    buried = exemption_reason(
        ["code"] * 50 + [f"# standards: file-length exempt -- {LONG}"], "file-length"
    )
    if buried:
        failures.append("exemption_reason: a marker buried past the header was honoured")

    # A LINE-comment marker must stop at the first code line. Before this was enforced the
    # reason ran on into the file body, and a `-- lookup` shrug cleared the 30-char floor on
    # the strength of the `namespace` line beneath it -- the floor met by text the author
    # never wrote as a reason. C# was the exposed language: `#` and `"""` happen to be
    # followed by a terminator often enough that the hole never showed.
    absorbed = exemption_reason(
        [
            "// standards: file-length exempt -- lookup",
            "namespace DonorLink.Data.Translations;",
            "",
            "public static class DanishTranslations",
        ],
        "file-length",
    )
    if absorbed:
        failures.append(
            "exemption_reason: a short reason cleared the floor by absorbing a code line "
            f"({absorbed!r})"
        )

    trailing_code = exemption_reason(
        [
            "// standards: file-length exempt -- a translation lookup table; its length",
            "// tracks the number of UI keys, not accumulated responsibility.",
            "namespace DonorLink.Data.Translations;",
        ],
        "file-length",
    )
    if trailing_code is None or "namespace" in trailing_code:
        failures.append(
            "exemption_reason: a good line-comment reason must be honoured AND stop at the "
            f"code line, got {trailing_code!r}"
        )

    # A TRAILING marker is the same closerless comment as a whole-line one, so it must be
    # delimited the same way. Classifying by "is the whole line a comment?" would miss it and
    # leave the floor reachable by borrowed text on this one shape.
    trailing_marker = exemption_reason(
        [
            "namespace DonorLink.Data.Translations;  // standards: file-length exempt -- lookup",
            "public static class DanishTranslations",
        ],
        "file-length",
    )
    if trailing_marker:
        failures.append(
            "exemption_reason: a trailing short marker cleared the floor by absorbing code "
            f"({trailing_marker!r})"
        )

    # The stop must NOT reach a docstring marker: its continuation lines are bare prose with
    # no comment prefix, so treating an unprefixed line as "code" would truncate every
    # Python exemption to its first line. The closer is what ends the reason there.
    docstring = exemption_reason(
        [
            '"""The account tables.',
            "",
            "standards: file-length exempt -- declarative ORM schema; its length tracks",
            "the number of tables, not accumulated responsibility.",
            '"""',
        ],
        "file-length",
    )
    if docstring is None or "number of tables" not in docstring:
        failures.append(
            "exemption_reason: a docstring reason must still wrap onto its unprefixed "
            f"continuation lines, got {docstring!r}"
        )

    # The two scopes must not leak into each other: a header marker is not a licence for
    # every line in the file, which is exactly the conflation this split exists to prevent.
    leaked = line_exemption_reason(
        [f"# standards: unset-not-zero exempt -- {LONG}"] + ["x = y ?? '';"] * 5,
        3,
        "unset-not-zero",
    )
    if leaked:
        failures.append("line_exemption_reason: a header marker excused an unrelated line")

    for failure in failures:
        print(f"FAIL  {failure}")
    total = len(CASES) + 7
    print(f"\n{total - len(failures)}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

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
    total = len(CASES) + 3
    print(f"\n{total - len(failures)}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

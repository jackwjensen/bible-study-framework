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

from standards_core import ZeroCoalescePatterns  # noqa: F401  (import-cycle canary)
from standards_exemptions import (
    MIN_EXEMPTION_REASON_LENGTH,
    exemption_reason,
    header_exemptions,
    line_exemption_reason,
)

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


def _line_scoped_cases() -> list[str]:
    """Every case in the CASES table: does the line-scoped marker apply where it should?"""
    failures: list[str] = []
    for label, should_exempt, lines, index in CASES:
        got = line_exemption_reason(lines, index, "unset-not-zero")
        if bool(got) != should_exempt:
            failures.append(
                f"line_exemption_reason: {label}\n"
                f"    expected {'an exemption' if should_exempt else 'none'}, got {got!r}"
            )

    return failures


def _header_window_cases() -> list[str]:
    """The file-scoped form, and that a marker past the header window does NOT satisfy it."""
    failures: list[str] = []
    # The file-scoped form must keep working, and must NOT be satisfied by a marker that
    # sits past the header window -- that limit is the whole reason it is readable.
    header_ok = exemption_reason([f"# standards: file-length exempt -- {LONG}"] + ["code"] * 50, "file-length")
    if not header_ok:
        failures.append("exemption_reason: a header marker stopped being honoured")

    buried = exemption_reason(["code"] * 50 + [f"# standards: file-length exempt -- {LONG}"], "file-length")
    if buried:
        failures.append("exemption_reason: a marker buried past the header was honoured")

    return failures


def _line_comment_delimiter_cases() -> list[str]:
    """A line-comment reason must stop at the first code line, or it borrows the file's text."""
    failures: list[str] = []
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
        failures.append(f"exemption_reason: a short reason cleared the floor by absorbing a code line ({absorbed!r})")

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
            f"exemption_reason: a trailing short marker cleared the floor by absorbing code ({trailing_marker!r})"
        )

    return failures


def _docstring_marker_cases() -> list[str]:
    """A docstring reason wraps onto unprefixed continuation lines; its closer is what ends it."""
    failures: list[str] = []
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

    # A docstring marker whose line carries PROSE containing line-comment syntax is still a
    # docstring marker. The classifier used to ask "does the text before the marker contain
    # `//` / `--` / `#`?", so a URL or this codebase's own prose dash flipped it to line-
    # comment style, the reason stopped at the first unprefixed line, fell under the floor,
    # and a perfectly good marker went silently inert. Position decides, not membership:
    # whichever opener comes FIRST is the one that started the comment.
    # The prose and the marker must share a LINE for this to bite -- that is the only
    # position `line_before_marker` looks at.
    for label, opener_and_prose, closer in (
        ("a URL in the prose", '"""See https://example.com/x. ', '"""'),
        ("a prose dash", '"""The account tables -- notes. ', '"""'),
        ("an HTML opener, whose own syntax contains `--`", "<!-- ", "-->"),
    ):
        prose_before_marker = exemption_reason(
            [
                opener_and_prose + "standards: file-length exempt -- declarative ORM schema; its length tracks",
                "the number of tables, not accumulated responsibility.",
                closer,
            ],
            "file-length",
        )
        if prose_before_marker is None or "number of tables" not in prose_before_marker:
            failures.append(
                f"exemption_reason: {label} before the marker must not make it read as a "
                f"line comment, got {prose_before_marker!r}"
            )

    # ...and the trailing-marker case must keep working, which is the one that genuinely
    # DOES need line-comment delimiting. Guarded here so a fix for the above cannot quietly
    # reopen the hole the classifier was added to close.
    trailing_after_code = exemption_reason(
        ["namespace DonorLink.Data;  // standards: file-length exempt -- lookup", "class Danish"],
        "file-length",
    )
    if trailing_after_code:
        failures.append(
            f"exemption_reason: a trailing short marker must still stop at the code line, got {trailing_after_code!r}"
        )

    return failures


def _block_comment_cases() -> list[str]:
    """The HTML/CSS block form, both when it closes on the marker's own line and when it wraps."""
    failures: list[str] = []
    # A BLOCK COMMENT THAT CLOSES ON THE MARKER'S OWN LINE. The walker only ever looked at
    # following lines for a terminator, which assumed a block comment always closes on a
    # later one. True by convention for `"""` and `/* */`, and false for the two syntaxes
    # normally written on a single line: HTML's `<!-- -->` and Razor's `@* *@`. The reason
    # then ran on into the markup beneath and borrowed its length -- the same defect this
    # module already guards for `//` markers, by the one route that fix did not cover.
    # Found in the prototypes repo, where the markers are HTML by necessity.
    for label, opener, closer in (
        ("an HTML comment", "<!-- ", " -->"),
        ("a Razor comment", "@* ", " *@"),
    ):
        absorbed_markup = exemption_reason(
            [
                f"{opener}standards: file-length exempt -- because{closer}",
                "<title>Testudkast - Allegro IT</title>",
                '<meta name="viewport" content="width=device-width, initial-scale=1">',
                '<meta name="robots" content="noindex, nofollow">',
            ],
            "file-length",
        )
        if absorbed_markup:
            failures.append(
                f"exemption_reason: {label} closing on its own line let a short reason clear "
                f"the floor by absorbing the markup below it ({absorbed_markup!r})"
            )

    # ...and the same shape with a REAL reason must still be honoured, or the fix above has
    # turned an exception mechanism into a ban. This is the case the prototypes repo relies
    # on, so it is the one that must not become collateral damage.
    single_line_html = exemption_reason(
        [
            "<!-- standards: prototype-js exempt -- illustrates the drag-to-reorder route "
            "planner, which is the feature under discussion. -->",
            "<title>Ruteplan</title>",
        ],
        "prototype-js",
    )
    if single_line_html is None or "drag-to-reorder" not in single_line_html:
        failures.append(
            f"exemption_reason: a single-line HTML marker with a real reason must be honoured, got {single_line_html!r}"
        )
    if single_line_html and ("<title>" in single_line_html or "-->" in single_line_html):
        failures.append(
            f"exemption_reason: a single-line HTML reason must stop at its own closer, got {single_line_html!r}"
        )

    # The multi-line block form must keep wrapping onto its continuation lines -- the case
    # the original walker was built for. Guarded so the single-line fix cannot truncate it.
    multi_line_html = exemption_reason(
        [
            "<!-- standards: file-length exempt -- a declarative translation table whose",
            "     length tracks the number of UI keys, not accumulated responsibility.",
            "-->",
            "<title>Something</title>",
        ],
        "file-length",
    )
    if multi_line_html is None or "number of UI keys" not in multi_line_html:
        failures.append(
            "exemption_reason: a multi-line HTML reason must still wrap onto its "
            f"continuation lines, got {multi_line_html!r}"
        )

    return failures


def _scope_separation_cases() -> list[str]:
    """The two scopes must not leak into each other, in either direction."""
    failures: list[str] = []
    # The two scopes must not leak into each other: a header marker is not a licence for
    # every line in the file, which is exactly the conflation this split exists to prevent.
    leaked = line_exemption_reason(
        [f"# standards: unset-not-zero exempt -- {LONG}"] + ["x = y ?? '';"] * 5,
        3,
        "unset-not-zero",
    )
    if leaked:
        failures.append("line_exemption_reason: a header marker excused an unrelated line")

    # ...and the other direction, which leaked in silence until 2026-09-01. A LINE marker
    # written inside the first HEADER_SCAN_LINES lines was also a valid FILE marker, so it
    # silenced the rule for every line of the file. This is the real shape: `client_ip()`
    # sits at line ~30 of every `lib/request.php` in the estate, well inside the window.
    promoted = exemption_reason(
        [
            "<?php",
            "declare(strict_types=1);",
            "",
            "// Facts about the incoming HTTP request, in one place.",
            "",
            "const REQUEST_HEADER_MAX_LENGTH = 500;",
            "",
            "function client_ip(): string {",
            f"    // standards: client-address exempt -- {LONG}",
            # standards: client-address exempt -- a PHP fixture string for the header
            # parser, not an address this file resolves; the peer read is here because the
            # marker's real-world placement is inside exactly this function.
            "    return $_SERVER['REMOTE_ADDR'];",
            "}",
        ],
        "client-address",
    )
    if promoted:
        failures.append(
            "exemption_reason: a line marker below the header was promoted to file scope, "
            f"silencing the whole file, got {promoted!r}"
        )

    # The legitimate file-scoped forms must survive that narrowing -- in the three languages
    # whose header block does NOT start on line 1, which is where a naive "stop at the first
    # code line" would have broken them.
    for label, lines_in, tag in (
        (
            "PHP, below <?php and declare()",
            [
                "<?php",
                "declare(strict_types=1);",
                "",
                f"// standards: client-address exempt -- {LONG}",
                "",
                "function client_ip(): string {",
                # standards: client-address exempt -- a PHP fixture string for the header
                # parser, not an address this file resolves; the peer read is here because
                # the marker's real placement is inside exactly this function.
                "    return $_SERVER['REMOTE_ADDR'];",
                "}",
            ],
            "client-address",
        ),
        (
            "Razor, above its @page directive",
            [
                f"@* standards: userdocs-unlinked-page exempt -- {LONG} *@",
                '@page "/"',
                "<h1>Hi</h1>",
            ],
            "userdocs-unlinked-page",
        ),
        (
            "markdown, below YAML frontmatter",
            [
                "---",
                "audience: dev",
                "---",
                f"<!-- standards: docs-orphan-page exempt -- {LONG} -->",
                "# Help",
            ],
            "docs-orphan-page",
        ),
    ):
        if not exemption_reason(lines_in, tag):
            failures.append(f"exemption_reason: a file marker in {label} stopped being honoured")

    return failures


def _header_exemptions_listing_cases() -> list[str]:
    """`header_exemptions` must list every marker AND agree with what the rules honour."""
    failures: list[str] = []
    # Every marker, whatever its tag -- the property that lets the scanner NAME exemptions
    # it was never told to ask about. A hand-kept list is how seventeen of them became
    # invisible in the first place.
    listed = header_exemptions(
        [
            f"# standards: file-length exempt -- {LONG}",
            "",
            f"# standards: query-shape exempt -- {LONG}",
            "import os",
        ]
    )
    if [tag for tag, _ in listed] != ["file-length", "query-shape"]:
        failures.append(f"header_exemptions: must report every tag it finds, got {listed!r}")

    # It must agree with what the rules actually honour, or the summary would advertise a
    # silence the scanner is not applying -- worse than printing nothing.
    if header_exemptions([f"# standards: file-length exempt -- {'x' * 5}", "import os"]):
        failures.append("header_exemptions: reported a marker that fails the reason floor")

    return failures


def main() -> int:
    """Run every group and report them together.

    Split into groups on 2026-09-02: this was one function with 23 branches and 55
    statements, which is over the pack's own limits -- and while the ruff config excuses
    a test's SIZE, a runner nobody can read is not excused by a config saying it may be
    long. Each group now names the property it guards, and a failure says which.
    """
    failures: list[str] = []
    for group in (
        _line_scoped_cases,
        _header_window_cases,
        _line_comment_delimiter_cases,
        _docstring_marker_cases,
        _block_comment_cases,
        _scope_separation_cases,
        _header_exemptions_listing_cases,
    ):
        failures.extend(group())

    for failure in failures:
        print(f"FAIL  {failure}")
    total = len(CASES) + 18
    print(f"\n{total - len(failures)}/{total} passed")
    return 1 if failures else 0


def test_every_case_in_this_module_passes() -> None:
    """pytest entry point for a table-driven module.

    Without this, pytest collects NOTHING here: the cases live in a table that main()
    walks, and no function in the file is named test_*. So the module ran only when
    somebody typed `python test_standards_exemptions.py` by hand -- while `verify.py` and CI, which
    run pytest, silently skipped it. The file looked tested and was not, which is the
    same failure mode it exists to guard against, one level up.
    """
    assert main() == 0, "see the standalone output above for which case failed"


if __name__ == "__main__":
    sys.exit(main())

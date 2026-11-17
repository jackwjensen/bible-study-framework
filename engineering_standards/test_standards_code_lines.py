#!/usr/bin/env python3
"""Cases for `iter_code_lines`: which lines of a file are CODE rather than prose.

Plain asserts, no pytest, so it runs anywhere Python does:

    python engineering_standards/test_standards_code_lines.py

WHY THIS SUITE EXISTS, and it is the most load-bearing primitive in the pack. Nearly every
per-file rule walks `iter_code_lines`, so a line it declines to yield is a line NO rule can
see. That failure is silent by construction: the rule reports nothing for the region, and the
run prints `clean`.

It had been doing exactly that. `standards_core` decided a block region opened when a line's
stripped form STARTED WITH a delimiter, which misses `r\"\"\"(?x)` -- a prefixed string, and the
shape every regex constant in this pack is written in. The opener was therefore invisible and
its CLOSER, a bare `\"\"\"` on its own line, was read as an opener instead. Parity inverted
there and stayed inverted: measured on 2026-09-09, `standards_client_address.py` skipped
`def _appears_near(...)` at line 201 as prose while yielding its docstring as code, and
swallowed everything after line 253 outright. `standards_sqlinjection.py` stopped at 299 of
333. Both are DETECTORS, so the rules they implement were partly blind in every repo that
ships them.

The last test below is the one that matters: Python's own tokenizer says which lines hold
code, and no line it names may ever be skipped. A hand-written case pins a shape somebody
thought of; the tokenizer pins every shape in the pack, including the next one.
"""

from __future__ import annotations

import io
import sys
import token
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import iter_code_lines  # noqa: E402

# Built rather than written, so the fixtures below can hold triple quotes without this file
# having to escape its own docstring apart.
Q = '"' * 3
SQ = "'" * 3


def code(source: str, suffix: str = ".py") -> list[int]:
    return [number for number, _ in iter_code_lines(source.splitlines(), suffix)]


# ---- the defect: a prefixed string is a region, and its closer is not an opener -----------


def test_a_prefixed_triple_quoted_string_does_not_invert_the_walk():
    """THE MEASURED BUG. `r\"\"\"` opens a region the old test could not see, so the bare
    `\"\"\"` closing it was read as an opener and every line after it changed sides."""
    source = "\n".join(
        [
            "PATTERN = re.compile(",
            f"    r{Q}(?x)",
            "      (?:REMOTE_ADDR)",
            f"    {Q},",
            "    re.IGNORECASE,",
            ")",
            "",
            "",
            "def resolve(request):",
            f"    {Q}Read the peer address.",
            "",
            "    Prose that names the pattern above, which no rule may read as code.",
            f"    {Q}",
            "    return request.peer",
        ]
    )

    yielded = code(source)

    assert 9 in yielded, "the `def` line is code and was being skipped as prose"
    assert 14 in yielded, "the function body is code and was being swallowed"
    assert 12 not in yielded, "docstring prose must never be yielded"


def test_a_region_opened_mid_line_still_closes_on_a_later_line():
    """`SPLITS_ON_COMMA = re.compile(r\"\"\"...` starts its string after real code, so the old
    `startswith` test never saw it -- and then mistook the closer for an opener."""
    source = "\n".join(
        [
            f"SPLITS = re.compile(r{Q}(?:split",
            "  |explode)",
            f"{Q})",
            "AFTER = 1",
        ]
    )

    yielded = code(source)

    assert 2 not in yielded, "the middle of a multi-line pattern is not code"
    assert 4 in yielded, "everything after the pattern is code"


def test_the_declaration_line_of_a_triple_quoted_value_is_code():
    """A constant is a constant whichever quotes hold it. The line has a name and an `=` on
    it, so the rules that read declarations have to be given it -- the whole reason this
    walk exists is to hide PROSE, and `SPLITS = ...` is not prose."""
    source = f"SPLITS = re.compile(r{Q}(?:split\n  |explode)\n{Q})\n"

    assert 1 in code(source)


# ---- what the walk has always promised ----------------------------------------------------


def test_a_docstring_body_is_never_yielded():
    """The founding guarantee: "prose explaining a rule necessarily contains an example of
    the thing the rule forbids"."""
    source = "\n".join(
        [
            f"{Q}A module.",
            "",
            "It explains that `password = 'hunter2'` is the shape this rule catches.",
            f"{Q}",
            "VALUE = 1",
        ]
    )

    assert code(source) == [5]


def test_a_single_line_docstring_opens_nothing():
    source = "\n".join(["def f():", f"    {Q}Do the thing.{Q}", "    return 1"])

    assert code(source) == [1, 3]


def test_a_comment_mentioning_a_triple_quote_does_not_open_a_region():
    """The scan stops at the comment opener, so the `\"\"\"` after it is never reached. Reading
    it as an opener would swallow the rest of the file -- the same failure by another door,
    and the one the old walk guarded against by skipping whole-line comments first."""
    source = "\n".join([f"# the {Q} delimiter is how Python spells a docstring", "VALUE = 1"])

    assert code(source) == [2]


def test_a_trailing_comment_does_not_make_the_line_prose():
    """Only the comment is dropped; the code before it still counts."""
    source = f"VALUE = 1  # mentions {Q} and a URL https://example.test\nOTHER = 2\n"

    assert code(source) == [1, 2]


def test_a_url_inside_a_string_is_not_a_comment():
    """`//` inside a string literal must not truncate the line. Strings are scanned PAST for
    this reason as much as for the delimiter case -- a C# or TypeScript file is full of them,
    and a truncated line whose only code sat after the URL would go unseen."""
    source = 'var endpoint = "https://api.example.test/v1";\n'

    assert code(source, ".ts") == [1]


def test_an_escaped_quote_does_not_end_a_string_early():
    source = 'var quoted = "he said \\"hi\\" loudly"; // note\nvar next = 2;\n'

    assert code(source, ".ts") == [1, 2]


def test_single_quoted_regions_work_the_same_way():
    source = "\n".join([f"TEXT = {SQ}first", "second", f"{SQ}", "VALUE = 1"])

    yielded = code(source)

    assert 2 not in yielded
    assert 4 in yielded


def test_a_brace_language_block_comment_still_spans_lines():
    """The other delimiters go through the same walk, so they need a case in it."""
    source = "\n".join(["var a = 1;", "/* a comment", "   still commenting */", "var b = 2;"])

    assert code(source, ".ts") == [1, 4]


def test_code_after_a_block_comment_closes_on_the_same_line_is_yielded():
    source = "\n".join(["/* leading note */ var a = 1;", "var b = 2;"])

    assert code(source, ".ts") == [1, 2]


def test_a_blank_line_outside_a_region_is_still_yielded():
    """Deliberate, and pinned because it looks like an oversight.

    Four rules materialise this sequence and reason about adjacency in it, so whether blank
    lines occupy a slot decides how far their proximity windows reach -- and dropping them
    would move `standards_tls` and `standards_sentinels` in opposite directions. That is a
    decision about those four rules, not a side effect of repairing the walk.
    """
    source = "\n".join(["VALUE = 1", "", "OTHER = 2"])

    assert code(source) == [1, 2, 3]


def test_a_blank_line_inside_a_docstring_is_not_yielded():
    """The other half: inside a region, blank or not, it is prose."""
    source = "\n".join([f"{Q}A module.", "", "Still prose.", f"{Q}", "VALUE = 1"])

    assert code(source) == [5]


# ---- the ground truth: Python's own tokenizer ---------------------------------------------


def _lines_holding_real_code(source: str) -> set[int]:
    """Line numbers Python's tokenizer says carry code, from the token stream itself.

    STRING and COMMENT tokens are excluded, so a docstring contributes nothing and neither
    does a comment. A multi-line string contributes only the line its token STARTS on, which
    is the line the assignment is written on -- exactly the line a rule needs to see.
    """
    holds_code: set[int] = set()
    for item in tokenize.generate_tokens(io.StringIO(source).readline):
        if item.type in (token.STRING, token.COMMENT, token.NL, token.NEWLINE, token.INDENT, token.DEDENT):
            continue
        if item.type in (token.ENDMARKER, getattr(token, "FSTRING_START", -1)):
            continue
        if item.start[0] == item.end[0]:
            holds_code.add(item.start[0])
    return holds_code


def test_no_line_python_calls_code_is_ever_skipped():
    """THE TEST THAT WOULD HAVE CAUGHT THIS, and will catch the next one.

    Run over every module the pack ships. `iter_code_lines` may be conservative in one
    direction -- yielding a line that is really prose costs a false positive, which is loud
    and gets fixed. Skipping a line that is really code costs BLINDNESS, which is silent and
    is the failure class this pack exists to prevent. So the assertion is one-directional:
    everything Python's tokenizer calls code must be yielded.

    Asserted against the pack's own source rather than fixtures because the shapes that broke
    it -- a prefixed multi-line regex, a pattern opened after an `=` -- are how this codebase
    actually writes its detectors, and no fixture author had thought of either.
    """
    pack = Path(__file__).resolve().parent
    blind: list[str] = []

    for source_file in sorted(pack.glob("*.py")):
        source = source_file.read_text(encoding="utf-8")
        yielded = {number for number, _ in iter_code_lines(source.splitlines(), ".py")}
        for line in sorted(_lines_holding_real_code(source) - yielded):
            blind.append(f"{source_file.name}:{line}")

    assert blind == [], f"{len(blind)} code line(s) no rule can see: {blind[:12]}"


if __name__ == "__main__":
    from standards_selftest import run_module_tests

    sys.exit(run_module_tests(globals(), "code-line walk cases"))

#!/usr/bin/env python3
"""Cases for the one thing the exemption report could not previously tell you: whether the
marker in front of you does anything at all.

Plain asserts, no pytest, so it runs anywhere Python does:

    python engineering_standards/test_standards_exemption_scope.py

THE FAILURE THESE COVER IS THE INVERSE OF THE ONE `collect_exemptions` WAS WRITTEN TO CLOSE.
That report exists because a marker could silence a rule invisibly. What shipped instead was
a report that NAMED a marker the scanner ignored: `exempt: engine/config.py
[const-environment-literal] -- <reason>` printed, and the finding reported in the same run.
A report that claims a decision nobody honours is worse than no report, because the claim is
the thing people rely on. Found on sourcetext.ai, 2026-09-09.

NO FILE-SCOPED EXEMPTION HERE, deliberately, though every other detector's suite carries one.
Every marker below is a Python string literal on a code line, which the detector already
declines to read for the reason `_is_quoted_text` gives -- so the hatch would buy nothing and
cost the one thing that matters: it would blind this rule on the file most likely to break it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


from standards_config import CheckConfig  # noqa: E402
from standards_dispatch import check_source_file  # noqa: E402
from standards_exemption_scope import (  # noqa: E402
    FILE_SCOPED_TAGS,
    INERT_RULE,
    LINE_SCOPED_TAGS,
    check_inert_exemption,
)
from standards_exemptions import HEADER_SCAN_LINES  # noqa: E402

MARKER = "standards:"

# Built rather than written, so this file holds no marker a reader could mistake for a
# decision -- and so the suite cannot excuse itself by accident.
FILE_HEADER = '"""A module.\n\n{marker} {tag} exempt -- {reason}\n"""\n'


def header(tag: str, reason: str) -> str:
    return FILE_HEADER.format(marker=MARKER, tag=tag, reason=reason)


def inert(source: str, name: str = "engine/config.py") -> list[str]:
    return [v.rule for v in check_inert_exemption(Path(name), source.splitlines())]


def messages(source: str, name: str = "engine/config.py") -> list[str]:
    return [v.message for v in check_inert_exemption(Path(name), source.splitlines())]


A_REAL_REASON = "declarative schema whose length tracks the number of tables, not responsibility."


# ---- the tag names no rule that reads an exemption ----------------------------------------


def test_a_marker_for_a_rule_with_no_hatch_is_reported():
    """`rules-scope-declared` deliberately has none -- alwaysLoad IS its exemption -- so a
    marker for it is a sentence its author believes works and nothing ever reads."""
    assert inert(header("rules-scope-declared", A_REAL_REASON)) == ["exemption-inert"]


def test_a_misspelled_tag_is_reported():
    """The failure with no other detector at all: nothing matches, so nothing complains."""
    source = header("file-lenght", A_REAL_REASON)

    assert inert(source) == ["exemption-inert"]
    assert "file-lenght" in messages(source)[0]


# ---- right tag, wrong scope ---------------------------------------------------------------


def test_a_line_scoped_rules_marker_in_the_header_is_reported():
    """The shape found on sourcetext.ai. The rule reads a marker beside the flagged line and
    never looks at the header, so a header marker for it is decoration."""
    source = header("const-environment-literal", "every value here is a vendor endpoint identical everywhere.")

    assert inert(source) == ["exemption-inert"]
    assert "beside the line" in messages(source)[0]


def test_a_file_scoped_rules_marker_beside_a_line_is_reported():
    """The same defect the other way round: `query-shape` is only ever read from the header,
    so a marker written against the query it excuses does nothing."""
    source = (
        "def report(session):\n"
        f"    # {MARKER} query-shape exempt -- a one-off migration report, run once and\n"
        "    # consumed nowhere else; a view would outlive its only caller.\n"
        "    return session.execute(BIG_QUERY)\n"
    )

    assert inert(source) == ["exemption-inert"]
    assert "in the file header" in messages(source)[0]


# ---- the markers that DO work -------------------------------------------------------------


def test_a_file_scoped_marker_in_the_header_is_silent():
    assert inert(header("file-length", A_REAL_REASON)) == []


def test_a_line_scoped_marker_beside_its_line_is_silent():
    source = (
        f"# {MARKER} const-environment-literal exempt -- the vendor's published API base,\n"
        "# identical in every deployment; config would invent a knob nobody ever turns.\n"
        'API_BASE = "https://api.stripe.com/v1"\n'
    )

    assert inert(source) == []


def test_a_file_with_no_markers_is_silent():
    assert inert('API_BASE = "https://api.stripe.com/v1"\n') == []


# ---- a reason under the floor is a marker that does nothing, silently ---------------------


def test_a_shrug_is_reported_as_inert():
    """A reason under the 30-character floor is ignored by every reader in the pack. SILENTLY
    is the problem: the author wrote a marker and believes the rule is now off."""
    source = header("file-length", "long")

    assert inert(source) == ["exemption-inert"]
    assert "30" in messages(source)[0]


# ---- the rule honours itself, like every detector that quotes its own patterns ------------


def test_a_file_may_excuse_itself_from_this_rule():
    """The pattern `standards_cors.py` and `standards_tls.py` already use: a file whose
    subject IS the marker syntax quotes inert markers as fixtures, not as decisions."""
    source = (
        header(
            "exemption-inert",
            "every marker quoted below is a fixture this detector is written against.",
        )
        + f"\n{MARKER} made-up-tag exempt -- a fixture, quoted so the detector has something to catch.\n"
    )

    assert inert(source) == []


# ---- a marker QUOTED is not a marker WRITTEN ----------------------------------------------


def test_an_example_quoted_deep_in_a_docstring_is_not_a_marker():
    """THE CALIBRATION CASE, and the one that decides whether this rule is usable.

    The modules that document the marker syntax quote it constantly -- `exemption_reason`
    shows the canonical `file-length` example, `standards_disclosure` shows the SQL form,
    every rules page shows its own. Sixty-six of the first draft's seventy-eight findings on
    this pack were prose of exactly that kind, which is a rule that gets baselined wholesale
    and then means nothing.

    The discriminator is not a guess about intent: it is whether a READER could ever look
    where the marker sits. `exemption_reason` bounds itself to the header window and
    `line_exemption_reason` to a comment block attached to a code line. A marker at line 300
    of a function docstring is in neither, so no rule was ever going to read it, and nobody
    wrote it expecting one to.
    """
    prose = (
        "def excuse(lines):\n"
        '    """The stated reason this file is exempt.\n'
        "\n"
        "    Write it in the file header, above the first line of code:\n"
        "\n"
        f"        {MARKER} file-lenght exempt -- declarative ORM schema; length tracks the\n"
        "        number of tables, not accumulated responsibility.\n"
        "\n"
        "    If you cannot write a reason you would defend in review, split the file.\n"
        '    """\n'
        "    return None\n"
    )

    assert inert(prose) == []


def test_a_marker_past_the_header_window_is_not_a_marker_either():
    """`exemption_reason` reads only the first HEADER_SCAN_LINES lines, so a marker below
    that is invisible to it by design -- and a long module docstring that goes on to DESCRIBE
    the marker is the ordinary reason one appears down there."""
    filler = ["A line of the module docstring." for _ in range(HEADER_SCAN_LINES + 5)]
    source = "\n".join(
        ['"""A module.', "", *filler, "", f"    {MARKER} made-up-tag exempt -- {A_REAL_REASON}", '"""', ""]
    )

    assert inert(source) == []


def test_a_marker_inside_a_string_literal_is_not_a_marker():
    """EVERY RULE WITH A HATCH NAMES IT IN ITS OWN MESSAGE, which is the whole point of the
    message -- `standards_docs`, `standards_query`, `standards_coverage`, `standards_userdocs`
    and `standards_disclosure` all end with "or mark 'standards: <tag> exempt -- <why>'".
    Those sit on CODE lines, so the line reader's own trailing-marker case reaches them, and
    the first draft reported all five. A rule that fires on the sentence telling you how to
    use it is not calibrated.

    A real trailing marker is in a comment; a quoted one is inside an unclosed string. That
    is the test, and it is applied only on code lines -- an apostrophe in a comment is prose,
    not a quote, and reading it as one would put the false positives back the other way.
    """
    source = (
        "def report(page):\n"
        "    return Violation(\n"
        '        message=("This page is unreachable. Link it, or mark "\n'
        f"                 \"'{MARKER} docs-orphan-page exempt -- <why>' in its header.\"),\n"
        "    )\n"
    )

    assert inert(source) == []


def test_a_real_trailing_marker_is_still_read():
    """The other side of the same test: a marker after a comment opener on a code line is
    exactly the shape `line_exemption_reason` documents, and must keep working."""
    source = f'SLUG = channel.slug or ""  # {MARKER} made-up-tag exempt -- {A_REAL_REASON}\n'

    assert inert(source) == ["exemption-inert"]


# ---- wired where the language dispatch cannot reach ---------------------------------------


def test_the_rule_reaches_a_file_type_the_language_dispatch_never_does(tmp_path):
    """Wired ABOVE the early returns in `check_source_file`, like mojibake and the credential
    rule and for their reason: a compose file, a workflow and a markdown page reach none of
    the language branches, and a stale marker in one of those is the likeliest of all --
    nobody re-reads a compose file looking for a sentence that stopped working.
    """
    compose = tmp_path / "docker-compose.yml"
    source = f"# {MARKER} made-up-tag exempt -- a reason long enough to clear the floor here.\nservices:\n"
    compose.write_text(source, encoding="utf-8")

    found = check_source_file(compose, source.splitlines(), CheckConfig(), {}, tmp_path)

    assert INERT_RULE in [violation.rule for violation in found]


# ---- the registry must not drift from what the rules actually consult ---------------------


# Call sites the derivation below cannot follow, because the tag arrives as a parameter or an
# attribute rather than as a literal. Each is declared with the tags that actually flow
# through it, read from its callers. A site NOT listed here FAILS the test rather than being
# quietly skipped -- skipping is what the first version did, and it under-reported four rules'
# hatches, which for a registry means calling working markers dead. Three of them were then
# reported as inert on this pack's own suite, and the "fix" nearly deleted them.
INDIRECT_DISPATCH: dict[tuple[str, str], set[str]] = {
    # `check_brace_language_jobs(path, lines, rule, message)` -- one caller, standards_jobs.
    ("standards_jobs_braces.py", "line_exemption_reason"): {"job-swallows-failure"},
    # `_finding(path, index, lines, rule, advice)` -- the test family's shared reporter,
    # which consults BOTH scopes for whichever of its three rules it was called for.
    ("standards_tests.py", "exemption_reason"): {
        "test-always-passes",
        "test-silently-skipped",
        "test-without-assertion",
    },
    ("standards_tests.py", "line_exemption_reason"): {
        "test-always-passes",
        "test-silently-skipped",
        "test-without-assertion",
    },
    # `toolchain.rule`, an attribute of the Toolchain each language module declares.
    ("standards_toolchain_consistency.py", "line_exemption_reason"): {
        "dotnet-consistency",
        "node-consistency",
        "php-consistency",
        "python-consistency",
    },
}


def _argument_list(text: str, opening: int) -> str:
    r"""The full argument text of a call whose `(` is at `opening`, brackets balanced.

    A plain `\(([^)]*)\)` stops at the first `)`, which inside
    `exemption_reason(read_lines(command), "docs-uncovered-command")` is the INNER one -- so
    the derivation read the argument as `read_lines(command` and declared that tag unread.
    """
    depth = 0
    for position in range(opening, len(text)):
        if text[position] == "(":
            depth += 1
        elif text[position] == ")":
            depth -= 1
            if depth == 0:
                return text[opening + 1 : position]
    return ""


def _tags_read_from_source() -> tuple[dict[str, set[str]], list[str]]:
    """Every tag the pack's modules pass to each exemption helper, and the sites it cannot read."""
    pack = Path(__file__).resolve().parent
    sources = [p for p in sorted(pack.glob("standards_*.py")) if not p.name.startswith("test_")]
    sources.append(pack / "check-source-limits.py")

    read_at: dict[str, set[str]] = {"exemption_reason": set(), "line_exemption_reason": set()}
    unresolved: list[str] = []

    for source in sources:
        # COMMENT LINES DROPPED, line numbers preserved. Prose quotes these helpers
        # constantly -- this module's registry comment names one, and so does every rule's
        # message -- and reading a comment back as a call site made the accounting check
        # below fail on its own explanation.
        #
        # `#` lines only, NOT `iter_code_lines`. That was the first attempt and it dropped
        # two real call sites: its block-comment state machine mistakes a `\"\"\"`-quoted regex
        # in the pack's own detectors for an unterminated docstring, and stops yielding at
        # line 253 of standards_client_address.py and 299 of standards_sqlinjection.py. A
        # marker QUOTED in a docstring is not a risk here anyway -- a call site is code.
        text = "\n".join(
            "" if line.lstrip().startswith("#") else line for line in source.read_text(encoding="utf-8").splitlines()
        )
        for helper, found in read_at.items():
            # `line_exemption_reason` ENDS with `exemption_reason`, so the file-scoped pattern
            # must refuse a `line_`-prefixed match or every line tag would land in both sets
            # and this check would pass while saying nothing.
            boundary = "" if helper.startswith("line_") else "(?<!line_)"
            for call in re.finditer(rf"{boundary}(?<![\w.]){helper}\s*\(", text):
                opening = call.end() - 1
                # The function's own `def` line is not a call site.
                if text.rfind("\n", 0, call.start()) + 1 == text.rfind("def ", 0, call.start()):
                    continue
                argument = _argument_list(text, opening).split(",")[-1].strip()
                if argument.startswith(("'", '"')):
                    found.add(argument.strip("'\""))
                    continue
                declared = re.search(rf'^{re.escape(argument)}\s*=\s*["\']([a-z][a-z0-9-]*)["\']', text, re.M)
                if declared:
                    found.add(declared.group(1))
                    continue

                site = (source.name, helper)
                if site in INDIRECT_DISPATCH:
                    found.update(INDIRECT_DISPATCH[site])
                else:
                    unresolved.append(
                        f"{source.name}:{text[: call.start()].count(chr(10)) + 1} {helper}(..., {argument})"
                    )

    return read_at, unresolved


def test_every_exemption_call_site_can_be_accounted_for():
    """A tag the derivation cannot follow must be DECLARED, never shrugged at.

    This is the check that makes the next one trustworthy. An unreadable call site used to be
    skipped, so the registry looked complete while missing four rules' hatches -- and a
    registry that under-reports does not fail safe: it reports working markers as dead, which
    is how this pack's own test-family exemptions came within one commit of being deleted.
    """
    _, unresolved = _tags_read_from_source()

    assert unresolved == [], (
        "an exemption helper is called with a tag this test cannot resolve. Add the site to "
        f"INDIRECT_DISPATCH with the tags that flow through it: {unresolved}"
    )


def test_the_registry_matches_what_the_packs_rules_actually_read():
    """THE ONE TEST THAT KEEPS THE OTHERS HONEST.

    Both frozensets are hand-written, and a hand-written list of what code does is exactly
    the claim this pack refuses elsewhere -- `collect_exemptions` was rewritten in the first
    place to stop asking about a hand-kept list of three rules. So the sets are checked
    against the source: every tag passed to `exemption_reason` must be in FILE_SCOPED_TAGS,
    every tag passed to `line_exemption_reason` in LINE_SCOPED_TAGS, and neither may name a
    tag no rule reads. A rule that grows a hatch and forgets the registry fails here, rather
    than shipping a marker the report calls inert and the rule quietly honours.
    """
    read_at, _ = _tags_read_from_source()

    assert read_at["exemption_reason"] == set(FILE_SCOPED_TAGS), (
        f"file scope drifted -- in the source but not the registry: "
        f"{sorted(read_at['exemption_reason'] - set(FILE_SCOPED_TAGS))}; "
        f"in the registry but not the source: {sorted(set(FILE_SCOPED_TAGS) - read_at['exemption_reason'])}"
    )
    assert read_at["line_exemption_reason"] == set(LINE_SCOPED_TAGS), (
        f"line scope drifted -- in the source but not the registry: "
        f"{sorted(read_at['line_exemption_reason'] - set(LINE_SCOPED_TAGS))}; "
        f"in the registry but not the source: {sorted(set(LINE_SCOPED_TAGS) - read_at['line_exemption_reason'])}"
    )


if __name__ == "__main__":
    from standards_selftest import run_module_tests

    sys.exit(run_module_tests(globals(), "exemption scope cases"))

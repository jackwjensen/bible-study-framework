"""Cases for the write-result-discarded rule.

standards: write-result-discarded exempt -- this suite's fixtures are literal discarded calls,
quoted so the detector can be tested against them, never writes this file performs.

THE CASE THAT MOTIVATED IT is `test_a_bare_statement_call_is_flagged`: Payvisia's handlers
called `Session.Write(...)` as a statement, then toasted success over a refused write. The
declared shape and the exemptions are Payvisia's own test, `NoCallerDiscardsTheResult`,
generalised to every language the scanner reads.

THE NEGATIVES ARE THE HARDER HALF. Every way of READING the answer puts something before the
call, and the two sanctioned non-reads -- an expression body after `=>`, and the explicit
`_ =` -- must pass, or the rule reports its own remedy. `test_real_source_stays_quiet` is the
half synthetic cases cannot do: it declares a call this pack makes everywhere and always
reads, and runs the rule over the pack's own source.

Run: python test_standards_write_results.py   (or pytest)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_config import CheckConfig, parse_must_read_results  # noqa: E402
from standards_dispatch import check_source_file  # noqa: E402
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402
from standards_write_results import RULE, check_write_results  # noqa: E402

DECLARED = ("Session.Write",)
EXEMPT = "a draft autosave; a refused one is retried by the next keystroke and nothing reads it"
assert len(EXEMPT) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


def rules(name: str, *lines: str, declared: tuple[str, ...] = DECLARED) -> list[str]:
    return [v.rule for v in check_write_results(Path(name), list(lines), declared)]


# ---- the finding -------------------------------------------------------------------------------


def test_a_bare_statement_call_is_flagged() -> None:
    """The Payvisia shape: the write, then success, whatever the store said."""
    assert rules(
        "Editor.razor",
        "    private void Save()",
        "    {",
        "        Session.Write(d => d.Notes.Add(note));",
        '        Toast.Success("Gemt");',
        "    }",
    ) == [RULE]


def test_every_receiver_spelling_payvisia_met_is_reached() -> None:
    """Field, property, `this.`, and a prefixed name -- the reach of `[\\w.]*[sS]ession`."""
    for call in (
        "session.Write(change);",
        "_session.Write(change);",
        "this.Session.Write(change);",
        "CurrentSession.Write(change);",
    ):
        assert rules("Page.razor.cs", "{", f"    {call}") == [RULE], call


def test_an_awaited_bare_call_is_still_discarded() -> None:
    """Awaiting the task reads the task, not the answer inside it."""
    assert rules("Page.cs", "{", "    await Session.Write(change);", declared=("Session.Write",)) == [RULE]


def test_the_method_name_is_matched_exactly() -> None:
    """`WriteAll` is a different method; only the declared one is must-read."""
    assert not rules("Page.cs", "{", "    Session.WriteAll(change);")


def test_the_message_names_the_remedy_and_the_rules_files() -> None:
    [violation] = check_write_results(Path("Page.cs"), ["{", "    Session.Write(change);"], DECLARED)
    assert "`_ = Session.Write(...)`" in violation.message
    assert "data-integrity.md" in violation.message


# ---- every read, and the two sanctioned non-reads ----------------------------------------------


def test_reading_the_answer_is_clean() -> None:
    for line in (
        "    if (!Session.Write(change)) return;",
        "    bool saved = Session.Write(change);",
        "    return Session.Write(change);",
        "    Assert.True(session.Write(_ => { }));",
    ):
        assert not rules("Page.cs", "{", line), line


def test_the_explicit_discard_is_clean() -> None:
    """`_ =` says "nothing afterwards depends on it", visibly -- Payvisia's sanctioned form."""
    assert not rules("Page.cs", "{", "    _ = Session.Write(change);")


def test_an_expression_body_on_the_next_line_is_clean() -> None:
    """The previous line ends in `=>`, so the call is the value the member returns."""
    assert not rules("Page.cs", "    public bool Save() =>", "        Session.Write(change);")


def test_a_call_continuing_an_open_argument_list_is_clean() -> None:
    """`Assert.True(` on its own line leaves the expression open; the call is its argument."""
    assert not rules("Tests.cs", "{", "    Assert.True(", "        session.Write(change));")


def test_a_statement_after_a_python_block_header_is_flagged() -> None:
    """`:` is NOT a continuation: after `if saved:` the next line is a statement."""
    assert rules("views.py", "if ready:", "    store.commit(batch)", declared=("store.commit",)) == [RULE]


def test_comments_and_undeclared_repos_are_silent() -> None:
    assert not rules("Page.cs", "{", "    // Session.Write(change);")
    assert not rules("Page.cs", "{", "    Session.Write(change);", declared=())


# ---- other languages ---------------------------------------------------------------------------


def test_php_arrow_receivers_are_reached() -> None:
    assert rules("Controller.php", "{", "    $this->session->write($row);", declared=("session.write",)) == [RULE]


def test_typescript_and_a_bare_function_name_are_reached() -> None:
    assert rules("save.ts", "{", "  await saveDraft(form);", declared=("saveDraft",)) == [RULE]
    assert not rules("save.ts", "{", "  void saveDraft(form);", declared=("saveDraft",))


# ---- exemptions --------------------------------------------------------------------------------


def test_a_line_marker_excuses_that_call_only() -> None:
    assert rules(
        "Page.cs",
        "{",
        f"    Session.Write(draft); // standards: {RULE} exempt -- {EXEMPT}",
        "    Session.Write(change);",
    ) == [RULE]


# ---- configuration and dispatch ----------------------------------------------------------------


def test_the_declaration_is_parsed_and_validated() -> None:
    assert parse_must_read_results(None) == ()
    assert parse_must_read_results(["Session.Write", "commit"]) == ("Session.Write", "commit")
    for malformed in (["Session.Write("], "Session.Write", [42]):
        try:
            parse_must_read_results(malformed)
        except SystemExit:
            continue
        raise AssertionError(f"accepted a malformed declaration: {malformed!r}")


def test_the_dispatcher_reaches_the_rule_from_a_declared_config() -> None:
    """Through check_source_file, so an unreachable rule fails here rather than going quiet."""
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        (root / ".standards.json").write_text(json.dumps({"mustReadResults": ["Session.Write"]}), encoding="utf-8")
        config = CheckConfig.load(root / ".standards.json")
        source = root / "Editor.razor"
        lines = ["@code {", "    void Save()", "    {", "        Session.Write(change);", "    }", "}"]
        found = [v.rule for v in check_source_file(source, lines, config, {}, root)]
        assert RULE in found
        assert RULE not in [v.rule for v in check_source_file(source, lines, CheckConfig(), {}, root)]


def test_real_source_stays_quiet() -> None:
    """Declare a call this pack makes in dozens of places and always reads -- zero findings.

    `json.loads` returns the parsed document and every caller in the pack uses it, so a
    finding here is the rule misreading ordinary code, not a discarded write.
    """
    pack = Path(__file__).resolve().parent
    findings = []
    for path in sorted(pack.rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        findings += list(check_write_results(path, lines, ("json.loads",)))
    assert not findings, [f"{v.path.name}:{v.line}" for v in findings]


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "write-result cases"))

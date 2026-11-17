#!/usr/bin/env python3
"""Tests for the rule-file governance family.

Plain asserts, no pytest, so it runs anywhere Python does:

    python engineering_standards/test_standards_rules.py

The three rules fail in different directions and the cases below are grouped that way. The
one that matters most is `budget_ignores_scoped_files`: if `paths:` parsing broke, every
scoped file would silently rejoin the always-load set, the budget would read high for a
reason nobody could see, and the natural response would be to raise the budget -- which is
the one outcome this whole family exists to prevent.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_config import CheckConfig  # noqa: E402
from standards_rules import always_loaded, check_rules, scope_of  # noqa: E402

FAILURES: list[str] = []

SCOPED = '---\npaths:\n  - "**/*.cs"\ndescription: scoped\n---\n\n# Scoped\n'
DECLARED = (
    "---\ndescription: always\n"
    "alwaysLoad: genuinely language-neutral because every language declares a name and a type\n"
    "---\n\n# Declared\n"
)
UNDECLARED = "# Undeclared\n\nNo frontmatter at all, so it loads everywhere and says nothing.\n"
THIN_REASON = "---\nalwaysLoad: because\n---\n\n# Thin\n"


def check(label: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(label)


def findings(files: dict[str, str], **tuning) -> list[str]:
    """Rule ids reported for a throwaway repo holding exactly these rule files."""
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        rules = root / "claude" / "rules"
        rules.mkdir(parents=True)
        for name, body in files.items():
            (rules / name).write_text(body, encoding="utf-8")
        config = CheckConfig(**tuning)
        return [violation.rule for violation in check_rules(root, config)]


# ---- scope_of, the primitive the other two depend on ----------------------------------------

check("scoped file reads as scoped", scope_of(SCOPED.splitlines()) == (True, None))
check(
    "declared file reads as always-load with a reason",
    scope_of(DECLARED.splitlines())[0] is False
    and (scope_of(DECLARED.splitlines())[1] or "").startswith("genuinely language-neutral"),
)
check("undeclared file reads as always-load with no reason", scope_of(UNDECLARED.splitlines()) == (False, None))
check("a scoped file is not always loaded", always_loaded(SCOPED.splitlines()) is False)
check("a declared file IS always loaded", always_loaded(DECLARED.splitlines()) is True)

# The real files use CRLF in at least one case (csharp-standards.md), and a parser that only
# handled LF would read the whole estate as undeclared.
check("CRLF frontmatter parses identically", scope_of(SCOPED.replace("\n", "\r\n").splitlines()) == (True, None))

# ---- rules-scope-declared -------------------------------------------------------------------

check("undeclared file is reported", findings({"a.md": UNDECLARED}) == ["rules-scope-declared"])
check("scoped file is silent", findings({"a.md": SCOPED}) == [])
check("declared file is silent", findings({"a.md": DECLARED}) == [])
check(
    "a reason under the 30-character floor is not a declaration",
    findings({"a.md": THIN_REASON}) == ["rules-scope-declared"],
)
# There is deliberately no exemption marker: alwaysLoad IS the exemption. A marker that
# worked here would let someone skip writing the reason, which is the whole mechanism.
check(
    "an exemption marker does NOT substitute for declaring scope",
    findings(
        {
            "a.md": "<!-- standards: rules-scope-declared exempt -- a perfectly good "
            "sounding reason that is long enough to clear the floor -->\n# X\n"
        }
    )
    == ["rules-scope-declared"],
)

# ---- rules-file-length ----------------------------------------------------------------------

LONG = DECLARED + "\n".join(f"line {n}" for n in range(200))
check("a rule file over the ceiling is reported", findings({"a.md": LONG}, rules_max_lines=50) == ["rules-file-length"])
check("under the ceiling is silent", findings({"a.md": LONG}, rules_max_lines=900) == [])
check(
    "an HTML-comment exemption in the header silences it",
    findings(
        {
            "a.md": DECLARED + "<!-- standards: rules-file-length exempt -- a lookup table whose length "
            "tracks the number of entries rather than accumulated subjects -->\n"
            + "\n".join(f"line {n}" for n in range(200))
        },
        rules_max_lines=50,
    )
    == [],
)

# ---- rules-context-budget -------------------------------------------------------------------

BULK = DECLARED + "\n".join(f"line {n}" for n in range(100))

check(
    "always-load files over the budget are reported once, for the set",
    findings({"a.md": BULK, "b.md": BULK}, rules_context_budget=50) == ["rules-context-budget"],
)

# The load-bearing one. Scoped files cost no session context, so they must not count.
# rules_max_lines is raised alongside the budget so this case tests ONE thing: a 500-line
# scoped file trips the per-file ceiling too, and a mixed result would not prove which rule
# stayed quiet for which reason.
check(
    "budget_ignores_scoped_files",
    findings(
        {"a.md": SCOPED + "\n".join(f"line {n}" for n in range(500))}, rules_context_budget=50, rules_max_lines=9000
    )
    == [],
)

check("under budget is silent", findings({"a.md": BULK}, rules_context_budget=5000) == [])

# An undeclared file is always-loaded, so it counts toward the budget as well as being
# reported for not declaring -- both findings, not one instead of the other.
check(
    "an undeclared file counts toward the budget too",
    sorted(findings({"a.md": UNDECLARED * 40}, rules_context_budget=10))
    == ["rules-context-budget", "rules-scope-declared"],
)

check("checkRules false silences the family", findings({"a.md": UNDECLARED}, check_rules=False) == [])


def both_shapes(name: str, body: str) -> list[str]:
    """Findings when the SAME rule file sits in both claude/rules and .claude/rules."""
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        for directory in ("claude/rules", ".claude/rules"):
            location = root / directory
            location.mkdir(parents=True)
            (location / name).write_text(body, encoding="utf-8")
        return [v.rule for v in check_rules(root, CheckConfig(rules_context_budget=150))]


# The same rule installed in both shapes is one rule, not two. Counting it twice inflates the
# budget by the size of the duplicate set and sends the reader hunting for prose to cut that
# does not exist -- whose likeliest end is raising the budget.
check("a file present in both directory shapes is counted once", both_shapes("a.md", BULK) == [])

check(
    "a repo with no rules directory is silent, not a crash",
    [v.rule for v in check_rules(Path(tempfile.gettempdir()) / "nope-not-here", CheckConfig())] == [],
)


def test_every_governance_case_holds() -> None:
    """Makes this module's coverage visible to pytest, and its passing checkable.

    The cases above run at module import and COLLECT into FAILURES rather than raising, so
    without this function pytest would collect zero tests from the file. That matters most
    for `budget_ignores_scoped_files`: if `paths:` parsing broke, every scoped file would
    rejoin the always-load set, the budget would read high for no visible reason, and the
    natural response would be to raise the budget -- the one outcome this family exists to
    prevent.
    """
    assert not FAILURES, "\n".join(FAILURES)


if __name__ == "__main__":
    if FAILURES:
        print(f"test_standards_rules: {len(FAILURES)} failure(s)\n")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("test_standards_rules: all cases pass")

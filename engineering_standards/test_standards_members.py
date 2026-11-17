"""The C# member rules: bool-prefix, money-not-decimal, and the date sentinels.

These had NO tests, which is why a whole shape of C# evaded them unnoticed. Both defects
below are the same mistake in two forms -- the regex assumed one declaration per line, and
that it began the line:

* `^\\s*` anchored the modifier to the start of the line, so a member declared inside a
  single-line class body (`public class P { public bool Active { get; set; } }`) was never
  seen at all.
* `.search()` returns the FIRST match, so a second declaration on the same line was ignored
  even once the anchor allowed it to match.

Neither produced an error. The scanner reported the file clean, which is the failure this
pack exists to prevent: the pack's own README says a regex that has never been exercised is
indistinguishable from a broken one, and this one had never been exercised.
"""

from __future__ import annotations

import sys
from pathlib import Path

from standards_checks import (
    check_csharp_dates,
    check_csharp_members,
    check_date_string_literals,
    check_python_dates,
    check_python_members,
    check_razor_var,
)
from standards_core import CheckConfig
from standards_dispatch import check_source_file
from standards_selftest import run_module_tests

# The fixtures are assembled by concatenation, following the idiom in
# test_standards_gate.py. The scanner reads THIS file as .py source, so writing
# `price: float`, `= date.min` or a sentinel date literally here would make the pack's own
# scanner report it -- the "prose explaining a rule contains an example of the thing it
# forbids" problem. Splitting the string across a `+` leaves the fixture intact and the
# pattern unmatched.
#
# EPOCH and ZERO_DATE joined the list on 2026-08-10, when `.py` was wired to
# `check_date_string_literals`. Until then this comment said the C# and Razor fixtures
# "need no such care: those rules only run on .cs, .razor, .php and .js" -- which stopped
# being true the moment the dispatch gap was closed, and the file promptly reported itself
# three times. A fixture is safe only while the rule it feeds does not read this language.
FLOAT = "float"
MIN = "min"
EPOCH = "1899-12-" + "30"
ZERO_DATE = "0000-00-" + "00"
EPOCH_YEAR = 1899


def members(source: str) -> list[str]:
    """The rule ids reported by the member checks, in order."""
    violations = check_csharp_members(Path("P.cs"), source.splitlines(), CheckConfig())
    return [violation.rule for violation in violations]


def member_names(source: str) -> list[str]:
    """The member name quoted in each message -- the thing the rule actually identified."""
    violations = check_csharp_members(Path("P.cs"), source.splitlines(), CheckConfig())
    return [violation.message.split("'")[1] for violation in violations]


def dates(source: str) -> list[str]:
    violations = check_csharp_dates(Path("P.cs"), source.splitlines())
    return [violation.rule for violation in violations]


# --------------------------------------------------------------------------------------
# bool-prefix
# --------------------------------------------------------------------------------------


def test_a_property_on_its_own_line_is_flagged() -> None:
    """The shape that always worked. Kept so a fix to the others cannot regress it."""
    source = "namespace S;\n\npublic class P\n{\n    public bool Active { get; set; }\n}\n"
    assert members(source) == ["bool-prefix"]


def test_a_property_in_a_single_line_class_body_is_flagged() -> None:
    """The reported bug: `^\\s*` required the modifier to start the line."""
    source = "namespace S;\npublic class P { public bool Active { get; set; } }\n"
    assert members(source) == ["bool-prefix"]


def test_both_properties_on_one_line_are_flagged() -> None:
    """`.search()` stops at the first match, so the second member was invisible."""
    source = "public class P { public bool Active { get; set; } public bool Done { get; set; } }\n"
    assert member_names(source) == ["Active", "Done"]


def test_a_prefixed_property_is_not_flagged() -> None:
    source = "public class P { public bool IsActive { get; set; } }\n"
    assert members(source) == []


def test_a_mix_reports_only_the_unprefixed_one() -> None:
    source = "public class P { public bool IsActive { get; set; } public bool Done { get; set; } }\n"
    assert member_names(source) == ["Done"]


def test_an_expression_bodied_property_is_flagged() -> None:
    source = "public class P { public bool Active => true; }\n"
    assert members(source) == ["bool-prefix"]


def test_a_local_variable_is_not_a_member() -> None:
    """No accessibility modifier, so the rule must not reach into method bodies."""
    source = "public class P { void M() { bool active = true; } }\n"
    assert members(source) == []


# --------------------------------------------------------------------------------------
# money-not-decimal -- the sibling with the identical assumption
# --------------------------------------------------------------------------------------


def test_money_in_a_single_line_class_body_is_flagged() -> None:
    source = "public class P { public double Price { get; set; } }\n"
    assert members(source) == ["money-not-decimal"]


def test_both_money_members_on_one_line_are_flagged() -> None:
    source = "public class P { public double Price; public float TotalAmount; }\n"
    assert member_names(source) == ["Price", "TotalAmount"]


def test_a_non_money_double_is_not_flagged() -> None:
    source = "public class P { public double Latitude { get; set; } }\n"
    assert members(source) == []


def test_a_bool_and_a_money_member_on_one_line_are_both_flagged() -> None:
    """The two rules run over the same line independently; neither may mask the other."""
    source = "public class P { public bool Active { get; set; } public double Price; }\n"
    assert sorted(members(source)) == ["bool-prefix", "money-not-decimal"]


# --------------------------------------------------------------------------------------
# money-precision -- decimal is necessary and not sufficient
#
# money-not-decimal gets you off double. This gets you off the PROVIDER'S OPINION. It exists
# because of what InvoTrack's 2026-08-24 Pomelo -> Oracle swap turned up: thirteen [Column]
# decimals had never declared a SQL type, so Pomelo had been silently choosing
# decimal(65,30) for them while every newer property in the same codebase declared
# decimal(18,2). Nothing was wrong at run time and nothing had ever reported it -- the
# difference only became visible when a second provider disagreed about the default.
# --------------------------------------------------------------------------------------


def test_a_column_decimal_without_a_type_is_flagged() -> None:
    source = '    [Column("t_invoices_Total")]\n    public decimal Total { get; set; }\n'
    assert members(source) == ["money-precision"]


def test_a_money_column_with_scale_zero_is_flagged() -> None:
    """decimal(18,0) stores no minor units -- 99.50 becomes 100, silently. An external audit
    found exactly this on a subscription price. Only money-named, so a count is left alone."""
    source = '    [Column("t_plans_Price", TypeName = "decimal(18,0)")]\n    public decimal Price { get; set; }\n'
    assert members(source) == ["money-precision"]


def test_a_non_money_column_with_scale_zero_is_clean() -> None:
    """A scale-0 decimal is legitimate for a whole-number quantity stored as decimal."""
    source = (
        '    [Column("t_orders_Quantity", TypeName = "decimal(18,0)")]\n    public decimal Quantity { get; set; }\n'
    )
    assert members(source) == []


def test_a_money_column_with_scale_two_is_clean() -> None:
    source = '    [Column("t_plans_Price", TypeName = "decimal(18,2)")]\n    public decimal Price { get; set; }\n'
    assert members(source) == []


def test_a_column_decimal_with_a_type_is_clean() -> None:
    source = '    [Column("t_products_Price", TypeName = "decimal(18,2)")]\n    public decimal Price { get; set; }\n'
    assert members(source) == []


def test_a_nullable_column_decimal_without_a_type_is_flagged() -> None:
    """Nullability says nothing about precision, and the estate's rate columns are nullable."""
    source = '    [Column("t_employees_HourlyRate")]\n    public decimal? HourlyRate { get; set; }\n'
    assert members(source) == ["money-precision"]


def test_an_intervening_attribute_does_not_hide_the_column() -> None:
    """[Required] above [Column] is ordinary in this estate; the lookback must walk past it."""
    source = '    [Required]\n    [Column("t_time_log_BillingHours")]\n    public decimal BillingHours { get; set; }\n'
    assert members(source) == ["money-precision"]


def test_a_not_mapped_decimal_is_clean() -> None:
    """A computed property has no column, so it has no precision to get wrong."""
    source = '    [Column("x")]\n    [NotMapped]\n    public decimal Computed { get; set; }\n'
    assert members(source) == []


def test_a_decimal_with_no_column_attribute_is_left_alone() -> None:
    """Deliberately quiet: it may be configured fluently in OnModelCreating, which a
    line scanner cannot see. The rule fires only where [Column] proves the mapping."""
    source = "    public decimal Whatever { get; set; }\n"
    assert members(source) == []


def test_a_decimal_local_is_not_a_column() -> None:
    """The qualifier requirement is what keeps method bodies out -- same guard as bool."""
    source = "public class P { void M() { decimal total = 0; } }\n"
    assert members(source) == []


def test_a_non_decimal_column_is_not_flagged() -> None:
    source = '    [Column("t_x_Days")]\n    public int Days { get; set; }\n'
    assert members(source) == []


# --------------------------------------------------------------------------------------
# date-sentinel / date-out-of-range -- no anchor bug, but the same one-per-line stop
# --------------------------------------------------------------------------------------


def test_both_date_sentinels_on_one_line_are_flagged() -> None:
    source = "void M() { From = DateTime.MinValue; To = DateTime.MaxValue; }\n"
    assert dates(source) == ["date-sentinel", "date-sentinel"]


def test_both_out_of_range_literals_on_one_line_are_flagged() -> None:
    source = "void M() { A(new DateTime(1, 1, 1)); B(new DateTime(2, 1, 1)); }\n"
    assert dates(source) == ["date-out-of-range", "date-out-of-range"]


def test_a_plausible_date_literal_is_not_flagged() -> None:
    source = "void M() { A(new DateTime(2026, 1, 1)); }\n"
    assert dates(source) == []


# --------------------------------------------------------------------------------------
# Current scope, documented so a change to it is a decision rather than a surprise
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# The same assumption in the other languages
# --------------------------------------------------------------------------------------


def python_members(source: str) -> list[str]:
    violations = check_python_members(Path("m.py"), source.splitlines(), CheckConfig())
    return [violation.message.split("'")[1] for violation in violations]


def test_every_money_annotation_in_a_signature_is_flagged() -> None:
    """Python is where the multiple-per-line shape is not an edge case: a signature carrying
    several annotations is simply how the language is written. `search` reported one."""
    source = f"def charge(price: {FLOAT}, cost: {FLOAT}) -> None: ...\n"
    assert python_members(source) == ["price", "cost"]


def test_a_non_money_annotation_is_not_flagged() -> None:
    source = f"def scale(ratio: {FLOAT}, weight: {FLOAT}) -> None: ...\n"
    assert python_members(source) == []


def test_both_python_date_sentinels_on_one_line_are_flagged() -> None:
    # `check_python_dates`, not `check_python_members`: the date rules moved to
    # standards_dates.py on 2026-08-10, when standards_checks.py crossed the file-length
    # limit and dates were the last rule family still inlined in the dispatcher.
    source = f"start = date.{MIN}; end = date.{'max'}\n"
    violations = check_python_dates(Path("m.py"), source.splitlines())
    assert [v.rule for v in violations] == ["date-sentinel", "date-sentinel"]


def test_a_python_date_constructor_with_an_implausible_year_is_flagged() -> None:
    """The constructor form, which the quoted-string rule cannot see. C# has had this
    since the beginning; Python had only the `.min`/`.max` sentinel."""
    # The year is interpolated rather than written: this file is .py, so a literal
    # `date(1899, …)` here would make the constructor rule report its own test.
    violations = check_python_dates(Path("m.py"), [f"start = date({EPOCH_YEAR}, 12, 30)"])
    assert [v.rule for v in violations] == ["date-out-of-range"]


def test_a_qualified_python_date_sentinel_is_flagged() -> None:
    """`import datetime; x = datetime.date.min` -- the spelling the receiver-anchored
    pattern was blind to, and at least as common as the bare-import one."""
    violations = check_python_dates(Path("m.py"), [f"start = datetime.date.{MIN}"])
    assert [v.rule for v in violations] == ["date-sentinel"]


def test_both_var_declarations_in_a_razor_line_are_flagged() -> None:
    violations = check_razor_var(Path("C.razor"), ["    var first = 1; var second = 2;"])
    assert [v.message.split("'")[1] for v in violations] == ["first", "second"]


def test_an_anonymous_type_still_exempts_the_whole_razor_line() -> None:
    """Conservative on purpose: `var` is mandatory for an anonymous type, and pairing each
    `var` with its own initialiser is beyond a regex. Under-reporting is the safe direction
    for the largest baselined rule in the estate."""
    violations = check_razor_var(Path("C.razor"), ["    var a = new { X = 1 }; var b = 2;"])
    assert list(violations) == []


def test_prose_containing_the_word_var_is_not_a_declaration() -> None:
    """Danish exposed this: "var" is the past tense of "to be", so a lawnote reading "hvis
    faktorerne var dokumenteret" was reported as a variable named `dokumenteret`. C# has no
    bare `var x;` -- a declaration always carries an initialiser -- so requiring one is more
    precise rather than looser."""
    violations = check_razor_var(
        Path("C.razor"),
        [
            "        Det, der ville stå tilbage, hvis faktorerne var dokumenteret.",
            "                <strong>Hvorfor 5 % er tallet</strong> Grænsen var lavere før.",
        ],
    )
    assert list(violations) == []


def test_every_legal_shape_of_a_razor_var_is_still_flagged() -> None:
    """The tightening must not cost coverage: each line below is a real declaration."""
    lines = [
        "    var total = Invoices.Sum(i => i.Amount);",
        "    @foreach (var invoice in Invoices)",
        "    for (var index = 0; index < 10; index++)",
        "    using (var scope = Services.CreateScope())",
        "    if (Lookup.TryGetValue(key, out var found))",
        "    if (candidate is var matched)",
        "    var (first, second) = Pair;",
    ]
    for line in lines:
        assert list(check_razor_var(Path("C.razor"), [line])), line


def test_every_out_of_range_string_literal_on_a_line_is_flagged() -> None:
    """The bad rows arrive as a list on one line, which is where search stopped."""
    violations = check_date_string_literals(Path("s.php"), [f"$rows = ['{ZERO_DATE}', '{EPOCH}'];"])
    assert [v.rule for v in violations] == ["date-out-of-range", "date-out-of-range"]


def test_a_plausible_string_literal_is_not_flagged() -> None:
    violations = check_date_string_literals(Path("s.php"), ["$rows = ['2026-01-01'];"])
    assert list(violations) == []


# --------------------------------------------------------------------------------------
# The DISPATCH, not the checks. Every test above calls a check function directly, so all of
# them passed while `.py` was never wired to the date rule at all -- the check worked and
# nothing reached it. A rule that exists but is not dispatched for a language is invisible
# to a unit test of the rule, which is exactly how this one survived: identical source fired
# in .cs, .php and .ts and was silent in .py.
# --------------------------------------------------------------------------------------


def _dispatched_rules(name: str, source: str) -> list[str]:
    """Rule ids `check_source_file` yields for this file -- the real entry point."""
    return sorted(v.rule for v in check_source_file(Path(name), source.splitlines(), CheckConfig(), {}, Path(".")))


def test_an_out_of_range_date_is_dispatched_for_every_language_that_can_hold_one() -> None:
    epoch = EPOCH
    for name, source in (
        ("m.cs", f'var start = "{epoch}";'),
        ("m.php", f"<?php\ndeclare(strict_types=1);\n$start = '{epoch}';"),
        ("m.ts", f"const start = '{epoch}';"),
        ("m.py", f'start = "{epoch}"'),
    ):
        assert "date-out-of-range" in _dispatched_rules(name, source), name


# --------------------------------------------------------------------------------------
# Accessibility is not part of either rule (decided 2026-08-10)
# --------------------------------------------------------------------------------------


def test_a_private_bool_property_is_flagged() -> None:
    """The bool regex listed `private protected` but not plain `private`, so a private
    boolean was exempt from a rule its money sibling already applied to itself."""
    source = "public class P { private bool Active { get; set; } }\n"
    assert members(source) == ["bool-prefix"]


def test_a_bool_field_is_flagged() -> None:
    """Fields are where private booleans actually live -- the terminator set had only
    `{` and `=>`, so every `private bool _loading;` in the estate was invisible."""
    source = "public class P { private bool _loading; }\n"
    assert members(source) == ["bool-prefix"]


def test_a_nullable_bool_field_is_flagged() -> None:
    source = "public class P { private bool? _ongoingFilter; }\n"
    assert members(source) == ["bool-prefix"]


def test_a_bool_local_is_not_flagged() -> None:
    """The reason bool requires at least one qualifier. A regex cannot distinguish a private
    field from a local, and every bool local in the estate would otherwise be a finding."""
    source = "public class P { void M() { bool active = true; bool done = false; } }\n"
    assert members(source) == []


def test_a_bool_returning_method_is_not_flagged() -> None:
    source = "public class P { private bool MatchesFilters(Job job) => true; }\n"
    assert members(source) == []


# --------------------------------------------------------------------------------------
# The prefix predicate, in the casings C# actually uses
# --------------------------------------------------------------------------------------


def test_an_underscored_camel_case_prefix_counts() -> None:
    """InvoTrack names private fields `_camelCase`, so `startswith(("Is",...))` rejected
    `_isFormValid` -- a correctly-named field reported as a violation."""
    source = "public class P { private bool _isFormValid; private bool _isSoleProprietor; }\n"
    assert members(source) == []


def test_a_bare_camel_case_prefix_counts() -> None:
    """DonorLink names them `camelCase`. Measured across both repos, 88 correctly-named
    members would have been flagged the moment the rule stopped being public-only."""
    source = "public class P { private bool canManage; private bool hasAccess; }\n"
    assert members(source) == []


def test_a_word_that_merely_starts_with_a_prefix_is_still_flagged() -> None:
    """The false-negative half: `"Issued".startswith("Is")` is True, so `Issued`, `Issuing`
    and `Cancelled` all passed while asserting nothing. Requiring the next character to be
    uppercase is what separates the prefix `Is` + `Active` from the word `Issued`."""
    source = "public class P { public bool Issued { get; set; } public bool Cancelled; }\n"
    assert member_names(source) == ["Issued", "Cancelled"]


def test_a_digit_or_underscore_after_the_prefix_counts() -> None:
    """`is2faEnabled` is real DonorLink code, and `Is_Active` is legal C#."""
    source = "public class P { private bool is2faEnabled; private bool Is_Active; }\n"
    assert members(source) == []


# --------------------------------------------------------------------------------------
# Money reaches locals and parameters, because that is where the bug lives
# --------------------------------------------------------------------------------------


def test_a_money_local_is_flagged() -> None:
    """`double total = 0;` accumulating invoice lines is the textbook money bug, and it
    carries no modifier at all -- so the rule could never see it."""
    source = "void M() { double totalAmount = 0; }\n"
    assert members(source) == ["money-not-decimal"]


def test_every_money_parameter_is_flagged() -> None:
    source = "void Charge(double amount, float fee) { }\n"
    assert member_names(source) == ["amount", "fee"]


def test_a_non_money_local_is_not_flagged() -> None:
    """The name is what makes reaching into method bodies safe here."""
    source = "void M() { double ratio = 1.0; double latitude = 2.0; }\n"
    assert members(source) == []


def test_a_money_returning_method_is_not_flagged() -> None:
    """A `decimal` return type is the fix; the method NAME is not the declaration."""
    source = "public class P { public double GetTotalAmount() => 0; }\n"
    assert members(source) == []


def test_a_money_cast_is_not_flagged() -> None:
    source = "void M() { Use((double)price); }\n"
    assert members(source) == []


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "member/date/var cases"))

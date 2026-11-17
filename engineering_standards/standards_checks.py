#!/usr/bin/env python3
"""The standards checks themselves: what counts as a violation in each language.

One tool, five files, each changing for its own reason:

    standards_core.py       what a violation IS, which files are in scope, file length
    standards_checks.py     what each LANGUAGE looks like
    standards_dispatch.py   which rules run against which file (split out 2026-08-25)
    standards_razor.py      razor-var: the one rule reading markup and C# at once
    standards_sentinels.py  zero-like sentinels: unset-not-zero and payload-default
    check-source-limits.py  the driver: walking, the baseline ratchet, reporting, CLI

The core names are re-exported below so `from standards_checks import CheckConfig` keeps
working for the driver.

Rules here: mojibake (via standards_encoding), bool-prefix, money-not-decimal, date-sentinel,
date-out-of-range, php-strict-types. razor-var lives in standards_razor.py and unset-not-zero and payload-default live in
standards_sentinels.py; all three are re-exported for the dispatcher.

Source of truth: engineering-standards/engineering_standards/standards_checks.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from standards_core import (  # noqa: F401  (CheckConfig/Violation re-exported for the driver)
    BOOLEAN_PREFIXES,
    MIN_PLAUSIBLE_YEAR,
    CheckConfig,
    Violation,
    check_file_length,
    has_boolean_prefix,
    is_money_name,
    iter_code_lines,
)
from standards_dates import (  # noqa: F401  (re-exported: callers import them from here)
    check_csharp_dates,
    check_date_string_literals,
    check_js_dates,
    check_python_dates,
)
from standards_deploy import check_compose_read_only  # noqa: F401  (re-exported for the dispatcher)
from standards_ef_provider import check_ef_provider_support  # noqa: F401  (re-exported for the dispatcher)
from standards_encoding import check_mojibake  # noqa: F401  (re-exported for the dispatcher)
from standards_injection import check_js_eval_interop, check_php_eval  # noqa: F401  (re-exported for the dispatcher)
from standards_layout import check_generic_filename  # noqa: F401  (re-exported for the dispatcher)
from standards_node_support import check_node_runtime_support  # noqa: F401
from standards_query import check_filter_in_memory, check_query_shape  # noqa: F401  (re-exported)
from standards_razor import (  # noqa: F401  (re-exported: the dispatcher and callers import it here)
    ANONYMOUS_TYPE_MARKER,
    RAZOR_VAR_DECLARATION,
    check_razor_var,
)
from standards_scope import (  # noqa: F401  (should_check is re-exported)
    DOCKERFILE_FILENAME,
    NVMRC_FILENAME,
    PROJECT_FILENAME,
    SCRIPT_SUFFIXES,
    should_check,
)
from standards_versions import (  # noqa: F401  (re-exported for the dispatcher)
    check_action_versions,
    check_ci_toolchain,
    check_dotnet_runtime_support,
)

# public/protected/internal bool Foo { get; set; }  -- captures the member name.
# A member declaration starts either at the beginning of the line or straight after a `{`,
# `}` or `;` -- which is what makes a single-line class body reachable:
#
#     public class P { public bool Active { get; set; } public bool Done { get; set; } }
#
# MEMBER_START must be a LOOKBEHIND, not a consumed character class. The boundary before the
# second declaration is the `}` (or `;`) that ENDS the first one, and finditer resumes at the
# end of the previous match -- so a consuming `[{};]` has already eaten the only boundary the
# next match could have used, and every declaration after the first stays invisible. That is
# the same bug in a new place, so it is spelled out here rather than left to be rediscovered.
#
# `^` is zero-width and the lookbehind is fixed-width, so alternating them is legal; a single
# lookbehind mixing the two widths would not be.
MEMBER_START = r"(?:^|(?<=[{};]))\s*"

# Money also has to be reachable inside a parameter list, so `(` and `,` are boundaries too.
# A cast is not caught by this and does not need to be: `(double)price` has no whitespace
# and no declared name after the type.
VALUE_START = r"(?:^|(?<=[{};(,]))\s*"

# Every modifier that can precede a member declaration, matched one word at a time rather
# than as hand-written combinations. The old spelling enumerated pairs (`private protected`,
# `protected internal`) and, in the bool rule, silently omitted plain `private` -- so a
# private boolean was exempt from a rule its own money sibling applied to. Matching word by
# word makes any legal ordering work and cannot develop that kind of hole.
CSHARP_QUALIFIER = (
    r"(?:public|protected|internal|private|static|readonly|virtual|override|abstract"
    r"|sealed|required|new|volatile|const|extern|partial)"
)

# ACCESSIBILITY IS NOT PART OF EITHER RULE (decided 2026-08-10). A `double` rounds the same
# whoever can see it, and a bare-adjective boolean is as ambiguous in a private field as in a
# public property -- the reader of a class body reads both. A mechanical rule that applied to
# only half the declarations read as arbitrary, and the same name was legal as a field and
# illegal once promoted to a property.
#
# What the two rules do NOT share is whether a qualifier is required at all, and that
# difference is load-bearing:
#
# * bool requires AT LEAST ONE qualifier, which is what keeps method-body locals out. A regex
#   cannot tell `private bool active;` from a local `bool active = true;`, and every bool
#   local in the estate would otherwise be a finding -- noise that would bury the rule.
# * money makes them OPTIONAL, because locals and parameters are exactly where the bug lives:
#   `double total = 0;` accumulating invoice lines is the textbook case, and it carries no
#   modifier. The `is_money_name` filter is what makes that safe -- it is the name, not the
#   modifier, doing the discriminating.
#
# Neither matches a method: a `(` after the name is not in either tail set.
CSHARP_BOOL_MEMBER = re.compile(
    MEMBER_START + rf"(?:{CSHARP_QUALIFIER}\s+)+"
    r"bool\??\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\{|=>|=|;)"
)

CSHARP_FLOAT_MEMBER = re.compile(
    VALUE_START + rf"(?:{CSHARP_QUALIFIER}\s+)*"
    r"(?P<type>double|float)\??\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\{|=>|=|;|,|\))"
)

# A mapped `decimal` property. Requires a qualifier (like bool, unlike float): this is about
# COLUMNS, so a method-body local is not a candidate.
CSHARP_DECIMAL_MEMBER = re.compile(
    MEMBER_START + rf"(?:{CSHARP_QUALIFIER}\s+)+"
    r"decimal\??\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\{|=>|=|;)"
)

# `[Column("t_invoices_Total")]` -- and whether it declares the SQL type.
CSHARP_COLUMN_ATTRIBUTE = re.compile(r"\[\s*Column\s*\(")
CSHARP_COLUMN_TYPE_NAME = re.compile(r"TypeName\s*=")
CSHARP_NOT_MAPPED = re.compile(r"\[\s*NotMapped\s*[\]\(]")
# `TypeName = "decimal(18,2)"` -- captures the scale so a money column declared with too few
# decimal places is caught. `decimal(18,0)` stores no minor units at all: a 99.50 subscription
# price rounds to 100, silently, which an external audit found shipped in production.
CSHARP_DECIMAL_TYPENAME = re.compile(
    r"""TypeName\s*=\s*["']\s*decimal\s*\(\s*\d+\s*,\s*(?P<scale>\d+)\s*\)\s*["']""",
    re.IGNORECASE,
)

# Python annotated assignment or parameter: name: float
PYTHON_FLOAT_ANNOTATION = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:Optional\[)?float\b")

# A PHP typed property or parameter declared float: `public float $totalPrice`,
# `private ?float $fee`, `function charge(float $amount)`.
#
# PHP is included where TypeScript deliberately is NOT, and the difference is that PHP has
# somewhere to go: php-standards.md names integer minor units or bcmath and says outright
# "Never `float` for a money column, a money property, or a running total". TS has no
# alternative primitive, so its money rule is about not COMPUTING client-side rather than
# about the type, and a type-shaped rule there would have nothing to demand.
PHP_FLOAT_DECLARATION = re.compile(
    r"(?:^|[({,;]|\b(?:public|protected|private|static|readonly|var)\s+)\s*"
    r"\??float\s+\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)

# `declare(strict_types=1)`, which PHP requires to be the file's first statement.
PHP_STRICT_TYPES = re.compile(r"declare\s*\(\s*strict_types\s*=\s*1\s*\)")

# The zero-like sentinel family (unset-not-zero, payload-default and the per-language
# coalesce patterns) lives in standards_sentinels.py. Re-exported so the dispatcher below
# and any caller importing from standards_checks keep working unchanged.
#
# F401 as well as E402 on every one of these: they are re-exports, so nothing in this file
# reads them, and ruff's F401 autofix DELETED all three blocks on 2026-09-02 -- taking
# thirteen suites down with them and leaving this comment describing imports that were no
# longer there. E402 alone says "not at the top on purpose"; it does not say "used".
from standards_compose import (  # noqa: E402,F401,I001  (kept beside the other rule definitions)
    COMPOSE_FILENAME,
    ENV_EXAMPLE_FILENAME,
    check_compose_container_names,
    check_compose_ports,
    check_production_ports,
    check_deploy_gate,
    check_env_example,
    check_overlay_name,
    is_workflow,
)
from standards_deploy import (  # noqa: E402,F401  (kept beside the other rule definitions)
    check_container_user,
    check_deploy_host_key,
    check_deploy_pull,
    check_deploy_ssh_user,
)
from standards_sentinels import (  # noqa: E402,F401  (kept beside the other rule definitions)
    CSHARP_ZERO_COALESCE,
    JS_ZERO_COALESCE,
    PHP_ZERO_COALESCE,
    PYTHON_ZERO_COALESCE,
    check_payload_default,
    check_zero_coalesce,
)


def column_attribute_without_type(lines: list[str], line_number: int) -> bool:
    """True when the attributes directly above this member map a column but declare no type.

    The lookback walks upward only over contiguous attribute lines, and is conservative both
    ways: no `[Column]` at all is left alone (it may be configured fluently in OnModelCreating,
    which this cannot see), and `[NotMapped]` short-circuits. So it fires on exactly one
    unambiguous shape -- somebody said "this is a column" and did not say what kind.
    """
    index = line_number - 2  # line_number is 1-based; start on the line above the member
    saw_column_without_type = False
    while index >= 0:
        stripped = lines[index].strip()
        if not stripped.startswith("["):
            break
        if CSHARP_NOT_MAPPED.search(stripped):
            return False
        if CSHARP_COLUMN_ATTRIBUTE.search(stripped):
            if CSHARP_COLUMN_TYPE_NAME.search(stripped):
                return False
            saw_column_without_type = True
        index -= 1
    return saw_column_without_type


def column_decimal_scale(lines: list[str], line_number: int) -> Optional[int]:
    """The scale of a `TypeName = "decimal(p,s)"` on the attributes above this member, if any.

    Walks the same contiguous attribute block as `column_attribute_without_type`, and returns
    None when no decimal TypeName is declared -- that case is the OTHER money-precision path
    (no type at all), handled separately.
    """
    index = line_number - 2
    while index >= 0:
        stripped = lines[index].strip()
        if not stripped.startswith("["):
            break
        found = CSHARP_DECIMAL_TYPENAME.search(stripped)
        if found:
            return int(found.group("scale"))
        index -= 1
    return None


def _csharp_boolean_findings(path: Path, line_number: int, line: str) -> Iterable[Violation]:
    """bool-prefix: a boolean member whose name does not assert anything."""
    for bool_match in CSHARP_BOOL_MEMBER.finditer(line):
        if has_boolean_prefix(bool_match.group("name")):
            continue
        yield Violation(
            path=path,
            line=line_number,
            rule="bool-prefix",
            message=(
                f"Boolean member '{bool_match.group('name')}' must start with "
                f"{'/'.join(BOOLEAN_PREFIXES)} -- bare adjectives are ambiguous."
            ),
        )


def _csharp_money_findings(path: Path, lines: list[str], line_number: int, line: str) -> Iterable[Violation]:
    """money-not-decimal and the decimal-scale half: a money value stored as a float, and
    a mapped decimal column whose declared scale cannot hold minor units."""
    for money_match in CSHARP_FLOAT_MEMBER.finditer(line):
        if not is_money_name(money_match.group("name")):
            continue
        yield Violation(
            path=path,
            line=line_number,
            rule="money-not-decimal",
            message=(
                f"'{money_match.group('name')}' holds money as "
                f"{money_match.group('type')}. Use decimal -- binary floating point "
                f"accumulates rounding errors across invoices."
            ),
        )

    for decimal_match in CSHARP_DECIMAL_MEMBER.finditer(line):
        name = decimal_match.group("name")
        if column_attribute_without_type(lines, line_number):
            yield Violation(
                path=path,
                line=line_number,
                rule="money-precision",
                message=(
                    f"'{name}' is mapped with [Column] but declares "
                    f"no SQL type, so the provider's default decides its precision -- and "
                    f"they disagree: Pomelo gave decimal(65,30), Oracle's gives "
                    f'decimal(18,2). Declare it: TypeName = "decimal(18,2)". Use the '
                    f"scale the value needs: 2 for money and hours, 4 for per-unit rates."
                ),
            )
            continue
        # It DOES declare a type -- but a money column with too few decimal places is a
        # different defect the "no type" path never sees. decimal(18,0) holds no minor
        # units: a 99.50 price becomes 100, silently. Only money-named columns, because
        # a scale-0 decimal is legitimate for a count or an id stored as decimal.
        scale = column_decimal_scale(lines, line_number)
        if scale is not None and scale < 2 and is_money_name(name):
            yield Violation(
                path=path,
                line=line_number,
                rule="money-precision",
                message=(
                    f"'{name}' holds money but its column is decimal(_,{scale}), which "
                    f"stores no minor units -- 99.50 rounds to 100 on the way in, "
                    f"silently. Money needs at least 2 decimal places: "
                    f'TypeName = "decimal(18,2)".'
                ),
            )


def check_csharp_members(path: Path, lines: list[str], config: CheckConfig) -> Iterable[Violation]:
    # finditer, not search: a single-line class body holds several declarations, and search
    # reports only the first -- so the second member was silently exempt from every rule.
    for line_number, line in iter_code_lines(lines, path.suffix):
        if config.check_boolean_prefixes:
            yield from _csharp_boolean_findings(path, line_number, line)
        if config.check_money_types:
            yield from _csharp_money_findings(path, lines, line_number, line)


def check_python_members(path: Path, lines: list[str], config: CheckConfig) -> Iterable[Violation]:
    # finditer throughout: a function signature routinely carries several annotations on one
    # line -- `def charge(amount: float, fee: float)` is two money findings, and search saw
    # one. This is the same defect as the C# member rules, in the language where the
    # multiple-per-line shape is not an edge case but the normal way code is written.
    for line_number, line in iter_code_lines(lines, path.suffix):
        if config.check_money_types:
            for money_match in PYTHON_FLOAT_ANNOTATION.finditer(line):
                if not is_money_name(money_match.group("name")):
                    continue
                yield Violation(
                    path=path,
                    line=line_number,
                    rule="money-not-decimal",
                    message=(f"'{money_match.group('name')}' is annotated float but holds money. Use decimal.Decimal."),
                )


def check_php_members(path: Path, lines: list[str], config: CheckConfig) -> Iterable[Violation]:
    """Money declared as `float` -- the PHP sibling of the C# and Python member rules."""
    if not config.check_money_types:
        return

    for line_number, line in iter_code_lines(lines, path.suffix):
        for money_match in PHP_FLOAT_DECLARATION.finditer(line):
            if not is_money_name(money_match.group("name")):
                continue
            yield Violation(
                path=path,
                line=line_number,
                rule="money-not-decimal",
                message=(
                    f"'${money_match.group('name')}' holds money as float. PHP has no decimal "
                    f"type, so use integer minor units (ore/cents) or bcmath -- "
                    f"0.1 + 0.2 !== 0.3 here as everywhere, and round() does not undo it."
                ),
            )


def check_php_strict_types(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Require `declare(strict_types=1)` in the files that can legally carry it.

    A template partial opens with HTML, and PHP demands the declare be the file's FIRST
    statement -- so those files genuinely cannot comply and are exempt. Only files whose
    first non-blank line opens PHP are checked.
    """
    first_code_line = next((line.strip() for line in lines if line.strip()), "")
    if not first_code_line.startswith("<?php"):
        return

    if any(PHP_STRICT_TYPES.search(line) for line in lines):
        return

    yield Violation(
        path=path,
        line=1,
        rule="php-strict-types",
        message=(
            "'declare(strict_types=1)' is missing. Without it PHP coerces silently -- a "
            '"12 apples" argument reaches an int parameter as 12, and "abc" as 0, which is '
            "the sentinel bug the type declaration was meant to prevent."
        ),
    )

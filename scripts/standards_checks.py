#!/usr/bin/env python3
"""The standards checks themselves: what counts as a violation in each language.

One tool, four files, each changing for its own reason:

    standards_core.py       what a violation IS, which files are in scope, file length
    standards_checks.py     what each LANGUAGE looks like, plus the dispatcher
    standards_sentinels.py  zero-like sentinels: unset-not-zero and payload-default
    check-source-limits.py  the driver: walking, the baseline ratchet, reporting, CLI

The core names are re-exported below so `from standards_checks import CheckConfig` keeps
working for the driver.

Rules here: bool-prefix, money-not-decimal, date-sentinel, date-out-of-range, razor-var,
php-strict-types. unset-not-zero and payload-default live in standards_sentinels.py and
are re-exported for the dispatcher.

Source of truth: engineering-standards/scripts/standards_checks.py
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
    is_money_name,
    iter_code_lines,
)
from standards_query import check_filter_in_memory, check_query_shape
from standards_scope import SCRIPT_SUFFIXES, should_check  # noqa: F401  (re-exported)

# public/protected/internal bool Foo { get; set; }  -- captures the member name.
CSHARP_BOOL_MEMBER = re.compile(
    r"^\s*(?:public|protected|internal|private\s+protected|protected\s+internal)\s+"
    r"(?:static\s+|virtual\s+|override\s+|abstract\s+|sealed\s+|required\s+|new\s+)*"
    r"bool\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\{|=>)"
)

# Any accessibility: double/float member whose name looks monetary.
CSHARP_FLOAT_MEMBER = re.compile(
    r"^\s*(?:public|protected|internal|private)\s+"
    r"(?:static\s+|readonly\s+|virtual\s+|override\s+|required\s+|new\s+)*"
    r"(?P<type>double|float)\??\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\{|=|;|=>)"
)

# Python annotated assignment or parameter: name: float
PYTHON_FLOAT_ANNOTATION = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:Optional\[)?float\b"
)

# `x = DateTime.MinValue` -- an assignment, not `==`, `=>`, or a `?? DateTime.MinValue`
# comparison key. Only stored sentinels are a problem; sort selectors are fine.
CSHARP_DATE_SENTINEL = re.compile(
    r"(?<![=!<>+\-*/%&|^~])=(?![=>])\s*DateTime(?:Offset)?\.(?P<name>MinValue|MaxValue)\b"
)

# `new DateTime(1, 1, 1)` and friends -- a literal before MIN_PLAUSIBLE_YEAR.
CSHARP_DATE_LITERAL = re.compile(r"new\s+DateTime(?:Offset)?\s*\(\s*(?P<year>\d{1,4})\s*,")

# A minimum/maximum date stored to mean "not set".
PYTHON_DATE_SENTINEL = re.compile(
    r"(?<![=!<>])=(?!=)\s*(?:date|datetime)\.(?P<name>min|max)\b"
)

# A quoted ISO date whose year is implausible: '0000-00-00', "1899-12-30". Shared by PHP
# and JavaScript, which both carry dates around as strings rather than as a date type.
DATE_STRING_LITERAL = re.compile(r"['\"](?P<year>\d{4})-\d{2}-\d{2}")

# `new Date(0)` -- the Unix epoch pressed into service as "not set".
JS_DATE_EPOCH = re.compile(r"new\s+Date\s*\(\s*0\s*\)")

# `declare(strict_types=1)`, which PHP requires to be the file's first statement.
PHP_STRICT_TYPES = re.compile(r"declare\s*\(\s*strict_types\s*=\s*1\s*\)")

# `var` in a Blazor @code block. IDE0008 owns this for .cs, but it is off-by-default and
# so never reaches .razor's generated syntax trees -- the same gap that costs .razor
# S138/S1541/S3776. Verified on SDK 10.0.203: zero IDE0008 diagnostics inside @code.
#
# The declared NAME is captured, not just the keyword, because the baseline grandfathers a
# violation under `file::rule::quoted-member`. Quoting the literal word "var" would give
# every declaration in a file one shared key, so baselining one would silently grandfather
# every future `var` added to that file -- a hole straight through the ratchet.
RAZOR_VAR_DECLARATION = re.compile(
    r"(?<![A-Za-z0-9_.$])var\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*|\()"
)

# An anonymous type has no name that could be written instead, so `var` is mandatory there
# and the rule does not apply. Matching `new {` also excludes the LINQ projection forms
# (`.Select(c => new { c.Name }).ToList()`), which is the intent -- IDE0008 makes exactly
# the same carve-out for .cs.
ANONYMOUS_TYPE_MARKER = re.compile(r"new\s*\{")

# The zero-like sentinel family (unset-not-zero, payload-default and the per-language
# coalesce patterns) lives in standards_sentinels.py. Re-exported so the dispatcher below
# and any caller importing from standards_checks keep working unchanged.
from standards_compose import (  # noqa: E402  (kept beside the other rule definitions)
    COMPOSE_FILENAME,
    ENV_EXAMPLE_FILENAME,
    check_compose_container_names,
    check_compose_ports,
    check_deploy_gate,
    check_env_example,
    check_overlay_name,
    is_workflow,
)
from standards_sentinels import (  # noqa: E402  (kept beside the other rule definitions)
    CSHARP_ZERO_COALESCE,
    JS_ZERO_COALESCE,
    PHP_ZERO_COALESCE,
    PYTHON_ZERO_COALESCE,
    check_payload_default,
    check_zero_coalesce,
)


def check_date_string_literals(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a quoted ISO date whose year predates MIN_PLAUSIBLE_YEAR.

    This is how the bad rows actually looked in InvoTrack: a '0000-00-00' in an import
    script, which aborts the statement the moment it reaches a typed intermediate.
    """
    for line_number, line in iter_code_lines(lines, path.suffix):
        literal_match = DATE_STRING_LITERAL.search(line)
        if literal_match and int(literal_match.group("year")) < MIN_PLAUSIBLE_YEAR:
            yield Violation(
                path=path,
                line=line_number,
                rule="date-out-of-range",
                message=(
                    f"Date literal year '{literal_match.group('year')}' is before "
                    f"{MIN_PLAUSIBLE_YEAR}. If the field really stores historic dates, say "
                    f"so where it is declared; otherwise this is a sentinel or a typo."
                ),
            )


def check_csharp_members(path: Path, lines: list[str], config: CheckConfig) -> Iterable[Violation]:
    for line_number, line in iter_code_lines(lines, path.suffix):
        if config.check_boolean_prefixes:
            bool_match = CSHARP_BOOL_MEMBER.search(line)
            if bool_match and not bool_match.group("name").startswith(BOOLEAN_PREFIXES):
                yield Violation(
                    path=path,
                    line=line_number,
                    rule="bool-prefix",
                    message=(
                        f"Boolean member '{bool_match.group('name')}' must start with "
                        f"{'/'.join(BOOLEAN_PREFIXES)} -- bare adjectives are ambiguous."
                    ),
                )

        if config.check_money_types:
            money_match = CSHARP_FLOAT_MEMBER.search(line)
            if money_match and is_money_name(money_match.group("name")):
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


def check_csharp_dates(path: Path, lines: list[str]) -> Iterable[Violation]:
    for line_number, line in iter_code_lines(lines, path.suffix):
        sentinel_match = CSHARP_DATE_SENTINEL.search(line)
        if sentinel_match:
            yield Violation(
                path=path,
                line=line_number,
                rule="date-sentinel",
                message=(
                    f"'DateTime.{sentinel_match.group('name')}' assigned as a stored value. "
                    f"Use null for 'not set' -- a sentinel date is indistinguishable from a "
                    f"real one once persisted."
                ),
            )

        literal_match = CSHARP_DATE_LITERAL.search(line)
        if literal_match and int(literal_match.group("year")) < MIN_PLAUSIBLE_YEAR:
            yield Violation(
                path=path,
                line=line_number,
                rule="date-out-of-range",
                message=(
                    f"Year '{literal_match.group('year')}' is before {MIN_PLAUSIBLE_YEAR}. If "
                    f"the field really stores historic dates, say so on the property; "
                    f"otherwise this is a sentinel or a typo."
                ),
            )


def check_razor_var(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag `var` inside a component, which IDE0008 cannot reach."""
    for line_number, line in iter_code_lines(lines, path.suffix):
        var_match = RAZOR_VAR_DECLARATION.search(line)
        if var_match and not ANONYMOUS_TYPE_MARKER.search(line):
            yield Violation(
                path=path,
                line=line_number,
                rule="razor-var",
                message=(
                    f"'{var_match.group('name')}' is declared with var, which hides its type. "
                    f"Write the real type, so a reader can tell a Car from a List<Car> without "
                    f"navigating away. (IDE0008 enforces this in .cs but cannot see .razor.)"
                ),
            )


def check_python_members(path: Path, lines: list[str], config: CheckConfig) -> Iterable[Violation]:
    for line_number, line in iter_code_lines(lines, path.suffix):
        if config.check_money_types:
            money_match = PYTHON_FLOAT_ANNOTATION.search(line)
            if money_match and is_money_name(money_match.group("name")):
                yield Violation(
                    path=path,
                    line=line_number,
                    rule="money-not-decimal",
                    message=(
                        f"'{money_match.group('name')}' is annotated float but holds money. "
                        f"Use decimal.Decimal."
                    ),
                )

        if config.check_date_ranges:
            date_match = PYTHON_DATE_SENTINEL.search(line)
            if date_match:
                yield Violation(
                    path=path,
                    line=line_number,
                    rule="date-sentinel",
                    message=(
                        f"A minimum/maximum date ('{date_match.group('name')}') is assigned as "
                        f"a stored value. Use None for 'not set'."
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


def check_js_dates(path: Path, lines: list[str]) -> Iterable[Violation]:
    for line_number, line in iter_code_lines(lines, path.suffix):
        if JS_DATE_EPOCH.search(line):
            yield Violation(
                path=path,
                line=line_number,
                rule="date-sentinel",
                message=(
                    "'new Date(0)' is the epoch used as 'not set'. Use null -- 1970-01-01 is "
                    "a real date and cannot be told apart from a chosen one once stored."
                ),
            )


def check_source_file(
    path: Path,
    lines: list[str],
    config: CheckConfig,
    baseline: dict[str, int],
    repo_root: Path,
) -> Iterable[Violation]:
    """Every check that applies to one file, dispatched by language."""
    # Compose files, workflows and .env.example are in scope for the deployment-contract
    # rules only. Length and the source rules are meaningless for them, and running the
    # length rule would flag long but entirely reasonable stack definitions.
    if COMPOSE_FILENAME.match(path.name):
        if config.check_compose_conventions:
            yield from check_compose_ports(path, lines)
            yield from check_compose_container_names(path, lines)
            yield from check_overlay_name(path, lines)
        return

    if ENV_EXAMPLE_FILENAME.match(path.name):
        # Only meaningful for a repo that actually ships as a compose stack; a library or
        # a docs repo has no COMPOSE_FILE to document.
        if config.check_compose_conventions and (repo_root / "docker-compose.yml").is_file():
            yield from check_env_example(path, lines)
        return

    if is_workflow(path, repo_root):
        if config.check_compose_conventions:
            yield from check_deploy_gate(path, lines)
        return

    yield from check_file_length(path, lines, config, baseline, repo_root)

    # The data-access rules cover every language that can hold a query builder, including
    # the two that do not have one in this estate yet -- see standards_query.py.
    if config.check_query_shape and path.suffix in (".cs", ".razor", ".py", ".php", ".ts", ".tsx"):
        yield from check_query_shape(path, lines, config)
        yield from check_filter_in_memory(path, lines, config)

    if path.suffix in (".cs", ".razor"):
        yield from check_csharp_members(path, lines, config)
        if config.check_unset_sentinels:
            yield from check_zero_coalesce(path, lines, CSHARP_ZERO_COALESCE)
        if config.check_payload_defaults:
            yield from check_payload_default(path, lines, CSHARP_ZERO_COALESCE)
        if config.check_date_ranges:
            yield from check_csharp_dates(path, lines)
        # .cs is IDE0008's job; only components need the script to stand in for it.
        if config.check_declared_types and path.suffix == ".razor":
            yield from check_razor_var(path, lines)
    elif path.suffix == ".py":
        yield from check_python_members(path, lines, config)
        if config.check_unset_sentinels:
            yield from check_zero_coalesce(path, lines, PYTHON_ZERO_COALESCE)
        if config.check_payload_defaults:
            yield from check_payload_default(path, lines, PYTHON_ZERO_COALESCE)
    elif path.suffix == ".php":
        if config.check_unset_sentinels:
            yield from check_zero_coalesce(path, lines, PHP_ZERO_COALESCE)
        if config.check_payload_defaults:
            yield from check_payload_default(path, lines, PHP_ZERO_COALESCE)
        if config.check_date_ranges:
            yield from check_date_string_literals(path, lines)
        if config.check_declared_types:
            yield from check_php_strict_types(path, lines)
    elif path.suffix in SCRIPT_SUFFIXES:
        if config.check_unset_sentinels:
            yield from check_zero_coalesce(path, lines, JS_ZERO_COALESCE)
        if config.check_payload_defaults:
            yield from check_payload_default(path, lines, JS_ZERO_COALESCE)
        if config.check_date_ranges:
            yield from check_js_dates(path, lines)
            yield from check_date_string_literals(path, lines)

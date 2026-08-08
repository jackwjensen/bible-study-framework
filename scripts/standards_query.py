#!/usr/bin/env python3
"""The `query-shape` rule: a big ORM query is a signal that a database view is wanted.

This is a SMOKE ALARM in exactly the sense refactoring.md gives the file-length limit --
and the same escape hatch applies. It does not say "this query is wrong". It says: this
query has grown to the size where a view is usually the better home, so either move it or
write down why not.

Why a view at all. When aggregation and multi-table shaping live in LINQ:

  * every consumer that needs the same shape rebuilds the joins, and they drift;
  * the grouping logic is invisible to anything that is not the app -- a report tool, a
    psql/mysql session, the next service;
  * the generated SQL is whatever the provider decides, and nobody reviews it.

A view fixes all three: one definition the database owns, callers compose with a plain
`.Where()` for their runtime filters. Map it keyless (`ToView("vw_Name")` in EF).

WHEN IT IS NOT RIGHT, and the exemption is the correct answer:

  * a single-table query that is merely long (a wide projection is not a view candidate);
  * a shape only ONE caller will ever want -- a view is a shared contract, and a contract
    with one party is just indirection;
  * anything whose grouping changes per call, which a view cannot express;
  * a one-off migration or backfill query that will outlive nothing.

Thresholds are per-repo (`.standards.json`). The aggregate threshold is lower on purpose:
a GROUP BY with aggregates across joined tables is the shape that most reliably wants a
view, so it trips sooner than a flat query of the same length.

Exempt a file with a reason in its first 40 lines, same marker family as file-length:

    standards: query-shape exempt -- per-caller grouping chosen at runtime; a view
    cannot express a GROUP BY whose columns vary by request.

Source of truth: engineering-standards/scripts/standards_query.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import CheckConfig, Violation, exemption_reason

# Where a query chain starts. Deliberately narrow: an EF DbContext member access, or an
# explicit queryable. A bare `.Where(` on an in-memory list is not a database query and
# must never trip this rule.
#
# `context` on its own is EXCLUDED, and that exclusion is load-bearing. It is MudBlazor's
# implicit row variable in every `.razor` table template (`@context.Number`) and ASP.NET's
# middleware parameter -- matching it flagged three files of pure markup as queries on the
# first run of this rule. A DbContext instance is `_db`, `db`, `_dbContext`, or something
# ending in `Context` with a real prefix; never the bare word.
CSHARP_QUERY_ROOT = re.compile(
    r"(?<![\w.])(?:_?db|_?dbContext|[A-Z]\w*Context)\s*\.\s*[A-Z]\w*"
    r"|\.AsQueryable\(\)"
)

# Django's queryset entry point. `.filter(` alone is not one -- it is also the name of a
# dozen unrelated helpers -- so the manager access has to be there.
PYTHON_QUERY_ROOT = re.compile(r"\.objects\s*\.")

CSHARP_GROUPING = re.compile(r"\.GroupBy\s*\(")
CSHARP_AGGREGATE = re.compile(r"\.(?:Sum|Count|LongCount|Average|Min|Max)\s*\(")
CSHARP_MULTI_TABLE = re.compile(r"\.(?:Join|GroupJoin|SelectMany|Union|Concat)\s*\(")

# A GroupBy whose projection opens another GroupBy. DOTALL because the two are on
# different lines in every real instance.
CSHARP_NESTED_GROUPING = re.compile(r"\.GroupBy\s*\(.*\.GroupBy\s*\(", re.DOTALL)

# Django aggregates the same two ways every time.
PYTHON_GROUPING = re.compile(r"\.(?:annotate|aggregate)\s*\(")
PYTHON_AGGREGATE = re.compile(r"\b(?:Sum|Count|Avg|Min|Max)\s*\(")
PYTHON_MULTI_TABLE = re.compile(r"\.(?:select_related|prefetch_related|union)\s*\(")
PYTHON_NESTED_GROUPING = re.compile(r"\.(?:annotate|aggregate)\s*\(.*\.(?:annotate|aggregate)\s*\(", re.DOTALL)

# A query chain has to actually chain to count. One `.Where()` spread over three lines
# for formatting is not what this rule is looking for.
MIN_CHAIN_OPERATORS = 3

# Query roots in one chain before its correlated subqueries look like a view. The outer
# chain is always one, so 3 means "the projection reaches into two other tables per row".
# Measured across the corpus: exactly one chain reaches 3+, and it is a true positive
# (four per-tenant counts over four tables); nothing sits at 2, so the threshold is not
# balanced on a knife edge.
MIN_CORRELATED_ROOTS = 3

# An explicit allowlist, not "any method call". `\.[A-Z]\w*\(` looked equivalent and
# counted `.FormatDate(` and `.OpenDetailDialog(` in Razor markup as query operators.
CSHARP_LINQ_METHODS = (
    "Where", "Select", "SelectMany", "OfType", "Cast",
    "OrderBy", "OrderByDescending", "ThenBy", "ThenByDescending",
    "GroupBy", "Join", "GroupJoin", "Include", "ThenInclude",
    "Sum", "Count", "LongCount", "Average", "Min", "Max",
    "Any", "All", "First", "FirstOrDefault", "Single", "SingleOrDefault",
    "ToList", "ToArray", "ToDictionary", "ToLookup",
    "Distinct", "Skip", "Take", "Union", "Concat", "Except", "Intersect",
    "AsNoTracking", "AsSplitQuery", "IgnoreQueryFilters",
)
CSHARP_OPERATOR = re.compile(
    r"\.\s*(?:" + "|".join(CSHARP_LINQ_METHODS) + r")(?:Async)?\s*\("
)

PYTHON_QUERYSET_METHODS = (
    "filter", "exclude", "annotate", "aggregate", "values", "values_list",
    "order_by", "distinct", "select_related", "prefetch_related", "only", "defer",
    "count", "exists", "first", "last", "union", "intersection", "difference",
)
PYTHON_OPERATOR = re.compile(r"\.\s*(?:" + "|".join(PYTHON_QUERYSET_METHODS) + r")\s*\(")

# ---------------------------------------------------------------------------------------
# PHP and TypeScript. No repo in the estate queries a database from either language today,
# so these are written to protect code that does not exist yet. That is a legitimate reason
# for a rule -- the file-length limit does not exist because files are long today either.
#
# What it changes is how they are verified. Their positive cases are synthetic, in
# test_standards_query.py; their FALSE-positive case is the real PHP and TypeScript already
# in the estate, which the rule must stay silent on. Between the two the dialect is as
# verified as the C# one, which also needed both halves -- its synthetic cases would never
# have caught it reporting Razor markup as SQL.
# ---------------------------------------------------------------------------------------

# Laravel's query builder and Eloquent, plus Doctrine's builder.
PHP_QUERY_ROOT = re.compile(
    r"DB::\s*table\s*\(|\b[A-Z]\w*::\s*(?:where|query|select|with)\s*\(|->createQueryBuilder\s*\("
)
PHP_GROUPING = re.compile(r"->\s*groupBy\s*\(")
PHP_AGGREGATE = re.compile(r"->\s*(?:sum|count|avg|average|min|max)\s*\(")
PHP_MULTI_TABLE = re.compile(r"->\s*(?:join|leftJoin|rightJoin|crossJoin|union|unionAll)\s*\(")
PHP_QUERY_METHODS = (
    "where", "orWhere", "whereIn", "whereNotIn", "whereHas", "select", "selectRaw",
    "join", "leftJoin", "rightJoin", "crossJoin", "groupBy", "having", "orderBy",
    "limit", "offset", "skip", "take", "with", "withCount", "distinct", "union",
    "sum", "count", "avg", "min", "max", "get", "first", "pluck", "paginate",
)
PHP_OPERATOR = re.compile(r"->\s*(?:" + "|".join(PHP_QUERY_METHODS) + r")\s*\(")

# Prisma, Drizzle, Knex and TypeORM builders.
TS_QUERY_ROOT = re.compile(
    r"\bprisma\s*\.\s*\w+\s*\.|\bdb\s*\.\s*select\s*\(|->createQueryBuilder\s*\("
    r"|\.createQueryBuilder\s*\(|\bknex\s*\("
)
TS_GROUPING = re.compile(r"\.\s*groupBy\s*\(|\bgroupBy\s*:")
# Three spellings, because the TypeScript ORMs do not agree: TypeORM/Knex chain a method
# (`.sum(`), Drizzle imports a bare helper (`sum(orders.amount)`), and Prisma uses an
# underscore key (`_sum`). The bare form is only consulted after a query root and three
# chained operators have already matched, so an unrelated `count(` cannot reach it.
TS_AGGREGATE = re.compile(
    r"\.\s*(?:sum|count|avg|min|max)\s*\("
    r"|\b_(?:sum|count|avg|min|max)\b"
    r"|\b(?:sum|count|avg|min|max)\s*\("
)
TS_MULTI_TABLE = re.compile(r"\.\s*(?:leftJoin|innerJoin|rightJoin|fullJoin|join|union|unionAll)\s*\(")
TS_QUERY_METHODS = (
    "select", "from", "where", "andWhere", "orWhere", "groupBy", "having", "orderBy",
    "leftJoin", "innerJoin", "rightJoin", "join", "limit", "offset", "findMany",
    "findFirst", "aggregate", "count", "sum", "avg", "min", "max", "union",
)
TS_OPERATOR = re.compile(r"\.\s*(?:" + "|".join(TS_QUERY_METHODS) + r")\s*\(")

# A SQL builder groups by a LIST of columns, so two `groupBy` calls in one chain mean two
# columns, not a nested grouping -- the C# signal has no honest analogue here. Rather than
# invent one that would fire on `->groupBy('a')->groupBy('b')`, these dialects rely on the
# other two signals. A pattern that can never match says so explicitly.
NEVER_MATCHES = re.compile(r"(?!x)x")

LINE_COMMENT = re.compile(r"//.*$|#.*$")

# A terminal call that pulls every matching row into application memory, immediately
# followed by work the database could have done. Written as ONE expression on purpose --
# see check_materialise_then_filter for why the cross-statement form is not included.
# PYTHON IS DELIBERATELY ABSENT, and the reason is that the shape does not exist there.
#
# This rule looks for `materialise().then_filter()` -- a terminal call, then work chained
# onto its result. Python materialises by WRAPPING (`list(qs)`), so the query methods sit
# inside the parentheses, before materialisation, and a Python list has no `.filter()` to
# chain afterwards anyway. Written for Python, the pattern matched three correct call sites
# in allegro-it-services on its first real run:
#
#     pending = list(AdConversion.objects.filter(retryable).order_by("created_at")[:limit])
#
# -- already filtered and sliced in SQL, then materialised, which is exactly right. Django's
# `.all()` is not a materialiser either; it returns a lazy QuerySet.
#
# The equivalent Django bug is `[r for r in list(qs) if r.is_active]` -- a comprehension, not
# a method chain, and a different rule if it is ever worth writing one.
MATERIALISERS = {
    ".cs": r"\.(?:ToList|ToListAsync|ToArray|ToArrayAsync|AsEnumerable)\s*\(\s*\)",
    ".razor": r"\.(?:ToList|ToListAsync|ToArray|ToArrayAsync|AsEnumerable)\s*\(\s*\)",
    ".php": r"->\s*(?:get|all)\s*\(\s*\)",
    ".ts": r"\.\s*(?:findMany|toArray)\s*\([^)]*\)",
    ".tsx": r"\.\s*(?:findMany|toArray)\s*\([^)]*\)",
}

# Work that belongs in SQL, applied to the materialised result.
DEFERRABLE_WORK = {
    ".cs": r"\.(?:Where|OrderBy|OrderByDescending|GroupBy|Sum|Count|Average|Min|Max|Skip|Take)\s*\(",
    ".razor": r"\.(?:Where|OrderBy|OrderByDescending|GroupBy|Sum|Count|Average|Min|Max|Skip|Take)\s*\(",
    ".php": r"->\s*(?:where|filter|sortBy|groupBy|sum|count|avg|min|max)\s*\(",
    ".ts": r"\.\s*(?:filter|sort|reduce|slice)\s*\(",
    ".tsx": r"\.\s*(?:filter|sort|reduce|slice)\s*\(",
}


class _Dialect:
    """The handful of patterns that differ between C# LINQ and the Django ORM."""

    def __init__(self, root, grouping, aggregate, multi_table, operator, nested_grouping):
        self.root = root
        self.grouping = grouping
        self.aggregate = aggregate
        self.multi_table = multi_table
        self.operator = operator
        self.nested_grouping = nested_grouping


CSHARP = _Dialect(
    CSHARP_QUERY_ROOT, CSHARP_GROUPING, CSHARP_AGGREGATE, CSHARP_MULTI_TABLE,
    CSHARP_OPERATOR, CSHARP_NESTED_GROUPING,
)
PYTHON = _Dialect(
    PYTHON_QUERY_ROOT, PYTHON_GROUPING, PYTHON_AGGREGATE, PYTHON_MULTI_TABLE,
    PYTHON_OPERATOR, PYTHON_NESTED_GROUPING,
)
PHP = _Dialect(
    PHP_QUERY_ROOT, PHP_GROUPING, PHP_AGGREGATE, PHP_MULTI_TABLE,
    PHP_OPERATOR, NEVER_MATCHES,
)
TYPESCRIPT = _Dialect(
    TS_QUERY_ROOT, TS_GROUPING, TS_AGGREGATE, TS_MULTI_TABLE,
    TS_OPERATOR, NEVER_MATCHES,
)

DIALECTS = {
    ".cs": CSHARP,
    ".razor": CSHARP,
    ".py": PYTHON,
    ".php": PHP,
    ".ts": TYPESCRIPT,
    ".tsx": TYPESCRIPT,
}


def _strip_comment(line: str) -> str:
    return LINE_COMMENT.sub("", line)


def view_shape(chain: str, dialect: "_Dialect") -> str | None:
    """Which view-shaped structure this chain has, or None.

    STRUCTURE is the primary trigger, not size. Line count is a proxy, and a poor one in
    both directions: a six-line aggregate across three tables is exactly what a view is
    for and no size threshold would ever catch it, while a twenty-line flat projection is
    merely wide. So the shapes below fire at ANY length, and size survives only as a
    backstop for the pathologically long.

    Single-table GroupBy-with-aggregate is deliberately NOT a shape. It is the common,
    usually-correct case -- measured against the corpus, treating it as view-shaped flagged
    a five-line `GroupBy(JobId).Select(Sum)` returning a dictionary, which should obviously
    stay where it is. Aggregating over ONE table is just a query; aggregating ACROSS tables
    is a contract.
    """
    grouped = bool(dialect.grouping.search(chain))
    if grouped and dialect.nested_grouping.search(chain):
        return "nests a grouping inside a grouping"

    if grouped and dialect.aggregate.search(chain) and dialect.multi_table.search(chain):
        return "aggregates across more than one table"

    # Per-row aggregates over other tables, written as correlated subqueries rather than a
    # GroupBy: `.Select(t => new { UserCount = db.Users.Count(u => u.TenantId == t.Id), ... })`.
    # There is no GroupBy anywhere, so the two signals above cannot see it -- but it is the
    # same thing, and it is the shape that turns one query into one-plus-N. Counting query
    # ROOTS finds it: the outer chain contributes one, each correlated subquery another.
    roots = len(dialect.root.findall(chain))
    if roots >= MIN_CORRELATED_ROOTS and dialect.aggregate.search(chain):
        return f"computes per-row aggregates over {roots - 1} other queries"

    return None


def _chain_end(lines: list[str], start: int, is_python: bool) -> int:
    """Index of the last line of the statement beginning at `start`.

    Bracket depth decides it, not punctuation: a C# statement ends at the `;` that sits
    at depth zero, and a Python one ends when the brackets it opened are closed again.
    Tracking depth is what keeps a lambda body or a nested projection from being read as
    the end of the chain.
    """
    depth = 0
    for index in range(start, len(lines)):
        text = _strip_comment(lines[index])
        depth += text.count("(") + text.count("[") - text.count(")") - text.count("]")

        if depth > 0:
            continue
        if is_python:
            # Python has no terminator, so the end is "brackets balanced and nothing is
            # still being continued". Three ways a chain continues, and the third is the
            # one that matters: the idiomatic Django style wraps the whole chain in outer
            # parens opened on an EARLIER line --
            #
            #     rows = (
            #         Organisation.objects.filter(...)      <- the scan starts here
            #         .annotate(total=Sum(...))             <- so depth is already balanced
            #         .annotate(recent=Count(...))
            #     )
            #
            # Counting depth from the root line alone sees a balanced line and stops, so
            # the chain measured one line and no multi-line Django query was ever analysed.
            # Looking ahead for a leading dot is what finds the rest of it.
            if text.rstrip().endswith(("\\", ",", ".")):
                continue
            following = next(
                (line for line in lines[index + 1:] if line.strip()),
                "",
            )
            if not following.lstrip().startswith("."):
                return index
        elif text.rstrip().endswith(";"):
            return index

    return len(lines) - 1


def check_filter_in_memory(path: Path, lines: list[str], config: CheckConfig) -> Iterable[Violation]:
    """Flag a query that materialises every row and then does the work in application memory.

    `db.Orders.ToListAsync()` sends the whole table across the wire; the `.Where()` that
    follows then throws most of it away. The right version costs the size of the RESULT, the
    wrong one costs the size of the TABLE, and the two read almost identically.

    Deliberately restricted to a SINGLE expression:

        (await db.Orders.ToListAsync()).Where(o => o.IsActive)     <- flagged
        List<Order> all = await db.Orders.ToListAsync();
        ...
        all.Where(o => Untranslatable(o))                          <- NOT flagged

    The cross-statement form is ambiguous and measuring it proved that: all three instances
    in the estate are correct, and one of them materialises a small lookup set once
    specifically to avoid issuing a query per loop iteration -- so flagging it would be
    flagging the fix for N+1. Whether it is a bug depends on whether the predicate can be
    translated to SQL at all, which no scanner can see.

    Within one expression there is no such ambiguity. Nobody materialises a set and filters
    it in the same breath on purpose; if the predicate were untranslatable, the two halves
    would not have been written as one expression. Zero occurrences in the estate today --
    which is what a guard rule is supposed to look like.
    """
    if exemption_reason(lines, "filter-in-memory") is not None:
        return

    materialiser = MATERIALISERS.get(path.suffix)
    deferrable = DEFERRABLE_WORK.get(path.suffix)
    dialect = DIALECTS.get(path.suffix)
    if materialiser is None or deferrable is None or dialect is None:
        return

    # Between the two: the close paren of an `(await ...)` wrapper, then any number of
    # chained calls -- but no statement boundary, which is what keeps this to one expression.
    pattern = re.compile(
        materialiser + r"[\s)]*" + r"(?:\.\w+\s*\([^;{}]*\)[\s)]*)*?" + deferrable,
        re.DOTALL,
    )

    index = 0
    while index < len(lines):
        text = _strip_comment(lines[index])
        if not dialect.root.search(text):
            index += 1
            continue

        end = _chain_end(lines, index, path.suffix == ".py")
        chain = "\n".join(_strip_comment(line) for line in lines[index:end + 1])

        if pattern.search(chain):
            yield Violation(
                path=path,
                line=index + 1,
                rule="filter-in-memory",
                message=(
                    "This query materialises every row and then filters, sorts or aggregates "
                    "in application memory. Move the work onto the query before the terminal "
                    "call so the database does it and only matching rows cross the wire -- the "
                    "cost then scales with the result rather than the table. If the predicate "
                    "genuinely cannot be translated to SQL, split it into two statements and "
                    "say so in a comment."
                ),
            )

        index = end + 1


def check_query_shape(
    path: Path,
    lines: list[str],
    config: CheckConfig,
    respect_exemption: bool = True,
) -> Iterable[Violation]:
    """Flag ORM query chains whose shape a database view usually ought to own.

    `respect_exemption=False` answers "would this file fire if it had no marker?", which is
    how the driver tells a load-bearing exemption from a stale one left on a file whose
    query has since been rewritten. Only the load-bearing ones are worth printing.
    """
    if respect_exemption and exemption_reason(lines, "query-shape") is not None:
        return

    dialect = DIALECTS.get(path.suffix)
    if dialect is None:
        return

    # Only Python's statement shape needs the leading-dot lookahead; the brace languages
    # all terminate on `;`.
    is_python = path.suffix == ".py"

    index = 0
    while index < len(lines):
        text = _strip_comment(lines[index])
        if not dialect.root.search(text):
            index += 1
            continue

        end = _chain_end(lines, index, is_python)
        chain = "\n".join(_strip_comment(line) for line in lines[index:end + 1])
        span = end - index + 1

        if len(dialect.operator.findall(chain)) < MIN_CHAIN_OPERATORS:
            index = end + 1
            continue

        shape = view_shape(chain, dialect)
        if shape is not None:
            reason = f"{shape}, which a view owns"
        elif span > config.query_max_lines:
            reason = f"spans {span} lines (limit {config.query_max_lines})"
        else:
            index = end + 1
            continue

        yield Violation(
            path=path,
            line=index + 1,
            rule="query-shape",
            message=(
                f"This query {reason}. A database view is usually the better home: one "
                f"definition the DB owns, callers compose with a plain filter for the runtime "
                f"parts. If a view is wrong here -- single caller, grouping varies per call, "
                f"one-off backfill -- say so with a "
                f"'standards: query-shape exempt -- <reason>' marker in the file header."
            ),
        )

        index = end + 1

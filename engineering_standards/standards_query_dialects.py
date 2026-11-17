#!/usr/bin/env python3
"""What a query CHAIN looks like in each language: the pattern tables the query rules read.

Split out of standards_query.py on 2026-09-03, when adopting `ruff format` expanded that
file past the pack's own 500-line limit. The seam is data against logic: everything here is
a description of syntax -- where a chain starts, what counts as grouping, aggregation, a
join, a materialiser -- while `standards_query` holds the two rules that read them
(`query-shape` and `filter-in-memory`) and changes for entirely different reasons.

ADDING A LANGUAGE IS A TABLE ENTRY HERE and touches no rule. That is the point of the
split: the rules already dispatched on `DIALECTS.get(suffix)`, so the dispatch was a table
long before the file was.

Source of truth: engineering-standards/engineering_standards/standards_query_dialects.py
"""

from __future__ import annotations

import re

from standards_scope import SCRIPT_SUFFIXES

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
    "Where",
    "Select",
    "SelectMany",
    "OfType",
    "Cast",
    "OrderBy",
    "OrderByDescending",
    "ThenBy",
    "ThenByDescending",
    "GroupBy",
    "Join",
    "GroupJoin",
    "Include",
    "ThenInclude",
    "Sum",
    "Count",
    "LongCount",
    "Average",
    "Min",
    "Max",
    "Any",
    "All",
    "First",
    "FirstOrDefault",
    "Single",
    "SingleOrDefault",
    "ToList",
    "ToArray",
    "ToDictionary",
    "ToLookup",
    "Distinct",
    "Skip",
    "Take",
    "Union",
    "Concat",
    "Except",
    "Intersect",
    "AsNoTracking",
    "AsSplitQuery",
    "IgnoreQueryFilters",
)
CSHARP_OPERATOR = re.compile(r"\.\s*(?:" + "|".join(CSHARP_LINQ_METHODS) + r")(?:Async)?\s*\(")

PYTHON_QUERYSET_METHODS = (
    "filter",
    "exclude",
    "annotate",
    "aggregate",
    "values",
    "values_list",
    "order_by",
    "distinct",
    "select_related",
    "prefetch_related",
    "only",
    "defer",
    "count",
    "exists",
    "first",
    "last",
    "union",
    "intersection",
    "difference",
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
    "where",
    "orWhere",
    "whereIn",
    "whereNotIn",
    "whereHas",
    "select",
    "selectRaw",
    "join",
    "leftJoin",
    "rightJoin",
    "crossJoin",
    "groupBy",
    "having",
    "orderBy",
    "limit",
    "offset",
    "skip",
    "take",
    "with",
    "withCount",
    "distinct",
    "union",
    "sum",
    "count",
    "avg",
    "min",
    "max",
    "get",
    "first",
    "pluck",
    "paginate",
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
    "select",
    "from",
    "where",
    "andWhere",
    "orWhere",
    "groupBy",
    "having",
    "orderBy",
    "leftJoin",
    "innerJoin",
    "rightJoin",
    "join",
    "limit",
    "offset",
    "findMany",
    "findFirst",
    "aggregate",
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "union",
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
# The JS-family entries are generated rather than listed, so a query builder cannot be
# checked in a .tsx file and ignored in the .mjs beside it. Prisma, Knex, Drizzle and
# TypeORM are all plain-JavaScript libraries whose TypeScript typings are optional; the
# extension says nothing about whether the query is there.
_JS_MATERIALISER = r"\.\s*(?:findMany|toArray)\s*\([^)]*\)"
_JS_DEFERRABLE = r"\.\s*(?:filter|sort|reduce|slice)\s*\("

MATERIALISERS = {
    ".cs": r"\.(?:ToList|ToListAsync|ToArray|ToArrayAsync|AsEnumerable)\s*\(\s*\)",
    ".razor": r"\.(?:ToList|ToListAsync|ToArray|ToArrayAsync|AsEnumerable)\s*\(\s*\)",
    ".php": r"->\s*(?:get|all)\s*\(\s*\)",
    **dict.fromkeys(SCRIPT_SUFFIXES, _JS_MATERIALISER),
}

# Work that belongs in SQL, applied to the materialised result.
DEFERRABLE_WORK = {
    ".cs": r"\.(?:Where|OrderBy|OrderByDescending|GroupBy|Sum|Count|Average|Min|Max|Skip|Take)\s*\(",
    ".razor": r"\.(?:Where|OrderBy|OrderByDescending|GroupBy|Sum|Count|Average|Min|Max|Skip|Take)\s*\(",
    ".php": r"->\s*(?:where|filter|sortBy|groupBy|sum|count|avg|min|max)\s*\(",
    **dict.fromkeys(SCRIPT_SUFFIXES, _JS_DEFERRABLE),
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
    CSHARP_QUERY_ROOT,
    CSHARP_GROUPING,
    CSHARP_AGGREGATE,
    CSHARP_MULTI_TABLE,
    CSHARP_OPERATOR,
    CSHARP_NESTED_GROUPING,
)
PYTHON = _Dialect(
    PYTHON_QUERY_ROOT,
    PYTHON_GROUPING,
    PYTHON_AGGREGATE,
    PYTHON_MULTI_TABLE,
    PYTHON_OPERATOR,
    PYTHON_NESTED_GROUPING,
)
PHP = _Dialect(
    PHP_QUERY_ROOT,
    PHP_GROUPING,
    PHP_AGGREGATE,
    PHP_MULTI_TABLE,
    PHP_OPERATOR,
    NEVER_MATCHES,
)
TYPESCRIPT = _Dialect(
    TS_QUERY_ROOT,
    TS_GROUPING,
    TS_AGGREGATE,
    TS_MULTI_TABLE,
    TS_OPERATOR,
    NEVER_MATCHES,
)

DIALECTS = {
    ".cs": CSHARP,
    ".razor": CSHARP,
    ".py": PYTHON,
    ".php": PHP,
    # Every JS-family extension, for the reason given above MATERIALISERS.
    **dict.fromkeys(SCRIPT_SUFFIXES, TYPESCRIPT),
}

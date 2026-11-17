#!/usr/bin/env python3
"""A paginated query with no total order -- the bug that shows a row twice, or never.

`LIMIT`/`OFFSET` slices whatever order the query plan happened to produce. Without an
`ORDER BY` that is unique per row, that order is free to differ between the request for page
1 and the request for page 2, so a row appears on both pages or on neither. Nothing errors.
The user sees a list that is quietly wrong.

WHY THIS EARNED A SCANNER RULE, when it lived as prose in data-access.md for months: it is
the rare judgment rule whose core is fully decidable, and it hides from every other kind of
review. Small result sets fit on one page, so the paging bug never shows in development; the
order itself LOOKS stable, because one query plan over one dataset is deterministic in
practice, and it flips only when the data grows or the statistics change. Caught in
allegro-it-services on 2026-08-10 by an index-based test that passed on a developer's
database and failed on CI's freshly-created one -- the resolver had been unordered for
months, and nobody reading the code had seen anything wrong with it. That is exactly the
profile of a rule that should not depend on being read.

IT UNDER-FIRES ON PURPOSE, in two directions, and both are the pack's usual choice for a
rule that must not cry wolf:

  * THE ORDERING GUARD IS WHOLE-FILE. A file with any ordering call anywhere is silent, even
    if this particular chain has none. Ordering is routinely applied in a different statement
    from the paging -- `query = query.OrderBy(...)` in one branch, `.Skip()` twenty lines
    later -- which is the same cross-statement blind spot `query-shape` documents. A rule
    that read one chain would fire on the commonest correct shape in the estate.
  * OFFSET PAGING ONLY. `.Take(20)` alone is "top N", which is a different question with
    legitimate unordered answers; `.Skip(n)` is unambiguously paging, and paging without an
    order is unambiguously the bug.

What is left after both guards is the file that paginates and orders nothing, anywhere --
which is precisely the shape of the incident, and reports once per file rather than once per
call, because the fix is one decision.

Source of truth: engineering-standards/engineering_standards/standards_paging.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from standards_core import Violation
from standards_exemptions import exemption_reason

PAGING_RULE = "paged-without-order"


@dataclass(frozen=True)
class Dialect:
    """One language's three signals: is it a query, does it page, does it order.

    `queries` exists to keep the rule off in-memory collections. `list.Skip(10)` over a
    `List<T>` is paging something whose order is already fixed by the list, and flagging it
    would be pure noise -- so a file must look like it reaches a database before the paging
    signal means anything at all.
    """

    queries: re.Pattern[str]
    pages: re.Pattern[str]
    orders: re.Pattern[str]
    # Paging shapes too ambiguous to trust on their own: they count only where the LINE
    # itself reaches the ORM, not merely the file. Python's start-less slice is the case --
    # see PYTHON below for the five false positives that put this here.
    pages_needing_query_on_line: re.Pattern[str] | None = None


# `.Skip(` on an EF chain. Ordering covers the LINQ operators, a raw-SQL ORDER BY, and the
# EF Core 6+ `.OrderBy` on a split query alike.
CSHARP = Dialect(
    queries=re.compile(r"\b(DbSet<|IQueryable<|ToListAsync\s*\(|AsQueryable\s*\(|_context\.|_db\.)"),
    pages=re.compile(r"\.Skip\s*\("),
    orders=re.compile(r"\.(OrderBy|OrderByDescending|ThenBy|ThenByDescending)\s*\(|ORDER\s+BY", re.IGNORECASE),
)

# Django. A queryset slice IS the paging construct (`qs[20:40]`), and `Paginator` is the
# framework's own wrapper for it. `Meta: ordering = [...]` counts as ordering: it applies a
# default order to every query on the model, which is exactly what the rule asks for.
PYTHON = Dialect(
    queries=re.compile(r"\.objects\b|\.filter\s*\(|\bQuerySet\b"),
    # A slice WITH a start is an offset, which is what this rule is about.
    pages=re.compile(r"\bPaginator\s*\(|[)\]]\s*\[\s*[\w.]+\s*:\s*[\w.]*\s*\]"),
    orders=re.compile(r"\.order_by\s*\(|\bordering\s*=|ORDER\s+BY", re.IGNORECASE),
    # A slice with NO start is a LIMIT, and in Python it is far more often a string being
    # truncated. Measured on allegro-it-services 2026-09-03: five findings, five truncations,
    # zero paging -- `str(exc)[:4000]`, `re.sub(r"...", "", subject)[:80]`, and
    # `(request.META.get("HTTP_USER_AGENT") or "")[:200]`. Every one ends in `)` immediately
    # before the slice, which is the only thing the old pattern asked for, and every one sat
    # in a file that mentions `.objects` somewhere else entirely.
    #
    # Requiring the ORM on the same line keeps `Model.objects.filter(...)[:50]` -- a genuine
    # unordered LIMIT -- while a truncation never has a queryset beside it.
    #
    # `[:1]` IS EXCLUDED, and a real Django file is why (found in the B3D pack's estate,
    # copied here 2026-09-05). Running the rule over a backend produced exactly one finding,
    # and it was wrong:
    #
    #     subquery = Category.objects.filter(id=OuterRef('category_id')).values('gc')[:1]
    #
    # That `[:1]` is not paging -- it is REQUIRED by Django's `Subquery` API, which needs a
    # queryset of exactly one row. It has an ORM call on the same line, so the guard above
    # cannot tell it from a LIMIT, and it is idiomatic correct code in every Django codebase
    # that uses a correlated subquery. Excluding a one-row take costs nothing, because
    # paginated output with a page size of one does not exist: nobody writes `[:1]` meaning
    # "the first page". `[:10]` and every other size still count.
    pages_needing_query_on_line=re.compile(r"[)\]]\s*\[\s*:\s*(?!1\s*\])[\w.]*\s*\]"),
)

# Prisma (`skip:`), TypeORM/Knex (`.offset(`, `.skip(`), Drizzle (`.offset(`).
TYPESCRIPT = Dialect(
    queries=re.compile(r"\bprisma\b|\.findMany\s*\(|createQueryBuilder\s*\(|\bknex\b|\bdrizzle\b|\bdb\."),
    pages=re.compile(r"\bskip\s*:|\.skip\s*\(|\.offset\s*\(|\btake\s*:\s*\w+[\s\S]{0,40}?\bskip\s*:"),
    orders=re.compile(r"\borderBy\b|\.orderBy\s*\(|\border\s*:|ORDER\s+BY", re.IGNORECASE),
)

# Eloquent and the Laravel query builder. `->paginate(` is included because Laravel's
# paginator is offset paging with a nicer name and inherits the identical defect.
PHP = Dialect(
    queries=re.compile(r"->where\s*\(|\bDB::|::query\s*\(|->get\s*\(\s*\)"),
    pages=re.compile(r"->skip\s*\(|->offset\s*\(|->forPage\s*\(|->paginate\s*\("),
    orders=re.compile(r"->orderBy\w*\s*\(|->latest\s*\(|->oldest\s*\(|ORDER\s+BY", re.IGNORECASE),
)

DIALECTS: dict[str, Dialect] = {
    ".cs": CSHARP,
    ".razor": CSHARP,
    ".py": PYTHON,
    ".php": PHP,
    ".ts": TYPESCRIPT,
    ".tsx": TYPESCRIPT,
    ".js": TYPESCRIPT,
    ".jsx": TYPESCRIPT,
    ".mjs": TYPESCRIPT,
    ".cjs": TYPESCRIPT,
}

# A line that is nothing but a comment. Commented-out paging is not paging, and firing on it
# would train people to delete the evidence of what they tried rather than to order a query.
COMMENT_LINE = re.compile(r"^\s*(//|#|\*|/\*|--)")


def check_paged_order(path: Path, lines: list[str]) -> Iterable[Violation]:
    """The first offset-paging call in a file that orders nothing, anywhere.

    One finding per file: the remedy is a single decision about what this query's total order
    is, and repeating it per call site would make a one-line fix read like a wall.
    """
    dialect = DIALECTS.get(path.suffix)
    if dialect is None:
        return

    body = "\n".join(lines)
    if not dialect.queries.search(body):
        return
    if dialect.orders.search(body):
        return
    # FILE-scoped, matching the finding's own granularity. Both guards above are whole-file
    # questions and the report is one per file, so the sentence that answers it -- "this
    # file's paging is not paginated output" -- is a statement about the file. A line marker
    # would sit on whichever call happened to be first and read as excusing only that one.
    if exemption_reason(lines, PAGING_RULE) is not None:
        return

    for index, line in enumerate(lines):
        if COMMENT_LINE.match(line):
            continue
        if not dialect.pages.search(line) and not (
            dialect.pages_needing_query_on_line
            and dialect.pages_needing_query_on_line.search(line)
            and dialect.queries.search(line)
        ):
            continue

        yield Violation(
            path=path,
            line=index + 1,
            rule=PAGING_RULE,
            message=(
                "this query pages with an offset and nothing in the file establishes an "
                "order, so LIMIT/OFFSET is slicing whatever order the plan happened to "
                "produce -- a row can appear on two pages or on none, with no error. Order "
                "by something UNIQUE per row: a timestamp alone is not enough when bulk "
                "inserts and test factories share one, so use the key or a tiebreak "
                "(.order_by('-created_at', 'id')). Sorting in the UI does not fix it -- the "
                "client sorts the page it received, not the page it should have received. "
                f"If this genuinely is not paginated output, say so in the file header: "
                f"'standards: {PAGING_RULE} exempt -- <why>'. The judgment half is "
                f".claude/rules/data-access.md, which is gate-scoped -- read it here rather "
                f"than assuming you have it."
            ),
        )
        return

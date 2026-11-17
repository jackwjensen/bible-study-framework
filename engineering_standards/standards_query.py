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

THE DRIFT THIS EXISTS TO CATCH, in the codebase that produced it. DonorLink computed
per-person, per-currency donation totals TWICE: once in a view (`vw_PersonSummary`), and once
in a LINQ chain with a nested `GroupBy` in the consolidation service, written months apart.
Two definitions of one aggregate in one codebase, and nothing to make them disagree loudly --
they simply return different totals on different pages. That query is why the nested-grouping
signal is the one this rule leads with: it is the shape that had already gone wrong here.

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

Source of truth: engineering-standards/engineering_standards/standards_query.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import CheckConfig, Violation, exemption_reason
from standards_query_dialects import (
    DEFERRABLE_WORK,
    DIALECTS,
    LINE_COMMENT,
    MATERIALISERS,
    MIN_CHAIN_OPERATORS,
    MIN_CORRELATED_ROOTS,
    _Dialect,
)


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
                (line for line in lines[index + 1 :] if line.strip()),
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
        chain = "\n".join(_strip_comment(line) for line in lines[index : end + 1])

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
                    "say so in a comment. The judgment half is .claude/rules/data-access.md, "
                    "which is gate-scoped -- read it here rather than assuming you have it."
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
        chain = "\n".join(_strip_comment(line) for line in lines[index : end + 1])
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
                f"'standards: query-shape exempt -- <reason>' marker in the file header. "
                f"Which signs make a view right, which make the exemption right, and how to "
                f"map one: .claude/rules/data-access.md, which is gate-scoped -- read it here "
                f"rather than assuming you have it."
            ),
        )

        index = end + 1

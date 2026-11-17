#!/usr/bin/env python3
"""Tests for the query-shape rule.

Plain asserts, no pytest, so it runs anywhere Python does:

    python engineering_standards/test_standards_query.py

Every case here is one the rule got WRONG at some point during its first hour of life, or
one that has never fired on real code and would otherwise be unverified. Those are the two
things a scanner test is for: a regex that has never been exercised is indistinguishable
from a broken one, and the first version of this rule reported three files of pure Razor
markup as database queries.

standards: file-length exempt -- a table of detector cases; its length tracks the number of
shapes the rule must get right, not accumulated responsibility, and splitting it would put
a signal's positive case in a different file from its false-positive twin.

standards: query-shape exempt -- the view-shaped queries in here are fixtures the detector
is supposed to catch, not queries this file runs; a view would have nothing to be a view of.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import CheckConfig  # noqa: E402
from standards_query import check_filter_in_memory, check_query_shape  # noqa: E402
from standards_scope import SCRIPT_SUFFIXES  # noqa: E402

CHECK = check_query_shape


def findings(source: str, suffix: str = ".cs") -> list[str]:
    """The messages the check under test produces for this source."""
    return [violation.message for violation in CHECK(Path(f"probe{suffix}"), source.splitlines(), CheckConfig())]


def expect_fires(label: str, source: str, contains: str, suffix: str = ".cs") -> None:
    hits = findings(source, suffix)
    assert hits, f"{label}: expected a finding, got none"
    assert any(contains in hit for hit in hits), f"{label}: expected a finding mentioning {contains!r}, got {hits}"
    print(f"  fires   {label}")


def expect_silent(label: str, source: str, suffix: str = ".cs") -> None:
    hits = findings(source, suffix)
    assert not hits, f"{label}: expected silence, got {hits}"
    print(f"  silent  {label}")


# --------------------------------------------------------------------------------------
# The three structural signals, C#
# --------------------------------------------------------------------------------------

NESTED_GROUPING = """
var aggregates = await db.Transactions
    .Where(t => ids.Contains(t.ShadowUserId))
    .GroupBy(t => t.ShadowUserId)
    .Select(g => new
    {
        ShadowUserId = g.Key,
        Count = g.Count(),
        Totals = g.GroupBy(x => x.Currency)
            .Select(cg => new { Currency = cg.Key, Sum = cg.Sum(x => x.Amount) })
            .ToList(),
    })
    .ToListAsync(ct);
"""

CROSS_TABLE_AGGREGATE = """
List<Row> rows = await db.Orders
    .Join(db.Customers, o => o.CustomerId, c => c.Id, (o, c) => new { o, c })
    .GroupBy(x => x.c.Country)
    .Select(g => new Row { Country = g.Key, Total = g.Sum(x => x.o.Amount) })
    .ToListAsync();
"""

CORRELATED_SUBQUERIES = """
_tenants = await DbContext.Tenants.IgnoreQueryFilters()
    .OrderByDescending(t => t.Id)
    .Select(t => new TenantInfo
    {
        Tenant = t,
        UserCount = DbContext.Users.IgnoreQueryFilters().Count(u => u.TenantId == t.Id),
        ClientCount = DbContext.Clients.IgnoreQueryFilters().Count(c => c.TenantId == t.Id),
        TimeLogCount = DbContext.TimeLogs.IgnoreQueryFilters().Count(l => l.TenantId == t.Id),
    })
    .ToListAsync();
"""

expect_fires("C# nested grouping", NESTED_GROUPING, "nests a grouping inside a grouping")
expect_fires("C# aggregate across tables", CROSS_TABLE_AGGREGATE, "across more than one table")
expect_fires("C# correlated subqueries", CORRELATED_SUBQUERIES, "per-row aggregates over 3")


# --------------------------------------------------------------------------------------
# The false positives. Each of these was, or would have been, a real report.
# --------------------------------------------------------------------------------------

# Real code from InvoTrack. Aggregating over ONE table is a query, not a contract; making
# this view-shaped was the first thing the corpus rejected.
SINGLE_TABLE_GROUPING = """
private async Task<Dictionary<int, decimal>> GetJobExpenseTotalsAsync(int tenantId)
    => await _db.Set<BillableExpense>()
        .Where(e => e.TenantId == tenantId && e.BillingAmount != null)
        .GroupBy(e => e.JobId)
        .Select(g => new { JobId = g.Key, Total = g.Sum(x => x.BillingAmount!.Value) })
        .ToDictionaryAsync(x => x.JobId, x => x.Total);
"""

# The one that actually shipped a wrong answer: `context` is MudBlazor's implicit row
# variable, and `.FormatDate(` is not a LINQ operator. Three files of markup were reported
# as queries before the root and operator patterns became allowlists.
RAZOR_MARKUP = """
<MudTd DataLabel="Kunde">@context.Client?.Name</MudTd>
<MudTd DataLabel="Dato">@Format.FormatDate(context.Date)</MudTd>
<MudTd DataLabel="I alt">@Format.FormatCurrency(context.Total)</MudTd>
<MudTd DataLabel="Status">@GetStatusLabel(context.DisplayStatus)</MudTd>
"""

# Fewer than MIN_CHAIN_OPERATORS: one filter wrapped for formatting is not a chain.
SHORT_CHAIN = """
Tenant? tenant = await db.Tenants
    .FirstOrDefaultAsync(t => t.Id == tenantId);
"""

# An in-memory LINQ chain over a list is not a database query at all.
IN_MEMORY_LINQ = """
List<string> names = people
    .Where(p => p.IsActive)
    .GroupBy(p => p.Country)
    .Select(g => g.Key)
    .ToList();
"""

expect_silent("C# single-table grouping", SINGLE_TABLE_GROUPING)
expect_silent("C# Razor markup using `context`", RAZOR_MARKUP, ".razor")
expect_silent("C# chain below the operator floor", SHORT_CHAIN)
expect_silent("C# in-memory LINQ over a list", IN_MEMORY_LINQ)


# --------------------------------------------------------------------------------------
# The exemption marker
# --------------------------------------------------------------------------------------

EXEMPTED = (
    "// standards: query-shape exempt -- per-caller grouping chosen at runtime; a view\n"
    "// cannot express a GROUP BY whose columns vary by request.\n" + NESTED_GROUPING
)

# The reason floor exists so `exempt -- fine` cannot buy silence.
EXEMPTED_WITH_SHRUG = "// standards: query-shape exempt -- fine\n" + NESTED_GROUPING

# A marker for a DIFFERENT rule must not suppress this one.
EXEMPTED_WRONG_TAG = (
    "// standards: file-length exempt -- declarative schema whose length tracks the number\n"
    "// of tables rather than accumulated responsibility.\n" + NESTED_GROUPING
)

expect_silent("C# exemption marker with a real reason", EXEMPTED)
expect_fires("C# exemption reason too short", EXEMPTED_WITH_SHRUG, "nests a grouping")
expect_fires("C# exemption tagged for another rule", EXEMPTED_WRONG_TAG, "nests a grouping")


# --------------------------------------------------------------------------------------
# Python / Django. None of these shapes exist in the estate's Django code, so without
# these cases the whole Python dialect would be unverified.
# --------------------------------------------------------------------------------------

DJANGO_NESTED_ANNOTATE = """
rows = (
    Organisation.objects.filter(is_active=True)
    .annotate(total=Sum("transactions__amount"))
    .annotate(recent=Count("transactions", filter=Q(created__gte=cutoff)))
    .order_by("-total")
)
"""

DJANGO_PLAIN_FILTER = """
users = User.objects.filter(is_active=True).exclude(email="").order_by("last_name")
"""

expect_fires("Django nested annotate", DJANGO_NESTED_ANNOTATE, "nests a grouping", ".py")
expect_silent("Django plain filter chain", DJANGO_PLAIN_FILTER, ".py")


# --------------------------------------------------------------------------------------
# PHP and TypeScript. No repo here queries a database from either language yet, so every
# positive case below is synthetic and every one exists to protect code not yet written.
# The false-positive half is covered against the estate's real PHP and TS, in
# test_dialect_silence.py.
# --------------------------------------------------------------------------------------

LARAVEL_CROSS_TABLE_AGGREGATE = """
$rows = DB::table('orders')
    ->join('customers', 'orders.customer_id', '=', 'customers.id')
    ->groupBy('customers.country')
    ->select('customers.country')
    ->sum('orders.amount');
"""

LARAVEL_PLAIN_QUERY = """
$users = User::where('is_active', true)
    ->orderBy('last_name')
    ->limit(50)
    ->get();
"""

TS_CROSS_TABLE_AGGREGATE = """
const rows = await db.select({ country: customers.country, total: sum(orders.amount) })
    .from(orders)
    .leftJoin(customers, eq(orders.customerId, customers.id))
    .groupBy(customers.country)
    .orderBy(customers.country);
"""

TS_PLAIN_QUERY = """
const users = await prisma.user.findMany({
    where: { isActive: true },
    orderBy: { lastName: 'asc' },
})
"""

expect_fires("Laravel aggregate across tables", LARAVEL_CROSS_TABLE_AGGREGATE, "across more than one table", ".php")
expect_silent("Laravel plain filtered query", LARAVEL_PLAIN_QUERY, ".php")
expect_fires("Drizzle aggregate across tables", TS_CROSS_TABLE_AGGREGATE, "across more than one table", ".ts")
expect_silent("Prisma plain findMany", TS_PLAIN_QUERY, ".ts")


# --------------------------------------------------------------------------------------
# filter-in-memory: the database sent the whole table and the app threw most of it away.
# Zero occurrences in the estate today -- this rule exists for code not yet written.
# --------------------------------------------------------------------------------------

CHECK = check_filter_in_memory
print()

CS_MATERIALISE_THEN_FILTER = """
List<Order> recent = (await db.Orders.ToListAsync())
    .Where(o => o.IsActive)
    .OrderByDescending(o => o.CreatedAt)
    .ToList();
"""

CS_PROPER_QUERY = """
List<Order> recent = await db.Orders
    .Where(o => o.IsActive)
    .OrderByDescending(o => o.CreatedAt)
    .ToListAsync();
"""

# The ambiguous cross-statement form the rule deliberately does NOT flag: in the estate all
# three real instances are correct, and one exists to avoid a query per loop iteration.
CS_CROSS_STATEMENT = """
List<Notification> notifications = await db.Notifications
    .Where(n => n.IsActive)
    .ToListAsync();
notifications = notifications.Where(n => MatchesCommaSeparatedRole(n)).ToList();
"""

PHP_MATERIALISE_THEN_FILTER = """
$active = User::where('tenant_id', $tenantId)->get()->where('is_active', true);
"""

# Real code from allegro-it-services. Python materialises by WRAPPING, so the query methods
# sit inside the parens and run in SQL -- this is correct, and the first Python version of
# this rule flagged three call sites exactly like it. Python is excluded from the rule; this
# case exists so nobody adds it back.
DJANGO_LIST_WRAPPING = """
pending = list(AdConversion.objects.filter(retryable).order_by("created_at")[:limit])
configs = list(TeamsSummaryTenantConfig.objects.select_related("tenant"))
"""

expect_fires("C# materialise then filter", CS_MATERIALISE_THEN_FILTER, "application memory")
expect_silent("C# filter before terminal call", CS_PROPER_QUERY)
expect_silent("C# cross-statement filter (deliberately allowed)", CS_CROSS_STATEMENT)
expect_fires("Laravel get() then filter", PHP_MATERIALISE_THEN_FILTER, "application memory", ".php")
expect_silent("Django list() wrapping a filtered queryset", DJANGO_LIST_WRAPPING, ".py")

# The SAME query in every JS-family extension. The dialect tables listed .ts and .tsx only,
# so an identical Prisma chain fired in a .tsx file and was silent in the .mjs beside it --
# a plain-JavaScript Node backend got no data-access rule at all, while the sentinel and
# date rules there worked normally. Prisma, Knex, Drizzle and TypeORM are JavaScript
# libraries whose typings are optional; the extension says nothing about whether the query
# is there. Looping the suffixes is the point: a new one added to SCRIPT_SUFFIXES without a
# dialect entry fails here rather than going quietly unchecked.
JS_MATERIALISE_THEN_FILTER = """
const active = (await prisma.user.findMany({ where: { tenantId } })).filter(u => u.isActive);
"""

for js_suffix in SCRIPT_SUFFIXES:
    expect_fires(
        f"Prisma findMany() then filter ({js_suffix})",
        JS_MATERIALISE_THEN_FILTER,
        "application memory",
        js_suffix,
    )


print("\nall data-access detector cases pass")


def test_the_detector_cases_all_ran() -> None:
    """Makes this module's coverage VISIBLE, and its passing robust.

    The cases above run at module import, so pytest does execute them -- but it collects
    no test function, so the suite reported them as zero tests and a failure would have
    surfaced as a collection error rather than as a named failing test. Reporting matters
    here for the same reason it matters everywhere in this pack: "it passed" should be a
    claim you can read rather than one you have to trust.

    Reaching this function at all means every expect_fires/expect_silent case above
    completed without raising.
    """
    assert SCRIPT_SUFFIXES, "the suffix table must not be empty, or nothing was exercised"

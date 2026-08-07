---
description: What a name and a declaration must tell the reader, in every language
---

# Naming and declared types (the judgment half)

Language-neutral, so this file has no `paths:` and always loads.

The rule behind all of it: **a declaration answers "what is this?" without the reader
navigating away.** Two things carry that answer — the *type* and the *name* — and where a
language can only give you one of them, the one it gives you has to work harder.

## Never `var` (or any other way of hiding the type)

```csharp
var items = GetThem();          // a Car? a List<Car>? a Car[]? an IQueryable<Car>?
List<Car> cars = GetThem();     // now the next reader cannot get it wrong
```

This is not a style preference. The mistakes it prevents are the ones that come from
*misreading what a variable is* — iterating something that is already materialised, awaiting
something that is not a task, indexing something with no indexer, calling `.Count` on a
sequence that will re-run the query. Every one of those reads fine at the point of the bug.

**Mechanically enforced — do not check these by eye:**

| Language | What holds the rule | Where |
|---|---|---|
| C# (`.cs`) | `IDE0008` as an error, all three `csharp_style_var_*` false | `.editorconfig` |
| Blazor (`.razor`) | `razor-var` rule (IDE0008 cannot see components) | `check-source-limits.py` |
| JS / TS | `no-var`, `prefer-const`, `no-explicit-any`, `explicit-module-boundary-types` | eslint |
| PHP | `php-strict-types` rule; typed params, returns and properties | `check-source-limits.py` |
| Python | type hints at boundaries (see the Python rules file) | judgment |

**The one exemption, and it is the language's, not yours:** where no type name exists to
write. C# anonymous types (`new { Name = x }`, and any LINQ projection producing them) *must*
use `var`, and IDE0008 already knows that — it never fires on them. If you find yourself
wanting an exemption anywhere else, the answer is a named type, not a `var`.

## Removing `var` is not mechanical — pick the type deliberately

`dotnet format style --diagnostics IDE0008` will write *a* type in, but it writes the
compiler's inferred one, and that is not always the one you want:

- **Declare the type the code depends on.** If the body calls `.Add`, `List<Car>` is honest
  and `IEnumerable<Car>` is a lie that will not compile. If the body only iterates, the
  interface documents the weaker requirement — prefer it.
- **Never widen to something that changes when the work happens.** `IQueryable<T>` and
  `IEnumerable<T>` defer; `List<T>` has already run. Writing the deferred type on something
  materialised (or the reverse) turns a naming fix into a performance or double-enumeration
  bug. This is the one real risk in a `var` sweep — review the diff, do not just trust it.
- **A type so long it hurts to write is telling you something.** A nested
  `Dictionary<string, List<Tuple<...>>>` wants a named record. Extract it rather than
  reaching back for `var`.

## Names describe meaning, and shape

The type answers "what is this?"; the name answers "which one, and how many?". A name that
disagrees with its type is worse than no name.

- **Singular is one, plural is many.** `car` holds a `Car`; `cars` holds a collection. Never
  `car` for a list, and never `cars` for a single record that happens to come from a
  collection.
- **Say what a keyed collection is keyed by** — `carsById`, `ratesByCurrency`. `carsMap`
  tells the reader there is a lookup and nothing about how to use it.
- **A count or total is not the thing it counts.** `carCount` / `totalCars`, never `cars` for
  an `int`.
- **No abbreviations outside the ones the domain actually uses.** `employee`, not `emp`;
  `project`, not `prj`. `db`, `id`, `url`, `api`, `vat`, `cvr` are fine — they are how the
  domain speaks.
- **Single letters only where tradition is unambiguous:** `i`/`j`/`k` for loop indices, `e`
  for event args, `ex` for exceptions, and a short lambda parameter where the type is obvious
  from one line of context (`.Where(t => t.Date > today)`). Anywhere else, name it.
- **Booleans assert:** `IsActive`, `HasDiscount`, `CanDelete` — never a bare adjective like
  `Active`. (Enforced mechanically for C# members; judgment everywhere else.)

## Why this rule is worth the noise it makes

It is the largest mechanical diff the pack introduces, and the payoff is not tidiness. Every
standard in this pack exists because something broke; this one exists because *reading code
correctly is the precondition for every other rule working at all.* A reviewer who cannot
tell what a variable holds cannot check the DRY rule, the null rule, or the money rule
either.

---
paths:
  - "**/*.cs"
  - "**/*.razor"
  - "**/*.py"
  - "**/*.php"
  - "**/*.ts"
  - "**/*.tsx"
  - "**/*.js"
  - "**/*.jsx"
description: What a name and a declaration must tell the reader, in every language
---

# Naming and declared types (the judgment half)

**Scoped to source files rather than always-loaded** (decided 2026-09-10), and scoped WIDER
than the other two gate-triggered rules on purpose. `generic-filename`, `razor-var`,
`php-strict-types`, `money-not-decimal` and the boolean-member rule have gates; the largest
part of this file does not. Nothing mechanical spots `carsMap`, `emp` or `cars` holding an
`int`, so there is no finding to send a reader here — the file has to arrive with the code it
judges. It still costs nothing in a session that touches only markdown, config or CI.

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

Where a language forces you to omit the type it also tells you so; anywhere else, write it.
**Removing an existing `var` is not mechanical** — the inferred type is not always the one you
want, and widening a materialised list to a deferred interface turns a naming fix into a
performance bug. That hazard is .NET's, so the worked version lives in `csharp-standards.md`,
which loads with the files it applies to.

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

  **Accessibility does not enter into it** (decided 2026-08-10). A private field is as
  ambiguous as a public property — the reader of a class body reads both — and a rule that
  applied to only half the declarations read as arbitrary, with the same name legal as a
  field and illegal once promoted to a property. Fields count too, not just properties.

  Your casing convention is respected: `_isFormValid` and `canManage` both assert, and both
  pass. What does *not* pass is a word that merely starts with those letters — `Issued`,
  `Issuing`, `Cancelled` — because the character after the prefix has to begin a new word.

  Method names are out of scope, deliberately. `MatchesFilters`, `TryGetMessage` and
  `FilterPasses` are good names and a `Should`/`Is` prefix would make them worse.

- **Where a value has a canonical short code and a human name, the column stores the code.**
  Country `"DK"` not `"Denmark"`, currency `"DKK"` not `"kroner"`, language `"da"` not
  `"Dansk"`. That decision is made when the entity is designed, which is why the one-line
  version is here; the full contract — form bindings, display helper, filtering on the
  rendered name, what crosses a system boundary — is in `ui-standards.md`, which loads with
  the UI files that implement it.

- **Money is `decimal`, wherever it lives.** This one reaches into method bodies — a
  `double total = 0;` accumulating invoice lines is the textbook rounding bug and carries no
  modifier at all, so locals and parameters are checked alongside members. It is the *name*
  that scopes this (`total`, `price`, `amount`, `fee`, …), which is what makes reaching that
  far safe; a `double ratio` or `double latitude` is not money and is not flagged.

## A filename is a name, and it is the one read most

**Enforced mechanically by `check-source-limits.py`'s `generic-filename` rule.** What follows
is the judgment half: what to call the file instead.

A path is a declaration, and it is read far more often than the code inside it — it is what a
grep result, a stack frame, a diff header, a file tree and an agent's search all show first.
`utils.ts` answers nothing, so the reader opens it to find out whether it holds date maths or
a Stripe client.

The cost is not the one lookup. **A name that means nothing excludes nothing**, so the next
person with a homeless function adds theirs to it, and `helpers.py` becomes the file that has
to be read in full before anyone can change anything in it. That is the actual mechanism by
which junk-drawer files form, and it is a naming failure before it is a refactoring one.

- **Name it after what it holds**, not after where it sits in an architecture diagram:
  `InvoiceNumbering.cs`, not `Helpers.cs`; `stripeWebhookPayloads.ts`, not `utils.ts`.
- **If you cannot name it, it is not one thing.** That is the same signal the file-length rule
  gives, arriving earlier and more cheaply. Split it and name the pieces.
- **The directory counts as part of the name.** `accounts/service.py` reads as a coordinate —
  the directory says what, the file says which layer — and the rule leaves it alone. Only a
  role noun with *no* subject anywhere above it (`src/service.ts`, a root-level `Service.cs`)
  is a finding. A junk drawer is a finding wherever it sits, because "helpers" survives any
  amount of context.
- **A name your framework resolves is not yours to change**, and is never flagged: `models.py`,
  `page.tsx`, `Program.cs`, `index.ts`, `__init__.py`. `utils.py` is exempt too — it is a
  settled Python convention — while `utils.ts` is not, because nothing mandates it.
- **Where a name genuinely cannot change** — a published entry point, a package's public API —
  say so in the file rather than arguing with the gate:
  `// standards: generic-filename exempt -- <reason>`.

## Why this rule is worth the noise it makes

It is the largest mechanical diff the pack introduces, and the payoff is not tidiness. Every
standard in this pack exists because something broke; this one exists because *reading code
correctly is the precondition for every other rule working at all.* A reviewer who cannot
tell what a variable holds cannot check the DRY rule, the null rule, or the money rule
either.

---
description: When and how to split oversized files, methods, and classes
---

# Refactoring: the judgment half of the size rules

The build already enforces the numbers — method ≤ 60 lines, cyclomatic ≤ 10, cognitive ≤ 15,
parameters ≤ 7 (`SonarLint.xml`), file ≤ 500 lines (`check-source-limits.py`). Those tell you
*that* something is too big. This file is about *how* to split it, and when not to.

## The thresholds are a smoke alarm, not a spec

A 520-line file is not a bug. The limit exists because size correlates with a real problem:
the file has accumulated more than one responsibility. Fix the responsibility, and the line
count follows. **Never split a file just to get under the number** — two 260-line files that
must always be edited together are worse than one 520-line file.

If a file genuinely is one cohesive thing at 520 lines, add it to the baseline with a comment
saying why, rather than inventing a seam that isn't there.

## Where the seams usually are

**Services** — split by *collaborator*, not by alphabet. A `PaymentService` that talks to
Stripe, writes ledger rows, and sends receipts is three things: `StripePaymentGateway`,
`PaymentLedger`, `PaymentNotifier`. The orchestrating method stays; the mechanics move.

**Blazor components** — extract child components around *state ownership*, not markup volume.
If a block of markup only reads one field and raises one event, it is a component. If a
`@code` block exceeds the method limits, the logic almost always belongs in a service (see
"business logic lives on entities or services" in the C# rules), not in a private helper.

**Entities** — an entity over the limit usually means computed/derived members that belong in
a service, or a value object waiting to be extracted (address, money-with-currency, period).

**`Program.cs` / startup** — split by concern into `AddXxx` extension methods on
`IServiceCollection`, one per subsystem. This is nearly always the right move and carries no
behavioural risk.

## Order of operations when a limit trips

1. Name the responsibilities in the file out loud. If you can only name one, baseline it.
2. Pick the seam with the fewest inbound references.
3. Move the code **without editing it** — pure move, commit, verify build.
4. Only then clean up the moved code.

Splitting and rewriting in one step is how refactors introduce bugs that nobody can bisect.

## Adopting the limits on existing code: ratchet, never big-bang

Existing violations are recorded in a baseline (`.standards-baseline.json` for file length,
`GlobalSuppressions.cs` for analyzer rules). The rule is:

- **New files and new methods must comply.** No exceptions.
- **Baselined items may not get worse.** A file already at 715 lines fails the build at 716.
- **Shrinking updates the baseline downward**, so the debt only ever ratchets down.

Do not mass-refactor a baseline in an unattended run. Splitting an 871-line payment service
with no test covering it is how prod breaks. Take one baselined item per PR, with a human
watching.

## When a threshold is genuinely wrong

Tune the threshold in the pack and re-apply — do not sprinkle per-file suppressions. If the
same rule needs suppressing in five places, the threshold is wrong, not the code.

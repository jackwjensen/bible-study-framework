---
paths:
  - "**/.standards-baseline.json"
  - "**/.standards.json"
  - "**/GlobalSuppressions.cs"
  - "**/eslint-suppressions.json"
description: When and how to split oversized files, methods, and classes
---

# Refactoring: the judgment half of the size rules

**This file is gate-triggered, not always-loaded** (decided 2026-09-10). Every rule it
carries the judgment half of is mechanically detected — `file-length`, `--paydown`, the
`.standards.json` tuning floor, and SonarLint's method and complexity limits — and each of
those names this file when it fires. So it loads with the artifacts you open while *acting*
on a finding: a baseline, a suppressions file, the config. Read it when a gate sends you
here; you are not expected to be carrying it before then.

The build already enforces the numbers — method ≤ 60 lines, cyclomatic ≤ 10, cognitive ≤ 15,
parameters ≤ 7 (`SonarLint.xml`), file ≤ 500 lines (`check-source-limits.py`). Those tell you
*that* something is too big. This file is about *how* to split it, and when not to.

## The thresholds are a smoke alarm, not a spec

A 520-line file is not a bug. The limit exists because size correlates with a real problem:
the file has accumulated more than one responsibility. Fix the responsibility, and the line
count follows. **Never split a file just to get under the number** — two 260-line files that
must always be edited together are worse than one 520-line file.

The tell that you are doing it anyway is in the justification. "Moving it also brings the
other file under 500 lines" is not a reason to move something; it is a confession that the
number chose the seam. If the split would be right at 400 lines, make it. If it would not,
do not make it at 520 either.

## When the file is genuinely one thing: exempt it, in the file

Some files are long for a reason refactoring cannot improve — a declarative schema whose
length tracks the number of *entities*, a lookup or translation table, a parser whose grammar
is one subject, a routing table. Splitting those scatters one subject across files that must
always be edited together, which is the outcome this rule is supposed to prevent.

Say so in the file's header, in the first 40 lines:

```python
"""The account tables.

standards: file-length exempt -- declarative ORM schema; its length tracks the number of
tables, not accumulated responsibility, and splitting it would scatter one schema across
files that must always be edited together.
"""
```

**This is not a baseline, and the difference is the whole point.** A baseline is silent debt
somebody intends to pay down. An exemption is a decision:

| | Baseline | Exemption |
|---|---|---|
| Lives | in a separate ledger | in the file, where it is read |
| Says why | optionally | **required** — under ~30 characters is rejected |
| Visible | as a count | `check-source-limits` prints the path and the reason every run |
| Means | "not yet fixed" | "looked at; nothing to fix" |

The marker works behind any comment syntax and the reason may wrap across lines. It must sit
in the header: buried at line 900 it is invisible to anyone opening the file, and the scanner
ignores it there.

**The reason is the gate.** If you cannot write one you would defend in review, that is the
rule doing its job — split the file. "It is fine" is rejected on length, deliberately.

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

1. Name the responsibilities in the file out loud. If you can only name one, exempt it with
   that sentence as the reason.
2. Pick the seam with the fewest inbound references.
3. Move the code **without editing it** — pure move, commit, verify build.
4. Only then clean up the moved code.

Splitting and rewriting in one step is how refactors introduce bugs that nobody can bisect.

## Adopting the limits on existing code

**A baseline is a starting point, not a resting place** (decided 2026-08-21, superseding the
earlier "paid down only on request" policy). The size limits exist to get oversized files
refactored. A baseline buys the *adoption* — it stops the pack landing as a wall of red on
day one — and nothing more. It is not permission to carry a 900-line file indefinitely, and
"it is baselined" is not an answer to "why is this file still like this?".

So: **when your branch changes a baselined file, that file leaves the branch smaller than
it arrived** (decided 2026-08-27, superseding "do not ask whether you are allowed to; you
are"). That wording granted permission and required nothing, and permission is not enough:
an optional refactor loses every race against a deadline, so it is always the thing that
gets dropped, and a rule that costs nothing to skip is not a rule.

`check-source-limits.py --paydown` enforces it at push and again in CI. Two conditions,
because either alone leaks:

1. The file is **below its size recorded at the base ref** — proof this branch paid
   something down.
2. The working-tree baseline **records the new size** — proof you re-ran
   `--write-baseline`. Without this the recorded number never moves, so the next branch to
   touch the file clears condition 1 for free and the ratchet quietly stops ratcheting.

Not at commit: being refused on your first commit into a big file would only teach
`--no-verify`. The unit of work is the branch.

Prefer the file you were already working in — you have the context and the diff is
reviewable. Paying a file all the way under the limit removes its entry entirely; that is
the target, not a bonus. **A file carrying a valid exemption owes nothing**: an exemption
means "looked at; nothing to fix", and demanding payment anyway would push people to delete
the marker, which is the one thing that makes the decision visible.

A one-line token shrink satisfies the gate and fails review. The gate enforces the
direction; the reviewer enforces that the slice is real.

Read the findings list before writing a baseline in the first place: a baseline is for
debt, not bugs, and a live defect filed under "pre-existing" is a defect nobody looks at
again.


## What `.standards.json` may not do quietly

The config file can relax a rule. It may not do so **silently** — every weakening states a
reason of at least 30 characters, the same floor an exemption meets, and every one is
printed on each run as `tuned: <setting> = <value> -- <reason>`.

This closed the back door behind every front door above. One line —
`{"maxFileLines": 100000}` or `{"checkGitignore": false}` — used to disable any rule with no
argument and no trace, so a repo that had switched off the gitignore check printed the same
`clean` an honest one printed. `checkGitignore` is in `NEVER_BASELINED` precisely because an
unignored `.env` is a live credential leak; a boolean turning it off made that decorative.

A **tightening** needs no reason — `maxFileLines: 300` is not a relaxation, and charging an
argument for it would teach people the config is hostile. `maxFileLines` above 1500 is
refused outright: past that it is not a threshold, it is the rule switched off with the
light left on.

The ratchet: existing violations are recorded in a baseline (`.standards-baseline.json` for
file length, `GlobalSuppressions.cs` for analyzer rules, `eslint-suppressions.json` for
ESLint — **reset all of them or none**), and

- **New files and new methods must comply.** No exceptions.
- **Baselined items may not get worse.** A file already at 715 lines fails the build at 716.
- **Shrinking updates the baseline downward**, so the debt only ever ratchets down.

**Refactor it, do not narrate it** — the four steps above, so each is independently
verifiable and revertable. Where a file has no test, that is a reason to **add the
characterisation test first**, not to leave it at 871 lines.

**A split is not finished when the line count drops.** Each extracted piece is a module no
test names, so the finding count rises first, and `max-lines-per-function` (staged files
only) is usually still unmet. Budget both rounds; take the second only where the pieces are
real. Measurements: [engineering-standards/docs/refactoring.md](https://github.com/jackwjensen/engineering-standards/blob/master/docs/refactoring.md).

## When a threshold is genuinely wrong

Tune the threshold in the pack and re-apply — do not sprinkle per-file suppressions. If the
same rule needs suppressing in five places, the threshold is wrong, not the code.

---
description: What the user is told when something fails — what failed, whose problem it is, whether trying again helps; the technical detail goes to the log
paths: ["**/*.cs", "**/*.razor", "**/*.py", "**/*.php", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx", "**/*.mjs", "**/*.cjs"]
---

# What the user is told when something fails

Reasoning and the incident: [engineering-standards/docs/user-errors.md](https://github.com/jackwjensen/engineering-standards/blob/master/docs/user-errors.md). The mechanical
half is `technical-error-shown` (C#/Razor, Python, PHP, TypeScript/JavaScript): an exception's
own text, a stack trace, or a bare status code reaching output a user reads. Not restated here.

**Every entry point's failure path shows a sentence written for the person reading it**, and
the sentence answers three questions:

1. **What failed** — the action, in the page's own words: "Kunden blev ikke gemt".
2. **Whose problem it is** — theirs (the input), ours (a fault, already logged), or a third
   party's (the accounting system, the payment provider, the network).
3. **Whether trying again helps** — and when it does not, what does: correct the input,
   reload, reconnect the integration, contact support.

"Der opstod en fejl" fails this rule. It is human-readable and answers none of the three.

**The technical detail goes to the log, at the place the sentence is chosen** — the exception
with its chain, the status code, the response body. The user's sentence and the log entry are
two renderings of one failure, for two audiences, and neither stands in for the other.

**One translator per repo.** Entry points never compose a message from an exception; they call
one shared function that maps exception types to sentences and logs the exception — InvoTrack's
`ErrorMessages.Describe(ex, whatFailed)` behind an injected reporter is the model. A concurrency
conflict, a duplicate key, a timeout and an unknown fault each get their own sentence there,
once, instead of seventy improvised ones.

**A designated user-facing exception carries deliberate user text.** When code deep in a
service knows the exact sentence the user needs ("Kørslen er faktureret og kan ikke
redigeres."), it throws the repo's carrier type — its name contains `UserFacing`, or it is
listed in `.standards.json` `userFacingExceptions` (a subclass declared in another file, a
domain error that predates the convention) — and the translator shows that message as-is.
Two ways to break it:

- **Throwing a general type for a user sentence.** If `InvalidOperationException` carries both
  "Kørslen er faktureret" and "Tenant not found.", the entry point cannot tell them apart and
  shows both. User text gets the carrier; everything else stays technical.
- **Putting technical text into the carrier.** Detail support will need goes in a separate
  log-only property (`Diagnostic`), never into the message.

**A status code is not an explanation.** Map it to a sentence at the boundary that received it:
401/403 — the stored credentials were refused, reconnect the integration; 404 — the record no
longer exists there; 400/409/422 — the data was rejected, quoting the provider's own validation
text only where it is written for people; 429/5xx — their side is busy or down, try again
later. The code itself goes to the diagnostic detail.

**Findings are never baselined.** Each is a defect a user meets today, so a repo taking the
rule fixes the site, declares its carrier, or marks the line — it does not grandfather it.

**Exempt only output no end user reads** — an operator-only diagnostic, a developer CLI whose
reader is the developer — with the line marker and its reason. "Only admins see it" is not one:
an admin is a user, and needs the three answers too.

---
paths:
  - "**/docs/**/*.md"
  - "**/README.md"
  - "**/.standards.json"
  - "**/.env.example"
  - "**/plans/**/*.md"
description: How to write a page in the docs tree — one need per page, audience, frontmatter, registration, coverage
---

# Writing a documentation page

The constitution — what a code change owes, `CLAUDE.md` as constitution and index, and where
rationale is allowed to live — is in `documentation.md`, which loads in every session. This
file is the authoring contract, and it loads with the files it governs.

## One need per page

A reader arrives with exactly one of four needs, and a page serves exactly one of them
(this is the Diátaxis discipline — findability is decided at authoring time, not by a
search box):

| `type:` | The reader is | The page is |
|---|---|---|
| `tutorial` | learning, first contact | a guided lesson with a guaranteed outcome |
| `how-to` | doing a task | numbered steps from a known start to a done state |
| `reference` | looking something up | facts, tables, contracts — structured for scanning |
| `explanation` | asking why | background, rationale, trade-offs |

The findability killer is the mixed page: a how-to that drifts into explaining, a reference
that teaches. When a page needs two types, it is two pages — split it and link them.
**Title every page as the reader's task or question** ("Add a migration", "Why push is the
deploy boundary"), never as the feature's internal name.

## Audience discipline

Every page declares who it is for, and the declaration changes the writing:

- `audience: dev` assumes repo context and may use the codebase's own vocabulary.
- `audience: user` is **customer-facing copy**: the product's users' vocabulary, no
  internal jargon, no exported dev notes — held to the same bar as the marketing site,
  because public user docs *are* marketing. Write user pages for users from the start;
  "converting" a dev page produces docs that read like internal notes, which is one of the
  classic ways user documentation becomes worthless.

**Who may read the docs is a product property, not a per-page exception.** A product's
documentation is fully public, fully customer-gated, or a hybrid — all three are
legitimate, and the repo declares which in the `docs/index.md` preamble (one sentence:
"Documentation here is public unless a page says otherwise", or the reverse). Prose in
the index rather than a config key, deliberately: a reader deciding what to write looks
at the index, and a config key nothing mechanical consumes is a copy of a decision
waiting to outlive it. Per-page `access:` frontmatter overrides the declared default
either way; a fully-public product still gates the page that reveals tenant-specific
operations or paid-feature internals.

## The frontmatter contract

Stated once, here. Every `docs/**/*.md` page opens with:

```markdown
---
audience: user        # dev | user
type: how-to          # tutorial | how-to | reference | explanation
access: public        # public | customer — user pages only, optional, default public
---
```

`README.md` and `CLAUDE.md` carry no frontmatter — they are the repo's front door and the
session's constitution, not pages in the tree.

## Documentation that ships as product

Some user documentation is not a markdown page and must not become one: a public
setup-guide route, in-app legal pages version-locked to an acceptance table, landing-page
FAQs with localisation overlays. That IS the `audience: user` tier — localised, styled,
SEO-visible — and duplicating it into `docs/` creates two copies of customer-facing copy
that will drift, with the shipped one being the copy customers actually read.

The tree handles it by **registration**: `docs/index.md` lists each shipped surface with
a relative link to its source file and a note of where it serves ("public at
`/en/teams-summary-guide`"). A registered surface counts as documented, and the link is
load-bearing, not decorative — `docs-broken-link` resolves it, so renaming the source
file without updating the registry fails the push. A repo whose user tier is entirely
registered is complete, not missing its docs.

## The shipped system is a contract, not just a registration

Registration says the shipped docs exist; the `userdocs-*` rules hold them together. A
repo whose user docs ship as product declares the machinery in `.standards.json` under
`userDocs`: the **registry** file that enumerates every topic (with a pattern capturing
each entry's `slug` and, where the registry records one, its `route`), the **help links**
that carry a user from a page into its topic, and the **routes** its user-facing pages
declare. The reference shape is InvoTrack's `DocRegistry` + `PageHeader HelpSlug`; a
Django app points the same three patterns at a fixtures file and a template include.

Declaring it buys four mechanical guarantees (see the pointer table below): the registry
exists and parses, no help link dangles into a not-found page, every declared user-facing
page links into the docs, and no topic outlives the route it documents. The route globs
are the repo's own statement of which pages are user-facing — auth, admin-only and
marketing surfaces stay out of the globs rather than collecting exemption markers.

The doc-sync discipline itself — change behaviour on a documented page, update the topic
and bump its review date in the same commit — stays judgment, because behaviour-change
versus refactor is not mechanically decidable. The commit stage prints a **note** when
staged changes touch documented pages without touching the registry or any topic content;
the note asks the question, `/code-review` checks the answer.

## Plans are not documentation

An implementation plan is a **working artifact**: executed once, checkbox by checkbox,
then deleted (git history keeps it). It lives in **`plans/` at the repo root**, outside
the docs tree, and the docs rules never scan it — deliberately, because a plan fails them
by nature rather than defect: it names symbols that only exist once its phases are built,
and its prose serves the executor, not a reader with one of the four needs. Keeping plans
in `docs/` costs exemption markers on every future symbol; InvoTrack's stripe plan needed
six under `docs/` and zero in `plans/` (decided 2026-08-18).

The split runs between the plan and its **design spec**: the spec is rationale — a
`type: explanation` page that stays in `docs/` and outlives the plan that implemented it.
A **reusable checklist** (a deploy runbook re-run every release) is documentation too —
`type: how-to`, stays in `docs/` — which is exactly the line the mechanical rule draws:
`docs-plan-page` fires on a `plans/` directory inside `docs/`, or on checkbox steps
combined with plan identity in the filename or title, and a genuine checklist carries
neither.

## What is enforced mechanically

**Never re-verify these by eye; if the push gate is green, they hold.** Each module's
docstring is the definition — repeating it here would be the same rule in two layers, which
is the drift this pack exists to prevent.

- **Structure** (`standards_docs.py`): `docs-missing`, `docs-frontmatter`, `docs-broken-link`
  (no exemption — fix it), `docs-orphan-page`, `docs-plan-page`, `claude-md-length`; plus
  `docs-stale-symbol` from `standards_symbols.py`.
- **Coverage** (`standards_coverage.py`) — documentation that is *missing*:
  `docs-uncovered-command`, `docs-uncovered-env`, `docs-uncovered-route`.
- **The shipped user-docs contract** (`standards_userdocs.py`): `userdocs-missing`,
  `userdocs-dangling-slug`, `userdocs-unlinked-page`, `userdocs-orphan-topic`.
- **The judgment layer itself** (`standards_rules.py`): `rules-scope-declared`,
  `rules-file-length`, `rules-context-budget`.

## Coverage: the rules that point at MISSING documentation

Everything above audits the docs that exist. The three `docs-uncovered-*` rules audit the
other direction — an element a reader will meet that nothing teaches. "Feature" is not
mechanically decidable, so they cover the enumerable proxies: management commands,
`.env.example` keys, and public routes (the last via a repo-declared inventory in
`.standards.json`: `"docsRouteInventories": [{ "file": "...", "pattern": "..." }]`,
group 1 = the route).

"Covered" means the name appears anywhere in the documentation corpus — any `docs/` page,
`README.md` or `CLAUDE.md` — and for an env key, a `#` comment beside it counts: the
comment IS the documentation. Findings ride the normal baseline ratchet, so adoption
grandfathers the existing gaps and a **new** command, key or route must arrive documented
in the same commit.

Two things about how those last two read your prose, because both decide what you may
safely write. **A path may be written relative to its package**, not only to the repo root:
`apps/growth/constants.py` resolves against
`packages/backend/apps/growth/constants.py`, because the package root is the frame a
monorepo's docs actually use. Matching is on whole leading segments, so a genuinely wrong
path still fires. And **an inline code span is never read as a link** — prose documenting
link syntax as `` `[label](url)` `` is prose, not a broken link — while a link whose *label*
is a code span, `` [`page.md`](./page.md) ``, is still checked. That asymmetry matters
because `docs-broken-link` is the one rule with no exemption: a false positive there could
only ever be cleared by rewording something correct.

What stays judgment — and therefore this file's business and `/code-review`'s — is
everything the scanner cannot decide: whether a page serves one need, whether user copy
reads like user copy, whether the docs a change touches were actually updated.

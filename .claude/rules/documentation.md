---
description: What a change owes its documentation, and the constitution-and-index role of CLAUDE.md
alwaysLoad: whether a change owes a doc page is decided while the code is written, and rationale escapes into memory files that are not in the repo at all — neither moment touches a markdown path
---

# Documentation: the third dimension

Code, tests, documentation. The first two are gated; this file is the judgment half of
gating the third. It holds only what is decided **away from a markdown file** — what a code
change owes, and where knowledge is allowed to live. How to write the page itself (the four
types, audience, frontmatter, registration, coverage) is in `docs-authoring.md`, which loads
with `docs/`, `README.md` and the docs config; the mechanical half is
`engineering_standards/standards_docs.py` and `standards_coverage.py`.

## Three audiences, one tree

Every repo serves three readers — the AI doing most of the writing, the developer, and the
product's users — but they do not get three doc sets. They get **one `docs/` tree** and two
access patterns:

- **Push** — `CLAUDE.md` is auto-loaded into every session before any work happens. It is
  the only surface that reaches a reader who does not yet know what to ask.
- **Pull** — everything else. The AI reads `docs/architecture-x.md` on demand exactly as a
  new developer does; there is nothing about an explanation or a how-to that needs to live
  in an AI-specific file.

The consequence is a hard role for `CLAUDE.md`: **constitution and index, never
encyclopedia.** It holds what must be known *unprompted* — prohibitions, conventions a
session would otherwise violate by default, workflow gates, the commands — plus one pointer
line per docs page. Knowledge (architecture, rationale, procedures, history) lives in
`docs/`, where every reader can find it and where it is versioned beside the code it
describes. A repo `CLAUDE.md` over 150 lines is almost certainly holding knowledge, not
constraints: move the knowledge to a typed page and leave the pointer
(mechanical half: `claude-md-length`).

## What a code change owes

**A user-visible feature ships with its `audience: user` page in the same commit as the
code** — this is the doc-before-commit rule applied to the new tree, not a new rule. The
same commit, because a repo that documents afterwards has a window where the code and its
prose disagree, and the window is where the wrong answer gets read.

The enumerable half of this is mechanical and ratcheted (`docs-uncovered-command`,
`docs-uncovered-env`, `docs-uncovered-route`): a **new** management command, `.env.example`
key or public route must arrive documented. What the scanner cannot see — a feature with no
enumerable surface, a page that names the thing but teaches nothing — stays with
`/code-review`.

## Design rationale is documentation

Decisions, architecture rationale, and "why is it built this way" belong in
`type: explanation` pages in the repo — visible, versioned, reviewable — not in Claude
memory files. Memory holds only what cannot live in a repo: cross-repo facts and personal
preferences. When you find rationale drifting into a memory file, that is a page waiting
to be written.

An implementation plan is the opposite case and is **not** documentation: a working
artifact, executed once and deleted, living in `plans/` at the repo root outside the docs
tree. The line between a plan, its design spec and a reusable checklist is in
`docs-authoring.md`.

## The judgment layer costs push-context, so it has a budget

`CLAUDE.md` is not the only file auto-loaded before work starts. A `claude/rules/*.md` file
with no `paths:` frontmatter is too, in every session, and prose is the one layer enforced by
being *attended to* rather than by a gate — so the always-loaded set has a ceiling for exactly
the reason `CLAUDE.md` has one.

**Every rule file declares its load scope**: `paths:` when its subject genuinely is a file
type, or `alwaysLoad: <why>` with a reason at the usual 30-character floor. There is no
exemption marker — `alwaysLoad` *is* the exemption, and its value is the argument. Before
writing judgment prose at all, check the ladder in the pack's `CLAUDE.md`: a rule a compiler,
analyzer or scanner can decide belongs there instead, where it costs no attention. The
reasoning, and what is still owed, is in the pack's `docs/judgment-layer.md`.

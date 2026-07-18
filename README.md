# Bible Study Framework

A method and a small toolchain for doing biblical study **with an AI assistant** without ending up
trusting it.

## Start here

Download or clone this repo, open the folder in your AI coding assistant (Claude Code, GitHub Copilot,
Cursor, Codex — any of them), and paste this:

> Read `AGENTS.md`, `README.md`, `MODES.md` and `LIMITATIONS.md` in this repo.
>
> Then, before we begin: tell me in plain language the ways you are likely to mislead me in biblical
> study, and what pushback you need from me. After that, ask me which operating mode I want to work in.

That's the whole setup. You don't need to know git, you don't need a GitHub account, and you don't need
to read the rest of this page — your assistant will walk you through it.

The rest is for whoever wants to understand what they just started.

---

## What this is

Not a chatbot, not a commentary, not a set of answers. It is a way of working: you study, and every
claim you settle gets written down as one atomic, cited, firmness-marked file that a validator can check
and git can track. Over time the files become a corpus — your readings, distinguishable from everyone
else's, with the reasoning still attached.

**Read [`LIMITATIONS.md`](LIMITATIONS.md).** It describes the specific, repeatable ways an AI assistant
will mislead you in this work. The whole design follows from those failures, and the framework is not
safe to use without knowing them.

## Why it exists

An AI assistant is genuinely good at some parts of biblical study — finding every occurrence of a word,
holding a dozen passages in view at once, laying out how a term is used across a corpus, noticing a
parallel you'd have missed. It is unreliable at exactly the part that matters most: **judgment**.

Left to itself it produces answers that are fluent, coherent, and quietly selective. It reaches for the
texts that support the shape of the answer it is already building, and does not reach for the ones that
complicate it. Nothing is concealed on purpose; the awkward verse simply never comes up.

So the framework does two things:

1. **Constrains the conversation** so omissions and leanings become visible while you work
   ([`MODES.md`](MODES.md)).
2. **Persists what survives** as files, so a claim you settled in March is still there in November,
   still cited, still arguable — instead of being re-derived from scratch, slightly differently, every
   session.

The second point is the one people underestimate. Without a corpus, every conversation starts cold and
the assistant re-invents your conclusions with fresh drift each time. With one, sessions build.

## What's in the box

| File | What it is |
|---|---|
| [`LIMITATIONS.md`](LIMITATIONS.md) | How this will mislead you. Read first, and re-read |
| [`MODES.md`](MODES.md) | The operating modes (Bible Only / Research / Debate) and the output discipline |
| [`AGENTS.md`](AGENTS.md) | Instructions your assistant reads. `CLAUDE.md`, `GEMINI.md` and `.github/copilot-instructions.md` point here |
| [`PACKS.md`](PACKS.md) | Sharing findings with other people, and subscribing to theirs |
| [`build_index.py`](build_index.py) | Regenerates `INDEX.md`; `--check` validates the corpus |
| `findings/` | One atomic claim per file. Yours. Starts empty |
| `threads/` | Open questions that aren't settled claims yet |
| `themes/` | Optional hub pages grouping findings by topic |
| `imported/` | Findings received from other people — read-only, quarantined |

No database. No dependencies. No build step. The Markdown files are the source of truth; `INDEX.md` is
generated and should never be hand-edited.

**Why files and not a database:** the consumer is an AI assistant that reads and greps text, and git
gives you the history of *how a finding firmed up* — often more valuable than the finding itself.

## You do not need a GitHub account

Git works perfectly well with no remote and no account. Your corpus is a folder on your computer with
version history — you get diffs, you can see how a finding changed over two years, you can undo. None
of that requires github.com. Your assistant can handle the git commands; you never have to learn them.

**But a local-only corpus dies with the laptop.** This is the one piece of housekeeping worth caring
about, because losing years of study is the failure that actually hurts. Either keep the folder inside
OneDrive / Dropbox / iCloud, or push it to a private remote once you're comfortable. Ask your assistant
to set one up when you're ready.

## Requirements

Python 3.9+. Nothing else — the validator is standard library only.

```bash
python build_index.py          # regenerate INDEX.md
python build_index.py --check  # validate only; exits non-zero on errors
```

Already installed on macOS and Linux. On Windows: `winget install Python.Python.3.12`, or let your
assistant do it.

`--check` fails on broken `[[wikilinks]]`, unknown edge ids, duplicate ids, missing required fields, and
invalid `status` / `claim_type` / `origin` values. Run it before every commit — it's what stops the
corpus rotting quietly.

## The core idea: one atomic claim per file

A finding is **one claim**. If a note makes three claims, it's three files, linked to each other. This
feels fussy for about a week and then becomes the whole point — because claims can then carry
*relationships*, and the relationships are where the thinking actually lives.

```markdown
---
id: gift-is-for-the-recipient-not-deliverer
title: "A spiritual gift is given to the person receiving it; the deliverer is only the courier"
status: established
claim_type: synthesized
origin: own
themes: [spiritual-gifts]
keywords: [prophecy, directionality, courier]
scripture: ["1 Corinthians 12:7", "1 Corinthians 14:3-4", "Numbers 22:28", "John 11:51"]
related: [office-of-prophet-differs-from-gift-of-prophecy]
builds_on: [some-lexical-tool-finding]
tension_with: [a-finding-that-pulls-the-other-way]
sources: ["Any external scholarship leaned on"]
created: 2026-07-18
updated: 2026-07-18
---

# The gift is addressed to the recipient

**The claim.** Stated plainly, in one paragraph, with the verses.

**Texts that cut against this.** Required. What pulls the other way, and how far it gets.

**Why it matters.** What it unlocks, what it constrains, what it connects to via [[wikilinks]].

**Caveat / open.** What this does *not* settle.
```

Only `id`, `title`, and `status` are required. The filename (without `.md`) **is** the `id` and the
`[[wikilink]]` target.

### The three axes

Independent of each other, and keeping them apart is most of the value:

**`status` — how mature the finding is**

| | |
|---|---|
| `hypothesis` | a hunch, just noticed |
| `developing` | actively gathering evidence |
| `established` | well-supported by multiple lines |
| `firm` | settled; safe to build on |
| `contested` | evidence pulls both ways |
| `retired` | superseded — keep the file, don't delete it |

**`claim_type` — what kind of claim it is**

| | |
|---|---|
| `asserted` | the text explicitly says it |
| `synthesized` | inferred across texts |
| `open` | underdetermined by the evidence |

**`origin` — whose finding it is**

| | |
|---|---|
| `own` | your own reading, worked out through the sessions |
| `mixed` | your distinctive route over non-original material |
| `reused` | standard scholarship, brought in for context |

`origin` is the axis that makes the corpus *yours* rather than a pile of borrowed opinions. It's
interpretive and it is **yours to set** — an assistant may seed `reused` for clearly external material,
but should never stamp `own` or `mixed` on your behalf. Unset is fine, especially on young findings.

### Typed edges

`related` (untyped association), `builds_on` (this depends on X), `tension_with` (this pulls against X —
reconcile it). `tension_with` is the most valuable of the three: it makes an unresolved conflict a
visible, permanent part of the corpus instead of something you quietly forget.

### Threads

Open questions that aren't claims yet live in `threads/<id>.md` with `status: open | deferred |
resolved`. Use one when a question is live but a verdict would be premature. Forcing a verdict early is
the failure a thread exists to prevent.

## What earns a place

**Record your findings, not someone else's.** Reused material earns a file only when it is *load-bearing*
for one of yours — a tool a finding `builds_on`, or a counter it sits `tension_with`. If no such edge
exists, don't write it. Free-floating standard scholarship dilutes the corpus into a commentary.

When a claim is *your use of* standard material, frame the finding around **your claim** and let the
scholarship sit inside it as scaffolding. Don't title it as the lexicon entry.

## Sharing with other people

Findings travel as **packs** — a zip anyone can send any way they like — and imported findings are
quarantined in `imported/<author>/` so your own work stays distinguishable. You can also subscribe to
someone's published pack feed and be told when they update. There is no central registry and nobody
approves anything. See [`PACKS.md`](PACKS.md).

## The workflow

1. Study, in one of the modes ([`MODES.md`](MODES.md)).
2. When something settles, write it: one file, one claim, cited, marked.
3. `python build_index.py && python build_index.py --check`
4. Commit the finding and the regenerated `INDEX.md` together.

The assistant proposes findings; **you** confirm them before anything is written. That ordering is not
politeness — it is the safety model. See [`LIMITATIONS.md`](LIMITATIONS.md).

## Contributing and licence

Bug reports and framework improvements are welcome; theological content is out of scope. See
[`CONTRIBUTING.md`](CONTRIBUTING.md).

The framework was extracted from a working personal corpus and ships **empty** — those findings are the
author's and stay with them. Yours should be yours.

### Licence

Dual-licensed, because the repo is two different kinds of work:

- **Code** (`build_index.py`, `packs.py`, the workflow) — [MIT](LICENSE). Use it, change it, ship it.
- **Documentation** (this file, `LIMITATIONS.md`, `MODES.md`, `PACKS.md`, `AGENTS.md`,
  `CONTRIBUTING.md`) — [CC BY 4.0](LICENSE-DOCS). Adapt it freely; credit the source.

The prose is the substance here, and CC BY means that if you build on `LIMITATIONS.md` in your own
project, the attribution travels with it.

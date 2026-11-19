# Instructions for the AI assistant

This repository is a **biblical study corpus** built on the framework in [`README.md`](README.md).
Most people using it will do everything by talking to you — they may not read a single file in here,
may not know git, and may not have a GitHub account. **You are the interface.** Act accordingly.

Read [`README.md`](README.md), [`MODES.md`](MODES.md) and [`LIMITATIONS.md`](LIMITATIONS.md) before
doing any study work.

`LIMITATIONS.md` documents *your own* systematic failure modes in this work. It is not a disclaimer for
the user's benefit — it is a description of how you will behave unless constrained, and you are expected
to actively work against it.

---

## First session in a new corpus

When `findings/` is empty, or the user seems new, do this before any study:

1. **Check the tooling.** Run `python build_index.py --check`. If Python is missing, say so plainly and
   offer to install it (`winget install Python.Python.3.12` on Windows; it is already present on macOS
   and Linux). Do not proceed to study work with a broken toolchain — the validator is the guardrail.
2. **Explain what this is, in plain language, in a short paragraph.** Not a feature tour. Roughly: *"We
   study; when you settle something, I write it down as one small file with the verses attached, and a
   script checks the files stay consistent. Over time it becomes your own body of work."*
3. **Walk them through your limitations.** Summarize [`LIMITATIONS.md`](LIMITATIONS.md) — at minimum
   that you omit texts which complicate your answers, that you hedge asymmetrically on contested
   questions, and that a confident well-cited answer from you is not evidence the answer is right. Tell
   them the pushback you need from them. **Do not skip this because it is awkward or because they seem
   eager to start.** They will not read the file on their own, and they are the person most exposed to
   what is in it.
4. **Ask which mode they want** (see [`MODES.md`](MODES.md)) rather than defaulting into unmarked
   conversation. Everything downstream inherits from this choice.

## Hard rules

- **Never write a finding the user has not confirmed.** Propose first — id, title, status, claim_type,
  themes, citations, edges — and wait. The user draws conclusions; you do the legwork.
- **Never set `origin: own` or `origin: mixed` on the user's behalf.** That axis is theirs to mark. You
  may seed `reused` for clearly external scholarship. Leaving it unset is correct when unsure.
- **Never hand-edit `INDEX.md`.** It is generated. Run the tooling.
- **Never commit or push without an explicit instruction.** Each is its own gate, and each instruction
  authorizes exactly one action.
- **Flag every lexical judgment** (Hebrew/Greek) and **every step outside the biblical text**, out loud,
  as it happens.
- **Verify factual claims rather than reciting them** — verse references, what a translation says, how
  rare a word is. Claims of the form "no translation says X" or "this word occurs twice" are checkable
  in a minute and wrong often enough to matter. Flag anything asserted from memory as unverified.
- **Do not render conclusions.** Gather, organize, mark, hand over.

## The output discipline

Every non-trivial claim carries:

- a **"Texts that cut against this"** section that is **not empty** — "none found" only with a statement
  of where you looked;
- a **firm / synthesis / open** marking (`claim_type`), and **verse citations** — marking without a
  citation is not marking.

Run an **adversarial pass separate** from the pass that built the finding, whose only job is to attack it
and hunt disconfirming texts.

## Before any study session in an existing corpus

**Read `INDEX.md` first**, then open the findings it points to. The corpus exists so sessions build on
each other instead of starting cold. Re-deriving a conclusion that is already filed — and deriving it
slightly differently — is the failure this repo exists to prevent.

## After editing or adding findings

```bash
python build_index.py
python build_index.py --check   # must pass
```

`--check` fails on broken `[[wikilinks]]`, unknown edge ids, duplicate or mismatched ids, missing
required fields, and invalid `status` / `claim_type` / `origin`. Commit the finding and the regenerated
`INDEX.md` together — never in separate commits, so the repo never has a window where they disagree.

## Corpus conventions

- **One atomic claim per file.** Several claims → several files, linked.
- **`related` / `builds_on` / `tension_with` accept finding ids only** — not thread ids. Link a thread
  from the body with a `[[wikilink]]` instead.
- **Reused material earns a file only when load-bearing** for one of the user's findings — a tool a
  finding `builds_on`, or a counter it sits `tension_with`. Otherwise don't write it.
- **Frame a finding around the user's claim**, letting external scholarship sit inside it as
  scaffolding. Don't title a finding as a lexicon entry.
- **Open questions are threads, not findings.** `threads/<id>.md`, `status: open | deferred | resolved`.
  A `deferred` thread is parked on purpose — don't work it until the user calls for it. Forcing a
  verdict early is exactly what a thread prevents.
- **`retired` findings are kept, not deleted.** The history of a claim is part of its value.

## Imported findings

Findings received from other people live in `imported/<author>/` and are **read-only** — see
[`PACKS.md`](PACKS.md). Never edit them, never move them into `findings/`, and never re-stamp their
origin. To use one, help the user write *their own* finding that `builds_on` or `tension_with` it.

## Feeds, private links and sync — which is which

Three purposes, three tools. **The table decides; never pick a tool by guessing.**

| Purpose | Tool | Direction | Who can read |
|---|---|---|---|
| A public feed | `packs.py` | one way, read-only into `imported/` | anyone; findable in a public list |
| A private feed | `packs.py` | one way, read-only into `imported/` | whoever the publisher gave a link |
| Your own devices | `sync.py` | two-way, your own `findings/`, `themes/`, `threads/` | you, signed in |

- **A user wanting their own work on another computer needs `sync.py`**, never a subscription to their
  own feed — that would land a second copy of every finding in `imported/`. If `pull` warns that a
  pack looks like their own work, explain this rather than carry on.
- **A private feed's link ends in `#key=…`, and the key is a secret.** Pass the link the user gives
  you straight to `python packs.py subscribe '<link>'` and nothing else. Never print it back, never
  quote it in a summary, never write it into any file (not `subscriptions.json`, a note, a finding, a
  script or a commit message), and never commit it. `packs.py` keeps the key in the user profile
  and never prints it; keep it that way in what you say.
- When `check` or `pull` says a feed *needs its link on this computer*, ask the user for the link —
  do not look for the key anywhere else. When it says the stored key is not accepted, the link may
  have been revoked: tell the user to ask the publisher for a new one.

## Sync conflicts

If the corpus syncs with a server ([`SYNC.md`](SYNC.md)), `python sync.py run` may leave a
`<id>.md.remote` file beside a finding: the same claim was edited in two places. **Resolving it is the
user's call, like any other edit to a finding.** Show both versions, propose a resolution, wait for
confirmation; then write the `.md`, delete the `.remote` file and sync again. Never pick the newer one
by rule, and never delete a `.remote` file without the user's say — that is what accepts the `.md`.

## Engineering standards (for anyone editing the tooling, not the corpus)

The Python under `build_index.py` / `packs*.py` / `study_corpus.py` / `study_frontmatter.py` /
`study_sections.py` / `study_words.py` / `user_secrets.py` is
checked by the shared standards pack. (`word_sets.json` beside them is data: the study's display
words per word set and language, read by `study_words.py` and consumed verbatim by SourceText and
Jack's Corner; stored codes never change.) **This only concerns changes to the *code*** — writing
findings and threads is unaffected.

The verification is `python engineering_standards/verify.py` — one definition, three callers.
**Do not run it by hand; the git hooks run it.** Wire them once per clone (a fresh clone has no
gate until you do):

```bash
git config core.hooksPath engineering_standards/hooks
```

- `pre-commit` checks the staged files; `pre-push` runs the fast stage (the scan of what the
  branch changed since `origin/main`); CI runs the full stage (`verify.py --ci --strict`: the
  whole-tree scan, the pay-down check and the dependency gate). A red CI on a branch whose
  code you did not touch is most often a new release of a pinned tool — see `/update-deps`.
- `engineering_standards/` is owned by the pack and replaced whole by `/apply-standards`;
  never edit a file in it here. It is one importing package, so a partial copy fails as an
  `ImportError` at commit time.
- **There is no baseline file, and there must not be one.** Every file meets the limits
  outright, so the checker passes from a clean slate and any failure belongs to the change
  under test. When a file crosses 500 lines, name its second responsibility and split it —
  that is exactly how `study_corpus.py` and `study_sections.py` came out of `build_index.py`,
  and `study_frontmatter.py` out of `study_corpus.py`.- `.claude/rules/` carries the judgment half, auto-loaded by path. It is copied verbatim from
  the pack and covers stacks this repo does not use; that is deliberate — an edited copy would
  drift from its source, which is the failure the pack exists to prevent.
- **Work on a branch, never directly on `main`.** Every branch push runs the full check in
  GitHub Actions (`.github/workflows/check.yml`); merge into `main` only when the branch's
  run is green. There is no pull-request trigger, so a fork's contribution is checked once
  it is pushed to a branch here. Nothing deploys from this repository,
  so the check is the whole of CI. After a push, read the job's conclusion in Actions rather
  than `gh run watch --exit-status`, which exits 1 when any job is skipped.
- CI runs the same standards check before the tooling tests and the corpus validation.

**The tooling is mirrored, not shared** (see the note in `CONTRIBUTING.md`): these modules and
their tests (`test_study_text.py`, `test_study_sections.py`, `test_packs.py` with
`packs_testserver.py`), together with `packs_archive.py`, `packs_follow.py`, `packs_feed.py` and
`user_secrets.py` (the per-user secret store `packs.py` and `sync_state.py` share), also live in
Jack's private corpus repo, and the only intended difference is the `origin` author slot — `own`
here, the author's name there (in `study_corpus.py`'s `VALID_ORIGINS` and `study_sections.py`'s
origin section). A fix to one must be ported to the other, or the two definitions of "valid"
drift apart. The optional sync modules (`sync.py`, `sync_http.py`, `sync_plan.py`,
`sync_state.py`, with `test_sync.py`, `test_sync_login.py` and `sync_testserver.py`) are copied
**unchanged** wherever a corpus syncs, and need `user_secrets.py` beside them; `sync_plan.py`
finds the frontmatter parser in either layout.

## Housekeeping the user cannot do themselves

They may not know git. Periodically, without being asked:

- **Remind them their corpus is not backed up** if the repo has no remote. A local-only corpus dies with
  the laptop, and this is years of their work. Suggest a synced folder or a private remote.
- **Commit for them when they ask**, explaining in one line what a commit is the first time.

# Feeds and sync — bible-study-framework plan

> **Execution note.** Produced under Fable-plan / Opus-implement mode on 2026-11-13.
> Implementation is by **Claude Opus 5 in a separate session, normally AFK**. **Do NOT
> auto-continue into implementation when this plan is accepted.** The decisions below are
> settled with Jack; do not re-ask them. This file is a working artifact: delete it in the
> commit that completes the last step.

One of three sibling plans with the same name: `sourcetext.ai` (the server, which owns the
wire contract), this repo (the client), and `jacks_corner` (the worked example, whose
`study/` tooling mirrors this repo's). Work only in this repo, on branch `feeds-and-sync`.
`jacks_corner` copies its tooling from that branch when this plan is done, so **keep the
branch name and report the final commit**.

## Why

The framework can follow a feed (`packs.py subscribe|check|pull`) and can sync a reader's
own corpus (`sync.py`, `SYNC.md`). SourceText is gaining feeds that are first-class,
private or public, with a revocable link for private ones. The client has to:

- read the new feed addresses,
- follow a private feed without ever writing its secret into `subscriptions.json`, which
  the documentation tells readers to commit,
- explain the three purposes clearly, since today a reader cannot tell which tool is for what.

## The three purposes (put this in the docs as written)

| Purpose | Tool | Direction | Who can read |
|---|---|---|---|
| A public feed | `packs.py` | one way, read-only into `imported/` | anyone; findable in a public list |
| A private feed | `packs.py` | one way, read-only into `imported/` | whoever the publisher gave a link |
| Your own devices | `sync.py` | two-way, your own `findings/`, `themes/`, `threads/` | you, signed in |

A reader never follows their **own** feed: the same findings would land in `imported/`
beside the originals. Their own work moves by sync.

## The wire contract (owned by the SourceText plan; any server may implement it)

| Call | Answer |
|---|---|
| `GET {site}/feeds/{code}/feed.json` | `{"author", "author_id", "feed": {"code", "title", "description"}, "packs": [{"pack", "title", "version", "url", "license", "findings"}]}` |
| `GET` a pack's `url` | the zip |

- A public feed needs no credential.
- A private feed needs `Authorization: Bearer <key>` on both calls. Without a valid key the
  server answers `404`, the same as for a feed that does not exist.
- The link a publisher hands out is one string:
  `https://<site>/feeds/{code}/feed.json#key=<key>`. The part after `#` is never sent to a
  server.
- Older addresses of the form `https://<site>/packs/<name>/feed.json` keep working and
  carry no key.
- `feed` is optional in the document: a feed without it is read as before.

## Pre-made decisions

1. **Port the four-module split first.** `jacks_corner/study/` has `study_frontmatter.py`
   split out of `study_corpus.py`, and its `packs.py` imports its directory and manifest
   constants from `study_corpus` instead of defining them. This repo has not got that
   split, and the standing rule is to port it before the next fix crosses between the
   repos. Port it from `../jacks_corner/study/` (read-only source), including
   `test_study_sections.py` and the `unresolved_sync_conflicts()` piece of `build_index.py`
   if it is missing here. **The only intended difference between the two copies is the
   `origin` author slot** (`own` here, the author's name there) — check every ported file
   for it and keep `own`. After this step `packs.py`, the `study_*.py` modules, the
   `sync*.py` modules, their tests and `SYNC.md` must differ from `jacks_corner`'s in that
   slot and nothing else.
2. **`subscribe` takes the link as pasted.** If it has a `#key=` fragment, the key is split
   off; `subscriptions.json` records the address **without** the fragment, exactly as for a
   public feed. A feed entry never contains a key.
3. **The key is stored where the sync sign-in is stored**: the per-user store in
   `sync_state.py` (`%APPDATA%\bible-study-framework\…` protected with DPAPI on Windows,
   `~/.config/bible-study-framework/…` readable by the user only elsewhere). Reuse that
   mechanism; do not write a second one. If `packs.py` cannot use it without importing
   sync-only code, move the storage primitive into a small module both import, and keep
   the dependency arrow one way. Keys are stored per feed address.
4. **Fetching.** HTTPS only, as today. The key is sent as `Authorization: Bearer` only to
   the host of the feed address, and **never forwarded across a redirect to another host**
   — refuse a cross-host redirect when a key is in play. A pack `url` on a different host
   than its feed is fetched without the key.
5. **A key is never printed**, not in `check`, `pull`, an error, or a traceback. Where a
   feed is named in output, print the address without the fragment.
6. **A second computer.** A private feed in `subscriptions.json` with no key on this
   computer is reported by `check` and `pull` as "this feed needs its link on this
   computer: `python packs.py subscribe <link>`" and the run continues with the other
   feeds. `subscribe` on an address already listed stores the key and changes nothing else.
7. **A `404` on a feed that has a stored key** says the link may have been revoked and to
   ask the publisher, instead of today's "not valid JSON" message. A `404` on a keyless
   feed says the feed does not exist or has nothing published.
8. **`unsubscribe`**: add it if it does not exist; it removes the entry and the stored key.
   Findings already in `imported/` stay.
9. **Following your own feed gets a warning, not a refusal.** The client cannot know whose
   feed an address is, so the check is the cheap one: when every finding id in a pulled
   pack already exists in `findings/`, say that this looks like the reader's own work and
   that their own work moves by `sync.py`. The pull still completes.
10. **Sync is unchanged on the wire.** `sync.py login` already registers as
    `bible-study-framework sync (<host>)`; add `--name <label>` to override the host label,
    and print, before the browser opens, the name the server will list this computer under.
11. **Tests**: this repo has no test for `packs.py`. Add `test_packs.py` in the style of
    `test_sync.py`, driving a local test server (extend `sync_testserver.py` or add a small
    sibling; keep each file under the source limit). Cover: public feed; private feed with
    key; wrong key; key never written to `subscriptions.json`; key never in captured
    output; cross-host redirect refused; missing key on a second computer; legacy
    `/packs/<name>/` address; `unsubscribe`.
12. **Stdlib only.** No new dependency, as everywhere in this repo.

## Steps

One commit per step; tests first.

1. Port the four-module split (decision 1). Verify: `python build_index.py --check`, the
   existing tests, and a file-by-file diff against `../jacks_corner/study/` showing only
   the `origin` slot.
2. Key storage shared between `packs.py` and sync (decision 3). Verify: `test_sync_login.py`
   still passes; a new test stores and reads a feed key.
3. `packs.py`: new addresses, link parsing, authenticated fetch, messages, `unsubscribe`
   (decisions 2, 4–9). Verify: `test_packs.py`.
4. `sync.py login --name` (decision 10). Verify: extend `test_sync_login.py`.
5. Documentation, in the same commits where practical and completed here: `PACKS.md`
   (feeds, private links, where the key lives, what to commit), `SYNC.md` (the `--name`
   flag; a pointer to the three purposes), `README.md` (the three-purposes table),
   `LIMITATIONS.md` if it mentions sharing, `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` /
   `.github/copilot-instructions.md` where they describe packs or sync (an assistant must
   never print or commit a feed key, and never resolve which tool to use by guessing — the
   table decides). Keep the wording free of anything specific to one server.
6. Delete this plan file.

## Stop conditions

- The contract above does not fit what `packs.py` can do. Stop and report; do not bend it.
- The port in step 1 would change behaviour here beyond the module boundaries, or the two
  copies differ in ways that are not the `origin` slot and not obviously a missed port.
  List the differences and stop — which side is right is Jack's call.
- Anything would require pushing. This repo is public.

## AFK execution requirements

- Branch `feeds-and-sync`, commits per step, never push.
- The repo's git hooks run the gates on commit; do not run the whole gate by hand. Run one
  test file at a time while editing.
- Nothing here needs Docker or the network; every test uses a local test server.
- Final report: the final commit id on `feeds-and-sync`, what was ported, anything skipped.

## For Jack

- None that block the work. Review the public-facing wording of `README.md` and `PACKS.md`
  before pushing.

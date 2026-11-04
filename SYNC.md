# Sync — keeping this corpus and a server in step

**Optional. Nothing in the framework needs it.** A corpus that never syncs is exactly the corpus it
was before this file existed: `build_index.py`, the findings, packs and everything else work with no
server, no account and no network. `sync.py` is one more command, for people who also keep their
findings in a tool that speaks the protocol below — [SourceText](https://sourcetext.ai) is one; any
server may implement it.

Sync is **two-way** and **never last-write-wins**. Every item is compared three ways: the file here,
the item on the server, and how both looked the last time they agreed (the *base*).

| Here | Server | What happens |
|---|---|---|
| unchanged | unchanged | nothing |
| changed | unchanged | your file is pushed (or the item deleted on the server) |
| unchanged | changed | the server's copy is pulled (or your file deleted) |
| changed | changed, to the same thing | recorded; nothing moves |
| changed | changed, differently | **a conflict** — see below |

"The same thing" means the framework's own parser reads the same frontmatter and the same body out of
both — a server is allowed to re-quote a title, reorder keys or stamp `updated:`. One changed word or
one changed `status` is a difference.

## Setting it up

```bash
python sync.py add sourcetext https://sourcetext.ai --corpus bible
python sync.py login            # once on each computer — approve in the browser
python sync.py run --dry-run    # see what would move
python sync.py run
python build_index.py           # if anything was pulled
```

`add` writes `.sync/remotes.json` — commit it, it holds no secret. `login` opens your browser on the
server's own sign-in page; you approve there, and the browser hands a one-time code back to this
script on `127.0.0.1`. Your password never passes through the script. The sign-in is stored in **your
user profile**, never in the corpus (`%APPDATA%\bible-study-framework\sync\` on Windows, encrypted
for your Windows user; `~/.config/bible-study-framework/sync/` elsewhere, readable by you only). So a
corpus can be pushed, zipped or shared without carrying it, and each computer signs in once.
`python sync.py logout` revokes this computer's sign-in.

### What to commit

- `.sync/remotes.json` and `.sync/<remote>.state.json` — **commit both.** The state file is the base.
  Committed, a second computer that clones or pulls the corpus has the same base as the first, so its
  first sync moves only what really changed.
- `<file>.md.remote` conflict files — commit them if you commit mid-conflict, so every clone knows the
  conflict is open. `build_index.py --check` fails while one exists, which is deliberate.
- `.sync/*.report.md` — not committed (ignored); it is the last run's report.

**With two computers, `git pull` before you sync.** Syncing on a clone that is behind is safe — it can
never lose an edit — but the files it pulls will then meet the same edits arriving through git.

## Conflicts

When both sides changed and they disagree, nothing is overwritten. The server's copy is written beside
yours as **`<file>.md.remote`** (or a short note, if the server deleted it), the report lists it, and
`sync.py run` exits non-zero until it is resolved. To resolve:

1. Make the `.md` say what it should — yours, theirs, or both combined. To take the server's copy,
   copy the `.remote` file over the `.md`.
2. Delete the `.remote` file.
3. `python sync.py run` — the `.md` is pushed, replacing the server's copy.

A deletion against an edit is a conflict too. To keep the edited copy, delete the `.remote` file. To
accept the deletion, delete both the `.remote` file and the `.md`.

For an assistant resolving one: a conflict is two edits to the same claim, and choosing between them
is the reader's judgment, like any other change to a finding. Show both, propose, and wait.

## What moves, and what does not

- **Moves:** `findings/`, `themes/`, `threads/` — files whose id is lowercase letters, digits and
  dashes (anything else is skipped and named).
- **Never moves:** `imported/` (other people's work is not yours to publish), `INDEX.md` (generated),
  anything else.
- **Pushed byte for byte.** Your file is sent exactly as it is on disk and is *not* rewritten after a
  push, even when the server stores it re-serialised; frontmatter keys the server does not know about
  survive the round trip.
- **Pulled verbatim.** A pulled item is written exactly as the server sends it.
- Line endings and a byte-order mark are not edits: a computer whose git turns LF into CRLF does not
  see every file as changed.

An interrupted run is safe to repeat. Every file is written atomically and the base is saved after
each item; an item that moved but was not yet recorded is found identical on both sides next time.

## The protocol

Written for anyone implementing a server. `{root}` is the server's root address, e.g.
`https://sourcetext.ai`. HTTPS is required except for a server on this computer (`localhost`).

### Signing in — OAuth 2.1, public client

1. `GET {root}/.well-known/oauth-authorization-server` (RFC 8414) — needs `authorization_endpoint`,
   `token_endpoint`, `registration_endpoint`; `revocation_endpoint` is used when present.
2. Register once (RFC 7591): `redirect_uris: ["http://127.0.0.1/callback"]` (no port),
   `grant_types: ["authorization_code", "refresh_token"]`, `token_endpoint_auth_method: "none"`,
   `scope: "study"`. The server must accept any port on that loopback URI (RFC 8252 §7.3).
3. Authorise with PKCE S256, `scope=study` and `resource={root}/api/sync/v1` (RFC 8707); exchange
   the code with the same `resource`. Refresh tokens may rotate; the client stores each new one at
   once.

### The API — `{root}/api/sync/v1/{corpus}/…`, `Authorization: Bearer <token>`

`{kind}` is `finding` | `theme` | `thread`; `{slug}` is the file's id, `[a-z0-9-]{1,160}`.

| Call | Answer |
|---|---|
| `GET changes?since=<cursor>` | `{cursor, items: [{kind, slug, version, deleted, sha256, updated_at}]}`. No `since`: everything that exists now. With `since`: every item changed after it, once, in its current state. `410` if the cursor is unknown — the client compares everything again |
| `GET {kind}/{slug}` | the item as a framework Markdown file; `ETag: "<version>"`, `X-Content-SHA256`; `404` when absent |
| `PUT {kind}/{slug}` | body = the Markdown file. `If-None-Match: *` creates, `If-Match: "<version>"` updates → `{version, sha256, markdown}` |
| `DELETE {kind}/{slug}` | `If-Match: "<version>"` → `204` |

`version` and `cursor` are opaque strings. `sha256` is over the exact bytes `GET` returns.

A stale precondition is **`409 {"error": "edit_conflict", "current": {version, sha256, markdown}}`**
(`current: null` when the item no longer exists) — never an overwrite. A delete refused because other
findings build on the item is `409 {"error": "has_dependents", "dependents": [...]}`. `401` means
refresh, then sign in again; `429` carries `Retry-After`. Every error body has a `message` sentence,
which the client shows as it is.

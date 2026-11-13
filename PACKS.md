# Packs — sharing findings with other people

A **pack** is a zip file of findings you can send however you like: email, a shared drive, a USB stick,
a link on your own website. There is no registry, no account, and nobody approves anything.

```bash
# make one
python packs.py export --out my-pack.zip --title "Spiritual gifts" \
    --author "Your Name" --author-id yourname --version 2026-07-18

# receive one
python packs.py import their-pack.zip
```

## The rule that makes this safe

**Imported findings never become your findings.** They land in `imported/<author-id>/`, read-only, and
are listed in `INDEX.md` under their author's name in a section of their own.

This is not tidiness. The `origin` axis exists so that your corpus can answer "which of these readings
are *mine*" years from now. Merging someone else's work into `findings/` destroys that permanently, and
no amount of careful labelling afterwards recovers it.

**To actually use an imported finding, write your own finding that `builds_on` or `tension_with` it.**
That is the same rule the framework already applies to any borrowed material: it earns a place when it
is load-bearing for something of yours, and not otherwise. An imported finding that never earns an edge
is visible dead weight you can drop.

An imported id **is** a valid edge and `[[wikilink]]` target from your own findings and threads, which
is what makes the rule above workable rather than merely stated.

Imported findings live in a separate namespace and are **not validated** against your corpus — their
internal edges point at ids inside their own pack, which you may not have. `--check` ignores them.

### Adoption — when you come to believe one yourself

Building on an imported finding is the normal route, and it stays the right one while the claim is
still someone else's. Sometimes it stops being someone else's: you work it, check the verses, and
conclude it is simply true. Then writing your own finding that says so is the honest outcome — which
is what the rule above already prescribes, only now the wording you would write is already in front
of you.

A tool that manages a corpus for you may automate that as **adoption**: copy the imported finding
into `findings/` as your own, and record where the copy came from in an `adopted_from:` key, whose
value is `<author-id>/<original-id>`. Two conventions keep the copy honest:

- **`origin` is copied verbatim, never rewritten.** It names whose reading this is, and adopting a
  finding does not make its reasoning yours. A copy arriving with `origin` reset to your own name
  would be false on the first hop and unrecoverable on the second.
- **`status` may be chosen, but never above the original's.** Copying cannot mint maturity: a
  `hypothesis` you adopt is still a hypothesis, however convinced you are. Working it up afterwards
  is ordinary study, and then the status is yours to raise.

`adopted_from` is an ordinary frontmatter key. Nothing in this repo requires it, reads it, or acts
on it, and a corpus that has never seen one is unaffected — the parser carries unknown keys through
untouched. It is written down here so that when a finding in someone's pack carries one, you know
what it is telling you.

## The format

A pack is a zip containing exactly two things:

```
pack.md            # the manifest
findings/*.md      # the findings, unchanged
```

Nothing else is extracted. `import` refuses any other member — including path traversal attempts and
non-Markdown files — and prints what it refused.

### `pack.md`

Ordinary frontmatter, same as a finding, so no new format to learn:

```markdown
---
pack: yourname-spiritual-gifts
title: "Spiritual gifts"
author: "Your Name"
author_id: yourname
version: 2026-07-18
license: "CC BY 4.0"
homepage: https://example.com/study
findings: 12
---

# Spiritual gifts

What this pack covers, what state it's in, and anything a reader should know before leaning on it.
```

`pack`, `title`, `author` and `author_id` are required. `author_id` becomes the directory name under
`imported/`, so it must be letters, digits, dashes or underscores.

If you omit `--pack`, the id is derived from **what is in the pack**, so a second pack never collides
with your first: `<author-id>-<theme>` when exporting by theme, `<author-id>-all` for a whole-corpus
export, and a slug of the title for an explicit list of findings.

**Set a licence.** Findings are writing, and without a stated licence a recipient has no clear right to
use or redistribute them. CC BY 4.0 is a reasonable default — it lets people build on your work while
requiring they credit you. Whatever you choose, say it.

**Use a date as the version.** It sorts, it's meaningful, and it tells a reader how stale the pack is.

### Selecting what goes in

```bash
python packs.py export --out gifts.zip --themes spiritual-gifts \
    --title "Spiritual gifts" --author "Your Name" --author-id yourname
python packs.py export --out two.zip --findings first-finding,second-finding ...
```

Exporting everything is fine, but a themed pack is usually more useful to a reader than your entire
corpus — and it lets you share the settled parts while keeping your half-formed ones private.

## Subscriptions

If someone publishes a **feed** — a JSON file at a URL — you can follow it and be told when their
packs change. Which tool is for what:

| Purpose | Tool | Direction | Who can read |
|---|---|---|---|
| A public feed | `packs.py` | one way, read-only into `imported/` | anyone; findable in a public list |
| A private feed | `packs.py` | one way, read-only into `imported/` | whoever the publisher gave a link |
| Your own devices | `sync.py` | two-way, your own `findings/`, `themes/`, `threads/` | you, signed in |

**Never follow your own feed.** The same findings would land in `imported/` beside the originals. Your
own work moves between your computers by [sync](SYNC.md). If you pull a pack whose findings are all
already in your `findings/`, `pull` says it looks like your own work — and still imports it, since it
cannot know whose feed an address is.

```bash
python packs.py subscribe https://example.com/feeds/k3x9q/feed.json
python packs.py check     # what's new?
python packs.py pull      # fetch and import updates
python packs.py unsubscribe https://example.com/feeds/k3x9q/feed.json
```

`check` compares each offered version against what you already have in `imported/`, so you find out when
something you're standing on has moved. That is the real reason to bother with versions at all: a
finding you imported as `established` may since have been marked `retired` by its author, and you would
otherwise never know.

`pull` only updates packs you already have, unless you pass `--all`. A feed that cannot be read is
reported and skipped; the others are still checked, and the command exits non-zero so a script notices.

`unsubscribe` removes the feed from `subscriptions.json` and forgets its key. Findings you already
imported from it stay in `imported/`; delete that folder yourself if you no longer want them.

Older addresses of the form `https://<site>/packs/<name>/feed.json` keep working, without a key.

### Private feeds

A private feed is read only by people its publisher gave a **link**. The link is the feed's address
with the key on the end:

```bash
python packs.py subscribe 'https://example.com/feeds/k3x9q/feed.json#key=…'
```

Paste the whole link, in quotes. What happens to it:

- **`subscriptions.json` records the address only** — without `#key=…`, exactly as for a public feed,
  plus `"private": true`. That file is safe to commit and share; it never holds a key.
- **The key is kept in your user profile on this computer**, beside the sync sign-in:
  `%APPDATA%\bible-study-framework\feeds\` on Windows, encrypted for your Windows user;
  `~/.config/bible-study-framework/feeds/` elsewhere, readable by you only.
- **The key is sent only to the feed's own host**, as an `Authorization` header — the part after `#`
  in a link is never sent to any server. A pack hosted somewhere else is fetched without it, and a
  redirect to another host is refused rather than followed with the key.
- **The key is never printed** — not by `check`, `pull`, or an error. Output names the address.

**On a second computer** the cloned corpus lists the feed but has no key, so `check` and `pull` say
*this feed needs its link on this computer* and carry on with the others. Run `subscribe` with the
same link there; it stores the key and changes nothing else.

**If a link stops working**, `check` says the feed does not accept the stored key: the publisher may
have revoked the link. Ask them for a new one and `subscribe` with it. (A private feed answers exactly
like a feed that does not exist when the key is wrong — that is deliberate, so nobody can probe for it.)

Anyone holding the link can read the feed, so treat it like a password: send it privately, never paste
it into a file you commit, and never into a chat with an assistant you would not hand the key to.

### Publishing a feed

Host a `feed.json` anywhere you can serve a file over HTTPS — your own site, a static host, object
storage, or a server that hosts feeds for you. A private feed needs a host that checks the key; a
plain file host can only serve a public one.

```json
{
  "author": "Your Name",
  "author_id": "yourname",
  "feed": {"code": "k3x9q", "title": "My study notes", "description": "Optional."},
  "packs": [
    {
      "pack": "yourname-spiritual-gifts",
      "title": "Spiritual gifts",
      "version": "2026-07-18",
      "url": "https://example.com/study/packs/spiritual-gifts.zip",
      "license": "CC BY 4.0",
      "findings": 12
    }
  ]
}
```

Update the `version` when you republish a pack. That single field is what tells subscribers there is
something new. The `feed` object is optional; a feed without it is read the same way.

A private feed answers both the feed and its packs only with `Authorization: Bearer <key>`, and `404`
otherwise — the same answer as for a feed that does not exist. Hand readers the link
`<feed address>#key=<key>`.

## Deliberately not built: a central registry

There is no index of all packs, and there will not be one. The moment a single registry exists, its
owner becomes the arbiter of which theological findings are acceptable to publish — and every available
answer to that is bad. Host it and you appear to endorse it; remove it and you are adjudicating
doctrine; write acceptance criteria and you have authored a statement of faith.

Subscriptions avoid the problem entirely. Trust is per-person, decided by each reader, and no one sits
in the middle. Share pack files directly, or publish a feed and let people choose to follow it.

A server that hosts feeds may list its own public ones. That list is the host's, not this
framework's: nothing here reads it, and you follow a feed by its address whoever hosts it.

## Safety notes

A pack is a file from someone else, so `import` is careful with it:

- **Only `pack.md` and `findings/*.md` are extracted.** Everything else is refused and reported.
- **Path traversal is blocked** — absolute paths, drive letters, and `..` segments are rejected rather
  than sanitised, so a malicious archive cannot write outside `imported/`.
- **Feeds and downloads must be HTTPS** (plain HTTP only to this computer, for testing a local
  server), and downloads are size-capped. A redirect must stay on
  HTTPS too, and while a private feed's key is in play it must stay on the same host.
- **Nothing is written until you confirm**, and you see the author, version, licence and finding count
  first.

None of that makes the *content* trustworthy. A pack is someone else's reading of scripture, arriving
with all of their assumptions and — if they used an AI assistant — all of its drift. Read imported
findings the way you would read any other author: check the verses, and notice what they left out.

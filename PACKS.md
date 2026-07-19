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

Imported findings live in a separate namespace and are **not validated** against your corpus — their
internal edges point at ids inside their own pack, which you may not have. `--check` ignores them.

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

If someone publishes a **feed** — a JSON file at a URL they control — you can follow it and be told when
their packs change.

```bash
python packs.py subscribe https://example.com/study/feed.json
python packs.py check     # what's new?
python packs.py pull      # fetch and import updates
```

`check` compares each offered version against what you already have in `imported/`, so you find out when
something you're standing on has moved. That is the real reason to bother with versions at all: a
finding you imported as `established` may since have been marked `retired` by its author, and you would
otherwise never know.

`pull` only updates packs you already have, unless you pass `--all`.

### Publishing a feed

Host a `feed.json` anywhere you can serve a file over HTTPS — your own site, a static host, object
storage. You host your own packs; nobody else is responsible for them.

```json
{
  "author": "Your Name",
  "author_id": "yourname",
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
something new.

## Deliberately not built: a central registry

There is no index of all packs, and there will not be one. The moment a single registry exists, its
owner becomes the arbiter of which theological findings are acceptable to publish — and every available
answer to that is bad. Host it and you appear to endorse it; remove it and you are adjudicating
doctrine; write acceptance criteria and you have authored a statement of faith.

Subscriptions avoid the problem entirely. Trust is per-person, decided by each reader, and no one sits
in the middle. Share pack files directly, or publish a feed and let people choose to follow it.

## Safety notes

A pack is a file from someone else, so `import` is careful with it:

- **Only `pack.md` and `findings/*.md` are extracted.** Everything else is refused and reported.
- **Path traversal is blocked** — absolute paths, drive letters, and `..` segments are rejected rather
  than sanitised, so a malicious archive cannot write outside `imported/`.
- **Feeds and downloads must be HTTPS**, and downloads are size-capped.
- **Nothing is written until you confirm**, and you see the author, version, licence and finding count
  first.

None of that makes the *content* trustworthy. A pack is someone else's reading of scripture, arriving
with all of their assumptions and — if they used an AI assistant — all of its drift. Read imported
findings the way you would read any other author: check the verses, and notice what they left out.

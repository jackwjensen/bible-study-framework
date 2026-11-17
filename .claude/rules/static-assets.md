---
description: A file a deploy can change is served at an address that changes with it; behind Cloudflare the origin's cache headers are not what the browser gets
paths: ["**/*.html", "**/*.htm", "**/*.php", "**/*.razor", "**/*.cshtml", "**/*.twig", "**/nginx*.conf", "**/vite.config.*"]
---

# Static assets: the address is the version

Reasoning, the survey and the incident: [engineering-standards/docs/static-assets.md](https://github.com/jackwjensen/engineering-standards/blob/master/docs/static-assets.md).
The mechanical half is `asset-unversioned`: a page template linking a local `.css`, `.js` or
`.mjs` with no query stamp and no hashed filename. Not restated here.

**A browser holding a cached file does not ask whether it changed, so a new deploy reaches it
only at a new address.** Every site here is behind Cloudflare, whose Browser Cache TTL
overrides the origin: sourcetext.ai's nginx said "always revalidate" and browsers were told
four hours (2026-11-11). Cache headers are the second layer, never the mechanism.

1. **Version by address, one of two ways.** A bundler's hashed filename (Vite, webpack — free,
   and correct by construction), or a `?v=` stamp taken from the file's mtime, its content
   hash, or the build. allegroit-dk (`filemtime`), jacks_corner and InvoTrack
   (`AssetVersion`) are the worked examples.
2. **Never a per-request stamp.** `time()`, a random value, a request counter: the address
   changes on every hit and nothing is ever cached. allegroit-dk shipped exactly that as the
   fallback of `@filemtime($p) ?: time()` for a missing file.
3. **An ES module's imports need the stamp too.** `?v=` on the entry script does not reach
   what it imports, and a *relative* import drops the query when it resolves. A site of plain
   modules stamps every import in the build step, with **one value for the whole site** — a
   page must never load half its modules from one deploy and half from the next.
4. **Versioned files are cached hard; only unversioned ones revalidate.** `?v=` or a hashed
   name gets a year and `immutable`. Anything still at a fixed address gets the short or
   revalidating policy — and is assumed stale for as long as Cloudflare's TTL anyway.
5. **Some addresses must never change**, and get no stamp: the social-preview image (platforms
   cache by URL and re-scrape poorly), `favicon.ico`, `robots.txt`, `sitemap.xml`, feeds,
   `llms.txt`, and anything another site links to. Mark the reference rather than stamping it.

#!/usr/bin/env python3
"""A page that links a stylesheet or script at an address that never changes.

Rule here: asset-unversioned. The judgment half is `claude/rules/static-assets.md`.

THE DEFECT. A deploy changes `site.css`; the page still asks for `/site.css`; every browser
that already holds a copy goes on using it until that copy expires -- and the server cannot
shorten that, because the browser does not ask. The only change that reaches a cached
browser is a different ADDRESS, so a file a deploy can change must be referenced at an address
that changes with it: a content hash in the filename (what a bundler emits) or a `?v=` stamp
(the file's mtime, its hash, the build).

WHY ORIGIN HEADERS ARE NOT THE FIX, measured rather than assumed (2026-11-11). sourcetext.ai's
nginx sent `expires -1` -- "always revalidate" -- for every .js and .css, with a comment saying
so. Cloudflare, in front of every site in this estate, answered browsers with
`Cache-Control: max-age=14400`: its Browser Cache TTL overrode the origin, so a reader ran the
previous deploy's account pages for up to four hours. The comment was true of nginx and false
of what the browser received. A rule that relied on headers would depend on a setting in a
dashboard no repository can see; the address is the one thing the repo controls end to end.

WHAT IT READS. Every page template a server renders -- `.html`, `.htm`, `.php`, `.razor`,
`.cshtml`, `.twig` -- for a `<link href>` or `<script src>` naming a LOCAL `.css`, `.js` or
`.mjs` with neither a query string nor a hashed filename. Repo-level, because `.html` is not in
the per-file source scope (adding it there would subject markup to the length and naming
rules), so it runs on the whole-tree scan only, like disclosure-file.

WHAT IT LEAVES ALONE, each because the address is already right or not ours to judge:

* a value carrying template syntax (`<?=`, `@`, `{{`, `{%`, `${`, `<%`) -- it is computed, and
  the stamp is usually the thing being computed (allegroit-dk's `?v=<?= filemtime(..) ?>`);
* any query string at all -- `?v=`, `?ver=`, `?h=` are all stamps, and naming one spelling
  would fight working code for no gain;
* a hashed filename (`index-BkX2a9Qz.js`) -- a bundler's output, versioned by its name;
* an absolute or protocol-relative URL -- a CDN's file, versioned by the CDN's own path;
* `_framework/` and `_content/` -- ASP.NET serves those through MapStaticAssets, which
  fingerprints them itself;
* a page that is a BUNDLER'S INPUT -- an `index.html` beside `vite.config.ts` names
  `/src/main.js`, which the bundler rewrites to a hashed file. Recognised by a bundler config
  in the page's own directory, which is where every one of them keeps its entry page.

WHAT IT CANNOT SEE: a module's own `import`s. A `?v=` on the entry script does not reach the
modules it imports -- a relative import even drops the query when it resolves -- so the
stamping step must rewrite imports too. That is a property of a build step, not of any one
line, and stays with the rules file.

Line-exemptable, for the reference that genuinely must keep a fixed address.

Source of truth: engineering-standards/engineering_standards/standards_assets.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import CheckConfig, Violation, warn_unreadable
from standards_exemptions import line_exemption_reason
from standards_tree import walk_repository

RULE = "asset-unversioned"

# The templates a server renders into a page. `.php` covers any PHP view; Razor's two
# spellings and Twig are the estate's other server-side templates.
PAGE_SUFFIXES = (".html", ".htm", ".php", ".razor", ".cshtml", ".twig")

# A `<link>` or `<script>` tag's address. The value is captured whole, quotes and all, so a
# PHP expression inside it -- whose own `?>` would end a `[^>]*` match -- stays inside.
REFERENCE = re.compile(
    r"<(?:link|script)\b[^>]*?\b(?:href|src)\s*=\s*(\"[^\"]*\"|'[^']*')",
    re.IGNORECASE,
)

# What a deploy changes and a browser caches by address.
ASSET_PATH = re.compile(r"\.(?:css|m?js)$", re.IGNORECASE)

# Template syntax in the value: the address is computed, so the scanner cannot judge it.
TEMPLATE_MARKERS = ("<?", "@", "{{", "{%", "${", "<%")

# A scheme (`https:`, `data:`) or a protocol-relative `//host`: not a file this repo serves.
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)

# ASP.NET's fingerprinted framework paths, served through MapStaticAssets.
FRAMEWORK_PREFIXES = ("_framework/", "_content/")

# A bundler's content hash in the filename: a `-` or `.` and then eight or more characters
# holding both a digit and a letter, right before the extension. Requiring both is what keeps
# `account-sections.js` and `bootstrap.bundle.min.js` from reading as hashes.
HASHED_NAME = re.compile(
    r"[.-](?=[A-Za-z0-9_]*\d)(?=[A-Za-z0-9_]*[A-Za-z])[A-Za-z0-9_]{8,}\.(?:css|m?js)$",
)

# A bundler's config beside a page makes that page the bundler's input, not what is served.
BUNDLER_CONFIG = re.compile(
    r"^(?:(?:vite|vitest|webpack|rollup|svelte|astro|next|nuxt)\.config\.[cm]?[jt]s|angular\.json)$",
    re.IGNORECASE,
)

# Never ours: somebody else's pages, and huge.
PRUNED_DIRECTORIES = frozenset({"node_modules", "vendor", "bower_components"})


def is_unversioned(value: str) -> bool:
    """True when this `href`/`src` value names a local asset at an address that never changes."""
    address = value.strip().strip("\"'").strip()
    if any(marker in address for marker in TEMPLATE_MARKERS):
        return False
    if EXTERNAL.match(address) or "?" in address:
        return False
    path = address.split("#", 1)[0]
    if not ASSET_PATH.search(path):
        return False
    if path.lstrip("/").startswith(FRAMEWORK_PREFIXES):
        return False
    return not HASHED_NAME.search(path)


def check_page(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Every unversioned stylesheet or script reference in one page template."""
    for index, line in enumerate(lines):
        for match in REFERENCE.finditer(line):
            if not is_unversioned(match.group(1)):
                continue
            if line_exemption_reason(lines, index, RULE):
                continue
            yield Violation(
                path=path,
                line=index + 1,
                rule=RULE,
                message=_message(match.group(1).strip("\"'")),
            )


def _message(address: str) -> str:
    return (
        f"`{address}` is served at an address that never changes, so a browser that cached it "
        f"keeps the old file after a deploy until its copy expires -- and behind Cloudflare "
        f"that is Cloudflare's Browser Cache TTL, not the origin's header (sourcetext.ai sent "
        f"'always revalidate' and browsers were told four hours). Give it an address that "
        f"changes with its content: a `?v=` stamp from the file's mtime, its hash or the build, "
        f"or a bundler's hashed filename -- and if it is an ES module, the stamp has to reach "
        f"its imports too. See static-assets.md. If this reference must keep a fixed address, "
        f"say why beside it: `standards: {RULE} exempt -- <reason>`."
    )


def _is_bundler_input(names: list[str]) -> bool:
    """A directory holding a bundler config: its pages are the bundler's input."""
    return any(BUNDLER_CONFIG.match(name) for name in names)


def check_unversioned_assets(repo_root: Path, config: CheckConfig) -> Iterable[Violation]:
    """Walk the tree's page templates and report every unversioned asset reference.

    Git-ignored files are skipped by `walk_repository`: a bundler's built `dist/index.html`
    is ignored, and it is hashed anyway.
    """
    _ = config  # no per-repo flag: a fixed address is answered beside the reference
    for dirpath, dirnames, filenames in walk_repository(repo_root):
        dirnames[:] = [d for d in dirnames if d.casefold() not in PRUNED_DIRECTORIES]
        directory = Path(dirpath)
        if _is_bundler_input(filenames):
            continue
        for filename in filenames:
            if not filename.casefold().endswith(PAGE_SUFFIXES):
                continue
            path = directory / filename
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as error:
                warn_unreadable(path, error, f"{RULE} could not examine this page")
                continue
            yield from check_page(path, lines)

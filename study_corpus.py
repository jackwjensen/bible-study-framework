#!/usr/bin/env python3
"""
study_corpus.py — the corpus itself: frontmatter parsing, the vocabularies a
finding may use, and the loaders that turn findings/, themes/, threads/ and
imported/ into records.

Split out of build_index.py, which had grown to hold three separate jobs. This
is the one every other tool needs: packs.py imports the parser from here, and
build_index.py builds on it. Nothing here knows what INDEX.md looks like.

Standard library only — no dependencies, no install beyond Python itself.
"""

from __future__ import annotations

import re
from pathlib import Path

# ── paths ────────────────────────────────────────────────────────────────────
# The script lives inside the corpus directory: findings/, themes/, threads/ and
# INDEX.md are all siblings of this file.
STUDY_DIR = Path(__file__).resolve().parent
FINDINGS_DIR = STUDY_DIR / "findings"
THEMES_DIR = STUDY_DIR / "themes"
THREADS_DIR = STUDY_DIR / "threads"
IMPORTED_DIR = STUDY_DIR / "imported"
INDEX_FILE = STUDY_DIR / "INDEX.md"

# The CLI entry point, named explicitly rather than derived from __file__. This module is
# imported by build_index.py, so Path(__file__).name would resolve to *this* file and the
# generated INDEX.md header would tell readers to run a script that has no CLI.
SCRIPT_NAME = "build_index.py"

# ── vocabularies ─────────────────────────────────────────────────────────────

#: Firmness values a finding's `status:` may take.
VALID_STATUSES = ["hypothesis", "developing", "established", "firm", "contested", "retired"]

#: Display order for statuses: most-settled first, retired last.
STATUS_ORDER = ["firm", "established", "developing", "contested", "hypothesis", "retired"]

#: Epistemic type of a finding's headline claim (the modes' firm/synthesis/open
#: marking, using non-colliding words so it never clashes with status:firm).
VALID_CLAIM_TYPES = ["asserted", "synthesized", "open"]

#: Whose finding this is — the axis that keeps your own readings distinguishable
#: from borrowed scaffolding (`own` + `mixed` are yours; `reused` is context).
#: Optional; unset = unclassified.
VALID_ORIGINS = ["own", "reused", "mixed"]

#: Frontmatter fields every finding must carry.
REQUIRED_FINDING_FIELDS = ["id", "title", "status"]

#: List-valued frontmatter fields on a finding, normalized to lists.
FINDING_LIST_FIELDS = ["themes", "keywords", "scripture", "related", "builds_on",
                       "tension_with", "sources", "posts"]

#: Lifecycle of an open thread (a live/unresolved investigation).
VALID_THREAD_STATUSES = ["open", "deferred", "resolved"]

#: Display order for threads: live work first, parked next, settled last.
THREAD_STATUS_ORDER = ["open", "deferred", "resolved"]

#: List-valued frontmatter fields on a thread.
THREAD_LIST_FIELDS = ["themes", "keywords", "related"]


# ── ordering helpers ─────────────────────────────────────────────────────────

def byte_key(text: str) -> bytes:
    """Sort key matching PHP's default string sort (raw UTF-8 byte order)."""
    return text.encode("utf-8")


def casefold_key(text: str) -> bytes:
    """
    Sort key matching PHP's strcasecmp: byte-wise comparison with ASCII letters
    folded to lowercase. Deliberately *not* str.lower() — that folds non-ASCII
    too and would reorder titles containing accented or Greek characters.
    """
    return bytes(b + 32 if 65 <= b <= 90 else b for b in text.encode("utf-8"))


# ── frontmatter ──────────────────────────────────────────────────────────────

def _is_scalar_item(item: str) -> bool:
    """
    Whether a block-list item is a plain value rather than a `- key: value` map.

    A *quoted* item is always a scalar, however many colons it contains. Without
    this, `- "Numbers 22:38"` matched neither the plain-item rule (it has a
    colon) nor the map rule (it starts with a quote, not a word), and the line
    was silently discarded — a `scripture:` list of seventeen refs survived as
    one. Verse references are the single most common thing in this corpus to
    write as a quoted block list, so the shape that got dropped was the shape
    the corpus is made of.
    """
    text = item.strip()
    if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
        return True
    return ":" not in text


def unquote_scalar(value: str) -> str:
    """
    Strip surrounding quotes from a frontmatter scalar, honouring the escapes YAML
    defines inside them. A title that quotes a phrase — title: "\\"False prophet\\"
    collapses ..." — is valid YAML and common in this corpus; without unescaping,
    the backslashes leak into INDEX.md and into every rendered link.

    Falls back to the old strip-any-quote behaviour for unbalanced or bare values,
    so nothing that parsed before parses differently now.
    """
    text = value.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'")  # YAML single-quoted doubles the quote
    return text.strip("\"'")


def parse_frontmatter(front: str) -> dict:
    """
    Parse the text between the leading and trailing `---` fences.

    Handles `key: value`, block-style lists (`key:` then indented `- item`), and
    lists of maps (indented `- k: v` blocks). An inline `[a, b]` flow list is
    left as a raw string; normalize_list() below splits it.
    """
    meta: dict = {}
    current_key: str | None = None
    items: list = []
    current_item: dict | None = None

    def flush() -> None:
        nonlocal current_key, items, current_item
        if current_key is not None:
            if current_item is not None:
                items.append(current_item)
                current_item = None
            meta[current_key] = items
            items = []
            current_key = None

    for raw_line in front.split("\n"):
        line = raw_line.rstrip("\r")
        if line.strip() == "":
            continue

        m = re.match(r"^([\w-]+):\s*(.*)$", line)
        if m:
            flush()
            if m.group(2) == "":
                current_key = m.group(1)
            else:
                meta[m.group(1)] = unquote_scalar(m.group(2))
            continue

        if current_key is None:
            continue

        m = re.match(r"^\s+-\s+(.+)$", line)
        if m and _is_scalar_item(m.group(1)):
            if current_item is not None:
                items.append(current_item)
                current_item = None
            items.append(unquote_scalar(m.group(1)))
            continue

        m = re.match(r"^\s+-\s+([\w-]+):\s*(.*)$", line)
        if m:
            if current_item is not None:
                items.append(current_item)
            current_item = {m.group(1): unquote_scalar(m.group(2))}
            continue

        m = re.match(r"^\s+([\w-]+):\s*(.*)$", line)
        if m and current_item is not None:
            current_item[m.group(1)] = unquote_scalar(m.group(2))
            continue

    flush()
    return meta


def load_content(path: Path) -> dict | None:
    """Read a Markdown file into {'meta': dict, 'body': str}; None if absent."""
    if not path.exists():
        return None

    # utf-8-sig, not utf-8: Windows editors (and PowerShell's Set-Content) often
    # write a UTF-8 BOM, which would otherwise sit in front of the opening `---`
    # and silently defeat frontmatter detection. Harmless when no BOM is present.
    raw = path.read_text(encoding="utf-8-sig")
    meta: dict = {}
    body = raw

    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            meta = parse_frontmatter(raw[4:end])
            body = raw[end + 4:].lstrip()

    return {"meta": meta, "body": body}


def split_scalar(scalar: str) -> list[str]:
    """
    Split one scalar (possibly an inline "[a, b]" list) into trimmed tokens.

    Commas *inside quotes* do not split. A plain `scalar.split(",")` fragmented
    every entry that contained a comma — one `sources:` citation became five
    nonsense ones — and citations contain commas as a matter of course
    ("Wenham, Genesis 1-15"). A quote only opens a token when it is the first
    character of that token, so an apostrophe inside an unquoted word ("God's")
    stays an apostrophe rather than swallowing the rest of the line.
    """
    scalar = scalar.strip()
    if scalar == "":
        return []
    if scalar.startswith("[") and scalar.endswith("]"):
        scalar = scalar[1:-1]
    if scalar.strip() == "":
        return []

    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(scalar):
        character = scalar[index]
        if quote is not None:
            if character == "\\" and quote == '"' and index + 1 < len(scalar):
                current.append(scalar[index + 1])
                index += 2
                continue
            if character == quote:
                quote = None
            else:
                current.append(character)
        elif character in "\"'" and "".join(current).strip() == "":
            quote = character
            current = []
        elif character == ",":
            tokens.append("".join(current))
            current = []
        else:
            current.append(character)
        index += 1
    tokens.append("".join(current))

    return [token.strip() for token in tokens if token.strip() != ""]


def normalize_list(value) -> list[str]:
    """
    Normalize a frontmatter value to a list of clean string tokens, from either a
    block-style list, an inline flow list, or a bare scalar. Duplicates are
    dropped, keeping first-seen order.
    """
    out: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                continue  # typed map items are not used in study frontmatter
            # A block list has already said where its items begin and end, so
            # its entries are taken whole. Re-splitting them on commas was how
            # a single "Wenham, Genesis 1-15" source became two.
            text = str(item).strip()
            if text != "":
                out.append(text)
    elif isinstance(value, str):
        out.extend(split_scalar(value))
    else:
        return []
    return list(dict.fromkeys(t for t in out if t != ""))


# ── text helpers ─────────────────────────────────────────────────────────────

def wikilinks(body: str) -> list[str]:
    """All [[wikilink]] targets referenced in a body, first-seen order."""
    return list(dict.fromkeys(re.findall(r"\[\[([a-z0-9-]+)\]\]", body)))


def first_paragraph(body: str) -> str:
    """
    First paragraph of a body: consecutive prose lines joined into one string,
    skipping leading headings/blank/quote/list/italic-only lines and stopping at
    the next blank line. Joining fixes source line-wrap cutting mid-sentence.
    """
    collected: list[str] = []
    for raw_line in body.split("\n"):
        line = raw_line.strip()
        if not collected:
            if line == "" or line[0] in "#>-":
                continue
            if line.startswith("*") and line.endswith("*") and line.count("*") <= 2:
                continue  # a fully-italic disclaimer line
            collected.append(line)
            continue
        if line == "":
            break
        collected.append(line)
    return " ".join(collected)


def plain(text: str) -> str:
    """Strip the lightest markdown for plain display text."""
    text = re.sub(r"\[\[([a-z0-9-]+)\]\]", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # [label](url) -> label
    for token in ("**", "*", "`", "_"):
        text = text.replace(token, "")
    return text.strip()


def truncate(text: str, max_len: int) -> str:
    """Truncate at a word boundary with an ellipsis."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    last_space = cut.rfind(" ")
    if last_space > 0:
        cut = cut[:last_space]
    return cut.rstrip(" ,;:") + "…"


def gloss_for(record: dict) -> str:
    """A one-line gloss: the explicit excerpt, else the first prose sentence."""
    excerpt = str(record["meta"].get("excerpt", "") or "").strip()
    if excerpt != "":
        return truncate(excerpt, 150)

    para = first_paragraph(record["body"])
    # Drop a leading bold lead-in label ("**The claim.**", "**Why it matters.**", …).
    para = re.sub(r"^\*\*[^*]{1,40}?[.:]\*\*\s*", "", para)
    para = plain(para)
    if para == "":
        return ""
    # First sentence, but require some length so a short lead-in isn't the whole gloss.
    m = re.match(r"^(.{25,}?[.!?])(\s|$)", para, re.DOTALL)
    if m:
        return truncate(m.group(1).strip(), 150)
    return truncate(para, 150)


# ── loading ──────────────────────────────────────────────────────────────────

def load_findings() -> dict:
    """Load every finding as an id-keyed dict. Id is the filename (canonical)."""
    findings: dict = {}
    for file in sorted(FINDINGS_DIR.glob("*.md"), key=lambda p: byte_key(p.name)):
        parsed = load_content(file)
        if parsed is None:
            continue
        finding_id = file.stem
        meta = parsed["meta"]

        record = {
            "id": finding_id,
            "file": "findings/" + file.name,
            "meta": meta,
            "body": parsed["body"],
            "title": str(meta.get("title", finding_id) or finding_id).strip(),
            "status": str(meta.get("status", "") or "").strip(),
            "claim_type": str(meta.get("claim_type", "") or "").strip(),
            "origin": str(meta.get("origin", "") or "").strip(),
        }
        for field in FINDING_LIST_FIELDS:
            record[field] = normalize_list(meta.get(field))
        record["gloss"] = gloss_for(record)
        record["wikilinks"] = wikilinks(record["body"])

        findings[finding_id] = record
    return dict(sorted(findings.items(), key=lambda kv: byte_key(kv[0])))


def load_themes() -> dict:
    """Load every theme hub as an id-keyed dict."""
    themes: dict = {}
    for file in sorted(THEMES_DIR.glob("*.md"), key=lambda p: byte_key(p.name)):
        parsed = load_content(file)
        if parsed is None:
            continue
        theme_id = file.stem
        meta = parsed["meta"]
        themes[theme_id] = {
            "id": theme_id,
            "file": "themes/" + file.name,
            "title": str(meta.get("title", theme_id) or theme_id).strip(),
            "series": str(meta.get("series", "") or "").strip(),
            "category": str(meta.get("category", "") or "").strip(),
            "description": truncate(plain(first_paragraph(parsed["body"])), 240),
        }
    return dict(sorted(themes.items(), key=lambda kv: byte_key(kv[0])))


def load_threads() -> dict:
    """
    Load every thread (a live/unresolved investigation) as an id-keyed dict.
    Threads capture questions that aren't settled findings yet; a `deferred`
    thread is parked on purpose and should not be worked until called for.
    """
    threads: dict = {}
    for file in sorted(THREADS_DIR.glob("*.md"), key=lambda p: byte_key(p.name)):
        parsed = load_content(file)
        if parsed is None:
            continue
        thread_id = file.stem
        meta = parsed["meta"]
        record = {
            "id": thread_id,
            "file": "threads/" + file.name,
            "meta": meta,
            "body": parsed["body"],
            "title": str(meta.get("title", thread_id) or thread_id).strip(),
            "status": str(meta.get("status", "") or "").strip(),
        }
        for field in THREAD_LIST_FIELDS:
            record[field] = normalize_list(meta.get(field))
        record["gloss"] = gloss_for(record)
        record["wikilinks"] = wikilinks(record["body"])
        threads[thread_id] = record
    return dict(sorted(threads.items(), key=lambda kv: byte_key(kv[0])))


def load_imported() -> list[dict]:
    """
    Load packs received from other people (see PACKS.md). These are read-only
    reference material in a *separate namespace* — their internal edges point at
    ids inside their own pack, so they are never validated themselves, nor
    merged into your own findings.

    Your own findings *may* point at them: PACKS.md tells you to use an import
    by writing your own finding that builds_on or tension_with it, so those ids
    are valid edge and [[wikilink]] targets. See validate().
    """
    packs = []
    if not IMPORTED_DIR.exists():
        return packs
    for pack_dir in sorted(IMPORTED_DIR.iterdir(), key=lambda p: byte_key(p.name)):
        if not pack_dir.is_dir() or pack_dir.name.startswith("."):
            continue
        manifest = load_content(pack_dir / "pack.md")
        if manifest is None:
            continue
        meta = manifest["meta"]
        entries = []
        for file in sorted((pack_dir / "findings").glob("*.md"), key=lambda p: byte_key(p.name)):
            parsed = load_content(file)
            if parsed is None:
                continue
            record = {
                "id": file.stem,
                "file": f"imported/{pack_dir.name}/findings/{file.name}",
                "meta": parsed["meta"],
                "body": parsed["body"],
                "title": str(parsed["meta"].get("title", file.stem) or file.stem).strip(),
                "status": str(parsed["meta"].get("status", "") or "").strip(),
            }
            record["gloss"] = gloss_for(record)
            entries.append(record)
        packs.append({
            "author": str(meta.get("author", pack_dir.name) or pack_dir.name).strip(),
            "author_id": pack_dir.name,
            "title": str(meta.get("title", "") or "").strip(),
            "version": str(meta.get("version", "") or "").strip(),
            "license": str(meta.get("license", "") or "").strip(),
            "findings": entries,
        })
    return packs

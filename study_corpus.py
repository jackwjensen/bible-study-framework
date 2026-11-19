#!/usr/bin/env python3
"""
study_corpus.py — the corpus itself: the vocabularies a finding may use, and the
loaders that turn findings/, themes/, threads/ and imported/ into records.

Split out of build_index.py, which had grown to hold three separate jobs. The
parsing half went on to study_frontmatter.py for the same reason — reading a
Markdown file is a job that knows nothing about findings, and this file's own
docstring used to name three responsibilities in one sentence. What is left is
one: what a record IS, and where records come from.

Nothing here knows what INDEX.md looks like; that is study_sections.py.

Standard library only — no dependencies, no install beyond Python itself.
"""

from __future__ import annotations

import re
from pathlib import Path

from study_frontmatter import load_content, normalize_list

# ── paths ────────────────────────────────────────────────────────────────────
# The script lives inside the corpus directory: findings/, themes/, threads/ and
# INDEX.md are all siblings of this file.
STUDY_DIR = Path(__file__).resolve().parent
FINDINGS_DIR = STUDY_DIR / "findings"
THEMES_DIR = STUDY_DIR / "themes"
THREADS_DIR = STUDY_DIR / "threads"
IMPORTED_DIR = STUDY_DIR / "imported"
INDEX_FILE = STUDY_DIR / "INDEX.md"
# Each imported pack's manifest, at the root of imported/<author-id>/. packs.py writes it on
# import and this module reads it to index the pack, so the name lives here, once.
MANIFEST_NAME = "pack.md"

# The CLI entry point, named explicitly rather than derived from __file__. This module is
# imported by build_index.py, so Path(__file__).name would resolve to *this* file and the
# generated INDEX.md header would tell readers to run a script that has no CLI.
SCRIPT_NAME = "build_index.py"

# ── vocabularies ─────────────────────────────────────────────────────────────

#: Firmness values a finding's `status:` may take.
VALID_STATUSES = ["hypothesis", "developing", "established", "firm", "contested", "retired"]

#: Display order for statuses: most-settled first, retired last.
STATUS_ORDER = ["firm", "established", "developing", "contested", "hypothesis", "retired"]

#: Maturity, ascending. A subset of VALID_STATUSES on purpose: `contested` and
#: `retired` are states a claim can be IN, not rungs on the way up, so they have
#: no rank. Tools that compare maturity (SourceText's adoption cap) read this;
#: display order stays STATUS_ORDER.
STATUS_LADDER = ["hypothesis", "developing", "established", "firm"]

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
FINDING_LIST_FIELDS = ["themes", "keywords", "citations", "related", "builds_on",
                       "tension_with", "sources", "posts"]

#: A citation names its corpus before the reference: "bible:John 3:16",
#: "dklaw:Momsloven § 37". The corpus code is lowercase letters and digits; the reference
#: is whatever that corpus cites by, so it may itself contain colons.
CORPUS_CODE = r"[a-z0-9]+"
CITATION_SHAPE = re.compile(rf"({CORPUS_CODE}):(\S.*)")


def split_citation(text: str) -> tuple[str, str]:
    """Split a citation into (corpus, reference) at the first colon.

    Raises ValueError for a citation that does not name its corpus.
    """
    match = CITATION_SHAPE.fullmatch(text)
    if match is None:
        raise ValueError(f"citation '{text}' does not name its corpus (expected corpus:reference)")
    return match.group(1), match.group(2)

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


# ── text helpers ─────────────────────────────────────────────────────────────

#: The alphabet a [[wikilink]] can name. One definition, because it is the same
#: fact seen from two sides: what a link may say, and what an id must be for a
#: link to reach it. Kept apart, the two drifted — slugify would mint
#: `guds-nåde`, slug_ok would approve it, and no link could ever match it.
LINK_ALPHABET = r"[a-z0-9-]+"

_WIKILINK = re.compile(rf"\[\[({LINK_ALPHABET})\]\]")
_LINKABLE_ID = re.compile(rf"^{LINK_ALPHABET}$")


def wikilinks(body: str) -> list[str]:
    """All [[wikilink]] targets referenced in a body, first-seen order."""
    return list(dict.fromkeys(_WIKILINK.findall(body)))


def linkable_id(value: str) -> bool:
    """Whether a [[wikilink]] could ever name this id.

    An id outside the alphabet is not merely unusual, it is unreachable — and
    silently so. `[[שלום]]` does not match the pattern, so it is never read as a
    link at all, and nothing reports it as broken: the link just does nothing,
    forever. A reported error is the only way that surfaces.
    """
    return bool(_LINKABLE_ID.match(value))


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
        manifest = load_content(pack_dir / MANIFEST_NAME)
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

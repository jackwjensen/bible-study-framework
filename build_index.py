#!/usr/bin/env python3
"""
build_index.py — regenerate INDEX.md from the findings/ + themes/ + threads/
frontmatter. The Markdown files are the source of truth; INDEX.md is generated.

Usage:
    python build_index.py            # regenerate INDEX.md
    python build_index.py --check    # validate only, write nothing; exit 1 on errors

--check reports hard ERRORS (broken [[wikilinks]], unknown edge ids, duplicate or
mismatched ids, missing required frontmatter, invalid status/claim_type/origin)
and SOFT NOTICES (a theme with no hub file yet) which never fail the build.

Standard library only — no dependencies, no install beyond Python itself.
"""

from __future__ import annotations

import re
import sys
from functools import cmp_to_key
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

SCRIPT_NAME = Path(__file__).name

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
                meta[m.group(1)] = m.group(2).strip("\"'")
            continue

        if current_key is None:
            continue

        m = re.match(r"^\s+-\s+(.+)$", line)
        if m and ":" not in m.group(1):
            if current_item is not None:
                items.append(current_item)
                current_item = None
            items.append(m.group(1).strip("\"' "))
            continue

        m = re.match(r"^\s+-\s+([\w-]+):\s*(.*)$", line)
        if m:
            if current_item is not None:
                items.append(current_item)
            current_item = {m.group(1): m.group(2).strip("\"'")}
            continue

        m = re.match(r"^\s+([\w-]+):\s*(.*)$", line)
        if m and current_item is not None:
            current_item[m.group(1)] = m.group(2).strip("\"'")
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
    """Split one scalar (possibly an inline "[a, b]" list) into trimmed tokens."""
    scalar = scalar.strip()
    if scalar == "":
        return []
    if scalar.startswith("[") and scalar.endswith("]"):
        scalar = scalar[1:-1]
    if scalar.strip() == "":
        return []
    tokens = []
    for part in scalar.split(","):
        part = part.strip().strip("\"'").strip()
        if part != "":
            tokens.append(part)
    return tokens


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
            out.extend(split_scalar(str(item)))
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


# ── validation ───────────────────────────────────────────────────────────────

def validate(findings: dict, themes: dict, threads: dict) -> tuple[list[str], list[str]]:
    """Validate the corpus. Returns (errors, notices); only errors fail --check."""
    errors: list[str] = []
    notices: list[str] = []
    ids = list(findings.keys())
    # [[wikilinks]] may point to a finding OR a thread.
    link_targets = set(ids) | set(threads.keys())

    for finding_id, finding in findings.items():
        where = finding["file"]

        for field in REQUIRED_FINDING_FIELDS:
            if str(finding.get(field, "") or "").strip() == "":
                errors.append(f"{where}: missing required frontmatter field '{field}'")

        declared_id = str(finding["meta"].get("id", "") or "").strip()
        if declared_id != "" and declared_id != finding_id:
            errors.append(
                f"{where}: frontmatter id '{declared_id}' does not match filename id '{finding_id}'")

        if finding["status"] != "" and finding["status"] not in VALID_STATUSES:
            errors.append(f"{where}: invalid status '{finding['status']}' "
                          f"(allowed: {', '.join(VALID_STATUSES)})")

        if finding["claim_type"] != "" and finding["claim_type"] not in VALID_CLAIM_TYPES:
            errors.append(f"{where}: invalid claim_type '{finding['claim_type']}' "
                          f"(allowed: {', '.join(VALID_CLAIM_TYPES)})")

        if finding["origin"] != "" and finding["origin"] not in VALID_ORIGINS:
            errors.append(f"{where}: invalid origin '{finding['origin']}' "
                          f"(allowed: {', '.join(VALID_ORIGINS)})")

        for edge_field in ("related", "builds_on", "tension_with"):
            for target in finding[edge_field]:
                if target not in findings:
                    errors.append(
                        f"{where}: {edge_field} references unknown finding id '{target}'")

        for target in finding["wikilinks"]:
            if target not in link_targets:
                errors.append(
                    f"{where}: [[{target}]] links to a finding/thread that does not exist")

        for theme in finding["themes"]:
            if theme not in themes:
                notices.append(
                    f"{where}: theme '{theme}' has no hub file (themes/{theme}.md) yet")

    thread_status_list = ", ".join(VALID_THREAD_STATUSES)
    for thread_id, thread in threads.items():
        where = thread["file"]

        if thread["title"].strip() == "":
            errors.append(f"{where}: missing required frontmatter field 'title'")
        if thread["status"] == "":
            errors.append(f"{where}: missing required frontmatter field 'status'")
        elif thread["status"] not in VALID_THREAD_STATUSES:
            errors.append(f"{where}: invalid status '{thread['status']}' "
                          f"(allowed: {thread_status_list})")

        declared_id = str(thread["meta"].get("id", "") or "").strip()
        if declared_id != "" and declared_id != thread_id:
            errors.append(
                f"{where}: frontmatter id '{declared_id}' does not match filename id '{thread_id}'")

        for target in thread["related"]:
            if target not in findings:
                errors.append(f"{where}: related references unknown finding id '{target}'")
        for target in thread["wikilinks"]:
            if target not in link_targets:
                errors.append(
                    f"{where}: [[{target}]] links to a finding/thread that does not exist")
        for theme in thread["themes"]:
            if theme not in themes:
                notices.append(
                    f"{where}: theme '{theme}' has no hub file (themes/{theme}.md) yet")

    # Flag a hub that neither a finding nor a thread uses (a dangling description).
    used_themes: set[str] = set()
    for finding in findings.values():
        used_themes.update(finding["themes"])
    for thread in threads.values():
        used_themes.update(thread["themes"])
    for theme_id, theme in themes.items():
        if theme_id not in used_themes:
            notices.append(f"{theme['file']}: theme hub is not referenced by any finding yet")

    return list(dict.fromkeys(errors)), list(dict.fromkeys(notices))


# ── index rendering ──────────────────────────────────────────────────────────

def sort_by_status(records: list[dict]) -> list[dict]:
    """Sort findings by status order, then title (case-insensitive)."""
    def rank(record: dict) -> int:
        try:
            return STATUS_ORDER.index(record["status"])
        except ValueError:
            return len(STATUS_ORDER)
    return sorted(records, key=lambda r: (rank(r), casefold_key(r["title"])))


def sort_by_title(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda r: casefold_key(r["title"]))


def finding_line(finding: dict) -> str:
    """A markdown link + status (+ claim_type tag) + gloss line for a finding."""
    line = f"- **{finding['status']}** — [{finding['title']}]({finding['file']})"
    if finding["claim_type"] != "":
        line += f" `{finding['claim_type']}`"
    if finding["gloss"] != "":
        line += f" — {finding['gloss']}"
    return line


def section_by_theme(findings: dict, themes: dict) -> list[str]:
    out = ["## By theme", ""]

    # Every theme id referenced by a finding, unioned with every hub that exists.
    theme_ids: dict = {}
    for finding in findings.values():
        for theme in finding["themes"]:
            theme_ids[theme] = True
    for theme_id in themes:
        theme_ids[theme_id] = True
    ordered_ids = sorted(theme_ids.keys(), key=byte_key)

    if not ordered_ids:
        out += ["_No themes yet._", ""]
        return out

    for theme_id in ordered_ids:
        hub = themes.get(theme_id)
        out.append(f"### {hub['title'] if hub else theme_id}")

        if hub is not None:
            links = []
            if hub["series"] != "":
                links.append(f"series: `{hub['series']}`")
            if hub["category"] != "":
                links.append(f"category: `{hub['category']}`")
            meta_line = f"[hub]({hub['file']})"
            if links:
                meta_line += " · " + " · ".join(links)
            out.append(f"_{meta_line}_")
            if hub["description"] != "":
                out.append(hub["description"])
        else:
            out.append(f"_(no hub file yet — add `themes/{theme_id}.md`)_")

        members = [f for f in findings.values() if theme_id in f["themes"]]
        out += [finding_line(f) for f in sort_by_status(members)]
        out.append("")

    return out


def section_by_status(findings: dict) -> list[str]:
    out = ["## By status", ""]
    for status in STATUS_ORDER:
        members = [f for f in findings.values() if f["status"] == status]
        if not members:
            continue
        out.append(f"### {status}")
        for finding in sort_by_title(members):
            line = f"- [{finding['title']}]({finding['file']})"
            if finding["themes"]:
                line += " — _" + ", ".join(finding["themes"]) + "_"
            if finding["gloss"] != "":
                line += f" — {finding['gloss']}"
            out.append(line)
        out.append("")
    return out


def section_by_claim_type(findings: dict) -> list[str]:
    labels = {
        "asserted": "asserted — the text (or a cited source) explicitly asserts it",
        "synthesized": "synthesized — inferred or synthesized across texts/sources",
        "open": "open — underdetermined by the evidence",
    }
    out = ["## By claim type", ""]
    for claim_type in VALID_CLAIM_TYPES:
        members = [f for f in findings.values() if f["claim_type"] == claim_type]
        if not members:
            continue
        out.append(f"### {labels[claim_type]}")
        out += [finding_line(f) for f in sort_by_status(members)]
        out.append("")
    unmarked = [f for f in findings.values() if f["claim_type"] == ""]
    if unmarked:
        out.append("### (unmarked — assign a claim_type)")
        out += [f"- [{f['title']}]({f['file']})" for f in sort_by_title(unmarked)]
        out.append("")
    return out


def section_by_origin(findings: dict) -> list[str]:
    labels = {
        "own": "own — your own, worked out through the sessions",
        "mixed": "mixed — your distinctive route/synthesis over non-original material",
        "reused": "reused — standard scholarship / someone else's, brought in for context",
    }
    out = ["## By origin", ""]
    out.append("_Your body of work is **own** + **mixed**; **reused** is context. Unclassified "
               "findings below are yours to mark — this bucket should shrink over time._")
    out.append("")
    for origin in ("own", "mixed", "reused"):
        members = [f for f in findings.values() if f["origin"] == origin]
        if not members:
            continue
        out.append(f"### {labels[origin]}")
        out += [finding_line(f) for f in sort_by_status(members)]
        out.append("")
    unclassified = [f for f in findings.values() if f["origin"] == ""]
    if unclassified:
        out.append(f"### (unclassified — {len(unclassified)} findings, origin not yet set)")
        out += [f"- [{f['title']}]({f['file']})" for f in sort_by_title(unclassified)]
        out.append("")
    return out


def section_by_keyword(findings: dict) -> list[str]:
    out = ["## By keyword", ""]
    by_keyword: dict[str, list[dict]] = {}
    for finding in findings.values():
        for keyword in finding["keywords"]:
            by_keyword.setdefault(keyword, []).append(finding)
    if not by_keyword:
        out += ["_No keywords yet._", ""]
        return out
    for keyword in sorted(by_keyword.keys(), key=byte_key):
        links = [f"[{f['title']}]({f['file']})" for f in sort_by_status(by_keyword[keyword])]
        out.append(f"- **{keyword}** — " + " · ".join(links))
    out.append("")
    return out


def scripture_parts(ref: str) -> tuple[str, tuple[int, int]]:
    """Split "1 John 4:8" into ("1 John", (4, 8)) for ordering."""
    m = re.match(r"^(.*?)\s+(\d+)(?::(\d+))?", ref.strip())
    if m:
        return m.group(1).strip(), (int(m.group(2)), int(m.group(3) or 0))
    return ref.strip(), (0, 0)


def scripture_compare(a: str, b: str) -> int:
    """Rough canonical-order compare: by book name, then chapter:verse numerically."""
    book_a, nums_a = scripture_parts(a)
    book_b, nums_b = scripture_parts(b)
    key_a, key_b = casefold_key(book_a), casefold_key(book_b)
    if key_a != key_b:
        return -1 if key_a < key_b else 1
    if nums_a != nums_b:
        return -1 if nums_a < nums_b else 1
    return 0


def section_by_scripture(findings: dict) -> list[str]:
    out = ["## By scripture", ""]
    by_ref: dict[str, list[dict]] = {}
    for finding in findings.values():
        for ref in finding["scripture"]:
            by_ref.setdefault(ref, []).append(finding)
    if not by_ref:
        out += ["_No scripture references yet._", ""]
        return out
    for ref in sorted(by_ref.keys(), key=cmp_to_key(scripture_compare)):
        members = by_ref[ref]
        links = [f"[{f['title']}]({f['file']})" for f in sort_by_status(members)]
        marker = " ⭑" if len(members) > 1 else ""
        out.append(f"- **{ref}**{marker} — " + " · ".join(links))
    out.append("")
    out.append("_⭑ = cited by more than one finding (a cross-reference worth noticing)._")
    out.append("")
    return out


def section_relationships(findings: dict) -> list[str]:
    out = ["## Relationships & gaps", ""]

    # Tensions: unordered unique pairs across tension_with edges.
    tensions: dict[str, tuple[dict, dict]] = {}
    for finding_id, finding in findings.items():
        for other in finding["tension_with"]:
            if other not in findings:
                continue
            low, high = (finding_id, other) if finding_id < other else (other, finding_id)
            tensions[f"{low}|{high}"] = (findings[low], findings[high])

    out.append("### Tensions to reconcile")
    if not tensions:
        out.append("_None flagged._")
    else:
        for left, right in tensions.values():
            out.append(f"- [{left['title']}]({left['file']}) ⇄ [{right['title']}]({right['file']})")
    out.append("")

    # Orphans: findings with no relationships and not linked to by anyone.
    linked_to: set[str] = set()
    for finding in findings.values():
        for field in ("related", "builds_on", "tension_with"):
            linked_to.update(finding[field])
        linked_to.update(finding["wikilinks"])
    orphans = [
        f for fid, f in findings.items()
        if not (f["related"] or f["builds_on"] or f["tension_with"] or f["wikilinks"])
        and fid not in linked_to
    ]

    out.append("### Orphans (no links yet — connect these)")
    if not orphans:
        out.append("_None._")
    else:
        out += [f"- [{f['title']}]({f['file']})" for f in sort_by_title(orphans)]
    out.append("")

    return out


def section_threads(threads: dict) -> list[str]:
    out = ["## Open threads", ""]
    if not threads:
        out += ["_No threads yet._", ""]
        return out
    labels = {
        "open": "open — live / unresolved",
        "deferred": "deferred — parked on purpose (do not open until called for)",
        "resolved": "resolved — settled (kept for history)",
    }
    for status in THREAD_STATUS_ORDER:
        members = [t for t in threads.values() if t["status"] == status]
        if not members:
            continue
        out.append(f"### {labels[status]}")
        for thread in sort_by_title(members):
            line = f"- [{thread['title']}]({thread['file']})"
            if thread["gloss"] != "":
                line += f" — {thread['gloss']}"
            out.append(line)
        out.append("")
    return out


def load_imported() -> list[dict]:
    """
    Load packs received from other people (see PACKS.md). These are read-only
    reference material in a *separate namespace* — their internal edges point at
    ids inside their own pack, so they are listed but never validated against,
    nor merged into, your own findings.
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


def section_imported(packs: list[dict]) -> list[str]:
    if not packs:
        return []
    out = ["## Imported from others", "",
           "_Read-only reference material from other people's packs — **not your findings**. To use "
           "one, write your own finding that `builds_on` or `tension_with` it. See "
           "[`PACKS.md`](PACKS.md)._", ""]
    for pack in packs:
        heading = f"### {pack['author']}"
        if pack["title"]:
            heading += f" — {pack['title']}"
        out.append(heading)
        details = [f"`imported/{pack['author_id']}/`"]
        if pack["version"]:
            details.append(f"version {pack['version']}")
        if pack["license"]:
            details.append(pack["license"])
        out.append("_" + " · ".join(details) + "_")
        for finding in sort_by_title(pack["findings"]):
            line = f"- [{finding['title']}]({finding['file']})"
            if finding["status"]:
                line += f" — _{finding['status']}_"
            if finding["gloss"]:
                line += f" — {finding['gloss']}"
            out.append(line)
        out.append("")
    return out


def build_index(findings: dict, themes: dict, threads: dict, imported: list[dict]) -> str:
    out = [
        f"<!-- GENERATED by {SCRIPT_NAME} — do not hand-edit. Run: python {SCRIPT_NAME} -->",
        "",
        "# Study Corpus Index",
        "",
        "The map of every biblical-study **finding**. One atomic claim per file in `findings/`; "
        "firmness in each finding's `status:`. **Read this first** before biblical-theme work, then "
        "open the findings it points to. This file is generated — edit the findings, then run "
        f"`python {SCRIPT_NAME}`.",
        "",
        f"_{len(findings)} findings · {len(themes)} theme hubs · {len(threads)} threads._",
        "",
    ]
    out += section_by_theme(findings, themes)
    out += section_by_status(findings)
    out += section_by_claim_type(findings)
    out += section_by_origin(findings)
    out += section_by_keyword(findings)
    out += section_by_scripture(findings)
    out += section_relationships(findings)
    out += section_threads(threads)
    out += section_imported(imported)
    return "\n".join(out) + "\n"


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    check_only = "--check" in argv[1:]

    findings = load_findings()
    themes = load_themes()
    threads = load_threads()
    imported = load_imported()
    errors, notices = validate(findings, themes, threads)

    # Windows consoles default to a legacy codepage; the corpus is full of
    # em-dashes, Greek and Hebrew. Force UTF-8 so reporting never crashes.
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    for notice in notices:
        print(f"notice: {notice}", file=sys.stderr)
    for error in errors:
        print(f"error:  {error}", file=sys.stderr)

    counts = f"{len(findings)} findings, {len(themes)} theme hubs, {len(threads)} threads"
    if imported:
        counts += f", {sum(len(p['findings']) for p in imported)} imported from {len(imported)} pack(s)"

    if check_only:
        if errors:
            print(f"\nFAIL — {len(errors)} error(s).", file=sys.stderr)
            return 1
        print(f"OK — {counts}, {len(notices)} notice(s).", file=sys.stderr)
        return 0

    INDEX_FILE.write_text(build_index(findings, themes, threads, imported),
                          encoding="utf-8", newline="\n")
    print(f"Wrote {INDEX_FILE.name} — {counts}, {len(notices)} notice(s), {len(errors)} error(s).",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

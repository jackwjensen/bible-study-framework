#!/usr/bin/env python3
"""
study_sections.py — rendering: turn loaded records into the Markdown sections of
INDEX.md, and assemble them into the finished document.

Split out of build_index.py. Everything here is presentation; it reads records
and returns lines of Markdown, and never touches the filesystem. Validation and
the CLI stay in build_index.py.

Standard library only — no dependencies, no install beyond Python itself.
"""

from __future__ import annotations

import re
from functools import cmp_to_key

from study_corpus import (
    SCRIPT_NAME, STATUS_ORDER, THREAD_STATUS_ORDER, VALID_CLAIM_TYPES, VALID_ORIGINS,
    byte_key, casefold_key, plain, truncate
)

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

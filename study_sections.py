#!/usr/bin/env python3
"""
study_sections.py — rendering: turn loaded records into the Markdown sections of
INDEX.md, and assemble them into the finished document.

Split out of build_index.py. Everything here is presentation, in the words of the study's
word set (`words`, from study_words.words_for); it reads records and returns lines of
Markdown, and never touches the filesystem. Validation and the CLI stay in build_index.py.

Standard library only — no dependencies, no install beyond Python itself.
"""

from __future__ import annotations

import re

from study_corpus import (
    SCRIPT_NAME, STATUS_ORDER, THREAD_STATUS_ORDER, VALID_CLAIM_TYPES,
    byte_key, casefold_key, split_citation
)

# ── index rendering ──────────────────────────────────────────────────────────

def say(words: dict[str, str], key: str, **fields: object) -> str:
    """A word-set string with its placeholders filled; the count nouns are always available."""
    nouns = {
        "finding": words["count.finding.one"], "findings": words["count.finding.other"],
        "theme": words["count.theme.one"], "themes": words["count.theme.other"],
        "thread": words["count.thread.one"], "threads": words["count.thread.other"],
    }
    return words[key].format(**nouns, **fields)


def status_word(words: dict[str, str], status: str) -> str:
    """The display word for a stored status; an unknown code is shown as it is."""
    return words.get(f"findings.status.{status}", status)


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


def finding_line(finding: dict, words: dict[str, str]) -> str:
    """A markdown link + status (+ claim_type tag) + gloss line for a finding."""
    line = f"- **{status_word(words, finding['status'])}** — [{finding['title']}]({finding['file']})"
    if finding["claim_type"] != "":
        line += f" `{finding['claim_type']}`"
    if finding["gloss"] != "":
        line += f" — {finding['gloss']}"
    return line


def section_by_theme(findings: dict, themes: dict, words: dict[str, str]) -> list[str]:
    out = [f"## {say(words, 'index.theme.heading')}", ""]

    # Every theme id referenced by a finding, unioned with every hub that exists.
    theme_ids: dict = {}
    for finding in findings.values():
        for theme in finding["themes"]:
            theme_ids[theme] = True
    for theme_id in themes:
        theme_ids[theme_id] = True
    ordered_ids = sorted(theme_ids.keys(), key=byte_key)

    if not ordered_ids:
        out += [say(words, "index.theme.none"), ""]
        return out

    for theme_id in ordered_ids:
        hub = themes.get(theme_id)
        out.append(f"### {hub['title'] if hub else theme_id}")

        if hub is not None:
            links = []
            if hub["series"] != "":
                links.append(f"{words['index.theme.series']}: `{hub['series']}`")
            if hub["category"] != "":
                links.append(f"{words['index.theme.category']}: `{hub['category']}`")
            meta_line = f"[{words['index.theme.hub']}]({hub['file']})"
            if links:
                meta_line += " · " + " · ".join(links)
            out.append(f"_{meta_line}_")
            if hub["description"] != "":
                out.append(hub["description"])
        else:
            out.append(say(words, "index.theme.nohub", id=theme_id))

        members = [f for f in findings.values() if theme_id in f["themes"]]
        out += [finding_line(f, words) for f in sort_by_status(members)]
        out.append("")

    return out


def section_by_status(findings: dict, words: dict[str, str]) -> list[str]:
    out = [f"## {say(words, 'index.status.heading')}", ""]
    for status in STATUS_ORDER:
        members = [f for f in findings.values() if f["status"] == status]
        if not members:
            continue
        out.append(f"### {status_word(words, status)}")
        for finding in sort_by_title(members):
            line = f"- [{finding['title']}]({finding['file']})"
            if finding["themes"]:
                line += " — _" + ", ".join(finding["themes"]) + "_"
            if finding["gloss"] != "":
                line += f" — {finding['gloss']}"
            out.append(line)
        out.append("")
    return out


def section_by_claim_type(findings: dict, words: dict[str, str]) -> list[str]:
    out = [f"## {say(words, 'index.claim.heading')}", ""]
    for claim_type in VALID_CLAIM_TYPES:
        members = [f for f in findings.values() if f["claim_type"] == claim_type]
        if not members:
            continue
        out.append(f"### {say(words, 'index.claim.' + claim_type)}")
        out += [finding_line(f, words) for f in sort_by_status(members)]
        out.append("")
    unmarked = [f for f in findings.values() if f["claim_type"] == ""]
    if unmarked:
        out.append(f"### {say(words, 'index.claim.unmarked')}")
        out += [f"- [{f['title']}]({f['file']})" for f in sort_by_title(unmarked)]
        out.append("")
    return out


def section_by_origin(findings: dict, words: dict[str, str]) -> list[str]:
    out = [f"## {say(words, 'index.origin.heading')}", ""]
    out.append(say(words, "index.origin.guidance"))
    out.append("")
    for origin in ("own", "mixed", "reused"):
        members = [f for f in findings.values() if f["origin"] == origin]
        if not members:
            continue
        out.append(f"### {say(words, 'index.origin.' + origin)}")
        out += [finding_line(f, words) for f in sort_by_status(members)]
        out.append("")
    unclassified = [f for f in findings.values() if f["origin"] == ""]
    if unclassified:
        out.append(f"### {say(words, 'index.origin.unclassified', n=len(unclassified))}")
        out += [f"- [{f['title']}]({f['file']})" for f in sort_by_title(unclassified)]
        out.append("")
    return out


def section_by_keyword(findings: dict, words: dict[str, str]) -> list[str]:
    out = [f"## {say(words, 'index.keyword.heading')}", ""]
    by_keyword: dict[str, list[dict]] = {}
    for finding in findings.values():
        for keyword in finding["keywords"]:
            by_keyword.setdefault(keyword, []).append(finding)
    if not by_keyword:
        out += [say(words, "index.keyword.none"), ""]
        return out
    for keyword in sorted(by_keyword.keys(), key=byte_key):
        links = [f"[{f['title']}]({f['file']})" for f in sort_by_status(by_keyword[keyword])]
        out.append(f"- **{keyword}** — " + " · ".join(links))
    out.append("")
    return out


def natural_key(text: str) -> tuple:
    """A sort key that compares digit runs as numbers and ignores case: "§ 9" before "§ 37"."""
    return tuple(int(part) if part.isdigit() else part
                 for part in re.split(r"(\d+)", text.casefold()))


def section_by_citation(findings: dict, words: dict[str, str]) -> list[str]:
    """Citations grouped by corpus (codes in alphabetical order), each corpus in natural order."""
    out = [f"## {say(words, 'index.citation.heading')}", ""]
    by_corpus: dict[str, dict[str, list[dict]]] = {}
    for finding in findings.values():
        for citation in finding["citations"]:
            code, reference = split_citation(citation)
            by_corpus.setdefault(code, {}).setdefault(reference, []).append(finding)
    if not by_corpus:
        out += [say(words, "index.citation.none"), ""]
        return out
    for code in sorted(by_corpus):
        out.append(f"### {code}")
        by_ref = by_corpus[code]
        for reference in sorted(by_ref, key=natural_key):
            members = by_ref[reference]
            links = [f"[{f['title']}]({f['file']})" for f in sort_by_status(members)]
            marker = " ⭑" if len(members) > 1 else ""
            out.append(f"- **{reference}**{marker} — " + " · ".join(links))
        out.append("")
    out.append(say(words, "index.citation.shared"))
    out.append("")
    return out


def section_relationships(findings: dict, words: dict[str, str]) -> list[str]:
    out = [f"## {say(words, 'index.relations.heading')}", ""]

    # Tensions: unordered unique pairs across tension_with edges.
    tensions: dict[str, tuple[dict, dict]] = {}
    for finding_id, finding in findings.items():
        for other in finding["tension_with"]:
            if other not in findings:
                continue
            low, high = (finding_id, other) if finding_id < other else (other, finding_id)
            tensions[f"{low}|{high}"] = (findings[low], findings[high])

    out.append(f"### {say(words, 'index.relations.tensions')}")
    if not tensions:
        out.append(say(words, "index.relations.tensions.none"))
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

    out.append(f"### {say(words, 'index.relations.orphans')}")
    if not orphans:
        out.append(say(words, "index.relations.orphans.none"))
    else:
        out += [f"- [{f['title']}]({f['file']})" for f in sort_by_title(orphans)]
    out.append("")

    return out


def section_threads(threads: dict, words: dict[str, str]) -> list[str]:
    out = [f"## {say(words, 'index.threads.heading')}", ""]
    if not threads:
        out += [say(words, "index.threads.none"), ""]
        return out
    for status in THREAD_STATUS_ORDER:
        members = [t for t in threads.values() if t["status"] == status]
        if not members:
            continue
        out.append(f"### {words['threads.status.' + status]}")
        for thread in sort_by_title(members):
            line = f"- [{thread['title']}]({thread['file']})"
            if thread["gloss"] != "":
                line += f" — {thread['gloss']}"
            out.append(line)
        out.append("")
    return out


def section_imported(packs: list[dict], words: dict[str, str]) -> list[str]:
    if not packs:
        return []
    out = [f"## {say(words, 'index.imported.heading')}", "", say(words, "index.imported.intro"), ""]
    for pack in packs:
        heading = f"### {pack['author']}"
        if pack["title"]:
            heading += f" — {pack['title']}"
        out.append(heading)
        details = [f"`imported/{pack['author_id']}/`"]
        if pack["version"]:
            details.append(say(words, "index.imported.version", version=pack["version"]))
        if pack["license"]:
            details.append(pack["license"])
        out.append("_" + " · ".join(details) + "_")
        for finding in sort_by_title(pack["findings"]):
            line = f"- [{finding['title']}]({finding['file']})"
            if finding["status"]:
                line += f" — _{status_word(words, finding['status'])}_"
            if finding["gloss"]:
                line += f" — {finding['gloss']}"
            out.append(line)
        out.append("")
    return out


def build_index(findings: dict, themes: dict, threads: dict, imported: list[dict],
                words: dict[str, str]) -> str:
    out = [
        say(words, "index.generated", script=SCRIPT_NAME),
        "",
        f"# {say(words, 'index.title')}",
        "",
        say(words, "index.intro", script=SCRIPT_NAME),
        "",
        say(words, "index.counts", n_findings=len(findings), n_themes=len(themes),
            n_threads=len(threads)),
        "",
    ]
    out += section_by_theme(findings, themes, words)
    out += section_by_status(findings, words)
    out += section_by_claim_type(findings, words)
    out += section_by_origin(findings, words)
    out += section_by_keyword(findings, words)
    out += section_by_citation(findings, words)
    out += section_relationships(findings, words)
    out += section_threads(threads, words)
    out += section_imported(imported, words)
    return "\n".join(out) + "\n"

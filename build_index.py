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

The corpus (parsing + loaders) lives in study_corpus.py and the Markdown
rendering in study_sections.py; this file is validation plus the CLI.

Standard library only — no dependencies, no install beyond Python itself.
"""

from __future__ import annotations

import sys

from study_corpus import (
    FINDINGS_DIR, INDEX_FILE, REQUIRED_FINDING_FIELDS, THEMES_DIR, THREADS_DIR, VALID_CLAIM_TYPES, VALID_ORIGINS, VALID_STATUSES,
    VALID_THREAD_STATUSES, linkable_id, load_findings, load_imported, load_themes,
    load_threads
)
from study_sections import build_index

# ── validation ───────────────────────────────────────────────────────────────

def validate(findings: dict, themes: dict, threads: dict,
             imported: list[dict] | None = None) -> tuple[list[str], list[str]]:
    """Validate the corpus. Returns (errors, notices); only errors fail --check."""
    errors: list[str] = []
    notices: list[str] = []
    ids = list(findings.keys())
    # An imported finding is a legitimate thing to point at. PACKS.md tells you
    # to use one by writing your own finding that builds_on or tension_with it,
    # so refusing that edge would make --check reject the documented workflow.
    # They remain read-only and in their own namespace — what changes here is
    # only that your own findings may *reference* them.
    imported_ids = {
        entry["id"] for pack in (imported or []) for entry in pack["findings"]
    }
    edge_targets = set(ids) | imported_ids
    # [[wikilinks]] may point to a finding OR a thread.
    link_targets = edge_targets | set(threads.keys())

    for finding_id, finding in findings.items():
        where = finding["file"]

        for field in REQUIRED_FINDING_FIELDS:
            if str(finding.get(field, "") or "").strip() == "":
                errors.append(f"{where}: missing required frontmatter field '{field}'")

        declared_id = str(finding["meta"].get("id", "") or "").strip()
        if declared_id != "" and declared_id != finding_id:
            errors.append(
                f"{where}: frontmatter id '{declared_id}' does not match filename id '{finding_id}'")

        # An error rather than a notice, because the failure it describes is
        # invisible: a [[link]] to an id outside the alphabet does not match the
        # wikilink pattern, so it is never seen as a link and never reported as
        # broken. It silently does nothing. Nothing else in this corpus fails
        # that quietly, which is why it is worth stopping the build over.
        if not linkable_id(finding_id):
            errors.append(
                f"{where}: id '{finding_id}' cannot be reached by a [[wikilink]] — "
                f"ids are lowercase letters, digits and dashes. Rename the file "
                f"(packs.py's slugify romanises a title into a legal id).")

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
                if target not in edge_targets:
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
            if target not in edge_targets:
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


def unresolved_sync_conflicts() -> list[str]:
    """
    Conflict files `sync.py` left beside an item (`<id>.md.remote`) — see SYNC.md.

    An error, not a notice: each one is an edit made in two places that nobody
    has reconciled yet, and a corpus that validates clean while holding one
    would let it be forgotten. A corpus that never syncs never has one, so this
    costs it nothing.
    """
    found = []
    for directory in (FINDINGS_DIR, THEMES_DIR, THREADS_DIR):
        if directory.is_dir():
            found += [f"{directory.name}/{path.name}" for path in sorted(directory.glob("*.md.remote"))]
    return [f"{name}: unresolved sync conflict — make the .md say what it should, delete "
            f"this file, and run: python sync.py run" for name in found]


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    check_only = "--check" in argv[1:]

    findings = load_findings()
    themes = load_themes()
    threads = load_threads()
    imported = load_imported()
    errors, notices = validate(findings, themes, threads, imported)
    errors += unresolved_sync_conflicts()

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

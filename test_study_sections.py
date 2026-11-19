#!/usr/bin/env python3
"""Tests for study_sections.py, the Markdown rendering behind INDEX.md.

Runs two ways, like test_study_text.py beside it: `python test_study_sections.py` needs
only the standard library, and pytest collects the same functions where it is installed.

The cases cover the ordering helpers, because ordering is the part of the index a reader
relies on without noticing: findings grouped by status, citations in natural order rather
than string order ("John 3:16" before "John 10:1").
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_corpus import STATUS_ORDER  # noqa: E402  (path set up above)
from study_sections import (  # noqa: E402
    build_index,
    finding_line,
    natural_key,
    section_by_citation,
    section_by_status,
    sort_by_status,
    sort_by_title,
)
from study_words import words_for  # noqa: E402

EN = words_for("default", "en")
LAW_DA = words_for("law", "da")


def finding(title: str, status: str = STATUS_ORDER[0], **fields: object) -> dict:
    """A finding record with every field the renderers read."""
    record = {"title": title, "status": status, "file": f"findings/{title}.md",
              "claim_type": "", "gloss": "", "citations": [], "themes": []}
    record.update(fields)
    return record


def test_natural_key_orders_sections() -> None:
    assert natural_key("§ 9") < natural_key("§ 37")
    assert natural_key("John 3:16") < natural_key("John 10:1")


def test_natural_key_ignores_case() -> None:
    assert natural_key("acts 1") == natural_key("Acts 1")


def test_sort_by_status_follows_the_status_order() -> None:
    late, early = STATUS_ORDER[-1], STATUS_ORDER[0]
    ordered = sort_by_status([finding("b", late), finding("a", early)])
    assert [f["status"] for f in ordered] == [early, late]


def test_sort_by_status_puts_an_unknown_status_last() -> None:
    ordered = sort_by_status([finding("a", "not-a-status"), finding("b", STATUS_ORDER[-1])])
    assert ordered[-1]["status"] == "not-a-status"


def test_sort_by_title_ignores_case() -> None:
    assert [f["title"] for f in sort_by_title([finding("beta"), finding("Alpha")])] == ["Alpha", "beta"]


def test_finding_line_without_tags() -> None:
    assert finding_line(finding("x"), EN) == f"- **{EN['findings.status.firm']}** — [x](findings/x.md)"


def test_finding_line_carries_claim_type_and_gloss() -> None:
    line = finding_line(finding("x", claim_type="exegesis", gloss="short"), EN)
    assert line.endswith(" `exegesis` — short")


def test_section_by_citation_says_so_when_empty() -> None:
    assert "_No citations yet._" in section_by_citation({}, EN)


def test_by_citation_marks_a_shared_reference() -> None:
    findings = {
        "a": finding("a", citations=["bible:John 3:16"]),
        "b": finding("b", citations=["bible:John 3:16", "bible:John 10:1"]),
    }
    lines = section_by_citation(findings, EN)
    shared = next(line for line in lines if line.startswith("- **John 3:16**"))
    single = next(line for line in lines if line.startswith("- **John 10:1**"))
    assert "⭑" in shared
    assert "⭑" not in single
    assert lines.index(shared) < lines.index(single)


def test_by_citation_groups_by_corpus() -> None:
    findings = {
        "a": finding("a", citations=["dklaw:Momsloven § 37", "bible:John 3:16", "dklaw:Momsloven § 9"]),
    }
    lines = section_by_citation(findings, EN)
    headings = [line for line in lines if line.startswith("### ")]
    assert headings == ["### bible", "### dklaw"]
    law = lines[lines.index("### dklaw"):]
    assert law.index(next(l for l in law if "§ 9**" in l)) < law.index(next(l for l in law if "§ 37**" in l))
    assert not any(line.startswith("- **bible:") for line in lines)


def test_status_heading_uses_words() -> None:
    lines = section_by_status({"a": finding("a", "hypothesis")}, LAW_DA)
    assert "### Idé" in lines


def test_index_has_no_english_in_law_da() -> None:
    findings = {"a": finding("a", "hypothesis", themes=["t"], keywords=["k"], origin="",
                             citations=["dklaw:Momsloven § 9"], related=[], builds_on=[],
                             tension_with=[], wikilinks=[])}
    imported = [{"author": "A", "author_id": "a", "title": "", "version": "1", "license": "",
                 "findings": []}]
    text = build_index(findings, {}, {"x": {"title": "x", "status": "open", "file": "threads/x.md",
                                             "gloss": ""}}, imported, LAW_DA)
    empty = build_index({}, {}, {}, [], LAW_DA)
    english = {k: v for k, v in words_for("default", "en").items()
               if k.startswith("index.") and not k.startswith("index.claim.")}
    for key, value in english.items():
        assert value not in text and value not in empty, key


def test_default_index_names_the_study_with_the_findings_noun() -> None:
    text = build_index({}, {}, {}, [], EN)
    assert "# Study Corpus Index" in text
    assert "## By citation" in text
    assert "biblical" not in text


def test_main_without_a_study_file_names_the_three_keys() -> None:
    import contextlib
    import io
    import tempfile

    import build_index

    saved = build_index.STUDY_DIR
    build_index.STUDY_DIR = Path(tempfile.mkdtemp())
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            code = build_index.main(["build_index.py", "--check"])
    finally:
        build_index.STUDY_DIR = saved
    assert code == 1
    for key in ("corpus", "word_set", "language"):
        assert key in stderr.getvalue()


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"{len(tests)} passed")

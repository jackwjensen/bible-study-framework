#!/usr/bin/env python3
"""Tests for study_sections.py, the Markdown rendering behind INDEX.md.

Runs two ways, like test_study_text.py beside it: `python test_study_sections.py` needs
only the standard library, and pytest collects the same functions where it is installed.

The cases cover the ordering helpers, because ordering is the part of the index a reader
relies on without noticing: findings grouped by status, scripture in canonical order rather
than string order ("John 3:16" before "John 10:1").
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_corpus import STATUS_ORDER  # noqa: E402  (path set up above)
from study_sections import (  # noqa: E402
    finding_line,
    scripture_compare,
    scripture_parts,
    section_by_scripture,
    sort_by_status,
    sort_by_title,
)


def finding(title: str, status: str = STATUS_ORDER[0], **fields: object) -> dict:
    """A finding record with every field the renderers read."""
    record = {"title": title, "status": status, "file": f"findings/{title}.md",
              "claim_type": "", "gloss": "", "scripture": []}
    record.update(fields)
    return record


def test_scripture_parts_splits_book_chapter_and_verse() -> None:
    assert scripture_parts("1 John 4:8") == ("1 John", (4, 8))


def test_scripture_parts_reads_a_chapter_without_a_verse() -> None:
    assert scripture_parts("Psalm 23") == ("Psalm", (23, 0))


def test_scripture_parts_keeps_an_unparseable_reference_whole() -> None:
    assert scripture_parts("Genesis") == ("Genesis", (0, 0))


def test_scripture_compare_orders_chapters_numerically() -> None:
    assert scripture_compare("John 3:16", "John 10:1") == -1


def test_scripture_compare_orders_books_before_numbers() -> None:
    assert scripture_compare("Acts 28:1", "John 1:1") == -1


def test_scripture_compare_equal_references() -> None:
    assert scripture_compare("John 3:16", "John 3:16") == 0


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
    assert finding_line(finding("x")) == f"- **{STATUS_ORDER[0]}** — [x](findings/x.md)"


def test_finding_line_carries_claim_type_and_gloss() -> None:
    line = finding_line(finding("x", claim_type="exegesis", gloss="short"))
    assert line.endswith(" `exegesis` — short")


def test_section_by_scripture_says_so_when_empty() -> None:
    assert "_No scripture references yet._" in section_by_scripture({})


def test_section_by_scripture_marks_a_shared_reference() -> None:
    findings = {
        "a": finding("a", scripture=["John 3:16"]),
        "b": finding("b", scripture=["John 3:16", "John 10:1"]),
    }
    lines = section_by_scripture(findings)
    shared = next(line for line in lines if line.startswith("- **John 3:16**"))
    single = next(line for line in lines if line.startswith("- **John 10:1**"))
    assert "⭑" in shared
    assert "⭑" not in single
    assert lines.index(shared) < lines.index(single)


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"{len(tests)} passed")

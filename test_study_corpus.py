#!/usr/bin/env python3
"""Tests for study_corpus.py's citation shape, and build_index.py's refusal of the old one.

A finding's `citations:` each name their corpus ("bible:John 3:16"); `scripture:` is gone and
is refused rather than read.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_index import validate  # noqa: E402  (path set up above)
from study_corpus import split_citation  # noqa: E402


def finding(meta: dict | None = None, citations: list[str] | None = None) -> dict:
    """A finding record with every field validate() reads."""
    return {"id": "a", "file": "findings/a.md", "meta": meta or {}, "body": "", "title": "A",
            "status": "firm", "claim_type": "", "origin": "", "themes": [], "keywords": [],
            "citations": citations or [], "related": [], "builds_on": [], "tension_with": [],
            "sources": [], "posts": [], "wikilinks": []}


def test_split_citation_takes_the_first_colon() -> None:
    assert split_citation("bible:John 3:16") == ("bible", "John 3:16")


def test_split_citation_refuses_bare() -> None:
    with pytest.raises(ValueError):
        split_citation("John 3:16")


def test_split_citation_keeps_section_sign() -> None:
    assert split_citation("dklaw:Momsloven § 37, stk. 1") == ("dklaw", "Momsloven § 37, stk. 1")


def test_validation_refuses_scripture_field() -> None:
    errors, _ = validate({"a": finding(meta={"scripture": ["John 3:16"]})}, {}, {})
    assert any("renamed to citations" in error for error in errors)


def test_validation_refuses_unqualified_citation() -> None:
    errors, _ = validate({"a": finding(citations=["John 3:16"])}, {}, {})
    assert any("John 3:16" in error and "corpus" in error for error in errors)


def test_validation_accepts_qualified_citation() -> None:
    errors, _ = validate({"a": finding(citations=["bible:John 3:16"])}, {}, {})
    assert errors == []

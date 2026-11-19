#!/usr/bin/env python3
"""Tests for study_words.py and the word_sets.json it reads."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_words import UnknownWordSet, words_for  # noqa: E402

DATA = json.loads((Path(__file__).resolve().parent / "word_sets.json").read_text(encoding="utf-8"))


def test_default_en_holds_every_key() -> None:
    every = {key for langs in DATA["sets"].values() for table in langs.values() for key in table}
    assert every <= set(DATA["sets"]["default"]["en"])


def test_law_da_status_words() -> None:
    words = words_for("law", "da")
    assert words["findings.status.hypothesis"] == "Idé"
    assert words["findings.status.established"] == "Klar til at fremføre"
    # A key law/da does not say falls back to default/en.
    assert words["index.claim.asserted"].startswith("asserted")


def test_law_da_counts_use_law_nouns() -> None:
    words = words_for("law", "da")
    assert words["count.finding.one"] == "argument"
    assert words["count.finding.other"] == "argumenter"
    law_da = DATA["sets"]["law"]["da"]
    default_en = DATA["sets"]["default"]["en"]
    counts = {k: v for k, v in law_da.items() if k.startswith("count.")}
    assert len(counts) == 6
    assert all(v != default_en[k] for k, v in counts.items())


def test_unknown_set_raises() -> None:
    with pytest.raises(UnknownWordSet):
        words_for("nope", "en")


def test_missing_language_falls_back_to_set_en_then_default() -> None:
    words = words_for("law", "fr")
    assert words["count.finding.one"] == "argument"
    assert words["threads.status.open"] == "Open"
    assert words_for("default", "fr") == DATA["sets"]["default"]["en"]


def test_default_da_overlays_default_en() -> None:
    words = words_for("default", "da")
    assert words["findings.status.firm"] == "Fast"
    assert words["adopt.confirm"] == "I believe this to be true"

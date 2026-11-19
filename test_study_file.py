#!/usr/bin/env python3
"""Tests for study_file.py: study.md names the corpus, word set and language once."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_corpus import split_citation
from study_file import Study, StudyFileError, load_study


def write_study(root: Path, text: str) -> None:
    (root / "study.md").write_text(text, encoding="utf-8")


def test_load_study_reads_three_keys(tmp_path: Path) -> None:
    write_study(tmp_path, "---\ncorpus: bible\nword_set: default\nlanguage: en\n---\n\nAbout.\n")
    assert load_study(tmp_path) == Study(corpus="bible", word_set="default", language="en")


def test_missing_study_file_names_what_to_write(tmp_path: Path) -> None:
    with pytest.raises(StudyFileError) as error:
        load_study(tmp_path)
    message = str(error.value)
    assert "study.md" in message
    for key in ("corpus", "word_set", "language"):
        assert key in message


def test_a_missing_key_is_named(tmp_path: Path) -> None:
    write_study(tmp_path, "---\ncorpus: bible\nlanguage: en\n---\n")
    with pytest.raises(StudyFileError, match="word_set"):
        load_study(tmp_path)


@pytest.mark.parametrize("corpus", ["Bible", "bi ble", "bible:x", "bible-x", ""])
def test_corpus_must_match_citation_shape_code(tmp_path: Path, corpus: str) -> None:
    write_study(tmp_path, f'---\ncorpus: "{corpus}"\nword_set: default\nlanguage: en\n---\n')
    with pytest.raises(StudyFileError, match="corpus"):
        load_study(tmp_path)
    # The same code that is refused here is refused as the front of a citation.
    if corpus and ":" not in corpus:
        with pytest.raises(ValueError):
            split_citation(f"{corpus}:John 3:16")


def test_a_corpus_code_the_citations_accept_is_accepted(tmp_path: Path) -> None:
    write_study(tmp_path, "---\ncorpus: dklaw\nword_set: law_da\nlanguage: da\n---\n")
    assert load_study(tmp_path).corpus == "dklaw"
    assert split_citation("dklaw:Momsloven § 37")[0] == "dklaw"


@pytest.mark.parametrize("key, value", [("word_set", "Default Set"), ("language", "English")])
def test_word_set_and_language_are_checked_for_shape(tmp_path: Path, key: str, value: str) -> None:
    values = {"corpus": "bible", "word_set": "default", "language": "en", key: value}
    front = "\n".join(f'{k}: "{v}"' for k, v in values.items())
    write_study(tmp_path, f"---\n{front}\n---\n")
    with pytest.raises(StudyFileError, match=key):
        load_study(tmp_path)


def test_a_trailing_newline_is_not_a_citation() -> None:
    with pytest.raises(ValueError):
        split_citation("bible:John 3:16\n")

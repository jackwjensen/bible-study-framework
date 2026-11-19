#!/usr/bin/env python3
"""
study_file.py — `study.md`, where a study says once which corpus it studies, which word set
its display words come from, and which language it is written in.

Three keys in the frontmatter, all required:

    ---
    corpus: bible
    word_set: default
    language: en
    ---

The corpus is the code every citation in the study starts with ("bible:John 3:16"), and the
code a sync server files the study under. It is the same code shape a citation uses, so a
study cannot name a corpus its own citations could not.

Standard library only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from study_corpus import CORPUS_CODE
from study_frontmatter import load_content

STUDY_FILE_NAME = "study.md"
KEYS = ("corpus", "word_set", "language")

#: Word sets are named in lowercase with digits, dashes and underscores.
_WORD_SET = re.compile(r"[a-z0-9_-]+")
#: A short language code: "en", "da", or with a region, "pt-br".
_LANGUAGE = re.compile(r"[a-z]{2,3}(-[a-z0-9]{2,8})?")
_CORPUS = re.compile(CORPUS_CODE)

_SHAPES = {
    "corpus": (_CORPUS, "lowercase letters and digits only, e.g. bible"),
    "word_set": (_WORD_SET, "lowercase letters, digits, dashes and underscores, e.g. default"),
    "language": (_LANGUAGE, "a short language code, e.g. en or da"),
}


class StudyFileError(Exception):
    """study.md is missing or wrong. The message is written for the person who owns the study."""


@dataclass(frozen=True)
class Study:
    corpus: str
    word_set: str
    language: str


def load_study(root: Path) -> Study:
    path = root / STUDY_FILE_NAME
    content = load_content(path)
    if content is None:
        raise StudyFileError(
            f"{STUDY_FILE_NAME} is missing. Create it at the root of the study with the frontmatter "
            f"keys {', '.join(KEYS)}, for example:\n---\ncorpus: bible\nword_set: default\n"
            f"language: en\n---")
    values: dict[str, str] = {}
    for key in KEYS:
        raw = content["meta"].get(key, "")
        value = raw.strip() if isinstance(raw, str) else ""
        if value == "":
            raise StudyFileError(f"{STUDY_FILE_NAME} does not set '{key}'. It needs all of: "
                                 f"{', '.join(KEYS)}.")
        pattern, hint = _SHAPES[key]
        if pattern.fullmatch(value) is None:
            raise StudyFileError(f"{STUDY_FILE_NAME}: '{key}: {value}' is not valid; "
                                 f"use {hint}.")
        values[key] = value
    return Study(**values)

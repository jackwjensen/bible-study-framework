#!/usr/bin/env python3
"""The words a study is shown in, per word set and language, read from word_sets.json.

Display only: stored codes never change. A set overlays the default set; a language a set
lacks falls back to that set's English, then to the default set's English.
"""

from __future__ import annotations

import json
from pathlib import Path

WORD_SETS_FILE = Path(__file__).resolve().parent / "word_sets.json"
DEFAULT_SET = "default"
FALLBACK_LANGUAGE = "en"


class UnknownWordSet(ValueError):
    """The named word set is not in word_sets.json."""


def _load() -> dict[str, dict[str, dict[str, str]]]:
    with WORD_SETS_FILE.open(encoding="utf-8") as handle:
        return json.load(handle)["sets"]


def words_for(word_set: str, language: str) -> dict[str, str]:
    """Every key's text for this set and language, with fallbacks applied."""
    sets = _load()
    if word_set not in sets:
        raise UnknownWordSet(f"unknown word set {word_set!r}; known: {', '.join(sorted(sets))}")
    default = sets[DEFAULT_SET]
    chosen = sets[word_set]
    words = dict(default[FALLBACK_LANGUAGE])
    if language in default:
        words.update(default[language])
    if word_set != DEFAULT_SET:
        words.update(chosen.get(language, chosen.get(FALLBACK_LANGUAGE, {})))
    return words

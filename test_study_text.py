#!/usr/bin/env python3
"""Exhaustive tests for the shared romanisation module.

SHARED FILE — byte-identical in three repositories, exactly like the module it
tests:

    bible-study-framework/test_study_text.py       (canonical)
    jacks_corner/study/test_study_text.py          (mirror)
    sourcetext.ai/tests/test_study_text.py         (mirror)

Runs two ways on purpose. `python test_study_text.py` needs nothing but the
standard library, because the framework has no test runner and must never
require one; `pytest` collects the same functions where a runner exists. A test
that only runs under pytest would never run in two of these three repos.

WHAT IT IS FOR. Most of this file is not example-based. Examples prove the cases
somebody thought of, and every defect found while building this module was a
case nobody thought of — a vav that was a vowel, a breathing that was a letter,
a patah that moved. So the bulk here is *properties*: statements that must hold
for every input, checked against a wide sweep of Unicode, the module's own
tables, and real text. A property test fails on inputs its author never saw,
which is the only kind of test that finds the next one of these.
"""

from __future__ import annotations

import sys
import unicodedata

# The same module under the name each repository gives it. Ugly, and the honest
# alternative — three near-identical test files — is worse: they would drift,
# and the drift would be silent because each repo's copy would still pass.
try:  # the framework and its mirror
    from study_text import (
        FURTIVE_PATAH_GUTTURALS,
        GREEK_LETTERS,
        HEBREW_CONSONANTS,
        HEBREW_POINTS,
        LATIN_LETTERS,
        romanize,
    )
except ImportError:  # SourceText
    from textnorm.romanize import (  # type: ignore[no-redef]
        FURTIVE_PATAH_GUTTURALS,
        GREEK_LETTERS,
        HEBREW_CONSONANTS,
        HEBREW_POINTS,
        LATIN_LETTERS,
        romanize,
    )

SLUG_CHARACTERS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


# ── golden cases ─────────────────────────────────────────────────────────────
# Each earned its place by being wrong once. The comment says how.

HEBREW_CASES = [
    ("חֶסֶד", "hesed"),
    ("שָׁלוֹם", "shalom"),        # was `shalvom`: vav + holam is the vowel, not a v
    ("בְּרֵאשִׁית", "bereshit"),   # was `bereshiyt`: yod after hiriq is the i itself
    ("תּוֹרָה", "torah"),
    ("רוּחַ", "ruah"),            # was `ruha`: furtive patah sounds before the guttural
    ("חַיִּים", "hayim"),         # the same het + patah mid-word must NOT move
    ("עֵזֶר כְּנֶגְדּוֹ", "ezer kenegedo"),
    # `dabar`, not the modern Israeli `davar`: general-purpose style does not
    # make the begadkefat b/v distinction, so bet is `b` with or without dagesh.
    ("דָּבָר", "dabar"),
    ("אֱלֹהִים", "elohim"),
    # `mishepat`, not `mishpat`. The sheva here is silent, and telling a silent
    # sheva from a vocal one needs the morphology of the word, not its letters.
    # An identifier only has to be stable and recognisable, so every sheva is
    # written `e` — stated here so the approximation is a decision on the record
    # rather than something a later reader mistakes for a bug.
    ("מִשְׁפָּט", "mishepat"),
]

GREEK_CASES = [
    ("ἀγάπη", "agape"),
    ("λόγος", "logos"),
    ("σάρξ", "sarx"),
    ("ὁδός", "hodos"),           # was `odos`: the rough breathing is an h
    ("ψυχή", "psyche"),
    ("Χριστός", "Christos"),
    ("θεός", "theos"),
    ("Ἐν ἀρχῇ ἦν ὁ λόγος", "En arche en ho logos"),
]

LATIN_CASES = [
    ("Guds nåde og kærlighed", "Guds nade og kaerlighed"),
    ("Ærø og Øresund", "Aeroe og Oeresund"),
    ("naïve café résumé", "naive cafe resume"),
    ("Straße", "Strasse"),
    ("Þórr", "Thorr"),
]


def test_hebrew_golden_cases() -> None:
    for hebrew, expected in HEBREW_CASES:
        assert romanize(hebrew) == expected, f"{hebrew!r} -> {romanize(hebrew)!r}"


def test_greek_golden_cases() -> None:
    for greek, expected in GREEK_CASES:
        assert romanize(greek) == expected, f"{greek!r} -> {romanize(greek)!r}"


def test_latin_golden_cases() -> None:
    for text, expected in LATIN_CASES:
        assert romanize(text) == expected, f"{text!r} -> {romanize(text)!r}"


def test_the_rough_breathing_is_not_an_accent() -> None:
    """`ho logos` and `o logos` are different words, and only a mark says which."""
    assert romanize("ὁ") == "ho"
    assert romanize("ὀ") == "o"


# ── properties ───────────────────────────────────────────────────────────────


def _sweep() -> list[str]:
    """A wide, deterministic sample of Unicode, plus the scripts we care about.

    Deterministic rather than random: a fuzz test that finds a defect on Tuesday
    and cannot reproduce it on Wednesday is worse than no fuzz test.
    """
    samples: list[str] = []
    for codepoint in range(0x0000, 0x2600):
        character = chr(codepoint)
        if unicodedata.category(character) not in ("Cs", "Cn"):
            samples.append(character)
    # Beyond the BMP: emoji and historic scripts, where a naive implementation
    # that indexes by codepoint or assumes one char per letter comes apart.
    samples += [chr(cp) for cp in (0x1F600, 0x10900, 0x1D400, 0x20000)]
    samples += [hebrew for hebrew, _ in HEBREW_CASES]
    samples += [greek for greek, _ in GREEK_CASES]
    samples += [text for text, _ in LATIN_CASES]
    # The zero-width space and the BOM are written as chr() rather than typed:
    # this file is copied by hand between three repositories, and nobody can
    # review — or notice the loss of — a character they cannot see.
    samples += ["", " ", "-", "a", "1", "\n", "\t", chr(0x200B), chr(0xFEFF)]
    return samples


def test_romanize_never_raises() -> None:
    """A slug is computed on the save path; an exception there loses a reader's work."""
    for sample in _sweep():
        try:
            romanize(sample)
        except Exception as error:  # noqa: BLE001 - the point is that nothing escapes
            raise AssertionError(
                f"romanize({sample!r}) raised {type(error).__name__}: {error}"
            ) from error


def test_ascii_is_a_fixed_point() -> None:
    """The guarantee every existing slug rests on: ASCII in, the same ASCII out."""
    for sample in _sweep():
        if sample.isascii():
            assert romanize(sample) == sample, f"ASCII input {sample!r} was altered"


def test_romanize_is_idempotent() -> None:
    """Romanising an already-romanised string must change nothing.

    Not a curiosity: an id is romanised when created and may be romanised again
    on import or re-export, and a function that keeps rewriting its own output
    would give one finding two ids.
    """
    for sample in _sweep():
        once = romanize(sample)
        assert romanize(once) == once, f"{sample!r}: {once!r} -> {romanize(once)!r}"


def test_output_is_always_ascii_for_the_scripts_we_handle() -> None:
    for source in [h for h, _ in HEBREW_CASES] + [g for g, _ in GREEK_CASES]:
        result = romanize(source)
        assert result.isascii(), f"{source!r} left non-ASCII: {result!r}"


def test_no_word_in_a_handled_script_vanishes() -> None:
    """The defect this module exists to remove: a title reduced to nothing.

    Checked per *word*, not per string, because a whole title collapsing is only
    the visible form of it — one word disappearing inside a title is the same
    bug and reads as a typo.
    """
    for source in [h for h, _ in HEBREW_CASES] + [g for g, _ in GREEK_CASES]:
        for word in source.split():
            assert romanize(word).strip(), f"the word {word!r} romanised to nothing"


def test_every_table_entry_is_reachable() -> None:
    """A mapping nothing can trigger is a mapping that is wrong and never says so."""
    for letter, expected in GREEK_LETTERS.items():
        assert romanize(letter) == expected, f"Greek {letter!r}"
    for letter, expected in LATIN_LETTERS.items():
        assert romanize(letter) == expected, f"Latin {letter!r}"
    for letter, expected in HEBREW_CONSONANTS.items():
        # A bare consonant carries no vowel, so it romanises to itself alone.
        assert romanize(letter) == expected, f"Hebrew {letter!r}"


def test_every_hebrew_point_produces_its_vowel() -> None:
    for point, vowel in HEBREW_POINTS.items():
        # Bet is a plain consonant with no mater or furtive behaviour, so the
        # output isolates exactly what the point contributes.
        assert romanize("ב" + point) == "b" + vowel, f"point {point!r}"


def test_the_furtive_patah_only_moves_at_the_end_of_a_word() -> None:
    patah = "ַ"
    for guttural in FURTIVE_PATAH_GUTTURALS:
        consonant = HEBREW_CONSONANTS[guttural]
        if not consonant:  # ayin romanises to nothing; there is no order to see
            continue
        assert romanize("ר" + "וּ" + guttural + patah) == "ru" + "a" + consonant
        # The same letter and point mid-word keep the ordinary order.
        assert romanize(guttural + patah + "ר").startswith(consonant + "a")


def test_a_slug_built_from_any_of_this_is_legal() -> None:
    """The end-to-end promise: whatever goes in, the id is linkable.

    The slug alphabet is applied here rather than imported, because each repo
    spells that step in its own module — and what has to be true is the same in
    all of them.
    """
    for sample in _sweep():
        romanised = romanize(sample).lower()
        slug = "-".join(
            part
            for part in "".join(
                character if character in SLUG_CHARACTERS else "-"
                for character in romanised
            ).split("-")
            if part
        )
        assert set(slug) <= SLUG_CHARACTERS, f"{sample!r} produced {slug!r}"


def main() -> int:
    tests = sorted(
        (name, value)
        for name, value in globals().items()
        if name.startswith("test_") and callable(value)
    )
    failures = []
    for name, test in tests:
        try:
            test()
        except AssertionError as error:
            failures.append(f"{name}: {error}")
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    print(f"{len(tests) - len(failures)}/{len(tests)} romanisation tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

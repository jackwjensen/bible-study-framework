"""Latin-script text from any script, for identifiers that must stay typeable.

SHARED FILE — byte-identical in three repositories, and a test in each asserts it:

    bible-study-framework/study_text.py        (canonical)
    jacks_corner/study/study_text.py           (mirror)
    sourcetext.ai/textnorm/romanize.py         (mirror)

Copy it whole; never edit one copy. It imports nothing but ``unicodedata``, which
is what lets the same bytes sit under three different names — and what keeps the
framework standalone, since it must never need SourceText to work.

WHY IT EXISTS. A slug is an address, not text: the target of a ``[[wikilink]]``
typed mid-sentence, a filename inside a published pack, a URL segment. The
wikilink regex is ``[a-z0-9-]+``, so a slug outside that alphabet cannot be
linked at all — and both tools used to produce exactly such slugs, in different
ways. SourceText *deleted* what the alphabet could not hold, so a finding titled
``עֵזֶר כְּנֶגְדּוֹ`` became ``finding``, the next ``finding-2``. The framework
*kept* it, via a Unicode-aware ``isalnum()``, so ``slugify`` happily produced
``עזר-כנגדו`` and ``slug_ok`` approved it — and then no ``[[…]]`` could ever
match it, silently, with nothing reported. Both are the same bug: an identifier
nobody can link.

TRANSLITERATION, NEVER TRANSLATION. Translating a title into English would put a
*meaning* judgement inside a permanent public identifier. ``ezer kenegdo`` is the
most contested phrase in the debate these tools refuse to adjudicate, and a slug
reading ``helper-corresponding-to-him`` would settle it silently, forever, on
every subscriber's machine. Romanising settles nothing: it is the same word in
different letters, and it is what English-language scholarship already writes. It
is also deterministic and offline, which an identifier has to be.

AN IDENTIFIER SCHEME, NOT A SCHOLARLY ONE. It targets SBL's general-purpose
style, which drops the diacritics academic style keeps (``ḥesed`` → ``hesed``,
``ʿezer`` → ``ezer``) — and that is what makes the choice of scheme mostly moot:
SBL, ISO 259 and ALA-LC differ chiefly in diacritics, and the ``a-z0-9-`` target
strips those anyway, so all three converge here. Where they still disagree the
tie-break is *recognisability to a reader typing a link*, never precision.

Arabic is deliberately absent until there is Arabic text to test against. A table
nobody can check against real words is a table that is wrong without anyone
knowing.
"""

from __future__ import annotations

import unicodedata

# Latin letters with no canonical decomposition, so NFKD cannot reach them.
# `å` is absent on purpose: it decomposes to `a` + ring, which NFKD already
# handles, and Danish `aa` would then need a rule for when it is a ring and when
# it is two letters.
LATIN_LETTERS = {
    "æ": "ae", "ø": "oe", "œ": "oe", "ß": "ss",
    "þ": "th", "ð": "d", "ł": "l", "đ": "d", "ħ": "h", "ı": "i",
}

# Greek. The accents and breathings are combining marks and are stripped before
# this map is reached, so only the base letters need an entry. Final sigma folds
# to sigma for the same reason search does: where a reader puts it is not a
# distinction worth carrying into an identifier.
GREEK_LETTERS = {
    "α": "a", "β": "b", "γ": "g", "δ": "d", "ε": "e", "ζ": "z",
    "η": "e", "θ": "th", "ι": "i", "κ": "k", "λ": "l", "μ": "m",
    "ν": "n", "ξ": "x", "ο": "o", "π": "p", "ρ": "r", "σ": "s",
    "ς": "s", "τ": "t", "υ": "y", "φ": "ph", "χ": "ch", "ψ": "ps",
    "ω": "o",
}

# Hebrew consonants, including the five final forms, which romanise as their
# base letter. Alef and ayin become nothing: general-purpose style drops them,
# and `'ezer` would lose the apostrophe to the slug alphabet regardless.
HEBREW_CONSONANTS = {
    "א": "", "ב": "b", "ג": "g", "ד": "d", "ה": "h", "ו": "v", "ז": "z",
    "ח": "h", "ט": "t", "י": "y", "כ": "k", "ך": "k", "ל": "l", "מ": "m",
    "ם": "m", "נ": "n", "ן": "n", "ס": "s", "ע": "", "פ": "p", "ף": "p",
    "צ": "ts", "ץ": "ts", "ק": "q", "ר": "r", "ש": "sh", "ת": "t",
}

# Hebrew is consonantal, so the vowels live in the points — and this is the one
# place romanising must run BEFORE marks are stripped. Fold first and `עֵזֶר`
# becomes `zr` rather than `ezer`, which is the whole word gone.
HEBREW_POINTS = {
    "ְ": "e",  # sheva      - see the note in _romanize_hebrew about vocal vs silent
    "ֱ": "e",  # hataf segol
    "ֲ": "a",  # hataf patah
    "ֳ": "o",  # hataf qamats
    "ִ": "i",  # hiriq
    "ֵ": "e",  # tsere
    "ֶ": "e",  # segol
    "ַ": "a",  # patah
    "ָ": "a",  # qamats
    "ֹ": "o",  # holam
    "ֺ": "o",  # holam haser for vav
    "ֻ": "u",  # qubuts
    "ׇ": "o",  # qamats qatan
}

# Marks that carry no vowel: dagesh, meteg, rafe and the shin/sin dots. Written
# as codepoints because a combining mark is INVISIBLE in source -- the first
# version of this table was a string literal of eight of them, and neither a
# reader nor an editor's search could tell which were present or in what order.
DAGESH = "ּ"
_SILENT_HEBREW_MARKS = frozenset(
    {
        DAGESH,       # dagesh / mapiq
        "ֽ",     # meteg
        "ֿ",     # rafe
        "ׁ",     # shin dot
        "ׂ",     # sin dot
    }
)

#: U+0314, the Greek rough breathing. It is an `h` in front of the vowel it sits
#: on, which is why breathings cannot be dropped along with the accents:
#: `ὁ λόγος` is `ho logos`, and `o logos` is a different word.
ROUGH_BREATHING = "̔"

HOLAM = "ֹ"
HOLAM_HASER_FOR_VAV = "ֺ"

#: The two consonants that double as vowels — a *mater lectionis*. Named because
#: the letters themselves are the only reason `_romanize_hebrew` needs to look at
#: what came before: `שָׁלוֹם` is `shalom`, not `shalvom`.
VAV = "ו"
YOD = "י"
HIRIQ_VOWEL = "i"

#: A patah under one of these, at the end of a word, is *furtive*: written after
#: the consonant and pronounced before it.
PATAH = "ַ"
FURTIVE_PATAH_GUTTURALS = frozenset({"ח", "ע", "ה"})

_HEBREW_BLOCK = range(0x0590, 0x0600)


def _is_hebrew(character: str) -> bool:
    return ord(character) in _HEBREW_BLOCK


def romanize(text: str) -> str:
    """Latin-script text, keeping word boundaries and leaving ASCII untouched.

    ASCII in, identical ASCII out — so routing an English title through this is
    free, which is what lets one implementation serve both callers.

    >>> romanize("Guds nåde og kærlighed")
    'Guds nade og kaerlighed'
    >>> romanize("Ἐν ἀρχῇ ἦν ὁ λόγος")
    'En arche en ho logos'
    """
    if text.isascii():
        return text

    # Decomposed, so Greek accents and Latin diacritics separate from their
    # letters and can be dropped, while Hebrew points — which are *vowels*, not
    # decoration — stay addressable in the same pass.
    decomposed = unicodedata.normalize("NFD", text)

    pieces: list[str] = []
    previous_vowel = ""
    index = 0
    while index < len(decomposed):
        character = decomposed[index]

        # A letter and the marks that belong to it are one unit. Reading them
        # together is what makes a Hebrew vowel and a Greek breathing reachable;
        # character-at-a-time, `וֹ` romanises as "vo" instead of "o".
        end = index + 1
        while end < len(decomposed) and unicodedata.category(decomposed[end]).startswith("M"):
            end += 1
        marks = decomposed[index + 1 : end]
        index = end

        if _is_hebrew(character):
            following = decomposed[index] if index < len(decomposed) else ""
            romanised, previous_vowel = _romanize_hebrew(
                character,
                marks,
                previous_vowel,
                at_word_end=not (following and _is_hebrew(following)),
            )
            pieces.append(romanised)
            continue

        previous_vowel = ""
        if unicodedata.category(character).startswith("M"):
            continue  # a stray mark with no base letter

        lowered = character.lower()
        if lowered in GREEK_LETTERS:
            # The rough breathing IS an h and belongs in front of the vowel;
            # the smooth breathing is its absence and vanishes with the accents.
            replacement = ("h" if ROUGH_BREATHING in marks else "") + GREEK_LETTERS[lowered]
        elif lowered in LATIN_LETTERS:
            replacement = LATIN_LETTERS[lowered]
        else:
            pieces.append(character)
            continue

        # Keep the case the writer used, so a title stays a title. Only the
        # first letter of a digraph is raised: "Th", not "TH".
        pieces.append(
            replacement.capitalize() if character.isupper() else replacement
        )

    return "".join(pieces)


def _romanize_hebrew(
    letter: str, marks: str, previous_vowel: str, *, at_word_end: bool
) -> tuple[str, str]:
    """One Hebrew letter with its points, and the vowel it leaves behind.

    The vowel is returned because a *mater lectionis* — a consonant standing in
    for a vowel already written — can only be recognised by what came before it.
    Without that, `בְּרֵאשִׁית` romanises as `bereshiyt`: the final yod is not a
    consonant there, it is the `i` spelled out.

    `at_word_end` is needed for the opposite reason — the *furtive patah*, which
    is written under a word-final guttural but pronounced before it: `רוּחַ` is
    `ruah`, not `ruha`. It cannot be a rule about the letter alone, because the
    same het with the same patah mid-word is an ordinary syllable (`חַיִּים` is
    `hayyim`, and an unconditional rule would make it `ahyyim`).
    """
    if letter in HEBREW_POINTS:  # a point with no consonant before it
        return HEBREW_POINTS[letter], HEBREW_POINTS[letter]
    if letter in _SILENT_HEBREW_MARKS:
        return "", previous_vowel

    vowel = "".join(HEBREW_POINTS[mark] for mark in marks if mark in HEBREW_POINTS)

    spelled = _vowel_this_consonant_spells(letter, marks, vowel, previous_vowel)
    if spelled is not None:
        return spelled

    consonant = HEBREW_CONSONANTS.get(letter, "")
    if at_word_end and letter in FURTIVE_PATAH_GUTTURALS and PATAH in marks:
        return vowel + consonant, vowel

    return consonant + vowel, vowel


def _vowel_this_consonant_spells(
    letter: str, marks: str, vowel: str, previous_vowel: str
) -> tuple[str, str] | None:
    """What a *mater lectionis* contributes, or None if the letter is a consonant.

    Vav and yod do double duty in Hebrew: usually consonants, but in three
    settings they are how a long vowel is written. Treating those as consonants
    is what turns `שָׁלוֹם` into `shalvom` and `בְּרֵאשִׁית` into `bereshiyt`.

    The pair is `(what to write, which vowel this leaves behind)` and the two
    genuinely differ, which is worth the extra return value: a vav *is* the
    vowel and writes it, while a yod follows a hiriq that has already been
    written and so writes **nothing**. Collapsing them to one value — as an
    earlier version of this did, to satisfy a return-count lint — spells
    `bereshiit`.
    """
    if letter == VAV:
        if HOLAM in marks or HOLAM_HASER_FOR_VAV in marks:
            return "o", "o"  # holam male: the vav carries the vowel, not a v
        if DAGESH in marks and not vowel:
            return "u", "u"  # shuruq: the same trick, with the dot inside
    if letter == YOD and not vowel and previous_vowel == HIRIQ_VOWEL:
        return "", HIRIQ_VOWEL  # hiriq male: the i is already on the page
    return None

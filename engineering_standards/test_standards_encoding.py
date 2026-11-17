"""Cases for the mojibake rule.

The positives are BUILT rather than pasted: each takes real text, encodes it to UTF-8, and
decodes those bytes as the wrong codepage -- which is precisely the accident the rule exists to
catch. Pasting mojibake into a test file would make the fixture itself corrupt, and the rule
would then flag its own test.

The negatives matter more than the positives here. A rule that fires on correctly-encoded
Hebrew or Arabic would be worse than no rule at all in a repo that ships either.
"""

from __future__ import annotations

import sys
from pathlib import Path

from standards_core import CheckConfig
from standards_dispatch import check_source_file
from standards_encoding import check_mojibake
from standards_selftest import run_module_tests


def mangle(text: str, codepage: str = "latin-1") -> str:
    """Reproduce the accident: UTF-8 bytes read as a single-byte codepage.

    Defaults to latin-1 rather than cp1252, and the reason is worth knowing. Python's cp1252
    codec RAISES on the five byte values the codepage leaves undefined (0x81, 0x8D, 0x8F, 0x90,
    0x9D); Windows' own decoder passes them through as the matching C1 control characters. So
    Windows will happily mangle Hebrew -- aleph is D7 90 and hits 0x90 -- while Python refuses
    to simulate it. latin-1 maps all 256 byte values, which reproduces the Windows behaviour
    for exactly those bytes and lets the test cover the scripts that matter.
    """
    return text.encode("utf-8").decode(codepage)


def findings(line: str) -> list[str]:
    return [v.message for v in check_mojibake(Path("sample.cs"), [line])]


# ---- positives: every script, not just Latin ---------------------------------------------


def test_catches_danish() -> None:
    assert findings(f'Title = "{mangle("Kørselsregnskab")}";')


def test_catches_hebrew() -> None:
    # בראשית -- the first word of Genesis.
    assert findings(f'Word = "{mangle("בראשית")}";')


def test_catches_arabic() -> None:
    assert findings(f'Word = "{mangle("العربية")}";')


def test_catches_greek() -> None:
    assert findings(f'Word = "{mangle("λόγος")}";')


def test_catches_cyrillic() -> None:
    assert findings(f'Word = "{mangle("Здравствуйте")}";')


def test_catches_cjk() -> None:
    assert findings(f'Word = "{mangle("日本語")}";')


def test_catches_cp1252_variant() -> None:
    """The other codepage in play. Latin text avoids cp1252's undefined bytes, so it can be
    simulated directly -- and both mappings must be caught."""
    assert findings(f'Title = "{mangle("Kørselsregnskab", "cp1252")}";')


def test_catches_double_mangling() -> None:
    """DocRegistry.cs went through the accident twice."""
    assert findings(f'Title = "{mangle(mangle("Kørselsregnskab"))}";')


def test_message_names_the_original() -> None:
    """The message should say what the text was, so the fix is obvious.

    Only the mangled RUN is quoted, not the whole word: "Kørselsregnskab" is ASCII apart from
    the ø, so the run is that one character and the message names it.
    """
    assert "ø" in findings(f'Title = "{mangle("Kørselsregnskab")}";')[0]


def test_run_is_reported_once() -> None:
    """A wholly-mangled word is one finding, not one per character."""
    assert len(findings(f'Word = "{mangle("בראשית")}";')) == 1


# ---- negatives: correctly-encoded text in the same scripts --------------------------------


def test_quiet_on_real_hebrew() -> None:
    assert not findings('Word = "בראשית";')


def test_quiet_on_real_arabic() -> None:
    assert not findings('Word = "العربية";')


def test_quiet_on_real_greek_and_cyrillic() -> None:
    assert not findings('Word = "λόγος";')
    assert not findings('Word = "Здравствуйте";')


def test_quiet_on_real_cjk_and_emoji() -> None:
    assert not findings('Word = "日本語";')
    assert not findings('Status = "✅ 🎉";')


def test_quiet_on_danish() -> None:
    """Ø and Å are legitimate Danish letters AND lead characters."""
    for word in ("Ørsted", "Ærø", "Åhus", "Kørselsregnskab", "Udlæg", "Lønsedler"):
        assert not findings(f'Title = "{word}";'), word


def test_quiet_on_accented_latin_before_punctuation() -> None:
    """The false-positive class the continuation count exists to remove."""
    for text in ("café’s", "naïve—really", "Ça va", "¿Qué? ¡Sí!", "«guillemets»"):
        assert not findings(f'Text = "{text}";'), text


def test_quiet_on_plain_ascii() -> None:
    assert not findings("int total = subtotal + tax;")


# ---- exemption ----------------------------------------------------------------------------


def test_line_exemption_silences_it() -> None:
    """A doc demonstrating mojibake must be able to say so."""
    lines = [
        "// standards: mojibake exempt -- this line demonstrates the corruption the rule "
        "catches, so the mangled bytes are the point rather than a defect.",
        f'string broken = "{mangle("Kørselsregnskab")}";',
    ]
    assert not list(check_mojibake(Path("sample.cs"), lines))


# ---- the dispatcher: mojibake must survive every early return -----------------------------
#
# These call check_source_file, NOT check_mojibake, and that is the entire point. The rule
# worked perfectly in isolation while being unreachable for three file types, because
# check_source_file returns early for compose files, workflows and .env.example -- and the
# mojibake call was placed below those returns. A corrupted docker-compose.production.yml
# passed a green gate until it was found by eye.
#
# Testing the rule alone could never have caught that. Testing the entry point does.


def dispatched_rules(name: str, line: str) -> list[str]:
    """Rule ids `check_source_file` yields for this file -- the real entry point."""
    return [violation.rule for violation in check_source_file(Path(name), [line], CheckConfig(), {}, Path("."))]


def test_dispatcher_catches_mojibake_in_a_compose_file() -> None:
    mangled = mangle("# ingen port i produktion — NPM når containeren")
    assert "mojibake" in dispatched_rules("docker-compose.yml", mangled)


def test_dispatcher_catches_mojibake_in_a_compose_overlay() -> None:
    """The overlay is the file the real defect was found in."""
    mangled = mangle("# ingen port i produktion — NPM når containeren")
    assert "mojibake" in dispatched_rules("docker-compose.production.yml", mangled)


def test_dispatcher_catches_mojibake_in_a_workflow() -> None:
    mangled = mangle("      # bygger og deployer — kører kun på master")
    assert "mojibake" in dispatched_rules(".github/workflows/deploy.yml", mangled)


def test_dispatcher_catches_mojibake_in_env_example() -> None:
    mangled = mangle("# projektets navn — bruges som container-præfiks")
    assert "mojibake" in dispatched_rules(".env.example", mangled)


def test_dispatcher_still_catches_mojibake_in_source() -> None:
    """The hoist must not cost the case the rule was written for."""
    mangled = f'string title = "{mangle("Kørselsregnskab")}";'
    assert "mojibake" in dispatched_rules("Invoice.cs", mangled)


def test_dispatcher_quiet_on_clean_compose_file() -> None:
    """Correctly-encoded Danish in a compose comment is not a finding."""
    assert "mojibake" not in dispatched_rules("docker-compose.yml", "# ingen port i produktion — NPM når containeren")


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "mojibake cases"))

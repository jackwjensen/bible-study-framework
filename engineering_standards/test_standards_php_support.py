"""Cases for the PHP floor and php-consistency.

Two shapes carry the weight, and both exist in the estate today: `FROM php:8.3-fpm-alpine` in
three repos (a version whose active support ended 2025-12-31), and AutoTranslate's
`"php": "8.3.0"` in composer.json -- an EXACT pin, which Composer honours literally and the
estate policy forbids outright.

The Composer negatives matter as much: `^8.5` is a correct minimum and must stay silent, and
the operator semantics are NOT PEP 440's. `~8.5` locks the major where `~=3.14` leaves the
minor free, and getting that backwards would either flag every correct constraint or wave
through a locked one.

Run: python test_standards_php_support.py   (or pytest)
"""

from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import CheckConfig  # noqa: E402
from standards_dispatch import check_source_file  # noqa: E402
from standards_php_support import (  # noqa: E402
    PHP_ACTIVE_END,
    PHP_END_OF_LIFE,
    PHP_SECURITY_END,
    PHP_TOOLCHAIN,
    check_php_support,
    constraint_admits,
    declarations_in,
    is_published_package,
)
from standards_scope import should_check  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402
from standards_toolchain_consistency import check_consistency  # noqa: E402
from standards_versions import PHP_FLOOR, RUNTIME_BASELINE  # noqa: E402

FLOOR = f"{PHP_FLOOR[0]}.{PHP_FLOOR[1]}"
AHEAD = f"{PHP_FLOOR[0]}.{PHP_FLOOR[1] + 1}"
# One step BELOW the floor: the canonical 'must be flagged' fixture. Derived rather than
# hardcoded, because raising a floor is the whole point of having one -- and with the
# versions written out, every raise turned dozens of tests red for reasons that said
# nothing about the rule they were guarding.
BELOW_TUPLE = (PHP_FLOOR[0], PHP_FLOOR[1] - 1)
BELOW = f"{BELOW_TUPLE[0]}.{BELOW_TUPLE[1]}"


def found(name: str, *lines: str) -> list[str]:
    return [v.message for v in check_php_support(Path(name), list(lines))]


@contextmanager
def repo(**files: str) -> Iterator[tuple[Path, list[Path]]]:
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        for relative, text in files.items():
            path = root / relative.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        paths = [path for path in sorted(root.rglob("*")) if path.is_file() and should_check(path, root, CheckConfig())]
        yield root, paths


def agree(**files: str) -> list[str]:
    with repo(**files) as (root, paths):
        return [v.message for v in check_consistency(root, paths, CheckConfig(), PHP_TOOLCHAIN)]


# ---- the floor -------------------------------------------------------------------------------


def test_the_estate_wide_base_image_is_flagged() -> None:
    """agentsite, allegroit-dk and jacks_corner all declare this today."""
    assert found("Dockerfile", f"FROM php:{BELOW}-fpm-alpine")


def test_the_message_quotes_the_support_fact() -> None:
    message = found("Dockerfile", f"FROM php:{BELOW}-fpm-alpine")[0]
    assert PHP_ACTIVE_END[BELOW_TUPLE] in message
    assert PHP_SECURITY_END[BELOW_TUPLE] in message


def test_a_registry_prefixed_php_image_is_still_judged() -> None:
    assert found("Dockerfile", f"FROM ${{CACHE_REPOSITORY}}php:{BELOW}-fpm-alpine")


def test_a_version_hoisted_into_a_build_argument_is_still_judged() -> None:
    assert found("Dockerfile", f"ARG PHP={BELOW}", "FROM php:${PHP}-fpm-alpine")


def test_a_base_image_at_the_floor_is_clean() -> None:
    assert not found("Dockerfile", f"FROM php:{FLOOR}-fpm-alpine")


def test_a_base_image_ahead_of_the_floor_is_clean() -> None:
    assert not found("Dockerfile", f"FROM php:{AHEAD}-fpm-alpine")


def test_a_lookalike_image_is_not_judged() -> None:
    assert not found("Dockerfile", f"FROM myphp:{BELOW}")
    assert not found("Dockerfile", "FROM python:3.14-slim")


def test_a_ci_php_version_below_the_floor_is_flagged() -> None:
    assert found(".github/workflows/deploy.yml", f"          php-version: '{BELOW}'")


# ---- the exact pin ---------------------------------------------------------------------------


def test_an_exact_composer_pin_is_flagged_even_at_the_floor() -> None:
    """AutoTranslate's shape. Composer reads a bare `8.3.0` as `==`, unlike NuGet -- so this is
    a genuine pin, and it stays a finding even when the number itself is current, because the
    policy is about the SHAPE: we only ever define a minimum."""
    assert found("composer.json", f'    "php": "{BELOW}.0"')
    assert found("composer.json", f'    "php": "{FLOOR}.0"'), "a pin at the floor is still a pin"


def test_the_pin_message_offers_the_correct_form() -> None:
    message = found("composer.json", f'    "php": "{BELOW}.0"')[0]
    assert f"^{FLOOR}" in message and f">={FLOOR}" in message


def test_a_caret_minimum_at_the_floor_is_clean() -> None:
    assert not found("composer.json", f'    "php": "^{FLOOR}"')


def test_a_caret_minimum_below_the_floor_is_flagged() -> None:
    assert found("composer.json", f'    "php": "^{BELOW}"')


def test_another_packages_php_prefixed_name_is_not_the_platform_requirement() -> None:
    """`phpunit/phpunit` and `php-http/client` are packages, not the platform."""
    assert not found("composer.json", '    "phpunit/phpunit": "^11.0"')
    assert not found("composer.json", '    "php-http/client-common": "^2.0"')


# ---- vendored libraries are not this repo's declaration ---------------------------------------

VENDORED_PHPMAILER = [
    "{",
    '    "name": "phpmailer/phpmailer",',
    '    "type": "library",',
    '    "description": "PHPMailer is a full-featured email creation and transfer class",',
    '    "authors": [',
    '        { "name": "Marcus Bointon" }',
    "    ],",
    '    "require": {',
    '        "php": ">=5.0.0"',
    "    }",
    "}",
]


def test_a_vendored_package_manifest_is_not_read() -> None:
    """customizeid's `phpmail/composer.json` verbatim in shape: a copy of PHPMailer sitting
    OUTSIDE `vendor/`, so the pack's exclusion never reaches it. Read naively its `>=5.0.0`
    becomes "this repo supports PHP 5" -- a finding about code nobody here wrote."""
    assert not found("composer.json", *VENDORED_PHPMAILER)


def test_an_author_name_is_not_mistaken_for_a_package_slug() -> None:
    """The fingerprint needs a `vendor/package` slash, or every manifest with an authors block
    would be skipped as vendored -- silencing the rule on real applications."""
    assert not is_published_package(['    "name": "Marcus Bointon",', '    "type": "library"'])


def test_an_application_manifest_has_neither_key_and_is_read() -> None:
    """AutoTranslate's shape: no name, no type. That is what an app looks like."""
    assert not is_published_package(["{", f'    "require": {{"php": "^{BELOW}"}}', "}"])
    assert found("composer.json", "{", f'    "require": {{"php": "^{BELOW}"}}', "}")


def test_a_project_type_is_an_application_not_a_package() -> None:
    """`"type": "project"` is what a Composer application declares when it declares anything."""
    assert not is_published_package(['    "name": "allegro/site",', '    "type": "project"'])


# ---- config.platform: the version Composer RESOLVES against ----------------------------------


def test_a_platform_pin_is_read() -> None:
    """AutoTranslate declares its version ONLY here. A reader that looked at `require.php`
    alone would have called that repo clean."""
    assert found(
        "composer.json",
        '    "config": {',
        '        "platform": {',
        f'            "php": "{BELOW}.0"',
        "        }",
        "    }",
    )


def test_the_platform_finding_says_what_platform_actually_does() -> None:
    """It is not the runtime -- it tells Composer to resolve dependencies AS IF that version
    were running, so an 8.5 container gets the 8.3 dependency set and tests green against
    packages it never receives."""
    message = found(
        "composer.json",
        '    "config": {',
        '        "platform": {',
        f'            "php": "{BELOW}.0"',
        "        }",
        "    }",
    )[0]
    assert "resolves dependencies" in message


def test_a_require_php_after_a_closed_platform_block_is_labelled_correctly() -> None:
    """The platform state has to close, or every later declaration inherits the wrong label."""
    declarations = list(
        declarations_in(
            Path("composer.json"),
            [
                "{",
                f'    "config": {{ "platform": {{ "php": "{BELOW}.0" }} }},',
                '    "require": { "php": "^8.5" }',
                "}",
            ],
        )
    )
    labels = [d.label for d in declarations]
    assert labels == ["composer.json platform", "composer.json"], labels


# ---- Composer range semantics, which are NOT PEP 440's ---------------------------------------


def test_constraint_admits_reads_composer_operators() -> None:
    assert constraint_admits("^8.5", (8, 5))
    assert constraint_admits("^8.5", (8, 6)), "caret locks the MAJOR, not the minor"
    assert not constraint_admits("^8.5", (9, 0))
    assert not constraint_admits("^8.5", (8, 4))
    assert constraint_admits("~8.5", (8, 6)), "two-component ~ locks the major in Composer"
    assert not constraint_admits("~8.5.1", (8, 6)), "three-component ~ locks the minor"
    assert not constraint_admits(f"{BELOW}.*", (8, 5))
    assert constraint_admits(">=8.5", (8, 9))
    assert not constraint_admits(">=8.2 <8.5", (8, 5))
    assert not constraint_admits(f"{BELOW}.0", (8, 5)), "an exact pin admits only itself"
    assert constraint_admits("something odd", (8, 5)), "unparseable must never invent a no"


def test_the_tilde_semantics_differ_from_python_deliberately() -> None:
    """`~8.5` admits 8.6 in Composer; `~=3.14` admits 3.15 in PEP 440 for the same reason, but
    `~8.5.1` and `~=3.14.1` lock DIFFERENT levels. One shared parser would be wrong somewhere."""
    from standards_python_consistency import (  # noqa: PLC0415  (local by design: imported after this test builds its tree)
        constraint_admits as python_admits,  # noqa: PLC0415  (local by design: imported after this test builds its tree)
    )

    assert constraint_admits("~8.5", (8, 6))
    assert python_admits("~=3.14", (3, 15))
    assert not constraint_admits("~8.5.1", (8, 6))
    assert not python_admits("~=3.14.1", (3, 15))


# ---- consistency -----------------------------------------------------------------------------


def test_a_composer_ceiling_the_container_breaches_is_flagged() -> None:
    """`8.3.*` locks the minor, so an 8.5 container is a runtime the package says it does not
    support -- composer would refuse to install it there."""
    assert agree(
        **{"Dockerfile": f"FROM php:{FLOOR}-fpm-alpine\n", "composer.json": f'{{"require": {{"php": "{BELOW}.*"}}}}\n'}
    )
    assert agree(
        **{"Dockerfile": f"FROM php:{FLOOR}-fpm-alpine\n", "composer.json": f'{{"require": {{"php": "~{BELOW}.1"}}}}\n'}
    )


def test_a_caret_minimum_below_the_runtime_is_correct_and_silent_here() -> None:
    """`^8.3` genuinely DOES admit 8.5 -- it means >=8.3 <9.0. It is a floor finding (the
    declared minimum is out of active support) and NOT a disagreement, and conflating the two
    would report one edit as two problems."""
    assert not agree(
        **{"Dockerfile": f"FROM php:{FLOOR}-fpm-alpine\n", "composer.json": f'{{"require": {{"php": "^{BELOW}"}}}}\n'}
    )
    assert found("composer.json", f'    "php": "^{BELOW}"'), "the floor still has something to say"


def test_a_container_and_ci_disagreeing_is_flagged() -> None:
    assert agree(
        **{
            "Dockerfile": f"FROM php:{FLOOR}-fpm-alpine\n",
            ".github__workflows__ci.yml": f"          php-version: '{AHEAD}'\n",
        }
    )


def test_everything_agreeing_is_clean() -> None:
    assert not agree(
        **{
            "Dockerfile": f"FROM php:{FLOOR}-fpm-alpine\n",
            ".github__workflows__ci.yml": f"          php-version: '{FLOOR}'\n",
            "composer.json": f'{{"require": {{"php": "^{FLOOR}"}}}}\n',
        }
    )


def test_an_exact_pin_matching_the_runtime_is_a_floor_finding_only() -> None:
    """The pin is wrong in SHAPE, which php-support says. It is not a disagreement, so
    php-consistency stays quiet rather than reporting the same edit twice."""
    assert found("composer.json", f'    "php": "{FLOOR}.0"')
    assert not agree(
        **{"Dockerfile": f"FROM php:{FLOOR}-fpm-alpine\n", "composer.json": f'{{"require": {{"php": "{FLOOR}.0"}}}}\n'}
    )


# ---- wiring ----------------------------------------------------------------------------------


def test_composer_json_is_in_scope_and_the_dispatcher_reaches_the_rule() -> None:
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        composer = root / "composer.json"
        assert should_check(composer, root, CheckConfig()), "composer.json must be in scope"
        rules = [v.rule for v in check_source_file(composer, [f'    "php": "{BELOW}.0"'], CheckConfig(), {}, root)]
        assert "php-support" in rules


def test_the_reader_is_the_one_the_floor_uses() -> None:
    assert declarations_in is PHP_TOOLCHAIN.readers


def test_the_runtime_baseline_clears_the_floor() -> None:
    """THE PACK LEADS: a baseline below the floor would have the pack recording its own
    violation as the estate's normal."""
    major, minor = (int(part) for part in RUNTIME_BASELINE["php"].split(".")[:2])
    assert (major, minor) >= PHP_FLOOR


def test_every_php_version_the_pack_ships_clears_the_floor() -> None:
    """THE PACK LEADS, guarded by a grep rather than by the reader.

    `ci/check-php.yml` lints with `docker run php:N-cli`, which the reader deliberately does not
    parse -- `docker run` takes an arbitrary image and reading it would flag `docker run
    mysql:8` and every other service a job starts. So the pack's own templates get this instead:
    a direct search for any `php:N.M` the pack hands out, which is the actual risk the reader's
    limit leaves open here.
    """
    import re  # noqa: PLC0415  (local by design: imported after this test builds its tree)

    pack = Path(__file__).resolve().parent.parent
    tag = re.compile(r"(?<![\w.-])php:(\d+)\.(\d+)")
    for template in sorted((pack / "ci").glob("*.yml")):
        for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
            for match in tag.finditer(line):
                declared = (int(match.group(1)), int(match.group(2)))
                assert declared >= PHP_FLOOR, (
                    f"{template.name}:{number} ships PHP {declared[0]}.{declared[1]}, below the floor"
                )


def test_a_dead_branch_gets_its_end_of_life_date_not_a_label() -> None:
    """air2trust declares 7.3 -- twice. "End of life since 2021-12-06" is a fact; "not a release
    this estate supports" is a label somebody can argue with."""
    message = found("composer.json", '    "php": "7.3"')[0]
    assert PHP_END_OF_LIFE[(7, 3)] in message
    assert "END OF LIFE" in message


def test_the_floor_has_a_support_date_of_its_own() -> None:
    """The finding indexes PHP_ACTIVE_END[PHP_FLOOR] directly; lifting the floor to a version
    with no entry would raise KeyError inside the scanner rather than report anything."""
    assert PHP_FLOOR in PHP_ACTIVE_END and PHP_FLOOR in PHP_SECURITY_END


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "php-support cases"))

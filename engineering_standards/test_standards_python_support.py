"""Cases for the python-support floor.

The negatives carry the weight, as they do for every version rule: a floor that fires on an
action pin, on a non-Python base image, or on an upper bound becomes noise that gets exempted
wholesale, and an exempted rule is a rule that is not running.

The positives carry a second weight this rule does not share with node-support: several of them
are CIRCUMVENTIONS rather than mistakes -- a registry-prefixed base image, a version hoisted
into a build ARG, a tag naming only a major. Each of those is a way to declare a Python version
that an obvious implementation cannot see, and "the scan reported nothing" and "the scan looked
at nothing" are the same output. Those cases are marked below.

Run: python test_standards_python_support.py   (or pytest)
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
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402
from standards_python_support import (  # noqa: E402
    PYTHON_BUGFIX_END,
    PYTHON_END_OF_LIFE,
    check_python_support,
    metadata_declarations,
    support_note,
)
from standards_scope import should_check  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402
from standards_versions import CI_DEFAULTS, PYTHON_FLOOR, RUNTIME_BASELINE  # noqa: E402

WORKFLOW = Path(".github/workflows/deploy.yml")
FLOOR = f"{PYTHON_FLOOR[0]}.{PYTHON_FLOOR[1]}"
AHEAD = f"{PYTHON_FLOOR[0]}.{PYTHON_FLOOR[1] + 1}"
# One step BELOW the floor: the canonical 'must be flagged' fixture. Derived rather than
# hardcoded, because raising a floor is the whole point of having one -- and with the
# versions written out, every raise turned dozens of tests red for reasons that said
# nothing about the rule they were guarding.
BELOW_TUPLE = (PYTHON_FLOOR[0], PYTHON_FLOOR[1] - 1)
BELOW = f"{BELOW_TUPLE[0]}.{BELOW_TUPLE[1]}"
# ruff and black spell the same version without the dot: `py313`, not `py3.13`.
BELOW_COMPACT = f"py{BELOW_TUPLE[0]}{BELOW_TUPLE[1]}"
FLOOR_COMPACT = f"py{PYTHON_FLOOR[0]}{PYTHON_FLOOR[1]}"

EXEMPT_REASON = "ViennaRNA publishes no wheel for 3.14; revisit when upstream builds one"
assert len(EXEMPT_REASON) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


def found(name: str, *lines: str) -> list[str]:
    """Findings for a file called `name` holding `lines`."""
    return [v.message for v in check_python_support(Path(name), list(lines))]


@contextmanager
def temporary_repo() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tree:
        yield Path(tree)


# ---- CI ------------------------------------------------------------------------------------


def test_a_ci_version_below_the_floor_is_flagged() -> None:
    assert found(".github/workflows/deploy.yml", f"          python-version: '{BELOW}'")


def test_the_estate_wide_shape_is_flagged() -> None:
    """Every repo in the estate installed one minor below PYTHON_FLOOR in CI on 2026-08-25,
    and nothing reported it. Stated without interpolating BELOW: an f-string is not a
    docstring -- Python evaluates and discards it, so the explanation vanished at runtime."""
    assert found(".github/workflows/deploy.yml", f'          python-version: "{BELOW}"')
    assert found(".github/workflows/deploy.yml", f"          python-version: {BELOW}")


def test_a_matrix_flow_list_is_judged_on_its_oldest_entry() -> None:
    """`[3.11, 3.14]` ships 3.11. Judging it on the newest entry would call that clean."""
    assert found(".github/workflows/check.yml", "        python-version: [3.11, 3.14]")


def test_a_block_scalar_listing_versions_is_read() -> None:
    assert found(
        ".github/workflows/check.yml",
        "        python-version: |",
        f"          {BELOW}",
        "          3.14",
    )


def test_a_block_scalar_entirely_at_the_floor_is_clean() -> None:
    assert not found(
        ".github/workflows/check.yml",
        "        python-version: |",
        f"          {FLOOR}",
        f"          {AHEAD}",
    )


def test_a_ci_version_at_the_floor_is_clean() -> None:
    assert not found(".github/workflows/deploy.yml", f"          python-version: '{FLOOR}'")


def test_a_ci_version_ahead_of_the_floor_is_clean() -> None:
    """A floor is a MINIMUM. A new app started on the next minor must not be punished for it."""
    assert not found(".github/workflows/deploy.yml", f"          python-version: '{AHEAD}'")


def test_a_setup_python_action_pin_is_not_a_python_version() -> None:
    """setup-python@v6 is an ACTION major. Reading it as Python 6 -- or as 0.6 -- would put a
    finding on every workflow in the estate."""
    assert not found(".github/workflows/deploy.yml", "      - uses: actions/setup-python@v6")


def test_a_python_version_file_key_is_not_mistaken_for_a_version() -> None:
    """`python-version-file:` points at a .python-version, which this rule reads directly."""
    assert not found(".github/workflows/deploy.yml", "          python-version-file: .python-version")


def test_a_matrix_expression_is_skipped_rather_than_guessed_at() -> None:
    """A KNOWN LIMIT, documented rather than pretended away: the number lives in a matrix
    definition this line does not contain, and inventing a verdict would be noise."""
    assert not found(".github/workflows/check.yml", "          python-version: ${{ matrix.python-version }}")


# ---- Dockerfiles ---------------------------------------------------------------------------


def test_a_base_image_below_the_floor_is_flagged() -> None:
    assert found("Dockerfile", f"FROM python:{BELOW}-slim")


def test_a_registry_prefixed_base_image_is_still_judged() -> None:
    """CIRCUMVENTION, and a real one: this is allegro-it-services' backend verbatim. The first
    survey of the estate used `^FROM\\s+python:` and reported this repo as clean."""
    assert found(
        "Dockerfile",
        "FROM ${SB_PULL_THROUGH_CACHE_REPOSITORY}python:3.11-slim-bullseye AS backend_build",
    )


def test_a_fully_qualified_registry_path_is_still_judged() -> None:
    assert found("Dockerfile", f"FROM docker.io/library/python:{BELOW}-slim")


def test_a_platform_flag_does_not_hide_the_image() -> None:
    assert found("Dockerfile", f"FROM --platform=$BUILDPLATFORM python:{BELOW}-slim AS build")


def test_a_version_hoisted_into_a_build_argument_is_still_judged() -> None:
    """CIRCUMVENTION: the FROM line shows a checker nothing comparable, and the real number
    sits three lines up. The finding is reported on the ARG line, where the fix goes."""
    findings = found(
        "Dockerfile",
        "ARG PYTHON_VERSION=3.11",
        "FROM python:${PYTHON_VERSION}-slim",
    )
    assert findings
    line = [
        v.line
        for v in check_python_support(
            Path("Dockerfile"),
            ["ARG PYTHON_VERSION=3.11", "FROM python:${PYTHON_VERSION}-slim"],
        )
    ]
    assert line == [1], "the finding belongs on the ARG line, not the FROM"


def test_an_unobviously_named_build_argument_is_still_judged() -> None:
    """CIRCUMVENTION: matching only PYTHON-ish argument names would be defeated by picking a
    different word, so ARG names are resolved by use rather than by spelling."""
    assert found("Dockerfile", f"ARG PY={BELOW}", "FROM python:$PY-alpine")


def test_a_build_argument_no_from_uses_is_not_judged() -> None:
    """An ARG the image never interpolates cannot decide the interpreter."""
    assert not found("Dockerfile", "ARG SOME_TOOL=3.11", f"FROM python:{FLOOR}-slim")


def test_a_tag_naming_only_a_major_is_flagged() -> None:
    """CIRCUMVENTION: `python:3` floats onto whatever is newest at build time, so two builds of
    one commit can run different interpreters -- and nothing shows it clears the floor."""
    assert found("Dockerfile", "FROM python:3-slim")


def test_a_latest_tag_is_flagged() -> None:
    assert found("Dockerfile", "FROM python:latest")


def test_a_base_image_at_the_floor_is_clean() -> None:
    assert not found("Dockerfile", f"FROM python:{FLOOR}-slim")


def test_a_lookalike_image_name_is_not_judged() -> None:
    """`mypython:3.11` is somebody's own image, not the official one."""
    assert not found("Dockerfile", "FROM mypython:3.11")


def test_a_non_python_base_image_is_not_judged() -> None:
    assert not found("Dockerfile", "FROM node:24-alpine")
    assert not found("Dockerfile", "FROM php:8.3-fpm-alpine")


def test_an_unresolvable_expression_tag_is_skipped() -> None:
    """No ARG in the file defines it, so the value is decided outside this rule's sight."""
    assert not found("Dockerfile", "FROM python:${PYTHON_VERSION}")


# ---- pyproject.toml and setup.cfg ------------------------------------------------------------


def test_a_requires_python_below_the_floor_is_flagged() -> None:
    """allegro-it-services verbatim. `~=3.11.1` means `>=3.11.1, ==3.11.*` -- a lower bound
    below the floor and a ceiling that excludes it outright."""
    assert found("pyproject.toml", 'requires-python = "~=3.11.1"')


def test_a_permissive_lower_bound_is_still_a_lower_bound() -> None:
    """A `>=` bound one minor below PYTHON_FLOOR declares support for a Python that left
    bugfix support in April 2025. It is still a lower bound, and still too low."""
    assert found("pyproject.toml", f'requires-python = ">={BELOW}"')


def test_a_caret_constraint_is_read() -> None:
    assert found("pyproject.toml", f'requires-python = "^{BELOW}"')


def test_a_bare_version_is_read_as_a_bound() -> None:
    """PEP 440 reads a bare version as an `==` pin, so a bare minor below PYTHON_FLOOR
    pins the repo to it."""
    assert found("pyproject.toml", f'requires-python = "{BELOW}"')


def test_an_upper_bound_is_never_read_as_a_lower_one() -> None:
    """THE range trap. `>=3.14,<4` is a correct declaration; a checker that saw `<4` and called
    it "4" -- or saw any number below the floor -- would flag the very shape it recommends."""
    assert not found("pyproject.toml", f'requires-python = ">={FLOOR},<4"')


def test_a_requires_python_at_the_floor_is_clean() -> None:
    assert not found("pyproject.toml", f'requires-python = ">={FLOOR}"')


def test_a_ruff_target_below_the_floor_is_flagged() -> None:
    """The pack's own canonical block said py312 until 2026-08-25."""
    assert found("pyproject.toml", f'target-version = "{BELOW_COMPACT}"')


def test_the_black_spelling_is_read_too() -> None:
    """allegro-it-services writes `target-versions` (plural). Reading only the singular would
    miss a real declaration sitting in the estate today."""
    assert found("pyproject.toml", f'target-versions = "{BELOW_COMPACT}"')


def test_a_target_version_list_is_judged_on_its_oldest() -> None:
    assert found("pyproject.toml", f'target-version = ["{BELOW_COMPACT}", "{FLOOR_COMPACT}"]')


def test_a_two_digit_minor_is_not_read_as_two_numbers() -> None:
    """`py314` is 3.14, not 3.1.4. Read wrongly it becomes 3.1 and every repo fails."""
    assert not found("pyproject.toml", f'target-version = "py{PYTHON_FLOOR[0]}{PYTHON_FLOOR[1]}"')


def test_a_mypy_python_version_below_the_floor_is_flagged() -> None:
    assert found("pyproject.toml", 'python_version = "3.11"')


def test_a_setup_cfg_python_requires_is_flagged() -> None:
    assert found("setup.cfg", "python_requires = >=3.9")


# ---- .python-version -------------------------------------------------------------------------


def test_a_pin_file_below_the_floor_is_flagged() -> None:
    assert found(".python-version", f"{BELOW}")


def test_a_patch_level_pin_is_read_by_its_minor() -> None:
    assert found(".python-version", f"{BELOW}.14")


def test_a_pin_file_at_the_floor_is_clean() -> None:
    assert not found(".python-version", f"{FLOOR}.2")


def test_comments_and_blank_lines_in_a_pin_file_are_ignored() -> None:
    assert not found(".python-version", "# set by uv", "", f"{FLOOR}")


# ---- comparison, exemption, and the constants ------------------------------------------------


def test_the_floor_compares_by_number_and_not_by_text() -> None:
    """`3.9 > 3.14` is true for floats and true for strings. It is false for tuples, which is
    the only one of the three that is right -- and 3.9 must be flagged, not waved through."""
    assert found(".python-version", "3.9")
    assert not found(".python-version", AHEAD)


def test_a_line_exemption_silences_the_floor() -> None:
    assert not found(
        "Dockerfile",
        f"# standards: python-support exempt -- {EXEMPT_REASON}",
        f"FROM python:{BELOW}-slim",
    )


def test_the_message_names_every_declaration_to_move() -> None:
    """The scanner reports one line at a time and the fix is repo-wide: a repo that moves only
    its Dockerfile lints against one interpreter and runs on another."""
    message = found("Dockerfile", f"FROM python:{BELOW}-slim")[0]
    for site in ("`.python-version`", "python-version:", "FROM python:", "requires-python", "target-version"):
        assert site in message, f"the message must name {site}"


def test_the_message_quotes_the_support_fact() -> None:
    """A floor is a support date, not a taste. The finding has to say which date."""
    message = found("Dockerfile", f"FROM python:{BELOW}-slim")[0]
    assert PYTHON_BUGFIX_END[BELOW_TUPLE] in message
    assert PYTHON_END_OF_LIFE[BELOW_TUPLE] in message


def test_the_floor_has_a_bugfix_date_of_its_own() -> None:
    """The finding describes the floor from PYTHON_BUGFIX_END / PYTHON_END_OF_LIFE, so lifting
    the floor to a version with no entry would have it call the estate's own floor "not a
    release this estate supports". This test is the reason that cannot ship."""
    assert PYTHON_FLOOR in PYTHON_BUGFIX_END
    assert PYTHON_FLOOR in PYTHON_END_OF_LIFE


def test_the_floor_clause_is_the_lookup_not_a_sentence() -> None:
    """A sentence does not fail a test the way a lookup does, so this is the lookup.

    The message asserted "Python {floor} is the oldest release still receiving bugfixes
    (until {date})". Already false on 2026-09-05 -- 3.13 is older and in bugfix support until
    2026-10-01 -- and the B3D pack found the same sentence promising bugfixes "until
    2024-04-01" the day its floor moved to 3.11. Whatever the floor, its clause must be what
    the table says of it on the day the finding is read.
    """
    today = "2026-09-05"
    message = [v.message for v in check_python_support(Path("Dockerfile"), [f"FROM python:{BELOW}-slim"], today)][0]
    assert support_note(PYTHON_FLOOR, today) in message
    assert "oldest release still receiving" not in message


def test_the_support_note_uses_the_right_tense_for_the_day() -> None:
    """All three tenses, pinned with a fixed clock: before the bugfix end, between the two
    dates, and after end of life. The old sentence had one tense and used it for all three."""
    version = (3, 12)  # bugfix end 2025-04-02, end of life 2028-10-31
    assert "is in bugfix support until 2025-04-02" in support_note(version, "2025-01-01")
    assert "left bugfix support on 2025-04-02" in support_note(version, "2026-09-05")
    assert "reached end of life on 2028-10-31" in support_note(version, "2029-01-01")


def test_a_version_still_in_bugfix_support_is_never_said_to_have_left_it() -> None:
    """The offending version's clause has the same obligation as the floor's. Below the floor
    is a fact about the estate's choice; "left bugfix support" is a fact about the calendar,
    and the finding may only claim the second when it is true."""
    version = (3, 13)  # bugfix end 2026-10-01
    assert "left bugfix support" not in support_note(version, "2026-09-05")
    assert "left bugfix support" in support_note(version, "2026-10-02")


def test_the_floor_is_a_major_minor_pair() -> None:
    assert isinstance(PYTHON_FLOOR, tuple) and len(PYTHON_FLOOR) == 2
    assert all(isinstance(part, int) and part > 0 for part in PYTHON_FLOOR)


def test_the_ci_default_and_the_baseline_clear_the_floor() -> None:
    """THE PACK LEADS. /apply-standards writes CI_DEFAULTS into a repo that declares nothing,
    so a default below the floor would have the pack installing its own violation."""
    for tier in (CI_DEFAULTS, RUNTIME_BASELINE):
        major, minor = (int(part) for part in tier["python"].split(".")[:2])
        assert (major, minor) >= PYTHON_FLOOR, f"{tier['python']} is below the floor"


def test_every_artifact_the_pack_ships_clears_the_floor() -> None:
    """The same "pack leads" check, against the FILES rather than the constants.

    `ci/*.yml` and `python/ruff-standards.toml` are copied into repos verbatim, and neither is
    in the scanner's own scope -- ruff-standards.toml is not named pyproject.toml, and the CI
    templates are not under .github/workflows until they land in a repo. So nothing else would
    catch the pack handing out a 3.12 it fails everyone else for.

    Each artifact is read with the reader its CONTENT needs, not through
    `check_python_support`'s filename dispatch: dispatch would hand ruff-standards.toml to the
    CI reader, which finds no `python-version:` and returns clean no matter what the file says.
    Written that way first, this test passed against a deliberately planted py312 -- a test
    that cannot fail is worth less than no test, because it is also a claim.
    """
    pack = Path(__file__).resolve().parent.parent

    for workflow in (pack / "ci").glob("*.yml"):
        lines = workflow.read_text(encoding="utf-8").splitlines()
        assert not list(check_python_support(workflow, lines)), f"{workflow.name} installs a Python below the floor"

    canonical = pack / "python" / "ruff-standards.toml"
    if canonical.is_file():
        lines = canonical.read_text(encoding="utf-8").splitlines()
        targets = [v for d in metadata_declarations(lines) for v in d.versions]
        assert targets, "the canonical ruff block must declare a target-version at all"
        assert min(targets) >= PYTHON_FLOOR, f"ruff-standards.toml targets {min(targets)}, below the floor"


# ---- scope and dispatch ----------------------------------------------------------------------


def test_the_three_new_file_kinds_are_in_scope() -> None:
    """A rule that is correct but never reached is indistinguishable from a rule that passes."""
    with temporary_repo() as repo:
        for name in ("pyproject.toml", ".python-version", "setup.cfg"):
            assert should_check(repo / name, repo, CheckConfig()), f"{name} must be in scope"


def test_the_dispatcher_reaches_the_rule_for_every_source() -> None:
    cases = {
        "pyproject.toml": [f'requires-python = ">={BELOW}"'],
        ".python-version": [f"{BELOW}"],
        "setup.cfg": [f"python_requires = >={BELOW}"],
        "Dockerfile": [f"FROM python:{BELOW}-slim"],
        ".github/workflows/deploy.yml": [f"          python-version: '{BELOW}'"],
    }
    with temporary_repo() as repo:
        for relative, lines in cases.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            rules = [v.rule for v in check_source_file(path, lines, CheckConfig(), {}, repo)]
            assert "python-support" in rules, f"the rule never reached {relative}"


def test_a_toml_file_that_is_not_pyproject_is_not_judged() -> None:
    """Scope is by filename, never by suffix -- otherwise every tool config in the tree would
    be scanned for a version it has no opinion about."""
    with temporary_repo() as repo:
        assert not should_check(repo / "ruff-standards.toml", repo, CheckConfig())


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "python-support cases"))

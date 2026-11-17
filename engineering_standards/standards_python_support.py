#!/usr/bin/env python3
"""The Python support floor.

Rules here: python-support -- every Python version a repo declares is at or above PYTHON_FLOOR.

WHY THIS IS ITS OWN MODULE. `PYTHON_FLOOR` lives in standards_versions.py beside DOTNET_FLOOR
and NODE_FLOOR, because that file is the one place a human looks when bumping a version and a
second literal is a second place to forget. The rule lives here because Python declares its
version in FIVE places to Node's three, and folding that into standards_versions.py would push
it past the pack's own 500-line limit -- which the plan for this work explicitly said to solve
with a new module rather than by shaving comments. Same shape as standards_ef_provider.py:
constant upstream, rule and its evidence here.

WHAT IT READS, AND WHY MISSING ONE WOULD MAKE IT A RULE THAT PASSES BY ACCIDENT:

  .python-version           the repo's own pin -- Python's `.nvmrc`, read by pyenv and uv
  python-version: in CI     what the workflow installs
  FROM python:N.M           what the image is built on
  requires-python           what the package claims to support, and what pip enforces
  target-version / py_ver   what ruff, black and mypy are told the interpreter is

The last one is not decoration. A `target-version` AHEAD of the interpreter makes ruff accept
and suggest syntax that fails at runtime, and ruff cannot know which interpreter executes; a
`target-version` BEHIND it silently withholds every modern-syntax fix the pack's rules exist to
apply. allegro-it-services documents the first failure in its own pyproject.toml. Both are
version drift, so both are this rule's business.

THE HOLE THIS RULE WAS NEARLY SHIPPED WITH. The first sweep written to survey the estate used
`^FROM\\s+python:` and reported that no repo built on Python below 3.12. One did:
allegro-it-services' backend says `FROM ${SB_PULL_THROUGH_CACHE_REPOSITORY}python:3.11-slim-
bullseye`, and a pull-through-cache variable between FROM and the image name made it invisible.
A rule that finds nothing and a rule that looks at nothing are indistinguishable from outside,
so DOCKER_PYTHON_IMAGE below tolerates any registry, path or variable prefix -- and
test_a_registry_prefixed_base_image_is_still_judged is the test that stops that regressing.

Source of truth: engineering-standards/engineering_standards/standards_python_support.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import Violation, today_iso
from standards_declarations import Declared, Version
from standards_exemptions import line_exemption_reason
from standards_scope import (
    DOCKERFILE_FILENAME,
    PYPROJECT_FILENAME,
    PYTHON_VERSION_FILENAME,
    SETUP_CFG_FILENAME,
    looks_like_workflow,
)
from standards_versions import PYTHON_FLOOR

PYTHON_RULE = "python-support"

# The two dates every Python release has, read from https://devguide.python.org/versions/ and
# cross-checked against endoflife.date on 2026-08-25. HERE RATHER THAN IN standards_versions.py
# for the same reason EF_PROVIDER_SUNSET sits in standards_ef_provider.py: it is the rule's
# EVIDENCE, quoted in the finding, not a knob anybody turns when lifting the floor.
#
# The first column is what makes the floor 3.14. A release leaves BUGFIX support roughly two
# years in and then coasts for three more on security patches only -- during which it still
# "is supported" by the loosest reading, while every non-security defect found in it stays
# unfixed forever. That coasting period is what "out of maintenance" means here.
PYTHON_BUGFIX_END: dict[tuple[int, int], str] = {
    (3, 9): "2022-05-17",
    (3, 10): "2023-04-05",
    (3, 11): "2024-04-01",
    (3, 12): "2025-04-02",
    (3, 13): "2026-10-01",
    (3, 14): "2027-10-01",
}
PYTHON_END_OF_LIFE: dict[tuple[int, int], str] = {
    (3, 9): "2025-10-31",
    (3, 10): "2026-10-31",
    (3, 11): "2027-10-31",
    (3, 12): "2028-10-31",
    (3, 13): "2029-10-31",
    (3, 14): "2030-10-31",
}

# A GitHub Actions expression or a shell/Docker variable. Its value is decided somewhere this
# rule is not looking, so it is SKIPPED rather than guessed at -- see `declared_versions`.
# `python-version-file:` is not matched by PYTHON_VERSION_KEY at all (the `-file` sits between
# the key and the colon), which is correct: it points at a .python-version this rule reads
# directly.
EXPRESSION = re.compile(r"\$\{\{.*?\}\}|\$\{?\w+\}?")

# `3.14`, `3.14.2`, `'3.12'`, `3.12-slim`. The minor is required; a tag naming only a major is
# deliberately NOT matched here so it lands in the floating branch below.
VERSION_PAIR = re.compile(r"(?<![\w.])v?(\d+)\.(\d+)")

# ruff and black spell the interpreter `py314`; black takes a list. First digit is the major and
# everything after it is the minor, so `py39` is (3, 9) and `py314` is (3, 14) -- reading it as
# three separate digits would turn 3.14 into 3.1.
PY_TAG = re.compile(r"\bpy(\d)(\d+)\b")

# The lower bound of a PEP 440 / poetry constraint. `<` and `<=` are deliberately absent: an
# upper bound says nothing about the minimum the project accepts, and `>=3.14,<4` is a correct
# declaration this rule must stay quiet about.
LOWER_BOUND = re.compile(r"(?:>=|==|~=|\^|>)\s*v?(\d+)\.(\d+)")

# CI. The indent is captured so a block scalar's continuation lines can be found by depth.
PYTHON_VERSION_KEY = re.compile(r"^(\s*)python-version\s*:\s*(.*)$", re.IGNORECASE)
BLOCK_SCALAR = ("|", ">", "|-", ">-", "|+", ">+")

# `FROM python:3.14-slim`, `FROM docker.io/library/python:3.14`, `FROM --platform=$BUILDPLATFORM
# python:3.14` and `FROM ${CACHE_REPOSITORY}python:3.11-slim-bullseye` alike. The prefix must
# end in `/` or `}` so that `FROM mypython:3.12` is not mistaken for the official image.
DOCKER_PYTHON_IMAGE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(?:\S*[/}])?python:(\S+)", re.IGNORECASE)
# The other half of the Dockerfile story: a repo that hoists the version into a build argument.
# Deliberately matches ANY name rather than only PYTHON-ish ones -- `ARG PY=3.11` with
# `FROM python:${PY}` is the same image, and a checker that only understood the obvious naming
# would be defeated by choosing a different word. Names are resolved against the FROM tag that
# uses them (see `dockerfile_declarations`), so an ARG no FROM interpolates is never judged.
DOCKER_ARG = re.compile(r"^\s*(?:ARG|ENV)\s+([A-Za-z_]\w*)\s*[=\s]\s*(\S+)", re.IGNORECASE)

# pyproject.toml / setup.cfg keys. Three families, one meaning.
REQUIRES_PYTHON = re.compile(r"^\s*(requires-python|python_requires)\s*[=:]\s*(.+?)\s*$")
TOOL_TARGET = re.compile(r"^\s*(target-versions?)\s*=\s*(.+?)\s*$")
MYPY_PYTHON = re.compile(r"^\s*(python_version)\s*[=:]\s*(.+?)\s*$")


def declared_versions(text: str) -> list[Version] | None:
    """Every (major, minor) in `text`, or None when the text defers to something unreadable.

    None is not "clean" -- it is "this rule cannot see the value", which happens for
    `${{ matrix.python }}` and `${PYTHON_VERSION}`. Flagging those would be noise, because the
    real number lives in a matrix or an ARG that this line does not contain. The ARG itself is
    read separately (DOCKER_PYTHON_ARG), which is what keeps the Docker case honest; the CI
    matrix case is a KNOWN LIMIT, documented here rather than pretended away, and no repo in
    the estate uses one today.
    """
    if EXPRESSION.search(text):
        return None
    return [(int(found.group(1)), int(found.group(2))) for found in VERSION_PAIR.finditer(text)]


def py_tag_versions(text: str) -> list[Version] | None:
    """`py314` / `["py311", "py312"]` as (major, minor) pairs."""
    if EXPRESSION.search(text):
        return None
    return [(int(found.group(1)), int(found.group(2))) for found in PY_TAG.finditer(text)]


def constraint_floor(text: str) -> list[Version] | None:
    """The lowest version a `requires-python` constraint accepts.

    A constraint is a RANGE, which is exactly why node-support refuses to read
    `package.json` engines.node -- but Python's case is not Node's. `requires-python` is the
    single standard declaration every packaged project carries and the one pip enforces, so
    skipping it would mean skipping the only authoritative statement of what a project runs on.
    What makes it safe to read is that a floor only ever asks about the LOWER bound, which is
    well defined in every shape used here: `>=3.14`, `~=3.11.1`, `^3.12`, `==3.12.*`, `3.12`.

    `~=3.11.1` is worth naming: it means `>=3.11.1, ==3.11.*`, so it is both a lower bound
    below the floor AND a ceiling that excludes the floor outright. The lower bound catches it,
    so the ceiling needs no separate arithmetic.
    """
    if EXPRESSION.search(text):
        return None
    bounds = [(int(f.group(1)), int(f.group(2))) for f in LOWER_BOUND.finditer(text)]
    if bounds:
        # The minimum of several alternatives is the true floor of what the project accepts.
        return [min(bounds)]
    # No comparator at all. PEP 440 reads a bare `3.12` as `==3.12`, so it is still a bound.
    return declared_versions(text)


def ci_declarations(lines: list[str]) -> Iterable[Declared]:
    """Every `python-version:` in a workflow: inline, flow list, and block scalar.

    setup-python takes all three, and a matrix listing several is the shape a checker that
    understood only the inline form would mis-read. The OLDEST entry decides, for the same
    reason a multi-target .csproj is judged on its oldest TFM: the oldest is the one that has
    to keep working.
    """
    for index, line in enumerate(lines):
        found = PYTHON_VERSION_KEY.match(line)
        if not found:
            continue

        indent, value = found.group(1), found.group(2).strip()
        if not value or value in BLOCK_SCALAR:
            listed: list[str] = []
            for following in lines[index + 1 :]:
                if not following.strip():
                    continue
                if len(following) - len(following.lstrip()) <= len(indent):
                    break
                listed.append(following.strip().strip("-'\" "))
            value = " ".join(listed)

        # `or []` here would be a BUG, not a tidy-up: it collapses None ("the value is decided
        # somewhere this rule cannot see") into [] ("declared, but names nothing comparable"),
        # and those are opposite verdicts -- skip versus flag. Written that way first, it put a
        # floating-tag finding on every `python-version: ${{ matrix.python-version }}`.
        versions = declared_versions(value)
        if versions is not None:
            yield Declared(index, "CI installs Python", versions, "CI")


def build_arguments(lines: list[str]) -> dict[str, tuple[str, int]]:
    """Every `ARG`/`ENV` name in a Dockerfile, with its default value and the line it is on."""
    declared: dict[str, tuple[str, int]] = {}
    for index, line in enumerate(lines):
        found = DOCKER_ARG.match(line)
        if found:
            declared[found.group(1)] = (found.group(2).strip("'\""), index)
    return declared


def dockerfile_declarations(lines: list[str]) -> Iterable[Declared]:
    """`FROM …python:<tag>`, with `${ARG}` in the tag resolved against the file's own ARG/ENV.

    THE SUBSTITUTION IS THE POINT, not a nicety. `ARG PYTHON_VERSION=3.11` followed by
    `FROM python:${PYTHON_VERSION}` is a complete, working way to build on 3.11 while showing a
    floor-checker nothing it can compare -- and a rule that skipped it would be one grep away
    from being routed around permanently. The finding is reported on the ARG line, because that
    is where the number a human has to change actually lives.
    """
    arguments = build_arguments(lines)
    for index, line in enumerate(lines):
        image = DOCKER_PYTHON_IMAGE.match(line)
        if not image:
            continue

        tag, at = image.group(1), index
        for name, (value, declared_at) in arguments.items():
            for spelling in (f"${{{name}}}", f"${name}"):
                if spelling in tag:
                    tag, at = tag.replace(spelling, value), declared_at

        found = declared_versions(tag)
        if found is not None:
            yield Declared(at, "this image is built on Python", found, "Dockerfile")


def metadata_declarations(lines: list[str]) -> Iterable[Declared]:
    """`requires-python`, ruff/black `target-version`, and mypy `python_version`."""
    for index, line in enumerate(lines):
        requires = REQUIRES_PYTHON.match(line)
        if requires:
            found = constraint_floor(requires.group(2))
            if found is not None:
                yield Declared(
                    index,
                    "this package declares support down to Python",
                    found,
                    "requires-python",
                    requires.group(2),
                )
            continue

        target = TOOL_TARGET.match(line)
        if target:
            found = py_tag_versions(target.group(2))
            if found:
                yield Declared(index, "the lint/format target is Python", found, "lint target")
            continue

        mypy = MYPY_PYTHON.match(line)
        if mypy:
            found = declared_versions(mypy.group(2))
            if found:
                yield Declared(index, "the type checker is told Python", found, "type checker")


def pin_file_declarations(lines: list[str]) -> Iterable[Declared]:
    """`.python-version` -- one version per line, `#` comments allowed."""
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        found = declared_versions(stripped)
        if found is not None:
            yield Declared(index, "`.python-version` pins Python", found, ".python-version")


def declarations_in(path: Path, lines: list[str]) -> Iterable[Declared]:
    """Every Python version this file declares, using the reader its filename calls for.

    SHARED BY BOTH RULES ON PURPOSE. `python-support` asks whether each declaration clears the
    floor; `python-consistency` asks whether they agree with each other. Two readers would let
    those questions disagree about what a file even says -- one rule seeing a `FROM` the other
    is blind to -- and the second rule would then quietly pass a repo the first one failed.
    """
    if PYTHON_VERSION_FILENAME.match(path.name):
        return pin_file_declarations(lines)
    if DOCKERFILE_FILENAME.match(path.name):
        return dockerfile_declarations(lines)
    if PYPROJECT_FILENAME.match(path.name) or SETUP_CFG_FILENAME.match(path.name):
        return metadata_declarations(lines)
    # NOT a fall-through: a reader that assumed every other file was a workflow
    # read test fixtures and documentation samples as real declarations once the
    # consistency engine began walking every in-scope path. See looks_like_workflow.
    if looks_like_workflow(path):
        return ci_declarations(lines)
    return ()


def support_note(version: Version, today: str) -> str:
    """Where this version stands in its support life, in one clause, as of `today`.

    DATE-AWARE, and that is a correction rather than a style. The first version always said
    "left bugfix support on {date}", which was true of every release below a 3.14 floor on the
    day it was written and false of 3.13 until 2026-10-01 -- and would have been false of the
    floor ITSELF the moment the same sentence was reused to describe it. A sentence does not
    fail a test the way a lookup does, so the tense now comes from the table and the clock,
    and a test can pin all three tenses with a fixed `today`.
    """
    bugfix = PYTHON_BUGFIX_END.get(version)
    end = PYTHON_END_OF_LIFE.get(version)
    spelled = f"{version[0]}.{version[1]}"
    if bugfix and end:
        if today < bugfix:
            return f"Python {spelled} is in bugfix support until {bugfix} (security patches until {end})"
        if today < end:
            return (
                f"Python {spelled} left bugfix support on {bugfix} and gets security patches only "
                f"until {end} -- every non-security defect found in it now stays unfixed forever"
            )
        return f"Python {spelled} reached end of life on {end} and receives no patches at all"
    if end:
        return f"Python {spelled} is out of support (ended {end})"
    return f"Python {spelled} is not a release this estate supports"


def check_python_support(path: Path, lines: list[str], today: str | None = None) -> Iterable[Violation]:
    """Flag a Python version below the estate floor, wherever the repo declares one.

    Five sources, one floor. Which source a line came from changes only the wording; the
    comparison is the same tuple comparison every time, because a floor that meant something
    different in CI than it meant in a Dockerfile would not be a floor.

    Line-exemptable like every other floor. A repo genuinely held below the floor by a
    dependency with no build for it says so in writing, on the line, and the scanner prints
    the reason.

    THE FLOOR CLAUSE IS LOOKED UP, NOT ASSERTED. The message read "Python {floor} is the
    oldest release still receiving bugfixes (until {date})" -- a sentence somebody wrote once,
    already false on 2026-09-05 (3.13 is older and in bugfix support until 2026-10-01), and
    self-contradicting the day the floor moves. The B3D pack hit the same defect with its
    floor corrected to 3.11 and the sentence still promising bugfixes "until 2024-04-01";
    both packs now derive the clause from the same table the finding quotes for the offending
    version. `today` is injectable so a test can pin the tense; the dispatcher passes nothing
    and gets the clock.
    """
    today = today or today_iso()
    floor = f"{PYTHON_FLOOR[0]}.{PYTHON_FLOOR[1]}"
    # The floor's OWN standing, from the same table. Where a floor sits below active support
    # -- as B3D's deliberately does -- saying so is the honest reading and the argument for a
    # scheduled raise, not something to hide behind a friendlier sentence.
    floor_standing = support_note(PYTHON_FLOOR, today)
    for declaration in declarations_in(path, lines):
        # The oldest entry decides: a declaration listing several versions is shipping all of
        # them, and judging it on its newest would call `[3.11, 3.14]` clean.
        oldest = min(declaration.versions) if declaration.versions else None
        if oldest is not None and oldest >= PYTHON_FLOOR:
            continue
        if line_exemption_reason(lines, declaration.index, PYTHON_RULE):
            continue

        if oldest is None:
            problem = (
                f"{declaration.source} a version this rule cannot compare -- a tag naming only "
                f"a major (`python:3`), `latest`, or a wildcard floats onto whatever is newest "
                f"at build time, so two builds of one commit can run different interpreters "
                f"and nothing can show this clears the floor of {floor}"
            )
        else:
            problem = (
                f"{declaration.source} "
                f"{oldest[0]}.{oldest[1]}, below the estate floor of {floor}. "
                f"{support_note(oldest, today)}"
            )

        yield Violation(
            path=path,
            line=declaration.index + 1,
            rule=PYTHON_RULE,
            message=(
                f"{problem}. Python {floor} is this estate's floor -- {floor_standing}. "
                f"MOVE EVERY DECLARATION IN THE "
                f"REPO TOGETHER -- `.python-version`, every `python-version:` in CI, any "
                f"`FROM python:` base image, `requires-python`, and the ruff/black "
                f"`target-version` -- because they are read by different tools and a repo that "
                f"moves only some of them lints against one interpreter and runs on another, "
                f"which is the failure this floor exists to make impossible. If a dependency "
                f"genuinely pins this repo below {floor}, exempt the line with a written "
                f"reason naming the blocker and what would unblock it."
            ),
        )

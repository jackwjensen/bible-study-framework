#!/usr/bin/env python3
"""The PHP floor, the Composer range reader, and the readers php-consistency judges PHP with.

Rules here: php-support -- every PHP version a repo declares is at or above PHP_FLOOR, and no
Composer constraint is an exact pin. Paired with php-consistency, produced from these same
readers by standards_toolchain_consistency.py.

WHY THE FLOOR IS 8.5 AND NOT 8.4. PHP's "active support" is the analogue of Python's bugfix
phase: two years of real fixes, then two more of security-only patches. Read from php.net on
2026-08-25 (the dates are in PHP_ACTIVE_END below, not from memory):

    8.3  active support ENDED 2025-12-31   -- what all four PHP repos declare today
    8.4  active support ends  2026-12-31   -- four months away
    8.5  active support ends  2027-12-31

8.4 would satisfy the letter of "still in active support" and force a second estate-wide
migration before Christmas, which contradicts "the floor is lifted deliberately, when the
estate is ready". 8.5 is the same choice PYTHON_FLOOR made -- the current stable, with over a
year of maintenance ahead of it -- and is the one that does not need redoing immediately.

THE EXACT-PIN FINDING, and why it lives in the floor rule. AutoTranslate declares
`"php": "8.3.0"` in composer.json. Composer reads a bare `8.3.0` as EXACTLY that -- unlike
NuGet, where `Version="8.3.0"` is a minimum -- so it genuinely is a pin, and pins are what the
estate policy forbids: "do not pin anything, that is how we get legacy packs in new apps. We
only define minimum." It is reported here rather than as its own rule because it is the same
subject (what PHP version this repo declares) and the same one-line fix.

WHY COMPOSER RANGES ARE READ AT ALL. The plan for this work warned to start with `FROM php:N.M`
and treat `composer.json` as a separate decision "needing real range parsing. Do not guess."
That machinery now exists -- Python's `requires-python` needed it first -- so the constraint is
parsed rather than guessed at, and rather than skipped. What is NOT shared is the parser:
Composer and PEP 440 spell overlapping operators with different meanings. `~8.3` is `>=8.3 <9.0`
and `~8.3.1` is `>=8.3.1 <8.4.0` -- the component COUNT decides which level is locked, and it
decides it differently from PEP 440's `~=`. One clever shared function would be right for one
ecosystem and quietly wrong for the other.

A KNOWN LIMIT, documented rather than pretended away: a version named in a `docker run
php:8.5-cli` inside a CI script is NOT read. It is a real environment -- the pack's own
`ci/check-php.yml` lints with one, and a syntax check running on a different minor from the
container is exactly the drift these rules exist for -- but `docker run` takes an arbitrary
image and parsing it would flag `docker run mysql:8` and every other service a job starts.
The pack's own templates are guarded by a test instead (see test_standards_php_support.py);
a repo that adopts the pattern wants a reader with real argument parsing, not a guess.

Source of truth: engineering-standards/engineering_standards/standards_php_support.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import Violation
from standards_declarations import Declared, Toolchain, Version
from standards_exemptions import line_exemption_reason
from standards_scope import COMPOSER_FILENAME, DOCKERFILE_FILENAME, looks_like_workflow
from standards_versions import PHP_FLOOR

PHP_RULE = "php-support"
PHP_CONSISTENCY_RULE = "php-consistency"

# php.net/supported-versions.php, read 2026-08-25. Active support is bugfixes; after it a branch
# gets two years of security-only patches, during which every non-security defect found in it
# stays unfixed. That second phase is what "out of maintenance" means here.
PHP_ACTIVE_END: dict[tuple[int, int], str] = {
    (8, 1): "2023-11-25",
    (8, 2): "2024-12-31",
    (8, 3): "2025-12-31",
    (8, 4): "2026-12-31",
    (8, 5): "2027-12-31",
}
PHP_SECURITY_END: dict[tuple[int, int], str] = {
    (8, 1): "2025-12-31",
    (8, 2): "2026-12-31",
    (8, 3): "2027-12-31",
    (8, 4): "2028-12-31",
    (8, 5): "2029-12-31",
}

# The dead branches, from php.net/eol.php the same day. Carried so a finding on a genuinely
# ancient declaration says WHEN it died rather than falling back to "not a release this estate
# supports" -- air2trust declares 7.3 twice, and "end of life since 2021-12-06" is an argument
# where the generic wording is only a label.
PHP_END_OF_LIFE: dict[tuple[int, int], str] = {
    (5, 6): "2018-12-31",
    (7, 0): "2019-01-10",
    (7, 1): "2019-12-01",
    (7, 2): "2020-11-30",
    (7, 3): "2021-12-06",
    (7, 4): "2022-11-28",
    (8, 0): "2023-11-26",
    (8, 1): "2025-12-31",
}

EXPRESSION = re.compile(r"\$\{\{.*?\}\}|\$\{?\w+\}?")
VERSION_PAIR = re.compile(r"(?<![\w.])v?(\d+)\.(\d+)")

# `FROM php:8.5-fpm-alpine`, and the same registry/path/variable tolerance the Python and Node
# readers have -- an anchored `FROM php:` is one pull-through cache away from seeing nothing.
DOCKER_PHP_IMAGE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(?:\S*[/}])?php:(\S+)", re.IGNORECASE)
DOCKER_ARG = re.compile(r"^\s*(?:ARG|ENV)\s+([A-Za-z_]\w*)\s*[=\s]\s*(\S+)", re.IGNORECASE)

# composer.json: `"php": "^8.5"` inside require. Matched on the key so `"php-http/client"` and
# a `"phpunit/phpunit"` dev dependency are not mistaken for the platform requirement -- the
# quotes have to close immediately after `php`, which no package name does.
#
# NOT anchored to the start of the line. It was, and a compact `{"require": {"php": "8.3.*"}}`
# -- one line, entirely legal JSON, and what `composer config` writes with --no-interaction --
# was invisible to it. A reader that only understands pretty-printed JSON is a reader that
# reports a repo clean for the way it happens to format a file.
COMPOSER_PHP = re.compile(r'(?<!\w)"php"\s*:\s*"([^"]+)"')

# A package manifest's fingerprint: a `vendor/package` slug AND a distributable type. The slug
# needs the slash, which is what keeps an author's `"name": "Marcus Bointon"` out of it. An
# application manifest has neither key -- see `is_published_package`.
COMPOSER_PACKAGE_NAME = re.compile(r'(?<!\w)"name"\s*:\s*"[\w.-]+/[\w.-]+"')
COMPOSER_LIBRARY_TYPE = re.compile(r'(?<!\w)"type"\s*:\s*"(?:library|metapackage|composer-plugin|php-ext[\w-]*)"')

# `config.platform.php` -- a version Composer RESOLVES against, whatever actually runs.
COMPOSER_PLATFORM_KEY = re.compile(r'(?<!\w)"platform"\s*:')

# shivammathur/setup-php.
PHP_VERSION_KEY = re.compile(r"^(\s*)php-version\s*:\s*(.*)$", re.IGNORECASE)
BLOCK_SCALAR = ("|", ">", "|-", ">-", "|+", ">+")

# Composer's own operators. `^` and two-component `~` lock the MAJOR; three-component `~` locks
# the minor; `.*` locks whatever level it replaces.
CARET = re.compile(r"\^\s*(\d+)\.(\d+)")
TILDE_MAJOR_LOCK = re.compile(r"~\s*(\d+)\.(\d+)(?!\.\d)")
TILDE_MINOR_LOCK = re.compile(r"~\s*(\d+)\.(\d+)\.\d+")
STAR_MINOR_LOCK = re.compile(r"(?<![\w.])(\d+)\.(\d+)\.\*")
GREATER_EQUAL = re.compile(r">=\s*(\d+)\.(\d+)")
LESS_THAN = re.compile(r"<(?!=)\s*(\d+)\.(\d+)")

# An EXACT pin: digits and dots only, no operator, no wildcard, at least three components.
# `8.3` alone is also exact in Composer, but two components read as a minor-level constraint in
# practice and the estate has no instance; requiring three keeps the finding to the real shape.
EXACT_PIN = re.compile(r"^\s*v?\d+\.\d+\.\d+\s*$")


def spell(version: Version) -> str:
    return f"{version[0]}.{version[1]}"


def declared_versions(text: str) -> list[Version] | None:
    """Every (major, minor) in `text`, or None when it defers to something unreadable."""
    if EXPRESSION.search(text):
        return None
    return [(int(f.group(1)), int(f.group(2))) for f in VERSION_PAIR.finditer(text)]


def constraint_floor(text: str) -> list[Version] | None:
    """The lowest PHP version a Composer constraint accepts."""
    if EXPRESSION.search(text):
        return None
    bounds = [
        (int(f.group(1)), int(f.group(2)))
        for pattern in (CARET, TILDE_MAJOR_LOCK, TILDE_MINOR_LOCK, STAR_MINOR_LOCK, GREATER_EQUAL)
        for f in pattern.finditer(text)
    ]
    return [min(bounds)] if bounds else declared_versions(text)


def constraint_admits(constraint: str, version: Version) -> bool:
    """Whether a Composer constraint accepts `version`.

    Unparseable constraints return True, for the same reason the Python one does: a rule that
    guessed would fail repos for shapes it merely does not understand.
    """
    if EXACT_PIN.match(constraint):
        pinned = declared_versions(constraint) or []
        return bool(pinned) and version == pinned[0]

    lower = [
        (int(f.group(1)), int(f.group(2)))
        for pattern in (CARET, TILDE_MAJOR_LOCK, TILDE_MINOR_LOCK, STAR_MINOR_LOCK, GREATER_EQUAL)
        for f in pattern.finditer(constraint)
    ]
    if lower and version < min(lower):
        return False

    # `^8.3` and `~8.3` both mean "this major only".
    for pattern in (CARET, TILDE_MAJOR_LOCK):
        locked = pattern.search(constraint)
        if locked and version[0] != int(locked.group(1)):
            return False

    for pattern in (TILDE_MINOR_LOCK, STAR_MINOR_LOCK):
        locked = pattern.search(constraint)
        if locked and version != (int(locked.group(1)), int(locked.group(2))):
            return False

    ceiling = LESS_THAN.search(constraint)
    return not (ceiling and version >= (int(ceiling.group(1)), int(ceiling.group(2))))


def dockerfile_declarations(lines: list[str]) -> Iterable[Declared]:
    """`FROM …php:<tag>`, with `${ARG}` in the tag resolved against the file's own ARG/ENV."""
    arguments: dict[str, tuple[str, int]] = {}
    for index, line in enumerate(lines):
        found = DOCKER_ARG.match(line)
        if found:
            arguments[found.group(1)] = (found.group(2).strip("'\""), index)

    for index, line in enumerate(lines):
        image = DOCKER_PHP_IMAGE.match(line)
        if not image:
            continue
        tag, at = image.group(1), index
        for name, (value, declared_at) in arguments.items():
            for spelling in (f"${{{name}}}", f"${name}"):
                if spelling in tag:
                    tag, at = tag.replace(spelling, value), declared_at
        found = declared_versions(tag)
        if found is not None:
            yield Declared(at, "this image is built on PHP", found, "Dockerfile")


def ci_declarations(lines: list[str]) -> Iterable[Declared]:
    """Every `php-version:` in a workflow: inline, flow list, and block scalar."""
    for index, line in enumerate(lines):
        found = PHP_VERSION_KEY.match(line)
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
        versions = declared_versions(value)
        if versions is not None:
            yield Declared(index, "CI installs PHP", versions, "CI")


def is_published_package(lines: list[str]) -> bool:
    """Whether this composer.json is a PACKAGE's own manifest rather than an application's.

    THE FALSE POSITIVE THIS PREVENTS was found by scanning the estate on 2026-08-25:
    `customizeid/bestilling.studietoej.dk/phpmail/composer.json` is a vendored copy of
    PHPMailer -- `"name": "phpmailer/phpmailer"`, `"type": "library"` -- sitting in `phpmail/`
    rather than `vendor/`, so the pack's `/vendor/` exclusion does not reach it. Read naively,
    its `"php": ">=5.0.0"` becomes "this repo supports PHP 5", which is a finding about code
    nobody here wrote and nobody here can change.

    The discriminator is what an APPLICATION manifest looks like: AutoTranslate's has no
    `name` and no `type` at all, which is typical -- an app has nothing to publish. A package
    declares both, and a package that is `library`/`metapackage`/`composer-plugin` is by
    definition somebody's dependency.

    THE LIMIT, stated rather than hidden: a repo that genuinely IS a published PHP library
    would be skipped here too. None in this estate is, and such a repo is still covered by its
    Dockerfile and CI readers. If one appears and needs its manifest judged, that wants a
    reader that knows the repo's own package name -- not a looser guess than this one.
    """
    text = "\n".join(lines)
    return bool(COMPOSER_PACKAGE_NAME.search(text) and COMPOSER_LIBRARY_TYPE.search(text))


def composer_declarations(lines: list[str]) -> Iterable[Declared]:
    """The PHP version composer.json declares, from either of the two places it can sit.

    `require.php` is the platform requirement. `config.platform.php` is the sneakier one and is
    read too: it tells Composer to resolve dependencies AS IF that version were running, so a
    repo on an 8.5 container with `platform: 8.3` installs the dependency set for 8.3 and tests
    green against packages its runtime never gets. That is this rule's own subject -- an
    environment disagreeing with the others -- expressed in the dependency graph rather than in
    a Dockerfile. AutoTranslate declares its version ONLY there, so a reader that skipped it
    would have called that repo clean.
    """
    if is_published_package(lines):
        return

    inside_platform = False
    for index, line in enumerate(lines):
        if COMPOSER_PLATFORM_KEY.search(line):
            inside_platform = True
        elif inside_platform and "}" in line:
            inside_platform = False

        found = COMPOSER_PHP.search(line)
        if not found:
            continue
        raw = found.group(1)
        versions = constraint_floor(raw)
        if versions is None:
            continue
        source = "composer.json resolves dependencies as PHP" if inside_platform else "composer.json requires PHP"
        label = "composer.json platform" if inside_platform else "composer.json"
        yield Declared(index, source, versions, label, raw)


def declarations_in(path: Path, lines: list[str]) -> Iterable[Declared]:
    """Every PHP version this file declares, using the reader its filename calls for."""
    if DOCKERFILE_FILENAME.match(path.name):
        return dockerfile_declarations(lines)
    if COMPOSER_FILENAME.match(path.name):
        return composer_declarations(lines)
    # NOT a fall-through: a reader that assumed every other file was a workflow
    # read test fixtures and documentation samples as real declarations once the
    # consistency engine began walking every in-scope path. See looks_like_workflow.
    if looks_like_workflow(path):
        return ci_declarations(lines)
    return ()


def support_note(version: Version) -> str:
    """What is actually wrong with shipping this version, in one clause.

    Three cases, because they are three different arguments. A branch still in active support
    needs no note at all (it is above the floor). One in security-only gets the date it stopped
    being fixed. A dead one gets the date it stopped getting anything -- and that is the wording
    that carries, because "end of life five years ago" is a fact, where "not supported" is a
    label somebody can argue with.
    """
    spelled = spell(version)
    dead = PHP_END_OF_LIFE.get(version)
    active = PHP_ACTIVE_END.get(version)
    security = PHP_SECURITY_END.get(version)

    if dead:
        return (
            f"PHP {spelled} reached END OF LIFE on {dead} -- it receives nothing, not even "
            f"security patches, and every vulnerability found in it since is unpatched"
        )
    if active and security:
        return (
            f"PHP {spelled} left active support on {active} and gets security patches only "
            f"until {security} -- every non-security defect found in it now stays unfixed"
        )
    return f"PHP {spelled} is not a release this estate supports"


def check_php_support(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a PHP version below the estate floor, or a Composer constraint that is a pin."""
    floor = spell(PHP_FLOOR)
    for declaration in declarations_in(path, lines):
        exact = declaration.constraint is not None and EXACT_PIN.match(declaration.constraint)
        oldest = min(declaration.versions) if declaration.versions else None
        if not exact and oldest is not None and oldest >= PHP_FLOOR:
            continue
        if line_exemption_reason(lines, declaration.index, PHP_RULE):
            continue

        if exact:
            # `config.platform` is the worse of the two and has to say so. It does not merely
            # state a requirement -- it tells Composer to RESOLVE every dependency as if that
            # version were running, so an 8.5 container installs the 8.3 dependency set and the
            # suite passes against packages the runtime never receives. AutoTranslate declares
            # its version there and nowhere else.
            where = (
                "composer.json resolves dependencies as PHP "
                f"{declaration.constraint} exactly, via `config.platform`. That is not just a "
                f"requirement: Composer picks every dependency version as if that release were "
                f"running, so a newer container still installs the older package set and the "
                f"tests pass against something production never gets"
                if declaration.label.endswith("platform")
                else f"composer.json pins PHP to exactly {declaration.constraint}"
            )
            problem = (
                f"{where}. Composer reads a bare version as `==`, unlike NuGet where it is a "
                f"minimum, so this genuinely is a pin -- and the estate policy is that we only "
                f"ever define a MINIMUM, because pinning is how a new app inherits an old "
                f"platform. Write `^{floor}` or `>={floor}`"
            )
        elif oldest is None:
            problem = (
                f"{declaration.source} a version this rule cannot compare -- a tag naming only "
                f"a major, or `latest`, floats onto whatever is newest at build time, so "
                f"nothing shows this clears the floor of {floor}"
            )
        else:
            problem = f"{declaration.source} {spell(oldest)}, below the estate floor of {floor}. {support_note(oldest)}"

        yield Violation(
            path=path,
            line=declaration.index + 1,
            rule=PHP_RULE,
            message=(
                f"{problem}. PHP {floor} is the oldest release still in active support (until "
                f"{PHP_ACTIVE_END[PHP_FLOOR]}). MOVE EVERY DECLARATION IN THE REPO TOGETHER -- "
                f"any `FROM php:` base image, every `php-version:` in CI, and composer.json's "
                f"`require.php` -- and note that agreement between them is enforced separately "
                f"by `php-consistency`, because a floor only makes being BELOW it impossible. "
                f"If an extension genuinely pins this repo below {floor}, exempt the line with "
                f"a written reason naming the blocker and what would unblock it."
            ),
        )


PHP_TOOLCHAIN = Toolchain(
    name="PHP",
    rule=PHP_CONSISTENCY_RULE,
    noun="runtime",
    readers=declarations_in,
    spell=spell,
    admits=constraint_admits,
)

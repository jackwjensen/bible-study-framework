#!/usr/bin/env python3
"""Values that are likely to change, and the three places one can live.

Added 2026-09-07 after a constants audit on `payvisia` found 191 `const` declarations where
its author normally carries fewer than ten. Jack's statement of the rule:

    Anything that may change should not be a constant. We may not need a UI to change them
    if it is unlikely but possible they will change. In that case it is ok we can change the
    value in a db table or config file.

The second sentence is the important half: the standard is NOT "build an admin screen for every
value", it is "put the value where changing it costs what the change is worth". The prose half
is `claude/rules/configuration.md`; the reasoning, the measurements, the four false-positive
classes these detectors had to lose, and what was deliberately NOT implemented are in
`docs/values-that-change.md`. None of that is restated here -- a rule in two layers drifts.

Three shapes. The first, which judges configuration READS rather than declarations, lives in
`standards_config_defaults.py` (split out 2026-10-22). The other two are here: the first is
per-file, the second repo-level, running from `standards_repository.py` because no per-file
rule can see the other file:

  * `config-default-in-code` -- a configuration read whose fallback is a literal; see
    `standards_config_defaults.py`.
  * `const-environment-literal` -- a constant whose VALUE is environment-specific by its own
    shape: an e-mail address, an absolute filesystem path, a URL or a host:port.
  * `const-duplicated-literal` -- one value with two homes, or a constant written raw in
    another file. It reads the tree twice, and it honours its own line exemptions rather than
    relying on the driver's filter, which only ever sees the per-file walk's findings.

`const-environment-literal` is also what catches a configuration fallback reached through a
helper, which `config-default-in-code` cannot see -- the blind spot is stated in that module.

Source of truth: engineering-standards/engineering_standards/standards_constants.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple

from standards_core import CheckConfig, Violation, iter_code_lines, warn_unreadable
from standards_exemptions import line_exemption_reason
from standards_scope import SCRIPT_SUFFIXES

ENVIRONMENT_LITERAL_RULE = "const-environment-literal"
DUPLICATED_LITERAL_RULE = "const-duplicated-literal"

# Suffixes any of these rules has an opinion about. The constant question is asked in every
# language here; only the syntax differs.
CONSTANT_SUFFIXES = (".cs", ".razor", ".py", ".php", *SCRIPT_SUFFIXES)

# ---- const-environment-literal -----------------------------------------------------------

# A constant declaration, per language, capturing the name and the quoted value. Deliberately
# narrow: a `const`/`final`/UPPER_SNAKE declaration, never an ordinary assignment, because the
# rule is about a value pinned for the life of a build.

# THE LITERAL MUST BE THE WHOLE RIGHT-HAND SIDE, which is what this terminator enforces.
# Without it `APPLY = "--apply" in sys.argv` reads as a constant whose value is `--apply`, when
# it is a BOOLEAN whose value is True or False -- the literal is an operand, not the value.
# Found by running this rule against the pack itself, where it was one of four findings and the
# only false one. Anything a value can legitimately be followed by: a statement terminator, a
# trailing comment, or end of line.
DECLARATION_END = r"\s*(?:;|//|\#|$)"

CONSTANT_DECLARATIONS = (
    # C#: [modifiers] const string Name = "value";  and  static readonly string Name = "value";
    re.compile(
        r"\b(?:const|static\s+readonly)\s+string\s+(?P<name>\w+)\s*=\s*"
        r"(?P<quote>[\"'])(?P<value>(?:(?!(?P=quote)).)*)(?P=quote)" + DECLARATION_END
    ),
    # PHP: const NAME = 'value';  and  define('NAME', 'value');
    re.compile(
        r"\bconst\s+(?P<name>\w+)\s*=\s*"
        r"(?P<quote>[\"'])(?P<value>(?:(?!(?P=quote)).)*)(?P=quote)" + DECLARATION_END
    ),
    re.compile(
        r"\bdefine\s*\(\s*[\"'](?P<name>\w+)[\"']\s*,\s*"
        r"(?P<quote>[\"'])(?P<value>(?:(?!(?P=quote)).)*)(?P=quote)\s*\)"
    ),
    # Python / JS / TS: a module-level UPPER_SNAKE name, which is how all three spell "const".
    re.compile(
        r"^\s*(?:export\s+)?(?:const\s+)?(?P<name>[A-Z][A-Z0-9_]{2,})\s*(?::[^=]+)?=\s*"
        r"(?P<quote>[\"'])(?P<value>(?:(?!(?P=quote)).)*)(?P=quote)" + DECLARATION_END
    ),
)

# Values whose SHAPE says "this differs between environments". Each one was chosen because
# there is no deployment in which the development and production values are the same.
#
# THE FILESYSTEM PATH IS ANCHORED TO A CONVENTIONAL ROOT, and that is not fussiness. "Two or
# more slash-separated segments" is also the shape of a ROUTE -- `/platform/partnere`,
# `/api/v1` -- and a route constant is the correct answer to the duplication rule below, so a
# looser pattern would have this family contradicting itself. `/app/keys` is a deployment
# path; `/platform/partnere` is the application's own vocabulary and never moves between
# environments.
#
# THE HOST:PORT REQUIRES A DOT OR `localhost` for the same kind of reason: `^\w+:\d+$` also
# matches a clock time, and "12:30" reported as a host and port is the finding nobody trusts.
ENVIRONMENT_SHAPES = (
    (re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"), "an e-mail address"),
    (re.compile(r"^[a-z][a-z0-9+.-]*://\S+$", re.IGNORECASE), "a URL"),
    (
        re.compile(
            r"^/(?:app|var|etc|usr|opt|home|tmp|srv|mnt|data|root|run|proc)"
            r"(?:/[^/\s:*?\"<>|]+)+/?$"
        ),
        "an absolute filesystem path",
    ),
    (re.compile(r"^[A-Za-z]:[\\/][^\s:*?\"<>|]+$"), "an absolute filesystem path"),
    (re.compile(r"^\\\\[^\\\s]+\\[^\s]+$"), "a UNC network path"),
    (re.compile(r"^(?:localhost|[\w-]+(?:\.[\w-]+)+):\d{2,5}$"), "a host and port"),
)

# The one shape that looks environment-specific and is not: a namespace, an assembly name or a
# reverse-DNS identifier. `Microsoft.AspNetCore.Identity` matches nothing above, but a
# `Payvisia.Core.Demo.ErhrLogo.svg` resource name would look like a path to a looser pattern --
# which is why the path shapes require a leading separator rather than merely a dot and a slash.
MEDIA_TYPE = re.compile(r"^\w+/[\w.+-]+$")


def check_environment_literal(path: Path, lines: list[str]) -> Iterable[Violation]:
    """A constant whose value is environment-specific by its own shape.

    Test files are out of scope, and that is not politeness. A fixture URL
    (`https://erhr.payvisia.example`) is SUPPOSED to be pinned in the source -- reading it from
    the environment would make the test assert whatever the machine happened to be configured
    for, which is the opposite of a test. Measured on payvisia, where this was the rule's only
    false positive.
    """
    if path.suffix not in CONSTANT_SUFFIXES or _is_test_path(path):
        return

    for line_number, line in iter_code_lines(lines, path.suffix):
        for name, value in _declared_constants(line):
            described = _environment_shape(value)
            if described is None:
                continue

            # LINE-SCOPED, like `const-duplicated-literal` below and for its reason: a file
            # routinely holds both a real defect and a value that genuinely is the same in
            # every deployment -- a vendor's published API base, a well-known public endpoint --
            # and a header marker would silently cover both. Added 2026-09-09: the rule
            # shipped consulting no exemption helper at all while `collect_exemptions` read
            # a header marker for it and printed it as honoured, so a run said `exempt:` and
            # reported the finding in the same breath. Measured on sourcetext.ai, three files.
            if line_exemption_reason(lines, line_number - 1, ENVIRONMENT_LITERAL_RULE):
                continue

            yield Violation(
                path=path,
                line=line_number,
                rule=ENVIRONMENT_LITERAL_RULE,
                message=(
                    f"'{name}' is {described} ('{_clip(value)}') held as a constant. Nothing of "
                    f"that shape is the same in development and production, so changing it "
                    f"means a commit and a deploy. Read it from configuration."
                ),
            )


def _environment_shape(value: str) -> str | None:
    """What kind of environment-specific value this is, or None when it is not one."""
    candidate = value.strip()
    if not candidate or MEDIA_TYPE.match(candidate):
        return None

    for pattern, description in ENVIRONMENT_SHAPES:
        if pattern.match(candidate):
            return description
    return None


# ---- const-duplicated-literal ------------------------------------------------------------

# Below this, a value is a word rather than a value: "DK", "Id", "true", "GET". Every one of
# those legitimately appears in many files, and flagging them would bury the real findings.
MINIMUM_DUPLICATED_LENGTH = 6

# A value only counts when it is a TOKEN rather than a word of the domain's own language.
#
# MEASURED ON PAYVISIA, and this filter is the difference between a usable rule and one that
# gets baselined wholesale: without it, 9 of 26 findings were two authors independently writing
# the same ordinary Danish word. "Ligelønsanalyse" is an audit area in one file and the name of
# a flow step in another; they share a spelling and are not the same fact, and centralising
# them would couple two things that must be free to diverge.
#
# A token has structure (a separator) or is a compound identifier (an internal capital) --
# `/log-ind`, `interaction_seen`, `Demo:AdminPassword`, `CompanyId`. A word has neither.
# Whitespace makes it prose, which is out of scope for the reason `_has_literal` gives.
STRUCTURAL_CHARACTERS = set("/:_-.@\\")
INTERNAL_CAPITAL = re.compile(r"^\W*[A-Za-zÆØÅæøå]\w*[A-ZÆØÅ]")

# A test that asserts a constant's VALUE is pinning it on purpose -- writing
# `Assert.Equal(AccountEndpoints.LoginPath, ...)` instead would be a tautology that passes
# however the constant changes. Test files declare and duplicate freely.
TEST_PATH_FRAGMENTS = ("test", "tests", "spec", "__tests__", "fixtures")

# Blazor resolves `@page` at compile time and accepts a literal only, so a route constant
# CANNOT be referenced there. Reporting it would be demanding something the framework forbids,
# and a rule that asks for the impossible teaches people to ignore it. The honest instrument
# for that one unavoidable copy is a test that pins the route, not a scanner finding.
FRAMEWORK_LITERAL_LINE = re.compile(r"^\s*@page\b")


class Declaration(NamedTuple):
    """Where one constant is declared, so a finding can point at the other file and line."""

    path: Path
    name: str
    line: int


def check_duplicated_constants(root: Path, paths: list[Path], config: CheckConfig) -> Iterable[Violation]:
    """A value declared as a constant in one file and written raw in another.

    Repo-level for the reason the *-consistency family is: the question compares two files, so
    neither one can answer it alone. Given `paths` rather than the root so it examines exactly
    the set the per-file rules did.
    """
    if not config.check_constants:
        return

    declarations = _collect_declarations(paths)
    if not declarations:
        return

    yield from _repeated_declarations(declarations, root)
    yield from _raw_literals(root, paths, declarations)


def _repeated_declarations(declarations: dict[str, list[Declaration]], root: Path) -> Iterable[Violation]:
    """The same value declared as a constant in more than one file.

    Reported at every site after the first rather than dropped. An earlier draft dropped these
    on the grounds that "which declaration is the real one" is judgment -- true, and beside the
    point: two homes for one value is itself the finding, and dropping it lost the sharpest
    case payvisia had. `"CompanyId"` was declared in `PayvisiaDbContext` and again in
    `PersonalDataInterceptor`, and it is the string the whole product's tenant isolation keys
    on.
    """
    for value, sites in declarations.items():
        first = sites[0]
        for repeat in sites[1:]:
            if repeat.path == first.path or _is_exempt(repeat.path, repeat.line):
                continue

            yield Violation(
                path=repeat.path,
                line=repeat.line,
                rule=DUPLICATED_LITERAL_RULE,
                message=(
                    f"'{_clip(value)}' is declared here as '{repeat.name}' and again as "
                    f"'{first.name}' in {_relative(first.path, root)}. One value, two homes: "
                    f"they cannot be kept in step by anything but memory. Keep one and have "
                    f"the other reference it."
                ),
            )


def _raw_literals(root: Path, paths: list[Path], declarations: dict[str, list[Declaration]]) -> Iterable[Violation]:
    """A declared constant's value, written as a raw literal in some other file."""
    for path in paths:
        if path.suffix not in CONSTANT_SUFFIXES or _is_test_path(path):
            continue

        lines = _read(path)
        if lines is None:
            continue

        declaring_lines = {site.line for sites in declarations.values() for site in sites if site.path == path}

        for line_number, line in iter_code_lines(lines, path.suffix):
            if line_number in declaring_lines or FRAMEWORK_LITERAL_LINE.match(line):
                continue
            if line_exemption_reason(lines, line_number - 1, DUPLICATED_LITERAL_RULE):
                continue

            for value, sites in declarations.items():
                canonical = sites[0]
                if canonical.path == path or not _has_literal(line, value):
                    continue

                yield Violation(
                    path=path,
                    line=line_number,
                    rule=DUPLICATED_LITERAL_RULE,
                    message=(
                        f"'{_clip(value)}' is written here as a literal and declared as "
                        f"'{canonical.name}' in {_relative(canonical.path, root)}. A constant "
                        f"that guards some of its own uses is worse than none -- the reader "
                        f"who finds it stops looking. Reference it, or delete it and be honest."
                    ),
                )


def _collect_declarations(paths: list[Path]) -> dict[str, list[Declaration]]:
    """Every constant value worth tracking, keyed by value, sites in a stable order.

    Sorted by path so the "first" site -- the one every message points at -- is the same on
    every machine and in every run. An arbitrary-but-stable choice is fine here; an
    arbitrary-and-unstable one would make the findings churn between runs.
    """
    found: dict[str, list[Declaration]] = {}

    for path in sorted(paths):
        if path.suffix not in CONSTANT_SUFFIXES or _is_test_path(path):
            continue

        lines = _read(path)
        if lines is None:
            continue

        for line_number, line in iter_code_lines(lines, path.suffix):
            for name, value in _declared_constants(line):
                if _is_worth_tracking(value):
                    found.setdefault(value, []).append(Declaration(path, name, line_number))

    return found


def _is_worth_tracking(value: str) -> bool:
    """Whether this value is a token somebody would want in one place, or just a word."""
    if len(value) < MINIMUM_DUPLICATED_LENGTH or any(character.isspace() for character in value):
        return False

    return bool(STRUCTURAL_CHARACTERS & set(value)) or bool(INTERNAL_CAPITAL.match(value))


def _declared_constants(line: str) -> Iterator[tuple[str, str]]:
    """(name, value) for every constant declaration on this line, in any supported language.

    DE-DUPLICATED, because the patterns deliberately overlap: `const DB_HOST = '...'` in PHP is
    matched by both the PHP form and the UPPER_SNAKE form, and `export const API_BASE` by the
    TypeScript reading of the same. Overlap is the right design -- each pattern stays legible
    on its own -- but reporting one declaration twice is not.
    """
    seen: set[tuple[str, str]] = set()

    for pattern in CONSTANT_DECLARATIONS:
        for match in pattern.finditer(line):
            declaration = (match.group("name"), match.group("value"))
            if declaration in seen:
                continue
            seen.add(declaration)
            yield declaration


def _has_literal(line: str, value: str) -> bool:
    """Whether `value` appears on this line as a quoted literal rather than inside a word.

    Quoted, deliberately: the same text inside prose is prose. `data-access.md`'s inline-code
    precedent applies -- a statutory citation inside a Danish sentence is not a duplicated
    constant, and treating it as one would make the rule unusable in this estate.
    """
    return f'"{value}"' in line or f"'{value}'" in line


def _is_exempt(path: Path, line_number: int) -> bool:
    """Whether the marker at this site excuses it.

    THIS RULE HAS TO ASK FOR ITSELF, unlike every per-file rule in the pack. The driver filters
    line exemptions out of the per-file walk's findings; a repo-level rule's findings never pass
    through that filter, so a `standards: const-duplicated-literal exempt` marker was written,
    read by nobody, and the finding stood -- measured on this pack's own `sync_pack.py`, where
    two correct duplications could not be excused. Re-reading the file costs one read per
    finding, which is nothing beside the two full passes this rule already makes.
    """
    lines = _read(path)
    if lines is None:
        return False

    return line_exemption_reason(lines, line_number - 1, DUPLICATED_LITERAL_RULE) is not None


def _is_test_path(path: Path) -> bool:
    """Whether this file is a test, which every rule in this family skips.

    Three reasons, one exclusion. A test PINS a constant's value on purpose -- asserting
    against the constant instead would be a tautology that passes however it changes. A test's
    fixture URL is supposed to live in the source, not in an environment. And a scanner's own
    tests carry example source as literals, so these rules would otherwise read their own
    examples back as findings.
    """
    parts = {part.lower() for part in path.parts}
    if parts & set(TEST_PATH_FRAGMENTS):
        return True
    stem = path.stem.lower()
    return stem.startswith("test_") or stem.endswith(("test", "tests", "spec"))


def _read(path: Path) -> list[str] | None:
    """This rule's own file reader, which must say so when it cannot read one.

    A file that fails to open here contributes no declarations and no duplications, which is
    indistinguishable from a file that legitimately holds neither -- so the rule would report
    the repo clean about a file it never saw. That is the failure the whole pack is built
    against, arriving through the error path. `utf-8-sig` drops the BOM Visual Studio writes
    into .razor files, which would otherwise hide a line-1 `@page` from FRAMEWORK_LITERAL_LINE.
    """
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError as error:
        warn_unreadable(
            path,
            error,
            "its constants are invisible to const-duplicated-literal, so a value declared "
            "or duplicated there will not be reported",
        )
        return None


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def _clip(value: str, limit: int = 48) -> str:
    """Values reach a message; a long one would push the advice off the line."""
    return value if len(value) <= limit else value[: limit - 1] + "…"

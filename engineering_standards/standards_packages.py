#!/usr/bin/env python3
"""How a package version may be declared, and how being behind one is justified.

Rules here: package-wildcard, dependency-holdback.

THE SECOND RULE is about the other half of the same subject. `package-wildcard` governs how a
version is WRITTEN; `dependency-holdback` governs how a decision to stay on an old one is
RECORDED -- in dependabot.yml's `ignore:` list, with a reason and an expiry date, in the same
lines as the suppression it justifies. They share a module because they share a failure: a
version decision that lives somewhere nothing reads.

THE POLICY THIS ENFORCES is the estate's, stated by Jack: "do not pin anything, that is how we
get legacy packs in new apps. **We only define minimum.**" A wildcard looks like the opposite of
a pin and is therefore easy to mistake for compliance -- but it is neither a pin nor a minimum.
It is a *deferral*, and what it defers is resolved at restore time on whichever machine happens
to run it.

WHY THAT MATTERS MORE THAN IT SOUNDS. `Version="48.*"` means two developers, or a developer and
CI, can build DIFFERENT CODE FROM THE SAME COMMIT, months apart, with nothing in the repository
recording which. The build is not reproducible, and the failure surfaces as "works on my
machine" -- the single hardest class of bug to attribute, because the diff is empty.

A plain `Version="52.3.0"` is already the pin the policy asks for (every dependency pinned
exactly, Jack 2026-10-02): NuGet resolves a direct reference to the LOWEST version satisfying
it, so the number is deterministic. That is the whole fix, and it is one line. Keeping a pin
current is the dependency gate's job (deps.py), not this rule's.

THE CASE THAT MOTIVATED IT. InvoTrack carried `Stripe.net Version="47.*"` and a comment
explaining that two machines had built different code from one commit; it was corrected to
`52.3.0` on 2026-08-24. An audit on 2026-08-26 found **DonorLink still on `Stripe.net 48.*`** --
the same defect, in a live donation platform, four majors behind, and missed because the earlier
sweep searched for bracket-syntax pins rather than for wildcards. One repo learning a lesson is
not the estate learning it; that is what a rule is for.

RUNTIME VERSUS DEV, for npm. `"*"` and `"latest"` are flagged in `dependencies` and deliberately
NOT in `devDependencies`. A floating type definition cannot reach production and pinning
`@types/*` to the runtime it describes is a normal idiom; a floating runtime dependency ships.

Source of truth: engineering-standards/engineering_standards/standards_packages.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import Violation
from standards_exemptions import line_exemption_reason
from standards_scope import (
    COMPOSER_FILENAME,
    DEPENDABOT_FILENAME,
    PACKAGE_JSON_FILENAME,
    PROJECT_FILENAME,
    REQUIREMENTS_FILENAME,
)

WILDCARD_RULE = "package-wildcard"
HOLDBACK_RULE = "dependency-holdback"

# `- dependency-name: "Foo"` inside dependabot.yml's `ignore:` list. Deliberately loose about
# quoting and spacing, because this is hand-edited YAML.
DEPENDABOT_IGNORE_ENTRY = re.compile(r"""^\s*-\s*dependency-name\s*:\s*["']?(?P<name>[^"'\s]+)["']?\s*$""")
IGNORE_SECTION = re.compile(r"^\s*ignore\s*:\s*$")
# What a justified hold-back looks like. The date is required and is the whole point.
HOLDBACK_MARKER = re.compile(
    r"standards:\s*dependency-holdback\b(?P<reason>.*?)REVISIT\s+(?P<date>\d{4}-\d{2}-\d{2})",
    re.IGNORECASE | re.DOTALL,
)
HOLDBACK_MARKER_UNDATED = re.compile(r"standards:\s*dependency-holdback\b", re.IGNORECASE)

# `<PackageReference Include="X" Version="Y" />`, and the rarer attribute order. A `<Version>`
# child element is matched separately because MSBuild accepts both spellings.
PACKAGE_REFERENCE = re.compile(
    r"""<PackageReference\s+(?=[^>]*Include\s*=\s*["'](?P<name>[^"']+)["'])"""
    r"""[^>]*Version\s*=\s*["'](?P<version>[^"']+)["']""",
    re.IGNORECASE,
)
VERSION_ELEMENT = re.compile(r"<Version>\s*(?P<version>[^<]+?)\s*</Version>", re.IGNORECASE)

# An MSBuild property -- `$(MauiVersion)` -- is resolved centrally at build time from the SDK or
# Directory.Build.props. That is the opposite of floating: one definition, one value, recorded in
# the repository. Never a finding.
MSBUILD_PROPERTY = re.compile(r"\$\([^)]+\)")

# npm. `"pkg": "*"`, `"latest"`, `"x"` -- the three spellings of "whatever is newest".
NPM_FLOATING = re.compile(r"""["'](?P<name>[@\w./-]+)["']\s*:\s*["'](?P<version>\*|latest|x)["']""", re.IGNORECASE)
NPM_DEPENDENCY_BLOCK = re.compile(r"""["'](?P<block>\w*[Dd]ependencies)["']\s*:""")

# Composer. A `"vendor/pkg": "<constraint>"` entry inside a require block. The constraint is
# examined by `_composer_is_floating` rather than a single regex, because "floating" here is
# several distinct spellings and a caret/tilde/range must pass.
COMPOSER_REQUIRE_ENTRY = re.compile(
    r"""["'](?P<name>[\w./-]+/[\w.-]+|php|ext-[\w.-]+)["']\s*:\s*["'](?P<version>[^"']+)["']"""
)
COMPOSER_REQUIRE_BLOCK = re.compile(r"""["'](?P<block>require(?:-dev)?)["']\s*:""")

# roave/security-advisories is DESIGNED to be tracked as `dev-latest` -- that is its documented,
# only-supported usage (it ships an ever-growing conflict list and pins nothing itself). Flagging
# it would teach the rule is noise, the way flagging a framework filename would. It is the
# composer twin of the `@types/*` devDependency exception on the npm side.
COMPOSER_INTENTIONAL_DEV = frozenset({"roave/security-advisories"})

# A requirements.txt line that names a package and NOTHING else -- no `==`, `>=`, `~=`, `<`,
# `>`, `!=`, `@ url`, no environment marker. Extras in brackets are allowed and ignored. That
# bare form is the only unambiguous floating case; a `>=` or `==` both record a decision and are
# left alone by THIS rule (the estate pins exactly, and the dependency gate -- deps.py -- is what
# refuses a `>=` in a repo that has adopted it; this rule only catches the bare floating form).
REQUIREMENTS_UNPINNED = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]+\])?\s*$")


def wildcards_in_project(lines: list[str]) -> Iterable[tuple[int, str, str]]:
    """(line index, package, version) for every wildcard version in a project file."""
    for index, line in enumerate(lines):
        found = PACKAGE_REFERENCE.search(line)
        if found:
            version = found.group("version")
            if "*" in version and not MSBUILD_PROPERTY.search(version):
                yield index, found.group("name"), version
            continue

        element = VERSION_ELEMENT.search(line)
        if element:
            version = element.group("version")
            if "*" in version and not MSBUILD_PROPERTY.search(version):
                yield index, "this package", version


def wildcards_in_manifest(lines: list[str]) -> Iterable[tuple[int, str, str]]:
    """(line index, package, version) for every floating RUNTIME dependency in package.json.

    Tracks which block each line sits in, because the answer differs between them: a floating
    `@types/*` in devDependencies is a normal idiom that cannot reach production, while the same
    spelling under `dependencies` ships whatever was newest on the machine that installed it.
    """
    runtime = False
    for index, line in enumerate(lines):
        block = NPM_DEPENDENCY_BLOCK.search(line)
        if block:
            runtime = block.group("block").casefold() == "dependencies"

        if not runtime:
            continue
        found = NPM_FLOATING.search(line)
        if found:
            yield index, found.group("name"), found.group("version")


def _composer_is_floating(version: str) -> bool:
    """True for a composer constraint that resolves to a moving target.

    `dev-master`/`dev-main` track a branch head; `@dev` is a stability flag, not a floor;
    `2.x-dev` is the branch alias; `*` is anything. A caret (`^1.2`), tilde (`~1.2`), range
    (`>=1.2`) or exact (`1.2.3`) all record a decision and pass.
    """
    v = version.strip()
    if "*" in v:
        return True
    if v.startswith("dev-"):
        return True
    if v == "@dev" or v.endswith("@dev"):
        return True
    return "x-dev" in v


def wildcards_in_composer(lines: list[str]) -> Iterable[tuple[int, str, str]]:
    """(line index, package, version) for every floating require in composer.json.

    Tracks the enclosing block only to keep the report honest about where the entry sits;
    require and require-dev are treated alike, because a floating dev tool is as
    unreproducible as a floating runtime one -- with the single documented exception of
    roave/security-advisories, whose only supported form IS `dev-latest`.
    """
    in_require = False
    for index, line in enumerate(lines):
        block = COMPOSER_REQUIRE_BLOCK.search(line)
        if block:
            in_require = True
            continue
        # A closing brace at the start of a stripped line ends the current require block.
        if in_require and line.strip().startswith("}"):
            in_require = False
        if not in_require:
            continue
        entry = COMPOSER_REQUIRE_ENTRY.search(line)
        if not entry:
            continue
        name = entry.group("name")
        if name.casefold() in COMPOSER_INTENTIONAL_DEV:
            continue
        if _composer_is_floating(entry.group("version")):
            yield index, name, entry.group("version")


def unpinned_in_requirements(lines: list[str]) -> Iterable[tuple[int, str, str]]:
    """(line index, package, "") for every bare, version-less requirement line."""
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # An environment marker or a URL is a decision; only a bare name is floating.
        if ";" in line or "@" in line:
            continue
        found = REQUIREMENTS_UNPINNED.match(line)
        if found:
            yield index, found.group("name"), ""


def check_package_wildcards(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a dependency whose version is decided at restore time rather than in the repo."""
    if PROJECT_FILENAME.match(path.name):
        found = wildcards_in_project(lines)
        fix = (
            'Name the version you actually want -- a plain `Version="1.2.3"` is already a '
            "MINIMUM in NuGet, and resolves deterministically to that version for a direct "
            "reference, so it is both the floor the estate policy asks for and reproducible"
        )
    elif PACKAGE_JSON_FILENAME.match(path.name):
        found = wildcards_in_manifest(lines)
        fix = (
            "Name a range with a floor -- `^1.2.3` -- so the lockfile has something to resolve "
            "against and the repository records what this depends on"
        )
    elif COMPOSER_FILENAME.match(path.name):
        found = wildcards_in_composer(lines)
        fix = (
            "Name a range with a floor -- `^1.2` -- so composer.lock has something to resolve "
            "against; a `dev-master` tracks a branch head that changes under you, and `@dev`/`*` "
            "pin nothing at all"
        )
    elif REQUIREMENTS_FILENAME.match(path.name):
        found = unpinned_in_requirements(lines)
        fix = (
            "Give it at least a floor -- `Django>=4.2` -- so the install is reproducible and the "
            "file records what this actually depends on; a bare name installs whatever is newest "
            "on the day"
        )
    else:
        return

    for index, name, version in found:
        if line_exemption_reason(lines, index, WILDCARD_RULE):
            continue
        declared = f"declared as `{version}`" if version else "declared with no version at all"
        yield Violation(
            path=path,
            line=index + 1,
            rule=WILDCARD_RULE,
            message=(
                f"`{name}` is {declared}, which is not a version -- it is a "
                f"decision deferred to whichever machine runs the restore. Two developers, or a "
                f"developer and CI, can build different code from this same commit with nothing "
                f"in the repository recording which, and that failure arrives as "
                f"works-on-my-machine with an empty diff. {fix}. "
                f"If a floating version is genuinely required here, say why on the line: "
                f"`standards: {WILDCARD_RULE} exempt -- <reason>`."
            ),
        )


def _preceding_comment_block(lines: list[str], index: int) -> str:
    """The contiguous `#` comment lines immediately above `index`, joined.

    Joined rather than examined one at a time because a reason worth reading rarely fits on one
    line, and a marker split across two comment lines is the normal case, not the exception.
    """
    collected: list[str] = []
    cursor = index - 1
    while cursor >= 0:
        stripped = lines[cursor].strip()
        if not stripped.startswith("#"):
            break
        collected.append(stripped.lstrip("#").strip())
        cursor -= 1
    return " ".join(reversed(collected))


def check_dependency_holdback(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a Dependabot ignore entry that does not say why, and until when.

    WHAT AN `ignore:` ENTRY ACTUALLY IS: a decision to stay on an old version, expressed
    somewhere no reviewer looks and no build reads. It suppresses the only automation that
    would otherwise raise the question monthly, so an unexplained one is indistinguishable
    from neglect -- and an explained-but-undated one becomes neglect on its own, quietly,
    the moment the reason stops being true.

    THE CASE THAT MOTIVATED IT (2026-08-27). InvoTrack's dependabot.yml carried four ignores
    with careful prose reasons and NO dates. Meanwhile the decision to move Stripe.net to 52.3.0
    lived in a C# comment three lines above the version string -- which no bot reads and nobody
    re-reads -- and a review agent, trusting that stale comment, REVERTED a Dependabot bump that
    was implementing the newer decision. Two hiding places for one class of decision, and the
    wrong one won.

    So the rule is not "explain your ignores". It is: **the suppression and its justification
    must be the same object**, and the justification must expire. A date is what turns "behind
    on purpose" back into a question instead of letting it decay into "behind, and nobody
    remembers why".

    The date is NOT validated against today. A past REVISIT is a prompt to re-decide, not a
    build failure -- failing on it would punish the repo that wrote an honest near-term date
    and reward the one that wrote 2099. Rule 3's health sweep is what re-raises it.
    """
    if not DEPENDABOT_FILENAME.match(path.name):
        return

    in_ignore_section = False
    ignore_indent = 0
    for index, line in enumerate(lines):
        if IGNORE_SECTION.match(line):
            in_ignore_section = True
            ignore_indent = len(line) - len(line.lstrip())
            continue

        if not in_ignore_section:
            continue

        stripped = line.strip()
        # A non-comment, non-blank line at or left of `ignore:`'s own indent ends the section.
        if stripped and not stripped.startswith("#"):
            indent = len(line) - len(line.lstrip())
            if indent <= ignore_indent:
                in_ignore_section = False
                continue

        entry = DEPENDABOT_IGNORE_ENTRY.match(line)
        if not entry:
            continue

        comment = _preceding_comment_block(lines, index)
        if HOLDBACK_MARKER.search(comment):
            continue

        name = entry.group("name")
        undated = bool(HOLDBACK_MARKER_UNDATED.search(comment))
        problem = (
            "carries a `standards: dependency-holdback` marker with no `REVISIT <date>`"
            if undated
            else "has no `standards: dependency-holdback` marker"
        )
        yield Violation(
            path=path,
            line=index + 1,
            rule=HOLDBACK_RULE,
            message=(
                f"The Dependabot ignore for `{name}` {problem}. An ignore is a decision to stay "
                f"behind, and it suppresses the one thing that would otherwise raise the "
                f"question again -- so undated, it is how a deliberate hold-back becomes a "
                f"forgotten one. Put the reason and an expiry on the lines directly above it: "
                f"`# standards: {HOLDBACK_RULE} -- <why> REVISIT <YYYY-MM-DD>`. Keeping the "
                f"justification in the same lines as the suppression is the point: a reason "
                f"recorded anywhere else (a code comment, a design doc) goes stale without the "
                f"suppression noticing."
            ),
        )

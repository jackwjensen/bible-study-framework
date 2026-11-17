#!/usr/bin/env python3
"""The toolchain versions this estate develops on, and the rule that enforces the safe half.

Rules here: action-version, ci-toolchain, runtime-support.
Floors declared here but enforced elsewhere: NODE_FLOOR (standards_node_support.py),
PYTHON_FLOOR (standards_python_support.py), PHP_FLOOR (standards_php_support.py), and
SERVER_UBUNTU (standards_runner.py).

THE LIST LIVES IN THIS FILE, not in a versions.toml beside it. Every adopted repo already
receives `engineering_standards/*.py`, so putting the numbers here makes them present wherever the scanner
runs, with no second file to sync and no chance of the data and its enforcement disagreeing.
A TOML file would read more nicely and would be the pack's own "restated in two layers" rule
broken in the one place it is least affordable.

FOUR TIERS, FOUR DIFFERENT AUTHORITIES. Collapsing them into one list is the mistake this
module exists to avoid, because a single "upgrade everything" list turns a routine
/apply-standards run into an unattended production runtime change:

  ACTION_FLOORS   CI plumbing. No runtime risk at all -- the action's own Node runtime is
                  invisible to the application. /apply-standards rewrites these outright and
                  `check_action_versions` below fails a repo that falls behind.

  CI_DEFAULTS     What CI installs to run the checks. Applied only where the repo declares
                  nothing itself; a repo with a .nvmrc or global.json wins, because CI's
                  toolchain must satisfy the app rather than dictate to it.

  RUNTIME_BASELINE  What production actually runs. REPORTED, NEVER REWRITTEN. Changing it
                  means a rebuild, a redeploy and a real chance of breakage, so it is a
                  watched per-repo decision. No rule enforces it, deliberately.

  DOTNET_FLOOR    The oldest .NET major the estate will ship. ENFORCED, NEVER REWRITTEN
  NODE_FLOOR      -- the fourth tier, added 2026-08-18, and the distinction from the tier
                  above it is the whole reason it can exist without contradicting it.
                  NODE_FLOOR joined it on 2026-08-25 for a reason worth keeping: Node 24
                  had already been declared the estate minimum and set in CI_DEFAULTS, and
                  four pins in two repos stayed on 22 with nothing reporting them. Tier 2
                  defers to a repo's own pin, so it can never move a repo that has one.
                  Only a floor fails a repo, and failing is what makes /apply-standards
                  the moment a repo catches up.

THE FOURTH TIER, AND WHY IT IS NOT THE THIRD ONE IN DISGUISE. RUNTIME_BASELINE is a
preference: "what we run today". Enforcing a preference is what the tier-3 note refuses,
because /apply-standards would then rewrite a production runtime unattended. DOTNET_FLOOR
is not a preference -- it is a support *fact* with a date attached, and shipping past that
date means shipping something Microsoft has stopped patching. So the floor is enforced the
way ACTION_FLOORS is enforced: the scanner FAILS a repo that falls behind and tells a human
to act. It never edits a project file, and /apply-standards never touches a TFM. "Fail and
name it" and "rewrite it for you" are different powers, and only the second one was ever
the thing tier 3 declined.

Source of truth: engineering-standards/engineering_standards/standards_versions.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from standards_core import Violation
from standards_exemptions import line_exemption_reason

# Safe direction: standards_scope imports only standards_core, so it cannot import back.

RULE = "action-version"
TOOLCHAIN_RULE = "ci-toolchain"
RUNTIME_RULE = "runtime-support"

# The pack's own version stamp. Bumped manually on any substantive pack change (see the
# pack CLAUDE.md, "Changing a rule"); shipped inside engineering_standards/, so every synced repo carries
# the version it was last brought current to, and every baseline records the scanner
# version that wrote it (`packVersion` in .standards-baseline.json). PROVENANCE, not
# authority: sync_pack.py still decides drift by comparing file contents, so a forgotten
# bump can never hide a stale file -- it only blurs the label on the report.
PACK_VERSION = "2026.11.11-5"

# Minimum MAJOR version per action. Majors only: a patch pin is a security practice some repos
# use deliberately (wordpress-docker-template pins @v7.0.0) and demanding an exact string would
# fight it for no benefit.
#
# WHY THESE NUMBERS: each is the first major on the node24 runtime. Everything below emits
# "Node.js 20 is deprecated ... being forced to run on Node.js 24" on every run, which means a
# compatibility shim is carrying it and nothing will announce it when the shim is withdrawn.
# Verified by reading `runs.using` from each action's action.yml at each ref, not from a
# changelog -- setup-dotnet v4 and pnpm/action-setup v4 both still say node20.
ACTION_FLOORS: dict[str, int] = {
    "actions/checkout": 5,
    "actions/setup-python": 6,
    "actions/setup-node": 7,
    "actions/setup-dotnet": 6,
    "actions/upload-artifact": 4,
    "actions/download-artifact": 4,
    "pnpm/action-setup": 6,
    "appleboy/ssh-action": 1,
}

# What /apply-standards writes into a check job when the repo declares nothing of its own.
#
# node moved 22 -> 24 on 2026-08-25, estate-wide: 24 is the minimum accepted anywhere. Note
# what this tier can and cannot do about that -- CI_DEFAULTS only fills a gap, so it reaches a
# repo that declares nothing and is silently outranked by any repo that pins its own version in
# a .nvmrc or a workflow. Bumping this alone is exactly how the previous attempt at "everything
# is on 24" left three repos behind.
CI_DEFAULTS: dict[str, str] = {
    "python": "3.14",
    "node": "24",
    "dotnet": "10.0.x",
}

# ---- tier 4: the oldest .NET the estate will ship ------------------------------------------
#
# THE NUMBER IS A SUPPORT DATE, NOT A TASTE. Dates below are Microsoft's published
# end-of-support, read from the official policy page on 2026-08-18 (which the page itself
# stamped "last updated 2026-08-11"), not from a blog post or from memory:
#
#     .NET  8 (LTS)  released 2023-11-14  ends 2026-11-10   -- maintenance
#     .NET  9 (STS)  released 2024-11-12  ends 2026-11-10   -- maintenance
#     .NET 10 (LTS)  released 2025-11-11  ends 2028-11-14   -- the only one in ACTIVE support
#
# 8 and 9 expire on the SAME DAY, which is what makes 10 the only defensible floor: there is
# no "one more release of headroom" option to fall back to. STS support was extended from 18
# to 24 months, which is why .NET 9 outlived the May-2026 date some older notes still carry.
#
# THIS IS NOT "BE ON THE NEWEST". A floor of 10 is enforceable precisely because 11 is not
# out yet, so nobody has to chase anything; when .NET 11 (STS) ships in November 2026 the
# floor STAYS at 10, because 10 is the LTS and an STS is not an upgrade the estate owes
# anyone. Raise this to 12 in late 2028, not to 11 in late 2026.
DOTNET_FLOOR = 10
DOTNET_END_OF_SUPPORT: dict[int, str] = {
    6: "2024-11-12",
    7: "2024-05-14",
    8: "2026-11-10",
    9: "2026-11-10",
}

# ---- tier 4: the oldest Node major the estate will run --------------------------------------
#
# 24 is the minimum accepted anywhere, decided 2026-08-25. It is the active LTS; 22 has moved to
# maintenance and 20 is done.
#
# WHY THIS IS A FLOOR AND NOT JUST A NUMBER IN CI_DEFAULTS. It was tried as a number first, and
# the number is the tier that CANNOT deliver "everywhere": CI_DEFAULTS only fills a gap, so it
# reaches a repo that declares nothing and is outranked by every repo that pins its own version
# -- which is every repo that has any Node at all. That is precisely how the previous attempt at
# moving the estate to 24 left three pins behind and nothing said a word. A floor is the tier
# that fails a repo, so applying or updating the pack in a repo is the moment it has to move.
#
# WHAT IT READS, and why these three. A Node version gets declared in exactly three places in
# this estate, and a floor that missed any one of them would be enforcing the toolchain
# everywhere except where it is actually set:
#
#   .nvmrc                    the repo's own pin, which outranks everything else
#   node-version: in CI       what the workflow installs
#   FROM node:N in a Dockerfile   what the image is built on
#
# package.json `engines.node` is deliberately NOT read. It is conventionally a RANGE (">=20",
# "^22 || ^24"), and a floor that tried to judge a range would either mis-read a permissive one
# as a violation or accept a range whose lower bound is years old. No repo here sets it; when
# one does, it wants its own rule with range semantics rather than a wrong answer from this one.
# The rule, its readers and its regexes moved to standards_node_support.py on 2026-08-25 --
# this module was at 470 of the pack's own 500 lines, and the parsing had to become a reusable
# reader before `node-consistency` could see it. The NUMBER stays here, beside DOTNET_FLOOR and
# PYTHON_FLOOR, because this file is the one place a human looks when lifting a floor.
NODE_FLOOR = 24
# ---- tier 4: the oldest Python the estate will run ------------------------------------------
#
# 3.14, decided by Jack on 2026-08-25, and the reasoning is the reason it is 3.14 and not 3.12:
# "code gets outdated really fast, so if we use packs that are already out of maintenance then
# we build legacy from day one."
#
# PYTHON HAS TWO SUPPORT BOUNDARIES, NOT ONE, and that is the whole argument. Every release
# gets ~2 years of BUGFIX support and then ~3 years of security-only patches (PEP 602). A floor
# set at "still gets security patches" would accept 3.10, which stopped getting bugfixes in
# April 2023. A floor set at "still gets bugfixes" is 3.14. Verified against
# https://devguide.python.org/versions/ on 2026-08-25, not from memory -- the same discipline
# DOTNET_END_OF_SUPPORT documents. The per-version dates are in standards_python_support.py,
# beside the rule that quotes them, exactly as EF_PROVIDER_SUNSET sits beside its own rule.
#
# THIS IS STILL A MINIMUM, NOT "BE ON THE NEWEST". 3.14 stays in bugfix support until
# 2027-10-01, so it does NOT need re-deciding when 3.15 ships on 2026-10-01 -- a repo that
# starts on 3.15 passes, because being ahead of a floor is never a finding. Raise this to 3.15
# in late 2027, when 3.14 leaves bugfix support, not the day 3.15 appears.
#
# A TUPLE, NOT A FLOAT AND NOT A STRING. `3.9 > 3.14` is true for floats and true for strings;
# it is false for `(3, 9) > (3, 14)`, which is the only one of the three that is right. Every
# version this rule reads is normalised to a (major, minor) tuple for that reason alone.
PYTHON_FLOOR = (3, 14)

# ---- tier 4: the oldest PHP the estate will run ---------------------------------------------
#
# 8.5, decided 2026-08-25 on the same reasoning as PYTHON_FLOOR. PHP's "active support" is the
# bugfix phase: two years of real fixes, then two more of security-only patches. Dates read from
# php.net/supported-versions.php that day, and carried in PHP_ACTIVE_END beside the rule.
#
#     8.3  active support ENDED 2025-12-31   -- what all four PHP repos declare today
#     8.4  active support ends  2026-12-31   -- four months of runway
#     8.5  active support ends  2027-12-31
#
# 8.4 satisfies the letter of "still maintained" and would force a second estate-wide migration
# before Christmas, which is the opposite of lifting a floor deliberately. 8.5 is the current
# stable with over a year ahead of it. The rule is in standards_php_support.py.
PHP_FLOOR = (8, 5)

# ---- the Ubuntu release the production server runs ------------------------------------------
#
# 26.04, since the Allegro IT server was upgraded on 2026-10-25 (Jack, 2026-11-01: CI pinned "to
# the version that is actually on the server"). NOT A FLOOR AND NOT A PREFERENCE, a fact about
# one machine: CI that checks a repo on a different OS from the one it deploys to is the "works
# in test, not in production" gap, and `ubuntu-latest` hid it -- it still pointed at 24.04 when
# the server moved, and nothing said so. `runner-image` (standards_runner.py) fails any other Ubuntu
# label. Change this the day the server is upgraded, in the same commit as the pack's workflows.
SERVER_UBUNTU = "26.04"

# Only the unified .NET 5+ target frameworks, optionally with a platform suffix
# (`net10.0-windows`). Deliberately does NOT match `netstandard2.0` or `net48`: a library
# targeting netstandard is making a compatibility choice this rule has no opinion about, and
# a .NET Framework project is a different world entirely. Both would be pure false positives,
# and a version rule that cries wolf is one that gets exempted wholesale.
TARGET_FRAMEWORK = re.compile(r"<TargetFrameworks?>([^<]+)</TargetFrameworks?>", re.IGNORECASE)
DOTNET_TFM = re.compile(r"^net(\d+)\.0(?:-[\w.]+)?$", re.IGNORECASE)

# What the estate runs, for the tier-3 report.
#
# node reads 24 from 2026-08-25 even though two repos are still on 22, and that is deliberate:
# THE PACK LEADS, THE REPOS FOLLOW ON APPLY. This was briefly set to 22 "because that is what
# production actually runs today", which inverts the whole mechanism -- a pack that waits for
# every repo to move before it states the standard can never be the thing that moves them.
# `/apply-standards` in a repo is where the repo catches up, and NODE_FLOOR below is what makes
# that non-optional rather than a suggestion.
RUNTIME_BASELINE: dict[str, str] = {
    "dotnet": "net10.0",
    "node": "24",
    "python": "3.14",
    "php": "8.5",
    "nginx": "1.27-alpine",
}

# `uses: owner/repo@ref` and `uses: owner/repo/sub@ref`. The ref is captured whole so a commit
# SHA can be recognised and skipped rather than mis-parsed as a version.
USES = re.compile(r"""uses:\s*['"]?([\w.-]+/[\w./-]+?)@([\w.-]+)['"]?\s*$""")

COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
MAJOR = re.compile(r"^v?(\d+)")


def action_major(reference: str) -> int | None:
    """The major version a `uses:` ref pins, or None when it cannot be compared.

    A 40-character SHA is a deliberate, *stricter* pin than a major tag -- it is what security
    guidance recommends -- so it is skipped rather than flagged. A branch name (`@main`) is
    likewise uncomparable; it is a different problem from being out of date and this rule does
    not pretend to judge it.
    """
    if COMMIT_SHA.match(reference):
        return None
    found = MAJOR.match(reference)
    return int(found.group(1)) if found else None


def check_action_versions(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a workflow pinning a known action below the estate's floor.

    Only actions named in ACTION_FLOORS are judged. An action nobody has taken a position on
    is not this rule's business, and flagging every unknown `uses:` would make the rule noise
    that gets exempted wholesale.
    """
    for index, line in enumerate(lines):
        found = USES.search(line)
        if not found:
            continue

        name, reference = found.group(1), found.group(2)
        floor = ACTION_FLOORS.get(name)
        if floor is None:
            continue

        major = action_major(reference)
        if major is None or major >= floor:
            continue

        if line_exemption_reason(lines, index, RULE):
            continue

        yield Violation(
            path=path,
            line=index + 1,
            rule=RULE,
            message=(
                f"{name}@{reference} is below the estate floor of v{floor}. Majors under "
                f"v{floor} run on the deprecated Node 20 runtime, which GitHub currently "
                f"carries on a compatibility shim -- nothing will announce it when that is "
                f"withdrawn. Bump to v{floor} or later; see engineering_standards/standards_versions.py "
                f"for the list and why each number is what it is."
            ),
        )


# ---- tier 2: CI must satisfy the repo's OWN pin, not merely the estate default -------------

DOTNET_VERSION_KEY = re.compile(r"^(\s*)dotnet-version:\s*(.*)$")
BLOCK_SCALAR = ("|", ">", "|-", ">-", "|+", ">+")
VERSION_MAJOR = re.compile(r"^v?(\d+)\.")


def dotnet_versions_declared(lines: list[str]) -> Iterable[tuple[int, list[str]]]:
    """Every `dotnet-version:` declaration in a workflow, and the versions it lists.

    setup-dotnet accepts an inline value *and* a block scalar listing several SDKs, and the
    block form is exactly the right fix for a repo that must satisfy a global.json pin while
    still targeting an older framework. A checker that understood only the inline form would
    flag the very shape it should be recommending, so both are parsed.
    """
    for index, line in enumerate(lines):
        found = DOTNET_VERSION_KEY.match(line)
        if not found:
            continue

        indent, value = found.group(1), found.group(2).strip().strip("'\"")
        if value and value not in BLOCK_SCALAR:
            yield index, [value]
            continue

        listed: list[str] = []
        for following in lines[index + 1 :]:
            if not following.strip():
                continue
            if len(following) - len(following.lstrip()) <= len(indent):
                break
            listed.append(following.strip().strip("-'\" "))
        yield index, listed


def required_sdk(repo_root: Path) -> tuple[int, str] | None:
    """The major SDK version `global.json` demands, and the exact pin, or None.

    A lower major can never satisfy a pin regardless of `rollForward`, which only ever rolls
    *up* -- so comparing majors is sound without modelling the feature-band rules, and stays
    free of the false positives a stricter comparison would invent.
    """
    try:
        data = json.loads((repo_root / "global.json").read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, ValueError:
        return None

    pinned = (data.get("sdk") or {}).get("version")
    if not isinstance(pinned, str):
        return None

    found = VERSION_MAJOR.match(pinned)
    return (int(found.group(1)), pinned) if found else None


def check_ci_toolchain(path: Path, lines: list[str], repo_root: Path) -> Iterable[Violation]:
    """Flag a workflow installing an SDK older than the repo's own `global.json` pin.

    THE FAILURE THIS CATCHES IS INVISIBLE WHILE IT WORKS. WAPPIT's release-desktop.yml
    installed `8.0.x` while global.json required `9.0.313`; the build succeeded anyway,
    because the GitHub runner image happens to preinstall a 9.x SDK that satisfied the pin.
    Nothing in the workflow said so, and the day the runner image drops that SDK the build
    fails with a message about global.json that points nowhere near this line.
    """
    required = required_sdk(repo_root)
    if required is None:
        return

    major, pinned = required
    for index, versions in dotnet_versions_declared(lines):
        majors = [int(found.group(1)) for found in (VERSION_MAJOR.match(version) for version in versions) if found]
        if not majors or any(declared >= major for declared in majors):
            continue
        if line_exemption_reason(lines, index, TOOLCHAIN_RULE):
            continue

        listed = ", ".join(versions)
        yield Violation(
            path=path,
            line=index + 1,
            rule=TOOLCHAIN_RULE,
            message=(
                f"this job installs .NET {listed}, but global.json pins the SDK to {pinned}, "
                f"so nothing below {major}.0 can satisfy it -- rollForward only ever rolls "
                f"up. If it builds today it is relying on an SDK the runner image happens to "
                f"preinstall. Install {major}.0.x here; to keep an older SDK as well, list "
                f"both under a block scalar."
            ),
        )


# ---- tier 4: the runtime floor -------------------------------------------------------------


def check_dotnet_runtime_support(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a project targeting a .NET major below the estate floor.

    Reads `<TargetFramework>` and `<TargetFrameworks>` alike. A multi-target project is judged
    on its OLDEST .NET target, because that is the one that decides what has to keep working --
    a library shipping `net8.0;net10.0` is still shipping a net8.0 asset after net8.0 stops
    getting fixes, and judging it on its newest target would call that clean.

    Non-.NET targets in the list (`netstandard2.0`) are skipped rather than failing the whole
    declaration, so a project that multi-targets netstandard for compatibility and one modern
    runtime is judged only on the runtime.
    """
    for index, line in enumerate(lines):
        found = TARGET_FRAMEWORK.search(line)
        if not found:
            continue

        majors = [
            int(matched.group(1))
            for matched in (DOTNET_TFM.match(target.strip()) for target in found.group(1).split(";"))
            if matched
        ]
        if not majors:
            continue

        oldest = min(majors)
        if oldest >= DOTNET_FLOOR:
            continue

        if line_exemption_reason(lines, index, RUNTIME_RULE):
            continue

        ended = DOTNET_END_OF_SUPPORT.get(oldest)
        deadline = (
            f".NET {oldest} stops receiving security fixes on {ended}" if ended else f".NET {oldest} is out of support"
        )
        yield Violation(
            path=path,
            line=index + 1,
            rule=RUNTIME_RULE,
            message=(
                f"targets net{oldest}.0, below the estate floor of net{DOTNET_FLOOR}.0. "
                f"{deadline}, and .NET {DOTNET_FLOOR} is the current LTS (to 2028-11-14) -- "
                f"note .NET 8 and 9 expire on the same day, so there is no older release to "
                f"step back to. Retarget to net{DOTNET_FLOOR}.0, remembering the base images "
                f"and the CI SDK. If this project genuinely cannot move -- a dependency with "
                f"no build for the new major is the usual reason -- exempt the line with a "
                f"written reason naming the blocker and what would unblock it."
            ),
        )

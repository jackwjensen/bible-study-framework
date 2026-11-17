#!/usr/bin/env python3
"""The Node floor, and the readers the consistency engine judges Node with.

Rules here: node-support -- every Node major a repo declares is at or above NODE_FLOOR.
Paired with node-consistency, produced from these same readers by
standards_toolchain_consistency.py.

MOVED OUT OF standards_versions.py on 2026-08-25, for two reasons at once. That module was at
470 of the pack's own 500 lines, and the Node parsing had to become a *reader* -- a function
yielding `Declared` -- before the consistency engine could see it. Inlined inside
`check_node_runtime_support`, the parsing was reachable only by the floor.

THE OVERCLAIM THIS MODULE EXISTS TO MAKE TRUE. The floor's message has always said:

    MOVE EVERY PIN IN THE REPO TOGETHER ... a repo that moves only one builds on a different
    Node than it develops on, which is the failure this floor exists to make impossible.

It did not make it impossible. Verified by running the real scanner on 2026-08-25: `.nvmrc`
saying 24 beside `FROM node:26-alpine` was clean, exit 0, and so was `.nvmrc` 24 beside a CI
`node-version: 26`. A floor makes being BELOW it impossible; being SPLIT above it was silent in
every repo. The promise is now kept by `node-consistency` rather than by the sentence, and the
sentence has been corrected to point at the rule that actually enforces it.

WHAT IT READS, and why these three:

  .nvmrc                        the repo's own pin, which outranks everything else
  node-version: in CI           what the workflow installs
  FROM node:N in a Dockerfile   what the image is built on

`package.json` engines.node is deliberately NOT read. It is conventionally a RANGE (">=20",
"^22 || ^24"), and unlike Python's `requires-python` -- which is the single declaration pip
enforces, and so worth parsing properly -- engines.node is advisory, unenforced by npm by
default, and set by no repo here. When one sets it, it wants a reader with real range semantics
rather than a wrong answer from this one.

Source of truth: engineering-standards/engineering_standards/standards_node_support.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import Violation, today_iso
from standards_declarations import Declared, Toolchain, Version
from standards_exemptions import line_exemption_reason
from standards_scope import DOCKERFILE_FILENAME, NVMRC_FILENAME, looks_like_workflow
from standards_versions import NODE_FLOOR

NODE_RULE = "node-support"
NODE_CONSISTENCY_RULE = "node-consistency"

# Node's two dates per major, from https://nodejs.org/en/about/previous-releases (the release
# schedule), read 2026-09-05. HERE RATHER THAN IN standards_versions.py for the same reason
# PYTHON_BUGFIX_END sits beside the Python rule: it is the rule's EVIDENCE, quoted in the
# finding, not a knob anybody turns when lifting the floor.
#
# WHY A TABLE AND NOT A SENTENCE. The finding used to say "24 is the active LTS; 22 is in
# maintenance and 20 is end of life" -- prose written on 2026-08-25 that nobody would re-read
# when 24 entered maintenance on 2026-10-27, and that the B3D pack found stale within a week
# when its floor moved. A lookup against the clock cannot go stale the same way, and a test can
# pin every tense of it. Copied from B3D (ae4528b) on 2026-09-05.
NODE_MAINTENANCE_START: dict[int, str] = {
    18: "2023-10-18",
    20: "2024-10-22",
    22: "2025-10-28",
    24: "2026-10-27",
}
NODE_END_OF_LIFE: dict[int, str] = {
    18: "2025-04-30",
    20: "2026-04-30",
    22: "2027-04-30",
    24: "2028-04-30",
}


def node_support_status(major: int, today: str) -> str:
    """A clause describing where this major sits as of `today`, or "" when the table does not
    know it. Takes `today` rather than reading the clock, so the sentence a developer sees and
    the sentence a test asserts are produced by the same code path."""
    end = NODE_END_OF_LIFE.get(major)
    maintenance = NODE_MAINTENANCE_START.get(major)
    if end is None:
        return ""
    if today >= end:
        return f"Node {major} reached end of life on {end} and receives no patches at all"
    if maintenance and today >= maintenance:
        return f"Node {major} is in maintenance until {end} -- critical fixes only"
    return f"Node {major} is in active support until {end}"


# `20`, `v20`, `20.11.1`, `'22'`, `"22"` -- every shape these three sources use. The major is
# group 1 and everything after it is ignored, because the floor has no opinion on the minor and
# neither does agreement: Node's breaking changes land on majors.
NVMRC_VERSION = re.compile(r"^\s*v?(\d+)(?:\.\d+)*\s*$")
NODE_VERSION_KEY = re.compile(r"""node-version\s*:\s*['"]?v?(\d+)(?:\.\d+)*['"]?""", re.IGNORECASE)

# Registry, path and build-argument prefixes tolerated for exactly the reason the Python rule
# documents: `FROM ${CACHE}node:24-alpine` is the same image, and an anchored `FROM node:` misses
# it silently. The prefix must end in `/` or `}` so `FROM mynode:24` is not mistaken for the
# official image.
DOCKER_NODE_IMAGE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(?:\S*[/}])?node:(\S+)", re.IGNORECASE)
DOCKER_ARG = re.compile(r"^\s*(?:ARG|ENV)\s+([A-Za-z_]\w*)\s*[=\s]\s*(\S+)", re.IGNORECASE)

# An unresolved expression: the value is decided somewhere this module is not looking.
EXPRESSION = re.compile(r"\$\{\{.*?\}\}|\$\{?\w+\}?")

# A bare major, with anything after it ignored: `24`, `24.3`, `24-alpine`, `24.3.0-slim`.
MAJOR_TAG = re.compile(r"^v?(\d+)(?:[.\w-]*)$")


def spell(version: Version) -> str:
    return str(version[0])


def tag_versions(text: str) -> list[Version] | None:
    """A Node version from an image tag or a pin, as a one-element (major,) tuple.

    None means unreadable-here (an expression). An EMPTY list means a declaration naming
    nothing comparable -- `node:latest`, `node:lts` -- which is the floor rule's finding, not a
    pass: an alias floats onto whatever is current at build time, so two builds of one commit
    can run different majors.
    """
    if EXPRESSION.search(text):
        return None
    found = MAJOR_TAG.match(text.strip().strip("'\""))
    return [(int(found.group(1)),)] if found else []


def _nvmrc_declarations(lines: list[str]) -> Iterable[Declared]:
    for index, line in enumerate(lines):
        found = NVMRC_VERSION.match(line)
        if found:
            yield Declared(index, "`.nvmrc` pins Node", [(int(found.group(1)),)], ".nvmrc")


def _dockerfile_declarations(lines: list[str]) -> Iterable[Declared]:
    """Resolves a version hoisted into a build ARG, so the FROM line still gets judged."""
    arguments: dict[str, tuple[str, int]] = {}
    for index, line in enumerate(lines):
        argument = DOCKER_ARG.match(line)
        if argument:
            arguments[argument.group(1)] = (argument.group(2).strip("'\""), index)

    for index, line in enumerate(lines):
        image = DOCKER_NODE_IMAGE.match(line)
        if not image:
            continue
        tag, at = image.group(1), index
        for name, (value, declared_at) in arguments.items():
            for spelling in (f"${{{name}}}", f"${name}"):
                if spelling in tag:
                    tag, at = tag.replace(spelling, value), declared_at
        versions = tag_versions(tag)
        if versions is not None:
            yield Declared(at, "this image is built on Node", versions, "Dockerfile")


def _workflow_declarations(lines: list[str]) -> Iterable[Declared]:
    for index, line in enumerate(lines):
        # Anchored on the KEY rather than on any digit, so `uses: actions/setup-node@v7` is not
        # read as Node 7 -- which would put a finding on every workflow in the estate.
        found = NODE_VERSION_KEY.search(line)
        if found:
            yield Declared(index, "CI installs Node", [(int(found.group(1)),)], "CI")


def declarations_in(path: Path, lines: list[str]) -> Iterable[Declared]:
    """Every Node version this file declares, using the reader its filename calls for.

    SHARED BY BOTH RULES ON PURPOSE. Two readers would let "is it above the floor?" and "do
    they agree?" disagree about what a file even says, and the second question would then
    quietly pass a repo the first one failed.
    """
    if NVMRC_FILENAME.match(path.name):
        return _nvmrc_declarations(lines)
    if DOCKERFILE_FILENAME.match(path.name):
        return _dockerfile_declarations(lines)
    # NOT a fall-through: a reader that assumed every other file was a workflow read test
    # fixtures and documentation samples as real declarations once the consistency engine began
    # walking every in-scope path. See looks_like_workflow.
    if looks_like_workflow(path):
        return _workflow_declarations(lines)
    return ()


def check_node_runtime_support(path: Path, lines: list[str], today: str | None = None) -> Iterable[Violation]:
    """Flag a Node version below the estate floor, wherever the repo declares one.

    Three sources, one floor. Which source a line came from changes only the wording of the
    finding -- the `.nvmrc` wording says outranks-everything-else, because that is the file
    that decides what a developer's shell and CI both end up using.

    Line-exemptable like every other floor: a repo genuinely pinned by a dependency with no
    build for the floor says so in writing, on the line, and the scanner prints the reason.

    The support standing of both the offending major and the floor comes from the date table
    (see node_support_status), never from a sentence. `today` is injectable so a test can pin
    the tense; the dispatcher passes nothing and gets the clock.
    """
    today = today or today_iso()
    floor_standing = node_support_status(NODE_FLOOR, today)
    for declaration in declarations_in(path, lines):
        # A tag naming no major (`node:latest`) is judged here, where "cannot be shown to clear
        # the floor" is the finding. The consistency rule skips it, so it is reported once.
        oldest = min(declaration.versions) if declaration.versions else None
        if oldest is not None and oldest >= (NODE_FLOOR,):
            continue
        if line_exemption_reason(lines, declaration.index, NODE_RULE):
            continue

        if oldest is None:
            problem = (
                f"{declaration.source} a version this rule cannot compare -- `latest`, `lts` or "
                f"an alias floats onto whatever is current at build time, so two builds of one "
                f"commit can run different majors and nothing shows this clears Node "
                f"{NODE_FLOOR}"
            )
        else:
            standing = "; ".join(clause for clause in (node_support_status(oldest[0], today), floor_standing) if clause)
            problem = f"{declaration.source} {oldest[0]}, below the estate floor of Node {NODE_FLOOR}. {standing}"

        yield Violation(
            path=path,
            line=declaration.index + 1,
            rule=NODE_RULE,
            message=(
                f"{problem}. MOVE EVERY PIN IN THE REPO TOGETHER -- .nvmrc, every "
                f"`node-version:` in CI, and any `FROM node:` base image -- because they are "
                f"read by different tools and a repo that moves only one builds on a different "
                f"Node than it develops on. That split is enforced by `node-consistency`, not "
                f"by this floor: a floor only makes being BELOW it impossible, and two pins "
                f"that disagree while both clearing it were silent here until 2026-08-25. "
                f"If something genuinely pins this repo below {NODE_FLOOR}, exempt the line "
                f"with a written reason naming the blocker and what would unblock it."
            ),
        )


NODE_TOOLCHAIN = Toolchain(
    name="Node",
    rule=NODE_CONSISTENCY_RULE,
    noun="runtime",
    readers=declarations_in,
    spell=spell,
    # None, and not a gap to fill later: a .nvmrc, a `node-version:` and an image tag are all
    # exact. The one range Node has -- package.json engines.node -- is deliberately unread.
    admits=None,
)

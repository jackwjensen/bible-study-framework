#!/usr/bin/env python3
"""The .NET runtime a container is built on, and whether it matches what the projects target.

Produces: dotnet-consistency, via standards_toolchain_consistency.py.

THE GAP THIS CLOSES. Audited on 2026-08-25 by grepping every rule module for
`mcr.microsoft.com`: nothing matched. `runtime-support` reads `<TargetFramework>` and that is
all it reads, and a Dockerfile was dispatched to exactly three rules -- container-user, the Node
floor, the Python floor. So a repo targeting net10.0 and building `FROM
mcr.microsoft.com/dotnet/aspnet:8.0` produced no finding of any kind.

WHY THE DIRECTION MATTERS, and why this is worth a rule rather than a note. A base image OLDER
than the TFM fails loudly at startup -- the app simply will not run, so that direction is
self-correcting and nobody ships it twice. A base image NEWER than the TFM works SILENTLY, via
roll-forward: `aspnet:10.0` will happily host a net8.0 app, so production runs a runtime the
build never targeted and the tests never exercised. That is the exact shape of "a bug that
tests as correct", in the toolchain with the most repos in this estate.

WHAT IT DELIBERATELY DOES NOT READ:

  sdk images      `mcr.microsoft.com/dotnet/sdk:N.M` is a BUILD tool, and an SDK ahead of the
                  TFM is normal and supported -- SDK 11 building net10.0 is not a defect.
                  Reading it as an environment would invent findings on correct multi-stage
                  Dockerfiles. SDK versions are governed by global.json and `ci-toolchain`.
  global.json     for the same reason, plus `ci-toolchain` already compares CI against it. Two
                  rules reporting one cause is how a rule becomes something people exempt.

So this rule compares exactly two kinds of site -- the TFM and the runtime image -- which is
precisely the gap the audit found and nothing wider.

Source of truth: engineering-standards/engineering_standards/standards_dotnet_images.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_declarations import Declared, Toolchain, Version
from standards_scope import DOCKERFILE_FILENAME, PROJECT_FILENAME
from standards_versions import DOTNET_TFM, TARGET_FRAMEWORK

DOTNET_CONSISTENCY_RULE = "dotnet-consistency"

# `mcr.microsoft.com/dotnet/aspnet:10.0`, `.../runtime:10.0-alpine`, and the same tolerance for a
# registry, path or build-argument prefix that every other image reader here has. `aspnet-deps`
# and the chiselled variants spell the repository with a suffix, so the name is matched up to the
# colon rather than exactly.
DOTNET_RUNTIME_IMAGE = re.compile(
    r"^\s*FROM\s+(?:--\S+\s+)*(?:\S*[/}])?(?:aspnet|runtime)(?:-[\w.-]+)?:(\S+)", re.IGNORECASE
)
DOCKER_ARG = re.compile(r"^\s*(?:ARG|ENV)\s+([A-Za-z_]\w*)\s*[=\s]\s*(\S+)", re.IGNORECASE)
EXPRESSION = re.compile(r"\$\{\{.*?\}\}|\$\{?\w+\}?")

# A tag's major: `10.0`, `10.0-alpine`, `10.0.2-noble-chiseled`. Majors only, because that is
# what a TFM names -- net10.0 has no minor to compare against.
IMAGE_MAJOR = re.compile(r"^v?(\d+)(?:[.\w-]*)$")


def spell(version: Version) -> str:
    return f"net{version[0]}.0"


def image_versions(text: str) -> list[Version] | None:
    """A .NET major from an image tag, or None when the tag defers to something unreadable."""
    if EXPRESSION.search(text):
        return None
    found = IMAGE_MAJOR.match(text.strip().strip("'\""))
    return [(int(found.group(1)),)] if found else []


def _project_declarations(lines: list[str]) -> Iterable[Declared]:
    for index, line in enumerate(lines):
        found = TARGET_FRAMEWORK.search(line)
        if not found:
            continue
        majors = [
            (int(matched.group(1)),)
            for matched in (DOTNET_TFM.match(target.strip()) for target in found.group(1).split(";"))
            if matched
        ]
        # A project multi-targeting net8.0;net10.0 is a library shipping both, which the
        # engine treats like a test matrix: no vote on what the repo runs, but it must
        # cover what does. `netstandard2.0` yields nothing here and is correctly ignored.
        if majors:
            yield Declared(index, "this project targets", majors, "TargetFramework")


def _image_declarations(lines: list[str]) -> Iterable[Declared]:
    """Resolves a version hoisted into a build ARG, so the FROM line still gets judged."""
    arguments: dict[str, tuple[str, int]] = {}
    for index, line in enumerate(lines):
        found = DOCKER_ARG.match(line)
        if found:
            arguments[found.group(1)] = (found.group(2).strip("'\""), index)

    for index, line in enumerate(lines):
        image = DOTNET_RUNTIME_IMAGE.match(line)
        if not image:
            continue
        tag, at = image.group(1), index
        for name, (value, declared_at) in arguments.items():
            for spelling in (f"${{{name}}}", f"${name}"):
                if spelling in tag:
                    tag, at = tag.replace(spelling, value), declared_at
        versions = image_versions(tag)
        if versions:
            yield Declared(at, "this image runs on", versions, "base image")


def declarations_in(path: Path, lines: list[str]) -> Iterable[Declared]:
    """The .NET version this file declares: a TFM in a project, or a runtime image tag."""
    if PROJECT_FILENAME.match(path.name):
        return _project_declarations(lines)
    if DOCKERFILE_FILENAME.match(path.name):
        return _image_declarations(lines)
    return ()


DOTNET_TOOLCHAIN = Toolchain(
    name=".NET",
    rule=DOTNET_CONSISTENCY_RULE,
    noun="runtime",
    readers=declarations_in,
    spell=spell,
    # None: a TFM and an image tag are both exact. .NET's one range-like mechanism is
    # global.json rollForward, which governs the SDK -- deliberately out of scope above.
    admits=None,
)

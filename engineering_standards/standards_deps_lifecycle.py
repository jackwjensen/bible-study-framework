#!/usr/bin/env python3
"""How long a dependency's release line is supported, from endoflife.date.

Decision 14 of the 2026-10-02 plan (Jack): *"A major upgrade is only mandatory if the support of
current major version is less than one year. If the support is longer ... it is just a
notification that the new major version exists."* Which date counts is endoflife.date's published
end of support (Jack, same day) -- the one source the gate reads, rather than a date per vendor.

A LINE is endoflife.date's *cycle*: MySQL `8.4`, .NET `10`, Node `24`, Python `3.14`. A version
belongs to the cycle whose numbers prefix its own (`8.4.11` -> `8.4`, `10.0.401` -> `10`). Only
products the estate actually runs are mapped -- the runtimes, databases and base images, where a
line move is expensive. An unmapped dependency has no lifecycle, and the verdict falls back to
the package rule (standards_deps_lines).

EF Core has no endoflife.date entry of its own and is mapped to .NET: an EF Core major tracks
the .NET major, the same assumption the pack's EF_CORE_FLOOR = DOTNET_FLOOR already makes.

Source of truth: engineering-standards/engineering_standards/standards_deps_lifecycle.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from standards_deps_cache import cached_lookup, store_lookup
from standards_deps_declared import DOCKER, DOTNET_SDK, NUGET, RUNNER, Dependency, official_image

# standards: const-environment-literal exempt -- endoflife.date's published API, identical in every environment; the source Jack chose for support dates (2026-10-02)
LIFECYCLE_API = "https://endoflife.date/api/v1/products/"

# Docker image (the last path segment for Hub images) -> endoflife.date product.
DOCKER_PRODUCTS = {
    "mysql": "mysql",
    "mariadb": "mariadb",
    "postgres": "postgresql",
    "redis": "redis",
    "valkey": "valkey",
    "nginx": "nginx",
    "node": "nodejs",
    "python": "python",
    "php": "php",
    "ubuntu": "ubuntu",
    "debian": "debian",
}
# GitHub-hosted runner family -> endoflife.date product. Each names its labels by that product's
# cycle (`ubuntu-26.04`, `windows-2025`, `macos-15`), so a label's version IS the cycle name.
RUNNER_PRODUCTS = {
    "ubuntu": "ubuntu",
    "windows": "windows-server",
    "macos": "macos",
}
DOTNET_PACKAGE_PREFIXES = ("microsoft.aspnetcore.", "microsoft.entityframeworkcore", "microsoft.extensions.")
DOTNET_TOOLS = frozenset({"dotnet-ef"})


@dataclass(frozen=True)
class Cycle:
    """One release line and when its support ends (None: not announced)."""

    name: str
    is_lts: bool
    end: date | None


def product_for(dependency: Dependency) -> str | None:
    """The endoflife.date product a dependency's line is judged against, or None."""
    name = dependency.name.lower()
    if dependency.ecosystem == RUNNER:
        return RUNNER_PRODUCTS.get(name)
    if dependency.ecosystem == DOCKER:
        return _docker_product(name)
    if dependency.ecosystem == DOTNET_SDK:
        return "dotnet"
    if dependency.ecosystem == NUGET and (name.startswith(DOTNET_PACKAGE_PREFIXES) or name in DOTNET_TOOLS):
        return "dotnet"
    return None


def _docker_product(name: str) -> str | None:
    if name.startswith("mcr.microsoft.com/dotnet/"):
        return "dotnet"
    if name.startswith("mcr.microsoft.com/mssql/"):
        return "mssqlserver"
    image = official_image(name)
    # a vendor's own `org/name` is not the product
    return DOCKER_PRODUCTS.get(image) if image is not None else None


def _numbers(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in text.removeprefix("v").split("."):
        digits = "".join(character for character in piece if character.isdigit())
        if not digits or digits != piece:
            break
        parts.append(int(digits))
    return tuple(parts)


def cycle_of(version: str, cycles: list[Cycle]) -> Cycle | None:
    """The line a version belongs to: the cycle whose numbers prefix the version's own."""
    numbers = _numbers(version)
    matching = [cycle for cycle in cycles if numbers[: len(_numbers(cycle.name))] == _numbers(cycle.name)]
    return max(matching, key=lambda cycle: len(_numbers(cycle.name))) if matching else None


def _parse(payload: dict) -> list[Cycle]:
    cycles = []
    for release in payload["result"]["releases"]:
        end = release.get("eolFrom")
        cycles.append(Cycle(str(release["name"]), bool(release.get("isLts")), date.fromisoformat(end) if end else None))
    return cycles


def lifecycle(product: str, fetch) -> list[Cycle]:
    """Every cycle of a product, through the one-hour machine cache. Raises on failure: an
    unreachable lifecycle must be said, never quietly turned into "no lifecycle"."""
    key = f"lifecycle:{product}"
    cached = cached_lookup(key)
    if cached and "cycles" in cached:
        return [Cycle(name, lts, date.fromisoformat(end) if end else None) for name, lts, end in cached["cycles"]]
    cycles = _parse(json.loads(fetch(LIFECYCLE_API + product, {})))
    store_lookup(
        key, {"cycles": [[cycle.name, cycle.is_lts, cycle.end.isoformat() if cycle.end else None] for cycle in cycles]}
    )
    return cycles

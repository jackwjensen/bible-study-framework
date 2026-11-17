#!/usr/bin/env python3
"""What each registry publishes for a declared dependency: its stable releases, and when.

THE PACK'S FIRST NETWORK CODE, so its boundaries are deliberate:

* Every request goes through one `Fetch` callable. Production passes `http_fetch`; every test
  passes a dictionary of canned responses, so no test ever touches the network.
* Only https, only GET, a short timeout, and only the registries named below. An unknown
  registry answers "unsupported" -- a named, printed state -- rather than a guess.
* A failure is an ANSWER, not an exception: `Answer.error` says what went wrong, and the caller
  decides what an unreachable registry means (warn on a developer machine, fail in CI).

RELEASES, NOT JUST "NEWEST". Decision 14 of the 2026-10-02 plan judges a dependency by its LINE
-- a newer patch in the pinned line blocks, a newer major is a notice until the pinned line's
support runs short -- so the gate needs every stable release, and for a package with no
published lifecycle, WHEN the pinned line last shipped. Where a registry's version list carries
no dates (NuGet's flat container) or no commits (GitHub releases are tags), the newest release
of each major is enriched with one extra request rather than every release.

"STABLE" MEANS NO PRERELEASE: a preview SDK, an `-rc` package or an `11.0.0-preview.1` tag is
never proposed. Docker tags are compared only within the pin's own SHAPE -- the same number of
numeric parts (at least the vendor's full release number, `release_parts`) and the same
suffix -- and an image's `lts` tag, where it publishes one, names the
preferred release (MySQL's `innovation` 26.7.0 is not 8.4's successor; `lts` 9.7.2 is).

A GitHub token is used when the environment provides one (`GITHUB_TOKEN`, as Actions sets it,
or `GH_TOKEN`); unauthenticated requests are limited to 60 an hour per IP.

Source of truth: engineering-standards/engineering_standards/standards_deps_registry.py
"""

from __future__ import annotations

import functools
import gzip
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from standards_deps_cache import cached_lookup, store_lookup
from standards_deps_declared import (
    ACTION,
    COMPOSER,
    DOCKER,
    DOTNET_SDK,
    NPM,
    NUGET,
    PYPI,
    RUNNER,
    RUNNER_VERSION,
    Dependency,
    release_parts,
)
from standards_deps_lifecycle import LIFECYCLE_API, RUNNER_PRODUCTS

Fetch = Callable[[str, dict[str, str]], bytes]
Release = tuple[str, str | None, str | None]  # version, ISO publish date, commit SHA (Actions only)

TIMEOUT_SECONDS = 15
DOCKER_HUB_PAGES = 5
# standards: const-environment-literal exempt -- Microsoft's published release-metadata endpoint, identical in every environment; when it moved (dotnetcli.blob -> builds.dotnet.microsoft.com) that was a code change either way
DOTNET_RELEASES_INDEX = "https://builds.dotnet.microsoft.com/dotnet/release-metadata/releases-index.json"
DOTNET_PREVIEW_PHASES = frozenset({"preview", "go-live"})
NUMERIC_TAG = re.compile(r"^v?(?P<numbers>\d+(?:\.\d+)*)(?P<suffix>.*)$")


@dataclass(frozen=True)
class Answer:
    """What a registry said: its stable releases, or an error. Never both."""

    releases: tuple[Release, ...] = ()
    preferred: str | None = None  # Docker: the release the image's `lts` tag points at
    homepage: str | None = None
    error: str | None = None
    # npm: (version, full ISO timestamp). A release's own date is a day, which the line logic
    # parses; a package manager's release-age guard counts minutes, so it needs the instant.
    published_at: tuple[tuple[str, str], ...] = ()
    # npm: versions the maintainer deprecated -- withdrawn, like PyPI's yanked files (which
    # `_pypi` already leaves out), so never a release to move to.
    withdrawn: tuple[str, ...] = ()

    @property
    def newest(self) -> Release | None:
        """The release to propose as the newest line: the LTS one where the image names it."""
        if self.preferred:
            return next((release for release in self.releases if release[0] == self.preferred), None)
        return max(self.releases, key=lambda release: version_key(release[0]), default=None)


class NotFound(Exception):
    """The registry answered, and the package is not in it."""


class Unsupported(Exception):
    """A registry this module does not know how to ask. Its own type, never `LookupError`:
    `KeyError` IS a `LookupError`, so catching that would file a malformed response under
    "unsupported" and hide it."""


def http_fetch(url: str, headers: dict[str, str]) -> bytes:
    """GET one https URL. The only place the pack opens a network connection."""
    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https registry URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "engineering-standards", **headers})  # noqa: S310  (https enforced above)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310  (https enforced above)
            body = response.read()
            # NuGet's SemVer 2 registration is served gzip-compressed whatever was asked for, and
            # urllib never decompresses -- so the gzip magic is checked, not only the header.
            if response.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            return body
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise NotFound(url) from error
        raise


def _json(fetch: Fetch, url: str, headers: dict[str, str] | None = None) -> dict:
    return json.loads(fetch(url, headers or {}))


def is_prerelease(version: str) -> bool:
    """Anything beyond digits and dots (after an optional `v`) marks a prerelease or a build."""
    return not re.fullmatch(r"v?\d+(?:\.\d+)*", version)


def version_key(version: str) -> tuple[int, ...]:
    found = NUMERIC_TAG.match(version)
    return tuple(int(part) for part in found["numbers"].split(".")) if found else ()


def newest_stable(versions: list[str]) -> str | None:
    stable = [version for version in versions if not is_prerelease(version)]
    return max(stable, key=version_key) if stable else None


def semver_line(version: str) -> tuple[int, ...]:
    """A package's line where it publishes no lifecycle: its major, or `0.minor` below 1.0."""
    numbers = version_key(version)
    return numbers[:2] if numbers[:1] == (0,) else numbers[:1]


def _newest_per_line(versions: list[str]) -> list[str]:
    best: dict[tuple[int, ...], str] = {}
    for version in versions:
        line = semver_line(version)
        if line not in best or version_key(version) > version_key(best[line]):
            best[line] = version
    return list(best.values())


def _day(timestamp: str | None) -> str | None:
    return timestamp[:10] if timestamp else None


def _nuget(dependency: Dependency, fetch: Fetch) -> Answer:
    package = dependency.name.lower()
    versions = [
        v
        for v in _json(fetch, f"https://api.nuget.org/v3-flatcontainer/{package}/index.json")["versions"]
        if not is_prerelease(v)
    ]
    dated = {}
    for version in _newest_per_line(versions):  # one leaf per line: the flat container carries no dates
        # The SemVer 2 hive: `registration5-semver1` silently omits any package using SemVer 2
        # features (MySql.EntityFrameworkCore was "not found" there). A missing leaf costs only the
        # date -- which the package rule needs -- never the whole lookup.
        try:
            leaf = _json(fetch, f"https://api.nuget.org/v3/registration5-gz-semver2/{package}/{version.lower()}.json")
            dated[version] = _day(leaf.get("published"))
        except NotFound:
            dated[version] = None  # undated: the package rule then treats the line as still shipping
    return Answer(
        releases=tuple((version, dated.get(version), None) for version in versions),
        homepage=f"https://www.nuget.org/packages/{dependency.name}",
    )


def _pypi(dependency: Dependency, fetch: Fetch) -> Answer:
    data = _json(fetch, f"https://pypi.org/pypi/{dependency.name}/json")
    releases = []
    for version, files in data.get("releases", {}).items():
        live = [file for file in files if not file.get("yanked")]
        if live and not is_prerelease(version):
            releases.append((version, _day(min(file["upload_time_iso_8601"] for file in live)), None))
    urls = data["info"].get("project_urls") or {}
    homepage = next(
        (url for label, url in urls.items() if "change" in label.lower() or "release" in label.lower()), None
    )
    return Answer(releases=tuple(releases), homepage=homepage or data["info"].get("project_url"))


def _npm(dependency: Dependency, fetch: Fetch) -> Answer:
    document = _json(fetch, f"https://registry.npmjs.org/{urllib.parse.quote(dependency.name, safe='@')}")
    times = document.get("time", {})
    versions = {version: entry for version, entry in document.get("versions", {}).items() if not is_prerelease(version)}
    return Answer(
        releases=tuple((version, _day(times.get(version)), None) for version in versions),
        homepage=document.get("homepage"),
        # Parsed here so a malformed instant fails the lookup out loud (`lookup` reports the
        # ValueError) instead of being skipped later; Python 3.11 reads npm's trailing `Z`.
        published_at=tuple(
            (version, datetime.fromisoformat(times[version]).isoformat()) for version in versions if times.get(version)
        ),
        withdrawn=tuple(
            version for version, entry in versions.items() if isinstance(entry, dict) and entry.get("deprecated")
        ),
    )


def _composer(dependency: Dependency, fetch: Fetch) -> Answer:
    entries = _json(fetch, f"https://repo.packagist.org/p2/{dependency.name}.json")["packages"][dependency.name]
    releases = []
    for entry in entries:
        version = entry["version"].removeprefix("v")
        if not is_prerelease(version):
            releases.append((version, _day(entry.get("time")), None))
    return Answer(releases=tuple(releases), homepage=f"https://packagist.org/packages/{dependency.name}")


def _shape(tag: str) -> tuple[int, str] | None:
    found = NUMERIC_TAG.match(tag)
    return (found["numbers"].count(".") + 1, found["suffix"]) if found else None


def _docker_tags(image: str, fetch: Fetch) -> dict[str, tuple[str | None, str | None]]:
    """Every tag of an image -> (digest, last updated), where the registry says (Hub does; MCR's
    tag list carries neither)."""
    if image.startswith("mcr.microsoft.com/"):
        repository = image.removeprefix("mcr.microsoft.com/")
        names = _json(fetch, f"https://mcr.microsoft.com/v2/{repository}/tags/list").get("tags") or []
        return dict.fromkeys(names, (None, None))
    first = image.split("/", 1)[0]
    if "/" in image and ("." in first or ":" in first) and first != "docker.io":
        raise Unsupported(f"registry {first} is not supported")
    path = image.removeprefix("docker.io/")
    namespace, repository = path.split("/", 1) if "/" in path else ("library", path)
    url = f"https://hub.docker.com/v2/namespaces/{namespace}/repositories/{repository}/tags?page_size=100&ordering=last_updated"
    tags: dict[str, tuple[str | None, str | None]] = {}
    for _ in range(DOCKER_HUB_PAGES):
        page = _json(fetch, url)
        tags.update(
            (result["name"], (result.get("digest"), _day(result.get("last_updated"))))
            for result in page.get("results", [])
        )
        url = page.get("next")
        if not url:
            break
    return tags


def _lts_release(tags: dict[str, tuple[str | None, str | None]], candidates: list[str], suffix: str) -> str | None:
    """The release an image's `lts` tag points at, matched by digest because `lts` is a moving
    alias. A suffixed pin follows its own alias (`24.1.0-alpine` follows `lts-alpine`)."""
    digest = tags.get(f"lts{suffix}", (None, None))[0]
    matching = [tag for tag in candidates if digest and tags[tag][0] == digest]
    return max(matching, key=version_key) if matching else None


def _docker(dependency: Dependency, fetch: Fetch) -> Answer:
    if dependency.version.startswith("sha256:"):
        return Answer(error="pinned by digest; compare the digest against the registry by hand")
    declared = _shape(dependency.version)
    if declared is None:
        return Answer(error=f"tag `{dependency.version}` is not a version number")
    # A floating pin is compared with full release numbers -- three parts, or two for a vendor
    # that numbers its releases that way (`postgres:16-alpine` -> `16.10-alpine`).
    count, suffix = max(declared[0], release_parts(DOCKER, dependency.name)), declared[1]
    tags = _docker_tags(dependency.name, fetch)
    candidates = [tag for tag in tags if _shape(tag) == (count, suffix)]
    if not candidates:
        return Answer(error=f"no `{'N.' * (count - 1)}N{suffix}` tags found")
    # Tags kept exactly as published, suffix included, so they compare with the pin as written.
    return Answer(
        releases=tuple((tag, tags[tag][1], None) for tag in candidates),
        preferred=_lts_release(tags, candidates, suffix),
    )


@functools.cache
def _github_token() -> str | None:
    """A token for GitHub's API: the environment's (CI passes `GITHUB_TOKEN`), else the GitHub
    CLI's own login where `gh` is installed. The CLI is the machine layer used as an EXTRA
    (self-contained-repo.md): without it lookups are merely unauthenticated -- 60 an hour per IP
    -- and a rate-limited one reports "unreachable", which warns locally rather than blocking.
    Never printed, never stored."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    gh = shutil.which("gh")
    if token or gh is None:
        return token
    completed = subprocess.run(  # noqa: S603  (fixed argv, resolved path, the user's own gh login; never echoed)
        [gh, "auth", "token"], capture_output=True, text=True, timeout=10, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 and completed.stdout.strip() else None


def _github_headers(accept: str) -> dict[str, str]:
    token = _github_token()
    headers = {"Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _action(dependency: Dependency, fetch: Fetch) -> Answer:
    base = f"https://api.github.com/repos/{dependency.name}"
    published = _json(fetch, f"{base}/releases?per_page=100", _github_headers("application/vnd.github+json"))
    releases = {
        release["tag_name"]: _day(release.get("published_at"))
        for release in published
        if not release.get("prerelease") and not release.get("draft") and not is_prerelease(release["tag_name"])
    }
    shas = {}
    for tag in _newest_per_line(list(releases)):  # a pin is a commit, so each line's newest needs one
        sha = fetch(f"{base}/commits/{urllib.parse.quote(tag)}", _github_headers("application/vnd.github.sha"))
        shas[tag] = sha.decode().strip()
    return Answer(
        releases=tuple((tag, day, shas.get(tag)) for tag, day in releases.items()),
        homepage=f"https://github.com/{dependency.name}/releases",
    )


def _runner(dependency: Dependency, fetch: Fetch) -> Answer:
    """A runner image's releases ARE its OS's cycles; there is no registry beside endoflife.date.

    Where the OS has LTS cycles (Ubuntu, Windows Server) only those are releases, because GitHub
    builds runner images for nothing else -- proposing `ubuntu-26.10` would name a label that does
    not exist. macOS publishes no LTS, and every numbered release gets an image.
    """
    product = RUNNER_PRODUCTS.get(dependency.name.lower())
    if product is None:
        raise Unsupported(f"runner family `{dependency.name}` has no published lifecycle")
    cycles = _json(fetch, LIFECYCLE_API + product)["result"]["releases"]
    numbered = [cycle for cycle in cycles if RUNNER_VERSION.match(str(cycle["name"]))]
    lts_only = any(cycle.get("isLts") for cycle in numbered)
    releases = tuple(
        (str(cycle["name"]), cycle.get("releaseDate"), None) for cycle in numbered if cycle.get("isLts") or not lts_only
    )
    return Answer(releases=releases, homepage="https://github.com/actions/runner-images")


def _dotnet_sdk(_dependency: Dependency, fetch: Fetch) -> Answer:
    channels = _json(fetch, DOTNET_RELEASES_INDEX)["releases-index"]
    releases = tuple(
        (entry["latest-sdk"], entry.get("latest-release-date"), None)
        for entry in channels
        if entry.get("support-phase") not in DOTNET_PREVIEW_PHASES and not is_prerelease(entry["latest-sdk"])
    )
    return Answer(releases=releases, homepage="https://dotnet.microsoft.com/download/dotnet")


LOOKUPS: dict[str, Callable[[Dependency, Fetch], Answer]] = {
    NUGET: _nuget,
    PYPI: _pypi,
    NPM: _npm,
    COMPOSER: _composer,
    DOCKER: _docker,
    ACTION: _action,
    DOTNET_SDK: _dotnet_sdk,
    RUNNER: _runner,
}


def _cache_key(dependency: Dependency) -> str:
    if dependency.ecosystem == DOCKER:
        return f"{dependency.key}#{_shape(dependency.version)}"
    return dependency.key


def lookup(dependency: Dependency, fetch: Fetch = http_fetch, use_cache: bool = True) -> Answer:
    """The registry's answer for one dependency, through the machine cache when allowed."""
    key = f"releases:{_cache_key(dependency)}"
    if use_cache:
        cached = cached_lookup(key)
        # An npm entry cached before publish instants were kept would let a release inside the
        # release-age guard through as installable; it is a miss rather than an hour of that.
        if cached and "releases" in cached and ("published_at" in cached or dependency.ecosystem != NPM):
            return Answer(
                tuple(tuple(release) for release in cached["releases"]),
                cached.get("preferred"),
                cached.get("homepage"),
                published_at=tuple(tuple(pair) for pair in cached.get("published_at", [])),
                withdrawn=tuple(cached.get("withdrawn", [])),
            )
    try:
        answer = LOOKUPS[dependency.ecosystem](dependency, fetch)
    except NotFound:
        return Answer(error="not found in its registry")
    except Unsupported as error:
        # standards: technical-error-shown exempt -- `Unsupported` is this module's own sentence,
        # written for the developer reading the deps report; no end user sees it.
        return Answer(error=str(error))
    except (OSError, ValueError, KeyError, TypeError) as error:
        # standards: technical-error-shown exempt -- read by the developer running the gate,
        # for whom the network or parse error IS the actionable detail; no end user sees it.
        return Answer(error=f"registry lookup failed: {error}")
    if answer.releases and use_cache:
        store_lookup(
            key,
            {
                "releases": [list(release) for release in answer.releases],
                "preferred": answer.preferred,
                "homepage": answer.homepage,
                "published_at": [list(pair) for pair in answer.published_at],
                "withdrawn": list(answer.withdrawn),
            },
        )
    if not answer.releases and not answer.error:
        return Answer(error="the registry lists no stable release")
    return answer

"""Cases for standards_deps_registry: what each registry publishes, without a network.

Every case passes a canned `fetch`, so the suite never opens a connection -- the pack's first
network code must not make its test suite depend on the network. What is pinned here is the part
the verdicts rest on: that only STABLE releases come back, with the dates and commits the line
rules need; that a Docker tag is only compared with tags of its own shape and an image's `lts`
tag names the preferred release; that a failure is an answer rather than an exception; and that
a failure is never cached.

Run: python test_standards_deps_registry.py   (or pytest)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_deps_cache import CACHE_ENVIRONMENT  # noqa: E402
from standards_deps_declared import ACTION, COMPOSER, DOCKER, DOTNET_SDK, NPM, NUGET, PYPI, Dependency  # noqa: E402
from standards_deps_registry import (  # noqa: E402
    DOTNET_RELEASES_INDEX,
    NotFound,
    is_prerelease,
    lookup,
    newest_stable,
    semver_line,
)
from standards_selftest import run_module_tests  # noqa: E402

SHA = "a" * 40
HUB_MYSQL = "https://hub.docker.com/v2/namespaces/library/repositories/mysql/tags?page_size=100&ordering=last_updated"


def dependency(ecosystem: str, name: str, version: str) -> Dependency:
    return Dependency(ecosystem, name, version, Path("manifest"), 1)


class Registry:
    """A canned registry: URL -> response body. Records every URL asked, refuses the unknown."""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.asked: list[str] = []

    def __call__(self, url: str, _headers: dict[str, str]) -> bytes:
        self.asked.append(url)
        if url not in self.responses:
            raise NotFound(url)
        body = self.responses[url]
        if isinstance(body, Exception):
            raise body
        return body if isinstance(body, bytes) else json.dumps(body).encode()


@contextmanager
def isolated_cache() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tree:
        path = Path(tree) / "cache.json"
        previous = os.environ.get(CACHE_ENVIRONMENT)
        os.environ[CACHE_ENVIRONMENT] = str(path)
        try:
            yield path
        finally:
            if previous is None:
                os.environ.pop(CACHE_ENVIRONMENT, None)
            else:
                os.environ[CACHE_ENVIRONMENT] = previous


def versions(answer) -> set[str]:
    return {release[0] for release in answer.releases}


def test_newest_means_newest_stable() -> None:
    assert newest_stable(["9.0.0", "10.0.0-rc.1", "9.1.0", "9.10.0"]) == "9.10.0"
    assert is_prerelease("10.0.0-preview.1")
    assert is_prerelease("1.0.0rc1")
    assert not is_prerelease("v1.2.3")


def test_a_line_is_the_major_or_zero_minor() -> None:
    assert semver_line("10.35.0.4138") == (10,)
    assert semver_line("v7.0.1") == (7,)
    assert semver_line("0.16.10") == (0, 16), "below 1.0 every minor is a breaking release"


def test_nuget_lists_stable_releases_and_dates_each_line_s_newest() -> None:
    base = "https://api.nuget.org/v3/registration5-gz-semver2/stripe.net"
    registry = Registry(
        {
            "https://api.nuget.org/v3-flatcontainer/stripe.net/index.json": {
                "versions": ["52.0.0", "52.3.0", "53.0.0", "54.0.0-beta.1"]
            },
            f"{base}/52.3.0.json": {"published": "2026-03-01T10:00:00Z"},
            f"{base}/53.0.0.json": {"published": "2026-08-01T10:00:00Z"},
        }
    )
    with isolated_cache():
        answer = lookup(dependency(NUGET, "Stripe.net", "52.0.0"), registry)
    assert versions(answer) == {"52.0.0", "52.3.0", "53.0.0"}
    assert {release[0]: release[1] for release in answer.releases}["52.3.0"] == "2026-03-01"
    assert answer.newest[0] == "53.0.0"


def test_a_missing_nuget_leaf_costs_the_date_not_the_lookup() -> None:
    registry = Registry({"https://api.nuget.org/v3-flatcontainer/x/index.json": {"versions": ["1.0.0"]}})
    with isolated_cache():
        answer = lookup(dependency(NUGET, "X", "1.0.0"), registry)
    assert answer.error is None and answer.releases == (("1.0.0", None, None),)


def test_pypi_npm_and_composer() -> None:
    registry = Registry(
        {
            "https://pypi.org/pypi/ruff/json": {
                "info": {"project_urls": {"Changelog": "https://c"}},
                "releases": {
                    "0.16.10": [{"upload_time_iso_8601": "2026-09-20T00:00:00Z"}],
                    "0.17.0rc1": [{"upload_time_iso_8601": "2026-09-25T00:00:00Z"}],
                    "0.16.9": [{"upload_time_iso_8601": "2026-09-01T00:00:00Z", "yanked": True}],
                },
            },
            "https://registry.npmjs.org/@types%2Fnode": {
                "versions": {"24.3.0": {}, "25.0.0-beta": {}},
                "time": {"24.3.0": "2026-09-01T00:00:00Z"},
            },
            "https://repo.packagist.org/p2/monolog/monolog.json": {
                "packages": {
                    "monolog/monolog": [
                        {"version": "v3.9.0", "time": "2026-01-01T00:00:00+00:00"},
                        {"version": "3.10.0-RC1"},
                    ]
                }
            },
        }
    )
    with isolated_cache():
        ruff = lookup(dependency(PYPI, "ruff", "==0.16.5"), registry)
        assert (versions(ruff), ruff.homepage) == ({"0.16.10"}, "https://c"), (
            "prereleases and yanked releases are not offered"
        )
        assert versions(lookup(dependency(NPM, "@types/node", "24.1.0"), registry)) == {"24.3.0"}
        assert versions(lookup(dependency(COMPOSER, "monolog/monolog", "3.8.0"), registry)) == {"3.9.0"}


def test_a_docker_tag_is_compared_only_with_tags_of_its_shape() -> None:
    tags = ["9.1.0", "9.1.0-oracle", "8.4.11", "8.4.10", "8.4", "lts", "26.7.0-oraclelinux9"]
    registry = Registry(
        {HUB_MYSQL: {"results": [{"name": tag, "last_updated": "2026-09-29T00:00:00Z"} for tag in tags], "next": None}}
    )
    with isolated_cache():
        answer = lookup(dependency(DOCKER, "mysql", "8.4"), registry)
    assert versions(answer) == {"9.1.0", "8.4.11", "8.4.10"}


def test_a_two_part_vendor_is_compared_with_its_own_two_part_releases() -> None:
    """PostgreSQL numbers releases MAJOR.MINOR: `16.1-alpine` is compared with the other
    `N.N-alpine` releases, and a floating `16-alpine` is offered them too -- never three-part
    tags, which postgres does not publish."""
    url = "https://hub.docker.com/v2/namespaces/library/repositories/postgres/tags?page_size=100&ordering=last_updated"
    tags = ["16.1-alpine", "16.10-alpine", "17.6-alpine", "16-alpine", "16.10", "alpine", "latest"]
    registry = Registry({url: {"results": [{"name": tag} for tag in tags], "next": None}})
    with isolated_cache():
        pinned = lookup(dependency(DOCKER, "postgres", "16.1-alpine"), registry)
        floating = lookup(dependency(DOCKER, "postgres", "16-alpine"), registry)
    assert versions(pinned) == {"16.1-alpine", "16.10-alpine", "17.6-alpine"}
    assert pinned.newest[0] == "17.6-alpine", "16.10 is above 16.9, numerically"
    assert versions(floating) == versions(pinned)


def test_an_lts_tag_outranks_a_higher_innovation_number() -> None:
    """Measured 2026-10-02: MySQL's `lts` pointed at 9.7.2 while `innovation` pointed at 26.7.0.
    The highest number would move a production database from 8.4 LTS onto an innovation build."""
    tags = {
        "26.7.0": "sha:innovation",
        "innovation": "sha:innovation",
        "9.7.2": "sha:lts",
        "lts": "sha:lts",
        "8.4.11": "sha:84",
    }
    registry = Registry(
        {HUB_MYSQL: {"results": [{"name": name, "digest": digest} for name, digest in tags.items()], "next": None}}
    )
    with isolated_cache():
        answer = lookup(dependency(DOCKER, "mysql", "8.4.11"), registry)
    assert (answer.preferred, answer.newest[0]) == ("9.7.2", "9.7.2")


def test_without_an_lts_tag_the_highest_release_is_newest() -> None:
    url = "https://hub.docker.com/v2/namespaces/library/repositories/redis/tags?page_size=100&ordering=last_updated"
    registry = Registry(
        {url: {"results": [{"name": "8.2.1", "digest": "a"}, {"name": "8.0.3", "digest": "b"}], "next": None}}
    )
    with isolated_cache():
        answer = lookup(dependency(DOCKER, "redis", "8.0.3"), registry)
    assert (answer.preferred, answer.newest[0]) == (None, "8.2.1")


def test_microsoft_images_come_from_mcr() -> None:
    url = "https://mcr.microsoft.com/v2/dotnet/aspnet/tags/list"
    registry = Registry({url: {"tags": ["10.0.11", "10.0.12", "10.0.12-noble", "11.0.0-preview.1"]}})
    with isolated_cache():
        assert lookup(dependency(DOCKER, "mcr.microsoft.com/dotnet/aspnet", "10.0.11"), registry).newest[0] == "10.0.12"


def test_an_unknown_registry_is_named_not_guessed() -> None:
    with isolated_cache():
        answer = lookup(dependency(DOCKER, "ghcr.io/owner/image", "1.0.0"), Registry({}))
    assert answer.releases == () and "not supported" in (answer.error or "")


def test_an_action_lists_its_releases_and_resolves_each_line_s_newest_to_a_commit() -> None:
    base = "https://api.github.com/repos/actions/checkout"
    releases = [
        {"tag_name": "v7.0.1", "published_at": "2026-09-01T00:00:00Z"},
        {"tag_name": "v7.0.0", "published_at": "2026-06-01T00:00:00Z"},
        {"tag_name": "v6.0.4", "published_at": "2026-05-01T00:00:00Z"},
        {"tag_name": "v8.0.0-beta", "prerelease": True},
    ]
    registry = Registry(
        {
            f"{base}/releases?per_page=100": releases,
            f"{base}/commits/v7.0.1": SHA.encode(),
            f"{base}/commits/v6.0.4": ("b" * 40).encode(),
        }
    )
    with isolated_cache():
        answer = lookup(dependency(ACTION, "actions/checkout", "c" * 40), registry)
    by_tag = {release[0]: release for release in answer.releases}
    assert set(by_tag) == {"v7.0.1", "v7.0.0", "v6.0.4"}
    assert (by_tag["v7.0.1"][2], by_tag["v6.0.4"][2], by_tag["v7.0.0"][2]) == (SHA, "b" * 40, None)


def test_the_dotnet_sdk_ignores_preview_channels() -> None:
    index = {
        "releases-index": [
            {"channel-version": "11.0", "latest-sdk": "11.0.100-rc.1", "support-phase": "go-live"},
            {
                "channel-version": "10.0",
                "latest-sdk": "10.0.401",
                "support-phase": "active",
                "latest-release-date": "2026-09-09",
            },
        ]
    }
    with isolated_cache():
        answer = lookup(dependency(DOTNET_SDK, "dotnet-sdk", "10.0.100"), Registry({DOTNET_RELEASES_INDEX: index}))
    assert answer.releases == (("10.0.401", "2026-09-09", None),)


def test_a_failure_is_an_answer_and_is_never_cached() -> None:
    url = "https://pypi.org/pypi/ruff/json"
    healthy_body = {"info": {}, "releases": {"0.16.10": [{"upload_time_iso_8601": "2026-09-20T00:00:00Z"}]}}
    with isolated_cache():
        failing = Registry({url: OSError("connection refused")})
        assert "connection refused" in (lookup(dependency(PYPI, "ruff", "==0.16.5"), failing).error or "")
        healthy = Registry({url: healthy_body})
        assert versions(lookup(dependency(PYPI, "ruff", "==0.16.5"), healthy)) == {"0.16.10"}
        assert healthy.asked == [url], "the failure was cached, so the healthy registry was never asked"


def test_a_missing_package_says_so() -> None:
    with isolated_cache():
        assert lookup(dependency(PYPI, "no-such-thing", "==1.0.0"), Registry({})).error == "not found in its registry"


def test_a_cached_answer_is_reused_within_the_hour() -> None:
    url = "https://pypi.org/pypi/ruff/json"
    registry = Registry(
        {url: {"info": {}, "releases": {"0.16.10": [{"upload_time_iso_8601": "2026-09-20T00:00:00Z"}]}}}
    )
    with isolated_cache():
        first = lookup(dependency(PYPI, "ruff", "==0.16.5"), registry)
        second = lookup(dependency(PYPI, "ruff", "==0.16.5"), registry)
    assert registry.asked == [url]
    assert first.releases == second.releases, "a cached answer must read back as the one stored"


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "dependency registries"))

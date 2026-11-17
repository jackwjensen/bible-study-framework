"""Cases for standards_deps_declared: what a repository declares, and what is not its to judge.

The reader is the gate's whole view of the repository, so a manifest it misreads is a dependency
the gate never checks -- the silent half. Every reader is exercised against a real file in a
temporary repository, and the two exclusions (a pack-owned file in a consumer, a Docker build
stage) are asserted from both sides.

Run: python test_standards_deps_declared.py   (or pytest)
"""

from __future__ import annotations

import io
import sys
import tempfile
from contextlib import contextmanager, redirect_stderr
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_deps_declared import (  # noqa: E402
    ACTION,
    COMPOSER,
    DOCKER,
    DOTNET_SDK,
    NPM,
    NUGET,
    PYPI,
    Dependency,
    declared_dependencies,
    is_exact,
    split_image,
)
from standards_pack_identity import PACK_DIRECTORY  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"


@contextmanager
def repository(**files: str) -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        for name, content in files.items():
            path = root / name.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        yield root


def found(root: Path) -> dict[str, str]:
    return {dependency.key: dependency.version for dependency in declared_dependencies(root)}


def test_every_manifest_kind_is_read() -> None:
    with repository(
        **{
            "App__App.csproj": '<Project><ItemGroup><PackageReference Include="Stripe.net" Version="53.0.0" /></ItemGroup></Project>',
            "Directory.Packages.props": '<Project><ItemGroup><PackageVersion Include="xunit" Version="2.9.3" /></ItemGroup></Project>',
            ".config__dotnet-tools.json": '{"tools": {"dotnet-ef": {"version": "10.0.12"}}}',
            "requirements.txt": "Django==5.2.1\n# a comment\n-r other.txt\n",
            "pyproject.toml": '[project]\ndependencies = ["httpx==0.28.1"]\n',
            "package.json": '{"dependencies": {"react": "19.1.0"}, "devDependencies": {"@types/node": "^24.0.0"}}',
            "composer.json": '{"require": {"php": "^8.3", "monolog/monolog": "3.9.0"}}',
            "Dockerfile": "FROM node:24.1.0-alpine\n",
            "docker-compose.yml": "services:\n  db:\n    image: mysql:8.4\n",
            ".github__workflows__ci.yml": f"steps:\n  - uses: actions/checkout@{SHA}  # v7\n  - uses: ./local\n",
            "global.json": '{"sdk": {"version": "10.0.100", "rollForward": "latestPatch"}}',
        }
    ) as root:
        assert found(root) == {
            f"{NUGET}:Stripe.net": "53.0.0",
            f"{NUGET}:xunit": "2.9.3",
            f"{NUGET}:dotnet-ef": "10.0.12",
            f"{PYPI}:django": "==5.2.1",
            f"{PYPI}:httpx": "==0.28.1",
            f"{NPM}:react": "19.1.0",
            f"{NPM}:@types/node": "^24.0.0",
            f"{COMPOSER}:monolog/monolog": "3.9.0",
            f"{DOCKER}:node": "24.1.0-alpine",
            f"{DOCKER}:mysql": "8.4",
            f"{ACTION}:actions/checkout": SHA,
            f"{DOTNET_SDK}:dotnet-sdk": "10.0.100",
        }


def test_php_itself_is_left_to_the_runtime_floor_rules() -> None:
    with repository(**{"composer.json": '{"require": {"php": "^8.3", "ext-intl": "*"}}'}) as root:
        assert found(root) == {}


def test_a_build_stage_is_not_an_image() -> None:
    dockerfile = "FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build\nFROM build AS publish\nFROM base\n"
    with repository(Dockerfile=dockerfile) as root:
        assert found(root) == {f"{DOCKER}:mcr.microsoft.com/dotnet/sdk": "10.0", f"{DOCKER}:base": "latest"}


def test_a_pack_file_in_a_consumer_is_the_pack_s_to_judge() -> None:
    """A consumer cannot change these -- the next sync overwrites the edit -- so judging them
    there would block a push on a decision the repo is not allowed to make."""
    with repository(
        **{
            f"{PACK_DIRECTORY}__requirements.txt": "ruff==0.16.10\n",
            "Directory.Build.props": (
                "<!-- Source of truth: engineering-standards/dotnet/Directory.Build.props -->\n"
                '<Project><ItemGroup><PackageReference Include="SonarAnalyzer.CSharp" Version="10.33.0.1635" /></ItemGroup></Project>'
            ),
            "requirements.txt": "pytest==9.1.1\n",
        }
    ) as root:
        assert found(root) == {f"{PYPI}:pytest": "==9.1.1"}


def test_vendored_and_tooling_directories_are_not_walked() -> None:
    with repository(
        **{
            "node_modules__left-pad__package.json": '{"dependencies": {"x": "1.0.0"}}',
            ".venv__requirements.txt": "x==1.0.0\n",
            ".idea__package.json": '{"dependencies": {"x": "1.0.0"}}',
        }
    ) as root:
        assert found(root) == {}


def test_a_malformed_manifest_is_named_not_skipped_quietly() -> None:
    """Not fatal -- one broken file must not stop every push -- but never silent: a manifest
    the gate cannot read is dependencies it is not checking, which must not look like "current"."""
    files = {"package.json": "{ not json", "global.json": "[1, 2]", "requirements.txt": "ruff==0.16.10\n"}
    with repository(**files) as root:
        errors = io.StringIO()
        with redirect_stderr(errors):
            assert found(root) == {f"{PYPI}:ruff": "==0.16.10"}
        assert "package.json" in errors.getvalue() and "global.json" in errors.getvalue()
        assert "NOT checked" in errors.getvalue()


def test_what_counts_as_a_pin() -> None:
    assert is_exact(PYPI, "pytest", "==9.1.1")
    assert not is_exact(PYPI, "pytest", ">=9.1")
    assert is_exact(NUGET, "SonarAnalyzer.CSharp", "10.33.0.1635")
    assert not is_exact(NUGET, "SonarAnalyzer.CSharp", "10.*")
    assert is_exact(NPM, "react", "19.1.0")
    assert not is_exact(NPM, "react", "^19.1.0")
    assert is_exact(DOCKER, "mysql", "8.4.11")
    assert is_exact(DOCKER, "node", "24.1.0-alpine")
    # Variant tags carry several suffix segments, and each still names one release -- the
    # gate itself proposes them as "newest pin in its line" (wordpress-docker-template, 2026-10-08).
    assert is_exact(DOCKER, "wordpress", "7.1.3-php8.5-fpm")
    assert is_exact(DOCKER, "php", "8.3.12-fpm-alpine")
    assert is_exact(DOCKER, "litespeedtech/openlitespeed", "1.9.2-lsphp85")
    assert not is_exact(DOCKER, "php", "8.3-fpm-alpine")
    assert not is_exact(DOCKER, "wordpress", "7.1-php8.5-fpm")
    assert not is_exact(DOCKER, "mysql", "8.4")
    assert not is_exact(DOCKER, "mysql", "latest")
    assert is_exact(ACTION, "actions/checkout", SHA)
    assert not is_exact(ACTION, "actions/checkout", "v7")


def test_two_parts_pin_only_a_vendor_that_numbers_releases_with_two() -> None:
    """allegro-it-services, 2026-10-14: PostgreSQL numbers a release MAJOR.MINOR, so no postgres
    tag could pass and the pin had to be a digest. Two parts are a release only for such a
    vendor -- for everyone else they are a line that moves onto every patch."""
    for name in ("postgres", "library/postgres", "docker.io/library/postgres"):
        assert is_exact(DOCKER, name, "16.1-alpine"), name
    assert is_exact(DOCKER, "postgres", "17.6-bookworm")
    assert is_exact(DOCKER, "postgres", "16.1")
    assert not is_exact(DOCKER, "postgres", "16-alpine"), "a postgres major is a line"
    assert not is_exact(DOCKER, "postgres", "16")
    assert not is_exact(DOCKER, "postgres", "latest")
    assert not is_exact(DOCKER, "redis", "7.2-alpine"), "redis numbers releases 7.2.N"
    assert not is_exact(DOCKER, "node", "24")
    assert not is_exact(DOCKER, "someone/postgres", "16.1"), "only the official image is that vendor"
    assert not is_exact(NUGET, "postgres", "16.1"), "the list is Docker images, nothing else"


def test_a_dependency_s_pin_is_judged_by_its_own_vendor() -> None:
    def pinned(name: str, version: str) -> bool:
        return Dependency(DOCKER, name, version, Path("docker-compose.yml"), 1).is_pinned

    assert pinned("postgres", "16.1-alpine")
    assert not pinned("redis", "7.2-alpine")


def test_the_release_is_spelled_as_its_registry_spells_it() -> None:
    def release(ecosystem: str, version: str) -> str:
        return Dependency(ecosystem, "x", version, Path("f"), 1).release

    assert release(PYPI, "==9.1.1") == "9.1.1"
    assert release(COMPOSER, "v3.9.0") == "3.9.0"
    assert release(DOCKER, "8.4.11") == "8.4.11"


def test_image_references_split_into_name_and_version() -> None:
    assert split_image("mysql:8.4") == ("mysql", "8.4")
    assert split_image("localhost:5000/app:1.2.3") == ("localhost:5000/app", "1.2.3")
    assert split_image("redis") == ("redis", "latest")
    assert split_image("nginx@sha256:abc") == ("nginx", "sha256:abc")
    assert split_image("${IMAGE}") is None


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "dependency declarations"))

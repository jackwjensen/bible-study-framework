#!/usr/bin/env python3
"""Which rules apply to a file that is NOT source code, keyed on its filename.

Split out of standards_dispatch.py on 2026-09-02, when `check_source_file` reached
cyclomatic complexity 37 against the pack's own limit of 10 -- 36 branches and 104
statements in one function, which the pack was shipping as a rule to nine repositories
while breaking it here. The seam is the one the function already had: a compose file, a
Dockerfile, a workflow and a `.csproj` are each in scope for a short, fixed list of rules
and nothing else, while source files go through the language dispatch that stayed behind.

A TABLE, NOT AN if/elif CHAIN. The chain was the complexity: adding a file type meant
editing a function nobody could hold in their head, and the ordering constraints inside it
were invisible. Written out as `DISPATCH` below, each entry is one file type, its rules are
one small function, and the two places where ORDER IS LOAD-BEARING are stated where they
apply rather than being a property of where a branch happened to sit.

FIRST MATCH WINS, and every handler is terminal -- exactly as the early `return`s were.

Source of truth: engineering-standards/engineering_standards/standards_dispatch_files.py
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from itertools import chain
from pathlib import Path

from standards_checks import (
    check_compose_container_names,
    check_compose_ports,
    check_compose_read_only,
    check_container_user,
    check_deploy_gate,
    check_deploy_host_key,
    check_deploy_pull,
    check_deploy_ssh_user,
    check_env_example,
    check_overlay_name,
    check_production_ports,
    is_workflow,
)
from standards_compose import COMPOSE_FILENAME, ENV_EXAMPLE_FILENAME
from standards_config import CheckConfig
from standards_core import Violation
from standards_deps_pinning import check_unpinned_dependencies
from standards_ef_provider import check_ef_core_support, check_ef_provider_support
from standards_node_support import check_node_runtime_support
from standards_packages import check_dependency_holdback, check_package_wildcards
from standards_php_support import check_php_support
from standards_python_support import check_python_support
from standards_runner import check_runner_image
from standards_scope import (
    COMPOSER_FILENAME,
    CONFIG_FILENAME,
    DEPENDABOT_FILENAME,
    DEPENDENCY_MANIFEST_FILENAME,
    DOCKERFILE_FILENAME,
    NVMRC_FILENAME,
    PACKAGE_JSON_FILENAME,
    PROJECT_FILENAME,
    PYPROJECT_FILENAME,
    PYTHON_VERSION_FILENAME,
    REQUIREMENTS_FILENAME,
    SETUP_CFG_FILENAME,
)
from standards_versions import (
    check_action_versions,
    check_ci_toolchain,
    check_dotnet_runtime_support,
)

Rules = Iterable[Violation]


def _compose(path: Path, lines: list[str], config: CheckConfig, repo_root: Path) -> Rules:
    """Compose files are in scope for the deployment-contract rules only.

    Length and the source rules are meaningless for them, and the length rule would flag
    long but entirely reasonable stack definitions.
    """
    # Not gated on check_compose_conventions, for the same reason as container-root-user: a
    # repo may opt out of the port and naming conventions and still not be allowed to ship a
    # writable root filesystem. Scoped to services with `build:`, so opting out of the
    # conventions can never land a finding on a third-party image.
    yield from check_compose_read_only(path, lines)
    if config.check_compose_conventions:
        yield from check_compose_ports(path, lines)
        yield from check_compose_container_names(path, lines)
        yield from check_overlay_name(path, lines)
        yield from check_production_ports(path, lines, repo_root)


def _env_example(path: Path, lines: list[str], config: CheckConfig, repo_root: Path) -> Rules:
    # Only meaningful for a repo that actually ships as a compose stack; a library or a docs
    # repo has no COMPOSE_FILE to document.
    if config.check_compose_conventions and (repo_root / "docker-compose.yml").is_file():
        yield from check_env_example(path, lines)


def _project(path: Path, lines: list[str], config: CheckConfig, repo_root: Path) -> Rules:
    """A .csproj is XML, so the source rules are meaningless -- four lifecycle rules only.

    None is gated on a config flag: a repo can opt out of the compose conventions and still
    not be allowed to ship an unsupported runtime or an unmaintainable data layer, both of
    which are security facts rather than conventions.

    They are separate rules because they read different things and fail independently.
    `runtime-support` reads <TargetFramework>; `ef-provider-support` reads the provider
    package, which pins the EF Core major regardless of the TFM. A project on net10.0 held at
    EF Core 9 by its provider passes the first and fails the second -- the case that
    motivated adding it. `package-wildcards` asks a different question again: not WHICH
    version, but whether one was declared at all. A wildcard passes every floor -- it has no
    number to be below -- while making the build unreproducible.
    """
    yield from check_dotnet_runtime_support(path, lines)
    yield from check_package_wildcards(path, lines)
    yield from check_ef_core_support(path, lines)
    yield from check_ef_provider_support(path, lines, config.distributes_binaries)


def _dockerfile(path: Path, lines: list[str], config: CheckConfig, repo_root: Path) -> Rules:
    """Four rules, none gated on check_compose_conventions.

    A repo may opt out of the port and naming conventions and still not ship a container
    running as root, nor build on a Node major, Python minor or PHP minor the estate has left
    behind. The .NET base image is read too, but only by the repo-level `dotnet-consistency`
    rule -- an image tag alone says nothing until there is a TFM to compare it against.
    """
    yield from check_container_user(path, lines)
    yield from check_node_runtime_support(path, lines)
    yield from check_python_support(path, lines)
    yield from check_php_support(path, lines)


def _nvmrc(path: Path, lines: list[str], config: CheckConfig, repo_root: Path) -> Rules:
    """One line, one version, and it outranks every other Node pin in the repo."""
    yield from check_node_runtime_support(path, lines)


def _python_declaration(path: Path, lines: list[str], config: CheckConfig, repo_root: Path) -> Rules:
    """`.python-version`, pyproject.toml and setup.cfg: the Python floor and nothing else.

    `.python-version` is `.nvmrc`'s twin; the other two hold the remaining declaration sites
    (`requires-python`, the ruff/black/mypy targets). All three are TOML or INI, so the
    length, naming and source rules have nothing to say about them.
    """
    yield from check_python_support(path, lines)


def _composer(path: Path, lines: list[str], config: CheckConfig, repo_root: Path) -> Rules:
    """The PHP floor and the wildcard rule; it is JSON, so no source rule applies.

    The wildcard rule reads require/require-dev for `dev-master`/`@dev`/`*` -- the composer
    twin of the .csproj and package.json cases, and the exact shape an external audit found
    throughout a codebase.
    """
    yield from check_php_support(path, lines)
    yield from check_package_wildcards(path, lines)


def _requirements(path: Path, lines: list[str], config: CheckConfig, repo_root: Path) -> Rules:
    """A bare, version-less line is the Python sibling of a floating npm/composer dependency."""
    yield from check_package_wildcards(path, lines)


def _package_json(path: Path, lines: list[str], config: CheckConfig, repo_root: Path) -> Rules:
    """The wildcard rule (the credential rule already ran, above the dispatch).

    `engines.node` is deliberately NOT read -- see standards_node_support.py for why a floor
    must not judge a range.
    """
    yield from check_package_wildcards(path, lines)


def _dependabot(path: Path, lines: list[str], config: CheckConfig, repo_root: Path) -> Rules:
    """Whether each `ignore:` entry says why it holds a package back, and until when."""
    yield from check_dependency_holdback(path, lines)


def _dependency_manifest(path: Path, lines: list[str], config: CheckConfig, repo_root: Path) -> Rules:
    """`global.json`, `dotnet-tools.json`, `Directory.*.props`, `compose.yml`, `action.yml`.

    In scope for dependency-unpinned ONLY, which `non_source_rules` applies to every entry --
    so this handler yields nothing itself. Terminal for the same reason as `_config_file`: it
    keeps the source rules (length, naming) off files they have no opinion about.
    """
    return ()


def _config_file(path: Path, lines: list[str], config: CheckConfig, repo_root: Path) -> Rules:
    """In scope for the credential rule ONLY, which already ran above the dispatch.

    Terminal on purpose: matching here keeps the length and naming rules off JSON and INI
    they have no opinion about. It yields nothing, and that is the whole behaviour.
    """
    return ()


def _workflow(path: Path, lines: list[str], config: CheckConfig, repo_root: Path) -> Rules:
    if config.check_compose_conventions:
        yield from check_deploy_gate(path, lines)
    # Not gated on check_compose_conventions, for the same reason as the Dockerfile rules:
    # who CI logs in as, and whether it verifies the host it is talking to, are security
    # properties rather than conventions a repo gets to opt out of.
    yield from check_deploy_ssh_user(path, lines)
    yield from check_deploy_host_key(path, lines)
    # Ungated for the same reason: an unpulled image is an unpatched one, which is a security
    # property and not a convention.
    yield from check_deploy_pull(path, lines)
    # Not gated either: an out-of-date action has nothing to do with the deployment contract,
    # and a repo that opts out of compose rules still runs CI.
    yield from check_action_versions(path, lines)
    # Ungated: what OS CI checks the code on is the staging-matches-production question, not a
    # compose convention.
    yield from check_runner_image(path, lines)
    yield from check_ci_toolchain(path, lines, repo_root)
    # Separate from ci-toolchain: that asks whether CI satisfies the repo's OWN pin, so a repo
    # pinning 22 everywhere is self-consistent and passes it. This asks whether the pin clears
    # the estate floor.
    yield from check_node_runtime_support(path, lines)
    # Same question for Python, and the reason it must be asked HERE as well as in
    # pyproject.toml: CI is the one declaration site a repo can have without owning any Python
    # packaging at all. Eleven repos in this estate install Python in CI to run the pack's own
    # scanner and declare it nowhere else.
    yield from check_python_support(path, lines)
    yield from check_php_support(path, lines)


Matcher = Callable[[Path, Path], bool]
Handler = Callable[[Path, list[str], CheckConfig, Path], Rules]

# FIRST MATCH WINS, and two orderings are load-bearing:
#
# * dependabot BEFORE config, because a repo spelling it `.yml` would otherwise be swallowed
#   by the config entry -- whose whole behaviour is to match and yield nothing.
# * the python-declaration entry BEFORE config, for the same reason: pyproject.toml and
#   setup.cfg are configuration by any reading, and matching there would silently drop the
#   Python floor's four remaining declaration sites.
DISPATCH: tuple[tuple[Matcher, Handler], ...] = (
    (lambda path, root: bool(COMPOSE_FILENAME.match(path.name)), _compose),
    (lambda path, root: bool(ENV_EXAMPLE_FILENAME.match(path.name)), _env_example),
    (lambda path, root: bool(PROJECT_FILENAME.match(path.name)), _project),
    (lambda path, root: bool(DOCKERFILE_FILENAME.match(path.name)), _dockerfile),
    (lambda path, root: bool(NVMRC_FILENAME.match(path.name)), _nvmrc),
    (
        lambda path, root: bool(
            PYTHON_VERSION_FILENAME.match(path.name)
            or PYPROJECT_FILENAME.match(path.name)
            or SETUP_CFG_FILENAME.match(path.name)
        ),
        _python_declaration,
    ),
    (lambda path, root: bool(COMPOSER_FILENAME.match(path.name)), _composer),
    (lambda path, root: bool(REQUIREMENTS_FILENAME.match(path.name)), _requirements),
    (lambda path, root: bool(PACKAGE_JSON_FILENAME.match(path.name)), _package_json),
    (lambda path, root: bool(DEPENDABOT_FILENAME.match(path.name)), _dependabot),
    (lambda path, root: bool(DEPENDENCY_MANIFEST_FILENAME.match(path.name)), _dependency_manifest),
    (lambda path, root: bool(CONFIG_FILENAME.match(path.name)), _config_file),
    (is_workflow, _workflow),
)


def non_source_rules(path: Path, lines: list[str], config: CheckConfig, repo_root: Path) -> Rules | None:
    """The rules for this file if it is a non-source type, or None if it is source.

    None and an empty iterable mean DIFFERENT things, which is why this cannot just return a
    list: `_config_file` legitimately yields nothing, and the caller must still stop rather
    than fall through to the length and naming rules.
    """
    for matches, handler in DISPATCH:
        if matches(path, repo_root):
            # dependency-unpinned beside EVERY handler rather than inside each: whether a file
            # declares versions is decided by the shared reader, not by which row matched, so
            # a new manifest type cannot be added here with the pin check quietly missing.
            return chain(check_unpinned_dependencies(path, lines, repo_root), handler(path, lines, config, repo_root))
    return None

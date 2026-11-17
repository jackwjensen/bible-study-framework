#!/usr/bin/env python3
"""The shipped user-docs system: an in-product documentation registry, held as a contract.

User documentation for a web product ships AS product -- localised, styled, reachable at
the point of need -- not as markdown in a repo its users can never see. The pack cannot
ship that system, so it enforces the invariants of whichever one the repo built. The repo
declares where the machinery lives (`.standards.json`, key `userDocs`; see UserDocsConfig
in standards_core.py), and these rules hold it together:

    userdocs-missing        userDocs is declared but the registry file is absent or empty
    userdocs-dangling-slug  a help link names a topic the registry does not have
    userdocs-unlinked-page  a declared user-facing page carries no link into the docs
    userdocs-orphan-topic   a registry topic documents a route no page declares any more

The reference shape is InvoTrack's: DocRegistry.cs enumerates every topic (slug, route,
LastReviewed, content component), each tenant-facing page carries a PageHeader HelpSlug
into it, and Dokumentation.razor renders topics from the registry alone. The rules are
deliberately shape-agnostic -- a Django app can point them at a fixtures file and a
template include; only the patterns change.

REPO-LEVEL, LIKE standards_docs. Whether a topic is orphaned is a property of every route
in the tree, so `check_userdocs` runs on whole-tree scans only -- push and CI, never the
commit stage -- and findings ride the baseline ratchet like every other repo-level rule.

THE DOC-SYNC NUDGE IS A NOTE, NOT A VIOLATION. `unsynced_change_notes` runs at the commit
stage and prints when staged changes touch a documented page without touching the registry
or any topic content. It cannot block: whether an edit changed BEHAVIOUR (doc update owed)
or merely refactored (no doc owed) is exactly the judgment the pack leaves to /code-review,
and a blocking rule would fight every mechanical sweep across documented pages. The note
surfaces the question at the moment the answer is cheapest.

GLOB SEMANTICS ARE fnmatch: shell-style against the repo-relative posix path, `*` crosses
directory separators. Declared inventories should therefore anchor on real path prefixes
(`App/Components/Pages/Manage/*.razor`), which doubles as the repo's own statement of
which pages are user-facing -- the admin-only and auth surfaces simply stay out of the
globs instead of collecting exemption markers.

Source of truth: engineering-standards/engineering_standards/standards_userdocs.py
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable

from standards_core import CheckConfig, UserDocsConfig, Violation
from standards_exemptions import exemption_reason, line_exemption_reason
from standards_markdown import SKIP_DIRECTORIES, read_lines
from standards_tree import walk_repository


def check_userdocs(repo_root: Path, config: CheckConfig) -> Iterable[Violation]:
    """Every user-docs finding for this repo. The one entry point the driver calls."""
    declared = config.user_docs
    if declared is None:
        return

    registry_path = repo_root / declared.registry_file
    topics = _registry_topics(registry_path, declared)

    if topics is None:
        yield _missing(registry_path, declared, "does not exist")
        return
    if not topics:
        yield _missing(registry_path, declared, "matches no registry entries")
        return

    slugs = {slug for slug, _route, _line in topics}
    tree = _relative_files(repo_root)

    yield from _check_help_links(repo_root, declared, slugs, tree)
    yield from _check_pages(repo_root, declared, tree)
    yield from _check_topics(registry_path, declared, topics, repo_root, tree)


def unsynced_change_notes(repo_root: Path, config: CheckConfig, staged: list[Path]) -> list[str]:
    """Commit-stage nudge: documented pages staged without their docs. Never blocks.

    "Documented page" = a staged file whose help links resolve into the registry's world.
    The commit is considered synced when the registry file itself is staged (a LastReviewed
    bump edits it) or any topic-content file is (a prose edit). What this cannot know --
    whether the change was behavioural at all -- is why it is a note.
    """
    declared = config.user_docs
    if declared is None:
        return []

    staged_relative = {
        path.resolve().relative_to(repo_root.resolve()).as_posix()
        for path in staged
        if path.resolve().is_relative_to(repo_root.resolve())
    }

    if declared.registry_file in staged_relative:
        return []
    if declared.content_glob and any(fnmatch(relative, declared.content_glob) for relative in staged_relative):
        return []

    touched: dict[str, set[str]] = {}
    for relative in sorted(staged_relative):
        for glob, pattern in declared.help_links:
            if not fnmatch(relative, glob):
                continue
            compiled = re.compile(pattern)
            for line in read_lines(repo_root / relative):
                for match in compiled.finditer(line):
                    touched.setdefault(relative, set()).add(_captured(match, "slug"))

    if not touched:
        return []

    described = "; ".join(
        f"{relative} (topics: {', '.join(sorted(slugs))})" for relative, slugs in sorted(touched.items())
    )
    return [
        f"staged changes touch documented pages -- {described} -- without touching "
        f"{declared.registry_file} or any topic content. If behaviour changed, doc-sync "
        f"requires the topic updated and its review date bumped in this same commit; a "
        f"pure refactor owes nothing."
    ]


def _missing(registry_path: Path, declared: UserDocsConfig, why: str) -> Violation:
    return Violation(
        path=registry_path,
        line=1,
        rule="userdocs-missing",
        message=(
            f"'{declared.registry_file}' is declared as the user-docs registry but {why}. "
            f"The registry is the single source of every shipped doc topic -- create it, or "
            f"fix the userDocs declaration in .standards.json."
        ),
    )


def _registry_topics(registry_path: Path, declared: UserDocsConfig) -> list[tuple[str, str | None, int]] | None:
    """(slug, route-or-None, line) per registry entry, or None when the file is absent."""
    if not registry_path.is_file():
        return None

    compiled = re.compile(declared.registry_pattern)
    topics: list[tuple[str, str | None, int]] = []
    for line_number, line in enumerate(read_lines(registry_path), start=1):
        for match in compiled.finditer(line):
            captures = match.groupdict()
            slug = captures.get("slug") or match.group(1)
            topics.append((slug, captures.get("route"), line_number))
    return topics


def _relative_files(repo_root: Path) -> list[str]:
    """Every file's repo-relative posix path, pruned like the docs symbol walk."""
    relative_paths: list[str] = []
    for directory, subdirectories, names in walk_repository(repo_root):
        subdirectories[:] = [name for name in subdirectories if name not in SKIP_DIRECTORIES]
        base = Path(directory)
        relative_paths.extend((base / name).relative_to(repo_root).as_posix() for name in names)
    return sorted(relative_paths)


def _matching(tree: list[str], glob: str) -> list[str]:
    return [relative for relative in tree if fnmatch(relative, glob)]


def _captured(match: re.Match[str], group_name: str) -> str:
    value = match.groupdict().get(group_name)
    return value if value is not None else match.group(1)


def _check_help_links(
    repo_root: Path, declared: UserDocsConfig, slugs: set[str], tree: list[str]
) -> Iterable[Violation]:
    for glob, pattern in declared.help_links:
        compiled = re.compile(pattern)
        for relative in _matching(tree, glob):
            lines = read_lines(repo_root / relative)
            for index, line in enumerate(lines):
                for match in compiled.finditer(line):
                    slug = _captured(match, "slug")
                    if slug in slugs:
                        continue
                    if line_exemption_reason(lines, index, "userdocs-dangling-slug"):
                        continue
                    yield Violation(
                        path=repo_root / relative,
                        line=index + 1,
                        rule="userdocs-dangling-slug",
                        message=(
                            f"'{slug}' is linked as a help topic but the registry has no "
                            f"such slug -- the link renders a not-found page. Register the "
                            f"topic in {declared.registry_file}, or fix the slug."
                        ),
                    )


def _declared_routes(repo_root: Path, declared: UserDocsConfig, tree: list[str]) -> dict[str, list[str]]:
    """route -> the files declaring it, across every routes inventory."""
    routes: dict[str, list[str]] = {}
    for glob, pattern in declared.routes:
        compiled = re.compile(pattern)
        for relative in _matching(tree, glob):
            for line in read_lines(repo_root / relative):
                for match in compiled.finditer(line):
                    routes.setdefault(_captured(match, "route"), []).append(relative)
    return routes


def _check_pages(repo_root: Path, declared: UserDocsConfig, tree: list[str]) -> Iterable[Violation]:
    help_patterns = [re.compile(pattern) for _glob, pattern in declared.help_links]

    for glob, pattern in declared.routes:
        compiled = re.compile(pattern)
        for relative in _matching(tree, glob):
            lines = read_lines(repo_root / relative)
            first_route: tuple[int, str] | None = None
            has_help_link = False
            for index, line in enumerate(lines):
                if first_route is None:
                    match = compiled.search(line)
                    if match:
                        first_route = (index + 1, _captured(match, "route"))
                if any(help_pattern.search(line) for help_pattern in help_patterns):
                    has_help_link = True
                    break

            if first_route is None or has_help_link:
                continue
            if exemption_reason(lines, "userdocs-unlinked-page") is not None:
                continue
            line_number, route = first_route
            yield Violation(
                path=repo_root / relative,
                line=line_number,
                rule="userdocs-unlinked-page",
                message=(
                    f"'{route}' is a user-facing page with no link into the shipped docs. "
                    f"Users reach documentation from the point of need -- add the help "
                    f"link, or mark 'standards: userdocs-unlinked-page exempt -- <why>' in "
                    f"the file's header if this page genuinely needs none."
                ),
            )


def _check_topics(
    registry_path: Path,
    declared: UserDocsConfig,
    topics: list[tuple[str, str | None, int]],
    repo_root: Path,
    tree: list[str],
) -> Iterable[Violation]:
    if all(route is None for _slug, route, _line in topics):
        return

    known_routes = _declared_routes(repo_root, declared, tree)
    registry_lines = read_lines(registry_path)

    for slug, route, line_number in topics:
        if route is None or route in known_routes:
            continue
        if line_exemption_reason(registry_lines, line_number - 1, "userdocs-orphan-topic"):
            continue
        yield Violation(
            path=registry_path,
            line=line_number,
            rule="userdocs-orphan-topic",
            message=(
                f"'{slug}' documents route {route}, which no page declares any more. A "
                f"topic for a removed feature misleads every reader -- retire the topic, "
                f"update its route, or mark 'standards: userdocs-orphan-topic exempt -- "
                f"<why>' on a comment line above the entry."
            ),
        )

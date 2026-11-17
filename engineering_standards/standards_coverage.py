#!/usr/bin/env python3
"""Documentation coverage: elements that ship with no documentation at all.

Every other documentation rule audits the docs that exist -- structure, links, staleness.
These audit the gap the other direction: an element a reader will meet that no
documentation names. "Feature" is undecidable mechanically, so the rules cover the
enumerable proxies, and the judgment half ("did this change ship with its docs?") stays
with /code-review and the doc-before-commit workflow:

    docs-uncovered-command  a Django management command no documentation names
    docs-uncovered-env      an .env.example key with no comment and no mention anywhere
    docs-uncovered-route    a declared public route no documentation names

"Named" means the token appears anywhere in the documentation corpus: every docs/ page,
README.md and CLAUDE.md. Substring match, deliberately -- command stems and env keys are
long and distinctive, and demanding a backtick span would fail prose that is perfectly
good documentation.

REPO-LEVEL, LIKE standards_docs. Coverage is a property of the whole tree (the docs that
would cover a command can live anywhere in it), so `check_coverage` runs only on
whole-tree scans -- push and CI, never the commit stage -- and its findings ride the same
baseline ratchet: existing gaps are grandfathered on adoption, a NEW command/key/route
must arrive documented, which is the doc-before-commit rule made mechanical for the
subset a scanner can see.

ROUTES NEED A REPO-DECLARED INVENTORY. There is no universal place routes live, so a repo
that wants route coverage declares where its enumeration is in .standards.json:

    "docsRouteInventories": [
      { "file": "scripts/generate-sitemap.mjs", "pattern": "path:\\s*'([^']+)'" }
    ]

The pattern's first capture group is the route token. No config, no route checking --
the other two surfaces need no config at all.

EXEMPTIONS. A command exempts itself file-scoped (`standards: docs-uncovered-command
exempt -- <why>` in its header) -- one file is one command, so the file scope is the
natural one. A route exempts line-scoped, on a comment line above its inventory entry. An
env key needs no marker: a `#` comment beside it IS the documentation, which is the whole
point of the rule.

Source of truth: engineering-standards/engineering_standards/standards_coverage.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import CheckConfig, Violation
from standards_exemptions import exemption_reason, line_exemption_reason
from standards_markdown import SKIP_DIRECTORIES, documentation_pages, read_lines
from standards_tree import walk_repository

ENV_KEY = re.compile(r"^([A-Z][A-Z0-9_]*)=")


def check_coverage(repo_root: Path, config: CheckConfig) -> Iterable[Violation]:
    """Every coverage finding for this repo. The one entry point the driver calls."""
    if not config.check_docs:
        return

    corpus = _documentation_corpus(repo_root)
    yield from _check_commands(repo_root, corpus)
    yield from _check_env_example(repo_root, corpus)
    yield from _check_route_inventories(repo_root, corpus, config)


def _documentation_corpus(repo_root: Path) -> str:
    """The text a surface can be covered BY: every docs/ page, README.md and CLAUDE.md."""
    documents = list(documentation_pages(repo_root))
    documents += [repo_root / name for name in ("README.md", "CLAUDE.md") if (repo_root / name).is_file()]
    return "\n".join("\n".join(read_lines(document)) for document in documents)


def _check_commands(repo_root: Path, corpus: str) -> Iterable[Violation]:
    for command in _management_commands(repo_root):
        stem = command.stem
        if stem in corpus:
            continue
        if exemption_reason(read_lines(command), "docs-uncovered-command") is not None:
            continue
        yield Violation(
            path=command,
            line=1,
            rule="docs-uncovered-command",
            message=(
                f"'{stem}' is a management command no documentation names. An operator can "
                f"only run what something teaches -- name it in a docs page, README.md or "
                f"CLAUDE.md, or mark 'standards: docs-uncovered-command exempt -- <why>' in "
                f"the command file's header."
            ),
        )


def _management_commands(repo_root: Path) -> list[Path]:
    """Every Django management command in the tree, pruned like the docs symbol walk.

    A command file is one command; dunder and underscore-prefixed files are the package
    plumbing and shared helpers, not commands anyone invokes by name.
    """
    commands: list[Path] = []
    for directory, subdirectories, names in walk_repository(repo_root):
        subdirectories[:] = [name for name in subdirectories if name not in SKIP_DIRECTORIES]
        base = Path(directory)
        if base.name != "commands" or base.parent.name != "management":
            continue
        commands.extend(base / name for name in names if name.endswith(".py") and not name.startswith("_"))
    return sorted(commands)


def _check_env_example(repo_root: Path, corpus: str) -> Iterable[Violation]:
    """Keys in the root .env.example that nothing explains.

    A `#` comment counts as the documentation -- self-documenting config is the desired
    end state, so the rule must never punish it. It may trail the key's own line, or head
    the CONTIGUOUS block of keys the line sits in: a section comment explains its whole
    section, and crediting only the first key beneath it fired on nine of the ten
    GOOGLE_ADS_* keys in allegro-it-services, under an eight-line comment that documents
    them all. A blank line is a section boundary and ends the credit. The
    single-root-.env convention is the estate's; a repo with per-package env files is out
    of scope here.
    """
    env_example = repo_root / ".env.example"
    if not env_example.is_file():
        return

    lines = read_lines(env_example)
    for index, line in enumerate(lines):
        match = ENV_KEY.match(line)
        if not match:
            continue
        key = match.group(1)
        if key in corpus:
            continue
        if "#" in line[match.end() :]:
            continue
        block_head = index
        while block_head > 0 and ENV_KEY.match(lines[block_head - 1]):
            block_head -= 1
        if block_head > 0 and lines[block_head - 1].lstrip().startswith("#"):
            continue
        yield Violation(
            path=env_example,
            line=index + 1,
            rule="docs-uncovered-env",
            message=(
                f"'{key}' is an .env.example key with no comment and no mention in any "
                f"documentation. A bare key makes the next operator guess -- comment it "
                f"where it stands, or document it in the docs tree."
            ),
        )


def _check_route_inventories(repo_root: Path, corpus: str, config: CheckConfig) -> Iterable[Violation]:
    for relative, pattern in config.docs_route_inventories:
        inventory = repo_root / relative
        if not inventory.is_file():
            continue
        compiled = re.compile(pattern)
        if compiled.groups < 1:
            raise ValueError(
                f"docsRouteInventories pattern {pattern!r} has no capture group; group 1 must yield the route token."
            )
        lines = read_lines(inventory)
        for index, line in enumerate(lines):
            for match in compiled.finditer(line):
                route = match.group(1)
                if route in corpus:
                    continue
                if line_exemption_reason(lines, index, "docs-uncovered-route"):
                    continue
                yield Violation(
                    path=inventory,
                    line=index + 1,
                    rule="docs-uncovered-route",
                    message=(
                        f"'{route}' is a declared public route no documentation names. A "
                        f"page users can reach deserves a page that explains it -- name it "
                        f"in the docs tree or registry, or mark 'standards: "
                        f"docs-uncovered-route exempt -- <why>' on a comment line above its "
                        f"inventory entry."
                    ),
                )

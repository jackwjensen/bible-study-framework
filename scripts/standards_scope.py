#!/usr/bin/env python3
"""Which files the scanner reads, and which it refuses to.

Split out of standards_core.py when the workflow and .env.example predicates pushed it
past the pack's own 500-line limit. The seam is the subject that grew: "which files are
in scope" changes when a new file type enters scope or a new generated directory needs
excluding, while the rest of core changes when the domain vocabulary does.

Two decisions here are load-bearing and easy to get wrong:

* Compose files, workflows and .env.example are matched by FILENAME, never by adding
  ".yml" to SOURCE_SUFFIXES. The suffix route would silently pull in every Kubernetes
  manifest and OpenAPI spec in the tree, and would apply the file-length rule to stack
  definitions where it means nothing.
* Exclusions are matched against the REPO-RELATIVE path, never the absolute one. A repo
  that happens to live under a directory called "build" or "dist" would otherwise have
  every one of its files silently skipped.

Source of truth: engineering-standards/scripts/standards_scope.py
"""

from __future__ import annotations

import re
from pathlib import Path

from standards_core import CheckConfig

SOURCE_SUFFIXES = (
    ".cs", ".razor", ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".php",
)

# Which files are in scope is this module's job, so the predicates stay here even though
# the RULES live in standards_compose.py -- that module imports them, and moving them
# there instead would make should_check depend on the rules it is supposed to gate.
#
# Matched by filename rather than by suffix so that putting ".yml" in scope does not
# silently drag in every Kubernetes manifest and OpenAPI spec in the tree.
COMPOSE_FILENAME = re.compile(r"^docker-compose[.\w-]*\.ya?ml$", re.IGNORECASE)

# A GitHub Actions workflow, in scope only for the deploy-gate rule. Matched on the
# containing directory as well as the suffix, since ".github/workflows" is the only place
# these live and a stray root-level "deploy.yml" is not one.
WORKFLOW_DIRECTORY = "/.github/workflows/"

# The committed env template. Never `.env` itself -- that holds real secrets and is never
# read by anything here.
ENV_EXAMPLE_FILENAME = re.compile(r"^\.env\.example$", re.IGNORECASE)


def is_workflow(path: Path, repo_root: Path) -> bool:
    """True for a GitHub Actions workflow file."""
    if path.suffix.casefold() not in (".yml", ".yaml"):
        return False
    return WORKFLOW_DIRECTORY in "/" + path.relative_to(repo_root).as_posix()

# The JavaScript family, where one set of regexes serves every member. TypeScript is in
# here too: the sentinel traps are runtime behaviour, identical with or without types.
SCRIPT_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

# Paths never checked: machine-generated, vendored, or build output.
EXCLUDED_PATH_FRAGMENTS = (
    "/obj/", "/bin/", "/node_modules/", "/vendor/", "/migrations/", "/.git/",
    "/dist/", "/build/", "/__pycache__/", "/.venv/", "/venv/", "/staticfiles/",
    "/wwwroot/lib/", "/.next/", "/coverage/",
)
EXCLUDED_NAME_FRAGMENTS = (".min.", ".g.", ".designer.", ".generated.")


def should_check(path: Path, repo_root: Path, config: CheckConfig) -> bool:
    """Decide whether a file is in scope.

    Exclusions are matched against the REPO-RELATIVE path, never the absolute one: a repo
    that happens to live under a directory called "build" or "dist" would otherwise have
    every one of its files silently skipped.
    """
    in_scope = (
        path.suffix in SOURCE_SUFFIXES
        or COMPOSE_FILENAME.match(path.name)
        or ENV_EXAMPLE_FILENAME.match(path.name)
        or is_workflow(path, repo_root)
    )
    if not in_scope:
        return False

    lowered = "/" + path.relative_to(repo_root).as_posix().casefold() + "/"

    excluded = EXCLUDED_PATH_FRAGMENTS + config.extra_excluded_fragments
    if any(fragment.casefold() in lowered for fragment in excluded):
        return False

    return not any(fragment in path.name.casefold() for fragment in EXCLUDED_NAME_FRAGMENTS)

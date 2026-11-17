#!/usr/bin/env python3
"""Finding the PROJECTS in a repository, and what tooling each one declares.

Split out of standards_gates.py on 2026-08-27, when wiring the pay-down gate put that file
at 508 of its own 500 lines. The seam is the question each half answers: this module asks
"what is in this repository and what does it say it uses", while standards_gates asks "what
therefore runs, and when". They change for different reasons -- a new project layout moves
this file, a new stage or gate moves that one.

Re-exported by standards_gates, because migrate.py and test_standards_gate.py both reach
for these names there and moving a function is not a reason to break their imports.

Source of truth: engineering-standards/engineering_standards/standards_projects.py
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

from standards_tree import walk_repository

# How deep to look for a nested `pyproject.toml` or an EF `Migrations/` directory, and what
# never to look inside. Shared with migrate.py, which discovers the same project shapes.
PROJECT_SEARCH_DEPTH = 3
VENDOR_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "site-packages",
        "__pycache__",
        "build",
        "dist",
        "obj",
        "bin",
    }
)


def declared_tools(project: Path) -> dict:
    """The `[tool.*]` table names a pyproject.toml actually declares.

    PARSED, not substring-matched. The previous version asked `"[tool.ruff" in <raw text>`,
    which is true of a COMMENT mentioning the section -- and that is not a hypothetical: this
    repo's own pyproject.toml says "Deliberately NO [tool.ruff] section", and the substring
    check duly enabled the ruff gate and failed the build on findings the repo had never
    opted into. A comment turning a gate on is the same class of bug as a comment turning one
    off, and the fix is to stop reading config with a regex.
    """
    try:
        with (project / "pyproject.toml").open("rb") as handle:
            return tomllib.load(handle).get("tool", {})
    except (OSError, tomllib.TOMLDecodeError) as parse_error:
        # Loud, because the consequence is silently fewer gates. A malformed pyproject.toml
        # would break ruff and pytest themselves anyway, so this is a real defect either way.
        print(
            f"standards: could not read {project / 'pyproject.toml'} ({parse_error}); "
            f"its ruff/pytest gates will NOT run",
            file=sys.stderr,
        )
        return {}


def python_projects(repo_root: Path) -> list[Path]:
    """Every directory holding a `pyproject.toml`, root or nested.

    Walked with pruning rather than `rglob`, because `rglob` descends into `node_modules`
    and `.venv` -- tens of thousands of directories, several of which contain a
    `pyproject.toml` belonging to a dependency. Gating on a third-party package's test
    suite would be absurd, and slow enough to notice.
    """
    found: list[Path] = []
    for directory, subdirectories, filenames in walk_repository(repo_root):
        here = Path(directory)
        if len(here.relative_to(repo_root).parts) >= PROJECT_SEARCH_DEPTH:
            subdirectories.clear()
        subdirectories[:] = [name for name in subdirectories if name not in VENDOR_DIRECTORIES]
        if "pyproject.toml" in filenames:
            found.append(here)
    return sorted(found)


def entity_framework_projects(repo_root: Path) -> list[Path]:
    """Every project directory holding a Migrations/ with generated migration code.

    The single definition, shared by the `ef model sync` gate here and by migrate.py --
    keyed on a Migrations/ directory rather than on the .sln, because a solution with no
    DbContext has nothing to migrate and `dotnet ef` would just error.

    The result is the PROJECT directory, not the repo root: `dotnet ef` resolves its
    project from the working directory, and every .NET repo in this estate keeps the
    .csproj one level down (InvoTrack/InvoTrack.csproj), so running at the root fails with
    "No project was found". Measured in InvoTrack on 2026-08-14 -- the first version of
    migrate.py did exactly that and reported a failure in a repo whose migrations were fine.
    """
    projects: list[Path] = []
    for directory, subdirectories, _ in walk_repository(repo_root):
        here = Path(directory)
        if "Migrations" in subdirectories and any((here / "Migrations").glob("*.cs")):
            projects.append(here)
        if len(here.relative_to(repo_root).parts) >= PROJECT_SEARCH_DEPTH:
            subdirectories.clear()
        subdirectories[:] = [name for name in subdirectories if name not in VENDOR_DIRECTORIES]
    return sorted(projects)


def python_interpreter(project: Path, repo_root: Path) -> str:
    """The project's own virtualenv where it has one, else the repo's, else this one.

    A project's ruff and pytest live in its venv, and `sys.executable` here is whatever
    interpreter started the hook -- on Windows usually a system Python that has neither.
    Running that one would report "No module named ruff" against a perfectly healthy repo,
    so a venv is preferred wherever one exists. The project is checked before the repo root
    because a monorepo's backend owns its own environment.
    """
    candidates = [
        base / name / executable
        for base in dict.fromkeys((project, repo_root))
        for name in (".venv", "venv")
        for executable in (Path("Scripts") / "python.exe", Path("bin") / "python")
    ]
    found = next((path for path in candidates if path.is_file()), None)
    return str(found) if found else sys.executable


def js_runner(repo_root: Path) -> str:
    """The package manager this repo actually uses, decided by its lockfile.

    Hardcoding `pnpm` was wrong twice over. On a machine without pnpm it raises
    FileNotFoundError, which run_gate treats as "toolchain absent" and skips -- so an npm
    repo's lint and type-check gates vanish silently, which is the failure mode this whole
    pack exists to prevent. And where pnpm *is* installed it runs anyway, against a
    node_modules another manager laid out.

    `run` is always passed explicitly: `npm type-check` is not a command, and relying on
    pnpm's bare-script shorthand is what made the two look interchangeable.
    """
    lockfiles = (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("package-lock.json", "npm"),
    )
    name = next(
        (runner for lockfile, runner in lockfiles if (repo_root / lockfile).is_file()),
        "npm",
    )
    # These ship as .cmd shims on Windows; the bare name is not executable without a shell,
    # so subprocess would raise FileNotFoundError and skip the gate silently.
    return f"{name}.cmd" if os.name == "nt" else name

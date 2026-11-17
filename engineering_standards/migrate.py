#!/usr/bin/env python3
"""Migrations. The one command, whatever the stack underneath.

    python engineering_standards/migrate.py                 # what stack, what is pending  (read-only)
    python engineering_standards/migrate.py new AddInvoiceVat
    python engineering_standards/migrate.py apply           # against the LOCAL dev database

WHY THIS EXISTS. Migrations were the last workflow in the pack documented only as prose:
`claude/rules/migrations.md` explained the discipline and then named a different command
per stack, in a repo whose central rule is that a standard restated in two layers will
drift. `verify.py` had already closed that hole for build/test/lint; this closes it for the
one workflow whose first real execution is against production.

The value is not the typing it saves -- it is that "how do I add a migration here?" has a
findable answer that does not depend on knowing whether this repo is EF Core or Django, and
cannot disagree with a README that was written when it was the other one.

WHAT IT DELIBERATELY DOES NOT DO. It never touches a remote database, and it has no flag
that would let it. Production migrations in this estate run from the deploy, on the server,
and a local tool that could reach production is a local tool that eventually will -- see
PUBLISH_NOTES.md for the 2026-05-28 incident where an unattended migration took a site down.
`apply` refuses outright when the environment names a production target.

Exit codes: 0 = done, 1 = the underlying command failed, 2 = bad invocation.

Source of truth: engineering-standards/engineering_standards/migrate.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_gates import (  # noqa: E402  (path set above)
    PROJECT_SEARCH_DEPTH,
    VENDOR_DIRECTORIES,
    entity_framework_projects,
    python_interpreter,
)
from standards_git import repository_root  # noqa: E402  (same reason)
from standards_tree import walk_repository  # noqa: E402  (same reason)

# Environment values that mean "this is not your laptop". Checked before anything that
# writes: the whole point of a single entry point is that the guard is in one place rather
# than in each developer's memory.
PRODUCTION_MARKERS = ("production", "prod", "staging", "live")
ENVIRONMENT_VARIABLES = ("ASPNETCORE_ENVIRONMENT", "DJANGO_SETTINGS_MODULE", "APP_ENV", "ENV")


@dataclass(frozen=True)
class Stack:
    """One migration toolchain found in this repo."""

    name: str
    directory: Path
    status_command: list[str]
    apply_command: list[str]

    def new_command(self, migration_name: str) -> list[str]:
        raise NotImplementedError


@dataclass(frozen=True)
class DjangoStack(Stack):
    def new_command(self, migration_name: str) -> list[str]:
        # -n names the migration file; Django otherwise invents one from the operations,
        # which is how a repo ends up with 0007_auto_20260813_1142.py and no idea what it did.
        return [*self.status_command[:2], "makemigrations", "-n", migration_name]


@dataclass(frozen=True)
class AlembicStack(Stack):
    def new_command(self, migration_name: str) -> list[str]:
        return [*self.status_command[:3], "revision", "--autogenerate", "-m", migration_name]


@dataclass(frozen=True)
class EntityFrameworkStack(Stack):
    def new_command(self, migration_name: str) -> list[str]:
        return ["dotnet", "ef", "migrations", "add", migration_name]


def find_directories(repo_root: Path, filename: str) -> list[Path]:
    """Every directory at or under the root holding `filename`, pruned of vendor trees."""
    found: list[Path] = []
    for directory, subdirectories, filenames in walk_repository(repo_root):
        here = Path(directory)
        if len(here.relative_to(repo_root).parts) >= PROJECT_SEARCH_DEPTH:
            subdirectories.clear()
        subdirectories[:] = [name for name in subdirectories if name not in VENDOR_DIRECTORIES]
        if filename in filenames:
            found.append(here)
    return sorted(found)


def discover_stacks(repo_root: Path) -> list[Stack]:
    """Every migration toolchain in this repo, in the order they should be reported.

    A repo may genuinely have none -- allegroit-dk and prototypes are static sites, and
    reporting that plainly is the correct outcome rather than an error.
    """
    stacks: list[Stack] = []

    for project in find_directories(repo_root, "manage.py"):
        interpreter = python_interpreter(project, repo_root)
        stacks.append(
            DjangoStack(
                name="Django",
                directory=project,
                status_command=[interpreter, "manage.py", "showmigrations", "--plan"],
                apply_command=[interpreter, "manage.py", "migrate"],
            )
        )

    # Alembic, which is NOT a hypothetical: sourcetext.ai is the estate's one Alembic repo,
    # and the first version of this tool reported "no migrations" there. A migration tool
    # that is silent about a repo's migrations is worse than no tool -- it answers the
    # question wrongly instead of not answering it.
    for project in find_directories(repo_root, "alembic.ini"):
        interpreter = python_interpreter(project, repo_root)
        stacks.append(
            AlembicStack(
                name="Alembic",
                directory=project,
                status_command=[interpreter, "-m", "alembic", "current", "--verbose"],
                apply_command=[interpreter, "-m", "alembic", "upgrade", "head"],
            )
        )

    # Discovery is shared with the `ef model sync` verify gate -- see the docstring on
    # entity_framework_projects in standards_gates.py for why it keys on Migrations/ and
    # returns the project directory rather than the repo root.
    for project in entity_framework_projects(repo_root):
        stacks.append(
            EntityFrameworkStack(
                name="EF Core",
                directory=project,
                status_command=["dotnet", "ef", "migrations", "list"],
                apply_command=["dotnet", "ef", "database", "update"],
            )
        )

    return stacks


def production_target() -> str | None:
    """The environment variable naming a non-local target, or None.

    Deliberately a substring test over a small set of names: the failure this prevents is
    running a schema change against a live database, and being over-cautious costs an
    explicit unset, while being under-cautious costs an outage.
    """
    for variable in ENVIRONMENT_VARIABLES:
        value = os.environ.get(variable, "")
        if any(marker in value.casefold() for marker in PRODUCTION_MARKERS):
            return f"{variable}={value}"
    return None


def run(command: list[str], directory: Path, repo_root: Path) -> int:
    """Run one migration command, having first said exactly what it is."""
    where = directory.relative_to(repo_root).as_posix() or "."
    print(f"  $ {' '.join(command)}   [in {where}]")
    try:
        return subprocess.run(command, cwd=directory, shell=False, check=False).returncode  # noqa: S603  (the project's own declared migration tool, a fixed argv, shell=False)
    except FileNotFoundError:
        print(f"  {command[0]} is not installed here -- skipped", file=sys.stderr)
        return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Create or apply this repository's migrations.")
    parser.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=("status", "new", "apply"),
        help="status (default, read-only), new <Name>, or apply to the local database",
    )
    parser.add_argument("name", nargs="?", help="Migration name, required by `new`")
    parser.add_argument("--root", default=None, help="Repository root (default: discover from cwd)")
    arguments = parser.parse_args(argv)

    root = Path(arguments.root).resolve() if arguments.root else repository_root(Path.cwd())
    if root is None or not root.is_dir():
        print("error: not inside a git repository, and no valid --root given", file=sys.stderr)
        return 2

    if arguments.action == "new" and not arguments.name:
        print("error: `new` needs a migration name, e.g. migrate.py new AddInvoiceVat", file=sys.stderr)
        return 2

    stacks = discover_stacks(root)
    if not stacks:
        print("migrate: this repository has no migrations (no manage.py, no Migrations/)")
        return 0

    if arguments.action == "apply":
        target = production_target()
        if target is not None:
            print(
                f"migrate: refusing to apply -- the environment names a non-local target "
                f"({target}).\n"
                f"         Production migrations run from the deploy, on the server. If this "
                f"IS your laptop, unset it for this shell and try again.",
                file=sys.stderr,
            )
            return 2

    failures = 0
    for stack in stacks:
        print(f"\nmigrate: {stack.name}")
        if arguments.action == "status":
            command = stack.status_command
        elif arguments.action == "new":
            command = stack.new_command(arguments.name)
        else:
            command = stack.apply_command
        failures += 1 if run(command, stack.directory, root) != 0 else 0

    if failures:
        print(f"\nmigrate: {failures} command(s) failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

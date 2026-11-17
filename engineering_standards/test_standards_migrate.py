"""Cases for migrate.py's stack discovery.

The EF Core case is pinned because it was already wrong once: the first version handed
`dotnet ef` the repo root as its working directory, and every .NET repo in this estate
keeps the .csproj one level down (InvoTrack/InvoTrack.csproj) -- so the tool reported
"No project was found" in a repo whose migrations were fine. The stack's directory must
be the project directory, the parent of Migrations/.

Source of truth: engineering-standards/engineering_standards/test_standards_migrate.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from migrate import discover_stacks, entity_framework_projects
from standards_selftest import run_module_tests


def make_ef_project(root: Path, relative: str) -> Path:
    """A minimal EF Core project: a .csproj beside a Migrations/ with generated code."""
    project = root / relative
    (project / "Migrations").mkdir(parents=True)
    (project / f"{project.name}.csproj").write_text("<Project />", encoding="utf-8")
    (project / "Migrations" / "20260101000000_Init.cs").write_text("// x", encoding="utf-8")
    return project


def test_ef_stack_runs_in_the_project_directory_not_the_repo_root() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = make_ef_project(root, "InvoTrack")
        stacks = [stack for stack in discover_stacks(root) if stack.name == "EF Core"]
        assert [stack.directory for stack in stacks] == [project]


def test_ef_discovery_finds_each_project_once_and_ignores_vendor_trees() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        first = make_ef_project(root, "AppOne")
        second = make_ef_project(root, "src/AppTwo")
        make_ef_project(root, "node_modules/some-package")
        assert entity_framework_projects(root) == sorted([first, second])


def test_a_migrations_directory_with_no_generated_code_is_not_a_stack() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "InvoTrack" / "Migrations").mkdir(parents=True)
        assert entity_framework_projects(root) == []


def test_a_repo_with_no_migrations_reports_no_stacks() -> None:
    with tempfile.TemporaryDirectory() as directory:
        assert discover_stacks(Path(directory)) == []


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "migrate discovery cases"))

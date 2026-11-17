#!/usr/bin/env python3
"""The pack's files are the PACK's source and the CONSUMER's dependency.

Both directions matter, and the dangerous one is the second: if `is_the_pack_itself` ever
answers True in a consumer the gates merely get noisier, but if it answers False in the pack
then the pack silently stops scanning and testing the ~100 modules where every rule in the
estate is written -- and nothing about that looks wrong from the outside.

Source of truth: engineering-standards/engineering_standards/test_standards_pack_identity.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_config import CheckConfig  # noqa: E402
from standards_gates import python_gates  # noqa: E402
from standards_pack_identity import (  # noqa: E402
    PACK_DIRECTORY,
    PACK_SELF_MARKER,
    is_foreign_pack_file,
    is_the_pack_itself,
)
from standards_scope import should_check  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402


def make_consumer(root: Path) -> Path:
    """A repo that has adopted the pack: the package's top level, never its `dev/`."""
    repo = root / "consumer"
    (repo / PACK_DIRECTORY).mkdir(parents=True)
    (repo / PACK_DIRECTORY / "standards_core.py").write_text("x = 1" + chr(10), encoding="utf-8")
    (repo / PACK_DIRECTORY / "test_standards_core.py").write_text("x = 1" + chr(10), encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("y = 2" + chr(10), encoding="utf-8")
    return repo


def make_pack(root: Path) -> Path:
    """The pack's own repository: identical, plus the `dev/` that is never synced."""
    repo = make_consumer(root)
    marker = repo / PACK_DIRECTORY / PACK_SELF_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("MANIFEST = ()" + chr(10), encoding="utf-8")
    return repo


def test_a_consumers_scan_skips_the_pack_but_keeps_its_own_source() -> None:
    with tempfile.TemporaryDirectory() as tree:
        repo = make_consumer(Path(tree))
        config = CheckConfig()

        assert not should_check(repo / PACK_DIRECTORY / "standards_core.py", repo, config), (
            "the pack's module is not the consuming repo's source to measure"
        )
        assert should_check(repo / "src" / "app.py", repo, config), "the repo's own source is still checked"


def test_the_pack_still_scans_itself() -> None:
    """The exception that must never invert: these files are where the rules are written."""
    with tempfile.TemporaryDirectory() as tree:
        repo = make_pack(Path(tree))

        assert is_the_pack_itself(repo)
        assert should_check(repo / PACK_DIRECTORY / "standards_core.py", repo, CheckConfig()), (
            "the pack must keep checking its own modules, or it stops enforcing its own rules"
        )


def test_a_consumers_python_gates_exclude_the_pack() -> None:
    """pytest under `-x` would otherwise let a PACK case fail a repo whose own code is fine."""
    with tempfile.TemporaryDirectory() as tree:
        repo = make_consumer(Path(tree))
        (repo / "pyproject.toml").write_text(
            "[project]" + chr(10) + 'name = "consumer"' + chr(10) + "[tool.ruff]" + chr(10),
            encoding="utf-8",
        )
        (repo / "tests").mkdir()

        commands = {gate.name: gate.command for gate in python_gates(repo)}

        assert f"--ignore={PACK_DIRECTORY}" in commands["pytest"], (
            "the consumer's suite must not collect the pack's ~900 cases"
        )
        assert PACK_DIRECTORY in commands["ruff"], "the consumer cannot fix lint in files the next sync overwrites"


def test_the_packs_own_python_gates_are_not_excluded() -> None:
    with tempfile.TemporaryDirectory() as tree:
        repo = make_pack(Path(tree))
        (repo / "pyproject.toml").write_text(
            "[project]" + chr(10) + 'name = "pack"' + chr(10) + "[tool.ruff]" + chr(10),
            encoding="utf-8",
        )
        (repo / "tests").mkdir()

        commands = {gate.name: gate.command for gate in python_gates(repo)}

        assert not any(PACK_DIRECTORY in argument for argument in commands["pytest"]), (
            "the pack's own suite is the whole point of the pack's gate"
        )
        assert not any(PACK_DIRECTORY in argument for argument in commands["ruff"])


def test_a_nested_project_is_never_handed_a_path_it_cannot_see() -> None:
    """The pack lands at the ROOT, so a Django half in packages/backend/ cannot reach it."""
    with tempfile.TemporaryDirectory() as tree:
        repo = make_consumer(Path(tree))
        backend = repo / "packages" / "backend"
        backend.mkdir(parents=True)
        (backend / "pyproject.toml").write_text(
            "[project]" + chr(10) + 'name = "backend"' + chr(10) + "[tool.ruff]" + chr(10),
            encoding="utf-8",
        )
        (backend / "tests").mkdir()

        for gate in python_gates(repo):
            assert not any(PACK_DIRECTORY in argument for argument in gate.command), (
                "a nested project must not be given --ignore for a directory above it"
            )


def test_the_directory_name_is_read_off_the_installation() -> None:
    """Never a literal: a stale path is what silently unhooked the scanner during the move."""
    assert Path(__file__).resolve().parent.name == PACK_DIRECTORY

    with tempfile.TemporaryDirectory() as tree:
        repo = make_consumer(Path(tree))
        assert is_foreign_pack_file(Path(PACK_DIRECTORY) / "standards_core.py", repo)
        assert not is_foreign_pack_file(Path("src") / "app.py", repo)


# Two cases moved to `dev/test_pack_self.py`: that THIS repository is recognised as the pack,
# and that the manifest and this module agree on the directory name. Both assert about the
# supplier, so both failed by design in every repo this file is shipped to -- and the second
# imported `pack_manifest`, which `sync_pack.py` never copies. The synthetic cases above keep
# covering the module everywhere; only the self-assertions had to stay home.


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "pack identity"))

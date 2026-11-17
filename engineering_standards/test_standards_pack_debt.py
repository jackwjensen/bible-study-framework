#!/usr/bin/env python3
"""Cases for `pack-no-debt`.

The asymmetry is the whole rule, so both halves are pinned: every artefact below is
LEGITIMATE in a consuming repo -- the ratchet exists so the pack can land without a wall of
red -- and debt in the pack, where the rules themselves are written.

The most important case is the last one, which runs against the real tree: the synthetic
fixtures all build their own pack marker, so they would keep passing while this repository
quietly acquired a baseline.

Run: python test_standards_pack_debt.py   (or pytest)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_pack_debt import check_pack_debt  # noqa: E402
from standards_pack_identity import PACK_DIRECTORY, PACK_SELF_MARKER  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

# A stand-in, never the pack's real file. Nothing below asserts on this CONTENT -- the
# fixtures only need something to exist at the shipped path -- and reading the real one made
# this module unimportable in the repos it is shipped to, where `python/ruff-standards.toml`
# is the supplier's path and not theirs. A literal keeps the suite hermetic as well as
# portable. The case that does need the real tree is `dev/test_pack_self.py`, which stays here.
RUFF_STANDARD = "[lint]" + chr(10) + 'select = ["E", "F"]' + chr(10)
# What a compliant pyproject.toml says: a POINTER at the shipped file, never a copy of it.
EXTENDS = "[tool.ruff]" + chr(10) + 'extend = "python/ruff-standards.toml"' + chr(10)


def make_pack(root: Path) -> Path:
    """A minimal but COMPLIANT pack: adopts ruff, and matches what it ships."""
    repo = root / "pack"
    marker = repo / PACK_DIRECTORY / PACK_SELF_MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text("MANIFEST = ()" + chr(10), encoding="utf-8")
    (repo / "python").mkdir()
    (repo / "python" / "ruff-standards.toml").write_text(RUFF_STANDARD, encoding="utf-8")
    (repo / "pyproject.toml").write_text(EXTENDS, encoding="utf-8")
    return repo


def make_consumer(root: Path) -> Path:
    """The same shape WITHOUT `dev/` -- a repo that adopted the pack."""
    repo = root / "consumer"
    (repo / PACK_DIRECTORY).mkdir(parents=True)
    (repo / "python").mkdir()
    (repo / "python" / "ruff-standards.toml").write_text(RUFF_STANDARD, encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]" + chr(10), encoding="utf-8")
    return repo


def rules(repo: Path) -> list[str]:
    return [violation.rule for violation in check_pack_debt(repo)]


def test_a_compliant_pack_is_clean() -> None:
    with tempfile.TemporaryDirectory() as tree:
        assert rules(make_pack(Path(tree))) == []


def test_a_baseline_in_the_pack_is_debt() -> None:
    with tempfile.TemporaryDirectory() as tree:
        repo = make_pack(Path(tree))
        (repo / ".standards-baseline.json").write_text("{}", encoding="utf-8")
        assert "pack-no-debt" in rules(repo)


def test_analyzer_debt_props_in_the_pack_is_debt() -> None:
    with tempfile.TemporaryDirectory() as tree:
        repo = make_pack(Path(tree))
        (repo / "Directory.Build.debt.props").write_text("<Project />", encoding="utf-8")
        assert "pack-no-debt" in rules(repo)


def test_switching_a_shipped_check_off_is_debt() -> None:
    """A rule that is wrong gets changed where every repo sees it, never silenced here."""
    with tempfile.TemporaryDirectory() as tree:
        repo = make_pack(Path(tree))
        (repo / ".standards.json").write_text(json.dumps({"checkQueryShape": False}), encoding="utf-8")
        assert "pack-no-debt" in rules(repo)


def test_dropping_the_ruff_block_is_caught() -> None:
    """The one edit that makes the push gate greener while checking ~100 modules less.

    Deleting `[tool.ruff]` removes the ruff gate `python_gates` builds, so ruff's findings
    stop blocking and nothing else notices. That is why the DECLARATION is checked here even
    though the findings enforce themselves.
    """
    with tempfile.TemporaryDirectory() as tree:
        repo = make_pack(Path(tree))
        (repo / "pyproject.toml").write_text("[project]" + chr(10), encoding="utf-8")
        assert "pack-no-debt" in rules(repo)


def test_spelling_the_settings_out_instead_of_extending_is_caught() -> None:
    """The regression this rule exists to prevent, and the one it used to BE.

    Writing the rules into pyproject.toml works -- ruff is perfectly happy -- and creates a
    second copy of the standard, free to drift from the one every other repo receives. The
    earlier version of this rule compared the two copies, which is DRY's problem restated
    rather than solved; there must be one copy, and this is what keeps it that way.
    """
    with tempfile.TemporaryDirectory() as tree:
        repo = make_pack(Path(tree))
        (repo / "pyproject.toml").write_text("[tool.ruff]" + chr(10) + "line-length = 120" + chr(10), encoding="utf-8")
        assert "pack-no-debt" in rules(repo)


def test_extending_somewhere_other_than_the_shipped_file_is_caught() -> None:
    with tempfile.TemporaryDirectory() as tree:
        repo = make_pack(Path(tree))
        (repo / "pyproject.toml").write_text(
            "[tool.ruff]" + chr(10) + 'extend = "my-own-rules.toml"' + chr(10), encoding="utf-8"
        )
        assert "pack-no-debt" in rules(repo)


def test_a_consuming_repo_may_carry_every_one_of_them() -> None:
    """The ratchet is why the pack can land at all; this rule must never reach a consumer."""
    with tempfile.TemporaryDirectory() as tree:
        repo = make_consumer(Path(tree))
        (repo / ".standards-baseline.json").write_text("{}", encoding="utf-8")
        (repo / "Directory.Build.debt.props").write_text("<Project />", encoding="utf-8")
        (repo / ".standards.json").write_text(json.dumps({"checkQueryShape": False}), encoding="utf-8")

        assert rules(repo) == [], (
            "a consuming repo is allowed its baseline, its analyzer debt and its tuning -- "
            "grandfathering is what lets the pack land without a wall of red"
        )


# `test_this_actual_repository_carries_no_debt` moved to `dev/test_pack_self.py`: it asserts
# the PACK carries no debt, which is false by design in a consumer -- the ratchet grants them
# exactly the baseline and tuning it denies their supplier.


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "pack debt"))

#!/usr/bin/env python3
"""A test file pytest collects nothing from is not a test file. It is a decoy.

FOUND 2026-08-27. `test_standards_exemptions.py` held 21 assertions, all of them passing,
none of them running: its cases lived in a table walked by `main()`, guarded behind
`if __name__ == "__main__"`, and no function in the file was named `test_*`. pytest
imported the module, found nothing to collect, and moved on. `verify.py` runs pytest at
the push stage and CI runs pytest, so the module executed only when somebody typed
`python test_standards_exemptions.py` by hand.

What it was guarding makes that worse rather than better. The exemption reason floor is
the only thing standing between an exemption and a shrug, and that file's own docstring
says a floor that silently accepted everything "would look exactly like a working one --
that failure mode has already hit this pack twice". It had now hit the test as well.

`test_standards_query.py` was a milder version of the same shape: its cases run at import,
so they did execute, but they were reported as zero tests and a failure would have arrived
as a collection error rather than as a named failing test.

This module is the guard, because fixing those two files fixes two symptoms. A pack whose
whole argument is that a rule needs a mechanism cannot leave its own test suite relying on
everyone remembering to name a function correctly.

Run: python test_suite_integrity.py   (or pytest)
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PACK_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(PACK_DIRECTORY))

from standards_selftest import run_module_tests  # noqa: E402  (path set above)

# pytest's default discovery: a module named test_*.py, a function named test_*.
TEST_FILE_GLOB = "test_*.py"
TEST_FUNCTION_PREFIX = "test_"


def collectable_test_functions(path: Path) -> list[str]:
    """Top-level `test_*` functions in a module -- what pytest would actually collect.

    Parsed rather than imported: importing to find out would run the module's import-time
    side effects, which is the very thing that made one of these files look covered.

    Read as utf-8-SIG because two modules in this pack currently carry a UTF-8 BOM, which
    CPython tolerates when it executes a file and `ast.parse` rejects outright. A guard
    that crashed on the pack's own sources would be one more thing to switch off.
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(TEST_FUNCTION_PREFIX)
    ]


def test_every_test_file_exposes_at_least_one_test() -> None:
    """No test module may be silently uncollected.

    The failure this prevents is invisible by construction: the suite goes green, the file
    count looks right, and the assertions never execute. Nothing else in the suite can
    notice, because there is nothing to notice -- an absence produces no output.
    """
    decoys = [path.name for path in sorted(PACK_DIRECTORY.glob(TEST_FILE_GLOB)) if not collectable_test_functions(path)]

    assert not decoys, (
        "these test modules define no top-level test_* function, so pytest collects "
        "nothing from them and their assertions never run:\n  "
        + "\n  ".join(decoys)
        + "\n\nAdd a test_* function. For a table-driven module, the smallest honest one "
        "is a wrapper that asserts its runner returned 0."
    )


def test_this_guard_can_actually_fail(tmp_path: Path = None) -> None:
    """A guard that cannot fail is the same decoy one level up.

    Proves the detector by handing it a module shaped like the bug: a `main()` and no
    `test_*` function. Without this, a typo in the prefix constant would make the guard
    pass forever while checking nothing.
    """
    import tempfile  # noqa: PLC0415  (local by design: imported after this test builds its tree)

    with tempfile.TemporaryDirectory() as tmp:
        decoy = Path(tmp) / "test_decoy.py"
        decoy.write_text(
            "def main() -> int:\n    assert False, 'never runs'\n    return 1\n",
            encoding="utf-8",
        )
        assert collectable_test_functions(decoy) == [], "the detector must report a main()-only module as uncollectable"

        real = Path(tmp) / "test_real.py"
        real.write_text("def test_something() -> None:\n    assert True\n", encoding="utf-8")
        assert collectable_test_functions(real) == ["test_something"], (
            "the detector must find a normally-named test function"
        )


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "suite integrity"))

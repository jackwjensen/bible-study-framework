#!/usr/bin/env python3
"""Cases for the interpreter floor guard.

The case that matters most is `test_the_guard_itself_parses_on_an_old_interpreter`. Every
other test here checks that the guard says the right thing; that one checks it can be READ
at all by the interpreter it exists to warn. A guard written in syntax the stranded Python
cannot parse is not a guard -- it is a second SyntaxError, arriving before the first one is
explained, and it would fail in exactly the situation it was added for.

Run: python test_standards_runtime.py   (or pytest)
"""

from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_runtime import (  # noqa: E402
    declared_python_floor,
    require_supported_python,
    unsupported_python_message,
)
from standards_selftest import run_module_tests  # noqa: E402
from standards_versions import PYTHON_FLOOR  # noqa: E402

GUARD = Path(__file__).resolve().parent / "standards_runtime.py"


def test_the_guard_itself_parses_on_an_old_interpreter() -> None:
    """THE ONE THAT PROTECTS THE OTHERS. Pinned well below the floor on purpose.

    `ruff format` runs with `target-version = py314` and will happily emit PEP 758's
    unparenthesised `except A, B:` -- which is precisely the syntax that stranded the pack on
    3.13 and made this guard necessary. If the formatter ever reaches into this file, the
    guard stops being readable by the interpreter it is meant to speak to, and this test is
    what notices.
    """
    source = GUARD.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(GUARD), feature_version=(3, 9))
    assert tree.body, "the guard must parse to real statements under an old grammar"


def test_the_floor_is_read_from_the_versions_module_not_imported() -> None:
    """Textual, because the module holding the floor may itself be unparseable here."""
    assert declared_python_floor(GUARD.parent) == PYTHON_FLOOR


def test_an_interpreter_below_the_floor_is_refused_with_both_numbers() -> None:
    message = unsupported_python_message((3, 14), (3, 13, 3, "final", 0))
    assert message is not None
    assert "3.14" in message and "3.13.3" in message, message
    assert "Nothing was checked" in message, "the reader must not think the run was clean"


def test_an_interpreter_at_the_floor_is_accepted() -> None:
    assert unsupported_python_message((3, 14), (3, 14, 0, "final", 0)) is None


def test_an_interpreter_above_the_floor_is_accepted() -> None:
    """A floor is a MINIMUM -- a machine on the next minor must not be refused."""
    assert unsupported_python_message((3, 14), (3, 15, 1, "final", 0)) is None


def test_an_unreadable_floor_does_not_block_the_run() -> None:
    """Permissive on purpose: a pack that told us nothing has not told us to stop.

    Refusing on the strength of a file we could not open would block work over a question
    nobody asked, which is the opposite of what this guard is for.
    """
    with tempfile.TemporaryDirectory() as tree:
        assert declared_python_floor(Path(tree)) is None
        assert unsupported_python_message(None, (3, 9, 0, "final", 0)) is None


def test_this_interpreter_satisfies_the_floor_it_declares() -> None:
    """Against the real tree: the machine running these tests is at or above its own floor.

    This is the check that was missing for months. `python-support` reads DECLARATION sites
    -- CI, the Dockerfile, pyproject -- and none of them is the interpreter that actually
    executes the gate, so a developer sitting below the floor was invisible to every rule.
    """
    running_is_supported = unsupported_python_message(declared_python_floor(GUARD.parent), sys.version_info)
    assert running_is_supported is None, running_is_supported
    require_supported_python(GUARD.parent)  # and the guard agrees, without exiting


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "interpreter floor"))

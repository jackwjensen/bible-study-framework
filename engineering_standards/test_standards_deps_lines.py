"""Cases for standards_deps_lines: where a pin stands within its line and against later lines.

Run: python test_standards_deps_lines.py   (or pytest)
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_deps_declared import DOCKER, PYPI, Dependency  # noqa: E402
from standards_deps_lifecycle import Cycle  # noqa: E402
from standards_deps_lines import SUPPORT_HORIZON_DAYS, assess, major_is_due  # noqa: E402
from standards_deps_registry import Answer  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

TODAY = date(2026, 10, 2)


def test_a_lifecycle_line_is_its_cycle() -> None:
    pin = Dependency(DOCKER, "mysql", "8.4.10", Path("f"), 1)
    answer = Answer(releases=(("8.4.10", None, None), ("8.4.11", None, None), ("9.7.2", None, None)), preferred="9.7.2")
    position = assess(pin, answer, [Cycle("8.4", True, date(2032, 4, 30)), Cycle("9.7", True, None)])
    assert (position.line, position.in_line[0], position.newest[0], position.newer_line) == (
        "8.4",
        "8.4.11",
        "9.7.2",
        True,
    )
    assert position.support_end == date(2032, 4, 30) and position.has_lifecycle


def test_without_a_lifecycle_the_line_is_the_semantic_major() -> None:
    pin = Dependency(PYPI, "ruff", "==0.16.5", Path("f"), 1)
    answer = Answer(releases=(("0.16.10", "2026-09-20", None), ("0.17.0", "2026-09-28", None)))
    position = assess(pin, answer, None)
    assert (position.line, position.in_line[0], position.newer_line) == ("0.16", "0.16.10", True)
    assert position.last_shipped() == date(2026, 9, 20) and not position.has_lifecycle


def test_an_older_release_is_never_a_newer_line() -> None:
    pin = Dependency(PYPI, "x", "==3.0.0", Path("f"), 1)
    position = assess(pin, Answer(releases=(("2.9.0", None, None), ("3.0.0", None, None))), None)
    assert not position.newer_line


def test_due_is_one_year_of_support_left() -> None:
    pin = Dependency(DOCKER, "mysql", "8.4.11", Path("f"), 1)
    answer = Answer(releases=(("8.4.11", None, None), ("9.7.2", None, None)))
    soon = assess(pin, answer, [Cycle("8.4", True, date(2027, 10, 2)), Cycle("9.7", True, None)])
    later = assess(pin, answer, [Cycle("8.4", True, date(2027, 10, 3)), Cycle("9.7", True, None)])
    assert SUPPORT_HORIZON_DAYS == 365
    assert major_is_due(soon, TODAY) and not major_is_due(later, TODAY)


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "dependency lines"))

#!/usr/bin/env python3
"""Run a test module's `test_*` functions without pytest.

WHY THIS EXISTS. The test files here are pytest-shaped, but nothing in this estate can rely
on pytest being installed: the pack has no CI of its own, the machine the git hooks run on
has no pytest, and a consuming repo only gets a pytest gate if it happens to be a Python
project. Until this ran, `python engineering_standards/test_standards_encoding.py` exited 0 having
executed none of its cases -- and `python engineering_standards/test_standards_members.py` exited 0
having skipped 38. An exit code of 0 from a suite that ran nothing is indistinguishable
from one that passed, which is the exact failure mode this whole pack exists to prevent,
aimed at the pack's own tests.

Pytest still works on these files and is still the better runner when it is available. This
is the floor, not a replacement: it guarantees the cases execute *somewhere*.

Shared rather than copied into each test module on purpose. Two copies of a runner is the
duplication the pack's own DRY rule forbids, and the second copy is where the drift starts.

Source of truth: engineering-standards/engineering_standards/standards_selftest.py
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Callable


def run_module_tests(namespace: dict[str, object], label: str) -> int:
    """Call every `test_*` in `namespace`; print a tally; return a process exit code.

    Pass `globals()` from the test module. Failures are collected rather than raised so one
    broken case cannot hide the verdict on the rest -- a runner that stops at the first
    failure reports one problem when there may be ten.

    COLLECTING NOTHING IS A FAILURE, NOT A PASS. This runner exists because a suite that
    executed nothing exited 0 and looked identical to one that passed; a runner that finds no
    tests and returns 0 would reproduce that exact bug one level down. It was written that way
    first, and caught while reviewing this file. Renaming the cases, changing the prefix
    convention, or pointing this at the wrong namespace now fails loudly instead of quietly
    reporting `0/0 passed`.
    """
    tests: list[tuple[str, Callable[[], None]]] = sorted(
        (name, value) for name, value in namespace.items() if name.startswith("test_") and callable(value)
    )

    if not tests:
        print(f"FAIL  no test_* functions found -- {label} ran nothing, which is not a pass")
        return 1

    failures: list[str] = []
    for name, test in tests:
        try:
            test()
        except AssertionError as assertion:
            failures.append(f"{name}: {assertion or 'assertion failed'}")
        except Exception as error:  # noqa: BLE001  (a broken case must not abort the run)
            failures.append(f"{name}: {type(error).__name__}: {error}")

    for failure in failures:
        print(f"FAIL  {failure}")
    print(f"{len(tests) - len(failures)}/{len(tests)} {label} passed")
    return 1 if failures else 0


def _self_check() -> int:
    """Verify this runner's own contract: `python engineering_standards/standards_selftest.py`.

    The runner is infrastructure four suites depend on, and every way it can be wrong is
    silent -- it reports success by printing a tally, so a broken runner and a green suite
    look the same. These three cases are the minimum that distinguishes them, and they live
    here rather than in a test module because a test module is the thing being run.
    """

    def passing() -> None:
        return None

    def failing() -> None:
        raise AssertionError("deliberate")

    # The probes' own output is swallowed: two of them are SUPPOSED to fail, and printing
    # their FAIL lines during a passing self-check is how people learn to skim past FAIL.
    with io.StringIO() as sink, redirect_stdout(sink):
        results = (
            run_module_tests({"test_ok": passing}, "probe"),
            run_module_tests({"test_no": failing}, "probe"),
            run_module_tests({"helper": passing}, "probe"),
        )

    checks = [
        ("a passing case returns 0", results[0] == 0),
        ("a failing case returns 1", results[1] == 1),
        ("collecting nothing returns 1", results[2] == 1),
    ]

    broken = [name for name, held in checks if not held]
    for name in broken:
        print(f"FAIL  {name}")
    print(f"{len(checks) - len(broken)}/{len(checks)} runner contract checks passed")
    return 1 if broken else 0


if __name__ == "__main__":
    import sys

    sys.exit(_self_check())

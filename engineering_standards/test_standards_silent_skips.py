#!/usr/bin/env python3
"""The guard against a reader that goes quiet: every swallowed read must NAME itself.

WHY THIS FILE EXISTS. Every reader in the pack has to answer the same question when a file
will not open, and the honest answer is almost never "block" -- an unreadable file is rare,
usually a permission or a lock, and refusing every commit over one would be worse than the
thing it guards. So each reader returns a "nothing" value and carries on.

The danger is that "nothing" is indistinguishable from "nothing found". A docs page that
cannot be read becomes an empty page and passes every documentation rule; an unreadable
`.gitignore` passes the gitignore rule; a declaration that drops out of a comparison makes
the repo look consistent. Each is a rule reporting `clean` about a file it never saw -- the
failure this pack is otherwise built against, arriving through the error path rather than
the happy one.

A sweep on 2026-09-04 found nine such readers, none of which said anything. This file is
what stops the tenth, and it works structurally rather than by listing them: it PARSES the
sources and fails on any handler that swallows into a nothing-value without a warning in it.
A list of known sites would go stale the first time somebody added a reader.

Arrived here from the B3D pack, where the sweep was run first and the same nine shapes were
found in the seven modules that estate carries.

Run: python test_standards_silent_skips.py   (or pytest)
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

# `warn_unreadable` is deliberately NOT imported here. The two cases that exercise it run
# it in a SUBPROCESS, because what they assert is which STREAM it writes to -- and a
# direct call would print into pytest's own capture rather than a stream this test can
# read. An unused import kept 'for documentation' is the kind of thing this pack's own
# dead-code rules exist to remove.
from standards_selftest import run_module_tests  # noqa: E402

# A handler body that produces one of these, and says nothing, leaves the caller unable to
# tell "I could not look" from "I looked and found nothing".
NOTHING_VALUES = {"None", "False", "[]", "()", "set()", "{}", "0", '""'}

# Handlers whose silence is correct, with the reason it is correct. Narrow on purpose: each
# entry is a claim that the quiet direction is the SAFE one there, and it has to be argued.
ALLOWED_SILENT = {
    # Best-effort message decoration. The rule has already fired; this only tries to quote
    # the original bytes, and a fragment that cannot be recovered is simply not quoted.
    ("standards_encoding.py", "continue"),
    # "No overrides" is the normal case for every repo, and a MALFORMED config is reported
    # loudly elsewhere -- verified: check-source-limits exits 1 and verify.py reports the
    # gate as failed.
    ("standards_gate_config.py", "return {}"),
    # The pack's own Python floor, read out of its versions module. Documented in place: a
    # file that could not be opened has told us nothing, and refusing to run on the strength
    # of it would block work over a question nobody asked.
    ("standards_runtime.py", "return None"),
    # No global.json, or one that will not parse. "No SDK pin" is the normal case for every
    # repo that is not .NET, and the rule it gates has nothing to compare against either way.
    ("standards_versions.py", "return None"),
    # The single git wrapper. None means "git could not be asked", and every caller is
    # required to distinguish that from an empty result -- which is what the callers'
    # own docstrings do, at the point where the meaning is known.
    ("standards_git.py", "return None"),
    # "No exemption was claimed", which leaves the rule to judge the file on its content --
    # the loud direction.
    ("standards_disclosure.py", "return None"),
    ("standards_pack_update.py", "return None"),
    # `git ls-files` could not be run. None is a SIGNAL, not a result: the very next branch
    # falls back to walking the tree, on the stated grounds that a slightly generous answer
    # beats refusing to resolve anything. The handling is deliberate and adjacent.
    ("standards_symbols.py", "return None"),
}


def swallowing_handlers(path: Path) -> list[tuple[int, str]]:
    """(line, produced-value) for each handler that swallows into a nothing-value silently.

    Parsed rather than grepped, so a handler spread over several lines, or one whose warning
    sits inside an `if`, is judged on its structure rather than on its formatting.
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
    found: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue

        produced = None
        if len(node.body) == 1:
            statement = node.body[0]
            if isinstance(statement, ast.Pass):
                produced = "pass"
            elif isinstance(statement, ast.Continue):
                produced = "continue"
            elif isinstance(statement, ast.Return):
                value = ast.unparse(statement.value) if statement.value else "None"
                if value in NOTHING_VALUES:
                    produced = f"return {value}"
        if produced is None:
            continue

        # Does anything in the handler say so? A `print`, or a call to the shared helper.
        speaks = any(
            isinstance(inner, ast.Call) and ast.unparse(inner.func) in ("print", "warn_unreadable")
            for inner in ast.walk(node)
        )
        if not speaks:
            found.append((node.lineno, produced))

    return found


def test_no_reader_swallows_a_file_without_saying_so() -> None:
    """THE SWEEP, as a test. Structural, so it also covers readers not yet written."""
    offenders: list[str] = []
    for path in sorted(SCRIPTS.glob("standards_*.py")):
        for line, produced in swallowing_handlers(path):
            if (path.name, produced) in ALLOWED_SILENT:
                continue
            offenders.append(f"{path.name}:{line} -- except ... -> {produced}, silently")

    assert not offenders, (
        "these handlers turn a file the scanner could not read into a result that looks "
        "like a clean one, and say nothing:\n  "
        + "\n  ".join(offenders)
        + "\n\nEither call warn_unreadable(path, error, consequence), or add the site to "
        "ALLOWED_SILENT with the argument for why the quiet direction is safe there."
    )


def test_the_detector_would_actually_catch_one() -> None:
    """A guard that cannot fail is the decoy it exists to prevent, one level up."""
    with tempfile.TemporaryDirectory() as tmp:
        quiet = Path(tmp) / "standards_probe.py"
        quiet.write_text(
            "def read(path):\n    try:\n        return path.read_text()\n    except OSError:\n        return []\n",
            encoding="utf-8",
        )
        assert swallowing_handlers(quiet) == [(4, "return []")]

        loud = Path(tmp) / "standards_probe_loud.py"
        loud.write_text(
            "def read(path):\n"
            "    try:\n"
            "        return path.read_text()\n"
            "    except OSError as error:\n"
            "        warn_unreadable(path, error, 'the rule could not run')\n"
            "        return []\n",
            encoding="utf-8",
        )
        assert swallowing_handlers(loud) == []


def test_the_warning_reaches_stderr_and_names_the_file_and_the_cost() -> None:
    """Both halves matter. The path alone tells a reader something went wrong; the
    consequence is what tells them which rule stopped running."""
    probe = Path(tempfile.mkdtemp()) / "locked.md"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, r'%s');\n"
            "from pathlib import Path\n"
            "from standards_core import warn_unreadable\n"
            "warn_unreadable(Path(r'%s'), OSError('permission denied'), 'the docs rules could not run')"
            % (str(SCRIPTS), str(probe)),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.stdout == "", "a scanner limit is not a finding; it belongs on stderr"
    assert "locked.md" in completed.stderr
    assert "permission denied" in completed.stderr
    assert "the docs rules could not run" in completed.stderr


def test_an_unreadable_docs_page_says_so_rather_than_passing() -> None:
    """End to end through the real reader: a page that cannot be read must not read as an
    empty page that satisfies every documentation rule."""
    directory = Path(tempfile.mkdtemp())
    missing = directory / "gone.md"  # never created: read_text raises
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, r'%s');\n"
            "from pathlib import Path\n"
            "from standards_markdown import read_lines\n"
            "print('lines:', read_lines(Path(r'%s')))" % (str(SCRIPTS), str(missing)),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "lines: []" in completed.stdout, "the reader still returns empty and carries on"
    assert "gone.md" in completed.stderr, "but it must no longer do so silently"


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "silent-skip sweep"))

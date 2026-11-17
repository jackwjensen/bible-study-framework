#!/usr/bin/env python3
"""Refuse, in words, when the interpreter running the gates is below the floor they declare.

THE ONE CHECK THAT CANNOT BE A RULE. Every other standard here is enforced by a module the
scanner imports -- and an interpreter too old to run the pack is, by definition, too old to
import the rule that would have said so. On 2026-09-03 this pack adopted `ruff format` with
`target-version = "py314"`, which legitimately emits PEP 758's unparenthesised
`except OSError, ValueError:`. On the Python 3.13 that happened to be first on PATH, eight
modules stopped parsing, and the commit hook answered with a SyntaxError traceback instead
of naming a violation. The gate did not fail -- it died.

So this runs FIRST, above every pack import, and it says what is wrong in a sentence.

THE FLOOR IS READ, NEVER IMPORTED. `standards_versions` is one of the modules that may not
parse on the interpreter being judged, so importing it to find out whether importing is safe
is circular. It is parsed textually instead -- the same reason `sync_pack` reads PACK_VERSION
with a regex rather than an import.

WHY THIS FILE MUST STAY OLD-PYTHON PARSEABLE, and why a test enforces it: a guard written in
syntax the stranded interpreter cannot read is not a guard, it is a second SyntaxError. It
therefore uses no match statement, no PEP 758 except, no modern-only syntax at all, and
`test_standards_runtime` compiles it with `feature_version` pinned well below the floor to
keep it that way. That test is the whole reason this file can be trusted.

Source of truth: engineering-standards/engineering_standards/standards_runtime.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# `PYTHON_FLOOR = (3, 14)` in standards_versions.py, read as text.
PYTHON_FLOOR_PATTERN = re.compile(r"^PYTHON_FLOOR\s*=\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", re.MULTILINE)

VERSIONS_MODULE = "standards_versions.py"


def declared_python_floor(pack_directory: Path):
    """The floor this installation declares, or None when it cannot be read.

    None is deliberately permissive: a pack whose version file is missing or unreadable has
    told us nothing, and refusing to run on the strength of a file we could not open would
    block work over a question nobody asked.
    """
    versions = pack_directory / VERSIONS_MODULE
    try:
        text = versions.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = PYTHON_FLOOR_PATTERN.search(text)
    if found is None:
        return None
    return (int(found.group(1)), int(found.group(2)))


def unsupported_python_message(floor, running):
    """The refusal, or None when this interpreter is fine.

    Separated from the exit so it can be tested without a process boundary, and so the
    caller decides what to do with it.
    """
    if floor is None or running >= floor:
        return None
    return (
        "standards: this repository requires Python "
        + "%d.%d" % floor
        + " or newer, and the interpreter running the gate is "
        + "%d.%d.%d" % running[:3]
        + ".\n"
        + "       Nothing was checked. The pack's own modules are written for "
        + "%d.%d" % floor
        + " and\n"
        + "       cannot be read by this interpreter, so the gate cannot run at all.\n"
        + "       Install Python "
        + "%d.%d" % floor
        + "+ and make sure it is the `python` on PATH, or point the\n"
        + "       repository's virtualenv at it. Interpreter: "
        + sys.executable
    )


def require_supported_python(pack_directory: Path) -> None:
    """Exit with an explanation when this interpreter cannot run the pack.

    Exit code 2, matching verify.py's "bad invocation" -- this is an environment fault, not
    a finding about the code, and the two must not be confused by whatever reads the code.
    """
    message = unsupported_python_message(declared_python_floor(pack_directory), sys.version_info)
    if message is None:
        return
    sys.stderr.write(message + "\n")
    raise SystemExit(2)

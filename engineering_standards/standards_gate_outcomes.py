#!/usr/bin/env python3
"""Reading a gate's non-zero exit: was the tool absent, or did the check really fail?

Split out of `standards_gates.py` on 2026-09-05, when that module crossed the pack's own
500-line limit while gaining the `declared` guard below. The two halves are different
concerns anyway: `standards_gates` decides WHAT runs and WHEN; this module decides what a
non-zero exit MEANS, and which lines of it are worth showing.

THE DIRECTION IS THE WHOLE POINT and it runs one way only. A skip is invisible in the exit
code, so every phrase matched here is a shell's, a driver's or a container runtime's own
wording for an absent tool -- never anything a linter, a compiler or a failing test can emit.
Widening one of them to catch a genuine failure would report a red suite as skipped, which is
the exact shape of bug this pack exists to prevent. Each pattern below carries the case that
taught it.

Source of truth: engineering-standards/engineering_standards/standards_gate_outcomes.py
"""

from __future__ import annotations

import re

# `python -m ruff` when ruff is not installed. Python spells this exactly one way, and it
# cannot be produced by a lint finding, so matching the phrase is safe.
MODULE_MISSING_PATTERN = re.compile(r"No module named '?(?P<module>[\w.]+)'?")

# The same absence one level down, where `run_gate`'s FileNotFoundError cannot see it: the
# command we launch is `npm`/`pnpm`, which EXISTS, and the missing tool is whatever it
# shells out to. Without this a repo whose node_modules is not installed reports "lint
# FAILED" -- indistinguishable from a real finding, and the developer goes looking for a
# lint error that was never there.
#
# Deliberately does NOT match a bare "not found": `tsc` and every bundler emit "Cannot find
# module" and "Module not found" for a genuine broken import, and skipping THAT would turn
# the real failure this gate exists to catch into a silent pass. Every alternative below is
# anchored to a shell's -- or the dotnet driver's -- own phrasing. The last two are what
# `dotnet ef` prints when the dotnet-ef tool is absent (no tool at all, and declared in a
# tool manifest but not restored); neither phrase can appear in a build or EF finding.
# The driver's wording varies by SDK version -- "program" and "file" have both shipped (the
# "file" variant surfaced 2026-08-15 on a CI runner without dotnet-ef, where the miss made
# the ef gate report FAILED instead of skipped) -- so both are accepted, still behind the
# full anchor phrase so a compiler's bare "file not found" can never match.
MISSING_COMMAND_PATTERN = re.compile(
    r"is not recognized as an internal or external command"
    r"|: command not found"
    r"|^(?:sh|bash|/bin/sh): \d*:? ?\S+: not found"
    r"|Could not execute because the specified command or (?:program|file) was not found"
    r"|Run \"dotnet tool restore\" to make the",
    re.MULTILINE,
)

# A DECLARED gate (see standards_gate_config) whose environment is not reachable from this
# machine right now -- a stopped container, a Docker daemon that is not running. Distinct
# from a missing tool: the tool exists, we simply cannot get to it this minute.
#
# ONLY EVER APPLIED TO A DECLARED GATE, and that scoping is the rule rather than a refinement
# of it -- see `absent_toolchain_reason`. Every alternative below is anchored to a CONTAINER
# RUNTIME's phrasing, which is safe only while the thing being run is a container command.
# `The system cannot find the file specified` is the giveaway: that is Windows' strerror for
# WinError 2/3, so an ordinary FileNotFoundError raised by a genuinely failing pytest case on
# this estate's own platform prints it verbatim, and an unscoped match turned a red suite into
# `skipped` -- invisible in the exit code, and the exact skip-reads-as-pass failure this pack
# exists to end. Found by review in the B3D pack on 2026-09-04 and copied here 2026-09-05;
# `test_a_real_test_failure_inside_the_container_is_never_skipped` had three shapes and not
# that one.
UNREACHABLE_TOOLCHAIN_PATTERN = re.compile(
    r"is not running"
    r"|no such service"
    r"|Cannot connect to the Docker daemon"
    r"|error during connect"
    r"|The system cannot find the file specified",
    re.IGNORECASE,
)

# A rule tag as check-source-limits.py prints it: `path:12: [unset-not-zero] message`.
# Matched by SHAPE rather than by an enumerated list of rule names. The list version of
# this named four rules and silently under-reported the other six: when a commit tripped
# both a listed and an unlisted rule, only the listed lines were shown, so the developer
# fixed those, re-ran, and was blocked again by findings that had been there all along.
# Adding a rule to standards_checks.py must not require remembering to edit this.
SCANNER_RULE_TAG = re.compile(r"\[[a-z][a-z0-9-]*\]")

# The scanner's two kinds of NOTE, printed before the findings and carrying the same `[rule]`
# tag: `exempt:` (a decision recorded in a file) and `tuned:` (a relaxed setting with its
# reason). Both are things already decided, never things to fix -- see failure_detail().
NOTE_LINE = re.compile(r"^\s*(?:exempt|tuned):")

MAX_DETAIL_LINES = 15

# A line a PASSING gate wants seen. verify.py prints only `ok <name>` for a pass and discards the
# output, which is right for build noise and wrong for a decision that let the run through: the
# dependency gate passes BECAUSE of a dated deferral, and decision 3 of the 2026-10-02 plan is
# that every active deferral is visible on every run. A gate prefixes such lines; verify shows
# them under the gate's `ok`, and only them.
NOTICE_PREFIX = "notice: "


def notices(output: str) -> tuple[str, ...]:
    """The `notice:` lines a gate printed, without the prefix, in order."""
    return tuple(
        line.strip().removeprefix(NOTICE_PREFIX.strip()).strip()
        for line in output.splitlines()
        if line.strip().startswith(NOTICE_PREFIX.strip())
    )


def tool_module_is_missing(command: list[str], output: str) -> bool:
    """Whether the missing module is the TOOL we invoked, not one of the project's own.

    `python -m pytest` on a machine without pytest says "No module named pytest", and
    skipping that is right -- it is the same absence `FileNotFoundError` catches one level
    up. But a broken import *inside the project* says exactly the same sentence about a
    different name, and skipping THAT reports a red suite as green.

    Not hypothetical: on 2026-08-20 a file rename in allegro-it-services left one stale
    import, and the entire backend suite -- which was genuinely failing to collect -- was
    recorded as `skipped`, indistinguishable from a machine that simply lacks pytest. A
    skip is invisible in the exit code, so this is the most dangerous direction a
    classifier can be wrong in.

    Falls back to the old, broader reading when the command is not a `python -m tool`
    invocation, because then there is no tool name to compare against.
    """
    match = MODULE_MISSING_PATTERN.search(output)
    if not match:
        return False
    if "-m" not in command:
        return True
    module_flag = command.index("-m")
    if module_flag + 1 >= len(command):
        return True
    return match.group("module") == command[module_flag + 1]


def absent_toolchain_reason(command: list[str], output: str, declared: bool = False) -> str | None:
    """Why this non-zero exit means "the tool is not here", or None if it is a real failure.

    `declared` is whether the repo wrote this command into .standards.json. It gates the
    container-phrase check alone: on an UNDECLARED gate -- a plain `pytest`, a plain
    `npm run lint` -- the same words can only come out of the code under test.
    """
    # Same reasoning as run_gate's FileNotFoundError, for a tool invoked as `python -m x`:
    # that spelling turns "not installed" into an ordinary non-zero exit rather than a
    # missing executable, so without this a machine that has never run `pip install -e .`
    # could not commit anything. Matched narrowly -- the phrase only appears when the
    # module itself is absent, never in a real finding.
    if tool_module_is_missing(command, output):
        return "required module is not installed here"

    if MISSING_COMMAND_PATTERN.search(output):
        return "the tool is not available here (dependencies not installed?)"

    # DECLARED GATES ONLY. A declared gate whose environment is a container that is not up
    # right now goes the same direction as a missing module, and for the same reason: a
    # developer with the stack stopped must still be able to push, and CI is the
    # authoritative backstop. The skip is NAMED, so "it passed" and "it never ran" stay
    # distinguishable.
    #
    # The `declared` guard is what keeps that from costing more than it buys. These phrases
    # are a container runtime's, and on an undeclared gate the same words can only come out
    # of the code under test: a Windows FileNotFoundError says "The system cannot find the
    # file specified" verbatim. Without the guard a genuinely failing suite on this estate's
    # own platform was recorded as `skipped`, which changes no exit code and reads as a pass.
    if declared and UNREACHABLE_TOOLCHAIN_PATTERN.search(output):
        return "the declared toolchain is not reachable (container stopped?)"

    return None


def failure_detail(output: str) -> str:
    """The lines worth showing from a failed gate: real errors and scanner findings.

    NOTES ARE EXCLUDED, and that is the whole subtlety. `exempt:` and `tuned:` lines carry a
    `[rule]` tag exactly as a finding does, so the tag pattern alone cannot tell them apart --
    and the scanner prints them FIRST, before any finding. In a repo carrying more than
    MAX_DETAIL_LINES of them the head of the list was entirely notes, so a failing gate
    reported only decisions somebody had already made and none of the things to fix.

    Not hypothetical: this pack carries fourteen, and adding a fifteenth on 2026-09-01
    silently hid `docs-frontmatter` from a run that had reported it the day before. The
    failure mode is the bad one -- the gate still fails, so nothing ships broken, but the
    developer is told the wrong reason and the right one is invisible.
    """
    interesting = [
        line
        for line in output.splitlines()
        if (": error " in line or SCANNER_RULE_TAG.search(line)) and not NOTE_LINE.match(line)
    ]
    chosen = interesting[:MAX_DETAIL_LINES] or output.splitlines()[-MAX_DETAIL_LINES:]
    return "\n".join(chosen)

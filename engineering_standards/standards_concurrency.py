#!/usr/bin/env python3
"""The two lost-update shapes an optimistic-concurrency token cannot defend against itself.

    concurrency-bulk-bypass      a set-based write that never passes the version token
    concurrency-retry-overwrite  a conflict caught and then written anyway

standards: concurrency-bulk-bypass exempt -- this file and its tests NAME the bypassing
calls in order to detect them; every statement quoted below is the subject of a regex, never
a write this pack performs.
standards: concurrency-retry-overwrite exempt -- same reason, for the catch-and-resave shape.

WHAT THE TOKEN DOES, AND WHERE IT STOPS. A version column turns the lost update into an
error: the UPDATE carries `WHERE Version = @original`, a racing write matches zero rows, and
the ORM raises. That protection is a property of the ORM's change tracker, so it covers
exactly the writes that go through it -- and the rule's own text names the two places that
do not.

  1. SET-BASED UPDATES SKIP THE TRACKER. `ExecuteUpdate` (EF Core 7+) compiles straight to SQL
     without loading or tracking an entity, so no version predicate is added and no conflict
     can be raised. The write silently becomes last-write-wins, on the code path most likely
     to touch many rows at once.

     `ExecuteDelete` is DELIBERATELY NOT INCLUDED, and the reason is the rule's own scope
     text: the lost update is a surviving row whose change was erased, so a statement that
     removes the row has nothing to lose. data-integrity.md says the same thing when it puts
     join tables out of scope -- "membership is inserted and deleted, not versioned".
     Measured before deciding: across 4,992 estate files the two spellings split ~45 deletes
     to ~29 updates, and every delete was a cascade, a retention prune or a GDPR erasure --
     exactly the shapes the rule already calls correct. Including them would have made the
     rule 60% noise on its first run, which is how a guideline gets switched off.

  2. CATCHING THE CONFLICT AND SAVING AGAIN converts the guard into a formality. It is the
     lost update with extra steps: the database correctly refused the write, and the handler
     answers by reloading and forcing it through. `claude/rules/data-integrity.md` states this
     one directly -- "Never auto-retry as an overwrite" -- because it is the tempting fix when
     a conflict first appears in production and reads as an error to make go away.

WHY THESE TWO AND NOT "EVERY TABLE HAS A VERSION COLUMN". That is the rule's headline
requirement and it is NOT decidable from source: whether a table is updateable depends on
whether an application workflow updates it, and the correctly-exempt cases (append-only logs,
pure join tables, atomic counters) are distinguished by how they are written rather than by
anything present in a declaration. A rule that guessed would report the append-only history
table this same file mandates. So the headline stays a review question, and the scanner takes
the two shapes that ARE decidable -- which are also the two that survive a correct schema.

WHAT IT LOOKS FOR:

  * C# (.cs)  -- `ExecuteUpdate`/`ExecuteUpdateAsync` whose statement carries no version
                 predicate; and a catch of `DbUpdateConcurrencyException` whose block calls
                 SaveChanges again.
  * PHP       -- a catch of Doctrine's `OptimisticLockException` whose block calls
                 `flush()` again.

A VERSION PREDICATE IN THE STATEMENT IS THE FIX and must never be the finding: an
`ExecuteUpdate` whose `Where` names the version column passes, because that is the rule's
own stated remedy. A catch that surfaces the conflict -- rethrows, returns 409, renders the
comparison screen -- passes too; only calling the save again is reported.

Source of truth: engineering-standards/engineering_standards/standards_concurrency.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from standards_core import Violation, iter_code_lines
from standards_exemptions import exemption_reason, line_exemption_reason
from standards_scope import SOURCE_SUFFIXES

BULK_RULE = "concurrency-bulk-bypass"
RETRY_RULE = "concurrency-retry-overwrite"

# How far the catch-block walk will read before giving up. A handler longer than this has
# stopped being "catch the conflict and answer it" and the finding would be guesswork.
MAX_CATCH_LINES = 25

# EF Core's set-based update, both spellings. NOT `ExecuteDelete` -- see the module docstring:
# a removed row has no surviving edit to lose, and including it measured as 60% noise.
CS_BULK_WRITE = re.compile(r"\bExecuteUpdate(?:Async)?\s*\(")

# The remedy, in the two ways it is written: a `Where` naming the version column, or the
# version passed into the SetProperty chain. Deliberately loose on the column's name -- an
# estate that spells it `RowVersion` or `Concurrency` is following the rule, not evading it.
CS_VERSION_PREDICATE = re.compile(r"\b(?:Version|RowVersion|Concurrency\w*)\b", re.IGNORECASE)

# The catch, per language. The exception type IS the signal: nothing raises these but a
# concurrency token doing its job.
CS_CONCURRENCY_CATCH = re.compile(r"\bcatch\s*\(\s*DbUpdateConcurrencyException\b")
PHP_CONCURRENCY_CATCH = re.compile(r"\bcatch\s*\(\s*\\?(?:\w+\\)*OptimisticLockException\b")

# Saving again inside that handler -- the overwrite. `SaveChanges`/`SaveChangesAsync` in C#,
# `flush()` in Doctrine.
CS_SAVE_CALL = re.compile(r"\bSaveChanges(?:Async)?\s*\(")
PHP_SAVE_CALL = re.compile(r"->\s*flush\s*\(")

_BULK_LEAD = (
    "this is a set-based update that never passes the concurrency token: `ExecuteUpdate` "
    "compiles straight to SQL without loading or tracking an entity, so no "
    "`WHERE Version = @original` is added and a racing edit cannot be detected"
)
_BULK_TAIL = (
    ". The write silently becomes last-write-wins, on the path most likely to touch many rows "
    "at once. Carry the version predicate in the statement yourself, or say why last-write-wins "
    "is correct for this specific write -- a nightly job rebuilding its own output qualifies, "
    f"\"it was a bulk endpoint\" does not: 'standards: {BULK_RULE} exempt -- <why>'."
)

_RETRY_LEAD = "this catches a concurrency conflict and then saves again, which is the lost update with extra steps"
_RETRY_TAIL = (
    ". The database correctly refused the write because someone else had already changed the "
    "row; reloading and forcing it through erases their edit and turns the version column into "
    "a formality. A conflict is a UX event: tell the user who changed it and when, show the "
    "current values beside theirs, and return 409 over HTTP. If this genuinely is a "
    "conflict-free merge rather than an overwrite -- appending to a collection, recomputing a "
    f"derived total -- say so: 'standards: {RETRY_RULE} exempt -- <why>'."
)


def _catch_block_lines(code_lines: list[tuple[int, str]], start: int) -> list[str]:
    """The lines of the catch block beginning at or after `start`, brace-counted.

    Reads from the `catch` line forward, opening on the block's first `{` and stopping when
    the depth returns to zero, so a nested `if`/`try` inside the handler does not end the walk
    early. Capped at MAX_CATCH_LINES: past that the handler is long enough that "does it save
    again" stops being one decidable question.
    """
    collected: list[str] = []
    depth = 0
    opened = False

    for offset, (_, line) in enumerate(code_lines[start : start + MAX_CATCH_LINES]):
        collected.append(line)

        # On the catch line itself, count only from its opening brace onward. PHP writes the
        # handler as `} catch (X $e) {` -- one line carrying the TRY block's closing brace as
        # well as this block's opening one -- and counting both nets to zero, which would end
        # the walk before it read a single statement of the handler.
        counted = line
        if offset == 0 and "{" in line:
            counted = line[line.index("{") :]

        depth += counted.count("{") - counted.count("}")
        if "{" in counted:
            opened = True
        if opened and depth <= 0:
            break

    return collected


def _bulk_finding(suffix: str, line: str) -> Optional[str]:
    """The lead for a set-based write with no version predicate on this line, or None."""
    if suffix != ".cs" or not CS_BULK_WRITE.search(line):
        return None
    if CS_VERSION_PREDICATE.search(line):
        return None
    return _BULK_LEAD


def check_concurrency(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Both lost-update findings for one file.

    Line-scoped, like the other security-and-correctness rules: one repository class
    legitimately holds a justified bulk rebuild beside an ordinary tracked write, and a
    file-scoped marker would excuse both. The file-scoped marker is kept only for a file whose
    subject IS these shapes -- this detector and its fixtures.
    """
    suffix = path.suffix
    if suffix not in SOURCE_SUFFIXES or suffix not in (".cs", ".php"):
        return

    code_lines = list(iter_code_lines(lines, suffix))

    bulk_exempt = exemption_reason(lines, BULK_RULE) is not None
    retry_exempt = exemption_reason(lines, RETRY_RULE) is not None
    if bulk_exempt and retry_exempt:
        return

    catch_pattern = CS_CONCURRENCY_CATCH if suffix == ".cs" else PHP_CONCURRENCY_CATCH
    save_pattern = CS_SAVE_CALL if suffix == ".cs" else PHP_SAVE_CALL

    for index, (line_number, line) in enumerate(code_lines):
        if not bulk_exempt:
            lead = _bulk_finding(suffix, line)
            if lead is not None and line_exemption_reason(lines, line_number - 1, BULK_RULE) is None:
                yield Violation(
                    path=path,
                    line=line_number,
                    rule=BULK_RULE,
                    message=lead + _BULK_TAIL,
                )

        if retry_exempt or not catch_pattern.search(line):
            continue
        block = _catch_block_lines(code_lines, index)
        if not any(save_pattern.search(blocked) for blocked in block):
            continue
        if line_exemption_reason(lines, line_number - 1, RETRY_RULE) is not None:
            continue

        yield Violation(
            path=path,
            line=line_number,
            rule=RETRY_RULE,
            message=_RETRY_LEAD + _RETRY_TAIL,
        )

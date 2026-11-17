#!/usr/bin/env python3
"""`job-swallows-failure` for the brace-delimited languages: C# and PHP.

The Python half (`standards_jobs.py`) reads a handler by INDENTATION, which is the language's
own delimiter and therefore exact. C# and PHP delimit with braces, and the first version of
this rule declined to cover them rather than guess at brace depth -- because the guess fails
in one specific direction: end the block early, miss the `throw` that is really there, and
report a CORRECT handler as broken. A job rule that fires on working code gets switched off,
and takes the real findings with it.

So the reader counts braces on text with comments and string literals REMOVED, and where it
cannot be sure it says so and the rule stays silent. `_block_end` returns None for a verbatim
string (`@"..."`, `<<<HEREDOC`) or an unterminated quote, and None means "not reported". That
is the same direction every other rule here errs in, and it is the only safe one: a missed
finding is one job nobody is told about, while a false one is the whole rule discarded.

WHAT COUNTS AS A JOB HERE, and it is narrower than "a class with a catch in it":

* C#   -- a class deriving `BackgroundService` / `IHostedService` (the .NET worker shapes),
          `IJob` (Quartz), or a method carrying Hangfire's `[AutomaticRetry]`.
* PHP  -- a class implementing Laravel's `ShouldQueue`.

Each is a DECLARATION that the code runs with no caller waiting on it, which is the property
the rule is about. A `Task.Run(...)` is not here for the same reason `Thread(target=...)` is
absent from the Python side: the work is named somewhere else, and following that reference
with a regex would be guessing.

Source of truth: engineering-standards/engineering_standards/standards_jobs_braces.py
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from standards_core import Violation
from standards_exemptions import line_exemption_reason

# The declarations that make a class a background job.
CSHARP_WORKER = re.compile(
    r"\bclass\s+\w+[^{;]*?:\s*[^{;]*\b(?:BackgroundService|IHostedService|IHostedLifecycleService|IJob)\b"
)
CSHARP_JOB_ATTRIBUTE = re.compile(r"^\s*\[\s*(?:AutomaticRetry|DisableConcurrentExecution)\b")
PHP_QUEUED = re.compile(r"\bclass\s+\w+[^{]*\bimplements\b[^{]*\bShouldQueue\b")

# `catch (Exception ex)`, `catch (\Throwable $e)`, `catch (Exception)`, and C#'s bare `catch`.
# NARROW CATCHES ARE NOT THIS RULE, exactly as on the Python side: `catch (JsonException)`
# around a parse with a defined fallback says what it expects and has handled it.
BROAD_CATCH = re.compile(
    r"^\s*\}?\s*catch\s*(?:\(\s*(?:\\?(?:System\.)?(?:Exception|Throwable|Error)\b[^)]*)?\)|\s*\{|\s*$)"
)

# Anything that lets the failure out, or records the run as failed.
SIGNALS_FAILURE = re.compile(
    r"\bthrow\b"
    r"|\bEnvironment\.Exit\s*\("
    r"|\bstopping(?:Token)?\.ThrowIfCancellationRequested\s*\("
    r"|\$this->fail\s*\("  # Laravel: mark the queued job failed
    r"|\$this->release\s*\("  # Laravel: put it back on the queue
    r"|\bfail\s*\(\s*\$"
)

# Text this reader will not count braces through. A verbatim or interpolated-verbatim string
# makes `\` ordinary and `""` an escaped quote; a heredoc runs to a terminator on its own
# line. Both are rare in a catch block and neither is worth a parser, so they end the attempt.
UNCOUNTABLE = re.compile(r'@"|\$@"|<<<|<<\'')

LINE_COMMENT = re.compile(r"//.*$|#.*$")
BLOCK_COMMENT = re.compile(r"/\*.*?\*/")
SIMPLE_STRING = re.compile(r'"(?:[^"\\]|\\.)*"' r"|'(?:[^'\\]|\\.)*'")


def _countable(line: str) -> str | None:
    """`line` with comments and string literals removed, or None if that cannot be done.

    None is not an error -- it is the reader declining to guess, which keeps the rule quiet
    rather than wrong.
    """
    if UNCOUNTABLE.search(line):
        return None
    without = BLOCK_COMMENT.sub("", line)
    without = SIMPLE_STRING.sub("", without)
    without = LINE_COMMENT.sub("", without)
    if '"' in without or "'" in without:
        return None  # a quote survived: the literal spans lines
    return without


def _block_end(lines: list[str], start: int) -> int | None:
    """Index of the line closing the block that opens at or after `start`, or None.

    Handles both `catch (...) {` and a brace on the following line. Gives up -- returning
    None -- on any line it cannot count honestly, and on a block that never closes.
    """
    depth = 0
    opened = False
    for index in range(start, len(lines)):
        countable = _countable(lines[index])
        if countable is None:
            return None
        if index == start:
            # `} catch (...) {` closes the TRY on the same line. Counting that brace would
            # take depth negative before the handler opens, and the block would "end" on its
            # own first line -- which reports every such handler, and silently made a passing
            # test pass for the wrong reason. Count from the `catch` keyword onward.
            keyword = countable.rfind("catch")
            if keyword >= 0:
                countable = countable[keyword:]
        for character in countable:
            if character == "{":
                depth += 1
                opened = True
            elif character == "}":
                depth -= 1
                if opened and depth <= 0:
                    return index
        if opened and depth <= 0:
            return index
    return None


def _is_job_file(lines: list[str], suffix: str) -> bool:
    if suffix == ".php":
        return any(PHP_QUEUED.search(line) for line in lines)
    return any(CSHARP_WORKER.search(line) or CSHARP_JOB_ATTRIBUTE.match(line) for line in lines)


def check_brace_language_jobs(path: Path, lines: list[str], rule: str, message: str) -> Iterable[Violation]:
    """Every broad catch in a C#/PHP background job whose handler neither throws nor fails.

    THE RULE NAME AND MESSAGE ARE PASSED IN, not imported, and that is load-bearing rather
    than style: `standards_jobs` imports THIS module, so importing its constants back would
    be a cycle. Passing them also makes it structurally impossible for the two readers to
    describe the same defect differently.

    Scoped to the FILE once it declares a job, not to the class body. A helper in the same
    file that swallows an exception makes the job report success just as surely as the entry
    point doing it -- the worker is told nothing either way -- and a rule that only looked
    inside `ExecuteAsync` would miss the commonest shape, which is the try/catch one level
    down in the method it calls.
    """
    if path.suffix not in (".cs", ".php") or not _is_job_file(lines, path.suffix):
        return

    for index, line in enumerate(lines):
        if not BROAD_CATCH.match(line):
            continue
        end = _block_end(lines, index)
        if end is None:
            continue  # could not read it honestly; say nothing
        if any(SIGNALS_FAILURE.search(lines[within]) for within in range(index, end + 1)):
            continue
        if line_exemption_reason(lines, index, rule):
            continue
        yield Violation(path=path, line=index + 1, rule=rule, message=message)

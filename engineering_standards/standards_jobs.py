#!/usr/bin/env python3
"""Background jobs that cannot report their own failure.

A JOB THAT CANNOT FAIL IS THE SAME DEFECT AS A GATE THAT CANNOT FAIL, and this pack has
been bitten by that shape repeatedly: a skipped gate indistinguishable from a passing one,
a probe that printed nothing where "no interpreter" and "no findings" looked identical,
`test-always-passes`. The instrument is always the same -- make the silent success
impossible to mistake for a real one -- and it had never been pointed at workers.

FOUND IN allegro-it-services' CELERY WORKER, 2026-09-02, and the admin table is the whole
specification: a hundred rows of task history in which TASK NAME, PERIODIC TASK NAME and
WORKER were all `-`, and TASK STATE was SUCCESS in every single row. Nobody could say what
had run, where, or whether it had worked. Three separate defects produce that one table,
and they need three separate rules, because fixing any one of them leaves the other two:

* `job-results-anonymous`  -- the history is stored WITHOUT the task's name.
* `job-swallows-failure`   -- the task catches everything and returns normally, so the row
                              says SUCCESS whatever happened.
* `job-results-not-stored` -- there is no result backend, so an outcome is nowhere at all.

WHY THE NAME IS MISSING IS A ONE-LINE SETTING, which is what makes it worth a rule rather
than a rules-file paragraph. Celery's `result_extended` defaults to False, and with it off
django-celery-results records only the id, the state and the timestamp -- the name, the
arguments and the worker are left NULL, which is exactly the `-` in every column. It is not
recoverable afterwards: the rows already written stay anonymous.

Source of truth: engineering-standards/engineering_standards/standards_jobs.py
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from standards_config import CheckConfig
from standards_core import Violation
from standards_exemptions import exemption_reason, line_exemption_reason
from standards_jobs_braces import check_brace_language_jobs
from standards_markdown import SKIP_DIRECTORIES
from standards_pack_identity import is_foreign_pack_file
from standards_tree import walk_repository

SWALLOWS_RULE = "job-swallows-failure"
ANONYMOUS_RULE = "job-results-anonymous"
NOT_STORED_RULE = "job-results-not-stored"

# ONE MESSAGE, TWO READERS. The indentation reader below and the brace reader in
# standards_jobs_braces find the same defect in different syntax, and a finding that reads
# differently depending on which language it was found in is two rules wearing one name.
# Worded without naming celery's `self.retry`, because it is also .NET and Laravel now.
SWALLOWS_MESSAGE = (
    "this background job catches every exception and returns normally, so the run is "
    "recorded as SUCCESS whatever happened -- the worker is never told, and the task "
    "history says the job worked. Let the failure out after logging it, retry it, or mark "
    "the run failed. If this catch is per-item in a batch that counts its failures and "
    "reports them, say so: 'standards: job-swallows-failure exempt -- <why>' on the line "
    "above."
)

# A function registered to run OUTSIDE the request that created it. Decorator-shaped only,
# deliberately: a decorator is an unambiguous declaration that this function is a job, where
# `Thread(target=handler)` names its target somewhere else entirely and a regex following
# that reference would be guessing.
#
# `@task` bare is NOT here. Invoke and Fabric both spell their build tasks that way, and a
# build task is not a background job -- flagging every `tasks.py` in a repo that uses Invoke
# is how a rule gets switched off. `app.task` / `celery.task` carry their namespace and are
# safe; `shared_task` and `periodic_task` are unambiguous on their own.
TASK_DECORATOR = re.compile(
    r"^\s*@(?:"
    r"shared_task"
    r"|periodic_task"
    r"|\w+\.task"  # app.task, celery.task, huey.task
    r"|\w+\.actor"  # dramatiq
    r"|\w+\.periodic_task"
    r")\b"
)

DEF_LINE = re.compile(r"^(?P<indent>\s*)(?:async\s+)?def\s+(?P<name>\w+)\s*\(")

# Only a BROAD catch. `except ValueError:` around a parse that has a defined fallback is
# ordinary control flow and says what it expects; `except Exception:` says "whatever happens,
# carry on", which is the shape that turns a failure into a SUCCESS row.
BROAD_EXCEPT = re.compile(r"^(?P<indent>\s*)except\s*(?:\(?\s*(?:Exception|BaseException)\b[^:]*)?:")

# Anything that lets the failure reach the worker, or records it as a failure. Any ONE of
# these is enough -- this rule is about the handler that does NONE of them.
SIGNALS_FAILURE = re.compile(
    r"\braise\b"
    r"|\.retry\s*\("
    r"|update_state\s*\("
    r"|\bself\.request\.chain\s*=\s*None"  # celery's "stop the chain" idiom
    r"|sys\.exit\s*\("
    r"|os\._exit\s*\("
    r"|\braise_for_status\s*\("
)

# django-celery-results, and the setting that makes its rows say anything.
RESULTS_APP = re.compile(r"django_celery_results|django-celery-results")
RESULT_EXTENDED_ON = re.compile(
    r"^\s*(?:CELERY_)?RESULT_EXTENDED\s*=\s*True"
    r"|^\s*(?:\w+\.conf\.)?result_extended\s*=\s*True"
    r"|result_extended\s*=\s*True\s*[,)]",
    re.IGNORECASE | re.MULTILINE,
)
RESULT_BACKEND_SET = re.compile(
    r"^\s*(?:CELERY_)?RESULT_BACKEND\s*=\s*[\"'\w]"
    r"|^\s*(?:\w+\.conf\.)?result_backend\s*=\s*[\"'\w]"
    r"|\bbackend\s*=\s*[\"']",
    re.IGNORECASE | re.MULTILINE,
)
IGNORE_RESULT_GLOBAL = re.compile(
    r"^\s*(?:CELERY_)?TASK_IGNORE_RESULT\s*=\s*True|task_ignore_result\s*=\s*True",
    re.IGNORECASE | re.MULTILINE,
)


def check_job_failure_handling(path: Path, lines: list[str]) -> Iterable[Violation]:
    """A background task that catches everything and returns as though it worked.

    TWO READERS, ONE RULE. Python delimits a handler by indentation, which is exact, so it is
    read here. C# and PHP delimit with braces and are read by `standards_jobs_braces`, which
    counts them only on text it can strip honestly and stays silent otherwise -- guessing at
    brace depth ends a block early, misses the `throw` that is really there, and reports a
    CORRECT handler as broken. Both readers yield SWALLOWS_MESSAGE, so a finding reads the
    same whichever language it was found in.

    Line-exemptable, because the legitimate case is real: a batch job that catches per ITEM
    to keep going, counts the failures and reports them at the end, has done nothing wrong --
    it just cannot be told apart from the broken shape by looking at the handler alone.
    """
    if exemption_reason(lines, SWALLOWS_RULE) is not None:
        return

    if path.suffix in (".cs", ".php"):
        # Brace-delimited: a different reader, the same rule and the same message. See
        # standards_jobs_braces for why it declines to guess rather than risk a false finding.
        yield from check_brace_language_jobs(path, lines, SWALLOWS_RULE, SWALLOWS_MESSAGE)
        return

    if path.suffix != ".py":
        return

    for start, indent in _task_bodies(lines):
        yield from _swallowing_handlers(path, lines, start, indent)


def _task_bodies(lines: list[str]) -> Iterable[tuple[int, int]]:
    """(index of the `def` line, its indent) for every decorated background task."""
    decorated = False
    for index, line in enumerate(lines):
        if TASK_DECORATOR.match(line):
            decorated = True
            continue
        found = DEF_LINE.match(line)
        if found is None:
            # Decorators stack: `@shared_task` above `@transaction.atomic` above the def.
            if line.strip() and not line.lstrip().startswith("@"):
                decorated = False
            continue
        if decorated:
            yield index, len(found.group("indent").expandtabs(4))
        decorated = False


def _swallowing_handlers(path: Path, lines: list[str], start: int, indent: int) -> Iterable[Violation]:
    """Every broad `except` inside this task whose handler neither raises nor signals."""
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.strip() and _indent_of(line) <= indent:
            return  # left the task body
        found = BROAD_EXCEPT.match(line)
        if found is None:
            continue
        handler_indent = len(found.group("indent").expandtabs(4))
        if _handler_signals(lines, index, handler_indent):
            continue
        if line_exemption_reason(lines, index, SWALLOWS_RULE):
            continue
        yield Violation(
            path=path,
            line=index + 1,
            rule=SWALLOWS_RULE,
            message=SWALLOWS_MESSAGE,
        )


def _handler_signals(lines: list[str], except_at: int, handler_indent: int) -> bool:
    for index in range(except_at + 1, len(lines)):
        line = lines[index]
        if not line.strip():
            continue
        if _indent_of(line) <= handler_indent:
            return False
        if SIGNALS_FAILURE.search(line):
            return True
    return False


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def check_job_result_storage(repo_root: Path, config: CheckConfig) -> Iterable[Violation]:
    """Repo-level: is a job's outcome recorded at all, and does the record name the job?

    REPO-LEVEL because both answers live in configuration that no single task file can see,
    and because the finding is one per repository rather than one per task -- there is one
    setting to add, not a hundred.
    """
    if not config.check_jobs:
        return

    python_files = list(_python_files(repo_root))
    if not any(_has_tasks(path) for path in python_files):
        return

    # CONFIGURATION FILES ONLY, and never the pack's own copy. Reading every .py in the tree
    # and asking "does this text configure celery?" answered YES on the strength of this
    # module's own message ("Set CELERY_RESULT_EXTENDED = True") and its own test fixtures --
    # so in exactly the repos that adopt the pack, the pack silenced the rule about itself.
    # Measured 2026-09-02 against a synthetic worker; the finding vanished, which is the
    # dangerous direction for a rule about things that fail silently.
    settings_text = chr(10).join(
        _read(path)
        for path in python_files
        if _is_configuration(path) and not is_foreign_pack_file(_relative(path, repo_root), repo_root)
    )

    if RESULTS_APP.search(settings_text) and not RESULT_EXTENDED_ON.search(settings_text):
        yield Violation(
            path=repo_root / "settings.py",
            line=1,
            rule=ANONYMOUS_RULE,
            message=(
                "this repo stores Celery results with django-celery-results but never sets "
                "result_extended, which defaults to False -- so every row records only an id, "
                "a state and a timestamp, and the task name, its arguments and the worker are "
                "left NULL. That is a task history in which nothing can be told from anything: "
                "measured in allegro-it-services on 2026-09-02, a hundred consecutive rows with "
                "'-' for the name and the worker. Set CELERY_RESULT_EXTENDED = True. Rows "
                "already written stay anonymous -- this cannot be backfilled."
            ),
        )

    if not RESULT_BACKEND_SET.search(settings_text) or IGNORE_RESULT_GLOBAL.search(settings_text):
        yield Violation(
            path=repo_root / "settings.py",
            line=1,
            rule=NOT_STORED_RULE,
            message=(
                "this repo runs background tasks but stores no result: there is no result "
                "backend configured, or results are ignored globally. A task that fails then "
                "leaves nothing behind except a line in whichever worker happened to run it, "
                "until that log rotates -- so 'it did not run', 'it ran and failed' and 'it ran "
                "and worked' are the same observation. Configure a result backend."
            ),
        )


def _python_files(repo_root: Path) -> Iterable[Path]:
    for directory, subdirectories, names in walk_repository(repo_root):
        subdirectories[:] = [n for n in subdirectories if n not in SKIP_DIRECTORIES]
        base = Path(directory)
        for name in names:
            if name.endswith(".py"):
                yield base / name


# Where celery is configured, by name. A task module can hold `app.conf.update(...)` too,
# so `celery.py` is included; an ordinary application module is not, and must not be able to
# satisfy a settings question by mentioning a setting in a comment.
CONFIGURATION_NAMES = re.compile(
    r"^(?:settings|celery|celeryconfig|config|conf|base|production|local|dev|prod)\w*\.py$"
)


def _is_configuration(path: Path) -> bool:
    return bool(CONFIGURATION_NAMES.match(path.name)) or path.parent.name in {"settings", "config"}


def _relative(path: Path, repo_root: Path) -> Path:
    try:
        return path.relative_to(repo_root)
    except ValueError:
        return path


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _has_tasks(path: Path) -> bool:
    return any(TASK_DECORATOR.match(line) for line in _read(path).splitlines())

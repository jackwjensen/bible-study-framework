#!/usr/bin/env python3
"""Cases for the background-job rules.

The negatives carry the weight here, as they do for every rule this pack ships. A job rule
that fires on a correct handler is worse than no job rule: the natural response to a finding
on working code is to switch the rule off, and then the real one -- the task that reports
SUCCESS whatever happened -- goes unreported with it.

The positives are the allegro-it-services shapes, 2026-09-02: a hundred task-history rows
with `-` for the task name and the worker, and SUCCESS in every one.

Run: python test_standards_jobs.py   (or pytest)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_config import CheckConfig  # noqa: E402
from standards_jobs import (  # noqa: E402
    check_job_failure_handling,
    check_job_result_storage,
)
from standards_selftest import run_module_tests  # noqa: E402

TASK = Path("app/tasks.py")


def found(*lines: str) -> list[str]:
    return [v.rule for v in check_job_failure_handling(TASK, list(lines))]


# ---- the shape that produced a hundred SUCCESS rows -----------------------------------------


def test_a_task_that_catches_everything_and_returns_is_flagged() -> None:
    assert found(
        "@shared_task",
        "def send_invoices():",
        "    try:",
        "        deliver()",
        "    except Exception:",
        '        logger.error("failed")',
    ) == ["job-swallows-failure"]


def test_a_bare_except_is_the_same_defect() -> None:
    assert found(
        "@app.task",
        "def rebuild():",
        "    try:",
        "        run()",
        "    except:",
        "        pass",
    ) == ["job-swallows-failure"]


def test_a_periodic_task_is_covered_too() -> None:
    assert found(
        "@periodic_task",
        "def nightly():",
        "    try:",
        "        run()",
        "    except Exception as error:",
        '        logger.warning("%s", error)',
    ) == ["job-swallows-failure"]


# ---- handlers that DO signal, and must stay silent -------------------------------------------


def test_re_raising_after_logging_is_correct() -> None:
    assert (
        found(
            "@shared_task",
            "def send_invoices():",
            "    try:",
            "        deliver()",
            "    except Exception:",
            '        logger.exception("delivery failed")',
            "        raise",
        )
        == []
    )


def test_calling_retry_is_signalling() -> None:
    assert (
        found(
            "@shared_task(bind=True)",
            "def send_invoices(self):",
            "    try:",
            "        deliver()",
            "    except Exception as error:",
            "        self.retry(exc=error)",
        )
        == []
    )


def test_marking_the_task_failed_is_signalling() -> None:
    assert (
        found(
            "@app.task(bind=True)",
            "def rebuild(self):",
            "    try:",
            "        run()",
            "    except Exception:",
            '        self.update_state(state="FAILURE")',
        )
        == []
    )


def test_a_narrow_except_is_ordinary_control_flow() -> None:
    """`except ValueError:` around a parse with a defined fallback says what it expects."""
    assert (
        found(
            "@shared_task",
            "def parse_feed():",
            "    try:",
            "        return int(value)",
            "    except ValueError:",
            "        return 0",
        )
        == []
    )


# ---- scope: only background tasks ------------------------------------------------------------


def test_an_ordinary_function_is_not_a_background_job() -> None:
    """The rule is about what the WORKER is told. A plain helper has a caller to tell."""
    assert (
        found(
            "def send_invoices():",
            "    try:",
            "        deliver()",
            "    except Exception:",
            "        pass",
        )
        == []
    )


def test_a_bare_task_decorator_is_not_assumed_to_be_celery() -> None:
    """Invoke and Fabric spell a BUILD task `@task`, and a build task is not a background job.

    Flagging every `tasks.py` in a repo that uses Invoke is how a rule gets switched off.
    """
    assert (
        found(
            "@task",
            "def build(c):",
            "    try:",
            "        c.run('make')",
            "    except Exception:",
            "        pass",
        )
        == []
    )


def test_a_handler_after_the_task_body_is_not_the_tasks() -> None:
    """The walk must stop at the end of the decorated function, not run to the file's end."""
    assert (
        found(
            "@shared_task",
            "def good():",
            "    try:",
            "        run()",
            "    except Exception:",
            "        raise",
            "",
            "def helper():",
            "    try:",
            "        run()",
            "    except Exception:",
            "        pass",
        )
        == []
    )


def test_the_line_exemption_is_honoured() -> None:
    """The legitimate case: catching per ITEM so a batch continues, and counting failures."""
    assert (
        found(
            "@shared_task",
            "def import_rows():",
            "    for row in rows:",
            "        try:",
            "            handle(row)",
            "        # standards: job-swallows-failure exempt -- per-row, counted and reported below",
            "        except Exception:",
            "            failures += 1",
        )
        == []
    )


# ---- the result store ------------------------------------------------------------------------


def repo_with(tree: Path, *files: tuple[str, str]) -> Path:
    repo = tree / "repo"
    for relative, text in files:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return repo


TASKS_FILE = "@shared_task" + chr(10) + "def run_it():" + chr(10) + "    pass" + chr(10)


def storage_rules(repo: Path, config: CheckConfig | None = None) -> list[str]:
    return [v.rule for v in check_job_result_storage(repo, config or CheckConfig())]


def test_results_stored_without_the_task_name_are_flagged() -> None:
    """The screenshot: TASK NAME, PERIODIC TASK NAME and WORKER all `-`, in every row."""
    with tempfile.TemporaryDirectory() as tree:
        repo = repo_with(
            Path(tree),
            ("app/tasks.py", TASKS_FILE),
            (
                "app/settings.py",
                'INSTALLED_APPS = ["django_celery_results"]'
                + chr(10)
                + 'CELERY_RESULT_BACKEND = "django-db"'
                + chr(10),
            ),
        )
        assert "job-results-anonymous" in storage_rules(repo)


def test_result_extended_settles_it() -> None:
    with tempfile.TemporaryDirectory() as tree:
        repo = repo_with(
            Path(tree),
            ("app/tasks.py", TASKS_FILE),
            (
                "app/settings.py",
                'INSTALLED_APPS = ["django_celery_results"]'
                + chr(10)
                + 'CELERY_RESULT_BACKEND = "django-db"'
                + chr(10)
                + "CELERY_RESULT_EXTENDED = True"
                + chr(10),
            ),
        )
        assert "job-results-anonymous" not in storage_rules(repo)


def test_no_result_backend_at_all_is_flagged() -> None:
    with tempfile.TemporaryDirectory() as tree:
        repo = repo_with(Path(tree), ("app/tasks.py", TASKS_FILE))
        assert "job-results-not-stored" in storage_rules(repo)


def test_ignoring_results_globally_is_the_same_thing() -> None:
    with tempfile.TemporaryDirectory() as tree:
        repo = repo_with(
            Path(tree),
            ("app/tasks.py", TASKS_FILE),
            (
                "app/settings.py",
                'CELERY_RESULT_BACKEND = "django-db"' + chr(10) + "CELERY_TASK_IGNORE_RESULT = True" + chr(10),
            ),
        )
        assert "job-results-not-stored" in storage_rules(repo)


def test_a_repo_with_no_background_tasks_is_silent() -> None:
    """No worker, no opinion. The rule must not reach a repo that runs nothing in background."""
    with tempfile.TemporaryDirectory() as tree:
        repo = repo_with(Path(tree), ("app/views.py", "def index(request):" + chr(10) + "    pass"))
        assert storage_rules(repo) == []


def test_the_repo_can_turn_the_dimension_off() -> None:
    with tempfile.TemporaryDirectory() as tree:
        repo = repo_with(Path(tree), ("app/tasks.py", TASKS_FILE))
        assert storage_rules(repo, CheckConfig(check_jobs=False)) == []


def test_the_packs_own_copy_cannot_configure_the_repo_it_is_installed_in() -> None:
    """The regression that made this rule useless in exactly the repos that adopt the pack.

    Every adopted repo physically holds `engineering_standards/`, including this module --
    whose message says "Set CELERY_RESULT_EXTENDED = True" -- and its test fixtures, which
    contain `CELERY_TASK_IGNORE_RESULT = True`. Reading every .py in the tree therefore
    answered the settings question out of the pack's own prose, and the finding disappeared.
    Silently, which is the direction that matters for a rule about silent failure.
    """
    with tempfile.TemporaryDirectory() as tree:
        repo = repo_with(
            Path(tree),
            ("app/tasks.py", TASKS_FILE),
            (
                "app/settings.py",
                'INSTALLED_APPS = ["django_celery_results"]'
                + chr(10)
                + 'CELERY_RESULT_BACKEND = "django-db"'
                + chr(10),
            ),
        )
        # The pack, installed as it really is: this module and its suite, verbatim.
        here = Path(__file__).resolve().parent
        installed = repo / here.name
        installed.mkdir(parents=True, exist_ok=True)
        for name in ("standards_jobs.py", "test_standards_jobs.py"):
            (installed / name).write_text((here / name).read_text(encoding="utf-8"), encoding="utf-8")

        rules = storage_rules(repo)
        assert "job-results-anonymous" in rules, (
            "the pack's own text configured the repo it is installed in, so the rule went silent in every adopted repo"
        )
        assert "job-results-not-stored" not in rules, (
            "a result backend IS configured here; the pack's own fixtures must not say otherwise"
        )


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "background jobs"))

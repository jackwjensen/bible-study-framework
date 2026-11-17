#!/usr/bin/env python3
"""Cases for `job-swallows-failure` in C# and PHP.

THE NEGATIVES ARE THE POINT OF THIS FILE. A brace reader can end a block early and miss the
`throw` that is really there, which reports a CORRECT handler as broken -- and a job rule
that fires on working code is switched off, taking the real findings with it. Every case
below that asserts `[]` is guarding that direction.

The reader is allowed to give up. `test_a_verbatim_string_is_declined_rather_than_guessed`
pins that: where braces cannot be counted honestly the rule says nothing, which is the same
way every other rule here errs.

Run: python test_standards_jobs_braces.py   (or pytest)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_jobs import SWALLOWS_MESSAGE, SWALLOWS_RULE  # noqa: E402
from standards_jobs_braces import check_brace_language_jobs  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

WORKER = Path("src/InvoiceWorker.cs")
QUEUED = Path("app/Jobs/SendInvoice.php")


def found(path: Path, *lines: str) -> list[str]:
    return [v.rule for v in check_brace_language_jobs(path, list(lines), SWALLOWS_RULE, SWALLOWS_MESSAGE)]


CS_WORKER_HEAD = ("public class InvoiceWorker : BackgroundService", "{")
PHP_JOB_HEAD = ("<?php", "class SendInvoice implements ShouldQueue", "{")


# ---- C# ---------------------------------------------------------------------------------


def test_a_hosted_service_that_swallows_is_flagged() -> None:
    assert found(
        WORKER,
        *CS_WORKER_HEAD,
        "    protected override async Task ExecuteAsync(CancellationToken stoppingToken)",
        "    {",
        "        try",
        "        {",
        "            await DeliverAsync();",
        "        }",
        "        catch (Exception ex)",
        "        {",
        '            _logger.LogError(ex, "delivery failed");',
        "        }",
        "    }",
        "}",
    ) == [SWALLOWS_RULE]


def test_rethrowing_is_correct() -> None:
    assert (
        found(
            WORKER,
            *CS_WORKER_HEAD,
            "        try",
            "        {",
            "            await DeliverAsync();",
            "        }",
            "        catch (Exception ex)",
            "        {",
            '            _logger.LogError(ex, "delivery failed");',
            "            throw;",
            "        }",
            "}",
        )
        == []
    )


def test_a_narrow_catch_is_ordinary_control_flow() -> None:
    assert (
        found(
            WORKER,
            *CS_WORKER_HEAD,
            "        catch (JsonException)",
            "        {",
            "            return Array.Empty<Row>();",
            "        }",
            "}",
        )
        == []
    )


def test_a_brace_on_the_catch_line_is_read() -> None:
    """`catch (Exception ex) {` is the other brace style, and must not read as unclosed."""
    assert found(
        WORKER,
        *CS_WORKER_HEAD,
        "        catch (Exception ex) {",
        '            _logger.LogError(ex, "failed");',
        "        }",
        "}",
    ) == [SWALLOWS_RULE]


def test_a_hangfire_attribute_is_a_job_too() -> None:
    assert found(
        Path("src/Reports.cs"),
        "public class Reports",
        "{",
        "    [AutomaticRetry(Attempts = 3)]",
        "    public void Rebuild()",
        "    {",
        "        try { Run(); }",
        "        catch (Exception)",
        "        {",
        "        }",
        "    }",
        "}",
    ) == [SWALLOWS_RULE]


def test_an_ordinary_class_is_not_a_background_job() -> None:
    """A controller swallowing an exception is a different rule's business, not this one's."""
    assert (
        found(
            Path("src/InvoiceController.cs"),
            "public class InvoiceController : ControllerBase",
            "{",
            "        catch (Exception ex)",
            "        {",
            '            _logger.LogError(ex, "failed");',
            "        }",
            "}",
        )
        == []
    )


def test_a_brace_inside_a_string_does_not_end_the_block_early() -> None:
    """The failure mode this reader exists to avoid: ending early and missing the `throw`."""
    assert (
        found(
            WORKER,
            *CS_WORKER_HEAD,
            "        catch (Exception ex)",
            "        {",
            '            _logger.LogError(ex, "unbalanced } brace in a message");',
            "            throw;",
            "        }",
            "}",
        )
        == []
    )


def test_a_brace_in_a_comment_does_not_end_the_block_early() -> None:
    assert (
        found(
            WORKER,
            *CS_WORKER_HEAD,
            "        catch (Exception ex)",
            "        {",
            "            // closing } in a comment",
            "            throw;",
            "        }",
            "}",
        )
        == []
    )


def test_a_verbatim_string_is_declined_rather_than_guessed() -> None:
    """`@"..."` makes `\\` ordinary and `""` an escaped quote, so the stripper cannot be sure.

    Declining is the whole design: silence where it cannot read honestly, never a guess that
    might call a correct handler broken.
    """
    assert (
        found(
            WORKER,
            *CS_WORKER_HEAD,
            "        catch (Exception ex)",
            "        {",
            '            var path = @"C:\\logs\\{worker}";',
            "        }",
            "}",
        )
        == []
    )


# ---- PHP --------------------------------------------------------------------------------


def test_a_queued_laravel_job_that_swallows_is_flagged() -> None:
    assert found(
        QUEUED,
        *PHP_JOB_HEAD,
        "    public function handle(): void",
        "    {",
        "        try {",
        "            $this->deliver();",
        "        } catch (\\Throwable $e) {",
        "            Log::error($e->getMessage());",
        "        }",
        "    }",
        "}",
    ) == [SWALLOWS_RULE]


def test_marking_the_laravel_job_failed_is_signalling() -> None:
    assert (
        found(
            QUEUED,
            *PHP_JOB_HEAD,
            "        try {",
            "            $this->deliver();",
            "        } catch (\\Throwable $e) {",
            "            $this->fail($e);",
            "        }",
            "}",
        )
        == []
    )


def test_releasing_back_to_the_queue_is_signalling() -> None:
    assert (
        found(
            QUEUED,
            *PHP_JOB_HEAD,
            "        try {",
            "            $this->deliver();",
            "        } catch (\\Throwable $e) {",
            "            $this->release(60);",
            "        }",
            "}",
        )
        == []
    )


def test_a_php_class_that_is_not_queued_is_out_of_scope() -> None:
    assert (
        found(
            Path("app/Services/Mailer.php"),
            "<?php",
            "class Mailer",
            "{",
            "        try {",
            "            $this->send();",
            "        } catch (\\Throwable $e) {",
            "            Log::error($e->getMessage());",
            "        }",
            "}",
        )
        == []
    )


def test_the_line_exemption_is_honoured() -> None:
    assert (
        found(
            QUEUED,
            *PHP_JOB_HEAD,
            "        foreach ($rows as $row) {",
            "            try {",
            "                $this->handleRow($row);",
            "            // standards: job-swallows-failure exempt -- per-row, counted and reported below",
            "            } catch (\\Throwable $e) {",
            "                $failures++;",
            "            }",
            "        }",
            "}",
        )
        == []
    )


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "brace-language jobs"))

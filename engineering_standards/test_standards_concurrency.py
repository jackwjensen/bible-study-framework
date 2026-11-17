"""Cases for the concurrency-bulk-bypass and concurrency-retry-overwrite rules.

standards: concurrency-bulk-bypass exempt -- this suite's fixtures are literal bypassing
writes, quoted so the detector can be tested against them, never writes this file performs.
standards: concurrency-retry-overwrite exempt -- same, for the catch-and-resave fixtures.

THE TWO CASES THAT MOTIVATED THEM are `test_a_bulk_update_without_a_version_predicate_is_flagged`
and `test_a_conflict_caught_and_resaved_is_flagged`: the two ways a correct version column
stops protecting anything. The first bypasses the change tracker, so no predicate is ever
emitted; the second lets the database refuse the write and then forces it through anyway.

THE NEGATIVES ARE THE HARDER HALF. A bulk write that DOES carry the version predicate is the
rule's own stated remedy, and a catch that surfaces the conflict is the correct handler --
reporting either would flag the fix as the defect. `test_a_save_after_the_handler_is_not_in_the_block`
is the one that constrains the implementation: without brace counting, any SaveChanges later
in the file would look like it belonged to the catch.

Run: python test_standards_concurrency.py   (or pytest)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_concurrency import (  # noqa: E402
    BULK_RULE,
    RETRY_RULE,
    check_concurrency,
)
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

EXEMPT = "nightly denormalisation job rebuilding its own output; no user edit can race it"
assert len(EXEMPT) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


def rules(name: str, *lines: str) -> list[str]:
    return [v.rule for v in check_concurrency(Path(name), list(lines))]


def messages(name: str, *lines: str) -> list[str]:
    return [v.message for v in check_concurrency(Path(name), list(lines))]


# ---- concurrency-bulk-bypass ------------------------------------------------------------------


def test_a_bulk_update_without_a_version_predicate_is_flagged() -> None:
    """The write that compiles straight to SQL, so no version predicate is ever emitted."""
    assert BULK_RULE in rules(
        "JobRepository.cs",
        '    await _db.Jobs.Where(j => j.Closed).ExecuteUpdateAsync(s => s.SetProperty(x => x.Status, "done"));',
    )


def test_the_synchronous_spelling_is_flagged() -> None:
    assert BULK_RULE in rules(
        "JobRepository.cs",
        "    _db.Jobs.Where(j => j.Closed).ExecuteUpdate(s => s.SetProperty(x => x.Status, closed));",
    )


def test_a_bulk_delete_is_not_the_lost_update_shape() -> None:
    """Deliberately out of scope: a lost update is a SURVIVING row whose change was erased, so
    a statement that removes the row has nothing to lose. Measured on the estate, every
    ExecuteDelete was a cascade, a retention prune or a GDPR erasure -- all shapes the rule
    already calls correct, so including them would have made this 60% noise on its first run."""
    assert not rules("JobRepository.cs", "    _db.Jobs.Where(j => j.Old).ExecuteDeleteAsync();")


def test_a_bulk_write_carrying_the_version_predicate_is_clean() -> None:
    """The rule's own stated remedy must never be the finding."""
    assert not rules(
        "JobRepository.cs",
        "    _db.Jobs.Where(j => j.Id == id && j.Version == expected).ExecuteUpdate(",
    )


def test_an_estate_specific_version_column_name_is_clean() -> None:
    """`RowVersion` is following the rule, not evading it."""
    assert not rules(
        "JobRepository.cs",
        "    _db.Jobs.Where(j => j.RowVersion == expected).ExecuteUpdate(s => s.SetProperty(x => x.N, n));",
    )


def test_an_ordinary_tracked_save_is_clean() -> None:
    assert not rules("JobService.cs", "    await _db.SaveChangesAsync(cancellationToken);")


# ---- concurrency-retry-overwrite --------------------------------------------------------------


def test_a_conflict_caught_and_resaved_is_flagged() -> None:
    """The database refused the write; this reloads and forces it through."""
    assert RETRY_RULE in rules(
        "JobService.cs",
        "        catch (DbUpdateConcurrencyException)",
        "        {",
        "            entry.Reload();",
        "            await _db.SaveChangesAsync();",
        "        }",
    )


def test_a_conflict_that_is_rethrown_is_clean() -> None:
    assert not rules(
        "JobService.cs",
        "        catch (DbUpdateConcurrencyException)",
        "        {",
        "            throw;",
        "        }",
    )


def test_a_conflict_answered_with_409_is_clean() -> None:
    """Surfacing the conflict is the correct handler."""
    assert not rules(
        "JobsController.cs",
        "        catch (DbUpdateConcurrencyException ex)",
        "        {",
        "            return Conflict(BuildComparison(ex));",
        "        }",
    )


def test_a_save_after_the_handler_is_not_in_the_block() -> None:
    """Brace counting, not proximity: a later save belongs to a different statement."""
    assert not rules(
        "JobService.cs",
        "        catch (DbUpdateConcurrencyException)",
        "        {",
        "            return Conflict();",
        "        }",
        "    }",
        "    public async Task Other()",
        "    {",
        "        await _db.SaveChangesAsync();",
        "    }",
    )


def test_a_nested_block_does_not_end_the_walk_early() -> None:
    """An `if` inside the handler must not be read as the end of it."""
    assert RETRY_RULE in rules(
        "JobService.cs",
        "        catch (DbUpdateConcurrencyException)",
        "        {",
        "            if (attempts < 3)",
        "            {",
        "                entry.Reload();",
        "            }",
        "            await _db.SaveChangesAsync();",
        "        }",
    )


# ---- PHP --------------------------------------------------------------------------------------


def test_a_doctrine_conflict_reflushed_is_flagged() -> None:
    assert RETRY_RULE in rules(
        "JobService.php",
        "        } catch (OptimisticLockException $e) {",
        "            $this->em->refresh($job);",
        "            $this->em->flush();",
        "        }",
    )


def test_a_namespaced_doctrine_catch_is_flagged() -> None:
    assert RETRY_RULE in rules(
        "JobService.php",
        "        } catch (\\Doctrine\\ORM\\OptimisticLockException $e) {",
        "            $this->em->flush();",
        "        }",
    )


def test_a_doctrine_conflict_rethrown_is_clean() -> None:
    assert not rules(
        "JobService.php",
        "        } catch (OptimisticLockException $e) {",
        "            throw new ConflictHttpException('changed by someone else', $e);",
        "        }",
    )


# ---- shared -----------------------------------------------------------------------------------


def test_the_bulk_message_names_the_acceptable_justification() -> None:
    message = messages("JobRepository.cs", "    _db.Jobs.ExecuteUpdate(s => s.SetProperty(x => x.N, n));")[0]
    assert "nightly job rebuilding its own output qualifies" in message


def test_the_retry_message_names_the_409() -> None:
    message = messages(
        "JobService.cs",
        "        catch (DbUpdateConcurrencyException)",
        "        {",
        "            await _db.SaveChangesAsync();",
        "        }",
    )[0]
    assert "409" in message


def test_a_line_exemption_silences_the_bulk_rule() -> None:
    assert not rules(
        "JobRepository.cs",
        f"    // standards: {BULK_RULE} exempt -- {EXEMPT}",
        "    _db.Jobs.ExecuteUpdate(s => s.SetProperty(x => x.N, n));",
    )


def test_a_line_exemption_silences_the_retry_rule() -> None:
    assert not rules(
        "JobService.cs",
        f"        // standards: {RETRY_RULE} exempt -- {EXEMPT}",
        "        catch (DbUpdateConcurrencyException)",
        "        {",
        "            await _db.SaveChangesAsync();",
        "        }",
    )


def test_an_unrelated_language_is_ignored() -> None:
    assert not rules("jobs.ts", "  await repo.createQueryBuilder().update().execute();")


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "concurrency cases"))

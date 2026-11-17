"""Cases for standards_deps_verdict: every state the gate can reach, and which of them block.

Decision 14 of the 2026-10-02 plan is asserted at its edges: a later major is a notice with more
than a year of the pinned line's support left and blocks with a year or less; a package with no
published lifecycle is judged by whether its line still ships.

Run: python test_standards_deps_verdict.py   (or pytest)
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_deps_declared import ACTION, DOCKER, NUGET, PYPI, Dependency  # noqa: E402
from standards_deps_lifecycle import Cycle  # noqa: E402
from standards_deps_record import DEFERRED, REJECTED, UPDATED, Decision  # noqa: E402
from standards_deps_registry import Answer  # noqa: E402
from standards_deps_verdict import (  # noqa: E402
    COVERED,
    CURRENT,
    EXPIRED,
    MAJOR_AVAILABLE,
    MAJOR_DUE,
    NEWER,
    UNPINNED,
    UNREACHABLE,
    UNSUPPORTED,
    judge,
)
from standards_selftest import run_module_tests  # noqa: E402

TODAY = date(2026, 10, 2)
RUFF = Dependency(PYPI, "ruff", "==0.16.5", Path("requirements.txt"), 1)
REASON = "0.16.10 reformats every file; take it with the formatting commit"
RUFF_RELEASES = Answer(releases=(("0.16.5", "2026-08-01", None), ("0.16.10", "2026-09-20", None)))


def mysql(version: str = "8.4.11") -> Dependency:
    return Dependency(DOCKER, "mysql", version, Path("docker-compose.yml"), 3)


MYSQL_RELEASES = Answer(
    releases=(("8.4.10", None, None), ("8.4.11", None, None), ("9.7.2", None, None)), preferred="9.7.2"
)


def mysql_cycles(end_of_84: date) -> list[Cycle]:
    return [Cycle("9.7", True, date(2034, 4, 30)), Cycle("8.4", True, end_of_84)]


def record(*decisions: Decision) -> dict[str, list[Decision]]:
    return {RUFF.key: list(decisions)}


def test_pinned_to_the_newest_passes() -> None:
    current = Dependency(PYPI, "ruff", "==0.16.10", Path("requirements.txt"), 1)
    assert judge(current, RUFF_RELEASES, None, {}, TODAY).state == CURRENT


def test_a_newer_patch_nobody_looked_at_blocks() -> None:
    verdict = judge(RUFF, RUFF_RELEASES, None, {}, TODAY)
    assert (verdict.state, verdict.blocks, verdict.target) == (NEWER, True, "0.16.10")


def test_an_active_deferral_covers_and_is_printed() -> None:
    deferral = Decision("0.16.10", DEFERRED, "2026-10-01", "Jack", REASON, "2026-10-20")
    verdict = judge(RUFF, RUFF_RELEASES, None, record(deferral), TODAY)
    assert (verdict.state, verdict.blocks) == (COVERED, False)
    assert "until 2026-10-20" in verdict.message and REASON in verdict.message


def test_an_expired_deferral_blocks() -> None:
    deferral = Decision("0.16.10", DEFERRED, "2026-09-01", "Jack", REASON, "2026-10-01")
    assert judge(RUFF, RUFF_RELEASES, None, record(deferral), TODAY).state == EXPIRED


def test_a_rejection_covers_only_the_release_it_names() -> None:
    rejection = Decision("0.16.10", REJECTED, "2026-10-01", "Jack", REASON)
    assert judge(RUFF, RUFF_RELEASES, None, record(rejection), TODAY).state == COVERED
    later = Answer(releases=(*RUFF_RELEASES.releases, ("0.16.11", "2026-09-30", None)))
    assert judge(RUFF, later, None, record(rejection), TODAY).state == NEWER


def test_an_update_the_manifest_never_made_still_blocks() -> None:
    claimed = Decision("0.16.10", UPDATED, "2026-10-01", "Claude")
    assert judge(RUFF, RUFF_RELEASES, None, record(claimed), TODAY).state == NEWER


def test_a_later_major_with_years_of_support_left_is_a_notice() -> None:
    """MySQL, 2026-10-02: 8.4 LTS supported to 2032, 9.7 LTS out. News, not a deadline."""
    verdict = judge(mysql(), MYSQL_RELEASES, mysql_cycles(date(2032, 4, 30)), {}, TODAY)
    assert (verdict.state, verdict.blocks) == (MAJOR_AVAILABLE, False)
    assert "9.7.2" in verdict.message and "2032-04-30" in verdict.message


def test_a_later_major_becomes_mandatory_inside_a_year_of_end_of_support() -> None:
    inside = judge(mysql(), MYSQL_RELEASES, mysql_cycles(TODAY + timedelta(days=365)), {}, TODAY)
    outside = judge(mysql(), MYSQL_RELEASES, mysql_cycles(TODAY + timedelta(days=366)), {}, TODAY)
    assert (inside.state, inside.blocks, inside.target) == (MAJOR_DUE, True, "9.7.2")
    assert outside.state == MAJOR_AVAILABLE


def test_a_line_past_its_end_of_support_blocks() -> None:
    verdict = judge(mysql(), MYSQL_RELEASES, mysql_cycles(TODAY - timedelta(days=1)), {}, TODAY)
    assert (verdict.state, verdict.blocks) == (UNSUPPORTED, True)


def test_a_patch_is_still_owed_while_a_major_is_only_news() -> None:
    verdict = judge(mysql("8.4.10"), MYSQL_RELEASES, mysql_cycles(date(2032, 4, 30)), {}, TODAY)
    assert (verdict.state, verdict.target) == (NEWER, "8.4.11")


def test_a_package_without_a_lifecycle_is_judged_by_whether_its_line_still_ships() -> None:
    """No published support dates: a later major is news while the pinned major keeps shipping,
    and mandatory once it has gone a year without a release (Jack, 2026-10-02)."""
    stripe = Dependency(NUGET, "Stripe.net", "52.3.0", Path("App.csproj"), 9)
    shipping = Answer(releases=(("52.3.0", "2026-06-01", None), ("53.0.0", "2026-08-01", None)))
    abandoned = Answer(releases=(("52.3.0", "2025-10-01", None), ("53.0.0", "2026-08-01", None)))
    assert judge(stripe, shipping, None, {}, TODAY).state == MAJOR_AVAILABLE
    assert judge(stripe, abandoned, None, {}, TODAY).state == MAJOR_DUE


def test_an_undated_line_without_a_lifecycle_is_never_called_abandoned() -> None:
    package = Dependency(NUGET, "X", "1.0.0", Path("App.csproj"), 1)
    undated = Answer(releases=(("1.0.0", None, None), ("2.0.0", None, None)))
    assert judge(package, undated, None, {}, TODAY).state == MAJOR_AVAILABLE


def test_a_lifecycle_with_no_announced_end_is_supported() -> None:
    cycles = [Cycle("9.7", True, None), Cycle("8.4", True, None)]
    assert judge(mysql(), MYSQL_RELEASES, cycles, {}, TODAY).state == MAJOR_AVAILABLE


def test_an_action_is_placed_in_its_line_by_its_tag_comment() -> None:
    newest = "a" * 40
    releases = Answer(
        releases=(("v7.0.1", "2026-09-01", newest), ("v7.0.0", "2026-06-01", None), ("v6.0.4", "2026-05-01", "b" * 40))
    )
    current = Dependency(ACTION, "actions/checkout", newest, Path("ci.yml"), 3, "v7.0.1")
    behind = Dependency(ACTION, "actions/checkout", "c" * 40, Path("ci.yml"), 3, "v7.0.0")
    assert judge(current, releases, None, {}, TODAY).state == CURRENT
    verdict = judge(behind, releases, None, {}, TODAY)
    assert (verdict.state, verdict.target) == (NEWER, newest), "a decision names the commit an Action is pinned by"


def test_a_floating_version_blocks_and_names_the_pin_to_use() -> None:
    verdict = judge(mysql("8.4"), MYSQL_RELEASES, mysql_cycles(date(2032, 4, 30)), {}, TODAY)
    assert (verdict.state, verdict.blocks) == (UNPINNED, True)
    assert "8.4.11" in verdict.message


def test_a_two_part_release_gets_its_patch_and_major_judged_like_any_other() -> None:
    """PostgreSQL: the major is the line, the minor is the patch. `16.1` behind `16.10` blocks;
    `17.6` existing while 16 has years of support left is news."""
    releases = Answer(releases=(("16.1-alpine", None, None), ("16.10-alpine", None, None), ("17.6-alpine", None, None)))
    cycles = [Cycle("17", False, date(2029, 11, 8)), Cycle("16", False, date(2028, 11, 9))]

    def postgres(version: str) -> Dependency:
        return Dependency(DOCKER, "postgres", version, Path("docker-compose.yml"), 4)

    behind = judge(postgres("16.1-alpine"), releases, cycles, {}, TODAY)
    assert (behind.state, behind.target) == (NEWER, "16.10-alpine")
    assert judge(postgres("16.10-alpine"), releases, cycles, {}, TODAY).state == MAJOR_AVAILABLE
    floating = judge(postgres("16-alpine"), releases, cycles, {}, TODAY)
    assert floating.state == UNPINNED and "16.10-alpine" in floating.message


def test_an_unreachable_registry_does_not_block_by_itself() -> None:
    """Whether it fails is the caller's call: warned on a laptop, failed in CI."""
    verdict = judge(RUFF, Answer(error="connection refused"), None, {}, TODAY)
    assert (verdict.state, verdict.blocks) == (UNREACHABLE, False)


def test_an_unreachable_lifecycle_is_said_not_assumed_away() -> None:
    """Falling back to the package rule would judge a database by whether it still ships."""
    verdict = judge(mysql(), MYSQL_RELEASES, None, {}, TODAY, lifecycle_error="mysql: timed out")
    assert verdict.state == UNREACHABLE and "support dates" in verdict.message


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "dependency verdicts"))

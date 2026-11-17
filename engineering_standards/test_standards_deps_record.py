"""Cases for standards_deps_record: the escape hatch's limits, held in code.

Decision 3 of the 2026-10-02 plan: a deferral is always dated, never longer than thirty days,
and always carries a reason. A limit that lives only in prose is a limit an agent reasons its
way around, so each one is asserted here from both sides -- refused just past the line,
accepted just inside it.

Run: python test_standards_deps_record.py   (or pytest)
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_deps_record import (  # noqa: E402
    DEFERRED,
    MAX_DEFERRAL_DAYS,
    REJECTED,
    UPDATED,
    Decision,
    RecordError,
    add_decision,
    covering_decision,
    has_record,
    load_record,
    save_record,
    validate,
)
from standards_selftest import run_module_tests  # noqa: E402

TODAY = date(2026, 10, 2)
REASON = "0.17 reformats every file; take it with the formatting commit"


def refused(decision: Decision) -> str:
    try:
        validate(decision, TODAY)
    except RecordError as error:
        return str(error)
    return ""


def deferral(until: date | None, reason: str = REASON) -> Decision:
    return Decision("0.17.0", DEFERRED, TODAY.isoformat(), "Jack", reason, until.isoformat() if until else None)


def test_a_deferral_is_never_longer_than_thirty_days() -> None:
    assert not refused(deferral(TODAY + timedelta(days=MAX_DEFERRAL_DAYS)))
    assert "at most" in refused(deferral(TODAY + timedelta(days=MAX_DEFERRAL_DAYS + 1)))


def test_a_deferral_without_a_date_is_refused() -> None:
    assert "silent no-update" in refused(deferral(None))


def test_a_deferral_into_the_past_is_refused() -> None:
    assert "past" in refused(deferral(TODAY - timedelta(days=1)))


def test_not_taking_a_release_needs_a_real_reason() -> None:
    assert "reason" in refused(deferral(TODAY + timedelta(days=7), reason="later"))
    assert "reason" in refused(Decision("9.0.0", REJECTED, TODAY.isoformat(), "Jack", "no"))
    assert not refused(Decision("9.0.0", REJECTED, TODAY.isoformat(), "Jack", REASON))


def test_an_update_needs_no_reason_and_no_date() -> None:
    assert not refused(Decision("0.16.10", UPDATED, TODAY.isoformat(), "Claude"))
    assert "deferrals only" in refused(Decision("0.16.10", UPDATED, TODAY.isoformat(), "Claude", until="2026-10-09"))


def test_an_expired_deferral_covers_nothing() -> None:
    decision = deferral(TODAY + timedelta(days=3))
    assert decision.is_active(TODAY + timedelta(days=3))
    assert not decision.is_active(TODAY + timedelta(days=4))


def test_the_record_round_trips_and_keeps_every_decision() -> None:
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        assert not has_record(root)
        save_record(root, {})
        assert has_record(root)
        add_decision(root, "pypi:ruff", deferral(TODAY + timedelta(days=7)), TODAY)
        add_decision(root, "pypi:ruff", Decision("0.17.0", UPDATED, TODAY.isoformat(), "Claude"), TODAY)
        record = load_record(root)
        assert [decision.decision for decision in record["pypi:ruff"]] == [DEFERRED, UPDATED]
        assert covering_decision(record, "pypi:ruff", "0.17.0").decision == UPDATED
        assert covering_decision(record, "pypi:ruff", "0.18.0") is None


def test_a_refused_decision_leaves_the_record_untouched() -> None:
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        save_record(root, {})
        before = (root / ".standards-dependencies.json").read_text(encoding="utf-8")
        refusal = ""
        try:
            add_decision(root, "pypi:ruff", deferral(None), TODAY)
        except RecordError as error:
            refusal = str(error)
        assert refusal, "an undated deferral was accepted"
        assert (root / ".standards-dependencies.json").read_text(encoding="utf-8") == before


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "dependency record"))

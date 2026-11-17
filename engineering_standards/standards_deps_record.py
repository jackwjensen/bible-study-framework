#!/usr/bin/env python3
"""The repository's record of version decisions: `.standards-dependencies.json`.

WHAT IT HOLDS, AND WHAT IT DELIBERATELY DOES NOT. Decisions, each about ONE release of ONE
dependency: `updated` (moved to it), `deferred` (not yet -- with a reason and an expiry), or
`rejected` (not this one -- with a reason). It never holds the pinned version, which lives in
the manifest that declares it (a second copy would be a second place to disagree), and it never
holds a "last checked" timestamp: freshness is proven by the gate checking live in CI, and a
timestamp in a committed file would be stale by the next run (plan 2026-10-02, decision 7).
So the file changes only when somebody decided something, and reads as the history of why the
repository is on the versions it is on.

THE ESCAPE HATCH HAS LIMITS IN CODE, not only in prose (decision 3): a deferral needs a reason
of at least MIN_EXEMPTION_REASON_LENGTH characters and an expiry no more than MAX_DEFERRAL_DAYS
away, and an expired deferral covers nothing. Who may write one is judgment the code cannot
make -- Claude never writes a deferral, it proposes one -- so every active deferral is printed
on every run, where it cannot be forgotten.

Source of truth: engineering-standards/engineering_standards/standards_deps_record.py
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH

RECORD_FILENAME = ".standards-dependencies.json"
RECORD_FORMAT = 1
MAX_DEFERRAL_DAYS = 30

UPDATED = "updated"
DEFERRED = "deferred"
REJECTED = "rejected"
DECISIONS = (UPDATED, DEFERRED, REJECTED)


class RecordError(ValueError):
    """A decision the record refuses, with the reason in words a person can act on."""


@dataclass(frozen=True)
class Decision:
    version: str
    decision: str
    date: str  # ISO date the decision was made
    by: str
    reason: str = ""
    until: str | None = None  # deferrals only

    def is_active(self, today: date) -> bool:
        """Whether this decision still covers its version on `today`."""
        if self.decision == DEFERRED:
            return self.until is not None and date.fromisoformat(self.until) >= today
        return True


def record_path(repo_root: Path) -> Path:
    return repo_root / RECORD_FILENAME


def has_record(repo_root: Path) -> bool:
    """Whether the repository has adopted the gate. No record, no gate (rollout is per repo)."""
    return record_path(repo_root).is_file()


def load_record(repo_root: Path) -> dict[str, list[Decision]]:
    """Decisions per dependency key (`pypi:ruff`). An absent record is an empty one."""
    path = record_path(repo_root)
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {key: [Decision(**entry) for entry in entry_list] for key, entry_list in raw.get("dependencies", {}).items()}


def save_record(repo_root: Path, record: dict[str, list[Decision]]) -> None:
    payload = {
        "format": RECORD_FORMAT,
        "dependencies": {
            key: [
                {name: value for name, value in asdict(decision).items() if value not in (None, "")}
                for decision in decisions
            ]
            for key, decisions in sorted(record.items())
        },
    }
    record_path(repo_root).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def covering_decision(record: dict[str, list[Decision]], key: str, version: str) -> Decision | None:
    """The newest decision about exactly this release of this dependency, active or not."""
    matching = [decision for decision in record.get(key, []) if decision.version == version]
    return matching[-1] if matching else None


def validate(decision: Decision, today: date) -> None:
    """Refuse a decision the record must not hold. Raises RecordError with the reason."""
    if decision.decision not in DECISIONS:
        raise RecordError(f"decision must be one of {', '.join(DECISIONS)}")
    if decision.decision in (DEFERRED, REJECTED) and len(decision.reason.strip()) < MIN_EXEMPTION_REASON_LENGTH:
        raise RecordError(
            f"a {decision.decision} release needs a reason of at least {MIN_EXEMPTION_REASON_LENGTH} "
            "characters: what is wrong with it, and what would change the decision"
        )
    if decision.decision == DEFERRED:
        if not decision.until:
            raise RecordError("a deferral needs --until: an escape hatch without a date becomes a silent no-update")
        expiry = date.fromisoformat(decision.until)
        if expiry < today:
            raise RecordError(f"--until {decision.until} is already in the past")
        if expiry > today + timedelta(days=MAX_DEFERRAL_DAYS):
            raise RecordError(
                f"a deferral may run at most {MAX_DEFERRAL_DAYS} days (until {today + timedelta(days=MAX_DEFERRAL_DAYS)})"
            )
    elif decision.until:
        raise RecordError("--until applies to deferrals only")


def add_decision(repo_root: Path, key: str, decision: Decision, today: date) -> None:
    """Validate and append one decision; the record keeps every decision ever made."""
    validate(decision, today)
    record = load_record(repo_root)
    record.setdefault(key, []).append(decision)
    save_record(repo_root, record)

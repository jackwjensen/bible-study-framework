#!/usr/bin/env python3
"""Enforce the coding standards that language analyzers cannot express.

Roslyn naming rules can filter by symbol kind and modifier but not by *type*, so
"bool properties start with Is/Has/Can/Should" and "money is never float/double"
have no analyzer equivalent. Ruff has no file-length rule at all. This script
covers exactly that residue -- nothing here duplicates a rule an analyzer already
enforces.

The checks themselves live in standards_checks.py, which must sit beside this file.
This half is the driver: walking the tree, ratcheting the baseline, reporting, CLI.

Checks: file-too-long, baseline-grew, bool-prefix, money-not-decimal,
unset-not-zero, payload-default, date-sentinel, date-out-of-range, razor-var,
php-strict-types. unset-not-zero and the date rules are the mechanical half of
"unset is null" and "dates declare a sensible range"; payload-default is a
separate rule about missing external data rather than about null-versus-zero.
The judgment halves stay prose in claude/rules/*-standards.md.

Usage:
    python check-source-limits.py [--root DIR] [--config FILE] [--format github]

Exit codes: 0 = clean, 1 = violations found, 2 = bad invocation.

Source of truth: engineering-standards/scripts/check-source-limits.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Import the sibling module by path rather than relying on the caller's cwd: the commit
# hook and the CI job both invoke this script from the repo root, not from scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_checks import (  # noqa: E402  (path must be set before the import)
    CheckConfig,
    Violation,
    check_source_file,
    should_check,
)


def violation_key(violation: Violation, repo_root: Path) -> str:
    """Stable identity for a member-level violation, independent of line number.

    Keyed on file + rule + the member name rather than the line, so unrelated edits that
    shift lines do not resurrect a grandfathered finding.
    """
    relative = violation.path.relative_to(repo_root).as_posix()
    member = violation.message.split("'")[1] if "'" in violation.message else ""
    return f"{relative}::{violation.rule}::{member}"


def load_baseline(baseline_path: Path) -> tuple[dict[str, int], set[str]]:
    """Grandfathered file sizes and member-level violations."""
    if not baseline_path.is_file():
        return {}, set()

    raw = json.loads(baseline_path.read_text(encoding="utf-8"))
    return raw.get("oversizedFiles", {}), set(raw.get("knownViolations", []))


def read_source_lines(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as read_error:
        print(f"warning: could not read {path}: {read_error}", file=sys.stderr)
        return None


def collect_oversized_files(root: Path, config: CheckConfig) -> dict[str, int]:
    """Current line counts of every file already over the limit, for --write-baseline."""
    oversized: dict[str, int] = {}

    for path in sorted(root.rglob("*")):
        if not path.is_file() or not should_check(path, root, config):
            continue

        lines = read_source_lines(path)
        if lines is not None and len(lines) > config.max_file_lines:
            oversized[path.relative_to(root).as_posix()] = len(lines)

    return oversized


def collect_violations(root: Path, config: CheckConfig, baseline: dict[str, int]) -> list[Violation]:
    violations: list[Violation] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file() or not should_check(path, root, config):
            continue

        lines = read_source_lines(path)
        if lines is None:
            continue

        violations.extend(check_source_file(path, lines, config, baseline, root))

    return violations


def report(violations: list[Violation], root: Path, output_format: str) -> None:
    for violation in violations:
        relative = violation.path.relative_to(root).as_posix()
        if output_format == "github":
            print(
                f"::error file={relative},line={violation.line}::"
                f"[{violation.rule}] {violation.message}"
            )
        else:
            print(f"{relative}:{violation.line}: [{violation.rule}] {violation.message}")


def write_baseline(root: Path, baseline_path: Path, config: CheckConfig) -> int:
    oversized = collect_oversized_files(root, config)
    member_violations = sorted(
        violation_key(violation, root)
        for violation in collect_violations(root, config, oversized)
        if violation.rule != "file-too-long"
    )
    baseline_path.write_text(
        json.dumps(
            {
                "_comment": (
                    "Violations that already existed when the standard was adopted. "
                    "Oversized files may shrink but never grow; grandfathered member "
                    "violations stay listed until fixed. NEW code must comply outright. "
                    "Regenerate with: check-source-limits.py --write-baseline"
                ),
                "maxFileLines": config.max_file_lines,
                "oversizedFiles": oversized,
                "knownViolations": member_violations,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"check-source-limits: baselined {len(oversized)} oversized file(s) "
        f"and {len(member_violations)} member violation(s)"
    )
    return 0


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Directory to scan (default: cwd)")
    parser.add_argument(
        "--config",
        default=".standards.json",
        help="Optional JSON config for per-repo tuning (default: .standards.json)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "github"),
        default="text",
        help="Output format; 'github' emits workflow error annotations",
    )
    parser.add_argument(
        "--baseline",
        default=".standards-baseline.json",
        help="Grandfathered oversized files (default: .standards-baseline.json)",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Record current oversized files as the baseline instead of failing on them",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)

    root = Path(arguments.root).resolve()
    if not root.is_dir():
        print(f"error: --root '{root}' is not a directory", file=sys.stderr)
        return 2

    config = CheckConfig.load(root / arguments.config)
    baseline_path = root / arguments.baseline

    if arguments.write_baseline:
        return write_baseline(root, baseline_path, config)

    oversized_baseline, known_violations = load_baseline(baseline_path)
    violations = collect_violations(root, config, oversized_baseline)

    grandfathered = [v for v in violations if violation_key(v, root) in known_violations]
    remaining = [v for v in violations if violation_key(v, root) not in known_violations]

    if not remaining:
        suffix = f" ({len(grandfathered)} grandfathered)" if grandfathered else ""
        print(f"check-source-limits: clean{suffix}")
        return 0

    report(remaining, root, arguments.format)
    print(f"\ncheck-source-limits: {len(remaining)} violation(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

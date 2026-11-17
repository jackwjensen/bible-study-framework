#!/usr/bin/env python3
"""The debt ledger: reading it, writing it, and who may authorise a write.

Rules here: none. This is the ratchet the driver operates, plus the policy gating it.

WHY IT IS ITS OWN MODULE. `check-source-limits.py` named four jobs in its own docstring --
walking the tree, RATCHETING THE BASELINE, reporting, CLI. The second was always a separable
subject; adding consent gave it a fifth collaborator (a human at a terminal) and pushed the
file past its own 500-line limit, which is how the seam became visible rather than how it was
chosen. Reading, writing and authorising the ledger are one subject and live here; walking,
reporting and the CLI stay in the driver. (Never split for the number -- see
claude/rules/refactoring.md.)

THE POLICY, stated by Jack 2026-08-27, when a review agent proposed baselining a repo's
existing dependencies so that only new ones would need justifying:

    "you tend to use it to avoid following the rules, and thereby invalidating those rules
    and making them ineffective"

A baseline written without consent is indistinguishable from the rule not existing. The ratchet
is for DEBT -- work somebody looked at and deliberately deferred -- and the failure mode is an
agent under time pressure reaching for it to turn a red build green. `NEVER_BASELINED` already
refuses the findings for which deferral is not even coherent; this refuses the *unattended act*
for all the rest.

Source of truth: engineering-standards/engineering_standards/standards_baseline.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TextIO

from standards_checks import CheckConfig, Violation
from standards_versions import PACK_VERSION

# The scanner's baseline: grandfathered findings, written by --write-baseline and read by every
# scan. One name for every module that asks whether a repo carries recorded debt.
BASELINE_FILE = ".standards-baseline.json"

# ESLint keeps its own ratchet, in its own file, in its own format. It is a baseline by every
# meaning of the word -- existing violations recorded so the gate goes green -- and nothing in
# this pack used to mention it.
ESLINT_SUPPRESSIONS_FILE = "eslint-suppressions.json"


def eslint_suppressed_count(root: Path) -> tuple[int, int]:
    """`(files, violations)` recorded in the repo's ESLint suppressions file, or `(0, 0)`.

    WHY THIS IS COUNTED HERE, in a Python scanner that does not run ESLint. A repo can carry
    TWO baselines: `.standards-baseline.json`, which this pack writes and reports, and
    `eslint-suppressions.json`, which `eslint --suppress-all` writes and which nothing in this
    pack ever named. So "reset the baseline" reads as one action and does half the job.

    Measured in allegro-it-services on 2026-09-03: deleting `.standards-baseline.json`
    surfaced 205 findings and felt like a full reset. The ESLint file was still holding 96
    suppressed violations across 66 files -- 66 `max-lines-per-function` and 30 `complexity` --
    including a 594-line component whose 220-line function read as clean while a 79-line one
    beside it failed. That inconsistency is very hard to diagnose from the outside, and it is
    the reason this count now appears on the same line as the others.

    Read defensively: an absent or unparseable file is reported as nothing suppressed rather
    than raising, because this is a report line and not a gate.
    """
    path = root / ESLINT_SUPPRESSIONS_FILE
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return 0, 0
    if not isinstance(recorded, dict):
        return 0, 0

    violations = 0
    for rules in recorded.values():
        if not isinstance(rules, dict):
            continue
        for entry in rules.values():
            if isinstance(entry, dict) and isinstance(entry.get("count"), int):
                violations += entry["count"]
    return len(recorded), violations


def report_eslint_suppressions(root: Path) -> tuple[int, int]:
    """Print what ESLint's own baseline is holding, and return the counts.

    Printed beside `tuned:` and `exempt:` and BEFORE any violations, because relief matters
    most on a run that is already red -- that is when somebody is deciding what the repo still
    owes. Silent when there is nothing suppressed: an absent file is not news.
    """
    files, violations = eslint_suppressed_count(root)
    if violations:
        print(
            f"suppressed: {ESLINT_SUPPRESSIONS_FILE} holds {violations} violation(s) "
            f"across {files} file(s) -- eslint's own baseline, reset separately"
        )
    return files, violations


def baseline_consent_granted(
    root: Path,
    entries: list[str],
    consent_flag: bool,
    stream: TextIO | None = None,
) -> bool:
    """Whether a human has agreed to grandfather `entries`.

    THE RULE THIS ENFORCES, stated by Jack 2026-08-27: "you tend to use it to avoid following
    the rules, and thereby invalidating those rules and making them ineffective." A baseline
    written without consent is indistinguishable from the rule not existing -- the ratchet is
    for debt somebody has looked at and deferred, and an agent under time pressure reaches for
    it to turn a build green.

    THREE OUTCOMES, and the non-interactive one is the load-bearing half. A prompt that
    degrades to "yes" when nobody is there to answer is not a gate; it is a gate-shaped delay.
    So a run with no terminal REFUSES unless `--baseline-consent` says a human already decided.
    Failing closed is the safe direction: the cost is a red CI run that names the problem, and
    the cost of the other choice is a silently emptied rulebook.

    Consent is INFORMED or it is not consent: the caller is shown what would be absorbed --
    how many findings, which rules, which files -- before being asked. A bare "baseline? [y/N]"
    would be the same rubber stamp with an extra keystroke.
    """
    if not entries:
        return True

    output = stream or sys.stderr
    rules: dict[str, int] = {}
    for entry in entries:
        rule = entry.rsplit(":", 1)[-1] if ":" in entry else entry
        rules[rule] = rules.get(rule, 0) + 1

    print(f"\n{len(entries)} finding(s) would be grandfathered into the baseline:", file=output)
    for rule, count in sorted(rules.items(), key=lambda pair: (-pair[1], pair[0])):
        print(f"  {count:>4}  {rule}", file=output)
    print(
        "\nA baseline is for DEBT -- work looked at and deliberately deferred. It is not a way "
        "to make a red build green. Anything live (a real defect, a licence exposure, a "
        "credential) should be fixed or exempted with a written reason instead.",
        file=output,
    )

    if consent_flag:
        print("Consent given via --baseline-consent.", file=output)
        return True

    return _prompt_for_consent(entries, output)


def _prompt_for_consent(entries: list[str], output: TextIO) -> bool:
    """Ask, and treat every way of not answering as a refusal.

    Split from `baseline_consent_granted` so the INFORMED half (showing what would be
    absorbed) and the CONSENT half are separate; between them they held seven returns
    against the pack's own limit of six.
    """
    # TWO independent refusals, because one of them lies. `isatty()` is the intent check, but
    # it reported a terminal under Git Bash on Windows even with stdin redirected from
    # /dev/null (measured 2026-08-27) -- and the fall-through then crashed in `input()` with an
    # EOFError traceback rather than refusing. A gate whose failure mode is a stack trace is
    # not a gate: catching EOF and treating it AS refusal is what actually makes this fail
    # closed, on every platform, whatever isatty() claims.
    if not sys.stdin.isatty():
        return _refuse_unattended()

    try:
        answer = input(f"Grandfather these {len(entries)} finding(s)? [y/N] ").strip().lower()
    except EOFError:
        return _refuse_unattended()
    except KeyboardInterrupt:
        print("\nBaseline not written.", file=output)
        return False

    if answer in {"y", "yes"}:
        return True

    print("Baseline not written.", file=output)
    return False


def _refuse_unattended() -> bool:
    """Say why nothing was written, and refuse. Always False -- the name is the return value."""
    print(
        "\nerror: refusing to write a baseline in a non-interactive run. Nobody is here to "
        "agree to it, and baselining unattended is how a rule quietly stops applying.\n"
        "       Run it in a terminal, or pass --baseline-consent if a human has already "
        "decided.",
        file=sys.stderr,
    )
    return False


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


def write_baseline_file(
    baseline_path: Path,
    config: CheckConfig,
    oversized: dict[str, int],
    member_violations: list[str],
    consent_flag: bool = False,
) -> int:
    """Write the ledger, once a human agrees to what it absorbs.

    TAKES the findings rather than computing them, so this module never needs the scanner --
    which is what keeps the dependency pointing one way. The driver walks the tree (its job)
    and hands the result here; this owns the ledger's FORMAT and the policy gating a write.

    The caller must pass the WHOLE tree's findings. A baseline written from a subset silently
    DROPS every entry outside it, turning grandfathered debt into "fixed" without anyone
    touching it. `main` refuses --write-baseline with --staged/--files for the same reason;
    this docstring is the second lock, because a wrong baseline cannot be spotted by reading it.

    # Documentation findings are baselined too, so adoption can grandfather a docs-less
    # repo the same way it grandfathers an oversized file. check_gitignore deliberately is
    # NOT: an unignored .env is a live credential leak, and grandfathering one would file a
    # security defect under "pre-existing".
    #
    # NEVER_BASELINED generalises that judgement, because a second and third rule turned out to
    # need it (2026-08-21). A baseline is for DEBT -- work that has been looked at and
    # deliberately deferred, where "not yet" is a coherent answer. A support deadline is not
    # debt: the date arrives whether or not anybody grandfathered it, so baselining one does
    # not defer the work, it only stops the repo being told. That is the same failure as
    # filing an unignored .env under "pre-existing", one clock removed.
    #
    # `runtime-support` was in this position without anybody noticing. It was added
    # 2026-08-18 and its docstring says the scanner "FAILS a repo that falls behind" -- true
    # for a repo already adopted, false on the /apply-standards run that adopts it, which
    # would have written the finding straight into the ledger. Nothing has hit it only because
    # the estate reached net10.0 first. Found while adding `ef-provider-support`, which has
    # the identical shape and three live instances.
    #
    # The escape hatch is unchanged and is the point: a line exemption with a written reason.
    # "We are waiting for the provider to ship" is a perfectly good answer. Silence is not.
    """
    # Oversized files are absorbed too, so they count toward what consent is being asked for.
    absorbed = [*member_violations, *(f"{name}:file-too-long" for name in oversized)]
    if not baseline_consent_granted(baseline_path.parent, absorbed, consent_flag):
        return 2
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
                # Provenance only -- which scanner version wrote this baseline. Drift is
                # still decided by file comparison in sync_pack.py, never by this label.
                "packVersion": PACK_VERSION,
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

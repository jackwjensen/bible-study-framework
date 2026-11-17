#!/usr/bin/env python3
"""`.standards.json` may relax a rule. It may not do so silently.

THE HOLE THIS CLOSES. Every other relaxation in this pack states its case where somebody
meets it: an exemption sits in the file and is printed every run, a baseline is a reviewed
ledger, and `NEVER_BASELINED` refuses the findings for which deferral is not even coherent.
`.standards.json` could do none of that. One line disabled any rule, required no argument,
and appeared in no output -- so a repo with `{"checkGitignore": false}` printed exactly the
`clean` that an honest repo printed. Bolting every front door while leaving that open is
worth very little.

Two halves, and both are load-bearing:
  * a weakening must carry a reason, at the exemption floor;
  * a weakening must be NAMED in the summary, or requiring the reason only moves the
    silence somewhere quieter.

A tightening is free. Charging a repo an argument for `maxFileLines: 300` would teach
people that the config is hostile, and the next thing they learn is `--no-verify`.

Run: python test_standards_tuning.py   (or pytest)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PACK_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(PACK_DIRECTORY))

from standards_config import (  # noqa: E402  (path set above)
    DEFAULT_MAX_FILE_LINES,
    MAX_TUNABLE_FILE_LINES,
    CheckConfig,
    collect_tuning,
)
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402  (same reason)
from standards_selftest import run_module_tests  # noqa: E402  (same reason)

GOOD_REASON = "vendored upstream copy; editing it would defeat the fixture"


def load_config(raw: dict) -> CheckConfig:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ".standards.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return CheckConfig.load(path)


def refusal(raw: dict) -> str:
    """The message load() dies with, or '' if it did not die."""
    try:
        load_config(raw)
    except SystemExit as refused:
        return str(refused)
    return ""


def test_no_config_is_not_a_weakening() -> None:
    """The overwhelmingly common case must stay free of ceremony."""
    assert CheckConfig.load(None).tuning == ()


def test_tightening_needs_no_reason() -> None:
    """A stricter limit is not a relaxation; asking it to argue would be backwards."""
    config = load_config({"maxFileLines": DEFAULT_MAX_FILE_LINES - 200})
    assert config.tuning == (), "a lower limit must not be treated as a weakening"


def test_disabling_a_check_without_a_reason_is_refused() -> None:
    """The exact one-liner that used to work: turn a rule off, say nothing."""
    message = refusal({"checkMoneyTypes": False})
    assert message, "disabling a check with no stated reason must be refused"
    assert "checkMoneyTypes" in message, f"the refusal must name the setting; got: {message}"


def test_the_gitignore_check_cannot_be_switched_off_quietly() -> None:
    """The sharpest case: NEVER_BASELINED protects this rule, config used to bypass it.

    An unignored .env is a live credential leak, which is why the baseline refuses to
    grandfather it. A boolean that disabled the same rule made that protection decorative.
    """
    assert refusal({"checkGitignore": False}), (
        "checkGitignore is refused from the baseline as a live security finding; the "
        "config file must not be a quieter way to reach the same result"
    )


def test_a_shrug_is_not_a_reason() -> None:
    """The floor is the only thing between a reason and a formality."""
    assert refusal({"checkMoneyTypes": False, "reasons": {"checkMoneyTypes": "n/a"}}), (
        f"a reason under {MIN_EXEMPTION_REASON_LENGTH} characters must be refused"
    )


def test_a_stated_reason_is_accepted_and_carried() -> None:
    """With an argument attached, the relaxation is allowed -- and recorded for the summary."""
    config = load_config(
        {
            "excludePathFragments": ["/tests/fixtures/"],
            "reasons": {"excludePathFragments": GOOD_REASON},
        }
    )
    assert len(config.tuning) == 1, "the weakening must be recorded, not merely permitted"
    setting, _value, reason = config.tuning[0]
    assert setting == "excludePathFragments"
    assert reason == GOOD_REASON, "the reason must survive to the summary verbatim"


def test_an_absurd_limit_is_refused_even_with_a_reason() -> None:
    """Past the ceiling it is not a threshold, it is the rule off with the light left on."""
    message = refusal(
        {
            "maxFileLines": 100000,
            "reasons": {"maxFileLines": GOOD_REASON + " and then some more words"},
        }
    )
    assert message, f"maxFileLines above {MAX_TUNABLE_FILE_LINES} must be refused outright"
    assert str(MAX_TUNABLE_FILE_LINES) in message, "the refusal must state the ceiling"


def test_a_defensible_limit_below_the_ceiling_is_allowed() -> None:
    """Firm is not the same as unusable: a real repo may genuinely need a higher limit."""
    config = load_config(
        {
            "maxFileLines": 800,
            "reasons": {"maxFileLines": "generated API client; regenerated, never hand-edited"},
        }
    )
    assert config.max_file_lines == 800
    assert len(config.tuning) == 1


def test_a_prose_comment_does_not_count_as_a_reason() -> None:
    """Every real .standards.json in the estate documents itself in `_comment`.

    That is the right instinct and the wrong mechanism: a blob cannot say WHICH setting it
    defends, so adding a second weakening later inherits the first one's justification.
    """
    assert refusal({"checkMoneyTypes": False, "_comment": GOOD_REASON + " at length"}), (
        "_comment is not attached to a setting and must not satisfy the requirement"
    )


def test_reasons_must_be_an_object() -> None:
    """A malformed reasons block must say so, not silently defend nothing."""
    assert refusal({"checkMoneyTypes": False, "reasons": "because"})


def test_collect_tuning_reports_every_undefended_setting_at_once() -> None:
    """One run, one list -- fixing these one refusal at a time is a bad afternoon."""
    message = refusal({"checkMoneyTypes": False, "checkDocs": False, "checkFilenames": False})
    for setting in ("checkMoneyTypes", "checkDocs", "checkFilenames"):
        assert setting in message, f"{setting} missing from: {message}"


def test_declarations_are_not_weakenings() -> None:
    """userDocs and docsRouteInventories turn checks ON. They owe nobody an explanation."""
    assert collect_tuning({"docsRouteInventories": [{"file": "x", "pattern": "y"}]}) == ()


def test_raising_the_gate_timeout_without_a_reason_is_refused() -> None:
    """A longer leash on the hang guard is a weakening like any other: it may be right
    (sourcetext.ai's suite builds a three-corpus site), and it says why, where the summary
    prints it."""
    message = refusal({"gateTimeoutSeconds": 1800})
    assert message, "raising the gate timeout with no stated reason must be refused"
    assert "gateTimeoutSeconds" in message


def test_lowering_the_gate_timeout_is_a_tightening() -> None:
    assert collect_tuning({"gateTimeoutSeconds": 300}) == ()


def test_the_summary_names_the_tuning() -> None:
    """End to end: a relaxed repo must not print the same thing an honest one prints."""
    limits = PACK_DIRECTORY / "check-source-limits.py"
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / ".git").mkdir()
        (repo / "app").mkdir()
        (repo / "app" / "big.py").write_text("\n".join(f"VALUE_{n} = {n}" for n in range(900)) + "\n", encoding="utf-8")
        (repo / ".standards.json").write_text(
            json.dumps(
                {
                    "maxFileLines": 1000,
                    "reasons": {"maxFileLines": "generated client; regenerated, never hand-edited"},
                }
            ),
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(limits), "--root", str(repo)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert "tuned: maxFileLines" in result.stdout, (
            "the summary must NAME the relaxation; a silent one is the back door this "
            f"module exists to close. Got:\n{result.stdout}{result.stderr}"
        )


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "config tuning"))

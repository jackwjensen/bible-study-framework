"""Cases for the dependency-holdback rule, and for baseline consent.

THE CASE THAT MOTIVATED THE RULE is `test_the_invotrack_shape_is_flagged`. InvoTrack's
dependabot.yml carried four ignores with careful prose reasons and no dates, while the decision
to move Stripe.net to 52.3.0 lived in a C# comment above the version string. A review agent
trusted the stale comment and REVERTED the Dependabot bump that was implementing the newer
decision. Two hiding places for one class of decision, and the wrong one won.

The negatives matter as much. A `dependency-name` outside the `ignore:` block is Dependabot's
normal grouping syntax, not a hold-back; a dated marker is the whole point and must pass; and
the date is deliberately NOT compared against today, because failing on an expired REVISIT
would punish the repo that wrote an honest near-term date and reward the one that wrote 2099.

BASELINE CONSENT is tested here rather than in test_standards_baseline.py because it is the
same policy: Jack, 2026-08-27 -- "you tend to use it to avoid following the rules, and thereby
invalidating those rules and making them ineffective." An undated hold-back and an unattended
baseline are the same move.

Run: python test_standards_holdback.py   (or pytest)
"""

from __future__ import annotations

import importlib.util
import io as _io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_baseline import baseline_consent_granted  # noqa: E402
from standards_core import CheckConfig  # noqa: E402
from standards_packages import HOLDBACK_RULE, check_dependency_holdback  # noqa: E402
from standards_scope import should_check  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

DEPENDABOT = Path(".github/dependabot.yml")


def found(*lines: str) -> list[str]:
    return [v.message for v in check_dependency_holdback(DEPENDABOT, list(lines))]


def _driver():
    spec = importlib.util.spec_from_file_location("driver", Path(__file__).resolve().parent / "check-source-limits.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- the rule fires --------------------------------------------------------------------------


def test_the_invotrack_shape_is_flagged() -> None:
    """A reason with no expiry. This is what four of InvoTrack's five ignores looked like."""
    messages = found(
        "    ignore:",
        "      # Pomelo has no EF Core 10 provider, so anything that transitively pulls",
        "      # EF Core 10 breaks the MySQL provider. Revisit when Pomelo ships 10.x.",
        '      - dependency-name: "Microsoft.EntityFrameworkCore*"',
        '        update-types: ["version-update:semver-major"]',
    )
    assert len(messages) == 1, messages
    assert "Microsoft.EntityFrameworkCore*" in messages[0]


def test_no_comment_at_all_is_flagged() -> None:
    messages = found(
        "    ignore:",
        '      - dependency-name: "MudBlazor"',
    )
    assert len(messages) == 1, messages


def test_a_marker_without_a_date_is_flagged_and_says_so() -> None:
    """The near-miss: the marker is present, the expiry is not. Worth a distinct message,
    because the fix is different from adding a marker wholesale."""
    messages = found(
        "    ignore:",
        "      # standards: dependency-holdback -- waiting on the vendor to ship a build",
        '      - dependency-name: "Stripe.net"',
    )
    assert len(messages) == 1, messages
    assert "no `REVISIT <date>`" in messages[0], messages[0]


def test_every_entry_is_judged_separately() -> None:
    """One justified hold-back does not cover the entry below it."""
    messages = found(
        "    ignore:",
        "      # standards: dependency-holdback -- vendor gap. REVISIT 2026-10-01",
        '      - dependency-name: "Pomelo"',
        '      - dependency-name: "MudBlazor"',
    )
    assert len(messages) == 1, messages
    assert "MudBlazor" in messages[0]


# ---- the rule stays silent -------------------------------------------------------------------


def test_a_dated_marker_passes() -> None:
    assert (
        found(
            "    ignore:",
            "      # standards: dependency-holdback -- Pomelo has no EF Core 10 provider.",
            "      # REVISIT 2026-10-01",
            '      - dependency-name: "Microsoft.EntityFrameworkCore*"',
        )
        == []
    )


def test_a_single_line_dated_marker_passes() -> None:
    assert (
        found(
            "    ignore:",
            "      # standards: dependency-holdback -- vendor gap. REVISIT 2026-12-31",
            '      - dependency-name: "Foo"',
        )
        == []
    )


def test_an_expired_date_still_passes() -> None:
    """Deliberate. A past REVISIT is a prompt to re-decide, not a build failure -- failing on
    it would punish an honest near-term date and reward `REVISIT 2099-01-01`."""
    assert (
        found(
            "    ignore:",
            "      # standards: dependency-holdback -- long overdue. REVISIT 2020-01-01",
            '      - dependency-name: "Foo"',
        )
        == []
    )


def test_dependency_name_outside_the_ignore_block_is_not_a_holdback() -> None:
    """Dependabot uses `dependency-name` in `groups:` and `allow:` too. Only `ignore:` is a
    decision to stay behind; flagging the others would be noise on normal config."""
    assert (
        found(
            "    groups:",
            "      all:",
            "        patterns:",
            '          - dependency-name: "Microsoft.*"',
        )
        == []
    )


def test_the_ignore_block_ends_at_a_dedent() -> None:
    """`commit-message:` after the ignore list must not keep the section open."""
    assert (
        found(
            "    ignore:",
            "      # standards: dependency-holdback -- reason. REVISIT 2026-10-01",
            '      - dependency-name: "Foo"',
            "    commit-message:",
            "      prefix: deps",
            "    allow:",
            '      - dependency-name: "Bar"',
        )
        == []
    )


def test_a_non_dependabot_yaml_is_ignored() -> None:
    assert [
        v.message
        for v in check_dependency_holdback(
            Path(".github/workflows/deploy.yml"),
            ["    ignore:", '      - dependency-name: "Foo"'],
        )
    ] == []


# ---- wiring ----------------------------------------------------------------------------------


def test_dependabot_yml_is_in_scope() -> None:
    """A rule that is correct but never reached is indistinguishable from one that passes."""
    root = Path(__file__).resolve().parent.parent
    assert should_check(root / ".github/dependabot.yml", root, CheckConfig())
    assert should_check(root / ".github/dependabot.yaml", root, CheckConfig())


def test_it_cannot_be_baselined() -> None:
    """An undated hold-back is not deferred debt -- it is a decision missing its expiry, and
    baselining it is exactly how "behind on purpose" becomes "behind, and nobody remembers"."""
    assert HOLDBACK_RULE in _driver().NEVER_BASELINED


# ---- baseline consent ------------------------------------------------------------------------


def test_consent_is_not_asked_when_there_is_nothing_to_absorb() -> None:
    assert baseline_consent_granted(Path("."), [], consent_flag=False) is True


def test_consent_flag_grants_without_a_terminal() -> None:
    sink = _io.StringIO()
    assert baseline_consent_granted(Path("."), ["a.cs::bool-prefix::Foo"], consent_flag=True, stream=sink) is True
    assert "would be grandfathered" in sink.getvalue()


def test_consent_is_refused_when_stdin_cannot_answer() -> None:
    """The load-bearing half. A prompt that degrades to "yes" when nobody is there is not a
    gate. Verified by replacing stdin with something that reports no terminal AND raises EOF on
    read -- both refusals must hold, because isatty() was observed lying under Git Bash on
    Windows (2026-08-27) and the fall-through then crashed instead of refusing."""

    class _NoTerminal:
        def isatty(self) -> bool:
            return False

        def readline(self) -> str:
            raise EOFError

    original = sys.stdin
    sys.stdin = _NoTerminal()  # type: ignore[assignment]
    try:
        granted = baseline_consent_granted(
            Path("."), ["a.cs::bool-prefix::Foo"], consent_flag=False, stream=_io.StringIO()
        )
    finally:
        sys.stdin = original
    assert granted is False


def test_consent_survives_a_lying_isatty() -> None:
    """The regression test for the bug testing found: isatty() claiming a terminal where there
    is none must still refuse rather than raise EOFError out of input()."""

    class _LyingTerminal:
        def isatty(self) -> bool:
            return True

        def readline(self) -> str:
            raise EOFError

    original = sys.stdin
    sys.stdin = _LyingTerminal()  # type: ignore[assignment]
    try:
        granted = baseline_consent_granted(
            Path("."), ["a.cs::bool-prefix::Foo"], consent_flag=False, stream=_io.StringIO()
        )
    finally:
        sys.stdin = original
    assert granted is False


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "dependency-holdback and baseline-consent cases"))

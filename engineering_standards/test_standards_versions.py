"""Cases for the action-version rule.

The negatives carry the weight here. A version rule that fires on a commit-SHA pin, or on an
action nobody has taken a position on, becomes noise that gets exempted wholesale -- and an
exempted rule is a rule that is not running. Each negative below is a shape that exists in
this estate today.
"""

from __future__ import annotations

import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from standards_core import CheckConfig
from standards_dispatch import check_source_file
from standards_scope import should_check
from standards_selftest import run_module_tests
from standards_versions import (
    ACTION_FLOORS,
    action_major,
    check_action_versions,
    check_ci_toolchain,
    check_dotnet_runtime_support,
)

WORKFLOW = Path(".github/workflows/deploy.yml")


def findings(*lines: str) -> list[str]:
    return [violation.message for violation in check_action_versions(WORKFLOW, list(lines))]


# ---- positives ----------------------------------------------------------------------------


def test_catches_an_action_below_the_floor() -> None:
    assert findings("      - uses: actions/checkout@v4")


def test_catches_every_node20_action_the_estate_actually_pins() -> None:
    """The exact set found across the repos on 2026-08-10."""
    stale = (
        "actions/checkout@v4",
        "actions/setup-python@v5",
        "actions/setup-node@v4",
        "actions/setup-dotnet@v4",
        "pnpm/action-setup@v4",
    )
    for pin in stale:
        assert findings(f"      - uses: {pin}"), f"{pin} should have been flagged"


def test_a_patch_pin_below_the_floor_is_still_caught() -> None:
    assert findings("      - uses: actions/checkout@v4.2.2")


def test_the_message_names_the_floor() -> None:
    message = findings("      - uses: actions/setup-node@v4")[0]
    assert "v7" in message


def test_it_reports_the_right_line() -> None:
    violations = list(
        check_action_versions(
            WORKFLOW,
            [
                "jobs:",
                "  check:",
                "      - uses: actions/checkout@v4",
            ],
        )
    )
    assert [violation.line for violation in violations] == [3]


# ---- negatives ----------------------------------------------------------------------------


def test_quiet_at_the_floor() -> None:
    assert not findings("      - uses: actions/checkout@v5")


def test_quiet_above_the_floor() -> None:
    assert not findings("      - uses: actions/checkout@v7")


def test_quiet_on_a_commit_sha_pin() -> None:
    """A SHA is a STRICTER pin than a tag and is what security guidance recommends."""
    sha = "a" * 40
    assert not findings(f"      - uses: actions/checkout@{sha}")


def test_quiet_on_an_action_with_no_stated_floor() -> None:
    """wordpress-docker-template pins several of these; none are our business."""
    assert not findings("      - uses: hadolint/hadolint-action@v3.3.0")


def test_quiet_on_a_nested_action_path_with_no_floor() -> None:
    assert not findings("      - uses: github/codeql-action/analyze@v4")


def test_quiet_on_a_branch_ref_it_cannot_compare() -> None:
    assert not findings("      - uses: actions/checkout@main")


def test_quiet_on_prose_mentioning_an_action() -> None:
    assert not findings("      # bumped actions/checkout@v4 to v7 in this commit")


def test_a_line_exemption_silences_it() -> None:
    assert not findings(
        "      # standards: action-version exempt -- pinned to v4 until the self-hosted "
        "runner image ships a node24-capable environment; tracked in PUBLISH_NOTES.",
        "      - uses: actions/checkout@v4",
    )


# ---- the version parser --------------------------------------------------------------------


def test_action_major_reads_the_common_pin_shapes() -> None:
    assert action_major("v7") == 7
    assert action_major("v1.2.5") == 1
    assert action_major("4") == 4


def test_action_major_declines_what_it_cannot_compare() -> None:
    assert action_major("a" * 40) is None
    assert action_major("main") is None


# ---- the dispatcher ------------------------------------------------------------------------
#
# Through check_source_file, not check_action_versions: workflows hit an early return that
# previously ran only the deploy-gate rule, and a rule that is correct but unreachable is the
# bug this pack has already shipped once.


def test_the_dispatcher_reaches_the_rule_for_a_workflow() -> None:
    rules = [
        violation.rule
        for violation in check_source_file(
            WORKFLOW, ["      - uses: actions/checkout@v4"], CheckConfig(), {}, Path(".")
        )
    ]
    assert "action-version" in rules


def test_a_yaml_file_outside_workflows_is_not_judged() -> None:
    """`uses:` is not a GitHub-Actions-only word; only workflow paths are in scope."""
    rules = [
        violation.rule
        for violation in check_source_file(
            Path("docker-compose.yml"),
            ["      - uses: actions/checkout@v4"],
            CheckConfig(),
            {},
            Path("."),
        )
    ]
    assert "action-version" not in rules


def test_every_floor_is_a_positive_integer() -> None:
    """A floor of 0 or a string would silently disable the rule for that action."""
    for name, floor in ACTION_FLOORS.items():
        assert isinstance(floor, int) and floor > 0, name


# ---- tier 2: ci-toolchain vs the repo's own global.json ------------------------------------

# No pytest fixtures below. `tmp_path` would make these cases unrunnable by
# standards_selftest, which calls each test with no arguments -- and the whole point of that
# runner is that the cases execute on a machine with no pytest. A test that only runs under
# one runner is half a test.


@contextmanager
def temporary_repo(global_json: str | None) -> Iterator[Path]:
    """A throwaway repo root, optionally carrying a global.json."""
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory)
        if global_json is not None:
            (repo / "global.json").write_text(global_json, encoding="utf-8")
        yield repo


def pinned(sdk: str) -> str:
    return json.dumps({"sdk": {"version": sdk, "rollForward": "latestFeature"}})


def toolchain(repo: Path, *lines: str) -> list[str]:
    return [v.message for v in check_ci_toolchain(WORKFLOW, list(lines), repo)]


def test_catches_an_sdk_below_the_global_json_pin() -> None:
    """WAPPIT exactly: installs 8.0.x while global.json demands 9.0.313."""
    with temporary_repo(pinned("9.0.313")) as repo:
        assert toolchain(repo, "          dotnet-version: 8.0.x")


def test_the_message_names_both_versions() -> None:
    with temporary_repo(pinned("9.0.313")) as repo:
        message = toolchain(repo, "          dotnet-version: 8.0.x")[0]
        assert "9.0.313" in message and "8.0.x" in message


def test_quiet_when_the_sdk_matches() -> None:
    with temporary_repo(pinned("9.0.313")) as repo:
        assert not toolchain(repo, "          dotnet-version: 9.0.x")


def test_quiet_when_the_sdk_is_newer() -> None:
    with temporary_repo(pinned("9.0.313")) as repo:
        assert not toolchain(repo, "          dotnet-version: 10.0.x")


def test_a_block_scalar_listing_both_satisfies_it() -> None:
    """The recommended fix -- keep the old SDK AND satisfy the pin. Must not be flagged."""
    with temporary_repo(pinned("9.0.313")) as repo:
        assert not toolchain(
            repo,
            "          dotnet-version: |",
            "            8.0.x",
            "            9.0.x",
        )


def test_a_block_scalar_listing_only_old_sdks_is_flagged() -> None:
    with temporary_repo(pinned("9.0.313")) as repo:
        assert toolchain(
            repo,
            "          dotnet-version: |",
            "            7.0.x",
            "            8.0.x",
        )


def test_quiet_with_no_global_json() -> None:
    """Most repos have no SDK pin at all; the rule must be silent for them."""
    with temporary_repo(None) as repo:
        assert not toolchain(repo, "          dotnet-version: 8.0.x")


def test_quiet_on_a_malformed_global_json() -> None:
    with temporary_repo("{ not json") as repo:
        assert not toolchain(repo, "          dotnet-version: 8.0.x")


def test_quiet_on_a_global_json_with_no_sdk_version() -> None:
    with temporary_repo('{"msbuild-sdks": {}}') as repo:
        assert not toolchain(repo, "          dotnet-version: 8.0.x")


def test_a_global_json_with_comment_keys_still_parses() -> None:
    """WAPPIT's global.json carries a `//` key holding its rationale."""
    document = json.dumps({"//": ["why this is pinned"], "sdk": {"version": "9.0.313"}})
    with temporary_repo(document) as repo:
        assert toolchain(repo, "          dotnet-version: 8.0.x")


def test_quiet_on_a_workflow_that_installs_no_dotnet() -> None:
    with temporary_repo(pinned("9.0.313")) as repo:
        assert not toolchain(repo, "      - uses: actions/checkout@v7")


def test_a_line_exemption_silences_the_toolchain_rule() -> None:
    with temporary_repo(pinned("9.0.313")) as repo:
        assert not toolchain(
            repo,
            "          # standards: ci-toolchain exempt -- this job only runs a linter that "
            "never loads global.json, so the pin does not apply to it.",
            "          dotnet-version: 8.0.x",
        )


def test_the_dispatcher_reaches_the_toolchain_rule() -> None:
    """The workflow path must sit INSIDE the repo root.

    `is_workflow` asks for the path relative to the root, so a relative path against an
    absolute root raises rather than returning False -- which is how this test failed the
    first time it ran, and is worth keeping in mind for any rule that takes repo_root.
    """
    with temporary_repo(pinned("9.0.313")) as repo:
        workflow = repo / ".github" / "workflows" / "deploy.yml"
        rules = [
            v.rule for v in check_source_file(workflow, ["          dotnet-version: 8.0.x"], CheckConfig(), {}, repo)
        ]
        assert "ci-toolchain" in rules


# ---- tier 4: the runtime floor -------------------------------------------------------------
#
# The negatives matter more than the positives again. This rule reads every project file in
# the estate, and the two shapes it must never touch -- netstandard libraries and .NET
# Framework projects -- are both spelled with a leading "net" and a digit.

PROJECT = Path("App/App.csproj")


def runtime(*lines: str) -> list[str]:
    return [v.message for v in check_dotnet_runtime_support(PROJECT, list(lines))]


def test_catches_a_project_below_the_floor() -> None:
    assert runtime("    <TargetFramework>net9.0</TargetFramework>")


def test_the_message_names_the_end_of_support_date() -> None:
    assert "2026-11-10" in runtime("    <TargetFramework>net9.0</TargetFramework>")[0]


def test_a_platform_suffixed_target_is_still_judged() -> None:
    assert runtime("    <TargetFramework>net8.0-windows</TargetFramework>")


def test_multi_target_is_judged_on_its_oldest_dotnet() -> None:
    """Shipping net8.0 alongside net10.0 still ships a net8.0 asset."""
    assert runtime("    <TargetFrameworks>net8.0;net10.0</TargetFrameworks>")


def test_the_floor_itself_passes() -> None:
    assert not runtime("    <TargetFramework>net10.0</TargetFramework>")


def test_a_future_major_passes() -> None:
    assert not runtime("    <TargetFramework>net11.0</TargetFramework>")


def test_netstandard_is_not_a_runtime_choice_and_is_ignored() -> None:
    assert not runtime("    <TargetFramework>netstandard2.0</TargetFramework>")


def test_dotnet_framework_is_ignored() -> None:
    assert not runtime("    <TargetFramework>net48</TargetFramework>")


def test_netstandard_beside_a_current_runtime_is_judged_only_on_the_runtime() -> None:
    assert not runtime("    <TargetFrameworks>netstandard2.0;net10.0</TargetFrameworks>")


def test_a_line_exemption_silences_the_runtime_rule() -> None:
    assert not runtime(
        "    <!-- standards: runtime-support exempt -- the vendor SDK this project wraps "
        "ships no net10.0 build; revisit when it does. -->",
        "    <TargetFramework>net9.0</TargetFramework>",
    )


def test_the_dispatcher_reaches_the_runtime_rule() -> None:
    with temporary_repo(None) as repo:
        project = repo / "App" / "App.csproj"
        rules = [
            v.rule
            for v in check_source_file(
                project,
                ["    <TargetFramework>net9.0</TargetFramework>"],
                CheckConfig(),
                {},
                repo,
            )
        ]
        assert "runtime-support" in rules


def test_a_project_file_is_in_scope_at_all() -> None:
    """should_check gates every rule above it; a .csproj outside scope is never read."""
    with temporary_repo(None) as repo:
        assert should_check(repo / "App" / "App.csproj", repo, CheckConfig())


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "version-rule cases"))

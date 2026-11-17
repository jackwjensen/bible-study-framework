"""Cases for runner-image, and for the runner labels the dependency rules read.

The reader is shared by three questions -- pinned, supported, the server's -- so its cases live
here beside the one rule that is only about runners. Driven through the real entry point
(`should_check` then `check_source_file`) as well as directly, because a correct rule the
dispatcher never reaches is the bug this pack has shipped before.

Run: python test_standards_runner.py   (or pytest)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import CheckConfig  # noqa: E402
from standards_deps_declared import RUNNER, runner_labels  # noqa: E402
from standards_deps_pinning import UNPINNED_RULE  # noqa: E402
from standards_deps_registry import lookup  # noqa: E402
from standards_dispatch import check_source_file  # noqa: E402
from standards_runner import RUNNER_RULE, check_runner_image  # noqa: E402
from standards_scope import should_check  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402
from standards_versions import SERVER_UBUNTU  # noqa: E402

WORKFLOW = Path(".github/workflows/ci.yml")


def labels(text: str) -> list[tuple[str, str, int]]:
    return [(found.name, found.version, found.line) for found in runner_labels(WORKFLOW, text)]


def rule_findings(*lines: str) -> list[int]:
    return [violation.line for violation in check_runner_image(WORKFLOW, list(lines))]


def dispatched_rules(body: str) -> list[str]:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        target = root / WORKFLOW
        target.parent.mkdir(parents=True)
        target.write_text(body, encoding="utf-8")
        assert should_check(target, root, CheckConfig())
        return [violation.rule for violation in check_source_file(target, body.splitlines(), CheckConfig(), {}, root)]


# ---- the reader ----------------------------------------------------------------------------


def test_every_hosted_label_shape_is_read() -> None:
    text = (
        "jobs:\n"
        "  a:\n    runs-on: ubuntu-26.04\n"
        "  b:\n    runs-on: ubuntu-26.04-arm\n"
        "  c:\n    runs-on: windows-2025\n"
        "  d:\n    runs-on: macos-15\n"
        "  e:\n    runs-on: ubuntu-latest\n"
        "  f:\n    runs-on: ubuntu-slim\n"
    )
    assert labels(text) == [
        ("ubuntu", "26.04", 3),
        ("ubuntu", "26.04", 5),
        ("windows", "2025", 7),
        ("macos", "15", 9),
        ("ubuntu", "latest", 11),
        ("ubuntu", "slim", 13),
    ]


def test_a_matrix_label_is_read() -> None:
    """A multi-OS job declares its runners in the matrix, and a label floats there just the same."""
    assert labels("    os: [ubuntu-latest, windows-latest]\n    runs-on: ${{ matrix.os }}\n") == [
        ("ubuntu", "latest", 1),
        ("windows", "latest", 1),
    ]


def test_comments_and_lookalikes_are_not_labels() -> None:
    text = (
        "    # moved off ubuntu-latest in this commit\n"
        "    runs-on: [self-hosted, linux]\n"
        "    image: my-ubuntu-latest-base\n"
        "    path: ubuntu-26.04.1.iso\n"
    )
    assert labels(text) == []


def test_only_numbered_labels_are_pins() -> None:
    pins = {
        found.version: found.is_pinned for found in runner_labels(WORKFLOW, "ubuntu-26.04 ubuntu-latest ubuntu-slim")
    }
    assert pins == {"26.04": True, "latest": False, "slim": False}


# ---- dependency-unpinned refuses the floating labels -----------------------------------------


def test_a_floating_label_is_unpinned_at_commit() -> None:
    rules = dispatched_rules("jobs:\n  check:\n    runs-on: ubuntu-latest\n")
    assert UNPINNED_RULE in rules
    assert RUNNER_RULE not in rules  # one finding, not the same one in two voices


def test_the_server_label_is_clean_through_the_dispatcher() -> None:
    rules = dispatched_rules(f"jobs:\n  check:\n    runs-on: ubuntu-{SERVER_UBUNTU}\n")
    assert UNPINNED_RULE not in rules
    assert RUNNER_RULE not in rules


# ---- runner-image --------------------------------------------------------------------------


def test_another_ubuntu_release_is_flagged() -> None:
    assert rule_findings("jobs:", "  check:", "    runs-on: ubuntu-24.04") == [3]
    assert rule_findings("    runs-on: ubuntu-22.04-arm") == [1]


def test_the_dispatcher_reaches_the_rule() -> None:
    assert RUNNER_RULE in dispatched_rules("jobs:\n  check:\n    runs-on: ubuntu-24.04\n")


def test_the_server_release_and_its_arm_variant_are_quiet() -> None:
    assert rule_findings(f"    runs-on: ubuntu-{SERVER_UBUNTU}", f"    runs-on: ubuntu-{SERVER_UBUNTU}-arm") == []


def test_other_families_are_not_judged() -> None:
    """The server is Ubuntu; a Windows job builds a desktop release that never runs there."""
    assert rule_findings("    runs-on: windows-2025", "    runs-on: macos-15") == []


def test_a_stated_exemption_is_honoured() -> None:
    assert (
        rule_findings(
            "    # standards: runner-image exempt -- deploys to the customer's own host, which runs Ubuntu 24.04",
            "    runs-on: ubuntu-24.04",
        )
        == []
    )


def test_the_pack_pins_the_server_release_as_a_real_label() -> None:
    """SERVER_UBUNTU is written into every consumer's check job; a typo here is a job that never starts."""
    assert labels(f"runs-on: ubuntu-{SERVER_UBUNTU}") == [("ubuntu", SERVER_UBUNTU, 1)]


# ---- the gate's lookup ---------------------------------------------------------------------


def _cycles(*entries: tuple[str, bool]) -> bytes:
    releases = ",".join(
        f'{{"name": "{name}", "isLts": {str(lts).lower()}, "releaseDate": "2026-04-23"}}' for name, lts in entries
    )
    return f'{{"result": {{"releases": [{releases}]}}}}'.encode()


def test_the_gate_proposes_only_lts_releases_where_the_os_has_them() -> None:
    """GitHub builds no image for an interim Ubuntu; proposing `ubuntu-26.10` names nothing."""
    payload = _cycles(("26.10", False), ("26.04", True), ("24.04", True))
    dependency = next(iter(runner_labels(WORKFLOW, "runs-on: ubuntu-24.04")))
    answer = lookup(dependency, lambda url, headers: payload, use_cache=False)
    assert answer.error is None
    assert [release[0] for release in answer.releases] == ["26.04", "24.04"]
    assert answer.newest[0] == "26.04"


def test_windows_semi_annual_channels_are_not_releases() -> None:
    payload = _cycles(("2025", True), ("23h2-ac", False), ("2022", True))
    dependency = next(iter(runner_labels(WORKFLOW, "runs-on: windows-2022")))
    answer = lookup(dependency, lambda url, headers: payload, use_cache=False)
    assert dependency.ecosystem == RUNNER
    assert [release[0] for release in answer.releases] == ["2025", "2022"]


def test_macos_has_no_lts_so_every_numbered_release_counts() -> None:
    payload = _cycles(("26", False), ("15", False))
    dependency = next(iter(runner_labels(WORKFLOW, "runs-on: macos-15")))
    answer = lookup(dependency, lambda url, headers: payload, use_cache=False)
    assert answer.newest[0] == "26"


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "runner-image"))

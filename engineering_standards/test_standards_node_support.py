"""Cases for the Node floor and the Node half of the consistency engine.

Split out of test_standards_versions.py on 2026-08-25 with the rule itself. The cases that
matter here are the two the FLOOR cannot answer -- two pins that disagree while both clearing
24 -- because those were silent in every repo until `node-consistency` existed, while the
floor's own message claimed it made them impossible.

Run: python test_standards_node_support.py   (or pytest)
"""

from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import CheckConfig  # noqa: E402
from standards_dispatch import check_source_file  # noqa: E402
from standards_node_support import (  # noqa: E402
    NODE_END_OF_LIFE,
    NODE_MAINTENANCE_START,
    NODE_TOOLCHAIN,
    check_node_runtime_support,
    declarations_in,
    node_support_status,
)
from standards_scope import should_check  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402
from standards_toolchain_consistency import check_consistency  # noqa: E402
from standards_versions import NODE_FLOOR  # noqa: E402

# One major BELOW the floor: the canonical 'must be flagged' fixture, derived rather than
# hardcoded. Raising a floor is the whole point of having one, and with the majors written
# out every raise turned this file red for reasons that said nothing about the rule.
BELOW = NODE_FLOOR - 1


@contextmanager
def temporary_repo(_unused: object = None) -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tree:
        yield Path(tree)


@contextmanager
def repo_with(**files: str) -> Iterator[tuple[Path, list[Path]]]:
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        for relative, text in files.items():
            path = root / relative.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        paths = [path for path in sorted(root.rglob("*")) if path.is_file() and should_check(path, root, CheckConfig())]
        yield root, paths


def agree(**files: str) -> list[str]:
    with repo_with(**files) as (root, paths):
        return [v.message for v in check_consistency(root, paths, CheckConfig(), NODE_TOOLCHAIN)]


# ---- tier 4: the Node floor -----------------------------------------------------------------
#
# THE CASE THAT MATTERS is test_a_repo_pinning_its_own_node_is_still_judged. Node 24 was already
# the declared estate minimum and already in CI_DEFAULTS when four pins across two repos stayed
# on 22 and nothing reported them -- because tier 2 DEFERS to a repo's own pin. If a later
# refactor ever makes this rule defer the same way, that test is what notices.


def node(filename: str, *lines: str) -> list[str]:
    return [v.message for v in check_node_runtime_support(Path(filename), list(lines))]


def test_an_nvmrc_below_the_floor_is_flagged() -> None:
    assert node(".nvmrc", f"{BELOW}")


def test_an_nvmrc_at_the_floor_is_clean() -> None:
    assert not node(".nvmrc", "24")


def test_an_nvmrc_reads_the_shapes_people_actually_write() -> None:
    """`v20`, `20.19.5` and a bare major are all legal .nvmrc content."""
    assert node(".nvmrc", f"v{BELOW}")
    assert node(".nvmrc", f"{BELOW}.19.5")
    assert not node(".nvmrc", "24.19.0")


def test_a_docker_base_image_below_the_floor_is_flagged() -> None:
    assert node("Dockerfile", f"FROM node:{BELOW}-alpine AS build")


def test_a_non_node_base_image_is_not_judged() -> None:
    assert not node("Dockerfile", "FROM python:3.12-slim")


def test_a_ci_node_version_below_the_floor_is_flagged() -> None:
    assert node(".github/workflows/deploy.yml", f"          node-version: '{BELOW}'")


def test_a_quoted_patch_version_is_read_by_its_major() -> None:
    assert node(".github/workflows/deploy.yml", f'          node-version: "{BELOW}.11.1"')


def test_an_action_pin_is_not_a_node_version() -> None:
    """setup-node@v7 is an ACTION major; reading it as Node 7 would flag every repo."""
    assert not node(".github/workflows/deploy.yml", "        uses: actions/setup-node@v7")


def test_a_repo_pinning_its_own_node_is_still_judged() -> None:
    """THE point of the tier. ci-toolchain asks whether CI satisfies the repo's own pin, so a
    repo pinning 22 in BOTH places is self-consistent and passes it. The floor asks a different
    question, and this is the shape that slipped through when only CI_DEFAULTS was bumped."""
    assert node(".nvmrc", f"{BELOW}")
    assert node(".github/workflows/deploy.yml", f"          node-version: '{BELOW}'")


def test_a_line_exemption_silences_the_node_floor() -> None:
    assert not node(
        ".nvmrc",
        "# standards: node-support exempt -- the vendor SDK publishes no Node 24 build; "
        "revisit when it does, tracked in PUBLISH_NOTES.md.",
        f"{BELOW}",
    )


def test_the_node_message_names_every_pin_to_move() -> None:
    """A repo that moves only its .nvmrc develops on 24 and builds on 22. The message has to
    say that, because the scanner reports one line at a time and the fix is repo-wide."""
    message = node(".nvmrc", f"{BELOW}")[0]
    assert ".nvmrc" in message and "node-version:" in message and "FROM node:" in message


def test_the_node_floor_is_a_positive_integer() -> None:
    assert isinstance(NODE_FLOOR, int) and NODE_FLOOR > 0


def test_a_node_major_ahead_of_the_floor_is_never_a_finding() -> None:
    """A floor is a MINIMUM. Node majors do NOT track .NET, so this floor is its own number --
    but it is still a minimum, and a repo that has moved ahead must not be punished for it."""
    assert not node(".nvmrc", str(NODE_FLOOR + 2))


def test_the_dispatcher_reaches_the_node_rule_for_an_nvmrc() -> None:
    """Scope is the other half: the rule is inert if .nvmrc never reaches check_source_file."""
    with temporary_repo(None) as repo:
        nvmrc = repo / ".nvmrc"
        assert should_check(nvmrc, repo, CheckConfig()), ".nvmrc must be in scope at all"
        rules = [v.rule for v in check_source_file(nvmrc, [f"{BELOW}"], CheckConfig(), {}, repo)]
        assert "node-support" in rules


# ---- what the floor could never answer -------------------------------------------------------


def test_two_pins_that_disagree_above_the_floor_are_flagged() -> None:
    """THE GAP the audit found. Both clear 24, so node-support is silent on every line -- and
    the repo develops on one major and ships on another. Verified against the real scanner on
    2026-08-25: this exact shape exited 0."""
    assert agree(**{".nvmrc": "24\n", "Dockerfile": "FROM node:26-alpine\n"})


def test_ci_disagreeing_with_the_pin_above_the_floor_is_flagged() -> None:
    assert agree(**{".nvmrc": "24\n", ".github__workflows__ci.yml": "          node-version: '26'\n"})


def test_three_sites_agreeing_is_clean() -> None:
    assert not agree(
        **{
            ".nvmrc": "24\n",
            "Dockerfile": "FROM node:24-alpine\n",
            ".github__workflows__ci.yml": "          node-version: '24'\n",
        }
    )


def test_a_patch_level_pin_is_not_a_disagreement() -> None:
    """Node breaks on majors; `.nvmrc` 24.3.0 beside CI 24 is one runtime."""
    assert not agree(**{".nvmrc": "24.3.0\n", ".github__workflows__ci.yml": "          node-version: '24'\n"})


def test_a_registry_prefixed_node_image_is_still_judged() -> None:
    """The same circumvention the Python reader was nearly shipped with."""
    assert agree(**{".nvmrc": "24\n", "Dockerfile": "FROM ${CACHE}node:26-alpine\n"})


def test_a_version_hoisted_into_a_build_argument_is_still_judged() -> None:
    assert agree(**{".nvmrc": "24\n", "Dockerfile": "ARG NODE=26\nFROM node:${NODE}-alpine\n"})


def test_the_floor_message_no_longer_claims_to_prevent_a_split() -> None:
    """The old wording said a split pin was "the failure this floor exists to make impossible".
    It was not: the floor only makes being BELOW it impossible. The sentence now points at the
    rule that actually enforces it, and this test is what stops the overclaim coming back."""
    message = [v.message for v in check_node_runtime_support(Path(".nvmrc"), [f"{BELOW}"])][0]
    assert "node-consistency" in message
    assert "make impossible" not in message


def test_an_alias_tag_is_a_floor_finding_not_a_consistency_one() -> None:
    """`node:latest` names nothing comparable. Reported once, by the floor."""
    assert [v.message for v in check_node_runtime_support(Path("Dockerfile"), ["FROM node:latest"])]
    assert not agree(**{".nvmrc": "24\n", "Dockerfile": "FROM node:latest\n"})


def test_the_reader_is_the_one_the_floor_uses() -> None:
    """Two readers would let the floor and the consistency rule disagree about what a file
    says, and the second would then pass a repo the first failed."""
    assert declarations_in.__module__ == NODE_TOOLCHAIN.readers.__module__
    assert declarations_in is NODE_TOOLCHAIN.readers


# ---- the support standing comes from a table, never from a sentence --------------------------


def test_the_floor_has_dates_in_the_table() -> None:
    """The finding describes the floor from NODE_MAINTENANCE_START / NODE_END_OF_LIFE. A floor
    with no entry would be described by an empty clause, which reads as the pack having
    nothing to say about its own floor. Only the floor is required here: the one-below fixture
    is an odd major (23), and odd majors are Current-only releases that never enter LTS, so the
    table has nothing to say about them -- which is correct, and node_support_status returns
    "" rather than inventing a date."""
    assert NODE_FLOOR in NODE_END_OF_LIFE and NODE_FLOOR in NODE_MAINTENANCE_START


def test_the_message_derives_both_standings_from_the_table() -> None:
    """The old wording -- "24 is the active LTS; 22 is in maintenance and 20 is end of life" --
    was prose written on one day and never re-read. Whatever the floor and whatever the day,
    the message must say what the table says of both majors on that day."""
    today = "2026-09-05"
    message = [v.message for v in check_node_runtime_support(Path(".nvmrc"), [f"{BELOW}"], today)][0]
    assert node_support_status(BELOW, today) in message
    assert node_support_status(NODE_FLOOR, today) in message
    assert "active LTS;" not in message


def test_the_node_status_uses_the_right_tense_for_the_day() -> None:
    """All three tenses, pinned with a fixed clock, so the sentence cannot go stale on the day
    a major changes state -- the exact way the old prose went stale."""
    assert "is in active support until 2027-04-30" in node_support_status(22, "2025-06-01")
    assert "is in maintenance until 2027-04-30" in node_support_status(22, "2026-09-05")
    assert "reached end of life on 2027-04-30" in node_support_status(22, "2027-05-01")
    assert node_support_status(99, "2026-09-05") == "", "an unknown major yields no claim at all"


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "node-support cases"))

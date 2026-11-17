"""Cases for the gate's "valid, active upgrade" rule: standards_deps_pnpm and standards_deps_installable.

The rule (Jack, 2026-11-11): a push is blocked only on a release the repository can install
today. These cases pin pnpm's release-age guard as the repo configures it, the releases it holds
back, the withdrawn releases that are never proposed, and the WAITING notice that replaces the
block until the release is installable. The override cases pin the site that let graphql 16.14.2
stay installed while package.json and the record said 16.15.0.

Run: python test_standards_deps_installable.py   (or pytest)
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_deps_declared import NPM, PYPI, Dependency, declared_dependencies  # noqa: E402
from standards_deps_installable import installable  # noqa: E402
from standards_deps_pnpm import (  # noqa: E402
    ReleaseAgeGuard,
    override_target,
    release_age_guard,
    workspace_overrides,
)
from standards_deps_registry import Answer  # noqa: E402
from standards_deps_verdict import CURRENT, NEWER, WAITING, judge, with_waiting  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

NOW = datetime(2026, 11, 11, 15, 30, tzinfo=UTC)
VITE = Dependency(NPM, "vite", "8.3.7", Path("package.json"), 9)
GUARD = ReleaseAgeGuard(1440, (), "pnpm minimumReleaseAge 1440 min (pnpm 11's default)")
VITE_RELEASES = Answer(
    releases=(("8.3.7", "2026-11-05", None), ("8.3.8", "2026-11-11", None)),
    published_at=(("8.3.7", "2026-11-05T10:00:00.000Z"), ("8.3.8", "2026-11-11T02:49:46.387Z")),
)


def repo(files: dict[str, str]) -> tempfile.TemporaryDirectory:
    tree = tempfile.TemporaryDirectory()
    for name, text in files.items():
        (Path(tree.name) / name).write_text(text, encoding="utf-8")
    return tree


def verdict_at(now: datetime, answer: Answer = VITE_RELEASES, guard: ReleaseAgeGuard | None = GUARD):
    kept, waiting = installable(VITE, answer, guard, now)
    return with_waiting(judge(VITE, kept, None, {}, now.date()), waiting)


# --- the release-age guard -------------------------------------------------------------------


def test_a_release_inside_the_guard_waits_and_does_not_block() -> None:
    verdict = verdict_at(NOW)
    assert verdict.state == WAITING and not verdict.blocks
    assert "8.3.8" in verdict.message and "2026-11-12 02:49 UTC" in verdict.message, "says when it can be taken"


def test_the_same_release_blocks_once_it_is_installable() -> None:
    verdict = verdict_at(datetime(2026, 11, 12, 2, 50, tzinfo=UTC))
    assert verdict.state == NEWER and verdict.blocks


def test_an_excluded_package_is_judged_at_once_because_pnpm_lets_it_through() -> None:
    for entry in ("vite", "vi*", "vite@8.3.8", "vite@8.3.6 || 8.3.8"):
        guard = ReleaseAgeGuard(1440, (entry,), "test")
        assert verdict_at(NOW, guard=guard).state == NEWER, entry
    assert verdict_at(NOW, guard=ReleaseAgeGuard(1440, ("vite@8.3.6",), "test")).state == WAITING


def test_without_a_guard_or_with_a_zero_one_nothing_waits() -> None:
    assert verdict_at(NOW, guard=None).state == NEWER
    assert verdict_at(NOW, guard=ReleaseAgeGuard(0, (), "test")).state == NEWER


def test_a_release_with_no_publish_instant_is_judged_as_before() -> None:
    undated = Answer(releases=VITE_RELEASES.releases)
    assert verdict_at(NOW, answer=undated).state == NEWER, "errs toward looking at a release, never skipping it"


def test_the_guard_only_applies_to_the_ecosystem_it_belongs_to() -> None:
    ruff = Dependency(PYPI, "ruff", "==0.16.5", Path("requirements.txt"), 1)
    answer = Answer(
        releases=(("0.16.5", "2026-08-01", None), ("0.16.10", "2026-11-11", None)),
        published_at=(("0.16.10", "2026-11-11T12:00:00Z"),),
    )
    kept, waiting = installable(ruff, answer, GUARD, NOW)
    assert waiting == () and len(kept.releases) == 2


def test_a_deprecated_release_is_never_proposed_but_the_pin_is_kept() -> None:
    jest_dom = Dependency(NPM, "@testing-library/jest-dom", "6.9.1", Path("package.json"), 4)
    answer = Answer(releases=(("6.9.1", "2026-07-01", None), ("6.10.0", "2026-07-20", None)), withdrawn=("6.10.0",))
    kept, _ = installable(jest_dom, answer, None, NOW)
    assert judge(jest_dom, kept, None, {}, NOW.date()).state == CURRENT
    pinned_on_it = Dependency(NPM, "@testing-library/jest-dom", "6.10.0", Path("package.json"), 4)
    kept, _ = installable(pinned_on_it, answer, None, NOW)
    assert [release[0] for release in kept.releases] == ["6.9.1", "6.10.0"], "the pin is where the repo stands"


# --- reading pnpm's settings -----------------------------------------------------------------


def test_pnpm_11_guards_by_default_and_pnpm_10_does_not() -> None:
    with repo({"package.json": json.dumps({"packageManager": "pnpm@11.7.0"})}) as root:
        guard = release_age_guard(Path(root))
        assert guard is not None and guard.minutes == 1440 and "default" in guard.source
    with repo({"package.json": json.dumps({"packageManager": "pnpm@10.18.0"})}) as root:
        assert release_age_guard(Path(root)).minutes == 0


def test_the_workspace_file_sets_the_age_and_the_exclusions() -> None:
    workspace = (
        "# pnpm settings\n"
        "minimumReleaseAge: 2880  # two days\n"
        "minimumReleaseAgeExclude:\n"
        "  - '@myorg/*'\n"
        '  - "webpack@4.47.0 || 5.102.1"\n'
        "allowBuilds:\n"
        "  esbuild: true\n"
    )
    files = {"package.json": json.dumps({"packageManager": "pnpm@11.7.0"}), "pnpm-workspace.yaml": workspace}
    with repo(files) as root:
        guard = release_age_guard(Path(root))
    assert guard.minutes == 2880 and "pnpm-workspace.yaml" in guard.source
    assert guard.excluded == ("@myorg/*", "webpack@4.47.0 || 5.102.1")
    assert not guard.guards("@myorg/ui", "1.0.0") and not guard.guards("webpack", "5.102.1")
    assert guard.guards("webpack", "5.102.2") and guard.guards("@other/ui", "1.0.0")


def test_a_flow_list_of_exclusions_is_read() -> None:
    files = {"pnpm-workspace.yaml": "minimumReleaseAge: 60\nminimumReleaseAgeExclude: [vite, 'lucide-react']\n"}
    with repo(files) as root:
        assert release_age_guard(Path(root)).excluded == ("vite", "lucide-react")


def test_a_repo_without_pnpm_has_no_guard() -> None:
    with repo({"package.json": json.dumps({"name": "x"})}) as root:
        assert release_age_guard(Path(root)) is None


def test_an_unreadable_exclusion_list_is_said_not_skipped() -> None:
    with repo({"pnpm-workspace.yaml": "minimumReleaseAgeExclude:\n  vite: true\n"}) as root:
        try:
            release_age_guard(Path(root))
        except ValueError as error:
            assert "minimumReleaseAgeExclude" in str(error)
        else:
            raise AssertionError("a shape the reader does not know must not read as 'no exclusions'")


# --- overrides are declaration sites ---------------------------------------------------------


def test_override_selectors_name_the_package_they_force() -> None:
    assert override_target("graphql") == "graphql"
    assert override_target("@graphql-tools/schema") == "@graphql-tools/schema"
    assert override_target("foo@^1.0.0") == "foo"
    assert override_target("parent@2>@scope/child@<3") == "@scope/child"


def test_workspace_overrides_are_sites_and_references_are_not() -> None:
    text = (
        "overrides:\n"
        "  graphql: '16.14.2'\n"
        "  '@graphql-tools/schema': '^10.0.33'\n"
        "  removed-thing: '-'\n"
        "  follows-root: $graphql\n"
        "allowBuilds:\n"
        "  esbuild: true\n"
    )
    found = workspace_overrides(Path("pnpm-workspace.yaml"), text)
    assert [(o.name, o.version, o.line) for o in found] == [
        ("graphql", "16.14.2", 2),
        ("@graphql-tools/schema", "^10.0.33", 3),
    ]


def test_an_override_lagging_package_json_is_a_second_site_for_the_same_package() -> None:
    files = {
        "package.json": json.dumps({"dependencies": {"graphql": "16.15.0"}, "resolutions": {"lodash": "4.17.21"}}),
        "pnpm-workspace.yaml": "overrides:\n  graphql: '16.14.2'\n",
    }
    with repo(files) as root:
        found = {(d.path.as_posix(), d.name, d.version) for d in declared_dependencies(Path(root))}
    assert ("package.json", "graphql", "16.15.0") in found
    assert ("pnpm-workspace.yaml", "graphql", "16.14.2") in found, "the site the gate could not see"
    assert ("package.json", "lodash", "4.17.21") in found


def test_today_is_not_frozen_by_the_fixture() -> None:
    assert NOW.date() == date(2026, 11, 11)


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "standards_deps_pnpm.py / standards_deps_installable.py"))

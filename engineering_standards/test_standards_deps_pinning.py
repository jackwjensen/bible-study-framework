"""Cases for dependency-unpinned: every declared dependency names exactly one release.

Driven through the REAL entry point (`should_check` then `check_source_file`) for every manifest
type, because the failure this rule is most exposed to is not a wrong verdict but never being
asked: `global.json`, `dotnet-tools.json`, the `.props` files and a bare `compose.yml` were in
scope for no rule at all until this rule added them, and a rule that never sees a file reports
it clean.

Run: python test_standards_deps_pinning.py   (or pytest)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import CheckConfig  # noqa: E402
from standards_deps_pinning import UNPINNED_RULE  # noqa: E402
from standards_dispatch import check_source_file  # noqa: E402
from standards_registry import NEVER_BASELINED  # noqa: E402
from standards_scope import should_check  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"


def unpinned_lines(relative: str, body: str) -> list[int]:
    """Lines the real entry point reports as dependency-unpinned, after confirming scope."""
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        config = CheckConfig()
        assert should_check(target, root, config), f"{relative} is not in scope, so the rule never sees it"
        found = check_source_file(target, body.splitlines(), config, {}, root)
        return [violation.line for violation in found if violation.rule == UNPINNED_RULE]


def test_a_floating_version_is_found_in_every_manifest_kind() -> None:
    cases = {
        "requirements.txt": "ruff>=0.16\n",
        "pyproject.toml": '[project]\nname = "x"\ndependencies = ["httpx~=0.28"]\n',
        "package.json": '{"dependencies": {"react": "^19.1.0"}}',
        "composer.json": '{"require": {"monolog/monolog": "^3.9"}}',
        "App.csproj": '<Project><ItemGroup><PackageReference Include="X" Version="1.*" /></ItemGroup></Project>',
        "Directory.Packages.props": '<Project><ItemGroup><PackageVersion Include="X" Version="1.*" /></ItemGroup></Project>',
        "Dockerfile": "FROM mysql:8.4\n",
        "docker-compose.yml": "services:\n  db:\n    image: mysql:8.4\n",
        "compose.yml": "services:\n  db:\n    image: redis:7\n",
        ".github/workflows/ci.yml": "steps:\n  - uses: actions/checkout@v7\n",
        ".config/dotnet-tools.json": '{"tools": {"dotnet-ef": {"version": "10.*"}}}',
    }
    for relative, body in cases.items():
        assert unpinned_lines(relative, body), f"{relative}: a floating version went unreported"


def test_exact_pins_are_quiet() -> None:
    cases = {
        "requirements.txt": "ruff==0.16.10\n",
        "Dockerfile": "FROM mcr.microsoft.com/dotnet/aspnet:10.0.12\n",
        "docker-compose.yml": "services:\n  db:\n    image: mysql:8.4.11\n",
        ".github/workflows/ci.yml": f"steps:\n  - uses: actions/checkout@{SHA}  # v7.0.1\n",
        "global.json": '{"sdk": {"version": "10.0.400", "rollForward": "latestPatch"}}',
        "App.csproj": '<Project><ItemGroup><PackageReference Include="X" Version="1.2.3" /></ItemGroup></Project>',
    }
    for relative, body in cases.items():
        assert unpinned_lines(relative, body) == [], f"{relative}: an exact pin was reported"


def test_a_two_part_vendor_s_release_is_a_pin_at_commit_too() -> None:
    """The commit rule and the push gate share `is_exact`, so `postgres:16.1-alpine` passes both
    -- and a two-part redis line still fails here, offline, before any registry is asked."""
    body = "services:\n  db:\n    image: postgres:16.1-alpine\n  cache:\n    image: redis:7.2-alpine\n"
    assert unpinned_lines("docker-compose.yml", body) == [5]


def test_the_finding_is_on_the_declaring_line() -> None:
    body = "services:\n  app:\n    build: .\n  db:\n    image: mysql:8.4\n"
    assert unpinned_lines("docker-compose.yml", body) == [5]


def test_a_stated_exemption_is_honoured() -> None:
    body = "# standards: dependency-unpinned exempt -- the vendor publishes only a floating tag for this image\nFROM vendor/thing:stable\n"
    assert unpinned_lines("Dockerfile", body) == []


def test_a_pack_copy_in_a_consumer_is_the_pack_s_to_judge() -> None:
    body = (
        "<!-- Source of truth: engineering-standards/dotnet/Directory.Build.props -->\n"
        '<Project><ItemGroup><PackageReference Include="X" Version="1.*" /></ItemGroup></Project>'
    )
    assert unpinned_lines("Directory.Build.props", body) == []


def test_it_can_never_be_baselined() -> None:
    """A floating version is not reproducible today; a baseline entry would only record that."""
    assert UNPINNED_RULE in NEVER_BASELINED


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "dependency-unpinned"))

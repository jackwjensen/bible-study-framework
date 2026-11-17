"""Cases for the package-wildcard rule.

THE CASE THAT MOTIVATED IT is `test_the_donorlink_shape_is_flagged`. InvoTrack carried
`Stripe.net Version="47.*"`, learned the lesson, and fixed it. DonorLink still carried `48.*`
on a live donation platform, and the sweep that checked the estate looked for bracket-syntax
pins rather than wildcards, so it reported clean. One repo learning a lesson is not the estate
learning it.

The negatives matter as much. An MSBuild property is resolved centrally and is the OPPOSITE of
floating; a caret range has a floor and a lockfile; a floating `@types/*` under devDependencies
cannot reach production. A rule that flagged those would be noise on three of the four shapes
it meets.

Run: python test_standards_packages.py   (or pytest)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import CheckConfig  # noqa: E402
from standards_dispatch import check_source_file  # noqa: E402
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402
from standards_packages import WILDCARD_RULE, check_package_wildcards  # noqa: E402
from standards_scope import should_check  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

EXEMPT = "the vendor ships only a floating tag and we mirror it deliberately; see PUBLISH_NOTES"
assert len(EXEMPT) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


def found(name: str, *lines: str) -> list[str]:
    return [v.message for v in check_package_wildcards(Path(name), list(lines))]


# ---- .csproj ---------------------------------------------------------------------------------


def test_the_donorlink_shape_is_flagged() -> None:
    """Verbatim from DonorLink.csproj on 2026-08-26, on a live donation platform."""
    assert found("App.csproj", '    <PackageReference Include="Stripe.net" Version="48.*" />')


def test_the_message_names_the_package_and_the_declaration() -> None:
    message = found("App.csproj", '    <PackageReference Include="Stripe.net" Version="48.*" />')[0]
    assert "Stripe.net" in message and "48.*" in message


def test_every_wildcard_spelling_is_flagged() -> None:
    for version in ("2.*", "17.*", "*", "1.2.*", "4.*-*"):
        assert found("App.csproj", f'<PackageReference Include="X" Version="{version}" />'), version


def test_the_estate_test_package_wildcards_are_flagged() -> None:
    """DiaTrack's test projects. Lower stakes than Stripe, same defect."""
    assert found("Tests.csproj", '    <PackageReference Include="xunit" Version="2.*" />')
    assert found("Tests.csproj", '    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.*" />')


def test_a_version_child_element_is_read_too() -> None:
    """MSBuild accepts both spellings, so a rule reading only the attribute is one refactor
    away from being silent."""
    assert found("App.csproj", "      <Version>3.*</Version>")


def test_a_plain_version_is_clean() -> None:
    """`52.3.0` is ALREADY the minimum the estate policy asks for -- NuGet resolves a direct
    reference to the lowest satisfying version, so it is a floor and deterministic at once."""
    assert not found("App.csproj", '<PackageReference Include="Stripe.net" Version="52.3.0" />')


def test_an_msbuild_property_is_not_a_wildcard() -> None:
    """`$(MauiVersion)` is resolved centrally from the SDK or Directory.Build.props -- one
    definition, one value, recorded in the repo. That is the opposite of floating."""
    assert not found("App.csproj", '<PackageReference Include="Microsoft.Maui.Controls" Version="$(MauiVersion)" />')


def test_an_explicit_range_is_not_a_wildcard() -> None:
    assert not found("App.csproj", '<PackageReference Include="X" Version="[1.0,2.0)" />')


def test_a_line_exemption_silences_it() -> None:
    assert not found(
        "App.csproj",
        f"    <!-- standards: {WILDCARD_RULE} exempt -- {EXEMPT} -->",
        '    <PackageReference Include="X" Version="1.*" />',
    )


# ---- package.json ----------------------------------------------------------------------------


def test_a_floating_runtime_dependency_is_flagged() -> None:
    """`jobbank-search/cli/package.json` verbatim in shape."""
    assert found(
        "package.json",
        '  "dependencies": {',
        '    "@bunli/core": "latest",',
        "  }",
    )


def test_every_floating_spelling_is_flagged() -> None:
    for version in ("*", "latest", "x"):
        assert found("package.json", '  "dependencies": {', f'    "pkg": "{version}"'), version


def test_a_floating_dev_dependency_is_not_flagged() -> None:
    """`"@types/bun": "latest"` is a normal idiom -- a type definition tracking the runtime it
    describes, which cannot reach production. Flagging it would be noise on the common case."""
    assert not found(
        "package.json",
        '  "devDependencies": {',
        '    "@types/bun": "latest",',
        '    "typescript": "^5.4.0"',
        "  }",
    )


def test_the_block_boundary_is_tracked_rather_than_assumed() -> None:
    """devDependencies after dependencies must turn the rule OFF again, or every repo with both
    blocks reports its dev types."""
    reported = found(
        "package.json",
        '  "dependencies": {',
        '    "left-pad": "latest"',
        "  },",
        '  "devDependencies": {',
        '    "@types/node": "*"',
        "  }",
    )
    assert len(reported) == 1, reported
    assert "left-pad" in reported[0]


def test_a_caret_range_is_clean() -> None:
    assert not found("package.json", '  "dependencies": {', '    "axios": "^1.7.0"')


# ---- composer.json ---------------------------------------------------------------------------


def test_composer_dev_master_is_flagged() -> None:
    """The exact shape an external audit found throughout a codebase's `require` block."""
    assert found("composer.json", '  "require": {', '    "alkurn/yii2-stripe": "dev-master"', "  }")


def test_every_composer_floating_spelling_is_flagged() -> None:
    for version in ("@dev", "*", "2.x-dev", "dev-main"):
        assert found("composer.json", '  "require": {', f'    "vendor/pkg": "{version}"'), version


def test_composer_ranges_and_exacts_are_clean() -> None:
    for version in ("^2.0", "~7.4", ">=1.2", "1.2.3"):
        assert not found("composer.json", '  "require": {', f'    "monolog/monolog": "{version}"'), version


def test_composer_roave_security_advisories_is_exempt_by_name() -> None:
    """Its only supported form IS `dev-latest`; flagging it teaches the rule is noise."""
    assert not found(
        "composer.json",
        '  "require-dev": {',
        '    "roave/security-advisories": "dev-latest"',
    )


def test_composer_require_dev_is_also_checked() -> None:
    assert found("composer.json", '  "require-dev": {', '    "phpunit/phpunit": "dev-master"')


# ---- requirements.txt ------------------------------------------------------------------------


def test_a_bare_requirement_is_flagged() -> None:
    assert found("requirements.txt", "Django")


def test_a_requirement_with_a_floor_is_clean() -> None:
    for line in ("Django>=4.2", "requests==2.31.0", "urllib3~=2.0", "flask<3", "numpy!=1.0"):
        assert not found("requirements.txt", line), line


def test_extras_without_a_version_is_still_flagged() -> None:
    assert found("requirements.txt", "celery[redis]")


def test_requirements_comments_options_markers_and_urls_are_ignored() -> None:
    for line in (
        "# a comment",
        "-r base.txt",
        "-e .",
        "mypkg @ git+https://github.com/x/y",
        "importlib; python_version < '3.8'",
        "",
    ):
        assert not found("requirements.txt", line), line


# ---- wiring ----------------------------------------------------------------------------------


def test_all_four_manifest_kinds_are_in_scope_and_dispatched() -> None:
    cases = {
        "App.csproj": ['<PackageReference Include="Stripe.net" Version="48.*" />'],
        "package.json": ['  "dependencies": { "x": "latest" }'],
        "composer.json": ['  "require": {', '    "vendor/pkg": "dev-master"', "  }"],
        "requirements.txt": ["Django"],
    }
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        for name, lines in cases.items():
            path = root / name
            assert should_check(path, root, CheckConfig()), f"{name} must be in scope"
            rules = [v.rule for v in check_source_file(path, lines, CheckConfig(), {}, root)]
            assert WILDCARD_RULE in rules, f"the rule never reached {name}"


def test_it_cannot_be_baselined() -> None:
    """A baseline records debt to pay down later. A wildcard is not debt -- it means the build
    is not reproducible TODAY, and writing that into a file does not make it reproducible."""
    import importlib.util  # noqa: PLC0415  (local by design: imported after this test builds its tree)

    spec = importlib.util.spec_from_file_location("driver", Path(__file__).resolve().parent / "check-source-limits.py")
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    assert WILDCARD_RULE in driver.NEVER_BASELINED


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "package-wildcard cases"))

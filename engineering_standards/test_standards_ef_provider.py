"""Cases for the ef-provider-support rule.

Split out of test_standards_versions.py on 2026-08-24. That file covers four separate rules
and crossed the 500-line limit when Pomelo became un-exemptable; the rules are independent,
so one file per rule is the seam, not an arbitrary cut to get under the number.

The load-bearing cases here are the two exemption tests. This rule USED to accept "we are
waiting for the provider" as a written decision, and no longer does for Pomelo specifically
-- so one test pins that the old marker is now inert, and its sibling pins that the mechanism
still works for a provider that is genuinely blocked. Withdrawing an escape hatch from one
entry is easy to over-apply and remove for everybody, which is what that pair guards.
"""

from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from standards_core import CheckConfig
from standards_dispatch import check_source_file
from standards_ef_provider import (
    EF_CORE_FLOOR,
    EF_PROVIDER_SUNSET,
    check_ef_core_support,
    check_ef_provider_support,
)
from standards_selftest import run_module_tests
from standards_versions import DOTNET_FLOOR

PROJECT = Path("App/App.csproj")


@contextmanager
def temporary_repo() -> Iterator[Path]:
    """A throwaway repo root. Local copy: the dispatcher test needs a real tree on disk."""
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


# ---- tier 4 again: the EF provider ceiling --------------------------------------------------
#
# The case that matters most is the LAST negative: a project fully on the current runtime must
# still be caught, because the whole reason this rule exists is that runtime-support cannot see
# a provider pinning the EF Core major. If a future refactor accidentally couples the two, that
# is the test that notices.


def provider(*lines: str) -> list[str]:
    """A hosted repo -- the default, and every repo in this estate today."""
    return [v.message for v in check_ef_provider_support(PROJECT, list(lines))]


def provider_dist(*lines: str) -> list[str]:
    """A repo that declares `"distributesBinaries": true` in .standards.json."""
    return [v.message for v in check_ef_provider_support(PROJECT, list(lines), distributes_binaries=True)]


POMELO = '    <PackageReference Include="Pomelo.EntityFrameworkCore.MySql" Version="9.0.0" />'


def test_catches_a_provider_with_no_successor_release() -> None:
    assert provider(POMELO)


def test_the_provider_message_names_the_end_of_support_date() -> None:
    assert "2026-11-10" in provider(POMELO)[0]


def test_the_message_names_the_replacement() -> None:
    assert "MySql.EntityFrameworkCore" in provider(POMELO)[0]


def test_the_message_says_the_swap_cannot_be_exempted() -> None:
    """Until 2026-08-24 this message warned the swap was "NOT a drop-in" and offered an
    exemption. InvoTrack then ran the swap and the unknowns came back clean, so the message
    now states the opposite: there is a proven route off Pomelo and no way to defer it."""
    assert "cannot be exempted" in provider(POMELO)[0]


def test_the_message_tells_you_how_to_do_the_swap() -> None:
    """A rule with no escape owes the reader the steps, or it is just an obstacle."""
    message = provider(POMELO)[0]
    assert "UseMySQL" in message
    assert "MySql.Data.MySqlClient.MySqlException" in message


def test_the_message_names_the_licence_change() -> None:
    """Pomelo is MIT and the replacement is not. Someone must meet that fact deliberately."""
    assert "GPL-2.0-only" in provider(POMELO)[0]


def test_the_id_is_matched_case_insensitively() -> None:
    """NuGet ids are case-insensitive, and every real csproj spells it in PascalCase."""
    assert provider('    <PackageReference Include="pomelo.entityframeworkcore.mysql" />')


def test_single_quoted_attributes_are_matched() -> None:
    assert provider("    <PackageReference Include='Pomelo.EntityFrameworkCore.MySql' />")


def test_an_older_version_of_the_same_provider_is_still_caught() -> None:
    """WAPPIT sits on 8.0.2. The ceiling is a property of the package, not of the pin."""
    assert provider('    <PackageReference Include="Pomelo.EntityFrameworkCore.MySql" Version="8.0.2" />')


def test_the_provider_rule_reports_the_right_line() -> None:
    violations = list(check_ef_provider_support(PROJECT, ["    <PropertyGroup>", "    </PropertyGroup>", POMELO]))
    assert [v.line for v in violations] == [3]


def test_quiet_on_a_provider_with_no_stated_ceiling() -> None:
    """The rule takes a position on named packages only, never on every PackageReference."""
    assert not provider('    <PackageReference Include="Npgsql.EntityFrameworkCore.PostgreSQL" Version="10.0.0" />')


def test_quiet_on_the_replacement_itself() -> None:
    """The obvious own-goal: flagging the package the message tells you to move to."""
    assert not provider('    <PackageReference Include="MySql.EntityFrameworkCore" Version="10.0.9" />')


def test_quiet_on_prose_merely_mentioning_the_package() -> None:
    assert not provider("    <!-- we are still on Pomelo.EntityFrameworkCore.MySql here -->")


def test_a_line_exemption_does_NOT_silence_pomelo() -> None:
    """The "no escape" half, and the reason this rule changed shape on 2026-08-24.

    This exact exemption used to be the sanctioned answer and is now inert. It is pinned as a
    test because the failure mode is silent: a repo carrying the old marker would otherwise
    keep passing, and nobody would learn that waiting had stopped being a decision.
    """
    assert provider(
        "    <!-- standards: ef-provider-support exempt : waiting on Pomelo to ship an EF Core 10 provider; revisit 2026-10-01 and swap to Oracle's if it has not. Colon, not a double hyphen: XML comments cannot contain one. -->",
        POMELO,
    )


LICENCE_REASON = (
    "    <!-- standards: ef-provider-support exempt : this repo ships a proprietary desktop "
    "binary that links the provider, and the replacement is GPL-2.0-only; migrating would "
    "create a licensing problem rather than solve a support one. Pomelo is MIT. -->"
)


def test_a_distributing_repo_may_exempt_pomelo() -> None:
    """The GPL carve-out. Oracle's provider is GPL-2.0-only WITH the Universal FOSS Exception
    and GPLv2 attaches to DISTRIBUTION, so for a conveyed binary "migrate" would be an
    instruction to create a licensing problem. MIT Pomelo is the right answer there."""
    assert not provider_dist(LICENCE_REASON, POMELO)


def test_a_distributing_repo_still_needs_a_written_reason() -> None:
    """The flag re-opens the door; it does not hold it open. Silence is still a finding --
    otherwise one config key would turn the whole rule off for the repo."""
    assert provider_dist(POMELO)


def test_a_hosted_repo_cannot_use_the_licence_reason() -> None:
    """The load-bearing negative. Without the repo-level flag the SAME exemption text is
    inert, so the carve-out cannot be pasted into a hosted app to dodge the migration."""
    assert provider(LICENCE_REASON, POMELO)


def test_a_line_exemption_still_works_for_a_genuinely_blocked_provider(monkeypatch) -> None:
    """The mechanism was withdrawn from Pomelo, not removed from the rule.

    A future provider may be blocked with no replacement shipped, and that case must still be
    exemptable. Tested through a stand-in entry, because Pomelo -- the only real entry -- is
    now precisely the one that cannot use it.
    """
    monkeypatch.setitem(EF_PROVIDER_SUNSET, "somevendor.entityframeworkcore.db", ("Other.Provider", 9, "2026-11-10"))
    blocked = '    <PackageReference Include="SomeVendor.EntityFrameworkCore.Db" Version="9.0.0" />'
    assert provider(blocked), "sanity: the stand-in should be flagged without an exemption"
    assert not provider(
        "    <!-- standards: ef-provider-support exempt : vendor has shipped nothing for EF Core 10 and there is no alternative provider for this database; revisit 2026-10-01. -->",
        blocked,
    )


def test_a_current_runtime_does_not_excuse_the_provider() -> None:
    """THE point of the rule. net10.0 is clean for runtime-support and irrelevant here."""
    assert provider("    <TargetFramework>net10.0</TargetFramework>", POMELO)


def test_the_dispatcher_reaches_the_provider_rule() -> None:
    with temporary_repo() as repo:
        project = repo / "App" / "App.csproj"
        rules = [v.rule for v in check_source_file(project, [POMELO], CheckConfig(), {}, repo)]
        assert "ef-provider-support" in rules


def test_every_sunset_entry_is_well_formed() -> None:
    """The id must be lowercase or the case-insensitive lookup silently never matches."""
    for package, (replacement, major, ends) in EF_PROVIDER_SUNSET.items():
        assert package == package.lower()
        assert replacement and replacement != package
        assert isinstance(major, int) and major > 0
        assert len(ends) == 10 and ends[4] == ends[7] == "-"


# ---- ef-core-support: the major itself -------------------------------------------------------
#
# THE CASE THAT MATTERS is test_a_current_tfm_does_not_excuse_stale_ef_packages. InvoTrack spent
# weeks on net10.0 with EF Core 9.0.11, passing runtime-support the whole time, and the only
# record of it was an exemption on a rule about the PROVIDER. If the provider had been healthy,
# nothing in the pack would have reported the gap at all.


def core(*lines: str) -> list[str]:
    return [v.message for v in check_ef_core_support(PROJECT, list(lines))]


EF9 = '    <PackageReference Include="Microsoft.EntityFrameworkCore" Version="9.0.11" />'
EF10 = '    <PackageReference Include="Microsoft.EntityFrameworkCore" Version="10.0.11" />'


def test_an_ef_package_below_the_floor_is_flagged() -> None:
    assert core(EF9)


def test_an_ef_package_at_the_floor_is_clean() -> None:
    assert not core(EF10)


def test_the_aspnetcore_companions_are_judged_too() -> None:
    """Identity and Diagnostics ship in lockstep with EF Core and are the ones most often
    forgotten, because neither name starts with Microsoft.EntityFrameworkCore."""
    assert core('    <PackageReference Include="Microsoft.AspNetCore.Identity.EntityFrameworkCore" Version="9.0.11" />')
    assert core(
        '    <PackageReference Include="Microsoft.AspNetCore.Diagnostics.EntityFrameworkCore" Version="8.0.10" />'
    )


def test_the_tooling_and_test_providers_are_judged_too() -> None:
    """A stale InMemory provider does not fail the build -- it throws MissingMethodException
    from every test that constructs a DbContext, which is a run-time surprise."""
    assert core('    <PackageReference Include="Microsoft.EntityFrameworkCore.Tools" Version="9.0.11" />')
    assert core('    <PackageReference Include="Microsoft.EntityFrameworkCore.InMemory" Version="9.0.11" />')


def test_a_provider_package_is_not_read_as_an_ef_version() -> None:
    """Providers version independently. Oracle's happens to track the EF major and Pomelo's
    does not, and guessing from a provider's number would be a confident wrong answer --
    a dead provider is ef-provider-support's finding, not this one's."""
    assert not core('    <PackageReference Include="MySql.EntityFrameworkCore" Version="10.0.9" />')
    assert not core('    <PackageReference Include="Pomelo.EntityFrameworkCore.MySql" Version="9.0.0" />')


def test_an_unrelated_package_is_not_judged() -> None:
    assert not core('    <PackageReference Include="MudBlazor" Version="9.0.0" />')


def test_a_reference_with_no_version_is_skipped() -> None:
    """Central Package Management puts the version in Directory.Packages.props; guessing
    would be worse than the silence."""
    assert not core('    <PackageReference Include="Microsoft.EntityFrameworkCore" />')


def test_a_current_tfm_does_not_excuse_stale_ef_packages() -> None:
    """THE point of the rule -- and the exact shape InvoTrack shipped for weeks."""
    assert core("    <TargetFramework>net10.0</TargetFramework>", EF9)


def test_a_line_exemption_silences_the_ef_core_floor() -> None:
    assert not core(
        "    <!-- standards: ef-core-support exempt : a dependency has no EF Core 10 build; "
        "revisit when it ships, tracked in PUBLISH_NOTES.md. -->",
        EF9,
    )


def test_the_message_names_the_packages_people_forget() -> None:
    message = core(EF9)[0]
    assert "Identity" in message and "dotnet-ef" in message


def test_the_ef_core_floor_is_a_positive_integer() -> None:
    assert isinstance(EF_CORE_FLOOR, int) and EF_CORE_FLOOR > 0


def test_ef_follows_dotnet_rather_than_carrying_its_own_number() -> None:
    """EF Core majors track .NET majors, so the two floors are ONE decision. Written as a
    second literal they would be two places to forget, and the estate would eventually sit on
    .NET 11 with the EF floor still reading 10 and nothing to say so."""
    assert EF_CORE_FLOOR == DOTNET_FLOOR


def test_being_ahead_of_the_floor_is_never_a_finding() -> None:
    """A floor is a MINIMUM. A new app started on the next major -- before the estate lifts
    the floor for everyone -- must pass, or the rule would punish being current."""
    ahead = EF_CORE_FLOOR + 1
    assert not core(f'    <PackageReference Include="Microsoft.EntityFrameworkCore" Version="{ahead}.0.0" />')


def test_the_dispatcher_reaches_the_ef_core_rule() -> None:
    with temporary_repo() as repo:
        project = repo / "App" / "App.csproj"
        rules = [v.rule for v in check_source_file(project, [EF9], CheckConfig(), {}, repo)]
        assert "ef-core-support" in rules


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "ef-provider cases"))

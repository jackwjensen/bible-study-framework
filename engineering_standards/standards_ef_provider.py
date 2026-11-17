"""The two EF rules: the EF Core major itself, and a provider that cannot follow it.

    ef-core-support      the EF Core packages are at or above EF_CORE_FLOOR
    ef-provider-support  the provider still ships for a supported EF Core major

They are separate because they fail independently and for different reasons, and a repo can
fail either one alone. A repo on a dead provider is stuck at whatever major that provider last
shipped; a repo with a perfectly healthy provider can simply have never bumped its EF packages,
and nothing else in the pack would notice.

Split out of standards_versions.py on 2026-08-25, following its tests, which had already moved
to test_standards_ef_provider.py. That module carries four independent rules and four version
tables and had reached the 500-line limit three times in two days; this rule shares nothing with
the other three but the Violation type, so it is the seam rather than a convenient cut.

WHY THE RUNTIME FLOOR CANNOT COVER THIS. `runtime-support` reads <TargetFramework>. An EF Core
provider is bound to the EF Core MAJOR, not to the TFM, so a project can be cleanly on net10.0,
pass that rule, and still be held at EF Core 9 by its provider -- supported right up until the
day the fixes stop, with nothing reporting it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import Violation
from standards_exemptions import line_exemption_reason

# One-way: standards_versions does not import this module, so there is no cycle. The import
# exists so EF_CORE_FLOOR can be DERIVED from DOTNET_FLOOR rather than restated as a literal.
from standards_versions import DOTNET_FLOOR

EF_PROVIDER_RULE = "ef-provider-support"
EF_CORE_RULE = "ef-core-support"

# ---- tier 4: the oldest EF Core major the estate will run -----------------------------------
#
# EF Core majors track .NET majors, so this is DOTNET_FLOOR's sibling and moves with it. EF
# Core 9 stops receiving fixes on 2026-11-10, the same day .NET 8 and 9 do.
#
# WHY THE TFM FLOOR DOES NOT ALREADY COVER THIS, demonstrated by InvoTrack for several weeks:
# every project net10.0, SDK 10.0.100, base images 10.0 -- and EF Core pinned at 9.0.11. It
# passed `runtime-support` the whole time, because that rule reads <TargetFramework> and the
# TFM was never the problem. The only thing recording the gap was a written exemption on
# ef-provider-support, i.e. a rule about the PROVIDER -- so had the provider been healthy and
# the packages merely stale, nothing in the pack would have said a word.
#
# WHICH PACKAGES. Only the Microsoft-owned EF packages, whose Version IS the EF Core version:
# `Microsoft.EntityFrameworkCore*` and the two ASP.NET Core companions that ship in lockstep.
# Provider packages are deliberately NOT judged here -- Npgsql and Oracle's MySQL provider do
# track the EF major, but third-party providers are free not to, and reading a provider's
# version as an EF version would be a confident wrong answer. Dead providers are
# ef-provider-support's job; this rule is about the packages that cannot lie about the major.
#
# DERIVED FROM DOTNET_FLOOR, NEVER WRITTEN AS ITS OWN NUMBER. EF Core majors track .NET majors,
# so these two are one decision, and a second literal would be a second place to forget. Moving
# DOTNET_FLOOR to 11 moves this with it, which is the whole point: EF follows .NET, and nobody
# has to remember that it should.
#
# IT IS A MINIMUM, NOT AN EQUALITY. A new app started on .NET 11 with EF Core 11 passes a floor
# of 10 -- being ahead of the estate is never a finding, and the floor is lifted deliberately
# once the estate is ready, not the moment a newer major exists.
EF_CORE_FLOOR = DOTNET_FLOOR

EF_CORE_PACKAGES = re.compile(
    r"^(?:Microsoft\.EntityFrameworkCore(?:\.[\w.]+)?"
    r"|Microsoft\.AspNetCore\.(?:Identity|Diagnostics)\.EntityFrameworkCore)$",
    re.IGNORECASE,
)

# `Version="10.0.11"`, `Version="10.*"`. A reference with NO Version attribute is skipped: that
# is Central Package Management, where the version lives in Directory.Packages.props, and
# guessing would be worse than the silence.
PACKAGE_VERSION = re.compile(r"""Version=["']v?(\d+)(?:[.\w*-]*)["']""", re.IGNORECASE)

# ---- tier 4 again: a data provider that cannot follow the runtime --------------------------
#
# THE RUNTIME FLOOR IS NOT ENOUGH ON ITS OWN, and this rule exists because InvoTrack proved it
# on 2026-08-21. That repo passes `runtime-support` cleanly -- every project is net10.0, the
# SDK is 10.0.100, the base images are 10.0 -- while its EF Core packages are deliberately held
# at 9.0.11. Not an oversight: `Pomelo.EntityFrameworkCore.MySql` has no EF Core 10 release, an
# EF Core provider is bound to its major, and bumping Identity/Diagnostics to 10.0.x
# transitively pulls EF Core 10 and breaks the provider. The combination is supported today
# (EF Core 9 targets net8.0+ and runs fine on the .NET 10 runtime), so nothing is broken -- and
# nothing would have told anyone before the fixes stopped.
#
# WHY THIS IS TIER 4 AND NOT A PREFERENCE. Same test the DOTNET_FLOOR note applies to itself:
# it is a support fact with a date, not "what we run today". EF Core 9 stops receiving fixes on
# 2026-11-10, the same day .NET 8 and 9 do. A provider with no successor release means a repo
# cannot leave EF Core 9 without changing provider, and the work to change provider is not the
# kind you start in the last fortnight. So the scanner FAILS and names it, months early, and
# never edits a project file -- "fail and name it" and "rewrite it for you" remain different
# powers.
#
# Verified against NuGet on 2026-08-21 rather than from a changelog: Pomelo's newest published
# version of ANY kind is 9.0.0, with no 10.x preview, rc, or stable. Oracle's
# MySql.EntityFrameworkCore has shipped 10.0.1, 10.0.7 and 10.0.9.
#
# THE REPLACEMENT WAS NOT A DROP-IN, AND THAT IS NOW SETTLED RATHER THAN FEARED. This block
# used to name three things to establish first and stop there, which was right while nobody had
# done it. InvoTrack did, on 2026-08-24, and all three came back: ExecuteUpdate/ExecuteDelete
# and EnableRetryOnFailure survive; `has-pending-model-changes` passes once the snapshot is
# regenerated; and the type mapping differed on exactly two things, both properties that had
# never declared a type, so the model had been letting the installed provider choose (diffed
# column by column -- 287 each side, none missing, none extra).
#
# The one real surprise was structural, not semantic: migration `.Designer.cs` files are
# provider-generated and do not compile against another provider, so the history has to be
# squashed to a baseline and an existing database STAMPED rather than migrated.
#
# HOW TO ANSWER IT. Migrate. For Pomelo there is no other answer -- see EF_PROVIDER_NO_EXEMPTION
# below. A future entry that is genuinely blocked is still exemptable, and that exemption is
# still a decision with a date rather than debt. What it never is, is silence.
#
# package id -> (replacement package, EF Core major it is stuck on, that major's end of support)
EF_PROVIDER_SUNSET: dict[str, tuple[str, int, str]] = {
    "pomelo.entityframeworkcore.mysql": ("MySql.EntityFrameworkCore", 9, "2026-11-10"),
}

# Providers that may NOT be exempted -- the finding stands until the package is gone.
#
# THE EXEMPTION EXISTED TO BUY TIME, AND THE TIME IS SPENT (2026-08-24). Until this date the
# honest answer to "why are you still on Pomelo?" was "the replacement is unproven and the swap
# is a spike, not a version bump", and the rule accepted that in writing. The spike has now been
# run -- see the three results in the block above -- so the remaining reason to stay is inertia,
# which is what an exemption with a revisit date turns into when nobody revisits. Pomelo has
# published nothing since 9.0.0; waiting is now a decision to run an unsupported data layer past
# 2026-11-10.
#
# WHY A SET AND NOT A FLAG ON THE ROW ABOVE. A future entry may well be genuinely blocked, and
# that entry should still be exemptable -- the mechanism is not removed, only withdrawn from the
# one provider where it has stopped being true. Adding a package here is the explicit act of
# saying "there is a proven route off this one".
EF_PROVIDER_NO_EXEMPTION: frozenset[str] = frozenset({"pomelo.entityframeworkcore.mysql"})

# NuGet package ids are case-insensitive, and the three repos carrying this one spell it
# `Pomelo.EntityFrameworkCore.MySql` while the lookup above is lowercased -- so the id is
# normalised before lookup rather than matched literally. A case-sensitive match here would
# pass every real project file in the estate while looking correct.
PACKAGE_REFERENCE = re.compile(r"""<PackageReference\s+Include=["']([^"']+)["']""", re.IGNORECASE)


def check_ef_provider_support(path: Path, lines: list[str], distributes_binaries: bool = False) -> Iterable[Violation]:
    """Flag an EF Core provider with no release for a still-supported EF Core major.

    The runtime floor cannot see this: a project can be cleanly on net10.0 and still be pinned
    to EF Core 9 by its provider, because an EF Core provider is bound to the EF Core major
    rather than to the runtime. `runtime-support` reads <TargetFramework>; this reads the
    package that decides how long that project can keep receiving data-layer fixes.

    Matched case-insensitively, because NuGet ids are -- see the note on PACKAGE_REFERENCE.

    `distributes_binaries` comes from the repo's `.standards.json` and re-opens the exemption
    for a repo that CONVEYS its binaries. The replacement is GPL-2.0-only WITH
    Universal-FOSS-exception-1.0 where Pomelo is MIT, and GPLv2 attaches to distribution -- so
    for a shipped binary this rule would otherwise be an instruction to create a licensing
    problem. Default False, because a hosted service distributes nothing.
    """
    for index, line in enumerate(lines):
        found = PACKAGE_REFERENCE.search(line)
        if not found:
            continue

        package_id = found.group(1).strip().lower()
        entry = EF_PROVIDER_SUNSET.get(package_id)
        if entry is None:
            continue

        exemptable = distributes_binaries or package_id not in EF_PROVIDER_NO_EXEMPTION
        if exemptable and line_exemption_reason(lines, index, EF_PROVIDER_RULE):
            continue

        replacement, efcore_major, ends = entry

        if not exemptable:
            yield Violation(
                path=path,
                line=index + 1,
                rule=EF_PROVIDER_RULE,
                message=(
                    f"{found.group(1)} must be replaced with {replacement}; this one cannot be "
                    f"exempted. It has no release for an EF Core major after {efcore_major}, "
                    f"and EF Core {efcore_major} stops receiving fixes on {ends}. The swap was "
                    f"a spike until 2026-08-24, when InvoTrack ran it and the three unknowns "
                    f"came back clean: ExecuteUpdate/ExecuteDelete and EnableRetryOnFailure all "
                    f"survive, `has-pending-model-changes` passes once the snapshot is "
                    f"regenerated, and the only type-mapping differences were properties that "
                    f"had never declared a type (see the `money-precision` rule). "
                    f"WHAT TO DO: swap the package, replace `UseMySql(conn, serverVersion)` "
                    f"with `UseMySQL(conn)`, re-point any `MySqlConnector.MySqlException` at "
                    f"`MySql.Data.MySqlClient.MySqlException`, declare precision on every "
                    f"[Column] decimal, and regenerate the model snapshot. Note the licence "
                    f"differs from Pomelo's MIT: GPL-2.0-only WITH "
                    f"Universal-FOSS-exception-1.0, which is settled for a service you host "
                    f"and do not distribute. IF THIS REPO SHIPS A BINARY that links the "
                    f"provider -- a desktop client, an on-prem install -- then migrating would "
                    f"create a licensing problem rather than solve a support one, and MIT "
                    f'Pomelo is the right answer: set `"distributesBinaries": true` in '
                    f".standards.json, which re-opens the line exemption for this rule. Say it "
                    f"there and not here, because it is a fact about the whole repo."
                ),
            )
            continue
        yield Violation(
            path=path,
            line=index + 1,
            rule=EF_PROVIDER_RULE,
            message=(
                f"{found.group(1)} has no release for an EF Core major after "
                f"{efcore_major}, so EF Core {efcore_major} is the ceiling for this project's "
                f"data layer and it cannot go past that without changing provider (this holds "
                f"whether the project is already at {efcore_major} or still below it). "
                f"EF Core {efcore_major} stops receiving fixes on {ends}. "
                f"Note the project can be fully on the current runtime and still be caught by "
                f"this -- an EF Core provider tracks the EF Core major, not the TFM, which is "
                f"why the runtime floor passes it. Replacement: {replacement}. It is NOT a "
                f"drop-in: establish ExecuteUpdate/ExecuteDelete support, type mapping against "
                f"the existing schema, and that `dotnet ef migrations "
                f"has-pending-model-changes` still passes, before committing to it. If the "
                f"decision is to wait for the current provider to catch up, exempt the line "
                f"with a written reason naming that and the date it has to be revisited. "
                f"NOTE: this rule fires on project files, which are XML, and an XML comment "
                f"CANNOT contain a double hyphen -- so write the marker with a colon "
                f"(`standards: {EF_PROVIDER_RULE} exempt : reason`) and keep it on ONE line "
                f"immediately above the reference, because the parser walks a contiguous "
                f"comment block and the continuation lines of a multi-line XML comment do not "
                f"read as comments."
            ),
        )


def check_ef_core_support(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag an EF Core package below the estate floor.

    Reads the project file's own package references, so it sees the pin rather than the
    restored graph -- which is the right level: the pin is what a person edits, and what a
    reviewer can see in the diff.
    """
    for index, line in enumerate(lines):
        reference = PACKAGE_REFERENCE.search(line)
        if not reference or not EF_CORE_PACKAGES.match(reference.group(1).strip()):
            continue

        version = PACKAGE_VERSION.search(line)
        if version is None:
            continue

        major = int(version.group(1))
        if major >= EF_CORE_FLOOR:
            continue
        if line_exemption_reason(lines, index, EF_CORE_RULE):
            continue

        yield Violation(
            path=path,
            line=index + 1,
            rule=EF_CORE_RULE,
            message=(
                f"{reference.group(1)} is on EF Core {major}, below the estate floor of "
                f"EF Core {EF_CORE_FLOOR}. EF Core majors track .NET majors, so a project can "
                f"be cleanly on the current TFM and still be here -- `runtime-support` reads "
                f"<TargetFramework> and will not see this. EF Core 9 stops receiving fixes on "
                f"2026-11-10, the same day .NET 8 and 9 do. "
                f"MOVE EVERY EF PACKAGE TOGETHER, including the ones that do not say "
                f"EntityFrameworkCore in an obvious way: Identity and Diagnostics ship in "
                f"lockstep, the test project's provider must match or it throws "
                f"MissingMethodException at run time rather than failing to build, and the "
                f"`dotnet-ef` pin in .config/dotnet-tools.json carries the major independently. "
                f"If the data provider is what pins this repo, that is ef-provider-support's "
                f"finding and the provider is the thing to change."
            ),
        )

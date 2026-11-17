"""--write-baseline must not grandfather files that are exempt from the length limit.

Written after a real incident (DonorLink, 2026-08-15): a --write-baseline run recorded the
seven `Data/Translations/*.cs` files -- all carrying a valid `standards: file-length exempt`
marker since 2026-08-10 -- into `.standards-baseline.json`, and the write had to be reverted
by hand. Baselining an exempt file converts a written decision into frozen debt: the next
line added to it (a new translation key, a new lookup row) fails the build, which is exactly
the outcome the exemption exists to prevent.

The suite drives the real CLI via subprocess rather than importing the hyphenated module,
because the writer's behaviour IS the contract here: the commit hook, CI, and a human at a
terminal all reach it through exactly this entry point.

Run: pytest test_standards_baseline.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH

SCANNER = Path(__file__).resolve().parent / "check-source-limits.py"

REASON = "declarative lookup table; length tracks entry count, not responsibility"
assert len(REASON) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"

SHRUG = "it is fine"
assert len(SHRUG) < MIN_EXEMPTION_REASON_LENGTH, "fixture must sit under the reason floor"


def make_repo(root: Path) -> None:
    """A minimal repo: a 10-line limit and three .cs files around it.

    * alpha.cs   -- over the limit, no marker: MUST be baselined.
    * bravo.cs   -- over the limit, valid exemption marker: must NOT be baselined.
    * charlie.cs -- under the limit: must not be baselined either.
    """
    (root / ".standards.json").write_text(json.dumps({"maxFileLines": 10}), encoding="utf-8")
    # The plain check run also enforces the gitignore rule; satisfy it so these tests
    # exercise only the baseline writer.
    (root / ".gitignore").write_text(".env*\n", encoding="utf-8")
    filler = [f"int x{i};" for i in range(14)]
    (root / "alpha.cs").write_text("\n".join(["// plain"] + filler), encoding="utf-8")
    (root / "bravo.cs").write_text(
        "\n".join([f"// standards: file-length exempt -- {REASON}"] + filler),
        encoding="utf-8",
    )
    (root / "charlie.cs").write_text("\n".join(filler[:5]), encoding="utf-8")


def run_scanner(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER), "--root", str(root), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def written_baseline(root: Path) -> dict:
    return json.loads((root / ".standards-baseline.json").read_text(encoding="utf-8"))


def test_writer_skips_exempt_files(tmp_path: Path) -> None:
    make_repo(tmp_path)

    result = run_scanner(tmp_path, "--write-baseline", "--baseline-consent")
    assert result.returncode == 0, result.stderr

    baseline = written_baseline(tmp_path)
    oversized = baseline["oversizedFiles"]
    assert "alpha.cs" in oversized, "an over-limit file without a marker must be baselined"
    assert "bravo.cs" not in oversized, "a validly exempt file must never become baseline debt"
    assert "charlie.cs" not in oversized, "an under-limit file has nothing to grandfather"
    assert baseline.get("packVersion"), "a baseline must record the scanner version that wrote it"


def test_exempt_file_still_passes_the_check_after_the_write(tmp_path: Path) -> None:
    """The pairing that makes skipping safe: not-in-baseline AND not-failing.

    If the checker half ever stopped honouring the marker while the writer skipped it, the
    exempt file would fail every run with nothing grandfathering it -- so assert the whole
    round trip, not just the writer's output.
    """
    make_repo(tmp_path)
    assert run_scanner(tmp_path, "--write-baseline", "--baseline-consent").returncode == 0

    check = run_scanner(tmp_path)
    assert check.returncode == 0, f"stdout: {check.stdout}\nstderr: {check.stderr}"
    assert "exempt: bravo.cs" in check.stdout, "the load-bearing exemption must be named"


def test_reason_floor_still_gates_the_writer(tmp_path: Path) -> None:
    """A marker with a shrug for a reason is not an exemption, so the writer baselines it.

    The reason floor is the only thing separating a decision from a suppression, and a
    writer that accepted any marker text would silently widen the escape hatch the checker
    keeps narrow.
    """
    make_repo(tmp_path)
    filler = [f"int y{i};" for i in range(14)]
    (tmp_path / "delta.cs").write_text(
        "\n".join([f"// standards: file-length exempt -- {SHRUG}"] + filler),
        encoding="utf-8",
    )

    assert run_scanner(tmp_path, "--write-baseline", "--baseline-consent").returncode == 0
    oversized = written_baseline(tmp_path)["oversizedFiles"]
    assert "delta.cs" in oversized, "an under-floor reason must not exempt a file"


# ---- support deadlines are never grandfathered ----------------------------------------------
#
# A baseline is for debt: work looked at and deliberately deferred, where "not yet" is a
# coherent answer. A support deadline is not debt -- the date arrives whether or not anybody
# baselined it, so grandfathering one does not defer the work, it only stops the repo being
# told. NEVER_BASELINED holds those rules; these tests are what stop it silently regressing,
# because the failure mode is a rule that still LOOKS enforced while never firing on adoption.

POMELO = '<PackageReference Include="Pomelo.EntityFrameworkCore.MySql" Version="9.0.0" />'


def add_project(root: Path, *lines: str) -> None:
    """Add a .csproj, and widen .gitignore because adding one changes what the repo IS.

    A repo with a project file reads as .NET, so `gitignore-build-output` starts asking for
    bin/ and obj/ -- and that rule is itself never baselined, so it would fail these runs for
    a reason that has nothing to do with what they are testing. Caught by the exemption test
    below on its first run.
    """
    (root / ".gitignore").write_text(".env*\nbin/\nobj/\n", encoding="utf-8")
    (root / "App.csproj").write_text(
        "\n".join(["<Project>", "  <ItemGroup>", *lines, "  </ItemGroup>", "</Project>"]),
        encoding="utf-8",
    )


def test_the_provider_rule_is_never_written_into_the_baseline(tmp_path: Path) -> None:
    make_repo(tmp_path)
    add_project(tmp_path, f"    {POMELO}")

    assert run_scanner(tmp_path, "--write-baseline", "--baseline-consent").returncode == 0
    recorded = json.dumps(written_baseline(tmp_path))
    assert "ef-provider-support" not in recorded


def test_the_runtime_rule_is_never_written_into_the_baseline(tmp_path: Path) -> None:
    """The hole this generalisation was found through -- see check-source-limits.py."""
    make_repo(tmp_path)
    add_project(tmp_path, "    <TargetFramework>net8.0</TargetFramework>")

    assert run_scanner(tmp_path, "--write-baseline", "--baseline-consent").returncode == 0
    recorded = json.dumps(written_baseline(tmp_path))
    assert "runtime-support" not in recorded


def test_tests_uncovered_module_is_grandfathered_by_the_write(tmp_path: Path) -> None:
    """The opposite hole, found in allegro-it-services 2026-09-03: a REPOSITORY-level rule
    that IS baselineable adoption debt but rode in on none of the writer's per-file checks.

    `tests-uncovered-module` is not in NEVER_BASELINED and the skill calls it the baseline's
    job outright, but it lives in `repository_violations`, not `collect_violations`, so it was
    never handed to the writer -- a repo with untested modules got a red gate it could neither
    exempt nor baseline away (199 of them there, against an empty baseline). The writer's
    allow-list now names it, and this asserts the round trip: it fires, the write records it,
    the check passes.
    """
    make_repo(tmp_path)
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "widget.php").write_text(
        "<?php\nfunction widget_render(): string { return 'x'; }\n", encoding="utf-8"
    )

    before = run_scanner(tmp_path)
    assert "tests-uncovered-module" in before.stdout + before.stderr

    assert run_scanner(tmp_path, "--write-baseline", "--baseline-consent").returncode == 0
    assert "tests-uncovered-module" in json.dumps(written_baseline(tmp_path))

    assert run_scanner(tmp_path).returncode == 0


def test_a_support_finding_still_fails_the_check_after_a_baseline_write(
    tmp_path: Path,
) -> None:
    """The half that makes the exclusion mean anything.

    Excluding a rule from the WRITER is pointless if the CHECKER passes anyway. This is the
    pairing test: after adoption has grandfathered everything it legitimately can, the repo
    must still fail on the provider ceiling -- that failure is the entire point of the rule.
    """
    make_repo(tmp_path)
    add_project(tmp_path, f"    {POMELO}")

    assert run_scanner(tmp_path, "--write-baseline", "--baseline-consent").returncode == 0

    check = run_scanner(tmp_path)
    assert check.returncode != 0, "a baselined repo must still fail on a support deadline"
    assert "ef-provider-support" in check.stdout


def test_neither_a_baseline_nor_an_exemption_gets_a_repo_off_pomelo(tmp_path: Path) -> None:
    """Pomelo has no way out left, and this test covers BOTH doors at once.

    Before 2026-08-24 the baseline was closed (a support deadline is not debt) while the line
    exemption was open (waiting for the provider was a real decision). Now that the swap has
    been proven in InvoTrack, both are shut, and a repo has to actually migrate.

    Worth pinning end to end rather than at the rule: --write-baseline and the exemption marker
    are separate mechanisms, and silencing the finding through either one would look exactly
    like compliance from the outside.
    """
    make_repo(tmp_path)
    add_project(
        tmp_path,
        "    <!-- standards: ef-provider-support exempt -- waiting on Pomelo to ship an "
        "EF Core 10 provider; revisit 2026-10-01 and swap to Oracle's if it has not. -->",
        f"    {POMELO}",
    )

    assert run_scanner(tmp_path, "--write-baseline", "--baseline-consent").returncode == 0
    check = run_scanner(tmp_path)
    assert check.returncode != 0, "an exempted, baselined Pomelo must still fail the scan"
    assert "ef-provider-support" in check.stdout


# ---- the OTHER baseline: ESLint's suppressions file --------------------------------------


def write_eslint_suppressions(root: Path, payload: object) -> None:
    (root / "eslint-suppressions.json").write_text(json.dumps(payload), encoding="utf-8")


def test_a_clean_run_names_the_eslint_suppressions_it_found(tmp_path: Path) -> None:
    """A repo can carry TWO baselines, and only one of them used to be visible.

    Measured in allegro-it-services on 2026-09-03: deleting `.standards-baseline.json`
    surfaced 205 findings and read as a full reset, while `eslint-suppressions.json` quietly
    went on holding 96 suppressed violations across 66 files. Relief nobody can see is relief
    nobody re-examines, which is the same argument that puts the grandfathered, exempt and
    tuned counts on the summary line.

    Reported on its own line rather than folded into that summary: this is a count the scanner
    reads but does not enforce, and the counts beside `clean` are ones it does.
    """
    make_repo(tmp_path)
    write_eslint_suppressions(
        tmp_path,
        {
            "src/one.ts": {"max-lines-per-function": {"count": 2}},
            "src/two.ts": {"complexity": {"count": 3}},
        },
    )

    assert run_scanner(tmp_path, "--write-baseline", "--baseline-consent").returncode == 0
    check = run_scanner(tmp_path)

    assert check.returncode == 0
    assert "holds 5 violation(s) across 2 file(s)" in check.stdout


def test_the_suppressions_note_shows_on_a_RED_run_too(tmp_path: Path) -> None:
    """Relief matters most on a run that is already failing.

    `tuned:` and `exempt:` print before the violations because that is when somebody is
    deciding what the repo still owes; the ESLint count belongs in the same place. Printing it
    only on the clean line would hide it from exactly the run that needed it.
    """
    make_repo(tmp_path)
    write_eslint_suppressions(tmp_path, {"src/one.ts": {"complexity": {"count": 4}}})

    check = run_scanner(tmp_path)

    assert check.returncode != 0, "alpha.cs is over the limit, so this run is red"
    assert "holds 4 violation(s) across 1 file(s)" in check.stdout


def test_a_repo_with_no_eslint_suppressions_says_nothing_about_them(tmp_path: Path) -> None:
    """The note appears only when there is something to report; an absent file is not news."""
    make_repo(tmp_path)

    assert run_scanner(tmp_path, "--write-baseline", "--baseline-consent").returncode == 0
    check = run_scanner(tmp_path)

    assert "eslint-suppressed" not in check.stdout


def test_an_unreadable_suppressions_file_does_not_break_the_run(tmp_path: Path) -> None:
    """This is a report line, not a gate: a malformed file must not take the scanner down."""
    make_repo(tmp_path)
    (tmp_path / "eslint-suppressions.json").write_text("{ not json", encoding="utf-8")

    check = run_scanner(tmp_path, "--write-baseline", "--baseline-consent")

    assert check.returncode == 0


def test_an_empty_suppressions_file_reports_nothing(tmp_path: Path) -> None:
    """`eslint --prune-suppressions` leaves `{}` behind once the debt is paid off."""
    make_repo(tmp_path)
    write_eslint_suppressions(tmp_path, {})

    assert run_scanner(tmp_path, "--write-baseline", "--baseline-consent").returncode == 0
    check = run_scanner(tmp_path)

    assert "eslint-suppressed" not in check.stdout

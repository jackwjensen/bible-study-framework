#!/usr/bin/env python3
"""Tests for the pack-update check a consuming repo runs at push.

Plain asserts, no pytest, so it runs anywhere Python does:

    python engineering_standards/test_standards_pack_update.py

The two that matter most are the silences. `silent_when_pack_absent` is what keeps CI and
other developers' machines from printing a nag about a pack they cannot see, and
`sibling_adopted_repo_is_not_the_pack` is what stops the upward walk mistaking a neighbouring
repo for the supplier -- the failure that would turn a one-to-one lookup back into discovery.

`reads_the_repos_own_version` pins the bug this check shipped with for ten minutes: report()
first took the repo's version from an IMPORT, which resolved to whichever copy was on
sys.path, so running the pack's own script against a repo compared the pack to itself and
said nothing while the repo was six days behind. It would have worked in production, which is
what made it worth removing -- a bug that only appears when you test it by hand is a bug
nobody finds.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_pack_update import find_pack, report, version_key  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(label)


def make_pack(root: Path, version: str, at: str = "AllegroIt/engineering-standards") -> Path:
    """A directory that looks like the pack: the dev marker plus a version stamp."""
    pack = root / at
    (pack / "engineering_standards" / "dev").mkdir(parents=True)
    (pack / "engineering_standards" / "dev" / "pack_manifest.py").write_text("# marker\n", encoding="utf-8")
    (pack / "engineering_standards" / "standards_versions.py").write_text(
        f'PACK_VERSION = "{version}"\n', encoding="utf-8"
    )
    return pack


def make_repo(root: Path, at: str, version: str = "2026.08.21-5", config: dict | None = None) -> Path:
    """An adopted repo: the scanner and a version stamp, and NO engineering_standards/dev."""
    repo = root / at
    (repo / "engineering_standards").mkdir(parents=True)
    (repo / "engineering_standards" / "check-source-limits.py").write_text("# scanner\n", encoding="utf-8")
    (repo / "engineering_standards" / "standards_versions.py").write_text(
        f'PACK_VERSION = "{version}"\n', encoding="utf-8"
    )
    if config is not None:
        (repo / ".standards.json").write_text(json.dumps(config), encoding="utf-8")
    return repo


# ---- version comparison ---------------------------------------------------------------------

# Lexicographically "2026.08.27-10" < "2026.08.27-5", which would report "behind" while ahead.
check("a tenth release in a day sorts after the fifth", version_key("2026.08.27-10") > version_key("2026.08.27-5"))
check("a later date sorts after an earlier one", version_key("2026.09.01-1") > version_key("2026.08.27-9"))
check("an unparseable version still equals itself", version_key("dev") == version_key("dev"))

# ---- finding the pack -----------------------------------------------------------------------

with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    pack = make_pack(root, "2026.08.27-5")
    same_owner = make_repo(root, "AllegroIt/InvoTrack")
    other_owner = make_repo(root, "ERHR/ligelon-compliance")

    check("found from a repo beside it", find_pack(same_owner) == pack)
    check("found from a repo under a different owner", find_pack(other_owner) == pack)

    # The discriminator earning its keep: a sibling adopted repo has engineering_standards/ but never
    # engineering_standards/dev, so the walk must not stop on it.
    check("sibling_adopted_repo_is_not_the_pack", find_pack(same_owner) != same_owner)

with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    orphan = make_repo(root, "somewhere/else")
    check("pack_absent_is_found_as_absent", find_pack(orphan) is None)

    # NOT silence. A check that could not run and says nothing reads exactly like a check
    # that ran and passed -- the estate's most repeated failure.
    unreachable = report(orphan)
    check("an unreachable pack WARNS rather than passing quietly", unreachable is not None and unreachable.is_warning)
    check("the warning names the fix", unreachable is not None and "packPath" in unreachable.text)

    # THE SCOPE OF THE WARNING, and the reason this case is here twice. The first wording
    # said "Nothing was checked", which is false: adoption copies the pack, so every gate
    # ran from this repo's own scanner. It also printed directly beneath "verify: N passed"
    # and contradicted it. A warning that inflates what failed teaches people to skip
    # warnings, which is worse than the silence it replaced.
    check(
        "the warning does NOT claim verification was skipped",
        unreachable is not None
        and "Nothing was checked" not in unreachable.text
        and "gates themselves ran normally" in unreachable.text,
    )
    check(
        "the warning says what actually could not be done",
        unreachable is not None and "check for a newer pack" in unreachable.text,
    )

# ---- packPath override ----------------------------------------------------------------------

with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    pack = make_pack(root, "2026.08.27-5", at="odd/place/engineering-standards")
    repo = make_repo(root, "unusual/repo", config={"packPath": str(pack)})
    check("an explicit packPath is honoured", find_pack(repo) == pack)

    pointed_wrong = make_repo(root, "unusual/other", config={"packPath": str(root / "nope")})
    check("a packPath pointing at nothing does not crash", find_pack(pointed_wrong) is None)

    # A declared-but-wrong path is a broken setting, not an absent pack, and the message has
    # to say which -- otherwise the fix reads as "install the pack" when it is "fix the path".
    misconfigured = report(pointed_wrong)
    check(
        "a wrong packPath is reported as a wrong packPath",
        misconfigured is not None and misconfigured.is_warning and "points at" in misconfigured.text,
    )

# ---- what it reports ------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    make_pack(root, "2026.08.27-5")

    behind = report(make_repo(root, "AllegroIt/Behind", version="2026.08.21-5"))
    check(
        "a repo behind the pack is told, with both versions",
        behind is not None and "2026.08.21-5" in behind.text and "2026.08.27-5" in behind.text,
    )
    check("the report names the remedy", behind is not None and "/apply-standards" in behind.text)
    check("the report says nothing was changed", behind is not None and "nothing was changed" in behind.text)
    # Being behind is a finding, not an admission -- it belongs on stdout.
    check("being behind is not a warning", behind is not None and not behind.is_warning)

    check("a current repo is silent", report(make_repo(root, "AllegroIt/Current", version="2026.08.27-5")) is None)
    # A repo can legitimately be ahead while the pack is mid-edit on this machine.
    check(
        "a repo ahead of the pack is silent", report(make_repo(root, "AllegroIt/Ahead", version="2026.09.02-1")) is None
    )

    # The bug this check shipped with: the repo's version must come from the repo on disk,
    # never from an import that resolves to whichever copy is on sys.path.
    check("reads_the_repos_own_version", report(make_repo(root, "AllegroIt/Older", version="2026.01.01-1")) is not None)

with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    pack = make_pack(root, "2026.08.27-5")
    check("the pack does not nag about itself", report(pack) is None)


def test_every_pack_update_case_holds() -> None:
    """Makes this module's coverage visible to pytest, and its passing checkable.

    The cases above run at import and collect into FAILURES rather than raising, so without
    this function pytest would collect nothing from the file and a broken walk would surface
    as silence -- which is also what this check looks like when it is working correctly.
    """
    assert not FAILURES, "\n".join(FAILURES)


if __name__ == "__main__":
    if FAILURES:
        print(f"test_standards_pack_update: {len(FAILURES)} failure(s)\n")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("test_standards_pack_update: all cases pass")

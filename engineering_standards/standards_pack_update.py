#!/usr/bin/env python3
"""Does this repo's copy of the pack lag the pack itself? Asked BY the repo, at push.

THE PULL MODEL'S MISSING HALF. Applying the pack is a pull: a repo takes an update when its
owner is working in it, by running /apply-standards there. That left one gap -- nothing told
the owner an update existed. The estate-wide drift report used to, badly: it printed "799
files across 11 repos are behind" while you were pushing the PACK, which is the one place you
cannot act on it, so it became a number to scroll past. It was removed on 2026-08-27 along
with the repo discovery it needed.

This is the same information delivered the other way round. The consuming repo asks, at its
own push, about itself, where its owner is standing and can act. One file read.

IT REPORTS AND NEVER BLOCKS, and never applies anything. Being behind is normal in a pull
model -- it is a state, not an incident -- and blocking would fail every push in every repo
the moment the pack changed, which is worse than the estate-wide sync it replaced.

WHY IT DOES NOT SELF-UPDATE, which was the first design and is the tempting one. The gate
would then run against the wrong rules, whichever order you choose. Update after the gate and
the push you just verified was checked by the old scanner, with the new files going out
unverified. Update before it and your push fails on rules you have never seen, in the middle
of shipping something unrelated -- which is precisely the shape that produced three uses of
SKIP_STANDARDS_GATE=1 in allegro-it-services, because a gate that fails for reasons
unconnected to the work teaches the bypass rather than the fix. It would also leave
uncommitted edits to tracked files nobody made, AFTER the push had gone, and have pre-push
rewrite itself mid-execution.

So the remedy stays one deliberate command. /apply-standards re-runs the gate against the new
rules as its own act, where a failure is expected and welcome.

FINDING THE PACK IS A CONSUMER RESOLVING ONE SUPPLIER, not the reverse. `adopted_repos()` --
deleted the same day -- was the supplier enumerating every consumer, one-to-many, and the
machinery a push needs. This walks up from ONE repo looking for ONE known directory, and can
never fan out. The discriminator is `engineering_standards/dev/pack_manifest.py`, which the pack has and no
consuming repo ever receives (only the top level of engineering_standards/ is synced), so a sibling adopted
repo can never be mistaken for the pack.

AN UNREACHABLE PACK WARNS; IT IS NOT SILENT. This shipped silent for an hour, justified by the
pack's rule for a toolchain that is not on the machine -- which was the wrong reading of that
rule, because it says such a gate "SKIPS with a named reason", and a named reason is the whole
point. A check that could not run and says nothing looks exactly like a check that ran and
passed. That confusion is this estate's most repeated failure: the drift guard three documents
attributed to CI while it existed nowhere, the pytest gate that reported skipped on a machine
without pytest, and `prototypes` missing from the drift report in a way that read identically
to being fine.

So a repo that cannot find its pack says so on every push, and names the fix. It still never
blocks -- a contractor without the pack must be able to push -- and the message is on stderr,
because "I could not check" is a different claim from "here is what I found".

BUT THE WARNING MUST NOT OVERSTATE WHAT FAILED, which the first wording did: it said "Nothing
was checked", and that is false in a way that matters. Adoption COPIES the pack -- every repo
holds its own scanner, hooks and rules, and CI runs them on a runner that never checks the
pack out (it is private) -- so a missing pack degrades exactly one thing: knowing whether a
NEWER version exists. Every gate still ran. Worse, this prints immediately after "verify: N
passed", so the original text contradicted the line above it and would have read as "your push
went out unverified". A warning that inflates its own scope trains people to ignore warnings,
which costs more than the silence it replaced.

Source of truth: engineering-standards/engineering_standards/standards_pack_update.py
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from standards_pack_identity import PACK_DIRECTORY, PACK_SELF_MARKER
from standards_runtime import VERSIONS_MODULE

# Parsed textually, never imported: the repo's own standards_versions is already in
# sys.modules under that name, and two different versions of one module cannot both be
# imported into a single process. sync_pack.py reads the repo's copy the same way, for the
# same reason.
PACK_VERSION_PATTERN = re.compile(r'^PACK_VERSION\s*=\s*"([^"]+)"', re.MULTILINE)

# The file that identifies the pack itself. Consuming repos receive the package's top level
# only, so they never have its `dev/` -- which makes this an exact discriminator between the
# pack and any other adopted repo sitting beside it. Stated ONCE, in standards_pack_identity:
# the scanner's scope and the Python gates ask the same question, and three copies of a path
# is how the 2026-09-02 move broke a gate that could no longer find itself.
PACK_MARKER = Path(PACK_DIRECTORY) / PACK_SELF_MARKER

# Where the pack lives relative to an ancestor directory. Two spellings because the estate
# nests by owner (source/AllegroIt/engineering-standards) and a flatter checkout is plausible.
PACK_LOCATIONS = (
    Path("AllegroIt") / "engineering-standards",
    Path("engineering-standards"),
)

VERSION_PART = re.compile(r"\d+")


def declared_pack_path(repo_root: Path) -> Optional[Path]:
    """`packPath` from .standards.json, for a repo that sits outside the usual layout.

    Read here rather than through CheckConfig on purpose: this is a location, not a rule a
    repo may tune, and putting a filesystem path among the check flags would invite it into
    the weakening table where it does not belong.
    """
    config = repo_root / ".standards.json"
    try:
        raw = json.loads(config.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, ValueError:
        return None
    declared = raw.get("packPath")
    if not isinstance(declared, str) or not declared.strip():
        return None
    path = Path(declared.strip())
    return path if path.is_absolute() else (repo_root / path)


def is_pack(candidate: Path) -> bool:
    return (candidate / PACK_MARKER).is_file()


def find_pack(repo_root: Path) -> Optional[Path]:
    """The pack's root, or None when it is not reachable from here."""
    declared = declared_pack_path(repo_root)
    if declared is not None:
        return declared if is_pack(declared) else None

    for ancestor in [repo_root, *repo_root.parents]:
        for location in PACK_LOCATIONS:
            candidate = ancestor / location
            if is_pack(candidate):
                return candidate
    return None


def version_of(pack_root: Path) -> Optional[str]:
    try:
        text = (pack_root / PACK_MARKER.parts[0] / VERSIONS_MODULE).read_text(encoding="utf-8")
    except OSError:
        return None
    found = PACK_VERSION_PATTERN.search(text)
    return found.group(1) if found else None


def version_key(version: str) -> tuple:
    """A comparable key for `2026.08.27-5`.

    Numeric, not lexicographic: `-10` sorts before `-5` as text, so a tenth release in a day
    would read as older than the fifth and the message would say "behind" while ahead. An
    unparseable version falls back to the raw string, which still compares equal to itself --
    the only property the "differs" case needs.
    """
    parts = VERSION_PART.findall(version)
    return tuple(int(part) for part in parts) if parts else (version,)


@dataclass(frozen=True)
class Report:
    """What to say, and whether it is a finding or an admission that none could be made.

    The two go to different streams because they are different claims. "You are behind" is
    information the check produced; "I could not look" is the check declaring it did not run,
    and collapsing those into one channel is how a skipped gate comes to read as a passed one.
    """

    text: str
    is_warning: bool = False


def report(repo_root: Path) -> Optional[Report]:
    """What to print, or None when there is genuinely nothing to say.

    BOTH versions are read from disk, from the roots they belong to. The first version of this
    imported PACK_VERSION for the repo's side, which silently read whichever copy happened to
    be on sys.path -- so running the pack's own script against a repo compared the pack to
    itself and reported nothing while the repo was six days behind. It would have worked in
    production, because the hook runs the repo's copy, and that is exactly what makes the
    coupling worth removing: a bug that only appears when you test it by hand is a bug nobody
    finds. Reading both sides the same way also drops the import, and with it the
    two-versions-of-one-module problem that made the import awkward in the first place.
    """
    own_version = version_of(repo_root)
    if own_version is None:
        return None  # not an adopted repo at all; nothing here is its business

    pack_root = find_pack(repo_root)
    if pack_root is None:
        declared = declared_pack_path(repo_root)
        # A declared-but-wrong path is a broken setting, not an absent pack, and saying so
        # specifically is the difference between a fixable message and a puzzling one.
        where = (
            f"packPath in .standards.json points at '{declared}', which is not the pack"
            if declared is not None
            else "no engineering-standards directory was found in any parent of this repo"
        )
        return Report(
            is_warning=True,
            text=(
                f"standards: could not check for a newer pack --\n"  # noqa: S608  (a warning message; this module contains no SQL at all)
                f"           {where}.\n"
                f"           The gates themselves ran normally. This repo carries its own copy "
                f"of the\n"
                f"           scanner, hooks and rules ({own_version}), so verification never "
                f"depends on\n"
                f"           the pack being reachable -- only this update check does.\n"
                f"           Set packPath in .standards.json if the pack lives somewhere "
                f"unusual."
            ),
        )

    if pack_root.resolve() == repo_root.resolve():
        return None  # this IS the pack; it does not lag itself

    available = version_of(pack_root)
    if available is None or available == own_version:
        return None
    if version_key(available) <= version_key(own_version):
        return None  # ahead of the pack, or a local edit -- not this rule's business

    return Report(
        text=(
            f"standards: this repo is on pack {own_version}; {available} is available.\n"
            f"           Run /apply-standards here to take it -- that re-runs the gate against\n"
            f"           the new rules, which this push did not.\n"
            f"           (informational -- nothing was changed and the push continues)"
        )
    )


def main(argv: list[str]) -> int:
    found = report(Path(argv[0]).resolve() if argv else Path.cwd())
    if found is not None:
        print(found.text, file=sys.stderr if found.is_warning else sys.stdout)
    return 0  # never blocks, whatever it found


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

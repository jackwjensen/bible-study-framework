#!/usr/bin/env python3
"""Which files belong to the PACK rather than to the repo holding them.

THE DIRECTORY IS THE OWNERSHIP CLAIM. That is the whole reason the pack moved out of
`scripts/` on 2026-09-02: ownership used to be claimed by FILENAME PATTERN inside a
directory the repo also used, and a pattern cannot tell a pack file the pack WITHDREW from
a file the pack NEVER SHIPPED. `sync_pack.py` needed a decidable answer in order to delete
legacy files without deleting the repo's own -- allegro-it-services keeps eleven scripts of
its own in `scripts/`, one of them called by that repo's pre-commit hook.

Removal was the reason, but it is not the only question the boundary answers. A consuming
repo holds ~100 files it did not write, must not edit (the next sync overwrites them), and
cannot fix findings in. Measuring them as that repo's own source is wrong in the same way
deleting its scripts would have been wrong, and it showed: a consumer's scan reported the
pack's ~18 internal exemption markers alongside its own -- burying the report whose entire
job is to make a silenced rule visible -- and its pytest gate collected the pack's ~900
cases beside the repo's, under `-x`, so a pack test could fail a push in a repo whose own
code was fine.

THE PACK ITSELF IS THE EXCEPTION, and getting that backwards would be the worst outcome
here: the pack would stop scanning and testing its own source, silently, and every rule in
the estate is authored in exactly those files. So the question is never "is this file under
the pack's directory" alone -- it is that AND "is this repo something other than the pack".

`dev/` is the discriminator because `sync_pack.py` copies only the top level of the package;
no consuming repo has ever received it. The same marker identifies the pack in
`standards_pack_update.py`, where a consumer resolves its one supplier.

Source of truth: engineering-standards/engineering_standards/standards_pack_identity.py
"""

from __future__ import annotations

from pathlib import Path

# Read off where this module actually sits, never written as a literal. A literal is what
# broke `source_limit_gates` silently during the 2026-09-02 move -- the path still said
# `scripts/` after the package had moved, and a scanner it cannot find yields NO GATE rather
# than a failure, so `verify` reported "no gates apply" and exited 0.
PACK_DIRECTORY = Path(__file__).resolve().parent.name

# Relative to PACK_DIRECTORY. Present in the pack, absent in every repo that adopts it.
PACK_SELF_MARKER = Path("dev") / "pack_manifest.py"


def is_the_pack_itself(repo_root: Path) -> bool:
    """Whether `repo_root` is the pack's own repository rather than a consumer of it."""
    return (repo_root / PACK_DIRECTORY / PACK_SELF_MARKER).is_file()


def is_foreign_pack_file(relative_path: Path, repo_root: Path) -> bool:
    """Whether a repo-relative path is the PACK's file sitting inside a CONSUMING repo.

    Asked per path, so the directory test comes first and settles almost every file without
    touching the filesystem; only a path actually under the pack's directory pays the one
    `is_file()` that `is_the_pack_itself` costs.
    """
    parts = relative_path.parts
    if not parts or parts[0] != PACK_DIRECTORY:
        return False
    return not is_the_pack_itself(repo_root)

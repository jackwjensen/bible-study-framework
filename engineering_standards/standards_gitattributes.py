#!/usr/bin/env python3
"""`gitattributes-eol`: every file a Unix shell reads must check out LF on every PC.

WHY. A CR is not cosmetic to `sh` or `bash`: it is read as part of the token, so a CRLF
script is a syntax error, not a slow one. Whether a checkout gets CRLF is decided by
`core.autocrlf`, which Git for Windows defaults to `true`, which differs per PC, and which
no clone carries. Only `.gitattributes` travels with the repository, and it outranks that
setting -- so it is the one place the answer can be made the same everywhere.

It has failed both ways it can. On 2026-08-27 a CRLF `_python.sh` reached every repo that
pulled the pack, died printing nothing, and so disabled the push gate silently (the pack's
own root `.gitattributes` records it). On 2026-10-06 a fresh clone of ellengaard-dk, on a PC
with `autocrlf=true` and no `.gitattributes`, checked every script out CRLF, and bash died
on `set -o pipefail\\r` inside a Linux container the scripts were bind-mounted into.

WHAT IS CHECKED: every versioned `*.sh`, every file under the pack's `hooks/` directory
(git hooks have no extension for `*.sh` to catch), and every `Dockerfile`, `Dockerfile.*`
and `*.Dockerfile` -- a `RUN` continuation ending in `\\` then CR breaks the same way. Each
must resolve `eol=lf`. `/apply-standards` seeds `BASELINE` into a repo that
has none; a repo that already had one keeps it, and this rule is what says which lines it
still needs -- the seed alone would be a one-off copy nothing re-checks.

GIT RESOLVES THE ATTRIBUTES, never a pattern matcher here: see `eol_attributes`. A tree
that is not in a repository at all has no checkout to convert, so it is quiet; a repository
git cannot be asked about is a limit on the scanner's reach, and is said so on stderr.

NO EXEMPTION, deliberately, and nothing registered in standards_exemption_scope.py: a
CRLF shell script is never correct, and the fix is one line in `.gitattributes`. Not
baselineable either, for the same reason as the gitignore family -- the write-baseline
pass does not collect it.

REPO-LEVEL, so it runs on a whole-tree scan only, for the gitignore reason.

Source of truth: engineering-standards/engineering_standards/standards_gitattributes.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

from standards_core import Violation
from standards_git import eol_attributes, versioned_files
from standards_pack_identity import PACK_DIRECTORY

GITATTRIBUTES_RULE = "gitattributes-eol"

HOOKS_PREFIX = f"{PACK_DIRECTORY}/hooks/"

# The pack's current LF baseline, at the same repo-relative path in the pack and in every repo
# that adopts it (pack_manifest ships it there on each sync). A finding names it, so it has to
# be a path the reader's own checkout holds -- the pack root's old `templates/` never shipped.
BASELINE = f"{PACK_DIRECTORY}/templates/.gitattributes"

# The same file, resolved from this module's own location rather than a repo root, so tests
# read it wherever the package sits.
BASELINE_FILE = Path(__file__).resolve().parent / "templates" / ".gitattributes"


def suggested_line(relative: str) -> str | None:
    """The `.gitattributes` line that covers this path, or None if it is not a shell file.

    Doubles as the classifier, so what is checked and what the finding tells you to add can
    never disagree. Hooks first: `_python.sh` is under both, and the hooks line is the one
    that also covers its extensionless siblings.
    """
    name = relative.rsplit("/", 1)[-1]
    if relative.startswith(HOOKS_PREFIX):
        return f"{HOOKS_PREFIX}* text eol=lf"
    if name.endswith(".sh"):
        return "*.sh text eol=lf"
    if name == "Dockerfile":
        return "Dockerfile text eol=lf"
    if name.startswith("Dockerfile."):
        return "Dockerfile.* text eol=lf"
    if name.endswith(".Dockerfile"):
        return "*.Dockerfile text eol=lf"
    return None


def _in_a_repository(root: Path) -> bool:
    """Whether a `.git` sits at or above `root` -- asked only once git has failed to answer.

    Separates "git is missing or broken here", which must be said out loud, from "this tree
    was never cloned", where no checkout exists for line endings to be converted in.
    """
    return any((directory / ".git").exists() for directory in (root, *root.parents))


def _cannot_ask(root: Path) -> None:
    if _in_a_repository(root):
        print(
            f"warning: git could not be asked about {root} -- {GITATTRIBUTES_RULE} could not run for this repo",
            file=sys.stderr,
        )


def check_gitattributes(repo_root: Path) -> Iterable[Violation]:
    """One finding per shell-read file that does not resolve `eol=lf`.

    Reported against `.gitattributes`, the file the fix goes in -- also when it does not
    exist, as gitignore-missing does -- with the offending path quoted first so each finding
    is its own key.
    """
    versioned = versioned_files(repo_root)
    if versioned is None:
        _cannot_ask(repo_root)
        return

    candidates = [relative for relative in versioned if suggested_line(relative)]
    resolved = eol_attributes(repo_root, candidates)
    if resolved is None:
        _cannot_ask(repo_root)
        return

    for relative in candidates:
        eol = resolved.get(relative, "unspecified")
        if eol == "lf":
            continue
        yield Violation(
            path=repo_root / ".gitattributes",
            line=1,
            rule=GITATTRIBUTES_RULE,
            message=(
                f"'{relative}' is read by a Unix shell but resolves eol={eol}, so a checkout "
                f"can hold CRLF (core.autocrlf=true does it) and the shell fails on the CR. Add "
                f"'{suggested_line(relative)}' to .gitattributes (the pack's baseline is "
                f"{BASELINE})."
            ),
        )

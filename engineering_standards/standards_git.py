#!/usr/bin/env python3
"""Which files a commit is actually about, read from the git index.

Split out rather than inlined into the driver for the same reason the other modules are
split: this changes when the question "what is being committed?" changes, which is a
different subject from what a violation is or which files are in scope.

WHY SCOPED SCANNING EXISTS. Checking the whole repository on every commit sounds strictly
safer and is not. A repo that starts red cannot be committed to at all, so a large body of
work is forced to sit uncommitted -- and uncommitted work is one mistake away from gone.
That is not hypothetical: roughly 620 lint fixes in allegro-it-services lived only in the
working tree for a day, because the gate required whole-repo green, and a `git checkout --`
over `git diff --name-only` destroyed every one of them. Nothing was staged, so nothing was
recoverable. A gate that checks what you are committing lets the clean half land today.

Wider verification still happens: at push (the per-file rules on what changed since trunk, the
repository rules over the whole repo, build and tests), and in CI, which checks the whole tree
on every branch push. See standards_gates.py.

WHAT THIS READS, AND THE ONE CAVEAT. `staged_files` names the paths in the index; the
scanner then reads those files from the WORKING TREE, not from the index. The two agree
whenever whole files were staged, which is how this estate works in practice. They diverge
under hunk-level staging (`git add -p`), where the working tree holds edits the commit does
not. Rather than let that pass silently -- a gate reporting on bytes that are not being
committed is exactly the "looks like it passed" failure this pack exists to prevent --
`partially_staged_files` names the affected paths so the caller can say so out loud.

Materialising the index to a temp tree would remove the caveat and was rejected: the
`env-example-compose` rule asks whether `docker-compose.yml` exists at the repo root, and a
tree containing only the staged files would answer that wrongly. Trading a loud, rare,
named caveat for a silent, systematic misfire is the wrong direction.

It is also the scanner's one door to git for the two tree questions only git can answer
exactly -- which files the repository ships (`versioned_files`), and what its
`.gitattributes` resolve for them (`eol_attributes`, asked by `gitattributes-eol`).

Source of truth: engineering-standards/engineering_standards/standards_git.py
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

GIT_TIMEOUT_SECONDS = 30

# Added / Copied / Modified / Renamed. Deletions are excluded deliberately: a file being
# removed has no content left to check, and every path lookup for one would fail.
STAGED_DIFF_FILTER = "ACMR"


def _run_git(
    root: Path,
    arguments: list[str],
    stdin: str | None = None,
    environment: dict[str, str] | None = None,
) -> str | None:
    """Stdout of a git command, or None if git could not answer.

    None and "" mean different things and the callers depend on the difference: "" is a
    real answer (nothing staged), None is "no git here" (not a repository, git not
    installed, a broken index). Collapsing them would turn "cannot tell" into "clean".
    """
    try:
        completed = subprocess.run(  # noqa: S603  (fixed argv, shell=False)
            ["git", "-C", str(root), *arguments],  # noqa: S607  (git must come from PATH; an absolute path would be machine-specific)
            input=stdin,
            env={**os.environ, **environment} if environment else None,
            capture_output=True,
            text=True,
            # git speaks UTF-8; the locale codec would mangle or reject a path or commit
            # message with ae/oe/aa in it, and this returns None on failure, so the damage
            # would be silent -- "no git here", from a repository that is right there.
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None

    if completed.returncode != 0:
        return None
    return completed.stdout


def trunk_ref(root: Path) -> str | None:
    """The ref a branch should be measured against, or None if there is not one yet.

    Used by the pay-down gate to ask "what did this branch change?". Remote-tracking refs
    first, because the question is what the branch adds relative to what is PUBLISHED: a
    stale local `master` would let a branch appear to have paid down debt that somebody
    else already paid, and appear to owe nothing for a file it did change.

    None is a real answer and callers must read it as "no pay-down check", never as a
    failure -- a repository before its first push has no trunk, and no recorded debt to
    move either.
    """
    for candidate in ("origin/master", "origin/main"):
        if _run_git(root, ["rev-parse", "--verify", "--quiet", candidate]) is not None:
            return candidate

    upstream = _run_git(root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    return upstream.strip() if upstream and upstream.strip() else None


def _paths_from_nul_list(root: Path, output: str) -> list[Path]:
    """Absolute paths from a `-z` name list.

    `-z` rather than newline separation because git otherwise quotes and escapes any path
    holding a space or a non-ASCII character -- and this estate has both. A quoted path
    does not resolve, so the file would be silently dropped from the scan.
    """
    return [root / name for name in output.split("\0") if name]


def repository_root(start: Path) -> Path | None:
    """The git root containing `start`, or None if it is not in a repository."""
    output = _run_git(start, ["rev-parse", "--show-toplevel"])
    if output is None:
        return None

    root = output.strip()
    return Path(root) if root else None


def ignored_entries(root: Path) -> set[Path]:
    """Absolute paths that are present in the tree, ignored by git, and not tracked.

    A directory whose whole content is ignored is ONE entry, not one per file -- that is
    `--directory`, and `standards_tree.walk_repository` prunes such an entry without
    descending. A file that is ignored in a directory that also holds tracked files is its
    own entry.

    WHY THE SCANNER NEEDS THIS. A whole-tree run walks the filesystem, and the filesystem
    holds files no commit will ever contain -- a developer's `.env` being the one that
    matters. `committed-credential` matches `.env` by NAME and reports it as "being
    committed", which is true of a staged one and false of an ignored one; without this,
    the rule read presence in the working tree as presence in git. The two are the same
    thing only on a CI runner, whose fresh checkout has no `.env` -- so the false positive
    fired on every developer machine and never once in CI, which is why it survived. The
    second case, 2026-11-03: another session's git worktree nested under `.claude/worktrees/`
    in jacks_corner, whose untested module blocked a push of unrelated content.

    `--others --ignored --exclude-standard` is the exact question, and the exactness is
    the point: `--others` means UNTRACKED, so a `.env` somebody has staged or committed is
    not in this set and stays flagged. The rule keeps the case it exists for and loses only
    the case it was never about. (`--directory` cannot swallow a tracked file either: git
    collapses a directory only when nothing inside it is tracked.)

    An empty set on failure, deliberately -- and this is the one place in this module where
    "cannot tell" is NOT distinguished from "nothing". Everywhere else that would be a lie;
    here it means an unreadable repository scans every file it can see, so a scanner that
    cannot ask git falls back to reporting too much rather than too little. For a rule about
    credentials, the noisy direction is the safe one.
    """
    output = _run_git(root, ["ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"])
    if output is None:
        return set()
    # A directory entry arrives as "dir/"; the trailing slash is dropped by Path itself.
    return set(_paths_from_nul_list(root, output))


def staged_files(root: Path) -> list[Path] | None:
    """Absolute paths of the files this commit would add or change.

    None means git could not be asked at all; the caller decides what that implies. An
    empty list is a genuine answer: nothing is staged.
    """
    output = _run_git(
        root,
        ["diff", "--cached", "--name-only", f"--diff-filter={STAGED_DIFF_FILTER}", "-z"],
    )
    if output is None:
        return None
    return _paths_from_nul_list(root, output)


def files_changed_since(root: Path, ref: str) -> list[Path] | None:
    """Absolute paths this branch changed relative to `ref`, as the working tree holds them.

    The pre-push scope: everything between `git merge-base ref HEAD` and the working tree
    (committed, staged or merely edited), plus untracked files git does not ignore -- a new
    file nobody has added yet is still part of what the branch is about. The merge-base, not
    `ref` itself, so a trunk that moved on after the branch forked does not make the branch
    look as though it reverted that work. Deletions are excluded for the reason
    STAGED_DIFF_FILTER gives; a rename is listed at its new path.

    None when git cannot answer (not a repository, `ref` unknown, no common ancestor). The
    caller must read that as "scan everything", never as "nothing changed": an empty list is a
    genuine answer, and collapsing the two would turn an unreadable repository into a pass.
    """
    base = _run_git(root, ["merge-base", ref, "HEAD"])
    if base is None or not base.strip():
        return None

    changed = _run_git(root, ["diff", "--name-only", "--diff-filter=d", "-z", base.strip()])
    untracked = _run_git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    if changed is None or untracked is None:
        return None
    return list(dict.fromkeys(_paths_from_nul_list(root, changed + untracked)))


def unstaged_files(root: Path) -> list[Path]:
    """Absolute paths with working-tree edits that are not in the index."""
    output = _run_git(root, ["diff", "--name-only", "-z"])
    if output is None:
        return []
    return _paths_from_nul_list(root, output)


def partially_staged_files(root: Path) -> list[Path]:
    """Staged paths that also carry unstaged edits.

    These are the only files where scanning the working tree can disagree with what is
    actually being committed. Named so the caller can report them rather than quietly
    checking the wrong bytes.
    """
    staged = staged_files(root)
    if not staged:
        return []

    unstaged = set(unstaged_files(root))
    return sorted(path for path in staged if path in unstaged)


def versioned_files(root: Path) -> list[str] | None:
    """Root-relative paths git would ship: tracked, plus untracked files it does not ignore.

    None when git cannot be asked. `-z` for the reason `_paths_from_nul_list` gives, and
    de-duplicated because an unmerged path is listed once per conflict stage.
    """
    output = _run_git(root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    if output is None:
        return None
    return list(dict.fromkeys(name for name in output.split("\0") if name))


# Attribute sources that live on ONE machine and travel with no clone. A user's global
# attributes file (`core.attributesFile`) and the installation's system file can make a path
# resolve `eol=lf` on the PC that asks while a fresh clone anywhere else gets CRLF -- which is
# the exact failure the asking rule exists for, so the answer must come from the repository
# alone. `$GIT_DIR/info/attributes` is also local, and git offers no switch to skip it.
_REPOSITORY_ONLY_ATTRIBUTES = ["-c", f"core.attributesFile={os.devnull}"]
_NO_SYSTEM_ATTRIBUTES = {"GIT_ATTR_NOSYSTEM": "1"}


def eol_attributes(root: Path, relative_paths: list[str]) -> dict[str, str] | None:
    """The `eol` attribute git resolves for each path: "lf", "crlf" or "unspecified".

    Git itself does the resolving, deliberately -- pattern syntax, precedence between
    lines, and every nested `.gitattributes` are its rules, and a hand-rolled matcher would
    be a second implementation of them that agrees until the day it does not. Paths go in
    on stdin so a large tree cannot overrun a command line. None when git cannot be asked.
    """
    if not relative_paths:
        return {}
    output = _run_git(
        root,
        [*_REPOSITORY_ONLY_ATTRIBUTES, "check-attr", "-z", "--stdin", "eol"],
        stdin="".join(f"{path}\0" for path in relative_paths),
        environment=_NO_SYSTEM_ATTRIBUTES,
    )
    if output is None:
        return None
    # `-z` output is flat triples: path NUL attribute NUL value NUL.
    fields = output.split("\0")
    return {fields[index]: fields[index + 2] for index in range(0, len(fields) - 2, 3)}

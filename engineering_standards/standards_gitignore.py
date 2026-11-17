#!/usr/bin/env python3
"""`gitignore-missing`: the build output and secrets a repository must never version.

Split out of standards_layout.py on 2026-09-03, when adopting `ruff format` expanded that
file past the pack's own 500-line limit. The seam was already drawn -- the file carried a
`# ---- gitignore ----` divider and two rules that share nothing: `generic-filename` reads
ONE path and asks whether its name identifies anything, while this reads the WHOLE tree and
asks what it contains. Different subject, different scope, different reason to change.

REPO-LEVEL, so it runs on a whole-tree scan only -- never at commit. Blocking every commit
in a repo whose .gitignore is short would block the commit that fixes it, which is the same
failure the staged scope exists to prevent.

Source of truth: engineering-standards/engineering_standards/standards_gitignore.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import CheckConfig, Violation, warn_unreadable

# `[Bb]in/` -- the casing form Visual Studio's own template writes. Collapsed to its first
# letter so it compares equal to a plain `bin/`. Only two-character classes are handled:
# `*.py[cod]` is a real pattern too, and is matched literally as an accepted spelling
# instead, because collapsing it would produce `*.pyc` and silently change its meaning.
CASING_CLASS = re.compile(r"\[([A-Za-z])[A-Za-z]\]")


def normalised_ignore_patterns(lines: list[str]) -> set[str]:
    """Every pattern the file actually ignores, in a form that can be compared.

    Anchoring (`/bin`), directory suffixes (`bin/`), recursion (`**/bin`) and VS-style
    casing classes (`[Bb]in`) are five spellings of one pattern, and a literal comparison
    would report four of them as missing -- against the most standard .NET .gitignore
    there is.

    A NEGATION IS DROPPED RATHER THAN NORMALISED. `!bin/keep.dll` un-ignores something;
    counting it as coverage would let a file that explicitly re-admits build output pass
    the rule that exists to keep build output out.
    """
    patterns: set[str] = set()

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue

        line = CASING_CLASS.sub(lambda match: match.group(1), line)
        line = line.removeprefix("**/").strip("/")
        if line:
            patterns.add(line.casefold())

    return patterns


class Requirement:
    """One thing a repo must ignore, and every spelling that satisfies it.

    `accepted` holds single patterns, any one of which is enough; `together` holds sets of
    patterns that only count when ALL of them are present (`.env` + `.env.*`).
    """

    def __init__(
        self,
        label: str,
        accepted: Iterable[str],
        why: str,
        together: Iterable[Iterable[str]] = (),
    ) -> None:
        self.label = label
        self.accepted = frozenset(form.casefold() for form in accepted)
        self.together = tuple(frozenset(form.casefold() for form in group) for group in together)
        self.why = why

    def is_satisfied_by(self, patterns: set[str]) -> bool:
        return bool(self.accepted & patterns) or any(group <= patterns for group in self.together)


# Applies to every repo, whatever it is written in. `.env` is first because it is the only
# entry here whose absence is a security defect rather than a hygiene one.
#
# EVERY COPY, NOT JUST `.env` (Jack, 2026-10-10). A bare `.env` line used to pass, and a
# `.env.bak-staging` -- a copy made on a server before an edit, holding the same passwords --
# then sat untracked but NOT ignored in a production checkout, one `git add -A` from being
# pushed. A copy of the file is the same credential. So the requirement is `.env*` (or
# `.env` + `.env.*`), and an example file is admitted ON PURPOSE with its own negation,
# `!.env.example`: a negation is never counted as coverage (normalised_ignore_patterns), so
# re-admitting the example cannot weaken the rule, and nothing else gets in by accident.
UNIVERSAL_REQUIREMENTS = (
    Requirement(
        ".env*",
        (".env*",),
        "every copy of .env (.env.local, .env.bak, .env.staging) is as live a credential as "
        ".env itself; ignore '.env*' and re-admit an example file on purpose with "
        "'!.env.example'",
        together=((".env", ".env.*"),),
    ),
)

# Visual Studio's per-machine state (Jack, 2026-11-01). `.vs/` holds the solution user options,
# the IntelliSense database and the debugger's breakpoints -- hundreds of megabytes that change
# on every open, differ per developer, and once versioned conflict on every pull. VS writes it
# into ANY folder it opens, not only a solution, which is why check_gitignore also requires it
# by presence rather than by stack alone.
VISUAL_STUDIO_STATE = Requirement(
    ".vs/",
    (".vs",),
    "Visual Studio's per-machine state (IntelliSense cache, user options, breakpoints)",
)

# Keyed on artifacts the repo actually contains, so a Python repo is never told to ignore
# `obj/`. See `repository_artifacts` for why this asks a different question from
# standards_gates.build_gates.
STACK_REQUIREMENTS = {
    "dotnet": (
        Requirement("bin/", ("bin",), "compiled output"),
        Requirement("obj/", ("obj",), "intermediate build output"),
        VISUAL_STUDIO_STATE,
        # `App.csproj.user` carries one developer's debug profile and start-up arguments.
        Requirement("*.user", ("*.user",), "one developer's project settings (App.csproj.user)"),
    ),
    "node": (Requirement("node_modules/", ("node_modules",), "installed dependencies"),),
    "python": (
        Requirement(
            "__pycache__/",
            ("__pycache__", "*.pyc", "*.py[cod]"),
            "compiled bytecode",
        ),
        Requirement(".venv/", (".venv", "venv", ".venv/", "env/"), "the virtualenv"),
    ),
}


def repository_artifacts(repo_root: Path) -> set[str]:
    """Which build systems will produce output in this tree.

    DELIBERATELY NOT `standards_gates.build_gates`, though the two overlap and the
    duplication is worth defending rather than hiding. They answer different questions:
    `build_gates` asks "is there something here I can run?" -- it needs a `.sln` to build,
    a `package.json` script to invoke, a declared `[tool.ruff]`. This asks "will this tree
    grow generated files?", which a bare `.csproj` with no solution answers just as well.

    Importing the gate builder here would also point the dependency the wrong way: the
    scanner is something `standards_gates` RUNS, and a module that its own runner imports
    back is a cycle waiting for the first shared helper.

    Globbed shallowly rather than with rglob, which descends into node_modules.
    """
    found: set[str] = set()

    for depth in ("*", "*/*", "*/*/*"):
        if any(repo_root.glob(f"{depth}.csproj")) or any(repo_root.glob(f"{depth}.sln")):
            found.add("dotnet")
        if any(repo_root.glob(f"{depth}/package.json")) or (repo_root / "package.json").is_file():
            found.add("node")
        if (
            any(repo_root.glob(f"{depth}/pyproject.toml"))
            or (repo_root / "pyproject.toml").is_file()
            or (repo_root / "requirements.txt").is_file()
            or (repo_root / "manage.py").is_file()
        ):
            found.add("python")

    return found


def check_gitignore(repo_root: Path, config: CheckConfig) -> Iterable[Violation]:
    """Flag generated output and secrets that version control would otherwise take.

    REPO-LEVEL, and therefore run only on a whole-tree scan -- never at the commit stage.
    A commit checks the commit, not the repository (see standards_git.py): a repo-level
    finding at commit time blocks every commit including the one that would fix it, which
    is how roughly 620 uncommitted lint fixes were destroyed once already.

    The MISSING file is the case worth having. A file-driven scanner cannot report a file
    that is not there, so a repo with no `.gitignore` at all -- the worst case, and the one
    where every requirement is unmet -- would have been the one case that passed silently.
    """
    if not config.check_gitignore:
        return

    gitignore = repo_root / ".gitignore"
    required = list(UNIVERSAL_REQUIREMENTS)
    for stack in sorted(repository_artifacts(repo_root)):
        required.extend(STACK_REQUIREMENTS[stack])

    # A bundle directory is required to be ignored ONLY when one actually exists in the tree.
    # `dist/` is not a fixed per-stack artifact the way `bin/` or `node_modules/` are -- a
    # library never produces one -- so demanding it unconditionally would be a false positive
    # on every repo that does not build a bundle. Conditioning on its presence catches the real
    # defect precisely: an external audit found a frontend with a 33 MB `dist/` committed to
    # git, churning a fresh diff on every build and carrying a second copy of the leaked keys.
    # The scanner already refuses to READ files under `/dist/`; this is about the directory
    # being VERSIONED at all, which the .gitignore is what decides.
    if (repo_root / "dist").is_dir():
        required.append(
            Requirement(
                "dist/",
                ("dist",),
                "a committed bundle directory churns a fresh diff on every build",
            )
        )

    # The same presence logic for `.vs/`: a Python or PHP repo opened once with Visual Studio's
    # "Open Folder" gets one too, and nothing in its stack would have asked for the line.
    if (repo_root / ".vs").is_dir() and VISUAL_STUDIO_STATE not in required:
        required.append(VISUAL_STUDIO_STATE)

    if not gitignore.is_file():
        yield Violation(
            path=gitignore,
            line=1,
            rule="gitignore-missing",
            message=(
                f"'.gitignore' does not exist. This repository needs one: "
                f"{', '.join(requirement.label for requirement in required)}."
            ),
        )
        return

    try:
        lines = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        # Returning here means the rule finds nothing -- and "found nothing" is what a
        # passing repo looks like, so the skip has to say so.
        warn_unreadable(gitignore, error, "gitignore-missing could not run for this repo")
        return

    patterns = normalised_ignore_patterns(lines)

    for requirement in required:
        if requirement.is_satisfied_by(patterns):
            continue
        yield Violation(
            path=gitignore,
            line=1,
            rule="gitignore-build-output",
            message=(
                f"'{requirement.label}' is not ignored -- {requirement.why}. "
                f"Add it, or set checkGitignore false in .standards.json if this repo "
                f"genuinely tracks it."
            ),
        )

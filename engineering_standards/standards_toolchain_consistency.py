#!/usr/bin/env python3
"""One engine: every environment in a repo runs the same version of a given toolchain.

Rules produced here: python-consistency, node-consistency, php-consistency, dotnet-consistency
-- one per toolchain, from one implementation. The rule NAMES stay separate because exemptions
are per-rule and a repo silencing Node's deliberate test matrix must not thereby silence
Python's.

WHY THIS IS NOT A FLOOR RULE. A floor asks "is this declaration at or above the minimum?" --
a question each file answers alone. This asks "do they agree with each other?", and NO SINGLE
FILE CAN ANSWER IT. A repo whose CI installs 3.14 and whose container runs 3.15 passes the
floor on every line while testing code on an interpreter it never ships: green suite, coherent
files, nothing to look at. That is the failure where a bug tests as correct.

WHY IT WAS GENERALISED RATHER THAN COPIED. The Python version shipped first, and an audit on
2026-08-25 found the identical gap in `node-support` -- `.nvmrc` 24 beside `FROM node:26` is
clean today -- while that rule's own message promised the opposite: "a repo that moves only one
builds on a different Node than it develops on, which is the failure this floor exists to make
impossible". It does not make it impossible; it makes being BELOW the floor impossible. Being
SPLIT above it was silent. Four near-identical modules would have drifted the same way, one
overclaim at a time, so the comparison lives here once and the toolchains supply only readers.

WHY IT IS REPO-LEVEL AND THEREFORE PUSH-ONLY. Like `check_gitignore`, this runs only on a
whole-tree scan. A repo-level finding at commit time blocks every commit including the one that
would fix it -- the pack has destroyed work that way once already (see standards_git.py). Push
and CI both scan the whole tree, so nothing reaches a remote unchecked.

TWO COMPARISONS, ONE FINDING. Sites split by whether they name a version or a range:

  environments  a concrete version -- .nvmrc, CI, FROM, a TFM, a lint target. Must AGREE.
  constraints   a RANGE -- requires-python, Composer require.php. Never required to equal
                anything; must merely ADMIT what the environments run.

The constraint row is what an equality check gets wrong. `requires-python = ">=3.14"` beside a
3.15 container is CORRECT -- it is a minimum, exactly as the estate policy asks -- and must
stay silent. What fails is a ceiling the runtime breaches.

They produce ONE finding rather than N, because they have one cause and one fix, and N findings
for one cause is how a rule becomes something people exempt wholesale.

Source of truth: engineering-standards/engineering_standards/standards_toolchain_consistency.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, NamedTuple, Optional

from standards_core import CheckConfig, Violation, warn_unreadable
from standards_declarations import Toolchain, Version
from standards_exemptions import line_exemption_reason


class Site(NamedTuple):
    """One declaration, located: which file, which line, what kind, what it says."""

    path: Path
    line: int
    label: str
    versions: list[Version]
    constraint: Optional[str]

    def where(self, repo_root: Path) -> str:
        return f"{self.path.relative_to(repo_root).as_posix()}:{self.line}"


def collect_sites(repo_root: Path, paths: list[Path], toolchain: Toolchain) -> list[Site]:
    """Every declaration this toolchain makes anywhere in the repo, located.

    TAKES THE SCANNER'S OWN PATH LIST rather than walking the tree, which is a deliberate
    difference from `check_gitignore`. The per-file rules and this one must examine the same
    set: a second walk would need its own copy of the exclusion list, and the day the two
    drifted this rule would be comparing a `.venv` dependency's pyproject.toml against the
    application's -- reporting an inconsistency nobody can fix.
    """
    sites: list[Site] = []
    for path in sorted(paths):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as error:
            # A site that drops out of the comparison makes the repo look MORE consistent
            # than it is -- the quiet direction, and the one worth naming.
            warn_unreadable(path, error, "this declaration is missing from the comparison")
            continue

        for declaration in toolchain.readers(path, lines):
            # A declaration naming nothing comparable (`node:latest`) is the FLOOR rule's
            # finding, not this one's. Reporting it in both would make one problem look like
            # two, and the fix is the same edit either way.
            if not declaration.versions:
                continue
            if line_exemption_reason(lines, declaration.index, toolchain.rule):
                continue
            sites.append(
                Site(
                    path=path,
                    line=declaration.index + 1,
                    label=declaration.label,
                    versions=sorted(declaration.versions),
                    constraint=declaration.constraint,
                )
            )
    return sites


def agreed_version(sites: list[Site]) -> Optional[Version]:
    """The single version the repo's environments run, or None when they disagree.

    A site naming SEVERAL versions is a deliberate test matrix (or a multi-targeted project),
    not a contradiction, so it gets no vote on what the repo runs -- it is checked separately
    for whether it COVERS the agreed version. Only single-version sites decide, which is what
    makes `python-version: [3.14, 3.15]` beside a 3.14 container clean rather than a finding.
    """
    decided = {site.versions[0] for site in sites if len(site.versions) == 1}
    return decided.pop() if len(decided) == 1 else None


def describe(sites: list[Site], toolchain: Toolchain, repo_root: Path) -> str:
    """The full picture, one clause per site, so the finding is actionable from its text alone.

    Deliberately free of single quotes: `violation_key` in check-source-limits.py derives a
    baseline identity from the first quoted token in a message, so a message quoting a version
    would change identity whenever a version changed -- resurrecting a grandfathered finding
    after an unrelated edit.
    """
    return "; ".join(
        f"{site.where(repo_root)} ({site.label}) says "
        + (site.constraint or " / ".join(toolchain.spell(v) for v in site.versions))
        for site in sites
    )


def _disagreeing_versions(sites: list, environments: list, toolchain: Toolchain, picture: str) -> Iterable[Violation]:
    """No single agreed version: the declarations contradict each other.

    Split out so `check_consistency` stays under the return limit -- it reports three
    distinct disagreements and each one ended in its own `return`.
    """
    declared = sorted({v for site in environments if len(site.versions) == 1 for v in site.versions})
    if len(declared) < 2:
        return
    anchor = sites[0]
    yield Violation(
        path=anchor.path,
        line=anchor.line,
        rule=toolchain.rule,
        message=(
            f"this repo declares more than one {toolchain.name} version -- "
            f"{', '.join(toolchain.spell(v) for v in declared)}. {picture}. "
            f"The environments have to match: code that passes its tests on one "
            f"{toolchain.noun} can fail on another, so a repo that tests on one and ships "
            f"on another is green right up until production. Pick one version and move "
            f"every declaration to it. A deliberate multi-version matrix is the one "
            f"legitimate exception -- exempt that line with a written reason."
        ),
    )


def check_consistency(
    repo_root: Path, paths: list[Path], config: CheckConfig, toolchain: Toolchain
) -> Iterable[Violation]:
    """Flag a repo whose declarations for one toolchain do not all name the same version.

    ONE finding, anchored on the first declaring file in sorted order and listing every site.
    Anchoring is unavoidable -- a Violation needs a path and a line -- and the alternatives are
    worse: reporting on every site turns one disagreement into N findings, and picking the
    "wrong" site requires knowing which version is correct, which is exactly the judgment the
    scanner cannot make and the human can.
    """
    if not config.check_toolchain_consistency:
        return

    sites = collect_sites(repo_root, paths, toolchain)
    environments = [site for site in sites if site.constraint is None]
    if len(environments) < 2 and not any(site.constraint for site in sites):
        return

    agreed = agreed_version(environments)
    picture = describe(sites, toolchain, repo_root)

    if agreed is None:
        yield from _disagreeing_versions(sites, environments, toolchain, picture)
        return

    uncovered = [site for site in environments if len(site.versions) > 1 and agreed not in site.versions]
    if uncovered:
        site = uncovered[0]
        yield Violation(
            path=site.path,
            line=site.line,
            rule=toolchain.rule,
            message=(
                f"this repo runs {toolchain.name} {toolchain.spell(agreed)}, but the matrix at "
                f"{site.where(repo_root)} does not cover it -- it lists "
                f"{' / '.join(toolchain.spell(v) for v in site.versions)}. A matrix that omits "
                f"the shipped version tests everything except what production runs. Add "
                f"{toolchain.spell(agreed)} to it, or move the runtime onto a version it covers."
            ),
        )
        return

    if toolchain.admits is None:
        return

    for site in sites:
        if site.constraint is None or toolchain.admits(site.constraint, agreed):
            continue
        yield Violation(
            path=site.path,
            line=site.line,
            rule=toolchain.rule,
            message=(
                f"this repo runs {toolchain.name} {toolchain.spell(agreed)}, which "
                f"`{site.constraint}` does not admit. {picture}. The package metadata and the "
                f"{toolchain.noun} that actually runs the code are describing different "
                f"projects. Widen the constraint to a minimum that includes "
                f"{toolchain.spell(agreed)}, or move the runtime."
            ),
        )
        return

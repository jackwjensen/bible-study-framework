#!/usr/bin/env python3
"""Which rules are about the REPOSITORY rather than about one file.

Split out of check-source-limits.py on 2026-09-07, when wiring `const-duplicated-literal` --
the thirteenth repo-level rule -- put that file at 505 of its own 500 lines. The pack's CLAUDE.md
names the answer for exactly this case: give the new concern its own module rather than trim a
comment back under the limit. `standards_gates.py` was split the same way and for the same
reason while the gate feature was being written.

THE SEAM IS THE REASON TO CHANGE. This module changes when a repo-level rule is ADDED; the
driver changes when walking, baselining, reporting or the CLI does. They had been one file
purely because the assembly had nowhere else to live, and the assembly is the half that grows:
it has gained a rule roughly every fortnight since the docs family landed, and each one cost
the driver an import at the top and a call in the middle.

A REPO-LEVEL RULE IS ONE OF TWO KINDS, and the comments below keep saying which because the
distinction decides what a new rule needs. Some READ THE REPOSITORY'S SHAPE -- gitignore,
sensitive files by name, a committed schema dump -- and take only the root, because the thing
they look at is not in any file the per-file walk reaches. The others COMPARE FILES -- the
toolchain-consistency family, and now the duplicated-constant rule -- and take `paths`, so they
examine exactly the set the per-file rules did rather than a second, differently-scoped tree.

Source of truth: engineering-standards/engineering_standards/standards_repository.py
"""

from __future__ import annotations

from pathlib import Path

from standards_assets import check_unversioned_assets
from standards_checks import CheckConfig, Violation
from standards_constants import check_duplicated_constants
from standards_coverage import check_coverage
from standards_disclosure import check_committed_sql_dump, check_sensitive_files
from standards_docs import check_documentation
from standards_gitattributes import check_gitattributes
from standards_gitignore import check_gitignore
from standards_jobs import check_job_result_storage
from standards_pack_debt import check_pack_debt
from standards_registry import TOOLCHAINS
from standards_rules import check_rules
from standards_tests import check_tests_cover_modules
from standards_toolchain_consistency import check_consistency
from standards_userdocs import check_userdocs


def repository_violations(root: Path, config: CheckConfig, paths: list[Path]) -> list[Violation]:
    """Every rule about the REPOSITORY rather than about one file.

    Whole-tree scans only -- the caller decides that. Extracted from `main` on 2026-09-02,
    which had reached cyclomatic complexity 19 against the pack's own limit of 10 by
    holding argument validation, scope resolution, twelve repo-level rules and the whole
    report in one function. Moved out of the driver entirely on 2026-09-07 -- see the module
    docstring.
    """
    violations: list[Violation] = []
    violations += list(check_gitignore(root, config))
    # Its sibling about what a CHECKOUT does to the tree rather than what the tree holds:
    # whether every file a Unix shell reads resolves eol=lf. Reads the shape, takes the root.
    violations += list(check_gitattributes(root))
    # Whether a background job's outcome is stored, and whether it names the job.
    violations += list(check_job_result_storage(root, config))
    # Whole-tree for the same reason, and a silent no-op in every repo but the pack:
    # the supplier does not get the ratchet it grants its consumers.
    violations += list(check_pack_debt(root))
    # Repo-level for the same reason as gitignore: a checked-in schema dump is a property of
    # the tree, not of any one file the per-file walk would reach (a `.sql` is out of source
    # scope). Whole-tree only, never at commit, for the gitignore reason.
    violations += list(check_committed_sql_dump(root, config))
    # The sensitive-file-by-name family (wp-config, a committed database, a certificate, a
    # credential store) -- also repo-level, because most of these suffixes are out of source
    # scope and their leak is the presence, not the content.
    violations += list(check_sensitive_files(root, config))
    # A page linking a stylesheet or script at an address that never changes. Repo-level for
    # the sql-dump reason: `.html` is out of source scope, and adding it there would put markup
    # under the length and naming rules. See standards_assets.py.
    violations += list(check_unversioned_assets(root, config))
    violations += list(check_documentation(root, config))
    violations += list(check_coverage(root, config))
    violations += list(check_userdocs(root, config))
    # The testing dimension, repo-level for the same reason as the docs one: the answer
    # lives in a different file from the subject, so a staged subset cannot see the
    # suite. It also means the commit that ADDS a test is never the commit this blocks --
    # a property of the scope, not an escape, since the push still refuses until it exists.
    violations += list(check_tests_cover_modules(root, config))
    # The judgment layer's own budget. Repo-level for the gitignore reason AND the
    # consistency reason at once: the scope question is per-file, but the budget is a
    # property of the set, and no per-file rule can ask what the set costs together.
    violations += list(check_rules(root, config))
    # Repo-level for the COMPARE-files reason, beside the consistency family below rather
    # than the shape-reading family above: a constant declared in one file and duplicated in
    # another is invisible from either one. Given `paths` for the same reason they are.
    violations += list(check_duplicated_constants(root, paths, config))
    # Repo-level for a different reason than the four above: not because they read the
    # repository's shape, but because they COMPARE files. Whether CI and the Dockerfile
    # agree on a version is invisible from either one, so no per-file rule can ask it.
    # Given `paths` rather than the root so they examine exactly the set the per-file rules
    # did -- see collect_sites.
    for toolchain in TOOLCHAINS:
        violations += list(check_consistency(root, paths, config, toolchain))
    return violations

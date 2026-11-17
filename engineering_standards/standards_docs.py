#!/usr/bin/env python3
"""The documentation dimension: the repo's docs/ tree, judged as structure.

Code, tests, documentation -- the third one had no mechanical enforcement anywhere in the
estate, which is why it was the dimension that slacked. These rules are the decidable half
of the documentation prose; everything needing judgment (does this page serve one need? does
user copy read like user copy?) stays there. That prose is in two files since 2026-09-10:
`.claude/rules/documentation.md` holds what is decided away from a markdown file (what a code
change owes, CLAUDE.md's role) and always loads; `.claude/rules/docs-authoring.md` holds the
page contract and loads with docs/. Point each message at whichever one answers it.

    docs-missing       the repo has no docs/index.md at all
    docs-frontmatter   a page does not declare a valid audience and type
    docs-broken-link   a relative link that resolves to nothing
    docs-orphan-page   a page no link path from the roots reaches
    docs-stale-symbol  a backtick-quoted identifier that exists nowhere in the source
                       (implemented in standards_symbols.py; orchestrated from here)
    docs-plan-page     a plan-shaped page in the docs tree; plans live in plans/, unscanned
    claude-md-length   CLAUDE.md holding knowledge instead of constraints

Shared markdown/tree primitives live in standards_markdown.py; the symbol-resolution
engine in standards_symbols.py. Both split out when this file crossed the length limit --
each is its own reason to change.

REPO-LEVEL, LIKE GITIGNORE. Every rule here is about the tree, not about a staged file:
orphanhood is a property of the whole link graph, and a missing index is a property of the
repository. So `check_documentation` is invoked from check-source-limits.py only on
whole-tree scans -- push and CI, never the commit stage -- exactly as `check_gitignore` is,
and for the same reason: a repo-level finding at commit time blocks every commit including
the one that would fix it.

`.md` STAYS OUT OF SOURCE_SUFFIXES, deliberately. Putting it there would run the per-file
rules over prose -- the 500-line limit against a reference page, the sentinel regexes
against code examples -- and every hit would be noise. Documentation is scanned by this
module only, with rules written for documents.

BROKEN LINKS HAVE NO EXEMPTION. The other rules here are smoke alarms and carry the marker
mechanism; a link to a file that does not exist is not a signal but a defect, with nothing
to argue about. Fix the link. Orphan pages DO get a file-scoped `docs-orphan-page` marker:
a page legitimately reached only from outside the tree -- an in-app help deep link -- is a
real, arguable case. (Written as a description rather than as the literal marker syntax on
purpose: `header_exemptions` reads any header it can parse, so spelling the marker out here
would make this module declare itself exempt and would put a documentation example in the
scanner's summary as if it were a decision somebody took.)

WHY collect_exemptions DOES NOT LIST ORPHAN EXEMPTIONS. Since 2026-09-01 that summary names
every file-scoped marker it can see -- but it sees the files the per-file scan walks, and
`.md` is out of that scope, so a docs page's marker is still absent from the "exempt:"
lines. The marker works and is still counted here. Closing the gap means giving the summary
a markdown pass of its own, not another hand-kept list of rules.

Source of truth: engineering-standards/engineering_standards/standards_docs.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import CheckConfig, Violation
from standards_exemptions import exemption_reason
from standards_markdown import (
    INLINE_CODE_SPAN,
    content_lines,
    documentation_pages,
    frontmatter_block,
    read_lines,
)
from standards_symbols import check_symbols

# The pages every repo's documentation tree starts from. README and CLAUDE.md are roots
# rather than pages: they carry no frontmatter (front door and session constitution are not
# tree content), but a link FROM them reaches a page just as well as one from the index.
ROOT_DOCUMENTS = ("docs/index.md", "README.md", "CLAUDE.md")

REQUIRED_FRONTMATTER: dict[str, frozenset[str]] = {
    "audience": frozenset({"dev", "user"}),
    "type": frozenset({"tutorial", "how-to", "reference", "explanation"}),
}
OPTIONAL_FRONTMATTER: dict[str, frozenset[str]] = {
    "access": frozenset({"public", "customer"}),
}

# `[text](target)` and `![alt](target "title")` -- the capture stops at whitespace or the
# closing parenthesis, which drops an optional title and never spans two links.
INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)[^)]*\)")

# A reference-style definition: `[label]: target`, at line start.
REFERENCE_DEFINITION = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)")

# A scheme prefix (`https:`, `mailto:`, `tel:`) -- external, not this rule's business.
EXTERNAL_TARGET = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)


def parse_frontmatter(lines: list[str]) -> dict[str, str] | None:
    """The leading `---` block as key -> value, or None when there is no closed block.

    Hand-parsed flat keys: the pack ships no YAML dependency, and the contract is three
    scalar fields. A `# comment` after a value is dropped, values are casefolded so the
    contract is spelling-insensitive.

    Finding the block is `frontmatter_block`'s job, shared with standards_rules; reading it
    as casefolded scalars is this function's, and only this one's.
    """
    block_lines = frontmatter_block(lines)
    if block_lines is None:
        return None
    block: dict[str, str] = {}
    for line in block_lines:
        key, separator, value = line.partition(":")
        if separator:
            block[key.strip().casefold()] = value.split("#")[0].strip().casefold()
    return block


def markdown_link_targets(lines: list[str]) -> list[tuple[int, str]]:
    """(line_number, target) for every checkable relative link in the document.

    External schemes and bare in-page anchors are dropped here; a `#fragment` or `?query`
    suffix on a relative target is stripped, because the file is what existence can decide.

    INLINE CODE SPANS ARE BLANKED FIRST, and this rule needs it more than the others: prose
    documenting link syntax (`` `[label](url)` ``) is not a link, and since docs-broken-link
    deliberately ships with no exemption, a false positive here can only be cleared by
    rewording correct prose. A space rather than nothing is substituted so that a link whose
    LABEL is a code span -- `[`page.md`](./page.md)`, how the estate's plans cross-reference
    each other -- collapses to `[ ](./page.md)` and is still matched.
    """
    targets: list[tuple[int, str]] = []
    for line_number, raw_line in content_lines(lines):
        line = INLINE_CODE_SPAN.sub(" ", raw_line)
        raw_targets = INLINE_LINK.findall(line)
        definition = REFERENCE_DEFINITION.match(line)
        if definition:
            raw_targets.append(definition.group(1))
        for raw in raw_targets:
            if EXTERNAL_TARGET.match(raw) or raw.startswith("#"):
                continue
            bare = raw.split("#")[0].split("?")[0]
            if bare:
                targets.append((line_number, bare))
    return targets


def check_documentation(repo_root: Path, config: CheckConfig) -> Iterable[Violation]:
    """Every documentation finding for this repo. The one entry point the driver calls."""
    if not config.check_docs:
        return

    pages = documentation_pages(repo_root)

    yield from _check_docs_presence(repo_root)
    yield from _check_frontmatter(pages)
    yield from _check_links(repo_root, pages)
    yield from _check_orphans(repo_root, pages)
    yield from check_symbols(repo_root, pages, config)
    yield from _check_plan_pages(repo_root, pages)
    yield from _check_claude_md(repo_root, config)


# A markdown task checkbox at list level: `- [ ] step`, `* [x] done`. The executable heart
# of a plan -- and the reason a plan cannot be documentation: it names symbols that will
# only exist once its steps run, and it dies when they have.
TASK_CHECKBOX = re.compile(r"^\s*[-*] \[[ xX]\] ")

# The word that claims plan identity in a filename or title. Word-bounded so `airplane`
# and `planning-poker` stay out of it.
PLAN_WORD = re.compile(r"\bplans?\b", re.IGNORECASE)


def _check_plan_pages(repo_root: Path, pages: list[Path]) -> Iterable[Violation]:
    """Plan-shaped pages inside the scanned docs tree. Plans belong in plans/, unscanned.

    A plan is a working artifact -- executed once, then deleted -- and the docs rules
    judge it wrongly by nature, not defect: it names symbols that only exist once its
    phases are built (stale-symbol), and its prose serves no reader need (frontmatter).
    Two signals, calibrated on the estate: a `plans/` directory component IS the claim and
    fires alone; otherwise it takes checkbox steps AND the word "plan" in the filename or
    title, so a reusable deploy checklist titled "Deploying" (agentsite) stays quiet --
    a checklist is re-run and therefore documentation, a plan is run once and is not.
    """
    for page in pages:
        relative = page.relative_to(repo_root)
        in_plans_directory = any(part.casefold() == "plans" for part in relative.parts[:-1])

        lines = read_lines(page)
        if not in_plans_directory:
            has_checkboxes = any(TASK_CHECKBOX.match(line) for _number, line in content_lines(lines))
            title = next(
                (line[2:] for _number, line in content_lines(lines) if line.startswith("# ")),
                "",
            )
            named_a_plan = bool(
                PLAN_WORD.search(page.stem.replace("-", " ").replace("_", " ")) or PLAN_WORD.search(title)
            )
            if not (has_checkboxes and named_a_plan):
                continue

        if exemption_reason(lines, "docs-plan-page") is not None:
            continue
        yield Violation(
            path=page,
            line=1,
            rule="docs-plan-page",
            message=(
                f"'{relative.as_posix()}' is plan-shaped (checkbox steps with a plan "
                f"title, or a plans/ directory) but sits in the scanned docs tree. A plan "
                f"is a working artifact, not documentation: move it to plans/ at the repo "
                f"root, delete it once executed -- or mark 'standards: docs-plan-page "
                f"exempt -- <why>' in its header if it is genuinely a reusable checklist."
            ),
        )


def _check_docs_presence(repo_root: Path) -> Iterable[Violation]:
    index = repo_root / "docs" / "index.md"
    if index.is_file():
        return
    yield Violation(
        path=index,
        line=1,
        rule="docs-missing",
        message=(
            "'docs/index.md' does not exist. Documentation is the third gated dimension: "
            "every repo keeps one docs/ tree with an index as its map -- see "
            ".claude/rules/docs-authoring.md. The index may be a REGISTRY: user docs that "
            "ship as product (an in-app guide, legal routes) are linked from it, never "
            "duplicated into markdown. A repo with genuinely nothing to document can set "
            "checkDocs false in .standards.json."
        ),
    )


def _check_frontmatter(pages: list[Path]) -> Iterable[Violation]:
    for page in pages:
        frontmatter = parse_frontmatter(read_lines(page))

        if frontmatter is None:
            for key in REQUIRED_FRONTMATTER:
                yield _frontmatter_violation(page, key, f"'{key}' is undeclared -- the page has no frontmatter block.")
            continue

        for key, allowed in REQUIRED_FRONTMATTER.items():
            value = frontmatter.get(key)
            if value is None:
                yield _frontmatter_violation(page, key, f"'{key}' is missing.")
            elif value not in allowed:
                yield _frontmatter_violation(
                    page, key, f"'{key}' is \"{value}\", which is not one of: {', '.join(sorted(allowed))}."
                )

        for key, allowed in OPTIONAL_FRONTMATTER.items():
            value = frontmatter.get(key)
            if value is not None and value not in allowed:
                yield _frontmatter_violation(
                    page, key, f"'{key}' is \"{value}\", which is not one of: {', '.join(sorted(allowed))}."
                )


def _frontmatter_violation(page: Path, key: str, what: str) -> Violation:
    return Violation(
        path=page,
        line=1,
        rule="docs-frontmatter",
        message=(
            f"{what} Every docs page declares audience (dev|user) and type "
            f"(tutorial|how-to|reference|explanation) -- the contract is in "
            f".claude/rules/docs-authoring.md."
        ),
    )


def _check_links(repo_root: Path, pages: list[Path]) -> Iterable[Violation]:
    for document in _link_carrying_documents(repo_root, pages):
        for line_number, target in markdown_link_targets(read_lines(document)):
            if _resolve_target(document, target).exists():
                continue
            yield Violation(
                path=document,
                line=line_number,
                rule="docs-broken-link",
                message=(
                    f"'{target}' does not resolve from this file. A broken link is a "
                    f"defect, not a judgment call -- fix the path or remove the link."
                ),
            )


def _link_carrying_documents(repo_root: Path, pages: list[Path]) -> list[Path]:
    roots = [repo_root / name for name in ROOT_DOCUMENTS]
    documents = [path for path in roots if path.is_file()]
    documents += [page for page in pages if page not in documents]
    return documents


def _resolve_target(document: Path, target: str) -> Path:
    return (document.parent / target.replace("\\", "/")).resolve()


def _check_orphans(repo_root: Path, pages: list[Path]) -> Iterable[Violation]:
    """Pages no link path from the roots reaches -- mechanically unfindable ones.

    Reachability is a breadth-first walk over md-to-md links starting from the roots that
    exist; a link on an orphan cannot rescue another orphan, which is what makes this a
    walk rather than a per-page grep for inbound links.
    """
    if not pages:
        return

    page_set = {page.resolve() for page in pages}
    reached: set[Path] = set()
    frontier = [(repo_root / name).resolve() for name in ROOT_DOCUMENTS if (repo_root / name).is_file()]

    while frontier:
        document = frontier.pop()
        for _, target in markdown_link_targets(read_lines(document)):
            resolved = _resolve_target(document, target)
            if resolved in page_set and resolved not in reached:
                reached.add(resolved)
                frontier.append(resolved)

    index = (repo_root / "docs" / "index.md").resolve()
    for page in pages:
        resolved = page.resolve()
        if resolved == index or resolved in reached:
            continue
        lines = read_lines(page)
        if exemption_reason(lines, "docs-orphan-page") is not None:
            continue
        yield Violation(
            path=page,
            line=1,
            rule="docs-orphan-page",
            message=(
                f"'{page.name}' is not reachable from docs/index.md, README.md or "
                f"CLAUDE.md -- a page no link leads to is a page no reader finds. Link it "
                f"from a reachable page, or mark 'standards: docs-orphan-page exempt -- "
                f"<why>' in its header."
            ),
        )


# ---- CLAUDE.md length ----------------------------------------------------------------------


def _check_claude_md(repo_root: Path, config: CheckConfig) -> Iterable[Violation]:
    """CLAUDE.md past the ceiling is holding knowledge, not constraints.

    The judgment half of the constitution-and-index rule in documentation.md. Presence-
    keyed when baselined: the baseline can grandfather that a repo's CLAUDE.md is over,
    but nothing tracks its size ratcheting -- the fix is the thinning itself, and the
    exemption marker exists for the repo whose constitution is genuinely that long.
    """
    claude = repo_root / "CLAUDE.md"
    if not claude.is_file():
        return

    lines = read_lines(claude)
    if len(lines) <= config.claude_md_max_lines:
        return
    if exemption_reason(lines, "claude-md-length") is not None:
        return

    yield Violation(
        path=claude,
        line=config.claude_md_max_lines + 1,
        rule="claude-md-length",
        message=(
            f"'CLAUDE.md' is {len(lines)} lines against the {config.claude_md_max_lines}-"
            f"line ceiling. CLAUDE.md is push-context -- constitution and index, never "
            f"encyclopedia. Move knowledge to a typed docs/ page and leave a pointer "
            f"(see .claude/rules/documentation.md), or mark 'standards: claude-md-length "
            f"exempt -- <why>' in its header."
        ),
    )

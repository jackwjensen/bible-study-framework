#!/usr/bin/env python3
"""The pack's judgment layer, judged as a budget: what `claude/rules/*.md` may cost.

Layers 1 and 2 of the decision ladder are free to grow. An analyzer rule, a banned symbol,
a scanner check -- none of them consume a reader's attention, because none of them depend
on being read. The gate runs whether or not anybody remembers the rule exists.

Layer 3 does not have that property. A `claude/rules/*.md` file with no `paths:` frontmatter
is loaded into EVERY session in an adopted repo, before any work starts, and it is enforced
by being attended to. That makes prose the one layer where growth has a running cost, and --
the part that makes it worth a rule -- the one layer where the cost is invisible. Nothing
announces the day the always-loaded set got big enough to start diluting itself. Measured on
2026-08-27, before this rule existed: nine files, 1,661 lines, roughly 24,000 tokens in every
session, with no ceiling of any kind and no record that anyone had chosen the number.

    rules-scope-declared   a rule file that has not said whether it always loads
    rules-file-length      one rule file past the point where it is a single subject
    rules-context-budget   the always-loaded set as a whole, past its ceiling

WHY THREE RULES AND NOT ONE. They fail in different directions and a single rule would miss
two of them. Per-file length catches the file that grew into two subjects; it does NOT stop
ten small always-load files appearing, which costs exactly the same attention. The budget
catches that; it does not tell you WHICH file to look at, and it cannot see a file that
never declared its scope at all. `rules-scope-declared` is the one that does the real work
over time, because it converts always-loading from a silent default into a written decision.

THE COMPLIANCE PATH IS THE ESCAPE HATCH, which is why `rules-scope-declared` ships with no
exemption marker. Every other smoke-alarm rule in the pack needs a way to say "looked at;
nothing to fix", and gets one. Here that sentence already has a home: `alwaysLoad:` IS the
exemption, carrying the same 30-character reason floor an exemption marker carries, and
sitting in the frontmatter where the next author meets it. A second mechanism would only let
someone skip writing the reason.

THE BUDGET'S ESCAPE HATCH IS `.standards.json`, deliberately, and it is the one place in
this module where the pack's usual instrument is the wrong one. An aggregate has no file to
carry a marker -- the finding belongs to the SET, not to whichever file happened to cross
the line -- so the only honest place to relax it is the repo's config, where
`collect_tuning` already forces a written reason and prints it on every run. And it is in
NEVER_BASELINED: a baselined context budget is precisely the silent growth this rule exists
to stop, recorded as accepted.

WHY LINES AND NOT BYTES. Tokens are what actually cost, and bytes track tokens better than
lines do. Lines win anyway for two reasons: every other size rule in this pack is in lines
(`file-length`, `claude-md-length`), so a second unit would be one more thing to hold; and
these files measure 73-80 characters per line across all fifteen of them, tight enough that
the proxy never disagrees with the thing it stands for.

Source of truth: engineering-standards/engineering_standards/standards_rules.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from standards_core import CheckConfig, Violation
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH, exemption_reason
from standards_markdown import frontmatter_block, read_lines

SCOPE_RULE = "rules-scope-declared"
LENGTH_RULE = "rules-file-length"
BUDGET_RULE = "rules-context-budget"

# Where the judgment layer lives, in both the shapes it takes. The pack authors it in
# `claude/rules`; `/apply-standards` installs it into a consuming repo's `.claude/rules`.
# Both are scanned and their findings unioned, so the rule reads the same in the pack (where
# a change is authored) and in every repo that received it (where the cost is actually paid).
RULE_DIRECTORIES = ("claude/rules", ".claude/rules")

# The frontmatter key that scopes a file to the paths it applies to. Consumed by the harness,
# not by this pack -- which is exactly why it needs a rule: nothing here fails when it is
# missing, so its absence is invisible at every stage except the token bill.
PATHS_KEY = "paths:"

# The frontmatter key that declares the opposite, and states why. Its VALUE is the argument,
# so declaring and defending are one act rather than two keys that can drift apart.
ALWAYS_LOAD_KEY = "alwaysload"


def rule_files(repo_root: Path) -> list[Path]:
    """Every judgment-layer file this repository carries, in either directory shape.

    DEDUPED BY FILENAME, first directory winning. Normally only one shape exists -- the pack
    authors in `claude/rules`, a consuming repo receives `.claude/rules` -- but the two can
    coexist, and when they do they hold the SAME rule twice rather than two rules. Counting
    both would inflate the budget by the size of the duplicate set and send the reader looking
    for prose to cut that does not exist; the likeliest response to that is raising
    `rulesContextBudget`, which is the one outcome this family exists to prevent.

    Found on 2026-08-27 by pointing `sync_pack.py --apply --repo` at the pack itself, which
    installed `claude/rules` into `.claude/rules` beside it and took the budget from 1,218 to
    1,834 with nothing actually added.
    """
    found: dict[str, Path] = {}
    for directory in RULE_DIRECTORIES:
        location = repo_root / directory
        if not location.is_dir():
            continue
        for path in location.glob("*.md"):
            found.setdefault(path.name, path)
    return sorted(found.values())


def _frontmatter_values(lines: list[str]) -> dict[str, str]:
    """Frontmatter as key -> raw value, case-preserved, list values flattened.

    Case-preserved because `alwaysLoad:`'s value is prose a human wrote and will read back;
    casefolding it would return a reason nobody typed. Keys ARE casefolded, so the contract
    does not hinge on remembering the capital L.

    A YAML list (`paths:` over several `- "**/*.cs"` lines) flattens to its items joined by
    spaces. Nothing here needs the items individually -- the question is only whether the key
    is present and non-empty -- and flattening keeps this from becoming a YAML parser.
    """
    block = frontmatter_block(lines)
    if block is None:
        return {}

    values: dict[str, str] = {}
    current: Optional[str] = None
    for line in block:
        stripped = line.strip()
        if stripped.startswith("- ") and current is not None:
            values[current] = f"{values[current]} {stripped[2:].strip()}".strip()
            continue
        key, separator, value = line.partition(":")
        if not separator or line[:1].isspace():
            continue
        current = key.strip().casefold()
        values[current] = value.strip()
    return values


def scope_of(lines: list[str]) -> tuple[bool, Optional[str]]:
    """(is scoped by paths, the stated always-load reason) for one rule file.

    Both can be absent, which is the finding `rules-scope-declared` reports. Both being
    present is not a finding: `paths:` wins on behaviour and the reason beside it is
    harmless documentation, and refusing that combination would be a rule about tidiness.
    """
    values = _frontmatter_values(lines)
    scoped = bool(values.get(PATHS_KEY.rstrip(":"), "").strip())
    reason = values.get(ALWAYS_LOAD_KEY, "").strip() or None
    return scoped, reason


def always_loaded(lines: list[str]) -> bool:
    """True when this file reaches every session -- the set the budget measures."""
    scoped, _ = scope_of(lines)
    return not scoped


def check_rules(repo_root: Path, config: CheckConfig) -> Iterable[Violation]:
    """Every judgment-layer finding for this repo. The one entry point the driver calls."""
    if not config.check_rules:
        return

    files = rule_files(repo_root)
    if not files:
        return

    always_load_lines = 0
    for path in files:
        lines = read_lines(path)
        yield from _check_scope(path, lines)
        yield from _check_length(path, lines, config)
        if always_loaded(lines):
            always_load_lines += len(lines)

    yield from _check_budget(repo_root, files, always_load_lines, config)


def _check_scope(path: Path, lines: list[str]) -> Iterable[Violation]:
    """A rule file that never said whether it costs every session or only some.

    THE DEFAULT IS THE PROBLEM, not any individual file. Omitting `paths:` is how a file
    becomes always-loaded, and omitting it is also what happens when nobody considered the
    question -- so the expensive outcome is the one you get by not deciding. This rule
    removes that: both answers now cost one line of frontmatter, and only one of them costs
    a reason.
    """
    scoped, reason = scope_of(lines)
    if scoped:
        return
    if reason is not None and len(reason) >= MIN_EXEMPTION_REASON_LENGTH:
        return

    stated = "states no reason" if reason is None else f"states only {len(reason)} characters"
    yield Violation(
        path=path,
        line=1,
        rule=SCOPE_RULE,
        message=(
            f"'{path.name}' does not declare its load scope, so it loads into every session "
            f"and {stated} for the cost. Add `paths:` frontmatter naming the files it applies "
            f"to, or `alwaysLoad: <why it is genuinely language-neutral>` with at least "
            f"{MIN_EXEMPTION_REASON_LENGTH} characters. There is no exemption marker for this "
            f"rule -- `alwaysLoad` is the exemption, and its value is the argument."
        ),
    )


def _check_length(path: Path, lines: list[str], config: CheckConfig) -> Iterable[Violation]:
    """One rule file past the length where it is still a single subject.

    The same smoke alarm `file-length` is for source, pointed at prose, and it carries the
    same file-scoped marker -- in markdown, an HTML comment in the header, which
    standards_exemptions already recognises.
    """
    if len(lines) <= config.rules_max_lines:
        return
    if exemption_reason(lines, LENGTH_RULE) is not None:
        return

    yield Violation(
        path=path,
        line=config.rules_max_lines + 1,
        rule=LENGTH_RULE,
        message=(
            f"'{path.name}' is {len(lines)} lines against the {config.rules_max_lines}-line "
            f"ceiling. A rule file that long has usually taken on a second subject: split it, "
            f"move the deep rationale to a typed docs/ page and leave the constraint here, or "
            f"promote the decidable half into the scanner. Mark "
            f"'standards: {LENGTH_RULE} exempt -- <why>' in an HTML comment in the header if "
            f"its length genuinely tracks one subject."
        ),
    )


def _check_budget(
    repo_root: Path, files: list[Path], always_load_lines: int, config: CheckConfig
) -> Iterable[Violation]:
    """The always-loaded set as a whole, past its ceiling.

    The finding names the set, never a file, because no single file is at fault -- the tenth
    small always-load rule costs the same attention as one large one, and blaming whichever
    happened to cross the line would send the next author to trim the wrong thing.

    A stable quoted token is load-bearing here: baseline identity is
    path::rule::first-quoted-token (standards_baseline.py), so quoting the line COUNT would
    make every measurement its own key. This rule is in NEVER_BASELINED anyway; quoting the
    directory keeps it correct if that ever changes.
    """
    if always_load_lines <= config.rules_context_budget:
        return

    scoped_out = [path.name for path in files if not always_loaded(read_lines(path))]
    over = always_load_lines - config.rules_context_budget
    yield Violation(
        path=repo_root / RULE_DIRECTORIES[0],
        line=1,
        rule=BUDGET_RULE,
        message=(
            f"'always-load-rules' total {always_load_lines} lines against the "
            f"{config.rules_context_budget}-line budget ({over} over). Every one of those "
            f"lines enters every session in this repo before any work starts, and prose is "
            f"enforced by being attended to -- so this is the one layer where growth has a "
            f"running cost. Scope a file with `paths:` where its subject is a file type, or "
            f"where a GATE already names it when it fires -- a finding is a trigger, so a "
            f"file a scanner can send a reader to need not be carried in advance "
            f"({len(scoped_out)} are scoped already). Otherwise promote a decidable rule into "
            f"the scanner, or move rationale to docs/ and leave the constraint. Raising "
            f"rulesContextBudget in .standards.json is the last answer, and it must state why."
        ),
    )

#!/usr/bin/env python3
"""Naming every relaxation the scanner is honouring.

An exemption nobody sees is just a baseline with better manners. The whole argument for
putting the marker IN the file, rather than in a ledger beside it, is that somebody
reviewing the repo can read the reason and disagree with it -- and that requires the
reason to appear somewhere they actually look. `clean` has to be a claim anybody can
check, which it is not while a sentence in a file header can switch a rule off in silence.

WHY THIS IS ITS OWN MODULE. It was a function in check-source-limits.py, whose docstring
calls that file the driver -- walking the tree, ratcheting the baseline, reporting, CLI.
Reporting relaxations grew into a concern with its own rules (which markers are
load-bearing, which scopes the walk can see, which files the summary cannot reach), and
adding it pushed the driver over the 500-line limit. The pack's own instruction for that
moment is explicit: give the new concern its own module, never trim a comment to fit.

Source of truth: engineering-standards/engineering_standards/standards_exemption_report.py
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

from standards_checks import CheckConfig
from standards_exemption_scope import FILE_SCOPED_TAGS
from standards_exemptions import exemption_reason, header_exemptions
from standards_query import check_query_shape
from standards_secrets import is_never_read


def read_source_lines(path: Path) -> list[str] | None:
    """This file's lines, or None with a warning if it cannot be read.

    Lives here rather than in the driver because the exemption walk was its heaviest
    caller and a module cannot import from `check-source-limits.py` -- the hyphen in that
    name makes it a script, not an importable module.

    A SECRET-BEARING FILE IS DECIDED BY ITS NAME AND NEVER OPENED -- see
    standards_secrets.is_never_read, which is the one predicate for that. An empty line list
    is the honest input: every content rule then finds nothing, which is correct, and the
    credential rule reports the file from its name alone, which is all it ever needed. A
    `.env` declares no exemptions either, which is the correct answer as well as the safe one.

    `utf-8-sig` drops a leading byte-order mark; left in, it hides whatever line 1 holds --
    a header exemption marker, a directive -- from every rule anchored at the line start.
    """
    if is_never_read(path):
        return []

    try:
        return path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError as read_error:
        print(f"warning: could not read {path}: {read_error}", file=sys.stderr)
        return None


def _file_exemptions(
    path: Path, relative: str, lines: list[str], config: CheckConfig
) -> Iterable[tuple[str, str, str]]:
    """One file's header markers, with the two that can cheaply be re-checked filtered.

    A `file-length` marker on a file since split under the limit, or a `query-shape` marker
    on a query since rewritten, is stale rather than a decision -- printing it would train
    people to skim the list, which is the one thing this report cannot afford. No such
    re-check exists for the other rules (it would mean running every rule twice), so their
    markers are reported as declared.

    A TAG NO RULE READS AT FILE SCOPE IS NOT AN EXEMPTION AND MUST NOT BE PRINTED AS ONE.
    `header_exemptions` reads whatever tag it FINDS, which is right and is what stopped a
    hand-kept list of three rules from hiding the other seventeen -- but nothing checked that
    a rule would read the tag back, so this report printed `exempt: engine/config.py
    [const-environment-literal]` while the scanner reported the finding in the same run
    (sourcetext.ai, 2026-09-09). Such a marker is a defect in the file, and `exemption-inert`
    reports it as one; the single thing this report must never do is call it honoured.
    """
    for tag, reason in header_exemptions(lines):
        if tag not in FILE_SCOPED_TAGS:
            continue
        if tag == "file-length":
            if len(lines) > config.max_file_lines:
                yield (relative, f"file-length, {len(lines)} lines", reason)
        elif tag == "query-shape":
            shape_is_checked = config.check_query_shape and path.suffix in (
                ".cs",
                ".razor",
                ".py",
            )
            if shape_is_checked and any(check_query_shape(path, lines, config, respect_exemption=False)):
                yield (relative, "query-shape", reason)
        else:
            yield (relative, tag, reason)


def _claude_md_exemption(root: Path, config: CheckConfig) -> Iterable[tuple[str, str, str]]:
    """CLAUDE.md is not a candidate file (.md is out of per-file scope), so its one
    file-scoped exemption is collected here or named nowhere."""
    claude_md = root / "CLAUDE.md"
    if not (config.check_docs and claude_md.is_file()):
        return
    claude_lines = read_source_lines(claude_md)
    if not claude_lines or len(claude_lines) <= config.claude_md_max_lines:
        return
    reason = exemption_reason(claude_lines, "claude-md-length")
    if reason is not None:
        yield ("CLAUDE.md", f"claude-md-length, {len(claude_lines)} lines", reason)


def collect_exemptions(root: Path, config: CheckConfig, paths: list[Path]) -> list[tuple[str, str, str]]:
    """(path, rule, stated reason) for every file-scoped exemption in the tree.

    IT ASKS THE FILE WHAT IT DECLARES rather than asking about rules it was told to
    expect, and that inversion is the fix for a real hole. This used to name three rules --
    `file-length`, `query-shape`, `claude-md-length` -- because those were the three
    somebody had wired up. Every other file-scoped marker silenced its rule and appeared in
    no output at all: `client-address`, `cors`, `tls`, `sql-injection`, the two concurrency
    rules, `paged-without-order` and the rest -- rules that could be switched off for a
    whole file without the summary, the gate, or CI ever mentioning it. `clean` then meant
    "clean, except where somebody wrote a sentence you cannot see".

    "Load-bearing" is still applied to the two rules that can cheaply answer it: a
    `file-length` marker on a file since split under the limit, or a `query-shape` marker
    on a query since rewritten, is stale rather than a decision, and printing it would
    train people to skim past the list. No such re-check exists for the other rules -- it
    would mean running every rule twice -- so their markers are reported as declared.

    "SINCE THE SCANNER REALLY IS HONOURING THEM" is what that last clause used to say, and
    it was false for every tag outside `FILE_SCOPED_TAGS`. Asking the file what it declares
    means the answer includes tags no rule reads at file scope -- a misspelling, a rule whose
    hatch is line-scoped, a rule that deliberately has none -- and each was printed here as an
    honoured decision. The registry now filters them out and `exemption-inert` reports them,
    so this list is once again only what it says it is.

    KNOWN GAP: `.md` is out of per-file scope, so a docs page's `docs-orphan-page` or
    `docs-plan-page` marker is still named nowhere; CLAUDE.md is handled below because it
    is the one that matters most. Those rules have no line-scoped form, so nothing is
    silently widened by them -- but they are invisible, which is the lesser half of the
    same defect. Closing it means giving this a markdown pass, not another list of rules.

    LINE-SCOPED markers are also absent, and for a harder reason: they are resolved inside
    each rule's own walk, against a line number only that rule knows. Their blast radius is
    one line, where a file marker's is the file.
    """
    exempt: list[tuple[str, str, str]] = []

    for path in paths:
        lines = read_source_lines(path)
        if lines is None:
            continue
        exempt.extend(_file_exemptions(path, path.relative_to(root).as_posix(), lines, config))

    exempt.extend(_claude_md_exemption(root, config))
    return exempt

#!/usr/bin/env python3
"""Cases for the exemption report: what a run tells you has been silenced.

Written on 2026-09-01 because `tests-uncovered-module` reported this module on its first
run over the pack -- it was split out of `check-source-limits.py` days earlier and never
got a suite. That is the rule doing its job on its own author, which is the best evidence
it is calibrated.

The subject matters more than its size. This module is what makes an exemption VISIBLE:
every file-scoped marker is a rule switched off for a whole file, and the argument for
allowing that at all is that each one gets printed on every run where a reviewer sees it.
If this module quietly stops listing a marker, `clean` goes back to meaning "clean, except
where somebody wrote a sentence you cannot see" -- silently, and in the direction nobody
checks.

Run: pytest test_standards_exemption_report.py
"""

from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import CheckConfig  # noqa: E402
from standards_exemption_report import collect_exemptions, read_source_lines  # noqa: E402
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402

REASON = "a declarative schema whose length tracks the table count, not accumulated logic"
assert len(REASON) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


@contextmanager
def repo() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tree:
        yield Path(tree)


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def report(root: Path, paths: list[Path]) -> list[tuple[str, str, str]]:
    return collect_exemptions(root, CheckConfig(), paths)


# ---- reading source ---------------------------------------------------------------------


def test_a_readable_file_comes_back_as_lines():
    with repo() as root:
        path = write(root, "a.py", "one\ntwo\n")
        assert read_source_lines(path) == ["one", "two"]


def test_an_unreadable_path_is_none_rather_than_an_exception():
    """The walk must survive a file it cannot open: one bad file is not a failed run."""
    with repo() as root:
        assert read_source_lines(root / "does-not-exist.py") is None


def test_undecodable_bytes_do_not_raise():
    """`errors='replace'` is deliberate -- a mojibaked file still gets scanned."""
    with repo() as root:
        path = root / "latin.php"
        path.write_bytes(b"<?php\n// caf\xe9 -- not valid UTF-8\n")
        lines = read_source_lines(path)
        assert lines is not None and len(lines) == 2


def test_a_byte_order_mark_is_not_part_of_line_one():
    """Left in, U+FEFF sits before whatever line 1 holds and hides it from every rule
    anchored at the line start -- a Blazor `@page`, a header exemption marker."""
    with repo() as root:
        path = write(root, "LogInd.razor", '﻿@page "/log-ind"\n')
        assert read_source_lines(path) == ['@page "/log-ind"']


# ---- what the report names --------------------------------------------------------------


def test_a_file_scoped_marker_is_reported_with_its_rule_and_reason():
    with repo() as root:
        path = write(root, "resolver.php", f"<?php\n// standards: client-address exempt -- {REASON}\n")
        found = report(root, [path])
        assert len(found) == 1
        name, rule, reason = found[0]
        assert rule == "client-address"
        assert REASON in reason
        assert "resolver" in name


def test_a_rule_the_report_was_never_told_about_is_still_named():
    """The inversion this module exists for: it asks the FILE what it declares.

    Naming a fixed list of rules is what let `client-address`, `cors`, `tls` and the rest
    be switched off file-wide while appearing in no output at all.
    """
    with repo() as root:
        path = write(root, "resolver.php", f"<?php\n// standards: client-address exempt -- {REASON}\n")
        assert [rule for _, rule, _ in report(root, [path])] == ["client-address"]


def test_a_file_with_no_marker_contributes_nothing():
    with repo() as root:
        path = write(root, "plain.py", "def add(a, b):\n    return a + b\n")
        assert report(root, [path]) == []


def test_a_reason_under_the_floor_is_not_a_declared_exemption():
    """Consistent with the exemption machinery itself: the reason IS the gate."""
    with repo() as root:
        path = write(root, "resolver.php", "<?php\n// standards: client-address exempt -- nope\n")
        assert report(root, [path]) == []


def test_a_stale_size_marker_is_not_reported_as_a_live_decision():
    """The load-bearing re-check, and why only two rules get one.

    Learned by writing this suite: a `file-length` marker on a file since split under the
    limit is stale, not a decision, so it is deliberately NOT printed. Padding the list
    with markers that silence nothing trains people to skim the one place an invisible
    rule-disabling is meant to be visible. Rules with no cheap re-check (client-address,
    cors, tls) are reported as declared instead -- the honest reading, since the scanner
    really is honouring them.
    """
    with repo() as root:
        path = write(root, "small.py", f'"""standards: file-length exempt -- {REASON}"""\n')
        assert report(root, [path]) == [], "a marker silencing nothing is not a decision"


def test_every_marker_in_a_file_is_reported_not_just_the_first():
    with repo() as root:
        path = write(
            root,
            "detector.php",
            f"<?php\n// standards: client-address exempt -- {REASON}\n// standards: cors-wildcard exempt -- {REASON}\n",
        )
        assert sorted(rule for _, rule, _ in report(root, [path])) == [
            "client-address",
            "cors-wildcard",
        ]


def test_the_reason_survives_wrapping_onto_a_second_line():
    """The reason floor actively pushes reasons onto a second line, so it must be joined."""
    with repo() as root:
        path = write(
            root,
            "resolver.php",
            "<?php\n"
            "// standards: client-address exempt -- the canonical resolver, whose peer read\n"
            "// is the deliberate last fallback after the declared-hop walk declines.\n",
        )
        found = report(root, [path])
        assert len(found) == 1
        assert "last fallback" in found[0][2]


# ---- the report may only claim what the scanner honours -----------------------------------


def test_a_marker_for_a_rule_that_reads_no_exemption_is_not_advertised():
    """THE DEFECT THIS MODULE'S OWN DOCSTRING PROMISED COULD NOT HAPPEN.

    `header_exemptions` reads whatever tag it finds -- correctly, since that is what stopped a
    hand-kept list of three rules from hiding the other seventeen -- but nothing checked that
    a rule would read the tag BACK. So a run printed `exempt: engine/config.py
    [const-environment-literal] -- <reason>` and reported the finding in the same breath.
    Found on sourcetext.ai, 2026-09-09, on three files. `exemption-inert` now reports the
    marker as the defect it is; the one thing the report must not do is call it honoured.
    """
    with repo() as root:
        path = write(
            root,
            "engine/config.py",
            f'"""Engine configuration.\n\nstandards: made-up-tag exempt -- {REASON}\n"""\n',
        )
        assert report(root, [path]) == []


def test_a_line_scoped_rules_marker_in_a_header_is_not_advertised_either():
    """The rule reads a marker beside the flagged line, so a header marker for it is not a
    file-scoped decision -- and this report only ever spoke about file scope."""
    with repo() as root:
        path = write(
            root,
            "engine/config.py",
            f'"""Engine configuration.\n\nstandards: const-environment-literal exempt -- {REASON}\n"""\n',
        )
        assert report(root, [path]) == []


def test_a_file_scoped_marker_is_still_advertised():
    """The property everything above is protecting: an exemption that IS honoured stays
    visible on every run, which is the entire argument for allowing one at all."""
    with repo() as root:
        path = write(root, "Cors.cs", f"// standards: cors-wildcard exempt -- {REASON}\n")
        assert [rule for _, rule, _ in report(root, [path])] == ["cors-wildcard"]


if __name__ == "__main__":
    from standards_selftest import run_module_tests

    raise SystemExit(run_module_tests(sys.modules[__name__]))

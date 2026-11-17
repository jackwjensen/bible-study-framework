#!/usr/bin/env python3
"""Cases for the zero-like sentinel family: `unset-not-zero` and `payload-default`.

Written on 2026-09-01 because `tests-uncovered-module` reported this module on its first run
over the pack. It was reachable only through `standards_checks`'s re-exports, so its own
seams -- the per-language coalesce patterns, the normalisation, the guard look-ahead -- had
no direct cases at all.

The distinction these two rules draw is the easiest in the pack to erode by accident, so it
is pinned from both sides: an IDENTITY or an external reference may never be zero-like,
because "nobody chose" and "somebody chose this" then become indistinguishable; MONEY
legitimately may, because an amount can really be zero, and `payload-default` covers the
case money was actually about -- a value INVENTED for a key that never arrived.

Every coalesce below is a fixture the detector is meant to catch, never a default this file
applies. That was a file-scoped `unset-not-zero` marker until 2026-09-09: the rule is
LINE-scoped, so the header form never carried, and the fixtures are quoted strings the rule
does not reach anyway. `exemption-inert` found it; prose is the honest version.

Run: pytest test_standards_sentinels.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_sentinels import (  # noqa: E402
    CSHARP_ZERO_COALESCE,
    JS_ZERO_COALESCE,
    PHP_ZERO_COALESCE,
    PYTHON_ZERO_COALESCE,
    check_payload_default,
    check_zero_coalesce,
    coalesce_names,
)


def zero(source: str, name: str, patterns=PHP_ZERO_COALESCE) -> list[str]:
    return [v.rule for v in check_zero_coalesce(Path(name), source.splitlines(), patterns)]


def payload(source: str, name: str, patterns=PHP_ZERO_COALESCE) -> list[str]:
    return [v.rule for v in check_payload_default(Path(name), source.splitlines(), patterns)]


# ---- the patterns, per language ---------------------------------------------------------


def test_php_null_coalesce_names_both_sides():
    """Both the landing name and the name READ matter, because a later guard may test either.

    Looking only at the reported name missed a real guard in DonorLink's SKAT export and
    reported correct code, which is why the tuple carries both.
    """
    names = coalesce_names("$job->project_id = $selected['id'] ?? 0;", PHP_ZERO_COALESCE)
    assert names == ("$job->project_id", "id")


def test_python_fallback_names_the_target():
    assert "project_id" in coalesce_names("project_id = selected.get('id') or 0", PYTHON_ZERO_COALESCE)


def test_csharp_fallback_names_the_target():
    assert "ProjectId" in coalesce_names("ProjectId = selected?.Id ?? 0;", CSHARP_ZERO_COALESCE)


def test_javascript_fallback_names_the_target():
    assert "projectId" in coalesce_names("const projectId = selected?.id ?? 0;", JS_ZERO_COALESCE)


def test_names_are_extracted_without_judging_the_line():
    """`coalesce_names` is a name extractor, not a detector.

    It reports the assignment target on any assignment, coalesce or not -- `find_zero_like
    _sentinel` decides whether the line is a finding, and only then are the names consulted.
    Asserting emptiness here would pin a contract the function does not have.
    """
    assert coalesce_names("$total = array_sum($lines);", PHP_ZERO_COALESCE) == ("$total",)
    assert coalesce_names("return walk($chain);", PHP_ZERO_COALESCE) == ()


# ---- unset-not-zero: an identity may never be zero-like ---------------------------------


def test_an_identity_defaulted_to_zero_fires():
    assert zero("$job->project_id = $selected['id'] ?? 0;\n", "job.php") == ["unset-not-zero"]


def test_an_external_reference_defaulted_to_empty_fires():
    assert zero("$row->stripe_reference = $payload['ref'] ?? '';\n", "sync.php") == ["unset-not-zero"]


def test_money_is_deliberately_not_covered():
    """An amount can genuinely BE zero -- an empty wallet, a verification charge."""
    assert zero("$invoice->amount_total = $line->total ?? 0;\n", "invoice.php") == []


def test_a_display_name_may_be_empty():
    """ "Absent" and "empty" render identically for a description; the distinction buys nothing."""
    assert zero("$page->subtitle = $meta['subtitle'] ?? '';\n", "page.php") == []


def test_an_aggregate_over_an_empty_set_is_genuinely_zero():
    assert zero("$order_count = count($orders) ?? 0;\n", "report.php") == []


def test_normalise_then_guard_is_not_a_finding():
    """Coalescing then branching on the same name restores the distinction before storing."""
    source = (
        "$series_id = (string) ($post['meta']['series'] ?? '');\n"
        "$is_hub    = $series_id !== '' && $series_part === null;\n"
    )
    assert zero(source, "post.php") == []


# ---- payload-default: a value invented for a key that never arrived ---------------------


def test_a_money_key_read_from_a_payload_and_defaulted_fires():
    source = "$amount_minor = $payload['amount_subtotal'] ?? 0;\n"
    assert payload(source, "webhook.php") == ["payload-default"]


def test_reading_the_same_key_without_defaulting_is_quiet():
    source = "$amount_minor = $payload['amount_subtotal'];\n"
    assert payload(source, "webhook.php") == []


def test_an_aggregate_is_exempt_from_the_payload_rule_too():
    assert payload("$total = array_sum($payload['lines']) ?? 0;\n", "webhook.php") == []


if __name__ == "__main__":
    from standards_selftest import run_module_tests

    raise SystemExit(run_module_tests(sys.modules[__name__]))

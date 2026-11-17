#!/usr/bin/env python3
"""Zero-like sentinels: what counts as one, and when the code has already excused it.

Split out of standards_checks.py when that file crossed the pack's own 500-line limit --
the pack does not get to baseline itself. The seam is a real responsibility boundary, not
a line-count trick: this file answers "is a zero-like fallback a lie here?", while
standards_checks.py answers "what does each language look like?".

Two rules live here, and the difference between them is the whole point:

    unset-not-zero   the NAME can never legitimately be zero-like (an identity, an
                     external reference), so a zero-like fallback destroys information.
    payload-default  the VALUE can legitimately be zero, but it was read from an external
                     payload by literal key, so a default invents data that never arrived.

Only the first gets the guard exemption below, and the asymmetry is deliberate -- see
_guards_against_sentinel.

Source of truth: engineering-standards/engineering_standards/standards_sentinels.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from standards_core import (
    AGGREGATE_CALL,
    Violation,
    ZeroCoalescePatterns,
    external_keys_read,
    is_identity_name,
    is_money_amount_name,
    iter_code_lines,
    zero_like_sentinel_kind,
)
from standards_exemptions import line_exemption_reason

# The zero-like fallbacks, as a capturing alternation reused by every language. `0` and
# the empty string are the same mistake wearing different clothes: both are real values
# that cannot be told apart from a deliberate choice once stored. Normalised to "0" or
# '""' by `_normalise_fallback` so the classifier only has two cases to reason about.
CSHARP_FALLBACK = r"(?P<fallback>0(?:\.0+)?[mMdDfFuUlL]{0,2}|\"\"|string\.Empty)"
PYTHON_FALLBACK = r"(?P<fallback>0|\"\"|'')"
PHP_FALLBACK = r"(?P<fallback>0|\"\"|'')"
JS_FALLBACK = r"(?P<fallback>0|\"\"|''|``)"

# C#: `?? 0`, `?? 0m`, `?? ""`, `?? string.Empty`.
#
# The assign_target alternation matters more than it looks. A C# declaration is
# `Type name = ...`, so a pattern that only skips an optional `var` captures the TYPE:
# `string cpr = user.NationalId ?? ""` yielded "string". That is not just a cosmetic
# mis-name -- the guard exemption looks the captured name up in the following lines, and
# it was searching for "string" instead of "cpr", so a textbook normalise-then-guard
# (`if (string.IsNullOrWhiteSpace(cpr))` on the very next line) was reported anyway.
# The first branch takes the declared name out of `Type name =`, including generics,
# arrays and nullable marks; the second is the plain re-assignment form.
CSHARP_ZERO_COALESCE = ZeroCoalescePatterns(
    coalesce=re.compile(rf"\?\?=?\s*{CSHARP_FALLBACK}\s*(?=[;,)\]}}]|$)"),
    assign_target=re.compile(
        r"^\s*(?:[A-Za-z_][A-Za-z0-9_.<>,\[\]]*\??\s+)?(?P<target>[A-Za-z_][A-Za-z0-9_.]*)"
        r"\s*=(?![=>])"
    ),
    left_operand=re.compile(rf"(?P<operand>[A-Za-z_][A-Za-z0-9_.]*)\??\s*\?\?=?\s*{CSHARP_FALLBACK}"),
    advice="Use null for unset, or assign the NAMED default if one genuinely applies.",
)

# Python's falsy-coalesce trap: `or` also swallows a legitimate 0 or "".
PYTHON_ZERO_COALESCE = ZeroCoalescePatterns(
    coalesce=re.compile(rf"\s+or\s+{PYTHON_FALLBACK}(?![\w.])"),
    assign_target=re.compile(r"^\s*(?P<target>[A-Za-z_][A-Za-z0-9_.]*)\s*=(?![=])"),
    left_operand=re.compile(rf"(?P<operand>[A-Za-z_][A-Za-z0-9_.\[\]'\"()]*?)\s+or\s+{PYTHON_FALLBACK}(?![\w.])"),
    advice="Use `x if x is not None else <named default>`; `or` also swallows a real 0.",
)

# PHP carries BOTH traps in one language: `??` coalesces only null, `?:` coalesces every
# falsy value -- so `?: 0` additionally eats a legitimate 0, "" and "0".
PHP_ZERO_COALESCE = ZeroCoalescePatterns(
    coalesce=re.compile(rf"(?:\?\?=?|\?:)\s*{PHP_FALLBACK}(?![\w.])"),
    assign_target=re.compile(
        r"^\s*(?P<target>\$?[A-Za-z_][A-Za-z0-9_]*(?:(?:->|::)[A-Za-z_][A-Za-z0-9_]*)*)"
        r"(?:\[[^\]]*\])?\s*(?:\?\?)?=(?![=>])"
    ),
    left_operand=re.compile(
        rf"(?P<operand>[A-Za-z_][A-Za-z0-9_]*)['\"]?\]?\s*(?:\?\?=?|\?:)\s*{PHP_FALLBACK}(?![\w.])"
    ),
    advice='Use null for unset. Note `?:` also swallows a legitimate 0, "" and "0".',
)

# JavaScript/TypeScript: `|| 0` is the falsy trap, `?? 0` the null one.
JS_ZERO_COALESCE = ZeroCoalescePatterns(
    coalesce=re.compile(rf"(?:\|\||\?\?)=?\s*{JS_FALLBACK}(?![\w.])"),
    assign_target=re.compile(r"^\s*(?:const\s+|let\s+)?(?P<target>[A-Za-z_$][A-Za-z0-9_$.]*)\s*=(?![=>])"),
    left_operand=re.compile(
        rf"(?P<operand>[A-Za-z_$][A-Za-z0-9_$.]*)['\"]?\]?\s*(?:\|\||\?\?)=?\s*{JS_FALLBACK}(?![\w.])"
    ),
    advice="Use null for unset, and `??` over `||` so a legitimate 0 survives.",
)


def _normalise_fallback(raw_fallback: str) -> str:
    """Reduce every spelling of a zero-like fallback to "0" or an empty-string marker."""
    return "0" if raw_fallback.lstrip("-").startswith(("0", "0.")) else '""'


def coalesce_names(line: str, patterns: ZeroCoalescePatterns) -> tuple[str, ...]:
    """Every name this line's coalesce is known by: what it lands in, and what it reads.

    Both matter to the guard exemption, because a subsequent branch may test EITHER:

        string cpr = linkedUser.NationalId ?? "";   // reads NationalId
        if (string.IsNullOrWhiteSpace(cpr))          // guards cpr

    Looking only at the reported name (`NationalId`, the one that makes it a finding)
    missed that guard entirely and reported correct code in DonorLink's SKAT export.
    """
    target_match = patterns.assign_target.search(line)
    operand_match = patterns.left_operand.search(line)
    return tuple(
        name
        for name in (
            target_match.group("target") if target_match else "",
            operand_match.group("operand") if operand_match else "",
        )
        if name
    )


def find_zero_like_sentinel(line: str, patterns: ZeroCoalescePatterns) -> Optional[tuple[str, str, str]]:
    """(name, fallback, why) for a zero-like sentinel on this line, or None.

    A zero-like fallback is only a lie where the value cannot legitimately BE zero-like:
    on an identity it writes a dangling FK and `WHERE TenantId = 0` returns an empty set
    instead of throwing; on a money amount it records a free transaction; on an external
    reference it makes the next `if (reference)` take the not-found branch. A display name
    or a subject line is none of those, and is left alone.

    The aggregate exemption comes first because a reduction over an empty set genuinely IS
    zero -- `Sum(lines) ?? 0m` on no rows is not a sentinel at all.
    """
    coalesce_match = patterns.coalesce.search(line)
    if coalesce_match is None or AGGREGATE_CALL.search(line):
        return None

    fallback = _normalise_fallback(coalesce_match.group("fallback"))
    target_match = patterns.assign_target.search(line)
    assigned_to = target_match.group("target") if target_match else ""

    for name in coalesce_names(line, patterns):
        # An EXTERNAL REFERENCE is only reported where THIS NAME is what the line binds.
        #
        # The reference rule's harm is specific and stated: `""` is forbidden because the
        # next `if (reference)` silently takes the not-found branch -- which requires the
        # value to be held in something later tested. Two situations are therefore not it:
        #
        #   rendering    `new XElement("Ref", x ?? "")`, `sb.Append((x ?? "").PadRight(30))`
        #                -- a PRESENTATION boundary, where empty is the correct spelling of
        #                absent because a CSV column has no null to write.
        #   comparing    `result = result.Where(d => (d.ExternalReference ?? "").Contains(q))`
        #                -- the line assigns `result`, not the reference. Testing merely
        #                "does this line assign anything?" called that bound and reported it.
        #
        # Measured on DonorLink: 12 findings across four export formatters and one filter
        # predicate, every one of them correct code.
        #
        # IDENTITY is deliberately NOT narrowed this way. An identity flowing into a bare
        # call is the documented failure itself: `Find(tenantId ?? 0)` issues
        # `WHERE TenantId = 0`, which returns an empty set instead of throwing.
        if fallback == '""' and not is_identity_name(name) and name != assigned_to:
            continue

        kind = zero_like_sentinel_kind(name, fallback)
        if kind:
            return name, fallback, kind
    return None


# How far past the coalesce to look for a guard on the same name. The guard is a
# normalise-then-branch idiom, so in practice it lands within a line or two; six code
# lines covers the observed cases with room to spare without letting an unrelated later
# comparison excuse an unguarded assignment.
GUARD_LOOKAHEAD_LINES = 6

# How far ABOVE the coalesce to look. Shorter than the forward window on purpose: a guard
# that governs the line reads immediately above it (`if (empty) x = y ?? "";`, or the
# condition of the ternary the coalesce sits in), whereas the forward case usually has a
# branch body in between.
GUARD_LOOKBEHIND_LINES = 2


def _guards_against_sentinel(names: tuple[str, ...], fallback: str, following: list[str]) -> bool:
    """True when the code branches on `name` being the zero-like value it just defaulted to.

    This is the normalise-then-guard idiom, and it is CORRECT code rather than a sentinel:

        $series_id = (string) ($post['meta']['series'] ?? '');
        $is_hub    = $series_id !== '' && $series_part === null;   // <- distinguishes

    The rule exists because "nobody chose" and "somebody chose this" become
    indistinguishable once a zero-like value is stored. A guard on that exact value
    restores the distinction *before* anything is stored, so the harm the rule prevents
    cannot occur. Read literally the assignment still looks like a sentinel, which is why
    three correct call sites in jacks_corner (two validators in build_index.py, one
    template) were reported before this exemption existed.

    Deliberately narrow: it wants the SAME name compared against the SAME zero-like
    literal, or a truthiness test on that name. Any of those is a real branch on absence.

    NOTE THE ASYMMETRY WITH payload-default, which does NOT get this exemption. There the
    value can legitimately BE zero, so `if amount == 0` cannot tell "the key was missing"
    from "the amount really was zero" and the guard recovers nothing. Here the name can
    never legitimately be zero-like -- that is the entire premise of unset-not-zero -- so
    the comparison is unambiguous. The exemption is sound precisely where the rule is.
    """
    zero_literal = r"0" if fallback == "0" else r"(?:''|\"\"|string\.Empty)"

    for name in names:
        bare = re.escape(name.lstrip("$").split(".")[-1])
        guard = re.compile(
            # x == "" / x != '' / x === 0 / x > 0  (either operand order)
            rf"\$?\b{bare}\b\s*(?:[!=]==?|[<>]=?)\s*{zero_literal}"
            rf"|{zero_literal}\s*(?:[!=]==?|[<>]=?)\s*\$?\b{bare}\b"
            # if (!x) / if not x / if (x) / while (x)
            rf"|\b(?:if|elif|while|and|or)\b[^\n]*?[(\s!]\$?\b{bare}\b\s*[):,\s]"
            # C# / PHP emptiness helpers that test exactly this distinction
            rf"|(?:IsNullOrEmpty|IsNullOrWhiteSpace|array_key_exists|isset)\s*\([^)]*\$?\b{bare}\b"
        )
        if any(guard.search(later) for later in following):
            return True

    return False


def check_zero_coalesce(path: Path, lines: list[str], patterns: ZeroCoalescePatterns) -> Iterable[Violation]:
    code_lines = list(iter_code_lines(lines, path.suffix))

    for index, (line_number, line) in enumerate(code_lines):
        found = find_zero_like_sentinel(line, patterns)
        if not found:
            continue

        name, fallback, kind = found
        # Look BOTH ways. Normalise-then-guard is the common order, but guard-then-fill is
        # the same idiom inverted and just as correct:
        #
        #     if (string.IsNullOrEmpty(promoteNationalId))
        #         promoteNationalId = IdValue(NationalId) ?? "";     // fill only if empty
        #
        #     private string Truncated => IsNullOrEmpty(id) || id.Length < 12
        #         ? (id ?? "")                                       // guarded by the ternary
        #         : $"{id[..7]}…{id[^4..]}";
        #
        # A forward-only window reported both. The backward window is deliberately shorter:
        # a guard that applies reads immediately above, whereas the forward case often has
        # the branch body between.
        nearby = [
            text
            for _, text in code_lines[max(0, index - GUARD_LOOKBEHIND_LINES) : index]
            + code_lines[index + 1 : index + 1 + GUARD_LOOKAHEAD_LINES]
        ]
        if _guards_against_sentinel(coalesce_names(line, patterns), fallback, nearby):
            continue

        # Indexed into the RAW lines, not `code_lines`: iter_code_lines drops comments,
        # which is precisely where the marker is written.
        if line_exemption_reason(lines, line_number - 1, "unset-not-zero"):
            continue

        shown = "0" if fallback == "0" else "the empty string"
        yield Violation(
            path=path,
            line=line_number,
            rule="unset-not-zero",
            message=(
                f"'{name}' falls back to {shown}, but it holds {kind} and so can never "
                f"legitimately be {shown} -- once stored, 'nobody chose' and 'somebody "
                f"chose this' are indistinguishable. {patterns.advice}"
            ),
        )


def check_payload_default(path: Path, lines: list[str], patterns: ZeroCoalescePatterns) -> Iterable[Violation]:
    """Flag a money amount read from an external payload by literal key and defaulted.

    Deliberately NOT about null-versus-zero. A money amount can legitimately be zero -- a
    zero-amount Stripe charge verifying a payment flow, an empty wallet -- which is exactly
    why zero cannot double as the marker for "the key was missing". The two become
    indistinguishable, and the ledger row that results looks entirely legitimate.

    So the trigger is the *literal key read*, not the value: `session.get("amount_subtotal")
    or 0` substitutes a confident number for an API contract surprise. Scoped to money
    names because that is where a wrong default costs money; a missing display name
    defaulting to "" is genuinely fine. Identity and reference names are covered by
    unset-not-zero regardless of where they came from, so they are skipped here to keep one
    finding per line.
    """
    for line_number, line in iter_code_lines(lines, path.suffix):
        coalesce_match = patterns.coalesce.search(line)
        if coalesce_match is None or AGGREGATE_CALL.search(line):
            continue

        payload_keys = external_keys_read(line)
        if not payload_keys:
            continue

        target_match = patterns.assign_target.search(line)
        operand_match = patterns.left_operand.search(line)
        # The literal key counts as a candidate name: `value = row["amount"] or 0` says
        # money only in the key, and the local it lands in says nothing at all.
        candidates = [
            target_match.group("target") if target_match else "",
            operand_match.group("operand") if operand_match else "",
            *payload_keys,
        ]
        if any(name and zero_like_sentinel_kind(name, "0") for name in candidates):
            continue  # unset-not-zero already reports this line

        flagged = next((name for name in candidates if name and is_money_amount_name(name)), None)
        if flagged:
            yield Violation(
                path=path,
                line=line_number,
                rule="payload-default",
                message=(
                    f"'{flagged}' is read from an external payload by literal key and "
                    f"defaulted when the key is absent. A missing key is a contract "
                    f"surprise, not an amount -- and because a real amount CAN be 0 (a "
                    f"zero-amount verification charge, an empty wallet), the two become "
                    f"indistinguishable. Index it so a surprise fails loudly, or handle "
                    f"the absence explicitly."
                ),
            )

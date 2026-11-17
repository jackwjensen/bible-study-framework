#!/usr/bin/env python3
"""Shared vocabulary for the standards checks: what a violation is, and what is in scope.

One module per reason-to-change. Nothing here is language-specific:

    standards_core.py        what a violation IS, the shared name/money/date vocabulary,
                             the file-length rule
    standards_code_lines.py  which lines of a file are code rather than prose
    standards_config.py      what a repo may tune in .standards.json, and the defaults
    standards_exemptions.py  how a file declares a smoke-alarm rule does not apply to it
    standards_scope.py       which files are in scope at all
    standards_checks.py      what counts as a violation in each language (the dispatcher)
    standards_sentinels.py   unset-not-zero + payload-default
    standards_compose.py     the deployment contract (ports, names, deploy gate)
    standards_query.py       query-shape: an ORM query big enough to want a view
    standards_docs.py        the documentation dimension (docs/ structure and links)
    standards_markdown.py    the markdown/tree primitives the docs modules share
    standards_symbols.py     docs-stale-symbol: the symbol-resolution engine
    standards_userdocs.py    the shipped user-docs system (registry, help links, routes)
    standards_python_support.py      the Python floor: every declaration clears PYTHON_FLOOR
    standards_python_consistency.py  and they all name the SAME version as each other
    check-source-limits.py   the driver: walking, the baseline ratchet, reporting, CLI

Adding a language touches standards_checks.py; adding a rule adds a module beside it.
`/apply-standards` copies ALL of them -- a partial copy is an ImportError at commit time.

Source of truth: engineering-standards/engineering_standards/standards_core.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Optional

# Re-exported for the same reason as CheckConfig above: eighteen modules import
# `iter_code_lines` from here, and the code-line walk moved out on 2026-09-09 only because
# fixing it properly took this file past the 500-line limit it ships to everyone else.
from standards_code_lines import (  # noqa: F401  (re-export)
    BLOCK_COMMENT_DELIMITERS,
    LINE_COMMENT_PREFIXES,
    iter_code_lines,
)

# Re-exported so every existing `from standards_core import CheckConfig` keeps working.
# The split is an internal seam, not an interface change: making forty importers move
# would have turned a file-length fix into an estate-wide rename with nothing to gain.
from standards_config import (  # noqa: F401  (re-export)
    DEFAULT_CLAUDE_MD_MAX_LINES,
    DEFAULT_MAX_FILE_LINES,
    DEFAULT_QUERY_MAX_LINES,
    CheckConfig,
    UserDocsConfig,
)

# `exemption_reason` is a RE-EXPORT: standards_query.py imports it from here, so it is used
# without appearing in this file. ruff's F401 autofix removed it on 2026-09-02 and broke
# collection in fifteen suites -- the noqa is what makes the re-export survive the fix.
from standards_exemptions import exemption_reason, file_length_exemption  # noqa: F401

# Prefixes required on boolean members by the naming standard.
BOOLEAN_PREFIXES = ("Is", "Has", "Can", "Should")

# The prefix test, which is NOT `name.startswith(BOOLEAN_PREFIXES)`. That version answered
# wrongly in both directions, and measurably:
#
# * FALSE POSITIVES on every private field, because C# does not name those in PascalCase.
#   `_isFormValid` fails on the leading underscore and `canManage` on the lowercase c --
#   measured across InvoTrack and DonorLink, 88 correctly-named members would have been
#   reported the moment the rule stopped being public-only. Flagging the code that already
#   follows the rule is the fastest way to teach people the gate is noise.
# * FALSE NEGATIVES on names that merely begin with those letters: `Issued`, `Issuing`,
#   `Island` and `Cancelled` all pass `startswith` today while asserting nothing. Requiring
#   the next character to be uppercase (or a digit/underscore) is what separates the prefix
#   `Is` + `Active` from the word `Issued`.
#
# Measured cost of the stricter half on existing public members: zero in both repos.
#
# The first letter is spelled as a two-case class INSTEAD of passing re.IGNORECASE, which
# would have quietly broken the rule: IGNORECASE applies to character classes too, so the
# `[A-Z0-9_]` lookahead would match a lowercase letter and `Issued` would pass after all --
# defeating the very half of the fix it was added for. Derived from BOOLEAN_PREFIXES so the
# prefix list stays single-source.
BOOLEAN_PREFIX_PATTERN = re.compile(
    r"^_?(?:"
    + "|".join(f"[{prefix[0]}{prefix[0].lower()}]{prefix[1:]}" for prefix in BOOLEAN_PREFIXES)
    + r")(?=[A-Z0-9_])"
)


def today_iso() -> str:
    """Today as `YYYY-MM-DD`, for the rules that compare a support date against the clock.

    One function, so every sunset table in the pack reads the same clock -- and so a test can
    hand a rule a fixed `today` and assert the sentence it produces, instead of asserting a
    sentence that changes meaning on the day a release leaves support. UTC, because the
    dates in those tables are calendar days published without a zone, and a local clock would
    flip the verdict for a few hours either side of midnight.
    """
    return datetime.now(tz=UTC).date().isoformat()


def warn_unreadable(path: Path, error: OSError, consequence: str) -> None:
    """Name a file the scanner could not read, and say what that costs.

    WHY THIS EXISTS. Every reader in this pack has to answer the same question when a file
    will not open, and the honest answer is almost never "block": an unreadable file is rare,
    usually a permission or a lock, and refusing every commit over one would be worse than
    the thing it guards. So each reader returns a "nothing" value and carries on.

    The DANGER is that "nothing" is indistinguishable from "nothing found". A docs page that
    cannot be read becomes an empty page and passes every documentation rule; an unreadable
    `.gitignore` passes gitignore-missing; a declaration that drops out of a comparison makes
    the repo look consistent. Each is a rule reporting `clean` about a file it never saw --
    the failure this pack is built against, arriving through the error path instead of the
    happy one. A sweep on 2026-09-04 found nine such readers, none of which said anything.

    So the silence is broken here rather than the flow: stderr, naming the file AND the
    consequence, so a reader can tell a skip from a pass without reading the source. Exactly
    what verify.py does for a missing toolchain, one layer down.

    NOT an exception and NOT a Violation. A Violation would be a finding about the repo,
    which this is not -- it is the scanner declaring a limit on its own reach.
    """
    print(f"warning: could not read {path}: {error} -- {consequence}", file=sys.stderr)


def has_boolean_prefix(member_name: str) -> bool:
    """Whether a boolean member's name asserts, in any of the casings C# actually uses."""
    return BOOLEAN_PREFIX_PATTERN.match(member_name) is not None


# Substrings that make a member monetary. Matched case-insensitively against the
# member name, so "TotalAmount" and "unit_price" both qualify.
#
# Substring matching is right for the money-TYPE check (a `double` named anything
# money-ish is wrong regardless of word order) but WRONG for the sentinel check, where
# it classifies `totalAttendanceInSeconds` as money because it contains "total". The
# sentinel check uses the segment-based test below instead.
MONEY_NAME_FRAGMENTS = (
    "amount",
    "price",
    "total",
    "balance",
    "vat",
    "salary",
    "fee",
    "cost",
    "subtotal",
    "discount",
    "tax",
    "payment",
    "refund",
)

# A money amount, decided by the LAST word segment of the name rather than a substring.
# `charge_amount` and `amountMinor` qualify; `totalAttendanceInSeconds` does not.
MONEY_NOUNS = frozenset(
    {
        "amount",
        "price",
        "subtotal",
        "balance",
        "vat",
        "salary",
        "fee",
        "fees",
        "cost",
        "discount",
        "tax",
        "refund",
        "refunded",
        "gross",
        "net",
        "charge",
    }
)

# Nouns that mean "a total of SOMETHING" and are money only when something else in the
# name says so. `total` and `sum` total anything: jacks_corner's `series_total` is a count
# of published parts, and flagging it as money was a real false positive. They qualify only
# with a currency unit (`total_minor`, `totalDkk`) or beside a money noun (`total_price`).
AMBIGUOUS_TOTAL_NOUNS = frozenset({"total", "totals", "sum"})

# Unit qualifiers that may trail a money noun -- `amount_minor`, `priceCents`, `total_dkk`.
# When the last segment is one of these, the test looks one segment further left.
MONEY_UNIT_QUALIFIERS = frozenset(
    {
        "minor",
        "major",
        "cents",
        "cent",
        "ore",
        "oere",
        "dkk",
        "eur",
        "usd",
        "gbp",
        "sek",
    }
)

# An external reference: a Stripe id, an idempotency key, a correlation id. The empty
# string is never a valid one, so `or ""` on these is the identity rule in another costume.
REFERENCE_NOUNS = frozenset({"reference", "ref", "key", "uuid", "guid", "token", "sku"})

# A reduction over a possibly-empty set, where zero IS the right answer -- it is the
# additive identity, not a stand-in for "unknown". Presence anywhere on the line exempts
# it, which errs toward silence, as every rule in this scanner deliberately does.
AGGREGATE_CALL = re.compile(
    r"\b(?:[Ss]um|[Cc]ount|[Aa]ggregate|reduce|len|[Aa]verage|[Aa]vg|[Mm]ax|[Mm]in)\s*\("
    r"|\.(?:Count|Length)\b|\barray_sum\s*\("
)

# A read of an external payload by LITERAL key: `session.get("amount_subtotal")`,
# `$row['amount']`, `payload["total"]`, `getattr(invoice, "subtotal", None)`.
#
# The literal key is the whole signal. It means the code is parsing data whose shape it
# does not control -- a Stripe webhook, a JSON body, a DB row, frontmatter -- where a key
# can simply be absent. A variable key (`$counts[$projectId]`) is an index into the
# program's own structure and is not this.
EXTERNAL_KEY_READ = re.compile(
    r"\.get\s*\(\s*['\"](?P<key>[^'\"]+)['\"]\s*[,)]"
    r"|\[\s*['\"](?P<bracket_key>[^'\"]+)['\"]\s*\]"
    r"|getattr\s*\([^,]+,\s*['\"](?P<attr_key>[^'\"]+)['\"]"
)


def external_keys_read(line: str) -> list[str]:
    """Every literal key this line reads out of an external payload.

    The key is checked as a NAME in its own right, because the local it lands in often says
    nothing: `amount = payload.get("x")` names the money on the left, but
    `subtotal = row["amount_subtotal"]` names it only on the right, and
    `value = row["amount"]` names it only in the key.
    """
    return [key for match in EXTERNAL_KEY_READ.finditer(line) for key in match.groups() if key]


# Earliest year a date field may hold before it needs an explicit justification.
MIN_PLAUSIBLE_YEAR = 1900


@dataclass(frozen=True)
class Violation:
    """A single standards breach, reported with enough context to fix it."""

    path: Path
    line: int
    rule: str
    message: str


@dataclass(frozen=True)
class ZeroCoalescePatterns:
    """What one language needs to spot a zero standing in for "not set".

    Three regexes rather than one, because the identity can be named on either side of the
    operator: `job.ProjectId = x ?? 0` names it left of the assignment, `y = row.ProjectId
    ?? 0` names it left of the coalesce. `advice` is the language-specific remedy, since
    the right answer differs (`??` in TypeScript, an explicit `is not None` in Python).
    """

    coalesce: re.Pattern[str]
    assign_target: re.Pattern[str]
    left_operand: re.Pattern[str]
    advice: str


def is_money_name(member_name: str) -> bool:
    lowered = member_name.casefold()
    return any(fragment in lowered for fragment in MONEY_NAME_FRAGMENTS)


def is_identity_name(member_name: str) -> bool:
    """True for names holding a record identity: ProjectId, _tenantId, client.Id, row.id.

    Deliberately case-SENSITIVE on the `Id` suffix so it matches `tenantId` and `ProjectId`
    without dragging in `Paid`, `Valid`, `Grid` or `Guid` -- all of which end in a lowercase
    "id" and none of which are identities.
    """
    last_segment = member_name.rstrip("?").split(".")[-1]
    return last_segment.endswith("Id") or last_segment.endswith("_id") or last_segment == "id"


def name_segments(member_name: str) -> list[str]:
    """The name's words, lowercased, from both snake_case and camelCase.

    `amount_minor` and `amountMinor` both become ["amount", "minor"], so one set of nouns
    serves C#, Python, PHP and JavaScript without four spellings of every rule.
    """
    bare = member_name.rstrip("?").split(".")[-1].strip("\"'[]$")
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", bare)
    return [segment.casefold() for segment in spaced.split("_") if segment]


def is_money_amount_name(member_name: str) -> bool:
    """True for a name holding a single money amount, by its last word segment.

    Segment-based, not substring-based, so `charge_amount` and `amountMinor` qualify while
    `totalAttendanceInSeconds` does not -- it is a duration that happens to start with a
    money word, and flagging it was the false positive that motivated this test.
    """
    segments = name_segments(member_name)
    if not segments:
        return False
    if segments[-1] in MONEY_NOUNS:
        return True

    # A currency unit makes the segment to its left count, and makes a bare "total" or
    # "sum" unambiguous: `total_minor` is money, `series_total` is not.
    if segments[-1] in MONEY_UNIT_QUALIFIERS and len(segments) >= 2:
        return segments[-2] in MONEY_NOUNS or segments[-2] in AMBIGUOUS_TOTAL_NOUNS

    # `total_price` / `sumOfFees`: an ambiguous total sitting beside a real money noun.
    if segments[-1] in AMBIGUOUS_TOTAL_NOUNS:
        return any(segment in MONEY_NOUNS for segment in segments[:-1])

    return False


def is_reference_name(member_name: str) -> bool:
    """True for a name holding an external reference -- a Stripe id, an idempotency key."""
    segments = name_segments(member_name)
    return bool(segments) and segments[-1] in REFERENCE_NOUNS


def zero_like_sentinel_kind(member_name: str, fallback: str) -> Optional[str]:
    """Why `member_name` may not fall back to `fallback`, or None if it legitimately may.

    Two kinds of name cannot hold a zero-like value:

      identity   -- neither 0 nor "". There is no record 0 and no record "".
      reference  -- not "". An external id is never the empty string, and code that then
                    tests `if reference:` silently takes the not-found branch.

    MONEY IS DELIBERATELY NOT HERE, and that is a reversal of an earlier version of this
    check. A money amount can legitimately BE zero -- a zero-amount Stripe charge used to
    verify a payment flow, an empty wallet, a sum over no rows -- so a money field usually
    should not be nullable at all: 0 is its genuine domain default, and `?? 0` on money is
    normally correct code rather than a sentinel.

    That does not make the webhook bug that prompted this go away, it relocates it. Writing
    `session.get("amount_subtotal") or 0` is wrong because a MISSING KEY in an external
    payload is a contract surprise, not a value -- and it is wrong regardless of whether
    the substituted value is 0 or None. That belongs to the payload-default rule, which
    keys on where the data came from rather than on null-versus-zero.

    Everything else legitimately may default: a display name, a subject line, a
    description. For those, "absent" and "empty" render identically and the distinction
    buys nothing.
    """
    if is_identity_name(member_name):
        return "an identity"
    if fallback != "0" and is_reference_name(member_name):
        return "an external reference"
    return None


def check_file_length(
    path: Path,
    lines: list[str],
    config: CheckConfig,
    baseline: dict[str, int],
    repo_root: Path,
) -> Iterable[Violation]:
    """Flag oversized files, ratcheting against the baseline.

    A file recorded in the baseline is grandfathered at its recorded size: it may shrink
    freely but fails the moment it grows. Files absent from the baseline must meet the
    limit outright, so new code complies from day one.

    A file carrying a justified `standards: file-length exempt` marker is neither: it has
    been looked at and found to be one cohesive thing. See file_length_exemption.
    """
    line_count = len(lines)
    if line_count <= config.max_file_lines:
        return

    if file_length_exemption(lines) is not None:
        return

    relative = path.relative_to(repo_root).as_posix()
    baselined_count = baseline.get(relative)

    if baselined_count is None:
        yield Violation(
            path=path,
            line=config.max_file_lines + 1,
            rule="file-too-long",
            message=(
                f"{line_count} lines exceeds the {config.max_file_lines}-line limit. "
                f"Split it -- where to cut, and when to exempt instead, is "
                f".claude/rules/refactoring.md, which is gate-scoped: read it here rather "
                f"than assuming you have it."
            ),
        )
        return

    if line_count > baselined_count:
        yield Violation(
            path=path,
            line=baselined_count + 1,
            rule="baseline-grew",
            message=(
                f"{line_count} lines, up from a baselined {baselined_count}. This file is "
                f"already over the {config.max_file_lines}-line limit and may not grow "
                f"further -- extract the new code instead, or shrink the file and re-run "
                f"with --write-baseline."
            ),
        )

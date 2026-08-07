#!/usr/bin/env python3
"""Shared vocabulary for the standards checks: what a violation is, and what is in scope.

Third file of the trio, split off when standards_checks.py crossed its own 500-line limit.
The seam is real -- these change for different reasons:

    standards_core.py     what a violation IS, which files are in scope, file length
    standards_checks.py   what counts as a violation in each language
    check-source-limits.py  the driver: walking, the baseline ratchet, reporting, CLI

Adding a language or a rule touches only the middle one. Nothing here is language-specific.

Source of truth: engineering-standards/scripts/standards_core.py
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_MAX_FILE_LINES = 500

# An in-file declaration that this file is long for a reason refactoring cannot improve.
# The reason is captured and required, because "exempt" on its own is a shrug and the
# whole point is that somebody looked. Matched case-insensitively, and the marker may sit
# behind any comment syntax (`#`, `//`, `*`, or bare inside a docstring), so one pattern
# serves every language the scanner reads.
#
# Matched per LINE, never against the joined header. A single DOTALL regex with a
# "run to the next blank line" terminator looked equivalent and was not: where the reason
# is the last thing in a docstring there IS no following blank line, so the capture ran on
# through the closing delimiter and into the code. Every reason then measured hundreds of
# characters, and `exempt -- it is fine` passed the length floor that exists to reject
# exactly that.
FILE_LENGTH_EXEMPTION = re.compile(
    r"standards:\s*file-length\s+exempt\s*[-–—:]{1,2}\s*(?P<reason>.*)",
    re.IGNORECASE,
)

# Openers and closers that end the reason: the marker's prose stops at the end of its
# comment block, never at the first thing that happens to follow it.
REASON_TERMINATORS = ('"""', "'''", "*/", "-->", "*@")

# Comment syntax stripped from a continuation line before it is appended to the reason.
REASON_LINE_PREFIXES = ("#", "//", "*", "--")

# Only the header is searched. A marker buried at line 900 would be invisible to anyone
# opening the file, which defeats the point of putting it in the file at all.
HEADER_SCAN_LINES = 40

# Shorter than this is not an argument. Tuned so "declarative schema" alone fails and
# "declarative ORM schema; length tracks table count, not responsibility" passes.
MIN_EXEMPTION_REASON_LENGTH = 30

# Prefixes required on boolean members by the naming standard.
BOOLEAN_PREFIXES = ("Is", "Has", "Can", "Should")

# Substrings that make a member monetary. Matched case-insensitively against the
# member name, so "TotalAmount" and "unit_price" both qualify.
#
# Substring matching is right for the money-TYPE check (a `double` named anything
# money-ish is wrong regardless of word order) but WRONG for the sentinel check, where
# it classifies `totalAttendanceInSeconds` as money because it contains "total". The
# sentinel check uses the segment-based test below instead.
MONEY_NAME_FRAGMENTS = (
    "amount", "price", "total", "balance", "vat", "salary", "fee",
    "cost", "subtotal", "discount", "tax", "payment", "refund",
)

# A money amount, decided by the LAST word segment of the name rather than a substring.
# `charge_amount` and `amountMinor` qualify; `totalAttendanceInSeconds` does not.
MONEY_NOUNS = frozenset({
    "amount", "price", "subtotal", "balance", "vat", "salary", "fee", "fees",
    "cost", "discount", "tax", "refund", "refunded", "gross", "net", "charge",
})

# Nouns that mean "a total of SOMETHING" and are money only when something else in the
# name says so. `total` and `sum` total anything: jacks_corner's `series_total` is a count
# of published parts, and flagging it as money was a real false positive. They qualify only
# with a currency unit (`total_minor`, `totalDkk`) or beside a money noun (`total_price`).
AMBIGUOUS_TOTAL_NOUNS = frozenset({"total", "totals", "sum"})

# Unit qualifiers that may trail a money noun -- `amount_minor`, `priceCents`, `total_dkk`.
# When the last segment is one of these, the test looks one segment further left.
MONEY_UNIT_QUALIFIERS = frozenset({
    "minor", "major", "cents", "cent", "ore", "oere", "dkk", "eur", "usd", "gbp", "sek",
})

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
    return [
        key
        for match in EXTERNAL_KEY_READ.finditer(line)
        for key in match.groups()
        if key
    ]

SOURCE_SUFFIXES = (
    ".cs", ".razor", ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".php",
)

# Which files are in scope is this module's job, so the predicate stays here even though
# the compose RULES live in standards_compose.py -- that module imports it, and moving it
# there instead would make should_check depend on the rules it is supposed to gate.
#
# Matched by filename rather than by suffix so that putting ".yml" in scope does not
# silently drag in every CI workflow, Kubernetes manifest and OpenAPI spec in the tree.
COMPOSE_FILENAME = re.compile(r"^docker-compose[.\w-]*\.ya?ml$", re.IGNORECASE)

# The JavaScript family, where one set of regexes serves every member. TypeScript is in
# here too: the sentinel traps are runtime behaviour, identical with or without types.
SCRIPT_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

# Paths never checked: machine-generated, vendored, or build output.
EXCLUDED_PATH_FRAGMENTS = (
    "/obj/", "/bin/", "/node_modules/", "/vendor/", "/migrations/", "/.git/",
    "/dist/", "/build/", "/__pycache__/", "/.venv/", "/venv/", "/staticfiles/",
    "/wwwroot/lib/", "/.next/", "/coverage/",
)
EXCLUDED_NAME_FRAGMENTS = (".min.", ".g.", ".designer.", ".generated.")

# Earliest year a date field may hold before it needs an explicit justification.
MIN_PLAUSIBLE_YEAR = 1900

# Openers that make a line pure comment. Only whole-line comments are skipped: cutting at
# the first "//" anywhere would truncate every line containing a "https://" URL and turn
# real findings invisible.
COMMENT_LINE_PREFIXES = ("//", "*", "#")

# Multi-line comment and docstring regions, per language, as (opener, closer) pairs.
# Prose inside them routinely contains code-shaped illustrations -- the docstrings in
# standards_checks.py spell out `date.min` and a falsy-coalesce example -- so the region
# must be skipped wholesale, not line by line.
BLOCK_COMMENT_DELIMITERS = {
    ".py": (('"""', '"""'), ("'''", "'''")),
    ".cs": (("/*", "*/"),),
    ".razor": (("/*", "*/"), ("@*", "*@"), ("<!--", "-->")),
    ".ts": (("/*", "*/"),),
    ".tsx": (("/*", "*/"),),
    ".js": (("/*", "*/"),),
    ".jsx": (("/*", "*/"),),
    ".mjs": (("/*", "*/"),),
    ".cjs": (("/*", "*/"),),
    ".php": (("/*", "*/"),),
}


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


@dataclass(frozen=True)
class CheckConfig:
    """Per-repo tuning. Defaults apply when no config file is present."""

    max_file_lines: int = DEFAULT_MAX_FILE_LINES
    extra_excluded_fragments: tuple[str, ...] = ()
    check_boolean_prefixes: bool = True
    check_money_types: bool = True
    check_unset_sentinels: bool = True
    check_date_ranges: bool = True
    check_declared_types: bool = True
    check_payload_defaults: bool = True
    check_compose_conventions: bool = True

    @staticmethod
    def load(config_path: Optional[Path]) -> "CheckConfig":
        if config_path is None or not config_path.is_file():
            return CheckConfig()

        raw = json.loads(config_path.read_text(encoding="utf-8"))
        return CheckConfig(
            max_file_lines=raw.get("maxFileLines", DEFAULT_MAX_FILE_LINES),
            extra_excluded_fragments=tuple(raw.get("excludePathFragments", [])),
            check_boolean_prefixes=raw.get("checkBooleanPrefixes", True),
            check_money_types=raw.get("checkMoneyTypes", True),
            check_unset_sentinels=raw.get("checkUnsetSentinels", True),
            check_date_ranges=raw.get("checkDateRanges", True),
            check_declared_types=raw.get("checkDeclaredTypes", True),
            check_payload_defaults=raw.get("checkPayloadDefaults", True),
            check_compose_conventions=raw.get("checkComposeConventions", True),
        )


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


def is_comment_line(line: str) -> bool:
    """True when the whole line is a single-line comment."""
    return line.lstrip().startswith(COMMENT_LINE_PREFIXES)


def iter_code_lines(lines: list[str], suffix: str) -> Iterable[tuple[int, str]]:
    """Yield (line_number, line) for lines that are actually code.

    Comments and docstrings are skipped, including multi-line regions. Without this the
    scanner reports its own documentation: prose explaining a rule necessarily contains an
    example of the thing the rule forbids.
    """
    delimiters = BLOCK_COMMENT_DELIMITERS.get(suffix, ())
    awaiting_closer = ""

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()

        if awaiting_closer:
            if awaiting_closer in stripped:
                awaiting_closer = ""
            continue

        opener_pair = next((pair for pair in delimiters if stripped.startswith(pair[0])), None)
        if opener_pair is not None:
            opener, closer = opener_pair
            # A region that also closes on this line needs no state. Where opener and
            # closer are identical ("""), closing takes a second occurrence.
            required_closers = 2 if opener == closer else 1
            if stripped.count(closer) < required_closers:
                awaiting_closer = closer
            continue

        if is_comment_line(line):
            continue

        yield line_number, line


def should_check(path: Path, repo_root: Path, config: CheckConfig) -> bool:
    """Decide whether a file is in scope.

    Exclusions are matched against the REPO-RELATIVE path, never the absolute one: a repo
    that happens to live under a directory called "build" or "dist" would otherwise have
    every one of its files silently skipped.
    """
    if path.suffix not in SOURCE_SUFFIXES and not COMPOSE_FILENAME.match(path.name):
        return False

    lowered = "/" + path.relative_to(repo_root).as_posix().casefold() + "/"

    excluded = EXCLUDED_PATH_FRAGMENTS + config.extra_excluded_fragments
    if any(fragment.casefold() in lowered for fragment in excluded):
        return False

    return not any(fragment in path.name.casefold() for fragment in EXCLUDED_NAME_FRAGMENTS)


def file_length_exemption(lines: list[str]) -> Optional[str]:
    """The stated reason this file is allowed to exceed the limit, or None.

    The size limit is a SMOKE ALARM, not a spec: it exists to find files that have
    accumulated a second responsibility. Some files are long for a reason that
    refactoring cannot improve -- a declarative schema whose length tracks the number of
    entities, a lookup table, a translation map, a parser whose grammar is one thing.
    Splitting those scatters one subject across two files that must always be edited
    together, which refactoring.md is explicit is WORSE than the long file.

    This is deliberately NOT a baseline. A baseline is silent debt that someone intends
    to pay down; this is a decision, and it differs in three ways that matter:

      * it lives IN the file, so it is read by anyone opening it and cannot drift from
        the code the way a separate ledger does;
      * it REQUIRES a reason, long enough to be an argument rather than a shrug;
      * the scanner counts it and prints the total, so an exemption is never invisible.

    Write it anywhere in the file's first HEADER_SCAN_LINES lines -- the module
    docstring or the top comment block:

        standards: file-length exempt -- declarative ORM schema; length tracks the
        number of tables, not accumulated responsibility.

    If you cannot write a reason you would defend in review, that is the rule working.
    Split the file.
    """
    header = lines[:HEADER_SCAN_LINES]
    marker_index = next(
        (index for index, line in enumerate(header) if FILE_LENGTH_EXEMPTION.search(line)),
        None,
    )
    if marker_index is None:
        return None

    match = FILE_LENGTH_EXEMPTION.search(header[marker_index])
    parts = [match.group("reason")] if match else []

    # A reason routinely wraps. Keep taking lines until the comment block ends, so the
    # length floor measures the whole argument rather than only its first line.
    for line in header[marker_index + 1:]:
        stripped = line.strip()
        if not stripped or any(end in stripped for end in REASON_TERMINATORS):
            break
        for prefix in REASON_LINE_PREFIXES:
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):].strip()
                break
        parts.append(stripped)

    reason = " ".join(" ".join(parts).split())
    return reason if len(reason) >= MIN_EXEMPTION_REASON_LENGTH else None


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
                f"Split it -- see .claude/rules/refactoring.md."
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

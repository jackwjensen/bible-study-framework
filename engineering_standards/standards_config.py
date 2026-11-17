#!/usr/bin/env python3
"""What a repo is allowed to tune, and the defaults when it tunes nothing.

Split out of standards_core.py on 2026-08-25, when adding the `python-consistency` flag put
that module at 503 of its own 500 lines. The seam is the subject that grew: this file changes
when a rule gains a knob or a threshold moves, while the rest of core changes when the shared
name/money/date vocabulary does -- two different reasons, two different readers.

The alternative was to shave a comment back under the limit, which is what the pack's own
CLAUDE.md names as the wrong answer every time it has come up. The limit exists to get
oversized files split, not to get their explanations deleted.

IMPORT DIRECTION: standards_core imports FROM here and re-exports, never the reverse. Nothing
in this module may import standards_core, or the re-export becomes a cycle. That is also why
the DEFAULT_ constants moved with the dataclasses rather than staying behind -- they are the
defaults these fields take, and splitting a value from its only consumer is how the two drift.

Source of truth: engineering-standards/engineering_standards/standards_config.py
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_MAX_FILE_LINES = 500

# The ceiling for a repo's CLAUDE.md: constitution and index, never encyclopedia. Sized so
# constraints, commands and one pointer line per docs page fit, and an architecture
# narrative does not -- the narrative belongs in docs/, where every reader can find it.
# The judgment half is .claude/rules/documentation.md; the check is in standards_docs.py.
DEFAULT_CLAUDE_MD_MAX_LINES = 150

# A BACKSTOP only. query-shape fires primarily on STRUCTURE (see standards_query.py) --
# a six-line aggregate across three tables wants a view and no size threshold would ever
# catch it, while a twenty-line flat projection is merely wide. Size survives for the
# pathologically long query that is view-shaped in no way the regexes can name.
#
# Calibrated against 209 real query chains across InvoTrack, DonorLink, allegro-it-services,
# sourcetext.ai and jacks_corner: median 4 lines, p90 9, p99 15, max 25. Nothing in the
# corpus reaches 30, which is the point -- as a backstop it should be quiet.
DEFAULT_QUERY_MAX_LINES = 30

# The ceiling for one `claude/rules/*.md` file. The same number as a source file, on purpose:
# the threshold is answering the same question in both places -- has this file taken on a
# second subject -- and two different numbers for one question is a thing to remember for no
# benefit. The judgment half is claude/rules/documentation.md; the check is standards_rules.py.
DEFAULT_RULES_MAX_LINES = 500

# The ceiling for the always-loaded judgment layer as a WHOLE, in lines.
#
# THIS IS THE ONE NUMBER THAT MAPS TO THE ACTUAL COST. Per-file length cannot see ten small
# always-load files, and ten small files dilute a session exactly as much as one large one.
#
# Calibrated 2026-08-27 against the pack's own set. It had been 1,661 lines across nine files
# -- roughly 24,000 tokens in every session in every adopted repo, a number nobody had ever
# chosen. Four reductions brought it to 1,218: scoping migrations.md and ui-standards.md with
# `paths:`, promoting `paged-without-order` into the scanner, moving the Laravel section into
# the (scoped) PHP rules, and paying down prose that a docs page or a module docstring could
# hold.
#
# THE NUMBER IS DELIBERATELY NOT ROUND. It is where the work actually landed plus enough for
# one honest edit, and no more. Rounding up would hand back most of the headroom the reduction
# just bought, which is how a ratchet becomes a rubber stamp; setting it at exactly the
# measured total would fail the next one-line clarification and teach people to raise the
# number reflexively. The next rule that wants always-load space has to pay for it by scoping,
# promoting or trimming something -- which is the entire mechanism.
#
# Second pass, 2026-09-10: 1,225 to 600, prompted by the first pass's own headroom running
# out -- the set had grown back to exactly 1,225, one clarification from failing in the repo
# that carries no baseline. Four moves, and the ratchet is why they were available at all:
#   - SPLIT documentation.md (214) into an always-loaded constitution (74) and the
#     `paths:`-scoped docs-authoring.md (170). Most of that file was the contract for writing
#     a page, which a session that never opens docs/ was paying for anyway.
#   - MOVED the .NET-only prose out of two language-neutral files into the path-scoped C#
#     rules: the `var`-removal hazard from naming.md, the IQueryable projection trap and the
#     BodyWriter detail from data-access.md.
#   - DELETED the Laravel lazy-loading bullet from data-access.md. php-standards.md had held
#     the full version since the first pass; docs/judgment-layer.md recorded it as MOVED, and
#     it had been copied -- the always-loaded layer was carrying the duplicate.
#   - SCOPED refactoring.md, data-access.md and naming.md (Jack, 2026-09-10), the three files
#     the pass had argued were unscopeable. They are language-neutral, and that turned out to
#     be the wrong question: what decides it is whether a GATE can send a reader here. All
#     three sit on rules a scanner detects, so the finding is the trigger and the file does
#     not have to be carried in advance. That criterion is now in docs/judgment-layer.md, and
#     it is why this number more than halved rather than shaving a few lines.
DEFAULT_RULES_CONTEXT_BUDGET = 610

# How long ONE verification gate may run before it is killed, in seconds. A HANG GUARD, not a
# performance budget: its job is to stop a watch-mode script or a wedged container command
# from holding a push hostage forever, so it only has to be comfortably above any honest run.
#
# Why it is tunable at all: a gate's honest duration is a fact about the REPO, not about the
# pack -- sourcetext.ai's pytest builds a three-corpus site and crossed 900 s on a slow
# morning (2026-11-11), failing three pushes whose suite was green. The repo made its suite
# faster first; the knob exists for the day the suite is honestly slow. Raising it is a
# WEAKENING (a hung gate blocks a push for longer before the guard says so), so it carries a
# written reason like every other relaxation; lowering it is free. The value is read by
# `standards_gate_config.gate_timeout_seconds`, which refuses zero and negatives -- a zero
# timeout fails every gate, which is the whole gate switched off backwards. The key lives
# beside the default (rather than in standards_gate_config, which reads it) because this
# module may not import that one: WEAKENINGS below names the key too, and the gate config
# already imports the default from here.
DEFAULT_GATE_TIMEOUT_SECONDS = 900
GATE_TIMEOUT_KEY = "gateTimeoutSeconds"


@dataclass(frozen=True)
class UserDocsConfig:
    """Where a repo's SHIPPED user-documentation system lives, declared in .standards.json.

    The pack cannot ship an in-app docs system, so it enforces the invariants of whichever
    one the repo built: a registry file enumerating every topic, help links from pages into
    topics, and the routes those pages declare. Patterns capture named groups (`slug`,
    `route`) or, failing that, group 1. The rules are in standards_userdocs.py; the model
    this generalises is InvoTrack's DocRegistry + PageHeader HelpSlug.
    """

    registry_file: str
    registry_pattern: str
    help_links: tuple[tuple[str, str], ...]
    routes: tuple[tuple[str, str], ...]
    content_glob: Optional[str] = None

    @staticmethod
    def parse(raw: Optional[dict]) -> "Optional[UserDocsConfig]":
        if not raw:
            return None
        registry = raw["registry"]
        return UserDocsConfig(
            registry_file=registry["file"],
            registry_pattern=registry["pattern"],
            help_links=tuple((entry["glob"], entry["pattern"]) for entry in raw.get("helpLinks", [])),
            routes=tuple((entry["glob"], entry["pattern"]) for entry in raw.get("routes", [])),
            content_glob=raw.get("contentGlob"),
        )


# A weakening must state a reason, at the same floor an exemption states one. Imported
# rather than repeated: the two are the same judgement about what counts as an argument,
# and a second copy is a second thing to drift. standards_exemptions imports nothing, so
# this cannot cycle.
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402  (see note above)

# Above this, `maxFileLines` is not a tuned threshold, it is the rule switched off while
# still appearing to be on. A repo that genuinely wants no size limit should say so with
# the flag, where the summary names it, rather than with a number nobody reads.
MAX_TUNABLE_FILE_LINES = 1500

# The opt-in declarations: each key TURNS a check family ON and is silent while undeclared.
# A declaration has no file the manifest can count absent, so the only surface that can ever
# report "undeclared" is a skill asking -- test_sync_pack.py therefore holds both the
# apply-standards and code-review skills to naming every key listed here. Adding a key to
# CheckConfig.parse without adding it here and to both skills fails the pack's own tests.
# (code-review names them in its reviewer brief, claude/skills/code-review/reviewers/.)
DECLARATIONS = ("userDocs", "docsRouteInventories", "mustReadResults", "userFacingExceptions")

# One `mustReadResults` entry: a dotted call name, `Session.Write` or bare `commit`. Validated
# here rather than trusted, because a typo'd entry -- `Session.Write(` -- would compile into a
# pattern that matches nothing, and a declaration that silently checks nothing is the exact
# failure a declaration exists to end. See standards_write_results.py for how it matches.
MUST_READ_ENTRY = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*")


def parse_must_read_results(raw: object) -> tuple[str, ...]:
    """The declared must-read write calls, or a SystemExit naming the malformed entry."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(entry, str) for entry in raw):
        raise SystemExit(
            'error: .standards.json "mustReadResults" must be a list of call names, e.g. ["Session.Write"].'
        )
    malformed = [entry for entry in raw if not MUST_READ_ENTRY.fullmatch(entry)]
    if malformed:
        raise SystemExit(
            f'error: .standards.json "mustReadResults" has entries that are not dotted call names: {malformed}\n'
            '  Write the receiver and method without parentheses or arguments -- "Session.Write", not\n'
            '  "Session.Write(" -- or the bare method name. A malformed entry would match nothing.'
        )
    return tuple(raw)


# `userFacingExceptions`: the repo's exception types whose message is written for the user,
# beyond the ones `technical-error-shown` already knows by name (any type containing
# `UserFacing`). Needed because the scanner reads one file at a time: InvoTrack's
# `AccountingRefusalException : UserFacingException` is declared in one file and caught in
# another, and sourcetext.ai's `AccountError` predates the naming convention. Jack, 2026-10-06.
#
# UNLIKE THE OTHER DECLARATIONS THIS ONE NARROWS A RULE -- every type listed stops being
# reported -- so it is validated against the one abuse that would switch the rule off outright:
# listing a general base type, whose message is by definition whatever the framework wrote.
USER_FACING_ENTRY = re.compile(r"[A-Za-z_]\w*")
GENERAL_EXCEPTION_TYPES = frozenset(
    {
        "Exception",
        "SystemException",
        "ApplicationException",
        "InvalidOperationException",
        "ArgumentException",
        "HttpRequestException",
        "BaseException",
        "Error",
        "TypeError",
        "ValueError",
        "RuntimeError",
        "Throwable",
        "RuntimeException",
        "LogicException",
    }
)


def parse_user_facing_exceptions(raw: object) -> tuple[str, ...]:
    """The declared user-facing exception types, or a SystemExit naming the bad entry."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(entry, str) for entry in raw):
        raise SystemExit(
            'error: .standards.json "userFacingExceptions" must be a list of type names, e.g. ["AccountError"].'
        )
    malformed = [entry for entry in raw if not USER_FACING_ENTRY.fullmatch(entry)]
    general = [entry for entry in raw if entry in GENERAL_EXCEPTION_TYPES]
    if malformed or general:
        raise SystemExit(
            f'error: .standards.json "userFacingExceptions" has entries it cannot accept: {malformed + general}\n'
            "  Write the bare class name, no namespace. List only types whose EVERY message is written\n"
            "  for the user; a general base type carries the framework's own text and would switch\n"
            "  technical-error-shown off."
        )
    return tuple(raw)


# The settings that make the pack WEAKER, and how to tell a weakening from a tightening.
# A reason is required to loosen a rule and never to tighten one -- `maxFileLines: 300`
# needs no defence, `maxFileLines: 900` does. Keyed by the JSON name so the error can quote
# what the author actually wrote.
#
# The DECLARATIONS above are deliberately absent from this table: they TURN CHECKS ON, and
# charging a repo a written reason for opting into more checking would be exactly backwards.
WEAKENINGS: dict[str, object] = {
    "maxFileLines": lambda value: value > DEFAULT_MAX_FILE_LINES,
    "queryMaxLines": lambda value: value > DEFAULT_QUERY_MAX_LINES,
    "claudeMdMaxLines": lambda value: value > DEFAULT_CLAUDE_MD_MAX_LINES,
    "excludePathFragments": bool,
    "distributesBinaries": bool,
    "checkBooleanPrefixes": lambda value: value is False,
    "checkMoneyTypes": lambda value: value is False,
    "checkUnsetSentinels": lambda value: value is False,
    "checkDateRanges": lambda value: value is False,
    "checkDeclaredTypes": lambda value: value is False,
    "checkPayloadDefaults": lambda value: value is False,
    "checkComposeConventions": lambda value: value is False,
    "checkQueryShape": lambda value: value is False,
    "checkFilenames": lambda value: value is False,
    "checkGitignore": lambda value: value is False,
    "checkDocs": lambda value: value is False,
    "checkToolchainConsistency": lambda value: value is False,
    "checkRules": lambda value: value is False,
    "checkConstants": lambda value: value is False,
    "rulesMaxLines": lambda value: value > DEFAULT_RULES_MAX_LINES,
    # The budget's ONLY escape hatch, and the reason it must be a weakening. An aggregate has
    # no file to carry an exemption marker -- the finding belongs to the set, not to whichever
    # file crossed the line -- so raising the number here is the one honest way to relax it,
    # and `collect_tuning` makes that cost a written reason printed on every run.
    "rulesContextBudget": lambda value: value > DEFAULT_RULES_CONTEXT_BUDGET,
    # A longer leash before the hang guard calls a wedged gate hung. The gate reader
    # (standards_gate_config) refuses the outright abuses -- zero, negatives, non-integers.
    GATE_TIMEOUT_KEY: lambda value: isinstance(value, int) and value > DEFAULT_GATE_TIMEOUT_SECONDS,
}


def collect_tuning(raw: dict) -> tuple[tuple[str, str, str], ...]:
    """(setting, value, reason) for every weakening the config declares.

    WHY THIS EXISTS. Every rule in this pack that can be relaxed states its reason where a
    reader will meet it: an exemption sits in the file and is printed every run, a baseline
    is a reviewed ledger, and neither can hide. `.standards.json` could do neither. One line
    -- `{"maxFileLines": 100000}` or `{"checkGitignore": false}` -- silently disabled any
    rule, required no argument, and appeared in no output, so a repo that had turned off the
    gitignore check printed the same `clean` as one that passed it honestly. That is the
    back door behind every front door this pack bolts.

    Measured before this existed: a 2000-line file and a float money field both vanished
    from the report under `{"maxFileLines": 100000, "checkMoneyTypes": false}`, and nothing
    in the output mentioned a config had been read at all.

    The estate was already doing the right thing informally -- all six `.standards.json`
    files in it carry a hand-written `_comment` justifying their exclusions. This only makes
    that reason per-setting, machine-checked, and printed, so the discipline survives the
    author who wrote it.
    """
    reasons = raw.get("reasons", {})
    if not isinstance(reasons, dict):
        raise SystemExit(
            'error: .standards.json "reasons" must be an object mapping a setting name to the reason it is tuned.'
        )

    declared: list[tuple[str, str, str]] = []
    undefended: list[str] = []

    for setting, is_weakening in WEAKENINGS.items():
        if setting not in raw:
            continue
        value = raw[setting]
        if not is_weakening(value):  # type: ignore[operator]
            continue  # a tightening needs no defence

        reason = str(reasons.get(setting, "")).strip()
        if len(reason) < MIN_EXEMPTION_REASON_LENGTH:
            undefended.append(setting)
            continue
        declared.append((setting, json.dumps(value), reason))

    if undefended:
        raise SystemExit(
            "error: .standards.json weakens "
            f"{len(undefended)} rule(s) without stating why: {', '.join(sorted(undefended))}\n"
            "\n"
            "  A rule may be relaxed -- silently is the part that is refused. Give each one a\n"
            f"  reason of at least {MIN_EXEMPTION_REASON_LENGTH} characters, the same floor an\n"
            "  in-file exemption meets, and it will be printed on every run:\n"
            "\n"
            '      "reasons": {\n'
            f'        "{sorted(undefended)[0]}": "why this repo is the exception"\n'
            "      }\n"
            "\n"
            "  A prose _comment does not count: it cannot say which setting it defends, and\n"
            "  nothing reads it."
        )

    if raw.get("maxFileLines", DEFAULT_MAX_FILE_LINES) > MAX_TUNABLE_FILE_LINES:
        raise SystemExit(
            f"error: .standards.json sets maxFileLines to {raw['maxFileLines']}, above the "
            f"{MAX_TUNABLE_FILE_LINES}-line ceiling.\n"
            "  Past that it is not a threshold, it is the size rule switched off while still\n"
            "  looking switched on. If that is the intent, say so where the summary names it."
        )

    return tuple(sorted(declared))


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
    check_query_shape: bool = True
    # Background jobs: whether a task can report its own failure, and whether the
    # result store names what ran. Tunable because a repo with no worker has no
    # opinion to express -- the rules are silent there anyway.
    check_jobs: bool = True
    check_filenames: bool = True
    check_gitignore: bool = True
    check_docs: bool = True
    # The testing dimension. Off is a real answer for a repo that is entirely declarative --
    # a content tree, a config bundle -- and, like every other weakening here, it has to
    # state a reason that gets printed on each run. It is NOT the way to defer adoption:
    # that is the baseline's job, which is visible, per-file and ratchets down.
    check_tests: bool = True
    # Covers the whole *-consistency family (Python, Node, PHP, .NET). A monorepo shipping two
    # INDEPENDENT services could legitimately run each on its own version. No repo in this
    # estate does, so the rules are repo-wide and this is the escape hatch for the first one
    # that is -- preferred over per-directory grouping, which would be a lot of machinery
    # guessing at a boundary the repo can simply state. Per-site line exemptions remain the
    # finer instrument; this flag is for a repo where the whole question does not apply.
    check_toolchain_consistency: bool = True
    # The judgment layer judged as a budget -- see standards_rules.py. On by default in every
    # repo, because the cost it measures is paid in every repo that received the rules.
    check_rules: bool = True
    # Where a value that may change is allowed to live -- see standards_config_defaults.py and
    # standards_constants.py. One flag
    # for all three shapes, because a repo that genuinely disagrees disagrees with the premise
    # rather than with one detector. Off is a real answer for a repo with no deployment of its
    # own -- a library, a shared schema package -- where "read it from the environment" names
    # an environment that does not exist.
    check_constants: bool = True
    query_max_lines: int = DEFAULT_QUERY_MAX_LINES
    claude_md_max_lines: int = DEFAULT_CLAUDE_MD_MAX_LINES
    rules_max_lines: int = DEFAULT_RULES_MAX_LINES
    rules_context_budget: int = DEFAULT_RULES_CONTEXT_BUDGET
    # (file, pattern) pairs naming where a repo enumerates its public routes -- group 1 of
    # the pattern yields the route token checked by docs-uncovered-route. Empty = no route
    # coverage checking; the other coverage surfaces need no configuration.
    docs_route_inventories: tuple[tuple[str, str], ...] = ()
    # The shipped user-docs system, if the repo declares one. None = the userdocs family
    # is silent, exactly as docsRouteInventories gates route coverage.
    user_docs: Optional[UserDocsConfig] = None
    # The persistence writes that can be refused and answer whether they saved -- see
    # standards_write_results.py. Empty = `write-result-discarded` is silent, as with userDocs:
    # which call is the must-read one is a fact about the repo that no call site states.
    must_read_results: tuple[str, ...] = ()
    # Exception types whose message is written for the user, beyond the `UserFacing*` names
    # `technical-error-shown` already recognises. See parse_user_facing_exceptions.
    user_facing_exceptions: tuple[str, ...] = ()
    # True when this repo CONVEYS a binary that links its data provider -- a desktop client, an
    # on-prem install, anything a third party receives and runs. Default False: everything in
    # this estate is a hosted service, and a hosted service distributes nothing.
    #
    # It exists for exactly one rule. `ef-provider-support` refuses Pomelo outright and cannot
    # normally be exempted, because the replacement is Oracle's provider and the only remaining
    # reason to stay was inertia. But Oracle's is GPL-2.0-only WITH Universal-FOSS-exception-1.0
    # where Pomelo is MIT, and GPLv2 obligations attach to DISTRIBUTION. For a repo that ships a
    # proprietary binary, "migrate to Oracle" stops being a maintenance instruction and becomes
    # a licensing problem, so that repo may exempt the rule -- with a written reason, as always.
    #
    # WHY A REPO-LEVEL FLAG RATHER THAN JUST ALLOWING THE LINE EXEMPTION BACK. The thing that
    # decides this is a property of the whole repo -- does anyone outside receive the binary --
    # not an opinion about one PackageReference. Stated once, in the repo's own config, it is
    # visible in review and cannot be sprinkled onto a hosted app to dodge the migration.
    distributes_binaries: bool = False
    # Every weakening this repo declared, as (setting, value, reason). Carried on the config
    # so the summary can NAME them -- a relaxation nobody sees is the back door again.
    tuning: tuple[tuple[str, str, str], ...] = ()

    @staticmethod
    def load(config_path: Optional[Path]) -> "CheckConfig":
        if config_path is None or not config_path.is_file():
            return CheckConfig()

        raw = json.loads(config_path.read_text(encoding="utf-8"))
        return CheckConfig(
            tuning=collect_tuning(raw),
            max_file_lines=raw.get("maxFileLines", DEFAULT_MAX_FILE_LINES),
            extra_excluded_fragments=tuple(raw.get("excludePathFragments", [])),
            check_boolean_prefixes=raw.get("checkBooleanPrefixes", True),
            check_money_types=raw.get("checkMoneyTypes", True),
            check_unset_sentinels=raw.get("checkUnsetSentinels", True),
            check_date_ranges=raw.get("checkDateRanges", True),
            check_declared_types=raw.get("checkDeclaredTypes", True),
            check_payload_defaults=raw.get("checkPayloadDefaults", True),
            check_compose_conventions=raw.get("checkComposeConventions", True),
            check_query_shape=raw.get("checkQueryShape", True),
            check_jobs=raw.get("checkJobs", True),
            check_filenames=raw.get("checkFilenames", True),
            check_gitignore=raw.get("checkGitignore", True),
            check_docs=raw.get("checkDocs", True),
            check_tests=raw.get("checkTests", True),
            check_toolchain_consistency=raw.get("checkToolchainConsistency", True),
            check_rules=raw.get("checkRules", True),
            check_constants=raw.get("checkConstants", True),
            query_max_lines=raw.get("queryMaxLines", DEFAULT_QUERY_MAX_LINES),
            claude_md_max_lines=raw.get("claudeMdMaxLines", DEFAULT_CLAUDE_MD_MAX_LINES),
            rules_max_lines=raw.get("rulesMaxLines", DEFAULT_RULES_MAX_LINES),
            rules_context_budget=raw.get("rulesContextBudget", DEFAULT_RULES_CONTEXT_BUDGET),
            docs_route_inventories=tuple(
                (entry["file"], entry["pattern"]) for entry in raw.get("docsRouteInventories", [])
            ),
            user_docs=UserDocsConfig.parse(raw.get("userDocs")),
            must_read_results=parse_must_read_results(raw.get("mustReadResults")),
            user_facing_exceptions=parse_user_facing_exceptions(raw.get("userFacingExceptions")),
            distributes_binaries=raw.get("distributesBinaries", False),
        )

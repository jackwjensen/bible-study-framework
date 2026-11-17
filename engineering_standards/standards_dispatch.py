#!/usr/bin/env python3
"""The dispatcher: which rules run against which file.

Split out of standards_checks.py on 2026-08-25. That module's own docstring described it as
"what each LANGUAGE looks like, plus the dispatcher", which is two jobs -- the language rules
say what a violation looks like, this says what gets asked. They change for different reasons:
adding a rule for an existing language touches only the first, and putting a NEW KIND OF FILE
in scope (`.nvmrc`, for the Node floor) touches only this one.

WHY THIS FILE IS WHERE THE BUGS LIVE. A rule that is correct but never reached is
indistinguishable from a rule that passes, and this pack has shipped that failure before --
workflows once hit an early return that ran only the deploy-gate rule. Every early `return`
below is therefore load-bearing, and the dispatcher tests go through THIS function rather than
calling a rule directly, precisely so an unreachable rule fails a test instead of going quiet.

Source of truth: engineering-standards/engineering_standards/standards_dispatch.py
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Iterable

from standards_checks import (
    CSHARP_ZERO_COALESCE,
    JS_ZERO_COALESCE,
    PHP_ZERO_COALESCE,
    PYTHON_ZERO_COALESCE,
    CheckConfig,
    Violation,
    check_csharp_dates,
    check_csharp_members,
    check_date_string_literals,
    check_file_length,
    check_filter_in_memory,
    check_generic_filename,
    check_js_dates,
    check_js_eval_interop,
    check_mojibake,
    check_payload_default,
    check_php_eval,
    check_php_members,
    check_php_strict_types,
    check_python_dates,
    check_python_members,
    check_query_shape,
    check_razor_var,
    check_zero_coalesce,
)
from standards_client_address import check_client_address
from standards_concurrency import check_concurrency
from standards_config_defaults import check_config_default
from standards_constants import check_environment_literal
from standards_cors import check_cors_wildcard
from standards_debugflag import check_debug_flag
from standards_disclosure import check_disclosure_file
from standards_dispatch_files import non_source_rules
from standards_exemption_scope import check_inert_exemption
from standards_jobs import check_job_failure_handling
from standards_paging import check_paged_order
from standards_scope import (
    SCRIPT_SUFFIXES,
)
from standards_secrets import check_committed_credentials
from standards_seed_credentials import check_seeded_credentials
from standards_sqlinjection import check_sql_injection
from standards_tests import check_test_quality
from standards_tls import check_tls_verification
from standards_user_errors import check_technical_error_shown
from standards_write_results import check_write_results


def check_migration_file(path: Path, lines: list[str]) -> Iterable[Violation]:
    """The rules that reach INTO a migrations directory, and only those.

    Everything else stays out, deliberately: a framework writes most of a migration, so the
    length, naming and structure rules would be judging generated code nobody chose. What
    belongs here is a rule whose subject is MORE dangerous inside a migration than outside
    one -- a migration runs on every environment the schema reaches, production included.
    seeded-credential-config is that: a data migration creating an administrator from an
    environment variable is the "set a password, get an admin" path on every server. Which
    files arrive here is standards_scope.should_check_migration's decision.
    """
    yield from check_seeded_credentials(path, lines)


def check_source_file(
    path: Path,
    lines: list[str],
    config: CheckConfig,
    baseline: dict[str, int],
    repo_root: Path,
) -> Iterable[Violation]:
    """Every check that applies to one file, dispatched by language."""
    # MOJIBAKE RUNS FIRST, ABOVE EVERY EARLY RETURN BELOW, AND MUST STAY HERE.
    #
    # Corruption does not care what a file is, so this rule is language-agnostic by intent --
    # the case that shipped was a Danish string literal in C#. The three early returns that
    # follow would otherwise exempt compose files, workflows and .env.example from it
    # entirely.
    #
    # They did, and that is why the call sits here. The rule was added in ec9be6b "so it runs
    # on every file in scope regardless of language", but was inserted *below* those returns,
    # where it could not. Its own verification hid the gap: "run over all ten repos: zero
    # findings" reads identically whether a rule is clean or blind. A real double-encoded
    # em-dash then sat in wappit.net's docker-compose.production.yml through a green gate
    # until it was spotted by eye on 2026-08-10 -- the exact "caught by eye, which is not a
    # control" failure this rule was written to end.
    #
    # The dispatcher cases in test_standards_encoding.py pin it. If you are about to move
    # this call back down beside the language dispatch, those tests are the reason not to.
    yield from check_mojibake(path, lines)

    # CREDENTIALS RUN BESIDE MOJIBAKE, ABOVE EVERY EARLY RETURN, AND FOR THE SAME REASON.
    # A credential does not care what kind of file it is in -- the real one was in
    # appsettings.Development.json, which reaches none of the language branches below, and the
    # next one will be in a compose file or a fixture. Every early return past this point is a
    # file type this rule would otherwise be blind to, which is exactly how the mojibake rule
    # shipped broken once already.
    yield from check_committed_credentials(path, lines)
    # Its sibling, for the same reason: the seeded-password MECHANISM lives in `.env.example`,
    # compose files and appsettings -- all past the early returns below -- as much as in source.
    yield from check_seeded_credentials(path, lines)

    # DEBUG FLAG RUNS BESIDE THE TWO ABOVE, ABOVE EVERY EARLY RETURN, AND FOR THE SAME REASON.
    # A verbose-error / debug flag turned on in production is an information-disclosure defect
    # regardless of the file it lives in -- it appears in a Django settings `.py`, a Yii entry
    # `.php`, a Laravel `config/*.php`, a `.env.example`, and a .NET `web.config` alike, and the
    # last two reach none of the language branches below. The function decides internally which
    # shape applies to this file, so it is safe to call on everything and fires on nothing it
    # does not recognise.
    yield from check_debug_flag(path, lines)

    # THE INERT-MARKER RULE RUNS BESIDE THE THREE ABOVE, AND FOR THEIR REASON. The exemption
    # marker syntax is language-agnostic by design -- `#`, `//`, `--`, `/* */`, `<!-- -->`,
    # `@* *@` -- so a stale one is likeliest exactly where the language branches below cannot
    # look: a compose file, a workflow, a markdown page. Nobody re-reads those hunting for a
    # sentence that stopped working, which is why this is the one class of file where an
    # unread marker can sit for years. See standards_exemption_scope.
    yield from check_inert_exemption(path, lines)

    # NON-SOURCE FILE TYPES ARE A TABLE, in standards_dispatch_files.py. This was an
    # if/elif chain of twelve early returns inline here, and it is what took this function to
    # cyclomatic complexity 37 against the pack's own limit of 10 -- a rule shipped to nine
    # repositories and broken in the file that ships it.
    #
    # None and an empty iterable are DIFFERENT here: a config file matches and yields nothing,
    # and must still stop rather than fall through to the length and naming rules below.
    non_source = non_source_rules(path, lines, config, repo_root)
    if non_source is not None:
        yield from non_source
        return

    yield from check_file_length(path, lines, config, baseline, repo_root)

    # BELOW the three early returns above, and unlike mojibake that is correct here. A
    # compose file, a workflow and `.env.example` all have names their tooling resolves --
    # `deploy.yml` is what GitHub looks for -- so a name rule has nothing to say about them
    # and would only ever be wrong. The rule reads the path, not the source, so it takes
    # `lines` solely to look for its exemption marker.
    if config.check_filenames:
        yield from check_generic_filename(path, lines, repo_root)

    # The data-access rules cover every language that can hold a query builder, including
    # the two that do not have one in this estate yet -- see standards_query.py.
    # SCRIPT_SUFFIXES, not a hand-written (".ts", ".tsx") pair: the tuple used to stop at
    # TypeScript, so an identical Knex or Prisma chain fired in a .tsx file and was silent
    # in the .mjs beside it. A plain-JavaScript Node backend got no data-access rule at all
    # while the sentinel and date rules there worked normally -- and "the query builder is
    # the same, only the extension differs" is exactly the drift a shared tuple prevents.
    query_suffixes = (".cs", ".razor", ".py", ".php", *SCRIPT_SUFFIXES)
    if config.check_query_shape and path.suffix in query_suffixes:
        yield from check_query_shape(path, lines, config)
        yield from check_filter_in_memory(path, lines, config)

    # Deliberately NOT gated on check_query_shape, and not on a flag of its own. That flag
    # relaxes a guideline about where a query should live; this is a correctness rule -- an
    # unordered paged query returns wrong data, silently -- and it follows the mojibake and
    # eval-interop precedent that a defect is not an estate convention a repo opts out of.
    # The line exemption remains, which is the instrument for the one query that is genuinely
    # not paginated output.
    if path.suffix in query_suffixes:
        yield from check_paged_order(path, lines)

    # Not gated on any config flag, and line-exemptable -- the same shape as the rule above.
    # Reading the client's address from the peer socket, or from the front of the forwarded
    # chain, is a correctness and security defect rather than an estate convention: it
    # returns a well-formed address belonging to a proxy, so a rate limit keyed on it is one
    # global bucket and an audit trail keyed on it records infrastructure. Scoped to the same
    # suffix set because "who is this request from" is asked by request-handling code in
    # every language here, not only by code that also holds a query.
    yield from check_client_address(path, lines)

    # Beside client-address and for the same reasons: both are security properties, not estate
    # conventions, so neither is config-gated, and both are line-exemptable. A wildcard CORS
    # policy turns an authenticated API into one any site can drive; a variable interpolated
    # into a SQL string is injection. Each function matches only its own language's syntax, so
    # running them across every source suffix is correct -- a C# CORS call cannot match a .py
    # line. An external audit confirmed both live: `header('Access-Control-Allow-Origin: *')`
    # and a GET parameter interpolated into six raw queries.
    yield from check_cors_wildcard(path, lines)
    yield from check_sql_injection(path, lines)

    # The test-quality half of the testing dimension. Per-file rather than repo-level,
    # because a test that cannot fail IS a property of the file it sits in -- and unlike the
    # coverage half it belongs at the COMMIT stage, where the test is being written. The
    # function decides internally that the path is a test, so running it on everything is
    # safe: production source is never asked whether it asserts.
    yield from check_test_quality(path, lines)

    # Third of the same family, and the one whose trigger is an OUTAGE: disabling TLS
    # certificate verification is what gets typed when a peer's certificate expires and the
    # calls have to start working again, so it arrives under time pressure and then never
    # fails again to prompt its removal. Only the residue lives here -- the C# kill-switch
    # symbols are in BannedSymbols.txt, JS/TS is eslint's no-restricted-syntax, and Python is
    # ruff's S501/S323 -- so the function matches C# callback shapes and PHP curl options only.
    yield from check_tls_verification(path, lines)

    # The scanner half of data-integrity.md's concurrency rule. Its headline requirement --
    # every updateable table carries a version column -- is not decidable from source, because
    # "updateable" is about how a table is written and the correct exemptions (append-only
    # logs, join tables, atomic counters) look identical in a declaration. These are the two
    # shapes that ARE decidable, and both survive a correct schema: a set-based write that
    # never passes the token, and a conflict caught and then written anyway.
    yield from check_concurrency(path, lines)

    # The caller's half of the same subject: a write the store refused must be able to say so,
    # and nobody may throw that answer away. Gated on a DECLARATION rather than a flag -- which
    # call returns a must-read result is a fact about this repo that no call site states, so an
    # undeclared repo gets silence, exactly as with `userDocs`. See standards_write_results.py.
    yield from check_write_results(path, lines, config.must_read_results)

    # What the person using the software is shown when something fails: never an exception's
    # own text, never a bare status code. Not config-gated, for the reason the security rules
    # above are not -- it is a defect the user meets, not an estate convention a repo may decline
    # -- and line-exemptable for the output no end user reads. Each dialect matches only its own
    # suffixes, so it is safe on every file. The repo's declared carriers ride along, because
    # the rule reads one file and the carrier's subclass is usually declared in another.
    # See standards_user_errors.py.
    yield from check_technical_error_shown(path, lines, config.user_facing_exceptions)

    # Where a value that may change is allowed to live -- see standards_config_defaults.py
    # and standards_constants.py. Config-
    # gated rather than unconditional, unlike the four security rules above: a value in the
    # wrong place costs a deploy, not a breach, so a repo is allowed to disagree in writing.
    # The third rule of this family, const-duplicated-literal, is repo-level and runs in
    # repository_violations -- it compares two files, so nothing here can ask it.
    if config.check_constants:
        yield from check_config_default(path, lines)
        yield from check_environment_literal(path, lines)

    language = LANGUAGE_RULES.get(path.suffix)
    if language is not None:
        yield from language(path, lines, config)


def _csharp(path: Path, lines: list[str], config: CheckConfig) -> Iterable[Violation]:
    # Background jobs, in this language's syntax. Same rule, same message, a reader that
    # matches how the language delimits a handler -- see standards_jobs_braces.
    if config.check_jobs:
        yield from check_job_failure_handling(path, lines)
    yield from check_csharp_members(path, lines, config)
    # Not gated on any config flag, for the same reason as the container and deploy rules
    # above: building a program by string interpolation and handing it to eval is a
    # security property, not an estate convention a repo gets to opt out of.
    yield from check_js_eval_interop(path, lines)
    if config.check_unset_sentinels:
        yield from check_zero_coalesce(path, lines, CSHARP_ZERO_COALESCE)
    if config.check_payload_defaults:
        yield from check_payload_default(path, lines, CSHARP_ZERO_COALESCE)
    if config.check_date_ranges:
        yield from check_csharp_dates(path, lines)
        # `check_csharp_dates` covers `DateTime.MinValue` and `new DateTime(1899, …)`;
        # this covers the same sentinel written as a STRING, which C# reaches through
        # `DateTime.Parse("…")`, EF `HasDefaultValue`, seed data, JSON fixtures and a
        # .razor attribute. Same data shape, so the same rule applies -- .php and the
        # script suffixes already called this and .cs did not.
        yield from check_date_string_literals(path, lines)
    # .cs is IDE0008's job; only components need the script to stand in for it.
    if config.check_declared_types and path.suffix == ".razor":
        yield from check_razor_var(path, lines)


def _python(path: Path, lines: list[str], config: CheckConfig) -> Iterable[Violation]:
    # Not gated on a config flag of its own beyond check_jobs, and for the same reason as
    # the eval and CORS rules: a job that reports SUCCESS whatever happened is a defect, not
    # an estate convention. The function decides internally that the file holds tasks.
    if config.check_jobs:
        yield from check_job_failure_handling(path, lines)
    yield from check_python_members(path, lines, config)
    if config.check_unset_sentinels:
        yield from check_zero_coalesce(path, lines, PYTHON_ZERO_COALESCE)
    if config.check_payload_defaults:
        yield from check_payload_default(path, lines, PYTHON_ZERO_COALESCE)
    if config.check_date_ranges:
        # Python used to get NO date rule beyond the bare `date.min` sentinel: no
        # constructor check, and no string check, while .cs, .php and the script
        # suffixes all had at least one. python-standards.md carries the same
        # "persisted dates declare a sensible range" section as every other rules file,
        # and a sentinel epoch in a fixture, a migration default or a serializer is
        # just as reachable here as it is in PHP.
        yield from check_python_dates(path, lines)
        yield from check_date_string_literals(path, lines)


def _php(path: Path, lines: list[str], config: CheckConfig) -> Iterable[Violation]:
    # Background jobs, in this language's syntax. Same rule, same message, a reader that
    # matches how the language delimits a handler -- see standards_jobs_braces.
    if config.check_jobs:
        yield from check_job_failure_handling(path, lines)
    yield from check_php_members(path, lines, config)
    # Not gated on any config flag, for the same reason as the eval-interop rule on the C#
    # side: a direct `eval()`/`create_function()` on a value, and a phpinfo() disclosure
    # page, are security defects rather than estate conventions. An external audit found
    # `eval($field->class)` run against a database column, and a shipped `phpinfo.php`.
    yield from check_php_eval(path, lines)
    yield from check_disclosure_file(path, lines)
    if config.check_unset_sentinels:
        yield from check_zero_coalesce(path, lines, PHP_ZERO_COALESCE)
    if config.check_payload_defaults:
        yield from check_payload_default(path, lines, PHP_ZERO_COALESCE)
    if config.check_date_ranges:
        yield from check_date_string_literals(path, lines)
    if config.check_declared_types:
        yield from check_php_strict_types(path, lines)


def _script(path: Path, lines: list[str], config: CheckConfig) -> Iterable[Violation]:
    if config.check_unset_sentinels:
        yield from check_zero_coalesce(path, lines, JS_ZERO_COALESCE)
    if config.check_payload_defaults:
        yield from check_payload_default(path, lines, JS_ZERO_COALESCE)
    if config.check_date_ranges:
        yield from check_js_dates(path, lines)
        yield from check_date_string_literals(path, lines)


# The per-language rules, keyed on suffix instead of an if/elif ladder. Each handler owns its
# own config gates, so adding a language is a new function and one mapping entry rather than
# another branch in a function that was already at cyclomatic complexity 37.
LANGUAGE_RULES: dict[str, Callable[[Path, list[str], CheckConfig], Iterable[Violation]]] = {
    ".cs": _csharp,
    ".razor": _csharp,
    ".py": _python,
    ".php": _php,
    **dict.fromkeys(SCRIPT_SUFFIXES, _script),
}

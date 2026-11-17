#!/usr/bin/env python3
"""A production-affecting debug/verbose-error flag committed in the ON position.

Rules here: debug-flag-in-prod.

THE FAMILY. Not "this app is misconfigured" -- that is a judgement about a running system no
scanner can make. This is the narrower, decidable thing: a flag that turns on stack traces,
SQL echoing or full error pages, hardcoded to its dangerous value IN A COMMITTED FILE, where
it ships to whoever deploys the file without touching it. The remedy is never to escape or
wrap the value; it is to stop committing the decision -- read it from the environment and
default it OFF, so production is safe unless someone deliberately opts a machine into verbose
errors.

WHY IT EARNS A RULE (audit finding). A Yii app shipped its production entry point with

    defined('YII_DEBUG') or define('YII_DEBUG', true);

hardcoded. With YII_DEBUG on, an unhandled exception does not render the generic error page --
it returns the full stack trace, absolute file paths, and the failing SQL straight to the
browser. That is information disclosure: the framework's own error handler, weaponised by one
committed `true`, hands an attacker the application's internal structure and a map of its
database on the first exception they can provoke. The line reads as boilerplate, which is
exactly why it survives review -- `defined(...) or define(...)` is the idiom everyone's eye
skips, and the `true` rides in on its coat-tails.

WHY THE SCANNER AND NOT AN ANALYZER. `define`, `ini_set`, a Django `DEBUG` assignment and an
`env('APP_DEBUG', true)` default are all entirely legitimate calls -- BannedSymbols cannot ban
them and no shipped analyzer inspects the VALUE that makes this one dangerous. The danger is a
literal `true` (or `'1'`, or `On`) in a specific position, and only a source scan sees it.

WHY IT IS NOT GATED ON A CONFIG FLAG, and is line-exemptable. Like js-eval-interop, this is a
security defect, not an estate convention a repo may opt out of: a repo that turned off "the
debug-flag rule" would be opting out of not-disclosing-its-stack-traces, which is not a choice
the compose-conventions switch is allowed to make. The one genuine exception -- a throwaway
local-only entry point that never deploys -- says so on the line with a written reason.

WHAT TO DO INSTEAD. Drive the flag from the environment and default it OFF:

    define('YII_DEBUG', getenv('YII_DEBUG') === '1');      # PHP
    DEBUG = os.environ.get('DEBUG') == '1'                 # Django settings
    'debug' => env('APP_DEBUG', false),                    # Laravel config

Now the committed default is safe, and a developer opts THEIR machine into verbose errors
through the environment without ever shipping that choice to production.

Source of truth: engineering-standards/engineering_standards/standards_debugflag.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import Violation, iter_code_lines
from standards_exemptions import line_exemption_reason

RULE = "debug-flag-in-prod"

# --- Python (.py) ----------------------------------------------------------------------------
#
# A module-level `DEBUG = True` -- the Django settings shape. Anchored to the start of the line
# (no leading indent) so it fires on the module-level settings toggle and NOT on a `self.DEBUG
# = True` inside a method or a nested dict entry. The right-hand side must be the bare literal
# `True`: `DEBUG = os.environ.get(...)`, `DEBUG = env.bool(...)` and `DEBUG = config(...)` all
# read from the environment and are precisely the fix, so only a literal is a finding.
PY_DEBUG_TRUE = re.compile(r"^DEBUG\s*=\s*True\b")

# --- PHP (.php) ------------------------------------------------------------------------------
#
# `define('YII_DEBUG', true)` / `define("YII_DEBUG", true)`, including the `defined(...) or
# define(...)` idiom the audit found -- the trailing `define` call is what this matches, so the
# `defined()` guard in front of it is irrelevant. Quotes may be either kind and need not match
# across the two arguments in practice, so each is its own character-agnostic group.
PHP_YII_DEBUG = re.compile(
    r"""define\s*\(\s*['"]YII_DEBUG['"]\s*,\s*true\s*\)""",
    re.IGNORECASE,
)

# `ini_set('display_errors', ...)` turned ON. The truthy spellings PHP accepts here are '1',
# 1, 'On' and 'true'; '0'/'Off'/'false' turn it OFF and are the correct production value, so
# they are deliberately NOT matched. `error_reporting(E_ALL)` is NOT flagged -- raising the
# reporting LEVEL is normal and legitimate (it controls logging, not what reaches the browser);
# it is `display_errors` that puts the errors in front of the user.
PHP_DISPLAY_ERRORS = re.compile(
    r"""ini_set\s*\(\s*['"]display_errors['"]\s*,\s*['"]?(?:1|On|true)['"]?\s*\)""",
    re.IGNORECASE,
)

# --- .env.example ----------------------------------------------------------------------------
#
# The committed env TEMPLATE (never `.env` itself). A debug flag defaulting to `true` here is
# the value a fresh deployer inherits verbatim -- Laravel's APP_DEBUG, Django's DEBUG, Yii's
# YII_DEBUG. Matched on raw lines (dotenv is not source), left-anchored so `APP_DEBUG=true` is
# a finding and a commented `# APP_DEBUG=true` is not.
ENV_DEBUG_TRUE = re.compile(
    r"""^\s*(?P<key>APP_DEBUG|DEBUG|YII_DEBUG)\s*=\s*["']?true["']?\s*$""",
    re.IGNORECASE,
)

# --- web.config / app.config (.config) -------------------------------------------------------
#
# `<compilation ... debug="true" ...>`. ASP.NET's debug compilation attribute left on in a
# committed config disables optimisations and, more to the point here, controls how much detail
# the framework's error pages disclose. The attribute may sit anywhere inside the tag, so the
# match spans from `<compilation` to the `debug="true"` regardless of intervening attributes,
# without letting the `[^>]*` run past the tag it belongs to.
CONFIG_DEBUG_TRUE = re.compile(
    r"""<compilation\b[^>]*\bdebug\s*=\s*["']true["']""",
    re.IGNORECASE,
)

# --- Laravel config/*.php --------------------------------------------------------------------
#
# `'debug' => env('APP_DEBUG', true)` -- a debug config whose env() DEFAULT is `true`. The
# default is what ships when the variable is unset, so this is the same defect as a hardcoded
# flag wearing the env() idiom: on a server that forgot to set APP_DEBUG, debug is ON. A `false`
# default is the correct shape and is not matched. Scoped to files living under a `config/`
# directory, because that key name is Laravel's and the shape is only decidable there -- a bare
# `'debug' => env(...)` elsewhere is too generic to read.
LARAVEL_DEBUG_DEFAULT_TRUE = re.compile(
    r"""['"]debug['"]\s*=>\s*env\s*\(\s*['"][^'"]*['"]\s*,\s*true\s*\)""",
    re.IGNORECASE,
)


# The shared remedy, appended to every message so the fix travels with the finding.
_FIX = (
    "Drive the flag from the environment and default it OFF, so the committed default is safe "
    "and a developer opts THEIR machine into verbose errors without shipping that choice: "
    "`env('APP_DEBUG', false)` / `getenv('YII_DEBUG') === '1'` / "
    "`DEBUG = os.environ.get('DEBUG') == '1'`. If this file genuinely never deploys (a "
    f"throwaway local entry point), exempt the line with a written reason: "
    f"`standards: {RULE} exempt -- <why>`."
)


def _python_findings(lines: list[str]) -> Iterable[tuple[int, str]]:
    """(line index, what) for a module-level `DEBUG = True` in Python source."""
    for line_number, line in iter_code_lines(lines, ".py"):
        if PY_DEBUG_TRUE.match(line):
            yield (
                line_number - 1,
                (
                    "a module-level `DEBUG = True` is committed. In Django this turns on the "
                    "debug error page, which renders the traceback, local variables, settings and "
                    "the failing SQL to anyone who triggers an exception -- full information "
                    "disclosure from one literal"
                ),
            )


def _php_findings(lines: list[str]) -> Iterable[tuple[int, str]]:
    """(line index, what) for YII_DEBUG defined true, or display_errors turned on."""
    for line_number, line in iter_code_lines(lines, ".php"):
        if PHP_YII_DEBUG.search(line):
            yield (
                line_number - 1,
                (
                    "`YII_DEBUG` is defined as `true` in committed PHP. With it on, an unhandled "
                    "exception returns the full stack trace, absolute file paths and the failing "
                    "SQL to the browser instead of a generic error page -- the exact information "
                    "disclosure this rule exists to stop. The `defined(...) or define(...)` idiom "
                    "does not make it safe; it only makes the `true` easy to miss in review"
                ),
            )
        if PHP_DISPLAY_ERRORS.search(line):
            yield (
                line_number - 1,
                (
                    "`display_errors` is turned ON in committed PHP. In production that puts PHP's "
                    "own error text -- paths, and often query fragments -- in front of the user "
                    "rather than in the log. (Raising `error_reporting` is fine; sending the "
                    "errors to the browser is not.)"
                ),
            )


def _laravel_findings(lines: list[str]) -> Iterable[tuple[int, str]]:
    """(line index, what) for a Laravel `'debug' => env('APP_DEBUG', true)` default."""
    for line_number, line in iter_code_lines(lines, ".php"):
        if LARAVEL_DEBUG_DEFAULT_TRUE.search(line):
            yield (
                line_number - 1,
                (
                    "the Laravel `debug` config defaults its env() lookup to `true`. The default "
                    "is what ships when `APP_DEBUG` is unset, so a server that forgot to set it "
                    "runs with Whoops rendering full stack traces and environment values to the "
                    "browser -- the committed default decides production, and here it decides ON"
                ),
            )


def _env_example_findings(lines: list[str]) -> Iterable[tuple[int, str]]:
    """(line index, what) for a debug flag defaulting to `true` in a committed env template."""
    for index, line in enumerate(lines):
        found = ENV_DEBUG_TRUE.match(line)
        if found:
            key = found.group("key")
            yield (
                index,
                (
                    f"`{key}=true` is committed in the env template. This is the value a fresh "
                    f"deployer copies into their `.env` verbatim, so the shipped default is "
                    f"verbose errors -- stack traces and internals disclosed to the browser -- "
                    f"until someone remembers to flip it"
                ),
            )


def _config_findings(lines: list[str]) -> Iterable[tuple[int, str]]:
    """(line index, what) for `<compilation ... debug="true">` in a committed .config."""
    for index, line in enumerate(lines):
        if CONFIG_DEBUG_TRUE.search(line):
            yield (
                index,
                (
                    '`<compilation debug="true">` is committed. ASP.NET debug compilation leaves '
                    "optimisations off and error pages verbose, so a committed `true` here is a "
                    "production build that discloses more than it should on failure"
                ),
            )


def check_debug_flag(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a production-affecting debug/verbose-error flag committed in the ON position.

    Dispatches internally by file type, because the shape a flag-left-on takes is different in
    each: a bare `DEBUG = True` in Django settings, a `define('YII_DEBUG', true)` in a PHP entry
    point, a `debug="true"` compilation attribute in a .NET config, and a `true` DEFAULT in
    both a committed env template and a Laravel `env()` lookup. Every shape is decidable from a
    literal in a fixed position, which is what keeps this low-false-positive: a value read from
    the environment WITHOUT a true-default is never a finding, because that is the fix.

    Source files (.py/.php) go through `iter_code_lines`, so a commented-out `# DEBUG = True`
    or a flag inside a docstring is ignored. `.env.example` and `.config` are not source and
    are read raw, with the regexes anchored so a leading `#`/`;`/`<!--` comment is not matched.
    """
    suffix = path.suffix.casefold()
    name = path.name.casefold()

    if suffix == ".py":
        findings = _python_findings(lines)
    elif suffix == ".php":
        # config/*.php carries the Laravel debug-config shape ON TOP of the entry-point shapes;
        # the parts of a Laravel app that hold `define('YII_DEBUG', ...)` never live under
        # config/, so running both against a config file costs nothing and misses neither.
        findings = _chain(
            _php_findings(lines),
            _laravel_findings(lines) if _is_laravel_config(path) else (),
        )
    elif name == ".env.example":
        findings = _env_example_findings(lines)
    elif suffix == ".config":
        findings = _config_findings(lines)
    else:
        return

    for index, what in findings:
        if line_exemption_reason(lines, index, RULE):
            continue
        yield Violation(
            path=path,
            line=index + 1,
            rule=RULE,
            message=f"{what}. {_FIX}",
        )


def _is_laravel_config(path: Path) -> bool:
    """True for a `.php` file living under a `config/` directory -- Laravel's config home.

    Decided on the path's own parents, not a repo-relative path, because the check is handed a
    file and no tree. The Laravel true-default shape is only decidable where the `'debug'` key
    means what Laravel means by it, and that is the `config/` directory.
    """
    return any(part.casefold() == "config" for part in path.parent.parts)


def _chain(*iterables: Iterable[tuple[int, str]]) -> Iterable[tuple[int, str]]:
    """`itertools.chain` spelled locally, to keep the module's imports to the two it shares
    with every other rule module."""
    for iterable in iterables:
        yield from iterable

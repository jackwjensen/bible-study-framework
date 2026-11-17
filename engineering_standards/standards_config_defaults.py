#!/usr/bin/env python3
"""`config-default-in-code`: a configuration read whose fallback is a literal.

The value already IS configuration, so a literal fallback is a second copy of it in the source
-- the committed-credential shape from `secrets.md` generalised past credentials. One of the
three "values that change" rules; the other two judge constant DECLARATIONS and live in
`standards_constants.py`, while this one judges configuration READS. Split out 2026-10-22, when
that module reached its 500-line limit with this rule's first real change; the reasoning shared
by all three is `docs/values-that-change.md`, and none of it is restated here.

KNOWN BLIND SPOT. The rule reads one line at a time, so a fallback reached through a HELPER is
invisible to it -- neither line of `Stated(app.Configuration, KEY) ?? (isDev ? Fallback : null)`
carries both an accessor and a literal. `const-environment-literal` catches that case from the
declaration side instead; where the value's shape gives nothing away it is judgment.

Source of truth: engineering-standards/engineering_standards/standards_config_defaults.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_constants import CONSTANT_SUFFIXES, _clip, _is_test_path
from standards_core import Violation, iter_code_lines

CONFIG_DEFAULT_RULE = "config-default-in-code"

# PHP's $_SERVER holds two things in one array. Under php-fpm it carries the process
# environment -- configuration, which this rule is about. It ALSO carries the request: the CGI
# meta-variables (RFC 3875 section 4.1), every header as HTTP_*, and PHP's own script
# bookkeeping. Those are data the client or the server just sent, so `$_SERVER['REQUEST_URI']
# ?? '/'` guards a missing request field rather than copying a setting. Reading them as
# configuration fired four times on jacks_corner's front controller (2026-10-22), none of them
# configuration, with no line exemption to clear them.
#
# Prefixes end in `_` and whole names must be followed by the closing quote, so an environment
# variable that merely starts like one (`HTTPS_PROXY`) is still configuration. A dynamic key,
# `$_SERVER[$name]`, stays in scope: nothing here can say which half of the array it reads.
SERVER_REQUEST_PREFIXES = ("HTTP_", "REQUEST_", "REMOTE_", "SERVER_", "SCRIPT_", "PHP_")
SERVER_REQUEST_NAMES = (
    "QUERY_STRING", "DOCUMENT_ROOT", "DOCUMENT_URI", "CONTENT_TYPE", "CONTENT_LENGTH",
    "PATH_INFO", "PATH_TRANSLATED", "GATEWAY_INTERFACE", "AUTH_TYPE", "HTTPS", "argv", "argc",
)  # fmt: skip
SERVER_REQUEST_KEY = "(?:" + "|".join(SERVER_REQUEST_PREFIXES) + "|(?:" + "|".join(SERVER_REQUEST_NAMES) + ")[\"'])"

# The accessors that mean "this value is configuration". Matched anywhere on the line, then
# the fallback is looked for separately -- two conditions rather than one long pattern, so a
# new accessor spelling is one entry rather than a rewrite.
CONFIG_ACCESSORS = re.compile(
    r"""(?x)
    \bConfiguration\s*\[                 # C#: Configuration["Key"], builder.Configuration[...]
  | \bconfiguration\s*\[
  | \.GetConnectionString\s*\(
  | \.GetValue\s*<                       # C#: GetValue<string>("Key", "default")
  | \bos\.environ\b | \benviron\.get\s*\(  # Python
  | \bos\.getenv\s*\( | \bgetenv\s*\(      # Python / PHP
  | \$_ENV\s*\[                          # PHP
  | \$_SERVER\s*\[(?!\s*["']REQUEST_KEY)   # PHP: the environment half only, see above
  | \bprocess\.env\b                     # JS / TS
    """.replace("REQUEST_KEY", SERVER_REQUEST_KEY)
)

# A fallback to a LITERAL. `?? "x"`, `or "x"`, `?: 'x'`, `|| "x"`, and the positional default
# of a getter call (`getenv("KEY", "default")`).
LITERAL_FALLBACK = re.compile(
    r"""(?x)
    (?:\?\?|\?:|\|\||\bor\b)\s*(?P<quote>["'])(?P<value>(?:(?!\1).)*)(?P=quote)
  | ,\s*(?P<q2>["'])(?P<v2>(?:(?!\3).)*)(?P=q2)\s*\)
    """
)

# `?? throw ...` is the shape `secrets.md` MANDATES for a seeder, and `?? null` is not a
# default at all. Neither may ever be reported, or the rule would fire on the correct answer.
CORRECT_FALLBACKS = re.compile(r"(?:\?\?|\?:|\|\|)\s*(?:throw\b|null\b|None\b|undefined\b)")


def check_config_default(path: Path, lines: list[str]) -> Iterable[Violation]:
    """A configuration read whose fallback is a literal written into the source.

    Test files are out of scope -- see `_is_test_path` for why the whole family skips them.
    Here the reason is sharper than elsewhere: a scanner's OWN test files contain fixture
    source as string literals, so this rule reads its own examples back as findings. Measured
    on this pack, where `test_standards_debugflag.py`'s fixture `os.environ.get('DEBUG')` was
    reported as a configuration default.
    """
    if path.suffix not in CONSTANT_SUFFIXES or _is_test_path(path):
        return

    for line_number, line in iter_code_lines(lines, path.suffix):
        if not CONFIG_ACCESSORS.search(line):
            continue
        if CORRECT_FALLBACKS.search(line):
            continue

        match = LITERAL_FALLBACK.search(line)
        if match is None:
            continue

        value = match.group("value") or match.group("v2") or ""
        # An empty-string default is "absent", not an invented value, and is how a great deal
        # of correct code spells "no override". Reporting it would train people to ignore this.
        if value.strip() == "":
            continue

        yield Violation(
            path=path,
            line=line_number,
            rule=CONFIG_DEFAULT_RULE,
            message=(
                f"A configuration read falls back to the literal '{_clip(value)}'. The value "
                f"already IS configuration, so this is a second copy of it that changes only "
                f"when somebody deploys. State it in the environment file instead, or throw "
                f"and name the missing key."
            ),
        )

#!/usr/bin/env python3
"""A CORS policy that answers "which sites may call this API?" with "any of them".

    cors-wildcard   an Access-Control-Allow-Origin that reflects or allows ANY origin

standards: cors-wildcard exempt -- this file and its tests NAME the wildcard shapes in
order to detect them; every policy quoted below is the subject of a regex, never a live
CORS configuration. Stated in the header because the marker is only read in the first 40
lines, and the Python leads further down ("CORS_ALLOW_ALL_ORIGINS = True", the FastAPI
allow_origins line) are real occurrences that would otherwise flag this detector on itself.

WHAT CORS ACTUALLY GUARDS. The same-origin policy is the browser's default answer to "may
the JavaScript on site A read a response from site B?" -- and the default is no. CORS is the
server's way of naming the exceptions: `Access-Control-Allow-Origin: https://app.example.com`
tells the browser that this one site is allowed to read the response. A wildcard erases the
exception list and replaces it with "everyone". On a public, unauthenticated, read-only feed
that is a defensible choice. On an AUTHENTICATED API it means any website a logged-in user
visits can drive that API with the user's own session -- the exact machinery CSRF protections
and the same-origin policy exist to prevent, handed back voluntarily in a response header.

WHY THIS EARNED A SCANNER RULE (real audit). An API in the estate set, in PHP,
`'Origin' => ['*']` together with `Access-Control-Allow-Headers: *`, on a surface behind a
login. The wildcard headers meant any origin could send any custom header -- including the
ones the API used to authenticate -- and read the reply. Nothing errored, no test failed, and
the header looked like boilerplate copied to "make CORS work" during development, which is
precisely how it survived to production: a wildcard is what you reach for when a preflight is
failing and you want it to stop failing, and it always works, because it allows everything.

THE CREDENTIALS COMBINATION is the sharpest tell, and the CORS spec itself rejects it: a
browser will refuse `Access-Control-Allow-Origin: *` sent together with
`Access-Control-Allow-Credentials: true`. So code that wants credentialed cross-origin calls
cannot use a literal `*` and instead REFLECTS the caller's Origin back -- `origin: true` in
the cors middleware, `SetIsOriginAllowed(_ => true)` in ASP.NET -- which is a dynamic wildcard
that also satisfies the spec's check. That is worse, not better: it allows every origin AND
sends credentials, the one combination the static `*` is blocked from expressing.

WHAT IT LOOKS FOR, the decidable shapes only -- a reflected allow-list built from a request
header is not something a source scan can see, so this fires on the spellings that are
unambiguously "any origin":

  * PHP     -- `header('Access-Control-Allow-Origin: *')`, any quote style.
  * C# (.cs)-- `AllowAnyOrigin()`, and `.WithOrigins("*")`. `AllowAnyOrigin()` is flagged on
               its own, and called out as the spec-forbidden combination when the same file
               also calls `AllowCredentials()`.
  * JS/TS   -- `cors({ ... origin: '*' ... })` and `origin: true` in a cors config; and the
               raw `res.header('Access-Control-Allow-Origin', '*')` / `setHeader(...)`.
  * Python  -- `CORS_ALLOW_ALL_ORIGINS = True` and the legacy `CORS_ORIGIN_ALLOW_ALL = True`
               (django-cors-headers); a FastAPI/Starlette `allow_origins=["*"]`.

WHAT IT DELIBERATELY DOES NOT FLAG. A named allow-list is the correct code this rule is
steering toward, so it must never be the thing reported: `WithOrigins("https://app.example.com")`,
`origin: ['https://x']`, `allow_origins=["https://x"]` all pass. Only the literal `*` (or the
`origin: true` reflection) is the finding.

WHY THE SCANNER AND NOT AN ANALYZER. This is a SECURITY defect, not a convention, so it is not
config-gated -- a repo cannot tune it off. But it is line-exemptable: a genuinely public,
unauthenticated, read-only API is the one case where `*` is the right answer, and it can say so
on the line with a written reason. A FILE-scoped marker is accepted too, for the one narrow
case that is not application code: a file whose subject IS these patterns -- this detector and
its fixtures -- exactly as `standards_client_address.py` treats its own kind.

Source of truth: engineering-standards/engineering_standards/standards_cors.py
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Iterable, Optional

from standards_core import Violation, iter_code_lines
from standards_exemptions import exemption_reason, line_exemption_reason
from standards_scope import SCRIPT_SUFFIXES, SOURCE_SUFFIXES

CORS_RULE = "cors-wildcard"

# PHP: the header set as a single colon-separated string, `Access-Control-Allow-Origin: *`,
# in any quote style. The header NAME is matched case-insensitively because HTTP field names
# are, and the value is the literal `*`. This is one string, unlike the JS two-argument form
# below, so the two patterns never collide.
PHP_HEADER_WILDCARD = re.compile(r"access-control-allow-origin\s*:\s*\*", re.IGNORECASE)

# C#: `AllowAnyOrigin()` names any origin outright; `.WithOrigins("*")` is the same thing
# spelled as a one-element allow-list of the wildcard. The WithOrigins pattern requires a
# quoted `*` argument, so a real allow-list -- `WithOrigins("https://app.example.com")`, or
# even a wildcard-subdomain `"https://*.example.com"` whose `*` is not adjacent to a quote --
# is not matched.
CS_ALLOW_ANY_ORIGIN = re.compile(r"\bAllowAnyOrigin\s*\(\s*\)")
CS_WITH_ORIGINS_WILDCARD = re.compile(r"""\bWithOrigins\s*\([^)]*['"]\*['"]""")
CS_ALLOW_CREDENTIALS = re.compile(r"\bAllowCredentials\s*\(")

# JS/TS: the raw response header, written as two string arguments
# (`res.header('Access-Control-Allow-Origin', '*')`, `setHeader(...)`, `res.set(...)`); and
# the cors-middleware config, `origin: '*'` or the reflecting `origin: true`. The config form
# is gated on the file actually referencing `cors`, because a bare `origin:` key is common in
# unrelated objects, while `origin: '*'`/`origin: true` inside a file that pulls in the cors
# middleware is decidably a CORS policy.
JS_HEADER_WILDCARD = re.compile(r"""['"]access-control-allow-origin['"]\s*,\s*['"]\*['"]""", re.IGNORECASE)
JS_ORIGIN_WILDCARD = re.compile(r"""\borigin\s*:\s*['"]\*['"]""")
JS_ORIGIN_TRUE = re.compile(r"\borigin\s*:\s*true\b")
JS_USES_CORS = re.compile(r"\bcors\b")

# Python: django-cors-headers' two settings flags (the current name and the legacy one), and
# FastAPI/Starlette's `allow_origins=["*"]`. The FastAPI pattern requires a quoted `*` inside
# the list, so `allow_origins=["https://x"]` does not match.
PY_CORS_ALLOW_ALL = re.compile(r"\bCORS_ALLOW_ALL_ORIGINS\s*=\s*True\b")
PY_CORS_ALLOW_ALL_LEGACY = re.compile(r"\bCORS_ORIGIN_ALLOW_ALL\s*=\s*True\b")
PY_FASTAPI_ALLOW_ORIGINS = re.compile(r"""\ballow_origins\s*=\s*\[[^\]]*['"]\*['"]""")

_PHP_LEAD = "this sends `Access-Control-Allow-Origin: *`"
_CS_ANY_LEAD = "this calls `AllowAnyOrigin()`, which reflects any Origin"
_CS_CREDENTIALS_LEAD = (
    "this calls `AllowAnyOrigin()` while the same file calls `AllowCredentials()` -- the exact "
    "combination the CORS spec rejects, so the browser discards the header and the policy fails "
    "in a way no test will catch"
)
_CS_WITH_ORIGINS_LEAD = (
    'this passes `"*"` to `WithOrigins()`, which is `AllowAnyOrigin()` spelled as a one-element '
    "allow-list of the wildcard"
)
_JS_HEADER_LEAD = "this sets the `Access-Control-Allow-Origin` response header to `*`"
_JS_ORIGIN_WILDCARD_LEAD = "this configures the cors middleware with `origin: '*'`"
_JS_ORIGIN_TRUE_LEAD = (
    "this configures the cors middleware with `origin: true`, which reflects whatever Origin the "
    "caller sent -- a dynamic `*` that also passes the credentials check the static `*` fails"
)
_PY_ALL_LEAD = "this sets `CORS_ALLOW_ALL_ORIGINS = True` (django-cors-headers)"
_PY_LEGACY_LEAD = "this sets `CORS_ORIGIN_ALLOW_ALL = True` (django-cors-headers, the legacy setting name)"
_PY_FASTAPI_LEAD = 'this passes `allow_origins=["*"]` to CORSMiddleware (FastAPI/Starlette)'

_SHARED_TAIL = (
    ". A wildcard `*` switches off the origin check entirely: every website your users visit "
    "can call this API from their browser, and on an authenticated surface that is the request "
    "forgery the same-origin policy exists to stop, granted back in a header. Combined with "
    "credentials it is the exact pattern the CORS spec forbids -- `Allow-Origin: *` may not be "
    "sent with `Allow-Credentials: true`, so reflecting the caller's origin to get around that "
    "is worse, not safer. Name the origins you actually serve instead. If this genuinely is a "
    "public, unauthenticated, read-only API where any origin is intended, say so on the line "
    f"above: 'standards: {CORS_RULE} exempt -- <why>'."
)


def _php_lead(line: str, has_credentials: bool, file_uses_cors: bool) -> Optional[str]:
    return _PHP_LEAD if PHP_HEADER_WILDCARD.search(line) else None


def _cs_lead(line: str, has_credentials: bool, file_uses_cors: bool) -> Optional[str]:
    """C# reads a file-level fact -- does the policy also enable credentials -- to choose."""
    if CS_ALLOW_ANY_ORIGIN.search(line):
        return _CS_CREDENTIALS_LEAD if has_credentials else _CS_ANY_LEAD
    return _CS_WITH_ORIGINS_LEAD if CS_WITH_ORIGINS_WILDCARD.search(line) else None


def _script_lead(line: str, has_credentials: bool, file_uses_cors: bool) -> Optional[str]:
    """JS reads another: whether the file uses the cors middleware at all.

    Without that, a bare `origin:` key is any object literal's property rather than a CORS
    policy, and the rule would fire on configuration that has nothing to do with CORS.
    """
    if JS_HEADER_WILDCARD.search(line):
        return _JS_HEADER_LEAD
    if not file_uses_cors:
        return None
    if JS_ORIGIN_WILDCARD.search(line):
        return _JS_ORIGIN_WILDCARD_LEAD
    return _JS_ORIGIN_TRUE_LEAD if JS_ORIGIN_TRUE.search(line) else None


def _python_lead(line: str, has_credentials: bool, file_uses_cors: bool) -> Optional[str]:
    for pattern, lead in (
        (PY_CORS_ALLOW_ALL, _PY_ALL_LEAD),
        (PY_CORS_ALLOW_ALL_LEGACY, _PY_LEGACY_LEAD),
        (PY_FASTAPI_ALLOW_ORIGINS, _PY_FASTAPI_LEAD),
    ):
        if pattern.search(line):
            return lead
    return None


# One entry per language family, replacing a four-arm if/elif ladder that reached fourteen
# return statements in one function -- the pack's own limit is six, and it was shipping that
# rule to nine repositories while breaking it here.
_LEAD_BY_SUFFIX: dict[str, Callable[[str, bool, bool], Optional[str]]] = {
    ".php": _php_lead,
    ".cs": _cs_lead,
    ".py": _python_lead,
    **dict.fromkeys(SCRIPT_SUFFIXES, _script_lead),
}


def _lead_for_line(suffix: str, line: str, has_credentials: bool, file_uses_cors: bool) -> Optional[str]:
    """The message lead for the wildcard shape on this line, or None if there is none."""
    resolve = _LEAD_BY_SUFFIX.get(suffix)
    return resolve(line, has_credentials, file_uses_cors) if resolve else None


def check_cors_wildcard(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Every line that opens a CORS policy to any origin.

    Line-scoped rather than file-scoped: one config module legitimately holds a public
    read-only feed's `*` beside an authenticated API's named allow-list, and a header marker
    would excuse both. The file-scoped marker is kept only for the detector-and-fixtures case
    -- a file whose subject IS these patterns -- and is checked once before the walk.
    """
    suffix = path.suffix
    if suffix not in SOURCE_SUFFIXES:
        return

    # The weaker instrument, for the narrow case in the module docstring: a file that names
    # the patterns in order to detect them. Checked once, so a fixture table needs no marker
    # per case.
    if exemption_reason(lines, CORS_RULE) is not None:
        return

    code_lines = list(iter_code_lines(lines, suffix))

    # File-level facts read once, over code only so a mention in a comment neither picks the
    # credentials lead nor turns the cors gate on.
    has_credentials = suffix == ".cs" and any(CS_ALLOW_CREDENTIALS.search(line) for _, line in code_lines)
    file_uses_cors = suffix in SCRIPT_SUFFIXES and any(JS_USES_CORS.search(line) for _, line in code_lines)

    for line_number, line in code_lines:
        lead = _lead_for_line(suffix, line, has_credentials, file_uses_cors)
        if lead is None:
            continue

        if line_exemption_reason(lines, line_number - 1, CORS_RULE) is not None:
            continue

        yield Violation(
            path=path,
            line=line_number,
            rule=CORS_RULE,
            message=lead + _SHARED_TAIL,
        )

#!/usr/bin/env python3
"""Credentials must not reach git. The enforceable half of claude/rules/secrets.md.

Rules here: committed-credential.

WHY THIS RUNS AT THE COMMIT STAGE, against the pack's usual staging. Nearly every repo-level
rule here runs at PUSH, because a commit-stage finding can block the very commit that fixes it.
Credentials invert that: **the commit IS the boundary.** Once a working credential is in a
commit, the fix stops being an edit and becomes a history rewrite plus a rotation. Blocking at
push is already too late. So this runs on the STAGED set at commit, and the whole tree at push
to catch what was already tracked before the pack landed.

WHY THE USUAL BIAS INVERTS TOO, AND WHERE IT DOES NOT. The pack's rules deliberately err toward
silence, because a noisy rule gets exempted wholesale and an exempted rule is not running. Here
a false negative costs a rotation and a history rewrite while a false positive costs one
exemption line -- so the cost asymmetry points the other way. But wholesale exemption is still
the failure that kills a rule, so the answer is not "be aggressive", it is TWO TIERS with
different powers:

  FORMAT   a known credential shape -- `sk_live_`, `AKIA…`, a PEM private key. These are never
           placeholders, so they block and cannot be baselined.
  SHAPE    a secret-NAMED key holding a value that is not obviously a placeholder. Blocks, but
           is line-exemptable, because only a human can say "that is our fake demo value".

THE VALUE IS NEVER PRINTED. Findings name the key and the format, never the secret. Most secret
scanners echo the match into CI logs, which relocates the leak rather than closing it -- and the
estate's own standing rule is to never echo a leaked value anywhere.

`.env` IS DETECTED BY NAME AND NEVER OPENED. A staged `.env` is itself the finding; no read is
required to know that, and reading it would break the same standing rule. THE RULE RETURNING
EARLY IS NOT WHAT MAKES THAT TRUE -- the scanner's reader opens every candidate file before any
rule is dispatched, so the invariant held in this module and was false in the pipeline feeding
it until 2026-09-05. `is_never_read` below is the enforcement point; the reader and the rule
both ask it, so the set of files never opened and the set reported by name are one set.

THE PATTERNS DO NOT MATCH THIS FILE, by construction: every format requires a payload after its
prefix, so the bare prefix written here as a regex matches nothing. That is deliberate -- a
detector that fires on its own source teaches people to exempt it on sight.

Source of truth: engineering-standards/engineering_standards/standards_secrets.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, NamedTuple

from standards_core import Violation
from standards_exemptions import line_exemption_reason

SECRET_RULE = "committed-credential"  # noqa: S105  (a RULE NAME, not a credential -- this is the detector, not a secret)

# ---- tier 1: shapes that are only ever real credentials --------------------------------------
#
# Each requires a PAYLOAD, not just the prefix. That is what keeps this module, its tests and
# secrets.md itself clean: the literal `sk_live_` written as documentation matches nothing.
CREDENTIAL_FORMATS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("a Stripe secret key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("a Stripe webhook signing secret", re.compile(r"\bwhsec_[A-Za-z0-9]{16,}")),
    ("a Brevo API key", re.compile(r"\bxkeysib-[A-Za-z0-9]{16,}")),
    ("an AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    # `ghs_` is a GitHub App installation token, and since 2026 it can be a stateless JWT: ~520
    # characters, two dots, base64url's `-` and `_`. Letters-and-digits-only stopped at the first
    # of those and passed the token. The charset is GitHub's own published validation pattern.
    (
        "a GitHub token",
        re.compile(r"\b(?:ghp|gho|ghu|ghr)_[A-Za-z0-9]{30,}|\bghs_[A-Za-z0-9._-]{36,}|\bgithub_pat_[A-Za-z0-9_]{30,}"),
    ),
    ("a Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("a SendGrid API key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}")),
    ("a private key block", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
)

# ---- tier 2: a secret-named key holding something that is not a placeholder -------------------
#
# The KEY is what makes a value suspicious; a bare high-entropy string is not enough on its own,
# because hashes, ids and base64 blobs are everywhere and flagging them is how a rule dies.
# THE BOUNDARY IS LETTERS AND DIGITS, NOT `\w`. Written as `(?<![\w-])` first, which excludes
# the underscore -- and `SMTP_PASSWORD`, `MYSQL_ROOT_PASSWORD`, `STRIPE_API_KEY` are exactly how
# every environment variable in the estate is named, so the rule was silent on the shape it will
# meet most often. The trailing boundary still has to reject `passwordless` and `tokenizer`,
# which a letter after the word does.
SECRET_KEY_NAME = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token|auth[_-]?token"
    r"|client[_-]?secret|private[_-]?key|connection[_-]?string|credential"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# `"Password": "x"`, `PASSWORD=x`, `password: x`, `password => 'x'`, `Password = "x"`.
#
# AN INTERPOLATION IS MATCHED AS A WHOLE SPAN, not by excluding terminators one at a time.
# That is the lesson of two bugs, not one. First `}` was excluded, which truncated
# `${DB_PASSWORD}` to `${DB_PASSWORD`; the fix was to stop excluding `}`. But whitespace is
# still excluded -- correctly, for a bare value -- and Compose's required (`:?`) and default
# (`:-`) forms carry a multi-word human message INSIDE the braces, so
# `${VAR:?set VAR in .env}` truncated at the first space instead. Both truncations then failed
# the placeholder test for the same missing brace, and the rule flagged a REFERENCE to a secret
# as a secret -- the exact shape secrets.md tells people to write.
#
# Excluding one more character would only move the boundary again, so the `${...}` alternative
# comes first and consumes the entire span whatever it contains. A quoted JSON value still ends
# at its quote, and a bare value still ends at the first terminator.
KEY_VALUE = re.compile(
    r"""["']?(?P<key>[\w.:\-]*[\w])["']?\s*(?:=>|[:=])\s*["']?"""
    r"""(?P<value>\$\{[^}]*\}|[^"'\s,;]*)["']?"""
)

# Inside a connection string: `...;Password=hunter2;...`. The line must ALSO carry a host or
# database part, which is what makes it a connection string rather than any text containing
# `password=`. Without that guard this fired on its own source -- the regex below literally
# contains `password|pwd)\s*=` -- and it would fire on any code that parses one.
CONNECTION_SECRET = re.compile(r"(?i)(?<![\w-])(?:password|pwd)\s*=\s*(?P<value>[^;\"']{1,})")
CONNECTION_CONTEXT = re.compile(
    r"(?i)(?<![\w-])(?:server|data\s+source|host|database|initial\s+catalog|uid|user\s+id)\s*="
)

# What a committed value is ALLOWED to be. Everything secrets.md permits -- an empty value, a
# name, a pointer, an obviously-fake placeholder -- plus the interpolation forms, which are a
# reference to a secret rather than one.
PLACEHOLDER = re.compile(
    r"^\s*$"
    r"|^(?:\$\{[^}]*\}|\$[A-Za-z_]\w*|%[A-Za-z_]\w*%|\{\{[^}]*\}\}|<[^>]*>)\s*$"
    r"|^(?:change[_-]?me|your[-_\w]*|example|dummy|placeholder|sample|fake|todo|tbd"
    r"|none|null|nil|notset|not[_-]?set|redacted|secret|password|xxx+|\*+|\.\.\.+"
    r"|set[_-]?in[_-]?\w+|see[_-]?\w+)\s*$",
    re.IGNORECASE,
)

# A value made of one repeated character (`aaaa`, `0000`) is a placeholder by any reading.
REPEATED = re.compile(r"^(.)\1{2,}$")

# Config files whose values are key/value pairs -- the shape an automatic move can handle. A
# credential in SOURCE is a different problem: relocating it needs a code change, not a text
# edit, so those block instead. See `is_config`.
CONFIG_SUFFIXES = (".json", ".yml", ".yaml", ".ini", ".cfg", ".conf", ".toml", ".xml", ".config")
ENV_EXAMPLE = re.compile(r"^\.env\.[\w.-]+$|^\.env$", re.IGNORECASE)

# A staged `.env` is a finding decided entirely by its NAME. Never opened.
DOTENV_FILENAME = re.compile(r"^\.env(?:\.local|\.production|\.development)?$", re.IGNORECASE)


def is_never_read(path: Path) -> bool:
    """Whether the scanner must decide this file by its NAME and never open it.

    THE CLAIM THIS MAKES TRUE. This module's header has always said a `.env` is detected by
    name and never opened, and the rule below really does return before touching `lines`.
    That was not the whole story: the reader in standards_exemption_report -- the one the
    driver imports -- read every candidate file into memory before dispatching to any rule,
    so a `.env` in scope was opened, decoded and held, by the scanner belonging to an estate
    whose standing rule is that secret-bearing files are not read. Nothing printed a value,
    so the leak was of custody rather than of output. It was still the documented invariant
    being false in the one file it exists for. Found in the B3D pack, copied here 2026-09-05.

    Exported from here rather than added to standards_scope, because scope answers "is this
    file checked?" -- and a `.env` IS checked, by name. This answers a different question:
    what the reader is allowed to do to get there. One predicate, so the reader and
    `check_committed_credentials` cannot disagree about which files are never opened and
    which are reported by name -- those must be the same files.
    """
    return bool(DOTENV_FILENAME.match(path.name))


# Values short enough that they cannot be a working credential. `pwd=1` in a fixture is noise,
# not a leak. Eight is below every real key length and above every placeholder people type.
MIN_CREDENTIAL_LENGTH = 8


class Leak(NamedTuple):
    """One credential found on one line. The VALUE is deliberately not carried."""

    index: int
    key: str
    what: str
    tier: str


def is_config(path: Path) -> bool:
    """Whether this file is key/value configuration rather than source code."""
    return path.suffix.casefold() in CONFIG_SUFFIXES or bool(ENV_EXAMPLE.match(path.name))


def is_placeholder(value: str) -> bool:
    """Whether a value is one secrets.md explicitly allows to be committed."""
    stripped = value.strip().strip("\"'")
    if PLACEHOLDER.match(stripped) or REPEATED.match(stripped):
        return True
    return len(stripped) < MIN_CREDENTIAL_LENGTH


def known_format(text: str) -> str | None:
    """The name of the credential format this text contains, if any."""
    for name, pattern in CREDENTIAL_FORMATS:
        if pattern.search(text):
            return name
    return None


def leaks_in(path: Path, lines: list[str]) -> Iterable[Leak]:
    """Every credential this file appears to commit.

    Order matters: a known FORMAT is reported even when the key name is innocent, because
    `"note": "sk_live_…"` is still a live key. The shape check then runs only where the format
    check found nothing, so one line never produces two findings.
    """
    config = is_config(path)
    for index, line in enumerate(lines):
        formatted = known_format(line)
        if formatted is not None:
            found = KEY_VALUE.search(line)
            yield Leak(index, found.group("key") if found else path.name, formatted, "format")
            continue

        connection = CONNECTION_SECRET.search(line)
        if connection and CONNECTION_CONTEXT.search(line) and not is_placeholder(connection.group("value")):
            yield Leak(index, "connection string", "a password inside a connection string", "shape")
            continue

        # THE SHAPE TIER IS CONFIG-ONLY, and that is a design decision rather than a shortcut.
        # In configuration a `password:` key holding a real-looking value IS a credential --
        # that is what configuration is for, and it is where the estate's actual leak was
        # (appsettings.Development.json). In SOURCE the same shape is overwhelmingly code
        # *about* credentials: a constant named SECRET_RULE, a regex named CONNECTION_SECRET,
        # a DTO property, a test fixture. This rule fired on its own detector before the split,
        # which is the clearest demonstration available that the source side is unwinnable on
        # key-name evidence alone.
        #
        # Source is not unprotected: the FORMAT tier runs everywhere, and a pasted API key,
        # token or PEM block has a known shape. What is genuinely given up is a hand-typed
        # password assigned to a variable in code, with no recognisable format -- a real gap,
        # named here rather than papered over, and one `/code-review` still reads for.
        if not config:
            continue

        for found in KEY_VALUE.finditer(line):
            key, value = found.group("key"), found.group("value")
            if not SECRET_KEY_NAME.search(key) or is_placeholder(value):
                continue
            yield Leak(index, key, "a value that is not an obvious placeholder", "shape")
            break


def check_committed_credentials(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a credential about to enter git, or already in it.

    A staged `.env` is judged on its NAME alone -- the file is never opened, because the
    standing rule is that secret-bearing files are not read, and its name is already enough to
    know it does not belong in a commit.
    """
    # The SAME predicate the reader asks, not the regex behind it. Two call sites reading one
    # pattern by hand can drift; two asking one function cannot -- and a file the reader
    # refuses to open but the rule does not report would be the silent pass in its purest form.
    if is_never_read(path):
        yield Violation(
            path=path,
            line=1,
            rule=SECRET_RULE,
            message=(
                f"`{path.name}` is being committed. This file holds real values by convention, "
                f"so it belongs in .gitignore and never in a commit -- it has NOT been read "
                f"here, and does not need to be for that to be true. Commit `.env.example` "
                f"instead, carrying the key names with empty values. See "
                f".claude/rules/secrets.md."
            ),
        )
        return

    for leak in leaks_in(path, lines):
        if line_exemption_reason(lines, leak.index, SECRET_RULE):
            continue

        # The value is never quoted back. The key and the format are enough to find it, and
        # printing the secret would move the leak into the terminal and the CI log.
        placement = (
            "moved into the framework secret store, a gitignored .env, or generated at first run"
            if is_config(path)
            else "read from configuration at runtime -- a literal in source cannot be moved by a "
            "tool, because reading it from config is a code change"
        )
        yield Violation(
            path=path,
            line=leak.index + 1,
            rule=SECRET_RULE,
            message=(
                f"`{leak.key}` appears to hold {leak.what}. A credential that works anywhere "
                f"must never reach git -- not in production config, not in a fixture, and not "
                f"in a development-only settings file, which is still a committed file. It "
                f"must be {placement}. If this value is a deliberate fake that cannot work "
                f"anywhere, say so on the line: `standards: {SECRET_RULE} exempt -- <why>`. "
                f"See .claude/rules/secrets.md."
            ),
        )

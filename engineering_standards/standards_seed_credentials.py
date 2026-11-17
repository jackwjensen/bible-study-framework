#!/usr/bin/env python3
"""A seeded account's password, wired in through configuration. The enforceable half of the
"Seed test systems" section in claude/rules/secrets.md.

Rules here: seeded-credential-config.

THE SHAPE. A seeder, restore script or app that reads an admin / demo / seed account's password
from configuration -- a key in `.env.example`, a compose variable, an `appsettings` entry, an
environment read in code -- and creates or resets the account with it. The value itself never
reaches git, so `committed-credential` passes it, correctly. What it misses is the MECHANISM:
a standing "set a password, get an admin" path on every machine that runs the code. Whoever can
set that variable -- a wrong `.env`, a value set on a server by mistake -- gets into the system,
and because the estate uses one test password everywhere, the same value opens every project.

WHY IT EARNS A RULE (2026-10-12). ellengaard-dk's restore created `localadmin` from
`LOCAL_ADMIN_PASSWORD` in `.env`, documented in `.env.example` -- the shape secrets.md itself
recommended at the time ("a seeder takes the password from configuration, never a literal").
InvoTrack seeds its admin from a configuration key the same way. Jack: test systems should be
seeded, "but not through a feature where you can set a password and then gain access". The
right shape is an explicit, local-only seed step that takes the values on STDIN from the PC's
values file; snippets in templates/seed-users/.

WHAT IT LOOKS AT. Two halves, because the pattern needs both and either alone is the defect:
  DECLARATION  the key, in a file that configures the code: `.env.example` (and other `.env.*`
               examples -- never `.env` itself, which is never opened), compose files,
               `appsettings*.json` / `settings*.json` (nested paths joined, so
               `{"Demo": {"Password": ...}}` is `Demo:Password`).
  READ         the key, read from the environment or configuration in source: PHP `getenv` /
               `$_ENV`, Python `os.environ` / `os.getenv`, C# `GetEnvironmentVariable` /
               `configuration["..."]` / `GetValue<T>("...")`, JS/TS `process.env`, shell `$VAR`,
               PowerShell `$env:VAR`.

MIGRATIONS INCLUDED -- THE ONE RULE THAT READS THEM (2026-10-14). The scanner skips every
migrations directory, because a framework writes most of what is in one. That skip made this
rule blind in its worst home: a hand-written data migration creating a superuser from an
environment variable runs on EVERY environment the schema reaches, production included, so
the "set a password, get an admin" path ships to every server rather than sitting in a
local-only script. allegro-it-services had exactly that, and the whole-tree scan called it
clean while this rule's own self-test -- which walked the tree without the exclusion -- found
it. The two now agree in the direction of the rule: `standards_scope.should_check_migration`
admits a source file skipped ONLY for being under `migrations/`, and
`standards_dispatch.check_migration_file` runs this rule on it and nothing else. Generated
migrations stay quiet without help: a framework never writes an environment read of a
seeded key. Pinned in test_standards_seed_credentials.py.

WHAT COUNTS AS A SEEDED ACCOUNT'S PASSWORD. Decided on the key's NAME, normalised (case and
separators dropped): it ends in a password word and, before that, names an account a seeder
would create -- admin, superuser, seed, demo, a local/test/default/initial/bootstrap user.
`MYSQL_ROOT_PASSWORD` or `SMTP_PASSWORD` name no such account and stay quiet: a database or
mail credential is `committed-credential`'s subject, not this rule's.

WHY IT IS LINE-EXEMPTABLE. A third-party tool configured by its own documented variable --
pgAdmin's `PGADMIN_DEFAULT_PASSWORD`, Grafana's admin password in a local-only compose service --
has the name and is not an estate seeder. That is a human's call, made on the line with a
reason; the rule's job is to make sure the question is asked.

Source of truth: engineering-standards/engineering_standards/standards_seed_credentials.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple

from standards_core import Violation
from standards_exemptions import line_exemption_reason
from standards_secrets import is_never_read

SEEDED_RULE = "seeded-credential-config"

# The NAME test, run on a normalised key (lower case, `_ - . :` removed). The account words are
# the ones a seeder creates; the password words end the key, which is how these are always named
# (`ADMIN_PASSWORD`, `Demo:AdminPassword`, `SeedUserPwd`).
_ACCOUNT = r"(?:admin|superuser|seed|demo|(?:local|test|default|initial|bootstrap|dev)user)"
SEEDED_KEY = re.compile(rf"{_ACCOUNT}\w*?(?:password|passwd|pwd|pass)$")
_SEPARATORS = re.compile(r"[_\-.:\s]")

# ---- declarations ----------------------------------------------------------------------------
ENV_EXAMPLE = re.compile(r"^\.env\.[\w.-]+$", re.IGNORECASE)
COMPOSE = re.compile(r"^(?:docker-)?compose(?:[\w.-]*)?\.ya?ml$", re.IGNORECASE)
JSON_SETTINGS = re.compile(r"^(?:appsettings[\w.-]*|settings[\w.-]*)\.json$", re.IGNORECASE)

ENV_LINE_KEY = re.compile(r"^\s*#?\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
YAML_LINE_KEY = re.compile(r"^\s*-?\s*([A-Za-z_][\w.]*)\s*[:=]")
INTERPOLATION = re.compile(r"\$\{([A-Za-z_]\w*)")
JSON_LINE_KEY = re.compile(r'"([\w.:\-]+)"\s*:')

# ---- reads in source, per language -----------------------------------------------------------
_Q = r"""["']"""
SOURCE_READS: dict[str, tuple[re.Pattern[str], ...]] = {
    ".php": (
        re.compile(rf"getenv\(\s*{_Q}([\w.:\-]+){_Q}"),
        re.compile(rf"\$_(?:ENV|SERVER)\[\s*{_Q}([\w.:\-]+){_Q}"),
        re.compile(rf"\benv\(\s*{_Q}([\w.:\-]+){_Q}"),
    ),
    ".py": (
        re.compile(rf"os\.(?:getenv|environ\.get)\(\s*{_Q}([\w.:\-]+){_Q}"),
        re.compile(rf"os\.environ\[\s*{_Q}([\w.:\-]+){_Q}"),
    ),
    ".cs": (
        re.compile(r'GetEnvironmentVariable\(\s*"([\w.:\-]+)"'),
        re.compile(r'\[\s*"([\w.:\-]+)"\s*\]'),
        re.compile(r'GetValue<[^>]+>\(\s*"([\w.:\-]+)"'),
    ),
    ".js": (
        re.compile(r"process\.env\.([A-Za-z_]\w*)"),
        re.compile(rf"process\.env\[\s*{_Q}([\w.:\-]+){_Q}"),
    ),
    ".sh": (re.compile(r"\$\{?([A-Za-z_]\w*)"),),
    ".ps1": (re.compile(r"\$env:([A-Za-z_]\w*)", re.IGNORECASE),),
}
_ALIASES = {".ts": ".js", ".tsx": ".js", ".mjs": ".js", ".cjs": ".js", ".jsx": ".js", ".bash": ".sh"}


class Finding(NamedTuple):
    """One seeded-password key on one line, and whether it was declared or read."""

    index: int
    key: str
    how: str


def is_seeded_key(key: str) -> bool:
    """Whether a configuration key names a seeded account's password."""
    return bool(SEEDED_KEY.search(_SEPARATORS.sub("", key).casefold()))


def _line_keys(lines: list[str], patterns: Iterable[re.Pattern[str]], how: str) -> Iterator[Finding]:
    """One finding per line whose matched key names a seeded password."""
    compiled = tuple(patterns)
    for index, line in enumerate(lines):
        for pattern in compiled:
            hit = next((m.group(1) for m in pattern.finditer(line) if is_seeded_key(m.group(1))), None)
            if hit is not None:
                yield Finding(index, hit, how)
                break


def _json_paths(node: object, prefix: str = "") -> Iterator[str]:
    """Every key path in parsed JSON, `Parent:Child` style (the .NET configuration form)."""
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}:{key}" if prefix else str(key)
            yield path
            yield from _json_paths(value, path)
    elif isinstance(node, list):
        for item in node:
            yield from _json_paths(item, prefix)


def _json_findings(lines: list[str]) -> Iterator[Finding]:
    """Nested paths when the file parses (appsettings may carry comments; then line keys)."""
    try:
        parsed = json.loads("\n".join(lines))
    except ValueError:
        yield from _line_keys(lines, (JSON_LINE_KEY,), "declared")
        return
    reported: set[int] = set()
    for path in _json_paths(parsed):
        if not is_seeded_key(path):
            continue
        leaf = path.rsplit(":", 1)[-1]
        index = next((i for i, line in enumerate(lines) if f'"{leaf}"' in line and i not in reported), 0)
        reported.add(index)
        yield Finding(index, path, "declared")


def findings_in(path: Path, lines: list[str]) -> Iterator[Finding]:
    """Every seeded-password key this file declares or reads."""
    name = path.name
    if is_never_read(path):
        return
    if ENV_EXAMPLE.match(name):
        yield from _line_keys(lines, (ENV_LINE_KEY,), "declared")
    elif COMPOSE.match(name):
        yield from _line_keys(lines, (YAML_LINE_KEY, INTERPOLATION), "declared")
    elif JSON_SETTINGS.match(name):
        yield from _json_findings(lines)
    else:
        suffix = path.suffix.casefold()
        patterns = SOURCE_READS.get(_ALIASES.get(suffix, suffix))
        if patterns:
            yield from _line_keys(lines, patterns, "read from configuration")


def check_seeded_credentials(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a seeded account's password wired in through configuration."""
    for finding in findings_in(path, lines):
        if line_exemption_reason(lines, finding.index, SEEDED_RULE):
            continue
        yield Violation(
            path=path,
            line=finding.index + 1,
            rule=SEEDED_RULE,
            message=(
                f"`{finding.key}` is {finding.how} as a seeded account's password. That makes "
                f"a standing 'set a password, get an admin' path on every machine that runs "
                f"this code -- whoever can set the value gets in, and the estate's test "
                f"password is the same everywhere. Seed test accounts through an explicit, "
                f"local-only step that refuses outside development and takes the values on "
                f"stdin from the PC's values file (templates/seed-users/ in the pack). If this "
                f"is a third-party tool's own documented setting rather than an estate seeder, "
                f"say so on the line: `standards: {SEEDED_RULE} exempt -- <why>`. See "
                f".claude/rules/secrets.md."
            ),
        )

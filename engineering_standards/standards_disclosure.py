#!/usr/bin/env python3
"""Things a repository should never ship: a live config-disclosure page, and a schema dump.

Rules here: disclosure-file, committed-sql-dump.

WHY THE TWO SHARE A MODULE. Neither is about how the code is WRITTEN -- both are about
something ENTERING the tree that has no business being version-controlled at all. A
`phpinfo.php` is a diagnostic that turned into a public endpoint; a `revjus.sql` is a
one-off export that turned into the repository's idea of a schema. They change for the same
reason (someone committed a convenience and it stayed), and neither has an opinion about a
line of application logic, which is what keeps them out of the per-language dispatcher's
subject and here instead.

    disclosure-file     a phpinfo() page, or a sensitive file by name -- a config, an
                        installer, a committed database, a certificate, a credential store
    committed-sql-dump   a *.sql schema dump checked in instead of migrations

WHY disclosure-file IS A LIST, NOT ONE FILE. phpinfo is the classic case, but the family is
"a file whose mere presence in the tree is the leak", and it spans the estate's frameworks:
WordPress `wp-config.php` (DB credentials + auth salts) and its `wp-admin/` installer; a
committed SQLite database (Django's `db.sqlite3`, Laravel's `database.sqlite`, or any
`*.sqlite`), whose rows ship with the repo; a .NET/Blazor `*.pubxml.user` (the deploy
password) or a `*.pfx`/`*.p12` (a certificate WITH its private key); an Adminer drop-in
database console; and the credential stores `.htpasswd`, `.pypirc`, `.git-credentials`,
`.netrc`. None is caught by the credential-FORMAT scanner, because a DB password or a binary
key store has no recognisable shape -- it is the NAME that identifies them.

WHY disclosure-file EARNS A RULE. A real audit found a repo shipping `phpinfo.php` whose
entire body was `<?php phpinfo(); ?>`. `phpinfo()` renders the COMPLETE PHP and server
configuration -- absolute paths, PHP and extension versions, every loaded module, the full
environment including anything the process inherited -- as a single HTML page. Anyone who
can reach the URL reads it, unauthenticated, and it is the reconnaissance step that turns a
vague "there is a PHP app here" into "here is its exact stack and where its files live". The
fix is not to guard it; it is to delete it. A deliberate local diagnostic is a local file,
and a local file does not get committed.

WHY committed-sql-dump EARNS A RULE. The same audit found `revjus.sql` (1.4 MB) and
`RevjusStaging_15102025.sql` (3.6 MB) -- hand-exported schema dumps checked in beside the
code, standing in for migrations. Two defects in one artefact. First, reproducibility: a
dump is a FROZEN SNAPSHOT, not a history. It records what the schema was on the day someone
clicked export, with no record of how it got there and no way to apply it forward
incrementally; the next person diverges from it silently and nothing reconciles the two.
Second, exposure: a dump that carries rows carries whatever was in those rows, and a
staging export is exactly where real personal data rides along unnoticed. Migrations are
the mechanism that fixes both -- versioned, reviewable, applyable -- which is why a `.sql`
UNDER a migrations directory is the opposite finding and is never flagged.

THE THIRD KIND OF .sql FILE, and why the rule needed a marker. A dump is the OUTPUT of an
exporter. A migration is a STEP. But a schema can also be the INPUT to a compiler -- a file
whose whole job is to create an empty database from nothing, every build, as part of
producing an artifact. sourcetext.ai's `pipeline/schema.sql` is one: `pipeline/spec.py`
reads it to build a read-only corpus database that is then filled from ingested sources and
shipped inside an image. Neither of the two defects above can occur -- there is no snapshot
to diverge from, because the file IS the definition and the database is rebuilt rather than
migrated, and no rows can ride along, because it declares tables and inserts nothing.

That file was flagged on 2026-09-01 with no honest way to answer. Its three location
opt-outs all describe something it is not, and this was the ONLY rule in the pack with no
in-file marker -- a gap this module's own docstring used to assert as a design choice. It
was not one: it made the rule unfalsifiable, and a rule that cannot be answered is a rule
that gets worked around. So `committed-sql-dump` now takes the same marker every other
smoke-alarm rule takes, with the same reason floor:

    -- standards: committed-sql-dump exempt -- <why this is not a dump>

The reason is the gate, exactly as it is for file-length. "It is fine" is rejected on
length; an argument you would defend in review is not.

Source of truth: engineering-standards/engineering_standards/standards_disclosure.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import CheckConfig, Violation, iter_code_lines, warn_unreadable
from standards_exemptions import HEADER_SCAN_LINES, exemption_reason, line_exemption_reason
from standards_scope import MIGRATION_FRAGMENT
from standards_tree import walk_repository

# ---- disclosure-file ---------------------------------------------------------------------

DISCLOSURE_RULE = "disclosure-file"

# A call to phpinfo(). `\b` before the name means `myphpinfo(` -- a different function that
# merely ends in the same letters -- is not matched, and requiring `(` after optional
# whitespace means `phpinfo_cache(` is not matched either. Case-insensitive because PHP
# function names are, so `PHPInfo()` is the same call.
PHPINFO_CALL = re.compile(r"\bphpinfo\s*\(", re.IGNORECASE)


def check_disclosure_file(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a .php file that calls phpinfo(), the full-server configuration page.

    Fires per call site, on CODE lines only -- `iter_code_lines` skips comments and
    docstrings, so a `// phpinfo();` note explaining what not to do is not itself a finding.
    That also means the classic `phpinfo.php` whose whole body is `<?php phpinfo(); ?>` and
    a `phpinfo()` buried in a larger diagnostic script are the same case: both reach here as
    a code line carrying the call, and both are reported.

    Line-exemptable, because the one legitimate shape -- a diagnostic that genuinely must
    live in the repo for a reason the author can defend -- is rare enough to state in place.
    """
    if path.suffix.casefold() != ".php":
        return

    for line_number, line in iter_code_lines(lines, ".php"):
        if not PHPINFO_CALL.search(line):
            continue
        if line_exemption_reason(lines, line_number - 1, DISCLOSURE_RULE):
            continue

        yield Violation(
            path=path,
            line=line_number,
            rule=DISCLOSURE_RULE,
            message=(
                "This .php file calls `phpinfo()`, which renders the entire PHP and server "
                "configuration -- absolute paths, PHP and extension versions, every loaded "
                "module, and the inherited environment -- as a single page readable by "
                "anyone who can reach the URL. That is unauthenticated reconnaissance of the "
                "exact stack and layout of the deployment. Delete it: guarding it is not the "
                "fix. If it is a deliberate local diagnostic, it does not belong in version "
                "control -- keep it as a local file. If this one genuinely must be tracked, "
                f"say why on the line: `standards: {DISCLOSURE_RULE} exempt -- <reason>`."
            ),
        )


# ---- disclosure-file: sensitive files by name --------------------------------------------

# Basenames (lowercased) that should never be in a repo: each is a config, a store of
# credentials, or a committed database. Mapped to the reason so the message can name it.
# `wp-config-sample.php` is NOT here and never matches -- it is the safe template WordPress
# ships, exactly the file this rule steers people towards.
SENSITIVE_BASENAMES: dict[str, str] = {
    "wp-config.php": "holds the WordPress database credentials and auth salts",
    ".htpasswd": "holds HTTP basic-auth password hashes",
    ".htdigest": "holds HTTP digest-auth credentials",
    ".pypirc": "holds PyPI upload credentials",
    ".git-credentials": "stores git credentials in the clear",
    ".netrc": "stores machine login credentials in the clear",
    "_netrc": "stores machine login credentials in the clear",
    "db.sqlite3": "is Django's default database — its rows ship with the repo",
    "database.sqlite": "is Laravel's default database — its rows ship with the repo",
    "laravel.log": "is an application log — stack traces, queries, sometimes personal data",
}

# Framework files whose NAME alone is too generic (`install.php`, `setup-config.php` exist
# elsewhere) but whose LOCATION identifies them. Matched as a path tail.
SENSITIVE_PATH_TAILS: dict[str, str] = {
    "/wp-admin/setup-config.php": "is the WordPress installer's database-config step",
    "/wp-admin/install.php": "is the WordPress installer — a reachable one lets anyone reinstall the site",
}

# Suffixes that are a leak by their nature, wherever they sit. `.pubxml.user` is a compound
# suffix, so it is tested with endswith like the rest.
SENSITIVE_SUFFIXES: dict[str, str] = {
    ".sqlite": "is a committed SQLite database — its rows ship with the repo",
    ".sqlite3": "is a committed SQLite database — its rows ship with the repo",
    ".pfx": "is a certificate bundle that includes its private key",
    ".p12": "is a certificate bundle that includes its private key",
    ".pubxml.user": "is a .NET publish profile's user file — it stores the deploy password",
}

# An Adminer/phpMyAdmin drop-in: a single PHP file that is a whole database GUI. Matched by a
# name STARTING with the tool and ending .php, so `adminer-4.8.1.php` is caught too.
ADMINER_PREFIX = "adminer"

# Where an otherwise-sensitive file is plausibly a deliberate, non-secret fixture or template,
# so it is not a finding -- the same location opt-out committed-sql-dump uses, plus sample/
# example, which is how a framework ships a safe template.
SENSITIVE_EXEMPT_DIR_FRAGMENTS = (
    "/fixtures/",
    "/seeds/",
    "/seed/",
    "/tests/",
    "/test/",
    "/__tests__/",
    "/testdata/",
    "/samples/",
    "/sample/",
    "/examples/",
    "/example/",
    "/demo/",
    "/demos/",
)


def _sensitive_reason(posix_lower: str, name_lower: str) -> str | None:
    """Why this file should never be version-controlled, or None. `posix_lower` is the
    repo-relative path with a leading slash and no trailing one."""
    if name_lower in SENSITIVE_BASENAMES:
        return SENSITIVE_BASENAMES[name_lower]
    if name_lower.startswith(ADMINER_PREFIX) and name_lower.endswith(".php"):
        return "is an Adminer-style database console — a full database GUI in a single file"
    for tail, why in SENSITIVE_PATH_TAILS.items():
        if posix_lower.endswith(tail):
            return why
    for suffix, why in SENSITIVE_SUFFIXES.items():
        if name_lower.endswith(suffix):
            return why
    return None


def check_sensitive_files(repo_root: Path, config: CheckConfig) -> Iterable[Violation]:
    """Flag a file whose mere presence in the tree is a leak -- by name, path or suffix.

    REPO-LEVEL, like committed-sql-dump and for the same reasons: it is about what the tree
    CONTAINS, so it runs only on a whole-tree scan, never at commit. It reads no file content
    -- only the name -- so a large committed database or certificate is never opened.

    GIT-IGNORED FILES ARE SKIPPED, and this matters more here than for the sql-dump rule: a
    local `db.sqlite3` or `.env`-adjacent file is routinely gitignored and present on disk, and
    flagging one git does not track would be a false positive on every developer's machine. The
    rule is about what is committed, so `walk_repository` walks past what git ignores.

    A file under fixtures/seeds/tests/samples/examples is treated as a deliberate, reviewed
    template or fixture and skipped -- the per-location opt-out this family uses in place of a
    config flag.
    """
    _ = config  # no per-repo flag: the opt-out is a fixtures/samples/tests location

    for dirpath, dirnames, filenames in walk_repository(repo_root):
        dirnames[:] = [d for d in dirnames if d.casefold() not in PRUNED_DIRECTORIES]

        for filename in filenames:
            path = Path(dirpath) / filename
            posix = "/" + path.relative_to(repo_root).as_posix().casefold()
            reason = _sensitive_reason(posix, filename.casefold())
            if reason is None:
                continue
            if any(fragment in posix + "/" for fragment in SENSITIVE_EXEMPT_DIR_FRAGMENTS):
                continue

            yield Violation(
                path=path,
                line=1,
                rule=DISCLOSURE_RULE,
                message=(
                    f"'{path.name}' {reason}. A file like this does not belong in version "
                    f"control: remove it from the repo and gitignore it, and rotate anything it "
                    f"exposed (a committed credential or certificate stays in history after the "
                    f"file is deleted). Where a framework ships a safe template, commit that "
                    f"instead -- `wp-config-sample.php`, an `.env.example` with empty values. If "
                    f"this is genuinely a non-secret fixture, keep it under a "
                    f"fixtures/ or samples/ directory."
                ),
            )


# ---- committed-sql-dump ------------------------------------------------------------------

SQL_DUMP_RULE = "committed-sql-dump"

# The signature of a schema dump: a `CREATE TABLE` statement. `\s+` between the words tolerates
# the varied whitespace a dumper emits, and the match is case-insensitive because SQL keywords
# are. This is the whole discriminator -- a file of only INSERTs is data, not a schema, and is
# not this rule's subject.
CREATE_TABLE = re.compile(r"CREATE\s+TABLE", re.IGNORECASE)

# Directories pruned from the walk entirely: never ours to judge, and huge. Pruned rather than
# fragment-matched so the walk does not descend into them at all. `.vs`/`.idea`/`.vscode` are
# IDE metadata -- Visual Studio keeps a `.vs/slnx.sqlite` of solution state there, which is not
# application data any more than `.git` is, and is always gitignored; pruning them is the same
# call as pruning `.git`.
PRUNED_DIRECTORIES = frozenset({"node_modules", "vendor", ".git", ".vs", ".idea", ".vscode"})

# Path fragments that make a `.sql` file legitimate where it sits, so it is not a finding:
#   * a migrations directory IS the versioned mechanism this rule steers people towards --
#     `migrations/`, `migration/`, and Rails' `db/migrate/` are the three spellings across
#     the estate's stacks;
#   * a `fixtures/`/`seeds/` directory is where an intentional, reviewed data file lives, so
#     it is the per-location opt-out for a dump that is deliberate. (A dump that carries
#     personal data still does not belong there -- the message says so.)
SQL_EXEMPT_DIR_FRAGMENTS = (
    MIGRATION_FRAGMENT,
    "/migration/",
    "/db/migrate/",
    "/fixtures/",
    "/seeds/",
    "/seed/",
)

# How much of a `.sql` file to read at once, with an overlap carried between chunks so a
# `CREATE TABLE` straddling a chunk boundary is still seen. A dump can carry the statement
# anywhere -- a 3.6 MB export puts its last table near the end -- so the scan continues until
# it finds one or reaches EOF, but it stops the instant it does, which is what keeps a huge
# file from being read in full when it need not be.
SQL_READ_CHUNK = 65536
SQL_CHUNK_OVERLAP = 64


def _sql_is_schema_dump(path: Path) -> bool:
    """True when the file contains a `CREATE TABLE` statement, read in chunks.

    Read with errors='replace' because a dump's encoding is whatever the exporting tool
    chose and this only needs to spot an ASCII keyword; a decode error must never decide the
    question. Any read failure answers False -- a file that cannot be read is not evidence of
    a dump.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            carry = ""
            while True:
                chunk = handle.read(SQL_READ_CHUNK)
                if not chunk:
                    return False
                if CREATE_TABLE.search(carry + chunk):
                    return True
                carry = chunk[-SQL_CHUNK_OVERLAP:]
    except OSError as error:
        # False means "not a dump", which is what a clean file looks like -- so an unreadable
        # .sql would leave committed-sql-dump silent about the one file type it exists for.
        warn_unreadable(path, error, "committed-sql-dump could not examine this file")
        return False


def _sql_exemption_reason(path: Path) -> "str | None":
    """The file's own answer to this rule, or None.

    Reads only the header, for two reasons. The marker is required to be there anyway --
    buried at line 900 it would be invisible to anyone opening the file, which is the whole
    point of putting it IN the file -- and a `.sql` under this rule's eye may be megabytes,
    so reading it whole to look at its first 40 lines would be the expensive way to ask a
    cheap question.

    `--` is SQL's line comment and is already in the shared parser's prefixes, so the marker
    needs no syntax of its own here. Any read failure answers None: a file that cannot be
    read has not claimed an exemption, which leaves the rule to judge it on its content.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            header = [line for _, line in zip(range(HEADER_SCAN_LINES), handle, strict=False)]
    except OSError:
        return None
    return exemption_reason(header, SQL_DUMP_RULE)


def check_committed_sql_dump(repo_root: Path, config: CheckConfig) -> Iterable[Violation]:
    """Flag a schema dump checked into the tree instead of expressed as migrations.

    REPO-LEVEL, and therefore run only on a whole-tree scan -- never at the commit stage.
    A commit checks the commit, not the repository (see standards_git.py and check_gitignore
    in standards_layout.py): a repo-level finding at commit time would block every commit,
    including the one that deletes the dump, which is the trap that once destroyed hundreds
    of uncommitted lint fixes. The whole-tree scan is where a rule about what the repository
    CONTAINS belongs.

    Walks the filesystem (pruning node_modules/vendor/.git) rather than asking git, for the
    same reason check_gitignore reads the working tree: the scanner is handed a directory,
    not a repository object.

    GIT-IGNORED FILES ARE SKIPPED, exactly as in `check_sensitive_files` above. A local
    `dump.sql` that somebody pulled down to inspect is gitignored and present on disk, and
    this rule is about what is COMMITTED -- flagging a file git does not track would be a
    finding on a developer's machine that CI never reproduces and nobody can act on. The two
    rules in this module diverged on that point until 2026-09-05 (found by review in the B3D
    pack); a module that is right about the same question in two different ways has one of
    them wrong.

    THEN TWO OPT-OUTS, and the order they are tested in is deliberate. By LOCATION: a `.sql`
    under a migrations, fixtures or seeds directory is skipped, because migrations are the
    mechanism this rule steers towards and those data directories are where a deliberate
    data file lives. By DECLARATION: a written `standards: committed-sql-dump exempt --
    <reason>` marker in the file's first 40 lines, for the schema that is a compiler's
    INPUT rather than an exporter's output (see the module docstring). Location is tested
    first because it costs a string search against a path already in hand; the marker costs
    a file read.

    Both are tested BEFORE `_sql_is_schema_dump`, which is the expensive step -- it may
    stream megabytes looking for a `CREATE TABLE`. An answered file is never scanned.

    `config` is accepted to match the repo-level check shape; there is no per-repo weakening
    flag for this rule, deliberately -- a blanket path exclusion would hide every other rule
    from the same file, and the point of the marker is that the reason lives where the file
    is read.
    """
    _ = config  # no per-repo flag: the opt-outs are a location and an in-file marker
    for dirpath, dirnames, filenames in walk_repository(repo_root):
        dirnames[:] = [d for d in dirnames if d.casefold() not in PRUNED_DIRECTORIES]

        for filename in filenames:
            if not filename.casefold().endswith(".sql"):
                continue

            path = Path(dirpath) / filename
            relative = "/" + path.relative_to(repo_root).as_posix().casefold() + "/"
            if any(fragment in relative for fragment in SQL_EXEMPT_DIR_FRAGMENTS):
                continue

            if _sql_exemption_reason(path) is not None:
                continue

            if not _sql_is_schema_dump(path):
                continue

            yield Violation(
                path=path,
                line=1,
                rule=SQL_DUMP_RULE,
                message=(
                    f"'{path.name}' is a checked-in SQL schema dump -- it contains "
                    f"`CREATE TABLE`. A dump is a frozen snapshot of the schema on the day it "
                    f"was exported, not a versioned history: it cannot be reviewed as a change, "
                    f"cannot be applied forward incrementally, and drifts from the code with "
                    f"nothing reconciling the two. The schema belongs in migrations (versioned, "
                    f"reviewable, applyable), where a `.sql` file is never flagged. If this is a "
                    f"one-off fixture rather than the schema, keep it out of the repo or move it "
                    f"under a fixtures/ or seeds/ directory. If it is neither -- a schema that is "
                    f"the INPUT to a build rather than the output of an exporter, creating an "
                    f"empty database from nothing every time -- say so in its header: "
                    f"`-- standards: committed-sql-dump exempt -- <why>`. Either way, confirm it "
                    f"carries no personal "
                    f"data, since an export, especially of staging, is exactly where rows ride "
                    f"along unnoticed."
                ),
            )

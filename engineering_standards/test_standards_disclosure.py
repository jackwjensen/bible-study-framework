"""Cases for the disclosure-file and committed-sql-dump rules.

THE CASES THAT MOTIVATED THEM are both from one real audit. A repo shipped `phpinfo.php`
whose whole body was `<?php phpinfo(); ?>` -- a full-server-config page reachable by anyone
-- and, beside its code, `revjus.sql` (1.4 MB) and `RevjusStaging_15102025.sql` (3.6 MB),
hand-exported schema dumps standing in for migrations.

The negatives matter as much. A `.php` file with no phpinfo, and a phpinfo written into a
comment as a warning, are not findings; nor is a `CREATE TABLE` under `migrations/`, which is
the mechanism the rule steers towards, or a `.sql` of pure INSERTs, which is data rather than
a schema. A rule that flagged those would be noise on the shapes it most often meets.

Run: python test_standards_disclosure.py   (or pytest)
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import CheckConfig  # noqa: E402
from standards_disclosure import (  # noqa: E402
    check_committed_sql_dump,
    check_disclosure_file,
    check_sensitive_files,
)
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

EXEMPT = "kept as the ops team's on-box diagnostic, reachable only from the admin VLAN"
assert len(EXEMPT) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


# ---- disclosure-file ---------------------------------------------------------------------


def found(name: str, *lines: str) -> list[str]:
    return [v.message for v in check_disclosure_file(Path(name), list(lines))]


def test_the_classic_phpinfo_page_is_flagged() -> None:
    """Verbatim shape of the `phpinfo.php` the audit found."""
    assert found("phpinfo.php", "<?php phpinfo(); ?>")


def test_phpinfo_inside_a_larger_file_is_flagged() -> None:
    """The call need not be the whole file -- one buried in a diagnostic script is the
    same disclosure."""
    assert found(
        "diagnostics.php",
        "<?php",
        "$config = load_config();",
        "if ($debug) {",
        "    phpinfo();",
        "}",
    )


def test_the_message_names_the_call_and_the_fix() -> None:
    message = found("phpinfo.php", "<?php phpinfo(); ?>")[0]
    assert "phpinfo()" in message and "Delete it" in message


def test_a_php_file_with_no_phpinfo_is_clean() -> None:
    assert not found(
        "index.php",
        "<?php",
        "echo 'hello';",
        "$info = get_app_info();",
    )


def test_a_commented_out_phpinfo_is_clean() -> None:
    """A whole-line comment is skipped by iter_code_lines, so a warning ABOUT phpinfo is not
    itself a finding."""
    assert not found(
        "notes.php",
        "<?php",
        "// phpinfo();  -- never ship this",
        "echo 'ok';",
    )


def test_a_non_php_file_is_out_of_scope() -> None:
    """The rule is PHP-only; a phpinfo string elsewhere is not its concern."""
    assert not found("readme.md", "run phpinfo() to inspect the server")


def test_a_line_exemption_silences_it() -> None:
    assert not found(
        "phpinfo.php",
        f"<?php phpinfo(); ?>  // standards: disclosure-file exempt -- {EXEMPT}",
    )


# ---- committed-sql-dump ------------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_toplevel_schema_dump_fires_and_migrations_and_data_do_not() -> None:
    """The audit's shape in miniature: a checked-in dump beside a migrations directory and a
    pure-data file. Only the dump is a finding."""
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        _write(root / "dump.sql", "CREATE TABLE users (id INT, name VARCHAR(255));\n")
        _write(root / "migrations" / "001.sql", "CREATE TABLE widgets (id INT);\n")
        _write(root / "data.sql", "INSERT INTO users VALUES (1, 'Jack');\n")

        violations = list(check_committed_sql_dump(root, CheckConfig()))
        names = sorted(v.path.name for v in violations)
        assert names == ["dump.sql"], names


def _git_repo_with_dump(tree: str, *, gitignore: str) -> Path:
    """A real git repo holding `db/dump.sql`, differing only in whether git ignores it."""
    root = Path(tree)
    subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True, text=True, check=False)
    _write(root / ".gitignore", gitignore)
    _write(root / "db" / "dump.sql", "CREATE TABLE users (id INT, name VARCHAR(255));\n")
    return root


def test_a_gitignored_dump_is_not_a_finding() -> None:
    """The same rule its sibling `check_sensitive_files` applies. A local dump somebody pulled
    down to inspect is gitignored and present on disk; flagging it is a finding on a
    developer's machine that CI never reproduces and nobody can act on. The two rules in this
    module diverged on this until a review in the B3D pack noticed -- a module that answers
    the same question two ways has one of them wrong."""
    with tempfile.TemporaryDirectory() as tree:
        root = _git_repo_with_dump(tree, gitignore="db/dump.sql\n")
        assert [v.path.name for v in check_committed_sql_dump(root, CheckConfig())] == []


def test_a_tracked_dump_is_still_a_finding_in_a_git_repo() -> None:
    """The filter is `--others --ignored`, i.e. UNTRACKED and ignored. The rule keeps the
    case it exists for: a dump git would commit."""
    with tempfile.TemporaryDirectory() as tree:
        root = _git_repo_with_dump(tree, gitignore="\n")
        assert [v.path.name for v in check_committed_sql_dump(root, CheckConfig())] == ["dump.sql"]


def test_the_dump_is_reported_at_line_one() -> None:
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        _write(root / "schema.sql", "-- export\nCREATE TABLE t (id INT);\n")
        violations = list(check_committed_sql_dump(root, CheckConfig()))
        assert len(violations) == 1
        assert violations[0].line == 1
        assert violations[0].rule == "committed-sql-dump"


def test_the_message_names_the_dump_and_migrations() -> None:
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        _write(root / "revjus.sql", "CREATE TABLE audit (id INT);\n")
        message = list(check_committed_sql_dump(root, CheckConfig()))[0].message
        assert "revjus.sql" in message and "migrations" in message


def test_a_rails_db_migrate_dump_is_not_flagged() -> None:
    """`db/migrate/` is the Rails spelling of the mechanism, so a CREATE TABLE there is
    exactly what the rule wants, not a finding."""
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        _write(root / "db" / "migrate" / "20251015_create_users.sql", "CREATE TABLE u (id INT);\n")
        assert not list(check_committed_sql_dump(root, CheckConfig()))


def test_a_fixtures_dump_is_the_per_location_optout() -> None:
    """A `.sql` under fixtures/ is a deliberate, reviewed data file -- the opt-out this rule
    offers in place of a config flag."""
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        _write(root / "fixtures" / "seed_schema.sql", "CREATE TABLE demo (id INT);\n")
        assert not list(check_committed_sql_dump(root, CheckConfig()))


SOURCETEXT_REASON = (
    "not a dump: this is the SOURCE a compiler reads to create the corpus database from "
    "nothing on every build, so there is no snapshot to drift from and it holds no rows"
)


def test_a_written_marker_is_the_per_file_optout() -> None:
    """The third kind of `.sql`: a schema that is a build's INPUT, not an export's output.

    sourcetext.ai's `pipeline/schema.sql` is the case this was added for -- none of the
    three location opt-outs describes it, and before the marker existed the rule could not
    be answered at all.
    """
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        _write(
            root / "pipeline" / "schema.sql",
            "-- The corpus schema.\n--\n"
            f"-- standards: committed-sql-dump exempt -- {SOURCETEXT_REASON}\n"
            "CREATE TABLE verses (id INT);\n",
        )
        assert not list(check_committed_sql_dump(root, CheckConfig()))


def test_a_marker_whose_reason_is_too_short_does_not_exempt() -> None:
    """The reason is the gate, exactly as it is for file-length. "It is fine" is not one."""
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        _write(
            root / "schema.sql",
            "-- standards: committed-sql-dump exempt -- it is fine\nCREATE TABLE t (id INT);\n",
        )
        assert len(list(check_committed_sql_dump(root, CheckConfig()))) == 1


def test_a_marker_below_the_header_does_not_exempt() -> None:
    """Buried past the header it is invisible to anyone opening the file, which defeats the
    point of putting it in the file. Same rule as every other marker."""
    from standards_exemptions import (  # noqa: PLC0415  (local by design: imported after this test builds its tree)
        HEADER_SCAN_LINES,  # noqa: PLC0415  (local by design: imported after this test builds its tree)
    )

    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        _write(
            root / "schema.sql",
            "-- filler\n" * (HEADER_SCAN_LINES + 5)
            + f"-- standards: committed-sql-dump exempt -- {SOURCETEXT_REASON}\n"
            + "CREATE TABLE t (id INT);\n",
        )
        assert len(list(check_committed_sql_dump(root, CheckConfig()))) == 1


def test_a_marker_for_another_rule_does_not_exempt_this_one() -> None:
    """Markers are keyed by tag, so a file-length exemption says nothing about this rule."""
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        _write(
            root / "schema.sql",
            f"-- standards: file-length exempt -- {SOURCETEXT_REASON}\nCREATE TABLE t (id INT);\n",
        )
        assert len(list(check_committed_sql_dump(root, CheckConfig()))) == 1


def test_the_message_offers_the_marker() -> None:
    """A rule that cannot be answered gets worked around, so the finding has to say how."""
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        _write(root / "schema.sql", "CREATE TABLE t (id INT);\n")
        message = list(check_committed_sql_dump(root, CheckConfig()))[0].message
        assert "committed-sql-dump exempt" in message


def test_an_exempted_file_is_never_scanned_for_create_table() -> None:
    """The marker is checked before the expensive step, so a huge exempted schema costs a
    header read rather than a stream of the whole file."""
    from standards_disclosure import (  # noqa: PLC0415  (local by design: imported after this test builds its tree)
        SQL_READ_CHUNK,  # noqa: PLC0415  (local by design: imported after this test builds its tree)
    )

    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        padding = "-- filler comment line\n" * (SQL_READ_CHUNK // 20)
        _write(
            root / "schema.sql",
            f"-- standards: committed-sql-dump exempt -- {SOURCETEXT_REASON}\n"
            + padding
            + "CREATE TABLE late (id INT);\n",
        )
        assert not list(check_committed_sql_dump(root, CheckConfig()))


def test_create_table_across_a_chunk_boundary_is_still_seen() -> None:
    """The chunked reader carries an overlap so a statement straddling the boundary is not
    missed -- padded past one 64 KB chunk with the keyword landing on the seam."""
    from standards_disclosure import (  # noqa: PLC0415  (local by design: imported after this test builds its tree)
        SQL_READ_CHUNK,  # noqa: PLC0415  (local by design: imported after this test builds its tree)
    )

    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        padding = "-- filler comment line\n" * (SQL_READ_CHUNK // 20)
        _write(root / "big.sql", padding + "CREATE TABLE late (id INT);\n")
        assert list(check_committed_sql_dump(root, CheckConfig()))


# ---- disclosure-file: sensitive files by name --------------------------------------------


def sensitive_findings(files: dict[str, str]) -> list[str]:
    """Basenames flagged by check_sensitive_files over a temp tree of {relpath: body}."""
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        for relative, body in files.items():
            _write(root / relative, body)
        return sorted(v.path.name for v in check_sensitive_files(root, CheckConfig()))


def test_the_wordpress_config_and_installer_are_flagged() -> None:
    found = sensitive_findings(
        {
            "wp-config.php": "<?php define('DB_PASSWORD', 'hunter2');",
            "wp-admin/setup-config.php": "<?php // installer",
            "wp-admin/install.php": "<?php // installer",
        }
    )
    assert found == ["install.php", "setup-config.php", "wp-config.php"], found


def test_the_wordpress_sample_config_is_the_safe_template() -> None:
    """wp-config-sample.php is what WordPress ships and what the rule steers towards."""
    assert sensitive_findings({"wp-config-sample.php": "<?php define('DB_PASSWORD', '');"}) == []


def test_committed_sqlite_databases_are_flagged() -> None:
    found = sensitive_findings(
        {
            "db.sqlite3": "SQLite format 3",  # Django default
            "database/database.sqlite": "SQLite",  # Laravel default
            "data/app.sqlite": "SQLite",  # any SQLite
        }
    )
    assert found == ["app.sqlite", "database.sqlite", "db.sqlite3"], found


def test_a_sqlite_fixture_is_the_per_location_optout() -> None:
    assert sensitive_findings({"tests/fixtures/sample.sqlite": "SQLite"}) == []


def test_certificates_and_publish_profiles_are_flagged() -> None:
    found = sensitive_findings(
        {
            "certs/server.pfx": "binary",
            "signing.p12": "binary",
            "Properties/PublishProfiles/Prod.pubxml.user": "<Project><Password>x</Password></Project>",
        }
    )
    assert found == ["Prod.pubxml.user", "server.pfx", "signing.p12"], found


def test_credential_stores_and_adminer_are_flagged() -> None:
    found = sensitive_findings(
        {
            ".htpasswd": "admin:$apr1$xyz",
            ".pypirc": "[pypi]\npassword = secret",
            ".git-credentials": "https://user:token@github.com",
            "public/adminer.php": "<?php // Adminer",
            "public/adminer-4.8.1.php": "<?php // Adminer",
        }
    )
    assert found == [".git-credentials", ".htpasswd", ".pypirc", "adminer-4.8.1.php", "adminer.php"], found


def test_ordinary_files_are_clean() -> None:
    assert (
        sensitive_findings(
            {
                "index.php": "<?php echo 'hi';",
                "app/models.py": "class User: pass",
                "config.json": "{}",
                "database/migrations/001.php": "<?php // migration",
            }
        )
        == []
    )


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "disclosure + committed-sql-dump cases"))

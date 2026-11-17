"""Cases for the seeded-credential-config rule.

THE CASE THAT MOTIVATED IT is ellengaard-dk's restore (2026-10-12): `.env.example` documented a
local admin password key, compose passed it into the container, and the restore script created
an administrator from it -- the very shape secrets.md recommended until that day. Each of its
three appearances is pinned below, and the rule must reach all three through the real
dispatcher, because two of them live in files the dispatcher returns early for.

The negatives matter as much: a database root password, an SMTP password, an admin e-mail
address and a markdown page that NAMES the forbidden key must all stay quiet -- a rule that
fired on those would be firing on the right answers and the documentation of the wrong one.

THE KEYS ARE ASSEMBLED AT RUNTIME, so this file cannot match its own detector when the pack
lints itself: a literal environment read of a seeded key, written out here, would be a finding
in the pack's own gate.

Run: python test_standards_seed_credentials.py   (or pytest)
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_config import CheckConfig  # noqa: E402
from standards_dispatch import check_migration_file, check_source_file  # noqa: E402
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402
from standards_pack_identity import PACK_DIRECTORY  # noqa: E402
from standards_scope import should_check, should_check_migration  # noqa: E402
from standards_seed_credentials import SEEDED_RULE, check_seeded_credentials, is_seeded_key  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

PW = "PASS" + "WORD"
ADMIN = "LOCAL_ADMIN_" + PW
INVOTRACK = "INVOTRACK_DEV_ADMIN_" + PW
EXEMPT = "pgAdmin's own documented login for a local-only container, not an estate seeder"
assert len(EXEMPT) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


def found(name: str, *lines: str) -> list[int]:
    """1-based line numbers the rule reports for this file."""
    return [v.line for v in check_seeded_credentials(Path(name), list(lines))]


def dispatched(name: str, *lines: str) -> list[str]:
    """Rule ids the REAL entry point yields -- the half that proves the rule is reachable."""
    return [v.rule for v in check_source_file(Path(name), list(lines), CheckConfig(), {}, Path("."))]


# ---- the name test ---------------------------------------------------------------------------


def test_seeded_account_password_names_match() -> None:
    for key in (
        ADMIN,
        INVOTRACK,
        "Demo:Admin" + "Password",
        "SeedUser" + "Pwd",
        "SUPERUSER_" + "PASS",
        "DEFAULT_USER_" + PW,
        "Seed:" + "Password",
        "demo_" + PW.lower(),
    ):
        assert is_seeded_key(key), key


def test_other_passwords_and_admin_settings_do_not_match() -> None:
    for key in (
        "MYSQL_ROOT_" + PW,
        "WORDPRESS_DB_" + PW,
        "SMTP_" + PW,
        "ADMIN_EMAIL",
        "TURNSTILE_SECRET",
        PW + "_MIN_LENGTH",
        "Admin" + "PasswordPolicy",
        "RABBITMQ_DEFAULT_PASS" + "PHRASE_FILE",
    ):
        assert not is_seeded_key(key), key


# ---- declarations ----------------------------------------------------------------------------


def test_env_example_key_is_flagged_set_or_commented() -> None:
    assert found(".env.example", "COMPOSE_FILE=docker-compose.yml", f"{ADMIN}=") == [2]
    assert found(".env.example", f"# {ADMIN}=<the standard password>") == [1]


def test_env_example_database_password_is_clean() -> None:
    assert not found(".env.example", f"MYSQL_ROOT_{PW}=", f"WORDPRESS_DB_{PW}=")


def test_dotenv_itself_is_never_opened() -> None:
    """`.env` is decided by name elsewhere and never read -- not even by this rule."""
    assert not found(".env", f"{ADMIN}=anything")


def test_compose_declaration_and_interpolation_are_flagged_once_per_line() -> None:
    assert found("docker-compose.yml", "    environment:", f"      {ADMIN}: ${{{ADMIN}:-}}") == [2]
    assert found("docker-compose.yml", f"      - {INVOTRACK}=${{SEED:?x}}") == [1]


def test_compose_database_root_password_is_clean() -> None:
    assert not found("docker-compose.yml", f"      MYSQL_ROOT_{PW}: ${{MYSQL_ROOT_{PW}:?set it}}")


def test_appsettings_nested_path_is_flagged_on_its_line() -> None:
    lines = ["{", '  "Demo": {', '    "Email": "localadmin@allegroit.dk",', f'    "Admin{PW.title()}": ""', "  }", "}"]
    assert found("appsettings.Development.json", *lines) == [4]


def test_appsettings_section_named_seed_is_flagged() -> None:
    lines = ["{", '  "Seed": {', f'    "{PW.title()}": ""', "  }", "}"]
    assert found("appsettings.json", *lines) == [3]


def test_appsettings_with_comments_falls_back_to_line_keys() -> None:
    lines = ["{", "  // dev only", f'  "Admin{PW.title()}": ""', "}"]
    assert found("appsettings.Development.json", *lines) == [3]


def test_appsettings_connection_string_is_clean() -> None:
    assert not found("appsettings.json", "{", '  "ConnectionStrings": { "Default": "Server=db" }', "}")


# ---- reads in source -------------------------------------------------------------------------


def test_reads_are_flagged_in_every_language() -> None:
    reads = {
        "seed.php": "$pw = getenv('" + ADMIN + "');",
        "seed2.php": "$pw = $_ENV['" + ADMIN + "'];",
        "seed.py": "pw = os.environ['" + ADMIN + "']",
        "seed2.py": "pw = os.getenv('" + ADMIN + "', '')",
        "Seeder.cs": 'var pw = configuration["Demo:Admin' + PW.title() + '"];',
        "Seeder2.cs": 'var pw = Environment.GetEnvironmentVariable("' + INVOTRACK + '");',
        "seed.ts": "const pw = process.env." + ADMIN + ";",
        "restore.sh": 'wp user update localadmin --user_pass="${' + ADMIN + ':-}"',
        "seed.ps1": "$pw = $env:" + ADMIN,
    }
    for name, line in reads.items():
        assert found(name, line) == [1], name


def test_reads_of_other_passwords_are_clean() -> None:
    assert not found("restore.sh", 'export MYSQL_PWD="$WORDPRESS_DB_' + PW + '"')
    assert not found("seed.py", "pw = os.environ['SMTP_" + PW + "']")


def test_markdown_naming_the_key_is_clean() -> None:
    """Documentation of the forbidden shape is not the shape."""
    assert not found("README.md", f"Never add `{ADMIN}` to .env.example.")


# ---- exemption -------------------------------------------------------------------------------


def test_a_line_exemption_silences_it() -> None:
    assert not found(
        "docker-compose.yml",
        f"      # standards: {SEEDED_RULE} exempt -- {EXEMPT}",
        "      PGADMIN_DEFAULT_" + PW + ": ${PGADMIN_DEFAULT_" + PW + ":-}",
    )


# ---- the dispatcher: the rule must survive every early return --------------------------------


def test_dispatcher_reaches_env_example_compose_and_appsettings() -> None:
    assert SEEDED_RULE in dispatched(".env.example", f"{ADMIN}=")
    assert SEEDED_RULE in dispatched("docker-compose.yml", f"      {ADMIN}: ${{{ADMIN}:-}}")
    assert SEEDED_RULE in dispatched("appsettings.json", "{", f'  "Admin{PW.title()}": ""', "}")
    assert SEEDED_RULE in dispatched("restore.sh", 'echo "${' + ADMIN + '}"')


# ---- migrations: the one excluded directory this rule reads ----------------------------------


DJANGO_SEED = (
    "def create_admin(apps, schema_editor):",
    "    User = apps.get_model('users', 'User')",
    "    User.objects.create_superuser('admin', password=os.environ['DJANGO_SUPER" + "USER_" + PW + "'])",
)


def test_a_migration_reaches_the_migration_route_and_nothing_else_does() -> None:
    root, config = Path("/repo"), CheckConfig()
    for relative in ("app/users/migrations/0001_initial.py", "src/Data/Migrations/20260101_Init.cs"):
        assert should_check_migration(root / relative, root, config), relative
        assert not should_check(root / relative, root, config), f"{relative} must stay out of every other rule"
    for relative in (
        "app/users/models.py",  # not a migration: the ordinary route has it
        "src/Data/Migrations/20260101_Init.Designer.cs",  # a generated name stays skipped
        "node_modules/pkg/migrations/0001.js",  # somebody else's migration
        "app/users/migrations/README.md",  # not source
    ):
        assert not should_check_migration(root / relative, root, config), relative


def test_a_repo_exclusion_still_wins_over_the_migration_route() -> None:
    root = Path("/repo")
    config = CheckConfig(extra_excluded_fragments=("/legacy/",))
    assert not should_check_migration(root / "legacy/app/migrations/0001_initial.py", root, config)


def test_the_migration_route_runs_this_rule_and_only_this_rule() -> None:
    """A 900-line generated migration would fail file-length on the ordinary route."""
    lines = [*DJANGO_SEED, *(["    pass"] * 900)]
    rules = {v.rule for v in check_migration_file(Path("app/users/migrations/0001_initial.py"), lines)}
    assert rules == {SEEDED_RULE}, rules


def test_a_generated_migration_is_quiet() -> None:
    lines = ["class Migration(migrations.Migration):", "    dependencies = [('users', '0001_initial')]"]
    assert not list(check_migration_file(Path("app/users/migrations/0002_auto.py"), lines))


def test_the_whole_tree_scan_and_the_rule_agree_on_a_migration() -> None:
    """THE INCONSISTENCY THIS PINS (2026-10-14): `check-source-limits.py --root .` called
    allegro-it-services clean while the rule, walking the same tree, found this exact shape."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        migration = root / "app" / "users" / "migrations" / "0001_initial.py"
        migration.parent.mkdir(parents=True)
        migration.write_text("\n".join(DJANGO_SEED) + "\n", encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = _driver().main(["--root", str(root), "--no-baseline"])
    assert code == 1, output.getvalue()
    assert f"app/users/migrations/0001_initial.py:3: [{SEEDED_RULE}]" in output.getvalue()


def _driver():
    spec = importlib.util.spec_from_file_location("driver", PACKAGE_DIRECTORY / "check-source-limits.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- quiet on real source --------------------------------------------------------------------
#
# THE PACKAGE DIRECTORY, NEVER `parents[1]`. This file ships to every consumer and runs there
# under `pytest engineering_standards/`. `parents[1]` is the pack's root here and the CONSUMER's
# root there: on 2026-10-14 this case walked allegro-it-services for 35 s, node_modules
# included, and failed its deploy on a migration of that repo's own -- a real finding, reported
# from the wrong place, in a repo whose own scan had not been asked. The directory beside this
# file holds the same files in every repo it lands in, so "the pack is clean" means one thing
# everywhere. The rest of the pack's tree is read by dev/test_pack_self.py, which never ships.


def scan_root_for(test_file: Path) -> Path:
    """What the self-test reads, given where this test file sits."""
    return test_file.resolve().parent


PACKAGE_DIRECTORY = scan_root_for(Path(__file__))


def findings_under(directory: Path) -> list[str]:
    """`path:line` for every finding the rule makes under `directory`."""
    noisy = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError, OSError:
            continue
        noisy += [f"{path.relative_to(directory).as_posix()}:{v.line}" for v in check_seeded_credentials(path, lines)]
    return noisy


def test_the_self_test_reads_the_package_directory() -> None:
    assert PACKAGE_DIRECTORY.name == PACK_DIRECTORY, PACKAGE_DIRECTORY


def test_a_consumer_tree_does_not_leak_into_the_self_test() -> None:
    """The allegro-it-services shape: the pack beside a repo whose own migration seeds an admin."""
    with tempfile.TemporaryDirectory() as directory:
        consumer = Path(directory)
        package = consumer / PACK_DIRECTORY
        package.mkdir()
        shipped_test = package / Path(__file__).name
        shipped_test.write_text("x = 1\n", encoding="utf-8")
        migration = consumer / "packages" / "backend" / "apps" / "users" / "migrations" / "0001_initial.py"
        migration.parent.mkdir(parents=True)
        migration.write_text("\n".join(DJANGO_SEED) + "\n", encoding="utf-8")

        assert findings_under(consumer), "fixture has no teeth: the consumer's migration must be a finding"
        assert scan_root_for(shipped_test) == package.resolve()
        assert findings_under(scan_root_for(shipped_test)) == []


def test_the_shipped_package_is_clean() -> None:
    """Every file the pack ships into `engineering_standards/`, read by the rule: zero findings."""
    noisy = findings_under(PACKAGE_DIRECTORY)
    assert not noisy, noisy


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "seeded-credential-config cases"))

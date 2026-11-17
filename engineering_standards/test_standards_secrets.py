"""Cases for the committed-credential rule.

EVERY CREDENTIAL FIXTURE IS ASSEMBLED AT RUNTIME, never written as a literal. A detector whose
own test suite trips it is one that gets exempted on sight -- and the pack has already been
bitten once by exactly this, when a `.py` file holding `node-version: '22'` as a fixture was
read as a real declaration and failed the pack's own gate. Concatenation keeps the literal out
of the source text while the assembled string is byte-identical at the moment it is judged,
which is the only moment that matters.

The negatives carry unusual weight here. `.env.example` full of key names, a fixture password,
an interpolation like `${DB_PASSWORD}` and an empty value are all things secrets.md explicitly
ALLOWS to be committed, and a rule that flags them makes the file it enforces unreadable.

Run: python test_standards_secrets.py   (or pytest)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import CheckConfig  # noqa: E402
from standards_dispatch import check_source_file  # noqa: E402
from standards_exemption_report import read_source_lines  # noqa: E402
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402
from standards_scope import should_check  # noqa: E402
from standards_secrets import (  # noqa: E402
    SECRET_RULE,
    check_committed_credentials,
    is_placeholder,
    known_format,
)
from standards_selftest import run_module_tests  # noqa: E402

# Assembled, never written. See the module docstring.
PAYLOAD = "A" * 24
STRIPE = "sk_" + "live_" + PAYLOAD
STRIPE_TEST = "sk_" + "test_" + PAYLOAD
BREVO = "xkeysib" + "-" + PAYLOAD
AWS = "AKIA" + "J" * 16
GITHUB = "ghp_" + "b" * 36
GITHUB_APP_CLASSIC = "ghs_" + "c" * 36
GITHUB_REFRESH = "ghr_" + "r" * 76
# The stateless installation-token format GitHub began rolling out in 2026: a ~520-character JWT
# behind the same `ghs_` prefix, so dots and base64url's `-` and `_` sit inside the payload. The
# `-` and `_` are placed early on purpose -- the old letters-and-digits-only pattern stopped at
# the first of them and let this token through.
GITHUB_APP_STATELESS = "ghs_" + "eyJ-hb_Gc" + "i" * 60 + "." + "d" * 380 + "." + "s" * 66
SLACK = "xoxb" + "-" + "1234567890" + "-" + PAYLOAD
GOOGLE = "AIza" + "C" * 35
PEM = "-----BEGIN RSA " + "PRIVATE KEY" + "-----"

EXEMPT = "the demo cast in the docs uses this string and it works nowhere; see seed notes"
assert len(EXEMPT) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


def found(name: str, *lines: str) -> list[str]:
    return [v.message for v in check_committed_credentials(Path(name), list(lines))]


# ---- tier 1: known formats -------------------------------------------------------------------


def test_every_known_format_is_caught() -> None:
    for label, value in (
        ("stripe", STRIPE),
        ("stripe test", STRIPE_TEST),
        ("brevo", BREVO),
        ("aws", AWS),
        ("github", GITHUB),
        ("github app classic", GITHUB_APP_CLASSIC),
        ("github app stateless", GITHUB_APP_STATELESS),
        ("github refresh", GITHUB_REFRESH),
        ("slack", SLACK),
        ("google", GOOGLE),
        ("pem", PEM),
    ):
        assert found("appsettings.json", f'  "Key": "{value}"'), f"{label} was not caught"


def test_a_known_format_is_caught_even_under_an_innocent_key() -> None:
    """`"note": "sk_live_…"` is still a live key. The key name is a hint, not the evidence."""
    assert found("appsettings.json", f'  "note": "{STRIPE}"')


def test_a_test_mode_key_is_still_a_credential() -> None:
    """A Stripe test key is a working credential for the test account -- it can read and write
    real objects there, and it is one dashboard toggle away from embarrassment."""
    assert found("appsettings.json", f'  "Stripe:SecretKey": "{STRIPE_TEST}"')


def test_the_documented_prefix_alone_is_not_a_finding() -> None:
    """Every format requires a PAYLOAD. This is what keeps secrets.md, this file and the
    detector itself clean -- a rule that fires on its own documentation gets exempted on sight."""
    assert known_format("use sk_live_ keys in production") is None
    assert known_format("the AKIA prefix identifies an AWS key") is None
    assert known_format("installation tokens start with ghs_ and may contain dots") is None


# ---- tier 2: shape ---------------------------------------------------------------------------


def test_the_real_estate_case_is_caught() -> None:
    """ligelon-compliance committed a demo password to appsettings.Development.json, which was
    in scope for NOTHING before this rule -- .json is not a source suffix and no filename
    predicate named it."""
    assert found("appsettings.Development.json", '    "Password": "Sommer2026!"')


def test_a_password_inside_a_connection_string_is_caught() -> None:
    assert found(
        "appsettings.json",
        # standards: committed-credential exempt -- the fixture the detector is supposed to
        # catch, not a credential this repo uses; the value works nowhere and never has.
        '  "Default": "Server=db;Database=app;User=root;Password=Sommer2026!;"',
    )


def test_the_env_shaped_form_is_caught() -> None:
    assert found(".env.example", "SMTP_PASSWORD=Sommer2026!")


def test_a_yaml_form_is_caught() -> None:
    assert found("settings.ini", "api_key: 9f2a7c41bd8e33a0")


# ---- what secrets.md explicitly ALLOWS to be committed ---------------------------------------


def test_key_names_with_empty_values_are_clean() -> None:
    """This is what `.env.example` is FOR. Flagging it would make the rule unusable."""
    assert not found(".env.example", "SMTP_PASSWORD=", "STRIPE_SECRET_KEY=", "API_KEY=")


def test_an_empty_json_value_is_clean() -> None:
    assert not found("appsettings.json", '  "Password": "",', '  "ApiKey": ""')


def test_an_interpolation_is_a_reference_not_a_credential() -> None:
    for reference in ("${DB_PASSWORD}", "$DB_PASSWORD", "%DB_PASSWORD%", "{{ db_password }}"):
        assert not found(".env.example", f"DB_PASSWORD={reference}"), reference


def test_an_interpolation_carrying_a_message_is_still_a_reference() -> None:
    """Compose's required (`:?`) and default (`:-`) forms put a human message INSIDE the braces,
    and that message is several words long. There is still no value on the line -- the whole
    point of `:?` is that the value lives elsewhere and the stack refuses to start without it.

    Caught in InvoTrack on 2026-08-27, where four correct lines each had to take an exemption:
    KEY_VALUE's value group stopped at the first space, so `${VAR:?set VAR in .env}` truncated
    to `${VAR:?set` and then failed the interpolation test for the missing brace. The same class
    of bug was fixed once before, when excluding `}` truncated `${DB_PASSWORD}` -- so the span
    has to be matched as a whole rather than by excluding terminators one at a time."""
    for reference in (
        "${DB_PASSWORD:?set DB_PASSWORD in .env}",
        "${DB_PASSWORD:-a default that nobody should ever use}",
        "${DB_PASSWORD:?one two three four five six seven eight}",
    ):
        assert not found("docker-compose.yml", f"      DB_PASSWORD: {reference}"), reference


def test_an_interpolation_with_a_message_inside_a_connection_string_is_a_reference() -> None:
    """The same line shape the shape tier reads as `Password=<value>`. Both tiers have to agree,
    or the connection-string check passes and the shape check flags the identical text."""
    assert not found(
        "docker-compose.yml",
        "      ConnectionStrings__Default: "
        '"Server=mysql;Database=app;User=app;Password=${APP_PASSWORD:?set APP_PASSWORD in .env};"',
    )


def test_obvious_placeholders_are_clean() -> None:
    for value in (
        "changeme",
        "CHANGE_ME",
        "your-api-key-here",
        "placeholder",
        "TODO",
        "<set in the secret store>",
        "xxxxxxxxxx",
        "***",
        "dummy",
        "not-set",
    ):
        assert not found("appsettings.json", f'  "Password": "{value}"'), value


def test_a_repeated_character_value_is_a_placeholder() -> None:
    assert not found("appsettings.json", '  "Password": "aaaaaaaaaa"')


def test_a_value_too_short_to_be_a_credential_is_clean() -> None:
    """`pwd=1` in a fixture is noise, not a leak."""
    assert not found("appsettings.json", '  "Password": "abc"')


def test_a_key_name_that_merely_contains_a_word_is_not_a_secret_key() -> None:
    """`passwordless`, `secretary`, `tokenizer` -- the boundary is what stops this rule from
    reading half the codebase as credentials."""
    assert not found("appsettings.json", '  "PasswordlessLogin": "enabled-for-everyone"')
    assert not found("appsettings.json", '  "TokenizerModel": "gpt-4-turbo-preview"')


def test_a_line_exemption_silences_it() -> None:
    assert not found(
        "appsettings.json",
        f"  // standards: {SECRET_RULE} exempt -- {EXEMPT}",
        '  "Password": "Sommer2026!"',
    )


def test_is_placeholder_agrees_with_the_documented_allowances() -> None:
    assert is_placeholder("")
    assert is_placeholder("   ")
    assert is_placeholder("${VAR}")
    assert not is_placeholder("Sommer2026!")


# ---- the value is never printed --------------------------------------------------------------


def test_the_finding_never_quotes_the_credential() -> None:
    """The standing rule is that a leaked value is never echoed anywhere. A scanner that prints
    the match relocates the leak into the terminal and the CI log rather than closing it."""
    for line, secret in (
        (f'  "Stripe:SecretKey": "{STRIPE}"', STRIPE),
        (f'  "GitHub:Token": "{GITHUB_APP_STATELESS}"', GITHUB_APP_STATELESS),
        ('  "Password": "Sommer2026!"', "Sommer2026!"),
    ):
        message = found("appsettings.json", line)[0]
        assert secret not in message, "the finding must not contain the credential"
        assert "secrets.md" in message, "and must point at the rule that explains the fix"


def test_the_finding_names_the_key_so_it_can_be_found() -> None:
    message = found("appsettings.json", f'  "Stripe:SecretKey": "{STRIPE}"')[0]
    assert "Stripe:SecretKey" in message


# ---- .env is judged by name and never opened -------------------------------------------------


def test_a_staged_dotenv_is_a_finding_without_being_read() -> None:
    """Its name is already enough. Reading it would break the same rule it enforces, so the
    check is given an EMPTY line list here -- and must still report."""
    assert found(".env")
    assert found(".env.production")


def test_the_dotenv_finding_says_it_was_not_read() -> None:
    message = found(".env")[0]
    assert "NOT been read" in message


def test_dotenv_example_is_read_normally_rather_than_refused() -> None:
    """`.env.example` is SUPPOSED to be committed, so it is read like any config file -- which
    is exactly why a real value hiding in it is worth catching."""
    assert not found(".env.example", "SMTP_PASSWORD=")
    assert found(".env.example", "SMTP_PASSWORD=Sommer2026!")


# ---- scope and dispatch ----------------------------------------------------------------------


def test_config_files_are_in_scope_at_all() -> None:
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        for name in ("appsettings.Development.json", ".env", "web.config"):
            assert should_check(root / name, root, CheckConfig()), f"{name} must be in scope"


def test_the_dispatcher_reaches_the_rule_on_every_kind_of_file() -> None:
    """It sits ABOVE every early return, beside mojibake, because a credential does not care
    what kind of file it is in. Each path below hits a different early return."""
    cases = {
        "appsettings.Development.json": '  "Password": "Sommer2026!"',
        "docker-compose.yml": "      MYSQL_ROOT_PASSWORD: Sommer2026!",
        "Program.cs": f'    var key = "{STRIPE}";',
        "settings.py": f'STRIPE_SECRET_KEY = "{STRIPE}"',
        "Dockerfile": f"ENV STRIPE_KEY={STRIPE}",
        ".github/workflows/deploy.yml": f"          token: {GITHUB}",
    }
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        for relative, line in cases.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            rules = [v.rule for v in check_source_file(path, [line], CheckConfig(), {}, root)]
            assert SECRET_RULE in rules, f"the rule never reached {relative}"


def test_source_and_config_get_different_advice() -> None:
    """A literal in source cannot be moved by a tool -- reading it from config is a code change.
    The message has to say which situation the reader is in."""
    config = found("appsettings.json", '  "Password": "Sommer2026!"')[0]
    source = found("Program.cs", f'    var key = "{STRIPE}";')[0]
    assert "gitignored .env" in config
    assert "code change" in source


# ---- a .env git IGNORES is not a .env git is committing ---------------------------------------
#
# End-to-end rather than unit, deliberately: the rule above is right and stayed right the whole
# time this was broken. What was wrong was WHICH FILES REACHED IT -- the whole-tree walk read the
# filesystem, so a developer's ignored `.env` was judged as "being committed". Only a real repo
# with a real .gitignore can tell the two apart, so a unit test over `check_committed_credentials`
# cannot express this and would have passed against the broken code.
#
# It failed on every developer machine and never in CI, whose fresh checkout has no `.env` --
# which is exactly why it lasted. Found in ligelon-compliance, 2026-08-27.


# These repos must answer to their OWN .gitignore and nothing else. A developer's global
# ignore file outranks nothing here but still applies -- and git reads `~/.config/git/ignore`
# even with `core.excludesFile` unset, so a global `.env` line made `git add .env` refuse, and
# the "tracked" fixture silently held an untracked file: red on Jack's machine, green in CI,
# found 2026-10-02. Config passed through GIT_CONFIG_COUNT has command-line precedence, so it
# beats that implicit default without touching HOME. The SCANNER gets the same environment,
# because it asks git what is tracked: isolating only the setup would build one repo and scan
# another.
def _isolated_git_env(tree: Path) -> dict[str, str]:
    no_excludes = tree / "no-excludes"
    no_excludes.write_text("", encoding="utf-8")
    return {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(no_excludes),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.excludesFile",
        "GIT_CONFIG_VALUE_0": str(no_excludes),
    }


def _git(repo: Path, *arguments: str) -> None:
    # check=True: a fixture step that fails must fail the test HERE. Swallowing it is what let
    # a refused `git add` surface as an unrelated assertion about the scanner's output.
    subprocess.run(
        ["git", *arguments],
        cwd=repo,
        env=_isolated_git_env(repo.parent),
        capture_output=True,
        text=True,
        check=True,
    )


def _scan(repo: Path) -> str:
    limits = Path(__file__).resolve().parent / "check-source-limits.py"
    done = subprocess.run(
        [sys.executable, str(limits), "--root", str(repo)],
        env=_isolated_git_env(repo.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout + done.stderr


def _repo_with_dotenv(tree: str, *, ignored: bool) -> Path:
    """A repo holding a `.env`, differing ONLY in whether git is told to ignore it."""
    repo = Path(tree) / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text(".env\n" if ignored else "", encoding="utf-8")
    (repo / ".env").write_text("MYSQL_" + "PASSWORD=" + PAYLOAD + "\n", encoding="utf-8")
    if not ignored:
        _git(repo, "add", ".env")
    return repo


def test_an_ignored_dotenv_is_not_reported_as_committed() -> None:
    with tempfile.TemporaryDirectory() as tree:
        output = _scan(_repo_with_dotenv(tree, ignored=True))
        assert ".env:1" not in output, output


def test_a_tracked_dotenv_is_still_reported() -> None:
    """The half that must not regress. Losing this would trade a false positive for a silent
    miss, on the one rule where a miss is a published credential."""
    with tempfile.TemporaryDirectory() as tree:
        output = _scan(_repo_with_dotenv(tree, ignored=False))
        assert SECRET_RULE in output, output


# ---- the invariant, driven through the READER and the SCANNER rather than the rule ----------
#
# Every case above hands `check_committed_credentials` a line list somebody else produced, so
# all of them passed while the reader was opening `.env` before any rule was dispatched. The
# rule returning early cannot demonstrate the file was never read; only the reader can, and
# only the whole pipeline can show that its contents never surface.

# Two things planted in the .env that only a READ could surface: a credential whose payload is
# unique enough to grep for, and a byte sequence the mojibake rule quotes back verbatim when it
# fires. Built by concatenation for the same reason as every fixture above. The mojibake is
# SPELLED IN ESCAPES, NEVER AS GLYPHS: the three Danish letters, UTF-8-encoded and then decoded
# as latin-1 -- written as literal characters the fixture would trip the mojibake rule on this
# module, and a detector that fires on its own test suite is one people exempt on sight.
SENTINEL_SECRET = "sk_" + "live_" + ("Z" * 24)
SENTINEL_MOJIBAKE = "".join(chr(code) for code in (0xC3, 0xA6, 0xC3, 0xB8, 0xC3, 0xA5))
SENTINEL_DOTENV = f"STRIPE_SECRET_KEY={SENTINEL_SECRET}\n# {SENTINEL_MOJIBAKE}\n"


def test_the_reader_hands_a_dotenv_to_the_rules_unopened() -> None:
    """THE INVARIANT AT ITS ENFORCEMENT POINT. `read_source_lines` is the one reader the
    driver and the exemption walk share, so this is the single place a `.env` could be opened
    -- and the single test that turns red if it is."""
    with tempfile.TemporaryDirectory() as tree:
        dotenv = Path(tree) / ".env"
        dotenv.write_text(SENTINEL_DOTENV, encoding="utf-8")
        assert read_source_lines(dotenv) == [], "the reader opened a .env; the rule's early return never protected it"


def _repo_with_a_readable_dotenv(tree: str) -> Path:
    repo = Path(tree) / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text("", encoding="utf-8")
    (repo / ".env").write_text(SENTINEL_DOTENV, encoding="utf-8")
    _git(repo, "add", ".env")
    return repo


def test_a_dotenv_is_reported_without_a_byte_of_it_reaching_the_output() -> None:
    """The standing rule is that a secret-bearing file is never read and its values are never
    echoed anywhere. A scanner that prints one is not reporting the leak, it is relocating it
    into the terminal and the CI log."""
    with tempfile.TemporaryDirectory() as tree:
        output = _scan(_repo_with_a_readable_dotenv(tree))
        assert SECRET_RULE in output, ".env must still be reported, by name"
        assert SENTINEL_SECRET not in output, "the scanner echoed a value out of a .env"


def test_no_content_rule_can_fire_on_a_dotenv() -> None:
    """The invariant stated as something observable end to end. The planted mojibake is a real
    finding for any file the scanner reads, and the mojibake rule quotes its match -- so a
    `[mojibake]` line here would mean the .env had been opened after all, whatever this
    module's header claims."""
    with tempfile.TemporaryDirectory() as tree:
        output = _scan(_repo_with_a_readable_dotenv(tree))
        assert "[mojibake]" not in output, "a content rule fired on .env, so it was read"
        assert SENTINEL_MOJIBAKE not in output


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "committed-credential cases"))

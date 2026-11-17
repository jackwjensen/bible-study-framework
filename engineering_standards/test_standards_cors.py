"""Cases for the cors-wildcard rule.

standards: cors-wildcard exempt -- this suite's fixtures are literal wildcard CORS policies,
quoted so the detector can be tested against them, never applied as configuration; the marker
keeps the scanner from reporting the Python fixtures below on this file itself.

THE CASE THAT MOTIVATED IT is `test_the_php_audit_shape_is_flagged`: an authenticated PHP API
that answered every origin with `*`, which is a security defect, not a style nit -- so it is
not config-gated, and a repo cannot tune it off. The negatives carry equal weight: a NAMED
allow-list is the exact code this rule steers toward, and reporting `WithOrigins("https://x")`
or `allow_origins=["https://x"]` would be flagging the fix as if it were the defect.

Run: python test_standards_cors.py   (or pytest)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_cors import CORS_RULE, check_cors_wildcard  # noqa: E402
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

EXEMPT = "public unauthenticated read-only status feed; every origin is intended, see SECURITY.md"
assert len(EXEMPT) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


def found(name: str, *lines: str) -> list[str]:
    return [v.message for v in check_cors_wildcard(Path(name), list(lines))]


# ---- PHP --------------------------------------------------------------------------------------


def test_the_php_audit_shape_is_flagged() -> None:
    """The shape of the real audit: an authenticated surface answering every origin with `*`."""
    assert found("cors.php", "header('Access-Control-Allow-Origin: *');")


def test_php_double_quotes_are_flagged_too() -> None:
    assert found("cors.php", 'header("Access-Control-Allow-Origin: *");')


def test_a_named_php_origin_is_clean() -> None:
    assert not found("cors.php", "header('Access-Control-Allow-Origin: https://app.example.com');")


# ---- C# ---------------------------------------------------------------------------------------


def test_a_bare_allow_any_origin_is_flagged() -> None:
    assert found("Program.cs", "    policy.AllowAnyOrigin();")


def test_allow_any_origin_with_credentials_names_the_forbidden_combination() -> None:
    """The spec rejects `*` together with credentials, so the pairing gets its own message."""
    reported = found(
        "Program.cs",
        'options.AddPolicy("api", policy =>',
        "    policy.AllowAnyOrigin()",
        "          .AllowCredentials());",
    )
    assert len(reported) == 1, reported
    assert "AllowCredentials" in reported[0]


def test_with_origins_wildcard_is_flagged() -> None:
    assert found("Program.cs", '    policy.WithOrigins("*");')


def test_a_named_with_origins_is_clean() -> None:
    assert not found("Program.cs", '    policy.WithOrigins("https://app.example.com");')


def test_a_wildcard_subdomain_origin_is_not_the_star_shape() -> None:
    """`"https://*.example.com"` is a different (narrower) thing than any-origin, and its `*`
    is not adjacent to a quote, so the WithOrigins pattern does not match it."""
    assert not found("Program.cs", '    policy.WithOrigins("https://*.example.com");')


# ---- JS / TS ----------------------------------------------------------------------------------


def test_the_raw_header_is_flagged() -> None:
    for setter in ("res.header", "res.setHeader", "res.set"):
        assert found("app.js", f"{setter}('Access-Control-Allow-Origin', '*');"), setter


def test_a_cors_origin_wildcard_is_flagged() -> None:
    assert found(
        "server.ts",
        "import cors from 'cors';",
        "app.use(cors({ origin: '*' }));",
    )


def test_a_cors_origin_true_is_flagged() -> None:
    """`origin: true` reflects the caller's Origin -- a dynamic `*` that also passes the
    credentials check the static `*` fails, so it is if anything worse."""
    assert found(
        "server.ts",
        "import cors from 'cors';",
        "app.use(cors({",
        "  origin: true,",
        "  credentials: true,",
        "}));",
    )


def test_a_named_cors_allow_list_is_clean() -> None:
    assert not found(
        "server.ts",
        "import cors from 'cors';",
        "app.use(cors({ origin: ['https://app.example.com'] }));",
    )


def test_origin_true_outside_a_cors_file_is_not_flagged() -> None:
    """The bare `origin:` key is common in unrelated objects; the config form fires only when
    the file actually references the cors middleware."""
    assert not found(
        "reducer.ts",
        "const initial = { origin: true, destination: false };",
    )


# ---- Python -----------------------------------------------------------------------------------


def test_django_allow_all_is_flagged() -> None:
    assert found("settings.py", "CORS_ALLOW_ALL_ORIGINS = True")


def test_django_legacy_allow_all_is_flagged() -> None:
    assert found("settings.py", "CORS_ORIGIN_ALLOW_ALL = True")


def test_fastapi_allow_origins_wildcard_is_flagged() -> None:
    assert found("main.py", '    allow_origins=["*"],')


def test_a_named_fastapi_allow_list_is_clean() -> None:
    assert not found("main.py", '    allow_origins=["https://app.example.com"],')


def test_the_setting_set_to_false_is_clean() -> None:
    assert not found("settings.py", "CORS_ALLOW_ALL_ORIGINS = False")


# ---- shared ----------------------------------------------------------------------------------


def test_the_message_steers_toward_naming_origins() -> None:
    message = found("settings.py", "CORS_ALLOW_ALL_ORIGINS = True")[0]
    assert "origins you actually serve" in message


def test_a_line_exemption_silences_it() -> None:
    assert not found(
        "settings.py",
        f"# standards: {CORS_RULE} exempt -- {EXEMPT}",
        "CORS_ALLOW_ALL_ORIGINS = True",
    )


def test_a_trailing_line_exemption_silences_it_too() -> None:
    assert not found(
        "Program.cs",
        f"    policy.AllowAnyOrigin();  // standards: {CORS_RULE} exempt -- {EXEMPT}",
    )


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "cors-wildcard cases"))

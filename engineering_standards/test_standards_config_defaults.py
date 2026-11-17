#!/usr/bin/env python3
"""Cases for `config-default-in-code`: a configuration read whose fallback is a literal.

Plain asserts, no pytest, so it runs anywhere Python does:

    python engineering_standards/test_standards_config_defaults.py

Split out of test_standards_constants.py with the rule itself (2026-10-22). As there, the
quiet cases matter most: a `??` is everywhere in correct code, so every negative case below is
a real false positive kept so it cannot come back -- this pack's own fixtures read as
configuration, and PHP's request fields in `$_SERVER` read as settings.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_config_defaults import check_config_default  # noqa: E402


def defaults(source: str, name: str = "Startup.cs") -> list[str]:
    return [v.rule for v in check_config_default(Path(name), source.splitlines())]


def test_a_csharp_configuration_read_with_a_literal_fallback_fires():
    assert defaults('var host = Configuration["Mail:Host"] ?? "localhost";') == ["config-default-in-code"]


def test_the_python_php_and_javascript_spellings_fire_too():
    """One rule, four languages -- the shape is the same and only the syntax differs."""
    assert defaults('host = os.getenv("MAIL_HOST", "localhost")', "settings.py")
    assert defaults("$host = getenv('MAIL_HOST') ?: 'localhost';", "config.php")
    assert defaults('const host = process.env.MAIL_HOST ?? "localhost";', "env.ts")


def test_throwing_instead_of_defaulting_is_the_correct_answer_and_stays_quiet():
    """`secrets.md` MANDATES this shape for a seeder. Firing on it would be firing on the fix."""
    assert defaults('var key = Configuration["Api:Key"] ?? throw new InvalidOperationException();') == []


def test_falling_back_to_null_is_not_a_default():
    assert defaults('var host = Configuration["Mail:Host"] ?? null;') == []


def test_an_empty_default_is_absence_not_an_invented_value():
    assert defaults('host = os.getenv("MAIL_HOST", "")', "settings.py") == []


def test_falling_back_to_another_configured_value_is_quiet():
    assert defaults('var x = Configuration["A"] ?? Configuration["B"];') == []


def test_a_literal_fallback_with_no_configuration_read_is_not_this_rule():
    assert defaults('var name = candidate ?? "unknown";') == []


def test_php_request_fields_in_server_are_not_configuration():
    """$_SERVER also carries the request. A guard on a missing request field is not a setting
    -- measured on jacks_corner's front controller, which this rule flagged four times."""
    assert defaults("$uri = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);", "index.php") == []
    assert defaults("$host = $_SERVER['HTTP_HOST'] ?? 'example.dk';", "seo.php") == []
    assert defaults('$proto = $_SERVER["HTTP_X_FORWARDED_PROTO"] ?? "http";', "seo.php") == []
    assert defaults("$on = $_SERVER['HTTPS'] ?? 'off';", "seo.php") == []
    assert defaults("$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';", "index.php") == []
    assert defaults("$query = $_SERVER['QUERY_STRING'] ?? 'a=1';", "index.php") == []


def test_php_environment_values_in_server_still_fire():
    """The other half of the array is the process environment, which IS configuration."""
    assert defaults("$host = $_SERVER['MAIL_HOST'] ?? 'localhost';", "config.php")
    # A name that only STARTS like a request field is still an environment variable.
    assert defaults("$proxy = $_SERVER['HTTPS_PROXY'] ?? 'http://proxy:3128';", "config.php")
    # A dynamic key could be either half; nothing here can tell which.
    assert defaults("$value = $_SERVER[$name] ?? 'fallback';", "config.php")


def test_a_test_files_fixture_source_is_not_a_configuration_default():
    """This pack's own tests carry example source as literals; the rule read them back."""
    assert defaults("source = \"DEBUG = os.environ.get('DEBUG') == '1'\"", "test_settings.py") == []


if __name__ == "__main__":
    from standards_selftest import run_module_tests

    sys.exit(run_module_tests(globals(), "config-default-in-code cases"))

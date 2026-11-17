"""Cases for the debug-flag-in-prod rule.

THE CASE THAT MOTIVATED IT is `test_the_yii_entrypoint_shape_is_flagged`: a production entry
point that shipped `defined('YII_DEBUG') or define('YII_DEBUG', true);`, so every unhandled
exception returned a stack trace, file paths and SQL to the browser. The verbatim line is
pinned here so the `defined(...) or define(...)` idiom -- the part reviewers' eyes skip -- can
never quietly stop being a finding.

The negatives matter as much. A value read from the environment WITHOUT a true-default IS the
fix, so `DEBUG = os.environ.get('DEBUG') == '1'` and `'debug' => env('APP_DEBUG', false)` must
stay clean; a commented `# DEBUG = True` and `APP_DEBUG=false` must too. A rule that flagged
those would be firing on the code that already did the right thing.

Run: python test_standards_debugflag.py   (or pytest)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_debugflag import RULE, check_debug_flag  # noqa: E402
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

EXEMPT = "throwaway local-only entry point that is never deployed; verified out of the image"
assert len(EXEMPT) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


def found(name: str, *lines: str) -> list[str]:
    return [v.message for v in check_debug_flag(Path(name), list(lines))]


# ---- Python (.py) ----------------------------------------------------------------------------


def test_a_module_level_debug_true_is_flagged() -> None:
    """The Django settings shape -- one literal that turns on the traceback error page."""
    assert found("settings.py", "DEBUG = True")


def test_debug_true_with_trailing_comment_is_flagged() -> None:
    assert found("settings.py", "DEBUG = True  # temporary")


def test_debug_read_from_the_environment_is_clean() -> None:
    """Reading from the environment with no true-default is the FIX, not the defect."""
    assert not found("settings.py", "DEBUG = os.environ.get('DEBUG') == '1'")
    assert not found("settings.py", "DEBUG = env.bool('DEBUG', default=False)")
    assert not found("settings.py", "DEBUG = config('DEBUG', cast=bool, default=False)")


def test_debug_false_is_clean() -> None:
    assert not found("settings.py", "DEBUG = False")


def test_an_indented_debug_true_is_not_the_settings_shape() -> None:
    """`self.DEBUG = True` in a method, or a nested dict entry, is not the module-level
    settings toggle this rule is about -- anchoring to the line start keeps it out."""
    assert not found("app.py", "    self.DEBUG = True")
    assert not found("app.py", "        DEBUG = True")


def test_a_commented_out_python_debug_flag_is_ignored() -> None:
    """`iter_code_lines` skips comments, so the rule never reports its own examples."""
    assert not found("settings.py", "# DEBUG = True")


# ---- PHP (.php) ------------------------------------------------------------------------------


def test_the_yii_entrypoint_shape_is_flagged() -> None:
    """Verbatim from the audited production entry point."""
    assert found("index.php", "defined('YII_DEBUG') or define('YII_DEBUG', true);")


def test_yii_debug_with_double_quotes_is_flagged() -> None:
    assert found("index.php", 'define("YII_DEBUG", true);')


def test_yii_debug_defined_false_is_clean() -> None:
    assert not found("index.php", "defined('YII_DEBUG') or define('YII_DEBUG', false);")


def test_display_errors_on_is_flagged() -> None:
    for value in ("'1'", "1", "'On'", "'true'"):
        assert found("bootstrap.php", f"ini_set('display_errors', {value});"), value


def test_display_errors_off_is_clean() -> None:
    for value in ("'0'", "0", "'Off'", "'false'"):
        assert not found("bootstrap.php", f"ini_set('display_errors', {value});"), value


def test_error_reporting_e_all_is_not_flagged() -> None:
    """Raising the reporting LEVEL is legitimate -- it controls logging, not what reaches the
    browser. Only display_errors puts the errors in front of the user."""
    assert not found("bootstrap.php", "error_reporting(E_ALL);")


def test_a_commented_out_yii_debug_is_ignored() -> None:
    assert not found("index.php", "// define('YII_DEBUG', true);")


# ---- Laravel config/*.php --------------------------------------------------------------------


def test_a_laravel_debug_env_default_true_is_flagged() -> None:
    """`config/app.php` verbatim in shape -- the env() DEFAULT is what ships when unset."""
    assert found(
        str(Path("config") / "app.php"),
        "    'debug' => env('APP_DEBUG', true),",
    )


def test_a_laravel_debug_env_default_false_is_clean() -> None:
    assert not found(
        str(Path("config") / "app.php"),
        "    'debug' => env('APP_DEBUG', false),",
    )


def test_the_laravel_shape_is_scoped_to_config_directory() -> None:
    """The `'debug' => env(...)` key means what Laravel means by it only under config/. A bare
    occurrence elsewhere is too generic to read, so it is not a finding there."""
    assert not found("Helper.php", "    'debug' => env('APP_DEBUG', true),")


# ---- .env.example ----------------------------------------------------------------------------


def test_env_example_app_debug_true_is_flagged() -> None:
    assert found(".env.example", "APP_DEBUG=true")


def test_env_example_every_debug_key_is_flagged() -> None:
    for key in ("APP_DEBUG", "DEBUG", "YII_DEBUG"):
        assert found(".env.example", f"{key}=true"), key


def test_env_example_app_debug_false_is_clean() -> None:
    assert not found(".env.example", "APP_DEBUG=false")


def test_a_commented_env_example_debug_flag_is_ignored() -> None:
    assert not found(".env.example", "# APP_DEBUG=true")


# ---- web.config / app.config -----------------------------------------------------------------


def test_config_compilation_debug_true_is_flagged() -> None:
    assert found("web.config", '    <compilation debug="true" targetFramework="4.8" />')


def test_config_compilation_debug_false_is_clean() -> None:
    assert not found("web.config", '    <compilation debug="false" targetFramework="4.8" />')


# ---- exemption -------------------------------------------------------------------------------


def test_a_line_exemption_silences_it() -> None:
    assert not found(
        "index.php",
        f"    // standards: {RULE} exempt -- {EXEMPT}",
        "    defined('YII_DEBUG') or define('YII_DEBUG', true);",
    )


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "debug-flag-in-prod cases"))

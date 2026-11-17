#!/usr/bin/env python3
"""Cases for the constants family: where a value that may change is allowed to live.

Plain asserts, no pytest, so it runs anywhere Python does:

    python engineering_standards/test_standards_constants.py

`config-default-in-code`'s cases moved to test_standards_config_defaults.py with the rule
(2026-10-22); the list below keeps its history.

THE CASES THAT MATTER MOST ARE THE QUIET ONES. All three rules fire on shapes that are
extremely common in correct code -- a `??`, an absolute-looking string, a repeated word -- so
the risk here is not a missed finding but a noisy rule, which gets baselined wholesale and
then means nothing. Every negative case below is a real false positive that the first draft
produced against payvisia or against this pack, kept as a case so it cannot come back:

  * a route (`/platform/partnere`) read as a filesystem path;
  * a clock time read as a host and port;
  * a test's fixture URL read as a deployment value;
  * this pack's own test fixtures read as configuration defaults;
  * an ordinary Danish word appearing in two files read as a duplicated constant;
  * a Blazor `@page`, which the framework forbids referencing a constant from.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_config import CheckConfig  # noqa: E402
from standards_constants import (  # noqa: E402
    check_duplicated_constants,
    check_environment_literal,
)


def environment(source: str, name: str = "Startup.cs") -> list[str]:
    return [v.rule for v in check_environment_literal(Path(name), source.splitlines())]


def duplicated(files: dict[str, str]) -> list[str]:
    """Rule ids for a throwaway repo holding exactly these files."""
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        paths = []
        for name, body in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            paths.append(path)
        return [v.rule for v in check_duplicated_constants(root, paths, CheckConfig())]


def messages(files: dict[str, str]) -> list[str]:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        paths = []
        for name, body in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            paths.append(path)
        return [v.message for v in check_duplicated_constants(root, paths, CheckConfig())]


# ---- const-environment-literal -----------------------------------------------------------


def test_an_email_address_held_as_a_constant_fires():
    source = '    private const string DevelopmentEmail = "localadmin@allegroit.dk";'
    assert environment(source) == ["const-environment-literal"]


def test_a_deployment_path_a_url_and_a_host_port_all_fire():
    assert environment('    private const string KeyPath = "/app/keys";')
    assert environment('DEFAULT_ENDPOINT = "https://api.example.com/v1"', "settings.py")
    assert environment("    const DB_HOST = 'db.internal:3307';", "config.php")


def test_a_declaration_is_reported_once_even_though_the_patterns_overlap():
    """PHP `const NAME = '...'` matches the PHP form AND the UPPER_SNAKE form, by design."""
    assert environment("    const DB_HOST = 'db.internal:3307';", "config.php") == ["const-environment-literal"]


def test_a_literal_that_is_only_an_operand_is_not_the_constants_value():
    """`APPLY = "--apply" in sys.argv` is a BOOLEAN. The literal is an operand, not the value,
    and reading it as one was the pack's own only false finding when this rule first ran."""
    findings = duplicated(
        {
            "sync_skills.py": 'APPLY = "--apply" in sys.argv\n',
            "sync_pack.py": '    parser.add_argument("--apply", action="store_true")\n',
        }
    )
    assert findings == []


def test_a_route_is_not_a_filesystem_path():
    """Two slash-separated segments is also the shape of a route, and a route constant is the
    RIGHT answer to the duplication rule -- a looser pattern had this family contradicting
    itself."""
    assert environment('    internal const string Partners = "/platform/partnere";') == []
    assert environment('    internal const string LoginPath = "/log-ind";') == []


def test_a_clock_time_is_not_a_host_and_port():
    assert environment('    private const string Cutoff = "12:30";') == []


def test_a_namespace_a_media_type_and_a_format_string_are_not_environment_values():
    assert environment('    internal const string Ns = "Microsoft.AspNetCore.Identity";') == []
    assert environment('CONTENT_TYPE = "application/json"', "http.py") == []
    assert environment('    public const string Stamp = "yyyy-MM-ddTHH:mm:ss.fffffff";') == []


def test_a_configuration_key_name_is_not_the_value_it_names():
    assert environment('    private const string EmailSetting = "Superadmin:Email";') == []


def test_an_ordinary_variable_is_not_a_constant():
    assert environment('var email = "someone@example.com";') == []


def test_a_tests_fixture_url_belongs_in_the_source():
    source = '    private const string BaseUrl = "https://erhr.payvisia.example";'
    assert environment(source, "OwnerInvitationTests.cs") == []


# ---- const-duplicated-literal ------------------------------------------------------------


def test_a_constant_written_raw_in_another_file_fires():
    findings = duplicated(
        {
            "Endpoints.cs": '    internal const string LoginPath = "/log-ind";\n',
            "Startup.cs": '        options.LoginPath = "/log-ind";\n',
        }
    )
    assert findings == ["const-duplicated-literal"]


def test_the_same_value_declared_in_two_files_fires():
    """Two homes for one value, which is the sharpest case payvisia had: `"CompanyId"` was
    declared in the DbContext and again in the interceptor, and it is what tenant isolation
    keys on."""
    findings = duplicated(
        {
            "Context.cs": '    internal const string TenantColumn = "CompanyId";\n',
            "Interceptor.cs": '    private const string TenantColumn = "CompanyId";\n',
        }
    )
    assert findings == ["const-duplicated-literal"]


def test_the_message_names_the_other_file_so_the_reader_can_go_there():
    said = messages(
        {
            "Endpoints.cs": '    internal const string LoginPath = "/log-ind";\n',
            "Startup.cs": '        options.LoginPath = "/log-ind";\n',
        }
    )
    assert len(said) == 1
    assert "LoginPath" in said[0] and "Endpoints.cs" in said[0]


def test_the_declaration_itself_is_never_reported_as_its_own_duplicate():
    findings = duplicated({"Endpoints.cs": '    internal const string LoginPath = "/log-ind";\n'})
    assert findings == []


def test_using_the_value_again_in_the_declaring_file_is_not_a_duplicate():
    findings = duplicated(
        {
            "Endpoints.cs": (
                '    internal const string LoginPath = "/log-ind";\n'
                '    // the same file may of course also mention "/log-ind" in code\n'
                '    private static string Failed() => "/log-ind" + "?fejl=forkert";\n'
            )
        }
    )
    assert findings == []


def test_an_ordinary_word_of_the_domains_language_is_not_a_duplicated_constant():
    """Two authors writing the same Danish word are not sharing a constant. This was 9 of the
    first draft's 26 findings against payvisia."""
    findings = duplicated(
        {
            "Lifecycle.cs": '    private const string AuditArea = "Ligelonsanalyse";\n',
            "Steps.razor": '        (7, "Ligelonsanalyse", "/analyse"),\n',
        }
    )
    assert findings == []


def test_a_short_value_is_a_word_not_a_value():
    findings = duplicated(
        {
            "Countries.cs": '    public const string Denmark = "DK";\n',
            "Seed.cs": '        CountryCode = "DK",\n',
        }
    )
    assert findings == []


def test_a_blazor_page_directive_cannot_reference_a_constant_so_it_is_not_reported():
    """Blazor resolves `@page` at compile time and accepts a literal only. Demanding a
    constant there would be demanding something the framework forbids."""
    findings = duplicated(
        {
            "Endpoints.cs": '    internal const string LoginPath = "/log-ind";\n',
            "LogInd.razor": '@page "/log-ind"\n',
        }
    )
    assert findings == []


def test_a_page_directive_after_a_byte_order_mark_is_still_a_page_directive():
    """Visual Studio saves .razor files with a UTF-8 BOM, and `\\s` does not match U+FEFF, so a
    BOM left in line 1 hid the `@page` from the exemption. Measured on payvisia's
    Dashboard.razor and Kriterier.razor, 2026-10-11."""
    findings = duplicated(
        {
            "Endpoints.cs": '    internal const string LoginPath = "/log-ind";\n',
            "LogInd.razor": '﻿@page "/log-ind"\n',
        }
    )
    assert findings == []


def test_a_test_file_may_pin_the_value_it_is_testing():
    findings = duplicated(
        {
            "Endpoints.cs": '    internal const string LoginPath = "/log-ind";\n',
            "PilotSurfaceTests.cs": '        Assert.Equal("/log-ind", response.Location);\n',
        }
    )
    assert findings == []


def test_the_rule_is_silent_when_the_repo_turns_the_family_off():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        first = root / "Endpoints.cs"
        first.write_text('    internal const string LoginPath = "/log-ind";\n', encoding="utf-8")
        second = root / "Startup.cs"
        second.write_text('        options.LoginPath = "/log-ind";\n', encoding="utf-8")
        config = CheckConfig(check_constants=False)
        assert list(check_duplicated_constants(root, [first, second], config)) == []


def test_a_line_marker_excuses_the_one_literal_it_sits_above():
    """The hatch this rule shipped without, while `collect_exemptions` advertised one anyway.

    Line-scoped, like `const-duplicated-literal` in this same family and for its reason: one
    file routinely holds both a genuine defect and a value that really is the same in every
    deployment, and a header marker would silently cover both.
    """
    findings = environment(
        """# standards: const-environment-literal exempt -- the vendor's published API base,
# identical in every deployment; config would invent a knob nobody ever turns.
API_BASE = "https://api.stripe.com/v1"
""",
        "engine/config.py",
    )
    assert findings == []


def test_a_marker_on_one_literal_does_not_excuse_the_next():
    """The whole argument for line scope: the excused value and the defect are neighbours."""
    findings = environment(
        """# standards: const-environment-literal exempt -- the vendor's published API base,
# identical in every deployment; config would invent a knob nobody ever turns.
API_BASE = "https://api.stripe.com/v1"
DATABASE_URL = "postgres://sourcetext@db.internal:5432/app"
""",
        "engine/config.py",
    )
    assert findings == ["const-environment-literal"]


def test_a_shrug_does_not_excuse_an_environment_literal():
    """The 30-character floor is the whole difference between a decision and a bypass."""
    findings = environment(
        """# standards: const-environment-literal exempt -- fine
API_BASE = "https://api.stripe.com/v1"
""",
        "engine/config.py",
    )
    assert findings == ["const-environment-literal"]


if __name__ == "__main__":
    from standards_selftest import run_module_tests

    sys.exit(run_module_tests(globals(), "constants family cases"))

#!/usr/bin/env python3
"""Cases for `technical-error-shown`.

Both halves, per docs/changing-a-rule.md: synthetic cases answer "does the signal fire", and
`test_stays_quiet_on_this_repos_own_source` answers the half they cannot. The C# cases are the
shapes InvoTrack actually shipped on 2026-10-06, not invented ones -- the multi-line Snackbar
with a ternary, the `root` walked down the inner exceptions, the provider's "(HTTP 400)".

This file is a test, so the rule skips it whole -- which is also why every fixture below can
quote the patterns without a marker.

Run: pytest test_standards_user_errors.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PACK_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(PACK_DIRECTORY))

from standards_checks import CheckConfig  # noqa: E402
from standards_config import parse_user_facing_exceptions  # noqa: E402
from standards_dispatch import check_source_file  # noqa: E402
from standards_registry import NEVER_BASELINED  # noqa: E402
from standards_user_errors import TECHNICAL_ERROR_RULE, check_technical_error_shown  # noqa: E402


def _findings(source: str, name: str = "Clients.razor") -> list[int]:
    return [v.line for v in check_technical_error_shown(Path(name), source.splitlines())]


def _catch(body: str, caught: str = "Exception ex") -> str:
    """`body` inside a C# catch block, starting at line 4."""
    return f"try\n{{\n}}\ncatch ({caught})\n{{\n{body}\n}}\n"


# ---- C# and Razor: the exception's own text --------------------------------------------


def test_snackbar_with_the_exception_message_fires():
    source = _catch('    Snackbar.Add($"Fejl ved lagring: {ex.Message}", Severity.Error);')
    assert _findings(source) == [6]


def test_the_multi_line_snackbar_with_a_ternary_fires_on_the_line_carrying_the_text():
    """The shape InvoTrack's Products and Clients pages shipped."""
    source = _catch(
        "    Snackbar.Add(ex is DbUpdateException\n"
        '        ? "Varen findes allerede."\n'
        '        : $"Fejl: {ex.InnerException?.Message ?? ex.Message}", Severity.Error);'
    )
    assert _findings(source) == [8]


def test_a_display_field_assigned_from_the_message_fires():
    source = _catch('    _error = $"Stripe-søgningen fejlede: {ex.Message}";')
    assert _findings(source) == [6]


def test_a_local_walked_down_the_inner_exceptions_fires():
    source = _catch(
        "    System.Exception root = ex;\n"
        "    while (root.InnerException != null) root = root.InnerException;\n"
        '    Snackbar.Add($"Fejl ved sletning: {root.Message}", Severity.Error);'
    )
    assert _findings(source) == [8]


def test_an_api_result_carrying_the_message_fires():
    assert _findings(_catch("    return Results.Problem(detail: ex.Message);"), "Endpoints.cs") == [6]


def test_a_response_write_carrying_the_stack_trace_fires():
    source = _catch('    await httpContext.Response.WriteAsync($"Export failed: {ex.StackTrace}");')
    assert _findings(source, "Endpoints.cs") == [6]


def test_a_result_object_carrying_the_message_back_to_the_page_fires():
    source = _catch("    return new ConnectionTestResult(false, ex.Message);")
    assert _findings(source, "UniContaProvider.cs") == [6]


def test_a_named_message_argument_fires():
    source = _catch(
        "    return new ConnectionTestResult(\n"
        "        Success: false,\n"
        '        Message: Truncate($"Uventet fejl: {ex.Message}", 480));'
    )
    assert _findings(source, "StripeProvider.cs") == [8]


def test_razor_markup_rendering_an_error_boundary_context_fires():
    source = (
        "<ErrorBoundary>\n"
        '    <ErrorContent Context="failure">\n'
        "        <MudAlert>@failure.Message</MudAlert>\n"
        "    </ErrorContent>\n"
        "</ErrorBoundary>\n"
    )
    assert _findings(source) == [3]


def test_a_field_declared_below_its_markup_still_binds_it():
    """A component's `@code` block, with its fields, sits under the markup that reads them."""
    source = "<MudAlert>@_loadError.Message</MudAlert>\n@code {\n    private Exception? _loadError;\n}\n"
    assert _findings(source) == [1]


def test_laundering_the_text_into_the_user_facing_exception_fires():
    """A throw, but into the one channel whose promise is that its text was written for people."""
    source = _catch('    throw new UserFacingException($"Kunne ikke gemme: {ex.Message}", ex);')
    assert _findings(source, "Service.cs") == [6]


# ---- C#: the status code standing in for an explanation ---------------------------------


def test_a_status_code_message_returned_to_the_page_fires():
    source = '    return new SyncResult(false, $"e-conomic svarede med en fejl (HTTP {(int)response.StatusCode}).");\n'
    assert _findings(source, "EconomicProvider.cs") == [1]


def test_a_status_code_in_a_snackbar_fires():
    source = '    Snackbar.Add($"Fejl (HTTP {(int)response.StatusCode})", Severity.Error);\n'
    assert _findings(source) == [1]


# ---- C#: what must stay silent ----------------------------------------------------------


def test_logging_the_message_is_silent():
    source = _catch('    _logger.LogError(ex, "Kunne ikke gemme {Message}", ex.Message);')
    assert _findings(source) == []


def test_a_multi_line_log_call_is_silent():
    source = _catch('    logger.LogWarning(\n        "Sync refused: {Message}",\n        ex.Message);')
    assert _findings(source, "Service.cs") == []


def test_wrapping_in_a_technical_exception_is_silent():
    source = _catch('    throw new InvalidOperationException($"Sync failed: {ex.Message}", ex);')
    assert _findings(source, "Service.cs") == []


def test_the_user_facing_exception_is_shown_as_is():
    source = _catch("    Snackbar.Add(ex.Message, Severity.Warning);", caught="UserFacingException ex")
    assert _findings(source) == []


def test_a_narrowed_user_facing_exception_is_shown_as_is():
    source = _catch("    if (ex is UserFacingException userFacing) Snackbar.Add(userFacing.Message, Severity.Warning);")
    assert _findings(source) == []


def test_a_chain_off_the_user_facing_exception_is_still_technical():
    source = _catch("    Snackbar.Add(ex.InnerException.Message, Severity.Error);", caught="UserFacingException ex")
    assert _findings(source) == [6]


def test_a_status_code_in_the_diagnostic_detail_is_silent():
    """InvoTrack's AccountingRefusal: the code goes to the log-bound property, the user gets a sentence."""
    source = (
        '    throw new UserFacingException("Regnskabssystemet afviste forbindelsen.")\n'
        "    {\n"
        '        Diagnostic = $"{system} {path} HTTP {(int)response.StatusCode}: {body}",\n'
        "    };\n"
    )
    assert _findings(source, "AccountingRefusal.cs") == []


def test_a_message_on_ordinary_data_is_silent():
    """`n.Message` on a notification and `result.Message` on a result are data, not exceptions."""
    source = (
        "<span>@n.Message</span>\n"
        "@code {\n"
        "    void Show() => Snackbar.Add(result.Message, result.IsSuccess ? Severity.Success : Severity.Error);\n"
        "}\n"
    )
    assert _findings(source) == []


def test_a_comment_is_silent():
    assert _findings(_catch('    // never: Snackbar.Add($"Fejl: {ex.Message}");')) == []


def test_the_line_exemption_silences_it():
    source = _catch(
        "    // standards: technical-error-shown exempt -- operator-only export log, read by\n"
        "    // support and never shown to a tenant.\n"
        '    await httpContext.Response.WriteAsync($"/* EXPORT ERROR: {ex.Message} */");'
    )
    assert _findings(source, "Endpoints.cs") == []


def test_test_files_are_skipped():
    source = _catch('    Snackbar.Add($"Fejl: {ex.Message}", Severity.Error);')
    assert _findings(source, "ClientsTests.cs") == []
    assert _findings(source, "InvoTrack.Tests/Pages/Clients.razor") == []


def test_a_component_whose_name_merely_ends_in_test_letters_is_not_a_test():
    """The test family's own filename pattern reads `LatestInvoices` as a test; this one must not."""
    source = _catch('    Snackbar.Add($"Fejl: {ex.Message}", Severity.Error);')
    assert _findings(source, "LatestInvoices.razor") == [6]
    assert _findings(source, "Contest.razor") == [6]


# ---- Python ------------------------------------------------------------------------------


def _except(body: str, caught: str = "Exception as e") -> str:
    """`body` inside a Python except clause, starting at line 4."""
    return f"def view(request):\n    try:\n        save()\n    except {caught}:\n{body}\n"


def test_django_messages_with_the_exception_fires():
    assert _findings(_except('        messages.error(request, f"Kunne ikke gemme: {e}")'), "views.py") == [5]


def test_a_multi_line_json_response_fires():
    source = _except('        return JsonResponse(\n            {"error": str(e)},\n            status=500,\n        )')
    assert _findings(source, "views.py") == [6]


def test_fastapi_http_exception_detail_fires():
    source = _except(
        "        raise HTTPException(status_code=400, detail=str(error)) from error", "AccountError as error"
    )
    assert _findings(source, "routes.py") == [5]


def test_laundering_into_a_validation_error_fires():
    assert _findings(_except("        raise ValidationError(str(e))"), "forms.py") == [5]


def test_a_response_status_code_in_a_message_fires():
    source = 'messages.error(request, f"Upstream said HTTP {response.status_code}")\n'
    assert _findings(source, "views.py") == [1]


def test_python_logging_and_wrapping_are_silent():
    assert _findings(_except('        logger.exception("save failed: %s", e)'), "views.py") == []
    assert _findings(_except('        raise RuntimeError(f"sync failed: {e}") from e'), "views.py") == []


def test_python_user_facing_error_is_shown_as_is():
    source = _except("        messages.error(request, str(e))", "UserFacingError as e")
    assert _findings(source, "views.py") == []


def test_a_field_on_a_log_record_is_where_detail_belongs():
    assert _findings(_except("        sync_log.error_message = str(e)[:4000]"), "tasks.py") == []


def test_a_models_own_status_field_is_not_an_http_status():
    """The first calibration false positive, from allegro-it-services."""
    assert _findings('reason = f"Already {existing_summary.status}"\n', "tasks.py") == []


# ---- PHP ---------------------------------------------------------------------------------


def _php_catch(body: str, caught: str = "Exception $e") -> str:
    """`body` inside a PHP catch block, starting at line 3."""
    return f"<?php\ntry {{ run(); }} catch ({caught}) {{\n{body}\n}}\n"


def test_php_echo_of_the_message_fires():
    assert _findings(_php_catch("    echo json_encode($e->getMessage());"), "api.php") == [3]


def test_php_display_local_fires():
    assert _findings(_php_catch("    $error = $e->getMessage();"), "page.php") == [3]


def test_php_logging_wrapping_and_the_carrier_are_silent():
    assert _findings(_php_catch("    error_log($e->getMessage());"), "api.php") == []
    assert _findings(_php_catch("    throw new RuntimeException('x: ' . $e->getMessage(), 0, $e);"), "api.php") == []
    assert _findings(_php_catch("    echo $e->getMessage();", "UserFacingException $e"), "api.php") == []


def test_php_previous_message_is_technical_even_off_the_carrier():
    source = _php_catch("    echo $e->getPrevious()->getMessage();", "UserFacingException $e")
    assert _findings(source, "api.php") == [3]


# ---- TypeScript / JavaScript --------------------------------------------------------------


def _ts_catch(body: str) -> str:
    """`body` inside a TS catch block, starting at line 3."""
    return f"try {{\n  await save();\n}} catch (err) {{\n{body}\n}}\n"


def test_ts_toast_and_state_setter_fire():
    assert _findings(_ts_catch("  toast.error(err.message);"), "page.tsx") == [4]
    assert _findings(_ts_catch("  setError(`Kunne ikke gemme: ${err.message}`);"), "page.tsx") == [4]


def test_ts_express_response_fires():
    assert _findings(_ts_catch("  res.status(500).json({ error: err.message });"), "server.ts") == [4]


def test_ts_promise_catch_binds_its_parameter():
    source = "load().catch((error: Error) => setFailure(error.message));\n"
    assert _findings(source, "editor.tsx") == [1]


def test_ts_response_status_in_a_message_fires():
    assert _findings("toast.error(`Request failed (HTTP ${response.status})`);\n", "api.ts") == [1]


def test_ts_logging_wrapping_and_member_access_are_silent():
    assert _findings(_ts_catch("  console.error(err.message);"), "page.tsx") == []
    assert _findings(_ts_catch("  throw new Error(`save failed: ${err.message}`);"), "page.tsx") == []
    # `result.err.message` is a client library's field, not the `err` the catch bound.
    assert _findings(_ts_catch("  toast.error(result.err.message);"), "page.tsx") == []


def test_ts_narrowed_user_facing_error_is_shown_as_is():
    assert _findings(_ts_catch("  if (err instanceof UserFacingError) toast.error(err.message);"), "page.tsx") == []


# ---- carriers declared in .standards.json -------------------------------------------------
#
# The rule reads one file, so a carrier's subclass declared elsewhere -- InvoTrack's
# `AccountingRefusalException : UserFacingException` -- is invisible without the declaration.


def _declared(source: str, name: str, *carriers: str) -> list[int]:
    return [v.line for v in check_technical_error_shown(Path(name), source.splitlines(), carriers)]


def test_an_undeclared_carrier_subclass_is_reported():
    source = _catch("    Snackbar.Add(ex.Message, Severity.Warning);", caught="AccountingRefusalException ex")
    assert _declared(source, "Export.razor") == [6]


def test_a_declared_carrier_is_shown_as_is():
    source = _catch("    Snackbar.Add(ex.Message, Severity.Warning);", caught="AccountingRefusalException ex")
    assert _declared(source, "Export.razor", "AccountingRefusalException") == []


def test_a_declared_python_domain_error_is_shown_as_is():
    """sourcetext.ai's shape: a domain error whose message was written for the reader."""
    source = _except(
        "        raise HTTPException(status_code=400, detail=str(error)) from error", "AccountError as error"
    )
    assert _declared(source, "routes.py", "AccountError") == []


def test_a_declared_carrier_narrowed_by_is_is_shown_as_is():
    source = _catch("    if (ex is AccountingRefusalException) Snackbar.Add(ex.Message, Severity.Warning);")
    assert _declared(source, "Export.razor", "AccountingRefusalException") == []


def test_laundering_into_a_declared_carrier_fires():
    source = _catch('    throw new AccountingRefusalException($"Afvist: {ex.Message}");')
    assert _declared(source, "Provider.cs", "AccountingRefusalException") == [6]


def test_the_declaration_parses_and_refuses_general_types():
    assert parse_user_facing_exceptions(None) == ()
    assert parse_user_facing_exceptions(["AccountError"]) == ("AccountError",)
    for bad in (["Exception"], ["InvalidOperationException"], ["Ns.AccountError"], "AccountError", [3]):
        with pytest.raises(SystemExit):
            parse_user_facing_exceptions(bad)


def test_findings_are_never_baselined():
    assert TECHNICAL_ERROR_RULE in NEVER_BASELINED


# ---- reached through the dispatcher -------------------------------------------------------


def test_the_dispatcher_reaches_the_rule(tmp_path):
    """A correct rule that is never called passes every case above -- see standards_dispatch."""
    page = tmp_path / "Clients.razor"
    source = _catch('    Snackbar.Add($"Fejl: {ex.Message}", Severity.Error);')
    page.write_text(source, encoding="utf-8")

    found = check_source_file(page, source.splitlines(), CheckConfig(), {}, tmp_path)

    assert TECHNICAL_ERROR_RULE in [violation.rule for violation in found]


def test_the_dispatcher_passes_the_declared_carriers(tmp_path):
    page = tmp_path / "Export.razor"
    source = _catch("    Snackbar.Add(ex.Message, Severity.Warning);", caught="AccountingRefusalException ex")
    page.write_text(source, encoding="utf-8")
    config = CheckConfig(user_facing_exceptions=("AccountingRefusalException",))

    found = check_source_file(page, source.splitlines(), config, {}, tmp_path)

    assert TECHNICAL_ERROR_RULE not in [violation.rule for violation in found]


# ---- the half synthetic cases cannot do --------------------------------------------------


def test_stays_quiet_on_this_repos_own_source():
    """Real code, exercising every construct the rule must ignore. The pack's own three hits --
    a developer CLI reporting a network error to the developer -- carry written exemptions."""
    offenders: list[str] = []
    for path in sorted(PACK_DIRECTORY.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        offenders.extend(f"{path.name}:{v.line}" for v in check_technical_error_shown(path, lines))

    assert offenders == [], f"{TECHNICAL_ERROR_RULE} fired on the pack's own source: {offenders}"

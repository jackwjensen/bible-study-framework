"""Cases for the js-eval-interop rule.

The negatives carry the weight. This rule reads every .cs and .razor line in the estate, and
JS interop is COMMON and almost always fine -- localStorage, clipboard, MudBlazor helpers.
A rule that fired on those would be exempted wholesale within a week, and an exempted rule
is a rule that is not running. Every negative below is a call shape that exists in the
estate today.

Run: pytest test_standards_injection.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from standards_core import CheckConfig
from standards_dispatch import check_source_file
from standards_injection import CODE_SINKS, check_js_eval_interop, check_php_eval
from standards_selftest import run_module_tests

RAZOR = Path("Components/Pages/Invoices.razor")


def findings(*lines: str) -> list[str]:
    return [v.message for v in check_js_eval_interop(RAZOR, list(lines))]


# ---- positives ------------------------------------------------------------------------------


def test_catches_the_invotrack_shape() -> None:
    """The real call site, verbatim in shape: identifier on one line, script on the next."""
    assert findings(
        '            await JS.InvokeVoidAsync("eval",',
        "                $\"{{ const a=document.createElement('a'); a.download='{name}'; }}\");",
    )


def test_catches_eval_with_the_script_on_the_same_line() -> None:
    assert findings('await JS.InvokeVoidAsync("eval", $"a.download=\'{name}\'");')


def test_catches_a_generic_invoke_async() -> None:
    assert findings('string? r = await JS.InvokeAsync<string?>("eval", script);')


def test_catches_the_function_constructor() -> None:
    assert findings('await JS.InvokeVoidAsync("Function", body);')


def test_catches_document_write() -> None:
    assert findings('await JS.InvokeVoidAsync("document.write", markup);')


def test_fires_even_with_a_constant_script() -> None:
    """A constant eval is still eval; the next edit is what makes it dynamic."""
    assert findings('await JS.InvokeVoidAsync("eval", "window.print()");')


def test_the_message_names_the_alternative() -> None:
    message = findings('await JS.InvokeVoidAsync("eval", script);')[0]
    assert "named function" in message and "ARGUMENT" in message


def test_the_message_warns_that_escaping_is_not_the_fix() -> None:
    """The helper that made the real bug survive review was a filesystem sanitiser."""
    assert "SanitizeFileName" in findings('await JS.InvokeVoidAsync("eval", s);')[0]


def test_it_reports_the_right_line() -> None:
    violations = list(
        check_js_eval_interop(RAZOR, ["// nothing", "// still nothing", 'await JS.InvokeVoidAsync("eval", s);'])
    )
    assert [v.line for v in violations] == [3]


# ---- negatives: real call shapes from the estate ---------------------------------------------


def test_quiet_on_localstorage() -> None:
    assert not findings('await JS.InvokeVoidAsync("localStorage.setItem", key, "true");')


def test_quiet_on_a_generic_localstorage_read() -> None:
    assert not findings('string? v = await JS.InvokeAsync<string?>("localStorage.getItem", k);')


def test_quiet_on_the_clipboard() -> None:
    assert not findings('await JS.InvokeVoidAsync("navigator.clipboard.writeText", text);')


def test_quiet_on_a_named_download_helper() -> None:
    """The shape this rule exists to push people towards must never itself be flagged."""
    assert not findings('await JS.InvokeVoidAsync("downloadFileFromStream", name, type, streamRef);')


def test_quiet_on_an_identifier_that_merely_contains_eval() -> None:
    """`evaluateTotals` is not `eval`; a substring match here would be pure noise."""
    assert not findings('await JS.InvokeVoidAsync("evaluateTotals", rows);')


def test_quiet_on_a_dotted_name_ending_in_eval() -> None:
    assert not findings('await JS.InvokeVoidAsync("myLib.evalHelper", x);')


def test_quiet_on_prose_mentioning_eval() -> None:
    assert not findings("// we used to call eval here; see FileDownload.cs for why we do not")


def test_quiet_on_a_csharp_method_actually_called_Function() -> None:
    """Matches only inside a JS interop call, not any string anywhere."""
    assert not findings('string kind = "Function";')


# ---- exemption + dispatch --------------------------------------------------------------------


def test_a_line_exemption_silences_it() -> None:
    assert not findings(
        "// standards: js-eval-interop exempt -- third-party widget requires a script string; "
        "the interpolated values are integers we generate, never user text.",
        'await JS.InvokeVoidAsync("eval", script);',
    )


def test_the_dispatcher_reaches_the_rule_for_razor() -> None:
    rules = [
        v.rule for v in check_source_file(RAZOR, ['await JS.InvokeVoidAsync("eval", s);'], CheckConfig(), {}, Path("."))
    ]
    assert "js-eval-interop" in rules


def test_the_dispatcher_reaches_the_rule_for_cs() -> None:
    rules = [
        v.rule
        for v in check_source_file(
            Path("Services/Download.cs"),
            ['await JS.InvokeVoidAsync("eval", s);'],
            CheckConfig(),
            {},
            Path("."),
        )
    ]
    assert "js-eval-interop" in rules


def test_every_sink_is_a_plain_identifier() -> None:
    """A sink with a quote or paren in it would never match the captured group."""
    for sink in CODE_SINKS:
        assert sink and '"' not in sink and "(" not in sink


# ---- php-eval: the same family, in the language that spells it by name ------------------------
#
# THE CASE THAT MOTIVATED IT: an external audit (RevJus, 2026-08) found `eval($field->class)`
# run on every admin page load against a value read straight from a database column, with a
# separate path where request data reached a database write -- together, remote code execution.

PHP = Path("common/models/CoreFieldHelper.php")


def php_findings(*lines: str) -> list[str]:
    return [v.message for v in check_php_eval(PHP, list(lines))]


def test_php_catches_the_revjus_shape() -> None:
    """A database column executed as PHP, verbatim in shape."""
    assert php_findings("$fieldClass = eval($field->class);")


def test_php_catches_create_function() -> None:
    """The deprecated dynamic-function builder is eval underneath."""
    assert php_findings("$fn = create_function('$a', $body);")


def test_php_message_names_the_sink_and_the_alternative() -> None:
    message = php_findings("eval($code);")[0]
    assert "eval()" in message and "allow-list" in message


def test_php_quiet_on_a_method_named_eval() -> None:
    """`$parser->eval(...)` is a method call, not the language construct."""
    assert not php_findings("$parser->eval($expression);")
    assert not php_findings("$this->eval($x);")


def test_php_quiet_on_an_identifier_merely_ending_in_eval() -> None:
    assert not php_findings("reeval($x);")
    assert not php_findings("$evaluation = compute($x);")


def test_php_quiet_on_a_commented_out_eval() -> None:
    """iter_code_lines skips comments, so a warning ABOUT eval is not itself a finding."""
    assert not php_findings("// eval($legacy);  -- never do this")


def test_php_a_line_exemption_silences_it() -> None:
    assert not php_findings(
        "// standards: php-eval exempt -- the template engine is sandboxed and the value is "
        "validated against an allow-list first",
        "eval($validated);",
    )


def test_the_dispatcher_reaches_php_eval() -> None:
    """An unreachable rule is indistinguishable from a passing one."""
    rules = [v.rule for v in check_source_file(PHP, ["eval($code);"], CheckConfig(), {}, Path("."))]
    assert "php-eval" in rules


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "injection-rule cases"))

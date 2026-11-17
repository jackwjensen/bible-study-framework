#!/usr/bin/env python3
"""Code assembled as text and then executed.

Rules here: js-eval-interop.

THE FAMILY. Not "untrusted input" -- that is a judgement no scanner can make. This is the
narrower, decidable thing: a call whose argument IS a program, built by string
interpolation in a language that has no idea it is producing code. Escaping is the wrong
remedy and always has been; the fix is to stop constructing the program.

WHY IT EARNS A RULE (InvoTrack, found 2026-08-21, present in FOUR call sites):

    await JS.InvokeVoidAsync("eval",
        $"{{ ... a.download='{downloadName}'; ... }}");

`downloadName` carried a customer's company name. A client called O'Brien closed the
JavaScript string early, so `eval` threw and that customer's invoice could never be
downloaded -- a plain functional bug, reported as "download is broken", with nothing
pointing at the cause. The same line is also stored XSS: a deliberately-chosen name runs
script in the authenticated session of whoever clicks download, and that repo's CSP
deliberately sets no `script-src`, so nothing stands in the way.

What made it survive review was a helper named `SanitizeFileName` sitting right above it,
which looked like the escaping was handled. It strips `Path.GetInvalidFileNameChars()` --
a FILESYSTEM concern -- and on Linux that set is exactly NUL and '/'. Every quote,
backslash and semicolon passes through. A sanitiser written for one boundary, reused at a
different one, keeping its reassuring name.

WHY THE SCANNER AND NOT AN ANALYZER. The decision ladder says use the strongest layer that
can hold a rule, and here that is not the analyzer layer. BannedSymbols.txt bans API
symbols, but `InvokeVoidAsync` is entirely legitimate -- the danger is the VALUE of its
first argument, which no shipped Roslyn analyzer inspects. ESLint cannot see it either,
because the JavaScript does not exist until C# builds it at runtime. So: a source scan.

WHAT TO DO INSTEAD. Pass the value as an ARGUMENT to a named function, so there is no
program text for it to break out of. In Blazor that is a function in wwwroot plus
`IJSRuntime.InvokeVoidAsync("myFunction", arg1, arg2)`, with `DotNetStreamReference` when
bytes are involved. Removing `eval` also un-blocks ever adding a `script-src` CSP, which
`unsafe-eval` would otherwise force open.

Source of truth: engineering-standards/engineering_standards/standards_injection.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import Violation, iter_code_lines
from standards_exemptions import line_exemption_reason

RULE = "js-eval-interop"
PHP_EVAL_RULE = "php-eval"

# Browser entry points that take a STRING and execute it as a program. `eval` and `Function`
# are the direct ones; document.write parses its argument as markup, which is the same
# problem wearing a different hat. Deliberately NOT listed: setTimeout/setInterval, whose
# string form is legal but whose overwhelmingly common form takes a function and would make
# this rule noisy -- and noise is what gets a rule exempted wholesale.
CODE_SINKS: frozenset[str] = frozenset({"eval", "Function", "execScript", "document.write", "document.writeln"})

# `InvokeVoidAsync("eval"` / `InvokeAsync<string?>("eval"` / `InvokeAsync<IJSObjectReference>(`.
# The generic argument is optional and may itself contain generics, so the character class
# excludes parentheses to stop the match running past the call it belongs to. A verbatim or
# interpolated prefix (@ or $) is tolerated because both are legal ways to spell the
# identifier, and an INTERPOLATED identifier is if anything more suspicious, not less.
JS_INTEROP_CALL = re.compile(
    r"""Invoke(?:Void)?Async        # the JSRuntime call
        (?:<[^>()]*>)?              # optional generic return type
        \s*\(\s*                    # opening paren
        [$@]{0,2}                   # optional $ / @ string prefixes
        "([^"]*)"                   # the JS identifier being invoked
    """,
    re.VERBOSE,
)


def check_js_eval_interop(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag JS interop that invokes a string-executing browser sink.

    Fires on the identifier alone, without requiring interpolation to be visible. Two
    reasons: the interpolation is frequently on the NEXT line (it was in all four InvoTrack
    call sites, because the script argument is long), and a call to `eval` with a constant
    string is still a call to `eval` -- the next edit is what makes it dynamic.
    """
    for index, line in enumerate(lines):
        for found in JS_INTEROP_CALL.finditer(line):
            identifier = found.group(1)
            if identifier not in CODE_SINKS:
                continue

            if line_exemption_reason(lines, index, RULE):
                continue

            yield Violation(
                path=path,
                line=index + 1,
                rule=RULE,
                message=(
                    f"JS interop invokes `{identifier}`, which executes its argument as a "
                    f"program. Whatever is interpolated into that argument becomes code: a "
                    f"name containing an apostrophe breaks the download outright, and a "
                    f"chosen one runs script in the caller's authenticated session. Escaping "
                    f"is not the fix -- a `SanitizeFileName`-style helper guards the "
                    f"FILESYSTEM (on Linux that is NUL and '/' only) and stops nothing here. "
                    f"Call a named function in wwwroot and pass the value as an ARGUMENT "
                    f"instead, using DotNetStreamReference where bytes are involved; then "
                    f"there is no program text to break out of. Dropping `eval` also leaves "
                    f"a `script-src` CSP available, which `unsafe-eval` would foreclose. If "
                    f"this call genuinely cannot be expressed as a named function, exempt the "
                    f"line with a written reason saying why."
                ),
            )


# PHP's direct dynamic-code sinks. `eval()` runs its string argument as PHP; `create_function`
# is the deprecated dynamic-function builder that does the same with an eval underneath. Both
# take a STRING that becomes a program, so anything interpolated into it becomes code. Unlike
# the C# interop case above there is no framework indirection: this is eval, in the language,
# by name -- which is why PHP needs the scanner where C#'s twin reads an interop call and JS/TS
# and Python are covered by eslint (`no-eval`) and ruff (`S307`/`S102`) respectively.
#
# The lookbehind keeps it off member accesses and other identifiers ending in the word: a
# method called `$this->eval(` or a variable `$eval` is not the language construct. Both sinks
# are always immediately followed by `(`.
#
# `assert()` is deliberately NOT listed: its string-executing behaviour was removed in PHP 8,
# and its ordinary boolean-expression form is legitimate and common, so flagging it would be
# noise on correct code. `preg_replace` with the `/e` modifier is gone since PHP 7.
PHP_CODE_SINK = re.compile(r"(?<![\w$>])(?P<sink>eval|create_function)\s*\(")


def check_php_eval(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a direct call to a PHP dynamic-code sink (`eval`, `create_function`).

    WHY IT EARNS A RULE. An external audit (RevJus, 2026-08) found `eval($field->class)` run
    on every admin page load against a value read straight from a database column, and a place
    elsewhere in the same app where request data reached a database write. Together that is
    remote code execution: an attacker stores PHP in a row and the next render runs it as the
    web user. `eval` also forecloses any Content-Security-Policy that would otherwise contain
    injected script.

    WHAT TO DO INSTEAD. There is nearly always a data-driven form of the same intent: a lookup
    table or match/switch over known cases, `json_decode` for data, a registered callable, or a
    class name validated against an allow-list and instantiated by name. If a call genuinely
    cannot avoid it, exempt the line with a written reason.
    """
    for line_number, line in iter_code_lines(lines, path.suffix):
        for found in PHP_CODE_SINK.finditer(line):
            if line_exemption_reason(lines, line_number - 1, PHP_EVAL_RULE):
                continue
            yield Violation(
                path=path,
                line=line_number,
                rule=PHP_EVAL_RULE,
                message=(
                    f"`{found.group('sink')}()` executes its string argument as PHP, so "
                    f"anything interpolated into it becomes code -- a value read from a "
                    f"database row or a request runs as the web user, which is remote code "
                    f"execution, and it also forecloses a script-src CSP. Escaping does not "
                    f"help; stop building a program. Use a match/switch over known cases, a "
                    f"registered callable, or a class name checked against an allow-list and "
                    f"instantiated by name. If this call genuinely cannot avoid it, exempt the "
                    f"line: `// standards: {PHP_EVAL_RULE} exempt -- <reason>`."
                ),
            )

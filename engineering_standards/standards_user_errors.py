#!/usr/bin/env python3
"""An exception's own text, or a bare status code, reaching the person using the software.

    technical-error-shown   `ex.Message`, `str(e)`, `$e->getMessage()`, `err.message` -- or a
                            message built as "(HTTP {status})" -- in output a user reads

Covers C# and Razor, Python, PHP, and TypeScript/JavaScript. The judgment half -- what the
message must say instead -- is `claude/rules/user-errors.md`; the incident and the design
are in `docs/user-errors.md`.

WHY THIS EARNED A SCANNER RULE. Every language's rules file has said since the pack began
that an entry point "shows a human-readable error". On 2026-10-06 InvoTrack was found to
break it in about seventy places, and Jack called it a repeat violation. Every one had the
same shape: a Blazor handler caught the exception and wrote
`Snackbar.Add($"Fejl ved lagring: {ex.Message}", Severity.Error)`. The Danish prefix is what
made it pass review -- it READS as a human-readable message -- while the part that carries the
information is .NET's English for a developer: "Response status code does not indicate
success: 400 (Bad Request).", or "An error occurred while saving the entity changes. See the
inner exception for details." Beside them, the accounting providers built "X svarede med en
fejl (HTTP 400).", where a status code stands in for the explanation. A rule that reviewers
read past seventy times is a rule that needs a mechanism.

WHAT DECIDES "REACHES THE USER", per statement rather than per line, because the shape that
actually occurs spans lines -- `Snackbar.Add(` on one, a ternary on the next, the
`{ex.InnerException?.Message ?? ex.Message}` on the third. In order:

  1. a logging call in the statement             -> silent: the log is where detail belongs
  2. the value lands in a log-bound target       -> silent: `Diagnostic = ...`, `debugInfo:`
  3. a sink, or a user-facing exception built    -> FINDING
  4. a technical exception thrown or wrapped     -> silent: it reaches the translator later
  5. a target named for display                  -> FINDING: `_error = ...`, `Message: ...`,
                                                    `"error": str(e)`, `setError(...)`

Step 3 before step 4 is deliberate. `throw new UserFacingException($"...{ex.Message}")` and
Django's `raise ValidationError(str(e))` are throws, and they are also the two ways to launder
technical text INTO the channel whose whole promise is that its text was written for a person.

WHAT COUNTS AS AN EXCEPTION'S TEXT. Only a name the file BINDS to a throwable -- a `catch` or
`except` clause, an exception-typed parameter, field or local, a pattern (`is X ex`), an
`instanceof` narrowing, a Razor `<ErrorContent Context="ex">` -- and never a name guessed from
its spelling, because `n.Message` on a notification and `result.Message` on a result object are
ordinary data and InvoTrack has dozens of each. Two chains are technical whatever their
receiver: an `InnerException`/`GetBaseException()` message (C#) and a `getPrevious()` message
(PHP) -- those are a framework's own text by construction.

THE DESIGNATED CARRIER IS EXEMPT, and it is identified by name: a type containing
`UserFacing` (`UserFacingException`, `UserFacingError`), or one the repo lists in
`.standards.json` `userFacingExceptions`. Its message was written for the user on purpose,
which is the whole point of having one -- an entry point shows it as-is and translates
everything else. A name bound to it is not technical; a chain off it
(`ufe.InnerException.Message`) still is.

WHY THE LIST, decided by Jack on 2026-10-06. This rule reads one file at a time, so it cannot
see that InvoTrack's `AccountingRefusalException` derives from `UserFacingException` in another
file, nor that sourcetext.ai's `AccountError` was written for the reader before the naming
convention existed. Scanning the whole repo for subclasses would put a tree walk into the
sub-second commit gate; a declaration states the fact once, where review sees it.

AND IT IS NEVER BASELINED (`standards_registry.NEVER_BASELINED`, same decision): a finding here
is a defect a user meets, not debt to defer. Fix it, declare the carrier, or mark the line.

WHAT IT CANNOT SEE, stated so nobody reads silence as compliance: a message passed through a
local with an unremarkable name (`var x = ex.Message;` two statements before the sink), a
string returned from a helper and shown by its caller, and markup that renders a field
assigned elsewhere. The display-target step catches the common case of each; the rest is the
rules file's job.

THE EXEMPTION IS LINE-SCOPED, because one file routinely holds a real violation beside a
legitimate exception -- an operator-only diagnostics page, a CLI whose user IS the developer.
Written beside the line, with a reason:

    // standards: technical-error-shown exempt -- operator-only export log, never shown to tenants

Test files are skipped: a test asserts on the technical text on purpose.

Source of truth: engineering-standards/engineering_standards/standards_user_errors.py
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from standards_core import Violation, iter_code_lines
from standards_exemptions import line_exemption_reason
from standards_tests import TEST_DIRECTORIES, TEST_PROJECT_DIRECTORY

TECHNICAL_ERROR_RULE = "technical-error-shown"

# The designated carrier of deliberate user text, matched on a type's last segment.
CARRIER = re.compile(r"\w*UserFacing\w*")
CARRIER_CONSTRUCTED = re.compile(r"(?<![\w.])(?:new\s+)?[\w.\\]*UserFacing\w*\s*\(")

# Where a value lands, read from the text BEFORE the occurrence in its statement: an assignment
# (`_error =`, `ref.value =`, `x ??=`), a named argument or object key (`Message:`, `{ error:`),
# a quoted key (`"error":`, `'error' =>`, `$_SESSION['error'] =`), or a Python keyword argument.
TARGET = re.compile(
    r"""(?mx)
    (?P<assigned>[A-Za-z_$][\w$.>-]*?) (?:\.value)? \s* (?:\?\?|\+)? = (?![=>])
  | (?:^|[({,]) \s* (?P<named>[A-Za-z_$][\w$]*) \s* : (?!:)
  | ['"] (?P<keyed>\w+) ['"] \s* (?: : | => | \]\s*= )
    """
)
DISPLAY_TARGET = re.compile(r"(?i)error|message|msg|alert|feedback|notice|warning|toast|failure|reason")
# The assigned target keeps its receiver (`sync_log.error_message`), so a field on a LOG record
# reads as the log it is: that row is where the technical detail is supposed to go.
DIAGNOSTIC_TARGET = re.compile(
    r"(?i)diagnostic|technical|debug|internal|trace|stack|forlog|logmessage|logtext|(?:^|[._>])log(?:s|ger)?[.\-]"
)

# How far a statement may reach either side of the occurrence. A Snackbar call with a ternary
# message is five lines; eight leaves room for a wrapped argument list without letting an
# unterminated statement swallow its neighbours.
STATEMENT_REACH = 8

NEVER = re.compile(r"(?!)")

# A bound name read as itself, never as a member of something else: `result.error?.message` is a
# GraphQL client's field, not the `error` a `catch (error)` elsewhere in the file bound.
UNQUALIFIED = r"(?<![\w$.])"


@dataclass(frozen=True)
class Dialect:
    """One language's spelling of every question this rule asks."""

    suffixes: frozenset[str]
    # Patterns with a `name` group and an optional `type` group. `catches` bind a throwable
    # whatever the type says (or with no type at all); `declarations` count only when the type
    # is a throwable or the carrier.
    catches: tuple[re.Pattern[str], ...]
    declarations: tuple[re.Pattern[str], ...]
    throwable_type: re.Pattern[str]
    technical: Callable[[str], str]  # bound name -> regex source for its technical text
    always_technical: re.Pattern[str]
    status_code: re.Pattern[str]
    sink: re.Pattern[str]
    log: re.Pattern[str]
    throw: re.Pattern[str]
    # True: a statement ends at `;`, `{` or `}`. False: at bracket balance, which is the only
    # boundary Python has, and the only reliable one in semicolon-free TypeScript.
    semicolons: bool


CSHARP = Dialect(
    suffixes=frozenset({".cs", ".razor"}),
    catches=(
        re.compile(r"\bcatch\s*\(\s*(?P<type>[\w.]+)\s+(?P<name>\w+)\s*\)"),
        re.compile(r"<ErrorContent\b[^>]*\bContext\s*=\s*\"(?P<name>\w+)\""),
        re.compile(r"\bvar\s+(?P<name>\w+)\s*=\s*\w+\s*\??\.\s*(?:GetBaseException\s*\(\s*\)|InnerException)\b"),
    ),
    declarations=(
        re.compile(r"(?<![\w.])(?P<type>[\w.]*Exception)\??\s+(?P<name>[A-Za-z_]\w*)\s*(?:[,);=]|=>|\bwhen\b)"),
        re.compile(r"\b(?:is|case)\s+(?P<type>[\w.]+)\s+(?P<name>[A-Za-z_]\w*)\b"),
        re.compile(r"\b(?P<name>[A-Za-z_]\w*)\s+is\s+(?P<type>[\w.]+)\s*(?:\)|&&|\|\||\?|$)"),
    ),
    throwable_type=re.compile(r"Exception$"),
    technical=lambda name: (
        rf"{UNQUALIFIED}{name}\s*\??\.\s*(?:Message|StackTrace)\b|{UNQUALIFIED}{name}\s*\??\.\s*ToString\s*\(\s*\)"
        rf"|\{{\s*{name}\s*(?:[,:][^}}]*)?\}}"
    ),
    always_technical=re.compile(
        r"\b(?!(?i:\w*userfacing))\w*Exception\s*\??\.\s*(?:Message|StackTrace)\b"
        r"|\bGetBaseException\s*\(\s*\)\s*\??\.\s*Message\b"
    ),
    status_code=re.compile(
        r"\{\s*(?:\(int\)\s*)?[\w.?]+\.StatusCode\b[^}]*\}|\"\s*\+\s*\(int\)\s*[\w.?]+\.StatusCode\b"
    ),
    sink=re.compile(
        r"""(?x)
        \b_?[sS]nackbar\s*\.\s*Add\s*\(
      | \bToastService\s*\.\s*Show\w*\s*\(
      | \bDialogService\s*\.\s*ShowMessageBox\w*\s*\(
      | \b(?:Typed)?Results\s*\.\s*\w+\s*\(
      | (?<![.\w])(?:Problem|ValidationProblem|BadRequest|Conflict|NotFound|UnprocessableEntity
                    |Unauthorized|Content|StatusCode|Ok)\s*\(
      | \bResponse\s*\.\s*Write\w*\s*\(
      | \bnew\s+[\w.]*(?:Result|Response)\s*\(
      | \bAddModelError\s*\(
        """
    ),
    log=re.compile(
        r"\b_?\w*[lL]ogger\w*\s*\.\s*\w+\s*\(|\bLog\s*\.\s*\w+\s*\("
        r"|\.\s*Log(?:Trace|Debug|Information|Warning|Error|Critical)?\s*\(|\b(?:Debug|Trace)\s*\.\s*Write"
    ),
    throw=re.compile(r"\bthrow\b|\bnew\s+[\w.]*Exception\s*\("),
    semicolons=True,
)

PYTHON = Dialect(
    suffixes=frozenset({".py"}),
    catches=(re.compile(r"\bexcept\s+\(?\s*(?P<type>[\w.]+(?:\s*,\s*[\w.]+)*)\s*\)?\s+as\s+(?P<name>\w+)"),),
    declarations=(
        re.compile(r"\b(?P<name>[A-Za-z_]\w*)\s*:\s*(?P<type>[\w.]*(?:Exception|Error))\b"),
        re.compile(r"\bisinstance\(\s*(?P<name>\w+)\s*,\s*(?P<type>[\w.]+)\s*\)"),
    ),
    throwable_type=re.compile(r"(?:Exception|Error)$"),
    technical=lambda name: (
        rf"\b(?:str|repr)\(\s*{name}\s*\)|\{{\s*{name}\s*(?:![rsa])?\s*(?::[^}}]*)?\}}"
        rf"|{UNQUALIFIED}{name}\.(?:args|message)\b|%\s*\(?\s*{name}\b(?!\.)|\.format\([^)]*\b{name}\b(?!\.)"
    ),
    always_technical=re.compile(r"\btraceback\.format_exc(?:eption)?\s*\("),
    # `.status_code` anywhere; bare `.status` only on a response, because `{summary.status}` is
    # a model's own state field and reading it as HTTP was the first calibration false positive.
    status_code=re.compile(
        r"\{\s*[\w.]+\.status_code\s*\}|\{\s*(?:[\w.]*\.)?resp\w*\.status\s*\}|\bstr\(\s*[\w.]+\.status_code\s*\)"
    ),
    sink=re.compile(
        r"""(?x)
        \bmessages\s*\.\s*(?:error|warning|info|success|debug|add_message)\s*\(
      | \b\w*HttpResponse\w*\s*\( | \bJsonResponse\s*\( | (?<![\w.])Response\s*\(
      | \.add_error\s*\( | \bValidationError\s*\( | \bHTTPException\s*\(
      | \bflash\s*\( | \babort\s*\(\s*\d+\s*,
        """
    ),
    log=re.compile(
        r"(?i:\b\w*log(?:ger)?)\s*\.\s*(?:debug|info|warning|warn|error|exception|critical|log)\s*\("
        r"|\blogging\.\w+\s*\(|\bcapture_exception\s*\("
    ),
    throw=re.compile(r"\braise\b|\b\w*(?:Error|Exception)\s*\("),
    semicolons=False,
)

PHP = Dialect(
    suffixes=frozenset({".php"}),
    catches=(re.compile(r"\bcatch\s*\(\s*(?P<type>[\w\\|\s]+?)\s+\$(?P<name>\w+)\s*\)"),),
    declarations=(
        re.compile(r"(?P<type>\\?[\w\\]*(?:Throwable|Exception|Error))\s+\$(?P<name>\w+)"),
        re.compile(r"\$(?P<name>\w+)\s+instanceof\s+(?P<type>[\w\\]+)"),
    ),
    throwable_type=re.compile(r"(?:Throwable|Exception|Error)$"),
    technical=lambda name: rf"\${name}\s*->\s*(?:getMessage|getTraceAsString)\s*\(\s*\)|\(string\)\s*\${name}\b",
    always_technical=re.compile(r"->\s*getPrevious\s*\(\s*\)\s*->\s*getMessage\s*\("),
    status_code=re.compile(
        r"\{\$[\w>-]+->(?:getStatusCode|status)\(\)\}|['\"]\s*\.\s*\$[\w>-]+->(?:getStatusCode|status)\(\)"
    ),
    sink=re.compile(
        r"""(?x)
        \becho\b | \bprint\b | \bprintf\s*\( | \bdie\s*\( | \bexit\s*\( | \bwp_die\s*\(
      | \bwp_send_json\w*\s*\( | \bjson_encode\s*\( | ->\s*with(?:Errors)?\s*\(
      | ->\s*flash\s*\( | ->\s*addFlash\s*\( | \bresponse\s*\(\s*\) | \bnew\s+\w*Response\s*\(
      | \babort\s*\( | \$_SESSION\s*\[
        """
    ),
    log=re.compile(
        r"\berror_log\s*\(|(?i:\$\w*log(?:ger)?)\s*->\s*\w+\s*\(|\bLog::\w+\s*\(|\blogger\s*\(\s*\)"
        r"|(?<![\w>:$])report\s*\("
    ),
    throw=re.compile(r"\bthrow\b|\bnew\s+\\?[\w\\]*(?:Exception|Error)\s*\("),
    semicolons=True,
)

SCRIPT = Dialect(
    suffixes=frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}),
    catches=(
        re.compile(r"\bcatch\s*\(\s*(?P<name>[A-Za-z_$][\w$]*)"),
        re.compile(r"\.catch\(\s*(?:async\s*)?\(?\s*(?P<name>[A-Za-z_$][\w$]*)"),
    ),
    declarations=(
        re.compile(r"\b(?P<name>[A-Za-z_$][\w$]*)\s*:\s*(?P<type>\w*Error)\b"),
        re.compile(r"\b(?P<name>[A-Za-z_$][\w$]*)\s+instanceof\s+(?P<type>\w+)"),
    ),
    throwable_type=re.compile(r"(?:Error|Exception)$"),
    technical=lambda name: (
        rf"{UNQUALIFIED}{name}\s*\??\.\s*(?:message|stack)\b|\(\s*{name}\s+as\s+\w+\s*\)\s*\??\.\s*message\b"
        rf"|\bString\(\s*{name}\s*\)|\$\{{\s*{name}\s*\}}"
    ),
    always_technical=NEVER,
    status_code=re.compile(r"\$\{\s*(?:[\w.?]*\.)?(?:res|resp\w*)\??\.status(?:Code|Text)?\s*\}"),
    sink=re.compile(
        r"""(?x)
        \btoast(?:\s*\.\s*\w+)?\s*\( | \balert\s*\( | \benqueueSnackbar\s*\(
      | \bset\w*(?:Error|Message|Alert|Notice|Feedback)\w*\s*\(
      | \b(?:res|reply)\s*(?:\.\s*status\s*\([^)]*\))?\s*\.\s*(?:json|send|end)\s*\(
      | \bnew\s+Response\s*\( | \b(?:Next)?Response\s*\.\s*json\s*\(
      | \.\s*(?:textContent|innerText|innerHTML)\s*= | \bctx\s*\.\s*body\s*=
      | \bmessage\s*\.\s*(?:error|warning|info)\s*\( | \bshow\w*(?:Error|Message|Toast)\w*\s*\(
        """
    ),
    log=re.compile(
        r"\bconsole\s*\.\s*\w+\s*\(|(?i:\b\w*log(?:ger)?)\s*\.\s*(?:debug|info|warn|warning|error|trace|log)\s*\("
        r"|\bcaptureException\s*\("
    ),
    throw=re.compile(r"\bthrow\b|\bnew\s+\w*Error\s*\(|\bPromise\s*\.\s*reject\s*\("),
    semicolons=False,
)

DIALECTS = (CSHARP, PYTHON, PHP, SCRIPT)


def _dialect(path: Path) -> Dialect | None:
    return next((dialect for dialect in DIALECTS if path.suffix in dialect.suffixes), None)


def _is_test_source(path: Path) -> bool:
    """Tests assert on technical text deliberately. Same directories and test-project shape as
    the test family, but a tighter FILENAME test: its `.+tests?` reads `LatestInvoices` and
    `Contest` as tests, which here would silence real components."""
    if any(part.casefold() in TEST_DIRECTORIES or TEST_PROJECT_DIRECTORY.match(part) for part in path.parts[:-1]):
        return True
    stem = path.stem
    lowered = stem.casefold()
    return (
        lowered in {"test", "tests", "conftest"}
        or lowered.startswith(("test_", "test-"))
        or lowered.endswith(("_test", "-test", ".test", ".spec", "_spec"))
        or bool(re.search(r"[a-z0-9]Tests?$", stem))
    )


def _is_carrier(type_text: str, declared: frozenset[str]) -> bool:
    """Every alternative in `except (A, B)` / `catch (A | B $e)` is a carrier -- a `UserFacing*`
    name, or one the repo declared in `userFacingExceptions`."""
    parts = [part.strip().rsplit(".", 1)[-1].rsplit("\\", 1)[-1] for part in re.split(r"[,|]", type_text)]
    parts = [part for part in parts if part]
    return bool(parts) and all(CARRIER.fullmatch(part) or part in declared for part in parts)


def _carrier_constructed(declared: frozenset[str]) -> re.Pattern[str]:
    """A carrier being built: `new UserFacingException(`, `raise AccountError(` when declared."""
    if not declared:
        return CARRIER_CONSTRUCTED
    names = "|".join(re.escape(name) for name in sorted(declared))
    return re.compile(rf"{CARRIER_CONSTRUCTED.pattern}|(?<![\w.])(?:new\s+)?(?:\w+[.\\])*(?:{names})\s*\(")


def _bindings(
    code: list[tuple[int, str]], dialect: Dialect, declared: frozenset[str]
) -> dict[str, list[tuple[int, bool]]]:
    """Every name bound to a throwable: name -> [(line index, is technical)], in line order."""
    found: dict[str, list[tuple[int, bool]]] = {}
    for number, line in code:
        for pattern in (*dialect.catches, *dialect.declarations):
            always_throwable = pattern in dialect.catches
            for match in pattern.finditer(line):
                type_text = match.groupdict().get("type") or ""
                carrier = bool(type_text) and _is_carrier(type_text, declared)
                if not (always_throwable or carrier or dialect.throwable_type.search(type_text)):
                    continue
                found.setdefault(match.group("name"), []).append((number - 1, not carrier))
    return found


def _is_technical_here(bindings: list[tuple[int, bool]], index: int) -> bool:
    """The binding in force at `index`: the last one above it, else the first below -- a field
    declared at the foot of a component's `@code` block binds every use above it."""
    above = [technical for line, technical in bindings if line <= index]
    return above[-1] if above else bindings[0][1]


def _ends_statement(line: str) -> bool:
    code = re.sub(r"\s+//[^\"']*$", "", line.rstrip())
    return code.endswith((";", "{", "}"))


def _net_open(line: str) -> int:
    return sum(line.count(c) for c in "([{") - sum(line.count(c) for c in ")]}")


def _statement_span(lines: list[str], index: int, dialect: Dialect) -> tuple[int, int]:
    """The first and last line of the statement holding line `index`."""
    lowest = max(0, index - STATEMENT_REACH)
    highest = min(len(lines) - 1, index + STATEMENT_REACH)
    start = index
    if dialect.semicolons:
        while start > lowest and not _ends_statement(lines[start - 1]):
            start -= 1
        end = index
        while end < highest and not _ends_statement(lines[end]):
            end += 1
        return start, end

    depth = 0
    for above in range(index - 1, lowest - 1, -1):
        depth += _net_open(lines[above])
        if depth > 0:
            start = above
    depth = sum(_net_open(lines[k]) for k in range(start, index + 1))
    end = index
    while depth > 0 and end < highest:
        end += 1
        depth += _net_open(lines[end])
    return start, end


def _target(prefix: str) -> str:
    """The name the value lands in: the last target written before it in its statement."""
    targets = [next(name for name in match.groups() if name) for match in TARGET.finditer(prefix)]
    return targets[-1] if targets else ""


def _reaches_the_user(
    lines: list[str], index: int, position: int, dialect: Dialect, path: Path, carrier_built: re.Pattern[str]
) -> bool:
    """Steps 1-5 of the module docstring, in that order."""
    start, end = _statement_span(lines, index, dialect)
    statement = "\n".join(lines[start : end + 1])
    offset = sum(len(lines[k]) + 1 for k in range(start, index)) + position
    if dialect.log.search(statement):
        return False
    target = _target(statement[:offset])
    if target and DIAGNOSTIC_TARGET.search(target):
        return False
    if dialect.sink.search(statement) or carrier_built.search(statement):
        return True
    if path.suffix == ".razor" and lines[index][:position].rstrip().endswith(("@", "@(")):
        return True
    if dialect.throw.search(statement):
        return False
    return bool(target and DISPLAY_TARGET.search(target))


def _occurrence(line: str, index: int, dialect: Dialect, technical: dict[str, tuple[re.Pattern[str], list]]):
    """(position, matched text, is a status code) for the first technical text on the line."""
    found = dialect.always_technical.search(line)
    if found:
        return found.start(), found.group(0), False
    for pattern, bindings in technical.values():
        found = pattern.search(line)
        if found and _is_technical_here(bindings, index):
            return found.start(), found.group(0), False
    found = dialect.status_code.search(line)
    if found:
        return found.start(), found.group(0), True
    return None


_EXCEPTION_ADVICE = (
    "an exception's own text is reaching the user here (`{text}`). It was written for a developer "
    "-- usually English, technical, and often pointing at an inner exception -- so it tells the "
    "reader neither what failed, whose problem it is, nor whether trying again helps"
)
_STATUS_ADVICE = (
    "a bare status code is standing in for an explanation here (`{text}`). 'HTTP 400' tells the "
    "reader that something refused, not what was refused, whose problem it is, or whether trying "
    "again helps"
)


def check_technical_error_shown(
    path: Path, lines: list[str], user_facing_exceptions: Iterable[str] = ()
) -> Iterable[Violation]:
    """Every line whose exception text or bare status code reaches output a user reads.

    `user_facing_exceptions` is the repo's `.standards.json` declaration: carrier types beyond
    the `UserFacing*` names, recognised wherever they are caught, narrowed or built."""
    dialect = _dialect(path)
    if dialect is None or _is_test_source(path):
        return

    declared = frozenset(user_facing_exceptions)
    carrier_built = _carrier_constructed(declared)
    code = list(iter_code_lines(lines, path.suffix))
    technical = {
        name: (re.compile(dialect.technical(re.escape(name))), bindings)
        for name, bindings in _bindings(code, dialect, declared).items()
    }

    for number, line in code:
        index = number - 1
        found = _occurrence(line, index, dialect, technical)
        if found is None:
            continue
        position, text, is_status = found
        if not _reaches_the_user(lines, index, position, dialect, path, carrier_built):
            continue
        if line_exemption_reason(lines, index, TECHNICAL_ERROR_RULE) is not None:
            continue

        advice = (_STATUS_ADVICE if is_status else _EXCEPTION_ADVICE).format(text=text.strip())
        yield Violation(
            path=path,
            line=number,
            rule=TECHNICAL_ERROR_RULE,
            message=(
                f"{advice}. Show a sentence written for the reader instead: translate the failure "
                "in ONE shared place (InvoTrack's `ErrorMessages.Describe(ex, whatFailed)` is the "
                "model) that says what failed, whose problem it is, and whether trying again "
                "helps -- and log the exception there, where the technical detail belongs. Text "
                "deliberately written for the user travels in the repo's user-facing exception "
                "type (a name containing `UserFacing`, or one listed in `.standards.json` "
                "`userFacingExceptions`), which may be shown as-is. If no end user "
                "ever reads this output -- an operator-only diagnostic, a developer CLI -- say so "
                f"beside the line: 'standards: {TECHNICAL_ERROR_RULE} exempt -- <why>'. See "
                "user-errors.md."
            ),
        )

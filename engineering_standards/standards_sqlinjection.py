#!/usr/bin/env python3
"""A variable built into a SQL string that is then executed: the classic injection.

Rule here: sql-string-interpolation.

THE FAMILY. This is the sibling of js-eval-interop, one boundary over. There the argument
that gets executed is a JavaScript program; here it is a SQL statement. Both share the one
defect this pack keeps returning to -- a value is spliced into a sentence in a language that
has no idea it is producing code, so a character the author never pictured (a quote, a
semicolon, a `--`) stops being data and starts being grammar. Escaping is the wrong remedy
at both boundaries; the fix is to stop building the statement out of the value at all.

WHY IT EARNS A RULE (a real audit, a Yii app). Six raw queries interpolated a `$year` that
came straight off a GET parameter:

    $rows = Yii::$app->db
        ->createCommand("SELECT MONTH(created_at) months FROM t WHERE YEAR(created_at) = $year")
        ->queryAll();

`$year` is under the visitor's control, and PHP's double-quoted string interpolates it
before the database ever sees the text. `?year=2024 OR 1=1` widens the result set;
`?year=2024); DROP TABLE t; --` ends the statement and starts another. The value is not a
parameter to the query -- it is part of the query, indistinguishable from the SQL the
developer wrote. That is not a convention violation, it is a security defect, so this rule is
NOT config-gated and NOT baselineable; it is line-exemptable only, for the case where the
regex misreads a genuinely constant string.

WHY THE SCANNER AND NOT AN ANALYZER. The decision ladder says use the strongest layer that
can hold the rule. It is not the analyzer layer: the sink -- `createCommand`, `query`,
`execute`, `FromSqlRaw` -- is entirely legitimate, and no shipped Roslyn analyzer, ESLint
rule or PHP linter in this estate inspects the SHAPE of the string handed to it across four
languages at once. A `.razor` app, a Django service and a Yii site do not share a linter.
They share this scanner. So: a source scan, one rule, every dialect.

WHAT TO DO INSTEAD. Pass the value as a PARAMETER, so the database receives the statement and
the data on separate wires and never confuses one for the other:

    C#      FromSqlInterpolated($"... {year}")  -- NOT FromSqlRaw; the Interpolated form
            turns each hole into a DbParameter. Or ExecuteSqlInterpolated / a parameterised
            command.
    Python  cursor.execute("... WHERE year = %s", (year,))  -- the driver binds the tuple.
    PHP     $cmd->bindValue(':year', $year) with a ':year' placeholder, or PDO prepare +
            execute([':year' => $year]).
    JS/TS   pool.query('... WHERE year = $1', [year])  -- the array is bound, never spliced.

Escaping (`addslashes`, hand-rolled quoting) is not the fix and never was: it is a denylist
racing the parser, and the parser wins. If a query genuinely has no interpolated value and
the regex misread a constant, exempt the LINE with a written reason saying so.

THIS IS A HEURISTIC, and it is written to stay quiet. It fires only on the clear shape -- a
SQL-executing sink receiving, as its own first argument, a string the language interpolates a
variable into, or a string concatenated with one.

TWO SHAPES THAT LOOK ALIKE AND ARE NOT, because conflating them is how this rule gets
misjudged in both directions:

  * The sink WRAPPED away from its argument is still ONE expression, and IS reported.
    `cursor.execute(\n    f"... {id}"\n)` differs from the one-line form only in where a
    formatter put the newline -- there is no value to follow, the string is right there
    inside the parentheses. Until 2026-09-09 it was silent in all four dialects, which meant
    line length decided whether a security rule could see a defect. `_statement_is_dangerous`
    reads one code line ahead, bounded exactly as standards_tls bounds its own.

  * The query ASSEMBLED INTO A VARIABLE and executed later is NOT reported, deliberately.
    `query = (...)` three lines up, then `execute(query)`, cannot be told from a safe
    parameterised assembly without following the value, and a rule that fires on good code
    gets switched off. That boundary is pinned by a test rather than left to memory.

Prefer a false negative to a false positive; the estate is silent under it today.

Source of truth: engineering-standards/engineering_standards/standards_sqlinjection.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import Violation, iter_code_lines
from standards_exemptions import exemption_reason, line_exemption_reason
from standards_scope import SCRIPT_SUFFIXES

RULE = "sql-string-interpolation"

# ---------------------------------------------------------------------------------------
# PHP. Yii's `createCommand`, PDO/mysqli `query`, and the older `rawQuery`, plus the bare
# `mysqli_query($conn, ...)` function. The danger is a DOUBLE-quoted string (PHP interpolates
# those; single-quoted ones are inert) carrying a `$variable`, or a string CONCATENATED with
# one, handed to such a sink.
#
# A REGEX CANNOT DECIDE THIS ONE, and the estate proved it: a `"a" [^"]* $var [^"]* "b"`
# pattern reads the gap BETWEEN two string literals as if it were the inside of one, so a
# fully-parameterised `$db->query("... :name ...", ["name" => $values['x'], "tag" => $y])`
# lit up on the `$values` sitting between two array-key strings. Whether a `$` is inside a
# double-quoted string depends on quote PARITY, which a regex has no state to track. So PHP
# is scanned with a small state machine that splits the call's arguments and walks only the
# SQL one, consuming each string literal as a unit -- see `_php_sql_argument_is_dangerous`.
# ---------------------------------------------------------------------------------------

# The sink call itself. `mysqli_query` is a bare function whose SQL is the SECOND argument
# (the first is the connection); the method sinks take the SQL as their FIRST argument. The
# `.end()` of a match is the position just past the opening paren, where argument splitting
# begins. `mysqli_query` is remembered separately so the right argument gets analysed.
PHP_SINK = re.compile(r"(?:->|::)\s*(?:createCommand|query|rawQuery)\s*\(|(?P<mysqli>\bmysqli_query)\s*\(")


def _skip_php_string(text: str, index: int) -> tuple[int, bool]:
    """Consume the string literal at `text[index]` and report whether it interpolates.

    Only a double-quoted string interpolates in PHP, and only on an UNescaped `$` followed by
    a name or a `{`. A single-quoted string is consumed the same way but can never interpolate.
    Returns the index just past the closing quote.
    """
    quote = text[index]
    interpolates = False
    index += 1
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            return index + 1, interpolates
        if (
            quote == '"'
            and char == "$"
            and index + 1 < length
            and (text[index + 1].isalpha() or text[index + 1] in "_{")
        ):
            interpolates = True
        index += 1
    return index, interpolates


def _php_call_arguments(text: str, start: int) -> list[tuple[int, int]]:
    """The (begin, end) span of each top-level argument of the call opened before `start`.

    Commas inside strings, nested calls `(...)` and array literals `[...]` do not split an
    argument -- only a comma at the call's own depth does. This is what lets the SQL argument
    be judged apart from a trailing parameter array, where a concatenation like `'/' . $x`
    builds a BOUND VALUE and is perfectly safe.
    """
    length = len(text)
    index = start
    depth = 1
    begin = start
    spans: list[tuple[int, int]] = []
    while index < length:
        char = text[index]
        if char in "\"'":
            index, _ = _skip_php_string(text, index)
            continue
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
            if depth == 0:
                spans.append((begin, index))
                return spans
        elif char == "," and depth == 1:
            spans.append((begin, index))
            begin = index + 1
        index += 1
    spans.append((begin, index))
    return spans


def _php_sql_argument_is_dangerous(text: str, begin: int, end: int) -> bool:
    """Judge the ONE argument `text[begin:end]` that carries the SQL string.

    Two dangerous shapes, decided with real string-state rather than a regex:

      * a `$variable` INSIDE a double-quoted string -- PHP interpolates it before the query
        runs (`"... WHERE year = $year"`).
      * a string literal CONCATENATED with a variable (`"..." . $id`, `$id . "..."`) -- the
        mysqli / hand-quoted shape.

    Restricting the walk to the SQL argument is load-bearing, not tidiness: a `$` interpolated
    into, or concatenated inside, a LATER argument is building a bound parameter value, which
    is exactly the safe thing this rule steers toward -- judging it would flag the fix.
    """
    index = begin
    tokens: list[str] = []  # 'STR', 'VAR', 'DOT' -- only what concatenation needs
    while index < end:
        char = text[index]
        if char in "\"'":
            new_index, interpolates = _skip_php_string(text, index)
            if interpolates:
                return True
            tokens.append("STR")
            index = new_index
        elif char == "$":
            index += 1
            while index < end and (text[index].isalnum() or text[index] in "_[]'->"):
                index += 1
            tokens.append("VAR")
        elif char == ".":
            tokens.append("DOT")
            index += 1
        else:
            index += 1

    return any(
        middle == "DOT" and {left, right} == {"STR", "VAR"}
        for left, middle, right in zip(tokens, tokens[1:], tokens[2:], strict=False)
    )


def _php_line_is_dangerous(line: str) -> bool:
    """Whether any SQL sink on this PHP line receives an interpolated/concatenated SQL string."""
    for sink in PHP_SINK.finditer(line):
        arguments = _php_call_arguments(line, sink.end())
        sql_index = 1 if sink.group("mysqli") else 0
        if sql_index < len(arguments):
            begin, end = arguments[sql_index]
            if _php_sql_argument_is_dangerous(line, begin, end):
                return True
    return False


# ---------------------------------------------------------------------------------------
# C#. EF Core's raw-SQL escape hatches. The danger is the INTERPOLATED string form (`$"..."`,
# `$@"..."`, `@$"..."`) with an actual `{hole}`. `FromSqlInterpolated` is the SAFE sibling --
# it parameterises every hole -- and is deliberately not a sink here; `FromSqlRaw("constant")`
# has no `$` and no hole and does not match either.
# ---------------------------------------------------------------------------------------


CS_INTERP = re.compile(r'\b(?:FromSqlRaw|ExecuteSqlRaw)(?:Async)?\s*\(\s*(?:\$@?|@\$)"(?P<body>[^"]*)"')

# ---------------------------------------------------------------------------------------
# Python. A cursor `.execute` (or `.executemany`) or a Django `.raw`, receiving an f-string
# with a `{hole}`, or a string glued to a variable with `+`. The parameterised form --
# `.execute(sql, params)` with a plain first argument and a bound tuple/dict -- matches
# neither pattern, which is the whole point.
# ---------------------------------------------------------------------------------------

# `.execute(f"...")` / `.raw(f'...')`, any f-string prefix casing (`f`, `rf`, `fr`, `F`...).
PY_FSTRING = re.compile(
    r"""\.\s*(?:execute|executemany|raw)\s*\(\s*
        (?:[rR]?[fF]|[fF][rR]?)(?P<q>["'])(?P<body>.*?)(?P=q)
    """,
    re.VERBOSE,
)

# `.execute("SELECT ... " + var)` or `.execute(base + " WHERE ...")`. One side a string
# literal, the other an identifier. String `+` string (a split constant) does NOT match --
# the `+` must sit against a name, never another quote.
PY_CONCAT = re.compile(
    r"""\.\s*(?:execute|executemany|raw)\s*\(\s*
        (?:(?P<q1>["']).*?(?P=q1)\s*\+\s*[A-Za-z_]   # "str" + var
          |[A-Za-z_]\w*\s*\+\s*["'])                 # var + "str"
    """,
    re.VERBOSE,
)

# ---------------------------------------------------------------------------------------
# JS / TS. A `.query` or `.raw` sink handed a TEMPLATE LITERAL with a `${hole}`. The
# parameterised form -- `.query('... $1 ...', [var])` -- is a plain quoted string with a
# placeholder token and an array, and has no backtick, so it never matches. An escaped
# `\${` (a literal dollar-brace in a template) is excluded by the lookbehind.
# ---------------------------------------------------------------------------------------

JS_TEMPLATE = re.compile(r"\.\s*(?:query|raw)\s*\(\s*`[^`]*(?<!\\)\$\{")


def _has_placeholder(body: str) -> bool:
    """True when an interpolated/f-string body has a real `{hole}`.

    `{{` and `}}` are the ESCAPED literal braces in both C# interpolated strings and Python
    f-strings, so they are removed before the test -- otherwise `$"{{ ... }}"` (a literal
    brace, no interpolation) would read as a hole and the rule would fire on constant SQL.
    """
    return "{" in body.replace("{{", "").replace("}}", "")


def _line_is_dangerous(suffix: str, line: str) -> bool:
    """Whether this ONE text shows a SQL sink receiving an interpolated/concatenated string.

    Takes text rather than a line on purpose: `_statement_is_dangerous` hands it a line joined
    to its successor, so a sink wrapped away from its own argument is read as the single
    expression it is. Every pattern below anchors the literal immediately after the sink's
    `(`, with only `\\s*` between, which is what makes that join safe -- see the note there.
    """
    if suffix == ".php":
        return _php_line_is_dangerous(line)

    if suffix == ".cs":
        match = CS_INTERP.search(line)
        return bool(match and _has_placeholder(match.group("body")))

    if suffix == ".py":
        fstring = PY_FSTRING.search(line)
        if fstring and _has_placeholder(fstring.group("body")):
            return True
        return bool(PY_CONCAT.search(line))

    if suffix in SCRIPT_SUFFIXES:
        return bool(JS_TEMPLATE.search(line))

    return False


def _statement_is_dangerous(suffix: str, code_lines: list[tuple[int, str]], index: int) -> bool:
    """The same verdict, read across this line and the NEXT CODE LINE.

    WHY THE LOOKAHEAD EXISTS. A sink and its argument are one expression, and a long query is
    exactly what a formatter wraps:

        cursor.execute(
            f"SELECT * FROM t WHERE id = {user_id}"
        )

    fired on one line and was silent the moment `ruff format`, csharpier or php-cs-fixer moved
    the string down -- in all four dialects. Whether a security rule sees a defect was being
    decided by line length, which is nobody's decision at all. Measured 2026-09-09.

    This is NOT the assembled-into-a-variable case the module docstring rules out, and the
    difference is the whole justification: there is no value to follow here. The dangerous
    string is still the sink's own first argument, sitting inside its parentheses.

    ONE CODE LINE, exactly as `standards_tls` bounds the wrapped-assignment lookahead it added
    for the same reason. Two lines of separation is no longer one wrapped call in any
    formatter's output, and the next code line rather than the next raw line so an intervening
    comment cannot hide the argument.

    THE JOIN CANNOT INVENT A MATCH, which is the safety property the whole approach rests on:
    every pattern in this module anchors the string literal immediately after the sink's `(`
    with only `\\s*` between, so gluing a line to its neighbour can only ever complete a call
    that was already opened -- never bridge two unrelated statements. Pinned by a test.
    """
    line = code_lines[index][1]
    if _line_is_dangerous(suffix, line):
        return True

    if index + 1 >= len(code_lines):
        return False
    return _line_is_dangerous(suffix, f"{line} {code_lines[index + 1][1]}")


def check_sql_injection(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a variable interpolated or concatenated into an executed SQL string.

    Comments and docstrings are skipped by `iter_code_lines`, so the rule never reports the
    example in its own header. A line-scoped exemption silences a single line the regex
    misread. There is also a FILE-scoped exemption, used for exactly one thing: a file whose
    whole job is to hold these shapes as fixtures -- this rule's own test file -- the same
    escape client-address and query-shape carry for the same reason. It is deliberately not a
    config gate: injection is a defect, not a tunable convention a repo opts out of.
    """
    suffix = path.suffix
    if suffix not in (".php", ".cs", ".py", *SCRIPT_SUFFIXES):
        return

    # A file that IS a wall of fixtures (this rule's test file) exempts itself with one header
    # marker rather than a line marker on every case. The reason is printed on every run, so it
    # cannot hide silently, and it is the only file in the estate that legitimately needs it.
    if exemption_reason(lines, RULE) is not None:
        return

    # Materialised because the verdict reads one line ahead -- see `_statement_is_dangerous`.
    code_lines = list(iter_code_lines(lines, suffix))

    for index, (line_number, _) in enumerate(code_lines):
        if not _statement_is_dangerous(suffix, code_lines, index):
            continue
        if line_exemption_reason(lines, line_number - 1, RULE):
            continue

        yield Violation(
            path=path,
            line=line_number,
            rule=RULE,
            message=(
                "A variable is built into this SQL string, which is then executed. The value "
                "stops being DATA and becomes part of the COMMAND: a crafted input -- a quote, "
                "a semicolon, a trailing `--` -- rewrites the statement and can read or destroy "
                "the whole database. Escaping is not the fix; it is a denylist racing the SQL "
                "parser, and the parser wins. Pass the value as a bound PARAMETER instead so "
                "the statement and the data travel separately: FromSqlInterpolated (never "
                "FromSqlRaw) in EF Core, `execute(sql, params)` with a tuple in Python, a "
                "`:name`/`?` placeholder with bindValue/prepare in PHP, `$1` with a values "
                "array in node-postgres. If this really is a constant query the regex misread, "
                "exempt the line with a written reason saying why."
            ),
        )

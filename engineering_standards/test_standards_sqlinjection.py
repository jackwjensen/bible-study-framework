"""Cases for the sql-string-interpolation rule.

standards: sql-string-interpolation exempt -- every SQL string below is a fixture the detector
is supposed to catch, not a query this file runs; this is the rule's own test file, and it
carries the same file-scoped escape client-address and query-shape carry for the same reason.

The negatives carry the weight. This rule reads every .php, .cs, .py and JS-family line in
the estate, and calling a database is COMMON and almost always parameterised. A rule that
fired on `execute(sql, params)` or a `$1` placeholder would be exempted wholesale within a
week, and an exempted rule is a rule that is not running. Every parameterised negative below
is a shape that appears in real, correct code.

Run: python test_standards_sqlinjection.py   (or pytest)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_selftest import run_module_tests  # noqa: E402
from standards_sqlinjection import check_sql_injection  # noqa: E402

PHP = Path("app/models/Report.php")
CS = Path("Data/ReportRepository.cs")
PY = Path("app/reports.py")
TS = Path("src/reports.ts")


def found(path: Path, *lines: str) -> list[str]:
    return [v.message for v in check_sql_injection(path, list(lines))]


# ---- PHP positives --------------------------------------------------------------------------


def test_catches_the_yii_audit_shape() -> None:
    """Verbatim from the audit: a GET-controlled $year interpolated into a raw query."""
    assert found(
        PHP,
        '        ->createCommand("SELECT MONTH(created_at) months FROM t WHERE YEAR(created_at) = $year")',
    )


def test_catches_pdo_query_with_interpolation() -> None:
    assert found(PHP, '$pdo->query("SELECT * FROM users WHERE id = $id");')


def test_catches_rawquery_with_interpolation() -> None:
    assert found(PHP, "$db->rawQuery(\"DELETE FROM sessions WHERE token = '$token'\");")


def test_catches_braced_interpolation() -> None:
    """`{$var}` is the same interpolation wearing braces."""
    assert found(PHP, "$db->query(\"SELECT * FROM t WHERE name = '{$name}'\");")


def test_catches_mysqli_query_with_concatenation() -> None:
    assert found(PHP, 'mysqli_query($conn, "SELECT * FROM t WHERE name = \'" . $name . "\'");')


def test_catches_createcommand_with_concatenation() -> None:
    assert found(PHP, '$db->createCommand("SELECT * FROM t WHERE id = " . $id);')


# ---- PHP negatives --------------------------------------------------------------------------


def test_quiet_on_php_bound_placeholder() -> None:
    """A `:year` placeholder with no variable in the string is the fix this rule pushes to."""
    assert not found(PHP, '$db->createCommand("SELECT * FROM t WHERE year = :year")->bindValue(":year", $year);')


def test_quiet_on_php_question_mark_placeholder() -> None:
    assert not found(PHP, '$pdo->query("SELECT * FROM users WHERE id = ?", [$id]);')


def test_quiet_on_single_quoted_string_which_php_does_not_interpolate() -> None:
    """PHP never interpolates single-quoted strings, so `$id` here is a literal, not a hole."""
    assert not found(PHP, "$db->query('SELECT * FROM t WHERE id = $id');")


def test_quiet_on_escaped_dollar_in_php() -> None:
    """`\\$total` renders as a literal dollar sign, not an interpolation."""
    assert not found(PHP, "$db->query(\"SELECT 'Price: \\$total' AS label FROM t\");")


def test_quiet_on_php_constant_query() -> None:
    assert not found(PHP, '$db->createCommand("SELECT COUNT(*) FROM users")->queryScalar();')


def test_quiet_on_variable_between_two_key_strings_in_params_array() -> None:
    """The real estate false positive: a `$` sitting BETWEEN two array-key strings is not
    inside a string, and a regex that pairs quotes greedily read it as interpolation."""
    assert not found(
        PHP,
        '$db->query("INSERT INTO t (a, b) VALUES (:a, :b)", ["a" => $values[\'a\'], "b" => $values[\'b\']]);',
    )


def test_quiet_on_concatenation_that_builds_a_bound_value() -> None:
    """`'/' . $old_url . '/'` builds the VALUE for a bound `:old_url`, not the SQL. The
    concatenation is in the params array, so the query itself is parameterised and safe."""
    assert not found(
        PHP,
        "$db->query(\"INSERT INTO redirects SET old_url = :old_url\", [\"old_url\" => '/' . $old_url . '/']);",
    )


def test_quiet_on_mysqli_query_with_parameterised_second_argument() -> None:
    """mysqli's SQL is the SECOND argument; a `$conn` in the first must not be read as SQL."""
    assert not found(PHP, 'mysqli_query($conn, "SELECT * FROM users WHERE active = 1");')


# ---- C# positives ---------------------------------------------------------------------------


def test_catches_fromsqlraw_interpolated() -> None:
    assert found(CS, 'var rows = _db.Reports.FromSqlRaw($"SELECT * FROM Reports WHERE Year = {year}");')


def test_catches_executesqlraw_interpolated() -> None:
    assert found(CS, 'await _db.Database.ExecuteSqlRawAsync($"DELETE FROM Logs WHERE Id = {id}");')


def test_catches_verbatim_interpolated_string() -> None:
    assert found(CS, "var q = _db.Users.FromSqlRaw($@\"SELECT * FROM Users WHERE Name = '{name}'\");")


# ---- C# negatives ---------------------------------------------------------------------------


def test_quiet_on_fromsqlinterpolated() -> None:
    """The SAFE sibling: FromSqlInterpolated parameterises every hole. Flagging it would be
    flagging the fix."""
    assert not found(CS, 'var rows = _db.Reports.FromSqlInterpolated($"SELECT * FROM Reports WHERE Year = {year}");')


def test_quiet_on_fromsqlraw_constant() -> None:
    assert not found(CS, 'var rows = _db.Reports.FromSqlRaw("SELECT * FROM Reports");')


def test_quiet_on_fromsqlraw_with_dbparameter() -> None:
    """A non-interpolated string plus a SqlParameter argument is the parameterised raw form."""
    assert not found(CS, 'var rows = _db.Reports.FromSqlRaw("SELECT * FROM Reports WHERE Year = {0}", yearParam);')


def test_quiet_on_escaped_braces_in_interpolated_string() -> None:
    """`{{` / `}}` are literal braces, not holes."""
    assert not found(CS, "var q = _db.Reports.FromSqlRaw($\"SELECT '{{literal}}' FROM Reports\");")


# ---- Python positives -----------------------------------------------------------------------


def test_catches_execute_fstring() -> None:
    assert found(PY, 'cursor.execute(f"SELECT * FROM reports WHERE year = {year}")')


def test_catches_django_raw_fstring() -> None:
    assert found(PY, 'qs = Report.objects.raw(f"SELECT * FROM reports WHERE id = {report_id}")')


def test_catches_execute_concatenation() -> None:
    assert found(PY, 'cursor.execute("SELECT * FROM reports WHERE name = \'" + name + "\'")')


# ---- Python negatives -----------------------------------------------------------------------


def test_quiet_on_execute_with_params_tuple() -> None:
    """The textbook parameterised call: placeholder in the SQL, values in a tuple."""
    assert not found(PY, 'cursor.execute("SELECT * FROM reports WHERE year = %s", (year,))')


def test_quiet_on_execute_with_named_params() -> None:
    assert not found(PY, 'cursor.execute("SELECT * FROM reports WHERE year = %(year)s", {"year": year})')


def test_quiet_on_execute_constant_string() -> None:
    assert not found(PY, 'cursor.execute("SELECT COUNT(*) FROM reports")')


def test_quiet_on_split_constant_string() -> None:
    """`"a" + "b"` is a constant assembled from two literals -- no variable, no injection."""
    assert not found(PY, 'cursor.execute("SELECT id, name " + "FROM reports")')


# ---- JS / TS positives ----------------------------------------------------------------------


def test_catches_query_template_literal() -> None:
    assert found(TS, "const rows = await pool.query(`SELECT * FROM reports WHERE year = ${year}`);")


def test_catches_knex_raw_template_literal() -> None:
    assert found(TS, "await knex.raw(`UPDATE reports SET seen = true WHERE id = ${id}`);")


# ---- JS / TS negatives ----------------------------------------------------------------------


def test_quiet_on_parameterised_query() -> None:
    """`$1` is a bind placeholder, and the values ride in the array -- never spliced in."""
    assert not found(TS, "const rows = await pool.query('SELECT * FROM reports WHERE year = $1', [year]);")


def test_quiet_on_constant_template_literal() -> None:
    """A template literal with no `${}` interpolates nothing."""
    assert not found(TS, "const rows = await pool.query(`SELECT * FROM reports`);")


# ---- message + exemption --------------------------------------------------------------------


def test_the_message_names_the_parameterised_alternative() -> None:
    message = found(PY, 'cursor.execute(f"SELECT * FROM reports WHERE year = {year}")')[0]
    assert "FromSqlInterpolated" in message and "parameter" in message.casefold()


def test_it_reports_the_right_line() -> None:
    violations = list(
        check_sql_injection(
            PY,
            ["# nothing", "# still nothing", 'cursor.execute(f"SELECT * FROM t WHERE id = {id}")'],
        )
    )
    assert [v.line for v in violations] == [3]


def test_a_line_exemption_silences_it() -> None:
    assert not found(
        PY,
        "# standards: sql-string-interpolation exempt -- the interpolated value is a "
        "hard-coded table name from an internal enum, never user input.",
        'cursor.execute(f"SELECT * FROM {table_name} LIMIT 10")',
    )


def test_a_comment_is_not_scanned() -> None:
    """iter_code_lines skips comments, so the rule never reports its own illustrations."""
    assert not found(PY, '# cursor.execute(f"SELECT * FROM t WHERE id = {id}") -- do not do this')


def test_a_file_scoped_exemption_silences_the_whole_file() -> None:
    """One header marker exempts a file that is nothing but fixtures -- this test file itself.
    Without it, every positive case above would trip the pack's own self-scan."""
    header = (
        "# standards: sql-string-interpolation exempt -- this whole module is nothing but "
        "detector fixtures, never queries it runs"
    )
    assert not found(PY, header, 'cursor.execute(f"SELECT * FROM t WHERE id = {id}")')


# ---- a sink wrapped away from its own argument ----------------------------------------------
#
# ONE EXPRESSION, NOT AN ASSEMBLED ONE. The dangerous string is still the sink's own first
# argument; only line-length formatting sits between them, and a formatter chose that, not the
# author. A real query is long, so this is the shape a long query takes. Distinct from the
# assembled-into-a-variable case below, which stays a deliberate non-goal because telling it
# from a parameterised assembly needs the value followed.


def test_catches_a_python_fstring_under_a_wrapped_sink() -> None:
    assert found(PY, "cursor.execute(", '    f"SELECT * FROM t WHERE id = {user_id}"', ")")


def test_catches_python_concatenation_under_a_wrapped_sink() -> None:
    assert found(PY, "cursor.execute(", '    "SELECT * FROM t WHERE id = " + user_id', ")")


def test_catches_a_csharp_interpolation_under_a_wrapped_sink() -> None:
    assert found(CS, "var rows = context.Reports.FromSqlRaw(", '    $"SELECT * FROM t WHERE id = {id}");')


def test_catches_a_php_interpolation_under_a_wrapped_sink() -> None:
    assert found(PHP, "$rows = $db->createCommand(", '    "SELECT * FROM t WHERE id = $id"', ")->queryAll();")


def test_catches_a_template_literal_under_a_wrapped_sink() -> None:
    assert found(TS, "const rows = await pool.query(", "    `SELECT * FROM t WHERE id = ${userId}`", ");")


def test_the_finding_is_reported_on_the_sink_line() -> None:
    """Where the defect is fixed is where the sink is -- and a reader scanning for
    `execute(` finds nothing useful pointed at a bare string literal."""
    violations = list(check_sql_injection(PY, ["cursor.execute(", '    f"SELECT * FROM t WHERE id = {user_id}"', ")"]))

    assert [v.line for v in violations] == [1]


def test_a_one_line_sink_is_still_reported_exactly_once() -> None:
    """The join must not double-report a line that already matched on its own."""
    violations = list(check_sql_injection(PY, ['cursor.execute(f"SELECT * FROM t WHERE id = {id}")', "return rows"]))

    assert len(violations) == 1


# ---- what the lookahead must NOT do ---------------------------------------------------------


def test_a_parameterised_wrapped_call_stays_silent() -> None:
    """The negative that decides whether this rule survives contact with real code: wrapping
    is how every long parameterised call is written too."""
    assert not found(PY, "cursor.execute(", '    "SELECT * FROM t WHERE id = %s",', "    (user_id,),", ")")


def test_two_unrelated_neighbouring_lines_do_not_join_into_a_finding() -> None:
    """Every pattern anchors the literal immediately after the sink's `(`, so joining a line
    to its neighbour cannot invent a match. Pinned, because that property is the whole
    safety argument for looking ahead at all."""
    assert not found(PY, "cursor.execute(query, params)", '    label = f"ran {query}"')


def test_a_query_assembled_into_a_variable_stays_a_non_goal() -> None:
    """DELIBERATE, not an oversight. Telling this from a safe assembly means following the
    value, and the module docstring's argument stands: a rule that fires on good code gets
    switched off. Pinned so the boundary is visible rather than remembered."""
    assert not found(
        PY,
        "query = (",
        '    "SELECT * FROM t "',
        '    f"WHERE id = {user_id}"',
        ")",
        "cursor.execute(query)",
    )


def test_the_lookahead_reaches_one_code_line_only() -> None:
    """Bounded exactly as `standards_tls` bounds its wrapped-assignment lookahead. Two lines
    of separation is no longer one wrapped call in any formatter's output."""
    assert not found(PY, "cursor.execute(", "    connection_options,", '    f"SELECT * FROM t WHERE id = {id}"', ")")


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "sql-string-interpolation cases"))

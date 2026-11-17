#!/usr/bin/env python3
"""The testing dimension: code that nothing exercises, and tests that cannot fail.

    tests-uncovered-module   a source module no test names
    test-without-assertion   a test function that asserts nothing
    test-always-passes       an assertion whose operands are all constant
    test-silently-skipped    a test disabled with no stated reason

standards: test-without-assertion exempt -- the strings in this module are the patterns it
detects, quoted so the detector can be written against them; nothing here is a test.

WHY THIS EXISTS AT ALL. `documentation.md` has opened with "Code, tests, documentation. The
first two are gated" since it was written, and tests were not gated by anything -- no rule,
no rules file, nothing. The pack asserted a gate it did not have, which is the worst of both:
the claim discourages anybody from noticing, and nothing enforces it.

WHY IT DOES NOT GRANDFATHER, which is the design decision worth defending. The first draft
of this rule was a change-ratchet -- a branch that touches source must touch a test -- chosen
because it produces no findings on day one. Jack rejected that on 2026-09-01, and the reason
generalises past this rule: designing a requirement so that it never disturbs existing code
is how a requirement becomes decorative. Every escape built into the rule is an escape
somebody takes. So this fires on every uncovered module today, and adoption relief lives
where it belongs -- in the baseline, a separate, visible, opt-in decision that repos which
want a ramp can take and Jack's repos do not.

The exclusions below are therefore not relief. Each is a shape with genuinely nothing to
run: a declarative table defines no behaviour, a vendored file is somebody else's code, a
migration is verified against a real schema rather than by a unit test, and a test is not
its own subject. If an exclusion ever reads as "we would rather not disturb this", it is
wrong and should be deleted.

WHAT "COVERED" MEANS, and its honest limit. The test corpus must name the module -- by its
stem, or by a symbol it defines. That is the `docs-uncovered-command` bargain: naming is a
weak proxy for exercising, and it is the strongest thing a scanner can decide without
running the suite. It catches the case that actually bites -- a module nothing has ever
looked at -- and it cannot tell a thorough suite from a shallow one. The quality rules below
cover the other end (a test that cannot fail); everything between them is `/code-review`'s.

THE FILENAME CLAIM IS SCOPED TO ITS PACKAGE (2026-09-03). A stem is a weak name and many
stems repeat: `admin`, `models`, `views`, `tasks`, `schema` and `serializers` exist in every
app of a Django project. Matching them repo-wide meant the first app to grow a
`test_admin.py` marked `admin.py` covered EVERYWHERE -- measured in allegro-it-services,
where it silenced a 488-line module that no test named and no test exercised. A filename
claim now reaches only the package the test sits in (`_filename_claim_scope`); a repo whose
tests all live in one root tree is unaffected, because its scope is the repo root. Symbol
matching stays repo-wide: a symbol name is distinctive enough to be evidence wherever it
appears, and scoping it would break the normal way a monorepo tests a shared package.

A PACKAGE IS NOT ALWAYS A PARENT (2026-09-28). .NET and JVM solutions express the same
relationship as a SIBLING -- `InvoTrack.Tests/` beside `InvoTrack/`, never inside it -- so the
scoping above gave those test projects a scope containing no production modules, and filename
claims covered nothing whatsoever. Measured in InvoTrack: 44 modules reported uncovered in a
repo with 191 passing tests. The tell was `AssetVersion.cs`, which stayed reported even after a
thorough `AssetVersionTests.cs` named it four times -- its only public surface is a property,
so it defines no symbol the corpus could name either, leaving it uncoverable by any test that
could be written. `_filename_claim_scope` now maps `<Project>.Tests` to `<Project>` when that
sibling exists, and `_subjects_of_test_filename` reads the separator-less `AssetVersionTests`
spelling. Both halves are needed: either alone still covers nothing.

Source of truth: engineering-standards/engineering_standards/standards_tests.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import CheckConfig, Violation
from standards_exemptions import exemption_reason, line_exemption_reason
from standards_markdown import SKIP_DIRECTORIES, read_lines
from standards_tree import walk_repository

TESTS_UNCOVERED_MODULE_RULE = "tests-uncovered-module"
TEST_WITHOUT_ASSERTION_RULE = "test-without-assertion"
TEST_ALWAYS_PASSES_RULE = "test-always-passes"
TEST_SILENTLY_SKIPPED_RULE = "test-silently-skipped"

SOURCE_SUFFIXES = frozenset({".py", ".cs", ".php", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})

# Directories whose contents are verified some other way, or are not ours. Deliberately
# short: every entry is a claim that there is nothing to run, not that it would be awkward.
UNTESTABLE_DIRECTORIES = frozenset({"migrations", "Migrations"})

TEST_FILENAME = re.compile(r"(?i)^(?:test[-_].+|.+[-_]test|.+\.test|.+\.spec|test|.+tests?)$")
TEST_DIRECTORIES = frozenset({"test", "tests", "spec", "specs", "__tests__"})

# A test PROJECT directory, which is how .NET and JVM solutions lay tests out: `InvoTrack.Tests`
# beside `InvoTrack`, rather than `tests/` inside it. It is the same relationship as
# TEST_DIRECTORIES expresses, spelled as a sibling instead of a child, so it scopes the same way
# -- see `_filename_claim_scope`.
TEST_PROJECT_DIRECTORY = re.compile(r"(?i)^(?P<subject>.+)\.(?:unit|integration|functional)?tests?$")

# A definition of anything. A file with none of these is declarative -- a returned table, a
# constant list, a schema -- and has no behaviour a test could pin.
DEFINITION = re.compile(
    r"""(?x)
    ^\s* (?: export \s+ )? (?: default \s+ )? (?: async \s+ )?
    (?: def | function | class | interface | trait | enum | record | struct )
    \s+ (?P<name>[A-Za-z_$][\w$]*)
  | ^\s* (?: export \s+ )? (?: const | let | var )
    \s+ (?P<binding>[A-Za-z_$][\w$]*) \s* = \s* (?: async \s* )? (?: \( | function )
  | ^\s* (?: public | private | protected | internal | static | abstract | override | virtual )
    [\w\s<>,\[\]?]*? \b (?P<member>[A-Z][\w]*) \s* \(
    """
)

# A test function, in each language's spelling: pytest/unittest naming, and the xUnit-style
# `it(...)` / `test(...)` callback used by every JS runner.
TEST_FUNCTION = re.compile(
    r"""(?x)
    ^\s* (?: async \s+ )? def \s+ (?P<py>test_[\w]*) \s* \(
  | ^\s* (?: it | test ) \s* \( \s* (?P<js>['"])
  | ^\s* \[ (?: Fact | Theory ) \]
    """
)

ASSERTION = re.compile(
    r"""(?xi)
    \b assert \b
    # `assertEqual(`, `assertTrue(`, `assert_called_once_with(`, `assert_not_called(`.
    # `\b assert \b` above cannot see any of them: the boundary it demands after "assert"
    # is not there when the next character is a letter or an underscore, so unittest's and
    # mock's entire vocabularies read as "this test asserts nothing". Between them they are
    # most of the assertions in a Django codebase -- 18 of the 20 findings in
    # allegro-it-services on 2026-09-03 were one of these two forms, every one a test that
    # did assert something.
  | \b assert \w* \s* \(
  | \b expect \s* \(
  | \b should \b
  | \b Assert \. \w+
  | \b verify \s* \(
  | \b check \s* \(
  | \b raises \b
  | \b throws \b
    """
)

# Both operands constant: `assert True`, `assertTrue(True)`, `expect(true).toBe(true)`,
# `assert 1 == 1`. A comparison with one real operand -- `assert flag() is True` -- is a
# genuine check and is deliberately not matched.
ALWAYS_PASSES = re.compile(
    r"""(?xi)
    ^\s* assert \s+ (?: True | 1 ) \s* $
  | ^\s* assert \s+ (?P<a>-?\d+|True|False|'[^']*'|"[^"]*") \s* == \s* (?P=a) \s* $
  | assertTrue \s* \( \s* True \s* \)
  | expect \s* \( \s* (?P<b>true|false|-?\d+) \s* \) \s* \. \s*
        (?: toBe | toEqual | toStrictEqual ) \s* \( \s* (?P=b) \s* \)
    """
)

# Disabled, with nothing said about why. A reason in the call (`reason=`, a string argument)
# or on the line above makes it a decision rather than an abandonment.
SKIPPED = re.compile(
    r"""(?x)
    @ (?: pytest \. mark \. )? (?: skip | xfail ) \b (?P<pyargs> \( [^)]* \) )?
  | \b (?: it | test | describe ) \s* \. \s* (?: skip | todo ) \s* \(
  | \[ \s* (?: Ignore | Skip ) \s* (?P<csargs> \( [^)]* \) )?
    """
)

REASONISH = re.compile(r"""(?i)reason\s*=|['"][^'"]{15,}['"]""")

COMMENT_LINE = re.compile(r"^\s*(//|#|\*|/\*|--)")


def _is_test_path(path: Path, repo_root: Path) -> bool:
    relative = path.relative_to(repo_root)
    if any(part in TEST_DIRECTORIES for part in relative.parts[:-1]):
        return True
    return bool(TEST_FILENAME.match(path.stem))


def _filename_claim_scope(path: Path) -> Path:
    """The directory within which a test's FILENAME claim counts.

    A filename claim is weak evidence -- `test_admin.py` says somebody wrote a test called
    "admin", not which `admin.py` they meant -- so it is scoped to the package the test sits
    in rather than to the whole repository. The scope is the test's own directory with any
    enclosing test directories stripped: `apps/legal/tests/test_admin.py` claims `admin`
    within `apps/legal`, and `src/core/utils/__tests__/textOr.spec.ts` claims `textOr` within
    `src/core/utils`.

    WHY, measured in allegro-it-services on 2026-09-03: adding `apps/legal/tests/test_admin.py`
    marked `admin.py` covered in EVERY app at once, including a 488-line `dna_wobbler/admin.py`
    that no test named and no test exercised. `admin`, `models`, `views`, `tasks`, `schema`
    and `serializers` repeat in every app of a Django project, so a repo-wide stem match means
    the first app to get a test silences all the others.

    A repo whose tests all live in one root `tests/` tree scopes to the repo root and so keeps
    the old, permissive behaviour -- the narrowing only bites where tests sit inside packages,
    which is where the collision was.

    A .NET or JVM solution expresses the same relationship as a SIBLING rather than a child:
    `InvoTrack.Tests/` beside `InvoTrack/`, never inside it. Without the sibling step below its
    scope was itself -- a directory holding no production modules -- so filename claims covered
    nothing at all. Measured in InvoTrack on 2026-09-28: 44 modules reported uncovered in a repo
    with 191 passing tests, and `AssetVersion.cs` stayed reported even with a thorough
    `AssetVersionTests.cs` naming it four times, because its only public surface is a property
    and so it defines no symbol the corpus could name either. The sibling must exist: a
    `Standalone.Tests/` next to no `Standalone/` claims only itself, so a stray name cannot
    silently widen the scope to the whole repository.
    """
    scope = path.parent
    while scope.name in TEST_DIRECTORIES:
        scope = scope.parent

    match = TEST_PROJECT_DIRECTORY.match(scope.name)
    if match:
        subject = scope.parent / match.group("subject")
        if subject.is_dir():
            return subject
    return scope


def _claims_cover(module: Path, claims: dict[str, list[Path]]) -> bool:
    """Whether some test filename claims this module's stem from a directory above it."""
    directory = module.parent
    return any(scope == directory or scope in directory.parents for scope in claims.get(module.stem, ()))


def _subjects_of_test_filename(stem: str) -> set[str]:
    """The module stems a test filename claims to cover, by the usual conventions.

    `test_content` / `content_test` / `content.test` / `content.spec` all name `content`.
    Returned as whole stems and compared as whole stems, so a test for `content` never
    silences `contents`.
    """
    subjects = {stem}
    for prefix in ("test_", "test-"):
        if stem.startswith(prefix):
            subjects.add(stem[len(prefix) :])
    for suffix in ("_test", "-test", ".test", ".spec"):
        if stem.endswith(suffix):
            subjects.add(stem[: -len(suffix)])

    # The .NET and JVM spelling has no separator: `AssetVersionTests` names `AssetVersion`.
    # Matched case-SENSITIVELY on a capital T, which is what keeps it from mangling the
    # snake_case convention above -- `standards_tests` must keep claiming `standards_tests`,
    # not `standards_`.
    for suffix in ("Tests", "Test"):
        if stem.endswith(suffix) and len(stem) > len(suffix):
            subjects.add(stem[: -len(suffix)])
    return subjects


def _defined_symbols(lines: list[str]) -> set[str]:
    """Every symbol this file defines. Empty means the file is declarative."""
    names: set[str] = set()
    for line in lines:
        if COMMENT_LINE.match(line):
            continue
        match = DEFINITION.match(line)
        if match:
            names.add(next(group for group in match.groups() if group))
    return names


def _collect_modules_and_suite(
    repo_root: Path,
) -> tuple[list[tuple[Path, set[str], list[str]]], list[str], dict[str, list[Path]]]:
    """One walk: the modules that could be tested, and everything the suite says.

    Split from the reporting half so each is readable on its own -- collection is a
    tree walk, the report is a judgment per module, and they share only the result.
    """
    modules: list[tuple[Path, set[str], list[str]]] = []
    corpus: list[str] = []
    # The suite's FILENAMES, matched as whole stems and kept WITH THE DIRECTORY each claim
    # was made from. `test_content.py` covers `content.py` without ever spelling the name in
    # its body -- the commonest convention there is, and one a contents-only corpus misses.
    # Whole stems, so `test_content` does not also silence `contents.py`; scoped by directory,
    # so one app's `test_admin.py` does not silence every other app's `admin.py`.
    named_by_filename: dict[str, list[Path]] = {}

    for directory, subdirectories, names in walk_repository(repo_root):
        subdirectories[:] = [
            name for name in subdirectories if name not in SKIP_DIRECTORIES and name not in UNTESTABLE_DIRECTORIES
        ]
        base = Path(directory)
        for name in names:
            path = base / name
            if path.suffix not in SOURCE_SUFFIXES:
                continue
            lines = read_lines(path)
            if _is_test_path(path, repo_root):
                corpus.append("\n".join(lines))
                scope = _filename_claim_scope(path)
                for subject in _subjects_of_test_filename(path.stem):
                    named_by_filename.setdefault(subject, []).append(scope)
                continue
            symbols = _defined_symbols(lines)
            if symbols:
                modules.append((path, symbols, lines))

    return modules, corpus, named_by_filename


def check_tests_cover_modules(repo_root: Path, config: CheckConfig) -> Iterable[Violation]:
    """Every source module in the tree that no test names.

    Repo-level, so it runs on a whole-tree scan only: the answer lives in a different file
    from the subject, and a staged subset cannot see the suite. It also means the commit
    that ADDS a test is never the commit this blocks -- which is a property of the scope,
    not an escape hatch, since the push still refuses until the test exists.
    """
    if not config.check_tests:
        return

    modules, corpus, named_by_filename = _collect_modules_and_suite(repo_root)
    text = "\n".join(corpus)
    for path, symbols, lines in modules:
        # Two signals, both requiring that somebody actually looked at this module: a test
        # FILENAME naming it, or a SYMBOL it defines appearing in the suite.
        #
        # A BARE MODULE NAME IN THE CORPUS IS DELIBERATELY NOT ONE. It was, and measuring the
        # rule against allegroit-dk on 2026-09-01 showed why not: the pack syncs its own
        # scanner suites into every consuming repo, and their fixtures use `router`, `mailer`
        # and `i18n` as sample names -- so four real, wholly untested modules read as covered
        # because of test data belonging to a different repo. Substring matching compounded
        # it (`content` matched inside `content-type`). Both signals below need a human to
        # have named this module's own filename or its own functions.
        # The filename claim is scoped to the package the test sits in: `test_admin.py` in one
        # app says nothing about another app's `admin.py`. See `_filename_claim_scope`.
        if _claims_cover(path, named_by_filename):
            continue
        # Short names are not evidence. `i18n.php` defines `t()`, the translation helper, and
        # a one- or two-character name matches inside ordinary prose in any suite -- so it
        # would mark the module covered because some unrelated test used the letter. Short
        # names belong to exactly the helpers called everywhere and tested nowhere.
        if any(len(symbol) > 2 and re.search(rf"\b{re.escape(symbol)}\b", text) for symbol in symbols):
            continue
        if exemption_reason(lines, TESTS_UNCOVERED_MODULE_RULE) is not None:
            continue
        yield Violation(
            path=path,
            line=1,
            rule=TESTS_UNCOVERED_MODULE_RULE,
            message=(
                f"no test names '{path.stem}' or anything it defines. Code, tests and "
                "documentation are the three gated dimensions, and this is the test one: a "
                "module nothing exercises is a module whose behaviour nobody has stated, so "
                "every change to it is unreviewable against intent and every regression in "
                "it is silent. Name it from a test -- by the module, or by a symbol it "
                "defines. If it genuinely has nothing to run (a declarative table defines "
                "no behaviour and is already skipped), say which: 'standards: "
                f"{TESTS_UNCOVERED_MODULE_RULE} exempt -- <why>' in its header. Adoption "
                "relief is the baseline's job, not an exemption's."
            ),
        )


def check_test_quality(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Tests that cannot fail: no assertion, a constant one, or quietly disabled.

    Runs on TEST FILES ONLY -- a print-only function in application code is not this rule's
    business, and checking production source for assertions would be nonsense.

    This is the other end of the same problem the coverage rule opens. Coverage asks whether
    anything looks at a module; this asks whether what looks at it can ever say no. A suite
    of assertion-free tests reports the module as covered and is worse than no suite, because
    it also reports it as working.
    """
    if path.suffix not in SOURCE_SUFFIXES:
        return
    if not (TEST_FILENAME.match(path.stem) or any(part in TEST_DIRECTORIES for part in path.parts[:-1])):
        return

    for index, line in enumerate(lines):
        if COMMENT_LINE.match(line):
            continue

        if ALWAYS_PASSES.search(line):
            yield from _finding(
                path,
                index,
                lines,
                TEST_ALWAYS_PASSES_RULE,
                "every operand in this assertion is a constant, so it passes whatever the "
                "code does. It is not a placeholder that will be filled in later -- it is a "
                "green tick reporting that something was checked when nothing was.",
            )
            continue

        skipped = SKIPPED.search(line)
        if skipped:
            stated = REASONISH.search(skipped.group(0)) or (
                index > 0 and COMMENT_LINE.match(lines[index - 1]) and len(lines[index - 1]) > 20
            )
            if not stated:
                yield from _finding(
                    path,
                    index,
                    lines,
                    TEST_SILENTLY_SKIPPED_RULE,
                    "this test is disabled and says nothing about why. A skip with a reason "
                    "is a decision somebody can revisit; a bare one is a test that was "
                    "failing, and the failure is still there.",
                )
            continue

        if TEST_FUNCTION.match(line) and not _asserts_within(lines, index):
            yield from _finding(
                path,
                index,
                lines,
                TEST_WITHOUT_ASSERTION_RULE,
                "this test asserts nothing, so it passes unless the code under it raises. It "
                "counts as coverage and checks no behaviour -- name what must be true, or "
                "delete it.",
            )


def _asserts_within(lines: list[str], start: int) -> bool:
    """Does the body following this test's signature contain any assertion?

    Bounded by the next test's signature rather than by indentation, so it reads the same in
    Python, PHP and a JS callback without three dialects of block parsing.
    """
    for index in range(start + 1, len(lines)):
        if TEST_FUNCTION.match(lines[index]):
            return False
        if COMMENT_LINE.match(lines[index]):
            continue
        if ASSERTION.search(lines[index]):
            return True
    return False


def _finding(path: Path, index: int, lines: list[str], rule: str, advice: str) -> Iterable[Violation]:
    if exemption_reason(lines, rule) is not None:
        return
    if line_exemption_reason(lines, index, rule) is not None:
        return
    yield Violation(
        path=path,
        line=index + 1,
        rule=rule,
        message=f"{advice} Exempt with a stated reason if this is genuinely right: "
        f"'standards: {rule} exempt -- <why>'.",
    )

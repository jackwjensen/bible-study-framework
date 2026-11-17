#!/usr/bin/env python3
"""Cases for the testing dimension: `tests-uncovered-module` and the test-quality family.

Both halves, per docs/changing-a-rule.md. The firing cases answer "does the signal fire";
the quiet cases answer whether the rule is usable at all.

NOTE ON THE QUIET CASES. Every one of them is a shape that genuinely has nothing to test --
a declarative table, a vendored file, the test itself. None of them is "an existing file we
would rather not disturb": adoption relief is the baseline's job, not the rule's, and a rule
that grandfathers by construction is a rule nobody ever has to satisfy.

standards: test-without-assertion exempt -- the fixtures below are assertion-free test
sources quoted so the detector can be run against them; this file's own checks all assert.

standards: test-always-passes exempt -- likewise: the constant assertions below are the
pattern table this detector is written against, never checks this file relies on.

standards: test-silently-skipped exempt -- likewise, for the disabled-test fixtures; nothing
in this suite is skipped.

Run: pytest test_standards_tests.py
"""

from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import CheckConfig, Violation  # noqa: E402
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402
from standards_tests import (  # noqa: E402
    TEST_ALWAYS_PASSES_RULE,
    TEST_SILENTLY_SKIPPED_RULE,
    TEST_WITHOUT_ASSERTION_RULE,
    TESTS_UNCOVERED_MODULE_RULE,
    check_test_quality,
    check_tests_cover_modules,
)

EXEMPT_REASON = "a generated client; the generator's own suite covers every branch here"
assert len(EXEMPT_REASON) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"

# FIXTURE NAMES ARE DELIBERATELY UNMISTAKABLE. This file is synced into every consuming
# repo, so a realistic fixture name becomes a phantom coverage signal there. The first
# version used ordinary names for a page loader and a mail sender, and thereby marked two
# real, wholly untested modules in allegroit-dk as covered. The prose here avoids spelling
# those names for the same reason: a COMMENT in this file is corpus too, so an example
# written out longhand re-creates the collision it is describing.
MODULE = "<?php\nfunction fixture_render_thing(string $slug): ?array {\n    return find($slug);\n}\n"
PY_MODULE = "def fixture_render_thing(slug):\n    return find(slug)\n"
# Defines a symbol, so it is not skipped as declarative -- but the .NET tests below name none
# of them, so only the FILENAME claim can cover it. That is what makes them test the scoping.
MODULE_CS = "public static class Fixturemod\n{\n    public static int Render(string slug) => 1;\n}\n"


@contextmanager
def repo() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tree:
        yield Path(tree)


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def covered(root: Path, config: CheckConfig | None = None) -> list[Violation]:
    return list(check_tests_cover_modules(root, config or CheckConfig()))


def quality(source: str, name: str) -> list[str]:
    return [v.rule for v in check_test_quality(Path(name), source.splitlines())]


# ---- tests-uncovered-module: it fires ---------------------------------------------------


def test_a_module_no_test_names_fires():
    with repo() as root:
        write(root, "lib/fixturemod.php", MODULE)
        found = covered(root)
        assert [v.rule for v in found] == [TESTS_UNCOVERED_MODULE_RULE]
        assert found[0].path.name == "fixturemod.php"


def test_a_repo_with_no_tests_at_all_reports_every_module():
    """No grandfathering. A repo that has never tested anything is told so, per module."""
    with repo() as root:
        write(root, "lib/fixturemod.php", MODULE)
        write(root, "lib/fixturesend.php", "<?php\nfunction fixture_deliver(string $to): bool { return true; }\n")
        write(
            root, "lib/fixtureroute.php", "<?php\nfunction fixture_dispatch(string $path): string { return $path; }\n"
        )
        assert len(covered(root)) == 3


def test_a_suite_that_names_other_modules_still_fires_for_this_one():
    with repo() as root:
        write(root, "lib/fixturemod.php", MODULE)
        write(root, "lib/fixturesend.php", "<?php\nfunction fixture_deliver(string $to): bool { return true; }\n")
        write(root, "tests/FixtureSendTest.php", "<?php\nassert(fixture_deliver('a@b.c') === true);\n")
        found = covered(root)
        assert [v.path.name for v in found] == ["fixturemod.php"]


def test_naming_the_module_only_in_docs_does_not_count():
    with repo() as root:
        write(root, "lib/fixturemod.php", MODULE)
        write(root, "docs/architecture.md", "`fixture_render_thing()` lives in `lib/fixturemod.php`.\n")
        assert len(covered(root)) == 1


# ---- tests-uncovered-module: it stays quiet ---------------------------------------------


def test_a_module_named_by_its_stem_is_covered():
    with repo() as root:
        write(root, "lib/fixturemod.php", MODULE)
        write(root, "tests/fixturemod_test.php", "<?php\n// exercises fixturemod.php end to end\n")
        assert covered(root) == []


def test_the_bare_module_name_appearing_in_unrelated_prose_does_not_cover_it():
    """Measured against allegroit-dk on 2026-09-01, and it was THIS suite doing the damage.

    The first version accepted the module's stem appearing anywhere in the corpus. The pack
    syncs its own scanner suites into every consuming repo, and the fixtures in this very
    file used ordinary module names, so four real, wholly untested modules in allegroit-dk
    read as covered because of test data belonging to another repo. Substring matching made
    it worse, matching a short name inside a longer unrelated one.

    So a bare name is no longer evidence. Coverage needs a test FILENAME naming the module,
    or a SYMBOL the module actually defines -- both of which require somebody to have looked
    at it.
    """
    with repo() as root:
        write(root, "lib/fixturesend.php", "<?php\nfunction fixture_handoff(string $to): bool { return true; }\n")
        write(
            root,
            "tests/unrelated_test.php",
            "<?php\n// a fixture that happens to mention lib/fixturesend.php in passing\n",
        )
        assert len(covered(root)) == 1, "a passing mention is not coverage"


def test_a_one_letter_symbol_is_not_evidence_of_coverage():
    """Measured against allegroit-dk, whose translation helper is a single letter.

    A one- or two-character name matches inside ordinary prose in any suite, so accepting it
    would mark a module covered because some unrelated test used the letter. Short names are
    common for exactly the helpers that get called everywhere and tested nowhere.
    """
    with repo() as root:
        write(root, "lib/fixturelang.php", "<?php\nfunction t(string $key): string { return $key; }\n")
        write(root, "tests/other_test.php", "<?php\n// t is mentioned here, incidentally\n")
        assert len(covered(root)) == 1


def test_a_module_named_by_one_of_its_symbols_is_covered():
    with repo() as root:
        write(root, "lib/fixturemod.php", MODULE)
        write(root, "scripts/test-site.php", "<?php\nassert(fixture_render_thing('x') !== null);\n")
        assert covered(root) == []


def test_a_declarative_file_defining_nothing_needs_no_test():
    """A returned table of constants has no behaviour. Not relief -- there is nothing to run."""
    with repo() as root:
        write(root, "config/fixturetable.php", "<?php\nreturn ['own' => ['127.0.0.0/8']];\n")
        write(root, "config/fixturetable2.php", "<?php\nreturn ['greeting' => 'Hej'];\n")
        assert covered(root) == []


def test_test_files_are_corpus_not_subjects():
    with repo() as root:
        write(root, "tests/test_thing.py", "def test_it():\n    assert thing() == 1\n")
        assert covered(root) == []


def test_vendored_code_is_out_of_scope():
    with repo() as root:
        write(root, "vendor/parsedown/Parsedown.php", MODULE)
        write(root, "node_modules/left-pad/index.js", "export function pad(s) { return s; }\n")
        assert covered(root) == []


def test_migrations_are_out_of_scope():
    """A migration is verified by running it against a real schema, not by a unit test."""
    with repo() as root:
        write(root, "migrations/0001_initial.py", "def upgrade():\n    op.create_table('x')\n")
        assert covered(root) == []


def test_a_file_scoped_exemption_silences_it_with_a_reason():
    with repo() as root:
        write(
            root,
            "lib/fixturegen.php",
            f"<?php\n// standards: {TESTS_UNCOVERED_MODULE_RULE} exempt -- {EXEMPT_REASON}\n"
            + MODULE[len("<?php\n") :],
        )
        assert covered(root) == []


def test_a_reason_under_the_floor_does_not_silence_it():
    with repo() as root:
        write(
            root,
            "lib/fixturegen.php",
            f"<?php\n// standards: {TESTS_UNCOVERED_MODULE_RULE} exempt -- later\n" + MODULE[len("<?php\n") :],
        )
        assert len(covered(root)) == 1


def test_a_test_file_named_after_the_module_covers_it():
    """`test_content.py` <-> `content.py` is the commonest convention there is.

    Found by running this rule over the pack on 2026-09-01: `standards_symbols.py` was
    reported uncovered although `test_standards_symbols.py` sits beside it, because that
    suite exercises the module through `standards_docs`'s entry point and so never spells
    its name in the body. The corpus is the suite's PATHS as well as its contents.
    """
    with repo() as root:
        write(root, "lib/fixturemod.php", MODULE)
        write(root, "tests/test_fixturemod.php", "<?php\nassert(render() !== '');\n")
        assert covered(root) == []


def test_a_similarly_named_test_does_not_cover_a_different_module():
    """The filename match is on the stem, not a substring: `test_content` is not `contents`."""
    with repo() as root:
        write(root, "lib/fixturemods.php", "<?php\nfunction listing(): array { return []; }\n")
        write(root, "tests/test_fixturemod.php", "<?php\nassert(render() !== '');\n")
        assert len(covered(root)) == 1


def test_a_filename_claim_does_not_reach_into_a_sibling_package():
    """The collision that made this scoping necessary.

    Measured in allegro-it-services on 2026-09-03: adding `apps/legal/tests/test_admin.py`
    marked `admin.py` covered in EVERY app, including a 488-line one that no test named and
    no test exercised. `admin`, `models`, `views`, `tasks` and `schema` repeat in every app
    of a Django project, so a repo-wide stem match means the first app to get a test silences
    all the others -- silently, and in the direction that hides work rather than invents it.
    """
    with repo() as root:
        write(root, "apps/one/fixturemod.py", PY_MODULE)
        write(root, "apps/two/fixturemod.py", PY_MODULE)
        write(root, "apps/one/tests/test_fixturemod.py", "def test_it():\n    assert render()\n")

        found = covered(root)

        assert [v.path.parent.name for v in found] == ["two"]


def test_a_filename_claim_covers_the_whole_package_it_sits_in():
    """Scoped to the package, not to the exact directory: a test tree at the app root covers
    the modules beneath it, which is how Django apps and monorepo packages are laid out."""
    with repo() as root:
        write(root, "apps/one/services/fixturemod.py", PY_MODULE)
        write(root, "apps/one/tests/test_fixturemod.py", "def test_it():\n    assert render()\n")

        assert covered(root) == []


def test_a_root_level_test_tree_still_covers_the_whole_repo():
    """The narrowing must not punish a repo whose tests all live in one root `tests/` tree --
    its scope is the repo root, so it behaves exactly as before."""
    with repo() as root:
        write(root, "src/deep/nested/fixturemod.py", PY_MODULE)
        write(root, "tests/test_fixturemod.py", "def test_it():\n    assert render()\n")

        assert covered(root) == []


def test_a_dotnet_sibling_test_project_covers_the_project_it_tests():
    """The .NET convention, and the one the scoping did not know about.

    Measured in InvoTrack on 2026-09-28: a solution lays out `InvoTrack/` and
    `InvoTrack.Tests/` as SIBLINGS, so the test project's scope was itself -- a directory
    containing no production modules. Filename claims therefore covered nothing at all, and
    the rule reported 44 modules uncovered in a repo with 191 passing tests. `AssetVersion.cs`
    was the proof: a thorough `AssetVersionTests.cs` naming it four times left it reported,
    because its only public surface is a property and so it defines no symbol the corpus can
    name either. `<Project>.Tests` is to a C# solution exactly what `tests/` is to a Python
    package, and it has to scope the same way.
    """
    with repo() as root:
        write(root, "Fixture/Fixturemod.cs", MODULE_CS)
        write(root, "Fixture.Tests/FixturemodTests.cs", "public class T { public void A() { } }\n")

        assert covered(root) == []


def test_a_dotnet_test_project_does_not_reach_into_a_sibling_project():
    """The narrowing that makes the widening safe: `A.Tests` claims `A`, never `B`."""
    with repo() as root:
        write(root, "Fixture/Fixturemod.cs", MODULE_CS)
        write(root, "Other/Fixturemod.cs", MODULE_CS)
        write(root, "Fixture.Tests/FixturemodTests.cs", "public class T { public void A() { } }\n")

        found = covered(root)

        assert [v.path.parent.name for v in found] == ["Other"]


def test_a_dotnet_test_project_with_no_matching_project_claims_only_itself():
    """`Standalone.Tests` beside no `Standalone/` must not silently widen to the repo root."""
    with repo() as root:
        write(root, "Fixture/Fixturemod.cs", MODULE_CS)
        write(root, "Standalone.Tests/FixturemodTests.cs", "public class T { public void A() { } }\n")

        assert len(covered(root)) == 1


def test_a_test_beside_the_module_covers_it():
    """The frontend convention: `__tests__` next to the source it exercises."""
    with repo() as root:
        write(root, "src/utils/fixturemod.ts", "export const fixtureRenderThing = () => 1;\n")
        write(root, "src/utils/__tests__/fixturemod.spec.ts", "it('renders', () => { expect(1).toBe(1); });\n")

        assert covered(root) == []


def test_a_symbol_match_still_reaches_across_packages():
    """Only the FILENAME claim is scoped. A symbol name is distinctive enough to be evidence
    wherever it appears -- and scoping it too would break a suite that imports a module from
    a shared package, which is the normal way a monorepo tests one."""
    with repo() as root:
        write(root, "apps/two/fixturemod.py", PY_MODULE)
        write(root, "apps/one/tests/test_other.py", "def test_it():\n    assert fixture_render_thing('x')\n")

        assert covered(root) == []


def test_stays_quiet_on_the_packs_own_source():
    """Every module in the pack is named by its own suite; a finding here is a misfire.

    SCOPED TO THE PACK, because this file SHIPS. `__file__.parent.parent` is the pack root
    here and the CONSUMING REPO'S ROOT everywhere else, so the unscoped version scanned each
    adopted repo's whole application and failed by construction the moment it arrived --
    allegro-it-services took it on 2026-09-02 and got 226 findings from a test asserting
    zero, the single red case in an otherwise green 900-case run. A shipped test that cannot
    pass where it is shipped teaches people that a red suite is normal, which costs far more
    than the misfire it was written to catch.

    The pack identifies itself the same way standards_pack_update.py does: by the dev
    directory no consuming repo ever receives.
    """
    pack_root = Path(__file__).resolve().parent.parent
    if not (pack_root / "engineering_standards" / "dev" / "pack_manifest.py").is_file():
        return  # a consuming repo: its own coverage is its own business, and its baseline's

    assert covered(pack_root) == []


# ---- test quality: a test that cannot fail ----------------------------------------------


def test_a_test_function_with_no_assertion_fires():
    source = "def test_user_is_created():\n    user = create_user('a@b.c')\n    print(user)\n"
    assert quality(source, "test_users.py") == [TEST_WITHOUT_ASSERTION_RULE]


def test_unittest_assertions_count_as_assertions():
    r"""`\b assert \b` cannot see `assertEqual`: the boundary it wants is not there.

    unittest's whole vocabulary is `assertX(`, so before this every `TestCase` method in the
    estate read as asserting nothing. Measured on allegro-it-services 2026-09-03.
    """
    source = "class T:\n    def test_codes(self):\n        self.assertEqual(codes('N'), ['N'])\n"
    assert quality(source, "test_rebase.py") == []


def test_mock_assertions_count_as_assertions():
    """Same boundary problem, same fix: `assert_not_called` continues past `assert` too."""
    source = "def test_nothing_saved():\n    save = make_mock()\n    save.assert_not_called()\n"
    assert quality(source, "test_graph.py") == []

    called = "def test_sent_once():\n    send = make_mock()\n    send.assert_called_once_with(1)\n"
    assert quality(called, "test_graph.py") == []


def test_a_call_that_merely_starts_with_a_verb_is_not_an_assertion():
    """The widening must not swallow the real finding sitting next to it.

    `check_org_access(info, pk)  # must not raise` is a genuinely assertion-free test, and
    reads as an assertion only to a pattern that stops caring after a prefix.
    """
    source = "def test_access_allowed():\n    access.check_org_access(info, 'org-pk')  # must not raise\n"
    assert quality(source, "test_access.py") == [TEST_WITHOUT_ASSERTION_RULE]


def test_a_tautological_assertion_fires():
    source = "def test_it_works():\n    assert True\n"
    assert TEST_ALWAYS_PASSES_RULE in quality(source, "test_users.py")


def test_a_javascript_tautology_fires():
    source = "it('works', () => {\n  expect(true).toBe(true);\n});\n"
    assert TEST_ALWAYS_PASSES_RULE in quality(source, "users.spec.js")


def test_a_skipped_test_with_no_reason_fires():
    source = "@pytest.mark.skip\ndef test_flaky():\n    assert compute() == 3\n"
    assert TEST_SILENTLY_SKIPPED_RULE in quality(source, "test_users.py")


def test_a_skipped_javascript_test_fires():
    source = "it.skip('resolves', () => {\n  expect(ip()).toBe('1.2.3.4');\n});\n"
    assert TEST_SILENTLY_SKIPPED_RULE in quality(source, "users.spec.ts")


# ---- test quality: what must NOT be reported --------------------------------------------


def test_a_real_assertion_is_quiet():
    source = "def test_user_is_created():\n    assert create_user('a@b.c').email == 'a@b.c'\n"
    assert quality(source, "test_users.py") == []


def test_a_helper_in_a_test_file_is_not_a_test():
    """Only test FUNCTIONS need assertions; a fixture or factory beside them is not one."""
    source = (
        "def make_user(email):\n"
        "    return User(email=email)\n"
        "\n"
        "def test_user_is_created():\n"
        "    assert make_user('a@b.c').email == 'a@b.c'\n"
    )
    assert quality(source, "test_users.py") == []


def test_a_skip_with_a_stated_reason_is_quiet():
    source = '@pytest.mark.skip(reason="upstream driver has no arm64 build yet")\ndef test_x():\n    assert f() == 1\n'
    assert TEST_SILENTLY_SKIPPED_RULE not in quality(source, "test_users.py")


def test_a_comparison_against_a_literal_true_is_not_a_tautology():
    """`assert x is True` checks something. Only both sides being constant is the defect."""
    source = "def test_flag():\n    assert feature_enabled() is True\n"
    assert quality(source, "test_users.py") == []


def test_production_source_is_never_checked_for_test_quality():
    """A `print()`-only function in application code is not this rule's business."""
    source = "def build_report():\n    print('no assertions here')\n"
    assert quality(source, "lib/fixturereports.py") == []


def test_an_exempted_assertion_free_test_is_quiet():
    source = (
        f"# standards: {TEST_WITHOUT_ASSERTION_RULE} exempt -- {EXEMPT_REASON}\n"
        "def test_import_does_not_explode():\n"
        "    import app.wiring\n"
    )
    assert quality(source, "test_wiring.py") == []


if __name__ == "__main__":
    from standards_selftest import run_module_tests

    raise SystemExit(run_module_tests(sys.modules[__name__]))

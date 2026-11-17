"""Cases for the repository-shape rules: generic-filename and the gitignore family.

Two things here are pinned harder than the rest, because both are places the rule was
already wrong once or would silently become useless:

* THE DIRECTORY ASYMMETRY. A junk drawer fires under a meaningful directory; a bare role
  noun does not. The first draft treated them alike and reported 8 findings that were the
  Django app layout doing exactly what it is supposed to. If someone later "simplifies"
  those two families into one test, these cases are the reason not to.
* THE GITIGNORE SPELLINGS. `bin/`, `/bin`, `**/bin` and `[Bb]in/` are one pattern written
  four ways, and Visual Studio's own template writes the fourth. A literal comparison
  passes its unit tests and then reports every .NET repo in the estate as unignored.

Source of truth: engineering-standards/engineering_standards/test_standards_layout.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from standards_core import CheckConfig
from standards_dispatch import check_source_file
from standards_gitignore import (
    check_gitignore,
    normalised_ignore_patterns,
    repository_artifacts,
)
from standards_layout import check_generic_filename
from standards_selftest import run_module_tests

REPO = Path("/repo")


def names(relative: str, lines: list[str] | None = None) -> list[str]:
    """Messages check_generic_filename yields for a repo-relative path."""
    return [violation.message for violation in check_generic_filename(REPO / relative, lines or ["x = 1"], REPO)]


# ---- the junk drawer: fires anywhere -------------------------------------------------------


def test_flags_utils_in_a_generic_directory() -> None:
    assert names("src/utils.ts")


def test_flags_helpers_under_a_meaningful_directory() -> None:
    """`acl/helpers.py` is a real finding, and this is the half that must NOT be relaxed.

    "helpers" survives any amount of directory context: it still says nothing about what is
    inside, and still accepts anything ACL-adjacent nobody wanted to name.
    """
    assert names("common/acl/helpers.py")


def test_flags_the_whole_junk_drawer_family() -> None:
    for relative in (
        "src/misc.ts",
        "src/common.ts",
        "src/stuff.py",
        "src/temp.php",
        "src/various.js",
        "src/old.cs",
        "workspace/shared.tsx",
    ):
        assert names(relative), relative


def test_is_case_insensitive() -> None:
    assert names("src/Utils.cs")
    assert names("src/HELPERS.py")


# ---- the bare role noun: fires only where the directory says nothing too -------------------


def test_flags_a_role_name_in_a_generic_directory() -> None:
    assert names("src/service.ts")


def test_flags_a_role_name_at_the_repository_root() -> None:
    """The root is the repository, not a domain, so it supplies no subject to inherit."""
    assert names("Service.cs")


def test_quiet_on_a_role_name_under_a_domain_directory() -> None:
    """THE CALIBRATION. The Django app layout and package-per-domain layout both read as a
    coordinate -- the directory says what, the file says which layer -- and the first draft
    reported 8 of these across the estate before the asymmetry was introduced."""
    for relative in (
        "apps/legal/services.py",
        "apps/ai/adapters.py",
        "engine/corpus/repository.py",
        "engine/relay/provider.py",
        "common/graphql/acl/wrappers.py",
    ):
        assert not names(relative), relative


def test_generic_directory_does_not_rescue_a_role_name() -> None:
    """`features/` is a container, not a domain, so its child inherits nothing."""
    assert names("features/handler.ts")


# ---- names the framework resolves ----------------------------------------------------------


def test_quiet_on_framework_filenames() -> None:
    for relative in (
        "apps/legal/models.py",
        "apps/legal/views.py",
        "apps/legal/urls.py",
        "src/index.ts",
        "src/main.tsx",
        "app/dashboard/page.tsx",
        "src/middleware.ts",
        "src/Program.cs",
        "Pages/Index.razor",
        "public/index.php",
        "pkg/__init__.py",
    ):
        assert not names(relative), relative


def test_utils_py_is_exempt_but_utils_ts_is_not() -> None:
    """A deliberate asymmetry, not an oversight. `utils.py` is a settled Django/Flask
    convention every Python developer reads without pause; nothing mandates `utils.ts`."""
    assert not names("apps/legal/utils.py")
    assert names("src/utils.ts")


# ---- tests inherit their subject's name ----------------------------------------------------


def test_quiet_on_test_files() -> None:
    for relative in (
        "src/utils.test.ts",
        "src/utils.spec.ts",
        "tests/test_helpers.py",
        "tests/helpers.py",
        "src/__tests__/utils.ts",
    ):
        assert not names(relative), relative


# ---- multi-extension filenames -------------------------------------------------------------


def test_reads_the_leading_segment_not_the_stem() -> None:
    """`Path.stem` of `settings.component.tsx` is `settings.component`, which matches
    nothing -- using it would make the rule silently inert on Angular-style names."""
    assert not names("src/settings.component.tsx")
    assert names("src/utils.component.ts")


# ---- exemption -------------------------------------------------------------------------------


def test_exemption_marker_silences_it() -> None:
    lines = [
        "// standards: generic-filename exempt -- this is the published entry point and the "
        "name is part of the package's public API, so renaming it is a breaking change.",
        "export const x = 1;",
    ]
    assert not names("src/utils.ts", lines)


def test_a_shrug_does_not_clear_the_reason_floor() -> None:
    assert names("src/utils.ts", ["// standards: generic-filename exempt -- fine"])


# ---- the baseline key ------------------------------------------------------------------------


def test_message_quotes_the_filename() -> None:
    """violation_key takes the first quoted token. Quoting a constant instead would give
    every finding in a repo one shared key -- the hole razor-var fell into in draft."""
    assert names("src/utils.ts")[0].startswith("'utils.ts'")


# ---- the dispatcher --------------------------------------------------------------------------


def dispatched_rules(relative: str) -> list[str]:
    return [violation.rule for violation in check_source_file(REPO / relative, ["x = 1"], CheckConfig(), {}, REPO)]


def test_dispatcher_reports_a_generic_filename() -> None:
    assert "generic-filename" in dispatched_rules("src/utils.ts")


def test_dispatcher_spares_files_whose_names_their_tooling_resolves() -> None:
    """Compose files, workflows and .env.example are matched by name by their own tooling,
    so a name rule has nothing to say about them. These are the three early returns in
    check_source_file, and the rule sits below them deliberately."""
    for relative in (
        "docker-compose.yml",
        ".github/workflows/deploy.yml",
        ".env.example",
    ):
        assert "generic-filename" not in dispatched_rules(relative), relative


def test_config_can_switch_it_off() -> None:
    config = CheckConfig(check_filenames=False)
    rules = [violation.rule for violation in check_source_file(REPO / "src/utils.ts", ["x = 1"], config, {}, REPO)]
    assert "generic-filename" not in rules


# ---- gitignore: the spellings ------------------------------------------------------------------


def test_every_spelling_of_one_pattern_normalises_alike() -> None:
    for spelling in ("bin/", "/bin", "/bin/", "**/bin/", "[Bb]in/"):
        assert "bin" in normalised_ignore_patterns([spelling]), spelling


def test_a_negation_is_not_coverage() -> None:
    """`!bin/keep.dll` re-admits build output. Counting it as coverage would let a file
    that explicitly un-ignores the thing pass the rule that exists to ignore it."""
    assert "bin" not in normalised_ignore_patterns(["!bin/"])


def test_comments_and_blanks_are_dropped() -> None:
    assert normalised_ignore_patterns(["# bin/", "", "   "]) == set()


def test_a_multi_character_class_is_left_alone() -> None:
    """`*.py[cod]` must NOT collapse to `*.pyc` -- that would change what it matches. It is
    accepted as a literal spelling of the bytecode requirement instead."""
    assert "*.py[cod]" in normalised_ignore_patterns(["*.py[cod]"])


# ---- gitignore: the rule ------------------------------------------------------------------------


def gitignore_findings(contents: str | None, files: dict[str, str] | None = None) -> list[str]:
    """Run check_gitignore against a real temporary repository.

    On disk rather than mocked, because the rule's whole job is reading a file that may not
    be there, and `repository_artifacts` decides what is required by globbing the tree.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for relative, body in (files or {}).items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        if contents is not None:
            (root / ".gitignore").write_text(contents, encoding="utf-8")
        return [f"{v.rule}:{v.message}" for v in check_gitignore(root, CheckConfig())]


def test_a_missing_gitignore_is_reported() -> None:
    """The worst case, and the one a file-driven scanner cannot see: no file at all means
    every requirement is unmet, and nothing to walk means nothing to report."""
    findings = gitignore_findings(None)
    assert any(f.startswith("gitignore-missing") for f in findings)


def test_missing_env_is_reported() -> None:
    assert any(".env" in f for f in gitignore_findings("__pycache__/\n"))


def test_a_complete_gitignore_is_clean() -> None:
    assert gitignore_findings(".env*\n") == []


def test_a_bare_env_line_is_not_enough() -> None:
    """A copy of .env is the same credential. `.env` alone left `.env.bak-staging` untracked
    but not ignored in a production checkout (2026-10-10)."""
    for spelling in (".env\n", ".env.local\n", "*.env\n", ".env\n.env.local\n"):
        assert any(f.startswith("gitignore-build-output:'.env*'") for f in gitignore_findings(spelling)), spelling


def test_env_plus_its_copies_is_enough() -> None:
    assert gitignore_findings(".env\n.env.*\n") == []


def test_an_example_file_is_re_admitted_on_purpose() -> None:
    """The negation that lets `.env.example` be committed must not count against coverage."""
    assert gitignore_findings(".env*\n!.env.example\n") == []
    assert any(".env*" in f for f in gitignore_findings(".env\n!.env.example\n"))


def test_dotnet_requirements_apply_only_to_a_dotnet_repo() -> None:
    clean = gitignore_findings(".env*\n")
    dotnet = gitignore_findings(".env*\n", {"src/App.csproj": "<Project />"})
    assert clean == []
    assert any("bin/" in f for f in dotnet)
    assert any("obj/" in f for f in dotnet)


def test_the_visual_studio_template_spelling_satisfies_dotnet() -> None:
    """The most standard .NET .gitignore there is. A literal comparison reports it as
    missing both entries."""
    findings = gitignore_findings(".env*\n[Bb]in/\n[Oo]bj/\n.vs/\n*.user\n", {"src/App.csproj": "<Project />"})
    assert findings == []


def test_a_dotnet_repo_must_ignore_visual_studio_state() -> None:
    findings = gitignore_findings(".env*\nbin/\nobj/\n", {"src/App.csproj": "<Project />"})
    assert any(f.startswith("gitignore-build-output:'.vs/'") for f in findings)
    assert any(f.startswith("gitignore-build-output:'*.user'") for f in findings)


def test_a_present_vs_directory_must_be_ignored_in_any_repo() -> None:
    """VS's "Open Folder" writes `.vs/` into a repo with no solution at all, so its presence
    is enough -- the same conditional shape as dist/."""
    findings = gitignore_findings(".env*\n", {".vs/slnx.sqlite": ""})
    assert any(f.startswith("gitignore-build-output:'.vs/'") for f in findings)
    assert not any("*.user" in f for f in findings)
    assert gitignore_findings(".env*\n.vs/\n", {".vs/slnx.sqlite": ""}) == []


def test_a_dotnet_repo_with_a_vs_directory_is_told_once() -> None:
    findings = gitignore_findings(".env*\nbin/\nobj/\n*.user\n", {"App.csproj": "<Project />", ".vs/x": ""})
    assert sum(f.startswith("gitignore-build-output:'.vs/'") for f in findings) == 1


def test_python_bytecode_accepts_either_spelling() -> None:
    project = {"pyproject.toml": "[project]\nname='x'\n"}
    assert not any("__pycache__" in f for f in gitignore_findings(".env*\n*.py[cod]\n.venv/\n", project))
    assert not any("__pycache__" in f for f in gitignore_findings(".env*\n__pycache__/\n.venv/\n", project))


def test_node_requirement_follows_package_json() -> None:
    findings = gitignore_findings(".env*\n", {"package.json": "{}"})
    assert any("node_modules/" in f for f in findings)


def test_artifacts_are_detected_without_descending_into_dependencies() -> None:
    """node_modules holds thousands of package.json files; the detector must not need to
    read them to answer a question the root already answers."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "package.json").write_text("{}", encoding="utf-8")
        assert "node" in repository_artifacts(root)
        assert "dotnet" not in repository_artifacts(root)


def test_config_can_switch_the_gitignore_rule_off() -> None:
    with tempfile.TemporaryDirectory() as directory:
        config = CheckConfig(check_gitignore=False)
        assert list(check_gitignore(Path(directory), config)) == []


# ---- gitignore: a present-but-unignored dist/ -------------------------------------------------


def test_a_present_dist_directory_must_be_ignored() -> None:
    """An external audit found a 33 MB `dist/` committed, churning a diff on every build. The
    requirement is CONDITIONAL on a dist/ existing, so a library that never builds one is not
    told to ignore it."""
    findings = gitignore_findings(".env*\n", {"dist/main-ABC.js": "// bundle"})
    assert any("dist/" in finding for finding in findings)


def test_an_ignored_dist_directory_is_clean() -> None:
    findings = gitignore_findings(".env*\ndist/\n", {"dist/main-ABC.js": "// bundle"})
    assert not any("dist/" in finding for finding in findings)


def test_no_dist_directory_means_no_dist_requirement() -> None:
    """The conditional half: absent a dist/, the rule asks for nothing."""
    findings = gitignore_findings(".env*\n", {})
    assert not any("dist/" in finding for finding in findings)


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "repository-shape cases"))

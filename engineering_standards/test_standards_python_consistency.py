"""Cases for the python-consistency rule, produced by the shared engine.

The rule exists for a failure that LOOKS FINE from every angle a per-file check has: each
declaration clears the floor, each file is internally coherent, the tests are green -- and CI
is exercising a different interpreter from the one production runs. So the cases here are built
as whole repos rather than as line fixtures, because a rule that compares files cannot be
tested one file at a time any more than it can be run that way.

Run: python test_standards_python_consistency.py   (or pytest)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import CheckConfig  # noqa: E402
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402
from standards_python_consistency import PYTHON_TOOLCHAIN, constraint_admits  # noqa: E402
from standards_scope import should_check  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402
from standards_toolchain_consistency import check_consistency  # noqa: E402

EXEMPT_REASON = "the matrix is deliberate: this library supports both minors for consumers"
assert len(EXEMPT_REASON) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


@contextmanager
def repo(**files: str) -> Iterator[tuple[Path, list[Path]]]:
    """A throwaway repo holding `files`, plus the path list the scanner would produce.

    The paths go through `should_check`, exactly as check-source-limits.py builds them, so a
    case cannot accidentally test the rule against a file the real scanner would never hand it.
    """
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        for relative, text in files.items():
            path = root / relative.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        paths = [path for path in sorted(root.rglob("*")) if path.is_file() and should_check(path, root, CheckConfig())]
        yield root, paths


def findings(**files: str) -> list[str]:
    with repo(**files) as (root, paths):
        return [v.message for v in check_consistency(root, paths, CheckConfig(), PYTHON_TOOLCHAIN)]


CI_314 = "jobs:\n  check:\n    steps:\n      - uses: actions/setup-python@v7\n        with:\n          python-version: '3.14'\n"
CI_315 = CI_314.replace("3.14", "3.15")


# ---- the failure the rule exists for --------------------------------------------------------


def test_ci_and_the_container_on_different_minors_is_flagged() -> None:
    """THE case. Both clear the floor, both files are internally fine, the suite is green --
    and the code is tested on an interpreter it never ships on."""
    assert findings(**{".github__workflows__check.yml": CI_314, "Dockerfile": "FROM python:3.15-slim\n"})


def test_the_message_names_both_versions_and_where_they_are() -> None:
    message = findings(**{".github__workflows__check.yml": CI_314, "Dockerfile": "FROM python:3.15-slim\n"})[0]
    assert "3.14" in message and "3.15" in message
    assert "Dockerfile:1" in message
    assert "check.yml" in message


def test_a_pin_file_disagreeing_with_ci_is_flagged() -> None:
    """The developer's shell is an environment too: a repo whose .python-version and CI differ
    means every local run tests something CI never does."""
    assert findings(**{".python-version": "3.15\n", ".github__workflows__check.yml": CI_314})


def test_a_lint_target_disagreeing_with_the_interpreter_is_flagged() -> None:
    """ruff cannot know which interpreter executes. A target ahead of it accepts syntax that
    fails at runtime; behind it, the modern-syntax fixes never get suggested."""
    assert findings(**{"Dockerfile": "FROM python:3.14-slim\n", "pyproject.toml": 'target-version = "py315"\n'})


def test_three_environments_agreeing_is_clean() -> None:
    assert not findings(
        **{
            ".python-version": "3.14.2\n",
            ".github__workflows__check.yml": CI_314,
            "Dockerfile": "FROM python:3.14-slim\n",
            "pyproject.toml": 'requires-python = ">=3.14"\ntarget-version = "py314"\n',
        }
    )


def test_a_patch_level_difference_is_not_a_disagreement() -> None:
    """`.python-version` naming 3.14.2 while CI installs 3.14 is the same interpreter line.
    Demanding patch equality would fire on every repo and teach people to exempt the rule."""
    assert not findings(**{".python-version": "3.14.7\n", ".github__workflows__check.yml": CI_314})


# ---- constraints are ranges, not versions ----------------------------------------------------


def test_a_ceiling_the_runtime_breaches_is_flagged() -> None:
    """allegro-it-services' live shape: `~=3.11.1` means `>=3.11.1, ==3.11.*`. pip would refuse
    to install this package into the very container it ships in."""
    assert findings(**{"Dockerfile": "FROM python:3.14-slim\n", "pyproject.toml": 'requires-python = "~=3.11.1"\n'})


def test_a_minimum_below_the_runtime_is_correct_and_silent() -> None:
    """A constraint is a MINIMUM. `>=3.14` while running 3.15 is right, not a disagreement --
    this is the case a rule comparing everything for equality would get wrong."""
    assert not findings(**{"Dockerfile": "FROM python:3.15-slim\n", "pyproject.toml": 'requires-python = ">=3.14"\n'})


def test_a_minimum_above_the_runtime_is_flagged() -> None:
    """The package says it needs newer than what actually runs it."""
    assert findings(**{"Dockerfile": "FROM python:3.14-slim\n", "pyproject.toml": 'requires-python = ">=3.15"\n'})


def test_constraint_admits_reads_the_shapes_that_exist() -> None:
    assert constraint_admits(">=3.14", (3, 14))
    assert constraint_admits(">=3.14", (3, 15))
    assert constraint_admits(">=3.14,<4", (3, 14))
    assert constraint_admits("~=3.14", (3, 15)), "two-component ~= leaves the minor free"
    assert not constraint_admits("~=3.11.1", (3, 14)), "three-component ~= locks the minor"
    assert not constraint_admits("==3.11.*", (3, 14))
    assert not constraint_admits(">=3.9,<3.13", (3, 14))
    assert not constraint_admits("<=3.13", (3, 14))
    assert not constraint_admits(">=3.15", (3, 14))
    assert constraint_admits("whatever nonsense", (3, 14)), "unparseable must never invent a no"


# ---- test matrices are deliberate, not contradictions ----------------------------------------


def test_a_matrix_covering_the_shipped_version_is_clean() -> None:
    """`[3.14, 3.15]` with a 3.14 container is a library testing forward, not a mismatch."""
    assert not findings(
        **{
            ".github__workflows__check.yml": "        python-version: [3.14, 3.15]\n",
            "Dockerfile": "FROM python:3.14-slim\n",
        }
    )


def test_a_matrix_that_omits_the_shipped_version_is_flagged() -> None:
    """Testing everything except what production runs."""
    assert findings(
        **{
            ".github__workflows__check.yml": "        python-version: [3.15, 3.16]\n",
            "Dockerfile": "FROM python:3.14-slim\n",
        }
    )


def test_a_line_exemption_silences_the_disagreement() -> None:
    assert not findings(
        **{
            ".python-version": "3.14\n",
            "Dockerfile": f"# standards: python-consistency exempt -- {EXEMPT_REASON}\nFROM python:3.15-slim\n",
        }
    )


def test_the_config_flag_turns_it_off_for_a_genuine_multi_service_repo() -> None:
    with repo(**{".python-version": "3.14\n", "Dockerfile": "FROM python:3.15-slim\n"}) as (root, paths):
        off = CheckConfig(check_toolchain_consistency=False)
        assert not list(check_consistency(root, paths, off, PYTHON_TOOLCHAIN))
        assert list(check_consistency(root, paths, CheckConfig(), PYTHON_TOOLCHAIN))


# ---- quiet where it should be ----------------------------------------------------------------


def test_a_repo_with_one_declaration_is_clean() -> None:
    """Nothing to disagree with. A rule that needed two and reported on one would fire on every
    repo that merely installs Python in CI to run the pack's own scanner."""
    assert not findings(**{".github__workflows__check.yml": CI_314})


def test_a_repo_with_no_python_at_all_is_clean() -> None:
    assert not findings(**{"Dockerfile": "FROM node:24-alpine\n"})


def test_a_floating_tag_is_left_to_the_floor_rule() -> None:
    """`python:latest` names nothing comparable. python-support already reports it; reporting it
    here as well would make one problem look like two."""
    assert not findings(**{"Dockerfile": "FROM python:latest\n", ".github__workflows__check.yml": CI_314})


def test_the_message_carries_no_single_quotes() -> None:
    """violation_key derives a baseline identity from the first single-quoted token in a
    message. A message quoting a version would change identity whenever a version changed,
    resurrecting a grandfathered finding for an unrelated edit."""
    message = findings(**{".github__workflows__check.yml": CI_314, "Dockerfile": "FROM python:3.15-slim\n"})[0]
    assert "'" not in message


def test_it_reports_once_and_not_once_per_file() -> None:
    """Three disagreeing sites are ONE problem with one fix. Three findings for one cause is how
    a rule becomes something people exempt wholesale."""
    reported = findings(
        **{
            ".python-version": "3.13\n",
            ".github__workflows__check.yml": CI_314,
            "Dockerfile": "FROM python:3.15-slim\n",
        }
    )
    assert len(reported) == 1


# ---- the driver actually reaches it ----------------------------------------------------------


def test_the_scanner_reports_it_on_a_whole_tree_run() -> None:
    """END TO END, through the real CLI, because everything above tests a function nobody calls
    directly. The rule is invoked from one `if only is None:` branch in check-source-limits.py;
    get that wrong and every unit test here still passes while the rule never runs. The pack's
    own tree cannot catch it -- the pack declares Python in exactly one place, so the rule
    correctly returns early on itself and a green pack gate proves nothing.
    """
    pack_directory = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "engineering_standards").mkdir()
        for source in pack_directory.glob("*.py"):
            shutil.copy2(source, root / "engineering_standards" / source.name)
        (root / "Dockerfile").write_text("FROM python:3.15-slim\n", encoding="utf-8")
        workflow = root / ".github" / "workflows" / "check.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(CI_314, encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(root / "engineering_standards" / "check-source-limits.py"), "--root", str(root)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert "python-consistency" in result.stdout, result.stdout + result.stderr
        assert result.returncode != 0, "a disagreement must FAIL the scan, not merely print"


def test_a_whole_tree_baseline_cannot_grandfather_it() -> None:
    """`--write-baseline` is how the pack lands in a repo without a wall of red, and this rule
    is deliberately outside it (NEVER_BASELINED). A baselined "CI and production run different
    Pythons" is not deferred debt -- it is a live bug recorded as accepted, and the reason it
    needs enforcing is that nothing else about the repo looks wrong. The line exemption remains,
    which is the difference that matters: it states a reason a human can disagree with.
    """
    pack_directory = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "engineering_standards").mkdir()
        for source in pack_directory.glob("*.py"):
            shutil.copy2(source, root / "engineering_standards" / source.name)
        (root / "Dockerfile").write_text("FROM python:3.15-slim\n", encoding="utf-8")
        workflow = root / ".github" / "workflows" / "check.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(CI_314, encoding="utf-8")

        driver = str(root / "engineering_standards" / "check-source-limits.py")
        subprocess.run(
            [sys.executable, driver, "--root", str(root), "--write-baseline", "--baseline-consent"],
            capture_output=True,
            text=True,
            check=False,
        )
        after = subprocess.run(
            [sys.executable, driver, "--root", str(root)], capture_output=True, text=True, check=False
        )
        assert "python-consistency" in after.stdout, after.stdout + after.stderr
        assert after.returncode != 0, "baselining must not silence it"


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "python-consistency cases"))

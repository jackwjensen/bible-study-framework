#!/usr/bin/env python3
"""Baselined debt must MOVE when a branch touches it.

The ratchet alone stops debt getting worse and permits a 900-line file to sit at 900
forever. `refactoring.md` has said "a baseline is a starting point, not a resting place"
since 2026-08-21 and could only follow it with "you are allowed to pay it down" -- which
grants permission and requires nothing. An optional refactor loses every race against a
deadline, so it is always what gets dropped.

The discriminating test is `test_an_in_place_edit_must_still_pay_down`: a file edited
without changing its line count is exactly the case the old ratchet waved through, and it
is the one that proves this gate does something the previous rules did not.

The two-condition structure gets its own tests because either half alone leaks, and the
leak is silent: with only "must shrink", the recorded number never moves and the NEXT
branch clears the bar for free.

Run: python test_standards_paydown.py   (or pytest; needs git on PATH)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PACK_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(PACK_DIRECTORY))

from standards_paydown import PAYDOWN_RULE, STALE_RULE, UNKNOWN_RULE  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402  (path set above)

LIMITS = PACK_DIRECTORY / "check-source-limits.py"
BASELINE = ".standards-baseline.json"


def git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *arguments], cwd=repo, capture_output=True, text=True, check=False)


def write_big_file(repo: Path, lines: int, *, marker: str = "") -> None:
    body = "\n".join(f"VALUE_{n} = {n}" for n in range(lines))
    (repo / "big.py").write_text((marker + body + "\n"), encoding="utf-8")


def init_repo(tmp: Path) -> Path:
    """A git repo the OTHER repo-level rules are already happy with.

    The .gitignore is not incidental: check_gitignore is a whole-tree rule, so a fixture
    without one fails for a reason that has nothing to do with pay-down, and the assertion
    that then fires reads like a pay-down bug. Fixtures have to satisfy every rule they are
    not testing, or they measure the wrong thing.
    """
    repo = tmp / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".env\n.env.*\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    return repo


def scan(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LIMITS), "--root", str(repo), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def make_baselined_repo(tmp: Path, *, lines: int = 900) -> Path:
    """A repo on `main` with one oversized file already recorded in the baseline."""
    repo = init_repo(tmp)
    write_big_file(repo, lines)
    scan(repo, "--write-baseline", "--baseline-consent")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "adopt")
    git(repo, "branch", "-M", "main")
    return repo


def rules_reported(result: subprocess.CompletedProcess[str]) -> set[str]:
    combined = result.stdout + result.stderr
    return {rule for rule in (PAYDOWN_RULE, STALE_RULE, UNKNOWN_RULE) if f"[{rule}]" in combined}


def test_an_in_place_edit_must_still_pay_down() -> None:
    """THE case the plain ratchet permits forever: same size, so `baseline-grew` cannot fire."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_baselined_repo(Path(tmp))
        git(repo, "checkout", "-qb", "feature")
        write_big_file(repo, 900, marker="# edited in place, same line count\n")
        # the marker adds a line, so trim one to hold the count exactly
        text = (repo / "big.py").read_text(encoding="utf-8").splitlines()
        (repo / "big.py").write_text("\n".join(text[:900]) + "\n", encoding="utf-8")
        git(repo, "commit", "-qam", "edit without shrinking")

        without = scan(repo)
        assert without.returncode == 0, (
            "precondition: the plain ratchet must PASS this, otherwise the test is not "
            "measuring the pay-down gate:\n" + without.stdout + without.stderr
        )

        withgate = scan(repo, "--paydown", "main")
        assert PAYDOWN_RULE in rules_reported(withgate), (
            "a branch that edits a baselined file without shrinking it must be refused; "
            f"got:\n{withgate.stdout}{withgate.stderr}"
        )


def test_growing_a_baselined_file_is_still_refused() -> None:
    """The original ratchet must keep working with the gate on."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_baselined_repo(Path(tmp))
        git(repo, "checkout", "-qb", "feature")
        write_big_file(repo, 950)
        git(repo, "commit", "-qam", "grow it")
        assert scan(repo, "--paydown", "main").returncode == 1


def test_paying_down_without_recording_it_is_refused() -> None:
    """Condition 2. Without it the recorded number never moves and the ratchet stops."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_baselined_repo(Path(tmp))
        git(repo, "checkout", "-qb", "feature")
        write_big_file(repo, 800)
        git(repo, "commit", "-qam", "pay down but do not re-record")

        result = scan(repo, "--paydown", "main")
        assert STALE_RULE in rules_reported(result), (
            "shrinking without regenerating the baseline leaves the recorded debt at its "
            f"old value, which the next branch then clears for free. Got:\n{result.stdout}"
        )


def test_paying_down_and_recording_it_passes() -> None:
    """The intended workflow must actually be reachable, or the gate teaches --no-verify."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_baselined_repo(Path(tmp))
        git(repo, "checkout", "-qb", "feature")
        write_big_file(repo, 800)
        scan(repo, "--write-baseline", "--baseline-consent")
        git(repo, "commit", "-qam", "pay down and re-record")

        result = scan(repo, "--paydown", "main")
        assert result.returncode == 0, (
            "shrink + re-record is the whole point and must pass:\n" + result.stdout + result.stderr
        )


def test_paying_the_file_all_the_way_off_passes() -> None:
    """The BEST outcome -- debt gone, entry removed -- must not read as a stale baseline."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_baselined_repo(Path(tmp))
        git(repo, "checkout", "-qb", "feature")
        write_big_file(repo, 200)
        scan(repo, "--write-baseline", "--baseline-consent")
        git(repo, "commit", "-qam", "split it properly")

        result = scan(repo, "--paydown", "main")
        assert result.returncode == 0, (
            "a file brought under the limit leaves the baseline entirely; that is the "
            "target, not a stale record:\n" + result.stdout + result.stderr
        )
        recorded = json.loads((repo / BASELINE).read_text(encoding="utf-8"))
        assert recorded["oversizedFiles"] == {}


def test_an_untouched_baselined_file_owes_nothing() -> None:
    """Only files this branch CHANGES owe a payment. Otherwise no branch could ever land."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_baselined_repo(Path(tmp))
        git(repo, "checkout", "-qb", "feature")
        (repo / "unrelated.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "unrelated work")

        assert scan(repo, "--paydown", "main").returncode == 0


def test_an_exempt_file_owes_nothing() -> None:
    """An exemption means "looked at; nothing to fix" -- the pack must not then argue.

    Demanding a payment from an exempt file would push people to delete the marker, which
    is the one thing making the decision visible.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = init_repo(Path(tmp))
        marker = (
            "# standards: file-length exempt -- declarative lookup table; its length "
            "tracks the number of rows, not accumulated responsibility.\n"
        )
        write_big_file(repo, 900, marker=marker)
        scan(repo, "--write-baseline", "--baseline-consent")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "adopt")
        git(repo, "branch", "-M", "main")

        git(repo, "checkout", "-qb", "feature")
        write_big_file(repo, 901, marker=marker)
        git(repo, "commit", "-qam", "add a row")

        result = scan(repo, "--paydown", "main")
        assert PAYDOWN_RULE not in rules_reported(result), (
            "an exempt file states its reason in the file and is printed every run; the "
            f"pay-down gate must respect that. Got:\n{result.stdout}{result.stderr}"
        )


def test_a_danish_filename_is_not_dropped_from_the_gate() -> None:
    """The silent half of this gate, and the one no other test could see.

    `git diff --name-only` QUOTES and escapes any path holding a non-ASCII character, so
    `længde.py` came back as a C-escaped string that matches nothing on disk -- the file
    fell out of `changed_files`, owed no pay-down, and the branch went green. standards_git's
    docstring already recorded exactly this and fixed it with `-z`; this module had its own
    git runner and did not. The estate is Danish, so this is the normal case, not an edge one.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = init_repo(Path(tmp))
        name = "l" + chr(0xE6) + "ngde.py"  # "længde.py", spelled without a literal
        body = "\n".join(f"VALUE_{n} = {n}" for n in range(900)) + "\n"
        (repo / name).write_text(body, encoding="utf-8")
        scan(repo, "--write-baseline", "--baseline-consent")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "adopt")
        git(repo, "branch", "-M", "main")

        git(repo, "checkout", "-qb", "feature")
        (repo / name).write_text(
            "# edited, still 900 lines\n" + body[: body.rfind("\n", 0, -1)] + "\n", encoding="utf-8"
        )
        git(repo, "commit", "-qam", "edit without shrinking")

        result = scan(repo, "--paydown", "main")
        assert PAYDOWN_RULE in rules_reported(result), (
            "a baselined file with a Danish name must owe a pay-down like any other; if it "
            f"does not, git quoted the path and the gate never saw it. Got:\n"
            f"{result.stdout}{result.stderr}"
        )


def test_an_unresolvable_base_ref_fails_rather_than_skips() -> None:
    """A gate that cannot run is not a gate that passed."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_baselined_repo(Path(tmp))
        result = scan(repo, "--paydown", "origin/does-not-exist")
        assert UNKNOWN_RULE in rules_reported(result), f"got:\n{result.stdout}{result.stderr}"
        assert result.returncode == 1


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "paydown"))

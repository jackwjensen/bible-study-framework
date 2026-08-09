#!/usr/bin/env python3
"""PreToolUse hook: block `git commit` when the repo's verification is red.

Claude Code pipes the pending tool call to this script as JSON on stdin. If the call is
a Bash `git commit`, the repo's own gates run first; a non-zero gate exits 2, which
Claude Code treats as "deny the tool call" and shows the stderr text as the reason.

Pushes auto-deploy to production, so a red commit is the last cheap place to stop.

Exit codes: 0 = allow, 2 = block (stderr is shown to Claude).

Source of truth: engineering-standards/scripts/precommit-gate.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# `git commit` in any form, but not `git commit --help` / log/status noise that merely
# mentions the word. Anchored on the git invocation so `echo "git commit"` won't match.
GIT_COMMIT_PATTERN = re.compile(r"(^|[;&|]\s*)git\s+(-[^\s]+\s+)*commit\b")

GATE_TIMEOUT_SECONDS = 900

# `python -m ruff` when ruff is not installed. Python spells this exactly one way, and it
# cannot be produced by a lint finding, so matching the phrase is safe.
MODULE_MISSING_PATTERN = re.compile(r"No module named '?\w+'?")

# One check: its name, the command, and the directory to run it in. The directory matters
# for a monorepo, where a nested Python project's config only applies from its own root.
Gate = tuple[str, list[str], Path]

# How deep to look for a nested `pyproject.toml`, and what never to look inside.
PYTHON_PROJECT_SEARCH_DEPTH = 3
VENDOR_DIRECTORIES = frozenset(
    {".git", ".venv", "venv", "node_modules", "site-packages", "__pycache__", "build", "dist"}
)

# A rule tag as check-source-limits.py prints it: `path:12: [unset-not-zero] message`.
# Matched by SHAPE rather than by an enumerated list of rule names. The list version of
# this named four rules and silently under-reported the other six: when a commit tripped
# both a listed and an unlisted rule, only the listed lines were shown, so the developer
# fixed those, re-committed, and got blocked again by findings that had been there all
# along. Adding a rule to standards_checks.py must not require remembering to edit this.
SCANNER_RULE_TAG = re.compile(r"\[[a-z][a-z0-9-]*\]")

# Shell tools that can run `git commit`. PowerShell matters as much as Bash: on Windows the
# standing convention is to prefer the PowerShell tool, so gating only on Bash leaves the
# commit gate decorative on exactly the machine it most needs to run on.
SHELL_TOOL_NAMES = ("Bash", "PowerShell")


def find_repo_root(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def build_gates(repo_root: Path) -> list[Gate]:
    """The verification commands that apply to this repo, in cheapest-first order."""
    gates: list[Gate] = []

    limits_script = repo_root / "scripts" / "check-source-limits.py"
    if limits_script.is_file():
        gates.append((
            "source limits",
            [sys.executable, str(limits_script), "--root", str(repo_root)],
            repo_root,
        ))

    if any(repo_root.glob("*.sln")):
        gates.append(("dotnet build", ["dotnet", "build", "--nologo", "-warnaserror"], repo_root))
        if any(repo_root.rglob("*Tests*.csproj")):
            gates.append(("dotnet test", ["dotnet", "test", "--nologo", "--no-build"], repo_root))

    package_json = repo_root / "package.json"
    if package_json.is_file():
        scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})
        runner = js_runner(repo_root)
        if "type-check" in scripts:
            gates.append(("type-check", [runner, "run", "type-check"], repo_root))
        if "lint" in scripts:
            gates.append(("lint", [runner, "run", "lint"], repo_root))

    gates += python_gates(repo_root)

    return gates


def python_gates(repo_root: Path) -> list[Gate]:
    """ruff and pytest, for every Python project in the repo that has declared them.

    These were missing, and their absence made the gate materially weaker than the CI job
    on exactly the repos it mattered for: a Python repo got `check-source-limits` and
    nothing else, while `ci/check-node-python.yml` and every repo's own documented
    verification command run `ruff check .` and `pytest`. A JS repo had its linter gated
    and a Python repo did not, which is not a decision anyone made.

    Gated on the project *declaring* the tool rather than on the tool being importable: a
    declaration is the repo saying it wants this enforced, and it cannot be satisfied by
    an incomplete local environment. A missing interpreter or module is handled by
    `run_gate`, which skips rather than blocks -- see the note there on why that direction
    is the safe one for a pre-commit hook.

    **Per project, not per repo.** allegro-it-services keeps its Django half in
    `packages/backend/`, so a root-only check would have gated the TypeScript and left the
    Python ungated -- reintroducing the very asymmetry this function exists to remove, in
    the one repo where it is least visible.
    """
    gates: list[Gate] = []
    for project in python_projects(repo_root):
        declared = (project / "pyproject.toml").read_text(encoding="utf-8")
        interpreter = python_interpreter(project, repo_root)
        where = "" if project == repo_root else f" ({project.relative_to(repo_root)})"

        if "[tool.ruff" in declared:
            gates.append((f"ruff{where}", [interpreter, "-m", "ruff", "check", "."], project))

        # Last, and deliberately: it is the only gate that costs real seconds, so
        # everything cheap has already had its say by the time a commit waits on a suite.
        if "[tool.pytest" in declared or (project / "tests").is_dir():
            gates.append(
                (f"pytest{where}", [interpreter, "-m", "pytest", "-q", "-x"], project)
            )

    return gates


def python_projects(repo_root: Path) -> list[Path]:
    """Every directory holding a `pyproject.toml`, root or nested.

    Walked with pruning rather than `rglob`, because `rglob` descends into `node_modules`
    and `.venv` -- tens of thousands of directories, several of which contain a
    `pyproject.toml` belonging to a dependency. Gating a commit on a third-party package's
    test suite would be absurd, and slow enough to notice.
    """
    found: list[Path] = []
    for directory, subdirectories, filenames in os.walk(repo_root):
        here = Path(directory)
        if len(here.relative_to(repo_root).parts) >= PYTHON_PROJECT_SEARCH_DEPTH:
            subdirectories.clear()
        subdirectories[:] = [
            name for name in subdirectories if name not in VENDOR_DIRECTORIES
        ]
        if "pyproject.toml" in filenames:
            found.append(here)
    return sorted(found)


def python_interpreter(project: Path, repo_root: Path) -> str:
    """The project's own virtualenv where it has one, else the repo's, else this one.

    A project's ruff and pytest live in its venv, and `sys.executable` here is the
    interpreter the *hook* was started with -- on Windows usually a system Python that has
    neither. Running that one would report "No module named ruff" against a perfectly
    healthy repo, so a venv is preferred wherever one exists. The project is checked before
    the repo root because a monorepo's backend owns its own environment.
    """
    candidates = [
        base / name / executable
        for base in dict.fromkeys((project, repo_root))
        for name in (".venv", "venv")
        for executable in (Path("Scripts") / "python.exe", Path("bin") / "python")
    ]
    found = next((path for path in candidates if path.is_file()), None)
    return str(found) if found else sys.executable


def js_runner(repo_root: Path) -> str:
    """The package manager this repo actually uses, decided by its lockfile.

    Hardcoding `pnpm` was wrong twice over. On a machine without pnpm it raises
    FileNotFoundError, which run_gate treats as "toolchain absent" and skips -- so an
    npm repo's lint and type-check gates vanish silently, which is the failure mode this
    whole pack exists to prevent. And where pnpm *is* installed it runs anyway, against a
    node_modules another manager laid out.

    `run` is always passed explicitly: `npm type-check` is not a command, and relying on
    pnpm's bare-script shorthand is what made the two look interchangeable.
    """
    lockfiles = (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("package-lock.json", "npm"),
    )
    name = next(
        (runner for lockfile, runner in lockfiles if (repo_root / lockfile).is_file()),
        "npm",
    )
    # These ship as .cmd shims on Windows; the bare name is not executable without a
    # shell, so subprocess would raise FileNotFoundError and skip the gate silently.
    return f"{name}.cmd" if os.name == "nt" else name


def run_gate(name: str, command: list[str], working_directory: Path) -> str | None:
    """Run one gate. Returns None on success, or a short failure description."""
    try:
        completed = subprocess.run(
            command,
            cwd=working_directory,
            capture_output=True,
            text=True,
            timeout=GATE_TIMEOUT_SECONDS,
            shell=False,
        )
    except FileNotFoundError:
        # The toolchain isn't installed here; CI still enforces it. Don't block on it.
        return None
    except subprocess.TimeoutExpired:
        return f"{name}: timed out after {GATE_TIMEOUT_SECONDS}s"

    if completed.returncode == 0:
        return None

    output = (completed.stdout or "") + (completed.stderr or "")

    # Same reasoning as the FileNotFoundError above, for a tool invoked as `python -m x`:
    # that spelling turns "not installed" into an ordinary non-zero exit rather than a
    # missing executable, so without this a machine that has never run `pip install -e .`
    # would be unable to commit anything. CI still enforces it. Matched narrowly -- the
    # phrase only appears when the module itself is absent, never in a real finding.
    if MODULE_MISSING_PATTERN.search(output):
        return None

    interesting = [
        line for line in output.splitlines()
        if ": error " in line or SCANNER_RULE_TAG.search(line)
    ]
    detail = "\n".join(interesting[:15]) or "\n".join(output.splitlines()[-15:])
    return f"{name}: FAILED\n{detail}"


def read_payload() -> dict | None:
    """The pending tool call, or None if stdin carried nothing usable.

    Read as BYTES and decoded explicitly, never `json.load(sys.stdin)`. Text-mode stdin
    decodes with the process's locale encoding -- cp1252 on a Danish Windows box -- which
    mangles any non-ASCII in the command string, and it does not strip a UTF-8 BOM.

    The BOM is not hypothetical: piping the payload from PowerShell prepends EF BB BF,
    json.load raises, and the old code returned 0 for it. That is the worst possible
    failure for a gate -- it allows every commit, reports nothing, and looks exactly like
    a passing hook. This function makes the parse survive both, and makes a genuine
    failure LOUD instead of silent.
    """
    # Decode BEFORE testing for emptiness: `bytes.strip()` removes ASCII whitespace but
    # leaves the three BOM bytes, so a BOM-only stdin looks like content and reaches the
    # parser as garbage. Decoding with utf-8-sig consumes the BOM first.
    text = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace").strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        print(
            "precommit-gate: stdin was not valid JSON, so this tool call could not be "
            "checked. The commit gate is NOT protecting this call.",
            file=sys.stderr,
        )
        return None


def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0

    if payload.get("tool_name") not in SHELL_TOOL_NAMES:
        return 0

    command = payload.get("tool_input", {}).get("command", "")
    if not GIT_COMMIT_PATTERN.search(command):
        return 0

    if os.environ.get("SKIP_STANDARDS_GATE") == "1":
        return 0

    repo_root = find_repo_root(Path.cwd())
    if repo_root is None:
        return 0

    failures = [
        failure
        for name, gate_command, working_directory in build_gates(repo_root)
        if (failure := run_gate(name, gate_command, working_directory)) is not None
    ]

    if not failures:
        return 0

    print(
        "Commit blocked: the repo's verification is red. Fix these, then commit again.\n\n"
        + "\n\n".join(failures)
        + "\n\n(Set SKIP_STANDARDS_GATE=1 only when the failure is provably unrelated.)",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())

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


def build_gates(repo_root: Path) -> list[tuple[str, list[str]]]:
    """The verification commands that apply to this repo, in cheapest-first order."""
    gates: list[tuple[str, list[str]]] = []

    limits_script = repo_root / "scripts" / "check-source-limits.py"
    if limits_script.is_file():
        gates.append(("source limits", [sys.executable, str(limits_script), "--root", str(repo_root)]))

    if any(repo_root.glob("*.sln")):
        gates.append(("dotnet build", ["dotnet", "build", "--nologo", "-warnaserror"]))
        if any(repo_root.rglob("*Tests*.csproj")):
            gates.append(("dotnet test", ["dotnet", "test", "--nologo", "--no-build"]))

    package_json = repo_root / "package.json"
    if package_json.is_file():
        scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})
        # pnpm ships as a .cmd shim on Windows; the bare name is not executable without a
        # shell, and subprocess would raise FileNotFoundError and skip the gate silently.
        pnpm = "pnpm.cmd" if os.name == "nt" else "pnpm"
        if "type-check" in scripts:
            gates.append(("type-check", [pnpm, "type-check"]))
        if "lint" in scripts:
            gates.append(("lint", [pnpm, "lint"]))

    return gates


def run_gate(name: str, command: list[str], repo_root: Path) -> str | None:
    """Run one gate. Returns None on success, or a short failure description."""
    try:
        completed = subprocess.run(
            command,
            cwd=repo_root,
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
        for name, gate_command in build_gates(repo_root)
        if (failure := run_gate(name, gate_command, repo_root)) is not None
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

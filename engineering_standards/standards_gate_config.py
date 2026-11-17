"""How a repo declares the way one of its gates is actually run.

Everything in `standards_gates` assumes a project's tools are runnable on this machine:
it finds a virtualenv, or falls back to the interpreter that started the hook. That
assumption is simply false for a project whose environment lives in a container, and the
failure it produces is the worst kind — allegro-it-services keeps its Django dependencies
in `__pypackages__` INSIDE the backend image, so the discovered interpreter was a host
Python that happened to have pytest and none of the application's dependencies. The gate
failed on `ImproperlyConfigured` on every push, for everyone, about nothing anybody had
written. ruff kept passing there, which is what hid it: ruff needs no application
environment.

Reaching for `SKIP_STANDARDS_GATE=1` is not the answer to that, and this module exists so
nobody has to. The hatch disables EVERY gate, so routing around one broken one silently
stops enforcing all the rest — on precisely the push where verification mattered.

Shape, in `.standards.json`, keyed by the project's repo-relative path (`.` for the root):

    "gateCommands": {
      "packages/backend": {
        "pytest": {
          "command": ["docker", "compose", "exec", "-T", "backend",
                      "python", "-m", "pytest", "-q", "-x"],
          "runFrom": "."
        }
      }
    }

`runFrom` is repo-relative and defaults to the project. It exists because a container
command usually has to run where the compose file is, not where the code is.

The same file also tunes the hang guard: `gateTimeoutSeconds` is how long one gate may run
in this repo before it is killed. See `gate_timeout_seconds` below, and the default's
rationale in `standards_config.DEFAULT_GATE_TIMEOUT_SECONDS`.
"""

from __future__ import annotations

import json
from pathlib import Path

from standards_config import DEFAULT_GATE_TIMEOUT_SECONDS, GATE_TIMEOUT_KEY

GATE_OVERRIDES_KEY = "gateCommands"


def gate_overrides(repo_root: Path) -> dict:
    """The declarations for this repo, or an empty mapping when there are none."""
    try:
        with (repo_root / ".standards.json").open(encoding="utf-8") as handle:
            return json.load(handle).get(GATE_OVERRIDES_KEY, {}) or {}
    except OSError, json.JSONDecodeError:
        # Absent or unreadable config means "no overrides", which is the normal case for
        # every repo. A malformed file is already reported loudly by check-source-limits.
        return {}


def project_overrides(repo_root: Path, project: Path, overrides: dict) -> dict:
    """The declarations for one project, keyed by its repo-relative path."""
    key = "." if project == repo_root else project.relative_to(repo_root).as_posix()
    return overrides.get(key, {})


def gate_timeout_seconds(repo_root: Path) -> int:
    """How long one gate may run in this repo, in seconds.

    An absent or unreadable config means the default, same as `gate_overrides` -- a broken
    file must not stop the gates running. A PRESENT but invalid value is refused loudly
    instead: whoever wrote the key meant something, and silently running on 900 would hide
    that the request was ignored -- in the one direction nobody notices until a push fails.
    Zero and negatives are refused for the same reason the scanner's config refuses
    `maxFileLines: 100000`: past the edge it is not a tuning, it is the gates switched off
    (or failed outright) while still looking configured.
    """
    try:
        with (repo_root / ".standards.json").open(encoding="utf-8") as handle:
            declared = json.load(handle).get(GATE_TIMEOUT_KEY)
    except OSError, json.JSONDecodeError:
        return DEFAULT_GATE_TIMEOUT_SECONDS
    if declared is None:
        return DEFAULT_GATE_TIMEOUT_SECONDS
    if isinstance(declared, bool) or not isinstance(declared, int) or declared <= 0:
        raise SystemExit(
            f'error: .standards.json sets "{GATE_TIMEOUT_KEY}" to {declared!r}, which is not a '
            "positive number of seconds.\n"
            "  The gates did not run -- fix the value, or remove the key to take the default "
            f"({DEFAULT_GATE_TIMEOUT_SECONDS} s). Raising it above the default also needs a "
            'reason in "reasons", like every other weakening.'
        )
    return declared

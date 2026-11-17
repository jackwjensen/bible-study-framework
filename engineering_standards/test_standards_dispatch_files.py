#!/usr/bin/env python3
"""Cases for the non-source dispatch table.

These were twelve early returns inside `check_source_file`, where the ordering constraints
between them were a property of which branch happened to sit higher and nothing stated or
tested them. Turning the chain into a table made them expressible; these are the cases that
make them enforced.

Two distinctions carry the weight:

* MATCHED-AND-SILENT is not UNMATCHED. A config file matches, yields nothing, and must still
  stop -- falling through would put the length and naming rules on JSON and INI they have no
  opinion about. `None` and `()` are different answers.
* FIRST MATCH WINS, so a file that two entries could claim goes to the earlier one. Both
  live cases are pinned below: dependabot.yml before the config entry, and pyproject.toml
  before it too.

Run: python test_standards_dispatch_files.py   (or pytest)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_config import CheckConfig  # noqa: E402
from standards_dispatch_files import non_source_rules  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

CONFIG = CheckConfig()


def routed(name: str, *lines: str, root: Path | None = None) -> list | None:
    """The findings for `name`, or None when it is source and the table declined it."""
    repo_root = root or Path.cwd()
    rules = non_source_rules(repo_root / name, list(lines), CONFIG, repo_root)
    return None if rules is None else list(rules)


def test_a_source_file_is_declined_so_the_language_rules_get_it() -> None:
    for name in ("Service.cs", "module.py", "app.tsx", "legacy.php"):
        assert routed(name, "x = 1") is None, f"{name} must fall through to the source rules"


def test_a_config_file_matches_and_yields_nothing() -> None:
    """The distinction the return type exists for: terminal, but with no findings.

    If this returned None the file would fall through to the length and naming rules, which
    have no opinion about JSON or INI -- the bare `return` in the old chain was doing exactly
    this, invisibly.
    """
    routes = routed("appsettings.json", '{"a": 1}')
    assert routes is not None, "a config file must STOP the dispatch"
    assert routes == [], "...while yielding nothing of its own"


def test_dependabot_is_claimed_before_the_config_entry() -> None:
    """A repo spelling it `.yml` would otherwise be swallowed by the silent config entry.

    Asserted by BEHAVIOUR rather than by reading the table: the holdback rule fires on an
    `ignore:` with no reason, so a finding here proves the dependabot entry won.
    """
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        target = root / ".github" / "dependabot.yml"
        target.parent.mkdir(parents=True)
        findings = routed(".github/dependabot.yml", "    ignore:", '      - dependency-name: "MudBlazor"', root=root)
        assert findings is not None
        assert findings, "dependabot.yml must reach the holdback rule, not the silent config entry"


def test_pyproject_reaches_the_python_floor_not_the_config_entry() -> None:
    """pyproject.toml is configuration by any reading, and matching there would silently drop
    one of the Python floor's declaration sites."""
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        findings = routed("pyproject.toml", 'requires-python = ">=3.9"', root=root)
        assert findings is not None
        assert findings, "a requires-python below the floor must be reported"


def test_a_compose_file_is_claimed_and_never_reaches_the_length_rule() -> None:
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        assert routed("docker-compose.yml", "services:", "  web:", "    build: .", root=root) is not None


def test_every_entry_returns_an_iterable_never_a_bare_none() -> None:
    """A handler that forgot to yield would read as 'this file is source' and fall through.

    The failure would be silent and file-type-wide, which is this scanner's worst shape, so
    it is checked structurally rather than trusted to the per-type cases above.
    """
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        for name in (
            "docker-compose.yml",
            ".env.example",
            "App.csproj",
            "Dockerfile",
            ".nvmrc",
            ".python-version",
            "composer.json",
            "requirements.txt",
            "package.json",
            "appsettings.json",
        ):
            assert routed(name, "", root=root) is not None, f"{name} must be claimed by the table"


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "non-source dispatch"))

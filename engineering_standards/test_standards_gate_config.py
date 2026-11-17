#!/usr/bin/env python3
"""Cases for gate-command overrides: how a repo declares a different way to run a gate.

Written on 2026-09-01 after `tests-uncovered-module` was tightened to stop accepting a bare
name as coverage. This module had passed the loose version because the word appeared in
another suite's prose, which is exactly the false negative the tightening removed.

The behaviour worth pinning is the FAILURE mode, not the happy path: every error here
resolves to "no overrides", so a typo in `.standards.json` does not stop a gate running --
it silently runs the default one. That is the right direction (a broken config must not
disable verification) and it is invisible, so it needs a test saying it is deliberate.

Run: pytest test_standards_gate_config.py
"""

from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_gate_config import (  # noqa: E402
    DEFAULT_GATE_TIMEOUT_SECONDS,
    gate_overrides,
    gate_timeout_seconds,
    project_overrides,
)


@contextmanager
def repo(config: str | None = None) -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        if config is not None:
            (root / ".standards.json").write_text(config, encoding="utf-8")
        yield root


def test_declared_overrides_are_returned():
    with repo('{"gateCommands": {".": {"pytest": "uv run pytest"}}}') as root:
        assert gate_overrides(root) == {".": {"pytest": "uv run pytest"}}


def test_a_repo_with_no_config_has_no_overrides():
    """The normal case for every repo: absence is not an error."""
    with repo() as root:
        assert gate_overrides(root) == {}


def test_a_config_without_the_key_has_no_overrides():
    with repo('{"maxFileLines": 400}') as root:
        assert gate_overrides(root) == {}


def test_malformed_json_degrades_to_no_overrides_rather_than_raising():
    """A broken config must not stop the gates running -- it runs the DEFAULT command.

    Deliberate, and worth stating: the alternative is a typo in `.standards.json` disabling
    verification, which fails in the direction nobody notices. check-source-limits reports
    the malformed file loudly, so the error is not lost.
    """
    with repo('{"gateCommands": {') as root:
        assert gate_overrides(root) == {}


def test_a_null_key_is_treated_as_absent():
    """`or {}` in the reader: an explicit null must not become a None nobody can index."""
    with repo('{"gateCommands": null}') as root:
        assert gate_overrides(root) == {}


def test_the_repo_root_is_keyed_as_a_dot():
    with repo() as root:
        overrides = {".": {"pytest": "uv run pytest"}}
        assert project_overrides(root, root, overrides) == {"pytest": "uv run pytest"}


def test_a_subproject_is_keyed_by_its_posix_relative_path():
    """POSIX separators regardless of platform, so one config works on Windows and Linux."""
    with repo() as root:
        overrides = {"packages/backend": {"pytest": "poetry run pytest"}}
        project = root / "packages" / "backend"
        assert project_overrides(root, project, overrides) == {"pytest": "poetry run pytest"}


def test_a_project_with_no_declaration_gets_an_empty_mapping():
    with repo() as root:
        assert project_overrides(root, root / "apps" / "web", {".": {"x": "y"}}) == {}


def test_the_timeout_defaults_when_nothing_declares_one():
    with repo() as root:
        assert gate_timeout_seconds(root) == DEFAULT_GATE_TIMEOUT_SECONDS
    with repo('{"maxFileLines": 400}') as root:
        assert gate_timeout_seconds(root) == DEFAULT_GATE_TIMEOUT_SECONDS


def test_a_declared_timeout_is_returned():
    with repo('{"gateTimeoutSeconds": 1800}') as root:
        assert gate_timeout_seconds(root) == 1800


def test_a_malformed_config_degrades_to_the_default_timeout():
    """Same direction as gate_overrides: a typo in the FILE must not stop the gates running."""
    with repo('{"gateTimeoutSeconds": ') as root:
        assert gate_timeout_seconds(root) == DEFAULT_GATE_TIMEOUT_SECONDS


def test_a_present_but_invalid_timeout_is_refused_loudly():
    """The opposite direction from a malformed file, deliberately: the KEY was written, so
    somebody meant something, and silently running on the default would hide that the
    request was ignored. Zero would fail every gate -- the gates switched off backwards.

    A plain loop rather than pytest.mark.parametrize: standards_selftest runs these files
    where pytest is absent, and it calls each test with no arguments.
    """
    for declared in ("0", "-60", '"900"', "true", "12.5"):
        with repo(f'{{"gateTimeoutSeconds": {declared}}}') as root:
            try:
                gate_timeout_seconds(root)
            except SystemExit as refused:
                assert "gateTimeoutSeconds" in str(refused), f"the refusal must name the key; got: {refused}"
            else:
                raise AssertionError(f"a declared {declared} must be refused, not defaulted")


if __name__ == "__main__":
    from standards_selftest import run_module_tests

    # globals() and a label, not the module object: the runner iterates a namespace dict.
    # The old call died on a TypeError the moment anyone ran this file directly -- loudly,
    # at least, which is why it could sit unnoticed next to a working pytest path.
    raise SystemExit(run_module_tests(globals(), "gate config"))

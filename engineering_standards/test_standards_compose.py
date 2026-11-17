"""Cases for the deployment-contract rules in standards_compose.py.

This module had no tests at all until 2026-08-18, which is how `compose-production-ports`
came to be missing: `check_compose_ports` *documented* the convention -- "a production
overlay publishes nothing, so it simply has no lines to match here" -- and then trusted it.
An assumption stated in a docstring is not a check, and the shape it assumed away is exactly
the one that put root MySQL on 0.0.0.0 of a public VPS for months.

The negatives matter as much as the positives here. A rule that fires on a correctly-reset
overlay would be exempted wholesale within a week, and an exempted rule is not running.
"""

from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from standards_compose import (
    check_compose_ports,
    check_overlay_name,
    check_production_ports,
)
from standards_selftest import run_module_tests

BASE = Path("docker-compose.yml")
OVERLAY = Path("docker-compose.production.yml")


@contextmanager
def repo_with_base(*base_lines: str) -> Iterator[Path]:
    """A repo root holding a base compose file with the given body."""
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "docker-compose.yml").write_text("\n".join(base_lines), encoding="utf-8")
        yield root


def production_findings(root: Path, *overlay_lines: str) -> list[str]:
    return [violation.message for violation in check_production_ports(root / OVERLAY, list(overlay_lines), root)]


PUBLISHING_BASE = (
    "services:",
    "  web:",
    "    container_name: app-web",
    "    ports:",
    '      - "8080:8080"',
    "  mysql:",
    "    container_name: app-db",
    "    ports:",
    '      - "127.0.0.1:3307:3306"',
)


# ---- positives ----------------------------------------------------------------------------


def test_catches_a_service_the_overlay_forgets_to_reset() -> None:
    """The InvoTrack shape: mysql reset, the app service silently inherited 0.0.0.0:8080."""
    with repo_with_base(*PUBLISHING_BASE) as root:
        found = production_findings(
            root,
            "services:",
            "  mysql:",
            "    ports: !override []",
        )
    assert len(found) == 1
    assert "web" in found[0]


def test_catches_an_overlay_that_resets_nothing_at_all() -> None:
    with repo_with_base(*PUBLISHING_BASE) as root:
        found = production_findings(
            root,
            "services:",
            "  web:",
            "    restart: unless-stopped",
        )
    assert len(found) == 2


def test_catches_an_overlay_that_publishes_a_port_itself() -> None:
    with repo_with_base("services:", "  web:", "    container_name: app-web") as root:
        found = production_findings(
            root,
            "services:",
            "  web:",
            "    ports:",
            '      - "8080:8080"',
        )
    assert found and "8080" in found[0]


# ---- negatives ------------------------------------------------------------------------------


def test_silent_when_every_published_service_is_reset() -> None:
    with repo_with_base(*PUBLISHING_BASE) as root:
        assert not production_findings(
            root,
            "services:",
            "  web:",
            "    ports: !override []",
            "  mysql:",
            "    ports: !override []",
        )


def test_accepts_the_reset_tag_spelling() -> None:
    with repo_with_base(*PUBLISHING_BASE) as root:
        assert not production_findings(
            root,
            "services:",
            "  web:",
            "    ports: !reset null",
            "  mysql:",
            "    ports: !reset []",
        )


def test_silent_when_the_base_publishes_nothing() -> None:
    with repo_with_base("services:", "  web:", "    container_name: app-web") as root:
        assert not production_findings(root, "services:", "  web:", "    restart: always")


def test_silent_on_the_base_file_itself() -> None:
    """The base file is SUPPOSED to publish -- that is what development connects to."""
    with repo_with_base(*PUBLISHING_BASE) as root:
        assert not [v.message for v in check_production_ports(root / BASE, list(PUBLISHING_BASE), root)]


def test_silent_when_there_is_no_base_file() -> None:
    """A standalone overlay in a repo that is not a compose stack is not our business."""
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        assert not production_findings(root, "services:", "  web:", "    ports: !override []")


def test_does_not_mistake_a_nested_key_for_a_service() -> None:
    """`ports:` and `environment:` are 4-space keys; only 2-space keys are services."""
    with repo_with_base(*PUBLISHING_BASE) as root:
        found = production_findings(
            root,
            "services:",
            "  web:",
            "    ports: !override []",
            "    environment:",
            "      PORTS: none",
            "  mysql:",
            "    ports: !override []",
        )
    assert not found


# ---- the rules that already existed, now actually covered -----------------------------------


def test_compose_port_still_flags_an_unassigned_host_port() -> None:
    found = [v.rule for v in check_compose_ports(BASE, ['      - "9999:80"'])]
    assert found == ["compose-port"]


def test_compose_port_still_flags_a_datastore_on_its_default_port() -> None:
    found = [v.rule for v in check_compose_ports(BASE, ['      - "3306:3306"'])]
    assert found == ["compose-port"]


def test_compose_port_accepts_the_registry() -> None:
    assert not list(check_compose_ports(BASE, ['      - "8080:8080"', '      - "127.0.0.1:3307:3306"']))


def test_overlay_name_still_flags_the_prod_spelling() -> None:
    found = [v.rule for v in check_overlay_name(Path("docker-compose.prod.yml"), [])]
    assert found == ["compose-overlay-name"]


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "compose deployment-contract cases"))

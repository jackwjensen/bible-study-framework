"""Cases for the compose-read-only rule.

The negatives decide whether this rule is keepable. It reads every compose file in the
estate, and the services it must stay silent on -- mysql, redis, mailpit -- are exactly the
ones where read_only commonly prevents the container starting at all. If it fired on those,
every finding's only correct resolution would be an exemption, and a rule whose findings are
all exemptions is noise that gets switched off.

Run: pytest test_standards_readonly.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from standards_core import CheckConfig
from standards_deploy import check_compose_read_only
from standards_dispatch import check_source_file
from standards_selftest import run_module_tests

BASE = Path("docker-compose.yml")
OVERLAY = Path("docker-compose.production.yml")


def findings(path: Path, *lines: str) -> list[str]:
    return [v.message for v in check_compose_read_only(path, list(lines))]


def service(name: str, *body: str) -> list[str]:
    return ["services:", f"  {name}:", *[f"    {line}" for line in body]]


# ---- positives ------------------------------------------------------------------------------


def test_catches_a_built_service_without_read_only() -> None:
    assert findings(BASE, *service("web", "build:", "  context: .", "ports:", '  - "8080:8080"'))


def test_the_message_names_the_service() -> None:
    message = findings(BASE, *service("web", "build: ."))[0]
    assert "'web'" in message


def test_the_message_warns_the_failure_is_late() -> None:
    """The whole risk of this change: it breaks at first write, not at startup."""
    assert "LATE" in findings(BASE, *service("web", "build: ."))[0]


def test_the_message_names_tmpfs() -> None:
    assert "tmpfs" in findings(BASE, *service("web", "build: ."))[0]


def test_catches_the_second_of_two_built_services() -> None:
    """sourcetext.ai builds two. A rule that only checks the first would miss half."""
    lines = [
        "services:",
        "  web:",
        "    build: .",
        "    read_only: true",
        "  api:",
        "    build: ./api",
    ]
    assert len(findings(BASE, *lines)) == 1


def test_read_only_false_is_still_a_finding() -> None:
    """Explicitly disabling it is a decision, and a decision needs a written reason."""
    assert findings(BASE, *service("web", "build: .", "read_only: false"))


# ---- negatives -------------------------------------------------------------------------------


def test_quiet_when_read_only_is_set() -> None:
    assert not findings(BASE, *service("web", "build: .", "read_only: true"))


def test_quiet_on_a_third_party_image() -> None:
    """mysql writes to its datadir, /var/run and /tmp before it will serve."""
    assert not findings(BASE, *service("mysql", "image: mysql:8.4", "restart: unless-stopped"))


def test_quiet_on_redis_and_mailpit() -> None:
    for name, image in (("redis", "redis:7-alpine"), ("mailpit", "axllent/mailpit:v1.31.4")):
        assert not findings(BASE, *service(name, f"image: {image}")), name


def test_quiet_on_the_production_overlay() -> None:
    """Hardening lives in the base file; the overlay inherits it."""
    assert not findings(OVERLAY, *service("web", "build: ."))


def test_quiet_on_a_top_level_volumes_block() -> None:
    """`volumes:` at column 0 is not a service, and its children are not services."""
    lines = ["volumes:", "  mysql-data:", "  invotrack-keys:"]
    assert not findings(BASE, *lines)


def test_a_nested_build_key_does_not_leak_to_the_next_service() -> None:
    """State must reset per service, or one built service marks every later one."""
    lines = [
        "services:",
        "  web:",
        "    build: .",
        "    read_only: true",
        "  cache:",
        "    image: redis:7-alpine",
    ]
    assert not findings(BASE, *lines)


def test_a_line_exemption_silences_it() -> None:
    lines = [
        "services:",
        "  # standards: compose-read-only exempt -- the vendored search daemon writes its "
        "index under /var/lib at runtime and has no option to relocate it; revisit if upstream "
        "adds one.",
        "  search:",
        "    build: ./search",
    ]
    assert not findings(BASE, *lines)


def test_four_space_indentation_is_handled() -> None:
    """Both 2- and 4-space styles are in use across the estate."""
    lines = ["services:", "    web:", "        build: ."]
    assert findings(BASE, *lines)


def test_the_dispatcher_reaches_the_rule() -> None:
    """A rule that is correct but unreachable is a bug this pack has shipped before."""
    rules = [
        v.rule for v in check_source_file(BASE, ["services:", "  web:", "    build: ."], CheckConfig(), {}, Path("."))
    ]
    assert "compose-read-only" in rules


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "compose-read-only cases"))

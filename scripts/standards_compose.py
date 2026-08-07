#!/usr/bin/env python3
"""The developer machine's shared state: host ports and container names.

Split out of standards_core.py and standards_checks.py, and the seam is a subject
boundary rather than a line count. Everything else the scanner knows is vocabulary
for reading SOURCE -- what a money name looks like, when a zero is a lie. This file
is about the machine every stack shares: only one Allegro IT app runs at a time, so
the host's port space and container namespace are common property, and a value each
repo picks for itself drifts.

It already did, which is why these rules exist. Two repos both published MySQL on
3306 and could not run together. Container names were set in the production overlay
but not the base file, so the same service answered to one name on the server and a
Compose-generated one on the laptop -- and when parallel sessions competed for
Docker, containers and ports moved under each other.

Rules here: compose-port, compose-container-name.
The prose half is claude/rules/infrastructure.md.

Source of truth: engineering-standards/scripts/standards_compose.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from standards_core import COMPOSE_FILENAME, Violation  # noqa: F401  (re-exported)

# THE FEATURE DEFINES THE PORT, not the implementation behind it. Every app answers on
# 8080 whether it is PHP behind nginx, Blazor, or a Vite dev server; the admin surface is
# 8081 whether it is Django, a second SPA, or a marketing site. One number per job, so a
# browser bookmark keeps working across projects.
#
# Only one app is expected to run at a time -- running two is not a supported case, and
# these numbers deliberately do not try to make it one.
#
#   8080   frontend / the app's main entry point (and where the Cloudflare tunnel points)
#   8081   backend / admin / API / a second web surface
#   1080   mail catcher, browsable inbox
#   1025   mail catcher, the SMTP endpoint the app delivers to
ALLOWED_PUBLISHED_PORTS = frozenset({8080, 8081, 1080, 1025})

# Datastores publish off their default port, and on loopback only. Same principle: 3307 is
# "the app's database" whatever the engine, so one saved connection in a DB client serves
# every project.
#
# Measured on this machine: the standalone MySQL80 service runs on 3360 and Herd's bundled
# MySQL on 3309 -- both already moved off 3306 by hand. 3306 is therefore free today only
# because two hand-edited configs say so, and a reinstall or upgrade resets either to the
# default. Publishing a container there would be betting on that.
LOOPBACK_ONLY_PORTS = {3307: "the app database", 6380: "Redis"}

# A datastore's own default port, mapped to where it belongs instead: the assigned host
# port for that FEATURE, and the words for it. Publishing one of these at all is the
# collision that started this rule -- two repos both took 3306 and could not run together.
#
# Every database engine points at the same 3307, deliberately: the feature is "the app's
# database", not "MySQL". A cache is a different feature and gets its own number, which is
# why this is a mapping rather than a set -- a set told Redis to become the app database.
DEFAULT_DATASTORE_PORTS = {
    3306: (3307, "the app's database"),
    5432: (3307, "the app's database"),
    1433: (3307, "the app's database"),
    27017: (3307, "the app's database"),
    6379: (6380, "the cache / broker"),
}

# A published port in either short or long form:
#   - "8080:80"                 host 8080
#   - "127.0.0.1:3307:3306"     host 3307, bound to loopback
PUBLISHED_PORT = re.compile(
    r"^\s*-\s*[\"']?(?:(?P<bind>[\d.]+):)?(?P<host>\d+):(?P<container>\d+)[\"']?\s*$"
)


def check_compose_ports(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a published host port that is not the one assigned for its purpose.

    The host's port space is shared between every project on this machine, and only one
    Allegro IT stack runs at a time, so a port a repo picks for itself is a decision made
    on behalf of all the others. Two repos both published MySQL on 3306 and could not run
    together; either also collided with a natively installed server.

    A production overlay publishes nothing, so it simply has no lines to match here.
    """
    for line_number, line in enumerate(lines, start=1):
        match = PUBLISHED_PORT.match(line)
        if match is None:
            continue

        host_port = int(match.group("host"))
        bind = match.group("bind")
        is_loopback = bind in ("127.0.0.1", "::1")

        if host_port in LOOPBACK_ONLY_PORTS and not is_loopback:
            yield Violation(
                path=path, line=line_number, rule="compose-port",
                message=(
                    f"port {host_port} ({LOOPBACK_ONLY_PORTS[host_port]}) is published on "
                    f"every interface. Bind it to loopback -- "
                    f'"127.0.0.1:{host_port}:{match.group("container")}" -- so nothing on '
                    f"the network can reach the datastore."
                ),
            )
        elif host_port in DEFAULT_DATASTORE_PORTS:
            assigned, feature = DEFAULT_DATASTORE_PORTS[host_port]
            yield Violation(
                path=path, line=line_number, rule="compose-port",
                message=(
                    f"port {host_port} is a datastore's DEFAULT port. Publish {feature} on "
                    f'"127.0.0.1:{assigned}:{match.group("container")}" whatever the engine '
                    f"behind it, so one saved connection serves every project. On this "
                    f"machine 3306 is free only because MySQL80 (3360) and Herd's MySQL "
                    f"(3309) were both moved off it by hand, and a reinstall resets either."
                ),
            )
        elif not is_loopback and host_port not in ALLOWED_PUBLISHED_PORTS:
            yield Violation(
                path=path, line=line_number, rule="compose-port",
                message=(
                    f"host port {host_port} is not assigned. The feature defines the port: "
                    f"8080 frontend / main entry, 8081 backend / admin / second surface, "
                    f"1080 mail inbox, 1025 mail SMTP. A datastore goes on "
                    f"127.0.0.1:3307 instead. See .claude/rules/infrastructure.md."
                ),
            )


def check_compose_container_names(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a service in the BASE compose file that does not name its container.

    Without it Compose generates `<project>-<service>-<N>`, which depends on the directory,
    the service key and a replica counter -- so the same container answers to one name on
    the server and another on a laptop, and a runbook cannot name it. Setting it only in
    the production overlay, which three repos did, fixes the server and leaves development
    drifting.

    Only the base file is checked: the overlay inherits the name it sets.

    A service is identified by INDENT, measured from the first key under `services:`
    rather than assumed. Matching "an indented key with no value on the line" instead
    reported `build:`, `ports:`, `environment:`, `volumes:`, `healthcheck:` and
    `depends_on:` as services -- 13 findings in one 90-line file, none of them real. The
    depth is what distinguishes a service from its own properties, and only the file can
    say what that depth is, since 2- and 4-space styles are both in use here.
    """
    if path.name.casefold() not in ("docker-compose.yml", "docker-compose.yaml"):
        return

    in_services = False
    service_indent: Optional[int] = None
    service_line = 0
    service_name = ""
    has_container_name = False

    def pending() -> Iterable[Violation]:
        if service_name and not has_container_name:
            yield Violation(
                path=path, line=service_line, rule="compose-container-name",
                message=(
                    f"service '{service_name}' has no container_name, so development gets "
                    f"Compose's generated <project>-{service_name}-N while production has a "
                    f"stable name. Set it here, in the base file: "
                    f"container_name: ${{COMPOSE_PROJECT_NAME}}-<role>."
                ),
            )

    for line_number, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        if re.match(r"^\S", line):                       # a top-level key
            yield from pending()
            service_name = ""
            in_services = line.startswith("services:")
            service_indent = None
            continue

        if not in_services:
            continue

        key_match = re.match(r"^(?P<indent>\s+)(?P<name>[A-Za-z0-9_.-]+):", line)
        if key_match is None:
            continue

        indent = len(key_match.group("indent").expandtabs(2))
        if service_indent is None:
            service_indent = indent                      # the first key sets the depth

        if indent > service_indent:
            if key_match.group("name") == "container_name":
                has_container_name = True
            continue

        yield from pending()
        service_name = key_match.group("name")
        service_line = line_number
        has_container_name = False

    yield from pending()

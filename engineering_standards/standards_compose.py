#!/usr/bin/env python3
"""The deployment contract: how a stack is defined, named, and shipped.

Split out of standards_core.py and standards_checks.py, and the seam is a subject
boundary rather than a line count. Everything else the scanner knows is vocabulary
for reading SOURCE -- what a money name looks like, when a zero is a lie. This file
is about everything AROUND the source: the machine every stack shares, and the
pipeline that puts it on the server.

Every rule here exists because something drifted or broke:

  compose-port            two repos both published MySQL on 3306 and could not run
                          together; InvoTrack published root MySQL on 0.0.0.0 in
                          production for months.
  compose-production-ports  the base file publishes for development and the production
                          overlay must reset it. InvoTrack reset mysql and forgot the app,
                          so a public VPS answered on 0.0.0.0:8080 for months; the rule
                          above could not see it, because a port it considers legal is
                          still illegal in production. Hetzner suspends servers over open
                          ports, so this is availability as well as security.
  compose-container-name  names were set in the production overlay but not the base
                          file, so a service answered to one thing on the server and
                          a Compose-generated one on the laptop.
  compose-overlay-name    the production overlay is docker-compose.production.yml in
                          seven repos and docker-compose.prod.yml in the eighth --
                          same concept, two spellings, so "which file does production
                          load?" stopped having one answer.
  deploy-gate             a deploy job with no `needs:` ships unverified code; before
                          the check jobs existed, everything compiled on the
                          production server AFTER merge. That is the failure this
                          whole pack was built to end.
  env-example-compose     DonorLink's server .env was missing COMPOSE_FILE, so the
                          production overlay never loaded, the base file's 3306 stood,
                          it collided with InvoTrack, and the site was down ~1.5h on
                          2026-05-27. Documenting the variable is the cheap half of
                          never repeating that.

The prose half is claude/rules/infrastructure.md.

Source of truth: engineering-standards/engineering_standards/standards_compose.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from standards_core import Violation, warn_unreadable
from standards_scope import (  # noqa: F401  (re-exported for the dispatcher)
    COMPOSE_FILENAME,
    ENV_EXAMPLE_FILENAME,
    is_workflow,
)

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
PUBLISHED_PORT = re.compile(r"^\s*-\s*[\"']?(?:(?P<bind>[\d.]+):)?(?P<host>\d+):(?P<container>\d+)[\"']?\s*$")


def check_compose_ports(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a published host port that is not the one assigned for its purpose.

    The host's port space is shared between every project on this machine, and only one
    Allegro IT stack runs at a time, so a port a repo picks for itself is a decision made
    on behalf of all the others. Two repos both published MySQL on 3306 and could not run
    together; either also collided with a natively installed server.

    A production overlay publishes nothing -- but that is `compose-production-ports`'
    job to verify, not an assumption this rule gets to make. It said so here for months
    while nothing checked it.
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
                path=path,
                line=line_number,
                rule="compose-port",
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
                path=path,
                line=line_number,
                rule="compose-port",
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
                path=path,
                line=line_number,
                rule="compose-port",
                message=(
                    f"host port {host_port} is not assigned. The feature defines the port: "
                    f"8080 frontend / main entry, 8081 backend / admin / second surface, "
                    f"1080 mail inbox, 1025 mail SMTP. A datastore goes on "
                    f"127.0.0.1:3307 instead. See .claude/rules/infrastructure.md."
                ),
            )


PRODUCTION_OVERLAY_NAMES = (
    "docker-compose.production.yml",
    "docker-compose.production.yaml",
    "docker-compose.prod.yml",
    "docker-compose.prod.yaml",
)

# `ports: !override []` (Compose 2.24+) or `ports: !reset ...` -- either clears what the
# base file published rather than merging with it. Without a tag, Compose APPENDS, which is
# why "just leave ports out of the overlay" does not work: the base's publish survives.
PORTS_CLEARED = re.compile(r"^ports:\s*!(?:override|reset)")


def _service_port_state(lines: list[str]) -> dict[str, dict]:
    """Map each service to whether its block publishes a host port and whether it clears them.

    Line-based, like every other rule here: a compose file carrying `!override` is not
    loadable by yaml.safe_load, and the tag is precisely what this rule needs to see.

    Service depth is MEASURED from the first key under `services:` rather than assumed --
    2- and 4-space styles are both in use, and matching "an indented key" instead would
    read `ports:`, `environment:` and `healthcheck:` as services.
    """
    state: dict[str, dict] = {}
    in_services = False
    service_indent: Optional[int] = None
    current: Optional[str] = None

    for line_number, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        stripped = line.strip()

        if indent == 0:
            in_services = stripped.startswith("services:")
            current = None
            continue
        if not in_services:
            continue

        if service_indent is None:
            service_indent = indent

        if indent == service_indent and stripped.endswith(":"):
            current = stripped[:-1].strip()
            state.setdefault(
                current,
                {"line": line_number, "publishes": False, "clears": False, "ports": []},
            )
            continue
        if current is None:
            continue

        if PORTS_CLEARED.match(stripped):
            state[current]["clears"] = True
        else:
            published = PUBLISHED_PORT.match(line)
            if published:
                state[current]["publishes"] = True
                state[current]["ports"].append(published.group("host"))

    return state


def check_production_ports(path: Path, lines: list[str], repo_root: Path) -> Iterable[Violation]:
    """Flag a production overlay that leaves a development port published.

    The base file publishes so a laptop can reach the stack. Production reaches every
    service over `nginx-proxy-network` by container name, so it needs no host port at all --
    and a host port on a public VPS is reachable from the internet whether or not anything
    is documented as using it. Compose MERGES `ports` by default, so an overlay that simply
    omits the key silently republishes everything the base file declared. Only an explicit
    `ports: !override []` closes it.

    Checked from the overlay, but it has to read the base file to know what was published --
    the whole failure mode is a service the overlay never mentions.
    """
    if path.name.casefold() not in PRODUCTION_OVERLAY_NAMES:
        return

    base_path = next(
        (repo_root / name for name in ("docker-compose.yml", "docker-compose.yaml") if (repo_root / name).is_file()),
        None,
    )
    if base_path is None:
        return

    try:
        base_lines = base_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        warn_unreadable(base_path, error, "the production-ports rule could not compare against the base file")
        return

    overlay = _service_port_state(lines)

    for service, base_state in sorted(_service_port_state(base_lines).items()):
        if not base_state["publishes"]:
            continue
        if overlay.get(service, {}).get("clears"):
            continue

        yield Violation(
            path=path,
            line=overlay.get(service, {}).get("line", 1),
            rule="compose-production-ports",
            message=(
                f"service '{service}' publishes a host port in {base_path.name}, and this "
                f"overlay does not reset it -- so production republishes it on the public "
                f"server. Add `ports: !override []` to '{service}' here. Compose merges "
                f"`ports`, so omitting the key is not the same as clearing it."
            ),
        )

    for service, overlay_state in sorted(overlay.items()):
        if overlay_state["publishes"]:
            yield Violation(
                path=path,
                line=overlay_state["line"],
                rule="compose-production-ports",
                message=(
                    f"service '{service}' publishes host port "
                    f"{', '.join(overlay_state['ports'])} in the production overlay. "
                    f"Production reaches services over nginx-proxy-network by container "
                    f"name and needs no published port; publish nothing here."
                ),
            )


def check_overlay_name(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a production overlay spelled anything other than docker-compose.production.yml.

    Seven repos say `production`; one says `prod`. Same concept, two spellings, so the
    question "which file does production load?" stops having a single answer -- and the
    server's `COMPOSE_FILE` names the file by hand, so a mismatch is silent until a deploy
    loads the wrong shape. DonorLink lost ~1.5h to a `COMPOSE_FILE` problem already.

    Additional environment files (`.ci`, `.preview`) are NOT flagged: they are extra
    environments rather than a second spelling of this one.
    """
    if path.name.casefold() in ("docker-compose.prod.yml", "docker-compose.prod.yaml"):
        yield Violation(
            path=path,
            line=1,
            rule="compose-overlay-name",
            message=(
                "the production overlay is docker-compose.production.yml everywhere else. "
                "Rename it, and update COMPOSE_FILE in .env.example and on the server in "
                "the same change -- COMPOSE_FILE names this file literally."
            ),
        )


def check_deploy_gate(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a deploy job that does not gate on anything.

    This is the pack's founding thesis in one rule. Before the check jobs existed, code
    was first compiled ON THE PRODUCTION SERVER, after merge -- the only standards that
    never rotted were the ones with a CI job gating deploy. A `deploy` job without
    `needs:` ships whatever was pushed.

    Deliberately shallow: it asks whether the job declares a dependency at all, not what
    that dependency runs. A repo can name its gate `check`, `test` or `lint`; what matters
    is that something has to pass first.
    """
    for job in iter_blocks(lines, "jobs:"):
        if not job.name.casefold().startswith("deploy") or "needs" in job.keys:
            continue
        yield Violation(
            path=path,
            line=job.line,
            rule="deploy-gate",
            message=(
                f"job '{job.name}' deploys without `needs:`, so nothing has to pass "
                f"before it ships. Add a check job that runs the repo's gates and make "
                f"this one depend on it. Until CI gated deploy, code was first compiled "
                f"on the production server after merge."
            ),
        )


def check_env_example(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag an .env.example that does not document COMPOSE_FILE / COMPOSE_PROJECT_NAME.

    `.env.example` is the only place a deployer learns which variables the server needs.
    A missing `COMPOSE_FILE` on DonorLink's server meant the production overlay never
    loaded, the base file's `3306:3306` stood, it collided with InvoTrack's MySQL, and the
    rollback hit the same fault -- the site was down ~1.5h on 2026-05-27.

    Mentioning it in a comment counts. The point is that the deployer sees the name, not
    that a value is committed.
    """
    text = "\n".join(lines)
    for variable, why in (
        ("COMPOSE_FILE", "the server opts into the production overlay with it"),
        ("COMPOSE_PROJECT_NAME", "it names every container the stack creates"),
    ):
        if variable not in text:
            yield Violation(
                path=path,
                line=1,
                rule="env-example-compose",
                message=(
                    f"{variable} is not documented here, but {why}. A deployer setting up "
                    f"a fresh server has no other list of what the .env must contain."
                ),
            )


def _is_blank_or_comment(line: str) -> bool:
    """Neither structure nor content -- skipped without changing any state."""
    return not line.strip() or line.lstrip().startswith("#")


@dataclass(frozen=True)
class Block:
    """One named block under a top-level YAML section: its name, first line and nested keys."""

    name: str
    line: int
    keys: dict[str, str]  # nested key -> the text after its colon


def iter_blocks(lines: list[str], section: str) -> Iterable[Block]:
    """Every block under `section` (`services:`, `jobs:`), with the keys declared under it.

    ONE PARSER, shared by every rule that reads a YAML section of named blocks.
    `compose-container-name`, `compose-read-only` and `deploy-gate` each carried a copy of
    this indent state machine -- the same twenty lines, in three places, with three sets of
    edge cases to keep in step. The complexity limit is what surfaced it: all three were
    over, and none was doing anything the others were not. Compose services and workflow
    jobs are the same shape, so the section name is the only thing that differed.

    A service is identified by INDENT, measured from the first key under `services:` rather
    than assumed. Matching "an indented key with no value on the line" instead reported
    `build:`, `ports:`, `environment:`, `volumes:`, `healthcheck:` and `depends_on:` as
    services -- 13 findings in one 90-line file, none of them real. The depth is what
    distinguishes a service from its own properties, and only the file can say what that
    depth is, since 2- and 4-space styles are both in use here.
    """
    blocks: list[Block] = []
    in_section = False
    section_indent: Optional[int] = None
    name = ""
    line_number_of_block = 0
    keys: dict[str, str] = {}

    for line_number, line in enumerate(lines, start=1):
        if _is_blank_or_comment(line):
            continue

        if re.match(r"^\S", line):  # a top-level key
            blocks.append(Block(name, line_number_of_block, keys))
            name, keys = "", {}
            in_section = line.startswith(section)
            section_indent = None
            continue

        if not in_section:
            continue

        key_match = re.match(r"^(?P<indent>\s+)(?P<name>[A-Za-z0-9_.-]+):", line)
        if key_match is None:
            continue

        indent = len(key_match.group("indent").expandtabs(2))
        if section_indent is None:
            section_indent = indent  # the first key sets the depth

        if indent > section_indent:
            # The regex above already matched `name:`, so the split always has a tail.
            keys[key_match.group("name")] = line.split(":", 1)[1]
            continue

        blocks.append(Block(name, line_number_of_block, keys))
        name = key_match.group("name")
        line_number_of_block = line_number
        keys = {}

    blocks.append(Block(name, line_number_of_block, keys))
    # The unnamed placeholders are the state before the first block and between blocks;
    # filtered once here rather than guarded at each of the three append sites.
    return [block for block in blocks if block.name]


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

    for service in iter_blocks(lines, "services:"):
        if "container_name" in service.keys:
            continue
        yield Violation(
            path=path,
            line=service.line,
            rule="compose-container-name",
            message=(
                f"service '{service.name}' has no container_name, so development gets "
                f"Compose's generated <project>-{service.name}-N while production has a "
                f"stable name. Set it here, in the base file: "
                f"container_name: ${{COMPOSE_PROJECT_NAME}}-<role>."
            ),
        )

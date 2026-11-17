#!/usr/bin/env python3
"""Who the deploy logs in as, what it trusts, and who the container runs as.

Split from standards_compose.py rather than added to it. That module was at 468 of the
pack's own 500 lines, and the subject is genuinely different: the compose rules are about
how a stack is DEFINED -- ports, names, which overlay production loads -- while these are
about the identity the pipeline assumes when it reaches the machine, and the identity the
process keeps once it is there.

Every rule here comes from the 2026-08-18 deployment audit of the Allegro IT estate, where
all ten deploy workflows in nine repos shared the same three properties:

  deploy-root-ssh       every workflow logged into production as `root`. A CI key with a
                        root shell means any compromise of the workflow, the key secret, or
                        the third-party SSH action is a full host takeover -- and the action
                        is the part nobody here controls.
  deploy-no-hostkey     none of them set `fingerprint:`, so each trusted whatever answered
                        at the address. Anything able to answer for the host -- a hijacked
                        DNS record, a BGP detour -- collected a credential with write access
                        to every stack on the box.
  container-root-user   the app containers ran as root because `USER` was never set. The
                        .NET and Node runtime images all ship a ready non-root user for
                        exactly this, so the fix is one line and the omission is pure
                        inheritance from the base image.

  compose-read-only     added 2026-08-21, the sibling of the rule above and the last cheap
                        item in the set. Non-root says what the process may DO; read-only
                        says what it may LEAVE BEHIND. It was unavailable in InvoTrack until
                        that day only because the app wrote generated PDFs into its own
                        image; once that write was removed, nothing outside the volumes was
                        written and the setting became free.

  deploy-no-pull        added 2026-09-28. Not one of the ten deploy scripts ever pulled, so
                        the server kept the first copy of every tag it had downloaded:
                        `mysql:8.4` was seven months old and `nginx:1.27-alpine` seventeen,
                        while the tags upstream had moved on with their security patches.
                        Since 2026-10-02 tags are pinned exactly and moved by the dependency
                        gate; the pull is still what fetches a newly pinned tag onto the box,
                        and what picks up an official image rebuilt under the same tag.

None of the first three had ever failed, which is the point: they are not bug reports, they
are the properties that decide how bad an unrelated bug is allowed to get.

The prose half -- the migration recipe, and the honest framing of what a docker-group
deploy user does and does not buy -- is claude/rules/publishing.md.

Source of truth: engineering-standards/engineering_standards/standards_deploy.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from standards_compose import iter_blocks
from standards_core import Violation
from standards_exemptions import line_exemption_reason

# Any SSH-over-CI action, not just the one the estate happens to use. `appleboy/ssh-action`
# is what all ten workflows run, but `garygrossgarten/github-action-ssh` and friends have
# the same shape, and a rule that knew only one vendor would go quiet the moment somebody
# switched. The SHA-pinned form (`uses: appleboy/ssh-action@0ff4204...`) still matches,
# because the match is on the action name and not on what follows the @.
SSH_ACTION = re.compile(r"^\s*(?:-\s*)?uses:\s*\S*ssh-action\S*", re.IGNORECASE)

USERNAME_INPUT = re.compile(r"^\s*username:\s*(?P<value>.+?)\s*$", re.IGNORECASE)

# Matches a bare `root` and the template-default form `${{ vars.DEPLOY_USER || 'root' }}`,
# which is how a repo ships root as the value a clone inherits without ever writing
# `username: root`. wordpress-docker-template did exactly that, and a rule reading only the
# literal would have called it clean.
ROOT_VALUE = re.compile(r"""^["']?root["']?$|\|\|\s*["']root["']""", re.IGNORECASE)

# The host-key pin. Only its PRESENCE is checked, never its value: the correct value is a
# property of the server, which the scanner cannot see, and a rule that guessed would be
# wrong in the one direction that matters -- see the ECDSA note in the message below.
FINGERPRINT_INPUT = re.compile(r"^\s*fingerprint:\s*\S", re.IGNORECASE)

# A step boundary: a list item introducing a new step. Used to decide where an ssh-action
# step's inputs stop, so a `fingerprint:` belonging to a LATER step -- or to a second deploy
# target in the same workflow -- cannot be read as satisfying an earlier one.
STEP_BOUNDARY = re.compile(r"^\s*-\s+(?:name|uses|id|run):", re.IGNORECASE)

# `docker compose` and the legacy `docker-compose`, with any global flags (`-p x`, `-f a.yml`)
# between the binary and the subcommand.
_COMPOSE = r"\bdocker(?:\s+|-)compose\b[^#\n]*?\s"

# A line that BUILDS an image from a Dockerfile: the compose `build` subcommand, `up --build`,
# or plain `docker build` / `docker buildx build`. The subcommand needs whitespace before it,
# which is what keeps `--build` from being read as the `build` subcommand.
BUILDS_IMAGE = re.compile(rf"{_COMPOSE}build\b|{_COMPOSE}up\b.*--build\b|\bdocker\s+(?:buildx\s+)?build\b")

# A build that refreshes its base image. `up --build` is deliberately NOT accepted, even with
# `--pull always`: that flag is documented for the images compose PULLS, and whether it also
# reaches the FROM line of the images it BUILDS has varied between compose releases. An
# explicit `build --pull` has meant one thing in every release.
PULLED_BUILD = re.compile(rf"(?:{_COMPOSE}build\b|\bdocker\s+(?:buildx\s+)?build\b).*--pull\b")

# A line that STARTS the stack, and therefore runs whatever third-party images it names.
STARTS_STACK = re.compile(rf"{_COMPOSE}up\b")

# A refresh of the images compose runs but does not build (mysql, redis, nginx).
PULLED_IMAGES = re.compile(rf"{_COMPOSE}pull\b|\bdocker\s+pull\b|{_COMPOSE}up\b.*--pull[\s=]+always\b")

DOCKERFILE_FROM = re.compile(r"^\s*FROM\s+\S+", re.IGNORECASE)
DOCKERFILE_USER = re.compile(r"^\s*USER\s+(?P<value>\S+)", re.IGNORECASE)

# `USER root` and `USER 0` are the explicit spellings of the thing this rule exists to stop,
# so they fail exactly as a missing USER does. `$APP_UID`, `app` and `1654` all pass: the
# rule asks whether root was dropped, never which non-root identity was chosen.
ROOT_USER_VALUES = frozenset({"root", "0", "root:root", "0:0"})


def _ssh_action_steps(lines: list[str]) -> Iterable[tuple[int, int]]:
    """Yield (start, end) line indices bounding each SSH-action step.

    The end is the next step boundary, or end of file. Bounding the block is what stops a
    `fingerprint:` further down the workflow from being counted for a step that has none.
    """
    for start, line in enumerate(lines):
        if not SSH_ACTION.match(line):
            continue
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if STEP_BOUNDARY.match(lines[index]):
                end = index
                break
        yield start, end


def check_deploy_ssh_user(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a deploy step that logs into the server as root.

    Scoped to SSH-action steps rather than to any `username:` in the file, because
    `username` is an ordinary key elsewhere -- a database service, a registry login -- and
    only this one hands out a shell.
    """
    for start, end in _ssh_action_steps(lines):
        for index in range(start, end):
            match = USERNAME_INPUT.match(lines[index])
            if match is None or not ROOT_VALUE.search(match.group("value")):
                continue
            if line_exemption_reason(lines, index, "deploy-root-ssh"):
                continue
            yield Violation(
                path=path,
                line=index + 1,
                rule="deploy-root-ssh",
                message=(
                    "this deploy logs into the server as root, so a compromise of the "
                    "workflow, the key secret, or the third-party SSH action is a full "
                    "host takeover. Create a named deploy user, put it in the docker "
                    "group, give it ownership of the stack directory, and name it here. "
                    "Docker-group membership is still root-ADJACENT -- it can bind-mount "
                    "/ into a container -- so this is one rung down from root rather than "
                    "least privilege; see claude/rules/publishing.md."
                ),
            )


def check_deploy_host_key(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag an SSH deploy step that does not pin the server's host key."""
    for start, end in _ssh_action_steps(lines):
        if any(FINGERPRINT_INPUT.match(lines[index]) for index in range(start, end)):
            continue
        if line_exemption_reason(lines, start, "deploy-no-hostkey"):
            continue
        yield Violation(
            path=path,
            line=start + 1,
            rule="deploy-no-hostkey",
            message=(
                "this SSH step sets no `fingerprint:`, so it trusts whatever answers at "
                "that address and a hijacked DNS record collects a key with write access "
                "to the server. Add the host key's SHA256 fingerprint. GET THE VALUE BY "
                "TESTING, NOT FROM `ssh -v`: this action runs a Go SSH client, which "
                "prefers ECDSA host keys where OpenSSH prefers ed25519, so on a server "
                "offering both, the fingerprint you are shown is usually NOT the one the "
                "deploy checks -- and the mismatch error names no algorithm. Try "
                "`ssh-keyscan -t ecdsa <host> | ssh-keygen -lf -` first."
            ),
        )


def check_deploy_pull(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag an SSH deploy step that builds or starts containers without pulling first.

    Judged per STEP, on presence, like the host-key rule -- not per command line. A deploy
    script legitimately builds without pulling in one place: its rollback, which rebuilds the
    previous commit and must not take a dependency on the registry being reachable at the
    moment everything else has already gone wrong. Requiring `--pull` on every build line
    would push exactly that dependency into the failure path.

    Two halves, because the two kinds of image go stale separately:

    * the BASE images of what the stack builds, refreshed only by `build --pull`;
    * the THIRD-PARTY images it runs as they are (mysql, redis), refreshed only by a pull.

    A step whose stack happens to build every service still needs the second half. The
    command is then a no-op, and it is required anyway: the first third-party service added
    to that compose file would otherwise go stale with nothing to say so.

    Shell comment lines are skipped, or a comment ABOUT pulling would satisfy the rule.
    """
    for start, end in _ssh_action_steps(lines):
        commands = [line for line in lines[start:end] if not line.lstrip().startswith("#")]
        missing: list[str] = []
        if any(BUILDS_IMAGE.search(line) for line in commands) and not any(
            PULLED_BUILD.search(line) for line in commands
        ):
            missing.append("`docker compose build --pull` (base images of what it builds)")
        if any(STARTS_STACK.search(line) for line in commands) and not any(
            PULLED_IMAGES.search(line) for line in commands
        ):
            missing.append("`docker compose pull --ignore-buildable` (the images it runs as-is)")
        if not missing:
            continue
        if line_exemption_reason(lines, start, "deploy-no-pull"):
            continue
        yield Violation(
            path=path,
            line=start + 1,
            rule="deploy-no-pull",
            message=(
                f"this deploy step runs containers without refreshing their images; add "
                f"{' and '.join(missing)} before the forward `up`. Without a pull the server "
                f"keeps the first copy of every tag it ever downloaded: a newly pinned tag is "
                f"never fetched, and an official image rebuilt under the same tag for a "
                f"security patch never arrives. Leave the ROLLBACK path unpulled: it must not depend on the registry "
                f"at the moment the deploy has already failed. `--ignore-buildable` matters -- "
                f"without it, compose tries to pull the images this repo builds and fails."
            ),
        )


def check_container_user(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a Dockerfile whose final stage leaves the container running as root.

    Judged on the FINAL stage only. Earlier stages are build scaffolding that never ships
    and routinely need root to install packages; flagging those would teach people to
    exempt the rule rather than fix it.
    """
    from_indices = [index for index, line in enumerate(lines) if DOCKERFILE_FROM.match(line)]
    if not from_indices:
        return

    final_stage_start = from_indices[-1]
    offending_user_index: Optional[int] = None
    dropped_root = False

    # Last USER in the stage wins, because that is what Docker does. A stage that goes back
    # to root to install something and then drops again is compliant; one that drops and
    # then returns to root is not.
    for index in range(final_stage_start, len(lines)):
        match = DOCKERFILE_USER.match(lines[index])
        if match is None:
            continue
        if match.group("value").casefold() in ROOT_USER_VALUES:
            offending_user_index = index
            dropped_root = False
        else:
            offending_user_index = None
            dropped_root = True

    if dropped_root:
        return

    report_index = final_stage_start if offending_user_index is None else offending_user_index
    if line_exemption_reason(lines, report_index, "container-root-user"):
        return

    detail = (
        "sets `USER root` explicitly"
        if offending_user_index is not None
        else "sets no `USER`, so it inherits root from the base image"
    )
    yield Violation(
        path=path,
        line=report_index + 1,
        rule="container-root-user",
        message=(
            f"this Dockerfile's final stage {detail}. A container listening on an "
            f"unprivileged port needs no capability at all, so root buys nothing and costs "
            f"a container escape that starts as root. The runtime images ship a ready "
            f"non-root user for this -- `USER $APP_UID` (uid 1654) on aspnet, `USER node` "
            f"on node. MIGRATING AN EXISTING DEPLOYMENT NEEDS A ONE-TIME `chown` OF ITS "
            f"NAMED VOLUMES, and of any directory the app writes to inside the image, "
            f"BEFORE the non-root image ships -- root can write to a volume owned by the "
            f"new uid, so that order has no downtime window. The recipe, and the reason "
            f"the in-image write paths are the ones that get forgotten, are in "
            f"claude/rules/publishing.md."
        ),
    )


def check_compose_read_only(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Flag a service built from this repo that does not set `read_only: true`.

    The sibling of container-root-user, and the last cheap item in the container-hardening
    set. Non-root says what the process may do; read-only says what it may leave behind. With
    both, code execution inside the container cannot drop a webshell, patch the app's own
    assemblies, or persist anything across a recreate -- a foothold becomes something that
    vanishes on restart.

    THE TRIGGER IS `build:`, not the service name or the proxy network, and that is what makes
    the rule quiet enough to keep. A service with `build:` has its Dockerfile here: we control
    what it writes and can give any new path a volume. A service running a third-party image
    (mysql, redis, mailpit) is one whose write behaviour we do not control, and where
    read-only routinely stops the container starting at all -- the official MySQL entrypoint
    writes to its datadir, /var/run and /tmp before it will serve. Flagging those would produce
    findings whose only correct resolution is an exemption, and a rule whose findings are all
    exemptions is noise that gets switched off.

    Base file only: hardening belongs there so development exercises the same constraints.

    WHAT IT COSTS, stated because this rule asks for real work rather than a one-line addition.
    Every path the app writes must become a volume or a tmpfs, and a missed one fails LATE --
    the container starts, serves, logs a user in, and dies only when somebody exercises that
    one feature. Grep CreateDirectory / WriteAllBytes / FileStream / open(...,'w') / fopen
    before switching it on, and check where the logger writes: stdout needs nothing, a file
    sink needs a home.

    TWO TRAPS MEASURED ON INVOTRACK 2026-08-21, both of which look like success:

    * A tmpfs is mounted ROOT-OWNED AT 0755 unless the path is /tmp, which Docker special-cases
      to 1777. So `- /home/app:size=16m` mounts fine, shows up in `docker inspect`, and is
      still unwritable by the app's uid. Pass `uid=`/`gid=` explicitly.
    * The runtime's HOME may be load-bearing without anything saying so. InvoTrack's publish
      notes recorded that QuestPDF/Skia "needed no XDG_CACHE_HOME workaround: /home/app is
      writable" -- a sentence that reads as trivia until read_only removes the writability, at
      which point PDF generation is the only thing that breaks.
    """
    if path.name.casefold() not in ("docker-compose.yml", "docker-compose.yaml"):
        return

    for service in iter_blocks(lines, "services:"):
        # `build:` is the trigger, per the docstring: a third-party image's write
        # behaviour is not ours to constrain.
        if "build" not in service.keys:
            continue
        if "true" in service.keys.get("read_only", "").casefold():
            continue
        if line_exemption_reason(lines, service.line - 1, "compose-read-only"):
            continue
        yield Violation(
            path=path,
            line=service.line,
            rule="compose-read-only",
            message=(
                f"service '{service.name}' is built from this repo but does not set "
                f"`read_only: true`, so anything that gets execution inside it can write "
                f"anywhere in the image and persist across a restart. Add `read_only: true` "
                f"plus a `tmpfs:` entry for /tmp (size it -- a tmpfs is RAM, and an unbounded "
                f"one defeats mem_limit), and make every path the app writes a volume. Grep "
                f"CreateDirectory / WriteAllBytes / FileStream / open(...,'w') first: a missed "
                f"path fails LATE, when somebody exercises that one feature, not at startup. "
                f"A tmpfs other than /tmp mounts root-owned at 0755, so pass uid=/gid= or the "
                f"process still cannot write it. If the image genuinely cannot run read-only, "
                f"exempt the service line with a reason naming what it writes and why that "
                f"cannot be a volume."
            ),
        )

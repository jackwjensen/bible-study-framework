#!/usr/bin/env python3
"""
sync_plan.py — the three-way decision, item by item. Pure: no network, no disk.

For every item it compares the file here and the item on the server against the
*base* — what both looked like the last time they agreed — and decides:

| here      | server    | action                                             |
|-----------|-----------|----------------------------------------------------|
| unchanged | unchanged | nothing                                            |
| changed   | unchanged | push the file (or delete it on the server)         |
| unchanged | changed   | pull the item (or delete the file here)            |
| changed   | changed   | compare: the same claim both sides → record it;    |
|           |           | different → a CONFLICT that a person resolves      |

There is no "newest wins". An edit is never discarded by a rule; when both sides
moved and they disagree, neither side is touched and a person decides.

Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass

from study_corpus import FINDING_LIST_FIELDS, THREAD_LIST_FIELDS, normalize_list, parse_frontmatter
from sync_state import content_sha

# Actions, in the words the plan is printed in.
NOTHING = "nothing"
PUSH = "push"                    # create or update on the server
PUSH_DELETE = "delete on server"
PULL = "pull"
LOCAL_DELETE = "delete here"
FORGET = "forget"                # gone on both sides; drop the base record
COMPARE = "both changed"         # fetch the server's copy, then converge or conflict
CONFLICT = "conflict"            # a deletion against an edit
STILL_OPEN = "conflict still open"

#: Frontmatter that records *when*, not *what*. A server may stamp it on write,
#: so two copies differing only here hold the same claim.
_NON_CONTENT_KEYS = {"updated"}
_LIST_KEYS = set(FINDING_LIST_FIELDS) | set(THREAD_LIST_FIELDS)


@dataclass
class Step:
    key: str
    action: str
    base_version: str | None = None     # the server version an If-Match must name
    remote_version: str | None = None   # the server's current version, when known
    remote_sha: str | None = None
    detail: str = ""


def plan_item(key: str, base: dict | None, local: bytes | None, remote: dict | None,
              *, listed: bool, conflict_open: bool) -> Step:
    """Decide one item.

    `remote` is the item's entry in the changes feed. `listed` says whether that
    feed is a full listing, in which case an item missing from it is gone from
    the server; in an incremental feed a missing item simply did not change.
    """
    pending = (base or {}).get("pending")
    if pending is not None:
        if conflict_open:
            return Step(key, STILL_OPEN)
        # The conflict file is gone: whoever deleted it has resolved the item,
        # and the server's side of the conflict is now the base.
        base = {"version": pending["version"], "remote_sha": pending["sha"],
                "local_sha": pending["sha"]}

    base_version = (base or {}).get("version")
    base_local = (base or {}).get("local_sha")

    if remote is not None:
        now_version = None if remote.get("deleted") else str(remote.get("version"))
        now_sha = None if remote.get("deleted") else remote.get("sha256")
    elif listed and base_version is not None:
        now_version, now_sha = None, None        # absent from a full listing: deleted
    else:
        now_version, now_sha = base_version, (base or {}).get("remote_sha")

    local_sha = content_sha(local) if local is not None else None
    local_changed = local_sha != base_local
    remote_changed = now_version != base_version
    here, there = local is not None, now_version is not None
    step = Step(key, NOTHING, base_version=base_version, remote_version=now_version,
                remote_sha=now_sha)

    if not local_changed and not remote_changed:
        step.action = NOTHING
    elif local_changed and not remote_changed:
        step.action = PUSH if here else (PUSH_DELETE if there else FORGET)
    elif remote_changed and not local_changed:
        step.action = PULL if there else (LOCAL_DELETE if here else FORGET)
    elif here and there:
        step.action = COMPARE
    elif not here and not there:
        step.action = FORGET
    else:
        step.action = CONFLICT
        step.detail = ("deleted here, edited on the server" if there
                       else "edited here, deleted on the server")
    return step


def plan(base_items: dict, local: dict, remote_items: list[dict], *, listed: bool,
         conflicts_open: set[str]) -> list[Step]:
    """Every item either side or the base knows of, decided."""
    remote = {f"{item['kind']}/{item['slug']}": item for item in remote_items}
    keys = sorted(set(base_items) | set(local) | set(remote))
    return [plan_item(key, base_items.get(key), local.get(key), remote.get(key),
                      listed=listed, conflict_open=key in conflicts_open)
            for key in keys]


# ── "the same claim" ─────────────────────────────────────────────────────────

def _split(text: str) -> tuple[dict, str]:
    text = text.removeprefix("﻿")
    text = text.replace("\r\n", "\n")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return parse_frontmatter(text[4:end]), text[end + 4:]
    return {}, text


def _normal_meta(meta: dict, slug: str) -> dict:
    out = {}
    for key, value in meta.items():
        if key in _NON_CONTENT_KEYS:
            continue
        if key in _LIST_KEYS or isinstance(value, list):
            out[key] = normalize_list(value) if not _has_maps(value) else value
        else:
            out[key] = str(value).strip()
    if out.get("id") == slug:
        del out["id"]                     # an id equal to the filename says nothing
    return {key: value for key, value in out.items() if value not in ("", [])}


def _has_maps(value) -> bool:
    return isinstance(value, list) and any(isinstance(item, dict) for item in value)


def _normal_body(body: str) -> str:
    return "\n".join(line.rstrip() for line in body.strip().split("\n"))


def same_claim(left: bytes, right: bytes, slug: str) -> bool:
    """Whether two copies of an item say the same thing.

    Byte equality is too strict: a server may write the same frontmatter with
    different quoting, key order or list style, and stamp `updated:`. This
    compares what the framework's own parser reads out of each, so two copies
    the validator and the index would treat identically count as one. Anything
    else — one changed word in the body, one status — is a difference.
    """
    if content_sha(left) == content_sha(right):
        return True
    try:
        left_meta, left_body = _split(left.decode("utf-8"))
        right_meta, right_body = _split(right.decode("utf-8"))
    except UnicodeDecodeError:
        return False
    return (_normal_meta(left_meta, slug) == _normal_meta(right_meta, slug)
            and _normal_body(left_body) == _normal_body(right_body))

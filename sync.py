#!/usr/bin/env python3
"""
sync.py — keep this corpus and a sync server in step, both ways. OPTIONAL.

Nothing else in the framework needs this file, a server, or an account. It is
for people who also keep their findings somewhere else (SourceText is one such
server; the protocol is in SYNC.md and any server may implement it).

    python sync.py add sourcetext https://sourcetext.ai --corpus bible
    python sync.py login                 # once per computer: approve in the browser
    python sync.py run --dry-run         # what would move, and why
    python sync.py run                   # move it
    python sync.py logout                # revoke this computer's sign-in

Every item is compared three ways — here, the server, and how both looked when
they last agreed. One side changed: it is copied to the other. Both changed to
the same thing: recorded. Both changed differently: a CONFLICT — the server's
copy is written beside yours as `<file>.md.remote`, neither side is touched, and
the run exits non-zero until a person resolves it. Nothing is last-write-wins.

Standard library only.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

from sync_http import ApiError, Client, CursorUnknown, SyncError, check_server_url, host_label
from sync_http import login as http_login
from sync_http import logout as http_logout
from sync_plan import (
    COMPARE, CONFLICT, FORGET, LOCAL_DELETE, NOTHING, PULL, PUSH, PUSH_DELETE, STILL_OPEN, Step,
    plan, same_claim
)
from sync_state import (
    KIND_DIRS, SyncState, TokenStore, atomic_write, conflict_path, content_sha, item_path,
    load_remotes, open_conflicts, raw_sha, save_remotes, scan_local, slug_ok, split_key, sync_dir
)

ROOT = Path(__file__).resolve().parent
#: A second pass picks up what the first one's pushes caused on the server
#: (a theme hub it created for a new finding). Never more: a pass that keeps
#: finding work is a bug, and looping on it would hide one.
MAX_PASSES = 2
#: Refusals about the account, the corpus or the deployment, not the item: no
#: consent to store study content, a corpus the server does not serve, or sync
#: not configured on that server at all.
ACCOUNT_WIDE_ERRORS = {"consent_required", "unknown_corpus", "sync_unavailable"}


@dataclass
class Outcome:
    counts: dict = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, what: str) -> None:
        self.counts[what] = self.counts.get(what, 0) + 1


# ── one run ──────────────────────────────────────────────────────────────────

class Syncer:
    def __init__(self, root: Path, name: str, client: Client, *, dry_run: bool = False):
        self.root = root
        self.name = name
        self.client = client
        self.dry_run = dry_run
        self.state = SyncState(root, name)
        self.outcome = Outcome()

    def run(self) -> Outcome:
        for note in self.state.notes:
            print(note)
        for _ in range(MAX_PASSES):
            pushed_before = self.outcome.counts.get("pushed", 0) + self.outcome.counts.get("created", 0)
            self._pass()
            pushed = (self.outcome.counts.get("pushed", 0) + self.outcome.counts.get("created", 0)
                      - pushed_before)
            if self.dry_run or not pushed or self.outcome.errors or self.outcome.conflicts:
                break
        return self.outcome

    def _pass(self) -> None:
        listed = self.state.cursor is None
        try:
            feed = self.client.changes(self.state.cursor)
        except CursorUnknown:
            print("The server no longer knows where we left off; comparing everything.")
            feed, listed = self.client.changes(None), True
        local, skipped = scan_local(self.root)
        for name in skipped:
            print(f"  skipped (not a syncable id): {name}")
        open_keys = {self._key_of(path) for path in open_conflicts(self.root)}
        steps = plan(self.state.items, local, feed["items"], listed=listed,
                     conflicts_open=open_keys)
        if self.dry_run:
            self._print_plan(steps)
            return
        errors_before = len(self.outcome.errors)
        for step in steps:
            self._settle_resolved(step.key, open_keys)
            try:
                self._execute(step, local.get(step.key))
            except ApiError as error:
                if error.code in ACCOUNT_WIDE_ERRORS:
                    # True of every item alike: say it once and stop, rather than
                    # repeating it for each of several hundred files.
                    self.state.save()
                    message, where = str(error), str(error.body.get("consent_url") or "")
                    if where and where not in message:
                        message += f" Go to: {where}"
                    raise SyncError(message) from error
                self.outcome.errors.append(f"{step.key}: {error}")
            self.state.save()
        # The cursor moves only when every change it covers was handled. A
        # failed pull left behind would otherwise never be listed again.
        if len(self.outcome.errors) == errors_before:
            self.state.cursor = str(feed.get("cursor") or "") or None
            self.state.save()

    def _key_of(self, path: Path) -> str:
        kind = next(k for k, folder in KIND_DIRS.items() if path.parent.name == folder)
        return f"{kind}/{path.name[:-len('.md.remote')]}"

    def _settle_resolved(self, key: str, open_keys: set[str]) -> None:
        """A conflict whose file was deleted: its server side becomes the base."""
        base = self.state.items.get(key) or {}
        pending = base.get("pending")
        if pending is not None and key not in open_keys:
            self.state.record(key, pending["version"], pending["sha"],
                              pending["sha"] if pending["version"] is not None else None)

    # ── the actions ──────────────────────────────────────────────────────────

    def _execute(self, step: Step, local: bytes | None) -> None:
        kind, slug = split_key(step.key)
        if step.action == NOTHING:
            return
        if step.action == STILL_OPEN:
            self.outcome.conflicts.append(f"{step.key}: still waiting for you (see its .remote file)")
        elif step.action == PUSH:
            self._push(step, kind, slug, local)
        elif step.action == PUSH_DELETE:
            self._push_delete(step, kind, slug)
        elif step.action == PULL:
            self._pull(step, kind, slug)
        elif step.action == LOCAL_DELETE:
            item_path(self.root, kind, slug).unlink(missing_ok=True)
            self.state.record(step.key, None, None, None)
            self.outcome.add("deleted here")
        elif step.action == FORGET:
            self.state.items.pop(step.key, None)
        elif step.action == COMPARE:
            remote = self.client.get(kind, slug)
            if remote is None:
                self._conflict(step.key, None, None, None, "edited here, deleted on the server")
            else:
                self._compare(step.key, slug, local, remote)
        elif step.action == CONFLICT:
            remote = self.client.get(kind, slug) if step.remote_version is not None else None
            self._conflict(step.key, remote and remote["version"], remote and remote["sha"],
                           remote and remote["markdown"], step.detail)

    def _push(self, step: Step, kind: str, slug: str, content: bytes) -> None:
        try:
            answer = self.client.put(kind, slug, content, version=step.base_version)
        except ApiError as error:
            if error.status != 409 or error.code != "edit_conflict":
                raise
            current = error.body.get("current")
            if current is None:
                self._conflict(step.key, None, None, None, "edited here, deleted on the server")
            else:
                self._compare(step.key, slug, content, {
                    "version": str(current["version"]), "sha": current.get("sha256"),
                    "markdown": str(current.get("markdown", "")).encode("utf-8")})
            return
        self.state.record(step.key, str(answer["version"]), answer.get("sha256"), content_sha(content))
        self.outcome.add("pushed" if step.base_version else "created")

    def _push_delete(self, step: Step, kind: str, slug: str) -> None:
        try:
            self.client.delete(kind, slug, version=step.base_version)
        except ApiError as error:
            current = error.body.get("current") if error.status == 409 else None
            if error.code == "has_dependents":
                names = ", ".join(error.body.get("dependents", []))
                raise ApiError(409, {"message": f"not deleted on the server: {names} still "
                                     "build on it. Push those edits first, then sync again."}) from error
            if error.code != "edit_conflict":
                raise
            if current is not None:
                self._conflict(step.key, str(current["version"]), current.get("sha256"),
                               str(current.get("markdown", "")).encode("utf-8"),
                               "deleted here, edited on the server")
                return
        self.state.record(step.key, None, None, None)
        self.outcome.add("deleted on server")

    def _pull(self, step: Step, kind: str, slug: str) -> None:
        remote = self.client.get(kind, slug)
        if remote is None:  # deleted between the listing and now; our copy is unchanged
            item_path(self.root, kind, slug).unlink(missing_ok=True)
            self.state.record(step.key, None, None, None)
            self.outcome.add("deleted here")
            return
        markdown = remote["markdown"]
        if remote["sha"] and raw_sha(markdown) != remote["sha"]:
            raise ApiError(0, {"message": "the content arrived damaged (hash mismatch); "
                               "nothing written. Sync again."})
        atomic_write(item_path(self.root, kind, slug), markdown)
        self.state.record(step.key, remote["version"], remote["sha"] or raw_sha(markdown),
                          content_sha(markdown))
        self.outcome.add("pulled")

    def _compare(self, key: str, slug: str, local: bytes, remote: dict) -> None:
        if same_claim(local, remote["markdown"], slug):
            self.state.record(key, remote["version"], remote["sha"] or raw_sha(remote["markdown"]),
                              content_sha(local))
            self.outcome.add("already the same")
            return
        self._conflict(key, remote["version"], remote["sha"] or raw_sha(remote["markdown"]),
                       remote["markdown"], "changed here and on the server")

    def _conflict(self, key: str, version: str | None, sha: str | None,
                  markdown: bytes | None, detail: str) -> None:
        kind, slug = split_key(key)
        if markdown is None:
            markdown = _deleted_note(self.name, item_path(self.root, kind, slug).name)
        atomic_write(conflict_path(self.root, kind, slug), markdown)
        self.state.mark_conflict(key, version, sha)
        self.outcome.conflicts.append(f"{key}: {detail}")

    def _print_plan(self, steps: list[Step]) -> None:
        moving = [step for step in steps if step.action != NOTHING]
        if not moving:
            print("In step: nothing to move.")
        for step in moving:
            print(f"  {step.action:<20} {step.key}" + (f"  ({step.detail})" if step.detail else ""))
            if step.action in (CONFLICT, STILL_OPEN):
                self.outcome.conflicts.append(step.key)


def _deleted_note(remote: str, filename: str) -> bytes:
    return (f"This item was DELETED on the server '{remote}', while {filename} was edited here.\n\n"
            f"- To keep your copy: delete this .remote file and sync again; it is re-created there.\n"
            f"- To accept the deletion: delete both this file and {filename}, then sync again.\n"
            ).encode()


# ── report ───────────────────────────────────────────────────────────────────

def report(root: Path, name: str, outcome: Outcome, *, dry_run: bool) -> int:
    moved = ", ".join(f"{count} {what}" for what, count in sorted(outcome.counts.items()))
    print(("Would move: " if dry_run else "Moved: ") + (moved or "nothing"))
    path = sync_dir(root) / f"{name}.report.md"
    if outcome.conflicts or outcome.errors:
        lines = ["# Sync needs a person", ""]
        if outcome.conflicts:
            lines += ["## Conflicts", "",
                      "Each `<file>.md.remote` beside an item holds the server's copy. Make the",
                      "`.md` say what it should, delete the `.remote` file, and sync again. The",
                      "`.md` is what gets kept; the server's copy is replaced by it.", ""]
            lines += [f"- {line}" for line in outcome.conflicts] + [""]
        if outcome.errors:
            lines += ["## Not done", ""] + [f"- {line}" for line in outcome.errors] + [""]
        text = "\n".join(lines)
        print("\n" + text)
        if not dry_run:
            atomic_write(path, text.encode("utf-8"))
        return 1
    if not dry_run:
        path.unlink(missing_ok=True)
    if outcome.counts.get("pulled") or outcome.counts.get("deleted here"):
        print("Run: python build_index.py")
    return 0


# ── commands ─────────────────────────────────────────────────────────────────

def _remote(root: Path, name: str | None) -> tuple[str, dict]:
    remotes = load_remotes(root)
    if name is None:
        if len(remotes) != 1:
            raise SyncError("name the remote (configured: " + (", ".join(remotes) or "none") + ")")
        name = next(iter(remotes))
    if name not in remotes:
        raise SyncError(f"no remote called '{name}'; add one: python sync.py add <name> <url> --corpus <code>")
    return name, remotes[name]


def cmd_add(args: argparse.Namespace, root: Path, store: TokenStore) -> int:
    if not slug_ok(args.name) or not slug_ok(args.corpus):
        raise SyncError("the name and the corpus code are lowercase letters, digits and dashes")
    remotes = load_remotes(root)
    remotes[args.name] = {"url": check_server_url(args.url), "corpus": args.corpus}
    save_remotes(root, remotes)
    print(f"Added '{args.name}'. Next, once on this computer: python sync.py login {args.name}")
    return 0


def cmd_login(args: argparse.Namespace, root: Path, store: TokenStore) -> int:
    _, remote = _remote(root, args.name)
    # --no-browser: print the address and wait, for approving in a browser this
    # script cannot open (another machine's, or an assistant's browser pane).
    opener = (lambda url: None) if args.no_browser else webbrowser.open
    http_login(remote["url"], store, client_name=f"bible-study-framework sync ({host_label()})",
               open_browser=opener)
    print("Signed in. The sign-in is kept in your user profile, not in this corpus.")
    return 0


def cmd_logout(args: argparse.Namespace, root: Path, store: TokenStore) -> int:
    _, remote = _remote(root, args.name)
    http_logout(remote["url"], store)
    print("Signed out on this computer.")
    return 0


def cmd_run(args: argparse.Namespace, root: Path, store: TokenStore) -> int:
    name, remote = _remote(root, args.name)
    client = Client(remote["url"], remote["corpus"], store)
    outcome = Syncer(root, name, client, dry_run=args.dry_run).run()
    return report(root, name, outcome, dry_run=args.dry_run)


def main(argv: list[str], *, root: Path = ROOT, store: TokenStore | None = None) -> int:
    parser = argparse.ArgumentParser(description="Keep this corpus and a sync server in step.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_add = sub.add_parser("add", help="name a server to sync with")
    p_add.add_argument("name")
    p_add.add_argument("url", help="the server's root address, e.g. https://sourcetext.ai")
    p_add.add_argument("--corpus", required=True, help="the server's corpus code, e.g. bible")
    p_add.set_defaults(func=cmd_add)
    for command, helptext, func in (("login", "sign in once on this computer", cmd_login),
                                    ("logout", "revoke this computer's sign-in", cmd_logout)):
        p_cmd = sub.add_parser(command, help=helptext)
        p_cmd.add_argument("name", nargs="?")
        p_cmd.set_defaults(func=func)
        if command == "login":
            p_cmd.add_argument("--no-browser", action="store_true",
                               help="print the sign-in address instead of opening a browser")
    p_run = sub.add_parser("run", help="sync both ways")
    p_run.add_argument("name", nargs="?")
    p_run.add_argument("--dry-run", action="store_true", help="print the plan; change nothing")
    p_run.set_defaults(func=cmd_run)

    # Titles carry em dashes, Greek and Hebrew; a legacy Windows codepage would
    # crash on printing one, mid-run, after some items had moved.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = parser.parse_args(argv[1:])
    try:
        return args.func(args, root, store or TokenStore())
    except SyncError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

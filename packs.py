#!/usr/bin/env python3
"""
packs.py — share findings with other people, and receive theirs.

A **pack** is a zip file containing a `pack.md` manifest and a `findings/`
directory. Send it however you like: email, a USB stick, a link. There is no
registry, no account, and nobody approves anything.

    python packs.py export --out my-pack.zip --title "Spiritual gifts" \
        --author "Your Name" --author-id yourname
    python packs.py import received-pack.zip
    python packs.py subscribe https://example.com/feeds/abc123/feed.json
    python packs.py subscribe 'https://example.com/feeds/abc123/feed.json#key=…'
    python packs.py check            # any subscribed pack newer than what you have?
    python packs.py pull             # fetch + import updates from subscriptions
    python packs.py unsubscribe https://example.com/feeds/abc123/feed.json

Imported findings land in `imported/<author-id>/` and are READ-ONLY. They are
never merged into your own `findings/` — see PACKS.md for why that matters.

This file is the command line. The work is in four modules, each with one job:
packs_archive.py (the pack format: export and import), packs_follow.py
(subscriptions), packs_feed.py (reading a feed over the network, and the rules
that keep a private feed's key safe) and user_secrets.py (where that key is
kept: the user profile, never subscriptions.json, which is meant to be committed).

Standard library only.
"""

from __future__ import annotations

import argparse
import sys

from packs_archive import cmd_export, cmd_import, ensure_imported_dir
from packs_feed import FeedError, redact
from packs_follow import cmd_check, cmd_pull, cmd_subscribe, cmd_unsubscribe
from user_secrets import FeedKeyStore


class _Parser(argparse.ArgumentParser):
    """argparse echoes arguments it rejects; a pasted private link must not be among them."""

    def error(self, message: str):
        super().error(redact(message))


def main(argv: list[str], *, keys: FeedKeyStore | None = None) -> int:
    parser = _Parser(description="Share and receive packs of findings.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="build a shareable zip from your findings")
    p_export.add_argument("--out", required=True, help="output zip path")
    p_export.add_argument("--title", required=True)
    p_export.add_argument("--author", required=True)
    p_export.add_argument("--author-id", required=True, help="short slug, used as the folder name")
    p_export.add_argument("--pack", help="pack id (default: <author-id>-pack)")
    p_export.add_argument("--version", default="1", help="version string; a date works well")
    p_export.add_argument("--license", default="CC BY 4.0")
    p_export.add_argument("--homepage")
    p_export.add_argument("--description")
    p_export.add_argument("--findings", help="comma-separated finding ids (default: all)")
    p_export.add_argument("--themes", help="comma-separated themes to filter by")
    p_export.set_defaults(func=cmd_export)

    p_import = sub.add_parser("import", help="import a pack zip you received")
    p_import.add_argument("zipfile")
    p_import.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    p_import.set_defaults(func=cmd_import)

    p_sub = sub.add_parser("subscribe", help="follow someone's feed, from the link they gave you")
    p_sub.add_argument("url", help="the feed link as given; a private one ends in #key=…")
    p_sub.set_defaults(func=cmd_subscribe)

    p_unsub = sub.add_parser("unsubscribe", help="stop following a feed and forget its key")
    p_unsub.add_argument("url", help="the feed's address (or its link)")
    p_unsub.set_defaults(func=cmd_unsubscribe)

    p_check = sub.add_parser("check", help="list subscribed packs and available updates")
    p_check.set_defaults(func=cmd_check)

    p_pull = sub.add_parser("pull", help="fetch and import updates from subscriptions")
    p_pull.add_argument("--all", action="store_true", help="also import packs you don't have yet")
    p_pull.add_argument("-y", "--yes", action="store_true", help="skip confirmation prompts")
    p_pull.set_defaults(func=cmd_pull)

    # Pack titles and finding titles carry em dashes, Greek and Hebrew. A Windows
    # console on a legacy codepage raises UnicodeEncodeError on those, which would
    # kill an import mid-run — after files were staged but before they were placed.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = parser.parse_args(argv[1:])
    args.keys = keys or FeedKeyStore()
    ensure_imported_dir()
    try:
        return args.func(args)
    except FeedError as error:
        print(f"error: {redact(str(error))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

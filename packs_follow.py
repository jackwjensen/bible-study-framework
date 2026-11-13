#!/usr/bin/env python3
"""
packs_follow.py — following other people's feeds: subscribe, check, pull,
unsubscribe.

`subscriptions.json` lists the feeds this corpus follows, by address. It is
meant to be committed, so it never holds a key: a private feed's entry carries
`"private": true`, and its key lives in the user profile (user_secrets.py) on
each computer that was given the link. The wire rules are packs_feed.py's; what
to do with a downloaded pack is packs_archive.py's.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile

from packs_archive import die, import_download, installed_version
from packs_feed import FeedError, feed_title, fetch, origin, parse_link, read_feed, redact
from study_corpus import STUDY_DIR
from user_secrets import FeedKeyStore

SUBSCRIPTIONS_FILE = STUDY_DIR / "subscriptions.json"


def load_subscriptions() -> list[dict]:
    if not SUBSCRIPTIONS_FILE.exists():
        return []
    try:
        data = json.loads(SUBSCRIPTIONS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"subscriptions.json is not valid JSON: {exc}")
    return data.get("feeds", [])


def save_subscriptions(feeds: list[dict]) -> None:
    SUBSCRIPTIONS_FILE.write_text(
        json.dumps({"feeds": feeds}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")


def find_subscription(feeds: list[dict], address: str) -> dict | None:
    return next((entry for entry in feeds if entry.get("url") == address), None)


def cmd_subscribe(args: argparse.Namespace) -> int:
    """Follow a feed from its link as pasted; a private link's key goes to the user profile.

    The entry in subscriptions.json is the address alone, public or private, plus
    `"private": true` for a feed that needs a key — so a second computer knows to
    ask for the link instead of reporting the feed as gone.
    """
    link = parse_link(args.url)
    keys: FeedKeyStore = args.keys
    feeds = load_subscriptions()
    existing = find_subscription(feeds, link.address)
    if existing is not None and link.key is None:
        print(f"Already subscribed to {link.address}.")
        return 0
    feed = read_feed(link.address, link.key)
    if existing is None:
        entry = {"url": link.address, "author": feed.get("author", "(unknown)")}
        feeds.append(entry)
        existing = entry
        title = feed_title(feed)
        print(f"Subscribed to {feed.get('author', link.address)}"
              + (f" — {title}" if title else "")
              + f" — {len(feed.get('packs', []))} pack(s) offered.")
    else:
        print(f"Already subscribed to {link.address}; stored its key on this computer.")
    if link.key is not None:
        existing["private"] = True
        keys.put(link.address, link.key)
        print("This is a private feed. Its key is kept in your user profile on this computer,")
        print("never in subscriptions.json, so that file is safe to commit.")
    save_subscriptions(feeds)
    print("Run: python packs.py check")
    return 0


def cmd_unsubscribe(args: argparse.Namespace) -> int:
    """Stop following a feed and forget its key. Its imported findings stay."""
    address = parse_link(args.url).address
    keys: FeedKeyStore = args.keys
    feeds = load_subscriptions()
    remaining = [entry for entry in feeds if entry.get("url") != address]
    had_key = keys.remove(address)
    if len(remaining) == len(feeds):
        print(f"Not subscribed to {address}." + (" Removed its stored key." if had_key else ""))
        return 0 if had_key else 1
    save_subscriptions(remaining)
    print(f"Unsubscribed from {address}" + (" and removed its key." if had_key else "."))
    print("Findings already imported from it stay in imported/; delete that folder to drop them.")
    return 0


class Offer:
    """One pack a followed feed offers, and what is needed to fetch it."""

    def __init__(self, feed: dict, pack: dict, have: str | None, address: str, key: str | None):
        self.feed, self.pack, self.have = feed, pack, have
        self.address, self.key = address, key
        self.offered = str(pack.get("version", "")).strip()


def survey(keys: FeedKeyStore) -> tuple[list[Offer], int]:
    """Every pack across subscriptions, and how many feeds could not be read.

    A feed that fails is reported and skipped; the others are still surveyed.
    """
    offers: list[Offer] = []
    problems = 0
    for subscription in load_subscriptions():
        address = str(subscription.get("url", ""))
        key = keys.get(address)
        if subscription.get("private") and key is None:
            print(f"- {address}: this feed needs its link on this computer: "
                  "python packs.py subscribe <link>", file=sys.stderr)
            problems += 1
            continue
        try:
            feed = read_feed(address, key)
        except FeedError as error:
            print(f"- {redact(str(error))}", file=sys.stderr)
            problems += 1
            continue
        author_id = str(feed.get("author_id", "")).strip()
        for pack in feed.get("packs", []):
            have = installed_version(author_id, str(pack.get("pack", "")))
            offers.append(Offer(feed, pack, have, address, key))
    return offers, problems


def cmd_check(args: argparse.Namespace) -> int:
    if not load_subscriptions():
        print("No subscriptions yet. Add one: python packs.py subscribe <link to a feed.json>")
        return 0
    offers, problems = survey(args.keys)
    updates = 0
    for offer in offers:
        if offer.have is None:
            state = "not imported"
        elif offer.have != offer.offered:
            state = f"UPDATE available (you have {offer.have})"
            updates += 1
        else:
            state = "up to date"
        print(f"- {offer.feed.get('author', '?')} / {offer.pack.get('title', offer.pack.get('pack'))} "
              f"v{offer.offered} — {state}")
    if updates:
        print(f"\n{updates} update(s). Run: python packs.py pull")
    return 1 if problems else 0


def cmd_pull(args: argparse.Namespace) -> int:
    offers, problems = survey(args.keys)
    pulled = 0
    for offer in offers:
        if offer.have == offer.offered:
            continue
        pack_id = str(offer.pack.get("pack", ""))
        if offer.have is None and not args.all:
            print(f"- skipping {pack_id} (not yet imported; use --all to add it)")
            continue
        if pull_one(offer, assume_yes=args.yes):
            pulled += 1
        else:
            problems += 1
    print(f"\n{pulled} pack(s) updated." if pulled else "\nNothing to update.")
    if pulled:
        print("Run: python build_index.py")
    return 1 if problems else 0


def pull_one(offer: Offer, *, assume_yes: bool) -> bool:
    """Fetch and import one offered pack; whether it went in."""
    pack_id = str(offer.pack.get("pack", ""))
    url = str(offer.pack.get("url", "")).strip()
    if not url:
        print(f"- {pack_id}: feed entry has no url", file=sys.stderr)
        return False
    print(f"\nFetching {offer.pack.get('title', pack_id)} v{offer.offered} …")
    try:
        # The key goes only to the feed's own host; fetch() checks that again.
        same_host = origin(url) == origin(offer.address)
        data = fetch(url, feed=offer.address, key=offer.key if same_host else None)
    except FeedError as error:
        print(f"- {pack_id}: {redact(str(error))}", file=sys.stderr)
        return False
    # One broken pack must not stop the run: import_zip() stops on a bad pack with
    # die() (SystemExit, after printing why) and zipfile raises on a file that is
    # not a zip at all. Either way this pack is a problem and the next one is tried.
    try:
        return import_download(data, pack_id, assume_yes=assume_yes)
    except SystemExit:
        print(f"- {pack_id}: not imported (see above)", file=sys.stderr)
    except zipfile.BadZipFile:
        print(f"- {pack_id}: the download is not a zip file; not imported", file=sys.stderr)
    return False

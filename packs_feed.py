#!/usr/bin/env python3
"""
packs_feed.py — reading a feed over the network, public or private.

Split out of packs.py, which is about packs (export, import, what is installed);
this is about the wire: what a feed link is, how to fetch it, and what to say
when that fails. packs.py imports it, and it imports nothing of this repo's.

The contract, which any server may implement:

- `GET <feed address>` answers `{"author", "author_id", "feed"?, "packs": [...]}`;
  `GET` a pack's `url` answers its zip.
- A **private** feed needs `Authorization: Bearer <key>` on both calls. Without a
  valid key it answers 404, exactly like a feed that does not exist.
- The publisher hands out one string, `<feed address>#key=<key>`. A fragment is
  never sent to a server, so the key travels only in the header, and only to
  the host of the feed address.

Rules this module keeps, whatever the caller does:

- HTTPS only (plain HTTP to this computer only, for tests and local servers).
- A key is never printed. Every message names the address, which never holds
  one, and `redact()` scrubs any text that might.
- A key is never forwarded to another host. A redirect to another host while a
  key is in play is refused, not followed without the key: the feed would then
  be read anonymously from somewhere its reader never chose.

Standard library only.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

USER_AGENT = "bible-study-framework-packs/1"
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024  # a findings pack is text; 25 MB is already absurd

#: Hosts that may be reached over plain HTTP: this computer, nothing else.
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

_KEY_IN_TEXT = re.compile(r"(#key=)[^\s'\"&#]*", re.IGNORECASE)


class FeedError(Exception):
    """A feed could not be read. The message is safe to print: it holds no key."""


class FeedNotFound(FeedError):
    """The server answered 404 — no such feed, or not with this key."""


@dataclass(frozen=True)
class FeedLink:
    """A link as a reader pasted it, split into what may be recorded and what may not."""

    address: str
    key: str | None


def redact(text: str) -> str:
    """Any `#key=…` in a piece of text, replaced, so the text may be printed."""
    return _KEY_IN_TEXT.sub(r"\1…", text)


def parse_link(link: str) -> FeedLink:
    """Split a pasted feed link into its address and, for a private feed, its key.

    The address is the link without its fragment — what subscriptions.json
    records for every feed, public or private. A fragment that is not `key=…`
    is dropped; a link whose fragment holds an empty key is refused, since it
    can only be a copying mistake.
    """
    parts = urllib.parse.urlsplit(link.strip())
    address = urllib.parse.urlunsplit(parts._replace(fragment=""))
    check_address(address)
    # Not parse_qs: it reads `+` as a space, and a key may well contain one.
    values = [part[4:] for part in parts.fragment.split("&") if part.startswith("key=")]
    if not values:
        return FeedLink(address, None)
    key = urllib.parse.unquote(values[0]).strip()
    if key == "":
        raise FeedError(f"the link to {address} ends in '#key=' with no key; copy the whole link")
    return FeedLink(address, key)


def check_address(url: str) -> None:
    """Refuse anything but HTTPS, or plain HTTP to this computer."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and (parts.hostname or "") in _LOOPBACK_HOSTS:
        return
    raise FeedError(f"refusing a non-HTTPS address: {redact(url)}")


def origin(url: str) -> tuple[str, str, int | None]:
    """Scheme, host and port: what "the same host" means for sending a key."""
    parts = urllib.parse.urlsplit(url)
    port = parts.port or {"https": 443, "http": 80}.get(parts.scheme)
    return parts.scheme, (parts.hostname or "").lower(), port


class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only where the request's rules still hold."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_address(newurl)
        if req.has_header("Authorization") and origin(newurl) != origin(req.full_url):
            raise FeedError(f"{_without_query(req.full_url)} redirects to another host; "
                            "refusing to follow it with this feed's key")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _without_query(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(parts._replace(query="", fragment=""))


_OPENER = urllib.request.build_opener(_GuardedRedirects)


def fetch(url: str, *, key: str | None = None, key_origin: str | None = None) -> bytes:
    """GET a feed or a pack.

    `key` is sent only when `url` is on the same host as `key_origin` — the feed
    address the key belongs to. A pack hosted elsewhere is fetched without it.
    """
    check_address(url)
    headers = {"User-Agent": USER_AGENT}
    if key is not None and key_origin is not None and origin(url) == origin(key_origin):
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with _OPENER.open(request, timeout=30) as response:
            data = response.read(MAX_DOWNLOAD_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise FeedNotFound(f"{url} answered 404") from None
        raise FeedError(f"{url} answered HTTP {error.code}") from None
    except urllib.error.URLError as error:
        raise FeedError(f"could not reach {url}: {error.reason}") from None
    except OSError as error:
        raise FeedError(f"could not reach {url}: {error}") from None
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise FeedError(f"refusing oversized download (> {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB): "
                        f"{url}")
    return data


def read_feed(address: str, key: str | None) -> dict:
    """The feed document at `address`, with the reader's key when it has one."""
    try:
        raw = fetch(address, key=key, key_origin=address)
    except FeedNotFound:
        if key is not None:
            raise FeedNotFound(f"the feed at {address} does not accept the key stored for it; "
                               "the link may have been revoked — ask its publisher for a new "
                               "one") from None
        raise FeedNotFound(f"there is no feed at {address}: it does not exist, has nothing "
                           "published, or is private and needs its link") from None
    try:
        document = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise FeedError(f"the feed at {address} is not valid JSON: {error}") from None
    if not isinstance(document, dict):
        raise FeedError(f"the feed at {address} is not a feed document")
    return document


def feed_title(document: dict) -> str | None:
    """The feed's own title, when the document has the optional `feed` object."""
    feed = document.get("feed")
    if isinstance(feed, dict):
        title = str(feed.get("title", "") or "").strip()
        return title or None
    return None

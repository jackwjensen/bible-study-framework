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

import ipaddress
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

#: What a bearer key may be made of (RFC 6750's b64token): URL-safe, no space or line break.
_KEY_CHARACTERS = re.compile(r"[A-Za-z0-9._~+/-]+=*")


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
    if not _KEY_CHARACTERS.fullmatch(key):
        # The key goes into an HTTP header, where a line break or space would let
        # it rewrite the request — and the error text would then quote it.
        raise FeedError(f"the key in the link to {address} has characters a feed key cannot "
                        "have; copy the link again exactly as you were given it")
    return FeedLink(address, key)


def check_address(url: str, *, feed: str | None = None) -> None:
    """Refuse anything but HTTPS, or plain HTTP to this computer.

    `feed` is the feed address `url` was reached from (None when `url` is the
    feed). This computer is reachable only from a feed that is itself on this
    computer — a local test server. A feed elsewhere that names a pack or a
    redirect here is pointing the reader's own machine at itself, which no
    publisher has a reason to do.
    """
    parts = urllib.parse.urlsplit(url)
    local = is_loopback(parts.hostname or "")
    if local and feed is not None and not is_loopback(urllib.parse.urlsplit(feed).hostname or ""):
        raise FeedError(f"{redact(_without_query(feed))} points at this computer "
                        f"({redact(_without_query(url))}); refusing")
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and local:
        return
    raise FeedError(f"refusing a non-HTTPS address: {redact(url)}")


def is_loopback(host: str) -> bool:
    """Whether a host name or address means this computer."""
    host = host.lower().strip("[]")
    if host in _LOOPBACK_HOSTS or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def origin(url: str) -> tuple[str, str, int | None]:
    """Scheme, host and port: what "the same host" means for sending a key."""
    parts = urllib.parse.urlsplit(url)
    port = parts.port or {"https": 443, "http": 80}.get(parts.scheme)
    return parts.scheme, (parts.hostname or "").lower(), port


class GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only where the request's rules still hold.

    fetch() tags each request with `feed_address`; it is carried to every hop,
    so the loopback rule judges the last hop by the feed, not by the hop before.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        feed = getattr(req, "feed_address", None)
        check_address(newurl, feed=feed if feed is not None else req.full_url)
        if req.has_header("Authorization") and origin(newurl) != origin(req.full_url):
            raise FeedError(f"{_without_query(req.full_url)} redirects to another host; "
                            "refusing to follow it with this feed's key")
        follow = super().redirect_request(req, fp, code, msg, headers, newurl)
        if follow is not None:
            follow.feed_address = feed
        return follow


def _without_query(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(parts._replace(query="", fragment=""))


_OPENER = urllib.request.build_opener(GuardedRedirects)


def fetch(url: str, *, feed: str, key: str | None = None) -> bytes:
    """GET a feed or a pack.

    `feed` is the feed address `url` belongs to (the same string, for the feed
    itself). `key` is sent only when `url` is on the feed's own host; a pack
    hosted elsewhere is fetched without it.
    """
    check_address(feed)
    check_address(url, feed=feed)
    headers = {"User-Agent": USER_AGENT}
    if key is not None and origin(url) == origin(feed):
        headers["Authorization"] = f"Bearer {key}"
    try:
        request = urllib.request.Request(url, headers=headers)
        request.feed_address = feed
    except ValueError:
        raise FeedError(f"could not build a request for {redact(_without_query(url))}") from None
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
    except ValueError:
        # http.client refuses a malformed header or URL with the offending value in
        # the message — which may be the key. Name the address only.
        raise FeedError(f"could not build a request for {redact(_without_query(url))}") from None
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise FeedError(f"refusing oversized download (> {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB): "
                        f"{url}")
    return data


def read_feed(address: str, key: str | None) -> dict:
    """The feed document at `address`, with the reader's key when it has one."""
    try:
        raw = fetch(address, feed=address, key=key)
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

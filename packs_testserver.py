#!/usr/bin/env python3
"""A feed server in memory, for test_packs.py.

Implements the feed contract packs_feed.py reads — `GET <prefix>/feed.json` and
`GET <prefix>/<pack>.zip`, a private feed answering 404 without its key — on
127.0.0.1 with a random port. It records every request with the Authorization
header it carried, so a test can see exactly where a key went.

The same server also answers as `localhost`, which is a different host to the
client: that is how the tests reach "another host" without a second server.

Standard library only.
"""

from __future__ import annotations

import io
import json
import threading
import zipfile
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class Feed:
    prefix: str                      # "/feeds/<code>" or the legacy "/packs/<name>"
    author: str
    author_id: str
    key: str | None = None
    title: str | None = None
    #: pack id -> (version, {finding id: markdown})
    packs: dict[str, tuple[str, dict[str, str]]] = field(default_factory=dict)
    #: pack id -> base URL to list it under instead of this server's own
    pack_hosts: dict[str, str] = field(default_factory=dict)
    #: pack id -> bytes served instead of the real zip (a broken pack)
    raw_packs: dict[str, bytes] = field(default_factory=dict)


class FeedServer:
    def __init__(self):
        self.feeds: list[Feed] = []
        self.redirects: dict[str, str] = {}
        self.requests: list[tuple[str, str | None]] = []
        self._http = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        port = self._http.server_address[1]
        self.url = f"http://127.0.0.1:{port}"
        self.other_host = f"http://localhost:{port}"
        threading.Thread(target=self._http.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._http.shutdown()
        self._http.server_close()

    def add(self, feed: Feed) -> str:
        """Publish a feed; its address."""
        self.feeds.append(feed)
        return self.url + feed.prefix + "/feed.json"

    def auth_seen(self, path_end: str) -> list[str | None]:
        """The Authorization headers every request for a path ending so carried."""
        return [auth for path, auth in self.requests if path.endswith(path_end)]

    # ── answering ────────────────────────────────────────────────────────────

    def answer(self, path: str, auth: str | None) -> tuple[int, dict[str, str], bytes]:
        self.requests.append((path, auth))
        if path in self.redirects:
            return 302, {"Location": self.redirects[path]}, b""
        for feed in self.feeds:
            if not path.startswith(feed.prefix + "/"):
                continue
            if feed.key is not None and auth != f"Bearer {feed.key}":
                return 404, {}, b"not found"
            name = path[len(feed.prefix) + 1:]
            if name == "feed.json":
                return 200, {"Content-Type": "application/json"}, self._document(feed)
            if name.endswith(".zip") and name[:-4] in feed.packs:
                return 200, {"Content-Type": "application/zip"}, _zip(feed, name[:-4])
        return 404, {}, b"not found"

    def _document(self, feed: Feed) -> bytes:
        document: dict = {"author": feed.author, "author_id": feed.author_id, "packs": []}
        if feed.title is not None:
            document["feed"] = {"code": feed.prefix.rsplit("/", 1)[-1], "title": feed.title,
                                "description": ""}
        for pack_id, (version, findings) in feed.packs.items():
            base = feed.pack_hosts.get(pack_id, self.url)
            document["packs"].append({
                "pack": pack_id, "title": pack_id.title(), "version": version,
                "url": f"{base}{feed.prefix}/{pack_id}.zip", "license": "CC BY 4.0",
                "findings": len(findings)})
        return json.dumps(document).encode("utf-8")


def _zip(feed: Feed, pack_id: str) -> bytes:
    if pack_id in feed.raw_packs:
        return feed.raw_packs[pack_id]
    version, findings = feed.packs[pack_id]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("pack.md", (
            f"---\npack: {pack_id}\ntitle: \"{pack_id}\"\nauthor: \"{feed.author}\"\n"
            f"author_id: {feed.author_id}\nversion: {version}\n---\n\n# {pack_id}\n"))
        for finding_id, markdown in findings.items():
            archive.writestr(f"findings/{finding_id}.md", markdown)
    return buffer.getvalue()


def _handler(server: FeedServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, headers, body = server.answer(self.path, self.headers.get("Authorization"))
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    return Handler

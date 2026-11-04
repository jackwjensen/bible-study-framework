#!/usr/bin/env python3
"""
sync_testserver.py — an in-memory sync server for test_sync.py. Not for use.

It implements the protocol in SYNC.md the way a real server is allowed to: it
re-serialises what it is sent (here: stamps `updated:`), rotates refresh tokens,
and answers 409 with its current copy. Tests reach into its state directly to
play the part of an edit made on the server.

Standard library only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CORPUS = "bible"
PREFIX = f"/api/sync/v1/{CORPUS}/"


class FakeServer:
    def __init__(self):
        self.items: dict[str, dict] = {}     # "kind/slug" -> {version, markdown, deleted, seq}
        self.received: dict[str, bytes] = {}  # the exact bytes of the last PUT per item
        self.seq = 0
        self.forget_cursors = False
        self.access, self.refresh = "access-1", "refresh-1"
        self.codes: dict[str, dict] = {}
        self.dependents: dict[str, list[str]] = {}
        self.before_put = None
        self.consent = True
        self.lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(self))
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # ── what a test does "on the server" ─────────────────────────────────────

    def edit(self, key: str, markdown: bytes) -> None:
        with self.lock:
            self.seq += 1
            old = self.items.get(key)
            version = (old["version"] + 1) if old else 1
            self.items[key] = {"version": version, "markdown": markdown, "deleted": False,
                               "seq": self.seq}

    def remove(self, key: str) -> None:
        with self.lock:
            self.seq += 1
            self.items[key].update(deleted=True, seq=self.seq)

    def text(self, key: str) -> bytes | None:
        item = self.items.get(key)
        return None if item is None or item["deleted"] else item["markdown"]

    def issue_code(self, params: dict) -> str:
        code = "code-" + str(len(self.codes) + 1)
        self.codes[code] = params
        return code

    @staticmethod
    def canonical(markdown: bytes) -> bytes:
        text = markdown.decode("utf-8").replace("\r\n", "\n")
        if text.startswith("---\n") and "\nupdated:" not in text.split("\n---", 2)[1]:
            end = text.find("\n---", 3)
            text = text[:end] + "\nupdated: 2026-11-04" + text[end:]
        return text.encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _handler_for(server: FakeServer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            return

        def _send(self, status: int, body=None, headers: dict | None = None) -> None:
            data = body if isinstance(body, bytes) else json.dumps(body or {}).encode("utf-8")
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> bytes:
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))

        def _authorised(self) -> bool:
            if self.headers.get("Authorization") == f"Bearer {server.access}":
                return True
            self._send(401, {"error": "invalid_token", "message": "sign in"},
                       {"WWW-Authenticate": 'Bearer error="invalid_token"'})
            return False

        def do_GET(self) -> None:
            parts = urllib.parse.urlsplit(self.path)
            if parts.path == "/.well-known/oauth-authorization-server":
                self._send(200, {"authorization_endpoint": server.url + "/authorize",
                                 "token_endpoint": server.url + "/token",
                                 "registration_endpoint": server.url + "/register",
                                 "revocation_endpoint": server.url + "/revoke"})
            elif not self._authorised():
                return
            elif parts.path == PREFIX + "changes":
                self._changes(urllib.parse.parse_qs(parts.query).get("since", [""])[0])
            else:
                key = parts.path[len(PREFIX):]
                with server.lock:
                    item = server.items.get(key)
                    if item is None or item["deleted"]:
                        self._send(404, {"error": "not_found", "message": "absent"})
                        return
                    self._send(200, item["markdown"], {
                        "Content-Type": "text/markdown; charset=utf-8",
                        "ETag": f'"{item["version"]}"', "X-Content-SHA256": _sha(item["markdown"])})

        def _changes(self, since: str) -> None:
            if since and server.forget_cursors:
                self._send(410, {"error": "cursor_unknown", "message": "start over"})
                return
            with server.lock:
                after = int(since) if since else None
                out = []
                for key, item in sorted(server.items.items()):
                    if after is None and item["deleted"]:
                        continue
                    if after is not None and item["seq"] <= after:
                        continue
                    kind, slug = key.split("/", 1)
                    out.append({"kind": kind, "slug": slug, "deleted": item["deleted"],
                                "version": None if item["deleted"] else str(item["version"]),
                                "sha256": None if item["deleted"] else _sha(item["markdown"]),
                                "updated_at": "2026-11-04T10:00:00"})
                self._send(200, {"cursor": str(server.seq), "items": out})

        def do_PUT(self) -> None:
            if not self._authorised():
                return
            key = urllib.parse.urlsplit(self.path).path[len(PREFIX):]
            body = self._body()
            if not server.consent:
                self._send(403, {"error": "consent_required",
                                 "message": "Agree to study storage in SourceText first.",
                                 "consent_url": "https://bible.example/?view=findings"})
                return
            if server.before_put:
                hook, server.before_put = server.before_put, None
                hook(key)
            match, none_match = self.headers.get("If-Match"), self.headers.get("If-None-Match")
            with server.lock:
                item = server.items.get(key)
                exists = item is not None and not item["deleted"]
                current = ({"version": str(item["version"]), "sha256": _sha(item["markdown"]),
                            "markdown": item["markdown"].decode("utf-8")} if exists else None)
            if not match and none_match != "*":
                self._send(428, {"error": "precondition_required", "message": "say which"})
                return
            if (none_match == "*" and exists) or (match and (not exists or match.strip('"')
                                                              != str(item["version"]))):
                self._send(409, {"error": "edit_conflict", "current": current, "message": "moved"})
                return
            server.received[key] = body
            server.edit(key, FakeServer.canonical(body))
            stored = server.items[key]
            self._send(200 if match else 201, {
                "version": str(stored["version"]), "sha256": _sha(stored["markdown"]),
                "markdown": stored["markdown"].decode("utf-8")})

        def do_DELETE(self) -> None:
            if not self._authorised():
                return
            key = urllib.parse.urlsplit(self.path).path[len(PREFIX):]
            match = (self.headers.get("If-Match") or "").strip('"')
            item = server.items.get(key)
            if item is None or item["deleted"]:
                self._send(409, {"error": "edit_conflict", "current": None, "message": "gone"})
            elif server.dependents.get(key):
                self._send(409, {"error": "has_dependents", "dependents": server.dependents[key],
                                 "message": "others build on it"})
            elif match != str(item["version"]):
                self._send(409, {"error": "edit_conflict", "message": "moved", "current": {
                    "version": str(item["version"]), "sha256": _sha(item["markdown"]),
                    "markdown": item["markdown"].decode("utf-8")}})
            else:
                server.remove(key)
                self._send(204, b"")

        def do_POST(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/register":
                self._send(201, {"client_id": "client-1"})
                return
            form = {k: v[0] for k, v in urllib.parse.parse_qs(self._body().decode()).items()}
            if path == "/revoke":
                self._send(200, {})
            elif form.get("grant_type") == "authorization_code":
                self._exchange(form)
            elif form.get("grant_type") == "refresh_token" and form.get("refresh_token") == server.refresh:
                number = int(server.refresh.split("-")[1]) + 1
                server.access, server.refresh = f"access-{number}", f"refresh-{number}"
                self._send(200, {"access_token": server.access, "refresh_token": server.refresh,
                                 "expires_in": 3600, "token_type": "Bearer", "scope": "study"})
            else:
                self._send(400, {"error": "invalid_grant", "message": "no"})

        def _exchange(self, form: dict) -> None:
            params = server.codes.pop(form.get("code", ""), None)
            verifier = form.get("code_verifier", "").encode("ascii")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).decode().rstrip("=")
            if (params is None or challenge != params["code_challenge"]
                    or form.get("redirect_uri") != params["redirect_uri"]
                    or form.get("resource") != params["resource"]):
                self._send(400, {"error": "invalid_grant", "message": "no"})
                return
            self._send(200, {"access_token": server.access, "refresh_token": server.refresh,
                             "expires_in": 3600, "token_type": "Bearer", "scope": "study"})

    return Handler

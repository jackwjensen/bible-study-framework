#!/usr/bin/env python3
"""
sync_http.py — talking to a sync server: signing in, and the five calls.

The protocol is written down in SYNC.md and is not specific to any one server.
Signing in is OAuth 2.1 with PKCE and a loopback redirect (RFC 8252): this
script opens the browser, the reader approves on the server, and the browser
hands a one-time code back to a listener on 127.0.0.1. No password ever passes
through this script.

Standard library only.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from typing import ClassVar

from sync_state import TokenStore

USER_AGENT = "bible-study-framework-sync/1"
SCOPE = "study"
API_PREFIX = "/api/sync/v1"
REQUEST_TIMEOUT_SECONDS = 30
#: How long the loopback listener waits for the reader to approve in the browser.
LOGIN_TIMEOUT_SECONDS = 300
#: A 429 is waited out, but never for longer than this per attempt, nor more often.
MAX_RETRY_AFTER_SECONDS = 120
MAX_RATE_LIMIT_RETRIES = 3
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


class SyncError(Exception):
    """A failure the reader is told about in one sentence."""


class NeedLogin(SyncError):
    pass


class ApiError(SyncError):
    def __init__(self, status: int, body: dict):
        self.status = status
        self.body = body
        self.code = str(body.get("error", "") or "")
        super().__init__(str(body.get("message") or f"server answered {status} {self.code}"))


class CursorUnknown(SyncError):
    pass


def check_server_url(url: str) -> str:
    """Normalise a server URL; refuse anything but HTTPS outside this computer."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise SyncError(f"not a server address: {url}")
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK_HOSTS:
        raise SyncError(f"refusing plain http to another computer: {url} (use https)")
    if parts.path.strip("/") or parts.query:
        raise SyncError(f"give the server's root address, with no path: {url}")
    return f"{parts.scheme}://{parts.netloc}"


# ── raw HTTP ─────────────────────────────────────────────────────────────────

def _request(method: str, url: str, *, body: bytes | None = None,
             headers: dict | None = None) -> tuple[int, dict, bytes]:
    merged = {"User-Agent": USER_AGENT, **(headers or {})}
    request = urllib.request.Request(url, data=body, method=method, headers=merged)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()
    except OSError as error:
        raise SyncError(f"could not reach {url}: {error}") from error


def _json(data: bytes) -> dict:
    try:
        parsed = json.loads(data.decode("utf-8")) if data else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _form(url: str, fields: dict) -> tuple[int, dict]:
    body = urllib.parse.urlencode(fields).encode("ascii")
    status, _, data = _request("POST", url, body=body, headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    return status, _json(data)


# ── signing in ───────────────────────────────────────────────────────────────

def discover(server: str) -> dict:
    status, _, data = _request("GET", f"{server}/.well-known/oauth-authorization-server")
    meta = _json(data)
    if status != 200 or "authorization_endpoint" not in meta or "token_endpoint" not in meta:
        raise SyncError(f"{server} does not publish OAuth metadata; is it a sync server?")
    return meta


def _register(meta: dict, client_name: str) -> str:
    endpoint = meta.get("registration_endpoint")
    if not endpoint:
        raise SyncError("the server does not allow this client to register itself")
    payload = json.dumps({
        "client_name": client_name, "redirect_uris": ["http://127.0.0.1/callback"],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "none", "scope": SCOPE}).encode("utf-8")
    status, _, data = _request("POST", endpoint, body=payload,
                               headers={"Content-Type": "application/json"})
    client_id = _json(data).get("client_id")
    if status not in (200, 201) or not client_id:
        raise SyncError(f"client registration refused ({status})")
    return str(client_id)


class _Callback(http.server.BaseHTTPRequestHandler):
    result: ClassVar[dict] = {}

    def do_GET(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if urllib.parse.urlsplit(self.path).path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        type(self).result = {key: values[0] for key, values in query.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<p>Signed in. You can close this tab and return to the terminal.</p>"
)

    def log_message(self, *args) -> None:  # keep the code off the console
        return


def login(server: str, store: TokenStore, *, client_name: str,
          open_browser: Callable[[str], object] = webbrowser.open,
          timeout: float = LOGIN_TIMEOUT_SECONDS) -> None:
    """The one browser sign-in. Stores tokens in the user profile, never the corpus."""
    meta = discover(server)
    saved = store.load(server)
    client_id = saved.get("client_id") or _register(meta, client_name)
    resource = server + API_PREFIX
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    state = secrets.token_urlsafe(24)

    _Callback.result = {}
    listener = http.server.HTTPServer(("127.0.0.1", 0), _Callback)
    listener.timeout = 1
    redirect_uri = f"http://127.0.0.1:{listener.server_address[1]}/callback"
    url = meta["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "scope": SCOPE, "resource": resource})
    # Flushed: under --no-browser, or run in the background, the address is the
    # only way in, and a buffered print would sit unseen while the listener waits.
    print("Sign in and approve at this address (waiting up to "
          f"{int(timeout // 60)} minutes):\n  {url}", flush=True)
    open_browser(url)
    deadline = time.monotonic() + timeout
    try:
        while not _Callback.result and time.monotonic() < deadline:
            listener.handle_request()
    finally:
        listener.server_close()
    answer = _Callback.result
    if not answer:
        raise SyncError("no answer from the browser in time; run login again")
    if answer.get("state") != state:
        raise SyncError("the sign-in answer did not match this request; nothing stored")
    if "code" not in answer:
        raise SyncError(f"sign-in not approved ({answer.get('error', 'no code')})")

    status, tokens = _form(meta["token_endpoint"], {
        "grant_type": "authorization_code", "code": answer["code"],
        "redirect_uri": redirect_uri, "client_id": client_id,
        "code_verifier": verifier, "resource": resource})
    if status != 200 or "access_token" not in tokens:
        raise SyncError(f"the server refused the sign-in code ({status} {tokens.get('error', '')})")
    store.save(server, _token_record(meta, client_id, tokens))


def _token_record(meta: dict, client_id: str, tokens: dict) -> dict:
    lifetime = int(tokens.get("expires_in") or 0)
    return {"client_id": client_id, "token_endpoint": meta["token_endpoint"],
            "revocation_endpoint": meta.get("revocation_endpoint"),
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token"),
            "expires_at": time.time() + lifetime if lifetime else None}


def logout(server: str, store: TokenStore) -> None:
    saved = store.load(server)
    endpoint = saved.get("revocation_endpoint")
    for kind in ("refresh_token", "access_token"):
        if endpoint and saved.get(kind):
            _form(endpoint, {"token": saved[kind], "client_id": saved.get("client_id", "")})
    store.delete(server)


# ── the API ──────────────────────────────────────────────────────────────────

class Client:
    """The five calls, with the bearer token kept fresh."""

    def __init__(self, server: str, corpus: str, store: TokenStore,
                 sleep: Callable[[float], None] = time.sleep):
        self.server = server
        self.corpus = corpus
        self.store = store
        self.sleep = sleep
        self.tokens = store.load(server)
        if not self.tokens.get("access_token"):
            raise NeedLogin("not signed in to this server; run: python sync.py login")

    def _refresh(self) -> None:
        refresh = self.tokens.get("refresh_token")
        if not refresh:
            raise NeedLogin("the sign-in has expired; run: python sync.py login")
        status, tokens = _form(self.tokens["token_endpoint"], {
            "grant_type": "refresh_token", "refresh_token": refresh,
            "client_id": self.tokens["client_id"]})
        if status != 200 or "access_token" not in tokens:
            raise NeedLogin("the sign-in was revoked or has expired; run: python sync.py login")
        # The refresh token rotates: the old one is dead the moment this returns,
        # so the new one is stored before anything else can fail.
        meta = {"token_endpoint": self.tokens["token_endpoint"],
                "revocation_endpoint": self.tokens.get("revocation_endpoint")}
        record = _token_record(meta, self.tokens["client_id"], tokens)
        if not record["refresh_token"]:
            record["refresh_token"] = refresh
        self.tokens = record
        self.store.save(self.server, record)

    def call(self, method: str, path: str, *, body: bytes | None = None,
             headers: dict | None = None) -> tuple[int, dict, bytes]:
        expires = self.tokens.get("expires_at")
        if expires and time.time() > expires - 30:
            self._refresh()
        url = f"{self.server}{API_PREFIX}/{self.corpus}{path}"
        refreshed = False
        rate_limited = 0
        while True:
            merged = {**(headers or {}), "Authorization": f"Bearer {self.tokens['access_token']}"}
            status, response_headers, data = _request(method, url, body=body, headers=merged)
            if status == 401 and not refreshed:
                self._refresh()
                refreshed = True
                continue
            if status == 401:
                raise NeedLogin("the server no longer accepts this sign-in; run: python sync.py login")
            if status == 429 and rate_limited < MAX_RATE_LIMIT_RETRIES:
                rate_limited += 1
                self.sleep(_retry_after(response_headers))
                continue
            return status, response_headers, data

    def changes(self, since: str | None) -> dict:
        query = "?" + urllib.parse.urlencode({"since": since}) if since else ""
        status, _, data = self.call("GET", "/changes" + query)
        body = _json(data)
        if status == 410:
            raise CursorUnknown(body.get("message", "the server no longer knows our place"))
        if status != 200 or "items" not in body:
            raise ApiError(status, body)
        return body

    def get(self, kind: str, slug: str) -> dict | None:
        status, headers, data = self.call("GET", f"/{kind}/{slug}")
        if status == 404:
            return None
        if status != 200:
            raise ApiError(status, _json(data))
        etag = _header(headers, "ETag").strip()
        return {"version": etag.removeprefix("W/").strip('"'), "markdown": data,
                "sha": _header(headers, "X-Content-SHA256").strip().lower()}

    def put(self, kind: str, slug: str, content: bytes, *, version: str | None) -> dict:
        condition = {"If-Match": f'"{version}"'} if version else {"If-None-Match": "*"}
        status, _, data = self.call("PUT", f"/{kind}/{slug}", body=content, headers={
            "Content-Type": "text/markdown; charset=utf-8", **condition})
        body = _json(data)
        if status in (200, 201):
            return body
        raise ApiError(status, body)

    def delete(self, kind: str, slug: str, *, version: str) -> None:
        status, _, data = self.call("DELETE", f"/{kind}/{slug}",
                                    headers={"If-Match": f'"{version}"'})
        if status not in (200, 204):
            raise ApiError(status, _json(data))


def _header(headers: dict, name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return str(value)
    return ""


def _retry_after(headers: dict) -> float:
    try:
        return min(float(_header(headers, "Retry-After") or 1), MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        return 1.0


def host_label() -> str:
    return socket.gethostname() or "this computer"

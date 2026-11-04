#!/usr/bin/env python3
"""
sync_state.py — what `sync.py` remembers between runs, and where.

Two kinds of memory, kept apart on purpose:

- **The sync base**, in the corpus at `.sync/<remote>.state.json`. For every item
  it records what both sides looked like the last time they agreed: the server's
  version and content hash, and the hash of the file on this disk. That is the
  third leg of a three-way comparison, and it is *committed*, so a corpus cloned
  onto a second computer carries the same base as the first.
- **Sign-in tokens**, in the user profile, never in the corpus. A corpus is meant
  to be shared, pushed and zipped; a token in it would travel with it. On
  Windows the tokens are additionally encrypted for the signed-in Windows user
  (DPAPI); elsewhere the file is readable by its owner only.

Standard library only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

#: Item kinds the protocol knows, and the corpus folder each lives in.
KIND_DIRS = {"finding": "findings", "theme": "themes", "thread": "threads"}

#: What a slug may be on the wire — the [[wikilink]] alphabet, length-capped.
_SLUG = re.compile(r"^[a-z0-9-]{1,160}$")

#: Suffix of a conflict file written beside the item it disputes.
CONFLICT_SUFFIX = ".remote"

STATE_VERSION = 1


# ── small file helpers ───────────────────────────────────────────────────────

def content_sha(data: bytes) -> str:
    """Hash of a file's content, independent of how this checkout stores line ends.

    git's autocrlf turns LF into CRLF on one computer and not on another, and a
    Windows editor may add a BOM. Neither is an edit, so neither may look like
    one: the hash is taken over UTF-8 without BOM and with LF line ends.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def raw_sha(data: bytes) -> str:
    """Plain sha256 — what the server's `sha256` fields are computed over."""
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    """Write via a sibling temp file and an atomic rename.

    An interrupted run must leave either the old file or the new one, never half
    of either: a truncated state file would lose the base for every item, and a
    truncated finding would be pushed as an edit.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def slug_ok(slug: str) -> bool:
    return bool(_SLUG.match(slug))


def item_key(kind: str, slug: str) -> str:
    return f"{kind}/{slug}"


def split_key(key: str) -> tuple[str, str]:
    kind, slug = key.split("/", 1)
    return kind, slug


def item_path(root: Path, kind: str, slug: str) -> Path:
    return root / KIND_DIRS[kind] / f"{slug}.md"


def conflict_path(root: Path, kind: str, slug: str) -> Path:
    path = item_path(root, kind, slug)
    return path.with_name(path.name + CONFLICT_SUFFIX)


# ── the local side ───────────────────────────────────────────────────────────

def scan_local(root: Path) -> tuple[dict[str, bytes], list[str]]:
    """Every syncable file on disk as {key: bytes}, plus the names it skipped.

    Only `findings/`, `themes/` and `threads/` — never `imported/`, which holds
    other people's work and is nobody's to publish under this account.
    """
    found: dict[str, bytes] = {}
    skipped: list[str] = []
    for kind, folder in KIND_DIRS.items():
        directory = root / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if not slug_ok(path.stem):
                skipped.append(f"{folder}/{path.name}")
                continue
            found[item_key(kind, path.stem)] = path.read_bytes()
    return found, skipped


def open_conflicts(root: Path) -> list[Path]:
    """Every conflict file still waiting for a person to resolve it."""
    out: list[Path] = []
    for folder in KIND_DIRS.values():
        directory = root / folder
        if directory.is_dir():
            out.extend(sorted(directory.glob(f"*.md{CONFLICT_SUFFIX}")))
    return out


# ── remotes and the sync base ────────────────────────────────────────────────

def sync_dir(root: Path) -> Path:
    return root / ".sync"


def load_remotes(root: Path) -> dict:
    path = sync_dir(root) / "remotes.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("remotes", {})


def save_remotes(root: Path, remotes: dict) -> None:
    text = json.dumps({"remotes": remotes}, indent=2, ensure_ascii=False, sort_keys=True)
    atomic_write(sync_dir(root) / "remotes.json", (text + "\n").encode("utf-8"))


class SyncState:
    """The base for one remote: a cursor, and one record per item.

    A record is `{"version", "remote_sha", "local_sha"}` — `version` None means
    the item did not exist on the server at the base, `local_sha` None that it
    did not exist here. A record may carry `"pending": {"version", "sha"}`: the
    server's side of an unresolved conflict, which becomes the base the moment
    the person resolving it deletes the conflict file.
    """

    def __init__(self, root: Path, remote: str):
        self.path = sync_dir(root) / f"{remote}.state.json"
        self.cursor: str | None = None
        self.items: dict[str, dict] = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.cursor = data.get("cursor")
            self.items = data.get("items", {})

    def save(self) -> None:
        data = {"format": STATE_VERSION, "cursor": self.cursor, "items": self.items}
        text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
        atomic_write(self.path, (text + "\n").encode("utf-8"))

    def record(self, key: str, version: str | None, remote_sha: str | None,
               local_sha: str | None) -> None:
        if version is None and local_sha is None:
            self.items.pop(key, None)
        else:
            self.items[key] = {"version": version, "remote_sha": remote_sha,
                               "local_sha": local_sha}

    def mark_conflict(self, key: str, version: str | None, sha: str | None) -> None:
        base = self.items.setdefault(key, {"version": None, "remote_sha": None,
                                           "local_sha": None})
        base["pending"] = {"version": version, "sha": sha}


# ── sign-in tokens (user profile, never the corpus) ──────────────────────────

def default_token_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "bible-study-framework" / "sync"


class TokenStore:
    """One file per server, holding the client registration and the tokens."""

    def __init__(self, directory: Path | None = None):
        self.directory = directory or default_token_dir()

    def _path(self, server_url: str) -> Path:
        parts = urlsplit(server_url)
        name = re.sub(r"[^A-Za-z0-9.-]", "_", parts.netloc.lower())
        return self.directory / f"{name}.json"

    def load(self, server_url: str) -> dict:
        path = self._path(server_url)
        if not path.exists():
            return {}
        stored = json.loads(path.read_text(encoding="utf-8"))
        return {key: _unseal(value) if key in _SECRET_KEYS else value
                for key, value in stored.items()}

    def save(self, server_url: str, data: dict) -> None:
        sealed = {key: _seal(value) if key in _SECRET_KEYS and value else value
                  for key, value in data.items()}
        path = self._path(server_url)
        atomic_write(path, json.dumps(sealed, indent=2).encode("utf-8"))
        if sys.platform != "win32":
            path.chmod(0o600)

    def delete(self, server_url: str) -> None:
        self._path(server_url).unlink(missing_ok=True)


_SECRET_KEYS = {"access_token", "refresh_token"}


def _seal(value: str) -> str:
    if sys.platform == "win32":
        return "dpapi:" + base64.b64encode(_dpapi(value.encode("utf-8"), protect=True)).decode()
    return "plain:" + value


def _unseal(value: str | None) -> str | None:
    if not value:
        return value
    if value.startswith("dpapi:"):
        return _dpapi(base64.b64decode(value[6:]), protect=False).decode("utf-8")
    if value.startswith("plain:"):
        return value[6:]
    return None  # an unknown scheme is unreadable, not a token


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    """Encrypt or decrypt for the current Windows user (CryptProtectData)."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    crypt32 = ctypes.windll.crypt32
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ui_forbidden = 0x01
    if not call(ctypes.byref(blob_in), None, None, None, None, ui_forbidden,
                ctypes.byref(blob_out)):
        raise OSError("Windows could not " + ("protect" if protect else "unprotect")
                      + " the stored sign-in; run: python sync.py login")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)

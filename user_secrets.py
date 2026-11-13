#!/usr/bin/env python3
"""
user_secrets.py — secrets kept in the user profile, never in the corpus.

Two tools hold a secret that must not travel with the corpus: `sync.py` keeps
its sign-in tokens, and `packs.py` keeps the key of each private feed the reader
follows. A corpus is meant to be shared, pushed and zipped, so neither may be
written inside it. Both live under one per-user folder instead:

- Windows: `%APPDATA%\\bible-study-framework\\<part>\\`, each secret additionally
  encrypted for the signed-in Windows user (DPAPI);
- elsewhere: `$XDG_CONFIG_HOME/bible-study-framework/<part>/` (default
  `~/.config/…`), in files readable by their owner only.

This module is the storage primitive both of them use, so there is one way to
seal a secret, not two. It knows nothing about sync or feeds beyond the small
FeedKeyStore at the bottom; sync's own TokenStore lives in sync_state.py and
builds on the same helpers. The dependency arrow points one way: sync_state.py
and packs.py import this, and this imports neither.

Standard library only.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from pathlib import Path

APP_FOLDER = "bible-study-framework"


def user_dir(part: str) -> Path:
    """The per-user folder for one tool's secrets (`sync`, `feeds`)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_FOLDER / part


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


def write_private(path: Path, data: bytes) -> None:
    """atomic_write, then readable by the owner only where the platform has modes."""
    atomic_write(path, data)
    if sys.platform != "win32":
        path.chmod(0o600)


def seal(value: str, *, remedy: str) -> str:
    """A secret as it may be written to disk: DPAPI on Windows, marked plain elsewhere."""
    if sys.platform == "win32":
        protected = _dpapi(value.encode("utf-8"), protect=True, remedy=remedy)
        return "dpapi:" + base64.b64encode(protected).decode()
    return "plain:" + value


def unseal(value: str | None, *, remedy: str) -> str | None:
    """Read back what seal() wrote; an unknown scheme is unreadable, not a secret."""
    if not value:
        return value
    if value.startswith("dpapi:"):
        return _dpapi(base64.b64decode(value[6:]), protect=False, remedy=remedy).decode("utf-8")
    if value.startswith("plain:"):
        return value[6:]
    return None


def _dpapi(data: bytes, *, protect: bool, remedy: str) -> bytes:
    """Encrypt or decrypt for the current Windows user (CryptProtectData).

    The error names what to run, never the secret: `remedy` is the caller's own
    advice ("run: python sync.py login").
    """
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
                      + " the stored secret; " + remedy)
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


# ── feed keys ────────────────────────────────────────────────────────────────

class FeedKeyStore:
    """The key of each private feed this computer follows, by feed address.

    One file, `keys.json`, mapping each address (as recorded in
    subscriptions.json, never with its `#key=` fragment) to its sealed key. The
    corpus records which feeds are followed; only this file knows how to open
    the private ones, so a corpus cloned onto a second computer lists the feed
    and asks for its link there.
    """

    REMEDY = "subscribe to the feed's link again: python packs.py subscribe <link>"

    def __init__(self, directory: Path | None = None):
        self.directory = directory or user_dir("feeds")
        self.path = self.directory / "keys.json"

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return dict(json.loads(self.path.read_text(encoding="utf-8")).get("keys", {}))

    def _write(self, sealed_by_address: dict[str, str]) -> None:
        text = json.dumps({"keys": sealed_by_address}, indent=2, sort_keys=True)
        write_private(self.path, (text + "\n").encode("utf-8"))

    def get(self, address: str) -> str | None:
        return unseal(self._read().get(address), remedy=self.REMEDY) or None

    def put(self, address: str, key: str) -> None:
        sealed_by_address = self._read()
        sealed_by_address[address] = seal(key, remedy=self.REMEDY)
        self._write(sealed_by_address)

    def remove(self, address: str) -> bool:
        """Forget one feed's key; whether there was one."""
        sealed_by_address = self._read()
        if address not in sealed_by_address:
            return False
        del sealed_by_address[address]
        self._write(sealed_by_address)
        return True

#!/usr/bin/env python3
"""
user_secrets.py — secrets kept in the user profile, never in the corpus.

Two tools hold a secret that must not travel with the corpus: `sync.py` keeps
its sign-in tokens, and `packs.py` keeps the key of each private feed the reader
follows. A corpus is meant to be shared, pushed and zipped, so neither may be
written inside it. Both live under one per-user folder instead,
`$XDG_CONFIG_HOME/bible-study-framework/<part>/` (default `~/.config/…`, on
Windows `%USERPROFILE%\\.config\\…`): on Windows each secret is additionally
encrypted for the signed-in Windows user (DPAPI); elsewhere the files are
readable by their owner only.

**Why not `%APPDATA%` on Windows** — where these files lived until 2026-11-16.
An app installed as an MSIX package (from the Microsoft Store, or the Claude
desktop app) gets a *merged* view of `%APPDATA%` that every process it starts
inherits: what such a process writes there goes to the package's own
`%LOCALAPPDATA%\\Packages\\<package>\\LocalCache\\Roaming\\`, and a copy there
then hides the real file from it. A sign-in begun from one program and
finished from another was stored where the first could never see it again.
`%LOCALAPPDATA%` is redirected the same way; the profile root is not. Old
files are adopted from `%APPDATA%` once (`adopt_legacy`).

This module is the storage primitive both of them use, so there is one way to
seal a secret, not two. It knows nothing about sync or feeds beyond the small
FeedKeyStore at the bottom; sync's own TokenStore lives in sync_state.py and
builds on the same helpers. The dependency arrow points one way: sync_state.py
and packs.py import this, and this imports neither.

Standard library only.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

APP_FOLDER = "bible-study-framework"

#: How often, and how far apart, atomic_write tries the final rename before it
#: gives up. Windows refuses to replace a file another program has open, and a
#: virus scanner or the search indexer opens a file for a moment right after it
#: changes: on 2026-11-16 every write of the sync base was refused once and
#: succeeded on the next try 0.25 s later. Ten tries over about two seconds
#: rides that out; a file that stays locked longer is a real problem, reported.
REPLACE_ATTEMPTS = 10
REPLACE_PAUSE_SECONDS = 0.2


def user_dir(part: str) -> Path:
    """The per-user folder for one tool's secrets (`sync`, `feeds`), on every platform."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_FOLDER / part


def legacy_user_dir(part: str) -> Path | None:
    """Where Windows kept these secrets before 2026-11-16 — `%APPDATA%`; None elsewhere."""
    if sys.platform != "win32":
        return None
    base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return base / APP_FOLDER / part


def adopt_legacy(path: Path, legacy_dir: Path | None,
                 usable: Callable[[Path], bool] = lambda _: True) -> None:
    """Move a secret from the old location to `path`, once, if `path` has none yet.

    `usable` lets a caller decline an old file that holds nothing worth keeping —
    a sign-in that was started but never finished — so that a stale copy seen
    through an app's redirected view cannot take the place of a good one that a
    plain terminal would still adopt. The old file is removed once copied, so the
    two can never drift apart; a removal that fails leaves a harmless orphan.
    """
    if legacy_dir is None or path.exists():
        return
    old = legacy_dir / path.name
    if not old.is_file() or not usable(old):
        return
    write_private(path, old.read_bytes())
    with contextlib.suppress(OSError):
        old.unlink()


def app_copies(legacy_file: Path) -> list[str]:
    """The MSIX packages holding their own redirected copy of a file under `%APPDATA%`.

    A program started by one of those apps reads that copy instead of the file
    itself (see the module docstring). Named, so a message can say why a file
    this program sees differs from the one a plain terminal sees.
    """
    roaming, local = os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")
    if not roaming or not local:
        return []
    try:
        relative = legacy_file.relative_to(roaming)
    except ValueError:
        return []
    packages = Path(local) / "Packages"
    if not packages.is_dir():
        return []
    return sorted(package.name for package in packages.iterdir()
                  if (package / "LocalCache" / "Roaming" / relative).is_file())


def atomic_write(path: Path, data: bytes) -> None:
    """Write via a sibling temp file and an atomic rename.

    An interrupted run must leave either the old file or the new one, never half
    of either: a truncated state file would lose the base for every item, and a
    truncated finding would be pushed as an edit.

    The rename is tried up to REPLACE_ATTEMPTS times, because Windows refuses it
    while another program briefly holds the target (see REPLACE_ATTEMPTS).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        _replace_patiently(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _replace_patiently(source: str, target: Path) -> None:
    """os.replace, retried while the target is held open by another program."""
    for attempt in range(1, REPLACE_ATTEMPTS + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS:
                raise
            time.sleep(REPLACE_PAUSE_SECONDS)


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

    def __init__(self, directory: Path | None = None, legacy_directory: Path | None = None):
        self.directory = directory or user_dir("feeds")
        self.legacy_directory = legacy_directory if directory else legacy_user_dir("feeds")
        self.path = self.directory / "keys.json"

    def _read(self) -> dict[str, str]:
        adopt_legacy(self.path, self.legacy_directory)
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

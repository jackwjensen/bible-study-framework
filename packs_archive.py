#!/usr/bin/env python3
"""
packs_archive.py — the pack itself: building one from your findings, and
placing a received one in `imported/`.

A **pack** is a zip file containing a `pack.md` manifest and a `findings/`
directory. This module knows that format and nothing about where a pack came
from: a file someone emailed and a download from a followed feed are imported
the same way. Following feeds is packs_follow.py; the CLI is packs.py.

Split out of packs.py when private feeds pushed it past the size limit; what is
here moved unchanged, apart from the warning about importing your own work.

Standard library only.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

from study_corpus import FINDINGS_DIR, IMPORTED_DIR, MANIFEST_NAME
from study_frontmatter import load_content, normalize_list
from study_text import romanize


# ── helpers ──────────────────────────────────────────────────────────────────

def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def safe_member_path(name: str) -> Path | None:
    """
    Resolve a zip member to a safe relative path, or None if it must be refused.

    Guards against zip-slip ("../../etc/passwd"), absolute paths, and drive
    letters. Only `pack.md` and `findings/*.md` are ever accepted — a pack is
    text, and nothing else has any business being extracted.
    """
    name = name.replace("\\", "/")
    if name.startswith("/") or ":" in name:
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    if not parts:
        return None
    if parts == [MANIFEST_NAME]:
        return Path(MANIFEST_NAME)
    if len(parts) == 2 and parts[0] == "findings" and parts[1].endswith(".md"):
        return Path("findings") / parts[1]
    return None


def read_manifest(path: Path) -> dict:
    parsed = load_content(path)
    if parsed is None:
        die(f"no manifest at {path}")
    meta = parsed["meta"]
    for field in ("pack", "title", "author", "author_id"):
        if not str(meta.get(field, "") or "").strip():
            die(f"manifest is missing required field '{field}'")
    return meta


def slug_ok(value: str) -> bool:
    """Whether this may be an author id — a folder name on every subscriber's disk.

    ASCII, deliberately. `isalnum()` alone is Unicode-aware and used to accept
    `guds-nåde`, which then became a directory name, a URL segment and half a
    download filename on machines whose filesystems normalise, whose terminals
    cannot render it, and whose HTTP stack must percent-encode it. Underscore is
    allowed here and not in an id, because an author id is a folder, never a
    [[wikilink]] target.
    """
    return value != "" and value.isascii() and all(c.isalnum() or c in "-_" for c in value)


def slugify(text: str) -> str:
    """A kebab-case id in the alphabet [[wikilinks]] can name.

    Romanised first, so a title in Hebrew, Greek or Danish yields something that
    says what it is about. Before that, `isalnum()` was Unicode-aware and simply
    *kept* the original letters — `שלום` slugged to `שלום`, which slug_ok
    approved and no `[[…]]` could ever match. See study_text.romanize for why
    this transliterates rather than translates.
    """
    lowered = romanize(text).lower()
    out = [character if _is_slug_character(character) else "-" for character in lowered]
    return "-".join(part for part in "".join(out).split("-") if part)


def _is_slug_character(character: str) -> bool:
    """ASCII alphanumeric. Spelled out because `isalnum()` is not that."""
    return character.isascii() and character.isalnum()


def default_pack_id(author_id: str, themes: str | None, title: str, is_everything: bool) -> str:
    """
    Derive a pack id when one is not given.

    Named after *what is in it*, so a second pack never collides with the first:
    the theme(s) when exporting by theme, "all" for a whole-corpus export, and a
    slug of the title for an explicit list of findings.
    """
    if themes:
        suffix = "-".join(slugify(t) for t in sorted(t.strip() for t in themes.split(",")) if t.strip())
    elif is_everything:
        suffix = "all"
    else:
        suffix = slugify(title)
        if len(suffix) > 40:                      # trim at a word boundary, not mid-word
            suffix = suffix[:40].rsplit("-", 1)[0]
        suffix = suffix or "selection"
    return f"{author_id}-{suffix}"


def ensure_imported_dir() -> None:
    IMPORTED_DIR.mkdir(exist_ok=True)


def installed_version(author_id: str, pack_id: str) -> str | None:
    manifest = IMPORTED_DIR / author_id / MANIFEST_NAME
    if not manifest.exists():
        return None
    parsed = load_content(manifest)
    if not parsed or str(parsed["meta"].get("pack", "")).strip() != pack_id:
        return None
    return str(parsed["meta"].get("version", "") or "").strip() or None


# ── export ───────────────────────────────────────────────────────────────────

def cmd_export(args: argparse.Namespace) -> int:
    if not slug_ok(args.author_id):
        die("--author-id must be letters, digits, dashes or underscores")

    selected = sorted(FINDINGS_DIR.glob("*.md"))
    if args.findings:
        wanted = {f.strip() for f in args.findings.split(",") if f.strip()}
        selected = [p for p in selected if p.stem in wanted]
        missing = wanted - {p.stem for p in selected}
        if missing:
            die("no such finding(s): " + ", ".join(sorted(missing)))
    if args.themes:
        wanted_themes = {t.strip() for t in args.themes.split(",") if t.strip()}
        kept = []
        for path in selected:
            parsed = load_content(path)
            if parsed and wanted_themes & set(normalize_list(parsed["meta"].get("themes"))):
                kept.append(path)
        selected = kept

    if not selected:
        die("nothing selected — no findings matched")

    pack_id = args.pack or default_pack_id(
        args.author_id, args.themes, args.title,
        is_everything=not (args.findings or args.themes))
    manifest = "\n".join([
        "---",
        f"pack: {pack_id}",
        f'title: "{args.title}"',
        f'author: "{args.author}"',
        f"author_id: {args.author_id}",
        f"version: {args.version}",
        f'license: "{args.license}"',
        f"findings: {len(selected)}",
    ] + ([f"homepage: {args.homepage}"] if args.homepage else []) + [
        "---",
        "",
        f"# {args.title}",
        "",
        args.description or "A pack of biblical-study findings.",
        "",
        "Findings in this pack are the author's own work. Importing them places them in",
        "`imported/` as read-only reference material — they do not become your findings.",
        "",
    ])

    out_path = Path(args.out)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, manifest)
        for path in selected:
            zf.write(path, f"findings/{path.name}")

    print(f"Wrote {out_path} — {len(selected)} findings, pack '{pack_id}' v{args.version}.")
    print("Share it however you like. Recipients run: python packs.py import <file>")
    return 0


# ── import ───────────────────────────────────────────────────────────────────

def import_zip(zip_path: Path, *, assume_yes: bool = False) -> int:
    if not zip_path.exists():
        die(f"no such file: {zip_path}")

    with zipfile.ZipFile(zip_path) as zf:
        members = {}
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = safe_member_path(info.filename)
            if target is None:
                print(f"  skipped (refused): {info.filename}", file=sys.stderr)
                continue
            members[target] = info

        manifest_member = members.get(Path(MANIFEST_NAME))
        if manifest_member is None:
            die(f"{zip_path.name} has no {MANIFEST_NAME} — not a findings pack")

        staging = IMPORTED_DIR / ".staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        for target, info in members.items():
            dest = staging / target
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, length=1024 * 1024)

    meta = read_manifest(staging / MANIFEST_NAME)
    author_id = str(meta["author_id"]).strip()
    if not slug_ok(author_id):
        shutil.rmtree(staging)
        die(f"manifest author_id '{author_id}' is not a safe directory name")

    incoming = sorted((staging / "findings").glob("*.md")) if (staging / "findings").exists() else []
    if not incoming:
        shutil.rmtree(staging)
        die("pack contains no findings")

    # Warn about ids that collide with the user's own work. Not fatal — imports are
    # namespaced by author — but worth seeing, since it usually means shared lineage.
    own_ids = {p.stem for p in FINDINGS_DIR.glob("*.md")}
    collisions = sorted({p.stem for p in incoming} & own_ids)
    looks_like_own_work = len(collisions) == len(incoming)

    print(f"\nPack:     {meta['title']}  ({meta['pack']})")
    print(f"Author:   {meta['author']}  [{author_id}]")
    print(f"Version:  {meta.get('version', '(none)')}")
    print(f"Licence:  {meta.get('license', '(unstated)')}")
    print(f"Findings: {len(incoming)}")
    if collisions:
        print(f"Note:     {len(collisions)} id(s) also exist in your own findings/: "
              + ", ".join(collisions[:5]) + ("…" if len(collisions) > 5 else ""))
    if looks_like_own_work:
        # A warning, not a refusal: nothing here can know whose pack this is, and
        # importing it is harmless. But following your own feed puts a second copy
        # of every finding beside the first, which is not how your work should move.
        print("Warning:  every finding in this pack is already in your own findings/ —")
        print("          this looks like your own work. Your own work moves between your")
        print("          computers with sync.py (see SYNC.md), not by following your own feed.")
    print("\nThese will be written to imported/%s/ as READ-ONLY reference material." % author_id)
    print("They do NOT become your findings. To use one, write your own finding that")
    print("builds_on or tension_with it.\n")

    if not assume_yes:
        answer = input("Import? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            shutil.rmtree(staging)
            print("Aborted; nothing written.")
            return 1

    dest_dir = IMPORTED_DIR / author_id
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(staging / MANIFEST_NAME, dest_dir / MANIFEST_NAME)
    (dest_dir / "findings").mkdir(exist_ok=True)
    for path in incoming:
        shutil.copy2(path, dest_dir / "findings" / path.name)
    shutil.rmtree(staging)

    print(f"Imported {len(incoming)} findings to imported/{author_id}/")
    print("Run: python build_index.py")
    return 0


def import_download(data: bytes, pack_id: str, *, assume_yes: bool) -> bool:
    """Import a pack fetched from a feed; whether it went in.

    The pack id comes from someone else's server; it names the temp file only if
    it is a plain folder-safe name, never a path.
    """
    tmp = IMPORTED_DIR / f".download-{pack_id if slug_ok(pack_id) else 'pack'}.zip"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    try:
        return import_zip(tmp, assume_yes=assume_yes) == 0
    finally:
        tmp.unlink(missing_ok=True)


def cmd_import(args: argparse.Namespace) -> int:
    return import_zip(Path(args.zipfile), assume_yes=args.yes)

#!/usr/bin/env python3
"""
packs.py — share findings with other people, and receive theirs.

A **pack** is a zip file containing a `pack.md` manifest and a `findings/`
directory. Send it however you like: email, a USB stick, a link. There is no
registry, no account, and nobody approves anything.

    python packs.py export --out my-pack.zip --title "Spiritual gifts" \
        --author "Your Name" --author-id yourname
    python packs.py import received-pack.zip
    python packs.py subscribe https://example.com/feed.json
    python packs.py check            # any subscribed pack newer than what you have?
    python packs.py pull             # fetch + import updates from subscriptions

Imported findings land in `imported/<author-id>/` and are READ-ONLY. They are
never merged into your own `findings/` — see PACKS.md for why that matters.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

from build_index import load_content, normalize_list

ROOT = Path(__file__).resolve().parent
FINDINGS_DIR = ROOT / "findings"
IMPORTED_DIR = ROOT / "imported"
SUBSCRIPTIONS_FILE = ROOT / "subscriptions.json"

MANIFEST_NAME = "pack.md"
USER_AGENT = "bible-study-framework-packs/1"
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024  # a findings pack is text; 25 MB is already absurd


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
    return value != "" and all(c.isalnum() or c in "-_" for c in value)


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

    pack_id = args.pack or f"{args.author_id}-pack"
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

    print(f"\nPack:     {meta['title']}  ({meta['pack']})")
    print(f"Author:   {meta['author']}  [{author_id}]")
    print(f"Version:  {meta.get('version', '(none)')}")
    print(f"Licence:  {meta.get('license', '(unstated)')}")
    print(f"Findings: {len(incoming)}")
    if collisions:
        print(f"Note:     {len(collisions)} id(s) also exist in your own findings/: "
              + ", ".join(collisions[:5]) + ("…" if len(collisions) > 5 else ""))
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


def cmd_import(args: argparse.Namespace) -> int:
    return import_zip(Path(args.zipfile), assume_yes=args.yes)


# ── subscriptions ────────────────────────────────────────────────────────────

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


def fetch(url: str) -> bytes:
    if not url.lower().startswith("https://"):
        die(f"refusing to fetch a non-HTTPS url: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (scheme checked)
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        die(f"refusing oversized download (> {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB): {url}")
    return data


def read_feed(url: str) -> dict:
    try:
        return json.loads(fetch(url).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        die(f"feed at {url} is not valid JSON: {exc}")
    except OSError as exc:
        die(f"could not reach {url}: {exc}")
    return {}


def installed_version(author_id: str, pack_id: str) -> str | None:
    manifest = IMPORTED_DIR / author_id / MANIFEST_NAME
    if not manifest.exists():
        return None
    parsed = load_content(manifest)
    if not parsed or str(parsed["meta"].get("pack", "")).strip() != pack_id:
        return None
    return str(parsed["meta"].get("version", "") or "").strip() or None


def cmd_subscribe(args: argparse.Namespace) -> int:
    feeds = load_subscriptions()
    if any(f["url"] == args.url for f in feeds):
        print("Already subscribed.")
        return 0
    feed = read_feed(args.url)
    feeds.append({"url": args.url, "author": feed.get("author", "(unknown)")})
    save_subscriptions(feeds)
    print(f"Subscribed to {feed.get('author', args.url)} — {len(feed.get('packs', []))} pack(s) offered.")
    print("Run: python packs.py check")
    return 0


def survey() -> list[tuple[dict, dict, str | None]]:
    """Return (feed, pack, installed_version) for every pack across subscriptions."""
    rows = []
    for subscription in load_subscriptions():
        feed = read_feed(subscription["url"])
        author_id = str(feed.get("author_id", "")).strip()
        for pack in feed.get("packs", []):
            rows.append((feed, pack, installed_version(author_id, str(pack.get("pack", "")))))
    return rows


def cmd_check(args: argparse.Namespace) -> int:
    rows = survey()
    if not rows:
        print("No subscriptions yet. Add one: python packs.py subscribe <https://…/feed.json>")
        return 0
    updates = 0
    for feed, pack, have in rows:
        offered = str(pack.get("version", "")).strip()
        if have is None:
            state = "not imported"
        elif have != offered:
            state = f"UPDATE available (you have {have})"
            updates += 1
        else:
            state = "up to date"
        print(f"- {feed.get('author', '?')} / {pack.get('title', pack.get('pack'))} "
              f"v{offered} — {state}")
    if updates:
        print(f"\n{updates} update(s). Run: python packs.py pull")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    rows = survey()
    pulled = 0
    for feed, pack, have in rows:
        offered = str(pack.get("version", "")).strip()
        if have == offered:
            continue
        if have is None and not args.all:
            print(f"- skipping {pack.get('pack')} (not yet imported; use --all to add it)")
            continue
        url = str(pack.get("url", "")).strip()
        if not url:
            print(f"- {pack.get('pack')}: feed entry has no url", file=sys.stderr)
            continue
        print(f"\nFetching {pack.get('title', pack.get('pack'))} v{offered} …")
        tmp = IMPORTED_DIR / f".download-{pack.get('pack')}.zip"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(fetch(url))
        try:
            if import_zip(tmp, assume_yes=args.yes) == 0:
                pulled += 1
        finally:
            tmp.unlink(missing_ok=True)
    print(f"\n{pulled} pack(s) updated." if pulled else "\nNothing to update.")
    if pulled:
        print("Run: python build_index.py")
    return 0


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Share and receive packs of findings.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="build a shareable zip from your findings")
    p_export.add_argument("--out", required=True, help="output zip path")
    p_export.add_argument("--title", required=True)
    p_export.add_argument("--author", required=True)
    p_export.add_argument("--author-id", required=True, help="short slug, used as the folder name")
    p_export.add_argument("--pack", help="pack id (default: <author-id>-pack)")
    p_export.add_argument("--version", default="1", help="version string; a date works well")
    p_export.add_argument("--license", default="CC BY 4.0")
    p_export.add_argument("--homepage")
    p_export.add_argument("--description")
    p_export.add_argument("--findings", help="comma-separated finding ids (default: all)")
    p_export.add_argument("--themes", help="comma-separated themes to filter by")
    p_export.set_defaults(func=cmd_export)

    p_import = sub.add_parser("import", help="import a pack zip you received")
    p_import.add_argument("zipfile")
    p_import.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    p_import.set_defaults(func=cmd_import)

    p_sub = sub.add_parser("subscribe", help="follow someone's published feed")
    p_sub.add_argument("url")
    p_sub.set_defaults(func=cmd_subscribe)

    p_check = sub.add_parser("check", help="list subscribed packs and available updates")
    p_check.set_defaults(func=cmd_check)

    p_pull = sub.add_parser("pull", help="fetch and import updates from subscriptions")
    p_pull.add_argument("--all", action="store_true", help="also import packs you don't have yet")
    p_pull.add_argument("-y", "--yes", action="store_true", help="skip confirmation prompts")
    p_pull.set_defaults(func=cmd_pull)

    args = parser.parse_args(argv[1:])
    IMPORTED_DIR.mkdir(exist_ok=True)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

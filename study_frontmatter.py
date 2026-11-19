#!/usr/bin/env python3
"""
study_frontmatter.py — reading a Markdown file with YAML frontmatter, and
normalizing the values it carries.

Split out of study_corpus.py, whose own docstring named three jobs: parsing, the
vocabularies, and the loaders. This is the first of them, and the only one that
knows nothing about findings, themes or threads — it turns bytes into
{'meta': dict, 'body': str} and would work just as well on any other corpus.
That independence is why it is the half worth having on its own: everything else
here depends on it, and it depends on nothing but `re`.

Every function below carries a scar. The quoted-scalar rules, the comma
splitting, the BOM, the block-list item that is a map — each was a silent
data-loss bug, not a style preference, so read the docstrings before
"simplifying" any of them.

Standard library only — no dependencies, no install beyond Python itself.
"""

from __future__ import annotations

import re
from pathlib import Path


def _is_scalar_item(item: str) -> bool:
    """
    Whether a block-list item is a plain value rather than a `- key: value` map.

    A *quoted* item is always a scalar, however many colons it contains. Without
    this, `- "Numbers 22:38"` matched neither the plain-item rule (it has a
    colon) nor the map rule (it starts with a quote, not a word), and the line
    was silently discarded — a `citations:` list of seventeen refs survived as
    one. Verse references are the single most common thing in this corpus to
    write as a quoted block list, so the shape that got dropped was the shape
    the corpus is made of.
    """
    text = item.strip()
    if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
        return True
    return ":" not in text


def unquote_scalar(value: str) -> str:
    """
    Strip surrounding quotes from a frontmatter scalar, honouring the escapes YAML
    defines inside them. A title that quotes a phrase — title: "\\"False prophet\\"
    collapses ..." — is valid YAML and common in this corpus; without unescaping,
    the backslashes leak into INDEX.md and into every rendered link.

    Falls back to the old strip-any-quote behaviour for unbalanced or bare values,
    so nothing that parsed before parses differently now.
    """
    text = value.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'")  # YAML single-quoted doubles the quote
    return text.strip("\"'")


def parse_frontmatter(front: str) -> dict:
    """
    Parse the text between the leading and trailing `---` fences.

    Handles `key: value`, block-style lists (`key:` then indented `- item`), and
    lists of maps (indented `- k: v` blocks). An inline `[a, b]` flow list is
    left as a raw string; normalize_list() below splits it.
    """
    meta: dict = {}
    current_key: str | None = None
    items: list = []
    current_item: dict | None = None

    def flush() -> None:
        nonlocal current_key, items, current_item
        if current_key is not None:
            if current_item is not None:
                items.append(current_item)
                current_item = None
            meta[current_key] = items
            items = []
            current_key = None

    for raw_line in front.split("\n"):
        line = raw_line.rstrip("\r")
        if line.strip() == "":
            continue

        m = re.match(r"^([\w-]+):\s*(.*)$", line)
        if m:
            flush()
            if m.group(2) == "":
                current_key = m.group(1)
            else:
                meta[m.group(1)] = unquote_scalar(m.group(2))
            continue

        if current_key is None:
            continue

        m = re.match(r"^\s+-\s+(.+)$", line)
        if m and _is_scalar_item(m.group(1)):
            if current_item is not None:
                items.append(current_item)
                current_item = None
            items.append(unquote_scalar(m.group(1)))
            continue

        m = re.match(r"^\s+-\s+([\w-]+):\s*(.*)$", line)
        if m:
            if current_item is not None:
                items.append(current_item)
            current_item = {m.group(1): unquote_scalar(m.group(2))}
            continue

        m = re.match(r"^\s+([\w-]+):\s*(.*)$", line)
        if m and current_item is not None:
            current_item[m.group(1)] = unquote_scalar(m.group(2))
            continue

    flush()
    return meta


def load_content(path: Path) -> dict | None:
    """Read a Markdown file into {'meta': dict, 'body': str}; None if absent."""
    if not path.exists():
        return None

    # utf-8-sig, not utf-8: Windows editors (and PowerShell's Set-Content) often
    # write a UTF-8 BOM, which would otherwise sit in front of the opening `---`
    # and silently defeat frontmatter detection. Harmless when no BOM is present.
    raw = path.read_text(encoding="utf-8-sig")
    meta: dict = {}
    body = raw

    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            meta = parse_frontmatter(raw[4:end])
            body = raw[end + 4:].lstrip()

    return {"meta": meta, "body": body}


def split_scalar(scalar: str) -> list[str]:
    """
    Split one scalar (possibly an inline "[a, b]" list) into trimmed tokens.

    Commas *inside quotes* do not split. A plain `scalar.split(",")` fragmented
    every entry that contained a comma — one `sources:` citation became five
    nonsense ones — and citations contain commas as a matter of course
    ("Wenham, Genesis 1-15"). A quote only opens a token when it is the first
    character of that token, so an apostrophe inside an unquoted word ("God's")
    stays an apostrophe rather than swallowing the rest of the line.
    """
    scalar = scalar.strip()
    if scalar == "":
        return []
    if scalar.startswith("[") and scalar.endswith("]"):
        scalar = scalar[1:-1]
    if scalar.strip() == "":
        return []

    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(scalar):
        character = scalar[index]
        if quote is not None:
            if character == "\\" and quote == '"' and index + 1 < len(scalar):
                current.append(scalar[index + 1])
                index += 2
                continue
            if character == quote:
                quote = None
            else:
                current.append(character)
        elif character in "\"'" and "".join(current).strip() == "":
            quote = character
            current = []
        elif character == ",":
            tokens.append("".join(current))
            current = []
        else:
            current.append(character)
        index += 1
    tokens.append("".join(current))

    return [token.strip() for token in tokens if token.strip() != ""]


def normalize_list(value) -> list[str]:
    """
    Normalize a frontmatter value to a list of clean string tokens, from either a
    block-style list, an inline flow list, or a bare scalar. Duplicates are
    dropped, keeping first-seen order.
    """
    out: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                continue  # typed map items are not used in study frontmatter
            # A block list has already said where its items begin and end, so
            # its entries are taken whole. Re-splitting them on commas was how
            # a single "Wenham, Genesis 1-15" source became two.
            text = str(item).strip()
            if text != "":
                out.append(text)
    elif isinstance(value, str):
        out.extend(split_scalar(value))
    else:
        return []
    return list(dict.fromkeys(t for t in out if t != ""))

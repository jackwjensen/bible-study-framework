"""Tests for migrate_citations.py: the one-off rewrite of `scripture:` into `citations:`."""

from __future__ import annotations

from pathlib import Path

import pytest

from migrate_citations import MigrationError, main, migrate_file

HEAD = '---\nid: x\ntitle: "T"\nstatus: developing\n'
TAIL = 'themes: [a]\ncreated: 2026-10-19\n---\n\nBody with scripture: ["John 3:16"]\n'


def doc(middle: str) -> str:
    return HEAD + middle + TAIL


def test_inline_list() -> None:
    out = migrate_file(doc('scripture: ["Mark 2:27", Acts 10:44-48]\n'), "bible")
    assert out == doc('citations: ["bible:Acts 10:44-48", "bible:Mark 2:27"]\n')


def test_block_list() -> None:
    out = migrate_file(doc('scripture:\n  - "Numbers 23:23"\n  - Numbers 22:38\n'), "bible")
    assert out == doc('citations: ["bible:Numbers 22:38", "bible:Numbers 23:23"]\n')


def test_empty_list() -> None:
    assert migrate_file(doc("scripture: []\n"), "bible") == doc("citations: []\n")


def test_idempotent() -> None:
    once = migrate_file(doc('scripture: ["John 3:16"]\n'), "bible")
    assert migrate_file(once, "bible") == once


def test_already_qualified_untouched() -> None:
    out = migrate_file(doc('scripture: ["law:Momsloven § 37", "Mark 2:27"]\n'), "bible")
    assert out == doc('citations: ["bible:Mark 2:27", "law:Momsloven § 37"]\n')


def test_body_untouched_and_key_position_kept() -> None:
    text = doc('scripture: ["John 3:16"]\n')
    out = migrate_file(text, "bible")
    assert out.endswith(TAIL)
    assert out.index("citations:") == text.index("scripture:")


def test_quote_and_backslash_escaped_like_the_canonical_writer() -> None:
    # File text: scripture: ["A \"q\" b\\c"]  -> the value is  A "q" b\c  -> written back escaped.
    out = migrate_file(doc('scripture: ["A \\"q\\" b\\\\c"]\n'), "bible")
    assert out == doc('citations: ["bible:A \\"q\\" b\\\\c"]\n')


def test_crlf_preserved() -> None:
    text = doc('scripture: ["John 3:16"]\n').replace("\n", "\r\n")
    expected = doc('citations: ["bible:John 3:16"]\n').replace("\n", "\r\n")
    assert migrate_file(text, "bible") == expected


def test_no_scripture_field_unchanged() -> None:
    text = doc("")
    assert migrate_file(text, "bible") == text


def test_both_fields_refused() -> None:
    with pytest.raises(MigrationError):
        migrate_file(doc('scripture: ["John 3:16"]\ncitations: ["bible:John 3:16"]\n'), "bible")


def _study(tmp_path: Path, scripture: str) -> Path:
    (tmp_path / "study.md").write_text("---\ncorpus: bible\nword_set: default\nlanguage: en\n---\n",
                                       encoding="utf-8")
    (tmp_path / "findings").mkdir()
    path = tmp_path / "findings" / "a.md"
    path.write_bytes(doc(scripture).encode("utf-8"))
    return path


def test_check_mode_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _study(tmp_path, 'scripture: ["John 3:16"]\n')
    before = path.read_bytes()
    assert main([str(tmp_path), "--check"]) == 1
    assert path.read_bytes() == before
    assert "a.md" in capsys.readouterr().out


def test_write_mode_rewrites_then_check_is_clean(tmp_path: Path) -> None:
    path = _study(tmp_path, 'scripture: ["John 3:16"]\n')
    assert main([str(tmp_path)]) == 0
    assert 'citations: ["bible:John 3:16"]' in path.read_text(encoding="utf-8")
    assert main([str(tmp_path), "--check"]) == 0

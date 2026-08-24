#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/epub_to_chapters.py — разбор EPUB: spine-порядок,
process_archive (реальная запись, dry-run, пустой архив)."""
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import epub_to_chapters as E2C  # noqa: E402


def _make_epub(path: Path, with_spine=True):
    ch1 = ("<html><head><title>Глава 1</title></head>"
           "<body><p>Глава 1</p><p>текст один</p></body></html>")
    ch2 = ("<html><head><title>Глава 2</title></head>"
           "<body><p>Глава 2</p><p>текст два</p></body></html>")
    container = ('<?xml version="1.0"?><container><rootfiles><rootfile '
                 'full-path="OEBPS/content.opf"/></rootfiles></container>')
    opf = ('<?xml version="1.0"?><package><manifest>'
           '<item id="ch1" href="ch1.xhtml"/>'
           '<item id="ch2" href="ch2.xhtml"/></manifest>'
           '<spine><itemref idref="ch1"/><itemref idref="ch2"/></spine>'
           '</package>')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", container)
        if with_spine:
            zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/ch1.xhtml", ch1)
        zf.writestr("OEBPS/ch2.xhtml", ch2)


def test_spine_order(tmp_path):
    epub = tmp_path / "book.epub"
    _make_epub(epub, with_spine=True)
    with zipfile.ZipFile(epub) as zf:
        order = E2C._get_spine_order(zf)
    assert order == ["OEBPS/ch1.xhtml", "OEBPS/ch2.xhtml"]
    # без OPF → None
    epub2 = tmp_path / "nospine.epub"
    _make_epub(epub2, with_spine=False)
    with zipfile.ZipFile(epub2) as zf:
        assert E2C._get_spine_order(zf) is None


def test_process_archive(tmp_path):
    epub = tmp_path / "book.epub"
    _make_epub(epub)
    out = tmp_path / "chapters"
    out.mkdir()
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    n, before, after, removed = E2C.process_archive(
        str(epub), pat, True, True, False, out)
    assert n >= 2 and before > 0 and after > 0
    assert (out / "00000_1_Глава_1" / "chapter.txt").is_file()
    assert (out / "00000_2_Глава_2" / "chapter.txt").is_file()
    content = (out / "00000_1_Глава_1" / "chapter.txt").read_text(encoding="utf-8")
    assert "текст один" in content


def test_process_archive_empty(tmp_path, capsys):
    empty = tmp_path / "empty.epub"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("readme.txt", "не html")
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    out = tmp_path / "chapters"
    out.mkdir()
    n, *_ = E2C.process_archive(str(empty), pat, True, True, False, out)
    assert n == 0
    assert "нет HTML" in capsys.readouterr().out


def test_process_archive_dry_run(tmp_path):
    epub = tmp_path / "book.epub"
    _make_epub(epub)
    out = tmp_path / "chapters"
    out.mkdir()
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    n, *_ = E2C.process_archive(str(epub), pat, True, True, False, out,
                                dry_run=True)
    assert n >= 2
    assert list(out.iterdir()) == []  # ничего не записано

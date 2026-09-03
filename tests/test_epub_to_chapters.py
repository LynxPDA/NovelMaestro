#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cli/epub_to_chapters.py — новая разбивка: канон каталогов (ширина 6),
режимы toc/regex/chunk, offset/skip, предпросмотр, запись."""
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cli"))

import epub_to_chapters as E2C  # noqa: E402


# ── канон имён каталогов ──────────────────────────────────────────────
def test_folder_name_canon_width6():
    """Нули добивают ширину 6: 00000_1, 0000_12, 000_177, 0_12345."""
    assert E2C.folder_name(1, "Глава 1") == "00000_1_Глава_1"
    assert E2C.folder_name(12, "Глава 1") == "0000_12_Глава_1"
    assert E2C.folder_name(177, "Глава 1") == "000_177_Глава_1"
    assert E2C.folder_name(12345, "Глава 1") == "0_12345_Глава_1"
    assert E2C.folder_name(123456, "Глава 1") == "123456_Глава_1"


def test_folder_name_sanitize():
    """Недопустимые символы (Windows и Linux) → '_', пробелы → '_',
    лимит длины, зарезервированные имена Windows."""
    assert E2C.folder_name(1, "Глава: 1?") == "00000_1_Глава_1"
    assert E2C.folder_name(1, "CON") == "00000_1_Chapter"
    assert E2C.folder_name(1, "  Глава  ") == "00000_1_Глава"
    assert E2C.folder_name(1, "x" * 200) == "00000_1_" + "x" * 50
    assert E2C.folder_name(1, "a/b\\c") == "00000_1_a_b_c"
    # пустой заголовок — запасное имя
    assert E2C.folder_name(1, "") == "00000_1_Chapter"


def test_safe_folder_title_limit():
    assert E2C.safe_folder("Глава", 3) == "Гла"
    assert E2C.safe_folder("Глава", 0) == "Глава"  # 0 = без лимита


# ── TXT: regexp-режим ─────────────────────────────────────────────────
def _write_txt(path: Path, text: str):
    path.write_text(text, encoding="utf-8")


def test_split_input_regex(tmp_path):
    txt = tmp_path / "book.txt"
    _write_txt(txt, "Пролог\nвступление\nГлава 1\nпервый\nГлава 2\nвторой\n")
    split_res = [E2C._safe_compile("Глава \\d+", "split-re")]
    entries, before, removed = E2C.split_input(
        txt, "regex", split_res, [], title_limit=50)
    assert before == len("Пролог\nвступление\nГлава 1\nпервый\nГлава 2\nвторой\n")
    assert removed == []
    # пролог — непустая преамбула → секция «Пролог»
    assert [e["heading"] for e in entries] == ["Пролог", "Глава 1", "Глава 2"]
    assert [e["num"] for e in entries] == [1, 2, 3]
    assert entries[1]["body"] == "первый"


def test_split_input_regex_offset(tmp_path):
    txt = tmp_path / "book.txt"
    _write_txt(txt, "Глава 1\nпервый\nГлава 2\nвторой\n")
    split_res = [E2C._safe_compile("Глава \\d+", "split-re")]
    entries, *_ = E2C.split_input(txt, "regex", split_res, [],
                                  num_offset=875)
    # offset 875 → первая папка 000_875_ (ширина 6)
    assert [e["num"] for e in entries] == [875, 876]
    assert E2C.folder_name(entries[0]["num"], entries[0]["heading"]) \
        == "000_875_Глава_1"


def test_split_input_regex_skip_renumber(tmp_path):
    txt = tmp_path / "book.txt"
    _write_txt(txt, "Глава 1\nпервый\nГлава 2\nвторой\nГлава 3\nтретий\n")
    split_res = [E2C._safe_compile("Глава \\d+", "split-re")]
    entries, *_ = E2C.split_input(txt, "regex", split_res, [],
                                  skips={2})
    # seq 2 (Глава 2) пропущен, остальные перенумерованы с 1
    assert [e["seq"] for e in entries] == [1, 3]
    assert [e["num"] for e in entries] == [1, 2]
    assert [e["heading"] for e in entries] == ["Глава 1", "Глава 3"]


def test_split_input_regex_cleanups(tmp_path):
    txt = tmp_path / "book.txt"
    _write_txt(txt, "Глава 1\n本章完\nпервый\n\n\nГлава 2\nвторой\n")
    split_res = [E2C._safe_compile("Глава \\d+", "split-re")]
    clean_res = [E2C._safe_compile("^本章完$", "clean-re",
                                   multiline=True)]
    entries, before, removed = E2C.split_input(txt, "regex", split_res,
                                               clean_res)
    assert "本章完" in removed[0]
    # пустые строки сжаты, маркер удалён из тела
    assert "\n\n\n" not in entries[0]["body"]
    assert "本章完" not in entries[0]["body"]


def test_split_input_regex_no_match(tmp_path, capsys):
    txt = tmp_path / "book.txt"
    _write_txt(txt, "просто текст без маркеров\n")
    with pytest_raises():
        E2C.split_input(txt, "regex", [E2C._safe_compile("Глава", "split-re")],
                        [])


def pytest_raises():
    import pytest
    return pytest.raises(SystemExit)


# ── TXT: chunk-режим ──────────────────────────────────────────────────
def test_split_input_chunk(tmp_path):
    txt = tmp_path / "book.txt"
    _write_txt(txt, "текст без маркеров. " * 800)
    entries, *_ = E2C.split_input(txt, "chunk", [], [],
                                  chunk_size=3000,
                                  chunk_mask="Часть {num}")
    assert len(entries) >= 4
    assert entries[0]["heading"] == "Часть 1"
    assert [e["num"] for e in entries] == list(range(1, len(entries) + 1))


def test_split_input_chunk_mask_requires_num(tmp_path, capsys):
    txt = tmp_path / "book.txt"
    _write_txt(txt, "текст. " * 100)
    with pytest_raises():
        E2C.split_input(txt, "chunk", [], [], chunk_mask="Часть")


# ── EPUB: структура (spine/TOC/h1-h2) ────────────────────────────────
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


def _make_epub_n(path: Path, n=4, with_toc=True):
    """n файлов с h1; spine-порядок; toc.ncx с заголовками «Глава N»."""
    container = ('<?xml version="1.0"?><container><rootfiles><rootfile '
                 'full-path="OEBPS/content.opf"/></rootfiles></container>')
    items = "".join(
        f'<item id="c{i}" href="c{i}.xhtml"/>' for i in range(1, n + 1))
    spine = "".join(
        f'<itemref idref="c{i}"/>' for i in range(1, n + 1))
    opf = (f'<?xml version="1.0"?><package><manifest>{items}</manifest>'
           f'<spine>{spine}</spine></package>')
    toc = ['<?xml version="1.0"?><ncx>']
    for i in range(1, n + 1):
        toc.append(f'<navPoint><navLabel><text>Глава {i}</text></navLabel>'
                   f'<content src="c{i}.xhtml"/></navPoint>')
    toc.append('</ncx>')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        if with_toc:
            zf.writestr("OEBPS/toc.ncx", "".join(toc))
        for i in range(1, n + 1):
            zf.writestr(
                f"OEBPS/c{i}.xhtml",
                f"<html><head><title>Глава {i}</title></head>"
                f"<body><h1>Глава {i}</h1><p>текст {i}</p></body></html>")


def test_spine_order(tmp_path):
    epub = tmp_path / "book.epub"
    _make_epub(epub, with_spine=True)
    with zipfile.ZipFile(epub) as zf:
        order = E2C._get_spine_order(zf)
    assert order == ["OEBPS/ch1.xhtml", "OEBPS/ch2.xhtml"]
    epub2 = tmp_path / "nospine.epub"
    _make_epub(epub2, with_spine=False)
    with zipfile.ZipFile(epub2) as zf:
        assert E2C._get_spine_order(zf) is None


def test_split_input_toc(tmp_path):
    epub = tmp_path / "book.epub"
    _make_epub_n(epub, n=4)
    entries, before, removed = E2C.split_input(epub, "toc", [], [])
    assert before > 0 and removed == []
    assert [e["heading"] for e in entries] == \
        ["Глава 1", "Глава 2", "Глава 3", "Глава 4"]
    assert [e["num"] for e in entries] == [1, 2, 3, 4]
    # дубль заголовка из тела убран: «Глава 1» — только заголовок секции
    assert entries[0]["body"] == "текст 1"


def test_split_input_toc_dup_indented(tmp_path):
    """toc-режим: дубль заголовка первым абзацем тела (с отступом из
    U+2003 EM SPACE и пустой строкой) убирается ВСЕ копии."""
    epub = tmp_path / "book.epub"
    container = ('<?xml version="1.0"?><container><rootfiles><rootfile '
                 'full-path="OEBPS/content.opf"/></rootfiles></container>')
    opf = ('<?xml version="1.0"?><package><manifest>'
           '<item id="c1" href="c1.xhtml"/>'
           '<item id="c2" href="c2.xhtml"/></manifest>'
           '<spine><itemref idref="c1"/><itemref idref="c2"/></spine>'
           '</package>')
    toc = ('<?xml version="1.0"?><ncx>'
           '<navPoint><navLabel><text>Глава 1</text></navLabel>'
           '<content src="c1.xhtml"/></navPoint>'
           '<navPoint><navLabel><text>Глава 2</text></navLabel>'
           '<content src="c2.xhtml"/></navPoint>'
           '</ncx>')
    with zipfile.ZipFile(epub, "w") as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/toc.ncx", toc)
        zf.writestr("OEBPS/c1.xhtml",
                    "<html><body><h1>Глава 1</h1>"
                    "<p>\u2003\u2003Глава 1</p><p>текст один</p></body></html>")
        zf.writestr("OEBPS/c2.xhtml",
                    "<html><body><h1>Глава 2</h1>"
                    "<p>\u2003\u2003Глава 2</p><p>текст два</p></body></html>")
    entries, *_ = E2C.split_input(epub, "toc", [], [])
    assert [e["heading"] for e in entries] == ["Глава 1", "Глава 2"]
    assert entries[0]["body"] == "текст один"
    assert entries[1]["body"] == "текст два"


def test_split_by_patterns_dup_title(tmp_path):
    """regex-режим: дубль заголовка в начале тела — только ТОЧНОЕ
    совпадение; строка, начинающаяся с заголовка, сохраняется."""
    text = ("Глава 1\n"
            "\u2003\u2003Глава 1\n"
            "Глава 1 была первой\n"
            "Глава 2\n"
            "текст два\n")
    src = tmp_path / "book.txt"
    src.write_text(text, encoding="utf-8")
    # якорный паттерн: «Глава 1 была первой» маркером НЕ становится
    entries, *_ = E2C.split_input(
        src, "regex",
        [E2C._safe_compile("^Глава \\d+$", "split-re", multiline=True)],
        [])
    assert [e["heading"] for e in entries] == ["Глава 1", "Глава 2"]
    # дубль «Глава 1» (точное совпадение) убран,
    # «Глава 1 была первой» (продолжение заголовка) — осталась
    assert entries[0]["body"] == "Глава 1 была первой"
    assert entries[1]["body"] == "текст два"


def test_strip_heading_line_prose_kept():
    """Длинная строка, содержащая заголовок, — проза, не дубль."""
    heading = "Глава 1"
    text = ("Глава 1\n"
            "Глава 1 была самой длинной и скучной\n"
            "текст\n")
    out = E2C._strip_heading_line(text, heading)
    assert out == "Глава 1 была самой длинной и скучной\nтекст\n"
    # декорация до +8 символов — дубль
    out = E2C._strip_heading_line("Глава 1（上）\nтекст\n", heading)
    assert out == "текст\n"
    # короткая строка, входящая в заголовок — дубль-маркер
    out = E2C._strip_heading_line(
        "第1章\n正文\n", "第1章 我看见女子剑仙安坐闺房")
    assert out == "正文\n"
    # exact: только точное совпадение
    out = E2C._strip_heading_line("Глава 1（上）\nтекст\n", heading,
                                  exact=True)
    assert out == "Глава 1（上）\nтекст\n"


def test_strip_heading_line_short_heading_safety():
    """Короткие заголовки («I», «1») не поедают прозу: допуск
    декорации пропорционален длине заголовка."""
    # «I. Начало» — проза, не дубль
    out = E2C._strip_heading_line("I\nI. Начало\nтекст\n", "I")
    assert out == "I. Начало\nтекст\n"
    # «I.» — декорация в пределах допуска (min(8, len//2)=1)
    out = E2C._strip_heading_line("I\nI.\nтекст\n", "I")
    assert out == "текст\n"
    # «Глава 1 — начало» длиннее допуска — проза
    out = E2C._strip_heading_line(
        "Глава 1\nГлава 1 — начало\nтекст\n", "Глава 1")
    assert out == "Глава 1 — начало\nтекст\n"


def test_strip_heading_line_nfc():
    """NFD-строка совпадает с NFC-заголовком (нормализация перед
    сравнением) — дубль убирается."""
    out = E2C._strip_heading_line("café\ncafe\u0301\nтекст\n", "café")
    assert out == "текст\n"
    # exact тоже по NFC
    out = E2C._strip_heading_line("café\ncafe\u0301\nтекст\n", "café",
                                  exact=True)
    assert out == "текст\n"


def test_strip_heading_line_promo_tail():
    """Заголовок с промо-хвостом («…【求全订，求月票】») — тоже
    дубль: парный декор снимается целиком; проза — нет."""
    h = "第100章 青山未来观想图，春闱甲榜少年高中"
    out = E2C._strip_heading_line(
        h + "\n" + h + "【求全订，求月票】\n正文\n", h)
    assert out == "正文\n"
    # декорация скобками
    out = E2C._strip_heading_line("Глава 1\nГлава 1（上）\n正文\n",
                                  "Глава 1")
    assert out == "正文\n"
    # пунктуационный хвост
    out = E2C._strip_heading_line("Глава 1\nГлава 1。\n正文\n", "Глава 1")
    assert out == "正文\n"
    # проза в скобках после заголовка — НЕ снимается (хвост не парный)
    out = E2C._strip_heading_line(
        "Глава 1\nГлава 1 была самой длинной\n正文\n", "Глава 1")
    assert out == "Глава 1 была самой длинной\n正文\n"
    # полный заголовок против обрезанного TOC: хвост «快！【…】»
    out = E2C._strip_heading_line(
        "第101章 秦相阳谋，殿试提前，姐姐他真的好\n"
        "第101章 秦相阳谋，殿试提前，姐姐他真的好快！【求全订，求月票】\n"
        "正文\n",
        "第101章 秦相阳谋，殿试提前，姐姐他真的好")
    assert out == "正文\n"


def test_split_input_toc_replace(tmp_path):
    """toc-режим: --replace-re применяется к заголовкам и телу секций
    (как и предпросмотр — в нём тот же split_input)."""
    epub = tmp_path / "book.epub"
    _make_epub_n(epub, n=2)
    replace_res = E2C._parse_replace_re([
        "Глава (\\d+) -> Часть \\1",
        "текст -> текст-очищен",
    ])
    entries, *_ = E2C.split_input(epub, "toc", [], [],
                                  replace_res=replace_res)
    assert [e["heading"] for e in entries] == ["Часть 1", "Часть 2"]
    assert entries[0]["body"] == "текст-очищен 1"
    assert entries[1]["body"] == "текст-очищен 2"


def test_split_input_toc_no_toc(tmp_path):
    """Без toc.ncx заголовок берётся из <title>/<h1>."""
    epub = tmp_path / "book.epub"
    _make_epub_n(epub, n=3, with_toc=False)
    entries, *_ = E2C.split_input(epub, "toc", [], [])
    assert [e["heading"] for e in entries] == ["Глава 1", "Глава 2", "Глава 3"]


def test_split_input_toc_h1_split(tmp_path):
    """Несколько h1 в одном файле — внутренний split."""
    epub = tmp_path / "book.epub"
    container = ('<?xml version="1.0"?><container><rootfiles><rootfile '
                 'full-path="OEBPS/content.opf"/></rootfiles></container>')
    opf = ('<?xml version="1.0"?><package><manifest>'
           '<item id="c1" href="c1.xhtml"/>'
           '<item id="c2" href="c2.xhtml"/>'
           '<item id="c3" href="c3.xhtml"/></manifest>'
           '<spine><itemref idref="c1"/><itemref idref="c2"/>'
           '<itemref idref="c3"/></spine></package>')
    c2 = ("<html><head><title>Глава 2</title></head><body>"
          "<h1>Глава 2</h1><p>текст два</p>"
          "<h1>Глава 2.5</h1><p>текст два с половиной</p></body></html>")
    with zipfile.ZipFile(epub, "w") as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/c1.xhtml",
                    "<html><body><h1>Глава 1</h1><p>текст один</p></body></html>")
        zf.writestr("OEBPS/c2.xhtml", c2)
        zf.writestr("OEBPS/c3.xhtml",
                    "<html><body><h1>Глава 3</h1><p>текст три</p></body></html>")
    entries, *_ = E2C.split_input(epub, "toc", [], [])
    assert [e["heading"] for e in entries] == \
        ["Глава 1", "Глава 2", "Глава 2.5", "Глава 3"]
    assert [e["num"] for e in entries] == [1, 2, 3, 4]


def test_split_input_toc_service(tmp_path):
    """Служебный пункт TOC («Информация») — не глава."""
    epub = tmp_path / "book.epub"
    container = ('<?xml version="1.0"?><container><rootfiles><rootfile '
                 'full-path="OEBPS/content.opf"/></rootfiles></container>')
    opf = ('<?xml version="1.0"?><package><manifest>'
           '<item id="i" href="info.xhtml"/>'
           '<item id="c1" href="c1.xhtml"/>'
           '<item id="c2" href="c2.xhtml"/>'
           '<item id="c3" href="c3.xhtml"/></manifest>'
           '<spine><itemref idref="i"/><itemref idref="c1"/>'
           '<itemref idref="c2"/><itemref idref="c3"/></spine></package>')
    toc = ('<?xml version="1.0"?><ncx>'
           '<navPoint><navLabel><text>Информация</text></navLabel>'
           '<content src="info.xhtml"/></navPoint>'
           '<navPoint><navLabel><text>Глава 1</text></navLabel>'
           '<content src="c1.xhtml"/></navPoint>'
           '<navPoint><navLabel><text>Глава 2</text></navLabel>'
           '<content src="c2.xhtml"/></navPoint>'
           '<navPoint><navLabel><text>Глава 3</text></navLabel>'
           '<content src="c3.xhtml"/></navPoint></ncx>')
    info = ("<html><head><title>Информация</title></head>"
            "<body><p>описание книги</p></body></html>")
    with zipfile.ZipFile(epub, "w") as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/toc.ncx", toc)
        zf.writestr("OEBPS/info.xhtml", info)
        for i in range(1, 4):
            zf.writestr(
                f"OEBPS/c{i}.xhtml",
                f"<html><body><h1>Глава {i}</h1><p>текст {i}</p></body></html>")
    entries, *_ = E2C.split_input(epub, "toc", [], [])
    assert [e["heading"] for e in entries] == ["Глава 1", "Глава 2", "Глава 3"]


def test_split_input_toc_rejects_txt(tmp_path, capsys):
    txt = tmp_path / "book.txt"
    _write_txt(txt, "текст\n")
    with pytest_raises():
        E2C.split_input(txt, "toc", [], [])


def test_split_input_replace_before_split(tmp_path):
    """--replace-re: замены применяются ДО разбивки (нормализация
    маркеров глав); пустая замена — удаление."""
    txt = tmp_path / "book.txt"
    txt.write_text("第1章\nтекст один\n第2章\nтекст два\n",
                   encoding="utf-8")
    split_res = [E2C._safe_compile("Глава \\d+", "split-re")]
    replace_res = E2C._parse_replace_re(["第(\\d+)章 -> Глава \\1"])
    entries, *_ = E2C.split_input(txt, "regex", split_res, [],
                                  replace_res)
    assert [e["heading"] for e in entries] == ["Глава 1", "Глава 2"]
    # удаление: пустая правая часть
    txt2 = tmp_path / "b2.txt"
    txt2.write_text("Глава 1\nтекст (12)\n", encoding="utf-8")
    replace2 = E2C._parse_replace_re(["\\(\\d+\\) ->"])
    entries2, *_ = E2C.split_input(txt2, "regex", split_res, [],
                                   replace2)
    assert "(12)" not in entries2[0]["body"]


def test_parse_replace_re_broken(tmp_path):
    """Битая --replace-re-строка — SystemExit с пояснением."""
    with pytest_raises():
        E2C._parse_replace_re(["без разделителя"])
    with pytest_raises():
        E2C._parse_replace_re([" -> x"])


def test_replace_re_inline_comment(tmp_path):
    """--replace-re: комментарий « # …» в конце строки отрезается;
    решётка без пробела слева — не комментарий."""
    from core.common import strip_line_comment
    txt = tmp_path / "c.txt"
    txt.write_text("Глава 1\nтекст один\n", encoding="utf-8")
    # main() срезает комментарий у --split-re/--clean-re/--replace-re
    split_res = [E2C._safe_compile(
        strip_line_comment("Глава \\d+ # маркер главы"), "split-re")]
    replace_res = E2C._parse_replace_re([
        "^(Глава) -> Глава №\\1  # нумерация",
        "цвет#[0-9a-f]+ -> X",
    ])
    assert len(replace_res) == 2
    assert replace_res[0][1] == "Глава №\\1"
    assert replace_res[1][0].pattern == "цвет#[0-9a-f]+"
    entries, *_ = E2C.split_input(txt, "regex", split_res, [], [])
    assert [e["heading"] for e in entries] == ["Глава 1"]


def test_replace_re_multiline(tmp_path):
    """--replace-re: «^»/«$» матчат СТРОКИ (MULTILINE) — заголовок
    нормализуется в каждой строке, а не только в первой."""
    txt = tmp_path / "ml.txt"
    txt.write_text("第1章\nтекст один\n第2章\nтекст два\n",
                   encoding="utf-8")
    split_res = [E2C._safe_compile("Глава \\d+", "split-re")]
    replace_res = E2C._parse_replace_re(["^第(\\d+)章 -> Глава \\1"])
    entries, *_ = E2C.split_input(txt, "regex", split_res, [],
                                  replace_res)
    assert [e["heading"] for e in entries] == ["Глава 1", "Глава 2"]


def test_replace_re_significant_ws(tmp_path):
    """--replace-re: «^  ->» — пробелы у якоря значимы (удаление
    отступа строки), «\\s+ -> » — сжатие пробелов."""
    pat, repl = E2C._parse_replace_re(["^  ->"])[0]
    assert pat.pattern == "^  " and pat.flags & re.MULTILINE
    src = "   " + "第1章\n" + "    " + "第2章\n"
    assert pat.sub("", src) == " " + "第1章\n" + "  " + "第2章\n"
    pat2, repl2 = E2C._parse_replace_re(["\\s+ -> "])[0]
    assert repl2 == " "
    assert pat2.sub(" ", "a   b\tc") == "a b c"


def test_replace_re_flags_space():
    """--replace-re: «\\s+ ->  |r» — пробел правой части значим
    (сжатие), флаг |r — без эффекта (всегда regexp)."""
    pat, repl = E2C._parse_replace_re(["\\s+ ->  |r"])[0]
    assert repl == " "
    assert pat.sub(" ", "a   b") == "a b"


def test_replace_re_flags_and_nfc(tmp_path):
    """--replace-re: « |i» — регистр; текст и паттерн в NFC —
    NFD-контент (cafe\u0301) совпадает с NFC-паттерном (café)."""
    txt = tmp_path / "nfc.txt"
    txt.write_text("第1章\ncafe\u0301 ХУНГ\n", encoding="utf-8")
    split_res = [E2C._safe_compile("第\\d+章", "split-re")]
    replace_res = E2C._parse_replace_re([
        "café -> кофе",
        "хунг -> Хун |i",
    ])
    entries, *_ = E2C.split_input(txt, "regex", split_res, [],
                                  replace_res)
    assert entries[0]["body"] == "кофе Хун"


def test_split_input_rejects_zip(tmp_path):
    """ZIP не принимается ни в одном режиме — только epub/txt."""
    z = tmp_path / "book.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("1.txt", "Глава 1\nтекст один\n")
    split_res = [E2C._safe_compile("Глава \\d+", "split-re")]
    with pytest_raises():
        E2C.split_input(z, "regex", split_res, [])
    with pytest_raises():
        E2C.split_input(z, "toc", [], [])
    with pytest_raises():
        E2C.split_input(z, "chunk", [], [])


# ── запись и предпросмотр ────────────────────────────────────────────
def _entries3():
    return [
        {"seq": 1, "num": 1, "heading": "Глава 1", "body": "первый"},
        {"seq": 2, "num": 2, "heading": "Глава 2", "body": "второй"},
    ]


def test_write_entries(tmp_path):
    out = tmp_path / "chapters"
    E2C.write_entries(_entries3(), out)
    assert (out / "00000_1_Глава_1" / "chapter.txt").is_file()
    assert (out / "00000_2_Глава_2" / "chapter.txt").is_file()
    c1 = (out / "00000_1_Глава_1" / "chapter.txt").read_text(
        encoding="utf-8")
    assert c1 == "Глава 1\n\nпервый\n"


def test_write_entries_output_types(tmp_path):
    """--output-type: канон имён артефактов стадий."""
    out = tmp_path / "chapters"
    for t in ("chapter", "translated", "redacted", "polished"):
        E2C.write_entries(_entries3(), out, output_type=t)
    assert (out / "00000_1_Глава_1" / "chapter.txt").is_file()
    assert (out / "00000_1_Глава_1" / "translated.txt").is_file()
    assert (out / "00000_1_Глава_1" / "redacted.txt").is_file()
    assert (out / "00000_1_Глава_1" / "polished.txt").is_file()
    # неизвестный тип — fallback на chapter.txt
    assert not (out / "00000_1_Глава_1" / "x.txt").exists()


def test_write_entries_dry_run(tmp_path):
    out = tmp_path / "chapters"
    out.mkdir()
    E2C.write_entries(_entries3(), out, dry_run=True)
    assert list(out.iterdir()) == []  # ничего не записано


def test_write_entries_keeps_old_dirs(tmp_path):
    """write_entries сам старые папки НЕ чистит — это делает main
    (--clean-output); старые каталоги остаются на месте."""
    out = tmp_path / "chapters"
    (out / "00000_1_Старая").mkdir(parents=True)
    E2C.write_entries(_entries3(), out)
    assert (out / "00000_1_Старая").exists()
    assert (out / "00000_1_Глава_1" / "chapter.txt").is_file()


def test_write_preview_json(tmp_path):
    pv = tmp_path / "preview.json"
    E2C.write_preview_json(_entries3(), pv, "book.txt", 1, 50)
    data = json.loads(pv.read_text(encoding="utf-8"))
    assert data["source"] == "book.txt"
    assert data["num_offset"] == 1 and data["title_limit"] == 50
    assert data["entries"][0]["folder"] == "00000_1_Глава_1"
    assert data["entries"][0]["text"] == "первый"


def test_write_preview_json_offset(tmp_path):
    pv = tmp_path / "preview.json"
    entries = [{"seq": 1, "num": 875, "heading": "Глава 1", "body": "x"}]
    E2C.write_preview_json(entries, pv, "book.txt", 875, 50)
    data = json.loads(pv.read_text(encoding="utf-8"))
    assert data["entries"][0]["folder"] == "000_875_Глава_1"


# ── main(): argv → файлы ─────────────────────────────────────────────
def test_main_regex_full(tmp_path, monkeypatch):
    src = tmp_path / "book.txt"
    src.write_text("Глава 1\nтекст один\nГлава 2\nтекст два\n",
                   encoding="utf-8")
    out = tmp_path / "chapters"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "epub_to_chapters.py", "--input", str(src),
        "--mode", "regex", "--split-re", "Глава \\d+",
        "--output", str(out), "--clean-output"])
    E2C.main()
    assert (out / "00000_1_Глава_1" / "chapter.txt").is_file()
    assert (out / "00000_2_Глава_2" / "chapter.txt").is_file()


def test_main_toc_clean_output(tmp_path, monkeypatch):
    """--clean-output удаляет старые папки глав перед записью (epub)."""
    epub = tmp_path / "book.epub"
    _make_epub_n(epub, n=2)
    out = tmp_path / "chapters"
    (out / "00000_1_Старая").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "epub_to_chapters.py", "--input", str(epub),
        "--mode", "toc", "--output", str(out), "--clean-output"])
    E2C.main()
    assert not (out / "00000_1_Старая").exists()
    assert (out / "00000_1_Глава_1" / "chapter.txt").is_file()


def test_main_requires_input(tmp_path, monkeypatch, capsys):
    """Без --input — ошибка (автоподхвата нет)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["epub_to_chapters.py", "--mode", "toc"])
    with pytest_raises():
        E2C.main()
    assert "--input" in capsys.readouterr().err


def test_main_no_lang_flag(capsys):
    """--lang удалён из argparse (unrecognized arguments)."""
    try:
        E2C.build_parser().parse_args(["--input", "x.epub", "--lang", "zh"])
        raise AssertionError("--lang не должен парситься")
    except SystemExit:
        err = capsys.readouterr().err
        assert "unrecognized" in err and "--lang" in err

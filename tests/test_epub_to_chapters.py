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
        zf.writestr("readme.md", "не html")
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    out = tmp_path / "chapters"
    out.mkdir()
    n, *_ = E2C.process_archive(str(empty), pat, True, True, False, out)
    assert n == 0
    assert "нет HTML" in capsys.readouterr().out


def test_process_archive_zip_of_txt(tmp_path):
    """ZIP без HTML, только txt — разбирается как TXT (не «0 файлов»)."""
    z = tmp_path / "book.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("1.txt", "Глава 1\nтекст один\n")
        zf.writestr("2.txt", "Глава 2\nтекст два\n")
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    out = tmp_path / "chapters"
    out.mkdir()
    n, *_ = E2C.process_archive(str(z), pat, True, True, False, out)
    assert n == 2
    assert (out / "00000_1_Глава_1" / "chapter.txt").is_file()
    assert (out / "00000_2_Глава_2" / "chapter.txt").is_file()


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


# ── структурный путь: один xhtml ≈ одна секция (≥3 файлов) ───────────
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


def test_process_archive_structural(tmp_path):
    epub = tmp_path / "book.epub"
    _make_epub_n(epub, n=4)
    out = tmp_path / "chapters"
    out.mkdir()
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    n, before, after, removed = E2C.process_archive(
        str(epub), pat, True, True, False, out)
    assert n == 4
    for i in range(1, 5):
        f = out / f"00000_{i}_Глава_{i}" / "chapter.txt"
        assert f.is_file()
    # заголовок из TOC, h1-строка не дублируется в теле
    c1 = (out / "00000_1_Глава_1" / "chapter.txt").read_text(
        encoding="utf-8")
    assert c1 == "Глава 1\n\nтекст 1\n"


def test_process_archive_structural_no_toc(tmp_path):
    """Без toc.ncx заголовок берётся из <title>/<h1>."""
    epub = tmp_path / "book.epub"
    _make_epub_n(epub, n=3, with_toc=False)
    out = tmp_path / "chapters"
    out.mkdir()
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    n, *_ = E2C.process_archive(str(epub), pat, True, True, False, out)
    assert n == 3
    assert (out / "00000_1_Глава_1" / "chapter.txt").is_file()


def test_process_archive_h1_split(tmp_path):
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
    out = tmp_path / "chapters"
    out.mkdir()
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    n, *_ = E2C.process_archive(str(epub), pat, True, True, False, out)
    assert n == 4
    assert (out / "00000_2_Глава_2" / "chapter.txt").is_file()
    assert (out / "00000_3_Глава_2.5" / "chapter.txt").is_file()


def test_process_archive_toc_service(tmp_path):
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
    out = tmp_path / "chapters"
    out.mkdir()
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    n, *_ = E2C.process_archive(str(epub), pat, True, True, False, out)
    assert n == 3  # инфо-страница пропущена
    assert not (out / "00000_1_Информация").exists()
    assert (out / "00000_1_Глава_1" / "chapter.txt").is_file()


# ── TXT: фоллбэк-чанки, китайские числительные, ложные маркеры ───────
def test_process_txt_chunks(tmp_path):
    """TXT без маркеров — чанки «Часть N», не одна секция."""
    txt = tmp_path / "book.txt"
    txt.write_text("текст без маркеров. " * 800, encoding="utf-8")
    out = tmp_path / "chapters"
    out.mkdir()
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    n, *_ = E2C.process_txt(txt, pat, True, True, False, out,
                            chunk_size=3000)
    assert n >= 4
    assert (out / "00000_1_Часть_1" / "chapter.txt").is_file()
    c1 = (out / "00000_1_Часть_1" / "chapter.txt").read_text(
        encoding="utf-8")
    assert c1.startswith("Часть 1\n\n")


def test_zh_cn_markers(tmp_path):
    """Только 第一章/第二章 — фоллбэк-маркеры (китайские числительные)."""
    txt = tmp_path / "book.txt"
    txt.write_text("第一章\nтекст один\n第二章\nтекст два\n",
                   encoding="utf-8")
    out = tmp_path / "chapters"
    out.mkdir()
    pat = E2C.Patterns(E2C.LANG_PRESETS["zh"], {})
    n, *_ = E2C.process_txt(txt, pat, True, True, False, out)
    assert n == 2
    assert (out / "00000_1_第一章" / "chapter.txt").is_file()
    assert (out / "00000_2_第二章" / "chapter.txt").is_file()


def test_zh_no_false_marker(tmp_path):
    """«详见第5章…» не маркер; 第1章 — маркер (убрали .*?)."""
    txt = tmp_path / "book.txt"
    txt.write_text("第1章\n详见第5章的内容\n第2章\nтекст\n",
                   encoding="utf-8")
    out = tmp_path / "chapters"
    out.mkdir()
    pat = E2C.Patterns(E2C.LANG_PRESETS["zh"], {})
    n, *_ = E2C.process_txt(txt, pat, True, True, False, out)
    assert n == 2
    c1 = (out / "00000_1_第1章" / "chapter.txt").read_text(encoding="utf-8")
    assert "详见第5章" in c1  # строка осталась в теле, не стала главой


def test_zh_volume_prefix(tmp_path):
    """卷二 第5章 — маркер, префикс тома отбрасывается."""
    txt = tmp_path / "book.txt"
    txt.write_text("卷二 第5章\nтекст\n", encoding="utf-8")
    out = tmp_path / "chapters"
    out.mkdir()
    pat = E2C.Patterns(E2C.LANG_PRESETS["zh"], {})
    n, *_ = E2C.process_txt(txt, pat, True, True, False, out)
    assert n == 1
    assert (out / "00000_1_第5章" / "chapter.txt").is_file()


def test_ru_volume_prefix(tmp_path):
    """Том 2. Глава 5 — маркер, префикс тома отбрасывается."""
    txt = tmp_path / "book.txt"
    txt.write_text("Том 2. Глава 5\nтекст\n", encoding="utf-8")
    out = tmp_path / "chapters"
    out.mkdir()
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    n, *_ = E2C.process_txt(txt, pat, True, True, False, out)
    assert n == 1
    assert (out / "00000_1_Глава_5" / "chapter.txt").is_file()


def test_empty_section_skipped(tmp_path):
    """Пустая секция после чисток — папки нет."""
    txt = tmp_path / "book.txt"
    txt.write_text("Глава 1\n\n\nГлава 2\nтекст\n", encoding="utf-8")
    out = tmp_path / "chapters"
    out.mkdir()
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    n, *_ = E2C.process_txt(txt, pat, True, True, False, out)
    assert n == 1
    assert not (out / "00000_1_Глава_1").exists()
    assert (out / "00000_1_Глава_2" / "chapter.txt").is_file()


def test_heading_tail_stripped(tmp_path):
    """Хвост заголовка (完)/(Конец главы) убирается."""
    txt = tmp_path / "book.txt"
    txt.write_text("Глава 5. Название (Конец главы)\nтекст\n",
                   encoding="utf-8")
    out = tmp_path / "chapters"
    out.mkdir()
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    n, *_ = E2C.process_txt(txt, pat, True, True, False, out)
    assert n == 1
    # точка после номера сохраняется (разделитель внутри заголовка)
    assert (out / "00000_1_Глава_5._Название" / "chapter.txt").is_file()


def test_main_clean_output(tmp_path, monkeypatch):
    """--clean-output удаляет старые папки глав перед записью."""
    src = tmp_path / "book.txt"
    src.write_text("Глава 1\nтекст\nГлава 2\nтекст\n", encoding="utf-8")
    out = tmp_path / "chapters"
    (out / "00000_1_Старая").mkdir(parents=True)
    (out / "00000_2_Старая").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "epub_to_chapters.py", "--input", str(src),
        "--output", str(out), "--lang", "ru", "--clean-output"])
    E2C.main()
    assert not (out / "00000_1_Старая").exists()
    assert (out / "00000_1_Глава_1" / "chapter.txt").is_file()


def test_main_warns_old_dirs(tmp_path, monkeypatch, capsys):
    """Без --clean-output — предупреждение, старые папки целы."""
    src = tmp_path / "book.txt"
    src.write_text("Глава 1\nтекст\n", encoding="utf-8")
    out = tmp_path / "chapters"
    (out / "00000_1_Старая").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "epub_to_chapters.py", "--input", str(src),
        "--output", str(out), "--lang", "ru"])
    E2C.main()
    assert (out / "00000_1_Старая").exists()
    assert "уже есть" in capsys.readouterr().out

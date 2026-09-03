#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Интеграционные прогоны скриптов на синтетических данных (без LLM)."""
# pyright: reportMissingImports=false
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cli"))
from conftest import SilentLog, make_ru_chapter_file  # noqa: E402

import clean_and_compile as CAC  # noqa: E402
import translate_check as TC     # noqa: E402
import batch_replace as BR       # noqa: E402


# ══════════════════════════════════════════════════════════════════════
# translate_check: check_chapter + полный прогон main()
# ══════════════════════════════════════════════════════════════════════
def _build_valid_chapter(dir_path: Path, num: int,
                         ch_bytes=3100, pol_bytes=6450):
    """Папка главы, проходящая все проверки пресета 1."""
    d = dir_path / f"00000_{num}_第{num}章"
    d.mkdir(parents=True)
    head = f"Глава {num}\n\n"
    polished = make_ru_chapter_file(head, pol_bytes)
    (d / "polished.txt").write_text(polished, encoding="utf-8")
    (d / "redacted.txt").write_text(polished, encoding="utf-8")  # ratio 1.0
    # источник — английский (проверяется только целевой файл)
    (d / "chapter.txt").write_text("a" * ch_bytes, encoding="utf-8")
    return d


def test_tc_check_chapter_clean(tmp_path):
    d = _build_valid_chapter(tmp_path, 1)
    errors, prev = TC.check_chapter(1, str(d), "polished",
                                    [("redacted", 1.0, 0.05), ("chapter", 2.1, 0.5)],
                                    strict=False, prev_inner_chapter=None)
    assert errors == [], f"ожидалось без ошибок: {errors}"
    assert prev == 1


def test_tc_check_chapter_errors(tmp_path):
    d = _build_valid_chapter(tmp_path, 1)
    # ломаем: латиница, китайский иероглиф, сбой нумерации, лишний заголовок
    broken = ("Глава 1\n\n"
              "слово broken и иероглиф 中 тут\n"
              "дальше идёт длинный русский текст чтобы пройти порог размера. " * 90
              + "\nГлава 9 лишняя\n")
    (d / "polished.txt").write_text(broken, encoding="utf-8")
    (d / "redacted.txt").write_text(broken, encoding="utf-8")
    errors, prev = TC.check_chapter(1, str(d), "polished",
                                    [("redacted", 1.0, 0.05)],
                                    strict=False, prev_inner_chapter=5)
    joined = "\n".join(errors)
    assert "broken" in joined                     # латиница
    assert "中" in joined                          # иероглиф
    assert "последовательность" in joined          # после Главы 5 → Глава 1
    assert "лишн" in joined                        # «Глава 9» после 3-й строки


def test_tc_check_chapter_no_nonrussian(tmp_path):
    """Задача 5: --no-nonrussian отключает проверку иероглифов/латиницы;
    свой regexp — свои паттерны."""
    d = _build_valid_chapter(tmp_path, 1)
    broken = ("Глава 1\n\n"
              "слово broken и иероглиф 中 тут\n"
              + "дальше русский текст. " * 90)
    (d / "polished.txt").write_text(broken, encoding="utf-8")
    (d / "redacted.txt").write_text(broken, encoding="utf-8")
    # проверка не-русских ВЫКЛЮЧЕНА
    errors, _ = TC.check_chapter(1, str(d), "polished",
                                 [("redacted", 1.0, 0.05)],
                                 strict=False, prev_inner_chapter=None,
                                 check_nonrussian=False)
    joined = "\n".join(errors)
    assert "broken" not in joined and "中" not in joined
    # свой regexp — только иероглифы
    errors, _ = TC.check_chapter(
        1, str(d), "polished", [("redacted", 1.0, 0.05)],
        strict=False, prev_inner_chapter=None,
        check_nonrussian=True, nonrussian_regexes=[re.compile(r"[一-鿿]+")])
    joined = "\n".join(errors)
    assert "中" in joined and "broken" not in joined


def test_tc_check_chapter_no_order_and_custom_regex(tmp_path):
    """Задача 5: --no-chapter-order отключает проверку нумерации;
    свой chapter_regex — свой формат заголовка."""
    d = _build_valid_chapter(tmp_path, 1)
    broken = ("Глава 1\n\n"
              + "дальше русский текст. " * 90 + "\nГлава 9 лишняя\n")
    (d / "polished.txt").write_text(broken, encoding="utf-8")
    (d / "redacted.txt").write_text(broken, encoding="utf-8")
    # последовательность ВЫКЛЮЧЕНА
    errors, prev = TC.check_chapter(1, str(d), "polished",
                                    [("redacted", 1.0, 0.05)],
                                    strict=False, prev_inner_chapter=5,
                                    check_chapter_order=False)
    joined = "\n".join(errors)
    assert "последовательность" not in joined
    assert "лишн" not in joined
    assert prev == 1  # номер всё равно обновляется
    # свой формат: «Раздел N» вместо «Глава N»
    custom = ("Раздел 3\n\n" + "дальше русский текст. " * 90)
    (d / "polished.txt").write_text(custom, encoding="utf-8")
    (d / "redacted.txt").write_text(custom, encoding="utf-8")
    errors, prev = TC.check_chapter(
        1, str(d), "polished", [("redacted", 1.0, 0.05)],
        strict=False, prev_inner_chapter=2,
        chapter_regex=re.compile(r"^Раздел\s+(\d+)"))
    joined = "\n".join(errors)
    assert "последовательность" not in joined and prev == 3
    # без своего формата — «Раздел 3» не распознаётся → ошибка
    errors, _ = TC.check_chapter(1, str(d), "polished",
                                 [("redacted", 1.0, 0.05)],
                                 strict=False, prev_inner_chapter=None)
    assert any("Нет «Глава N»" in e for e in errors)


def test_tc_check_chapter_missing_and_fatal(tmp_path):
    d = tmp_path / "00000_1_x"
    d.mkdir()
    # файла нет
    errors, _ = TC.check_chapter(1, str(d), "polished", [], False, None)
    assert any("не найден" in e for e in errors)
    # strict + дубли (два файла под один паттерн) → FATAL
    (d / "polished.txt").write_text("x", encoding="utf-8")
    (d / "Polished.txt").write_text("y", encoding="utf-8")
    errors, _ = TC.check_chapter(1, str(d), "polished", [], True, None)
    assert any("[FATAL]" in e for e in errors)


def test_tc_load_exclusions_empty_default(tmp_path):
    """Слова-исключения: пусто = ничего (дефолт VIP,MVP,【,】,NPC
    убран); TRANSLATE_CHECK_EXCLUDE_WORDS — заполняет."""
    # без .env — пустой список
    assert TC.load_exclusions() == []
    env = tmp_path / ".env"
    env.write_text("TRANSLATE_CHECK_EXCLUDE_WORDS=VIP,NPC\n",
                   encoding="utf-8")
    assert TC.load_exclusions(str(env)) == ["vip", "npc"]
    env.write_text("TRANSLATE_CHECK_EXCLUDE_WORDS=\n", encoding="utf-8")
    assert TC.load_exclusions(str(env)) == []


def test_tc_main_full_run(tmp_path, monkeypatch, capsys):
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    _build_valid_chapter(chapters, 1)
    _build_valid_chapter(chapters, 2, pol_bytes=6500)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "translate_check.py", "--chapters-dir", str(chapters),
        "--preset", "1", "--start", "1", "--end", "2"])
    try:
        TC.main()
        code = 0
    except SystemExit as e:
        code = e.code or 0
    out = capsys.readouterr().out
    report = tmp_path / "logs" / "check_polished_1-2.txt"
    assert report.is_file(), "отчёт не создан"
    text = report.read_text(encoding="utf-8")
    assert "Сводка" in text
    assert "Проверено глав : 2" in text
    assert "С ошибками     : 0" in text




# ══════════════════════════════════════════════════════════════════════
# clean_and_compile: export_titles / compile_book / chunks / нативные EPUB/FB2
# ══════════════════════════════════════════════════════════════════════
@pytest.fixture()
def cac_env(tmp_path, monkeypatch):
    """Изолированное окружение для clean_and_compile (глобальный cfg)."""
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    for n in (1, 2):
        d = chapters / f"00000_{n}_x"
        d.mkdir()
        text = (f"Глава {n}\n\n"
                f"Текст главы номер {n}. 【Квадратные】 скобки…\n"
                ". . .\n"
                "Продолжение текста.\n")
        (d / "polished.txt").write_text(text, encoding="utf-8")
    # экспорт называется по имени папки проекта → chdir в именованную
    proj_dir = tmp_path / "Тестовая_Книга"
    proj_dir.mkdir()
    monkeypatch.chdir(proj_dir)
    old = {k: v for k, v in vars(CAC.cfg).items()}
    CAC.cfg.base_dir = str(chapters)
    CAC.cfg.tmp_dir = str(tmp_path)
    CAC.cfg.start, CAC.cfg.end = 1, 2
    CAC.cfg.compile_type = "polished"
    CAC.cfg.set_order = 1
    CAC.cfg.paywall = ""
    CAC.cfg.volume = ""
    yield tmp_path
    for k, v in old.items():
        setattr(CAC.cfg, k, v)


def test_cac_titles_mode_removed(cac_env):
    """Режим titles выпилен из CLI: выбора нет, export_titles отсутствует."""
    assert not hasattr(CAC, "export_titles")
    choices = CAC.build_parser()._actions
    mode_choices = [a for a in choices if a.dest == "mode"][0].choices
    assert mode_choices is not None and "titles" not in mode_choices


def test_cac_compile_txt(cac_env):
    # кастомные заголовки подхватываются из titles-файла
    Path(CAC.cfg.titles_file).write_text(
        "1:::Глава 1 (правленый)\n2:::Глава 2 (правленый)\n", encoding="utf-8")
    CAC.compile_book("txt")
    out = Path(CAC.cfg.tmp_dir) / "compiled_1_2_txt.txt"
    assert out.is_file()
    content = out.read_text(encoding="utf-8")
    assert "[Глава 1 (правленый) :|: 1]" in content
    assert "【Квадратные】" in content      # 【】 НЕ заменяются (косметика убрана)
    assert "* * *" in content              # «. . .» → сепаратор
    log = Path("logs") / "build_log_txt.txt"
    assert log.is_file() and "[OK] Глава 1" in log.read_text(encoding="utf-8")


def test_cac_compile_txt_plain(cac_env):
    """txt-plain: заголовки КАК В ПЕРЕВОДЕ — без markdown-префиксов
    и rulate-тегов «[:|:]» (в отличие от txt = TXT (Rulate))."""
    CAC.compile_book("txt-plain")
    out = Path(CAC.cfg.tmp_dir) / "compiled_1_2_txt-plain.txt"
    assert out.is_file()
    content = out.read_text(encoding="utf-8")
    assert "Глава 1" in content
    assert "Глава 2" in content
    assert "# Глава 1" not in content
    assert ":|:" not in content
    assert "[Глава 1" not in content


def test_cac_separators_unified(cac_env):
    """Единый алгоритм разделителей: строки точек, звёздочек и многоточий
    → один сепаратор «* * *»; смешанные строки тоже сворачиваются."""
    d = cac_env / "chapters" / "00000_3_x"
    d.mkdir()
    (d / "polished.txt").write_text(
        "Глава 3\n\nТекст.\n. . .\nТекст2.\n* * * *\nТекст3.\n… … …\n"
        "Конец.\n* . * .\nФинал.\n",
        encoding="utf-8")
    CAC.cfg.start, CAC.cfg.end = 1, 3
    CAC.compile_book("txt")
    out = Path(CAC.cfg.tmp_dir) / "compiled_1_3_txt.txt"
    content = out.read_text(encoding="utf-8")
    # 4 разделителя из главы 3 + 2 из глав 1–2 (фикстура: «. . .»)
    assert content.count("* * *") == 6
    assert ". . ." not in content
    assert "… … …" not in content
    assert "* . * ." not in content


def test_cac_no_clean_keeps_chapter_lines(cac_env):
    """Очистка из compile убрана: «Глава N» в теле главы НЕ удаляется
    (удаление лишних заголовков — через batch_replace)."""
    d = cac_env / "chapters" / "00000_3_x"
    d.mkdir()
    body = ("Глава 3\n\n"
            + "\n".join(f"Строка {i}" for i in range(1, 11))
            + "\nГлава 5 в середине\n\nГлава 7 ближе к концу\n")
    (d / "polished.txt").write_text(body, encoding="utf-8")
    CAC.cfg.start, CAC.cfg.end = 1, 3
    CAC.compile_book("txt")
    content = (Path(CAC.cfg.tmp_dir) / "compiled_1_3_txt.txt").read_text(
        encoding="utf-8")
    assert "Глава 5 в середине" in content
    assert "Глава 7 ближе к концу" in content
    assert "Строка 10" in content  # тело сохранилось


def test_cac_title_from_first_line(cac_env):
    """Глава без «Глава N»: заголовок — первая непустая строка
    (вики-глава «Wiki Новеллы»); ###/#### остаются в теле."""
    d = cac_env / "chapters" / "00000_3_x"
    d.mkdir()
    (d / "polished.txt").write_text(
        "Wiki Новеллы\n\n### Линь Шуй\n\n#### Описание\n\nТекст статьи.\n",
        encoding="utf-8")
    CAC.cfg.start, CAC.cfg.end = 1, 3
    CAC.compile_book("txt")
    out = Path(CAC.cfg.tmp_dir) / "compiled_1_3_txt.txt"
    content = out.read_text(encoding="utf-8")
    assert "[Wiki Новеллы :|: 3]" in content   # заголовок из первой строки
    assert "### Линь Шуй" in content           # внутренние заголовки целы


def test_cac_title_from_prologue_first_line(cac_env):
    """Нет «Глава N»: заголовок — первая непустая строка, даже если она
    начинается с «Пролог.» — она целиком становится названием главы."""
    d = cac_env / "chapters" / "00000_3_x"
    d.mkdir()
    (d / "polished.txt").write_text(
        "Пролог. Пример\n\nТекст пролога.\n", encoding="utf-8")
    CAC.cfg.start, CAC.cfg.end = 1, 3
    CAC.compile_book("txt")
    out = Path(CAC.cfg.tmp_dir) / "compiled_1_3_txt.txt"
    content = out.read_text(encoding="utf-8")
    assert "[Пролог. Пример :|: 3]" in content
    assert "Пролог. Пример" not in content.replace("[Пролог. Пример :|: 3]", "")


def test_cac_epub_wiki_chapter_title(cac_env):
    """EPUB: вики-глава в TOC как «Wiki Новеллы»; ###/#### не создают
    пунктов содержания (остаются текстом в теле)."""
    import zipfile
    d = cac_env / "chapters" / "00000_3_x"
    d.mkdir()
    (d / "polished.txt").write_text(
        "Wiki Новеллы\n\n### Линь Шуй\n\n#### Описание\n\nТекст статьи.\n",
        encoding="utf-8")
    CAC.cfg.start, CAC.cfg.end = 1, 3
    CAC.cfg.epub_cover = str(Path(CAC.cfg.tmp_dir) / "nonexistent.jpg")
    CAC.compile_book("epub")
    epub = Path(CAC.cfg.tmp_dir) / "Тестовая_Книга_1_3.epub"
    assert epub.is_file()
    with zipfile.ZipFile(epub) as zf:
        nav = zf.read("OEBPS/nav.xhtml").decode("utf-8")
        assert "Wiki Новеллы" in nav            # TOC: вики-глава
        assert "Глава 3" not in nav             # без фолбека «Глава N»
        ch3 = zf.read("OEBPS/chapter_0003.xhtml").decode("utf-8")
        assert "<h1>Wiki Новеллы</h1>" in ch3
        # внутренние ###/#### — простой текст в <p>, пунктов TOC нет
        assert "### Линь Шуй" in ch3
        assert "#### Описание" in ch3
        assert ch3.count("<navPoint") == 0


def test_cac_compile_txt_no_titles_fallback(cac_env):
    CAC.compile_book("txt")
    out = Path(CAC.cfg.tmp_dir) / "compiled_1_2_txt.txt"
    content = out.read_text(encoding="utf-8")
    assert "[Глава 1 :|: 1]" in content   # заголовок из самого файла


def test_cac_compile_epub_native(cac_env):
    """Нативная генерация EPUB: ZIP-структура, обложка, главы."""
    import zipfile
    cover = Path(CAC.cfg.tmp_dir) / "cover.jpg"
    cover.write_bytes(b"fake")
    CAC.cfg.epub_cover = str(cover)
    CAC.compile_book("epub")
    epub = Path(CAC.cfg.tmp_dir) / "Тестовая_Книга_1_2.epub"
    assert epub.is_file(), "EPUB не создан"
    with zipfile.ZipFile(epub) as zf:
        names = zf.namelist()
        assert names[0] == "mimetype"                       # первый элемент
        assert zf.read("mimetype") == b"application/epub+zip"
        assert "META-INF/container.xml" in names
        assert "OEBPS/content.opf" in names
        assert "OEBPS/toc.ncx" in names
        assert "OEBPS/nav.xhtml" in names
        assert "OEBPS/cover.jpg" in names                   # обложка
        assert "OEBPS/cover.xhtml" in names
        assert "OEBPS/chapter_0001.xhtml" in names
        assert "OEBPS/chapter_0002.xhtml" in names
        ch1 = zf.read("OEBPS/chapter_0001.xhtml").decode("utf-8")
        assert "Глава 1" in ch1
        assert "Квадратные" in ch1


def test_cac_compile_epub_no_cover(cac_env):
    """EPUB без обложки — не падает, обложка отсутствует в ZIP."""
    import zipfile
    CAC.cfg.epub_cover = str(Path(CAC.cfg.tmp_dir) / "nonexistent.jpg")
    CAC.compile_book("epub")
    epub = Path(CAC.cfg.tmp_dir) / "Тестовая_Книга_1_2.epub"
    assert epub.is_file()
    with zipfile.ZipFile(epub) as zf:
        names = zf.namelist()
        assert "OEBPS/cover.jpg" not in names
        assert "OEBPS/chapter_0001.xhtml" in names


def test_cac_compile_fb2_native(cac_env):
    """Нативная генерация FB2: XML-структура, обложка, donate-страница."""
    cover = Path(CAC.cfg.tmp_dir) / "cover.jpg"
    cover.write_bytes(b"fake")
    CAC.cfg.fb2_cover = str(cover)
    # Создаём donate-файл для теста (ищется в ./source от cwd — папка проекта)
    src = Path.cwd() / "source"
    src.mkdir(exist_ok=True)
    (src / "donate.txt").write_text(
        "# Поддержать проект\n\nТестовая ссылка\n", encoding="utf-8")
    CAC.compile_book("fb2")
    fb2 = Path(CAC.cfg.tmp_dir) / "Тестовая_Книга_1_2.fb2"
    assert fb2.is_file(), "FB2 не создан"
    content = fb2.read_text(encoding="utf-8")
    assert "<coverpage>" in content                         # обложка нативно
    assert '<binary id="cover"' in content
    assert "Глава 1" in content
    # страница поддержки добавлена из файла
    compiled = Path(CAC.cfg.tmp_dir) / "compiled_1_2_fb2.txt"
    assert "Поддержать проект" in compiled.read_text(encoding="utf-8")
    # --no-donate
    CAC.cfg.add_donate_page = 0
    CAC.compile_book("fb2")
    assert "Поддержать проект" not in compiled.read_text(encoding="utf-8")


def test_cac_compile_fb2_no_cover(cac_env):
    """FB2 без обложки (--no-fb2-cover) — coverpage отсутствует."""
    CAC.cfg.fb2_inject_cover = 0
    CAC.compile_book("fb2")
    fb2 = Path(CAC.cfg.tmp_dir) / "Тестовая_Книга_1_2.fb2"
    assert fb2.is_file()
    content = fb2.read_text(encoding="utf-8")
    assert "<coverpage>" not in content
    assert "Глава 1" in content


def test_cac_epub_title_not_duplicated(cac_env):
    """Заголовок главы в EPUB только в <h1>, не дублируется в теле."""
    import re, zipfile
    CAC.cfg.add_donate_page = 0
    CAC.compile_book("epub")
    epub = Path(CAC.cfg.tmp_dir) / "Тестовая_Книга_1_2.epub"
    with zipfile.ZipFile(epub) as zf:
        ch1 = zf.read("OEBPS/chapter_0001.xhtml").decode("utf-8")
    m = re.search(r"<body>.*</body>", ch1, re.DOTALL)
    assert m is not None
    body = m.group(0)
    assert body.count("<h1>") == 1
    assert "<p>Глава 1</p>" not in body
    assert "# Глава" not in body


def test_cac_fb2_title_not_duplicated(cac_env):
    """Заголовок главы в FB2 только в <title>, не дублируется в <p>."""
    import re
    CAC.cfg.add_donate_page = 0
    CAC.compile_book("fb2")
    fb2 = Path(CAC.cfg.tmp_dir) / "Тестовая_Книга_1_2.fb2"
    content = fb2.read_text(encoding="utf-8")
    m = re.search(r"<section>(.*?)</section>", content, re.DOTALL)
    assert m is not None
    sec = m.group(1)
    sec_no_title = re.sub(r"<title>.*?</title>", "", sec, flags=re.DOTALL)
    assert sec.count("<title>") == 1
    assert "Глава 1" not in sec_no_title


def test_cac_load_donate_page_no_file(cac_env, monkeypatch, tmp_path):
    """load_donate_page: None если файла нет (нет хардкода)."""
    monkeypatch.chdir(tmp_path)
    result = CAC.load_donate_page()
    assert result is None


def test_cac_load_donate_page_external(cac_env, monkeypatch, tmp_path):
    """load_donate_page: внешний файл в source/."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "source"
    src.mkdir()
    (src / "donate.txt").write_text(
        "# Моя поддержка\n\nПоддержите!\n\n- Ссылка\n", encoding="utf-8")
    title, body = CAC.load_donate_page()
    assert title == "Моя поддержка"
    assert body[0] == "Поддержите!"
    assert "- Ссылка" in body


def test_cac_load_donate_page_explicit_path(cac_env, monkeypatch, tmp_path):
    """load_donate_page: явный путь имеет приоритет."""
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "custom_donate.txt"
    f.write_text("# Кастом\nТекст\n", encoding="utf-8")
    title, body = CAC.load_donate_page(str(f))
    assert title == "Кастом"
    assert body == ["Текст"]


def test_cac_compile_epub_donate_external(cac_env, monkeypatch):
    """EPUB с внешним donate-файлом: заголовок из файла."""
    import zipfile
    src = Path.cwd() / "source"
    src.mkdir(exist_ok=True)
    (src / "donate.txt").write_text(
        "# Поддержать нас\n\nСсылка сюда\n", encoding="utf-8")
    CAC.cfg.donate_file = ""
    CAC.compile_book("epub")
    epub = Path(CAC.cfg.tmp_dir) / "Тестовая_Книга_1_2.epub"
    with zipfile.ZipFile(epub) as zf:
        names = zf.namelist()
        # Последняя глава — donate
        last_ch = zf.read(f"OEBPS/chapter_{len(names) - 5:04d}.xhtml").decode("utf-8")
    assert "Поддержать нас" in last_ch


def test_cac_compile_chunks(cac_env):
    CAC.compile_chunks("txt", 1)
    assert (Path(CAC.cfg.tmp_dir) / "compiled_1_1_txt.txt").is_file()
    assert (Path(CAC.cfg.tmp_dir) / "compiled_2_2_txt.txt").is_file()
    # диапазон восстановлен
    assert (CAC.cfg.start, CAC.cfg.end) == (1, 2)


def test_cac_main_argparse(cac_env, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "clean_and_compile.py", "--mode", "txt",
        "--chapters-dir", CAC.cfg.base_dir, "--tmp-dir", str(cac_env)])
    CAC.main()
    assert (cac_env / "compiled_1_2_txt.txt").is_file()


def test_cac_main_bad_range(cac_env, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "clean_and_compile.py", "--mode", "txt",
        "--chapters-dir", CAC.cfg.base_dir,
        "--start", "9", "--end", "2"])
    with pytest.raises(SystemExit):
        CAC.main()


# ══════════════════════════════════════════════════════════════════════
# epub_to_chapters: полный прогон main() по .txt
# ══════════════════════════════════════════════════════════════════════
def test_e2c_main_txt_flow(tmp_path, monkeypatch, capsys):
    import epub_to_chapters as E2C
    src = tmp_path / "book.txt"
    src.write_text("Глава 1\nтекст один\nГлава 2\nтекст два\n", encoding="utf-8")
    out = tmp_path / "chapters"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "epub_to_chapters.py", "--input", str(src),
        "--output", str(out), "--mode", "regex",
        "--split-re", "Глава \\d+"])
    E2C.main()
    assert (out / "00000_1_Глава_1" / "chapter.txt").is_file()
    assert (out / "00000_2_Глава_2" / "chapter.txt").is_file()


def test_e2c_main_toc_flow(tmp_path, monkeypatch, capsys):
    """epub по TOC: канон папок, дубль заголовка в теле удалён."""
    import zipfile
    import epub_to_chapters as E2C
    src = tmp_path / "book.epub"
    container = ('<?xml version="1.0"?><container><rootfiles><rootfile '
                 'full-path="OEBPS/content.opf"/></rootfiles></container>')
    opf = ('<?xml version="1.0"?><package><manifest>'
           '<item id="c1" href="c1.xhtml"/>'
           '<item id="c2" href="c2.xhtml"/></manifest>'
           '<spine><itemref idref="c1"/><itemref idref="c2"/></spine>'
           '</package>')
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/c1.xhtml", "<html><body><h1>Глава 1</h1>"
                     "<p>текст один</p></body></html>")
        zf.writestr("OEBPS/c2.xhtml", "<html><body><h1>Глава 2</h1>"
                     "<p>текст два</p></body></html>")
    out = tmp_path / "chapters"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "epub_to_chapters.py", "--input", str(src),
        "--output", str(out), "--mode", "toc"])
    E2C.main()
    assert (out / "00000_1_Глава_1" / "chapter.txt").is_file()
    assert (out / "00000_2_Глава_2" / "chapter.txt").is_file()
    c1 = (out / "00000_1_Глава_1" / "chapter.txt").read_text(
        encoding="utf-8")
    assert "текст один" in c1 and c1.count("Глава 1") == 1


def test_e2c_main_no_input(tmp_path, monkeypatch, capsys):
    """Без --input — ошибка (автоподхвата исходника нет)."""
    import epub_to_chapters as E2C
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["epub_to_chapters.py", "--mode", "toc"])
    with pytest.raises(SystemExit):
        E2C.main()
    assert "--input" in capsys.readouterr().err


# ── дополнительные ветки translate_check ──────────────────────────────
def test_tc_main_no_chapters(tmp_path, monkeypatch, capsys):
    empty = tmp_path / "chapters"
    empty.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "translate_check.py", "--chapters-dir", str(empty),
        "--preset", "1"])
    with pytest.raises(SystemExit) as ei:
        TC.main()
    assert ei.value.code == 1


def test_tc_main_gaps_and_dup_folders(tmp_path, monkeypatch, capsys):
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    _build_valid_chapter(chapters, 1)
    # глава 3 в двух папках → дубль
    _build_valid_chapter(chapters, 3)
    extra = chapters / "00000_3_дубль"
    extra.mkdir()
    (extra / "polished.txt").write_text("дубль", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "translate_check.py", "--chapters-dir", str(chapters),
        "--preset", "1", "--start", "1", "--end", "3", "--strict"])
    TC.main()
    out = capsys.readouterr().out
    assert "дубли папок" in out.lower()
    report = (tmp_path / "logs" / "check_polished_1-3.txt").read_text(
        encoding="utf-8")
    assert "Папка не найдена" in report       # глава 2 — разрыв диапазона
    assert "Дубль папок" in report            # глава 3
    assert "[FATAL] Глава пропущена." in report


def test_tc_check_chapter_small_file_and_missing_ref(tmp_path):
    d = tmp_path / "00000_1_x"
    d.mkdir()
    (d / "polished.txt").write_text("короткий файл", encoding="utf-8")
    errors, _ = TC.check_chapter(
        1, str(d), "polished", [("redacted", 1.0, 0.05)],
        strict=False, prev_inner_chapter=None)
    joined = "\n".join(errors)
    assert "Размер файла слишком мал" in joined
    assert "эталон 'redacted' не найден" in joined


# ══════════════════════════════════════════════════════════════════════
# batch_replace: полный прогон main() на синтетических главах
# ══════════════════════════════════════════════════════════════════════
def _br_chapter(tmp_path, num, text="Хунг пришёл."):
    d = tmp_path / "chapters" / f"00000_{num}_x"
    d.mkdir(parents=True)
    f = d / "polished.txt"
    f.write_text(text, encoding="utf-8")
    return d, f


def _br_rules(tmp_path, text="Хунг -> Хун\n"):
    p = tmp_path / "prompts" / "replacements.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_br_main_dry_run(tmp_path, monkeypatch, capsys):
    _br_rules(tmp_path)
    d, f = _br_chapter(tmp_path, 1)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["batch_replace.py", "--dry-run"])
    assert BR.main() == 0
    out = capsys.readouterr().out
    assert "[DRY]" in out and "Всего замен:     1" in out
    assert f.read_text(encoding="utf-8") == "Хунг пришёл."  # не изменился


def test_br_main_apply(tmp_path, monkeypatch, capsys):
    _br_rules(tmp_path)
    d, f = _br_chapter(tmp_path, 1)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["batch_replace.py"])
    assert BR.main() == 0
    assert f.read_text(encoding="utf-8") == "Хун пришёл."
    assert "[FIX]" in capsys.readouterr().out


def test_br_main_type_filter(tmp_path, monkeypatch):
    _br_rules(tmp_path)
    d, _ = _br_chapter(tmp_path, 1)
    red = d / "redacted.txt"
    red.write_text("Хунг пришёл.", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["batch_replace.py", "--type", "redacted"])
    assert BR.main() == 0
    assert red.read_text(encoding="utf-8") == "Хун пришёл."
    assert (d / "polished.txt").read_text(encoding="utf-8") == "Хунг пришёл."


def test_br_main_range(tmp_path, monkeypatch):
    _br_rules(tmp_path)
    _, f1 = _br_chapter(tmp_path, 1)
    _, f2 = _br_chapter(tmp_path, 2)
    _, f3 = _br_chapter(tmp_path, 3)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["batch_replace.py", "--start", "2", "--end", "2"])
    assert BR.main() == 0
    assert f1.read_text(encoding="utf-8") == "Хунг пришёл."
    assert f2.read_text(encoding="utf-8") == "Хун пришёл."
    assert f3.read_text(encoding="utf-8") == "Хунг пришёл."


def test_br_main_no_rules(tmp_path, monkeypatch, capsys):
    _br_rules(tmp_path, text="# пусто\n")
    _br_chapter(tmp_path, 1)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["batch_replace.py"])
    assert BR.main() == 1
    assert "не содержит ни одной замены" in capsys.readouterr().out


def test_br_main_missing_rules_file(tmp_path, monkeypatch, capsys):
    _br_chapter(tmp_path, 1)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["batch_replace.py"])
    assert BR.main() == 1
    assert "Не удалось прочитать" in capsys.readouterr().out

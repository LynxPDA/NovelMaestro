#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cli/wiki.py — генерация вики: форматирование, статьи, сборка,
run_wiki_generation и main() целиком (мок LLM)."""
# pyright: reportMissingImports=false
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cli"))
from conftest import SilentLog  # noqa: E402

import wiki as WIKI  # noqa: E402


# ══════════════════════════════════════════════════════════════════════
# форматирование / генерация статьи / сборка
# ══════════════════════════════════════════════════════════════════════
def test_format_term_for_prompt():
    item = {"translation": "линь шуй", "type": "Person (female)",
            "notes": "главная героиня"}
    s = WIKI._format_term_for_prompt(item)
    assert s.startswith("Перевод: Линь шуй")
    assert "Person (female)" in s and "главная героиня" in s
    item2 = {"term": "т"}
    s2 = WIKI._format_term_for_prompt(item2)
    assert "Other" in s2


def test_generate_article(monkeypatch):
    seen = {}

    def fake_llm(system_prompt, user_content, logger=None, **kw):
        seen["sys"] = system_prompt
        seen["user"] = user_content
        seen["kw"] = kw
        return "ГОТОВАЯ СТАТЬЯ"

    monkeypatch.setattr(WIKI, "llm_request", fake_llm)
    item = {"term": "林水", "translation": "Линь Шуй",
            "type": "Person (female)", "notes": ""}
    out = WIKI.generate_article(
        item, ["фрагмент один", "фрагмент два"],
        [("Чэнь Ян", "Person", 5)], "Person",
        "Статья о {translation}. {relations_label}.",
        {"base_url": "h", "model": "m", "api_key": "", "max_retries": 1,
         "timeout": 10, "temperature": None, "thinking": None},
        SilentLog())
    assert out == "ГОТОВАЯ СТАТЬЯ"
    assert "Статья о Линь Шуй" in seen["sys"]
    assert "Чэнь Ян" in seen["user"] and "[Фрагмент 1]" in seen["user"]


def test_assemble(tmp_path):
    sections = [
        ("Персонажи", [("линь шуй", "# Линь Шуй\nописание")]),
        ("Пустой тип", []),
    ]
    out = str(tmp_path / "wiki.md")
    WIKI.assemble_wiki(sections, out, rulate=False, logger=SilentLog())
    text = Path(out).read_text(encoding="utf-8")
    assert "# Wiki — Энциклопедия новеллы" in text
    assert "## Содержание" in text and "Линь шуй" in text
    assert "# Линь Шуй" in text
    # rulate: заголовки сдвинуты, содержания нет
    WIKI.assemble_wiki(sections, out, rulate=True, logger=SilentLog())
    text = Path(out).read_text(encoding="utf-8")
    assert ":|:" in text and "## Содержание" not in text
    assert "## Линь Шуй" in text
    # пусто — файл не создаётся
    WIKI.assemble_wiki([], str(tmp_path / "нет.md"), False, SilentLog())
    assert not (tmp_path / "нет.md").exists()


def test_assemble_toc_toggles(tmp_path):
    """toc/toc_links: оглавление и якоря-ссылки в обычном режиме."""
    sections = [("Персонажи", [("Линь Шуй", "## Линь Шуй\nтекст")])]
    out = str(tmp_path / "w.md")
    # без оглавления
    WIKI.assemble_wiki(sections, out, False, SilentLog(),
                       toc=False, toc_links=True)
    text = Path(out).read_text(encoding="utf-8")
    assert "## Содержание" not in text
    assert "<a id=" not in text
    # оглавление без ссылок
    WIKI.assemble_wiki(sections, out, False, SilentLog(),
                       toc=True, toc_links=False)
    text = Path(out).read_text(encoding="utf-8")
    assert "## Содержание" in text
    assert "  - Линь Шуй" in text
    assert "[Линь Шуй]" not in text and "<a id=" not in text
    # оглавление со ссылками: якорь в теле + ссылка в содержании
    WIKI.assemble_wiki(sections, out, False, SilentLog(),
                       toc=True, toc_links=True)
    text = Path(out).read_text(encoding="utf-8")
    assert "  - [Линь Шуй](#линь-шуй)" in text
    assert '<a id="линь-шуй"></a>' in text


def test_assemble_rulate_html(tmp_path):
    """rulate_html: заголовки — span font-size, списки <ul>, <hr />."""
    sections = [
        ("Персонажи", [("Чу Синь",
                         "## Чу Синь\n\nСотрудница.\n\n"
                         "### Описание\n\n- Пункт **жирный**\n- Ещё\n\n---\n")]),
    ]
    out = str(tmp_path / "wiki.html")
    WIKI.assemble_wiki(sections, out, True, SilentLog(),
                       rulate_html=True)
    text = Path(out).read_text(encoding="utf-8")
    # заголовок — не тег <h1..h6>, а указание шрифта
    assert '<p><strong><span style="font-size:20px">Чу Синь</span>' in text
    assert '<p><strong><span style="font-size:16px">Описание</span>' in text
    assert "<h1>" not in text and "<h2>" not in text
    assert "<ul>" in text and "<li>Пункт <strong>жирный</strong></li>" in text
    assert "<hr />" in text  # и межстатейный разделитель — <hr />
    assert text.count("<hr />") >= 2
    assert "## Содержание" not in text


def test_md_to_html_escaping():
    """md_to_html: экранирование и inline-жирный."""
    html = WIKI.md_to_html("<script>alert(1)</script>\n\n**важно** & <тег>")
    assert "&lt;script&gt;" in html and "&amp;" in html
    assert "<strong>важно</strong>" in html


def test_slugify():
    assert WIKI._slugify("Линь Шуй") == "линь-шуй"
    assert WIKI._slugify("Чу Синь (female)") == "чу-синь-female"
    assert WIKI._slugify("  ") == "-"


def test_stats_helpers():
    # TYPE_NAMES_RU покрывает базовые типы
    assert WIKI.TYPE_NAMES_RU["Person"]


# ══════════════════════════════════════════════════════════════════════
# run_wiki_generation целиком
# ══════════════════════════════════════════════════════════════════════
_NOVEL = ("Глава 1\n\n"
          "Линь Шуй шла по дороге. Ветер трепал её волосы.\n\n"
          "Линь Шуй думала о своём пути. Путь был долог.\n")


def test_run_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(WIKI, "llm_request",
                        lambda *a, **k: "СТАТЬЯ О ГЕРОЕ")
    db = WIKI.build_fts_index(_NOVEL, 1000, SilentLog())
    ner = [{"term": "林水", "translation": "Линь Шуй",
            "type": "Person (female)", "count": 5}]
    out = str(tmp_path / "wiki.md")
    WIKI.run_wiki_generation(
        ner, db, exclude_types=set(), top_n=10, min_count=2,
        context_chunks=8, near_distance=64,
        system_prompt="Статья о {translation}.",
        llm_args={"base_url": "h", "model": "m", "api_key": "",
                  "max_retries": 1, "timeout": 10, "temperature": None,
                  "thinking": None},
        max_workers=1, save_interval=5, cache_file=str(tmp_path / "c.json"),
        output_path=out, co_pairs=[], co_top=5, rulate=False,
        logger=SilentLog(),
    )
    text = Path(out).read_text(encoding="utf-8")
    assert "СТАТЬЯ О ГЕРОЕ" in text
    assert "Линь Шуй" in text
    # кэш сохранён
    cache = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert "article_Линь Шуй" in cache


def test_run_generation_cache_hit(tmp_path, monkeypatch):
    """Статья в кэше — LLM не вызывается."""
    cache_file = tmp_path / "c.json"
    cache_file.write_text(json.dumps(
        {"article_Линь Шуй": "ИЗ КЭША"}, ensure_ascii=False),
        encoding="utf-8")

    def boom(*a, **k):
        raise AssertionError("LLM не должен вызываться")

    monkeypatch.setattr(WIKI, "llm_request", boom)
    db = WIKI.build_fts_index(_NOVEL, 1000, SilentLog())
    ner = [{"term": "林水", "translation": "Линь Шуй",
            "type": "Person (female)", "count": 5}]
    out = str(tmp_path / "wiki.md")
    WIKI.run_wiki_generation(
        ner, db, set(), 10, 2, 8, 64, "Статья о {translation}.",
        {"base_url": "h", "model": "m", "api_key": "", "max_retries": 1,
         "timeout": 10, "temperature": None, "thinking": None},
        1, 5, str(cache_file), out, [], 5, False, SilentLog(),
    )
    assert "ИЗ КЭША" in Path(out).read_text(encoding="utf-8")


def test_run_generation_empty(tmp_path, monkeypatch):
    """Нет терминов после фильтрации — ранний выход."""
    monkeypatch.setattr(WIKI, "llm_request",
                        lambda *a, **k: "X")
    db = WIKI.build_fts_index(_NOVEL, 1000, SilentLog())
    ner = [{"term": "林水", "translation": "", "count": 5}]
    out = str(tmp_path / "wiki.md")
    WIKI.run_wiki_generation(
        ner, db, set(), 10, 2, 8, 64, "Статья о {translation}.",
        {"base_url": "h", "model": "m", "api_key": "", "max_retries": 1,
         "timeout": 10, "temperature": None, "thinking": None},
        1, 5, str(tmp_path / "c.json"), out, [], 5, False, SilentLog(),
    )
    assert not Path(out).exists()


def test_run_generation_llm_fail(tmp_path, monkeypatch):
    """LLM вернул None — статья пропускается, wiki не создаётся."""
    monkeypatch.setattr(WIKI, "llm_request", lambda *a, **k: None)
    db = WIKI.build_fts_index(_NOVEL, 1000, SilentLog())
    ner = [{"term": "林水", "translation": "Линь Шуй",
            "type": "Person (female)", "count": 5}]
    out = str(tmp_path / "wiki.md")
    WIKI.run_wiki_generation(
        ner, db, set(), 10, 2, 8, 64, "Статья о {translation}.",
        {"base_url": "h", "model": "m", "api_key": "", "max_retries": 1,
         "timeout": 10, "temperature": None, "thinking": None},
        1, 5, str(tmp_path / "c.json"), out, [], 5, False, SilentLog(),
    )
    assert not Path(out).exists()


# ══════════════════════════════════════════════════════════════════════
# main() целиком (мок LLM)
# ══════════════════════════════════════════════════════════════════════
_NOVEL_MAIN = ("Глава 1\n\n"
               "Линь Шуй шла по дороге и думала о своём.\n\n"
               "Линь Шуй остановилась у реки.\n")


def test_main_full(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "novel.txt").write_text(_NOVEL_MAIN, encoding="utf-8")
    (tmp_path / "ner.json").write_text(json.dumps([
        {"term": "林水", "translation": "Линь Шуй",
         "type": "Person (female)", "count": 7},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(WIKI, "determine_model", lambda *a, **k: "модель-х")
    monkeypatch.setattr(WIKI, "llm_request", lambda *a, **k: "СТАТЬЯ ГЕРОЯ")
    monkeypatch.setattr(sys, "argv", [
        "wiki.py", "novel.txt", "--ner_file", "ner.json",
        "--output", "wiki.md", "--host", "http://h", "--model", "m",
        "--threads", "1", "--top", "10"])
    WIKI.main()
    text = (tmp_path / "wiki.md").read_text(encoding="utf-8")
    assert "СТАТЬЯ ГЕРОЯ" in text and "Линь Шуй" in text
    assert (tmp_path / "tmp" / "wiki_cache.json").is_file()


def test_main_missing_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(WIKI, "determine_model", lambda *a, **k: "модель-х")
    monkeypatch.setattr(sys, "argv", [
        "wiki.py", "нет.txt", "--ner_file", "ner.json",
        "--host", "http://h", "--model", "m"])
    WIKI.main()  # файл не найден → тихий возврат
    (tmp_path / "novel.txt").write_text("текст", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "wiki.py", "novel.txt", "--ner_file", "нет.json",
        "--host", "http://h", "--model", "m"])
    WIKI.main()
    assert not (tmp_path / "wiki.md").exists()


def test_main_bad_params(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "novel.txt").write_text("текст", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "wiki.py", "novel.txt", "--top", "0", "--host", "http://h"])
    with pytest.raises(SystemExit):
        WIKI.main()


def test_main_rulate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "novel.txt").write_text(_NOVEL_MAIN, encoding="utf-8")
    (tmp_path / "ner.json").write_text(json.dumps([
        {"term": "林水", "translation": "Линь Шуй",
         "type": "Person (female)", "count": 7},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(WIKI, "determine_model", lambda *a, **k: "модель-х")
    monkeypatch.setattr(WIKI, "llm_request", lambda *a, **k: "СТАТЬЯ")
    monkeypatch.setattr(sys, "argv", [
        "wiki.py", "novel.txt", "--ner_file", "ner.json",
        "--output", "wiki.md", "--host", "http://h", "--model", "m",
        "--threads", "1", "--rulate-mode"])
    WIKI.main()
    text = (tmp_path / "wiki.md").read_text(encoding="utf-8")
    assert ":|:" in text  # rulate-таблица вместо содержания


def test_main_compile_chapters(tmp_path, monkeypatch):
    """wiki: --compile-chapters собирает главы в память (без txt)."""
    chapters = tmp_path / "chapters"
    d1 = chapters / "00000_1_x"
    d1.mkdir(parents=True)
    (d1 / "chapter.txt").write_text(
        "Глава 1\n\nЛинь Шуй шла по дороге.\n\nЛинь Шуй думала.\n",
        encoding="utf-8")
    (tmp_path / "ner.json").write_text(json.dumps([
        {"term": "林水", "translation": "Линь Шуй",
         "type": "Person (female)", "count": 3},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(WIKI, "determine_model", lambda *a, **k: "модель-х")
    monkeypatch.setattr(WIKI, "llm_request", lambda *a, **k: "СТАТЬЯ")
    monkeypatch.setattr(sys, "argv", [
        "wiki.py", "--compile-chapters", "--type", "chapter",
        "--start", "1", "--end", "1", "--ner_file", "ner.json",
        "--output", "wiki.md", "--host", "http://h", "--model", "m",
        "--threads", "1"])
    WIKI.main()
    text = (tmp_path / "wiki.md").read_text(encoding="utf-8")
    assert "СТАТЬЯ" in text and "Линь Шуй" in text
    # txt-файл не создавался — сборка шла в память
    assert not (tmp_path / "compiled_1_1_chapter.txt").exists()


def test_main_compile_chapters_missing(tmp_path, monkeypatch):
    """wiki: --compile-chapters без глав — ранний выход."""
    (tmp_path / "chapters").mkdir()
    (tmp_path / "ner.json").write_text("[]", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(WIKI, "determine_model", lambda *a, **k: "модель-х")
    monkeypatch.setattr(sys, "argv", [
        "wiki.py", "--compile-chapters", "--ner_file", "ner.json",
        "--host", "http://h", "--model", "m"])
    WIKI.main()
    assert not (tmp_path / "wiki.md").exists()


def test_main_no_file_no_compile(tmp_path, monkeypatch):
    """wiki: ни file, ни --compile-chapters — ошибка."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(WIKI, "determine_model", lambda *a, **k: "модель-х")
    monkeypatch.setattr(sys, "argv", [
        "wiki.py", "--ner_file", "ner.json", "--host", "http://h",
        "--model", "m"])
    WIKI.main()
    assert not (tmp_path / "wiki.md").exists()


def test_main_rulate_html(tmp_path, monkeypatch):
    """wiki: --rulate-html → wiki.txt с span-заголовками (HTML-разметка
    внутри txt-файла)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "novel.txt").write_text(_NOVEL_MAIN, encoding="utf-8")
    (tmp_path / "ner.json").write_text(json.dumps([
        {"term": "林水", "translation": "Линь Шуй",
         "type": "Person (female)", "count": 7},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(WIKI, "determine_model", lambda *a, **k: "модель-х")
    monkeypatch.setattr(WIKI, "llm_request", lambda *a, **k: "СТАТЬЯ")
    monkeypatch.setattr(sys, "argv", [
        "wiki.py", "novel.txt", "--ner_file", "ner.json",
        "--output", "wiki.md", "--host", "http://h", "--model", "m",
        "--threads", "1", "--rulate-html"])
    WIKI.main()
    html = (tmp_path / "wiki.txt").read_text(encoding="utf-8")
    assert "СТАТЬЯ" in html
    assert "<h1>" not in html
    assert "## Содержание" not in html
    assert ":|:" not in html
    assert not (tmp_path / "wiki.html").exists()


def test_main_toc_off(tmp_path, monkeypatch):
    """wiki: --no-toc — оглавления нет."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "novel.txt").write_text(_NOVEL_MAIN, encoding="utf-8")
    (tmp_path / "ner.json").write_text(json.dumps([
        {"term": "林水", "translation": "Линь Шуй",
         "type": "Person (female)", "count": 7},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(WIKI, "determine_model", lambda *a, **k: "модель-х")
    monkeypatch.setattr(WIKI, "llm_request", lambda *a, **k: "СТАТЬЯ")
    monkeypatch.setattr(sys, "argv", [
        "wiki.py", "novel.txt", "--ner_file", "ner.json",
        "--output", "wiki.md", "--host", "http://h", "--model", "m",
        "--threads", "1", "--no-toc"])
    WIKI.main()
    text = (tmp_path / "wiki.md").read_text(encoding="utf-8")
    assert "## Содержание" not in text
    assert "СТАТЬЯ" in text

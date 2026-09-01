#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/common.py — полное покрытие: P0-канон (главы, .env, промпты,
текст, NER-поиск), стрим LLM (моки requests), determine_model,
логирование, файловые утилиты, детектор зацикливания."""
import json
import os
import sys
from pathlib import Path

import pytest
import requests as real_requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import common as C  # noqa: E402
from conftest import SilentLog  # noqa: E402


# ══════════════════════════════════════════════════════════════════════
# parse_chapter_id / format_ranges — канон глав
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("name,expected", [
    ("00000_1_第1章", 1), ("0000_10_第10章", 10), ("000_100_x", 100),
    ("0_10000_x", 10000), ("00000_1", 1), ("_7_x", 7),
    ("000001_title", 1), ("12_title", 12), ("001_x", 1), ("1_x", 1),
    ("000001", 1), ("7", 7),
    ("chapter 5", None), ("", None), ("第1章", None),
])
def test_parse_chapter_id(name, expected):
    assert C.parse_chapter_id(name) == expected


def test_parse_chapter_id_consistency():
    # все форматы write_section (zeros = 6-len)
    for c in (1, 9, 10, 99, 100, 999, 1000, 9999, 10000, 123456):
        zeros = "0" * max(0, 6 - len(str(c)))
        assert C.parse_chapter_id(f"{zeros}_{c}_第{c}章") == c


def test_format_ranges():
    assert C.format_ranges([1, 2, 3, 5, 6, 7, 8]) == "1-3, 5-8"
    assert C.format_ranges([4]) == "4"
    assert C.format_ranges([]) == "—"


# ══════════════════════════════════════════════════════════════════════
# текст / CJK / n-граммы
# ══════════════════════════════════════════════════════════════════════
def test_get_ngrams():
    assert C.get_ngrams("abcab", 3) == {"abc", "bca", "cab"}
    assert C.get_ngrams("ab", 3) == {"ab"}
    assert C.get_ngrams("", 3) == set()


def test_get_ngrams_strip():
    assert C.get_ngrams("  ABC ", 3) == {"abc"}


def test_normalize_for_search():
    assert C.normalize_for_search("Ци Чжаопин") == C.normalize_for_search("ци  чжаопин!")
    assert C.normalize_for_search("Линь Шуя") != C.normalize_for_search("Линь Шуи")


def test_split_text_smart_limits():
    text = ("АБВ. " * 200 + "\n") * 20  # ~100k символов
    chunks = C.split_text_smart(text, target_chars=7000, multiplier=1.3)
    hard = int(7000 * 1.3)
    assert all(len(c) <= hard + 5 for c in chunks)
    assert sum(len(c) for c in chunks) >= len(text)  # ничего не потеряно


def test_split_text_smart_long_line():
    one_line = ("Предложение номер раз. " * 500) + "\n"  # длиннее hard limit
    hard = int(1000 * 1.2)
    chunks = C.split_text_smart(one_line, target_chars=1000, multiplier=1.2)
    assert len(chunks) > 1
    # грань: на каждое предложение добавляется « \n», но current_len считает
    # только длину предложения — реальный чанк чуть больше hard limit
    assert all(len(c) <= hard + 150 for c in chunks)
    assert sum(len(c) for c in chunks) >= len(one_line)


def test_split_text_smart_small():
    assert C.split_text_smart("короткий текст", target_chars=7000) == ["короткий текст"]


def test_split_text_smart_with_logger_and_flush():
    # несколько строк, каждая меньше hard, но сумма превышает —
    # покрывает flush-ветку по накоплению
    text = "\n".join(f"строка текста номер {i}." for i in range(50))
    chunks = C.split_text_smart(text, target_chars=100, multiplier=1.5,
                                logger=SilentLog())
    assert len(chunks) > 1
    assert all(len(c) <= 160 for c in chunks)


def test_is_cjk():
    assert C.is_cjk("第") and C.is_cjk("あ") and C.is_cjk("한")
    assert not C.is_cjk("Я")
    assert C.is_cjk_string("第1章") and not C.is_cjk_string("Глава 1")


def test_is_cjk_ranges():
    assert C.is_cjk("𠀀")      # расширение B
    assert C.is_cjk("㐀")      # расширение A
    assert C.is_cjk("豈")      # совместимость
    assert not C.is_cjk("")
    assert not C.is_cjk("A")


def test_is_cjk_string_edge():
    assert not C.is_cjk_string("")
    assert not C.is_cjk_string("а第")   # ровно 50% — не больше половины
    assert C.is_cjk_string("第一")


def test_build_smart_regex():
    assert C.build_smart_regex("").search("что угодно") is None
    rx = C.build_smart_regex("Линь Шуй")
    assert rx.search("это Линь   Шуй идёт")
    assert not rx.search("Линь Шуи")


def test_find_exact_match():
    assert C.find_exact_match("Тут есть Линь  Шуй.", "линь шуй")
    assert not C.find_exact_match("текст", "")
    assert not C.find_exact_match("", "термин")
    assert not C.find_exact_match("Линь Шуи", "Линь Шуй")


def test_trim_rule_left():
    """Правила замен: паддинг у «->» убирается, значимые пробелы
    у якорей («^  », «  $») сохраняются."""
    assert C.trim_rule_left("Хунг ") == "Хунг"
    assert C.trim_rule_left(" Хунг") == "Хунг"
    assert C.trim_rule_left("^  ") == "^  "          # отступ строки
    assert C.trim_rule_left("  $ ") == "  $"          # хвостовые пробелы
    assert C.trim_rule_left("^ ") == "^"              # 1 пробел — паддинг
    assert C.trim_rule_left("^ +") == "^ +"            # обычный regex
    assert C.trim_rule_left("") == ""
    assert C.trim_rule_left("   ") == ""


def test_trim_rule_right():
    """Правая часть: паддинг убирается, но « -> » (только пробелы) —
    значимая замена (сжатие пробелов)."""
    assert C.trim_rule_right(" Хун") == "Хун"
    assert C.trim_rule_right("Хун ") == "Хун"
    assert C.trim_rule_right(" ") == " "
    assert C.trim_rule_right("  ") == "  "
    assert C.trim_rule_right("") == ""


def test_strip_rule_flags():
    """Флаги « |i»/« |r» в конце строки правила; разделитель — ровно
    один пробел, лишние пробелы остаются значимыми в правиле."""
    assert C.strip_rule_flags("Хунг -> Хун") == ("Хунг -> Хун", "")
    assert C.strip_rule_flags("Хунг -> Хун |i") == ("Хунг -> Хун", "i")
    assert C.strip_rule_flags("Хунг -> Хун |IR") == ("Хунг -> Хун", "ir")
    assert C.strip_rule_flags("Хунг -> Хун|i") == ("Хунг -> Хун|i", "")
    # два пробела перед «|»: один — разделитель флагов, второй —
    # значимая правая часть («сжать пробелы», а не удаление)
    assert C.strip_rule_flags("\\s+ ->  |r") == ("\\s+ -> ", "r")
    assert C.strip_rule_flags("Хунг -> Хун  |i") == ("Хунг -> Хун ", "i")


def test_loop_detection_patterns():
    ok = "Обычный текст перевода без повторов, просто длинное предложение."
    assert not any(r.search(ok) for r in C._LOOP_RES)
    assert C._LOOP_RES[0].search("аб" * 80)                      # 1–3 симв.
    assert C._LOOP_RES[2].search(("фраза из четырёх слов, ") * 30)  # 16+
    assert C._LOOP_RES[2].search(
        ("очень длинное предложение из шестнадцати символов и более, ") * 12)


# ══════════════════════════════════════════════════════════════════════
# .env / конфигурация серверов
# ══════════════════════════════════════════════════════════════════════
def test_parse_dotenv(tmp_path):
    p = tmp_path / ".env"
    p.write_text('# comment\nLOCAL_HOST="http://h:9989"\nexport X=1\nEMPTY=\n', encoding="utf-8")
    d = C.parse_dotenv(str(p))
    assert d["LOCAL_HOST"] == "http://h:9989"
    assert d["X"] == "1"
    assert C.parse_dotenv(str(tmp_path / "nope.env")) == {}


def test_parse_dotenv_full(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "# комментарий\n"
        "\n"
        "export EXPORTED=значение\n"
        "QUOTED=\"в кавычках\"\n"
        "SINGLE='одинарные'\n"
        "без_равенства\n"
        "EMPTY=\n",
        encoding="utf-8")
    data = C.parse_dotenv(str(p))
    assert data["EXPORTED"] == "значение"
    assert data["QUOTED"] == "в кавычках"
    assert data["SINGLE"] == "одинарные"
    assert "без_равенства" not in data
    assert data["EMPTY"] == ""
    assert C.parse_dotenv(str(tmp_path / "нет.env")) == {}
    assert C.parse_dotenv(None) == {}


def test_find_env_file_upward(tmp_path):
    """Из глубины projects/ находится системный корневой .env."""
    (tmp_path / "projects").mkdir()
    (tmp_path / ".env").write_text("A=1", encoding="utf-8")
    deep = tmp_path / "projects" / "ACTIVE" / "book"
    deep.mkdir(parents=True)
    found = C.find_env_file(start_dir=str(deep))
    assert found == str(tmp_path / ".env")  # системный корневой


def test_find_env_file_project_env_wins(tmp_path):
    """Из папки книги собственный .env проекта — приоритетнее системного."""
    book = tmp_path / "projects" / "ACTIVE" / "book"
    book.mkdir(parents=True)
    (book / ".env").write_text("P=1", encoding="utf-8")
    (tmp_path / ".env").write_text("S=1", encoding="utf-8")
    found = C.find_env_file(start_dir=str(book))
    assert found == str(book / ".env")


def test_find_env_file_explicit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # изоляция от реального projects/.env
    p = tmp_path / ".env"
    p.write_text("A=1", encoding="utf-8")
    assert C.find_env_file(explicit=str(p)) == os.path.abspath(str(p))
    # поиск от start_dir находит созданный .env
    assert C.find_env_file(start_dir=str(tmp_path)) == os.path.abspath(str(p))
    # явный несуществующий путь игнорируется, поиск идёт по базам
    assert C.find_env_file(explicit=str(tmp_path / "нет.env"),
                           start_dir=str(tmp_path)) == os.path.abspath(str(p))


def test_find_env_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    real_isfile = os.path.isfile

    def fake_isfile(path):
        if str(path).endswith(".env"):
            return False
        return real_isfile(path)

    monkeypatch.setattr(C.os.path, "isfile", fake_isfile)
    assert C.find_env_file(start_dir=str(tmp_path)) is None


def test_get_server_config(monkeypatch):
    for k in ("HOST", "API_KEY", "MODEL", "NER_HOST", "NER_MODEL"):
        monkeypatch.delenv(k, raising=False)
    env = {"HOST": "http://h", "MODEL": "m"}
    cfg = C.get_server_config(env)
    assert cfg["host"] == "http://h" and cfg["model"] == "m" and cfg["api_key"] == ""
    # legacy-ключи профилей игнорируются
    assert C.get_server_config({"LOCAL_HOST": "x"})["host"] == ""


def test_get_server_config_environment_wins(monkeypatch):
    """Канон §7: os.environ приоритетнее .env (Docker env_file → окружение)."""
    for k in ("HOST", "API_KEY", "MODEL", "NER_HOST", "NER_MODEL"):
        monkeypatch.delenv(k, raising=False)
    cfg = C.get_server_config({"HOST": "http://file", "API_KEY": "fk",
                               "MODEL": "fm"})
    assert cfg == {"host": "http://file", "api_key": "fk", "model": "fm"}
    monkeypatch.setenv("HOST", "http://env")
    monkeypatch.setenv("MODEL", "env-m")
    cfg = C.get_server_config({"HOST": "http://file", "API_KEY": "fk",
                               "MODEL": "fm"})
    assert cfg["host"] == "http://env" and cfg["model"] == "env-m"
    # ключ файла для чужого хоста не подставляется при env-HOST'е
    assert cfg["api_key"] == "fk"
    # стадийный ключ окружения переопределяет и общий env, и файл
    monkeypatch.setenv("NER_HOST", "http://ner")
    assert C.get_server_config({"HOST": "http://file"}, "ner")["host"] == "http://ner"
    # пустые значения окружения = отсутствуют (файл остаётся)
    monkeypatch.setenv("HOST", "  ")
    assert C.get_server_config({"HOST": "http://file"})["host"] == "http://file"


def test_get_stage_model_environment_wins(monkeypatch):
    for k in ("MODEL", "NER_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert C.get_stage_model({"MODEL": "общая"}, "ner") == "общая"
    monkeypatch.setenv("MODEL", "env-общая")
    assert C.get_stage_model({"MODEL": "общая"}, "ner") == "env-общая"
    monkeypatch.setenv("NER_MODEL", "env-стадийная")
    assert C.get_stage_model({"MODEL": "общая"}, "ner") == "env-стадийная"


def test_get_server_config_remote(monkeypatch):
    for k in ("HOST", "API_KEY", "MODEL"):
        monkeypatch.delenv(k, raising=False)
    env = {"HOST": "https://r", "API_KEY": "k", "MODEL": "m"}
    cfg = C.get_server_config(env)
    assert cfg == {"host": "https://r", "api_key": "k", "model": "m"}
    assert C.get_server_config({}) == {"host": "", "api_key": "", "model": ""}


def test_get_stage_model_stage_wins():
    # схема «один скрипт — одна модель»: NER_MODEL → общая MODEL
    env = {"MODEL": "общая", "NER_MODEL": "стадийная"}
    assert C.get_stage_model(env, "ner") == "стадийная"


def test_get_stage_model_fallback_to_shared():
    env = {"MODEL": "общая"}
    assert C.get_stage_model(env, "ner") == "общая"


def test_get_stage_model_empty():
    assert C.get_stage_model({}, "ner") == ""

def test_get_stage_model_case():
    env = {"MODEL": "r", "wiki_model": "w", "WIKI_MODEL": "W"}
    assert C.get_stage_model(env, "wiki") == "W"
    assert C.get_stage_model(env, "translate_check_llm") == "r"


def test_get_stage_model_no_stage_is_shared():
    env = {"MODEL": "общая", "NER_MODEL": "нер"}
    assert C.get_stage_model(env, "") == "общая"


def test_get_server_config_stage_wins():
    """Стадия непуста: <СТАДИЯ>_HOST/API_KEY/MODEL → общие ключи."""
    env = {"HOST": "http://общий", "API_KEY": "общий-ключ",
           "MODEL": "общая", "NER_HOST": "http://нер",
           "NER_API_KEY": "нер-ключ", "NER_MODEL": "нер-модель"}
    cfg = C.get_server_config(env, "ner")
    assert cfg == {"host": "http://нер", "api_key": "нер-ключ",
                   "model": "нер-модель"}


def test_get_server_config_stage_fallback():
    """Стадийных ключей нет — общие HOST/API_KEY/MODEL."""
    env = {"HOST": "http://общий", "API_KEY": "ключ", "MODEL": "модель"}
    cfg = C.get_server_config(env, "wiki")
    assert cfg == {"host": "http://общий", "api_key": "ключ",
                   "model": "модель"}


def test_get_server_config_stage_empty_is_shared():
    """Пустая стадия — только общие ключи."""
    env = {"HOST": "http://общий", "NER_HOST": "http://нер"}
    assert C.get_server_config(env)["host"] == "http://общий"
    assert C.get_server_config(env, "")["host"] == "http://общий"


def test_print_env_help(capsys):
    C.print_env_help()
    out = capsys.readouterr().out
    assert "HOST=" in out and "MODEL=" in out and ".env" in out
    assert "LOCAL_HOST=" not in out and "REMOTE_HOST=" not in out


# ══════════════════════════════════════════════════════════════════════
# промпты
# ══════════════════════════════════════════════════════════════════════
def test_load_prompt(tmp_path):
    p = tmp_path / "prompt.txt"
    p.write_text("  промпт с пробелами  \n", encoding="utf-8")
    assert C.load_prompt(str(p)) == "промпт с пробелами"
    assert C.load_prompt(str(tmp_path / "нет.txt")) is None
    assert C.load_prompt(None) is None
    empty = tmp_path / "пусто.txt"
    empty.write_text("   \n", encoding="utf-8")
    assert C.load_prompt(str(empty)) is None


def test_load_prompt_oserror(tmp_path):
    p = tmp_path / "закрытый.txt"
    p.write_text("секрет", encoding="utf-8")
    os.chmod(p, 0)
    try:
        assert C.load_prompt(str(p), SilentLog()) is None
    finally:
        os.chmod(p, 0o644)


def test_get_tagged_prompt():
    content = "pre\n<translate>\nTR\n</translate>\n<polish>\nPL\n</polish>"
    assert C.get_tagged_prompt(content, "translate") == "TR"
    assert C.get_tagged_prompt(content, "polish") == "PL"
    assert C.get_tagged_prompt(content, "redact") is None


def test_get_tagged_prompt_edge():
    assert C.get_tagged_prompt("", "translate") is None
    assert C.get_tagged_prompt("без тегов", "translate") is None
    assert C.get_tagged_prompt("<t>\nмногострочно\n</t>", "t") == "многострочно"


# ══════════════════════════════════════════════════════════════════════
# файловые утилиты
# ══════════════════════════════════════════════════════════════════════
def test_atomic_write(tmp_path):
    target = tmp_path / "вложенная" / "папка" / "файл.txt"
    C.atomic_write(str(target), "содержимое")
    assert target.read_text(encoding="utf-8") == "содержимое"
    C.atomic_write(str(target), "замена")
    assert target.read_text(encoding="utf-8") == "замена"
    assert not list(target.parent.glob("*.tmp"))


def test_atomic_write_failure(tmp_path, monkeypatch):
    target = tmp_path / "файл.txt"

    def boom(*a, **k):
        raise OSError("диск отвалился")

    monkeypatch.setattr(C.os, "replace", boom)
    with pytest.raises(OSError):
        C.atomic_write(str(target), "данные")
    assert not target.exists()
    assert not list(tmp_path.glob("*.tmp"))  # временный файл убран


def test_read_text_safe_cp1251(tmp_path):
    p = tmp_path / "win.txt"
    p.write_bytes("Привет, мир".encode("cp1251"))
    assert C.read_text_safe(str(p)) == "Привет, мир"
    u = tmp_path / "utf.txt"
    u.write_text("Привет", encoding="utf-8")
    assert C.read_text_safe(str(u)) == "Привет"


def test_read_text_safe_gb18030(tmp_path):
    """B7 (AUDIT): китайские GBK/GB18030-исходники не теряются."""
    p = tmp_path / "zh.txt"
    p.write_bytes("第一章 测试".encode("gb18030"))
    assert C.read_text_safe(str(p)) == "第一章 测试"


# ══════════════════════════════════════════════════════════════════════
# прогресс для web (emit_progress / web_progress_enabled)
# ══════════════════════════════════════════════════════════════════════
def test_web_progress_enabled_by_env(monkeypatch):
    monkeypatch.delenv("WEB_PROGRESS", raising=False)
    assert C.web_progress_enabled() is False
    monkeypatch.setenv("WEB_PROGRESS", "1")
    assert C.web_progress_enabled() is True
    monkeypatch.setenv("WEB_PROGRESS", "0")
    assert C.web_progress_enabled() is False


def test_emit_progress_noop_without_env(capsys):
    """CLI-режим (без флага) — stdout пуст, tqdm как раньше."""
    C.emit_progress(3, 10, "Перевод")
    out = capsys.readouterr().out
    assert out == ""


def test_emit_progress_json(monkeypatch, capsys):
    monkeypatch.setenv("WEB_PROGRESS", "1")
    C.emit_progress(3, 10, "Перевод")
    out = capsys.readouterr().out
    assert out.startswith(C.PROGRESS_PREFIX)
    ev = json.loads(out[len(C.PROGRESS_PREFIX):].strip())
    assert ev == {"type": "progress", "label": "Перевод",
                  "done": 3, "total": 10}


def test_emit_progress_total_none(monkeypatch, capsys):
    """total=None → "total": null (неопределённый бар)."""
    monkeypatch.setenv("WEB_PROGRESS", "1")
    C.emit_progress(5, None, "")
    out = capsys.readouterr().out
    ev = json.loads(out[len(C.PROGRESS_PREFIX):].strip())
    assert ev["total"] is None
    assert ev["done"] == 5


def test_emit_progress_flush_and_unicode(monkeypatch, capsys):
    """Кириллица в label без &#39;\u0026#39;ASCII-искажений."""
    monkeypatch.setenv("WEB_PROGRESS", "1")
    C.emit_progress(1, 2, "Проверка глоссария")
    out = capsys.readouterr().out
    assert "Проверка глоссария" in out
    assert r"\u043f" not in out  # ensure_ascii=False


# ══════════════════════════════════════════════════════════════════════
# главы: карта / поиск файла
# ══════════════════════════════════════════════════════════════════════
def test_build_chapter_map(tmp_path):
    (tmp_path / "00000_1_第1章").mkdir()
    (tmp_path / "0000_10_第10章").mkdir()
    (tmp_path / "junk").mkdir()
    m = C.build_chapter_map(str(tmp_path))
    assert set(m) == {1, 10}


def test_build_chapter_map_duplicates_and_files(tmp_path):
    (tmp_path / "00000_1_a").mkdir()
    (tmp_path / "1_b").mkdir()                 # дубль номера 1
    (tmp_path / "5_x").write_text("", encoding="utf-8")  # файл, не папка
    m = C.build_chapter_map(str(tmp_path))
    assert len(m[1]) == 2 and set(m) == {1}
    assert C.build_chapter_map(str(tmp_path / "нет")) == {}


def test_compile_chapter_texts(tmp_path):
    root = tmp_path / "chapters"
    d1 = root / "00000_1_a"
    d2 = root / "00000_2_b"
    d1.mkdir(parents=True)
    d2.mkdir()
    (d1 / "chapter.txt").write_text("раз\n", encoding="utf-8")
    (d2 / "chapter.txt").write_text("два\n", encoding="utf-8")
    out = tmp_path / "all.txt"
    info = C.compile_chapter_texts(str(root), str(out), want="chapter")
    assert info["written"] == 2 and info["missing"] == []
    assert out.read_text(encoding="utf-8") == "раз\n\nдва\n"
    info2 = C.compile_chapter_texts(str(root), str(tmp_path / "one.txt"),
                                    want="chapter", start=2, end=2)
    assert info2["written"] == 1
    assert (tmp_path / "one.txt").read_text(encoding="utf-8") == "два\n"


def test_compile_chapter_text_in_memory(tmp_path):
    """compile_chapter_text — склейка в память без записи файла."""
    root = tmp_path / "chapters"
    d1 = root / "00000_1_a"
    d2 = root / "00000_2_b"
    d1.mkdir(parents=True)
    d2.mkdir()
    (d1 / "chapter.txt").write_text("раз\n", encoding="utf-8")
    (d2 / "chapter.txt").write_text("два\n", encoding="utf-8")
    text, info = C.compile_chapter_text(str(root), want="chapter")
    assert info["written"] == 2 and info["missing"] == []
    assert text == "раз\n\nдва\n"
    # файл не создаётся
    assert not list(tmp_path.glob("*.txt"))
    # диапазон глав
    text2, info2 = C.compile_chapter_text(str(root), want="chapter",
                                          start=2, end=2)
    assert info2["written"] == 1 and text2 == "два\n"


def test_read_chapter_titles(tmp_path):
    """read_chapter_titles: первая непустая строка файла главы."""
    root = tmp_path / "chapters"
    d1 = root / "00000_1_a"
    d2 = root / "00000_2_b"
    d1.mkdir(parents=True)
    d2.mkdir()
    (d1 / "polished.txt").write_text("\n\nГлава 1 Начало\n\nТекст\n",
                                      encoding="utf-8")
    (d2 / "polished.txt").write_text("Глава 2 Продолжение\n\nТекст\n",
                                      encoding="utf-8")
    titles = C.read_chapter_titles(str(root), want="polished")
    assert titles == {1: "Глава 1 Начало", 2: "Глава 2 Продолжение"}
    # файлов нужного типа нет → пусто (strict_types без fallback)
    assert C.read_chapter_titles(str(root), want="translated") == {}


def test_first_nonempty_line_chunk_cut(tmp_path):
    """Разрез на границе 4096 байт не портит первую строку (utf-8).

    Многобайтовый символ «Ж» переживает границу чанка — декодируется
    ТОЛЬКО первая строка, а не весь буфер (иначе utf-8 падает и
    фолбек cp1251 даёт «Р“Р»Р°РІР°»-кракозябры).
    """
    head = "Глава 1. Проиграл всё\n\n".encode("utf-8")
    filler = "Текст главы. ".encode("utf-8") * 120
    buf = head + filler
    assert len(buf) < 4095
    buf += b"x" * (4095 - len(buf))
    buf += "Ж".encode("utf-8") + "\nхвост".encode("utf-8")
    p = tmp_path / "polished.txt"
    p.write_bytes(buf)
    assert C._first_nonempty_line(str(p)) == "Глава 1. Проиграл всё"
    # пустые строки перед заголовком — тоже корректно
    p2 = tmp_path / "p2.txt"
    p2.write_bytes(b"\n\n" + head + b"\n")
    assert C._first_nonempty_line(str(p2)) == "Глава 1. Проиграл всё"
    # реально cp1251 — фолбек работает
    p3 = tmp_path / "p3.txt"
    p3.write_bytes("Глава 2. Побить его\n".encode("cp1251"))
    assert C._first_nonempty_line(str(p3)) == "Глава 2. Побить его"


def test_write_chapter_titles(tmp_path):
    """write_chapter_titles: замена первой строки, остальное сохраняется."""
    root = tmp_path / "chapters"
    d1 = root / "00000_1_a"
    d1.mkdir(parents=True)
    (d1 / "polished.txt").write_text("\nГлава 1 Старое\n\nТекст\n",
                                      encoding="utf-8")
    res = C.write_chapter_titles(str(root), "polished", {1: "Глава 1 Новое"})
    assert res["updated"] == [1] and res["missing"] == []
    text = (d1 / "polished.txt").read_text(encoding="utf-8")
    assert text == "\nГлава 1 Новое\n\nТекст\n"
    # отсутствующая глава — в missing, файл не трогается
    res2 = C.write_chapter_titles(str(root), "polished", {2: "Глава 2"})
    assert res2["missing"] == [2]
    # пустой заголовок — missing
    res3 = C.write_chapter_titles(str(root), "polished", {1: "  "})
    assert res3["missing"] == [1]


def test_find_chapter_file_priority(tmp_path):
    d = tmp_path / "00000_1_x"
    d.mkdir()
    (d / "chapter.txt").write_text("zh", encoding="utf-8")
    (d / "polished.txt").write_text("ru", encoding="utf-8")
    (d / "translated.txt").write_text("draft", encoding="utf-8")
    p, w = C.find_chapter_file(str(d), 1, "polished")
    assert p is not None
    assert Path(p).name == "polished.txt" and not w
    p, _ = C.find_chapter_file(str(d), 1, "chapter")
    assert p is not None
    assert Path(p).name == "chapter.txt"
    # fallback: единственный безопасный
    d2 = tmp_path / "00000_2_x"
    d2.mkdir()
    (d2 / "weird_name.txt").write_text("ru", encoding="utf-8")
    (d2 / "translated.txt").write_text("draft", encoding="utf-8")
    p, _ = C.find_chapter_file(str(d2), 2, "polished")
    assert p is not None
    assert Path(p).name == "weird_name.txt"


def test_find_chapter_file_strict_dup(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    # два файла под один паттерн (^polished\.txt$, IGNORECASE)
    (d / "polished.txt").write_text("б", encoding="utf-8")
    (d / "Polished.txt").write_text("а", encoding="utf-8")
    p, warns = C.find_chapter_file(str(d), 1, "polished", strict=True)
    assert p is None and warns[0].startswith("[FATAL]")
    # без strict — первый по алфавиту + предупреждение
    p, warns = C.find_chapter_file(str(d), 1, "polished")
    assert p is not None
    assert Path(p).name == "Polished.txt"
    assert warns and "КОНФЛИКТ" in warns[0]


def test_find_chapter_file_conflict_with_logger(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    (d / "polished.txt").write_text("а", encoding="utf-8")
    (d / "Polished.txt").write_text("б", encoding="utf-8")
    log = SilentLog()
    p, warns = C.find_chapter_file(str(d), 1, "polished", logger=log)
    assert p is not None and warns and "КОНФЛИКТ" in warns[0]


def test_find_chapter_file_strict_types_no_fallback(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    (d / "chapter.txt").write_text("zh", encoding="utf-8")
    p, warns = C.find_chapter_file(str(d), 1, "polished", strict_types=True)
    assert p is None and warns and "fallback" in warns[0]


def test_find_chapter_file_blacklist(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    (d / "raw.txt").write_text("сырьё", encoding="utf-8")
    (d / "итоговый.txt").write_text("текст", encoding="utf-8")
    p, _ = C.find_chapter_file(str(d), 1, "polished")
    assert p is not None
    assert Path(p).name == "итоговый.txt"   # raw в блэклисте


def test_find_chapter_file_ambiguous_safe(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    (d / "один.txt").write_text("1", encoding="utf-8")
    (d / "два.txt").write_text("2", encoding="utf-8")
    p, warns = C.find_chapter_file(str(d), 1, "polished")
    assert p is None and warns


def test_find_chapter_file_no_dir(tmp_path):
    p, warns = C.find_chapter_file(str(tmp_path / "нет"), 1)
    assert p is None and warns == []


def test_find_chapter_file_chapter_want(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    (d / "chapter5.txt").write_text("x", encoding="utf-8")
    p, _ = C.find_chapter_file(str(d), 5, "chapter")
    assert p is not None
    assert Path(p).name == "chapter5.txt"


# ══════════════════════════════════════════════════════════════════════
# NER: загрузка и поиск
# ══════════════════════════════════════════════════════════════════════
_NER_SAMPLE = [
    {"term": "陈阳", "aliases": ["陳陽"], "translation": "Чэнь Ян",
     "type": "Person (male)"},
    {"term": "Linh Thuy", "aliases": [], "translation": "Линь Шуй",
     "type": "Person (female)"},
    {"term": "", "translation": "пустой термин пропускается", "type": "x"},
]


def _write_ner(tmp_path):
    p = tmp_path / "ner.json"
    p.write_text(json.dumps(_NER_SAMPLE, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_load_and_find_ner(tmp_path):
    ner = [
        {"term": "陈阳", "aliases": ["陳陽"], "translation": "Чэнь Ян", "type": "Person (male)"},
        {"term": "Linh Thuy", "translation": "Линь Шуй", "type": "Person (female)"},
    ]
    p = tmp_path / "ner.json"
    p.write_text(json.dumps(ner, ensure_ascii=False), encoding="utf-8")
    data, automaton = C.load_ner_data(str(p), 3, SilentLog())
    assert len(data) == 2
    # CJK-термин найден через alias (точное совпадение)
    s, cnt = C.find_relevant_ner("陳陽 вошёл в зал", data, 0.7, 3,
                                 "term,translation,type", automaton=automaton)
    assert cnt == 1 and "Чэнь Ян" in s
    # не-CJK термин найден в ОРИГИНАЛЬНОМ написании (n-граммы)
    s, cnt = C.find_relevant_ner("Linh Thuy ушла", data, 0.7, 3,
                                 "term,translation,type", automaton=automaton)
    assert cnt == 1
    # translation НЕ является поисковым ключом (историческая семантика:
    # ner-блок собирается по оригинальному тексту)
    s, cnt = C.find_relevant_ner("Линь Шуй ушла", data, 0.7, 3,
                                 "term,translation,type", automaton=automaton)
    assert cnt == 0
    s, cnt = C.find_relevant_ner("ничего нет", data, 0.7, 3,
                                 "term,translation,type", automaton=automaton)
    assert cnt == 0 and s == "[]"


def test_load_ner_data_missing_and_broken(tmp_path):
    log = SilentLog()
    data, automaton = C.load_ner_data(str(tmp_path / "нет.json"), 3, log)
    assert data == [] and automaton is None
    bad = tmp_path / "битый.json"
    bad.write_text("{не json", encoding="utf-8")
    data, automaton = C.load_ner_data(str(bad), 3, log)
    assert data == [] and automaton is None


def test_load_ner_data_skips_empty_term(tmp_path):
    data, _ = C.load_ner_data(_write_ner(tmp_path), 3, SilentLog())
    assert [d["term"] for d in data] == ["陈阳", "Linh Thuy"]


def test_load_ner_data_regex_fallback(tmp_path, monkeypatch):
    """Без pyahocorasick — кортеж-фолбэк, поиск продолжает работать."""
    monkeypatch.setitem(sys.modules, "ahocorasick", None)
    data, automaton = C.load_ner_data(_write_ner(tmp_path), 3, SilentLog())
    assert isinstance(automaton, tuple) and automaton[0] == "regex_fallback"
    s, cnt = C.find_relevant_ner("陳陽 здесь", data, 0.7, 3,
                                 "term,translation,type", automaton=automaton)
    assert cnt == 1 and "Чэнь Ян" in s


def test_regex_fallback_prefix_overlap(tmp_path, monkeypatch):
    """Фолбэк находит ВСЕ варианты, включая короткий термин-префикс
    внутри длинного (регэксп-чередование их теряет, Aho-Corasick — нет)."""
    monkeypatch.setitem(sys.modules, "ahocorasick", None)
    ner = [
        {"term": "系统", "translation": "Система", "type": "Object"},
        {"term": "系统管理员", "translation": "Администратор", "type": "Person"},
    ]
    p = tmp_path / "ner.json"
    p.write_text(json.dumps(ner, ensure_ascii=False), encoding="utf-8")
    data, automaton = C.load_ner_data(str(p), 3, SilentLog())
    assert isinstance(automaton, tuple) and automaton[0] == "regex_fallback"
    s, cnt = C.find_relevant_ner("系统管理员", data, 0.7, 3,
                                 "term,translation", automaton=automaton)
    assert cnt == 2
    assert {e["term"] for e in json.loads(s)} == {"系统", "系统管理员"}
    # то же без фолбэка (Aho-Corasick) — результат идентичен
    data2, ac = C.load_ner_data(str(p), 3, SilentLog())
    s2, cnt2 = C.find_relevant_ner("系统管理员", data2, 0.7, 3,
                                   "term,translation", automaton=ac)
    assert cnt2 == 2 and {e["term"] for e in json.loads(s2)} == {"系统", "系统管理员"}


def test_find_relevant_ner_aliases_flag(tmp_path):
    data, automaton = C.load_ner_data(_write_ner(tmp_path), 3, SilentLog())
    # include_aliases=True → aliases добавляются, даже если не в полях
    s, _ = C.find_relevant_ner("陳陽 здесь", data, 0.7, 3, "term,translation",
                               automaton=automaton, include_aliases=True)
    assert "aliases" in json.loads(s)[0]
    # include_aliases=False → не добавляются
    s, _ = C.find_relevant_ner("陳陽 здесь", data, 0.7, 3, "term,translation",
                               automaton=automaton, include_aliases=False)
    assert "aliases" not in json.loads(s)[0]
    # поле aliases запрошено явно → есть всегда
    s, _ = C.find_relevant_ner("陳陽 здесь", data, 0.7, 3,
                               "term,aliases,translation",
                               automaton=automaton, include_aliases=False)
    assert json.loads(s)[0]["aliases"] == ["陳陽"]


def test_find_relevant_ner_dedup_and_fuzzy(tmp_path):
    # два варианта одного термина → дедуп по term в выдаче
    tn = C.normalize_for_search("Линь Шуй")
    data = [
        {"term": "Линь Шуй", "translation": "Линь Шуй", "type": "Person",
         "_term_norm": tn, "_ngrams": C.get_ngrams(tn), "_len": 8},
        {"term": "Линь Шуй", "translation": "Линь Шуй (дубль)", "type": "Person",
         "_term_norm": tn, "_ngrams": C.get_ngrams(tn), "_len": 8},
    ]
    s, cnt = C.find_relevant_ner("Линь Шуй пришла", data, 0.7, 3,
                                 "term,translation", automaton=None)
    assert cnt == 1  # дубль схлопнут


def test_find_relevant_ner_fuzzy_match():
    # термин не входит подстрокой (последний символ искажён), но
    # n-граммы почти совпадают и longest_match ≥ 0.8 длины термина
    term = "abcdefghij"
    tn = C.normalize_for_search(term)
    data = [{"term": term, "translation": "перевод", "type": "Artifact",
             "_term_norm": tn, "_ngrams": C.get_ngrams(tn), "_len": len(term)}]
    text = "zz abcdefghiX yy"
    s, cnt = C.find_relevant_ner(text, data, 0.8, 3, "term", automaton=None)
    assert cnt == 1


def test_find_relevant_ner_empty_inputs(tmp_path):
    data, automaton = C.load_ner_data(_write_ner(tmp_path), 3, SilentLog())
    assert C.find_relevant_ner("", data, 0.7, 3, "term") == ("[]", 0)
    assert C.find_relevant_ner("текст", [], 0.7, 3, "term") == ("[]", 0)


def test_find_relevant_ner_ngram_threshold(tmp_path):
    """Не-CJK термин с опечаткой находит по n-граммам ниже порога."""
    data, automaton = C.load_ner_data(_write_ner(tmp_path), 3, SilentLog())
    # точное нахождение
    _, cnt = C.find_relevant_ner("Linh Thuy ушла", data, 0.7, 3, "term",
                                 automaton=automaton)
    assert cnt == 1
    # слишком высокий порог для зашумлённого текста — не находит
    _, cnt = C.find_relevant_ner("zzzz", data, 0.99, 3, "term",
                                 automaton=automaton)
    assert cnt == 0


# ══════════════════════════════════════════════════════════════════════
# collect_gender_names (имена по полу для polish)
# ══════════════════════════════════════════════════════════════════════
_GENDER_SAMPLE = [
    {"term": "廖停雁", "translation": "Ляо Тинъянь",
     "type": "Person (female)", "count": 5},
    {"term": "苏星宇", "translation": "Су Синюй",
     "type": "Person (male)", "count": 10},
    {"term": "萧炎", "translation": "Сяо Янь",
     "type": "Person (male)", "count": 2},
    {"term": "灵儿", "translation": "Линъэр", "type": "Creature (female)"},
    {"term": "龙爷", "translation": "Лун Е", "type": "Creature (male)"},
    {"term": "苏星宇2", "translation": "Су Синюй",
     "type": "Person (male)", "count": 1},          # дубль перевода
    {"term": "白虎", "translation": "Байху", "type": "Creature (unknown)"},
    {"term": "张三", "translation": "Чжан Сань", "type": "Person (unknown)"},
    {"term": "李四", "type": "Person (male)"},            # нет translation
    {"term": "王五", "translation": "Ван У", "type": "Person"},  # нет пола
    {"term": "赵六", "translation": "Чжао Лю",
     "type": "Title / Person (male)"},                # составной тип
]

_GENDER_TEXT = ("Ляо Тинъянь увидела Су Синюя. Сяо Янь и Линъэр шли рядом. "
                "Лун Е, Чжао Лю, Байху и Чжан Сань остались позади. "
                "Ван У тоже был там.")


def _gender_data(tmp_path):
    p = tmp_path / "ner.json"
    p.write_text(json.dumps(_GENDER_SAMPLE, ensure_ascii=False),
                 encoding="utf-8")
    return str(p)


def test_load_ner_data_precomputes_translation_norm(tmp_path):
    data, _ = C.load_ner_data(_gender_data(tmp_path), 3, SilentLog())
    by_term = {d["term"]: d for d in data}
    assert by_term["廖停雁"]["_translation_norm"] == "ляотинъянь"
    assert by_term["廖停雁"]["_ngrams_translation"]
    # нет translation → пустая норма
    assert by_term["李四"]["_translation_norm"] == ""


def test_collect_gender_names_basic(tmp_path):
    data, _ = C.load_ner_data(_gender_data(tmp_path), 3, SilentLog())
    female, male = C.collect_gender_names(_GENDER_TEXT, data, 0.75, 3)
    # женские: Person (female) + Creature (female), без unknown
    assert female == ["Ляо Тинъянь", "Линъэр"]
    # мужские: count desc, дубль убран, Байху/Чжан Сань (unknown) не попали
    assert male == ["Су Синюй", "Сяо Янь", "Лун Е", "Чжао Лю"]
    assert "Байху" not in female + male
    assert "Ван У" not in female + male


@pytest.mark.parametrize("type_str, expected", [
    ("Person (female)", "female"),
    ("PERSON (FEMALE)", "female"),    # регистр не важен
    ("Creature (female)", "female"),
    ("Person (male)", "male"),
    ("Creature (male)", "male"),
    ("Title / Person (male)", "male"),
    ("Person (unknown)", ""),
    ("Person", ""),
    ("female", ""),                    # без скобок — не пол
    ("male", ""),
    ("", ""),
    (None, ""),
])
def test_gender_of_type(type_str, expected):
    """Пол — только по '(female)'/'(male)': скобки убирают ложное
    вхождение 'male' внутри 'female'."""
    assert C._gender_of_type(type_str) == expected


def test_collect_gender_names_case_and_inflection(tmp_path):
    data, _ = C.load_ner_data(_gender_data(tmp_path), 3, SilentLog())
    # другой регистр + лишние пробелы
    female, male = C.collect_gender_names("ляо  тинъянь пришла", data, 0.75, 3)
    assert female == ["Ляо Тинъянь"] and male == []
    # склонённая форма (нет точного вхождения) — нечёткий матчинг
    female, male = C.collect_gender_names("Сяо Яня ранили", data, 0.75, 3)
    assert male == ["Сяо Янь"]


def test_collect_gender_names_raw_items_without_precompute():
    """Работает и без load_ner_data (нет предвычисленных _translation_norm)."""
    female, male = C.collect_gender_names(
        "Су Синюй победил", [
            {"term": "苏星宇", "translation": "Су Синюй", "type": "Person (male)"},
        ], 0.75, 3)
    assert female == [] and male == ["Су Синюй"]


@pytest.mark.parametrize("text, data", [(_GENDER_TEXT, []), ("", []),
                                         ("", _GENDER_SAMPLE),
                                         (None, _GENDER_SAMPLE)])
def test_collect_gender_names_empty(text, data):
    assert C.collect_gender_names(text, data, 0.75, 3) == ([], [])


# ══════════════════════════════════════════════════════════════════════
# МОКИ ДЛЯ stream_chat_completion
# ══════════════════════════════════════════════════════════════════════
class _FakeResp:
    def __init__(self, lines=(), status=200, headers=None):
        self._lines = list(lines)
        self.status_code = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self):
        yield from self._lines


def _sse(parts, finish=None, done=True, raw_extra=()):
    """Собирает SSE-строки: data: {chunks} [+ data: [DONE]]."""
    lines = list(raw_extra)
    for p in parts:
        ch = {"choices": [{"delta": {"content": p}}]}
        if finish:
            ch["choices"][0]["finish_reason"] = finish
        lines.append(b"data: " + json.dumps(ch, ensure_ascii=False).encode("utf-8"))
    if done:
        lines.append(b"data: [DONE]")
    return lines


def _patch_post(monkeypatch, lines=(), status=200, capture=None):
    def fake_post(url, headers=None, json=None, stream=None, timeout=None):
        if capture is not None:
            capture.update(url=url, headers=headers or {}, json=json or {},
                           timeout=timeout)
        return _FakeResp(lines, status)

    monkeypatch.setattr(C.requests, "post", fake_post)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """time.sleep в ретраях — мгновенно."""
    monkeypatch.setattr(C.time, "sleep", lambda *_a, **_k: None)
    yield


# ══════════════════════════════════════════════════════════════════════
# stream_chat_completion
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("variant", ["done", "finish_stop"])
def test_stream_success(monkeypatch, variant):
    if variant == "done":
        lines = _sse(["Привет, ", "мир!"])
    else:
        lines = _sse(["Готово"], finish="stop", done=False)
    cap = {}
    _patch_post(monkeypatch, lines, capture=cap)
    text, err = C.stream_chat_completion("http://h/v1", "m", [{"role": "user", "content": "x"}])
    assert err == "" and text is not None
    assert ("Привет, мир!" if variant == "done" else "Готово") == text
    # payload и заголовки
    assert cap["url"] == "http://h/v1/chat/completions"
    assert cap["json"]["stream"] is True
    assert cap["json"]["model"] == "m"
    assert cap["json"]["max_tokens"] == 65536
    # reasoning_effort не задан: никаких reasoning-полей — дефолт сервера
    # (структурированное reasoning:{...} не шлём: его не знают строгие серверы)
    assert "reasoning" not in cap["json"]
    assert "reasoning_effort" not in cap["json"]
    assert "Authorization" not in cap["headers"]            # api_key пуст


def test_stream_payload_options(monkeypatch):
    cap = {}
    _patch_post(monkeypatch, _sse(["ок"]), capture=cap)
    C.stream_chat_completion("http://h/v1", "m", [], api_key="СЕКРЕТ",
                             reasoning_effort="low", temperature=0.2,
                             max_tokens=1024)
    assert cap["headers"]["Authorization"] == "Bearer СЕКРЕТ"
    assert cap["json"]["reasoning_effort"] == "low"
    assert "reasoning" not in cap["json"]                   # effort важнее
    assert cap["json"]["temperature"] == 0.2
    assert cap["json"]["max_tokens"] == 1024


def test_stream_reasoning_effort_none(monkeypatch):
    """reasoning_effort="none" — единый OpenAI-совместимый способ
    отключить рассуждения (OpenAI, llama.cpp, OpenRouter/Bothub
    пробрасывают провайдеру)."""
    cap = {}
    _patch_post(monkeypatch, _sse(["ок"]), capture=cap)
    C.stream_chat_completion("http://h/v1", "m", [],
                             reasoning_effort="none")
    assert cap["json"]["reasoning_effort"] == "none"
    assert "reasoning" not in cap["json"]

    # пустой effort = поле не шлём (дефолт сервера)
    cap2 = {}
    _patch_post(monkeypatch, _sse(["ок"]), capture=cap2)
    C.stream_chat_completion("http://h/v1", "m", [], reasoning_effort=None)
    assert "reasoning_effort" not in cap2["json"]
    assert "reasoning" not in cap2["json"]


def test_stream_cut_by_max_tokens(monkeypatch):
    _patch_post(monkeypatch, _sse(["текст"], finish="length", done=False))
    text, err = C.stream_chat_completion("h", "m", [], max_retries=1)
    assert text is None and err == "Cut by max_tokens"


def test_stream_loop_detected(monkeypatch):
    _patch_post(monkeypatch, _sse(["аб" * 80]))  # 160 символов повтора
    text, err = C.stream_chat_completion("h", "m", [], max_retries=1)
    assert text is None and err == "Loop detected"


def test_stream_interrupted(monkeypatch):
    _patch_post(monkeypatch, _sse(["кусок"], done=False))  # нет [DONE]/stop
    text, err = C.stream_chat_completion("h", "m", [], max_retries=1)
    assert text is None and err == "Stream interrupted"


def test_stream_empty_response(monkeypatch):
    _patch_post(monkeypatch, [b"data: [DONE]"])
    text, err = C.stream_chat_completion("h", "m", [], max_retries=1)
    assert text is None and err == "Empty response"


def test_stream_min_len_ratio(monkeypatch):
    _patch_post(monkeypatch, _sse(["коротко"]))
    text, err = C.stream_chat_completion("h", "m", [], max_retries=1,
                                         min_len_ratio=0.5, reference_len=1000)
    assert text is None and err == "Length ratio check failed"


def test_stream_min_len_ratio_passes(monkeypatch):
    # разнообразный текст, чтобы не сработал loop-детект
    varied = "Слово%d отличается. " % 1 + "".join(f"фраза{i} " for i in range(120))
    _patch_post(monkeypatch, _sse([varied]))
    text, err = C.stream_chat_completion("h", "m", [], max_retries=1,
                                         min_len_ratio=0.5,
                                         reference_len=len(varied) + 10)
    assert err == "" and text == varied


def test_stream_http_error(monkeypatch):
    _patch_post(monkeypatch, [], status=500)
    text, err = C.stream_chat_completion("h", "m", [], max_retries=1)
    assert text is None and err == "HTTP 500"


def test_stream_http_401_no_retry(monkeypatch):
    """H3 (AUDIT): 401/403/404 — НЕ ретраим (битый ключ/запрос)."""
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, stream=None, timeout=None):
        calls["n"] += 1
        return _FakeResp([], status=401)

    monkeypatch.setattr(C.requests, "post", fake_post)
    text, err = C.stream_chat_completion("h", "m", [], max_retries=5)
    assert text is None and err == "HTTP 401" and calls["n"] == 1


def test_stream_http_429_retries_with_retry_after(monkeypatch):
    """H3: 429 ретраится; Retry-After уважается (sleep замокан)."""
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, stream=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp([], status=429, headers={"Retry-After": "3"})
        return _FakeResp(_sse(["после паузы"]))

    monkeypatch.setattr(C.requests, "post", fake_post)
    text, err = C.stream_chat_completion("h", "m", [], max_retries=3)
    assert err == "" and text == "после паузы" and calls["n"] == 2


def test_stream_http_500_retries_then_fails(monkeypatch):
    """H3: 5xx ретраится до исчерпания попыток."""
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, stream=None, timeout=None):
        calls["n"] += 1
        return _FakeResp([], status=503)

    monkeypatch.setattr(C.requests, "post", fake_post)
    text, err = C.stream_chat_completion("h", "m", [], max_retries=3)
    assert text is None and err == "HTTP 503" and calls["n"] == 3


def test_stream_garbage_lines_skipped(monkeypatch):
    lines = [
        b"event: ping",                       # не data:
        b"data: {broken json",                # не парсится
        b"",                                   # пустая
        b"data: " + json.dumps({"choices": []}).encode(),  # пустой choices
    ] + _sse(["нормально"])
    _patch_post(monkeypatch, lines)
    text, err = C.stream_chat_completion("h", "m", [], max_retries=1)
    assert err == "" and text == "нормально"


def test_stream_timeouts_and_retry_success(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, stream=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise real_requests.exceptions.ReadTimeout()
        if calls["n"] == 2:
            raise real_requests.exceptions.Timeout()
        if calls["n"] == 3:
            raise real_requests.exceptions.ChunkedEncodingError()
        return _FakeResp(_sse(["успех после ретраев"]))

    monkeypatch.setattr(C.requests, "post", fake_post)
    text, err = C.stream_chat_completion("h", "m", [], max_retries=5)
    assert err == "" and text == "успех после ретраев" and calls["n"] == 4


def test_stream_timeout_exhausts_retries(monkeypatch):
    def fake_post(*a, **k):
        raise real_requests.exceptions.ReadTimeout()

    monkeypatch.setattr(C.requests, "post", fake_post)
    text, err = C.stream_chat_completion("h", "m", [], max_retries=2,
                                         stream_timeout=900)
    assert text is None and err == "Read timeout (900s)"


def test_stream_generic_exception_and_logger(monkeypatch, caplog):
    def fake_post(*a, **k):
        raise RuntimeError("всё сломалось")

    monkeypatch.setattr(C.requests, "post", fake_post)
    log = SilentLog()
    text, err = C.stream_chat_completion("h", "m", [], max_retries=1,
                                         logger=log, label="[ТЕСТ]")
    assert text is None and err == "всё сломалось"


# ══════════════════════════════════════════════════════════════════════
# determine_model
# ══════════════════════════════════════════════════════════════════════
class _ModelsResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return {"data": self._data}


def test_determine_model_from_arg():
    assert C.determine_model("модель-х") == "модель-х"


def test_determine_model_empty_raises():
    # автоопределение убрано — пусто = SystemExit
    with pytest.raises(SystemExit):
        C.determine_model("")


def test_determine_model_none_raises():
    with pytest.raises(SystemExit):
        C.determine_model(None, SilentLog())


# ══════════════════════════════════════════════════════════════════════
# логирование
# ══════════════════════════════════════════════════════════════════════
def test_setup_logging(tmp_path):
    out = tmp_path / "стадия.txt"
    logger, log_name = C.setup_logging(str(out), logger_name="тест.стадия")
    assert log_name == str(tmp_path / "стадия.log")
    assert os.path.isfile(log_name)
    n_handlers = len(logger.handlers)
    assert n_handlers == 2
    # повторный вызов не дублирует хендлеры
    logger2, _ = C.setup_logging(str(out), logger_name="тест.стадия")
    assert len(logger2.handlers) == n_handlers
    logger.info("сообщение")
    for h in logger.handlers:
        h.flush()
    assert "сообщение" in Path(log_name).read_text(encoding="utf-8")


def test_log_argv(tmp_path):
    """R9-D: фактическая команда запуска пишется в лог (shlex.join)."""
    out = tmp_path / "запуск.txt"
    logger, log_name = C.setup_logging(str(out), logger_name="тест.argv")
    C.log_argv(logger, argv=["python3", "cli/ner.py", "--chunk_size 1"
                             .replace(" ", "=")])
    for h in logger.handlers:
        h.flush()
    text = Path(log_name).read_text(encoding="utf-8")
    assert "Запуск: python3 cli/ner.py --chunk_size=1" in text


def test_log_argv_masks_secrets(tmp_path):
    """M2 (AUDIT): значения --api_key/--token в лог НЕ попадают."""
    out = tmp_path / "секрет.txt"
    logger, log_name = C.setup_logging(str(out), logger_name="тест.секрет")
    C.log_argv(logger, argv=[
        "python3", "cli/translate_book.py", "--api_key", "СЕКРЕТ-КЛЮЧ",
        "--model", "модель", "--host", "http://h",
        "--token=ТОКЕН", "--timeout", "300",
    ])
    for h in logger.handlers:
        h.flush()
    text = Path(log_name).read_text(encoding="utf-8")
    assert "СЕКРЕТ-КЛЮЧ" not in text and "ТОКЕН" not in text
    assert "--api_key '••••'" in text  # shlex.join берёт значение в кавычки
    assert "'--token=••••'" in text
    assert "--model 'модель'" in text and "http://h" in text

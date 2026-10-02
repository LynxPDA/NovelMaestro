#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/common.py — полное покрытие: P0-канон (главы, .env, промпты,
текст, NER-поиск), стрим LLM (мок транспорта core/transport.py),
determine_model, логирование, файловые утилиты, детектор зацикливания."""
import json
import os
import re
import sys
from pathlib import Path

import pytest

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
    chunks = C.split_text_smart(text, target_tokens=7000, multiplier=1.3)
    hard = int(7000 * 1.3)
    assert all(C.estimate_tokens(c) <= hard + 60 for c in chunks)
    assert sum(len(c) for c in chunks) >= len(text)  # ничего не потеряно


def test_split_text_smart_long_line():
    one_line = ("Предложение номер раз. " * 500) + "\n"  # длиннее hard limit
    hard = int(1000 * 1.2)
    chunks = C.split_text_smart(one_line, target_tokens=1000, multiplier=1.2)
    assert len(chunks) > 1
    # грань: на каждое предложение добавляется « \n», но current_len считает
    # только оценку предложения — реальный чанк чуть больше hard limit
    # запас +10% начисляется и на границы предложений внутри чанка
    assert all(C.estimate_tokens(c) <= hard + 250 for c in chunks)
    assert sum(len(c) for c in chunks) >= len(one_line)


def test_split_text_smart_small():
    assert C.split_text_smart("короткий текст", target_tokens=7000) == ["короткий текст"]


def test_split_text_smart_with_logger_and_flush():
    # несколько строк, каждая меньше hard, но сумма превышает —
    # покрывает flush-ветку по накоплению
    text = "\n".join(f"строка текста номер {i}." for i in range(50))
    chunks = C.split_text_smart(text, target_tokens=100, multiplier=1.5,
                                logger=SilentLog())
    assert len(chunks) > 1
    assert all(C.estimate_tokens(c) <= int(100 * 1.5) + 30 for c in chunks)


# ══════════════════════════════════════════════════════════════════
# estimate_tokens — язык-осведомлённая оценка (ТОКЕНЫ)
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("text,expected", [
    ("", 0),                       # пустая строка
    ("   \n  ", 2),                # прогон пробельных — 1 токен + запас +10%
    ("Привет, мир! Это тестовый русский текст.", 18),
    ("林凡说。", 4),      # CJK: ~1 токен на иероглиф и знак конца
    ("こんにちは", 6),             # каны — likewise ~1/символ
    ("안녕하세요", 5),              # хангыль — 0.8/символ
    ("สวัสดี", 4),                 # тайский — 0.6/символ
    ("Xin chào", 4),      # вьетнамская латиница с диакритикой — 0.3
    ("مرحبا بالعالم", 8),         # арабский — 0.5/символ
    ("नमस्ते दुनिया", 9),          # деванагари — 0.55
    ("Բարև", 3),               # армянский (вне таблицы) — фолбэк 0.5
    ("2026", 3),                   # цифры — 0.5/символ
    ("😀🔥", 1),                    # эмодзи — 0.35/символ
])
def test_estimate_tokens_scripts(text, expected):
    assert C.estimate_tokens(text) == expected


def test_estimate_tokens_scripts_ratio():
    # один смысл по длине: китайский считается ~1 токен/иероглиф,
    # русский — примерно втрое меньше символами
    assert C.estimate_tokens("字" * 100) > 3 * C.estimate_tokens("ж" * 100)
    # неучтённая письменность считается с запасом против кириллицы
    assert C.estimate_tokens("Բ" * 100) > C.estimate_tokens("ж" * 100)


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


# ══════════════════════════════════════════════════════════════════════
# extract_term_context — контекст термина из чанка
# ══════════════════════════════════════════════════════════════════════
def test_extract_term_context_cjk():
    text = "李小明走进大殿。殿内站着许多人。李小明微微一笑。"
    ctx = C.extract_term_context(text, "李小明", 300)
    # только предложение с термином, самое длинное из найденных
    assert ctx == "李小明走进大殿。"


def test_extract_term_context_latin():
    text = "John walked into the hall. Many people stood inside. John smiled."
    ctx = C.extract_term_context(text, "John", 300)
    assert ctx == "John walked into the hall."


def test_extract_term_context_whitespace_tolerant():
    # термин с пробелами; в тексте слова разделены по-другому
    # (двойные пробелы/перенос строки) — пробелы схлопываются
    text = "Линь  Шуй шёл домой. Потом Линь\nШуй вернулся."
    ctx = C.extract_term_context(text, "Линь Шуй", 300)
    assert ctx == "Потом Линь Шуй вернулся."


def test_extract_term_context_max_len_trim():
    # длинное предложение без границ — окно вокруг термина
    text = "前 " * 10 + "ТЕРМИН " + "后 " * 10
    ctx = C.extract_term_context(text, "ТЕРМИН", 20)
    assert len(ctx) <= 20 and "ТЕРМИН" in ctx


def test_extract_term_context_single_sentence():
    # соседнее предложение не берётся — только предложение с термином
    text = "Короткое. Второе предложение влезло бы, но не нужно."
    ctx = C.extract_term_context(text, "Короткое", 300)
    assert ctx == "Короткое."


def test_extract_term_context_disabled_and_missing():
    assert C.extract_term_context("текст ТЕРМИН текст", "ТЕРМИН", 0) == ""
    assert C.extract_term_context("текст ТЕРМИН текст", "ТЕРМИН", None) == ""
    assert C.extract_term_context("текст", "ТЕРМИН", 300) == ""  # нет вхождения
    assert C.extract_term_context("", "ТЕРМИН", 300) == ""
    assert C.extract_term_context("текст", "", 300) == ""


def test_extract_term_context_closing_quotes():
    """Закрывающие кавычки/скобки после знака конца — часть предыдущего
    предложения (не начинают следующее)."""
    text = "Он сказал: «Привет, Джон!» И ушёл."
    ctx = C.extract_term_context(text, "Джон", 300)
    assert ctx == "Он сказал: «Привет, Джон!»"
    # кавычка без знака конца внутри — не обрывает предложение
    text2 = "Затем громкий «привет, Джон» разнёсся по залу. Тишина."
    ctx2 = C.extract_term_context(text2, "Джон", 300)
    assert ctx2 == "Затем громкий «привет, Джон» разнёсся по залу."


def test_extract_term_context_multilang_ends():
    """Знаки конца любых языков: арабский ؟, деванагари ।, армянский ։."""
    text = "مرحبا جون كيف حالك. جون ذهب إلى البيت؟"
    ctx = C.extract_term_context(text, "جون", 300)
    assert ctx == "مرحبا جون كيف حالك."
    ctx2 = C.extract_term_context("Сегодня Джон пришёл। Завтра уйдёт.",
                                  "Джон", 300)
    assert ctx2 == "Сегодня Джон пришёл।"  # граница по ।, знак в предложении


def test_extract_term_context_fuzzy_fallback():
    """Точного вхождения нет — нечёткий фолбэк по предложениям
    (n-граммное перекрытие + longest match, порог threshold).
    Напр. «Линь Шуй» найдётся в «Линь-Шуй» (пунктуация вырезана)."""
    text = "Утром Линь-Шуй собрался в дорогу. Погода была ясная."
    # без порога — фолбэк выключен, термин не найден
    assert C.extract_term_context(text, "Линь Шуй", 300) == ""
    ctx = C.extract_term_context(text, "Линь Шуй", 300, threshold=0.75)
    assert ctx == "Утром Линь-Шуй собрался в дорогу."
    # далеко не похожее предложение не берётся
    ctx2 = C.extract_term_context(text, "Чжао Минь", 300, threshold=0.75)
    assert ctx2 == ""


def test_extract_term_context_fuzzy_cjk_exact_only():
    """CJK-термин — только точный поиск (фолбэк не срабатывает)."""
    text = "李晓明走进大殿。"
    assert C.extract_term_context(text, "李小明", 300,
                                  threshold=0.75) == ""
    assert C.extract_term_context(text, "李晓明", 300,
                                  threshold=0.75) == "李晓明走进大殿。"


@pytest.mark.parametrize(
    "raw,expect",
    [
        ("a  b", "a··b"),
        ("\tтаб", "\\tтаб"),   # таб — текстовая метка: ⇥ рендерится стрелкой
        ("a\n\nb", "a⏎\n⏎\nb"),      # перенос остаётся переносом, метка перед ним
        ("a\r\nb", "a␍⏎\nb"),
        ("", ""),
        (None, ""),
        (7, "7"),
        ("·⏎", "·⏎"),                    # уже размеченное не меняется
    ],
)
def test_mark_whitespace(raw, expect):
    r"""Пробелы видимы (·, 	, ␍, ⏎) — предпросмотр и отчёт замен."""
    assert C.mark_whitespace(raw) == expect


def test_trim_rule_left():
    """Правила замен: у «->» срезается только её пробельный хвост;
    пробелы паттерна значимы и внутри, и перед ним."""
    assert C.trim_rule_left("Хунг ") == "Хунг"
    assert C.trim_rule_left(" Хунг") == " Хунг"   # ведущий пробел — часть паттерна
    assert C.trim_rule_left(" +$") == " +$"        # хвостовые пробелы строк
    assert C.trim_rule_left("^  ") == "^  "          # отступ строки
    assert C.trim_rule_left("  $ ") == "  $"          # хвостовые пробелы
    assert C.trim_rule_left("^ ") == "^"              # хвост у стрелки — паддинг
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


def test_parse_dotenv_comment_and_dollar(tmp_path):
    """Синтаксис .env (AGENTS §7): `#` вне кавычек — комментарий, в кавычках —
    обычный символ; `${VAR}` не раскрывается (интерполяции сознательно нет)."""
    p = tmp_path / ".env"
    p.write_text(
        "INLINE=значение # комментарий\n"
        'HASH_QUOTED="a # b"\n'
        "DOLLAR=a${OTHER}c\n"
        "OTHER=x\n",
        encoding="utf-8")
    data = C.parse_dotenv(str(p))
    assert data["INLINE"] == "значение"
    assert data["HASH_QUOTED"] == "a # b"
    assert data["DOLLAR"] == "a${OTHER}c"


def test_system_env_file(monkeypatch, tmp_path):
    """system_env_file: WEB_ENV_FILE перекрывает всё (и возвращается даже
    без файла — это цель создания в редакторе «Настроек»); без переменной —
    корневой .env репо, а не .env книги: общий конфиг и файл книги — РАЗНЫЕ
    слои, подъём вверх от папки проекта больше не нужен."""
    monkeypatch.delenv("WEB_ENV_FILE", raising=False)
    monkeypatch.setattr(C, "_REPO_ROOT", str(tmp_path / "repo"))
    monkeypatch.chdir(tmp_path)
    assert C.system_env_file() is None  # ни общего, ни cwd-файла
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    (root / ".env").write_text("HOST=x", encoding="utf-8")
    assert C.system_env_file() == str(root / ".env")
    # cwd с .env — только когда общего файла нет
    (tmp_path / ".env").write_text("A=1", encoding="utf-8")
    assert C.system_env_file() == str(root / ".env")
    (root / ".env").unlink()
    assert C.system_env_file() == str(tmp_path / ".env")
    # WEB_ENV_FILE (Docker: projects/.env в томе) — даже если файла нет
    monkeypatch.setenv("WEB_ENV_FILE", str(tmp_path / "vol" / ".env"))
    assert C.system_env_file() == str(tmp_path / "vol" / ".env")


def test_env_files_single_file(tmp_path, monkeypatch):
    """Файл конфига один: собственного .env у книги больше нет.

    Раньше подъём вверх от папки книги находил её же копию общего файла, и
    книга жила копией, а не общим конфигом.
    """
    monkeypatch.delenv("WEB_ENV_FILE", raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(C, "_REPO_ROOT", str(root))
    monkeypatch.chdir(root)
    shared = root / ".env"
    shared.write_text("HOST=http://shared\n", encoding="utf-8")
    book = root / "projects" / "ACTIVE" / "book"
    (book / "chapters").mkdir(parents=True)
    assert C.env_files() == [str(shared)]
    # файл в папке книги — больше не слой
    (book / ".env").write_text("MODEL=книжная\n", encoding="utf-8")
    assert C.env_files() == [str(shared)]
    assert C.load_env() == {"HOST": "http://shared"}
    # явный --env_file заменяет общий файл собой; несуществующий — слои те же
    one = tmp_path / "manual.env"
    one.write_text("A=1", encoding="utf-8")
    assert C.env_files(explicit=str(one)) == [str(one)]
    assert C.env_files(explicit=str(tmp_path / "нет.env")) == [str(shared)]


def test_get_server_config(monkeypatch):
    """Сервер, ключ и модель — общие ключи; стадийные игнорируются."""
    for k in ("HOST", "API_KEY", "MODEL", "NER_HOST", "NER_MODEL"):
        monkeypatch.delenv(k, raising=False)
    cfg = C.get_server_config({"HOST": "http://h", "MODEL": "m"})
    assert cfg == {"host": "http://h", "api_key": "", "model": "m"}
    # legacy-ключи профилей игнорируются
    assert C.get_server_config({"LOCAL_HOST": "x"})["host"] == ""
    # стадийные ключи — тоже: модель в конвейере одна
    assert C.get_server_config({"HOST": "http://h", "NER_HOST": "http://ner",
                                "NER_MODEL": "нер",
                                "NER_API_KEY": "нер-ключ"}) == {
        "host": "http://h", "api_key": "", "model": ""}
    assert C.get_server_config({}) == {"host": "", "api_key": "", "model": ""}


def test_get_server_config_environment_wins(monkeypatch):
    """Канон §7: os.environ приоритетнее .env (Docker env_file → окружение)."""
    for k in ("HOST", "API_KEY", "MODEL", "NER_HOST"):
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
    # стадийный ключ окружения игнорируется: сервер один на весь запуск
    monkeypatch.setenv("NER_HOST", "http://ner")
    assert C.get_server_config({"HOST": "http://file"})["host"] == "http://env"
    # пустые значения окружения = отсутствуют (файл остаётся)
    monkeypatch.setenv("HOST", "  ")
    assert C.get_server_config({"HOST": "http://file"})["host"] == "http://file"


def test_env_overlay(monkeypatch):
    """env_overlay: перечисленные ключи перекрываются непустыми
    значениями os.environ (канон §7: окружение > файл); остальные
    ключи файла не трогаются, лишние из окружения не подмешиваются."""
    for k in ("HOST", "MODEL", "NER_HOST"):
        monkeypatch.delenv(k, raising=False)
    env = {"HOST": "http://file", "MODEL": "fm", "KEY": "v"}
    assert C.env_overlay(env, ["HOST"]) == {
        "HOST": "http://file", "MODEL": "fm", "KEY": "v"}
    monkeypatch.setenv("HOST", "http://env")
    assert C.env_overlay(env, ["HOST"]) == {
        "HOST": "http://env", "MODEL": "fm", "KEY": "v"}
    # пустое значение окружения = отсутствует (файл остаётся)
    monkeypatch.setenv("HOST", "   ")
    assert C.env_overlay(env, ["HOST"])["HOST"] == "http://file"
    # ключ из окружения вне списка не подмешивается; исходный dict
    # не мутируется (стадийные LLM-ключи в список не входят в принципе)
    monkeypatch.setenv("NER_HOST", "http://ner")
    assert C.env_overlay(env, ["HOST"])["HOST"] == "http://file"
    assert env["HOST"] == "http://file"



def test_get_server_config_remote(monkeypatch):
    for k in ("HOST", "API_KEY", "MODEL"):
        monkeypatch.delenv(k, raising=False)
    env = {"HOST": "https://r", "API_KEY": "k", "MODEL": "m"}
    cfg = C.get_server_config(env)
    assert cfg == {"host": "https://r", "api_key": "k", "model": "m"}
    assert C.get_server_config({}) == {"host": "", "api_key": "", "model": ""}


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


def test_atomic_write_through_symlink(tmp_path):
    """Симлинк-цель: запись идёт в реальный файл, симлинк сохраняется
    (docker: /app/.env → env.d/.env)."""
    real = tmp_path / "env.d" / ".env"
    real.parent.mkdir()
    real.write_text("HOST=old\n", encoding="utf-8")
    link = tmp_path / ".env"
    link.symlink_to(real)
    C.atomic_write(str(link), "HOST=new\n")
    # симлинк не заменён файлом; цель обновлена
    assert link.is_symlink()
    assert real.read_text(encoding="utf-8") == "HOST=new\n"
    assert not list(real.parent.glob("*.tmp"))


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
    head = "Глава 1. Проиграл всё\n\n".encode()
    filler = "Текст главы. ".encode() * 120
    buf = head + filler
    assert len(buf) < 4095
    buf += b"x" * (4095 - len(buf))
    buf += "Ж".encode() + "\nхвост".encode()
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


def test_collect_gender_names_string_count(tmp_path):
    """count-строки в ner.json не ломают сортировку имён (регрессия
    артефакта [FAIL: bad operand type for unary -: 'str'])."""
    ner = [
        {"term": "陈阳", "translation": "Чэнь Ян",
         "type": "Person (male)", "count": "5"},   # строка!
        {"term": "白虎", "translation": "Байху",
         "type": "Creature", "count": 50},
        {"term": "苏星宇", "translation": "Су Синюй",
         "type": "Person (male)", "count": "abc"},  # мусор → 0
    ]
    p = tmp_path / "ner.json"
    p.write_text(json.dumps(ner, ensure_ascii=False), encoding="utf-8")
    data, automaton = C.load_ner_data(str(p), 3, SilentLog())
    # load_ner_data нормализует count к int
    by_term = {d["term"]: d["count"] for d in data}
    assert by_term["陈阳"] == 5 and by_term["白虎"] == 50
    assert by_term["苏星宇"] == 0
    # сортировка по -count не падает (был TypeError со строкой)
    female, male = C.collect_gender_names(
        "Чэнь Ян и Су Синюй", data, 0.7, 3)
    assert "Чэнь Ян" in male and "Су Синюй" in male
    # find_relevant_ner с порогом тоже терпит строки
    s, cnt = C.find_relevant_ner("白虎", data, 0.7, 3,
                                 "term,translation,type",
                                 automaton=automaton, min_count=10)
    assert cnt == 1 and "白虎" in s


def test_find_relevant_ner_min_count(tmp_path):
    """min_count: термины с count ниже порога не попадают в блок."""
    ner = [
        {"term": "陈阳", "translation": "Чэнь Ян", "type": "Person (male)",
         "count": 3},
        {"term": "白虎", "translation": "Байху", "type": "Creature",
         "count": 50},
    ]
    p = tmp_path / "ner.json"
    p.write_text(json.dumps(ner, ensure_ascii=False), encoding="utf-8")
    data, automaton = C.load_ner_data(str(p), 3, SilentLog())
    # порог 10: 陈阳 (3) отсекается, 白虎 (50) остаётся
    s, cnt = C.find_relevant_ner("陈阳 и 白虎", data, 0.7, 3,
                                 "term,translation,type",
                                 automaton=automaton, min_count=10)
    assert cnt == 1 and "白虎" in s and "陈阳" not in s
    # порог 0 (по умолчанию) — оба попадают
    s2, cnt2 = C.find_relevant_ner("陈阳 и 白虎", data, 0.7, 3,
                                   "term,translation,type",
                                   automaton=automaton)
    assert cnt2 == 2 and "陈阳" in s2


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


def test_find_relevant_dict_normal_direction(tmp_path):
    """Обычное направление (чанк на языке term): записи с term-стороны,
    ориентация каноническая."""
    f = tmp_path / "dict.json"
    f.write_text(json.dumps([
        {"term": "苏星宇", "translation": "Су Синюй"},
        {"term": "大殿", "translation": "великий зал"},
    ], ensure_ascii=False), encoding="utf-8")
    data, automaton = C.load_ner_data(str(f), 3, SilentLog())
    s, cnt = C.find_relevant_dict("苏星宇走进了大殿", data, 0.7, 3,
                                  automaton=automaton)
    recs = json.loads(s)
    assert cnt == 2
    assert recs[0]["term"] == "苏星宇"
    assert recs[0]["translation"] == "Су Синюй"


def test_find_relevant_dict_reverse_direction(tmp_path):
    """Обратное направление: чанк на языке translation — автодетект
    выбирает translation-сторону, записи переворачиваются."""
    f = tmp_path / "dict.json"
    f.write_text(json.dumps([
        {"term": "苏星宇", "translation": "Су Синюй"},
        {"term": "大殿", "translation": "великий зал"},
    ], ensure_ascii=False), encoding="utf-8")
    data, automaton = C.load_ner_data(str(f), 3, SilentLog())
    s, cnt = C.find_relevant_dict(
        "Су Синюй вошёл в великий зал.", data, 0.7, 3,
        automaton=automaton)
    recs = json.loads(s)
    assert cnt == 2
    # term = сторона, найденная в тексте; translation — противоположная
    by_term = {r["term"]: r for r in recs}
    assert by_term["Су Синюй"]["translation"] == "苏星宇"
    assert by_term["великий зал"]["translation"] == "大殿"


def test_find_relevant_dict_prefers_bigger_side(tmp_path):
    """Совпадения на обеих сторонах — берётся та, где больше записей."""
    f = tmp_path / "dict.json"
    f.write_text(json.dumps([
        {"term": "苏星宇", "translation": "Су Синюй"},
        {"term": "长老", "translation": "старейшина"},
        {"term": "大殿", "translation": "зал"},
    ], ensure_ascii=False), encoding="utf-8")
    data, automaton = C.load_ner_data(str(f), 3, SilentLog())
    # term-сторона: 2 совпадения (苏星宇, 长老); translation: 1 (зал)
    s, cnt = C.find_relevant_dict("苏星宇和长老。 зал", data, 0.7, 3,
                                  automaton=automaton)
    recs = json.loads(s)
    assert cnt == 2
    assert {r["term"] for r in recs} == {"苏星宇", "长老"}


def test_find_relevant_dict_empty_inputs():
    assert C.find_relevant_dict("", [], 0.7, 3) == ("[]", 0)
    assert C.find_relevant_dict("текст", [], 0.7, 3) == ("[]", 0)
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
# РАСШИРЕННЫЙ КОНТЕКСТ: примеры (few-shot), правила, словарь
# ══════════════════════════════════════════════════════════════════════
def _pair(orig, trans):
    return {"original_text": orig, "translated_text": trans}


def test_load_examples_ok(tmp_path):
    f = tmp_path / "examples.json"
    f.write_text(json.dumps([
        _pair("第一章 神秘道种", "Глава 1. Таинственное семя дао"),
        _pair("苏星宇睁开了眼", "Су Синюй открыл глаза"),
    ], ensure_ascii=False), encoding="utf-8")
    ex = C.load_examples(str(f), 3, SilentLog())
    assert len(ex) == 2
    assert ex[0]["translated_text"] == "Глава 1. Таинственное семя дао"
    # кэш нормализации и n-грамм source-стороны
    assert ex[0]["_norm"] == C.normalize_for_search("第一章 神秘道种")
    assert "神秘道" in ex[0]["_ngrams"]  # 3-грамма "第一章神秘道种"


def test_load_examples_aliases_source_target(tmp_path):
    """Алиасы source/target принимаются (совместимость trace-файлов)."""
    f = tmp_path / "examples.json"
    f.write_text(json.dumps([
        {"source": "原文", "target": "Перевод"},
    ], ensure_ascii=False), encoding="utf-8")
    ex = C.load_examples(str(f), 3, SilentLog())
    assert len(ex) == 1
    assert ex[0]["original_text"] == "原文"
    assert ex[0]["translated_text"] == "Перевод"


def test_load_examples_skips_bad_records(tmp_path):
    """Записи без одной из сторон / не dict — пропускаются."""
    f = tmp_path / "examples.json"
    f.write_text(json.dumps([
        {"original_text": "只有原文"},          # нет перевода
        {"translated_text": "только перевод"},  # нет оригинала
        "не-объект",
        _pair("ок", "норм"),
    ], ensure_ascii=False), encoding="utf-8")
    ex = C.load_examples(str(f), 3, SilentLog())
    assert len(ex) == 1 and ex[0]["original_text"] == "ок"


def test_load_examples_missing_and_broken(tmp_path):
    assert C.load_examples(str(tmp_path / "нет.json"), 3, SilentLog()) == []
    f = tmp_path / "examples.json"
    f.write_text("не json{", encoding="utf-8")
    assert C.load_examples(str(f), 3, SilentLog()) == []
    f.write_text('{"не": "список"}', encoding="utf-8")
    assert C.load_examples(str(f), 3, SilentLog()) == []


def _load_pairs(tmp_path, pairs):
    f = tmp_path / "examples.json"
    f.write_text(json.dumps(pairs, ensure_ascii=False), encoding="utf-8")
    return C.load_examples(str(f), 3, SilentLog())


def test_find_relevant_examples_rank_order(tmp_path):
    chunk = "苏星宇走进了大殿。长老们都在等待。"
    ex = _load_pairs(tmp_path, [
        _pair("苏星宇走进了大殿。", "Су Синюй вошёл в зал."),
        _pair("他去了市场买药。", "Он пошёл на рынок за лекарством."),
        _pair("长老们都在等待。", "Старейшины ждали."),
    ])
    sel = C.find_relevant_examples(chunk, ex, k=2, threshold=0.3,
                                   ngram_size=3)
    # топ-2: оба пересекающихся примера, нерелевантный не попал
    assert len(sel) == 2
    assert sel[0]["original_text"].startswith("苏星宇")
    assert any(e["original_text"].startswith("长老") for e in sel)
    assert not any("市场" in e["original_text"] for e in sel)


def test_find_relevant_examples_threshold_cutoff(tmp_path):
    """Ниже порога — пример отсекается (лучше без шумных)."""
    ex = _load_pairs(tmp_path, [
        _pair("совершенно другой текст", "совсем другой перевод"),
    ])
    sel = C.find_relevant_examples("苏星宇走进大殿", ex, k=3, threshold=0.3)
    assert sel == []


def test_find_relevant_examples_k_limit(tmp_path):
    ex = _load_pairs(tmp_path, [
        _pair(f"相同句子{i} 内容", f"перевод {i}") for i in range(10)
    ])
    # все 10 одинаково похожи на чанк (пересечение 2/5 n-грамм = 0.4)
    chunk = "相同句子3 内容"
    sel = C.find_relevant_examples(chunk, ex, k=3, threshold=0.3)
    assert len(sel) == 3
    assert sel[0]["original_text"] == "相同句子3 内容"  # лучший — первым


def test_find_relevant_examples_side_autodetect(tmp_path):
    """Автодетект направления: пара считается по стороне, где
    совпадений больше (исходный язык или язык перевода)."""
    ex = _load_pairs(tmp_path, [
        _pair("苏星宇走进了大殿。", "Су Синюй вошёл в зал."),
    ])
    # обычное направление: чанк на исходном языке → сторона src
    sel = C.find_relevant_examples("长老们看着苏星宇走进了大殿", ex,
                                   k=1, threshold=0.3)
    assert len(sel) == 1 and sel[0]["_side"] == "src"
    # обратное: чанк на языке перевода → сторона tgt
    sel = C.find_relevant_examples("Здесь Су Синюй вошёл в зал.", ex,
                                   k=1, threshold=0.3)
    assert len(sel) == 1 and sel[0]["_side"] == "tgt"
    # обе стороны мимо порога — пусто
    assert C.find_relevant_examples("совершенно другой текст", ex,
                                    k=1, threshold=0.3) == []


def test_find_relevant_examples_empty_inputs(tmp_path):
    assert C.find_relevant_examples("", [], 3) == []
    assert C.find_relevant_examples("текст", [], 3) == []
    assert C.find_relevant_examples("   ", [], 3) == []


def test_format_fewshot_block():
    assert C.format_fewshot_block([]) == ""
    block = C.format_fewshot_block([
        _pair("原文一", "Перевод один"),
        _pair("原文二", "Перевод два"),
    ])
    # JSON-массив, по одной паре на строку; служебный _side не утекает
    assert block.startswith("[") and block.endswith("]")
    assert '{"original_text": "原文一", "translated_text": "Перевод один"}' in block
    assert '{"original_text": "原文二", "translated_text": "Перевод два"}' in block
    assert "_side" not in block


def test_load_rules_block(tmp_path):
    f = tmp_path / "rules.md"
    f.write_text("Правила:\n1. Глаголы ставятся в конце.\n", encoding="utf-8")
    text = C.load_rules_block(str(f), SilentLog())
    assert "Глаголы" in text
    # нет файла → ""
    assert C.load_rules_block(str(tmp_path / "нет.txt"), SilentLog()) == ""


# ══════════════════════════════════════════════════════════════════════
# МОКИ ДЛЯ stream_chat_completion
# ══════════════════════════════════════════════════════════════════════
class _FakeResp:
    """Заглушка ResponseStream из core/transport.py (тот же контракт)."""

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
    """Мок единого шва транспорта: core.common.open_stream(url, headers=…,
    payload=…, connect_timeout=…, read_timeout=…). В capture — прежние ключи
    (json/timeout), чтобы проверки payload не переписывать."""

    def fake_open_stream(url, *, headers=None, payload=None,
                        connect_timeout=None, read_timeout=None):
        if capture is not None:
            capture.update(url=url, headers=headers or {}, json=payload or {},
                           timeout=(connect_timeout, read_timeout))
        return _FakeResp(lines, status)

    monkeypatch.setattr(C, "open_stream", fake_open_stream)


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
    # рассуждения не заданы: никаких reasoning-полей — дефолт сервера
    assert "reasoning" not in cap["json"]
    assert "reasoning_effort" not in cap["json"]
    assert "Authorization" not in cap["headers"]            # api_key пуст


def test_stream_payload_options(monkeypatch):
    cap = {}
    _patch_post(monkeypatch, _sse(["ок"]), capture=cap)
    C.stream_chat_completion("http://h/v1", "m", [], api_key="СЕКРЕТ",
                             reasoning={"reasoning_effort": "low"},
                             temperature=0.2, max_tokens=1024)
    assert cap["headers"]["Authorization"] == "Bearer СЕКРЕТ"
    assert cap["json"]["reasoning_effort"] == "low"
    assert cap["json"]["temperature"] == 0.2
    assert cap["json"]["max_tokens"] == 1024


def test_stream_reasoning_keys_pass_through(monkeypatch):
    """Ключи рассуждений уходят в payload как есть.

    Способ передачи у провайдеров разный (openai — reasoning_effort,
    anthropic — thinking, ollama — think), поэтому в стрим едет готовый dict
    профиля; пустой dict — «не трогаем» (дефолт сервера)."""
    cap = {}
    _patch_post(monkeypatch, _sse(["ок"]), capture=cap)
    C.stream_chat_completion("http://h/v1", "m", [],
                             reasoning={"reasoning_effort": "none"})
    assert cap["json"]["reasoning_effort"] == "none"

    cap2 = {}
    _patch_post(monkeypatch, _sse(["ок"]), capture=cap2)
    C.stream_chat_completion("http://h/v1", "m", [], reasoning={})
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
    varied = "Слово1 отличается. " + "".join(f"фраза{i} " for i in range(120))
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

    def fake_open_stream(url, **kw):
        calls["n"] += 1
        return _FakeResp([], status=401)

    monkeypatch.setattr(C, "open_stream", fake_open_stream)
    text, err = C.stream_chat_completion("h", "m", [], max_retries=5)
    assert text is None and err == "HTTP 401" and calls["n"] == 1


def test_stream_http_429_retries_with_retry_after(monkeypatch):
    """H3: 429 ретраится; Retry-After уважается (sleep замокан)."""
    calls = {"n": 0}

    def fake_open_stream(url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp([], status=429, headers={"Retry-After": "3"})
        return _FakeResp(_sse(["после паузы"]))

    monkeypatch.setattr(C, "open_stream", fake_open_stream)
    text, err = C.stream_chat_completion("h", "m", [], max_retries=3)
    assert err == "" and text == "после паузы" and calls["n"] == 2


def test_stream_http_500_retries_then_fails(monkeypatch):
    """H3: 5xx ретраится до исчерпания попыток."""
    calls = {"n": 0}

    def fake_open_stream(url, **kw):
        calls["n"] += 1
        return _FakeResp([], status=503)

    monkeypatch.setattr(C, "open_stream", fake_open_stream)
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

    def fake_open_stream(url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise C.ReadTimeout()
        if calls["n"] == 2:
            raise C.ConnectTimeout()
        if calls["n"] == 3:
            raise C.BrokenStream()
        return _FakeResp(_sse(["успех после ретраев"]))

    monkeypatch.setattr(C, "open_stream", fake_open_stream)
    text, err = C.stream_chat_completion("h", "m", [], max_retries=5)
    assert err == "" and text == "успех после ретраев" and calls["n"] == 4


def test_stream_timeout_exhausts_retries(monkeypatch):
    def fake_open_stream(*a, **k):
        raise C.ReadTimeout()

    monkeypatch.setattr(C, "open_stream", fake_open_stream)
    text, err = C.stream_chat_completion("h", "m", [], max_retries=2,
                                         stream_timeout=900)
    assert text is None and err == "Read timeout (900s)"


def test_stream_generic_exception_and_logger(monkeypatch, caplog):
    def fake_open_stream(*a, **k):
        raise RuntimeError("всё сломалось")

    monkeypatch.setattr(C, "open_stream", fake_open_stream)
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
    """M2 (AUDIT): значения --api_key/--token в лог НЕ попадают;
    --max_tokens — лимит ответа, не секрет: в лог попадает как есть"""
    out = tmp_path / "секрет.txt"
    logger, log_name = C.setup_logging(str(out), logger_name="тест.секрет")
    C.log_argv(logger, argv=[
        "python3", "cli/translate_book.py", "--api_key", "СЕКРЕТ-КЛЮЧ",
        "--model", "модель", "--host", "http://h",
        "--token=ТОКЕН", "--timeout", "300",
        "--max_tokens", "65536", "--max-tokens=1024",
    ])
    for h in logger.handlers:
        h.flush()
    text = Path(log_name).read_text(encoding="utf-8")
    assert "СЕКРЕТ-КЛЮЧ" not in text and "ТОКЕН" not in text
    assert "--api_key '••••'" in text  # shlex.join берёт значение в кавычки
    assert "'--token=••••'" in text
    assert "--model 'модель'" in text and "http://h" in text
    # max_tokens — не секрет: значение видно в логе
    assert "65536" in text and "--max-tokens=1024" in text
    assert "--max_tokens '••••'" not in text


# ══════════════════════════════════════════════════════════════════════
# предпросмотр запроса (preview_request_payload / write / logger)
# ══════════════════════════════════════════════════════════════════════

def test_preview_request_payload_chars_and_meta():
    """Сводка символов по ролям + total; meta — только если задана."""
    messages = [{"role": "system", "content": "сист"},
                {"role": "user", "content": "юзер"}]
    p = C.preview_request_payload("ner", "Pass1 · чанк 1/3", "модель",
                                  messages, meta={"chunks": 3})
    assert p["stage"] == "ner" and p["model"] == "модель"
    assert p["label"] == "Pass1 · чанк 1/3"
    assert p["chars"] == {"system": 4, "user": 4, "total": 8}
    assert p["meta"] == {"chunks": 3}
    assert p["messages"] is messages
    # без meta ключа нет (не пустой словарь)
    p2 = C.preview_request_payload("wiki", "L", None, [])
    assert "meta" not in p2 and p2["model"] == "" and p2["chars"]["total"] == 0


def test_preview_request_write_roundtrip(tmp_path):
    """write_preview_request: атомарная JSON-запись; кириллица
    сохраняется как есть (ensure_ascii=False)."""
    payload = C.preview_request_payload(
        "pipeline", "Перевод · чанк 1/2", "модель-х",
        [{"role": "user", "content": "Переведи: 主角"}],
        meta={"mode": "translate"})
    path = tmp_path / "preview.json"
    C.write_preview_request(str(path), payload)
    raw = path.read_text(encoding="utf-8")
    assert "Переведи: 主角" in raw  # без \\u-экранирования
    import json as _json
    assert _json.loads(raw) == payload


def test_preview_logger_stderr_only():
    """preview_logger: только stderr-хендлер, propagate выключен —
    записи не утекают в файловый лог запуска (mode="w")."""
    import logging
    log = C.preview_logger("тест")
    assert log.handlers, "должен быть хотя бы один хендлер"
    assert all(getattr(h, "stream", None) is sys.stderr
               for h in log.handlers)
    assert not any(isinstance(h, logging.FileHandler)
                   for h in log.handlers)
    assert log.propagate is False


# ══════════════════════════════════════════════════════════════════════
# flex_fragment_pattern / find_fragment_owner (переаттестация цитат)
# ══════════════════════════════════════════════════════════════════════

def test_flex_fragment_pattern_matches_typography():
    """Мягкий паттерн: кавычки «»/"", тире, многоточие и пробелы
    эквивалентны; обычный текст работает как re.escape."""
    pat = C.flex_fragment_pattern('Он сказал: «стой… стрелять» — и замолчал')
    text = 'Он сказал: "стой... стрелять" - и замолчал сразу'
    assert re.search(pat, text)
    # короткий репрезентативный набор
    assert re.search(C.flex_fragment_pattern("а — б"), "а – б")
    assert re.search(C.flex_fragment_pattern("а «б»"), 'а "б"')
    # пробелы становятся \s+, остальное — re.escape
    assert C.flex_fragment_pattern("просто текст") == "просто\\s+текст"
    # точка не становится «любым символом»
    assert not re.search(C.flex_fragment_pattern("а.б"), "аXб")


def test_find_fragment_owner_unique(tmp_path):
    """Цитата ровно в одной главе → её номер; claimed исключается."""
    ch_dir = tmp_path / "chapters"
    for num, text in {1: "начало текст без цитаты.", 2: "здесь уникальная фраза про дракона.",
                      3: "третья глава тоже без неё."}.items():
        d = ch_dir / f"00000_{num}_t"
        d.mkdir(parents=True)
        (d / "polished.txt").write_text(text, encoding="utf-8")
    cmap = C.build_chapter_map(str(ch_dir), SilentLog())
    ch, why = C.find_fragment_owner(cmap, "уникальная фраза про дракона",
                                    claimed=1, want="polished")
    assert (ch, why) == (2, None)
    # claimed совпадает с владельцем → не кандидат, ничего не найдено
    ch, why = C.find_fragment_owner(cmap, "уникальная фраза про дракона",
                                    claimed=2, want="polished")
    assert ch is None and why is not None and "не найдена" in why


def test_find_fragment_owner_ambiguous_and_short(tmp_path):
    """Несколько равных совпадений → None; короткие цитаты не ищутся."""
    ch_dir = tmp_path / "chapters"
    frag = "повторяющаяся фраза в двух главах книги"
    for num, text in {1: f"вот {frag} тут.", 2: "пусто.", 3: f"и {frag} здесь."}.items():
        d = ch_dir / f"00000_{num}_t"
        d.mkdir(parents=True)
        (d / "polished.txt").write_text(text, encoding="utf-8")
    cmap = C.build_chapter_map(str(ch_dir), SilentLog())
    ch, why = C.find_fragment_owner(cmap, frag, want="polished")
    assert ch is None and why is not None and "не однозначно" in why
    # короткий фрагмент
    ch, why = C.find_fragment_owner(cmap, "пусто.", want="polished")
    assert ch is None and why is not None and "короче" in why


# ══════════════════════════════════════════════════════════════════════
# REASONING / THINKING: реестр профилей провайдеров
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("profile,mode,effort,budget,expect", [
    # openai: уровень и есть весь режим; «включено» без уровня — решение сервера
    ("openai", "default", "", 0, {}),
    ("openai", "on", "", 0, {}),
    ("openai", "off", "", 0, {"reasoning_effort": "none"}),
    ("openai", "on", "high", 0, {"reasoning_effort": "high"}),
    # anthropic: budget_tokens только при включённых рассуждениях
    ("anthropic", "default", "", 0, {}),
    ("anthropic", "on", "", 2048,
     {"thinking": {"type": "enabled", "budget_tokens": 2048}}),
    ("anthropic", "on", "", 0, {"thinking": {"type": "enabled"}}),
    ("anthropic", "off", "", 2048, {"thinking": {"type": "disabled"}}),
    # qwen: ключ живёт в шаблоне чата
    ("qwen", "on", "high", 0,
     {"chat_template_kwargs": {"thinking": True, "enable_thinking": True}}),
    ("qwen", "off", "", 0,
     {"chat_template_kwargs": {"thinking": False, "enable_thinking": False}}),
    ("dashscope", "on", "", 4096,
     {"enable_thinking": True, "thinking_budget": 4096}),
    ("ollama", "off", "", 0, {"think": False}),
    # openrouter: только ОДНО из effort и max_tokens
    ("openrouter", "on", "low", 2048,
     {"reasoning": {"enabled": True, "effort": "low"}}),
    ("openrouter", "on", "", 2048,
     {"reasoning": {"enabled": True, "max_tokens": 2048}}),
])
def test_reasoning_fields_matrix(profile, mode, effort, budget, expect):
    assert C.reasoning_fields(mode, profile, effort, budget) == expect


def test_reasoning_fields_unknown_profile_is_openai():
    assert C.reasoning_fields("off", "neizvestno", "", 0) == (
        {"reasoning_effort": "none"})
    assert C.reasoning_fields("default", "", "", 0) == {}


def test_reasoning_fields_unknown_mode_is_default():
    """Неизвестный режим — не трогать запрос, а не угадывать."""
    assert C.reasoning_fields("galochka", "anthropic", "", 0) == {}


def test_reasoning_fields_all_sends_every_key():
    """«all» — отдельный осознанный режим: ключи всех профилей разом."""
    assert set(C.reasoning_fields("on", "all", "high", 2048)) == {
        "reasoning_effort", "thinking", "chat_template_kwargs",
        "enable_thinking", "thinking_budget", "think", "reasoning"}


def test_reasoning_settings_env_over_file(monkeypatch):
    """Окружение перекрывает файл точечно, по ключу (AGENTS §7)."""
    for k in C.REASONING_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REASONING_MODE", "on")
    assert C.reasoning_settings({
        "REASONING_MODE": "off", "THINKING_PROFILE": "qwen",
        "REASONING_EFFORT": "low", "THINKING_BUDGET": "1024",
    }) == {"mode": "on", "profile": "qwen", "effort": "low", "budget": 1024}


def test_reasoning_settings_defaults_and_bad_budget(monkeypatch):
    for k in C.REASONING_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    assert C.reasoning_settings({}) == {"mode": "default", "profile": "openai",
                                       "effort": "", "budget": 0}
    assert C.reasoning_settings({"THINKING_BUDGET": "не число"})["budget"] == 0


def test_extra_body_fields_json(monkeypatch):
    """Свои поля тела (LLM_EXTRA_BODY_JSON) — JSON-объект, едет как есть.

    Битый JSON и не-объект запрос не ломают: {} и предупреждение в лог —
    стадия уходит с обычным запросом."""
    import logging
    log = logging.getLogger("test.extra.body")
    log.addHandler(logging.NullHandler())
    monkeypatch.delenv(C.EXTRA_BODY_ENV_KEY, raising=False)
    assert C.extra_body_fields({}, log) == {}
    assert C.extra_body_fields(
        {"LLM_EXTRA_BODY_JSON": '{"top_k": 5, "seed": 1}'}, log) == {
        "top_k": 5, "seed": 1}
    assert C.extra_body_fields({"LLM_EXTRA_BODY_JSON": "{top_k: 5}"}, log) == {}
    assert C.extra_body_fields({"LLM_EXTRA_BODY_JSON": "[1, 2]"}, log) == {}
    # окружение деплоя перекрывает файл
    monkeypatch.setenv(C.EXTRA_BODY_ENV_KEY, '{"a": 1}')
    assert C.extra_body_fields({"LLM_EXTRA_BODY_JSON": '{"b": 2}'}, log) == {
        "a": 1}


def test_reasoning_fields_bad_budget_is_zero():
    """Нечисловой бюджет — 0 (не отправляем), а не падение стадии."""
    assert C.reasoning_fields("on", "anthropic", "", "много") == (
        {"thinking": {"type": "enabled"}})
    assert C.reasoning_fields("on", "dashscope", "", None) == (
        {"enable_thinking": True})
    assert C.reasoning_fields("on", "dashscope", "", "-5") == (
        {"enable_thinking": True})

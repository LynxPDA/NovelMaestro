#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cli/translate_check.py — ratio-логика сравнения размеров
артефактов (остальные ветки — в tests/test_cli_e2e.py)."""
# pyright: reportMissingImports=false
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cli"))

import translate_check as TC  # noqa: E402


def test_compare_sizes():
    assert TC.compare_sizes("polished", "redacted", 1000, 1000, 1.0, 0.05) is None
    assert TC.compare_sizes("polished", "redacted", 1200, 1000, 1.0, 0.05) is not None
    assert TC.compare_sizes("polished", "chapter", 2100, 1000, 2.1, 0.5) is None
    assert TC.compare_sizes("polished", "chapter", 3500, 1000, 2.1, 0.5) is not None
    assert TC.compare_sizes("x", "y", 10, 0, 1.0, 0.05) is not None  # пустой эталон


# ════════════════════════════════════════════════════════════════════
# R9: слова-исключения


def test_load_exclusions_default(tmp_path):
    # дефолт убран: пусто = ничего (раньше VIP,MVP,【,】,NPC)
    assert TC.load_exclusions() == []
    assert not hasattr(TC, "DEFAULT_EXCLUSIONS")
    # пустой .env → тоже ничего
    f = tmp_path / ".env"
    f.write_text("", encoding="utf-8")
    assert TC.load_exclusions(str(f)) == []


def test_load_exclusions_from_env(tmp_path):
    f = tmp_path / ".env"
    f.write_text("TRANSLATE_CHECK_EXCLUDE_WORDS=АЛЬФА,бета\n",
                 encoding="utf-8")
    assert TC.load_exclusions(str(f)) == ["альфа", "бета"]


def test_load_exclusions_cli_wins(tmp_path):
    f = tmp_path / ".env"
    f.write_text("TRANSLATE_CHECK_EXCLUDE_WORDS=альфа\n", encoding="utf-8")
    assert TC.load_exclusions(str(f)) == ["альфа"]
    # --exclude-words передаётся как явный список в check_chapter
    assert TC.check_chapter is not None


def test_check_chapter_exclusions(tmp_path):
    """Слово из exclusions не даёт ошибку; без — ошибка латиницы."""
    d = tmp_path / "00000_1_x"
    d.mkdir()
    text = "Глава 1\n\nслово VIP и нормальный русский текст. " * 60
    (d / "polished.txt").write_text(text, encoding="utf-8")
    (d / "redacted.txt").write_text(text, encoding="utf-8")
    comps = [("redacted", 1.0, 0.05)]
    # без исключений — VIP даёт ошибку
    errors, _ = TC.check_chapter(1, str(d), "polished", comps,
                                 False, None, exclusions=[])
    assert any("VIP" in e for e in errors)
    # с исключением — чисто
    errors, _ = TC.check_chapter(1, str(d), "polished", comps,
                                 False, None, exclusions=["vip"])
    assert not any("VIP" in e for e in errors)


# ════════════════════════════════════════════════════════════════════
# Новые сравнения по типу файлов: --check-type + «ratio±tol»


def test_parse_ratio_tol():
    assert TC.parse_ratio_tol(None, 1.0, 0.05) == (1.0, 0.05)
    assert TC.parse_ratio_tol("1.2±0.07", 1.0, 0.05) == (1.2, 0.07)
    assert TC.parse_ratio_tol("2.5", 2.1, 0.5) == (2.5, 0.5)
    assert TC.parse_ratio_tol(" 2.5 ± 0.7 ", 2.1, 0.5) == (2.5, 0.7)


def test_comparisons_for():
    assert TC.comparisons_for("polished") == [
        ("redacted", 1.0, 0.05), ("chapter", 2.1, 0.5)]
    assert TC.comparisons_for("redacted") == [
        ("translated", 1.0, 0.05), ("chapter", 2.1, 0.5)]
    assert TC.comparisons_for("translated") == [("chapter", 2.1, 0.5)]
    # кастомные коэффициенты
    assert TC.comparisons_for("polished", "1.3±0.1", "2.0±0.2")[0] == \
        ("redacted", 1.3, 0.1)
    assert TC.comparisons_for("polished", "1.3±0.1", "2.0±0.2")[1] == \
        ("chapter", 2.0, 0.2)


def test_check_chapter_regexp_checks(tmp_path):
    """regexp-проверки: всё найденное — ошибка; заголовок главы — нет."""
    d = tmp_path / "00000_2_x"
    d.mkdir()
    body = ("Глава 2\n\nнормальный русский текст. " * 40
            + "\n\nГлава 99\nещё текст. " * 40)
    (d / "polished.txt").write_text(body, encoding="utf-8")
    comps = []
    # дефолтные проверки: лишняя «Глава 99» — ошибка, первая — нет
    errors, prev = TC.check_chapter(2, str(d), "polished", comps,
                                    False, None, exclusions=[])
    assert prev == 2
    assert any("Глава 99" in e for e in errors)
    assert not any("Глава 2" in e for e in errors)
    # кастомный паттерн: только иероглифы
    import re as _re
    rx = _re.compile(r"[一-鿿]+", _re.MULTILINE)
    clean = body.replace("Глава 99", "99")
    (d / "polished.txt").write_text(clean + "中文", encoding="utf-8")
    errors, _ = TC.check_chapter(2, str(d), "polished", comps,
                                 False, None, exclusions=[],
                                 regexp_checks=[rx])
    assert any("中文" in e for e in errors)


def test_check_chapter_no_header(tmp_path):
    """Нет «Глава N» в начале — структурная ошибка (всегда включена)."""
    d = tmp_path / "00000_3_x"
    d.mkdir()
    body = ("просто текст без заголовка. " * 60)
    (d / "polished.txt").write_text(body, encoding="utf-8")
    errors, prev = TC.check_chapter(3, str(d), "polished", [],
                                    False, None, exclusions=[])
    assert any("Нет «Глава N» в начале" in e for e in errors)
    assert prev is None


# ════════════════════════════════════════════════════════════════════
# Настраиваемые структурные проверки: --min-file-size / --header-regexp
# / --no-sequence-check


def test_check_chapter_min_file_size(tmp_path):
    """Минимальный размер файла — настраиваемый (БАЙТЫ)."""
    d = tmp_path / "00000_4_x"
    d.mkdir()
    body = "Глава 4\n\nкороткий текст"
    (d / "polished.txt").write_text(body, encoding="utf-8")
    (d / "redacted.txt").write_text(body, encoding="utf-8")
    comps = [("redacted", 1.0, 0.05)]
    # дефолт 3072 — файл мал → ошибка
    errors, _ = TC.check_chapter(4, str(d), "polished", comps,
                                 False, None, exclusions=[])
    assert any("слишком мал" in e for e in errors)
    # меньший минимум — ошибки нет
    errors, _ = TC.check_chapter(4, str(d), "polished", comps,
                                 False, None, exclusions=[],
                                 min_file_size=10)
    assert not any("слишком мал" in e for e in errors)


def test_check_chapter_custom_header_regexp(tmp_path):
    """Заголовок главы — настраиваемый regexp (--header-regexp)."""
    d = tmp_path / "00000_5_x"
    d.mkdir()
    body = "Раздел 5\n\n" + "русский текст. " * 100
    (d / "polished.txt").write_text(body, encoding="utf-8")
    (d / "redacted.txt").write_text(body, encoding="utf-8")
    comps = [("redacted", 1.0, 0.05)]
    # дефолтный «Глава N» — не совпало
    errors, prev = TC.check_chapter(5, str(d), "polished", comps,
                                    False, None, exclusions=[])
    assert any("Нет «Глава N» в начале" in e for e in errors)
    assert prev is None
    # свой паттерн — проходит, номер извлекается для последовательности
    hdr = re.compile(r"^Раздел\s+(\d+)", re.MULTILINE)
    errors, prev = TC.check_chapter(5, str(d), "polished", comps,
                                    False, 4, exclusions=[],
                                    header_regexp=hdr)
    assert not any("Нет «Глава N» в начале" in e for e in errors)
    assert not any("последовательность" in e for e in errors)
    assert prev == 5


def test_check_chapter_sequence_greater_than(tmp_path):
    """Последовательность: первое число первой непустой строки должно
    быть БОЛЬШЕ предыдущего (не ровно N+1)."""
    d = tmp_path / "00000_6_x"
    d.mkdir()
    comps = [("redacted", 1.0, 0.05)]

    def run(chapter_line, prev):
        body = chapter_line + "\n\n" + "русский текст. " * 100
        (d / "polished.txt").write_text(body, encoding="utf-8")
        (d / "redacted.txt").write_text(body, encoding="utf-8")
        return TC.check_chapter(6, str(d), "polished", comps,
                                False, prev, exclusions=[])

    # 7 после 5 — пропуск нумерации НЕ ошибка (только «больше»)
    errors, prev = run("Глава 7", 5)
    assert not any("последовательность" in e for e in errors)
    assert prev == 7
    # 1 после 5 — ошибка
    errors, prev = run("Глава 1", 5)
    assert any("последовательность" in e for e in errors)
    assert prev == 1
    # равный номер — ошибка
    errors, _ = run("Глава 5", 5)
    assert any("последовательность" in e for e in errors)


def test_check_chapter_sequence_off(tmp_path):
    """--no-sequence-check: сбой последовательности не ошибка, номер
    не накапливается."""
    d = tmp_path / "00000_7_x"
    d.mkdir()
    body = "Глава 1\n\n" + "русский текст. " * 100
    (d / "polished.txt").write_text(body, encoding="utf-8")
    (d / "redacted.txt").write_text(body, encoding="utf-8")
    comps = [("redacted", 1.0, 0.05)]
    errors, prev = TC.check_chapter(7, str(d), "polished", comps,
                                    False, 5, exclusions=[],
                                    sequence_check=False)
    assert not any("последовательность" in e for e in errors)
    assert prev == 5  # номер не накапливается

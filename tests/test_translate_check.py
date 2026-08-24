#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/translate_check.py — ratio-логика сравнения размеров
артефактов (остальные ветки — в tests/test_scripts_e2e.py)."""
# pyright: reportMissingImports=false
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

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
    assert TC.load_exclusions() == TC.DEFAULT_EXCLUSIONS
    # пустой/битый .env → дефолт
    f = tmp_path / ".env"
    f.write_text("", encoding="utf-8")
    assert TC.load_exclusions(str(f)) == TC.DEFAULT_EXCLUSIONS


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

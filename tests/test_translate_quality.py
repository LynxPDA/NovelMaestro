#!/usr/bin/env python3
"""Тесты стадии «Оценка перевода (LLM)» (cli/translate_quality.py):
промпт-тег, бюджет-обрезка до целых глав, подстановка плейсхолдеров,
md-отчёт и прогон main() с моками LLM. Без сети."""
# pyright: reportMissingImports=false
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cli"))

import translate_quality as TQ  # noqa: E402
from conftest import SilentLog  # noqa: E402

LOG = SilentLog()


# ══════════════════════════════════════════════════════════════════════
# Промпт: тег <prompt_assessment>
# ══════════════════════════════════════════════════════════════════════

def test_load_assessment_prompt_tagged(tmp_path):
    """Тег извлекается; комментарии вне тега игнорируются."""
    p = tmp_path / "p.txt"
    p.write_text(
        "# комментарий-справка\n"
        "<prompt_assessment>\nТы — критик.\n"
        "</prompt_assessment>\n"
        "# после тега\n",
        encoding="utf-8")
    got = TQ.load_assessment_prompt(str(p), LOG)
    assert got == "Ты — критик."
    assert "комментарий" not in got


def test_load_assessment_prompt_untagged_fallback(tmp_path):
    """Файл без тегов — целиком; None — встроенный дефолт."""
    p = tmp_path / "p.txt"
    p.write_text("просто промпт\n", encoding="utf-8")
    assert TQ.load_assessment_prompt(str(p), LOG) == "просто промпт"
    assert TQ.load_assessment_prompt(str(tmp_path / "нет.txt"), LOG) \
        == TQ.DEFAULT_PROMPT


# ══════════════════════════════════════════════════════════════════════
# Бюджет: целое количество глав
# ══════════════════════════════════════════════════════════════════════

def test_fit_budget_all_fits():
    ch = [(1, "ааа"), (2, "ббб"), (3, "ввв")]
    orig = {1: "А", 2: "Б", 3: "В"}
    kept, dropped = TQ.fit_budget(ch, orig, "промпт", budget=100)
    assert kept == ch and dropped == 0


def test_fit_budget_trims_to_whole_chapters():
    """Не влезает — первые N целых глав (оригинал+перевод), порядок
    сохраняется."""
    ch = [(1, "а" * 100), (2, "б" * 100), (3, "в" * 100)]
    orig = {1: "А" * 20, 2: "Б" * 20, 3: "В" * 20}
    kept, dropped = TQ.fit_budget(ch, orig, "промпт", budget=230)
    assert [n for n, _ in kept] == [1]
    assert dropped == 2


def test_fit_budget_counts_original_and_translation():
    """Размер главы = перевод + оригинал: 100+20=120, влезают 2."""
    ch = [(1, "а" * 100), (2, "б" * 100), (3, "в" * 100)]
    orig = {1: "А" * 20, 2: "Б" * 20, 3: "В" * 20}
    kept, dropped = TQ.fit_budget(ch, orig, "промпт", budget=260)
    assert [n for n, _ in kept] == [1, 2]
    assert dropped == 1


def test_fit_budget_prompt_too_big(tmp_path):
    """Промпт больше бюджета — ранний выход с ошибкой."""
    with pytest.raises(SystemExit):
        TQ.fit_budget([(1, "текст")], {}, "п" * 500, budget=10)


# ══════════════════════════════════════════════════════════════════════
# Подстановка плейсхолдеров
# ══════════════════════════════════════════════════════════════════════

def test_build_user_prompt_placeholders():
    tpl = "Оригинал: {original_text}\nПеревод: {translated_text}"
    out = TQ.build_user_prompt(tpl, "林水", "Линь Шуй")
    assert "Оригинал: 林水" in out
    assert "Перевод: Линь Шуй" in out


# ══════════════════════════════════════════════════════════════════════
# Отчёт
# ══════════════════════════════════════════════════════════════════════

def test_build_report_technical_header():
    meta = {
        "date": "2026-01-02 10:00",
        "range_requested": (1, 50),
        "range_included": (1, 20),
        "chapters": 20,
        "dropped": 30,
        "file_type": "polished",
        "budget": 200000,
        "packet_size": 120000,
        "model": "qwen3",
        "host": "http://h/v1",
        "prompt_file": "p.txt",
    }
    r = TQ.build_report(meta, "**9/10** — хорошо")
    assert "# Оценка качества перевода" in r
    assert "| Дата | 2026-01-02 10:00 |" in r
    assert "| Диапазон глав | 1 – 20 (из запрошенных 1 – 50; отсечено 30 глав бюджетом) |" in r
    assert "| Модель | qwen3 |" in r
    assert "**9/10** — хорошо" in r


def test_build_report_no_trimming():
    meta = {
        "date": "d", "range_requested": (1, 3),
        "range_included": (1, 3), "chapters": 3, "dropped": 0,
        "file_type": "redacted", "budget": 100000, "packet_size": 3000,
        "model": "m", "host": "h", "prompt_file": "",
    }
    r = TQ.build_report(meta, "текст")
    assert "| Диапазон глав | 1 – 3 |" in r
    assert "отсечено" not in r


# ══════════════════════════════════════════════════════════════════════
# main(): сбор глав, бюджет, LLM, отчёт
# ══════════════════════════════════════════════════════════════════════

def make_chapters(tmp_path, n=3):
    """Папки глав 00000_1_x… с chapter.txt + polished.txt."""
    ch = tmp_path / "chapters"
    for i in range(1, n + 1):
        d = ch / f"00000_{i}_x"
        d.mkdir(parents=True)
        (d / "chapter.txt").write_text(
            f"Глава {i}\n\nОригинал {i}.\n", encoding="utf-8")
        (d / "polished.txt").write_text(
            f"Глава {i}\n\nПеревод {i}.\n", encoding="utf-8")
    return ch


def test_main_e2e_report(tmp_path, monkeypatch):
    """Полный прогон: отчёт с технической шапкой и оценкой LLM."""
    make_chapters(tmp_path, 3)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(TQ, "determine_model", lambda *a, **k: "модель-х")
    monkeypatch.setattr(TQ, "llm_request",
                        lambda *a, **k: "**9/10** — отличный перевод")
    monkeypatch.setattr(sys, "argv", [
        "translate_quality.py", "--type", "polished",
        "--start", "1", "--end", "3",
        "--host", "http://h", "--model", "m",
        "--budget", "200000"])
    rc = TQ.main()
    assert rc == 0
    out = (tmp_path / "translation_quality_assessment.md")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "Оценка качества перевода" in text
    assert "| Диапазон глав | 1 – 3 |" in text
    assert "| Тип файлов глав | polished |" in text
    assert "**9/10** — отличный перевод" in text


def test_main_budget_trims(tmp_path, monkeypatch):
    """Малый бюджет — в отчёте отсечённые главы."""
    make_chapters(tmp_path, 3)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(TQ, "determine_model", lambda *a, **k: "м")
    monkeypatch.setattr(TQ, "llm_request", lambda *a, **k: "оценка")
    monkeypatch.setattr(sys, "argv", [
        "translate_quality.py", "--type", "polished",
        "--start", "1", "--end", "3",
        "--host", "http://h", "--model", "m",
        "--budget", "790"])
    rc = TQ.main()
    assert rc == 0
    text = (tmp_path / "translation_quality_assessment.md").read_text(
        encoding="utf-8")
    assert "отсечено" in text


def test_main_empty_llm_returns_1(tmp_path, monkeypatch):
    """Пустой ответ LLM — код 1, отчёт не пишется."""
    make_chapters(tmp_path, 1)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(TQ, "determine_model", lambda *a, **k: "m")
    monkeypatch.setattr(TQ, "llm_request", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", [
        "translate_quality.py", "--start", "1", "--end", "1",
        "--host", "http://h", "--model", "m"])
    assert TQ.main() == 1
    assert not (tmp_path / "translation_quality_assessment.md").exists()


def test_main_no_chapters(tmp_path, monkeypatch):
    """Пустая папка глав — код 1."""
    (tmp_path / "chapters").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "translate_quality.py", "--host", "http://h", "--model", "m"])
    assert TQ.main() == 1


def test_main_custom_output(tmp_path, monkeypatch):
    """--output задаёт имя отчёта; тег-промпт из файла."""
    make_chapters(tmp_path, 1)
    (tmp_path / "p.txt").write_text(
        "<prompt_assessment>оцени</prompt_assessment>\n",
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(TQ, "determine_model", lambda *a, **k: "m")
    monkeypatch.setattr(TQ, "llm_request", lambda *a, **k: "хорошо")
    monkeypatch.setattr(sys, "argv", [
        "translate_quality.py", "--start", "1", "--end", "1",
        "--prompt_file", "p.txt", "--output", "reports/my.md",
        "--host", "http://h", "--model", "m"])
    assert TQ.main() == 0
    assert (tmp_path / "reports" / "my.md").exists()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/tokenize.py — реестр счётчиков токенов: оценка + файлы tokenizers/.

Библиотека tokenizers опциональна: тесты покрывают оба пути — реальный
(если библиотека есть в окружении) и фолбэк (мок импорта).
"""
# pyright: reportMissingImports=false
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import tokenize as TK  # noqa: E402


@pytest.fixture
def isolated(monkeypatch):
    """Чистые кеши модуля + подменённая папка токенизаторов."""
    monkeypatch.setattr(TK, "_LOADED", {})
    monkeypatch.setattr(TK, "_LOAD_ERR", {})
    monkeypatch.setattr(TK, "_RESOLVED", {})
    monkeypatch.setattr(TK, "_CHECKED", {})
    monkeypatch.setattr(TK, "dir_path", lambda: Path("/nonexistent-tokenizers"))
    return TK


def _write_tok(path: Path, vocab: int = 4) -> None:
    """Минимальный валидный tokenizer.json (WordLevel, whitespace-сплит)."""
    doc = {
        "version": "1.0",
        "added_tokens": [],
        "normalizer": None,
        "pre_tokenizer": {"type": "Whitespace"},
        "post_processor": None,
        "decoder": None,
        "model": {
            "type": "WordLevel",
            "unk_token": "[UNK]",
            "vocab": {f"w{i}": i for i in range(vocab)} | {"[UNK]": vocab},
        },
    }
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_available_estimate_first(isolated):
    """estimate — всегда первый пункт; папки нет — файлов в списке нет."""
    av = isolated.available()
    assert av[0]["id"] == "estimate" and av[0]["ok"]
    assert all(a["id"] != "gemma-4-31b" for a in av)


def test_available_lists_files(isolated, tmp_path):
    isolated.dir_path = lambda: tmp_path  # noqa: ARG005
    _write_tok(tmp_path / "alpha.json")
    av = isolated.available()
    ids = [a["id"] for a in av]
    assert ids[0] == "estimate" and "alpha" in ids


def test_available_broken_file(isolated, tmp_path):
    """Битый файл: ok=False с причиной, запуск не падает."""
    isolated.dir_path = lambda: tmp_path  # noqa: ARG005
    (tmp_path / "bad.json").write_text("{битый", encoding="utf-8")
    av = isolated.available()
    bad = next(a for a in av if a["id"] == "bad")
    assert bad["ok"] is False and bad["error"]


def test_available_no_library(isolated, tmp_path, monkeypatch):
    """Библиотека tokenizers не установлена: файлы с ok=False и подсказкой."""
    isolated.dir_path = lambda: tmp_path  # noqa: ARG005
    _write_tok(tmp_path / "alpha.json")
    monkeypatch.setattr(TK, "_LIB", False)
    av = isolated.available()
    alpha = next(a for a in av if a["id"] == "alpha")
    assert alpha["ok"] is False
    assert "pip install tokenizers" in alpha["error"]


def test_resolve_estimate(isolated):
    """Пустой запрос и estimate — оценка без фолбэка."""
    assert isolated.resolve("") == ("estimate", "")
    assert isolated.resolve("estimate") == ("estimate", "")


def test_resolve_missing_file_falls_back(isolated, tmp_path, capsys):
    """Файла нет → оценка с предупреждением (один на процесс)."""
    isolated.dir_path = lambda: tmp_path  # noqa: ARG005
    tid, reason = isolated.resolve("absent")
    assert tid == "estimate" and reason
    out = capsys.readouterr().out
    assert "absent" in out
    # кеш решения: второй resolve молчит
    tid2, _ = isolated.resolve("absent")
    assert tid2 == "estimate"
    assert capsys.readouterr().out == ""


def test_resolve_broken_file_falls_back(isolated, tmp_path, capsys):
    isolated.dir_path = lambda: tmp_path  # noqa: ARG005
    (tmp_path / "bad.json").write_text("{битый", encoding="utf-8")
    tid, reason = isolated.resolve("bad")
    assert tid == "estimate" and reason


def test_resolve_logger_warning(isolated, tmp_path):
    """Логгер передан — предупреждение в лог, не в stdout."""
    isolated.dir_path = lambda: tmp_path  # noqa: ARG005
    msgs = []

    class Log:
        def warning(self, fmt, *args):
            msgs.append(fmt % args if args else fmt)

    isolated.resolve("absent", logger=Log())
    assert any("absent" in m for m in msgs)


def test_count_tokens_empty(isolated):
    assert isolated.count_tokens("", "estimate") == 0
    assert isolated.count_tokens("", "gemma-4-31b") == 0


def test_count_tokens_estimate(isolated):
    n = isolated.count_tokens("Привет, мир! Это тест.", "estimate")
    assert n > 0


def test_count_files_estimate(isolated, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("Привет, мир! Это тест.", encoding="utf-8")
    out = isolated.count_files([str(f)], "estimate")
    assert out[str(f)] == isolated.count_tokens("Привет, мир! Это тест.",
                                                "estimate")


def test_count_files_empty_and_unreadable(isolated, tmp_path):
    """Пустой файл — 0; нечитаемый — 0 с предупреждением, не падение."""
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    out = isolated.count_files([str(empty)], "estimate")
    assert out[str(empty)] == 0
    missing = tmp_path / "missing.txt"
    out = isolated.count_files([str(missing)], "estimate")
    assert out[str(missing)] == 0


def test_count_files_real_tokenizer(isolated, tmp_path):
    """Реальный счёт по файлу-токенизатору (WordLevel): пакет и кеш."""
    pytest.importorskip("tokenizers")
    isolated.dir_path = lambda: tmp_path  # noqa: ARG005
    _write_tok(tmp_path / "alpha.json")
    f = tmp_path / "text.txt"
    f.write_text("w1 w2 w3", encoding="utf-8")
    out = isolated.count_files([str(f)], "alpha")
    assert out[str(f)] == 3  # Whitespace + WordLevel: по токену на слово
    assert isolated.count_tokens("w1 w2 w3", "alpha") == 3


def test_count_files_batch(isolated, tmp_path):
    """Пакетный счёт: несколько файлов за один вызов, ключ = путь."""
    pytest.importorskip("tokenizers")
    isolated.dir_path = lambda: tmp_path  # noqa: ARG005
    _write_tok(tmp_path / "alpha.json")
    files = []
    for i in range(5):
        f = tmp_path / f"t{i}.txt"
        f.write_text(f"w{i % 4} w{(i + 1) % 4}", encoding="utf-8")
        files.append(str(f))
    out = isolated.count_files(files, "alpha")
    assert all(out[k] == 2 for k in files)

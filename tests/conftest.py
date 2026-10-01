#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Общие фикстуры и пути для всего тестового набора.

Хелперы, доступные всем тестам (импорт `from conftest import ...`):
- SilentLog — логгер-заглушка;
- make_ru_chapter_file — русский текст нужного размера;
- feed — эмуляция ввода через подмену input();
- fake_env — минимальный .env во временной папке.
"""
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "cli"):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)


class SilentLog:
    """Логгер-заглушка для функций, требующих logger."""

    handlers = ()  # для _flush_log(...) в скриптах

    def debug(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def log(self, *a, **k):
        pass


def make_ru_chapter_file(head: str, target_bytes: int, unit: str | None = None) -> str:
    """Русский текст (без латиницы/CJK) нужного размера в байтах (utf-8)."""
    unit = unit or "Тестовое предложение для проверки перевода. "
    text = head
    while len(text.encode("utf-8")) < target_bytes:
        text += unit
    return text


def feed(monkeypatch, *lines):
    """Подменяет input() очередью строк; исчерпание → EOFError."""
    it = iter(lines)

    def fake_input(prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", fake_input)


@pytest.fixture()
def srv_port() -> int:
    """Свободный TCP-порт на 127.0.0.1 (bind + close)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def isolated_env_layers(tmp_path, monkeypatch):
    """Тесты не видят .env машины разработчика: общий конфиг — отдельный
    временный файл, ключи LLM из окружения сняты. Иначе локальный .env
    меняет эффективные значения стадий прямо в прогоне."""
    shared = tmp_path / "shared.env"
    # HOST обязателен: без него стадия считает, что конфига нет, и падает
    # sys.exit — тест должен падать на assert, а не на окружении машины
    shared.write_text("HOST=http://127.0.0.1:9\n", encoding="utf-8")
    monkeypatch.setenv("WEB_ENV_FILE", str(shared))
    for key in ("HOST", "API_KEY", "MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def fake_env(tmp_path) -> str:
    """Минимальный .env (local-сервер) во временной папке; путь — строкой.
    Изолирует чтение .env от реального корневого .env."""
    env = tmp_path / "fake.env"
    env.write_text("HOST=http://testhost:9989\n"
                   "API_KEY=testkey\n"
                   "MODEL=testmodel\n", encoding="utf-8")
    return str(env)


def ensure_tmp(tmp_path):
    """Каталог tmp/ проекта (рабочие файлы) для тестов."""
    (tmp_path / "tmp").mkdir(exist_ok=True)

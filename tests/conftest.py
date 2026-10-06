#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Общие фикстуры и пути для всего тестового набора.

Хелперы, доступные всем тестам (импорт `from conftest import ...`):
- SilentLog — логгер-заглушка для функций, требующих logger;
- make_ru_chapter_file — русский текст нужного размера;
- feed — эмуляция ввода через подмену input();
- fake_env — минимальный .env во временной папке;
- http_send / http_request / json_payload — единый HTTP-транспорт
  web-тестов: одно соединение на запрос, тело — байтами и текстом на ответе
  (res.raw_bytes / res.raw_text), JSON-ответ — словарём;
- pid_alive — жив ли процесс на самом деле (зомби считается мёртвым)."""
import http.client
import json
import logging
import os
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "cli"):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)


class SilentLog(logging.Logger):
    """Логгер-заглушка для функций, требующих logger.

    Наследует logging.Logger: утиная типизация не проходила проверку типов
    (Stage(logger=...) объявлен как logging.Logger). Уровень выше CRITICAL и
    propagate=False — вывод никуда не идёт, а _flush_log(...) в скриптах
    видит штатный пустой список handlers."""

    def __init__(self, name: str = "silent") -> None:
        super().__init__(name=name, level=logging.CRITICAL + 1)
        self.propagate = False


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
    временный файл (своего .env у книги больше нет, он и есть единственный).
    HOST/MODEL заданы намеренно: без них стадия считает, что конфига нет, и
    падает sys.exit — тест должен падать на assert, а не на окружении машины.
    Остальные ключи реестра снимаются из окружения: эффективное значение
    должно быть либо из этого файла, либо дефолтом реестра."""
    shared = tmp_path / "shared.env"
    shared.write_text("HOST=http://127.0.0.1:9\nMODEL=testmodel\n",
                      encoding="utf-8")
    monkeypatch.setenv("WEB_ENV_FILE", str(shared))
    from core import settings as _S
    # профиль не выбран: тесты видят General, а не то, что выбрал браузер
    monkeypatch.delenv(_S.PROFILE_ENV, raising=False)
    for key in ("LLM_API_KEY", *{s.key for s in _S.SETTINGS}):
        monkeypatch.delenv(key, raising=False)
    return shared


def fake_env(tmp_path) -> str:
    """Минимальный .env (local-сервер) во временной папке; путь — строкой.
    Изолирует чтение .env от реального корневого .env."""
    env = tmp_path / "fake.env"
    env.write_text("HOST=http://testhost:9989\n"
                   "API_KEY=testkey\n"
                   "MODEL=testmodel\n", encoding="utf-8")
    return str(env)


def http_send(port: int, method: str, path: str,
              body: bytes | None = None,
              headers: dict | None = None) -> tuple[Any, bytes]:
    """Низовой запрос к 127.0.0.1:port; возвращает (response, тело).

    Единственный транспорт web-тестов: на нём построены http_request (JSON)
    и multipart-хелпер файловых тестов. Текст и байты тела дублируются на
    ответе (res.raw_text/res.raw_bytes): частью проверок нужен сам ответ.
    """
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body, headers or {})
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    # служебные поля ответа: pyright не знает, что тестовый транспорт вправе
    # повесить их на HTTPResponse (duck typing осознанный)
    res.raw_bytes = raw  # pyright: ignore
    try:
        res.raw_text = raw.decode("utf-8")  # pyright: ignore
    except UnicodeDecodeError:
        res.raw_text = ""  # pyright: ignore
    return res, raw


def json_payload(raw: bytes) -> dict:
    """Тело ответа как JSON-объект; текст и битый JSON — пустой словарь."""
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def http_request(port: int, method: str, path: str, body: Any = None,
                 cookie: str | None = None,
                 xrw: str | None = "fetch") -> tuple[Any, dict]:
    """JSON-запрос к серверу; возвращает (response, payload-словарь).

    cookie — заголовок сессии, xrw=None — запрос без X-Requested-With."""
    headers = {}
    if cookie:
        headers["Cookie"] = cookie
    if xrw is not None:
        headers["X-Requested-With"] = xrw
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    res, raw = http_send(port, method, path, data, headers)
    return res, json_payload(raw)


def ensure_tmp(tmp_path):
    """Каталог tmp/ проекта (рабочие файлы) для тестов."""
    (tmp_path / "tmp").mkdir(exist_ok=True)


def pid_alive(pid: int) -> bool:
    """Жив ли процесс: зомби — мёртв.

    `os.kill(pid, 0)` отвечает «да» и на зомби: убитый вместе с группой потомок
    остаётся записью в /proc, пока его не подчистят. Обычная система делает это
    сама; в контейнере PID 1 — обычный процесс, и «мёртвый» потомок выглядит
    живым бесконечно."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="ignore") as f:
            # «pid (comm) state …»: в comm встречаются скобки — берём после последней
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True   # нет /proc (macOS, Windows) — остаёмся на прежней проверке


def pytest_xdist_auto_num_workers(config):
    """Число воркеров для `-n auto`: из бюджета памяти, а не «сколько ядер».

    Жёсткий потолок, чтобы параллельный прогон не съедал машину (например,
    когда рядом живёт LLM-сервер): 5 ГБ по умолчанию, NOVELMAESTRO_TEST_BUDGET_MB
    перекрывает. Замер набора — десятки МБ на воркер; 256 МБ/воркер — запас.
    Явный `-n K` в командной строке этот хук не вызывает.
    """
    budget_mb = int(os.environ.get("NOVELMAESTRO_TEST_BUDGET_MB", "5120"))
    per_worker_mb = 256
    return max(1, min(budget_mb // per_worker_mb, os.cpu_count() or 2))

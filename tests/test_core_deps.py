#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты core/deps.py — реестра внешних зависимостей.

Проверяем то, ради чего реестр и нужен: у каждой роли свой кандидат (и одна
роль — один кандидат), опциональные роли прикрыты stdlib-фолбэком, обязательные
роли фолбэка не имеют, «деградация» отличается от «не на чем работать», а
подсказка ставит обязательные роли первыми. Установленное окружение мокается:
find_spec подменяется, чтобы все ветки были достижимы на любой машине.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core import deps  # noqa: E402

# все кандидаты реестра: на полном окружении установлены они все
ALL_MODULES = tuple(sorted({c["module"] for r in deps.ROLES
                           for c in r["candidates"] if c["module"]}))
ROLE_NAMES = [r["role"] for r in deps.ROLES]


def _finder(*present: str):
    """find_spec, который видит только перечисленные модули."""
    seen = set(present)

    def fake_find_spec(name, *args, **kwargs):
        return object() if name in seen else None
    return fake_find_spec


@pytest.fixture
def installed(monkeypatch):
    """Фиксатор набора «установлено»: installed("httpx", "dotenv")."""
    def _set(*present: str) -> None:
        monkeypatch.setattr(deps, "find_spec", _finder(*present))
    return _set


@pytest.fixture
def rows(installed):
    """Строки реестра на полном стеке."""
    installed(*ALL_MODULES)
    return {r["role"]: r for r in deps.status()}


# ════════════════════════════════════════════════════════════════════
# реестр ролей


def test_roles_cover_the_documented_set():
    """Реестр держит ровно те роли, о которых говорит AGENTS §2."""
    assert ROLE_NAMES == ["HTTP-транспорт LLM", ".env-конфигурация",
                         "поиск терминов по главам",
                         "история проектов (контрольные точки)",
                         "прогресс-бары CLI", "тесты"]


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_role_metadata(role):
    """У каждой роли есть владелец, флаг обязательности и кандидаты с полями."""
    row = next(r for r in deps.ROLES if r["role"] == role)
    assert set(row) == {"role", "where", "required", "candidates"}
    assert row["where"] and row["candidates"]
    for c in row["candidates"]:
        assert set(c) == {"pip", "module", "label", "note"}
        assert c["label"] and c["note"]


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_role_owner_file_exists(role):
    """Поле where указывает на реальный файл модуля-владельца."""
    row = next(r for r in deps.ROLES if r["role"] == role)
    path = row["where"].split(" ", 1)[0]
    assert (REPO / path).exists(), f"{role}: нет пути {path}"


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_role_candidates_are_unique(role):
    """Один кандидат на роль: двух «запасных» клиентов реестр не держит."""
    row = next(r for r in deps.ROLES if r["role"] == role)
    labels = [c["label"] for c in row["candidates"]]
    pips = [c["pip"] for c in row["candidates"] if c["pip"]]
    assert len(labels) == len(set(labels))
    assert len(pips) == len(set(pips))


def test_required_roles_have_no_fallback():
    """Обязательные роли закрыты одним стандартным кандидатом без фолбэка."""
    required = [r for r in deps.ROLES if r["required"]]
    assert [r["role"] for r in required] == ["HTTP-транспорт LLM",
                                            ".env-конфигурация",
                                            "история проектов (контрольные точки)"]
    for row in required:
        assert len(row["candidates"]) == 1
        assert row["candidates"][0]["module"], "обязательная роль не фолбэк"


@pytest.mark.parametrize("role", ["поиск терминов по главам",
                                  "прогресс-бары CLI"])
def test_runtime_roles_end_with_stdlib_fallback(role):
    """Опциональная роль рантайма кончается кандидатом без модуля — он всегда
    «есть», поэтому приложение остаётся полноценным без пакета."""
    row = next(r for r in deps.ROLES if r["role"] == role)
    last = row["candidates"][-1]
    assert last["module"] == "" and last["pip"] == ""
    assert row["candidates"][0]["pip"], "первый кандидат — настоящий пакет"


def test_dev_test_role_has_no_fallback():
    """Роль тестов фолбэка не имеет: их два pytest-пакета, третий не нужен."""
    row = next(r for r in deps.ROLES if r["role"] == "тесты")
    assert [c["pip"] for c in row["candidates"]] == ["pytest", "pytest-xdist"]
    assert all(c["module"] for c in row["candidates"])


def test_http_transport_has_exactly_one_candidate():
    """Запасного HTTP-клиента нет и не будет: кандидат один."""
    row = next(r for r in deps.ROLES if r["role"] == "HTTP-транспорт LLM")
    assert row["candidates"][0]["pip"] == "httpx"
    assert len(row["candidates"]) == 1


# ════════════════════════════════════════════════════════════════════
# _installed


@pytest.mark.parametrize("candidate,expected", [
    ({"module": ""}, True),
    ({"module": None}, True),
    ({"module": "json"}, True),
    ({"module": "нет_такого_модуля_депс"}, False),
])
def test_installed(candidate, expected):
    assert deps._installed(candidate) is expected


@pytest.mark.parametrize("exc", [ImportError, ValueError])
def test_installed_broken_environment_is_missing(monkeypatch, exc):
    """Битое окружение (find_spec бросает) — «не установлено», не падение."""
    def boom(name, *a, **kw):
        raise exc("сломанный окружение-путь")
    monkeypatch.setattr(deps, "find_spec", boom)
    assert deps._installed({"module": "httpx"}) is False


# ════════════════════════════════════════════════════════════════════
# status()


def test_status_rows_follow_registry_order(rows):
    assert [r["role"] for r in deps.status()] == ROLE_NAMES


def test_status_full_stack_is_not_degraded(rows):
    """На полном стеке активен первый кандидат роли, фолбэк не сработал."""
    for row in rows.values():
        assert row["active"] != "—"
        assert row["degraded"] is False
        assert row["pip_missing"] == []


def test_status_optional_role_falls_back(rows, installed):
    """Без pyahocorasick/tqdm роль живёт на фолбэке — это не ошибка."""
    installed("httpx", "dotenv", "pytest", "xdist")
    got = {r["role"]: r for r in deps.status()}
    terms = got["поиск терминов по главам"]
    assert terms["active"] == "regex" and terms["degraded"] is True
    assert terms["pip_missing"] == ["pyahocorasick"]
    prog = got["прогресс-бары CLI"]
    assert prog["active"] == "счётчик" and prog["degraded"] is True


def test_status_required_role_without_library(rows, installed):
    """Без httpx роль пуста: активен «—», деградации нет, пакет в подсказке."""
    installed("dotenv", "ahocorasick", "tqdm", "pytest", "xdist")
    got = {r["role"]: r for r in deps.status()}
    http = got["HTTP-транспорт LLM"]
    assert http["required"] is True
    assert http["active"] == "—" and http["installed"] == "—"
    assert http["degraded"] is False
    assert http["pip_missing"] == ["httpx"]


def test_status_partial_role_reports_only_missing(rows, installed):
    """Частично закрытая роль: активен установленный кандидат,
    остальное — в pip_missing."""
    installed("httpx", "dotenv", "ahocorasick", "tqdm", "pytest")
    got = {r["role"]: r for r in deps.status()}
    tests_role = got["тесты"]
    assert tests_role["active"] == "pytest"
    assert tests_role["installed"] == "pytest"
    assert tests_role["pip_missing"] == ["pytest-xdist"]


def test_status_tests_role_reports_both_runners(rows):
    """Роль тестов: pytest и pytest-xdist — два обязательных инструмента."""
    tests_role = rows["тесты"]
    assert tests_role["installed"] == "pytest, pytest-xdist"
    assert tests_role["degraded"] is False


# ════════════════════════════════════════════════════════════════════
# format_status() / missing_hint()


def test_format_status_one_line(rows):
    """Одна строка для лога сервера: роль: бэкенд, разделитель « · »."""
    line = deps.format_status()
    assert "\n" not in line
    assert line.count(" · ") == len(deps.ROLES) - 1
    assert "HTTP-транспорт LLM: httpx" in line
    assert ".env-конфигурация: python-dotenv" in line


def test_format_status_marks_fallback_backend(installed):
    installed("httpx", "dotenv", "pytest", "xdist")
    line = deps.format_status()
    assert "поиск терминов по главам: regex" in line
    assert "прогресс-бары CLI: счётчик" in line


def test_missing_hint_empty_on_full_stack(rows):
    assert deps.missing_hint() == ""


def test_missing_hint_lists_every_gap(installed):
    installed("dotenv", "ahocorasick", "dulwich")
    got = set(deps.missing_hint().split())
    assert got == {"httpx", "tqdm", "pytest", "pytest-xdist"}


def test_missing_hint_puts_required_first(installed):
    """Обязательные роли в подсказке идут раньше опциональных."""
    installed("ahocorasick", "tqdm", "pytest", "xdist")
    assert deps.missing_hint().startswith("httpx python-dotenv")


def test_missing_hint_lists_each_package_once(installed):
    installed()
    got = deps.missing_hint().split()
    assert len(got) == len(set(got))


# ════════════════════════════════════════════════════════════════════
# main()


def test_main_table_lists_every_role(rows, capsys):
    assert deps.main([]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "Стек зависимостей NovelMaestro"
    for name in ROLE_NAMES:
        assert name in out
    assert "Все доступные библиотеки установлены." in out


def test_main_marks_degraded_role(capsys, installed):
    installed("httpx", "dotenv", "pytest", "xdist")
    assert deps.main([]) == 0
    out = capsys.readouterr().out
    assert "(фолбэк)" in out
    assert "regex" in out and "Aho-Corasick" not in out


def test_main_warns_about_required_role(capsys, installed):
    installed("ahocorasick", "tqdm", "pytest", "xdist")
    assert deps.main([]) == 0
    out = capsys.readouterr().out
    assert "Обязательная роль без библиотеки" in out
    assert "pip install httpx" in out


def test_main_reports_optional_packages(capsys, installed):
    installed("httpx", "dotenv", "dulwich", "pytest", "xdist")
    assert deps.main([]) == 0
    out = capsys.readouterr().out
    assert "Опциональные библиотеки не установлены" in out
    assert "pip install pyahocorasick tqdm" in out


def test_main_accepts_argv(capsys, installed):
    """argv не используется, но пробрасывается без падения (модуль-точка)."""
    installed(*ALL_MODULES)
    assert deps.main(["--anything"]) == 0
    capsys.readouterr()

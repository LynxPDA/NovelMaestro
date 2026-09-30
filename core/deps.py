#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deps.py — реестр внешних зависимостей: что установлено, чем прикрыто.

Политика проекта (AGENTS §2): внешних библиотек минимум, каждая опциональна и
обязана иметь фолбэк — установка без интернета и на экзотической платформе не
должна ронять приложение. Реестр держит это решение в коде, а не только в
документации: для каждой роли перечислены кандидаты в порядке предпочтения и
stdlib-фолбэк, который остаётся активным, если библиотека не установлена.

Только чтение: модуль ничего не устанавливает и не меняет поведение — поведение
выбирают `core/transport.py` (транспорт) и `core/common.py` (поиск терминов).

Запуск:

    python3 -m core.deps     # таблица ролей/бэкендов + что доустановить
"""
from __future__ import annotations

from importlib.util import find_spec

# Кандидат роли: pip-имя, импортируемый модуль, подпись в отчёте, что даёт.
# Последний кандидат без модуля — встроенный фолбэк: он «установлен» всегда.
ROLES: tuple[dict, ...] = (
    {
        "role": "HTTP-транспорт LLM",
        "where": "core/transport.py",
        # без транспорта ходить в LLM нечем: единственный кандидат обязателен
        # (фолбэк-клиент был бы вторым адаптером и вторым набором тестов)
        "required": True,
        "candidates": (
            {"pip": "httpx", "module": "httpx", "label": "httpx",
             "note": "пул соединений, раздельные connect/write/read"},
        ),
    },
    {
        "role": "поиск терминов по главам",
        "where": "core/common.py (load_ner_data)",
        "required": False,
        "candidates": (
            {"pip": "pyahocorasick", "module": "ahocorasick",
             "label": "Aho-Corasick", "note": "мультпоиск одним проходом"},
            {"pip": "", "module": "", "label": "regex",
             "note": "stdlib-фолбэк: медленнее, результат тот же"},
        ),
    },
    {
        "role": "прогресс-бары CLI",
        "where": "cli/*.py",
        "required": True,
        "candidates": (
            {"pip": "tqdm", "module": "tqdm", "label": "tqdm",
             "note": "в web-режиме гасится (WEB_PROGRESS=1)"},
        ),
    },
    {
        "role": "тесты",
        "where": "tests/",
        "required": False,
        "candidates": (
            {"pip": "pytest", "module": "pytest", "label": "pytest",
             "note": "единственный гейт перед коммитом"},
        ),
    },
)


def _installed(candidate: dict) -> bool:
    """Установлен ли кандидат; кандидат без модуля — встроенный фолбэк."""
    module = candidate.get("module") or ""
    if not module:
        return True
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):  # битое окружение → считаем «нет»
        return False


def status() -> list[dict]:
    """По строке на роль: `{role, where, required, active, installed,
    degraded, pip_missing}`.

    `degraded` — роль работает на фолбэке (последний кандидат): это не ошибка,
    приложение полноценное, просто медленнее/проще.
    """
    rows: list[dict] = []
    for role in ROLES:
        cands = role["candidates"]
        got = [c for c in cands if _installed(c)]
        labels = [c["label"] for c in got]
        active = labels[0] if labels else ""
        rows.append({
            "role": role["role"],
            "where": role["where"],
            "required": bool(role.get("required")),
            "active": active or "—",
            "installed": ", ".join(labels) or "—",
            "degraded": bool(active) and active == cands[-1]["label"]
            and len(cands) > 1,
            "pip_missing": [c["pip"] for c in cands
                            if c["pip"] and c["label"] not in labels],
        })
    return rows


def format_status() -> str:
    """Одна строка для баннера/лога: «транспорт: httpx · термины: regex …»."""
    parts = [f"{r['role']}: {r['active']}" for r in status()]
    return " · ".join(parts)


def missing_hint() -> str:
    """Что доустановить одним pip-ом (пусто — ставить нечего)."""
    need: list[str] = []
    for r in status():
        for pip in r["pip_missing"]:
            if pip not in need:
                need.append(pip)
    return " ".join(need)


def main(argv: list[str] | None = None) -> int:
    """`python3 -m core.deps` — таблица активного стека зависимостей."""
    print("Стек зависимостей NovelMaestro")
    for row in status():
        mark = "обязательная" if row["required"] else "опциональная"
        note = " (фолбэк)" if row["degraded"] else ""
        print(f"  {row['role']:<26} {row['active']:<14} {mark}{note}"
              f"  [{row['where']}]")
    hint = missing_hint()
    if hint:
        print(f"\nОпциональные библиотеки не установлены: pip install {hint}")
    else:
        print("\nВсе доступные библиотеки установлены.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

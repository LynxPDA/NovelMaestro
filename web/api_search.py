#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_search.py — поиск по текстам проекта.

Один read-only роут: обычный проход по файлам книги (core/search.py), без
индексов и кешей. SPA рисует вкладку «Поиск»: группы файлов, число совпадений
и фрагменты с подсветкой. Клик по имени файла открывает его в редакторе, а
файл главы — во вкладке «Редактор» на нужной главе и с запросом в панели
поиска. Глоссарий здесь не ищется: у него своя вкладка со своим поиском.
"""
from __future__ import annotations

from web.server import ApiError, Router
from web.api_common import _project_ctx

# меньше двух символов — поиск только шумит; длиннее — бессмысленный обход
MIN_QUERY = 2
MAX_QUERY = 200


def _import_search(ctx: dict):
    """Ленивый импорт core.search (падает 500 с понятной причиной)."""
    try:
        from core import search as s
        return s
    except ImportError as exc:
        raise ApiError(500, f"core.search недоступен: {exc}")


def _context_param(ctx: dict, default: int, maximum: int) -> int:
    """Контекст (СИМВОЛЫ): пусто/мусор — дефолт, больше потолка — потолок."""
    raw = str(ctx["query"].get("context", "") or "").strip()
    if not raw:
        return default
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return default
    return max(0, min(value, maximum))


def _search_get(ctx: dict) -> dict:
    """Поиск по проекту (GET /api/search).

    query: project=sec/name, q — строка, scope — ключи групп через запятую
    (пусто — дефолтные), context — СИМВОЛЫ до и после совпадения (0–300),
    case=1 — без учёта регистра. Пустой q — только реестр групп.
    Совпадений возвращается столько, сколько есть: лимитов прогона нет."""
    s = _import_search(ctx)
    pdir, _section, _name = _project_ctx(ctx)
    query = str(ctx["query"].get("q") or "").strip()
    scopes = tuple(x.strip() for x in
                   str(ctx["query"].get("scope") or "").split(",")
                   if x.strip())
    if query and len(query) < MIN_QUERY:
        raise ApiError(400, f"Запрос короче {MIN_QUERY} символов")
    bad = [x for x in scopes if x not in s.GROUP_IDS]
    if bad:
        raise ApiError(400, f"Неизвестная группа поиска: {', '.join(bad)}")
    res = s.search_project(
        pdir, query[:MAX_QUERY], scopes or None,
        context=_context_param(ctx, s.DEFAULT_CONTEXT, s.MAX_CONTEXT),
        case_sensitive=str(ctx["query"].get("case") or "") in ("1", "true"),
    )
    res["ok"] = True
    return res


def _register_search(router: Router) -> None:
    """Роуты поиска (регистрация — в web/api.py)."""
    router.add("GET", "/api/search", _search_get)

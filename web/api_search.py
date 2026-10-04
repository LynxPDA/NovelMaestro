#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_search.py — поиск по текстам проекта.

Один read-only роут: ordinary scan по файлам книги (core/search.py), без
индексов и кешей. SPA рисует的结果 вкладкой «Поиск»: группы файлов, число
совпадений и фрагменты с подсветкой; из фрагмента — переход в «Главы» или
«Редактор» на нужной главе.
"""
from __future__ import annotations

from web.server import ApiError, Router
from web.api_common import _project_ctx, _repo_root

# меньше двух символов — поиск только шумит; длиннее — бессмысленный обход
MIN_QUERY = 2
MAX_QUERY = 200
# лимиты прогона (совпадений на файл и всего) и ширина контекста (СИМВОЛЫ)
MAX_PER_FILE = 20
MAX_TOTAL = 500
CONTEXT_MAX = 300


def _import_search(ctx: dict):
    """Ленивый импорт core.search (падает 500 с понятной причиной)."""
    try:
        from core import search as s
        return s
    except ImportError as exc:
        raise ApiError(500, f"core.search недоступен: {exc}")


def _int_param(ctx: dict, key: str, default: int, maximum: int) -> int:
    """Числовой query-параметр: пусто/мусор — дефолт, вне диапазона — потолок."""
    raw = str(ctx["query"].get(key, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))


def _search_get(ctx: dict) -> dict:
    """Поиск по проекту (GET /api/search).

    query: project=sec/name, q — строка, scope — ключи групп через запятую
    (пусто — дефолтные), context — СИМВОЛЫ до и после совпадения,
    per_file/max_total — лимиты совпадений, case=1 — без учёта регистра.
    """
    s = _import_search(ctx)
    pdir, _section, _name = _project_ctx(ctx)
    query = str(ctx["query"].get("q") or "").strip()
    if len(query) >= MIN_QUERY:
        scopes = tuple(x.strip() for x in
                       str(ctx["query"].get("scope") or "").split(",")
                       if x.strip())
        bad = [x for x in scopes if x not in s.GROUP_IDS]
        if bad:
            raise ApiError(400, f"Неизвестная группа поиска: {', '.join(bad)}")
    elif query:
        raise ApiError(400, f"Запрос короче {MIN_QUERY} символов")
    else:
        return {"ok": True, "query": "", "scopes": list(s.DEFAULT_SCOPES),
                "labels": dict(s.GROUP_LABELS), "groups":
                [[g.id, g.label] for g in s.SEARCH_GROUPS],
                "files": [], "total": 0, "scanned": 0, "skipped": 0,
                "truncated": False}
    res = s.search_project(
        pdir, query[:MAX_QUERY], scopes or None,
        context=_int_param(ctx, "context", s.DEFAULT_CONTEXT, CONTEXT_MAX),
        max_per_file=_int_param(ctx, "per_file", MAX_PER_FILE, 200),
        max_total=_int_param(ctx, "max_total", MAX_TOTAL, 5000),
        case_sensitive=str(ctx["query"].get("case") or "") in ("1", "true"),
    )
    res["ok"] = True
    res["labels"] = dict(s.GROUP_LABELS)
    res["groups"] = [[g.id, g.label] for g in s.SEARCH_GROUPS]
    return res


def _register_search(router: Router) -> None:
    """Роуты поиска (регистрация — в web/api.py)."""
    router.add("GET", "/api/search", _search_get)

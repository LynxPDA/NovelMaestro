#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_glossary.py — глоссарий и его проверки (M7): ner.json, экспорт,
review-файлы ner_check и translate_check_llm и их применение.
"""
from __future__ import annotations

import logging
import unicodedata
from pathlib import Path
from web.jobs import JobManager
from web.server import ApiError, Router
from web.api_common import log, NER_TEXT_LIMIT
from web.api_assets import (
    _cover_delete,
    _cover_get,
    _cover_put,
    _notes_get,
    _notes_put,
)
from web.api_common import _import_common, _project_ctx
from web.api_env import (
    _env_get,
    _env_put,
    _metadata_get,
    _metadata_put,
    _prompts_delete,
    _prompts_get,
    _prompts_list,
    _prompts_put,
    _prompts_template,
)
from web.api_stage import _jobs_start


def _ner_get(ctx: dict) -> dict:
    """Глоссарий (GET /api/ner?project=): total + by_type + items.

    Файл > 10 МБ — отдаём флаг too_large (скачивание файлом)."""
    pdir, _section, _name = _project_ctx(ctx)
    ner = pdir / "ner.json"
    if not ner.is_file():
        return {"ok": True, "exists": False, "total": 0,
                "by_type": {}, "items": []}
    size = ner.stat().st_size
    if size > NER_TEXT_LIMIT:
        return {"ok": True, "exists": True, "too_large": True, "size": size}
    try:
        import json as _json
        items = _json.loads(ner.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        raise ApiError(500, f"ner.json не читается: {exc}")
    if not isinstance(items, list):
        raise ApiError(500, "ner.json: ожидался список терминов")
    by_type: dict[str, int] = {}
    for it in items:
        t = it.get("type") or "?"
        by_type[t] = by_type.get(t, 0) + 1
    return {"ok": True, "exists": True, "total": len(items),
            "by_type": by_type, "items": items}


def _ner_put(ctx: dict) -> dict:
    """Сохранить глоссарий (PUT /api/ner {project, items})."""
    common = _import_common(ctx)
    pdir, _section, _name = _project_ctx(ctx)
    items = ctx["body"].get("items")
    if not isinstance(items, list):
        raise ApiError(400, "Поле items: список терминов")
    import json as _json
    text = _json.dumps(items, ensure_ascii=False, indent=2)
    common.atomic_write(pdir / "ner.json",
                        unicodedata.normalize("NFC", text))
    return {"ok": True, "total": len(items)}


def _ner_export(ctx: dict) -> dict:
    """Экспорт глоссария для анализа (GET /api/ner/export?project=&format=…).

    format=json  → полные записи JSON;
    format=text  → JSONL по записи на строку (format_ner_record);
    format=names → имена по полу (женские/мужские).
    Общие фильтры: count_threshold, types.
    Возвращает {ok, name, content} — фронт скачивает файлом.
    """
    common = _import_common(ctx)
    pdir, _section, _name = _project_ctx(ctx)
    ner = pdir / "ner.json"
    if not ner.is_file():
        raise ApiError(404, "ner.json не найден")
    try:
        import json as _json
        items = _json.loads(ner.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        raise ApiError(500, f"ner.json не читается: {exc}")
    if not isinstance(items, list):
        raise ApiError(500, "ner.json: ожидался список терминов")

    q = ctx["query"]
    fmt = q.get("format", "json")
    if fmt not in ("json", "text", "names"):
        raise ApiError(400, "format: json | text | names")

    def _int(name: str, default: int) -> int:
        v = q.get(name)
        if v in (None, ""):
            return default
        try:
            return int(v)
        except ValueError:
            raise ApiError(400, f"{name}: ожидалось число")

    def _csv(name: str) -> list[str]:
        return [s.strip() for s in q.get(name, "").split(",") if s.strip()]

    threshold = _int("count_threshold", 0)
    types = _csv("types")
    filtered = common.filter_ner_items(items, threshold, types)
    if not filtered:
        raise ApiError(400, "Нет записей, подходящих под критерии")

    if fmt == "json":
        content = _json.dumps(filtered, ensure_ascii=False, indent=2) + "\n"
        return {"ok": True, "name": "ner_export.json", "content": content,
                "total": len(filtered)}
    if fmt == "text":
        # JSONL — по одной записи на строку (тот же формат, что в
        # промптах: format_ner_record)
        content = "\n".join(
            _json.dumps(common.format_ner_record(item),
                        ensure_ascii=False)
            for item in filtered) + "\n"
        return {"ok": True, "name": "ner_analysis.jsonl",
                "content": content, "total": len(filtered)}
    # names: имена по полу
    female_types = _csv("female_types") or ["Person (female)"]
    male_types = _csv("male_types") or ["Person (male)"]
    female, male = [], []
    for item in filtered:
        t = item.get("type", "")
        tr = item.get("translation", "")
        if t in female_types:
            female.append(tr)
        elif t in male_types:
            male.append(tr)
    content = ("=== ЖЕНСКИЕ ИМЕНА ===\n"
               + ("\n".join(female) if female else "Нет данных")
               + "\n\n=== МУЖСКИЕ ИМЕНА ===\n"
               + ("\n".join(male) if male else "Нет данных") + "\n")
    return {"ok": True, "name": "ner_names.txt", "content": content,
            "total": len(filtered)}


def _review_file(ctx: dict, fname: str) -> Path:
    """Файл review внутри tmp/ проекта (tmp/ner_review.json /
    tmp/translate_check_llm_review.json — рабочие файлы)."""
    pdir, _section, _name = _project_ctx(ctx)
    return pdir / "tmp" / fname


def _review_get(ctx: dict, fname: str) -> dict:
    """Чтение review-файла: JSON pretty, отсутствующий — пустой."""
    p = _review_file(ctx, fname)
    if not p.is_file():
        return {"ok": True, "exists": False, "content": ""}
    text = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix == ".json":
        try:
            import json as _json
            text = _json.dumps(_json.loads(text), ensure_ascii=False, indent=2)
        except (ValueError, TypeError):
            log.debug("review-файл невалиден, отдаём как есть: %s", fname)
    return {"ok": True, "exists": True, "content": text,
            "size": p.stat().st_size}


def _review_put(ctx: dict, fname: str) -> dict:
    """Запись review-файла (PUT, контент в body)."""
    common = _import_common(ctx)
    content = ctx["body"].get("content")
    if content is None:
        raise ApiError(400, "Поле content обязательно")
    p = _review_file(ctx, fname)
    common.atomic_write(p, unicodedata.normalize("NFC", str(content)))
    return {"ok": True, "exists": True}


def _review_apply(ctx: dict, action: str) -> dict:
    """Применение review-правок: запуск стадии n/5 с --apply.

    POST /api/{ner|translate_check_llm}/review/apply
    {project, dry_run?} → JobManager
    (subprocess ner_check.py/translate_check_llm.py --apply
    [--dry-run])."""
    body = ctx["body"]
    project = (body.get("project") or "").strip()
    if "/" not in project:
        raise ApiError(400, "Параметр project=sec/name обязателен")
    dry = bool(body.get("dry_run", False))
    params: dict = {"apply": True, "dry_run": dry}
    if body.get("no_bak"):
        params["no_bak"] = True
    if action == "translate_check_llm":
        params["type"] = body.get("type") or "polished"
    ctx["body"] = {"action": action, "project": project, "params": params}
    # маркер: флаги применения собираются только этим путём («Проверка»)
    ctx["review_apply"] = True
    return _jobs_start(ctx)


def _ner_review_apply(ctx: dict) -> dict:
    """POST /api/ner/review/apply → ner_check.py --apply."""
    return _review_apply(ctx, "ner_check")


def _tcl_review_apply(ctx: dict) -> dict:
    """POST /api/translate_check_llm/review/apply →
    translate_check_llm.py --apply."""
    return _review_apply(ctx, "translate_check_llm")


def _register_m7(router: Router) -> None:
    router.add("GET", "/api/ner", _ner_get)
    router.add("GET", "/api/ner/export", _ner_export)
    router.add("PUT", "/api/ner", _ner_put)
    router.add("GET", "/api/ner/review", lambda ctx: _review_get(ctx, "ner_review.json"))
    router.add("PUT", "/api/ner/review", lambda ctx: _review_put(ctx, "ner_review.json"))
    router.add("POST", "/api/ner/review/apply", _ner_review_apply)
    router.add("GET", "/api/translate_check_llm/review", lambda ctx: _review_get(ctx, "translate_check_llm_review.json"))
    router.add("PUT", "/api/translate_check_llm/review", lambda ctx: _review_put(ctx, "translate_check_llm_review.json"))
    router.add("POST", "/api/translate_check_llm/review/apply", _tcl_review_apply)
    router.add("GET", "/api/notes", _notes_get)
    router.add("PUT", "/api/notes", _notes_put)
    # общий .env — один редактор на вкладке «Настройки»; собственный файл
    # книги через API не отдаётся (его пересобирают формы «Запусков»)
    router.add("GET", "/api/env", _env_get)
    router.add("PUT", "/api/env", _env_put)
    router.add("GET", "/api/prompts", _prompts_list)
    router.add("GET", "/api/prompts/{name}", _prompts_get)
    router.add("PUT", "/api/prompts/{name}", _prompts_put)
    router.add("DELETE", "/api/prompts/{name}", _prompts_delete)
    router.add("GET", "/api/prompts/{name}/template", _prompts_template)
    router.add("GET", "/api/metadata", _metadata_get)
    router.add("PUT", "/api/metadata", _metadata_put)
    router.add("GET", "/api/cover", _cover_get)
    router.add("PUT", "/api/cover", _cover_put)
    router.add("DELETE", "/api/cover", _cover_delete)


# ════════════════════════════════════════════════════════════════════
# Регистрация роутов
# ════════════════════════════════════════════════════════════════════
# Отчёты translate_check (W7)
# ════════════════════════════════════════════════════════════════════

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_env.py — конфигурация: страница «Настройки», промпты и metadata проекта.

Значения настроек живут в одном общем .env, но API отдаёт и принимает их
БЛОКАМИ РЕЕСТРА (core/settings.py), а не текстом файла: метки, типы, варианты
и дефолты описаны там же, и чужой ключ в файл не попадает. Собственный .env
книги из модели убран: поля запусков, изменённые для одной книги, — рабочее
состояние браузера (localStorage), а не второй конфигурационный файл.
"""
from __future__ import annotations

import logging
import os
import unicodedata
from pathlib import Path
from web.server import ApiError
from core import settings as core_settings
from web.server import Router
from web.api_common import (
    log,
    _import_common,
    _project_ctx,
    _repo_root,
    _resolve_project_path,
)


def _settings_get(ctx: dict) -> dict:
    """Страница «Настройки» (GET /api/settings): реестр блоками и значения.

    Ответ — subvкладки → блоки → поля (метки, типы, варианты, подсказки) с
    эффективными значениями. env_wins — ключи, которые заданы переменными
    окружения процесса: они перекрывают файл (канон §7), и правка в
    интерфейсе их не применит, пока не убрано окружение.
    """
    path = core_settings.env_file()
    return {"ok": True,
            "path": str(path) if path else "",
            "exists": bool(path) and Path(path).is_file(),
            "groups": core_settings.groups_payload(),
            "env_wins": [s.key for s in core_settings.SETTINGS
                         if os.environ.get(s.key, "").strip()]}


def _settings_put(ctx: dict) -> dict:
    """Сохранить настройки (PUT /api/settings {values: {КЛЮЧ: значение}}).

    Ключи — только имена реестра: чужой ключ отклоняется, а не дописывается в
    файл, иначе «одно место истины» распалось бы снова. Значения сливаются с
    тем, что уже лежит в файле (PAGE PUT не должен терять незапрошенные
    ключи); пустое значение снимает ключ, без ключей файл удаляется.
    """
    values = ctx["body"].get("values")
    if not isinstance(values, dict):
        raise ApiError(400, "Поле values: {КЛЮЧ: значение}")
    clean: dict = {}
    for key, value in values.items():
        k = str(key).strip()
        setting = core_settings.BY_KEY.get(k)
        if setting is None:
            raise ApiError(400, f"Неизвестный ключ настройки: {k!r}")
        if setting.secret and str(value or "").strip() == "••••":
            continue  # приехала маска вместо значения — ключ не трогаем
        clean[k] = value
    merged = dict(core_settings.file_values())
    merged.update(clean)
    try:
        stored = core_settings.write_values(merged)
    except RuntimeError as exc:
        raise ApiError(500, str(exc))
    path = core_settings.env_file()
    return {"ok": True, "keys": stored,
            "path": str(path) if path else "",
            "exists": bool(path) and Path(path).is_file(),
            "env_wins": [s.key for s in core_settings.SETTINGS
                         if os.environ.get(s.key, "").strip()],
            "groups": core_settings.groups_payload()}


def _register_settings(router: Router) -> None:
    """Роуты страницы «Настройки» (реестр, а не текст файла)."""
    router.add("GET", "/api/settings", _settings_get)
    router.add("PUT", "/api/settings", _settings_put)


def _prompts_list(ctx: dict) -> dict:
    """Список prompts/ проекта + доступные шаблоны (W4).

    Шаблоны — имена файлов из templates/*/prompts (уникальные, с пометкой
    from_template): фронт показывает их даже при пустом prompts/ проекта
    и умеет создавать промпт из шаблона.
    """
    pdir, _section, _name = _project_ctx(ctx)
    pr = pdir / "prompts"
    out = []
    if pr.is_dir():
        for f in sorted(pr.iterdir()):
            if not f.is_file():
                continue
            try:
                out.append({"name": f.name, "size": f.stat().st_size})
            except OSError as exc:
                log.debug("Промпт не читается %s: %s", f, exc)
    repo = _repo_root(ctx)
    tpl_root = repo / "templates"
    templates = []
    if tpl_root.is_dir():
        for tset in sorted(tpl_root.iterdir()):
            tdir = tset / "prompts"
            if not tset.is_dir() or not tdir.is_dir():
                continue
            try:
                for f in sorted(tdir.iterdir()):
                    if f.is_file() and f.name not in [t["name"] for t in templates]:
                        templates.append({"name": f.name, "set": tset.name,
                                          "size": f.stat().st_size})
            except OSError as exc:
                log.debug("Шаблоны не читаются %s: %s", tset.name, exc)
    return {"ok": True, "prompts": out, "templates": templates}


def _prompts_get(ctx: dict) -> dict:
    """Содержимое промпта (GET /api/prompts/{name}?project=)."""
    pdir, _section, _name = _project_ctx(ctx)
    name = ctx["params"]["name"]
    target = _resolve_project_path(ctx, pdir / "prompts", name)
    if not target.is_file():
        raise ApiError(404, f"Промпт не найден: {name}")
    return {"ok": True, "name": name,
            "content": target.read_text(encoding="utf-8", errors="replace")}


def _prompts_put(ctx: dict) -> dict:
    """Сохранить промпт (PUT /api/prompts/{name} {project, content})."""
    common = _import_common(ctx)
    pdir, _section, _name = _project_ctx(ctx)
    name = ctx["params"]["name"]
    content = ctx["body"].get("content")
    if content is None:
        raise ApiError(400, "Поле content обязательно")
    target = _resolve_project_path(ctx, pdir / "prompts", name)
    target.parent.mkdir(parents=True, exist_ok=True)
    common.atomic_write(target, unicodedata.normalize("NFC", str(content)))
    return {"ok": True, "name": name}


def _prompts_delete(ctx: dict) -> dict:
    """Удалить промпт проекта (DELETE /api/prompts/{name}?project=)."""
    pdir, _section, _name = _project_ctx(ctx)
    name = ctx["params"]["name"]
    target = _resolve_project_path(ctx, pdir / "prompts", name)
    if not target.is_file():
        raise ApiError(404, f"Промпт не найден: {name}")
    target.unlink()
    return {"ok": True, "name": name}


def _prompts_template(ctx: dict) -> dict:
    """Шаблоны промпта из templates/*/prompts (GET .../template)."""
    repo = _repo_root(ctx)
    name = ctx["params"]["name"]
    tpl_root = repo / "templates"
    out = []
    if tpl_root.is_dir():
        for tset in sorted(tpl_root.iterdir()):
            if not tset.is_dir():
                continue
            f = tset / "prompts" / name
            if f.is_file():
                try:
                    out.append({"set": tset.name, "name": name,
                                "content": f.read_text(
                                    encoding="utf-8", errors="replace")})
                except OSError as exc:
                    log.debug("Шаблон не читается %s: %s", f, exc)
    if not out:
        raise ApiError(404, f"Шаблон не найден: {name}")
    return {"ok": True, "name": name, "templates": out}


def _metadata_get(ctx: dict) -> dict:
    """source/metadata.yaml (GET /api/metadata?project=)."""
    pdir, _section, _name = _project_ctx(ctx)
    p = pdir / "source" / "metadata.yaml"
    if not p.is_file():
        return {"ok": True, "exists": False, "content": ""}
    return {"ok": True, "exists": True,
            "content": p.read_text(encoding="utf-8", errors="replace")}


def _metadata_put(ctx: dict) -> dict:
    """Сохранить source/metadata.yaml (PUT /api/metadata)."""
    common = _import_common(ctx)
    pdir, _section, _name = _project_ctx(ctx)
    content = ctx["body"].get("content")
    if content is None:
        raise ApiError(400, "Поле content обязательно")
    p = pdir / "source" / "metadata.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    common.atomic_write(p, unicodedata.normalize("NFC", str(content)))
    return {"ok": True, "exists": True}


# ══════════════════════════════════════════════════════════════
# Обложка (W6)
# ══════════════════════════════════════════════════════════════

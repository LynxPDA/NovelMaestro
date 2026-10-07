#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_env.py — конфигурация: страница «Настройки», профили LLM, промпты и metadata.

Значения настроек живут в одном общем .env, но API отдаёт и принимает их
БЛОКАМИ РЕЕСТРА (core/settings.py), а не текстом файла: метки, типы, варианты
и дефолты описаны там же, и чужой ключ в файл не попадает. Собственный .env
книги из модели убран: поля запусков, изменённые для одной книги, — рабочее
состояние браузера (localStorage), а не второй конфигурационный файл.

LLM-настройки отдаются профиль за профилем: General — значения общего .env,
остальные лежат рядом с ним в llm_profiles.json и переопределяют только то,
что в них задано; профиль выбирается в запусках проекта.
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


def _clean_values(values) -> dict:
    """Тело формы → {КЛЮЧ реестра: значение}; чужой ключ — 400, маска — пропуск.

    Ключи — только имена реестра: иначе «одно место истины» распалось бы
    снова. Значение-маска пароля — не значение: ключ остаётся как был.
    """
    if not isinstance(values, dict):
        raise ApiError(400, "Поле values: {КЛЮЧ: значение}")
    clean: dict = {}
    for key, value in values.items():
        k = str(key).strip()
        setting = core_settings.BY_KEY.get(k)
        if setting is None:
            raise ApiError(400, f"Неизвестный ключ настройки: {k!r}")
        if setting.secret and core_settings.is_mask(value):
            continue
        clean[k] = value
    return clean


def _env_wins() -> set:
    """Ключи, заданные переменными окружения процесса: они перекрывают файл
    (канон §7), и правка в интерфейсе их не применит."""
    return {s.key for s in core_settings.SETTINGS
            if os.environ.get(s.key, "").strip()}


def _settings_payload(profile: str = "") -> dict:
    """Общий ответ страницы: путь конфига, env_wins, блоки, профили."""
    path = core_settings.env_file()
    return {"ok": True,
            "path": str(path) if path else "",
            "exists": bool(path) and Path(path).is_file(),
            "profile": profile or core_settings.PROFILE_DEFAULT,
            "profile_file": core_settings.profiles_file(),
            "groups": core_settings.groups_payload(),
            "profiles": core_settings.profiles_payload(),
            "env_wins": sorted(_env_wins())}


def _settings_get(ctx: dict) -> dict:
    """Страница «Настройки» (GET /api/settings): реестр блоками и значения.

    Ответ — субвкладки → блоки → поля (метки, типы, варианты, подсказки) с
    эффективными значениями General. env_wins — ключи, которые заданы
    переменными окружения процесса: они перекрывают файл (канон §7), и правка
    в интерфейсе их не применит, пока не убрано окружение.
    """
    return _settings_payload()


def _settings_put(ctx: dict) -> dict:
    """Сохранить настройки (PUT /api/settings {profile, values}).

    Профиль пустой или general → всё в общий .env. Иначе — по владельцу
    ключа: LLM-ключи в профиль (в файле профилей живут только
    переопределения, остальное — General), остальные — в общий .env (профиль
    — набор только LLM-настроек: правка «Глоссария» при выбранном профиле
    обязана дожить до файла, а не молча выбрасываться).
    Ключи — только имена реестра; значения сливаются с уже сохранёнными (PUT
    одной вкладки не должен терять другие ключи); пустое значение снимает ключ.
    """
    body = ctx["body"]
    profile = str(body.get("profile") or "").strip()
    clean = _clean_values(body.get("values"))
    if profile in ("", core_settings.PROFILE_DEFAULT):
        merged = dict(core_settings.file_values())
        merged.update(clean)
        try:
            stored = core_settings.write_values(merged)
        except RuntimeError as exc:
            raise ApiError(500, str(exc))
    else:
        llm_keys = {s.key for s in core_settings.llm_settings()}
        llm_vals = {k: v for k, v in clean.items() if k in llm_keys}
        rest_vals = {k: v for k, v in clean.items() if k not in llm_keys}
        try:
            prof = core_settings.profile_save_values(profile, llm_vals)
        except ValueError as exc:
            raise ApiError(404, str(exc))
        stored = list(prof.get("values") or {})
        if rest_vals:
            merged = dict(core_settings.file_values())
            merged.update(rest_vals)
            try:
                stored = sorted(set(stored) | set(core_settings.write_values(merged)))
            except RuntimeError as exc:
                raise ApiError(500, str(exc))
    out = _settings_payload(profile)
    out["keys"] = stored
    return out


def _settings_profiles(ctx: dict) -> dict:
    """Профили LLM (POST /api/settings/profiles): create | rename | delete.

    Тело: {action, name?, id?}. id встроенного General не переименовывается и
    не удаляется: его значения — обычный общий .env.
    """
    body = ctx["body"]
    action = str(body.get("action") or "").strip()
    pid = str(body.get("id") or "").strip()
    name = str(body.get("name") or "").strip()
    try:
        if action == "create":
            core_settings.profile_create(name)
        elif action == "rename":
            core_settings.profile_rename(pid, name)
        elif action == "delete":
            if not core_settings.profile_delete(pid):
                raise ApiError(404, f"Профиль не найден: {pid}")
        else:
            raise ApiError(400, "Поле action: create | rename | delete")
    except ValueError as exc:
        raise ApiError(400, str(exc))
    return {"ok": True, "profiles": core_settings.profiles_payload()}


def _settings_check(ctx: dict) -> dict:
    """Проверить LLM-сервер (POST /api/settings/check {profile, values}).

    Проверяет ТО, чем сервер будет работать: значения формы (возможно, ещё не
    сохранённые) поверх общего .env и выбранного профиля. Ключ из окружения
    формой не перебивается, маска пароля сохранённый ключ не затирает. Сервер
    спрашивается коротким GET /models — без генерации и без токенов.
    """
    body = ctx["body"]
    profile = str(body.get("profile") or "").strip()
    wins = _env_wins()
    merged = core_settings.layered_values(profile)
    for key, value in _clean_values(body.get("values")).items():
        if key not in wins:
            merged[key] = core_settings.sanitize(core_settings.BY_KEY[key], value)
    host = str(merged.get("HOST") or "").strip()
    if not host:
        raise ApiError(400, "Сервер не задан: заполните «Сервер LLM»")
    common = _import_common(ctx)
    # отчёт — своим ключом: свой ok внутри ответа SPA значит «запрос не удался»,
    # а недоступный сервер — не неудача запроса, а его результат
    return {"ok": True,
            "profile": profile or core_settings.PROFILE_DEFAULT,
            "check": common.probe_server(host, str(merged.get("API_KEY") or ""),
                                        model=str(merged.get("MODEL") or ""))}


def _register_settings(router: Router) -> None:
    """Роуты страницы «Настройки» (реестр, а не текст файла)."""
    router.add("GET", "/api/settings", _settings_get)
    router.add("PUT", "/api/settings", _settings_put)
    router.add("POST", "/api/settings/check", _settings_check)
    router.add("POST", "/api/settings/profiles", _settings_profiles)


def _prompts_list(ctx: dict) -> dict:
    """Список prompts/ проекта + доступные шаблоны.

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
# Обложка
# ══════════════════════════════════════════════════════════════

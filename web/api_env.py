#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_env.py — конфигурация: общий (системный) .env и его ключи,
промпты и metadata.yaml проекта.

Собственный .env книги в web не показывается и руками не правится: его
пересобирает web-слой из форм «Запусков» (api_stage._persist_run_params),
а читается он только как слой конфига. Здесь — один редактор, общий файл.
"""
from __future__ import annotations

import logging
import os
import unicodedata
from pathlib import Path
from web.server import ApiError
from web.api_common import (
    log,
    _ENV_KEY_PREFIXES,
    _ENV_KEY_SUFFIXES,
    _ENV_KEY_RE,
)
from web.api_common import (
    _import_common,
    _project_ctx,
    _repo_root,
    _resolve_project_path,
    _sys_env_path,
)
from web.api_stage import _sanitize_env_value


def _env_path(ctx: dict) -> Path:
    """Файл .env для web-редактирования — общий (системный): WEB_ENV_FILE
    в Docker (projects/.env в томе), иначе корневой .env репо.
    Редактируется на вкладке «Настройки»; правки доходят до всех книг,
    поля которых они локально не меняли."""
    return _sys_env_path(ctx)


def _mask_env(text: str) -> str:
    """Маскирование значений: KEY=value → KEY=•••• (комментарии целы)."""
    out = []
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].rstrip()
            out.append(f"{key}=••••")
        else:
            out.append(line)
    return "\n".join(out)


def _env_no_auth(ctx: dict) -> bool:
    """Режим без аутентификации (W1): значения .env можно показывать."""
    auth_obj = ctx.get("auth")
    return bool(auth_obj is not None and getattr(auth_obj, "no_auth", False))


def _is_env_config_key(key: str) -> bool:
    """Похожа ли переменная окружения на ключ конфига NovelMaestro
    (для env_extra в GET /api/env: только имена наших ключей, без
    шелл-шума сессии — NVM_BIN, LS_COLORS, PI_* агента и т.п.)."""
    if key.startswith("PI_"):
        return False
    if key in ("HOST", "API_KEY", "MODEL", "TZ"):
        return True
    return key.startswith(_ENV_KEY_PREFIXES) \
        or key.endswith(_ENV_KEY_SUFFIXES)


def _env_get(ctx: dict) -> dict:
    """Общий .env (GET /api/env).

    W6: без аутентификации (доверенная LAN) значения ВИДИМЫ — отдаём
    целиком (content). При --auth — только ключи и маска ••••.
    Прозрачность слоёв: sources (ключ файла перекрыт os.environ — правка
    на «Настройках» не применится) и env_extra (ключи окружения, которых
    нет в файле: WEB_* из compose и т.п.).
    """
    scope = "global"
    p = _env_path(ctx)
    info = {"source": "shared"}
    if not p.is_file():
        return {"ok": True, "scope": scope, "exists": False,
                "masked": "", "keys": [], "visible": _env_no_auth(ctx),
                "values": {}, "sources": {}, "env_extra": [], **info}
    text = p.read_text(encoding="utf-8", errors="replace")
    keys = [line.split("=", 1)[0].strip()
            for line in text.splitlines()
            if "=" in line and not line.lstrip().startswith("#")]
    resp = {"ok": True, "scope": scope, "exists": True,
            "masked": _mask_env(text), "keys": keys,
            "visible": _env_no_auth(ctx), **info}
    if _env_no_auth(ctx):
        resp["content"] = text
    # Прозрачность слоёв (канон «окружение > файл»): какие ключи файла
    # перекрыты os.environ (правка в файле не применится) и какие ключи
    # есть в окружении, но не в файле (значения не отдаём — только имена)
    resp["sources"] = {k: "env" if os.environ.get(k, "").strip()
                       else "file" for k in keys}
    resp["env_extra"] = sorted(
        k for k in os.environ
        if k not in keys and os.environ.get(k, "").strip()
        and _is_env_config_key(k))
    # M9: значения НЕсекретных ключей (COMPILE_EPUB_COVER и т.п.) — для
    # предзаполнения селектов в «Настройках»; секреты (API_KEY/TOKEN/…)
    # не отдаются даже без аутентификации (маскировка их и так прячет)
    resp["values"] = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            up = key.upper()
            if not any(s in up for s in
                       ("API_KEY", "TOKEN", "PASSWORD", "SECRET")):
                resp["values"][key] = line.split("=", 1)[1].strip()
    return resp


def _env_put(ctx: dict) -> dict:
    """Запись общего .env (PUT /api/env {content | changes}).

    content — ПОЛНАЯ замена файла (создание с нуля / дублирование из
    шаблона); changes: {KEY: value} — точечная замена
    (пустое значение — удалить строку ^KEY=, комментарии не трогаем).
    Значения в ответ не возвращаются."""
    common = _import_common(ctx)
    body = ctx["body"]
    scope = "global"
    p = _env_path(ctx)
    if "content" in body:
        if not isinstance(body["content"], str):
            raise ApiError(400, "Поле content: строка")
        common.atomic_write(p, unicodedata.normalize("NFC", body["content"]))
        keys = [line.split("=", 1)[0].strip()
                for line in body["content"].splitlines()
                if "=" in line and not line.lstrip().startswith("#")]
        return {"ok": True, "scope": scope, "keys": keys}
    changes = body.get("changes")
    if not isinstance(changes, dict):
        raise ApiError(400, "Поле changes: {KEY: value}")
    text = ""
    if p.is_file():
        text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    for key, value in (changes or {}).items():
        k = str(key).strip()
        # M4 (AUDIT): ключ — строго [A-Za-z0-9_] (нет '=', пробелов, '\n')
        if not _ENV_KEY_RE.match(k):
            raise ApiError(400, f"Некорректный ключ: {key!r}")
        # M4 (AUDIT): перевод строки в значении — инъекция новых ключей
        if "\n" in str(value or "") or "\r" in str(value or ""):
            raise ApiError(400, f"Значение ключа {k!r} не может содержать перевод строки")
        value = _sanitize_env_value(value)  # один санитайзер на всех (§7)
        replaced = False
        for i, line in enumerate(lines):
            if line.split("=", 1)[0].strip() == k:
                if value:
                    lines[i] = f"{k}={value}"
                else:
                    del lines[i]
                replaced = True
                break
        if not replaced and value:
            lines.append(f"{k}={value}")
    out = "\n".join(lines)
    if out and not out.endswith("\n"):
        out += "\n"
    common.atomic_write(p, out)
    keys = [line.split("=", 1)[0].strip()
            for line in lines
            if "=" in line and not line.lstrip().startswith("#")]
    return {"ok": True, "scope": scope, "keys": keys}


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

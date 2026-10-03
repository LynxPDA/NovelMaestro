#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_templates.py — вкладка «Шаблоны»: наборы, файлы, каталоги,
загрузка, скачивание и переименование.
"""
from __future__ import annotations

import copy
from pathlib import Path
from web.multipart import (
    MultipartError, extract_files, extract_value, iter_parts,
    parse_disposition,
)
from web.sandbox import SandboxError, resolve_path
from web.server import ApiError, Router
from web.api_common import log
from web.api_common import (
    _atomic_write_spool,
    _close_multipart_fields,
    _import_projects,
    _multipart_fields,
    _repo_root,
)


def _templates(ctx: dict) -> dict:
    """Наборы шаблонов (templates/*) с файлами набора.

    files — полное дерево набора (относительные пути со
    слэшами) — единый ответ для «создания проекта» и «Шаблонов».
    """
    prj = _import_projects(ctx)
    repo = _repo_root(ctx)
    sets = prj.list_template_sets(repo / "templates")
    out = []
    for s in sets:
        # ремонт скелета при чтении — инвариант prompts/+source/
        # гарантирован и для наборов, созданных ранее
        prj._ensure_template_skeleton(repo / "templates" / s)
        out.append({"name": s, "files": prj.templates_files(repo / "templates", s)})
    return {"ok": True, "templates": out}


# ════════════════════════════════════════════════════════════════════
# NER, review, конфиги, промпты
# ════════════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════════════
# Шаблоны (вкладка «Шаблоны»)
# ════════════════════════════════════════════════════════════════════
def _templates_root(ctx: dict) -> Path:
    """Корень шаблонов: templates/ репозитория."""
    return _repo_root(ctx) / "templates"


def _templates_create(ctx: dict) -> dict:
    """POST /api/templates — создать набор ({"name": ...})."""
    prj = _import_projects(ctx)
    name = prj.create_template_set(
        _templates_root(ctx), (ctx["body"].get("name") or "").strip())
    if not name:
        raise ApiError(400, "Недопустимое имя набора: General занят, "
                             "недопустимые символы или уже существует")
    return {"ok": True, "name": name}


def _templates_copy(ctx: dict) -> dict:
    """POST /api/templates/{set}/copy — копировать ({"dst": ...})."""
    prj = _import_projects(ctx)
    src = ctx["params"]["set"]
    dst = prj.copy_template_set(
        _templates_root(ctx), src, (ctx["body"].get("dst") or "").strip())
    if not dst:
        raise ApiError(400, "Нельзя скопировать: имя недопустимо "
                             "(General занят) или dst уже существует")
    return {"ok": True, "name": dst}


def _templates_delete(ctx: dict) -> dict:
    """DELETE /api/templates/{set} — удалить набор (General — 403)."""
    prj = _import_projects(ctx)
    name = ctx["params"]["set"]
    if name == prj.TEMPLATE_PROTECTED:
        raise ApiError(403, "General — системный набор, удаление запрещено")
    if not prj.delete_template_set(_templates_root(ctx), name):
        raise ApiError(404, f"Набор не найден: {name}")
    return {"ok": True, "name": name}


def _templates_file_get(ctx: dict) -> dict:
    """GET /api/templates/{set}/file?path=… — содержимое файла."""
    prj = _import_projects(ctx)
    name = ctx["params"]["set"]
    rel = ctx["query"].get("path", "")
    text = prj.read_template_file(_templates_root(ctx), name, rel)
    if text is None:
        raise ApiError(404, f"Файл не найден: {name}/{rel}")
    info = prj.template_file_info(_templates_root(ctx), name, rel) or {}
    return {"ok": True, "name": name, "path": rel, "content": text,
            "size": info.get("size", 0), "mtime": info.get("mtime", 0)}


def _templates_file_put(ctx: dict) -> dict:
    """PUT /api/templates/{set}/file — записать файл (path+content)."""
    prj = _import_projects(ctx)
    name = ctx["params"]["set"]
    if name == prj.TEMPLATE_PROTECTED:
        raise ApiError(403, "General — системный набор, изменение запрещено")
    body = ctx["body"]
    rel = (body.get("path") or "").strip()
    if not rel:
        raise ApiError(400, "path обязателен")
    err = prj.write_template_file(
        _templates_root(ctx), name, rel, body.get("content") or "")
    if err:
        code = 403 if "Каталоги" in err or "General" in err else \
            (404 if "не найден" in err or "Недопустимый" in err else 400)
        raise ApiError(code, f"{name}: {err}")
    return {"ok": True, "name": name, "path": rel}


def _templates_file_delete(ctx: dict) -> dict:
    """DELETE /api/templates/{set}/file?path=… — удалить файл.

    Каталоги неизменяемы  → 403."""
    prj = _import_projects(ctx)
    name = ctx["params"]["set"]
    if name == prj.TEMPLATE_PROTECTED:
        raise ApiError(403, "General — системный набор, изменение запрещено")
    rel = ctx["query"].get("path", "")
    err = prj.delete_template_file(_templates_root(ctx), name, rel)
    if err:
        code = 403 if "Каталоги" in err else \
            (404 if "не найден" in err or "Недопустимый" in err else 400)
        raise ApiError(code, f"{name}: {err}")
    return {"ok": True, "name": name, "path": rel}


def _templates_rename(ctx: dict) -> dict:
    """POST /api/templates/{set}/rename — переименовать/перенести файл.

    Body: {src, dst} — относительные пути внутри набора."""
    prj = _import_projects(ctx)
    name = ctx["params"]["set"]
    body = ctx["body"]
    src = (body.get("src") or "").strip()
    dst = (body.get("dst") or "").strip()
    if not src or not dst:
        raise ApiError(400, "src и dst обязательны")
    if name == prj.TEMPLATE_PROTECTED:
        raise ApiError(403, "General — системный набор, изменение запрещено")
    err = prj.move_template_file(_templates_root(ctx), name, src, dst)
    if err:
        code = 403 if "Каталоги" in err else \
            (404 if "не найден" in err or "Недопустимый" in err else 400)
        raise ApiError(code, f"{name}: {err}")
    return {"ok": True, "name": name, "src": src, "dst": dst}


def _templates_upload(ctx: dict) -> dict:
    """Загрузка файлов в набор (POST /api/templates/{set}/upload, multipart).

    Поля: files[] (несколько); dest — подпапка внутри набора (опц.).
    General — 403; файлы пишутся атомарно (tmp+replace); ошибка
    валидации — на диск не пишется ничего.
    """
    prj = _import_projects(ctx)
    name = ctx["params"]["set"]
    if name == prj.TEMPLATE_PROTECTED:
        raise ApiError(403, "General — системный набор, изменение запрещено")
    set_dir = _templates_root(ctx) / name
    if not set_dir.is_dir():
        raise ApiError(404, f"Набор не найден: {name}")
    fields = _multipart_fields(ctx)
    try:
        dest = extract_value(fields, "dest") or ""
        files = extract_files(fields)
        if not files:
            raise ApiError(400, "Нет файлов в запросе")
        saved = []
        for f in files:
            fname = f.get("filename") or ""
            if not fname or "\x00" in fname:
                continue
            rel = f"{dest}/{fname}" if dest else fname
            try:
                target = resolve_path(set_dir, rel)
            except SandboxError as exc:
                raise ApiError(400, str(exc))
            if not target.parent.is_dir():
                # каталоги в шаблонах не создаются даже неявно
                raise ApiError(400, f"Каталог не существует: {rel}")
            _atomic_write_spool(target, f["data"])
            saved.append(rel)
        return {"ok": True, "saved": saved}
    finally:
        _close_multipart_fields(fields)


def _templates_download(ctx: dict) -> dict:
    """Скачивание файла набора (GET /api/templates/{set}/download?path=)."""
    prj = _import_projects(ctx)
    name = ctx["params"]["set"]
    set_dir = _templates_root(ctx) / name
    if not set_dir.is_dir():
        raise ApiError(404, f"Набор не найден: {name}")
    rel = ctx["query"].get("path", "")
    try:
        target = resolve_path(set_dir, rel)
    except SandboxError as exc:
        raise ApiError(400, str(exc))
    if not target.is_file():
        raise ApiError(404, f"Файл не найден: {name}/{rel}")
    data = target.read_bytes()
    handler = ctx["handler"]
    from urllib.parse import quote
    handler._send(200, "application/octet-stream", data,
                  [("Content-Disposition",
                    f'attachment; filename="{quote(target.name)}"')])
    return {}  # ответ уже отправлен


def _templates_mkdir(ctx: dict) -> dict:
    """Создать пустой каталог в наборе (POST /api/templates/{set}/mkdir)."""
    prj = _import_projects(ctx)
    name = ctx["params"]["set"]
    rel = (ctx["body"].get("path") or "").strip()
    if not rel:
        raise ApiError(400, "path обязателен")
    err = prj.create_template_dir(_templates_root(ctx), name, rel)
    if err:
        # любые каталоги в шаблонах запрещены → всегда 403
        code = 403 if ("General" in err or "Каталоги" in err) else \
            (404 if "не найден" in err or "Недопустимый" in err else 400)
        raise ApiError(code, f"{name}: {err}")
    return {"ok": True, "name": name, "path": rel}


def _register_templates(router: Router) -> None:
    router.add("POST", "/api/templates", _templates_create)
    router.add("POST", "/api/templates/{set}/copy", _templates_copy)
    router.add("DELETE", "/api/templates/{set}", _templates_delete)
    router.add("GET", "/api/templates/{set}/file", _templates_file_get)
    router.add("PUT", "/api/templates/{set}/file", _templates_file_put)
    router.add("DELETE", "/api/templates/{set}/file", _templates_file_delete)
    router.add("POST", "/api/templates/{set}/rename", _templates_rename)
    router.add("POST", "/api/templates/{set}/upload", _templates_upload)
    router.add("GET", "/api/templates/{set}/download", _templates_download)
    router.add("POST", "/api/templates/{set}/mkdir", _templates_mkdir)

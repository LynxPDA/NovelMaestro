#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_files.py — файлы проекта: список, чтение, запись, каталоги,
переименование, удаление, загрузка и скачивание.
"""
from __future__ import annotations

import logging
import os
import shutil
import unicodedata
from pathlib import Path
from web.multipart import (
    MultipartError, extract_files, extract_value, iter_parts,
    parse_disposition,
)
from web.server import ApiError, Router
from web.api_common import log, UPLOAD_DIRS, FILE_TEXT_LIMIT, DIR_TREE_LIMIT
from web.api_common import (
    _atomic_write_spool,
    _close_multipart_fields,
    _import_common,
    _multipart_fields,
    _project_ctx,
    _resolve_project_path,
)


def _dir_tree(root: Path, cap: int = DIR_TREE_LIMIT) -> list[str]:
    """Все каталоги проекта относительно корня — для диалога переноса.

    Обход без перехода по симлинкам; срез на cap, чтобы диалог не разрастался
    на тысячу папок.
    """
    out: list[str] = []
    for dirpath, _dirnames, _files in os.walk(root, followlinks=False):
        rel = os.path.relpath(dirpath, root)
        if rel == os.curdir:
            continue
        out.append(rel.replace(os.sep, "/"))
        if len(out) >= cap:
            break
    return sorted(out)


def _files_listing(ctx: dict) -> dict:
    """Листинг папки проекта (GET /api/files?project=&path=).

    `dirs` — плоское дерево всех каталогов проекта (относительно корня):
    по нему SPA строит выбор папки назначения при переносе выделенного.
    """
    pdir, section, name = _project_ctx(ctx)
    rel = ctx["query"].get("path", "")
    target = _resolve_project_path(ctx, pdir, rel)
    if not target.is_dir():
        raise ApiError(404, "Папка не найдена")
    entries = []
    for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name)):
        try:
            st_p = p.stat()
            size = st_p.st_size if p.is_file() else 0
            mtime = int(st_p.st_mtime)
        except OSError:
            continue
        entries.append({
            "name": p.name,
            "dir": p.is_dir(),
            "size": size,
            "mtime": mtime,
        })
    return {"ok": True, "path": rel, "entries": entries,
            "dirs": _dir_tree(pdir)}


def _is_binary_bytes(data: bytes) -> bool:
    """NUL-снифф: бинарный файл не открываем как текст."""
    return b"\x00" in data[:8192]


def _file_read(ctx: dict) -> dict:
    """Чтение файла текстом (GET /api/file?project=&path=).

    JSON отдаётся pretty-print'ом; бинарные файлы — ошибка 400;
    файлы больше FILE_TEXT_LIMIT — 413 (предложить скачивание).
    Файла нет — пустой редактор (missing: true): сохранение создаст файл.
    """
    pdir, section, name = _project_ctx(ctx)
    rel = ctx["query"].get("path", "")
    target = _resolve_project_path(ctx, pdir, rel)
    if not target.is_file():
        if target.is_dir():
            raise ApiError(400, "Это каталог — открыть как текст нельзя")
        return {"ok": True, "path": rel, "content": "", "size": 0,
                "missing": True}
    size = target.stat().st_size
    if size > FILE_TEXT_LIMIT:
        raise ApiError(413, f"Файл {size} Б — слишком большой для редактора, "
                            "скачайте его через кнопку скачивания")
    raw = target.read_bytes()
    if _is_binary_bytes(raw):
        raise ApiError(400, "Бинарный файл — открыть как текст нельзя")
    text = raw.decode("utf-8", errors="replace")
    if target.suffix == ".json":
        try:
            import json as _json
            text = _json.dumps(_json.loads(text), ensure_ascii=False, indent=2)
        except (ValueError, TypeError):
            log.debug("JSON-файл невалиден, отдаём как есть: %s", rel)
    return {"ok": True, "path": rel, "content": text,
            "size": target.stat().st_size}


def _file_write(ctx: dict) -> dict:
    """Запись файла (PUT /api/file {project, path, content})."""
    common = _import_common(ctx)
    pdir, section, name = _project_ctx(ctx)
    rel = (ctx["body"].get("path") or "").strip()
    content = ctx["body"].get("content")
    if content is None:
        raise ApiError(400, "Поле content обязательно")
    target = _resolve_project_path(ctx, pdir, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = unicodedata.normalize("NFC", str(content))
    common.atomic_write(target, text)
    return {"ok": True, "path": rel, "size": len(text.encode("utf-8"))}


def _file_mkdir(ctx: dict) -> dict:
    """Создать каталог (POST /api/mkdir?project=&path=).

    «＋ Каталог» в «Файлы»; занято → 400, эскейп → 400.
    """
    pdir, _section, _name = _project_ctx(ctx)
    rel = (ctx["query"].get("path") or ctx["body"].get("path") or "").strip()
    if not rel:
        raise ApiError(400, "path обязателен")
    target = _resolve_project_path(ctx, pdir, rel)
    if target.exists():
        raise ApiError(400, f"Путь уже существует: {rel}")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ApiError(500, f"Не удалось создать каталог: {exc}")
    return {"ok": True, "path": rel}


def _file_rename(ctx: dict) -> dict:
    """Переименовать файл ИЛИ каталог (POST /api/file/rename).

    Body: {project, path, new_name} — new_name только имя внутри той же
    папки (без слешей). Занято → 400, нет исходника → 404, эскейп → 400.
    """
    pdir, _section, _name = _project_ctx(ctx)
    rel = (ctx["body"].get("path") or "").strip()
    new_name = (ctx["body"].get("new_name") or "").strip()
    if not rel or not new_name:
        raise ApiError(400, "path и new_name обязательны")
    if ("/" in new_name or "\\" in new_name or "\x00" in new_name
            or new_name in (".", "..")):
        raise ApiError(400, "new_name: только имя внутри той же папки")
    src = _resolve_project_path(ctx, pdir, rel)
    if not src.exists():
        raise ApiError(404, f"Файл не найден: {rel}")
    parent_rel = rel.rpartition("/")[0]
    dst_rel = f"{parent_rel}/{new_name}" if parent_rel else new_name
    dst = _resolve_project_path(ctx, pdir, dst_rel)
    if dst.exists():
        raise ApiError(400, f"Путь назначения уже существует: {dst_rel}")
    try:
        src.replace(dst)
    except OSError as exc:
        raise ApiError(500, f"Не удалось переименовать: {exc}")
    return {"ok": True, "path": rel, "new_path": dst_rel}


def _file_copy(ctx: dict) -> dict:
    """Копировать файл ИЛИ каталог в ту же папку (POST /api/file/copy).

    Body: {project, path}. Имя копии — «Копия - <имя>»; если занято —
    «Копия - <имя> (2)», «(3)», … Каталог копируется рекурсивно.
    Нет исходника → 404, эскейп → 400.
    """
    pdir, _section, _name = _project_ctx(ctx)
    rel = (ctx["body"].get("path") or "").strip()
    if not rel:
        raise ApiError(400, "path обязателен")
    src = _resolve_project_path(ctx, pdir, rel)
    if not src.exists():
        raise ApiError(404, f"Файл не найден: {rel}")
    parent_rel = rel.rpartition("/")[0]
    stem = f"{parent_rel}/" if parent_rel else ""
    base = "Копия - " + src.name
    dst = _resolve_project_path(ctx, pdir, stem + base)
    if dst.exists():
        for i in range(2, 1000):
            candidate = f"{base} ({i})"
            dst = _resolve_project_path(ctx, pdir, stem + candidate)
            if not dst.exists():
                break
        else:
            raise ApiError(400, "Не удалось подобрать имя копии")
    try:
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    except OSError as exc:
        raise ApiError(500, f"Не удалось скопировать: {exc}")
    dst_rel = stem + dst.name
    return {"ok": True, "path": rel, "new_path": dst_rel}

def _file_delete(ctx: dict) -> dict:
    """Удаление файла ИЛИ каталога (DELETE /api/file?project=&path=).

    Каталог удаляется рекурсивно — поэтому удаление в SPA живёт в панели
    выделения (с подтверждением), а не на каждой строке списка.
    """
    pdir, section, name = _project_ctx(ctx)
    rel = ctx["query"].get("path", "")
    target = _resolve_project_path(ctx, pdir, rel)
    if not target.exists():
        raise ApiError(404, "Файл не найден")
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as exc:
        raise ApiError(500, f"Не удалось удалить: {exc}")
    return {"ok": True, "path": rel}


def _file_move(ctx: dict) -> dict:
    """Перенести выделенное в другую папку (POST /api/file/move).

    Body: {project, paths: [путь внутри проекта, …], dest: папка ("" — корень)}
    — один вызов на всю выделку. Найдено/занято/внутри себя — объект уходит
    в skipped, остальное переносится; каталог внутрь собственного подкаталога
    не переносится. Выход за проект — 400 (снимает _resolve_project_path).
    """
    pdir, _section, _name = _project_ctx(ctx)
    paths = ctx["body"].get("paths") or ctx["body"].get("path") or []
    if isinstance(paths, str):
        paths = [paths]
    dest_rel = str(ctx["body"].get("dest") or "").strip().strip("/")
    if not paths:
        raise ApiError(400, "Поле paths обязательно")
    dest = _resolve_project_path(ctx, pdir, dest_rel)
    if not dest.is_dir():
        raise ApiError(404, f"Каталог назначения не найден: {dest_rel or '/'}")
    moved: list[dict] = []
    skipped: list[dict] = []
    for rel in (str(p).strip() for p in paths):
        if not rel:
            continue
        src = _resolve_project_path(ctx, pdir, rel)
        if not src.exists():
            skipped.append({"path": rel, "reason": "не найдено"})
            continue
        if src.is_dir() and str(dest).startswith(f"{str(src)}{os.sep}"):
            skipped.append({"path": rel, "reason": "нельзя внутрь себя"})
            continue
        dst_rel = f"{dest_rel}/{src.name}" if dest_rel else src.name
        dst = _resolve_project_path(ctx, pdir, dst_rel)
        if dst.exists():
            skipped.append({"path": rel, "reason": "занято"})
            continue
        try:
            src.replace(dst)
        except OSError as exc:
            skipped.append({"path": rel, "reason": str(exc)})
            continue
        moved.append({"path": rel, "new_path": dst_rel})
    return {"ok": True, "moved": moved, "skipped": skipped}


def _file_upload(ctx: dict) -> dict:
    """Загрузка файлов (POST /api/upload, multipart).

    Поля: dest=source|chapters|prompts|images|tmp|вложенная chapters/…
    + files[] (несколько); пусто/отсутствует dest = корень проекта.
    Имена — только basename; лимит max_upload_mb на файл и на тело;
    файлы пишутся атомарно, при ошибке валидации не пишется ничего.
    """
    pdir, _section, _name = _project_ctx(ctx)
    fields = _multipart_fields(ctx)
    try:
        dest = extract_value(fields, "dest")
        # пусто = корень проекта (поля files с dir="" — ner_file, wiki
        # file); вложенные папки глав — для загрузки внутрь chapter-папок
        if dest and dest not in UPLOAD_DIRS \
                and not dest.startswith("chapters/"):
            raise ApiError(400, f"Папка назначения недопустима: {dest}")
        uploads = []
        for f in extract_files(fields):
            fname = f.get("filename") or ""
            if not fname or "\x00" in fname:
                continue
            if "/" in fname or "\\" in fname or fname in (".", ".."):
                raise ApiError(400, f"Недопустимое имя файла: {fname}")
            uploads.append((fname, f["data"]))
        if not uploads:
            raise ApiError(400, "Нет файлов в запросе")
        dest_dir = _resolve_project_path(ctx, pdir, dest)
        dest_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for fname, spool in uploads:
            target = _resolve_project_path(ctx, dest_dir, fname)
            _atomic_write_spool(target, spool)
            saved.append(f"{dest}/{fname}" if dest else fname)
        return {"ok": True, "saved": saved}
    finally:
        _close_multipart_fields(fields)


def _file_download(ctx: dict) -> dict:
    """Скачивание файла (GET /api/download?project=&path=&inline=1).

    inline=1 — без Content-Disposition (для предпросмотра картинок в SPA).
    """
    pdir, section, name = _project_ctx(ctx)
    rel = ctx["query"].get("path", "")
    target = _resolve_project_path(ctx, pdir, rel)
    if not target.is_file():
        raise ApiError(404, "Файл не найден")
    data = target.read_bytes()
    handler = ctx["handler"]
    if ctx["query"].get("inline"):
        ctype = "image/jpeg" if target.suffix.lower() in (".jpg", ".jpeg") \
            else "image/png" if target.suffix.lower() == ".png" \
            else "application/octet-stream"
        handler._send(200, ctype, data, cache="no-cache")
        return {}
    from urllib.parse import quote
    handler._send(200, "application/octet-stream", data,
                  [("Content-Disposition",
                    f'attachment; filename="{quote(target.name)}"')])
    return {}  # ответ уже отправлен


def _register_files(router: Router) -> None:
    router.add("GET", "/api/files", _files_listing)
    router.add("GET", "/api/file", _file_read)
    router.add("PUT", "/api/file", _file_write)
    router.add("DELETE", "/api/file", _file_delete)
    router.add("POST", "/api/mkdir", _file_mkdir)
    router.add("POST", "/api/file/rename", _file_rename)
    router.add("POST", "/api/file/copy", _file_copy)
    router.add("POST", "/api/file/move", _file_move)
    router.add("POST", "/api/upload", _file_upload)
    router.add("GET", "/api/download", _file_download)

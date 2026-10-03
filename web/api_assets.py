#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_assets.py — обложка проекта, логи стадии и отчёты
translate_check.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from web.server import ApiError, Router
from web import state as st
from web.api_common import (
    log,
    COVER_NAMES,
    COVER_MAX_BYTES,
    LOG_TAIL_LIMIT,
    CHECK_REPORT_LIMIT,
)
from web.api_common import (
    _import_common,
    _project_ctx,
    _projects_root,
    _resolve_project_path,
)


def _cover_file(pdir: Path) -> Path | None:
    """Существующий файл обложки в source/ (первый по приоритету имён)."""
    src = pdir / "source"
    if not src.is_dir():
        return None
    for n in COVER_NAMES:
        p = src / n
        if p.is_file():
            return p
    return None


def _cover_get(ctx: dict) -> dict:
    """Статус обложки (GET /api/cover?project=): имя/размер или ничего."""
    pdir, _section, _name = _project_ctx(ctx)
    p = _cover_file(pdir)
    if p is None:
        return {"ok": True, "exists": False}
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise ApiError(404, f"Обложка не читается: {exc.strerror}") from exc
    return {"ok": True, "exists": True, "name": p.name, "size": size,
            "path": f"source/{p.name}"}


def _cover_put(ctx: dict) -> dict:
    """Загрузить обложку (PUT /api/cover {project, content_base64, name}).

    Имя приводится к cover.<расширение>; допустимы jpg/png/jpeg (webp
    не принимаем: обложка идёт в EPUB/FB2, где webp не в спецификациях),
    лимит COVER_MAX_BYTES. Прежние обложки с другими расширениями
    удаляются (одна обложка — один файл).
    """
    import base64
    import binascii
    pdir, _section, _name = _project_ctx(ctx)
    body = ctx["body"]
    raw_b64 = str(body.get("content_base64") or "")
    if not raw_b64:
        raise ApiError(400, "Поле content_base64 обязательно")
    try:
        raw = base64.b64decode(raw_b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ApiError(400, f"Некорректный base64: {exc}") from exc
    if len(raw) > COVER_MAX_BYTES:
        raise ApiError(413, f"Обложка больше {COVER_MAX_BYTES // (1024 * 1024)} МБ")
    name = str(body.get("name") or "cover.jpg")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "jpg"
    if ext not in ("jpg", "jpeg", "png"):
        raise ApiError(400, "Допустимы cover.jpg / .png / .jpeg")
    # Сигнатура — файл должен быть реальным изображением
    if not _cover_magic_ok(raw, ext):
        raise ApiError(400, f"Файл не похож на изображение .{ext}")
    src = pdir / "source"
    src.mkdir(parents=True, exist_ok=True)
    # убираем прочие варианты обложки — должен остаться один файл
    for n in COVER_NAMES:
        old = src / n
        if n != f"cover.{ext}" and old.is_file():
            try:
                old.unlink()
            except OSError as exc:
                log.debug("Старая обложка не удалена %s: %s", old, exc)
    target = src / f"cover.{ext}"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(raw)
    tmp.replace(target)
    return {"ok": True, "exists": True, "name": target.name,
            "size": len(raw), "path": f"source/{target.name}"}


def _cover_magic_ok(raw: bytes, ext: str) -> bool:
    """Сигнатура изображения по первым байтам."""
    if ext in ("jpg", "jpeg"):
        return raw[:3] == b"\xff\xd8\xff"
    if ext == "png":
        return raw[:8] == b"\x89PNG\r\n\x1a\n"
    return False


def _cover_delete(ctx: dict) -> dict:
    """Удалить обложку (DELETE /api/cover?project=)."""
    pdir, _section, _name = _project_ctx(ctx)
    p = _cover_file(pdir)
    if p is None:
        return {"ok": True, "exists": False}
    try:
        p.unlink()
    except OSError as exc:
        raise ApiError(400, f"Не удалось удалить обложку: {exc.strerror}") from exc
    return {"ok": True, "exists": False}


# ════════════════════════════════════════════════════════════════════
# Логи
# ════════════════════════════════════════════════════════════════════


def _logs_list(ctx: dict) -> dict:
    """Дерево логов проекта: рекурсивно по logs/, только *.log.
    path — относительный путь от logs/ (папки через '/'); GET /api/logs."""
    pdir, _section, _name = _project_ctx(ctx)
    out = []
    root = pdir / "logs"
    if not root.is_dir():
        return {"ok": True, "logs": out}
    for f in sorted(root.rglob("*")):
        try:
            if not f.is_file() or f.suffix.lower() != ".log":
                continue
            st = f.stat()
            rel = f.relative_to(root)
            out.append({"name": rel.name,
                        "path": rel.as_posix(),
                        "size": st.st_size,
                        "mtime": int(st.st_mtime)})
        except OSError as exc:
            log.debug("Лог недоступен (%s): %s", f.name, exc)
    return {"ok": True, "logs": out}


def _logs_read(ctx: dict) -> dict:
    """Хвост лог-файла (GET /api/logs/{name}?project=&tail=N байт)."""
    pdir, _section, _name = _project_ctx(ctx)
    name = ctx["params"]["name"]
    sub = ctx["query"].get("dir", "")
    # подпапка — путь от logs/; sub валидируется ПЕРЕД join (абсолютный
    # путь в pathlib перекрыл бы базу — песочницу обходим)
    base = pdir / "logs"
    if sub:
        base = _resolve_project_path(ctx, base, sub)
    target = _resolve_project_path(ctx, base, name)
    if not target.is_file():
        raise ApiError(404, f"Лог не найден: {name}")
    try:
        tail = int(ctx["query"].get("tail", "0") or "0")
    except ValueError:
        tail = 0
    size = target.stat().st_size
    start = max(0, size - min(tail, LOG_TAIL_LIMIT)) if tail else 0
    try:
        with open(target, "rb") as fh:
            fh.seek(start)
            raw = fh.read()
    except OSError as exc:
        raise ApiError(404, f"Лог не читается: {name} ({exc.strerror})") from exc
    text = raw.decode("utf-8", errors="replace")
    return {"ok": True, "name": name, "size": size,
            "start": start, "content": text}


def _logs_delete(ctx: dict) -> dict:
    """Удаление логов проекта: один файл (DELETE /api/logs/{name}
    ?dir=подпапка) или ВСЕ *.log (DELETE /api/logs).
    Пути валидируются _resolve_project_path (выход за logs/ запрещён)."""
    pdir, _section, _name = _project_ctx(ctx)
    name = ctx.get("params", {}).get("name", "")
    sub = ctx.get("query", {}).get("dir", "")
    root = pdir / "logs"
    if name:
        # sub валидируется ПЕРЕД join — абсолютный/.. путь не уйдёт
        # за пределы logs/ (см. _logs_read)
        base = root
        if sub:
            base = _resolve_project_path(ctx, base, sub)
        target = _resolve_project_path(ctx, base, name)
        if not target.is_file():
            raise ApiError(404, f"Лог не найден: {name}")
        try:
            target.unlink()
        except OSError as exc:
            raise ApiError(500, f"Не удалось удалить лог: {exc}") from exc
        return {"ok": True, "deleted": name}
    # очистка всех *.log (папки и не-.log файлы не трогаем)
    deleted = []
    if root.is_dir():
        for f in sorted(root.rglob("*.log")):
            try:
                if f.is_file():
                    f.unlink()
                    deleted.append(f.relative_to(root).as_posix())
            except OSError as exc:
                log.debug("Лог не удаляется (%s): %s", f.name, exc)
    return {"ok": True, "deleted": deleted}


def _notes_get(ctx: dict) -> dict:
    """Заметки (GET /api/notes): projects/notes.md целиком.
    Файла нет — пустая строка (создастся при PUT)."""
    path = _projects_root(ctx) / "notes.md"
    content = path.read_text(encoding="utf-8", errors="replace") \
        if path.is_file() else ""
    return {"ok": True, "exists": path.is_file(), "content": content}


def _notes_put(ctx: dict) -> dict:
    """Запись заметок (PUT /api/notes {content}) — атомарно."""
    body = ctx.get("body") or {}
    if not isinstance(body.get("content"), str):
        raise ApiError(400, "Поле content: строка")
    common = _import_common(ctx)
    path = _projects_root(ctx) / "notes.md"
    common.atomic_write(str(path),
                        unicodedata.normalize("NFC", body["content"]))
    return {"ok": True, "exists": True}


def _parse_check_report(text: str) -> dict:
    """Разбор текстового отчёта translate_check (logs/check_*.txt).

    Формат: `N. Папка: путь` + строки ошибок (  - … / [ВНИМАНИЕ] / [FATAL]),
    финал — `--- Сводка ---`. Возвращает метаданные + entries.
    """
    def _m(pattern: str) -> str | None:
        m = re.search(pattern, text)
        return m.group(1).strip() if m else None

    entries: list[dict] = []
    cur: dict | None = None
    def _chapter_num(m: re.Match) -> int | None:
        """Номер главы из regex-совпадения; мусорные отчёты не роняем."""
        try:
            return int(m.group(1))
        except (ValueError, IndexError):
            return None

    for line in text.splitlines():
        m = re.match(r"^(\d+)\. Папка: (.+)$", line)
        num = _chapter_num(m) if m else None
        if m and num is not None:
            if cur:
                entries.append(cur)
            cur = {"chapter": num, "dir": m.group(2).strip(),
                   "errors": []}
            continue
        m = re.match(r"^(\d+)\.\s+(\S.*)$", line)
        num = _chapter_num(m) if m else None
        if m and num is not None:  # «Папка не найдена.» и т.п.
            if cur:
                entries.append(cur)
            cur = {"chapter": num, "dir": "",
                   "errors": [m.group(2).strip()]}
            continue
        if cur is not None:
            stripped = line.strip()
            if stripped == "--- Сводка ---" or stripped.startswith("==="):
                entries.append(cur)
                cur = None
                continue
            if stripped:
                cur["errors"].append(stripped)
    if cur:
        entries.append(cur)
    for e in entries:
        e["fatal"] = any(x.startswith("[FATAL]") for x in e["errors"])
        if e["dir"].startswith("./"):  # './chapters/xxx' → 'chapters/xxx'
            e["dir"] = e["dir"][2:]
    return {
        "type": _m(r"=== Отчёт о проверке перевода \((\w+)\) ==="),
        "range": _m(r"Диапазон глав\s*: (.+)"),
        "date": _m(r"Дата\s*: (.+)"),
        "checked": _m(r"Проверено глав\s*: (\d+)"),
        "failed": _m(r"С ошибками\s*: (\d+)"),
        "skipped": _m(r"Пропущено\s*: (\d+)"),
        "entries": entries,
    }


def _check_reports(ctx: dict) -> dict:
    """Список и разбор отчётов translate_check (GET /api/check?project=)."""
    pdir, _section, _name = _project_ctx(ctx)
    logs = pdir / "logs"
    out = []
    if logs.is_dir():
        files = [f for f in logs.iterdir()
                 if f.is_file() and f.name.startswith("check_")
                 and f.suffix == ".txt"]
        for f in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                text = f.read_text(encoding="utf-8",
                                   errors="replace")[:CHECK_REPORT_LIMIT]
                info = _parse_check_report(text)
                out.append({"name": f.name,
                            "mtime": int(f.stat().st_mtime), **info})
            except OSError as exc:
                log.debug("Отчёт не читается %s: %s", f.name, exc)
    return {"ok": True, "reports": out}


def _register_check(router: Router) -> None:
    router.add("GET", "/api/check", _check_reports)


def _register_logs(router: Router) -> None:
    router.add("GET", "/api/logs", _logs_list)
    router.add("GET", "/api/logs/{name}", _logs_read)
    router.add("DELETE", "/api/logs/{name}", _logs_delete)
    router.add("DELETE", "/api/logs", _logs_delete)

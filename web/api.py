#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api.py — REST-хендлеры web-бэкэнда.

M1: сессия и вход (GET /api/session, POST /api/login|logout).
M2: hub и проекты — разделы, список, создание, управление, hub_state,
    ACTIONS из run.py, дерево глав, шаблоны.
Далее по майлстоунам: файлы, NER, env, промпты, запуски.
Хендлеры получают ctx {params, query, body, handler, auth, authenticated,
repo_root, projects_root} и возвращают dict для JSON-ответа (200)
или бросают ApiError.
"""
from __future__ import annotations

import copy
import logging
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path

import unicodedata

log = logging.getLogger("web")

# Кеш stats без TTL: вместо времени — сигнатура состояния (mtime папок
# глав + ner/wiki + compiled). При каждом чтении считаем сигнатуру (это
# scandir 1 уровня, а НЕ полный обход глав) и пересчитываем проект только
# если сигнатура изменилась. Так кеш всегда актуален, ловит даже внешние
# правки файлов и переживает рестарт сервера (дисковый кеш).
_STATS_CACHE: dict[str, dict] = {}   # key "sec/name" → {"sig", "stats"}
_STATS_LOCK = threading.Lock()
_STATS_CACHE_FILE = ".stats_cache.json"  # в корне projects/ (рядом с hub_state)
_CACHE_LOADED: set[str] = set()      # корни, для которых загружен дисковой кеш

from web.auth import COOKIE_NAME
from web.jobs import JobManager
from web.multipart import (
    MultipartError, extract_files, extract_value, iter_parts,
    parse_disposition,
)
from web.sandbox import SandboxError, resolve_path
from web.server import ApiError, Router
from web.stages import STAGE_SPECS, build_command, ordered_stages, script_path, spec_for
from web import state as st

UPLOAD_DIRS = ("source", "chapters", "prompts", "images", "tmp")
# Текстовое поле формы (без filename) крупнее — подозрительный запрос
MAX_TEXT_FIELD = 1024 * 1024
BINARY_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".epub",
              ".zip", ".fb2", ".ttf", ".otf", ".woff", ".woff2")
TEXT_EXT = (".txt", ".md", ".json", ".yaml", ".yml", ".env", ".log",
            ".csv", ".xml", ".html", ".css", ".js", ".py")

# ════════════════════════════════════════════════════════════════════
# Служебные
# ════════════════════════════════════════════════════════════════════
def _projects_root(ctx: dict) -> Path:
    """Корень projects/ (обязателен для хендлеров M2+)."""
    root = ctx.get("projects_root")
    if root is None:
        raise ApiError(500, "Корень projects/ не настроен")
    return root


def _repo_root(ctx: dict) -> Path:
    """Корень репозитория (для шаблонов)."""
    root = ctx.get("repo_root")
    if root is None:
        raise ApiError(500, "Корень репозитория не настроен")
    return root


def _import_projects(ctx: dict):
    """Ленивый импорт core.projects (падает 500 с понятной причиной)."""
    try:
        from core import projects as prj
        return prj
    except ImportError as exc:
        raise ApiError(500, f"core.projects недоступен: {exc}")


def _import_common(ctx: dict):
    """Ленивый импорт core.common."""
    try:
        from core import common as c
        return c
    except ImportError as exc:
        raise ApiError(500, f"core.common недоступен: {exc}")


def _import_batch_replace():
    """Ленивый импорт чистой логики cli/batch_replace.py (правила)."""
    try:
        from cli import batch_replace as br
        return br
    except ImportError as exc:
        raise ApiError(500, f"cli.batch_replace недоступен: {exc}")


class _LengthLimitedReader:
    """Бинарный reader, отдающий не более limit байт (тело запроса)."""

    def __init__(self, src, limit: int) -> None:
        self.src = src
        self.left = limit

    def read(self, n: int = -1) -> bytes:
        if self.left <= 0:
            return b""
        if n < 0 or n > self.left:
            n = self.left
        data = self.src.read(n)
        self.left -= len(data)
        return data


def _close_multipart_fields(fields: list[dict]) -> None:
    """Закрыть spool-файлы полей multipart (у файловых полей data — файл)."""
    for f in fields:
        data = f.get("data")
        if data is not None and hasattr(data, "close"):
            data.close()


def _multipart_fields(ctx: dict) -> list[dict]:
    """Поля multipart-запроса: файлы — во временных файлах (spool).

    Тело читается из rfile чанками — память не растёт с размером файлов.
    Файловое поле крупнее max_upload_mb → 413 ДО записи чего-либо на
    диск; текстовое поле (без filename) крупнее MAX_TEXT_FIELD → 400.
    """
    handler = ctx["handler"]
    ctype = ctx.get("content_type") or ""
    boundary = ctx.get("boundary") or ""
    if not boundary and "boundary=" in ctype:
        boundary = ctype.split("boundary=", 1)[1].strip()\
            .strip('"').strip("'").split(";")[0]
    try:
        cl = int(handler.headers.get("Content-Length", "0") or 0)
    except (ValueError, TypeError):
        raise ApiError(400, "Некорректный Content-Length")
    if cl <= 0:
        raise ApiError(400, "Пустое тело multipart")
    try:
        limit_mb = int(getattr(handler.server, "max_upload_mb", 512))
    except (TypeError, ValueError):
        limit_mb = 512
    limit_bytes = limit_mb * 1024 * 1024
    body = _LengthLimitedReader(handler.rfile, cl)
    fields: list[dict] = []
    try:
        for headers, data_iter in iter_parts(body, boundary):
            disp = parse_disposition(headers.get("content-disposition", ""))
            filename = disp.get("filename")
            if filename:
                spool = tempfile.TemporaryFile(mode="w+b")
                size = 0
                try:
                    for chunk in data_iter:
                        size += len(chunk)
                        if size > limit_bytes:
                            raise ApiError(413,
                                           f"Файл слишком большой: {filename}")
                        spool.write(chunk)
                except BaseException:
                    spool.close()
                    raise
                spool.seek(0)
                fields.append({
                    "name": disp.get("name", ""),
                    "filename": filename,
                    "content_type": headers.get("content-type", ""),
                    "data": spool,
                })
            else:
                parts: list[bytes] = []
                size = 0
                for chunk in data_iter:
                    size += len(chunk)
                    if size > MAX_TEXT_FIELD:
                        raise ApiError(400,
                                       "Текстовое поле формы слишком большое")
                    parts.append(chunk)
                fields.append({
                    "name": disp.get("name", ""),
                    "filename": None,
                    "content_type": headers.get("content-type", ""),
                    "data": b"".join(parts),
                })
    except MultipartError as exc:
        _close_multipart_fields(fields)
        raise ApiError(400, f"Некорректный multipart: {exc}")
    except ApiError:
        _close_multipart_fields(fields)
        raise
    return fields


def _atomic_write_spool(target: Path, spool) -> None:
    """Записать spool в target атомарно (tmp в той же папке + os.replace).

    Обрыв соединения не оставляет битый файл поверх существующего;
    spool закрывается.
    """
    try:
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".up-")
    except OSError as exc:
        raise ApiError(500, f"Не удалось создать временный файл: {exc}")
    try:
        with os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(spool, out, 1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, target)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise ApiError(500, f"Не удалось записать файл: {exc}")
    finally:
        spool.close()


def _project_path(ctx: dict) -> tuple[Path, str, str]:
    """Путь к папке проекта по параметрам маршрута + раздел/имя."""
    prj = _import_projects(ctx)
    section = ctx["params"]["sec"]
    name = ctx["params"]["name"]
    pdir = prj.project_dir(_projects_root(ctx), section, name)
    if not pdir.is_dir():
        raise ApiError(404, f"Проект не найден: {section}/{name}")
    return pdir, section, name


def _check_confirm(ctx: dict, what: str = "УДАЛИТЬ") -> None:
    """Опасные действия требуют ввода слова подтверждения."""
    got = (ctx["body"].get("confirm") or "").strip().upper()
    if got != what:
        raise ApiError(400, f"Для подтверждения введите слово {what}")


# ════════════════════════════════════════════════════════════════════
# Сессия (M1)
# ════════════════════════════════════════════════════════════════════
def register(router: Router, host: str) -> None:
    """Регистрирует все хендлеры web-бэкэнда."""
    router.add("GET", "/api/session", _session)
    router.add("POST", "/api/login", _login)
    router.add("POST", "/api/logout", _logout)
    _register_hub(router)
    _register_files(router)
    _register_m7(router)
    _register_logs(router)
    _register_check(router)
    _register_templates(router)
    _register_jobs(router)


def _session(ctx: dict) -> dict:
    return {
        "ok": True,
        "authenticated": ctx["authenticated"],
        "token_set": ctx["auth"].token_set(),
        "host": ctx["host"],
    }


def _login(ctx: dict) -> dict:
    # M3 (AUDIT): rate-limit — много неудачных входов за минуту → 429
    if ctx["auth"].login_blocked():
        raise ApiError(429, "Слишком много попыток входа. Подождите минуту.")
    token = (ctx["body"].get("token") or "").strip()
    if not ctx["auth"].check_token(token):
        ctx["auth"].login_failure()
        raise ApiError(401, "Неверный токен")
    sid = ctx["auth"].issue_session()
    ctx["handler"].set_cookie(COOKIE_NAME, sid)
    return {"ok": True, "authenticated": True}


def _logout(ctx: dict) -> dict:
    sid = ctx["handler"].session_id()
    ctx["auth"].invalidate_session(sid)
    ctx["handler"].clear_cookie(COOKIE_NAME)
    return {"ok": True}


# ════════════════════════════════════════════════════════════════════
# Пульт и проекты (M2)
# ════════════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════════════
# Файлы (M3)
# ════════════════════════════════════════════════════════════════════
def _project_ctx(ctx: dict) -> tuple[Path, str, str]:
    """Проект по query project=sec/name (для файловых хендлеров)."""
    prj = _import_projects(ctx)
    project = (ctx["query"].get("project") or ctx["body"].get("project") or "")
    if "/" not in project:
        raise ApiError(400, "Параметр project=sec/name обязателен")
    section, _, name = project.partition("/")
    pdir = prj.project_dir(_projects_root(ctx), section, name)
    if not pdir.is_dir():
        raise ApiError(404, f"Проект не найден: {section}/{name}")
    return pdir, section, name


def _resolve_project_path(ctx: dict, pdir: Path, rel: str) -> Path:
    """Разрешает путь внутри проекта; запрещает выход и NUL."""
    try:
        return resolve_path(pdir, rel)
    except SandboxError as exc:
        raise ApiError(400, str(exc))


def _files_listing(ctx: dict) -> dict:
    """Листинг папки проекта (GET /api/files?project=&path=)."""
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
    return {"ok": True, "path": rel, "entries": entries}


def _is_binary_bytes(data: bytes) -> bool:
    """NUL-снифф: бинарный файл не открываем как текст."""
    return b"\x00" in data[:8192]


FILE_TEXT_LIMIT = 5 * 1024 * 1024  # > 5 МБ — редактор не открывает, скачивание


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


def _file_delete(ctx: dict) -> dict:
    """Удаление файла (DELETE /api/file?project=&path=)."""
    pdir, section, name = _project_ctx(ctx)
    rel = ctx["query"].get("path", "")
    target = _resolve_project_path(ctx, pdir, rel)
    if not target.exists():
        raise ApiError(404, "Файл не найден")
    try:
        if target.is_dir():
            import shutil
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as exc:
        raise ApiError(500, f"Не удалось удалить: {exc}")
    return {"ok": True, "path": rel}


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
    router.add("POST", "/api/upload", _file_upload)
    router.add("GET", "/api/download", _file_download)


def _register_hub(router: Router) -> None:
    router.add("GET", "/api/state", _state)
    router.add("GET", "/api/dashboard", _dashboard)
    router.add("GET", "/api/actions", _actions)
    router.add("GET", "/api/sections", _sections)
    router.add("POST", "/api/sections", _sections_create)
    router.add("POST", "/api/sections/rename", _sections_rename)
    router.add("DELETE", "/api/sections/{name}", _sections_delete)
    router.add("GET", "/api/projects", _projects_list)
    router.add("POST", "/api/projects", _projects_create)
    router.add("POST", "/api/projects/move", _projects_move)
    router.add("POST", "/api/projects/rename", _projects_rename)
    router.add("POST", "/api/projects/copy", _projects_copy)
    router.add("DELETE", "/api/projects", _projects_delete)
    router.add("GET", "/api/projects/{sec}/{name}/stats", _project_stats)
    router.add("GET", "/api/projects/{sec}/{name}/status", _project_status)
    router.add("GET", "/api/projects/{sec}/{name}/tree", _project_tree)
    router.add("GET", "/api/projects/{sec}/{name}/chapters/titles",
               _chapter_titles_get)
    router.add("PUT", "/api/projects/{sec}/{name}/chapters/titles",
               _chapter_titles_put)
    router.add("DELETE", "/api/projects/{sec}/{name}/chapters",
               _chapters_delete)
    router.add("GET", "/api/templates", _templates)


def _state(ctx: dict) -> dict:
    """hub_state — общий с cli-пультом (последний раздел/проект)."""
    hub = st.load_hub_state(_projects_root(ctx))
    return {"ok": True, **hub}


def _stats_signature(pdir: Path) -> str:
    """Дешёвый отпечаток состояния проекта для кеша stats.

    Содержит mtime папок глав (создание/удаление файлов меняет mtime
    каталога), mtime ner.json/wiki.md и mtime папок compiled-кандидатов.
    Это scandir 1 уровня, а НЕ полный обход глав — поэтому проверка
    сигнатуры на порядки дешевле самого project_stats().
    """
    pdir = Path(pdir)
    parts: list[str] = []
    ch = pdir / "chapters"
    try:
        subs = sorted(
            (d.name, d.stat().st_mtime_ns)
            for d in ch.iterdir() if d.is_dir()
        )
    except OSError:
        subs = []
    parts.append(f"ch:{len(subs)}")
    parts.extend(f"{n}:{m}" for n, m in subs)
    for f in ("ner.json", "wiki.md"):
        p = pdir / f
        try:
            parts.append(f"{f}:{p.stat().st_mtime_ns}")
        except OSError:
            parts.append(f"{f}:0")
    for d in (pdir, pdir / "tmp", pdir / "output"):
        try:
            parts.append(f"d:{d.name}:{d.stat().st_mtime_ns}")
        except OSError:
            parts.append(f"d:{d.name}:0")
    return "|".join(parts)


def _stats_cache_path(root: Path) -> Path:
    """Файл дискового кеша stats (в корне projects/, рядом с hub_state)."""
    return root / _STATS_CACHE_FILE


def _stats_cache_key(root: Path, sec: str, name: str) -> str:
    """Ключ кеша с пространством имён корня projects/ (L5, AUDIT):
    два сервера на разных корнях не смешивают записи."""
    return f"{root}::{sec}/{name}"


def _load_stats_cache(root: Path) -> None:
    """Загрузка дискового кеша stats (переживает рестарт сервера)."""
    try:
        import json as _json
        data = _json.loads(_stats_cache_path(root).read_text(
            encoding="utf-8"))
        if isinstance(data, dict):
            pfx = f"{root}::"
            _STATS_CACHE.update(
                {pfx + k: v for k, v in data.items()
                 if isinstance(v, dict) and "sig" in v
                 and ("stats" in v or "status" in v)})
    except (OSError, ValueError) as exc:
        log.debug("stats-кеш не читается: %s", exc)


def _save_stats_cache(root: Path) -> None:
    """Атомарная запись дискового кеша stats (только записи этого корня)."""
    try:
        import json as _json
        from core.common import atomic_write
        pfx = f"{root}::"
        mine = {k[len(pfx):]: v for k, v in _STATS_CACHE.items()
                if k.startswith(pfx)}
        atomic_write(_stats_cache_path(root),
                     _json.dumps(mine, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — кеш не критичен
        log.debug("stats-кеш не пишется: %s", exc)


def _ensure_stats_cache(root: Path) -> None:
    """Загрузка дискового кеша один раз на корень projects/."""
    key_root = str(root)
    if key_root in _CACHE_LOADED:
        return
    _CACHE_LOADED.add(key_root)
    _load_stats_cache(root)


def _invalidate_all_stats(root: Path) -> None:
    """Сброс кеша stats для этого корня (структурные операции)."""
    with _STATS_LOCK:
        pfx = f"{root}::"
        for k in [k for k in _STATS_CACHE if k.startswith(pfx)]:
            del _STATS_CACHE[k]
        _save_stats_cache(root)


def _invalidate_stats_entry(root: Path, sec: str, name: str) -> None:
    """Точечный сброс кеша stats одного проекта (создание/перенос/
    переименование/дублирование/удаление) — остальные проекты кеш
    сохраняют, dashboard после операции не пересобирается целиком.
    """
    with _STATS_LOCK:
        key = _stats_cache_key(root, sec, name)
        for k in (key, key + ":status"):
            _STATS_CACHE.pop(k, None)
        _save_stats_cache(root)


def _cached_stats(prj, root: Path, sec: str, name: str) -> str:
    """stats проекта: из кеша, если сигнатура не изменилась (без TTL).

    Кеш в памяти + на диске; актуальность — сигнатура mtime, а не время.
    """
    key = _stats_cache_key(root, sec, name)
    pdir = root / sec / name
    sig = _stats_signature(pdir)
    with _STATS_LOCK:
        _ensure_stats_cache(root)
        entry = _STATS_CACHE.get(key)
        if entry is not None and entry.get("sig") == sig:
            return entry["stats"]
        try:
            stats = prj.project_stats(pdir)
        except Exception as exc:  # noqa: BLE001 — статистика необязательна
            log.debug("stats(%s) не собралась: %s", key, exc)
            stats = ""
        _STATS_CACHE[key] = {"sig": sig, "stats": stats}
        _save_stats_cache(root)
        return stats


def _cached_status(prj, root: Path, sec: str, name: str) -> dict:
    """Таблица готовности глав: кеш по сигнатуре (как stats), ключ :status.

    Сигнатура та же (_stats_signature): mtime папок глав покрывает
    появление/удаление артефактов, ner.json/wiki.md — свои mtime.
    """
    key = _stats_cache_key(root, sec, name) + ":status"
    pdir = root / sec / name
    sig = _stats_signature(pdir)
    with _STATS_LOCK:
        _ensure_stats_cache(root)
        entry = _STATS_CACHE.get(key)
        if entry is not None and entry.get("sig") == sig:
            return entry.get("status", {})
        try:
            status = prj.project_progress_table(pdir)
        except Exception as exc:  # noqa: BLE001 — статус необязателен
            log.debug("status(%s) не собрался: %s", key, exc)
            status = {}
        _STATS_CACHE[key] = {"sig": sig, "status": status}
        _save_stats_cache(root)
        return status


def _collect_stats(prj, root: Path) -> tuple[list, int]:
    """Обход всех разделов/проектов; stats — из кеша по сигнатуре."""
    sections = []
    total = 0
    for sec in prj.load_sections(root):
        names = prj.list_projects(root, sec)
        total += len(names)
        items = []
        for n in names:
            stats = _cached_stats(prj, root, sec, n)
            items.append({"name": n, "stats": stats})
        sections.append({"name": sec, "projects": items})
    return sections, total


def _dashboard(ctx: dict) -> dict:
    """Сводка для дашборда (W3): разделы/проекты/статистика + недавние jobs.

    Один запрос вместо N запросов /stats с фронта. Stats — кеш по
    сигнатуре (без TTL); jobs читаются всегда свежими через общий
    JobManager (_job_manager): HTTP-контекст не носит job_manager.
    """
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    hub = st.load_hub_state(root)
    sections, total = _collect_stats(prj, root)
    recent_jobs = []
    running_jobs = []
    jm = _job_manager(ctx)
    try:
        # «Последние запуски» — до 20; активные — из ПОЛНОГО
        # списка, а не из среза (running может быть старше 20 записей)
        all_jobs = jm.list()
        recent_jobs = all_jobs[:20]
        for item in all_jobs:
            if item.get("status") == "running":
                job = jm.get(item["id"])
                if job is not None:
                    running_jobs.append(job.payload())
    except Exception as exc:  # noqa: BLE001
        log.debug("jobs.list() не собрался: %s", exc)
    return {"ok": True, "total": total, "sections": sections,
            "hub": hub, "recent_jobs": recent_jobs,
            "running_jobs": running_jobs}


def _actions(ctx: dict) -> dict:
    """Реестр стадий из STAGE_SPECS + доступность скриптов ."""
    repo = _repo_root(ctx)
    items = []
    for key, spec in ordered_stages():
        script = script_path(key, repo)
        items.append({
            "key": key, "title": spec["title"], "folder": "cli",
            "script": spec["script"],
            "available": script is not None and script.is_file(),
        })
    return {"ok": True, "actions": items}


def _sections(ctx: dict) -> dict:
    """Разделы со счётчиками проектов."""
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    return {"ok": True, "sections": [
        {"name": s, "count": len(prj.list_projects(root, s))}
        for s in prj.load_sections(root)
    ]}


def _hub_clear_section(root: Path, section: str) -> None:
    """Зачистить ссылку на раздел в hub_state (переименован/удалён)."""
    hub = st.load_hub_state(root)
    if hub.get("section") == section:
        hub.pop("section", None)
        hub.pop("project", None)
        st.save_hub_state(root, hub)


def _sections_create(ctx: dict) -> dict:
    """Создать раздел (POST /api/sections, {"name": ...})."""
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    name = (ctx["body"].get("name") or "").strip()
    ok, res = prj.create_section(root, name)
    if not ok:
        raise ApiError(400, str(res))
    _invalidate_all_stats(root)
    return {"ok": True, "name": res}


def _sections_rename(ctx: dict) -> dict:
    """Переименовать/слить раздел (POST /api/sections/rename, {src, dst})."""
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    body = ctx["body"]
    src = (body.get("src") or "").strip()
    dst = (body.get("dst") or "").strip()
    if not src or not dst:
        raise ApiError(400, "src и dst обязательны")
    ok, res = prj.rename_section(root, src, dst)
    if not ok:
        raise ApiError(400, str(res))
    _hub_clear_section(root, src)
    _invalidate_all_stats(root)
    return {"ok": True, "src": src, "dst": res}


def _sections_delete(ctx: dict) -> dict:
    """Удалить пустой раздел (DELETE /api/sections/{name})."""
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    name = ctx["params"]["name"]
    ok, res = prj.delete_section(root, name)
    if not ok:
        code = 409 if "не пуст" in str(res) else 404
        raise ApiError(code, str(res))
    _hub_clear_section(root, name)
    _invalidate_all_stats(root)
    return {"ok": True, "name": res}


def _projects_list(ctx: dict) -> dict:
    """Имена проектов раздела (GET /api/projects?section=)."""
    prj = _import_projects(ctx)
    section = ctx["query"].get("section", "")
    if section not in prj.load_sections(_projects_root(ctx)):
        raise ApiError(400, f"Неизвестный раздел: {section!r}")
    names = prj.list_projects(_projects_root(ctx), section)
    return {"ok": True, "section": section, "projects": names}


def _projects_create(ctx: dict) -> dict:
    """Создать проект: раздел, имя, метаданные, шаблон типа книги."""
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    repo = _repo_root(ctx)
    body = ctx["body"]
    section = (body.get("section") or "").strip()
    name = (body.get("name") or "").strip()
    if section not in prj.load_sections(root):
        raise ApiError(400, f"Неизвестный раздел: {section!r}")
    raw_name = name
    name = prj.sanitize_project_name(name)
    if not name:
        raise ApiError(400, "Имя после очистки пустое — допустимы латинские "
                             "буквы, цифры, точки, '_' и '-'")
    ok, res = prj.create_project(root, section, name)
    if not ok:
        raise ApiError(400, str(res))
    pdir = Path(res)
    tpl = (body.get("template") or "").strip() or None
    copied: list[str] = []
    if tpl:
        tpl_dir = repo / "templates" / tpl
        if not tpl_dir.is_dir():
            raise ApiError(400, f"Шаблон не найден: templates/{tpl}")
        prj.write_project_metadata(
            pdir, tpl_dir,
            title=(body.get("title") or "").strip(),
            author=(body.get("author") or "").strip(),
            genres=([g.strip() for g in (body.get("genres") or "").split(",")
                     if g.strip()] or None),
        )
        copied = prj.fill_project_from_template(pdir, tpl_dir)
    _invalidate_stats_entry(root, section, name)
    return {"ok": True, "section": section, "name": name,
            "renamed": name != raw_name, "copied": copied}


def _projects_move(ctx: dict) -> dict:
    """Перенос проекта в другой раздел."""
    prj = _import_projects(ctx)
    body = ctx["body"]
    section = (body.get("section") or "").strip()
    name = (body.get("name") or "").strip()
    dst = (body.get("dst") or "").strip()
    ok, res = prj.move_project(_projects_root(ctx), section, name, dst)
    if not ok:
        raise ApiError(400, str(res))
    # старый ключ (root::раздел/имя) устарел; новый ещё не закеширован
    _invalidate_stats_entry(_projects_root(ctx), section, name)
    return {"ok": True, "section": dst, "name": Path(res).name}


def _projects_rename(ctx: dict) -> dict:
    """Переименование проекта внутри раздела."""
    prj = _import_projects(ctx)
    body = ctx["body"]
    section = (body.get("section") or "").strip()
    name = (body.get("name") or "").strip()
    new_name = (body.get("new_name") or "").strip()
    ok, res = prj.rename_project(_projects_root(ctx), section, name, new_name)
    if not ok:
        raise ApiError(400, str(res))
    _invalidate_stats_entry(_projects_root(ctx), section, name)
    return {"ok": True, "section": section, "name": Path(res).name}


def _projects_copy(ctx: dict) -> dict:
    """Дублирование проекта внутри раздела."""
    prj = _import_projects(ctx)
    body = ctx["body"]
    section = (body.get("section") or "").strip()
    name = (body.get("name") or "").strip()
    new_name = (body.get("new_name") or "").strip()
    ok, res = prj.copy_project(_projects_root(ctx), section, name, new_name)
    if not ok:
        raise ApiError(400, str(res))
    _invalidate_stats_entry(_projects_root(ctx), section, new_name)
    return {"ok": True, "section": section, "name": Path(res).name}


def _projects_delete(ctx: dict) -> dict:
    """Удаление проекта (требует confirm: 'УДАЛИТЬ')."""
    prj = _import_projects(ctx)
    body = ctx["body"]
    section = (body.get("section") or "").strip()
    name = (body.get("name") or "").strip()
    _check_confirm(ctx, "УДАЛИТЬ")
    ok, res = prj.delete_project(_projects_root(ctx), section, name)
    if not ok:
        raise ApiError(400, str(res))
    _invalidate_stats_entry(_projects_root(ctx), section, name)
    return {"ok": True, "section": section, "name": name}


def _project_status(ctx: dict) -> dict:
    """GET /api/projects/{sec}/{name}/status — таблица готовности глав."""
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    _pdir, sec, name = _project_path(ctx)
    return {"ok": True, "status": _cached_status(prj, root, sec, name)}


def _project_stats(ctx: dict) -> dict:
    """Краткая статистика проекта (строка) + структура папок.

    stats — кеш по сигнатуре (без TTL): страница «Проекты» раньше
    делала запрос на каждую карточку, каждый запрос = полный обход
    chapters/. skeleton дёшев (iterdir верхнего уровня) — не кешируется.
    """
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    pdir, section, name = _project_path(ctx)
    stats = _cached_stats(prj, root, section, name)
    return {"ok": True, "section": section, "name": name,
            "stats": stats,
            "skeleton": [p.name for p in sorted(pdir.iterdir())
                          if p.is_dir()]}


def _project_tree(ctx: dict) -> dict:
    """Дерево глав: номер, папка, артефакты с размерами.

    помимо канонических имён — легаси-суффиксы старых проектов:
    ``*_translated.txt``, ``*_redacted.txt``, ``*_polished.txt``
    (вкладка «Редактор» видит ключевые файлы независимо от маски)."""
    common = _import_common(ctx)
    pdir, section, name = _project_path(ctx)
    chapters_dir = pdir / "chapters"
    if not chapters_dir.is_dir():
        return {"ok": True, "section": section, "name": name,
                "chapters": []}
    chapter_map = common.build_chapter_map(chapters_dir)
    artifacts = ("chapter.txt", "translated.txt", "translated_trace.json",
                 "redacted.txt", "polished.txt")
    legacy_sfx = ("_translated.txt", "_redacted.txt", "_polished.txt")
    items = []
    for num in sorted(chapter_map):
        for dir_str in chapter_map[num]:
            d = Path(dir_str)
            entry = {"id": num, "dir": d.name, "artifacts": {}}
            for art in artifacts:
                f = d / art
                if f.is_file():
                    entry["artifacts"][art] = f.stat().st_size
            try:
                entries = list(d.iterdir())
            except OSError:
                entries = []
            for f in entries:
                if f.is_file() and f.name.endswith(legacy_sfx):
                    entry["artifacts"][f.name] = f.stat().st_size
            items.append(entry)
    return {"ok": True, "section": section, "name": name,
            "chapters": items}


def _chapter_titles_get(ctx: dict) -> dict:
    """Названия глав (GET /api/projects/{s}/{n}/chapters/titles).

    type=polished|redacted|translated|chapter — тип файлов глав;
    названия — первая непустая строка файла (read_chapter_titles).
    """
    common = _import_common(ctx)
    pdir, section, name = _project_path(ctx)
    want = ctx["query"].get("type", "polished")
    if want not in ("chapter", "translated", "redacted", "polished"):
        raise ApiError(400, f"Недопустимый тип: {want}")
    chapters_dir = pdir / "chapters"
    if not chapters_dir.is_dir():
        return {"ok": True, "section": section, "name": name,
                "type": want, "titles": {}}
    titles = common.read_chapter_titles(chapters_dir, want=want)
    # all_ids — непрерывный диапазон 1..N по существующим ПАПКАМ глав
    # (build_chapter_map), а не по titles: файлов типа может не быть вовсе;
    # missing — номера без файла нужного типа (для серых ячеек)
    try:
        ids = sorted({int(k) for k in common.build_chapter_map(chapters_dir)})
    except (ValueError, TypeError):
        ids = []
    all_ids = list(range(1, ids[-1] + 1)) if ids else []
    missing = [n for n in all_ids if n not in titles]
    return {"ok": True, "section": section, "name": name,
            "type": want, "titles": titles,
            "all_ids": all_ids, "missing": missing}


def _chapters_delete(ctx: dict) -> dict:
    """Удалить файлы глав (DELETE …/chapters).

    query: type=polished|redacted|translated|chapter + start/end
    (диапазон номеров, включительно). Возвращает удалённые номера.
    """
    common = _import_common(ctx)
    pdir, section, name = _project_path(ctx)
    q = ctx["query"]
    want = q.get("type", "polished")
    if want not in ("chapter", "translated", "redacted", "polished"):
        raise ApiError(400, f"Недопустимый тип: {want}")
    try:
        start = int(q.get("start", "")) if q.get("start") else None
        end = int(q.get("end", "")) if q.get("end") else None
    except ValueError:
        raise ApiError(400, "start/end должны быть числами")
    chapters_dir = pdir / "chapters"
    if not chapters_dir.is_dir():
        return {"ok": True, "section": section, "name": name,
                "type": want, "deleted": []}
    ch_map = common.build_chapter_map(chapters_dir)
    deleted = []
    for num in sorted(ch_map):
        if start is not None and num < start:
            continue
        if end is not None and num > end:
            continue
        dirs = ch_map[num]
        if not dirs:
            continue
        f, _msgs = common.find_chapter_file(dirs[-1], num, want=want,
                                            strict=True)
        if f:
            try:
                os.unlink(f)
                deleted.append(num)
            except OSError:
                pass
    return {"ok": True, "section": section, "name": name,
            "type": want, "deleted": deleted}


def _chapter_titles_put(ctx: dict) -> dict:
    """Сохранить названия глав (PUT …/chapters/titles).

    body: {type, titles: {номер: строка}} — каждая строка заменяет
    первую непустую строку соответствующего файла главы
    (write_chapter_titles, NFC).
    """
    common = _import_common(ctx)
    pdir, section, name = _project_path(ctx)
    body = ctx["body"] or {}
    want = str(body.get("type") or "polished")
    if want not in ("chapter", "translated", "redacted", "polished"):
        raise ApiError(400, f"Недопустимый тип: {want}")
    raw = body.get("titles") or {}
    if not isinstance(raw, dict):
        raise ApiError(400, "titles должен быть объектом {номер: строка}")
    titles: dict[int, str] = {}
    for k, v in raw.items():
        try:
            titles[int(k)] = str(v)
        except (TypeError, ValueError):
            raise ApiError(400, f"Недопустимый номер главы: {k!r}")
    chapters_dir = pdir / "chapters"
    if not chapters_dir.is_dir():
        raise ApiError(404, "Папка chapters/ не найдена")
    result = common.write_chapter_titles(chapters_dir, want, titles)
    return {"ok": True, "section": section, "name": name,
            "type": want, "updated": result["updated"],
            "missing": result["missing"],
            "warnings": result["warnings"]}


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
# NER, review, конфиги, промпты (M7)
# ════════════════════════════════════════════════════════════════════
NER_TEXT_LIMIT = 10 * 1024 * 1024  # > 10 МБ — не JSON, а скачивание


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
    format=text  → записи текстом (format_ner_record, Термин/Тип/Перевод);
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
        lines: list[str] = []
        for i, item in enumerate(filtered, 1):
            lines.extend(common.format_ner_record(item, i))
        return {"ok": True, "name": "ner_analysis.txt",
                "content": "\n".join(lines), "total": len(filtered)}
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
    """Файл review внутри проекта (ner_review.json /
    translate_check_llm_review.json)."""
    pdir, _section, _name = _project_ctx(ctx)
    return pdir / fname


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


def _sys_env_path(ctx: dict) -> Path:
    """Системный (общий) .env: WEB_ENV_FILE (в образе Docker —
    /app/projects/.env внутри постоянного тома — правки вкладки
    «Настройки» переживают обновление образа) → корневой .env репо."""
    override = os.environ.get("WEB_ENV_FILE", "").strip()
    if override:
        return Path(override)
    return _repo_root(ctx) / ".env"


def _env_path(ctx: dict, scope: str) -> Path:
    """Файл .env для web-редактирования.

    scope=project → ТОЛЬКО pdir/.env (собственный файл проекта; если его
    нет — хендлеры отвечают exists=False); scope=global → системный .env
    (WEB_ENV_FILE в Docker — projects/.env в томе; иначе корневой .env
    репо), правится на вкладке «Настройки».
    """
    if scope == "global":
        return _sys_env_path(ctx)
    return _project_ctx(ctx)[0] / ".env"


def _env_scope_info(ctx: dict, scope: str, path: Path) -> dict:
    """Пометка: чей файл фактически открыт (проект/общий)."""
    if scope == "project":
        return {"source": "project"}
    return {"source": "shared"}


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


_ENV_KEY_PREFIXES = ("WEB_", "PIPELINE_", "TRANSLATE_CHECK_", "NER_CHECK_",
                     "NER_", "BATCH_REPLACE_", "COMPILE_", "WIKI_",
                     "EPUB_", "CHUNK_", "MIN_LEN_RATIO_")
_ENV_KEY_SUFFIXES = ("_HOST", "_API_KEY", "_MODEL")


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
    """.env (GET /api/env?project=&scope=project|global).

    W6: без аутентификации (доверенная LAN) значения ВИДИМЫ — отдаём
    целиком (content). При --auth — только ключи и маска ••••.
    Плюс пометка source: чей файл фактически открыт (project/shared/repo).
    scope=global — прозрачность слоёв: sources (ключ файла перекрыт
    os.environ — правка на «Настройках» не применится) и env_extra
    (ключи окружения, которых нет в файле: WEB_* из compose и т.п.).
    """
    scope = ctx["query"].get("scope", "project")
    p = _env_path(ctx, scope)
    info = _env_scope_info(ctx, scope, p)
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
    """Запись .env (PUT /api/env {scope, content | changes}).

    content — ПОЛНАЯ замена файла (создание с нуля / дублирование из
    общего или шаблона); changes: {KEY: value} — точечная замена
    (пустое значение — удалить строку ^KEY=, комментарии не трогаем).
    Значения в ответ не возвращаются."""
    common = _import_common(ctx)
    body = ctx["body"]
    scope = body.get("scope", "project")
    p = _env_path(ctx, scope)
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
    elif scope == "project":
        # M9: точечная запись в ещё не созданный .env проекта — сид из
        # системного корневого .env (без секретов), как в
        # _persist_run_params: голый .env затенял бы системный
        # (HOST/API_KEY/MODEL) и ломал конфиг LLM проекта
        try:
            src = _sys_env_path(ctx)
            if src.is_file():
                text = _strip_secret_keys(
                    src.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            log.debug("Не удалось сидировать .env проекта: %s", exc)
    lines = text.splitlines()
    for key, value in (changes or {}).items():
        k = str(key).strip()
        # M4 (AUDIT): ключ — строго [A-Za-z0-9_] (нет '=', пробелов, '\n')
        if not _ENV_KEY_RE.match(k):
            raise ApiError(400, f"Некорректный ключ: {key!r}")
        value = "" if value is None else str(value)
        # M4 (AUDIT): перевод строки в значении — инъекция новых ключей
        if "\n" in value or "\r" in value:
            raise ApiError(400, f"Значение ключа {k!r} не может содержать перевод строки")
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


def _env_delete(ctx: dict) -> dict:
    """Удалить собственный .env проекта (DELETE /api/env?project=&scope=project).

    После удаления проект снова использует общий корневой .env —
    канон find_env_file (fallback) остаётся для конвейера.
    """
    pdir, _section, _name = _project_ctx(ctx)
    p = pdir / ".env"
    if p.is_file():
        p.unlink()
    return {"ok": True, "scope": "project", "deleted": True}


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
COVER_NAMES = ("cover.jpg", "cover.png", "cover.jpeg")
COVER_MAX_BYTES = 8 * 1024 * 1024  # 8 МБ


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
    # L6 (AUDIT): сигнатура — файл должен быть реальным изображением
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
    """Сигнатура изображения по первым байтам (L6, AUDIT)."""
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
# Логи (M8)
# ════════════════════════════════════════════════════════════════════
LOG_TAIL_LIMIT = 1024 * 1024  # максимум 1 МБ на просмотр


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
    router.add("GET", "/api/env", _env_get)
    router.add("PUT", "/api/env", _env_put)
    router.add("DELETE", "/api/env", _env_delete)
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
CHECK_REPORT_LIMIT = 512 * 1024  # читаем не больше 512 КБ на отчёт


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


# ════════════════════════════════════════════════════════════════════
# Запуски и стадии (M4)
# ════════════════════════════════════════════════════════════════════
def _job_manager(ctx: dict) -> JobManager:
    """Общий JobManager (singleton на сервер, ленивое создание).

    Приоритет: явный ctx['job_manager'] (тесты/встраивание) →
    handler.server.job_manager (реальный сервер) → глобальный
    _main.JOB_MANAGER (fallback, напр. CLI-импорты)."""
    jm = ctx.get("job_manager")
    if jm is not None:
        return jm
    handler = ctx.get("handler")
    srv = handler.server if handler is not None else None
    jm = getattr(srv, "job_manager", None) if srv is not None else None
    if jm is None:
        from web import main as _main
        jm = _main.JOB_MANAGER  # pragma: no cover — реальный сервер
        if srv is not None:
            srv.job_manager = jm
    return jm


def _jobs_start(ctx: dict) -> dict:
    """Запуск стадии (POST /api/jobs {action, project, params})."""
    body = ctx["body"]
    action = (body.get("action") or "").strip()
    project = (body.get("project") or "").strip()
    params = body.get("params") or {}
    if not action:
        raise ApiError(400, "Поле action обязательно")
    if "/" not in project:
        raise ApiError(400, "Параметр project=sec/name обязателен")
    prj = _import_projects(ctx)
    section, _, name = project.partition("/")
    pdir = prj.project_dir(_projects_root(ctx), section, name)
    if not pdir.is_dir():
        raise ApiError(404, f"Проект не найден: {section}/{name}")
    spec = spec_for(action)
    if spec is None:
        raise ApiError(400, f"Стадия {action} пока не поддерживается в web")
    title = spec["title"]
    repo = _repo_root(ctx)
    script = script_path(action, repo)
    if script is None or not script.is_file():
        raise ApiError(500, f"Скрипт не найден: {spec['script']}")
    ctx["project_dir"] = pdir  # для LLM-профилей (find_env_file)
    # валидация number-полей с min/max из spec: недопустимое значение
    # → 400 ДО запуска (скрипт бы упал с кодом 2 и «failed» без причины)
    for f in spec.get("fields") or []:
        if f.get("type") != "number" or (f.get("min") is None
                                          and f.get("max") is None):
            continue
        raw = params.get(f["name"])
        if raw is None or raw == "":
            continue
        try:
            n = float(str(raw))
        except (TypeError, ValueError):
            continue
        label = (f.get("label") or f["name"]).split("(")[0].strip()
        try:
            fmin = None if f.get("min") is None else float(f["min"])
            fmax = None if f.get("max") is None else float(f["max"])
        except (TypeError, ValueError):
            continue
        if fmin is not None and n < fmin:
            raise ApiError(400, f"«{label}»: минимум {f['min']}")
        if fmax is not None and n > fmax:
            raise ApiError(400, f"«{label}»: максимум {f['max']}")
    # R9: настройки запуска сохраняются в .env проекта (копия общего);
    # путь «Проверки» (ctx["review_apply"]) — не настройки запуска:
    # флаги apply/dry_run в pdir/.env — шум, их там быть не должно
    if not ctx.get("review_apply"):
        _persist_run_params(ctx, pdir, action, params)
    argv = build_command(action, params, ctx)
    argv[0] = str(script)  # абсолютный путь к скрипту
    jm = _job_manager(ctx)
    # H2 (AUDIT): лимит параллельных задач --jobs-limit (мёртвая опция → живая)
    handler = ctx.get("handler")
    srv = handler.server if handler is not None else None
    limit = getattr(srv, "jobs_limit", 2) if srv is not None else 2
    running = sum(1 for j in jm.list() if j.get("status") == "running")
    if running >= limit:
        raise ApiError(
            429,
            f"Лимит параллельных задач: {limit} (активно: {running}). "
            f"Дождитесь завершения или остановите запуск.",
        )
    # M10 (AUDIT): per-project лок — две стадии на один проект
    # параллельно перезаписали бы одни и те же артефакты
    busy = jm.running_on(project)
    if busy is not None:
        raise ApiError(
            409,
            f"Проект {project} уже обрабатывается задачей «{busy.title}» "
            f"({busy.id}) — дождитесь завершения или остановите её.",
        )
    env = None
    api_key = ctx.pop("_llm_api_key", None)
    if api_key:
        # P1 (AUDIT #2): ключ — только в окружении subprocess
        env = {"LLM_API_KEY": str(api_key)}
    job = jm.start(action, title, project, argv, pdir, env=env)
    return {"ok": True, "job": _job_payload(job)}


def _job_payload(job) -> dict:
    """Публичное представление запуска (метаданные + буфер)."""
    return job.payload()


# ════════════════════════════════════════════════════════════════════
# R9: настройки запусков в .env проекта
# ════════════════════════════════════════════════════════════════════
def _env_apply_keys(env_path: Path, updates: dict,
                    removes: set[str] | None = None) -> None:
    """Обновляет KEY=VALUE в .env, сохраняя комментарии/порядок строк.

    Существующие ключи заменяются на месте, новые добавляются в конец;
    ключи из removes удаляются (строка убирается целиком); запись —
    атомарная (atomic_write). M4 (AUDIT): значения санитизируются
    (strip + перевод строки → пробел) — нет инъекции новых ключей."""
    lines: list[str] = []
    if env_path.is_file():
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
    keys = set(updates)
    drop = set(removes or ())
    out: list[str] = []
    used: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            name = stripped.split("=", 1)[0].strip()
            if name in drop:
                continue
            if name in keys:
                out.append(f"{name}={_sanitize_env_value(updates[name])}")
                used.add(name)
                continue
        out.append(line)
    for name in keys - used:
        out.append(f"{name}={_sanitize_env_value(updates[name])}")
    c = _import_common({})
    c.atomic_write(str(env_path), "\n".join(out) + "\n")


_ENV_KEY_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _sanitize_env_value(value) -> str:
    """Значение .env: строка без переводов строк (M4)."""
    s = "" if value is None else str(value).strip()
    return s.replace("\n", " ").replace("\r", " ")


# LLM-подключение (host/model/api_key) — системная настройка: в
# pdir/.env пишутся только отличия от глобального эффективного значения
# (_persist_run_params), иначе глобальная смена сервера не доезжала
# бы до проектов с уже созданным pdir/.env
_LLM_CONN_FIELDS = ("host", "model", "api_key")


def _persist_run_params(ctx: dict, pdir: Path, stage: str,
                        params: dict) -> None:
    """Сохраняет настройки запуска стадии в .env проекта (R9).

    Если pdir/.env нет — копия системного корневого .env (или шаблона),
    затем обновляются ключи по env_keys_for. LLM-подключение
    (host/model/api_key) — по отклонениям: значение совпадает с
    глобальным эффективным (os.environ > системный .env) — локальный
    оверрайд удаляется (или не пишется), отличается — пишется
    <СТАДИЯ>_KEY. Поле не пришло (простой режим) — .env не трогается.
    api_key пишется в .env (локальный однопользовательский проект —
    удобство важнее сокрытия). Пустые значения НЕ пишутся; системный
    .env не трогается."""
    from web.stages import env_keys_for
    # noenv-поля (типы/поля чипсов, секреты) в .env не пишем
    spec = spec_for(stage)
    noenv = {f["name"] for f in (spec or {}).get("fields", [])
             if f.get("noenv")}
    # многстрочные regexp (textarea) в .env — одной строкой, переносы
    # как литерал «\\n» (одно значение .env — одна строка)
    textareas = {f["name"] for f in (spec or {}).get("fields", [])
                 if f.get("type") == "textarea"}
    updates: dict[str, str] = {}
    removes: set[str] = set()
    # глобальный эффективный LLM-конфиг стадии — база сравнения
    base_cfg: dict = {}
    if any(f in params for f in _LLM_CONN_FIELDS):
        c = _import_common(ctx)
        base_cfg = c.get_server_config(
            c.parse_dotenv(c.find_env_file()), stage)
    profile = str(params.get("profile") or "")
    for field, value in params.items():
        if field in noenv or value is None or value == "":
            continue
        keys = env_keys_for(stage, field, profile)
        if not keys:
            continue
        if field in _LLM_CONN_FIELDS:
            v = str(value).strip()
            if v == (base_cfg.get(field) or "").strip():
                # совпадает с глобальным — оверрайд не нужен
                removes.add(keys[0])
            else:
                updates[keys[0]] = v
            continue
        if isinstance(value, bool):
            updates[keys[0]] = "1" if value else "0"
        else:
            v = str(value).strip()
            if field in textareas:
                v = v.replace("\n", "\\n")
            updates[keys[0]] = v
    if not updates and not removes:
        return
    env_path = pdir / ".env"
    if not env_path.is_file():
        if not updates:
            return  # удалять нечего — файла нет (создавать ради удаления глупо)
        src = _sys_env_path(ctx)
        if not src.is_file():
            src = _repo_root(ctx) / "templates" / ".env.example"
        try:
            if src.is_file():
                # M1 (AUDIT): копия БЕЗ секретов — ключи остаются только
                # в системном projects/.env (fallback в _llm_argv)
                text = src.read_text(encoding="utf-8", errors="replace")
                text = _strip_secret_keys(text)
                env_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            log.debug("Не удалось скопировать .env в проект: %s", exc)
    _env_apply_keys(env_path, updates, removes)


def _strip_secret_keys(text: str) -> str:
    """Убирает значения секретных ключей (*_API_KEY, API_KEY) из текста
    .env (M1): строки остаются с пустым значением + комментарий.
    единый ключ API_KEY тоже секретный (не *_API_KEY).
    Системные WEB_* (настройки web-сервера и интерфейса) в проект НЕ
    копируются вовсе — в проектном .env они бесполезны (читает их только
    системный корневой .env)."""
    marker = "# (секрет не копируется в проект — M1, AUDIT)"
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        # синтетический маркер прошлой чистки — не дублируем
        # (рядом со следующим секретным ключом добавится заново)
        if stripped == marker:
            continue
        if stripped and not stripped.startswith("#") and "=" in stripped:
            name = stripped.split("=", 1)[0].strip()
            if name == "API_KEY" or name.upper().endswith("_API_KEY"):
                out.append(f"{name}=")
                out.append(marker)
                continue
            if name.startswith("WEB_"):
                # системная настройка web — не место в .env проекта
                continue
        out.append(line)
    return "\n".join(out) + ("\n" if out else "")


def _env_ctx(ctx: dict, scope: str) -> tuple[Path, str]:
    """(путь к .env, источник) для scope global/project."""
    if scope == "global":
        return _sys_env_path(ctx), "shared"
    pdir, _s, _n = _project_ctx(ctx)
    return pdir / ".env", "project"


def _jobs_list(ctx: dict) -> dict:
    """История запусков (GET /api/jobs)."""
    jm = _job_manager(ctx)
    return {"ok": True, "jobs": jm.list()}


def _jobs_get(ctx: dict) -> dict:
    """Детали запуска + хвост буфера (GET /api/jobs/{id})."""
    jm = _job_manager(ctx)
    job = jm.get(ctx["params"]["id"])
    if job is None:
        raise ApiError(404, "Запуск не найден")
    return {"ok": True, "job": _job_payload(job)}


def _jobs_stop(ctx: dict) -> dict:
    """Остановка (POST /api/jobs/{id}/stop): terminate → 5 c → kill."""
    jm = _job_manager(ctx)
    job = jm.stop(ctx["params"]["id"])
    if job is None:
        raise ApiError(404, "Запуск не найден")
    return {"ok": True, "status": job.status}


def _jobs_delete(ctx: dict) -> dict:
    """Удалить из истории (DELETE /api/jobs/{id})."""
    jm = _job_manager(ctx)
    if not jm.remove(ctx["params"]["id"]):
        raise ApiError(404, "Запуск не найден")
    return {"ok": True}


def _jobs_clear(ctx: dict) -> dict:
    """Очистить историю завершённых запусков (DELETE /api/jobs).
    Активные (running) не трогаются — остаются на дашборде."""
    jm = _job_manager(ctx)
    return {"ok": True, "cleared": jm.clear_finished()}


def _jobs_stream(ctx: dict) -> dict:
    """SSE-стрим лога (GET /api/jobs/{id}/stream)."""
    import json as _json
    jm = _job_manager(ctx)
    job = jm.get(ctx["params"]["id"])
    if job is None:
        raise ApiError(404, "Запуск не найден")
    handler = ctx["handler"]
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Accel-Buffering", "no")
    handler.send_header("Connection", "close")
    handler.end_headers()
    # SSE-стрим сам пишет ответ; конец потока — EOF для fetch:
    # второй JSON-ответ сервер дописывать не должен
    ctx["streamed"] = True
    handler.close_connection = True
    q = job.subscribe()
    try:
        # сразу — весь текущий буфер + события (для живой таблицы)
        for line in job.tail(5000):
            ev = _json.dumps({"type": "line", "text": line}, ensure_ascii=False)
            handler.wfile.write(f"data: {ev}\n\n".encode("utf-8"))
        for ev_item in list(job.events):
            ev = _json.dumps({"type": "event", "event": ev_item},
                             ensure_ascii=False)
            handler.wfile.write(f"data: {ev}\n\n".encode("utf-8"))
        # текущий прогресс — живое прикрепление сразу видит бар
        if job.progress:
            ev = _json.dumps({"type": "progress", "event": job.progress},
                             ensure_ascii=False)
            handler.wfile.write(f"data: {ev}\n\n".encode("utf-8"))
        # уже завершился до подписки — статус сразу и закрываем
        if job.status != "running":
            ev = _json.dumps({"type": "status", "status": job.status},
                             ensure_ascii=False)
            handler.wfile.write(f"data: {ev}\n\n".encode("utf-8"))
            handler.wfile.flush()
            return {}
        handler.wfile.flush()
        while True:
            try:
                ev_type, payload = q.get(timeout=15.0)
            except Exception:
                handler.wfile.write(b": ping\n\n")
                handler.wfile.flush()
                continue
            if ev_type == "line":
                ev = _json.dumps({"type": "line", "text": payload}, ensure_ascii=False)
            elif ev_type == "event":
                ev = _json.dumps({"type": "event", "event": payload},
                                 ensure_ascii=False)
            elif ev_type == "progress":
                ev = _json.dumps({"type": "progress", "event": payload},
                                 ensure_ascii=False)
            else:
                ev = _json.dumps({"type": "status", "status": payload}, ensure_ascii=False)
            handler.wfile.write(f"data: {ev}\n\n".encode("utf-8"))
            handler.wfile.flush()
            if ev_type == "status":
                break
    finally:
        job.unsubscribe(q)
    return {}


def _stage_spec(ctx: dict) -> dict:
    """Спека стадии (GET /api/stages/{key}/spec).

    R9: при project=sec/name поля предзаполняются по слоям конфига —
    системный корневой .env → собственный pdir/.env (по ключам) →
    os.environ (канон §7: окружение > файл). Секреты (api_key) —
    только из pdir/.env; приоритет .env-слоёв > дефолт спеки."""
    spec = spec_for(ctx["params"]["key"])
    if spec is None:
        raise ApiError(404, "Стадия не найдена")
    spec = copy.deepcopy(spec)  # не мутируем глобальный кэш спекаций
    # пресет простого режима: параметры считаются в web/stages.py
    # (дефолты полей формы + overrides) и уходят в спеку целиком
    if spec.get("preset") is not None:
        from web.stages import preset_params
        spec["preset"]["params"] = preset_params(spec)
    project = ctx["query"].get("project", "")
    if "/" in project:
        try:
            from web.stages import env_keys_for
            pdir, _sec, _name = _project_ctx(ctx)
            # автоподхвата compiled_chapters.txt больше нет — режим
            # «собрать главы» склеивает главы в память без файла
            c = _import_common(ctx)
            # Слои префилла (канон §7: окружение > файл; проект >
            # глобальный): системный корневой .env (дефолты для всех
            # проектов) → собственный pdir/.env (локальные переопределения,
            # по ключам) → os.environ по ключам-кандидатам полей. Так
            # HOST/API_KEY/MODEL из docker-compose environment доходят
            # до формы даже при сидированном pdir/.env.
            sys_env = c.parse_dotenv(c.system_env_file())
            proj_path = pdir / ".env"
            proj_env = c.parse_dotenv(
                str(proj_path) if proj_path.is_file() else None)
            stage_key = ctx["params"]["key"]
            cand: set[str] = set()
            for field in spec.get("fields", []):
                if not field.get("noenv"):
                    cand.update(env_keys_for(stage_key, field["name"]))
            env = c.env_overlay(
                {**sys_env, **proj_env},
                [k for k in cand
                 if k != "API_KEY" and not k.endswith("_API_KEY")])
            for field in spec.get("fields", []):
                if field.get("noenv"):
                    continue  # epub: многострочные regexp — только localStorage
                keys = env_keys_for(stage_key, field["name"])
                # секреты (api_key) в префилл отдаются ТОЛЬКО из
                # собственного файла проекта: ни os.environ, ни
                # системный .env в спеку не попадают (при --auth
                # маскировка /api/env их не прикрывает)
                src = proj_env if field["name"] == "api_key" else env
                for key in keys:
                    # пустое значение не забивает fallback-ключ
                    # (пустой PIPELINE_MODEL не прячет общую MODEL)
                    if key in src and str(src[key]) != "":
                        val = src[key]
                        if field.get("type") == "bool":
                            # D: строка "0" не должна быть truthy —
                            # чекбокс вспыхивает
                            field["default"] = str(val).strip().lower() in (
                                "1", "true", "yes", "on")
                        elif field.get("type") == "files":
                            # C: basename — NER_PROMPT_FILE=prompts/ner_prompt.txt
                            # → ner_prompt.txt (селект наполнен именами)
                            name = str(val).replace("\\", "/").rsplit("/", 1)[-1]
                            # автоподхват только реально существующих файлов:
                            # удалённый промпт не предзаполняется из .env
                            # (иначе «мёртвый» выбор ломает автоподхват)
                            d = field.get("dir") or ""
                            if not ((pdir / d if d else pdir) / name).is_file():
                                continue
                            field["default"] = name
                        elif field.get("type") == "textarea":
                            # многстрочные regexp в .env — одной строкой,
                            # переносы как литерал «\\n» (хвостовой
                            # перенос — артефакт кодирования)
                            field["default"] = str(val).replace(
                                "\\n", "\n").rstrip("\n")
                        else:
                            field["default"] = val
                        break
        except ApiError:
            raise
        except Exception as exc:  # noqa: BLE001 — .env необязателен
            log.debug("Предзаполнение формы из .env: %s", exc)
    return {"ok": True, "spec": spec}


# U8: кэш опций стадий — сигнатура mtime папок, влияющих на опции
# (chapters/source/prompts/корень). build_chapter_map на каждый запрос
# дорогой, а папки меняются редко; любое изменение — инвалидация.
_OPTIONS_CACHE: dict[str, tuple[tuple[float, ...], dict]] = {}


def _options_signature(pdir: Path) -> tuple[float, ...]:
    """Сигнатура для кэша опций стадий (U8).

    max mtime файлов в папке (а не mtime каталога): перезапись
    существующего файла (например, правка промпта с тегами) mtime
    каталога не меняет — иначе кэш опций (список промптов, auto_prompt)
    устаревал бы."""
    sig: list[float] = []
    for name in ("chapters", "source", "prompts"):
        d = pdir / name
        try:
            if d.is_dir():
                mt = 0.0
                for f in d.iterdir():
                    try:
                        mt = max(mt, f.stat().st_mtime)
                    except OSError:
                        continue
                sig.append(mt)
            else:
                sig.append(0.0)
        except OSError:
            sig.append(0.0)
    try:
        sig.append(pdir.stat().st_mtime)  # корень: ner.json и т.п.
    except OSError:
        sig.append(0.0)
    return tuple(sig)


def _stage_options(ctx: dict) -> dict:
    """Динамические опции стадии (GET /api/stages/{key}/options?project=)."""
    common = _import_common(ctx)
    spec = spec_for(ctx["params"]["key"])
    if spec is None:
        raise ApiError(404, "Стадия не найдена")
    out: dict = {"ok": True, "options": {}}
    project = ctx["query"].get("project", "")
    if "/" in project:
        pdir, _section, _name = _project_ctx(ctx)
        sig = _options_signature(pdir)
        cached = _OPTIONS_CACHE.get(str(pdir))
        if cached is not None and cached[0] == sig:
            out["options"] = cached[1]
            return out
        # диапазон глав
        chapters_dir = pdir / "chapters"
        if chapters_dir.is_dir():
            ch_map = common.build_chapter_map(chapters_dir)
            nums = sorted(ch_map)
            if nums:
                # ids — реальные главы (B10): таблица конвейера рисует
                # строки по списку, а не по диапазону min..max
                out["options"]["chapters"] = {
                    "min": nums[0], "max": nums[-1], "ids": nums}
        # файлы source/ — ВСЕ файлы (клиент фильтрует по ext селекта):
        # epub-исходники, txt, обложки (jpg/png/webp), metadata.yaml и т.п.
        src = pdir / "source"
        if src.is_dir():
            out["options"]["source"] = sorted(
                f.name for f in src.iterdir() if f.is_file())
        # файлы prompts/
        pr = pdir / "prompts"
        if pr.is_dir():
            out["options"]["prompts"] = sorted(
                f.name for f in pr.iterdir() if f.is_file())
        # pipeline: автоподхват общего промпт-файла с тегами — ровно
        # тот, что выберет auto-режим конвейера (первый существующий
        # кандидат из _PROMPT_COMBINED_CANDIDATES с тегами)
        if ctx["params"]["key"] == "pipeline":
            for cand in ("pipeline_prompt.txt", "prompts.txt",
                         "translate_book_prompt.txt"):
                f = pdir / "prompts" / cand
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if any(common.get_tagged_prompt(text, tag)
                       for tag in ("translate", "redact", "polish")):
                    out["options"]["auto_prompt"] = f"prompts/{cand}"
                    break
        # файлы корня проекта (для полей files с dir="");
        # dot-файлы (.env, .web_secret) не показываем — секреты
        root = sorted(f.name for f in pdir.iterdir()
                      if f.is_file() and not f.name.startswith("."))
        if root:
            out["options"]["root"] = root
        _OPTIONS_CACHE[str(pdir)] = (sig, out["options"])
    return out


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


def _stages_list(ctx: dict) -> dict:
    """Список стадий (GET /api/stages): key/title/script."""
    return {"ok": True, "stages": [
        {"key": k, "title": v["title"], "script": v["script"]}
        for k, v in ordered_stages()]}


# ════════════════════════════════════════════════════════════════════
# epub: предпросмотр разбивки (папки, размеры, удаление, текст)
# ════════════════════════════════════════════════════════════════════
EPUB_PREVIEW_FILE = "tmp/epub_preview.json"  # относит. cwd = папка проекта


def _epub_preview_path(pdir: Path) -> Path:
    return pdir / EPUB_PREVIEW_FILE


def _epub_preview_read(pdir: Path) -> dict:
    """JSON предпросмотра; нет файла/битый — пустой предпросмотр."""
    import json as _json
    path = _epub_preview_path(pdir)
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"source": "", "num_offset": 1, "title_limit": 50,
                "entries": []}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("source", "")
    data.setdefault("num_offset", 1)
    data.setdefault("title_limit", 50)
    data.setdefault("entries", [])
    return data


def _epub_preview_summary(data: dict) -> dict:
    """Публичное представление: папки + размеры (без текстов)."""
    entries = []
    for e in data.get("entries", []):
        text = e.get("text", "") or ""
        entries.append({
            "seq": e.get("seq"),
            "num": e.get("num"),
            "folder": e.get("folder", ""),
            "heading": e.get("heading", ""),
            "size_kb": round(len(text.encode("utf-8")) / 1024, 1),
        })
    return {"entries": entries}


def _epub_preview_run(ctx: dict, pdir: Path, params: dict,
                      skip: list) -> dict:
    """Запускает скрипт с --preview-json (синхронно); возвращает данные."""
    import subprocess
    import sys as _sys
    repo = Path(_repo_root(ctx))  # repo_root может прийти строкой
    script = script_path("epub", repo)
    if script is None or not script.is_file():
        raise ApiError(500, "Скрипт epub_to_chapters.py не найден")
    argv = build_command("epub", params, ctx)
    argv[0] = str(script)
    argv += ["--preview-json", EPUB_PREVIEW_FILE]
    for s in skip or []:
        argv += ["--skip", str(s)]
    try:
        proc = subprocess.run(
            [_sys.executable, *argv], cwd=str(pdir),
            capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise ApiError(500, "Предпросмотр не уложился в 120 c — "
                            "уменьшите исходник")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise ApiError(400, f"Ошибка разбивки: {err[:500]}")
    data = _epub_preview_read(pdir)
    if not data.get("entries"):
        raise ApiError(400, "Разбивка не дала ни одной главы")
    return data


def _epub_preview_post(ctx: dict) -> dict:
    """Создать/обновить предпросмотр (POST /api/stages/epub/preview)."""
    pdir, _sec, _name = _project_ctx(ctx)
    body = ctx["body"] or {}
    data = _epub_preview_run(ctx, pdir, body.get("params") or {},
                             body.get("skip") or [])
    return {"ok": True, "source": data.get("source", ""),
            **_epub_preview_summary(data)}


def _epub_preview_get(ctx: dict) -> dict:
    """Текущий предпросмотр (GET /api/stages/epub/preview)."""
    pdir, _sec, _name = _project_ctx(ctx)
    data = _epub_preview_read(pdir)
    return {"ok": True, "source": data.get("source", ""),
            **_epub_preview_summary(data)}


def _epub_preview_text(ctx: dict) -> dict:
    """Текст главы предпросмотра (GET .../preview/text?num=N)."""
    pdir, _sec, _name = _project_ctx(ctx)
    try:
        num = int(ctx["query"].get("num", ""))
    except (TypeError, ValueError):
        raise ApiError(400, "Параметр num обязателен (номер главы)")
    data = _epub_preview_read(pdir)
    for e in data.get("entries", []):
        if e.get("num") == num:
            return {"ok": True, "heading": e.get("heading", ""),
                    "text": e.get("text", "")}
    raise ApiError(404, f"Глава {num} не найдена в предпросмотре")


def _epub_preview_folder_delete(ctx: dict) -> dict:
    """Удалить главу из предпросмотра + перенумерация
    (DELETE .../preview/folder?seq=N; seq — исходный порядок)."""
    pdir, _sec, _name = _project_ctx(ctx)
    try:
        seq = int(ctx["query"].get("seq", ""))
    except (TypeError, ValueError):
        raise ApiError(400, "Параметр seq обязателен")
    data = _epub_preview_read(pdir)
    entries = [e for e in data.get("entries", [])
               if e.get("seq") != seq]
    if len(entries) == len(data.get("entries", [])):
        raise ApiError(404, f"Секция {seq} не найдена в предпросмотре")
    # перенумерация: каталоги нумеруются по порядку от num_offset,
    # префикс — ширина 6 (00000_1, 0000_12, 000_177…)
    try:
        offset = int(data.get("num_offset", 1))
    except (TypeError, ValueError):
        offset = 1
    for i, e in enumerate(entries):
        num = offset + i
        e["num"] = num
        parts = str(e.get("folder", "")).split("_", 2)
        if len(parts) == 3:
            zeros = "0" * max(0, 6 - len(str(num)))
            e["folder"] = f"{zeros}_{num}_{parts[2]}"
    data["entries"] = entries
    import json as _json
    common = _import_common(ctx)
    common.atomic_write(str(_epub_preview_path(pdir)),
                        _json.dumps(data, ensure_ascii=False, indent=1))
    return {"ok": True, "source": data.get("source", ""),
            **_epub_preview_summary(data)}


def _batch_replace_preview(ctx: dict) -> dict:
    """Предпросмотр замен в одной главе
    (POST /api/stages/batch_replace/preview).

    Тело: {project, type, chapter, replacements}. Правила парсятся и
    применяются тем же путём, что реальный запуск
    (cli.batch_replace.parse_replace_lines + apply_rules_segments) —
    файлы не изменяются. Пустые правила — текст главы без изменений.
    Возвращает segments (keep/del/ins) итогового текста и счётчики
    замен по правилам.
    """
    pdir, _sec, _name = _project_ctx(ctx)
    br = _import_batch_replace()
    common = _import_common(ctx)
    body = ctx["body"] or {}
    ftype = str(body.get("type") or "polished")
    if ftype not in br.FILE_TYPES:
        raise ApiError(400, f"Неизвестный тип файлов глав: {ftype}")
    try:
        num = int(body.get("chapter") or "")
    except (TypeError, ValueError):
        raise ApiError(400, "Номер главы (chapter) обязателен")
    replacements = body.get("replacements")
    if isinstance(replacements, str):
        lines = [ln for ln in replacements.splitlines() if ln.strip()]
    elif isinstance(replacements, list):
        lines = [str(x) for x in replacements if str(x).strip()]
    else:
        lines = []
    rules, warnings = br.parse_replace_lines(lines)
    if lines and not rules:
        raise ApiError(400, "В форме нет ни одной корректной замены"
                            + (": " + "; ".join(warnings) if warnings else ""))
    chapters_dir = pdir / "chapters"
    ch_map = common.build_chapter_map(chapters_dir)
    dirs = ch_map.get(num)
    if not dirs:
        raise ApiError(404, f"Глава {num} не найдена")
    filepath, warns = common.find_chapter_file(dirs[0], num,
                                               want=ftype, strict=True,
                                               strict_types=True)
    warnings += [w for w in warns if w not in warnings]
    if filepath is None:
        raise ApiError(404, f"В главе {num} нет файла типа {ftype}")
    content = common.read_text_safe(filepath)
    segments, stats = br.apply_rules_segments(content, rules)
    return {"ok": True, "num": num, "dir": Path(dirs[0]).name,
            "type": ftype,
            "changed": bool(stats),
            "stats": [{"label": label, "count": cnt}
                      for label, cnt in sorted(stats.items(),
                                               key=lambda x: -x[1])],
            "warnings": warnings,
            "segments": segments}


# ════════════════════════════════════════════════════════════════════
# LLM-стадии: предпросмотр первого запроса (--preview-request)
# ════════════════════════════════════════════════════════════════════
PREVIEW_REQUEST_FILE = "tmp/preview_request.json"  # относит. cwd = проект
# стадии, чьи скрипты поддерживают --preview-request
_PREVIEW_STAGES = {"pipeline", "ner", "ner_check", "translate_check_llm",
                   "translate_quality", "wiki"}


def _preview_request_post(ctx: dict) -> dict:
    """Предпросмотр первого LLM-запроса стадии
    (POST /api/stages/{key}/preview-request).

    Тело: {project, params}. Синхронный запуск скрипта стадии с
    --preview-request tmp/preview_request.json (без сети, cwd=проект);
    артефакты и логи запуска не создаются. Возвращает payload:
    stage, label, model, messages, chars (символы), meta."""
    import subprocess
    import sys as _sys
    key = ctx["params"]["key"]
    if key not in _PREVIEW_STAGES:
        raise ApiError(404, f"Стадия {key!r} не поддерживает "
                            f"предпросмотр запроса")
    pdir, _sec, _name = _project_ctx(ctx)
    repo = Path(_repo_root(ctx))
    script = script_path(key, repo)
    if script is None or not script.is_file():
        raise ApiError(500, "Скрипт стадии не найден")
    params = (ctx["body"] or {}).get("params") or {}
    argv = build_command(key, params, ctx)
    argv[0] = str(script)
    argv += ["--preview-request", PREVIEW_REQUEST_FILE]
    try:
        proc = subprocess.run(
            [_sys.executable, *argv], cwd=str(pdir),
            capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise ApiError(500, "Предпросмотр не уложился в 300 c")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise ApiError(400, f"Ошибка предпросмотра: {err[:500]}")
    path = pdir / PREVIEW_REQUEST_FILE
    import json as _json
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ApiError(500, "Файл предпросмотра не создан или битый")
    if not isinstance(data, dict) or not data.get("messages"):
        raise ApiError(500, "Предпросмотр не содержит сообщений")
    return {"ok": True, **data}



def _register_jobs(router: Router) -> None:
    router.add("GET", "/api/stages", _stages_list)
    router.add("POST", "/api/jobs", _jobs_start)
    router.add("GET", "/api/jobs", _jobs_list)
    router.add("DELETE", "/api/jobs", _jobs_clear)
    router.add("GET", "/api/jobs/{id}", _jobs_get)
    router.add("POST", "/api/jobs/{id}/stop", _jobs_stop)
    router.add("DELETE", "/api/jobs/{id}", _jobs_delete)
    router.add("GET", "/api/jobs/{id}/stream", _jobs_stream)
    router.add("GET", "/api/stages/{key}/spec", _stage_spec)
    router.add("GET", "/api/stages/{key}/options", _stage_options)
    # epub: предпросмотр разбивки (папки/размеры/удаление/текст)
    router.add("POST", "/api/stages/epub/preview", _epub_preview_post)
    router.add("GET", "/api/stages/epub/preview", _epub_preview_get)
    router.add("GET", "/api/stages/epub/preview/text", _epub_preview_text)
    router.add("DELETE", "/api/stages/epub/preview/folder",
               _epub_preview_folder_delete)
    router.add("POST", "/api/stages/batch_replace/preview",
               _batch_replace_preview)
    # LLM-стадии: предпросмотр первого запроса
    router.add("POST", "/api/stages/{key}/preview-request",
               _preview_request_post)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_common.py — общие хендлеры REST-слоя: сессия и вход, пути и
ctx, multipart и константы веб-слоя.

Хендлеры получают ctx {params, query, body, handler, auth, authenticated,
repo_root, projects_root} и возвращают dict для JSON-ответа (200) или бросают
ApiError.

Кеш stats без TTL: вместо времени — сигнатура состояния (mtime папок глав +
ner/wiki + compiled). При каждом чтении считаем сигнатуру (это scandir одного
уровня, а НЕ полный обход глав) и пересчитываем проект только если сигнатура
изменилась. Так кеш всегда актуален, ловит даже внешние правки файлов и
переживает рестарт сервера (дисковой кеш).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

from core import common
from core import transport
from web.auth import COOKIE_NAME
from web.multipart import (
    MultipartError, extract_files, extract_value, iter_parts,
    parse_disposition,
)
from web.sandbox import SandboxError, resolve_path
from web.server import ApiError
from web.version import app_version

log = logging.getLogger("web")

# ── проверка обновлений: кеш последнего ответа GitHub (в процессе) ──
# Сеть ходит только по кнопке «Проверить» (POST /api/update/check); сессия
# и интерфейс читают кеш. checked — unix-время последней успешной проверки.
_UPDATE_CACHE: dict = {"release": None, "checked": 0.0}
# Кеш stats без TTL: вместо времени — сигнатура состояния (mtime папок
# глав + ner/wiki + compiled). При каждом чтении считаем сигнатуру (это
# scandir 1 уровня, а НЕ полный обход глав) и пересчитываем проект только
# если сигнатура изменилась. Так кеш всегда актуален, ловит даже внешние
# правки файлов и переживает рестарт сервера (дисковый кеш).
_STATS_CACHE: dict[str, dict] = {}   # key "sec/name" → {"sig", "stats"}
_STATS_LOCK = threading.Lock()
_STATS_CACHE_FILE = ".stats_cache.json"  # в корне projects/ (рядом с hub_state)
# версия методики расчёта статуса: изменение project_progress_table
# делает старые записи кеша неверными (сигнатура mtime их не ловит)
_STATUS_CACHE_VER = 2
_CACHE_LOADED: set[str] = set()      # корни, для которых загружен дисковой кеш
UPLOAD_DIRS = ("source", "chapters", "prompts", "images", "tmp")
# Текстовое поле формы (без filename) крупнее — подозрительный запрос
MAX_TEXT_FIELD = 1024 * 1024
BINARY_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".epub",
              ".zip", ".fb2", ".ttf", ".otf", ".woff", ".woff2")
TEXT_EXT = (".txt", ".md", ".json", ".yaml", ".yml", ".env", ".log",
            ".csv", ".xml", ".html", ".css", ".js", ".py")
FILE_TEXT_LIMIT = 5 * 1024 * 1024  # > 5 МБ — редактор не открывает, скачивание
# максимум каталогов в дереве проекта: по нему SPA строит выбор
# папки назначения при переносе выделенного (диалог не должен разрастаться)
DIR_TREE_LIMIT = 500
# ════════════════════════════════════════════════════════════════════
# NER, review, конфиги, промпты
# ════════════════════════════════════════════════════════════════════
NER_TEXT_LIMIT = 10 * 1024 * 1024  # > 10 МБ — не JSON, а скачивание
# ══════════════════════════════════════════════════════════════
# Обложка
# ══════════════════════════════════════════════════════════════
COVER_NAMES = ("cover.jpg", "cover.png", "cover.jpeg")
COVER_MAX_BYTES = 8 * 1024 * 1024  # 8 МБ
# ════════════════════════════════════════════════════════════════════
# Логи
# ════════════════════════════════════════════════════════════════════
LOG_TAIL_LIMIT = 1024 * 1024  # максимум 1 МБ на просмотр
# ════════════════════════════════════════════════════════════════════
# Регистрация роутов
# ════════════════════════════════════════════════════════════════════
# Отчёты translate_check
# ════════════════════════════════════════════════════════════════════
CHECK_REPORT_LIMIT = 512 * 1024  # читаем не больше 512 КБ на отчёт
# U8: кэш опций стадий — сигнатура mtime папок, влияющих на опции
# (chapters/source/prompts/корень). build_chapter_map на каждый запрос
# дорогой, а папки меняются редко; любое изменение — инвалидация.
_OPTIONS_CACHE: dict[str, tuple[tuple[float, ...], dict]] = {}
# ════════════════════════════════════════════════════════════════════
# epub: предпросмотр разбивки (папки, размеры, удаление, текст)
# ════════════════════════════════════════════════════════════════════
EPUB_PREVIEW_FILE = "tmp/epub_preview.json"  # относит. cwd = папка проекта
# ════════════════════════════════════════════════════════════════════
# LLM-стадии: предпросмотр первого запроса (--preview-request)
# ════════════════════════════════════════════════════════════════════
PREVIEW_REQUEST_FILE = "tmp/preview_request.json"  # относит. cwd = проект
# стадии, чьи скрипты поддерживают --preview-request
_PREVIEW_STAGES = {"pipeline", "ner", "ner_check", "translate_check_llm",
                   "translate_quality", "wiki"}

# ════════════════════════════════════════════════════════════════════
# Служебные
# ════════════════════════════════════════════════════════════════════
def _projects_root(ctx: dict) -> Path:
    """Корень projects/ — общий для всех доменных хендлеров."""
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
    """Ленивый импорт core.common (для обратной совместимости)."""
    return common


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


def _session(ctx: dict) -> dict:
    return {
        "ok": True,
        "authenticated": ctx["authenticated"],
        "token_set": ctx["auth"].token_set(),
        "host": ctx["host"],
        "version": app_version(),
        "update": _update_state(),
    }


def _update_state() -> dict:
    """Сводка обновления для SPA: способ обновления этой установки + кеш

    последней проверки. Сеть НЕ ходит (сессию спрашивают часто): свежий
    релиз подтягивает POST /api/update/check. Форма установки:
    docker (образ из ghcr), portable (Windows-сборка из zip), git (репозиторий).
    """
    kind = _install_kind()
    cached = _UPDATE_CACHE["release"]
    if cached and common.update_available(app_version(), cached):
        note = "Доступна новая версия"
    elif cached:
        note = ""
    else:
        note = ""
    return {
        "kind": kind,
        "current": app_version(),
        "release": cached,
        "checked": _UPDATE_CACHE["checked"],
        "available": bool(cached) and common.update_available(
            app_version(), cached),
        "note": note,
    }


def _install_kind() -> str:
    """Как установлена эта копия: docker | portable | git (см. _kind_at)."""
    return _kind_at(Path(__file__).resolve().parent.parent)


def _kind_at(root: Path) -> str:
    """Способ установки по маркерам в корне кода.

    docker — маркер-файл .docker, который создаёт Dockerfile (/app/.docker);
    portable — START.txt, который кладёт упаковщик портативной сборки;
    иначе — запуск из git-репозитория.
    """
    if (root / ".docker").exists():
        return "docker"
    if (root / "START.txt").exists():
        return "portable"
    return "git"


def _update_check(ctx: dict) -> dict:
    """Проверить обновление сейчас (POST /api/update/check).

    Один короткий запрос к GitHub Releases; результат кешируется в
    процессe — сессия отдаёт его без сети. Недоступная сеть — не ошибка
    запроса: вердикт «сейчас не проверить» в ответе.
    """
    release = common.latest_release()
    if release.get("ok"):
        _UPDATE_CACHE["release"] = release
        _UPDATE_CACHE["checked"] = time.time()
        return {"ok": True, "update": _update_state()}
    return {"ok": False, "error": release.get("error") or "Сеть недоступна"}


def _update_download(ctx: dict) -> dict:
    """Страница обновления (GET /api/update/download): для портативной

    сборки и git-установки — свежий zip релиза как attachment; для Docker
    обновление идёт через реестр образов (400 с объяснением).
    """
    kind = _install_kind()
    if kind == "docker":
        raise ApiError(
            400,
            "Установка в Docker обновляется образом: выполните на хосте "
            "«docker compose pull && docker compose up -d»")
    release = _UPDATE_CACHE["release"]
    if not release or not release.get("tag"):
        raise ApiError(400, "Обновление ещё не проверялось — нажмите «Проверить»")
    tag = release["tag"]
    data = _download_release_asset(tag)
    name = f"novelmaestro-portable-{tag[1:] if tag.startswith('v') else tag}.zip"
    ctx["handler"]._send(
        200, "application/zip", data,
        [("Content-Disposition", f'attachment; filename="{name}"')])
    return {}  # ответ уже отправлен


def _download_release_asset(tag: str) -> bytes:
    """Ассет портативной сборки релиза (zip) — GET по API релиза.

    Имя ассета публикует воркфлоу windows.yml
    (novelmaestro-portable-<версия>.zip); зеркало — прямая ссылка
    «Downloads» страницы релиза. Без ключа: репозиторий публичный.
    """
    assets_url = f"https://api.github.com/repos/{common.GITHUB_REPO}/releases/tags/{tag}"
    data = transport.open_json_get(
        assets_url, timeout=common.UPDATE_CHECK_TIMEOUT)
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.startswith("novelmaestro-portable-") and name.endswith(".zip"):
            url = str(asset.get("browser_download_url") or "")
            if not url:
                break
            return _download_bytes(url)
    raise ApiError(404, f"В релизе {tag} нет архива портативной сборки")


def _download_bytes(url: str) -> bytes:
    """Бинарный GET (zip релиза): общий транспорт, follow — GitHub отдаёт
    ассет редиректом на свой CDN; тело — `iter_bytes()` (сырое: переводы
    строк zip не трогаются), таймаут щедрее проверки, но конечный."""
    status, body = 0, b""
    with transport.open_get(url, timeout=120.0,
                            follow_redirects=True) as resp:
        status = resp.status_code
        body = b"".join(resp.iter_bytes())
    if status != 200:
        raise ApiError(502, f"GitHub отдал HTTP {status} на скачивание")
    return body


def _login(ctx: dict) -> dict:
    # много неудачных входов за минуту → 429
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
# Пульт и проекты
# ════════════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════════════
# Файлы
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


def _sys_env_path(ctx: dict) -> Path:
    """Системный (общий) .env: WEB_ENV_FILE (в образе Docker —
    /app/projects/.env внутри постоянного тома — правки вкладки
    «Настройки» переживают обновление образа) → корневой .env репо."""
    override = os.environ.get("WEB_ENV_FILE", "").strip()
    if override:
        return Path(override)
    return _repo_root(ctx) / ".env"

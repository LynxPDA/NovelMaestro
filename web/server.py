#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server.py — HTTP-сервер web-бэкэнда.

ThreadingHTTPServer + собственный роутер (метод + шаблон пути с
{параметрами}), раздача статики SPA, JSON-ответы, обработка 404/405/500,
аутентификация и CSRF-проверка. Только stdlib.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import mimetypes
import re
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

from web.auth import COOKIE_NAME, Auth, csrf_ok

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_JSON_BODY = 16 * 1024 * 1024  # 16 МБ — лимит JSON-тел

# Версионирование ассетов SPA: index.html раздаётся всегда свежим
# (no-store), но ссылки на js/css получают ?v=<sha256> — тогда сами
# ассеты можно кэшировать в браузере навсегда (immutable): новая
# версия файла = новый URL, устаревший кэш никогда не используется.
_ASSET_URL_RE = re.compile(rb'((?:src|href)=")(/[^"?#]+\.(?:js|css))(")')
_ASSET_VERSION_CACHE: dict[str, tuple[int, str]] = {}

log = logging.getLogger("web")


class ApiError(Exception):
    """Ошибка API с HTTP-статусом и человекочитаемой причиной."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# ════════════════════════════════════════════════════════════════════
# Роутер
# ════════════════════════════════════════════════════════════════════
class Router:
    """Диспетчер: (метод, шаблон пути) → хендлер.

    Шаблон: "/api/projects/{sec}/{name}/stats"; сегменты {name} становятся
    параметрами. Путь сравнивается по сегментам без учёта пустых.
    """

    def __init__(self) -> None:
        self._routes: list[
            tuple[str, tuple[str, ...], Callable[[dict], dict]]
        ] = []

    def add(self, method: str, pattern: str, handler: Callable[[dict], dict]) -> None:
        parts = tuple(p for p in pattern.split("/") if p)
        self._routes.append((method.upper(), parts, handler))

    def dispatch(self, method: str, parts: list[str]):
        """Возвращает (handler, params) или (None, None)."""
        method = method.upper()
        for m, pat, handler in self._routes:
            if m != method or len(pat) != len(parts):
                continue
            params: dict[str, str] = {}
            ok = True
            for pat_seg, got_seg in zip(pat, parts):
                if pat_seg.startswith("{"):
                    params[pat_seg[1:-1]] = urllib.parse.unquote(got_seg)
                elif pat_seg != got_seg:
                    ok = False
                    break
            if ok:
                return handler, params
        return None, None

    def has_path(self, parts: list[str]) -> bool:
        """Есть ли маршрут по пути (для ответа 405 вместо 404)."""
        for m, pat, _ in self._routes:
            if len(pat) == len(parts):
                for pat_seg, got_seg in zip(pat, parts):
                    if not pat_seg.startswith("{") and pat_seg != got_seg:
                        break
                else:
                    return True
        return False


# ════════════════════════════════════════════════════════════════════
# Обработчик запросов
# ════════════════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    server_version = "web/0.1.1"
    protocol_version = "HTTP/1.1"

    # ── служебное ──────────────────────────────────────────────
    def log_message(self, format: str, *args) -> None:
        log.info("%s %s", self.address_string(), format % args)

    def _send(self, status: int, ctype: str, body: bytes,
              extra_headers: list[tuple[str, str]] | None = None,
              cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for k, v in extra_headers or []:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict,
                   extra_headers: list[tuple[str, str]] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body, extra_headers)

    def set_cookie(self, name: str, value: str) -> None:
        self._extra_headers.append(
            ("Set-Cookie", f"{name}={value}; Path=/; HttpOnly; SameSite=Strict")
        )

    def clear_cookie(self, name: str) -> None:
        self._extra_headers.append(
            ("Set-Cookie", f"{name}=; Path=/; Max-Age=0")
        )

    def session_id(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            if part.strip().startswith(COOKIE_NAME + "="):
                return part.strip().split("=", 1)[1]
        return None

    def _read_json_body(self) -> dict:
        try:
            cl = int(self.headers.get("Content-Length", "0") or 0)
        except (ValueError, TypeError):
            raise ApiError(400, "Некорректный Content-Length")
        if cl == 0:
            return {}
        if cl < 0:
            # rfile.read(-1) читал бы до EOF — поток зависал (DoS)
            raise ApiError(400, "Некорректный Content-Length")
        if cl > MAX_JSON_BODY:
            raise ApiError(413, "Тело запроса слишком большое")
        raw = self.rfile.read(cl)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "Некорректный JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "Тело запроса должно быть JSON-объектом")
        return data

    # ── диспетчеризация ────────────────────────────────────────
    def _dispatch(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        parts = [p for p in path.split("/") if p]
        qs = {
            k: v[-1] for k, v in
            urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()
        }
        self._extra_headers: list[tuple[str, str]] = []
        try:
            if path.startswith("/api/"):
                self._handle_api(parts, qs)
            else:
                self._serve_static(path)
        except ApiError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
        except OSError as exc:
            # обрыв соединения — не ошибка сервера
            log.debug("Соединение оборвано: %s", exc)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка обработки %s %s", self.command, path)
            self._send_json(500, {"ok": False, "error": "Внутренняя ошибка сервера"})

    def _handle_api(self, parts: list[str], qs: dict) -> None:
        srv = cast(WebServer, self.server)
        auth_obj = srv.auth
        # аутентификация (исключения — /api/session и /api/login)
        sid = self.session_id()
        authenticated = auth_obj.valid_session(sid)
        if not auth_obj.no_auth and path_not_public(self.path) \
                and not authenticated:
            self._send_json(401, {"ok": False, "error": "Требуется вход"})
            return
        # CSRF: мутирующие методы
        if self.command in ("POST", "PUT", "DELETE") and not csrf_ok(self):
            self._send_json(403, {"ok": False, "error": "CSRF-заголовок отсутствует"})
            return
        handler_fn, params = srv.router.dispatch(self.command, parts)
        if handler_fn is None:
            if srv.router.has_path(parts):
                self._send_json(405, {"ok": False, "error": "Метод не поддерживается"})
            else:
                self._send_json(404, {"ok": False, "error": "Не найдено"})
            return
        raw_ctype = self.headers.get("Content-Type") or ""
        ctype = raw_ctype.lower()
        ctx: dict = {
            "params": params or {},
            "query": qs,
            "handler": self,
            "auth": auth_obj,
            "authenticated": authenticated,
            "host": srv.host,
            "repo_root": srv.repo_root,
            "projects_root": srv.projects_root,
            "raw_body": b"",
            "content_type": ctype,
        }
        if self.command in ("POST", "PUT", "DELETE"):
            if ctype.startswith("multipart/form-data"):
                # тело НЕ читаем в память: хендлер забирает его из rfile
                # потоково (upload), здесь — только валидация длины
                ctx["raw_body"] = b""
                ctx["body"] = {}
                ctx["upload_length"] = self._check_upload_length()
                # boundary регистрозависим — отдаём как в заголовке
                if "boundary=" in raw_ctype:
                    ctx["boundary"] = raw_ctype.split("boundary=", 1)[1]\
                        .strip().strip('"').strip("'").split(";")[0]
            else:
                ctx["body"] = self._read_json_body()
        else:
            ctx["body"] = {}
        result = handler_fn(ctx)
        # SSE-стримы сами пишут ответ и закрывают соединение —
        # второй JSON-ответ дописывать нельзя
        if ctx.get("streamed"):
            return
        self._send_json(200, result, self._extra_headers)

    def _check_upload_length(self) -> int:
        """Валидация Content-Length multipart-тела (без чтения байт).

        Отрицательный — 400 (#read(-1) зависал бы), больше max_upload_mb
        — 413 (#H5 AUDIT: лимит — max_upload_mb (--max-upload-mb), а не
        MAX_JSON_BODY (16 МБ); иначе загрузка 16–512 МБ давала 413).
        Тело затем читает хендлер потоково (upload).
        """
        try:
            cl = int(self.headers.get("Content-Length", "0") or 0)
        except (ValueError, TypeError):
            raise ApiError(400, "Некорректный Content-Length")
        if cl < 0:
            # rfile.read(-1) читал бы до EOF — поток зависал (DoS)
            raise ApiError(400, "Некорректный Content-Length")
        if cl == 0:
            return 0
        limit = getattr(self.server, "max_upload_mb", 512) * 1024 * 1024
        if cl > limit:
            raise ApiError(
                413, f"Тело запроса слишком большое (лимит {limit // (1024 * 1024)} МБ)")
        return cl

    # ── статика SPA ────────────────────────────────────────────
    def _serve_static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        target = (STATIC_DIR / rel).resolve(strict=False)
        # is_relative_to, а НЕ startswith: префиксная проверка пропускала
        # «соседей» (web/static_evil.txt) через /../static_evil.txt
        if not target.is_relative_to(STATIC_DIR) or not target.is_file():
            self._send(404, "text/plain; charset=utf-8", b"Not Found")
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        try:
            data = target.read_bytes()
        except OSError:
            self._send(404, "text/plain; charset=utf-8", b"Not Found")
            return
        if rel == "index.html":
            # index.html — всегда свежий (no-store по умолчанию), но
            # ссылки на ассеты версионируются ?v=<sha256>: кэш браузера
            # по ассетам живёт вечно (immutable), смена файла = смена
            # URL — устаревших версий не бывает
            html = _ASSET_URL_RE.sub(self._version_asset, data)
            self._send(200, ctype, html)
            return
        # ассеты: immutable-кэш (URL несёт ?v= из index.html) + gzip,
        # когда клиент умеет; Vary — чтобы кэши не мешали варианты
        headers = [("Vary", "Accept-Encoding")]
        if "gzip" in self.headers.get("Accept-Encoding", ""):
            gz = gzip.compress(data, 6)
            if len(gz) < len(data):
                data = gz
                headers.append(("Content-Encoding", "gzip"))
        self._send(200, ctype, data, headers,
                   cache="max-age=31536000, immutable")

    def _version_asset(self, m: re.Match) -> bytes:
        """Замена src/href="/x.js" → "?v=<sha256 содержимого>" в index.html."""
        url = m.group(2).decode("utf-8")
        target = (STATIC_DIR / url.lstrip("/")).resolve(strict=False)
        if not target.is_file():
            return m.group(0)
        try:
            st = target.stat()
        except OSError:
            return m.group(0)
        cached = _ASSET_VERSION_CACHE.get(str(target))
        if cached is None or cached[0] != st.st_mtime_ns:
            try:
                ver = hashlib.sha256(target.read_bytes()).hexdigest()[:12]
            except OSError:
                return m.group(0)
            _ASSET_VERSION_CACHE[str(target)] = (st.st_mtime_ns, ver)
        else:
            ver = cached[1]
        return m.group(1) + url.encode() + f"?v={ver}".encode() + m.group(3)

    # ── HTTP-методы ────────────────────────────────────────────
    do_GET = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch


def path_not_public(path: str) -> bool:
    """Публичные пути (без сессии): /api/session и /api/login."""
    return path not in ("/api/session", "/api/login")


# ════════════════════════════════════════════════════════════════════
# Фабрика сервера
# ════════════════════════════════════════════════════════════════════
class WebServer(ThreadingHTTPServer):
    """Сервер с подключёнными auth и роутером (атрибуты задаёт make_server)."""

    auth: Auth
    router: Router
    host: str = "127.0.0.1"
    max_upload_mb: int = 512
    jobs_limit: int = 2
    repo_root: Path | None = None
    projects_root: Path | None = None
    # JobManager присваивается main.py / тестами; импорт не нужен (аннотация)
    job_manager: "object | None" = None


def make_server(host: str, port: int, auth_obj: Auth,
                router: Router | None = None,
                repo_root: Path | None = None,
                projects_root: Path | None = None) -> WebServer:
    """Создаёт ThreadingHTTPServer с подключёнными auth и роутером."""
    srv = WebServer((host, port), Handler)
    srv.auth = auth_obj
    srv.host = host
    srv.router = router if router is not None else Router()
    srv.repo_root = repo_root
    srv.projects_root = projects_root
    return srv

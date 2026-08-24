#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auth.py — токен-аутентификация, сессии и CSRF-проверка web-бэкэнда.

Токен сравнивается constant-time (hmac.compare_digest). Сессии — случайные
id в памяти сервера; cookie HttpOnly + SameSite=Strict. Мутирующие запросы
требуют заголовок X-Requested-With: fetch (или Origin == Host).
"""
from __future__ import annotations

import hmac
import secrets
import threading
import time

COOKIE_NAME = "web_session"

# M3 (AUDIT): сессии живут не вечно, вход ограничен по частоте
SESSION_TTL = 14 * 24 * 3600   # скользящий TTL сессии, сек (14 дней)
LOGIN_WINDOW = 60.0            # окно rate-limit, сек
LOGIN_MAX_FAILS = 10           # неудачных входов за окно → блок


class Auth:
    """Хранит токен, таблицу сессий и флаг no-auth."""

    def __init__(self, token: str | None = None, no_auth: bool = False) -> None:
        self.token = token or ""
        self.no_auth = no_auth
        self._sessions: dict[str, float] = {}  # sid → last_seen (M3)
        self._fails: list[float] = []          # метки неудачных входов (M3)
        self._lock = threading.Lock()

    # ── токен ────────────────────────────────────────────────
    def token_set(self) -> bool:
        return bool(self.token)

    def check_token(self, candidate: str) -> bool:
        """Constant-time сравнение токена; без токена — всегда True."""
        if not self.token:
            return True
        return hmac.compare_digest(
            self.token.encode("utf-8"), candidate.strip().encode("utf-8")
        )

    # ── сессии ───────────────────────────────────────────────
    def issue_session(self) -> str:
        sid = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._sessions[sid] = now
        return sid

    def valid_session(self, sid: str | None) -> bool:
        if self.no_auth:
            return True
        if not sid:
            return False
        now = time.time()
        with self._lock:
            last = self._sessions.get(sid)
            if last is None or now - last > SESSION_TTL:
                self._sessions.pop(sid, None)  # протухшая — удаляется
                return False
            self._sessions[sid] = now  # скользящий TTL
            return True

    def invalidate_session(self, sid: str | None) -> None:
        if not sid:
            return
        with self._lock:
            self._sessions.pop(sid, None)

    # ── rate-limit входа (M3) ─────────────────────────────────
    def login_failure(self) -> bool:
        """Отметить неудачный вход; True — лимит превышен (429)."""
        now = time.time()
        with self._lock:
            self._fails = [t for t in self._fails
                           if now - t <= LOGIN_WINDOW]
            self._fails.append(now)
            return len(self._fails) >= LOGIN_MAX_FAILS  # M3: предел достигнут

    def login_blocked(self) -> bool:
        """Слишком много неудачных входов за окно? (429 до проверки)."""
        now = time.time()
        with self._lock:
            self._fails = [t for t in self._fails
                           if now - t <= LOGIN_WINDOW]
            return len(self._fails) >= LOGIN_MAX_FAILS  # M3: предел достигнут


def csrf_ok(handler) -> bool:
    """Мутирующие запросы должны прийти из SPA: заголовок или Origin == Host."""
    if handler.headers.get("X-Requested-With", "").lower() == "fetch":
        return True
    origin = handler.headers.get("Origin")
    host = handler.headers.get("Host", "")
    return bool(origin) and origin == f"http://{host}"

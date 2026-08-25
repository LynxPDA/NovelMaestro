#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты web-сервера (M1): сессия, вход, статика, CSRF, 404/405, no-auth.

Сервер поднимается в фоновом потоке на свободном порту; запросы — http.client
без внешних зависимостей и без сети.
"""
import http.client
import json
import threading
from typing import Any

import pytest

from web import api as web_api
from web.auth import Auth
from web.server import make_server


@pytest.fixture()
def srv_ctx():
    """Фабрика: поднимает сервер на порту 0 и гасит его после теста."""
    servers = []

    def _make(auth_obj, host="127.0.0.1"):
        srv = make_server(host, 0, auth_obj)
        web_api.register(srv.router, host)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        servers.append(srv)
        return srv, srv.server_address[1]

    yield _make
    for srv in servers:
        srv.server_close()


def _request(port, method, path, body=None, cookie=None,
             xrw: str | None = "fetch") -> tuple[Any, dict]:
    """Запрос к серверу; возвращает (response, payload)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {}
    if cookie:
        headers["Cookie"] = cookie
    if xrw is not None:
        headers["X-Requested-With"] = xrw
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, data, headers)
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    payload: dict = {}
    try:
        decoded = json.loads(raw.decode("utf-8"))
        if isinstance(decoded, dict):
            payload = decoded
    except (ValueError, UnicodeDecodeError):
        pass
    return res, payload


# ════════════════════════════════════════════════════════════════════
# сессия и вход
# ════════════════════════════════════════════════════════════════════
def test_session_unauthenticated(srv_ctx):
    _, port = srv_ctx(Auth("sekret-token"))
    res, payload = _request(port, "GET", "/api/session", cookie=None, xrw=None)
    assert res.status == 200
    assert payload["ok"] is True
    assert payload["authenticated"] is False
    assert payload["token_set"] is True


def test_login_wrong_token(srv_ctx):
    _, port = srv_ctx(Auth("sekret-token"))
    res, payload = _request(port, "POST", "/api/login", body={"token": "bad"})
    assert res.status == 401
    assert payload["ok"] is False
    assert "токен" in payload["error"].lower()


def test_login_ok_sets_cookie(srv_ctx):
    _, port = srv_ctx(Auth("sekret-token"))
    res, payload = _request(port, "POST", "/api/login", body={"token": "sekret-token"})
    assert res.status == 200
    assert payload["authenticated"] is True
    set_cookie = res.headers.get("Set-Cookie", "")
    assert set_cookie.startswith("web_session=")
    assert "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie
    cookie = set_cookie.split(";", 1)[0]

    # с cookie — сессия уже есть
    res2, payload2 = _request(port, "GET", "/api/session", cookie=cookie, xrw=None)
    assert res2.status == 200
    assert payload2["authenticated"] is True


def test_login_requires_csrf(srv_ctx):
    _, port = srv_ctx(Auth("sekret-token"))
    res, _ = _request(port, "POST", "/api/login",
                      body={"token": "sekret-token"}, xrw=None)
    assert res.status == 403


def test_logout_invalidates_session(srv_ctx):
    _, port = srv_ctx(Auth("sekret-token"))
    res, _ = _request(port, "POST", "/api/login", body={"token": "sekret-token"})
    cookie = res.headers.get("Set-Cookie", "").split(";", 1)[0]

    res2, _ = _request(port, "POST", "/api/logout", cookie=cookie)
    assert res2.status == 200

    res3, payload3 = _request(port, "GET", "/api/session",
                              cookie=cookie, xrw=None)
    assert res3.status == 200
    assert payload3["authenticated"] is False


def test_protected_requires_session(srv_ctx):
    """Без cookie защищённый эндпоинт отдаёт 401."""
    _, port = srv_ctx(Auth("sekret-token"))
    res, payload = _request(port, "GET", "/api/session", cookie=None, xrw=None)
    assert res.status == 200  # session публичный
    res2, payload2 = _request(port, "GET", "/api/nope", cookie=None, xrw=None)
    assert res2.status == 401  # защищённый путь без сессии
    assert payload2["error"] == "Требуется вход"


def test_no_auth_mode(srv_ctx):
    """--no-auth: сессия не требуется, authenticated сразу True."""
    _, port = srv_ctx(Auth(no_auth=True))
    res, payload = _request(port, "GET", "/api/session", cookie=None, xrw=None)
    assert res.status == 200
    assert payload["authenticated"] is True


# ════════════════════════════════════════════════════════════════════
# M3 (AUDIT): TTL сессий и rate-limit входа
# ════════════════════════════════════════════════════════════════════
def test_session_ttl_expires(monkeypatch):
    """M3: сессия протухает после SESSION_TTL; скользящий TTL продлевает."""
    from web import auth as auth_mod
    import time
    auth_obj = Auth("token")
    sid = auth_obj.issue_session()
    now = [time.time()]
    monkeypatch.setattr(auth_mod.time, "time", lambda: now[0])
    assert auth_obj.valid_session(sid)
    # в пределах TTL — жива (скользящее продление обновляет last_seen)
    now[0] += auth_mod.SESSION_TTL - 1
    assert auth_obj.valid_session(sid)
    # за пределами TTL (после продления) — протухла и удалена
    now[0] += auth_mod.SESSION_TTL + 1
    assert not auth_obj.valid_session(sid)
    assert sid not in auth_obj._sessions


def test_login_rate_limit_blocks(srv_ctx):
    """M3: >LOGIN_MAX_FAILS неудачных входов за минуту → 429."""
    from web import auth as auth_mod
    auth_obj = Auth("sekret")
    _, port = srv_ctx(auth_obj)
    for _ in range(auth_mod.LOGIN_MAX_FAILS):
        res, _ = _request(port, "POST", "/api/login",
                          body={"token": "wrong"})
        assert res.status == 401
    res, payload = _request(port, "POST", "/api/login",
                            body={"token": "sekret"})
    assert res.status == 429
    assert "попыток" in payload["error"].lower()


def test_login_rate_limit_resets_after_window(srv_ctx, monkeypatch):
    """M3: после окна лимит сбрасывается — вход снова возможен."""
    from web import auth as auth_mod
    import time
    auth_obj = Auth("sekret")
    _, port = srv_ctx(auth_obj)
    now = [time.time()]
    monkeypatch.setattr(auth_mod.time, "time", lambda: now[0])
    for _ in range(auth_mod.LOGIN_MAX_FAILS + 1):
        res, _ = _request(port, "POST", "/api/login",
                          body={"token": "wrong"})
        assert res.status in (401, 429)
    # окно прошло — блок снят, верный токен пускает
    now[0] += auth_mod.LOGIN_WINDOW + 1
    res, payload = _request(port, "POST", "/api/login",
                            body={"token": "sekret"})
    assert res.status == 200 and payload["authenticated"] is True


# ════════════════════════════════════════════════════════════════════
# роутер: 404/405
# ════════════════════════════════════════════════════════════════════
def test_unknown_api_404(srv_ctx):
    _, port = srv_ctx(Auth("t", no_auth=True))
    res, payload = _request(port, "GET", "/api/nope")
    assert res.status == 404
    assert payload["ok"] is False


def test_wrong_method_405(srv_ctx):
    _, port = srv_ctx(Auth("t", no_auth=True))
    res, payload = _request(port, "PUT", "/api/session")
    assert res.status == 405
    assert payload["ok"] is False


# ════════════════════════════════════════════════════════════════════
# статика SPA
# ════════════════════════════════════════════════════════════════════
def test_static_index(srv_ctx):
    _, port = srv_ctx(Auth("t"))
    res, _ = _request(port, "GET", "/", xrw=None)
    assert res.status == 200
    assert res.headers.get("Content-Type", "").startswith("text/html")


def test_static_app_js(srv_ctx):
    _, port = srv_ctx(Auth("t"))
    for path in ("/app.js", "/project-views.js", "/run-views.js"):
        res, _ = _request(port, "GET", path, xrw=None)
        assert res.status == 200
        assert "javascript" in res.headers.get("Content-Type", "")


def test_static_index_links_split_js(srv_ctx):
    """index.html подключает все три JS-файла разбитого app.js."""
    import http.client
    _, port = srv_ctx(Auth("t"))
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/", headers={"X-Requested-With": "fetch"})
    res = conn.getresponse()
    body = res.read().decode("utf-8")
    conn.close()
    assert res.status == 200
    for script in ("project-views.js", "run-views.js", "app.js"):
        assert script in body


def test_static_traversal_rejected(srv_ctx):
    """Пути вне static/ (../, симлинки) не отдаются."""
    _, port = srv_ctx(Auth("t"))
    for bad in ("/../core/common.py", "/%2e%2e/core/common.py",
                "/static/../core/common.py"):
        res, _ = _request(port, "GET", bad, xrw=None)
        assert res.status == 404, f"{bad} должен быть 404"


def test_static_unknown_404(srv_ctx):
    _, port = srv_ctx(Auth("t"))
    res, _ = _request(port, "GET", "/no-such-file.js", xrw=None)
    assert res.status == 404


# ════════════════════════════════════════════════════════════════════
# W1: дефолты main.py — локальная сеть без аутентификации
# ════════════════════════════════════════════════════════════════════

def test_w1_defaults_lan_no_auth():
    """Без флагов: host=0.0.0.0, auth выключен."""
    from web.main import _parse_args
    import os
    saved = {k: os.environ.pop(k, None) for k in
             ("WEB_HOST", "WEB_PORT", "WEB_AUTH", "WEB_TOKEN")}
    try:
        args = _parse_args([])
        assert args.host == "0.0.0.0"
        assert args.auth is False
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_w1_auth_flag_and_env():
    """--auth / WEB_AUTH=1 включает аутентификацию; WEB_HOST переопределяет."""
    from web.main import _parse_args
    import os
    saved = {k: os.environ.pop(k, None) for k in
             ("WEB_HOST", "WEB_PORT", "WEB_AUTH", "WEB_TOKEN")}
    try:
        assert _parse_args(["--auth"]).auth is True
        os.environ["WEB_AUTH"] = "1"
        assert _parse_args([]).auth is True
        os.environ["WEB_AUTH"] = "0"
        assert _parse_args([]).auth is False
        os.environ["WEB_HOST"] = "127.0.0.1"
        assert _parse_args([]).host == "127.0.0.1"
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def test_w1_lan_ip_is_sane():
    """_lan_ip возвращает строку-адрес, не бросает."""
    from web.main import _lan_ip
    ip = _lan_ip()
    assert isinstance(ip, str) and ip
    parts = ip.split(".")
    assert len(parts) == 4 and all(p.isdigit() for p in parts)

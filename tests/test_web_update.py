#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Обновления web-приложения (web/api_common.py): сводка в сессии,
POST /api/update/check, GET /api/update/download и способ установки.

Сеть наружу не ходит: GitHub замокан на уровне core.common/core.transport
(monkeypatch), ответы сервера — живые (srv_ctx из conftest).
"""
import json
import threading
from pathlib import Path

import pytest

from conftest import http_request as _request
from conftest import http_send
from web import api as web_api
from web import api_common
from web.auth import Auth
from web.server import make_server
from web.version import app_version

REPO = Path(__file__).resolve().parent.parent

_RELEASE = {
    "ok": True, "tag": "v9.9.0", "version": "9.9.0",
    "name": "v9.9.0", "url": "https://example.com/r",
    "published": "2026-01-01T00:00:00Z", "notes": "что нового",
}


@pytest.fixture()
def srv_ctx(tmp_path):
    """Фабрика сервера (как в test_web_api): projects_root=tmp_path."""
    servers = []

    def _make():
        from core import projects as P
        projects_root = tmp_path / "projects"
        P.ensure_projects_root(projects_root)
        srv = make_server("127.0.0.1", 0, Auth("tok", no_auth=True),
                          repo_root=REPO, projects_root=projects_root)
        web_api.register(srv.router, "127.0.0.1")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv, srv.server_address[1]

    yield _make
    for srv in servers:
        threading.Thread(target=srv.shutdown, daemon=True).start()
        srv.server_close()


@pytest.fixture()
def fresh_cache():
    """Кеш проверки обновлений — состояние процесса: чистим до и после."""
    api_common._UPDATE_CACHE.update({"release": None, "checked": 0.0})
    yield
    api_common._UPDATE_CACHE.update({"release": None, "checked": 0.0})


@pytest.fixture()
def srv(srv_ctx):
    _, port, = srv_ctx()
    return port


def _session(port):
    return _request(port, "GET", "/api/session")


def test_session_carries_update_state(srv, fresh_cache):
    """Сессия несёт сводку обновления: версия, способ установки, кеш."""
    res, payload = _session(srv)
    assert res.status == 200
    upd = payload["update"]
    assert upd["current"] == app_version()
    assert upd["kind"] in ("docker", "portable", "git")
    assert upd["release"] is None and upd["available"] is False
    assert upd["checked"] == 0.0


def test_session_note_empty_without_check(srv, fresh_cache):
    """До первой проверки — никакой подписи о доступности."""
    _, payload = _session(srv)
    assert payload["update"]["note"] == ""


def test_update_check_ok(srv, fresh_cache, monkeypatch):
    """POST /api/update/check: релиз свежее текущего → available + note."""
    monkeypatch.setattr(api_common.common, "latest_release",
                        lambda: dict(_RELEASE))
    res, payload = _request(srv, "POST", "/api/update/check")
    assert res.status == 200 and payload["ok"] is True
    upd = payload["update"]
    assert upd["available"] is True
    assert upd["release"]["version"] == "9.9.0"
    assert upd["checked"] > 0
    assert "новая версия" in upd["note"]
    # кеш видит и сессия — сеть второй раз не нужна
    _, again = _session(srv)
    assert again["update"]["available"] is True


def test_update_check_up_to_date(srv, fresh_cache, monkeypatch):
    """Релиз не новее текущего — available: False, без подписи."""
    monkeypatch.setattr(api_common.common, "latest_release",
                        lambda: dict(_RELEASE, version="0.0.1",
                                     tag="v0.0.1"))
    _, payload = _request(srv, "POST", "/api/update/check")
    assert payload["update"]["available"] is False
    assert payload["update"]["note"] == ""


def test_update_check_network_down(srv, fresh_cache, monkeypatch):
    """Сеть недоступна — не 500: вердикт в теле, кеш не тронут."""
    def boom(url, *, timeout=None):
        raise api_common.common.ConnectTimeout("нет сети")
    monkeypatch.setattr(api_common.common, "open_json_get", boom)
    res, payload = _request(srv, "POST", "/api/update/check")
    assert res.status == 200 and payload["ok"] is False
    assert "нет сети" in payload["error"]
    _, session = _session(srv)
    assert session["update"]["release"] is None


def test_update_cache_kept_after_check(srv, fresh_cache, monkeypatch):
    """Кеш в процессе: после одной проверки сессия отвечает без сети."""
    calls = []
    def fake_get(url, *, timeout=None):
        calls.append(url)
        return {"tag_name": "v9.9.0", "name": "", "html_url": "",
                "published_at": "", "body": ""}
    monkeypatch.setattr(api_common.common, "open_json_get", fake_get)
    _request(srv, "POST", "/api/update/check")
    assert len(calls) == 1
    # дальше сеть ломаем: сессия всё равно должна отдать закешированный релиз
    def boom(url, *, timeout=None):
        raise AssertionError("сессия не должна ходить в сеть")
    monkeypatch.setattr(api_common.common, "open_json_get", boom)
    _, payload = _session(srv)
    assert payload["update"]["available"] is True


def test_install_kind_markers(srv, fresh_cache, tmp_path):
    """Способ установки по маркерам в корне кода (порядок: docker первый)."""
    from web.api_common import _kind_at
    assert _kind_at(tmp_path) == "git"
    (tmp_path / "START.txt").write_text("", encoding="utf-8")
    assert _kind_at(tmp_path) == "portable"
    (tmp_path / ".docker").write_text("", encoding="utf-8")
    assert _kind_at(tmp_path) == "docker"


def test_update_download_docker_rejected(srv, fresh_cache, monkeypatch):
    """Docker обновляется образом: скачивание — 400 с командой pull."""
    monkeypatch.setattr(api_common, "_install_kind", lambda: "docker")
    res, payload = _request(srv, "GET", "/api/update/download")
    assert res.status == 400
    assert "docker compose pull" in payload["error"]


def test_update_download_requires_check(srv, fresh_cache, monkeypatch):
    """Без проверки скачивать нечего: 400 с подсказкой нажать «Проверить»."""
    monkeypatch.setattr(api_common, "_install_kind", lambda: "git")
    res, payload = _request(srv, "GET", "/api/update/download")
    assert res.status == 400
    assert "Проверить" in payload["error"]


def test_update_download_missing_asset(srv, fresh_cache, monkeypatch):
    """В релизе нет портативного zip — 404 с понятной причиной."""
    monkeypatch.setattr(api_common, "_install_kind", lambda: "portable")
    monkeypatch.setattr(api_common, "_UPDATE_CACHE",
                        {"release": dict(_RELEASE), "checked": 1.0})
    def fake_json_get(url, *, timeout=None):
        return {"assets": [{"name": "другое.txt",
                            "browser_download_url": "https://x/f.txt"}]}
    monkeypatch.setattr(api_common.transport, "open_json_get",
                        fake_json_get)
    res, payload = _request(srv, "GET", "/api/update/download")
    assert res.status == 404
    assert "нет архива" in payload["error"]


def test_update_download_streams_zip(srv, fresh_cache, monkeypatch):
    """Успешный путь: zip релиза как attachment с именем версии."""
    monkeypatch.setattr(api_common, "_install_kind", lambda: "portable")
    monkeypatch.setattr(api_common, "_UPDATE_CACHE",
                        {"release": dict(_RELEASE), "checked": 1.0})
    def fake_json_get(url, *, timeout=None):
        assert "/releases/tags/v9.9.0" in url
        return {"assets": [
            {"name": "novelmaestro-portable-9.9.0.zip",
             "browser_download_url": "https://x/zip"},
        ]}
    monkeypatch.setattr(api_common.transport, "open_json_get",
                        fake_json_get)
    ZIP = b"PK\x03\x04fake-zip\r\nbytes"
    def fake_download(url):
        assert url == "https://x/zip"
        return ZIP
    monkeypatch.setattr(api_common, "_download_bytes", fake_download)
    res, raw = http_send(srv, "GET", "/api/update/download")
    assert res.status == 200
    assert res.headers.get("Content-Type") == "application/zip"
    assert "novelmaestro-portable-9.9.0.zip" in \
        res.headers.get("Content-Disposition", "")
    assert raw == ZIP

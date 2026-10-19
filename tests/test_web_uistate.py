#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты роутов состояния книги: GET/PUT /api/projects/{sec}/{name}/ui-state.

Проверяется то, ради чего слой заведён: состояние живёт в книге (tmp/ui_state),
cookie устройства выдаётся и узнаётся, слияние не теряет правки устройств,
кривое тело не портит файл.
"""
import json
import threading
from pathlib import Path

import pytest

from conftest import http_request as _request
from web import api as web_api
from web.auth import Auth
from web.server import DEVICE_COOKIE, make_server

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def srv(tmp_path):
    servers = []

    def _make():
        from core import projects as P
        projects_root = tmp_path / "projects"
        P.ensure_projects_root(projects_root)
        auth_obj = Auth("tok", no_auth=True)
        srv = make_server("127.0.0.1", 0, auth_obj,
                          repo_root=REPO, projects_root=projects_root)
        web_api.register(srv.router, "127.0.0.1")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        res, payload = _request(srv.server_address[1], "POST", "/api/projects",
                                {"section": "ACTIVE", "name": "book"})
        assert payload.get("ok"), payload
        return srv, srv.server_address[1], projects_root

    yield _make
    for one in servers:
        threading.Thread(target=one.shutdown, daemon=True).start()
        one.server_close()


def _device_cookie(res) -> str:
    for key, value in res.getheaders():
        if key.lower() == "set-cookie" and value.startswith(DEVICE_COOKIE + "="):
            return value.split(";")[0]
    return ""


def test_state_kept_in_project_tmp(srv):
    srv_, port, projects_root = srv()
    res, payload = _request(port, "GET", "/api/projects/ACTIVE/book/ui-state")
    assert res.status == 200 and payload["ok"]
    assert payload["data"] == {}
    assert payload["dir"] == "tmp/ui_state"
    # каталога ещё нет: он появляется первой записью, а не чтением
    assert not (projects_root / "ACTIVE" / "book" / "tmp" / "ui_state").exists()


def test_put_then_get_roundtrip_in_book(srv):
    srv_, port, projects_root = srv()
    res, payload = _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
                            {"data": {"runs": {"ner": {"chunk_size": "1234"}}}})
    assert payload["ok"], payload
    cookie = _device_cookie(res)
    assert cookie.startswith(DEVICE_COOKIE + "="), res.getheaders()
    res2, payload2 = _request(port, "GET", "/api/projects/ACTIVE/book/ui-state",
                              cookie=cookie)
    assert payload2["data"] == {"runs": {"ner": {"chunk_size": "1234"}}}
    ddir = projects_root / "ACTIVE" / "book" / "tmp" / "ui_state"
    files = list(ddir.glob("*.json"))
    assert len(files) == 1, files
    raw = json.loads(files[0].read_text(encoding="utf-8"))
    assert raw["runs"]["ner"]["chunk_size"] == "1234"
    assert raw["version"] >= 1


def test_different_devices_do_not_overwrite_each_other(srv):
    srv_, port, _ = srv()
    res_a, _ = _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
                        {"data": {"tab": {"editor": {"chapter": "7"}}}})
    res_b, _ = _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
                        {"data": {"tab": {"logs": {"path": "logs"}}}})
    cookie_a = _device_cookie(res_a)
    cookie_b = _device_cookie(res_b)
    assert cookie_a and cookie_b and cookie_a != cookie_b
    _, got = _request(port, "GET", "/api/projects/ACTIVE/book/ui-state",
                      cookie=cookie_a)
    # новое устройство видит чужое состояние: переезд с телефона на десктоп
    assert got["data"]["tab"] == {"editor": {"chapter": "7"},
                                  "logs": {"path": "logs"}}


def test_get_gives_device_cookie(srv):
    srv_, port, _ = srv()
    res, _ = _request(port, "GET", "/api/projects/ACTIVE/book/ui-state")
    cookie = _device_cookie(res)
    assert cookie.startswith(DEVICE_COOKIE + "=")
    header = next(v for k, v in res.getheaders() if k.lower() == "set-cookie")
    assert "HttpOnly" in header and "SameSite=Lax" in header
    assert "Max-Age=" in header
    # тот же id приезжает и в PUT: файл устройства один
    res2, _ = _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
                       {"data": {"a": {"b": 1}}}, cookie=cookie)
    assert _device_cookie(res2) == cookie


def test_foreign_device_cookie_gets_new_id(srv):
    srv_, port, projects_root = srv()
    res, _ = _request(port, "GET", "/api/projects/ACTIVE/book/ui-state",
                      cookie=f"{DEVICE_COOKIE}=../../etc/passwd")
    cookie = _device_cookie(res)
    assert cookie and ".." not in cookie and "/" not in cookie
    assert list((projects_root / "ACTIVE" / "book" / "tmp").glob("*.json")) == []


@pytest.mark.parametrize("body", [{"data": [1, 2]}, {"data": "строка"},
                                  {"data": 5}])
def test_put_rejects_non_object_body(srv, body):
    srv_, port, _ = srv()
    res, payload = _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
                            body)
    assert res.status == 400, payload
    assert payload["ok"] is False


def test_put_empty_data_keeps_file(srv):
    """Пустое тело не стирает книгу: очистка — удаление каталога, не запись."""
    srv_, port, projects_root = srv()
    res, _ = _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
                      {"data": {"tab": {"editor": {"chapter": "1"}}}})
    cookie = _device_cookie(res)
    _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
             {"data": {}}, cookie=cookie)
    _, got = _request(port, "GET", "/api/projects/ACTIVE/book/ui-state",
                      cookie=cookie)
    assert got["data"]["tab"] == {"editor": {"chapter": "1"}}


def test_missing_project_is_404(srv):
    srv_, port, _ = srv()
    res, payload = _request(port, "GET", "/api/projects/ACTIVE/none/ui-state")
    assert res.status == 404 and payload["ok"] is False
    res2, _ = _request(port, "PUT", "/api/projects/ACTIVE/none/ui-state",
                       {"data": {"a": {}}})
    assert res2.status == 404


def test_state_survives_copy_of_book(srv, tmp_path):
    """Копия книги наследует состояние: копия продолжает с тех же параметров."""
    srv_, port, projects_root = srv()
    res, _ = _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
                      {"data": {"runs": {"ner": {"start": "5"}}}})
    cookie = _device_cookie(res)
    _request(port, "POST", "/api/projects/copy",
             {"section": "ACTIVE", "name": "book", "new_name": "book2"})
    _, got = _request(port, "GET", "/api/projects/ACTIVE/book2/ui-state",
                      cookie=cookie)
    assert got["data"]["runs"] == {"ner": {"start": "5"}}

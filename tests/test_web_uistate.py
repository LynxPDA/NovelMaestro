#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты роутов состояния книги: GET/PUT /api/projects/{sec}/{name}/ui-state.

Проверяется то, ради чего слой заведён: состояние живёт в книге (tmp/ui_state)
одним файлом, одинаково для всех браузеров и доменов, слияние не теряет правки
разных писателей, кривое тело не портит файл.
"""
import json
import threading
from pathlib import Path

import pytest

from conftest import http_request as _request
from web import api as web_api
from web.auth import Auth
from web.server import LEGACY_DEVICE_COOKIE, make_server

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


def _ddir(projects_root) -> Path:
    return projects_root / "ACTIVE" / "book" / "tmp" / "ui_state"


def test_state_kept_in_project_tmp(srv):
    srv_, port, projects_root = srv()
    res, payload = _request(port, "GET", "/api/projects/ACTIVE/book/ui-state")
    assert res.status == 200 and payload["ok"]
    assert payload["data"] == {}
    assert payload["dir"] == "tmp/ui_state"
    # каталога ещё нет: он появляется первой записью, а не чтением
    assert not _ddir(projects_root).exists()


def test_put_then_get_roundtrip_in_book(srv):
    srv_, port, projects_root = srv()
    res, payload = _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
                            {"data": {"runs": {"ner": {"chunk_size": "1234"}}}})
    assert payload["ok"], payload
    # ответ несёт состояние книги: клиент живёт его снимком
    assert payload["data"] == {"runs": {"ner": {"chunk_size": "1234"}}}
    _, payload2 = _request(port, "GET", "/api/projects/ACTIVE/book/ui-state")
    assert payload2["data"] == {"runs": {"ner": {"chunk_size": "1234"}}}
    files = list(_ddir(projects_root).glob("*.json"))
    assert [p.name for p in files] == ["state.json"], files
    raw = json.loads(files[0].read_text(encoding="utf-8"))
    assert raw["runs"]["ner"]["chunk_size"] == "1234"
    assert raw["version"] >= 1


def test_state_is_one_for_all_browsers(srv):
    """Два браузера и два домена видят одно состояние книги.

    Раньше состояние раскладывалось по cookie устройства: на боевом один
    компьютер с двух адресов давал два файла и два разных диапазона глав."""
    srv_, port, projects_root = srv()
    _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
             {"data": {"runVals:a/b:ner": {"start": "7"}}},
             cookie="nm_device=deviceOne")
    _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
             {"data": {"runVals:a/b:ner": {"end": "9"}}},
             cookie="nm_device=deviceTwo")
    _, got = _request(port, "GET", "/api/projects/ACTIVE/book/ui-state")
    assert got["data"]["runVals:a/b:ner"] == {"start": "7", "end": "9"}
    files = [p.name for p in _ddir(projects_root).glob("*.json")]
    assert files == ["state.json"], files


def test_editing_one_field_keeps_neighbours(srv):
    """Запись одного поля формы не стирает соседние поля того же ключа."""
    srv_, port, _ = srv()
    key = "runVals:a/b:pipeline"
    _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
             {"data": {key: {"start": "100", "end": "200"}}})
    _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
             {"data": {key: {"chunk_size": "9000"}}})
    _, got = _request(port, "GET", "/api/projects/ACTIVE/book/ui-state")
    assert got["data"][key] == {"start": "100", "end": "200",
                                "chunk_size": "9000"}


def test_null_inside_object_removes_field(srv):
    """`null` внутри объекта снимает поле: снятая галочка не воскресает."""
    srv_, port, _ = srv()
    key = "runVals:a/b:ner"
    _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
             {"data": {key: {"start": "1", "two_pass": "1"}}})
    _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
             {"data": {key: {"two_pass": None}}})
    _, got = _request(port, "GET", "/api/projects/ACTIVE/book/ui-state")
    assert got["data"][key] == {"start": "1"}


def test_no_device_cookie_is_set(srv):
    """Cookie устройства больше не выдаётся: она делила книгу на правды."""
    srv_, port, _ = srv()
    res, _ = _request(port, "GET", "/api/projects/ACTIVE/book/ui-state")
    headers = [v for k, v in res.getheaders() if k.lower() == "set-cookie"]
    assert not any(v.startswith(LEGACY_DEVICE_COOKIE + "=") for v in headers), \
        headers


def test_legacy_device_cookie_is_cleared(srv):
    """Старая cookie гасится при первом запросе состояния книги."""
    srv_, port, _ = srv()
    res, _ = _request(port, "GET", "/api/projects/ACTIVE/book/ui-state",
                      cookie=f"{LEGACY_DEVICE_COOKIE}=deviceOne")
    headers = [v for k, v in res.getheaders() if k.lower() == "set-cookie"]
    assert any(v.startswith(LEGACY_DEVICE_COOKIE + "=") and "Max-Age=0" in v
               for v in headers), headers


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
    srv_, port, _ = srv()
    _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
             {"data": {"nmTab:a/b": {"page": {"view": "run"}}}})
    _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state", {"data": {}})
    _, got = _request(port, "GET", "/api/projects/ACTIVE/book/ui-state")
    assert got["data"]["nmTab:a/b"] == {"page": {"view": "run"}}


def test_missing_project_is_404(srv):
    srv_, port, _ = srv()
    res, payload = _request(port, "GET", "/api/projects/ACTIVE/none/ui-state")
    assert res.status == 404 and payload["ok"] is False
    res2, _ = _request(port, "PUT", "/api/projects/ACTIVE/none/ui-state",
                       {"data": {"a": {}}})
    assert res2.status == 404


def test_state_survives_copy_of_book(srv, tmp_path):
    """Копия книги наследует состояние: копия продолжает с тех же параметров."""
    srv_, port, _ = srv()
    _request(port, "PUT", "/api/projects/ACTIVE/book/ui-state",
             {"data": {"runs": {"ner": {"start": "5"}}}})
    _request(port, "POST", "/api/projects/copy",
             {"section": "ACTIVE", "name": "book", "new_name": "book2"})
    _, got = _request(port, "GET", "/api/projects/ACTIVE/book2/ui-state")
    assert got["data"]["runs"] == {"ner": {"start": "5"}}


def test_legacy_files_folded_on_first_request(srv):
    """Файлы устройств прежней версии вливаются в общий файл при первом GET."""
    srv_, port, projects_root = srv()
    ddir = _ddir(projects_root)
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "olddev.json").write_text(json.dumps({
        "version": 1, "updated": 1,
        "runVals:a/b:ner": {"start": "42"},
    }), encoding="utf-8")
    _, got = _request(port, "GET", "/api/projects/ACTIVE/book/ui-state")
    assert got["data"]["runVals:a/b:ner"] == {"start": "42"}

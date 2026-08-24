#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты web-API (M2): разделы, проекты (CRUD через core.projects),
hub_state, ACTIONS из run.py, дерево глав, шаблоны.

Сервер на порту 0 с projects_root в tmp_path и настоящим repo_root —
всё на диске pytest, без сети.
"""
import http.client
import json
import threading
from urllib.parse import quote
from pathlib import Path
from typing import Any, Callable

import pytest

from web import api as web_api
from web.auth import Auth
from web.server import make_server

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def srv_ctx(tmp_path):
    """Сервер с projects_root=tmp_path/projects и настоящим repo_root."""
    servers = []

    def _make(projects_root: Path | None = None):
        projects_root = projects_root or (tmp_path / "projects")
        # как web/main.py: bootstrap дефолтных разделов перед запуском
        from core import projects as P
        P.ensure_projects_root(projects_root)
        auth_obj = Auth("tok", no_auth=True)
        srv = make_server("127.0.0.1", 0, auth_obj,
                          repo_root=REPO, projects_root=projects_root)
        web_api.register(srv.router, "127.0.0.1")
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        servers.append(srv)
        return srv, srv.server_address[1], projects_root

    yield _make
    for srv in servers:
        srv.server_close()


def _request(port, method, path, body=None,
             xrw: str | None = "fetch") -> tuple[Any, dict]:
    """Запрос к серверу; возвращает (response, payload)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {}
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


def _create_project(port, projects_root, section="ACTIVE", name="test_book",
                    **extra) -> dict:
    res, payload = _request(port, "POST", "/api/projects",
                            {"section": section, "name": name, **extra})
    assert res.status == 200, payload
    return payload


# ════════════════════════════════════════════════════════════════════
# разделы и состояние

def test_sections_lists_counts(srv_ctx):
    _, port, projects_root = srv_ctx()
    res, payload = _request(port, "GET", "/api/sections")
    assert res.status == 200
    names = [s["name"] for s in payload["sections"]]
    assert names == ["ACTIVE", "HOLD", "DONE"]
    assert all(s["count"] == 0 for s in payload["sections"])
    # после создания проекта счётчик растёт
    _create_project(port, projects_root)
    _, payload = _request(port, "GET", "/api/sections")
    by_name = {s["name"]: s["count"] for s in payload["sections"]}
    assert by_name["ACTIVE"] == 1


def test_sections_crud_api(srv_ctx):
    """Раунд 22: создание/переименование(merge)/удаление разделов."""
    _, port, projects_root = srv_ctx()
    # создать
    res, payload = _request(port, "POST", "/api/sections", {"name": "Архив"})
    assert res.status == 200 and payload["name"] == "Архив"
    assert (projects_root / "Архив").is_dir()
    res, payload = _request(port, "POST", "/api/sections", {"name": "Архив"})
    assert res.status == 400 and "уже существует" in payload["error"]
    # переименование (merge в существующий): проект переносится
    _create_project(port, projects_root, section="Архив", name="Kniga")
    res, payload = _request(port, "POST", "/api/sections/rename",
                            {"src": "Архив", "dst": "DONE"})
    assert res.status == 200 and payload["dst"] == "DONE"
    assert (projects_root / "DONE" / "Kniga").is_dir()
    assert not (projects_root / "Архив").exists()
    # переименование дефолтного разрешено
    res, payload = _request(port, "POST", "/api/sections/rename",
                            {"src": "ACTIVE", "dst": "В работе"})
    assert res.status == 200 and payload["dst"] == "В работе"
    assert (projects_root / "В работе").is_dir()
    # удаление пустого (кириллица в пути — percent-encode)
    res, payload = _request(port, "POST", "/api/sections", {"name": "Пустой"})
    assert res.status == 200
    empty_enc = quote("Пустой")
    res, payload = _request(port, "DELETE", f"/api/sections/{empty_enc}")
    assert res.status == 200
    assert not (projects_root / "Пустой").exists()
    # удаление непустого — 409
    res, payload = _request(port, "DELETE", "/api/sections/DONE")
    assert res.status == 409 and "не пуст" in payload["error"]
    assert (projects_root / "DONE").is_dir()
    # неизвестный — 404
    res, payload = _request(port, "DELETE", "/api/sections/%D0%9D%D0%B5"
                            "%D1%82_%D1%82%D0%B0%D0%BA%D0%BE%D0%B3%D0%BE")
    assert res.status == 404


def test_sections_rename_unknown(srv_ctx):
    _, port, _ = srv_ctx()
    res, payload = _request(port, "POST", "/api/sections/rename",
                            {"src": "Нет_такого", "dst": "X"})
    assert res.status == 400 and "не найден" in payload["error"]
    res, payload = _request(port, "POST", "/api/sections/rename",
                            {"src": "ACTIVE"})
    assert res.status == 400 and "обязательны" in payload["error"]


def test_projects_list_unknown_section(srv_ctx):
    _, port, _ = srv_ctx()
    res, payload = _request(port, "GET", "/api/projects?section=NOPE")
    assert res.status == 400
    assert "Неизвестный раздел" in payload["error"]


def test_projects_list_empty_and_after_create(srv_ctx):
    _, port, projects_root = srv_ctx()
    _, payload = _request(port, "GET", "/api/projects?section=ACTIVE")
    assert payload["projects"] == []
    _create_project(port, projects_root)
    _, payload = _request(port, "GET", "/api/projects?section=ACTIVE")
    assert payload["projects"] == ["test_book"]


# ════════════════════════════════════════════════════════════════════
# создание проекта

def test_create_project_makes_skeleton(srv_ctx):
    _, port, projects_root = srv_ctx()
    payload = _create_project(port, projects_root)
    assert payload["ok"] is True
    assert payload["name"] == "test_book"
    pdir = projects_root / "ACTIVE" / "test_book"
    assert pdir.is_dir()
    assert (pdir / "chapters").is_dir()


def test_create_project_sanitizes_name(srv_ctx):
    _, port, projects_root = srv_ctx()
    payload = _create_project(port, projects_root, name="My Book!")
    assert payload["renamed"] is True
    assert payload["name"] == "My_Book"
    assert (projects_root / "ACTIVE" / "My_Book").is_dir()


def test_create_project_cyrillic_name_rejected(srv_ctx):
    """Кириллица в имени недопустима (канон core.projects)."""
    _, port, _ = srv_ctx()
    res, payload = _request(port, "POST", "/api/projects",
                            {"section": "ACTIVE", "name": "Моя Книга!"})
    assert res.status == 400
    assert "Имя после очистки пустое" in payload["error"]


def test_create_project_duplicate_fails(srv_ctx):
    _, port, projects_root = srv_ctx()
    _create_project(port, projects_root)
    res, payload = _request(port, "POST", "/api/projects",
                            {"section": "ACTIVE", "name": "test_book"})
    assert res.status == 400


def test_create_project_with_template(srv_ctx):
    """Шаблон General: metadata.yaml + скопированные prompts/source."""
    _, port, projects_root = srv_ctx()
    payload = _create_project(port, projects_root, template="General",
                              title="Тестовая книга", author="Автор",
                              genres="фэнтези, боевик")
    assert payload["ok"] is True
    pdir = projects_root / "ACTIVE" / "test_book"
    meta = pdir / "source" / "metadata.yaml"
    assert meta.is_file()
    text = meta.read_text(encoding="utf-8")
    assert "Тестовая книга" in text and "Автор" in text
    assert (pdir / "prompts").is_dir()


def test_create_project_unknown_template(srv_ctx):
    _, port, _ = srv_ctx()
    res, payload = _request(port, "POST", "/api/projects",
                            {"section": "ACTIVE", "name": "x",
                             "template": "NoSuchSet"})
    assert res.status == 400
    assert "Шаблон не найден" in payload["error"]


def test_create_project_bad_section(srv_ctx):
    _, port, _ = srv_ctx()
    res, _ = _request(port, "POST", "/api/projects",
                      {"section": "NOPE", "name": "x"})
    assert res.status == 400


# ════════════════════════════════════════════════════════════════════
# управление: move / rename / copy / delete

def test_move_project(srv_ctx):
    _, port, projects_root = srv_ctx()
    _create_project(port, projects_root)
    res, payload = _request(port, "POST", "/api/projects/move",
                            {"section": "ACTIVE", "name": "test_book",
                             "dst": "HOLD"})
    assert res.status == 200, payload
    assert payload["section"] == "HOLD"
    assert not (projects_root / "ACTIVE" / "test_book").exists()
    assert (projects_root / "HOLD" / "test_book").is_dir()


def test_move_project_unknown_section(srv_ctx):
    _, port, projects_root = srv_ctx()
    _create_project(port, projects_root)
    res, payload = _request(port, "POST", "/api/projects/move",
                            {"section": "ACTIVE", "name": "test_book",
                             "dst": "NOPE"})
    assert res.status == 400


def test_rename_project(srv_ctx):
    _, port, projects_root = srv_ctx()
    _create_project(port, projects_root)
    res, payload = _request(port, "POST", "/api/projects/rename",
                            {"section": "ACTIVE", "name": "test_book",
                             "new_name": "renamed_book"})
    assert res.status == 200, payload
    assert payload["name"] == "renamed_book"
    assert (projects_root / "ACTIVE" / "renamed_book").is_dir()


def test_copy_project(srv_ctx):
    _, port, projects_root = srv_ctx()
    _create_project(port, projects_root)
    res, payload = _request(port, "POST", "/api/projects/copy",
                            {"section": "ACTIVE", "name": "test_book",
                             "new_name": "copy_book"})
    assert res.status == 200, payload
    assert (projects_root / "ACTIVE" / "copy_book").is_dir()
    assert (projects_root / "ACTIVE" / "test_book").is_dir()


def test_delete_project_requires_confirm(srv_ctx):
    _, port, projects_root = srv_ctx()
    _create_project(port, projects_root)
    res, payload = _request(port, "DELETE", "/api/projects",
                            {"section": "ACTIVE", "name": "test_book",
                             "confirm": "нет"})
    assert res.status == 400
    assert (projects_root / "ACTIVE" / "test_book").exists()


def test_delete_project_with_confirm(srv_ctx):
    _, port, projects_root = srv_ctx()
    _create_project(port, projects_root)
    res, payload = _request(port, "DELETE", "/api/projects",
                            {"section": "ACTIVE", "name": "test_book",
                             "confirm": "УДАЛИТЬ"})
    assert res.status == 200, payload
    assert not (projects_root / "ACTIVE" / "test_book").exists()


# ════════════════════════════════════════════════════════════════════
# статистика и дерево глав

def test_project_stats(srv_ctx):
    _, port, projects_root = srv_ctx()
    _create_project(port, projects_root)
    res, payload = _request(port, "GET",
                            "/api/projects/ACTIVE/test_book/stats")
    assert res.status == 200
    assert payload["name"] == "test_book"
    assert "глав:" in payload["stats"]


def test_project_stats_cached(monkeypatch, srv_ctx):
    """R5-K follow-up: stats проекта кешируются по сигнатуре — повторный
    запрос не пересчитывает project_stats (полный обход chapters/)."""
    import web.api as api
    from core import projects as P
    calls = []
    orig = P.project_stats
    def counting(*a, **k):
        calls.append(1)
        return orig(*a, **k)
    monkeypatch.setattr(P, "project_stats", counting)
    api._STATS_CACHE.clear()
    api._CACHE_LOADED.clear()
    _, port, _ = srv_ctx()
    _create_project(port, _)
    res1, _ = _request(port, "GET", "/api/projects/ACTIVE/test_book/stats")
    assert res1.status == 200 and len(calls) == 1
    res2, _ = _request(port, "GET", "/api/projects/ACTIVE/test_book/stats")
    assert res2.status == 200 and len(calls) == 1  # из кеша
    api._STATS_CACHE.clear()
    api._CACHE_LOADED.clear()


def test_stats_signature_invalidates(monkeypatch, srv_ctx):
    """Создание translated.txt меняет сигнатуру mtime → stats пересчёт;
    следующий запрос снова из кеша (без TTL)."""
    import web.api as api
    from core import projects as P
    calls = []
    orig = P.project_stats
    def counting(*a, **k):
        calls.append(1)
        return orig(*a, **k)
    monkeypatch.setattr(P, "project_stats", counting)
    api._STATS_CACHE.clear()
    api._CACHE_LOADED.clear()
    _, port, proot = srv_ctx()
    _create_project(port, proot)
    _request(port, "GET", "/api/projects/ACTIVE/test_book/stats")
    assert len(calls) == 1
    # создаём translated.txt в первой главе → mtime папки главы меняется
    ch = proot / "ACTIVE" / "test_book" / "chapters" / "00001_1"
    ch.mkdir(parents=True)
    (ch / "chapter.txt").write_text("Текст", encoding="utf-8")
    (ch / "translated.txt").write_text("тест", encoding="utf-8")
    _request(port, "GET", "/api/projects/ACTIVE/test_book/stats")
    assert len(calls) == 2  # сигнатура изменилась → пересчёт
    _request(port, "GET", "/api/projects/ACTIVE/test_book/stats")
    assert len(calls) == 2  # снова из кеша
    api._STATS_CACHE.clear()
    api._CACHE_LOADED.clear()


def test_stats_cache_persisted_on_disk(monkeypatch, srv_ctx):
    """Дисковой кеш stats: после «рестарта» (чистый словарь в памяти)
    данные читаются с диска, project_stats не пересчитывается."""
    import web.api as api
    from core import projects as P
    calls = []
    orig = P.project_stats
    def counting(*a, **k):
        calls.append(1)
        return orig(*a, **k)
    monkeypatch.setattr(P, "project_stats", counting)
    api._STATS_CACHE.clear()
    api._CACHE_LOADED.clear()
    _, port, proot = srv_ctx()
    _create_project(port, proot)
    _request(port, "GET", "/api/projects/ACTIVE/test_book/stats")
    assert len(calls) == 1
    assert (proot / ".stats_cache.json").is_file()  # записан на диск
    # «перезапуск сервера»: память чиста, дисковой кеш остался
    api._STATS_CACHE.clear()
    api._CACHE_LOADED.clear()
    _request(port, "GET", "/api/projects/ACTIVE/test_book/stats")
    assert len(calls) == 1  # из дискового кеша, без пересчёта
    api._STATS_CACHE.clear()
    api._CACHE_LOADED.clear()


# ════════════════════════════════════════════════════════════════════
# раунд 21: шаблоны (CRUD) и статус проекта

def test_templates_crud(srv_ctx):
    """Создание/копирование/удаление набора через /api/templates."""
    _, port, _ = srv_ctx()
    name = "TplProbe_01"
    try:
        res, payload = _request(port, "POST", "/api/templates",
                                {"name": name})
        assert res.status == 200 and payload["ok"]
        # дубль — 400
        res, payload = _request(port, "POST", "/api/templates",
                                {"name": name})
        assert res.status == 400
        # General — 400 (занят)
        res, payload = _request(port, "POST", "/api/templates",
                                {"name": "General"})
        assert res.status == 400
        # копия
        res, payload = _request(port, "POST", f"/api/templates/{name}/copy",
                                {"dst": name + "_copy"})
        assert res.status == 200 and payload["name"] == name + "_copy"
        # файл: PUT/GET/DELETE
        res, payload = _request(
            port, "PUT", f"/api/templates/{name}/file",
            {"path": "prompts/x.txt", "content": "привет"})
        assert res.status == 200
        res, payload = _request(
            port, "GET", f"/api/templates/{name}/file?path=prompts/x.txt")
        assert res.status == 200 and payload["content"] == "привет"
        assert "size" in payload and "mtime" in payload  # мета редактора
        # переименование
        res, payload = _request(
            port, "POST", f"/api/templates/{name}/rename",
            {"src": "prompts/x.txt", "dst": "prompts/y.txt"})
        assert res.status == 200 and payload["dst"] == "prompts/y.txt"
        res, payload = _request(
            port, "GET", f"/api/templates/{name}/file?path=prompts/y.txt")
        assert res.status == 200 and payload["content"] == "привет"
        # rename: нет исходника → 404, занято → 400
        res, payload = _request(
            port, "POST", f"/api/templates/{name}/rename",
            {"src": "prompts/nope.txt", "dst": "prompts/z.txt"})
        assert res.status == 404
        res, payload = _request(
            port, "POST", f"/api/templates/{name}/rename",
            {"src": "prompts/y.txt", "dst": "prompts/y.txt"})
        assert res.status == 400
        res, payload = _request(
            port, "DELETE", f"/api/templates/{name}/file?path=prompts/y.txt")
        assert res.status == 200
        # список: набор и его дерево
        res, payload = _request(port, "GET", "/api/templates")
        assert res.status == 200
        names = [t["name"] for t in payload["templates"]]
        assert name in names and name + "_copy" in names
        t = next(t for t in payload["templates"] if t["name"] == name)
        assert isinstance(t["files"], list)  # дерево файлов (каркас пуст)
        # удаление
        res, payload = _request(port, "DELETE", f"/api/templates/{name}")
        assert res.status == 200
        res, payload = _request(port, "DELETE",
                                f"/api/templates/{name}_copy")
        assert res.status == 200
    finally:
        # зачистка на диске репозитория (наборы временные)
        from core import projects as P
        P.delete_template_set(REPO / "templates", name)
        P.delete_template_set(REPO / "templates", name + "_copy")


def test_templates_general_protected(srv_ctx):
    """General — только чтение: запись/удаление → 403."""
    _, port, _ = srv_ctx()
    res, payload = _request(port, "PUT", "/api/templates/General/file",
                            {"path": "x.txt", "content": "x"})
    assert res.status == 403
    res, payload = _request(port, "DELETE", "/api/templates/General/file"
                            "?path=prompts/translate.txt")
    assert res.status == 403
    res, payload = _request(port, "POST", "/api/templates/General/rename",
                            {"src": "prompts/x.txt", "dst": "prompts/y.txt"})
    assert res.status == 403
    res, payload = _request(port, "DELETE", "/api/templates/General")
    assert res.status == 403
    # чтение разрешено
    res, payload = _request(port, "GET", "/api/templates")
    assert res.status == 200


def test_templates_upload_download_mkdir(srv_ctx):
    """Раунд 22: загрузка/скачивание/каталоги в наборе шаблонов."""
    _, port, _ = srv_ctx()
    name = "TplUp_01"
    try:
        res, _ = _request(port, "POST", "/api/templates", {"name": name})
        assert res.status == 200
        # mkdir
        res, payload = _request(port, "POST", f"/api/templates/{name}/mkdir",
                                {"path": "prompts/extra"})
        assert res.status == 200, payload
        res, payload = _request(port, "POST", f"/api/templates/{name}/mkdir",
                                {"path": "prompts/extra"})
        assert res.status == 400 and "уже существует" in payload["error"]
        # upload в подпапку dest=prompts/extra
        res, payload = _multipart_request(
            port, f"/api/templates/{name}/upload",
            [("dest", "prompts/extra")],
            [("up.txt", "text/plain", "содержимое".encode("utf-8"))])
        assert res.status == 200, payload
        assert payload["saved"] == ["prompts/extra/up.txt"]
        # пустой каталог виден в дереве (trailing '/')
        res, payload = _request(port, "GET", "/api/templates")
        t = next(x for x in payload["templates"] if x["name"] == name)
        assert "prompts/extra/up.txt" in t["files"]
        # download — сырой ответ с attachment
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", f"/api/templates/{name}/download?path="
                            "prompts/extra/up.txt",
                     headers={"X-Requested-With": "fetch"})
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        assert res.status == 200
        assert raw == "содержимое".encode("utf-8")
        assert "attachment" in (res.getheader("Content-Disposition") or "")
        # переименование каталога — файл переезжает вместе с ним
        res, payload = _request(port, "POST", f"/api/templates/{name}/rename",
                                {"src": "prompts/extra",
                                 "dst": "prompts/more"})
        assert res.status == 200 and payload["dst"] == "prompts/more"
        res, payload = _request(
            port, "GET", f"/api/templates/{name}/file?path=prompts/more/up.txt")
        assert res.status == 200 and payload["content"] == "содержимое"
        # удаление каталога (рекурсивно) через DELETE-роут файлов
        res, payload = _request(port, "DELETE",
                                f"/api/templates/{name}/file?path=prompts/more")
        assert res.status == 200
        res, payload = _request(
            port, "GET", f"/api/templates/{name}/file?path=prompts/more/up.txt")
        assert res.status == 404
        # General: upload/mkdir/delete → 403
        res, payload = _request(port, "POST", "/api/templates/General/upload")
        assert res.status == 403
        res, payload = _request(port, "POST", "/api/templates/General/mkdir",
                                {"path": "x"})
        assert res.status == 403
        res, payload = _request(port, "DELETE", "/api/templates/General/file"
                                "?path=prompts")
        assert res.status == 403
    finally:
        from core import projects as P
        P.delete_template_set(REPO / "templates", name)


def test_templates_file_404_and_escape(srv_ctx):
    """Нет файла / эскейп из набора → 404."""
    _, port, _ = srv_ctx()
    res, payload = _request(port, "GET", "/api/templates/General/file"
                            "?path=../../etc/passwd")
    assert res.status == 404
    res, payload = _request(port, "GET", "/api/templates/NoSuchSet/file"
                            "?path=prompts/x.txt")
    assert res.status == 404


def test_project_status_table(srv_ctx):
    """GET /api/projects/{sec}/{name}/status — таблица готовности."""
    srv, port, projects_root = srv_ctx()
    payload = _create_project(port, projects_root)
    pname = payload["name"]
    pdir = projects_root / "ACTIVE" / pname
    (pdir / "chapters" / "001").mkdir(parents=True)
    (pdir / "chapters" / "002").mkdir()
    (pdir / "chapters" / "001" / "translated.txt").write_text("t",
                                                                 encoding="utf-8")
    (pdir / "ner.json").write_text("[{}]", encoding="utf-8")
    res, payload = _request(port, "GET",
                            f"/api/projects/ACTIVE/{pname}/status")
    assert res.status == 200
    st = payload["status"]
    assert st["counts"]["chapters"] == 2
    assert st["counts"]["translate"] == 1
    assert st["chapters"]["1"]["translate"] is True
    assert st["ner"]["exists"] is True
    # повторный запрос — из кеша (тот же результат)
    res2, payload2 = _request(port, "GET",
                              f"/api/projects/ACTIVE/{pname}/status")
    assert res2.status == 200
    assert payload2["status"]["counts"]["chapters"] == 2
    # 404 для несуществующего проекта
    res, payload = _request(port, "GET", "/api/projects/ACTIVE/Nope/status")
    assert res.status == 404


def test_project_stats_404(srv_ctx):
    _, port, _ = srv_ctx()
    res, payload = _request(port, "GET",
                            "/api/projects/ACTIVE/no_such/stats")
    assert res.status == 404
    assert "не найден" in payload["error"]


def test_project_tree_lists_chapters(srv_ctx):
    _, port, projects_root = srv_ctx()
    _create_project(port, projects_root)
    ch = projects_root / "ACTIVE" / "test_book" / "chapters" / "00001_1"
    ch.mkdir(parents=True)
    (ch / "chapter.txt").write_text("Текст главы", encoding="utf-8")
    (ch / "polished.txt").write_text("Полированный текст", encoding="utf-8")
    res, payload = _request(port, "GET",
                            "/api/projects/ACTIVE/test_book/tree")
    assert res.status == 200
    assert len(payload["chapters"]) == 1
    entry = payload["chapters"][0]
    assert entry["id"] == 1
    assert entry["dir"] == "00001_1"
    assert "chapter.txt" in entry["artifacts"]
    assert entry["artifacts"]["chapter.txt"] > 0


def test_project_tree_no_chapters(srv_ctx):
    _, port, _ = srv_ctx()
    _create_project(port, None)  # projects_root не используется здесь
    res, payload = _request(port, "GET",
                            "/api/projects/ACTIVE/test_book/tree")
    assert res.status == 200
    assert payload["chapters"] == []


# ════════════════════════════════════════════════════════════════════
# hub_state и ACTIONS

def test_state_roundtrip(srv_ctx):
    """hub_state — общий с cli: файл projects/.hub_state.json."""
    _, port, projects_root = srv_ctx()
    res, payload = _request(port, "GET", "/api/state")
    assert res.status == 200
    assert payload.get("section") is None
    hub_file = projects_root / ".hub_state.json"
    hub_file.parent.mkdir(parents=True, exist_ok=True)
    hub_file.write_text(json.dumps({"section": "ACTIVE",
                                    "last_project": "ACTIVE/x"}),
                        encoding="utf-8")
    _, payload = _request(port, "GET", "/api/state")
    assert payload["section"] == "ACTIVE"
    assert payload["last_project"] == "ACTIVE/x"


def test_actions_lists_registry(srv_ctx):
    _, port, _ = srv_ctx()
    res, payload = _request(port, "GET", "/api/actions")
    assert res.status == 200
    keys = [a["key"] for a in payload["actions"]]
    assert "epub" in keys and "ner" in keys and "ner_check" in keys
    assert "pipeline" in keys and "translate_check" in keys
    assert "translate_check_llm" in keys and "compile" in keys
    assert "wiki" in keys and "batch_replace" in keys
    for a in payload["actions"]:
        if a["folder"] is not None:
            assert a["available"] is True


def test_templates_lists_sets(srv_ctx):
    _, port, _ = srv_ctx()
    res, payload = _request(port, "GET", "/api/templates")
    assert res.status == 200
    assert isinstance(payload["templates"], list)
    if payload["templates"]:
        assert "name" in payload["templates"][0]
        assert isinstance(payload["templates"][0]["files"], list)


def test_auth_required_for_projects_api(srv_ctx):
    """При включённой аутентификации защищённые пути требуют вход."""
    srv, port, _ = srv_ctx()
    # выключаем no-auth: создаём сервер с auth поверх
    srv.auth.no_auth = False
    res, payload = _request(port, "GET", "/api/projects?section=ACTIVE")
    assert res.status == 401
    assert "Требуется вход" in payload["error"]
    # публичные пути остаются открытыми
    res, payload = _request(port, "GET", "/api/session")
    assert res.status == 200
    assert payload["authenticated"] is False


# ════════════════════════════════════════════════════════════════════
# M3: файлы — листинг, чтение/запись, удаление, upload, download


def _multipart_request(port, path, fields: list[tuple[str, str]],
                       files: list[tuple[str, str, bytes]],
                       xrw: str | None = "fetch") -> tuple[Any, dict]:
    """Сырой multipart-запрос (boundary='BOUND'), JSON-ответ."""
    boundary = "BOUND"
    body = b""
    for name, value in fields:
        body += (f"--{boundary}\r\n"
                 f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                 f"{value}\r\n").encode("utf-8")
    for fname, ctype, data in files:
        body += (f"--{boundary}\r\n"
                 f"Content-Disposition: form-data; name=\"files[]\"; "
                 f"filename=\"{fname}\"\r\n"
                 f"Content-Type: {ctype}\r\n\r\n").encode("utf-8") + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if xrw is not None:
        headers["X-Requested-With"] = xrw
    conn.request("POST", path, body, headers)
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


def _file_project(port, projects_root) -> Path:
    """Проект с файлом для файловых тестов."""
    _create_project(port, projects_root)
    pdir = projects_root / "ACTIVE" / "test_book"
    (pdir / "source" / "hello.txt").write_text("Привет, мир!", encoding="utf-8")
    (pdir / "source" / "meta.json").write_text(
        '{"title": "Книга", "tags": ["a", "b"]}', encoding="utf-8")
    (pdir / "images").mkdir(exist_ok=True)
    (pdir / "images" / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    return pdir


# листинг

def test_files_listing_root(srv_ctx):
    _, port, projects_root = srv_ctx()
    pdir = _file_project(port, projects_root)
    (pdir / "chapters").mkdir(exist_ok=True)
    res, payload = _request(port, "GET",
                            "/api/files?project=ACTIVE/test_book")
    assert res.status == 200, payload
    names = [e["name"] for e in payload["entries"]]
    assert "source" in names and "chapters" in names
    src = next(e for e in payload["entries"] if e["name"] == "source")
    assert src["dir"] is True


def test_files_listing_subdir(srv_ctx):
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    res, payload = _request(port, "GET",
                            "/api/files?project=ACTIVE/test_book&path=source")
    assert res.status == 200, payload
    names = [e["name"] for e in payload["entries"]]
    assert "hello.txt" in names and "meta.json" in names
    f = next(e for e in payload["entries"] if e["name"] == "hello.txt")
    assert f["dir"] is False and f["size"] == len("Привет, мир!".encode("utf-8"))


def test_files_listing_404(srv_ctx):
    _, port, _ = srv_ctx()
    _create_project(port, None)
    res, payload = _request(port, "GET",
                            "/api/files?project=ACTIVE/test_book&path=nope")
    assert res.status == 404
    assert "Папка не найдена" in payload["error"]


def test_files_requires_project_param(srv_ctx):
    _, port, _ = srv_ctx()
    res, payload = _request(port, "GET", "/api/files")
    assert res.status == 400
    assert "project" in payload["error"]


# чтение / запись

def test_file_read_write_roundtrip(srv_ctx):
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    res, payload = _request(port, "PUT", "/api/file",
                            {"project": "ACTIVE/test_book",
                             "path": "source/hello.txt",
                             "content": "Новый текст\n"})
    assert res.status == 200, payload
    res, payload = _request(port, "GET",
                            "/api/file?project=ACTIVE/test_book"
                            "&path=source/hello.txt")
    assert res.status == 200
    assert payload["content"] == "Новый текст\n"
    assert payload["size"] == len("Новый текст\n".encode("utf-8"))


def test_file_read_json_pretty(srv_ctx):
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    res, payload = _request(port, "GET",
                            "/api/file?project=ACTIVE/test_book"
                            "&path=source/meta.json")
    assert res.status == 200
    assert "\n  \"title\"" in payload["content"]  # indent=2


def test_file_read_missing_404(srv_ctx):
    _, port, _ = srv_ctx()
    _create_project(port, None)
    res, payload = _request(port, "GET",
                            "/api/file?project=ACTIVE/test_book&path=zz.txt")
    assert res.status == 404


def test_file_read_binary_rejected(srv_ctx):
    """NUL-снифф: бинарный файл нельзя открыть как текст."""
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    res, payload = _request(port, "GET",
                            "/api/file?project=ACTIVE/test_book"
                            "&path=images/pic.png")
    assert res.status == 400
    assert "Бинарный файл" in payload["error"]


def test_file_read_too_large_413(srv_ctx):
    """W2: файл больше FILE_TEXT_LIMIT не читается в редактор (413)."""
    from web.api import FILE_TEXT_LIMIT
    _, port, projects_root = srv_ctx()
    proj = _file_project(port, projects_root)
    big = proj / "big.txt"
    big.write_text("x" * (FILE_TEXT_LIMIT + 1), encoding="utf-8")
    res, payload = _request(port, "GET",
                            "/api/file?project=ACTIVE/test_book&path=big.txt")
    assert res.status == 413
    assert "слишком большой" in payload["error"]


def test_file_write_normalizes_nfc(srv_ctx):
    """NFC-нормализация при записи (канон §7)."""
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    decomposed = "e\u0301"  # e + combining acute
    res, _ = _request(port, "PUT", "/api/file",
                      {"project": "ACTIVE/test_book",
                       "path": "source/accent.txt", "content": decomposed})
    assert res.status == 200
    raw = (projects_root / "ACTIVE" / "test_book"
           / "source" / "accent.txt").read_bytes()
    assert raw == "\u00e9".encode("utf-8")  # é, одна кодпоинта


def test_file_write_creates_subdirs(srv_ctx):
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    res, payload = _request(port, "PUT", "/api/file",
                            {"project": "ACTIVE/test_book",
                             "path": "tmp/deep/nested.txt",
                             "content": "x"})
    assert res.status == 200, payload
    assert (projects_root / "ACTIVE" / "test_book"
            / "tmp" / "deep" / "nested.txt").is_file()


# удаление

def test_file_delete(srv_ctx):
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    res, payload = _request(port, "DELETE",
                            "/api/file?project=ACTIVE/test_book"
                            "&path=source/hello.txt")
    assert res.status == 200, payload
    assert not (projects_root / "ACTIVE" / "test_book"
                / "source" / "hello.txt").exists()


def test_file_delete_dir_recursive(srv_ctx):
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    (projects_root / "ACTIVE" / "test_book" / "tmp" / "sub").mkdir(
        parents=True)
    res, payload = _request(port, "DELETE",
                            "/api/file?project=ACTIVE/test_book&path=tmp")
    assert res.status == 200, payload
    assert not (projects_root / "ACTIVE" / "test_book" / "tmp").exists()


def test_file_delete_404(srv_ctx):
    _, port, _ = srv_ctx()
    _create_project(port, None)
    res, payload = _request(port, "DELETE",
                            "/api/file?project=ACTIVE/test_book&path=nope")
    assert res.status == 404


# upload

def test_upload_files(srv_ctx):
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    res, payload = _multipart_request(
        port, "/api/upload?project=ACTIVE/test_book",
        [("dest", "source")],
        [("up.txt", "text/plain", "Содержимое".encode("utf-8")),
         ("up2.bin", "application/octet-stream", b"\x00\x01\x02")])
    assert res.status == 200, payload
    assert sorted(payload["saved"]) == ["source/up.txt", "source/up2.bin"]
    pdir = projects_root / "ACTIVE" / "test_book"
    assert (pdir / "source" / "up.txt").read_text(encoding="utf-8") == "Содержимое"
    assert (pdir / "source" / "up2.bin").read_bytes() == b"\x00\x01\x02"


def test_upload_into_chapters_ok(srv_ctx):
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    res, payload = _multipart_request(
        port, "/api/upload?project=ACTIVE/test_book",
        [("dest", "chapters")],
        [("ch.txt", "text/plain", b"abc")])
    assert res.status == 200, payload
    assert payload["saved"] == ["chapters/ch.txt"]


def test_upload_bad_dest_rejected(srv_ctx):
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    res, payload = _multipart_request(
        port, "/api/upload?project=ACTIVE/test_book",
        [("dest", "../evil")],
        [("x.txt", "text/plain", b"x")])
    assert res.status == 400
    assert "недопустима" in payload["error"]


def test_upload_no_files_400(srv_ctx):
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    res, payload = _multipart_request(
        port, "/api/upload?project=ACTIVE/test_book",
        [("dest", "source")], [])
    assert res.status == 400
    assert "Нет файлов" in payload["error"]


def test_upload_missing_boundary_400(srv_ctx):
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    res, payload = _multipart_request(
        port, "/api/upload?project=ACTIVE/test_book",
        [("dest", "source")],
        [("x.txt", "text/plain", b"x")], xrw="fetch")
    assert res.status == 200  # валидный multipart работает
    assert payload["saved"] == ["source/x.txt"]


# download

def test_download_attachment(srv_ctx):
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/download?project=ACTIVE/test_book"
                         "&path=source/hello.txt",
                 headers={"X-Requested-With": "fetch"})
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    assert res.status == 200
    assert res.getheader("Content-Disposition", "").startswith(
        'attachment; filename="hello.txt"')
    assert raw == "Привет, мир!".encode("utf-8")


def test_download_404(srv_ctx):
    _, port, _ = srv_ctx()
    _create_project(port, None)
    res, _ = _request(port, "GET",
                      "/api/download?project=ACTIVE/test_book&path=zz")
    assert res.status == 404


# безопасность пути

def test_path_traversal_denied(srv_ctx):
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    for bad in ("../evil", "../../etc/passwd", "..%2F..%2Fetc"):
        res, payload = _request(port, "GET",
                                "/api/file?project=ACTIVE/test_book"
                                f"&path={bad}")
        assert res.status == 400, (bad, payload)
        assert "за пределы" in payload["error"] or "Недопустимый" in payload["error"]


def test_path_absolute_denied(srv_ctx):
    _, port, projects_root = srv_ctx()
    _file_project(port, projects_root)
    res, payload = _request(port, "GET",
                            "/api/file?project=ACTIVE/test_book"
                            "&path=/etc/passwd")
    assert res.status == 400


def test_project_not_found_404(srv_ctx):
    _, port, _ = srv_ctx()
    res, payload = _request(port, "GET",
                            "/api/files?project=ACTIVE/no_such")
    assert res.status == 404
    assert "Проект не найден" in payload["error"]


def test_dashboard_summary(srv_ctx, tmp_path):
    """W3: /api/dashboard — разделы, статистика, недавние jobs."""
    _, port, projects_root = srv_ctx()
    proj = projects_root / "ACTIVE" / "demo"
    (proj / "chapters" / "000001_test").mkdir(parents=True)
    (proj / "chapters" / "000001_test" / "polished.txt").write_text("текст",
                                                                   encoding="utf-8")
    res, payload = _request(port, "GET", "/api/dashboard")
    assert res.status == 200
    assert payload["total"] >= 1
    secs = {s["name"]: s for s in payload["sections"]}
    assert "demo" in [p["name"] for p in secs["ACTIVE"]["projects"]]
    demo = [p for p in secs["ACTIVE"]["projects"] if p["name"] == "demo"][0]
    assert "1" in demo["stats"]  # главы считаются
    assert isinstance(payload["recent_jobs"], list)
    assert isinstance(payload.get("running_jobs"), list)  # раунд 2


def test_dashboard_stats_cache(monkeypatch, srv_ctx):
    """Сводка: stats проектов не пересобираются на повторном запросе
    (кеш по сигнатуре, без TTL)."""
    import web.api as api
    from core import projects as P
    calls = []
    orig = P.project_stats
    def counting(*a, **k):
        calls.append(1)
        return orig(*a, **k)
    monkeypatch.setattr(P, "project_stats", counting)
    api._STATS_CACHE.clear()
    api._CACHE_LOADED.clear()
    _, port, _ = srv_ctx()
    _create_project(port, _)
    res1, _ = _request(port, "GET", "/api/dashboard")
    assert res1.status == 200 and len(calls) == 1
    res2, _ = _request(port, "GET", "/api/dashboard")
    assert res2.status == 200 and len(calls) == 1  # из кеша
    api._STATS_CACHE.clear()
    api._CACHE_LOADED.clear()

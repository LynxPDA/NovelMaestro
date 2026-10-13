#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Обновления web-слоя (web/api_common.py): сводка в сессии, проверка при запуске
и по кнопке, обновление в отдельном потоке, перезапуск сервера и архив релиза.

Сеть наружу не ходит: ответы GitHub подменяются на уровне core.transport, способ
применения тестируется в tests/test_core_update.py. Корень кода этой копии
подменяется временной папкой — боевой репозиторий не трогается. Настоящий
перезапуск подменяется заглушкой: живой процесс теста убивать не нужно.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import http_request as _request
from conftest import http_send
from core import update as core_update
from web import api as web_api
from web import api_common
from web.auth import Auth
from web.server import make_server
from web.version import app_version

REPO = Path(__file__).resolve().parent.parent

#: ответ GitHub, какой бывает у реального релиза: автозаметка со ссылкой
_RELEASE = {
    "ok": True, "tag": "v9.9.0", "version": "9.9.0", "name": "v9.9.0",
    "url": "https://github.com/LynxPDA/NovelMaestro/releases/tag/v9.9.0",
    "published": "2026-01-01T00:00:00Z",
    "notes": "- **жирный** пункт описания",
    "changes": "https://github.com/LynxPDA/NovelMaestro/compare/v0.4.0...v9.9.0",
}
#: что отдаёт релиз на вопрос об ассетах (скачивание архива)
_ASSETS = {"assets": [{"name": "novelmaestro-portable-9.9.0.zip",
                       "browser_download_url": "https://x/zip"}]}
_IDLE = {"state": "idle", "kind": "", "mode": "", "from": "", "to": "",
         "note": "", "error": "", "log": [], "started": 0.0, "finished": 0.0}


@pytest.fixture(autouse=True)
def update_state():
    """Кеш проверки и состояние потока — состояние процесса.

    В одном воркере xdist тесты идут друг за другом: чистим их до теста, иначе
    предыдущая проверка или предыдущий поток обновления попадут в следующий
    тест как собственное состояние сервера.
    """
    api_common._UPDATE_CACHE.update({"release": None, "checked": 0.0})
    api_common._UPDATE_JOB.update(dict(_IDLE, log=[]))
    return api_common._UPDATE_CACHE


@pytest.fixture(autouse=True)
def server_argv(tmp_path, monkeypatch):
    """argv сервера в тесте свой: у процесса pytest sys.argv[0] — сам pytest,
    а у воркера xdist и вовсе «-c», и план перезапуска считался бы по нему."""
    entry = tmp_path / "main.py"
    entry.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(entry), "--port", "8756"])


@pytest.fixture(autouse=True)
def restarts(monkeypatch):
    """Заглушка перезапуска: план считается по-настоящему, процесс не сворачивается."""
    calls: list[dict] = []

    def fake_schedule(argv=None, kind=None, root=None, allowed=True,
                      pid=None, wait=None):
        plan = core_update.restart_plan(kind=kind, root=root, allowed=allowed)
        calls.append(plan)
        return plan
    monkeypatch.setattr(core_update, "schedule_restart", fake_schedule)
    return calls


@pytest.fixture()
def code_root(tmp_path):
    """Корень кода этой копии — временная папка: боевой репозиторий не трогаем."""
    root = tmp_path / "install"
    (root / "core").mkdir(parents=True)
    (root / "VERSION").write_text(app_version(), encoding="utf-8")
    return root


@pytest.fixture()
def srv_ctx(code_root, monkeypatch):
    """Фабрика сервера (как в test_web_api): код этой копии — code_root.

    Способ установки задаётся маркером в code_root: «git» — каталог .git,
    «portable» — START.txt, «docker» — файл .docker. no_restart — тот же
    флаг --no-restart, что передаёт dev-серверу скрипт dev.sh. Кеш и состояние
    потока обновления — состояние процесса: чистим до и после.
    """
    monkeypatch.setattr(api_common, "_CODE_ROOT", code_root)
    servers = []

    def _make(kind: str = "git", no_restart: bool = True):
        from core import projects as P
        if kind == "docker":
            (code_root / ".docker").touch()
        elif kind == "portable":
            (code_root / "START.txt").touch()
        elif kind == "git":
            (code_root / ".git").mkdir(exist_ok=True)
        projects_root = code_root / "projects"
        P.ensure_projects_root(projects_root)
        srv = make_server("127.0.0.1", 0, Auth("tok", no_auth=True),
                          repo_root=REPO, projects_root=projects_root,
                          no_restart=no_restart)
        web_api.register(srv.router, "127.0.0.1")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv, srv.server_address[1]

    yield _make
    for srv in servers:
        threading.Thread(target=srv.shutdown, daemon=True).start()
        srv.server_close()


@pytest.fixture()
def port(srv_ctx):
    """Сервер обычной установки (клон репозитория), перезапуск — руками."""
    _, port = srv_ctx("git")
    return port


def _session(port):
    return _request(port, "GET", "/api/session")


def _wait_job(port, timeout: float = 15.0) -> dict:
    """Дождаться конца потока обновления и отдать сводку."""
    job, deadline = {}, time.time() + timeout
    while time.time() < deadline:
        _, payload = _request(port, "GET", "/api/update/status")
        job = payload["update"]["job"]
        if job["state"] != "running":
            return payload["update"]
        time.sleep(0.05)
    raise AssertionError(f"поток обновления не завершился: {job}")


# ══════════════════════════════════════════════════════════════════
# СВОДКА В СЕССИИ
# ══════════════════════════════════════════════════════════════════
def test_session_carries_update_state(port):
    """Сессия несёт сводку обновления: версия, способ, кеш, поток, перезапуск."""
    res, payload = _session(port)
    upd = payload["update"]
    assert res.status == 200
    assert upd["current"] == app_version()
    assert upd["kind"] == "git" and upd["mode"] == "git"
    assert upd["label"] == core_update.KIND_LABELS["git"]
    assert upd["mode_label"] == core_update.MODE_LABELS["git"]
    assert upd["command"] == ""
    assert upd["release"] is None and upd["available"] is False
    assert upd["checked"] == 0.0
    assert upd["job"]["state"] == "idle"
    # dev-сервер (и этот тест) идут с --no-restart: перезапуск руками
    assert upd["restart"]["auto"] is False
    assert "--no-restart" in upd["restart"]["note"]


def test_session_restart_auto_for_repo_install(srv_ctx):
    """Запуск из репозитория без --no-restart: сервер перезапустит себя сам."""
    _, port = srv_ctx("git", no_restart=False)
    _, payload = _session(port)
    restart = payload["update"]["restart"]
    assert restart["auto"] is True
    assert "перезапустится сам" in restart["note"]


def test_session_docker_offers_command(srv_ctx):
    """Docker: режим — команда хоста, она же уезжает в сводку, авто-перезапуска нет."""
    _, port = srv_ctx("docker")
    _, payload = _session(port)
    upd = payload["update"]
    assert upd["kind"] == "docker" and upd["mode"] == "command"
    assert upd["command"] == core_update.HOST_COMMAND
    assert upd["mode_label"] == core_update.MODE_LABELS["command"]
    assert upd["restart"]["auto"] is False
    assert "хост" in upd["restart"]["note"]


def test_session_portable_restarts_by_hand(srv_ctx):
    """Портативная сборка: путь архива, но перезапуск — руками (окно заново)."""
    _, port = srv_ctx("portable")
    _, payload = _session(port)
    upd = payload["update"]
    assert upd["kind"] == "portable" and upd["mode"] == "zip"
    assert upd["command"] == ""
    assert upd["restart"]["auto"] is False
    assert "start.bat" in upd["restart"]["note"]


def test_session_passes_markdown_as_is(port, monkeypatch):
    """Заметки и ссылка на сравнение уходят SPA как есть: рендерит SPA."""
    monkeypatch.setattr(core_update, "latest_release", lambda: dict(_RELEASE))
    _request(port, "POST", "/api/update/check")
    _, payload = _session(port)
    upd = payload["update"]
    assert upd["available"] is True and upd["checked"] > 0
    assert upd["release"]["notes"] == _RELEASE["notes"]
    assert upd["release"]["changes"] == _RELEASE["changes"]


# ══════════════════════════════════════════════════════════════════
# ПРОВЕРКА ПРИ ЗАПУСКЕ СЕРВЕРА
# ══════════════════════════════════════════════════════════════════
def test_start_update_check_fills_cache_in_background(code_root, update_state,
                                                     monkeypatch):
    """Проверка при запуске: свой поток, кеш тот же, сессия потом отвечает без сети."""
    monkeypatch.setattr(api_common, "_CODE_ROOT", code_root)
    monkeypatch.setattr(core_update, "latest_release", lambda: dict(_RELEASE))
    api_common.start_update_check()
    upd, deadline = {}, time.time() + 10
    while time.time() < deadline:
        upd = api_common._update_state()
        if upd["checked"]:
            break
        time.sleep(0.02)
    assert upd["checked"] > 0 and upd["available"] is True
    assert upd["release"]["version"] == "9.9.0"


def test_start_update_check_silent_on_network_error(code_root, update_state,
                                                    monkeypatch):
    """Сеть лежит — запуск это не ломает: кеш пустой, проверки просто не видно."""
    monkeypatch.setattr(api_common, "_CODE_ROOT", code_root)

    monkeypatch.setattr(core_update, "latest_release",
                        lambda: {"ok": False,
                                 "error": "нет сети: таймаут соединения"})
    api_common.start_update_check()
    time.sleep(0.3)
    upd = api_common._update_state()
    assert upd["checked"] == 0.0 and upd["release"] is None


def test_session_never_goes_to_network(port, update_state, monkeypatch):
    """Сессию спрашивают часто: она обязана отдавать кеш, а не ходит в GitHub."""
    monkeypatch.setattr(core_update.transport, "open_json_get",
                        lambda url, **kw: (_ for _ in ()).throw(
                            AssertionError("сессия не должна ходить в сеть")))
    api_common._UPDATE_CACHE.update({"release": dict(_RELEASE), "checked": 1.0})
    res, payload = _session(port)
    assert res.status == 200 and payload["update"]["available"] is True


# ══════════════════════════════════════════════════════════════════
# ПРОВЕРКА ПО КНОПКЕ
# ══════════════════════════════════════════════════════════════════
def test_update_check_available(port, monkeypatch):
    """POST /api/update/check: релиз новее текущего → available True."""
    monkeypatch.setattr(core_update.transport, "open_json_get",
                        lambda url, **kw: dict(_RELEASE, tag_name="v9.9.0",
                                               body=_RELEASE["notes"]))
    res, payload = _request(port, "POST", "/api/update/check")
    assert res.status == 200 and payload["ok"] is True
    assert payload["update"]["available"] is True
    assert payload["update"]["release"]["version"] == "9.9.0"


def test_update_check_up_to_date(port, monkeypatch):
    """Релиз не новее текущего — available False."""
    monkeypatch.setattr(core_update.transport, "open_json_get",
                        lambda url, **kw: dict(_RELEASE, tag_name="v0.0.1",
                                               body=""))
    res, payload = _request(port, "POST", "/api/update/check")
    assert res.status == 200 and payload["ok"] is True
    assert payload["update"]["available"] is False


def test_update_check_network_down(port, monkeypatch):
    """Сеть лежит — не 500: вердикт в теле, кеш не тронут."""
    def boom(url, **kw):
        raise core_update.transport.ConnectTimeout("нет сети")
    monkeypatch.setattr(core_update.transport, "open_json_get", boom)
    res, payload = _request(port, "POST", "/api/update/check")
    assert res.status == 200 and payload["ok"] is False
    assert "нет сети" in payload["error"]
    _, session = _session(port)
    assert session["update"]["release"] is None


def test_update_cache_kept_after_check(port, monkeypatch):
    """Кеш в процессе: после одной проверки сессия отвечает без сети."""
    calls: list[str] = []

    def fake_get(url, **kw):
        calls.append(url)
        return {"tag_name": "v9.9.0", "name": "", "html_url": "",
                "published_at": "", "body": ""}
    monkeypatch.setattr(core_update.transport, "open_json_get", fake_get)
    _request(port, "POST", "/api/update/check")
    assert len(calls) == 1
    _, payload = _session(port)
    assert payload["update"]["available"] is True


# ══════════════════════════════════════════════════════════════════
# ОБНОВЛЕНИЕ ПО КНОПКЕ
# ══════════════════════════════════════════════════════════════════
def _git_apply(monkeypatch, note: str = ""):
    """Поток обновления обычной установки: ветка перематывается без сети."""
    monkeypatch.setattr(core_update, "latest_release", lambda: dict(_RELEASE))
    seen: dict[str, object] = {}

    def fake_apply(root, tag, kind=None, log=None):
        seen["kind"], seen["tag"], seen["root"] = kind, tag, str(root)
        if log:
            log("Ветка master: перемотка на релиз v9.9.0 (только ff-only)")
        return {"branch": "master", "kind": kind, "mode": "git",
                "from": app_version(), "to": "9.9.0", "note": note}
    monkeypatch.setattr(core_update, "apply_update", fake_apply)
    return seen


def test_update_apply_docker_rejected(srv_ctx, monkeypatch):
    """Docker: кнопка не предлагается, причина — команда хоста."""
    _, port = srv_ctx("docker")
    monkeypatch.setattr(core_update, "latest_release", lambda: dict(_RELEASE))
    _request(port, "POST", "/api/update/check")
    res, payload = _request(port, "POST", "/api/update/apply")
    assert res.status == 400
    assert "docker compose pull" in payload["error"]
    assert "docker compose up -d" in payload["error"]


def test_update_apply_without_check(port):
    """Без проверки применять нечего: 400 с подсказкой нажать «Проверить»."""
    res, payload = _request(port, "POST", "/api/update/apply")
    assert res.status == 400
    assert "Проверить" in payload["error"]


def test_update_apply_up_to_date(port, monkeypatch):
    """Обновления нет — 400, а не пустой поток."""
    monkeypatch.setattr(core_update, "latest_release",
                        lambda: dict(_RELEASE, version="0.0.1", tag="v0.0.1"))
    _request(port, "POST", "/api/update/check")
    res, payload = _request(port, "POST", "/api/update/apply")
    assert res.status == 400 and "последняя версия" in payload["error"]


def test_update_apply_git_restarts_by_hand(port, monkeypatch, restarts):
    """--no-restart: поток перематывает ветку, итог — done и просьба перезапустить."""
    seen = _git_apply(monkeypatch)
    _request(port, "POST", "/api/update/check")
    res, payload = _request(port, "POST", "/api/update/apply")
    assert res.status == 200
    assert payload["update"]["job"]["mode"] == "git"

    upd = _wait_job(port)
    assert upd["job"]["state"] == "done" and upd["job"]["to"] == "9.9.0"
    assert any("перемотка" in ln for ln in upd["job"]["log"])
    assert upd["job"]["error"] == ""
    assert "--no-restart" in upd["job"]["note"]
    # поток работает по корню кода этой копии, а не по боевому репозиторию
    assert seen["kind"] == "git" and seen["tag"] == "v9.9.0"
    assert seen["root"] == str(api_common._CODE_ROOT)
    # перезапуск выключен флагом: наблюдатель не запускается вовсе
    assert restarts == []


def test_update_apply_git_restarts_itself(srv_ctx, monkeypatch, restarts):
    """Без --no-restart: сервер сворачивается, наблюдатель поднимет его заново."""
    _, port = srv_ctx("git", no_restart=False)
    _git_apply(monkeypatch)
    _request(port, "POST", "/api/update/check")
    _request(port, "POST", "/api/update/apply")
    upd = _wait_job(port)
    assert upd["job"]["state"] == "restarting"
    assert "перезапустится сам" in upd["job"]["note"]
    assert len(restarts) == 1 and restarts[0]["auto"] is True


def test_update_apply_reports_new_dependencies(port, monkeypatch):
    """Сменились зависимости — итог обязан сказать про установку пакетов."""
    _git_apply(monkeypatch, note="Изменились зависимости — обновите их: "
                                 "python3 -m pip install -r requirements.txt")
    _request(port, "POST", "/api/update/check")
    _request(port, "POST", "/api/update/apply")
    upd = _wait_job(port)
    assert "requirements.txt" in upd["job"]["note"]


def test_update_apply_reports_error(port, monkeypatch):
    """Поток не падает: причина обновления приходит текстом в сводке."""
    monkeypatch.setattr(core_update, "latest_release", lambda: dict(_RELEASE))

    def fake_apply(root, tag, kind=None, log=None):
        raise core_update.UpdateError("Ветка master разошлась с сервером")
    monkeypatch.setattr(core_update, "apply_update", fake_apply)
    _request(port, "POST", "/api/update/check")
    _request(port, "POST", "/api/update/apply")
    upd = _wait_job(port)
    assert upd["job"]["state"] == "error"
    assert "разошлась" in upd["job"]["error"]


def test_update_apply_single_job(port, monkeypatch):
    """Пока обновление идёт, второе — 409: два потока правили бы те же файлы."""
    monkeypatch.setattr(core_update, "latest_release", lambda: dict(_RELEASE))
    gate = threading.Event()

    def slow_apply(root, tag, kind=None, log=None):
        assert gate.wait(10)
        return {"branch": "master", "kind": kind, "mode": "git",
                "from": app_version(), "to": "9.9.0", "note": ""}
    monkeypatch.setattr(core_update, "apply_update", slow_apply)
    _request(port, "POST", "/api/update/check")
    _, first = _request(port, "POST", "/api/update/apply")
    assert first["update"]["job"]["state"] == "running"
    res, payload = _request(port, "POST", "/api/update/apply")
    assert res.status == 409 and "уже идёт" in payload["error"]
    gate.set()
    assert _wait_job(port)["job"]["state"] == "done"


# ══════════════════════════════════════════════════════════════════
# АРХИВ РЕЛИЗА
# ══════════════════════════════════════════════════════════════════
def test_update_download_docker_rejected(srv_ctx):
    """Docker скачивает не архив, а образ: 400 с командой хоста."""
    _, port = srv_ctx("docker")
    res, payload = _request(port, "GET", "/api/update/download")
    assert res.status == 400 and "docker compose pull" in payload["error"]


def test_update_download_requires_check(port):
    """Без проверки скачивать нечего: 400 с подсказкой."""
    res, payload = _request(port, "GET", "/api/update/download")
    assert res.status == 400 and "Проверить" in payload["error"]


def test_update_download_missing_asset(port, update_state, monkeypatch):
    """В релизе нет портативного zip — 404 с понятной причиной."""
    update_state.update({"release": dict(_RELEASE), "checked": 1.0})

    def fake_json_get(url, **kw):
        return {"assets": [{"name": "другое.txt",
                            "browser_download_url": "https://x/f.txt"}]}
    monkeypatch.setattr(core_update.transport, "open_json_get", fake_json_get)
    res, payload = _request(port, "GET", "/api/update/download")
    assert res.status == 404 and "нет архива" in payload["error"]


def test_update_download_streams_zip(port, update_state, monkeypatch):
    """Успешный путь: zip релиза как attachment с именем версии."""
    update_state.update({"release": dict(_RELEASE), "checked": 1.0})
    seen: list[str] = []

    def fake_json_get(url, **kw):
        assert "/releases/tags/v9.9.0" in url
        return _ASSETS

    class Resp:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def iter_bytes(self):
            yield b"PK\x03\x04\x05\x06"

    def fake_get(url, **kw):
        seen.append(url)
        return Resp()
    monkeypatch.setattr(core_update.transport, "open_json_get", fake_json_get)
    monkeypatch.setattr(core_update.transport, "open_get", fake_get)
    res, raw = http_send(port, "GET", "/api/update/download")
    assert res.status == 200 and raw == b"PK\x03\x04\x05\x06"
    assert seen == ["https://x/zip"]
    assert res.headers.get("Content-Type") == "application/zip"
    assert f"{core_update.ASSET_PREFIX}9.9.0.zip" in \
        res.headers.get("Content-Disposition", "")

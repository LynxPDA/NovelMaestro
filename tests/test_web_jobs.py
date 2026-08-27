#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты M4: JobManager (Popen, буфер, stop, персистентность),
спеки стадий (build_command) и jobs/stages API через HTTP.

Фейковый скрипт — маленький python-код, печатающий строки с паузами;
без сети, всё в tmp_path.
"""
import json
import time
from pathlib import Path
from typing import cast

import pytest

from web.jobs import Job, JobManager, RING_SIZE
from web.stages import (
    STAGE_ORDER, STAGE_SPECS, build_command, ordered_stages, script_path,
    spec_for,
)

REPO = Path(__file__).resolve().parent.parent

# ── фейковый скрипт: печатает 10 строк по 0.1 c, код 0 ─────────────────
FAKE_SCRIPT = (
    "import sys, time\n"
    "for i in range(10):\n"
    "    print(f'line-{i}', flush=True)\n"
    "    time.sleep(0.05)\n"
    "sys.exit(0)\n"
)
FAKE_FAIL = "import sys\nprint('boom', flush=True)\nsys.exit(3)\n"
FAKE_HANG = "import time\nprint('start', flush=True)\ntime.sleep(60)\n"

# Родитель порождает потомка; потомок пишет pid и спит. Если stop убьёт
# только родителя — потомок-сирота останется жить (баг W2).
FAKE_PARENT_CHILD = (
    "import subprocess, sys, time\n"
    "kid = subprocess.Popen([sys.executable, '-c', \n"
    "    \"import time; time.sleep(120)\"], start_new_session=False)\n"
    "print(f'child-pid:{kid.pid}', flush=True)\n"
    "while True:\n"
    "    time.sleep(1)\n"
)

# строки лога вперемешку с событиями прогресса @@PROGRESS@@
FAKE_PROGRESS = (
    "import sys, time\n"
    "for i in range(3):\n"
    "    print(f'line-{i}', flush=True)\n"
    "    print('@@PROGRESS@@' + '{\"type\": \"progress\", '"
    "          '\"label\": \"Перевод\", \"done\": %d, \"total\": 3}' "
    "          % (i + 1), flush=True)\n"
    "    time.sleep(0.05)\n"
    "sys.exit(0)\n"
)
# Кривой JSON под префиксом — должен уйти в буфер строкой, без падения
FAKE_BAD_PROGRESS = (
    "import sys\n"
    "print('@@PROGRESS@@not-json', flush=True)\n"
    "sys.exit(0)\n"
)


@pytest.fixture()
def fake_script(tmp_path):
    """Папка с фейковыми скриптами: ok.py, fail.py, hang.py."""
    d = tmp_path / "bin"
    d.mkdir()
    (d / "ok.py").write_text(FAKE_SCRIPT, encoding="utf-8")
    (d / "fail.py").write_text(FAKE_FAIL, encoding="utf-8")
    (d / "hang.py").write_text(FAKE_HANG, encoding="utf-8")
    (d / "parent.py").write_text(FAKE_PARENT_CHILD, encoding="utf-8")
    (d / "progress.py").write_text(FAKE_PROGRESS, encoding="utf-8")
    (d / "bad_progress.py").write_text(FAKE_BAD_PROGRESS, encoding="utf-8")
    return d


def _wait_status(jm, job_id, *statuses, timeout=15.0):
    """Ждёт, пока статус job войдёт в statuses."""
    end = time.time() + timeout
    while time.time() < end:
        job = jm.get(job_id)
        assert job is not None
        if job.status in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"Статус не наступил: {jm.get(job_id).status}")


# ════════════════════════════════════════════════════════════════════
# JobManager


def test_start_done(tmp_path, fake_script):
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(fake_script / "ok.py")], tmp_path)
    assert job.status == "running"
    job = _wait_status(jm, job.id, "done")
    assert job.exit_code == 0
    assert job.finished is not None
    assert len(job.lines) == 10
    assert job.lines[0] == "line-0"
    assert job.lines[-1] == "line-9"


def test_start_failed(tmp_path, fake_script):
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(fake_script / "fail.py")], tmp_path)
    job = _wait_status(jm, job.id, "failed")
    assert job.exit_code == 3
    assert job.lines[0] == "boom"


def test_dashboard_running_jobs(tmp_path, fake_script):
    """/api/dashboard через _dashboard включает активные запуски
    с хвостом лога (running_jobs)."""
    from web.api import _dashboard

    jm = JobManager(tmp_path, python="python3")
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(fake_script / "hang.py")], tmp_path)
    assert job.status == "running"
    ctx = {"job_manager": jm, "projects_root": tmp_path}
    d = _dashboard(ctx)
    running = [j for j in d.get("running_jobs", []) if j["id"] == job.id]
    assert running, "активный запуск должен попасть в running_jobs"
    assert running[0]["status"] == "running"
    # хвост буфера: дождаться первой строки из скрипта
    for _ in range(100):
        if any("start" in l for l in running[0]["lines"]):
            break
        time.sleep(0.05)
        running = [j for j in _dashboard(ctx)["running_jobs"]
                   if j["id"] == job.id]
    assert any("start" in l for l in running[0]["lines"])
    # после завершения — исчезает
    jm.stop(job.id)
    job = _wait_status(jm, job.id, "stopped", "failed", "done")
    d2 = _dashboard(ctx)
    assert not any(j["id"] == job.id for j in d2.get("running_jobs", []))


def test_start_missing_script(tmp_path):
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(tmp_path / "no_such.py")], tmp_path)
    # python3 стартует, но скрипта нет — процесс падает с кодом != 0
    job = _wait_status(jm, job.id, "failed")
    assert job.exit_code != 0
    assert job.finished is not None


def test_ring_buffer_cap(tmp_path, fake_script):
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(fake_script / "ok.py")], tmp_path)
    _wait_status(jm, job.id, "done")
    # переполняем буфер вручную
    for i in range(RING_SIZE + 100):
        job.append(f"x-{i}")
    assert len(job.lines) == RING_SIZE
    assert job.lines[-1] == f"x-{RING_SIZE + 99}"


def test_stop_terminates(tmp_path, fake_script):
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(fake_script / "hang.py")], tmp_path)
    _wait_status(jm, job.id, "running")
    # ждём первую строку — процесс точно жив
    time.sleep(0.3)
    jm.stop(job.id)
    job = _wait_status(jm, job.id, "stopped", "done", "failed")
    assert job.status in ("stopped", "done", "failed")
    # finished ставит _kill_later после реальной смерти процесса
    end = time.time() + 10
    while job.finished is None and time.time() < end:
        time.sleep(0.05)
        cur = jm.get(job.id)
        assert cur is not None
        job = cur
    assert job.finished is not None


def test_stop_kills_child_process_group(tmp_path, fake_script):
    """W2: stop убивает ГРУППУ — дочерний процесс не остаётся сиротой."""
    import os
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("test", "Родитель с потомком", "ACTIVE/x",
                   [str(fake_script / "parent.py")], tmp_path)
    end = time.time() + 10
    child_pid = None
    while time.time() < end:
        for line in job.lines:
            if line.startswith("child-pid:"):
                child_pid = int(line.split(":", 1)[1])
                break
        if child_pid:
            break
        time.sleep(0.05)
    assert child_pid, "потомок не запущен / pid не получен"
    jm.stop(job.id)
    _wait_status(jm, job.id, "stopped", "done", "failed", timeout=15)
    end = time.time() + 5
    alive = True
    while time.time() < end:
        try:
            os.kill(child_pid, 0)  # сигнал 0 — проверка существования
        except OSError:
            alive = False
            break
        time.sleep(0.1)
    assert not alive, f"потомок pid={child_pid} остался жив после stop"


def test_stop_unknown(tmp_path):
    jm = JobManager(tmp_path, python="python3")
    assert jm.stop("nope") is None


def test_remove(tmp_path, fake_script):
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(fake_script / "ok.py")], tmp_path)
    _wait_status(jm, job.id, "done")
    assert jm.remove(job.id) is True
    assert jm.get(job.id) is None
    assert jm.remove(job.id) is False


def test_persist_metadata(tmp_path, fake_script):
    """Хвост лога персистится в job_logs/{id}.log и читается обратно;
    argv/секреты в jobs.json не попадают."""
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(fake_script / "ok.py"), "--api_key", "SECRET"],
                   tmp_path)
    _wait_status(jm, job.id, "done")
    # сайдкар хвоста на месте
    assert (tmp_path / "job_logs" / f"{job.id}.log").is_file()
    # новый менеджер на той же папке — читает jobs.json + сайдкар
    jm2 = JobManager(tmp_path, python="python3")
    loaded = jm2.get(job.id)
    assert loaded is not None
    assert loaded.status == "done"
    assert loaded.exit_code == 0
    assert loaded.title == "Тест"
    assert loaded.lines == [f"line-{i}" for i in range(10)]
    assert loaded.lines[-1] == "line-9"
    # секретов нет ни в jobs.json, ни в сайдкаре
    data = (tmp_path / "jobs.json").read_text(encoding="utf-8")
    assert "SECRET" not in data
    assert "argv" not in data
    side = (tmp_path / "job_logs" / f"{job.id}.log").read_text(
        encoding="utf-8")
    assert "SECRET" not in side


def test_subscribe_lines(tmp_path, fake_script):
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(fake_script / "ok.py")], tmp_path)
    seen = []
    q = job.subscribe()
    _wait_status(jm, job.id, "done")
    try:
        while True:
            ev = q.get(timeout=0.1)
            seen.append(ev)
    except Exception:
        pass
    kinds = {e[0] for e in seen}
    assert "line" in kinds
    assert "status" in kinds
    assert ("status", "done") in seen
    job.unsubscribe(q)


# ════════════════════════════════════════════════════════════════════
# прогресс @@PROGRESS@@ → job.progress / payload / SSE


def test_progress_events(tmp_path, fake_script):
    """Строки @@PROGRESS@@ парсятся в job.progress (последнее событие),
    в буфер строк не попадают."""
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("translate", "Перевод", "ACTIVE/x",
                   [str(fake_script / "progress.py")], tmp_path)
    _wait_status(jm, job.id, "done")
    assert job.progress is not None
    assert job.progress["type"] == "progress"
    assert job.progress["done"] == 3
    assert job.progress["total"] == 3
    assert job.progress["label"] == "Перевод"
    # событий нет в буфере строк
    assert not any("@@PROGRESS@@" in l for l in job.lines)
    assert job.lines == ["line-0", "line-1", "line-2"]
    # payload несёт progress
    assert job.payload()["progress"]["done"] == 3


def test_progress_persist(tmp_path, fake_script):
    """progress персистится в jobs.json и восстанавливается при рестарте."""
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("translate", "Перевод", "ACTIVE/x",
                   [str(fake_script / "progress.py")], tmp_path)
    _wait_status(jm, job.id, "done")
    # в jobs.json есть progress и нет argv
    data = (tmp_path / "jobs.json").read_text(encoding="utf-8")
    assert "\"progress\"" in data
    assert "argv" not in data
    # новый менеджер на той же папке — progress восстановлен
    jm2 = JobManager(tmp_path, python="python3")
    loaded = jm2.get(job.id)
    assert loaded is not None
    assert loaded.progress == job.progress
    assert loaded.progress is not None
    assert loaded.progress["done"] == 3


def test_progress_subscribe(tmp_path, fake_script):
    """Подписчик получает ("progress", ev) и последнее значение — финал."""
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("translate", "Перевод", "ACTIVE/x",
                   [str(fake_script / "progress.py")], tmp_path)
    seen = []
    q = job.subscribe()
    _wait_status(jm, job.id, "done")
    try:
        while True:
            ev = q.get(timeout=0.1)
            seen.append(ev)
    except Exception:
        pass
    prog = [e for e in seen if e[0] == "progress"]
    assert prog, "подписчик не получил событий прогресса"
    assert prog[-1][1]["done"] == 3
    job.unsubscribe(q)


def test_progress_bad_json(tmp_path, fake_script):
    """Кривой JSON под префиксом — обычная строка в буфер, без падения."""
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("translate", "Перевод", "ACTIVE/x",
                   [str(fake_script / "bad_progress.py")], tmp_path)
    _wait_status(jm, job.id, "done")
    assert job.progress is None
    assert any("@@PROGRESS@@" in l for l in job.lines)


def test_dashboard_progress(tmp_path, fake_script):
    """_dashboard: running_jobs (payload) и recent_jobs (_serialize)
    несут progress."""
    from web.api import _dashboard

    jm = JobManager(tmp_path, python="python3")
    job = jm.start("translate", "Перевод", "ACTIVE/x",
                   [str(fake_script / "progress.py")], tmp_path)
    ctx = {"job_manager": jm, "projects_root": tmp_path}
    # пока запуск жив — running_jobs (payload) несёт progress
    running = []
    for _ in range(100):
        d = _dashboard(ctx)
        running = [j for j in d.get("running_jobs", [])
                   if j["id"] == job.id]
        if running and running[0].get("progress"):
            break
        time.sleep(0.05)
    assert running, "активный запуск должен быть в running_jobs"
    assert running[0].get("progress"), "running_jobs без progress"
    assert running[0]["progress"]["done"] >= 1
    _wait_status(jm, job.id, "done")
    # после завершения — recent_jobs (_serialize) тоже с progress
    d2 = _dashboard(ctx)
    recent = [j for j in d2.get("recent_jobs", []) if j["id"] == job.id]
    assert recent and recent[0].get("progress"), "recent_jobs без progress"
    assert recent[0]["progress"]["done"] == 3


def test_progress_http(jobs_srv, fake_script):
    """HTTP: GET /api/jobs/{id} и /api/dashboard отдают progress."""
    port, req, jm = jobs_srv
    job = jm.start("translate", "Перевод", "ACTIVE/x",
                   [str(fake_script / "progress.py")], Path("."))
    _wait_status(jm, job.id, "done")
    res, payload = req("GET", f"/api/jobs/{job.id}")
    assert res.status == 200
    assert payload["job"]["progress"]["done"] == 3
    res, payload = req("GET", "/api/dashboard")
    assert res.status == 200
    recent = [j for j in payload.get("recent_jobs", [])
              if j["id"] == job.id]
    assert recent and recent[0].get("progress"), "дашборд без progress"


def test_reconcile_dead_pid(tmp_path, fake_script):
    """R15: «running»-запуск с мёртвым pid при загрузке → failed
    (процесс не пережил рестарт сервера)."""
    import os
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(fake_script / "ok.py")], tmp_path)
    _wait_status(jm, job.id, "done")
    # перезаписываем метаданные: running + несуществующий pid
    data = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    for item in data:
        if item["id"] == job.id:
            item["status"] = "running"
            item["pid"] = 999999999  # заведомо мёртвый
    (tmp_path / "jobs.json").write_text(json.dumps(data), encoding="utf-8")
    jm2 = JobManager(tmp_path, python="python3")
    loaded = jm2.get(job.id)
    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.finished is not None
    # статус зафиксирован в jobs.json
    data2 = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    fixed = [i for i in data2 if i["id"] == job.id][0]
    assert fixed["status"] == "failed"


def test_reconcile_alive_pid(tmp_path, fake_script):
    """R15: «running»-запуск с живым pid остаётся running и управляемым
    (stop по pid)."""
    import subprocess
    import time as _time
    # живой дочерний процесс
    sleeper = subprocess.Popen(["python3", "-c",
                                "import time; time.sleep(120)"])
    try:
        jm = JobManager(tmp_path, python="python3")
        job = jm.start("test", "Тест", "ACTIVE/x",
                       [str(fake_script / "hang.py")], tmp_path)
        _wait_status(jm, job.id, "running")
        # симулируем рестарт: новый менеджер, pid живого процесса
        data = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
        for item in data:
            if item["id"] == job.id:
                item["status"] = "running"
                item["pid"] = sleeper.pid
        (tmp_path / "jobs.json").write_text(json.dumps(data), encoding="utf-8")
        jm2 = JobManager(tmp_path, python="python3")
        loaded = jm2.get(job.id)
        assert loaded is not None
        assert loaded.status == "running"
        assert loaded.pid == sleeper.pid
        # stop сироты по pid убивает процесс
        jm2.stop(job.id)
        end = _time.time() + 10
        while _time.time() < end:
            cur = jm2.get(job.id)
            assert cur is not None
            if cur.status != "running":
                break
            _time.sleep(0.1)
        cur = jm2.get(job.id)
        assert cur is not None and cur.status == "stopped"
        # процесс реально умер
        try:
            sleeper.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        assert sleeper.poll() is not None, "сирота не остановлен по pid"
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait()


def test_orphan_watcher_marks_dead(tmp_path, fake_script, monkeypatch):
    """B1: сирота (running без proc, pid умер после рестарта) —
    фоновый наблюдатель помечает failed, не дожидаясь перезагрузки."""
    import web.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "ORPHAN_POLL", 0.05)
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(fake_script / "ok.py")], tmp_path)
    _wait_status(jm, job.id, "done")
    # превращаем в сироту: running + мёртвый pid + proc=None
    job.status = "running"
    job.pid = 999999999
    job.proc = None
    job.finished = None
    job = _wait_status(jm, job.id, "failed")
    assert job.exit_code == 1
    assert job.finished is not None
    # статус зафиксирован на диске (persist)
    data = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    fixed = [i for i in data if i["id"] == job.id][0]
    assert fixed["status"] == "failed"


def test_orphan_watcher_ignores_live(tmp_path, fake_script, monkeypatch):
    """B1: сирота с ЖИВЫМ pid остаётся running (наблюдатель не трогает)."""
    import subprocess
    import web.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "ORPHAN_POLL", 0.05)
    sleeper = subprocess.Popen(["python3", "-c",
                                "import time; time.sleep(120)"])
    try:
        jm = JobManager(tmp_path, python="python3")
        job = jm.start("test", "Тест", "ACTIVE/x",
                       [str(fake_script / "hang.py")], tmp_path)
        _wait_status(jm, job.id, "running")
        job.proc = None
        job.pid = sleeper.pid
        time.sleep(0.3)  # несколько циклов наблюдателя
        cur = jm.get(job.id)
        assert cur is not None and cur.status == "running"
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait()


def test_remove_stops_orphan(tmp_path, fake_script):
    """B2: remove() сироты останавливает живой процесс (proc=None,
    pid жив) — раньше SIGTERM не уходил и процесс работал дальше."""
    import subprocess
    import time as _time
    sleeper = subprocess.Popen(["python3", "-c",
                                "import time; time.sleep(120)"])
    try:
        jm = JobManager(tmp_path, python="python3")
        job = jm.start("test", "Тест", "ACTIVE/x",
                       [str(fake_script / "hang.py")], tmp_path)
        _wait_status(jm, job.id, "running")
        # сирота: proc=None, pid — живой внешний процесс
        job.proc = None
        job.pid = sleeper.pid
        assert jm.remove(job.id) is True
        assert jm.get(job.id) is None
        try:
            sleeper.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        assert sleeper.poll() is not None, "сирота не остановлен при remove"
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait()


def test_shutdown_stops_running(tmp_path, fake_script):
    """R15: shutdown() останавливает все активные запуски (завершение
    сервера — никаких процессов-сирот)."""
    jm = JobManager(tmp_path, python="python3")
    job1 = jm.start("test", "Тест 1", "ACTIVE/x",
                    [str(fake_script / "hang.py")], tmp_path)
    job2 = jm.start("test", "Тест 2", "ACTIVE/y",
                    [str(fake_script / "hang.py")], tmp_path)
    _wait_status(jm, job1.id, "running")
    _wait_status(jm, job2.id, "running")
    jm.shutdown()
    j1 = jm.get(job1.id)
    j2 = jm.get(job2.id)
    assert j1 is not None and j1.status == "stopped"
    assert j2 is not None and j2.status == "stopped"
    assert j1.finished is not None
    assert j2.finished is not None


def test_pid_in_payload(tmp_path, fake_script):
    """R15: pid процесса доступен в payload (для UI-управления),
    argv — нет (секреты)."""
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(fake_script / "hang.py"), "--api_key", "SECRET"],
                   tmp_path)
    _wait_status(jm, job.id, "running")
    payload = job.payload()
    assert payload["pid"] == job.pid
    assert "SECRET" not in json.dumps(payload)
    jm.stop(job.id)
    _wait_status(jm, job.id, "stopped", "done", "failed")


# ════════════════════════════════════════════════════════════════════
# спеки стадий


def test_all_m4_stages_present():
    assert {"epub", "translate_check", "compile", "batch_replace"} <= set(STAGE_SPECS)
    for key, spec in STAGE_SPECS.items():
        assert spec["title"]
        assert spec["script"].endswith(".py")
        assert callable(spec["build"])
        assert isinstance(spec["fields"], list)


def test_stage_order_and_titles():
    """R6-D: порядок стадий = STAGE_ORDER; названия без имён .py;
    LLM-пометки в названиях LLM-стадий."""
    assert set(STAGE_ORDER) == set(STAGE_SPECS)
    keys = [k for k, _ in ordered_stages()]
    assert keys == STAGE_ORDER
    assert STAGE_ORDER == [
        "epub", "ner", "ner_check", "pipeline", "translate_check",
        "translate_check_llm", "batch_replace", "compile", "wiki",
    ]
    for key, spec in STAGE_SPECS.items():
        assert ".py" not in spec["title"], key
    assert STAGE_SPECS["ner_check"]["title"] == "Проверка глоссария (LLM)"
    assert STAGE_SPECS["pipeline"]["title"].endswith("(LLM)")
    assert STAGE_SPECS["translate_check"]["title"] == \
        "Проверка перевода"
    assert STAGE_SPECS["translate_check_llm"]["title"] == "Проверка перевода (LLM)"


def test_script_path(tmp_path):
    # репо — настоящий, значит cli/*.py существуют
    for key in ("epub", "translate_check", "compile", "batch_replace"):
        p = script_path(key, REPO)
        assert p is not None and p.is_file(), key


def test_build_epub_to_chapters_defaults():
    argv = build_command("epub", {}, {})
    assert argv[0] == "cli/epub_to_chapters.py"
    # дефолты: ничего не добавляется
    assert argv == ["cli/epub_to_chapters.py"]


def test_build_epub_to_chapters_full():
    form = {"input": "source/book.epub", "lang": "zh", "polished": True,
            "clean": 0, "remove_pages": 0, "dry_run": True,
            "chapter_re": "第\\d+章", "book_title": "Книга",
            "chunk_size": "5000", "clean_output": True}
    argv = build_command("epub", form, {})
    assert "--input" in argv and "source/book.epub" in argv
    assert "--lang" in argv and "zh" in argv
    assert "--polished" in argv and "1" in argv
    assert "--clean" in argv and "0" in argv
    assert "--remove-pages" in argv and "0" in argv
    assert "--dry-run" in argv
    assert "--chapter-re" in argv and "第\\d+章" in argv
    assert "--book-title" in argv and "Книга" in argv
    assert "--chunk-size" in argv and "5000" in argv
    assert "--clean-output" in argv


def test_build_translate_check():
    # подписи пресетов web-формы маппятся на числовой --preset
    form = {"preset": "redacted", "start": 5, "end": 10, "lenient": True}
    argv = build_command("translate_check", form, {})
    assert argv[0] == "cli/translate_check.py"
    assert "--preset" in argv and "2" in argv
    assert "--start" in argv and "5" in argv
    assert "--end" in argv and "10" in argv
    assert "--lenient" in argv
    # без диапазона — только пресет
    argv2 = build_command("translate_check", {"preset": "polished"}, {})
    assert "--preset" in argv2 and "1" in argv2
    assert "--start" not in argv2 and "--end" not in argv2
    # неизвестная подпись — передаётся как есть
    argv3 = build_command("translate_check", {"preset": "9"}, {})
    assert "--preset" in argv3 and "9" in argv3
    # R9: слова-исключения → --exclude-words
    argv4 = build_command("translate_check",
                          {"preset": "polished",
                           "exclude_words": "VIP,NPC"}, {})
    assert "--exclude-words" in argv4
    assert argv4[argv4.index("--exclude-words") + 1] == "VIP,NPC"
    argv5 = build_command("translate_check",
                          {"preset": "polished", "exclude_words": ""}, {})
    assert "--exclude-words" not in argv5



def test_build_clean_and_compile():
    form = {"mode": "epub", "source_type": "redacted", "chunk_size": 50,
            "no_donate": True, "no_fb2_cover": True,
            "donate_file": "donate.txt"}
    argv = build_command("compile", form, {})
    assert "--mode" in argv and "epub" in argv
    assert "--source-type" in argv and "redacted" in argv
    assert "--chunk-size" in argv and "50" in argv
    assert "--no-donate" in argv
    assert "--no-fb2-cover" in argv
    assert "--donate-file" in argv and "donate.txt" in argv
    # дефолтный mode = txt
    argv2 = build_command("compile", {}, {})
    assert argv2[argv2.index("--mode") + 1] == "txt"


def test_build_batch_replace():
    form = {"rules_file": "prompts/replacements.txt", "type": "polished",
            "start": 1, "end": 5, "regex": True, "dry_run": True}
    argv = build_command("batch_replace", form, {})
    assert "--rules-file" in argv
    assert "--type" in argv and "polished" in argv
    assert "--start" in argv and "1" in argv
    assert "--end" in argv and "5" in argv
    assert "--regex" in argv
    assert "--dry-run" in argv


def test_spec_for_strips_build():
    spec = spec_for("epub")
    assert spec is not None and "build" not in spec
    assert spec_for("nope") is None


def test_build_unknown_stage():
    with pytest.raises(ValueError):
        build_command("nope", {}, {})


# ════════════════════════════════════════════════════════════════════
# пресеты «Простого режима» (карточка запуска вместо формы)


def test_presets_all_stages():
    """У стадий с простым режимом — пресет {title, desc} + непустой
    список simple; params = непустые дефолты полей + overrides;
    LLM-полей нет (скрипты берут сервер из .env). Стадии без simple
    (translate_check/batch_replace/compile) — только экспертные."""
    from web.stages import preset_params
    expert_only = ("translate_check", "batch_replace", "compile")
    for key in STAGE_ORDER:
        spec = STAGE_SPECS[key]
        names = {f["name"] for f in spec["fields"]}
        if key in expert_only:
            assert spec.get("preset") is None, key
            assert not spec.get("simple"), key
            continue
        preset = spec.get("preset")
        assert preset is not None, key
        assert preset.get("title"), key
        assert preset.get("desc"), key
        simple = spec.get("simple") or []
        assert simple, key  # есть простой режим — есть и поля к карточке
        assert set(simple) <= names, key
        params = preset_params(spec)
        assert set(params) <= names, key
        assert not {"host", "model", "api_key"} & set(params), key
        # эталон: непустые дефолты полей + overrides пресета
        expected = {}
        for f in spec["fields"]:
            d = f.get("default")
            if f["type"] == "bool":
                expected[f["name"]] = bool(d)
            elif d is None or str(d) == "":
                continue
            elif f["type"] == "files":
                v = str(d)
                if f.get("dir") and "/" not in v:
                    v = f"{f['dir']}/{v}"
                expected[f["name"]] = v
            else:
                expected[f["name"]] = str(d)
        expected.update(spec.get("preset", {}).get("overrides") or {})
        assert params == expected, key


def test_simple_fields_per_stage():
    """Состав простого режима по стадиям (согласовано с ТЗ): какие
    поля показываются в простом режиме к карточке пресета."""
    expected = {
        "epub": ["input", "lang"],
        "ner": ["mode", "prompt_file", "two_pass"],
        "ner_check": ["prompt_file", "passes"],
        "pipeline": ["action", "prompt_file"],
        "translate_check_llm": ["type", "two_pass", "prompt_file"],
        "wiki": ["source", "file", "type", "prompt_file", "top",
                 "min_count", "format"],
    }
    for key, names in expected.items():
        assert STAGE_SPECS[key]["simple"] == names, key


def test_ner_check_no_report_no_apply():
    """ner_check: отчёт (ner_report.md) и --apply выпилены из формы
    Запусков; apply остался в build (его шлёт /api/ner/review/apply)."""
    spec = STAGE_SPECS["ner_check"]
    assert "report" not in {f["name"] for f in spec["fields"]}
    assert "apply" not in {f["name"] for f in spec["fields"]}
    argv = build_command("ner_check", {"apply": True, "input": "ner.json"},
                         {})
    assert "--apply" in argv


def test_compile_donate_autofile():
    """compile: donate_file с autofile source/donate.txt (автоподхват)."""
    spec = STAGE_SPECS["compile"]
    f = next(x for x in spec["fields"] if x["name"] == "donate_file")
    assert f.get("autofile") == "source/donate.txt"


def test_preset_spot_checks():
    """Точечные проверки: что реально уедет в params при нажатии
    «Запустить» в простом режиме."""
    from web.stages import preset_params
    # ner: compile без входного txt
    params = preset_params(STAGE_SPECS["ner"])
    assert params["mode"] == "compile"
    assert "file" not in params
    assert params["ner_file"] == "ner.json"
    # pipeline: полный цикл, дефолты, без диапазона
    params = preset_params(STAGE_SPECS["pipeline"])
    assert params["action"] == "8"
    assert params["jobs"] == "4"
    assert "start" not in params and "end" not in params
    # epub: автопоиск исходника + пресет языка zh
    params = preset_params(STAGE_SPECS["epub"])
    assert params["lang"] == "zh"
    assert "input" not in params
    # wiki: ner.json + дефолтные настройки
    params = preset_params(STAGE_SPECS["wiki"])
    assert params["ner_file"] == "ner.json"
    assert params["top"] == "80"


def test_preset_llm_stage_argv_buildable():
    """params простого режима LLM-стадий проходят build_command без
    ошибок (нет обязательных полей, которые бы уронили сборку)."""
    from web.stages import preset_params
    for key in ("ner", "ner_check", "translate_check_llm", "wiki"):
        params = preset_params(STAGE_SPECS[key])
        argv = build_command(key, params, {})
        assert argv[0].endswith(".py"), key


# ════════════════════════════════════════════════════════════════════
# U2: дефолты формы == дефолты argparse скриптов (единый источник)


def test_form_defaults_match_script_argparse():
    """Известные расхождения дефолтов (B6) выравнены: форма → скрипт.
    Таблица: (стадия, поле) → (build_parser, argparse-dest). При
    расхождении — скрипт выравнивается под форму (web — основной UI)."""
    from cli.ner import build_parser as ner_parser
    from cli.ner_check import build_parser as ner_check_parser
    from cli.translate_check_llm import build_parser as tcl_parser

    table = [
        ("ner", "threads", ner_parser, "threads"),
        ("ner_check", "timeout", ner_check_parser, "timeout"),
        ("ner_check", "stream_timeout", ner_check_parser,
         "stream_timeout"),
        ("translate_check_llm", "max_retries", tcl_parser, "max_retries"),
    ]
    for stage, field, build, dest in table:
        form_default = STAGE_SPECS[stage]["fields"]
        f = next(x for x in form_default if x["name"] == field)
        assert str(f["default"]) == str(build().get_default(dest)), \
            f"{stage}.{field}: форма {f['default']} ≠ скрипт " \
            f"{build().get_default(dest)}"


# ════════════════════════════════════════════════════════════════════
# M5: LLM-стадии (2/n/5/7) — build_command + профиль .env


def test_m5_stages_in_specs():
    for key in ("ner", "ner_check", "translate_check_llm", "wiki"):
        spec = spec_for(key)
        assert spec is not None, key
        assert spec["script"] in ("ner.py", "ner_check.py",
                                   "translate_check_llm.py", "wiki.py")
        # LLM-поля обязательны (profile убран)
        names = [f["name"] for f in spec["fields"]]
        assert "profile" not in names
        assert "host" in names and "model" in names and "api_key" in names
        api_f = next(f for f in spec["fields"] if f["name"] == "api_key")
        assert api_f["type"] == "password"


def test_build_ner_defaults_and_flags():
    form = {"file": "compiled_book.txt", "ner_file": "ner.json",
            "prompt_file": "prompts/ner.txt", "threads": "4",
            "chunk_size": "7000", "threshold": "0.75", "ngram": "3",
            "two_pass": True, "strip_meta": True, "min_count": "2",
            "save_interval": "10", "retries": "3", "timeout": "900"}
    argv = build_command("ner", form, {})
    assert argv[0] == "cli/ner.py"
    assert "compiled_book.txt" in argv
    assert "--two-pass" in argv and "--strip-meta" in argv
    assert "--min-count" in argv and "2" in argv
    assert "--chunk_size" in argv and "7000" in argv
    assert "--save-interval" in argv
    # пустые значения не дают флагов
    assert "--reasoning-effort" not in argv
    assert "--temperature" not in argv


def test_ner_spec_no_keep_all():
    """keep_all убран из формы NER; флаг --keep-all-fields не строится."""
    spec = spec_for("ner")
    assert spec is not None
    names = [f["name"] for f in spec["fields"]]
    assert "keep_all" not in names
    argv = build_command("ner", {"file": "x.txt", "keep_fields": ""}, {})
    assert "--keep-all-fields" not in argv
    assert "--keep-fields" not in argv
    argv2 = build_command("ner", {"file": "x.txt",
                                  "keep_fields": "notes,context"}, {})
    assert "--keep-fields" in argv2
    assert "--keep-all-fields" not in argv2


def test_stage_spec_env_prefill(jobs_srv, tmp_path):
    """C/D: предзаполнение spec из .env — bool "0" → False, files → basename."""
    port, req, _jm = jobs_srv
    _make_project(port, req)
    pdir = tmp_path / "projects" / "ACTIVE" / "test_book"
    (pdir / "prompts" / "ner_prompt.txt").write_text("промпт",
                                                       encoding="utf-8")
    (pdir / ".env").write_text(
        "NER_STRIP_META=0\n"
        "NER_PROMPT_FILE=prompts/ner_prompt.txt\n",
        encoding="utf-8")
    res, payload = req("GET", "/api/stages/ner/spec?project=ACTIVE/test_book")
    assert res.status == 200
    fields = {f["name"]: f for f in payload["spec"]["fields"]}
    # D: "0" не truthy — чекбокс не вспыхивает (именно bool, не строка)
    assert fields["strip_meta"]["default"] is False
    # C: полный путь из .env → basename, селект находит option
    assert fields["prompt_file"]["default"] == "ner_prompt.txt"


def test_stage_spec_env_prefill_skips_missing_file(jobs_srv, tmp_path):
    """files из .env предзаполняется ТОЛЬКО если файл реально существует:
    удалённый промпт не остаётся «подхваченным» после перезагрузки."""
    port, req, _jm = jobs_srv
    _make_project(port, req)
    pdir = tmp_path / "projects" / "ACTIVE" / "test_book"
    (pdir / ".env").write_text(
        "PIPELINE_TRANSLATE_PROMPT=translate_prompt.txt\n"
        "PIPELINE_REDACT_PROMPT=redact_prompt.txt\n",
        encoding="utf-8")
    res, payload = req("GET",
                       "/api/stages/pipeline/spec?project=ACTIVE/test_book")
    assert res.status == 200
    fields = {f["name"]: f for f in payload["spec"]["fields"]}
    # файлов нет в prompts/ → дефолт остаётся пустым (не подхватываем)
    assert fields["translate_prompt"]["default"] == ""
    assert fields["redact_prompt"]["default"] == ""


def test_stage_spec_env_prefill_bool_on(jobs_srv, tmp_path):
    """D: =1 → default True (bool)."""
    port, req, _jm = jobs_srv
    _make_project(port, req)
    pdir = tmp_path / "projects" / "ACTIVE" / "test_book"
    (pdir / ".env").write_text("NER_STRIP_META=1\n", encoding="utf-8")
    res, payload = req("GET", "/api/stages/ner/spec?project=ACTIVE/test_book")
    assert res.status == 200
    fields = {f["name"]: f for f in payload["spec"]["fields"]}
    assert fields["strip_meta"]["default"] is True


def test_build_ner_modes():
    """extract / finetune / compile (с диапазоном) / postprocess."""

    # compile: --compile_chapters + диапазон глав
    compile_argv = build_command(
        "ner", {"mode": "compile", "start": "1", "end": "20",
                "ner_file": "ner.json", "threads": "4"}, {})
    assert "--compile_chapters" in compile_argv
    assert compile_argv[1] == "--compile_chapters"
    assert "--start" in compile_argv and "1" in compile_argv
    assert "--end" in compile_argv and "20" in compile_argv
    # LLM-флаги собираются для compile
    assert "--threads" in compile_argv and "4" in compile_argv

    # extract / finetune: файл позиционный, LLM-флаги есть
    for mode in ("extract", "finetune"):
        argv = build_command(
            "ner", {"mode": mode, "file": "book.txt",
                    "ner_file": "ner.json", "threads": "4"}, {})
        assert "book.txt" in argv
        assert "--compile_chapters" not in argv
        assert "--threads" in argv

    # postprocess: без файла и без LLM-флагов
    post_argv = build_command(
        "ner", {"mode": "postprocess", "ner_file": "ner.json",
                "strip_meta": True, "min_count": "2"}, {})
    assert "--compile_chapters" not in post_argv
    assert "--strip-meta" in post_argv and "--min-count" in post_argv
    assert "book.txt" not in post_argv
    assert "--host" not in post_argv and "--model" not in post_argv
    assert "--threads" not in post_argv


def test_build_ner_check_flags():
    form = {"input": "ner.json",
            "review": "ner_review.json", "passes": "types",
            "types": "Person", "count_threshold": "2",
            "exclude_words": "палладия", "show_aliases": True,
            "show_votes": True, "dry_run": True}
    argv = build_command("ner_check", form, {})
    assert argv[0] == "cli/ner_check.py"
    assert "--input" in argv and "ner.json" in argv
    assert "--passes" in argv and "types" in argv
    assert "-c" in argv and "2" in argv
    assert "--exclude-words" in argv
    assert "--show-aliases" in argv and "--show-votes" in argv
    assert "--dry-run" in argv
    assert "--apply" not in argv and "--auto-apply" not in argv


def test_build_ner_check_no_bak():
    """no_bak: true → --no-bak в argv; false/нет — флага нет."""
    argv = build_command("ner_check", {"no_bak": True}, {})
    assert "--no-bak" in argv
    argv2 = build_command("ner_check", {"no_bak": False}, {})
    assert "--no-bak" not in argv2
    argv3 = build_command("ner_check", {"apply": True}, {})
    assert "--no-bak" not in argv3


def test_build_translate_check_llm_no_bak():
    """Флаги применения/бэкапов из «Запусков» всегда выключены."""
    argv = build_command("translate_check_llm", {"no_bak": True}, {})
    assert "--no-bak" not in argv
    argv2 = build_command("translate_check_llm", {"no_bak": False}, {})
    assert "--no-bak" not in argv2


def test_build_translate_check_llm_flags():
    form = {"type": "polished", "start": "1", "end": "10",
            "two_pass": True, "context_budget": "75000",
            "review": "translate_check_llm_review.json", "dry_run": True,
            "apply": True, "auto_apply": True,
            "reasoning_effort": "low",
            "threads": "4", "max_fixes_per_chapter": "0"}
    argv = build_command("translate_check_llm", form, {})
    assert argv[0] == "cli/translate_check_llm.py"
    assert "--start" in argv and "--end" in argv
    assert "--two_pass" in argv and "--context_budget" in argv
    # применение/предпросмотр/бэкапы — только через «Проверка» проекта
    assert "--dry-run" not in argv
    assert "--apply" not in argv and "--auto-apply" not in argv
    assert "--no-bak" not in argv
    assert "--reasoning_effort" in argv and "low" in argv
    assert "--no_reasoning" not in argv
    assert "--threads" in argv and "--max_fixes_per_chapter" in argv

    # none в text-поле = --no_reasoning
    form2 = dict(form, reasoning_effort="none")
    argv2 = build_command("translate_check_llm", form2, {})
    assert "--no_reasoning" in argv2
    assert "--reasoning_effort" not in argv2
    # произвольные значения (xhigh/max) передаются как есть
    form3 = dict(form, reasoning_effort="xhigh")
    argv3 = build_command("translate_check_llm", form3, {})
    assert "--reasoning_effort" in argv3 and "xhigh" in argv3


def test_build_wiki_flags():
    form = {"file": "compiled_book.txt", "ner_file": "ner.json",
            "output": "wiki.md", "top": "80", "min_count": "2",
            "exclude_types": "Other", "types": "Person",
            "context_chunks": "12", "near_distance": "64",
            "chunk_size": "1000", "co_occurrence_pairs": "Person:Person",
            "co_occurrence_top": "5", "format": "rulate-md",
            "thinking": "medium"}
    argv = build_command("wiki", form, {})
    assert argv[0] == "cli/wiki.py"
    assert "compiled_book.txt" in argv
    assert "--near-distance" in argv and "64" in argv
    assert "--chunk-size" in argv and "1000" in argv
    assert "--co-occurrence-pairs" in argv
    assert "--rulate-mode" in argv
    assert "--thinking" in argv and "medium" in argv


def test_build_wiki_compile_chapters():
    """wiki: источник «собрать из глав» → --compile-chapters + тип/диапазон."""
    form = {"source": "chapters", "type": "polished",
            "start": "1", "end": "20", "ner_file": "ner.json"}
    argv = build_command("wiki", form, {})
    assert "--compile-chapters" in argv
    assert "--type" in argv and "polished" in argv
    assert "--start" in argv and "1" in argv
    assert "--end" in argv and "20" in argv
    assert "file" not in argv


def test_build_wiki_rulate_html():
    """wiki: rulate-html → --rulate-html, дефолтный выход wiki.html."""
    argv = build_command("wiki", {"format": "rulate-html"}, {})
    assert "--rulate-html" in argv
    assert "--output" in argv and "wiki.html" in argv
    assert "--rulate-mode" not in argv


def test_build_wiki_toc_off():
    """wiki: toc/toc_links выключены → --no-toc/--no-toc-links."""
    argv = build_command("wiki", {"toc": False, "toc_links": False}, {})
    assert "--no-toc" in argv and "--no-toc-links" in argv
    argv2 = build_command("wiki", {"toc": True, "toc_links": True}, {})
    assert "--no-toc" not in argv2 and "--no-toc-links" not in argv2


def test_llm_profile_from_env(tmp_path, monkeypatch):
    """единый сервер из .env → host/model/api_key подставляются."""
    env = tmp_path / ".env"
    env.write_text(
        "HOST=http://192.168.1.8:9989\n"
        "API_KEY=secret-key\n"
        "MODEL=local-model\n"
        "NER_MODEL=ner-model\n",
        encoding="utf-8")
    import core.common as common
    monkeypatch.setattr(common, "find_env_file",
                        lambda explicit=None, start_dir=None: str(env))
    form = {"file": "compiled_book.txt"}
    ctx: dict = {"project_dir": str(tmp_path)}
    argv = build_command("ner", form, ctx)
    joined = " ".join(argv)
    assert "--host" in joined and "http://192.168.1.8:9989" in joined
    # P1 (AUDIT #2): ключ НЕ в argv — он уходит в ctx["_llm_api_key"]
    assert "--api_key" not in joined and "secret-key" not in joined
    assert ctx.get("_llm_api_key") == "secret-key"
    # стадийная модель приоритетнее общей
    assert "--model" in joined and "ner-model" in joined
    # модель стадии NER приоритетнее общей
    assert "--model" in joined and "ner-model" in joined


def test_llm_stage_keys_from_env(tmp_path, monkeypatch):
    """Стадийные NER_HOST/NER_API_KEY приоритетнее общих HOST/API_KEY."""
    env = tmp_path / ".env"
    env.write_text(
        "HOST=http://общий:9989\n"
        "API_KEY=общий-ключ\n"
        "MODEL=общая-модель\n"
        "NER_HOST=http://нер:9989\n"
        "NER_API_KEY=нер-ключ\n"
        "NER_MODEL=нер-модель\n",
        encoding="utf-8")
    import core.common as common
    monkeypatch.setattr(common, "find_env_file",
                        lambda explicit=None, start_dir=None: str(env))
    ctx: dict = {"project_dir": str(tmp_path)}
    argv = build_command("ner", {"file": "book.txt"}, ctx)
    joined = " ".join(argv)
    assert "http://нер:9989" in joined and "http://общий:9989" not in joined
    assert "нер-модель" in joined
    assert ctx.get("_llm_api_key") == "нер-ключ"

    # другой стадии (wiki) стадийные ключи не мешают — общие
    ctx2: dict = {"project_dir": str(tmp_path)}
    argv2 = build_command("wiki", {"file": "book.txt"}, ctx2)
    joined2 = " ".join(argv2)
    assert "http://общий:9989" in joined2
    assert ctx2.get("_llm_api_key") == "общий-ключ"


def test_llm_cli_overrides_env(tmp_path, monkeypatch):
    """Явные host/model/api_key в форме приоритетнее .env ."""
    env = tmp_path / ".env"
    env.write_text("HOST=http://old:9989\nAPI_KEY=old\n"
                    "MODEL=old-model\n", encoding="utf-8")
    import core.common as common
    monkeypatch.setattr(common, "find_env_file",
                        lambda explicit=None, start_dir=None: str(env))
    form = {"host": "http://new:9989",
            "model": "new-model", "api_key": "new-key"}
    ctx: dict = {"project_dir": str(tmp_path)}
    argv = build_command("ner", form, ctx)
    joined = " ".join(argv)
    assert "http://new:9989" in joined and "http://old:9989" not in joined
    assert "new-model" in joined and "old-model" not in joined
    # ключ — в ctx, а не в argv
    assert "--api_key" not in joined and "new-key" not in joined
    assert ctx.get("_llm_api_key") == "new-key"


def test_llm_no_profile_no_env(tmp_path, monkeypatch):
    """Без профиля и .env — LLM-флаги не добавляются (скрипт сам найдёт)."""
    import core.common as common
    monkeypatch.setattr(common, "find_env_file", lambda **kw: None)
    argv = build_command("ner_check", {"input": "ner.json"}, {})
    assert "--host" not in argv and "--api_key" not in argv
    assert "--model" not in argv


def test_api_key_not_in_payload(tmp_path, fake_script):
    """argv (с api_key) не попадает в payload/персистентность."""
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("translate_check_llm", "Проверка", "ACTIVE/x",
                   [str(fake_script / "ok.py"), "--api_key", "SECRET"],
                   tmp_path)
    _wait_status(jm, job.id, "done")
    payload = job.payload()
    assert "SECRET" not in json.dumps(payload)
    assert "argv" not in payload
    # jobs.json тоже без ключа
    data = (tmp_path / "jobs.json").read_text(encoding="utf-8")
    assert "SECRET" not in data


def test_llm_api_key_via_env(tmp_path, fake_script):
    """P1 (AUDIT #2): LLM_API_KEY идёт через env, а не argv."""
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("translate_check_llm", "Проверка", "ACTIVE/x",
                   [str(fake_script / "ok.py")],
                   tmp_path,
                   env={"LLM_API_KEY": "env-secret"})
    _wait_status(jm, job.id, "done")
    payload = job.payload()
    assert "env-secret" not in json.dumps(payload)
    data = (tmp_path / "jobs.json").read_text(encoding="utf-8")
    assert "env-secret" not in data


# ════════════════════════════════════════════════════════════════════
# API через HTTP (jobs/stages)


@pytest.fixture()
def jobs_srv(tmp_path, fake_script):
    """Сервер с JobManager, привязанным к tmp_path/web."""
    import threading

    from web import api as web_api
    from web.auth import Auth
    from web.server import make_server

    web_dir = tmp_path / "web"
    web_dir.mkdir()
    jm = JobManager(web_dir, python="python3")
    auth_obj = Auth("tok", no_auth=True)
    srv = make_server("127.0.0.1", 0, auth_obj,
                      repo_root=REPO, projects_root=tmp_path / "projects")
    srv.job_manager = jm
    web_api.register(srv.router, "127.0.0.1")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    import http.client
    def _req(method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1],
                                          timeout=10)
        headers = {"X-Requested-With": "fetch"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, data, headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        payload = {}
        try:
            decoded = json.loads(raw.decode("utf-8"))
            if isinstance(decoded, dict):
                payload = decoded
        except (ValueError, UnicodeDecodeError):
            pass
        return res, payload

    yield srv.server_address[1], _req, jm
    srv.server_close()


def _make_project(port, req, name="test_book"):
    res, payload = req("POST", "/api/projects",
                       {"section": "ACTIVE", "name": name})
    assert res.status == 200, payload
    return name


def test_job_start_windows_flags(monkeypatch, tmp_path):
    """B6 (AUDIT): на Windows — CREATE_NEW_PROCESS_GROUP вместо
    start_new_session (иначе ValueError); сигналы — terminate/kill."""
    import web.jobs as J
    monkeypatch.setattr(J.os, "name", "nt")
    kw = J._popen_kwargs()
    # 0x200 = CREATE_NEW_PROCESS_GROUP (константы нет на Linux)
    assert kw.get("creationflags") == 0x200
    assert "start_new_session" not in kw
    monkeypatch.setattr(J.os, "name", "posix")
    assert J._popen_kwargs() == {"start_new_session": True}

    # JobManager создаём ДО патча nt — pathlib резолвится по os.name
    jm = JobManager(tmp_path / "web")

    # сигнал на Windows: kill() для SIGKILL, terminate() для SIGTERM (нет killpg)
    monkeypatch.setattr(J.os, "name", "nt")

    class FakeProc:
        def __init__(self):
            self.calls = []

        def terminate(self):
            self.calls.append("terminate")

        def kill(self):
            self.calls.append("kill")

    fp = FakeProc()
    jm._signal_group(cast(J.subprocess.Popen, fp), J.signal.SIGTERM)
    assert fp.calls == ["terminate"]
    fp.calls.clear()
    jm._signal_group(cast(J.subprocess.Popen, fp), J.signal.SIGKILL)
    assert fp.calls == ["kill"]


def test_jobs_get_after_restart_has_lines(jobs_srv, fake_script):
    """GET /api/jobs/{id} после «рестарта» менеджера отдаёт непустой lines
    (хвост лога из job_logs/{id}.log)."""
    from web.api import _jobs_get
    from web.jobs import JobManager

    _port, _req, jm = jobs_srv
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(fake_script / "ok.py")], Path("."))
    _wait_status(jm, job.id, "done")
    # «рестарт»: новый менеджер на той же web-папке читает сайдкар
    jm2 = JobManager(jm.web_dir, python="python3")
    loaded = jm2.get(job.id)
    assert loaded is not None and loaded.lines
    # HTTP-хендлер с «перезагруженным» менеджером отдаёт хвост лога
    ctx = {"job_manager": jm2, "params": {"id": job.id}}
    result = _jobs_get(ctx)
    assert result["job"]["lines"], "хвост лога пуст после рестарта"
    assert result["job"]["lines"][-1] == "line-9"


def test_jobs_start_and_status(jobs_srv, fake_script):
    port, req, jm = jobs_srv
    _make_project(port, req)
    # стадия "0" (batch_replace) — но подменим спеко-скрипт нельзя;
    # поэтому проверяем через прямой JobManager-путь: start без HTTP.
    # HTTP-проверка: несуществующая стадия → 400
    res, payload = req("POST", "/api/jobs",
                       {"action": "x", "project": "ACTIVE/test_book",
                        "params": {}})
    assert res.status == 400
    # стадия без проекта → 400
    res, payload = req("POST", "/api/jobs",
                       {"action": "epub", "project": "", "params": {}})
    assert res.status == 400


def test_jobs_history_trimmed(jobs_srv):
    """R5-F + история ограничена MAX_HISTORY (20); сайдкары
    удаляются."""
    _, req, jm = jobs_srv
    for i in range(25):
        jm.start("test", f"Тест {i}", "ACTIVE/x", [], Path("."))
    jobs = jm.list()
    assert len(jobs) == 20
    # свежие остались, самые старые выброшены
    assert all(j["title"] != "Тест 0" for j in jobs)
    assert "Тест 24" in [j["title"] for j in jobs]
    # сайдкары выброшенных заданий удалены, у свежих — на месте
    logs_dir = jm.web_dir / "job_logs"
    ids = [j["id"] for j in jobs]
    for jid in ids:
        assert (logs_dir / f"{jid}.log").is_file(), jid
    # 25 заданий было, 5 самых старых ушли вместе с сайдкарами
    remaining = [p.name for p in logs_dir.glob("*.log")]
    assert len(remaining) == 20
    for jid in ids:
        assert f"{jid}.log" in remaining


def test_jobs_list_delete(jobs_srv):
    port, req, jm = jobs_srv
    job = jm.start("test", "Тест", "ACTIVE/x", [], Path(port and "."))
    res, payload = req("GET", "/api/jobs")
    assert res.status == 200
    assert any(j["id"] == job.id for j in payload["jobs"])
    res, payload = req("GET", f"/api/jobs/{job.id}")
    assert res.status == 200
    assert payload["job"]["id"] == job.id
    res, payload = req("DELETE", f"/api/jobs/{job.id}")
    assert res.status == 200
    res, payload = req("GET", f"/api/jobs/{job.id}")
    assert res.status == 404
    # сайдкар удаления не пережил
    assert not (jm.web_dir / "job_logs" / f"{job.id}.log").exists()


def test_jobs_clear_finished(jobs_srv, fake_script):
    """DELETE /api/jobs — очистка истории завершённых запусков;
    активные (running) остаются."""
    port, req, jm = jobs_srv
    done = jm.start("test", "Готов", "ACTIVE/x", [], Path("."))
    _wait_status(jm, done.id, "done", "failed", "stopped")
    running = jm.start("test", "Активный", "ACTIVE/x",
                       [str(fake_script / "hang.py")], Path("."))
    time.sleep(0.3)
    assert running.status == "running"
    res, payload = req("DELETE", "/api/jobs")
    assert res.status == 200
    assert payload["cleared"] >= 1
    jobs = jm.list()
    # завершённый ушёл, активный остался
    assert not any(j["id"] == done.id for j in jobs)
    assert any(j["id"] == running.id for j in jobs)
    # сайдкар очищенного задания удалён
    assert not (jm.web_dir / "job_logs" / f"{done.id}.log").exists()


def test_jobs_stop_api(jobs_srv, fake_script):
    port, req, jm = jobs_srv
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(fake_script / "hang.py")], Path("."))
    time.sleep(0.3)
    res, payload = req("POST", f"/api/jobs/{job.id}/stop")
    assert res.status == 200
    _wait_status(jm, job.id, "stopped", "done", "failed")
    # повторный stop несуществующего → 404
    res, payload = req("POST", "/api/jobs/nope/stop")
    assert res.status == 404


def test_jobs_limit_enforced(jobs_srv, monkeypatch):
    """H2 (AUDIT): --jobs-limit (2) — третий одновременный запуск → 429."""
    port, req, jm = jobs_srv
    # три РАЗНЫХ проекта — чтобы не спотыкаться о per-project лок (M10)
    for name in ("p1", "p2", "p3"):
        _make_project(port, req, name=name)

    def fake_start(action, title, project, argv, cwd, env=None):
        # не порождаем процесс: задача сразу «running»
        job = Job(action, title, project, argv, cwd)
        job.status = "running"
        with jm._lock:
            jm._jobs[job.id] = job
        return job

    monkeypatch.setattr(jm, "start", fake_start)
    for name in ("p1", "p2"):
        res, payload = req("POST", "/api/jobs",
                           {"action": "batch_replace",
                            "project": f"ACTIVE/{name}", "params": {}})
        assert res.status == 200, payload
    res, payload = req("POST", "/api/jobs",
                       {"action": "batch_replace",
                        "project": "ACTIVE/p3", "params": {}})
    assert res.status == 429, payload
    assert "Лимит" in payload["error"]


def test_jobs_project_lock_conflict(jobs_srv, monkeypatch):
    """M10 (AUDIT): вторая стадия на тот же проект → 409."""
    port, req, jm = jobs_srv
    _make_project(port, req)

    def fake_start(action, title, project, argv, cwd, env=None):
        job = Job(action, title, project, argv, cwd)
        job.status = "running"
        with jm._lock:
            jm._jobs[job.id] = job
        return job

    monkeypatch.setattr(jm, "start", fake_start)
    res, payload = req("POST", "/api/jobs",
                       {"action": "batch_replace",
                        "project": "ACTIVE/test_book", "params": {}})
    assert res.status == 200, payload
    res, payload = req("POST", "/api/jobs",
                       {"action": "batch_replace",
                        "project": "ACTIVE/test_book", "params": {}})
    assert res.status == 409, payload
    assert "уже обрабатывается" in payload["error"]
    # другой проект — не конфликт
    _make_project(port, req, name="other")
    res, _ = req("POST", "/api/jobs",
                 {"action": "batch_replace",
                  "project": "ACTIVE/other", "params": {}})
    assert res.status == 200


def test_stage_spec_api(jobs_srv):
    port, req, _jm = jobs_srv
    res, payload = req("GET", "/api/stages/epub/spec")
    assert res.status == 200
    assert payload["spec"]["title"] == "Разбор исходника на главы"
    assert payload["spec"]["fields"]
    res, payload = req("GET", "/api/stages/nope/spec")
    assert res.status == 404


def test_dashboard_recent_cap(tmp_path):
    """recent_jobs на дашборде — не более 20 записей."""
    from web.api import _dashboard

    jm = JobManager(tmp_path, python="python3")
    for i in range(25):
        jm.start("test", f"Тест {i}", "ACTIVE/x", [], Path("."))
    ctx = {"job_manager": jm, "projects_root": tmp_path}
    d = _dashboard(ctx)
    assert len(d.get("recent_jobs", [])) == 20
    assert all(j["title"] != "Тест 0" for j in d["recent_jobs"])
    assert "Тест 24" in [j["title"] for j in d["recent_jobs"]]


def test_dashboard_running_from_full_list(tmp_path, fake_script):
    """running_jobs собираются из полного списка jobs,
    а не из среза «последних N»."""
    from web.api import _dashboard

    jm = JobManager(tmp_path, python="python3")
    job = jm.start("test", "Висит", "ACTIVE/x",
                   [str(fake_script / "hang.py")], Path("."))
    ctx = {"job_manager": jm, "projects_root": tmp_path}
    running = []
    for _ in range(100):
        d = _dashboard(ctx)
        running = [j for j in d.get("running_jobs", [])
                   if j["id"] == job.id]
        if running:
            break
        time.sleep(0.05)
    assert running, "активный запуск должен быть в running_jobs"
    assert running[0]["status"] == "running"
    # завершённые (ok.py) — только в recent_jobs, не в running_jobs
    done = jm.start("test", "Готов", "ACTIVE/x",
                    [str(fake_script / "ok.py")], Path("."))
    _wait_status(jm, done.id, "done")
    d2 = _dashboard(ctx)
    assert not any(j["id"] == done.id for j in d2.get("running_jobs", []))
    assert any(j["id"] == done.id for j in d2.get("recent_jobs", []))
    jm.shutdown()


def test_dashboard_http_with_job_manager(jobs_srv, fake_script):
    """R15: /api/dashboard через HTTP видит активные запуски —
    job_manager привязан к серверу (srv.job_manager), а не ctx."""
    port, req, jm = jobs_srv
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(fake_script / "hang.py")], Path("."))
    assert job.status == "running"
    res, payload = req("GET", "/api/dashboard")
    assert res.status == 200
    running = [j for j in payload.get("running_jobs", [])
               if j["id"] == job.id]
    assert running, "активный запуск должен быть в running_jobs дашборда"
    assert running[0]["status"] == "running"
    # pid доступен для управления
    assert running[0]["pid"] == job.pid
    # recent_jobs тоже содержит запуск
    ids = [j["id"] for j in payload.get("recent_jobs", [])]
    assert job.id in ids
    jm.shutdown()


def test_stages_list_order(jobs_srv):
    """R6-D: /api/stages отдаёт стадии в порядке STAGE_ORDER."""
    port, req, _jm = jobs_srv
    res, payload = req("GET", "/api/stages")
    assert res.status == 200
    keys = [s["key"] for s in payload["stages"]]
    assert keys == STAGE_ORDER
    assert all(".py" not in s["title"] for s in payload["stages"])
    by_key = {s["key"]: s for s in payload["stages"]}
    assert by_key["translate_check_llm"]["title"] == "Проверка перевода (LLM)"
    assert by_key["ner_check"]["title"] == "Проверка глоссария (LLM)"


def test_stage_options_api(jobs_srv, tmp_path):
    port, req, _jm = jobs_srv
    _make_project(port, req)
    # кладём главы и source-файл
    pdir = tmp_path / "projects" / "ACTIVE" / "test_book"
    (pdir / "chapters" / "00001").mkdir(parents=True)
    (pdir / "chapters" / "00001" / "chapter.txt").write_text(
        "текст", encoding="utf-8")
    (pdir / "chapters" / "00003").mkdir()
    (pdir / "chapters" / "00003" / "chapter.txt").write_text(
        "текст", encoding="utf-8")
    src = pdir / "source"
    src.mkdir(exist_ok=True)
    (src / "book.epub").write_bytes(b"x")
    res, payload = req("GET",
                       "/api/stages/epub/options?project=ACTIVE/test_book")
    assert res.status == 200
    # B10: ids — реальные главы (пропусков в нумерации нет в списке)
    assert payload["options"]["chapters"] == {"min": 1, "max": 3,
                                               "ids": [1, 3]}
    assert "book.epub" in payload["options"]["source"]


def test_stage_options_cache_invalidates(jobs_srv, tmp_path):
    """U8: кэш опций инвалидируется по mtime — новый файл в корне
    виден вторым запросом без перезагрузки сервера."""
    from web.api import _OPTIONS_CACHE
    _OPTIONS_CACHE.clear()
    port, req, _jm = jobs_srv
    _make_project(port, req)
    pdir = tmp_path / "projects" / "ACTIVE" / "test_book"
    (pdir / "chapters" / "00001").mkdir(parents=True)
    (pdir / "chapters" / "00001" / "chapter.txt").write_text(
        "текст", encoding="utf-8")
    res, payload = req("GET",
                       "/api/stages/wiki/options?project=ACTIVE/test_book")
    assert res.status == 200
    assert payload["options"]["chapters"] == {"min": 1, "max": 1,
                                               "ids": [1]}
    assert "ner.json" not in payload["options"].get("root", [])
    # появился ner.json (корень проекта) — кэш должен инвалидироваться
    (pdir / "ner.json").write_text("{}", encoding="utf-8")
    res, payload = req("GET",
                       "/api/stages/wiki/options?project=ACTIVE/test_book")
    assert res.status == 200
    assert "ner.json" in payload["options"]["root"]


def test_pipeline_options_auto_prompt(jobs_srv, tmp_path):
    """Автоподхват общего промпт-файла: pipeline_prompt.txt с тегами →
    options.auto_prompt; без тегов/файла — нет; инвалидация по mtime."""
    from web.api import _OPTIONS_CACHE
    _OPTIONS_CACHE.clear()
    port, req, _jm = jobs_srv
    _make_project(port, req)
    pdir = tmp_path / "projects" / "ACTIVE" / "test_book"

    # кандидата нет → auto_prompt отсутствует
    res, payload = req("GET",
                       "/api/stages/pipeline/options?project=ACTIVE/test_book")
    assert res.status == 200
    assert "auto_prompt" not in payload["options"]

    # файл без тегов — не кандидат
    (pdir / "prompts" / "pipeline_prompt.txt").write_text(
        "обычный текст без тегов\n", encoding="utf-8")
    res, payload = req("GET",
                       "/api/stages/pipeline/options?project=ACTIVE/test_book")
    assert res.status == 200
    assert "auto_prompt" not in payload["options"]

    # с тегами translate/redact/polish — автоподхват
    (pdir / "prompts" / "pipeline_prompt.txt").write_text(
        "<translate>перевод</translate>\n<redact>редактура</redact>\n"
        "<polish>полировка</polish>\n", encoding="utf-8")
    res, payload = req("GET",
                       "/api/stages/pipeline/options?project=ACTIVE/test_book")
    assert res.status == 200
    assert payload["options"]["auto_prompt"] == "prompts/pipeline_prompt.txt"

    # удалили файл → авто-кандидат исчез (кэш инвалидирован по mtime)
    (pdir / "prompts" / "pipeline_prompt.txt").unlink()
    res, payload = req("GET",
                       "/api/stages/pipeline/options?project=ACTIVE/test_book")
    assert res.status == 200
    assert "auto_prompt" not in payload["options"]
    _OPTIONS_CACHE.clear()


def test_stream_sse(jobs_srv, fake_script):
    """SSE: буфер + живые строки + статус; конец — EOF без мусора."""
    import http.client
    port, req, jm = jobs_srv
    job = jm.start("test", "Тест", "ACTIVE/x",
                   [str(fake_script / "ok.py")], Path("."))
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    conn.request("GET", f"/api/jobs/{job.id}/stream",
                 headers={"X-Requested-With": "fetch"})
    res = conn.getresponse()
    assert res.status == 200
    assert "text/event-stream" in (res.getheader("Content-Type") or "")
    # сервер закрывает соединение после статуса — читаем до EOF
    data = b""
    while True:
        chunk = res.read(1)
        if not chunk:
            break
        data += chunk
    conn.close()
    body = data.decode("utf-8")
    assert "line-0" in body
    assert '"type": "line"' in body
    assert '"status": "done"' in body
    # после стрима не должно быть второго HTTP-ответа
    assert b"HTTP/" not in data

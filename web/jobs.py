#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jobs.py — менеджер запусков стадий (M4).

- запуск скрипта subprocess'ом с cwd=папка проекта;
- кольцевой буфер строк (RING_SIZE=5000);
- SSE-подписчики: очередь событий на job (line/status);
- stop: сигнал группе процессов (start_new_session + killpg),
  SIGTERM → 5 с → SIGKILL — потомки (translate_book.py и др.) не осиротеют;
- персистентность: метаданные в jobs.json, хвост лога в job_logs/{id}.log
  (+ события в job_logs/{id}.events.json); argv не сериализуется (секреты);
- прогресс: строки @@PROGRESS@@ от скриптов (web-режим WEB_PROGRESS=1)
  парсятся как JSON → job.progress (последнее событие) → payload/SSE;
- reconcile: при загрузке сервера «running»-запуски проверяются по pid —
  живой процесс остаётся управляемым (stop по pid), мёртвый помечается failed;
- shutdown: остановка всех активных запусков при завершении сервера.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger("web")

# Кольцевой буфер строк лога на задачу
RING_SIZE = 5000
# Сайдкары хвоста лога/событий (web/job_logs/{id}.log + {id}.events.json)
JOB_LOGS_DIRNAME = "job_logs"
STOP_GRACE = 5.0  # секунд между terminate и kill
SSE_PING = 15.0  # секунд между ping в стриме

# Префиксы структурированных событий в stdout (JSON после него).
# @@CHAPTER@@ — события глав конвейера (web/pipeline.py);
# @@PROGRESS@@ — события прогресса LLM-стадий (core/common.py emit_progress).
CHAPTER_PREFIX = "@@CHAPTER@@"
from core.common import PROGRESS_PREFIX  # noqa: E402


def _popen_kwargs() -> dict:
    """B6 (AUDIT): параметры Popen для группы процессов.
    POSIX — start_new_session (killpg убьёт потомков); Windows —
    CREATE_NEW_PROCESS_GROUP (start_new_session там не поддержан)."""
    if os.name == "nt":
        # константа есть только на Windows; 0x200 — её значение
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}
    return {"start_new_session": True}


class Job:
    """Один запуск: метаданные + буфер + подписчики + процесс."""

    def __init__(self, action: str, title: str, project: str,
                 argv: list[str], cwd: Path, pid: int | None = None) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.action = action
        self.title = title
        self.project = project
        self.argv = list(argv)
        self.cwd = str(cwd)
        self.status = "running"  # running | done | failed | stopped
        self.exit_code: int | None = None
        self.created = time.time()
        self.finished: float | None = None
        self.pid: int | None = pid  # pid процесса (для reconcile/stop по pid)
        self.lines: list[str] = []  # кольцевой буфер
        self.events: list[dict] = []  # события глав (конвейер)
        self.progress: dict | None = None  # последнее событие прогресса
        self.proc: subprocess.Popen | None = None
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    # ── буфер ────────────────────────────────────────────────
    def append(self, line: str) -> None:
        with self._lock:
            self.lines.append(line)
            if len(self.lines) > RING_SIZE:
                del self.lines[: len(self.lines) - RING_SIZE]

    def tail(self, n: int = 200) -> list[str]:
        with self._lock:
            return list(self.lines[-n:])

    def payload(self) -> dict:
        """Метаданные + хвост буфера + события (для API).

        argv НЕ включается: в argv могут быть секреты (--api_key)."""
        return {
            "id": self.id,
            "action": self.action,
            "title": self.title,
            "project": self.project,
            "status": self.status,
            "exit_code": self.exit_code,
            "created": self.created,
            "finished": self.finished,
            "pid": self.pid,
            "cwd": self.cwd,
            "lines": self.tail(500),
            "events": list(self.events),
            "progress": self.progress,
        }

    # ── подписчики (SSE) ─────────────────────────────────────
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def notify(self, event: tuple) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                log.debug("Медленный подписчик — событие потеряно: %s", event[0])


class JobManager:
    """История запусков: словарь id → Job + персистентность."""

    MAX_HISTORY = 20  # R5-F + раунд 20: дашборд «Последние запуски» — до 20

    def __init__(self, web_dir: Path, python: str | None = None,
                 repo_root: Path | None = None) -> None:
        self.web_dir = Path(web_dir)
        self.python = python or sys.executable
        self.repo_root = Path(repo_root) if repo_root else \
            Path(__file__).resolve().parents[1]
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._load()
        self._cleanup_orphans()
        self._trim_history()

    # ── персистентность ─────────────────────────────────────
    def _store_path(self) -> Path:
        return self.web_dir / "jobs.json"

    def _logs_dir(self) -> Path:
        """Каталог сайдкаров: web/job_logs/."""
        return self.web_dir / JOB_LOGS_DIRNAME

    def _log_path(self, job_id: str) -> Path:
        return self._logs_dir() / f"{job_id}.log"

    def _events_path(self, job_id: str) -> Path:
        return self._logs_dir() / f"{job_id}.events.json"

    def _write_sidecar(self, job: Job) -> None:
        """Хвост буфера и события — в сайдкары (по строке; RING_SIZE)."""
        d = self._logs_dir()
        d.mkdir(parents=True, exist_ok=True)
        self._log_path(job.id).write_text(
            "\n".join(job.tail(RING_SIZE)), encoding="utf-8")
        if job.events:
            self._events_path(job.id).write_text(
                json.dumps(job.events, ensure_ascii=False),
                encoding="utf-8")

    def _read_sidecar(self, job: Job) -> None:
        """Восстановить хвост лога/событий после рестарта сервера."""
        p = self._log_path(job.id)
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            job.lines = text.splitlines()[-RING_SIZE:]
        ep = self._events_path(job.id)
        if ep.is_file():
            try:
                ev = json.loads(ep.read_text(encoding="utf-8"))
                if isinstance(ev, list):
                    job.events = ev
            except ValueError as exc:
                log.debug("События %s не читаются: %s", ep, exc)

    def _drop_sidecar(self, job_id: str) -> None:
        """Удалить сайдкары задания (trim истории / remove)."""
        for p in (self._log_path(job_id), self._events_path(job_id)):
            try:
                p.unlink()
            except OSError as exc:
                log.debug("Сайдкар %s не удаляется: %s", p, exc)

    def _serialize(self, job: Job) -> dict:
        return {
            "id": job.id,
            "action": job.action,
            "title": job.title,
            "project": job.project,
            "status": job.status,
            "exit_code": job.exit_code,
            "created": job.created,
            "finished": job.finished,
            "pid": job.pid,
            "cwd": job.cwd,
            "progress": job.progress,
        }

    def _trim_locked(self) -> None:
        """R5-F (лок уже взят): не более MAX_HISTORY запусков."""
        if len(self._jobs) <= self.MAX_HISTORY:
            return
        ordered = sorted(self._jobs.values(),
                         key=lambda j: j.created, reverse=True)
        for stale in ordered[self.MAX_HISTORY:]:
            self._jobs.pop(stale.id, None)
            self._drop_sidecar(stale.id)

    def _trim_history(self) -> None:
        """R5-F: оставить не более MAX_HISTORY последних запусков."""
        with self._lock:
            self._trim_locked()

    def _persist(self) -> None:
        """Метаданные + сайдкары; весь цикл — под локом, чтобы reader-
        поток завершённого задания не писал сайдкар задания, которое
        параллельный trim уже выбросил из истории."""
        with self._lock:
            self._trim_locked()
            jobs = list(self._jobs.values())
            try:
                data = [self._serialize(j) for j in jobs]
                self._store_path().write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
            except OSError as exc:
                log.debug("jobs.json не пишется: %s", exc)
            # сайдкары хвоста лога — отдельно от метаданных; падение не
            # ломает jobs.json (метаданные уже записаны)
            for job in jobs:
                try:
                    self._write_sidecar(job)
                except OSError as exc:
                    log.debug("job_logs/{id}.log не пишется: %s", exc)

    def _cleanup_orphans(self) -> None:
        """Раунд 21 (п.10): сайдкары без записи в jobs.json — сироты от
        старых версий/падений; удаляются, чтобы job_logs/ не рос вечно."""
        d = self._logs_dir()
        if not d.is_dir():
            return
        known = {f"{job.id}.log" for job in self._jobs.values()}
        known |= {f"{job.id}.events.json" for job in self._jobs.values()}
        for p in d.iterdir():
            if not p.is_file():
                continue
            if p.name not in known:
                try:
                    p.unlink()
                except OSError as exc:
                    log.debug("Сирота %s не удаляется: %s", p, exc)

    def _load(self) -> None:
        p = self._store_path()
        if not p.is_file():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.debug("jobs.json не читается: %s", exc)
            return
        for item in data if isinstance(data, list) else []:
            job = Job(item.get("action", "?"), item.get("title", "?"),
                      item.get("project", ""), [], Path("."))
            job.id = item.get("id", job.id)
            job.status = item.get("status", "done")
            job.exit_code = item.get("exit_code")
            job.created = item.get("created", time.time())
            job.finished = item.get("finished")
            job.pid = item.get("pid")
            job.argv = list(item.get("argv") or [])
            job.cwd = str(item.get("cwd") or ".")
            job.progress = item.get("progress")
            self._jobs[job.id] = job
            self._read_sidecar(job)
        self._reconcile()

    # ── reconcile: «running» после рестарта ─────────────────────
    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        """Жив ли процесс по pid (сигнал 0; Windows — нет доступа).

        Зомби (state Z) считается мёртвым: kill(pid, 0) на зомби не
        бросает ошибку, но процесс уже завершился (его заберёт init)."""
        if not pid:
            return False
        # зомби: /proc/<pid>/stat, поле состояния (3-е) == 'Z'
        try:
            stat = open(f"/proc/{pid}/stat", "rb").read().decode(
                "utf-8", errors="replace")
            fields = stat.split()
            if len(fields) > 2 and fields[2] == "Z":
                return False
        except OSError:
            pass
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # процесс есть, но чужой — считаем живым
        except OSError:
            return False
        return True

    def _reconcile(self) -> None:
        """Проверяет сохранённые «running»-запуски после рестарта сервера:
        живой pid → запуск остаётся running (управление по pid: stop/shutdown),
        мёртвый → помечается failed (процесс не пережил рестарт)."""
        changed = False
        for job in list(self._jobs.values()):
            if job.status != "running":
                continue
            if self._pid_alive(job.pid):
                log.info("Reconcile: запуск %s (pid=%s) продолжает работать "
                         "после рестарта сервера", job.id, job.pid)
            else:
                job.status = "failed"
                job.exit_code = job.exit_code or 1
                job.finished = time.time()
                changed = True
                log.info("Reconcile: запуск %s (pid=%s) не пережил рестарт "
                         "— помечен failed", job.id, job.pid)
        if changed:
            self._persist()

    # ── остановка группы процессов ──────────────────────
    def _signal_group(self, proc: subprocess.Popen, sig: int) -> None:
        """Сигнал группе процессов (start_new_session) с fallback на процесс.
        B6 (AUDIT): на Windows групп процессов/killpg нет — per-process
        (terminate/kill — единственный доступный механизм)."""
        if os.name == "nt":
            # SIGTERM/SIGKILL на Windows не передаются send_signal —
            # TerminateProcess через terminate()/kill()
            try:
                if sig == signal.SIGKILL:
                    proc.kill()
                else:
                    proc.terminate()
            except OSError as exc:
                log.debug("Windows-сигнал не сработал (%s), pid=%s",
                          exc, proc.pid)
            return
        try:
            os.killpg(proc.pid, sig)
        except (AttributeError, OSError, PermissionError) as exc:
            log.debug("killpg не сработал (%s), сигнал процессу pid=%s",
                      exc, proc.pid)
            try:
                proc.send_signal(sig)
            except OSError:
                pass

    # ── CRUD ─────────────────────────────────────────────────
    def running_on(self, project: str) -> Job | None:
        """M10 (AUDIT): активная задача на этот проект — гонка записи."""
        with self._lock:
            for j in self._jobs.values():
                if j.status == "running" and j.project == project:
                    return j
        return None

    def start(self, action: str, title: str, project: str,
              argv: list[str], cwd: Path,
              env: dict | None = None) -> Job:
        """Запуск задачи; env — доп. переменные окружения (напр.
        LLM_API_KEY), сливаются поверх os.environ."""
        job = Job(action, title, project, argv, cwd)
        try:
            proc_env = dict(os.environ)
            if env:
                proc_env.update(env)
            # раунд 19: каждый web-запуск — в режиме структурированного
            # прогресса (emit_progress в скриптах; tqdm отключается)
            proc_env["WEB_PROGRESS"] = "1"
            proc = subprocess.Popen(
                [self.python] + argv,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # B5 (AUDIT): text=True + bufsize=1 — построчный буфер
                # без RuntimeWarning (бинарный режим bufsize=1 не поддержан)
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=proc_env,
                # B6 (AUDIT): группа процессов — платформозависимо
                **_popen_kwargs(),
            )
        except OSError as exc:
            # не запустился — сразу failed
            job.status = "failed"
            job.exit_code = 1
            job.finished = time.time()
            job.append(f"Ошибка запуска: {exc}")
            with self._lock:
                self._jobs[job.id] = job
            self._persist()
            return job
        job.proc = proc
        job.pid = proc.pid
        with self._lock:
            self._jobs[job.id] = job
        threading.Thread(target=self._reader, args=(job,), daemon=True).start()
        self._persist()
        return job

    def _reader(self, job: Job) -> None:
        """Читает stdout до EOF, потом фиксирует статус.

        Строки с префиксом @@CHAPTER@@ — структурированные события
        (JSON) от конвейера: не попадают в буфер, кладутся в events
        и уходят подписчикам как ("event", dict).
        Строки @@PROGRESS@@ — события прогресса LLM-стадий: последнее
        сохраняется в job.progress, уходит подписчикам как ("progress", dict)."""
        proc = job.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for raw in iter(proc.stdout.readline, ""):
                line = raw.rstrip("\n")
                if line.startswith(PROGRESS_PREFIX):
                    try:
                        ev = json.loads(line[len(PROGRESS_PREFIX):].strip())
                    except ValueError as exc:
                        log.debug("Кривое событие прогресса: %s", exc)
                        job.append(line)
                        job.notify(("line", line))
                        continue
                    if isinstance(ev, dict) and ev.get("type") == "progress":
                        job.progress = ev
                        job.notify(("progress", ev))
                        continue
                    job.append(line)
                    job.notify(("line", line))
                    continue
                if line.startswith(CHAPTER_PREFIX):
                    try:
                        ev = json.loads(line[len(CHAPTER_PREFIX):].strip())
                    except ValueError as exc:
                        log.debug("Кривое событие конвейера: %s", exc)
                        job.append(line)
                        job.notify(("line", line))
                        continue
                    job.events.append(ev)
                    job.notify(("event", ev))
                    continue
                job.append(line)
                job.notify(("line", line))
        finally:
            code = proc.wait()
            job.exit_code = code
            job.finished = time.time()
            if code == 0:
                job.status = "done"
            elif job.status == "running":
                job.status = "failed"
            job.notify(("status", job.status))
            self._persist()

    def stop(self, job_id: str) -> Job | None:
        """Сигнал группе (SIGTERM) → 5 с → SIGKILL. Возвращает job или None.

        Работает и для «сирот» после рестарта сервера: proc отсутствует,
        но pid жив — сигнал уходит группе по pid (killpg)."""
        job = self.get(job_id)
        if job is None or job.status != "running":
            return job
        proc = job.proc
        if proc is None and not self._pid_alive(job.pid):
            # живого процесса нет — просто фиксируем остановку
            job.status = "stopped"
            job.exit_code = job.exit_code or 1
            job.finished = time.time()
            self._persist()
            job.notify(("status", job.status))
            return job
        if proc is not None:
            self._signal_group(proc, signal.SIGTERM)
        else:
            # сирота: сигнал группе по pid (killpg как в _signal_group)
            self._signal_group_pid(job.pid, signal.SIGTERM)
        job.status = "stopped"  # reader перезапишет только если не done

        def _kill_later() -> None:
            if proc is not None:
                try:
                    proc.wait(timeout=STOP_GRACE)
                except subprocess.TimeoutExpired:
                    self._signal_group(proc, signal.SIGKILL)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        log.warning("Процесс pid=%s не завершился после SIGKILL",
                                    proc.pid)
            else:
                # сирота: ждём смерти по pid, потом SIGKILL
                end = time.time() + STOP_GRACE
                while time.time() < end and self._pid_alive(job.pid):
                    time.sleep(0.2)
                if self._pid_alive(job.pid):
                    self._signal_group_pid(job.pid, signal.SIGKILL)
            job.finished = time.time()
            job.notify(("status", job.status))
            self._persist()
        threading.Thread(target=_kill_later, daemon=True).start()
        return job

    def _signal_group_pid(self, pid: int | None, sig: int) -> None:
        """Сигнал группе процессов по pid (для сирот без Popen)."""
        if not pid:
            return
        if os.name == "nt":
            # Windows: per-process (killpg нет) — только процесс
            try:
                if sig == signal.SIGKILL:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True)
                else:
                    subprocess.run(["taskkill", "/PID", str(pid)],
                                   capture_output=True)
            except OSError as exc:
                log.debug("Windows-сигнал не сработал (%s), pid=%s", exc, pid)
            return
        try:
            os.killpg(pid, sig)
        except (AttributeError, OSError, PermissionError) as exc:
            log.debug("killpg по pid не сработал (%s), сигнал pid=%s", exc, pid)
            try:
                os.kill(pid, sig)
            except OSError:
                pass

    def shutdown(self) -> None:
        """Остановка всех активных запусков (вызывается при завершении
        сервера). Живые сироты останавливаются по pid; статусы/финиш
        фиксируются."""
        with self._lock:
            jobs = [j for j in self._jobs.values()
                    if j.status == "running"]
        for job in jobs:
            log.info("Shutdown: останавливаю запуск %s (%s)",
                     job.id, job.title)
            try:
                self.stop(job.id)
            except Exception as exc:  # noqa: BLE001 — сервер умирает
                log.warning("Shutdown: не удалось остановить %s: %s",
                            job.id, exc)
        # ждём, пока фоновые _kill_later зафиксируют finished у всех
        end = time.time() + STOP_GRACE + 1.0
        while time.time() < end:
            with self._lock:
                pending = [j for j in self._jobs.values()
                           if j.status in ("running", "stopped")
                           and j.finished is None]
            if not pending:
                break
            time.sleep(0.2)

    def remove(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is None:
            return False
        if job.status == "running" and job.proc is not None:
            self._signal_group(job.proc, signal.SIGTERM)
        self._drop_sidecar(job_id)
        self._persist()
        return True

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[dict]:
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.created, reverse=True)
        return [self._serialize(j) for j in jobs]

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_stage.py — запуски и стадии: JobManager, запуск стадии, список
и поток событий, спецификация и опции стадий, предпросмотр запроса, эмуляции
epub_to_chapters и batch_replace.
"""
from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from web.jobs import JobManager
from web.server import ApiError, Router
from core import settings as core_settings
from web.stages import (
    STAGE_SPECS, build_command, ordered_stages, script_path, spec_for,
)
from web.api_common import (
    log,
    _OPTIONS_CACHE,
    EPUB_PREVIEW_FILE,
    PREVIEW_REQUEST_FILE,
    _PREVIEW_STAGES,
)
from web.api_common import (
    _import_batch_replace,
    _import_common,
    _import_projects,
    _project_ctx,
    _projects_root,
    _repo_root,
    _sys_env_path,
)


# ════════════════════════════════════════════════════════════════════
# Запуски и стадии
# ════════════════════════════════════════════════════════════════════
def _job_manager(ctx: dict) -> JobManager:
    """Общий JobManager (singleton на сервер, ленивое создание).

    Приоритет: явный ctx['job_manager'] (тесты/встраивание) →
    handler.server.job_manager (реальный сервер) → глобальный
    _main.JOB_MANAGER (fallback, напр. CLI-импорты)."""
    jm = ctx.get("job_manager")
    if jm is not None:
        return jm
    handler = ctx.get("handler")
    srv = handler.server if handler is not None else None
    jm = getattr(srv, "job_manager", None) if srv is not None else None
    if jm is None:
        from web import main as _main
        jm = _main.JOB_MANAGER  # pragma: no cover — реальный сервер
        if srv is not None:
            srv.job_manager = jm
    return jm


def _jobs_start(ctx: dict) -> dict:
    """Запуск стадии (POST /api/jobs {action, project, params})."""
    body = ctx["body"]
    action = (body.get("action") or "").strip()
    project = (body.get("project") or "").strip()
    params = body.get("params") or {}
    # профиль LLM — поле формы стадии: у каждой стадии свой выбор
    profile = str(params.get(core_settings.PROFILE_FIELD) or "").strip()
    if not action:
        raise ApiError(400, "Поле action обязательно")
    if "/" not in project:
        raise ApiError(400, "Параметр project=sec/name обязателен")
    prj = _import_projects(ctx)
    section, _, name = project.partition("/")
    pdir = prj.project_dir(_projects_root(ctx), section, name)
    if not pdir.is_dir():
        raise ApiError(404, f"Проект не найден: {section}/{name}")
    spec = spec_for(action)
    if spec is None:
        raise ApiError(400, f"Стадия {action} пока не поддерживается в web")
    title = spec["title"]
    repo = _repo_root(ctx)
    script = script_path(action, repo)
    if script is None or not script.is_file():
        raise ApiError(500, f"Скрипт не найден: {spec['script']}")
    # валидация number-полей с min/max из spec: недопустимое значение
    # → 400 ДО запуска (скрипт бы упал с кодом 2 и «failed» без причины)
    for f in spec.get("fields") or []:
        if f.get("type") != "number" or (f.get("min") is None
                                          and f.get("max") is None):
            continue
        raw = params.get(f["name"])
        if raw is None or raw == "":
            continue
        try:
            n = float(str(raw))
        except (TypeError, ValueError):
            continue
        label = (f.get("label") or f["name"]).split("(")[0].strip()
        try:
            fmin = None if f.get("min") is None else float(f["min"])
            fmax = None if f.get("max") is None else float(f["max"])
        except (TypeError, ValueError):
            continue
        if fmin is not None and n < fmin:
            raise ApiError(400, f"«{label}»: минимум {f['min']}")
        if fmax is not None and n > fmax:
            raise ApiError(400, f"«{label}»: максимум {f['max']}")
    # LLM-конфиг (сервер/модель/ключ/потоки) в argv подставляет реестр
    # (web.stages.build_command): в форме запусков этих полей больше нет,
    # а свои .env у книги больше нет — локальные значения живут в браузере
    argv = build_command(action, params, ctx)
    argv[0] = str(script)  # абсолютный путь к скрипту
    jm = _job_manager(ctx)
    # Лимит параллельных задач --jobs-limit (мёртвая опция → живая)
    handler = ctx.get("handler")
    srv = handler.server if handler is not None else None
    limit = getattr(srv, "jobs_limit", 2) if srv is not None else 2
    running = sum(1 for j in jm.list() if j.get("status") == "running")
    if running >= limit:
        raise ApiError(
            429,
            f"Лимит параллельных задач: {limit} (активно: {running}). "
            f"Дождитесь завершения или остановите запуск.",
        )
    # лок на проект: две стадии на один проект
    # параллельно перезаписали бы одни и те же артефакты
    busy = jm.running_on(project)
    if busy is not None:
        raise ApiError(
            409,
            f"Проект {project} уже обрабатывается задачей «{busy.title}» "
            f"({busy.id}) — дождитесь завершения или остановите её.",
        )
    env = {}
    if profile and profile != core_settings.PROFILE_DEFAULT:
        # скрипт читает реестр сам: профиль этой стадии доезжает до него
        # переменной, иначе стадия жила бы значениями General
        env[core_settings.PROFILE_ENV] = profile
    api_key = ctx.pop("_llm_api_key", None)
    if api_key:
        # ключ — только в окружении процесса
        env["LLM_API_KEY"] = str(api_key)
    job = jm.start(action, title, project, argv, pdir, env=env)
    return {"ok": True, "job": _job_payload(job)}


def _job_payload(job) -> dict:
    """Публичное представление запуска (метаданные + буфер)."""
    return job.payload()


# ════════════════════════════════════════════════════════════════════
# Настройки: одно место истины — реестр core/settings.py + общий .env
# ════════════════════════════════════════════════════════════════════
# Собственный .env книги убран из модели: поля запусков, изменённые для
# одной книги, — рабочее состояние браузера (localStorage), а не второй
# конфигурационный файл, который сервер обязан разбирать.


def _jobs_list(ctx: dict) -> dict:
    """История запусков (GET /api/jobs)."""
    jm = _job_manager(ctx)
    return {"ok": True, "jobs": jm.list()}


def _jobs_active(ctx: dict) -> dict:
    """Только активные запуски (GET /api/jobs/active) — лёгкий опрос
    для индикатора в шапке SPA (в отличие от /api/jobs — без истории)."""
    jm = _job_manager(ctx)
    active = [j for j in jm.list() if j.get("status") == "running"]
    return {"ok": True, "jobs": active}


def _jobs_get(ctx: dict) -> dict:
    """Детали запуска + хвост буфера (GET /api/jobs/{id})."""
    jm = _job_manager(ctx)
    job = jm.get(ctx["params"]["id"])
    if job is None:
        raise ApiError(404, "Запуск не найден")
    return {"ok": True, "job": _job_payload(job)}


def _jobs_stop(ctx: dict) -> dict:
    """Остановка (POST /api/jobs/{id}/stop): terminate → 5 c → kill."""
    jm = _job_manager(ctx)
    job = jm.stop(ctx["params"]["id"])
    if job is None:
        raise ApiError(404, "Запуск не найден")
    return {"ok": True, "status": job.status}


def _jobs_delete(ctx: dict) -> dict:
    """Удалить из истории (DELETE /api/jobs/{id})."""
    jm = _job_manager(ctx)
    if not jm.remove(ctx["params"]["id"]):
        raise ApiError(404, "Запуск не найден")
    return {"ok": True}


def _jobs_clear(ctx: dict) -> dict:
    """Очистить историю завершённых запусков (DELETE /api/jobs).
    Активные (running) не трогаются — остаются на дашборде."""
    jm = _job_manager(ctx)
    return {"ok": True, "cleared": jm.clear_finished()}


def _jobs_stream(ctx: dict) -> dict:
    """SSE-стрим лога (GET /api/jobs/{id}/stream)."""
    import json as _json
    jm = _job_manager(ctx)
    job = jm.get(ctx["params"]["id"])
    if job is None:
        raise ApiError(404, "Запуск не найден")
    handler = ctx["handler"]
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Accel-Buffering", "no")
    handler.send_header("Connection", "close")
    handler.end_headers()
    # SSE-стрим сам пишет ответ; конец потока — EOF для fetch:
    # второй JSON-ответ сервер дописывать не должен
    ctx["streamed"] = True
    handler.close_connection = True
    q = job.subscribe()
    try:
        # сразу — весь текущий буфер + события (для живой таблицы)
        for line in job.tail(5000):
            ev = _json.dumps({"type": "line", "text": line}, ensure_ascii=False)
            handler.wfile.write(f"data: {ev}\n\n".encode("utf-8"))
        for ev_item in list(job.events):
            ev = _json.dumps({"type": "event", "event": ev_item},
                             ensure_ascii=False)
            handler.wfile.write(f"data: {ev}\n\n".encode("utf-8"))
        # текущий прогресс — живое прикрепление сразу видит бар
        if job.progress:
            ev = _json.dumps({"type": "progress", "event": job.progress},
                             ensure_ascii=False)
            handler.wfile.write(f"data: {ev}\n\n".encode("utf-8"))
        # уже завершился до подписки — статус сразу и закрываем
        if job.status != "running":
            ev = _json.dumps({"type": "status", "status": job.status},
                             ensure_ascii=False)
            handler.wfile.write(f"data: {ev}\n\n".encode("utf-8"))
            handler.wfile.flush()
            return {}
        handler.wfile.flush()
        while True:
            try:
                ev_type, payload = q.get(timeout=15.0)
            except Exception:
                handler.wfile.write(b": ping\n\n")
                handler.wfile.flush()
                continue
            if ev_type == "line":
                ev = _json.dumps({"type": "line", "text": payload}, ensure_ascii=False)
            elif ev_type == "event":
                ev = _json.dumps({"type": "event", "event": payload},
                                 ensure_ascii=False)
            elif ev_type == "progress":
                ev = _json.dumps({"type": "progress", "event": payload},
                                 ensure_ascii=False)
            else:
                ev = _json.dumps({"type": "status", "status": payload}, ensure_ascii=False)
            handler.wfile.write(f"data: {ev}\n\n".encode("utf-8"))
            handler.wfile.flush()
            if ev_type == "status":
                break
    finally:
        job.unsubscribe(q)
    return {}


def _stage_spec(ctx: dict) -> dict:
    """Спека стадии (GET /api/stages/{key}/spec).

    Поля и их значения — из реестра настроек: значение общего .env, поверх
    него — переменные окружения, иначе дефолт реестра. Собственного .env у
    книги больше нет: локальные значения запусков — рабочее состояние
    браузера (localStorage), а не слой конфига.

    У LLM-стадии первым идёт поле `profile` (тоже noenv: его значение — выбор
    браузера, а не конфиг): оно и решает, чьи сервер/модель/потоки/рассуждения
    подставит with_llm. Профиль выбирается стадией, поэтому перевод и
    глоссарий одной книги могут идти на разные серверы.
    """
    key = ctx["params"]["key"]
    spec = spec_for(key)
    if spec is None:
        raise ApiError(404, "Стадия не найдена")
    spec = copy.deepcopy(spec)  # не мутируем глобальный кэш спекаций
    spec["profiles"] = _profile_summary()
    if core_settings.is_llm_stage(key):
        # профиль — поле формы: его варианты и подписи перечитываются при
        # каждом открытии формы (профиль могли создать вне интерфейса),
        # кэш спекаций их не видит
        pf = core_settings.profile_field()
        name = core_settings.PROFILE_FIELD
        spec["fields"] = [pf if f.get("name") == name else f
                          for f in (spec.get("fields") or [])]
    pdir = None
    project = ctx["query"].get("project", "")
    if "/" in project:
        try:
            pdir, _section, _name = _project_ctx(ctx)
        except ApiError:
            pdir = None
    for field in spec.get("fields") or []:
        if field.get("noenv"):
            continue  # чипсы (типы/поля) — состояние UI: значения из браузера
        setting = core_settings.BY_KEY.get(core_settings.env_key(key, field["name"]))
        if setting is None:
            continue
        val = core_settings.display_value(setting)
        type_ = field.get("type")
        if type_ == "bool":
            field["default"] = bool(val)
        elif type_ == "files":
            # только basename: селект наполнен именами файлов проекта
            name = str(val).replace("\\", "/").rsplit("/", 1)[-1]
            if not name:
                field["default"] = ""
                continue
            d = field.get("dir") or ""
            if pdir is not None and not ((pdir / d if d else pdir) / name).is_file():
                continue  # «мёртвый» выбор не предзаполняется
            field["default"] = name
        else:
            field["default"] = val
    return {"ok": True, "spec": spec}


# U8: кэш опций стадий — сигнатура mtime папок, влияющих на опции
# (chapters/source/prompts/корень). build_chapter_map на каждый запрос
# дорогой, а папки меняются редко; любое изменение — инвалидация.


def _options_signature(pdir: Path) -> tuple[float, ...]:
    """Сигнатура для кэша опций стадий (U8).

    max mtime файлов в папке (а не mtime каталога): перезапись
    существующего файла (например, правка промпта с тегами) mtime
    каталога не меняет — иначе кэш опций (список промптов, auto_prompt)
    устаревал бы."""
    sig: list[float] = []
    for name in ("chapters", "source", "prompts"):
        d = pdir / name
        try:
            if d.is_dir():
                mt = 0.0
                for f in d.iterdir():
                    try:
                        mt = max(mt, f.stat().st_mtime)
                    except OSError:
                        continue
                sig.append(mt)
            else:
                sig.append(0.0)
        except OSError:
            sig.append(0.0)
    try:
        sig.append(pdir.stat().st_mtime)  # корень: ner.json и т.п.
    except OSError:
        sig.append(0.0)
    return tuple(sig)


def _stage_options(ctx: dict) -> dict:
    """Динамические опции стадии (GET /api/stages/{key}/options?project=)."""
    common = _import_common(ctx)
    spec = spec_for(ctx["params"]["key"])
    if spec is None:
        raise ApiError(404, "Стадия не найдена")
    out: dict = {"ok": True, "options": {}}
    project = ctx["query"].get("project", "")
    if "/" in project:
        pdir, _section, _name = _project_ctx(ctx)
        sig = _options_signature(pdir)
        cached = _OPTIONS_CACHE.get(str(pdir))
        if cached is not None and cached[0] == sig:
            out["options"] = cached[1]
            return out
        # диапазон глав
        chapters_dir = pdir / "chapters"
        if chapters_dir.is_dir():
            ch_map = common.build_chapter_map(chapters_dir)
            nums = sorted(ch_map)
            if nums:
                # ids — реальные главы: таблица конвейера рисует
                # строки по списку, а не по диапазону min..max
                out["options"]["chapters"] = {
                    "min": nums[0], "max": nums[-1], "ids": nums}
        # файлы source/ — ВСЕ файлы (клиент фильтрует по ext селекта):
        # epub-исходники, txt, обложки (jpg/png/webp), metadata.yaml и т.п.
        src = pdir / "source"
        if src.is_dir():
            out["options"]["source"] = sorted(
                f.name for f in src.iterdir() if f.is_file())
        # файлы prompts/
        pr = pdir / "prompts"
        if pr.is_dir():
            out["options"]["prompts"] = sorted(
                f.name for f in pr.iterdir() if f.is_file())
        # автоподхват общего промпт-файла с тегами — ровно тот, что
        # выберет auto-режим конвейера (первый существующий кандидат
        # из _PROMPT_COMBINED_CANDIDATES с тегами). Считается для
        # ЛЮБОЙ стадии: кэш опций общий на проект — иначе при первом
        # запросе чужой стадии pipeline получал кэш без auto_prompt,
        # и «Общий промпт-файл» оставался пустым при живом файле
        for cand in ("pipeline_prompt.txt", "prompts.txt",
                     "translate_book_prompt.txt"):
            f = pdir / "prompts" / cand
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(common.get_tagged_prompt(text, tag)
                   for tag in ("translate_lr", "translate",
                               "redact", "polish")):
                out["options"]["auto_prompt"] = f"prompts/{cand}"
                break
        # файлы корня проекта (для полей files с dir="");
        # dot-файлы (.env, .web_secret) не показываем — секреты
        root = sorted(f.name for f in pdir.iterdir()
                      if f.is_file() and not f.name.startswith("."))
        if root:
            out["options"]["root"] = root
        _OPTIONS_CACHE[str(pdir)] = (sig, out["options"])
    return out


def _profile_summary() -> list:
    """Профили LLM для поля «Профиль LLM»: id, имя, эффективные сервер/модель.

    Значения отдаются уже разложенными по именам полей стадии (host/model),
    секрета здесь нет: api_key в интерфейс не ездит никогда.
    """
    out = []
    for p in core_settings.profiles():
        vals = core_settings.llm_values(p["id"])
        out.append({"id": p["id"], "name": p["name"],
                    "builtin": bool(p.get("builtin")),
                    "host": str(vals.get("host") or ""),
                    "model": str(vals.get("model") or ""),
                    "reasoning_mode": str(vals.get("reasoning_mode") or "")})
    return out


def _stages_list(ctx: dict) -> dict:
    """Список стадий (GET /api/stages): key/title/script + сводка профилей LLM.

    Профили отдаются сводкой — [{id,name,builtin,host,model,reasoning_mode}]:
    SPA рисует по ним подпись выбранного профиля у поля «Профиль LLM». Значения
    уже разложены по историческим именам полей стадии; api_key здесь нет
    никогда. Плюс reasoning — глобальный блок рассуждений (General): спеки
    стадий своих reasoning-полей не содержат.
    """
    return {"ok": True, "stages": [
        {"key": k, "title": v["title"], "script": v["script"]}
        for k, v in ordered_stages()],
        "reasoning": core_settings.block_payload("llm_reasoning"),
        "profiles": _profile_summary()}


# ════════════════════════════════════════════════════════════════════
# epub: предпросмотр разбивки (папки, размеры, удаление, текст)
# ════════════════════════════════════════════════════════════════════


def _epub_preview_path(pdir: Path) -> Path:
    return pdir / EPUB_PREVIEW_FILE


def _epub_preview_read(pdir: Path) -> dict:
    """JSON предпросмотра; нет файла/битый — пустой предпросмотр."""
    import json as _json
    path = _epub_preview_path(pdir)
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"source": "", "num_offset": 1, "title_limit": 50,
                "entries": []}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("source", "")
    data.setdefault("num_offset", 1)
    data.setdefault("title_limit", 50)
    data.setdefault("entries", [])
    return data


def _epub_preview_summary(data: dict) -> dict:
    """Публичное представление: папки + размеры (без текстов)."""
    entries = []
    for e in data.get("entries", []):
        text = e.get("text", "") or ""
        entries.append({
            "seq": e.get("seq"),
            "num": e.get("num"),
            "folder": e.get("folder", ""),
            "heading": e.get("heading", ""),
            "size_kb": round(len(text.encode("utf-8")) / 1024, 1),
        })
    return {"entries": entries}


def _epub_preview_run(ctx: dict, pdir: Path, params: dict,
                      skip: list) -> dict:
    """Запускает скрипт с --preview-json (синхронно); возвращает данные."""
    import subprocess
    import sys as _sys
    repo = Path(_repo_root(ctx))  # repo_root может прийти строкой
    script = script_path("epub", repo)
    if script is None or not script.is_file():
        raise ApiError(500, "Скрипт epub_to_chapters.py не найден")
    argv = build_command("epub", params, ctx)
    argv[0] = str(script)
    argv += ["--preview-json", EPUB_PREVIEW_FILE]
    for s in skip or []:
        argv += ["--skip", str(s)]
    # stdout потомка — utf-8 (на Windows иначе cp1251 → UnicodeEncodeError
    # на стрелках/значках в тексте предпросмотра; см. PYTHONIOENCODING
    # в web/jobs.py)
    proc_env = dict(os.environ)
    proc_env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            [_sys.executable, *argv], cwd=str(pdir),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, env=proc_env)
    except subprocess.TimeoutExpired:
        raise ApiError(500, "Предпросмотр не уложился в 120 c — "
                            "уменьшите исходник")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise ApiError(400, f"Ошибка разбивки: {err[:500]}")
    data = _epub_preview_read(pdir)
    if not data.get("entries"):
        raise ApiError(400, "Разбивка не дала ни одной главы")
    return data


def _epub_preview_post(ctx: dict) -> dict:
    """Создать/обновить предпросмотр (POST /api/stages/epub/preview)."""
    pdir, _sec, _name = _project_ctx(ctx)
    body = ctx["body"] or {}
    data = _epub_preview_run(ctx, pdir, body.get("params") or {},
                             body.get("skip") or [])
    return {"ok": True, "source": data.get("source", ""),
            **_epub_preview_summary(data)}


def _epub_preview_get(ctx: dict) -> dict:
    """Текущий предпросмотр (GET /api/stages/epub/preview)."""
    pdir, _sec, _name = _project_ctx(ctx)
    data = _epub_preview_read(pdir)
    return {"ok": True, "source": data.get("source", ""),
            **_epub_preview_summary(data)}


def _epub_preview_text(ctx: dict) -> dict:
    """Текст главы предпросмотра (GET .../preview/text?num=N)."""
    pdir, _sec, _name = _project_ctx(ctx)
    try:
        num = int(ctx["query"].get("num", ""))
    except (TypeError, ValueError):
        raise ApiError(400, "Параметр num обязателен (номер главы)")
    data = _epub_preview_read(pdir)
    for e in data.get("entries", []):
        if e.get("num") == num:
            return {"ok": True, "heading": e.get("heading", ""),
                    "text": e.get("text", "")}
    raise ApiError(404, f"Глава {num} не найдена в предпросмотре")


def _epub_preview_folder_delete(ctx: dict) -> dict:
    """Удалить главу из предпросмотра + перенумерация
    (DELETE .../preview/folder?seq=N; seq — исходный порядок)."""
    pdir, _sec, _name = _project_ctx(ctx)
    try:
        seq = int(ctx["query"].get("seq", ""))
    except (TypeError, ValueError):
        raise ApiError(400, "Параметр seq обязателен")
    data = _epub_preview_read(pdir)
    entries = [e for e in data.get("entries", [])
               if e.get("seq") != seq]
    if len(entries) == len(data.get("entries", [])):
        raise ApiError(404, f"Секция {seq} не найдена в предпросмотре")
    # перенумерация: каталоги нумеруются по порядку от num_offset,
    # префикс — ширина 6 (00000_1, 0000_12, 000_177…)
    try:
        offset = int(data.get("num_offset", 1))
    except (TypeError, ValueError):
        offset = 1
    for i, e in enumerate(entries):
        num = offset + i
        e["num"] = num
        parts = str(e.get("folder", "")).split("_", 2)
        if len(parts) == 3:
            zeros = "0" * max(0, 6 - len(str(num)))
            e["folder"] = f"{zeros}_{num}_{parts[2]}"
    data["entries"] = entries
    import json as _json
    common = _import_common(ctx)
    common.atomic_write(str(_epub_preview_path(pdir)),
                        _json.dumps(data, ensure_ascii=False, indent=1))
    return {"ok": True, "source": data.get("source", ""),
            **_epub_preview_summary(data)}


def _batch_replace_preview(ctx: dict) -> dict:
    """Предпросмотр замен в одной главе
    (POST /api/stages/batch_replace/preview).

    Тело: {project, type, chapter, replacements}. Правила парсятся и
    применяются тем же путём, что реальный запуск
    (cli.batch_replace.parse_replace_lines + apply_rules_segments) —
    файлы не изменяются. Пустые правила — текст главы без изменений.

    Возвращает segments (keep/del/ins) итогового текста, эффективные правила
    и счётчики замен. Паттерны, замены и метки правил отдаются через
    `mark_whitespace`: пробелы в них невидимы, и без меток «^ + -> » (один
    пробел вместо удаления отступа) выглядит как «ничего не изменилось».
    Битые строки правил не роняют предпросмотр — они приходят в `warnings`.
    """
    pdir, _sec, _name = _project_ctx(ctx)
    br = _import_batch_replace()
    common = _import_common(ctx)
    body = ctx["body"] or {}
    ftype = str(body.get("type") or "polished")
    if ftype not in br.FILE_TYPES:
        raise ApiError(400, f"Неизвестный тип файлов глав: {ftype}")
    try:
        num = int(body.get("chapter") or "")
    except (TypeError, ValueError):
        raise ApiError(400, "Номер главы (chapter) обязателен")
    replacements = body.get("replacements")
    if isinstance(replacements, str):
        lines = [ln for ln in replacements.splitlines() if ln.strip()]
    elif isinstance(replacements, list):
        lines = [str(x) for x in replacements if str(x).strip()]
    else:
        lines = []
    rules, warnings = br.parse_replace_lines(lines)
    if lines and not rules:
        raise ApiError(400, "В форме нет ни одной корректной замены"
                            + (": " + "; ".join(warnings) if warnings else ""))
    chapters_dir = pdir / "chapters"
    ch_map = common.build_chapter_map(chapters_dir)
    dirs = ch_map.get(num)
    if not dirs:
        raise ApiError(404, f"Глава {num} не найдена")
    filepath, warns = common.find_chapter_file(dirs[0], num,
                                               want=ftype, strict=True,
                                               strict_types=True)
    warnings += [w for w in warns if w not in warnings]
    if filepath is None:
        raise ApiError(404, f"В главе {num} нет файла типа {ftype}")
    content = common.read_text_safe(filepath)
    segments, stats = br.apply_rules_segments(content, rules)
    return {"ok": True, "num": num, "dir": Path(dirs[0]).name,
            "type": ftype,
            "changed": bool(stats),
            "rules": [{"pattern": common.mark_whitespace(r.pattern),
                       "replacement": common.mark_whitespace(r.replacement),
                       "count": stats.get(r.label, 0)} for r in rules],
            "stats": [{"label": common.mark_whitespace(label), "count": cnt}
                      for label, cnt in sorted(stats.items(),
                                               key=lambda x: -x[1])],
            "warnings": warnings,
            "segments": segments}


# ════════════════════════════════════════════════════════════════════
# LLM-стадии: предпросмотр первого запроса (--preview-request)
# ════════════════════════════════════════════════════════════════════
# стадии, чьи скрипты поддерживают --preview-request


def _preview_request_post(ctx: dict) -> dict:
    """Предпросмотр первого LLM-запроса стадии
    (POST /api/stages/{key}/preview-request).

    Тело: {project, params}. Синхронный запуск скрипта стадии с
    --preview-request tmp/preview_request.json (без сети, cwd=проект);
    артефакты и логи запуска не создаются. Возвращает payload:
    stage, label, model, messages, chars (символы), meta."""
    import subprocess
    import sys as _sys
    key = ctx["params"]["key"]
    if key not in _PREVIEW_STAGES:
        raise ApiError(404, f"Стадия {key!r} не поддерживает "
                            f"предпросмотр запроса")
    pdir, _sec, _name = _project_ctx(ctx)
    repo = Path(_repo_root(ctx))
    script = script_path(key, repo)
    if script is None or not script.is_file():
        raise ApiError(500, "Скрипт стадии не найден")
    params = (ctx["body"] or {}).get("params") or {}
    argv = build_command(key, params, ctx)
    argv[0] = str(script)
    argv += ["--preview-request", PREVIEW_REQUEST_FILE]
    proc_env = dict(os.environ)
    proc_env.setdefault("PYTHONIOENCODING", "utf-8")
    # предпросмотр обязан показывать тот же запрос, что и запуск: профиль
    # стадии доезжает скрипту той же переменной
    prof = str(params.get(core_settings.PROFILE_FIELD) or "").strip()
    if prof and prof != core_settings.PROFILE_DEFAULT:
        proc_env[core_settings.PROFILE_ENV] = prof
    try:
        proc = subprocess.run(
            [_sys.executable, *argv], cwd=str(pdir),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300, env=proc_env)
    except subprocess.TimeoutExpired:
        raise ApiError(500, "Предпросмотр не уложился в 300 c")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise ApiError(400, f"Ошибка предпросмотра: {err[:500]}")
    path = pdir / PREVIEW_REQUEST_FILE
    import json as _json
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ApiError(500, "Файл предпросмотра не создан или битый")
    if not isinstance(data, dict) or not data.get("messages"):
        raise ApiError(500, "Предпросмотр не содержит сообщений")
    return {"ok": True, **data}


def _register_jobs(router: Router) -> None:
    router.add("GET", "/api/stages", _stages_list)
    router.add("POST", "/api/jobs", _jobs_start)
    router.add("GET", "/api/jobs", _jobs_list)
    router.add("GET", "/api/jobs/active", _jobs_active)
    router.add("DELETE", "/api/jobs", _jobs_clear)
    router.add("GET", "/api/jobs/{id}", _jobs_get)
    router.add("POST", "/api/jobs/{id}/stop", _jobs_stop)
    router.add("DELETE", "/api/jobs/{id}", _jobs_delete)
    router.add("GET", "/api/jobs/{id}/stream", _jobs_stream)
    router.add("GET", "/api/stages/{key}/spec", _stage_spec)
    router.add("GET", "/api/stages/{key}/options", _stage_options)
    # снять локальные отличия книги по стадии (кнопка «Сбросить» в форме)
    # epub: предпросмотр разбивки (папки/размеры/удаление/текст)
    router.add("POST", "/api/stages/epub/preview", _epub_preview_post)
    router.add("GET", "/api/stages/epub/preview", _epub_preview_get)
    router.add("GET", "/api/stages/epub/preview/text", _epub_preview_text)
    router.add("DELETE", "/api/stages/epub/preview/folder",
               _epub_preview_folder_delete)
    router.add("POST", "/api/stages/batch_replace/preview",
               _batch_replace_preview)
    # LLM-стадии: предпросмотр первого запроса
    router.add("POST", "/api/stages/{key}/preview-request",
               _preview_request_post)

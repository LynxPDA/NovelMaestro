#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_stage.py — запуски и стадии: JobManager, запуск стадии, список
и поток событий, спецификация и опции стадий, предпросмотр запроса, эмуляции
epub_to_chapters и batch_replace.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import queue
import time
from collections.abc import Callable
from pathlib import Path
from web.jobs import ACTIVE as _ACTIVE, JobManager, SSE_PING, TAIL_LIMIT
from web.server import ApiError, Router
from core import common as _common
from core import settings as core_settings
from core.common import output_tail
from web.stages import (
    STAGE_SPECS, build_command, ordered_stages, script_path, spec_for,
)
from web.api_common import (
    log,
    _OPTIONS_CACHE,
    EPUB_PREVIEW_FILE,
    PREVIEW_REQUEST_FILE,
    PROMPT_CHECK_FILE,
    PROMPT_CHECK_TIMEOUT,
    _PREVIEW_STAGES,
    _PROMPT_CHECK_CACHE,
    _PROMPT_CHECK_STAGES,
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


def _chapter_bounds(ctx: dict, pdir: Path) -> tuple[int | None, int | None]:
    """Первая и последняя глава книги по папкам chapters/ (None — глав нет)."""
    try:
        common = _import_common(ctx)
        ch_map = common.build_chapter_map(pdir / "chapters")
        nums = sorted(ch_map)
    except Exception:  # noqa: BLE001  границы нужны только для подсказки
        return None, None
    return (nums[0], nums[-1]) if nums else (None, None)


def _check_chapter_range(ctx: dict, action: str, pdir: Path,
                        params: dict) -> None:
    """Диапазон глав из формы — 400 ДО запуска, если он невозможен.

    Ошибка в диапазоне не роняет скрипт — она молча оставляет книгу без работы
    («64 – 3» — пустое пересечение). Правило одно (core.settings.chapter_range_error)
    и для формы (UICore.chapterRangeError), а пределы берутся у книги: стадия,
    которой диапазон не касается в текущем режиме, не проверяется вовсе.
    """
    if not core_settings.range_applies(action, params):
        return
    first, last = _chapter_bounds(ctx, pdir)
    try:
        err = core_settings.chapter_range_error(
            params.get("start"), params.get("end"), last=last, first=first)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from None
    if err:
        raise ApiError(400, err)


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
    _check_chapter_range(ctx, action, pdir, params)
    argv = build_command(action, params, ctx)
    argv[0] = str(script)  # абсолютный путь к скрипту
    jm = _job_manager(ctx)
    # Лимит параллельных задач --jobs-limit (мёртвая опция → живая)
    handler = ctx.get("handler")
    srv = handler.server if handler is not None else None
    limit = (getattr(srv, "jobs_limit", None)
             or int(core_settings.BY_KEY["WEB_JOBS_LIMIT"].default))
    running = sum(1 for j in jm.list() if j.get("status") in _ACTIVE)
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
    # точка истории ДО применения правок проверки (снимок до изменений;
    # точка «после» создаётся по завершении запуска в web.history.on_job_finished).
    # Создаётся в фоновом потоке запуска: ответ уходит сразу, а браузер
    # видит запуск в статусе «pending» — на большой книге add занимает
    # секунды, и ждать его в запросе значило держать кнопку без ответа.
    before: Callable[[], None] | None = None
    if (ctx.get("review_apply") and params.get("apply")
            and not params.get("dry_run")):
        if core_settings.effective("HISTORY_ON_APPLY"):
            def _snapshot() -> None:
                from web import history as hs
                hs.create(pdir, "Перед применением правок", "apply")

            before = _snapshot

    job = jm.start(action, title, project, argv, pdir, env=env,
                   before=before)
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
    active = [j for j in jm.list() if j.get("status") in _ACTIVE]
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


def _sse_frame(obj: dict) -> bytes:
    """Один кадр SSE: «data: <json>» и пустая строка (пробел после «:» — часть
    кадра)."""
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")


def _sse_batch(events: list[tuple]) -> bytes:
    """Очередь событий стрима → кадры одним байтовым блоком: сокет не дёргается
    на каждое событие (тип, данные) → свой кадр."""
    buf = []
    for ev_type, payload in events:
        if ev_type == "line":
            obj = {"type": "line", "text": payload}
        elif ev_type == "event":
            obj = {"type": "event", "event": payload}
        elif ev_type == "progress":
            obj = {"type": "progress", "event": payload}
        else:
            obj = {"type": "status", "status": payload}
        buf.append(_sse_frame(obj))
    return b"".join(buf)


def _jobs_stream(ctx: dict) -> dict:
    """SSE-стрим лога (GET /api/jobs/{id}/stream).

    Снимок буфера уходит ОДНИМ кадром `snapshot` (строки, события глав,
    последний прогресс), живые события — пачками на очередь: тысячи отдельных
    кадров с отдельным flush гнали сокет и браузер через тысячи перерисовок
    панели лога, и вкладка «Запуски» на большой книге висла десятками секунд."""
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
        # снимок: весь текущий хвост буфера, события глав (для живой таблицы)
        # и текущий прогресс — одним кадром и одной записью в сокет
        handler.wfile.write(_sse_frame({
            "type": "snapshot", "lines": job.tail(TAIL_LIMIT),
            "events": list(job.events), "progress": job.progress}))
        # уже завершился до подписки — статус сразу и закрываем
        if job.status != "running":
            handler.wfile.write(_sse_frame({"type": "status",
                                            "status": job.status}))
            handler.wfile.flush()
            return {}
        handler.wfile.flush()
        while True:
            # всё, что накопилось в очереди, уходит одной записью; таймаут
            # — служебный кадр, чтобы соединение не остыл
            try:
                events = [q.get(timeout=SSE_PING)]
            except queue.Empty:
                events = []
            while True:
                try:
                    events.append(q.get_nowait())
                except queue.Empty:
                    break
            if events:
                handler.wfile.write(_sse_batch(events))
            else:
                handler.wfile.write(b": ping\n\n")
            handler.wfile.flush()
            if any(ev_type == "status" for ev_type, _ in events):
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


# U8: кэш опций стадий — отпечаток содержимого папок, влияющих на опции
# (chapters/source/prompts/корень). build_chapter_map на каждый запрос
# дорогой, а папки меняются редко; любое изменение — инвалидация.


def _options_signature(pdir: Path) -> tuple:
    """Сигнатура для кэша опций стадий (U8).

    Отпечаток содержимого папок (core.common.dir_fingerprint), а не mtime
    каталога: переименование epub внутри source/ (book.epub → book2.epub) и
    перезапись файла с тем же размером сохраняют mtime каталога — кеш
    опций оставался бы с прежним списком, и новый исходник не появлялся в
    селекте «Запусков» ни после обновления страницы, ни после перезапуска
    сервера (кеш живёт в процессе)."""
    sig: list = []
    for name in ("chapters", "source", "prompts"):
        sig.append(_common.dir_fingerprint(pdir / name))
    sig.append(_common.dir_fingerprint(pdir))  # корень: ner.json и т.п.
    return tuple(sig)


#: запасной байт на токен, если замерить не по чему (в книге нет ни одной
#: главы с текстом выбранного типа). Прежнее консервативное значение:
#: прогноз от него только ЗАВЫШАЕТ токены, и «не влезет» не пропустит
#: тяжёлую главу
PAIR_RATIO_FALLBACK = 2.5

#: сколько глав диапазона читать ради замера байт на токен. `estimate_tokens`
#: ходит по символам, поэтому мерить на всей книге значило бы прочитать её
#: целиком ради одной цифры: коэффициент задаётся языком и письменностью, а
#: не главой. 10 глав по 30 КБ — 300 КБ, десяток миллисекунд
PAIR_SAMPLE = 10

#: байт на токен по книгам: {путь книги: (отпечаток, коэффициент)}. Считается
#: один раз — иначе каждое открытие формы перечитывало бы те же главы. Живёт
#: в процессе: после перезапуска сервера главы могли измениться
_PAIR_RATIO_CACHE: dict = {}


def _chapter_sizes(common, ch_map: dict, file_type: str = "") -> tuple:
    """Байты файлов глав одним проходом: `(sizes, sample_texts, empty, sig)`.

    `sizes` — {номер: (перевод, оригинал)} в байтах. `sample_texts` — тексты
    первых `PAIR_SAMPLE` глав, у которых есть перевод: по ним считается
    байт на токен (`pair_ratio`). Читать здесь же дешевле, чем второй раз
    открывать те же файлы.

    `file_type` — тип файлов стадии (`polished`/`redacted`/`translated`):
    прогноз обязан считать ровно ту пару, которую прочитает стадия. Пустой
    тип — первый доступный по приоритету (общая оценка книги).

    Один `os.scandir` на папку и `stat` по готовым именам: 0.05 с на 5652
    главах против 1.3 с через `find_chapter_file`. Нет пары у главы — её нет
    и в словаре (в прогноз она попасть не может, но и ложного «влезет» от
    неё не будет — стадия напишет её в лог отдельно).
    """
    # С выбранным типом — ТОЛЬКО он: `strict_types=True` у стадии не
    # подставляет другой файл, и прогноз, падающий в translated, обещал бы
    # проверку главы, которую стадия молча пропустит (на живой книге так
    # расходились 111 polished против 114 translated)
    order = ["chapter.txt"]
    if file_type:
        order.append(file_type if file_type.endswith(".txt")
                     else file_type + ".txt")
    else:
        order += ["polished.txt", "redacted.txt", "translated.txt"]
    sizes: dict = {}
    texts: list = []
    sig: list = []
    empty = 0
    for num, dirs in sorted(ch_map.items()):
        if not dirs:
            continue
        try:
            entries = {e.name: e for e in os.scandir(dirs[0]) if e.is_file()}
        except OSError:
            continue
        tr = src = 0
        tr_name = ""
        for name in order[1:]:
            if name in entries:
                size = entries[name].stat().st_size
                if not size:
                    # ПУСТОЙ файл — это не текст: у главы 111 `polished.txt`
                    # нулевой после сбоя стадии, а перевод лежит в
                    # `redacted.txt`. Считать его «главой с переводом»
                    # значило бы обещать проверку пустой главы
                    continue
                tr, tr_name = size, name
                break
        if "chapter.txt" in entries:
            src = entries["chapter.txt"].stat().st_size
        # глава без единого байта текста — стадия её пропустит, и прогноз
        # обязан сказать о ней отдельно, а не молчать
        if not (tr or src):
            empty += 1
        if tr or src:
            sizes[num] = (tr, src)
        if tr_name and len(texts) < PAIR_SAMPLE:
            tr_st = entries[tr_name].stat()
            sr_st = (entries["chapter.txt"].stat()
                     if "chapter.txt" in entries else None)
            # размер и mtime обоих файлов — отпечаток ВЫБОРКИ: правку
            # содержания иначе не увидеть (mtime папки главы при
            # перезаписи файла не меняется)
            sig.append((num, tr_st.st_size, tr_st.st_mtime_ns,
                        sr_st.st_size if sr_st else 0,
                        sr_st.st_mtime_ns if sr_st else 0))
            texts.append((_read_text(entries[tr_name].path),
                          _read_text(entries["chapter.txt"].path)
                          if "chapter.txt" in entries else ""))
    return sizes, texts, empty, tuple(sig)


def _read_text(path) -> str:
    """Текст файла для замера коэффициента (пусто — файл не прочитался)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _pair_ratio(common, pairs: list) -> float:
    """Байт на токен для ПАРЫ «перевод + оригинал».

    Делить байты пары на коэффициент одного перевода нельзя: у китайского
    оригинала 3 байта на иероглиф при том же числе токенов, и общая дробь
    выходила завышенной на 18 % — прогноз недосчитывал по тысяче токенов на
    главу (проверено на книге: 111 глав, все 110 недооценены).

    Считать надо ровно то, что складывается: `sum(байт пары) /
    sum(estimate_tokens(перевод) + estimate_tokens(оригинал))`. Поэтому
    тексты выборки передаются парами, а `estimate_tokens` вызывается для
    обоих файлов — он и есть единственный счётчик проекта.

    Возвращает `PAIR_RATIO_FALLBACK`, если мерить не по чему.
    """
    if not pairs:
        return PAIR_RATIO_FALLBACK
    tokens = 0
    raw = 0
    for tr, src in pairs:
        t = common.estimate_tokens(tr) if tr else 0
        s2 = common.estimate_tokens(src) if src else 0
        if not (t or s2):
            continue
        tokens += t + s2
        raw += len((tr or "").encode("utf-8")) + len((src or "").encode("utf-8"))
    if not tokens or not raw:
        return PAIR_RATIO_FALLBACK
    value = round(raw / tokens, 2)
    # Предохранитель: прогноз обязан скорее ЗАВЫСИТЬ, чем пропустить
    # тяжёлую главу, поэтому ниже прежних 2.5 коэффициент не опускаем.
    return max(PAIR_RATIO_FALLBACK, value)


def _stage_file_type(pdir, key: str) -> str:
    """Тип файлов главы из настроек стадии (поле `<СТАДИЯ>_TYPE`).

    Прогноз обязан считать ту же пару, что прочитает стадия: у «polished»
    и «translated» разные размеры, и прогноз по одному типу давал бы
    «влезет» на книге, где стадия читает другой. Нет настройки — пусто:
    `_chapter_sizes` возьмёт первый доступный тип.
    """
    try:
        from core import settings as core_settings
        name = core_settings.env_key(key, "type")
        if not name:
            return ""
        return str(core_settings.effective(name) or "")
    except Exception:  # реестр не обязан знать про эту стадию
        return ""


def _pair_ratio_cached(common, pdir, sample: list, sig) -> float:
    """Байт на токен с кешем на книгу: перечитывать выборку незачем.

    Ключ — путь книги и отпечаток ТЕХ ФАЙЛОВ, по которым мерили (`sig`
    считает `_chapter_sizes` из самих `stat` выборки). `dir_fingerprint`
    папки `chapters/` здесь не годится: от подпапки главы он берёт только
    имя и mtime, а перезапись существующего файла mtime папки не меняет —
    заменили перевод на другой язык, коэффициент остался прежним (4.2 →
    4.2 при латинице вместо кириллицы).

    Пустая выборка не кешируется: у книги без глав файлов ещё нет, и
    запасное значение «не по чему мерить» — не измеренный коэффициент.
    """
    key = str(pdir)
    hit = _PAIR_RATIO_CACHE.get(key)
    if hit is not None and hit[0] == sig and hit[1] is not None:
        return hit[1]
    ratio = _pair_ratio(common, sample)
    if sig:
        _PAIR_RATIO_CACHE[key] = (sig, ratio)
    return ratio


def _file_type_arg(ctx: dict) -> str:
    """Тип файлов главы, выбранный В ФОРМЕ (`type=polished|redacted|…`).

    Прогноз обязан считать ту пару, которую прочитает стадия. Значение в
    `.env` — лишь дефолт: человек меняет список «Тип файлов глав» прямо в
    форме, и прогноз по старому типу показывал бы чужой вес пары (на книге
    с разными `polished`/`translated` это давало завышение в разы).
    """
    val = str(ctx["query"].get("type") or "").strip()
    return val if val in ("polished", "redacted", "translated") else ""


def _pair_options(common, pdir: Path, key: str, file_type: str = "") -> dict:
    """Прогноз бюджета для «Проверки перевода (LLM)»: байты пары по главам
    и замеренный байт на токен.

    Считается по требованию, а НЕ кладётся в общий кеш опций: кеш общий на
    книгу и заполняется любой стадией, а этот словарь нужен одной. `file_type`
    — тип из формы; пустой — берётся из настроек стадии (`.env`).
    """
    chapters_dir = pdir / "chapters"
    if not chapters_dir.is_dir():
        return {}
    ch_map = common.build_chapter_map(chapters_dir)
    if not ch_map:
        return {}
    ft = file_type or _stage_file_type(pdir, key)
    sizes, texts, empty, fsig = _chapter_sizes(common, ch_map, ft)
    ratio = _pair_ratio_cached(common, pdir, texts, fsig)
    return {
        "pair_bytes": {str(n): {"tr": tr, "src": src}
                       for n, (tr, src) in sizes.items()},
        "pair_ratio": {"value": ratio,
                       "fallback": ratio == PAIR_RATIO_FALLBACK,
                       "samples": len(texts), "type": ft, "empty": empty}}


def _with_pair_options(common, pdir: Path, key: str, ctx: dict) -> dict:
    """Опции стадии из кеша ПЛЮС прогноз бюджета (кеш не меняется).

    Кеш мог заполнить кто угодно и когда угодно — прогноз добавляется на
    выдаче, чтобы чужая стадия не «съедала» его из общего словаря.
    """
    out = dict(_OPTIONS_CACHE.get(str(pdir), (None, {}))[1])
    out.update(_pair_options(common, pdir, key, _file_type_arg(ctx)))
    return _options_for_stage(out, key)


def _options_for_stage(options: dict, key: str) -> dict:
    """Опции одной стадии из общего кеша проекта.

    Кеш опций общий на книгу (диапазон глав, файлы source/prompts, корень) —
    иначе каждая стадия заново обходила бы папки. Но `pair_bytes` (прогноз
    «не влезет в бюджет») нужен ТОЛЬКО проверке перевода LLM: остальным он
    не просто лишний — клиент его не знает, а размер словаря по 5652 главам
    в каждом ответе не нужен никому.
    """
    if key == "translate_check_llm":
        return options
    if "pair_bytes" not in options and "pair_ratio" not in options:
        return options
    out = dict(options)
    out.pop("pair_bytes", None)
    out.pop("pair_ratio", None)
    return out


def _stage_options(ctx: dict) -> dict:
    """Динамические опции стадии (GET /api/stages/{key}/options?project=)."""
    common = _import_common(ctx)
    key = ctx["params"]["key"]
    spec = spec_for(key)
    if spec is None:
        raise ApiError(404, "Стадия не найдена")
    out: dict = {"ok": True, "options": {}}
    project = ctx["query"].get("project", "")
    if "/" in project:
        pdir, _section, _name = _project_ctx(ctx)
        sig = _options_signature(pdir)
        cached = _OPTIONS_CACHE.get(str(pdir))
        if cached is not None and cached[0] == sig:
            out["options"] = _options_for_stage(cached[1], key)
            # Прогноз бюджета считается на ВЫДАЧЕ и в кеш не попадает: кеш
            # общий на книгу, его заполняет любая стадия (ner, epub, перевод),
            # а тип файлов приходит из формы — от него зависит вес пары.
            if key == "translate_check_llm":
                out["options"] = _with_pair_options(common, pdir, key, ctx)
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
                   for tag in ("translate", "redact", "polish")):
                out["options"]["auto_prompt"] = f"prompts/{cand}"
                break
        # файлы корня проекта (для полей files с dir="");
        # dot-файлы (.env, .web_secret) не показываем — секреты
        root = sorted(f.name for f in pdir.iterdir()
                      if f.is_file() and not f.name.startswith("."))
        if root:
            out["options"]["root"] = root
        _OPTIONS_CACHE[str(pdir)] = (sig, out["options"])
        out["options"] = _options_for_stage(out["options"], key)
        # прогноз бюджета — ПОСЛЕ записи в кеш и отдельным словарём: в кеше
        # ему делать нечего (нужен одной стадии и зависит от типа файлов,
        # который меняют в форме, а не на диске)
        if key == "translate_check_llm":
            out["options"].update(
                _pair_options(common, pdir, key, _file_type_arg(ctx)))
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
    """Публичное представление: папки + размеры (без текстов).

    num_offset едет к клиенту: номер каталога считается от него, и SPA
    показывает нумерацию так же, как её посчитает скрипт.
    """
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
    return {"entries": entries,
            "num_offset": data.get("num_offset", 1)}


def _epub_preview_run(ctx: dict, pdir: Path, params: dict) -> dict:
    """Запускает скрипт с --preview-json (синхронно); возвращает данные.

    Параметры — ровно та форма, что уйдёт в запуск: снятые галочки секций
    (`skip`) приезжают внутри params, поэтому предпросмотр и запись на диск
    собираются одним build_command и не могут разойтись.
    """
    import subprocess
    import sys as _sys
    repo = Path(_repo_root(ctx))  # repo_root может прийти строкой
    script = script_path("epub", repo)
    if script is None or not script.is_file():
        raise ApiError(500, "Скрипт epub_to_chapters.py не найден")
    argv = build_command("epub", params or {}, ctx)
    argv[0] = str(script)
    argv += ["--preview-json", EPUB_PREVIEW_FILE]
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
        err = output_tail(proc.stderr, proc.stdout)
        raise ApiError(400, f"Ошибка разбивки: {err}")
    data = _epub_preview_read(pdir)
    if not data.get("entries"):
        raise ApiError(400, "Разбивка не дала ни одной главы")
    return data


def _epub_preview_post(ctx: dict) -> dict:
    """Создать/обновить предпросмотр (POST /api/stages/epub/preview)."""
    pdir, _sec, _name = _project_ctx(ctx)
    body = ctx["body"] or {}
    data = _epub_preview_run(ctx, pdir, body.get("params") or {})
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
def _int_or_none(value) -> int | None:
    """Пустое/битое значение диапазона — «не задано» (скрипт возьмёт края книги)."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    # тот же диапазон глав, что и у запуска: предпросмотр не может показывать
    # запрос главы, которая за пределами книги (иначе pipeline молча берёт 001)
    _check_chapter_range(ctx, key, pdir, params)
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
        err = output_tail(proc.stderr, proc.stdout)
        raise ApiError(400, f"Ошибка предпросмотра: {err}")
    path = pdir / PREVIEW_REQUEST_FILE
    import json as _json
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ApiError(500, "Файл предпросмотра не создан или битый")
    if not isinstance(data, dict) or not data.get("messages"):
        raise ApiError(500, "Предпросмотр не содержит сообщений")
    return {"ok": True, **data}


# ════════════════════════════════════════════════════════════════════
# LLM-стадии: проверка промпт-файла (--check-prompt)
# ════════════════════════════════════════════════════════════════════
def _prompt_file_rel(key: str, params: dict) -> str:
    """Выбранный промпт-файл путием внутри книги. Форма присылает готовый
    путь («prompts/имя»); голое имя приходит при ручном вызове API — ему
    возвращаем папку поля, тот же путь, что даёт apply_cli_defaults
    обычному запуску."""
    name = str(params.get("prompt_file") or "").strip()
    if not name or "/" in name:
        return name
    fld = next((s for s in core_settings.stage_fields(key)
                if s.name == "prompt_file"), None)
    return f"{fld.dir}/{name}" if fld is not None and fld.dir else name


def _prompt_check_post(ctx: dict) -> dict:
    """Проверка промпт-файла стадии
    (POST /api/stages/{key}/prompt-check).

    Тело: {project, params}. Синхронный запуск скрипта стадии с
    --check-prompt tmp/prompt_check.json (без сети, cwd=проект): какие теги и
    переменные читает стадия — знает её скрипт, здесь проверка только
    запускается. Проверяются ВСЕ режимы стадии: форма выбирает один, а
    пользователь обязан видеть, что не так в остальных. Логи запусков и
    артефакты стадии не создаются, файл вердикта читается и удаляется.
    Ответ кэшируется по (книга, стадия, файл, mtime): промпт меняют редко.
    Не удалось (таймаут, чужой код возврата, битый вердикт) — `result: None`
    плюс `error` с причиной: SPA показывает его тем же предупреждением, а не
    молчит — молчание читается как «промпт в порядке». Форма не ломается."""
    import subprocess
    import sys as _sys
    key = ctx["params"]["key"]
    if key not in _PROMPT_CHECK_STAGES:
        raise ApiError(404, f"Стадия {key!r} не проверяет промпт-файл")
    pdir, _sec, _name = _project_ctx(ctx)
    repo = Path(_repo_root(ctx))
    script = script_path(key, repo)
    if script is None or not script.is_file():
        raise ApiError(500, "Скрипт стадии не найден")
    params = (ctx["body"] or {}).get("params") or {}
    prompt_file = _prompt_file_rel(key, params)
    try:
        mtime = (pdir / prompt_file).stat().st_mtime if prompt_file else -1.0
    except OSError:
        mtime = -1.0
    ck = f"{pdir}|{key}|{prompt_file}"
    cached = _PROMPT_CHECK_CACHE.get(ck)
    if cached is not None and cached[0] == mtime:
        return {"ok": True, "result": cached[1]}
    argv = build_command(key, params, ctx)
    argv[0] = str(script)
    argv += ["--check-prompt", PROMPT_CHECK_FILE]
    proc_env = dict(os.environ)
    proc_env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            [_sys.executable, *argv], cwd=str(pdir),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=PROMPT_CHECK_TIMEOUT, env=proc_env)
    except subprocess.TimeoutExpired:
        log.info("prompt-check: %s/%s не уложилась в %d c", key, pdir.name,
                 PROMPT_CHECK_TIMEOUT)
        return {"ok": True, "result": None,
                "error": f"проверка не уложилась в {PROMPT_CHECK_TIMEOUT} c"}
    if proc.returncode != 0:
        err = output_tail(proc.stderr, proc.stdout, 300)
        log.info("prompt-check: %s/%s — код %d: %s", key, pdir.name,
                 proc.returncode, err)
        return {"ok": True, "result": None,
                "error": f"проверка завершилась с кодом {proc.returncode}: "
                         f"{err}"}
    path = pdir / PROMPT_CHECK_FILE
    import json as _json
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": True, "result": None,
                "error": f"вердикт не читается: {exc}"}
    finally:
        # вердикт — служебный файл проверки, в списке файлов книги он не нужен
        try:
            path.unlink()
        except OSError:
            pass
    if not isinstance(data, dict) or "problems" not in data:
        return {"ok": True, "result": None, "error": "битый вердикт"}
    _PROMPT_CHECK_CACHE[ck] = (mtime, data)
    return {"ok": True, "result": data}


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
    # LLM-стадии: проверка промпт-файла (иконка предупреждения в форме)
    router.add("POST", "/api/stages/{key}/prompt-check", _prompt_check_post)

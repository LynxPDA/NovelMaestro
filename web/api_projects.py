#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_projects.py — пульт и проекты: разделы, список, создание, перенос,
переименование, копирование, удаление, статусы, дерево и названия глав.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import os
from pathlib import Path
from web.jobs import JobManager
from web.server import ApiError, Router
from core import archive as core_archive
from core import uistate as core_uistate
from web.stages import (
    STAGE_SPECS, build_command, ordered_stages, script_path, spec_for,
)
from web import state as st
from web.api_common import (
    log,
    _STATS_CACHE,
    _STATS_LOCK,
    _STATS_CACHE_FILE,
    _STATUS_CACHE_VER,
    _CACHE_LOADED,
)
from web.api_common import (
    _check_confirm,
    _import_common,
    _import_projects,
    _project_path,
    _projects_root,
    _repo_root,
)
from web.api_stage import _job_manager
from web.api_templates import _templates


def _register_hub(router: Router) -> None:
    router.add("GET", "/api/state", _state)
    router.add("GET", "/api/dashboard", _dashboard)
    router.add("GET", "/api/actions", _actions)
    router.add("GET", "/api/sections", _sections)
    router.add("POST", "/api/sections", _sections_create)
    router.add("POST", "/api/sections/rename", _sections_rename)
    router.add("DELETE", "/api/sections/{name}", _sections_delete)
    router.add("GET", "/api/projects", _projects_list)
    router.add("POST", "/api/projects", _projects_create)
    router.add("POST", "/api/projects/move", _projects_move)
    router.add("POST", "/api/projects/move-many", _projects_move_many)
    router.add("POST", "/api/projects/rename", _projects_rename)
    router.add("POST", "/api/projects/copy", _projects_copy)
    router.add("DELETE", "/api/projects", _projects_delete)
    router.add("POST", "/api/projects/archive", _projects_archive)
    router.add("POST", "/api/sections/{name}/archive", _section_archive_all)
    router.add("GET", "/api/projects/{sec}/{name}/archive", _project_archive)
    router.add("GET", "/api/projects/{sec}/{name}/archive/download",
               _project_archive_download)
    router.add("POST", "/api/sections/{name}/props", _section_props_set)
    router.add("GET", "/api/projects/{sec}/{name}/stats", _project_stats)
    router.add("GET", "/api/projects/{sec}/{name}/status", _project_status)
    router.add("GET", "/api/projects/{sec}/{name}/tree", _project_tree)
    router.add("GET", "/api/projects/{sec}/{name}/chapters/titles",
               _chapter_titles_get)
    router.add("PUT", "/api/projects/{sec}/{name}/chapters/titles",
               _chapter_titles_put)
    router.add("DELETE", "/api/projects/{sec}/{name}/chapters",
               _chapters_delete)
    router.add("GET", "/api/projects/{sec}/{name}/ui-state", _ui_state_get)
    router.add("PUT", "/api/projects/{sec}/{name}/ui-state", _ui_state_put)
    router.add("GET", "/api/templates", _templates)


def _state(ctx: dict) -> dict:
    """hub_state — общий с cli-пультом (последний раздел/проект)."""
    hub = st.load_hub_state(_projects_root(ctx))
    return {"ok": True, **hub}


# артефакты готовности главы — те же имена, что в core.projects
# (_artifact_ready): канонические и легаси-суффиксы старых проектов
_ARTIFACT_CANON = ("translated.txt", "redacted.txt", "polished.txt")
_ARTIFACT_SUFFIXES = tuple("_" + n for n in _ARTIFACT_CANON)


def _chapter_artifacts(path: str) -> str:
    """Артефакты одной главы строкой «имя:размер:mtime».

    Размер обязателен: стадия открывает выход на запись до генерации, и
    пустой файл готовым не считается (core.projects._artifact_ready).
    Полный перечень нужен только пока не все канонические файлы готовы:
    легаси-суффиксы меняют готовность главы, лишь когда канонические пусты.
    """
    items = []
    complete = True
    for name in _ARTIFACT_CANON:
        try:
            st = os.stat(os.path.join(path, name))
        except OSError:
            complete = False
            continue
        items.append(f"{name}:{st.st_size}:{st.st_mtime_ns}")
        if st.st_size <= 0:
            complete = False
    if not complete:
        try:
            with os.scandir(path) as it:
                for f in it:
                    if not f.name.endswith(_ARTIFACT_SUFFIXES):
                        continue
                    try:
                        st = f.stat()
                    except OSError:
                        continue
                    items.append(f"{f.name}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            pass
    items.sort()
    return ",".join(items)


def _stats_signature(pdir: Path) -> str:
    """Отпечаток состояния проекта для кеша stats и status.

    Содержит состав папок глав с их артефактами (размер + mtime), mtime
    ner.json/wiki.md и mtime папок compiled-кандидатов. Сами артефакты нужны
    потому, что mtime папки главы готовность не показывает: стадия открывает
    выход на запись ДО генерации — папка меняет mtime на пустом файле, а
    дописанный в него позже текст папку уже не трогает.
    """
    pdir = Path(pdir)
    h = hashlib.sha256()
    ch = pdir / "chapters"
    try:
        with os.scandir(ch) as it:
            dirs = sorted((e for e in it if e.is_dir()),
                          key=lambda e: e.name)
    except OSError:
        dirs = []
    h.update(f"ch:{len(dirs)}\0".encode())
    for d in dirs:
        h.update(f"{d.name}\0{_chapter_artifacts(d.path)}\0".encode())
    for f in ("ner.json", "wiki.md"):
        p = pdir / f
        try:
            m = p.stat().st_mtime_ns
        except OSError:
            m = 0
        h.update(f"{f}:{m}\0".encode())
    for d in (pdir, pdir / "tmp", pdir / "output"):
        try:
            m = d.stat().st_mtime_ns
        except OSError:
            m = 0
        h.update(f"d:{d.name}:{m}\0".encode())
    return h.hexdigest()


def _stats_cache_path(root: Path) -> Path:
    """Файл дискового кеша stats (в корне projects/, рядом с hub_state)."""
    return root / _STATS_CACHE_FILE


def _stats_cache_key(root: Path, sec: str, name: str) -> str:
    """Ключ кеша с пространством имён корня projects/:
    два сервера на разных корнях не смешивают записи."""
    return f"{root}::{sec}/{name}"


def _load_stats_cache(root: Path) -> None:
    """Загрузка дискового кеша stats (переживает рестарт сервера).

    Записи статуса чужой версии методики не грузятся: их ключ всё равно не
    читается, а держать их в памяти и переписывать на диск незачем.
    """
    try:
        import json as _json
        data = _json.loads(_stats_cache_path(root).read_text(
            encoding="utf-8"))
        if isinstance(data, dict):
            pfx = f"{root}::"
            own = f":status:v{_STATUS_CACHE_VER}"
            _STATS_CACHE.update(
                {pfx + k: v for k, v in data.items()
                 if isinstance(v, dict) and "sig" in v
                 and ("stats" in v or "status" in v)
                 and (":status:" not in k or k.endswith(own))})
    except (OSError, ValueError) as exc:
        log.debug("stats-кеш не читается: %s", exc)


def _save_stats_cache(root: Path) -> None:
    """Атомарная запись дискового кеша stats (только записи этого корня)."""
    try:
        import json as _json
        from core.common import atomic_write
        pfx = f"{root}::"
        mine = {k[len(pfx):]: v for k, v in _STATS_CACHE.items()
                if k.startswith(pfx)}
        atomic_write(_stats_cache_path(root),
                     _json.dumps(mine, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — кеш не критичен
        log.debug("stats-кеш не пишется: %s", exc)


def _ensure_stats_cache(root: Path) -> None:
    """Загрузка дискового кеша один раз на корень projects/."""
    key_root = str(root)
    if key_root in _CACHE_LOADED:
        return
    _CACHE_LOADED.add(key_root)
    _load_stats_cache(root)


def _invalidate_all_stats(root: Path) -> None:
    """Сброс кеша stats для этого корня (структурные операции)."""
    with _STATS_LOCK:
        pfx = f"{root}::"
        for k in [k for k in _STATS_CACHE if k.startswith(pfx)]:
            del _STATS_CACHE[k]
        _save_stats_cache(root)


def _invalidate_stats_entry(root: Path, sec: str, name: str) -> None:
    """Точечный сброс кеша stats одного проекта (создание/перенос/
    переименование/дублирование/удаление) — остальные проекты кеш
    сохраняют, dashboard после операции не пересобирается целиком.
    """
    with _STATS_LOCK:
        key = _stats_cache_key(root, sec, name)
        for k in (key, key + f":status:v{_STATUS_CACHE_VER}"):
            _STATS_CACHE.pop(k, None)
        _save_stats_cache(root)


def _cached_stats(prj, root: Path, sec: str, name: str) -> str:
    """stats проекта: из кеша, если сигнатура не изменилась (без TTL).

    Кеш в памяти + на диске; актуальность — сигнатура состояния, а не время.
    """
    key = _stats_cache_key(root, sec, name)
    pdir = root / sec / name
    sig = _stats_signature(pdir)
    with _STATS_LOCK:
        _ensure_stats_cache(root)
        entry = _STATS_CACHE.get(key)
        if entry is not None and entry.get("sig") == sig:
            return entry["stats"]
        try:
            stats = prj.project_stats(pdir)
        except Exception as exc:  # noqa: BLE001 — статистика необязательна
            log.debug("stats(%s) не собралась: %s", key, exc)
            stats = ""
        _STATS_CACHE[key] = {"sig": sig, "stats": stats}
        _save_stats_cache(root)
        return stats


def _cached_stats_or_empty(prj, root: Path, sec: str, name: str) -> str:
    """stats архивного проекта: только сохранённая строка, без пересчёта.

    Папки нет — сигнатуру считать не по чему. Если строки в кеше не оказалось
    (архив лежал до первого открытия страницы), отдаётся пустая: карточка
    всё равно покажет размер и дату из манифеста.
    """
    key = _stats_cache_key(root, sec, name)
    with _STATS_LOCK:
        _ensure_stats_cache(root)
        entry = _STATS_CACHE.get(key)
        return (entry or {}).get("stats", "")


def _cached_status(prj, root: Path, sec: str, name: str) -> dict:
    """Таблица готовности глав: кеш по сигнатуре (как stats).

    Ключ с версией методики (_STATUS_CACHE_VER): правка расчёта
    (напр. счётчик статей rulate-wiki) инвалидирует старые записи —
    сигнатура wiki.md их не ловит.
    """
    key = _stats_cache_key(root, sec, name) + f":status:v{_STATUS_CACHE_VER}"
    pdir = root / sec / name
    sig = _stats_signature(pdir)
    with _STATS_LOCK:
        _ensure_stats_cache(root)
        entry = _STATS_CACHE.get(key)
        if entry is not None and entry.get("sig") == sig:
            return entry.get("status", {})
        try:
            status = prj.project_progress_table(pdir)
        except Exception as exc:  # noqa: BLE001 — статус необязателен
            log.debug("status(%s) не собрался: %s", key, exc)
            status = {}
        _STATS_CACHE[key] = {"sig": sig, "status": status}
        _save_stats_cache(root)
        return status


def _collect_stats(prj, root: Path) -> tuple[list, int]:
    """Обход всех разделов/проектов; stats — из кеша по сигнатуре.

    Проект в архиве папки не имеет: пересчитывать статистику не по чему,
    поэтому отдаётся сохранённая строка кеша (ключ переживает упаковку)
    плюс сведения самого архива — размер и дата упаковки.
    """
    sections = []
    total = 0
    for sec in prj.load_sections(root):
        items = []
        props = prj.section_props(root, sec)
        for item in prj.project_items(root, sec):
            n = item["name"]
            if item.get("archived"):
                stats = _cached_stats_or_empty(prj, root, sec, n)
            else:
                stats = _cached_stats(prj, root, sec, n)
            entry = {"name": n, "stats": stats}
            if item.get("archived"):
                entry["archive"] = {"size": item.get("size", 0),
                                    "packed_at": item.get("packed_at", "")}
            items.append(entry)
        total += len(items)
        sections.append({"name": sec, "projects": items,
                         "archive": bool(props.get("archive")),
                         "pack_on_move": bool(props.get("pack_on_move"))})
    return sections, total


def _dashboard(ctx: dict) -> dict:
    """Сводка для дашборда: разделы/проекты/статистика + недавние jobs.

    Один запрос вместо N запросов /stats с фронта. Stats — кеш по
    сигнатуре (без TTL); jobs читаются всегда свежими через общий
    JobManager (_job_manager): HTTP-контекст не носит job_manager.
    """
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    hub = st.load_hub_state(root)
    sections, total = _collect_stats(prj, root)
    recent_jobs = []
    running_jobs = []
    jm = _job_manager(ctx)
    try:
        # «Последние запуски» — до 20; активные — из ПОЛНОГО
        # списка, а не из среза (running может быть старше 20 записей)
        all_jobs = jm.list()
        recent_jobs = all_jobs[:20]
        for item in all_jobs:
            if item.get("status") == "running":
                job = jm.get(item["id"])
                if job is not None:
                    running_jobs.append(job.payload())
    except Exception as exc:  # noqa: BLE001
        log.debug("jobs.list() не собрался: %s", exc)
    return {"ok": True, "total": total, "sections": sections,
            "hub": hub, "recent_jobs": recent_jobs,
            "running_jobs": running_jobs}


def _actions(ctx: dict) -> dict:
    """Реестр стадий из STAGE_SPECS + доступность скриптов ."""
    repo = _repo_root(ctx)
    items = []
    for key, spec in ordered_stages():
        script = script_path(key, repo)
        items.append({
            "key": key, "title": spec["title"], "folder": "cli",
            "script": spec["script"],
            "available": script is not None and script.is_file(),
        })
    return {"ok": True, "actions": items}


def _sections(ctx: dict) -> dict:
    """Разделы со счётчиками проектов."""
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    out = []
    for s in prj.load_sections(root):
        props = prj.section_props(root, s)
        out.append({"name": s, "count": len(prj.list_projects(root, s)),
                    "archive": bool(props.get("archive")),
                    "pack_on_move": bool(props.get("pack_on_move"))})
    return {"ok": True, "sections": out}


def _hub_clear_section(root: Path, section: str) -> None:
    """Зачистить ссылку на раздел в hub_state (переименован/удалён)."""
    hub = st.load_hub_state(root)
    if hub.get("section") == section:
        hub.pop("section", None)
        hub.pop("project", None)
        st.save_hub_state(root, hub)


def _sections_create(ctx: dict) -> dict:
    """Создать раздел (POST /api/sections, {"name": ...})."""
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    name = (ctx["body"].get("name") or "").strip()
    ok, res = prj.create_section(root, name)
    if not ok:
        raise ApiError(400, str(res))
    _invalidate_all_stats(root)
    return {"ok": True, "name": res}


def _sections_rename(ctx: dict) -> dict:
    """Переименовать/слить раздел (POST /api/sections/rename, {src, dst})."""
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    body = ctx["body"]
    src = (body.get("src") or "").strip()
    dst = (body.get("dst") or "").strip()
    if not src or not dst:
        raise ApiError(400, "src и dst обязательны")
    ok, res = prj.rename_section(root, src, dst)
    if not ok:
        raise ApiError(400, str(res))
    _hub_clear_section(root, src)
    _invalidate_all_stats(root)
    return {"ok": True, "src": src, "dst": res}


def _sections_delete(ctx: dict) -> dict:
    """Удалить пустой раздел (DELETE /api/sections/{name})."""
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    name = ctx["params"]["name"]
    ok, res = prj.delete_section(root, name)
    if not ok:
        code = 409 if "не пуст" in str(res) else 404
        raise ApiError(code, str(res))
    _hub_clear_section(root, name)
    _invalidate_all_stats(root)
    return {"ok": True, "name": res}


def _projects_list(ctx: dict) -> dict:
    """Проекты раздела (GET /api/projects?section=).

    `projects` — имена, как было; `items` — те же проекты со сведениями об
    архиве (упакован ли, размер, дата). Старое поле остаётся: его читают
    и чужие клиенты, и часть SPA; новое добавляется рядом, чтобы не ломать
    форму ответа.
    """
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    section = ctx["query"].get("section", "")
    if section not in prj.load_sections(root):
        raise ApiError(400, f"Неизвестный раздел: {section!r}")
    items = [{"name": p["name"], "archived": bool(p.get("archived")),
              "size": p.get("size", 0),
              "packed_at": p.get("packed_at", "")}
             for p in prj.project_items(root, section)]
    props = prj.section_props(root, section)
    return {"ok": True, "section": section,
            "projects": [p["name"] for p in items],
            "items": items,
            "props": {"archive": bool(props.get("archive")),
                      "pack_on_move": bool(props.get("pack_on_move"))}}


def _projects_create(ctx: dict) -> dict:
    """Создать проект: раздел, имя, метаданные, шаблон типа книги."""
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    repo = _repo_root(ctx)
    body = ctx["body"]
    section = (body.get("section") or "").strip()
    name = (body.get("name") or "").strip()
    if section not in prj.load_sections(root):
        raise ApiError(400, f"Неизвестный раздел: {section!r}")
    raw_name = name
    name = prj.sanitize_project_name(name)
    if not name:
        raise ApiError(400, "Имя после очистки пустое — допустимы латинские "
                             "буквы, цифры, точки, '_' и '-'")
    ok, res = prj.create_project(root, section, name)
    if not ok:
        raise ApiError(400, str(res))
    pdir = Path(res)
    tpl = (body.get("template") or "").strip() or None
    copied: list[str] = []
    if tpl:
        tpl_dir = repo / "templates" / tpl
        if not tpl_dir.is_dir():
            raise ApiError(400, f"Шаблон не найден: templates/{tpl}")
        prj.write_project_metadata(
            pdir, tpl_dir,
            title=(body.get("title") or "").strip(),
            author=(body.get("author") or "").strip(),
            genres=([g.strip() for g in (body.get("genres") or "").split(",")
                     if g.strip()] or None),
        )
        copied = prj.fill_project_from_template(pdir, tpl_dir)
    _invalidate_stats_entry(root, section, name)
    return {"ok": True, "section": section, "name": name,
            "renamed": name != raw_name, "copied": copied}


def _projects_move(ctx: dict) -> dict:
    """Перенос проекта в другой раздел.

    `pack: true` — упаковать книгу в новом разделе (клиент спрашивает об этом,
    когда раздел архивный); без него книга остаётся каталогом.
    """
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    body = ctx["body"]
    section = (body.get("section") or "").strip()
    name = (body.get("name") or "").strip()
    dst = (body.get("dst") or "").strip()
    ok, res = prj.move_project(root, section, name, dst)
    if not ok:
        raise ApiError(400, str(res))
    moved = [{"section": section, "name": name, "dst": dst, "path": str(res)}]
    # журнал следует за проектом: логи запусков остаются видны на новой
    # вкладке (иначе после переноса в другой раздел история «пропала»)
    _job_manager(ctx).update_project_path(
        f"{section}/{name}", f"{dst}/{Path(res).name}", Path(res))
    payload = {"ok": True, "section": dst, "name": Path(res).name}
    pack_job = _pack_after_move(ctx, root, dst, moved)
    if pack_job is not None:
        payload["pack"] = pack_job
    return payload


def _pack_after_move(ctx: dict, root: Path, dst: str,
                     moved: list[dict]) -> dict | None:
    """Упаковать перенесённые книги — только по явному согласию.

    `moved` — результат `move_project(s)`. Пачка не спрашивает повторно:
    человек уже ответил один раз, а частичная упаковка половины была бы
    худшим из ответов. Ответ «оставить каталогами» — `pack: false`.
    """
    body = ctx["body"]
    if not body.get("pack"):
        return None
    todo = [(str(m["dst"]), Path(m["path"]).name) for m in moved
            if Path(m["path"]).is_dir()]
    if not todo:
        return None
    return _archive_job(ctx, root, "pack", todo, [])


def _projects_move_many(ctx: dict) -> dict:
    """Перенос пачки проектов в другой раздел (POST /api/projects/move-many).

    `{items: [{section, name}], dst}`. Имена целевого раздела проверяются
    один раз; каждый проект едет сам за себя: занятое имя или чужое
    состояние уезжает в `skipped` с причиной, остальные переносятся.
    Журналы запусков переезжают вместе с книгами.
    """
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    body = ctx["body"]
    dst = (body.get("dst") or "").strip()
    if not dst:
        raise ApiError(400, "Не указан раздел назначения")
    if dst not in prj.load_sections(root):
        raise ApiError(404, f"Раздел не найден: {dst}")
    by_section: dict[str, list[str]] = {}
    for it in body.get("items") or []:
        if not isinstance(it, dict):
            continue
        sec = str(it.get("section") or "").strip()
        name = str(it.get("name") or "").strip()
        if sec and name and sec != dst:
            by_section.setdefault(sec, []).append(name)
    if not by_section:
        raise ApiError(400, "Не выбрано ни одного проекта (или все уже в целевом разделе)")
    jm = _job_manager(ctx)
    moved: list[dict] = []
    skipped: list[dict] = []
    for sec, names in by_section.items():
        if sec not in prj.load_sections(root):
            skipped.extend({"section": sec, "name": n,
                            "reason": f"Раздел не найден: {sec}"}
                           for n in names)
            continue
        res = prj.move_projects(root, sec, names, dst)
        moved.extend(res["moved"])
        skipped.extend(res["skipped"])
    for m in moved:
        jm.update_project_path(f"{m['section']}/{m['name']}",
                               f"{dst}/{Path(m['path']).name}",
                               Path(m["path"]))
    pack_job = _pack_after_move(ctx, root, dst, moved)
    payload = {"ok": True, "dst": dst, "moved": moved, "skipped": skipped}
    if pack_job is not None:
        payload["pack"] = pack_job
    return payload


def _projects_rename(ctx: dict) -> dict:
    """Переименование проекта внутри раздела."""
    prj = _import_projects(ctx)
    body = ctx["body"]
    section = (body.get("section") or "").strip()
    name = (body.get("name") or "").strip()
    new_name = (body.get("new_name") or "").strip()
    ok, res = prj.rename_project(_projects_root(ctx), section, name, new_name)
    if not ok:
        _invalidate_stats_entry(_projects_root(ctx), section, name)
    # журнал следует за проектом: логи запусков остаются видны на новой
    # вкладке (иначе после переноса в другой раздел история «пропала»)
    _job_manager(ctx).update_project_path(
        f"{section}/{name}", f"{section}/{Path(res).name}", Path(res))
    return {"ok": True, "section": section, "name": Path(res).name}


def _projects_copy(ctx: dict) -> dict:
    """Дублирование проекта внутри раздела."""
    prj = _import_projects(ctx)
    body = ctx["body"]
    section = (body.get("section") or "").strip()
    name = (body.get("name") or "").strip()
    new_name = (body.get("new_name") or "").strip()
    ok, res = prj.copy_project(_projects_root(ctx), section, name, new_name)
    if not ok:
        raise ApiError(400, str(res))
    _invalidate_stats_entry(_projects_root(ctx), section, new_name)
    return {"ok": True, "section": section, "name": Path(res).name}


def _projects_delete(ctx: dict) -> dict:
    """Удаление проекта (требует confirm: 'УДАЛИТЬ')."""
    prj = _import_projects(ctx)
    body = ctx["body"]
    section = (body.get("section") or "").strip()
    name = (body.get("name") or "").strip()
    _check_confirm(ctx, "УДАЛИТЬ")
    ok, res = prj.delete_project(_projects_root(ctx), section, name)
    if not ok:
        raise ApiError(400, str(res))
    _invalidate_stats_entry(_projects_root(ctx), section, name)
    return {"ok": True, "section": section, "name": name}


def _section_props_set(ctx: dict) -> dict:
    """Свойства раздела: архивность и «упаковывать при переносе».

    POST /api/sections/{name}/props — `{archive?, pack_on_move?}`. Свойство
    `archive` не запрещает хранить в разделе папки: оно говорит интерфейсу,
    что раздел предназначен для закрытых книг, и включает массовое
    «Архивировать». Молчаливой упаковки нет — даже при `pack_on_move`
    SPA спрашивает подтверждение.
    """
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    name = ctx["params"]["name"]
    body = ctx["body"]
    wanted = {k: body[k] for k in ("archive", "pack_on_move") if k in body}
    if not wanted:
        raise ApiError(400, "Нужно хотя бы одно свойство: archive, pack_on_move")
    ok, res = prj.set_section_props(root, name, **wanted)
    if not ok:
        raise ApiError(404 if "не найден" in str(res) else 400, str(res))
    return {"ok": True, "name": res,
            "props": prj.section_props(root, str(res))}


def _project_storage(ctx: dict, section: str, name: str):
    """Как книга лежит на диске: `pack` (папкой) | `unpack` (архивом) | None.

    Имена — вид операции, которой книга ещё НЕ потребует: лежащую папкой
    надо упаковать, лежащую архивом — распаковать. Одна точка правды и для
    пачки по явному списку, и для проверок карточки.
    """
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    if prj.project_dir(root, section, name).is_dir():
        return "pack"
    if core_archive.archive_path(root / section, name).is_file():
        return "unpack"
    return None


def _projects_archive(ctx: dict) -> dict:
    """Массовая архивация/разархивация: `{items, op}` → чтó сделано.

    `items` — `[{section, name}, …]`; `op` — `pack` (папка → `.nmz`) или
    `unpack` (обратно). Работа идёт одним запросом и одним запуском (job):
    ответ возвращается сразу после создания запуска — прогресс и лог
    смотрит обычная панель запусков. Отказы по отдельным проектам
    (занят запуском, архив уже есть, не найден) не рушат пачку: они едут
    в `skipped`, чтобы человек видел причину для каждой книги.
    """
    root = _projects_root(ctx)
    body = ctx["body"]
    op = (body.get("op") or "pack").strip()
    if op not in ("pack", "unpack"):
        raise ApiError(400, f"Неизвестная операция: {op}")
    raw = body.get("items") or []
    if not isinstance(raw, list) or not raw:
        raise ApiError(400, "Список items пуст")
    jm = _job_manager(ctx)
    todo: list[tuple[str, str]] = []
    skipped: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        section = str(item.get("section") or "").strip()
        name = str(item.get("name") or "").strip()
        if not section or not name:
            skipped.append({"section": section, "name": name,
                            "reason": "не указаны раздел и имя"})
            continue
        busy = jm.running_on(f"{section}/{name}")
        if busy is not None:
            skipped.append({"section": section, "name": name,
                            "reason": f"идёт запуск «{busy.title}»"})
            continue
        # Вид операции обязан совпадать с тем, как книга лежит на диске:
        # «Выделить все» отмечает весь раздел, и пачка «Распаковать» тащила
        # живые папки в скрипт, который на каждой отвечал «уже распакован» —
        # запуск завершался «готово: 1 из 3». Смотрим диск, а не ответ
        # клиента: карточка могла устареть.
        storage = _project_storage(ctx, section, name)
        if storage is None:
            skipped.append({"section": section, "name": name,
                            "reason": "книга не найдена"})
            continue
        if storage != op:
            skipped.append({"section": section, "name": name,
                            "reason": ("уже упакована" if op == "pack"
                                       else "уже распакована")})
            continue
        todo.append((section, name))
    if not todo:
        return {"ok": True, "items": [], "skipped": skipped,
                "job": None}
    return _archive_job(ctx, root, op, todo, skipped)


def _archive_job(ctx: dict, root: Path, op: str,
                 todo: list[tuple[str, str]], skipped: list[dict]) -> dict:
    """Запуск упаковки/распаковки пачки — один job с логом и прогрессом.

    Архивация читает весь проект и пишет десятки мегабайт — это работа, а не
    мгновенная операция: один запуск на пачку, как у стадий, с логом и
    прогрессом в общей панели запусков.
    """
    title = ("Архивация проектов" if op == "pack"
             else "Разархивация проектов")
    project = (f"{todo[0][0]}/{todo[0][1]}" if len(todo) == 1
               else "archive")
    script = _repo_root(ctx) / "cli" / "archive.py"
    if not script.is_file():
        raise ApiError(500, f"Скрипт не найден: {script}")
    argv = [str(script), op, *[f"{s}/{n}" for s, n in todo],
            "--projects-dir", str(root)]
    if op == "pack":
        argv.append("--gc-history")
    job = _job_manager(ctx).start("archive", title, project, argv,
                                  _repo_root(ctx))
    return {"ok": True, "items": [{"section": s, "name": n}
                                    for s, n in todo],
            "skipped": skipped, "job": _job_payload(job)}


def _section_archive_all(ctx: dict) -> dict:
    """Раздел целиком: упаковать или распаковать все его проекты.

    POST /api/sections/{name}/archive — `{op}`. Список книг считает сервер
    (`archive_candidates`), а не клиент: у большого раздела карточки грузятся
    в фоне, и пачка по ним сначала молча упаковала бы половину. Пустóй
    раздел и «всё уже в нужном виде» отвечают без запуска — `job: null`.
    """
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    section = ctx["params"]["name"]
    op = (ctx["body"].get("op") or "pack").strip()
    if op not in ("pack", "unpack"):
        raise ApiError(400, f"Неизвестная операция: {op}")
    if section not in prj.load_sections(root):
        raise ApiError(404, f"Раздел не найден: {section}")
    plan = prj.archive_candidates(root, section, op)
    jm = _job_manager(ctx)
    todo: list[tuple[str, str]] = []
    skipped: list[dict] = []
    for name in plan["todo"]:
        busy = jm.running_on(f"{section}/{name}")
        if busy is not None:
            skipped.append({"section": section, "name": name,
                            "reason": f"идёт запуск «{busy.title}»"})
            continue
        todo.append((section, name))
    payload = ({"ok": True, "items": [], "skipped": skipped, "job": None}
               if not todo else _archive_job(ctx, root, op, todo, skipped))
    payload.update({"section": section, "op": op,
                    "total": plan["total"], "done": plan["done"]})
    return payload


def _project_archive(ctx: dict) -> dict:
    """Манифест архива проекта (GET …/archive).

    Отсутствие архива — это ответ, а не ошибка: у живой книги архива нет,
    и клиент спрашивает об этом у каждой открытой книги. 404 на штатный
    вопрос засорял бы журнал и маскировал настоящие отказы.
    """
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    section = ctx["params"]["sec"]
    name = ctx["params"]["name"]
    pdir = prj.project_dir(root, section, name)
    item = _find_archive(prj, root, section, name)
    if item is None:
        if not pdir.is_dir():
            raise ApiError(404, f"Проект не найден: {section}/{name}")
        return {"ok": True, "section": section, "name": name,
                "archived": False, "size": 0, "path": "",
                "manifest": None}
    man = core_archive.archive_manifest(item["path"])
    return {"ok": True, "section": section, "name": name,
            "archived": not pdir.is_dir(), "size": item["size"],
            "path": f"{section}/{prj.ARCHIVE_DIR}/{Path(item['path']).name}",
            "manifest": man}


def _project_archive_download(ctx: dict) -> dict:
    """Скачать архив проекта (GET …/archive/download) — обычный `.nmz`.

    Отдаётся тем же приёмом, что и файлы: имя с кавычками кодируется
    (`quote`), иначе CJK-имена ломали бы заголовок.
    """
    from urllib.parse import quote
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    section = ctx["params"]["sec"]
    name = ctx["params"]["name"]
    item = _find_archive(prj, root, section, name)
    if item is None:
        raise ApiError(404, f"Архив не найден: {section}/{name}")
    path = Path(item["path"])
    handler = ctx["handler"]
    handler._send(200, "application/octet-stream", path.read_bytes(),
                  [("Content-Disposition",
                    f'attachment; filename="{quote(path.name)}"')])
    return {}


def _find_archive(prj, root: Path, section: str, name: str) -> dict | None:
    """Архив проекта в разделе (по имени файла, без чтения содержимого)."""
    for item in prj.list_archives(root / section):
        if item["name"] == prj.project_dir(root, section, name).name:
            return item
    return None


def _job_payload(job) -> dict:
    """Публичное представление запуска (метаданные + буфер)."""
    return job.payload()


def _project_status(ctx: dict) -> dict:
    """GET /api/projects/{sec}/{name}/status — таблица готовности глав."""
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    _pdir, sec, name = _project_path(ctx)
    return {"ok": True, "status": _cached_status(prj, root, sec, name)}


def _ui_state_get(ctx: dict) -> dict:
    """GET …/ui-state — состояние книги.

    Состояние книги — её свойство, а не свойство устройства: ответ один для
    всех браузеров, доменов и компьютеров, поэтому форма запусков показывает
    одинаковые поля везде. Файла ещё нет — отдаётся пустое состояние.
    """
    pdir, section, name = _project_path(ctx)
    handler = ctx.get("handler")
    if handler is not None:
        handler.drop_legacy_device_cookie()
    ddir = core_uistate.ui_state_dir(pdir)
    return {"ok": True, "section": section, "name": name,
            "data": core_uistate.ui_state_read(ddir),
            "dir": str(core_uistate.UI_STATE_DIR).replace("\\", "/")}


def _ui_state_put(ctx: dict) -> dict:
    """PUT …/ui-state ({data}) — записать изменённые ключи книги.

    Тело — только изменённые клиентом ключи; сервер накладывает их на файл
    книги, сливая вложенные объекты по полям (правка одного поля формы не
    стирает соседние, записанные другим устройством). Пустой `data` — 400:
    очистка состояния делается удалением каталога, а не «записью ничего»,
    иначе один пустой PUT от старого клиента стирал бы книгу целиком.
    """
    if not isinstance(ctx["body"].get("data", {}), dict):
        raise ApiError(400, "data должен быть JSON-объектом")
    pdir, section, name = _project_path(ctx)
    try:
        data = core_uistate.ui_state_write(
            core_uistate.ui_state_dir(pdir), ctx["body"].get("data") or {})
    except (OSError, ValueError) as exc:
        log.warning("ui-state не записан (%s/%s): %s", section, name, exc)
        raise ApiError(500, f"Не удалось сохранить состояние: {exc}")
    return {"ok": True, "section": section, "name": name, "data": data}


def _project_stats(ctx: dict) -> dict:
    """Краткая статистика проекта (строка) + структура папок.

    stats — кеш по сигнатуре (без TTL): страница «Проекты» раньше
    делала запрос на каждую карточку, каждый запрос = полный обход
    chapters/. skeleton дёшев (iterdir верхнего уровня) — не кешируется.
    """
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    pdir, section, name = _project_path(ctx)
    stats = _cached_stats(prj, root, section, name)
    return {"ok": True, "section": section, "name": name,
            "stats": stats,
            "skeleton": [p.name for p in sorted(pdir.iterdir())
                          if p.is_dir()]}


def _project_tree(ctx: dict) -> dict:
    """Дерево глав: номер, папка, артефакты с размерами в СИМВОЛАХ
    (не байты: клиент считает бюджет запроса translate_quality в
    символах; байты кириллицы/CJK дают расхождение до ~3×).

    помимо канонических имён — легаси-суффиксы старых проектов:
    ``*_translated.txt``, ``*_redacted.txt``, ``*_polished.txt``
    (вкладка «Редактор» видит ключевые файлы независимо от маски)."""
    common = _import_common(ctx)
    pdir, section, name = _project_path(ctx)
    chapters_dir = pdir / "chapters"
    if not chapters_dir.is_dir():
        return {"ok": True, "section": section, "name": name,
                "chapters": []}
    chapter_map = common.build_chapter_map(chapters_dir)
    artifacts = ("chapter.txt", "translated.txt", "translated_trace.json",
                 "redacted.txt", "polished.txt")
    legacy_sfx = ("_translated.txt", "_redacted.txt", "_polished.txt")
    def _char_size(f: Path) -> int:
        # utf-8 почти всегда (наши артефакты); ошибки — длина байтов
        try:
            return len(f.read_bytes().decode("utf-8"))
        except UnicodeDecodeError:
            return f.stat().st_size

    items = []
    for num in sorted(chapter_map):
        for dir_str in chapter_map[num]:
            d = Path(dir_str)
            entry = {"id": num, "dir": d.name, "artifacts": {}}
            for art in artifacts:
                f = d / art
                if f.is_file():
                    entry["artifacts"][art] = _char_size(f)
            try:
                entries = list(d.iterdir())
            except OSError:
                entries = []
            for f in entries:
                if f.is_file() and f.name.endswith(legacy_sfx):
                    entry["artifacts"][f.name] = _char_size(f)
            items.append(entry)
    return {"ok": True, "section": section, "name": name,
            "chapters": items}


def _chapter_titles_get(ctx: dict) -> dict:
    """Названия глав (GET /api/projects/{s}/{n}/chapters/titles).

    type=polished|redacted|translated|chapter — тип файлов глав;
    названия — первая непустая строка файла (read_chapter_titles).
    """
    common = _import_common(ctx)
    pdir, section, name = _project_path(ctx)
    want = ctx["query"].get("type", "polished")
    if want not in ("chapter", "translated", "redacted", "polished"):
        raise ApiError(400, f"Недопустимый тип: {want}")
    chapters_dir = pdir / "chapters"
    if not chapters_dir.is_dir():
        return {"ok": True, "section": section, "name": name,
                "type": want, "titles": {}}
    titles = common.read_chapter_titles(chapters_dir, want=want)
    # all_ids — непрерывный диапазон 1..N по существующим ПАПКАМ глав
    # (build_chapter_map), а не по titles: файлов типа может не быть вовсе;
    # missing — номера без файла нужного типа (для серых ячеек)
    try:
        ids = sorted({int(k) for k in common.build_chapter_map(chapters_dir)})
    except (ValueError, TypeError):
        ids = []
    all_ids = list(range(1, ids[-1] + 1)) if ids else []
    missing = [n for n in all_ids if n not in titles]
    return {"ok": True, "section": section, "name": name,
            "type": want, "titles": titles,
            "all_ids": all_ids, "missing": missing}


def _chapters_delete(ctx: dict) -> dict:
    """Удалить файлы глав (DELETE …/chapters).

    query: type=polished|redacted|translated|chapter + start/end
    (диапазон номеров, включительно). Возвращает удалённые номера.
    """
    common = _import_common(ctx)
    pdir, section, name = _project_path(ctx)
    q = ctx["query"]
    want = q.get("type", "polished")
    if want not in ("chapter", "translated", "redacted", "polished"):
        raise ApiError(400, f"Недопустимый тип: {want}")
    try:
        start = int(q.get("start", "")) if q.get("start") else None
        end = int(q.get("end", "")) if q.get("end") else None
    except ValueError:
        raise ApiError(400, "start/end должны быть числами")
    chapters_dir = pdir / "chapters"
    if not chapters_dir.is_dir():
        return {"ok": True, "section": section, "name": name,
                "type": want, "deleted": []}
    ch_map = common.build_chapter_map(chapters_dir)
    deleted = []
    for num in sorted(ch_map):
        if start is not None and num < start:
            continue
        if end is not None and num > end:
            continue
        dirs = ch_map[num]
        if not dirs:
            continue
        f, _msgs = common.find_chapter_file(dirs[-1], num, want=want,
                                            strict=True)
        if f:
            try:
                os.unlink(f)
                deleted.append(num)
            except OSError:
                pass
    return {"ok": True, "section": section, "name": name,
            "type": want, "deleted": deleted}


def _chapter_titles_put(ctx: dict) -> dict:
    """Сохранить названия глав (PUT …/chapters/titles).

    body: {type, titles: {номер: строка}} — каждая строка заменяет
    первую непустую строку соответствующего файла главы
    (write_chapter_titles, NFC).
    """
    common = _import_common(ctx)
    pdir, section, name = _project_path(ctx)
    body = ctx["body"] or {}
    want = str(body.get("type") or "polished")
    if want not in ("chapter", "translated", "redacted", "polished"):
        raise ApiError(400, f"Недопустимый тип: {want}")
    raw = body.get("titles") or {}
    if not isinstance(raw, dict):
        raise ApiError(400, "titles должен быть объектом {номер: строка}")
    titles: dict[int, str] = {}
    for k, v in raw.items():
        try:
            titles[int(k)] = str(v)
        except (TypeError, ValueError):
            raise ApiError(400, f"Недопустимый номер главы: {k!r}")
    chapters_dir = pdir / "chapters"
    if not chapters_dir.is_dir():
        raise ApiError(404, "Папка chapters/ не найдена")
    result = common.write_chapter_titles(chapters_dir, want, titles)
    return {"ok": True, "section": section, "name": name,
            "type": want, "updated": result["updated"],
            "missing": result["missing"],
            "warnings": result["warnings"]}

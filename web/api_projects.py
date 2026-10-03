#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_projects.py — пульт и проекты: разделы, список, создание, перенос,
переименование, копирование, удаление, статусы, дерево и названия глав.
"""
from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from web.jobs import JobManager
from web.server import ApiError, Router
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
    router.add("POST", "/api/projects/rename", _projects_rename)
    router.add("POST", "/api/projects/copy", _projects_copy)
    router.add("DELETE", "/api/projects", _projects_delete)
    router.add("GET", "/api/projects/{sec}/{name}/stats", _project_stats)
    router.add("GET", "/api/projects/{sec}/{name}/status", _project_status)
    router.add("GET", "/api/projects/{sec}/{name}/tree", _project_tree)
    router.add("GET", "/api/projects/{sec}/{name}/chapters/titles",
               _chapter_titles_get)
    router.add("PUT", "/api/projects/{sec}/{name}/chapters/titles",
               _chapter_titles_put)
    router.add("DELETE", "/api/projects/{sec}/{name}/chapters",
               _chapters_delete)
    router.add("GET", "/api/templates", _templates)


def _state(ctx: dict) -> dict:
    """hub_state — общий с cli-пультом (последний раздел/проект)."""
    hub = st.load_hub_state(_projects_root(ctx))
    return {"ok": True, **hub}


def _stats_signature(pdir: Path) -> str:
    """Дешёвый отпечаток состояния проекта для кеша stats.

    Содержит mtime папок глав (создание/удаление файлов меняет mtime
    каталога), mtime ner.json/wiki.md и mtime папок compiled-кандидатов.
    Это scandir 1 уровня, а НЕ полный обход глав — поэтому проверка
    сигнатуры на порядки дешевле самого project_stats().
    """
    pdir = Path(pdir)
    parts: list[str] = []
    ch = pdir / "chapters"
    try:
        subs = sorted(
            (d.name, d.stat().st_mtime_ns)
            for d in ch.iterdir() if d.is_dir()
        )
    except OSError:
        subs = []
    parts.append(f"ch:{len(subs)}")
    parts.extend(f"{n}:{m}" for n, m in subs)
    for f in ("ner.json", "wiki.md"):
        p = pdir / f
        try:
            parts.append(f"{f}:{p.stat().st_mtime_ns}")
        except OSError:
            parts.append(f"{f}:0")
    for d in (pdir, pdir / "tmp", pdir / "output"):
        try:
            parts.append(f"d:{d.name}:{d.stat().st_mtime_ns}")
        except OSError:
            parts.append(f"d:{d.name}:0")
    return "|".join(parts)


def _stats_cache_path(root: Path) -> Path:
    """Файл дискового кеша stats (в корне projects/, рядом с hub_state)."""
    return root / _STATS_CACHE_FILE


def _stats_cache_key(root: Path, sec: str, name: str) -> str:
    """Ключ кеша с пространством имён корня projects/ (L5, AUDIT):
    два сервера на разных корнях не смешивают записи."""
    return f"{root}::{sec}/{name}"


def _load_stats_cache(root: Path) -> None:
    """Загрузка дискового кеша stats (переживает рестарт сервера)."""
    try:
        import json as _json
        data = _json.loads(_stats_cache_path(root).read_text(
            encoding="utf-8"))
        if isinstance(data, dict):
            pfx = f"{root}::"
            _STATS_CACHE.update(
                {pfx + k: v for k, v in data.items()
                 if isinstance(v, dict) and "sig" in v
                 and ("stats" in v or "status" in v)})
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

    Кеш в памяти + на диске; актуальность — сигнатура mtime, а не время.
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


def _cached_status(prj, root: Path, sec: str, name: str) -> dict:
    """Таблица готовности глав: кеш по сигнатуре (как stats).

    Ключ с версией методики (_STATUS_CACHE_VER): правка расчёта
    (напр. счётчик статей rulate-wiki) инвалидирует старые записи —
    сигнатура mtime wiki.md их не ловит.
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
    """Обход всех разделов/проектов; stats — из кеша по сигнатуре."""
    sections = []
    total = 0
    for sec in prj.load_sections(root):
        names = prj.list_projects(root, sec)
        total += len(names)
        items = []
        for n in names:
            stats = _cached_stats(prj, root, sec, n)
            items.append({"name": n, "stats": stats})
        sections.append({"name": sec, "projects": items})
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
    return {"ok": True, "sections": [
        {"name": s, "count": len(prj.list_projects(root, s))}
        for s in prj.load_sections(root)
    ]}


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
    """Имена проектов раздела (GET /api/projects?section=)."""
    prj = _import_projects(ctx)
    section = ctx["query"].get("section", "")
    if section not in prj.load_sections(_projects_root(ctx)):
        raise ApiError(400, f"Неизвестный раздел: {section!r}")
    names = prj.list_projects(_projects_root(ctx), section)
    return {"ok": True, "section": section, "projects": names}


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
    """Перенос проекта в другой раздел."""
    prj = _import_projects(ctx)
    body = ctx["body"]
    section = (body.get("section") or "").strip()
    name = (body.get("name") or "").strip()
    dst = (body.get("dst") or "").strip()
    ok, res = prj.move_project(_projects_root(ctx), section, name, dst)
    if not ok:
        raise ApiError(400, str(res))
    # старый ключ (root::раздел/имя) устарел; новый ещё не закеширован
    _invalidate_stats_entry(_projects_root(ctx), section, name)
    # журнал следует за проектом: логи запусков остаются видны на новой
    # вкладке (иначе после переноса в другой раздел история «пропала»)
    _job_manager(ctx).update_project_path(
        f"{section}/{name}", f"{dst}/{Path(res).name}", Path(res))
    return {"ok": True, "section": dst, "name": Path(res).name}


def _projects_rename(ctx: dict) -> dict:
    """Переименование проекта внутри раздела."""
    prj = _import_projects(ctx)
    body = ctx["body"]
    section = (body.get("section") or "").strip()
    name = (body.get("name") or "").strip()
    new_name = (body.get("new_name") or "").strip()
    ok, res = prj.rename_project(_projects_root(ctx), section, name, new_name)
    if not ok:
        raise ApiError(400, str(res))
    _invalidate_stats_entry(_projects_root(ctx), section, name)
    # журнал следует за проектом (как при move)
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


def _project_status(ctx: dict) -> dict:
    """GET /api/projects/{sec}/{name}/status — таблица готовности глав."""
    prj = _import_projects(ctx)
    root = _projects_root(ctx)
    _pdir, sec, name = _project_path(ctx)
    return {"ok": True, "status": _cached_status(prj, root, sec, name)}


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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_glossary.py — глоссарий и его проверки: ner.json, экспорт,
review-файлы ner_check и translate_check_llm и их применение.
"""
from __future__ import annotations

import json
import logging
import threading
import unicodedata
from pathlib import Path
from web.jobs import JobManager
from web.server import ApiError, Router
from web.api_common import log, NER_TEXT_LIMIT
from web.api_assets import (
    _cover_delete,
    _cover_get,
    _cover_put,
    _notes_get,
    _notes_put,
)
from web.api_common import _import_common, _project_ctx
from web.api_env import (
    _metadata_get,
    _metadata_put,
    _prompts_delete,
    _prompts_get,
    _prompts_list,
    _prompts_put,
    _prompts_copy_set,
    _prompts_template,
)
from web.api_stage import _jobs_start

#: запись глоссария — read-modify-write: PATCH и PUT держат один замок,
#: иначе параллельные правки теряют записи друг друга
_NER_LOCK = threading.Lock()


def _ner_get(ctx: dict) -> dict:
    """Глоссарий (GET /api/ner?project=): total + by_type + items.

    Файл > 10 МБ — отдаём флаг too_large (скачивание файлом)."""
    pdir, _section, _name = _project_ctx(ctx)
    ner = pdir / "ner.json"
    if not ner.is_file():
        return {"ok": True, "exists": False, "total": 0,
                "by_type": {}, "items": []}
    size = ner.stat().st_size
    if size > NER_TEXT_LIMIT:
        return {"ok": True, "exists": True, "too_large": True, "size": size}
    try:
        items = json.loads(ner.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        raise ApiError(500, f"ner.json не читается: {exc}")
    if not isinstance(items, list):
        raise ApiError(500, "ner.json: ожидался список терминов")
    common = _import_common(ctx)
    by_type: dict[str, int] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        # ключ счётчика — строка: type бывает числом или объектом, и
        # нехэшируемое значение роняло GET /api/ner в 500 (вкладка
        # «Глоссарий» не открывалась)
        t = common.ner_text(it.get("type")).strip() or "?"
        by_type[t] = by_type.get(t, 0) + 1
    return {"ok": True, "exists": True, "total": len(items),
            "by_type": by_type, "items": items}


def _ner_put(ctx: dict) -> dict:
    """Сохранить глоссарий (PUT /api/ner {project, items})."""
    common = _import_common(ctx)
    pdir, _section, _name = _project_ctx(ctx)
    items = ctx["body"].get("items")
    if not isinstance(items, list):
        raise ApiError(400, "Поле items: список терминов")
    # aliases — список в любой записи: старые файлы знают и пустую строку
    # (очистка ячейки «Глоссария» старым клиентом), и одиночное имя —
    # нормализуем при записи, чтобы load_ner_data не спотыкался на типах
    normalized = []
    for it in items:
        if isinstance(it, dict):
            it = common.ner_record(dict(it))
            if it is None:
                continue  # запись без терминa — мусор, а не термин
        normalized.append(it)
    text = json.dumps(normalized, ensure_ascii=False, indent=2)
    # тот же замок, что у PATCH: иначе полное сохранение затирало бы
    # точечную правку, пришедшую между чтением и записью
    with _NER_LOCK:
        common.atomic_write(pdir / "ner.json",
                            unicodedata.normalize("NFC", text))
    return {"ok": True, "total": len(normalized)}


def _ner_canon(item: dict) -> str:
    """Канонический вид записи — для адресации дублей термина.

    Термин не уникален: в ner.json бывает несколько записей одного слова с
    разными значениями (слияние алиас-групп, разные прогоны). Правка «по
    терму» задевала ВСЕ такие записи — замок одного дубля лочил все, а
    удаление одной строки сносило их разом.

    Клиент присылает эту же строку (`canon`, UICore.nerCanon) и сервер
    сравнивает её, а не хеш: алгоритм не нужно дублировать на двух языках,
    и расхождение дайджестов не может молча превратить правку в «не
    нашли». Служебные поля (`_locked`, `__new`, `_fp*`) в канон не входят:
    замок и переименование не должны менять адрес записи.
    """
    body = {k: v for k, v in item.items()
            if k not in ("_locked", "__new") and not k.startswith("_fp")}
    text = json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return unicodedata.normalize("NFC", text)


def _ner_targets(items: list, ch: dict, term_of) -> list:
    """Записи, которые правка задевает: терм + канон (если передан).

    Без канона адресация прежняя (все записи термина) — так работают
    клиенты, которые его ещё не шлют: старый SPA не должен терять правки.
    С каноном — ровно одна запись; если записей-побайтовых дублей несколько,
    `order` (номер совпадения в файле) различает их.

    Канон не нашёлся — правка НЕ применяется и `order` не используется:
    вкладка открывалась на прежнем файле, запись с тех пор изменили или
    удалили, и `order`-й кандидат — уже ДРУГАЯ запись. Именно так замок,
    адресованный одному дублю, ложился на соседний, а правка перевода
    писала чужое слово: клиент видел успех и показывал локальную правку.
    Возврат пустого списка безопасен — остаётся `stale`, и клиент
    перечитывает глоссарий.
    """
    term = unicodedata.normalize("NFC", str(ch.get("term", ""))).strip()
    if not term:
        return []
    cands = [it for it in items if term_of(it) == term]
    canon = ch.get("canon")
    if isinstance(canon, str) and canon:
        want = unicodedata.normalize("NFC", canon)
        same = [it for it in cands if _ner_canon(it) == want]
        if not same:
            return []
        cands = same
    order = _to_int_or_none(ch.get("order"))
    if order is not None:
        # `order` — часть адреса: он пришёл вместе с каноном и описывает
        # ТОТ ЖЕ набор. Вне диапазона — набор изменился (запись удалили в
        # другом окне), и брать «все совпадения» нельзя: удаление одной
        # строки сносило все, `changed: 3` и пустой файл вместо трёх
        # записей.
        if not (0 <= order < len(cands)):
            return []
        return [cands[order]]
    if len(cands) > 1:
        # ни канона, ни номера: адрес неоднозначен. `delete` в такой
        # ситуации опаснее пропуска — молча ничего не делаем
        if ch.get("delete"):
            return []
    return cands


def _to_int_or_none(v):
    """Целое из JSON-числа или строки; иначе None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _ner_patch(ctx: dict) -> dict:
    """Точечная правка глоссария (PATCH /api/ner {project, changes}).

    Каждое изменение — `{term, set, canon?, order?}` (поля записи) либо
    `{term, delete: true, canon?, order?}`. Клиент присылает его на
    микродействие — замок термина, правку ячейки, удаление строки, — а не
    весь список: у книги на 10978 терминов PUT с полным `items` весил 6 МБ
    и занимал 4 с на одну снятую галочку.

    Адресация — терм + необязательный `canon` (см. `_ner_canon`): термин
    НЕ уникален, и правка «по терму» задевала соседние записи того же
    слова. Без канона правка ложится на все записи термина — прежнее
    поведение.

    Записи нормализуются `ner_record` (как в PUT): иначе `aliases: ""`,
    `count: "3"` и служебный `__new` попадали в файл мимо проверок, и
    глоссарий читался иначе, чем после полного сохранения.
    Возвращает `{ok, changed, total, stale}`: `stale` — правки, чей канон
    не нашёлся (вкладка устарела), их не применили и клиенту надо
    перечитать глоссарий."""
    common = _import_common(ctx)
    pdir, _section, _name = _project_ctx(ctx)
    changes = ctx["body"].get("changes")
    if not isinstance(changes, list) or not changes:
        raise ApiError(400, "Поле changes: непустой список изменений")
    ner = pdir / "ner.json"
    if not ner.is_file():
        raise ApiError(404, "ner.json не найден")
    # read-modify-write под общим замком: два одновременных PATCH иначе
    # читали бы один и тот же файл и второй затирал правки первого
    with _NER_LOCK:
        try:
            items = json.loads(ner.read_text(encoding="utf-8",
                                             errors="replace"))
        except (OSError, ValueError) as exc:
            raise ApiError(500, f"ner.json не читается: {exc}")
        if not isinstance(items, list):
            raise ApiError(500, "ner.json: ожидался список терминов")

        def term_of(it) -> str:
            if not isinstance(it, dict):
                return ""
            return unicodedata.normalize(
                "NFC", str(it.get("term", ""))).strip()

        changed = 0
        stale = 0
        for ch in changes:
            if not isinstance(ch, dict):
                continue
            targets = _ner_targets(items, ch, term_of)
            if not targets:
                # адрес не нашёлся ИЛИ канон устарел: различаем, чтобы
                # клиент не молчал о потерянной правке
                if ch.get("canon"):
                    stale += 1
                continue
            if ch.get("delete"):
                dead = {id(it) for it in targets}
                items = [it for it in items if id(it) not in dead]
                changed += len(targets)
                continue
            fields = ch.get("set")
            if not isinstance(fields, dict):
                continue
            for it in targets:
                for k, v in fields.items():
                    # __new — маркер «запись ещё не в файле» из формы SPA:
                    # в файл он попадать не должен
                    if k == "__new":
                        continue
                    if v is None:
                        it.pop(k, None)
                    else:
                        it[k] = v
                # та же нормализация, что у полного PUT: файл не должен
                # отличаться в зависимости от того, чем его правили
                clean = common.ner_record(dict(it))
                if clean is None:
                    continue
                it.clear()
                it.update(clean)
            changed += len(targets)

        text = json.dumps(items, ensure_ascii=False, indent=2)
        common.atomic_write(ner, unicodedata.normalize("NFC", text))
        total = len(items)
    return {"ok": True, "changed": changed, "total": total,
            "stale": stale}


def _ner_export(ctx: dict) -> dict:
    """Экспорт глоссария для анализа (GET /api/ner/export?project=&format=…).

    format=json  → полные записи JSON;
    format=text  → JSONL по записи на строку (format_ner_record);
    format=names → имена по полу (женские/мужские).
    Общие фильтры: count_threshold, types.
    Возвращает {ok, name, content} — фронт скачивает файлом.
    """
    common = _import_common(ctx)
    pdir, _section, _name = _project_ctx(ctx)
    ner = pdir / "ner.json"
    if not ner.is_file():
        raise ApiError(404, "ner.json не найден")
    try:
        items = json.loads(ner.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        raise ApiError(500, f"ner.json не читается: {exc}")
    if not isinstance(items, list):
        raise ApiError(500, "ner.json: ожидался список терминов")

    q = ctx["query"]
    fmt = q.get("format", "json")
    if fmt not in ("json", "text", "names"):
        raise ApiError(400, "format: json | text | names")

    def _int(name: str, default: int) -> int:
        """Целое из строки запроса: браузер шлёт число из input как есть
        («17.0», « 17 »), а не float-исключение в 400."""
        v = q.get(name)
        if v in (None, ""):
            return default
        try:
            return int(float(str(v).strip()))
        except (TypeError, ValueError):
            raise ApiError(400, f"{name}: ожидалось число")

    def _csv(name: str) -> list[str]:
        return [s.strip() for s in q.get(name, "").split(",") if s.strip()]

    threshold = _int("count_threshold", 0)
    types = []
    for t in _csv("types"):
        # по типу считается не только в файле, но и в фильтре: пустой type в
        # «Типах» глоссария подписан «?» (см. _ner_get) — строку «?» из фильтра
        # надо понимать так же, иначе выбранный «?» не находит НИ ОДНОЙ записи
        # и экспорт молча возвращал чужой набор
        if t == "?":
            t = ""
        types.append(t)
    # пустой тип — осмысленное значение («?» на чипсах), а filter_ner_items
    # пустое имя типа отбрасывает как «фильтра нет» — поэтому белый список
    # собирается здесь, а порог остаётся общему правилу
    filtered = [it for it in common.filter_ner_items(items, threshold)
                if not types or str(it.get("type", "")) in types]
    if not filtered:
        raise ApiError(400, "Нет записей, подходящих под критерии")

    if fmt == "json":
        content = json.dumps(filtered, ensure_ascii=False, indent=2) + "\n"
        return {"ok": True, "name": "ner_export.json", "content": content,
                "total": len(filtered)}
    if fmt == "text":
        # JSONL — по одной записи на строку (тот же формат, что в
        # промптах: format_ner_record)
        content = "\n".join(
            json.dumps(common.format_ner_record(item),
                        ensure_ascii=False)
            for item in filtered) + "\n"
        return {"ok": True, "name": "ner_analysis.jsonl",
                "content": content, "total": len(filtered)}
    # names: имена по полу
    female_types = _csv("female_types") or ["Person (female)"]
    male_types = _csv("male_types") or ["Person (male)"]
    female, male = [], []
    for item in filtered:
        t = item.get("type", "")
        tr = item.get("translation", "")
        if t in female_types:
            female.append(tr)
        elif t in male_types:
            male.append(tr)
    content = ("=== ЖЕНСКИЕ ИМЕНА ===\n"
               + ("\n".join(female) if female else "Нет данных")
               + "\n\n=== МУЖСКИЕ ИМЕНА ===\n"
               + ("\n".join(male) if male else "Нет данных") + "\n")
    return {"ok": True, "name": "ner_names.txt", "content": content,
            "total": len(filtered)}


def _review_file(ctx: dict, fname: str) -> Path:
    """Файл review внутри tmp/ проекта (tmp/ner_review.json /
    tmp/translate_check_llm_review.json — рабочие файлы)."""
    pdir, _section, _name = _project_ctx(ctx)
    return pdir / "tmp" / fname


def _review_get(ctx: dict, fname: str) -> dict:
    """Чтение review-файла: JSON pretty, отсутствующий — пустой."""
    p = _review_file(ctx, fname)
    if not p.is_file():
        return {"ok": True, "exists": False, "content": ""}
    text = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix == ".json":
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False,
                              indent=2)
        except (ValueError, TypeError):
            log.debug("review-файл невалиден, отдаём как есть: %s", fname)
    return {"ok": True, "exists": True, "content": text,
            "size": p.stat().st_size}


def _review_put(ctx: dict, fname: str) -> dict:
    """Запись review-файла (PUT, контент в body)."""
    common = _import_common(ctx)
    content = ctx["body"].get("content")
    if content is None:
        raise ApiError(400, "Поле content обязательно")
    p = _review_file(ctx, fname)
    common.atomic_write(p, unicodedata.normalize("NFC", str(content)))
    return {"ok": True, "exists": True}


def _review_apply(ctx: dict, action: str) -> dict:
    """Применение review-правок: запуск стадии n/5 с --apply.

    POST /api/{ner|translate_check_llm}/review/apply
    {project, dry_run?} → JobManager
    (subprocess ner_check.py/translate_check_llm.py --apply
    [--dry-run])."""
    body = ctx["body"]
    project = (body.get("project") or "").strip()
    if "/" not in project:
        raise ApiError(400, "Параметр project=sec/name обязателен")
    dry = bool(body.get("dry_run", False))
    params: dict = {"apply": True, "dry_run": dry}
    if action == "translate_check_llm":
        params["type"] = body.get("type") or "polished"
    ctx["body"] = {"action": action, "project": project, "params": params}
    # маркер: флаги применения собираются только этим путём («Проверка»)
    ctx["review_apply"] = True
    return _jobs_start(ctx)


def _ner_review_apply(ctx: dict) -> dict:
    """POST /api/ner/review/apply → ner_check.py --apply."""
    return _review_apply(ctx, "ner_check")


def _tcl_review_apply(ctx: dict) -> dict:
    """POST /api/translate_check_llm/review/apply →
    translate_check_llm.py --apply."""
    return _review_apply(ctx, "translate_check_llm")


def _register_glossary(router: Router) -> None:
    router.add("GET", "/api/ner", _ner_get)
    router.add("GET", "/api/ner/export", _ner_export)
    router.add("PUT", "/api/ner", _ner_put)
    router.add("PATCH", "/api/ner", _ner_patch)
    router.add("GET", "/api/ner/review", lambda ctx: _review_get(ctx, "ner_review.json"))
    router.add("PUT", "/api/ner/review", lambda ctx: _review_put(ctx, "ner_review.json"))
    router.add("POST", "/api/ner/review/apply", _ner_review_apply)
    router.add("GET", "/api/translate_check_llm/review", lambda ctx: _review_get(ctx, "translate_check_llm_review.json"))
    router.add("PUT", "/api/translate_check_llm/review", lambda ctx: _review_put(ctx, "translate_check_llm_review.json"))
    router.add("POST", "/api/translate_check_llm/review/apply", _tcl_review_apply)
    router.add("GET", "/api/notes", _notes_get)
    router.add("PUT", "/api/notes", _notes_put)
    # настройки (общий .env) — в api_env._register_settings; файл книги
    # через API не отдаётся: его в модели больше нет
    router.add("GET", "/api/prompts", _prompts_list)
    router.add("POST", "/api/prompts/copy-set", _prompts_copy_set)
    router.add("GET", "/api/prompts/{name}", _prompts_get)
    router.add("PUT", "/api/prompts/{name}", _prompts_put)
    router.add("DELETE", "/api/prompts/{name}", _prompts_delete)
    # очистка вкладки: все промпты книги (роут — без {name})
    router.add("DELETE", "/api/prompts", _prompts_delete)
    router.add("GET", "/api/prompts/{name}/template", _prompts_template)
    router.add("GET", "/api/metadata", _metadata_get)
    router.add("PUT", "/api/metadata", _metadata_put)
    router.add("GET", "/api/cover", _cover_get)
    router.add("PUT", "/api/cover", _cover_put)
    router.add("DELETE", "/api/cover", _cover_delete)


# ════════════════════════════════════════════════════════════════════
# Регистрация роутов
# ════════════════════════════════════════════════════════════════════
# Отчёты translate_check
# ════════════════════════════════════════════════════════════════════

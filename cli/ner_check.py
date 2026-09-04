#!/usr/bin/env python3
"""
ner_check.py — LLM-проверка глоссария ner.json и применение правок.
Без интерактивного меню. Все параметры через argparse или .env.

Режимы (--passes, по умолчанию whole):
  whole — выбранные типы ОДНОВРЕМЕННО: весь (отфильтрованный по
          --types) список одной посылкой; батчи только если глоссарий
          больше бюджета --batch_size, записи по count по убыванию;
  types — выбранные типы ПО ОЧЕРЕДИ: каждый type отдельно
          (консистентность внутри типа); батчи и типы идут
          ПАРАЛЛЕЛЬНО в рамках --threads (общий прогрессбар по
          всем батчам).

Рекомендуемый двухэтапный режим (человеческие контрольные точки):
  1) --passes whole          → правки в ner_review.json, человек правит
     статусы принять/отклонить → --apply;
  2) --passes types          → по уже обновлённому ner.json, правки
     ДОписываются в тот же ner_review.json → человек → --apply.
Файл ner_review.json — накопительный: каждая правка несёт «этап»,
«статус» и флаг «применено»; повторный прогон не затирает решения
человека (дедупликация по term+field+old+new).

Артефакты: ner_review.json (накопительный файл правок; отчёты
ner_report.md и ner_changes.md удалены — не нужны). --apply
применяет правки ИЗ ФАЙЛА (без LLM): бэкап ner.json.bak;
--dry-run — без записи.
Legacy-формат (простой массив патчей, старый ner_patches.json)
понимается автоматически.

Авто-режим (--auto-apply): правки применяются сразу после каждого
этапа, без человека: whole → применение → types по обновлённым
данным → применение. Ошибка LLM/парсинга в авто-режиме — fail-fast
(код 1).

Примеры:
  python3 ner_check.py --passes whole          # выбранные типы разом
  python3 ner_check.py --apply --dry-run       # предпросмотр правок
  python3 ner_check.py --apply                 # применить правки
  python3 ner_check.py --passes types          # выбранные типы по очереди
"""
from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import re
import shutil
import sys
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

try:
    import requests  # noqa: F401 — проверка зависимости (LLM через core.common)
except ImportError:
    sys.exit("❌ Требуется: pip install requests")

# ── bootstrap: поиск core/common.py подъёмом от скрипта ──
def _bootstrap_core() -> None:
    from pathlib import Path as _P
    p = _P(os.path.dirname(os.path.abspath(__file__)))
    for _ in range(6):
        if (p / "core" / "common.py").is_file():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
            return
        if p.parent == p:
            break
        p = p.parent

_bootstrap_core()

from core.common import (  # noqa: E402
    REVIEW_ACCEPT,
    REVIEW_REJECT,
    _int_count,
    apply_ner_patches,
    atomic_write,
    build_fts_index,
    build_ner_batches,
    compile_chapter_text,
    diff_ner_records,
    emit_progress,
    even_sample,
    find_env_file,
    log_argv,
    filter_ner_items,
    format_ner_record,
    fts_escape,
    fts_search_all,
    get_server_config,
    glossary_body,
    load_prompt,
    merge_review_entries,
    ner_item_lookup,
    parse_dotenv,
    parse_rag_suggestions,
    parse_review_doc,
    print_env_help,
    review_entry,
    setup_logging,
    stream_chat_completion,
    web_progress_enabled,
)

DEFAULT_PROMPT_FILE = os.path.join("prompts", "ner_check_prompt.txt")
DEFAULT_REVIEW = "ner_review.json"
DEFAULT_BATCH_SIZE = 196608  # СИМВОЛЫ (~65536 токенов)

# Префикс запроса типовых этапов (этап 2): глоссарий уже выверен целиком —
# не перетирать уже унифицированные решения этапа 1.
TYPES_STAGE_PREFIX = (
    "ВНИМАНИЕ: глоссарий уже проверен целиком, и правки этого этапа "
    "внесены. НЕ предлагай переименования терминов и переводов, "
    "унифицированных на прошлом этапе, — они приняты человеком. Ищи "
    "только несогласованность, ошибки и неверные типы ВНУТРИ данной "
    "группы записей.\n\n")

DEFAULT_NER_RAG_PROMPT = """\
Ты — профессиональный редактор и локализатор. Ниже дан ОДИН термин
и релевантные фрагменты книги, где он встречается.

**Твоя задача:** уточни ТОЛЬКО запрошенный термин: исправь значения
выбранных полей, если по фрагментам видно, что текущее значение
неверно. Неизменённые поля скопируй ДОСЛОВНО.

**Проверяемые поля:** {fields}

### ФОРМАТ ОТВЕТА
Верни ТОЛЬКО JSON-массив без markdown-заборов — одна запись (или
пустой массив, если уточнений нет):
[{"term": "<термин дословно>", "<поле>": "<исправленное значение>", ..., "reason": "<обоснование по фрагментам>"}]

### ВАЖНЫЕ ПРАВИЛА
1. Термин (term) — идентификатор записи: менять его ЗАПРЕЩЕНО.
2. Возвращай ТОЛЬКО запрошенный термин — записи по другим терминам
   не включай.
3. Поля можно править только из списка «Проверяемые поля»; остальные
   поля записи не возвращай.
4. Не выдумывай: если по фрагментам правка не обоснована — верни
   пустой массив.

## ТЕРМИН И ФРАГМЕНТЫ

{rag_block}
"""


DEFAULT_NER_CHECK_PROMPT = """\
Ты — профессиональный редактор и локализатор. Ниже приведён глоссарий перевода (термины с выбранными полями).

**Твоя задача:** провести полный анализ глоссария и выявить ошибки,
нарушения логики лора и непоследовательность; вернуть ИСПРАВЛЕННЫЕ
записи — те, в которые ты внёс правки в одно или несколько полей.

Оценивай глоссарий по следующим 4 критериям:

**1. Корректность и соответствие лору (Контекст)**
- Неверный перевод, искажение смысла или неверный тип сущности (например, Person вместо Location).
- Противоречия между предложенным переводом, контекстом и примечаниями (notes).
- Отсутствие адаптации (калькирование оригинала, неестественное звучание).

**2. Консистентность терминологии и имен (Единообразие)**
- Разное написание одного и того же имени, топонима или термина в разных записях.
- Синонимический разнобой в рамках лора (например, один тип объекта переводится то как «клан», то как «школа»; то «гора», то «пик»).
- Наличие дублей или конфликтующих записей, требующих приведения к единому стандарту.

**3. Стилистика и грамматика**
- Грамматические ошибки, несогласования падежей/родов.
- Смешение стилей (например, неуместные архаизмы рядом с современным сленгом).

**4. Форматирование, типографика и капитализация**
- Непоследовательное использование заглавных и строчных букв (в титулах, названиях навыков, артефактов и топонимов).
- Разнобой в написании числительных, использовании кавычек, дефисов и пробелов.

**Проверяемые поля:** {fields}

### ФОРМАТ ОТВЕТА
Верни ТОЛЬКО JSON-массив без markdown-заборов и пояснений. Каждый элемент — исправленная запись:
{"term": "<термин дословно>", "<поле>": "<исправленное значение>", ..., "reason": "<краткое обоснование>"}
Возвращай ТОЛЬКО записи с изменениями; без изменений — не включай.
Если правок нет — верни пустой массив: []

### ВАЖНЫЕ ПРАВИЛА
1. Термин (term) — идентификатор записи: менять его ЗАПРЕЩЕНО.
2. Поля можно править только из списка «Проверяемые поля»; незатронутые
   поля скопируй в запись ДОСЛОВНО.
3. Системная проблема — правь все затронутые записи, по одной на термин.
4. Без удалений: не предлагай удалять дублирующие записи. Для дублей укажи единый перевод.
5. Строгость: не выдумывай ошибки. Допустимый вариант, не нарушающий консистентность, — пропускай.

## ГЛОССАРИЙ

{glossary}
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="LLM-проверка глоссария ner.json + применение правок")
    p.add_argument("--input", default="ner.json",
                   help="Путь к ner.json (по умолчанию: ner.json).")
    p.add_argument("--review", "--patches", dest="review",
                   default=DEFAULT_REVIEW,
                   help="Накопительный файл правок для человека "
                        "(по умолчанию: ner_review.json). Понимает и "
                        "legacy-массив патчей (старый ner_patches.json).")
    p.add_argument("--prompt_file", default=DEFAULT_PROMPT_FILE,
                   help="Внешний промпт; плейсхолдер {glossary}. "
                        "Нет файла — встроенный fallback.")
    p.add_argument("--passes", choices=["whole", "types", "rag"],
                   default="whole",
                   help="whole — выбранные типы одновременно (весь список "
                        "разом, по умолчанию); types — выбранные типы по "
                        "очереди (каждый тип отдельно); rag — точечная "
                        "проверка спорных терминов по списку с FTS5-"
                        "контекстом.")
    p.add_argument("--rag_terms", default="",
                   help="RAG-режим: список терминов, каждый с новой строки "
                        "(остальное подтягивается из ner.json).")
    p.add_argument("--rag_novel", default="",
                   help="RAG-режим (legacy): txt-файл книги для "
                        "FTS5-поиска (релевантные фрагменты по терминам). "
                        "Новый способ — --rag_source_type (сборка глав).")
    p.add_argument("--rag_source_type", default="",
                   choices=["", "chapter", "translated", "redacted",
                            "polished"],
                   help="RAG-режим: тип исходного файла главы для сборки "
                        "книги в память (chapter/translated/redacted/"
                        "polished); пусто — legacy --rag_novel файл.")
    p.add_argument("--chapters_dir", default="chapters",
                   help="RAG-режим: папка глав для сборки (по умолчанию: "
                        "./chapters).")
    p.add_argument("--start", type=int, default=None,
                   help="RAG-режим: начальная глава сборки (по умолчанию: "
                        "минимальная найденная).")
    p.add_argument("--end", type=int, default=None,
                   help="RAG-режим: конечная глава сборки (по умолчанию: "
                        "максимальная найденная).")
    p.add_argument("--rag_budget", type=int, default=65536,
                   help="RAG-режим: бюджет релевантного текста на термин, "
                        "СИМВОЛЫ (по умолчанию: 6000).")
    p.add_argument("--save-interval", type=int, default=0,
                   help="RAG-режим: сохранять review-файл каждые N "
                        "терминов (0 = только в конце)")
    p.add_argument("--rag_prompt_file", default=None,
                   help="RAG-режим: файл промпта с тегом <prompt_rag> "
                        "(по умолчанию: тот же --prompt_file).")
    p.add_argument("--types", default="",
                   help="Ограничить проходы по типам (через запятую). "
                        "Пусто = все типы ner.json.")
    p.add_argument("--batch_size", type=int, default=DEFAULT_BATCH_SIZE,
                   help="Бюджет батча, СИМВОЛЫ (по умолчанию: 196608 "
                        "≈ 65536 токенов).")
    p.add_argument("--threads", type=int, default=1,
                   help="Параллельных потоков (1..16): батчи и типы "
                        "выполняются одновременно (по умолчанию: 1).")
    p.add_argument("-c", "--count-threshold", type=int, default=0,
                   help="Порог count: записи с count > X (по умолчанию: 0).")
    p.add_argument("--fields", default="term,type,translation",
                   help="Поля записи, передаваемые LLM (через запятую). "
                        "term — всегда. По умолчанию: term,type,translation.")
    p.add_argument("--apply", action="store_true",
                   help="Применить правки из --review к ner.json (без LLM).")
    p.add_argument("--auto-apply", action="store_true",
                   help="Авто-режим: применять правки сразу после этапа, "
                        "без человека: whole → применение (types — по "
                        "обновлённым данным при ручном втором прогоне).")
    p.add_argument("--dry-run", action="store_true",
                   help="С --apply/--auto-apply: показать правки без "
                        "записи файлов.")
    p.add_argument("--no-bak", action="store_true",
                   help="Не создавать бэкап <файл>.bak при применении "
                        "(по умолчанию создаётся).")
    # сервер: CLI > HOST/API_KEY/MODEL из .env > help+exit
    p.add_argument("--host", default=None, help="URL API-сервера (пусто = HOST из .env).")
    p.add_argument("--api_key", default=None, help="Bearer-ключ (пусто = API_KEY из .env).")
    p.add_argument("--model", default=None,
                   help="Модель: --model или MODEL/NER_CHECK_MODEL в .env (обязательна).")
    p.add_argument("--env_file", default=None, help="Явный путь к .env.")
    p.add_argument("--temperature", type=float, default=None,
                   help="Температура LLM (пусто = сервер).")
    p.add_argument("--reasoning_effort", default=None,
                   choices=["none", "minimal", "low", "medium", "high",
                            "xhigh", "max"],
                   help="Усилия рассуждений: none/minimal/low/medium/"
                        "high/xhigh/max (пусто = сервер; none — отключить).")
    p.add_argument("--max_tokens", type=int, default=65536,
                   help="Серверный предел ответа, ТОКЕНЫ (не расчёт).")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--max_retries", type=int, default=3)
    return p


def load_ner_json(path: str, logger):
    if not os.path.exists(path):
        sys.exit(f"❌ Файл не найден: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        sys.exit(f"❌ Ошибка чтения JSON {path}: {e}")
    if not isinstance(data, list):
        sys.exit(f"❌ {path}: ожидается список записей.")
    # count — частота: строки из JSON нормализуем к int (иначе пороги
    # и сортировки падают с TypeError)
    for it in data:
        if isinstance(it, dict) and "count" in it:
            it["count"] = _int_count(it["count"])
    logger.info(f"📖 {path}: {len(data)} записей.")
    return data


def load_review_file(path: str, logger):
    """Чтение файла правок (новый формат-объект или legacy-массив).
    Возвращает (meta: dict|None, entries: list|None); нет файла —
    (None, None); ошибка разбора — выход."""
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"❌ Не удалось прочитать {path}: {e}")
    entries = parse_review_doc(doc, logger)
    meta = ({k: v for k, v in doc.items() if k != "entries"}
            if isinstance(doc, dict) else None)
    return meta, entries


def save_review_file(path, input_path, created, entries, params=None,
                     meta=None):
    """Запись накопительного файла правок (сохраняет дату создания и
    параметры прогона: params из do_check, иначе — из meta файла)."""
    doc = {"created": created,
           "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "input": input_path,
           "entries": entries}
    saved_params = params if params is not None \
        else (meta or {}).get("params")
    if saved_params:
        doc["params"] = saved_params
    atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=2))


def resolve_server(args, logger):
    """CLI > HOST/API_KEY/MODEL из .env > help+exit.
    Возвращает (base_url, key, model, env_data)."""
    env_data = parse_dotenv(find_env_file(args.env_file))
    sc = get_server_config(env_data, "ner_check")
    host = args.host or sc["host"]
    api_key = args.api_key if args.api_key is not None else sc["api_key"]
    model = args.model or sc["model"]
    if not host:
        print_env_help()
        sys.exit("❌ Не задан сервер: укажите --host или создайте .env (HOST).")
    if not api_key:
        api_key = os.environ.get("LLM_API_KEY", "")
    base_url = host.rstrip("/")
    if "/v1" not in base_url:
        base_url += "/v1"
    return base_url, (api_key or ""), model, env_data


def get_prompt(args, logger) -> str:
    """Промпт проверки выбранных типов: тег <prompt_check> из файла;
    нет тега — файл целиком (обратная совместимость); нет файла —
    встроенный DEFAULT_NER_CHECK_PROMPT."""
    text = load_prompt(args.prompt_file, logger)
    if not text:
        logger.info("ℹ Внешний промпт не найден — встроенный fallback.")
        return DEFAULT_NER_CHECK_PROMPT
    m = re.search(r"<prompt_check>(.*?)</prompt_check>", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def render_prompt(prompt_tpl: str, body: str, fields=None) -> str:
    """Подстановка плейсхолдеров: {glossary}/{rag_block} — тело,
    {fields} — список проверяемых полей (term не включается)."""
    fields_line = ", ".join(fields) if fields else "-"
    if "{glossary}" in prompt_tpl:
        tpl = prompt_tpl.replace("{glossary}", body)
    elif "{rag_block}" in prompt_tpl:
        tpl = prompt_tpl.replace("{rag_block}", body)
    else:
        tpl = prompt_tpl.rstrip() + "\n\n## ГЛОССАРИЙ\n\n" + body
    return tpl.replace("{fields}", fields_line)


def run_pass_tasks(title, items, prompt_tpl, args, logger):
    """Собирает задачи (title, batch) прохода: резка по бюджету.
    Возвращает список (title, batch) в порядке следования батчей."""
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    batches = build_ner_batches(items, args.batch_size, fields)
    logger.info(f"── {title}: {len(items)} записей, батчей: {len(batches)}")
    return [(title, batch) for batch in batches]


def run_batch(task, prompt_tpl, args, base_url, api_key, model, logger):
    """Один батч проверки (запускается в потоке). Возвращает
    (title, records|None) — исправленные записи от LLM; None при
    ошибке LLM/парсинга. Diff с ner.json — в do_check (нужен
    полный items_by_term)."""
    title, batch = task
    fields = [f.strip() for f in args.fields.split(",") if f.strip()
              if f.strip() != "term"]
    body = glossary_body(batch, fields)
    tpl = (prompt_tpl if title == "Весь глоссарий"
           else TYPES_STAGE_PREFIX + prompt_tpl)
    user_msg = render_prompt(tpl, body, fields)
    logger.info(f"  батч: {len(batch)} записей, "
                f"{len(user_msg)} символов запроса")
    text, err = stream_chat_completion(
        base_url, model,
        [{"role": "user", "content": user_msg}],
        api_key=api_key,
        max_retries=args.max_retries,
        timeout=args.timeout, stream_timeout=args.timeout,
        temperature=args.temperature,
        reasoning_effort=args.reasoning_effort,
        max_tokens=args.max_tokens,
        reference_len=0, logger=logger,
        label=f"ner_check {title}")
    if err:
        logger.error(f"  ❌ LLM: {err}")
        return title, None
    found = parse_rag_suggestions(text, logger, fields)
    if found is None:
        logger.error("  ❌ Ответ не распарсился (см. лог выше).")
        return title, None
    logger.info(f"  ✔ Исправленных записей: {len(found)}")
    return title, found


def load_rag_prompt(prompt_file: str, logger) -> str:
    """RAG-промпт: тег <prompt_rag> из файла (или --rag_prompt_file,
    или --prompt_file); нет тега — встроенный DEFAULT_NER_RAG_PROMPT."""
    for f in (prompt_file,):
        if not f or not os.path.exists(f):
            continue
        try:
            text = open(f, "r", encoding="utf-8").read()
        except OSError:
            continue
        m = re.search(r"<prompt_rag>(.*?)</prompt_rag>", text, re.DOTALL)
        if m:
            return m.group(1).strip()
    logger.info("ℹ RAG-промпт: тег <prompt_rag> не найден — встроенный "
                "fallback.")
    return DEFAULT_NER_RAG_PROMPT


def build_rag_block(terms, items_by_term, db, budget, fields=None,
                    logger=None):
    """Собирает блок «термины + релевантные фрагменты» для RAG-промпта.
    fields — поля записи ner.json, передаваемые LLM (термин — всегда,
    в заголовке; дефолт — type/translation); фрагменты — FTS5-поиск по
    term (исходный термин — для chapter-источника), при пустом
    результате — по translation (переведённые источники), равномерная
    выборка (не только начало книги), суммарный бюджет — budget
    СИМВОЛОВ."""
    selected = {f.strip() for f in fields} if fields else None
    lines = []
    for term in terms:
        item = ner_item_lookup(items_by_term, term)
        translation = (item or {}).get("translation") or ""
        type_str = (item or {}).get("type") or "?"
        lines.append(f"--- {term} (тип: {type_str}) ---")
        # выбранные поля записи (термин уже в заголовке)
        if item:
            rec = format_ner_record(item, 0, selected)
            for ln in rec[1:]:
                if ln.startswith("term:"):
                    continue
                lines.append("  " + ln)
        else:
            lines.append("  (нет записи в ner.json)")
        # ищем канонический термин (вариант LLM со скобками → запись)
        ev = fts_escape(item["term"] if item else term)
        hits = fts_search_all(db, f'"{ev}"')
        if not hits and translation:
            # перевод — fallback для переведённых источников
            # (translated/redacted/polished)
            ev = fts_escape(translation)
            hits = fts_search_all(db, f'"{ev}"')
        if not hits:
            lines.append("  (термин не найден в тексте)")
            continue
        frags = even_sample(hits, max(1, budget // 1500))
        # нарезаем фрагменты по остатку бюджета
        used = 0
        for f in frags:
            if used >= budget:
                break
            take = min(len(f), budget - used)
            lines.append(f"  · {f[:take]}")
            used += take
        lines.append("")
    return "\n".join(lines)


def _rag_query(term, user_msg, args, base_url, api_key, model, logger,
               items_by_term, fields):
    """Один термин — один LLM-запрос (блок собран заранее, FTS5-БД
    не трогаем из воркера). Возвращает (entries, ok)."""
    logger.info(f"  {term}: запрос {len(user_msg)} символов "
                f"(бюджет на термин {args.rag_budget})")
    text_out, err = stream_chat_completion(
        base_url, model,
        [{"role": "user", "content": user_msg}],
        api_key=api_key,
        max_retries=args.max_retries,
        timeout=args.timeout, stream_timeout=args.timeout,
        temperature=args.temperature,
        reasoning_effort=args.reasoning_effort,
        max_tokens=args.max_tokens,
        reference_len=0, logger=logger,
        label=f"ner_check rag {term}")
    if err:
        logger.error(f"  ❌ {term}: LLM: {err}")
        return [], False
    found = parse_rag_suggestions(text_out, logger, fields)
    if found is None:
        logger.error(f"  ❌ {term}: ответ не распарсился (см. лог выше).")
        return [], False
    # строго по запрошенному термину: записи по другим терминам —
    # шум (LLM «уточняет» всё, что видит во фрагментах), отбрасываем
    wanted = unicodedata.normalize("NFC", term)
    records = []
    for rec in found:
        t = unicodedata.normalize(
            "NFC", str(rec.get("term", "")).strip())
        if t != wanted:
            logger.warning(f"  ⚠ Термин {rec.get('term')!r} — не из "
                           f"списка запроса, пропущен.")
            continue
        records.append(rec)
    # diff по выбранным полям → review-записи (stage=RAG)
    patches = diff_ner_records(records, items_by_term, fields, logger)
    entries = [e for p in patches
               if (e := review_entry(p, stage="RAG"))]
    logger.info(f"  ✔ {term}: записей {len(found)}, "
                f"правок: {len(entries)}")
    return entries, True


def run_rag(args, logger, base_url, api_key, model, prompt_tpl) -> int:
    """RAG-режим: каждый термин — ОТДЕЛЬНЫЙ LLM-запрос, параллельно
    (--threads); бюджет на термин: промпт + фрагменты ≤ rag_budget.
    Возвращает код возврата (0 — ок)."""
    terms = [t.strip() for t in args.rag_terms.split("\n")
             if t.strip()]
    if not terms:
        logger.error("❌ RAG: пустой список терминов (--rag_terms).")
        return 1
    # книга для FTS5: сборка глав в память (--rag_source_type) или
    # legacy txt-файл (--rag_novel)
    if args.rag_source_type:
        text, info = compile_chapter_text(
            args.chapters_dir, want=args.rag_source_type,
            start=args.start, end=args.end, logger=logger)
        if info["written"] == 0:
            logger.error(f"❌ RAG: не собрано ни одной главы из "
                         f"{args.chapters_dir} (тип {args.rag_source_type}).")
            return 1
        logger.info(f"📚 RAG: сборка {args.chapters_dir} "
                    f"({args.rag_source_type}): {info['written']} глав, "
                    f"{len(text)} символов.")
    elif args.rag_novel:
        if not os.path.exists(args.rag_novel):
            logger.error(f"❌ RAG: файл книги не найден: {args.rag_novel}")
            return 1
        try:
            with open(args.rag_novel, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as exc:
            logger.error(f"❌ RAG: не удалось прочитать "
                         f"{args.rag_novel}: {exc}")
            return 1
        logger.info(f"🔎 RAG: {len(terms)} терминов, "
                    f"{len(text)} символов книги (--rag_novel).")
    else:
        logger.error("❌ RAG: укажите --rag_source_type (сборка глав) "
                     "или --rag_novel (txt-файл книги).")
        return 1
    data = load_ner_json(args.input, logger)
    items_by_term = {unicodedata.normalize("NFC", i.get("term", "")): i
                     for i in data}
    if args.rag_source_type:
        logger.info(f"🔎 RAG: {len(terms)} терминов.")
    db = build_fts_index(text, 1000)
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    # бюджет на термин — ТОЛЬКО фрагменты (промпт не вычитается):
    # один термин = один запрос, фрагменты влезают в rag_budget
    logger.info(f"🔎 RAG: {len(terms)} терминов × бюджет {args.rag_budget} "
                f"(фрагменты на термин), потоков {args.threads}")

    # FTS5-БД — только из главного потока (sqlite thread-bound):
    # блоки собираем заранее, воркеры только шлют LLM-запросы
    tasks = []
    for term in terms:
        block = build_rag_block([term], items_by_term, db, args.rag_budget,
                                fields, logger)
        tasks.append((term, render_prompt(prompt_tpl, block)))

    total = len(tasks)
    done = 0
    lock = threading.Lock()
    entries: list[dict] = []
    failures = 0
    emit_progress(0, total, "Точечная проверка (RAG)")

    # накопительный review-файл: --save-interval N — сохранять каждые
    # N терминов (0 = только в конце, как раньше)
    save_interval = max(0, args.save_interval or 0)
    meta, existing = load_review_file(args.review, logger)
    created = (meta or {}).get("created") \
        or datetime.now().strftime("%Y-%m-%d %H:%M")
    last_flush = 0

    def flush_review(final=False):
        nonlocal last_flush
        if len(entries) == last_flush and not final:
            return
        merged, added = merge_review_entries(existing or [], entries, logger)
        save_review_file(args.review, args.input, created, merged)
        last_flush = len(entries)
        if final:
            logger.info(f"🧩 Правки: {args.review} "
                        f"(новых: {added}, всего: {len(merged)})")
        else:
            logger.info(f"🧩 Правки: {args.review} (всего: {len(merged)})")

    def worker(term, user_msg):
        nonlocal done, failures
        res, ok = _rag_query(term, user_msg, args, base_url, api_key,
                             model, logger, items_by_term, fields)
        with lock:
            entries.extend(res)
            if not ok:
                failures += 1
            done += 1
            emit_progress(done, total, "Точечная проверка (RAG)")
            if save_interval and done % save_interval == 0:
                flush_review()

    workers = max(1, min(args.threads or 1, total))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for _ in ex.map(lambda t: worker(*t), tasks):
            pass

    if failures == total:
        logger.error("❌ RAG: все запросы завершились ошибкой.")
        return 1
    if failures:
        logger.warning(f"⚠ RAG: {failures} из {total} запросов с ошибкой.")
    logger.info(f"  ✔ Определено терминов: {len(entries)}")
    if not entries:
        logger.info("  ℹ Уточнений, отличающихся от ner.json, нет.")
    # финальное сохранение (с числом новых правок)
    flush_review(final=True)
    return 0


def patches_table(patches, offset=0) -> str:
    lines = ["| # | Термин | Поле | Было | Стало | Причина |",
             "|---|--------|------|------|-------|---------|"]
    for i, p in enumerate(patches, offset + 1):
        old = p["old"].replace("|", "\\|")
        new = p["new"].replace("|", "\\|")
        reason = p["reason"].replace("|", "\\|")
        lines.append(f"| {i} | {p['term']} | {p['field']} "
                     f"| {old} | {new} | {reason} |")
    return "\n".join(lines)


def do_check(args, logger) -> int:
    base_url, api_key, model, _ = resolve_server(args, logger)

    if args.passes == "rag":
        prompt_tpl = load_rag_prompt(args.rag_prompt_file or args.prompt_file,
                                     logger)
        return run_rag(args, logger, base_url, api_key, model, prompt_tpl)

    data = load_ner_json(args.input, logger)
    type_filter = ([t.strip() for t in args.types.split(",") if t.strip()]
                   or None)
    items = filter_ner_items(data, args.count_threshold, type_filter)
    if not items:
        sys.exit("❌ Нет записей после фильтрации.")
    logger.info(f"📊 После фильтрации: {len(items)} записей.")
    # эталон для diff: ВСЕ записи ner.json (не только отфильтрованные)
    items_by_term = {unicodedata.normalize("NFC", i.get("term", "")): i
                     for i in data}
    check_fields = [f.strip() for f in args.fields.split(",")
                    if f.strip() and f.strip() != "term"]

    prompt_tpl = get_prompt(args, logger)
    params = {"input": args.input,
              "бюджет батча": args.batch_size,
              "порог count": args.count_threshold,
              "поля": args.fields,
              "промпт файл": args.prompt_file,
              "типы": args.types}
    meta, entries = load_review_file(args.review, logger)
    created = (meta or {}).get("created") \
        or datetime.now().strftime("%Y-%m-%d %H:%M")
    if entries is None:
        entries = []
    added_total = 0
    backed_up = False

    def save_review():
        save_review_file(args.review, args.input, created, entries,
                         params=params)

    def collect(title, patches):
        """Патчи прохода → записи review-файла (дедуп с накопленным)."""
        nonlocal entries, added_total
        fresh = [e for e in (review_entry(p, stage=title) for p in patches)
                 if e]
        entries, added = merge_review_entries(entries, fresh, logger)
        added_total += added
        save_review()

    def auto_apply():
        """Авто-режим: применить принятые неприменённые правки сразу."""
        nonlocal data, backed_up
        if args.dry_run:
            d2, e2 = copy.deepcopy(data), copy.deepcopy(entries)
            applied, skipped = apply_ner_patches(d2, e2)
            logger.info(f"DRY-RUN авто-применение: было бы "
                        f"{len(applied)}, пропущено {skipped}.")
            for p in applied:
                logger.info(f"  · [{p.get('этап', '')}] {p['term']} "
                            f"[{p['field']}]: {p['old']!r} → {p['new']!r}")
            return
        applied, skipped = apply_ner_patches(data, entries, logger)
        if applied:
            if not args.no_bak and not backed_up:
                shutil.copy2(args.input, args.input + ".bak")
                backed_up = True
                logger.info(f"💾 Бэкап: {args.input}.bak")
            atomic_write(args.input,
                         json.dumps(data, ensure_ascii=False, indent=2))
        save_review()
        logger.info(f"Авто-применение: применено {len(applied)}, "
                    f"пропущено {skipped}"
                    + (" (без бэкапа)" if args.no_bak else "") + ".")

    # ── сбор задач (батчей): whole — один проход, types — по типам
    stage_tasks = []  # [(title, batch)]

    def run_stage_tasks(title, subset):
        nonlocal stage_tasks
        stage_tasks.extend(
            run_pass_tasks(title, subset, prompt_tpl, args, logger))

    if args.passes == "whole":
        run_stage_tasks("Весь глоссарий", items)
    if args.passes == "types":
        if not items:
            logger.info("ℹ Нет записей для типовых проходов.")
        else:
            all_types = sorted({i.get("type", "") for i in items
                                if i.get("type")})
            wanted = type_filter or all_types
            for t in [t for t in all_types if t in wanted]:
                subset = [i for i in items if i.get("type") == t]
                run_stage_tasks(f"Тип: {t}", subset)

    if not stage_tasks:
        logger.info("ℹ Нет батчей — проверять нечего.")
        save_review()
        return 0

    # ── параллельное выполнение: threads потоков на ВСЕ батчи
    #    (типы и чанки идут одновременно, не последовательно)
    try:
        workers = max(1, min(16, int(args.threads)))
    except (TypeError, ValueError):
        workers = 1
    logger.info(f"🔀 Потоков: {workers} (батчей всего: {len(stage_tasks)})")
    total = len(stage_tasks)
    emit_progress(0, total, "Проверка глоссария")
    if web_progress_enabled():
        logger.info(f"📊 Прогресс: 0/{total}")
    results = {}  # title -> list[records] (исправленные записи от LLM)
    failures = {}  # title -> число упавших батчей
    done = 0

    def _run_batch(task):
        return run_batch(task, prompt_tpl, args,
                         base_url, api_key, model, logger)

    if workers <= 1:
        for task in stage_tasks:
            title, found = _run_batch(task)
            done += 1
            emit_progress(done, total, "Проверка глоссария")
            if found is None:
                failures[title] = failures.get(title, 0) + 1
            else:
                results.setdefault(title, []).extend(found)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_run_batch, t): t for t in stage_tasks}
            for fut in as_completed(futs):
                title, found = fut.result()
                done += 1
                emit_progress(done, total, "Проверка глоссария")
                if found is None:
                    failures[title] = failures.get(title, 0) + 1
                else:
                    results.setdefault(title, []).extend(found)
    if web_progress_enabled():
        logger.info(f"📊 Прогресс: {done}/{total}")

    # ── сборка в порядке проходов (детерминированный порядок записи) ──
    seen_titles = []
    for title, _ in stage_tasks:
        if title not in seen_titles:
            seen_titles.append(title)
    for title in seen_titles:
        records = results.get(title)
        failed = failures.get(title, 0)
        n_batches = sum(1 for t, _ in stage_tasks if t == title)
        if records is None and failed >= n_batches:
            logger.error(f"❌ Этап «{title}» не завершился "
                         f"(LLM/парсинг): {failed}/{n_batches} батчей.")
            if args.auto_apply:
                sys.exit(f"❌ Авто-режим: этап «{title}» не завершился "
                         f"(LLM/парсинг) — останов.")
            continue
        if failed:
            logger.warning(f"⚠ Этап «{title}»: {failed}/{n_batches} "
                           f"батчей не завершились — пропущены.")
        # diff с эталоном: LLM вернула исправленные записи — правки
        # (old→new) считаем здесь, сверяя с ner.json
        records = records or []
        patches = diff_ner_records(records, items_by_term, check_fields,
                                   logger)
        logger.info(f"  ✔ «{title}»: записей от LLM {len(records)}, "
                    f"правок: {len(patches)}")
        collect(title, patches)
        if args.auto_apply:
            auto_apply()

    save_review()
    logger.info(f"🧩 Правки: {args.review} "
                f"(новых: {added_total}, всего: {len(entries)})")
    if entries:
        logger.info("Дальше: правка статусов в " + args.review
                    + " (принять/отклонить), затем: python3 ner_check.py "
                      "--apply --dry-run и --apply.")
    else:
        logger.info("Правок нет — применять нечего.")
    return 0


def do_apply(args, logger) -> int:
    if not os.path.exists(args.review):
        sys.exit(f"❌ Файл правок не найден: {args.review}. "
                 f"Сначала запустите проверку.")
    meta, entries = load_review_file(args.review, logger)
    if entries is None:
        sys.exit(f"❌ {args.review}: не распознан список правок.")
    pending = sum(1 for e in entries
                  if e["status"] == REVIEW_ACCEPT and not e["applied"])
    n_rej = sum(1 for e in entries if e["status"] == REVIEW_REJECT)
    n_done = sum(1 for e in entries if e["applied"])
    logger.info(f"📋 {args.review}: к применению {pending}, "
                f"отклонено {n_rej}, уже применено {n_done}.")
    data = load_ner_json(args.input, logger)
    applied, skipped = apply_ner_patches(data, entries, logger)
    logger.info(f"Итог: применено {len(applied)}, "
                f"пропущено/отклонено {skipped}.")
    if not applied:
        logger.info("Нечего применять (все правки отклонены, уже "
                    "применены или список пуст).")
        return 0
    if args.dry_run:
        logger.info("DRY-RUN: файлы не изменены.")
        for p in applied:
            stage = p.get("stage") or ""
            prefix = f"[{stage}] " if stage else ""
            logger.info(f"  · {prefix}{p['term']} [{p['field']}]: "
                        f"{p['old']!r} → {p['new']!r}")
        return 0
    if not args.no_bak:
        shutil.copy2(args.input, args.input + ".bak")
        logger.info(f"💾 Бэкап: {args.input}.bak")
    atomic_write(args.input, json.dumps(data, ensure_ascii=False, indent=2))
    save_review_file(args.review, args.input,
                     (meta or {}).get("created")
                     or datetime.now().strftime("%Y-%m-%d %H:%M"),
                     entries, meta=meta)
    logger.info(f"✅ ner.json обновлён ({len(applied)} правок"
                + (" без бэкапа" if args.no_bak else "") + "); "
                f"флаги «применено» сохранены в {args.review}")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        os.makedirs("logs", exist_ok=True)
    except OSError as exc:
        print(f"Не удалось создать logs/: {exc}")
        return 1
    logger, _ = setup_logging(os.path.join("logs", "ner_check"))
    log_argv(logger)
    if args.apply:
        return do_apply(args, logger)
    return do_check(args, logger)


if __name__ == "__main__":
    sys.exit(main())

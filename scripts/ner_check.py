#!/usr/bin/env python3
"""
ner_check.py — LLM-проверка глоссария ner.json и применение правок.
Без интерактивного меню. Все параметры через argparse или .env.

Проходы (--passes, по умолчанию all):
  whole — весь список одной посылкой (батчи только если глоссарий
          больше бюджета --batch_size; записи идут по count по убыванию);
  types — каждый type отдельно (консистентность внутри типа);
  all   — весь список + каждый тип за один запуск по одному снимку
          данных (быстрый режим).

Рекомендуемый двухэтапный режим (человеческие контрольные точки):
  1) --passes whole          → правки в ner_review.json, человек правит
     статусы принять/отклонить → --apply;
  2) --passes types          → по уже обновлённому ner.json, правки
     ДОписываются в тот же ner_review.json → человек → --apply.
Файл ner_review.json — накопительный: каждая правка несёт «этап»,
«статус» и флаг «применено»; повторный прогон не затирает решения
человека (дедупликация по term+field+old+new).

Артефакты: ner_report.md (отчёт прогона) + ner_review.json
(накопительный файл правок). --apply применяет правки ИЗ ФАЙЛА
(без LLM): бэкап ner.json.bak, лог ner_changes.md со ВСЕМИ
применёнными правками и их этапами; --dry-run — без записи.
Legacy-формат (простой массив патчей, старый ner_patches.json)
понимается автоматически.

Авто-режим (--auto-apply): правки применяются сразу после каждого
этапа, без человека. --passes all --auto-apply идёт ПОСЛЕДОВАТЕЛЬНО:
whole → применение → types уже по обновлённым данным → применение.
Ошибка LLM/парсинга в авто-режиме — fail-fast (код 1).

Примеры:
  python3 ner_check.py --passes whole          # этап 1: весь список
  python3 ner_check.py --apply --dry-run       # предпросмотр правок
  python3 ner_check.py --apply                 # применить правки
  python3 ner_check.py --passes types          # этап 2: по типам
  python3 ner_check.py --passes all --auto-apply   # полный автомат
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
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
    apply_ner_patches,
    atomic_write,
    build_ner_batches,
    emit_progress,
    find_env_file,
    log_argv,
    filter_ner_items,
    get_server_config,
    get_stage_model,
    glossary_body,
    load_prompt,
    merge_review_entries,
    parse_dotenv,
    parse_ner_patches,
    parse_review_doc,
    print_env_help,
    review_entry,
    setup_logging,
    stream_chat_completion,
    web_progress_enabled,
)

DEFAULT_PROMPT_FILE = os.path.join("prompts", "ner_check_prompt.txt")
DEFAULT_REPORT = "ner_report.md"
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

DEFAULT_NER_CHECK_PROMPT = """\
Ты — профессиональный редактор и локализатор. Ниже приведён глоссарий перевода (термины, типы, переводы, контекст, примечания).

**Твоя задача:** Провести полный анализ глоссария и выявить все ошибки, нарушения логики лора и непоследовательность.

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

### ФОРМАТ ОТВЕТА
Верни ТОЛЬКО JSON-массив без markdown-заборов и пояснений. Каждый элемент:
{"term": "<term записи дословно>", "field": "translation|type|notes", "old": "<текущее значение поля дословно>", "new": "<исправленное значение>", "reason": "<краткое обоснование>"}
Если ошибок нет — верни пустой массив: []

### ВАЖНЫЕ ПРАВИЛА
1. «old» копируй из записи ДОСЛОВНО (регистр, пробелы, пунктуация) — правка применяется только при точном совпадении.
2. «field» — только одно из: translation, type, notes.
3. Системная проблема — отдельный патч на каждый затронутый термин.
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
    p.add_argument("--report", default=DEFAULT_REPORT,
                   help="Путь к отчёту (по умолчанию: ner_report.md).")
    p.add_argument("--review", "--patches", dest="review",
                   default=DEFAULT_REVIEW,
                   help="Накопительный файл правок для человека "
                        "(по умолчанию: ner_review.json). Понимает и "
                        "legacy-массив патчей (старый ner_patches.json).")
    p.add_argument("--prompt_file", default=DEFAULT_PROMPT_FILE,
                   help="Внешний промпт; плейсхолдер {glossary}. "
                        "Нет файла — встроенный fallback.")
    p.add_argument("--passes", choices=["all", "whole", "types"],
                   default="all",
                   help="whole — весь список (этап 1); types — каждый тип "
                        "отдельно (этап 2); all — и то и другое за один "
                        "запуск (по умолчанию).")
    p.add_argument("--types", default="",
                   help="Ограничить проходы по типам (через запятую). "
                        "Пусто = все типы ner.json.")
    p.add_argument("--batch_size", type=int, default=DEFAULT_BATCH_SIZE,
                   help="Бюджет батча, СИМВОЛЫ (по умолчанию: 196608 "
                        "≈ 65536 токенов).")
    p.add_argument("-c", "--count-threshold", type=int, default=0,
                   help="Порог count: записи с count > X (по умолчанию: 0).")
    p.add_argument("--exclude-words", default="палладия,палладию",
                   help="Подстроки в notes для исключения записей.")
    p.add_argument("--show-aliases", action="store_true",
                   help="Показывать aliases в глоссарии.")
    p.add_argument("--show-votes", action="store_true",
                   help="Показывать статистику голосования.")
    p.add_argument("--apply", action="store_true",
                   help="Применить правки из --review к ner.json (без LLM).")
    p.add_argument("--auto-apply", action="store_true",
                   help="Авто-режим: применять правки сразу после каждого "
                        "этапа, без человека. С --passes all этапы идут "
                        "последовательно: whole → применение → types по "
                        "обновлённым данным → применение.")
    p.add_argument("--dry-run", action="store_true",
                   help="С --apply/--auto-apply: показать правки без "
                        "записи файлов.")
    # сервер: CLI > HOST/API_KEY/MODEL из .env > help+exit
    p.add_argument("--host", default=None, help="URL API-сервера (пусто = HOST из .env).")
    p.add_argument("--api_key", default=None, help="Bearer-ключ (пусто = API_KEY из .env).")
    p.add_argument("--model", default=None,
                   help="Модель: --model или MODEL/NER_CHECK_MODEL в .env (обязательна).")
    p.add_argument("--env_file", default=None, help="Явный путь к .env.")
    p.add_argument("--temperature", type=float, default=None,
                   help="Температура LLM (пусто = сервер).")
    p.add_argument("--reasoning_effort", default=None,
                   choices=["low", "medium", "high"],
                   help="Усилия рассуждений (low/medium/high).")
    p.add_argument("--no_reasoning", action="store_true",
                   help="Не слать reasoning-поле в payload.")
    p.add_argument("--max_tokens", type=int, default=65536,
                   help="Серверный предел ответа, ТОКЕНЫ (не расчёт).")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--stream_timeout", type=int, default=900)
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
    meta = ({k: v for k, v in doc.items() if k != "правки"}
            if isinstance(doc, dict) else None)
    return meta, entries


def save_review_file(path, input_path, created, entries, params=None,
                     meta=None):
    """Запись накопительного файла правок (сохраняет дату создания и
    параметры прогона: params из do_check, иначе — из meta файла)."""
    doc = {"создан": created,
           "обновлён": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "вход": input_path,
           "правки": entries}
    saved_params = params if params is not None \
        else (meta or {}).get("параметры")
    if saved_params:
        doc["параметры"] = saved_params
    atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=2))


def resolve_server(args, logger):
    """CLI > HOST/API_KEY/MODEL из .env > help+exit.
    Возвращает (base_url, key, model, env_data)."""
    env_data = parse_dotenv(find_env_file(args.env_file))
    sc = get_server_config(env_data)
    host = args.host or sc["host"]
    api_key = args.api_key if args.api_key is not None else sc["api_key"]
    model = args.model or get_stage_model(env_data, "ner_check")
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
    text = load_prompt(args.prompt_file, logger)
    if not text:
        logger.info("ℹ Внешний промпт не найден — встроенный fallback.")
        return DEFAULT_NER_CHECK_PROMPT
    return text


def render_prompt(prompt_tpl: str, body: str) -> str:
    if "{glossary}" in prompt_tpl:
        return prompt_tpl.replace("{glossary}", body)
    return prompt_tpl.rstrip() + "\n\n## ГЛОССАРИЙ\n\n" + body


def run_pass(title, items, prompt_tpl, args, base_url, api_key, model,
             logger, raws):
    """Один проход (весь список или один тип). Возвращает список патчей
    или None при ошибке LLM/парсинга всех батчей. Нераспознанные ответы
    складывает в raws: [(title, bi, nbatches, text)]."""
    batches = build_ner_batches(items, args.batch_size,
                                args.show_aliases, args.show_votes)
    logger.info(f"── {title}: {len(items)} записей, батчей: {len(batches)}")
    # Раунд 21: стартовое событие прогресса — бар виден сразу
    emit_progress(0, len(batches), "Проверка глоссария")
    if web_progress_enabled():
        logger.info(f"📊 Прогресс: 0/{len(batches)}")
    patches, ok_any = [], False
    for bi, batch in enumerate(batches, 1):
        body = glossary_body(batch, args.show_aliases, args.show_votes)
        user_msg = render_prompt(prompt_tpl, body)
        logger.info(f"  батч {bi}/{len(batches)}: {len(batch)} записей, "
                    f"{len(user_msg)} символов запроса")
        text, err = stream_chat_completion(
            base_url, model,
            [{"role": "user", "content": user_msg}],
            api_key=api_key,
            max_retries=args.max_retries,
            timeout=args.timeout, stream_timeout=args.stream_timeout,
            temperature=args.temperature,
            reasoning_effort=args.reasoning_effort,
            enable_reasoning=not args.no_reasoning,
            max_tokens=args.max_tokens,
            reference_len=0, logger=logger,
            label=f"ner_check {title} {bi}/{len(batches)}")
        if err:
            logger.error(f"  ❌ LLM: {err}")
            continue
        found = parse_ner_patches(text, logger)
        if found is None:
            logger.error(f"  ❌ Ответ не распарсился "
                         f"(сырьё — в {args.report}).")
            raws.append((title, bi, len(batches), text))
            continue
        ok_any = True
        logger.info(f"  ✔ Предложено правок: {len(found)}")
        patches.extend(found)
        emit_progress(bi, len(batches), "Проверка глоссария")
    return patches if ok_any else None


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


def review_table(entries) -> str:
    """Таблица применённых правок (для ner_changes.md): с этапом и датой."""
    lines = ["| # | Этап | Термин | Поле | Было | Стало | Причина | Когда |",
             "|---|------|--------|------|------|-------|---------|-------|"]
    for i, p in enumerate(entries, 1):
        old = p["old"].replace("|", "\\|")
        new = p["new"].replace("|", "\\|")
        reason = p["причина"].replace("|", "\\|")
        lines.append(f"| {i} | {p.get('этап', '')} | {p['term']} "
                     f"| {p['field']} | {old} | {new} | {reason} "
                     f"| {p.get('дата применения', '')} |")
    return "\n".join(lines)


def write_report(args, passes_out, raws, total_items, logger):
    header = [f"# NER-check: отчёт",
              f"",
              f"- Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
              f"- Вход: {args.input} (записей после фильтра: {total_items})",
              f"- Режим: --passes {args.passes}",
              f"- Правки: {args.review} (статусы «принять»/«отклонить»)",
              ""]
    sections = []
    for title, patches in passes_out:
        sec = [f"## {title}", ""]
        if patches is None:
            sec += ["⛔ Ошибка LLM/парсинга — см. лог и секции «СЫРЬЁ» ниже.", ""]
        elif not patches:
            sec += ["Ошибок не обнаружено.", ""]
        else:
            sec += [f"Предложено правок: {len(patches)}", "",
                    patches_table(patches), ""]
        sections.append("\n".join(sec))
    for title, bi, nb, text in raws:
        sections.append(f"## {title} (батч {bi}/{nb}) — СЫРЬЁ\n\n{text}\n")
    atomic_write(args.report, "\n".join(header) + "\n".join(sections))
    logger.info(f"📄 Отчёт: {args.report}")


def write_changes_md(entries, args, logger):
    """Лог применённых правок: все этапы накопительного файла."""
    applied = [e for e in entries if e.get("применено")]
    changes = [f"# NER-check: применённые правки",
               f"",
               f"- Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
               f"- Вход: {args.input}, правки: {args.review}",
               f"- Применено всего (все этапы): {len(applied)}",
               "", review_table(applied), ""]
    atomic_write("ner_changes.md", "\n".join(changes))
    logger.info("📄 Лог применённых правок: ner_changes.md")


def do_check(args, logger) -> int:
    base_url, api_key, model, _ = resolve_server(args, logger)
    data = load_ner_json(args.input, logger)
    exclude = [w.strip() for w in args.exclude_words.split(",") if w.strip()]
    type_filter = ([t.strip() for t in args.types.split(",") if t.strip()]
                   or None)
    items = filter_ner_items(data, args.count_threshold, type_filter, exclude)
    if not items:
        sys.exit("❌ Нет записей после фильтрации.")
    logger.info(f"📊 После фильтрации: {len(items)} записей.")

    prompt_tpl = get_prompt(args, logger)
    params = {"вход": args.input,
              "бюджет батча": args.batch_size,
              "порог count": args.count_threshold,
              "исключения notes": args.exclude_words,
              "показ aliases": bool(args.show_aliases),
              "показ votes": bool(args.show_votes),
              "промпт файл": args.prompt_file,
              "типы": args.types}
    meta, entries = load_review_file(args.review, logger)
    created = (meta or {}).get("создан") \
        or datetime.now().strftime("%Y-%m-%d %H:%M")
    if entries is None:
        entries = []
    passes_out = []                       # [(title, patches|None)]
    raws = []                             # [(title, bi, nbatches, text)]
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
            if not backed_up:
                shutil.copy2(args.input, args.input + ".bak")
                backed_up = True
                logger.info(f"💾 Бэкап: {args.input}.bak")
            atomic_write(args.input,
                         json.dumps(data, ensure_ascii=False, indent=2))
            write_changes_md(entries, args, logger)
        save_review()
        logger.info(f"Авто-применение: применено {len(applied)}, "
                    f"пропущено {skipped}.")

    def run_stage(title, subset):
        tpl = (prompt_tpl if title == "Весь глоссарий"
               else TYPES_STAGE_PREFIX + prompt_tpl)
        patches = run_pass(title, subset, tpl, args,
                           base_url, api_key, model, logger, raws)
        passes_out.append((title, patches))
        if patches is None:
            if args.auto_apply:
                write_report(args, passes_out, raws, len(items), logger)
                sys.exit(f"❌ Авто-режим: этап «{title}» не завершился "
                         f"(LLM/парсинг) — останов.")
            return
        collect(title, patches)
        if args.auto_apply:
            auto_apply()

    if args.passes in ("all", "whole"):
        run_stage("Весь глоссарий", items)
        if args.auto_apply and args.passes == "all":
            # типовые проходы — по обновлённым данным после применения
            items = filter_ner_items(data, args.count_threshold,
                                     type_filter, exclude)
    if args.passes in ("all", "types"):
        if not items:
            logger.info("ℹ После обновления нет записей для типовых "
                        "проходов.")
        else:
            all_types = sorted({i.get("type", "") for i in items
                                if i.get("type")})
            wanted = type_filter or all_types
            for t in [t for t in all_types if t in wanted]:
                subset = [i for i in items if i.get("type") == t]
                run_stage(f"Тип: {t}", subset)

    write_report(args, passes_out, raws, len(items), logger)
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
                  if e["статус"] == REVIEW_ACCEPT and not e["применено"])
    n_rej = sum(1 for e in entries if e["статус"] == REVIEW_REJECT)
    n_done = sum(1 for e in entries if e["применено"])
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
            stage = p.get("этап") or ""
            prefix = f"[{stage}] " if stage else ""
            logger.info(f"  · {prefix}{p['term']} [{p['field']}]: "
                        f"{p['old']!r} → {p['new']!r}")
        return 0
    shutil.copy2(args.input, args.input + ".bak")
    logger.info(f"💾 Бэкап: {args.input}.bak")
    atomic_write(args.input, json.dumps(data, ensure_ascii=False, indent=2))
    save_review_file(args.review, args.input,
                     (meta or {}).get("создан")
                     or datetime.now().strftime("%Y-%m-%d %H:%M"),
                     entries, meta=meta)
    write_changes_md(entries, args, logger)
    logger.info(f"✅ ner.json обновлён ({len(applied)} правок); "
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

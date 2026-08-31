#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translate_book.py — ЕДИНЫЙ скрипт LLM-обработки текста (фаза 2 рефакторинга).

Режимы (--mode):
  translate  оригинал (txt) → перевод;    trace-JSON пишется (мост для redact)
  redact     JSON-trace (original_text+translated_text) → редактура с глоссарием
  polish     перевод (txt) → полировка;   trace НЕ пишется

Авто-режим (без --mode): вход *.json → redact, иначе translate
(обратная совместимость со старыми вызовами).

ЕДИНИЦЫ: --chunk_size, --min_len_ratio-пороги и длины — СИМВОЛЫ;
max_tokens — серверный предохранитель (ТОКЕНЫ), не расчёт.

Промпты: --prompt_file БЕЗ тегов = промпт текущего режима (файл целиком);
с тегами <translate>/<redact>/<polish> — один файл на все режимы.
Плейсхолдеры: {ner_block} (все режимы); {original_text} — входной текст
(translate/polish: тег ОБЯЗАТЕЛЕН, нет тега — предупреждение в лог,
текст дописывается после промпта; redact: внутри <source_text>);
{translated_text} (только redact);
{female_names}, {male_names} — женские/мужские имена из
ner.json (поле translation; пол по наличию (female)/(male) в type), ищутся
в тексте чанка (основное назначение — polish).

Сервер: --host/--model/--api_key (CLI) > HOST/API_KEY/MODEL из .env
(единая модель скрипта — без отдельных моделей под режимы).
Ничего нет → подсказка по .env и выход.

Совместимость: redact_book.py = shim (--mode redact); все старые флаги
принимаются. core.utils не используется — только core.common.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm

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

from core.common import (
    collect_gender_names,
    determine_model,
    emit_progress,
    find_env_file,
    find_relevant_ner,
    get_server_config,
    get_stage_model,
    get_tagged_prompt,
    load_ner_data,
    load_prompt,
    log_argv,
    parse_dotenv,
    print_env_help,
    setup_logging,
    split_text_smart,
    stream_chat_completion,
    web_progress_enabled,
)

# ══════════════════════════════════════════════════════════════════════
# ВСТРОЕННЫЕ ПРОМПТЫ (fallback при отсутствии --prompt_file)
# ══════════════════════════════════════════════════════════════════════
DEFAULT_TRANSLATE_PROMPT = """<glossary>
{ner_block}
</glossary>
Translate the following segment into Russian, without additional explanation.

{original_text}
"""

DEFAULT_REDACT_PROMPT = """# Role
Ты — профессиональный литературный редактор и корректор со специализацией на художественном переводе (фэнтези, фантастика). Твоя задача — довести черновой перевод до идеального состояния, используя предоставленный глоссарий, но сохраняя здравый смысл и контекст.
Input Data Structure
Тебе будут предоставлены три блока данных:
<glossary>: Словарь именованных сущностей (NER) в формате JSON. Поля: term, translation, type, aliases (если есть).
<source_text>: Оригинальный текст на исходном языке.
<draft_translation>: Черновой перевод на русский язык.
Task
Отредактируй <draft_translation>, опираясь на контекст <source_text> и глоссарий <glossary>.
Editing Rules
1. Склонение и морфология
Если ты используешь термин из словаря, ты ОБЯЗАН склонять его по падежам, числам и родам, чтобы предложение звучало грамматически верно.
Сохраняй корень слова из словаря, меняй только окончания.
2. Грамматика и Пол (Gender)
Согласование: Проверь согласование всех слов. Если <glossary> указывает пол персонажа, все глаголы и прилагательные, относящиеся к нему, должны соответствовать этому полу.
Person (female) -> она пошла, она сказала.
Person (male) -> он пошел, он сказал.
Если пол не указан в словаре или указан с ошибкой, определи его по местоимениям (he/she) в <source_text>.
Output Format
Верни ТОЛЬКО отредактированный текст в формате plain text (не добавляй форматирование markdown и другие, если такого форматирования не было в исходном тексте <source_text>).
<glossary>
{ner_block}
</glossary>
<source_text>
{original_text}
</source_text>
<draft_translation>
{translated_text}
</draft_translation>
"""

# ══════════════════════════════════════════════════════════════════════
# ПРЕСЕТЫ РЕЖИМОВ (исторические дефолты старых скриптов)
# ══════════════════════════════════════════════════════════════════════
MODE_PRESETS = {
    #            min_len_ratio  max_retries  threads_cap  trace_default
    "translate": dict(min_len_ratio=0.5, max_retries=3,  threads_cap=16, trace_default=True),
    "redact":    dict(min_len_ratio=0.9, max_retries=3,  threads_cap=64, trace_default=False),
    "polish":    dict(min_len_ratio=0.9, max_retries=3,  threads_cap=16, trace_default=False),
}

# человекочитаемые фазы для web-прогресса (emit_progress)
MODE_LABELS = {
    "translate": "Перевод",
    "redact": "Редактура",
    "polish": "Полировка",
}

# ══════════════════════════════════════════════════════════════════════
# УПОРЯДОЧЕННАЯ ЗАПИСЬ + TRACE
# ══════════════════════════════════════════════════════════════════════
_write_lock = threading.Lock()
_next_idx = 0
# режимы, для которых уже предупреждено о пропущенном {original_text}
# (не спамим лог одним предупреждением на каждый чанк)
_warned_missing_text_tag: set[str] = set()
_pending: dict = {}
_trace: list = []
_write_mode = "translate"
_trace_on = False


def save_result_ordered(fh, idx, text, original):
    """Порядковая запись результатов при многопоточности.
    redact: rstrip+\\n (исторически); translate/polish: как есть + \\n.
    При включённом trace — накапливаем пары original/translated."""
    global _next_idx
    with _write_lock:
        _pending[idx] = (text, original)
        while _next_idx in _pending:
            t, orig = _pending.pop(_next_idx)
            if _write_mode == "redact":
                fh.write(t.rstrip() + "\n")
            else:
                fh.write(t + "\n")
            fh.flush()
            if _trace_on:
                _trace.append({
                    "chunk_id": _next_idx,
                    "original_text": orig,
                    "translated_text": t,
                })
            _next_idx += 1


# ══════════════════════════════════════════════════════════════════════
# ОБРАБОТКА ОДНОГО ЧАНКА
# ══════════════════════════════════════════════════════════════════════
def process_item(internal_id, original_text, draft_text, ctx):
    """original_text — текст для поиска NER и (в translate/polish) вход;
    draft_text — черновик (только redact)."""
    ner_block, ner_count = find_relevant_ner(
        original_text, ctx["ner_data"], ctx["ner_threshold"],
        ctx["ner_ngram"], ctx["ner_fields"],
        automaton=ctx["automaton"],
        include_aliases=ctx["include_aliases"],
    )
    # Имена по полу (по translation; основное назначение — polish).
    # В redact вход — оригинал на исходном языке, списки будут пустыми.
    female, male = collect_gender_names(
        original_text, ctx["ner_data"],
        ctx["ner_threshold"], ctx["ner_ngram"])
    female_block = "\n".join(female) if female else "(нет)"
    male_block = "\n".join(male) if male else "(нет)"
    if ctx["mode"] == "redact":
        if ner_block == "[]":
            ner_block = "(Нет специфических терминов)"
        user_content = (ctx["prompt"]
                        .replace("{ner_block}", ner_block)
                        .replace("{female_names}", female_block)
                        .replace("{male_names}", male_block)
                        .replace("{original_text}", original_text)
                        .replace("{translated_text}", draft_text or ""))
        reference = draft_text or ""
    else:
        # translate/polish: {original_text} — обязательный тег-переменная;
        # нет тега — предупреждение в лог (один раз на режим), текст
        # дописывается после промпта, чтобы перевод не сломался
        if "{original_text}" in ctx["prompt"]:
            user_content = (ctx["prompt"]
                            .replace("{ner_block}", ner_block)
                            .replace("{female_names}", female_block)
                            .replace("{male_names}", male_block)
                            .replace("{original_text}", original_text))
        else:
            mode = ctx["mode"]
            if mode not in _warned_missing_text_tag:
                _warned_missing_text_tag.add(mode)
                ctx["logger"].warning(
                    "Промпт режима %s не содержит {original_text} — "
                    "добавьте тег; текст дописан после промпта", mode)
            user_content = (ctx["prompt"]
                            .replace("{ner_block}", ner_block)
                            .replace("{female_names}", female_block)
                            .replace("{male_names}", male_block)
                            + "\n\n" + original_text)
        reference = original_text

    text, err = stream_chat_completion(
        ctx["base_url"], ctx["model"],
        [{"role": "user", "content": user_content}],
        api_key=ctx["api_key"],
        max_retries=ctx["max_retries"],
        timeout=ctx["timeout"],
        stream_timeout=ctx["stream_timeout"],
        temperature=ctx["temperature"],
        reasoning_effort=ctx["reasoning_effort"],
        min_len_ratio=ctx["min_len_ratio"],
        reference_len=len(reference),
        logger=ctx["logger"],
        label=f"[{ctx['mode']} chunk {internal_id}]",
    )
    if text is not None:
        return internal_id, text, (f"Chunk {internal_id} OK | NER: {ner_count}"
                                   f" | Имена Ж/М: {len(female)}/{len(male)}")

    if ctx["mode"] == "redact":
        fb = (f"\n\n[ОШИБКА РЕДАКТУРЫ: {err}]\n\n"
              f"<fallback>\n{draft_text}\n</fallback>\n\n")
    else:
        fb = f"\n[FAIL: {err}]\n{original_text}\n[FAIL: {err}]\n"
    return internal_id, fb, f"Chunk {internal_id} FAIL: {err}"


# ══════════════════════════════════════════════════════════════════════
# ARGPARSE (superset старых аргументов обоих скриптов)
# ══════════════════════════════════════════════════════════════════════
def build_parser():
    p = argparse.ArgumentParser(
        description="Единый скрипт LLM-обработки: перевод / редактура / полировка.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Примеры:
  %(prog)s chapter.txt --out translated.txt
  %(prog)s translated_trace.json --mode redact --out redacted.txt
  %(prog)s redacted.txt --mode polish --prompt_file prompts.txt --out polished.txt
  %(prog)s chapter.txt --host http://h:9989 --model m --chunk_size 7000

Режимы:
  translate  txt → перевод + translated_trace.json (мост для redact)
  redact     JSON-trace → редактура с глоссарием (склонение/пол)
  polish     txt → полировка; trace-файл НЕ создаётся

Промпт-файл:
  без тегов  = промпт текущего режима (файл целиком);
  с тегами   <translate>/<redact>/<polish> — один файл на все режимы.
  Плейсхолдеры: {ner_block} (все режимы),
                {original_text} — входной текст (translate/polish: тег
                  ОБЯЗАТЕЛЕН, нет — предупреждение в лог, текст
                  дописывается после промпта; redact: <source_text>),
                {translated_text} (только redact),
                {female_names}, {male_names} — женские/мужские имена из
                ner.json (translation; пол по (female)/(male) в type; без term)
                для справочника полов в polish.

Единицы:
  --chunk_size, длины и min_len_ratio — СИМВОЛЫ;
  max_tokens — серверный предохранитель (ТОКЕНЫ), не внутренний расчёт.

Сервер (приоритет): CLI --host/--model/--api_key > HOST/API_KEY/MODEL из .env
(единая модель скрипта — без отдельных моделей под режимы).
""",
    )
    p.add_argument("file", help="Вход: txt (translate/polish) или JSON-trace (redact).")
    p.add_argument("--mode", choices=["translate", "redact", "polish"], default=None,
                   help="Режим. Без флага: *.json → redact, иначе translate (legacy).")
    p.add_argument("--out", default=None,
                   help="Выходной txt. Дефолты: translate=translated_book.txt, "
                        "redact=edited_book.txt, polish=polished_book.txt.")
    # NER
    p.add_argument("--ner_file", default="ner.json", help="Путь к ner.json.")
    p.add_argument("--ner_threshold", type=float, default=0.7,
                   help="Порог схожести нечёткого поиска терминов (0.0–1.0).")
    p.add_argument("--ner_ngram", type=int, default=3,
                   help="Размер n-грамм поиска терминов (символы).")
    p.add_argument("--ner_fields", type=str, default="term,translation,type",
                   help="Поля ner.json через запятую; aliases добавляются "
                        "автоматически (отключить: --no-aliases).")
    p.add_argument("--no-aliases", action="store_true",
                   help="Не добавлять aliases в NER-блок.")
    # Промпт
    p.add_argument("--prompt_file", default=None,
                   help="Внешний промпт (теги <translate>/<redact>/<polish> "
                        "или файл целиком = промпт режима).")
    # Сервер
    p.add_argument("--host", default=None, help="URL API-сервера.")
    p.add_argument("--api_key", default=None, help="Bearer-ключ.")
    p.add_argument("--model", default=None,
                   help="Модель: --model или MODEL в .env (обязательна).")
    p.add_argument("--env_file", default=None, help="Явный путь к .env.")
    # Чанкование (символы)
    p.add_argument("--chunk_size", type=int, default=800,
                   help="Целевой размер чанка, СИМВОЛЫ (default: 800).")
    p.add_argument("--multiplier", type=float, default=1.1,
                   help="Коэффициент жёсткого лимита чанка.")
    # Генерация
    p.add_argument("--threads", type=int, default=1, help="Потоки (1..N).")
    p.add_argument("--temperature", type=float, default=None,
                   help="Температура (иначе — дефолт сервера).")
    p.add_argument("--reasoning_effort", type=str, default=None,
                   help="Усилия рассуждения: none/minimal/low/medium/"
                        "high/xhigh/max (none — отключить).")
    # Надёжность
    p.add_argument("--timeout", type=int, default=900,
                   help="Таймаут соединения, сек.")
    p.add_argument("--stream_timeout", type=int, default=900,
                   help="Таймаут стрима (сек без токенов).")
    p.add_argument("--max_retries", type=int, default=None,
                   help="Попытки на чанк. Дефолты: 3 (все режимы).")
    p.add_argument("--min_len_ratio", type=float, default=None,
                   help="Мин. отношение длины результата к входу. "
                        "Дефолты: translate=0.5, redact/polish=0.9.")
    # Trace
    p.add_argument("--trace", action=argparse.BooleanOptionalAction, default=None,
                   help="Писать *_trace.json (default: только в translate).")
    return p


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════
def main(argv=None):
    global _write_mode, _trace_on, _next_idx
    _next_idx = 0
    _pending.clear()
    _trace.clear()

    args = build_parser().parse_args(argv)
    mode = args.mode or ("redact" if args.file.lower().endswith(".json")
                         else "translate")
    preset = MODE_PRESETS[mode]
    _write_mode = mode
    _web_mode = web_progress_enabled()
    _mode_label = MODE_LABELS.get(mode, mode)

    out_path = args.out or {
        "translate": "translated_book.txt",
        "redact": "edited_book.txt",
        "polish": "polished_book.txt",
    }[mode]

    _rel = os.path.relpath(out_path)
    _log_dir = os.path.join("logs", "chapters") \
        if _rel.startswith("chapters" + os.sep) else "logs"
    try:
        os.makedirs(_log_dir, exist_ok=True)
    except OSError as exc:
        print(f"Не удалось создать {_log_dir}: {exc}")
        return 1
    _log_name = re.sub(r"[\\/]+", "_", _rel)
    logger, _ = setup_logging(os.path.join(_log_dir, _log_name))
    log_argv(logger)

    logger.info(f"🧭 Режим: {mode} | вход: {args.file} | выход: {out_path}")

    # ── Сервер: CLI > HOST/API_KEY/MODEL из .env > help+exit ──
    env_data = parse_dotenv(find_env_file(args.env_file))
    sc = get_server_config(env_data)
    host = args.host or sc["host"]
    api_key = args.api_key if args.api_key is not None else sc["api_key"]
    model = args.model or get_stage_model(env_data)
    if not host:
        print_env_help()
        sys.exit("❌ Не задан сервер: укажите --host или создайте .env (HOST).")
    if not api_key:
        api_key = os.environ.get("LLM_API_KEY", "")
    base_url = host.rstrip("/")
    if "/v1" not in base_url:
        base_url += "/v1"

    if not os.path.exists(args.file):
        logger.error("❌ Input file not found.")
        return 1  # L3 (AUDIT): отсутствующий вход = код 1, а не 0

    # ── NER ──
    ner_data, automaton = load_ner_data(args.ner_file, args.ner_ngram, logger)
    if not ner_data:
        logger.error("❌ CRITICAL: NER data is empty! Check ner.json path and format.")
    else:
        logger.info(f"✅ NER Data loaded successfully. Terms count: {len(ner_data)}")

    # ── Промпт ──
    custom = load_prompt(args.prompt_file, logger) if args.prompt_file else None
    if custom:
        tagged = get_tagged_prompt(custom, mode)
        active_prompt = tagged if tagged else custom
        if tagged:
            logger.info(f"📝 Промпт: тег <{mode}> из {args.prompt_file} "
                        f"({len(active_prompt)} симв.)")
        else:
            # файл с ТЕГАМИ, но без тега текущей стадии — предупреждение
            # и встроенный промпт вместо «файла целиком» (теги бы не
            # попали в промпт стадии)
            has_any_tag = any(
                get_tagged_prompt(custom, t) for t in
                ("translate", "redact", "polish")
            )
            if has_any_tag:
                logger.warning(
                    f"⚠️ В промпт-файле {args.prompt_file} нет тега "
                    f"<{mode}> — используется ВСТРОЕННЫЙ промпт")
                active_prompt = (DEFAULT_REDACT_PROMPT if mode == "redact"
                                 else DEFAULT_TRANSLATE_PROMPT)
            else:
                logger.info(f"📝 Промпт (файл целиком, без тегов): "
                            f"{args.prompt_file} ({len(active_prompt)} симв.)")
    else:
        active_prompt = (DEFAULT_REDACT_PROMPT if mode == "redact"
                         else DEFAULT_TRANSLATE_PROMPT)
        src = args.prompt_file if args.prompt_file else "(не указан)"
        logger.info(f"ℹ️  Внешний промпт не найден ({src}). "
                    f"Используется ВСТРОЕННЫЙ ({len(active_prompt)} симв.).")

    try:
        model_name = determine_model(model, logger)
    except SystemExit:
        return 1  # L3 (AUDIT): неопределённая модель = код 1, а не 0

    # ── Входные элементы ──
    if mode == "redact":
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                json_chunks = json.load(f)
        except Exception as e:
            logger.error(f"❌ Error reading JSON: {e}")
            return 1  # H4 (AUDIT): битый JSON — код 1, а не 0
        logger.info(f"✅ Loaded {len(json_chunks)} chunks.")
        items = [(i, c.get("original_text", ""), c.get("translated_text", ""))
                 for i, c in enumerate(json_chunks)]
    else:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                chunks = split_text_smart(f.read(), args.chunk_size,
                                          args.multiplier, logger)
        except OSError as exc:
            logger.error("❌ Не удалось прочитать вход: %s", exc)
            return 1
        items = [(i, c, None) for i, c in enumerate(chunks)]

    # ── Trace ──
    trace_path = os.path.splitext(out_path)[0] + "_trace.json"
    _trace_on = (args.trace if args.trace is not None
                 else preset["trace_default"])
    if _trace_on:
        try:
            with open(trace_path, "w", encoding="utf-8") as f:
                f.write("[]")
        except OSError as exc:
            logger.error("❌ Не удалось создать trace: %s", exc)
            return 1

    ctx = dict(
        mode=mode, ner_data=ner_data, automaton=automaton,
        ner_threshold=args.ner_threshold, ner_ngram=args.ner_ngram,
        ner_fields=args.ner_fields, include_aliases=not args.no_aliases,
        prompt=active_prompt, base_url=base_url, model=model_name,
        api_key=api_key,
        max_retries=(args.max_retries if args.max_retries is not None
                     else preset["max_retries"]),
        timeout=args.timeout, stream_timeout=args.stream_timeout,
        temperature=args.temperature, reasoning_effort=args.reasoning_effort,
        min_len_ratio=(args.min_len_ratio if args.min_len_ratio is not None
                       else preset["min_len_ratio"]),
        logger=logger,
    )

    try:
        workers = max(1, int(min(preset["threads_cap"], args.threads)))
    except (TypeError, ValueError):
        workers = 1
    try:
        with open(out_path, "w", encoding="utf-8"):
            pass  # очистка
        fh = open(out_path, "a", encoding="utf-8")
    except OSError as exc:
        logger.error("❌ Не удалось открыть выход: %s", exc)
        return 1
    with fh:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(process_item, i, orig, draft, ctx): i
                    for i, orig, draft in items}
            pbar = tqdm(total=len(items), unit="chunk", disable=_web_mode)
            # свой счётчик — pbar.n мёртв при disable=True
            done = 0
            # стартовое событие прогресса — бар и «📊» видны
            # сразу, до первого завершённого чанка (медленный LLM)
            emit_progress(done, len(items), _mode_label)
            if _web_mode:
                logger.info(f"📊 Прогресс: {done}/{len(items)}")
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    res_idx, text, info = fut.result()
                    if "FAIL" in info:
                        logger.warning(info)
                        tqdm.write(f"⚠️ {info}")
                    else:
                        logger.info(info)
                    save_result_ordered(fh, res_idx, text, items[i][1])
                except Exception as e:
                    logger.error(f"Error chunk {i}: {e}")
                    if mode == "redact":
                        fb = (f"\n\n[ОШИБКА РЕДАКТУРЫ: {e}]\n\n"
                              f"<fallback>\n{items[i][2]}\n</fallback>\n\n")
                    else:
                        fb = f"\n[FAIL: {e}]\n{items[i][1]}\n[FAIL: {e}]\n"
                    save_result_ordered(fh, i, fb, items[i][1])
                finally:
                    pbar.update(1)
                    done += 1
                    emit_progress(done, len(items), _mode_label)
                    # «📊» в текстовый лог каждые 10 чанков
                    if _web_mode and done % 10 == 0:
                        logger.info(f"📊 Прогресс: {done}/{len(items)}")
            pbar.close()

    if _trace_on and _trace:
        tmp = trace_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as tf:
                json.dump(_trace, tf, ensure_ascii=False, indent=2)
            os.replace(tmp, trace_path)
        except OSError as exc:
            logger.error("❌ Не удалось записать trace: %s", exc)
            return 1
        logger.info(f"💾 Trace: {len(_trace)} чанков")


if __name__ == "__main__":
    sys.exit(main())  # H4 (AUDIT): код возврата main() без подмены на 0

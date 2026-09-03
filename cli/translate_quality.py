#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translate_quality.py — оценка качества перевода (LLM).

Один LLM-запрос по пакету глав из указанного диапазона: собираются
{original_text} (chapter.txt) и {translated_text} (выбранный «Тип файлов
глав»), подставляются в промпт и отправляются на оценку. Если главы +
промпт не влезают в --budget (СИМВОЛЫ) — пакет обрезается до ЦЕЛОГО
количества глав (первые N диапазона). Результат — Markdown-отчёт
(по умолчанию translation_quality_assessment.md): техническая шапка
(дата, диапазон, пакет, бюджет, модель) + текст оценки LLM.

Промпт-файл: тег <prompt_assessment> (между тегами можно писать
комментарии — код берёт содержимое тега); файл без тегов — целиком.
Плейсхолдеры: {original_text}, {translated_text}.

ЕДИНИЦЫ: --budget и размеры пакета — СИМВОЛЫ; max_tokens (32768) —
серверный предохранитель, ТОКЕНЫ. Форматы папок глав — единый канон
core.common.parse_chapter_id.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path


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
    build_chapter_map,
    determine_model,
    emit_progress,
    find_chapter_file,
    find_env_file,
    get_server_config,
    get_tagged_prompt,
    load_prompt,
    log_argv,
    parse_dotenv,
    print_env_help,
    read_text_safe,
    setup_logging,
    stream_chat_completion,
    web_progress_enabled,
)

DEFAULT_OUTPUT = "translation_quality_assessment.md"
DEFAULT_BUDGET = 200_000  # СИМВОЛЫ: главы + промпт

# ──────────────────────────────────────────────
# ПРОМПТ
# ──────────────────────────────────────────────
DEFAULT_PROMPT = """\
Ты — профессиональный редактор и критик качества художественного \
перевода (китайская веб-новелла → русский).

Тебе даны оригинал ({original_text}) и перевод ({translated_text}) \
нескольких глав. Оцени качество перевода.

В отчёте укажи:
1. Общую оценку (0–10) и её обоснование (2–4 предложения).
2. Сильные стороны перевода.
3. Слабые стороны: точность, стиль, читаемость, терминология, \
ошибки/опечатки — с примерами.
4. Рекомендации по улучшению (конкретные, по приоритету).

СТРОГИЕ ПРАВИЛА:
- Пиши ТОЛЬКО на русском, только Markdown.
- Не выдумывай фактов, которых нет в тексте.
- Не пересказывай сюжет — оценивай качество перевода.
- Не упоминай count/частоту/количество символов в оценке.
"""


def load_assessment_prompt(filepath, logger) -> str:
    """Промпт оценки: тег <prompt_assessment> из файла (между тегами
    можно писать комментарии); файл без тегов — целиком; None/пусто —
    встроенный дефолт."""
    if filepath and os.path.isfile(filepath):
        content = read_text_safe(filepath)
        tagged = get_tagged_prompt(content, "prompt_assessment")
        if tagged:
            return tagged
        if content.strip():
            return content.strip()
    if logger:
        logger.info("Промпт: встроенный дефолт "
                    "(файл не задан или пуст)")
    return DEFAULT_PROMPT


# ──────────────────────────────────────────────
# СБОР ГЛАВ
# ──────────────────────────────────────────────
def resolve_range(args, chapter_map, logger) -> None:
    """Диапазон: явные --start/--end или автодиапазон по найденным."""
    auto_min = min(chapter_map)
    auto_max = max(chapter_map)
    args.start = args.start if args.start is not None else auto_min
    args.end = args.end if args.end is not None else auto_max
    if args.start > args.end:
        sys.exit("Ошибка: Начальная глава не может быть больше конечной.")
    logger.info(f"Диапазон глав: {args.start} – {args.end}")


def collect_chapters(start, end, file_type, chapter_map, logger):
    """(num, text) по порядку диапазона; пропущенные — счётчиком."""
    chapters: list[tuple[int, str]] = []
    missing = 0
    for i in range(start, end + 1):
        paths = chapter_map.get(i) or []
        if not paths:
            missing += 1
            continue
        dir_path = paths[-1]
        fp, msgs = find_chapter_file(dir_path, i, want=file_type,
                                     logger=logger)
        if not fp:
            missing += 1
            continue
        text = read_text_safe(fp)
        if text:
            chapters.append((i, text))
    if logger:
        logger.info(f"Собрано глав: {len(chapters)} "
                    f"(пропущено: {missing}) | тип: {file_type}")
    return chapters


def fit_budget(chapters, orig_by_num: dict, prompt: str, budget: int):
    """Обрезка до ЦЕЛОГО количества глав: главы (оригинал + перевод)
    + промпт ≤ budget. Возвращает (kept, dropped): kept — список
    (num, text), dropped — число отсечённых глав (первые N диапазона).
    """
    available = budget - len(prompt)
    if available <= 0:
        sys.exit(f"Ошибка: промпт ({len(prompt)} симв.) уже больше "
                 f"бюджета ({budget} симв.) — увеличьте --budget.")
    total = sum(len(t) + len(orig_by_num.get(n, ""))
                for n, t in chapters)
    if total <= available:
        return chapters, 0
    kept: list[tuple[int, str]] = []
    size = 0
    for item in chapters:
        s = len(item[1]) + len(orig_by_num.get(item[0], ""))
        if size + s <= available:
            kept.append(item)
            size += s
        else:
            break
    return kept, len(chapters) - len(kept)


def build_user_prompt(template: str, original_text: str,
                      translated_text: str) -> str:
    """Подстановка {original_text}/{translated_text} (NFC)."""
    import unicodedata
    out = template
    out = out.replace("{original_text}",
                      unicodedata.normalize("NFC", original_text))
    out = out.replace("{translated_text}",
                      unicodedata.normalize("NFC", translated_text))
    return out


# ──────────────────────────────────────────────
# ЗАПРОС К LLM
# ──────────────────────────────────────────────
def llm_request(user_content, base_url, model, api_key,
                max_retries, timeout, temperature, reasoning_effort,
                logger) -> str | None:
    """Единый стрим core.common.stream_chat_completion
    ([DONE]/finish_reason, loop-детект, cut, empty — одна гигиена).
    max_tokens=32768 — серверный предохранитель, ТОКЕНЫ."""
    text, _err = stream_chat_completion(
        base_url, model,
        [{"role": "user", "content": user_content}],
        api_key=api_key,
        max_retries=max_retries,
        timeout=timeout,
        stream_timeout=timeout,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_tokens=32768,
        logger=logger,
        label="[quality]",
    )
    return text


# ──────────────────────────────────────────────
# ОТЧЁТ
# ──────────────────────────────────────────────
def build_report(meta: dict, assessment: str) -> str:
    """Markdown-отчёт: техническая шапка + оценка LLM."""
    requested = meta["range_requested"]
    included = meta["range_included"]
    range_desc = f"{included[0]} – {included[1]}"
    if requested != included:
        range_desc += (f" (из запрошенных {requested[0]} – {requested[1]}; "
                       f"отсечено {meta['dropped']} глав бюджетом)")
    rows = [
        ("Дата", meta["date"]),
        ("Диапазон глав", range_desc),
        ("Включено глав", str(meta["chapters"])),
        ("Тип файлов глав", meta["file_type"]),
        ("Бюджет запроса", f"{meta['budget']:,} символов".replace(",", " ")),
        ("Размер пакета",
         f"{meta['packet_size']:,} символов".replace(",", " ")
         + " (главы + промпт)"),
        ("Модель", meta["model"]),
        ("Сервер", meta["host"]),
        ("Промпт-файл", meta["prompt_file"] or "встроенный"),
    ]
    table = "\n".join(f"| {k} | {v} |" for k, v in rows)
    head = (
        "# Оценка качества перевода\n\n"
        f"| Параметр | Значение |\n| --- | --- |\n{table}\n\n"
        "---\n\n## Оценка\n\n"
    )
    return head + assessment.strip() + "\n"


def write_report(output_path: str, meta: dict, assessment: str,
                 logger) -> None:
    """Атомарная запись md-отчёта + консольная ссылка."""
    text = build_report(meta, assessment)
    parent = os.path.dirname(os.path.abspath(output_path))
    try:
        os.makedirs(parent, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        logger.error(f"❌ Не удалось записать отчёт {output_path}: {exc}")
        return
    logger.info(f"✅ Отчёт: {os.path.abspath(output_path)}")
    print(f"Отчёт: {os.path.abspath(output_path)}")


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Оценка качества перевода (LLM) — один запрос по "
                    "пакету глав.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Единицы: --budget и размеры пакета — СИМВОЛЫ (главы + промпт);
max_tokens (32768) — серверный предохранитель, ТОКЕНЫ.
Промпт-файл: тег <prompt_assessment>; плейсхолдеры {original_text}
(chapter.txt) и {translated_text} (тип файлов глав).
Сервер: --host/--model/--api_key (CLI) > HOST/API_KEY/MODEL из .env
(модель: TRANSLATE_QUALITY_MODEL → MODEL).
Примеры:
  %(prog)s --type polished --start 1 --end 50
  %(prog)s --prompt_file prompts/translate_quality_prompt.txt --budget 200000
""",
    )
    # Сервер
    parser.add_argument("--host", default=None,
                        help="URL API-сервера (пусто = HOST из .env).")
    parser.add_argument("--model", default=None,
                        help="Модель: --model или MODEL/TRANSLATE_QUALITY_MODEL "
                             "в .env.")
    parser.add_argument("--api_key", default=None,
                        help="Bearer-ключ (пусто = API_KEY из .env).")
    parser.add_argument("--env_file", default=None,
                        help="Явный путь к .env.")
    # Главы
    parser.add_argument("--chapters-dir", dest="chapters_dir",
                        default="./chapters",
                        help="Папка глав (default: ./chapters).")
    parser.add_argument("--type", default="polished",
                        choices=["chapter", "translated", "redacted",
                                 "polished"],
                        help="Тип файлов глав: chapter/translated/redacted/"
                             "polished (default: polished).")
    parser.add_argument("--start", type=int, default=None,
                        help="Начальная глава (иначе автодиапазон).")
    parser.add_argument("--end", type=int, default=None,
                        help="Конечная глава (иначе автодиапазон).")
    # Промпт и выход
    parser.add_argument("--prompt_file", default=None,
                        help="Промпт-файл (тег <prompt_assessment>; без "
                             "тега — файл целиком; пусто = встроенный).")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"Выходной md-отчёт (default: "
                             f"{DEFAULT_OUTPUT}).")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                        help=f"Бюджет запроса, СИМВОЛЫ: главы + промпт; "
                             f"если не влезает — пакет обрезается до "
                             f"целого количества глав (default: "
                             f"{DEFAULT_BUDGET}).")
    # LLM
    parser.add_argument("--timeout", type=int, default=300,
                        help="Таймаут запроса, сек (default: 300).")
    parser.add_argument("--max_retries", type=int, default=3,
                        help="Повторы при ошибке LLM (default: 3).")
    parser.add_argument("--temperature", type=float, default=None,
                        help="Температура (иначе дефолт сервера).")
    parser.add_argument("--reasoning_effort", default=None,
                        choices=["none", "minimal", "low", "medium", "high",
                                 "xhigh", "max"],
                        help="Усилия рассуждения модели: none/minimal/low/"
                             "medium/high/xhigh/max (пусто = сервер; "
                             "none — отключить).")
    args = parser.parse_args()

    try:
        os.makedirs("logs", exist_ok=True)
    except OSError as exc:
        print(f"Не удалось создать папку logs/: {exc}")
        return 1
    logger, _ = setup_logging(
        os.path.join("logs", "translate_quality.log"))
    log_argv(logger)

    # ── Сервер: CLI > HOST/API_KEY/MODEL из .env > help+exit ──
    env_data = parse_dotenv(find_env_file(args.env_file))
    sc = get_server_config(env_data, "translate_quality")
    host = args.host or sc["host"]
    api_key = args.api_key if args.api_key is not None else sc["api_key"]
    model = args.model or sc["model"]
    if not api_key:
        api_key = os.environ.get("LLM_API_KEY", "")
    if not host:
        print_env_help()
        sys.exit("❌ Не задан сервер: укажите --host или создайте .env (HOST).")
    base_url = host.rstrip("/")
    if "/v1" not in base_url:
        base_url += "/v1"
    model_name = determine_model(model, logger)

    logger.info(f"API: {base_url} | модель: {model_name} | "
                f"бюджет: {args.budget} симв. | тип: {args.type}")

    # ── Главы ──
    ch_dir = os.path.abspath(args.chapters_dir)
    chapter_map = build_chapter_map(ch_dir, logger=logger)
    if not chapter_map:
        logger.error(f"❌ В '{args.chapters_dir}' не найдено глав.")
        return 1
    resolve_range(args, chapter_map, logger)
    chapters = collect_chapters(args.start, args.end, args.type,
                                chapter_map, logger)
    if not chapters:
        logger.error("❌ Главы не найдены (тип файлов или диапазон).")
        return 1

    # ── Оригиналы (chapter.txt) для бюджета и подстановки ──
    orig_by_num: dict[int, str] = {}
    for num, _text in chapters:
        paths = chapter_map.get(num) or []
        dir_path = paths[-1] if paths else None
        if not dir_path:
            continue
        orig, _msgs = find_chapter_file(dir_path, num, want="chapter",
                                        logger=logger)
        if orig:
            t = read_text_safe(orig)
            if t:
                orig_by_num[num] = t if t.endswith("\n") else t + "\n"

    # ── Промпт + бюджет (главы: оригинал + перевод + промпт ≤ budget) ──
    prompt = load_assessment_prompt(args.prompt_file, logger)
    kept, dropped = fit_budget(chapters, orig_by_num, prompt, args.budget)
    if not kept:
        logger.error("❌ Ни одна глава не влезает в бюджет "
                     f"({args.budget} симв.).")
        return 1
    if dropped:
        logger.warning(f"⚠️ Бюджет: отсечено {dropped} глав "
                       f"(включено {len(kept)} из {len(chapters)}).")
    nums = [n for n, _ in kept]

    original_parts = [orig_by_num[n] for n, _ in kept
                      if n in orig_by_num]
    translated_parts = []
    for num, text in kept:
        translated_parts.append(text if text.endswith("\n")
                                else text + "\n")

    original_text = "\n".join(original_parts)
    translated_text = "\n".join(translated_parts)
    user_content = build_user_prompt(prompt, original_text, translated_text)
    packet_size = len(user_content)

    if web_progress_enabled():
        emit_progress(0, 1, "Оценка перевода")
    logger.info(f"Пакет: глав {nums[0]}–{nums[-1]} ({len(nums)}), "
                f"размер {packet_size:,} симв.".replace(",", " "))

    # ── LLM ──
    print(f"Запрос к LLM ({model_name}) по главам {nums[0]}–{nums[-1]}…")
    assessment = llm_request(
        user_content, base_url, model_name, api_key,
        args.max_retries, args.timeout, args.temperature,
        args.reasoning_effort, logger)
    if web_progress_enabled():
        emit_progress(1, 1, "Оценка перевода")
    if not assessment:
        logger.error("❌ LLM вернул пустой ответ.")
        return 1

    # ── Отчёт ──
    meta = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "range_requested": (args.start, args.end),
        "range_included": (nums[0], nums[-1]),
        "chapters": len(nums),
        "dropped": dropped,
        "file_type": args.type,
        "budget": args.budget,
        "packet_size": packet_size,
        "model": model_name,
        "host": base_url,
        "prompt_file": args.prompt_file or "",
    }
    write_report(args.output, meta, assessment, logger)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nПрервано пользователем.")
        sys.exit(130)

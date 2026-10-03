#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translate_quality.py — оценка качества перевода (LLM).

Два режима (--mode):

* range (по умолчанию) — ОДИН запрос по пакету глав диапазона: собираются
  {original_text} (chapter.txt) и {translated_text} (выбранный «Тип файлов
  глав»). Если главы не влезают в --budget (ТОКЕНЫ, оценка) — пакет обрезается
  до ЦЕЛОГО количества глав (первые N диапазона), отсечённые видны в отчёте.
* chunks — книга режется на чанки по N ЦЕЛЫХ глав (--chunk_size, ГЛАВЫ),
  каждый чанк оценивается отдельным запросом (--threads потоков), затем
  отчёты чанков сворачиваются LLM в итоговое заключение. Глава крупнее бюджета
  режется по абзацам и становится несколькими чанками одной главы; глава,
  которая не влезла даже одной частью, остаётся в «больше бюджета даже одной
  частью». Промежуточные
  сводки режутся тем же бюджетом уровнями, пока не соберётся один запрос
  (--summary-only перечитывает tmp/quality/ и сводит без новых запросов).

Целостность книги — приоритет над «красивым» числом запросов: глава никогда не
рвётся посередине, если влезает целиком.

Отчёты чанков — tmp/quality/ (новый запуск затирает каталог: диапазон и бюджет
между запусками меняются, старьё исказило бы сводку). Итог —
tmp/translation_quality_assessment.md (в web имя фиксировано; CLI может
переопределить --output).

Промпт-файл: тег <prompt_assessment> (между тегами можно писать комментарии —
код берёт содержимое тега); файл без тегов — целиком. Промпт свёртки — тег
<prompt_assessment_summary> с плейсхолдером {batch_text}.

Плейсхолдеры: {original_text}, {translated_text}, {batch_text} (свёртка).

ЕДИНИЦЫ: --budget и размеры пакета — ТОКЕНЫ (оценка estimate_tokens);
--chunk_size и --overlap — ГЛАВЫ; max_tokens — серверный предохранитель, ТОКЕНЫ.
Форматы папок глав — единый канон core.common.parse_chapter_id.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime


def _bootstrap_core() -> None:
    """Скрипт запускается из любого cwd: корень репо ищется подъёмом от себя.

    Там же проверяются обязательные зависимости: httpx — транспорт
    core.transport, dotenv — парсер .env; сам скрипт их не зовёт, но
    core.common без них не импортируется (офлайн-установка — wheels из
    vendor/, см. packaging/README.md).
    """
    from importlib.util import find_spec
    from pathlib import Path as _P
    p = _P(os.path.dirname(os.path.abspath(__file__)))
    for _ in range(6):
        if (p / "core" / "common.py").is_file():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
            break
        if p.parent == p:
            break
        p = p.parent
    missing = [m for m in ("httpx", "dotenv") if find_spec(m) is None]
    if missing:
        print("❌ Требуется: " + ", ".join(missing)
              + " — python3 -m pip install -r requirements.txt")
        sys.exit(1)


_bootstrap_core()

from core.stage import (  # noqa: E402
    Progress,
    add_llm_args,
    setup_stage,
)
from core import settings as core_settings  # noqa: E402
from core.common import (  # noqa: E402
    atomic_write,
    build_chapter_map,
    estimate_tokens,
    find_chapter_file,
    get_tagged_prompt,
    llm_messages,
    log_argv,
    preview_logger,
    preview_request_payload,
    read_text_safe,
    split_text_smart,
    trim_to_tokens,
    write_preview_request,
)

DEFAULT_OUTPUT = "tmp/translation_quality_assessment.md"  # web: фиксирован
DEFAULT_BUDGET = 65_000  # ТОКЕНОВ (оценка): главы (содержимое; промпт не входит)
DEFAULT_MODE = "range"
DEFAULT_CHUNK_SIZE = 1     # ГЛАВЫ в чанке
DEFAULT_CHUNKS = 0         # 0 — все чанки
DEFAULT_SAMPLE = "uniform"
DEFAULT_OVERLAP = 0        # ГЛАВЫ
DEFAULT_THREADS = 4

CHUNK_DIR = "tmp/quality"  # артефакты чанков: chunk-<NNN>.json, summary-l<k>-<i>.json
CHUNK_FILE_RE = None       # заполняется после импорта re (см. ниже)

# машина разбирает баллы только из блока фиксированной формы в конце ответа
MARK_OPEN, MARK_CLOSE = "<<<QUALITY>>>", "<<<END>>>"

QUOTE_MAX_CHARS = 160      # СИМВОЛЫ: цитата замечания длиннее — с …
NOTE_MAX_CHARS = 400       # СИМВОЛЫ: заметка длиннее — с …
MAX_REDUCE_LEVELS = 3      # уровней сжатия перед принудительной обрезкой
MAX_REDUCE_REQUESTS = 60   # потолок запросов свёртки на один запуск
LEVEL_NAMES = {0: "полные отчёты", 1: "без цитат", 2: "только баллы"}
SECTION_ORDER = ("точность", "стиль", "читаемость", "терминология", "ошибки")

# ──────────────────────────────────────────────
# ПРОМПТ
# ──────────────────────────────────────────────
DEFAULT_PROMPT = """\
<system>
Ты — профессиональный редактор и критик качества художественного \
перевода (китайская веб-новелла → русский).

Тебе даны оригинал и перевод нескольких глав. Оцени качество перевода.

Оценка строится вокруг пяти разделов: точность, стиль, читаемость, \
терминология, ошибки.

СТРОГИЕ ПРАВИЛА:
- Пиши ТОЛЬКО на русском, только Markdown.
- Не выдумывай фактов, которых нет в тексте.
- Не пересказывай сюжет — оценивай качество перевода.
- Не упоминай count/частоту/количество символов в оценке.
- Каждое замечание — с номером главы.
</system>
<user>
## ОРИГИНАЛ

{original_text}

## ПЕРЕВОД

{translated_text}

Дай короткий вывод (3–5 предложений) и список замечаний.

В самом конце добавь блок строго в таком виде — машина разбирает только его:
<<<QUALITY>>>
{"score": 8.5,
 "sections": {"точность": 9, "стиль": 8, "читаемость": 8, "терминология": 7, "ошибки": 9},
 "strengths": [{"type": "стиль", "chapter": 1, "note": "что получилось хорошо"}],
 "issues": [{"type": "терминология", "chapter": 1, "quote": "фрагмент перевода", "note": "что не так"}]}
<<<END>>>
Баллы — числа 0–10. В strengths и issues — не больше трёх примеров на тип.
</user>
"""

# свёртка отчётов чанков в итоговое заключение (тег <prompt_assessment_summary>)
DEFAULT_SUMMARY_PROMPT = """\
<system>
Ты — профессиональный редактор и критик качества художественного перевода.
Тебе даны сводки оценки качества по частям книги: у каждой части общий балл,
баллы по разделам и замечания. Составь итоговое заключение по всей книге.

РАЗДЕЛЫ те же: точность, стиль, читаемость, терминология, ошибки.

СТРОГИЕ ПРАВИЛА:
- Пиши ТОЛЬКО на русском, только Markdown.
- Опирайся только на приведённые сводки, ничего не выдумывай.
- Номера глав приводи как в сводках.
- Не пересказывай сюжет — оценивай качество перевода.
- Не упоминай токены, лимиты, чанки, пакет и технику расчёта.
</system>
<user>
{batch_text}

СТРУКТУРА ОТВЕТА:
1. Общую оценку (0–10) и её обоснование (2–4 предложения).
2. Сильные стороны перевода.
3. Слабые стороны: точность, стиль, читаемость, терминология, \
ошибки/опечатки — с примерами.
4. Рекомендации по улучшению (конкретные, по приоритету).
</user>
"""


def load_assessment_prompt(filepath, logger, tag: str = "prompt_assessment",
                           default: str = DEFAULT_PROMPT) -> str:
    """Промпт оценки: тег `tag` из файла (между тегами можно писать
    комментарии); файл без тегов — целиком; None/пусто — встроенный дефолт."""
    if filepath and os.path.isfile(filepath):
        content = read_text_safe(filepath)
        tagged = get_tagged_prompt(content, tag)
        if tagged:
            return tagged
        if content.strip():
            return content.strip()
    if logger:
        logger.info(f"Промпт {tag}: встроенный дефолт "
                    "(файл не задан или пуст)")
    return default


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


def collect_originals(chapters, chapter_map, logger) -> dict[int, str]:
    """{num: текст оригинала (chapter.txt)} — для бюджета и {original_text}."""
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
    return orig_by_num


def fit_budget(chapters, orig_by_num: dict, prompt: str, budget: int):
    """Обрезка до ЦЕЛОГО количества глав: главы (оригинал + перевод)
    ≤ budget (промпт НЕ вычитается — бюджет только на содержимое).
    Возвращает (kept, dropped): kept — список (num, text), dropped —
    число отсечённых глав (первые N диапазона).
    """
    available = budget
    total = sum(estimate_tokens(t) + estimate_tokens(orig_by_num.get(n, ""))
                for n, t in chapters)
    if total <= available:
        return chapters, 0
    kept: list[tuple[int, str]] = []
    size = 0
    for item in chapters:
        s = (estimate_tokens(item[1])
             + estimate_tokens(orig_by_num.get(item[0], "")))
        if size + s <= available:
            kept.append(item)
            size += s
        else:
            break
    return kept, len(chapters) - len(kept)


def build_user_prompt(template: str, original_text: str,
                      translated_text: str) -> str:
    """Подстановка {original_text}/{translated_text} (NFC)."""
    out = template
    out = out.replace("{original_text}",
                      unicodedata.normalize("NFC", original_text))
    out = out.replace("{translated_text}",
                      unicodedata.normalize("NFC", translated_text))
    return out


# ══════════════════════════════════════════════════════════════════════
# РЕЖИМ chunks: ЧАНКИ ПО ЦЕЛЫМ ГЛАВАМ
# ══════════════════════════════════════════════════════════════════════

def chunk_label(nums: list[int], part=None) -> str:
    """Подпись чанка в отчёте: «главы 5–6» или «глава 7, часть 2/3»."""
    if part and len(part) == 2 and part[1] > 1:
        return f"глава {nums[0]}, часть {part[0]}/{part[1]}"
    if len(nums) == 1:
        return f"глава {nums[0]}"
    return f"главы {nums[0]}–{nums[-1]}"


def chunk_content(chunk: dict, orig_by_num: dict) -> tuple[str, str]:
    """(оригинал, перевод) чанка — целыми главами.

    У чанка-части главы оригинал свой (свой кусок chapter.txt), иначе каждая
    часть таскала бы по главе оригинала за собой.
    """
    if chunk.get("orig") is not None:
        return chunk["orig"], "".join(t for _n, t in chunk["items"])
    nums = chunk["nums"]
    original = "\n".join(orig_by_num[n] for n in nums if n in orig_by_num)
    translated = "\n".join(t if t.endswith("\n") else t + "\n"
                          for _n, t in chunk["items"])
    return original, translated


def chunk_tokens(chunk: dict, orig_by_num: dict) -> int:
    """Размер содержимого чанка, ТОКЕНЫ (оценка estimate_tokens)."""
    original, translated = chunk_content(chunk, orig_by_num)
    return estimate_tokens(original) + estimate_tokens(translated)


def build_chunks(chapters, chunk_size: int = DEFAULT_CHUNK_SIZE,
                 overlap: int = DEFAULT_OVERLAP) -> list[dict]:
    """Главы → чанки по chunk_size ЦЕЛЫХ глав (ГЛАВЫ), шаг = size − overlap.

    Перекрытие тоже целыми главами: соседняя глава входит в чанк целиком.
    """
    size = max(1, int(chunk_size))
    step = max(1, size - max(0, int(overlap)))
    out: list[dict] = []
    for start in range(0, len(chapters), step):
        group = chapters[start:start + size]
        if not group:
            break
        out.append({"id": len(out) + 1, "nums": [n for n, _ in group],
                    "part": None, "items": group})
        if start + size >= len(chapters):
            break
    return out


def merge_small_parts(parts: list[str], budget: int) -> list[str]:
    """Куски меньше 1/10 бюджета вклеиваются в предыдущий: запроса на 5 токенов
    не должно быть, если соседний кусок ещё влезает в бюджет."""
    floor = max(1, budget // 10)
    out: list[str] = []
    for p in parts:
        if not p.strip():
            continue
        if (out and estimate_tokens(p) < floor
                and estimate_tokens(out[-1]) + estimate_tokens(p) <= budget):
            out[-1] = out[-1] + p
        else:
            out.append(p)
    return out


def refit_oversize(chunks, orig_by_num: dict, budget: int, logger):
    """Чанк больше бюджета → его главы режутся по абзацам, часть = свой чанк.

    Возвращает (чанки с проставленными id, сколько глав разрезано, номера глав,
    что остались больше бюджета даже одной частью). Режем главу только когда
    она одна не влезает.

    Оригинал режется тем же target и собирается с частями перевода по номеру
    части: часть перевода идёт в запрос со своим куском оригинала, а не со всей
    главой.
    """
    out: list[dict] = []
    split_ch, oversize = 0, []
    for ch in chunks:
        if chunk_tokens(ch, orig_by_num) <= budget:
            out.append(ch)
            continue
        split_ch += 1
        # в чанк части идут И перевод, И оригинал: целевая часть — половина бюджета
        per = max(1, budget // (2 * max(1, len(ch["items"]))))
        for num, text in ch["items"]:
            # multiplier=1.0: hard_limit = target, иначе часть заведомо больше
            # бюджета и запрос всё равно не отправился бы
            parts = merge_small_parts(
                split_text_smart(text, target_tokens=per, multiplier=1.0), budget)
            otext = orig_by_num.get(num, "")
            oparts = merge_small_parts(
                split_text_smart(otext, target_tokens=per, multiplier=1.0),
                budget) if otext else []
            total = max(len(parts), len(oparts), 1)
            for k in range(total):
                sub = {"id": 0, "nums": [num],
                       "items": [(num, parts[k] if k < len(parts) else "")],
                       "orig": oparts[k] if k < len(oparts) else "",
                       "part": (k + 1, total) if total > 1 else None}
                if chunk_tokens(sub, orig_by_num) > budget and num not in oversize:
                    oversize.append(num)
                out.append(sub)
    out = [c for c in out if c["items"][0][1].strip()]
    for i, c in enumerate(out, 1):
        c["id"] = i
    logger.info(f"✂️ Чанки: разрезано по абзацам {split_ch} глав "
                f"(бюджет {budget:,} токенов), чанков стало {len(out)}"
                .replace(",", " "))
    if oversize:
        logger.warning(f"⚠️ Больше бюджета даже одной частью: главы {oversize}")
    return out, split_ch, oversize


def select_chunks(chunks, count: int = DEFAULT_CHUNKS,
                  sample: str = DEFAULT_SAMPLE) -> tuple[list[dict], str]:
    """Сколько чанков идёт в оценку: 0 = все; uniform — равномерно по книге."""
    n = int(count or 0)
    if n <= 0 or n >= len(chunks):
        return chunks, "все"
    if sample != "uniform" or n == 1:
        return chunks[:n], "первые по порядку"
    last = len(chunks) - 1
    idx = sorted({round(i * last / (n - 1)) for i in range(n)})
    return [chunks[i] for i in idx], "равномерно по книге"


# ══════════════════════════════════════════════════════════════════════
# РАЗБОР ОТВЕТА (шкала 0–10 живёт в промпте, не в коде)
# ══════════════════════════════════════════════════════════════════════

def _score(value) -> float | None:
    """Балл: первое число, шкала крупнее 10 переводится в 0–10."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
    else:
        m = re.search(r"\d+(?:[.,]\d+)?", str(value))
        if not m:
            return None
        v = float(m.group(0).replace(",", "."))
    if v > 10:
        v = v / 10.0
    return round(max(0.0, min(10.0, v)), 2)


def _clip(text, limit: int) -> str:
    """Обрезка по СИМВОЛЫ с многоточием (цитаты и заметки в отчёте)."""
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[:max(1, limit - 1)].rstrip() + "…"


def _chapter_num(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    m = re.search(r"\d+", str(value))
    return int(m.group(0)) if m else None


def _finding(item) -> dict | None:
    """Одно замечание/похвала: dict или строка → {type, chapter, quote, note}."""
    if isinstance(item, str):
        s = _clip(item, NOTE_MAX_CHARS)
        return ({"type": "", "chapter": None, "quote": "", "note": s}
                if s else None)
    if not isinstance(item, dict):
        return None
    note = _clip(item.get("note") or item.get("текст"), NOTE_MAX_CHARS)
    quote = _clip(item.get("quote") or item.get("цитата"), QUOTE_MAX_CHARS)
    if not note and not quote:
        return None
    chapter = item.get("chapter")
    if chapter is None:
        chapter = item.get("глава")
    return {"type": str(item.get("type") or item.get("тип") or "").strip(),
            "chapter": _chapter_num(chapter), "quote": quote, "note": note}


def normalize_quality_data(data: dict) -> dict:
    """Баллы и замечания модели к единому виду; ничего не досочиняем."""
    out: dict = {"score": _score(data.get("score")), "sections": {},
                 "strengths": [], "issues": []}
    sections = data.get("sections") or data.get("разделы") or {}
    if isinstance(sections, dict):
        for key, val in sections.items():
            s = _score(val)
            if s is not None:
                out["sections"][str(key).strip()] = s
    elif isinstance(sections, list):
        for item in sections:
            if not isinstance(item, dict):
                continue
            name = str(item.get("type") or item.get("тип") or "").strip()
            s = _score(item.get("score") or item.get("балл"))
            if name and s is not None:
                out["sections"][name] = s
    for key in ("strengths", "issues"):
        raw = data.get(key)
        if isinstance(raw, list):
            for item in raw:
                f = _finding(item)
                if f:
                    out[key].append(f)
    return out


def parse_quality_answer(text: str) -> tuple[str, dict | None]:
    """Ответ модели → (текст для отчёта, разобранные данные | None).

    Блок `<<<QUALITY>>> {json} <<<END>>>` — машина разбирает только его, весь
    остальной текст модели остаётся в отчёте как есть. Нет блока/битый JSON —
    только текст, разбора нет (в отчёте это честно показано).
    """
    if not text:
        return "", None
    m = re.search(re.escape(MARK_OPEN) + r"(.*?)" + re.escape(MARK_CLOSE),
                  text, re.S)
    if not m:
        return text.strip(), None
    body = m.group(1).strip()
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", body).strip()
    try:
        data = json.loads(body)
    except ValueError:
        return text.strip(), None
    if not isinstance(data, dict):
        return text.strip(), None
    clean = (text[:m.start()] + text[m.end():]).strip()
    return clean, normalize_quality_data(data)


# ══════════════════════════════════════════════════════════════════════
# АРТЕФАКТЫ ЧАНКОВ (tmp/quality/)
# ══════════════════════════════════════════════════════════════════════

def reset_chunks_dir(path: str = CHUNK_DIR, logger=None) -> bool:
    """Новый запуск — чистый каталог чанков (старьё исказило бы сводку)."""
    global CHUNK_FILE_RE
    if CHUNK_FILE_RE is None:
        CHUNK_FILE_RE = re.compile(r"^chunk-(\d+)\.json$")
    if os.path.isdir(path):
        try:
            shutil.rmtree(path)
        except OSError as exc:
            if logger:
                logger.error(f"❌ Не удалось очистить {path}: {exc}")
            return False
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        if logger:
            logger.error(f"❌ Не удалось создать {path}: {exc}")
        return False
    return True


def save_chunk_result(res: dict, path: str = CHUNK_DIR) -> None:
    """Отчёт чанка на диске: без него сводка невоспроизводима."""
    atomic_write(os.path.join(path, f"chunk-{int(res['id']):03d}.json"),
                 json.dumps(res, ensure_ascii=False, indent=2))


def load_chunk_results(path: str = CHUNK_DIR) -> list[dict]:
    """Сохранённые отчёты чанков по порядку id (режим --summary-only)."""
    out: list[dict] = []
    if not os.path.isdir(path) or CHUNK_FILE_RE is None:
        return out
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return out
    for name in names:
        m = CHUNK_FILE_RE.match(name)
        if not m:
            continue
        try:
            data = json.loads(read_text_safe(os.path.join(path, name)) or "")
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("nums"):
            data["id"] = int(m.group(1))
            out.append(data)
    return sorted(out, key=lambda d: d["id"])


# ══════════════════════════════════════════════════════════════════════
# ЗАПРОСЫ: чанки (потоки) и свёртка (всегда последовательно)
# ══════════════════════════════════════════════════════════════════════

def assess_chunks(args, stage, chunks, orig_by_num: dict, prompt: str,
                  logger) -> list[dict]:
    """Один запрос на чанк, N потоков; результат каждого — свой файл."""
    results: list[dict] = [None] * len(chunks)  # type: ignore[list-item]
    quiet = stage.quiet()  # стадия со своими ретраями логирует каждый запрос

    def worker(idx: int, chunk: dict) -> None:
        original, translated = chunk_content(chunk, orig_by_num)
        user = build_user_prompt(prompt, original, translated)
        text, err = quiet.complete(
            user, label=f"[quality {chunk['id']}/{len(chunks)}]")
        clean, parsed = parse_quality_answer(text or "")
        results[idx] = {
            "stage": "translate_quality", "id": chunk["id"],
            "label": chunk_label(chunk["nums"], chunk.get("part")),
            "nums": chunk["nums"],
            "part": list(chunk["part"]) if chunk.get("part") else None,
            "model": stage.profile.model,
            "tokens": estimate_tokens(user),
            "error": None if text else (err or "пустой ответ"),
            "text": clean, "parsed": parsed,
        }
        save_chunk_result(results[idx])

    workers = max(1, min(int(args.threads), 16, len(chunks)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(worker, i, c): i for i, c in enumerate(chunks)}
        with Progress(len(chunks), "Оценка перевода", unit="чанк",
                      bar=True, logger=logger) as progress:
            for f in as_completed(futs):
                try:
                    f.result()
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"Поток чанка {futs[f] + 1}: {exc}")
                    results[futs[f]] = {"stage": "translate_quality",
                                        "id": chunks[futs[f]]["id"],
                                        "label": chunk_label(
                                            chunks[futs[f]]["nums"],
                                            chunks[futs[f]].get("part")),
                                        "nums": chunks[futs[f]]["nums"],
                                        "part": None, "text": "", "parsed": None,
                                        "model": stage.profile.model,
                                        "tokens": 0, "error": str(exc)}
                progress.step()
    return [r for r in results if r]


def unit_text(res: dict, level: int = 0) -> str:
    """Один отчёт чанка в тексте свёртки: 0 — с цитатами, 1 — без, 2 — баллы."""
    label = res.get("label") or f"чанк {res.get('id')}"
    if res.get("error"):
        return f"### {label}\n(оценка не получена: {res['error']})"
    parsed = res.get("parsed")
    if not parsed:
        body = (res.get("text") or "").strip()
        if level >= 1 and body:
            body = trim_to_tokens(body, 600).rstrip() + "…"
        return f"### {label}\n{body}" if body else f"### {label}\n(пустой ответ)"
    lines = [f"### {label}"]
    if parsed.get("score") is not None:
        lines.append(f"общий балл: {parsed['score']}")
    if parsed.get("sections"):
        lines.append(" · ".join(f"{k} {v}"
                                for k, v in parsed["sections"].items()))
    if level >= 2:
        return "\n".join(lines)
    for key, title in (("strengths", "сильное"), ("issues", "замечание")):
        for f in parsed.get(key) or []:
            bits = []
            if f.get("type"):
                bits.append(str(f["type"]))
            if f.get("chapter") is not None:
                bits.append(f"глава {f['chapter']}")
            if level == 0 and f.get("quote"):
                bits.append(f"«{f['quote']}»")
            if f.get("note"):
                bits.append(str(f["note"]))
            text = " — ".join(bits)
            if text:
                lines.append(f"- {title}: {text}")
    return "\n".join(lines)


def group_by_budget(texts: list[str], budget: int) -> list[list[str]]:
    """Разбить тексты свёртки на батчи ≤ budget (ТОКЕНЫ); в батче ≥ 1 текст."""
    batches: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for t in texts:
        n = estimate_tokens(t)
        if cur and size + n > budget:
            batches.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += n
    if cur:
        batches.append(cur)
    return batches


def fold_reports(stage, texts, budget: int, prompt: str, logger):
    """Дерево LLM-сводок: батчи ≤ budget → промежуточные сводки, повтор.

    Возвращает (тексты, запросов, уровней). Сводка короче предыдущего уровня:
    результат режется trim_to_tokens по тому же бюджету — иначе дерево никогда
    не сошлось бы. Пустой ответ — кусок передаётся как есть.
    """
    quiet = stage.quiet()
    requests, levels = 0, 0
    while (len(texts) > 1
           and estimate_tokens("\n\n".join(texts)) > budget
           and levels < MAX_REDUCE_LEVELS and requests < MAX_REDUCE_REQUESTS):
        batches = group_by_budget(texts, budget)
        if len(batches) >= len(texts):
            break
        levels += 1
        out: list[str] = []
        for bi, batch in enumerate(batches, 1):
            body = "\n\n".join(batch)
            text, err = summarize(quiet, body, prompt, logger,
                                 label=f"[свёртка {levels}:{bi}/{len(batches)}]")
            requests += 1
            atomic_write(os.path.join(CHUNK_DIR, f"summary-l{levels}-{bi}.json"),
                         json.dumps({"stage": "translate_quality",
                                     "level": levels, "index": bi,
                                     "chunks": len(batch),
                                     "error": None if text else err,
                                     "text": (text or "").strip()},
                                    ensure_ascii=False, indent=2))
            if not text:
                logger.warning(f"⚠️ Свёртка {levels}:{bi} не получилась "
                               f"({err}) — кусок передаётся как есть.")
            out.append(trim_to_tokens((text or body).strip(), budget))
        logger.info(f"🧩 Свёртка уровня {levels}: {len(texts)} → {len(out)} "
                    f"отчётов (бюджет {budget:,} токенов)".replace(",", " "))
        texts = out
    return texts, requests, levels


def summarize_user(prompt: str, body: str) -> str:
    """user-текст свёртки: данные через {batch_text}; шаблон без плейсхолдера =
    старый внешний промпт: данные дописываются в конец с разметкой."""
    tpl = prompt or DEFAULT_SUMMARY_PROMPT
    if "{batch_text}" in tpl:
        return tpl.replace("{batch_text}", body)
    return f"{tpl}\n\n=== СВОДКИ ЧАНКОВ ===\n{body}"


def summarize(stage, body: str, prompt: str, logger=None, *,
              label: str = "[свёртка]") -> tuple[str, str | None]:
    """Последний запрос свёртки: сводки чанков → заключение."""
    user = summarize_user(prompt, body)
    text, err = stage.complete(user, label=label)
    if not text:
        return "", err
    clean, _parsed = parse_quality_answer(text)
    return clean, None


def reduce_reports(stage, results, budget: int, prompt: str,
                   logger) -> tuple[str, dict]:
    """Отчёты чанков → текст для заключения (бюджет тот же, --budget).

    Каскад сжатия: полные отчёты → без цитат → только баллы. Если и на
    последнем уровне не влезло — текст урезается по бюджету, в отчёте это
    помечено.
    """
    meta = {"requests": 0, "levels": 0, "compression": LEVEL_NAMES[0],
            "used": 0, "total": len(results), "trimmed": False}
    texts: list[str] = []
    for level in range(MAX_REDUCE_LEVELS):
        texts = [unit_text(r, level) for r in results]
        meta["used"] = len(texts)
        meta["compression"] = LEVEL_NAMES[level]
        if estimate_tokens("\n\n".join(texts)) <= budget:
            break
        texts, req, levels = fold_reports(stage, texts, budget, prompt, logger)
        meta["requests"] += req
        meta["levels"] = max(meta["levels"], levels)
        if len(texts) == 1 and estimate_tokens(texts[0]) <= budget:
            break
    combined = "\n\n".join(texts)
    if estimate_tokens(combined) > budget:
        combined = trim_to_tokens(combined, budget)
        meta["trimmed"] = True
        logger.warning(f"⚠️ Свёртка не сошлась — текст урезан по бюджету "
                       f"{budget:,} токенов".replace(",", " "))
    return combined, meta


# ──────────────────────────────────────────────
# ОТЧЁТЫ
# ──────────────────────────────────────────────
def build_report(meta: dict, assessment: str) -> str:
    """Отчёт режима range: техническая шапка + блок оценки (обычный Markdown)."""
    requested = meta["range_requested"]
    included = meta["range_included"]
    range_desc = f"{included[0]} – {included[1]}"
    if requested != included:
        range_desc += (f" (из запрошенных {requested[0]} – {requested[1]}; "
                       f"отсечено {meta['dropped']} глав бюджетом)")
    rows = [
        ("Дата", meta["date"]),
        ("Режим", "одним пакетом (диапазон глав)"),
        ("Диапазон глав", range_desc),
        ("Включено глав", str(meta["chapters"])),
        ("Тип файлов глав", meta["file_type"]),
        ("Бюджет запроса", f"{meta['budget']:,} токенов".replace(",", " ")
         + " (оценка; промпт не вычитается)"),
        ("Размер пакета",
         f"{meta['packet_size']:,} токенов".replace(",", " ")
         + " (фактический запрос: главы + промпт)"),
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


def _avg(values) -> float | None:
    vals = [float(v) for v in values]
    return round(sum(vals) / len(vals), 2) if vals else None


def _median(values) -> float | None:
    vals = sorted(float(v) for v in values)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return round(vals[mid], 2)
    return round((vals[mid - 1] + vals[mid]) / 2, 2)


def merge_findings(results, key: str) -> list[dict]:
    """Замечания всех чанков: группировка по (тип, цитата), счёт повторов.

    Ключ — нормализованная (регистр + пробелы) цитата, без неё — начало заметки.
    """
    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    for r in results:
        for f in (r.get("parsed") or {}).get(key) or []:
            quote = " ".join(str(f.get("quote") or "").casefold().split())
            note = " ".join(str(f.get("note") or "").casefold().split())
            gk = (str(f.get("type") or "").casefold(), quote or note[:40])
            if gk not in groups:
                groups[gk] = {"type": f.get("type") or "",
                              "quote": f.get("quote") or "",
                              "note": f.get("note") or "", "chapters": [],
                              "count": 0}
                order.append(gk)
            g = groups[gk]
            g["count"] += 1
            if f.get("chapter") is not None and f["chapter"] not in g["chapters"]:
                g["chapters"].append(f["chapter"])
            if not g["note"] and f.get("note"):
                g["note"] = f["note"]
    out = [groups[k] for k in order]
    out.sort(key=lambda g: (-g["count"], str(g["type"])))
    return out


def build_chunks_report(meta: dict, results, conclusion: str,
                        red: dict) -> str:
    """Отчёт режима chunks: шапка, сводка баллами, merged замечания,
    заключение LLM, приложение по чанкам."""
    included = meta["range_included"]
    scored = [r for r in results
              if (r.get("parsed") or {}).get("score") is not None]
    failed = [r for r in results if r.get("error")]
    rows = [
        ("Дата", meta["date"]),
        ("Режим", "чанками (оценка по частям книги)"),
        ("Диапазон глав", f"{included[0]} – {included[1]}"),
        ("Глав в оценке", str(meta["chapters"])),
        ("Тип файлов глав", meta["file_type"]),
        ("Чанков", f"{len(results)} (глав в чанке {meta['chunk_size']}, "
                   f"отбор: {meta['sample']})"),
        ("Разрезано глав", str(meta["split_chapters"])),
        ("Бюджет запроса", f"{meta['budget']:,} токенов".replace(",", " ")
         + " (оценка; промпт не вычитается)"),
        ("Потоков", str(meta["threads"])),
        ("Модель", meta["model"]),
        ("Сервер", meta["host"]),
        ("Промпт-файл", meta["prompt_file"] or "встроенный"
         + " (оба тега)"),
        ("Артефакты чанков", CHUNK_DIR),
    ]
    lines = ["# Оценка качества перевода", "",
             "| Параметр | Значение |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in rows]
    lines += ["", "---", "", "## Итоговая оценка", ""]
    scores = [r["parsed"]["score"] for r in scored]
    if scores:
        lines.append(
            f"Оценено числом: {len(scores)} из {len(results)} чанков. "
            f"Среднее **{_avg(scores)}**, медиана **{_median(scores)}**, "
            f"минимум {min(scores)}, максимум {max(scores)}.")
    else:
        lines.append("Модель не вернула числовых оценок — ниже только её текст.")
    lines.append("")
    sec: dict[str, list] = {}
    for r in results:
        for k, v in (r.get("parsed") or {}).get("sections", {}).items():
            sec.setdefault(k, []).append(v)
    if sec:
        lines += ["| Раздел | средний балл | чанков |", "| --- | --- | --- |"]
        order = [k for k in SECTION_ORDER if k in sec]
        order += [k for k in sec if k not in order]
        lines += [f"| {k} | {_avg(sec[k])} | {len(sec[k])} |" for k in order]
        lines.append("")
    notes = [f"Свёртка: {red.get('used', 0)} отчётов из "
             f"{red.get('total', 0)}, уровней {red.get('levels', 0)}, "
             f"запросов {red.get('requests', 0)}, сжатие — "
             f"{red.get('compression', LEVEL_NAMES[0])}."]
    if red.get("trimmed"):
        notes.append("Сводка не влезла целиком — текст урезан по бюджету.")
    if meta["oversize"]:
        notes.append(f"Больше бюджета даже одной частью: главы "
                     f"{meta['oversize']}.")
    lines += [f"_{n}_" for n in notes] + [""]
    for key, title in (("strengths", "Сильные стороны"),
                       ("issues", "Замечания")):
        merged = merge_findings(results, key)
        if not merged:
            continue
        lines += [f"### {title} ({len(merged)})", ""]
        for f in merged:
            head = str(f["type"]) if f["type"] else ""
            if f["chapters"]:
                head += (f" · главы "
                        f"{', '.join(str(n) for n in f['chapters'][:12])}")
            if f["count"] > 1:
                head += f" ×{f['count']}"
            quote = f" «{f['quote']}»" if f["quote"] else ""
            body = f"{head}{quote} — {f['note']}" if head else f["note"]
            lines.append(f"- {body.strip(' —')}")
        lines.append("")
    if failed:
        lines += [f"### Чанки с ошибками ({len(failed)})", ""]
        lines += [f"- {r.get('label')}: {r['error']}" for r in failed[:20]]
        lines.append("")
    lines += ["---", "", "## Заключение", "",
              (conclusion or "_(заключение не получено)_").strip(), "",
              "---", "", "## Приложение: оценки по чанкам", "",
              "| № | Часть книги | Балл | Разделы |", "| --- | --- | --- | --- |"]
    for r in results:
        parsed = r.get("parsed") or {}
        secs = " · ".join(f"{k} {v}"
                          for k, v in parsed.get("sections", {}).items())
        lines.append(f"| {r['id']} | {r.get('label', '')} | "
                     f"{parsed.get('score', '—')} | {secs or '—'} |")
    return "\n".join(lines) + "\n"


def write_chunks_report(output_path: str, meta: dict, results, conclusion: str,
                        red: dict, logger) -> None:
    """Отчёт режима chunks атомарно (tmp + os.replace)."""
    text = build_chunks_report(meta, results, conclusion, red)
    try:
        atomic_write(output_path, text)
    except OSError as exc:
        logger.error(f"❌ Не удалось записать отчёт {output_path}: {exc}")
        return
    logger.info(f"✅ Отчёт: {os.path.abspath(output_path)}")
    print(f"Отчёт: {os.path.abspath(output_path)}")


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
# РЕЖИМЫ
# ──────────────────────────────────────────────
def run_range(args, stage, chapters, orig_by_num, prompt, logger) -> int:
    """Один запрос по пакету целых глав диапазона."""
    kept, dropped = fit_budget(chapters, orig_by_num, prompt, args.budget)
    if not kept:
        logger.error(f"❌ Ни одна глава не влезает в бюджет "
                     f"({args.budget} токенов, оценка).")
        return 1
    if dropped:
        logger.warning(f"⚠️ Бюджет: отсечено {dropped} глав "
                       f"(включено {len(kept)} из {len(chapters)}).")
    nums = [n for n, _ in kept]
    original_text = "\n".join(orig_by_num[n] for n, _ in kept
                              if n in orig_by_num)
    translated_text = "\n".join(t if t.endswith("\n") else t + "\n"
                               for _n, t in kept)
    user_content = build_user_prompt(prompt, original_text, translated_text)
    packet_size = estimate_tokens(user_content)

    # ── Предпросмотр запроса (--preview-request): первый пакет оценки ──
    if stage.preview(f"Оценка · главы {nums[0]}–{nums[-1]}", user_content,
                    meta={"chapters": len(kept), "first": nums[0],
                          "last": nums[-1], "budget": args.budget,
                          "prompt_file": args.prompt_file or ""}):
        return 0

    progress = Progress(1, "Оценка перевода")
    progress.start()
    logger.info(f"Пакет: глав {nums[0]}–{nums[-1]} ({len(nums)}), "
                f"размер {packet_size:,} токенов "
                f"(оценка)".replace(",", " "))
    print(f"Запрос к LLM ({stage.profile.model}) по главам "
          f"{nums[0]}–{nums[-1]}…")
    assessment, err = stage.complete(user_content, label="[quality]")
    progress.step()
    if not assessment:
        logger.error(f"❌ LLM вернул пустой ответ. {err or ''}".rstrip())
        return 1
    meta = {
        "date": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
        "range_requested": (args.start, args.end),
        "range_included": (nums[0], nums[-1]),
        "chapters": len(nums), "dropped": dropped, "file_type": args.type,
        "budget": args.budget, "packet_size": packet_size,
        "model": stage.profile.model, "host": stage.profile.base_url,
        "prompt_file": args.prompt_file or "",
    }
    write_report(args.output, meta, assessment, logger)
    return 0


def preview_chunks(stage, args, all_chunks, selected, note, oversize,
                   orig_by_num, prompt, summary_prompt) -> int:
    """--preview-request режима chunks: план чанков + запрос чанка + запрос свёртки.

    payload тот же, что у Stage.preview (messages — первый запрос); поверх него
    `requests` — все запросы прогона по порядку, у каждого свои messages,
    chars и tokens.
    """
    plog = preview_logger(stage.name)
    log_argv(plog)
    first = selected[0]
    original, translated = chunk_content(first, orig_by_num)
    # батч свёртки из всех чанков прогона: у первого — пример сводки с баллами
    # и разделами (чтобы была видна форма), у остальных — заглушки
    units = []
    for i, c in enumerate(selected):
        sample = {"id": c["id"], "label": chunk_label(c["nums"], c.get("part")),
                  "nums": c["nums"], "parsed": None,
                  "text": "(здесь сводка чанка)"}
        if i == 0:
            sample["parsed"] = {"score": 8.5,
                                "sections": {"точность": 9, "стиль": 8,
                                             "читаемость": 8,
                                             "терминология": 7,
                                             "ошибки": 9},
                                "strengths": [], "issues": []}
            sample["text"] = ""
        units.append(unit_text(sample))
    unit = "\n\n".join(units)
    requests = [
        preview_request_payload(
            stage.name, f"Оценка · чанк 1/{len(selected)}",
            stage.profile.model,
            llm_messages(build_user_prompt(prompt, original, translated)),
            meta={"главы": list(first["nums"]),
                  "размер": f"{chunk_tokens(first, orig_by_num)} токенов"}),
        preview_request_payload(
            stage.name, "Свёртка отчётов → заключение", stage.profile.model,
            llm_messages(summarize_user(summary_prompt, unit)),
            meta={"сводок": len(units), "уровней": 1}),
    ]
    payload = dict(requests[0])
    payload["requests"] = requests
    payload["meta"] = {
        "режим": "chunks", "чанков": len(all_chunks),
        "оценивается": len(selected), "отбор": note,
        "глав в чанке": args.chunk_size, "перекрытие": args.overlap,
        "бюджет": f"{args.budget} токенов", "потоки": args.threads,
        "разрезано глав": sum(1 for c in all_chunks if c.get("part")),
        "не влезли даже частью": oversize or "нет",
        "артефакты": CHUNK_DIR,
        "промпт-файл": args.prompt_file or "встроенный",
    }
    write_preview_request(stage.preview_path, payload)
    plog.info("✅ Предпросмотр запросов: %s → %s",
              ", ".join(str(r["label"]) for r in requests),
              stage.preview_path)
    return 0


def run_chunks(args, stage, chapters, orig_by_num, prompt, logger) -> int:
    """Чанки по целым главам → N запросов → LLM-свёртка → один отчёт."""
    all_chunks = build_chunks(chapters, args.chunk_size, args.overlap)
    all_chunks, split_ch, oversize = refit_oversize(
        all_chunks, orig_by_num, args.budget, logger)
    selected, note = select_chunks(all_chunks, args.chunks, args.sample)
    if not selected:
        logger.error("❌ Пустой список чанков.")
        return 1
    logger.info(f"Чанки: {len(all_chunks)}, оценивается {len(selected)} "
                f"({note}), потоков {args.threads}")
    # тот же файл, другой тег: <prompt_assessment_summary> свёртки
    summary_prompt = load_assessment_prompt(
        args.prompt_file, logger, tag="prompt_assessment_summary",
        default=DEFAULT_SUMMARY_PROMPT)
    if stage.preview_path:
        return preview_chunks(stage, args, all_chunks, selected, note,
                              oversize, orig_by_num, prompt, summary_prompt)

    if args.summary_only:
        results = load_chunk_results()
        if not results:
            logger.error(f"❌ В {CHUNK_DIR} нет отчётов чанков — "
                         f"сначала обычный запуск режима chunks.")
            return 1
        logger.info(f"♻️ --summary-only: {len(results)} отчётов из {CHUNK_DIR}")
    else:
        if not reset_chunks_dir(logger=logger):
            return 1
        results = assess_chunks(args, stage, selected, orig_by_num, prompt,
                                logger)
    done = [r for r in results if not r.get("error")]
    if not done:
        logger.error("❌ Ни один чанк не оценён — отчёт не пишется.")
        return 1

    if len(results) == 1:
        conclusion, red = (results[0].get("text") or "").strip(), \
            {"requests": 0, "levels": 0, "compression": LEVEL_NAMES[0],
             "used": 1, "total": 1, "trimmed": False}
        logger.info("ℹ️ Чанк один — свёртка не нужна.")
    else:
        logger.info(f"🧩 Оценка получена: {len(done)}/{len(results)}, "
                    f"сворачиваю")
        body, red = reduce_reports(stage, results, args.budget, summary_prompt,
                                   logger)
        conclusion, err = summarize(stage, body, summary_prompt, logger,
                                    label="[свёртка → заключение]")
        if not conclusion:
            logger.warning(f"⚠️ Заключение не получено: {err} — в отчёте "
                           f"только сводка кода.")
    nums = [n for r in results for n in r.get("nums") or []]
    meta = {
        "date": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
        "range_included": (min(nums), max(nums)), "chapters": len(chapters),
        "file_type": args.type, "budget": args.budget,
        "chunk_size": args.chunk_size, "sample": note,
        "threads": args.threads,
        "split_chapters": split_ch,
        "oversize": oversize,
        "model": stage.profile.model, "host": stage.profile.base_url,
        "prompt_file": args.prompt_file or "",

    }
    write_chunks_report(args.output, meta, results, conclusion, red, logger)
    return 0


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    """Парсер translate_quality: дефолты полей — из реестра (core/settings.py)."""
    parser = argparse.ArgumentParser(
        description="Оценка качества перевода (LLM) — один запрос по "
                    "пакету глав или чанками по целым главам со свёрткой.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Единицы: --budget и размеры пакета — ТОКЕНЫ (оценка; главы; промпт НЕ входит);
--chunk_size/--overlap — ГЛАВЫ; max_tokens — серверный предохранитель, ТОКЕНЫ.
Промпт-файл один: тег <prompt_assessment> (плейсхолдеры {original_text}
(chapter.txt) и {translated_text} (тип файлов глав)) и тег
<prompt_assessment_summary> для свёртки (плейсхолдер {batch_text}).
Сервер: --host/--model/--api_key (CLI) > HOST/API_KEY/MODEL из .env
(модель: TRANSLATE_QUALITY_MODEL → MODEL).
Примеры:
  %(prog)s --type polished --start 1 --end 50
  %(prog)s --mode chunks --chunk_size 1 --chunks 40 --sample uniform
  %(prog)s --mode chunks --summary-only   # пересобрать отчёт из tmp/quality/
  %(prog)s --preview-request tmp/preview_request.json   # без сети
""",
    )
    # Сервер/LLM — общий блок стадий (имена флагов контрактны с web/stages.py)
    add_llm_args(parser, aliases=True)
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
    # Режим чанков
    parser.add_argument("--mode", choices=("range", "chunks"),
                        default=DEFAULT_MODE,
                        help="range — один запрос по пакету; chunks — чанки "
                             "по целым главам и LLM-свёртка их отчётов "
                             f"(default: {DEFAULT_MODE}).")
    parser.add_argument("--chunk_size", dest="chunk_size", type=int,
                        default=DEFAULT_CHUNK_SIZE,
                        help="Сколько ЦЕЛЫХ глав в чанке, ГЛАВЫ (default: "
                             f"{DEFAULT_CHUNK_SIZE}).")
    parser.add_argument("--chunks", type=int, default=DEFAULT_CHUNKS,
                        help="Сколько чанков оценить, 0 = все (default: "
                             f"{DEFAULT_CHUNKS}).")
    parser.add_argument("--sample", choices=("uniform", "first"),
                        default=DEFAULT_SAMPLE,
                        help="Как брать чанки: равномерно по книге или первые "
                             f"(default: {DEFAULT_SAMPLE}).")
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP,
                        help="Перекрытие соседних чанков, ГЛАВЫ (целыми "
                             f"главами, default: {DEFAULT_OVERLAP}).")
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS,
                        help=f"Потоки оценки чанков (свёртка всегда "
                             f"последовательно, default: {DEFAULT_THREADS}).")
    parser.add_argument("--summary-only", dest="summary_only",
                        action="store_true",
                        help="Не опрашивать модель по чанкам: взять "
                             f"{CHUNK_DIR} и только свернуть.")
    # Промпт и выход
    parser.add_argument("--prompt_file", default=None,
                        help="Промпт-файл (тег <prompt_assessment> — оценка, "
                             "тег <prompt_assessment_summary> — свёртка "
                             "отчётов чанков; без тегов — файл целиком; "
                             "пусто = встроенные шаблоны).")
    parser.add_argument("--preview-request",
                        dest="preview_request", default=None,
                        help="ПРЕДПРОСМОТР: запрос оценки первого\n"
                             "пакета глав без сети → JSON-файл\n"
                             "(messages + статистика символов и токенов).")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"Выходной md-отчёт (default: "
                             f"{DEFAULT_OUTPUT}; в web — фиксирован).")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                        help=f"Бюджет запроса, ТОКЕНЫ (оценка): главы (содержимое; промпт НЕ входит); "
                             f"если не влезает — пакет обрезается до "
                             f"целого количества глав (default: "
                             f"{DEFAULT_BUDGET}).")
    core_settings.apply_cli_defaults(parser, "translate_quality")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # ── Лог стадии + сервер: CLI > os.environ > .env > help+exit ──
    stage, logger = setup_stage("translate_quality", args)
    logger.info(f"API: {stage.profile.base_url} | модель: "
                f"{stage.profile.model} | режим: {args.mode} | бюджет: "
                f"{args.budget} токенов (оценка) | тип: {args.type}")

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
    orig_by_num = collect_originals(chapters, chapter_map, logger)
    prompt = load_assessment_prompt(args.prompt_file, logger)

    if args.mode == "chunks":
        return run_chunks(args, stage, chapters, orig_by_num, prompt, logger)
    return run_range(args, stage, chapters, orig_by_num, prompt, logger)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nПрервано пользователем.")
        sys.exit(130)

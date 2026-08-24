#!/usr/bin/env python3
"""
NER Extraction для веб-новелл.
Извлечение именованных сущностей через LLM с дедупликацией,
голосованием по полям и двухпроходной схемой ревью.

Язык и набор полей определяются промптом пользователя.
Скрипт автоматически обнаруживает фонетическое поле
(«pinyin» или «reading») в ответе LLM и использует его
для alias-merge. Если ни одно не найдено — группировка
по звучанию пропускается.

Two-pass работает конвейером (pipeline): каждый поток выполняет
pass1 → pass2 для одного чанка, затем берёт следующий. Без барьеров.
Кэш сохраняется периодически — при падении возобновление с места остановки.
"""

import os
import argparse
import time
import json
import logging
import threading
import unicodedata
import re
import copy
import sys
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

from core.common import (  # noqa: E402
    compile_chapter_texts,
    determine_model,
    emit_progress,
    find_env_file,
    get_ngrams,
    get_server_config,
    get_stage_model,
    is_cjk_string,
    log_argv,
    parse_dotenv,
    print_env_help,
    setup_logging,
    split_text_smart,
    stream_chat_completion,
    web_progress_enabled,
)

# ══════════════════════════════════════════════════════════════════════
# ВСТРОЕННЫЕ ПРОМПТЫ (дефолт для китайского; переопределяются
# через --prompt_file для любого другого языка)
# ══════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT_PASS1 = """
Analyze the provided text and extract Named Entities (NER) useful for consistent
translation (fantasy terms, names, locations, specific abilities).
Return result strictly in JSON format:
[
  {
    "term": "Entity Name",
    "pinyin": "Pinyin with tones",
    "type": "Person (male/female)/Location/Sect/Technique/Artifact/Stage/Race/Other",
    "translation": "Russian Translation",
    "notes": "Optional comments",
    "context": "Full sentence from text where it appears",
    "translated_context": "Literary Russian translation of that sentence"
  }
]
Rules:
- For Person type, specify gender: "Person (male)" / "Person (female)".
- Normalize "term" to the form as it appears in the text.
- Skip generic words that need no glossary entry.
- If no entities found, return [].
Output ONLY valid JSON.
Text for work:
"""

SYSTEM_PROMPT_PASS2 = """
You are a quality-control reviewer for a Chinese web-novel translation glossary.
You will receive:
1. A chunk of the original Chinese text.
2. A JSON array of NER entries previously extracted from this chunk.

Your job:
- Verify each entry: is the term actually present in the text?
- Fix pinyin (must match the characters, use tone marks).
- Fix translation (no brackets, no slashes, no English, one final variant).
- Fix type if wrong.
- Remove entries that are generic words, not real named entities.
- Add any important entities that were missed.
- Keep "context" and "translated_context" accurate.

Return the corrected JSON array in the SAME format. Output ONLY valid JSON.

=== ORIGINAL TEXT ===
{chunk_text}

=== EXTRACTED NER (to review) ===
{ner_json}
"""

SYSTEM_PROMPT_PASS2_SYS = (
    "You are a meticulous editor verifying a translation glossary. "
    "Output ONLY valid JSON."
)

# ══════════════════════════════════════════════════════════════════════
# КОНСТАНТЫ
# ══════════════════════════════════════════════════════════════════════

META_PREFIX = "_"

NON_VOTABLE_FIELDS = {"term", "count", "aliases"}
DEFAULT_NON_VOTED_FIELDS = {"notes", "context", "translated_context"}
TRANSIENT_FIELDS = {"_ngrams", "_len", "_source_chunks"}

DEFAULT_SAVE_INTERVAL = 10

# ══════════════════════════════════════════════════════════════════════
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ
# ══════════════════════════════════════════════════════════════════════

ner_lock = threading.Lock()
global_ner_data: list[dict] = []
processed_chunks: set[int] = set()
EXTRA_VOTED_FIELDS: set[str] = set()

# ══════════════════════════════════════════════════════════════════════
# ЛОГИРОВАНИЕ В РЕАЛЬНОМ ВРЕМЕНИ
# ══════════════════════════════════════════════════════════════════════


def _flush_log(logger) -> None:
    for handler in logger.handlers:
        try:
            handler.flush()
        except Exception:
            pass


def _log(logger, level: int, msg: str) -> None:
    logger.log(level, msg)
    _flush_log(logger)

# ══════════════════════════════════════════════════════════════════════
# CJK-УТИЛИТЫ И ДЕДУПЛИКАЦИЯ
# ══════════════════════════════════════════════════════════════════════


def normalize_cjk(s: str) -> str:
    return unicodedata.normalize("NFC", s).replace(" ", "").replace("\u3000", "")


def cjk_levenshtein(a: str, b: str) -> int:
    if len(a) > len(b):
        a, b = b, a
    prev = list(range(len(a) + 1))
    for cb in b:
        curr = [prev[0] + 1]
        for i, ca in enumerate(a, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[i] + 1, curr[i - 1] + 1, prev[i - 1] + cost))
        prev = curr
    return prev[-1]


def jaccard_similarity(set_a: set, set_b: set) -> float:
    union = len(set_a | set_b)
    return len(set_a & set_b) / union if union else 0.0


def terms_are_duplicate(
    term_a: str, term_b: str, ngram_size: int, threshold: float
) -> bool:
    norm_a = normalize_cjk(term_a)
    norm_b = normalize_cjk(term_b)

    if norm_a == norm_b:
        return True

    if is_cjk_string(term_a) and is_cjk_string(term_b):
        len_a, len_b = len(norm_a), len(norm_b)
        if len_a <= 3 or len_b <= 3:
            return False
        if len_a <= 6 and len_b <= 6:
            # Короткие CJK: только точное совпадение.
            # norm_a == norm_b уже проверено выше.
            # Левенштейн <= 1 опасен: 一階強化 и 九階強化
            # отличаются на 1 символ, но это РАЗНЫЕ термины.
            return False
        bg_a = get_ngrams(norm_a, n=2)
        bg_b = get_ngrams(norm_b, n=2)
        if bg_a and bg_b:
            return jaccard_similarity(bg_a, bg_b) >= threshold
        return False

    len_a, len_b = len(norm_a), len(norm_b)
    if len_a == 0 or len_b == 0:
        return False
    if not (0.33 < len_a / len_b < 3.0):
        return False
    ng_a = get_ngrams(norm_a, n=ngram_size)
    ng_b = get_ngrams(norm_b, n=ngram_size)
    if ng_a and ng_b:
        return jaccard_similarity(ng_a, ng_b) >= threshold
    return False


def is_fuzzy_duplicate_in_list(
    candidate: str, items: list[dict], ngram_size: int, threshold: float
) -> tuple[bool, dict | None]:
    for item in items:
        if terms_are_duplicate(candidate, item["term"], ngram_size, threshold):
            return True, item
    return False, None


# ══════════════════════════════════════════════════════════════════════
# ГОЛОСОВАНИЕ
# ══════════════════════════════════════════════════════════════════════

_GENDER_PATTERN = re.compile(
    r"^(.+?)\s*\((unknown|male|female)\)$", re.IGNORECASE
)

def _resolve_votes(votes: dict) -> str:
    """
    Определить победителя голосования (двухфазный алгоритм).

    Фаза 1 — базовый класс:
      Отбрасываем гендерный квалификатор, суммируем голоса по базовому
      имени.  Person (male):6 + Person (female):5 + Person (unknown):2
      → Person: 13.  Location:10 остаётся Location:10.
      Победитель = max по суммарному count.

    Фаза 2 — гендер (только если победивший класс гендерный):
      Внутри класса Person голоса male/female/unknown разыгрываются
      по прежним правилам: unknown исключается при наличии конкретных,
      ничья male:female → unknown.

    Для негендерных классов (Location, Sect, Technique…) фаза 2
    не наступает — возвращается значение как есть.
    """
    if not votes:
        return ""

    # ── Фаза 1: группировка по базовому классу ──
    base_totals: dict[str, int] = {}
    base_members: dict[str, dict[str, int]] = {}

    for val, cnt in votes.items():
        m = _GENDER_PATTERN.match(val.strip())
        base = m.group(1).strip() if m else val.strip()
        base_totals[base] = base_totals.get(base, 0) + cnt
        base_members.setdefault(base, {})[val] = cnt

    max_total = max(base_totals.values())
    base_winners = sorted(b for b, c in base_totals.items() if c == max_total)
    winning_base = base_winners[0]

    members = base_members[winning_base]

    # ── Фаза 2: гендерная резолюция (только для гендерного класса) ──
    has_gendered = any(_GENDER_PATTERN.match(v.strip()) for v in members)

    if not has_gendered:
        if len(members) == 1:
            return next(iter(members))
        max_cnt = max(members.values())
        return sorted(v for v, c in members.items() if c == max_cnt)[0]

    gendered: dict[str, int] = {}
    unknown_val: str | None = None

    for val, cnt in members.items():
        m = _GENDER_PATTERN.match(val.strip())
        if m:
            if m.group(2).lower() == "unknown":
                unknown_val = val
            else:
                gendered[val] = cnt
        else:
            unknown_val = val

    if not gendered:
        return unknown_val or next(iter(members))

    max_count = max(gendered.values())
    winners = sorted(v for v, c in gendered.items() if c == max_count)

    if len(winners) == 1:
        return winners[0]

    if unknown_val is not None:
        return unknown_val
    m = _GENDER_PATTERN.match(winners[0].strip())
    if m:
        return f"{m.group(1)} (unknown)"
    return winners[0]

def _votes_key(field: str) -> str:
    return f"{META_PREFIX}votes_{field}"


def _is_votable(field: str) -> bool:
    if field in NON_VOTABLE_FIELDS or field.startswith(META_PREFIX):
        return False
    if field in EXTRA_VOTED_FIELDS:
        return True
    if field in DEFAULT_NON_VOTED_FIELDS:
        return False
    return True


def _is_storable(field: str) -> bool:
    return field not in NON_VOTABLE_FIELDS and not field.startswith(META_PREFIX)


def add_vote(item: dict, field: str, value) -> None:
    if value is None:
        return
    value = str(value).strip()
    if not value:
        return
    key = _votes_key(field)
    if key not in item:
        item[key] = {}
    votes: dict = item[key]
    votes[value] = votes.get(value, 0) + 1
    item[field] = _resolve_votes(votes)


def init_votes(item: dict) -> None:
    for field in list(item.keys()):
        if not _is_storable(field) or not item[field]:
            continue
        val = str(item[field]).strip()
        if not val:
            continue
        if _is_votable(field):
            item[_votes_key(field)] = {val: 1}


def merge_fields(item: dict, ner: dict) -> None:
    for field, value in ner.items():
        if not _is_storable(field):
            continue
        if value is None:
            continue
        val = str(value).strip()
        if not val:
            continue
        if _is_votable(field):
            add_vote(item, field, val)
        else:
            item[field] = val


# ══════════════════════════════════════════════════════════════════════
# ВАЛИДАЦИЯ И ПАРСИНГ
# ══════════════════════════════════════════════════════════════════════

REQUIRED_KEYS = {"term", "type", "translation"}


def is_valid_ner_format(data) -> bool:
    if not isinstance(data, list):
        return False
    for item in data:
        if not isinstance(item, dict):
            return False
        if not REQUIRED_KEYS.issubset(item.keys()):
            return False
        for k in REQUIRED_KEYS:
            if not isinstance(item[k], str):
                return False
    return True


def parse_ner_response(response_text: str):
    try:
        match = re.search(r"\[.*\]", response_text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return data if is_valid_ner_format(data) else None
        data = json.loads(response_text)
        return data if is_valid_ner_format(data) else None
    except (json.JSONDecodeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════════
# ЗАГРУЗКА / СОХРАНЕНИЕ
# ══════════════════════════════════════════════════════════════════════


def load_initial_ner(filepath: str, ngram_size: int, logger) -> None:
    global global_ner_data
    if not os.path.exists(filepath):
        return
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            term = item.get("term", "")
            if not term:
                continue
            item["_ngrams"] = get_ngrams(term, n=ngram_size)
            item["_len"] = len(term)
            if "count" not in item:
                item["count"] = 1
            for field in list(item.keys()):
                if _is_votable(field) and _votes_key(field) not in item:
                    val = item.get(field)
                    if val and str(val).strip():
                        item[_votes_key(field)] = {
                            str(val).strip(): item.get("count", 1)
                        }
            global_ner_data.append(item)
        _log(logger, logging.INFO,
             f"📂 Загружено {len(global_ner_data)} терминов из {filepath}")
    except Exception as e:
        _log(logger, logging.ERROR, f"⚠️ Ошибка чтения NER файла: {e}")


def _save_ner_data_unlocked(filepath: str) -> None:
    snapshot = []
    for item in global_ner_data:
        entry = {k: v for k, v in item.items() if k not in TRANSIENT_FIELDS}
        snapshot.append(entry)
    snapshot.sort(key=lambda x: x.get("count", 0), reverse=True)

    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, filepath)
    except OSError as exc:
        print(f"⚠ NER не сохранён ({exc}): {filepath}", file=sys.stderr)


def save_ner_data(filepath: str) -> None:
    with ner_lock:
        _save_ner_data_unlocked(filepath)


def load_progress(progress_file: str) -> None:
    global processed_chunks
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            processed_chunks = set(data.get("processed_indices", []))
        except Exception:
            pass


def save_progress_unlocked(progress_file: str) -> None:
    tmp_path = progress_file + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"processed_indices": sorted(processed_chunks)}, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, progress_file)
    except OSError:
        pass


# ── Универсальный кэш (pass1 / pass2) ──


def save_chunk_cache(filepath: str, results: dict[int, list[dict]]) -> None:
    serializable = {str(k): v for k, v in results.items()}
    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, filepath)
    except OSError as exc:
        print(f"⚠ Кэш не сохранён ({exc}): {filepath}", file=sys.stderr)


def load_chunk_cache(filepath: str, logger) -> dict[int, list[dict]]:
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        result = {int(k): v for k, v in data.items()}
        _log(logger, logging.INFO,
             f"📂 Загружен кэш: {len(result)} чанков из {filepath}")
        return result
    except Exception as e:
        _log(logger, logging.ERROR, f"⚠️ Ошибка чтения кэша {filepath}: {e}")
        return {}


# ══════════════════════════════════════════════════════════════════════
# ЗАГРУЗКА ПРОМПТОВ
# ══════════════════════════════════════════════════════════════════════


def load_two_pass_prompts(filepath: str, logger) -> tuple[str, str | None]:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        _log(logger, logging.ERROR, f"⚠️ Не удалось прочитать prompt_file: {e}")
        return SYSTEM_PROMPT_PASS1, None

    p1 = re.search(r"<prompt_pass1>(.*?)</prompt_pass1>", content, re.DOTALL)
    p2 = re.search(r"<prompt_pass2>(.*?)</prompt_pass2>", content, re.DOTALL)

    pass1 = p1.group(1).strip() if p1 else content.strip()
    pass2 = p2.group(1).strip() if p2 else None

    if not pass1:
        _log(logger, logging.WARNING,
             "⚠️ Pass1 промпт пуст — используется встроенный.")
        pass1 = SYSTEM_PROMPT_PASS1
    if not p1 and not p2:
        _log(logger, logging.INFO,
             "📝 Теги не найдены — файл целиком используется как pass1.")
    if pass2 is None:
        _log(logger, logging.INFO,
             "📝 Pass2 не задан в файле — используется встроенный.")

    return pass1, pass2


# ══════════════════════════════════════════════════════════════════════
# ЗАПРОС К LLM
# ══════════════════════════════════════════════════════════════════════


def llm_request(
    system_prompt: str,
    user_content: str,
    base_url: str,
    model: str,
    api_key: str,
    max_retries: int,
    timeout: int,
    temperature: float | None,
    logger,
    reasoning_effort: str | None = None,
) -> str | None:
    """Делегирует в единый стрим core.common.stream_chat_completion
    ([DONE]/finish_reason, loop-детект, cut, empty — одна гигиена на проект).
    max_tokens=65536 — исторический предел NER (ТОКЕНЫ, серверный
    предохранитель). enable_reasoning=False: NER исторически не слал
    поле reasoning в payload."""
    text, _err = stream_chat_completion(
        base_url, model,
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_content}],
        api_key=api_key,
        max_retries=max_retries,
        timeout=timeout,
        stream_timeout=timeout,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        enable_reasoning=False,
        max_tokens=65536,
        logger=logger,
        label="[NER]",
    )
    return text


# ══════════════════════════════════════════════════════════════════════
# ОБРАБОТЧИКИ ЧАНКОВ
# ══════════════════════════════════════════════════════════════════════


def process_chunk_pass1(
    index: int,
    text: str,
    base_url: str,
    model: str,
    api_key: str,
    max_retries: int,
    timeout: int,
    temperature: float | None,
    system_prompt: str,
    logger,
    reasoning_effort: str | None = None,
) -> tuple[int, list[dict], str | None]:
    raw = llm_request(
        system_prompt, text, base_url, model, api_key,
        max_retries, timeout, temperature, logger,
        reasoning_effort=reasoning_effort,
    )
    if raw is None:
        return index, [], f"Fail after {max_retries} retries"
    ners = parse_ner_response(raw)
    if ners is None:
        return index, [], "Invalid NER format"
    return index, ners, None


def process_chunk_pass2(
    index: int,
    text: str,
    pass1_ners: list[dict],
    base_url: str,
    model: str,
    api_key: str,
    max_retries: int,
    timeout: int,
    temperature: float | None,
    review_prompt_template: str,
    logger,
    reasoning_effort: str | None = None,
) -> tuple[int, list[dict], str | None]:
    if not pass1_ners:
        return index, [], None

    ner_json = json.dumps(pass1_ners, ensure_ascii=False, indent=2)
    user_content = review_prompt_template.replace("{chunk_text}", text)
    user_content = user_content.replace("{ner_json}", ner_json)

    raw = llm_request(
        SYSTEM_PROMPT_PASS2_SYS, user_content, base_url, model,
        api_key, max_retries, timeout, temperature, logger,
        reasoning_effort=reasoning_effort,
    )
    if raw is None:
        return index, [], f"Pass2 fail after {max_retries} retries"
    ners = parse_ner_response(raw)
    if ners is None:
        return index, [], "Pass2: invalid format"
    return index, ners, None


# ══════════════════════════════════════════════════════════════════════
# TWO-PASS: КОНВЕЙЕРНАЯ ОБРАБОТКА (PIPELINE)
# ══════════════════════════════════════════════════════════════════════
#
#  Каждый поток выполняет pass1 → pass2 для ОДНОГО чанка, затем
#  берёт следующий. Никаких барьеров между pass1 и pass2.
#
#  Thread 1: pass1(0) → pass2(0) → pass1(4) → pass2(4) → ...
#  Thread 2: pass1(1) → pass2(1) → pass1(5) → pass2(5) → ...
#  Thread 3: pass1(2) → pass2(2) → pass1(6) → pass2(6) → ...
#  Thread 4: pass1(3) → pass2(3) → pass1(7) → pass2(7) → ...
#
#  Кэши сохраняются каждые save_interval завершённых чанков.
#  При падении: возобновление пропускает чанки из pass2_cache,
#  для чанков из pass1_cache (но не pass2) — только pass2.
# ══════════════════════════════════════════════════════════════════════


def run_two_pass(
    all_chunks: list[str],
    pass1_cache_file: str,
    pass2_cache_file: str,
    base_url: str,
    model_name: str,
    api_key: str,
    max_retries: int,
    timeout: int,
    temperature: float | None,
    pass1_prompt: str,
    pass2_prompt: str,
    max_workers: int,
    ner_file: str,
    threshold: float,
    ngram_size: int,
    save_interval: int,
    logger,
    reasoning_effort: str | None = None,
) -> int:
    """Двухпроходный конвейер NER. Возвращает число УПАВШИХ чанков
    (pass1-ошибка/необработанное исключение; pass2-fallback — не сбой)."""
    total = len(all_chunks)

    pass1_cache: dict[int, list[dict]] = load_chunk_cache(pass1_cache_file, logger)
    pass2_cache: dict[int, list[dict]] = load_chunk_cache(pass2_cache_file, logger)

    cache_lock = threading.Lock()

    todo = [i for i in range(total) if i not in pass2_cache]

    p1_done = len(pass1_cache)
    p2_done = len(pass2_cache)
    failed = 0  # H4 (AUDIT): счётчик упавших чанков

    if not todo:
        _log(logger, logging.INFO,
             f"✅ Все {total} чанков уже обработаны (pass2). Переход к финализации.")
    else:
        _log(logger, logging.INFO,
             f"🔄 Конвейер: {len(todo)} чанков в работе | "
             f"pass1 кэш: {p1_done}/{total} | pass2 кэш: {p2_done}/{total} | "
             f"потоков: {max_workers}")

        already_done = p2_done * 2 + max(0, p1_done - p2_done)
        pbar = tqdm(
            total=total * 2,
            unit="step",
            desc="Two-pass pipeline",
            initial=already_done,
            disable=web_progress_enabled(),
        )
        pbar_lock = threading.Lock()
        # Раунд 20: tqdm при disable=True НЕ двигает pbar.n — считаем сами
        # (иначе web-прогрессбар залипает на 0/N)
        steps_done = already_done
        # Раунд 21: стартовое событие прогресса — бар виден сразу,
        # до первого завершённого шага (медленный LLM)
        emit_progress(steps_done, total * 2, "NER (pass1+pass2)")
        if web_progress_enabled():
            _log(logger, logging.INFO,
                 f"📊 Прогресс: {steps_done}/{total * 2}")

        def _step(n: int = 1) -> None:
            """n шагов прогресса (под pbar_lock — потокобезопасно)."""
            nonlocal steps_done
            with pbar_lock:
                steps_done += n
                pbar.update(n)
                emit_progress(steps_done, total * 2, "NER (pass1+pass2)")

        completed_since_save = 0

        def _process_one_chunk(idx: int) -> tuple[int, list[dict], str | None]:
            """Один поток: pass1 → pass2 для чанка idx."""
            steps_done = 0
            try:
                text = all_chunks[idx]

                # ── PASS 1 ──
                p1_err: str | None = None
                if idx in pass1_cache:
                    p1_ners = pass1_cache[idx]
                    _step()
                    steps_done += 1
                else:
                    _, p1_ners, err = process_chunk_pass1(
                        idx, text, base_url, model_name, api_key,
                        max_retries, timeout, temperature, pass1_prompt, logger,
                        reasoning_effort=reasoning_effort,
                    )
                    if err:
                        _log(logger, logging.ERROR,
                             f"⚠️  Pass1 chunk {idx}/{total}: {err}")
                        tqdm.write(f"⚠️  Pass1 {idx}: {err}")
                        p1_err = err  # H4: сбой pass1 = упавший чанк
                        p1_ners = []
                    else:
                        _log(logger, logging.INFO,
                             f"✅ Pass1 chunk {idx}/{total}: {len(p1_ners)} entities")
                        tqdm.write(f"✅ Pass1 {idx}: {len(p1_ners)} ent.")
                    with cache_lock:
                        pass1_cache[idx] = p1_ners
                    _step()
                    steps_done += 1

                # ── PASS 2 ──
                if idx in pass2_cache:
                    return idx, pass2_cache[idx], None

                if not p1_ners:
                    with cache_lock:
                        pass2_cache[idx] = []
                    _step()
                    steps_done += 1
                    return idx, [], p1_err  # H4: сбой pass1 ≠ fallback

                _, p2_ners, err = process_chunk_pass2(
                    idx, text, p1_ners, base_url, model_name, api_key,
                    max_retries, timeout, temperature, pass2_prompt, logger,
                    reasoning_effort=reasoning_effort,
                )
                if err:
                    _log(logger, logging.WARNING,
                         f"⚠️  Pass2 chunk {idx}/{total}: {err} — fallback to pass1")
                    tqdm.write(f"⚠️  Pass2 {idx}: {err} (fallback)")
                    p2_ners = p1_ners
                else:
                    _log(logger, logging.INFO,
                         f"✅ Pass2 chunk {idx}/{total}: {len(p2_ners)} entities")
                    tqdm.write(f"✅ Pass2 {idx}: {len(p2_ners)} ent.")

                with cache_lock:
                    pass2_cache[idx] = p2_ners
                _step()
                steps_done += 1

                return idx, p2_ners, None

            except Exception as e:
                _log(logger, logging.ERROR,
                     f"💥 Chunk {idx}: необработанная ошибка: {e}")
                tqdm.write(f"💥 Chunk {idx}: {e}")
                with cache_lock:
                    pass1_cache.setdefault(idx, [])
                    pass2_cache.setdefault(idx, [])
                remaining = 2 - steps_done
                if remaining > 0:
                    _step(remaining)
                return idx, [], f"Unhandled: {e}"

        # ── Запуск конвейера ──
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_process_one_chunk, idx): idx
                for idx in todo
            }

            for fut in as_completed(futures):
                idx, ners, err = fut.result()
                if err:
                    failed += 1  # H4: чанк не извлечён

                completed_since_save += 1

                if completed_since_save >= save_interval:
                    with cache_lock:
                        save_chunk_cache(pass1_cache_file, pass1_cache)
                        save_chunk_cache(pass2_cache_file, pass2_cache)
                        p2_snapshot = copy.deepcopy(pass2_cache)
                    save_ner_snapshot(
                        p2_snapshot, ner_file,
                        threshold, ngram_size, logger,
                    )
                    _log(logger, logging.INFO,
                         f"💾 Кэши сохранены ({len(pass2_cache)}/{total} готово)")
                    if web_progress_enabled():
                        _log(logger, logging.INFO,
                             f"📊 Прогресс: {steps_done}/{total * 2}")
                    completed_since_save = 0

        pbar.close()

        with cache_lock:
            save_chunk_cache(pass1_cache_file, pass1_cache)
            save_chunk_cache(pass2_cache_file, pass2_cache)
        _log(logger, logging.INFO,
             f"💾 Финальное сохранение кэшей ({len(pass2_cache)}/{total})")

    # ── Финализация: голосование → дедупликация → словарь ──
    _log(logger, logging.INFO,
         "━━━ ФИНАЛИЗАЦИЯ: Голосование → Дедупликация ━━━")
    finalize_two_pass(pass2_cache, ner_file, threshold, ngram_size, logger)

    for f in (pass1_cache_file, pass2_cache_file):
        try:
            os.remove(f)
            _log(logger, logging.INFO, f"🗑️ Удалён {f}")
        except OSError:
            pass
    if failed:
        _log(logger, logging.WARNING,
             f"⚠️ Не извлечено чанков: {failed}/{total} (частичный результат)")
    return failed


# ══════════════════════════════════════════════════════════════════════
# ФИНАЛИЗАЦИЯ TWO-PASS: ГОЛОСОВАНИЕ → ДЕДУПЛИКАЦИЯ → СЛОВАРЬ
# ══════════════════════════════════════════════════════════════════════


def _compute_final_ner(
    pass2_results: dict[int, list[dict]],
    threshold: float,
    ngram_size: int,
) -> list[dict]:
    """Голосование + дедупликация. Чистая функция, не трогает global_ner_data."""
    groups: dict[str, dict] = {}
    for idx in sorted(pass2_results.keys()):
        seen_in_chunk: set[str] = set()
        for ner in pass2_results[idx]:
            term = str(ner.get("term", "")).strip()
            if not term:
                continue
            norm = normalize_cjk(term)
            if norm in seen_in_chunk:
                continue
            seen_in_chunk.add(norm)
            if norm not in groups:
                groups[norm] = {
                    "term": term, "count": 0,
                    "source_chunks": [], "votes": {}, "last_write": {},
                }
            g = groups[norm]
            g["count"] += 1
            if idx not in g["source_chunks"]:
                g["source_chunks"].append(idx)
            for field, value in ner.items():
                if not _is_storable(field):
                    continue
                if value is None:
                    continue
                val = str(value).strip()
                if not val:
                    continue
                if _is_votable(field):
                    if field not in g["votes"]:
                        g["votes"][field] = {}
                    g["votes"][field][val] = g["votes"][field].get(val, 0) + 1
                else:
                    g["last_write"][field] = val
    candidates: list[dict] = []
    for norm, g in groups.items():
        item = {
            "term": g["term"], "count": g["count"],
            "_source_chunks": g["source_chunks"],
            "_ngrams": get_ngrams(g["term"], n=ngram_size),
            "_len": len(g["term"]),
        }
        for field, votes in g["votes"].items():
            item[field] = _resolve_votes(votes)
            item[_votes_key(field)] = votes
        for field, val in g["last_write"].items():
            item[field] = val
        candidates.append(item)
    final: list[dict] = []
    for cand in candidates:
        is_dup, existing = is_fuzzy_duplicate_in_list(
            cand["term"], final, ngram_size, threshold
        )
        if is_dup:
            assert existing is not None
            existing["count"] += cand["count"]
            for sc in cand.get("_source_chunks", []):
                if sc not in existing["_source_chunks"]:
                    existing["_source_chunks"].append(sc)
            for field in list(cand.keys()):
                if field.startswith(META_PREFIX + "votes_"):
                    if field not in existing:
                        existing[field] = {}
                    for variant, cnt in cand[field].items():
                        existing[field][variant] = existing[field].get(variant, 0) + cnt
                    base_field = field[len(META_PREFIX + "votes_"):]
                    if existing[field]:
                        existing[base_field] = _resolve_votes(existing[field])
                elif _is_storable(field) and not _is_votable(field):
                    existing[field] = cand[field]
        else:
            final.append(cand)
    return final


def save_ner_snapshot(
    pass2_results: dict[int, list[dict]],
    filename: str,
    threshold: float,
    ngram_size: int,
    logger,
) -> None:
    """Промежуточное сохранение ner.json = старые (global) + текущий pass2-кэш."""
    final = _compute_final_ner(pass2_results, threshold, ngram_size)
    with ner_lock:
        base = copy.deepcopy(global_ner_data)
        _merge_into(base, final, threshold, ngram_size)
        merge_alias_groups(base, logger)
        snapshot = [
            {k: v for k, v in item.items() if k not in TRANSIENT_FIELDS}
            for item in base
        ]
        snapshot.sort(key=lambda x: x.get("count", 0), reverse=True)
        tmp_path = filename + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                        json.dump(snapshot, f, ensure_ascii=False, indent=2)
                        f.write("\n")
            os.replace(tmp_path, filename)
        except OSError as exc:
            _log(logger, logging.ERROR, f"⚠ Кэш не сохранён ({exc}): {filename}")
        _log(logger, logging.INFO,
         f"\U0001f4be Промежуточное сохранение ner.json ({len(snapshot)} терминов)")


def _merge_into(
    target: list[dict],
    final: list[dict],
    threshold: float,
    ngram_size: int,
) -> None:
    """Влить final в target (in-place). Общая логика для finalize и snapshot."""
    for item in final:
        is_dup, existing = is_fuzzy_duplicate_in_list(
            item["term"], target, ngram_size, threshold
        )
        if is_dup:
            assert existing is not None
            existing["count"] += item["count"]
            for s in item.get("_source_chunks", []):
                if s not in existing.setdefault("_source_chunks", []):
                    existing["_source_chunks"].append(s)
            for field in list(item.keys()):
                if field.startswith(META_PREFIX + "votes_"):
                    if field not in existing:
                        existing[field] = {}
                    for variant, cnt in item[field].items():
                        existing[field][variant] = existing[field].get(variant, 0) + cnt
                    base_field = field[len(META_PREFIX + "votes_"):]
                    if existing[field]:
                        existing[base_field] = _resolve_votes(existing[field])
                elif _is_storable(field) and not _is_votable(field):
                    existing[field] = item[field]
        else:
            target.append(item)


def finalize_two_pass(
    pass2_results: dict[int, list[dict]],
    filename: str,
    threshold: float,
    ngram_size: int,
    logger,
) -> None:
    global global_ner_data

    final = _compute_final_ner(pass2_results, threshold, ngram_size)

    _log(logger, logging.INFO,
         f"\U0001f4ca Финализация: {len(final)} уникальных терминов.")

    with ner_lock:
        _merge_into(global_ner_data, final, threshold, ngram_size)
        merge_alias_groups(global_ner_data, logger)
        _save_ner_data_unlocked(filename)

    _log(logger, logging.INFO,
         f"💾 Словарь обновлён: {len(global_ner_data)} терминов всего.")


# ══════════════════════════════════════════════════════════════════════
# ОБНОВЛЕНИЕ СЛОВАРЯ (режим БЕЗ two-pass)
# ══════════════════════════════════════════════════════════════════════


def update_global_ner(
    new_ners: list[dict],
    chunk_index: int,
    threshold: float,
    ngram_size: int,
    logger,
) -> tuple[int, int]:
    """Обновить глобальный словарь в памяти. БЕЗ записи на диск."""
    global global_ner_data
    added, updated = 0, 0

    with ner_lock:
        for ner in new_ners:
            term = str(ner.get("term", "")).strip()
            if not term:
                continue

            is_dup, existing = is_fuzzy_duplicate_in_list(
                term, global_ner_data, ngram_size, threshold
            )
            if is_dup:
                assert existing is not None
                existing["count"] = existing.get("count", 1) + 1
                merge_fields(existing, ner)
                src = existing.setdefault("_source_chunks", [])
                if chunk_index not in src:
                    src.append(chunk_index)
                updated += 1
                continue

            ner["term"] = term
            ner["_ngrams"] = get_ngrams(term, n=ngram_size)
            ner["_len"] = len(term)
            ner["count"] = 1
            ner["_source_chunks"] = [chunk_index]
            init_votes(ner)
            global_ner_data.append(ner)
            added += 1
            _log(logger, logging.INFO, f"➕ ADD: '{term}'")

    return added, updated


# ══════════════════════════════════════════════════════════════════════
# ALIAS-MERGE (пост-обработка): схлопывание дублей написания
# ══════════════════════════════════════════════════════════════════════
#
#  Группирует записи с одинаковым нормализованным фонетическим
#  полем («pinyin» или «reading»).
#  Например: 陳陽 (count=231) и 陈阳 (count=51) -> одна запись:
#    term = "陳陽"  (вариант с большим count)
#    aliases = ["陈阳"]
#    count = 282
#    translation/type/pinyin — по суммарным голосам всей группы.
#
#  Если ни «pinyin», ни «reading» в записи нет, запись
#  не участвует в группировке. Скрипт не падает.
#
#  Downstream ищет по: [term] + aliases.
# ══════════════════════════════════════════════════════════════════════

def normalize_phonetic(py: str) -> str:
    """
    Нормализовать фонетическое поле (pinyin / reading) для группировки:
    убрать пробелы → lower → убрать цифры.
    Возвращает "" если поле пусто или отсутствует.
    """
    if not py or not py.strip():
        return ""
    py = py.replace(" ", "").replace("\u3000", "").lower()
    py = re.sub(r"[0-9]", "", py)
    return py


_GENDER_SUFFIX_RE = re.compile(
    r"\s*\((?:unknown|male|female)\)\s*$", re.IGNORECASE
)


def get_type_base(type_str: str) -> str:
    """'Person (male)' -> 'Person', 'Location' -> 'Location'."""
    if not type_str:
        return ""
    return _GENDER_SUFFIX_RE.sub("", type_str.strip()).strip()


def merge_alias_groups(ner_data: list[dict], logger) -> int:
    """
    Схлопнуть записи с одинаковым фонетическим полем в одну.

    Ищет поле «pinyin», затем «reading». Если ни одно не найдено —
    запись пропускается (не участвует в группировке).

    - Основная запись = max(count). Её term сохраняется.
    - Term'ы остальных записей -> aliases основной.
    - Голоса суммируются по всей группе -> победитель перезаписывается.
    - Вторичные записи удаляются из ner_data.

    Returns: количество схлопнутых групп.
    """
    # ── Группировка ──
    groups: dict[str, list[int]] = {}
    for i, item in enumerate(ner_data):
        phonetic = item.get("pinyin", "") or item.get("reading", "")
        key = normalize_phonetic(phonetic)
        if not key:
            continue
        groups.setdefault(key, []).append(i)

    if not groups:
        return 0

    merged = 0
    to_remove: set[int] = set()

    for key, indices in groups.items():
        if len(indices) < 2:
            continue

        # Safety: группа > 4 — логируем, но всё равно мёржим
        if len(indices) > 4:
            terms = [ner_data[i].get("term", "?") for i in indices]
            _log(logger, logging.WARNING,
                 f"⚠️  Alias-группа > 4 записей ({len(indices)}): {terms}")

        # ── Основная запись ──
        primary_idx = max(indices, key=lambda i: ner_data[i].get("count", 0))
        primary = ner_data[primary_idx]

        # ── Сбор данных всей группы ──
        combined_votes: dict[str, dict[str, int]] = {}
        total_count = 0
        all_source_chunks: list = list(primary.get("_source_chunks", []))
        all_aliases: list[str] = list(primary.get("aliases", []))

        for i in indices:
            item = ner_data[i]
            total_count += item.get("count", 0)

            for sc in item.get("_source_chunks", []):
                if sc not in all_source_chunks:
                    all_source_chunks.append(sc)

            if i != primary_idx:
                t = item.get("term", "")
                if t and t not in all_aliases:
                    all_aliases.append(t)
                for a in item.get("aliases", []):
                    if a not in all_aliases:
                        all_aliases.append(a)
                to_remove.add(i)

            for field_key in item:
                if not field_key.startswith("_votes_"):
                    continue
                field = field_key[len("_votes_"):]
                if field not in combined_votes:
                    combined_votes[field] = {}
                for variant, cnt in item[field_key].items():
                    cv = combined_votes[field]
                    cv[variant] = cv.get(variant, 0) + cnt

        # ── Перезапись основной записи ──
        primary["count"] = total_count
        primary["_source_chunks"] = all_source_chunks
        if all_aliases:
            primary["aliases"] = all_aliases

        for field, votes in combined_votes.items():
            primary[f"_votes_{field}"] = votes
            primary[field] = _resolve_votes(votes)

        terms = [ner_data[i].get("term", "?") for i in indices]
        _log(logger, logging.INFO,
             f"🔗 Merge: {terms} -> «{primary['term']}» "
             f"(aliases={all_aliases}, count={total_count})")
        merged += 1

    # ── Удаление вторичных записей (в обратном порядке индексов) ──
    for i in sorted(to_remove, reverse=True):
        ner_data.pop(i)

    if merged:
        _log(logger, logging.INFO,
             f"🔗 Alias-merge: {merged} групп, "
             f"удалено {len(to_remove)} дублей, "
             f"осталось {len(ner_data)} записей.")
    return merged




def postprocess_ner_file(
    filepath: str,
    strip_meta: bool,
    min_count: int | None,
    logger,
) -> None:
    if not os.path.exists(filepath):
        _log(logger, logging.ERROR, f"❌ Файл {filepath} не найден.")
        return

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        _log(logger, logging.ERROR, f"❌ NER не читается: {exc}")
        return

    original_len = len(data)

    if min_count is not None and min_count > 1:
        data = [item for item in data if item.get("count", 1) >= min_count]
        removed = original_len - len(data)
        if removed:
            _log(logger, logging.INFO,
                 f"✂️ --min-count={min_count}: удалено {removed} записей.")

    # ── Alias-merge: схлопывание дублей написания ──
    # Вызывается ДО strip_meta, пока _votes_* ещё на месте.
    merged_groups = merge_alias_groups(data, logger)
    if merged_groups:
        _log(logger, logging.INFO,
             f"🔗 postprocess: схлопнуто {merged_groups} alias-групп.")

    if strip_meta:
        data = [
            {k: v for k, v in item.items() if not k.startswith(META_PREFIX)}
            for item in data
        ]
        _log(logger, logging.INFO, "🧹 --strip-meta: служебные поля удалены.")

    data.sort(key=lambda x: x.get("count", 0), reverse=True)

    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, filepath)
    except OSError as exc:
        _log(logger, logging.ERROR, f"⚠ NER не сохранён ({exc}): {filepath}")
        return

    _log(logger, logging.INFO, f"💾 Сохранено {len(data)} записей в {filepath}")


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════


def main():
    global EXTRA_VOTED_FIELDS

    parser = argparse.ArgumentParser(
        description=(
            "NER Extraction для веб-новелл.\n"
            "Извлечение именованных сущностей через LLM с дедупликацией,\n"
            "голосованием по полям и двухпроходной схемой ревью.\n"
            "Язык и набор полей определяются промптом пользователя."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "\n"
            "  Китайский (встроенные промпты по умолчанию):\n"
            "    python ner.py novel.txt --two-pass\n"
            "\n"
            "  Японский / корейский / любой другой язык:\n"
            "    python ner.py novel.txt --two-pass --prompt_file my_prompts.txt\n"
            "    (язык, поля и инструкции задаются в тексте промпт-файла)\n"
            "\n"
            "  С уровнем усилий размышления:\n"
            "    python ner.py novel.txt --two-pass --reasoning-effort high\n"
            "\n"
            "  Постпроцессинг без входного файла:\n"
            "    python ner.py --ner_file ner.json --min-count 2 --strip-meta\n"
            "\n"
            "Формат промпт-файла (--prompt_file):\n"
            "\n"
            "  <prompt_pass1>\n"
            "    Системный промпт для извлечения NER.\n"
            "  </prompt_pass1>\n"
            "  <prompt_pass2>\n"
            "    Промпт ревью. Плейсхолдеры: {chunk_text}, {ner_json}.\n"
            "  </prompt_pass2>\n"
            "\n"
            "  Если теги отсутствуют, файл целиком используется как pass1.\n"
            "  Pass2 при этом берётся встроенный.\n"
            "\n"
            "Фонетическое поле (alias-merge по звучанию):\n"
            "\n"
            "  Скрипт автоматически обнаруживает в ответе LLM поле \"pinyin\"\n"
            "  или \"reading\" и использует его для группировки вариантов\n"
            "  написания одного термина (alias-merge).\n"
            "\n"
            "  • Китайский: запрашивайте в промпте поле \"pinyin\".\n"
            "  • Остальные языки: запрашивайте поле \"reading\"\n"
            "    (ромадзи, транслитерация и т. п.).\n"
            "  • Если ни одно из полей не запрошено в промпте —\n"
            "    alias-merge по звучанию не выполняется.\n"
            "\n"
            "Единицы:\n"
            "  --chunk_size — СИМВОЛЫ; --threshold (0.0–1.0) и --ngram — безразмерно/символы;\n"
            "  max_tokens (65536) — серверный предохранитель, ТОКЕНЫ.\n"
            ),
    )
    parser.add_argument(
        "file", nargs="?", default=None,
        help="Путь к входному .txt файлу. Не требуется при --strip-meta / --min-count.",
    )
    parser.add_argument(
        "--ner_file", default="ner.json",
        help="Путь к JSON-глоссарию (по умолчанию: ner.json).",
    )
    parser.add_argument(
        "--prompt_file", default=None,
        help=(
            "Путь к файлу промптов. Поддерживает теги <prompt_pass1>...</prompt_pass1> "
            "и <prompt_pass2>...</prompt_pass2>. Если тегов нет — файл целиком = pass1."
        ),
    )
    parser.add_argument(
        "--threads", type=int, default=1,
        help="Число параллельных потоков (по умолчанию: 1, макс: 16).",
    )
    parser.add_argument(
        "--host", default=None,
        help="URL API-сервера (пусто = HOST из .env).",
    )
    parser.add_argument(
        "--api_key", default=None,
        help="API-ключ (пусто = API_KEY из .env).",
    )
    parser.add_argument(
        "--model", default=None,
        help="Модель: --model или MODEL/NER_MODEL в .env (обязательна).",
    )
    parser.add_argument(
        "--no_reasoning", action="store_true",
        help="Не слать reasoning-поле в payload.",
    )
    parser.add_argument(
        "--env_file", default=None, help="Явный путь к .env.",
    )
    parser.add_argument(
        "--chunk_size", type=int, default=7000,
        help="Размер чанка в символах (по умолчанию: 7000).",
    )
    parser.add_argument(
        "--retries", type=int, default=3,
        help="Число повторных попыток при ошибках (по умолчанию: 3).",
    )
    parser.add_argument(
        "--timeout", type=int, default=900,
        help="Таймаут запроса в секундах (по умолчанию: 900).",
    )
    parser.add_argument(
        "--temperature", type=float, default=None,
        help="Температура LLM. Если не указана — значение сервера.",
    )
    parser.add_argument(
        "--reasoning-effort", type=str, default=None,
        help=(
            "Уровень усилий размышления модели (например: low, medium, high). "
            "Передаётся в запрос как есть. По умолчанию не отправляется."
        ),
    )
    parser.add_argument(
        "--threshold", type=float, default=0.75,
        help="Порог Jaccard для дедупликации, 0.0–1.0 (по умолчанию: 0.75).",
    )
    parser.add_argument(
        "--ngram", type=int, default=3,
        help="Размер n-грамм для латиницы (по умолчанию: 3). CJK использует биграммы.",
    )
    parser.add_argument(
        "--two-pass", action="store_true",
        help=(
            "Двухпроходная конвейерная схема: каждый поток выполняет "
            "pass1 → pass2 для чанка, затем берёт следующий. Без барьеров."
        ),
    )
    parser.add_argument(
        "--save-interval", type=int, default=DEFAULT_SAVE_INTERVAL,
        help=(
            "Интервал сохранения на диск (каждые N чанков, "
            f"по умолчанию: {DEFAULT_SAVE_INTERVAL})."
        ),
    )
    parser.add_argument(
        "--keep-fields", type=str, default="", metavar="FIELDS",
        help=(
            "Список полей через запятую, которые ВКЛЮЧАЮТСЯ в голосование. "
            "По умолчанию notes, context, translated_context НЕ голосуют. "
            "Пример: --keep-fields notes,context"
        ),
    )
    parser.add_argument(
        "--keep-all-fields", action="store_true",
        help="Все поля голосуют (включая notes, context, translated_context).",
    )
    parser.add_argument(
        "--strip-meta", action="store_true",
        help=(
            "Удалить служебные поля (_votes_*, _source_chunks) из ner.json. "
            "Можно использовать как постпроцессинг без входного файла."
        ),
    )
    parser.add_argument(
        "--min-count", type=int, default=None, metavar="N",
        help=(
            "Удалить из ner.json записи с count < N. "
            "Например, --min-count 2 уберёт одноразовые термины."
        ),
    )
    parser.add_argument(
        "--compile_chapters", action="store_true",
        help=(
            "Собрать chapters/*/chapter.txt в один файл "
            "(compiled_chapters.txt) и использовать его как вход."
        ),
    )
    parser.add_argument(
        "--chapters_dir", default="chapters",
        help="Папка глав для --compile_chapters (по умолчанию: chapters).",
    )
    parser.add_argument(
        "--compile_out", default="compiled_chapters.txt",
        help="Куда писать собранный txt (--compile_chapters).",
    )

    args = parser.parse_args()
    # Сервер: CLI > HOST/API_KEY/MODEL из .env (раунд 12)
    env_data = parse_dotenv(find_env_file(args.env_file)) if args.env_file \
        else parse_dotenv(find_env_file())
    sc = get_server_config(env_data)
    args.host = args.host or sc["host"] or ""
    args.api_key = args.api_key if args.api_key is not None else sc["api_key"]
    args.model = args.model or get_stage_model(env_data, "ner")
    if not args.host:
        print_env_help()
        sys.exit("❌ Не задан сервер: укажите --host или создайте .env (HOST).")
    if not args.api_key:
        # P1 (AUDIT #2): ключ может прийти из окружения (web-слой)
        args.api_key = os.environ.get("LLM_API_KEY", "")

    # ── Настройка голосования ──
    if args.keep_all_fields:
        EXTRA_VOTED_FIELDS = set(DEFAULT_NON_VOTED_FIELDS)
    elif args.keep_fields:
        EXTRA_VOTED_FIELDS = {
            f.strip() for f in args.keep_fields.split(",") if f.strip()
        }

    # ── Сборка глав (chapter.txt → один txt) ──
    if args.compile_chapters:
        out = args.compile_out or "compiled_chapters.txt"
        try:
            os.makedirs("logs", exist_ok=True)
        except OSError as exc:
            print(f"⚠ logs/ не создаётся: {exc}", file=sys.stderr)
        clog, _ = setup_logging(os.path.join("logs", "ner_compile.log"))
        info = compile_chapter_texts(
            args.chapters_dir, out, want="chapter", logger=clog)
        if info["written"] == 0:
            clog.error("Не собрано ни одной главы из %s", args.chapters_dir)
            return 1
        args.file = out

    # ── Режим постпроцессинга (без входного файла) ──
    postprocess_requested = args.strip_meta or args.min_count is not None
    if args.file is None:
        if postprocess_requested:
            try:
                os.makedirs("logs", exist_ok=True)
            except OSError as exc:
                print(f"⚠ logs/ не создаётся: {exc}", file=sys.stderr)
            logger, _ = setup_logging(os.path.join("logs", "ner_postprocess.log"))
            log_argv(logger)
            postprocess_ner_file(
                args.ner_file, args.strip_meta, args.min_count, logger
            )
            return 0
        parser.error(
            "Укажите входной файл, --compile_chapters или флаги "
            "постпроцессинга (--strip-meta, --min-count)."
        )

    # ── Основной режим ──
    file_dir = os.path.dirname(os.path.abspath(args.file)) or "."
    try:
        os.makedirs("logs", exist_ok=True)
    except OSError as exc:
        print(f"⚠ logs/ не создаётся: {exc}", file=sys.stderr)
    log_path = os.path.join("logs", "ner_extraction.log")
    progress_file = os.path.join(file_dir, "ner_progress.json")  # кэши не переносим
    pass1_cache_file = os.path.join(file_dir, "ner_pass1_cache.json")
    pass2_cache_file = os.path.join(file_dir, "ner_pass2_cache.json")

    logger, _ = setup_logging(log_path)
    log_argv(logger)

    if not os.path.exists(args.file):
        _log(logger, logging.ERROR, f"❌ Файл не найден: {args.file}")
        return 1  # H4 (AUDIT): нет входного файла — код 1

    # Логируем конфигурацию голосования
    voted = ["translation", "type", "pinyin / reading"]
    voted += sorted(EXTRA_VOTED_FIELDS)
    non_voted = sorted(DEFAULT_NON_VOTED_FIELDS - EXTRA_VOTED_FIELDS)
    _log(logger, logging.INFO, f"🗳️ Голосуют: {', '.join(voted)}")
    if non_voted:
        _log(logger, logging.INFO, f"📝 Last-write-wins: {', '.join(non_voted)}")

    load_initial_ner(args.ner_file, args.ngram, logger)
    load_progress(progress_file)

    base_url = args.host.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"

    if args.no_reasoning:
        args.reasoning_effort = None  # раунд 12: disable в select = --no_reasoning
    try:
        model_name = determine_model(args.model, logger)
    except SystemExit:
        return 1  # H4 (AUDIT): модель не определена — код 1

    pass1_prompt = SYSTEM_PROMPT_PASS1
    pass2_prompt = SYSTEM_PROMPT_PASS2
    if args.prompt_file:
        p1, p2 = load_two_pass_prompts(args.prompt_file, logger)
        pass1_prompt = p1
        if p2:
            pass2_prompt = p2

    if args.two_pass:
        if "{chunk_text}" not in pass2_prompt or "{ner_json}" not in pass2_prompt:
            _log(logger, logging.WARNING,
                 "⚠️ Pass2 промпт не содержит {chunk_text} и/или {ner_json}.")

    try:
        with open(args.file, "r", encoding="utf-8") as f:
            full_text = f.read()
    except OSError as exc:
        parser.error(f"Файл не читается: {exc}")

    all_chunks = split_text_smart(
        full_text, target_chars=args.chunk_size, logger=logger
    )
    _flush_log(logger)

    chunks_todo = [
        (i, c) for i, c in enumerate(all_chunks) if i not in processed_chunks
    ]

    if not chunks_todo and not args.two_pass:
        _log(logger, logging.INFO, "✅ Все чанки уже обработаны.")
        if postprocess_requested:
            postprocess_ner_file(
                args.ner_file, args.strip_meta, args.min_count, logger
            )
        return 0

    max_workers = max(1, min(16, args.threads))
    save_interval = max(1, args.save_interval)

    # ════════════════════════════════════════════════════════════════
    # РЕЖИМ: TWO-PASS (конвейерный)
    # ════════════════════════════════════════════════════════════════
    failed_chunks = 0  # H4 (AUDIT): fail-fast, если не извлечён НИ ОДИН чанк

    if args.two_pass:
        _log(logger, logging.INFO,
             f"🚀 TWO-PASS PIPELINE | Модель: {model_name} | "
             f"Чанков: {len(all_chunks)} | Потоков: {max_workers} | "
             f"Save interval: {save_interval}")

        failed_chunks = run_two_pass(
            all_chunks=all_chunks,
            pass1_cache_file=pass1_cache_file,
            pass2_cache_file=pass2_cache_file,
            base_url=base_url,
            model_name=model_name,
            api_key=args.api_key,
            max_retries=args.retries,
            timeout=args.timeout,
            temperature=args.temperature,
            pass1_prompt=pass1_prompt,
            pass2_prompt=pass2_prompt,
            max_workers=max_workers,
            ner_file=args.ner_file,
            threshold=args.threshold,
            ngram_size=args.ngram,
            save_interval=save_interval,
            logger=logger,
            reasoning_effort=args.reasoning_effort,
        )

        try:
            os.remove(progress_file)
        except OSError:
            pass

    # ════════════════════════════════════════════════════════════════
    # РЕЖИМ: ОБЫЧНЫЙ (без two-pass)
    # ════════════════════════════════════════════════════════════════
    else:
        _log(logger, logging.INFO,
             f"🚀 Модель: {model_name} | Чанков: {len(chunks_todo)}/{len(all_chunks)} "
             f"| Потоков: {max_workers} | Save interval: {save_interval}")
        _log(logger, logging.INFO, "━━━ ИЗВЛЕЧЕНИЕ (однопроходное) ━━━")

        chunks_since_save = 0

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    process_chunk_pass1, idx, chunk, base_url, model_name,
                    args.api_key, args.retries, args.timeout,
                    args.temperature, pass1_prompt, logger,
                    args.reasoning_effort,
                ): idx
                for idx, chunk in chunks_todo
            }
            pbar = tqdm(total=len(all_chunks), unit="chunk", desc="Extract",
                        disable=web_progress_enabled())
            pbar.update(len(processed_chunks))
            # Раунд 20: свой счётчик — pbar.n мёртв при disable=True
            done = len(processed_chunks)
            # Раунд 21: стартовое событие прогресса — бар и «📊» видны
            # сразу, до первого завершённого чанка (медленный LLM)
            emit_progress(done, len(all_chunks), "Извлечение терминов")
            if web_progress_enabled():
                _log(logger, logging.INFO,
                     f"📊 Прогресс: {done}/{len(all_chunks)}")

            for fut in as_completed(futures):
                idx, ners, err = fut.result()
                if err:
                    failed_chunks += 1  # H4 (AUDIT): счётчик упавших
                    _log(logger, logging.ERROR, f"⚠️ Chunk {idx}: {err}")
                    tqdm.write(f"⚠️  Chunk {idx}: {err}")
                elif ners:
                    a, u = update_global_ner(
                        ners, idx, args.threshold, args.ngram, logger,
                    )
                    _log(logger, logging.INFO,
                         f"✅ Chunk {idx}: +{a} new | +{u} hits")
                    tqdm.write(f"Chunk {idx}: +{a} new | +{u} hits")
                else:
                    _log(logger, logging.INFO, f"✅ Chunk {idx}: 0 entities")

                with ner_lock:
                    processed_chunks.add(idx)
                    save_progress_unlocked(progress_file)

                chunks_since_save += 1
                pbar.update(1)
                done += 1
                emit_progress(done, len(all_chunks),
                              "Извлечение терминов")

                if chunks_since_save >= save_interval:
                    with ner_lock:
                        merge_alias_groups(global_ner_data, logger)
                        _save_ner_data_unlocked(args.ner_file)
                    _log(logger, logging.INFO,
                         f"💾 Промежуточное сохранение "
                         f"({len(global_ner_data)} терминов)")
                    if web_progress_enabled():
                        _log(logger, logging.INFO,
                             f"📊 Прогресс: {done}/{len(all_chunks)}")
                    chunks_since_save = 0

            pbar.close()

        with ner_lock:
            merge_alias_groups(global_ner_data, logger)
            _save_ner_data_unlocked(args.ner_file)

        _log(logger, logging.INFO,
             f"💾 Финальное сохранение ({len(global_ner_data)} терминов)")

        if len(processed_chunks) == len(all_chunks):
            try:
                os.remove(progress_file)
            except OSError:
                pass

    # ════════════════════════════════════════════════════════════════
    # ПОСТПРОЦЕССИНГ
    # ════════════════════════════════════════════════════════════════
    _log(logger, logging.INFO,
         f"🏁 Готово. Терминов: {len(global_ner_data)}. Файл: {args.ner_file}")
    if postprocess_requested:
        postprocess_ner_file(
            args.ner_file, args.strip_meta, args.min_count, logger
        )

    # H4 (AUDIT): все чанки упали → код 1 (частичный успех — 0 + warning)
    if failed_chunks:
        total_todo = len(chunks_todo) if not args.two_pass else len(all_chunks)
        if failed_chunks == total_todo:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())  # H4 (AUDIT): код возврата main() наружу

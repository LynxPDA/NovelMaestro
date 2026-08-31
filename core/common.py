#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/common.py — единый общий модуль проекта NovelMaestro.
Замена core/utils.py. Зависимости: stdlib + requests (+ опционально pyahocorasick).
tiktoken НЕ используется и не требуется.

СОГЛАШЕНИЕ О ЕДИНИЦАХ (важно):
  • ВСЕ внутренние расчёты — в СИМВОЛАХ: chunk_size, context_budget,
    min_len_ratio (безразмерная), пороги длин.
  • ТОКЕНЫ встречаются ТОЛЬКО в двух местах:
      - max_tokens в payload — серверный предохранитель, не расчёт;
      - --near-distance в wiki.py — дистанция FTS5 NEAR (природа FTS5).

СОДЕРЖИМОЕ (для заимствований):
  конфиг/.env   parse_dotenv, find_env_file,
                get_server_config, get_stage_model, print_env_help
  логирование   setup_logging
  модель        determine_model
  промпты       load_prompt, get_tagged_prompt
  текст         split_text_smart, get_ngrams, is_cjk, is_cjk_string,
                normalize_for_search, build_smart_regex, find_exact_match
  NER           load_ner_data, find_relevant_ner
  LLM           stream_chat_completion
  файловая ФС   atomic_write, read_text_safe
  главы         parse_chapter_id, build_chapter_map, find_chapter_file,
                format_ranges, compile_chapter_text, compile_chapter_texts
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import random
import re
import sys
import tempfile
import threading
import time
import unicodedata
from collections import defaultdict

import requests

# ══════════════════════════════════════════════════════════════════════
# .ENV / КОНФИГ (stdlib, без внешних зависимостей)
# ══════════════════════════════════════════════════════════════════════
def parse_dotenv(path) -> dict:
    """Парсит KEY=VALUE. Комментарии/пустые строки игнор, 'export ' терпим,
    парные кавычки снимаются. Файла нет → {} (не падает)."""
    result: dict = {}
    if not path or not os.path.isfile(path):
        return result
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                    value = value[1:-1]
                result[key] = value
    except OSError:
        pass
    return result


def find_env_file(explicit=None, start_dir=None):
    """Ищет .env БЕЗ хардкода абсолютных путей: явный путь → вверх от
    start_dir/каталога common/cwd — единственный кандидат <dir>/.env.
    При подъёме из папки проекта первым находится собственный pdir/.env
    книги, из корня репо — системный корневой .env (канон web-first).
    Не найден → None (скрипт обязан работать дальше с ручным вводом)."""
    if explicit and os.path.isfile(explicit):
        return os.path.abspath(explicit)
    bases, seen = [], set()
    for b in (start_dir, os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
        ab = os.path.abspath(b) if b else None
        if ab and ab not in seen:
            seen.add(ab)
            bases.append(ab)
    for base in bases:
        d = base
        for _ in range(6):  # подъём вверх по дереву
            cand = os.path.join(d, ".env")
            if os.path.isfile(cand):
                return os.path.abspath(cand)
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return None


def get_server_config(env_data: dict, stage: str = "") -> dict:
    """Сервер → {host, api_key, model}.

    stage непуст — схема «один скрипт — один набор сервер+ключ+модель»:
    <СТАДИЯ>_HOST/<СТАДИЯ>_API_KEY/<СТАДИЯ>_MODEL переопределяют общие
    HOST/API_KEY/MODEL (модель — через get_stage_model). Пустая стадия —
    только общие ключи (профили local/remote убраны).

    Приоритет (канон §7): os.environ > .env: в Docker конфиг приходит
    переменными окружения (env_file в compose), файла .env в образе нет.
    env_data — значения, распарсенные из файла .env.
    """
    s = (stage or "").upper()

    def val(name: str) -> str:
        """Значение ключа: os.environ > env_data (оба обрезаны)."""
        raw = os.environ.get(name)
        if raw is not None and raw.strip():
            return raw.strip()
        return env_data.get(name, "").strip()

    host = val(f"{s}_HOST" if s else "HOST")
    if not host:
        host = val("HOST")
    api_key = val(f"{s}_API_KEY" if s else "API_KEY")
    if not api_key:
        api_key = val("API_KEY")
    return {
        "host": host,
        "api_key": api_key,
        "model": get_stage_model(env_data, stage),
    }


def get_stage_model(env_data: dict, stage: str = "") -> str:
    """Модель этапа: <STAGE>_MODEL → общая MODEL → ''.
    stage пуст → сразу общая модель. os.environ приоритетнее файла."""

    def val(name: str) -> str:
        raw = os.environ.get(name)
        if raw is not None and raw.strip():
            return raw.strip()
        return env_data.get(name, "").strip()

    if stage:
        stage_model = val((stage or "").upper() + "_MODEL")
        if stage_model:
            return stage_model
    return val("MODEL")


def print_env_help() -> None:
    """Подсказка: что можно записать в .env (печатается при его отсутствии)."""
    print("─" * 60)
    print("ℹ .env не найден. Сервер можно задать вручную (--host/--model/--api_key)")
    print("  или создать .env (пример — в .env.example):")
    print("    HOST=http://192.168.1.8:9989")
    print("    API_KEY=")
    print("    MODEL=")
    print("  Модели по скриптам (необязательно; не задана — общая MODEL):")
    print("    NER_MODEL= | NER_CHECK_MODEL=")
    print("    TRANSLATE_CHECK_LLM_MODEL= | WIKI_MODEL=")
    print("    PIPELINE_MODEL=   # web-конвейер (единая модель)")
    print("  Опциональные дефолты (CLI всегда приоритетнее):")
    print("    CHUNK_SIZE=7000 | NER_CHUNK_SIZE=10000")
    print("    NER_THRESHOLD=0.75 | MIN_LEN_RATIO_TRANSLATE=0.5")
    print("    MIN_LEN_RATIO_REDACT=0.9 | TIMEOUT=300 | STREAM_TIMEOUT=900")
    print("    MAX_RETRIES=3")
    print("─" * 60)


# ══════════════════════════════════════════════════════════════════════
# ЛОГИРОВАНИЕ / МОДЕЛЬ
# ══════════════════════════════════════════════════════════════════════
_SECRET_FLAG_RE = re.compile(
    r"^(--[A-Za-z0-9_-]*(?:key|token|secret|password|auth)[A-Za-z0-9_-]*)"
    r"(?:=(\S+))?$",
    re.IGNORECASE,
)


def _mask_secret_argv(cmd: list[str]) -> list[str]:
    """Маскирует значения после флагов с key/token/secret/password/auth
    в названии (включая форму --flag=value). M2 (AUDIT): ключи не в лог."""
    masked: list[str] = []
    i = 0
    while i < len(cmd):
        a = cmd[i]
        m = _SECRET_FLAG_RE.match(a)
        if m:
            if m.group(2) is not None:
                masked.append(f"{m.group(1)}=••••")
            else:
                masked.append(m.group(1))
                masked.append("••••")  # следующий элемент — значение
                i += 1
        else:
            masked.append(a)
        i += 1
    return masked


def log_argv(logger, argv=None, prefix="Запуск"):
    """Пишет фактическую команду запуска в лог (R9):
    logger.info(f'{prefix}: {shlex.join(argv)}'); argv=None → sys.argv.
    Значения секретных флагов (--*api_key* и т.п.) маскируются (M2)."""
    import shlex
    cmd = argv if argv is not None else sys.argv
    logger.info("%s: %s", prefix, shlex.join(_mask_secret_argv(list(cmd))))


def setup_logging(output_filename, logger_name=None):
    """Логгер файл+консоль. Файл = output_filename с заменой расширения на .log.
    Повторный вызов с тем же именем не дублирует хендлеры."""
    log_filename = os.path.splitext(output_filename)[0] + ".log"
    name = logger_name or ("bookllm." + os.path.basename(log_filename))
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh = logging.FileHandler(log_filename, encoding="utf-8", mode="w")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger, log_filename


def determine_model(arg_model, logger=None):
    """Модель ТОЛЬКО из аргумента/конфига (автоопределение
    через GET /models убрано). Пусто → SystemExit (fail-fast)."""
    if arg_model:
        if logger:
            logger.info(f"🤖 Модель: {arg_model}")
        return arg_model
    if logger:
        logger.error("❌ Модель не задана: укажите --model или MODEL в .env")
    raise SystemExit("Модель не задана: укажите --model или MODEL в .env")


# ══════════════════════════════════════════════════════════════════════
# ПРОМПТЫ
# ══════════════════════════════════════════════════════════════════════
def load_prompt(path, logger=None):
    """Файл целиком = промпт этапа (режим без тегов). Нет файла/пусто → None."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        return content or None
    except OSError as e:
        if logger:
            logger.warning(f"⚠️ Не удалось прочитать промпт {path}: {e}")
        return None


def get_tagged_prompt(content: str, tag: str):
    """Извлекает <tag>...</tag> (DOTALL). Тега нет → None."""
    if not content:
        return None
    m = re.search(rf"<{tag}>(.*?)</{tag}>", content, re.DOTALL)
    return m.group(1).strip() if m else None


# ══════════════════════════════════════════════════════════════════════
# ТЕКСТ (всё в символах)
# ══════════════════════════════════════════════════════════════════════
def get_ngrams(text, n=3) -> set:
    """Множество n-грамм строки (lower)."""
    if not text:
        return set()
    text = text.lower().strip()
    if len(text) < n:
        return {text}
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def split_text_smart(text, target_chars=7000, multiplier=1.3, logger=None):
    """Разбивка на чанки ПО СИМВОЛАМ (абзацы → предложения).
    hard_limit = target * multiplier."""
    if logger:
        logger.info(f"✂️  Разбиение текста (цель: {target_chars} символов)...")
    lines = text.splitlines(keepends=True)
    chunks, current_chunk = [], []
    current_len = 0
    try:
        hard_limit = int(target_chars * multiplier)
    except (TypeError, ValueError, OverflowError):
        hard_limit = target_chars
    sent_re = re.compile(r"(?<=[.!?。؟؟;；])\s+")
    for line in lines:
        line_len = len(line)
        if line_len > hard_limit:
            sentences = sent_re.split(line.rstrip("\n"))
            for sent in sentences:
                sent_len = len(sent)
                if current_len + sent_len > hard_limit and current_chunk:
                    chunks.append("".join(current_chunk))
                    current_chunk, current_len = [], 0
                current_chunk.append(sent + " ")
                current_len += sent_len
                if line.endswith("\n") and current_chunk:
                    current_chunk[-1] += "\n"
            continue
        if current_len + line_len > hard_limit and current_chunk:
            chunks.append("".join(current_chunk))
            current_chunk, current_len = [], 0
        current_chunk.append(line)
        current_len += line_len
        if current_len >= target_chars:
            chunks.append("".join(current_chunk))
            current_chunk, current_len = [], 0
    if current_chunk:
        chunks.append("".join(current_chunk))
    if logger:
        logger.info(f"✅ Текст разбит на {len(chunks)} частей.")
    return chunks


def is_cjk(c: str) -> bool:
    """Один символ — CJK (включая кану и хангыль)."""
    cp = ord(c) if c else 0
    return (
        0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF
        or 0x20000 <= cp <= 0x2A6DF or 0x2A700 <= cp <= 0x2B73F
        or 0xF900 <= cp <= 0xFAFF or 0x2F800 <= cp <= 0x2FA1F
        or 0x3040 <= cp <= 0x309F or 0x30A0 <= cp <= 0x30FF
        or 0xAC00 <= cp <= 0xD7AF
    )


def is_cjk_string(s: str) -> bool:
    if not s:
        return False
    return sum(1 for c in s if is_cjk(c)) / len(s) > 0.5


_SEARCH_DROP_RE = re.compile(r"[\s\u3000\u200b.,!?;:()«»\"'’‘…—–\-]+")


def normalize_for_search(s: str) -> str:
    """Нормализация для поиска: NFC → lower → убрать пробелы/пунктуацию.
    Одинаково применяется к терминам и к тексту."""
    s = unicodedata.normalize("NFC", s or "").lower()
    return _SEARCH_DROP_RE.sub("", s)


def build_smart_regex(term: str, flags=re.IGNORECASE):
    """Regex по термину, терпимый к любым пробелам между словами."""
    parts = [re.escape(p) for p in re.split(r"\s+", (term or "").strip()) if p]
    if not parts:
        return re.compile(r"a^")  # не матчит ничего
    return re.compile(r"\s+".join(parts), flags)


def find_exact_match(text: str, term: str) -> bool:
    """Точное (терпимое к пробелам/регистру) вхождение термина в текст."""
    if not text or not term:
        return False
    return bool(build_smart_regex(term).search(text))


# ══════════════════════════════════════════════════════════════════════
# NER: загрузка глоссария + релевантные термины
# ══════════════════════════════════════════════════════════════════════
def load_ner_data(filepath, ngram_size, logger):
    """Загрузка глоссария с aliases и построением Aho-Corasick
    (без библиотеки — regex fallback). Возвращает (processed_data, automaton)."""
    if not filepath or not os.path.exists(filepath):
        logger.warning(f"⚠️ NER file ({filepath}) not found. Working without dictionary.")
        return [], None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"❌ Error reading NER file: {e}")
        return [], None

    processed_data = []
    variant_to_idx: dict = {}
    for item in data:
        term = item.get("term", "")
        if not term:
            continue
        variants = [term] + item.get("aliases", [])
        term_norm = normalize_for_search(term)
        item["_term_norm"] = term_norm
        item["_term_lower"] = term_norm  # совместимость
        item["_ngrams"] = get_ngrams(term_norm, n=ngram_size) if len(term_norm) >= 3 else set()
        # русское написание — для поиска имён по translation (polish)
        trans_norm = normalize_for_search(item.get("translation", ""))
        item["_translation_norm"] = trans_norm
        item["_ngrams_translation"] = get_ngrams(trans_norm, n=ngram_size) \
            if len(trans_norm) >= 3 else set()
        idx = len(processed_data)
        processed_data.append(item)
        for v in variants:
            v_norm = normalize_for_search(v)
            if v_norm:
                variant_to_idx[v_norm] = idx

    automaton = None
    try:
        import ahocorasick
        automaton = ahocorasick.Automaton()
        for v_norm, idx in variant_to_idx.items():
            automaton.add_word(v_norm, (v_norm, idx))
        automaton.make_automaton()
        logger.info(f"✅ Loaded {len(processed_data)} terms, "
                    f"{len(variant_to_idx)} variants (Aho-Corasick built).")
    except ImportError:
        logger.info(f"✅ Loaded {len(processed_data)} terms, "
                    f"{len(variant_to_idx)} variants (regex fallback, "
                    f"pip install pyahocorasick for speedup).")
        automaton = ("regex_fallback", variant_to_idx)
    return processed_data, automaton


def _fuzzy_hit(term_norm, term_ngrams, text_norm, text_ngrams, threshold):
    """Точное вхождение нормализованной строки либо нечёткое:
    n-граммное пересечение >= threshold + longest match >= 0.8 длины."""
    if not term_norm:
        return False
    if term_norm in text_norm:
        return True
    if not term_ngrams:
        return False
    inter = len(term_ngrams & text_ngrams)
    if inter / len(term_ngrams) < threshold:
        return False
    lm = difflib.SequenceMatcher(None, term_norm, text_norm) \
        .find_longest_match(0, len(term_norm), 0, len(text_norm))
    return lm.size >= len(term_norm) * 0.8


def find_relevant_ner(text, ner_data, threshold, ngram_size, ner_fields,
                      automaton=None, include_aliases=True):
    """Поиск релевантных терминов: Aho-Corasick/regex → n-граммы (не-CJK).
    Возвращает (JSON-строка, count). ner_fields — 'f1,f2,...'."""
    if not ner_data or not text:
        return "[]", 0
    text_norm = normalize_for_search(text)
    found: set = set()

    if automaton is not None:
        if isinstance(automaton, tuple) and automaton[0] == "regex_fallback":
            # регэксп-чередование с longest-first НЕ находит перекрытия
            # (короткий термин-префикс внутри длинного) — проверяем каждый
            # вариант вхождением, семантика как у Aho-Corasick: «есть ли
            # термин в тексте» (только медленнее: V×len(text))
            _, variant_to_idx = automaton
            for v_norm, idx in variant_to_idx.items():
                if v_norm and v_norm in text_norm:
                    found.add(idx)
        else:
            if isinstance(automaton, tuple):
                raise TypeError("Неизвестный формат автомата")
            for _end, (_variant, idx) in automaton.iter(text_norm):
                found.add(idx)

    text_ngrams = get_ngrams(text_norm, n=ngram_size)
    for i, item in enumerate(ner_data):
        if i in found:
            continue
        term_norm = item["_term_norm"]
        if not term_norm or is_cjk(term_norm[0]):
            continue
        if _fuzzy_hit(term_norm, item["_ngrams"], text_norm,
                      text_ngrams, threshold):
            found.add(i)

    if not found:
        return "[]", 0

    fields = [f.strip() for f in ner_fields.split(",") if f.strip()]
    wants_aliases = "aliases" in fields
    entries, seen = [], set()
    for i in sorted(found):
        item = ner_data[i]
        if item["term"] in seen:
            continue
        seen.add(item["term"])
        entry = {f: item[f] for f in fields if f in item}
        if (include_aliases and not wants_aliases
                and item.get("aliases") and "aliases" not in entry):
            entry["aliases"] = item["aliases"]
        entries.append(entry)
    return json.dumps(entries, ensure_ascii=False, indent=2), len(entries)


def _gender_of_type(type_str) -> str:
    """Пол по полю type: наличие '(female)'/'(male)' (регистр не важен).
    Скобки исключают ложное вхождение 'male' внутри 'female'.
    Возвращает 'female' / 'male' / '' (пола нет)."""
    t = (type_str or "").lower()
    if "(female)" in t:
        return "female"
    if "(male)" in t:
        return "male"
    return ""


def collect_gender_names(text, ner_data, threshold=0.75, ngram_size=3):
    """Поиск в тексте имён из ner.json ПО ПОЛЮ translation (русское
    написание; term не используется). Пол — по наличию '(female)'/'(male)'
    в поле type (Person/Creature/составные типы; '(unknown)' — без пола).
    Возвращает (female, male) — списки translation найденных записей:
    дедупликация по нормализованному написанию, сортировка
    count desc → алфавит. Пустой вход → ([], [])."""
    if not text or not ner_data:
        return [], []
    text_norm = normalize_for_search(text)
    if not text_norm:
        return [], []
    text_ngrams = get_ngrams(text_norm, n=ngram_size)
    buckets: dict = {"female": [], "male": []}
    seen: set = set()
    for item in ner_data:
        trans = (item.get("translation") or "").strip()
        if not trans:
            continue
        gender = _gender_of_type(item.get("type"))
        if not gender:
            continue
        t_norm = item.get("_translation_norm")
        if t_norm is None:
            t_norm = normalize_for_search(trans)
        if not t_norm or t_norm in seen:
            continue
        ngrams = item.get("_ngrams_translation")
        if ngrams is None:
            ngrams = get_ngrams(t_norm, n=ngram_size) if len(t_norm) >= 3 else set()
        if not _fuzzy_hit(t_norm, ngrams, text_norm, text_ngrams, threshold):
            continue
        seen.add(t_norm)
        buckets[gender].append(item)

    def _key(it):
        return (-(it.get("count") or 0),
                (it.get("translation") or "").strip().lower())

    return tuple([(it.get("translation") or "").strip()
                  for it in sorted(buckets[g], key=_key)]
                 for g in ("female", "male"))


# ══════════════════════════════════════════════════════════════════════
# NER-CHECK: фильтры/формат/батчи глоссария + правки LLM (JSON-патчи)
# и review-файл для человека (статусы принять/отклонить, накопление
# по этапам, флаги применения).
# ══════════════════════════════════════════════════════════════════════
NER_PATCH_FIELDS = ("translation", "type", "notes")
REVIEW_ACCEPT = "принять"
REVIEW_REJECT = "отклонить"
REVIEW_STATUSES = (REVIEW_ACCEPT, REVIEW_REJECT)


def filter_ner_items(items, count_threshold=0, types=None,
                     exclude_words=None):
    """Фильтр записей нер.json: порог count, список типов, подстроки
    в notes для исключения. types/exclude_words — списки или None."""
    types = [t.strip() for t in types if t.strip()] if types else []
    exclude = [w.strip().lower() for w in (exclude_words or []) if w.strip()]
    out = []
    for item in items:
        if types and item.get("type", "") not in types:
            continue
        if (item.get("count", 0) or 0) <= count_threshold:
            continue
        notes = item.get("notes", "")
        notes = notes if isinstance(notes, str) else str(notes or "")
        notes_lower = notes.lower()
        if any(w in notes_lower for w in exclude):
            continue
        out.append(item)
    return out


def format_ner_record(item, idx, show_aliases=False, show_votes=False):
    """Блок одной записи глоссария (список строк). idx — номер записи."""
    lines = [f"--- Запись {idx} ---", f"term: {item.get('term', '')}"]
    aliases = item.get("aliases", [])
    if aliases and show_aliases:
        lines.append(f"aliases: {', '.join(aliases)}")
    lines.append(f"type: {item.get('type', '')}")
    lines.append(f"translation: {item.get('translation', '')}")
    lines.append(f"context: {item.get('context', '')}")
    if item.get("notes"):
        lines.append(f"notes: {item['notes']}")
    if show_votes:
        for key in ("_votes_translation", "_votes_type", "_votes_pinyin"):
            votes = item.get(key)
            if votes and len(votes) > 1:
                top = sorted(votes.items(), key=lambda x: -x[1])[:3]
                votes_str = ", ".join(f'"{v}":{c}' for v, c in top)
                field_name = key.replace("_votes_", "")
                lines.append(f"  [{field_name} голоса: {votes_str}]")
    lines.append("")
    return lines


def glossary_body(items, show_aliases=False, show_votes=False):
    """Тело глоссария для промпта: записи с перенумерацией 1..N."""
    lines = []
    for i, item in enumerate(items, 1):
        lines.extend(format_ner_record(item, i, show_aliases, show_votes))
    return "\n".join(lines)


def build_ner_batches(items, budget, show_aliases=False, show_votes=False):
    """Резка глоссария на батчи по бюджету (СИМВОЛЫ).
    Записи сортируются по count по убыванию — самые частотные термины
    попадают в первый батч. Возвращает список списков записей;
    в норме один батч (бюджет ~ контекст сервера)."""
    ordered = sorted(items,
                     key=lambda it: -((it.get("count") or 0)))
    batches, cur, cur_len = [], [], 0
    for item in ordered:
        block = "\n".join(
            format_ner_record(item, 0, show_aliases, show_votes))
        if cur and cur_len + len(block) > budget:
            batches.append(cur)
            cur, cur_len = [], 0
        cur.append(item)
        cur_len += len(block)
    if cur:
        batches.append(cur)
    return batches


def parse_ner_patches(text, logger=None):
    """Разбор ответа LLM: JSON-массив патчей
    [{term, field, old, new, reason?}]. Терпим к код-заборам и мусору
    вокруг JSON. Возвращает список патчей (dict) или None, если не
    распарсилось. field нормализуется (lower) и проверяется по
    NER_PATCH_FIELDS."""
    s = (text or "").strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    start, end = s.find("["), s.rfind("]")
    if start == -1 or end <= start:
        if logger:
            logger.warning("⚠ В ответе LLM не найден JSON-массив.")
        return None
    try:
        raw = json.loads(s[start:end + 1])
    except json.JSONDecodeError as e:
        if logger:
            logger.warning(f"⚠ JSON патчей не распарсился: {e}")
        return None
    if not isinstance(raw, list):
        return None
    patches = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        term = str(p.get("term", "")).strip()
        field = str(p.get("field", "")).strip().lower()
        if not term or field not in NER_PATCH_FIELDS:
            continue
        patches.append({
            "term": unicodedata.normalize("NFC", term),
            "field": field,
            "old": unicodedata.normalize("NFC", str(p.get("old", ""))),
            "new": unicodedata.normalize("NFC", str(p.get("new", ""))),
            "reason": str(p.get("reason", "")).strip(),
        })
    return patches


def review_entry(raw, stage=""):
    """Нормализация одной правки в запись review-файла.
    Понимает и legacy-патч {term,field,old,new,reason}, и полную запись
    со статусами. NFC для term/old/new; field проверяется по
    NER_PATCH_FIELDS. Возвращает dict-запись или None, если запись
    некорректна (нет term/поле вне списка)."""
    if not isinstance(raw, dict):
        return None
    term = unicodedata.normalize("NFC", str(raw.get("term", "")).strip())
    field = str(raw.get("field", "")).strip().lower()
    if not term or field not in NER_PATCH_FIELDS:
        return None
    status = str(raw.get("status", REVIEW_ACCEPT)).strip().lower()
    if status not in REVIEW_STATUSES:
        status = REVIEW_ACCEPT
    return {
        "stage": str(raw.get("stage") or stage),
        "term": term,
        "field": field,
        "old": unicodedata.normalize("NFC", str(raw.get("old", ""))),
        "new": unicodedata.normalize("NFC", str(raw.get("new", ""))),
        "reason": str(raw.get("reason") or "").strip(),
        "status": status,
        "applied": bool(raw.get("applied", False)),
    }


def parse_review_doc(doc, logger=None):
    """Разбор содержимого review-файла: массив (legacy ner_patches.json)
    или объект с ключом «правки». Возвращает список записей или None,
    если структура не распознана."""
    if isinstance(doc, dict):
        rows = doc.get("entries")
    else:
        rows = doc
    if not isinstance(rows, list):
        if logger:
            logger.warning("⚠ Review-файл: не найден список правок.")
        return None
    return [e for e in (review_entry(r) for r in rows) if e]


def merge_review_entries(existing, new, logger=None):
    """Накопление правок: к существующим записям добавляются новые,
    дедупликация по (term, field, old, new). Статусы/флаги существующих
    записей НЕ трогаются — решения человека не затираются повторным
    прогоном. Возвращает (merged, added)."""
    def key(e):
        return (e["term"], e["field"], e["old"], e["new"])
    seen = {key(e) for e in existing}
    merged, added = list(existing), 0
    for e in new:
        k = key(e)
        if k in seen:
            continue
        seen.add(k)
        merged.append(e)
        added += 1
    if logger and added:
        logger.info(f"  ⊕ Новых правок в review-файл: {added}")
    return merged, added


def apply_ner_patches(items, patches, logger=None):
    """Применение правок к записям нер.json (in-place).
    Запись применяется, если статус == «принять» (или отсутствует —
    legacy-патчи), флаг «применено» не стоит, термин существует
    (точное совпадение, NFC) и поле записи совпадает с old (NFC).
    Правка ложится на ПЕРВУЮ запись термина с совпавшим old — это
    корректно и для дублей термина с разными значениями поля.
    Успешные записи помечаются in-place: применено=True +
    «дата применения». Возвращает (applied: список dict,
    skipped: int — отклонённые/уже применённые/не совпавшие)."""
    index = {}
    for item in items:
        term = unicodedata.normalize("NFC", str(item.get("term", "")))
        if term:
            index.setdefault(term, []).append(item)
    applied, skipped = [], 0
    for p in patches:
        status = str(p.get("status", REVIEW_ACCEPT)).strip().lower()
        if status == REVIEW_REJECT or p.get("applied"):
            skipped += 1
            continue
        term = unicodedata.normalize("NFC", str(p.get("term", "")))
        cands = index.get(term)
        if not cands:
            skipped += 1
            continue
        old = unicodedata.normalize("NFC", str(p.get("old", "")))
        new = unicodedata.normalize("NFC", str(p.get("new", "")))
        if new == old:
            skipped += 1
            continue
        target = None
        for item in cands:
            current = unicodedata.normalize(
                "NFC", str(item.get(p["field"], "") or ""))
            if current == old:
                target = item
                break
        if target is None:
            skipped += 1
            continue
        target[p["field"]] = p["new"]
        p["applied"] = True
        p["applied_at"] = time.strftime("%Y-%m-%d %H:%M")
        applied.append(p)
        if logger:
            stage = p.get("stage") or ""
            prefix = f"[{stage}] " if stage else ""
            logger.info(f"  ✔ {prefix}{p['term']} [{p['field']}]: "
                        f"{p['old']!r} → {p['new']!r}")
    return applied, skipped


# ══════════════════════════════════════════════════════════════════════
# translate_check_llm: review-записи правок текста глав (fragment → corrected)
# ══════════════════════════════════════════════════════════════════════
FIX_ERROR_TYPES = ("typo", "missing_word", "grammar", "logic",
                   "incomplete", "artifact")


def fix_entry(raw, stage=""):
    """Сырая ошибка LLM {chapter, fragment, corrected, type, reason}
    → нормализованная запись review-файла. Возвращает None, если
    запись некорректна (нет главы/фрагмента/правки, old == new).
    old/new — NFC. Статус по умолчанию «принять» (человек правит
    в файле на «отклонить» при необходимости)."""
    if not isinstance(raw, dict):
        return None
    ch = raw.get("chapter")
    if isinstance(ch, str):
        ch = ch.strip()
        try:
            ch = int(ch) if ch.isdigit() else None
        except (ValueError, OverflowError):
            ch = None
    elif isinstance(ch, float):
        try:
            ch = int(ch)
        except (ValueError, OverflowError):
            ch = None
    if not isinstance(ch, int):
        return None
    old = str(raw.get("fragment") or "").strip()
    new = str(raw.get("corrected") or "").strip()
    if not old or not new or old == new:
        return None
    status = str(raw.get("status") or REVIEW_ACCEPT).strip().lower()
    if status not in REVIEW_STATUSES:
        status = REVIEW_ACCEPT
    etype = str(raw.get("type") or "").strip().lower()
    if etype and etype not in FIX_ERROR_TYPES:
        etype = ""
    return {"stage": str(stage or ""), "chapter": ch,
            "file": str(raw.get("file") or ""),
            "type": etype,
            "old": unicodedata.normalize("NFC", old),
            "new": unicodedata.normalize("NFC", new),
            "reason": str(raw.get("reason") or "").strip(),
            "status": status, "applied": bool(raw.get("applied"))}


def merge_fix_entries(existing, fresh, logger=None):
    """Накопление правок без затирания решений человека: существующая
    запись с тем же (глава, old, new) сохраняется как есть, новая не
    добавляется. Возвращает (merged_list, added_count)."""
    merged = list(existing)
    seen = {(e.get("chapter"), e.get("old"), e.get("new")) for e in merged}
    added = 0
    for e in fresh:
        key = (e.get("chapter"), e.get("old"), e.get("new"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(e)
        added += 1
    if logger and added:
        logger.info(f"➕ Новых правок: {added} (всего: {len(merged)})")
    return merged, added


def apply_fix_to_text(text, old, new):
    """Одна замена фрагмента в тексте: NFC с обеих сторон, первое
    вхождение. Возвращает (новый_текст, True) или (None, False), если
    фрагмент не найден."""
    tn = unicodedata.normalize("NFC", text)
    on = unicodedata.normalize("NFC", str(old))
    if on not in tn:
        return None, False
    return tn.replace(on, unicodedata.normalize("NFC", str(new)), 1), True


# ══════════════════════════════════════════════════════════════════════
# LLM: единый стрим-запрос (гигиена стрима — одна реализация на всех)
# ══════════════════════════════════════════════════════════════════════
_LOOP_RES = (
    re.compile(r"(.{1,3})\1{50,}"),
    re.compile(r"(.{4,15})\1{15,}"),
    re.compile(r"(.{16,})\1{10,}"),
)


# H3 (AUDIT): ретраим ТОЛЬКО перегрузки/нестабильность сервера;
# 4xx-ошибки (400/401/403/404 и т.п.) — сразу наружу.
_RETRYABLE_STATUS = frozenset({408, 425, 429}) | frozenset(range(500, 600))
_RETRY_WAIT_MAX = 60.0   # Retry-After кап, сек
_BACKOFF_MAX = 30.0      # экспоненциальный backoff: min(2**attempt, 30) + jitter


def _retry_wait(attempt: int, resp=None) -> float:
    """Пауза перед ретраем: Retry-After (capped) > exp-backoff + jitter."""
    if resp is not None:
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                return min(float(ra), _RETRY_WAIT_MAX)
            except ValueError:
                pass
    base = min(2.0 ** attempt, _BACKOFF_MAX)
    return base + random.random()


def stream_chat_completion(
    base_url, model, messages, api_key="",
    max_retries=3, timeout=300, stream_timeout=900,
    temperature=None, reasoning_effort=None,
    max_tokens=65536, min_len_ratio=0.0, reference_len=0,
    logger=None, label="",
):
    """Стрим-запрос к OpenAI-совместимому API.
    Возвращает (text | None, error_str).
    Гигиена: [DONE]/finish_reason, детект зацикливания, обрезка max_tokens,
    пустой ответ, min_len_ratio к reference_len (в символах).
    max_tokens — серверный предел (ТОКЕНЫ), всё остальное — символы.
    reasoning_effort: None — поле не шлём (дефолт сервера); строка —
    шлём как есть (в т.ч. "none" — отключение рассуждений)."""
    url = f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "messages": messages,
               "stream": True, "max_tokens": max_tokens}
    if reasoning_effort:
        # единый OpenAI-совместимый способ: значение (в т.ч. "none" —
        # отключение рассуждений) понимают OpenAI API, llama.cpp,
        # OpenRouter/Bothub пробрасывают провайдеру; None — ничего не
        # шлём (дефолт сервера)
        payload["reasoning_effort"] = reasoning_effort
    if temperature is not None:
        payload["temperature"] = temperature

    last_err = "Unknown"
    for attempt in range(max(1, max_retries)):
        try:
            with requests.post(url, headers=headers, json=payload, stream=True,
                               timeout=(timeout, stream_timeout)) as resp:
                if resp.status_code != 200:
                    last_err = f"HTTP {resp.status_code}"
                    # H3 (AUDIT): ретраим ТОЛЬКО 408/425/429/5xx;
                    # 400/401/403/404 — сразу наружу (ключ/запрос битые)
                    if resp.status_code not in _RETRYABLE_STATUS:
                        break
                    time.sleep(_retry_wait(attempt, resp))
                    continue
                full, looped, ok, cut = "", False, False, False
                for line in resp.iter_lines():
                    if not line:
                        continue
                    ls = line.decode("utf-8", errors="ignore").strip()
                    if not ls.startswith("data: "):
                        continue
                    ds = ls[6:]
                    if ds == "[DONE]":
                        ok = True
                        break
                    try:
                        ch = json.loads(ds)
                    except json.JSONDecodeError:
                        continue
                    choices = ch.get("choices", [{}])
                    if not choices:
                        continue
                    fr = choices[0].get("finish_reason")
                    if fr == "stop":
                        ok = True
                    elif fr == "length":
                        cut = True
                        break
                    piece = choices[0].get("delta", {}).get("content")
                    if piece:
                        full += piece
                        if len(full) > 100:
                            w = full[-1500:]
                            if any(r.search(w) for r in _LOOP_RES):
                                looped = True
                                break
                if looped:
                    last_err = "Loop detected"; time.sleep(_retry_wait(attempt)); continue
                if cut:
                    last_err = "Cut by max_tokens"; time.sleep(_retry_wait(attempt)); continue
                if not ok:
                    last_err = "Stream interrupted"; time.sleep(_retry_wait(attempt)); continue
                if not full.strip():
                    last_err = "Empty response"; time.sleep(_retry_wait(attempt)); continue
                if min_len_ratio > 0 and reference_len > 0 \
                        and len(full) < reference_len * min_len_ratio:
                    last_err = "Length ratio check failed"
                    time.sleep(_retry_wait(attempt)); continue
                return full, ""
        except requests.exceptions.ReadTimeout:
            last_err = f"Read timeout ({stream_timeout}s)"
        except requests.exceptions.Timeout:
            last_err = "Conn timeout"
        except requests.exceptions.ChunkedEncodingError:
            last_err = "Chunked error"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
        time.sleep(_retry_wait(attempt))
    if logger:
        logger.error(f"❌ {label} {max_retries} попыток: {last_err}")
    return None, last_err


# ══════════════════════════════════════════════════════════════════════
# ФАЙЛОВАЯ ФС
# ══════════════════════════════════════════════════════════════════════
def atomic_write(filepath, content) -> None:
    """Запись через tmp + os.replace (не оставляет битых файлов)."""
    d = os.path.dirname(filepath) or "."
    try:
        os.makedirs(d, exist_ok=True)
    except OSError as exc:
        raise OSError(f"Не удалось создать каталог {d}: {exc}") from exc
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, filepath)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _mojibake_ratio(text: str, limit: int = 4000) -> float:
    """Доля «подозрительных» символов (Latin-1 supplement / Latin Extended /
    греческий) — признак неверной декодировки (GBK-байты в cp1251 дают
    µ, » и т.п.). Нормальный русский/латиница такого мусора не даёт."""
    sample = text[:limit]
    if not sample:
        return 0.0
    weird = sum(1 for ch in sample if 0x80 <= ord(ch) <= 0x2AF)
    return weird / len(sample)


def read_text_safe(path) -> str:
    """utf-8 → cp1251 → gb18030 fallback (B7, AUDIT).
    Порядок критичен: cp1251 раньше gb18030 (проект русскоязычный), но
    cp1251 «успешно» декодирует почти любые байты — после него эвристика
    мусора: GBK-китайский в cp1251 даёт Latin-1-мусор → переходим к gb18030."""
    text = None
    for enc in ("utf-8", "cp1251"):
        try:
            with open(path, "r", encoding=enc) as f:
                text = f.read()
        except UnicodeDecodeError:
            continue
        if enc == "cp1251" and _mojibake_ratio(text) >= 0.05:
            break  # вероятный GBK — пробуем gb18030
        return text
    try:
        with open(path, "r", encoding="gb18030") as f:
            return f.read()
    except UnicodeDecodeError:
        pass
    if text is not None:
        return text  # оба «успешны», но оба мусорные — историческое поведение
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        raise  # файл не существует/не читается — как и раньше (OSError)


# ══════════════════════════════════════════════════════════════════════
# ПРОГРЕСС ДЛЯ WEB (структурированные события)
# ══════════════════════════════════════════════════════════════════════
PROGRESS_PREFIX = "@@PROGRESS@@"


def web_progress_enabled() -> bool:
    """Web-режим прогресса: флаг WEB_PROGRESS=1 ставит JobManager.start.
    CLI-запуски без флага — no-op (tqdm как раньше)."""
    return os.environ.get("WEB_PROGRESS") == "1"


def _int_safe(v) -> int:
    """Безопасное приведение к int (кривое значение → 0)."""
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return 0


def emit_progress(done: int, total: int | None, label: str = "") -> None:
    """Структурированное событие прогресса для web (stdout, JSON).

    Печатает @@PROGRESS@@ {"type":"progress","label":...,"done":...,"total":...}
    только в web-режиме (WEB_PROGRESS=1); иначе — ничего. total=None/0 —
    неопределённый бар. Вызов рядом с pbar.update(1); потокобезопасность —
    на вызывающей стороне (там же, где pbar_lock)."""
    if not web_progress_enabled():
        return
    print(PROGRESS_PREFIX + json.dumps(
        {"type": "progress", "label": label,
         "done": _int_safe(done),
         "total": None if total is None else _int_safe(total)},
        ensure_ascii=False), flush=True)


# ══════════════════════════════════════════════════════════════════════
# ГЛАВЫ: единый парсер имени папки / карта / поиск файла
# ══════════════════════════════════════════════════════════════════════
def parse_chapter_id(folder_name: str):
    """Единый канонический парсер номера главы. Возвращает int | None.
    Поддерживаемые форматы (все, что порождает epub_to_chapters, + legacy):
      00000_1_第1章 / 0000_10_第10章 / 0_10000_x / 00000_1  → ведущие нули + '_' + номер
      000001_title / 12_title / 001_x / 1_x                → число с ведущими нулями
      000001 / 7                                           → папка-число
    Экзотика (напр. '1234_56') трактуется как 1234 (первое число до '_')."""
    name = (folder_name or "").strip()
    m = re.match(r"^0*_(\d+)(?:[_.]|$)", name)
    if m:
        digits = m.group(1)
        if digits is None:
            return None
        try:
            return int(digits)
        except (ValueError, OverflowError):
            return None
    m = re.match(r"^0*(\d+)[_.]", name)
    if m:
        digits = m.group(1)
        if digits is None:
            return None
        try:
            return int(digits)
        except (ValueError, OverflowError):
            return None
    m = re.match(r"^0*(\d+)$", name)
    if m:
        digits = m.group(1)
        if digits is None:
            return None
        try:
            return int(digits)
        except (ValueError, OverflowError):
            return None
    return None


def build_chapter_map(chapters_dir, logger=None) -> dict:
    """{номер: [пути папок]}. Дубли сохраняются списком — решают вызывающие."""
    chapter_map: dict = defaultdict(list)
    if not os.path.isdir(chapters_dir):
        return {}
    try:
        entries = sorted(os.listdir(chapters_dir))
    except OSError:
        return {}
    for entry in entries:
        full = os.path.join(chapters_dir, entry)
        if not os.path.isdir(full):
            continue
        num = parse_chapter_id(entry)
        if num is not None:
            chapter_map[num].append(full)
    return dict(chapter_map)


_CH_BLACKLIST = {"raw", "draft", "translated", "original", "source", "backup"}

def find_chapter_file(dir_path, chapter_num, want="polished", logger=None,
                      strict=False, strict_types=False):
    """Единый поиск файла главы. Возвращает (path | None, warnings).
    want: 'polished' | 'redacted' | 'translated' | 'chapter' | иное имя типа.
    Приоритеты: точные имена → подстрока типа → единственный безопасный txt.
    strict: при нескольких совпадениях одного паттерна —
    (None, ['[FATAL] ...']) — вызывающий пропускает главу;
    без strict берётся первый файл + предупреждение [КОНФЛИКТ].
    strict_types: не подменять запрошенный тип другим файлом (никакого fallback на chapter.txt)
    """
    if not os.path.isdir(dir_path):
        return None, []
    try:
        names = os.listdir(dir_path)
    except OSError:
        return None, []
    all_txt = sorted(f for f in names
                     if f.lower().endswith(".txt")
                     and os.path.isfile(os.path.join(dir_path, f)))
    if not all_txt:
        return None, []
    n = chapter_num
    if want == "chapter":
        patterns = [rf"^chapter{n}\.txt$", rf"^chapter_{n}\.txt$",
                    r"^chapter\.txt$"]
    else:
        w = re.escape(want)
        patterns = [
            rf"^chapter{n}_{w}\.txt$", rf"^chapter_{n}_{w}\.txt$",
            rf"^{n}_{w}\.txt$", rf"^{w}\.txt$", rf"^{w}_{n}\.txt$",
            rf".*{w}.*\.txt$",
        ]
    for pat in patterns:
        hits = [f for f in all_txt if re.match(pat, f, re.IGNORECASE)]
        if len(hits) == 1:
            return os.path.join(dir_path, hits[0]), []
        if len(hits) > 1:
            hits.sort()
            if strict:
                return None, [
                    f"[FATAL] Глава {n} ({want}): дубли файлов "
                    f"({', '.join(hits)}). Глава пропущена."
                ]
            warn = (f"[КОНФЛИКТ] Глава {n} ({want}): "
                    f"{', '.join(hits)}. Беру: {hits[0]}")
            if logger:
                logger.warning(warn)
            return os.path.join(dir_path, hits[0]), [warn]
    if strict_types:
        return None, [f"[ВНИМАНИЕ] Глава {n}: файл типа '{want}' не найден "
                      f"(fallback на другие типы отключён)."]
    safe = [f for f in all_txt
            if not any(b in f.lower() for b in _CH_BLACKLIST)]
    if len(safe) == 1:
        return os.path.join(dir_path, safe[0]), []
    return None, [f"[ВНИМАНИЕ] Глава {n}: не удалось выбрать файл ({want})."]


def format_ranges(ids) -> str:
    """[1,2,3,5,6,7,8] → '1-3, 5-8'; [4] → '4'; [] → '—'."""
    if not ids:
        return "—"
    ids = sorted(ids)
    ranges, lo, hi = [], ids[0], ids[0]
    for n in ids[1:]:
        if n == hi + 1:
            hi = n
        else:
            ranges.append(f"{lo}-{hi}" if lo != hi else str(lo))
            lo = hi = n
    ranges.append(f"{lo}-{hi}" if lo != hi else str(lo))
    return ", ".join(ranges)


def compile_chapter_text(chapters_dir, want="chapter", start=None, end=None,
                         logger=None) -> tuple[str, dict]:
    """Собрать тексты глав из папок в память (без записи файла).

    Папки — через build_chapter_map; файл главы — find_chapter_file.
    Дубли номера: берётся последняя папка (как clean_and_compile).
    start/end — включительно, None = весь диапазон.
    Возвращает (text, info) — info {written, missing, warnings}.
    """
    chapter_map = build_chapter_map(chapters_dir, logger=logger)
    ids = sorted(chapter_map)
    try:
        lo = int(start) if start is not None and str(start) != "" else None
    except (TypeError, ValueError):
        lo = None
    try:
        hi = int(end) if end is not None and str(end) != "" else None
    except (TypeError, ValueError):
        hi = None
    if lo is not None:
        ids = [i for i in ids if i >= lo]
    if hi is not None:
        ids = [i for i in ids if i <= hi]
    parts: list[str] = []
    missing: list[int] = []
    warnings: list[str] = []
    for cid in ids:
        dirs = chapter_map.get(cid) or []
        dir_path = dirs[-1] if dirs else None
        if not dir_path:
            missing.append(cid)
            continue
        path, warns = find_chapter_file(dir_path, cid, want=want, logger=logger)
        warnings.extend(warns or [])
        if not path:
            missing.append(cid)
            continue
        text = read_text_safe(path)
        if text and not text.endswith("\n"):
            text += "\n"
        parts.append(text)
        if logger:
            logger.info("Глава %s: %s", cid, os.path.basename(path))
    if logger:
        logger.info("Собрано глав в память: %d (пропущено: %d)",
                    len(parts), len(missing))
    return "\n".join(parts), {"written": len(parts),
                               "missing": missing, "warnings": warnings}


def compile_chapter_texts(chapters_dir, out_path, want="chapter",
                          start=None, end=None, logger=None) -> dict:
    """Собрать тексты глав из папок в один файл (для NER и т.п.).

    Склейка — через compile_chapter_text (в память), затем запись файла.
    start/end — включительно, None = весь диапазон.
    Возвращает {written, missing, path, warnings}.
    """
    text, info = compile_chapter_text(chapters_dir, want=want, start=start,
                                      end=end, logger=logger)
    parent = os.path.dirname(out_path)
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            raise OSError(f"Не удалось создать папку {parent}: {exc}") from exc
    atomic_write(out_path, text)
    if logger:
        logger.info("Собрано глав: %d → %s (пропущено: %d)",
                    info["written"], out_path, len(info["missing"]))
    info["path"] = out_path
    return info


def _first_nonempty_line(path: str, limit: int = 65536) -> str | None:
    """Первая непустая строка файла — БЕЗ чтения файла целиком.

    Читает префикс, пока первая непустая строка не будет завершена
    переводом строки (или limit байт); декодирует ТОЛЬКО эту строку
    (utf-8 → cp1251) — разрез на границе чанка не может испортить
    строку (весь буфер строго не декодируется). Строка NFC.
    """
    import unicodedata
    data = b""
    try:
        with open(path, "rb") as f:
            while len(data) < limit:
                chunk = f.read(4096)
                if not chunk:
                    break
                data += chunk
                lines = data.split(b"\n")
                for idx, ln in enumerate(lines):
                    if ln.strip() and idx + 1 < len(lines):
                        break
                else:
                    continue  # строки нет или не завершена — дочитать
                break
    except OSError:
        return None
    line_b = next((ln for ln in data.split(b"\n") if ln.strip()), b"")
    if not line_b:
        return None
    for enc in ("utf-8", "cp1251"):
        try:
            s = line_b.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        s = line_b.decode("utf-8", errors="replace")
    s = unicodedata.normalize("NFC", s.strip())
    return s or None


def read_chapter_titles(chapters_dir, want="polished", logger=None) -> dict:
    """Названия глав: первая непустая строка каждого файла.

    Папки — build_chapter_map (дубли: последняя), файл —
    find_chapter_file (want=type, strict_types для не-chapter).
    Чтение — префиксом (_first_nonempty_line), файл целиком НЕ
    читается: тысячи глав — быстро.
    Возвращает {номер главы: строка-название} — только главы, где
    файл найден и есть непустая строка.
    """
    chapter_map = build_chapter_map(chapters_dir, logger=logger)
    titles: dict[int, str] = {}
    for cid in sorted(chapter_map):
        dirs = chapter_map.get(cid) or []
        dir_path = dirs[-1] if dirs else None
        if not dir_path:
            continue
        path, _warns = find_chapter_file(
            dir_path, cid, want=want,
            strict_types=(want != "chapter"), logger=logger)
        if not path:
            continue
        s = _first_nonempty_line(path)
        if s:
            titles[cid] = s
    if logger:
        logger.info("Названия глав (%s): %d/%d", want, len(titles),
                    len(chapter_map))
    return titles


def write_chapter_titles(chapters_dir, want, titles, logger=None) -> dict:
    """Записать названия глав: замена первой непустой строки в файле.

    titles: {номер главы: новая первая строка}. Файл ищется как в
    read_chapter_titles; строка заменяется (NFC), остальной текст и
    хвостовой перевод строки сохраняются. Возвращает
    {updated: [номера], missing: [номера], warnings: [строки]}.
    """
    import unicodedata
    chapter_map = build_chapter_map(chapters_dir, logger=logger)
    updated: list[int] = []
    missing: list[int] = []
    warnings: list[str] = []
    for cid, new_title in titles.items():
        if not str(new_title).strip():
            missing.append(cid)
            continue
        dirs = chapter_map.get(cid) or []
        dir_path = dirs[-1] if dirs else None
        if not dir_path:
            missing.append(cid)
            continue
        path, warns = find_chapter_file(
            dir_path, cid, want=want,
            strict_types=(want != "chapter"), logger=logger)
        warnings.extend(warns or [])
        if not path:
            missing.append(cid)
            continue
        text = read_text_safe(path)
        lines = text.splitlines()
        idx = None
        for i, line in enumerate(lines):
            if unicodedata.normalize("NFC", line.strip()):
                idx = i
                break
        if idx is None:
            lines.append("")
            idx = len(lines) - 1
        lines[idx] = unicodedata.normalize(
            "NFC", str(new_title).strip())
        nl = "\n" if text.endswith("\n") else ""
        atomic_write(path, "\n".join(lines) + nl)
        updated.append(cid)
        if logger:
            logger.info("Глава %s: заголовок обновлён", cid)
    if logger:
        logger.info("Названия глав: обновлено %d, пропущено %d",
                    len(updated), len(missing))
    return {"updated": updated, "missing": missing,
            "warnings": warnings}

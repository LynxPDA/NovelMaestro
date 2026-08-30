#!/usr/bin/env python3
"""
Wiki Generator для веб-новелл (любой исходный язык).
RAG-подход: SQLite FTS5 индекс → извлечение контекста → LLM-генерация.

Поиск в FTS5 ведётся СТРОГО по полю translation (русский перевод).
Каждый термин получает индивидуальную статью (detailed-подход).
"""
import os
import argparse
import json
import logging
import sqlite3
import threading
import time
import re
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
    build_chapter_map,
    compile_chapter_text,
    determine_model,
    emit_progress,
    find_env_file,
    get_server_config,
    log_argv as _cc_log_argv,
    parse_dotenv,
    print_env_help,
    setup_logging as _cc_setup_logging,
    stream_chat_completion,
    web_progress_enabled,
)

# ══════════════════════════════════════════════════════════════════════
# ВСТРОЕННЫЕ УТИЛИТЫ (замена core.utils)
# ══════════════════════════════════════════════════════════════════════

def _setup_logging(log_path: str) -> logging.Logger:
    """Делегирует в core.common.setup_logging (файл + консоль)."""
    logger, _ = _cc_setup_logging(log_path)
    return logger



# ══════════════════════════════════════════════════════════════════════
# ВСТРОЕННЫЕ ПРОМПТЫ
# ══════════════════════════════════════════════════════════════════════

SYSTEM_WIKI_ARTICLE = """\
Ты — автор-составитель энциклопедии по веб-новелле.
На основе предоставленных данных напиши энциклопедическую статью.

СТРУКТУРА СТАТЬИ:
- Заголовок: ## {translation}
- Краткое введение (2–3 предложения).
- ### Описание (подробное) — внешность, характер, свойства, функционал и так далее.
- ### {relations_label} — в контексте данного типа.
- ### Примечания — если есть важные детали.

СТРОГИЕ ПРАВИЛА:
- ЗАПРЕЩЕНО указывать пиньинь, иероглифы, пометку «кит.» в тексте.
- ЗАПРЕЩЕНО упоминать количество упоминаний, частоту, count.
- ЗАПРЕЩЕНО просить дополнительные данные, оставлять шаблоны,
  писать «пришлите описание» или «укажите роль».
- ЗАПРЕЩЕНО выдумывать факты, которых нет во фрагментах текста.
  Если информации нет — напиши: «В тексте нет детального описания.»
- Пиши ТОЛЬКО на основе предоставленных фрагментов и данных.
- Если данных мало — напиши краткую статью (3–5 предложений).
- Стиль: информативный, лаконичный, без воды. Язык: русский.
- Формат: чистый Markdown. Без JSON, без блоков кода.
- Объём: 200–800 слов.
"""

# ══════════════════════════════════════════════════════════════════════
# МАРКЕРЫ ОПИСАНИЯ (с учётом склонений и родов)
# ══════════════════════════════════════════════════════════════════════

DESCRIPTION_MARKERS = {
    "Person": [
        "выглядел*", "внешност*", "одет*", "одежд*", "волос*", "глаз*",
        "лиц*", "фигур*", "рост*", "шрам*", "кожу*",
        "происходил*", "возраст*", "гений", "статус*", "талант*", "появил*"
    ],
    "Location": [
        "располож*", "находил*", "огромн*", "древн*", "территори*",
        "здани*", "город*", "дворец*", "храм*", "пещер*", "долин*",
        "озер*", "руин*", "гор*", "остров*", "лес*", "выглядел*",
        "окружен*", "аур*", "энерги*", "таинствен*", "опасн*"
    ],
    "Organisation": [
        "глав*", "лидер*", "патриарх*", "предок*", "старейшин*", "ученик*",
        "основан*", "входит*", "состоит*", "управлял*", "контролир*",
        "организаци*", "фракци*", "клан*", "сект*", "корпораци*", "семь*", "базирует*"
    ],
    "Technique": [
        "активировал*", "выполнил*", "задействовал*", "применил*", "использовал*",
        "ци", "энерги*", "формаци*", "техник*", "заклинани*", "метод*",
        "навык*", "искусс*", "мантр*",
        "практиковал*", "освоил*", "овладел*", "совершенствовал*", "постиг*"
    ],
    "Artifact": [
        "артефакт*", "оружи*", "меч*", "талисман*", "пилюл*", "сокровищ*",
        "кольц*", "зеркал*", "копь*", "посох*", "бронь*", "доспех*", "печь*",
        "извлек*", "достал*", "активировал*",
        "владел*", "принадлежал*", "создан*", "выкован*", "материал*"
    ],
    "Creature": [
        "выглядел*", "внешност*", "одет*", "одежд*", "волос*", "глаз*",
        "лиц*", "фигур*", "рост*", "шрам*", "кожу*",
        "звер*", "монстр*", "существ*", "дух*", "демон*", "дракон*",
        "феникс*", "тигр*", "черепах*", "питомец*",
        "появил*", "напал*", "зарычал*", "приручил*", "обитал*", "водилс*",
        "чешу*", "крыль*", "когт*", "клык*", "кров*"
    ],
    "Stage": [
        "стади*", "уровен*", "ранг*", "прорыв*", "культиваци*", "этап*",
        "сфер*", "царств*", "мир*",
        "достиг*", "прорвал*", "перешел*", "базис*", "формирован*",
        "зарождающ*", "бессмерт*"
    ],
}

DEFAULT_MARKERS = [
    "впервы*", "появил*", "называл*", "известн*", "считал*", "важн*"
]

# ══════════════════════════════════════════════════════════════════════
# РУССКИЕ НАЗВАНИЯ ТИПОВ И ПОРЯДОК
# ══════════════════════════════════════════════════════════════════════

TYPE_NAMES_RU = {
    "Person":       "Персонажи",
    "Location":     "Локации и миры",
    "Organisation": "Организации и секты",
    "Technique":    "Техники и методы",
    "Artifact":     "Артефакты и сокровища",
    "Creature":     "Существа и духи",
    "Stage":        "Ступени культивации",
    "Material":     "Материалы и ресурсы",
    "Event":        "Ключевые события",
    "Era":          "Эпохи",
    "Race":         "Расы и народы",
    "Title":        "Титулы и звания",
    "Other":        "Прочие термины",
}

TYPE_ORDER = [
    "Person", "Organisation", "Location", "Technique",
    "Artifact", "Creature", "Stage", "Material",
    "Event", "Era", "Race", "Title", "Other",
]

SECTION_RELATIONS_LABEL = {
    "Person":       "Взаимосвязи",
    "Organisation": "Структура и иерархия",
    "Location":     "Роль в сюжете",
    "Technique":    "Применение и освоение",
    "Artifact":     "Владельцы и применение",
    "Creature":     "Взаимодействие с персонажами",
    "Stage":        "Переход и условия",
    "Material":     "Применение и добыча",
    "Event":        "Участники и последствия",
    "Era":          "Ключевые события эпохи",
    "Race":         "Отношения с другими народами",
    "Title":        "Иерархия и полномочия",
    "Other":        "Примечания",
}

SKIP_RELATIONS_TYPES = {"Stage", "Material", "Other", "Title"}

# ══════════════════════════════════════════════════════════════════════
# ЛОГИРОВАНИЕ
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
# FTS5 УТИЛИТЫ
# ══════════════════════════════════════════════════════════════════════

def _fts_escape(s: str) -> str:
    return s.replace('"', '""')


def _fts_search_all(db: sqlite3.Connection, query: str) -> list[str]:
    """Все совпавшие чанки в порядке следования по тексту (без BM25)."""
    try:
        rows = db.execute(
            "SELECT content FROM chunks WHERE chunks MATCH ? "
            "ORDER BY CAST(chunk_id AS INTEGER)",
            (query,),
        ).fetchall()
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        return []


def _fts_search_first(db: sqlite3.Connection, query: str) -> str | None:
    """Первый чанк по порядку текста."""
    try:
        row = db.execute(
            "SELECT content FROM chunks WHERE chunks MATCH ? "
            "ORDER BY CAST(chunk_id AS INTEGER) LIMIT 1",
            (query,),
        ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def _fts_search_ids_all(db: sqlite3.Connection, query: str) -> set[str]:
    """Все chunk_id, где встречается запрос. Без BM25, без LIMIT."""
    try:
        rows = db.execute(
            "SELECT chunk_id FROM chunks WHERE chunks MATCH ?",
            (query,),
        ).fetchall()
        return {r[0] for r in rows}
    except sqlite3.OperationalError:
        return set()


def _even_sample(items: list, n: int) -> list:
    """
    Равномерная выборка n элементов из списка.
    Первый и последний элементы включаются всегда.
    """
    if len(items) <= n:
        return list(items)
    if n <= 0:
        return []
    if n == 1:
        return [items[0]]
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]


def build_fts_index(text: str, chunk_size: int, logger) -> sqlite3.Connection:
    _log(logger, logging.INFO, "📇 Построение FTS5 индекса...")
    t0 = time.time()

    paragraphs = re.split(r'\n\s*\n', text)
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 > chunk_size and current:
            chunks.append(current)
            current = para
        else:
            current = current + "\n" + para if current else para
    if current:
        chunks.append(current)

    if len(chunks) <= 1 and len(text) > chunk_size:
        chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
        _log(logger, logging.WARNING,
             f"⚠️ Абзацы не найдены — нарезка по {chunk_size} символов")

    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE VIRTUAL TABLE chunks USING fts5(content, chunk_id UNINDEXED)"
    )
    for i, chunk in enumerate(chunks):
        db.execute("INSERT INTO chunks VALUES (?, ?)", (chunk, str(i)))
    db.commit()

    elapsed = time.time() - t0
    _log(logger, logging.INFO,
         f"✅ FTS5 индекс: {len(chunks)} чанков за {elapsed:.1f}s")
    return db


# ══════════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ
# ══════════════════════════════════════════════════════════════════════

def _get_base_type(type_str) -> str:
    if not type_str:
        return "Other"
    s = str(type_str).strip()
    result = re.sub(r'\s*\(.*?\)\s*$', '', s).strip()
    _EXTRA_MAP = {
        "Base Term":         "Other",
        "Sect":              "Organisation",
        "Cultivation Stage": "Stage",
        "Location (Body)":   "Location",
    }
    return _EXTRA_MAP.get(result, result) if result else "Other"


def _get_translation(item: dict) -> str:
    """Единственный поисковый ключ — русский перевод."""
    return (item.get("translation") or "").strip()

def _capitalize_first(s: str) -> str:
    """Первая буква заглавная, остальное без изменений."""
    if not s:
        return s
    return s[0].upper() + s[1:]

def _get_type_name_ru(base_type: str) -> str:
    return TYPE_NAMES_RU.get(base_type, base_type)


def _shift_headings(md: str) -> str:
    """Сдвигает все Markdown-заголовки на один уровень глубже (## → ###)."""
    return re.sub(r'^(#{1,5})\s', r'#\1 ', md, flags=re.MULTILINE)


def _slugify(s: str) -> str:
    """Якорь для оглавления: NFC + lowercase, не-буквы → '-', схлопывание.

    Совпадает для заголовка статьи (## {translation}) и строки оглавления
    (один и тот же текст) — ссылка работает без внешних slugger'ов.
    """
    import unicodedata
    s = unicodedata.normalize("NFC", s or "").lower()
    out = [ch if ch.isalnum() else "-" for ch in s]
    slug = re.sub(r"-+", "-", "".join(out)).strip("-")
    return slug or "-"


def _inline_html(s: str) -> str:
    """Экранирование + минимальный inline-markdown (**жирный**, *курсив*)."""
    from html import escape as _esc
    s = _esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*(.+?)\*", r"<em>\1</em>", s)
    return s


def md_to_html(md: str) -> str:
    """Конвертация статьи (markdown от LLM) в HTML для Rulate.

    ## / ### → <p><strong><span style="font-size:20px/16px">…</span></strong></p>
    (заголовки — указанием шрифта, НЕ тегами <h1..h6>); списки → <ul>;
    --- → <hr />; абзацы → <p>. Всё экранируется (_inline_html).
    """
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        # разделитель
        if re.match(r"^(?:-{3,}|\*{3,}|_{3,})$", stripped):
            out.append("<hr />")
            i += 1
            continue
        # заголовки: НЕ тег заголовка, а указание шрифта (требование Rulate)
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            size = "20px" if level <= 2 else "16px"
            text = _inline_html(m.group(2).strip())
            out.append(
                f'<p><strong><span style="font-size:{size}">{text}</span>'
                f"</strong></p>")
            i += 1
            continue
        # список: собираем подряд идущие пункты
        if re.match(r"^[-*+]\s+", stripped):
            items: list[str] = []
            while i < len(lines):
                lm = re.match(r"^[-*+]\s+(.*)$", lines[i].strip())
                if not lm:
                    break
                items.append(
                    f"    <li>{_inline_html(lm.group(1).strip())}</li>")
                i += 1
            out.append("<ul>")
            out.extend(items)
            out.append("</ul>")
            continue
        # абзац: до пустой строки
        para = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip():
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_inline_html(' '.join(para))}</p>")
    return "\n\n".join(out)


# ══════════════════════════════════════════════════════════════════════
# ИЗВЛЕЧЕНИЕ КОНТЕКСТА — поиск ТОЛЬКО по translation
# ══════════════════════════════════════════════════════════════════════

def extract_context(
    translation: str,
    base_type: str,
    count: int,
    db: sqlite3.Connection,
    top_k: int,
    near_distance: int,
    logger,
) -> list[str]:
    """
    Извлечь релевантные фрагменты по русскому переводу.
    Все чанки извлекаются в порядке текста, выборка равномерная.
    """
    if not translation:
        return []

    markers = DESCRIPTION_MARKERS.get(base_type, DEFAULT_MARKERS)
    candidates: list[str] = []
    seen: set[str] = set()
    ev = _fts_escape(translation)

    def _add(hits: list[str]) -> None:
        for h in hits:
            if h not in seen:
                candidates.append(h)
                seen.add(h)

    # 1. Первое появление (по порядку текста)
    first = _fts_search_first(db, f'"{ev}"')
    if first:
        _add([first])

    # 2. Появления с маркерами описания (все совпадения, равномерная выборка)
    for marker in markers:
        em = _fts_escape(marker)
        if em.endswith("*"):
            q = f'NEAR("{ev}" "{em[:-1]}"*, {near_distance})'
        else:
            q = f'NEAR("{ev}" "{em}", {near_distance})'

        hits = _fts_search_all(db, q)
        _add(_even_sample(hits, 3))
        if len(candidates) >= top_k:
            break

    # 3. Дополняем равномерной выборкой из всех упоминаний
    if len(candidates) < top_k:
        all_hits = _fts_search_all(db, f'"{ev}"')
        remaining = [h for h in all_hits if h not in seen]
        _add(_even_sample(remaining, top_k - len(candidates)))

    return candidates[:top_k]


# ══════════════════════════════════════════════════════════════════════
# CO-OCCURRENCE — мультитипный, настраиваемый
# ══════════════════════════════════════════════════════════════════════

def _parse_co_occurrence_pairs(spec: str) -> list[tuple[str, str]]:
    """Парсит строку вида 'Person:Person,Person:Organisation' в список пар."""
    pairs = []
    for part in spec.split(","):
        part = part.strip()
        if ":" in part:
            a, b = part.split(":", 1)
            a, b = a.strip(), b.strip()
            if a and b:
                pairs.append((a, b))
    return pairs


def compute_co_occurrence(
    entity_groups: dict[str, list[dict]],
    pairs: list[tuple[str, str]],
    top_n: int,
    db: sqlite3.Connection,
    logger,
) -> dict[str, list[tuple[str, str, int]]]:
    """
    Вычисляет co-occurrence для заданных пар типов.

    entity_groups: {"Person": [...], "Organisation": [...], "Artifact": [...]}
    pairs: [("Person","Person"), ("Person","Organisation"), ("Person","Artifact")]
    top_n: сколько связей оставлять для каждого термина.

    Квоты: минимум 1 слот на каждый тип связи (если связи этого типа
    существуют). Пустые квоты заполняются следующими по overlap.

    Возвращает:
        {translation: [(связанный_термин, тип_связанного, count), ...]}
    """
    if not pairs:
        return {}

    needed_types: set[str] = set()
    for a, b in pairs:
        needed_types.add(a)
        needed_types.add(b)

    _log(logger, logging.INFO,
         f"🔗 Co-occurrence: пары={pairs}, типы={sorted(needed_types)}")

    chunk_map: dict[str, dict[str, set[str]]] = {}

    for btype in needed_types:
        items = entity_groups.get(btype, [])
        if not items:
            continue
        chunk_map[btype] = {}
        for item in items:
            trans = _get_translation(item)
            if not trans:
                continue
            ids = _fts_search_ids_all(db, f'"{_fts_escape(trans)}"')
            if ids:
                chunk_map[btype][trans] = ids

    total_entities = sum(len(v) for v in chunk_map.values())
    _log(logger, logging.INFO,
         f"   Собрано chunk_id для {total_entities} терминов "
         f"({', '.join(f'{k}:{len(v)}' for k, v in chunk_map.items())})")

    result: dict[str, list[tuple[str, str, int]]] = {}

    for type_a, type_b in pairs:
        map_a = chunk_map.get(type_a, {})
        map_b = chunk_map.get(type_b, {})
        if not map_a or not map_b:
            continue

        same_type = (type_a == type_b)

        for trans_a, ids_a in map_a.items():
            if not ids_a:
                continue
            if trans_a not in result:
                result[trans_a] = []

            for trans_b, ids_b in map_b.items():
                if same_type and trans_a == trans_b:
                    continue
                overlap = len(ids_a & ids_b)
                if overlap > 0:
                    result[trans_a].append((trans_b, type_b, overlap))

    for key in result:
        by_type: dict[str, list[tuple[str, str, int]]] = {}
        for c in result[key]:
            by_type.setdefault(c[1], []).append(c)

        picked: list[tuple[str, str, int]] = []
        pool: list[tuple[str, str, int]] = []

        for t, items in by_type.items():
            items.sort(key=lambda x: -x[2])
            picked.append(items[0])
            pool.extend(items[1:])

        pool.sort(key=lambda x: -x[2])
        picked.extend(pool[:max(0, top_n - len(picked))])
        picked.sort(key=lambda x: -x[2])
        result[key] = picked[:top_n]

    result = {k: v for k, v in result.items() if v}

    _log(logger, logging.INFO,
         f"✅ Co-occurrence: связи найдены для {len(result)} терминов")
    return result


# ══════════════════════════════════════════════════════════════════════
# LLM ЗАПРОС
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
    thinking: str | None,
    logger,
) -> str | None:
    """Делегирует в единый стрим core.common.stream_chat_completion
    ([DONE]/finish_reason, loop-детект, cut, empty — одна гигиена на проект).
    max_tokens=65536 — исторический предел wiki (ТОКЕНЫ, серверный
    предохранитель). enable_reasoning=False: wiki шлёт reasoning_effort="none"
    (генерация вики не требует рассуждений); --thinking добавляет effort."""
    text, _err = stream_chat_completion(
        base_url, model,
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_content}],
        api_key=api_key,
        max_retries=max_retries,
        timeout=timeout,
        stream_timeout=timeout,
        temperature=temperature,
        reasoning_effort=thinking,
        enable_reasoning=False,
        max_tokens=65536,
        logger=logger,
        label="[wiki]",
    )
    return text


# ══════════════════════════════════════════════════════════════════════
# ЗАГРУЗКА ПРОМПТОВ
# ══════════════════════════════════════════════════════════════════════

def load_wiki_prompts(filepath: str, logger) -> dict[str, str]:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        _log(logger, logging.ERROR, f"⚠️ Не удалось прочитать {filepath}: {e}")
        return {}

    result: dict[str, str] = {}
    m = re.search(
        r"<prompt_wiki_article>(.*?)</prompt_wiki_article>",
        content, re.DOTALL,
    )
    if m:
        result["article"] = m.group(1).strip()

    if not result:
        result["article"] = content.strip()

    return result


# ══════════════════════════════════════════════════════════════════════
# ФОРМАТИРОВАНИЕ ДАННЫХ ДЛЯ ПРОМПТА
# ══════════════════════════════════════════════════════════════════════

def _format_term_for_prompt(item: dict) -> str:
    lines = [
        f"Перевод: {_capitalize_first(_get_translation(item))}",
        f"Тип: {item.get('type') or 'Other'}",
    ]
    if item.get("notes"):
        lines.append(f"Заметки: {item['notes']}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# ГЕНЕРАЦИЯ: СТАТЬЯ ПО ОДНОМУ ТЕРМИНУ
# ══════════════════════════════════════════════════════════════════════

def generate_article(
    item: dict,
    fragments: list[str],
    co_occur: list[tuple[str, str, int]],
    base_type: str,
    system_prompt: str,
    llm_args: dict,
    logger,
) -> str | None:
    translation = _get_translation(item)

    user_parts = ["=== ДАННЫЕ ТЕРМИНА ==="]
    user_parts.append(_format_term_for_prompt(item))

    if co_occur and base_type not in SKIP_RELATIONS_TYPES:
        rel_label = SECTION_RELATIONS_LABEL.get(base_type, "Взаимосвязи")
        user_parts.append(f"\n=== {rel_label.upper()} (совместные появления) ===")
        for name, related_type, cnt in co_occur:
            type_ru = TYPE_NAMES_RU.get(related_type, related_type)
            user_parts.append(
                f"  - {name} ({type_ru}): {cnt} совместных фрагментов"
            )

    if fragments:
        user_parts.append("\n=== ФРАГМЕНТЫ ТЕКСТА ===")
        for i, frag in enumerate(fragments, 1):
            user_parts.append(f"\n[Фрагмент {i}]\n{frag[:1500]}")

    user_content = "\n".join(user_parts)

    rel_label = SECTION_RELATIONS_LABEL.get(base_type, "Примечания")
    sys_prompt = system_prompt.replace("{translation}", _capitalize_first(translation))
    sys_prompt = sys_prompt.replace("{relations_label}", rel_label)

    if base_type in SKIP_RELATIONS_TYPES:
        sys_prompt = sys_prompt.replace(
            f"- ### {rel_label} — в контексте данного типа.\n", ""
        )

    return llm_request(sys_prompt, user_content, logger=logger, **llm_args)


# ══════════════════════════════════════════════════════════════════════
# КЭШ
# ══════════════════════════════════════════════════════════════════════

def load_wiki_cache(filepath: str, logger) -> dict:
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        _log(logger, logging.INFO, f"📂 Загружен wiki-кэш: {len(data)} записей")
        return data
    except Exception as e:
        _log(logger, logging.ERROR, f"⚠️ Ошибка чтения кэша: {e}")
        return {}


def save_wiki_cache(filepath: str, cache: dict) -> None:
    tmp = filepath + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, filepath)
    except OSError as exc:
        # кэш некритичен: молча пропускаем, wiki соберётся заново
        print(f"⚠️ Ошибка записи кэша: {exc}")


# ══════════════════════════════════════════════════════════════════════
# СБОРКА WIKI (Markdown)
# ══════════════════════════════════════════════════════════════════════

def assemble_wiki(
    sections_by_type: list[tuple[str, list[tuple[str, str]]]],
    output_path: str,
    rulate: bool,
    logger=None,
    toc: bool = True,
    toc_links: bool = True,
    rulate_html: bool = False,
) -> None:
    """Собирает итоговый файл вики.

    sections_by_type: [(type_name_ru, [(title, content), ...]), ...]
    rulate: True — режим для импорта на Rulate (без содержания,
            заголовки сдвинуты на уровень глубже, спецзаголовок).
    rulate_html: True — вместо Markdown генерируется HTML: заголовки —
            <span style="font-size:20px/16px"> (не теги заголовков),
            списки <ul>, разделители <hr /> (требование Rulate).
    toc: оглавление (только в обычном режиме; Rulate — всегда без него).
    toc_links: якоря-ссылки в оглавлении (обычный режим).
    """
    if not sections_by_type:
        _log(logger, logging.WARNING, "⚠️ Нет разделов для сборки.")
        return

    lines: list[str] = []
    use_html = rulate_html

    # ── Заголовок ──
    if use_html:
        lines.append(
            '<p><strong><span style="font-size:20px">'
            'Wiki — Энциклопедия новеллы</span></strong></p>\n')
    elif rulate:
        lines.append("# [Wiki — Энциклопедия новеллы :|:  :|:  :|: ]\n")
    else:
        lines.append("# Wiki — Энциклопедия новеллы\n")

    # ── Оглавление (только в обычном режиме) ──
    if not rulate and not use_html and toc:
        lines.append("## Содержание\n")
        for type_name_ru, terms in sections_by_type:
            if not terms:
                continue
            lines.append(f"- **{type_name_ru}**")
            for title, _ in terms:
                t = _capitalize_first(title)
                if toc_links:
                    lines.append(f"  - [{t}](#{_slugify(t)})")
                else:
                    lines.append(f"  - {t}")
        lines.append("")
        lines.append("---\n")

    # ── Тело ──
    for _type_name_ru, terms in sections_by_type:
        if not terms:
            continue
        for title, content in terms:
            if use_html:
                lines.append(md_to_html(content))
            else:
                if rulate:
                    content = _shift_headings(content)
                # якорь для ссылки оглавления (обычный режим с ссылками)
                if not rulate and toc and toc_links:
                    lines.append(f'<a id="{_slugify(_capitalize_first(title))}"></a>')
                lines.append(content)
            lines.append("\n<hr />\n" if use_html else "\n---\n")

    text = "\n".join(lines)
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        _log(logger, logging.ERROR,
             f"❌ Не удалось записать {output_path}: {exc}")
        return

    _log(logger, logging.INFO,
         f"💾 Wiki сохранена: {output_path} ({len(text)} символов)")


def assemble_wiki_chapter(
    sections_by_type: list[tuple[str, list[tuple[str, str]]]],
    output_path: str,
    logger=None,
) -> None:
    """Собрать вики как дополнительную последнюю главу (--as-chapter).

    output_path — файл выбранного типа (translated/redacted/polished,
    задаёт main): первая строка — название главы «Wiki Новеллы» ПРОСТЫМ
    текстом (без rulate-спецзаголовка); статьи — в формате как у rulate
    (заголовки сдвинуты глубже: ## → ###), разделители ---. chapter.txt
    не пишется.
    """
    if not sections_by_type:
        _log(logger, logging.WARNING, "⚠️ Нет разделов для сборки.")
        return

    lines = ["Wiki Новеллы", ""]
    for _type_name_ru, terms in sections_by_type:
        if not terms:
            continue
        for _title, content in terms:
            lines.append(_shift_headings(content))
            lines.append("\n---\n")

    text = "\n".join(lines)
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        _log(logger, logging.ERROR,
             f"❌ Не удалось записать {output_path}: {exc}")
        return

    _log(logger, logging.INFO,
         f"💾 Вики-глава сохранена: {output_path} ({len(text)} символов)")


# ══════════════════════════════════════════════════════════════════════
# ОСНОВНОЙ PIPELINE
# ══════════════════════════════════════════════════════════════════════

def run_wiki_generation(
    ner_data: list[dict],
    db: sqlite3.Connection,
    exclude_types: set[str],
    top_n: int,
    min_count: int,
    context_chunks: int,
    near_distance: int,
    system_prompt: str,
    llm_args: dict,
    max_workers: int,
    save_interval: int,
    cache_file: str,
    output_path: str,
    co_pairs: list[tuple[str, str]],
    co_top: int,
    rulate: bool,
    logger=None,
    toc: bool = True,
    toc_links: bool = True,
    rulate_html: bool = False,
    as_chapter: bool = False,
) -> None:

    # ── Статистика отбора ──
    total_ner = len(ner_data)
    with_trans = sum(1 for i in ner_data if _get_translation(i))
    above_min = sum(
        1 for i in ner_data
        if i.get("count", 0) >= min_count and _get_translation(i)
    )
    _log(logger, logging.INFO,
         f"📊 NER: всего={total_ner}, с переводом={with_trans}, "
         f"порог(≥{min_count})={above_min}")

    # ── Фильтрация: только записи с translation ──
    filtered = [
        item for item in ner_data
        if item.get("count", 0) >= min_count
        and _get_translation(item)
    ]
    filtered.sort(key=lambda x: x.get("count", 0), reverse=True)
    filtered = filtered[:top_n]
    _log(logger, logging.INFO,
         f"   После top-{top_n}: {len(filtered)}")

    # ── Исключение типов ──
    if exclude_types:
        filtered = [
            item for item in filtered
            if _get_base_type(item.get("type")) not in exclude_types
        ]
        _log(logger, logging.INFO,
             f"   После исключения типов: {len(filtered)}")

    if not filtered:
        _log(logger, logging.WARNING,
             "⚠️ Нет терминов после фильтрации. "
             "Проверьте наличие поля 'translation' в ner.json.")
        return

    _log(logger, logging.INFO,
         f"📋 Терминов для Wiki: {len(filtered)} "
         f"(top={top_n}, min_count={min_count}, "
         f"excluded={exclude_types or 'нет'})")

    # ── Группировка по type ──
    groups: dict[str, list[dict]] = {}
    for item in filtered:
        base_type = _get_base_type(item.get("type"))
        groups.setdefault(base_type, []).append(item)

    _log(logger, logging.INFO,
         f"📊 Группы: {', '.join(f'{k}({len(v)})' for k, v in groups.items())}")

    # ── Кэш ──
    cache = load_wiki_cache(cache_file, logger)
    cache_lock = threading.Lock()

    # ── Co-occurrence (мультитипный) ──
    co_occurrence: dict[str, list[tuple[str, str, int]]] = {}
    if co_pairs:
        co_occurrence = compute_co_occurrence(
            groups, co_pairs, co_top, db, logger,
        )

    # ── Извлечение контекста по translation ──
    _log(logger, logging.INFO,
         "🔍 Извлечение контекста (поиск по translation)...")
    fragments_map: dict[str, list[str]] = {}
    # стартовое событие прогресса — бар виден сразу
    emit_progress(0, len(filtered), "Извлечение контекста")
    if web_progress_enabled():
        _log(logger, logging.INFO, f"📊 Прогресс: 0/{len(filtered)}")
    for i, item in enumerate(tqdm(filtered, desc="Context extraction",
                                  disable=web_progress_enabled())):
        trans = _get_translation(item)
        if not trans:
            emit_progress(i + 1, len(filtered), "Извлечение контекста")
            continue
        base_type = _get_base_type(item.get("type"))
        frags = extract_context(
            trans, base_type,
            item.get("count", 0), db, context_chunks,
            near_distance, logger,
        )
        fragments_map[trans] = frags
        emit_progress(i + 1, len(filtered), "Извлечение контекста")

    found = sum(1 for v in fragments_map.values() if v)
    _log(logger, logging.INFO,
         f"✅ Контекст: {found}/{len(fragments_map)} терминов имеют фрагменты")

    if found == 0:
        _log(logger, logging.WARNING,
             "⚠️ НИ ОДНОГО фрагмента не найдено! "
             "Проверьте: novel.txt — русский текст, "
             "ner.json содержит корректные translation.")

    # ── Задачи: все термины → индивидуальные статьи ──
    total_tasks = len(filtered)
    _log(logger, logging.INFO,
         f"🚀 Генерация: {total_tasks} статей | Потоков: {max_workers}")

    if total_tasks == 0:
        return

    # ── Функция генерации одной статьи ──
    def _gen_one(item: dict) -> tuple[str, str, str | None, int]:
        trans = _get_translation(item)
        cache_key = f"article_{trans}"
        base_type = _get_base_type(item.get("type"))
        count = item.get("count", 0)

        with cache_lock:
            if cache_key in cache:
                return base_type, trans, cache[cache_key], count

        frags = fragments_map.get(trans, [])
        co = co_occurrence.get(trans, [])
        result = generate_article(
            item, frags, co, base_type,
            system_prompt, llm_args, logger,
        )
        if result:
            with cache_lock:
                cache[cache_key] = result
        return base_type, trans, result, count

    # ── Потоки ──
    pbar = tqdm(total=total_tasks, desc="Wiki generation",
                disable=web_progress_enabled())
    completed = 0
    # стартовое событие прогресса — бар виден сразу
    emit_progress(0, total_tasks, "Генерация статей")
    if web_progress_enabled():
        _log(logger, logging.INFO, f"📊 Прогресс: 0/{total_tasks}")
    results: dict[str, list[tuple[str, str, int]]] = {}
    results_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures: dict = {}
        for item in filtered:
            fut = pool.submit(_gen_one, item)
            futures[fut] = _get_translation(item)

        for fut in as_completed(futures):
            ref = futures[fut]
            try:
                base_type, title, content, count = fut.result()
                if content:
                    with results_lock:
                        results.setdefault(base_type, []).append(
                            (title, content, count))
                    tqdm.write(f"✅ {title}")
                else:
                    _log(logger, logging.WARNING,
                         f"⚠️ Пусто: {ref}")
            except Exception as e:
                _log(logger, logging.ERROR, f"💥 {ref}: {e}")

            completed += 1
            pbar.update(1)
            emit_progress(completed, total_tasks, "Генерация статей")
            if completed % save_interval == 0:
                with cache_lock:
                    save_wiki_cache(cache_file, cache)

    pbar.close()
    with cache_lock:
        save_wiki_cache(cache_file, cache)

    # ── Сборка ──
    if as_chapter:
        _log(logger, logging.INFO, "📝 Сборка вики-главы...")
    else:
        _log(logger, logging.INFO, "📝 Сборка wiki.md...")
    sections_by_type: list[tuple[str, list[tuple[str, str]]]] = []

    for base_type in TYPE_ORDER:
        if base_type not in results:
            continue
        type_name_ru = _get_type_name_ru(base_type)
        terms = results[base_type]
        if terms:
            # Сортировка по count убывающе (самые частые первыми)
            terms.sort(key=lambda x: -x[2])
            sections_by_type.append(
                (type_name_ru, [(t, c) for t, c, _ in terms])
            )

    # Типы, не вошедшие в TYPE_ORDER
    known = set(TYPE_ORDER)
    for base_type in results:
        if base_type in known:
            continue
        type_name_ru = _get_type_name_ru(base_type)
        terms = results[base_type]
        if terms:
            terms.sort(key=lambda x: -x[2])
            sections_by_type.append(
                (type_name_ru, [(t, c) for t, c, _ in terms])
            )

    if sections_by_type:
        if as_chapter:
            assemble_wiki_chapter(sections_by_type, output_path,
                                  logger=logger)
        else:
            assemble_wiki(sections_by_type, output_path, rulate,
                          toc=toc, toc_links=toc_links,
                          rulate_html=rulate_html, logger=logger)
    else:
        _log(logger, logging.WARNING, "⚠️ Ни одного раздела не сгенерировано.")


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Wiki Generator для веб-новелл (RAG + FTS5).\n"
            "\n"
            "Пайплайн:\n"
            "  1. Нарезка текста новеллы на чанки и построение FTS5 индекса.\n"
            "  2. Извлечение релевантных фрагментов для каждого термина (NER).\n"
            "  3. Вычисление co-occurrence связей между терминами.\n"
            "  4. Генерация энциклопедических статей через LLM.\n"
            "  5. Сборка итогового Markdown с оглавлением.\n"
        ),
        epilog=(
            "Пример запуска:\n"
            "  python wiki.py novel.txt --ner_file ner.json \\\n"
            "      --host http://127.0.0.1:9989 --model qwen3 \\\n"
            "      --threads 4 --top 60 --exclude-types Other,Material\n"
            "  python wiki.py --compile-chapters --type polished \\\n"
            "      --start 1 --end 100 --ner_file ner.json --output wiki.md\n"
            "  python wiki.py novel.txt --rulate-html --output wiki.txt\n"
            "  python wiki.py --as-chapter --compile-chapters --type polished\n"
            "\n"
            "Единицы:\n"
            "  --chunk-size и размеры текста — СИМВОЛЫ;\n"
            "  --context-chunks / --top / --min-count — штуки/пороги count;\n"
            "  --near-distance — дистанция FTS5 NEAR, ТОКЕНЫ (природа FTS5);\n"
            "  max_tokens (65536) — серверный предохранитель, ТОКЕНЫ.\n"
            ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── Входные файлы ──
    g_input = parser.add_argument_group("Входные файлы")
    g_input.add_argument(
        "file", nargs="?", default=None,
        help="Путь к .txt файлу новеллы (русский перевод); не нужен,"
             " если задан --compile-chapters.",
    )
    g_input.add_argument(
        "--compile-chapters", action="store_true",
        help="Собрать главы из chapters/ в память (как ner --compile_chapters)"
             " и строить вики по ним; тип файлов — --type, диапазон —"
             " --start/--end.",
    )
    g_input.add_argument(
        "--type", default="chapter",
        choices=["chapter", "translated", "redacted", "polished"],
        metavar="TYPE",
        help="Тип файлов глав для --compile-chapters (по умолчанию: chapter).",
    )
    g_input.add_argument(
        "--start", type=int, default=None, metavar="N",
        help="Начальная глава для --compile-chapters (пусто = с первой).",
    )
    g_input.add_argument(
        "--end", type=int, default=None, metavar="N",
        help="Конечная глава для --compile-chapters (пусто = до последней).",
    )
    g_input.add_argument(
        "--ner_file",
        default="ner.json",
        metavar="PATH",
        help="Путь к JSON-файлу с NER-данными (по умолчанию: ner.json).",
    )
    g_input.add_argument(
        "--output",
        default="wiki.md",
        metavar="PATH",
        help="Путь к выходному Markdown-файлу (по умолчанию: wiki.md).",
    )
    g_input.add_argument(
        "--prompt_file",
        default=None,
        metavar="PATH",
        help=(
            "Путь к файлу с кастомным промптом. "
            "Ожидается тег <prompt_wiki_article>...</prompt_wiki_article>. "
            "Если не указан — используется встроенный промпт."
        ),
    )

    # ── Параметры генерации ──
    g_gen = parser.add_argument_group("Параметры генерации")
    g_gen.add_argument(
        "--top",
        type=int,
        default=80,
        metavar="N",
        help="Максимальное число терминов для генерации (по умолчанию: 80).",
    )
    g_gen.add_argument(
        "--min-count",
        type=int,
        default=2,
        metavar="N",
        help="Минимальная частота упоминания термина в NER (по умолчанию: 2).",
    )
    g_gen.add_argument(
        "--exclude-types",
        type=str,
        default=None,
        metavar="T1,T2,...",
        help=(
            "Типы терминов для ИСКЛЮЧЕНИЯ из генерации, через запятую. "
            "По умолчанию ничего не исключается. "
            "Пример: --exclude-types Other,Material,Title"
        ),
    )
    g_gen.add_argument(
        "--types",
        type=str,
        default=None,
        metavar="T1,T2,...",
        help=(
            "Белый список типов (дополнительная фильтрация до генерации). "
            "Если не указан — обрабатываются все типы."
        ),
    )
    g_gen.add_argument(
        "--context-chunks",
        type=int,
        default=12,
        metavar="N",
        help="Максимум фрагментов контекста на один термин (по умолчанию: 12).",
    )
    g_gen.add_argument(
        "--near-distance",
        type=int,
        default=64,
        metavar="N",
        help=(
            "Дистанция NEAR-запроса в токенах для поиска маркеров "
            "описания рядом с именем термина. По умолчанию: 64."
        ),
    )
    g_gen.add_argument(
        "--chunk-size",
        type=int,
        default=1000,
        metavar="N",
        help="Размер чанка в символах для FTS5 индекса (по умолчанию: 1000).",
    )
    g_gen.add_argument(
        "--save-interval",
        type=int,
        default=5,
        metavar="N",
        help="Интервал сохранения кэша (каждые N статей, по умолчанию: 5).",
    )

    # ── Co-occurrence ──
    g_co = parser.add_argument_group("Co-occurrence (связи между терминами)")
    g_co.add_argument(
        "--co-occurrence-pairs",
        type=str,
        default="Person:Person,Person:Organisation,Person:Artifact",
        metavar="A:B,...",
        help=(
            "Пары типов для вычисления совместных появлений. "
            "Формат: ТипА:ТипБ,ТипА:ТипВ. "
            "По умолчанию: Person:Person,Person:Organisation,Person:Artifact. "
            "Пустая строка отключает co-occurrence."
        ),
    )
    g_co.add_argument(
        "--co-occurrence-top",
        type=int,
        default=5,
        metavar="N",
        help="Сколько связей выводить для каждого термина (по умолчанию: 5).",
    )

    # ── LLM-сервер ──
    g_llm = parser.add_argument_group("LLM-сервер")
    g_llm.add_argument(
        "--host",
        default=None,
        metavar="URL",
        help="Базовый URL LLM-сервера (пусто = HOST из .env).",
    )
    g_llm.add_argument(
        "--api_key",
        default=None,
        metavar="KEY",
        help="API-ключ (пусто = API_KEY из .env).",
    )
    g_llm.add_argument(
        "--model",
        default=None,
        metavar="NAME",
        help="Модель: --model или MODEL/WIKI_MODEL в .env (обязательна).",
    )
    g_llm.add_argument(
        "--no_reasoning", action="store_true",
        help="Отключить рассуждения (reasoning_effort=none).",
    )
    g_llm.add_argument(
        "--env_file", default=None, help="Явный путь к .env.",
    )
    g_llm.add_argument(
        "--retries",
        type=int,
        default=10,
        metavar="N",
        help="Число повторных попыток при ошибке LLM (по умолчанию: 10).",
    )
    g_llm.add_argument(
        "--timeout",
        type=int,
        default=600,
        metavar="SEC",
        help="Таймаут одного запроса в секундах (по умолчанию: 600).",
    )
    g_llm.add_argument(
        "--temperature",
        type=float,
        default=None,
        metavar="FLOAT",
        help=(
            "Температура генерации (0.0–2.0). "
            "По умолчанию не задаётся (сервер решает сам)."
        ),
    )
    g_llm.add_argument(
        "--thinking",
        type=str,
        default=None,
        choices=["none", "minimal", "low", "medium", "high",
                 "xhigh", "max"],
        metavar="LEVEL",
        help=(
            "Режим (усилие) размышления модели (reasoning effort). "
            "Варианты: none/minimal/low/medium/high/xhigh/max "
            "(none — отключить). "
            "По умолчанию не задаётся (сервер использует свой дефолт)."
        ),
    )

    # ── Производительность ──
    g_perf = parser.add_argument_group("Производительность")
    g_perf.add_argument(
        "--threads",
        type=int,
        default=4,
        metavar="N",
        help="Число параллельных потоков генерации (1–16, по умолчанию: 4).",
    )

    # ── Режим вывода ──
    g_out = parser.add_argument_group("Режим вывода")
    g_out.add_argument(
        "--rulate-mode",
        action="store_true",
        help=(
            "Режим форматирования для импорта на Rulate (Markdown): "
            "без содержания, заголовки сдвинуты на уровень глубже, "
            "спецзаголовок для импорта."
        ),
    )
    g_out.add_argument(
        "--rulate-html",
        action="store_true",
        help=(
            "Rulate в HTML: заголовки — <span style=\"font-size:20px/16px\">, "
            "списки <ul>, разделители <hr /> (вместо Markdown)."
        ),
    )
    g_out.add_argument(
        "--as-chapter", action="store_true",
        help=(
            "Сохранить вики как дополнительную последнюю главу "
            "chapters/00000_{N+1}_Wiki_Новеллы/: название главы «Wiki "
            "Новеллы» простым текстом, статьи — в формате как у rulate "
            "(заголовки глубже); файл — тип из --save-type (вместо "
            "файла --output)."
        ),
    )
    g_out.add_argument(
        "--save-type", default="polished",
        choices=["translated", "redacted", "polished"], metavar="TYPE",
        help="Тип файла вики-главы для --as-chapter "
             "(translated/redacted/polished; по умолчанию: polished).",
    )
    g_out.add_argument(
        "--toc", action=argparse.BooleanOptionalAction, default=True,
        help="Оглавление в обычном режиме (--no-toc — выключить).",
    )
    g_out.add_argument(
        "--toc-links", action=argparse.BooleanOptionalAction, default=True,
        help="Якоря-ссылки в оглавлении (--no-toc-links — плоский список).",
    )

    args = parser.parse_args()
    # Сервер: CLI > HOST/API_KEY/MODEL из .env
    env_data = parse_dotenv(find_env_file(args.env_file)) if args.env_file \
        else parse_dotenv(find_env_file())
    sc = get_server_config(env_data, "wiki")
    args.host = args.host or sc["host"] or ""
    args.api_key = args.api_key if args.api_key is not None else sc["api_key"]
    args.model = args.model or sc["model"]
    if not args.host:
        print_env_help()
        sys.exit("❌ Не задан сервер: укажите --host или создайте .env (HOST).")
    if not args.api_key:
        # P1 (AUDIT #2): ключ может прийти из окружения (web-слой)
        args.api_key = os.environ.get("LLM_API_KEY", "")

    # ── Валидация ──
    if args.context_chunks < 1 or args.top < 1 or args.min_count < 1:
        parser.error("Параметры --top, --min-count, --context-chunks должны быть ≥ 1.")
    if args.near_distance < 1:
        parser.error("Параметр --near-distance должен быть ≥ 1.")

    # ── Логирование ──
    try:
        os.makedirs("logs", exist_ok=True)
        os.makedirs("tmp", exist_ok=True)
    except OSError as exc:
        print(f"Не удалось создать logs/tmp: {exc}")
        return 1
    log_path = os.path.join("logs", "wiki_generation.log")
    cache_file = os.path.join("tmp", "wiki_cache.json")

    logger = _setup_logging(log_path)
    _cc_log_argv(logger)

    # ── Проверка файлов ──
    if args.compile_chapters:
        if not os.path.isdir("./chapters"):
            _log(logger, logging.ERROR, "❌ Папка chapters/ не найдена.")
            return
    elif not args.file:
        _log(logger, logging.ERROR,
             "❌ Нужен входной txt (file) или --compile-chapters.")
        return
    elif not os.path.exists(args.file):
        _log(logger, logging.ERROR, f"❌ Файл не найден: {args.file}")
        return
    if not os.path.exists(args.ner_file):
        _log(logger, logging.ERROR, f"❌ NER файл не найден: {args.ner_file}")
        return

    # ── Модель ──
    if args.no_reasoning:
        args.thinking = None  # disable в select = --no_reasoning
    base_url = args.host.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    try:
        model_name = determine_model(args.model, logger)
    except SystemExit:
        _log(logger, logging.ERROR, "❌ Модель не определена.")
        return

    # ── Промпт ──
    system_prompt = SYSTEM_WIKI_ARTICLE
    if args.prompt_file:
        if not os.path.exists(args.prompt_file):
            # R5-I: внешний промпт не найден — минимальный fallback
            _log(logger, logging.WARNING,
                 f"⚠️ Промпт-файл не найден: {args.prompt_file} "
                 f"— встроенный промпт.")
        else:
            loaded = load_wiki_prompts(args.prompt_file, logger)
            if "article" in loaded:
                system_prompt = loaded["article"]

    # ── Чтение текста: готовый txt ИЛИ сборка глав в память ──
    if args.compile_chapters:
        _log(logger, logging.INFO,
             f"📖 Сборка глав в память: chapters/ ({args.type})")
        full_text, info = compile_chapter_text(
            "./chapters", want=args.type,
            start=args.start, end=args.end, logger=logger)
        for w in (info.get("warnings") or []):
            _log(logger, logging.WARNING, w)
        if info.get("missing"):
            _log(logger, logging.WARNING,
                 f"⚠️ Пропущено глав: {len(info['missing'])} "
                 f"({', '.join(str(i) for i in info['missing'])})")
        if not full_text.strip():
            _log(logger, logging.ERROR, "❌ Ни одной главы не собрано.")
            return
        _log(logger, logging.INFO,
             f"   Глав собрано: {info['written']}, "
             f"размер: {len(full_text)} символов")
    else:
        _log(logger, logging.INFO, f"📖 Чтение: {args.file}")
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                full_text = f.read()
        except OSError as exc:
            _log(logger, logging.ERROR, f"❌ Не удалось прочитать {args.file}: {exc}")
            return 1
        if not full_text.strip():
            _log(logger, logging.ERROR, "❌ Файл пуст.")
            return
        _log(logger, logging.INFO, f"   Размер: {len(full_text)} символов")

    db = build_fts_index(full_text, args.chunk_size, logger)

    # ── Чтение NER ──
    _log(logger, logging.INFO, f"📂 Чтение NER: {args.ner_file}")
    try:
        with open(args.ner_file, "r", encoding="utf-8") as f:
            ner_data = json.load(f)
    except Exception as e:
        _log(logger, logging.ERROR, f"❌ Ошибка ner.json: {e}")
        return
    if not isinstance(ner_data, list):
        _log(logger, logging.ERROR, "❌ ner.json должен быть массивом.")
        return
    _log(logger, logging.INFO, f"   Записей: {len(ner_data)}")

    # ── Фильтр типов (белый список) ──
    if args.types:
        allowed = {t.strip() for t in args.types.split(",") if t.strip()}
        ner_data = [
            item for item in ner_data
            if _get_base_type(item.get("type")) in allowed
        ]
        _log(logger, logging.INFO, f"   После фильтра типов: {len(ner_data)}")

    # ── Исключаемые типы ──
    exclude_types: set[str] = set()
    if args.exclude_types:
        exclude_types = {t.strip() for t in args.exclude_types.split(",") if t.strip()}

    # ── Co-occurrence пары ──
    co_pairs: list[tuple[str, str]] = []
    if args.co_occurrence_pairs:
        co_pairs = _parse_co_occurrence_pairs(args.co_occurrence_pairs)

    # ── LLM аргументы ──
    llm_args = {
        "base_url": base_url,
        "model": model_name,
        "api_key": args.api_key,
        "max_retries": args.retries,
        "timeout": args.timeout,
        "temperature": args.temperature,
        "thinking": args.thinking,
    }

    rulate = args.rulate_mode or args.rulate_html
    rulate_html = args.rulate_html
    if rulate_html and os.path.splitext(args.output)[1].lower() == ".md":
        args.output = os.path.splitext(args.output)[0] + ".txt"

    # --as-chapter: вики-глава в chapters/ как последняя по номеру;
    # файл — выбранного типа (translated/redacted/polished), chapter.txt
    # не пишется
    if args.as_chapter:
        ch_map = build_chapter_map("./chapters")
        next_num = (max(ch_map) + 1) if ch_map else 1
        ch_dir = os.path.join("./chapters",
                              f"00000_{next_num}_Wiki_Новеллы")
        try:
            os.makedirs(ch_dir, exist_ok=True)
        except OSError as exc:
            _log(logger, logging.ERROR,
                 f"❌ Не удалось создать папку {ch_dir}: {exc}")
            return
        args.output = os.path.join(ch_dir, f"{args.save_type}.txt")
        _log(logger, logging.INFO,
             f"📁 Вики-глава: {ch_dir} (номер {next_num}, "
             f"{args.save_type}.txt)")

    _log(logger, logging.INFO,
         f"🚀 Wiki | Модель: {model_name} | Top: {args.top} | "
         f"Exclude: {exclude_types or 'нет'} | "
         f"Co-occur: {args.co_occurrence_pairs or 'выкл'} | "
         f"NEAR: {args.near_distance} | "
         f"Thinking: {args.thinking or 'сервер'} | "
         f"Rulate: {'html' if rulate_html else ('md' if rulate else 'off')} | "
         f"TOC: {'on' if args.toc else 'off'}/links: "
         f"{'on' if args.toc_links else 'off'} | "
         f"Потоков: {args.threads}")

    run_wiki_generation(
        ner_data=ner_data,
        db=db,
        exclude_types=exclude_types,
        top_n=args.top,
        min_count=args.min_count,
        context_chunks=args.context_chunks,
        near_distance=args.near_distance,
        system_prompt=system_prompt,
        llm_args=llm_args,
        max_workers=max(1, min(16, args.threads)),
        save_interval=max(1, args.save_interval),
        cache_file=cache_file,
        output_path=args.output,
        co_pairs=co_pairs,
        co_top=args.co_occurrence_top,
        rulate=rulate,
        toc=args.toc,
        toc_links=args.toc_links,
        rulate_html=rulate_html,
        as_chapter=args.as_chapter,
        logger=logger,
    )

    _log(logger, logging.INFO, f"🏁 Готово: {args.output}")


if __name__ == "__main__":
    sys.exit(main())  # H4 (AUDIT): код возврата main() наружу

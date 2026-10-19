#!/usr/bin/env python3
"""
translate_check_llm.py — проверка перевода через LLM (бывший fix_errors.py):
поиск и исправление ошибок перевода в главах. Вместо генерации
fix-скрипта пишется человекочитаемый накопительный файл правок
translate_check_llm_review.json (как ner_review.json): человек правит
статусы «принять»/«отклонить», затем --apply применяет только принятые.

Примеры:
  python3 translate_check_llm.py --start 1 --end 50  # поиск → review.json
  python3 translate_check_llm.py --apply --dry-run                  # предпросмотр
  python3 translate_check_llm.py --apply                            # применить принятые
  python3 translate_check_llm.py --start 1 --end 50 --auto-apply    # без человека
"""

import os
import sys
import re
import json
import argparse
import threading
import unicodedata
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── bootstrap: корень репо + обязательные зависимости ──
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
    add_check_prompt_arg,
    add_llm_args,
    bind_profile,
    check_prompt,
    complete_until,
    new_stage,
)
from core.common import (  # noqa: E402
    REVIEW_ACCEPT,
    REVIEW_REJECT,
    apply_fix_to_text,
    apply_flex_fix,
    atomic_write,
    build_chapter_map,
    estimate_tokens,
    find_fragment_owner,
    find_chapter_file as common_find_chapter_file,
    fix_entry,
    flex_fragment_pattern,
    get_tagged_prompt,
    merge_fix_entries,
    read_text_safe,
)
from core import settings as core_settings  # noqa: E402

DEFAULT_PROMPT_FILE = os.path.join("prompts", "translate_check_prompt.txt")
#: тег промпта стадии: промпт один, берётся только из него
PROMPT_TAG = "prompt_translate_check"
#: что читает стадия из промпт-файла: (тег, где читается, переменные тега)
PROMPT_CONTRACT: tuple = (
    (PROMPT_TAG, "один проход поиска ошибок", ("batch_text",)),
)
# Серверный предел ответа стадии, ТОКЕНЫ (не расчёт)
# предельный размер ответа сервера — общий настройка MAX_TOKENS
DEFAULT_REVIEW = "tmp/translate_check_llm_review.json"

# ─────────────────────────────────────────────
# ВСТРОЕННЫЙ ПРОМПТ
# ─────────────────────────────────────────────

DEFAULT_PROMPT = """\
<system>
Ты — профессиональный редактор перевода веб-новелл на русский язык.
Тебе дан текст одной или нескольких глав перевода. Найди ТОЛЬКО критические ошибки.

ТИПЫ ОШИБОК (исчерпывающий список):
- typo — несуществующие слова, опечатки («повзмаху», «взожжётся», «высокогого», «нефритовоую», «колецо», «воини», «бронен», «касая» вместо «кашая»)
- missing_word — пропущенное слово, без которого предложение грамматически не завершено («осталась была искра» — лишнее; «Линси возникло чувство» — пропущен предлог «у»)
- grammar — неверный падеж, согласование рода/числа, управление глагола
- logic — смена пола/имени/вида персонажа, числа предметов, названия организации — СТРОГО при наличии противоречия в ВИДИМОМ тексте
- incomplete — предложение обрывается, отсутствует сказуемое или подлежащее
- artifact — фрагменты исходного языка, оставшиеся в тексте; западные единицы измерения (футы, мили) в сеттинге оригинала; примечания переводчика в теле текста

ПОРОГ УВЕРЕННОСТИ (зависит от типа):
- typo, grammar: 90%+
- missing_word, incomplete, artifact: 95%+
- logic: 99%+ И обязательное подтверждение в видимом тексте.
Если для обоснования нужно знание за пределами данных глав — НЕ сообщай.

ПРИНЦИП МИНИМАЛЬНОГО ВМЕШАТЕЛЬСТВА:
- Меняй ТОЛЬКО ошибочнные слова/окончания. Не переписывай предложение целиком.
- Если исправление требует перестройки порядка слов — скорее всего, это стилистика. НЕ трогай.

КРИТИЧЕСКИ ВАЖНО — ИМЕНА, ТЕРМИНЫ, ФОРМЫ СЛОВ:
1. Если имя/название встречается в видимом тексте ТОЛЬКО ОДИН раз — ты НЕ можешь знать, правильное ли написание. Не сообщай об ошибке в написании имени, если нет второго упоминания для сравнения.
2. Если в видимом тексте имя встречается в ДВУХ РАЗНЫХ формах
(например, «Ци Чжаопин» в одной главе и «Ци Чжаои» в другой) — это противоречие, но ты НЕ знаешь, какая форма каноническая.
Не исправляй ни одну из форм. Вместо этого сообщи: type: "logic", corrected: скопируй fragment БЕЗ ИЗМЕНЕНИЙ, reason: «Встречены две формы имени: X и Y. Требуется сверка.»
3. Если не можешь определить пол персонажа по видимому тексту — НЕ сообщай об ошибке рода. Имя собственное НЕ является маркером пола.
Маркеры пола: местоимения он/она, слова мужчина/женщина/девушка, обращения брат/сестра/господин/госпожа, причастия с явным родом в том же предложении.
4. Составные существительные (тигр-демон, шелкопряд-насекомое, Образ-Дхарма) склоняются по обоим компонентам или по основному.
Проверяй оба компонента, но не «исправляй» нестандартное, но допустимое склонение.

НЕ ТРОГАЙ:
- Стилистику и синонимы (если смысл не искажён)
- Пунктуацию (если не меняет смысл)
- Авторский стиль, ритм, инверсии
- Написание имён собственных (слитно/раздельно/через дефис)
- Титулы и звания (если нет явного противоречия в видимом тексте)
- Порядок слов, если предложение грамматически верно
- Архаичные/поэтические формы, если они грамматически допустимы
в данном синтаксическом контексте
- Пол персонажа, если в видимом тексте нет ЯВНОГО маркера
- Вид/расу существа, если в видимом тексте нет прямого описания
- Названия техник/сект/артефактов, встречающиеся только один раз

ПРИМЕРЫ ТОГО, ЧТО НЕ ЯВЛЯЕТСЯ ОШИБКОЙ (не исправлять):
✗ «убедю» — корректная форма 1 л. ед. ч. будущего времени от «убедить».
Формы «убежду» НЕ существует.
✗ «Линь Шуи» в род. п. — корректное склонение имени «Линь Шуя».
НЕ менять обратно на «Линь Шуя».
✗ «Его Величество устали» — допустимое почтительное мн. ч.
Не менять на «устало».
✗ «раздробил Юэ Тяньцзи глазницу» — допустимая дативная конструкция.
Не менять порядок слов.
✗ «в сект возвращались фигуры» — если «сект» стоит в конструкции
«одна из сект» (род. п. мн. ч.), это корректно. Проверяй управление.
✗ «нефритовою» в твор. п. — архаичная, но допустимая форма.
Ошибка — только если по контексту нужен другой падеж.
✗ «Каждые тысяча трибуляций добавляли» — если «каждые» согласовано
с мн. ч. по смыслу (каждые [наборы по] тысяче), это допустимо.
Проверяй, действительно ли подлежащее в ед. ч.

САМОПРОВЕРКА ПЕРЕД ВЫДАЧЕЙ (обязательна для КАЖДОЙ правки):
1. ПРОИЗНЕСИ исправленное слово по слогам. Звучит ли оно как реальное русское слово? Ловушки: «убежду», «шелкопрядее», «Белопераой», «чуть-ли» (правильно: «чуть ли»).
Если звучит неестественно — НЕ выдавай.
2. ПОСТРОЙ ПАРАДИГМУ. Для исправленного существительного/прилагательного назови три формы: им. п., род. п., вин. п. Для глагола: инфинитив, 1 л. ед. ч., причастие. Если не можешь — форма не существует. НЕ выдавай.
3. ПРОВЕРЬ что фрагмент находится в нужном номере главы и "chapter" записан в json в формате int. Пример: "chapter": 117
4. ВСТАВЬ corrected обратно в предложение. Согласуется ли оно с подлежащим, сказуемым, управлением глагола?
Если исправление создаёт новое рассогласование или тавтологию — НЕ выдавай.
5. ПРОВЕРЬ СМЫСЛ. Не инвертирует ли исправление условие/отрицание? Пример: удаление «не» из «если бы не открыла» меняет смысл на противоположный. Если не уверен в логике — НЕ выдавай.

ТРЕБОВАНИЯ К ФРАГМЕНТУ:
- 1–3 предложения, НЕ КОРОЧЕ 50 символов, без переноса строк.
- УНИКАЛЬНЫЙ в пределах главы.
- НЕ пересказывай фрагмент. Скопируй его из текста посимвольно, включая: пробелы, тире (— vs – vs -), кавычки («» vs ""), многоточия (… vs ...), регистр букв.
- Если сомневаешься в точном написании хотя бы одного знака — расширь фрагмент на слово в каждую сторону, но НЕ угадывай.
- Фрагмент НЕ должен начинаться или заканчиваться на пробел.
- fragment и corrected отличаются ТОЛЬКО в месте ошибки; всё до и после — идентично посимвольно.
- Фрагмент из одного абзаца, от начала до конца слова/знака препинания.
Формат ответа — СТРОГО JSON-массив. Без markdown-обёртки, без ```, без пояснений до или после JSON:

[
{
"chapter": <номер главы>,
"fragment": "<точная цитата, ≥50 символов>",
"corrected": "<исправленный фрагмент>",
"type": "typo|missing_word|logic|incomplete|artifact|grammar",
"reason": "<краткое пояснение>"
}
]
Если ошибок нет — верни пустой массив: []
</system>
<user>
=== ТЕКСТ ГЛАВ ===

{batch_text}
</user>
"""


# ГИБКИЙ ПОИСК ФАЙЛА (адаптер над core.common.find_chapter_file)
def find_chapter_file_in_dir(dir_path, chapter_num, file_type, logger=None):
    """Возвращает (path, content) — как исторический интерфейс.
    Приоритеты и blacklist — единые из core.common.
    strict_types=True: если нет файла запрошенного типа, НЕ подставлять
    другие (оригинал chapter.txt, translated и т.п.)."""
    path, _warns = common_find_chapter_file(
        dir_path, chapter_num, want=file_type, logger=logger,
        strict_types=(file_type != "chapter"))
    if not path:
        if logger:
            logger.debug(f"Гл.{chapter_num}: '{file_type}' не найден в {dir_path}")
        return None, None
    try:
        return path, read_text_safe(path)
    except Exception as e:
        if logger:
            logger.error(f"Ошибка чтения {path}: {e}")
        return None, None


def find_chapter_file(ch, file_type, chapter_map, logger=None):
    paths = chapter_map.get(ch)
    if not paths:
        return None, None
    return find_chapter_file_in_dir(paths[0], ch, file_type, logger)


# ─────────────────────────────────────────────
# АВТООПРЕДЕЛЕНИЕ ДИАПАЗОНА
# ─────────────────────────────────────────────

def detect_chapter_range(chapter_map):
    if not chapter_map:
        return None, None
    nums = sorted(chapter_map.keys())
    return nums[0], nums[-1]


# ─────────────────────────────────────────────
# ПРОМПТЫ ИЗ ФАЙЛА
# ─────────────────────────────────────────────

def load_prompt(prompt_file, logger):
    """Внешний промпт (тег <prompt_translate_check>) или встроенный.
    Разметка запроса: правила — в <system>, данные — в <user> через
    переменную {batch_text}; без плейсхолдера данные дописываются после
    промпта (render_prompt). Промпт берётся только из тега: файл без него —
    не «промпт целиком», а встроенный промпт стадии."""
    if not prompt_file or not os.path.isfile(prompt_file):
        return DEFAULT_PROMPT
    try:
        with open(prompt_file, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as exc:
        logger.warning(f"Промпт-файл не читается ({exc}): {prompt_file}")
        return DEFAULT_PROMPT
    body = get_tagged_prompt(content, PROMPT_TAG)
    if body:
        return body
    logger.warning(f"В промпт-файле {prompt_file} нет тега <{PROMPT_TAG}> — "
                   f"промпт ВСТРОЕННЫЙ.")
    return DEFAULT_PROMPT


# ─────────────────────────────────────────────
# ЗАГОЛОВКИ ГЛАВ
# ─────────────────────────────────────────────

_HDR_RE = re.compile(r'^\s*Глава\s+\d+[.．:：]?\s*.*$', re.MULTILINE)


def strip_chapter_headers(content):
    return _HDR_RE.sub('', content)


# ─────────────────────────────────────────────
# ЧАНКИ
# ─────────────────────────────────────────────

def split_into_chunks(content, max_tokens):
    """Чанкование ПО ТОКЕНАМ (оценка estimate_tokens): абзацы → строки."""
    if estimate_tokens(content) <= max_tokens:
        return [content]
    chunks, current, cur_size = [], [], 0
    for para in content.split("\n\n"):
        ps = estimate_tokens(para) + 1
        if cur_size + ps > max_tokens and current:
            chunks.append("\n\n".join(current))
            current, cur_size = [], 0
        if ps > max_tokens:
            if current:
                chunks.append("\n\n".join(current))
                current, cur_size = [], 0
            lines, buf, ls = para.split("\n"), [], 0
            for line in lines:
                lt = estimate_tokens(line) + 1
                if ls + lt > max_tokens and buf:
                    chunks.append("\n".join(buf))
                    buf, ls = [], 0
                buf.append(line)
                ls += lt
            if buf:
                chunks.append("\n".join(buf))
        else:
            current.append(para)
            cur_size += ps
    if current:
        chunks.append("\n\n".join(current))
    return chunks or [content]


# ─────────────────────────────────────────────
# СБОР ГЛАВ
# ─────────────────────────────────────────────

def collect_chapters(start, end, file_type, budget, chapter_map, logger):
    chapters, skipped, chunked = [], 0, 0
    OV = 200
    for i in range(start, end + 1):
        paths = chapter_map.get(i)
        if not paths:
            skipped += 1
            continue
        fp, content = find_chapter_file_in_dir(paths[0], i, file_type, logger)
        if fp is None or content is None:
            skipped += 1
            continue
        if estimate_tokens(content) + OV > budget:
            mc = budget - OV
            ch = split_into_chunks(content, mc)
            chunked += 1
            logger.info(f"Глава {i}: {estimate_tokens(content)} ток. "
                        f"(оценка) → {len(ch)} чанков")
            for c in ch:
                chapters.append((i, fp, paths[0], c))
            continue
        chapters.append((i, fp, paths[0], content))
    total = end - start + 1
    logger.info(
        f"Сбор: найдено={len(chapters)}, пропущено={skipped}, "
        f"чанков={chunked}, диапазон={total}")
    if total > 0 and skipped / total > 0.1:
        logger.warning(f"⚠️ Пропущено {skipped}/{total} ({skipped/total:.0%})")
    return chapters


# ─────────────────────────────────────────────
# ПАКЕТЫ
# ─────────────────────────────────────────────

def build_batches(chapters, budget, logger):
    if not chapters:
        return []
    batches, cur, size = [], [], 0
    OV = 200
    for item in chapters:
        s = estimate_tokens(item[3]) + OV
        if cur and size + s > budget:
            batches.append(cur)
            cur, size = [], 0
        cur.append(item)
        size += s
    if cur:
        batches.append(cur)
    logger.info(f"Пакетов: {len(batches)} "
                f"(бюджет: {budget} токенов, оценка)")
    return batches


# ─────────────────────────────────────────────
# ЗАПРОС К LLM
# ─────────────────────────────────────────────

def parse_llm_json(text, logger):
    c = text.strip()
    c = re.sub(r'^```(?:json)?\s*', '', c)
    c = re.sub(r'\s*```$', '', c).strip()
    m = re.search(r'\[.*\]', c, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group())
        return d if isinstance(d, list) else None
    except json.JSONDecodeError:
        return None


# ─────────────────────────────────────────────
# ВАЛИДАЦИЯ
# ─────────────────────────────────────────────

def _to_int(v):
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        try:
            return int(v)
        except (ValueError, OverflowError):
            return None
    if isinstance(v, str) and v.strip().isdigit():
        try:
            return int(v.strip())
        except ValueError:
            return None
    return None


def _frag_in_text(frag, text):
    """Цитата в тексте: точное NFC-вхождение, иначе типографически-
    мягкое (кавычки/тире/многоточия/пробелы — flex_fragment_pattern).
    LLM процитировала текст в другой нормализации — это не повод
    отклонять правку."""
    frag_n = unicodedata.normalize("NFC", frag)
    text_n = unicodedata.normalize("NFC", text)
    if frag_n in text_n:
        return True
    flex = flex_fragment_pattern(frag)
    return flex != re.escape(frag_n) and re.search(flex, text_n) is not None

def validate_errors(errors, valid_ch, batch_contents, logger):
    """Сверка ответа LLM с фактическим текстом батча. Дрейф атрибуции
    (LLM указала соседнюю главу) отсекается здесь: цитата обязана
    найтись в заявленной главе (точно или типографически-мягко —
    flex_fragment_pattern), иначе правка перепривязывается на
    единственную главу батча с совпадением; ни одна — отклонение.
    Раньше сверка была только при чужом номере главы, и сдвиг ±1
    проходил насквозь."""
    out, dropped, moved = [], 0, 0
    for e in errors:
        ch = _to_int(e.get("chapter"))
        if ch is None:
            continue
        frag, corr = e.get("fragment", ""), e.get("corrected", "")
        if not frag or len(frag) < 10 or not corr:
            continue
        if frag.strip() == corr.strip():
            continue
        cont = batch_contents.get(ch, "")
        if cont and _frag_in_text(frag, cont):
            e["chapter"] = ch
            out.append(e)
            continue
        found = [oc for oc, ocont in batch_contents.items()
                 if ocont and _frag_in_text(frag, ocont)]
        if len(found) == 1:
            moved += 1
            logger.info(f"  ↪ Цитата не в гл.{ch}, а в гл.{found[0]} — "
                        f"правка перепривязана.")
            e["chapter"] = found[0]
            out.append(e)
            continue
        dropped += 1
        continue
    rej = dropped
    if rej:
        logger.info(f"Валидация: отклонено {rej}/{len(errors)}")
    if moved:
        logger.info(f"Валидация: перепривязано {moved} правок на "
                    f"реальные главы.")
    return out


# ─────────────────────────────────────────────
# SAFETY
# ─────────────────────────────────────────────

def _changed(f, c):
    # разная длина пар — ожидаемое состояние, strict=False осознанно
    d = sum(1 for a, b in zip(f, c, strict=False) if a != b)
    return d + abs(len(f) - len(c))


def apply_safety(errors, max_fix, min_len, max_chars, logger):
    if not max_fix and not min_len and not max_chars:
        return errors
    out, counts = [], defaultdict(int)
    for e in errors:
        ch = e.get("chapter", 0)
        if max_fix and counts[ch] >= max_fix:
            continue
        if max_fix:
            counts[ch] += 1
        if min_len and len(e.get("corrected", "")) < min_len:
            continue
        if max_chars and _changed(e.get("fragment", ""), e.get("corrected", "")) > max_chars:
            continue
        out.append(e)
    rm = len(errors) - len(out)
    if rm:
        logger.info(f"[SAFETY] Отфильтровано {rm}/{len(errors)}")
    return out


# ─────────────────────────────────────────────
# ОБРАБОТКА ПАКЕТА
# ─────────────────────────────────────────────

def compose_batch_text(batch) -> str:
    """Текст батча для LLM: заголовки глав («=== Глава N ===», с
    нумерацией частей для разрезанных) + очищенный текст. Используется
    и в предпросмотре запроса."""
    ch_counts = defaultdict(int)
    for c in batch:
        ch_counts[c[0]] += 1
    ch_seen = defaultdict(int)
    parts = []
    for c in batch:
        cleaned = strip_chapter_headers(c[3])
        if ch_counts[c[0]] > 1:
            ch_seen[c[0]] += 1
            hdr = f"=== Глава {c[0]} (часть {ch_seen[c[0]]}/{ch_counts[c[0]]}) ==="
        else:
            hdr = f"=== Глава {c[0]} ==="
        parts.append(f"{hdr}\n{cleaned}")
    return "\n".join(parts)


def render_prompt(prompt_tpl: str, batch_text: str) -> str:
    """user-контент: текст батча — через переменную {batch_text}.
    Нет плейсхолдера (внешний промпт без него) — текст дописывается
    после промпта (прежнее поведение). Используется и в предпросмотре."""
    if "{batch_text}" in prompt_tpl:
        return prompt_tpl.replace("{batch_text}", batch_text)
    return f"{prompt_tpl}\n\n{batch_text}"


def process_batch(batch, prompt, stage, retry_empty=0):
    """Один батч — один запрос (с повторами, когда правок не нашлось).

    Повторы считает core.stage.complete_until по числу стадии
    (TRANSLATE_CHECK_LLM_RETRY_EMPTY): ответ без ошибок, в т.ч. пустой
    массив `[]`, — повод спросить ещё раз; 0 — повторов нет."""
    logger = stage.logger
    text = compose_batch_text(batch)

    raw, _err = complete_until(
        stage, render_prompt(prompt, text), label="[check]",
        attempts=retry_empty,
        accept=lambda raw: bool(parse_llm_json(raw, logger)),
        note="пустой/нераспарсенный ответ")
    if raw is None:
        return None   # ошибка LLM → fail-fast на уровне прогона
    return parse_llm_json(raw, logger) or []


# ─────────────────────────────────────────────
# REVIEW-ФАЙЛ (накопительный, человекочитаемый)
# ─────────────────────────────────────────────

def load_review_file(path, logger):
    """Чтение review-файла: (meta|None, entries|None).
    Понимает объект с «правками» и legacy-массив записей."""
    if not os.path.isfile(path):
        return None, []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"❌ {path}: {e}")
        return None, None
    if isinstance(raw, list):
        entries = [x for x in (fix_entry(e) for e in raw) if x]
        return None, entries
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        entries = []
        for e in raw["entries"]:
            if not isinstance(e, dict):
                continue
            rec = {"chapter": e.get("chapter"), "fragment": e.get("old"),
                   "corrected": e.get("new"), "type": e.get("type"),
                   "reason": e.get("reason"),
                   "status": e.get("status", REVIEW_ACCEPT),
                   "applied": e.get("applied", False),
                   "file": e.get("file", "")}
            norm = fix_entry(rec, stage=e.get("stage", ""))
            if norm:
                if e.get("applied_at"):
                    norm["applied_at"] = e["applied_at"]
                entries.append(norm)
        return raw, entries
    return None, None


def save_review_file(path, input_dir, created, entries, params=None,
                     meta=None):
    """Запись накопительного файла правок (сохраняет дату создания и
    параметры прогона: params из проверки, иначе — из meta файла)."""
    doc = {"created": created,
           "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "input": input_dir,
           "entries": entries}
    saved_params = params if params is not None \
        else (meta or {}).get("params")
    if saved_params:
        doc["params"] = saved_params
    atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=2))


def errors_to_entries(errors, stage, file_type, chapter_map, logger):
    """Ошибки LLM → записи review-файла с путём файла главы."""
    entries, skip = [], 0
    cache = {}
    for e in errors:
        rec = fix_entry(e, stage=stage)
        if not rec:
            skip += 1
            continue
        ch = rec["chapter"]
        if ch not in cache:
            cache[ch] = find_chapter_file(ch, file_type, chapter_map,
                                          logger)
        fp, _content = cache[ch]
        if fp:
            rec["file"] = fp
        entries.append(rec)
    if skip:
        logger.info(f"Некорректных записей отсеяно: {skip}")
    return entries


# ─────────────────────────────────────────────
# ПРИМЕНЕНИЕ ПРАВОК
# ─────────────────────────────────────────────

def resolve_entry_path(entry, file_type, chapter_map, logger=None):
    """Путь файла правки: сохранённый «файл», иначе поиск по главе."""
    fp = entry.get("file") or ""
    if fp and os.path.isfile(fp):
        return fp
    fp2, _content = find_chapter_file(entry.get("chapter"), file_type,
                                      chapter_map, logger)
    return fp2


def apply_fix_entries(entries, file_type, chapter_map, logger,
                      dry_run=False):
    """Применение принятых неприменённых правок к файлам глав.
    Две фазы: точная/типографическая (группировка по файлу, замены
    последовательные — apply_flex_fix), затем — для пропущенных —
    переаттестация: цитата ищется по ВСЕЙ книге (find_fragment_owner)
    и правка перепривязывается на главу только при РОВНО ОДНОМ
    совпадении на всю книгу (дрейф атрибуции LLM ±1; защита от
    чужого текста). Помечает записи in-place: применено=True +
    «дата применения»; пропущенные получают note с причиной
    (из find_fragment_owner). Возвращает (applied, skipped)."""
    pending = [e for e in entries
               if e.get("status", REVIEW_ACCEPT) == REVIEW_ACCEPT
               and not e.get("applied")]
    skipped = len(entries) - len(pending)
    by_file = defaultdict(list)
    for e in pending:
        fp = resolve_entry_path(e, file_type, chapter_map, logger)
        if not fp:
            e["note"] = "файл главы не найден"
            logger.warning(f"  ⚠ Гл.{e.get('chapter')}: файл не найден — "
                           f"правка пропущена.")
            skipped += 1
            continue
        e["file"] = fp
        by_file[fp].append(e)
    applied = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    reattached = []   # (запись, прежний chapter) — во вторую фазу
    moved = []        # (запись, прежний chapter, прежний file) — откат dry-run
    for fp, group in by_file.items():
        try:
            text = read_text_safe(fp)
        except Exception as ex:
            logger.error(f"  ❌ Чтение {fp}: {ex}")
            skipped += len(group)
            continue
        cur, changed = text, False
        for e in group:
            nt, ok = apply_flex_fix(cur, e["old"], e["new"])
            if not ok:
                reattached.append((e, e.get("chapter")))
                continue
            cur, changed = nt, True
            e["applied"] = True
            e["applied_at"] = now
            e.pop("note", None)
            applied.append(e)
            logger.info(f"  ✔ Гл.{e['chapter']} [{e.get('type') or '?'}]: "
                        f"«{e['old'][:60]}» → «{e['new'][:60]}»")
        if changed and not dry_run:
            atomic_write(fp, cur)
    # Фаза 2: переаттестация пропущенных — только однозначные по всей книге
    for e, claimed in reattached:
        claimed = claimed if claimed is not None else e.get("chapter")
        ch, why = find_fragment_owner(chapter_map, e["old"],
                                      claimed=claimed, want=file_type,
                                      logger=logger)
        if ch is None:
            e["note"] = why or "фрагмент не найден в книге"
            logger.info(f"  ⚠ Гл.{claimed}: {e['note']} — пропуск.")
            skipped += 1
            continue
        logger.info(f"  ↪ Гл.{claimed}: цитата реально в гл.{ch} — "
                    f"правка перепривязана.")
        e["chapter"] = ch
        e.pop("note", None)
        fp2, _w = common_find_chapter_file(chapter_map[ch][0], ch,
                                           want=file_type,
                                           strict_types=True)
        if not fp2:
            e["chapter"] = claimed
            e["note"] = "файл найденной главы не открылся"
            skipped += 1
            continue
        e["file"] = fp2
        try:
            text = read_text_safe(fp2)
        except Exception as ex:
            e["chapter"] = claimed
            e["note"] = f"файл найденной главы не читается: {ex}"
            skipped += 1
            continue
        nt, ok = apply_flex_fix(text, e["old"], e["new"])
        if not ok:
            e["chapter"] = claimed
            e["note"] = "фрагмент не найден в найденной главе"
            skipped += 1
            continue
        if not dry_run:
            atomic_write(fp2, nt)
        else:
            moved.append((e, claimed, e.get("file")))
        e["applied"] = True
        e["applied_at"] = now
        e.pop("note", None)
        applied.append(e)
        logger.info(f"  ✔ Гл.{ch} (перепривязана) [{e.get('type') or '?'}]: "
                    f"«{e['old'][:60]}» → «{e['new'][:60]}»")
    if dry_run and applied:
        for e in applied:          # dry-run ничего не применяет
            e["applied"] = False
            e.pop("applied_at", None)
        for e, oc, of in moved:    # и перепривязку откатывает
            e["chapter"], e["file"] = oc, of
    return applied, skipped





# ─────────────────────────────────────────────
# MAIN (чистый CLI)
# ─────────────────────────────────────────────

def build_parser():
    ap = argparse.ArgumentParser(
        description="Поиск и исправление ошибок перевода через LLM (CLI).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Режимы:
  без флагов        поиск ошибок → правки в накопительный --review файл;
  --apply           применить правки со статусом «принять» (без LLM);
  --apply --dry-run предпросмотр применения;
  --auto-apply      сразу применить всё найденное (без человека).
Единицы: --request_budget — ТОКЕНЫ (оценка estimate_tokens); --type,
--min_fix_length, --max_changed_chars — СИМВОЛЫ; max_tokens — серверный
предохранитель, ТОКЕНЫ.
Сервер, модель, ключ, таймауты, повторы, потоки и рассуждения — ОДНИ на весь
конвейер: флаг стадии пуст → значение из общего .env (или профиля LLM).
Промпт-файл: один тег <prompt_translate_check>; без него — встроенный промпт.
""",
    )
    ap.add_argument("--preview-request", dest="preview_request", default=None,
                    help="ПРЕДПРОСМОТР: запрос первого батча\n"
                         "без сети → JSON-файл (messages +\n"
                         "статистика символов и токенов).")
    # Главы (имена полей — реестр настроек, core/settings.py)
    ap.add_argument("--start", type=int, default=None,
                    help="Начальная глава (иначе автодиапазон).")
    ap.add_argument("--end", type=int, default=None,
                    help="Конечная глава (иначе автодиапазон).")
    ap.add_argument("--chapters_dir", default="./chapters",
                    help="Папка глав (default: ./chapters).")
    ap.add_argument("--type", default="polished",
                    choices=["polished", "redacted", "translated"],
                    help="Тип файлов проверяемых глав.")
    ap.add_argument("--request_budget", type=int, default=25000,
                    help="Бюджет запроса, ТОКЕНЫ (оценка; промпт — запас 200).")
    ap.add_argument("--retry_empty", type=int, default=0, metavar="N",
                    help="Повторов при отсутствии правок, ШТ (0 = нет; пусто = "
                         "TRANSLATE_CHECK_LLM_RETRY_EMPTY). Ответ без ошибок "
                         "— не ошибка, а повод спросить ещё раз.")
    ap.add_argument("--prompt_file", default=DEFAULT_PROMPT_FILE,
                    help="Внешний промпт: тег <prompt_translate_check>.")
    # Ограничители правок: 0 — выключен (метрики разные: ШТ, СИМВОЛЫ, СИМВОЛЫ)
    ap.add_argument("--max_fixes_per_chapter", type=int, default=0,
                    help="Лимит правок на главу, ШТ (0 = нет); лишние правки "
                         "отсекаются по порядку выдачи.")
    ap.add_argument("--min_fix_length", type=int, default=0,
                    help="Мин. длина правки, СИМВОЛЫ (0 = нет); короче — "
                         "правка отбрасывается.")
    ap.add_argument("--max_changed_chars", type=int, default=0,
                    help="Макс. изменённых символов, СИМВОЛЫ (0 = нет); "
                         "длиннее — правка отбрасывается.")
    # Review-файл и применение (режимы, не настройки)
    ap.add_argument("--review", default=DEFAULT_REVIEW,
                    help=f"Накопительный файл правок (default: "
                         f"{DEFAULT_REVIEW}). Понимает и простой "
                         f"JSON-массив записей.")
    ap.add_argument("--apply", action="store_true",
                    help="Применить принятые правки из --review (без LLM).")
    ap.add_argument("--auto-apply", dest="auto_apply", action="store_true",
                    help="Сразу применить найденное без человека.")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="Предпросмотр: файлы не изменяются.")
    # сервер, модель, ключ, таймауты, повторы, потоки и рассуждения —
    # общий блок флагов (core.stage), своих дефолтов у стадии нет
    add_llm_args(ap, aliases=True)
    # проверка промпт-файла (--check-prompt) — общий слой core.stage
    add_check_prompt_arg(ap)
    return core_settings.apply_cli_defaults(ap, "translate_check_llm")


def build_chapter_map_safe(ch_dir, logger):
    chapter_map = build_chapter_map(ch_dir, logger)
    if not chapter_map:
        sys.exit(f"❌ Папки глав не найдены в {ch_dir}.")
    return chapter_map


def resolve_range(args, chapter_map, logger):
    if args.start is None or args.end is None:
        mn, mx = detect_chapter_range(chapter_map)
        if mn is None:
            sys.exit("Не удалось определить диапазон.")
        args.start = args.start if args.start is not None else mn
        args.end = args.end if args.end is not None else mx
        logger.info(f"Автодиапазон: {args.start}–{args.end}")
    if args.start > args.end:
        sys.exit("start > end")


def do_apply(args, logger) -> int:
    """Применение правок из review-файла (без LLM)."""
    if not os.path.exists(args.review):
        sys.exit(f"❌ Файл правок не найден: {args.review}. "
                 f"Сначала запустите поиск ошибок.")
    meta, entries = load_review_file(args.review, logger)
    if entries is None:
        sys.exit(f"❌ {args.review}: не распознан список правок.")
    pending = sum(1 for e in entries
                  if e["status"] == REVIEW_ACCEPT and not e["applied"])
    n_rej = sum(1 for e in entries if e["status"] == REVIEW_REJECT)
    n_done = sum(1 for e in entries if e["applied"])
    logger.info(f"📋 {args.review}: к применению {pending}, "
                f"отклонено {n_rej}, уже применено {n_done}.")
    ch_dir = os.path.abspath(args.chapters_dir)
    chapter_map = build_chapter_map_safe(ch_dir, logger)
    if args.dry_run:
        applied, skipped = apply_fix_entries(entries, args.type,
                                             chapter_map, logger,
                                             dry_run=True)
        logger.info(f"DRY-RUN: было бы применено {len(applied)}, "
                    f"пропущено {skipped}. Файлы не изменены.")
        return 0
    applied, skipped = apply_fix_entries(entries, args.type,
                                         chapter_map, logger)
    logger.info(f"Итог: применено {len(applied)}, "
                f"пропущено/отклонено {skipped}.")
    if not applied:
        logger.info("Нечего применять (все правки отклонены, уже "
                    "применены или список пуст).")
        return 0
    save_review_file(args.review, ch_dir,
                     (meta or {}).get("created")
                     or datetime.now().strftime("%Y-%m-%d %H:%M"),
                     entries, meta=meta)
    logger.info(f"✅ Главы обновлены ({len(applied)} правок); "
                f"флаги «применено» сохранены в {args.review}")
    return 0


def do_check(args, stage) -> int:
    """Поиск ошибок LLM → накопительный review-файл."""
    logger = stage.logger

    ch_dir = os.path.abspath(args.chapters_dir)
    logger.info(f"Директория глав: {ch_dir}")
    chapter_map = build_chapter_map_safe(ch_dir, logger)
    logger.info(f"Найдено глав: {len(chapter_map)}")
    resolve_range(args, chapter_map, logger)

    # Сервер, модель и рассуждения в лог пишет core.stage (общая точка) — здесь
    # только параметры самой проверки
    logger.info(
        f"Главы {args.start}–{args.end} | {args.type} | "
        f"budget={args.request_budget} | "
        f"threads={stage.profile.threads} | "
        f"retry_empty={args.retry_empty}")

    prompt = load_prompt(args.prompt_file, logger)
    logger.info(f"Лог: {stage.log_path}")

    chapters = collect_chapters(args.start, args.end, args.type,
                                args.request_budget, chapter_map, logger)
    if not chapters:
        logger.error("❌ Главы не найдены.")
        return 1
    batches = build_batches(chapters, args.request_budget, logger)
    if not batches:
        return 1

    # ── Предпросмотр запроса (--preview-request): первый батч ──
    if stage.preview(f"Батч 1/{len(batches)}",
                    render_prompt(prompt, compose_batch_text(batches[0])),
                    meta={"batches": len(batches),
                          "request_budget": args.request_budget,
                          "threads": stage.profile.threads,
                          "retry_empty": args.retry_empty,
                          "first_batch_chapters": sorted(
                              {c[0] for c in batches[0]}),
                          "prompt_file": args.prompt_file or ""}):
        return 0

    # метка прогона в review-файле (не путать с контекстом стадии `stage`)
    stage_label = f"Главы {args.start}–{args.end} ({args.type})"
    params = {"директория глав": args.chapters_dir,
              "тип файлов": args.type,
              "начало": args.start, "конец": args.end,
              "бюджет запроса": args.request_budget,
              "потоки": stage.profile.threads,
              "повторов при отсутствии правок": args.retry_empty,
              "промпт файл": args.prompt_file}
    meta, existing = load_review_file(args.review, logger)
    created = (meta or {}).get("created") \
        or datetime.now().strftime("%Y-%m-%d %H:%M")
    entries = existing or []
    added_total = 0
    lock = threading.Lock()

    def merge_and_save(errs):
        """Ошибки батча → записи → накопительный файл (под замком)."""
        nonlocal entries, added_total
        fresh = errors_to_entries(errs, stage_label, args.type,
                                  chapter_map, logger)
        if not fresh:
            return
        with lock:
            entries, added = merge_fix_entries(entries, fresh, logger)
            added_total += added
            save_review_file(args.review, ch_dir, created, entries,
                             params=params)

    all_errors = []
    done_cnt = [0]
    failed_cnt = [0]

    def worker(idx, batch):
        nums = [c[0] for c in batch]
        bc = {}
        for c in batch:
            bc[c[0]] = bc.get(c[0], "") + ("\n" if c[0] in bc else "") + c[3]
        logger.info(f"Пакет {idx + 1}/{len(batches)}: гл. "
                    f"{nums[0]}–{nums[-1]}")
        errs = process_batch(batch, prompt, stage, args.retry_empty)
        if errs is None:
            with lock:
                failed_cnt[0] += 1
            logger.error(f"✗ Пакет {idx + 1}: гл. {nums[0]}–{nums[-1]} — "
                         f"ошибка LLM.")
            return
        errs = validate_errors(errs, set(nums), bc, logger)
        errs = apply_safety(errs, args.max_fixes_per_chapter,
                            args.min_fix_length, args.max_changed_chars,
                            logger)
        with lock:
            all_errors.extend(errs)
            done_cnt[0] += 1
            dn = done_cnt[0]
        logger.info(f"✓ Пакет {idx + 1}: гл. {nums[0]}–{nums[-1]}, "
                    f"ошибок: {len(errs)} | {dn}/{len(batches)}")
        if errs:
            # файл пишется после КАЖДОГО ответа LLM — упавший прогон
            # не теряет правки уже готовых батчей
            merge_and_save(errs)

    # верхний предел потоков 16 (как в ner/wiki/translate)
    with ThreadPoolExecutor(max_workers=stage.profile.threads) as ex:
        futs = {ex.submit(worker, i, b): i for i, b in enumerate(batches)}
        with Progress(len(batches), "Проверка перевода LLM", unit="батч",
                      bar=True, logger=logger) as progress:
            for f in as_completed(futs):
                try:
                    f.result()
                except Exception as e:
                    logger.error(f"Поток: {e}")
                progress.step()

    logger.info(f"Батчей: {done_cnt[0]}/{len(batches)}")
    logger.info(f"Ошибок найдено: {len(all_errors)}")
    save_review_file(args.review, ch_dir, created, entries, params=params)
    logger.info(f"🧩 Правки: {args.review} "
                f"(новых: {added_total}, всего: {len(entries)})")
    if failed_cnt[0]:
        logger.error(f"❌ Пакетов с ошибкой LLM: {failed_cnt[0]} — прогон "
                     f"неполный, применение не запускается.")
        return 1
    if all_errors:
        tc = defaultdict(int)
        for e in all_errors:
            tc[e.get("type", "?")] += 1
        logger.info(f"Типы: {dict(tc)}")

    if args.auto_apply and entries:
        applied, skipped = apply_fix_entries(entries, args.type,
                                             chapter_map, logger,
                                             dry_run=args.dry_run)
        if args.dry_run:
            logger.info(f"DRY-RUN авто-применение: было бы "
                        f"{len(applied)}, пропущено {skipped}.")
        elif applied:
            save_review_file(args.review, ch_dir, created, entries,
                             params=params)
            logger.info(f"Авто-применение: {len(applied)} правок, "
                        f"пропущено {skipped}.")
        else:
            logger.info("Авто-применение: применять нечего.")
    elif entries and not args.auto_apply:
        logger.info("Дальше: правка статусов в " + args.review
                    + " (принять/отклонить), затем: python3 "
                    + "translate_check_llm.py "
                      "--apply --dry-run и --apply.")
    else:
        logger.info("Ошибок нет — применять нечего.")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # --check-prompt: вердикт о промпт-файле до логов, чтения глав и сети
    if check_prompt(args, "translate_check_llm", PROMPT_CONTRACT):
        return 0
    # лог стадии — всегда; профиль LLM нужен только режиму проверки
    stage = new_stage("translate_check_llm", args, log_fatal=False)
    if args.apply:
        return do_apply(args, stage.logger)
    return do_check(args, bind_profile(stage, args))


if __name__ == "__main__":
    sys.exit(main())

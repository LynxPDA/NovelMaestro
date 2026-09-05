#!/usr/bin/env python3
"""
translate_check_llm.py — проверка перевода через LLM (бывший fix_errors.py):
поиск и исправление ошибок перевода в главах. Вместо генерации
fix-скрипта пишется человекочитаемый накопительный файл правок
translate_check_llm_review.json (как ner_review.json): человек правит
статусы «принять»/«отклонить», затем --apply применяет только принятые
(бэкап <файл>.bak).

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
import shutil
import argparse
import threading
import unicodedata
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests  # noqa: F401 — проверка зависимости (LLM через core.common)
except ImportError:
    sys.exit("❌ Требуется: pip install requests")

try:
    from tqdm import tqdm
except ImportError:
    sys.exit("❌ Требуется: pip install tqdm")

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
    apply_fix_to_text,
    atomic_write,
    build_chapter_map,
    determine_model,
    emit_progress,
    find_chapter_file as common_find_chapter_file,
    find_env_file,
    fix_entry,
    get_server_config,
    log_argv,
    merge_fix_entries,
    parse_dotenv,
    print_env_help,
    read_text_safe,
    setup_logging,
    stream_chat_completion,
    web_progress_enabled,
    preview_logger,
    preview_request_payload,
    write_preview_request,
)

DEFAULT_PROMPT_FILE = os.path.join("prompts", "translate_check_prompt.txt")
DEFAULT_REVIEW = "translate_check_llm_review.json"

# ─────────────────────────────────────────────
# ВСТРОЕННЫЕ ПРОМПТЫ
# ─────────────────────────────────────────────

PASS1_PROMPT = """\
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
"""

PASS2_PROMPT = """\
Ты — старший редактор, выполняющий ВЕРИФИКАЦИЮ найденных ошибок.

Тебе дан:
1. Оригинальный текст глав.
2. Список ошибок, найденных младшим редактором (JSON).

Твоя задача — для КАЖДОЙ ошибки выполнить четыре проверки.
Ошибка проходит только если ВСЕ четыре проверки пройдены.

ПРОВЕРКА 1 — ФРАГМЕНТ СУЩЕСТВУЕТ:
- Найди fragment в тексте ТОЧНЫМ посимвольным совпадением.
- Если не найден — попробуй заменить тире/кавычки/многоточия на альтернативные варианты (— vs –, «» vs "").
- Если всё равно не найден → rejected.
- НЕ пытайся угадать или восстановить фрагмент.
- Убедись, что fragment уникален в главе.

ПРОВЕРКА 2 — ОШИБКА РЕАЛЬНА:
- Убедись, что это действительно ошибка, а не:
· стилистический выбор или авторская инверсия,
· корректная склонённая форма имени (Линь Шуи ≠ ошибка),
· допустимый порядок слов (дативная конструкция и т. п.),
· архаичная/поэтическая форма, грамматически допустимая в данном синтаксическом контексте,
· авторский неологизм или устоявшийся термин.
- Для logic-ошибок: в поле reason младший редактор обязан содержать конкретную цитату из видимого текста, которая
противоречит фрагменту. Если в reason нет цитаты-подтверждения или ссылка на текст за пределами видимых глав → rejected.
- Для ошибок рода: убедись, что пол персонажа подтверждён ЯВНЫМ маркером в видимом тексте (местоимение, обращение,
причастие в том же или соседнем предложении). Имя собственное НЕ является маркером. Если маркера нет → rejected.
- Для ошибок в именах/названиях: убедись, что каноническая форма подтверждена ВТОРЫМ упоминанием в видимом тексте.
Если имя встречается только один раз → rejected.
ПРОВЕРКА 3 — ИСПРАВЛЕНИЕ КОРРЕКТНО:
- Независимо от младшего редактора, просклоняй/проспрягай исправленное слово. Назови три формы (им. п., род. п., вин. п. для существительного; инфинитив, 1 л. ед. ч., причастие для глагола). Если не можешь построить парадигму — форма не существует → rejected.
- Подсчитай количество изменённых слов между fragment и corrected.
- Исправление не создаёт: тавтологию, новое рассогласование рода/числа/падежа, несуществующее слово, смысловую инверсию.
- Вставь corrected обратно в предложение. Убедись, что оно согласуется с подлежащим, сказуемым и управлением глагола.
- Ловушки: «убежду», «шелкопрядее», «Белопераой», «чуть-ли».
Если исправленная форма похожа на одну из ловушек → rejected.

ПРОВЕРКА 4 — МИНИМАЛЬНОСТЬ:
- Исправление меняет ТОЛЬКО ошибочный элемент.
- Если corrected перестраивает порядок слов, добавляет уточняющие слова или заменяет более 2 содержательных слов без грамматической необходимости → rejected.
- Если fragment и corrected идентичны (младший редактор сообщил о противоречии без исправления) → оставь как есть
со status "confirmed". Это информационное сообщение.

ПРИНЦИП МИНИМАЛЬНОГО ВМЕШАТЕЛЬСТВА:
- Меняй ТОЛЬКО ошибочное слово/окончание.
- Если не уверен на 95%+ (для logic — 99%+) → rejected.
- Лучше пропустить одну реальную ошибку, чем пропустить ложную правку.

НЕ ТРОГАЙ:
- Стилистику и синонимы (если смысл не искажён)
- Пунктуацию (если не меняет смысл)
- Авторский стиль, ритм, инверсии
- Написание имён собственных (слитно/раздельно/через дефис)
- Титулы и звания (если нет явного противоречия в видимом тексте)
- Порядок слов, если предложение грамматически верно

КРИТИЧЕСКИ ВАЖНО — ИМЕНА И ФОРМЫ СЛОВ:
- Китайские имена склоняются по правилам русского языка.
НЕ исправляй склонённую форму обратно на именительный падеж.
- Если не можешь определить пол персонажа по видимому тексту —
НЕ сообщай об ошибке рода.
- Если имя встречается в видимом тексте только один раз —
ты не можешь судить о правильности написания.
Если видишь КРИТИЧЕСКУЮ ошибку, пропущенную младшим редактором, и она проходит все четыре проверки → status "new".
Заполни для неё все поля (chapter, fragment, corrected, type, reason).

ТРЕБОВАНИЯ К ФРАГМЕНТУ:
- 1–3 предложения, НЕ КОРОЧЕ 50 символов, без переноса строк.
- УНИКАЛЬНЫЙ в пределах главы.
- fragment и corrected отличаются ТОЛЬКО в месте ошибки.
- Фрагмент из одного абзаца.
- Скопируй фрагмент посимвольно из текста. НЕ пересказывай.
Формат ответа — СТРОГО JSON-массив. Без markdown-обёртки, без ```, без пояснений до или после JSON:

[
{
"chapter": <номер главы>,
"fragment": "<точная цитата, ≥50 символов>",
"corrected": "<исправленный фрагмент>",
"type": "typo|missing_word|logic|incomplete|artifact|grammar",
"reason": "<краткое пояснение>",
"status": "confirmed|new"
}
]

Верни ТОЛЬКО confirmed и new. Отклонённые (rejected) НЕ включай.
Если все отклонены и новых нет — верни: []
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

def load_prompts(prompt_file, logger):
    p1, p2 = PASS1_PROMPT, PASS2_PROMPT
    if prompt_file and os.path.isfile(prompt_file):
        try:
            with open(prompt_file, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as exc:
            logger.warning(f"Промпт-файл не читается ({exc}): {prompt_file}")
            return p1, p2
        m1 = re.search(r'<pass1>(.*?)</pass1>', content, re.DOTALL)
        m2 = re.search(r'<pass2>(.*?)</pass2>', content, re.DOTALL)
        if m1:
            p1 = m1.group(1).strip()
        if m2:
            p2 = m2.group(1).strip()
        if not m1 and not m2:
            logger.warning("Теги <pass1>/<pass2> не найдены, встроенные.")
        elif not m1:
            logger.warning("В промпт-файле нет тега <pass1> — "
                           "pass1 будет ВСТРОЕННЫМ.")
        elif not m2:
            logger.warning("В промпт-файле нет тега <pass2> — "
                           "pass2 будет ВСТРОЕННЫМ.")
    return p1, p2


# ─────────────────────────────────────────────
# ЗАГОЛОВКИ ГЛАВ
# ─────────────────────────────────────────────

_HDR_RE = re.compile(r'^\s*Глава\s+\d+[.．:：]?\s*.*$', re.MULTILINE)


def strip_chapter_headers(content):
    return _HDR_RE.sub('', content)


# ─────────────────────────────────────────────
# ЧАНКИ
# ─────────────────────────────────────────────

def split_into_chunks(content, max_size):
    if len(content) <= max_size:
        return [content]
    chunks, current, cur_size = [], [], 0
    for para in content.split("\n\n"):
        ps = len(para) + 2
        if cur_size + ps > max_size and current:
            chunks.append("\n\n".join(current))
            current, cur_size = [], 0
        if ps > max_size:
            if current:
                chunks.append("\n\n".join(current))
                current, cur_size = [], 0
            lines, buf, ls = para.split("\n"), [], 0
            for line in lines:
                l = len(line) + 1
                if ls + l > max_size and buf:
                    chunks.append("\n".join(buf))
                    buf, ls = [], 0
                buf.append(line)
                ls += l
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
        if len(content) + OV > budget:
            mc = budget - OV
            ch = split_into_chunks(content, mc)
            chunked += 1
            logger.info(f"Глава {i}: {len(content)} симв. → {len(ch)} чанков")
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
        s = len(item[3]) + OV
        if cur and size + s > budget:
            batches.append(cur)
            cur, size = [], 0
        cur.append(item)
        size += s
    if cur:
        batches.append(cur)
    logger.info(f"Пакетов: {len(batches)} (бюджет: {budget})")
    return batches


# ─────────────────────────────────────────────
# ЗАПРОС К LLM
# ─────────────────────────────────────────────

def query_llm_raw(user_msg, sys_prompt, base_url, model, api_key,
                  max_retries, timeout, stream_timeout, temperature,
                  reasoning_effort, logger, label=""):
    """Делегирует в единый стрим-запрос core.common
    (loop-детект, [DONE], cut, empty — одна реализация на проект).
    max_tokens=32768 — исторический предел (ТОКЕНЫ, серверный
    предохранитель)."""
    text, _err = stream_chat_completion(
        base_url, model,
        [{"role": "system", "content": sys_prompt},
         {"role": "user", "content": user_msg}],
        api_key=api_key,
        max_retries=max_retries,
        timeout=timeout,
        stream_timeout=stream_timeout,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_tokens=32768,
        logger=logger,
        label=label,
    )
    return text


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


def validate_errors(errors, valid_ch, batch_contents, logger):
    out = []
    for e in errors:
        ch = _to_int(e.get("chapter"))
        if ch is None:
            continue
        frag, corr = e.get("fragment", ""), e.get("corrected", "")
        if not frag or len(frag) < 10 or not corr:
            continue
        if frag.strip() == corr.strip():
            continue
        if ch not in valid_ch:
            found = None
            for oc, ocont in batch_contents.items():
                if frag in ocont:
                    found = oc
                    break
            if found is not None:
                ch = found
            else:
                continue
        e["chapter"] = ch
        out.append(e)
    rej = len(errors) - len(out)
    if rej:
        logger.info(f"Валидация: отклонено {rej}/{len(errors)}")
    return out


# ─────────────────────────────────────────────
# SAFETY
# ─────────────────────────────────────────────

def _changed(f, c):
    d = sum(1 for a, b in zip(f, c) if a != b)
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


def process_batch(batch, p1, p2, two_pass, base_url, model, api_key,
                  retries, timeout, stream_timeout, temperature,
                  reasoning_effort, logger, retry_empty=0):
    text = compose_batch_text(batch)

    errors1 = []
    for att in range(1 + retry_empty):
        raw = query_llm_raw(text, p1, base_url, model, api_key,
                            retries, timeout, stream_timeout, temperature,
                            reasoning_effort, logger, "[P1]")
        if raw is None:
            return None   # ошибка LLM → fail-fast на уровне прогона
        parsed = parse_llm_json(raw, logger)
        if parsed:
            errors1 = parsed
            break
        if att < retry_empty:
            logger.info(f"[P1] Пусто, повтор {att+2}/{1+retry_empty}")
    if not errors1:
        return []
    if not two_pass:
        return errors1

    ej = json.dumps(errors1, ensure_ascii=False, indent=2)
    user2 = f"=== ОРИГИНАЛЬНЫЙ ТЕКСТ ===\n{text}\n\n=== НАЙДЕННЫЕ ОШИБКИ ===\n{ej}"
    raw2 = query_llm_raw(user2, p2, base_url, model, api_key,
                         retries, timeout, stream_timeout, temperature,
                         reasoning_effort, logger, "[P2]")
    if raw2 is None:
        logger.warning("[P2] Нет ответа, использую P1.")
        return errors1
    e2 = parse_llm_json(raw2, logger)
    if e2 is None:
        logger.warning("[P2] JSON не распарсен, P1.")
        return errors1
    verified = [e for e in e2 if e.get("status") in ("confirmed", "new")]
    n_new = sum(1 for e in e2 if e.get("status") == "new")
    logger.info(f"[P2] Подтверждено: {len(verified)} (новых: {n_new})")
    return verified


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
                      dry_run=False, no_bak=False):
    """Применение принятых неприменённых правок к файлам глав.
    Группировка по файлу, замены последовательные (NFC, первое
    вхождение). Бэкап <файл>.bak перед первой записью (кроме
    no_bak=True). Помечает записи in-place: применено=True +
    «дата применения». Возвращает (applied, skipped)."""
    pending = [e for e in entries
               if e.get("status", REVIEW_ACCEPT) == REVIEW_ACCEPT
               and not e.get("applied")]
    skipped = len(entries) - len(pending)
    by_file = defaultdict(list)
    for e in pending:
        fp = resolve_entry_path(e, file_type, chapter_map, logger)
        if not fp:
            logger.warning(f"  ⚠ Гл.{e.get('глава')}: файл не найден — "
                           f"правка пропущена.")
            skipped += 1
            continue
        e["file"] = fp
        by_file[fp].append(e)
    applied = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for fp, group in by_file.items():
        try:
            text = read_text_safe(fp)
        except Exception as ex:
            logger.error(f"  ❌ Чтение {fp}: {ex}")
            skipped += len(group)
            continue
        cur, changed = text, False
        for e in group:
            nt, ok = apply_fix_to_text(cur, e["old"], e["new"])
            if not ok:
                logger.info(f"  ⚠ Гл.{e['chapter']}: фрагмент не найден — "
                            f"пропуск.")
                skipped += 1
                continue
            cur, changed = nt, True
            e["applied"] = True
            e["applied_at"] = now
            applied.append(e)
            logger.info(f"  ✔ Гл.{e['chapter']} [{e.get('type') or '?'}]: "
                        f"«{e['old'][:60]}» → «{e['new'][:60]}»")
        if changed and not dry_run:
            if not no_bak:
                shutil.copy2(fp, fp + ".bak")
            atomic_write(fp, cur)
    if dry_run and applied:
        for e in applied:          # dry-run ничего не применяет
            e["applied"] = False
            e.pop("applied_at", None)
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
Единицы: --context_budget, --min_fix_length, --max_changed_chars — СИМВОЛЫ;
max_tokens (32768) — серверный предохранитель, ТОКЕНЫ.
Сервер: --host/--model/--api_key (CLI) > HOST/API_KEY/MODEL из .env
(модель: TRANSLATE_CHECK_LLM_MODEL → MODEL).
Промпт-файл: теги <pass1>/<pass2>; без тегов — встроенные промпты.
""",
    )
    # Сервер
    ap.add_argument("--preview-request", dest="preview_request",
                   default=None,
                   help="ПРЕДПРОСМОТР: pass1-запрос первого батча\n"
                        "без сети → JSON-файл (messages +\n"
                        "статистика СИМВОЛОВ).")
    ap.add_argument("--host", default=None,
                    help="URL API-сервера (пусто = HOST из .env).")
    ap.add_argument("--model", default=None,
                    help="Модель: --model или MODEL/TRANSLATE_CHECK_LLM_MODEL в .env.")
    ap.add_argument("--api_key", default=None,
                    help="Bearer-ключ (пусто = API_KEY из .env).")
    ap.add_argument("--env_file", default=None, help="Явный путь к .env.")
    # Главы
    ap.add_argument("--start", type=int, default=None,
                    help="Начальная глава (иначе автодиапазон).")
    ap.add_argument("--end", type=int, default=None,
                    help="Конечная глава (иначе автодиапазон).")
    ap.add_argument("--chapters_dir", default="./chapters",
                    help="Папка глав (default: ./chapters).")
    ap.add_argument("--type", dest="file_type", default="polished",
                    choices=["polished", "redacted", "translated"],
                    help="Тип проверяемых файлов (default: polished).")
    # Режим
    ap.add_argument("--two_pass", action="store_true",
                    help="Второй проход верификации (pass2).")
    ap.add_argument("--context_budget", type=int, default=75000,
                    help="Бюджет контекста на пакет, СИМВОЛЫ (default: 75000).")
    # Review-файл и применение
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
    ap.add_argument("--no-bak", dest="no_bak", action="store_true",
                    help="Не создавать бэкапы <файл>.bak при применении "
                        "(по умолчанию создаются).")
    # LLM
    ap.add_argument("--temperature", type=float, default=None,
                    help="Температура (иначе дефолт сервера).")
    ap.add_argument("--reasoning_effort", default=None,
                    choices=["none", "minimal", "low", "medium", "high",
                             "xhigh", "max"],
                    help="Усилия рассуждения модели: none/minimal/low/"
                         "medium/high/xhigh/max (пусто = сервер; "
                         "none — отключить).")
    ap.add_argument("--max_retries", type=int, default=3,
                    help="Попытки на запрос (default: 3).")
    ap.add_argument("--timeout", type=int, default=120,
                    help="Таймаут соединения, сек (default: 120).")
    ap.add_argument("--stream_timeout", type=int, default=300,
                    help="Таймаут стрима, сек (default: 300).")
    ap.add_argument("--retry_empty", type=int, default=0,
                    help="Доп. повторы при пустом ответе (0=выкл).")
    # Потоки
    ap.add_argument("--threads", type=int, default=4,
                    help="Параллельные пакеты (default: 4).")
    ap.add_argument("--prompt_file", default=DEFAULT_PROMPT_FILE,
                    help="Внешние промпты: теги <pass1>/<pass2>.")
    # Safety
    ap.add_argument("--max_fixes_per_chapter", type=int, default=0,
                    help="Лимит правок на главу (0=без лимита).")
    ap.add_argument("--min_fix_length", type=int, default=0,
                    help="Мин. длина corrected, СИМВОЛЫ (0=выкл).")
    ap.add_argument("--max_changed_chars", type=int, default=0,
                    help="Макс. изменённых символов в правке (0=выкл).")
    return ap


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
        applied, skipped = apply_fix_entries(entries, args.file_type,
                                             chapter_map, logger,
                                             dry_run=True,
                                             no_bak=args.no_bak)
        logger.info(f"DRY-RUN: было бы применено {len(applied)}, "
                    f"пропущено {skipped}. Файлы не изменены.")
        return 0
    applied, skipped = apply_fix_entries(entries, args.file_type,
                                         chapter_map, logger,
                                         no_bak=args.no_bak)
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
    logger.info(f"✅ Главы обновлены ({len(applied)} правок"
                + ("; без бэкапов" if args.no_bak else "; бэкапы "
                  f"<файл>.bak") + "); флаги «применено» сохранены "
                f"в {args.review}")
    return 0


def do_check(args, logger) -> int:
    """Поиск ошибок LLM → накопительный review-файл."""
    env_path = find_env_file(args.env_file)
    env_data = parse_dotenv(env_path)
    sc = get_server_config(env_data, "translate_check_llm")
    host = args.host or sc["host"]
    api_key = args.api_key if args.api_key is not None else sc["api_key"]
    model = args.model or sc["model"]
    if not api_key:
        api_key = os.environ.get("LLM_API_KEY", "")
    if not host:
        print_env_help()
        sys.exit("❌ Не задан сервер: укажите --host или создайте .env (HOST).")

    ch_dir = os.path.abspath(args.chapters_dir)
    logger.info(f"Директория глав: {ch_dir}")
    chapter_map = build_chapter_map_safe(ch_dir, logger)
    logger.info(f"Найдено глав: {len(chapter_map)}")
    resolve_range(args, chapter_map, logger)

    logger.info(
        f"Главы {args.start}–{args.end} | {args.file_type} | "
        f"2pass={args.two_pass} | budget={args.context_budget} | "
        f"threads={args.threads} | retry_empty={args.retry_empty} | "
        f"reasoning={args.reasoning_effort or 'off'}")

    p1, p2 = load_prompts(args.prompt_file, logger)
    base_url = host.rstrip("/")
    if "/v1" not in base_url:
        base_url += "/v1"
    model_name = determine_model(model, logger)
    logger.info(f"API: {base_url} | модель: {model_name}")

    chapters = collect_chapters(args.start, args.end, args.file_type,
                                args.context_budget, chapter_map, logger)
    if not chapters:
        logger.error("❌ Главы не найдены.")
        return 1
    batches = build_batches(chapters, args.context_budget, logger)
    if not batches:
        return 1

    # ── Предпросмотр запроса (--preview-request): pass1 первого батча ──
    if args.preview_request:
        log = preview_logger("translate_check_llm")
        log_argv(log)
        user_text = compose_batch_text(batches[0])
        payload = preview_request_payload(
            "translate_check_llm", f"Pass1 · батч 1/{len(batches)}",
            model_name,
            [{"role": "system", "content": p1},
             {"role": "user", "content": user_text}],
            meta={
                "batches": len(batches),
                "context_budget": args.context_budget,
                "two_pass": bool(args.two_pass),
                "threads": args.threads,
                "first_batch_chapters": sorted({c[0] for c in batches[0]}),
                "prompt_file": args.prompt_file or "",
            })
        write_preview_request(args.preview_request, payload)
        log.info("✅ Предпросмотр запроса: %s (%d симв. user)",
                 args.preview_request, len(user_text))
        return 0

    stage = f"Главы {args.start}–{args.end} ({args.file_type})"
    params = {"директория глав": args.chapters_dir,
              "тип файлов": args.file_type,
              "начало": args.start, "конец": args.end,
              "бюджет": args.context_budget, "потоки": args.threads,
              "два прохода": bool(args.two_pass),
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
        fresh = errors_to_entries(errs, stage, args.file_type,
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
        errs = process_batch(
            batch, p1, p2, args.two_pass, base_url, model_name, api_key,
            args.max_retries, args.timeout, args.stream_timeout,
            args.temperature, args.reasoning_effort,
            logger, retry_empty=args.retry_empty)
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
            merge_and_save(errs)

    # верхний предел потоков 16 (как в ner/wiki/translate)
    with ThreadPoolExecutor(max_workers=max(1, min(16, args.threads))) as ex:
        futs = {ex.submit(worker, i, b): i for i, b in enumerate(batches)}
        pbar = tqdm(total=len(batches), unit="batch", desc="LLM",
                    disable=web_progress_enabled())
        # свой счётчик — pbar.n мёртв при disable=True
        done = 0
        # стартовое событие прогресса — бар виден сразу
        emit_progress(done, len(batches), "Проверка перевода LLM")
        if web_progress_enabled():
            logger.info(f"📊 Прогресс: {done}/{len(batches)}")
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as e:
                logger.error(f"Поток: {e}")
            done += 1
            pbar.update(1)
            emit_progress(done, len(batches), "Проверка перевода LLM")
        pbar.close()

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
        applied, skipped = apply_fix_entries(entries, args.file_type,
                                             chapter_map, logger,
                                             dry_run=args.dry_run,
                                             no_bak=args.no_bak)
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
    try:
        os.makedirs("logs", exist_ok=True)
    except OSError as exc:
        print(f"⚠ logs/ не создаётся: {exc}", file=sys.stderr)
    logger, log_path = setup_logging(os.path.join("logs", "translate_check_llm"))
    log_argv(logger)
    logger.info(f"Лог: {log_path}")
    if args.apply:
        return do_apply(args, logger)
    return do_check(args, logger)


if __name__ == "__main__":
    sys.exit(main())

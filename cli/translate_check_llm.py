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
    FIX_ERROR_TYPES,
    SENTENCE_END_CHARS,
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
    fragment_occurrences,
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

#: запас бюджета на сам промпт и разметку пары — ТОКЕНЫ (оценка); глава,
#: чья пара с этим запасом не влезает в бюджет, в проверку не идёт.
#: Промпт стадии длинный (правила типов, глоссарий-примеры, род имён) и
#: вместе с разметкой пары занимает около трёх тысяч; запас в 200 оставлял
#: главам лишние токены, и пакет упирался в предел сервера уже на ответе.
PROMPT_OVERHEAD = 3000

# ─────────────────────────────────────────────
# ВСТРОЕННЫЙ ПРОМПТ
# ─────────────────────────────────────────────

DEFAULT_PROMPT = """\
<system>
Ты — технический редактор перевода веб-новелл на русский язык.
Твоя задача — найти в тексте ТОЛЬКО объективные, 100% доказуемые ошибки. При малейшем сомнении, а также при вкусовых, стилистических или пунктуационных шероховатостях — игнорируй их.

РАБОТА С ТЕКСТОМ И ПАРАМИ [ОРИГИНАЛ] / [ПЕРЕВОД]:
- Каждая глава приходит либо парой блоков [ОРИГИНАЛ] и [ПЕРЕВОД], либо только блоком [ПЕРЕВОД].
- Если есть [ОРИГИНАЛ] — используй его как источник истины для ВСЕХ типов ошибок (смысл, имена, кто говорит реплику, пол персонажей, термины).
- Все цитаты (`fragment`) и правки (`corrected`) берутся СТРОГО из блока [ПЕРЕВОД].
- Оценивай только видимый текст. Если для доказательства ошибки нужно знать контекст прошлых глав — НЕ сообщай о ней.

ТИПЫ ОШИБОК (исчерпывающий список):
- typo — опечатки, несуществующие слова («повзмаху», «высокогого», «нефритовоую», «колецо», «касая» вместо «кашая»).
- grammar — неверный падеж (в т.ч. несклоняемые вставки из глоссария: «вышел из Павильон Меча»), нарушение согласования рода/числа, неверное управление глагола.
- missing_word — пропуск предлога/слова или случайный дубль слова, ломающий грамматику («Линси возникло чувство» → пропущено «У»; «осталась была искра» → лишнее слово).
- incomplete — предложение обрывается на полуслове (в т.ч. в самом конце главы), отсутствует подлежащее или сказуемое, и это не авторская парцелляция.
- artifact — непереведённые символы (иероглифы, кана, хангыль, латиница в русских словах), чуждые сеттингу меры (футы/мили в восточном фэнтези), оставшиеся в тексте примечания переводчика/автора («прим. пер.», «PS:»), системный мусор сайтов.
- logic — противоречие внутри видимого текста или со здравым смыслом: внезапная смена пола/имени/вида персонажа, путаница кто кому отвечает в диалоге, скачки обращения «ты/вы» между одними и теми же людьми в одной сцене, перепутанные омонимы («упал на мужской пол», «выстрелил из репчатого лука»).
- mistranslation — расхождение смысла с [ОРИГИНАЛ] или очевидный сбой перевода/глоссария: искажение смысла фразы, потеря/добавление «не», инверсия причины и следствия, перепутаны субъект и объект, буквальный перевод идиом и счётных слов, ложные подстановки из глоссария.

КРИТИЧЕСКИ ВАЖНЫЕ ПРАВИЛА (ИМЕНА, ГЛОССАРИЙ, РОД):

1. Ложные срабатывания глоссария (термин вместо обычного слова):
   Частая ошибка автоперевода — замена обычного слова на имя собственное или термин из глоссария (например: яд → «Веном», огни → «Иллюминаты», воля → «Уилл»). В итоге получаются нелепые фразы: «...шёл дождь из Веном...» или «...всполохи Иллюминатов озарили город...».
   — Такие ошибки ОБЯЗАТЕЛЬНО исправляй (тип `mistranslation` или `logic`): заменяй неуместный термин на обычное слово по смыслу и ставь его в правильный падеж.

2. Подмена персонажа или термина:
   — Если по [ОРИГИНАЛ] или прямому контексту сцены очевидно, что переводчик по ошибке вставил чужое имя (перепутал двух присутствующих героев или взял не ту запись из глоссария) — ИСПРАВЬ на правильное имя из контекста (`mistranslation` или `logic`).
   — Если имя/название встречается в видимом тексте только ОДИН раз и не противоречит [ОРИГИНАЛ] — НЕ трогай его.
   — Если одно и то же имя в тексте написано в ДВУХ разных вариантах транскрипции (например, «Ци Чжаопин» и «Ци Чжаои») и нельзя точно знать, какой вариант каноничен: НЕ исправляй наугад. Укажи `type`: "logic", скопируй `fragment` в `corrected` БЕЗ ИЗМЕНЕНИЙ, а в `reason` напиши: «Встречены две формы имени: X и Y. Требуется сверка».

3. Род персонажей и ники/прозвища («Сицилия», «Машина Смерти»):
   — Глаголы, причастия и краткие прилагательные (в т.ч. в репликах от 1-го лица «Я сделал/сделала») должны согласовываться с РЕАЛЬНЫМ полом персонажа по сюжету, а НЕ с грамматическим родом его имени или никнейма.
   — Пример: если персонаж по контексту мужчина (есть маркеры: он, парень, мужчина, брат, господин), но носит ник женского рода («Сицилия», «Машина Смерти»), то фраза «Сицилия сказала» или «Машина Смерти пришла» — это ОШИБКА (`grammar`). Исправляй на мужской род: «Сицилия сказал». И наоборот — не «исправляй» правильный мужской род на женский из-за окончания ника.
   — Реальный пол определяй с приоритетом: [ОРИГИНАЛ], затем видимый контекст сцены; грамматический род имени или никнейма источником НЕ является. Если [ОРИГИНАЛ] однозначно задаёт пол (местоимения, родственные слова, титулы, согласование), а в [ПЕРЕВОДЕ] пол противоположный — прав [ОРИГИНАЛ]: исправляй род (`grammar`; если искажён смысл — `mistranslation`). Если пол по видимому тексту и [ОРИГИНАЛУ] определить невозможно — род НЕ трогай.

СТРОГИЕ ЗАПРЕТЫ (НЕ ТРОГАЙ):
- Стилистику, подбор синонимов, тавтологии, авторский ритм и инверсии.
- Пунктуацию, если она не меняет смысл предложения на противоположный.
- Заголовки глав (они переводятся свободно).

ТЕХНИЧЕСКИЕ ТРЕБОВАНИЯ К ПОЛЯМ `fragment` И `corrected`:
- Длина `fragment`: НЕ МЕНЕЕ 50 символов (минимум 7–10 слов, бери 1–2 полных предложения подряд), чтобы цитата была абсолютно уникальной в пределах главы.
- Границы: `fragment` берётся строго внутри ОДНОГО абзаца, без переносов строк. Не начинай и не заканчивай `fragment` пробелом.
- Посимвольная точность: копируй `fragment` из [ПЕРЕВОД] один в один, сохраняя все пробелы, виды кавычек («» vs ""), тире (— vs -) и многоточия.
- Точечность правки: `corrected` должен отличаться от `fragment` ТОЛЬКО в месте самой ошибки. Весь текст до и после ошибки должен посимвольно совпадать с `fragment`.
- Самопроверка перед записью: убедись, что исправленное слово реально существует в русском языке, стоит в нужном падеже и согласуется с соседними словами.

ФОРМАТ ОТВЕТА:
Верни СТРОГО валидный JSON-массив (без обёртки ```json и без любого текста до или после массива).
Соблюдай именно такой порядок полей (сначала докажи ошибку в `reason`, затем пиши `corrected`):

[
  {
    "chapter": 117,
    "type": "typo|grammar|missing_word|incomplete|artifact|logic|mistranslation",
    "fragment": "<точная цитата из ПЕРЕВОДА без переносов строк, ≥50 символов (от 7–10 слов)>",
    "reason": "<краткое доказательство ошибки и проверка грамматики исправления>",
    "corrected": "<исправленный fragment>"
  }
]

Если подтверждённых ошибок нет, верни пустой массив: []
</system>
<user>
=== ТЕКСТ ГЛАВ ===
(главы идут парами [ОРИГИНАЛ]/[ПЕРЕВОД], если оригинал есть)

{batch_text}
</user>
"""

#: отличительная фраза нового блока правил: по ней --check-prompt
#: предупреждает, что внешний промпт старше сверки с оригиналом
SOURCE_PROMPT_MARK = "[ОРИГИНАЛ]"


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

#: Порог «строка похожа на заголовок» — СИМВОЛЫ. У заголовков двух книг
#: медиана 11 и 22, 99-й процентиль 25 и 106, а самый длинный абзац тела
#: начинается с 18 и 37. Граница на 160 с запасом покрывает длинные
#: названия («Глава 2693. …» с подзаголовком) и не задевает прозу.
_HDR_MAX_CHARS = 160

#: сколько символов допускается ПЕРЕД номером главы в заголовке. Замер по
#: 7333 заголовкам двух книг: самый длинный префикс — «Глава» (5 символов),
#: у китайской записи префикс «第» (1). В прозе перед числом стоит фраза
#: («В 1999 году…»), и она в этот порог не влезает.
_HDR_PREFIX = 8

#: что срезается с краёв первого слова, чтобы «Глава:» и «(Глава)» дали
#: служебное слово, а не пунктуацию: знаки обоих тире, кавычки, скобки
_HDR_TRIM_CHARS = ",.;:!?—–-–»«\"'()[]"


def first_line(text):
    """Первая непустая строка текста ("" — текста нет)."""
    for line in str(text or "").split("\n"):
        if line.strip():
            return line.strip()
    return ""


#: служебные слова, которые НЕ бывают тегом заголовка: если перед номером
#: стоит такое, это проза («в 113 году», «about 113 people»), а не «Глава».
_NOT_HEADER_TAG = frozenset((
    "в", "во", "на", "о", "об", "обо", "за", "до", "по", "из", "от", "к",
    "ко", "с", "со", "у", "при", "для", "без", "через", "около", "свыше",
    "более", "менее", "около",
    "in", "on", "at", "of", "to", "by", "for", "with", "from", "about",
    "after", "before", "since", "until", "over", "under", "around",
    "the", "a", "an",
))


def _is_header_tag(before: str) -> bool:
    """Слово перед номером — тег заголовка, а не предлог и не артикль.

    Тег — короткое служебное слово («Глава», «Chapter», «第», «Capítulo»,
    «Kapitel»): в нём нет пробелов и оно не входит в список предлогов.
    Одиночная буква китайской записи (「第」) под это правило попадает:
    предлогом она не является.
    """
    words = before.replace("\xa0", " ").split()
    if len(words) > 1:
        return False
    return before.strip().lower() not in _NOT_HEADER_TAG


def _starts_sentence(after: str) -> bool:
    """Остаток после номера — продолжение фразы, а не название.

    В заголовке после числа идёт разделитель («.», «:», «—») или имя
    названия, и первое слово — не предлог: «Глава 113. Использование…».
    В прозе предлог идёт сразу за числом: «в 113 году», «о 113 случаях».
    Служебные слова на любом языке берутся из _NOT_HEADER_TAG — списка
    предлогов и артиклей, а не конкретного языка.
    """
    words = after.replace("\xa0", " ").split()
    if not words:
        return False
    first = words[0].strip(_HDR_TRIM_CHARS).lower()
    return first in _NOT_HEADER_TAG


def chapter_number_of(line, number):
    """Номер главы стоит в НАЧАЛЕ строки (перед ним не больше _HDR_PREFIX символов).

    Признак заголовка, не зависящий от языка: название переводится или
    пишется на исходном, а НОМЕР остаётся числом в любой записи —
    «第113章 公器私用», «Глава 113. …», «Chapter 113: …», «Capítulo 113 — …»,
    «113 Начало». Слова-префиксы при этом короткие: на 7333 заголовках двух
    книг самый длинный — «Глава» (5 символов).

    Сверяем с ожидаемым номером, а не «любое число»: в первой строке тела
    тоже бывает число («В 1999 году…», «40K宇宙的萨卡星…»), и заголовком оно
    не становится — там перед номером целая фраза.
    """
    try:
        number = int(number)
    except (TypeError, ValueError):
        return False
    if number <= 0:
        return False
    text = str(line or "").strip()
    if not text:
        return False
    # нумерация в файле иногда отстаёт или опережает на единицу
    # («第2692章» перед 2693-й главой) — это тот же заголовок
    for candidate in (number, number - 1, number + 1):
        if candidate <= 0:
            continue
        m = re.search(rf"(?<!\d){candidate}(?!\d)", text)
        if not m:
            continue
        before = text[:m.start()].strip()
        after = text[m.end():].lstrip()
        if len(before) > _HDR_PREFIX:
            continue
        # Номер — начало строки, а перед ним только тег («Глава», «第»,
        # «Chapter»). Перед тегом стоят в начале строки короткие слова
        # любого языка, а служебное слово тега само по себе не длинное:
        # префикс ограничен _HDR_PREFIX, и прозаическая фраза перед числом
        # («В 113 году до нашей эры…») в него не влезает.
        if before and not _is_header_tag(before):
            continue
        # После номера в заголовке: знак-разделитель или название, но не
        # продолжение фразы. «В 113 году до нашей эры…» имеет предлог ПОСЛЕ
        # числа — проверяем это по служебным словам в начале остатка.
        if after and _starts_sentence(after):
            continue
        return True
    return False


def looks_like_header(line, number):
    """Похожа ли строка на заголовок главы (без привязки к языку).

    Номер в начале короткой строки: `chapter_number_of` отвечает за номер и
    его место, `_HDR_MAX_CHARS` — за то, что это заголовок, а не абзац.
    """
    text = str(line or "").strip()
    if not text or len(text) > _HDR_MAX_CHARS:
        return False
    return chapter_number_of(text, number)


def headers_lead(content, number):
    """Сколько строк с начала текста занимают заголовки (0 — их нет).

    Заголовков бывает несколько подряд: разбор исходника оставил строку из
    папки («第4章 哈莉奎茵») и приписал переведённую («第4章 哈莉·奎茵»), а
    нумерация иногда отстаёт на единицу («第2692章» перед 2693-й). Считаем
    подряд идущие строки-заголовки, пропуская пустые между ними; пустая
    строка после блока заголовков — уже начало текста.
    """
    lines = str(content or "").lstrip("\ufeff").split("\n")
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            # пустые строки внутри блока заголовков — оформление; после
            # последнего заголовка начинается текст
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and looks_like_header(lines[j], number):
                i = j
                continue
            break
        if not looks_like_header(lines[i], number):
            break
        i += 1
    return i


def strip_chapter_header(content, number=None):
    """Текст без строк-заголовков в начале.

    Признак заголовка — номер главы в короткой строке, а не слово «Глава»
    или «第”：название переводится и пишется на любом языке, а номер
    остаётся числом. Раньше шаблон знал только «Глава N» и «第N章», и у
    книги с другим языком заголовок попадал в [ОРИГИНАЛ], но не в
    [ПЕРЕВОД] — модель видела пропуск там, где заголовок просто не
    переводился.

    `number` — номер главы (для сверки). Без него заголовок не отличить
    от первой строки прозы, и текст возвращается как есть: срезать первую
    строку «на всякий случай» нельзя — у книги без заголовков это начало
    главы.
    """
    text = str(content or "").lstrip("\ufeff")
    if not text.strip() or not number:
        return text
    lines = text.split("\n")
    n = headers_lead(text, number)
    if not n:
        return text
    return "\n".join(lines[n:]).lstrip("\n")


def strip_chapter_headers(content, number=None):
    """Заголовки в начале и внутри текста (легаси: склейка глав).

    Первый блок уже срезан `strip_chapter_header`; здесь добираются
    заголовки, оставшиеся в середине у склеенных файлов. Правило то же —
    номер главы в короткой строке.
    """
    text = str(content or "").lstrip("\ufeff")
    if not text.strip() or not number:
        return text
    out = []
    for line in text.split("\n"):
        if looks_like_header(line, number):
            continue
        out.append(line)
    return "\n".join(out)


# ─────────────────────────────────────────────
# СБОР ГЛАВ
# ─────────────────────────────────────────────

def collect_chapters(start, end, file_type, budget, chapter_map, logger,
                     use_source=False):
    """Главы диапазона: (номер, файл, папка, текст, оригинал|None).

    С `use_source` рядом с переведённым файлом читается `chapter.txt` —
    пара уходит в запрос одним куском. Нет оригинала (у книги только
    перевод) — глава проверяется одна, как раньше: это не ошибка, а
    свойство книги, и о нём сообщается одной строкой итога.

    Глава, чья пара не влезает в бюджет запроса, в проверку НЕ идёт и
    пишется в лог отдельной строкой. Резать её нельзя: части одной главы
    пришлось бы проверять по отдельности, а тогда разъехавшиеся границы
    кусков (оригинал и перевод режутся независимо) дают ложные находки на
    стыках — модель видит в «своём» куске половину пары, которой в нём нет.
    Оценка
    размера — та же `estimate_tokens`, которой считается бюджет.
    """
    chapters, skipped, too_big, no_source = [], 0, [], 0
    for i in range(start, end + 1):
        paths = chapter_map.get(i)
        if not paths:
            skipped += 1
            continue
        fp, content = find_chapter_file_in_dir(paths[0], i, file_type, logger)
        if fp is None or content is None:
            skipped += 1
            continue
        source = None
        if use_source:
            src_fp, source = find_chapter_file_in_dir(paths[0], i, "chapter",
                                                      logger)
            if src_fp is None or not (source or "").strip():
                source = None
                no_source += 1
        size = estimate_tokens(content) + _tokens_of(source) + PROMPT_OVERHEAD
        if size > budget:
            too_big.append((i, size))
            logger.error(f"✗ Глава {i}: {size} ток. (оценка) — больше "
                         f"бюджета {budget}, в проверку не идёт. "
                         f"Поднимите «Бюджет запроса» до {size} и "
                         f"перезапустите только эту главу.")
            continue
        chapters.append((i, fp, paths[0], content, source))
    total = end - start + 1
    logger.info(
        f"Сбор: найдено={len(chapters)}, пропущено={skipped}, "
        f"не влезло={len(too_big)}, диапазон={total}")
    if use_source:
        logger.info(f"Сверка с оригиналом: пар="
                    f"{len(chapters) - no_source}, без оригинала={no_source} "
                    f"(проверяются только по переводу)")
    if too_big and len(too_big) <= 20:
        nums = ", ".join(str(n) for n, _ in too_big)
        logger.warning(f"⚠️ Не влезли в бюджет ({len(too_big)}): гл. {nums}")
    elif too_big:
        logger.warning(f"⚠️ Не влезли в бюджет: {len(too_big)} глав — "
                       f"поднимите бюджет или проверьте их по отдельности")
    if total > 0 and skipped / total > 0.1:
        logger.warning(f"⚠️ Пропущено {skipped}/{total} ({skipped/total:.0%})")
    return chapters


# ─────────────────────────────────────────────
# ПАКЕТЫ
# ─────────────────────────────────────────────

def build_batches(chapters, budget, logger):
    """Пакеты глав: каждая глава целиком, без разрезания на части.

    Пакет собирается только по бюджету — глав в запросе столько, сколько
    влезает, поэтому пакеты бывают разного размера. Глава либо входит
    целиком, либо начинает новый пакет, но никогда не режется пополам:
    части одной главы в разных пакетах дали бы ложные находки на стыке.
    """
    if not chapters:
        return []
    batches, cur, size = [], [], 0
    for item in chapters:
        # текст главы БЕЗ накладных: `PROMPT_OVERHEAD` — вес промпта, и он
        # один на ЗАПРОС, а не на каждую главу. Прежний подсчёт прибавлял
        # 3000 к каждой главе, и пакет закрывался на 8 главах там, где по
        # бюджету влезало 11: 24 000 токенов бюджета уходили впустую
        s = estimate_tokens(item[3]) + _tokens_of(_item_source(item))
        if cur and size + s + PROMPT_OVERHEAD > budget:
            batches.append(cur)
            cur, size = [], 0
        cur.append(item)
        size += s
    if cur:
        batches.append(cur)
    if batches:
        sizes = [len(b) for b in batches]
        # пакеты намеренно разного размера: видно, сколько глав влезает и
        # что остаток — не потерянные главы, а хвост диапазона
        span = str(min(sizes)) if min(sizes) == max(sizes) \
            else f"{min(sizes)}–{max(sizes)}"
        logger.info(f"Пакетов: {len(batches)} на {len(chapters)} глав "
                    f"(бюджет {budget} токенов, оценка; "
                    f"глав в пакете: {span})")
    return batches


def _tokens_of(text):
    """Токены текста или 0: у главы без оригинала второго текста нет."""
    return estimate_tokens(text) if text else 0


def _item_source(item):
    """Оригинал элемента батча ("" — его нет): элемент — кортеж
    (номер, файл, папка, текст[, оригинал])."""
    return (item[4] if len(item) > 4 else "") or ""


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

#: сколько символов общего контекста обязано быть у правки, чтобы
#: применять её вместо цитаты (замена внутри слова даёт контекст вокруг)
_MIN_COMMON_CONTEXT = 8


def _common_context(a: str, b: str) -> tuple:
    """Общее начало и общий конец двух строк — в символах.

    Правка отличается от цитаты только в месте ошибки, поэтому общий
    контекст есть почти всегда. Ноль с обеих сторон означает, что текст
    переписан целиком: применять его нельзя, это не адресованная правка.
    """
    a = a or ""
    b = b or ""
    limit = min(len(a), len(b))
    head = 0
    while head < limit and a[head] == b[head]:
        head += 1
    tail = 0
    while (tail < limit - head
           and a[len(a) - 1 - tail] == b[len(b) - 1 - tail]):
        tail += 1
    return head, tail


def validate_errors(errors, valid_ch, batch_contents, logger):
    """Сверка ответа LLM с фактическим ПЕРЕВОДОМ батча.

    Дрейф атрибуции (LLM указала соседнюю главу) отсекается здесь: цитата
    обязана найтись в заявленной главе (точно или типографически-мягко —
    flex_fragment_pattern), иначе правка перепривязывается на единственную
    главу батча с совпадением; ни одна — отклонение.

    Сверяемся ТОЛЬКО с переводом: оригинал в батче идёт рядом, но правка
    адресуется файлу перевода, и цитата, случайно совпавшая с оригиналом
    (латиница, цифры, имена), была бы неверной находкой.

    Цитата обязана быть УНИКАЛЬНОЙ в главе: замена адресуется ею, и второе
    вхождение в файле — это уже не адрес.

    Правка — замена внутри найденной цитаты: стадия не вставляет и не
    удаляет текст. `corrected == fragment` отбрасывается: применять его
    нечем, а «здесь пропуск» — это не находка, а догадка о невидимом.
    """
    out, dropped, moved = [], 0, 0
    for e in errors:
        ch = _to_int(e.get("chapter"))
        if ch is None:
            continue
        frag, corr = e.get("fragment", ""), e.get("corrected", "")
        if not frag or len(frag) < 10 or not corr:
            continue
        # Тип вне списка находок — устаревший промпт или догадка модели:
        # вставлять и удалять текст стадия больше не умеет, и такая запись
        # не должна попасть ни в правки, ни человеку как «пропуск».
        etype = str(e.get("type") or "").strip().lower()
        if etype and etype not in FIX_ERROR_TYPES:
            dropped += 1
            continue
        if frag.strip() == corr.strip():
            dropped += 1
            continue
        cont = batch_contents.get(ch, "")
        if not (cont and _frag_in_text(frag, cont)):
            found = [oc for oc, ocont in batch_contents.items()
                     if ocont and _frag_in_text(frag, ocont)]
            if len(found) != 1:
                dropped += 1
                continue
            moved += 1
            logger.info(f"  ↪ Цитата не в гл.{ch}, а в гл.{found[0]} — "
                        f"правка перепривязана.")
            ch = found[0]
            cont = batch_contents.get(ch, "")
        e["chapter"] = ch
        # уникальность: замена адресуется цитатой
        if fragment_occurrences(cont, frag) > 1:
            dropped += 1
            continue
        # Правка обязана делить с цитатой общий контекст: модель, переписавшая
        # кусок своими словами, общего контекста не даёт, и подстановка её
        # текста заменила бы часть главы чужим.
        head, tail = _common_context(frag, corr)
        if head + tail < _MIN_COMMON_CONTEXT:
            dropped += 1
            logger.info(f"  ⚠ Гл.{ch}: правка переписана целиком — "
                        f"контекст {head + tail} симв., отклонена")
            continue
        out.append(e)
    if dropped:
        logger.info(f"Валидация: отклонено {dropped}/{len(errors)}")
    if moved:
        logger.info(f"Валидация: перепривязано {moved} правок на "
                    f"реальные главы.")
    return out


# ─────────────────────────────────────────────
# ОБРАБОТКА ПАКЕТА
# ─────────────────────────────────────────────

def compose_batch_text(batch) -> str:
    """Текст батча для LLM: заголовок «=== Глава N ===» + текст. С
    оригиналом — пара блоков `[ОРИГИНАЛ]` и `[ПЕРЕВОД]` одной главы.
    Используется и в предпросмотре запроса.

    Заголовок главы вырезан из обоих текстов одинаково и по одному
    признаку — номер главы в короткой строке (`strip_chapter_header`):
    название переводится свободно, и его расхождение с оригиналом — не
    ошибка, а разный язык записи заголовка давал в паре [ОРИГИНАЛ] с
    заголовком и [ПЕРЕВОД] без него.
    Глава в батче всегда целиком: нарезки на части нет, поэтому и нумерации
    частей здесь нет.
    """
    parts = []
    for c in batch:
        cleaned = strip_chapter_header(c[3], c[0])
        source = strip_chapter_header(_item_source(c), c[0])
        hdr = f"=== Глава {c[0]} ==="
        if source and source.strip():
            body = (f"{hdr}\n[ОРИГИНАЛ]\n{source.strip()}\n\n"
                    f"[ПЕРЕВОД]\n{cleaned.strip()}")
        else:
            body = f"{hdr}\n{cleaned}"
        parts.append(body)
    return "\n\n".join(parts)


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


def _apply_fail_note(text, e) -> str:
    """Почему правка не легла: «не найден» и «формат не совпал» — разные
    причины, и вторая означает, что модель расставила переносы иначе.

    Наличие фрагмента проверяется МЯГКО (`flex_fragment_pattern`): правка
    отклонена именно там, где мягкий поиск его нашёл, но форматирование
    разошлось. Проверять точное вхождение бессмысленно — до отказа
    `apply_flex_fix` дошёл только потому, что точного совпадения не было.
    """
    frag = unicodedata.normalize("NFC", str(e.get("old") or ""))
    if not frag:
        return "пустая цитата"
    try:
        found = re.search(flex_fragment_pattern(frag),
                          unicodedata.normalize("NFC", text))
    except re.error:
        found = None
    if found:
        return ("переносы строк в правке не совпали с файлом — не применяю, "
                "чтобы не склеить абзацы")
    return "фрагмент не найден в файле"


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
            e["note"] = _apply_fail_note(text, e)
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
Единицы: --request_budget — ТОКЕНЫ (оценка estimate_tokens); max_tokens —
серверный предохранитель, ТОКЕНЫ.
Сервер, модель, ключ, таймауты, повторы, потоки и рассуждения — ОДНИ на весь
конвейер: флаг стадии пуст → значение из общего .env (или профиля LLM).
Промпт-файл: один тег <prompt_translate_check>; без него — встроенный промпт.
Сверка с оригиналом: к каждой главе подмешивается её chapter.txt (блоки
[ОРИГИНАЛ]/[ПЕРЕВОД]), правки берутся только из перевода.
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
    ap.add_argument(
        "--request_budget", type=int,
        default=int(core_settings.BY_KEY[
            "TRANSLATE_CHECK_LLM_REQUEST_BUDGET"].default),
        help="Бюджет запроса, ТОКЕНЫ (оценка; промпт — запас 3000; "
             "по умолчанию: "
             f"{core_settings.BY_KEY['TRANSLATE_CHECK_LLM_REQUEST_BUDGET'].default}).")
    ap.add_argument("--use-source", dest="use_source", default=True,
                    action=argparse.BooleanOptionalAction,
                    help="Сверять с оригиналом: к главе подмешивается её "
                         "chapter.txt парой [ОРИГИНАЛ]/[ПЕРЕВОД]; правки — "
                         "только из перевода (пусто = "
                         "TRANSLATE_CHECK_LLM_USE_SOURCE).")
    ap.add_argument("--retry_empty", type=int, default=0, metavar="N",
                    help="Повторов при отсутствии правок, ШТ (0 = нет; пусто = "
                         "TRANSLATE_CHECK_LLM_RETRY_EMPTY). Ответ без ошибок "
                         "— не ошибка, а повод спросить ещё раз.")
    ap.add_argument("--prompt_file", default=DEFAULT_PROMPT_FILE,
                    help="Внешний промпт: тег <prompt_translate_check>.")
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
        f"оригинал={'да' if args.use_source else 'нет'} | "
        f"threads={stage.profile.threads} | "
        f"retry_empty={args.retry_empty}")

    prompt = load_prompt(args.prompt_file, logger)
    if args.use_source and SOURCE_PROMPT_MARK not in prompt:
        logger.warning("⚠️ В промпте нет блока сверки с оригиналом "
                       "([ОРИГИНАЛ]/[ПЕРЕВОД], примечания автора) — "
                       "устаревший промпт; встроенные правила не подставлены")

    logger.info(f"Лог: {stage.log_path}")

    chapters = collect_chapters(args.start, args.end, args.type,
                                args.request_budget, chapter_map, logger,
                                use_source=args.use_source)
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
                          "use_source": bool(args.use_source),
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
              "сверка с оригиналом": "да" if args.use_source else "нет",
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
        # сверка идёт ТОЛЬКО по тексту перевода: оригинал в батче есть,
        # но адрес правки — файл перевода, и цитата, случайно совпавшая
        # с оригиналом, была бы неверной находкой (см. validate_errors)
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

    # верхний предел потоков 16 (как в ner/wiki/translate). Прогресс
    # создаётся ДО запуска потоков: иначе первый пакет успевал напечатать
    # «Пакет 1/154» и отдать правки раньше, чем прогресс объявлял 0/154 —
    # в ленте это читалось как откат счётчика (1/154 → 0/154)
    with ThreadPoolExecutor(max_workers=stage.profile.threads) as ex:
        with Progress(len(batches), "Проверка перевода LLM", unit="батч",
                      bar=True, logger=logger) as progress:
            futs = {ex.submit(worker, i, b): i for i, b in enumerate(batches)}
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


#: фразы, без которых внешний промпт устарел по смыслу (теги на месте,
#: а правил сверки нет): попадают в notes вердикта --check-prompt
PROMPT_MARKS: tuple = (
    (SOURCE_PROMPT_MARK,
     "в промпте нет блоков [ОРИГИНАЛ]/[ПЕРЕВОД] — правила сверки с "
     "оригиналом (пропуски, примечания автора) не действуют, пока "
     "включено «Сверять с оригиналом»"),
)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # --check-prompt: вердикт о промпт-файле до логов, чтения глав и сети
    if check_prompt(args, "translate_check_llm", PROMPT_CONTRACT,
                    marks=PROMPT_MARKS):
        return 0
    # лог стадии — всегда; профиль LLM нужен только режиму проверки
    stage = new_stage("translate_check_llm", args, log_fatal=False)
    if args.apply:
        return do_apply(args, stage.logger)
    return do_check(args, bind_profile(stage, args))


if __name__ == "__main__":
    sys.exit(main())

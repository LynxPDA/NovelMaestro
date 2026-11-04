#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline.py — web-оркестратор конвейера (стадия pipeline).

Запускается через JobManager как subprocess (cwd = папка проекта).
По главам [start..end] гоняет cli/translate_book.py
(--mode translate|redact|polish), fail-fast (код 0 + непустой выход +
grep слов-ошибок), ThreadPoolExecutor(jobs).

События глав пишутся в stdout строками:
  @@CHAPTER@@ {"type":"chapter","id":3,"stage":1,"status":"OK"}
JobManager._reader парсит их в job.events → SSE и payload. Строку пишет
core.common.emit_line — одним `write`: stdout в web-режиме делят лог
запуска и события глав из потоков пула, и печать через `print()` (текст и
«\\n» двумя вызовами) отдавала событие в рваном виде.

Никакого интерактива: только argparse (те же правила, что cli/).
"""
import argparse
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# ═══ Bootstrap core (копия паттерна §4 AGENTS.md) ═══
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
from core import settings as core_settings  # noqa: E402
from core.common import (  # noqa: E402
    PROGRESS_PREFIX, build_chapter_map, emit_line, format_ranges, output_tail,
    stage_call_timeout, stage_log_path,
)
# общий блок LLM-флагов: у конвейера тот же набор имён, что у стадий
from core.stage import (add_check_prompt_arg, add_llm_args,  # noqa: E402
                     check_prompt, resolve_profile)

# ═══ Константы (канон run_pipeline.py) ═══
_STAGE_IO = {
    1: ("chapter.txt", "translated.txt"),
    2: ("translated_trace.json", "redacted.txt"),
    3: ("redacted.txt", "polished.txt"),
}
_STAGE_NAME = {1: "translate", 2: "redact", 3: "polish"}
#: человекочитаемые фазы для web-прогресса (те же имена, что у MODE_LABELS
#: translate_book): бар читает человек, а «Глава 2 · redact · Редактура» —
#: два названия одной фазы подряд
_STAGE_LABEL = {1: "Перевод", 2: "Редактура", 3: "Полировка"}
# Типы работ: стадии конвейера + откуда полировка берёт исходник.
# polish_input: "translated.txt" — полировка прямо из перевода (без редактуры);
# не задан — дефолт _STAGE_IO[3] = redacted.txt.
_ACTION_SPECS: dict[int, dict] = {
    1: {"stages": [1], "name": "Перевод"},
    2: {"stages": [2], "name": "Редактура (исходник - Перевод)"},
    3: {"stages": [3], "name": "Полировка (исходник - Редактура)"},
    4: {"stages": [3], "name": "Полировка (исходник - Перевод)",
        "polish_input": "translated.txt"},
    5: {"stages": [1, 2],
        "name": "Сокращенный цикл: Перевод -> Редактура"},
    6: {"stages": [1, 3],
        "name": "Сокращенный цикл: Перевод -> Полировка",
        "polish_input": "translated.txt"},
    7: {"stages": [2, 3],
        "name": "Сокращенный цикл: Редактура -> Полировка"},
    8: {"stages": [1, 2, 3],
        "name": "Полный цикл: Перевод -> Редактура -> Полировка"},
}
# Дефолты конвейера — из реестра настроек core/settings.py: стадийные поля
# (PIPELINE_*) плюс общие LLM-ключи (HOST/MODEL/THREADS/TIMEOUT/...). Порядок
# слоёв один на весь проект: реестр → общий .env → os.environ. Своего словаря
# дефолтов и своего чтения .env здесь больше нет.
_DEFAULTS = core_settings.stage_values("pipeline")
_DEFAULTS.update(core_settings.llm_values())
# поведение оставлено как было: текст перевода НЕ попадает в stdout
# скриптов (только прогресс/ошибки), лишняя настройка PIPELINE_ERROR_WORDS
# отменена по решению пользователя; жёсткий список ловит реальные сбои.
_ERROR_RE = re.compile(
    r"traceback|exception|error|warning|fatal|fail(?:ed)?(?:\s+to)?",
    re.IGNORECASE,
)
# Префикс логгера "2026-01-01 12:00:00 - WARNING - …": имя уровня
# матчит _ERROR_RE, но это не ошибка стадии.
_LOGGER_PREFIX_RE = re.compile(
    r"^.*? - (?:DEBUG|INFO|WARNING|ERROR|CRITICAL) - ",
    re.IGNORECASE,
)
_SYM = {"OK": "✓", "ERROR": "✗", "SKIP": "⊘"}
CHAPTER_PREFIX = "@@CHAPTER@@"
#: шаг сторожа стадии, СЕК: как часто смотреть, жива ли стадия (она
#: отмечается строкой «ответ идёт», см. core.common._Heartbeat)
WATCHDOG_TICK = 60.0
# промпт-файлы конвейера — только единый общий файл с тегами
# <translate>/<redact>/<polish> (отдельные файлы на стадию убраны).
# Кандидаты «одного файла с тегами» для режима auto.
_PROMPT_COMBINED_CANDIDATES = [
    "prompts/pipeline_prompt.txt",
    "prompts/prompts.txt",
    "prompts/translate_book_prompt.txt",
]
# теги стадий конвейера: файл с ними авто-подхватывается и не считается
# «без тега стадии»
_PROMPT_TAGS = ("translate", "redact", "polish")


def emit(ev: dict) -> None:
    """Событие конвейера → stdout одной строкой (парсит JobManager)."""
    emit_line(CHAPTER_PREFIX, ev)


def emit_progress(done: int, total: int, label: str = "") -> None:
    """Общий прогресс конвейера → stdout (JobManager → SSE).

    done/total — завершённые стадии от общего числа (главы × стадии),
    а не чанки одной главы: полоска в web растёт по главам, детали
    текущего чанка — в label."""
    emit_line(PROGRESS_PREFIX,
              {"type": "progress", "done": done, "total": total,
               "label": label})


# ═══ Трекер (потокобезопасный, web-вариант) ═══
class Tracker:
    def __init__(self, chapter_ids: list[int], stages: list[int]) -> None:
        self._lock = threading.Lock()
        self._stages = stages
        self._status: dict[int, dict[int, str]] = {c: {} for c in chapter_ids}
        self._not_found: set[int] = set()

    def mark_not_found(self, cid: int) -> None:
        with self._lock:
            self._not_found.add(cid)

    def record(self, cid: int, stage: int, status: str) -> None:
        with self._lock:
            self._status.setdefault(cid, {})[stage] = status

    def snapshot(self) -> dict:
        full, partial, failed, pending = [], [], [], []
        with self._lock:
            for cid, st in self._status.items():
                if not st:
                    pending.append(cid)
                elif any(v == "ERROR" for v in st.values()):
                    failed.append(cid)
                elif (len(st) == len(self._stages)
                      and all(v == "OK" for v in st.values())):
                    full.append(cid)
                else:
                    partial.append(cid)
        return {"full": sorted(full), "partial": sorted(partial),
                "failed": sorted(failed), "pending": sorted(pending),
                "not_found": sorted(self._not_found)}

    def overall(self) -> tuple[int, int]:
        """Общий прогресс: (завершённых стадий, всего стадий).

        done — стадии со статусом OK/SKIP/ERROR; total — главы × стадии.
        NOT_FOUND не входит (глава без папки — не работа)."""
        with self._lock:
            total = len(self._status) * len(self._stages)
            done = sum(
                1 for st in self._status.values()
                for v in st.values() if v in ("OK", "SKIP", "ERROR")
            )
            return done, total

    def progress_str(self) -> str:
        s = self.snapshot()
        total = sum(len(v) for v in
                    (s["full"], s["partial"], s["failed"], s["pending"]))
        parts = [f"{len(s['full'])}/{total} завершено "
                 f"[{format_ranges(s['full'])}]"]
        if s["partial"]:
            parts.append(f"{len(s['partial'])} частично "
                         f"[{format_ranges(s['partial'])}]")
        if s["failed"]:
            parts.append(f"{len(s['failed'])} ошибка "
                         f"[{format_ranges(s['failed'])}]")
        if s["pending"]:
            parts.append(f"{len(s['pending'])} в очереди")
        return " │ ".join(parts)

    def report(self) -> list[str]:
        s = self.snapshot()
        total = sum(len(v) for v in
                    (s["full"], s["partial"], s["failed"], s["pending"]))
        lines = ["═" * 60, "  ИТОГИ ЗАПУСКА", "═" * 60]
        lines.append(f"  Полностью завершено : {len(s['full']):>3} / {total}"
                     f"   [{format_ranges(s['full'])}]")
        lines.append(f"  Частично пройдено   : {len(s['partial']):>3} / {total}"
                     f"   [{format_ranges(s['partial'])}]")
        lines.append(f"  Ошибка              : {len(s['failed']):>3} / {total}"
                     f"   [{format_ranges(s['failed'])}]")
        if s["pending"]:
            lines.append(f"  Не обработано       : {len(s['pending']):>3} / "
                         f"{total} [{format_ranges(s['pending'])}]")
        if s["not_found"]:
            lines.append(f"  Папка не найдена    : {len(s['not_found']):>3}"
                         f"   [{format_ranges(s['not_found'])}]")
        lines.append("─" * 60)
        incomplete = []
        for cid, st in sorted(self._status.items()):
            is_full = (len(st) == len(self._stages)
                       and all(v == "OK" for v in st.values()))
            if not is_full:
                incomplete.append((cid, st))
        for cid in sorted(self._not_found):
            incomplete.append((cid, {}))
        if incomplete:
            lines.append("  Детали неполных глав:")
            for cid, st in sorted(incomplete):
                if not st:
                    lines.append(f"    Глава {cid:03d}: папка не найдена")
                    continue
                parts = []
                for stage in self._stages:
                    name = _STAGE_NAME[stage]
                    if stage in st:
                        parts.append(f"[{name} {_SYM.get(st[stage], '?')} "
                                     f"{st[stage]}]")
                    else:
                        parts.append(f"[{name} —]")
                lines.append(f"    Глава {cid:03d}: {' '.join(parts)}")
        else:
            lines.append("  Все главы завершены полностью.")
        lines.append("═" * 60)
        return lines


# ═══ Команда translate_book.py ═══
def resolve_prompt_paths(combined: str = "") -> dict[int, str]:
    """Пути промпт-файлов по стадиям : явный файл > авто.

    combined — один файл на все стадии (теги <translate>/<redact>/<polish>);
    пусто — auto: первый кандидат с тегами в prompts/ → все стадии;
    иначе — пустые пути (стадии используют встроенные промпты).
    Пути относительные — cwd = папка проекта.
    """
    out: dict[int, str] = {}
    if combined:
        for stage in _STAGE_NAME:
            out[stage] = combined
        return out
    # auto: ищем файл с тегами (combined-режим)
    from core.common import get_tagged_prompt
    for cand in _PROMPT_COMBINED_CANDIDATES:
        p = Path(cand)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(get_tagged_prompt(text, tag) for tag in _PROMPT_TAGS):
            for stage in _STAGE_NAME:
                out[stage] = str(p)
            return out
    # иначе — стадии используют встроенные промпты translate_book.py
    for stage in _STAGE_NAME:
        out[stage] = ""
    return out


# тег стадии в общем промпт-файле
_STAGE_TAG = {1: "translate", 2: "redact", 3: "polish"}
#: что читает конвейер из общего промпт-файла: (тег, где читается,
#: переменные тега); проверяются ВСЕ стадии — отчёт обязан быть честным
#: про любой тип работы, а не только про выбранный в форме
PROMPT_CONTRACT: tuple = (
    ("translate", "тип работы «Перевод»", ("original_text", "ner_block")),
    ("redact", "тип работы «Редактура»",
     ("original_text", "translated_text", "ner_block")),
    ("polish", "тип работы «Полировка»",
     ("original_text", "female_names", "male_names")),
)


def warn_missing_prompt_tag(prompt_file: str, stage: int, log) -> bool:
    """Предупреждение, если промпт-файл не содержит тега стадии.

    Промпт стадии читается только из своего тега: файл без него — не «промпт
    целиком», стадия уходит на встроенный промпт (иначе в запрос одной стадии
    попадала бы задача другой).
    Возвращает True, если тег отсутствует (стадия на встроенном).
    """
    if not prompt_file:
        return True
    path = Path(prompt_file)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    from core.common import get_tagged_prompt
    tags = (_STAGE_TAG[stage],)
    if any(get_tagged_prompt(text, t) for t in tags):
        return False
    log.warning("В промпт-файле %s нет тега %s — стадия «%s» "
                "использует ВСТРОЕННЫЙ промпт",
                prompt_file, "/".join(f"<{t}>" for t in tags),
                _STAGE_NAME[stage])
    return True


def build_stage_cmd(stage: int, script: Path, in_file: Path, out_file: Path,
                    host: str, api_key: str, model: str,
                    timeout: int, temperature=None,
                    stream_timeout=None, max_retries=None,
                    threads: int = 1, prompt_file: str = "",
                    ner_min_count: int = 0,
                    names_min_count: int = 10,
                    ner_fields: str = "term,translation,type",
                    request_budget: int = 0,
                    chunk_size: int | None = None) -> list[str]:
    # модель, сервер и ключ конвейера — одни на все стадии (translate,
    # redact, polish), стадийных ключей больше нет
    common = [
        "--host", host,
        "--model", model,
        "--threads", str(threads),
        "--timeout", str(timeout),
        # стрим — своё число (общий STREAM_TIMEOUT), а не таймаут соединения
        "--stream_timeout", str(stream_timeout),
        "--max_retries", str(max_retries if max_retries is not None
                              else _DEFAULTS["max_retries"]),
        "--out", str(out_file),
        # глоссарий — всегда канонический ner.json (выбор файла убран)
        "--ner_file", "ner.json",
    ]
    # ключ не попадает в argv — он уходит в окружение процесса
    # (LLM_API_KEY), см. process_chapter
    if temperature is not None:
        common += ["--temperature", str(temperature)]
    # рассуждения (mode/profile/effort/budget) сюда не прокидываются:
    # они ОБЩИЕ на конвейер и каждая стадия читает их сама из .env
    # общий промпт-файл — только если задан (пусто = встроенный)
    if prompt_file:
        common += ["--prompt_file", prompt_file]
    # пороги count: ner_block и имена (дефолты: 0 — выключено, 10);
    # поля {ner_block} — выбор из формы: aliases в блок попадают только
    # когда поле выбрано в чипсах формы
    common += ["--ner_min_count", str(ner_min_count),
               "--names_min_count", str(names_min_count),
               "--ner_fields", str(ner_fields)]
    # общий потолок запроса (ТОКЕНЫ, оценка) — действует во всех режимах:
    # перевод, редактура, полировка
    ext_extra = []
    if request_budget:
        ext_extra += ["--request_budget", str(request_budget)]
    # размер чанка перевода/полировки (ТОКЕНЫ): форма > дефолт
    # (_DEFAULTS["chunk_size"] ← PIPELINE_CHUNK_SIZE из .env);
    # редактура идёт главой целиком — стадии 2 чанк не нужен
    cs = str(chunk_size if chunk_size else _DEFAULTS["chunk_size"])
    if stage == 1:
        return [sys.executable, str(script), str(in_file), "--mode",
                "translate", *common,
                "--chunk_size", cs,
                "--ner_threshold", str(_DEFAULTS["ner_threshold"]),
                "--ner_ngram", str(_DEFAULTS["ner_ngram"]), *ext_extra]
    if stage == 2:
        return [sys.executable, str(script), str(in_file), "--mode",
                "redact", *common,
                "--ner_threshold", str(_DEFAULTS["ner_threshold"]),
                "--ner_ngram", str(_DEFAULTS["ner_ngram"]), *ext_extra]
    return [sys.executable, str(script), str(in_file), "--mode",
            "polish", *common,
            "--chunk_size", cs,
            "--ner_threshold", str(_DEFAULTS["ner_threshold"]),
            "--ner_ngram", str(_DEFAULTS["ner_ngram"]), *ext_extra]


def chapters_in_range(chapters: dict, start: int, end: int) -> dict:
    """Главы диапазона по возрастанию номера — порядок подачи в пул.

    Порядок ключей здесь — расписание работы: однопоточный обход `with
    ThreadPoolExecutor` берёт задачи в порядке поданных futures. Поэтому он не
    наследуется от карты, а задаётся явно. Алфавитный порядок имён папок
    ставит «00000_100…» и «00000_10…» раньше «00000_1_…» («0» < «_»): на
    боевой книге 16 потоков брали главы 100–114, а первые ждали конца книги.
    """
    return {cid: [Path(p) for p in chapters[cid]]
            for cid in sorted(chapters) if start <= cid <= end}


def grep_errors(text: str, max_lines: int = 20) -> list[str]:
    """Строки с признаками ошибок. Имя уровня логгера (- WARNING -)
    отрезается до матчинга — иначе любое предупреждение валит главу."""
    hits = []
    for line in text.splitlines():
        body = _LOGGER_PREFIX_RE.sub("", line, count=1)
        if _ERROR_RE.search(body):
            hits.append(line.rstrip())
            if len(hits) >= max_lines:
                break
    return hits


def _ts() -> str:
    """Штамп времени в том же формате, в каком пишет логгер стадии."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


#: слова, которыми ошибка модели передаётся человеку из хвоста вывода
_REASON_WORDS = ("exceeded", "cut by", "timeout", "таймаут", "loop",
                 "interrupted", "попыток", "ошибка", "error")


def _reason_from(tail: list[str], code: int) -> str:
    """Причина сбоя стадии одной строкой — из хвоста её вывода.

    Хвост читает человек в общем логе, а клетке таблицы глав и событию
    запуска нужна одна фраза: иначе клетка «✗» отвечает «что-то
    случилось». Причины LLM («4 попытки: Read timeout (900s)», «Cut by
    max_tokens») лежат последними строками — их и берём."""
    for line in reversed(tail):
        body = _LOGGER_PREFIX_RE.sub("", line, count=1).strip()
        body = body.lstrip("│ ").strip()
        if not body:
            continue
        if any(w in body.lower() for w in _REASON_WORDS):
            return body[:300]
    return f"стадия завершилась с кодом {code}"


def _stamp(path: Path) -> tuple:
    """Отпечаток файла (размер, mtime): менялся ли он, пока ждал родитель."""
    try:
        st = path.stat()
    except OSError:
        return (0, 0)
    return (st.st_size, st.st_mtime_ns)


def process_chapter(chapter_id: int, dirs: list[Path], script: Path,
                    stages: list[int], host: str, api_key: str, model: str,
                    timeout: int, log: logging.Logger,
                    tracker: Tracker,
                    temperature=None,
                    stream_timeout=None, max_retries=None,
                    threads: int = 1,
                    prompts: dict[int, str] | None = None,
                    polish_in: str | None = None,
                    ner_min_count: int = 0,
                    names_min_count: int = 10,
                    ner_fields: str = "term,translation,type",
                    request_budget: int = 0,
                    chunk_size: int | None = None,
                    stage_timeout: float | None = None) -> bool:
    """Одна глава: стадии по порядку, fail-fast (код 0 + файл + grep).

    polish_in — вход полировки вместо дефолтного redacted.txt
    (полировка из перевода: действия 4 и 6).
    stage_timeout — предел ОДНОЙ стадии, СЕК (core.common.stage_call_timeout);
    пусто — считается из timeout/stream_timeout стадии."""
    if stage_timeout is None:
        stage_timeout = stage_call_timeout(timeout, stream_timeout, max_retries)

    def _record(status: str, reason: str = "") -> None:
        """Запись статуса стадии + событие главы + общий прогресс.

        reason — почему не OK (причина словами): клетка таблицы глав
        подсказывает её человеку, иначе клетка «✗» отвечает «что-то
        случилось» и заставляет искать причину в двух файлах логов.
        """
        tracker.record(chapter_id, stage, status)
        ev = {"type": "chapter", "id": chapter_id, "stage": stage,
              "status": status}
        if reason:
            ev["reason"] = reason
        emit(ev)
        done, total = tracker.overall()
        emit_progress(done, total,
                      f"Глава {chapter_id} · {_STAGE_LABEL[stage]}")

    def _stage_note(out_file: Path, lines: list[str]) -> str:
        """Дописать причину в лог стадии.

        Лог стадии — единственное место, где человек ищет, что случилось с
        главой; снятая стадия до сих пор оставляла там строку о старте и
        тишину: причина была только в общем логе запуска. Файла нет — не
        ошибка: стадия могла не дойти до него (нет папки logs/).
        """
        path = Path(stage_log_path(out_file))
        try:
            with open(path, "a", encoding="utf-8") as fh:
                for line in lines:
                    fh.write(line + "\n")
        except OSError:
            pass
        return path.name

    # построчное чтение вывода стадии: метка (`head`) и приёмник (`sink`)
    # приходят аргументами — иначе функция, определённая внутри цикла по
    # стадиям, замыкалась бы на переменные цикла
    def _drain(stream, sink: list[str], head: str) -> None:
        if stream is None:
            return
        for line in stream:
            if line.startswith(PROGRESS_PREFIX):
                try:
                    ev = json.loads(line[len(PROGRESS_PREFIX):].strip())
                    label = head
                    # фаза в head уже русская и та же самая: подпись чанка
                    # («Перевод») ничего не добавляет, дубль в бар не едет
                    if ev.get("label") and ev["label"] not in label:
                        label += f" · {ev['label']}"
                    # полоска — ОБЩИЙ прогресс конвейера (главы ×
                    # стадии), а не чанки одной главы; детальный
                    # label чанка сохраняется в тексте бара
                    done, total = tracker.overall()
                    emit_progress(done, total, label)
                except Exception:
                    # кривая строка прогресса — в лог как есть, ОДНИМ `write`:
                    # `print(line, end="")` пишет текст и «\n» двумя
                    # вызовами, и лог-строка соседнего потока вклинивалась
                    # между ними — строки слипались
                    sys.stdout.write(line.rstrip("\n") + "\n")
                    sys.stdout.flush()
                continue
            sink.append(line)

    for chapter_dir in dirs:
        for stage in stages:
            name = _STAGE_NAME[stage]
            in_name, out_name = _STAGE_IO[stage]
            if stage == 3 and polish_in:
                in_name = polish_in
            in_file = chapter_dir / in_name
            out_file = chapter_dir / out_name
            if not in_file.is_file():
                log.warning("Глава %03d | [%s] Пропущена (нет исходника: %s)",
                            chapter_id, name, in_name)
                _record("SKIP")
                return True
            cmd = build_stage_cmd(stage, script, in_file, out_file,
                                  host, api_key, model, timeout,
                                  temperature,
                                  stream_timeout, max_retries,
                                  threads,
                                  prompt_file=(prompts or {}).get(stage) or "",
                                  ner_min_count=ner_min_count,
                                  names_min_count=names_min_count,
                                  ner_fields=ner_fields,
                                  request_budget=request_budget,
                                  chunk_size=chunk_size)
            proc_env = dict(os.environ)
            if api_key:
                proc_env["LLM_API_KEY"] = api_key
            # stdout потомков — строго utf-8 (на Windows иначе cp1251 →
            # ромбы в логе; см. PYTHONIOENCODING в web/jobs.py)
            proc_env.setdefault("PYTHONIOENCODING", "utf-8")
            try:
                # Popen + построчное чтение — строки @@PROGRESS@@
                # потомка ретранслируются в свой stdout (JobManager пробросит
                # их в SSE), остальной вывод копится в буфер для grep_errors.
                # subprocess.run(capture_output=True) глотал прогресс целиком.
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=proc_env, bufsize=1,
                    encoding="utf-8", errors="replace",
                )
            except OSError as exc:
                log.error("Глава %03d | [%s] Не удалось запустить: %s",
                          chapter_id, name, exc)
                _stage_note(out_file, [
                    f"{_ts()} - ERROR - ❌ Стадия не запустилась: {exc}"])
                _record("ERROR", f"стадия не запустилась: {exc}")
                return False
            out_lines: list[str] = []
            err_lines: list[str] = []
            head = f"Глава {chapter_id} · {_STAGE_LABEL[stage]}"

            tout = threading.Thread(
                target=_drain, args=(proc.stdout, out_lines, head))
            terr = threading.Thread(
                target=_drain, args=(proc.stderr, err_lines, head))
            tout.start()
            terr.start()
            started = time.monotonic()
            # сторож смотрит на лог стадии, а не только на время: живой,
            # но долгий ответ отмечается строкой «ответ идёт»
            # (core.common._Heartbeat), поэтому до предела не доходит, а
            # зависшая стадия снимается через stage_timeout
            stage_log = Path(stage_log_path(out_file))
            last_stamp = _stamp(stage_log)
            last_change = started
            quiet = 0.0
            killed = False
            while True:
                try:
                    proc.wait(timeout=WATCHDOG_TICK)
                    break
                except subprocess.TimeoutExpired:
                    pass
                now = time.monotonic()
                stamp = _stamp(stage_log)
                if stamp != last_stamp:
                    last_stamp, last_change = stamp, now
                quiet = now - last_change
                if quiet < stage_timeout:
                    continue
                killed = True
                break
            if killed:
                proc.kill()
                tout.join()
                terr.join()
                limit = int(stage_timeout)
                log.error("Глава %03d | [%s] ПРЕДЕЛ СТАДИИ: тишина %d с "
                          "(лимит %d с) — процесс снят",
                          chapter_id, name, int(quiet), limit)
                _stage_note(out_file, [
                    f"{_ts()} - ERROR - ❌ Стадия снята: тишина "
                    f"{int(quiet)} с при лимите {limit} с",
                    f"{_ts()} - ERROR - ❌ Причина снятия — молчание "
                    f"стадии, а не обрыв файла: ни одной строки "
                    f"«ответ идёт» за это время",
                ])
                _record("ERROR", f"тишина {int(quiet)} с "
                                 f"(лимит {limit} с)")
                return False
            tout.join()
            terr.join()
            combined = "".join(out_lines) + "\n" + "".join(err_lines)
            elapsed = int(time.monotonic() - started)
            if proc.returncode != 0:
                log.error("Глава %03d | [%s] ОШИБКА (код %d)",
                          chapter_id, name, proc.returncode)
                tail = list(combined.strip().splitlines()[-10:])
                for line in tail:
                    log.error("  │ %s", line)
                _stage_note(out_file, [
                    f"{_ts()} - ERROR - ❌ Стадия завершилась с кодом "
                    f"{proc.returncode} за {elapsed} с"]
                    + [f"{_ts()} - ERROR -   │ {line}" for line in tail])
                _record("ERROR", _reason_from(tail, proc.returncode))
                return False
            if not out_file.is_file() or out_file.stat().st_size == 0:
                log.error("Глава %03d | [%s] ОШИБКА (файл %s не создан)",
                          chapter_id, name, out_name)
                _stage_note(out_file, [
                    f"{_ts()} - ERROR - ❌ Стадия вернула успех, но файл "
                    f"{out_name} пуст или не создан"])
                _record("ERROR", f"файл {out_name} не создан")
                return False
            error_lines = grep_errors(combined)
            if error_lines:
                log.error("Глава %03d | [%s] СЛОВА-ОШИБКИ В ВЫВОДЕ:",
                          chapter_id, name)
                for line in error_lines:
                    log.error("  │ %s", line)
                _stage_note(out_file, [
                    f"{_ts()} - ERROR - ❌ Слова-ошибки в выводе стадии:"]
                    + [f"{_ts()} - ERROR -   │ {line}" for line in error_lines])
                _record("ERROR", _reason_from(error_lines,
                                              proc.returncode))
                return False
            _record("OK")
            log.info("Глава %03d | [%s] OK", chapter_id, name)
    return True


# ═══ main ═══
def main() -> None:
    ap = argparse.ArgumentParser(description="Web-оркестратор конвейера")
    ap.add_argument("--action", type=int, required=True,
                    choices=list(_ACTION_SPECS),
                    help="Тип работы: 1=перевод, 2=редактура (из перевода), "
                         "3=полировка (из редактуры), 4=полировка (из перевода), "
                         "5=перевод→редактура, 6=перевод→полировка, "
                         "7=редактура→полировка, 8=полный цикл")
    ap.add_argument("--start", type=int, default=None, help="Начальная глава")
    ap.add_argument("--end", type=int, default=None, help="Конечная глава")
    # Сервер LLM, таймауты, повторы, потоки и рассуждения — общий блок
    # флагов (core.stage.add_llm_args): имена канонические, --jobs и --retries
    # принимаются как старые написания. Потоки — СУММАРНО: распределение на
    # параллельные главы и чанки внутри главы считает сам pipeline.py.
    # Рассуждения — НЕ параметр конвейера: режим один на весь запуск, его
    # читают стадии сами из общего конфига.
    add_llm_args(ap, aliases=True)
    # проверка промпт-файла (--check-prompt) — общий слой core.stage
    add_check_prompt_arg(ap)
    ap.add_argument("--script", default=None,
                    help="Путь к translate_book.py (обычно не нужен)")
    ap.add_argument("--prompt_file", default="",
                    help="Общий промпт-файл с тегами <translate>/<redact>/<polish> "
                         "(пусто = авто: кандидат с тегами из prompts/)")
    ap.add_argument("--ner_min_count", type=int, default=0,
                    help="Минимальный count термина для {ner_block} "
                         "(0 — выключено).")
    ap.add_argument("--names_min_count", type=int, default=10,
                    help="Минимальный count термина для {female_names}/"
                         "{male_names} (0 — выключено).")
    ap.add_argument("--ner_fields", default="term,translation,type",
                    help="Поля термина в {ner_block} через запятую "
                         "(алиасы — при включении aliases).")
    ap.add_argument("--request_budget", type=int, default=0,
                    help="Общий потолок user-запроса (чанк + все блоки), "
                         "ТОКЕНЫ (оценка); 0 = без ограничения; "
                         "превышение — ошибка чанка; действует во всех "
                         "режимах: перевод, редактура, полировка.")
    ap.add_argument("--chunk_size", type=int, default=None,
                    help="Размер чанка перевода/полировки, ТОКЕНЫ (оценка) "
                         "(пусто = PIPELINE_CHUNK_SIZE из .env → дефолт); "
                         "редактура идёт главой целиком.")
    ap.add_argument("--preview-request", dest="preview_request",
                   default=None,
                   help="ПРЕДПРОСМОТР: первый LLM-запрос действия "
                        "(стадия 1 цикла, первая глава, чанк 1, "
                        "1 поток) без сети → JSON-файл.")
    args = ap.parse_args()

    # --check-prompt: вердикт о промпт-файле до логов, чтения глав и сети.
    # Пустое поле у конвейера — не «встроенный промпт»: авто-режим сам берёт
    # первый кандидат с тегами из prompts/, и стадии работают именно им.
    # Проверка обязана видеть тот же файл — иначе она молчит о битом промпте,
    # который завтра уйдёт в запрос
    if args.check_prompt:
        auto = resolve_prompt_paths(args.prompt_file)
        args.prompt_file = args.prompt_file or auto.get(1, "")
    if check_prompt(args, "pipeline", PROMPT_CONTRACT):
        return


    log = logging.getLogger("web.pipeline")
    # Дефолты слоя уже посчитаны реестром (стадийные PIPELINE_* + общие
    # LLM-ключи, канон §7): свой .env у книги больше нет, локальные значения
    # запусков живут в браузере. LLM-часть конфига разрешает resolve_profile.
    args.chunk_size = args.chunk_size or _DEFAULTS["chunk_size"]

    log.setLevel(logging.INFO)
    if not log.handlers:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - "
                                          "%(message)s",
                                          datefmt="%Y-%m-%d %H:%M:%S"))
        log.addHandler(sh)
    # Фактическая команда запуска
    import shlex as _shlex
    log.info("Запуск: %s", _shlex.join(sys.argv))

    # корень репо (для cli/translate_book.py) — подъём от этого файла
    repo = Path(__file__).resolve().parents[1]
    script = Path(args.script).resolve() if args.script \
        else repo / "cli" / "translate_book.py"
    if not script.is_file():
        log.error("translate_book.py не найден: %s", script)
        sys.exit(1)

    # Сервер, ключ, модель, таймауты, повторы и потоки конвейера разрешаются
    # тем же общим слоем, что и у отдельной стадии (CLI > os.environ > общий
    # .env > дефолт реестра), и печатаются тоже им: раньше конвейер собирал
    # те же числа сам своим порядком. Предпросмотр запроса в сеть не идёт, поэтому
    # пустой сервер ему не ошибка.
    profile = resolve_profile(args, stage="pipeline",
                              require_model=not args.preview_request,
                              require_server=not args.preview_request,
                              logger=None if args.preview_request else log)
    host, api_key, model = profile.base_url, profile.api_key, profile.model
    args.threads = profile.threads
    args.timeout = profile.timeout
    args.stream_timeout = profile.stream_timeout
    args.max_retries = profile.max_retries
    args.temperature = profile.temperature
    # предел ОДНОЙ стадии (сторож подпроцесса) — из тех же таймаутов, что
    # действуют в stream_chat_completion: своё число лишало стадию
    # возможности доработать живую генерацию (kill посреди ответа)
    stage_limit = stage_call_timeout(args.timeout, args.stream_timeout,
                                     args.max_retries)

    chapters_dir = Path("chapters")
    chapters = build_chapter_map(str(chapters_dir))
    if not chapters:
        log.error("Папки глав не найдены в %s", chapters_dir)
        sys.exit(1)
    ids = sorted(chapters)
    start = args.start if args.start is not None else ids[0]
    end = args.end if args.end is not None else ids[-1]
    if start > end:
        log.error("start > end: %d > %d", start, end)
        sys.exit(1)

    action_spec = _ACTION_SPECS[args.action]
    stages = action_spec["stages"]
    action_label = action_spec["name"]
    # общий лог запуска (канон run_pipeline.sh): один файл на запуск
    # для ЛЮБОГО типа работы — logs/{Метка}_{start}-{end}_j{jobs}_{время}.log
    # (детальные логи по главам — logs/chapters/ как раньше);
    # в режиме предпросмотра файлы логов не создаются
    if not args.preview_request:
      try:
        short = {1: "Translate", 2: "Redact", 3: "Polish"}
        common_label = "FullCycle" if stages == [1, 2, 3] \
            else "".join(short[s] for s in stages)
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)
        common_log = logs_dir / (
            f"{common_label}_{start}-{end}_j{args.threads}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        # прошлый FileHandler от предыдущего запуска (тесты гоняют
        # main() в одном процессе) — убрать, оставив stdout
        for h in list(log.handlers):
            if isinstance(h, logging.FileHandler):
                log.removeHandler(h)
        fh = logging.FileHandler(common_log, encoding="utf-8", mode="w")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"))
        log.addHandler(fh)
        log.info("ОБЩИЙ ЛОГ  : %s", common_log)
      except OSError as exc:
        log.warning("Общий лог запуска не пишется: %s", exc)
    # промпт-файлы — явный общий файл > авто (первый кандидат с тегами
    # из prompts/); пусто = встроенные промпты translate_book.py
    prompts = resolve_prompt_paths(args.prompt_file)
    builtin = {}
    for stage in stages:
        if warn_missing_prompt_tag(prompts[stage], stage, log):
            builtin[stage] = True
    log.info("═" * 60)
    log.info("ТИП РАБОТЫ : %s", action_label)
    log.info("ДИАПАЗОН   : Главы с %d по %d", start, end)
    log.info("ПОТОКОВ    : %d суммарно (главы × чанки)", args.threads)
    log.info("ПРЕДЕЛ СТАДИИ: %d с (%s попыток × %d с таймаута) — живой, "
             "но долгий ответ не снимается раньше",
             int(stage_limit), args.max_retries,
             max(args.timeout, args.stream_timeout or 0))
    # сервер, таймауты, повторы и модель напечатаны общим слоем (строки
    # resolve_profile перед этим блоком): здесь только то, что есть только тут
    log.info("ПРОМПТЫ    : %s", " | ".join(
        f"{_STAGE_NAME[s]}={('встроенный' if builtin.get(s) or not prompts[s] else prompts[s])}"
        for s in stages))
    # поля {ner_block} — единый выбор для всех стадий конвейера;
    # aliases в блок попадают только когда поле выбрано в --ner_fields
    nf_list = [f.strip() for f in args.ner_fields.split(",") if f.strip()]
    log.info("NER-ПОЛЯ   : %s", ",".join(nf_list))
    log.info("ЧАНК       : %d ТОКЕНОВ (оценка; перевод/полировка)",
             args.chunk_size)
    # потолок запроса — предохранитель любого типа работы
    if args.request_budget:
        log.info("БЮДЖЕТ     : %d ТОКЕНОВ на user-запрос (превышение — "
                 "ошибка чанка)", args.request_budget)
    log.info("ВРЕМЯ      : %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("═" * 60)

    # ── Предпросмотр запроса (--preview-request): первый LLM-запрос
    #    действия (стадия 1 цикла, первая глава, чанк 1) без сети;
    #    артефакты и логи запуска не создаются ──
    if args.preview_request:
        stage = stages[0]
        in_name, _ = _STAGE_IO[stage]
        if stage == 3:
            in_name = action_spec.get("polish_input") or in_name
        cid = start if start in chapters else None
        if cid is None:
            # затишье здесь не запасной вариант: предпросмотр главы 001, о
            # которой человек не просил, показывает чужой запрос
            log.error("ПРЕДПРОСМОТР: глав %d нет в книге (главы %d–%d)",
                      start, ids[0], ids[-1])
            sys.exit(1)
        in_file = Path(chapters[cid][0]) / in_name
        if not in_file.is_file():
            log.error("ПРЕДПРОСМОТР: нет исходника главы %03d: %s",
                      cid, in_file)
            sys.exit(1)
        cmd = build_stage_cmd(
            stage, script, in_file, Path("tmp") / "preview_out.txt",
            host, api_key, model, args.timeout, args.temperature,
            args.stream_timeout,
            args.max_retries, 1,
            prompt_file=prompts.get(stage) or "",
            ner_min_count=args.ner_min_count,
            names_min_count=args.names_min_count,
            ner_fields=args.ner_fields,
            request_budget=args.request_budget,
            chunk_size=args.chunk_size)
        cmd += ["--preview-request", args.preview_request]
        proc_env = dict(os.environ)
        if api_key:
            proc_env["LLM_API_KEY"] = api_key
        proc_env.setdefault("PYTHONIOENCODING", "utf-8")
        log.info("ПРЕДПРОСМОТР : %s · глава %03d · чанк 1 · 1 поток",
                 _STAGE_NAME[stage], cid)
        try:
            proc = subprocess.run(cmd, env=proc_env, capture_output=True,
                                  text=True, encoding="utf-8",
                                  errors="replace", timeout=300)
        except subprocess.TimeoutExpired:
            log.error("ПРЕДПРОСМОТР: таймаут 300 с")
            sys.exit(1)
        if proc.returncode != 0:
            # причина — в хвосте вывода потомка (шапка лога бесполезна),
            # плюс слова-ошибки из grep_errors выше него
            hits = grep_errors((proc.stderr or "") + (proc.stdout or ""))
            reason = output_tail(proc.stderr, proc.stdout, 1500) \
                or "причина не напечатана"
            if hits:
                reason = "\n".join(hits + [reason])
            log.error("ПРЕДПРОСМОТР: ошибка (rc=%d)\n%s",
                      proc.returncode, reason)
            sys.exit(1)
        pv = Path(args.preview_request)
        if not pv.is_file():
            log.error("ПРЕДПРОСМОТР: файл не создан: %s", pv)
            sys.exit(1)
        try:
            data = json.loads(pv.read_text(encoding="utf-8"))
            chars = data.get("chars", {})
            log.info("ПРЕДПРОСМОТР : OK — %s (user %s симв.)",
                     pv, chars.get("user", "?"))
        except (OSError, ValueError):
            log.info("ПРЕДПРОСМОТР : OK — %s", pv)
        sys.exit(0)

    # порядок подачи в пул — возрастание номера главы (chapters_in_range):
    # имена папок алфавитно ставят «00000_100…» раньше «00000_1_…»
    to_process = chapters_in_range(chapters, start, end)
    tracker = Tracker(list(to_process.keys()), stages)
    for cid in range(start, end + 1):
        if cid not in to_process:
            log.info("Глава %03d | Пропущена (папка не найдена)", cid)
            tracker.mark_not_found(cid)
            emit({"type": "chapter", "id": cid, "stage": 0,
                  "status": "NOT_FOUND"})
    if not to_process:
        log.error("В диапазоне %d–%d нет папок глав", start, end)
        sys.exit(1)
    log.info("К обработке: %d глав(а/ы)", len(to_process))

    # суммарные потоки: параллельные главы × чанки на главу ≤ jobs;
    # глав много — потоки идут на главы (чанки 1), мало — свободные
    # уходят внутрь главы (потоков на главу больше 1)
    total = args.threads
    n_chapters = len(to_process)
    jobs = min(total, max(1, n_chapters))
    threads = max(1, total // jobs)
    log.info("РАЗЛОЖЕНИЕ : %d глав × %d чанков на главу", jobs, threads)

    results: dict[int, bool] = {}
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {
            pool.submit(process_chapter, cid, dirs, script, stages,
                        host, api_key, model, args.timeout, log, tracker,
                        args.temperature,
                        args.stream_timeout, args.max_retries,
                        threads, prompts,
                        polish_in=action_spec.get("polish_input"),
                        ner_min_count=args.ner_min_count,
                        names_min_count=args.names_min_count,
                        ner_fields=args.ner_fields,
                        request_budget=args.request_budget,
                        chunk_size=args.chunk_size,
                        stage_timeout=stage_limit): cid
            for cid, dirs in to_process.items()
        }
        for fut in as_completed(futures):
            cid = futures[fut]
            try:
                results[cid] = fut.result()
            except Exception as exc:  # noqa: BLE001
                log.error("Глава %03d | Необработанное исключение: %s",
                          cid, exc)
                results[cid] = False
            log.info("── %s ──", tracker.progress_str())

    log.info("")
    for line in tracker.report():
        log.info("%s", line)
    # провал ВСЕХ глав ≠ «успех»: exit 1, а не 0
    sys.exit(0 if results and all(results.values()) else 1)


if __name__ == "__main__":
    main()

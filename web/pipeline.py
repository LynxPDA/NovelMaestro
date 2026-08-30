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
JobManager._reader парсит их в job.events → SSE и payload.

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
from core.common import (  # noqa: E402
    build_chapter_map, find_env_file, format_ranges, get_server_config,
    parse_dotenv,
)

# ═══ Константы (канон run_pipeline.py) ═══
_STAGE_IO = {
    1: ("chapter.txt", "translated.txt"),
    2: ("translated_trace.json", "redacted.txt"),
    3: ("redacted.txt", "polished.txt"),
}
_STAGE_NAME = {1: "translate", 2: "redact", 3: "polish"}
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
# Дефолты конвейера. Переопределяются из .env: PIPELINE_TIMEOUT,
# PIPELINE_STREAM_TIMEOUT, PIPELINE_MAX_RETRIES, PIPELINE_CHUNK_SIZE,
# PIPELINE_NER_THRESHOLD, PIPELINE_NER_NGRAM, PIPELINE_JOBS (R5-H).
_DEFAULTS = {
    "timeout": 300,        # сек на один LLM-запрос внутри стадии
    "stream_timeout": 300,  # сек простоя стрима
    "max_retries": 3,
    "chunk_size": 7000,    # СИМВОЛЫ
    "ner_threshold": 0.75,
    "ner_ngram": 3,
    "jobs": 4,
}
# M7 (AUDIT): возвращено как было — текст перевода НЕ попадает в stdout
# скриптов (только прогресс/ошибки), лишняя настройка PIPELINE_ERROR_WORDS
# отменена по решению пользователя; жёсткий список ловит реальные сбои.
_ERROR_RE = re.compile(
    r"traceback|exception|error|warning|fatal|fail(?:ed)?(?:\s+to)?",
    re.IGNORECASE,
)
# Префикс логгера "2026-01-01 12:00:00 - WARNING - …": имя уровня
# матчит _ERROR_RE, но это не ошибка стадии (P0, AUDIT #1).
_LOGGER_PREFIX_RE = re.compile(
    r"^.*? - (?:DEBUG|INFO|WARNING|ERROR|CRITICAL) - ",
    re.IGNORECASE,
)
_SYM = {"OK": "✓", "ERROR": "✗", "SKIP": "⊘"}
CHAPTER_PREFIX = "@@CHAPTER@@"
# промпт-файлы конвейера — только единый общий файл с тегами
# <translate>/<redact>/<polish> (отдельные файлы на стадию убраны).
# Кандидаты «одного файла с тегами» для режима auto.
_PROMPT_COMBINED_CANDIDATES = [
    "prompts/pipeline_prompt.txt",
    "prompts/prompts.txt",
    "prompts/translate_book_prompt.txt",
]
_PROMPT_TAGS = ("translate", "redact", "polish")


def emit(ev: dict) -> None:
    """Событие конвейера → stdout (парсится JobManager'ом)."""
    print(CHAPTER_PREFIX + json.dumps(ev, ensure_ascii=False), flush=True)


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


def warn_missing_prompt_tag(prompt_file: str, stage: int, log) -> bool:
    """Предупреждение, если общий промпт-файл не содержит тега стадии.

    Файл без тегов — легальный режим «промпт целиком», не ругаемся.
    Если теги есть, но нужного нет — стадия уйдёт на встроенный промпт.
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
    tag = _STAGE_TAG[stage]
    if get_tagged_prompt(text, tag):
        return False
    if not any(get_tagged_prompt(text, t) for t in _PROMPT_TAGS):
        return False
    log.warning("В промпт-файле %s нет тега <%s> — стадия «%s» "
                "использует ВСТРОЕННЫЙ промпт",
                prompt_file, tag, _STAGE_NAME[stage])
    return True


def build_stage_cmd(stage: int, script: Path, in_file: Path, out_file: Path,
                    host: str, api_key: str, model: str,
                    timeout: int, temperature=None, reasoning_effort=None,
                    no_reasoning=False, stream_timeout=None,
                    threads: int = 1, prompt_file: str = "") -> list[str]:
    # единая модель конвейера (PIPELINE_MODEL → MODEL) — без
    # отдельных моделей под translate/redact/polish
    common = [
        "--host", host,
        "--model", model,
        "--threads", str(threads),
        "--timeout", str(timeout),
        "--stream_timeout", str(stream_timeout or timeout),
        "--max_retries", str(_DEFAULTS["max_retries"]),
        "--out", str(out_file),
        "--ner_file", "ner.json",
    ]
    # P1 (AUDIT #2): ключ не попадает в argv — передаётся через
    # окружение subprocess (LLM_API_KEY), см. process_chapter.
    if temperature is not None:
        common += ["--temperature", str(temperature)]
    if reasoning_effort:
        common += ["--reasoning_effort", str(reasoning_effort)]
    if no_reasoning:
        common.append("--no_reasoning")
    # общий промпт-файл — только если задан (пусто = встроенный)
    if prompt_file:
        common += ["--prompt_file", prompt_file]
    if stage == 1:
        return [sys.executable, str(script), str(in_file), "--mode",
                "translate", *common,
                "--ner_fields", "term,type,translation",
                "--chunk_size", str(_DEFAULTS["chunk_size"]),
                "--ner_threshold", str(_DEFAULTS["ner_threshold"]),
                "--ner_ngram", str(_DEFAULTS["ner_ngram"])]
    if stage == 2:
        return [sys.executable, str(script), str(in_file), "--mode",
                "redact", *common,
                "--min_len_ratio", "0.9",
                "--ner_threshold", str(_DEFAULTS["ner_threshold"]),
                "--ner_ngram", str(_DEFAULTS["ner_ngram"])]
    return [sys.executable, str(script), str(in_file), "--mode",
            "polish", *common,
            "--ner_fields", "term,type,translation",
            "--chunk_size", str(_DEFAULTS["chunk_size"]),
            "--ner_threshold", str(_DEFAULTS["ner_threshold"]),
            "--ner_ngram", str(_DEFAULTS["ner_ngram"])]


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


def process_chapter(chapter_id: int, dirs: list[Path], script: Path,
                    stages: list[int], host: str, api_key: str, model: str,
                    timeout: int, log: logging.Logger,
                    tracker: Tracker,
                    temperature=None, reasoning_effort=None,
                    no_reasoning=False, stream_timeout=None,
                    threads: int = 1,
                    prompts: dict[int, str] | None = None,
                    polish_in: str | None = None) -> bool:
    """Одна глава: стадии по порядку, fail-fast (код 0 + файл + grep).

    polish_in — вход полировки вместо дефолтного redacted.txt
    (полировка из перевода: действия 4 и 6)."""
    sub_timeout = timeout * 10 + 600
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
                tracker.record(chapter_id, stage, "SKIP")
                emit({"type": "chapter", "id": chapter_id, "stage": stage,
                      "status": "SKIP"})
                return True
            cmd = build_stage_cmd(stage, script, in_file, out_file,
                                  host, api_key, model, timeout,
                                  temperature, reasoning_effort,
                                  no_reasoning, stream_timeout,
                                  threads,
                                  prompt_file=(prompts or {}).get(stage) or "")
            proc_env = dict(os.environ)
            if api_key:
                proc_env["LLM_API_KEY"] = api_key
            try:
                # Popen + построчное чтение — строки @@PROGRESS@@
                # потомка ретранслируются в свой stdout (JobManager пробросит
                # их в SSE), остальной вывод копится в буфер для grep_errors.
                # subprocess.run(capture_output=True) глотал прогресс целиком.
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=proc_env, bufsize=1,
                )
            except OSError as exc:
                log.error("Глава %03d | [%s] Не удалось запустить: %s",
                          chapter_id, name, exc)
                tracker.record(chapter_id, stage, "ERROR")
                emit({"type": "chapter", "id": chapter_id, "stage": stage,
                      "status": "ERROR"})
                return False
            out_lines: list[str] = []
            err_lines: list[str] = []

            def _drain(stream, sink: list[str]) -> None:
                if stream is None:
                    return
                for line in stream:
                    if line.startswith("@@PROGRESS@@"):
                        try:
                            ev = json.loads(
                                line[len("@@PROGRESS@@"):].strip())
                            label = f"Глава {chapter_id} · {name}"
                            if ev.get("label"):
                                label += f" · {ev['label']}"
                            print("@@PROGRESS@@" + json.dumps(
                                {"type": "progress",
                                 "done": ev.get("done", 0),
                                 "total": ev.get("total", 0),
                                 "label": label},
                                ensure_ascii=False), flush=True)
                        except Exception:
                            print(line, end="", flush=True)
                        continue
                    sink.append(line)

            tout = threading.Thread(target=_drain, args=(proc.stdout, out_lines))
            terr = threading.Thread(target=_drain, args=(proc.stderr, err_lines))
            tout.start()
            terr.start()
            try:
                proc.wait(timeout=sub_timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                tout.join()
                terr.join()
                log.error("Глава %03d | [%s] ТАЙМАУТ subprocess (%d с)",
                          chapter_id, name, sub_timeout)
                tracker.record(chapter_id, stage, "ERROR")
                emit({"type": "chapter", "id": chapter_id, "stage": stage,
                      "status": "ERROR"})
                return False
            tout.join()
            terr.join()
            combined = "".join(out_lines) + "\n" + "".join(err_lines)
            if proc.returncode != 0:
                log.error("Глава %03d | [%s] ОШИБКА (код %d)",
                          chapter_id, name, proc.returncode)
                for line in combined.strip().splitlines()[-10:]:
                    log.error("  │ %s", line)
                tracker.record(chapter_id, stage, "ERROR")
                emit({"type": "chapter", "id": chapter_id, "stage": stage,
                      "status": "ERROR"})
                return False
            if not out_file.is_file() or out_file.stat().st_size == 0:
                log.error("Глава %03d | [%s] ОШИБКА (файл %s не создан)",
                          chapter_id, name, out_name)
                tracker.record(chapter_id, stage, "ERROR")
                emit({"type": "chapter", "id": chapter_id, "stage": stage,
                      "status": "ERROR"})
                return False
            error_lines = grep_errors(combined)
            if error_lines:
                log.error("Глава %03d | [%s] СЛОВА-ОШИБКИ В ВЫВОДЕ:",
                          chapter_id, name)
                for line in error_lines:
                    log.error("  │ %s", line)
                tracker.record(chapter_id, stage, "ERROR")
                emit({"type": "chapter", "id": chapter_id, "stage": stage,
                      "status": "ERROR"})
                return False
            tracker.record(chapter_id, stage, "OK")
            emit({"type": "chapter", "id": chapter_id, "stage": stage,
                  "status": "OK"})
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
    ap.add_argument("--jobs", type=int, default=None, help="Потоков (1–16)")
    ap.add_argument("--threads", type=int, default=None,
                    help="Потоков на главу (чанки), 1–16 (по умолчанию 1)")
    ap.add_argument("--timeout", type=int, default=None,
                    help="Таймаут одного LLM-запроса, сек")
    ap.add_argument("--stream_timeout", type=int, default=None,
                    help="Таймаут простоя стрима, сек")
    ap.add_argument("--temperature", type=float, default=None,
                    help="Температура LLM (пусто = сервер)")
    ap.add_argument("--reasoning_effort", default=None,
                    choices=["none", "minimal", "low", "medium", "high",
                             "xhigh", "max"],
                    help="Усилия рассуждений: none/minimal/low/medium/"
                         "high/xhigh/max (none — отключить)")
    ap.add_argument("--no_reasoning", action="store_true",
                    help="Отключить рассуждения (reasoning_effort=none)")
    ap.add_argument("--host", default="", help="URL LLM-сервера (пусто = HOST из .env)")
    ap.add_argument("--api_key", default="", help="API-ключ (argv — только для тестов; в web идёт через LLM_API_KEY)")
    ap.add_argument("--model", default="",
                    help="Модель (пусто = PIPELINE_MODEL из .env → MODEL)")
    ap.add_argument("--env_file", default=None, help="Путь к .env")
    ap.add_argument("--script", default=None,
                    help="Путь к translate_book.py (обычно не нужен)")
    ap.add_argument("--prompt_file", default="",
                    help="Общий промпт-файл с тегами <translate>/<redact>/<polish> "
                         "(пусто = авто: кандидат с тегами из prompts/)")
    args = ap.parse_args()

    log = logging.getLogger("web.pipeline")
    # PIPELINE_* из .env переопределяют дефолты (R5-H)
    env_data = parse_dotenv(find_env_file(args.env_file))
    for key, default in list(_DEFAULTS.items()):
        raw = env_data.get(f"PIPELINE_{key.upper()}")
        if raw is None:
            continue
        try:
            _DEFAULTS[key] = (float(raw) if key == "ner_threshold"
                              else int(raw))
        except ValueError as exc:
            log.warning("PIPELINE_%s=%r не число (%s); дефолт: %r",
                        key.upper(), raw, exc, default)
    args.jobs = args.jobs or _DEFAULTS["jobs"]
    args.threads = args.threads or 1
    if not 1 <= args.threads <= 16:
        ap.error("--threads должен быть 1–16")
    args.timeout = args.timeout or _DEFAULTS["timeout"]
    args.stream_timeout = args.stream_timeout or _DEFAULTS["stream_timeout"]

    if not 1 <= args.jobs <= 16:
        ap.error("--jobs должен быть 1–16")

    log.setLevel(logging.INFO)
    if not log.handlers:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - "
                                          "%(message)s",
                                          datefmt="%Y-%m-%d %H:%M:%S"))
        log.addHandler(sh)
    # R9: фактическая команда запуска
    import shlex as _shlex
    log.info("Запуск: %s", _shlex.join(sys.argv))

    # корень репо (для cli/translate_book.py) — подъём от этого файла
    repo = Path(__file__).resolve().parents[1]
    script = Path(args.script).resolve() if args.script \
        else repo / "cli" / "translate_book.py"
    if not script.is_file():
        log.error("translate_book.py не найден: %s", script)
        sys.exit(1)

    # сервер: явные CLI > PIPELINE_HOST/API_KEY/MODEL из .env > LLM_API_KEY
    sc = get_server_config(env_data, "pipeline")
    host = args.host or sc["host"]
    api_key = args.api_key or sc["api_key"]
    if not api_key:
        api_key = os.environ.get("LLM_API_KEY", "")
    # единая модель конвейера: PIPELINE_MODEL → общая MODEL
    model = args.model or sc["model"] or ""

    if not host:
        log.error("Host LLM-сервера не задан: укажите --host или создайте .env (HOST)")
        sys.exit(1)

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
    # (детальные логи по главам — logs/chapters/ как раньше)
    try:
        short = {1: "Translate", 2: "Redact", 3: "Polish"}
        common_label = "FullCycle" if stages == [1, 2, 3] \
            else "".join(short[s] for s in stages)
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)
        common_log = logs_dir / (
            f"{common_label}_{start}-{end}_j{args.jobs}_"
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
    log.info("ПОТОКОВ    : %d (главы) × %d (чанки на главу)",
             args.jobs, args.threads)
    log.info("МОДЕЛЬ     : %s", model or "из .env")
    log.info("СЕРВЕР     : %s", host)
    log.info("ПРОМПТЫ    : %s", " | ".join(
        f"{_STAGE_NAME[s]}={('встроенный' if builtin.get(s) or not prompts[s] else prompts[s])}"
        for s in stages))
    log.info("ВРЕМЯ      : %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("═" * 60)

    to_process = {cid: [Path(p) for p in dirs]
                  for cid, dirs in chapters.items() if start <= cid <= end}
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

    results: dict[int, bool] = {}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {
            pool.submit(process_chapter, cid, dirs, script, stages,
                        host, api_key, model, args.timeout, log, tracker,
                        args.temperature, args.reasoning_effort,
                        args.no_reasoning, args.stream_timeout,
                        args.threads, prompts,
                        polish_in=action_spec.get("polish_input")): cid
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
    # C2 (AUDIT): провал ВСЕХ глав ≠ «успех» — exit 1, а не 0
    sys.exit(0 if results and all(results.values()) else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stages.py — спеки стадий web-интерфейса (M4: не-LLM; M5: LLM).

Каждая стадия: spec (поля формы) + build_command(form, ctx) → argv.
- cwd = папка проекта; python = sys.executable; скрипт = REPO/scripts/xxx.py.
- Единицы в лейблах — как в help скриптов (СИМВОЛЫ/ТОКЕНЫ/ГЛАВЫ).
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("web.stages")

# ── типы полей ─────────────────────────────────────────────────────────
# text / number / bool / select / files (select из папки проекта) /
# range (start-end) / password

# ── R9: настройки запусков в .env ───────────────────────────────────────
# Поля формы стадии сохраняются в .env проекта при каждом запуске и
# предзаполняются из него. Схема ключей: {STAGE}_{FIELD} (верхним
# регистром); LLM-поля — отдельно: model → {STAGE}_LOCAL/REMOTE_MODEL
# (по профилю; скрипты читают эти ключи через get_stage_model),
# host → {STAGE}_HOST. api_key/profile в .env НЕ пишутся.

def env_keys_for(stage: str, field: str, profile: str = "") -> list[str]:
    """Кандидаты env-ключей для поля формы стадии (R9, ).

    Схема без профилей: host → HOST (единый адрес), model →
    <STAGE>_MODEL → MODEL, api_key → API_KEY (единый ключ — персист
    и предзаполнение, ключ хранится в .env). profile убран.
    Персист берёт первый ключ; предзаполнение — первый найденный.
    """
    s = (stage or "").upper()
    f = (field or "").upper()
    if f == "PROFILE":
        return []
    if f == "API_KEY":
        return ["API_KEY"]
    if f == "MODEL":
        return [f"{s}_MODEL", "MODEL"]
    if f == "HOST":
        return ["HOST"]
    return [f"{s}_{f}"]


def _range_argv(name: str, form: dict, start_key: str = "start",
                end_key: str = "end") -> list[str]:
    out = []
    start = form.get(start_key)
    end = form.get(end_key)
    if start not in (None, ""):
        out += [f"--{start_key}", str(start)]
    if end not in (None, ""):
        out += [f"--{end_key}", str(end)]
    return out


def _llm_argv(form: dict, ctx: dict, stage: str = "") -> list[str]:
    """--host/--model/--api_key для LLM-стадий (без профилей).

    Приоритет: явные значения формы > HOST/API_KEY/MODEL из .env
    (find_env_file от папки проекта + get_server_config/get_stage_model).
    Пустой результат — скрипт сам найдёт .env или попросит ввод.

    C1 (AUDIT): ключ из .env подставляется ТОЛЬКО если host не переопределён
    формой (или совпадает с env-HOST) — иначе ключ уйдёт на чужой сервер.
    M1 (AUDIT): ключа нет в проектном .env (он копируется без секретов) →
    fallback на системный корневой .env.
    """
    argv = []
    form_host = (form.get("host") or "").strip()
    host = form_host
    model = (form.get("model") or "").strip()
    api_key = (form.get("api_key") or "").strip()
    env_data: dict = {}
    pdir = ctx.get("project_dir") if isinstance(ctx, dict) else None
    if pdir is not None:
        try:
            from core.common import find_env_file, parse_dotenv
            env_path = find_env_file(start_dir=pdir)
            env_data = parse_dotenv(env_path)
        except Exception as exc:
            log.debug(".env не загружен: %s", exc)
            env_data = {}
    try:
        from core.common import get_server_config, get_stage_model
        sc = get_server_config(env_data)
        if not host:
            host = sc["host"]
        # C1: ключ из .env — только для env-HOST'а
        if not api_key and (not form_host or host == sc["host"]):
            api_key = sc["api_key"]
            if not api_key:
                # M1: проектный .env без секретов → системный корневой .env
                from core.common import (find_env_file as _find_env,
                                         parse_dotenv as _parse_env)
                sys_env = _parse_env(_find_env())
                api_key = get_server_config(sys_env)["api_key"]
        if not model:
            model = get_stage_model(env_data, stage) or sc["model"]
    except Exception as exc:
        log.debug("Сервер из .env не применён: %s", exc)
    if host:
        argv += ["--host", host]
    if api_key:
        # P1 (AUDIT #2): ключ не попадает в argv (виден в ps) — он
        # уходит в окружение subprocess через ctx["_llm_api_key"]
        # (JobManager.start env), а скрипты читают LLM_API_KEY.
        if isinstance(ctx, dict):
            ctx["_llm_api_key"] = api_key
    if model:
        argv += ["--model", model]
    return argv


def build_epub_to_chapters(form: dict, ctx: dict) -> list[str]:
    argv = ["scripts/epub_to_chapters.py"]
    if form.get("input"):
        argv += ["--input", str(form["input"])]
    if form.get("lang"):
        argv += ["--lang", str(form["lang"])]
    if form.get("polished"):
        argv += ["--polished", "1"]
    if form.get("clean") == 0:
        argv += ["--clean", "0"]
    if form.get("remove_pages") == 0:
        argv += ["--remove-pages", "0"]
    if form.get("chapter_re"):
        argv += ["--chapter-re", str(form["chapter_re"])]
    if form.get("book_title"):
        argv += ["--book-title", str(form["book_title"])]
    if form.get("chunk_size") not in (None, ""):
        argv += ["--chunk-size", str(form["chunk_size"])]
    if form.get("clean_output"):
        argv += ["--clean-output"]
    if form.get("dry_run"):
        argv += ["--dry-run"]
    return argv


# подписи пресетов в web-форме → числовой --preset translate_check.py
PRESET_BY_LABEL = {"polished": "1", "redacted": "2", "translated": "3"}


def build_translate_check(form: dict, ctx: dict) -> list[str]:
    argv = ["scripts/translate_check.py"]
    if form.get("preset"):
        preset = PRESET_BY_LABEL.get(str(form["preset"]), str(form["preset"]))
        argv += ["--preset", preset]
    argv += _range_argv("start", form)
    if form.get("lenient"):
        argv += ["--lenient"]
    if form.get("exclude_words"):
        argv += ["--exclude-words", str(form["exclude_words"])]
    return argv


def build_clean_and_compile(form: dict, ctx: dict) -> list[str]:
    argv = ["scripts/clean_and_compile.py",
            "--mode", str(form.get("mode", "txt"))]
    argv += _range_argv("start", form)
    if form.get("source_type"):
        argv += ["--source-type", str(form["source_type"])]
    if form.get("chunk_size"):
        argv += ["--chunk-size", str(form["chunk_size"])]
    if form.get("tmp_dir"):
        argv += ["--tmp-dir", str(form["tmp_dir"])]
    if form.get("no_donate"):
        argv += ["--no-donate"]
    if form.get("no_fb2_cover"):
        argv += ["--no-fb2-cover"]
    if form.get("donate_file"):
        argv += ["--donate-file", str(form["donate_file"])]
    return argv


def build_batch_replace(form: dict, ctx: dict) -> list[str]:
    argv = ["scripts/batch_replace.py"]
    if form.get("rules_file"):
        argv += ["--rules-file", str(form["rules_file"])]
    if form.get("type"):
        argv += ["--type", str(form["type"])]
    argv += _range_argv("start", form)
    if form.get("regex"):
        argv += ["--regex"]
    if form.get("dry_run"):
        argv += ["--dry-run"]
    return argv


def build_pipeline(form: dict, ctx: dict) -> list[str]:
    """Стадия 3 — конвейер translate→redact→polish по главам.

    argv: pipeline.py --action 1..4 --start --end --jobs + LLM.
    """
    argv = ["web/pipeline.py"]
    action = form.get("action")
    if action not in (None, ""):
        argv += ["--action", str(action)]
    argv += _range_argv("pipeline", form)
    if form.get("jobs") not in (None, ""):
        argv += ["--jobs", str(form["jobs"])]
    if form.get("threads") not in (None, ""):
        argv += ["--threads", str(form["threads"])]
    if form.get("timeout") not in (None, ""):
        argv += ["--timeout", str(form["timeout"])]
    if form.get("temperature") not in (None, ""):
        argv += ["--temperature", str(form["temperature"])]
    re_effort = form.get("reasoning_effort")
    if re_effort == "none":
        argv.append("--no_reasoning")
    elif re_effort not in (None, ""):
        argv += ["--reasoning_effort", str(re_effort)]
    # промпт-файлы конвейера — режим auto/separate/combined.
    # combined → --prompt_file (теги); separate → по одному флагу на стадию;
    # auto → явно заданные поля, остальное дорешает resolve_prompt_paths.
    prompt_mode = form.get("prompt_mode") or "auto"
    _PER_STAGE = (("translate_prompt", "--translate_prompt"),
                  ("redact_prompt", "--redact_prompt"),
                  ("polish_prompt", "--polish_prompt"))
    if prompt_mode == "combined":
        if form.get("prompt_file"):
            argv += ["--prompt_file", str(form["prompt_file"])]
    elif prompt_mode == "separate":
        for name, flag in _PER_STAGE:
            if form.get(name):
                argv += [flag, str(form[name])]
    else:  # auto
        if form.get("prompt_file"):
            argv += ["--prompt_file", str(form["prompt_file"])]
        for name, flag in _PER_STAGE:
            if form.get(name):
                argv += [flag, str(form[name])]
    argv += _llm_argv(form, ctx, "pipeline")
    return argv


def build_ner(form: dict, ctx: dict) -> list[str]:
    """Стадия 2 — извлечение NER (ner.py).

    Три режима скрипта: extract (txt → глоссарий / дообучение),
    compile (собрать chapter.txt + извлечение), postprocess
    (обработка ner.json без LLM: --strip-meta / --min-count).
    """
    argv = ["scripts/ner.py"]
    mode = form.get("mode") or "extract"
    if mode == "compile":
        argv.append("--compile_chapters")
    elif form.get("file"):
        argv.append(str(form["file"]))
    if form.get("ner_file"):
        argv += ["--ner_file", str(form["ner_file"])]
    if form.get("prompt_file"):
        argv += ["--prompt_file", str(form["prompt_file"])]
    for name, flag in (("threads", "--threads"), ("chunk_size", "--chunk_size"),
                       ("retries", "--retries"), ("timeout", "--timeout"),
                       ("save_interval", "--save-interval"),
                       ("min_count", "--min-count")):
        if form.get(name) not in (None, ""):
            argv += [flag, str(form[name])]
    if form.get("threshold") not in (None, ""):
        argv += ["--threshold", str(form["threshold"])]
    if form.get("ngram") not in (None, ""):
        argv += ["--ngram", str(form["ngram"])]
    if form.get("temperature") not in (None, ""):
        argv += ["--temperature", str(form["temperature"])]
    if form.get("reasoning") == "none":
        argv.append("--no_reasoning")
    elif form.get("reasoning"):
        argv += ["--reasoning-effort", str(form["reasoning"])]
    if form.get("two_pass"):
        argv.append("--two-pass")
    if form.get("keep_fields"):
        argv += ["--keep-fields", str(form["keep_fields"])]
    if form.get("strip_meta"):
        argv.append("--strip-meta")
    argv += _llm_argv(form, ctx, "ner")
    return argv


def build_ner_check(form: dict, ctx: dict) -> list[str]:
    """Стадия n — проверка глоссария (ner_check.py)."""
    argv = ["scripts/ner_check.py"]
    if form.get("input"):
        argv += ["--input", str(form["input"])]
    if form.get("report"):
        argv += ["--report", str(form["report"])]
    if form.get("review"):
        argv += ["--review", str(form["review"])]
    if form.get("prompt_file"):
        argv += ["--prompt_file", str(form["prompt_file"])]
    if form.get("passes"):
        argv += ["--passes", str(form["passes"])]
    if form.get("types"):
        argv += ["--types", str(form["types"])]
    if form.get("batch_size") not in (None, ""):
        argv += ["--batch_size", str(form["batch_size"])]
    if form.get("count_threshold") not in (None, ""):
        argv += ["-c", str(form["count_threshold"])]
    if form.get("exclude_words"):
        argv += ["--exclude-words", str(form["exclude_words"])]
    for flag in ("show_aliases", "show_votes"):
        if form.get(flag):
            argv.append(f"--{flag.replace('_', '-')}")
    for flag in ("apply", "auto_apply", "dry_run"):
        if form.get(flag):
            argv.append(f"--{flag.replace('_', '-')}")
    if form.get("no_bak"):
        argv.append("--no-bak")
    if form.get("temperature") not in (None, ""):
        argv += ["--temperature", str(form["temperature"])]
    re_effort = form.get("reasoning_effort")
    if re_effort == "none":
        argv.append("--no_reasoning")
    elif re_effort not in (None, ""):
        argv += ["--reasoning_effort", str(re_effort)]
    if form.get("max_tokens") not in (None, ""):
        argv += ["--max_tokens", str(form["max_tokens"])]
    for name, flag in (("timeout", "--timeout"),
                       ("stream_timeout", "--stream_timeout"),
                       ("max_retries", "--max_retries")):
        if form.get(name) not in (None, ""):
            argv += [flag, str(form[name])]
    argv += _llm_argv(form, ctx, "ner_check")
    return argv


def build_translate_check_llm(form: dict, ctx: dict) -> list[str]:
    """Стадия translate_check_llm — проверка перевода через LLM
    (translate_check_llm.py)."""
    argv = ["scripts/translate_check_llm.py"]
    argv += _range_argv("translate_check_llm", form)
    if form.get("chapters_dir"):
        argv += ["--chapters_dir", str(form["chapters_dir"])]
    if form.get("type"):
        argv += ["--type", str(form["type"])]
    if form.get("two_pass"):
        argv.append("--two_pass")
    if form.get("context_budget") not in (None, ""):
        argv += ["--context_budget", str(form["context_budget"])]
    if form.get("review"):
        argv += ["--review", str(form["review"])]
    # флаги применения/предпросмотра/бэкапов собираются только для пути
    # «Проверка» проекта (ctx["review_apply"]); из «Запусков» (форма без
    # этих чекбоксов) они всегда выключены
    if ctx.get("review_apply"):
        for flag in ("apply", "auto_apply", "dry_run"):
            if form.get(flag):
                argv.append(f"--{flag.replace('_', '-')}")
        if form.get("no_bak"):
            argv.append("--no-bak")
    if form.get("prompt_file"):
        argv += ["--prompt_file", str(form["prompt_file"])]
    if form.get("temperature") not in (None, ""):
        argv += ["--temperature", str(form["temperature"])]
    if form.get("reasoning_effort") == "none":
        argv.append("--no_reasoning")
    elif form.get("reasoning_effort"):
        argv += ["--reasoning_effort", str(form["reasoning_effort"])]
    for name, flag in (("max_retries", "--max_retries"),
                       ("timeout", "--timeout"),
                       ("stream_timeout", "--stream_timeout"),
                       ("retry_empty", "--retry_empty"),
                       ("threads", "--threads"),
                       ("max_fixes_per_chapter", "--max_fixes_per_chapter"),
                       ("min_fix_length", "--min_fix_length"),
                       ("max_changed_chars", "--max_changed_chars")):
        if form.get(name) not in (None, ""):
            argv += [flag, str(form[name])]
    argv += _llm_argv(form, ctx, "translate_check_llm")
    return argv


def build_wiki(form: dict, ctx: dict) -> list[str]:
    """Стадия 7 — генерация вики (wiki.py). file — txt новеллы."""
    argv = ["scripts/wiki.py"]
    if form.get("file"):
        argv.append(str(form["file"]))
    if form.get("ner_file"):
        argv += ["--ner_file", str(form["ner_file"])]
    if form.get("output"):
        argv += ["--output", str(form["output"])]
    if form.get("prompt_file"):
        argv += ["--prompt_file", str(form["prompt_file"])]
    for name, flag in (("top", "--top"), ("min_count", "--min-count"),
                       ("context_chunks", "--context-chunks"),
                       ("near_distance", "--near-distance"),
                       ("chunk_size", "--chunk-size"),
                       ("save_interval", "--save-interval"),
                       ("co_occurrence_top", "--co-occurrence-top"),
                       ("retries", "--retries"), ("timeout", "--timeout"),
                       ("threads", "--threads")):
        if form.get(name) not in (None, ""):
            argv += [flag, str(form[name])]
    if form.get("exclude_types"):
        argv += ["--exclude-types", str(form["exclude_types"])]
    if form.get("types"):
        argv += ["--types", str(form["types"])]
    if form.get("co_occurrence_pairs"):
        argv += ["--co-occurrence-pairs", str(form["co_occurrence_pairs"])]
    if form.get("temperature") not in (None, ""):
        argv += ["--temperature", str(form["temperature"])]
    if form.get("thinking") == "none":
        argv.append("--no_reasoning")
    elif form.get("thinking"):
        argv += ["--thinking", str(form["thinking"])]
    if form.get("rulate_mode"):
        argv.append("--rulate-mode")
    argv += _llm_argv(form, ctx, "wiki")
    return argv


# ── Общие поля сервера для LLM-стадий (вставляются в начало формы) ──
# значения подтягиваются из .env и вписываются в поля —
# подсказки «Пусто = …» не нужны (help отсутствует).
_LLM_FIELDS = [
    {"name": "host", "label": "Сервер LLM",
     "type": "text", "default": ""},
    {"name": "model", "label": "Модель",
     "type": "text", "default": ""},
    {"name": "api_key", "label": "API-ключ",
     "type": "password", "default": ""},
]


STAGE_SPECS: dict[str, dict] = {
    "epub": {
        "title": "Разбор исходника на главы",
        "script": "epub_to_chapters.py",
        "build": build_epub_to_chapters,
        "fields": [
            {"name": "input", "label": "Исходник (epub/zip/txt), пусто = автопоиск",
             "type": "files", "dir": "source", "ext": [".epub", ".zip", ".txt"],
             "default": ""},
            {"name": "lang", "label": "Пресет языка",
             "type": "select", "options": ["", "zh", "en", "ru"], "default": ""},
            {"name": "polished", "label": "Полированный TXT (--polished 1)",
             "type": "bool", "default": False},
            {"name": "clean", "label": "Чистки (--clean 0 = выключить)",
             "type": "select", "options": ["1", "0"], "default": "1"},
            {"name": "remove_pages", "label": "Номера страниц (0 = не убирать)",
             "type": "select", "options": ["1", "0"], "default": "1"},
            {"name": "chapter_re",
             "label": "Маркер главы (regex, пусто = пресет)",
             "type": "text", "default": ""},
            {"name": "book_title",
             "label": "Название книги (удаляется из заголовков)",
             "type": "text", "default": ""},
            {"name": "chunk_size",
             "label": "Чанк фоллбэка (нет маркеров), СИМВОЛЫ",
             "type": "number", "default": "7000"},
            {"name": "clean_output",
             "label": "Очистить папки глав перед записью",
             "type": "bool", "default": False},
            {"name": "dry_run", "label": "Предпросмотр (--dry-run)",
             "type": "bool", "default": False},
        ],
    },
    "translate_check": {
        "title": "Проверка перевода",
        "script": "translate_check.py",
        "build": build_translate_check,
        "fields": [
            {"name": "preset", "label": "Пресет сравнения",
             "type": "select",
             "options": ["polished", "redacted", "translated"],
             "default": "polished"},
            {"name": "start", "label": "Начальная глава (ГЛАВЫ)",
             "type": "number", "default": ""},
            {"name": "end", "label": "Конечная глава", "type": "number", "default": ""},
            {"name": "lenient", "label": "Мягкий режим (--lenient)",
             "type": "bool", "default": False},
            {"name": "exclude_words",
             "label": "Слова-исключения (через запятую)",
             "type": "text", "default": "",
             "help": "Пусто = TRANSLATE_CHECK_EXCLUDE_WORDS из .env "
                      "или дефолт скрипта (VIP,MVP,【,】,NPC)"},
        ],
    },
    "compile": {
        "title": "Компиляция TXT/EPUB/FB2",
        "script": "clean_and_compile.py",
        "build": build_clean_and_compile,
        "fields": [
            {"name": "mode", "label": "Режим",
             "type": "select",
             "options": ["txt", "epub", "fb2", "titles", "epub-chunks",
                         "txt-chunks", "fb2-chunks"],
             "default": "txt"},
            {"name": "start", "label": "Начальная глава (ГЛАВЫ)",
             "type": "number", "default": ""},
            {"name": "end", "label": "Конечная глава", "type": "number", "default": ""},
            {"name": "source_type", "label": "Исходный файл главы",
             "type": "select", "options": ["polished", "redacted", "translated", "chapter"],
             "default": "polished"},
            {"name": "chunk_size", "label": "Глав в части для *-chunks",
             "type": "number", "default": ""},
            {"name": "tmp_dir", "label": "Папка для compiled_*/book_*",
             "type": "text", "default": ""},
            {"name": "no_donate", "label": "Без страницы поддержки (--no-donate)",
             "type": "bool", "default": False},
            {"name": "no_fb2_cover", "label": "Без обложки в FB2",
             "type": "bool", "default": False},
            {"name": "donate_file", "label": "Файл страницы поддержки",
             "type": "text", "default": ""},
        ],
    },
    "pipeline": {
        "title": "Перевод (LLM)",
        "script": "web/pipeline.py",
        "build": build_pipeline,
        "fields": _LLM_FIELDS + [
            {"name": "action", "label": "Тип работы",
             "type": "select",
             "options": ["1", "2", "3", "4", "5", "6", "7", "8"],
             "default": "8",
             "labels": {
                 "1": "Перевод",
                 "2": "Редактура (исходник - Перевод)",
                 "3": "Полировка (исходник - Редактура)",
                 "4": "Полировка (исходник - Перевод)",
                 "5": "Сокращенный цикл: Перевод -> Редактура",
                 "6": "Сокращенный цикл: Перевод -> Полировка",
                 "7": "Сокращенный цикл: Редактура -> Полировка",
                 "8": "Полный цикл: Перевод -> Редактура -> Полировка"},
             "help": "1=перевод, 2=редактура (исходник - перевод), "
                      "3=полировка (исходник - редактура), "
                      "4=полировка (исходник - перевод), "
                      "5=перевод→редактура, 6=перевод→полировка, "
                      "7=редактура→полировка, 8=полный цикл"},
            {"name": "prompt_mode", "label": "Режим промптов",
             "type": "select",
             "options": ["auto", "separate", "combined"],
             "default": "auto",
             "labels": {"auto": "Авто (по папке prompts/)",
                        "separate": "Отдельные файлы на стадию",
                        "combined": "Один файл с тегами"},
             "help": "auto: ищет файл с тегами <translate>/<redact>/<polish> "
                      "(pipeline_prompt.txt, prompts.txt, "
                      "translate_book_prompt.txt), иначе — дефолтные имена "
                      "по стадиям (translate/redact/polish_prompt.txt)"},
            {"name": "prompt_file",
             "label": "Общий промпт-файл (теги translate/redact/polish)",
             "type": "files", "dir": "prompts", "ext": [".txt"],
             "default": "",
             "help": "для режима combined; в auto — перекрывает автоподхват"},
            {"name": "translate_prompt", "label": "Промпт перевода",
             "type": "files", "dir": "prompts", "ext": [".txt"],
             "default": "",
             "help": "для режима separate; пусто = дефолтный файл/встроенный"},
            {"name": "redact_prompt", "label": "Промпт редактуры",
             "type": "files", "dir": "prompts", "ext": [".txt"],
             "default": "",
             "help": "для режима separate; пусто = дефолтный файл/встроенный"},
            {"name": "polish_prompt", "label": "Промпт полировки",
             "type": "files", "dir": "prompts", "ext": [".txt"],
             "default": "",
             "help": "для режима separate; пусто = дефолтный файл/встроенный"},
            {"name": "start", "label": "Начальная глава (ГЛАВЫ)",
             "type": "number", "default": ""},
            {"name": "end", "label": "Конечная глава", "type": "number", "default": ""},
            {"name": "jobs", "label": "Параллельных потоков (1–16)",
             "type": "number", "default": "4"},
            {"name": "threads", "label": "Потоков на главу (чанки), 1–16",
             "type": "number", "default": "1",
             "help": "при параллельных главах (jobs>1) держите 1 — иначе упрётесь в лимит запросов"},
            {"name": "timeout", "label": "Таймаут LLM-запроса, сек",
             "type": "number", "default": "300"},
            {"name": "temperature", "label": "Температура (пусто = сервер)",
             "type": "text", "default": ""},
            {"name": "reasoning_effort", "label": "Reasoning effort",
             "type": "text",
             "help": "пусто — не передаётся; none — отключает; low/medium/high/xhigh/max — как есть",
             "default": ""},
        ],
    },
    "ner": {
        "title": "Создание глоссария (LLM)",
        "script": "ner.py",
        "build": build_ner,
        "fields": _LLM_FIELDS + [
            {"name": "mode", "label": "Режим работы",
             "type": "select",
             "options": ["extract", "compile", "postprocess"],
             "default": "extract",
             "labels": {
                 "extract": "Извлечение из txt (новый или дообучение)",
                 "compile": "Собрать chapter.txt по папкам + извлечение",
                 "postprocess": "Обработка ner.json (без LLM)",
             },
             "help": "извлечение: нужен txt; ner.json есть — дообучение, нет — новый. собрать главы: сам склеит chapters/*/chapter.txt. обработка: только strip-meta / min-count, без LLM."},
            {"name": "file", "label": "Входной txt (для извлечения)",
             "type": "files", "dir": "", "ext": [".txt"], "default": "",
             "help": "нужен в режиме извлечения; в «собрать главы» и «обработка» не обязателен"},
            {"name": "ner_file", "label": "Глоссарий ner.json",
             "type": "files", "dir": "", "ext": [".json"], "default": "ner.json",
             "help": "если файл есть — термины добавятся к нему (дообучение); если нет — создастся новый. в режиме «обработка» — входной файл"},
            {"name": "prompt_file", "label": "Промпт-файл (теги pass1/pass2)",
             "type": "files", "dir": "prompts", "ext": [".txt"],
             "default": "ner_prompt.txt"},
            {"name": "threads", "label": "Потоков (1–16)",
             "type": "number", "default": "4"},
            {"name": "chunk_size", "label": "Размер чанка, СИМВОЛЫ",
             "type": "number", "default": "7000"},
            {"name": "threshold", "label": "Порог дедупликации (0–1)",
             "type": "number", "default": "0.75"},
            {"name": "ngram", "label": "N-граммы для латиницы",
             "type": "number", "default": "3"},
            {"name": "temperature", "label": "Температура (пусто = сервер)",
             "type": "text", "default": ""},
            {"name": "reasoning", "label": "Reasoning effort",
             "type": "text",
             "help": "пусто — не передаётся; none — отключает; low/medium/high/xhigh/max — как есть",
             "default": ""},
            {"name": "two_pass", "label": "Двухпроходная схема (--two-pass)",
             "type": "bool", "default": True},
            {"name": "keep_fields", "label": "Поля в голосование (через запятую)",
             "type": "text", "default": "",
             "help": "Пусто = голосуют translation/type/pinyin; notes, context, translated_context не голосуют. Пример: notes,context"},
            {"name": "strip_meta", "label": "Удалить служебные поля (--strip-meta)",
             "type": "bool", "default": False},
            {"name": "min_count", "label": "Мин. count для сохранения",
             "type": "number", "default": "",
             "help": "Пусто = сохраняются все записи (фильтр count не применяется)"},
            {"name": "save_interval", "label": "Интервал сохранения кэша",
             "type": "number", "default": "10"},
            {"name": "retries", "label": "Повторные попытки",
             "type": "number", "default": "3"},
            {"name": "timeout", "label": "Таймаут запроса, сек",
             "type": "number", "default": "300"},
        ],
    },
    "ner_check": {
        "title": "Проверка глоссария (LLM)",
        "script": "ner_check.py",
        "build": build_ner_check,
        "fields": _LLM_FIELDS + [
            {"name": "input", "label": "Входной JSON (ner.json)",
             "type": "files", "dir": "", "ext": [".json"], "default": "ner.json"},
            {"name": "report", "label": "Отчёт", "type": "text", "default": "ner_report.md"},
            {"name": "review", "label": "Review-файл правок",
             "type": "text", "default": "ner_review.json"},
            {"name": "prompt_file", "label": "Промпт-файл",
             "type": "files", "dir": "prompts", "ext": [".txt"],
             "default": "ner_check_prompt.txt"},
            {"name": "passes", "label": "Проходы",
             "labels": {"all": "Всё (этапы 1+2)", "whole": "Весь список (этап 1)",
                         "types": "По типам (этап 2)"},
             "type": "select", "options": ["all", "whole", "types"],
             "default": "all"},
            {"name": "types", "label": "Типы через запятую (пусто = все)",
             "type": "text", "default": ""},
            {"name": "batch_size", "label": "Бюджет пакета, СИМВОЛЫ",
             "type": "number", "default": "196608"},
            {"name": "count_threshold", "label": "Порог count (> X)",
             "type": "number", "default": "0"},
            {"name": "exclude_words", "label": "Исключить слова",
             "type": "text", "default": "палладия,палладию"},
            {"name": "show_aliases", "label": "Показывать алиасы",
             "type": "bool", "default": False},
            {"name": "show_votes", "label": "Показывать голоса",
             "type": "bool", "default": False},
            {"name": "apply", "label": "Применить принятые правки (--apply)",
             "type": "bool", "default": False},
            {"name": "auto_apply", "label": "Автоприменение (--auto-apply)",
             "type": "bool", "default": False},
            {"name": "dry_run", "label": "Предпросмотр (--dry-run)",
             "type": "bool", "default": False},
            {"name": "no_bak", "label": "Не создавать .bak (--no-bak)",
             "type": "bool", "default": False},
            {"name": "temperature", "label": "Температура (пусто = сервер)",
             "type": "text", "default": ""},
            {"name": "reasoning_effort", "label": "Reasoning effort",
             "type": "text",
             "help": "пусто — не передаётся; none — отключает; low/medium/high/xhigh/max — как есть",
             "default": ""},
            {"name": "max_tokens", "label": "Max tokens (серверный лимит), ТОКЕНЫ",
             "type": "number", "default": "65536"},
            {"name": "timeout", "label": "Таймаут, сек", "type": "number", "default": "300"},
            {"name": "stream_timeout", "label": "Таймаут стрима, сек",
             "type": "number", "default": "300"},
            {"name": "max_retries", "label": "Повторы", "type": "number", "default": "3"},
        ],
    },
    "translate_check_llm": {
        "title": "Проверка перевода (LLM)",
        "script": "translate_check_llm.py",
        "build": build_translate_check_llm,
        "fields": _LLM_FIELDS + [
            {"name": "type", "label": "Тип файлов глав",
             "type": "select", "options": ["polished", "redacted"],
             "default": "polished"},
            {"name": "start", "label": "Начальная глава (ГЛАВЫ)",
             "type": "number", "default": ""},
            {"name": "end", "label": "Конечная глава", "type": "number", "default": ""},
            {"name": "chapters_dir", "label": "Папка глав",
             "type": "text", "default": "chapters"},
            {"name": "two_pass", "label": "Второй проход верификации",
             "type": "bool", "default": False},
            {"name": "context_budget", "label": "Бюджет контекста на пакет, СИМВОЛЫ",
             "type": "number", "default": "75000"},
            {"name": "review", "label": "Review-файл правок",
             "type": "text", "default": "translate_check_llm_review.json"},
            {"name": "prompt_file", "label": "Промпт-файл (теги pass1/pass2)",
             "type": "files", "dir": "prompts", "ext": [".txt"],
             "default": "translate_check_prompt.txt"},
            {"name": "temperature", "label": "Температура (пусто = сервер)",
             "type": "text", "default": ""},
            {"name": "reasoning_effort", "label": "Reasoning effort",
             "type": "text",
             "help": "пусто — не передаётся; none — отключает; low/medium/high/xhigh/max — как есть",
             "default": ""},
            {"name": "max_retries", "label": "Попытки на запрос", "type": "number", "default": "3"},
            {"name": "timeout", "label": "Таймаут соединения, сек", "type": "number", "default": "300"},
            {"name": "stream_timeout", "label": "Таймаут стрима, сек",
             "type": "number", "default": "300"},
            {"name": "retry_empty", "label": "Доп. повторы при пустом ответе",
             "type": "number", "default": "0"},
            {"name": "threads", "label": "Параллельные пакеты", "type": "number", "default": "4"},
            {"name": "max_fixes_per_chapter", "label": "Лимит правок на главу (0 = нет)",
             "type": "number", "default": "0"},
            {"name": "min_fix_length", "label": "Мин. длина правки, СИМВОЛЫ",
             "type": "number", "default": "0"},
            {"name": "max_changed_chars", "label": "Макс. изменённых символов, СИМВОЛЫ",
             "type": "number", "default": "0"},
        ],
    },
    "wiki": {
        "title": "Создание Wiki (LLM)",
        "script": "wiki.py",
        "build": build_wiki,
        "fields": _LLM_FIELDS + [
            {"name": "file", "label": "Входной txt новеллы (перевод)",
             "type": "files", "dir": "", "ext": [".txt"], "default": ""},
            {"name": "ner_file", "label": "NER JSON",
             "type": "files", "dir": "", "ext": [".json"], "default": "ner.json"},
            {"name": "output", "label": "Выходной Markdown", "type": "text", "default": "wiki.md"},
            {"name": "prompt_file", "label": "Промпт (тег <prompt_wiki_article>)",
             "type": "files", "dir": "prompts", "ext": [".txt"],
             "default": "wiki_prompt.txt"},
            {"name": "top", "label": "Макс. терминов", "type": "number", "default": "80"},
            {"name": "min_count", "label": "Мин. частота термина", "type": "number", "default": "2"},
            {"name": "exclude_types", "label": "Исключить типы (через запятую)",
             "type": "text", "default": ""},
            {"name": "types", "label": "Белый список типов", "type": "text", "default": ""},
            {"name": "context_chunks", "label": "Фрагментов контекста на термин",
             "type": "number", "default": "12"},
            {"name": "near_distance", "label": "NEAR-дистанция, ТОКЕНЫ",
             "type": "number", "default": "64"},
            {"name": "chunk_size", "label": "Размер чанка FTS5, СИМВОЛЫ",
             "type": "number", "default": "1000"},
            {"name": "save_interval", "label": "Интервал сохранения кэша",
             "type": "number", "default": "5"},
            {"name": "co_occurrence_pairs", "label": "Пары типов для связей",
             "type": "text", "default": "Person:Person,Person:Organisation,Person:Artifact"},
            {"name": "co_occurrence_top", "label": "Связей на термин", "type": "number", "default": "5"},
            {"name": "rulate_mode", "label": "Rulate-режим (--rulate-mode)",
             "type": "bool", "default": False},
            {"name": "temperature", "label": "Температура (пусто = сервер)",
             "type": "text", "default": ""},
            {"name": "thinking", "label": "Reasoning effort",
             "type": "text",
             "help": "пусто — не передаётся; none — отключает; low/medium/high/xhigh/max — как есть",
             "default": ""},
            {"name": "retries", "label": "Повторы", "type": "number", "default": "3"},
            {"name": "timeout", "label": "Таймаут запроса, сек", "type": "number", "default": "300"},
            {"name": "threads", "label": "Потоков", "type": "number", "default": "4"},
        ],
    },
    "batch_replace": {
        "title": "Массовые замены",
        "script": "batch_replace.py",
        "build": build_batch_replace,
        "fields": [
            {"name": "rules_file", "label": "Файл правил",
             "type": "files", "dir": "prompts", "ext": [".txt"],
             "default": "prompts/replacements.txt"},
            {"name": "type", "label": "Тип файлов глав",
             "type": "select", "options": ["polished", "redacted", "translated", "chapter"],
             "default": "polished"},
            {"name": "start", "label": "Начальная глава (ГЛАВЫ)",
             "type": "number", "default": ""},
            {"name": "end", "label": "Конечная глава", "type": "number", "default": ""},
            {"name": "regex", "label": "Правила как regex (--regex)",
             "type": "bool", "default": False},
            {"name": "dry_run", "label": "Предпросмотр (--dry-run)",
             "type": "bool", "default": False},
        ],
    },
}

# Порядок отображения стадий в «Запусках» (логика конвейера: разбор →
# NER → проверка глоссария → конвейер → проверка → правки → замены →
# компиляция → вики). Слаги — контракт API, не менять.
STAGE_ORDER: list[str] = [
    "epub", "ner", "ner_check", "pipeline", "translate_check",
    "translate_check_llm", "batch_replace", "compile", "wiki",
]


def ordered_stages() -> list[tuple[str, dict]]:
    """(key, spec) в порядке STAGE_ORDER; новые ключи — в конце."""
    keys = STAGE_ORDER + [k for k in STAGE_SPECS if k not in STAGE_ORDER]
    return [(k, STAGE_SPECS[k]) for k in keys]


def spec_for(key: str) -> dict | None:
    """Спека стадии (без функции build)."""
    spec = STAGE_SPECS.get(key)
    if spec is None:
        return None
    out = dict(spec)
    out.pop("build", None)
    return out


def build_command(key: str, form: dict, ctx: dict) -> list[str]:
    """argv для стадии (относительные пути — cwd=проект)."""
    spec = STAGE_SPECS.get(key)
    if spec is None:
        raise ValueError(f"Нет спеки стадии: {key}")
    return spec["build"](form, ctx)


def script_path(key: str, repo_root: Path) -> Path | None:
    """Абсолютный путь к скрипту стадии в репо.

    script может быть "x.py" (scripts/x.py) или "папка/файл.py"
    (относительно корня репо — web-оркестраторы)."""
    spec = STAGE_SPECS.get(key)
    if spec is None:
        return None
    rel = spec["script"]
    if "/" in rel:
        return repo_root / rel
    return repo_root / "scripts" / rel

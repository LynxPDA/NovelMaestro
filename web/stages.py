#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stages.py — спеки стадий web-интерфейса (M4: не-LLM; M5: LLM).

Каждая стадия: spec (поля формы) + build_command(form, ctx) → argv.
- cwd = папка проекта; python = sys.executable; скрипт = REPO/cli/xxx.py.
- Единицы в лейблах — как в help скриптов (СИМВОЛЫ/ТОКЕНЫ/ГЛАВЫ).
- preset: карточка «Простой режим» — title/desc + overrides; параметры
  простого режима считаются preset_params() (дефолты полей формы +
  overrides).
- simple: имена полей, ДОПОЛНИТЕЛЬНО показываемых в простом режиме
  (карточка пресета + диапазон глав + эти поля); остальные поля —
  экспертные и в простом режиме берут дефолты пресета.
- стадий без simple (translate_check/batch_replace/compile) простого
  режима нет — только экспертный (переключатель не показывается).
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
# регистром); LLM-поля — отдельно: host → {STAGE}_HOST → HOST,
# api_key → {STAGE}_API_KEY → API_KEY, model → {STAGE}_MODEL → MODEL
# (скрипты читают через get_server_config(env, stage)).

def env_keys_for(stage: str, field: str, profile: str = "") -> list[str]:
    """Кандидаты env-ключей для поля формы стадии (R9).

    Схема «один скрипт — один набор сервер+ключ+модель»: host →
    <STAGE>_HOST → HOST, api_key → <STAGE>_API_KEY → API_KEY, model →
    <STAGE>_MODEL → MODEL. Персист берёт первый ключ (стадийный);
    предзаполнение — первый найденный. profile убран.
    """
    s = (stage or "").upper()
    f = (field or "").upper()
    if f == "PROFILE":
        return []
    if f == "API_KEY":
        return [f"{s}_API_KEY", "API_KEY"]
    if f == "MODEL":
        return [f"{s}_MODEL", "MODEL"]
    if f == "HOST":
        return [f"{s}_HOST", "HOST"]
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
    (find_env_file от папки проекта + get_server_config(env, stage)).
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
        from core.common import get_server_config
        sc = get_server_config(env_data, stage)
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
                api_key = get_server_config(sys_env, stage)["api_key"]
        if not model:
            model = sc["model"]
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


def _epub_lines(value) -> list[str]:
    """Строки textarea-поля epub (по одному паттерну на строку)."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    return [ln.strip() for ln in str(value).splitlines() if ln.strip()]


def _replace_lines(value) -> list[str]:
    r"""Строки textarea правил замен (batch_replace/replace_patterns).

    В отличие от _epub_lines пробелы по краям строки НЕ режутся:
    они могут быть значимы («^  ->» — отступ строки; «\s+ -> » —
    сжатие пробелов). Убираются только переводы строки.
    """
    if not value:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    return [ln.rstrip("\r") for ln in str(value).splitlines() if ln.strip()]


def build_epub_to_chapters(form: dict, ctx: dict) -> list[str]:
    argv = ["cli/epub_to_chapters.py"]
    if form.get("input"):
        argv += ["--input", str(form["input"])]
    mode = str(form.get("mode") or "toc")
    argv += ["--mode", mode]
    for p in _epub_lines(form.get("split_patterns")):
        argv += ["--split-re", p]
    # clean-re применяются к исходному тексту: пробелы по краям строки
    # значимы (напр. « +$» — хвостовые пробелы), как в CLI
    for p in _replace_lines(form.get("clean_patterns")):
        argv += ["--clean-re", p]
    # «Замены и очистки (replace)» из формы epub убраны — замены после
    # разбивки делает отдельная стадия batch_replace (у неё же
    # предпросмотр с подсветкой)
    if mode == "chunk":
        if form.get("chunk_size") not in (None, ""):
            argv += ["--chunk-size", str(form["chunk_size"])]
    # маска нужна и в chunk-режиме, и при переопределении названий
    if mode == "chunk" or form.get("rename_chapters"):
        if form.get("chunk_mask"):
            argv += ["--chunk-mask", str(form["chunk_mask"])]
    if form.get("rename_chapters"):
        argv.append("--rename-chapters")
    if form.get("title_limit") not in (None, ""):
        argv += ["--title-limit", str(form["title_limit"])]
    if form.get("num_offset") not in (None, ""):
        argv += ["--num-offset", str(form["num_offset"])]
    for s in form.get("skip") or []:
        argv += ["--skip", str(s)]
    if form.get("output_type") not in (None, "", "chapter"):
        argv += ["--output-type", str(form["output_type"])]
    if form.get("clean_output"):
        argv += ["--clean-output"]
    return argv


# подписи пресетов в web-форме → числовой --preset translate_check.py

def build_translate_check(form: dict, ctx: dict) -> list[str]:
    argv = ["cli/translate_check.py"]
    check_type = str(form.get("check_type") or "polished")
    argv += ["--check-type", check_type]
    argv += _range_argv("start", form)
    if form.get("exclude_words"):
        argv += ["--exclude-words", str(form["exclude_words"])]
    for name, flag in (("neighbor", "--neighbor"),
                       ("original", "--original")):
        if form.get(name) not in (None, ""):
            argv += [flag, str(form[name])]
    for line in _epub_lines(form.get("regexp_checks")):
        argv += ["--regexp-check", line]
    if form.get("min_file_size") not in (None, ""):
        argv += ["--min-file-size", str(form["min_file_size"])]
    if form.get("header_regexp") not in (None, ""):
        argv += ["--header-regexp", str(form["header_regexp"])]
    if form.get("sequence_check", True) in (False, "0", 0):
        argv.append("--no-sequence-check")
    return argv


def build_clean_and_compile(form: dict, ctx: dict) -> list[str]:
    argv = ["cli/clean_and_compile.py",
            "--mode", str(form.get("mode", "txt"))]
    argv += _range_argv("start", form)
    if form.get("source_type"):
        argv += ["--source-type", str(form["source_type"])]
    if form.get("chunk_size"):
        argv += ["--chunk-size", str(form["chunk_size"])]
    # --tmp-dir убран из web: compiled_*/book_* пишутся в корень проекта
    # единая обложка для EPUB и FB2; пусто = без обложки (--no-cover)
    cover = form.get("cover")
    if cover:
        argv += ["--epub-cover", str(cover), "--fb2-cover", str(cover)]
    else:
        argv.append("--no-cover")
    if form.get("epub_meta"):
        argv += ["--epub-meta", str(form["epub_meta"])]
    # страница поддержки: явный файл или ничего (без автоподхвата)
    if form.get("donate_file"):
        argv += ["--donate-file", str(form["donate_file"])]
    else:
        argv.append("--no-donate")
    return argv


def build_batch_replace(form: dict, ctx: dict) -> list[str]:
    argv = ["cli/batch_replace.py"]
    for line in _replace_lines(form.get("replacements")):
        argv += ["--replace", line]
    if form.get("type"):
        argv += ["--type", str(form["type"])]
    argv += _range_argv("start", form)
    # --dry-run на этапе формы не нужен: предпросмотр изменений —
    # панель «Предпросмотр замен» по выбранной главе (SPA →
    # POST /api/stages/batch_replace/preview)
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
    # потоки — СУММАРНО (jobs): распределение на главы/чанки делает
    # сам pipeline.py; отдельного --threads больше нет
    if form.get("jobs") not in (None, ""):
        argv += ["--jobs", str(form["jobs"])]
    # пороги count: ner_block и имена (пусто/0 = фильтр выключен)
    for flag, name in (("--ner_min_count", "ner_min_count"),
                       ("--names_min_count", "names_min_count")):
        if form.get(name) not in (None, ""):
            argv += [flag, str(form[name])]
    if form.get("timeout") not in (None, ""):
        argv += ["--timeout", str(form["timeout"])]
    if form.get("max_retries") not in (None, ""):
        argv += ["--max_retries", str(form["max_retries"])]
    if form.get("temperature") not in (None, ""):
        argv += ["--temperature", str(form["temperature"])]
    re_effort = form.get("reasoning_effort")
    if re_effort not in (None, ""):
        argv += ["--reasoning_effort", str(re_effort)]
    # единый общий промпт-файл (теги <translate>/<redact>/<polish>);
    # пусто = авто (кандидат с тегами из prompts/)
    if form.get("prompt_file"):
        argv += ["--prompt_file", str(form["prompt_file"])]
    argv += _llm_argv(form, ctx, "pipeline")
    return argv


def build_ner(form: dict, ctx: dict) -> list[str]:
    """Стадия 2 — извлечение NER (ner.py).

    Режимы: extract (новый глоссарий), finetune (дообучение на
    существующий ner.json). Вход: выбранный файл (позиционный
    аргумент) ИЛИ сборка глав в память (--compile_chapters,
    опционально start/end). LLM-флаги — для обоих режимов.
    """
    argv = ["cli/ner.py"]
    mode = form.get("mode") or "extract"
    if form.get("file"):
        argv.append(str(form["file"]))
    else:
        argv.append("--compile_chapters")
        argv += _range_argv("start", form)
    if form.get("ner_file"):
        argv += ["--ner_file", str(form["ner_file"])]
    if form.get("prompt_file"):
        argv += ["--prompt_file", str(form["prompt_file"])]
    for name, flag in (("threads", "--threads"),
                       ("chunk_size", "--chunk_size"),
                       ("retries", "--retries"),
                       ("timeout", "--timeout"),
                       ("save_interval", "--save-interval")):
        if form.get(name) not in (None, ""):
            argv += [flag, str(form[name])]
    if form.get("threshold") not in (None, ""):
        argv += ["--threshold", str(form["threshold"])]
    if form.get("ngram") not in (None, ""):
        argv += ["--ngram", str(form["ngram"])]
    if form.get("temperature") not in (None, ""):
        argv += ["--temperature", str(form["temperature"])]
    if form.get("reasoning") not in (None, ""):
        argv += ["--reasoning-effort", str(form["reasoning"])]
    if form.get("two_pass"):
        argv.append("--two-pass")
    if form.get("keep_fields"):
        argv += ["--keep-fields", str(form["keep_fields"])]
    argv += _llm_argv(form, ctx, "ner")
    return argv


def build_ner_check(form: dict, ctx: dict) -> list[str]:
    """Стадия n — проверка глоссария (ner_check.py)."""
    argv = ["cli/ner_check.py"]
    if form.get("input"):
        argv += ["--input", str(form["input"])]
    # --report удалён: отчёт ner_report.md не нужен (выпилен из web и cli)
    if form.get("review"):
        argv += ["--review", str(form["review"])]
    if form.get("prompt_file"):
        argv += ["--prompt_file", str(form["prompt_file"])]
    if form.get("passes"):
        argv += ["--passes", str(form["passes"])]
    if form.get("rag_terms"):
        argv += ["--rag_terms", str(form["rag_terms"])]
    if form.get("rag_source_type"):
        argv += ["--rag_source_type", str(form["rag_source_type"])]
    argv += _range_argv("start", form)
    if form.get("rag_budget") not in (None, ""):
        argv += ["--rag_budget", str(form["rag_budget"])]
    if form.get("save_interval") not in (None, ""):
        argv += ["--save-interval", str(form["save_interval"])]
    # RAG-промпт — это тот же «Промпт-файл» (внутри тег <prompt_rag>);
    # отдельного --rag_prompt_file нет: CLI берёт --prompt_file
    if form.get("types"):
        argv += ["--types", str(form["types"])]
    if form.get("batch_size") not in (None, ""):
        argv += ["--batch_size", str(form["batch_size"])]
    if form.get("threads") not in (None, ""):
        argv += ["--threads", str(form["threads"])]
    if form.get("count_threshold") not in (None, ""):
        argv += ["-c", str(form["count_threshold"])]
    if form.get("fields"):
        argv += ["--fields", str(form["fields"])]
    # --apply/--auto-apply/--no-bak убраны из Запусков — правки
    # применяются только в «Проверках» проекта
    if form.get("temperature") not in (None, ""):
        argv += ["--temperature", str(form["temperature"])]
    re_effort = form.get("reasoning_effort")
    if re_effort not in (None, ""):
        argv += ["--reasoning_effort", str(re_effort)]
    if form.get("max_tokens") not in (None, ""):
        argv += ["--max_tokens", str(form["max_tokens"])]
    for name, flag in (("timeout", "--timeout"),
                       ("max_retries", "--max_retries")):
        if form.get(name) not in (None, ""):
            argv += [flag, str(form[name])]
    argv += _llm_argv(form, ctx, "ner_check")
    return argv


def build_translate_check_llm(form: dict, ctx: dict) -> list[str]:
    """Стадия translate_check_llm — проверка перевода через LLM
    (translate_check_llm.py)."""
    argv = ["cli/translate_check_llm.py"]
    argv += _range_argv("translate_check_llm", form)
    # папка глав всегда ./chapters (дефолт скрипта, cwd = проект)
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
    if form.get("reasoning_effort") not in (None, ""):
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


def build_translate_quality(form: dict, ctx: dict) -> list[str]:
    """Стадия «Оценка перевода (LLM)» — translate_quality.py.

    Один LLM-запрос по пакету глав диапазона (тип файлов глав →
    {translated_text}, chapter.txt → {original_text}); бюджет —
    СИМВОЛЫ (главы; промпт НЕ входит), пакет обрезается до целого количества
    глав. Выход — md-отчёт.
    """
    argv = ["cli/translate_quality.py"]
    argv += _range_argv("translate_quality", form)
    if form.get("type"):
        argv += ["--type", str(form["type"])]
    if form.get("prompt_file"):
        argv += ["--prompt_file", str(form["prompt_file"])]
    output = str(form.get("output") or "translation_quality_assessment.md")
    argv += ["--output", output]
    if form.get("budget") not in (None, ""):
        argv += ["--budget", str(form["budget"])]
    if form.get("temperature") not in (None, ""):
        argv += ["--temperature", str(form["temperature"])]
    re_effort = form.get("reasoning_effort")
    if re_effort not in (None, ""):
        argv += ["--reasoning_effort", str(re_effort)]
    for name, flag in (("max_retries", "--max_retries"),
                       ("timeout", "--timeout")):
        if form.get(name) not in (None, ""):
            argv += [flag, str(form[name])]
    argv += _llm_argv(form, ctx, "translate_quality")
    return argv


def build_wiki(form: dict, ctx: dict) -> list[str]:
    """Стадия 7 — генерация вики (wiki.py).

    Источник текста: готовый txt (source/file) ИЛИ сборка глав в память
    (source=chapters → --compile-chapters + --type/--start/--end).
    Формат: md / rulate-md / rulate-html; оглавление и якоря — только
    в обычном режиме (toc/toc_links).
    """
    argv = ["cli/wiki.py"]
    src = form.get("source") or "chapters"
    fmt = form.get("format") or "md"
    output = str(form.get("output") or "wiki.md")
    as_chapter = bool(form.get("as_chapter"))
    if src == "chapters":
        argv.append("--compile-chapters")
        if form.get("type"):
            argv += ["--type", str(form["type"])]
        argv += _range_argv("start", form)
    elif form.get("file"):
        argv.append(str(form["file"]))
    if as_chapter:
        argv.append("--as-chapter")
        if form.get("save_type"):
            argv += ["--save-type", str(form["save_type"])]
    else:
        if fmt == "rulate-md":
            argv.append("--rulate-mode")
        elif fmt == "rulate-html":
            argv.append("--rulate-html")
            if output == "wiki.md":
                output = "wiki.txt"
        toc_on = form.get("toc", True)
        links_on = form.get("toc_links", True)
        if toc_on in (False, "0", 0):
            argv.append("--no-toc")
        if links_on in (False, "0", 0):
            argv.append("--no-toc-links")
        if output:
            argv += ["--output", output]
    if form.get("ner_file"):
        argv += ["--ner_file", str(form["ner_file"])]
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
    # типы — чипсы (hidden): выбранные = белый список --types;
    # пусто (все выбраны) — флаг не передаётся (CLI: все типы)
    if form.get("types"):
        argv += ["--types", str(form["types"])]
    if form.get("co_occurrence_pairs"):
        argv += ["--co-occurrence-pairs", str(form["co_occurrence_pairs"])]
    if form.get("temperature") not in (None, ""):
        argv += ["--temperature", str(form["temperature"])]
    if form.get("thinking") not in (None, ""):
        argv += ["--thinking", str(form["thinking"])]
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


# ── пресеты «Простого режима» в Запусках ───────────────────────────────
# Пресет: title (название карточки) + desc (1–2 строки «что будет
# сделано») + overrides (отклонения от дефолтов полей формы). Параметры
# простого режима — preset_params(spec): непустые дефолты полей формы +
# overrides; LLM-поля (host/model/api_key) имеют пустые дефолты и в
# params не попадают — скрипты сами берут сервер из .env.

def preset_params(spec: dict) -> dict:
    """Параметры простого режима запуска (пресет) для спеки стадии.

    params = непустые дефолты полей формы + overrides пресета — ровно
    то, что форма отправила бы с дефолтными значениями: булёвы входят
    всегда (false = выключено), пустые строки пропускаются, files с
    dir префиксуются папкой (R5-G). API вкладывает результат в
    spec.preset.params — SPA читает его без дублирования логики.
    """
    params: dict = {}
    for f in spec.get("fields", []):
        name = f["name"]
        default = f.get("default")
        if f["type"] == "bool":
            params[name] = bool(default)
        elif default is None or str(default) == "":
            continue
        elif f["type"] == "files":
            v = str(default)
            if f.get("dir") and "/" not in v:
                v = f"{f['dir']}/{v}"
            params[name] = v
        else:
            params[name] = str(default)
    params.update(spec.get("preset", {}).get("overrides") or {})
    return params


STAGE_SPECS: dict[str, dict] = {
    "epub": {
        "title": "Разбор исходника на главы",
        "script": "epub_to_chapters.py",
        "build": build_epub_to_chapters,
        "autosave": True,  # настройки формы — сразу в localStorage
        "fields": [
            {"name": "input",
             "label": "Исходник",
             "type": "files", "dir": "source",
             "ext": [".epub", ".txt"], "default": ""},
            {"name": "mode", "label": "Режим разбивки",
             "type": "select",
             "options": ["toc", "regex", "chunk"],
             "labels": {"toc": "По TOC (epub)",
                        "regex": "Ручной (regexp)",
                        "chunk": "По чанкам"},
             "default": "toc",
             "help": "toc — только epub, по структуре (TOC/spine/h1-h2); "
                      "regex/chunk — epub ИЛИ txt (epub перегоняется "
                      "в текст); zip не принимается"},
            {"name": "split_patterns",
             "label": "Паттерны разбивки (regexp, по одному на строку)",
             "type": "textarea", "rows": 4, "default": "",
             "help": "ТОЛЬКО режим regexp. Строка считается маркером, "
                      "если НАЧИНАЕТСЯ с любого паттерна; вся строка "
                      "становится заголовком главы; комментарий в конце "
                      "строки — « # …»; пример: «Глава \\d+»; "
                      "EPUB_SPLIT_PATTERNS в .env — переносы строк "
                      "как «\\n»"}, 
            {"name": "chunk_size", "label": "Размер чанка, СИМВОЛЫ",
             "type": "number", "default": "7000",
             "help": "ТОЛЬКО режим «по чанкам»"},
            {"name": "chunk_mask",
             "label": "Маска названия глав",
             "type": "text", "default": "Chapter {num}",
             "help": "названия чанков в режиме «по чанкам»; при включённом "
                      "«Переопределить названия» — названия ВСЕХ глав; "
                      "{num} — номер; пример: «Часть {num}» → 00000_1_Часть_1…"},
            {"name": "rename_chapters",
             "label": "Переопределить названия глав маской",
             "type": "bool", "default": False,
             "help": "все заголовки глав заменяются на «Маска названия "
                      "глав» ({num} — номер). Удобно после разбивки по "
                      "TOC/паттернам: «Chapter 1», «Chapter 2»…"},
            {"name": "title_limit",
             "label": "Длина названия каталога, СИМВОЛЫ",
             "type": "number", "default": "50",
             "help": "имя папки обрезается; первая строка файла — "
                      "полный заголовок"},
            {"name": "num_offset",
             "label": "Смещение нумерации (первый номер)",
             "type": "number", "default": "1",
             "help": "875 → первая папка 000_875_… (нули добивают "
                      "ширину 6)"},
            {"name": "output_type", "label": "Тип выходного файла",
             "type": "select",
             "options": ["chapter", "translated", "redacted", "polished"],
             "labels": {"chapter": "chapter.txt",
                        "translated": "translated.txt",
                        "redacted": "redacted.txt",
                        "polished": "polished.txt"},
             "default": "chapter",
             "help": "какой файл создаётся в папке главы (канон "
                      "артефактов стадий)"},
            {"name": "clean_output",
             "label": "Очистить папки глав перед записью",
             "type": "bool", "default": False,
             "help": "Удалить старые каталоги глав (00000_1_…, 00000_2_…) "
                      "в chapters/ перед записью. Рекомендуется при "
                      "повторном разборе — иначе старые главы останутся "
                      "рядом с новыми и могут попасть в конвейер"},
        ],
        "preset": {
            "title": "Разобрать исходник",
            "desc": "Автоматическая разбивка epub по TOC; txt не "
                    "принимается; исходник выбирается вручную",
            "overrides": {"mode": "toc"},
        },
        "simple": ["input"],
    },
    "translate_check": {
        "title": "Проверка перевода",
        "script": "translate_check.py",
        "build": build_translate_check,
        "fields": [
            {"name": "check_type", "label": "Тип файлов глав",
             "type": "select",
             "options": ["polished", "redacted", "translated"],
             "default": "polished",
             "help": "polished → сравнивается с redacted (соседняя "
                      "стадия) и chapter (оригинал); redacted → с "
                      "translated и chapter; translated → только с chapter"},
            {"name": "start", "label": "Начальная глава (ГЛАВЫ)",
             "type": "number", "default": ""},
            {"name": "end", "label": "Конечная глава", "type": "number", "default": ""},
            {"name": "exclude_words",
             "label": "Слова-исключения (через запятую)",
             "type": "text", "default": "",
             "help": "Пусто = ничего не исключается; если задано "
                      "TRANSLATE_CHECK_EXCLUDE_WORDS в .env — поле "
                      "заполняется оттуда"},
            {"name": "neighbor",
             "label": "Выбранная Стадия/Предыдущая Стадия (по занимаемому месту)",
             "type": "text", "default": "",
             "help": "Ожидаемый ratio с предыдущей стадией и допуск: "
                      "«1.0±0.05» (напр. polished/redacted); пусто = "
                      "встроенный дефолт; дефолт в .env — "
                      "TRANSLATE_CHECK_NEIGHBOR"},
            {"name": "original",
             "label": "Выбранная Стадия/Оригинал (по занимаемому месту)",
             "type": "text", "default": "",
             "help": "Ожидаемый ratio с оригиналом и допуск: "
                      "«2.1±0.5» (напр. polished/chapter); пусто = "
                      "встроенный дефолт; дефолт в .env — "
                      "TRANSLATE_CHECK_ORIGINAL"},
            {"name": "regexp_checks",
             "label": "Regexp-проверки (по одной на строку)",
             "type": "textarea", "rows": 4,
             "default": "",
             "help": "Каждая строка — regexp по тексту главы (multiline): "
                      "всё найденное — ошибка, проверяются ВСЕ строки "
                      "включая заголовок главы; ^/$ — начало/конец СТРОКИ; "
                      "комментарий в конце строки — « # …»; пусто = "
                      "дефолтные проверки (иероглифы, латиница, лишние "
                      "заголовки «Глава N» — первое совпадение не ошибка); "
                      "TRANSLATE_CHECK_REGEXP_CHECKS в .env — переносы "
                      "строк как «\n»"},
            {"name": "min_file_size",
             "label": "Минимальный размер файла (БАЙТЫ)",
             "type": "number", "default": "3072",
             "help": "Файл меньше этого размера — ошибка «слишком мал»; "
                      "пусто = встроенный дефолт 3072 Б"},
            {"name": "header_regexp",
             "label": "Заголовок главы (regexp)",
             "type": "text", "default": "",
             "help": "Regexp первой непустой строки главы: не совпало — "
                      "ошибка «Нет „Глава N“ в начале»; пусто = «Глава N» "
                      "без учёта регистра; ^ — начало строки"},
            {"name": "sequence_check",
             "label": "Проверять последовательность глав",
             "type": "bool", "default": True,
             "help": "Первое число в первой непустой строке должно быть "
                      "ровно на 1 больше предыдущей главы (N+1); "
                      "выключено — проверка пропускается"},
        ],
        "preset": {
            "title": "Проверить перевод",
            "desc": "Проверка объёмов по цепочке и текста глав "
                     "(дефолтные regexp-проверки); тип файлов выбирается",
            "overrides": {"check_type": "polished"},
        },
        "simple": ["check_type", "start", "end"],
    },
    "compile": {
        "title": "Компиляция TXT/EPUB/FB2",
        "script": "clean_and_compile.py",
        "build": build_clean_and_compile,
        "fields": [
            {"name": "mode", "label": "Режим",
             "type": "select",
             "options": ["txt", "txt-plain", "epub", "fb2", "epub-chunks",
                         "txt-chunks", "fb2-chunks"],
             "labels": {"txt": "TXT (Rulate)", "txt-plain": "TXT",
                        "epub": "EPUB", "fb2": "FB2",
                         "epub-chunks": "EPUB частями",
                         "txt-chunks": "TXT частями",
                         "fb2-chunks": "FB2 частями"},
             "default": "txt",
             "help": "TXT (Rulate) — заголовки «# [Название :|: N]» для "
                      "загрузки на rulate; TXT — обычный txt без "
                      "rulate-форматирования"},
            {"name": "start", "label": "Начальная глава (ГЛАВЫ)",
             "type": "number", "default": ""},
            {"name": "end", "label": "Конечная глава", "type": "number", "default": ""},
            {"name": "source_type", "label": "Тип файлов глав",
             "type": "select", "options": ["polished", "redacted", "translated", "chapter"],
             "default": "polished"},
            {"name": "chunk_size", "label": "Глав в части",
             "type": "number", "default": "",
             "help": "для *-chunks режимов; пусто = дефолт (epub=50, txt=500, fb2=50)"},
            {"name": "cover", "label": "Обложка",
             "type": "files", "dir": "source",
             "ext": [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"],
             "default": "",
             "help": "единая обложка для EPUB и FB2; пусто = без обложки; "
                      "варианты обложек загружаются через «Файлы»"},
            {"name": "epub_meta", "label": "Метаданные (YAML)",
             "type": "files", "dir": "source",
             "ext": [".yaml", ".yml"],
             "default": "",
             "help": "пусто = source/metadata.yaml; любой yaml/yml из source/ "
                      "(несколько наборов метаданных)"},
            {"name": "donate_file", "label": "Файл страницы поддержки",
             "type": "files", "dir": "source", "ext": [".txt"],
             "default": "",
             "help": "страница поддержки для EPUB/FB2; пусто = без страницы; "
                      "файл загружается через «Файлы» (source/)"},
        ],
        # только экспертный режим (без простого/пресета)
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
            {"name": "prompt_file",
             "label": "Общий промпт-файл (теги translate/redact/polish)",
             "type": "files", "dir": "prompts", "ext": [".txt"],
             "default": "",
             "help": "один файл с тегами <translate>/<redact>/<polish>; "
                      "пусто = авто (первый кандидат с тегами из prompts/); "
                      "недостающий тег стадии — предупреждение + встроенный "
                      "промпт"},
            {"name": "start", "label": "Начальная глава (ГЛАВЫ)",
             "type": "number", "default": ""},
            {"name": "end", "label": "Конечная глава", "type": "number", "default": ""},
            {"name": "jobs", "label": "Потоков (1–16)",
             "type": "number", "default": "4", "min": 1, "max": 16,
             "help": "СУММАРНО одновременных LLM-запросов: распределяются "
                      "на параллельные главы и чанки внутри главы "
                      "(глав много — потоки идут на главы, мало — "
                      "свободные уходят внутрь главы)"},
            {"name": "threads", "type": "hidden", "default": "",
             "noenv": True},
            {"name": "ner_min_count", "label": "Мин. count для глоссария "
             "({ner_block})",
             "type": "number", "default": "0",
             "help": "термины с count ниже порога НЕ попадают в {ner_block}; "
                      "0 — фильтр выключен (все найденные)"},
            {"name": "names_min_count", "label": "Мин. count для имён "
             "({female_names}/{male_names})",
             "type": "number", "default": "10",
             "help": "имена с count ниже порога НЕ попадают в справочник "
                      "полов; 0 — фильтр выключен"},
            {"name": "timeout", "label": "Таймаут LLM-запроса, сек",
             "type": "number", "default": "300"},
            {"name": "max_retries", "label": "Повторы",
             "type": "number", "default": "3",
             "help": "попытки виртуального потока на один LLM-запрос "
                      "(сеть/стрим)"},
            {"name": "temperature", "label": "Температура (пусто = сервер)",
             "type": "text", "default": ""},
            {"name": "reasoning_effort", "label": "Reasoning effort",
             "type": "text",
             "help": "пусто — не передаётся; none — отключает; low/medium/high/xhigh/max — как есть",
             "default": ""},
        ],
        "preset": {
            "title": "Перевести книгу",
            "desc": "Полный цикл: перевод → редактура → полировка "
                    "всех глав, промпты из prompts/",
        },
        "simple": ["action", "prompt_file"],
    },
    "ner": {
        "title": "Создание глоссария (LLM)",
        "script": "ner.py",
        "build": build_ner,
        "fields": _LLM_FIELDS + [
            {"name": "mode", "label": "Режим",
             "type": "select",
             "options": ["extract", "finetune"],
             "default": "extract",
             "labels": {
                 "extract": "Новый глоссарий (автоматический)",
                 "finetune": "Дообучение",
             },
             "help": "новый глоссарий: извлечение терминов в ner.json. "
                      "дообучение: термины добавятся к существующему ner.json. "
                      "Вход: выбранный txt или сборка глав chapters/*/chapter.txt "
                      "в память (диапазон ниже, пусто = все главы)."},
            {"name": "file", "label": "Входной txt",
             "type": "files", "dir": "", "ext": [".txt"], "default": "",
             "help": "необязателен: выбран — работаем с ним; пусто — сборка "
                      "глав chapters/*/chapter.txt в память (диапазон ниже)"},
            {"name": "start", "label": "Начальная глава (ГЛАВЫ)",
             "type": "number", "default": "",
             "help": "когда входной файл не выбран (сборка глав); пусто = с первой"},
            {"name": "end", "label": "Конечная глава (ГЛАВЫ)",
             "type": "number", "default": "",
             "help": "когда входной файл не выбран (сборка глав); пусто = до последней"},
            {"name": "ner_file", "label": "Глоссарий ner.json",
             "type": "files", "dir": "", "ext": [".json"], "default": "ner.json",
             "help": "«новый глоссарий» — создастся новый; «дообучение» — термины добавятся к существующему"},
            {"name": "prompt_file", "label": "Промпт-файл (теги pass1/pass2)",
             "type": "files", "dir": "prompts", "ext": [".txt"],
             "default": "ner_prompt.txt"},
            {"name": "threads", "label": "Потоков (1–16)",
             "type": "number", "default": "4", "min": 1, "max": 16},
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
            {"name": "two_pass", "label": "Двухпроходная схема",
             "type": "bool", "default": True},
            {"name": "keep_fields", "label": "Поля в голосование (через запятую)",
             "type": "text", "default": "",
             "help": "Пусто = голосуют translation/type/pinyin; notes, context, translated_context не голосуют. Пример: notes,context"},
            {"name": "save_interval", "label": "Интервал сохранения ner.json",
             "type": "number", "default": "10",
             "help": "каждые N чанков — промежуточный снапшот глоссария. "
                      "Возобновление с места остановки убрано: каждый "
                      "запуск идёт с первого чанка"},
            {"name": "retries", "label": "Повторные попытки",
             "type": "number", "default": "3",
             "help": "общее число попыток LLM на чанк: сеть/стрим и "
                      "невалидный формат ответа считаются одинаково"},
            {"name": "timeout", "label": "Таймаут запроса, сек",
             "type": "number", "default": "300"},
        ],
        "preset": {
            "title": "Новый глоссарий",
            "desc": "Извлечение терминов в ner.json: входной txt или "
                    "сборка глав в память (все главы)",
        },
        "simple": ["mode", "file", "prompt_file", "two_pass"],
    },
    "ner_check": {
        "title": "Проверка глоссария (LLM)",
        "script": "ner_check.py",
        "build": build_ner_check,
        "fields": _LLM_FIELDS + [
            {"name": "input", "label": "Входной JSON (ner.json)",
             "type": "files", "dir": "", "ext": [".json"], "default": "ner.json"},
            {"name": "review", "label": "Review-файл правок",
             "type": "text", "default": "ner_review.json"},
            {"name": "prompt_file", "label": "Промпт-файл",
             "type": "files", "dir": "prompts", "ext": [".txt"],
             "default": "ner_check_prompt.txt",
             "autofile": "prompts/ner_check_prompt.txt",
             "help": "Теги: <prompt_check> — проверка выбранных типов, "
                      "<prompt_rag> — точечная RAG-проверка; комментарии "
                      "вне тегов — через #; автоподхват ner_check_prompt.txt"}, 
            {"name": "rag_budget", "label": "RAG: бюджет на термин, СИМВОЛЫ",
             "type": "number", "default": "65536",
             "help": "На ОДИН термин: промпт + фрагменты ≤ бюджету; каждый "
                      "термин — отдельный LLM-запрос (параллельно, "
                      "«Потоков (1–16)»); фрагменты — равномерно по "
                      "книге (FTS5, чанки 1000 симв.), влезают в остаток "
                      "бюджета после промпта"},
            {"name": "passes", "label": "Режимы",
             "labels": {"whole": "Выбранные типы (одновременно)",
                         "types": "Выбранные типы (по отдельности)",
                         "rag": "Точечно по списку (RAG)"},
             "type": "select", "options": ["whole", "types", "rag"],
             "default": "whole",
             "help": "одновременно — весь список выбранных типов разом "
                      "(батчи по бюджету); по отдельности — каждый тип "
                      "отдельно; rag — точечная проверка списка терминов "
                      "по FTS5-фрагментам книги"}, 
            {"name": "batch_size", "label": "Бюджет пакета, СИМВОЛЫ",
             "type": "number", "default": "196608"},
            {"name": "threads", "label": "Потоков (1–16)",
             "type": "number", "default": "1", "min": 1, "max": 16,
             "help": "батчи, типы и RAG-термины выполняются параллельно "
                      "(вместо последовательного прохода)"},
            {"name": "count_threshold", "label": "Порог count",
             "type": "number", "default": "0"},
            # RAG-режим: список терминов, сборка глав в память,
            # бюджет, сохранение, промпт-файл (поля видны только в rag)
            {"name": "rag_terms", "label": "RAG: список терминов",
             "type": "textarea", "default": "",
             "help": "Каждый термин с новой строки; тип/перевод "
                      "подтягиваются из ner.json; нужен режим «rag»"},
            {"name": "rag_source_type", "label": "RAG: тип исходного файла",
             "type": "select",
             "options": ["chapter", "translated", "redacted", "polished"],
             "default": "chapter",
             "help": "Из какого файла главы собирается текст книги "
                      "для FTS5-поиска (сборка в память, файл не пишется)"},
            {"name": "save_interval", "label": "Сохранять каждые N терминов",
             "type": "number", "default": "0",
             "help": "RAG: review-файл сохраняется каждые N терминов "
                      "(0 = только в конце)"},
            {"name": "start", "label": "Начальная глава (ГЛАВЫ)",
             "type": "number", "default": ""},
            {"name": "end", "label": "Конечная глава", "type": "number", "default": ""},

            # types/fields — скрытые: значения пишет виджет чипсов
            # (типы и поля из ner.json), buildParams собирает их в params;
            # noenv — .env не предзаполняет чипсы (дефолт: все типы и
            # term+type+translation+notes+context, если есть в данных)
            {"name": "types", "type": "hidden", "default": "",
             "noenv": True},
            {"name": "fields", "type": "hidden", "default": "",
             "noenv": True},
            # --apply/--auto-apply/--no-bak убраны из Запусков:
            # применяется только в «Проверках» проекта
            # (/api/ner/review/apply шлёт apply напрямую)
            {"name": "temperature", "label": "Температура (пусто = сервер)",
             "type": "text", "default": ""},
            {"name": "reasoning_effort", "label": "Reasoning effort",
             "type": "text",
             "help": "пусто — не передаётся; none — отключает; low/medium/high/xhigh/max — как есть",
             "default": ""},
            {"name": "max_tokens", "label": "Max tokens (серверный лимит), ТОКЕНЫ",
             "type": "number", "default": "65536"},
            {"name": "timeout", "label": "Таймаут, сек", "type": "number", "default": "300"},
            {"name": "max_retries", "label": "Повторы", "type": "number", "default": "3"},
        ],
        "preset": {
            "title": "Проверить глоссарий",
            "desc": "LLM-проверка ner.json по выбранным типам, "
                    "правки не применяются",
        },
        "simple": ["prompt_file", "passes"],
    },
    "translate_check_llm": {
        "title": "Проверка перевода (LLM)",
        "script": "translate_check_llm.py",
        "build": build_translate_check_llm,
        "fields": _LLM_FIELDS + [
            {"name": "type", "label": "Тип файлов глав",
             "type": "select", "options": ["polished", "redacted", "translated"],
             "default": "polished"},
            {"name": "start", "label": "Начальная глава (ГЛАВЫ)",
             "type": "number", "default": ""},
            {"name": "end", "label": "Конечная глава", "type": "number", "default": ""},
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
            {"name": "threads", "label": "Параллельные пакеты (1–16)", "type": "number", "default": "4", "min": 1, "max": 16},
            {"name": "max_fixes_per_chapter", "label": "Лимит правок на главу (0 = нет)",
             "type": "number", "default": "0"},
            {"name": "min_fix_length", "label": "Мин. длина правки, СИМВОЛЫ",
             "type": "number", "default": "0"},
            {"name": "max_changed_chars", "label": "Макс. изменённых символов, СИМВОЛЫ",
             "type": "number", "default": "0"},
        ],
        "preset": {
            "title": "Проверить перевод (LLM)",
            "desc": "LLM-проверка polished всех глав, один проход, "
                    "дефолтные настройки",
        },
        "simple": ["type", "two_pass", "prompt_file"],
    },
    "translate_quality": {
        "title": "Оценка перевода (LLM)",
        "script": "translate_quality.py",
        "build": build_translate_quality,
        "fields": _LLM_FIELDS + [
            {"name": "start", "label": "Начальная глава (ГЛАВЫ)",
             "type": "number", "default": ""},
            {"name": "end", "label": "Конечная глава",
             "type": "number", "default": ""},
            {"name": "type", "label": "Тип файлов глав",
             "type": "select",
             "options": ["chapter", "translated", "redacted", "polished"],
             "default": "polished",
             "help": "какой файл главы сравнивается с оригиналом: "
                      "подставляется в {translated_text} промпта, "
                      "chapter.txt — в {original_text}"},
            {"name": "prompt_file", "label": "Промпт-файл",
             "type": "files", "dir": "prompts", "ext": [".txt"],
             "default": "translate_quality_prompt.txt",
             "autofile": "prompts/translate_quality_prompt.txt",
             "help": "тег <prompt_assessment> (между тегами — комменты); "
                      "плейсхолдеры {original_text} и {translated_text}; "
                      "автоподхват translate_quality_prompt.txt"},
            {"name": "output", "label": "Выходной файл",
             "type": "text", "default": "translation_quality_assessment.md",
             "help": "md-отчёт в корне проекта; виден на вкладке "
                      "«Проверки» → «Оценка перевода (LLM)»"},
            {"name": "budget", "label": "Бюджет запроса, СИМВОЛЫ",
             "type": "number", "default": "200000",
             "help": "главы (содержимое, промпт НЕ входит); если не влезает — пакет "
                      "обрезается до целого количества глав (первые "
                      "диапазона), отсечённые указываются в отчёте"},
            {"name": "temperature", "label": "Температура (пусто = сервер)",
             "type": "text", "default": ""},
            {"name": "reasoning_effort", "label": "Reasoning effort",
             "type": "text",
             "help": "пусто — не передаётся; none — отключает; low/medium/high/xhigh/max — как есть",
             "default": ""},
            {"name": "max_retries", "label": "Повторы",
             "type": "number", "default": "3"},
            {"name": "timeout", "label": "Таймаут LLM-запроса, сек",
             "type": "number", "default": "300"},
        ],
        "preset": {
            "title": "Оценить перевод",
            "desc": "Один LLM-запрос по главам диапазона: оригинал "
                     "vs перевод (polished), дефолтный промпт и бюджет",
            "overrides": {"type": "polished"},
        },
        "simple": ["start", "end", "type", "prompt_file", "budget"],
    },
    "wiki": {
        "title": "Создание Wiki (LLM)",
        "script": "wiki.py",
        "build": build_wiki,
        "fields": _LLM_FIELDS + [
            {"name": "start", "label": "Начальная глава (ГЛАВЫ)",
             "type": "number", "default": "",
             "help": "при источнике «Собрать из глав»; пусто = с первой"},
            {"name": "end", "label": "Конечная глава (ГЛАВЫ)",
             "type": "number", "default": "",
             "help": "при источнике «Собрать из глав»; пусто = до последней"},
            {"name": "source", "label": "Источник текста",
             "type": "select",
             "options": ["txt", "chapters"],
             "labels": {"txt": "Готовый txt", "chapters": "Собрать из глав"},
             "default": "chapters",
             "help": "txt — готовый скомпилированный файл; «собрать из глав» — "
                      "склейка chapters/* в память (как в Создании глоссария)"},
            {"name": "file", "label": "Входной txt новеллы (перевод)",
             "type": "files", "dir": "", "ext": [".txt"], "default": "",
             "help": "нужен при источнике «Готовый txt»"},
            {"name": "type", "label": "Тип файлов глав",
             "type": "select", "options": ["chapter", "translated", "redacted", "polished"],
             "default": "chapter",
             "help": "при источнике «Собрать из глав»"},
            {"name": "ner_file", "label": "NER JSON",
             "type": "files", "dir": "", "ext": [".json"], "default": "ner.json"},
            {"name": "output", "label": "Выходной файл", "type": "text", "default": "wiki.md"},
            {"name": "as_chapter", "label": "Сохранить как главу",
             "type": "bool", "default": False,
             "help": "вместо файла — дополнительная последняя глава "
                      "chapters/00000_{N+1}_Wiki_Новеллы/, название "
                      "«Wiki Новеллы» простым текстом"},
            {"name": "save_type", "label": "Тип файла вики-главы",
             "type": "select",
             "options": ["translated", "redacted", "polished"],
             "default": "polished",
             "help": "для «Сохранить как главу вики»; polished — как "
                      "компиляция по умолчанию; chapter.txt не пишется"},
            {"name": "format", "label": "Формат",
             "type": "select",
             "options": ["md", "rulate-md", "rulate-html"],
             "labels": {"md": "Обычный Markdown",
                        "rulate-md": "Rulate (Markdown)",
                        "rulate-html": "Rulate (HTML)"},
             "default": "md",
             "help": "rulate-html: заголовки — <span style=font-size>, "
                      "списки <ul>, разделители <hr />"},
            {"name": "toc", "label": "Оглавление",
             "type": "bool", "default": True,
             "help": "обычный режим; Rulate — всегда без оглавления"},
            {"name": "toc_links", "label": "Якоря-ссылки в оглавлении",
             "type": "bool", "default": True,
             "help": "обычный режим; ссылки [термин](#якорь) на статью"},
            {"name": "prompt_file", "label": "Промпт (тег <prompt_wiki_article>)",
             "type": "files", "dir": "prompts", "ext": [".txt"],
             "default": "wiki_prompt.txt"},
            {"name": "top", "label": "Макс. терминов", "type": "number", "default": "80"},
            {"name": "min_count", "label": "Мин. частота термина", "type": "number", "default": "2"},
            # типы — скрытые: значение пишет виджет чипсов (как в
            # ner_check); пусто = все типы (по умолчанию выбраны все)
            {"name": "types", "type": "hidden", "default": "",
             "noenv": True},
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
            {"name": "temperature", "label": "Температура (пусто = сервер)",
             "type": "text", "default": ""},
            {"name": "thinking", "label": "Reasoning effort",
             "type": "text",
             "help": "пусто — не передаётся; none — отключает; low/medium/high/xhigh/max — как есть",
             "default": ""},
            {"name": "retries", "label": "Повторы", "type": "number", "default": "3"},
            {"name": "timeout", "label": "Таймаут запроса, сек", "type": "number", "default": "300"},
            {"name": "threads", "label": "Потоков (1–16)", "type": "number", "default": "4", "min": 1, "max": 16},
        ],
        "preset": {
            "title": "Создать вики",
            "desc": "Генерация wiki.md: ner.json + перевод, "
                    "дефолтные настройки",
        },
        "simple": ["source", "file", "type", "prompt_file", "top",
                    "min_count", "format", "as_chapter", "save_type"],
    },
    "batch_replace": {
        "title": "Массовые замены",
        "script": "batch_replace.py",
        "build": build_batch_replace,
        "fields": [
            {"name": "replacements",
             "label": "Regexp-замены (по одной на строку)",
             "type": "textarea", "rows": 5, "default": "",
             "help": "Формат: паттерн -> замена (regexp, спецсимволы "
                      "работают). Пустая правая часть — УДАЛЕНИЕ: "
                      "«<div>.*?</div> ->». «^»/«$» — начало/конец "
                      "СТРОКИ; пробелы в паттерне значимы; флаги "
                      "в конце строки (до комментария): « |i» (регистр), "
                      "« |r» (regexp — всегда). "
                      "Примеры: «Глава \\d+ -> Глава №\\g<0>», "
                      "«\\s+ -> » (сжать пробелы), «^  ->» (отступ "
                      "строки), «^(第\\d+章.*)\\n(?=\\1$) ->» "
                      "(строка-дубликат заголовка главы). Строки с # — "
                      "комментарии; комментарий в конце строки — « # …»; "
                      "BATCH_REPLACE_REPLACEMENTS в .env — переносы "
                      "строк как «\\n»"},
            {"name": "type", "label": "Тип файлов глав",
             "type": "select", "options": ["polished", "redacted", "translated", "chapter"],
             "default": "polished"},
            {"name": "start", "label": "Начальная глава (ГЛАВЫ)",
             "type": "number", "default": ""},
            {"name": "end", "label": "Конечная глава", "type": "number", "default": ""},
        ],
        # только экспертный режим (без простого/пресета)
    }, 
}

# Порядок отображения стадий в «Запусках» (логика конвейера: разбор →
# NER → проверка глоссария → конвейер → проверка → правки → замены →
# компиляция → вики). Слаги — контракт API, не менять.
STAGE_ORDER: list[str] = [
    "epub", "ner", "ner_check", "pipeline", "translate_check",
    "translate_check_llm", "batch_replace", "translate_quality",
    "compile", "wiki",
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

    script может быть "x.py" (cli/x.py) или "папка/файл.py"
    (относительно корня репо — web-оркестраторы)."""
    spec = STAGE_SPECS.get(key)
    if spec is None:
        return None
    rel = spec["script"]
    if "/" in rel:
        return repo_root / rel
    return repo_root / "cli" / rel

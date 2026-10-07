#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stages.py — спеки стадий web-интерфейса: метаданные полей в core/settings.py.

Каждая стадия: spec (title/script/build + fields) + build_command(form, ctx) →
argv. Поля формы (и их дефолты, метки, подсказки) берутся из реестра настроек
в порядке реестра — здесь остаётся только сборка argv.
- в argv едут только применимые поля: build_command пропускает форму через
  core.settings.applicable_form (when/when_any/when_set у настроек), поэтому
  режим стадии больше не размножается ветками в сборке — RAG-поля в whole,
  расширенный контекст в действиях 1-8 и т.п. отсекаются реестром;
- cwd = папка проекта; python = sys.executable; скрипт = REPO/cli/xxx.py.
- Единицы в метках — как в help скриптов (СИМВОЛЫ/ТОКЕНЫ/ГЛАВЫ).
- LLM-настройки (сервер, модель, ключ, температура, таймауты, повторы,
  потоки) в форме запусков одним полем «Профиль LLM»: стадия берёт их из
  выбранного профиля (или общего конфига — это профиль General). build_command
  подставляет значения профиля в имена полей стадии (jobs ← THREADS,
  retries ← MAX_RETRIES), поэтому сборка argv ниже не изменилась.
- режимы «Простой/Экспертный» и пресеты убраны: у всех стадий одна форма.
"""
from __future__ import annotations

import logging
from pathlib import Path

from core import settings as core_settings

log = logging.getLogger("web.stages")

# ── типы полей ─────────────────────────────────────────────────────────
# text / number / bool / select / files (select из папки проекта) /
# range (start-end) / password


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
    """LLM-параметры в argv: они приходят только из общего конфига.

    Форма запуска LLM-полей не содержит — build_command подставляет в неё
    глобальные значения (core.settings.with_llm), поэтому «свой сервер у
    стадии» больше не существует и сравнивать не с чем.
    Пустой результат — скрипт сам найдёт .env или попросит ввод.
    """
    argv = []
    host = str(form.get("host") or "").strip()
    model = str(form.get("model") or "").strip()
    api_key = str(form.get("api_key") or "").strip()
    # ключ не попадает в argv (виден в ps) — он уходит в окружение процесса
    # через ctx["_llm_api_key"] (JobManager.start), скрипты читают LLM_API_KEY.
    if api_key and isinstance(ctx, dict):
        ctx["_llm_api_key"] = api_key
    if host:
        argv += ["--host", host]
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
    # «Замены и очистки» из формы epub убраны: замены после разбивки делает
    # отдельная стадия batch_replace, --clean-re и --skip остаются CLI-флагами
    # (своих настроек в реестре нет)
    for p in _epub_lines(form.get("split_patterns")):
        argv += ["--split-re", p]
    if form.get("chunk_size") not in (None, ""):
        argv += ["--chunk-size", str(form["chunk_size"])]
    # маска нужна и в chunk-режиме, и при переопределении названий
    if (mode == "chunk" or form.get("rename_chapters")) and form.get(
            "chunk_mask"):
        argv += ["--chunk-mask", str(form["chunk_mask"])]
    if form.get("rename_chapters"):
        argv.append("--rename-chapters")
    if form.get("title_limit") not in (None, ""):
        argv += ["--title-limit", str(form["title_limit"])]
    if form.get("num_offset") not in (None, ""):
        argv += ["--num-offset", str(form["num_offset"])]
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
    if form.get("sequence_check", True) in (False, "0", 0):
        argv.append("--no-sequence-check")
    return argv


def build_clean_and_compile(form: dict, ctx: dict) -> list[str]:
    mode = str(form.get("mode", "txt"))
    argv = ["cli/clean_and_compile.py", "--mode", mode]
    argv += _range_argv("start", form)
    if form.get("source_type"):
        argv += ["--source-type", str(form["source_type"])]
    # разбивка на части по «Глав в части» (>0) — для любого режима;
    # пусто/0 — одна сборка без разбивки
    if form.get("chunk_size") not in (None, "", 0):
        argv += ["--chunk-size", str(form["chunk_size"])]
    # --tmp-dir не передаётся: рабочие файлы — tmp/ проекта (дефолт CLI)
    # обложка/метаданные/донат относятся только к книжным форматам
    # (в txt-режимах форма их прячет — и в argv они не попадают)
    book = mode in ("epub", "fb2")
    if not book:
        argv += ["--no-cover", "--no-donate"]
        return argv
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
    # пороги count: ner_block и имена (пусто/0 = фильтр выключен);
    # поля {ner_block} — чипсы формы (hidden ner_fields), общие для
    # всех стадий конвейера
    for flag, name in (("--ner_min_count", "ner_min_count"),
                       ("--names_min_count", "names_min_count")):
        if form.get(name) not in (None, ""):
            argv += [flag, str(form[name])]
    nf = str(form.get("ner_fields") or "").strip()
    if nf:
        argv += ["--ner_fields", nf]
    if form.get("timeout") not in (None, ""):
        argv += ["--timeout", str(form["timeout"])]
    if form.get("max_retries") not in (None, ""):
        argv += ["--max_retries", str(form["max_retries"])]
    if form.get("temperature") not in (None, ""):
        argv += ["--temperature", str(form["temperature"])]
    # единый общий промпт-файл (теги <translate>/<redact>/<polish>);
    # пусто = авто (кандидат с тегами из prompts/)
    if form.get("prompt_file"):
        argv += ["--prompt_file", str(form["prompt_file"])]
    # расширенный контекст (действие 9): словарь/правила/примеры
    # из source/ + общий бюджет запроса (ТОКЕНЫ); файлы приходят
    # как source/имя
    for name, flag in (("dict_file", "--dict_file"),
                       ("rules_file", "--rules_file"),
                       ("examples_file", "--examples_file")):
        if form.get(name) not in (None, ""):
            argv += [flag, str(form[name])]
    for name, flag in (("fewshot_k", "--fewshot_k"),
                       ("fewshot_threshold", "--fewshot_threshold")):
        if form.get(name) not in (None, ""):
            argv += [flag, str(form[name])]
    # бюджет запроса (ТОКЕНЫ): 0 = без ограничения — флаг не передаётся
    if str(form.get("request_budget") or "").strip() not in ("", "0"):
        argv += ["--request_budget", str(form["request_budget"])]
    # размер чанка перевода/полировки (ТОКЕНЫ); редактура — глава целиком
    if form.get("chunk_size") not in (None, ""):
        argv += ["--chunk_size", str(form["chunk_size"])]
    argv += _llm_argv(form, ctx, "pipeline")
    return argv


def build_ner(form: dict, ctx: dict) -> list[str]:
    """Стадия 2 — извлечение NER (ner.py).

    Вход — всегда сборка глав в память (--compile_chapters,
    опционально start/end). Глоссарий — канонический ner.json:
    отсутствует — создаётся новый, существует — дообучение
    (ner.py сам загружает существующий файл). LLM-флаги — для
    обоих случаев.
    """
    argv = ["cli/ner.py", "--compile_chapters"]
    argv += _range_argv("start", form)
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
    if form.get("two_pass"):
        argv.append("--two-pass")
    if form.get("keep_fields"):
        argv += ["--keep-fields", str(form["keep_fields"])]
    if form.get("context_max_len") not in (None, ""):
        argv += ["--context_max_len", str(form["context_max_len"])]
    # не голосующие поля: режим выбора значения и его длина
    if form.get("non_voted_mode") not in (None, ""):
        argv += ["--non_voted_mode", str(form["non_voted_mode"])]
    if form.get("non_voted_max_len") not in (None, ""):
        argv += ["--non_voted_max_len", str(form["non_voted_max_len"])]
    argv += _llm_argv(form, ctx, "ner")
    return argv


def build_ner_check(form: dict, ctx: dict) -> list[str]:
    """Стадия n — проверка глоссария (ner_check.py).

    Режим (NER_CHECK_PASSES) разводит поля по-настоящему: RAG-поля и диапазон
    глав приезжают в форме только в rag, поля пакетной проверки — только в
    whole/types (core.settings.applicable_form).
    """
    argv = ["cli/ner_check.py"]
    # «Проверка» проекта применяет принятые правки (--apply): ner.json и
    # ner_review.json — каноны (дефолты CLI), LLM в этом режиме не зовётся —
    # команда собирается целиком из флагов применения, без остальных полей
    if ctx.get("review_apply"):
        for flag in ("apply", "auto_apply", "dry_run"):
            if form.get(flag):
                argv.append(f"--{flag.replace('_', '-')}")
        if form.get("no_bak"):
            argv.append("--no-bak")
        return argv
    # вход и review — канонические ner.json / ner_review.json
    # (выбор файлов из web убран)
    if form.get("prompt_file"):
        argv += ["--prompt_file", str(form["prompt_file"])]
    if form.get("passes"):
        argv += ["--passes", str(form["passes"])]
    # RAG-режим: список терминов, тип исходного файла и диапазон глав книги,
    # бюджет на один термин. RAG-промпт — тот же «Промпт-файл» (тег
    # <prompt_rag>); отдельного --rag_prompt_file нет: CLI берёт --prompt_file
    if form.get("rag_terms"):
        argv += ["--rag_terms", str(form["rag_terms"])]
    if form.get("rag_source_type"):
        argv += ["--rag_source_type", str(form["rag_source_type"])]
    argv += _range_argv("start", form)
    if form.get("rag_budget") not in (None, ""):
        argv += ["--rag_budget", str(form["rag_budget"])]
    if form.get("save_interval") not in (None, ""):
        argv += ["--save-interval", str(form["save_interval"])]
    # пакетная проверка: типы, бюджет пакета, порог count
    if form.get("types"):
        argv += ["--types", str(form["types"])]
    if form.get("batch_size") not in (None, ""):
        argv += ["--batch_size", str(form["batch_size"])]
    if form.get("threads") not in (None, ""):
        argv += ["--threads", str(form["threads"])]
    if form.get("count_threshold") not in (None, ""):
        argv += ["-c", str(form["count_threshold"])]
    # замок: зафиксированные записи с проверки снимаются целиком (флаг без
    # значения); снятая настройка — проверяются как раньше
    if form.get("skip_locked"):
        argv.append("--skip_locked")
    if form.get("fields"):
        argv += ["--fields", str(form["fields"])]
    if form.get("temperature") not in (None, ""):
        argv += ["--temperature", str(form["temperature"])]
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
    # «Проверка» проекта применяет принятые правки (--apply): LLM не зовётся,
    # из полей формы нужны только тип файлов главы и флаги применения
    if ctx.get("review_apply"):
        if form.get("type"):
            argv += ["--type", str(form["type"])]
        for flag in ("apply", "auto_apply", "dry_run"):
            if form.get(flag):
                argv.append(f"--{flag.replace('_', '-')}")
        if form.get("no_bak"):
            argv.append("--no-bak")
        return argv
    argv += _range_argv("translate_check_llm", form)
    # папка глав всегда ./chapters (дефолт скрипта, cwd = проект)
    if form.get("type"):
        argv += ["--type", str(form["type"])]
    if form.get("two_pass"):
        argv.append("--two_pass")
    if form.get("context_budget") not in (None, ""):
        argv += ["--context_budget", str(form["context_budget"])]
    # review — канонический translate_check_llm_review.json
    # (выбор файла из web убран; его же читает «Правки»)
    if form.get("prompt_file"):
        argv += ["--prompt_file", str(form["prompt_file"])]
    if form.get("temperature") not in (None, ""):
        argv += ["--temperature", str(form["temperature"])]
    for name, flag in (("max_retries", "--max_retries"),
                       ("timeout", "--timeout"),
                       ("retry_empty", "--retry_empty"),
                       ("threads", "--threads"),
                       ("max_fixes_per_chapter", "--max_fixes_per_chapter"),
                       ("min_fix_length", "--min_fix_length"),
                       ("max_changed_chars", "--max_changed_chars")):
        if form.get(name) not in (None, ""):
            argv += [flag, str(form[name])]
    # единый таймаут: стриму — то же значение (форма показывает
    # одно поле «Таймаут, сек»)
    if form.get("timeout") not in (None, ""):
        argv += ["--stream_timeout", str(form["timeout"])]
    argv += _llm_argv(form, ctx, "translate_check_llm")
    return argv


def build_translate_quality(form: dict, ctx: dict) -> list[str]:
    """Стадия «Оценка перевода (LLM)» — translate_quality.py.

    Два режима: range — один LLM-запрос по пакету глав диапазона; chunks — чанки
    по N целых глав (глава крупнее бюджета режется по абзацам), каждый чанк —
    отдельный запрос, их отчёты сворачиваются LLM в заключение. Тип файлов глав →
    {translated_text}, chapter.txt → {original_text}; бюджет — ТОКЕНЫ — оценка
    estimate_tokens (главы; промпт НЕ входит) — он же режет чанки и сводки
    свёртки. Выход — md-отчёт tmp/translation_quality_assessment.md
    (фиксирован), артефакты чанков — tmp/quality/.
    """
    argv = ["cli/translate_quality.py"]
    argv += _range_argv("translate_quality", form)
    if form.get("type"):
        argv += ["--type", str(form["type"])]
    if form.get("prompt_file"):
        argv += ["--prompt_file", str(form["prompt_file"])]
    # выходной файл фиксирован: tmp/translation_quality_assessment.md
    if form.get("budget") not in (None, ""):
        argv += ["--budget", str(form["budget"])]
    for name, flag in (("mode", "--mode"), ("chunk_size", "--chunk_size"),
                       ("chunks", "--chunks"), ("sample", "--sample"),
                       ("overlap", "--overlap"), ("threads", "--threads")):
        if form.get(name) not in (None, ""):
            argv += [flag, str(form[name])]
    if form.get("temperature") not in (None, ""):
        argv += ["--temperature", str(form["temperature"])]
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
    # ner_file не передаётся: глоссарий — всегда ner.json (дефолт CLI)
    if form.get("prompt_file"):
        argv += ["--prompt_file", str(form["prompt_file"])]
    for name, flag in (("top", "--top"), ("min_count", "--min-count"),
                       ("context_chunks", "--context-chunks"),
                       ("near_distance", "--near-distance"),
                       ("chunk_size", "--chunk-size"),
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
    argv += _llm_argv(form, ctx, "wiki")
    return argv









# ── пресеты «Простого режима» в Запусках ───────────────────────────────
# Пресет: title (название карточки) + desc (1–2 строки «что будет
# сделано») + overrides (отклонения от дефолтов полей формы). Параметры
# простого режима — preset_params(spec): непустые дефолты полей формы +
# overrides; LLM-поля (host/model/api_key) имеют пустые дефолты и в
# params не попадают — скрипты сами берут сервер из .env.



STAGE_SPECS: dict[str, dict] = {
    "epub": {
        "title": core_settings.STAGE_TITLES["epub"],
        "script": "epub_to_chapters.py",
        "build": build_epub_to_chapters,
        "autosave": True,  # настройки формы — сразу в localStorage
        "fields": core_settings.form_fields("epub"),
    },
    "translate_check": {
        "title": core_settings.STAGE_TITLES["translate_check"],
        "script": "translate_check.py",
        "build": build_translate_check,
        "fields": core_settings.form_fields("translate_check"),
    },
    "compile": {
        "title": core_settings.STAGE_TITLES["compile"],
        "script": "clean_and_compile.py",
        "build": build_clean_and_compile,
        "fields": core_settings.form_fields("compile"),
    },
    "pipeline": {
        "title": core_settings.STAGE_TITLES["pipeline"],
        "preview": True,   # кнопка «Предпросмотр запроса»
        "script": "web/pipeline.py",
        "build": build_pipeline,
        "fields": core_settings.form_fields("pipeline"),
    },
    "ner": {
        "title": core_settings.STAGE_TITLES["ner"],
        "preview": True,   # кнопка «Предпросмотр запроса»
        "script": "ner.py",
        "build": build_ner,
        "fields": core_settings.form_fields("ner"),
    },
    "ner_check": {
        "title": core_settings.STAGE_TITLES["ner_check"],
        "preview": True,   # кнопка «Предпросмотр запроса»
        "script": "ner_check.py",
        "build": build_ner_check,
        "fields": core_settings.form_fields("ner_check"),
    },
    "translate_check_llm": {
        "title": core_settings.STAGE_TITLES["translate_check_llm"],
        "preview": True,   # кнопка «Предпросмотр запроса»
        "script": "translate_check_llm.py",
        "build": build_translate_check_llm,
        "fields": core_settings.form_fields("translate_check_llm"),
    },
    "translate_quality": {
        "title": core_settings.STAGE_TITLES["translate_quality"],
        "preview": True,   # кнопка «Предпросмотр запроса»
        "script": "translate_quality.py",
        "build": build_translate_quality,
        "fields": core_settings.form_fields("translate_quality"),
    },
    "wiki": {
        "title": core_settings.STAGE_TITLES["wiki"],
        "preview": True,   # кнопка «Предпросмотр запроса»
        "script": "wiki.py",
        "build": build_wiki,
        "fields": core_settings.form_fields("wiki"),
    },
    "batch_replace": {
        "title": core_settings.STAGE_TITLES["batch_replace"],
        "script": "batch_replace.py",
        "build": build_batch_replace,
        "fields": core_settings.form_fields("batch_replace"),
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
    """argv для стадии (относительные пути — cwd=проект).

    Профиль LLM — поле формы `profile`: у каждой LLM-стадии свой выбор, он
    живёт в браузере проекта и приезжает вместе с формой.
    """
    spec = STAGE_SPECS.get(key)
    if spec is None:
        raise ValueError(f"Нет спеки стадии: {key}")
    # поля режима из реестра (when/when_any/when_set): браузер помнит значения
    # всех полей, но в argv стадии едут только касающиеся текущего режима
    form = core_settings.applicable_form(
        key, core_settings.with_llm(key, form))
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

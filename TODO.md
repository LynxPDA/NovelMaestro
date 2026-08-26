# TODO — план: режимы «Создание глоссария», диапазон глав, серверы по скриптам

Статус: **готово (ожидает коммита и push).**

---

## Исследование (диагностика по пунктам запроса)

1. **«Запуски → Создание глоссария» (стадия ner, cli/ner.py)**:
   - режим `compile` (`--compile_chapters`) пишет временный
     `compiled_chapters.txt` в корень проекта и читает его обратно —
     файл не нужен, можно склеивать главы в память;
   - диапазона глав нет: `compile_chapter_texts` в core/common.py уже
     умеет `start/end` (использует clean_and_compile), но ner.py
     флаги `--start/--end` не принимает и в форму не выведены;
   - режим `extract` совмещает «с нуля» и «дообучение» по факту
     существования ner.json, а «постобработка» (без LLM) показана в той
     же форме с LLM-полями — режимы надо развести явно.
2. **Сервер по скриптам**: `get_stage_model` даёт только модель
   (`<СТАДИЯ>_MODEL` → `MODEL`); HOST/API_KEY — единые
   (`get_server_config` без стадии). В шаблоне и системном .env —
   только `MODEL` по скриптам. Нужны `<СТАДИЯ>_HOST`/`<СТАДИЯ>_API_KEY`
   по той же схеме fallback.
3. `web/api.py::_stage_spec` — автоподхват `compiled_chapters.txt` в
   поле file стадии ner: с переходом на in-memory сборку файл больше
   не создаётся — автоподхват убрать.

---

## План реализации

### T1 — core: сервер по скриптам (`<СТАДИЯ>_HOST`/`<СТАДИЯ>_API_KEY`)

- [x] `core/common.py::get_server_config(env_data, stage="")` — стадия
      непуста: `<СТАДИЯ>_HOST` → `HOST`, `<СТАДИЯ>_API_KEY` → `API_KEY`,
      модель через `get_stage_model`; docstring; без стадии — как раньше.
- [x] Скрипты передают свою стадию: cli/ner.py («ner»),
      cli/ner_check.py («ner_check»), cli/wiki.py («wiki»),
      cli/translate_check_llm.py («translate_check_llm»),
      web/pipeline.py («pipeline»); cli/translate_book.py — общие ключи
      (не стадия web; конвейер передаёт ему всё явными флагами).
- [x] `web/stages.py::env_keys_for` — host → `[<СТАДИЯ>_HOST, HOST]`,
      api_key → `[<СТАДИЯ>_API_KEY, API_KEY]` (персист и предзаполнение
      формы); `_llm_argv` — `get_server_config(env_data, stage)` + тот же
      fallback на системный .env.
- [x] `templates/.env.example` + системный `.env` — блок
      «Серверы по скриптам»: `<СКРИПТ>_HOST/API_KEY/MODEL` (комментарии).
- [x] Доки: core/README.md, AGENTS.md §6, README.md, web/README.md.

### T2 — core: `compile_chapter_text` (сборка глав в память)

- [x] `core/common.py::compile_chapter_text(chapters_dir, want, start,
      end, logger)` → `(text, info)` без записи файла;
      `compile_chapter_texts` переиспользует её (тот же вывод на диск).
- [x] Тесты `tests/test_core_common.py`: in-memory сборка + диапазон.

### T3 — cli/ner.py: диапазон глав, без временного файла

- [x] `--start`/`--end` (ГЛАВЫ, включительно) — проброс в
      `compile_chapter_text`; help в единицах.
- [x] `--compile_out` default None: пусто = сборка в память, файл не
      пишется; явный `--compile_out` — сохранить собранный txt
      (кастомный файл для извлечений остаётся и через `file`).
- [x] Кэши (ner_progress.json / ner_pass1/2_cache.json) при сборке в
      память — в cwd (корень проекта), как раньше рядом с файлом.
- [x] `get_server_config(env_data, "ner")` (T1).

### T4 — web/stages.py: режимы стадии ner

- [x] Спека ner: mode select — `extract` («С нуля (новый глоссарий)») /
      `finetune` («Дообучение (глоссарий уже есть)») / `compile`
      («Собрать главы + извлечение») / `postprocess»
      («Постобработка ner.json (без LLM)»); поля start/end (ГЛАВЫ);
      help «файл нужен для extract/finetune».
- [x] `build_ner`: compile → `--compile_chapters` + `_range_argv`;
      extract/finetune → позиционный file; postprocess → только
      ner_file/strip_meta/min_count; LLM-флаги и `_llm_argv` — только
      для LLM-режимов (extract/finetune/compile).
- [x] `web/api.py::_stage_spec` — убрать автоподхват
      compiled_chapters.txt.
- [x] `run-views.js`: переключение видимости полей по режиму ner
      (как prompt_mode у pipeline): postprocess — только
      ner_file/strip_meta/min_count; compile — start/end, без file;
      extract/finetune — file, без start/end.

### T5 — Тесты

- [x] `tests/test_core_common.py`: `get_server_config` со стадией
      (host/api_key/model, fallback, пустая стадия); `compile_chapter_text`.
- [x] `tests/test_ner.py`: `test_main_compile_chapters` — файл
      `compiled_chapters.txt` НЕ создаётся по умолчанию, извлечение
      работает; `--compile_out` пишет файл; диапазон `--start/--end`.
- [x] `tests/test_web_jobs.py`: `test_build_ner_modes` — новые режимы,
      start/end в compile, LLM-флаги только в LLM-режимах;
      `test_llm_profile_from_env` — стадийные NER_HOST/NER_API_KEY.

### T6 — Доки и проверки

- [x] README.md, web/README.md — режимы ner, серверы по скриптам.
- [x] `python3 -m pytest tests/ -q` — все зелёные.
- [x] `node --check` на изменённые JS.
- [x] Smoke: web — режимы ner переключают поля; запуск compile без
      compiled_chapters.txt; NER_HOST/NER_API_KEY в .env подхватываются.
- [x] Коммит + push (несколько атомарных).

---

## Карта правок

| Файл | Изменения |
| --- | --- |
| `core/common.py` | `get_server_config(env, stage)`, `compile_chapter_text` |
| `cli/ner.py` | `--start/--end`, in-memory compile, `--compile_out` опциональный, стадия «ner» |
| `cli/ner_check.py`, `cli/wiki.py`, `cli/translate_check_llm.py`, `web/pipeline.py` | стадия в `get_server_config` |
| `web/stages.py` | `env_keys_for` (host/api_key по стадиям), `build_ner`, спека ner (режимы + start/end) |
| `web/api.py` | убран автоподхват compiled_chapters.txt |
| `web/static/run-views.js` | видимость полей по режиму ner |
| `templates/.env.example`, `.env` | `<СКРИПТ>_HOST/API_KEY/MODEL` |
| `core/README.md`, `web/README.md`, `README.md`, `AGENTS.md` | серверы по скриптам, режимы ner |
| тесты | T5 |

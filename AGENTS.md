# AGENTS.md — руководство для AI-агентов (Pi и др.)

> Идеология проекта: **Планирование, Функциональность, Поддерживаемость,
> Надежность, Развитие, Тестирование.**

Этот файл — контракт между проектом и AI-агентом. Читай его ДО любых правок. Документация: `README.md` — лендинг для ознакомления и быстрого старта пользователя (возможности, установка, первые шаги); технические детали там не держать — настройки web-сервера, конфигурация, сборки живут в `DEVELOPERS.md` и `packaging/README.md`; API общего модуля: `core/README.md`; контракт web — `web/README.md`; планы и текущие статусы реализации: `TODO.md`. Здесь — только правила и ограничения для агента.

## 1. Суть

Конвейер перевода веб-новелл с любого исходного языка (по умолчанию китайский — шаблон General) на русский через OpenAI-совместимые LLM-серверы с человеческими контрольными точками. Интерфейс и логи — на русском языке. Интерфейс один — **web** (сервер + SPA, пакет `web/`; контракт — `web/README.md`). `run.py` — тонкий лаунчер: поднимает `web/main.py` и открывает браузер. Разделы ACTIVE/HOLD/DONE/DONE_OPEN — в web-интерфейсе; реестр стадий — `web/stages.py::STAGE_SPECS` (ключи-слаги: epub, ner, ner_check, pipeline, translate_check, translate_check_llm, compile, wiki, batch_replace). Стадии: epub_to_chapters → ner → ner_check (LLM-проверка глоссария, контрольная точка) → pipeline (translate → redact → polish) → translate_check → translate_check_llm → clean_and_compile → wiki.

## 2. Окружение

- **Зависимости: venv — рекомендуемый способ установки** и на Windows, и на Linux; системный python3 + apt-пакеты — допустимая альтернатива. Команды в коде и доках остаются унифицированными (`python3`); не пиши `.venv/bin/python3` в код и документацию. В разработке venv поднимает `./dev.sh` (`setup|test|run|deps|shell|clean`): скрипт **активирует** окружение, поэтому внутри него команды остаются `python3 …`.
- Зависимости: `httpx` (единственный HTTP-транспорт LLM) + `python-dotenv` (парсер `.env`) + `tqdm` (прогресс CLI); опциональны `pyahocorasick` (иначе regex-фолбэк) и `pytest` + `pytest-xdist` (тесты). Раннер ОДИН — pytest: никакой второй тест-фреймворк и «свой раннер» не появлялся и не появится; скорость набора даёт параллельность (`-n auto`), а не смена инструмента. Pip-списки: `requirements.txt` (рантайм) и `requirements-dev.txt` (разработка, включает рантайм); активный стек печатает `python3 -m core.deps` (и он же — первой строкой в логе сервера). Принцип: stdlib + три библиотеки (транспорт, парсер .env, прогресс); опциональные пакеты обязаны иметь fallback.
- HTTP-клиент в коде импортирует **только** `core/transport.py` (один, `httpx`): `stream_chat_completion` ходит через `open_stream()`, `iter_lines()` отдаёт строки SSE. Прямой `import httpx` (как и любой второй HTTP-клиент) вне транспорта запрещён — страж `tests/test_architecture.py`; `requests` в проекте не было и не будет: запасной клиент = второй адаптер, второй путь ошибок и второй набор тестов.
- **Осознанные отказы** (аргументы — в `TODO.md`, блок «Миграция на внешние библиотеки»): `tenacity` (политика ретраев одна и она не про исключения), `psutil` (снятие дерева процессов закрыто stdlib-кодом: `killpg`/`taskkill`; пакет = бинарное колесо + третья ветка остановки), `uvicorn`/`fastapi`/ASGI (сервер синхронный и однопользовательский: SSE-соединений ровно столько, сколько открытых вкладок, рабочих потоков — десятки; переписывание web-слоя не решает нашей задачи), `tiktoken` (BPE-файлы из сети ломают офлайн-установку).
- **Фронтенд офлайн.** Все сторонние JS-библиотеки лежат в `web/static/vendor/`, внешних `src`/`href` в SPA быть не может. Состав, версии, лицензии и sha256 описывает манифест `web/static/vendor/vendor.lock.json`; гейт — `python3 tools/vendor_assets.py check` (он же ловит молчаливую подмену файла и возврат удалённого `alpine.min.js`: SPA на ванильном JS, Alpine не возвращать).
- **Кроссплатформенность.** Целевая среда — Linux/macOS (системный `python3`), но код и доки не должны ломаться на Windows:
  - команды в коде/доках — `python3` (Unix); на Windows `python3` нет, поэтому в README указывать явно «на Windows: `python run.py` или `py run.py`»;
  - web-сервер — чистый stdlib (`http.server`), SPA — ванильный JS без сборки; никаких платформозависимых библиотек;
  - пути — только `pathlib`/`os.path`, без Unix-слешей в хардкоде (см. §4).

## 3. Архитектура (три слоя)

```text
core/     общий код: common.py (логика) + settings.py (РЕЕСТР НАСТРОЕК:
          единственный источник дефолтов, меток и подсказок — читает его и
          web, и CLI; там же профили LLM: General = общий .env, остальные —
          llm_profiles.json рядом) + projects.py
          (менеджмент проектов) + transport.py (единственная точка выхода в сеть:
          единственный HTTP-клиент) + deps.py (реестр внешних зависимостей:
          что установлено и чем прикрыто) + stage.py (общий слой стадий:
          флаги LLM, профиль сервера, контекст стадии, прогресс). НЕ скрипты.
          Интерактива (ui/tui) больше нет.
web/      web-интерфейс: server.py + api.py (фасад: register() и порядок
          роутов) + доменные модули хендлеров api_common.py (служебное, ctx,
          кешы и константы, сессия), api_projects.py (пульт и проекты),
          api_files.py (файлы), api_glossary.py (глоссарий и review),
          api_env.py (страница «Настройки» и профили LLM, промпты, metadata),
          api_assets.py (обложка, логи,
          отчёты), api_stage.py (запуски и стадии, предпросмотр) и
          api_templates.py (шаблоны); общий mutable-контекст — в api_common,
          фасад реэкспортирует те же объекты; stages.py
          (спеки стадий: title/script/build — поля берутся из реестра)
          и сборка argv, jobs.py (JobManager + SSE),
          pipeline.py (web-оркестратор конвейера), static/ (SPA: app.js —
          состояние, роутер, настройки и общие action; project-views.js —
          вкладки проекта; run-views.js — запуски и очередь; ui-core.js —
          чистые функции ($, api, fmtBytes, taskState…); ui-components.js —
          общий DOM-слой: фабрика h, iconEl и каркасы modal, menuButton,
          previewPane, listPager — new-логика в view-файлах повторяет только
          данные и разметку; локальные библиотеки — static/vendor/ + манифест
          vendor.lock.json). Контракт API — web/README.md.
cli/  исполнители — чистый CLI (argparse), без интерактивных меню.
          batch_replace.py — массовые замены (правила «паттерн -> замена»
          из формы/аргументов --replace; файл replacements.txt выпилен);
tools/    вспомогательные утилиты вне конвейера (README — tools/README.md):
          rulate_reload/ (userscript Rulate,
          README — tools/rulate_reload/README.md),
          NovelMaestro_Lite/ (юзерскрипт-переводчик, README —
          tools/NovelMaestro_Lite/README.md). Юзерскрипт лежит раскладкой:
          meta.js (баннер ==UserScript==, единственный источник @version) +
          src/NNN-slug.js (части, уже на финальном отступе внутри IIFE;
          000-open.js и 900-close.js — сама обёртка). Публикуемый
          <имя>.user.js — собранный файл, руками не правится:
          python3 tools/build_userscripts.py (--check сверяет артефакт со
          сборкой). Порядок частей = порядок секций, менять его нельзя.
          Плюс vendor_assets.py — манифест локальных библиотек SPA
          (check/list/lock/fetch, офлайн-гейт index.html).
          Дробим только Lite (3,1 тыс. строк); rulate остаётся одной
          частью — 637 строк читаемы целиком.
templates/ шаблоны новых проектов: общие шаблоны в корне (.env.example);
          подпапки по типу книги — жанру и
          языку (General/) с промптами, metadata.yaml и donate.txt.
run.py    лаунчер: python3 run.py → web/main.py (+браузер); проброс
          --host/--port/--auth/--token/--max-upload-mb/--jobs-limit/
          --projects-dir.
projects/ <раздел>/<книга>/ — данные проектов (НЕ в git, см .gitignore).
tests/    pytest P0–P2.
```

Правило слоёв:

- интерактив — только в браузере (SPA) и в `web/` (серверная часть интерактивна через HTTP); `cli/` — только argparse, без `input()` и без импорта UI-слоёв (их больше не существует);
- общая логика — только в `core/`; скрипты заимствуют импортом из `core.common`, НЕ копируют функции себе; менеджмент проектов (разделы, переносы, статистика) — только `core/projects.py` (нужен web-слою);
- web-модули импортируют соседей через `from web.* import …`, а общее — из `core.common`; логика из `core/` в `web/` НЕ дублируется;
- внутри `core/` взаимные импорты модулей — относительные (`from . import transport`, `from .common import …`): у настройек анализатора (`pyrightconfig.json: extraPaths`) `core/` — отдельный корень поиска, и абсолютная самоссылка `core.transport` в этой модели не разрешается (runtime от формы не зависит: пакет `core` всегда импортируется целиком);
- проекты НЕ содержат копий скриптов. Старые копии в DONE-проектах лежат в `_legacy_scripts/` — они заморожены, НЕ трогай и не обновляй их.

## 4. Bootstrap-паттерн (обязателен для новых скриптов)

Все скрипты находят корень репо подъёмом вверх от себя и добавляют его в `sys.path` перед импортом `core.*`. Абсолютные пути и хардкод запрещены. `_bootstrap_core()` продублирован в каждой точке входа ОСОЗНАННО (скрипты запускаются из любого cwd) — не «рефакторить» в один общий импорт:

```python
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
from core.common import ...  # noqa: E402
```

## 5. Соглашение о единицах (критично)

- **ТОКЕНЫ** (язык-осведомлённая оценка `estimate_tokens`, ±20–30%): все размеры LLM-запросов — `--chunk_size` (translate_book/pipeline/ner/epub chunk), `--request_budget`, бюджеты пакетов ner_check (`--batch_size`, `--rag_budget`) и translate_check_llm (`--context_budget`), `--budget` translate_quality, FTS5 chunk wiki, `chunkSize` Lite. Имена параметров сохранены (решение пользователя), единица — токены; веса: кириллица/латиница ~0.30, CJK-идеографы и каны ~1.0, хангыль ~0.8, тай/лаос/кхмер/бирма ~0.6, индийские ~0.55, арабский ~0.5, иврит ~0.4, греческий ~0.35; неучтённые буквы/цифры — 0.5, знаки/символы/эмодзи — 0.35; подряд идущие пробелы — 1 токен; итог вверх +10%.
- **ТОКЕНЫ** (предохранитель сервера): `max_tokens` в payload LLM (не расчёт) и `--near-distance` в wiki.py (природа FTS5 NEAR).
- **СИМВОЛЫ**: длины вне запросов — `--title-limit`, `min_fix_length`, `max_changed_chars`, `context_max_len`, длины в логах/отчётах. `min_len_ratio` — безразмерное отношение длин именно в символах (в CJK→RU токены меняли бы смысл).
- **БАЙТЫ**: только размеры файлов в отчётах translate_check.
- **ГЛАВЫ**: чанкование в clean_and_compile (`--chunk-size` — сколько глав в части).

Если меняешь размер/бюджет — проверь, что единица верная, и укажи её в help argparse («СИМВОЛЫ»/«ТОКЕНЫ»).

## 6. Что использовать из core/ (не изобретай заново)

| Задача | Функция |
| --- | --- |
| .env | `parse_dotenv` (читает python-dotenv: `#` — комментарий, интерполяции `${}` нет) / `system_env_file` (ОДИН общий файл: `WEB_ENV_FILE` → корневой `.env` репо → `cwd/.env`; путь возвращается и когда файла нет — это цель создания на «Настройках») / `env_files(explicit=None)` (файл один; явный `--env_file` заменяет его собой; отдельного `.env` книги нет) / `load_env(explicit=None)` (значения общего файла; остальное — дефолты реестра) / `env_overlay` (перекрытие перечисленных ключей непустым `os.environ`) / `get_server_config(env_data)` (единый `HOST`/`API_KEY`/`MODEL`: сервер, ключ и модель ОДНИ на весь конвейер — стадийных `<СТАДИЯ>_HOST/_MODEL/_API_KEY` и профилей local/remote больше нет) / `print_env_help` (справка по `.env` для CLI) |
| лог | `setup_logging` / `log_argv` (фактическая команда запуска в лог) |
| модель | `determine_model` (только из аргумента/`.env`; авто через `GET /models` убрано — модель обязательна) |
| промпты | `load_prompt` (файл целиком) / `get_tagged_prompt` (теги) |
| токены | `estimate_tokens` (язык-осведомлённая ОЦЕНКА числа токенов: таблица весов по скриптам + фолбэк; единица всех размеров запросов) / `split_at_tokens` (граница бюджета в тексте: (голова ≤ budget, хвост)) / `trim_to_tokens` (начало текста ≤ budget токенов) |
| чанкование | `split_text_smart` (абзацы → предложения; `target_tokens` — ТОКЕНЫ, оценка `estimate_tokens`) / `build_fts_index` (FTS5-чанки тоже в ТОКЕНАХ, оценка) |
| текст/CJK | `get_ngrams` / `is_cjk` / `is_cjk_string` / `find_exact_match` |
| поиск терминов | `load_ner_data` + `find_relevant_ner` (+ `normalize_for_search`, `build_smart_regex`) |
| контекст термина (context) | `extract_term_context` (предложение с термином из чанка; `max_len` — СИМВОЛЫ, 0 = выключено; границы предложений — знаки конца любых языков + закрывающие кавычки/скобки; `threshold`/`ngram_size` — нечёткий фолбэк по предложениям, зеркально `find_relevant_ner`) |
| расширенный контекст перевода (словарь/правила/примеры) | словарь — `load_ner_data` + `find_relevant_dict` (формат ner.json; автодетект направления: совпадения по term и translation, где больше — та сторона; записи в ответе в канонической ориентации; поиск по чанку + сторонам выбранных примеров) / `load_examples` (пары `{original_text, translated_text}`, алиасы source/target — совместимо с translated_trace.json; кэш нормализации и n-грамм обеих сторон) / `find_relevant_examples` (containment n-грамм по обеим сторонам пары — автодетект направления; топ-K жадным отбором со штрафом за пересечение — анти-дубликаты; отсечка по порогу) / `format_fewshot_block` (JSON-массив пар, по одной на строку) / `load_rules_block` (справочник языка txt\|md целиком) |
| правила замен «паттерн -> замена» (batch_replace/epub replace-re) | `trim_rule_left` / `trim_rule_right` (у «->» срезается только её пробельный хвост: пробелы паттерна значимы и внутри, и перед ним — «  +», «^  », « +$»; правая часть из одних пробелов значима («\s+ -> » — сжать пробелы); строка из одних пробелов — пустой паттерн) / `mark_whitespace` (пробелы видимы: «·», «\t», «␍», «⏎» перед своим переводом — нужен отчёту и предпросмотру замен: иначе удалённый отступ или перенос неотличимы от пустоты) |
| проверка глоссария (ner_check) | `filter_ner_items` (порог count + типы) / `format_ner_record` (запись как JSON-объект: term — всегда, fields — поля для LLM, None = все) / `glossary_body` (JSON-массив записей, по одной на строку) / `build_ner_batches` (count по убыванию, бюджет в СИМВОЛАХ; fields — поля записи для LLM, term — всегда) / `parse_rag_suggestions` (текст LLM → записи; fields — разрешённые поля) / `ner_item_lookup` (поиск записи по term: NFC, затем без скобок) / `ner_action` (действие правки: «патч» поля или «удаление» термина; ключ `action` опционален — старые файлы это патчи; англ. delete/remove — то же) / `ner_item_summary` (что сейчас в записи — old для правки-удаления) / `diff_ner_records` (записи LLM ↔ ner.json → правки {term,action,field,old,new,reason}; NFC; запись с action=«удаление» — правка-удаление: поля не сверяются, old — текущие значения, new пусто; нет записи — warning с близкими) / `review_entry` / `parse_review_doc` / `merge_review_entries` (review-файл: поля английские — `stage`/`action`/`status`/`applied`/`old`/`new`, статусы принять/отклонить, накопление; дедуп по term+action+field+old+new) / `apply_ner_patches` (status + applied; патч — дубли термина по совпавшему `old`, list/dict — json; удаление — из глоссария вычёркиваются ВСЕ записи термина; неприменимое — `note` с причиной) |
| проверка перевода LLM (translate_check_llm) | `fix_entry` (ошибка LLM → запись review) / `merge_fix_entries` (накопление, дедуп по chapter+old+new) / `apply_fix_to_text` (NFC, первое вхождение) / `flex_fragment_pattern` (типографически-мягкий паттерн цитаты в обе стороны: кавычки «»“”", тире —–−, …/... и пробелы эквивалентны) / `apply_flex_fix` (замена: точная NFC, иначе найденный мягким паттерном спан) / `find_fragment_owner` (где цитата реально живёт: (глава\|None, причина\|None); гейт — ровно 1 совпадение ровно в 1 главе на всю книгу, глава claimed исключается, короткие не ищутся) |
| имена по полу | `collect_gender_names` (polish: поиск по `translation`, пол по наличию `(female)`/`(male)` в `type`) |
| рассуждения модели (ОБЩИЕ на весь запуск — стадийного префикса нет намеренно) | `REASONING_MODES` (default — не трогать запрос, on/off) / `REASONING_ENV_KEYS` (`REASONING_MODE`, `THINKING_PROFILE`, `REASONING_EFFORT`, `THINKING_BUDGET`) / `REASONING_PROFILES` (id → (название для UI, сборщик ключей): openai, anthropic, qwen, dashscope, ollama, openrouter, all — каждый профиль отправляет только СВОИ ключи) / `reasoning_fields(mode, profile, effort, budget)` → dict ключей payload (пустой — дефолт сервера; budget — ТОКЕНЫ) / `reasoning_settings(env_data)` → {mode, profile, effort, budget} (os.environ приоритетнее файла по каждому ключу). Свои поля тела — `EXTRA_BODY_ENV_KEY`/`extra_body_fields` (JSON-объект, едет после ключей профиля; битый JSON — warning и {}). В web — одна карточка «Рассуждения модели» на «Настройках»; спеки стадий reasoning-полей не содержат, argv стадий их не прокидывает |
| запрос к LLM | **ТОЛЬКО** `stream_chat_completion` — единая гигиена стрима ([DONE]/finish_reason, loop-детект, cut, empty, min_len_ratio) → `(text, err)`; transport-ошибки (`ConnectTimeout`/`ReadTimeout`/`BrokenStream`/`TransportError`) она превращает в `(«», err)` — наружу не бросает; messages — `llm_messages` (унификация: промпт и данные в user, system — пустое поле в запросе; фактический запрос и предпросмотр строятся одной функцией) |
| запись файла | `atomic_write` (tmp + fsync + os.replace) |
| чтение | `read_text_safe` (utf-8 → cp1251 fallback) |
| прогресс web | `web_progress_enabled` (флаг `WEB_PROGRESS=1`) / `emit_progress` (done, total, label → `@@PROGRESS@@` + JSON; только в web-режиме, no-op в CLI) |
| предпросмотр запроса (web+CLI) | `preview_request_payload` (JSON {stage, label, model, messages, chars — СИМВОЛЫ, tokens — оценка, meta}) / `write_preview_request` (атомарная запись) / `preview_logger` (только stderr) |
| главы | `parse_chapter_id` / `build_chapter_map` / `find_chapter_file` / `format_ranges` / `compile_chapter_text` (склейка `chapter.txt` из папок в память, `(text, info)`, `start/end`) / `compile_chapter_texts` (та же склейка → файл) / `read_chapter_titles` / `write_chapter_titles` (названия глав: первая непустая строка, чтение/замена) |
| HTTP-транспорт (core/transport.py) | **ТОЛЬКО** `open_stream` (POST JSON → контекстный менеджер `ResponseStream`: `status_code`, `headers`, `iter_lines()` — байтовые строки SSE без `\n`; выход из контекста закрывает соединение; ошибка соединения прилетает уже на входе) / `client()` (общий `httpx.Client` с пулом) / `reset_client()` (тесты) / `BACKEND` (`"httpx"`) + нормализованные ошибки `TransportError`, `ConnectTimeout`, `ReadTimeout`, `BrokenStream` |
| слой стадии (core/stage.py) | `add_llm_args` (общий блок флагов `--host/--model/--api_key/--env_file/--temperature/--reasoning_effort/--timeout/--max_retries` — имена есть контракт форм web и SPA, не переименовывать; `aliases=True` — старые написания) / `LlmProfile` + `resolve_profile` (сервер стадии: CLI > `os.environ` > `.env`; `/v1` дописывается; модель обязательна) / `LoggedStage` + `new_stage` (лог стадии и команда запуска) / `Stage` + `bind_profile` + `setup_stage` (контекст LLM-стадии и один вызов `stage.complete(prompt, данные)`) / `Progress` (счётчик глав/батчей: в CLI — бар tqdm, в web — `@@PROGRESS@@`, лог стадии не трогает) / `REASONING_EFFORTS` |
| зависимости (core/deps.py) | `ROLES` (роли и их кандидаты: HTTP — только httpx, термины — pyahocorasick либо regex, tqdm, pytest + pytest-xdist) / `status` (строка на роль: активный бэкенд, `degraded` — работа на фолбэке) / `format_status` (одна строка в лог сервера) / `missing_hint` / `main` (`python3 -m core.deps`) |
| настройки (ОДНО МЕСТО ИСТИНЫ) | **ТОЛЬКО** `core/settings.py`: реестр `Setting`/`Block`/`Group` → `GROUPS` (6 субвкладок: `llm`, `transfer`, `glossary`, `checks`, `book`, `server`) / `SETTINGS` / `BY_KEY` / `BY_BLOCK` / `STAGES` — ключ настройки = её имя в .env (у стадийных — `<STAGE>_<FIELD>`, у общих — без префикса); `LLM_BLOCKS` (общие блоки LLM), `LLM_ALIAS` (`jobs`→`threads`, `retries`→`max_retries`), `STAGE_LLM_FIELDS` (какие LLM-поля уходят в argv стадии — исторические имена флагов); чтение: `groups()` / `stage_fields(stage)` / `settings_of(stage)` / `form_fields(stage)` (hidden-настройки в форму стадии не идут) / `defaults(stage)` / `llm_settings()` / `llm_values(profile="")` / `stage_values(stage)` — эффективные значения (реестр → общий .env → профиль → `os.environ`, числа числами); `llm_form(stage,profile)` и `with_llm(stage,form,profile)` — LLM-значения подставляются в form под историческими именами полей, поэтому сборка argv стадии не изменилась (профиль — параметр либо поле формы `profile`); `env_key(stage,name)`, `env_file()`, `file_values()`, `layered_values(profile)`, `effective(key)`; запись: `sanitize(setting,value)` (textarea-переносы → литерал «\n», `#` → кавычки), `write_values(ключ→значение)` (ключи — имена .env; пустое снимает ключ, без ключей файл удаляется); SPA: `display_value(setting)` (секреты — «••••»), `block_payload(block_id)`, `groups_payload()` |
| профили LLM (несколько наборов серверных настроек) | там же, `core/settings.py`: профиль — полный набор LLM-настроек (сервер, ключ, модель, таймауты, повторы, потоки, температура, рассуждения); **General** — встроенный, его значения и есть общий .env (не переименовывается и не удаляется); остальные — один файл `llm_profiles.json` **рядом с общим .env** (в Docker — в том же томе), в нём только переопределения, пустое поле профиля наследует General, пустой файл удаляется. Константы `PROFILE_DEFAULT`/`PROFILE_DEFAULT_TITLE`/`PROFILE_ENV` (`NM_LLM_PROFILE`)/`PROFILES_NAME`; файлы: `profiles_file()`, `profiles_read()`, `profiles_write(list)`, `profiles()` (General первым); чтение: `profile_get(id)`, `profile_values(id)`, `profile_display(id)` (секрет — «••••»), `profile_slug(name,taken)` (латиница — slug, иначе `p1`, `p2`, …); CRUD: `profile_create(name,values)`, `profile_rename(id,name)`, `profile_delete(id)`, `profile_save_values(id,values)`, `profiles_payload()`. Выбор проекта — состояние браузера (`localStorage nmProfile:<раздел>/<книга>`), в web он едет **отдельным полем тела** запуска, а не полем формы стадии; подпроцесс получает `NM_LLM_PROFILE`, поэтому скрипты читают реестр как читали |
| проекты | **ТОЛЬКО** `core/projects.py`: `DEFAULT_SECTIONS` (ACTIVE/HOLD/DONE, алиас `SECTIONS`) / `load_sections` / `save_sections` / `create_section` / `rename_section` (в существующий — перенос проектов) / `delete_section` (непустой — отказ) / `ensure_projects_root` / `valid_project_name` / `sanitize_project_name` / `list_projects` / `project_stats` / `project_progress_table` / `create_project` / `move_project` / `rename_project` / `copy_project` / `delete_project` / `list_template_sets` / `TEMPLATE_SKELETON` (`prompts`+`source`) / `_ensure_template_skeleton` (идемпотентный ремонт скелета) / `create_template_set` (каркас prompts/+source/) / `create_template_dir` (всегда ошибка — каталоги неизменяемы) / `copy_template_set` / `delete_template_set` / `templates_files` (пустые каталоги как `path/`) / `read_template_file` / `write_template_file` / `delete_template_file` (каталог → ошибка; `str \| None`) / `template_file_info` / `move_template_file` (только файлы; каталог → ошибка) / `fill_project_from_template` / `render_metadata` / `write_project_metadata` |

Запрещено: свои парсеры .env, свои стрим-обработчики SSE, свои парсеры имён главных папок, прямой импорт `requests`/`httpx` вне `core/transport.py`. Добавил функцию в таблицу — обнови и `core/README.md`, и `tests/test_docs.py` (сверка доков с кодом).

## 7. Ключевые конвенции

### Конфигурация: системный корневой .env

Весь серверный конфиг — в ОДНОМ общем `.env` (LLM-подключение, модель, дефолты стадий, `WEB_*`). **Собственного `.env` у книги больше нет**: изменённые для одной книги поля запусков — рабочее состояние браузера (localStorage), а не слой на диске; кнопка «Сбросить настройки» в форме стадии возвращает её на значения общего конфига. Приоритет: `CLI-флаг` > `os.environ` > общий `.env` > встроенный дефолт реестра. Без .env скрипт обязан работать дальше с ручным вводом — не падать. **Все UI-предпочтения — в localStorage браузера, НЕ в .env** (12-factor: клиентские настройки живут на клиенте): тема интерфейса — переключатель 🌙/☀ в шапке (доступен с любого экрана, редакторы перекрашиваются на месте), тема и кегль редакторов и кегль предпросмотра — карточки страницы «Настройки», авто-обновление, локальные значения запусков (`runParams`). В .env — только серверная конфигурация; WEB_UI_THEME/WEB_EDITOR_THEME/WEB_EDITOR_FONT_SIZE удалены (внешний вид — в localStorage). Общий .env правится на странице «Настройки» (API `/api/settings`; в Docker файл персистентен, см. ниже).

**Синтаксис .env** (парсер — `python-dotenv`, свой диалект не поддерживаем): `KEY=VALUE`, `export ` терпим, парные кавычки снимаются; **`#` вне кавычек начинает комментарий** — значение с решёткой пишется в кавычках («"a # b"»); `${VAR}` раскрывать нечем: интерполяция выключена (`interpolate=False`), `$` и `{` — обычные символы. Пустое значение — пустая строка, она не затеняет глобальный ключ.

**Слои конфига (обязательны для префилла форм и персиста настроек):**

1. реестр `core/settings.py` — ОДНО место истины: ключ, метка, тип, дефолт, подсказка и владелец-стадия каждой настройки; дефолты стадий, CLI-флагов и `.env` берутся только отсюда;
2. общий `.env` (`core.common.system_env_file`): `WEB_ENV_FILE` → корневой `.env` репо → `cwd/.env`. В Docker (образ) `WEB_ENV_FILE=/app/projects/.env` — файл внутри постоянного тома: entrypoint копирует его из шаблона при первом старте, правки страницы «Настройки» переживают обновление образа; заводского `/app/.env` в образе нет (последний рубеж — встроенные дефолты кода);
3. **профиль LLM** — если запуск выбрал не General: значения профиля лежат поверх общего файла (только переопределения); сам выбор профиля — состояние браузера проекта, не диск;
4. `os.environ` — деплой-конфиг: в compose задают ТОЛЬКО `WEB_*` (запуск контейнера); LLM-конфиг и дефолты стадий в compose НЕ задают — их единое место системный `.env` (нет конфликтов «правлю, а не применяется»); `env_overlay` перекрывает файлы по ключам;
5. **своего `.env` у книги больше нет**: изменённые для одной книги поля запусков — рабочее состояние браузера (localStorage), а не слой на диске; кнопка «Сбросить настройки» в форме стадии возвращает её на значения общего конфига. Легаси-копии книжных `.env` вынесены в `backup/legacy_book_env/` (gitignored, содержат ключи).

LLM-подключение (host/model/api_key/temperature/потоки/повторы/рассуждения) — ОДНИ на весь конвейер и на все стадии: стадийных `<СТАДИЯ>_HOST/_MODEL/_API_KEY` нет, их значение было нулевым. Сервер подставляет их в форму запуска сам (`core.settings.with_llm()`), поэтому в форме стадии их нет. Рассуждения модели (`REASONING_MODE`/`THINKING_PROFILE`/`REASONING_EFFORT`/`THINKING_BUDGET`) — общие ключи без стадийного префикса: модель в конвейере одна.

**Профили LLM** — когда наборов серверных настроек нужно несколько (дома один сервер, в облаке другой): встроенный General (его значения — обычный общий `.env`) плюс именованные профили в `llm_profiles.json` рядом с ним, хранящие только переопределения. Профиль выбирается **один на проект** на вкладке «Запуски» (панель «LLM профиль»), выбор живёт в localStorage и уезжает в теле запуска; подпроцесс получает `NM_LLM_PROFILE=<id>`. Встроенные дефолты подключения: `HOST=https://routerai.ru/api/v1`, `MODEL=google/gemma-4-31b-it`, ключ пуст.


### Канон глав

Имена папок парсятся ТОЛЬКО через `parse_chapter_id` (00000_1_…, 000001_…, 1_x, числа…). Поиск файла главы — только через `find_chapter_file` (приоритеты: точные имена → подстрока типа → единственный безопасный txt; blacklist: raw/draft/translated/original/source/backup). Там, где дубли файлов = катастрофа, передавай `strict=True`.

### Unicode

Везде, где сравнивается/заменяется русский текст — NFC-нормализация (`unicodedata.normalize("NFC", …)`): поиск фрагментов, fix-скрипты, замена строк. Кавычки «»/", тире —–-, многоточия …/... считаются разными.

### Regexp-поля (все стадии)

Regexp-поля форм и CLI — чистые стандартные выражения Python `re` (MULTILINE: «^»/«$» — начало/конец СТРОКИ); регистр и прочие режимы — стандартными inline-флагами ((?i), (?s)…). Кастомные флаги (« |i», « |s») и комментарии « # …» в regexp-полях запрещены: особые семантики («пропуск первого совпадения») — только встроенными проверками скриптов. Значение с `#` внутри (редкий паттерн) в `.env` пишется в кавычках — иначе это комментарий (§7 «Синтаксис .env»).

### JSON-файлы данных (правило консистентности)

Названия полей в JSON-файлах данных (review-файлы ner_review.json / translate_check_llm_review.json и т.п.) — ПО УМОЛЧАНИЮ на английском: `entries`, `status`, `applied`, `reason`, `stage`, `chapter`, `file`, `type`, `term`, `field`, `old`, `new`, `created`, `updated`, `note` … Значения (статусы «принять»/«отклонить», тексты ошибок, логи) остаются русскими. Новые JSON-ключи писать только на английском; переименование — жёсткое, без fallback-чтения старых ключей (совместимость не сохраняем).

### Промпты

- Разметка запроса: содержимое `<system>…</system>` уходит в системное сообщение LLM, остальное (и `<user>…</user>`) — в user (`llm_messages`); без разметки весь промпт в user. Правила и описательные константы — в `<system>`, задание и переменные данные — в `<user>`; данные вставляются ТОЛЬКО через плейсхолдеры-переменные, дописывание данных в конец промпта кодом — только fallback для старых внешних промптов без плейсхолдера (с предупреждением). В промптах — только реально обрабатываемые теги; данные размечаются текстовыми маркерами («=== ГЛОССАРИЙ ===» и т.п.), фиктивной xml-разметки нет.
- Внешние промпты хранятся в `prompts/` проекта; формат — теги: `<translate>/<translate_lr>/<redact>/<polish>`, `<pass1>/<pass2>`, `<prompt_pass1/2>`, `<prompt_ner_check>/<prompt_rag>`, `<prompt_assessment>`, `<prompt_wiki_article>` + JSON-теги wiki (`<wiki_markers>`, `<wiki_default_markers>`, `<wiki_type_names_ru>`, `<wiki_relations_labels>`, `<wiki_skip_relations>`, `<wiki_type_order>`); файл БЕЗ тегов = промпт этапа целиком (допустимый режим «отдельный файл на этап», не legacy). Теги во всех скриптах достаются единым `get_tagged_prompt` (открывающий тег — только в начале строки: упоминания тегов в «#»-комментариях не захватываются).
- Встроенные промпты в скриптах (DEFAULT_*/PASS1_PROMPT) — только fallback. Меняя встроенный промпт, синхронизируй смысл с внешним шаблоном, если есть.
- Плейсхолдеры: `{ner_block}`, `{original_text}`, `{translated_text}`, `{female_names}`, `{male_names}` (polish: имена из ner.json по полю `translation`, пол по наличию `(female)`/`(male)` в `type`); `{dict_block}`, `{rules_block}`, `{fewshot_block}` (translate_lr); `{chunk_text}`, `{ner_json}` (ner); `{glossary}`, `{fields}`, `{ner_block}`, `{rag_block}` (ner_check); `{batch_text}`, `{errors_json}` (translate_check_llm). Форматирующие `{translation}`/`{relations_label}` — в системном шаблоне wiki.

### Логирование

- Проектные логи: `logs/`; логи стадий по главам: `logs/chapters/`.
- `setup_logging` заменяет расширение выходного файла на `.log`.
- Каждый скрипт после `setup_logging` вызывает `log_argv(logger)` — в лог пишется фактическая команда запуска (shlex.join(sys.argv)).

### UI/UX-гайдлайн (web/static, M9)

- **Тултипы** — `attachTooltip(el, text)` (app.js): ВСЕ чекбоксы и сложные контролы (select/textarea/files) с полем `help` получают всплывающую подсказку при наведении/фокусе; у text/number подсказка — inline `.field-help` под полем. Новое поле формы с `help` — тултип обязателен.
- **Множественный выбор** (типы, типы-по-полу и т.п.) — чипсы-чекбоксы из реальных данных проекта + кнопки «Выбрать все / Снять все»; минимум один пункт выбран (паттерн модалки типов глоссария).
- **Важные режимы** — карточки-пресеты с названием и описанием, а не абстрактный select (паттерн ner_check).
- UI-предпочтения — localStorage (см. §7); настройки внешнего вида — системный .env.

### Артефакты стадий (НЕ менять имена)

`chapter.txt → translated.txt (+translated_trace.json) → redacted.txt → polished.txt`. Trace-JSON — мост translate→redact (пары original/translated); polish trace НЕ пишет. `_STAGE_IO` в `web/pipeline.py` — фиксирован. Глоссарий — всегда `ner.json` в корне проекта (чтение и сохранение во всех стадиях; выбор файла в web убран); review-файлы правок — рабочие, в tmp/ проекта: ner_review.json / translate_check_llm_review.json (бэкап глоссария и отчёт оценки качества — тоже в tmp/; сборки compile — в tmp/, имя `<проект>_<начало>_<конец>…`).

## 8. Запреты

- **Dev и Prod разделены**: рабочие проекты живут в репо (`projects/<раздел>/<книга>/` — gitignored), боевые — в Docker-контейнере (папка вне репо, обычно `~/dockers/NovelMaestro/`). Изменения в проектах репо — обычная работа; боевой контейнер и его bind-mount-данные (`projects/`, `templates/`, `web/job_logs` вне репо) не трогай без явной просьбы — там живут реальные книги. Для проверок и тестов — только временные данные: pytest `tmp_path`, `/tmp`, моки API; PUT/POST/DELETE к живому web-серверу против боевых проектов запрещены. Эксперименты с книгами — только в разделе `projects/TMP` (песочница, не боевой): копируй туда книгу из ACTIVE/HOLD/DONE и работай с копией. Работай в пределах рабочей директории репо; за её пределы — только по явной необходимости.
- Не менять имена артефактов стадий и канон `parse_chapter_id` без миграции всех потребителей и тестов.
- Не коммитить `projects/`, `servers/`, `Images/`, `backup/`, `__pycache__/` и корневой `.env` (уже в .gitignore — не обходи).
- Не менять единицы измерения параметров (символы ↔ токены) «для красоты».
- Не убирать fail-fast в web/pipeline.py (returncode 0 + непустой выходной файл + grep слов-ошибок).
- Не читай файлы с приватными SSH ключами.
- Не возвращай интерактивный cli/tui и `backends/` — интерфейс web-only; (папка `cli/` — только argparse-исполнители, §3); исторический документ AUDIT.md удалён, его выводы учтены.
- НЕ вводи настройку слов-ошибок пайплайна (M7 отменён): текст перевода НЕ попадает в stdout скриптов (только прогресс/ошибки) — жёсткий `_ERROR_RE` в web/pipeline.py ловит реальные сбои; настройка = регресс.
- **Комментарии в коде — только для понимания**: минимальные, объясняют «почему», а не «что» (что видно из кода). Запрещены комментарии-дневники (номера раундов/этапов, «сделано в сессии N», отчёты о правках) — их место в TODO.md и сообщениях коммитов, а не в коде.

## 8а. pi-lens (настройки шума)

- `~/.pi-lens/config.json` (глобально, вне репо): `tests.enabled: false` — встроенный тест-раннер жёстко зовёт `python` (в системе только `python3`, ENOENT-шум); тесты гоним вручную: `./dev.sh test` (он же `python3 -m pytest tests/ -q -n auto`).
- `.pi-lens.json` (в репо): `format.enabled: false` — НЕ переформатировать файлы автоматически (перекраивает весь файл, шум в диффах); `rules.jscpd.disable: ["duplicate"]` — без дубликатов-предупреждений; `ignore` — projects/, servers/, Images/, backup/, **pycache**/, .venv/.
- Находки pi-lens — подсказки, не истина: перед реакцией проверяй фактическое состояние (grep / node --check / pytest). Устаревший кэш диспатч-пайплайна повторяет старые находки (например, «exportModal unused» после переноса функции) — снимать через `lens_diagnostic_mark` false-positive, реальная проверка — `python3 -m pytest` + `node --check`.

## 8б. UI-правки: headless-прогон Playwright со скриншотами

- **Меняешь UI — прогон со скриншотами обязателен, «глазами открыл вкладку» не считается.** Общий проход: `./dev.sh probe --shot` (`tools/ui_probe.mjs`): сам поднимает сервер на временных данных, проходит все view, вкладки проекта и модалки, ловит `pageerror`/`console`/4xx-5xx и пишет PNG + `report.json` в `logs/ui_probe/`. Выход 0 — чисто, 1 — список находок. Скриншоты — артефакты прогона: `logs/` в `.gitignore`, в git они попадать не должны.
- Точечно: `./dev.sh probe --shot --only settings project/run` (список целей — ключи `ROUTES` и вкладок проекта внутри `tools/ui_probe.mjs`); `--url`/`--keep` — пройтись по уже поднятому серверу, `PROBE_VERBOSE=1` — лог сервера в stdout. Playwright стоит глобально (`playwright-core`, chromium в `~/.cache/ms-playwright`): своей npm-папки и сборки в репо нет и не будет, node используется только как `node --check`/`node --test`/пробег.
- Данные прогона — только временные (`/tmp`, песочница `TMP`): к боевому серверу `PUT/POST/DELETE` не ходить (§8).
- Юзерскрипты (`tools/NovelMaestro_Lite/`, `tools/rulate_reload/`) — свои полигоны в `/tmp/nm_probe/` (таблица — `tools/NovelMaestro_Lite/AGENTS.md`): читалка, топбар, панель ⋮, настройки; артефакты прогона (скриншоты, логи) — там же, в /tmp, не в git.
- Прогон без `--shot` скриншоты не пишет: сравнить «до/после» UI-правки потом нечем.

## 9. Как вносить изменения

0. **Релизы и VERSION** — релиз (тег `v*` + GitHub Release, см. packaging/README.md) делается ТОЛЬКО по явному запросу пользователя. CHANGELOG.md не ведётся — история между релизами восстанавливается по коммитам (`git log v<старый>..v<новый>`); заметки GitHub Release генерируются воркфлоу автоматически (`gh release create --generate-notes`). Версия — файл `VERSION` в корне репо (обновляется при релизе), читается `web/version.py`; приоритет: NOVELMAESTRO_VERSION → VERSION → фолбэк. TODO.md: перед задачей сверься с текущими статусами; завершил майлстоун — обнови статус в `TODO.md` в том же коммите. Планы в отдельные файлы не выноси (web_plan.md упразднён). **Отметки и коммиты — по мере выполнения, а не в самом конце**: сделал задачу (или её законченную часть) — сразу отметь чекбокс в `TODO.md`, закоммить и запушь. Не копи в конце сессии всё разом; незапушенный коммит — не завершённая работа.
1. Общая логика → `core/common.py` (LLM-профиль, флаги и прогресс стадии → `core/stage.py`), + запись в `core/README.md` и в таблицу §6 / `tests/test_docs.py`.
2. Новый исполнитель → `cli/xxx.py` (CLI, argparse, bootstrap §4).
3. Новая стадия в web → строка в `web/stages.py::STAGE_SPECS` (ключ-слаг, title, script, build-функция, fields) + форма в SPA.
4. Новый роут API → доменный `web/api_<домен>.py` (хендлер и `router.add` туда же; `web/api.py` — только фасад) + строка в таблицу `web/README.md`.
5. Новая функция web-слоя → `web/*.py`, общая логика — только из `core/` (не копировать из скриптов).
6. Тесты → `tests/` (см. §10). Для новых функций core — параметризованные тесты обязательны. Добавил функцию в таблицу §6 — обнови и тесты, и `tests/test_docs.py`.
7. **Перед коммитом — обязательно:**
   - прогони `./dev.sh test` — это `python3 -m pytest tests/ -q -n auto` (pytest-xdist: весь набор — единицы секунд вместо минут). Последовательно (`-n 0`) — только когда отлаживаешь один тест. Все тесты должны быть зелёными. Коммит с падающими тестами запрещён;
   - если менял код `core/`, `cli/`, `web/`, `run.py` — проверь, что существующие тесты это покрывают; не покрывают — добавь/обнови тесты в том же коммите;
   - затем smoke-запуск затронутого скрипта с `--help` (и `--dry-run`, если поддерживается);
   - если менял SPA (`web/static/*.js`) — `./dev.sh spa` (`node --check` по всем файлам + `node --test` по `tests/spa/`) и `./dev.sh probe --shot` (headless-обход всех экранов, панелей и модалок на временных данных + скриншоты в `logs/ui_probe/`, см. §8б); менял UI юзерскриптов — тот же принцип: headless-прогон полигоном и скриншоты до/после;
   - **закоммить и запушь изменения на GitHub** (`git add -A` → `git commit` → `git push origin`). Незапушенный коммит — не завершённая работа. Тесты НЕ должны ходить в сеть: LLM только мокать (monkeypatch на `stream_chat_completion` / `core.transport.open_stream`), данные — во временных папках pytest (`tmp_path`).
8. **Ветка одна — `main`** (ветвление `dev` упразднено: разработка ведётся сразу в `main`, CI — `tests.yml` только на `main`).
9. **Стандарт коммитов**: `<тип>(<область>): <описание на русском>`; типы — `feat`/`fix`/`refactor`/`docs`/`test`/`chore`; области — `core`/`cli`/`web`/`templates`/`tests`/`docs`/`repo`. Описание — инфинитив, до ~72 символов; одно логическое изменение — один коммит. Если в одном файле смешаны правки из разных задач (например, докстринг + фича), файл можно коммитить целиком в коммит основной задачи — не нужно вырезать хунки по-атомному. Первый коммит истории: `chore(repo): initial commit — NovelMaestro`.

Стиль кода: русский в строках/логах/UI; компактные функции; docstring на каждую публичную функцию core; секции в core/common.py разделены комментариями `# ══…`.

## 10. Быстрая проверка среды

```bash
./dev.sh test                                     # тесты: pytest -n auto (по умолчанию; обязательно перед коммитом)
./dev.sh test -n 0 tests/test_ner.py              # один файл последовательно — только для отладки
./dev.sh spa                                      # SPA: node --check по всем файлам + node --test tests/spa/
./dev.sh probe --shot                             # UI: headless-обход экранов + скриншоты в logs/ui_probe/
python3 -m pytest tests/ -q --cov=core --cov=cli --cov=web  # покрытие (нужен pytest-cov)
python3 run.py                                    # web-интерфейс (сервер + браузер)
python3 web/main.py --help                        # флаги сервера
python3 cli/translate_book.py --help          # единый LLM-скрипт
python3 cli/translate_check_llm.py --help   # проверка перевода LLM
ls projects/ACTIVE/*/chapters | head              # данные реального проекта
# публикация изменений (обязательно):
git add -A && git commit -m "…" && git push origin
```

Карта тестов `tests/` (принцип: один модуль — один файл тестов):

- `tests/conftest.py` — общие хелперы (SilentLog, make_ru_chapter_file, feed, fake_env);
- `tests/test_core_common.py` — `core/common.py` целиком (стрим SSE моками, .env, чанкование, NER-поиск, имена по полу, канон глав);
- `tests/test_projects_core.py` — `core/projects.py` (создание/перенос/переименование, tmp_path);
- `tests/test_core_settings.py` — `core/settings.py`: целостность реестра (ключи, типы, владельцы), совпадение метаданных и дефолтов со спеками стадий, чтение слоёв, запись общего .env и профили LLM (файл рядом с .env, General, формат записей, маски, slug и дубли имён);
- `tests/test_core_stage.py` — `core/stage.py`: контракт имён флагов, порядок источников сервера (CLI > env > файл), `<СТАДИЯ>_*`, нормализация `/v1`, форма одного запроса стадии, предпросмотр, прогресс в обоих режимах;
- `tests/test_core_transport.py` — `core/transport.py`: нарезка SSE на строки (терминатор отсечен, `[DONE]` и finish_reason видны), нормализация ошибок, живой раунд-трип через stdlib-сервер, ошибка соединения как ConnectTimeout;
- `tests/test_run_flows.py` — `run.py` (bootstrap, лаунчер web);
- по одному файлу на скрипт: `tests/test_translate_book.py`, `tests/test_ner.py`, `tests/test_ner_check.py`, `tests/test_translate_check_llm.py`, `tests/test_wiki.py`, `tests/test_epub_to_chapters.py`, `tests/test_translate_check.py` — чистые функции + оркестраторы (`run_two_pass`, `run_wiki_generation`) и `main()` с моками LLM;
- `tests/test_cli_units.py` / `tests/test_cli_e2e.py` — чистые функции и прогоны `main()` остальных `cli/` без сети (batch_replace, clean_and_compile, translate_check и др.);
- `tests/test_web_pipeline.py` — web-оркестратор `web/pipeline.py` (Tracker, build_stage_cmd, grep_errors, process_chapter, main);
- `tests/test_web_api.py` / `tests/test_web_jobs.py` / `tests/test_web_m7.py` / `tests/test_web_server.py` / `tests/test_web_sandbox.py` — web-слой (роуты, JobManager, SSE, env-редактор, NER-экспорт) на реальном HTTP-сервере без сети;
- `tests/test_docs.py` — сверка доков (`core/README.md`, AGENTS.md §6) с кодом;
- `tests/test_tools_vendor.py` — `tools/vendor_assets.py`: файлы вендора == манифест (sha256/размер), ни одной ссылки на CDN в SPA, удалённые библиотеки не возвращаются;
- `tests/test_tools_userscripts.py` — юзерскрипты `tools/`: собранный `.user.js` побайтово равен закоммиченному, части нумерованы и держат обёртку, версия берётся из `meta.js`, канон метаданных и ссылки установки, `node --check` по артефактам, плюс `node --test` по `tests/tools/*.test.mjs`;
- `tests/tools/lite-reasoning.test.mjs` — node-тесты чистой логики Lite (части — один IIFE, поэтому подопытный блок вырезается из артефакта по маркерам и исполняется на заглушках): какие reasoning/thinking-ключи уходят в тело запроса, финальное состояние панели прогресса и жизнь флага отмены;
- `tests/test_architecture.py` — регресс-гарды архитектуры (§3: запрет `input()` и UI-импортов в `cli/`, единый стрим, bootstrap, web-раскладка, run.py — лаунчер web, отсутствие backends/cli|tui).

## 11. Правила коммитов

Коммиты строго атомарны (одно логическое изменение — один коммит) и оформляются по Conventional Commits: `<type>(<scope>): краткое описание на русском`. Допустимые `type`: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`. `scope` — короткий латинский модуль (`web`, `api`, `pipeline`, `ner`, `core`, `docs`). Описание до 72 символов, без точки в конце; детали, списки изменений и ссылки на задачи выносятся в тело коммита.

Категорически запрещены произвольные префиксы и неинформативные сообщения: `W*`, `M*`, `TODO:`, `Шаблоны:`, `фиксы`, `правки`, CAPS и эмодзи. Запрещено смешивать несвязанные правки (например, новую фичу и обновление статусов в `TODO.md`) в одном коммите. Эталонные примеры: `feat(run): добавлен прогресс запусков`, `fix(web): убран кегль 11 из предпросмотра`, `docs(todo): обновлён статус задач`.

## 12. Форматирование документации (.md)

- **Абзац — одна длинная строка.** Жёсткие переносы строк внутри абзаца (source wrapping на ~80 колонок) запрещены: они читаются как лишние разрывы. Один абзац = одна строка исходника, сколько бы она ни занимала колонок.
- Структура размывается пустыми строками: заголовки, списки, таблицы, цитаты и код-блоки отделяются пустой строкой; соседние строки одного абзаца/списка пустой строкой не разрывают список.
- Элементы с сохранённым форматом НЕ перенабираются в длинные строки: код-блоки (```…```), таблицы (`| … |` — одна строка на ряд), ASCII-схемы, индентированные продолжения элементов списка — их внутренние переносы остаются как есть.
- Списки: маркеры `-`/`*`/нумерация — каждый пункт с новой строки; подпункты — с отступом, продолжение пункта — с отступом без маркера.
- Не склеивать при форматировании: заголовки, элементы списков, строки таблиц, цитаты (`>`) и разные абзацы (пустая строка между ними обязательна).
- Терминология: «README» — лендинг; «TODO.md» — планы/статусы; «AGENTS.md» — правила агента; правки дока = коммит с типом `docs`.

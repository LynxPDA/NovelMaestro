# AGENTS.md — руководство для AI-агентов (Pi и др.)

> Идеология проекта: **Планирование, Функциональность, Поддерживаемость, Надежность, Развитие, Тестирование.**

Этот файл — контракт между проектом и AI-агентом, читай его ДО любых правок. Здесь только правила и ограничения. Детали живут по своим местам: `README.md` — лендинг пользователя, `DEVELOPERS.md` — web-сервер, конфигурация, запуск и сборки, `packaging/README.md` — Docker и portable-сборка, `core/README.md` — полное описание API общего модуля, `web/README.md` — контракт web-слоя, `TODO.md` — планы и текущие статусы.

## 1. Суть проекта

Конвейер перевода веб-новелл с любого исходного языка (по умолчанию китайский — шаблон General) на русский через OpenAI-совместимые LLM-серверы, с человеческими контрольными точками. Интерфейс и логи — на русском.

Интерфейс один — **web** (сервер + SPA, пакет `web/`). `run.py` — тонкий лаунчер: поднимает `web/main.py` и открывает браузер. Разделы проектов: ACTIVE / HOLD / DONE, песочница — TMP. Реестр стадий — `web/stages.py::STAGE_SPECS` (ключи-слаги: `epub`, `ner`, `ner_check`, `pipeline`, `translate_check`, `translate_check_llm`, `translate_quality`, `batch_replace`, `compile`, `wiki`): разбор исходника на главы → глоссарий → его проверка (контрольная точка) → перевод (translate → redact → polish) → проверка перевода → проверка перевода LLM → оценка качества → массовые замены → сборка → wiki книги.

## 1а. Язык общения с пользователем

- **Отвечать по-русски и обычными словами.** Разговорные англицизмы, кальки с английского и внутренний жаргон («префилл», «диспатч», «прокинуть», «шум в диффах», «фолбэк-кейс», «зачембэк») заменять понятными русскими формулировками: «начение поля», «обработка запуска», «передать дальше», «лишние изменения в диффе», «запасной вариант».
- **Имена из кода не переводить и не искажать**: функции, флаги, ключи `.env`, имена файлов и статусы приводить как есть, в обратных кавычках — по ним пользователь ищет в интерфейсе и в коде.
- **Коротко и по делу**: что сделано, в каких файлах, что проверить. Без отчёта о процессе, перечисления инструментов и оценок собственной работы — детали в сообщение коммита.
- Тот же язык — у комментариев в коде (§8), сообщений коммитов (§11) и документации (§12).

## 2. Окружение и зависимости

- **venv — рекомендуемый способ установки** и на Linux, и на Windows; системный `python3` + пакеты дистрибутива — допустимая альтернатива. Команды в коде и доках остаются унифицированными (`python3 …`): `./dev.sh` окружение **активирует**, поэтому внутри него `python3` — это интерпретатор venv. `.venv/bin/python3` в код и документацию не писать.
- Команды разработки: `./dev.sh setup|deps|test|cov|run|probe|spa|shell|clean`.
- Зависимости: `httpx` (единственный HTTP-транспорт LLM), `python-dotenv` (чтение `.env`), `tqdm` (прогресс CLI); опционально `pyahocorasick` (иначе — поиск регуляркой) и `pytest` + `pytest-xdist` (тесты). Активный стек печатает `python3 -m core.deps` (он же первой строкой в логе сервера), pip-списки — `requirements.txt` (рантайм) и `requirements-dev.txt` (разработка, включает рантайм).
- Принцип: стандартная библиотека + три библиотеки. Опциональный пакет обязан иметь запасной вариант на stdlib; обязательные роли (транспорт, `.env`) его не имеют — второй HTTP-клиент или свой парсер диалекта означают второй путь ошибок и второй набор тестов.
- HTTP-клиент импортирует **только** `core/transport.py`: `stream_chat_completion` ходит через `open_stream()`, `iter_lines()` отдаёт строки SSE. Прямой `import httpx` (как и любой другой HTTP-клиент) вне транспорта запрещён — за этим следит `tests/test_architecture.py`.
- Сознательные отказы (аргументы — в блоке «Миграция на внешние библиотеки» в `TODO.md`): `tenacity` (политика повторов одна и она не про исключения), `psutil` (снятие дерева процессов закрыто кодом stdlib: `killpg`/`taskkill`), `uvicorn`/`fastapi`/ASGI (сервер синхронный и однопользовательский), `tiktoken` (файлы BPE тянутся из сети и ломают офлайн-установку).
- **Фронтенд полностью офлайн**: все сторонние JS-библиотеки лежат в `web/static/vendor/`, внешних `src`/`href` в SPA быть не может. Состав, версии, лицензии и sha256 описывает манифест `web/static/vendor/vendor.lock.json`, гейт — `python3 tools/vendor_assets.py check` (он же ловит молчаливую подмену файла). SPA на ванильном JS, Alpine не возвращать.
- **Кроссплатформенность**: целевая среда — Linux/macOS, но код и доки не должны ломаться на Windows: в README явно указывать «на Windows: `python run.py` или `py run.py`»; веб-сервер — чистый stdlib (`http.server`), SPA без сборки; пути только через `pathlib`/`os.path`, обратных слешей в хардкоде нет.

## 3. Архитектура: три слоя

```text
core/     общий код, не скрипты: common.py (логика), settings.py (реестр
          настроек и профили LLM), projects.py (менеджмент проектов),
          transport.py (единственная точка выхода в сеть), deps.py (реестр
          зависимостей), stage.py (слой стадии: флаги LLM, профиль сервера,
          контекст, прогресс), search.py (поиск по текстам книги).
cli/      исполнители — чистый argparse без интерактива: translate_book.py,
          ner.py, ner_check.py, translate_check.py, translate_check_llm.py,
          translate_quality.py, wiki.py, clean_and_compile.py,
          epub_to_chapters.py, batch_replace.py.
web/      server.py + api.py (фасад: register() и порядок роутов) и доменные
          модули хендлеров: api_common.py (служебное, ctx, кешы, сессия),
          api_projects.py (пульт и проекты), api_files.py (файлы),
          api_glossary.py (глоссарий и review), api_env.py (страница
          «Настройки» и профили LLM), api_assets.py (обложка, логи, отчёты),
          api_search.py (поиск по текстам книги),
          api_stage.py (запуски и стадии, предпросмотр запроса),
          api_templates.py (шаблоны); общий изменяемый контекст — в
          api_common, фасад реэкспортирует те же объекты; stages.py (спеки
          стадий: title/script/build — поля берутся из реестра) и сборка
          аргументов, jobs.py (JobManager + SSE), pipeline.py (оркестратор
          конвейера), state.py (hub_state), version.py, static/ (SPA: app.js —
          состояние, роутер, настройки; project-views.js — вкладки проекта;
          run-views.js — запуски и очередь; ui-core.js — чистые функции;
          ui-components.js — общий слой DOM: h, iconEl, modal, menuButton,
          previewPane, listPager).
tools/    утилиты вне конвейера (README — tools/README.md): vendor_assets.py
          (манифест библиотек SPA), build_userscripts.py (сборка юзерскриптов
          из частей; --check сверяет артефакт), ui_probe.mjs (headless-обход
          SPA, §8б), NovelMaestro_Lite/ и rulate_reload/ — юзерскрипты, у
          каждого свой AGENTS.md: публикуемый <имя>.user.js — собранный файл,
          руками не правится, порядок частей менять нельзя.
templates/ шаблоны новых проектов: один набор General/ с каркасом prompts/ +
          source/; общий шаблон конфига — templates/.env.example.
run.py    лаунчер: --host/--port/--auth/--token/--max-upload-mb/--jobs-limit/
          --projects-dir/--no-open.
projects/ <раздел>/<книга>/ — данные проектов (НЕ в git).
tests/    pytest; карта — в §10.
.tmp/     артефакты разработки: кэш pytest и данные покрытия (НЕ в git).
```

Правило слоёв:

- интерактив — только в браузере (SPA) и в `web/` (сервер отвечает по HTTP); `cli/` — только argparse, без `input()` и без импорта web-слоёв;
- общая логика — только в `core/`; скрипты берут её импортом из `core.common`, а не копируют к себе; менеджмент проектов (разделы, переносы, статистика, шаблоны) — только `core/projects.py`;
- web-модули импортируют соседей как `from web.* import …`, общее берут из `core.common`; логика из `core/` в `web/` не дублируется;
- внутри `core/` взаимные импорты относительные (`from . import transport`, `from .common import …`): у анализатора (`pyrightconfig.json: extraPaths`) `core/` — отдельный корень поиска, и абсолютная самоссылка `core.transport` в этой модели не разрешается;
- проекты НЕ содержат копий скриптов; старые копии в DONE-проектах лежат в `_legacy_scripts/` — они заморожены, их не трогать.

## 4. Bootstrap-паттерн (обязателен для новых скриптов)

Все скрипты находят корень репо подъёмом вверх от себя и добавляют его в `sys.path` перед импортом `core.*`; абсолютные пути и хардкод запрещены. `_bootstrap_core()` продублирован в каждой точке входа ОСОЗНАННО (скрипты запускаются из любого cwd) — не «рефакторить» в один общий импорт:

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

## 5. Единицы параметров (критично)

- **ТОКЕНЫ** — все размеры LLM-запросов: `--chunk_size` (translate_book, pipeline, ner, epub), `--request_budget` (pipeline, ner_check, translate_check_llm, translate_quality, wiki; у ner_check он же режет глоссарий на батчи, у translate_quality он же ограничивает чанк оценки, сводку свёртки и каждый промежуточный уровень её дерева, у wiki обрезает фрагменты статьи от хвоста), `--rag_budget` ner_check (фрагменты на термин), чанки FTS5 wiki и `chunkSize` Lite. Это язык-осведомлённая **оценка** `estimate_tokens` (±20–30%, таблица весов — в её описании в `core/README.md`). Имена полей форм и флагов унифицированы: бюджет запроса везде — `request_budget`.
- **ТОКЕНЫ** — и `max_tokens` в payload LLM: это предохранитель сервера, а не расчёт.
- **СИМВОЛЫ** — длины вне запросов: `--title-limit`, `min_fix_length`, `max_changed_chars`, `context_max_len`, длины в логах и отчётах. `min_len_ratio` — безразмерное отношение длин именно в символах (в CJK→RU токены изменили бы смысл).
- **БАЙТЫ** — только размеры файлов в отчётах translate_check. **ГЛАВЫ** — `--chunk-size` в clean_and_compile (сколько глав в части) и `--chunk_size`/`--overlap` оценки качества (сколько целых глав в чанке и как они перекрываются).
- Меняешь размер или бюджет — проверь единицу и подпиши её в help argparse («СИМВОЛЫ»/«ТОКЕНЫ»): метка настройки в реестре и справка флага обязаны совпадать с тем, что реально считает код.

## 6. Что использовать из core/ (не изобретай заново)

Полное описание API с пояснениями — `core/README.md`; здесь только имена по группам и правила, из-за которых их не переписывают.

- **`.env`** — `parse_dotenv` / `system_env_file` / `env_files` / `load_env` / `env_overlay` / `get_server_config` / `print_env_help`.
- **логи и модель** — `setup_logging`, `log_argv` (вызывает каждый скрипт сразу после `setup_logging`), `determine_model`.
- **промпты** — `load_prompt`, `get_tagged_prompt` (теги во всех скриптах достаются только им).
- **токены и чанки** — `estimate_tokens`, `split_at_tokens`, `trim_to_tokens`, `split_text_smart`, `build_fts_index`.
- **текст и поиск терминов** — `get_ngrams`, `is_cjk`, `is_cjk_string`, `find_exact_match`, `normalize_for_search`, `build_smart_regex`, `load_ner_data`, `find_relevant_ner`, `extract_term_context`.
- **расширенный контекст перевода** — `find_relevant_dict` (словарь в формате ner.json), `load_examples`, `find_relevant_examples`, `format_fewshot_block`, `load_rules_block`, `collect_gender_names`.
- **замены «паттерн -> замена»** — `trim_rule_left`, `trim_rule_right`, `mark_whitespace`.
- **глоссарий и его проверка** — `NER_LOCK_FIELD`, `ner_is_locked`, `ner_set_locked`, `ner_locked_count`, `NER_NON_VOTED_MODES`, `NER_NON_VOTED_LABELS`, `ner_pick_non_voted` (не голосующие поля: одно значение по режиму реестра), `filter_ner_items` (порядок: `skip_locked` снимает зафиксированные записи с проверки), `format_ner_record`, `glossary_body`, `build_ner_batches`, `parse_rag_suggestions`, `ner_item_lookup`, `ner_item_summary`, `ner_action`, `diff_ner_records`, `apply_ner_patches`, `review_entry`, `parse_review_doc`, `merge_review_entries`.
- **правки перевода** — `fix_entry`, `merge_fix_entries`, `apply_fix_to_text`, `flex_fragment_pattern`, `apply_flex_fix`, `find_fragment_owner`.
- **запрос к LLM** — **только** `stream_chat_completion` (весь стрим и политика повторов уже там; ключ `provider` — отдельным параметром) и `llm_messages` (сообщения строятся одной функцией).
- **проверка сервера** — `server_base_url` (нормализация `HOST`: концевой слэш, `/v1`) — ОДНА на все выходы, и `probe_server` (короткий `GET /models`: жив ли сервер, сколько моделей отдаёт, есть ли в списке модель из конфига). Проверяется ровно тот адрес, которым потом работает стадия.
- **файлы, прогресс, предпросмотр** — `atomic_write`, `read_text_safe`, `web_progress_enabled`, `emit_progress`, `preview_request_payload`, `write_preview_request`, `preview_logger`.
- **главы** — `parse_chapter_id`, `build_chapter_map`, `find_chapter_file`, `format_ranges`, `compile_chapter_text`, `compile_chapter_texts`, `read_chapter_titles`, `write_chapter_titles`.
- **поиск по текстам книги — отдельный модуль `core/search.py`** — реестр групп `SEARCH_GROUPS` (`SearchGroup`, `GROUP_IDS`, `GROUP_LABELS`, `CLUSTERS`, `CLUSTER_LABELS`, `DEFAULT_SCOPES`, `MAX_CONTEXT` (верхняя граница «Контекст:», СИМВОЛЫ); имена групп глав — слаги стадий, остальные кластеры именованы) и три функции: `iter_project_files` (обход файлов по группам), `find_in_text` (все совпадения в тексте — лимитов нет), `search_project` (прогон по книге). Индексов нет намеренно: обычный проход, результат всегда равен файлу.
- **транспорт (`core/transport.py`)** — **только** `open_stream` (контекстный менеджер `ResponseStream`: `status_code`, `headers`, `iter_lines()`; выход из контекста закрывает соединение), `open_get` (тот же контракт, но GET с коротким таймаутом — проверка сервера и скачивания) и `open_json_get` (короткий GET с ожиданием JSON-словаря — GitHub Releases), плюс `client`, `reset_client`, `BACKEND` и нормализованные ошибки `TransportError`, `ConnectTimeout`, `ReadTimeout`, `BrokenStream`.
- **слой стадии (`core/stage.py`)** — `add_llm_args` (имена флагов — контракт форм web и SPA, не переименовывать), `LlmProfile`, `resolve_profile` (сервер стадии: CLI > `os.environ` > `.env`; `/v1` дописывается; модель обязательна), `LoggedStage`, `new_stage`, `Stage`, `bind_profile`, `setup_stage`, `Progress` (в CLI — бар tqdm, в web — `@@PROGRESS@@`), `REASONING_EFFORTS`, `apply_cli_defaults` (вызывается в каждой точке входа `cli/`).
- **рассуждения модели (общие на весь запуск, стадийного префикса нет намеренно)** — `REASONING_MODES`, `DEFAULT_REASONING_EFFORT`, `REASONING_ENV_KEYS`, `REASONING_PROFILES`, `reasoning_fields`, `reasoning_settings`, `EXTRA_BODY_ENV_KEY`, `extra_body_fields`. У openai-профиля режим «включены» отправляет уровень всегда: без ключа `reasoning_effort` гибридные модели считают рассуждения выключенными.
- **выбор провайдера (роутеры OpenRouter-типа, общие ключи)** — `PROVIDER_KEYS` (`PROVIDER_ORDER`/`PROVIDER_ONLY`/`PROVIDER_IGNORE`/`PROVIDER_ALLOW_FALLBACKS`/`PROVIDER_COUNTRY`), `provider_list`, `provider_settings`: пустые поля — ключ `provider` в тело не едет вовсе; `allow_fallbacks` отправляется только вместе с `order` и только в false; @-синтаксис в MODEL (`model@provider=...`) с этим ключом несовместим — роутер отвечает 400.
- **настройки: одно место истины — `core/settings.py`** — реестр `Setting`, `Block`, `Group`, `GROUPS` (5 субвкладок: `llm`, `transfer`, `glossary`, `checks`, `book`; блоки веб-сервера — последние в `llm`), `SETTINGS`, `BY_KEY`, `BY_BLOCK`, `STAGES`, `STAGE_TITLES` (название стадии — одно на реестр и на запуски), `LLM_BLOCKS`, `SERVER_BLOCKS`, `LLM_ALIAS`, `STAGE_LLM_FIELDS`; чтение — `groups`, `web_settings`, `web_values`, `stage_fields`, `settings_of`, `form_fields`, `defaults`, `llm_settings`, `llm_values`, `stage_values`, `env_key`, `env_file`, `file_values`, `layered_values`, `effective`; формы — `llm_form`, `with_llm`; **поля режима** — `when`/`when_any`/`when_set` у самой настройки (условие — пара `("поле режима", (значения,))`, булевы сравниваются как `"1"`/`"0"`; пустой или отсутствующий режим ничего не режет) и `applies`, `applicable_form` — один источник правды и для argv запусков, и для формы SPA; запись — `sanitize`, `write_values`; для SPA — `display_value`, `block_payload`, `groups_payload`. Ручных блоков «какое поле в каком режиме видно» в `web/stages.py` и SPA быть не может: режим описан у настройки.
- **профили LLM** — там же: `PROFILE_DEFAULT`, `PROFILE_DEFAULT_TITLE`, `PROFILE_FIELD`, `PROFILE_ENV`, `PROFILES_NAME`, `profiles_file`, `profiles_read`, `profiles_write`, `profiles`, `profile_get`, `profile_values`, `profile_display`, `profile_slug`, `profile_create`, `profile_rename`, `profile_delete`, `profile_save_values`, `profiles_payload`, `profile_defaults` (новый профиль — значения General), `is_llm_stage`, `profile_field`.
- **проекты и шаблоны: только `core/projects.py`** — `DEFAULT_SECTIONS` (алиас `SECTIONS`), `load_sections`, `save_sections`, `create_section`, `rename_section`, `delete_section`, `ensure_projects_root`, `valid_project_name`, `sanitize_project_name`, `list_projects`, `project_stats`, `project_progress_table`, `create_project`, `move_project`, `rename_project`, `copy_project`, `delete_project`, `list_template_sets`, `TEMPLATE_SKELETON`, `_ensure_template_skeleton`, `create_template_set`, `create_template_dir`, `copy_template_set`, `delete_template_set`, `templates_files`, `read_template_file`, `write_template_file`, `delete_template_file`, `template_file_info`, `move_template_file`, `fill_project_from_template`, `render_metadata`, `write_project_metadata`.
- **зависимости (`core/deps.py`)** — `ROLES`, `status`, `format_status`, `missing_hint`, `main`.

Запрещено: свои парсеры `.env`, свои обработчики SSE, свои парсеры имён папок-разделов, прямой импорт `httpx` вне транспорта. Добавил функцию в общий слой — добавь её в `core/README.md`, в этот список и в зеркало `tests/test_docs.py`.

## 7. Ключевые конвенции

### Конфигурация: один общий .env

Весь серверный конфиг — в ОДНОМ общем `.env` (подключение LLM, модель, дефолты стадий, `WEB_*`). Страница «Настройки» рисует реестр пятью субвкладками; путь общего конфига интерфейсу не показывается: путь — реализация, а не настройка. У книги своего `.env` нет: изменённые для одной книги поля запусков — рабочее состояние браузера (localStorage), кнопка «Сбросить настройки» возвращает значения общего конфига. Без `.env` скрипт обязан работать дальше с ручным вводом — не падать.

Приоритет: **флаг CLI > `os.environ` > общий `.env` > встроенный дефолт реестра**. Слои: реестр (ключ, метка, тип, дефолт, подсказка, владелец-стадия — отсюда же дефолты CLI-флагов) → общий `.env` (`core.common.system_env_file`: `WEB_ENV_FILE` → корневой `.env` репо → `cwd/.env`) → профиль LLM, если стадия выбрала не General → `os.environ` (в compose задают только `WEB_*`; LLM-конфиг и дефолты стадий через compose не задают) → флаги запуска. В Docker конфиг лежит в постоянном томе (`WEB_ENV_FILE=/app/projects/.env`), заводского `/app/.env` в образе нет; в portable-сборке `.env` — единственная точка настройки. Подробности запуска — в `DEVELOPERS.md`.

Синтаксис `.env`: `KEY=VALUE`, `export ` терпим, парные кавычки снимаются; `#` вне кавычек начинает комментарий (значение с решёткой пишется в кавычках); `${VAR}` не раскрывается, `$` и `{` — обычные символы; пустое значение — пустая строка, она не затеняет глобальный ключ; переносы в textarea-настройках хранятся литералом «\n».

LLM-подключение (host/model/api_key/temperature, потоки, повторы, рассуждения) — одно на стадию и приходит из её профиля: у стадии своего сервера и модели нет, стадийных `<СТАДИЯ>_HOST/_MODEL/_API_KEY` в реестре не появляется. Из LLM-полей в форме стадии ровно одно — «Профиль LLM». Рассуждения модели (`REASONING_MODE`/`THINKING_PROFILE`/`REASONING_EFFORT`/`THINKING_BUDGET`) — общие ключи, в argv стадий они не передаются.

**Профили LLM** — когда наборов настроек несколько (дома один сервер, в облаке другой): General встроенный, его значения и есть общий `.env` (не переименовывается и не удаляется); остальные — один файл `llm_profiles.json` рядом с ним, только переопределения, пустое поле наследует General. Новый профиль создаётся **копией значений General** (`profile_defaults`), а не пустой формой: «свой сервер» иначе пришлось бы заполнять с нуля. В интерфейсе профиль выбирают одним списком (не чипсами: их больше трёх, и строка чипсов выглядела второй панелью вкладок). Профиль выбирает **каждая LLM-стадия своего проекта** — это первое поле её формы; смена профиля — не «изменённая настройка», кнопка «Сбросить настройки» её не трогает. Выбор живёт в браузере (`localStorage nmProfile:<раздел>/<книга>:<стадия>`), в `.env` и в argv его нет; подпроцесс получает `NM_LLM_PROFILE=<id>`, поэтому скрипты читают реестр как читали. Встроенные дефолты подключения: `HOST=https://routerai.ru/api/v1`, `MODEL=google/gemma-4-31b-it`, ключ пуст.

**UI-предпочтения — в localStorage, не в `.env`**: тема интерфейса (переключатель в шапке — SVG-иконка moon/sun с `aria-label`, доступен с любого экрана), тема и кегль редакторов, кегль предпросмотра (локальная субвкладка «Внешний вид» на «Настройках», без полей реестра и без кнопки «Сохранить»), автообновление, сортировка файлов, локальные значения запусков.

**Последнее состояние вкладок книги — тоже браузер, один ключ `nmTab:<раздел>/<книга>`** (`UICore.projectPrefs`): какая вкладка была открыта, глава и обе панели «Редактора» с режимом и подсветкой, тип файлов «Глав», сортировка глоссария, папка и файл «Логов», открытый промпт и отчёт «Заметок». Ссылка с конкретной вкладкой старше памяти; другая книга ничего не наследует. Это состояние просмотра, а не настройки книги: в `.env` оно не попадает.

### Канон глав

Имена папок парсятся ТОЛЬКО через `parse_chapter_id` (`00000_1_…`, `000001_…`, `1_x`, числа…). Файл главы ищется только через `find_chapter_file` (точные имена → подстрока типа → единственный безопасный txt; чёрный список: raw/draft/translated/original/source/backup). Там, где дубли файлов — катастрофа, передавай `strict=True`.

### Unicode

Везде, где сравнивается или заменяется русский текст — NFC-нормализация (`unicodedata.normalize("NFC", …)`). Кавычки «»/"", тире —–−, многоточия …/... считаются разными.

### Regexp-поля (все стадии)

Regexp-поля форм и CLI — чистые стандартные выражения Python `re` (MULTILINE: «^»/«$» — начало и конец СТРОКИ); регистр и прочие режимы — стандартными inline-флагами ((?i), (?s)…). Кастомные флаги (« |i», « |s») и комментарии « # …» в regexp-полях запрещены: особые семантики («пропуск первого совпадения») обеспечивают сами скрипты.

### JSON-файлы данных

Поля в JSON-файлах данных (`ner_review.json`, `translate_check_llm_review.json` и т.п.) — по умолчанию на английском: `entries`, `status`, `applied`, `reason`, `stage`, `chapter`, `file`, `type`, `term`, `field`, `old`, `new`, `created`, `updated`, `note`. Значения (статусы «принять»/«отклонить», тексты ошибок, логи) остаются русскими. Переименование полей — жёсткое, без чтения старых ключей.

### Промпты

- Содержимое `<system>…</system>` уходит в системное сообщение, остальное (и `<user>…</user>`) — в user; без разметки весь промпт в user. Правила и описательные константы — в `<system>`, задание и данные — в `<user>`; данные вставляются только через плейсхолдеры, а дописывать их в конец промпта кодом можно лишь для старых внешних промптов без плейсхолдера (с предупреждением). В промптах — только реально обрабатываемые теги; данные размечаются текстовыми маркерами («=== ГЛОССАРИЙ ===»), фиктивной xml-разметки нет.
- Внешние промпты лежат в `prompts/` проекта; теги: `<translate>`, `<translate_lr>`, `<redact>`, `<polish>`, `<pass1>`, `<pass2>`, `<prompt_pass1>`, `<prompt_pass2>`, `<prompt_ner_check>`, `<prompt_rag>`, `<prompt_assessment>`, `<prompt_assessment_summary>`, `<prompt_wiki_article>` + JSON-теги wiki (`<wiki_markers>`, `<wiki_default_markers>`, `<wiki_type_names_ru>`, `<wiki_relations_labels>`, `<wiki_skip_relations>`, `<wiki_type_order>`). Файл без тегов = промпт этапа целиком (допустимый режим «отдельный файл на этап»).
- Встроенные промпты в скриптах (`DEFAULT_*`, `PASS1_PROMPT`) — только запасной вариант; меняя встроенный, синхронизируй смысл с внешним шаблоном.
- Плейсхолдеры: `{ner_block}`, `{original_text}`, `{translated_text}`, `{female_names}`, `{male_names}`, `{dict_block}`, `{rules_block}`, `{fewshot_block}`, `{chunk_text}`, `{ner_json}`, `{glossary}`, `{fields}`, `{rag_block}`, `{batch_text}`, `{errors_json}`; форматирующие `{translation}` и `{relations_label}` — в системном шаблоне wiki.

### Логирование и артефакты стадий

- Проектные логи — `logs/`, логи стадий по главам — `logs/chapters/`; `setup_logging` заменяет расширение выходного файла на `.log`.
- Цепочка артефактов фиксирована: `chapter.txt → translated.txt (+translated_trace.json) → redacted.txt → polished.txt`; trace-JSON — мост translate→redact (пары original/translated), polish trace не пишет; `_STAGE_IO` в `web/pipeline.py` фиксирован.
- Глоссарий — всегда `ner.json` в корне проекта (выбор файла в web убран). Review-файлы правок, снапшот глоссария, отчёт оценки и сборки compile — в `tmp/` проекта.

### UI/UX

- **Тултипы** — `attachTooltip(el, text)`: все чекбоксы и сложные контролы (select/textarea/password/files) с полем `help` получают всплывающую подсказку при наведении и фокусе; у text/number подсказка — inline `.field-help` под полем. Новое поле с `help` — тултип обязателен.
- **Множественный выбор** (типы глоссария, типы по полу) — чипсы-чекбоксы из реальных данных проекта с кнопками «Выбрать все / Снять все», хотя бы один пункт выбран.
- **Важные режимы** — карточки-пресеты с названием и описанием, а не абстрактный select.
- **Действия в списках** — SVG-иконки (`UICore.icon` + `iconBtn`) с подписью в тултипе и `aria-label`; эмодзи в UI не появляется. На строке — только действия одного объекта; опасные и групповые (удалить, перенести, скачать) — в панели выделения, которая заменяет кнопки тулбара, пока выделение есть.
- **Согласование UI** — схемой до правки, и только ASCII (mermaid в терминале — просто код, он уместен лишь в markdown). Схема — пожелание, а не ритуал: рисуй её, когда она объясняет раскладку быстрее слов.

## 8. Запреты

- **Dev-сервер поднимать и останавливать только `./dev.sh start|stop|status`** (PID в `.tmp/dev.pid`, данные — временная папка `/tmp/nm-dev`, порт 8799). Искать процесс по имени скрипта (`pkill -f web/main.py`) нельзя: боевой контейнер поднимает тот же `web/main.py` и виден из общего PID namespace хоста, а `restart: unless-stopped` прячет следы за «сам перезапустился». Если процесс всё-таки ищется вручную — отличить его можно по `/proc/<pid>/cwd` (у dev — каталог репозитория, у контейнера `/app`) и по `/proc/<pid>/cgroup` (у контейнера — `docker-…`); останавливать только по PID, никогда по маске.
- **Dev и Prod разделены**: рабочие проекты живут в репо (`projects/<раздел>/<книга>/` — gitignored), боевые — в Docker-контейнере вне репо (обычно `~/dockers/NovelMaestro/`). Боевой контейнер и его bind-mount-данные (`projects/`, `templates/`, `web/job_logs` вне репо) не трогать без явной просьбы — там реальные книги. Для проверок и тестов — только временные данные (pytest `tmp_path`, `/tmp`, моки API); PUT/POST/DELETE к живому web-серверу против боевых проектов запрещены. Эксперименты с книгами — только в разделе `projects/TMP`: копируй туда книгу из ACTIVE/HOLD/DONE и работай с копией.
- Не менять имена артефактов стадий и канон `parse_chapter_id` без миграции всех потребителей и тестов.
- Не обходить `.gitignore`: `projects/`, `servers/`, `Images/`, `backup/`, `__pycache__/` и корневой `.env` в git не попадают.
- Не менять единицы параметров (символы ↔ токены) «для красоты».
- Не убирать быструю проверку неудачи в `web/pipeline.py` (код возврата 0 + непустой выходной файл + поиск слов-ошибок): текст перевода в stdout скриптов не попадает, жёсткий `_ERROR_RE` ловит реальные сбои; настройка списка слов-ошибок = регресс.
- Не читать файлы с приватными SSH-ключами.
- Не возвращать интерактивный CLI/TUI и `backends/` — интерфейс web-only, `cli/` — только argparse (§3).
- **Комментарии в коде — только для понимания**: минимальные, объясняют «почему», а не «что». Комментарии-дневники запрещены: любой номер или название пункта плана — раунд, этап, веха, сессия, «задача 10», «пункт 3» — и отчёты о правках; их место только в `TODO.md` (в коммитах то же самое, §11).

## 8а. pi-lens (настройки шума)

- `~/.pi-lens/config.json` (глобально, вне репо): `tests.enabled: false` — встроенный тест-раннер зовёт `python`, которого в системе нет (ENOENT-шум); тесты гоняем вручную: `./dev.sh test`.
- `.pi-lens.json` (в репо): `format.enabled: false` — файлы не переформатировать автоматически (перекраивает весь файл, лишние изменения в диффе); отключены предупреждения о дубликатах и шумные правила; `ignore` — projects/, servers/, Images/, backup/, `__pycache__/`, .venv/.
- Находки анализаторов — подсказки, не истина: перед реакцией сверься с фактическим состоянием (grep, `node --check`, pytest). Устаревший кэш повторяет уже снятые находки — помечай их ложными через `lens_diagnostic_mark`, а проверяй прогоном тестов.

## 8б. UI-правки: headless-прогон Playwright со скриншотами

- **Меняешь UI — прогон со скриншотами обязателен, «глазами открыл вкладку» не считается.** Общий проход: `./dev.sh probe --shot` (`tools/ui_probe.mjs`): сам поднимает сервер на временных данных, проходит все экраны, вкладки проекта и модалки, ловит `pageerror`, `console.error` и ответы 4xx/5xx и пишет PNG и `report.json` в `logs/ui_probe/`. Выход 0 — чисто, 1 — список находок. Скриншоты — артефакты прогона: `logs/` в `.gitignore`, в git они попадать не должны.
- Точечно: `./dev.sh probe --shot --only settings project/run` (список целей — ключи `ROUTES` и вкладок проекта внутри `tools/ui_probe.mjs`); `--url`/`--keep` — пройтись по уже поднятому серверу; `PROBE_VERBOSE=1` — лог сервера в stdout.
- Playwright стоит глобально (`playwright-core`, chromium в `~/.cache/ms-playwright`): своей npm-папки и сборки в репо нет и не будет, node используется только как `node --check`, `node --test` и для прогона.
- Юзерскрипты (`tools/NovelMaestro_Lite/`, `tools/rulate_reload/`) имеют свои полигоны в `/tmp/nm_probe/` (таблица — `tools/NovelMaestro_Lite/AGENTS.md`): читалка, топбар, панель ⋮, настройки; артефакты прогона — там же, в /tmp, не в git.
- Прогон без `--shot` скриншотов не пишет: сравнить «до/после» правки будет нечем.

## 9. Как вносить изменения

0. **Релизы и VERSION** — релиз (тег `v*` + GitHub Release, см. packaging/README.md) делается ТОЛЬКО по явному запросу пользователя. CHANGELOG.md не ведётся — история между релизами восстанавливается по коммитам (`git log v<старый>..v<новый>`), заметки GitHub Release генерирует workflow. Версия — файл `VERSION` в корне (обновляется при релизе), читает его `web/version.py`; приоритет: `NOVELMAESTRO_VERSION` → `VERSION` → запасное значение. Планы в отдельные файлы не выноси. **Отметки и коммиты — по мере выполнения**: сделал задачу или её законченную часть — сразу отметь чекбокс в `TODO.md`, закоммить и запушь.
1. Общая логика → `core/common.py` (профиль LLM, флаги и прогресс стадии → `core/stage.py`), с записью в `core/README.md`, в список §6 и в зеркало `tests/test_docs.py`.
2. Новый исполнитель → `cli/xxx.py` (argparse, bootstrap §4).
3. Новая стадия в web → строка в `web/stages.py::STAGE_SPECS` (ключ-слаг, title, script, build-функция, fields) + форма в SPA.
4. Новый роут API → доменный `web/api_<домен>.py` (хендлер и `router.add` туда же; `web/api.py` — только фасад) + строка в таблицу `web/README.md`.
5. Новая функция web-слоя → `web/*.py`, общая логика — только из `core/`.
6. Тесты → `tests/`, один модуль — один файл; для новых функций core параметризованные тесты обязательны.
7. **Перед коммитом — обязательно:**
   - `./dev.sh test` (это `python3 -m pytest tests/ -q -n auto`; весь набор — единицы секунд). Последовательно (`-n 0`) — только при отладке одного теста. Коммит с падающими тестами запрещён.
   - менял `core/`, `cli/`, `web/`, `run.py` — проверь, что тесты это покрывают; не покрывают — добавь тесты в том же коммите;
   - smoke-запуск затронутого скрипта с `--help` (и `--dry-run`, если поддерживается);
   - менял SPA (`web/static/*.js`) — `./dev.sh spa` и `./dev.sh probe --shot` (§8б); менял UI юзерскриптов — тот же принцип: прогон полигоном, скриншоты до/после;
   - **закоммить и запушь** (`git add -A` → `git commit` → `git push origin`). Незапушенный коммит — не завершённая работа. Тесты в сеть не ходят: LLM мокается на `stream_chat_completion` и `core.transport.open_stream`, данные — в `tmp_path`.
8. **Ветка одна — `main`** (ветвление `dev` упразднено), CI — `tests.yml` только на `main`.
9. Сообщения коммитов — по §11.

Стиль кода: русский в строках, логах и UI; компактные функции; docstring на каждую публичную функцию core; секции в `core/common.py` разделены комментариями `# ══…`.

## 10. Быстрая проверка среды

```bash
./dev.sh test                                     # тесты: pytest -n auto (обязательно перед коммитом)
./dev.sh test -n 0 tests/test_ner.py              # один файл последовательно — для отладки
./dev.sh spa                                      # SPA: node --check по всем файлам + node --test tests/spa/
./dev.sh probe --shot                             # UI: обход экранов + скриншоты в logs/ui_probe/
./dev.sh cov                                      # покрытие (движки стадий считаются тем же прогоном); кэш тестов и данные замера — в .tmp/
python3 run.py                                    # web-интерфейс (сервер + браузер)
python3 web/main.py --help                        # флаги сервера
python3 cli/translate_book.py --help              # единый LLM-скрипт
python3 -m core.deps                              # активный стек зависимостей
# публикация изменений (обязательно):
git add -A && git commit -m "…" && git push origin
```

Карта тестов (принцип один: модуль ↔ файл):

- `tests/conftest.py` — хелперы (`SilentLog`, `make_ru_chapter_file`, `feed`, `fake_env`, `isolated_env_layers`, `ensure_tmp`, `srv_port`), общий HTTP-транспорт тестов (`http_send`, `http_request`, `json_payload`) и хук числа воркеров xdist (бюджет памяти 5 ГБ);
- `tests/test_core_common.py` — `core/common.py` (стрим SSE моками, `.env`, чанкование, поиск терминов, имена по полу, канон глав); `tests/test_core_deps.py` — `core/deps.py` (роли, кандидаты, запасные варианты, статус); `tests/test_projects_core.py` — `core/projects.py`; `tests/test_core_settings.py` — `core/settings.py` (целостность реестра, совпадение метаданных со спеками стадий, слои чтения, запись `.env`, профили); `tests/test_core_stage.py` — `core/stage.py` (имена флагов, порядок источников, форма запроса стадии, прогресс); `tests/test_core_transport.py` — `core/transport.py` (нарезка SSE, нормализация ошибок, живой раунд-трип через stdlib-сервер); `tests/test_run_flows.py` — `run.py`;
- скрипты: `tests/test_translate_book.py`, `tests/test_ner.py`, `tests/test_ner_check.py`, `tests/test_translate_check.py`, `tests/test_translate_check_llm.py`, `tests/test_translate_quality.py`, `tests/test_wiki.py`, `tests/test_epub_to_chapters.py`, плюс `tests/test_cli_units.py` и `tests/test_cli_e2e.py` — чистые функции и `main()` на синтетических данных;
- web: `tests/test_web_pipeline.py` (оркестратор, `build_stage_cmd`, `grep_errors`, `process_chapter`), `tests/test_web_api.py` (пульт, проекты, файлы, глоссарий и review, настройки и профили, шаблоны), `tests/test_web_jobs.py` (JobManager, буфер, очередь, SSE, остановка, сироты), `tests/test_web_content.py` (промпты, обложка, логи, отчёты, поиск), `tests/test_core_search.py` (`core/search.py`: группы, кластеры, фрагменты), `tests/test_web_server.py` (сессия, вход, статика, CSRF, 404 и 405), `tests/test_web_sandbox.py` (песочница путей), `tests/test_web_state.py` (`hub_state`);
- SPA и документация: `tests/test_spa_js.py` и `tests/spa/` (jsdom: `ui-core`, `ui-components`, вкладки проекта, массовый review, оценка по бюджету, справка), `tests/test_prompt_structure.py` (правила в `<system>`, данные в `<user>`), `tests/test_docs.py` (сверка документации с кодом: список §6, пути, `templates/.env.example` ↔ реестр), `tests/test_architecture.py` (гарды §3 и §4);
- инструменты: `tests/test_tools_vendor.py` (манифест вендора: sha256, размер, ни одной ссылки на CDN), `tests/test_tools_userscripts.py` и `tests/tools/lite-*.test.mjs` (юзерскрипты: собранный `.user.js` побайтово равен сборке, части нумерованы и держат обёртку, версия из `meta.js`, чистая логика Lite на заглушках).

## 11. Правила коммитов

Коммиты строго атомарны (одно логическое изменение — один коммит) и оформляются по Conventional Commits: `<type>(<scope>): краткое описание на русском`. Допустимые `type`: `feat`, `fix`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`, `docs`. `scope` — короткое латинское имя модуля (`core`, `cli`, `web`, `api`, `pipeline`, `ner`, `templates`, `tests`, `docs`, `repo`). Описание — инфинитив, до 72 символов, без точки в конце; детали и списки изменений выносятся в тело коммита. Если в одном файле смешаны правки из разных задач, файл можно коммитить целиком в коммит основной задачи.

Категорически запрещены произвольные префиксы и неинформативные сообщения: `W*`, `M*`, `TODO:`, «Шаблоны:», «фиксы», «правки», CAPS, эмодзи. Запрещено смешивать несвязанные правки (новую фичу и обновление статусов `TODO.md` — в разные коммиты). Эталон: `feat(run): добавлен прогресс запусков`, `fix(web): убран кегль 11 из предпросмотра`, `docs(todo): обновлён статус задач`.

**Номера и названия пунктов плана в сообщения коммита не пишутся** — ни в заголовок, ни в тело. Счёт любой величины запрещён: раунд, этап, веха, сессия, а также «задача 10», «пункт 3», «из 12» — коммит обязан оставаться понятным вне плана, а через месяц номер не читается ни с чем. Коммит описывает ЧТО изменилось и в каких файлах; плановые номера, галочки и порядок работ живут только в `TODO.md`. Плохо: `docs(todo): раунд 4 закрыт, политика полей обновлена`, `docs(todo): задача 10 — параметры режимов собраны в реестре`, `feat(web): пункт 3 из плана`; хорошо: `docs(todo): обновлены статусы задач и политика не голосующих полей`, `feat(web): поля режима берутся из реестра настроек`.

## 12. Форматирование документации (.md)

- **Абзац — одна длинная строка** исходника: жёсткие переносы внутри абзаца запрещены, они читаются как лишние разрывы.
- Структура размывается пустыми строками: заголовки, списки, цитаты, таблицы и код-блоки отделяются пустой строкой; соседние строки одного абзаца или списка пустой строкой не разрывают.
- Элементы с сохранённым форматом не перенабираются: код-блоки, таблицы (одна строка на ряд), ASCII-схемы и продолжения элементов списка с отступом остаются как есть.
- Списки: маркеры `-`/`*`/нумерация, каждый пункт с новой строки, подпункты — с отступом, продолжение пункта — с отступом без маркера.
- Не склеивать при форматировании заголовки, элементы списков, строки таблиц, цитаты и разные абзацы.
- Терминология: `README.md` — лендинг, `TODO.md` — планы и статусы, `AGENTS.md` — правила агента; правки документации — коммит с типом `docs`.

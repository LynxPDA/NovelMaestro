# DEVELOPERS.md — техническая документация NovelMaestro

Техническая документация для разработчиков: архитектура, конвейер,
структура данных, конфигурация, промпты, тесты и соглашения.
Пользовательское описание и быстрый старт — в [README.md](README.md).

| Документ | Содержание |
| --- | --- |
| [README.md](README.md) | Лендинг: что это, установка, первые шаги |
| [AGENTS.md](AGENTS.md) | Правила и ограничения для AI-агентов (контракт) |
| [core/README.md](core/README.md) | API общего модуля `core/` |
| [web/README.md](web/README.md) | Контракт web-слоя и API |
| [tools/README.md](tools/README.md) | Вспомогательные утилиты (Rulate userscript) |
| [packaging/README.md](packaging/README.md) | Релизные сборки (Docker, Windows) |

---

## Архитектура (три слоя)

```text
core/     общий код: common.py (логика) + projects.py (менеджмент
          проектов). НЕ скрипты.
web/      web-интерфейс: server.py + api.py (роуты/хендлеры), stages.py
          (реестр стадий и сборка argv), jobs.py (JobManager + SSE),
          pipeline.py (web-оркестратор конвейера), static/ (SPA).
          Контракт API — web/README.md.
cli/      исполнители — чистый CLI (argparse), без интерактивных меню.
tools/    вспомогательные утилиты вне конвейера.
templates/ шаблоны новых проектов: общие (.env.example,
          replacements.txt.example) + подпапки по типу книги (General/
          с prompts/, source/ и donate.txt).
run.py    лаунчер: python3 run.py → web/main.py (+браузер).
projects/ <раздел>/<книга>/ — данные проектов (НЕ в git).
tests/    pytest P0–P2 (один модуль — один файл тестов).
```

Правила слоёв:

- интерактив — только в браузере (SPA) и в `web/`; `cli/` — только
  argparse, без `input()` и без импорта UI-слоёв;
- общая логика — только в `core/`; скрипты заимствуют импортом из
  `core.common`, НЕ копируют функции себе; менеджмент проектов —
  только `core/projects.py`;
- web-модули импортируют соседей через `from web.* import …`, а общее —
  из `core.common`; логика из `core/` в `web/` НЕ дублируется;
- проекты НЕ содержат копий скриптов (легаси-копии в DONE-проектах —
  `_legacy_scripts/`, заморожены).

## Конвейер и стадии

Каждая книга проходит стадии (порядок карточек в «Запусках» задаёт
`web/stages.py::STAGE_ORDER`; слаги — контракт API, не менять):

```text
epub → ner → ner_check → pipeline → translate_check →
translate_check_llm → batch_replace → compile → wiki
```

| Слаг | Название в UI | Скрипт | LLM |
| --- | --- | --- | --- |
| `epub` | Разбор исходника на главы | `cli/epub_to_chapters.py` | нет |
| `ner` | Создание глоссария (LLM) | `cli/ner.py` | да |
| `ner_check` | Проверка глоссария (LLM) | `cli/ner_check.py` | да |
| `pipeline` | Перевод (LLM) | `web/pipeline.py` | да |
| `translate_check` | Проверка перевода | `cli/translate_check.py` | нет |
| `translate_check_llm` | Проверка перевода (LLM) | `translate_check_llm.py` | да |
| `batch_replace` | Массовые замены | `cli/batch_replace.py` | нет |
| `compile` | Компиляция TXT/EPUB/FB2 | `cli/clean_and_compile.py` | нет |
| `wiki` | Создание Wiki (LLM) | `cli/wiki.py` | да |

Детали каждого слага (поля форм, режимы, параметры) — в
`web/stages.py::STAGE_SPECS` и в Справке web-интерфейса
(`web/static/help.md`).

### Артефакты стадий (НЕ менять имена)

`chapter.txt → translated.txt (+translated_trace.json) → redacted.txt →
polished.txt`. Trace-JSON — мост translate→redact (пары
original/translated); polish trace НЕ пишет. `_STAGE_IO` в
`web/pipeline.py` — фиксирован.

### Канон глав

Имена папок парсятся ТОЛЬКО через `parse_chapter_id` (00000_1_…,
000001_…, 1_x, числа…). Поиск файла главы — только через
`find_chapter_file` (приоритеты: точные имена → подстрока типа →
единственный безопасный txt; blacklist:
raw/draft/translated/original/source/backup). Где дубли файлов =
катастрофа, передавай `strict=True`.

## Структура папки проекта

```text
MyNovel/
├── source/
│   ├── cover.jpg          # обложка (варианты cover.<ext>)
│   ├── metadata.yaml      # метаданные для EPUB/FB2 (варианты yaml)
│   ├── donate.txt         # страница поддержки (опционально)
│   └── novel.epub         # исходник
├── chapters/
│   ├── 00000_1_Заголовок/ # <нули>_<номер>_<заголовок>
│   │   ├── chapter.txt
│   │   ├── translated.txt
│   │   ├── translated_trace.json
│   │   ├── redacted.txt
│   │   └── polished.txt
│   └── ...
├── prompts/               # промпты стадий + replacements.txt
├── logs/                  # логи прогонов; logs/chapters/ — по главам
├── tmp/
├── ner.json               # глоссарий
├── ner_review.json        # правки ner_check (принять/отклонить)
├── ner_changes.md         # лог применённых правок
├── translate_check_llm_review.json  # правки проверки перевода LLM
├── wiki.md                # wiki-компендиум
├── compiled_1_50_txt.txt  # собранный TXT
└── MyNovel_1_50.epub/fb2  # собранные EPUB/FB2
```

## Конфигурация LLM-сервера

Весь серверный конфиг — в корневом `.env` репо (LLM-профили, модели по
стадиям, дефолты пайплайна, `WEB_*`, настройки внешнего вида);
проектный `pdir/.env` приоритетнее для конкретной книги.
Приоритет: `CLI-флаг` > `os.environ` > `.env` > встроенный дефолт.
Без `.env` скрипт обязан работать дальше с ручным вводом — не падать.
Файлы `.env` содержат API-ключи и не коммитятся (шаблон —
`templates/.env.example`).

```ini
# Единый сервер LLM (vLLM, Ollama, LM Studio, OpenAI-совместимый)
HOST=http://localhost:8080/v1
API_KEY=your-api-key
MODEL=gemma-3-novel-224b

# Сервер конкретного скрипта (необязательно; fallback на общие ключи)
# Схема «один скрипт — один набор сервер + ключ + модель»:
# <СКРИПТ>_HOST / <СКРИПТ>_API_KEY / <СКРИПТ>_MODEL
NER_HOST=...
NER_API_KEY=...
NER_MODEL=...
NER_CHECK_HOST=...        # проверка глоссария
TRANSLATE_CHECK_LLM_HOST=...
WIKI_HOST=...
PIPELINE_HOST=...         # web-конвейер (единый сервер и модель)
```

Приоритет сервера: `<СКРИПТ>_HOST` → `HOST`, ключ —
`<СКРИПТ>_API_KEY` → `API_KEY`, модель — `<СКРИПТ>_MODEL` → общая
`MODEL`. Модель обязательна: из `--model` или `.env`, автоопределение
через `GET /models` убрано. Отдельных моделей под
перевод/редактуру/полировку нет (`TRANSLATE_MODEL`/`REDACT_MODEL`/
`POLISH_MODEL` убраны).

Web-сервер читает `WEB_HOST`, `WEB_PORT`, `WEB_AUTH`, `WEB_TOKEN`,
`WEB_MAX_UPLOAD_MB`, `WEB_JOBS_LIMIT`, `WEB_PROJECTS_DIR`.

**Настройки запусков (R9).** Поля форм «Запусков» предзаполняются из
`.env` и при каждом запуске сохраняются обратно: системный корневой
`.env` репо копируется в папку проекта (`pdir/.env`), если её нет,
затем обновляются ключи `<STAGE>_<FIELD>` (например `NER_CHUNK_SIZE`,
`TRANSLATE_CHECK_EXCLUDE_WORDS`; сервер — `<STAGE>_HOST` → `HOST`,
ключ — `<STAGE>_API_KEY` → `API_KEY`, модель — `<STAGE>_MODEL` →
общая `MODEL`). Ключ сохраняется как `<STAGE>_API_KEY` (fallback —
`API_KEY`); системные `WEB_*` в проект не копируются.

## Промпты

Внешние промпты — в `prompts/` проекта; формат — теги:

| Стадия | Файл (по умолчанию) | Теги (в режиме общего файла) |
| --- | --- | --- |
| Перевод | `prompts/pipeline_prompt.txt` | `<translate>` |
| Редактура (redact) | `prompts/pipeline_prompt.txt` | `<redact>` |
| Полировка (polish) | `prompts/pipeline_prompt.txt` | `<polish>` |
| NER-извлечение | `prompts/ner_prompt.txt` | `<prompt_pass1>`, `<prompt_pass2>` |
| Проверка глоссария | `prompts/ner_check_prompt.txt` | — (файл целиком) |
| Проверка перевода (LLM) | `prompts/translate_check_prompt.txt` | `<pass1>/<pass2>` |
| Wiki-статьи | `prompts/wiki_prompt.txt` | `<prompt_wiki_article>` |

Файл БЕЗ тегов = промпт этапа целиком (допустимый режим «отдельный
файл на этап»). Встроенные промпты в скриптах (DEFAULT_*/PASS1_PROMPT) —
только fallback. В конвейере — единый «Общий промпт-файл» с тегами
`<translate>/<redact>/<polish>` (`pipeline_prompt.txt` → `prompts.txt`
→ `translate_book_prompt.txt` при пустом поле формы).

Плейсхолдеры: `{ner_block}`; `{original_text}` — входной текст
(translate/polish: нет тега — текст дописывается после промпта;
redact: внутри `<source_text>`); `{translated_text}` (redact);
`{female_names}`, `{male_names}` (polish: имена из ner.json по полю
`translation`, пол по наличию `(female)`/`(male)` в `type`).

Нюансы ответов LLM:

- **NER** — строго валидный JSON-массив без markdown-обёртки; поля
  `term`/`reading`/`type`/`translation`/`notes`/`context`. `reading` —
  произношение/чтение термина (для китайского — пиньинь с тонами);
  парсер принимает и `pinyin`, и `reading` (`merge_alias_groups` в
  ner.py, поле ищется по обоим именам).
- **wiki** — статья обычным текстом (маркдаун в шаблонах статей);
  `--near-distance` — единица FTS5 NEAR (ТОКЕНЫ).
- **translate_check_llm** — review-записи полями `stage`/`status`/
  `old`/`new` (см. `merge_fix_entries`).

## Шаблоны проектов

`templates/` — стартовые файлы: общие в корне (`.env.example`,
`replacements.txt.example`), подпапки по типу книги (`General/`) с
`prompts/` + `source/` (metadata.yaml, donate.txt).
Скелет набора — `core/projects.py::TEMPLATE_SKELETON`; каталоги в
наборах неизменяемы (`create_template_dir` всегда ошибка);
`General` — системный (создание/удаление/запись → 400/403).

## Web-слой

Сервер — чистый stdlib (`http.server`), SPA — ванильный JS без
сборки (vendored: Alpine.js/CodeMirror/marked в `static/vendor/`, без
CDN в рантайме). Подробный контракт (роуты API, JobManager, SSE,
песочница, прогресс) — в [web/README.md](web/README.md).

Ключевые точки:

- `web/stages.py::STAGE_SPECS` — реестр стадий: поля форм, build-функции
  сборки argv, пресеты простого режима;
- `web/pipeline.py` — web-оркестратор конвейера: `@@CHAPTER@@`-события
  для таблицы глав, fail-fast (returncode 0 + непустой выходной файл +
  grep слов-ошибок `_ERROR_RE`);
- `web/jobs.py` — JobManager: Popen c `start_new_session` +
  reader-поток + SSE; killpg по группе; лимит параллельности
  `WEB_JOBS_LIMIT` (429), одна стадия на проект (409);
- `web/api.py` — роуты; `_stage_options` — опции форм (главы, source-пул
  со всеми файлами, auto_prompt); `_env_put` — сид из системного `.env`
  без секретов при отсутствии `pdir/.env`.

## Тесты

```bash
python3 -m pytest tests/ -q                        # все тесты
python3 -m pytest tests/ -q --cov=core --cov=cli --cov=web  # покрытие
```

- `tests/conftest.py` — общие хелперы (SilentLog, make_ru_chapter_file,
  feed, fake_env);
- `tests/test_core_common.py` — `core/common.py` целиком (стрим SSE
  моками, .env, чанкование, NER-поиск, имена по полу, канон глав);
- `tests/test_projects_core.py` — `core/projects.py`;
- по одному файлу на скрипт: translate_book, ner, ner_check,
  translate_check_llm, wiki, epub_to_chapters, translate_check — чистые
  функции + оркестраторы и `main()` с моками LLM;
- `tests/test_cli_units.py` / `test_cli_e2e.py` — остальные `cli/` без
  сети (batch_replace, clean_and_compile и др.);
- `tests/test_web_*.py` — web-слой (роуты, JobManager, SSE, env-редактор,
  NER-экспорт) на реальном HTTP-сервере без сети;
- `tests/test_docs.py` — сверка доков (AGENTS.md §6, пути) с кодом;
- `tests/test_architecture.py` — регресс-гарды архитектуры;
- `tests/test_spa_js.py` + `tests/spa/*.test.mjs` — SPA (node --check,
  node --test чистых функций).

Тесты НЕ ходят в сеть: LLM только мокать (monkeypatch на
`stream_chat_completion` / `requests.post`), данные — во временных
папках pytest (`tmp_path`).

## Соглашения

- **Единицы.** СИМВОЛЫ: `--chunk_size`, `--context_budget`, все пороги
  длин, размеры чанков/пакетов, FTS5 chunk (`min_len_ratio` —
  безразмерное отношение). ТОКЕНЫ: только `max_tokens` (серверный
  предохранитель) и `--near-distance` (wiki, природа FTS5 NEAR).
  БАЙТЫ: размеры файлов в отчётах translate_check. ГЛАВЫ: чанкование
  в clean_and_compile. Единица обязана быть указана в help argparse.
- **Unicode.** NFC-нормализация везде, где сравнивается/заменяется
  русский текст. Кавычки «»/", тире –—-, многоточия …/... считаются
  разными.
- **JSON-файлы данных.** Ключи — по умолчанию на английском (`entries`,
  `status`, `applied`, `reason`, `stage`, `chapter`, `file`, `type`,
  `term`, `field`, `old`, `new`, `created`, `updated` …); значения
  (статусы «принять»/«отклонить», тексты ошибок, логи) — русские.
  Новые ключи — только английские; переименование жёсткое, без
  fallback-чтения старых ключей.
- **Логирование.** Проектные логи: `logs/`; по главам — `logs/chapters/`.
  `setup_logging` заменяет расширение на `.log`; после него —
  `log_argv(logger)` (фактическая команда запуска).
- **Bootstrap.** Новые скрипты находят корень репо подъёмом вверх
  (`_bootstrap_core()`, продублирован в каждой точке входа осознанно)
  и добавляют в `sys.path` перед импортом `core.*`.

## Регулярные выражения

Применяемый диалект — Python `re` (regex101 в режиме Python). Где
используются: разбор исходника (`--split-re`, очистки, замены
`паттерн -> замена`), массовые замены (`--replace`, `replacements.txt`
с флагами `|i`/`|r`), `--replace-re`. Подробное руководство с
примерами — в Справке web-интерфейса (`web/static/help.md`, раздел
«Регулярные выражения»).

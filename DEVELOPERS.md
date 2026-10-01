# DEVELOPERS.md — техническая документация NovelMaestro

Техническая документация для разработчиков: архитектура, конвейер, структура данных, конфигурация, промпты, тесты и соглашения. Пользовательское описание и быстрый старт — в [README.md](README.md).

| Документ | Содержание |
| --- | --- |
| [README.md](README.md) | Лендинг: что это, установка, первые шаги |
| [AGENTS.md](AGENTS.md) | Правила и ограничения для AI-агентов (контракт) |
| [core/README.md](core/README.md) | API общего модуля `core/` |
| [web/README.md](web/README.md) | Контракт web-слоя и API |
| [tools/README.md](tools/README.md) | Вспомогательные утилиты вне конвейера: обзор и сборка |
| [tools/rulate_reload/README.md](tools/rulate_reload/README.md) | Юзерскрипт массового обновления глав на Rulate |
| [tools/NovelMaestro_Lite/README.md](tools/NovelMaestro_Lite/README.md) | Юзерскрипт-переводчик NovelMaestro Lite: лендинг пользователя |
| [tools/NovelMaestro_Lite/DEVELOPERS.md](tools/NovelMaestro_Lite/DEVELOPERS.md) | Юзерскрипт-переводчик NovelMaestro Lite: техническая документация |
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
tools/    вспомогательные утилиты вне конвейера (README — tools/README.md):
          два юзерскрипта (meta.js + src/NNN-slug.js → собранные
          <имя>.user.js и <имя>.meta.js) и build_userscripts.py — сборщик.
templates/ шаблоны новых проектов: общие (.env.example) + подпапки
          по типу книги (General/ с prompts/, source/ и donate.txt).
run.py    лаунчер: python3 run.py → web/main.py (+браузер).
projects/ <раздел>/<книга>/ — данные проектов (НЕ в git).
tests/    pytest P0–P2 (один модуль — один файл тестов).
```

Правила слоёв:

- интерактив — только в браузере (SPA) и в `web/`; `cli/` — только argparse, без `input()` и без импорта UI-слоёв;
- общая логика — только в `core/`; скрипты заимствуют импортом из `core.common`, НЕ копируют функции себе; менеджмент проектов — только `core/projects.py`;
- web-модули импортируют соседей через `from web.* import …`, а общее — из `core.common`; логика из `core/` в `web/` НЕ дублируется;
- проекты НЕ содержат копий скриптов (легаси-копии в DONE-проектах — `_legacy_scripts/`, заморожены).

## Конвейер и стадии

Каждая книга проходит стадии (порядок карточек в «Запусках» задаёт `web/stages.py::STAGE_ORDER`; слаги — контракт API, не менять):

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

Детали каждого слага (поля форм, режимы, параметры) — в `web/stages.py::STAGE_SPECS` и в Справке web-интерфейса (`web/static/help.md`).

### Артефакты стадий (НЕ менять имена)

`chapter.txt → translated.txt (+translated_trace.json) → redacted.txt → polished.txt`. Trace-JSON — мост translate→redact (пары original/translated); polish trace НЕ пишет. `_STAGE_IO` в `web/pipeline.py` — фиксирован.

### Канон глав

Имена папок парсятся ТОЛЬКО через `parse_chapter_id` (00000_1_…, 000001_…, 1_x, числа…). Поиск файла главы — только через `find_chapter_file` (приоритеты: точные имена → подстрока типа → единственный безопасный txt; blacklist: raw/draft/translated/original/source/backup). Где дубли файлов = катастрофа, передавай `strict=True`.

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
├── prompts/               # промпты стадий
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

Серверный конфиг живёт в ОДНОМ общем `.env` (в dev-репо — корневой, в Docker — `projects/.env` в постоянном томе, см. `WEB_ENV_FILE`): LLM-серверы, модели по стадиям, дефолты пайплайна, `WEB_*`. Файл `.env` в папке книги остаётся, но это Дифф: только ключи, которыми книга отличается от общего; пишет его сервер при запуске стадии, отдельно в интерфейсе он не редактируется (на вкладке «Запуски» — бейджи «локально» и сброс отличий стадии). Приоритет: `CLI-флаг` > `os.environ` > `.env` книги > общий `.env` > встроенный дефолт. Без `.env` скрипт обязан работать дальше с ручным вводом — не падать. Файлы `.env` содержат API-ключи и не коммитятся (шаблон — `templates/.env.example`).

Парсер `.env` — `python-dotenv` (`core/common.py::parse_dotenv`): `export ` терпим, парные кавычки снимаются, **`#` вне кавычек начинает комментарий**, интерполяция `${VAR}` выключена. Пишет файлы один санитайзер `web/api_stage.py::_sanitize_env_value`: значение с `#` он сам оборачивает в кавычки; UI-предпочтения в `.env` не живут — они в localStorage.

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

Приоритет сервера: `<СКРИПТ>_HOST` → `HOST`, ключ — `<СКРИПТ>_API_KEY` → `API_KEY`, модель — `<СКРИПТ>_MODEL` → общая `MODEL`. Модель обязательна: из `--model` или `.env`, автоопределение через `GET /models` убрано. Отдельных моделей под перевод/редактуру/полировку нет (`TRANSLATE_MODEL`/`REDACT_MODEL`/ `POLISH_MODEL` убраны).

Web-сервер читает `WEB_HOST`, `WEB_PORT`, `WEB_AUTH`, `WEB_TOKEN`, `WEB_MAX_UPLOAD_MB`, `WEB_JOBS_LIMIT`, `WEB_PROJECTS_DIR`.

**Настройки запусков (R9).** Поля форм «Запусков» предзаполняются эффективными значениями (общий `.env` → файл книги → `os.environ`) и при запуске сохраняются обратно, но в файл книги попадает только diff: значение отличается от общего эффективного — пишется `<STAGE>_<FIELD>` (например `NER_CHUNK_SIZE`; LLM-подключение сравнивается с эффективным `HOST`/`API_KEY`/`MODEL`, а не с одним `<STAGE>_HOST`), совпало или опустело — ключ из файла уходит, без отличий файл удаляется. Служебное (`preset`, `profile`) и `WEB_*` в файл книги не пишутся, старые полные копии конфига вычищаются сами. У изменённого поля — бейдж «локально» (тултип: локальное и общее значения, секреты — «••••»), кнопка «Сбросить локальные (N)» снимает ключи одной стадии.

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

Файл БЕЗ тегов = промпт этапа целиком (допустимый режим «отдельный файл на этап»). Встроенные промпты в скриптах (DEFAULT_*/PASS1_PROMPT) — только fallback. В конвейере — единый «Общий промпт-файл» с тегами `<translate>/<redact>/<polish>` (`pipeline_prompt.txt` → `prompts.txt` → `translate_book_prompt.txt` при пустом поле формы).

Плейсхолдеры: `{ner_block}`; `{original_text}` — входной текст (translate/polish: тег обязателен, нет — предупреждение в лог, текст дописывается после промпта; redact: внутри `<source_text>`); `{translated_text}` (redact); `{female_names}`, `{male_names}` (polish: имена из ner.json по полю `translation`, пол по наличию `(female)`/`(male)` в `type`).

Нюансы ответов LLM:

- **NER** — строго валидный JSON-массив без markdown-обёртки; поля `term`/`reading`/`type`/`translation`/`notes`/`context`. `reading` — произношение/чтение термина (для китайского — пиньинь с тонами); парсер принимает и `pinyin`, и `reading` (`merge_alias_groups` в ner.py, поле ищется по обоим именам).
- **wiki** — статья обычным текстом (маркдаун в шаблонах статей); `--near-distance` — единица FTS5 NEAR (ТОКЕНЫ).
- **translate_check_llm** — review-записи полями `stage`/`status`/ `old`/`new` (см. `merge_fix_entries`).

## Шаблоны проектов

`templates/` — стартовые файлы: общие в корне (`.env.example`), подпапки по типу книги (`General/`) с `prompts/` + `source/` (metadata.yaml, donate.txt). Скелет набора — `core/projects.py::TEMPLATE_SKELETON`; каталоги в наборах неизменяемы (`create_template_dir` всегда ошибка); `General` — системный (создание/удаление/запись → 400/403).

## Web-слой

Сервер — чистый stdlib (`http.server`, `ThreadingHTTPServer`: долгая стадия не блокирует UI; `protocol_version="HTTP/1.1"` — keep-alive, длинный SSE не выдирает соединение из пула браузера; `daemon_threads=True` — поток не держит контейнер; `Handler.timeout` — общий idle-таймаут, глушащий застрявшие потоки), SPA — ванильный JS без сборки. Внешние библиотеки SPA (`CodeMirror`, `marked`) лежат в `static/vendor/` и описаны в манифесте `static/vendor/vendor.lock.json` (версии, лицензии, sha256): ни одного CDN в рантайме, интерфейс работает офлайн; проверка — `python3 tools/vendor_assets.py check`. Alpine.js из вендора удалён: SPA на ванильном JS, возвращать его не надо. Подробный контракт (роуты API, JobManager, SSE, песочница, прогресс) — в [web/README.md](web/README.md).

Ключевые точки:

- `web/stages.py::STAGE_SPECS` — реестр стадий: поля форм, build-функции сборки argv, пресеты простого режима;
- `web/pipeline.py` — web-оркестратор конвейера: `@@CHAPTER@@`-события для таблицы глав, fail-fast (returncode 0 + непустой выходной файл + grep слов-ошибок `_ERROR_RE`);
- `web/jobs.py` — JobManager: Popen c `start_new_session` + reader-поток + SSE; остановка дерева процессов `_kill_tree` (POSIX killpg: SIGTERM → 5 с → SIGKILL; Windows — `taskkill /F /T`); лимит параллельности `WEB_JOBS_LIMIT` (429), одна стадия на проект (409); журнал следует за проектом при move/rename (`update_project_path`);
- `web/api.py` — только фасад: `register()` подключает доменные модули в порядке `api_projects → api_files → api_glossary → api_env → api_assets → api_stage → api_templates`, общий mutable-контекст (кешы, сессия) определён ровно один раз — в `api_common`;
- `web/api_env.py` — единственный редактор `.env`: GET/PUT общего файла (секреты маскируются), список и файлы промптов, `source/metadata.yaml`; книжный `.env` отсюда не читается и не правится;
- `_stage_options` — опции форм (главы, source-пул со всеми файлами, auto_prompt).

## Юзерскрипты (tools/)

Юзерскрипты живут в этом же репо: единый источник истины, code review и история версий в git вместо зеркал (gists для этого не подходит: нет ревью, CI, дерева и issue, а история версий — дропдаун вместо тегов).

Раскладка одного скрипта:

```text
tools/<скрипт>/
├── <имя>.user.js    # публикуемый файл: СОБРАННЫЙ, руками не правится
├── <имя>.meta.js    # собранный файл проверки обновлений: баннер без тела
├── meta.js          # баннер ==UserScript==, единственный источник @version
├── src/000-open.js  # открывающая строка IIFE-обёртки
├── src/NNN-….js     # части, уже на финальном отступе
├── src/900-close.js # закрывающая строка обёртки
└── README.md        # установка, формат файла, настройки
```

Сборщик — `tools/build_userscripts.py` (stdlib + `core.common` для `atomic_write`/`log_argv`): баннер, пустая строка, части в порядке имён. Никаких переотступов, переносов и минификации — артефакт обязан побайтово совпадать с тем, что было бы написано руками, иначе `git diff` артефакта нечитаем. Единственные преобразования: CRLF→LF и подстановка `{{VERSION}}` из `@version`. Порядок частей = порядок секций исходного файла: часть шелла создаёт DOM-ссылки, «События» и «Инициализация» исполняются сразу, так что это функциональный инвариант, а не вкус. Нумерация `NNN` с шагом 10 — чтобы вставка части была новым файлом `025-*.js`, а не переименованием хвоста.

```bash
python3 tools/build_userscripts.py                   # пересобрать после правки в src/
python3 tools/build_userscripts.py --check --node-check  # артефакт == сборка + синтаксис
python3 tools/build_userscripts.py --list            # версии и число частей
```

Дробим только Lite (3,1 тыс. строк, 24 части): rulate (645 строк) остаётся одной частью — файл читаем целиком, сборочная инфраструктура ради него ничего не даёт.

### Где выкладывать и как обновляется

- Ставят по raw GitHub: `https://raw.githubusercontent.com/<владелец>/<репо>/main/tools/<скрипт>/<имя>.user.js`, и по нему же `@downloadURL`/`@updateURL`, которые дописывает сборщик: raw отдаёт `cache-control: max-age=300`, тогда как jsDelivr по ветке — `public, max-age=604800`, и новый коммит этот кэш не снимает: менеджер отвечал «обновлений нет», хотя в репо уже новая версия.
- Зеркало — jsDelivr `https://cdn.jsdelivr.net/gh/<владелец>/<репо>@main/tools/<скрипт>/<имя>.user.js` (MIME `application/javascript`); после релиза его кэш чистит отдельный сервис `https://purge.jsdelivr.net/gh/<владелец>/<репо>@main/…` GET-запросом: обычный GET кэш не сбрасывает, а старый способ `curl -X PURGE` по URL файла отвечает 400.
- Проверка обновлений читает `<имя>.meta.js` — собранный файл с тем же блоком метаданных и без тела: менеджеру не нужно скачивать сотни килобайт ради одной строки версии. Оба собранных файла обязаны быть побайтово равны сборке — за этим следит `tests/test_tools_userscripts.py`.
- `@main` выбран сознательно: скрипты в активной разработке, обновление должно приходить без ожидания релиза. Если захочется обновлений «только релизами» — заменить `@main` на `@latest` (он резолвится на последний релизный тег).
- GitHub Pages и отдельный репозиторий юзерскриптов не нужны, пока у Lite нет внешней аудитории; gist — максимум зеркало, синхронизируемое из CI.
- Автоматизация — `.github/workflows/userscripts.yml`: по кнопке или по пушу тега `v*` проверяет `--check --node-check`, выкладывает `.user.js` ассетами релиза и чистит кэш CDN (GET к `purge.jsdelivr.net`).

### Мелочи выкладки на GreasyFork/GreasyArchive

- Каталоги ре-хостят код сами: «выложил один раз и оно само обновляется» не будет, это ручной пункт релизного чек-листа.
- `@namespace` у обоих скриптов — URL репозитория (проверяется `tests/test_tools_userscripts.py`). Менять его после старта распространения нельзя: менеджер идентифицирует скрипт по name+namespace, поэтому смена дала бы дубль установки вместо обновления.
- Lite объявляет `@match *://*/*`: интерфейс появляется на любой странице. Модератор каталога вправе спросить зачем; при этом без `@noframes` скрипт работает и внутри iframe (двойные кнопки на сайтах с iframe), а с `@noframes` перестанет ловить главы, лежащие в iframe.

## Настройки web-сервера

По умолчанию сервер слушает **только этот компьютер**: `127.0.0.1:8756`. Браузер открывается автоматически; все настройки можно задать флагами `run.py`/`web/main.py`, переменными окружения или в корневом `.env` (приоритет: флаг > окружение > `.env` > дефолт). Те же настройки действуют в портативной сборке Windows и в Docker — см. [packaging/README.md](packaging/README.md).

### Адрес и порт

| Что | Флаг / .env | Дефолт | Назначение |
| --- | --- | --- | --- |
| Адрес | `--host` / `WEB_HOST` | `127.0.0.1` | только этот компьютер; `0.0.0.0` — вся локальная сеть |
| Порт | `--port` / `WEB_PORT` | `8756` | если занят — сервер сам берёт следующий свободный и пишет его в консоль |

- **127.0.0.1 (по умолчанию)** — доступ только с этого компьютера. Безопасно: ключи API и интерфейс не видны сети, токен не нужен.
- **0.0.0.0** — доступ с любой машины в локальной сети (`http://<IP-компьютера>:8756`). В этом режиме **обязательно включите токен** (см. ниже), иначе любой в сети увидит ваш `.env` с ключами.

### Ключи авторизации (токен)

| Что | Флаг / .env | Назначение |
| --- | --- | --- |
| Включить вход по токену | `--auth` / `WEB_AUTH=1` | страница входа вместо прямого доступа |
| Свой токен | `--token` / `WEB_TOKEN` | если не задан — генерируется и сохраняется в `projects/.web_secret` |

- С `--auth` значения `.env` в интерфейсе маскируются (`••••`).
- Токен для входа показывается в консоли при старте и лежит в `projects/.web_secret` (права 600).
- Для доступа только с localhost токен можно не включать.

### Решение возможных проблем

- **Порт занят** — сервер сам переходит на следующий свободный порт (`8757`, `8758`, …) и сообщает фактический адрес в консоли; браузер открывается на реальном адресе. Чтобы занять конкретный порт, укажите его в `.env` (`WEB_PORT=9000`).
- **Страница не открывается** — проверьте, что сервер реально стартовал (в консоли есть баннер с адресом), и что браузер не заблокирован файрволом (для localhost блокировок не бывает).
- **Не заходит с другого компьютера** — сервер слушает `0.0.0.0` (`WEB_HOST=0.0.0.0`), включён токен, и порт открыт в файрволе.
- **Ошибка входа по токену** — токен из консоли/`.web_secret` вводится целиком, без лишних пробелов; после смены `WEB_TOKEN` перезапустите сервер.
- **Логи сервера** — `logs/web.log` (ротация по 5 МБ).

## Тесты

```bash
./dev.sh test                                      # все тесты: pytest -n auto (параллельно, pytest-xdist)
./dev.sh test -n 0 tests/test_ner.py               # один файл в одном процессе (только для отладки)
./dev.sh spa                                       # SPA: node --check по static/*.js + node --test tests/spa/
./dev.sh probe --shot                              # UI: обход экранов headless-браузером + скриншоты
python3 -m pytest tests/ -q --cov=core --cov=cli --cov=web  # покрытие
```

- **Раннер один — pytest.** Скорость набора даёт параллельность (`-n auto`, pytest-xdist), а не второй инструмент: тестовые сервера берут свободный порт, данные — `tmp_path`, поэтому воркеры не мешают друг другу. «Быстрых/медленных» слоёв нет.
- **Правка UI без прогона не закрыта.** `./dev.sh probe --shot` проходит все view, вкладки проекта и модалки, ловит `pageerror`/`console`/4xx-5xx и пишет скриншоты и `report.json` в `logs/ui_probe/` (в git не попадают) — сравнивать «до/после» иначе нечем. Playwright стоит глобально (`playwright-core`), своей npm-папки и сборки в репо нет.

- `tests/conftest.py` — общие хелперы (SilentLog, make_ru_chapter_file, feed, fake_env);
- `tests/test_core_common.py` — `core/common.py` целиком (стрим SSE моками, .env, чанкование, NER-поиск, имена по полу, канон глав);
- `tests/test_core_stage.py` — `core/stage.py` (флаги LLM, профиль сервера, прогресс);
- `tests/test_projects_core.py` — `core/projects.py`;
- по одному файлу на скрипт: translate_book, ner, ner_check, translate_check_llm, wiki, epub_to_chapters, translate_check — чистые функции + оркестраторы и `main()` с моками LLM;
- `tests/test_cli_units.py` / `test_cli_e2e.py` — остальные `cli/` без сети (batch_replace, clean_and_compile и др.);
- `tests/test_web_*.py` — web-слой (роуты, JobManager, SSE, env-редактор, NER-экспорт) на реальном HTTP-сервере без сети;
- `tests/test_docs.py` — сверка доков (AGENTS.md §6, пути) с кодом;
- `tests/test_tools_userscripts.py` — юзерскрипты: артефакт и `.meta.js` == сборка
- `tests/test_architecture.py` — регресс-гарды архитектуры;
- `tests/test_spa_js.py` + `tests/spa/*.test.mjs` — SPA (node --check, node --test чистых функций).

Тесты НЕ ходят в сеть: LLM только мокать (monkeypatch на `stream_chat_completion`, на уровне транспорта — на `core.transport.open_stream`, бэкенд — на `core.transport.reset_client`), данные — во временных папках pytest (`tmp_path`).

## Соглашения

- **Единицы.** ТОКЕНЫ (язык-осведомлённая оценка `estimate_tokens`, ±20–30%): все размеры LLM-запросов — `--chunk_size` (translate_book/конвейер/ner/epub chunk), `--request_budget`, бюджеты пакетов ner_check и translate_check_llm, `--budget` оценки, FTS5 chunk wiki, `chunkSize` Lite (имена параметров сохранены, единица — токены). ТОКЕНЫ (предохранитель сервера): `max_tokens` и `--near-distance` (wiki, природа FTS5 NEAR). СИМВОЛЫ: длины вне запросов — `--title-limit`, `min_fix_length`, `max_changed_chars`, `context_max_len`, длины в логах; `min_len_ratio` — безразмерное отношение длин именно в символах. БАЙТЫ: размеры файлов в отчётах translate_check. ГЛАВЫ: чанкование в clean_and_compile. Единица обязана быть указана в help argparse.
- **Unicode.** NFC-нормализация везде, где сравнивается/заменяется русский текст. Кавычки «»/", тире –—-, многоточия …/... считаются разными.
- **JSON-файлы данных.** Ключи — по умолчанию на английском (`entries`, `status`, `applied`, `reason`, `stage`, `chapter`, `file`, `type`, `term`, `field`, `old`, `new`, `created`, `updated` …); значения (статусы «принять»/«отклонить», тексты ошибок, логи) — русские. Новые ключи — только английские; переименование жёсткое, без fallback-чтения старых ключей.
- **Логирование.** Проектные логи: `logs/`; по главам — `logs/chapters/`. `setup_logging` заменяет расширение на `.log`; после него — `log_argv(logger)` (фактическая команда запуска).
- **Bootstrap.** Новые скрипты находят корень репо подъёмом вверх (`_bootstrap_core()`, продублирован в каждой точке входа осознанно) и добавляют в `sys.path` перед импортом `core.*`.

## Регулярные выражения

Применяемый диалект — Python `re` (regex101 в режиме Python); все regexp-поля — чистые стандартные выражения (без кастомных флагов и комментариев, режимы — inline-флагами `(?i)`…). Где используются: разбор исходника (`--split-re`, очистки, замены `паттерн -> замена`), массовые замены (`--replace`), `--replace-re`. Подробное руководство с примерами — в Справке web-интерфейса (`web/static/help.md`, раздел «Регулярные выражения»).

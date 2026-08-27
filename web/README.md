# web — web-интерфейс

Статус: **работает** (v1.0 M1–M8 + v2 W1–W9, web-first).
Единственный интерфейс проекта: тот же конвейер перевода (`cli/` +
`core/`), управление — через браузер (cli/tui удалены).

## Запуск

```bash
python3 run.py                  # web-сервер + открыть браузер
python3 web/main.py             # 0.0.0.0:8756, без токена (LAN)
python3 web/main.py --auth      # включить токен (см. ниже)
```

По умолчанию (W1) сервер слушает `0.0.0.0` — доступен с любой машины
локальной сети (`http://<LAN-IP>:8756`), аутентификация **выключена**
(доверенная сеть). Включить: `--auth` или `WEB_AUTH=1` — тогда вход по
токену (`--token` > `.web_secret`, автогенерация, chmod 600), а значения
.env маскируются `••••`.

Параметры: `--host --port --auth --token --open --max-upload-mb
--jobs-limit --projects-dir`; источник: CLI-флаг > окружение > корневой
`.env` (`WEB_HOST WEB_PORT WEB_AUTH WEB_TOKEN WEB_MAX_UPLOAD_MB
WEB_JOBS_LIMIT WEB_PROJECTS_DIR`) > дефолт. `--projects-dir`/`WEB_PROJECTS_DIR`
— папка проектов (по умолчанию `<репо>/projects`; там же `projects/.web_secret`
и `projects/.sections.json`). `--no-auth` — устаревший no-op.

## Возможности

- **Дашборд** (W3): сводка одним запросом — все проекты со статистикой,
  последние запуски, «Продолжить» по последнему проекту.
- **Проекты (hub)**: разделы ACTIVE/HOLD/DONE (динамические:
  создание/переименование с merge/удаление пустых через «Управление
  разделами»; `projects/.sections.json` — источник истины), карточки
  проектов, создание/перенос/переименование/копирование/удаление
  (с подтверждением); мастер создания — опциональные загрузка обложки
  (jpg/png → `source/cover.*`, `PUT /api/cover`) и исходника
  (txt/md/epub/zip → `source/`, `POST /api/upload` с dest=source),
  реестр стадий `web/stages.py::STAGE_SPECS`
  (ключи-слаги), дерево глав с артефактами, шаблоны проектов.
- **Файлы**: браузер/редактор/загрузка (multipart, свой парсер)/скачивание,
  песочница `resolve_path` (запрет `..`, абсолютных путей, симлинк-побега);
  «＋ Файл»/«＋ Каталог»/«Переим.» (mkdir/rename — файлы И
  каталоги); большие файлы (>5 МБ) не открываются в редакторе (413,
  скачивание).
- **Редактор** (W8): CodeMirror 6 (вендор в `static/vendor/`, офлайн):
  вкладка «Редактор» — главы проекта (артефакты по маскам:
  канон И легаси `*_перевод/редактура/полировка`), панели Оригинал/
  Перевод/Редактура/Полировка — по умолчанию два файла: слева оригинал,
  справа полировка > редактура > перевод (по доступности); канонические
  файлы видны, даже если их ещё нет — пустой редактор, сохранение создаст
  файл (напр. polished.txt до полировки); сохранение через
  `/api/file`; подсветка терминов глоссария оверлеем (по умолчанию вкл;
  поиск по логике `translate_book.py`: `normalize_for_search` + точное
  вхождение со слово-границами + нечёткие n-граммы «корень+окончание»,
  поле «n-грамма» — аналог `--ner_ngram`, дефолт 3, и поле «Порог» —
  аналог `--ner_threshold`, дефолт 0.75, выше — строже; порог
  применяется сразу при вводе), тултип с «→ Глоссарий»; выделение в
  `chapter.txt` — «＋ в глоссарий» (форма: термин, тип, перевод;
  `context` ≤200 символов, `count=1`; дубль — предупреждение); DOM вкладки кешируется, пока
  открыт проект (возврат из «Глоссарий» без сброса; F5/другой проект —
  заново); настройки — localStorage браузера, не .env.
  подсветка по расширению файла (md/html/json/yaml/py), без ручного
  выбора представления; предпросмотр md/html — кнопка «Рендер»
  (sandbox-iframe); поиск/замена Ctrl+F/Ctrl+H; fallback на textarea.
- **NER** (W5): таблица глоссария — инлайн-редактирование строк,
  настраиваемые столбцы (все поля ner.json, настройка в localStorage),
  сортировка кликом по заголовку (первый — убывание, повтор — возрастание;
  по умолчанию `count` по убыванию, даже если столбец скрыт),
  добавление/удаление, поиск слева (поле + выбор полей), справа —
  кнопки типов и столбцов; выбор столбцов/полей/типов — единая модалка
  «Все / набор» (всё снять нельзя — остаётся первый пункт, «Все» сверху;
  подписи кнопок «Все поля/типы/столбцы» → «Поля/Типы/Столбцы (N)»);
  тултипы на всех кнопках; review-флоу:
  `ner_review.json` / `translate_check_llm_review.json`.
- **Проверка** (W7): отчёты translate_check (`logs/check_*.txt`) — таблица
  «глава → папка → ошибки» с переходом в «Файлы», FATAL подсвечен;
  LLM-карточки (`ner_review.json` / `translate_check_llm_review.json`) —
  переключатель «Список правок / Редактор JSON»: список отдельных правок
  со сводкой (всего/принято/отклонено/применено), кнопки «Принять»,
  «Отклонить», «Откорректировать» (Было/Стало/Причина — JSON: old/new/reason)
  и переход к тексту
  правки («→ Глава» — файл главы с поиском фрагмента, «→ Глоссарий» —
  таб с поиском термина); JSON-редактор — прежний режим. Применение
  запускается как фоновая задача и уведомляет о завершении toast-ом
  (✅/❌/⏹); чекбокс «не создавать .bak» (по умолчанию бэкапы создаются).
- **Конфиг** (W6, раунд 8): системный `.env` — корневой `.env` репо
  (`scope=global`; проектный `pdir/.env` — изоляция проекта, приоритетнее
  системного; значения видимы без auth, маскируются при `--auth`);
  настройки внешнего вида (`WEB_UI_THEME`/`WEB_EDITOR_THEME`/
  `WEB_EDITOR_FONT_SIZE`) — `GET /api/settings`;
  metadata.yaml; обложка (upload/предпросмотр/удаление, `source/cover.*`).
- **Промпты** (W4): файлы `prompts/` + шаблоны `templates/*/prompts`
  в одном списке; создание промпта из шаблона.
- **Запуски**: все стадии пайплайна, **«Простой режим / Экспертный»**
  (сегмент-переключатель над формой). Простой режим — «частично
  показанный экспертный»: карточка пресета (название, «что будет
  сделано») + диапазон глав + простые поля (`spec.simple`: исходник/язык
  у epub, режим/промпт/двухпроходная у ner и т.д.); params = дефолты
  спеки `preset.params` + изменённые поля. Значения формы — общие для
  обоих режимов (`st.values`): правки в экспертном применяются в
  простом и наоборот. LLM-поля (host/model/api_key) в простом не
  показываются — сервер из `.env` (если заполнены в экспертном —
  уйдут в запуск). Стадии translate_check/batch_replace/compile —
  только экспертные (без переключателя). Выбор режима — localStorage
  (`runMode`, по стадиям), дефолт — простой.
  live-лог + SSE (reconnect с backoff при обрыве), прогрессбар
  LLM-стадий (события
  `@@PROGRESS@@` из скриптов, `WEB_PROGRESS=1` ставит JobManager.start в
  env subprocess, tqdm в web отключается; без событий бар скрыт),
  конвейер с таблицей глав (✓/✗/⊘, строки — реальные главы из
  `options.chapters.ids`), stop — сигнал **группе процессов**
  (дочерние translate_book.py не осиротеют).
  Страница «Запуски» истории НЕ показывает: слева — карточка
  **активного** запуска проекта (badge/бар/переход/стоп), лог
  (текстовое окно + SSE) — только у активного запуска; завершённый
  запуск по URL `…/{jobId}` лог не открывает (история логов — во
  вкладке «Логи» проекта). Метаданные всех запусков — `jobs.json`,
  хвост лога/событий — `web/job_logs/{id}.log` (переживает рестарт
  сервера).
- **Дашборд**: «Последние запуски» — до 20 записей
  (`MAX_HISTORY=20`), колонки Задача/Проект/Статус/Прогресс/Дата; клик
  по строке ведёт на Запуски проекта (без job id); кнопка «Очистить»
  (DELETE `/api/jobs`) удаляет завершённые запуски, активные остаются.
  Активные запуски собираются из ПОЛНОГО списка jobs, а не из среза
  «последних 20».
- **Промпты конвейера**: форма pipeline — режим
  `auto`/`separate`/`combined` + выбор файлов из `prompts/`.
  auto: кандидат с тегами `<translate>/<redact>/<polish>`
  (`pipeline_prompt.txt` → `prompts.txt` → `translate_book_prompt.txt`),
  иначе дефолтные имена по стадиям (`translate/redact/polish_prompt.txt`);
  separate — по одному файлу на стадию; combined — один файл с тегами.
  translate_book.py сам различает формат (файл без тегов = промпт стадии).
  Автоподхват — только реально существующие файлы: удалённый промпт не
  остаётся выбранным (ни из .env-памяти, ни в форме); «Общий промпт-файл»
  предзаполняется победителем auto-режима (`options.auto_prompt`).
  Кэш опций инвалидируется по max mtime файлов папки — перезапись
  промпта тоже обновляет списки.
- **Настройки запусков** (R9): поля форм предзаполняются из `.env`
  (`GET /api/stages/{key}/spec?project=…`), при каждом запуске
  сохраняются в `.env` проекта — копия системного корневого `.env`
  создаётся в папке проекта при первом запуске, затем обновляются ключи
  `<STAGE>_<FIELD>` (напр. `NER_CHUNK_SIZE`, `TRANSLATE_CHECK_EXCLUDE_WORDS`;
  сервер — `<STAGE>_HOST` → `HOST`, ключ — `<STAGE>_API_KEY` → `API_KEY`,
  модель — `<STAGE>_MODEL` → общая `MODEL`). Ключ сохраняется как
  `<STAGE>_API_KEY` (fallback — `API_KEY`); профили убраны.
- **Заметки** (раунд 12): `GET/PUT /api/notes` — `projects/notes.md`
  (markdown-редактор на вкладке «Заметки», рендер в sandbox-iframe).
- **Логи**: список `logs/` и `logs/chapters/`, хвост, follow.

## Стадии (порядок в «Запусках»)

Порядок карточек стадий задаёт `web/stages.py::STAGE_ORDER` (логика
конвейера: разбор → NER → проверка глоссария → конвейер → проверка →
правки → замены → компиляция → вики). Слаги — контракт API, не менять.
Имя скрипта — подпись карточки в UI и колонка `script` в `/api/stages`.

| Слаг | Название в UI | Скрипт | LLM |
| --- | --- | --- | --- |
| `epub` | Разбор исходника на главы | `cli/epub_to_chapters.py` | нет |
| `ner` | Создание глоссария (LLM) | `cli/ner.py` | да |

Стадия `ner` — 4 режима формы: `extract` (с нуля, новый глоссарий),
`finetune` (дообучение на существующий ner.json), `compile` (склейка
глав `chapters/*/chapter.txt` в память без временного файла +
извлечение; поля `start`/`end` — диапазон глав, ГЛАВЫ), `postprocess`
(обработка ner.json без LLM: `strip_meta`/`min_count`; LLM-поля формы
скрываются).
| `ner_check` | Проверка глоссария (LLM) | `cli/ner_check.py` | да |
| `pipeline` | Перевод (LLM) | `web/pipeline.py` | да |
| `translate_check` | Проверка перевода | `cli/translate_check.py` | нет |
| `translate_check_llm` | Проверка перевода (LLM) | `cli/translate_check_llm.py` | да |
| `batch_replace` | Массовые замены | `cli/batch_replace.py` | нет |
| `compile` | Компиляция TXT/EPUB/FB2 | `cli/clean_and_compile.py` | нет |
| `wiki` | Создание Wiki (LLM) | `cli/wiki.py` | да |

Стадия `wiki` — источник текста: «Готовый txt» (как раньше) или
«Собрать из глав» (`source=chapters` → `--compile-chapters`, склейка
`chapters/*` в память, `type` — chapter/translated/redacted/polished,
`start`/`end` — диапазон, ГЛАВЫ). Формат вывода: `md` (обычный
Markdown), `rulate-md`, `rulate-html` (заголовки — `span font-size`,
`<ul>`, `<hr />`, выход `wiki.txt` — HTML-разметка внутри txt). Оглавление (`toc`) и якоря-ссылки
(`toc_links`) — только в обычном режиме; у rulate-режимов содержания
нет. «Сохранить как главу вики» (`as_chapter` → `--as-chapter`):
вместо файла — дополнительная последняя глава
`chapters/00000_{N+1}_Wiki_Новеллы/` с ОДНИМ файлом выбранного типа
(`save_type` → `--save-type`: translated/redacted/polished, по
умолчанию polished; `chapter.txt` не пишется), название «Wiki Новеллы»
простым текстом, статьи в rulate-стиле (заголовки глубже). Компилятор: глава без «Глава N» берёт заголовок из
первой непустой строки файла — вики-глава попадает в TOC epub/fb2 как
«Wiki Новеллы», внутренние `###`/`####` остаются текстом в теле и
пунктов содержания не создают. Режим `titles` компилятора выпилен (вкладка «Главы» правит
названия прямо в файлах).

## Архитектура (контракт)

- Общая логика — только из `core/` (`core.common`, `core.projects`);
  web замкнут на core + собственные модули: `server.py`, `auth.py`,
  `sandbox.py`, `api.py`, `jobs.py`, `stages.py`, `pipeline.py`,
  `multipart.py`, `state.py`.
- LLM-запросы — только через `core.common.stream_chat_completion`; web
  запускает стадии как subprocess (JobManager: Popen c
  `start_new_session` + reader-поток + SSE; killpg по группе).
  Конвейер — `web/pipeline.py` (CLI-исполнитель с `@@CHAPTER@@`-событиями
  для таблицы глав).
- Секреты: при `--auth` значения .env в браузер не отдаются (маска
  `••••`); без auth (доверенная LAN) — видны. api_key не попадает ни в
  payload, ни в jobs.json (только argv subprocess).
- Прогресс: `payload().progress` и `_serialize` (jobs.json)
  несут последнее событие прогресса `{type,label,done,total}`; SSE-тип
  `progress` (начальный бурст + живые события); дашборд рисует
  мини-бары (активные запуски + колонка «Прогресс»).
- SPA: vanilla JS + Alpine.js/CodeMirror/marked (vendored в
  `static/vendor/`, без CDN в рантайме, без сборки).

## API (кратко)

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET/POST | `/api/session`, `/api/login`, `/api/logout` | сессия, вход по токену |
| GET | `/api/dashboard`, `/api/state`, `/api/sections`, `/api/projects` | дашборд (`running_jobs` — все активные; `recent_jobs` — до 20), hub |
| POST | `/api/projects` (создание) | + move/rename/copy/delete |
| GET | `/api/projects/{s}/{n}/tree`, `/api/stats` | главы+артефакты, статистика (раунд 23: артефакты включают легаси `*_перевод/редактура/полировка`) |
| GET | `/api/projects/{s}/{n}/status` | таблица готовности глав (раунд 21): по-главные флаги translate/redact/polish + ner/wiki/compiled; кеш по сигнатуре mtime |
| GET/PUT | `/api/projects/{s}/{n}/chapters/titles` | названия глав (вкладка «Главы»): GET `?type=polished\|redacted\|translated\|chapter` → `{titles: {номер: первая непустая строка}}`; PUT `{type, titles: {номер: строка}}` — замена первой строки в файлах глав (NFC), → `{updated, missing, warnings}` |
| GET | `/api/templates` | наборы шаблонов с деревом файлов (для создания проекта и вкладки «Шаблоны») |
| POST/DELETE | `/api/templates` (создание), `/api/templates/{s}/copy`, DELETE `/api/templates/{s}` | CRUD наборов; `General` — системный: создание/удаление/запись → 400/403 |
| GET/PUT/DELETE | `/api/templates/{s}/file` (`?path=…`, PUT — `{path, content}`) | чтение/запись/удаление ФАЙЛОВ набора; каталог → 403 (неизменяемы); запись в несуществующий каталог → 400 (неявный mkdir запрещён); эскейпы за пределы набора → 404; GET отдаёт `size`/`mtime` для мета редактора |
| POST | `/api/templates/{s}/rename` (`{src, dst}`) | переименование/перенос ФАЙЛА внутри набора; каталог → 403 (неизменяемы); General → 403, нет исходника → 404, занято → 400, родительский каталог dst отсутствует → 400 |
| POST | `/api/templates/{s}/upload` (multipart `files[]`, опц. `dest`) | загрузка файлов в набор; General → 403; `dest` обязан существовать (неявный mkdir запрещён) → 400 |
| GET | `/api/templates/{s}/download?path=…` | скачивание файла набора (attachment); работает и для General |
| POST | `/api/templates/{s}/mkdir` (`{path}`) | ВСЕГДА 403 — каталоги в шаблонах неизменяемы (скелет `prompts/`+`source/` ремонтируется при чтении) |
| POST/DELETE | `/api/sections`, `/api/sections/rename` (`{src,dst}`), DELETE `/api/sections/{name}` | управление разделами: создание, переименование (merge — перенос проектов), удаление; непустой раздел → 409 |
| GET/PUT | `/api/ner`, `/api/metadata` | глоссарий, metadata.yaml |
| GET | `/api/ner/export?project=&format=json\|text\|names` | экспорт глоссария для анализа (JSON / записи текстом / имена по полу); фильтры: `count_threshold`, `types`, `exclude_words`, `range`, `show_aliases`, `show_votes`, `female_types`, `male_types` → `{name, content}` |
| GET/PUT/DELETE | `/api/cover` | обложка source/cover.* |
| GET/PUT | `/api/{ner\|translate_check_llm}/review`, POST `.../apply` | review-флоу |
| GET | `/api/check` | отчёты translate_check (W7) |
| GET/PUT/DELETE | `/api/env?scope=project\|global` | .env (global — корневой `.env` репо; project — только собственный `pdir/.env`) |
| GET | `/api/env/template` | шаблон `templates/.env.example` |
| GET | `/api/settings` | внешний вид интерфейса из системного .env: `ui_theme` (WEB_UI_THEME), `editor_theme` (WEB_EDITOR_THEME), `editor_font_size` (WEB_EDITOR_FONT_SIZE); невалидные → дефолты (dark/auto/13) |
| GET/PUT/DELETE | `/api/prompts`, `/api/prompts/{name}` | промпты (DELETE — удаление, PUT с пустым content — создание) |
| GET | `/api/prompts/{name}/template` | шаблоны промптов |
| GET | `/api/logs`, `/api/logs/{name}` | логи проекта |
| GET/PUT/DELETE | `/api/files`, `/api/file`, `/api/upload`, `/api/download` | файлы (`download?inline=1` — предпросмотр); чтение отсутствующего файла — `missing: true` + пустой `content` (редактор создаёт файл при сохранении); `upload` с пустым `dest` — корень проекта (поля files с `dir=""`) |
| POST | `/api/mkdir` (`?project=&path=` или body) | создание пустого каталога проекта; дубль/эскейп → 400 |
| POST | `/api/file/rename` (`{project, path, new_name}`) | переименование файла ИЛИ каталога проекта; нет исходника → 404, занято → 400, недопустимое имя → 400 |
| GET | `/api/stages`, `/api/stages/{k}/spec\|options` | стадии, формы; `spec.preset.params` — параметры «Простого режима» (дефолты полей + overrides); `options.chapters.ids` — реальные главы |
| POST/GET | `/api/jobs`, `/api/jobs/{id}`, `/api/jobs/{id}/stream` | запуски, SSE; при лимите параллельных (`WEB_JOBS_LIMIT`) — **429** (H2); вторая стадия на тот же проект — **409** (M10). SSE: стартовый бурст хвоста (до 5000 строк) + события/прогресс + финальный `status`; клиент берёт лог ТОЛЬКО из стрима (payload `lines` на running не дублирует) |
| DELETE | `/api/jobs` | очистить историю завершённых запусков (активные не трогаются) |
| POST | `/api/jobs/{id}/stop`, DELETE `/api/jobs/{id}` | стоп (killpg)/удаление |

## Тесты

`tests/test_web_*.py`: сервер/сессия/песочница, файлы, jobs (включая
killpg-группу), конвейер, NER/env/промпты/логи/check/cover — зелёные,
без сети (LLM мокается/не нужен).

# TODO — план (имена экспортов + «Файлы»: Файл/Каталог/Переим. + шаблоны-скелет + вкладка «Редактор» + .env-аудит)

Статус: **реализация завершена (M1–M5), тесты зелёные, коммит — последний шаг**.

Идеология: планирование → функциональность → поддерживаемость → надёжность → тесты.
Интерфейс и логи — на русском. Единицы и имена артефактов стадий — не трогать.

---

## Исследование (диагностика по пунктам запроса)

### 1. Имена fb2/epub при экспорте — имя проекта + диапазон глав

Сейчас имена генерирует `scripts/clean_and_compile.py`:

| Строка | Сейчас | Что нужно |
| --- | --- | --- |
| 653 | `compiled_{start}_{end}_{mode}.txt` | **не трогаем** (промежуточный TXT; на него завязан автоподхват `compiled_chapters.txt` в `web/api.py:1872-1880`) |
| 770 | `book_{start}_{end}.epub` | `{ИмяПроекта}_{start}_{end}.epub` |
| 776 | `book_{start}_{end}.fb2` | `{ИмяПроекта}_{start}_{end}.fb2` |

- Запуски web идут subprocess'ом с `cwd = папка проекта` (`web/jobs.py:6,394`) → имя проекта = `os.path.basename(os.getcwd())`.
- Имена проекта валидируются `valid_project_name` (буквы/цифры/`._-` и пробел, ≤120 символов) → для имени файла нужна санитизация: NFC, пробелы → `_`, убрать ведущие/хвостовые `._`, fallback `"book"`.
- Режимы `*-chunks` зовут `compile_book` в цикле с меняющимися `cfg.start/end` → каждая часть получает свой диапазон автоматически.
- Внутренние метаданные EPUB/FB2 (`title` из `source/metadata.yaml`) от имени файла не зависят — не трогаем.
- Потребители старого паттерна `book_*` (нужно расширить, legacy сохранить):
  - `core/projects.py:588` `_has_compiled` — паттерны `("compiled_*.txt", "book_*.epub", "book_*.fb2")` → добавить `f"{pdir.name}_*.epub"`, `f"{pdir.name}_*.fb2"`;
  - `core/projects.py:692` `project_progress_table` — фильтр `startswith("compiled_") or startswith("book_")` → аналогично (компилед-список вкладки «Статус»);
- Тесты, завязанные на `book_*`: `tests/test_scripts_e2e.py` (строки 174, 198, 217, 236, 248, 264, 314: `book_1_2.epub/.fb2`), `tests/test_projects_core.py:158` (легаси-детект — останется зелёным). E2E-тесты сидят на `monkeypatch.chdir(tmp_path)` → перевести на chdir в именованную подпапку (напр. `tmp_path/"Тестовая_Книга"`) и ожидать `<имя>_1_2.epub`.
- Доки: `README.md:371-372` — пример дерева с `book_1_50.epub/.fb2` → обновить на новый формат.

### 2. «Файлы» проекта — кнопки «＋ Файл» / «＋ Каталог» и «Переим.»

Текущее состояние (`web/static/project-views.js::filesView`, строки 107–283):

- Тулубар: только «Загрузить» (+ drag&drop). «＋ Файл» / «＋ Каталог» отсутствуют.
- `fileRow` (стр. 232): у файлов — «Скачать», «Правка», «Удалить»; у каталогов — только переход (никаких кнопок). «Переим.» нет.
- API: `GET/PUT/DELETE /api/file`, `POST /api/upload`, `GET /api/download` (`web/api.py:342-348`). **Роутов mkdir и rename для проектов нет** (есть только для шаблонов: `_templates_mkdir`, `_templates_rename` — `web/api.py:2031-2138`).

Что нужно:

- **API** (по образцу шаблонных, но в `web/api.py` рядом с файловыми хендлерами — файловые операции проекта там же, см. `_file_read/_file_write/_file_delete`; в `core/projects.py` НЕ дублируем, контракт файловых роутов — в `web/README.md`):
  - `POST /api/mkdir?project=&path=` — создать каталог (`resolve_path` + `mkdir(parents=True)`); занято → 400, эскейп → 400;
  - `POST /api/file/rename` `{project, path, new_name}` — переименовать файл ИЛИ каталог внутри той же папки (`src.replace(dst)`, dst через `resolve_path`, занято → 400, нет исходника → 404).
- **SPA** (`filesView`):
  - тулубар: «Загрузить» → «＋ Файл» → «＋ Каталог» (`nameModal`, паттерн как в шаблонах, `app.js:2058-2110`); «＋ Файл» = `PUT /api/file {path, content: ""}` + `openEditor(full)`; «＋ Каталог» = `POST /api/mkdir`;
  - `fileRow`: у файлов — «Скачать» «Правка» **«Переим.»** «Удалить»; у каталогов — **«Переим.»** «Удалить» (каталоги уже удаляются: `DELETE /api/file` делает rmtree). Переим. — `nameModal` → `POST /api/file/rename`.

### 3. Шаблоны — скелет каталогов как в General, действия с каталогами запрещены

Текущее состояние:

- `core/projects.py:365 create_template_set` — **уже** создаёт `prompts/` + `source/` ✅
- `copy_template_set` (стр. 384) — `shutil.copytree` целиком; если исходный набор «деградировал» (потерял каталог), копия тоже будет без скелета ⚠️
- Каталоги сейчас **изменяемы**: `move_template_file` (стр. 541) и `delete_template_file` (стр. 496) работают и с каталогами; `create_template_dir` (стр. 416) создаёт произвольные каталоги; в UI `viewTemplates` (`app.js:1897-2010`) каталоги имеют «Переим.» и «Удалить», в тулубаре есть «＋ Каталог» (стр. 2081) ⚠️
- `templates_files` (стр. 439) уже отдаёт пустые каталоги (`path/`) → скелет всегда виден, пока существует.

Что нужно (решение пользователя: **в шаблонах всегда каталоги как в General — `prompts/` + `source/`; с каталогами любые действия запрещены**):

- Инвариант скелета: приватный `_ensure_template_skeleton(set_dir)` (mkdir `prompts/`, `source/`, exist_ok) в `core/projects.py`; вызывать в `create_template_set` (уже), в `copy_template_set` (после copytree — самовосстановление деградировавших исходников) и **ремонт при чтении**: в `_templates` (`web/api.py:798`) для каждого набора (идемпотентно, дёшево) — чтобы гарантировать инвариант и для наборов, созданных до введения скелета.
- Запрет действий с каталогами в core:
  - `move_template_file`: `sp.is_dir()` → ошибка «Каталоги в шаблонах неизменяемы»;
  - `delete_template_file`: `p.is_dir()` → та же ошибка (файлы удаляются как раньше);
  - `create_template_dir`: возвращать «Каталоги в шаблонах неизменяемы» (роут остаётся, но всегда 403; UI кнопку убираем — меньше диффа, чем удаление функции из публичного API и таблицы §6).
- SPA (`viewTemplates`): у каталогов **никаких кнопок** (только переход); убрать «＋ Каталог» из тулубара; «＋ Файл»/«Загрузить»/«Скачать»/«Правка»/«Переим.»/«Удалить» для файлов — как есть.

### 4. Вкладка «Редактор» между «Проверка» и «Статус»

Текущее состояние:

- Табы проекта — `web/static/project-views.js::TABS` (стр. 32): files, ner, review («Проверка»), status («Статус»), config, prompts, logs, notes → вставляем `["editor", "Редактор"]` после review.
- Данные уже доступны, **новые API-роуты НЕ нужны**:
  - `GET /api/projects/{sec}/{name}/tree` (`web/api.py:768`) — главы: `{id, dir, artifacts:{chapter.txt|translated.txt|redacted.txt|polished.txt: size}}` → выбор главы + какие типы доступны;
  - `GET/PUT /api/file` — чтение/сохранение `chapters/<папка>/<файл>` (лимит 5 МБ, бинарные → 400);
  - `GET /api/ner` — глоссарий (`items[]` с полями `term`, `translation`, `type`, `notes`; >10 МБ → `too_large`).
- Редактор — `makeEditor` (`app.js:1364`, CodeMirror 6 через `window.CM`). **Важная находка**: бандл `web/static/vendor/codemirror.min.js` экспортирует только `{EditorView, basicSetup, EditorState, Compartment, keymap, langs}` — **`Decoration`/`StateField`/`ViewPlugin`/`hoverTooltip` наружу НЕ отдаются** (проверено по коду бандла). Значит подсветка «в редакторе» делается оверлеем:
  - слой `.ed-hl-layer` (position:absolute, **pointer-events:none** — не мешает выделению текста) поверх CM;
  - позиции марок через `view.coordsAtPos(from)/coordsAtPos(to)` минус `contentDOM.getBoundingClientRect()`; перерисовка — на изменения текста (`contentDOM` input, дебаунс ~150 мс), ресайз; скролл перерисовки не требует, если слой вложен в прокручиваемый контейнер (координаты считаем относительно него);
  - hover-тултип — без pointer-events на марках: `mousemove` на `view.scrollDOM` → `view.posAtCoords({x,y})` → бинарный поиск совпадения → показать тултип (в бандле есть `posAtCoords`, `coordsAtPos`, `visibleRanges`, `contentDOM`, `scrollDOM` — проверено);
  - видимость: отсекать по `view.visibleRanges` (в бандле есть) — на главу (10–50 КБ) и пару тысяч терминов это дёшево.

План вкладки:

- **Выбор главы**: селект из `/tree` (показываем `dir`, напр. `00000_1_第1章`); главы без файлов — недоступны.
- **Режимы**: переключатель «Один файл» / «Два файла (слева-справа)».
- **Типы файлов** (только существующие в папке главы, по `artifacts`):
  - `chapter.txt` → «Оригинал», `translated.txt` → «Перевод», `redacted.txt` → «Редактура», `polished.txt` → «Полировка».
  - В режиме «два файла» — по своему селекту на каждую панель (напр. слева Оригинал, справа Перевод); панели независимы.
- **Сохранение**: кнопка «Сохранить» на панель → `PUT /api/file {path: chapters/<dir>/<file>, content}`.
- **Подсветка глоссария** (кнопка-тумблер, по умолчанию выключена):
  - загрузка `/api/ner` один раз (кеш в состоянии вкладки);
  - поиск **по обоим полям: `term` И `translation`** (в `chapter.txt` — китайский термин, в переводах — русская запись);
  - тултип при наведении: **Термин / Перевод / Примечание** (+ тип), ссылка «→ Глоссарий» (переход на таб ner с предзаполненным поиском: `st.view="ner"`, `st.search=term`, после рендера проставить значение в `search`-инпут — паттерн уже есть для CM-поиска в `render()`, стр. 85–97);
  - NFC: термины нормализуем при построении матчера; текст документа ищем как есть (смещения не трогаем).
- **Чистые функции в `ui-core.js`** (node-тестируемые, `tests/spa/ui-core.test.mjs`):
  - `buildGlossaryMatcher(items)` — список `{term, translation, notes, type}` → чанки по ~2000 терминов, escape, сортировка по длине убыв. (длинные раньше), NFC;
  - `glossaryMatches(text, matcher)` → `[{from, to, item}]`.
- **CSS** (`styles.css`): `.ed-toolbar`, `.ed-grid` (2 колонки, → 1 колонка на узких экранах), `.ed-pane` (relative), `.ed-hl-layer`, `.hl-mark` (жёлтая подложка), `.hl-tip` (тултип).

### 5. Шаблон .env — обновлять?

Провёл аудит всех ключей, которые читает код, против `templates/.env.example`:

| Источник | Ключи | В шаблоне |
| --- | --- | --- |
| `web/main.py` (host/port/auth/token/max-upload/jobs-limit) | `WEB_HOST/WEB_PORT/WEB_AUTH/WEB_TOKEN/WEB_MAX_UPLOAD_MB/WEB_JOBS_LIMIT` | ✅ |
| `core/common.py::get_server_config/get_stage_model` | `HOST/API_KEY/MODEL`, `<STAGE>_MODEL` (TRANSLATE/REDACT/POLISH/NER/NER_CHECK/TRANSLATE_CHECK_LLM/WIKI) | ✅ |
| дефолты скриптов (stages/пайплайн) | `CHUNK_SIZE/NER_CHUNK_SIZE/NER_THRESHOLD/MIN_LEN_RATIO_*/TIMEOUT/STREAM_TIMEOUT/MAX_RETRIES/PIPELINE_*` | ✅ |
| `translate_check.py` | `TRANSLATE_CHECK_EXCLUDE_WORDS` | ✅ |
| внутренние | `WEB_PROGRESS`, `LLM_API_KEY` (задаёт JobManager, не пользователь) | не нужны |

**Вывод: правки не нужны** — все ключи уже описаны. Новым возможностям (вкладка «Редактор») серверный конфиг не требуется: настройки подсветки/режима — UI-предпочтения, хранятся в localStorage браузера (AGENTS.md §7). Единственное действие — зафиксировать вывод в TODO и при необходимости добавить в `templates/.env.example` комментарий-напоминание о localStorage (не обязательно).

---

## План реализации

### M1 — Имена экспортов fb2/epub = имя проекта + диапазон

- [x] `scripts/clean_and_compile.py`:
  - [ ] новая функция `_export_label()`: NFC + санитизация basename cwd (пробелы → `_`, срезать `._` по краям, fallback `"book"`);
  - [ ] стр. 770: `book_{start}_{end}.epub` → `{label}_{start}_{end}.epub`; стр. 776: то же для `.fb2`; `compiled_*`/`titles_*` — не трогаем.
- [x] `core/projects.py:588` `_has_compiled` и стр. ~692 `project_progress_table`: паттерны `book_*.epub/.fb2` + `{pdir.name}_*.epub/.fb2` (legacy остаётся).
- [x] Тесты: `tests/test_scripts_e2e.py` — epub/fb2-тесты перевести на chdir в именованную подпапку и ожидать `<имя>_1_2.epub`/`.fb2`; `tests/test_projects_core.py` — детект скомпилированных по новому имени.
- [x] Доки: `README.md:371-372` — новый формат имён.

### M2 — «Файлы»: «＋ Файл» / «＋ Каталог» / «Переим.»

- [x] `web/api.py`:
  - [ ] `_file_mkdir` → `POST /api/mkdir?project=&path=` (resolve_path + mkdir; занято/эскейп → 400);
  - [ ] `_file_rename` → `POST /api/file/rename` `{project, path, new_name}` (файл И каталог; dst = src.parent/new_name через resolve_path; занято → 400, нет исходника → 404);
  - [ ] регистрация в `_register_files` (стр. 342).
- [x] `web/static/project-views.js::filesView`: тулубар «Загрузить» → «＋ Файл» → «＋ Каталог»; `fileRow`: «Переим.» между «Правка» и «Удалить» (у файлов и у каталогов).
- [x] Тесты: `tests/test_web_api.py` — mkdir (создание, дубль, эскейп), rename файла и каталога (успех, занято, эскейп, 404).
- [x] `web/README.md` — строки роутов в таблицу API.

### M3 — Шаблоны: скелет как в General, каталоги неизменяемы

- [x] `core/projects.py`:
  - [ ] приватный `_ensure_template_skeleton(set_dir)` (mkdir `prompts/`+`source/`); вызов в `create_template_set` (уже), `copy_template_set` (после copytree);
  - [ ] `move_template_file`: каталог → «Каталоги в шаблонах неизменяемы»; `delete_template_file`: каталог → та же ошибка; `create_template_dir`: всегда та же ошибка;
- [x] `web/api.py::_templates` (стр. 798): ремонт скелета при чтении (идемпотентно для каждого набора).
- [x] `web/static/app.js::viewTemplates`: у каталогов убрать «Переим.»/«Удалить» (только переход); убрать «＋ Каталог» из тулубара.
- [x] Тесты: `tests/test_projects_core.py` — скелет при создании/копировании (в т.ч. из «деградированного» исходника), отказ move/delete/create для каталогов; `tests/test_web_api.py` — mkdir → 403, rename/delete каталога → 403/400.
- [x] `core/README.md` (при необходимости) — контракт функций шаблонов.

### M4 — Вкладка «Редактор»

- [x] `web/static/ui-core.js`: `buildGlossaryMatcher(items)` + `glossaryMatches(text, matcher)` (чанки, escape, длинные раньше, NFC, поля `term`+`translation`).
- [x] `web/static/project-views.js`:
  - [ ] `TABS`: `["editor", "Редактор"]` между review и status;
  - [ ] `editorView()`: селект главы (из `/tree`), переключатель «Один файл»/«Два файла», селекты типов по существующим артефактам (Оригинал/Перевод/Редактура/Полировка), панели `makeEditor("txt")`, загрузка `GET /api/file` (`chapters/<dir>/<file>`), «Сохранить» → `PUT /api/file`;
  - [ ] подсветка: тумблер «Подсветка глоссария», оверлей-марки через `coordsAtPos` (pointer-events:none), тултип через `posAtCoords`+mousemove (Термин/Перевод/Примечание/тип), кнопка «→ Глоссарий» (`st.view="ner"`, `st.search=term`, проставление поиска после рендера);
  - [ ] состояние вкладки (`st.editor = {chapter, mode, left, right, hl}`) — сохраняется между переключениями табов.
- [x] `web/static/styles.css`: `.ed-*`, `.ed-grid` (2 колонки → 1 на узких), `.hl-mark`, `.hl-tip`.
- [x] Тесты: `tests/spa/ui-core.test.mjs` — matcher (escape, длинные раньше, оба поля, NFC); node --check по JS.
- [x] `web/README.md` — упоминание вкладки «Редактор» в возможностях.

### M5 — .env-шаблон

- [x] Аудит (см. исследование п.5): правки не требуются; вывод зафиксирован в этом TODO.
- [x] (опционально) комментарий в `templates/.env.example` о том, что UI-предпочтения — localStorage, не .env.

---

## Карта правок

| Файл | Изменения |
| --- | --- |
| `scripts/clean_and_compile.py` | `_export_label()`; имена epub/fb2: `{label}_{start}_{end}.{ext}` |
| `core/projects.py` | `_has_compiled`/`project_progress_table`: паттерны `{pdir.name}_*.epub/.fb2`; `_ensure_template_skeleton`; запрет действий с каталогами в move/delete/create_template_dir |
| `web/api.py` | `POST /api/mkdir`, `POST /api/file/rename` (проекты); ремонт скелета шаблонов в `_templates` |
| `web/static/project-views.js` | таб «Редактор» (editorView + подсветка), кнопки «＋ Файл»/«＋ Каталог»/«Переим.» в filesView |
| `web/static/app.js` | viewTemplates: каталоги без действий, убрать «＋ Каталог» |
| `web/static/ui-core.js` | `buildGlossaryMatcher`/`glossaryMatches` |
| `web/static/styles.css` | `.ed-*`, `.hl-mark`, `.hl-tip`, grid |
| `README.md` | формат имён экспортов (стр. 371-372) |
| `web/README.md` | роуты `/api/mkdir`, `/api/file/rename`; вкладка «Редактор»; каталоги шаблонов неизменяемы |
| `core/README.md` | (при необходимости) контракт функций шаблонов |
| `tests/…` | per-майлстоун: test_scripts_e2e, test_projects_core, test_web_api, spa/ui-core.test.mjs |
| `templates/.env.example` | (опционально) комментарий о localStorage |
| `TODO.md` | этот файл |

---

## Проверки (обязательные перед коммитом)

- [x] `python3 -m pytest tests/ -q` — все зелёные (коммит с падающими тестами запрещён).
- [x] `node --test tests/spa/` + `node --check` по web/static/*.js.
- [x] Smoke: `--help` clean_and_compile.py; HTTP-сервер — mkdir/rename файлов проекта, экспорт epub/fb2 с именем проекта; шаблоны — скелет нового/скопированного набора, отказ действий с каталогами; вкладка «Редактор» (глава → типы → загрузка/сохранение → подсветка/тултип → переход в глоссарий).
- [x] Коммит + push (`git add -A` → commit → `git push origin`).

---

## Ручная приёмка (через web)

1. Запуск «Компиляция» (epub/fb2): в корне проекта и логе запуска — `<ИмяПроекта>_<start>_<end>.epub/.fb2`; в «Статусе» — файл виден в списке compiled.
2. Проект → «Файлы»: «＋ Файл» создаёт и открывает пустой файл; «＋ Каталог» создаёт папку; «Переим.» работает для файлов и каталогов (кнопка между «Правка» и «Удалить»).
3. «Шаблоны»: у любого набора (нового, скопированного, старого) видны `prompts/` и `source/`; у каталогов нет ни одной кнопки; «＋ Каталог» отсутствует; файлы — как раньше.
4. Проект → «Редактор» (между «Проверка» и «Статус»): выбор главы; «Один файл»/«Два файла»; доступны только существующие типы; сохранение пишет файл; «Подсветка глоссария» подсвечивает термины и переводы, тултип показывает Термин/Перевод/Примечание, клик ведёт в «Глоссарий» с поиском.
5. `templates/.env.example` — расхождений с кодом нет (аудит п.5).

---

## Решения (утверждены пользователем)

- **Экспорты**: имя = санитизированное имя папки проекта + `_<start>_<end>`, только epub/fb2; `compiled_*`/`titles_*` и внутренние метаданные не меняем; legacy `book_*` по-прежнему распознаётся в «Статусе».
- **Файлы**: переименование и для файлов, и для каталогов; подтверждение словом не нужно (`nameModal`); «＋ Файл» открывает редактор сразу.
- **Шаблоны**: скелет `prompts/`+`source/` гарантирован всегда (создание, копирование, ремонт при чтении); каталоги полностью неизменяемы (создание/переименование/удаление → ошибка); «＋ Каталог» из UI убран.
- **Редактор**: без новых API (tree + /api/file + /api/ner); подсветка — оверлей поверх CodeMirror (в бандле нет Decoration/StateField), тултип через posAtCoords; ссылка на глоссарий с предзаполненным поиском.
- **.env-шаблон**: правки не требуются (все ключи покрыты; UI-предпочтения — localStorage).

---

## Вне скоупа (осознанно не делаем)

- Переименование `compiled_*`/`titles_*` TXT и внутренних метаданных EPUB/FB2 (только имена файлов).
- Drag&drop «＋ Каталог»/«＋ Файл» в шаблонах (действия с каталогами запрещены; файлы — через «＋ Файл»/«Загрузить»).
- Подсветка в режиме «Рендер» как основной механизм (оверлей в редакторе — первичный; рендер-режим оставлен как fallback, если оверлей окажется нестабильным).
- Редактирование `translated_trace.json` во вкладке «Редактор» (типы — только 4 артефакта текста: chapter/translated/redacted/polished).
- Синхронный скролл двух панелей и diff-подсветка расхождений (сравнение — визуальное, слева-справа).

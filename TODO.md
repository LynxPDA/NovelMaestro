# TODO — раунд 22: план (ретраи 3 + Заметки проекта + разделы + единая «Проверка» + Шаблоны-файловый менеджер)

Статус: **реализация завершена (M1–M5), тесты зелёные, коммит — последний шаг**..
Ниже — исследование по каждому пункту запроса и майлстоуны с картой правок.

Идеология: планирование → функциональность → поддерживаемость → надёжность → тесты.
Интерфейс и логи — на русском. Единицы и имена артефактов — не трогать.

---

## Исследование (диагностика по пунктам запроса)

### 1. Ретраи по умолчанию 3 взамен 25

Где сейчас дефолты повторов (grep по `default=25` / `max_retries=`):

| Место | Сейчас | Комментарий |
| --- | --- | --- |
| `core/common.py::stream_chat_completion` | `max_retries=3` | уже 3 — серверный стрим, не трогаем |
| `scripts/translate_book.py::MODE_PRESETS` | translate=25, redact=5, polish=25 | «исторические дефолты»; help в `--max_retries` (стр. 315) тоже говорит «translate/polish=25, redact=5» |
| `scripts/ner.py::--retries` | 25 | стр. 1404 |
| `web/stages.py` поля форм | ner `retries=25` (590), wiki `retries=10` (732), translate_check_llm `max_retries=5` (680), ner_check `max_retries=3` (643 — уже 3) | дефолты, которыми SPA предзаполняет формы |
| `tests/test_translate_book.py:142` | `assert MODE_PRESETS["polish"]["max_retries"] == 25` | единственный тест, завязанный на 25 |

**Решение**: единый дефолт **3** во всех перечисленных местах (translate/polish/redact, ner, wiki, translate_check_llm — включая те, что 5/10: «по умолчанию 3» без исключений). Тест `test_web_jobs.py:655` передаёт `retries="25"` явным значением формы (не дефолт) — можно оставить, но привести к `"3"` для единообразия. В `web/stages.py` обновить `default` у полей; в `translate_book.py` — пресеты и help; в `ner.py` — `--retries` и help.

### 2. Вкладка «Заметки» в проекте (после «Логи»)

- Табы проекта — `web/static/project-views.js::TABS` (сейчас: files, ner, review, check, status, config, prompts, logs). Вставить `["notes", "Заметки"]` после `["logs", "Логи"]`.
- Хранение: `source/info.md`. **Новый роут НЕ нужен**: `GET/PUT /api/file?project=&path=source/info.md` уже умеет читать/писать произвольный путь внутри проекта (проверено: `_file_read`/`_file_write` ходят через `resolve_path` без ограничения по подпапкам). Поведение «файла ещё нет»: GET вернёт 404 → в SPA показываем пустой редактор со статусом «инфо-файла ещё нет — сохраните, чтобы создать».
- Шаблон: `templates/General/source/info.md` — `fill_project_from_template` уже копирует верхний уровень `source/` без перезаписи (стр. 125 core/projects.py) → файл автоматически попадёт во все новые проекты. Дополнительно копировать ничего не нужно.
- **donate.txt/replacements.txt (решение пользователя)**: `templates/General/source/donate.txt` уже есть (проверено) → копируется в `source/donate.txt`, `clean_and_compile.py::load_donate_page` ищет его первым кандидатом; `templates/General/prompts/replacements.txt` — **добавить** (по образцу `templates/replacements.txt.example`) → копируется в `prompts/replacements.txt`, `batch_replace.py` берёт его по дефолту. Файлы-образцы `templates/*.example` остаются как доки.
- Содержимое шаблона (минимум по запросу): названия на русском/английском/китайском, ссылка на оригинал, ссылка на Rulate, описание, промпты генерации обложек — секции-заголовки с пустыми местами.
- SPA: `notesView()` в project-views.js по образцу `viewNotes()` из app.js (CodeMirror md + превью в sandbox-iframe, кнопки Рендер/Сохранить), но с путём `source/info.md` и сохранением через `PUT /api/file`.

### 3. Управление разделами в «Проекты»

- Сейчас `SECTIONS = ["ACTIVE", "HOLD", "DONE", "DONE_OPEN"]` — **жёсткая константа** в `core/projects.py`, зашита в: `ensure_projects_root`, `project_dir`, `list_projects`, `create_project`, `move_project`, `rename_project`, `delete_project`, `copy_project`; в `web/api.py` — `_sections`, `_projects_list`, `_projects_create`, `_collect_stats`; в тестах `test_projects_core.py`/`test_run_flows.py` (итерируют `P.SECTIONS`), в `test_docs.py` (список API), в `tests/test_web_api.py:86` (assert списка разделов).
- **Решение пользователя**: дефолты — `ACTIVE/HOLD/DONE` (DONE_OPEN выходит из дефолтов; существующая папка подхватится как кастомный раздел-миграция). Переименование разрешено всем (включая дефолтные); удаление непустого раздела — **запрещено** (любого, включая дефолтные); пустой удаляется. Свежая установка: `run.py::bootstrap_projects()` и `web/main.py:194` уже вызывают `ensure_projects_root` — projects/ и дефолтные разделы создаются сами (проверено; добавить тест-гард).
- Персист: `projects/.sections.json` (рядом с `.hub_state.json`; уже в .gitignore через projects/) — **полный упорядоченный список** разделов (источник истины после бутстрапа).
- Операции:
  - `load_sections(root)` — список из .sections.json; файла нет → дефолты + легаси-папки на диске (миграция DONE_OPEN); папка на диске, которой нет в списке → добавляется (ручные папки);
  - `save_sections(root, sections)` — атомарная запись;
  - `create_section(root, name)` — валидация `valid_project_name`, дубликат → ошибка; mkdir + запись списка;
  - `rename_section(root, src, dst)` — dst уже существует → **предупреждаем и просто переносим проекты** (`move_project` каждого src→dst; коллизия имени проекта → ошибка), затем удаление src; dst нет → `shutil.move` папки; в обоих случаях обновление списка; дефолтные переименовывать можно;
  - `delete_section(root, name)` — непустой → **отказ** («Раздел не пуст — сначала перенесите или удалите проекты»); пустой → удаление папки + записи списка.
  - `ensure_projects_root` — создаёт дефолтные папки ACTIVE/HOLD/DONE и .sections.json (если нет); существующие кастомные папки не трогает.
- `hub_state` (последний выбранный раздел) при переименовании/удалении — зачищать ссылку.
- API: `POST /api/sections` (создать), `POST /api/sections/rename` ({src,dst}), `DELETE /api/sections/{name}` (непустой → 400 с сообщением; `_check_confirm` НЕ нужен — удаление непустого запрещено).
- SPA: кнопка «Управление разделами» в шапке «Проектов» (`viewHub`) → модалка: список разделов (счётчик проектов), действия Переименовать/Удалить, «＋ Раздел». После изменения — сброс `hubCache` и `render()`.

### 4. «Ревью» + «Проверка» → единая «Проверка»

- Сейчас две вкладки: `["review", "Ревью"]` (reviewView: карточки ner_review.json + translate_check_llm_review.json) и `["check", "Проверка"]` (checkView: отчёты `logs/check_*.txt` алгоритмического translate_check).
- **Решение**: один таб `["review", "Проверка"]`; внутри — три визуально разделённые секции по порядку:
  1. **Проверка глоссария (LLM)** — `ner_review.json` (существующая карточка reviewView);
  2. **Проверка перевода (алгоритмическая)** — отчёты `logs/check_*.txt` (логика checkView переносится внутрь);
  3. **Проверка перевода (LLM)** — `translate_check_llm_review.json` (существующая карточка).
- Разделение нагляднее: заголовки секций с номерами «1. / 2. / 3.» + подзаголовок «бывшая вкладка "Проверка"» у алгоритмической, разделители (`.review-section` + border-top).
- `checkView()` как отдельная вьюха удаляется; её код переезжает во внутреннюю функцию `renderCheckReports()` внутри reviewView.

### 5. Шаблоны → почти полноценный файловый менеджер

Текущее состояние (раунд 21): `viewTemplates` в app.js — список наборов → файловый список (только файлы; каталоги выводятся из путей), Правка/Переименовать/Удалить/＋Файл; General — только чтение; загрузки/скачивания нет; `create_template_set` создаёт только `prompts/`.

Пробелы по запросу:

- **Загрузка файлов**: роута нет (`/api/upload` привязан к проекту через `_project_ctx`). Нужен `POST /api/templates/{set}/upload` (multipart, переиспользовать `parse_multipart`/`extract_files`, лимит `max_upload_mb`; General → 403).
- **Новый набор** — каталог пустой: `create_template_set` создаёт только `prompts/`; нужно создавать каркас как у General: `prompts/` + `source/` (пустые).
- **«＋ Каталог»**: функции нет. Нужен `create_template_dir` в core + `POST /api/templates/{set}/mkdir` (пустая папка; `write_template_file` и так создаёт родителей, но пустой каталог без файла создать нельзя).
- **Пустые каталоги пропадают**: `templates_files` возвращает только файлы (`rglob` + `is_file()`), а `UICore.dirEntries` строит каталоги из путей файлов → каталог, где удалили последний файл, исчезает. Решение: `templates_files` возвращает и пустые каталоги (путь с завершающим `/`, напр. `"source/"`), `dirEntries` — пропускать `rest === ""` (иначе сам текущий каталог станет «файлом с пустым именем»).
- **«Переименовать» → «Переим.»** — текст кнопки.
- **«Скачать»**: роута нет. Нужен `GET /api/templates/{set}/download?path=` (как `_file_download`, через `handler._send`).
- **Паритет с «Файлами»**: загрузка кнопкой + drag&drop, скачивание, переименование, удаление, правка, пагинация (в filesView — 200/стр.), «＋ Каталог», плюс превью md/html в редакторе (уже есть).

---

## План реализации

### M1 — Ретраи по умолчанию 3 (пункт 1)

- [x] `scripts/translate_book.py::MODE_PRESETS`: `translate`/`redact`/`polish` → `max_retries=3`; help `--max_retries`: «Попытки на чанк. Дефолты: 3 (все режимы)».
- [x] `scripts/ner.py::--retries`: `default=25 → 3`; help «Повторные попытки (дефолт 3)».
- [x] `web/stages.py`: поля форм — ner `retries` 25→3, wiki `retries` 10→3, translate_check_llm `max_retries` 5→3 (ner_check уже 3).
- [x] Тесты: `tests/test_translate_book.py:142` — `== 25` → `== 3`; `tests/test_web_jobs.py:655` — `"retries": "25"` → `"3"` (явное значение формы); smoke `--help` по translate_book.py/ner.py.

### M2 — Вкладка «Заметки» проекта (пункт 2)

- [x] `templates/General/source/info.md` — шаблон с секциями: названия ru/en/zh, ссылки (оригинал, Rulate), описание, промпты обложек (пустые места для заполнения). Копирование в новые проекты уже работает через `fill_project_from_template` (проверить тестом).
- [x] `templates/General/prompts/replacements.txt` — НОВЫЙ файл правил массовых замен (контент из `templates/replacements.txt.example`); `donate.txt` уже в General/source — только проверка.
- [x] Тест: `fill_project_from_template` копирует в новый проект `source/info.md`, `source/donate.txt`, `prompts/replacements.txt`.
- [x] `web/static/project-views.js`: `TABS` — `["notes", "Заметки"]` после `["logs", "Логи"]`; `notesView()` по образцу `viewNotes()` (app.js): md-редактор + превью, GET/PUT `/api/file?path=source/info.md`; 404 → пустой редактор со статусом «инфо-файла ещё нет — сохраните, чтобы создать».
- [x] Тесты: `tests/test_projects_core.py` — `fill_project_from_template` копирует `source/info.md` в новый проект; `tests/test_web_api.py` — чтение/запись `source/info.md` через `/api/file` (реальный HTTP); JS-юнит на рендер заметок не нужен (вьюха — DOM).

### M3 — Управление разделами (пункт 3)

- [x] `core/projects.py`:
  - `SECTIONS` остаётся константой дефолтов (для `test_docs.py` и обратной совместимости), но все проверки переходят на `load_sections(projects_root)`:
    - `load_sections(root)` — дефолты + кастомные из `projects/.sections.json` (дефолты первыми, без дублей; отсутствие файла = только дефолты);
    - `save_sections(root, sections)` — атомарная запись (`atomic_write`-паттерн);
    - `create_section(root, name)` — валидация `valid_project_name`, дубль → ошибка, создание папки + запись в .sections.json;
    - `rename_section(root, src, dst)` — dst существует → перенос всех проектов src→dst (`move_project` по одному) и удаление src; dst нет → `shutil.move` папки; дефолтные разделы — защита от переименования/удаления;
    - `delete_section(root, name)` — пустой: удаление папки + записи; непустой: возврат ошибки/флага `needs_confirm` (решение ниже).
  - `ensure_projects_root` — создаёт только дефолтные разделы.
- [x] `web/api.py`: `_sections`/`_projects_list`/`_projects_create`/`_collect_stats` → `load_sections`; новые роуты `POST /api/sections`, `POST /api/sections/rename`, `DELETE /api/sections/{name}` (непустой → `_check_confirm` со словом «УДАЛИТЬ»); зачистка ссылки в `hub_state`.
- [x] `web/static/app.js::viewHub`: кнопка «Управление разделами» → модалка (список с счётчиками, Переименовать/Удалить/＋ Раздел); после изменений — `hubCache = null; render()`.
- [x] `core/projects.py`: `SECTIONS` → `DEFAULT_SECTIONS = ["ACTIVE", "HOLD", "DONE"]`; все проверки на `load_sections(root)`; docstring и комментарии.
- [x] Доки: `README.md` (разделы больше «фиксированные» — динамические, дефолты ACTIVE/HOLD/DONE), `AGENTS.md` §3, `core/README.md`, `web/README.md` — синхронизация; `tests/test_docs.py` (PROJECTS_API + раздел про SECTIONS).
- [x] Тесты: `tests/test_projects_core.py` — create/rename/rename-merge/delete (непустой → отказ), переименование дефолтного, персист в .sections.json, миграция легаси-папки (DONE_OPEN); `tests/test_web_api.py` — роуты на реальном сервере + правка assert на стр. 86; `tests/test_run_flows.py` — `ensure_projects_root` создаёт дефолты и не трогает кастомные (свежая установка).

### M4 — Единая «Проверка» (пункт 4)

- [x] `web/static/project-views.js`:
  - `TABS`: убрать `["review", "Ревью"]` и `["check", "Проверка"]` → `["review", "Проверка"]`;
  - `reviewView()`: три секции с номерами 1–3 и разделителями: глоссарий (LLM) → отчёты translate_check (алгоритмическая, пометка «бывшая вкладка "Проверка"») → перевод (LLM); логика `checkView()` переносится во внутреннюю `renderCheckReports()` (пагинации отчётов и строк сохранить);
  - `checkView` удаляется; роут `/api/check` и хендлер `_check_reports` не трогаем (данные те же).
- [x] Стили: `.review-section` (отступ/рамка между секциями), заголовки с бейджами «1 · LLM», «2 · алгоритмическая», «3 · LLM».
- [x] Тесты: JS-юниты не нужны (DOM); ручная приёмка — чек-лист внизу.

### M5 — Шаблоны: файловый менеджер (пункт 5)

- [x] `core/projects.py`:
  - `create_template_set` — каркас `prompts/` + `source/` (как у General);
  - `create_template_dir(templates_dir, name, rel)` — пустой каталог (General 403, эскейпы отклоняются);
  - `templates_files` — дополнительно возвращать **пустые каталоги** (путь с завершающим `/`); `delete_template_file` — поведение не меняем (каталог остаётся видимым благодаря новому листингу);
  - `download_template_file` не нужен в core — чтение байтов в api-слое (как `_file_download`).
- [x] `web/api.py`:
  - `POST /api/templates/{set}/upload` — multipart (переиспользовать `parse_multipart`/`extract_files`/`max_upload_mb`), dest-подпапка внутри набора, General → 403;
  - `GET /api/templates/{set}/download?path=` — `handler._send` с attachment (как `_file_download`);
  - `POST /api/templates/{set}/mkdir` — `{path}` → `create_template_dir`.
- [x] `web/static/app.js::viewTemplates`:
  - «＋ Каталог» в тулбаре набора (nameModal → mkdir);
  - «Скачать» в строке каждого файла (link на download-роут, как в filesView);
  - «Переименовать» → «Переим.»;
  - загрузка: кнопка «Загрузить» (hidden input, multiple) + drag&drop на список (как filesView);
  - пагинация списка (как filesView, 200/стр.);
  - `UICore.dirEntries` — пропуск `rest === ""` (пустые каталоги с trailing `/`).
- [x] Тесты: `tests/test_projects_core.py` — каркас набора (prompts+source), `create_template_dir`, пустые каталоги в `templates_files`; `tests/test_web_api.py` — upload/download/mkdir на реальном HTTP; `tests/spa/ui-core.test.mjs` — dirEntries с пустым каталогом.

---

## Файлы (карта правок)

| Файл | Что |
| --- | --- |
| `scripts/translate_book.py` | пресеты max_retries 25/5 → 3, help |
| `scripts/ner.py` | `--retries` 25 → 3, help |
| `web/stages.py` | дефолты полей retries: ner 3, wiki 3, translate_check_llm 3 |
| `templates/General/source/info.md` | НОВЫЙ: шаблон заметок проекта (названия ru/en/zh, ссылки, описание, промпты обложек) |
| `templates/General/prompts/replacements.txt` | НОВЫЙ: правила массовых замен (из replacements.txt.example) — копируется в projects/prompts/ |
| `templates/General/source/donate.txt` | уже есть — проверить копирование тестом |
| `README.md`, `AGENTS.md`, `core/README.md` | разделы: дефолты ACTIVE/HOLD/DONE, динамические разделы |
| `core/projects.py` | разделы: load/save/create/rename/delete_section, все проверки на load_sections; шаблоны: каркас prompts+source, create_template_dir, пустые каталоги в templates_files |
| `web/api.py` | роуты разделов (/sections CRUD), /api/templates/{set}/upload, download, mkdir; _sections и др. на load_sections |
| `web/static/project-views.js` | таб «Заметки» (notesView), объединённая «Проверка» (3 секции), удаление checkView |
| `web/static/app.js` | viewHub: «Управление разделами»; viewTemplates: загрузка/скачивание/＋Каталог/пагинация/«Переим.» |
| `web/static/ui-core.js` | dirEntries: skip rest=="" |
| `web/static/styles.css` | .review-section, кнопки разделов |
| `web/README.md` | новые роуты (разделы, шаблоны upload/download/mkdir) |
| `core/README.md` | новые функции раздела «проекты» (синхронно) |
| `tests/…` | per-майлстоун: test_projects_core, test_web_api, test_run_flows, test_translate_book, test_web_jobs, ui-core.test.mjs |
| `tests/test_docs.py` | сверка доков с кодом (новые функции в таблицу §6) |
| `TODO.md` | этот файл |

---

## Проверки (обязательные перед коммитом)

- [x] `python3 -m pytest tests/ -q` — все зелёные (коммит с падающими тестами запрещён).
- [x] `node --test tests/spa/` + `node --check` по web/static/*.js.
- [x] Smoke: HTTP-сервер — создание/переименование/удаление раздела (merge проектов, отказ на непустом), заметки проекта (PUT/GET source/info.md), upload/download/mkdir шаблонов, `--help` по translate_book.py/ner.py.
- [x] Свежая установка: покрыто тестами на tmp (`test_run_flows.py` + `test_sections_*`), реальный `projects/` (12 ГБ) не трогали; легаси DONE_OPEN реального корня подхватится как кастомный при первом запуске.
- [x] Коммит + push — выполнен (e194b6b).

---

## Ручная приёмка (через web)

1. Форма запуска NER/перевода/wiki: поле повторов предзаполнено «3»; CLI `--help` показывает дефолт 3.
2. Новый проект по шаблону General: в `source/` есть `info.md` с секциями; вкладка «Заметки» — редактор + превью, сохранение пишет файл.
3. «Проекты» → «Управление разделами»: создать раздел; переименовать в существующий (предупреждение, проекты перенеслись); переименовать дефолтный ACTIVE; удалить пустой (сразу) и непустой (отказ «сначала перенесите или удалите проекты»).
4. Проект → «Проверка»: три секции по порядку (глоссарий LLM → алгоритмическая → перевод LLM), отчёты check_*.txt видны в средней секции.
5. Шаблоны: загрузка файлов (кнопка + drag&drop), «Скачать», «＋ Каталог», пустой каталог виден после удаления всех файлов, кнопка «Переим.», пагинация; General — по-прежнему только чтение.
6. Новый проект по General: в `source/` есть `info.md` и `donate.txt`, в `prompts/` — `replacements.txt`; batch_replace подхватывает его по дефолту.

---

## Решения (утверждены пользователем)

- **Удаление непустого раздела** — ЗАПРЕЩАЕМ: отказ «Раздел не пуст — сначала перенесите или удалите проекты»; подтверждение словом не нужно (пустые разделы удаляются сразу).
- **Дефолтные разделы**: дефолты — ACTIVE/HOLD/DONE (DONE_OPEN выходит из дефолтов, легаси-папка мигрирует в кастомные); переименование разрешено всем; от удаления защищены только непустые (единое правило для всех разделов).
- **Свежая установка**: projects/ и дефолтные разделы создаются автоматически (`run.py::bootstrap_projects` + `web/main.py` → `ensure_projects_root`; .env копируется из шаблона; notes.md — при первом сохранении). Закрепить тестом.
- **Все ретраи — 3** по дефолту (translate/polish/redact, ner, wiki, translate_check_llm; ner_check уже 3).
- **General — read-only** (включая загрузку/скачивание запись); в General добавить `replacements.txt` в `prompts/` (donate.txt уже есть в `source/`) — оба подхватываются новыми проектами и скриптами (batch_replace, clean_and_compile).

---

## Вне скоупа (осознанно не делаем)

- Удаление непустых разделов (запрещено единым правилом; «перенесите или удалите проекты»).
- Полноценный CRUD проектов внутри модалки разделов (управление проектами остаётся в карточках).
- Запись в General (read-only, включая загрузку файлов).
- Хранение заметок проекта не в source/info.md (именование зафиксировано запросом).
- Переименование проектов при переименовании раздела (имена проектов не меняются, переносится папка/проекты как есть).

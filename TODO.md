# TODO — согласование: «Простой режим» в Запусках, ревью кода, улучшения

Статус: **реализовано** (ответы на вопросы 1.4 учтены). Коммиты:
`feat(web): простой/экспертный режим…`, `fix(web): сироты…`, `fix(web): …`
(см. git log). Невыполненное — ниже в тексте (U1/U3/U4/U9/U12, B7).

---

## 1. «Простой режим / Экспертный» в Запусках — ✅ выполнено

### 1.1 Общая механика — ✅

- В панели «Запуски» (`web/static/run-views.js`) — сегмент-переключатель
  **«Простой режим / Экспертный»** над формой стадии (`modeToggle`).
- **Простой режим**: карточка пресета — название, 1–2 строки «что будет
  сделано», диапазон глав (всегда отображается, если стадия принимает
  start/end), одна кнопка «Запустить». Все поля скрыты.
- **Экспертный**: текущая форма целиком, без изменений (`expertForm`).
- **Отправка**: простой режим шлёт `params` = `spec.preset.params`
  (считается на сервере: непустые дефолты полей + overrides пресета).
  Диапазон глав уходит в params как start/end, если заполнен.
- **LLM-поля (host/model/api_key)** в простом режиме не показываются
  вовсе: скрипты сами берут их из .env (`get_server_config` + fallback
  на системный .env — уже работает, `_llm_argv`).
- Запоминание режима — localStorage (`runMode`, JSON по стадиям;
  дефолт — «простой»). Хелперы `UICore.runModeGet/runModeSet`.
- Серверный API: `POST /api/jobs` без изменений; `_stage_spec` теперь
  вкладывает `preset.params` в ответ (логика — в `web/stages.py`).

### 1.2 Что в простой режим, что в экспертный — ✅ (по таблице)

Все поля — экспертные (в простом режиме форма скрыта целиком); пресеты:

| Стадия | Пресет (title / desc) | overrides |
| --- | --- | --- |
| epub | «Разобрать исходник» / автопоиск в source/, zh, чистки и уборка номеров | `lang: zh` |
| ner | «Собрать главы + глоссарий» / склейка chapters/*/chapter.txt в память, извлечение в ner.json | `mode: compile` |
| ner_check | «Проверить глоссарий» / все проходы, правки не применяются | — |
| pipeline | «Перевести книгу» / полный цикл, промпты из prompts/ | — |
| translate_check | «Сравнить перевод» / polished vs исходник, все главы | — |
| translate_check_llm | «Проверить перевод (LLM)» / polished, один проход | — |
| batch_replace | «Применить замены» / prompts/replacements.txt по polished | — |
| compile | «Собрать книгу» / TXT из polished, все главы | — |
| wiki | «Создать вики» / wiki.md: ner.json + перевод | — |

### 1.3 Реализация — ✅

1. `web/stages.py`: `preset` (title/desc/overrides) в каждой спеке +
   `preset_params()` (дефолты формы + overrides; булёвы входят всегда,
   files префиксуются папкой, LLM-поля не попадают — пустые дефолты).
2. `web/static/run-views.js`: переключатель, карточка пресета, отправка
   `preset.params`; лог/прогресс/стоп — как сейчас.
3. `web/api.py`: `_stage_spec` вкладывает `preset.params` (спека уходит
   целиком, expert-поля SPA игнорирует).
4. Тесты: `tests/test_web_jobs.py` — пресеты (params == дефолты спеки +
   overrides, spot-checks, argv-собираемость); SPA — `tests/spa/ui-core.test.mjs`
   (runMode, isCjkString).
5. Доки: web/README.md, README.md, AGENTS.md (localStorage-предпочтение).

### 1.4 Вопросы к пользователю — ответы учтены

- Дефолт переключателя: **простой**.
- Глобальный переключатель, память **по стадии**.
- ner в простом: «собрать главы + извлечение» (compile, без входного txt);
  во всех Запусках простого режима отображается диапазон глав.
- Сводка пресета: одна строка + кнопка.
- Расхождения дефолтов (B6): скрипты выровнены под форму.

---

## 2. Общая проверка кода — найденные баги и проблемы

### B1 (средний) — ✅ наблюдатель сирот

`web/jobs.py::_orphan_watch` — фоновый поток (daemon, `ORPHAN_POLL=5с`)
опрашивает running-запуски без proc (сироты после рестарта): pid умер →
failed + exit_code=1 + persist + notify. Тесты: `test_orphan_watcher_marks_dead`,
`test_orphan_watcher_ignores_live`.

### B2 (низкий) — ✅ remove() останавливает сирот

`remove()`: SIGTERM уходит и по pid сироты (`_signal_group_pid`), если
proc=None, но pid жив. Тест: `test_remove_stops_orphan`.

### B3 (низкий) — ✅ upload с dir="" в корень проекта

`/api/upload` с пустым `dest` = корень проекта (поля files с dir="");
SPA шлёт `dest = dir || ""`. Тест: `test_upload_to_project_root`.

### B4 (низкий, UX) — ✅ смена host очищает api_key

В `expertForm` при изменении host поле api_key очищается (C1 защищал
только env-fallback).

### B5 (низкий) — ✅ кэш options инвалидируется

`st.options = null` при смене стадии и после завершения запуска
(в attachStream перед финальным render).

### B6 (низкий) — ✅ дефолты скриптов выровнены под форму

- ner threads 1 → **4** (cli/ner.py);
- ner_check timeout/stream_timeout 900 → **300** (cli/ner_check.py);
- translate_check_llm max_retries 5 → **3** (cli/translate_check_llm.py).
U2: тест-таблица форма ↔ argparse (build_parser вынесен из main в ner.py).

### B7 (низкий) — ⏸️ НЕ делал

Зомби-детект только на Linux (`/proc/<pid>/stat`); на macOS/stdlib
портабельного способа нет (psutil — тяжёлая зависимость, запрещена
AGENTS.md). Некритично: зомби быстро исчезают.

### B8 (инфо) — ✅ мёртвый груз

- `vendor/alpine.min.js` и toast-host на Alpine убраны (тосты — обычный DOM);
- `web/main.py --no-auth` — оставлен (совместимость);
- `env_keys_for(profile=...)` — параметр оставлен (публичная сигнатура).

### B9 (низкий) — ✅ isCjkString и суррогатные пары

Итерация `for...of` (code points), экспорт `UICore.isCjkString` +
тест в ui-core.test.mjs.

### B10 (низкий) — ✅ таблица глав по реальным id

`options.chapters.ids` (api.py) + строки с `data-id`; SSE-обновление —
по `[data-id=…]`, не nth-child. Тест: `test_stage_options_api` обновлён.

### B11 (низкий) — ✅ ротация web.log

`RotatingFileHandler` (5 МБ, backupCount=2) в `web/main.py`.

### B12 (инфо) — ✅ сужены широкие except

`cli/clean_and_compile.py` (parse_yaml_meta → OSError/ValueError;
epub/fb2-native → OSError/ValueError/TypeError), `cli/epub_to_chapters.py`
→ KeyError/IndexError/ValueError.

---

## 3. Улучшения, упрощения, поддержка и расширение

### 3.1 Упрощение кода

- **U1. Декларативный сбор argv** — ⏸️ НЕ делал (крупный рефактор
  build_*; риск регрессий не оправдан в этой задаче).
- **U2. Единый источник дефолтов** — ✅ тест-таблица форма ↔ argparse
  (см. B6).
- **U3. Вынести st.options-логику** — ⏸️ частично: params простого
  режима считаются на сервере (stages.py), JS их не дублирует; полный
  вынос логики опций — не делал.
- **U4. Разбить project-views.js** — ⏸️ НЕ делал (4010 строк, отдельная
  задача).

### 3.2 Надёжность

- **U5. Наблюдатель сирот** — ✅ = B1.
- **U6. Reconnect SSE** — ✅ `attachStream` с backoff (1с, 2с, 4с, …,
  ≤5 попыток); при переподключении st.log очищается — снапшот хвоста
  приходит целиком без дублей.
- **U7. Ротация web.log** — ✅ = B11.

### 3.3 Оптимизация

- **U8. Кэш `_stage_options`** — ✅ по сигнатуре mtime папок
  (chapters/source/prompts/корень); тест `test_stage_options_cache_invalidates`.
- **U9. Хвост SSE постранично** — ⏸️ НЕ делал (API-изменение
  offset/limit, низкая ценность).

### 3.4 Поддержка и расширение

- **U10. Пресеты как расширяемый механизм** — ✅ задел: preset =
  {title, desc, overrides}, params считаются; добавление второго пресета —
  селект в простом режиме (структура готова).
- **U11. Тесты SPA шире** — ✅ частично: ui-core.test.mjs — runMode
  (localStorage-стаб) и isCjkString; рендер карточки/отправка params —
  без jsdom не тестируются (params-логика покрыта Python-тестами).
- **U12. Согласованность matcher JS↔Python** — ⏸️ НЕ делал (property-тест,
  отдельная задача).
- **U13. Доки** — ✅ web/README.md, README.md, AGENTS.md.

### 3.5 Проверено, замечаний нет — без изменений

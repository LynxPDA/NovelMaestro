# TODO — план: системный .env в корень, правки review-интерфейса, настройки внешнего вида

Статус: **готово (ожидает коммита и push).**

---

## Дополнительные задачи (по ходу)

### T6 — Флаги применения в «Настройках» не применялись (баг)

- [x] `api("/api/settings")` → `api("/settings")`: хелпер api() сам
  добавляет префикс /api, двойной префикс давал 404 → молчаливый catch →
  сброс темы на тёмную. Заодно исправлен `api("/api/jobs")` (очистка
  истории запусков — тот же двойной префикс).
- [x] `exportModal` перенесена из app.js в project-views.js (кросс-файловый
  глобал, no-unused-vars); `catch (_e)` → optional catch binding.

### T7 — JSON-поля review-файлов на английском (правило консистентности)

- [x] Правило в AGENTS.md §7 (JSON-файлы данных: поля — английские,
  значения — русские; жёстко, без fallback-чтения).
- [x] `core/common.py`: review_entry/fix_entry/parse_review_doc/
  merge_review_entries/merge_fix_entries/apply_ner_patches —
  `stage`/`status`/`applied`/`applied_at`/`reason`/`chapter`/`file`/`type`,
  контейнер `entries`, мета `created`/`updated`/`input`/`params`.
- [x] `scripts/ner_check.py`, `scripts/translate_check_llm.py` — чтение/
  запись/применение/логгеры.
- [x] SPA: ui-core.js (parseReviewContent/update/remove/reviewSummary),
  project-views.js (вьювер: chapter/type/file/reason/status/applied).
- [x] Тесты: pytest (849) + node (44) обновлены и зелёные.
- [x] Доки: README.md, core/README.md, web/README.md, AGENTS.md §6.

---

Идеология: планирование → функциональность → поддерживаемость → надёжность → тесты.
Интерфейс и логи — на русском. Единицы и имена артефактов стадий — не трогать.

---

## Исследование (диагностика по пунктам запроса)

1. **Системный .env живёт в `projects/.env`** — его читают `find_env_file`
   (канон `DIR/projects/.env` → `DIR/.env`), web-редактор (`_env_path`
   scope=global → `_projects_root/.env`), `run.py` (копия шаблона),
   `_persist_run_params` (источник копии в pdir/.env). Доки уже частично
   говорят про «корневой .env» (`web/README.md:22,184`) — код отстал.
   Перенос: корневой `.env`, проектный `pdir/.env` по-прежнему приоритетнее.
2. **Незакоммиченные правки пользователя** (сделаны вручную): userscript
   Rulate перенесён `scripts/Other_tools/` → `tools/`, из `.gitignore`
   убраны `servers/` и `backup/`, со скриптов снят бит исполняемости
   (755→644), `web/static/run-views.js` и `styles.css` отформатированы.
3. **Запуски · Проверка перевода (LLM)**: в форме стадии 4 чекбокса
   `apply`/`auto_apply`/`dry_run`/`no_bak` — для web-запусков они не нужны
   (применение — через «Проверка» проекта), должны быть скрыты и всегда
   выключены. Сборка argv — `web/stages.py::build_translate_check_llm`.
4. **Проверка · «Редактор JSON»** (`project-views.js::makeCard`):
   - редактор создаётся пустым (`makeEditor("", "json")`), значение
     проставляется только «если ed уже есть» — при первом переключении
     окно пустое, хотя `load()` уже прочитал файл;
   - нет «Очистить» (удалить все правки) и «Удалить» (одну запись);
   - «Откорректировать»: поле «Было» можно менять — правка перестанет
     находиться; в подписях торчат `(old)`/`(new)`.
5. **`translate_check_llm_changes.md`** — отчёт-дубликат web-интерфейса
   (`scripts/translate_check_llm.py:write_changes_md`), выпилить.
6. **Настройки внешнего вида**: редакторы (CodeMirror 6) наследуют
   синтаксические цвета светлой темы вендора (`#a11` — тёмно-красный на
   тёмном фоне нечитаем). Классы подсветки вендор генерирует автоименами
   (`JsClass.newName`) — CSS-переопределения не работают; нужен свой
   `HighlightStyle` (в вендоре есть `Zs`=HighlightStyle, `Fu`=syntaxHighlighting,
   `p`=tags — не экспортированы). Плюс светлая тема всего интерфейса
   (по умолчанию — тёмная) через переопределение CSS-переменных `:root`.

---

## План реализации

### T1 — Системный .env → корень репо

- [x] `core/common.py::find_env_file` — кандидат только `<dir>/.env`
  (из папки книги первым находится её `pdir/.env`, из корня — корневой);
  docstring.
- [x] `run.py::bootstrap_projects` — системный `.env` в `REPO/.env`;
  перенос существующего `projects/.env` → корень (если корневого нет).
- [x] `web/api.py` — `_env_path`/`_env_ctx` scope=global → `_repo_root/.env`;
  `_persist_run_params` — источник копии корневой `.env`; docstrings.
- [x] `web/main.py`, `web/stages.py` — упоминания projects/.env.
- [x] `templates/.env.example` — комментарии WEB_* → корневой .env.
- [x] Доки: `core/README.md`, `web/README.md`, `AGENTS.md` §6/§7.
- [x] Тесты: `test_core_common.py`, `test_run_flows.py`, `test_web_m7.py`.
- [x] Перенос реального `projects/.env` → `./.env`.

### T2 — Закоммитить ручные правки пользователя

- [x] `chore(repo): userscript Rulate перенесён в tools/`
- [x] `chore(repo): .gitignore — убраны servers/ и backup/`
- [x] `chore(scripts): снят бит исполняемости со скриптов`
- [x] `style(web): форматирование SPA-кода`

### T3 — Запуски: скрыть флаги применения у translate_check_llm

- [x] `web/stages.py`: убрать поля `apply`/`auto_apply`/`dry_run`/`no_bak`
  из STAGE_SPECS.translate_check_llm; сборка флагов в argv — только для
  пути «Проверка» (маркер `ctx["review_apply"]`).
- [x] Тесты: `test_build_translate_check_llm_no_bak`,
  `test_build_translate_check_llm_flags` — флаги не собираются.

### T4 — Проверка (LLM): редактор JSON и список правок

- [x] `project-views.js`: `renderEditor` — инициализация содержимым
  `parsed`; кнопка «Очистить» (пустые `правки`); «Удалить» в строке;
  «Откорректировать» — поле «Было» readonly, подписи без (old)/(new).
- [x] `ui-core.js`: `removeReviewEntry`; тесты `tests/spa/ui-core.test.mjs`.

### T5 — Светлая тема интерфейса и настройки редактора (.env)

- [x] Ключи: `WEB_UI_THEME` (dark|light, дефолт dark), `WEB_EDITOR_THEME`
  (auto|dark|light, дефолт auto), `WEB_EDITOR_FONT_SIZE` (px, дефолт 13).
- [x] Вендор: экспорт `syntaxHighlighting`/`HighlightStyle`/`tags`.
- [x] `web/api.py`: `GET /api/settings` (валидация + дефолты) + роут.
- [x] `app.js`: `applyEditorSettings` в boot; `makeEditor` — кегль +
  HighlightStyle по теме; `previewCss` — по теме; форма в «Настройках».
- [x] `styles.css`: `:root[data-ui-theme="light"]` — переменные + точечные
  фиксы (#252d38, .log-view); `body[data-editor-theme="light"]` — редакторы.
- [x] Тесты: settings-эндпоинт; ui-core; smoke.

---

## Карта правок

| Файл | Изменения |
| --- | --- |
| `core/common.py` | `find_env_file` — кандидат `<dir>/.env` |
| `run.py` | системный `.env` в корне + миграция |
| `web/api.py` | global-env в корне; `GET /api/settings` |
| `web/main.py`, `web/stages.py` | упоминания пути .env; флаги T3 |
| `scripts/translate_check_llm.py` | удалён `write_changes_md` |
| `web/static/vendor/codemirror.min.js` | экспорт HighlightStyle/tags/syntaxHighlighting |
| `web/static/app.js` | настройки внешнего вида, makeEditor, previewCss |
| `web/static/project-views.js` | T4 — review-карточка |
| `web/static/ui-core.js` | `removeReviewEntry` |
| `web/static/styles.css` | светлая тема, редакторы |
| `templates/.env.example`, `core/README.md`, `web/README.md`, `AGENTS.md` | путь .env + новые ключи |
| тесты | T1/T3/T4/T5 + `tests/spa/ui-core.test.mjs` |

---

## Проверки (обязательные перед коммитом)

- [x] `python3 -m pytest tests/ -q` — 849 passed.
- [x] `node --check` на JS/CSS (см. ниже).
- [x] node-тесты SPA: 44/44 (ui-core.test.mjs).
- [x] Smoke: web — светлая/тёмная тема, редактор JSON непустой, «Очистить»/
  «Удалить» работают, флаги применения не видны в «Запусках».
- [ ] Коммит + push.

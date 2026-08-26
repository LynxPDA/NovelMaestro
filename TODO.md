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
- [x] `cli/ner_check.py`, `cli/translate_check_llm.py` — чтение/
  запись/применение/логгеры.
- [x] SPA: ui-core.js (parseReviewContent/update/remove/reviewSummary),
  project-views.js (вьювер: chapter/type/file/reason/status/applied).
- [x] Тесты: pytest (849) + node (44) обновлены и зелёные.
- [x] Доки: README.md, core/README.md, web/README.md, AGENTS.md §6.

### T12 — Локальные конфиги и баг селекта редактора (пакет)

- [x] pyrightconfig.json убран из git и добавлен в .gitignore (вместе с
      .vscode/, .idea/, .DS_Store, *.iml, .pylintrc) — локальные инструменты;
      .pi-lens.json остаётся в репо (контракт pi-lens, AGENTS.md §8а).
- [x] Фикс дублирования в селекте «Файл» редактора: missing-каноны
      собирались на каждой итерации (выходило 7 вариантов вместо 4);
      теперь missing.push только в ветке отсутствующего канона.

---

### T11 — Переименование scripts/ → cli/ (пакет)

- [x] `git mv scripts cli` + `tests/test_scripts_*.py` → `tests/test_cli_*.py`;
      web/stages.py (argv ×9 + script_path), web/pipeline.py (fallback),
      web/api.py (folder) обновлены.
- [x] Тесты: sys.path на `ROOT / "cli"` (conftest + 10 файлов), argv-assert'ы
      test_web_jobs, fake-repo test_web_m7; pytest 852 зелёные, --help OK.
- [x] Доки: README/web/core/AGENTS/TODO/packaging/templates/tools — все
      упоминания `scripts/` → `cli/` (`_legacy_scripts/` не тронут;
      запрет «интерактивного cli» переформулирован).
- [x] pyrightconfig.json: extraPaths core+cli — тестовые импорты
      разрешаются (пре-экзистинг Optional-находки в тестах помечены
      false-positive; подтверждено на пре-ренейм-коммите).

---

### T10 — Доработки по отзывам (пакет)

- [x] `WEB_PROJECTS_DIR` читается и в run.py (CLI > окружение > системный
      .env > дефолт; find_env_file от корня репо — детерминированно);
      шаблон templates/.env.example дополнен; тест .env-пути.
- [x] Доки: «Пульт» убран везде (актуальные вкладки: Дашборд/Проекты/…,
      внутри проекта — Файлы/Редактор/…/Запуски); «колесо» → wheels.
- [x] Docker: non-root пользователь uid 1000 + `user: "${UID:-1000}:
      ${GID:-1000}"` в compose — файлы в bind-mount принадлежат
      хост-пользователю (проверено e2e: владелец 1000:1000, запись с хоста).
- [x] GitHub Actions: docker.yml (кнопка + тег v*, ghcr.io, amd64+arm64),
      windows.yml (кнопка + тег v*, zip → артефакт/релиз); инпут-харденинг
      через env + валидация версии Python в ps1; .github исключён из сборки.

---

### T9 — Релизные сборки и инженерные задачи (пакет)

- [x] `tools/README.md` — юзерскрипт Rulate (установка, формат файла,
      сопоставление, настройки, безопасность); доки переведены на
      фактический путь `tools/` (AGENTS.md, README.md).
- [x] Кастомная папка проектов: `--projects-dir`/`WEB_PROJECTS_DIR`
      в web/main.py и run.py (bootstrap + проброс); тесты;
      доки (README.md, web/README.md, AGENTS.md).
- [x] regex-fallback NER: найден и исправлен баг — короткий CJK-термин-
      префикс терялся (регэксп-чередование не перекрывает); теперь
      проверка каждого варианта вхождением (семантика Aho-Corasick);
      тест `test_regex_fallback_prefix_overlap`.
- [x] Docker: Dockerfile (python:3.12-slim, deps, healthcheck),
      .dockerignore, docker-compose.yml (порт, volume projects/job_logs,
      env_file required:false); собран и проверен e2e (проект, subprocess).
- [x] Портативная Windows-сборка: `packaging/build_portable_windows.ps1`
      (embeddable Python + pip + deps + start.bat, zip);
      `packaging/README.md` (обе сборки + публичный релиз: LICENSE,
      секреты, GitHub Releases).

---

### T8 — Редактор: отсутствующий файл — пустой редактор (создание при сохранении)

- [x] `web/api.py` `_file_read`: файла нет → `{content: "", missing: true}`
      вместо 404 (каталог — по-прежнему ошибка); PUT создаёт файл.
- [x] SPA `editorTabView`: канонические артефакты главы (chapter/translated/
      redacted/polished.txt) видны в селекте, даже если файла ещё нет
      (missing — в конец, дефолты предпочитают существующие); панель
      открывается пустой, метка «новый файл · …», ввод текста разблокирует
      «Сохранить» → создаёт файл (напр. polished.txt до полировки).
- [x] Заметки/переход из отчётов: проверка существования через `missing`
      вместо ловли 404.
- [x] Тесты: `test_file_read_missing_404` → `test_file_read_missing_empty`
      (пустой редактор + создание через PUT); pytest 849 зелёные.
- [x] Доки: web/README.md (редактор + роут GET /api/file).

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
   Rulate перенесён `cli/Other_tools/` → `tools/`, из `.gitignore`
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
   (`cli/translate_check_llm.py:write_changes_md`), выпилить.
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
| `cli/translate_check_llm.py` | удалён `write_changes_md` |
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

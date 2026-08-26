# AGENTS.md — руководство для AI-агентов (Pi и др.)

> Идеология проекта: **Планирование, Функциональность, Поддерживаемость,
> Надежность, Развитие, Тестирование.**

Этот файл — контракт между проектом и AI-агентом. Читай его ДО любых правок.
Структура репо и пайплайн: `README.md` (первоисточник); API общего модуля:
`core/README.md`; планы и текущие статусы реализации: `TODO.md`.
Здесь — только правила и ограничения для агента.

## 1. Суть

Конвейер перевода веб-новелл (zh→ru) через OpenAI-совместимые LLM-серверы
с человеческими контрольными точками. Интерфейс и логи — на русском языке.
Интерфейс один — **web** (сервер + SPA, пакет `web/`; контракт —
`web/README.md`). `run.py` — тонкий лаунчер: поднимает `web/main.py`
и открывает браузер. Разделы ACTIVE/HOLD/DONE/DONE_OPEN — в web-интерфейсе;
реестр стадий — `web/stages.py::STAGE_SPECS` (ключи-слаги: epub, ner,
ner_check, pipeline, translate_check, translate_check_llm, compile, wiki,
batch_replace).
Стадии: epub_to_chapters → ner → ner_check (LLM-проверка глоссария,
контрольная точка) → pipeline (translate → redact → polish) →
translate_check → translate_check_llm → clean_and_compile → wiki.

## 2. Окружение

- **Зависимости: venv — рекомендуемый способ установки** и на Windows,
  и на Linux; системный python3 + apt-пакеты — допустимая альтернатива.
  Команды в коде и доках остаются унифицированными (`python3`); не пиши
  `.venv/bin/python3` в код и документацию.
- Зависимости: `requests`, `tqdm`, `pyahocorasick` (опционален — есть regex
  fallback), `pytest` (тесты). Единый pip-список — `requirements.txt`.
  Принцип: stdlib + requests; новые тяжёлые зависимости не добавлять,
  опциональные пакеты обязаны иметь fallback.
- **Кроссплатформенность.** Целевая среда — Linux/macOS (системный
  `python3`), но код и доки не должны ломаться на Windows:
  - команды в коде/доках — `python3` (Unix); на Windows `python3` нет,
    поэтому в README указывать явно «на Windows: `python run.py` или
    `py run.py`»;
  - web-сервер — чистый stdlib (`http.server`), SPA — ванильный JS без
    сборки; никаких платформозависимых библиотек;
  - пути — только `pathlib`/`os.path`, без Unix-слешей в хардкоде (см. §4).

## 3. Архитектура (три слоя)

```text
core/     общий код: common.py (логика) + projects.py (менеджмент
          проектов). НЕ скрипты. Интерактива (ui/tui) больше нет.
web/      web-интерфейс: server.py + api.py (роуты/хендлеры), stages.py
          (реестр стадий и сборка argv), jobs.py (JobManager + SSE),
          pipeline.py (web-оркестратор конвейера), static/ (SPA).
          Контракт API — web/README.md.
scripts/  исполнители — чистый CLI (argparse), без интерактивных меню.
          batch_replace.py — массовые замены по файлу правил (prompts/replacements.txt);
          Other_tools/ — вспомогательные утилиты вне конвейера (userscript Rulate).
templates/ шаблоны новых проектов: общие шаблоны в корне (.env.example,
          donate.txt.example, replacements.txt.example); подпапки по типу
          книги — жанру и языку (General/) с промптами и metadata.yaml.
run.py    лаунчер: python3 run.py → web/main.py (+браузер); проброс
          --host/--port/--auth/--token/--max-upload-mb/--jobs-limit.
projects/ <раздел>/<книга>/ — данные проектов (НЕ в git, см .gitignore).
tests/    pytest P0–P2.
```

Правило слоёв:

- интерактив — только в браузере (SPA) и в `web/` (серверная часть
  интерактивна через HTTP); `scripts/` — только argparse, без `input()`
  и без импорта UI-слоёв (их больше не существует);
- общая логика — только в `core/`; скрипты заимствуют импортом из
  `core.common`, НЕ копируют функции себе; менеджмент проектов (разделы,
  переносы, статистика) — только `core/projects.py` (нужен web-слою);
- web-модули импортируют соседей через `from web.* import …`, а общее —
  из `core.common`; логика из `core/` в `web/` НЕ дублируется;
- проекты НЕ содержат копий скриптов. Старые копии в DONE-проектах лежат в
  `_legacy_scripts/` — они заморожены, НЕ трогай и не обновляй их.

## 4. Bootstrap-паттерн (обязателен для новых скриптов)

Все скрипты находят корень репо подъёмом вверх от себя и добавляют его в
`sys.path` перед импортом `core.*`. Абсолютные пути и хардкод запрещены.
`_bootstrap_core()` продублирован в каждой точке входа ОСОЗНАННО (скрипты
запускаются из любого cwd) — не «рефакторить» в один общий импорт:

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

- **СИМВОЛЫ**: `--chunk_size`, `--context_budget`, все пороги длин,
  размеры чанков/пакетов, FTS5 chunk. (`min_len_ratio` — безразмерное
  отношение длин, считается в символах.)
- **ТОКЕНЫ**: только `max_tokens` в payload LLM (серверный предохранитель,
  не расчёт) и `--near-distance` в wiki.py (природа FTS5 NEAR).
- **БАЙТЫ**: только размеры файлов в отчётах translate_check.
- **ГЛАВЫ**: чанкование в clean_and_compile.

Если меняешь размер/бюджет — проверь, что единица верная, и укажи её в help
argparse («СИМВОЛЫ»/«ТОКЕНЫ»).

## 6. Что использовать из core/common.py (не изобретай заново)

| Задача | Функция |
| --- | --- |
| .env | `parse_dotenv` / `find_env_file` (системный корневой `.env`, вверх от старта) / `get_server_config` (единый `HOST/API_KEY/MODEL`, профили local/remote убраны) / `get_stage_model` (`<СТАДИЯ>_MODEL` → общая `MODEL`) / `print_env_help` |
| лог | `setup_logging` / `log_argv` (фактическая команда запуска в лог) |
| модель | `determine_model` (только из аргумента/`.env`; авто через `GET /models` убрано — модель обязательна) |
| промпты | `load_prompt` (файл целиком) / `get_tagged_prompt` (теги) |
| чанкование | `split_text_smart` |
| текст/CJK | `get_ngrams` / `is_cjk` / `is_cjk_string` / `find_exact_match` |
| поиск терминов | `load_ner_data` + `find_relevant_ner` (+ `normalize_for_search`, `build_smart_regex`) |
| проверка глоссария (ner_check) | `filter_ner_items` / `format_ner_record` / `glossary_body` / `build_ner_batches` (count по убыванию, бюджет в СИМВОЛАХ) / `parse_ner_patches` / `review_entry` / `parse_review_doc` / `merge_review_entries` (review-файл: поля английские — `stage`/`status`/`applied`/`old`/`new`, статусы принять/отклонить, накопление) / `apply_ner_patches` (status + applied, дубли термина по совпавшему `old`) |
| проверка перевода LLM (translate_check_llm) | `fix_entry` (ошибка LLM → запись review) / `merge_fix_entries` (накопление, дедуп по chapter+old+new) / `apply_fix_to_text` (NFC, первое вхождение) |
| имена по полу | `collect_gender_names` (polish: поиск по `translation`, пол по наличию `(female)`/`(male)` в `type`) |
| запрос к LLM | **ТОЛЬКО** `stream_chat_completion` — единая гигиена стрима ([DONE]/finish_reason, loop-детект, cut, empty, min_len_ratio) → `(text, err)` |
| запись файла | `atomic_write` (tmp + fsync + os.replace) |
| чтение | `read_text_safe` (utf-8 → cp1251 fallback) |
| прогресс web | `web_progress_enabled` (флаг `WEB_PROGRESS=1`) / `emit_progress` (done, total, label → `@@PROGRESS@@` + JSON; только в web-режиме, no-op в CLI) |
| главы | `parse_chapter_id` / `build_chapter_map` / `find_chapter_file` / `format_ranges` / `compile_chapter_texts` (склейка `chapter.txt` из папок в один файл) |
| проекты | **ТОЛЬКО** `core/projects.py`: `DEFAULT_SECTIONS` (ACTIVE/HOLD/DONE, алиас `SECTIONS`) / `load_sections` / `save_sections` / `create_section` / `rename_section` (в существующий — перенос проектов) / `delete_section` (непустой — отказ) / `ensure_projects_root` / `valid_project_name` / `sanitize_project_name` / `list_projects` / `project_stats` / `project_progress_table` / `create_project` / `move_project` / `rename_project` / `copy_project` / `delete_project` / `list_template_sets` / `TEMPLATE_SKELETON` (`prompts`+`source`) / `_ensure_template_skeleton` (идемпотентный ремонт скелета) / `create_template_set` (каркас prompts/+source/) / `create_template_dir` (всегда ошибка — каталоги неизменяемы) / `copy_template_set` / `delete_template_set` / `templates_files` (пустые каталоги как `path/`) / `read_template_file` / `write_template_file` / `delete_template_file` (каталог → ошибка; `str \| None`) / `template_file_info` / `move_template_file` (только файлы; каталог → ошибка) / `fill_project_from_template` / `render_metadata` / `write_project_metadata` |

Запрещено: свои парсеры .env, свои стрим-обработчики SSE, свои парсеры имён
главных папок. Добавил функцию в таблицу — обнови и `core/README.md`,
и `tests/test_docs.py` (сверка доков с кодом).

## 7. Ключевые конвенции

### Конфигурация: системный корневой .env

Весь серверный конфиг — в корневом `.env` репо (LLM-профили, модели по
стадиям, дефолты пайплайна, `WEB_*`, настройки внешнего вида);
проектный `pdir/.env` приоритетнее для конкретной книги. Приоритет:
`CLI-флаг` > `os.environ` > `.env` > встроенный дефолт. Без .env скрипт
обязан работать дальше с ручным вводом — не падать. Настройки внешнего
вида (`WEB_UI_THEME`, `WEB_EDITOR_THEME`, `WEB_EDITOR_FONT_SIZE`) —
системные, в проекты не копируются; остальные UI-предпочтения (кегль
предпросмотра, авто-обновление) — в localStorage браузера, НЕ в .env.

### Канон глав

Имена папок парсятся ТОЛЬКО через `parse_chapter_id` (00000_1_…, 000001_…,
1_x, числа…). Поиск файла главы — только через `find_chapter_file`
(приоритеты: точные имена → подстрока типа → единственный безопасный txt;
blacklist: raw/draft/translated/original/source/backup).
Там, где дубли файлов = катастрофа, передавай `strict=True`.

### Unicode

Везде, где сравнивается/заменяется русский текст — NFC-нормализация
(`unicodedata.normalize("NFC", …)`): поиск фрагментов, fix-скрипты,
замена строк. Кавычки «»/", тире —–-, многоточия …/... считаются разными.

### JSON-файлы данных (правило консистентности)

Названия полей в JSON-файлах данных (review-файлы ner_review.json /
translate_check_llm_review.json и т.п.) — ПО УМОЛЧАНИЮ на английском:
`entries`, `status`, `applied`, `reason`, `stage`, `chapter`, `file`,
`type`, `term`, `field`, `old`, `new`, `created`, `updated` … Значения
(статусы «принять»/«отклонить», тексты ошибок, логи) остаются русскими.
Новые JSON-ключи писать только на английском; переименование — жёсткое,
без fallback-чтения старых ключей (совместимость не сохраняем).

### Промпты

- Внешние промпты хранятся в `prompts/` проекта; формат — теги:
  `<translate>/<redact>/<polish>`, `<pass1>/<pass2>`, `<prompt_pass1/2>`,
  `<prompt_wiki_article>`; файл БЕЗ тегов = промпт этапа целиком
  (допустимый режим «отдельный файл на этап», не legacy).
- Встроенные промпты в скриптах (DEFAULT_*/PASS1_PROMPT) — только fallback.
  Меняя встроенный промпт, синхронизируй смысл с внешним шаблоном, если есть.
- Плейсхолдеры: `{ner_block}`, `{original_text}`, `{translated_text}`,
  `{female_names}`, `{male_names}` (polish: имена из ner.json по полю
  `translation`, пол по наличию `(female)`/`(male)` в `type`).

### Логирование

- Проектные логи: `logs/`; логи стадий по главам: `logs/chapters/`.
- `setup_logging` заменяет расширение выходного файла на `.log`.
- Каждый скрипт после `setup_logging` вызывает `log_argv(logger)` — в лог
  пишется фактическая команда запуска (shlex.join(sys.argv)).

### Артефакты стадий (НЕ менять имена)

`chapter.txt → translated.txt (+translated_trace.json) → redacted.txt →
polished.txt`. Trace-JSON — мост translate→redact (пары original/translated);
polish trace НЕ пишет. `_STAGE_IO` в `web/pipeline.py` — фиксирован.

## 8. Запреты

- Не менять имена артефактов стадий и канон `parse_chapter_id` без миграции
  всех потребителей и тестов.
- Не коммитить `projects/`, `servers/`, `Images/`, `backup/`, `__pycache__/`
  и корневой `.env` (уже в .gitignore — не обходи).
- Не менять единицы измерения параметров (символы ↔ токены) «для красоты».
- Не убирать fail-fast в web/pipeline.py (returncode 0 + непустой выходной
  файл + grep слов-ошибок).
- Не читай файлы с приватными SSH ключами.
- Не возвращай cli/tui и `backends/` — интерфейс web-only;
  исторический документ AUDIT.md удалён, его выводы учтены.
- НЕ вводи настройку слов-ошибок пайплайна (M7 отменён): текст
  перевода НЕ попадает в stdout скриптов (только прогресс/ошибки) — жёсткий
  `_ERROR_RE` в web/pipeline.py ловит реальные сбои; настройка = регресс.

## 8а. pi-lens (настройки шума)

- `~/.pi-lens/config.json` (глобально, вне репо): `tests.enabled: false` —
  встроенный тест-раннер жёстко зовёт `python` (в системе только `python3`,
  ENOENT-шум); тесты гоним вручную `python3 -m pytest tests/ -q`.
- `.pi-lens.json` (в репо): `format.enabled: false` — НЕ переформатировать
  файлы автоматически (перекраивает весь файл, шум в диффах);
  `rules.jscpd.disable: ["duplicate"]` — без дубликатов-предупреждений;
  `ignore` — projects/, servers/, Images/, backup/, __pycache__/, .venv/.
- Находки pi-lens — подсказки, не истина: перед реакцией проверяй
  фактическое состояние (grep / node --check / pytest). Устаревший кэш
  диспатч-пайплайна повторяет старые находки (например, «exportModal
  unused» после переноса функции) — снимать через `lens_diagnostic_mark`
  false-positive, реальная проверка — `python3 -m pytest` + `node --check`.

## 9. Как вносить изменения

0. Планы и статусы — в `TODO.md`: перед задачей сверься с текущими
   статусами; завершил майлстоун — обнови статус в `TODO.md` в том же
   коммите. Планы в отдельные файлы не выноси (web_plan.md упразднён).
   **Отметки и коммиты — по мере выполнения, а не в самом конце**:
   сделал задачу (или её законченную часть) — сразу отметь чекбокс
   в `TODO.md`, закоммить и запушь. Не копи в конце сессии всё разом;
   незапушенный коммит — не завершённая работа.
1. Общая логика → `core/common.py` (+ запись в `core/README.md` и при
   необходимости в таблицу §6 / `tests/test_docs.py`).
2. Новый исполнитель → `scripts/xxx.py` (CLI, argparse, bootstrap §4).
3. Новая стадия в web → строка в `web/stages.py::STAGE_SPECS` (ключ-слаг,
   title, script, build-функция, fields) + форма в SPA.
4. Новый роут API → `web/api.py` (+ строка в таблицу `web/README.md`).
5. Новая функция web-слоя → `web/*.py`, общая логика — только из `core/`
   (не копировать из скриптов).
6. Тесты → `tests/` (см. §10). Для новых функций core — параметризованные
   тесты обязательны. Добавил функцию в таблицу §6 — обнови и тесты,
   и `tests/test_docs.py`.
7. **Перед коммитом — обязательно:**
   - прогони `python3 -m pytest tests/ -q` — все тесты должны быть
     зелёными. Коммит с падающими тестами запрещён;
   - если менял код `core/`, `scripts/`, `web/`, `run.py` — проверь,
     что существующие тесты это покрывают; не покрывают — добавь/обнови
     тесты в том же коммите;
   - затем smoke-запуск затронутого скрипта с `--help` (и `--dry-run`,
     если поддерживается);
   - **закоммить и запушь изменения на GitHub** (`git add -A` →
     `git commit` → `git push origin`). Незапушенный коммит — не
     завершённая работа.
   Тесты НЕ должны ходить в сеть: LLM только мокать (monkeypatch на
   `stream_chat_completion` / `requests.post`), данные — во временных
   папках pytest (`tmp_path`).
8. **Стандарт коммитов**: `<тип>(<область>): <описание на
   русском>`; типы — `feat`/`fix`/`refactor`/`docs`/`test`/`chore`;
   области — `core`/`scripts`/`web`/`templates`/`tests`/`docs`/`repo`.
   Описание — инфинитив, до ~72 символов; одно логическое изменение —
   один коммит. Если в одном файле смешаны правки из разных задач
   (например, докстринг + фича), файл можно коммитить целиком в коммит
   основной задачи — не нужно вырезать хунки по-атомному.
   Первый коммит истории: `chore(repo): initial commit —
   NovelMaestro`.

Стиль кода: русский в строках/логах/UI; компактные функции; docstring на
каждую публичную функцию core; секции в core/common.py разделены
комментариями `# ══…`.

## 10. Быстрая проверка среды

```bash
python3 -m pytest tests/ -q                      # тесты (обязательно перед коммитом)
python3 -m pytest tests/ -q --cov=core --cov=scripts --cov=web  # покрытие (нужен pytest-cov)
python3 run.py                                    # web-интерфейс (сервер + браузер)
python3 web/main.py --help                        # флаги сервера
python3 scripts/translate_book.py --help          # единый LLM-скрипт
python3 scripts/translate_check_llm.py --help   # проверка перевода LLM
ls projects/ACTIVE/*/chapters | head              # данные реального проекта
# публикация изменений (обязательно):
git add -A && git commit -m "…" && git push origin
```

Карта тестов `tests/` (принцип: один модуль — один файл тестов):

- `tests/conftest.py` — общие хелперы (SilentLog, make_ru_chapter_file,
  feed, fake_env);
- `tests/test_core_common.py` — `core/common.py` целиком (стрим SSE
  моками, .env, чанкование, NER-поиск, имена по полу, канон глав);
- `tests/test_projects_core.py` — `core/projects.py` (создание/перенос/
  переименование, tmp_path);
- `tests/test_run_flows.py` — `run.py` (bootstrap, лаунчер web);
- по одному файлу на скрипт: `tests/test_translate_book.py`,
  `tests/test_ner.py`, `tests/test_ner_check.py`,
  `tests/test_translate_check_llm.py`, `tests/test_wiki.py`,
  `tests/test_epub_to_chapters.py`, `tests/test_translate_check.py` —
  чистые функции + оркестраторы (`run_two_pass`, `run_wiki_generation`)
  и `main()` с моками LLM;
- `tests/test_scripts_units.py` / `tests/test_scripts_e2e.py` — чистые
  функции и прогоны `main()` остальных `scripts/` без сети
  (batch_replace, clean_and_compile, translate_check и др.);
- `tests/test_web_pipeline.py` — web-оркестратор `web/pipeline.py`
  (Tracker, build_stage_cmd, grep_errors, process_chapter, main);
- `tests/test_web_api.py` / `tests/test_web_jobs.py` /
  `tests/test_web_m7.py` / `tests/test_web_server.py` /
  `tests/test_web_sandbox.py` — web-слой (роуты, JobManager, SSE,
  env-редактор, NER-экспорт) на реальном HTTP-сервере без сети;
- `tests/test_docs.py` — сверка доков (`core/README.md`, AGENTS.md §6)
  с кодом;
- `tests/test_architecture.py` — регресс-гарды архитектуры (§3: запрет
  `input()` и UI-импортов в `scripts/`, единый стрим, bootstrap,
  web-раскладка, run.py — лаунчер web, отсутствие backends/cli|tui).

## 11. Правила коммитов

Коммиты строго атомарны (одно логическое изменение — один коммит) и оформляются по Conventional Commits: `<type>(<scope>): краткое описание на русском`. Допустимые `type`: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`. `scope` — короткий латинский модуль (`web`, `api`, `pipeline`, `ner`, `core`, `docs`). Описание до 72 символов, без точки в конце; детали, списки изменений и ссылки на задачи выносятся в тело коммита.

Категорически запрещены произвольные префиксы и неинформативные сообщения: `W*`, `M*`, `TODO:`, `Шаблоны:`, `фиксы`, `правки`, CAPS и эмодзи. Запрещено смешивать несвязанные правки (например, новую фичу и обновление статусов в `TODO.md`) в одном коммите. Эталонные примеры: `feat(run): добавлен прогресс запусков`, `fix(web): убран кегль 11 из предпросмотра`, `docs(todo): обновлён статус задач`.

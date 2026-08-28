# TODO — Переработка «epub · Разбор исходника на главы» (CLI + web)

Статус: **готово**. Полная переработка стадии `epub`: новые режимы,
каноничные имена каталогов с настраиваемой длиной, предпросмотр с
удалением/перенумерацией, автосохранение настроек.

## Исследование (факты, на которых строится план)

- Канон `parse_chapter_id` (core/common.py): папка `<нули>_<номер>_<заголовок>`,
  нули добивают ширину 6 — `00000_1`, `0000_85`, `000_177`, `0_12345`.
  Текущий `write_section` уже так делает (`zeros = 6 - len(counter)`).
  Смещение 875 → первая папка `000_875_…` (ширина сохраняется).
- Текущий скрипт (1044 строки): пресеты языков zh/en/ru, `Patterns`,
  `process_archive`/`process_txt`/`split_and_write`, `--lang`, автопоиск
  в `source/`, `--clean/--remove-pages/--book-title/--move-done`.
- Web: `web/stages.py::build_epub_to_chapters` + спека (input/lang/polished/
  clean/remove_pages/chapter_re/book_title/chunk_size/clean_output/dry_run);
  SPA — generic-форма (buildField), пресет простого режима `{input, lang}`.
- R9: настройки запусков пишутся в `.env` проекта при запуске; предзаполнение
  из `.env` в `_stage_spec`. Многострочные regexp-поля в .env не пишем
  (перевод строк → пробелы ломает «по одному на строку»).
- UI-предпочтения — localStorage (AGENTS §7); автосохранение настроек
  epub-формы делаем в localStorage по проекту.
- Предпросмотр: CLI-режим `--preview-json tmp/epub_preview.json` (секции +
  тексты), API читает JSON; удаление/перенумерация — правка JSON на сервере;
  запуск получает `--skip` из предпросмотра (удаления переносятся в реальный
  разбор).

## План

### 1. CLI — `cli/epub_to_chapters.py` (полная переработка)

- [x] Три режима `--mode`: `toc` (по структуре epub/zip: spine/TOC/h1-h2,
      дубль заголовка главы в тексте удаляется), `regex` (строки-маркеры,
      `--split-re` по одному на строку), `chunk` (`--chunk-size`, СИМВОЛЫ;
      название — по маске `--chunk-mask`, обязателен `{num}`).
- [x] Вход только .epub/.zip/.txt; `--input` обязателен (автопоиск убран);
      TOC-режим отклоняет txt; regex/chunk принимают txt и epub/zip.
- [x] Пресеты языков удалены (LANG_PRESETS/Patterns/--lang и все
      языковые паттерны).
- [x] Очистки: `--clean-re` (append, по одному на строку) — удаляет все
      совпадения из текста (MULTILINE — якоря ^/$ работают построчно).
- [x] Имена каталогов: `safe_folder` (недопустимые символы Windows+Linux,
      пробелы → `_`, зарезервированные имена, точки/пробелы в конце),
      `--title-limit` (СИМВОЛЫ, дефолт 50), первая строка файла — заголовок.
- [x] `--num-offset` (первый номер, дефолт 1), `--skip N` (append) —
      пропуск секции по исходному seq с перенумерацией.
- [x] `--preview-json PATH` — вместо записи пишет JSON
      `{source, num_offset, title_limit, entries: [{seq, num, folder, heading, text}]}`.
- [x] `--polished`, `--clean-output`, `--dry-run`, `--report`, сверка
      ДО/ПОСЛЕ — сохранить.

### 2. Web-спека — `web/stages.py`

- [x] Новые поля: input (files, source, epub/zip/txt), mode (select toc/regex/
      chunk), split_patterns (textarea, noenv), chunk_size (СИМВОЛЫ),
      chunk_mask («Глава {num}»), clean_patterns (textarea, noenv),
      title_limit (СИМВОЛЫ, 50), num_offset (1), polished, clean_output.
- [x] `build_epub_to_chapters`: --mode всегда; split/clean — по строкам
      (append); chunk-поля только в chunk-режиме; skip — списком.
- [x] `autosave: True` в спеке; noenv-поля не пишутся/не читаются из .env
      (`_persist_run_params`, `_stage_spec` в api.py).

### 3. API предпросмотра — `web/api.py`

- [x] `POST /api/stages/epub/preview` {params, skip} — subprocess скрипта с
      `--preview-json tmp/epub_preview.json`, ответ — summary (папки+размеры).
- [x] `GET /api/stages/epub/preview` — summary из JSON.
- [x] `GET /api/stages/epub/preview/text?num=` — текст главы.
- [x] `DELETE /api/stages/epub/preview/folder?seq=` — удаление секции +
      перенумерация (префикс ширины 6 от num_offset).

### 4. SPA — `web/static/run-views.js` + styles.css

- [x] textarea-тип поля; regexp-справка (сворачиваемая, с примерами) —
      экспертный режим.
- [x] Автосохранение настроек epub-формы в localStorage (по проекту),
      загрузка поверх .env/дефолтов.
- [x] Динамическая фильтрация исходника по режиму: toc — epub/zip,
      regex/chunk — +txt; простой режим — epub/zip; «Запустить»/предпросмотр
      без исходника — ошибка.
- [x] Панель предпросмотра (оба режима): кнопка «Предпросмотр»,
      список каталогов + размер КБ + удаление с перенумерацией, выбор главы
      и текст; правка настроек — предпросмотр устаревает.
- [x] Запуск переносит skip-список предпросмотра в params.

### 5. Тесты

- [x] `tests/test_epub_to_chapters.py` — полная переработка: toc/regex/chunk,
      safe_folder/folder_name (ширина 6, лимит 50), offset, skip+перенумерация,
      preview_json, main (required input, bad suffix, clean-output, preview).
- [x] `tests/test_cli_units.py` — e2c-тесты под новые сигнатуры.
- [x] `tests/test_web_jobs.py` — build-тесты (mode/split-re/clean-re/skip/
      chunk), simple = [input], preset spot-checks.
- [x] `tests/test_web_api.py` — preview-флоу: создание, текст, удаление+
      перенумерация, offset, ошибки (txt в toc, 404).

### 6. Доки и коммит

- [x] `web/README.md` — описание стадии epub, API-таблица (preview-роуты).
- [ ] `python3 -m pytest tests/ -q` зелёные; `node --check` run-views;
      smoke `--help`; коммит+пуш.

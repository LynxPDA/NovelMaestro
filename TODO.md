# TODO — Запуски: Wiki (LLM), Компиляция TXT/EPUB/FB2, Проекты: «Главы»

Статус: **реализовано**. Три группы фич — в одном цикле (коммиты
`feat(wiki)`, `feat(compile)`, `feat(api)` …). Невыполненное — ниже.

---

## 6. Интерактивный разбор исходника («Запуски → Разобрать исходник») — 🚧 в работе

Экран «Разбор»: наглядный сплит epub/zip/txt до записи — точки разрыва,
заголовки, чистки, диапазон, целевой слот; план = source/split_plan.json.
Кликовые действия прямо в тексте (read-only виртуализированный просмотрщик).

### Фаза 1 — ядро + API + верхняя панель + список секций

- [ ] `core/split.py`: перенос логики из `cli/epub_to_chapters.py`
      (пресеты, Patterns, cleanings, write_section, archive/txt) +
      `parse_source` (без записи), `compute_folders` (plan → папки +
      статистика, БЕЗ записи), `apply_plan` (запись, один путь с
      compute_folders); `_safe_compile` кидает ValueError (не sys.exit —
      web-сервер не должен падать), CLI ловит как раньше.
- [ ] Канонные слоты при записи: chapter/translated/redacted/polished.txt
      (легаси `chapter{N}_polished.txt` — только в старом флаге
      `--polished`, его не трогаем).
- [ ] `cli/epub_to_chapters.py` — тонкая обёртка: re-export имён для
      тестов, `--plan plan.json`; старые флаги и поведение неизменны
      (регрессия S7: tests/test_epub_to_chapters.py зелёный).
- [ ] API: `GET /api/split/parse`, `POST /api/split/preview`,
      `POST /api/split/apply` (пишет главы + сохраняет план), `GET
      /api/split/plan`; песочница resolve_path, лимит размера.
- [ ] SPA: экран «Разбор» (`#/split/{sec}/{name}`, split-views.js),
      кнопка на карточке стадии epub; верхняя панель (файл, кодировка/
      язык, пресет + свой regex со счётчиком, диапазон, чистки, слот),
      список секций (rename/exclude, предпросмотр имён папок, «Глав: X,
      будет записано: Y»), таблица предпросмотра, apply с режимами
      коллизий (сдвинуть/перезаписать/пропустить).
- [ ] Тесты: tests/test_split_core.py (parse/preview/apply, tmp_path,
      без сети), API-тесты в test_web_api.py; доки (AGENTS.md §3/§6,
      core/README.md, web/README.md, test_docs.py).

### Фаза 2 — текст и указание (read-only)

- [ ] Виртуализированный просмотрщик строк (свой, без зависимостей).
- [ ] Подсветка: границы секций, строки-заголовки, удаляемые строки.
- [ ] Клик по строке: «разделить здесь», «удалить эту строку»,
      «удалить все похожие» (паттерн с автозаменой цифр на \d+, живой
      счётчик), «восстановить»; «сбросить все коррекции».
- [ ] «Разбить по TOC» (epub/zip): заголовки секций из TOC.
- [ ] Приёмка S1–S7 на cradle/tom_8/source/source.txt (0.85 МБ, без
      маркеров).

### Фаза 3 — опционально (после S1–S7)

- [ ] Правка секции (textarea).
- [ ] Переиспользование плана на другой файл того же формата;
      diff «до/после» чисток.

---

## 1. Запуски — Создание Wiki (LLM) — ✅ выполнено

### 1.1 Источник текста: «Готовый txt» ИЛИ «Собрать из глав»

- `cli/wiki.py`: `file` — опциональный (`nargs="?"`); добавлены
  `--compile-chapters` (склейка `chapters/*` в память, как в Создании
  глоссария), `--type` (chapter/translated/redacted/polished),
  `--start`/`--end` (диапазон, ГЛАВЫ); нет глав — ранний выход.
- `web/stages.py::build_wiki`: `source` select (txt/chapters) →
  `--compile-chapters` + тип/диапазон, либо входной файл.
- SPA: переключатель источника прячет `file` (эксперт) / `type` (простой);
  диапазон глав — как у ner.
- Тесты: `test_main_compile_chapters`, `test_main_compile_chapters_missing`,
  `test_main_no_file_no_compile`, `test_build_wiki_compile_chapters`.

### 1.2 Оглавление и якоря-ссылки (обычный режим)

- `cli/wiki.py`: `--toc`/`--no-toc`, `--toc-links`/`--no-toc-links`;
  оглавление и якоря `<a id="slug">` — только в обычном Markdown;
  Rulate — всегда без содержания.
- `web/stages.py`: `toc`/`toc_links` (bool, дефолт True) → флаги;
  SPA прячет их при формате ≠ md.
- Тесты: `test_assemble_toc_toggles`, `test_build_wiki_toc_off`,
  `test_main_toc_off`.

### 1.3 Rulate HTML (вместо Markdown)

- `cli/wiki.py`: `--rulate-html`, вывод по умолчанию `wiki.txt`
  (HTML-разметка внутри txt, как просил пользователь);
- `cli/wiki.py`: `--as-chapter` — вики как дополнительная последняя
  глава `chapters/00000_{N+1}_Wiki_Новеллы/` (один файл выбранного
  типа: `--save-type` translated/redacted/polished, по умолчанию
  polished; chapter.txt не пишется), название «Wiki Новеллы» простым
  текстом, статьи в rulate-стиле (заголовки глубже); компилятор берёт заголовок главы из
  первой непустой строки, когда нет «Глава N» — вики-глава попадает в
  TOC epub/fb2, `###`/`####` остаются текстом и TOC не ломают;
  `md_to_html`: `##` → `<p><strong><span style="font-size:20px">…`,
  `###` → 16px, списки → `<ul>`, `---` → `<hr />` (и межстатейный
  разделитель — `<hr />`), экранирование HTML.
- `web/stages.py`: `format` select (md/rulate-md/rulate-html) →
  `--rulate-mode`/`--rulate-html`; старый чекбокс `rulate_mode` убран.
- Тесты: `test_assemble_rulate_html`, `test_md_to_html_escaping`,
  `test_slugify`, `test_main_rulate_html`, `test_build_wiki_rulate_html`.

---

## 2. Запуски — Компиляция TXT/EPUB/FB2 — ✅ выполнено

- Убрано поле «Папка для compiled_*/book_*» (`tmp_dir`) из формы и
  `--tmp-dir` из сборки argv (комментарий в stages.py).
- Чекбоксы — СЛЕВА от текста (`label.field-check` + CSS): checkbox + label
  в строку во всех полях (не только компиляция).
- «Файл страницы поддержки» — автозаполнение `source/donate.txt` при
  автодетекте (autofile; баг был в web/main.py: игнорировался
  `--projects-dir` — исправлен на `projects_root=projects_root`).
- Переименования: «Без страницы поддержки (--no-donate)» → «Без страницы
  поддержки»; «Глав в части для *-chunks» → «Глав в части» (labels
  режимов — TXT/EPUB/FB2 частями).
- Режим `titles` выпилен: из CLI (`export_titles` удалён, choice убран) и
  из формы; legacy-файлы titles по-прежнему читаются (совместимость).
- Тесты: `test_cac_titles_mode_removed`, compile-спека в
  `test_simple_fields_per_stage`, smoke `--help`.

---

## 3. Проекты — вкладка «Главы» — ✅ выполнено

- Вкладка «Главы» между «Проверка» и «Статус» (`project-views.js`).
- Дропдаун типа файлов (chapters/translated/redacted/polished),
  список строк: слева номер главы, справа редактируемое поле
  (первая непустая строка файла), одна кнопка «Сохранить».
- `core/common.py`: `read_chapter_titles` / `write_chapter_titles`
  (первая непустая строка; замена с NFC, остальной текст и перевод
  строки сохраняются; `missing`/`warnings`).
- API: `GET/PUT /api/projects/{s}/{n}/chapters/titles` (type,
  `{titles: {номер: строка}}`).
- Тесты: `test_read_chapter_titles`, `test_write_chapter_titles`,
  `test_project_chapters_titles` (web API); доки — AGENTS.md §6,
  core/README.md, web/README.md, test_docs.py.

---

## 4. Сопутствующие правки

- `web/main.py`: фикс `--projects-dir` (сервер смотрел в репозиторный
  `projects/` вместо переданного каталога) — причина «невидимых»
  autofile-файлов в эмпирических тестах.
- `tests/test_web_jobs.py`: `rulate_mode` → `format`/`toc`/`toc_links`,
  wiki simple-поля, новые build-тесты.
- `cli/wiki.py`/`cli/clean_and_compile.py` — smoke-прогоны `--help`,
  wiki e2e на стаб-LLM (SSE): compile-chapters, rulate-md, rulate-html.

---

## 5. Не делал (вне объёма)

- **U4. Разбить project-views.js** — ⏸️ отдельная задача.
- **U12. Согласованность matcher JS↔Python** — ⏸️ отдельная задача.
- **B7. Портабельный зомби-детект** — ⏸️ (см. прежний TODO).

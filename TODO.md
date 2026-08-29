# TODO — UI-полировка запусков и конфига (раунд UI)

Статус: **готово (раунд 2 правок по ревью)**.

## Техническое задание

1. **Проекты → Конфиг**: убрать карточку «Обложки и метаданные
   (варианты по умолчанию)» (выбор дефолтов — в Запусках/Компиляции);
   «Обложка (source/)» → «Обложка» с выбором/предпросмотром файлов
   source/; «source/metadata.yaml» → «Метаданные epub/fb2» с выбором;
   «source/donate.txt» → «Файл страницы поддержки» с выбором.
2. **Запуски → compile**: режим «TXT» → «TXT (Rulate)» + новый «TXT»
   без rulate-форматирования заголовков (`# [...]`); donate-файл —
   выпадающим списком; чекбоксы «Без страницы поддержки»/«Без обложки
   в FB2» убрать (пусто = не подхватывать); обложка epub/fb2 — единым
   полем; предпросмотр обложки/metadata.yaml/страницы поддержки.
3. **Запуски → epub**: «Удалить» → чекбоксы (по умолчанию все
   включены, снятие = пропуск с перенумерацией); «Ручная (regexp)» →
   «Ручной (regexp)»; маска по умолчанию «Chapter {num}»; чекбокс
   «переопределить названия глав маской» (по умолчанию выкл);
   «Очистки текста» и «Замены» слить (пустая правая часть = удаление);
   отключать неактуальные поля по режиму.
4. **Запуски → ner_check**: карточки-пресеты убрать (названия — в
   select «Проходы»); поле «Типы через запятую» убрать (есть чипсы).
5. **Запуски → ner**: «С нуля (новый глоссарий)» → «Новый глоссарий
   (автоматический)», «Дообучение (глоссарий уже есть)» →
   «Дообучение», режим «Собрать главы + извлечение» удалить. Логика:
   выбран входной файл — работаем с ним; не выбран — сборка глав в
   память (диапазон, как сейчас по умолчанию).

## План

### 1. Конфиг (web/static/project-views.js, configView)

* [x] Убрать mediaCard «Обложки и метаданные (варианты по умолчанию)»
      и его обработчики (loadMedia/mediaSelects/MEDIA_KEYS).
* [x] «Обложка»: select изображений из source/ (опции стадии compile) +
      предпросмотр выбранного; upload/delete — как сейчас.
* [x] «Метаданные epub/fb2»: select yaml из source/ + редактор/просмотр
      выбранного файла; сохранение/«Из шаблона» — по выбранному пути.
* [x] «Файл страницы поддержки»: select txt из source/ + редактор/
      просмотр; сохранение/«Из шаблона».

### 2. Компиляция (web/stages.py + cli/clean_and_compile.py + run-views.js)

* [x] CLI: режим `txt-plain` — заголовки `# {title}` без `[:|:]`-суффикса
      и скобок; `--no-cover` (нет обложки ни в EPUB, ни в FB2).
* [x] web/stages.py compile: mode-лейблы TXT (Rulate)/TXT; единое поле
      `cover` (files source/, jpg/png/webp/gif/bmp); `donate_file` —
      files source/ .txt без autofile; чекбоксы no_donate/no_fb2_cover
      убраны; build: cover → --epub-cover + --fb2-cover, пусто →
      --no-cover; donate_file → --donate-file, пусто → --no-donate.
* [x] run-views.js compile: предпросмотр обложки (img), metadata.yaml и
      donate (текст) под формой; поля cover/meta/donate скрыты в
      txt-режимах.

### 3. Разбор исходника (web/stages.py + cli/epub_to_chapters.py + run-views.js)

* [x] CLI: `--rename-chapters` (заголовки по маске), дефолт маски
      «Chapter {num}».
* [x] web/stages.py epub: лейбл «Ручной (regexp)»; chunk_mask default
      «Chapter {num}», label «Маска названия глав»; чекбокс
      `rename_chapters` (default false); поле clean_patterns убрано
      (replace_patterns покрывает удаление пустой правой частью).
* [x] run-views.js epub: чекбоксы вместо «Удалить» в предпросмотре
      (снятие → skip, перезапуск предпросмотра с перенумерацией);
      видимость полей по режиму: маска — chunk или rename;
      rename — не chunk; replace_patterns — все режимы.

### 4. Проверка глоссария (web/stages.py + run-views.js)

* [x] passes: лейблы «Полный цикл», «Весь список (этап 1)», «По типам
      (этап 2)»; карточки nerCheckWidgets удалить.
* [x] Поле `types` из спеки убрать; чипсы типов — в обоих режимах.

### 5. Создание глоссария (web/stages.py + run-views.js)

* [x] ner: лейблы «Новый глоссарий (автоматический)»/«Дообучение»;
      опция compile удалена; help обновлён.
* [x] build_ner: file → позиционный аргумент; нет file →
      --compile_chapters + start/end; постобработка — как есть.
* [x] run-views.js ner: диапазон глав виден, когда входной файл не
      выбран; в постобработке LLM-поля/файл/диапазон скрыты; пресет
      простого режима — «Новый глоссарий» (extract), simple += file.

### 6. Тесты и доки

* [x] tests/test_web_jobs.py: build_epub (без clean-re, rename),
      build_clean_and_compile (cover/donate), спеки compile/ner/ner_check,
      preset-споты (ner mode=extract), simple-поля.
* [x] tests/test_cli_units.py: split_input rename_chapters.
* [x] tests/test_cli_e2e.py: compile txt-plain без `[:|:]`.
* [x] web/README.md: конфиг, compile, epub, ner, ner_check.
* [x] Прогон `python3 -m pytest tests/ -q`, node --check, smoke --help.
* [x] Коммит+пуш по Conventional Commits.

## Раунд 2 (правки по ревью)

### R2-1. txt-plain: заголовки как в переводе

* [x] clean_and_compile.py: `txt-plain` — заголовок БЕЗ `#`-префикса
      (только очистка и компиляция); доки/help обновлены.

### R2-2. epub: чекбоксы без перезапуска предпросмотра

* [x] run-views.js: снятие галочки НЕ перезапускает предпросмотр —
      строка остаётся с отжатым чекбоксом (наглядность);
      перенумерация — при реальном запуске.
* [x] Кэш настроек epub версионирован (EPUB_SAVE_V=2): старый
      «Глава {num}» больше не перекрывает дефолт «Chapter {num}».
* [x] БАГ CSS: `.hidden` проигрывал `.field` (display:flex идёт позже
      при равной специфичности) — скрытие полей по режиму НЕ работало;
      исправлено `display: none !important`.

### R2-3. ner_check: «Режимы» вместо «Проходы»

* [x] Режимы: «Выбранные типы (одновременно)» (whole, по умолчанию)
      и «Выбранные типы (по очереди)» (types); «Полный цикл» (all)
      убран из CLI и формы; auto-apply — whole → применение.

### R2-4. batch_replace: .bak не создаётся (как и раньше)

* [x] Проверено: batch_replace пишет через atomic_write, .bak нет —
      изменений не требуется.

### R2-5. Доки и сборки

* [x] help.md: epub/ner/ner_check/compile — актуализированы.
* [x] web/README.md: ner_check «Режимы».
* [x] Dockerfile/docker-compose/workflows — актуальны
      (проверено: пути и зависимости совпадают с репозиторием).
* [x] Тесты: test_cac_compile_txt_plain (без `#`), ner_check whole/types
      (all отклоняется), 943 зелёных; node --check; smoke --help.

## Раунд 3: единый диапазон глав + Windows-сборка

### R3-1. Единая строка диапазона в форме запусков

* [x] Диапазон «Главы: [start] – [end]» — одна строка в простом И
      экспертном режимах (buildRangeRow); start/end из спека отдельными
      полями не рисуются.
* [x] Строка диапазона — ПЕРВОЙ в списке (все 7 стадий с start/end:
      ner, wiki, pipeline, translate_check, translate_check_llm,
      compile, batch_replace).
* [x] Простой режим: files-поля брали row-обёртку вместо select —
      условная видимость (ner файл/диапазон, wiki источник) не
      работала; исправлено (_sel || _input), оба режима согласованы.
* [x] number-поля без стрелок «больше/меньше» (CSS appearance).
* [x] Коммит b16f254 (fix(web): единый диапазон глав в форме запусков).

### R3-2. Windows-сборка: кодировка .ps1

* [x] Причина падения: UTF-8 БЕЗ BOM — Windows PowerShell 5.1 (дефолт
      windows-latest) парсит такие файлы как ANSI → кириллица ломает
      парсер (лог: Ñ€ÑƒÑÑÐºÐ¸Ð¹, missing closing '}').
* [x] Фикс: BOM добавлен в packaging/build_portable_windows.ps1.
* [x] Проверка: других .ps1 в репо нет; инвокация workflow корректна.

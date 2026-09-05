# core/common.py (stdlib + requests; опц. pyahocorasick) — для заимствований

.env: parse_dotenv / find_env_file (системный корневой .env, вверх от
  старта: из папки книги — её pdir/.env, из корня — корневой .env) /
  env_overlay (копия словаря .env, где перечисленные ключи перекрыты
  непустыми значениями os.environ — канон «окружение > файл»;
  для чтения дефолтов из файла с приоритетом переменных деплоя) /
  system_env_file (системный .env — дефолты всех проектов:
  WEB_ENV_FILE — в Docker это projects/.env в постоянном томе,
  правки вкладки «Настройки» переживают обновление образа; иначе
  find_env_file) /
  get_server_config (HOST/API_KEY/MODEL; стадия непуста —
  СТАДИЯ_HOST/API_KEY/MODEL → общие; os.environ приоритетнее файла
  .env — в Docker конфиг приходит переменными окружения) /
  get_stage_model (СТАДИЯ_MODEL → общая MODEL, env > файл) / print_env_help
лог/модель: setup_logging / log_argv (фактическая команда запуска,
  значения --*api_key*/*token*/*secret* маскируются — M2) /
  determine_model (только из аргумента/`.env`; авто через GET /models убрано)
промпты: load_prompt / get_tagged_prompt
текст (СИМВОЛЫ): split_text_smart / get_ngrams / is_cjk / is_cjk_string /
  normalize_for_search / build_smart_regex / find_exact_match
правила замен «паттерн -> замена»: trim_rule_left / trim_rule_right
  (паддинг у «->»; значимые пробелы «^  » / «  $» и «\s+ -> ») /
  strip_rule_flags (флаги « |i»/« |r» в конце строки правила;
  разделитель — ровно один пробел: лишние пробелы остаются значимыми) /
  strip_line_comment (inline-комментарий « # …» в конце строки правила;
  «#» без пробела слева — не комментарий; строка с «#» в начале — "")
NER: load_ner_data / find_relevant_ner (поиск по term+aliases в оригинальном написании) /
  collect_gender_names (поиск имён по translation; пол по наличию (female)/(male) в type) /
  extract_term_context (контекст термина из чанка: предложение с термином,
  самое длинное из найденных; max_len — СИМВОЛЫ, 0 = выключено; границы —
  знаки конца любых языков (。！？.!?… и др.) + закрывающие кавычки/скобки;
  точных вхождений нет и threshold задан — нечёткий поиск по предложениям
  (n-граммное перекрытие, зеркало _fuzzy_hit); CJK — только точный)
ner_check: filter_ner_items (порог count + типы) / format_ner_record /
  glossary_body / build_ner_batches (count по убыванию, бюджет СИМВОЛЫ;
  fields — какие поля записи передавать LLM, None = все, term — всегда) /
  parse_rag_suggestions (текст LLM → список записей {term,<поле>,reason};
  fields — разрешённые поля, term — всегда) /
  ner_item_lookup (поиск записи по term: NFC, затем вариант без скобок
  【】「」『』()（）; нет — None) /
  diff_ner_records (записи LLM ↔ ner.json → сырые патчи
  {term,field,old,new,reason}: NFC, list/dict через json.dumps,
  нет записи — warning с близкими терминами) /
FTS5 (RAG/wiki): build_fts_index (текст → in-memory sqlite, нарезка
  по абзацам с fallback по chunk_size) / fts_escape / fts_search_all /
  fts_search_first / fts_search_ids_all (все — без BM25, в порядке
  текста, битый запрос → пусто) / even_sample (равномерная выборка:
  первый и последний всегда)
  review_entry / parse_review_doc / merge_review_entries (review-файл:
  поля английские: stage/status принять|отклонить/applied/old/new,
  накопление по этапам, дедуп по term+field+old+new) /
  apply_ner_patches (status + applied, дубли термина по совпавшему
  old, сверка old по NFC; list/dict-поля new — через json.loads)
translate_check_llm: fix_entry (ошибка LLM {chapter,fragment,corrected,type,reason}
  → запись review: stage/chapter/file/type/old/new/reason/status/applied, NFC) /
  merge_fix_entries (накопление без затирания, дедуп по chapter+old+new) /
  apply_fix_to_text (одна замена фрагмента, NFC, первое вхождение)
LLM: stream_chat_completion ([DONE]/finish_reason/loop/cut/empty/min_len_ratio) → (text, err);
  ретраи ТОЛЬКО 408/425/429/5xx + Retry-After/backoff+jitter (H3)
ФС: atomic_write / read_text_safe (utf-8 → cp1251 → gb18030 — B7)
прогресс (web): web_progress_enabled (флаг WEB_PROGRESS=1 ставит JobManager.start) /
  emit_progress (done, total, label → stdout-строка @@PROGRESS@@ + JSON,
  только в web-режиме; total=None/0 — неопределённый бар; no-op в CLI)
предпросмотр запроса (web+CLI): preview_request_payload (JSON {stage, label,
  model, messages, chars — СИМВОЛЫ по ролям + total, meta}) /
  write_preview_request (атомарная запись) / preview_logger (логгер
  ТОЛЬКО stderr — файловые логи скриптов mode="w" не трогаются)
главы: parse_chapter_id / build_chapter_map / find_chapter_file(strict) / format_ranges /
  compile_chapter_text (склейка chapter.txt из папок глав в память,
  (text, info), start/end) / compile_chapter_texts (та же склейка → файл) /
  read_chapter_titles / write_chapter_titles (названия глав: первая
  непустая строка файла; чтение/замена с NFC, вкладка «Главы»)

# core/projects.py — менеджмент проектов (общий слой бэкэндов)

разделы (динамические): DEFAULT_SECTIONS (ACTIVE/HOLD/DONE,
  алиас SECTIONS) / load_sections (файл .sections.json → дефолты + легаси
  DONE_OPEN при первом запуске; папки на диске до-обнаруживаются; дубли
  в файле схлопываются, записи без папки на диске — призраки — игнорируются;
  операции с файлом под мьютексом — параллельные запросы web не плодят
  дубли) /
  save_sections (атомарно tmp+replace) / create_section /
  rename_section (в существующий — merge: проекты переносятся
  move_project-ом; переименование на месте сохраняет позицию) /
  delete_section (непустой — отказ, пустой — сразу) / ensure_projects_root
  (создаёт дефолтные разделы + .sections.json; кастомные не трогает)
имена: valid_project_name / sanitize_project_name (англ. имя: пробелы → '*',
  только латиница/цифры/.*-)
проекты: list_projects / project_stats / project_progress_table
  (таблица готовности глав: по-главные флаги translate/redact/polish,
  легаси-суффиксы, ner/wiki/compiled) /
  create_project (каркас source/chapters/prompts/logs/tmp) /
  move_project (перенос между разделами) / rename_project /
  delete_project (без подтверждений — они в UI) /
  copy_project (дубликат, проектный .env не копируется);
  create/move/rename/copy/delete под мьютексом — параллельные запросы web
  не дают TOCTOU-гонок
шаблоны: TEMPLATE_PROTECTED="General" (системный, только чтение) /
  list_template_sets (подпапки templates/ с prompts/) /
  TEMPLATE_SKELETON=("prompts", "source") — инвариант набора;
  _ensure_template_skeleton (идемпотентный mkdir скелета; ремонт при
  чтении — web/api.py::_templates) /
  create_template_set (каркас prompts/+source/ как у General) /
  create_template_dir (ВСЕГДА ошибка «Каталоги в шаблонах
  неизменяемы» — каталоги не создаются) /
  copy_template_set (ИЗ General можно; после копии — ремонт скелета) /
  delete_template_set (General — запрещён) /
  templates_files (дерево файлов набора, относительные пути;
  пустые каталоги — как "path/") /
  read_template_file / write_template_file (только ФАЙЛЫ —
  родительский каталог обязан существовать, неявное создание каталогов
  запрещено; str|None) / delete_template_file (только файлы —
  каталог возвращает «Каталоги в шаблонах неизменяемы», str|None;
  эскейпы за пределы набора и запись в General отклоняются) /
  template_file_info (size/mtime файла — мета редактора) /
  move_template_file (только файлы; каталог — «Каталоги в
  шаблонах неизменяемы»; General запрещён, dst не должен существовать,
  родительский каталог dst — обязан существовать) /
  fill_project_from_template (prompts/+source/ без перезаписи) /
  render_metadata + write_project_metadata (title/author/subject/date
  в source/metadata.yaml, date по умолчанию — дата создания)

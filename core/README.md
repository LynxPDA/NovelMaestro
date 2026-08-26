# core/common.py (stdlib + requests; опц. pyahocorasick) — для заимствований

.env: parse_dotenv / find_env_file (системный корневой .env, вверх от
  старта: из папки книги — её pdir/.env, из корня — корневой .env) /
  get_server_config (HOST/API_KEY/MODEL; стадия непуста —
  СТАДИЯ_HOST/API_KEY/MODEL → общие, профили убраны) /
  get_stage_model (СТАДИЯ_MODEL → общая MODEL) / print_env_help
лог/модель: setup_logging / log_argv (фактическая команда запуска,
  значения --*api_key*/*token*/*secret* маскируются — M2) /
  determine_model (только из аргумента/`.env`; авто через GET /models убрано)
промпты: load_prompt / get_tagged_prompt
текст (СИМВОЛЫ): split_text_smart / get_ngrams / is_cjk / is_cjk_string /
  normalize_for_search / build_smart_regex / find_exact_match
NER: load_ner_data / find_relevant_ner (поиск по term+aliases в оригинальном написании) /
  collect_gender_names (поиск имён по translation; пол по наличию (female)/(male) в type)
ner_check: filter_ner_items / format_ner_record / glossary_body /
  build_ner_batches (count по убыванию, бюджет СИМВОЛЫ) /
  parse_ner_patches (JSON-патчи LLM {term,field,old,new,reason},
  field ∈ translation|type|notes) /
  review_entry / parse_review_doc / merge_review_entries (review-файл:
  поля английские: stage/status принять|отклонить/applied/old/new,
  накопление по этапам, дедуп по term+field+old+new) /
  apply_ner_patches (status + applied, дубли термина по совпавшему
  old, сверка old по NFC)
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
главы: parse_chapter_id / build_chapter_map / find_chapter_file(strict) / format_ranges /
  compile_chapter_text (склейка chapter.txt из папок глав в память,
  (text, info), start/end) / compile_chapter_texts (та же склейка → файл)

# core/projects.py — менеджмент проектов (общий слой бэкэндов)

разделы (динамические): DEFAULT_SECTIONS (ACTIVE/HOLD/DONE,
  алиас SECTIONS) / load_sections (файл .sections.json → дефолты + легаси
  DONE_OPEN при первом запуске; папки на диске до-обнаруживаются) /
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
  delete_project (без подтверждений — они в UI) / copy_project (дубликат)
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

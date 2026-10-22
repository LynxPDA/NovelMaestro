#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Свежесть документации: AGENTS.md ↔ код, пути из быстрой проверки.

Страховка от рассинхрона: имена функций в таблице §6 AGENTS.md обязаны
существовать в core/common.py, core/projects.py, core/transport.py,
core/deps.py, core/settings.py, core/search.py, core/update.py и упоминаться в
самом файле; пути в backticks (cli/, web/,
tests/, *.md, run.py) — существовать. Добавил функцию в таблицу §6 —
добавь её и сюда (CORE_API/PROJECTS_API/TRANSPORT_API/DEPS_API).
Запуск: python3 -m pytest tests/ -q"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import common as C  # noqa: E402
from core import deps as D  # noqa: E402
from core import projects as PRJ  # noqa: E402
from core import search as SRCH  # noqa: E402
from core import settings as SET  # noqa: E402
from core import stage as ST_  # noqa: E402
from core import transport as T  # noqa: E402
from core import uistate as UIS  # noqa: E402
from core import update as UPD  # noqa: E402

AGENTS_MD = ROOT / "AGENTS.md"

# Зеркало таблицы «Что использовать из core/common.py» (AGENTS.md §6)
CORE_API = [
    "parse_dotenv", "system_env_file", "env_files",
    "load_env", "env_overlay",
    "get_server_config", "print_env_help",
    "setup_logging", "log_argv", "determine_model",
    "load_prompt", "get_tagged_prompt",
    "estimate_tokens", "split_at_tokens", "trim_to_tokens",
    "count_file_tokens", "split_text_smart",
    "get_ngrams", "is_cjk", "is_cjk_string", "find_exact_match",
    "trim_rule_left", "trim_rule_right",
    "alias_list", "load_ner_data", "find_relevant_ner",
    "collect_gender_names",
    "load_examples", "find_relevant_examples", "format_fewshot_block",
    "load_rules_block", "find_relevant_dict",
    "normalize_for_search", "build_smart_regex", "extract_term_context",
    "ner_is_locked", "ner_set_locked", "ner_locked_count",
    "ner_pick_non_voted",
    "filter_ner_items", "format_ner_record", "glossary_body",
    "build_ner_batches", "parse_rag_suggestions", "ner_item_lookup",
    "diff_ner_records", "apply_ner_patches",
    "review_entry", "parse_review_doc", "merge_review_entries",
    "fix_entry", "merge_fix_entries", "apply_fix_to_text",
    "flex_fragment_pattern", "find_fragment_owner",
    "iter_sse_events", "stream_chat_completion", "llm_messages",
    "server_base_url", "probe_server",
    "atomic_write", "read_text_safe",
    "web_progress_enabled", "emit_progress",
    "parse_chapter_id", "build_chapter_map", "find_chapter_file",
    "format_ranges", "compile_chapter_texts",
    "read_chapter_titles", "write_chapter_titles",
    # предпросмотр запроса и проверка промпт-файла (web + CLI)
    "preview_request_payload", "write_preview_request", "preview_logger",
    "output_tail",
    "prompt_check_result", "write_prompt_check",
]
# Зеркало API core/stage.py (слой стадии: флаги LLM, профиль, контекст, прогресс)
STAGE_API = ["add_llm_args", "LlmProfile", "resolve_profile", "describe_profile",
             "add_check_prompt_arg", "check_prompt", "complete_until",
             "LoggedStage", "new_stage", "bind_profile", "setup_stage",
             "Progress", "REASONING_EFFORTS"]
# Зеркало API core/projects.py (web-интерфейс берёт его отсюда)
PROJECTS_API = ["SECTIONS", "DEFAULT_SECTIONS", "load_sections",
                "save_sections", "create_section", "rename_section",
                "delete_section", "valid_project_name",
                "sanitize_project_name", "ensure_projects_root",
                "list_projects", "project_stats", "project_progress_table",
                "progress_missing", "PROGRESS_STAGES",
                "create_project",
                "move_project", "rename_project", "list_template_sets",
                "TEMPLATE_SKELETON", "_ensure_template_skeleton",
                "create_template_dir", "render_metadata",
                "fill_project_from_template", "copy_template_prompts",
                "write_project_metadata",
                "delete_project", "copy_project",
                "builtin_template_files", "sync_builtin_templates",
                "TEMPLATE_ENV_EXAMPLE"]
# Зеркало API core/transport.py (единственная точка выхода в сеть)
TRANSPORT_API = ["TransportError", "ConnectTimeout", "ReadTimeout", "BrokenStream",
                 "ResponseStream", "open_stream", "open_get", "open_json_get",
                 "client", "reset_client", "BACKEND"]
# Зеркало API core/deps.py (реестр внешних зависимостей)
DEPS_API = ["ROLES", "status", "format_status", "missing_hint"]
# Зеркало API core/settings.py (реестр настроек — одно место истины)
SETTINGS_API = ["Setting", "Block", "Group", "GROUPS", "SETTINGS", "BY_KEY",
                "BY_BLOCK", "STAGES", "STAGE_TITLES", "LLM_BLOCKS", "LLM_ALIAS",
                "LLM_STAGE_FIELDS", "STAGE_LLM_FIELDS", "groups", "stage_fields",
                "settings_of",
                # диапазон глав — параметр запуска вне реестра настроек
                "RANGE_FIELDS", "RANGE_STAGES", "range_settings", "range_fields",
                "range_applies", "chapter_range_error", "apply_cli_defaults",
                "form_fields", "defaults", "stage_values", "llm_settings",
                "llm_values", "llm_form", "with_llm", "applies",
                "applicable_form", "env_key", "env_file",
                "is_on", "sanitize", "file_values", "layered_values", "effective",
                "display_value", "block_payload", "write_values",
                "groups_payload", "profile_defaults"]
# Зеркало API core/update.py (способ установки и обновление программы)
UPDATE_API = ["UpdateError", "PROTECTED_PATHS", "PROTECTED_DIRS", "install_kind",
              "update_mode", "installed_version", "version_tuple",
              "update_available", "release_notes", "latest_release",
              "release_asset_url", "download_asset", "is_protected",
              "host_command_hint", "apply_update", "RESTART_NOTES",
              "restart_supported", "restart_command", "restart_plan",
              "schedule_restart"]
# Зеркало API core/uistate.py (состояние книги: tmp/ui_state/)
UISTATE_API = ["UI_STATE_DIR", "UI_STATE_VERSION", "UI_STATE_KEEP",
               "UI_STATE_STALE", "UI_STATE_DEVICE_COOKIE", "ui_state_dir",
               "ui_state_device", "ui_state_read", "ui_state_write",
               "ui_state_prune"]
# Зеркало API core/search.py (поиск по текстам книги)
SEARCH_API = ["SearchGroup", "SEARCH_GROUPS", "GROUP_IDS", "GROUP_LABELS",
              "CLUSTERS", "CLUSTER_LABELS", "DEFAULT_SCOPES", "MAX_CONTEXT",
              "iter_project_files", "find_in_text", "search_project"]


def _agents_text() -> str:
    assert AGENTS_MD.is_file(), "AGENTS.md отсутствует в корне репо"
    return AGENTS_MD.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", CORE_API)
def test_core_api_exists_in_code(name):
    """Каждая функция из таблицы §6 реально существует в core.common."""
    assert callable(getattr(C, name, None)), f"core.common.{name} исчезла"


@pytest.mark.parametrize("name", PROJECTS_API)
def test_projects_api_exists_in_code(name):
    """API менеджмента проектов существует в core.projects."""
    assert hasattr(PRJ, name), f"core.projects.{name} исчезла"


@pytest.mark.parametrize("name", PROJECTS_API)
def test_projects_api_mentioned_in_agents_md(name):
    """API менеджмента проектов не потеряно в AGENTS.md (§6)."""
    assert re.search(rf"\b{name}\b", _agents_text()), \
        f"{name} не упоминается в AGENTS.md — таблица §6 устарела"


@pytest.mark.parametrize("name", CORE_API)
def test_core_api_mentioned_in_agents_md(name):
    """Таблица §6 не потеряла ни одной функции из зеркала."""
    assert re.search(rf"\b{name}\b", _agents_text()), \
        f"{name} не упоминается в AGENTS.md — таблица §6 устарела"


@pytest.mark.parametrize(
    "name",
    TRANSPORT_API + DEPS_API + SETTINGS_API + SEARCH_API + UPDATE_API
    + STAGE_API + UISTATE_API)
def test_transport_deps_api_exists_in_code(name):
    """API транспорта, зависимостей, реестра настроек, поиска, слоя стадии и
    обновлений существует."""
    module = (T if name in TRANSPORT_API
              else D if name in DEPS_API
              else SET if name in SETTINGS_API
              else SRCH if name in SEARCH_API
              else ST_ if name in STAGE_API
              else UIS if name in UISTATE_API else UPD)
    assert hasattr(module, name), f"{module.__name__}.{name} исчезла"


@pytest.mark.parametrize(
    "name",
    TRANSPORT_API + DEPS_API + SETTINGS_API + SEARCH_API + UPDATE_API
    + STAGE_API)
def test_transport_deps_api_mentioned_in_agents_md(name):
    """Строки §6 полные: имена транспорта, зависимостей, настроек, поиска и
    обновлений на месте."""
    assert re.search(rf"\b{name}\b", _agents_text()), \
        f"{name} не упоминается в AGENTS.md — таблица §6 устарела"


def test_agents_md_paths_exist():
    """Пути в backticks (код/доки) существуют; шаблоны xxx пропускаются."""
    text = _agents_text()
    paths = set(re.findall(r"`([A-Za-z0-9_./-]+\.(?:py|md))`", text))
    assert paths, "в AGENTS.md не нашлось ни одного пути в backticks"
    for rel in sorted(paths):
        if "xxx" in rel or "*" in rel:
            continue  # шаблон нового файла, а не реальный путь
        assert (ROOT / rel).exists(), \
            f"AGENTS.md ссылается на несуществующий путь: {rel}"


def _env_example_pairs():
    """templates/.env.example → [(ключ, значение)] в порядке файла."""
    out = []
    for line in (ROOT / "templates" / ".env.example").read_text(
            encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out.append((k.strip(), v.strip()))
    return out


def test_env_example_mirrors_registry():
    """templates/.env.example — зеркало реестра: те же ключи в том же порядке
    и те же дефолты. Страж «никакие настройки не потерялись»: шаблон —
    документация, а не четвёртый экземпляр конфига, и уезжать в сторону он
    не имеет права.

    bool-значения в файле — «1»/«0»; textarea-переносы — литералом «\\n»
    (тот же sanitize, что и у страницы «Настройки»).
    """
    pairs = _env_example_pairs()
    want = [s for s in SET.SETTINGS if not s.noenv and not s.run]
    assert [k for k, _ in pairs] == [s.key for s in want], (
        "templates/.env.example разошёлся с реестром по ключам или порядку")
    bad = []
    for s in want:
        rendered = SET.sanitize(s, s.default)
        got = dict(pairs)[s.key]
        if got != rendered:
            bad.append(f"{s.key}: шаблон {got!r} ≠ реестр {rendered!r}")
    assert not bad, "; ".join(bad)


def test_no_legacy_launcher_names():
    """start_ner/start_redact_errors и имя redact_errors переименованы —
    в доках их быть не должно."""
    for doc in ("AGENTS.md", "README.md", "core/README.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert "start_ner" not in text and "start_redact_errors" not in text, \
            f"{doc}: устаревшие имена лаунчеров (start_*)"
        assert "redact_errors" not in text, \
            f"{doc}: устаревшее имя redact_errors (теперь translate_check_llm)"


def _pip_names(path):
    """Пакеты pip-списка: строки без комментариев и без `-r ...`."""
    lines = (path.read_text(encoding="utf-8").splitlines())
    return [line.split("#")[0].strip()
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
            and not line.strip().startswith("-r")]


def test_requirements_cover_declared_roles():
    """pip-списки и реестр ролей не разъезжаются: каждый кандидат роли
    объявлен в requirements*.txt (тесты — только в dev-списке)."""
    runtime = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    runtime_pkgs = [n.lower() for n in _pip_names(ROOT / "requirements.txt")]
    assert "-r requirements.txt" in dev, "requirements-dev.txt не включает рантайм"
    names = {c["pip"] for role in D.ROLES for c in role["candidates"] if c["pip"]}
    for pip in sorted(names):
        in_runtime = re.search(rf"^{pip}$", runtime, re.M) is not None
        in_dev = re.search(rf"^{pip}\b", dev, re.M) is not None
        assert in_runtime or in_dev, f"{pip}: роль в core/deps.py есть, в списках нет"
        if pip == "pytest":
            assert not in_runtime, "pytest не должен попадать в рантайм/образ"
    for pip in ("httpx", "tqdm", "pyahocorasick", "pytest"):
        assert pip in names, f"{pip}: есть в requirements, но не в реестре ролей"
    assert "requests" not in runtime_pkgs, \
        f"requests вытеснен, а в рантайме остался: {runtime_pkgs}"


def _project_tab_labels():
    """Подписи вкладок проекта из реестра SPA (project-views.js: PROJECT_TABS)."""
    src = (ROOT / "web" / "static" / "project-views.js").read_text(encoding="utf-8")
    block = src[src.index("const PROJECT_TABS = ["):]
    block = block[:block.index("];")]
    return re.findall(r'\[\s*"[a-z_]+"\s*,\s*"([^"]+)"\s*\]', block)


def test_readme_lists_all_project_tabs():
    """README перечисляет вкладки проекта поимённо, и список обязан совпадать
    с PROJECT_TABS: новая вкладка молча выпадала из документации (так
    «История» и пропала). Порядок в README держим как в реестре."""
    labels = _project_tab_labels()
    assert labels, "PROJECT_TABS не разобран — изменился формат реестра"
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    line = next((ln for ln in text.splitlines()
                 if "Внутри проекта — вкладки:" in ln), "")
    assert line, "в README нет строки со списком вкладок проекта"
    listed = re.findall(r"\*\*([^*]+)\*\*", line)
    listed = [p.strip() for part in listed for p in part.split(",")]
    assert listed == labels, (
        f"список вкладок в README разошёлся с PROJECT_TABS: "
        f"{listed} ≠ {labels}")

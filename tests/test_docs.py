#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Свежесть документации: AGENTS.md ↔ код, пути из быстрой проверки.

Страховка от рассинхрона: имена функций в таблице §6 AGENTS.md обязаны
существовать в core/common.py и core/projects.py и упоминаться в самом
файле; пути в backticks (cli/, web/, tests/, *.md, run.py) —
существовать. Добавил функцию в таблицу §6 — добавь её и сюда
(CORE_API/PROJECTS_API). Запуск: python3 -m pytest tests/ -q"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import common as C  # noqa: E402
from core import projects as PRJ  # noqa: E402

AGENTS_MD = ROOT / "AGENTS.md"

# Зеркало таблицы «Что использовать из core/common.py» (AGENTS.md §6)
CORE_API = [
    "parse_dotenv", "find_env_file", "get_server_config", "get_stage_model",
    "print_env_help",
    "setup_logging", "log_argv", "determine_model",
    "load_prompt", "get_tagged_prompt",
    "split_text_smart",
    "get_ngrams", "is_cjk", "is_cjk_string", "find_exact_match",
    "load_ner_data", "find_relevant_ner", "collect_gender_names",
    "normalize_for_search", "build_smart_regex",
    "filter_ner_items", "format_ner_record", "glossary_body",
    "build_ner_batches", "parse_ner_patches", "apply_ner_patches",
    "review_entry", "parse_review_doc", "merge_review_entries",
    "fix_entry", "merge_fix_entries", "apply_fix_to_text",
    "stream_chat_completion",
    "atomic_write", "read_text_safe",
    "web_progress_enabled", "emit_progress",
    "parse_chapter_id", "build_chapter_map", "find_chapter_file",
    "format_ranges", "compile_chapter_texts",
]
# Зеркало API core/projects.py (web-интерфейс берёт его отсюда)
PROJECTS_API = ["SECTIONS", "DEFAULT_SECTIONS", "load_sections",
                "save_sections", "create_section", "rename_section",
                "delete_section", "valid_project_name",
                "sanitize_project_name", "ensure_projects_root",
                "list_projects", "project_stats", "create_project",
                "move_project", "rename_project", "list_template_sets",
                "TEMPLATE_SKELETON", "_ensure_template_skeleton",
                "create_template_dir", "render_metadata",
                "fill_project_from_template", "write_project_metadata",
                "delete_project", "copy_project"]
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


def test_no_legacy_launcher_names():
    """start_ner/start_redact_errors и имя redact_errors переименованы —
    в доках их быть не должно."""
    for doc in ("AGENTS.md", "README.md", "core/README.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert "start_ner" not in text and "start_redact_errors" not in text, \
            f"{doc}: устаревшие имена лаунчеров (start_*)"
        assert "redact_errors" not in text, \
            f"{doc}: устаревшее имя redact_errors (теперь translate_check_llm)"

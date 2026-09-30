#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Свежесть документации: AGENTS.md ↔ код, пути из быстрой проверки.

Страховка от рассинхрона: имена функций в таблице §6 AGENTS.md обязаны
существовать в core/common.py, core/projects.py, core/transport.py,
core/deps.py и упоминаться в самом файле; пути в backticks (cli/, web/,
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
from core import transport as T  # noqa: E402

AGENTS_MD = ROOT / "AGENTS.md"

# Зеркало таблицы «Что использовать из core/common.py» (AGENTS.md §6)
CORE_API = [
    "parse_dotenv", "find_env_file", "system_env_file", "env_overlay",
    "get_server_config", "get_stage_model", "print_env_help",
    "setup_logging", "log_argv", "determine_model",
    "load_prompt", "get_tagged_prompt",
    "estimate_tokens", "split_at_tokens", "trim_to_tokens",
    "split_text_smart",
    "get_ngrams", "is_cjk", "is_cjk_string", "find_exact_match",
    "trim_rule_left", "trim_rule_right",
    "load_ner_data", "find_relevant_ner", "collect_gender_names",
    "load_examples", "find_relevant_examples", "format_fewshot_block",
    "load_rules_block", "find_relevant_dict",
    "normalize_for_search", "build_smart_regex", "extract_term_context",
    "filter_ner_items", "format_ner_record", "glossary_body",
    "build_ner_batches", "parse_rag_suggestions", "ner_item_lookup",
    "diff_ner_records", "apply_ner_patches",
    "review_entry", "parse_review_doc", "merge_review_entries",
    "fix_entry", "merge_fix_entries", "apply_fix_to_text",
    "flex_fragment_pattern", "find_fragment_owner",
    "stream_chat_completion", "llm_messages",
    "atomic_write", "read_text_safe",
    "web_progress_enabled", "emit_progress",
    "parse_chapter_id", "build_chapter_map", "find_chapter_file",
    "format_ranges", "compile_chapter_texts",
    "read_chapter_titles", "write_chapter_titles",
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
# Зеркало API core/transport.py (единственная точка выхода в сеть)
TRANSPORT_API = ["TransportError", "ConnectTimeout", "ReadTimeout", "BrokenStream",
                 "ResponseStream", "open_stream", "client", "reset_client",
                 "BACKEND"]
# Зеркало API core/deps.py (реестр внешних зависимостей)
DEPS_API = ["ROLES", "status", "format_status", "missing_hint"]


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


@pytest.mark.parametrize("name", TRANSPORT_API + DEPS_API)
def test_transport_deps_api_exists_in_code(name):
    """API транспорта и реестра зависимостей существует в core (зеркало §6)."""
    module = T if name in TRANSPORT_API else D
    assert hasattr(module, name), f"{module.__name__}.{name} исчезла"


@pytest.mark.parametrize("name", TRANSPORT_API + DEPS_API)
def test_transport_deps_api_mentioned_in_agents_md(name):
    """Строки §6 про транспорт и зависимости полные: имён там терять нельзя."""
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

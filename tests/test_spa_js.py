"""SPA : юнит-тесты чистых функций + синтаксис static/*.js.

ui-core.js покрывается node --test (tests/spa/ui-core.test.mjs) —
без сети и без DOM; каждый static/*.js проверяется node --check.
Если node отсутствует — тесты пропускаются (skip), как требует
AGENTS.md §2 (опциональные зависимости с fallback).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SPA_DIR = REPO / "web" / "static"
SPA_TESTS = str(REPO / "tests" / "spa" / "*.test.mjs")

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node не установлен")


def _node(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", *args], capture_output=True,
                          text=True, cwd=REPO)


def test_ui_core_node_tests():
    """node --test по tests/spa/*.test.mjs — чистые функции SPA."""
    r = _node("--test", SPA_TESTS)
    assert r.returncode == 0, (
        f"node --test упал (rc={r.returncode}):\n{r.stdout}\n{r.stderr}")


@pytest.mark.parametrize("name", sorted(p.name for p in SPA_DIR.glob("*.js")))
def test_js_syntax(name):
    """node --check — синтаксис каждого статического JS."""
    r = _node("--check", str(SPA_DIR / name))
    assert r.returncode == 0, f"node --check {name}:\n{r.stderr}"


def test_run_views_expert_form_not_async():
    """Регрессия «[object Promise]»: expertForm рендерит DOM синхронно
    (formPanel вставляет результат как узел) — async вернул бы Promise."""
    src = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    assert "async function expertForm" not in src
    assert "function expertForm(key, spec)" in src


def test_create_project_modal_uploads():
    """Мастер создания: опциональные обложка и исходник → source/."""
    src = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    # два опциональных file-input: обложка (jpg/png — для EPUB/FB2,
    # webp не предлагаем) и исходник
    assert 'accept: ".jpg,.jpeg,.png"' in src
    assert "webp" not in src.split("function manageProjectModal")[0]
    assert 'accept: ".txt,.md,.epub,.zip"' in src
    # обложка — PUT /api/cover (base64), исходник — upload с dest=source
    assert 'await api("/cover"' in src
    assert 'form.append("dest", "source")' in src
    # проект создаётся ДО загрузок (нужен существующий project=sec/name)
    create = src.index("function createProjectModal")
    up = src.index("form.append(\"dest\", \"source\")")
    assert create < up
    # загрузки живут внутри мастера (до конца его тела)
    assert src.index("function manageProjectModal") > up


def test_help_view_renders_static_md():
    """Справка: viewHelp грузит web/static/help.md и рендерит через marked
    (без innerHTML — санитайзер + createContextualFragment)."""
    src = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    assert "async function viewHelp" in src
    assert 'fetch("/help.md"' in src
    assert "window.marked.parse" in src
    assert "createContextualFragment" in src


def test_glossary_dispute_vars_declared():
    """Регрессия «пустая вкладка Глоссарий»: dispute/voteKeys/
    disputeVictims/saveDispute ОБЯЗАНЫ быть объявлены в project-views.js
    (ReferenceError при рендере nerView оставлял только панели)."""
    src = (SPA_DIR / "project-views.js").read_text(encoding="utf-8")
    for decl in ("const LS_DISPUTE_KEY", "const voteKeys",
                 "let dispute = null", "function saveDispute",
                 "function disputeVictims"):
        assert decl in src, f"не найдено объявление: {decl}"
    # использование внутри nerView — до конца файла (нет дубля вне)
    assert src.count("function disputeVictims") == 1

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


def test_run_views_stream_ctrl_let():
    """Регрессия «Assignment to constant variable»: streamCtrl
    переприсваивается в attachStream/очистке — только let.
    const ронял SSE до первого подключения: лог пуст, статус
    (stop/done) без перезагрузки страницы не приходил."""
    src = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    assert "let streamCtrl = null" in src
    assert "const streamCtrl" not in src


def test_app_h_null_attrs_safe():
    """Регрессия «can't convert null to object» в модалке предпросмотра
    (h("div", null, …)): h() терпит attrs = null."""
    src = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    assert "Object.entries(attrs || {})" in src


def test_run_views_chips_persistence():
    """Выбор чипсов (hidden noenv: types/fields/ner_fields) переживает
    перезагрузку страницы: chipRestore — в initFormValues, saveChips —
    в обработчиках чипсов. Иначе запуск уходил не с теми полями,
    что показаны (перезагрузка сбрасывала выбор на дефолт)."""
    src = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    assert "function chipRestore(" in src
    assert "chipRestore(key, spec, vals)" in src
    assert "function saveChips(" in src
    assert "localStorage.setItem(chipKey(key)" in src
    # ner_check: дефолт полей, материализованный curFields, — touched
    # (в простом режиме уходит именно то, что показано чипсами)
    assert 'st.touched[key].add("fields");' in src
    assert "function expertForm(key, spec)" in src


def test_run_views_last_finished_log():
    """Запуски: лог последнего завершённого запуска остаётся на вкладке
    стадии (панель — текущий запуск в любом статусе или история стадии
    из /api/jobs; live-гард — строки чужого запуска не утекают)."""
    src = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    # колонка лога: текущий запуск (любой статус) или история стадии
    assert "async function logColumn()" in src
    assert "lastFinishedJob(" in src
    assert "lazyLastLog(" in src
    assert 'j.status !== "running"' in src
    # live-гард onPayload: DOM — только при совпадении стадии запуска
    assert "st.job.action === st.stage" in src
    # logPanel параметризован; «Стоп» — только для running, у финала — время
    assert "function logPanel(view)" in src
    assert "function stopBtn(job)" in src
    assert 'job.status === "running"' in src
    assert "job.finished || job.created" in src
    # кэш истории стадии инвалидируется при финальном статусе
    assert "delete st.lastLog[st.job.action]" in src


def test_fit_preview_frame_defers_hidden():
    """Предпросмотр отчёта в скрытом контейнере (неактивная под-вкладка
    «Проверки»): load iframe срабатывает при display:none, scrollHeight=0
    — подгон высоты откладывается до появления кадра (IntersectionObserver),
    иначе кадр навсегда остаётся 80px; уход со страницы снимает наблюдение."""
    src = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    assert "!frame.isConnected || !frame.offsetParent" in src
    assert "new IntersectionObserver(" in src
    assert "frame.dataset.fitPending" in src
    # очистка отложенных подгонов при навигации (кадры выбрасываются)
    assert 'querySelectorAll("iframe[data-fit-pending]")' in src
    # повторный вызов снимает предыдущего наблюдателя (новый load)
    assert "if (frame._fitIO) {" in src


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


def test_templates_general_readonly():
    """Шаблоны · General: файл открывается в просмотре (read-only),
    кнопка «Сохранить» не рендерится, «Просмотр» вместо «Правка»."""
    src = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    assert "ed.setReadOnly(readonly)" in src
    assert "const readonly = st.set === \"General\"" in src
    assert '"Просмотр"' in src
    assert "только чтение" in src
    assert "...(readonly ? [] : [saveBtn])" in src


def test_ner_check_rag_ui_present():
    """Запуски ner_check · RAG: условная видимость RAG-полей,
    кнопка «Добавить спорные»; отдельный RAG-промпт-файл убран —
    RAG-промпт живёт в общем «Промпт-файле» (тег <prompt_rag>)."""
    rv = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    # RAG-поля строятся и прячутся по режиму (и простой, и экспертный)
    assert "rag_source_type" in rv
    assert "rag_budget" in rv
    assert "rag_prompt_file" not in rv  # дубль убран
    assert "addDisputedTermsModal" in rv
    assert "Добавить спорные" in rv
    assert "classList.toggle(\"hidden\", !isRag)" in rv
    # RAG-промпт — тег <prompt_rag> в общем промпт-файле стадии
    st = (REPO / "web" / "stages.py").read_text(encoding="utf-8")
    assert "ner_check_prompt.txt" in st
    cli = (REPO / "cli" / "ner_check.py").read_text(encoding="utf-8")
    assert "load_rag_prompt(args.rag_prompt_file or args.prompt_file" in cli
    # автоподхват ner_check_prompt.txt остался в общем «Промпт-файле»
    assert "ner_check_prompt.txt" in st and "autofile" in st
    # чипсы типов/полей скрываются в RAG-режиме (не влияют)
    assert "ragHidden" in rv


def test_prompt_edit_button():
    """Запуски (LLM): промпт не выбран — «Загрузить»; выбран —
    «Редактировать» (просмотр/правка/сохранение через /api/prompts)."""
    rv = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    assert "function editPromptModal(fileName)" in rv
    assert "isPrompt" in rv  # только dir=prompts, .txt
    assert "upBtn.textContent = sel.value ? \"Редактировать\" : \"Загрузить\"" \
        in rv
    assert "/prompts/${encodeURIComponent(fileName)}" in rv
    assert "makeEditor(d.content || \"\", extOf(fileName))" in rv
    # редактор промпта — модалка с сохранением
    assert "Сохранить" in rv and "editor-modal-body" in rv


def test_glossary_dispute_removed():
    """«Спорные» убраны из вкладки Глоссарий (перенос в Запуски
    ner_check): dispute-объявления и кнопка отсутствуют — но
    «Добавить спорные» живёт в run-views.js (RAG)."""
    src = (SPA_DIR / "project-views.js").read_text(encoding="utf-8")
    for decl in ("LS_DISPUTE_KEY", "voteKeys", "saveDispute",
                 "disputeVictims", "Спорные"):
        assert decl not in src, f"dispute-код остался: {decl}"
    rv = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    assert "Добавить спорные" in rv
    assert "addDisputedTermsModal" in rv


def test_run_views_preview_request():
    """Запуски: кнопка «Предпросмотр запроса» — только у LLM-стадий
    (spec.preview) и только в Экспертном режиме; модалка — POST
    /stages/{key}/preview-request, сводка символов + messages."""
    src = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    # кнопка: по флагу спеки, ghost, идёт в экспертную форму
    assert "spec.preview" in src
    assert '"Предпросмотр запроса"' in src
    assert 'previewRequestModal(key, spec, "expert")' in src
    # модалка: POST на preview-request и рендер payload
    assert "async function previewRequestModal(" in src
    assert "`/stages/${key}/preview-request`" in src
    assert "previewRequestView(" in src
    assert "d.chars" in src and "d.messages" in src
    # previewRequestView не показывает секреты: моделей/меток достаточно;
    # в простом режиме (simplePanel) кнопки нет — modal зовётся только
    # с mode "expert"
    assert '"expert"' in src

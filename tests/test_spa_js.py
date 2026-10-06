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


def test_index_loads_ui_layer():
    """Порядок скриптов в index.html: ui-core → ui-components → вьюхи → app.js.
    Вьюхи зовут h/iconEl глобально — их объявляет ui-components, он обязан
    загрузиться раньше."""
    html = (SPA_DIR / "index.html").read_text(encoding="utf-8")
    order = [n for n in ("ui-core.js", "ui-components.js", "project-views.js",
                         "run-views.js", "app.js") if f"/{n}" in html]
    assert order == ["ui-core.js", "ui-components.js", "project-views.js",
                     "run-views.js", "app.js"], order


def test_ui_core_node_tests():
    """node --test по tests/spa/*.test.mjs — чистые функции и DOM-слой SPA."""
    r = _node("--test", SPA_TESTS)
    assert r.returncode == 0, (
        f"node --test упал (rc={r.returncode}):\n{r.stdout}\n{r.stderr}")


@pytest.mark.parametrize("name", sorted(p.name for p in SPA_DIR.glob("*.js")))
def test_js_syntax(name):
    """node --check — синтаксис каждого статического JS."""
    r = _node("--check", str(SPA_DIR / name))
    assert r.returncode == 0, f"node --check {name}:\n{r.stderr}"


def test_run_views_stage_form_not_async():
    """Регрессия «[object Promise]»: stageForm рендерит DOM синхронно
    (formPanel вставляет результат как узел) — async вернул бы Promise."""
    src = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    assert "async function stageForm" not in src
    assert "function stageForm(key, spec)" in src


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
    (h("div", null, …)): h() терпит attrs = null (h живёт в ui-components)."""
    src = (SPA_DIR / "ui-components.js").read_text(encoding="utf-8")
    assert "Object.entries(attrs || {})" in src


def test_run_views_chips_persistence():
    """Выбор чипсов (hidden noenv: types/fields/ner_fields) переживает
    перезагрузку страницы: chipRestore — в initFormValues, saveChips —
    в обработчиках чипсов. Иначе запуск уходил не с теми полями,
    что показаны (перезагрузка сбрасывала выбор на дефолт)."""
    src = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    assert "function chipRestore(" in src
    assert "chipRestore(key, spec, vals)" in src
    assert "localStorage.setItem(chipKey(key)" in src
    # ner_check: дефолт полей, материализованный curFields, — touched
    assert 'st.touched[key].add("fields");' in src
    # форма стадии одна (Простой/Экспертный удалены), кнопка сброса
    # пересчитывается делегатом, а не перестройкой формы; тем же делегатом
    # изменённые поля уходят в память браузера
    assert "function stageForm(key, spec)" in src
    assert ('panel.addEventListener("input", () => '
            "{ syncResetBtn(key); runSave(key); });") in src
    assert ('panel.addEventListener("change", () => '
            "{ syncResetBtn(key); runSave(key); });") in src
    assert "st.baseline[key] = Object.assign({}, vals)" in src


def test_run_views_values_persistence():
    """Изменённые поля формы запусков — рабочее состояние браузера: их нет ни в
    .env книги, ни в argv, поэтому без памяти они молча возвращались к
    значениям общего конфига после запуска или перезагрузки страницы."""
    src = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    assert "function runKey(key)" in src
    assert 'return `runVals:${section}/${name}:${key}`' in src
    assert "function runSave(key)" in src
    assert "function runRestore(key, spec, vals)" in src
    # чтение — в initFormValues до чипсов; запись — делегатом формы
    assert "runRestore(key, spec, vals)" in src
    assert "localStorage.setItem(runKey(key), JSON.stringify(data))" in src
    # пустое изменение — ключ памяти удаляется, а не оставляется с мусором
    assert "localStorage.removeItem(runKey(key))" in src
    # профиль стадии — не «изменённое поле»: у него своя память по стадии
    assert "if (f.name === PROFILE_FIELD) continue;" in src


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


def test_editor_pane_toggle_says_editor():
    """Кнопка предпросмотра в редакторе: «Рендер» ↔ «Редактор».

    Прежнее имя второго режима — «Код»: слово из мира разработчика, обычный
    читатель книги читает «вернуть редактор»."""
    src = (SPA_DIR / "ui-components.js").read_text(encoding="utf-8")
    assert '"Рендер" : "Редактор"' in src
    assert '"Код"' not in src


def test_settings_profile_switch_is_select():
    """Настройки · профили LLM: выбор — один <select>.

    Профилей больше трёх, и строка чипсов выглядела второй панелью вкладок
    прямо над карточками. Плюс регрессия «сохранено, но не видно»: после PUT
    /settings SPA обязана перечитать значения профилей — поля нераскрытого
    профиля рисуются именно из model.profiles."""
    src = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    assert "settings-profile-select" in src
    assert '"aria-label": "Профиль LLM"' in src
    assert "model.profiles = r.profiles" in src
    assert 'localStorage.setItem("settingsProfile"' in src
    css = (SPA_DIR / "styles.css").read_text(encoding="utf-8")
    block = css[css.index(".settings-cards {"):][:120]
    assert "margin-top" in block, "карточки настроек липнут к строке вкладок"


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
    # RAG-поля строятся и прячутся по режиму ner_check
    assert "rag_source_type" in rv
    assert "rag_budget" in rv
    assert "rag_prompt_file" not in rv  # дубль убран
    assert "addDisputedTermsModal" in rv
    assert "Добавить спорные" in rv
    assert "classList.toggle(\"hidden\", !isRag)" in rv
    # RAG-промпт — тег <prompt_rag> в общем промпт-файле стадии; метаданные
    # поля (промпт-файл) — в реестре настроек, не в stages.py
    st = (REPO / "core" / "settings.py").read_text(encoding="utf-8")
    assert "ner_check_prompt.txt" in st
    cli = (REPO / "cli" / "ner_check.py").read_text(encoding="utf-8")
    assert "load_rag_prompt(args.rag_prompt_file or args.prompt_file" in cli
    # автоподхват ner_check_prompt.txt остался в общем «Промпт-файле»
    assert "ner_check_prompt.txt" in st and "autofile" in st
    # чипсы типов/полей скрываются в RAG-режиме (не влияют)
    assert "ragHidden" in rv


def test_prompt_edit_button():
    """Запуски (LLM): промпт не выбран — «Загрузить»; выбран —
    «Редактировать» (просмотр/правка/сохранение через /api/prompts).
    Флаг editable в спеке расширяет кнопку на текстовые файлы из
    source/ (compile: метаданные YAML, страница поддержки) — те же
    модалки, но через /api/file."""
    rv = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    assert "function editFileModal(relPath, opts = {})" in rv
    assert "isEditable" in rv  # dir=prompts+.txt или f.editable
    assert "upBtn.textContent = sel.value ? \"Редактировать\" : \"Загрузить\"" \
        in rv
    assert "/prompts/${encodeURIComponent(relPath)}" in rv
    assert "/file?project=${section}/${name}" in rv  # чтение source-файлов
    assert 'makeEditor(d.content || "", UICore.fileLang(relPath, isPromptFile))' \
        in rv
    # редактор файла — модалка с сохранением
    assert "Сохранить" in rv and "editor-modal-body" in rv
    # спека: compile epub_meta/donate_file — editable, cover — нет
    from web.stages import STAGE_SPECS
    fields = {f["name"]: f for f in STAGE_SPECS["compile"]["fields"]}
    assert fields["epub_meta"].get("editable") is True
    assert fields["donate_file"].get("editable") is True
    assert not fields["cover"].get("editable")


def test_prompt_markup_is_readable():
    """Промпты читаются как промпты, а не как непонятный txt: редактор
    файла промптов получает html-язык (теги <system>/<translate> видны
    лицом), а предпросмотр запроса размечает теги, подстановки {плейсхолдеры}
    и ключи JSON отдельными span-ами."""
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    core = (SPA_DIR / "ui-core.js").read_text(encoding="utf-8")
    pv = (SPA_DIR / "project-views.js").read_text(encoding="utf-8")
    rv = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    css = (SPA_DIR / "styles.css").read_text(encoding="utf-8")

    # язык — по назначению файла (prompts/), а не по расширению .txt
    assert 'prompt: "html",' in app, "язык промптов — html"
    assert "function fileLang(path, isPrompt)" in core
    assert 'return /(^|\\/)prompts\\//.test(String(path || "")) ? "prompt"' in core
    assert 'makeEditor("", "prompt")' in pv, "вкладка «Промпты» — язык промпта"
    assert "UICore.fileLang(full)" in pv and "UICore.fileLang(full)" in app

    # лицо подсветки: текст, скобки и имена тегов
    for face in ("t.content, t.bracket, t.separator", "t.angleBracket",
                 "t.tagName, t.attributeName", "t.attributeValue"):
        assert face in app, f"в EDITOR_HIGHLIGHT нетfaces: {face}"

    # предпросмотр запроса — span-ы вместо голого текста
    assert "UICore.promptParts(m.content)" in rv
    for cls in (".pv-tag", ".pv-var", ".pv-key"):
        assert cls in css, f"в CSS нет класса {cls}"
    for var in ("--code-tag", "--code-var", "--code-key"):
        assert css.count(var) >= 2, f"{var}: тема и светлая тема"


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
    (spec.preview); модалка — POST /stages/{key}/preview-request, сводка
    символов + messages. Форма стадии одна — модалка вызывается без режима."""
    src = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    # кнопка: по флагу спеки, ghost, в единой форме стадии
    assert "spec.preview" in src
    assert '"Предпросмотр запроса"' in src
    assert "previewRequestModal(key, spec)" in src
    # модалка: POST на preview-request и рендер payload
    assert "async function previewRequestModal(" in src
    assert "`/stages/${key}/preview-request`" in src
    assert "previewRequestView(" in src
    assert "d.chars" in src and "d.messages" in src
    # режимов формы нет: ни переключателя, ни пресет-карточки
    assert "runMode" not in src and "simplePanel" not in src
    assert "localFieldBadge" not in src


def test_editor_search_is_a_button():
    """Поиск по тексту редактора — отдельной кнопкой (лупой), а не только
    Ctrl+F: общий слой открывает панель поиска CM и выделяет поле. Кнопка
    стоит в шапке одиночного редактора и в шапке активной панели
    многосоставной вкладки «Редактор»."""
    comp = (SPA_DIR / "ui-components.js").read_text(encoding="utf-8")
    assert "function editorSearch(ed, opts)" in comp
    assert "if (!ed || !ed.isCM) return null;" in comp
    assert "openSearchPanel" in comp
    assert 'const tip = o.tip || "Поиск в тексте (Ctrl+F)";' in comp
    assert '"aria-label": tip' in comp
    # textarea (fallback) остаётся без кнопки: панели поиска там нет
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    assert app.count("UIC.editorSearch(ed)") == 2, "редактор и предпросмотр"
    views = (SPA_DIR / "project-views.js").read_text(encoding="utf-8")
    assert "UIC.editorSearch(ed)" in views and "UIC.editorSearch(e)" in views


def test_glossary_controls_live_behind_one_button():
    """Глоссарий: настройки вкладки собраны за одной кнопкой «⋮» (режим
    «только зафиксированные», столбцы, типы, поля поиска, добавление столбца
    и термина, удаление столбца и по фильтру) — в тулбаре остаются только поле
    поиска и экспорт; строка таблицы — чекбокс выделения, правка и замок;
    групповые замок и удаление — в панели выделения; служебный ключ «_locked»
    столбцом не показывается."""
    src = (SPA_DIR / "project-views.js").read_text(encoding="utf-8")
    i = src.index("async function nerView()")
    body = src[i:src.index("async function reviewView()", i)]
    for name in ('iconName: "kebab"', 'btnClass: "ner-menu-btn"',
                 "{ el: lockBox }", "{ el: colBtn }", "{ el: typeBtn }",
                 "{ el: searchFieldsBtn }", "{ el: addColBtn }",
                 '{ label: "Добавить термин", action: addTerm }',
                 "{ el: delColBtn }", "{ el: delFilterBtn }",
                 'class: "ner-search"', "bulkLock(true)", "bulkDelete",
                 "ner-td-sel", "ner-row-locked", "bulkLock(false)",
                 '.filter((x) => x != null)'):
        assert name in body, f"в коде вкладки нет {name!r}"
    # панель выделения заменяет кнопки тулбара, а не добавляется к ним
    assert 'h("div", { class: "files-tools" }, menu, exportBtn)' in body
    # замок — состояние записи, а не столбец данных
    assert 'k !== "__new" && k !== "_locked"' in src


def test_dropdown_menus_are_fixed_positioned():
    """Регрессия «у нижней строки списка не хватает места для меню «⋮»»:
    dropdown был position:absolute от строки, а карточка списка файлов
    (.files-list) с overflow:hidden обрезала его по собственной высоте —
    у последней строки из 78px меню оставалось ~4px. Меню обязано быть
    position:fixed: ancestor-overflow fixed-потомка не режет, а
    разворот вверх и зажим по краям окна (UICore.menuPlacement) доводят
    его целиком. Координаты ставит JS — жёсткие top/right/left в CSS
    здесь запрещены."""
    css = (SPA_DIR / "styles.css").read_text(encoding="utf-8")
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    comp = (SPA_DIR / "ui-components.js").read_text(encoding="utf-8")

    def rule(name):
        i = css.index(name)
        return css[i:css.index("}", i)]

    for name in (".user-menu {", ".toolbar-menu .menu-box {"):
        body = rule(name)
        assert "position: fixed" in body, f"{name} обязано быть position:fixed"
        assert "position: absolute" not in body
        assert "calc(100%" not in body, f"{name}: якорь по кнопке ставит placeMenu"

    # скрытие — общим классом .hidden (attribute-механизм [hidden] убран)
    assert ".menu-box[hidden]" not in css
    assert 'class: (o.menuClass || "menu-box") + " hidden"' in comp
    # оба вида меню собираются одним компонентом и общим переключателем,
    # а не своими слушателями: app.js остался тонким делегатором
    assert "onclick: () => window.toggleMenu(btn, box)" in comp
    assert "return UIC.menuButton(items," in app
    assert "window.closeMenus()" in comp
    # геометрия — одна чистая функция; слежение за скроллом/ресайзом
    assert "UICore.menuPlacement(" in app
    assert "if (!menu.isConnected)" in app, "перерисовка строки не оставляет висящее меню"

"""SPA : юнит-тесты чистых функций + синтаксис static/*.js.

ui-core.js покрывается node --test (tests/spa/ui-core.test.mjs) —
без сети и без DOM; каждый static/*.js проверяется node --check.
Если node отсутствует — тесты пропускаются (skip), как требует
AGENTS.md §2 (опциональные зависимости с fallback).
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SPA_DIR = REPO / "web" / "static"
# список файлов, а не маска: раскрытие шаблонов появилось в node 22,
# а на CI стоит node 20 — маска приходила аргументом и не находилась
SPA_TESTS = tuple(str(p) for p in sorted((REPO / "tests" / "spa").glob("*.test.mjs")))

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
    r = _node("--test", "--test-reporter=tap", *SPA_TESTS)
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


def test_hl_tip_replace_children_no_null():
    """Подсказка термина в редакторе: условные строки фильтруются перед
    replaceChildren. Узел null там не пропуск, а текст «null»: подсказка
    читалась бы «熊胖胖null Перевод: …» (в панели выделения этот подводный
    камень уже обойдён)."""
    src = (SPA_DIR / "project-views.js").read_text(encoding="utf-8")
    tip = src[src.index("tip.replaceChildren("):]
    tip = tip[:tip.index("tip.style.display")]
    assert ".filter((x) => x != null)" in tip, \
        "условные строки подсказки не отфильтрованы — в подсказке появится «null»"


def test_run_views_chips_persistence():
    """Выбор чипсов (hidden noenv: types/fields/ner_fields) переживает
    перезагрузку страницы: chipRestore — в initFormValues, saveChips —
    в обработчиках чипсов. Иначе запуск уходил не с теми полями,
    что показаны (перезагрузка сбрасывала выбор на дефолт)."""
    src = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    assert "function chipRestore(" in src
    assert "chipRestore(key, spec, vals)" in src
    assert "store.setItem(chipKey(key)" in src
    # ner_check: дефолт полей, материализованный curFields, — touched
    assert 'st.touched[key].add("fields");' in src
    # форма стадии одна (Простой/Экспертный удалены), кнопка сброса
    # пересчитывается делегатом, а не перестройкой формы; тем же делегатом
    # изменённые поля уходят в состояние книги
    assert "function stageForm(key, spec)" in src
    assert "const onEdit = () => {" in src
    assert "scheduleRunSave(key);" in src
    assert 'panel.addEventListener("input", onEdit);' in src
    assert 'panel.addEventListener("change", onEdit);' in src
    assert "st.baseline[key] = Object.assign({}, vals)" in src


def test_run_views_values_persistence():
    """Изменённые поля формы запусков — состояние КНИГИ (tmp/ui_state): их нет
    ни в .env книги, ни в argv, поэтому без памяти они молча возвращались к
    значениям общего конфига после запуска или перезагрузки страницы. Читает и
    пишет их снимок книги (bookState().store), а не localStorage: смена
    браузера/домена не должна терять параметры книги."""
    src = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    assert "function runKey(key)" in src
    assert 'return `runVals:${section}/${name}:${key}`' in src
    assert "function runSave(key)" in src
    assert "function runRestore(key, spec, vals)" in src
    # чтение — в initFormValues до чипсов; запись — делегатом формы
    assert "runRestore(key, spec, vals)" in src
    assert "store.setItem(runKey(key), JSON.stringify(data))" in src
    # пустое изменение — ключ памяти удаляется, а не оставляется с мусором
    assert "store.removeItem(runKey(key))" in src
    # пишутся только тронутые поля: иначе правка одного поля формы отправляла
    # бы в книгу все значения стадии и затирала чужие (два компьютера видели
    # разные диапазоны глав)
    assert "for (const n of t) if (vals[n] !== undefined) data[n] = vals[n];" in src
    # хранилище — снимок книги (вне загруженного app.js — localStorage)
    assert "bookState(section, name).store" in src
    assert 'const store = typeof bookState === "function"' in src
    assert 'bookState(section, name).store : localStorage;' in src
    # прямых обращений к localStorage в «Запусках» нет: только через снимок
    assert "localStorage." not in src
    # профиль стадии — не «изменённое поле»: у него своя память по стадии
    assert "if (f.name === PROFILE_FIELD) continue;" in src
    # запись отложена на паузу ввода и одна на пачку изменений: на каждое
    # нажатие клавиши писать в память нельзя
    assert "const RUN_SAVE_DELAY = 500;" in src
    assert "clearTimeout(runTimers[key]);" in src
    # отдельной кнопки сохранения в форме нет — состояние показывает подпись
    # в шапке панели (узел постоянный: пересобирать его нельзя)
    assert "function runSaveState(key)" in src
    assert 'class: "run-save-state hidden"' in src
    assert "syncRunSave(key); // подпись сразу показывает" in src
    assert "const btn = st.head && st.head.querySelector" in src


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



def test_app_update_helpers_defined():
    """Регрессия «панель обновления падает в ReferenceError»: каждый вызов
    update*(...) в app.js обязан быть определён в app.js или run-views.js —
    опечатка в имени помощника видна только когда панель реально отрисуется."""
    src = (SPA_DIR / "app.js").read_text(encoding="utf-8") \
        + (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    # имена помощников обновления начинаются с «upd»: имена собираются целиком,
    # иначе опечатка «updBootBox» живёт до момента, когда панель отрисуется
    called = set(re.findall(r"\b(upd[A-Za-z]*)\s*\(", src))
    assert called, "в app.js нет ни одного помощника обновления"
    for name in sorted(called):
        assert re.search(rf"\bfunction {name}\s*\(", src), f"{name} вызывается, но не определена"


def test_app_update_check_rerenders_panel():
    """Проверка обновления по кнопке перерисовывает панель, а не только
    состояние: без перерисовки в открытом меню остаётся «не проверялось»,
    вердикт виден тостом, а итог — только после перезагрузки страницы.
    Пока запрос идёт, кнопка говорит «Проверяю…» и не жмётся второй раз;
    отказ сети виден в панели, а не только в тосте."""
    src = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    body = src[src.index("async function checkUpdates("):]
    body = body[:body.index("\n}\n")]
    assert "refreshUpdateBox()" in body, "панель не перерисовывается итогом"
    assert "finally" in body, "флаг проверки не снимается при ошибке"
    assert "updateChecking" in body, "нет защиты от повторного запуска"
    assert "updateCheckError" in body, "отказ проверки не сохраняется для панели"
    # «Проверяю…» рисуется одними и теми же кнопками панели
    btn = src[src.index("function updateCheckBtn("):]
    btn = btn[:btn.index("\n}\n")]
    assert "Проверяю…" in btn and "disabled" in btn
    # причина отказа — строка панели, а не только тост
    err = src[src.index("function updateErrorBox("):]
    err = err[:err.index("\n}\n")]
    assert "updateCheckError" in err
    menu = src[src.index("function updateMenuItem("):]
    assert menu.count("updateErrorBox()") == 2, "отказ не выводится в обоих состояниях"


def test_app_update_restart_text_from_server():
    """Текст о перезапуске пишет сервер (core/update.py::RESTART_NOTES): способ
    установки его и определяет. В SPA его быть не может — там только показ
    u.restart.note, иначе текст расходится с реальным поведением установки."""
    src = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    body = src[src.index("function updateRestartBox("):]
    body = body[:body.index("\n}\n")]
    assert "u.restart" in body, "помощник не читает u.restart"
    for word in ("start.bat", "compose pull", "перезапустите", "перезапустится сам"):
        assert word not in body, f"в SPA завёлся текст перезапуска «{word}»"


def test_app_update_how_box_has_no_hidden_duplicate():
    """Объяснение способа обновления — одно: у Docker причину называет строка
    о перезапуске (сервер), а подпись способа остаётся только про команду
    хоста. Заголовок панели версию не повторяет: она стоит в футере меню.
    """
    src = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    how = src[src.index("function updateHowBox("):]
    how = how[:how.index("\n}\n")]
    for word in ("сам себя не заменит", "Образ обновляет хост"):
        assert word not in how, f"в подписи способа завёлся дубль «{word}»"
    assert "u.command" in how, "команда хоста не берётся из сводки сервера"
    body = src[src.index("function updateMenuItem("):]
    body = body[:body.index("\n}\n")]
    head = body[body.index("update-title"):]
    head = head[:head.index("));")]
    assert "u.current" not in head and "state.version" not in head, \
        "версия задублирована в заголовке панели"

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


def test_settings_page_autosaves():
    """«Настройки» — страница автосохранения: кнопки «Сохранить» нет, одно
    изменённое поле уходит PUT'ом через полсекунды тишины, идущая запись одна,
    ответ перечитывает model.groups БЕЗ перерисовки вкладки (иначе из поля
    выпадал бы фокус). «Внешний вид» пишет предпочтения прямо по change.

    Накопленные правки живут в очереди UICore.saveQueue: снимок забирается и
    очередь очищается ОДНИМ движением. Это и есть исправление потери правки,
    набранной пока PUT в полёте: прежний код удалял ключи по списку, снятому
    при отправке, и такая правка исчезала на ответе предыдущего запроса."""
    src = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    view = src[src.index("async function viewSettings()"):
               src.index("/* ── Дашборд")]
    assert '"Сохранить"' not in view, "на странице осталась кнопка сохранения"
    assert "const SAVE_DELAY = 500;" in view
    assert "const queue = UICore.saveQueue(SAVE_DELAY);" in view
    assert "queue.due(savePending);" in view
    # один писатель: второй PUT не пересекает идущую запись
    save = view[view.index("async function savePending()"):
                view.index("// ── сброс вкладки")]
    assert "if (queue.isSaving()) return;" in save
    assert "const sent = queue.take();" in save
    assert "if (queue.has()) queue.again();" in save, \
        "хвост, набранный во время записи, должен уйти следом"
    assert "queue.back(sent.values);" in save, "ошибка обязана вернуть правку"
    # ключ строки — ключ .env (имена полей стадий не уникальны)
    assert "const id = f.key || f.name;" in view
    # состояние автосохранения — в шапке страницы; пока сохранять нечего,
    # строка молчит (подтверждение — тост)
    assert 'h("div", { class: "page-header-actions" }, status)' in view
    assert 'class: "settings-save-state hidden"' in view
    assert "model.groups = [...(r.groups || []), uiGroup];" in save
    assert "renderTab()" not in save, "запись пересобирает вкладку — фокус lost"
    # «Внешний вид» — localStorage по change контрола
    assert "function saveLook()" in view
    assert 'el.addEventListener(ev, saveLook);' in view
    css = (SPA_DIR / "styles.css").read_text(encoding="utf-8")
    assert ".run-save-state" in css and ".settings-save-state" in css


def test_settings_page_tab_reset():
    """«Настройки»: на каждой вкладке конфига — кнопка «Сбросить настройки».

    Возвращает поля вкладки к значениям по умолчанию из реестра (дефолт
    приходит в `f.default`, своего знания о настройках у SPA нет) и не трогает
    соседние вкладки. «Внешний вид» — предпочтения браузера, их нет в реестре,
    поэтому там кнопки нет. Запись идёт тем же PUT'ом, что и автосохранение.
    """
    src = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    view = src[src.index("async function viewSettings()"):
               src.index("/* ── Дашборд")]
    assert "function groupDefaults(g)" in view
    assert 'if (f.type === "hidden" || f.noenv) continue;' in view
    # textarea хранит переносы в реестре литералом «\\n» — их разворачиваем
    assert 'String(d).replace(/\\\\n/g, "\\n")' in view
    assert 'f.type === "bool" ? Boolean(d) : String(d);' in view
    reset = view[view.index("async function resetGroup()"):
                 view.index("function tabTools(g)")]
    assert 'if (!g || g.id === "ui") return;' in reset
    # не записанные правки уходят первыми, иначе сброс их затрёт
    assert "if (queue.has() || queue.isSaving()) await savePending();" in reset
    assert "await queue.idle();" in reset
    assert "queue.drop(keys);" in reset
    assert 'method: "PUT", body: { profile: model.profile, values: defaults },' in reset
    assert "renderTab();" in reset  # фокус в модалке — перерисовка безопасна
    # кнопка живёт в теле вкладки, а не в шапке страницы
    tools = view[view.index("function tabTools(g)"):view.index("// контрол поля")]
    assert 'class: "settings-tab-tools"' in tools
    assert '"Сбросить настройки"' in tools
    assert "const out = [tabTools(g)];" in view
    css = (SPA_DIR / "styles.css").read_text(encoding="utf-8")
    assert ".settings-tab-tools" in css


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


def test_settings_profile_edit_is_system_modal():
    """Настройки · профили LLM: имя и удаление — системные модалки.

    Переименование профиля рисует тот же `nameModal`, что у файлов, каталогов
    и разделов (поле с текущим именем + ОК), удаление — `confirmModal` со
    словом «УДАЛИТЬ», как любое опасное действие SPA. Нативный
    `window.prompt`/`window.confirm` — второй слой интерфейса: он не читает
    состояние книги, не показывает ошибку ответа и не жмётся Enter'ом."""
    src = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    assert 'nameModal("Новый профиль LLM", "имя профиля"' in src
    assert "nameModal(`Переименовать профиль ${cur.name}`, \"новое имя\"" in src
    assert 'confirmModal("Удалить профиль"' in src
    for f in ("app.js", "project-views.js", "run-views.js", "ui-components.js"):
        s = (SPA_DIR / f).read_text(encoding="utf-8")
        assert "window.prompt(" not in s, f"{f}: нативный prompt вернулся"
        assert "window.confirm(" not in s, f"{f}: нативный confirm вернулся"


def test_modals_confirm_on_enter():
    """Все модалки жмутся Enter: кнопка действия одна на все вьюхи.

    Соглашение одно: в `.modal-actions` первым идёт «Отмена»/«Закрыть»
    (btn-ghost), последней — дело (btn-primary/btn-danger); именно его и
    нажимает Enter. Многострочное поле и редактор остаются собой (Enter
    печатает перенос), как и кнопка/ссылка внутри окна: там работает штатная
    активация браузера, второй клик не нужен. Окно без панели кнопок
    (просмотр файла) действия не имеет, а «Управлению разделами» она помечена
    вручную. Опасные действия от случайного Enter держит слово-пароль, а от
    двойного — блокировка кнопки на время записи."""
    uic = (SPA_DIR / "ui-components.js").read_text(encoding="utf-8")
    assert "function actionButton(backdrop)" in uic
    assert 'classes(n).includes("modal-actions")' in uic
    assert 'if (e.key !== "Enter" || e.isComposing) return;' in uic
    assert 'const plain = t.isContentEditable || tag === "TEXTAREA";' in uic
    assert 'if (plain || tag === "BUTTON" || tag === "A") return;' in uic
    src = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    assert '"data-modal-default": "1"' in src, "окно разделов без своей кнопки"
    assert "if (go.disabled) return;" in src, "кнопка «Подтвердить» жмётся дважды"


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
    кнопка «Сохранить» не рендерится, действия — иконками, как во вкладке
    «Файлы»: у General — глаз (просмотр) и скачивание."""
    src = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    assert "ed.setReadOnly(readonly)" in src
    assert "const readonly = st.set === \"General\"" in src
    assert 'rowIcon("eye"' in src, "просмотр General — иконка глаза"
    assert "только чтение" in src
    assert "...(readonly ? [] : [saveBtn])" in src


def test_ner_check_rag_ui_present():
    """Запуски ner_check · RAG: видимость полей режима — общий проход по
    метаданным реестра (attachModeVisibility + UICore.fieldApplies), а не
    ручной блок на стадию; кнопка «Добавить спорные» на месте;
    отдельный RAG-промпт-файл убран — RAG-промпт живёт в общем
    «Промпт-файле» (тег <prompt_rag>)."""
    rv = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    # самих RAG-полей в SPA больше нет: они описаны в реестре, а видность
    # считает общий проход по when
    assert "attachModeVisibility" in rv and "UICore.fieldApplies" in rv
    assert "rag_prompt_file" not in rv  # дубль убран
    assert "addDisputedTermsModal" in rv
    assert "Добавить спорные" in rv
    # RAG-промпт — тег <prompt_rag> в общем промпт-файле стадии; метаданные
    # поля (промпт-файл) — в реестре настроек, не в stages.py
    st = (REPO / "core" / "settings.py").read_text(encoding="utf-8")
    assert "ner_check_prompt.txt" in st
    # RAG-поля объявлены в реестре и помечены режимом rag: их прячет не
    # ручной блок в SPA, а when у самой настройки
    for nm in ("RAG_TERMS", "RAG_SOURCE_TYPE", "RAG_BUDGET"):
        assert nm in st, nm
    assert st.count('when=(("passes", ("rag",)),)') >= 4
    # диапазон глав в RAG-режиме скрыт тем же when: он описан у общих полей
    assert 'RANGE_STAGES' in st and '"ner_check": (("passes", ("rag",)),)' in st
    cli = (REPO / "cli" / "ner_check.py").read_text(encoding="utf-8")
    assert "load_rag_prompt(args.rag_prompt_file or args.prompt_file" in cli
    # автоподхват ner_check_prompt.txt остался в общем «Промпт-файле»
    assert "ner_check_prompt.txt" in st and "autofile" in st
    # чипсы типов следуют за скрытым полем types (в RAG они не влияют)
    assert 'mirror(w.chipsBar, "types")' in rv


def test_prompt_edit_button():
    """Запуски (LLM): промпт выбран — «Редактировать» (просмотр/правка/
    сохранение через /api/prompts). Флаг editable в спеке расширяет кнопку на
    текстовые файлы из source/ (compile: метаданные YAML, страница
    поддержки) — те же модалки, но через /api/file. Промпта в книге нет —
    «Из шаблона»: готовый файл копируется из templates/*, свои файлы кладут
    вкладкой «Файлы», где и видны все файлы книги."""
    rv = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    assert "function editFileModal(relPath, opts = {})" in rv
    assert "const isPrompt = dir === \"prompts\"" in rv
    assert "const isEditable = isPrompt || Boolean(f.editable);" in rv
    assert """        upBtn.textContent = sel.value
          ? "Редактировать"
          : isPrompt ? "Из шаблона" : "Загрузить";""" in rv
    assert "/prompts/${encodeURIComponent(fname)}/template" in rv
    assert "/prompts/${encodeURIComponent(relPath)}" in rv
    assert "/file?project=${section}/${name}" in rv  # чтение source-файлов
    assert 'makeEditor(d.content || "", UICore.editorLang(relPath, isPromptFile))' \
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
    """Языки подсветки редакторов — ИМЕНА языков бандла CodeMirror: у промптов
    — собственный язык разметки «prompt» (комментарии, теги секций,
    подстановки {плейсхолдеры}; собирается в бандле StreamLanguage-режимом),
    дефолт выбора «Внешнего вида». Разметку промпта показывает и предпросмотр
    запроса — теги, подстановки и ключи JSON отдельными span-ами."""
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    core = (SPA_DIR / "ui-core.js").read_text(encoding="utf-8")
    pv = (SPA_DIR / "project-views.js").read_text(encoding="utf-8")
    rv = (SPA_DIR / "run-views.js").read_text(encoding="utf-8")
    css = (SPA_DIR / "styles.css").read_text(encoding="utf-8")

    # язык редактора выбирается в UICore: prompts/ — настройка пользователя
    # (дефолт — язык разметки промптов), логи — своя, остальное — расширение
    assert "function editorLang(path, isPrompt)" in core
    assert 'return EDITOR_SETTINGS.langPrompt;' in core
    assert 'return /\\.log$/i.test(String(path || "").split("/").pop() || "")' in core
    assert 'makeEditor("", UICore.EDITOR_SETTINGS.langPrompt)' in pv, \
        "вкладка «Промпты» — язык промпта из выбора пользователя"
    assert "UICore.editorLang(full)" in pv and "UICore.editorLang(full)" in app
    # выбор живёт в localStorage браузера, а не в .env
    assert 'langPrompt: "prompt", langLog: "text",' in core
    # язык для .env книги — properties (KEY=VALUE с «#»)
    for pair in ('xml: "xml"', 'fb2: "xml"', 'env: "properties"',
                 'md: "markdown"', 'json: "json"', 'yaml: "yaml"'):
        assert pair in app, f"в CM_LANG_BY_EXT нет {pair}"
    assert 'if (EDITOR_LANGS.some((o) => o.v === d[k])) EDITOR_SETTINGS[k] = d[k];' in app

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


def test_run_pill_switches_tab_without_hash():
    """Регрессия «клик по индикатору запуска ничего не делает»: вкладки
    проекта меняются состоянием, хэш остаётся тем, чем в проект вошли
    («.../run»). Ссылка pill'а вела на тот же хэш — hashchange не случался.
    Пилл зовёт projectNavigate: тот же проект — смена вкладки напрямую."""
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    pv = (SPA_DIR / "project-views.js").read_text(encoding="utf-8")
    # один запуск — сразу его лог: третий довод адресует «Запуски» к нужному
    assert "projectNavigate(j.project, \"run\", j.id)" in app
    # несколько — карточка выбора, каждый ряд ведёт в свой запуск
    assert "projectNavigate(j.project, \"run\", j.id)" in app
    assert "function projectNavigate(project, view, jobId)" in pv
    # «открыт» — по маршруту: у живого view хэш всегда ведёт тот же проект
    assert 'if (live && r.view === "project"' in pv
    assert "activeProject = { key: `${section}/${name}`, setView };" in pv
    # чужой проект или другой экран — обычный переход ссылкой
    assert 'location.hash = `#/project/${key}/${view}${tail}`;' in pv
    # id запуска едет в маршруте: второй ряд открывает ИМЕННО свой прогон
    assert "const tail = jobId ? `/${jobId}` : \"\";" in pv
    assert "location.hash = `#/project/${key}/${view}${tail}`;" in pv


def test_field_applies_js_matches_python(tmp_path):
    """Поля режима считает одно правило: JS `UICore.fieldApplies` обязан давать
    ровно то же, что `core.settings.applies` — иначе форма врёт о команде."""
    import json
    import sys
    sys.path.insert(0, str(REPO))
    from core import settings as core_settings

    cases = []   # (настройка, её поле формы, значения полей режима)
    for stage in core_settings.STAGES:
        settings = core_settings.stage_fields(stage)
        by_name = {s.name: s for s in settings}
        for s in settings:
            if not (s.when or s.when_any or s.when_set):
                continue
            conds = list(s.when) + list(s.when_any)
            vals = {}
            for n in [c[0] for c in conds] + list(s.when_set):
                g = by_name.get(n)
                if g is None:
                    continue
                if g.type == "bool":
                    vals[n] = (True, False)
                elif g.options:
                    vals[n] = tuple(g.options) + ("",)
                else:
                    vals[n] = ("значение", "")
            base = {k: vs[0] for k, vs in vals.items()}
            for n, vs in vals.items():     # каждое поле режима по очереди
                for v in vs:
                    cases.append((s, {**base, n: v}))
            cases.append((s, base))
            cases.append((s, {}))
    data = tmp_path / "cases.json"
    data.write_text(json.dumps([f.form_field() for f, _ in cases],
                               ensure_ascii=False), encoding="utf-8")
    forms = tmp_path / "forms.json"
    forms.write_text(json.dumps([form for _, form in cases],
                                ensure_ascii=False), encoding="utf-8")
    script = tmp_path / "check.mjs"
    script.write_text(
        "import { createRequire } from 'node:module';\n"
        "import { readFileSync } from 'node:fs';\n"
        "const require = createRequire(process.cwd() + '/');\n"
        "const UICore = require('./web/static/ui-core.js');\n"
        "const read = (p) => JSON.parse(readFileSync(p, 'utf8'));\n"
        "const fs = read(process.argv[2]), vs = read(process.argv[3]);\n"
        "process.stdout.write(fs.map((f, i) => "
        "(UICore.fieldApplies(f, vs[i]) ? '1' : '0')).join(''));\n",
        encoding="utf-8")
    r = _node(str(script), str(data), str(forms))
    assert r.returncode == 0, f"node упал: {r.stderr}"
    got = r.stdout.strip()
    assert len(got) == len(cases), "число кейсов разошлось"
    bad = [(s.stage, s.name, form)
           for (s, form), res in zip(cases, got)
           if res != ("1" if core_settings.applies(s, form) else "0")]
    assert not bad, f"расхождения JS/Python: {bad[:5]}"
    assert len(cases) > 100, f"мало кейсов: {len(cases)}"


def test_hub_archive_refreshes_after_job():
    """Регрессия «после архивации карточка ещё показывает Открыть/Запуски»:
    запуск архивации асинхронный, и хаб обязан дождаться его и перечитать
    списки, а не ждать ручного F5. Пока работа идёт, карточка говорит
    правду («идёт архивация…»), а не показывает готовую книгу."""
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    # ожидание запусков: опрос /jobs/active по действию archive
    assert "async function waitArchiveJobs()" in app
    assert 'api("/jobs/active")' in app
    assert 'String(j.action) === "archive"' in app
    # после старта запуска хаб перечитывается целиком (списки разделов и
    # архивы приходят разными запросами)
    assert "await waitArchiveJobs();" in app
    assert "function refreshHub() {\n  hubCache = null;\n  render();\n}" in app
    # «идёт архивация…» вместо строки статистики
    assert "let hubArchiving = new Set();" in app
    assert 'text: busy ? "идёт архивация…" : statsLine' in app


def test_move_asks_pack_only_for_archive_section():
    """Перенос в архивный раздел спрашивает «закрыть книгу или оставить
    каталогом», а в обычный — не спрашивает вовсе: вопрос ни о чём был бы
    лишним шагом. Флаг приходит из `/api/sections` (`pack_on_move` —
    следствие `archive`), в SPA своего списка разделов-архивов нет."""
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    assert "function asksPack(hub, dst)" in app
    assert "sec.pack_on_move" in app
    assert "function packChoice()" in app
    assert '"Оставить каталогом"' in app
    assert '"Закрыть книгу и освободить место"' in app
    # вопрос включается выбором раздела в обоих окнах переноса
    assert "syncPackChoice(choice, hub, dstSel.value)" in app
    assert "syncPackChoice(choice, hubCache, dstSel.value)" in app
    # согласие уезжает на сервер отдельным полем, а не выводится там заново
    assert "body: { dst: dstSel.value, items, pack: choice.pack }" in app
    assert "body: { section, name, dst: dstSel.value, pack: choice.pack }" in app


def test_hub_cards_carry_no_actions():
    """Карточка не несёт кнопок вовсе: у живой книги открытие делает клик по
    карточке, у упакованной — кнопки внизу спорили за место с подписью
    статистики и у короткой книги занимали половину высоты. Архивирование,
    распаковка и скачивание живут в «Управлении книгой» (шестерёнка), а
    скачать архив можно ещё с экрана упакованной книги."""
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    block = app[app.index("function sectionBlock("):app.index("async function openManage(")]
    assert "card-actions" not in block
    assert "openBook" not in block and "runs" not in block
    assert "unarchiveOne" not in block and "archive/download" not in block
    # групповые действия объекта — в панели выделения (как у «Файлов»)
    assert '"Управление"' in app and "openManage(section, name)" in app


def test_hub_card_opens_project_and_gear_manages():
    """Карточка проекта целиком — ссылка в книгу, а «Открыть»/«Запуски» из
    неё убраны: из карточки всегда приходили в один и тот же проект. Ссылка
    лежит отдельным слоем (.card-open), потому что чекбокс и шестерёнка
    внутри <a> недопустимы, а по пустому месту карточки кликать тоже надо.
    Шестерёнка в углу — «Управление книгой»: иконка без подписи."""
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    block = app[app.index("function sectionBlock("):app.index("async function openManage(")]
    # ссылка на всю карточку, а не на имя
    assert 'class: "card-open"' in block
    assert '"#/project/' not in block  # шаблон адреса собирается encodeURIComponent-ами
    assert "encodeURIComponent(section.name)}/${encodeURIComponent(name)}`" in block
    # шестерёнка: только иконка, с подписью в тултипе и aria-label
    assert 'class: "btn btn-sm btn-ghost icon-btn card-gear"' in block
    assert 'iconEl("settings")' in block
    assert '`Управление книгой «${name}»: архив, перенос, переименование, копия, удаление`' in block
    assert 'e.stopPropagation();' in block
    # принадлежность разделу с карточки убрана — раздел и так виден
    assert "card-sub" not in block


def test_hub_selection_bar_sits_in_header():
    """Панель выделения занимает место кнопок шапки, а не отдельную строку
    под ней: раньше она появлялась под шапкой и сдвигала вниз все карточки
    — раскладка прыгала от одного клика по чекбоксу."""
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    hub = app[app.index("function selectionBar()"):app.index("async function viewHub()")]
    assert 'class: "page-header-actions selection-bar"' in hub
    # шапка выбирает одно из двух: панель или кнопки тулбара
    view = app[app.index("async function viewHub()"):app.index("/* ── файловый менеджер")]
    assert "hubSelection.size\n        ? selectionBar()" in view
    assert "＋ Проект" in view and "Управление разделами" in view
    # снятие выделения возвращает тулбар, а не пустое место
    assert "hubSelection = new Set();\n          render();" in hub


def test_hub_section_has_select_all_instead_of_archive_all():
    """У раздела одна кнопка «Выделить все»: упаковать или распаковать пачку
    решает панель выделения, где эти действия уже есть. Отдельные
    «Архивировать все»/«Разархивировать все» дублировали её и заранее
    выбирали вид операции за человека."""
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    assert "Архивировать все" not in app and "Разархивировать все" not in app
    assert "section-tools" not in app
    # серверный роут раздела остаётся рабочим API — убран только вызов из UI
    assert "async function archiveSection" not in app
    block = app[app.index("function sectionBlock("):app.index("async function openManage(")]
    assert 'class: "btn btn-sm btn-ghost section-select-all"' in block
    assert '"Выделить все"' in block and '"Снять выделение"' in block
    # кнопка отмечает весь раздел, а не пачку «по одной операции»
    assert "for (const n of projects) hubSelection.add(" in block
    assert "const allSelected = projects.length > 0" in block


def test_archive_selected_uses_server_answer():
    """Подтверждение пачки — ответ сервера, а не наш список: «Выделить все»
    отмечает раздел целиком, и книга, уже лежащая в нужном виде, в работу не
    попадает. Иначе тост обещал «Упаковать: 3», хотя упаковали одну."""
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    fn = app[app.index("async function archiveSelected("):
             app.index("/* После создания запуска архивации:")]
    assert "const done = r.items || [];" in fn
    assert "await afterArchiveStart(r, done, verb);" in fn
    # пустой ответ — причина из skipped, а не пустой тост
    assert "reasons.length ?" in fn and "s.reason" in fn


def test_hub_card_click_reaches_text():
    """Клик по ТЕКСТУ карточки открывает книгу: почти вся её площадь занята
    текстом, и пока содержимое лежало над ссылкой-слоем, кликабельным
    оставалось только пустое место у края. Текст пропускает клик насквозь,
    а контролы (чекбокс, шестерёнка, действия) возвращают его себе."""
    css = (SPA_DIR / "styles.css").read_text(encoding="utf-8")
    card = css[css.index(".project-card > *:not(.card-open)"):
               css.index("/* Подсветка карточки при наведении")]
    assert "pointer-events: none;" in card
    assert ".project-card .card-check," in card
    assert ".project-card .card-gear {" in card
    assert "pointer-events: auto;" in card
    # подсветка при наведении — от карточки: слой под текстом не ловит :hover
    assert ".project-card:has(.card-open:hover)" in css


def test_selection_bar_buttons_are_plain():
    """В панели выделения одна операция не главная: «Архивировать» была
    зелёной (btn-primary) и читалась как «правильный ответ», хотя рядом
    равноправное «Разархивировать»."""
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    bar = app[app.index("function selectionBar()"):app.index("async function viewHub()")]
    assert "btn-primary" not in bar
    assert bar.count('"btn btn-sm btn-ghost"') >= 4


def test_buttons_center_their_label():
    """Подпись кнопки стоит по центру самой кнопки. База .btn — inline-flex,
    и без justify-content кнопка, растянутая шире текста (на телефоне
    .modal-actions даёт .btn flex: 1 1 auto, .btn-block и .update-btn —
    width: 100%), оставляла подпись у левого края. Строки-списки, которым
    нужен левый край, гасят это своим правилом — иначе центрирование
    возвращается к ним через ту же базу."""
    css = (SPA_DIR / "styles.css").read_text(encoding="utf-8")
    assert ".btn {" in css
    base = css[css.index("\n.btn {"):]
    base = base[:base.index("\n}")]
    assert "display: inline-flex;" in base
    assert "justify-content: center;" in base
    # широкие кнопки без своего правила — на них проверялось смещение
    assert ".btn-block {\n  width: 100%;\n}" in css
    assert ".update-btn {\n  width: 100%;\n}" in css
    assert ".modal-actions .btn {\n    flex: 1 1 auto;\n  }" in css
    # строки-списки и меню читаются от левого края — это их правило, а не бага
    for sel in (".toolbar-menu .menu-box > *,\n.user-menu > *",
                ".prompt-item",
                ".stage-card",
                ".search-file-path"):
        assert sel + " {" in css, sel
        rule = css[css.index(sel + " {"):]
        rule = rule[:rule.index("\n}")]
        assert "justify-content: flex-start;" in rule, sel


def test_run_form_buttons_span_width_with_centered_text():
    """Кнопки внизу формы запуска — во всю ширину формы, подпись по центру
    (её центрирует база .btn); сужать саму кнопку нельзя — две полосы во всю
    ширину и были задуманы."""
    css = (SPA_DIR / "styles.css").read_text(encoding="utf-8")
    # собственного правила у формы нет: центрирование живёт в базе .btn
    assert ".run-form > .btn {" not in css
    # ширину даёт растяжка flex-колонки: собственного сужения быть не должно
    assert "align-self: center;" not in css


def test_manage_modal_archive_and_long_name():
    """«Управление книгой»: имя — подписью, а не в заголовке (длинное
    название распирало окно по горизонтали и заставляло прокручивать его
    вбок до кнопок), и кнопка архивации по текущему состоянию книги.
    Кнопки внизу карточки за место с подписью не спорят."""
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    fn = app[app.index("function manageProjectModal("):
             app.index("/* Экспорт глоссария")]
    assert 'h("div", { class: "modal-sub" }' in fn
    assert "`${section} / ${name}`" in fn
    assert 'title: "Управление книгой"' in fn
    # подпись кнопки решает ответ по архиву, а не догадка
    assert "/archive`)" in fn
    assert 'archived ? "Разархивировать" : "Архивировать"' in fn
    assert 'body: { op, items: [{ section, name }] }' in fn
    # статистику у упакованной книги не спрашиваем: папки нет, /stats → 404
    assert fn.index("archived = !!(r && r.archived);") < fn.index("projectStats(section, name)")
    # после работы кнопка перечитывает диск: окно остаётся открытым, а
    # подпись меняется сразу — close из build в этом обработчике недоступен
    # (на его месте оказался бы window.close, который вкладку не закрывает)
    assert "async function syncArchiveState()" in fn
    assert fn.count("await syncArchiveState();") == 1
    assert fn.index("await afterArchiveStart(r, r.items, verb);") \
        < fn.index("await syncArchiveState();")

    css = (SPA_DIR / "styles.css").read_text(encoding="utf-8")
    assert ".modal-sub {" in css and "overflow-wrap: anywhere;" in css
    # ряд кнопок переносится и не распирает окно вбок
    row = css[css.index(".manage-row {"):css.index("[hidden] {")]
    assert "flex-wrap: wrap;" in row
    assert ".manage-row .field {\n  flex: 1;" in css
    assert "min-width: 0;" in css[css.index(".manage-row .field {"):][:120]


def test_hub_create_button_label():
    """Кнопка создания проекта — «＋ Проект»: шапка хаба держит рядом два
    действия, и длинная подпись занимала строку целиком. Проба интерфейса
    ищет её по тексту — совпадение обязательно."""
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    assert '"＋ Проект"' in app
    assert '"＋ Создать проект"' not in app
    probe = (REPO / "tools" / "ui_probe.mjs").read_text(encoding="utf-8")
    assert "＋ Проект" in probe and "Создать проект" not in probe


def test_check_reports_have_trash_icon():
    """У отчётов translate_check в «Проверках» → «Перевод» есть корзина —
    как у логов: отчёт лежит файлом в logs/, удалить его можно там, где он
    виден, а не только пачкой «Очистить»."""
    pv = (SPA_DIR / "project-views.js").read_text(encoding="utf-8")
    fn = pv[pv.index("async function renderCheckReports("):
            pv.index("/* ── Оценка перевода (LLM)")]
    assert 'iconBtn("trash", "Удалить отчёт"' in fn
    assert "`/file?${dq}&path=" in fn
    assert "logs/${r.name}" in fn
    # строка списка — кнопка + корзина (общий стиль prompt-item-row)
    assert 'class: "prompt-item-row"' in fn
    # удаление идёт через подтверждение, а не сразу по клику
    assert 'confirmModal(' in fn and '"УДАЛИТЬ"' in fn
    # счётчик и «Очистить» обновляются вместе со списком: после удаления
    # одного отчёта подпись «N отчёт(ов)» обязана врать перестать
    assert "function syncToolbar()" in fn
    assert fn.count("syncToolbar();") == 2  # вызовы: удаление одного и «Очистить»
    assert "count.textContent = `${n} отчёт(ов)`" in fn


def test_archived_project_opens_with_explanation():
    """Прямая ссылка на упакованную книгу (закладка, «Назад») раньше давала
    вкладки с 404: папки нет. Теперь — экран «Книга упакована» с распаковкой.
    Проверка архива обязана идти ДО вкладок."""
    pv = (SPA_DIR / "project-views.js").read_text(encoding="utf-8")
    assert "async function loadArchiveInfo()" in pv
    assert "async function archivedPage(info)" in pv
    assert "/archive`);" in pv
    # проверка стоит перед выбором вкладки: иначе вкладка успела бы сходить
    # в несуществующую папку
    i_check = pv.index("const arcinfo = await loadArchiveInfo();")
    i_tabs = pv.index('if (st.edit) body = await editorView();')
    assert i_check < i_tabs
    # у экрана нет вкладок проекта, зато есть распаковка и скачивание
    page = pv[pv.index("async function archivedPage(info)"):i_check]
    assert '"Разархивировать"' in page and "archive/download" in page
    assert '"К списку книг"' in page


def test_sections_modal_is_wide_and_named():
    """Регрессия «в окне «Управление разделами» не видно названий разделов»:
    narrow-окно 420 px с тремя текстовыми кнопками в строке выдавливало
    имена за край. Окно стало wide, действия строки — иконки с подсказкой."""
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    css = (SPA_DIR / "styles.css").read_text(encoding="utf-8")
    modal = app[app.index("function sectionsModal()"):]
    modal = modal[:modal.index("\nasync function viewTemplates()")]
    assert "wide: true" in modal
    # строки рисуются иконками: подпись живёт в тултипе и aria-label
    assert 'class: "hub-sections-modal"' in modal
    assert "function rowIcon(icon, tip, onclick, cls)" in modal
    # у списка разделов есть собственный стиль: иначе длинный список
    # растягивал окно на всю высоту
    assert ".hub-sections-modal {" in css
    assert "max-height: 52vh" in css


def test_files_upload_goes_to_current_folder():
    """Регрессия «сброс файлов всегда уносит их в tmp»: пустой путь — это
    КОРЕНЬ книги (папка проекта — такая же папка), а не повод уходить в
    служебный `tmp/`. Каталог перетащенного дерева — под текущей папкой."""
    pv = (SPA_DIR / "project-views.js").read_text(encoding="utf-8")
    start = pv.index("async function uploadFiles(files)")
    body = pv[start:pv.index("return { saved };", start)]
    assert "const dest = st.path;" in body
    assert 'st.path || "tmp"' not in body
    # подсказка при перетаскивании называет ту же папку
    assert '"Загрузить в " + (st.path || "корень книги")' in pv


def test_files_upload_collision_modal_lists_paths():
    """Пересечение имён: модалка со списком путей и выбором «Заменить»,
    а не одна строка «уже есть: …» — по ней нельзя понять, что заменяется
    (одинаковые имена в разных папках — разные файлы)."""
    pv = (SPA_DIR / "project-views.js").read_text(encoding="utf-8")
    assert "function overwriteModal(paths)" in pv
    modal = pv[pv.index("function overwriteModal(paths)"):]
    modal = modal[:modal.index("function toggleSel(")]
    assert "upload-collide" in modal
    # путь с папкой: корень книги — без ведущего слэша
    assert "function logicalPath(dest, fname)" in pv
    assert '"Заменить " + paths.length' in modal.replace("`Заменить ${paths.length}`", '"Заменить " + paths.length') \
        or "Заменить ${paths.length}" in modal
    # опасное действие — danger-кнопка, отмена идёт первой (Enter — на ней)
    assert "btn btn-danger" in modal and '"data-modal-default": "1"' in modal


def test_hub_batch_move_between_sections():
    """Выделение в «Проектах»: массовый перенос книг между разделами."""
    app = (SPA_DIR / "app.js").read_text(encoding="utf-8")
    assert "function moveManyModal(items)" in app
    assert '"Перенести все выделенные книги в другой раздел"' in app
    assert "async function openMoveMany()" in app
    assert 'api("/projects/move-many"' in app
    # после переноса выделение снимается и хаб перечитывается
    body = app[app.index("async function openMoveMany()"):]
    body = body[:body.index("\nfunction sectionBlock(") if "\nfunction sectionBlock(" in body else 900]
    assert "hubSelection = new Set();" in body and "refreshHub();" in body

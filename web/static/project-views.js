/* поллинг завершения применений проверок (вкладка «Проверка»):
   ключ `${project}:${kind}` → {timer, jobId}; повторный запуск гасит
   старый watcher того же ключа (не плодим интервалы). */
const _reviewWatchers = new Map();
/* Размер страницы таблиц глоссария (вкладка «Глоссарий») и отчётов
   проверок (вкладка «Проверка»). Был глобалом app.js и пропал при
   рефакторинге (c60ac00) — без него вкладки падают с ReferenceError. */
const PAGE_SIZE = 200;
/* артефакты глав в каноническом порядке цепочки — кнопка «Только главы»
   на вкладке «Поиск» (ключи группы совпадают с core/search.py) */
const CHAPTER_SCOPES = ["chapter", "translated", "redacted", "polished"];
/* Кнопка-иконка: SVG без подписи, поэтому имя действия живёт в тултипе
   и в aria-label (строки списка файлов узкие — текстовые кнопки в них
   не умещаются). danger — окраска опасных действий. */
function iconBtn(name, tip, onclick, danger) {
  const b = h(
    "button",
    {
      class:
        "btn btn-sm btn-ghost icon-btn" + (danger ? " btn-danger-ghost" : ""),
      title: tip,
      "aria-label": tip,
      onclick,
    },
    iconEl(name),
  );
  attachTooltip(b, tip);
  return b;
}

/* Открыть панель поиска CodeMirror с готовым запросом (из «Поиска» и из
   карточек «Проверок»): панель живёт в самом редакторе, поэтому запрос
   вписывается в её поле и коммитится событием «change» — ровно как если бы
   его набрали руками. Кнопка-лупа тулбара открывает ту же панель пустой. */
function openFind(ed, text) {
  if (!ed || !ed.isCM || !text) return;
  const CM = window.CM;
  if (!CM || !CM.openSearchPanel) return;
  CM.openSearchPanel(ed.view);
  const field = ed.view.dom.querySelector(
    '.cm-panel.cm-search [main-field="true"]',
  );
  if (!field) return;
  field.value = String(text);
  field.dispatchEvent(new Event("change", { bubbles: true }));
  field.focus();
}

/* вкладки проекта — реестр (кей и подпись): рендер вкладок и палитра
   Ctrl+K (app.js); ключ «files» — вкладка по умолчанию */
const PROJECT_TABS = [
  ["files", "Файлы"],
  ["run", "Запуски"],
  ["editor", "Редактор"],
  ["ner", "Глоссарий"],
  ["review", "Проверки"],
  ["chapters", "Главы"],
  ["search", "Поиск"],
  ["status", "Статус"],
  ["config", "Настройки"],
  ["prompts", "Промпты"],
  ["logs", "Логи"],
  ["notes", "Заметки"],
  ["history", "История"],
];

/* Живой view открытого проекта: {key, setView}. Вкладки проекта — локальное
   состояние: хэш остаётся тем, чем в проект вошли («.../run»), поэтому ссылке
   «на ту же вкладку» менять нечего и hashchange не случается */
let activeProject = null;

/* Навигация по вкладкам проекта из шапки (индикатор запусков): проект открыт —
   переключаем вкладку напрямую, иначе ведём ссылкой. «Открыт» сверяем по
   маршруту: у живого view хэш всегда ведёт тот же проект, а вкладка в нём —
   какая угодно */
function projectNavigate(project, view) {
  const key = String(project || "");
  const r = UICore.parseRoute(location.hash);
  const live = activeProject;
  if (live && r.view === "project" && r.rest.slice(0, 2).join("/") === key) {
    live.setView(view);
    return;
  }
  location.hash = `#/project/${key}/${view}`;
}

/* eslint-disable-next-line no-unused-vars -- глобал SPA, вызывается из app.js */
function viewProject(section, name, tab, job) {
  /* Последнее состояние вкладок книги живёт в браузере одним ключом
     (nmTab:<раздел>/<книга>): «Редактор» помнит главу и обе панели, «Главы» —
     тип файлов, «Логи» — папку и открытый файл, «Заметки» и «Промпты» —
     открытый документ. Раньше это терялось при перезагрузке страницы.
     Хранилище — предпочтение браузера, не конфиг книги (AGENTS §7) */
  const pref = UICore.projectPrefs(localStorage, section, name);
  const st = {
    view: "files", // tab (третий сегмент роута) может открыть свою вкладку
    path: pref.get("files").path || "",
    edit: null,
    ner: null,
    review: {},
    search: null,
    nerAll: null, // из «Поиска»: глоссарий открывается со всеми столбцами
    editor: null, // вкладка «Редактор» (глава/панели/подсветка)
    chaptersType: pref.get("chapters").type || "polished", // тип файлов «Глав»
    logPath: pref.get("logs").path || "", // папка логов и открытый в ней файл
    logFile: pref.get("logs").file || "",
    runJob: null, // jobId из роута (лог конкретного запуска на «Запусках»)
    filesSort: localStorage.getItem("filesSort") || "name", // name | mtime | size
    filesAsc: localStorage.getItem("filesAsc") !== "0",
    filesSel: new Set(), // выделенные пути «Файлов» (состояние вкладки)
    nerSel: new Set(), // выделенные записи глоссария (объекты data.items)
    filesPage: pref.get("files").page || 0, // страница: выделение её не сбрасывает
  };
  const page = h("div", { class: "page" });

  /* папка логов — тоже состояние вкладки; открытый файл при переходе в другую
     папку сбрасывается: он был файлом прошлой папки */
  function setLogPath(path) {
    st.logPath = path || "";
    st.logFile = "";
    pref.set("logs", { path: st.logPath, file: "" });
  }

  function setPath(path) {
    st.path = path || "";
    st.edit = null;
    st.search = null;
    // выделение и страница — про конкретную папку: при переходе сбрасываем
    st.filesSel.clear();
    st.filesPage = 0;
    pref.set("files", { path: st.path, page: 0 });
    render();
  }

  /* состояние вкладки «Редактор» — одно на проект: повторный рендер вкладки
     не пересоздаёт CodeMirror и не теряет несохранённые правки. Открытое
     раньше возвращается из localStorage: значащие поля проверяются по месту
     (глава сверяется со списком, тип файла — с вариантами главы) */
  function editorState() {
    if (!st.editor) {
      const p = pref.get("editor");
      const ngram = parseInt(p.ngram, 10);
      let threshold = parseFloat(String(p.threshold).replace(",", "."));
      threshold = Number.isFinite(threshold)
        ? Math.min(1, Math.max(0, threshold))
        : 0.75;
      st.editor = {
        chapter: p.chapter || null,
        // one | two — по умолчанию две панели (оригинал+перевод)
        mode: p.mode === "one" ? "one" : "two",
        left: { type: p.left || null, text: null, dirty: false },
        right: { type: p.right || null, text: null, dirty: false },
        // подсветка терминов глоссария — по умолчанию включена
        hl: p.hl !== false,
        ngram: Number.isFinite(ngram) ? Math.min(6, Math.max(1, ngram)) : 3,
        threshold, // размер n-граммы и порог — аналоги --ner_ngram/--ner_threshold
        ner: null, // кеш {items, matcher} глоссария
        find: null, // {type, q}: открыть панель поиска с этим запросом
        panes: null, // кеш панелей — повторный рендер не теряет правки
        wrap: null, // DOM вкладки: пока открыт проект; F5 — то же состояние
      };
    }
    return st.editor;
  }

  /* «Редактор» помнит главу, панели, режим и подсветку: пишем при каждой
     смене — перезагрузка страницы возвращает пользователя в то же место */
  function saveEditorPref() {
    const ed = st.editor;
    if (!ed) return;
    pref.set("editor", {
      chapter: ed.chapter,
      mode: ed.mode,
      hl: !!ed.hl,
      ngram: ed.ngram,
      threshold: ed.threshold,
      left: ed.left.type,
      right: ed.right.type,
    });
  }

  /* Открыть файл результата: артефакт главы — во вкладке «Редактор» на его
     главе (глава, её файл и запрос в панели поиска), остальное — обычным
     файловым редактором. find — строка поиска, которой предзаполнить панель. */
  function openEditor(full, find) {
    const seg = String(full || "").split("/");
    if (seg.length === 3 && seg[0] === "chapters") {
      const ed = editorState();
      ed.wrap = null; // глава другая — вкладку собираем заново
      ed.chapter = seg[1];
      saveEditorPref();
      // имя файла результата и есть тип артефакта панели (канон — с .txt)
      ed.left.type = seg[2];
      ed.left.text = null;
      ed.left.dirty = false;
      ed.find = find ? { type: seg[2], q: String(find) } : null;
      st.edit = null;
      st.search = null;
      st.view = "editor";
      render();
      return;
    }
    st.edit = full;
    st.search = find ? String(find) : null;
    render();
  }

  /* Смена вкладки — локальный рендер (pi-navigate из app.js не диспатчится:
     view тот же); старому телу — сигнал ухода (run-views гасит SSE-стрим,
     скрытые таймеры). find — запрос, с которым вкладка открывается
     (из «Поиска» — в «Глоссарий»); all — открыть глоссарий со ВСЕМИ
     столбцами: искателю нужно значение поля, а не три колонки по умолчанию */
  function setView(view, find, all) {
    if (view === st.view && !all && (!find || st.search === find)) return;
    const body = page.querySelector(".project-body");
    if (body) body.dispatchEvent(new CustomEvent("pi-navigate", { bubbles: true }));
    st.view = view;
    st.edit = null;
    st.search = find ? String(find) : null;
    st.nerAll = all ? 1 : null;
    st.nerSel.clear(); // выделение — про конкретную вкладку
    render();
  }
  activeProject = { key: `${section}/${name}`, setView };

  const TABS = PROJECT_TABS;
  // роут #/project/раздел/книга/<вкладка>[/<jobId>] — открыть конкретную
  // вкладку (например, #/.../logs из чипсов логов); job (4-й аргумент из
  // роутера, короткий #/run/…/<id>) — лог конкретного запуска
  if (tab && TABS.some((t) => t[0] === tab)) {
    st.view = tab;
    if (tab === "run") st.runJob = job || null;
  } else {
    /* ссылка ведёт на книгу целиком (без вкладки) — открываем последнюю */
    const last = pref.get("page").view;
    if (TABS.some((t) => t[0] === last)) st.view = last;
  }

  async function render() {
    /* последняя вкладка книги запоминается и при открытии по ссылке: ссылка —
       тоже способ оказаться на вкладке */
    pref.set("page", { view: st.view });
    page.replaceChildren();
    const header = h(
      "div",
      { class: "page-header" },
      h(
        "div",
        { class: "page-header-main" },
        h("h1", { class: "page-title" }, name),
        h("div", { class: "page-sub" }, `${section} · проект`),
      ),
    );
    const tabs = h(
      "div",
      { class: "tabs" },
      TABS.map(([key, label]) =>
        h(
          "button",
          {
            class: "tab" + (st.view === key ? " tab-active" : ""),
            onclick: () => setView(key),
          },
          label,
        ),
      ),
    );
    let body;
    if (st.edit) body = await editorView();
    else if (st.view === "editor") body = await editorTabView();
    else if (st.view === "run")
      body = window.viewRun(section, name, st.runJob || undefined);
    else if (st.view === "ner") body = await nerView();
    else if (st.view === "review") body = await reviewView();
    else if (st.view === "chapters") body = await chaptersView();
    else if (st.view === "search") body = await searchView();
    else if (st.view === "history") body = await historyView();
    else if (st.view === "status") body = await statusView();
    else if (st.view === "config") body = await configView();
    else if (st.view === "prompts") body = await promptsView();
    else if (st.view === "logs") body = await logsView();
    else if (st.view === "notes") body = await notesView();
    else body = await filesView();
    page.append(header, tabs);
    if (body) {
      // якорь для pi-navigate при локальной смене вкладки (setView):
      // run-views гасит SSE-стрим своего экземпляра по этому событию
      body.classList.add("project-body");
      page.append(body);
    }
    // одиночный файл: после монтирования редактора открыть поиск с запросом
    if (st.edit && st.search) {
      const q = st.search;
      st.search = null;
      openFind(st._ed, q);
    }
  }

  function downloadUrl(full) {
    const q = new URLSearchParams({
      project: `${section}/${name}`,
      path: full,
    });
    return `/api/download?${q}`;
  }

  /* Скачивание одного файла: ссылка с download. Строка списка забирает файл
     сама, панель выделения — каждый из выделенных с паузой. */
  function downloadFile(full, name) {
    const a = h("a", { href: downloadUrl(full), download: name });
    document.body.append(a);
    a.click();
    a.remove();
  }

  /* «Файлы»: список с ВЫДЕЛЕНИЕМ. Строка — чекбокс, имя, метаданные и кнопки
     одного объекта: файл — править, скачать, копировать и переименовать,
     каталог — копировать и переименовать. Скачивание, перенос и удаление
     применяются к выделке и живут в панели выделения: пока что-то выделено,
     она заменяет кнопки тулбара. Выделение — состояние вкладки (не
     localStorage): снимается при смене папки и после операции; чекбокс
     строки перерисовывает только панель — список и его страница от клика
     выделения не сбрасываются. */
  async function filesView() {
    const q = new URLSearchParams({ project: `${section}/${name}` });
    if (st.path) q.set("path", st.path);
    let data;
    try {
      data = await api(`/files?${q}`);
    } catch (ex) {
      return h("div", { class: "files-empty" }, ex.message);
    }
    const sel = st.filesSel;
    const entries = sortFiles(data.entries || []);
    // дерево каталогов проекта: по нему перенос выбирает папку назначения
    const dirs = data.dirs || [];
    const path = (e) => (st.path ? `${st.path}/${e.name}` : e.name);
    const picked = () => entries.filter((e) => sel.has(path(e)));

    const crumbs = h("div", { class: "crumbs" });
    const walk = [];
    crumbs.append(crumb(`${section}/${name}`, () => setPath("")));
    for (const p of st.path ? st.path.split("/") : []) {
      walk.push(p);
      const target = walk.join("/"); // snapshot: замыкание не мутирует
      crumbs.append(h("span", { class: "crumb-sep" }, " / "));
      crumbs.append(crumb(p, () => setPath(target)));
    }
    const upInput = h("input", {
      type: "file",
      multiple: true,
      class: "hidden",
    });
    /* чекбокс «выделить всё» — первой колонкой тулбара, над чекбоксами строк */
    const allCb = h("input", {
      type: "checkbox",
      class: "fsel",
      "aria-label": "Выделить всё в папке",
    });
    allCb.addEventListener("change", () => {
      sel.clear();
      if (allCb.checked) entries.forEach((e) => sel.add(path(e)));
      render();
    });
    /* «＋ Файл» — создать пустой файл и открыть редактор;
       «＋ Каталог» — POST /api/mkdir */
    const addFileBtn = h(
      "button",
      {
        class: "btn btn-sm",
        onclick: () =>
          nameModal(
            "Новый файл",
            "путь внутри проекта, напр. prompts/x.txt",
            async (rel) => {
              await api("/file", {
                method: "PUT",
                body: {
                  project: `${section}/${name}`,
                  path: rel,
                  content: "",
                },
              });
              toast(`Создан: ${rel}`);
              openEditor(rel);
            },
          ),
      },
      "＋ Файл",
    );
    const addDirBtn = h(
      "button",
      {
        class: "btn btn-sm",
        onclick: () =>
          nameModal(
            "Новый каталог",
            "путь внутри проекта, напр. tmp/extra",
            async (rel) => {
              const mq = new URLSearchParams({
                project: `${section}/${name}`,
                path: rel,
              });
              await api(`/mkdir?${mq}`, { method: "POST" });
              toast(`Создан каталог: ${rel}`);
              render();
            },
          ),
      },
      "＋ Каталог",
    );
    const tools = h(
      "div",
      { class: "files-tools" },
      h(
        "button",
        { class: "btn btn-sm", onclick: () => upInput.click() },
        "Загрузить",
      ),
      addFileBtn,
      addDirBtn,
    );
    const selBar = h("div", { class: "files-sel hidden" });
    const toolbar = h(
      "div",
      { class: "files-toolbar" },
      allCb,
      crumbs,
      h("span", { class: "spacer" }),
      sortControl(),
      tools,
      selBar,
    );
    upInput.addEventListener("change", async () => {
      try {
        const r = await uploadFiles(upInput.files);
        if (r) {
          toast(`Загружено: ${r.saved.length} файл(ов)`);
          render();
        }
      } catch (ex) {
        toast(ex.message, "err");
      }
    });
    async function uploadFiles(files) {
      const dest = st.path || "tmp";
      // перезапись существующих файлов — с подтверждением
      const names = [...files].map((f) => f.name).filter(Boolean);
      let existing = [];
      try {
        const d = await api(
          `/files?project=${encodeURIComponent(`${section}/${name}`)}` +
          `&path=${encodeURIComponent(dest)}`,
        );
        existing = (d.entries || []).map((e) => e.name);
      } catch {
        /* папки ещё нет — всё новое */
      }
      const collide = names.filter((n) => existing.includes(n));
      if (collide.length) {
        const ok = await confirmModal(
          "Загрузка с перезаписью",
          `В ${dest} уже есть: ${collide.join(", ")}. Заменить этими файлами?`,
          "ПЕРЕЗАПИСАТЬ",
          async () => {},
        );
        if (!ok) return null;
      }
      const form = new FormData();
      form.append("dest", dest);
      for (const f of files) form.append("files[]", f, f.name);
      return apiUpload(`/upload?project=${section}/${name}`, form);
    }

    function toggleSel(p, on) {
      if (on) sel.add(p);
      else sel.delete(p);
      // строку подсвечиваем на месте: полная перерисовка списка из-за клика
      // сбросила бы фокус строки (с неё же Space-просмотр)
      for (const r of drop.querySelectorAll(".frow")) {
        if (r.getAttribute("data-name") === p) r.classList.toggle("frow-sel", on);
      }
      paintSel();
    }

    /* Панель выделения — только то, что применимо к выделке: переименовать
       имеет смысл при одном объекте, скачать — когда в выделке есть файлы. */
    function paintSel() {
      const n = sel.size;
      tools.classList.toggle("hidden", n > 0);
      selBar.classList.toggle("hidden", n === 0);
      allCb.checked = entries.length > 0 && n === entries.length;
      allCb.indeterminate = n > 0 && n < entries.length;
      if (!n) return;
      const one = n === 1 ? entries.find((e) => sel.has(path(e))) : null;
      const hasFile = entries.some((e) => !e.dir && sel.has(path(e)));
      /* null в replaceChildren — не «пропустить», а вставить текст «null»:
         панель выделения каталогов читалась бы «выделено: 2nullnull» */
      selBar.replaceChildren(
        ...[
          h("span", { class: "files-sel-count" }, `выделено: ${n}`),
          one
            ? iconBtn(
              "textCursor", `Переименовать ${one.name}`, () => renameOne(one))
            : null,
          one
            ? iconBtn(
              "copy", `Копировать ${one.name}`, () => copyOne(one))
            : null,
          hasFile
            ? iconBtn("download", "Скачать выделенные файлы", downloadSel)
            : null,
          iconBtn("folderMove", "Перенести в…", moveModal),
          iconBtn("trash", "Удалить выделенное", deleteSel, true),
          iconBtn("close", "Снять выделение", () => {
            sel.clear();
            paintSel();
          }),
        ].filter((x) => x != null),
      );
    }

    function renameOne(e) {
      nameModal(
        `Переименовать ${e.dir ? "каталог" : "файл"} ${e.name}`,
        "новое имя",
        async (nm) => {
          await api("/file/rename", {
            method: "POST",
            body: {
              project: `${section}/${name}`,
              path: path(e),
              new_name: nm,
            },
          });
          toast(`Переименовано: ${e.name} → ${nm}`);
          sel.clear();
          render();
        },
        e.name,
      );
    }

    /* Копия в ту же папку: имя подбирает сервер («Копия - имя», при занятости
       — «(2)», «(3)», …); каталог — рекурсивно. */
    async function copyOne(e) {
      const r = await api("/file/copy", {
        method: "POST",
        body: { project: `${section}/${name}`, path: path(e) },
      });
      toast(`Скопировано: ${r.new_path}`);
      sel.clear();
      render();
    }

    /* Браузер режет пакетную загрузку — ссылки кликаются по одной, с зазором;
       каталоги скачиванию не поддаются (в выделке только они — глухо). */
    function downloadSel() {
      const files = picked().filter((e) => !e.dir);
      if (!files.length) {
        toast("В выделке только каталоги — скачивать нечего", "err");
        return;
      }
      files.forEach((e, i) =>
        setTimeout(() => downloadFile(path(e), e.name), i * 250),
      );
      toast(`Скачивание: ${files.length} файл(ов)`);
    }

    /* Перенос — ОДИН вызов на всю выделку (POST /api/file/move); текущая папка
       и сами выделенные каталоги из списка назначения исключены. */
    function moveModal() {
      const paths = picked().map(path);
      const box = h(
        "select",
        { class: "input", "aria-label": "Каталог назначения" },
        h("option", { value: "" }, "Корень проекта"),
        ...dirs
          .filter((d) => d !== st.path && !paths.includes(d))
          .map((d) => h("option", { value: d }, d)),
      );
      const err = h("div", { class: "form-error" });
      UIC.modal({
        title: `Перенести ${paths.length} объект(ов)`,
        build: (close) => [
          h("div", { class: "modal-text" }, paths.join(", ")),
          box,
          err,
          h(
            "div",
            { class: "modal-actions" },
            h(
              "button",
              { class: "btn btn-ghost", onclick: () => close(false) },
              "Отмена",
            ),
            h(
              "button",
              {
                class: "btn btn-primary",
                onclick: async () => {
                  try {
                    const r = await api("/file/move", {
                      method: "POST",
                      body: {
                        project: `${section}/${name}`,
                        paths,
                        dest: box.value,
                      },
                    });
                    toast(
                      `Перенесено: ${r.moved.length}` +
                        (r.skipped.length
                          ? ` · пропущено: ${r.skipped
                              .map((s) => `${s.path} (${s.reason})`)
                              .join("; ")}`
                          : ""),
                    );
                    sel.clear();
                    close();
                    render();
                  } catch (ex) {
                    err.textContent = ex.message;
                  }
                },
              },
              "ПЕРЕНЕСТИ",
            ),
          ),
        ],
      });
    }

    /* Опасная кнопка живёт здесь, а не на каждой строке списка; каталоги
       уходят вместе с содержимым — confirmModal с словом «УДАЛИТЬ». */
    async function deleteSel() {
      const list = picked();
      const hasDir = list.some((e) => e.dir);
      const ok = await confirmModal(
        `Удаление (${list.length})`,
        list.map((e) => path(e)).join(", ") +
          (hasDir ? " · каталоги — вместе с содержимым" : ""),
        "УДАЛИТЬ",
        async () => {
          for (const e of list) {
            const dq = new URLSearchParams({
              project: `${section}/${name}`,
              path: path(e),
            });
            await api(`/file?${dq}`, { method: "DELETE" });
          }
          toast(`Удалено: ${list.length}`);
          sel.clear();
        },
      );
      if (ok) render();
    }

    const drop = h("div", { class: "files-list" });
    /* Space на строке — быстрый просмотр файла без редактора (Escape закрывает
       так же, как любую модалку); каталоги не просматриваются. */
    drop.addEventListener("keydown", (e) => {
      if (e.key !== " ") return;
      const row = e.target && e.target.closest ? e.target.closest(".frow") : null;
      if (!row || row.getAttribute("data-dir")) return;
      e.preventDefault();
      quickLook(row.getAttribute("data-name") || "");
    });
    // одна страница — пагинация не нужна, достаточно счётчика
    const fPager = UIC.listPager({
      list: drop,
      rows: (slice) =>
        slice.map((e) => fileRow(e, { toggle: toggleSel, rename: renameOne, copy: copyOne })),
      infoOnlySinglePage: true,
      info: (total, page, pages) =>
        pages <= 1
          ? `файлов: ${total}`
          : ` ${page} / ${pages} · файлов: ${total} `,
      // страница списка — состояние вкладки: выделение её не сбрасывает
      onChange: () => {
        st.filesPage = fPager.page;
        fPager.render();
      },
    });
    fPager.items = entries;
    fPager.page = st.filesPage;
    fPager.render();
    paintSel();
    drop.addEventListener("dragover", (e) => {
      e.preventDefault();
      drop.classList.add("drop-over");
    });
    drop.addEventListener("dragleave", () =>
      drop.classList.remove("drop-over"),
    );
    drop.addEventListener("drop", async (e) => {
      e.preventDefault();
      drop.classList.remove("drop-over");
      try {
        const r = await uploadFiles(e.dataTransfer.files);
        if (r) {
          toast(`Загружено: ${r.saved.length} файл(ов)`);
          render();
        }
      } catch (ex) {
        toast(ex.message, "err");
      }
    });
    return h("div", { class: "files-wrap" }, toolbar, drop, fPager.el);
  }

  /* Сортировка списка файлов: каталоги всегда первыми (обход вниз идёт по
     ним), порядок — имя/дата/размер; предпочтение живёт в localStorage. */
  function sortFiles(entries) {
    const key = st.filesSort;
    const sign = st.filesAsc ? 1 : -1;
    const val = (e) =>
      key === "mtime" ? e.mtime || 0 : key === "size" ? e.size || 0 : e.name;
    return [...entries].sort((a, b) => {
      if ((a.dir ? 1 : 0) !== (b.dir ? 1 : 0)) return a.dir ? -1 : 1;
      return sign * (key === "name"
        ? String(val(a)).localeCompare(String(val(b)), "ru",
            { numeric: true, sensitivity: "base" })
        : val(a) - val(b));
    });
  }

  function sortControl() {
    const sel = h(
      "select",
      {
        class: "input input-sm sort-select",
        title: "Сортировка списка файлов (каталоги всегда первыми)",
        onchange: () => {
          st.filesSort = sel.value;
          localStorage.setItem("filesSort", sel.value);
          render();
        },
      },
      ...[["name", "по имени"], ["mtime", "по дате"], ["size", "по размеру"]].map(
        ([k, label]) =>
          h("option", { value: k, selected: k === st.filesSort }, label),
      ),
    );
    const dir = h(
      "button",
      {
        class: "btn btn-sm btn-ghost sort-dir",
        title: st.filesAsc ? "По возрастанию" : "По убыванию",
        "aria-label": st.filesAsc ? "По возрастанию" : "По убыванию",
        onclick: () => {
          st.filesAsc = !st.filesAsc;
          localStorage.setItem("filesAsc", st.filesAsc ? "1" : "0");
          render();
        },
      },
      st.filesAsc ? "↑" : "↓",
    );
    return h("div", { class: "files-sort" }, sel, dir);
  }

  /* Строка списка: чекбокс выделения, имя, метаданные и кнопки одного объекта.
     Деструктивных и групповых действий здесь нет — они в панели выделения. */
  function fileRow(e, acts) {
    const full = st.path ? `${st.path}/${e.name}` : e.name;
    const cb = h("input", {
      type: "checkbox",
      class: "fsel",
      checked: st.filesSel.has(full),
      "aria-label": `Выбрать ${e.name}`,
    });
    cb.addEventListener("change", () => acts.toggle(full, cb.checked));
    const nameNode = e.dir
      ? h(
          "a",
          {
            class: "fname",
            href: "#",
            title: e.dir ? "" : UICore.relTimeAbs(e.mtime),
            onclick: (ev) => {
              ev.preventDefault();
              setPath(full);
            },
          },
          iconEl(UICore.fileIcon(e), "fname-icon"),
          e.name,
        )
      : h(
          "span",
          {
            class: "fname",
            title: UICore.relTimeAbs(e.mtime),
            ondblclick: () => openEditor(full),
          },
          iconEl(UICore.fileIcon(e), "fname-icon"),
          e.name,
        );
    const actions = h("div", { class: "factions" });
    if (!e.dir) {
      /* порядок один везде: правка → скачать → копировать → переименовать;
         скачивание — действие самого файла, а не группы, поэтому в строке,
         а не в тулбаре */
      actions.append(
        iconBtn("pencil", `Править ${e.name}`, () => openEditor(full)),
        iconBtn("download", `Скачать ${e.name}`, () => downloadFile(full, e.name)),
      );
    }
    actions.append(
      iconBtn("copy", `Копировать ${e.name}`, () => acts.copy(e)),
      iconBtn("textCursor", `Переименовать ${e.name}`, () =>
        acts.rename(e)),
    );
    const meta = h(
      "div",
      { class: "fmeta" },
      e.dir ? "" : `${fmtSize(e.size)} · ${UICore.relTime(e.mtime)}`,
    );
    return h("div", {
      class: "frow" + (st.filesSel.has(full) ? " frow-sel" : ""),
      tabindex: "0",
      "data-name": full,
      "data-dir": e.dir ? "1" : "",
    }, cb, nameNode, meta, actions);
  }
  async function quickLook(rel) {
    const frame = h("iframe", {
      class: "editor-preview-frame quick-frame",
      sandbox: "allow-same-origin",
      title: `Содержимое ${rel}`,
    });
    UIC.modal({
      title: `Просмотр · ${rel}`,
      wide: true,
      build: () => [frame],
    });
    /* тот же рендер, что у редактора: режим — по расширению (html как есть,
       md через marked, остальное — обычный текст: markdown склеивает строки) */
    const mode = UICore.previewMode(rel);
    const md = (t) =>
      window.marked
        ? window.marked.parse(t, { mangle: false, headerIds: false })
        : `<pre>${UICore.escapeHtml(t)}</pre>`;
    try {
      const d = await api(
        `/file?project=${encodeURIComponent(`${section}/${name}`)}` +
          `&path=${encodeURIComponent(rel)}`,
      );
      const raw = d.content || "";
      frame.srcdoc =
        mode === "html" ? UIC.docs.html(raw)
          : mode === "md" ? UIC.docs.md(md(raw))
            : UIC.docs.text(raw);
    } catch (ex) {
      frame.srcdoc = UIC.docs.text(`Не удалось прочитать: ${ex.message}`);
    }
  }

  async function editorView() {
    const full = st.edit;
    const q = new URLSearchParams({
      project: `${section}/${name}`,
      path: full,
    });
    let data;
    try {
      data = await api(`/file?${q}`);
    } catch (ex) {
      return h("div", { class: "files-empty" }, ex.message);
    }
    const ext = extOf(full);
    /* язык — по назначению файла: prompts/* читаются как промпт (html-язык
       даёт <system>/<translate> быть тегами), остальное — по расширению */
    const ed = makeEditor(data.content, UICore.editorLang(full));
    const err = h("div", { class: "form-error" });

    /* подсветка и предпросмотр — по расширению файла (makeEditor/setLang),
       без ручного выбора представления */

    /* предпросмотр: режим тоже берётся из расширения (html как есть, md через
       marked, остальное — обычный текст с переносами); каркас один —
       sandbox-iframe без allow-scripts, те же стили/кегль/высота, что у «Заметок» */
    const pane = UIC.previewPane(ed, { renderMode: UICore.previewMode(ext) });

    const saveBtn = h("button", { class: "btn btn-sm" }, "Сохранить");
    saveBtn.addEventListener("click", async () => {
      err.textContent = "";
      try {
        await api("/file", {
          method: "PUT",
          body: {
            project: `${section}/${name}`,
            path: full,
            content: ed.getValue(),
          },
        });
        toast("Сохранено");
      } catch (ex) {
        err.textContent = ex.message;
      }
    });

    const toolbar = h(
      "div",
      { class: "files-toolbar" },
      h(
        "button",
        { class: "btn btn-sm btn-ghost", onclick: () => setPath(st.path) },
        "← Назад",
      ),
      h("span", { class: "editor-meta" }, `${full} · ${fmtSize(data.size)}`),
      h("span", { class: "spacer" }),
      h("span", { class: "field-help" }, "кегль"),
      previewFontSelect(() => {
        if (pane.mode !== "code") pane.render();
      }),
      UIC.editorSearch(ed),
      pane.btn,
      saveBtn,
    );
    st._ed = ed;
    return h(
      "div",
      { class: "editor-wrap editor-has-preview" },
      toolbar,
      err,
      pane.host,
      pane.frame,
    );
  }
  /* ── Редактор глав  ─────────────────── */
  /* Поиск артефактов по маске: канон (translated.txt) И легаси
     (chapter1_translated.txt — старые проекты). test — предикат по имени. */
  const ED_CLASSIFY = [
    {
      canon: "chapter.txt",
      label: "Оригинал",
      test: (n) => n === "chapter.txt",
    },
    {
      canon: "translated.txt",
      label: "Перевод",
      test: (n) => n === "translated.txt" || n.endsWith("_translated.txt"),
    },
    {
      canon: "redacted.txt",
      label: "Редактура",
      test: (n) => n === "redacted.txt" || n.endsWith("_redacted.txt"),
    },
    {
      canon: "polished.txt",
      label: "Полировка",
      test: (n) => n === "polished.txt" || n.endsWith("_polished.txt"),
    },
  ];

  async function editorTabView() {
    const ed = editorState();
    /* вкладка уже собрана: не пересоздаём (скролл, глава, правки, подсветка) */
    if (ed.wrap) return ed.wrap;
    const wrap = h("div", { class: "ed-wrap" });
    const toolbar = h("div", { class: "files-toolbar" });
    const grid = h("div", { class: "ed-grid" });

    let tree;
    try {
      tree = await api(`/projects/${section}/${name}/tree`);
    } catch (ex) {
      return h("div", { class: "files-empty" }, ex.message);
    }
    const chapters = (tree.chapters || []).filter(
      (c) => c.artifacts && Object.keys(c.artifacts).length > 0,
    );
    if (!chapters.length) {
      return h(
        "div",
        { class: "files-empty" },
        "Нет глав с артефактами — сначала запустите epub_to_chapters",
      );
    }
    if (!chapters.some((c) => c.dir === ed.chapter)) {
      ed.chapter = chapters[0].dir; // глава могла удалиться — помним новую
      saveEditorPref();
    }
    /* артефакты главы по маскам (канон приоритетен, затем легаси). Канон
       показывается даже если файла ещё нет — пустой редактор, сохранение
       создаст файл (напр. polished.txt до полировки); канон-без-файла —
       в конец списка, чтобы дефолты предпочитали существующие файлы */
    const chapterTypes = (dir) => {
      const ch = chapters.find((c) => c.dir === dir);
      const names = ch ? Object.keys(ch.artifacts || {}) : [];
      const out = [];
      const missing = [];
      for (const c of ED_CLASSIFY) {
        const hits = names.filter(c.test);
        if (hits.includes(c.canon)) {
          out.push({ name: c.canon, label: c.label });
        } else if (hits.length) {
          out.push({ name: hits.sort()[0], label: `${c.label} (легаси)` });
        } else {
          missing.push({ name: c.canon, label: c.label });
        }
      }
      return out.concat(missing);
    };
    /* типы по умолчанию: слева — оригинал (chapter.txt), справа —
       по приоритету доступности: полировка > редактура > перевод */
    function defaultTypes() {
      const opts = chapterTypes(ed.chapter);
      const by = (pred) => opts.find(pred) || null;
      const left = by((o) => o.name === "chapter.txt") || opts[0] || null;
      const right =
        by(
          (o) => o.name === "polished.txt" || o.name.endsWith("_polished.txt"),
        ) ||
        by(
          (o) => o.name === "redacted.txt" || o.name.endsWith("_redacted.txt"),
        ) ||
        by(
          (o) =>
            o.name === "translated.txt" || o.name.endsWith("_translated.txt"),
        ) ||
        (left ? opts.find((o) => o.name !== left.name) : opts[0]) ||
        left;
      return {
        left: left ? left.name : null,
        right: right ? right.name : null,
      };
    }

    /* ── панель: селект артефакта, редактор, сохранение ── */
    function makePane(paneState) {
      const pane = h("div", { class: "ed-pane" });
      const bar = h("div", { class: "ed-pane-bar" });
      const typeSel = h("select", {
        class: "input ed-type",
        title: "Какой файл главы открыт в этой панели",
      });
      const meta = h("span", { class: "ed-meta" });
      const saveBtn = h(
        "button",
        { class: "btn btn-sm", disabled: true },
        "Сохранить",
      );
      const perr = h("div", { class: "form-error" });
      bar.append(
        h("span", { class: "field-help" }, "Файл:"),
        typeSel,
        meta,
        h("span", { class: "spacer" }),
        saveBtn,
      );
      const host = h("div", { class: "ed-cm" });
      pane.append(bar, perr, host);
      return {
        pane,
        bar,
        typeSel,
        meta,
        saveBtn,
        perr,
        host,
        state: paneState,
        editor: null,
        hl: null,
        /* редактор у панели создаётся один раз — тогда же рядом с «Сохранить»
           появляется и кнопка поиска (без CodeMirror кнопки нет) */
        addSearch: (e) => {
          const f = UIC.editorSearch(e);
          if (f) bar.insertBefore(f, saveBtn);
        },
      };
    }
    /* панели кешируются между рендерами вкладки: повторный рендер (смена
       вкладки и т.п.) не пересоздаёт CodeMirror и не теряет правки */
    if (!ed.panes) {
      ed.panes = [makePane(ed.left), makePane(ed.right)];
      for (const p of ed.panes) {
        p.typeSel.addEventListener("change", () => {
          p.state.type = p.typeSel.value || null;
          p.state.text = null;
          saveEditorPref();
          loadPane(p);
        });
        p.saveBtn.addEventListener("click", () => savePane(p));
      }
    }
    const pLeft = ed.panes[0];
    const pRight = ed.panes[1];
    const panes = [pLeft, pRight];

    /* селект типа артефакта: опции по главе, выбор — прежний, иначе —
       defaultName (если доступен) или первый вариант */
    function fillTypeSel(pInfo, defaultName) {
      const opts = chapterTypes(ed.chapter);
      pInfo.typeSel.replaceChildren();
      for (const o of opts) {
        pInfo.typeSel.append(h("option", { value: o.name }, o.label));
      }
      if (opts.some((o) => o.name === pInfo.state.type)) {
        pInfo.typeSel.value = pInfo.state.type;
      } else {
        const d = opts.find((o) => o.name === defaultName);
        const pick = d ? d.name : opts.length ? opts[0].name : "";
        pInfo.typeSel.value = pick;
        pInfo.state.type = pick || null;
      }
      return pInfo.state.type;
    }

    /* программная замена текста — без колбэка «правки» (смена главы,
       загрузка, очистка): колбэк реагирует только на правки пользователя */
    function setEditorText(pInfo, text) {
      if (!pInfo.editor) return;
      pInfo._loading = true;
      try {
        pInfo.editor.setValue(text);
      } finally {
        pInfo._loading = false;
      }
    }

    /* загрузка артефакта главы в редактор (первый раз — создаёт CM) */
    async function loadPane(pInfo) {
      const type = pInfo.state.type;
      if (!type) {
        setEditorText(pInfo, "");
        pInfo.meta.textContent = "—";
        pInfo.saveBtn.disabled = true;
        pInfo.state.text = null;
        return;
      }
      /* повторный рендер вкладки: редактор уже загружен — только
         переподключаем DOM (несохранённые правки не трогаем) */
      if (pInfo.editor && pInfo.state.text != null) {
        pInfo.host.replaceChildren(pInfo.editor.root);
        pInfo.perr.textContent = "";
        pInfo.meta.textContent = pInfo.state.missing
          ? `новый файл · ${type}`
          : `${fmtSize(pInfo.state.text.length)} · ${type}`;
        pInfo.saveBtn.disabled = !pInfo.state.dirty;
        if (pInfo.hl) {
          pInfo.host.append(pInfo.hl.tip);
          pInfo.hl.tip.style.display = "none";
          computeHighlight(pInfo);
        }
        if (pInfo.addUi) pInfo.host.append(pInfo.addUi.wrap);
        updateAddTerm(pInfo);
        applyPaneFind(pInfo);
        return;
      }
      const path = `chapters/${ed.chapter}/${type}`;
      const q = new URLSearchParams({ project: `${section}/${name}`, path });
      pInfo.perr.textContent = "";
      pInfo.meta.textContent = "загрузка…";
      try {
        const data = await api(`/file?${q}`);
        pInfo.state.text = data.content;
        pInfo.state.dirty = false;
        pInfo.state.missing = !!data.missing; /* файла нет — создание при сохранении */
        pInfo.meta.textContent = data.missing
          ? `новый файл · ${type}`
          : `${fmtSize(data.size)} · ${type}`;
        if (pInfo.editor) {
          setEditorText(pInfo, data.content);
          pInfo.host.replaceChildren(pInfo.editor.root);
        } else {
          pInfo.editor = makeEditor(data.content, "txt", (u) => {
            if (pInfo._loading) return; // программные setValue — не «правка»
            if (u && u.viewportChanged) placeMarks(pInfo);
            if (u && u.selectionSet) updateAddTerm(pInfo);
            if (!u || !u.docChanged) return;
            pInfo.state.text = pInfo.editor.getValue();
            pInfo.state.dirty = true;
            pInfo.saveBtn.disabled = false;
            if (pInfo.hl) pInfo.hl.tip.style.display = "none";
            scheduleHighlight(pInfo);
          });
          pInfo.addSearch(pInfo.editor);
          pInfo.host.replaceChildren(pInfo.editor.root);
        }
        /* replaceChildren снял тултип/кнопку (соседи editor.root) — вернуть */
        if (pInfo.hl) {
          pInfo.host.append(pInfo.hl.tip);
          pInfo.hl.tip.style.display = "none";
        }
        if (pInfo.addUi) pInfo.host.append(pInfo.addUi.wrap);
        pInfo.saveBtn.disabled = !pInfo.state.dirty;
        maybeHl(pInfo);
        updateAddTerm(pInfo);
        applyPaneFind(pInfo);
      } catch (ex) {
        pInfo.state.text = null;
        pInfo.state.missing = false;
        pInfo.perr.textContent = ex.message;
        pInfo.meta.textContent = "";
        pInfo.saveBtn.disabled = true;
      }
    }

    async function savePane(pInfo) {
      if (!pInfo.state.type || pInfo.state.text == null) return;
      pInfo.perr.textContent = "";
      const path = `chapters/${ed.chapter}/${pInfo.state.type}`;
      try {
        await api("/file", {
          method: "PUT",
          body: {
            project: `${section}/${name}`,
            path,
            content: pInfo.state.text,
          },
        });
        pInfo.state.dirty = false;
        pInfo.state.missing = false; /* файл создан — больше не «новый» */
        pInfo.meta.textContent = `${fmtSize(pInfo.state.text.length)} · ${pInfo.state.type}`;
        pInfo.saveBtn.disabled = true;
        toast("Сохранено");
      } catch (ex) {
        pInfo.perr.textContent = ex.message;
      }
    }

    /* «Поиск» прислал главу: панель того же типа открывается с запросом
       (лупа в тулбаре панели открывает её пустой) */
    function applyPaneFind(pInfo) {
      const f = ed.find;
      if (!f || f.type !== pInfo.state.type) return;
      ed.find = null;
      openFind(pInfo.editor, f.q);
    }

    /* ── подсветка терминов глоссария поверх редактора ── */
    async function ensureNer() {
      if (ed.ner) return;
      const q = new URLSearchParams({ project: `${section}/${name}` });
      const data = await api(`/ner?${q}`);
      ed.ner = {
        items: data.items || [],
        matcher: UICore.buildGlossaryMatcher(
          data.items || [],
          ed.ngram,
          ed.threshold,
        ),
      };
    }
    /* пересчёт подсветки всех панелей (после смены ngram/порога) */
    function applyMatcher() {
      if (!ed.ner) return;
      ed.ner.matcher = UICore.buildGlossaryMatcher(
        ed.ner.items,
        ed.ngram,
        ed.threshold,
      );
      recomputeAll();
    }
    function recomputeAll() {
      if (!ed.hl || !ed.ner) return;
      for (const p of panes) {
        if (p.editor && p.editor.isCM) {
          if (p.hl) computeHighlight(p);
          else attachHl(p);
        }
      }
      /* порог/скролл: координаты CM на кадр могут быть пустыми — второй проход */
      requestAnimationFrame(() => {
        for (const p of panes) if (p.hl) placeMarks(p);
      });
    }

    function scheduleHighlight(pInfo) {
      if (!ed.hl || !ed.ner || !pInfo.editor) return;
      if (pInfo.hlTimer) clearTimeout(pInfo.hlTimer);
      pInfo.hlTimer = setTimeout(() => {
        pInfo.hlTimer = null;
        computeHighlight(pInfo);
      }, 150);
    }

    function computeHighlight(pInfo) {
      const hl = pInfo.hl;
      if (!hl || !pInfo.editor || !pInfo.editor.isCM) return;
      const view = pInfo.editor.view;
      const text = view.state.doc.toString();
      hl.matches = UICore.glossaryMatches(text, ed.ner.matcher).sort(
        (a, b) => a.from - b.from,
      );
      placeMarks(pInfo);
    }

    function placeMarks(pInfo) {
      const hl = pInfo.hl; /* НЕ h — не затенять глобальный h() */
      if (!hl || !pInfo.editor || !pInfo.editor.isCM) return;
      const view = pInfo.editor.view;
      const scroller = view.scrollDOM;
      const scrollRect = scroller.getBoundingClientRect();
      const layer = hl.layer;
      if (!layer.isConnected) scroller.append(layer);
      layer.replaceChildren();
      const ranges = view.visibleRanges;
      let lastEnd = -1;
      for (let i = 0; i < hl.matches.length; i++) {
        const m = hl.matches[i];
        if (m.from < lastEnd) continue; // перекрытия — только первое
        lastEnd = m.to;
        if (!inRanges(m.from, m.to, ranges)) continue;
        const a = view.coordsAtPos(m.from);
        const b = view.coordsAtPos(m.to);
        if (!a || !b) continue;
        const span = h("span", { class: "hl-mark" });
        span.style.left = a.left - scrollRect.left + scroller.scrollLeft + "px";
        span.style.top = a.top - scrollRect.top + scroller.scrollTop + "px";
        span.style.width = Math.max(2, b.right - a.left) + "px";
        span.style.height = Math.max(1, b.bottom - a.top) + "px";
        span.dataset.i = String(i);
        layer.append(span);
      }
    }

    function inRanges(from, to, ranges) {
      for (const r of ranges) {
        if (to <= r.from) return false;
        if (from < r.to) return true;
      }
      return false;
    }

    function findAt(matches, pos) {
      const ms = matches || null;
      if (!ms) return null;
      let lo = 0;
      let hi = ms.length - 1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        const m = ms[mid];
        if (pos < m.from) hi = mid - 1;
        else if (pos >= m.to) lo = mid + 1;
        else return m;
      }
      return null;
    }

    function moveTip(pInfo, e) {
      const hl = pInfo.hl;
      if (!hl || !pInfo.editor || !pInfo.editor.isCM) return;
      const view = pInfo.editor.view;
      const tip = hl.tip;
      const pos = view.posAtCoords({ x: e.clientX, y: e.clientY });
      const m = pos == null ? null : findAt(hl.matches, pos);
      if (!m || !m.item) {
        hl.cur = null;
        tip.style.display = "none";
        return;
      }
      /* тот же термин — не пересобирать DOM (кнопка «→ Глоссарий» живая) */
      if (hl.cur === m && tip.style.display !== "none") return;
      const a = view.coordsAtPos(m.from);
      if (!a) {
        hl.cur = null;
        tip.style.display = "none";
        return;
      }
      hl.cur = m;
      const hostRect = pInfo.host.getBoundingClientRect();
      const term = String(m.item.term || "");
      const translation = String(m.item.translation || "");
      const type = String(m.item.type || "");
      const notes = String(m.item.notes || "");
      tip.replaceChildren(
        h("div", { class: "hl-tip-term" }, term || "?"),
        // замок — состояние термина: запись защищена от правок
        UICore.nerIsLocked(m.item)
          ? h("div", { class: "hl-tip-row hl-tip-locked" },
              "зафиксирован (замок)")
          : null,
        translation
          ? h("div", { class: "hl-tip-row" }, `Перевод: ${translation}`)
          : null,
        type ? h("div", { class: "hl-tip-row" }, `Тип: ${type}`) : null,
        notes ? h("div", { class: "hl-tip-notes" }, notes) : null,
        h(
          "button",
          {
            class: "btn btn-sm hl-tip-btn",
            onclick: () => goGlossary(term || translation),
          },
          "→ Глоссарий",
        ),
      );
      tip.style.display = "block";
      const x = a.left - hostRect.left + 12;
      const y = a.top - hostRect.top - tip.offsetHeight - 8;
      tip.style.left =
        Math.max(4, Math.min(x, hostRect.width - tip.offsetWidth - 8)) + "px";
      tip.style.top =
        Math.max(4, Math.min(y, hostRect.height - tip.offsetHeight - 8)) + "px";
    }

    function attachHl(pInfo) {
      if (!pInfo.editor || !pInfo.editor.isCM) return;
      /* идемпотентно: повторный вызов (смена главы/типа, переключение
         подсветки) НЕ создаёт второй слой — только пересчитывает марки */
      if (pInfo.hl) {
        computeHighlight(pInfo);
        return;
      }
      const view = pInfo.editor.view;
      const scroller = view.scrollDOM;
      const layer = h("div", { class: "ed-hl-layer" });
      const tip = h("div", { class: "hl-tip" });
      tip.style.display = "none";
      pInfo.host.append(tip);
      pInfo.hl = { layer, tip, matches: [], cur: null };
      scroller.append(layer);
      /* тултип — сосед скроллера (не потомок): переход курсора на него
         даёт mouseleave скроллера. relatedTarget = тултип → не прятать,
         иначе кнопка «→ Глоссарий» исчезает до клика. */
      const overTip = (node) => {
        const t = pInfo.hl && pInfo.hl.tip;
        return !!(t && node && (t === node || t.contains(node)));
      };
      const onMove = (e) => {
        if (overTip(e.target)) return;
        const tipEl = pInfo.hl && pInfo.hl.tip;
        if (tipEl && tipEl.style.display !== "none") {
          const r = tipEl.getBoundingClientRect();
          if (
            r.width > 0 &&
            e.clientX >= r.left - 8 &&
            e.clientX <= r.right + 8 &&
            e.clientY >= r.top - 8 &&
            e.clientY <= r.bottom + 8
          ) {
            return;
          }
        }
        moveTip(pInfo, e);
      };
      const onLeave = (e) => {
        if (overTip(e.relatedTarget)) return;
        if (pInfo.hl) {
          pInfo.hl.cur = null;
          pInfo.hl.tip.style.display = "none";
        }
      };
      /* скролл: прячем тултип и переставляем марки видимой области
         (без этого подсветка не появляется в новых местах при прокрутке) */
      let hlRaf = null;
      const onScroll = () => {
        if (pInfo.hl) {
          pInfo.hl.cur = null;
          pInfo.hl.tip.style.display = "none";
        }
        if (hlRaf == null) {
          hlRaf = requestAnimationFrame(() => {
            hlRaf = null;
            placeMarks(pInfo);
          });
        }
      };
      const onTipLeave = (e) => {
        const sc =
          pInfo.editor && pInfo.editor.view && pInfo.editor.view.scrollDOM;
        if (
          sc &&
          e.relatedTarget &&
          (sc === e.relatedTarget || sc.contains(e.relatedTarget))
        ) {
          return; // обратно в текст — moveTip решит, прятать ли
        }
        if (pInfo.hl) {
          pInfo.hl.cur = null;
          pInfo.hl.tip.style.display = "none";
        }
      };
      scroller.addEventListener("mousemove", onMove);
      scroller.addEventListener("mouseleave", onLeave);
      scroller.addEventListener("scroll", onScroll);
      tip.addEventListener("mouseleave", onTipLeave);
      pInfo._hlCleanup = () => {
        scroller.removeEventListener("mousemove", onMove);
        scroller.removeEventListener("mouseleave", onLeave);
        scroller.removeEventListener("scroll", onScroll);
      };
      if (typeof ResizeObserver === "function") {
        const ro = new ResizeObserver(() => placeMarks(pInfo));
        ro.observe(pInfo.host);
        pInfo._hlResize = ro;
      }
      computeHighlight(pInfo);
    }

    function detachHl(pInfo) {
      if (!pInfo.hl) return;
      pInfo.hl.layer.remove();
      pInfo.hl.tip.remove();
      if (pInfo._hlCleanup) {
        pInfo._hlCleanup();
        pInfo._hlCleanup = null;
      }
      if (pInfo._hlResize) {
        pInfo._hlResize.disconnect();
        pInfo._hlResize = null;
      }
      pInfo.hl = null;
    }

    async function maybeHl(pInfo) {
      if (!ed.hl || !pInfo.editor) return;
      try {
        await ensureNer();
        attachHl(pInfo);
      } catch (ex) {
        toast(ex.message, "err");
      }
    }

    function goGlossary(term) {
      st.view = "ner";
      st.search = term || "";
      render();
    }

    /* ── добавление термина из выделения в chapter.txt ── */
    function ensureAddUi(pInfo) {
      if (pInfo.addUi) return pInfo.addUi;
      const btn = h(
        "button",
        { class: "btn btn-sm ed-add-term", type: "button" },
        "＋ в глоссарий",
      );
      const termEl = h("div", { class: "hl-tip-term" });
      const typeInp = h("input", {
        class: "input input-sm",
        placeholder: "Тип",
      });
      const trInp = h("input", {
        class: "input input-sm",
        placeholder: "Перевод",
      });
      const saveBtn = h(
        "button",
        { class: "btn btn-sm", type: "button" },
        "Добавить",
      );
      const cancelBtn = h(
        "button",
        { class: "btn btn-sm btn-ghost", type: "button" },
        "Отмена",
      );
      const form = h(
        "div",
        { class: "ed-add-form" },
        termEl,
        h("div", { class: "hl-tip-row" }, "Тип"),
        typeInp,
        h("div", { class: "hl-tip-row" }, "Перевод"),
        trInp,
        h("div", { class: "ed-add-form-actions" }, saveBtn, cancelBtn),
      );
      form.style.display = "none";
      const wrap = h("div", { class: "ed-add-wrap" }, btn, form);
      wrap.style.display = "none";
      btn.addEventListener("mousedown", (e) => e.preventDefault());
      form.addEventListener("mousedown", (e) => e.stopPropagation());
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        openAddForm(pInfo);
      });
      cancelBtn.addEventListener("click", () => closeAddForm(pInfo));
      saveBtn.addEventListener("click", () => submitAddForm(pInfo));
      typeInp.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          trInp.focus();
        } else if (e.key === "Escape") {
          closeAddForm(pInfo);
        }
      });
      trInp.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          submitAddForm(pInfo);
        } else if (e.key === "Escape") {
          closeAddForm(pInfo);
        }
      });
      pInfo.host.append(wrap);
      pInfo.addUi = { wrap, btn, form, termEl, typeInp, trInp, saveBtn };
      return pInfo.addUi;
    }

    function placeAddUi(pInfo, from, to) {
      const ui = pInfo.addUi;
      if (!ui || !pInfo.editor || !pInfo.editor.isCM) return;
      const view = pInfo.editor.view;
      const a = view.coordsAtPos(from);
      const b = view.coordsAtPos(to);
      if (!a) return;
      ui.wrap.style.display = "block";
      const hostRect = pInfo.host.getBoundingClientRect();
      const w = ui.wrap.offsetWidth || 220;
      const hh = ui.wrap.offsetHeight || 28;
      let x = a.left - hostRect.left;
      const bottom = (b && b.bottom) || a.bottom;
      let y = bottom - hostRect.top + 4;
      if (y + hh > hostRect.height - 4) y = a.top - hostRect.top - hh - 4;
      x = Math.max(4, Math.min(x, hostRect.width - w - 4));
      y = Math.max(4, Math.min(y, hostRect.height - hh - 4));
      ui.wrap.style.left = x + "px";
      ui.wrap.style.top = y + "px";
    }

    function closeAddForm(pInfo) {
      pInfo.addDraft = null;
      if (!pInfo.addUi) return;
      pInfo.addUi.form.style.display = "none";
      pInfo.addUi.btn.style.display = "block";
      updateAddTerm(pInfo);
    }

    function updateAddTerm(pInfo) {
      if (!pInfo.editor || !pInfo.editor.isCM) {
        if (pInfo.addUi) pInfo.addUi.wrap.style.display = "none";
        return;
      }
      const ui = ensureAddUi(pInfo);
      if (pInfo.state.type !== "chapter.txt") {
        pInfo.addDraft = null;
        ui.wrap.style.display = "none";
        return;
      }
      /* форма открыта — не прятать при потере выделения (фокус в полях) */
      if (pInfo.addDraft) {
        ui.btn.style.display = "none";
        ui.form.style.display = "flex";
        placeAddUi(pInfo, pInfo.addDraft.from, pInfo.addDraft.to);
        return;
      }
      const view = pInfo.editor.view;
      const sel = view.state.selection.main;
      if (sel.empty) {
        ui.wrap.style.display = "none";
        return;
      }
      const term = view.state.doc.sliceString(sel.from, sel.to).trim();
      if (!term) {
        ui.wrap.style.display = "none";
        return;
      }
      ui.form.style.display = "none";
      ui.btn.style.display = "block";
      placeAddUi(pInfo, sel.from, sel.to);
    }

    async function openAddForm(pInfo) {
      if (
        pInfo.state.type !== "chapter.txt" ||
        !pInfo.editor ||
        !pInfo.editor.isCM
      ) {
        return;
      }
      const view = pInfo.editor.view;
      const sel = view.state.selection.main;
      if (sel.empty) return;
      const text = view.state.doc.toString();
      const term = text.slice(sel.from, sel.to).trim().normalize("NFC");
      if (!term) return;
      const ui = ensureAddUi(pInfo);
      try {
        const q = new URLSearchParams({ project: `${section}/${name}` });
        const data = await api(`/ner?${q}`);
        if (data.too_large) {
          toast("Глоссарий слишком большой — правьте через «Файлы»", "err");
          return;
        }
        const items = data.items || [];
        const dup = items.some(
          (it) =>
            String(it.term || "")
              .trim()
              .normalize("NFC") === term,
        );
        if (dup) {
          toast(`Термин «${term}» уже есть в глоссарии`, "err");
          return;
        }
        pInfo.addDraft = { term, from: sel.from, to: sel.to, text };
        ui.termEl.textContent = term;
        ui.typeInp.value = "";
        ui.trInp.value = "";
        ui.btn.style.display = "none";
        ui.form.style.display = "flex";
        placeAddUi(pInfo, sel.from, sel.to);
        setTimeout(() => ui.typeInp.focus(), 0);
      } catch (ex) {
        toast(ex.message, "err");
      }
    }

    async function submitAddForm(pInfo) {
      const draft = pInfo.addDraft;
      if (!draft || !pInfo.addUi) return;
      const type = pInfo.addUi.typeInp.value.trim();
      const translation = pInfo.addUi.trInp.value.trim();
      const term = draft.term;
      pInfo.addUi.saveBtn.disabled = true;
      try {
        const q = new URLSearchParams({ project: `${section}/${name}` });
        const data = await api(`/ner?${q}`);
        if (data.too_large) {
          toast("Глоссарий слишком большой — правьте через «Файлы»", "err");
          return;
        }
        const items = data.items || [];
        const dup = items.some(
          (it) =>
            String(it.term || "")
              .trim()
              .normalize("NFC") === term,
        );
        if (dup) {
          toast(`Термин «${term}» уже есть в глоссарии`, "err");
          return;
        }
        const context = UICore.glossarySentence(
          draft.text,
          draft.from,
          draft.to,
          200,
        );
        items.push({ term, type, translation, context, count: 1 });
        await api("/ner", {
          method: "PUT",
          body: { project: `${section}/${name}`, items },
        });
        ed.ner = {
          items,
          matcher: UICore.buildGlossaryMatcher(items, ed.ngram, ed.threshold),
        };
        recomputeAll();
        pInfo.addDraft = null;
        pInfo.addUi.form.style.display = "none";
        pInfo.addUi.wrap.style.display = "none";
        toast("Термин добавлен в глоссарий");
      } catch (ex) {
        toast(ex.message, "err");
      } finally {
        if (pInfo.addUi) pInfo.addUi.saveBtn.disabled = false;
      }
    }

    /* ── сборка интерфейса ── */
    const chapterSel = h("select", {
      class: "input ed-chapter",
      title: "Глава, чьи файлы редактируются",
    });
    for (const c of chapters) {
      chapterSel.append(h("option", { value: c.dir }, c.dir));
    }
    chapterSel.value = ed.chapter;
    const modeBtn = h("button", {
      class: "btn btn-sm btn-ghost",
      title: "Одна панель или две рядом (например, оригинал + перевод)",
    });
    const hlBtn = h("button", {
      class: "btn btn-sm btn-ghost",
      title: "Подсветить термины глоссария (ner.json) в тексте редактора",
    });
    const ngramInput = h("input", {
      class: "input ed-ngram",
      type: "number",
      min: "1",
      max: "6",
      title:
        "Размер n-граммы нечёткого поиска терминов (аналог --ner_ngram в translate_book)",
    });
    ngramInput.value = String(ed.ngram);
    ngramInput.addEventListener("change", () => {
      const v = parseInt(ngramInput.value, 10);
      ed.ngram = Number.isFinite(v) ? Math.min(6, Math.max(1, v)) : 3;
      ngramInput.value = String(ed.ngram);
      saveEditorPref();
      applyMatcher();
    });
    const thresholdInput = h("input", {
      class: "input ed-threshold",
      type: "number",
      min: "0",
      max: "1",
      step: "0.05",
      title:
        "Порог нечёткого поиска терминов: выше — строже (аналог --ner_threshold в translate_book)",
    });
    thresholdInput.value = String(ed.threshold);
    const readThreshold = (raw) => {
      const v = parseFloat(String(raw || "").replace(",", "."));
      return Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : null;
    };
    /* input — сразу (спиннер/ввод); change — нормализует отображаемое значение */
    thresholdInput.addEventListener("input", () => {
      const raw = thresholdInput.value.trim().replace(",", ".");
      if (!/^\d+(\.\d+)?$/.test(raw)) return; // «0.» — ждём цифру
      const v = readThreshold(raw);
      if (v == null || v === ed.threshold) return;
      ed.threshold = v;
      saveEditorPref();
      applyMatcher();
    });
    thresholdInput.addEventListener("change", () => {
      const v = readThreshold(thresholdInput.value);
      ed.threshold = v == null ? 0.75 : v;
      thresholdInput.value = String(ed.threshold);
      saveEditorPref();
      applyMatcher();
    });
    function renderModeLabel() {
      /* подпись = ТЕКУЩЕЕ состояние; клик переключает */
      modeBtn.textContent = ed.mode === "two" ? "Два файла" : "Один файл";
    }
    function renderHlLabel() {
      hlBtn.textContent = ed.hl
        ? "Подсветка терминов: вкл"
        : "Подсветка терминов: выкл";
      hlBtn.classList.toggle("btn-active", ed.hl);
    }

    function rebuildGrid() {
      grid.replaceChildren();
      grid.classList.toggle("ed-grid-two", ed.mode === "two");
      // spread: append([a, b]) привёл бы массив к строке «[object …]»
      grid.append(
        ...(ed.mode === "two" ? [pLeft.pane, pRight.pane] : [pLeft.pane]),
      );
    }

    chapterSel.addEventListener("change", () => {
      ed.chapter = chapterSel.value;
      saveEditorPref();
      for (const p of panes) {
        p.state.type = null;
        p.state.text = null;
        p.state.dirty = false;
        p.state.missing = false;
        setEditorText(p, ""); // без колбэка «правки» — иначе текст станет ""
        p.meta.textContent = "—";
        p.saveBtn.disabled = true;
        if (p.hl) {
          // сброс подсветки прошлой главы (марки + тултип)
          p.hl.matches = [];
          p.hl.cur = null;
          p.hl.layer.replaceChildren();
          p.hl.tip.style.display = "none";
        }
        p.addDraft = null;
        if (p.addUi) p.addUi.wrap.style.display = "none";
      }
      const def = defaultTypes();
      fillTypeSel(pLeft, def.left);
      fillTypeSel(pRight, def.right);
      loadPane(pLeft);
      loadPane(pRight);
    });
    modeBtn.addEventListener("click", () => {
      ed.mode = ed.mode === "two" ? "one" : "two";
      saveEditorPref();
      renderModeLabel();
      rebuildGrid();
    });
    hlBtn.addEventListener("click", () => {
      ed.hl = !ed.hl;
      saveEditorPref();
      renderHlLabel();
      if (ed.hl) {
        for (const p of panes) maybeHl(p);
      } else {
        for (const p of panes) detachHl(p);
      }
    });

    toolbar.append(
      h("span", { class: "field-help" }, "Глава:"),
      chapterSel,
      h("span", { class: "field-help" }, "Режим:"),
      modeBtn,
      hlBtn,
      h("span", { class: "field-help" }, "n-грамма:"),
      ngramInput,
      h("span", { class: "field-help" }, "Порог:"),
      thresholdInput,
    );
    renderModeLabel();
    renderHlLabel();
    rebuildGrid();
    const def = defaultTypes();
    fillTypeSel(pLeft, def.left);
    fillTypeSel(pRight, def.right);
    loadPane(pLeft);
    loadPane(pRight);
    wrap.append(toolbar, grid);
    ed.wrap = wrap;
    return wrap;
  }

  /* ── Глоссарий NER ───────────────────────────── */
  async function nerView() {
    const q = new URLSearchParams({ project: `${section}/${name}` });
    let data;
    try {
      data = await api(`/ner?${q}`);
    } catch (ex) {
      return h("div", { class: "files-empty" }, ex.message);
    }
    if (data.too_large) {
      return h(
        "div",
        { class: "files-empty" },
        `Глоссарий ${fmtSize(data.size)} — откройте через «Файлы» (правка ner.json)`,
      );
    }
    data.items = data.items || [];
    const LS_KEY = `nerCols:${section}/${name}`;
    const DEFAULT_COLS = ["term", "type", "translation"];
    let cols = [...DEFAULT_COLS];
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (raw != null) {
        const saved = JSON.parse(raw);
        if (saved === null)
          cols = null; // «Все столбцы» (выбор в модалке)
        else if (Array.isArray(saved) && saved.length) cols = saved;
      }
    } catch {
      cols = [...DEFAULT_COLS];
    }
    const knownKeys = new Set(DEFAULT_COLS);
    for (const it of data.items) {
      // «_locked» — не столбец данных: замок термина — состояние, оно
      // отмечается кнопкой в действиях строки
      for (const k of Object.keys(it)) {
        if (k !== "__new" && k !== "_locked") knownKeys.add(k);
      }
    }

    const search = h("input", {
      class: "input ner-q",
      placeholder: "Поиск…",
      "aria-label": "Поиск по глоссарию",
    });
    // из «→ Глоссарий» редактора — применяем один раз и сбрасываем,
    // чтобы ручные заходы на вкладку не наследовали чужой поиск
    if (st.search) {
      search.value = st.search;
      st.search = null;
    }
    const table = h("table", { class: "ner-table" });
    const tbody = h("tbody");
    /* страница таблицы — общий пейджер: пустой список pager молчит */
    const pg = UIC.listPager({
      list: tbody,
      hideOnEmpty: true,
      info: (total, page, pages) => {
        const locked = UICore.nerLockedCount(data.items);
        return (
          ` ${page} / ${pages} · всего ${total}` +
          (locked ? ` · зафиксировано ${locked}` : "") +
          " "
        );
      },
      rows: (slice) => {
        const rows = slice.map((it) =>
          editing === it ? editRow(it) : viewRow(it),
        );
        // редактируемая запись гарантированно видна: если она не попала
        // в slice (сортировка count ↓, поиск, фильтр типов) — докидываем
        if (editing && !slice.includes(editing)) rows.push(editRow(editing));
        if (!slice.length && !editing) {
          rows.push(
            h("tr", {}, h("td", {
              colspan: String(visibleCols().length + 1),
            }, "Нет записей")),
          );
        }
        return rows;
      },
    });
    let editing = null; // редактируемая запись (объект из data.items)
    const colLabel = (key) => key; // заголовки — сырые ключи JSON (term, type, …)
    const visibleCols = () => (cols == null ? [...knownKeys] : cols);
    function colsLabel() {
      if (cols == null) return "Столбцы: все";
      if (cols.length === 1) return `Столбцы: ${colLabel(cols[0])}`;
      return `Столбцы (${cols.length})`;
    }
    /* Значения-объекты/массивы (напр. _votes_pinyin) показываем
       компактным JSON, а не «[object Object]» */
    const cellText = UICore.nerCellText;
    const isStruct = (v) => v != null && typeof v === "object";

    /* сортировка кликом по заголовку; дефолт — count ↓ даже без столбца.
       Порядок — состояние вкладки: книга помнит, по чему её сортировали */
    const nsort = pref.get("ner");
    let sortField = nsort.sortField || "count";
    let sortDir = nsort.sortDir || "desc";

    const LS_SEARCH_KEY = `nerSearch:${section}/${name}`;
    const LS_TYPES_KEY = `nerTypes:${section}/${name}`;
    const LS_LOCK_KEY = `nerLock:${section}/${name}`;
    let lockOnly = false; // показать только зафиксированные термины
    try {
      lockOnly = localStorage.getItem(LS_LOCK_KEY) === "1";
    } catch {
      /* localStorage недоступен (приватный режим) — не критично */
    }
    // null = искать по отображаемым столбцам (дефолт), [] = ни одного
    let searchFields = null;
    let typeFilter = null; // null = все типы, [] = ни одного
    try {
      const saved = JSON.parse(localStorage.getItem(LS_SEARCH_KEY) || "null");
      if (Array.isArray(saved)) {
        if (saved.length) {
          const filtered = saved.filter((k) => knownKeys.has(k));
          searchFields =
            filtered.length && filtered.length < knownKeys.size
              ? filtered
              : null;
        } else {
          searchFields = [];
        }
      }
    } catch {
      searchFields = null;
    }
    const typeNames = () => Object.keys(data.by_type || {});
    try {
      const saved = JSON.parse(localStorage.getItem(LS_TYPES_KEY) || "null");
      if (Array.isArray(saved)) {
        if (saved.length) {
          const all = typeNames();
          const filtered = saved.filter((t) => all.includes(t));
          typeFilter =
            filtered.length && filtered.length < all.length ? filtered : null;
        } else {
          typeFilter = [];
        }
      }
    } catch {
      typeFilter = null;
    }

    /* из «Поиска» вкладка открывается со всеми столбцами: поиск идёт по
       отображаемым ключам, а отображается все ключи записи — то есть ищется
       по-настоящему по всем полям. Выбор пользователя в localStorage не
       перезаписываем: он вернётся при обычном заходе на вкладку */
    if (st.nerAll) {
      cols = null;
      searchFields = null;
      st.nerAll = null;
    }

    function visible() {
      const filtered = UICore.filterNerItems(
        data.items,
        search.value,
        // дефолт поиска — то, что показано на экране, а не все ключи записи
        searchFields == null ? visibleCols() : searchFields,
        typeFilter,
        lockOnly ? "locked" : null,
      );
      return UICore.sortNerItems(filtered, sortField, sortDir);
    }
    function saveCols() {
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(cols));
      } catch {
        /* localStorage недоступен (приватный режим) — не критично */
      }
    }
    function saveSearchFields() {
      try {
        localStorage.setItem(LS_SEARCH_KEY, JSON.stringify(searchFields));
      } catch {
        /* localStorage недоступен (приватный режим) — не критично */
      }
    }
    function saveTypeFilter() {
      try {
        localStorage.setItem(LS_TYPES_KEY, JSON.stringify(typeFilter));
      } catch {
        /* localStorage недоступен (приватный режим) — не критично */
      }
    }
    function saveLockOnly() {
      try {
        localStorage.setItem(LS_LOCK_KEY, lockOnly ? "1" : "0");
      } catch {
        /* localStorage недоступен (приватный режим) — не критично */
      }
    }
    /* фильтр «только зафиксированные»: чекбокс в панели, состояние — в
       localStorage рядом со столбцами и типами */
    const lockCb = h("input", {
      type: "checkbox",
      class: "ner-lock-cb",
      "aria-label": "Показывать только зафиксированные термины",
    });
    lockCb.checked = lockOnly;
    const lockBox = h(
      "label",
      {
        class: "chk ner-lock" + (lockOnly ? " chk-on" : ""),
        title: "Показывать только зафиксированные термины",
      },
      lockCb,
      " только зафиксированные",
    );
    lockCb.addEventListener("change", () => {
      lockOnly = lockCb.checked;
      lockBox.className = "chk ner-lock" + (lockOnly ? " chk-on" : "");
      saveLockOnly();
      pg.page = 0;
      renderRows();
    });

    function searchFieldsLabel() {
      if (searchFields == null) return "Поля поиска: отображаемые";
      if (searchFields.length >= knownKeys.size) return "Поля поиска: все";
      if (searchFields.length === 1) {
        return `Поля поиска: ${colLabel(searchFields[0])}`;
      }
      return `Поля поиска (${searchFields.length})`;
    }
    function typeFilterLabel() {
      if (typeFilter == null) return "Типы: все";
      if (typeFilter.length === 1) {
        const t = typeFilter[0];
        const n = (data.by_type || {})[t];
        return n == null ? t : `${t} (${n})`;
      }
      return `Типы (${typeFilter.length})`;
    }
    /* модалка «Все / набор» — единая для столбцов, полей и типов:
       сверху чекбокс «Все»; снять ВСЁ нельзя — остаётся минимум один
       пункт; снятие «Все» оставляет первый пункт списка */
    function openToggleAllModal(opts) {
      const allKeys = [...opts.keys];
      const allCb = h("input", { type: "checkbox" });
      const itemCbs = [];
      function current() {
        return opts.get();
      }
      function apply(next) {
        opts.set(next);
        allCb.checked = next == null;
        const set = new Set(next == null ? allKeys : next);
        for (const { k, cb } of itemCbs) cb.checked = set.has(k);
      }
      allCb.checked = current() == null || current().length === allKeys.length;
      allCb.addEventListener("change", () => {
        apply(allCb.checked ? null : [allKeys[0]]);
      });
      const rows = allKeys.map((k) => {
        const cb = h("input", { type: "checkbox" });
        const cur = current();
        cb.checked = cur == null || cur.includes(k);
        itemCbs.push({ k, cb });
        cb.addEventListener("change", () => {
          const set = new Set(current() == null ? allKeys : current());
          if (cb.checked) {
            set.add(k);
            apply(
              set.size === allKeys.length
                ? null
                : allKeys.filter((x) => set.has(x)),
            );
          } else if (set.size > 1) {
            set.delete(k);
            apply(allKeys.filter((x) => set.has(x)));
          } else {
            cb.checked = true; // последний пункт не снимаем
          }
        });
        return h("label", { class: "ner-col-row" }, cb, " " + opts.labelOf(k));
      });
      UIC.modal({
        title: opts.title,
        build: (close) => [
          h("div", { class: "modal-text" }, opts.text),
          h("label", { class: "ner-col-row" }, allCb, " " + opts.allLabel),
          ...rows,
          h(
            "div",
            { class: "modal-actions" },
            h(
              "button",
              {
                class: "btn btn-ghost",
                onclick: () => {
                  opts.reset();
                  close();
                },
              },
              "Сбросить",
            ),
            h("button", { class: "btn btn-primary", onclick: close }, "Готово"),
          ),
        ],
      });
    }
    async function saveNer() {
      try {
        const items = data.items.map((it) => {
          const { __new, ...rest } = it;
          return rest;
        });
        await api("/ner", {
          method: "PUT",
          body: { project: `${section}/${name}`, items },
        });
        toast("Глоссарий сохранён");
      } catch (ex) {
        toast(ex.message, "err");
      }
    }


    /* выделение записей — как в «Файлах»: чекбокс строки, групповые действия
       (замок, удаление) в панели выделения, она заменяет кнопки тулбара.
       Выделение — состояние вкладки, объектом держимся: порядок и сортировка
       записей меняются, объект остаётся */
    const nerSel = st.nerSel;
    const allCb = h("input", {
      type: "checkbox",
      class: "fsel",
      "aria-label": "Выделить все записи",
    });
    allCb.addEventListener("change", () => {
      nerSel.clear();
      if (allCb.checked) visible().forEach((it) => nerSel.add(it));
      renderRows();
      paintSel();
    });
    const selBar = h("div", { class: "files-sel hidden" });

    function selectedItems() {
      return data.items.filter((it) => nerSel.has(it));
    }

    function bulkLock(on) {
      const items = selectedItems();
      items.forEach((it) => UICore.nerSetLocked(it, on));
      renderRows();
      paintSel();
      saveNer();
      toast(
        on
          ? `Зафиксировано записей: ${items.length}`
          : `Замок снят с записей: ${items.length}`,
      );
    }

    /* зафиксированная запись не удаляется ничем — выделенные замки только
       считаются пропущенными, они не снимаются молча */
    function bulkDelete() {
      const items = selectedItems();
      const keep = items.filter((it) => UICore.nerIsLocked(it));
      const victims = items.filter((it) => !UICore.nerIsLocked(it));
      if (!victims.length) {
        toast(
          keep.length
            ? "Все выделенные зафиксированы — снимите замок"
            : "Нет выделенных записей",
          "err",
        );
        return;
      }
      confirmModal(
        "Удалить термины",
        `Будет удалено записей: ${victims.length}` +
          (keep.length ? ` · зафиксированных пропущено: ${keep.length}` : ""),
        "УДАЛИТЬ",
        async () => {
          const dead = new Set(victims);
          data.items = data.items.filter((it) => !dead.has(it));
          nerSel.clear();
          await saveNer();
          renderRows();
          paintSel();
        },
      );
    }

    function paintSel() {
      const n = nerSel.size;
      tools.classList.toggle("hidden", n > 0);
      selBar.classList.toggle("hidden", n === 0);
      const vis = visible();
      let inView = 0;
      vis.forEach((it) => {
        if (nerSel.has(it)) inView += 1;
      });
      allCb.checked = vis.length > 0 && inView === vis.length;
      allCb.indeterminate = inView > 0 && inView < vis.length;
      if (!n) return;
      selBar.replaceChildren(
        ...[
          h("span", { class: "files-sel-count" }, `выделено: ${n}`),
          iconBtn("lock", "Зафиксировать выделенные", () => bulkLock(true)),
          iconBtn("unlock", "Снять замок с выделенных", () => bulkLock(false)),
          iconBtn("trash", "Удалить выделенные", bulkDelete, true),
          iconBtn("close", "Снять выделение", () => {
            nerSel.clear();
            renderRows();
            paintSel();
          }),
        ].filter((x) => x != null),
      );
    }

    function renderRows() {
      table.replaceChildren(
        h(
          "thead",
          {},
          h(
            "tr",
            {},
            h("th", { class: "ner-th-sel" }, allCb),
            ...visibleCols().map((c) => {
              const active = sortField === c;
              const mark = active ? (sortDir === "desc" ? " ↓" : " ↑") : "";
              return h(
                "th",
                {
                  class: "ner-th" + (active ? " ner-th-active" : ""),
                  "aria-sort": active
                    ? sortDir === "desc"
                      ? "descending"
                      : "ascending"
                    : "none",
                },
                h(
                  "button",
                  {
                    type: "button",
                    class: "ner-th-btn",
                    title: `Сортировать по «${colLabel(c)}»`,
                    onclick: () => {
                      const next = UICore.nextNerSort(sortField, sortDir, c);
                      sortField = next.field;
                      sortDir = next.dir;
                      pref.set("ner", { sortField, sortDir });
                      pg.page = 0;
                      renderRows();
                    },
                  },
                  colLabel(c),
                  mark ? h("span", { class: "ner-th-dir" }, mark) : "",
                ),
              );
            }),
            h("th", { class: "ner-th-actions" }, ""),
          ),
        ),
        tbody,
      );
      // список режет страницу и рисует pager — компонент
      pg.items = visible(); // отфильтровано и отсортировано
    }

    /* Замок термина (служебное поле «_locked», NER_LOCK_FIELD): запись не
       правится и не удаляется ни руками, ни прогоном проверки, а
       apply_ner_patches пропускает по ней правки. Снятый замок ключ удаляет,
       поэтому в ner.json он появляется только когда реально стоит. */
    function toggleLock(it) {
      const on = !UICore.nerIsLocked(it);
      UICore.nerSetLocked(it, on);
      renderRows();
      saveNer();
      toast(
        on
          ? `Термин «${it.term}» зафиксирован`
          : `Замок снят: «${it.term}»`,
      );
    }

    /* чекбокс строки: выделение держится на самом объекте записи, строка
       помечается тем же классом, что и в «Файлах» */
    function selCell(it) {
      const cb = h("input", {
        type: "checkbox",
        class: "fsel",
        checked: nerSel.has(it),
        "aria-label": `Выбрать ${String(it.term || "")}`,
      });
      cb.addEventListener("change", () => {
        if (cb.checked) nerSel.add(it);
        else nerSel.delete(it);
        paintSel();
      });
      return h("td", { class: "ner-td-sel" }, cb);
    }

    function viewRow(it) {
      const locked = UICore.nerIsLocked(it);
      const acts = [];
      if (!locked) {
        // у зафиксированной записи правки и удаления нет вовсе
        acts.push(iconBtn("pencil", "Редактировать", () => startEdit(it)));
        acts.push(
          iconBtn(
            "trash",
            "Удалить термин",
            () =>
              confirmModal(
                "Удалить термин",
                String(it.term || ""),
                "УДАЛИТЬ",
                async () => {
                  data.items = data.items.filter((x) => x !== it);
                  await saveNer();
                  renderRows();
                },
              ),
            true,
          ),
        );
      }
      acts.push(
        iconBtn(
          locked ? "lock" : "unlock",
          locked
            ? "Зафиксирован — снять замок"
            : "Зафиксировать: не правится, не удаляется, не уходит на проверку",
          () => toggleLock(it),
        ),
      );
      return h(
        "tr",
        {
          class:
            "ner-row" + (locked ? " ner-row-locked" : "")
            + (nerSel.has(it) ? " frow-sel" : ""),
        },
        selCell(it),
        ...visibleCols().map((c) =>
          h(
            "td",
            {
              // служебные поля (_votes_*) приходят структурой: ширину задаём
              // на самом td — max-width в табличной раскладке браузер
              // игнорирует, и один термин растягивает таблицу на весь экран
              class:
                (c === "type" ? "ner-type " : "")
                + (isStruct(it[c]) ? "ner-cell-struct" : ""),
              title: isStruct(it[c]) ? cellText(it[c]) : "",
            },
            cellText(it[c]),
          ),
        ),
        h("td", { class: "ner-actions" }, ...acts),
      );
    }

    function editRow(it) {
      const inputs = {};
      const tr = h(
        "tr",
        { class: "ner-row ner-editing" },
        h("td", { class: "ner-td-sel" }),
        ...visibleCols().map((c) => {
          /* Значение-объект редактируется как JSON-текст; при
             коммите парсим — невалидный JSON не сохраняется */
          const struct = isStruct(it[c]);
          const inp = struct
            ? h("textarea", {
                class: "input input-sm ner-json-cell",
                rows: "3",
                spellcheck: "false",
                value: cellText(it[c]),
              })
            : h("input", {
                class: "input input-sm",
                value: cellText(it[c]),
              });
          inp.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && !(struct && e.shiftKey)) {
              e.preventDefault();
              commit();
            } else if (e.key === "Escape") {
              e.preventDefault();
              cancel();
            }
          });
          inputs[c] = inp;
          return h("td", { class: struct ? "ner-json-td" : "" }, inp);
        }),
        h(
          "td",
          { class: "ner-actions" },
          h("button", { class: "btn btn-sm", onclick: commit }, "✓"),
          h("button", { class: "btn btn-sm btn-ghost", onclick: cancel }, "✕"),
        ),
      );
      function commit() {
        for (const c of visibleCols()) {
          const raw = inputs[c].value;
          if (isStruct(it[c])) {
            if (raw.trim()) {
              try {
                it[c] = JSON.parse(raw);
              } catch {
                toast(
                  `Невалидный JSON в «${colLabel(c)}» — правка не сохранена`,
                  "err",
                );
                return;
              }
            } else {
              it[c] = ""; // очистка значения
            }
          } else {
            it[c] = raw;
          }
        }
        if (it.__new) {
          const t = String(it.term || "").trim();
          const tr = String(it.translation || "").trim();
          if (!t || !tr) {
            toast("Термин и перевод не могут быть пустыми", "err");
            return;
          }
          it.term = t;
          it.translation = tr;
        }
        delete it.__new;
        editing = null;
        renderRows();
        saveNer();
      }
      function cancel() {
        if (it.__new) data.items = data.items.filter((x) => x !== it);
        editing = null;
        renderRows();
      }
      setTimeout(() => inputs[visibleCols()[0]]?.focus(), 0);
      return tr;
    }

    function startEdit(it) {
      editing = it;
      const vis = visible();
      const idx = vis.indexOf(it);
      if (idx >= 0) pg.page = Math.floor(idx / PAGE_SIZE); // На свою страницу
      renderRows();
    }

    /* «Столбцы»: чекбоксы всех ключей записи (единая модалка «Все / набор»),
       подпись как у полей и типов */
    const colBtn = h(
      "button",
      {
        class: "btn btn-sm btn-ghost ner-cols-btn",
        title: "Какие столбцы показывать",
      },
      colsLabel(),
    );
    function refreshColBtn() {
      colBtn.textContent = colsLabel();
    }
    colBtn.addEventListener("click", () => {
      openToggleAllModal({
        title: "Столбцы глоссария",
        text: "Отображаемые поля записей ner.json:",
        allLabel: "Все",
        keys: [...knownKeys],
        labelOf: colLabel,
        get: () => cols,
        set: (next) => {
          cols = next;
          saveCols();
          refreshColBtn();
          renderRows();
        },
        reset: () => {
          cols = [...DEFAULT_COLS];
          saveCols();
          refreshColBtn();
          renderRows();
        },
      });
    });



    /* поля поиска: по умолчанию — текущие отображаемые столбцы; «Сбросить»
       возвращает именно их, «Все» в модалке — все ключи записи */
    const searchFieldsBtn = h(
      "button",
      {
        class: "btn btn-sm btn-ghost ner-fields-btn",
        title: "Где искать",
      },
      searchFieldsLabel(),
    );
    function refreshSearchFieldsBtn() {
      searchFieldsBtn.textContent = searchFieldsLabel();
    }
    searchFieldsBtn.addEventListener("click", () => {
      openToggleAllModal({
        title: "Где искать",
        text: "Поля записи, в которых ищется строка:",
        allLabel: "Все",
        keys: [...knownKeys],
        labelOf: colLabel,
        get: () => (searchFields == null ? visibleCols() : searchFields),
        set: (next) => {
          searchFields = next == null ? [...knownKeys] : next;
          saveSearchFields();
          refreshSearchFieldsBtn();
          pg.page = 0;
          renderRows();
        },
        reset: () => {
          searchFields = null;
          saveSearchFields();
          refreshSearchFieldsBtn();
          pg.page = 0;
          renderRows();
        },
      });
    });
    const typeBtn = h(
      "button",
      {
        class: "btn btn-sm btn-ghost ner-types-btn",
        title: "Фильтр типов",
      },
      typeFilterLabel(),
    );
    function refreshTypeBtn() {
      typeBtn.textContent = typeFilterLabel();
    }
    typeBtn.addEventListener("click", () => {
      openToggleAllModal({
        title: "Типы",
        text: "Какие типы записей показывать:",
        allLabel: "Все",
        keys: typeNames(),
        labelOf: (t) => {
          const n = (data.by_type || {})[t];
          return n == null ? t : `${t} (${n})`;
        },
        get: () => typeFilter,
        set: (next) => {
          typeFilter = next;
          saveTypeFilter();
          refreshTypeBtn();
          pg.page = 0;
          renderRows();
        },
        reset: () => {
          typeFilter = null;
          saveTypeFilter();
          refreshTypeBtn();
          pg.page = 0;
          renderRows();
        },
      });
    });

    /* «Добавить столбец»: новое поле всем терминам глоссария */
    const addColBtn = h(
      "button",
      { class: "btn btn-sm btn-ghost", title: "Добавить столбец ко всем терминам" },
      "Добавить столбец",
    );
    addColBtn.addEventListener("click", () => {
      const inp = h("input", {
        class: "input",
        placeholder: "имя столбца (ключ JSON)",
      });
      const err2 = h("div", { class: "form-error" });
      UIC.modal({
        title: "Новый столбец",
        build: (close) => [
          inp,
          err2,
          h(
            "div",
            { class: "modal-actions" },
            h("button", { class: "btn btn-ghost", onclick: close }, "Отмена"),
            h(
              "button",
              {
                class: "btn btn-primary",
                onclick: () => {
                  const name = inp.value.trim();
                  if (!name) {
                    err2.textContent = "Введите имя столбца";
                    return;
                  }
                  if (knownKeys.has(name)) {
                    err2.textContent = "Такой столбец уже есть";
                    return;
                  }
                  knownKeys.add(name);
                  data.items.forEach((it) => {
                    if (it[name] === undefined) it[name] = "";
                  });
                  if (cols != null) cols.push(name);
                  saveCols();
                  refreshColBtn();
                  renderRows();
                  saveNer();
                  close();
                },
              },
              "Добавить",
            ),
          ),
        ],
      });
      inp.focus();
    });
    /* «Удалить столбец»: поле из всех терминов (кроме term) */
    const delColBtn = h(
      "button",
      { class: "btn btn-sm btn-ghost", title: "Удалить столбец из всех терминов" },
      "Удалить столбец",
    );
    delColBtn.addEventListener("click", () => {
      const keys = [...knownKeys].filter((k) => k !== "term");
      if (!keys.length) {
        toast("Нет столбцов для удаления", "err");
        return;
      }
      const sel = h(
        "select",
        { class: "input" },
        ...keys.map((k) => h("option", { value: k }, k)),
      );
      const err2 = h("div", { class: "form-error" });
      UIC.modal({
        title: "Удалить столбец",
        build: (close) => [
          h(
            "div",
            { class: "modal-text" },
            "Поле удаляется из всех терминов глоссария:",
          ),
          sel,
          err2,
          h(
            "div",
            { class: "modal-actions" },
            h("button", { class: "btn btn-ghost", onclick: close }, "Отмена"),
            h(
              "button",
              {
                class: "btn btn-danger",
                onclick: () => {
                  const name = sel.value;
                  if (!name) {
                    err2.textContent = "Выберите столбец";
                    return;
                  }
                  confirmModal(
                    "Удалить столбец",
                    `Поле ${name} будет удалено из всех терминов глоссария`,
                    "УДАЛИТЬ",
                    async () => {
                      knownKeys.delete(name);
                      data.items.forEach((it) => delete it[name]);
                      if (cols != null) {
                        cols = cols.filter((c) => c !== name);
                        if (!cols.length) cols = [...DEFAULT_COLS];
                      }
                      saveCols();
                      refreshColBtn();
                      renderRows();
                      saveNer();
                      close();
                    },
                  );
                },
              },
              "Удалить",
            ),
          ),
        ],
      });
    });
    /* «Удалить по фильтру»: термины по условию (count > N) или все найденные */
    const delFilterBtn = h(
      "button",
      {
        class: "btn btn-sm btn-ghost",
        title: "Удалить термины по условию или все найденные",
      },
      "Удалить по фильтру",
    );
    delFilterBtn.addEventListener("click", () => {
      const fieldSel = h(
        "select",
        { class: "input" },
        ...[...knownKeys].map((k) => h("option", { value: k }, k)),
      );
      fieldSel.value = "count";
      const opSel = h(
        "select",
        { class: "input" },
        h("option", { value: ">" }, ">"),
        h("option", { value: "<" }, "<"),
        h("option", { value: "=" }, "="),
        h("option", { value: "≠" }, "≠"),
      );
      const valInp = h("input", { class: "input", placeholder: "значение" });
      const countEl = h("div", { class: "field-help" }, "");
      const err2 = h("div", { class: "form-error" });
      function victims() {
        // условие применяется ко ВСЕМ незафиксированным терминам: замок
        // значит «не трогать», массовое удаление его не снимает
        const raw = valInp.value.trim();
        if (!raw) return [];
        const num = Number(raw);
        return data.items.filter((it) => {
          if (UICore.nerIsLocked(it)) return false;
          const v = it[fieldSel.value];
          const s = String(v == null ? "" : v);
          // «=»/«≠» — СОДЕРЖИТ/НЕ СОДЕРЖИТ (по подстроке), а не
          // точное совпадение; «>»/«<» — числовое сравнение, если
          // обе стороны числа (иначе строковое)
          if (opSel.value === "=") return s.includes(raw);
          if (opSel.value === "≠") return !s.includes(raw);
          if (
            Number.isFinite(num) &&
            v != null &&
            v !== "" &&
            Number.isFinite(Number(v))
          ) {
            const n = Number(v);
            if (opSel.value === ">") return n > num;
            return n < num;
          }
          if (opSel.value === ">") return s > raw;
          return s < raw;
        });
      }
      function refreshCount() {
        countEl.textContent = `Будет удалено: ${victims().length}`;
      }
      [fieldSel, opSel, valInp].forEach((el) =>
        el.addEventListener("input", refreshCount),
      );
      refreshCount();
      UIC.modal({
        title: "Удалить термины по фильтру",
        build: (close) => [
          h(
            "div",
            { class: "modal-text" },
            "Условие применяется ко всем незафиксированным терминам ",
            "глоссария. ",
            "«=» — содержит, «≠» — не содержит (по подстроке, ",
            "не точное совпадение); «>»/«<» — больше/меньше.",
          ),
          h("div", { class: "ner-del-filter" }, fieldSel, opSel, valInp),
          countEl,
          err2,
          h(
            "div",
            { class: "modal-actions" },
            h("button", { class: "btn btn-ghost", onclick: close }, "Отмена"),
            h(
              "button",
              {
                class: "btn btn-danger",
                onclick: () => {
                  const vics = victims();
                  if (!vics.length) {
                    err2.textContent = "Нет записей под условием";
                    return;
                  }
                  confirmModal(
                    "Удалить термины",
                    `Будет удалено терминов: ${vics.length}`,
                    "УДАЛИТЬ",
                    async () => {
                      const set = new Set(vics);
                      data.items = data.items.filter((it) => !set.has(it));
                      saveNer();
                      renderRows();
                      close();
                      toast(`Удалено терминов: ${vics.length}`);
                    },
                  );
                },
              },
              "Удалить",
            ),
          ),
        ],
      });
    });

    const addTerm = () => {
      const it = { term: "", type: "noun", translation: "", __new: true };
      data.items.unshift(it); // новый термин — сверху
      editing = it;
      // сбросить поиск и фильтр типов — иначе новая запись (count=null,
      // пустой термин) не пройдёт фильтр и не будет видна
      search.value = "";
      typeFilter = null;
      pg.page = 0;
      renderRows();
    };
    /* Экспорт для анализа: настройки в модалке, файл скачивается */
    const exportBtn = h(
      "button",
      {
        class: "btn btn-sm btn-ghost",
        title: "Скачать ner.json для анализа (все записи)",
      },
      "⬇ Экспорт для анализа",
    );
    exportBtn.addEventListener("click", () =>
      exportModal(data.by_type || {}, `${section}/${name}`),
    );
    /* панель вкладки: поиск плюс ОДНА кнопка «⋮» со всеми настройками показа и
       правки (по образцу «Файлов»); пока что-то выделено, панель выделения
       заменяет эти кнопки */
    const menu = UIC.menuButton(
      [
        { el: lockBox },
        { el: colBtn },
        { el: typeBtn },
        { el: searchFieldsBtn },
        { sep: true },
        { el: addColBtn },
        { label: "Добавить термин", action: addTerm },
        { sep: true },
        { el: delColBtn },
        { el: delFilterBtn },
      ],
      {
        iconName: "kebab",
        btnClass: "ner-menu-btn",
        title: "Показ и правка глоссария",
        aria: "Дополнительные настройки глоссария",
      },
    );
    const tools = h("div", { class: "files-tools" }, menu, exportBtn);
    const toolbar = h(
      "div",
      { class: "files-toolbar" },
      h("span", { class: "ner-search" }, search),
      h("span", { class: "spacer" }),
      tools,
      selBar,
    );
    search.addEventListener("input", () => {
      pg.page = 0; // Фильтр — на первую страницу
      renderRows();
      paintSel();
    });
    renderRows();
    paintSel();
    return h("div", { class: "files-wrap" }, toolbar, table, pg.el);
  }
  /* ── Проверка (review ner / translate_check_llm) ── */
  async function reviewView() {
    const wrap = h("div", { class: "review-wrap" });
    const panel = h("div", { class: "review-panel" });
    const q = new URLSearchParams({ project: `${section}/${name}` });
    // карточка LLM-проверки: глоссарий (1) и перевод (3)
    function makeCard(title, path, applyPath, kind) {
      // kind: "ner" | "tcl" — формат записей и переход к тексту правки
      const card = h("div", { class: "review-card" });
      const err = h("div", { class: "form-error" });
      const status = h("div", { class: "review-status" });
      const dryBtn = h(
        "button",
        { class: "btn btn-sm btn-ghost" },
        "Пробный прогон",
      );
      const applyBtn = h("button", { class: "btn btn-sm" }, "Применить");
      const body = h("div", { class: "review-card-body" });
      const stKey = `review:${kind}`;
      st.review[kind] = st.review[kind] || { mode: "list" };
      const seg = h(
        "div",
        { class: "seg" },
        h(
          "button",
          {
            class:
              "btn btn-sm seg-btn" +
              (st.review[kind].mode === "list" ? " seg-active" : ""),
            onclick: () => setMode("list"),
          },
          "Список правок",
        ),
        h(
          "button",
          {
            class:
              "btn btn-sm seg-btn" +
              (st.review[kind].mode === "editor" ? " seg-active" : ""),
            onclick: () => setMode("editor"),
          },
          "Редактор JSON",
        ),
      );
      card.append(
        h(
          "div",
          { class: "review-card-title" },
          h("span", { text: title }),
          h("span", { class: "spacer" }),
          seg,
        ),
        body,
      );
      // ── состояние: распарсенный review-файл + редактор ──
      let parsed = null; // {doc, entries, isArray} | null (нет файла)
      let ed = null;
      const RV_PAGE = 100; // правок на страницу списка
      const RV_LIST = h("div", { class: "rv-list" });
      /* страница списка правок — общий пейджер (на одной странице молчит);
         индекс записи глобальный (page * RV_PAGE + i): кнопки правят запись
         по позиции в parsed.entries, ner-группировка — по предыдущей */
      const RV_PAGER = UIC.listPager({
        pageSize: RV_PAGE,
        list: RV_LIST,
        hideSinglePage: true,
        info: (total, page, pages) => ` ${page} / ${pages} · всего ${total} `,
        rows: (slice, page) =>
          slice.map((e, i) => {
            const prev = parsed.entries[page * RV_PAGE + i - 1];
            const cont =
              kind === "ner" && prev != null && prev["term"] === e["term"];
            return entryRow(e, page * RV_PAGE + i, { cont });
          }),
      });
      async function load() {
        const d = await api(path + "?" + q);
        status.textContent = d.exists
          ? `файл есть · ${fmtSize(d.size)}`
          : "файла ещё нет — создастся при применении";
        parsed = d.exists ? UICore.parseReviewContent(d.content) : null;
        return d;
      }
      async function save() {
        await api(path, {
          method: "PUT",
          body: {
            project: `${section}/${name}`,
            content: JSON.stringify(parsed.doc, null, 2),
          },
        });
        parsed.entries = parsed.isArray ? parsed.doc : parsed.doc["entries"];
      }
      async function deleteEntry(i) {
        err.textContent = "";
        try {
          const doc2 = UICore.removeReviewEntry(parsed.doc, i, parsed.isArray);
          if (!doc2) {
            err.textContent = "Не удалось удалить запись";
            return;
          }
          parsed.doc = doc2;
          await save();
          renderList();
          toast("Правка удалена");
        } catch (ex) {
          err.textContent = ex.message;
        }
      }
      function refresh() {
        load()
          .then(() => {
            if (ed)
              ed.setValue(parsed ? JSON.stringify(parsed.doc, null, 2) : "");
            renderCard();
          })
          .catch((ex) => (err.textContent = ex.message));
      }
      // ── режим «Список правок» ──
      function setMode(next) {
        if (next === st.review[kind].mode) return;
        st.review[kind].mode = next;
        renderCard();
      }
      /* ner-правки бывают двух действий: патч поля и удаление термина целиком
         (LLM помечает термин лишним) — у удаления поля не сверяются */
      function isDel(e) {
        return kind === "ner" && UICore.nerAction(e) === "удаление";
      }
      function entryHead(e) {
        if (kind === "ner") {
          return `${e["term"] || "?"} · ` +
            (isDel(e) ? "удаление термина" : e["field"] || "?");
        }
        const ch = e["chapter"];
        return (
          `Гл.${ch == null ? "?" : ch}` + (e["type"] ? ` · ${e["type"]}` : "")
        );
      }
      function entryMeta(e) {
        const parts = [];
        if (e["stage"]) parts.push(e["stage"]);
        if (e["applied_at"]) parts.push(`применено: ${e["applied_at"]}`);
        if (e["note"]) parts.push(e["note"]);
        return parts.join(" · ");
      }
      function gotoEntry(e) {
        if (kind === "ner") {
          st.view = "ner";
          st.edit = null;
          st.search = e["term"] || "";
          render();
          return;
        }
        // tcl: файл главы из записи (абсолютный путь → chapters/…)
        const m = String(e["file"] || "")
          .replace(/\\/g, "/")
          .match(/\/chapters\/([^/]+\/.+)$/);
        if (m) {
          st.edit = "chapters/" + m[1];
          st.search = e["old"] || "";
          render();
        } else {
          toast("В записи нет пути к файлу главы", "err");
        }
      }
      function statusBtn(i, e, s, label) {
        const active = e["status"] === s;
        return h(
          "button",
          {
            class:
              "btn btn-xs btn-ghost rv-act" + (active ? " rv-act-active" : ""),
            title: active ? "уже выбран" : `установить «${s}»`,
            onclick: async () => {
              err.textContent = "";
              try {
                const doc2 = UICore.updateReviewEntry(
                  parsed.doc,
                  i,
                  { status: s },
                  parsed.isArray,
                );
                if (!doc2) {
                  err.textContent = "Не удалось обновить запись";
                  return;
                }
                parsed.doc = doc2;
                await save();
                renderList();
              } catch (ex) {
                err.textContent = ex.message;
              }
            },
          },
          label,
        );
      }
      function correctModal(i, e) {
        const del = isDel(e);
        const oldIn = h(
          "textarea",
          { class: "input rv-ta rv-old-ro", rows: 2, readonly: true },
          e["old"] || "",
        );
        const newIn = h(
          "textarea",
          { class: "input rv-ta", rows: 2 },
          e["new"] || "",
        );
        const reasonIn = h("input", {
          class: "input",
          value: e["reason"] || "",
        });
        const err2 = h("div", { class: "form-error" });
        UIC.modal({
          title: del
            ? `Удаление термина · правка ${i + 1}`
            : `Правка ${i + 1}`,
          build: (close) => [
            h("label", { class: "rv-label" }, del ? "Что уходит" : "Было"),
            oldIn,
            h(
              "div",
              { class: "field-help" },
              del
                ? "текущие значения полей термина; принять — удалить запись из ner.json"
                : "не редактируется — по этому тексту правка ищется в главе",
            ),
            ...(del ? [] : [h("label", { class: "rv-label" }, "Стало"), newIn]),
            h("label", { class: "rv-label" }, "Причина"),
            reasonIn,
            err2,
            h(
              "div",
              { class: "modal-actions" },
              h("button", { class: "btn btn-ghost", onclick: close }, "Отмена"),
              h(
                "button",
                {
                  class: "btn btn-primary",
                  onclick: async () => {
                    err2.textContent = "";
                    const doc2 = UICore.updateReviewEntry(
                      parsed.doc,
                      i,
                      del
                        ? {
                            action: "удаление",
                            field: "",
                            old: oldIn.value.trim(),
                            reason: reasonIn.value.trim(),
                          }
                        : {
                            old: oldIn.value.trim(),
                            new: newIn.value.trim(),
                            reason: reasonIn.value.trim(),
                          },
                      parsed.isArray,
                    );
                    if (!doc2) {
                      err2.textContent = "Не удалось обновить запись";
                      return;
                    }
                    try {
                      parsed.doc = doc2;
                      await save();
                      close();
                      renderList();
                      toast("Правка сохранена");
                    } catch (ex) {
                      err2.textContent = ex.message;
                    }
                  },
                },
                "Сохранить",
              ),
            ),
          ],
        });
        (del ? reasonIn : newIn).focus();
      }
      function entryRow(e, i, opts) {
        const cont = opts && opts["cont"]; // продолжение группы термина
        const applied = e["applied"];
        const accepted = e["status"] === "принять";
        const rejected = e["status"] === "отклонить";
        const badge = applied
          ? h("span", { class: "badge badge-done" }, "применено")
          : accepted
            ? h("span", { class: "badge badge-accept" }, "принять")
            : rejected
              ? h("span", { class: "badge badge-reject" }, "отклонить")
              : h("span", { class: "badge" }, "новая");
        const actions = h(
          "div",
          { class: "rv-actions" },
          statusBtn(i, e, "принять", "Принять"),
          statusBtn(i, e, "отклонить", "Отклонить"),
          h(
            "button",
            {
              class: "btn btn-xs btn-ghost",
              onclick: () => correctModal(i, e),
            },
            "Откорректировать",
          ),
          h(
            "button",
            {
              class: "btn btn-xs btn-ghost rv-del",
              title: "Удалить правку из файла",
              onclick: () =>
                confirmModal(
                  "Удалить правку",
                  `Запись «${entryHead(e)}» будет удалена из файла`,
                  "УДАЛИТЬ",
                  async () => deleteEntry(i),
                ),
            },
            "Удалить",
          ),
          h(
            "button",
            {
              class: "btn btn-xs btn-ghost",
              title:
                kind === "ner"
                  ? "Открыть глоссарий с этим термином"
                  : "Открыть файл главы с фрагментом",
              onclick: () => gotoEntry(e),
            },
            kind === "ner" ? "→ Глоссарий" : "→ Глава",
          ),
        );
        return h(
          "div",
          {
            class:
              "rv-row" +
              (rejected ? " rv-row-reject" : "") +
              (cont ? " rv-row-cont" : "") +
              (isDel(e) ? " rv-row-del" : ""),
          },
          h(
            "div",
            { class: "rv-row-head" },
            h(
              "span",
              { class: "rv-row-title" },
              cont && kind === "ner"
                ? isDel(e)
                  ? "удаление термина"
                  : e["field"] || "?"
                : entryHead(e),
            ),
            badge,
          ),
          h(
            "div",
            { class: "rv-diff" },
            h("span", { class: "rv-old" }, e["old"] || ""),
            " → ",
            isDel(e)
              ? h("span", { class: "rv-del" }, "термин удалится из глоссария")
              : h("span", { class: "rv-new" }, e["new"] || ""),
          ),
          e["reason"] ? h("div", { class: "rv-reason" }, e["reason"]) : null,
          h("div", { class: "rv-meta" }, entryMeta(e)),
          actions,
        );
      }
      function clearAllEntries() {
        const n = parsed && parsed.entries ? parsed.entries.length : 0;
        confirmModal(
          "Очистить правки",
          `Будут удалены все правки из файла (${n} шт.)`,
          "УДАЛИТЬ",
          async () => {
            err.textContent = "";
            try {
              const empty = parsed && parsed.isArray ? [] : { entries: [] };
              await api(path, {
                method: "PUT",
                body: {
                  project: `${section}/${name}`,
                  content: JSON.stringify(empty, null, 2),
                },
              });
              toast("Все правки удалены");
              refresh();
            } catch (ex) {
              err.textContent = ex.message;
            }
          },
        );
      }
      async function setAllStatus(status) {
        err.textContent = "";
        try {
          const entries = parsed.isArray ? parsed.doc : parsed.doc["entries"];
          let doc2 = parsed.doc;
          let changed = 0;
          for (let i = 0; i < entries.length; i++) {
            const e = entries[i] || {};
            if (e["applied"] || e["status"] === status) continue;
            const next = UICore.updateReviewEntry(
              doc2,
              i,
              { status: status },
              parsed.isArray,
            );
            if (!next) continue;
            doc2 = next;
            changed++;
          }
          if (!changed) {
            toast("Статусы уже установлены");
            return;
          }
          parsed.doc = doc2;
          await save();
          renderList();
          toast(`Установлено «${status}»: ${changed}`);
        } catch (ex) {
          err.textContent = ex.message;
        }
      }
      function renderList() {
        body.replaceChildren();
        if (!parsed || !parsed.ok) {
          body.append(
            h(
              "div",
              { class: "files-empty" },
              "Файл правок не прочитан (невалидный JSON) — переключитесь на «Редактор JSON» или запустите проверку",
            ),
          );
        } else if (parsed.entries.length) {
          const sum = UICore.reviewSummary(parsed.entries);
          body.append(
            h(
              "div",
              { class: "review-summary" },
              h("span", { class: "badge" }, `всего: ${sum.total}`),
              h(
                "span",
                { class: "badge badge-accept" },
                `принято: ${sum.accepted}`,
              ),
              h(
                "span",
                { class: "badge badge-reject" },
                `отклонено: ${sum.rejected}`,
              ),
              h(
                "span",
                { class: "badge badge-done" },
                `применено: ${sum.applied}`,
              ),
              h("span", { class: "spacer" }),
              h(
                "button",
                {
                  class: "btn btn-xs btn-ghost",
                  title: "Всем неприменённым правкам — статус «принять»",
                  onclick: () => setAllStatus("принять"),
                },
                "Принять все",
              ),
              h(
                "button",
                {
                  class: "btn btn-xs btn-ghost",
                  title: "Всем неприменённым правкам — статус «отклонить»",
                  onclick: () => setAllStatus("отклонить"),
                },
                "Отклонить все",
              ),
              h(
                "button",
                {
                  class: "btn btn-xs",
                  title:
                    "Применить все правки со статусом «принять» (как кнопка «Применить» внизу)",
                  onclick: () => runApply(false),
                },
                "Применить все принятые",
              ),
              h(
                "button",
                {
                  class: "btn btn-xs btn-ghost rv-clear",
                  title: "Удалить все правки из файла",
                  onclick: () =>
                    clearAllEntries(),
                },
                "Очистить",
              ),
            ),
            RV_LIST,
            RV_PAGER.el,
          );
        RV_PAGER.items = parsed.entries;
        } else {
          body.append(
            h(
              "div",
              { class: "card-hint" },
              "Правок ещё нет — запустите проверку (вкладка «Запуски»)",
            ),
          );
        }
        body.append(status, actionsBar, err);
      }
      // ── применение: запуск + уведомление о результате ──
      async function runApply(dry) {
        err.textContent = "";
        try {
          const r = await api(applyPath, {
            method: "POST",
            body: {
              project: `${section}/${name}`,
              dry_run: dry,
            },
          });
          toast(`${dry ? "Пробный прогон" : "Применение"}: запущено`);
          watchJob(r.job && r.job.id, dry);
        } catch (ex) {
          err.textContent = ex.message;
        }
      }
      function watchJob(jobId, dry) {
        const key = `${section}/${name}:${stKey}`;
        const prev = _reviewWatchers.get(key);
        if (prev) {
          clearInterval(prev.timer);
          _reviewWatchers.delete(key);
        }
        if (!jobId) return;
        const label = dry ? "Пробный прогон" : "Применение";
        const timer = setInterval(async () => {
          let job = null;
          try {
            const r = await api(`/jobs/${jobId}`);
            job = r.job;
          } catch {
            /* сеть — пробуем ещё раз */
          }
          if (!job || job.status === "running") return;
          clearInterval(timer);
          _reviewWatchers.delete(key);
          if (job.status === "done") toast(`✅ ${label} завершено`);
          else if (job.status === "failed")
            toast(`❌ ${label}: ошибка — смотрите лог запуска`, "err");
          else if (job.status === "stopped") toast(`⏹ ${label} остановлено`);
          refresh();
        }, 2000);
        _reviewWatchers.set(key, { timer, jobId });
      }
      dryBtn.addEventListener("click", () => runApply(true));
      applyBtn.addEventListener("click", () => runApply(false));
      // ── режим «Редактор JSON» (прежний интерфейс) ──
      const edHost = h("div", { class: "editor-cm editor-cm-small" });
      const saveBtn = h(
        "button",
        { class: "btn btn-sm btn-ghost" },
        "Сохранить файл",
      );
      saveBtn.addEventListener("click", async () => {
        err.textContent = "";
        try {
          await api(path, {
            method: "PUT",
            body: { project: `${section}/${name}`, content: ed.getValue() },
          });
          toast("Сохранено");
        } catch (ex) {
          err.textContent = ex.message;
        }
      });
      const actionsBar = h(
        "div",
        { class: "review-actions" },
        dryBtn,
        applyBtn,
        h("span", { class: "spacer" }),
        saveBtn,
      );
      function renderEditor() {
        body.replaceChildren();
        if (!ed) {
          ed = makeEditor(
            parsed ? JSON.stringify(parsed.doc, null, 2) : "",
            "json",
          );
          const f = UIC.editorSearch(ed);
          if (f) actionsBar.insertBefore(f, saveBtn);
          edHost.replaceChildren(ed.root);
        }
        body.append(edHost, status, actionsBar, err);
      }
      function renderCard() {
        if (st.review[kind].mode === "editor") renderEditor();
        else renderList();
      }
      load()
        .then(() => {
          if (ed)
            ed.setValue(parsed ? JSON.stringify(parsed.doc, null, 2) : "");
          renderCard();
        })
        .catch((ex) => (err.textContent = ex.message));
      return card;
    }
    /* под-вкладки «Проверок»: каждая проверка — своя вкладка; выбор
       запоминается в localStorage (UI-предпочтения — на клиенте) */
    const REVIEW_TABS = [
      { key: "ner", label: "Глоссарий (LLM)" },
      { key: "tcheck", label: "Перевод" },
      { key: "tcl", label: "Перевод (LLM)" },
      { key: "quality", label: "Оценка перевода (LLM)" },
    ];
    /* вкладка проверки — своя на каждую книгу (была одной на весь интерфейс) */
    let curTab = pref.get("review").tab || null;
    if (!REVIEW_TABS.some((t) => t.key === curTab)) curTab = "ner";
    const items = []; // {key, btn, pane}
    const bar = h("div", { class: "subtabs" });
    function setTab(key) {
      curTab = key;
      pref.set("review", { tab: key });
      for (const it of items) {
        it.btn.classList.toggle("subtab-active", it.key === key);
        it.pane.style.display = it.key === key ? "" : "none";
      }
    }
    for (const t of REVIEW_TABS) {
      const btn = h(
        "button",
        { class: "subtab", onclick: () => setTab(t.key) },
        t.label,
      );
      const pane = h("div", { class: "review-pane" });
      items.push({ key: t.key, btn, pane });
      bar.append(btn);
      panel.append(pane);
    }
    const paneOf = (key) => items.find((it) => it.key === key).pane;
    paneOf("ner").append(
      makeCard(
        "Проверка глоссария (LLM) — ner_review.json",
        "/ner/review",
        "/ner/review/apply",
        "ner",
      ),
    );
    paneOf("tcheck").append(
      h(
        "div",
        { class: "review-section-sub" },
        "отчёты стадии translate_check (logs/check_*.txt)",
      ),
      await renderCheckReports(section, name),
    );
    paneOf("tcl").append(
      makeCard(
        "Проверка перевода (LLM) — translate_check_llm_review.json",
        "/translate_check_llm/review",
        "/translate_check_llm/review/apply",
        "tcl",
      ),
    );
    /* renderCheckReports/renderQualityReports — глобальные функции
       (см. ниже), принимают (section, name) параметрами — без
       глобалов, иначе вкладка падает с ReferenceError */
    paneOf("quality").append(
      h(
        "div",
        { class: "review-section-sub" },
        "md-отчёт стадии translate_quality "
          + "(tmp/translation_quality_assessment.md)",
      ),
      await renderQualityReports(section, name),
    );
    wrap.append(bar, panel);
    setTab(curTab);
    return wrap;
  }

  /* ── Настройки проекта: metadata + обложка ────────────────────────── */
  // «Главы» — названия глав: тип файлов (chapters/translated/redacted/
  // polished), слева номер каталога, справа редактируемая первая строка;
  // одна кнопка «Сохранить» — все изменения разом в соответствующие файлы
  async function chaptersView() {
    const err = h("div", { class: "form-error" });
    const typeSel = h("select", { class: "input chapters-type" });
    attachTooltip(
      typeSel,
      "Какой файл главы править: chapter/translated/redacted/polished",
    );
    const types = ["chapter", "translated", "redacted", "polished"];
    for (const t of types) {
      typeSel.append(h("option", { value: t }, t));
    }
    typeSel.value = st.chaptersType || "polished";
    const saveBtn = h("button", { class: "btn btn-primary" }, "Сохранить");
    saveBtn.disabled = true;
    const delBtn = h("button", { class: "btn btn-danger" }, "Удалить файлы");
    delBtn.disabled = true;
    const status = h("span", { class: "review-status" });
    const rows = h("div", { class: "chapters-rows" });
    // диапазон глав — ДВА поля (начало/конец), как в «Запусках»
    const startIn = h("input", { type: "number", class: "input run-range" });
    const endIn = h("input", { type: "number", class: "input run-range" });
    attachTooltip(startIn, "Начальная глава; пусто = с первой");
    attachTooltip(endIn, "Конечная глава; пусто = до последней");
    let inputs = {}; // id → {input, orig}
    let allIds = []; // непрерывный 1..N (для серых ячеек и префилла)

    function readRange() {
      const a = parseInt(startIn.value || "", 10) || 1;
      const b = parseInt(endIn.value || "", 10) || (allIds.at(-1) || 0);
      if (a > b) return { start: b, end: a };
      return { start: a, end: b };
    }

    async function load() {
      st.chaptersType = typeSel.value;
      pref.set("chapters", { type: st.chaptersType });
      saveBtn.disabled = true;
      delBtn.disabled = true;
      status.textContent = "Загрузка…";
      rows.replaceChildren();
      inputs = {};
      try {
        const r = await api(
          `/projects/${section}/${name}/chapters/titles` +
          `?type=${encodeURIComponent(typeSel.value)}`,
        );
        const titles = r.titles || {};
        allIds = Array.isArray(r.all_ids) ? r.all_ids : [];
        const missing = new Set(r.missing || []);
        const range = readRange();
        let present = 0;
        let absent = 0;
        // строки в ПОРЯДКЕ НОМЕРОВ: существующая глава идёт на своём
        // месте, пропущенная — серой ячейкой «N —» между соседями
        for (let id = range.start; id <= range.end; id++) {
          if (missing.has(id)) {
            absent++;
            rows.append(
              h(
                "div",
                { class: "chapters-row ch-missing" },
                h("span", { class: "ch-num" }, String(id)),
                h("span", { class: "ch-miss-text" }, "—"),
              ),
            );
            continue;
          }
          const title = titles[id] ?? "";
          present++;
          const inp = h("input", {
            class: "input chapters-title",
            value: title,
          });
          inputs[id] = { input: inp, orig: String(title) };
          inp.addEventListener("input", () => {
            saveBtn.disabled = false;
          });
          rows.append(
            h(
              "div",
              { class: "chapters-row" },
              h("span", { class: "ch-num" }, String(id)),
              inp,
            ),
          );
        }
        saveBtn.disabled = true;
        delBtn.disabled = !present;
        status.textContent =
          `Глав: ${present} (${typeSel.value})`
          + (absent ? `, нет файла: ${absent}` : "")
          + (present ? "" : " — файлы не найдены");
        // префилл диапазона полным (один раз)
        if (!startIn.dataset.filled && allIds.length) {
          startIn.value = String(allIds[0]);
          endIn.value = String(allIds.at(-1));
          startIn.dataset.filled = "1";
        }
      } catch (ex) {
        status.textContent = ex.message;
      }
    }

    saveBtn.addEventListener("click", async () => {
      const titles = {};
      let changed = 0;
      for (const id of Object.keys(inputs)) {
        const { input, orig } = inputs[id];
        const v = input.value.trim();
        if (v && v !== orig) {
          titles[id] = v;
          changed++;
        }
      }
      if (!changed) {
        toast("Изменений нет");
        return;
      }
      saveBtn.disabled = true;
      try {
        const r = await api(`/projects/${section}/${name}/chapters/titles`, {
          method: "PUT",
          body: { type: typeSel.value, titles },
        });
        toast(`Сохранено глав: ${r.updated.length}`);
        status.textContent = "Сохранено";
        await load();
      } catch (ex) {
        saveBtn.disabled = false;
        status.textContent = ex.message;
        err.textContent = ex.message;
      }
    });

    startIn.addEventListener("input", load);
    endIn.addEventListener("input", load);
    typeSel.addEventListener("change", load);
    delBtn.addEventListener("click", async () => {
      const range = readRange();
      const present = Object.keys(inputs)
        .map(Number)
        .filter((id) => id >= range.start && id <= range.end);
      if (!present.length) {
        toast("В диапазоне нет файлов");
        return;
      }
      // подтверждение — как во всех местах проекта: ввод слова УДАЛИТЬ
      confirmModal(
        "Удаление файлов глав",
        `Будут удалены файлы ${typeSel.value}.txt глав ` +
        `${present[0]} – ${present.at(-1)} (${present.length} шт.). ` +
        "Действие необратимо.",
        "УДАЛИТЬ",
        async () => {
          delBtn.disabled = true;
          const q = new URLSearchParams({
            type: typeSel.value,
            start: String(range.start),
            end: String(range.end),
          });
          const r = await api(
            `/projects/${section}/${name}/chapters?${q}`,
            { method: "DELETE" },
          );
          toast(`Удалено файлов: ${r.deleted.length}`);
          await load();
        },
      ).catch(() => {
        delBtn.disabled = false;
      });
    });
    load();

    const toolbar = h(
      "div",
      { class: "chapters-toolbar" },
      h("span", { class: "field-label" }, "Тип файлов глав:"),
      typeSel,
      h("span", { class: "field-label" }, "Главы:"),
      startIn,
      h("span", { class: "run-range-sep" }, "–"),
      endIn,
      h("span", { class: "spacer" }),
      status,
      saveBtn,
      delBtn,
    );
    const hint = h(
      "div",
      { class: "card-hint" },
      "Название главы — первая непустая строка файла. " +
        "Правки сохраняются в соответствующие файлы глав одной кнопкой; " +
        "«Удалить файлы» стирает файлы выбранного типа в диапазоне.",
    );
    return h("div", { class: "files-wrap" }, toolbar, hint, err, rows);
  }

  /* «Поиск» — обычный проход по текстам книги (core/search.py): список групп,
     их кластеры и подписи приходят с сервера, совпадение — подстрока, вывод —
     фрагменты с контекстом. Индексов и кешей нет, лимитов на число совпадений
     тоже: показаны ВСЕ результаты, список листается постранично. Глоссарий
     здесь не ищется — у него своя вкладка, туда уходим с запросом. */
  const CTX_DEFAULT = 60;
  const CTX_MAX = 300;

  async function searchView() {
    const LS_KEY = `search:${section}/${name}`;
    let prefs = {};
    try {
      prefs = JSON.parse(localStorage.getItem(LS_KEY) || "{}") || {};
    } catch {
      prefs = {};
    }
    function savePrefs(patch) {
      Object.assign(prefs, patch);
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(prefs));
      } catch {
        /* localStorage недоступен (приватный режим) — не критично */
      }
    }

    const err = h("div", { class: "form-error" });
    const status = h(
      "span",
      { class: "review-status search-status" },
      "Поиска не было",
    );
    const rows = h("div", { class: "search-rows" });
    const chips = h("div", { class: "search-chips" });
    const input = h("input", {
      class: "input search-q",
      placeholder: "Текст для поиска…",
      value: String(prefs.q || ""),
    });
    attachTooltip(input, "Ищется подстрока; перенос строки ищется построчно");
    const ctxIn = h("input", {
      class: "input search-ctx",
      type: "number",
      min: "0",
      max: String(CTX_MAX),
      step: "10",
      value: String(prefs.context || CTX_DEFAULT),
      "aria-label": "Контекст: СИМВОЛОВ до и после совпадения",
    });
    attachTooltip(
      ctxIn,
      `Сколько символов брать до и после совпадения (не больше ${CTX_MAX})`,
    );
    const caseBox = h("input", {
      type: "checkbox",
      class: "checkbox search-case",
    });
    caseBox.checked = prefs.ci === true;
    attachTooltip(caseBox, "Учитывать регистр букв");
    const runBtn = h("button", { class: "btn btn-primary search-run" }, "Найти");

    // реестр групп, кластеров и дефолтный охват — с сервера: в браузере
    // реестр не дублируется, SPA только рисует чипсы
    let clusters = [];
    let groups = [];
    let defaults = [];
    // куда клик по файлу группы ведёт: editor | glossary (4-й элемент меты)
    const groupOpen = {};
    let scopes = Array.isArray(prefs.scopes) && prefs.scopes.length
      ? prefs.scopes.slice() : null; // null — дефолт сервера

    const currentScopes = () => scopes || defaults.slice();

    function pick(list) {
      scopes = groups.filter((g) => list.includes(g[0])).map((g) => g[0]);
      savePrefs({ scopes });
      renderChips();
    }

    function scopeChip(g) {
      const box = h("input", { type: "checkbox", class: "checkbox search-scope" });
      box.checked = currentScopes().includes(g[0]);
      box.addEventListener("change", () => {
        const cur = currentScopes().slice();
        const at = cur.indexOf(g[0]);
        if (box.checked && at < 0) cur.push(g[0]);
        if (!box.checked && at >= 0) cur.splice(at, 1);
        if (!cur.length) {
          box.checked = true; // хотя бы одна группа обязана остаться
          return;
        }
        pick(cur);
      });
      return h("label", { class: "search-chip search-scope-chip" }, box, g[1]);
    }

    /* охват двумя блоками: файлы глав (имена — как в поле «Тип файлов глав»
       форм стадий) и остальное */
    function renderChips() {
      /* показываются ВСЕ кластеры и все их группы: чипсы — это выбор охвата,
         снимать группу кнопкой «Только главы» не значит прятать её соседей */
      const blocks = clusters
        .filter((c) => groups.some((g) => g[2] === c[0]))
        .map((c) =>
          h(
            "span",
            { class: "search-cluster" },
            h("span", { class: "search-cluster-label" }, `${c[1]}:`),
            ...groups.filter((g) => g[2] === c[0]).map(scopeChip),
          ));
      chips.replaceChildren(
        ...blocks,
        h("button", {
          class: "btn btn-sm btn-ghost",
          title: "Оставить только артефакты стадий глав",
          onclick: () => pick(CHAPTER_SCOPES),
        }, "Только главы"),
        h("button", {
          class: "btn btn-sm btn-ghost",
          title: "Искать и в промптах, отчётах, логах",
          onclick: () => pick(groups.map((g) => g[0])),
        }, "Все группы"),
      );
    }

    function hitNode(hit) {
      const t = String((hit && hit.text) || "");
      const s = Math.max(0, Math.min(t.length, Number(hit.start) || 0));
      const e = Math.max(s, Math.min(t.length, Number(hit.end) || 0));
      return h(
        "div",
        { class: "search-hit" },
        h("span", { class: "search-hit-line" }, String(hit.line)),
        h(
          "span",
          { class: "search-hit-text" },
          t.slice(0, s),
          h("mark", {}, t.slice(s, e)),
          t.slice(e),
        ),
      );
    }

    /* строка результата: группа, путь (клик открывает файл) и число совпадений.
       Куда ведёт клик — свойство группы из меты: «editor» (глава во вкладке
       «Редактор» или обычный редактор) и «glossary» (нер.json — тот же текст,
       но смотреть его удобнее в таблице глоссария). Запрос в обоих случаях едет
       предзаполненным — искать заново не придётся */
    function fileNode(f) {
      const rel = String(f.path || "");
      const q = String(input.value || "").trim();
      const glossary = groupOpen[String(f.group || "")] === "glossary";
      const isChapter = /^chapters\/[^/]+\/[^/]+$/.test(rel);
      return h(
        "div",
        { class: "search-file" },
        h(
          "div",
          { class: "search-file-head" },
          h("span", { class: "search-file-group" }, f.group),
          h("button", {
            class: "btn btn-sm btn-ghost search-file-path",
            title: glossary
              ? "Открыть «Глоссарий»: все поля, все столбцы"
              : (isChapter
                ? "Открыть главу в редакторе с этим запросом"
                : "Открыть файл в редакторе с этим запросом"),
            "aria-label": glossary
              ? `Искать ${q} в глоссарии`
              : `Открыть ${rel} в редакторе`,
            onclick: () => (glossary
              ? setView("ner", q, true)
              : openEditor(rel, q)),
          }, rel),
          h("span", { class: "search-file-count" }, String(f.count)),
        ),
        h("div", { class: "search-file-hits" }, (f.hits || []).map(hitNode)),
      );
    }

    /* все результаты остаются результатами: список режется страницами,
       счётчик — в тулбаре */
    const pager = UIC.listPager({
      list: rows,
      pageSize: 25,
      label: "файлов с совпадениями",
      hideSinglePage: true,
      rows: (slice) =>
        slice.length
          ? slice.map(fileNode)
          : [h("div", { class: "files-empty" }, "Совпадений нет")],
    });

    function readCtx() {
      const v = parseInt(String(ctxIn.value).replace(",", "."), 10);
      const safe = Number.isFinite(v) ? Math.max(0, Math.min(CTX_MAX, v)) : CTX_DEFAULT;
      if (String(safe) !== ctxIn.value) ctxIn.value = String(safe);
      return safe;
    }

    async function run() {
      const q = String(input.value || "").trim();
      const ctx = readCtx();
      savePrefs({ q, context: String(ctx), ci: caseBox.checked });
      if (q.length < 2) {
        status.textContent = "Минимум 2 символа";
        return;
      }
      status.textContent = "Поиск…";
      const p = new URLSearchParams({ project: `${section}/${name}`, q });
      p.set("scope", currentScopes().join(","));
      p.set("context", String(ctx));
      if (caseBox.checked) p.set("case", "1");
      try {
        const r = await api(`/search?${p}`);
        const files = r.files || [];
        status.textContent =
          `Совпадений: ${r.total} · файлов: ${files.length}`
          + ` · прочитано: ${r.scanned}`
          + (r.skipped ? ` · не прочиталось: ${r.skipped}` : "");
        pager.items = files;
      } catch (ex) {
        status.textContent = ex.message;
        pager.items = [];
      }
    }

    runBtn.addEventListener("click", run);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") run();
    });
    ctxIn.addEventListener("change", () =>
      savePrefs({ context: String(readCtx()) }));
    caseBox.addEventListener("change", () => savePrefs({ ci: caseBox.checked }));

    // реестр групп — один GET без запроса
    try {
      const r = await api(
        `/search?${new URLSearchParams({ project: `${section}/${name}` })}`,
      );
      groups = r.groups || [];
      clusters = r.clusters || [];
      defaults = (r.scopes || []).filter((s) => groups.some((g) => g[0] === s));
      for (const g of groups) groupOpen[g[0]] = g[3] || "editor";
    } catch (ex) {
      err.textContent = ex.message;
    }
    renderChips();

    const toolbar = h(
      "div",
      { class: "files-toolbar" },
      input,
      runBtn,
      h("label", { class: "search-chip search-case-chip" }, caseBox, "регистр"),
      h("span", { class: "field-label search-ctx-label" }, "Контекст:"),
      ctxIn,
      h("span", { class: "spacer" }),
      status,
    );
    return h(
      "div",
      { class: "files-wrap" },
      toolbar,
      h("div", { class: "search-scope-row" }, chips),
      err,
      pager.el,
      rows,
    );
  }

  /* ── История: контрольные точки проекта (локальный git) ─────────
     Точки создаются вручную («＋ Контрольная точка»), перед применением
     правок проверок и после успешных запусков (переключатели в
     «Настройках» приложения). Восстановление к точке НЕ стирает
     последующие: текущее состояние фиксируется точкой «Возврат к …»,
     а файлы приводятся к выбранной точке. Дифф — таблицей файлов
     (статус A/M/D и +/- строк), по клику — разворачивается текст
     изменений. Источник данных — /api/history. */
  async function historyView() {
    const wrap = h("div", { class: "files-wrap history-wrap" });
    const err = h("div", { class: "form-error" });
    const list = h("div", { class: "history-list" });
    const KIND_LABELS = {
      manual: "вручную",
      run: "запуск",
      apply: "применение правок",
      restore: "возврат",
    };
    let items = []; // [{sha,time,label,kind}]

    function kindBadge(kind) {
      return h(
        "span",
        { class: `history-kind history-kind-${kind}` },
        KIND_LABELS[kind] || kind,
      );
    }

    function whenText(c) {
      const rel = UICore.relTime(c.time);
      const abs = UICore.relTimeAbs(c.time);
      return rel ? `${rel} · ${abs}` : abs;
    }

    /* модалка сравнения двух точек: слева таблица файлов, клик — дифф */
    async function diffModal(cFrom, cTo) {
      let data;
      try {
        data = await api(
          `/history/diff?${new URLSearchParams({
            project: `${section}/${name}`,
            from: cFrom.sha,
            to: cTo.sha,
          })}`,
        );
      } catch (ex) {
        err.textContent = ex.message;
        return;
      }
      const STATUSES = { A: ["добавлен", "history-add"],
                         M: ["изменён", "history-mod"],
                         D: ["удалён", "history-del"] };
      /* null в adds/dels — «много»: сервер не считает строки у больших
         файлов и удалений (порог LINE_DELTA_MAX_CHARS) */
      const countsText = (f) => {
        if (f.status === "D") return "";
        if (f.adds == null && f.dels == null) return "много";
        return `+${f.adds ?? 0}${f.dels ? ` −${f.dels}` : ""}`;
      };
      const rowsEl = h("div", { class: "history-diff-files" });
      for (const f of data.files || []) {
        const [stLabel, stClass] = STATUSES[f.status] || [f.status, ""];
        const summary = h(
          "summary",
          {},
          h("code", { class: "history-diff-path" }, f.path),
          h("span", { class: `history-badge ${stClass}` }, stLabel),
          h("span", { class: "history-lines" }, countsText(f)),
        );
        const body = h(
          "div",
          { class: "history-diff-body" },
          h("div", { class: "field-help" }, "Загрузка…"),
        );
        summary.addEventListener("click", async () => {
          if (body.dataset.loaded) return;
          try {
            const r = await api(
              `/history/patch?${new URLSearchParams({
                project: `${section}/${name}`,
                from: cFrom.sha,
                to: cTo.sha,
                path: f.path,
              })}`,
            );
            /* дифф — раскрашенный блок строк (спаны с классом строки):
               рендер мгновенный, цвет строки несёт +/− */
            const text = String(r.patch || "");
            const line = (cls, s) =>
              h("span", { class: cls },
                h("span", { class: "history-diff-marker" }, s.charAt(0)),
                s.slice(1) || " ");
            const lines = text.split("\n").map((ln) => {
              if (ln.startsWith("+") && !ln.startsWith("+++"))
                return line("history-line-ins", ln);
              if (ln.startsWith("-") && !ln.startsWith("---"))
                return line("history-line-del", ln);
              if (ln.startsWith("@@"))
                return h("span", { class: "history-line-hunk" }, ln);
              return h("span", { class: "history-line-ctx" }, ln || " ");
            });
            body.replaceChildren(
              h("pre", { class: "history-patch" }, ...lines));
          } catch (ex) {
            body.replaceChildren(h("div", { class: "form-error" }, ex.message));
          }
          body.dataset.loaded = "1";
        });
        rowsEl.append(h("details", { class: "history-diff-file" }, summary,
          body));
      }
      if (!(data.files || []).length) {
        rowsEl.append(h("div", { class: "card-hint" },
          "Между этими точками изменений файлов нет"));
      } else if ((data.total_files || 0) > data.files.length) {
        rowsEl.append(h("div", { class: "card-hint" },
          `Показаны первые ${data.files.length} из ${data.total_files} ` +
          "изменённых файлов"));
      }
      UIC.modal({
        title: "Изменения между точками",
        wide: true,
        build: (close) => [
          h("div", { class: "modal-text history-diff-head" },
            h("div", {}, `С: ${cFrom.label} · ${whenText(cFrom)}`),
            h("div", {}, `По: ${cTo.label} · ${whenText(cTo)}`)),
          rowsEl,
          h("div", { class: "modal-actions" },
            h("button", { class: "btn btn-ghost", onclick: () => close() },
              "Закрыть")),
        ],
      });
    }

    function restoreModal(c) {
      UIC.modal({
        title: "Возврат к контрольной точке",
        build: (close) => {
          const errM = h("div", { class: "form-error" });
          return [
            h("div", { class: "modal-text" },
              `Файлы проекта будут приведены к состоянию точки ` +
              `«${c.label}» (${whenText(c)}).`,
              h("br"),
              `Текущее состояние не пропадёт: перед возвратом оно ` +
              `фиксируется точкой «Возврат к…» — последующие точки ` +
              `останутся в истории.`),
            errM,
            h("div", { class: "modal-actions" },
              h("button", { class: "btn btn-ghost",
                onclick: () => close() }, "Отмена"),
              h("button", { class: "btn btn-primary", onclick: async () => {
                try {
                  await api("/history/restore", { method: "POST",
                    body: { project: `${section}/${name}`, sha: c.sha } });
                  toast("Возврат выполнен — создана точка «Возврат к…»");
                  close();
                  await load();
                } catch (ex) {
                  errM.textContent = ex.message;
                }
              } }, "Вернуться")),
          ];
        },
      });
    }

    /* выделение точек для сравнения (максимум две): чекбокс в строке —
       как на «Файлах»; кнопка «Сравнить» в панели выделения сравнивает
       выбранные между собой, а не только с предыдущей */
    const picked = new Set(); // sha выбранных точек
    const selBar = h("div", { class: "files-sel hidden" });

    function paintSel() {
      selBar.classList.toggle("hidden", picked.size === 0);
      if (!picked.size) return;
      const pair = [...picked];
      selBar.replaceChildren(
        h("span", { class: "files-sel-count" }, `выделено: ${picked.size}`),
        /* сравнивать можно только пару: кнопка появляется на двух точках */
        pair.length === 2
          ? iconBtn("compare", "Сравнить выбранные точки", () => {
            /* picked хранится от свежей к старой — diff идёт от старой */
            const bySha = (s) => items.find((c) => c.sha === s);
            diffModal(bySha(pair[1]), bySha(pair[0]));
          })
          : null,
        iconBtn("close", "Снять выделение", () => {
          picked.clear();
          load();
        }),
      );
    }

    function row(c, idx) {
      /* строка точки: маркер-линия, метка+вид, время, действия */
      const cmp = iconBtn("compare", "Сравнить с предыдущей точкой",
        async () => {
          /* вертушка на время сбора диффа: большая книга считается
             заметно дольше малого проекта — окно не должно выглядеть
             как зависшее */
          if (cmp.classList.contains("busy")) return;
          cmp.classList.add("busy");
          try {
            await diffModal(items[idx + 1] || c, c);
          } finally {
            cmp.classList.remove("busy");
          }
        });
      const restoreBtn = iconBtn("refresh", "Вернуть файлы проекта к этой точке",
        () => restoreModal(c));
      const cb = h("input", {
        type: "checkbox",
        class: "fsel",
        checked: picked.has(c.sha),
        "aria-label": `Выбрать точку «${c.label}» для сравнения`,
      });
      attachTooltip(cb, `Выбрать точку «${c.label}» для сравнения`);
      cb.addEventListener("change", () => {
        if (cb.checked) {
          if (picked.size >= 2) {
            /* третья точка не выбирается: сравнивать можно пару — гасим
               самую старую из выбранных (как в Confluence: лимит выбора) */
            const old = [...picked].pop();
            picked.delete(old);
          }
          picked.add(c.sha);
        } else {
          picked.delete(c.sha);
        }
        paintSel();
        load();
      });
      return h(
        "div",
        { class: "history-row" + (picked.has(c.sha) ? " frow-sel" : "") },
        cb,
        h("div", { class: "history-marker" }),
        h(
          "div",
          { class: "history-main" },
          h("div", { class: "history-title" },
            h("span", { class: "history-label" }, c.label),
            kindBadge(c.kind)),
          h("div", { class: "history-time" }, whenText(c)),
        ),
        h("div", { class: "history-actions" }, cmp, restoreBtn),
      );
    }

    async function load() {
      try {
        const r = await api(
          `/history?${new URLSearchParams({ project: `${section}/${name}` })}`,
        );
        items = r.checkpoints || [];
      } catch (ex) {
        list.replaceChildren(h("div", { class: "card-hint" }, ex.message));
        return;
      }
      if (!items.length) {
        list.replaceChildren(
          h("div", { class: "card-hint" },
            "Точек пока нет. Создайте первую кнопкой выше — снимок состояния файлов «ner.json» и глав; «tmp/» и «logs/» в точки не попадают. Автоматические точки (перед применением правок и после запусков) включаются в «Настройках» приложения."));
        return;
      }
      /* items — от свежей к старой; рисуем сверху вниз (свежая сверху) */
      list.replaceChildren(...items.map((c, i) => row(c, i)));
      paintSel();
    }

    const newBtn = h(
      "button",
      { class: "btn btn-sm btn-primary" },
      "＋ Контрольная точка",
    );
    newBtn.addEventListener("click", () => {
      UIC.modal({
        title: "Новая контрольная точка",
        build: (close) => {
          const input = h("input", {
            class: "input",
            placeholder: "Например: «Глоссарий после проверки»",
          });
          const errM = h("div", { class: "form-error" });
          const save = async () => {
            const label = input.value.trim();
            if (!label) {
              errM.textContent = "Введите метку точки";
              return;
            }
            try {
              const r = await api("/history", { method: "POST",
                body: { project: `${section}/${name}`, label } });
              if (!r.created) {
                toast("Изменений с прошлой точки нет — точка не создана");
              } else {
                toast("Контрольная точка создана");
              }
              close();
              await load();
            } catch (ex) {
              errM.textContent = ex.message;
            }
          };
          input.addEventListener("keydown", (e) => {
            if (e.key === "Enter") save();
          });
          return [
            h("div", { class: "modal-text" },
              "Метка — что в этом состоянии проекта. Дата и время добавляются сами."),
            input,
            errM,
            h("div", { class: "modal-actions" },
              h("button", { class: "btn btn-ghost", onclick: () => close() },
                "Отмена"),
              h("button", { class: "btn btn-primary", onclick: save },
                "Создать")),
          ];
        },
      });
    });

    /* тулбар: заголовок слева, кнопка справа; пояснение — одна строка help
       под шапкой (не «простыня» над списком) */
    const toolbar = h(
      "div",
      { class: "files-toolbar history-toolbar" },
      h("span", { class: "history-heading" }, "Контрольные точки"),
      h("span", { class: "spacer" }),
      selBar,
      newBtn,
    );
    wrap.append(toolbar, err, list);
    await load();
    return wrap;
  }

  // «Статус» — таблица готовности глав + сводка ner/wiki/compiled
  async function statusView() {
    let data;
    try {
      data = await api(`/projects/${section}/${name}/status`);
    } catch (ex) {
      return h("div", { class: "files-empty" }, ex.message);
    }
    const s = data.status || {};
    const chapters = s.chapters || {};
    const counts = s.counts || {};
    const ids = Object.keys(chapters)
      .map(Number)
      .sort((a, b) => a - b);
    const rows = ids.map((id) => {
      const c = chapters[id];
      const cells = ["translate", "redact", "polish"].map((k) => {
        const ok = !!c[k];
        return h(
          "td",
          { class: ok ? "ch-cell ch-ok" : "ch-cell ch-pending" },
          ok ? "✓" : "·",
        );
      });
      return h(
        "tr",
        { class: "ch-row" },
        h("th", { class: "ch-num" }, String(id)),
        ...cells,
      );
    });
    const table = h(
      "table",
      { class: "ch-table" },
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", {}, "Глава"),
          h("th", {}, "пер"),
          h("th", {}, "ред"),
          h("th", {}, "пол"),
        ),
      ),
      h(
        "tbody",
        {},
        rows.length
          ? rows
          : [h("tr", {}, h("td", { colspan: 4 }, "Глав пока нет"))],
      ),
    );
    const ner = s.ner || {};
    const wiki = s.wiki || {};
    const sumCard = h(
      "div",
      { class: "dash-summary" },
      h(
        "div",
        { class: "stat-card" },
        h("div", { class: "stat-num" }, String(counts.chapters ?? 0)),
        h("div", { class: "stat-label" }, "глав"),
      ),
      h(
        "div",
        { class: "stat-card" },
        h(
          "div",
          { class: "stat-num" },
          `${counts.translate ?? 0}/${counts.redact ?? 0}/${counts.polish ?? 0}`,
        ),
        h("div", { class: "stat-label" }, "пер/ред/пол"),
      ),
      h(
        "div",
        { class: "stat-card" },
        h(
          "div",
          { class: "stat-num" },
          ner.exists ? String(ner.terms ?? 0) : "—",
        ),
        h("div", { class: "stat-label" }, "терминов в глоссарии"),
      ),
      h(
        "div",
        { class: "stat-card" },
        h(
          "div",
          { class: "stat-num" },
          wiki.exists ? String(wiki.articles ?? 0) : "—",
        ),
        h("div", { class: "stat-label" }, "статей wiki"),
      ),
    );
    return h("div", { class: "files-wrap" }, sumCard, table);
  }

  async function configView() {
    const wrap = h("div", { class: "config-wrap" });
    const err = h("div", { class: "form-error" });
    const q = new URLSearchParams({ project: `${section}/${name}` });

    /* — Настройки книги в этом интерфейсе не редактируются: конфиг один
         (страница «Настройки»), а его значения стадии читают сами. Файл
         .env книги остаётся обычным файлом во «Файлах». — */

    /* — Обложка — */
    const coverCard = h("div", { class: "review-card" });
    const coverInfo = h("div", { class: "review-status" });
    const coverSel = h("select", { class: "input" });
    const coverImg = h("img", { class: "cover-preview", alt: "обложка" });
    const coverFile = h("input", {
      type: "file",
      accept: ".jpg,.jpeg,.png,.webp,.gif,.bmp",
    });
    const coverUpload = h("button", { class: "btn btn-sm" }, "Загрузить");
    const coverDelete = h(
      "button",
      { class: "btn btn-sm btn-danger-ghost" },
      "Удалить",
    );
    const IMG_EXT = [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"];
    coverCard.append(
      h("div", { class: "review-card-title" }, "Обложка"),
      h(
        "div",
        { class: "review-card-body" },
        h(
          "label",
          { class: "field" },
          h("div", { class: "field-label" }, "Файл из source/"),
          coverSel,
        ),
        coverInfo,
        coverImg,
        h(
          "div",
          { class: "review-actions" },
          coverFile,
          coverUpload,
          coverDelete,
        ),
      ),
    );
    function showCoverImg(path) {
      coverImg.src = `/api/download?${new URLSearchParams({
        project: `${section}/${name}`,
        path,
        inline: "1",
      })}`;
      coverImg.style.display = "";
    }
    async function loadCover() {
      try {
        // варианты обложек в source/ — из опций стадии compile
        const o = await api(`/stages/compile/options?${q}`);
        const src = o.options && o.options.source ? o.options.source : [];
        const imgs = src.filter((n) =>
          IMG_EXT.some((e) => n.toLowerCase().endsWith(e)));
        coverSel.replaceChildren();
        coverSel.append(
          h("option", { value: "" }, "— выберите обложку —"),
        );
        for (const n of imgs) coverSel.append(h("option", { value: n }, n));
        const d = await api(`/cover?${q}`);
        if (d.exists) {
          coverInfo.textContent = `${d.name} · ${fmtSize(d.size)}`;
          if (imgs.includes(d.name)) coverSel.value = d.name;
          showCoverImg(d.path);
        } else {
          coverInfo.textContent = "обложки нет";
          coverImg.style.display = "none";
          coverSel.value = "";
        }
      } catch (ex) {
        coverInfo.textContent = ex.message;
      }
    }
    coverSel.addEventListener("change", () => {
      const v = coverSel.value;
      if (v) {
        coverInfo.textContent = v;
        showCoverImg(`source/${v}`);
      } else {
        coverImg.style.display = "none";
        coverInfo.textContent = "выберите файл из source/";
      }
    });
    coverUpload.addEventListener("click", async () => {
      const f = coverFile.files && coverFile.files[0];
      if (!f) {
        toast("Сначала выберите файл", "err");
        return;
      }
      try {
        // имя сохраняется: cover1.png, cover2.png… попадают в source/
        // как есть (PUT /cover пишет всегда source/cover.*)
        let existing = [];
        try {
          const d = await api(
            `/files?project=${encodeURIComponent(`${section}/${name}`)}` +
            `&path=source`,
          );
          existing = (d.entries || []).map((e) => e.name);
        } catch {
          /* source/ ещё нет — всё новое */
        }
        if (existing.includes(f.name)) {
          const ok = await confirmModal(
            "Загрузка с перезаписью",
            `В source/ уже есть ${f.name}. Заменить этим файлом?`,
            "ПЕРЕЗАПИСАТЬ",
            async () => {},
          );
          if (!ok) return;
        }
        const form = new FormData();
        form.append("dest", "source");
        form.append("files[]", f, f.name);
        await apiUpload(`/upload?${q}`, form);
        toast(`Загружено: source/${f.name}`);
        await loadCover();
        if ([...coverSel.options].some((o) => o.value === f.name)) {
          coverSel.value = f.name;
          coverSel.dispatchEvent(new Event("change"));
        }
      } catch (ex) {
        toast(ex.message, "err");
      }
    });
    coverDelete.addEventListener("click", () => {
      const sel = coverSel.value;
      confirmModal(
        "Удалить обложку",
        sel
          ? `Файл source/${sel} будет удалён`
          : "Каноническая обложка (cover.*) будет удалена из source/",
        "УДАЛИТЬ",
        async () => {
          try {
            if (sel) {
              const dq = new URLSearchParams({
                project: `${section}/${name}`,
                path: `source/${sel}`,
              });
              await api(`/file?${dq}`, { method: "DELETE" });
            } else {
              await api(`/cover?${q}`, { method: "DELETE" });
            }
            toast("Обложка удалена");
            await loadCover();
          } catch (ex) {
            toast(ex.message, "err");
          }
        },
      );
    });
    await loadCover();

    /* — source-файлы с выбором файла из source/ (редактор, сохранение,
         загрузка из шаблона). defFile — файл по умолчанию (metadata.yaml,
         donate.txt); ext — расширение для фильтра списка. — */
    function sourceFilesCard(title, ext, defFile) {
      const card = h("div", { class: "review-card" });
      const ed = makeEditor("", ext === ".yaml" ? "yaml" : "txt");
      const save = h("button", { class: "btn btn-sm" }, "Сохранить");
      const tpl = h("button", { class: "btn btn-sm btn-ghost" }, "Из шаблона");
      const sel = h("select", { class: "input" });
      const status = h("div", { class: "review-status" });
      card.append(
        h("div", { class: "review-card-title" }, title),
        h(
          "div",
          { class: "review-card-body" },
          h(
            "label",
            { class: "field" },
            h("div", { class: "field-label" }, "Файл из source/"),
            sel,
          ),
          status,
          h("div", { class: "editor-cm editor-cm-small" }, ed.root),
          h("div", { class: "review-actions" }, UIC.editorSearch(ed), tpl, save),
        ),
      );
      let rel = `source/${defFile}`;
      async function loadFile() {
        status.textContent = "Загрузка…";
        try {
          const d = await api(`/file?${q}&path=${encodeURIComponent(rel)}`);
          ed.setValue(d.content || "");
          status.textContent = d.missing
            ? "файла нет — создастся при сохранении"
            : rel;
        } catch (ex) {
          status.textContent = ex.message;
        }
      }
      async function load() {
        try {
          const o = await api(`/stages/compile/options?${q}`);
          const src = o.options && o.options.source ? o.options.source : [];
          const items = src.filter((n) =>
            n.toLowerCase().endsWith(ext));
          sel.replaceChildren();
          if (items.length) {
            sel.append(h("option", { value: "" }, "— выберите файл —"));
            for (const n of items) sel.append(h("option", { value: n }, n));
            sel.value = items.includes(defFile) ? defFile : items[0];
          } else {
            sel.append(h("option", { value: "" }, "— нет файлов —"));
          }
          rel = sel.value ? `source/${sel.value}` : `source/${defFile}`;
          await loadFile();
        } catch (ex) {
          status.textContent = ex.message;
        }
      }
      sel.addEventListener("change", async () => {
        rel = sel.value ? `source/${sel.value}` : `source/${defFile}`;
        await loadFile();
      });
      save.addEventListener("click", async () => {
        try {
          await api("/file", {
            method: "PUT",
            body: {
              project: `${section}/${name}`,
              path: rel,
              content: ed.getValue(),
            },
          });
          toast(`${rel} сохранён`);
          await loadFile();
        } catch (ex) {
          err.textContent = ex.message;
        }
      });
      tpl.addEventListener("click", () =>
        templateFileModal(rel, (content) => {
          ed.setValue(content);
          toast(`${rel} — загружен из шаблона, сохраните`);
        }),
      );
      load();
      return card;
    }

    /* — source-файлы с выбором из имеющихся в source/ — */

    wrap.append(
      coverCard,
      sourceFilesCard("Метаданные epub/fb2", ".yaml", "metadata.yaml"),
      sourceFilesCard("Файл страницы поддержки", ".txt", "donate.txt"),
    );
    return wrap;
  }

  /* Модалка «файл из шаблона»: наборы templates, где есть нужный
     файл → колбэк (содержимое). Список наборов — GET /api/templates
     (дерево файлов), чтение — GET /api/templates/{set}/file?path=. */
  function templateFileModal(rel, onLoad) {
    const err = h("div", { class: "form-error" });
    const sel = h("select", { class: "input" });
    UIC.modal({
      title: `Загрузить ${rel} из шаблона`,
      build: (close) => [
        sel,
        err,
        h(
          "div",
          { class: "modal-actions" },
          h("button", { class: "btn btn-ghost", onclick: close }, "Отмена"),
          h(
            "button",
            {
              class: "btn btn-primary",
              onclick: async () => {
                const set = sel.value;
                if (!set) {
                  err.textContent = "Выберите набор шаблонов";
                  return;
                }
                try {
                  const d = await api(
                    `/templates/${encodeURIComponent(set)}/file` +
                      `?path=${encodeURIComponent(rel)}`,
                  );
                  close();
                  onLoad(d.content || "");
                } catch (ex) {
                  err.textContent = ex.message;
                }
              },
            },
            "Загрузить",
          ),
        ),
      ],
    });
    api("/templates")
      .then((d) => {
        const withFile = (d.templates || []).filter((t) =>
          (t.files || []).includes(rel),
        );
        if (withFile.length) {
          for (const t of withFile) {
            sel.append(h("option", { value: t.name }, t.name));
          }
        } else {
          sel.append(
            h("option", { value: "" }, "Нет наборов с этим файлом"),
          );
        }
      })
      .catch((ex) => {
        err.textContent = ex.message;
      });
  }

  /* ── Промпты ───────────────────────────────────── */
  async function promptsView() {
    const q = new URLSearchParams({ project: `${section}/${name}` });
    let data;
    try {
      data = await api(`/prompts?${q}`);
    } catch (ex) {
      return h("div", { class: "files-empty" }, ex.message);
    }
    const list = h("div", { class: "prompt-list" });
    const err = h("div", { class: "form-error" });
    /* все файлы вкладки — промпты: язык один на вкладке (выбор с «Внешнего вида») */
    const ed = makeEditor("", UICore.EDITOR_SETTINGS.langPrompt);
    const nameLabel = h("div", { class: "prompt-name" });
    /* открытый промпт — состояние вкладки */
    let current = pref.get("prompts").file || null;

    function renderList() {
      list.replaceChildren();
      delBtn.disabled = !current;
      for (const p of data.prompts || []) {
        const btn = h(
          "button",
          {
            class:
              "btn btn-sm btn-ghost prompt-item" +
              (current === p.name ? " prompt-item-active" : ""),
          },
          `${p.name} · ${fmtSize(p.size)}`,
        );
        btn.addEventListener("click", () => load(p.name));
        list.append(btn);
      }
      if (!data.prompts?.length) {
        list.append(
          h(
            "div",
            { class: "empty" },
            "Нет промптов — создайте кнопками «Создать» или «Из шаблона»",
          ),
        );
      }
    }
    async function load(fname) {
      err.textContent = "";
      try {
        const d = await api(`/prompts/${encodeURIComponent(fname)}?${q}`);
        current = fname;
        pref.set("prompts", { file: fname });
        nameLabel.textContent = fname;
        ed.setValue(d.content || "");
        renderList();
      } catch (ex) {
        err.textContent = ex.message;
      }
    }
    const saveBtn = h("button", { class: "btn btn-sm" }, "Сохранить");
    saveBtn.addEventListener("click", async () => {
      if (!current) return;
      try {
        await api(`/prompts/${encodeURIComponent(current)}`, {
          method: "PUT",
          body: { project: `${section}/${name}`, content: ed.getValue() },
        });
        toast("Промпт сохранён");
        render();
      } catch (ex) {
        err.textContent = ex.message;
      }
    });
    const delBtn = h(
      "button",
      { class: "btn btn-sm btn-danger-ghost", disabled: true },
      "Удалить",
    );
    delBtn.addEventListener("click", () => {
      if (!current) return;
      confirmModal(
        "Удаление промпта",
        `${current} — файл будет удалён`,
        "УДАЛИТЬ",
        async () => {
          try {
            await api(`/prompts/${encodeURIComponent(current)}?${q}`, {
              method: "DELETE",
            });
            toast(`Удалён: ${current}`);
            current = null;
            pref.set("prompts", { file: "" });
            nameLabel.textContent = "";
            ed.setValue("");
            renderList();
          } catch (ex) {
            err.textContent = ex.message;
          }
        },
      );
    });
    const createBtn = h("button", { class: "btn btn-sm btn-ghost" }, "Создать");
    createBtn.addEventListener("click", () => {
      const nameInput = h("input", {
        class: "input",
        placeholder: "имя_промпта.txt",
      });
      const cerr = h("div", { class: "form-error" });
      UIC.modal({
        title: "Новый промпт",
        build: (close) => [
          nameInput,
          cerr,
          h(
            "div",
            { class: "modal-actions" },
            h(
              "button",
              { class: "btn btn-ghost", onclick: () => close() },
              "Отмена",
            ),
            h(
              "button",
              {
                class: "btn btn-primary",
                onclick: async () => {
                  const fname = nameInput.value.trim();
                  if (!fname) {
                    cerr.textContent = "Укажите имя файла";
                    return;
                  }
                  try {
                    await api(`/prompts/${encodeURIComponent(fname)}`, {
                      method: "PUT",
                      body: { project: `${section}/${name}`, content: "" },
                    });
                    close();
                    toast(`Создан: ${fname}`);
                    render();
                  } catch (ex) {
                    cerr.textContent = ex.message;
                  }
                },
              },
              "Создать",
            ),
          ),
        ],
      });
      nameInput.focus();
    });
    const tplBtn = h("button", { class: "btn btn-sm btn-ghost" }, "Из шаблона");
    tplBtn.addEventListener("click", () =>
      templateModal(data.templates || [], async (tpl, outName) => {
        try {
          await api(`/prompts/${encodeURIComponent(outName)}`, {
            method: "PUT",
            body: { project: `${section}/${name}`, content: tpl.content },
          });
          toast(`Создан ${outName} (из «${tpl.set}»)`);
          render();
        } catch (ex) {
          toast(ex.message, "err");
        }
      }),
    );
    const toolbar = h(
      "div",
      { class: "files-toolbar" },
      nameLabel,
      h("span", { class: "spacer" }),
      createBtn,
      tplBtn,
      UIC.editorSearch(ed),
      saveBtn,
    );
    renderList();
    /* файл мог быть удалён в другом месте — возвращаем только живой */
    if (current && (data.prompts || []).some((p) => p.name === current)) {
      nameLabel.textContent = current;
      load(current);
    }
    const editorHost = h("div", { class: "editor-cm" }, ed.root);
    return h("div", { class: "files-wrap" }, toolbar, err, list, editorHost);
  }

  /* Модалка выбора шаблона: наборы templates + имя файла →
     колбэк (шаблон, имя). Работает и на пустом prompts/. */
  function templateModal(templates, onApply) {
    const q = new URLSearchParams({ project: `${section}/${name}` });
    const err = h("div", { class: "form-error" });
    const sel = h("select", { class: "input" });
    for (const t of templates) {
      sel.append(h("option", { value: t.name }, `${t.name} · набор ${t.set}`));
    }
    const fname = h("input", { class: "input", placeholder: "имя файла" });
    function syncName() {
      if (!fname.dataset.touched) fname.value = sel.value || "";
    }
    sel.addEventListener("change", syncName);
    fname.addEventListener("input", () => (fname.dataset.touched = "1"));
    UIC.modal({
      title: "Создать промпт из шаблона",
      build: (close) => [
        templates.length
          ? h("div", { class: "form-row" }, sel, fname)
          : h("div", { class: "modal-text" }, "Шаблоны не найдены"),
        err,
        h(
          "div",
          { class: "modal-actions" },
          h("button", { class: "btn btn-ghost", onclick: close }, "Отмена"),
          h(
            "button",
            {
              class: "btn btn-primary",
              onclick: async () => {
                const target = templates.find((t) => t.name === sel.value);
                const outName = fname.value.trim();
                if (!target || !outName) {
                  err.textContent = "Выберите шаблон и укажите имя файла";
                  return;
                }
                try {
                  const d = await api(
                    `/prompts/${encodeURIComponent(target.name)}/template?${q}`,
                  );
                  const tpl =
                    (d.templates || []).find((t) => t.set === target.set) ||
                    (d.templates || [])[0];
                  close();
                  await onApply(tpl, outName);
                } catch (ex) {
                  err.textContent = ex.message;
                }
              },
            },
            "Создать",
          ),
        ),
      ],
    });
    syncName();
    sel.focus();
  }

  /* ── Логи ─────────────────────────────────── */
  async function logsView() {
    /* структура папок как «Проекты-Файлы» (crumbs + подпапки),
       отображаются только *.log */
    const q = new URLSearchParams({ project: `${section}/${name}` });
    let data;
    try {
      data = await api(`/logs?${q}`);
    } catch (ex) {
      return h("div", { class: "files-empty" }, ex.message);
    }
    const all = (data.logs || []).slice().sort((a, b) => b.mtime - a.mtime);
    const cur = st.logPath || "";
    const crumbs = h("div", { class: "crumbs" });
    crumbs.append(
      crumb("logs", () => {
        setLogPath("");
        render();
      }),
    );
    const parts = cur ? cur.split("/") : [];
    const walk = [];
    for (const p of parts) {
      walk.push(p);
      const target = walk.join("/");
      crumbs.append(h("span", { class: "crumb-sep" }, " / "));
      crumbs.append(
        crumb(p, () => {
          setLogPath(target);
          render();
        }),
      );
    }
    const list = h("div", { class: "prompt-list" });
    const pre = h("pre", { class: "log-view" });
    const meta = h("div", { class: "review-status" });
    const LOG_PAGE_SIZE = 20;
    let lSelected = st.logFile || ""; // выбранный лог (подсвечивается в списке)
    /* страница списка логов — общий пейджер */
    const lPager = UIC.listPager({
      pageSize: LOG_PAGE_SIZE,
      list,
      info: (_n, page, pages) => ` ${page} / ${pages} · логов: ${logFiles} `,
      rows: (slice) => {
        if (!slice.length) return [h("div", { class: "empty" }, "Логов нет")];
        const rows = [];
        for (const e of slice) {
          if (e.kind === "dir") {
            const btn = h("button", { class: "btn btn-sm btn-ghost prompt-item" });
            btn.append(iconEl("folder", "fname-icon"), `${e.name}/`);
            btn.addEventListener("click", () => {
              setLogPath(cur ? `${cur}/${e.name}` : e.name);
              render();
            });
            rows.push(btn);
            continue;
          }
          const btn = h("button", {
            class: "btn btn-sm btn-ghost prompt-item"
              + (lSelected === e.name ? " prompt-item-active" : ""),
          }, `${e.name} · ${fmtSize(e.size)}`);
          btn.addEventListener("click", () => loadLog(e.name, false, cur));
          const del = iconBtn("trash", "Удалить лог", (ev) => {
            ev.stopPropagation();
            const full = cur ? `${cur}/${e.name}` : e.name;
            confirmModal("Удалить лог", `Файл ${full} будет удалён`, "УДАЛИТЬ", async () => {
              try {
                const sub = cur ? `&dir=${encodeURIComponent(cur)}` : "";
                await api(`/logs/${encodeURIComponent(e.name)}?${q}${sub}`, {
                  method: "DELETE",
                });
                toast(`Лог удалён: ${full}`);
                setLogPath(""); // папка могла стать пустой — на корень
                render();
              } catch (ex) {
                toast(ex.message, "err");
              }
            });
          });
          rows.push(h("div", { class: "prompt-item-row" }, btn, del));
        }
        return rows;
      },
    });
    let logFiles = 0; // файлов без папок — их показывает подпись

    const follow = h(
      "button",
      {
        class: "btn btn-sm btn-ghost",
        title: "Догружать хвост файла каждые 1,5 с (автопрокрутка вниз)",
      },
      "Автообновление",
    );
    let timer = null;

    function renderList() {
      const prefix = cur ? cur + "/" : "";
      const dirs = [
        ...new Set(
          all
            .map((l) => l.path)
            .filter((p2) => p2.startsWith(prefix))
            .map((p2) => p2.slice(prefix.length))
            .filter((p2) => p2.includes("/"))
            .map((p2) => p2.split("/")[0]),
        ),
      ].sort();
      // файлы ТОЛЬКО из текущей папки: путь начинается с prefix и
      // после него не содержит больше «/» (иначе корневые логи
      // «протекали» бы в подпапки — например, в chapters)
      const files = all.filter(
        (l) =>
          l.path.startsWith(prefix) &&
          !l.path.slice(prefix.length).includes("/"),
      );
      logFiles = files.length;
      lPager.items = [
        ...dirs.map((d) => ({ kind: "dir", name: d, mtime: 0, size: 0 })),
        ...files.map((f) => ({ kind: "file", ...f })),
      ];
    }
    async function loadLog(name, append, dir) {
      try {
        const sub = dir ? `&dir=${encodeURIComponent(dir)}` : "";
        const d = await api(
          `/logs/${encodeURIComponent(name)}?${q}${sub}&tail=${append ? 65536 : 0}`,
        );
        if (append) pre.textContent += d.content;
        else pre.textContent = d.content;
        pre.dataset.log = name;
        pre.dataset.dir = dir || "";
        meta.textContent = `${dir ? dir + "/" : ""}${name} · ${fmtSize(d.size)}`;
        lSelected = name;
        st.logFile = name;
        pref.set("logs", { file: name });
        renderList();
        pre.scrollTop = pre.scrollHeight;
      } catch (ex) {
        meta.textContent = ex.message;
      }
    }
    follow.addEventListener("click", () => {
      if (timer) {
        clearInterval(timer);
        timer = null;
        follow.textContent = "Автообновление";
        meta.textContent = meta.textContent.replace(
          " · автообновление вкл",
          "",
        );
        return;
      }
      follow.textContent = "Стоп";
      meta.textContent += " · автообновление вкл";
      timer = setInterval(async () => {
        const active = pre.dataset.log;
        const adir = pre.dataset.dir;
        if (active) await loadLog(active, true, adir);
      }, 1500);
    });
    renderList();
    const clearAll = h(
      "button",
      { class: "btn btn-sm btn-danger" },
      "Очистить все",
    );
    clearAll.disabled = !all.length;
    clearAll.addEventListener("click", () =>
      confirmModal(
        "Очистить все логи",
        `Будут удалены все *.log в logs/ (${all.length} шт.)`,
        "УДАЛИТЬ",
        async () => {
          try {
            await api(`/logs?${q}`, { method: "DELETE" });
            toast("Все логи удалены");
            setLogPath("");
            render();
          } catch (ex) {
            toast(ex.message, "err");
          }
        },
      ),
    );
    const toolbar = h(
      "div",
      { class: "files-toolbar" },
      crumbs,
      h("span", { class: "spacer" }),
      meta,
      follow,
      clearAll,
    );
    /* открытый лог — состояние вкладки, но возвращается он только из своей
       папки: сверяем полный путь по списку (пейджер к этому моменту ещё пуст) */
    function prefersFile(logs, dir, name) {
      const prefix = dir ? `${dir}/` : "";
      return logs.some((l) => l.path === `${prefix}${name}`);
    }
    if (lSelected && prefersFile(all, cur, lSelected)) loadLog(lSelected, false, cur);
    return h("div", { class: "files-wrap" }, toolbar, list, lPager.el, pre);
  }

  /* ── Отчёты translate_check ────────────── */
  async function notesView() {
    /* «Заметки» проекта — markdown-файл source/info.md (копируется из
       шаблона General при создании проекта); редактор как у «Заметок»
       приложения: CodeMirror + md-предпросмотр в sandbox-iframe */
    const NOTES_PATH = "source/info.md";
    const err = h("div", { class: "form-error" });
    const ed = makeEditor("", "md");
    const pane = UIC.previewPane(ed, { small: true });
    const status = h("div", { class: "review-status" });
    const saveBtn = h(
      "button",
      { class: "btn btn-sm btn-primary" },
      "Сохранить",
    );
    saveBtn.addEventListener("click", async () => {
      err.textContent = "";
      try {
        await api("/file", {
          method: "PUT",
          body: {
            project: `${section}/${name}`,
            path: NOTES_PATH,
            content: ed.getValue(),
          },
        });
        toast("Заметки сохранены");
        status.textContent = "source/info.md";
      } catch (ex) {
        err.textContent = ex.message;
      }
    });
    async function loadNotes() {
      const q = new URLSearchParams({
        project: `${section}/${name}`,
        path: NOTES_PATH,
      });
      try {
        const d = await api(`/file?${q}`);
        ed.setValue(d.content || "");
        status.textContent = d.missing
          ? "инфо-файла ещё нет — сохраните, чтобы создать source/info.md"
          : d.path;
      } catch (ex) {
        err.textContent = ex.message;
      }
    }
    await loadNotes();
    // по умолчанию — отрендеренный вид (правка — по кнопке «Редактор»)
    pane.setMode("md");
    return h(
      "div",
      { class: "page" },
      h(
        "div",
        { class: "page-header" },
        h(
          "div",
          { class: "page-header-main" },
          h("h1", { class: "page-title" }, "Заметки"),
          h(
            "div",
            { class: "page-sub" },
            "Информация о книге (markdown, source/info.md — копируется из шаблона)",
          ),
        ),
      ),
      h(
        "div",
        { class: "review-card" },
        h("div", { class: "review-card-title" }, "Редактор"),
        h(
          "div",
          { class: "review-card-body" },
          h(
            "div",
            { class: "files-toolbar" },
            status,
            h("span", { class: "spacer" }),
            h("span", { class: "field-help" }, "кегль"),
            previewFontSelect(() => {
              if (pane.mode !== "code") pane.render();
            }),
            UIC.editorSearch(ed),
            pane.btn,
            saveBtn,
          ),
          err,
          pane.host,
          pane.frame,
        ),
      ),
    );
  }

  /* ── Отчёты translate_check — секция 2 «Проверки» ── */
  async function renderCheckReports(section, name) {
    const q = new URLSearchParams({ project: `${section}/${name}` });
    let data;
    try {
      data = await api(`/check?${q}`);
    } catch (ex) {
      return h("div", { class: "files-empty" }, ex.message);
    }
    if (!data.reports?.length) {
      return h(
        "div",
        { class: "files-empty" },
        "Нет отчётов — запустите стадию translate_check (вкладка «Запуски», стадия 4)",
      );
    }
    /* открытый отчёт — состояние вкладки: книга помнит, что читали */
    const savedReport = pref.get("notes").name;
    let current = data.reports.find((r) => r.name === savedReport)
      || data.reports[0];
    const REPORT_PAGE_SIZE = 10;
    const list = h("div", { class: "prompt-list" });
    const body = h("div", { class: "review-card" });
    /* страницы — общий пейджер, режим управляемый: компонент держит
       страницу, список и таблицу рисует вьюха (onChange) */
    const listPager = UIC.listPager({
      pageSize: REPORT_PAGE_SIZE,
      info: (total, page, pages) => ` ${page} / ${pages} · отчётов: ${total} `,
      onChange: () => renderList(),
    });
    const pager = UIC.listPager({
      pageSize: PAGE_SIZE,
      hideOnEmpty: true,
      info: (total, page, pages) => ` ${page} / ${pages} · всего ${total} `,
      onChange: () => renderReport(),
    });

    function renderList() {
      list.replaceChildren();
      const rPage = listPager.page;
      const slice = data.reports.slice(
        rPage * REPORT_PAGE_SIZE,
        (rPage + 1) * REPORT_PAGE_SIZE,
      );
      for (const r of slice) {
        const btn = h(
          "button",
          {
            class:
              "btn btn-sm btn-ghost prompt-item" +
              (r === current ? " prompt-item-active" : ""),
          },
          `${r.name} · ошибок: ${r.failed ?? "?"}`,
        );
        btn.addEventListener("click", () => {
          current = r;
          pref.set("notes", { name: r.name });
          pager.page = 0; // Другой отчёт — с первой страницы
          renderList();
          renderReport();
        });
        list.append(btn);
      }
      listPager.items = data.reports.length;
    }
    function renderReport() {
      body.replaceChildren();
      const r = current;
      if (!r) {
        // после «Очистить» отчётов нет — пустое состояние вместо падения
        body.append(
          h("div", { class: "files-empty" },
            "Нет отчётов — запустите стадию translate_check "
            + "(вкладка «Запуски», стадия 4)"),
        );
        pager.items = 0;
        return;
      }
      body.append(
        h("div", { class: "review-card-title" }, r.name),
        h(
          "div",
          { class: "review-status" },
          `тип: ${r.type || "?"} · диапазон: ${r.range || "?"} · проверено: ${r.checked || "?"} · с ошибками: ${r.failed || "?"} · дата: ${r.date || "?"}`,
        ),
      );
      if (!r.entries?.length) {
        body.append(h("div", { class: "card-hint" }, "Ошибок нет ✓"));
        return;
      }
      const rows = [];
      /* фрагмент для поиска: последний «…» / '…' / текст после «:» */
      function searchFragment(msg) {
        const q = msg.match(/«([^»]+)»/);
        if (q) return q[1];
        const sq = msg.match(/'([^']+)'/);
        if (sq) return sq[1];
        const colon = msg.match(/:\s*([^:]*)$/);
        if (colon && colon[1].trim()) return colon[1].trim();
        return msg;
      }
      /* открыть файл главы (тип из отчёта) в редакторе с поиском ошибки */
      async function openErrorFile(dir, type, msg) {
        const rel = dir ? `${dir}/${type}.txt` : `${type}.txt`;
        const fq = new URLSearchParams({
          project: `${section}/${name}`,
          path: rel,
        });
        try {
          const d = await api(`/file?${fq}`);
          if (d.missing) throw new Error("missing");
          st.edit = rel;
          st.search = searchFragment(msg);
          render();
        } catch {
          setPath(dir || "");
          setView("files");
        }
      }
      for (const e of r.entries) {
        const dirCell = h(
          "td",
          {},
          e.dir
            ? h(
                "a",
                {
                  class: "link",
                  onclick: () => setPath(e.dir),
                  title: "Открыть папку главы в «Файлы»",
                },
                e.dir,
              )
            : "—",
        );
        const errCell = h(
          "td",
          {},
          ...e.errors.map((msg) =>
            h(
              "button",
              {
                class:
                  "check-msg-link " +
                  (e.fatal || msg.startsWith("[FATAL]")
                    ? "check-fatal"
                    : "check-msg"),
                onclick: () => openErrorFile(e.dir, r.type, msg),
                title: `Открыть ${e.dir}/${r.type}.txt и найти ошибку`,
              },
              msg,
            ),
          ),
        );
        rows.push(
          h(
            "tr",
            { class: "ner-row" },
            h("td", { class: "ch-num" }, String(e.chapter)),
            dirCell,
            errCell,
          ),
        );
      }
      body.append(
        h(
          "table",
          { class: "ner-table" },
          h(
            "thead",
            {},
            h(
              "tr",
              {},
              h("th", {}, "Глава"),
              h("th", {}, "Папка"),
              h("th", {}, "Ошибки"),
            ),
          ),
          h("tbody", {}, rows.slice(pager.page * PAGE_SIZE, (pager.page + 1) * PAGE_SIZE)),
        ),
      );
      body.append(pager.el);
      pager.items = rows.length;
    }
    // «Очистить» — удалить все отчёты check_*.txt из logs/ проекта
    const clearBtn = h(
      "button",
      {
        class: "btn btn-sm btn-ghost rv-clear",
        title: "Удалить все отчёты check_*.txt из logs/",
      },
      "Очистить",
    );
    clearBtn.disabled = !(data.reports || []).length;
    clearBtn.addEventListener("click", () => {
      const n = (data.reports || []).length;
      confirmModal(
        "Очистить отчёты",
        `Будут удалены все отчёты translate_check (${n} шт.) из logs/`,
        "УДАЛИТЬ",
        async () => {
          const q = new URLSearchParams({ project: `${section}/${name}` });
          try {
            for (const r of data.reports || []) {
              await api(`/file?${q}&path=${encodeURIComponent(`logs/${r.name}`)}`, {
                method: "DELETE",
              });
            }
            data.reports = [];
            current = null;
            pref.set("notes", { name: "" });
            pager.page = 0;
            listPager.page = 0;
            clearBtn.disabled = true;
            renderList();
            renderReport();
            toast("Все отчёты удалены");
          } catch (ex) {
            toast(ex.message, "err");
          }
        },
      );
    });
    renderList();
    renderReport();
    return h(
      "div",
      { class: "files-wrap" },
      h(
        "div",
        { class: "check-list" },
        h(
          "div",
          { class: "check-list-toolbar" },
          h("span", { class: "check-list-count" },
            `${data.reports.length} отчёт(ов)`),
          clearBtn,
        ),
        list,
        listPager.el,
      ),
      body,
    );
  }

  render();
  return page;
}

/* ── Оценка перевода (LLM): md-отчёт translate_quality ── */
async function renderQualityReports(section, name) {
  /* Секция «Проверки»: рендер фиксированного отчёта
     tmp/translation_quality_assessment.md в sandbox-iframe;
     выбор файла убран — имя отчёта фиксируется стадией. */
  const err = h("div", { class: "form-error" });
  const empty = h("div", { class: "files-empty" });
  function syncMsgs() {
    empty.hidden = !empty.textContent;
    err.hidden = !err.textContent;
  }
  syncMsgs();
  const frame = h("iframe", {
    class: "editor-preview-frame preview-adaptive",
    sandbox: "allow-same-origin",
    title: "предпросмотр отчёта оценки перевода",
  });
  frame.style.display = "none";

  const q = new URLSearchParams({
    project: `${section}/${name}`,
    path: "tmp/translation_quality_assessment.md",
  });
  const wrap = h(
    "div",
    { class: "quality-reports" },
    empty,
    err,
    frame,
  );
  try {
    const r = await api(`/file?${q}`);
    if (r.missing) {
      empty.textContent =
        "Нет отчёта — запустите стадию «Оценка перевода (LLM)» (вкладка «Запуски»)";
      syncMsgs();
      return wrap;
    }
    const html = window.marked
      ? window.marked.parse(r.content || "", {
          mangle: false,
          headerIds: false,
        })
      : "<pre>marked не загружен</pre>";
    frame.srcdoc = mdPreviewSrcdoc(html);
    frame.style.display = "block";
  } catch (ex) {
    err.textContent = ex.message;
    empty.textContent =
      "Нет отчёта — запустите стадию «Оценка перевода (LLM)» (вкладка «Запуски»)";
  }
  syncMsgs();
  frame.addEventListener("load", () => fitPreviewFrame(frame));

  return wrap;
}

function exportModal(byType, project) {
  const err = h("div", { class: "form-error" });
  const fmtJson = h("input", { type: "radio", name: "expfmt", value: "json" });
  const fmtText = h("input", { type: "radio", name: "expfmt", value: "text" });
  const fmtNames = h("input", {
    type: "radio",
    name: "expfmt",
    value: "names",
  });
  fmtJson.checked = true;
  const fmtPairs = [
    [fmtJson, "JSON — полные записи"],
    [fmtText, "JSONL — записи по одной на строку"],
    [fmtNames, "Текст — имена (женские/мужские)"],
  ];
  const cntInp = h("input", {
    type: "number",
    class: "input input-sm",
    min: "0",
    value: "0",
  });

  const typeKeys = Object.keys(byType);
  const typeCbs = typeKeys.map((t) => {
    const cb = h("input", { type: "checkbox" });
    cb.dataset.type = t;
    return h("label", { class: "ner-col-row" }, cb, ` ${t} (${byType[t]})`);
  });

  const femaleCbs = typeKeys.map((t) => {
    const cb = h("input", { type: "checkbox" });
    cb.checked = /\(female\)/i.test(t);
    cb.dataset.type = t;
    return h("label", { class: "ner-col-row" }, cb, ` ${t} (${byType[t]})`);
  });
  const maleCbs = typeKeys.map((t) => {
    const cb = h("input", { type: "checkbox" });
    cb.checked = /\(male\)/i.test(t);
    cb.dataset.type = t;
    return h("label", { class: "ner-col-row" }, cb, ` ${t} (${byType[t]})`);
  });
  const extra = h("div", { class: "exp-extra" });
  function renderExtra() {
    extra.replaceChildren();
    const fmt = fmtJson.checked ? "json" : fmtText.checked ? "text" : "names";
    if (fmt === "names") {
      extra.append(
        h("div", { class: "modal-text" }, "Женские типы:"),
        ...(femaleCbs.length
          ? femaleCbs
          : [h("div", { class: "card-hint" }, "нет типов")]),
        h("div", { class: "modal-text" }, "Мужские типы:"),
        ...(maleCbs.length
          ? maleCbs
          : [h("div", { class: "card-hint" }, "нет типов")]),
      );
    }
  }
  for (const [r] of fmtPairs) r.addEventListener("change", renderExtra);
  const goBtn = h("button", { class: "btn btn-primary" }, "Экспорт");
  goBtn.addEventListener("click", async () => {
    err.textContent = "";
    const fmt = fmtJson.checked ? "json" : fmtText.checked ? "text" : "names";
    const q = new URLSearchParams({ project, format: fmt });
    if (cntInp.value !== "") q.set("count_threshold", cntInp.value);
    const sel = typeCbs.filter((cb) => cb.checked).map((cb) => cb.dataset.type);
    if (sel.length) q.set("types", sel.join(","));
    if (fmt === "names") {
      const f = femaleCbs
        .filter((cb) => cb.checked)
        .map((cb) => cb.dataset.type);
      const m = maleCbs.filter((cb) => cb.checked).map((cb) => cb.dataset.type);
      if (f.length) q.set("female_types", f.join(","));
      if (m.length) q.set("male_types", m.join(","));
    }
    try {
      const r = await api(`/ner/export?${q}`);
      downloadText(r.name, r.content);
      toast(`Экспортировано записей: ${r.total}`);
      close();
    } catch (ex) {
      err.textContent = ex.message;
    }
  });

  /* Скачивание сгенерированного на клиенте файла (экспорт глоссария) */
  function downloadText(name, content) {
    const url = URL.createObjectURL(
      new Blob([content], { type: "text/plain;charset=utf-8" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }
  const cancelBtn = h("button", { class: "btn btn-ghost" }, "Отмена");
  const modal = UIC.modal({
    title: "Экспорт глоссария для анализа",
    build: () => [
      h("div", { class: "modal-text" }, "Формат файла:"),
      ...fmtPairs.map(([r, label]) =>
        h("label", { class: "ner-col-row" }, r, ` ${label}`),
      ),
      h(
        "div",
        { class: "exp-rows" },
        h("label", { class: "ner-col-row" }, "Порог count (мин.):", cntInp),
      ),
      typeCbs.length
        ? h(
            "div",
            { class: "exp-rows" },
            h("div", { class: "modal-text" }, "Типы (пусто = все):"),
            ...typeCbs,
          )
        : null,
      extra,
      err,
      h("div", { class: "modal-actions" }, cancelBtn, goBtn),
    ],
  });
  renderExtra();
  // cancelBtn/goBtn собраны до модалки — close отдаём поднятым объявлением
  function close(result) {
    return modal.close(result);
  }
  cancelBtn.addEventListener("click", close);
}

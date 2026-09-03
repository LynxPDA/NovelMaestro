/* поллинг завершения применений проверок (вкладка «Проверка»):
   ключ `${project}:${kind}` → {timer, jobId}; повторный запуск гасит
   старый watcher того же ключа (не плодим интервалы). */
const _reviewWatchers = new Map();
/* Размер страницы таблиц глоссария (вкладка «Глоссарий») и отчётов
   проверок (вкладка «Проверка»). Был глобалом app.js и пропал при
   рефакторинге (c60ac00) — без него вкладки падают с ReferenceError. */
const PAGE_SIZE = 200;
/* eslint-disable-next-line no-unused-vars -- глобал SPA, вызывается из app.js */
function viewProject(section, name, tab) {
  const st = {
    view: "files", // tab (третий сегмент роута) может открыть свою вкладку
    path: "",
    edit: null,
    ner: null,
    review: {},
    search: null,
    editor: null, // вкладка «Редактор» (глава/панели/подсветка)
    chaptersType: null, // тип файлов во вкладке «Главы» (localStorage-нет)
  };
  const page = h("div", { class: "page" });

  function setPath(path) {
    st.path = path || "";
    st.edit = null;
    st.search = null;
    render();
  }

  function openEditor(full) {
    st.edit = full;
    st.search = null;
    render();
  }

  function setView(view) {
    st.view = view;
    st.edit = null;
    st.search = null;
    render();
  }

  const TABS = [
    ["files", "Файлы"],
    ["editor", "Редактор"],
    ["ner", "Глоссарий"],
    ["review", "Проверки"],
    ["chapters", "Главы"],
    ["status", "Статус"],
    ["config", "Конфиг"],
    ["prompts", "Промпты"],
    ["logs", "Логи"],
    ["notes", "Заметки"],
  ];
  // роут #/project/раздел/книга/<вкладка> — открыть конкретную
  // вкладку (например, #/.../logs из страницы «Запуски»)
  if (tab && TABS.some((t) => t[0] === tab)) st.view = tab;

  async function render() {
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
      h(
        "a",
        { class: "btn btn-sm", href: `#/run/${section}/${name}` },
        "▶ Запуски",
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
    else if (st.view === "ner") body = await nerView();
    else if (st.view === "review") body = await reviewView();
    else if (st.view === "chapters") body = await chaptersView();
    else if (st.view === "status") body = await statusView();
    else if (st.view === "config") body = await configView();
    else if (st.view === "prompts") body = await promptsView();
    else if (st.view === "logs") body = await logsView();
    else if (st.view === "notes") body = await notesView();
    else body = await filesView();
    page.append(header, tabs, body);
    if (st.edit && st.search && st._ed && st._ed.isCM) {
      // открыть CM-поиск с фрагментом ошибки после монтирования редактора
      const q = st.search;
      window.CM.openSearchPanel(st._ed.view);
      const panel = st._ed.view.dom.querySelector(".cm-panel.cm-search");
      const input = panel && panel.querySelector("input");
      if (input) {
        input.value = q;
        input.dispatchEvent(new Event("change", { bubbles: true }));
        input.focus();
      }
    }
  }

  function downloadUrl(full) {
    const q = new URLSearchParams({
      project: `${section}/${name}`,
      path: full,
    });
    return `/api/download?${q}`;
  }

  async function filesView() {
    const q = new URLSearchParams({ project: `${section}/${name}` });
    if (st.path) q.set("path", st.path);
    let data;
    try {
      data = await api(`/files?${q}`);
    } catch (ex) {
      return h("div", { class: "files-empty" }, ex.message);
    }
    const parts = st.path ? st.path.split("/") : [];
    const crumbs = h("div", { class: "crumbs" });
    const walk = [];
    crumbs.append(crumb(`${section}/${name}`, () => setPath("")));
    for (const p of parts) {
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
    const toolbar = h(
      "div",
      { class: "files-toolbar" },
      crumbs,
      h("span", { class: "spacer" }),
      h(
        "button",
        { class: "btn btn-sm", onclick: () => upInput.click() },
        "Загрузить",
      ),
      addFileBtn,
      addDirBtn,
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
    const entries = data.entries || [];
    const FILES_PAGE_SIZE = 200;
    const fPager = h("div", { class: "ner-pager" });
    let fPage = 0;
    const drop = h("div", { class: "files-list" });
    function renderFiles() {
      const pages = Math.max(1, Math.ceil(entries.length / FILES_PAGE_SIZE));
      fPage = Math.min(fPage, pages - 1);
      drop.replaceChildren(
        ...entries
          .slice(fPage * FILES_PAGE_SIZE, (fPage + 1) * FILES_PAGE_SIZE)
          .map((e) => fileRow(e)),
      );
      fPager.replaceChildren(
        h(
          "button",
          {
            class: "btn btn-sm btn-ghost",
            disabled: fPage <= 0,
            onclick: () => {
              fPage--;
              renderFiles();
            },
          },
          "‹",
        ),
        h(
          "span",
          { class: "ner-pager-info" },
          ` ${fPage + 1} / ${pages} · файлов: ${entries.length} `,
        ),
        h(
          "button",
          {
            class: "btn btn-sm btn-ghost",
            disabled: fPage >= pages - 1,
            onclick: () => {
              fPage++;
              renderFiles();
            },
          },
          "›",
        ),
      );
    }
    renderFiles();
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
    return h("div", { class: "files-wrap" }, toolbar, drop, fPager);
  }

  function fileRow(e) {
    const full = st.path ? `${st.path}/${e.name}` : e.name;
    const nameNode = e.dir
      ? h(
          "a",
          {
            class: "fname",
            href: "#",
            onclick: (ev) => {
              ev.preventDefault();
              setPath(full);
            },
          },
          UICore.fileIcon(e) + " " + e.name,
        )
      : h("span", { class: "fname" }, UICore.fileIcon(e) + " " + e.name);
    const actions = h("div", { class: "factions" });
    /* «Переим.» — и у файлов, и у каталогов (POST /api/file/rename) */
    const renameBtn = h(
      "button",
      {
        class: "btn btn-sm btn-ghost",
        onclick: () =>
          nameModal(
            `Переименовать ${e.dir ? "каталог" : "файл"} ${e.name}`,
            "новое имя",
            async (nm) => {
              await api("/file/rename", {
                method: "POST",
                body: {
                  project: `${section}/${name}`,
                  path: full,
                  new_name: nm,
                },
              });
              toast(`Переименовано: ${e.name} → ${nm}`);
              render();
            },
            e.name,
          ),
      },
      "Переим.",
    );
    if (!e.dir) {
      actions.append(
        h(
          "a",
          {
            class: "btn btn-sm btn-ghost",
            href: downloadUrl(full),
            download: e.name,
          },
          "Скачать",
        ),
        h(
          "button",
          {
            class: "btn btn-sm btn-ghost",
            onclick: () => openEditor(full),
          },
          "Правка",
        ),
      );
    }
    actions.append(renameBtn);
    actions.append(
      h(
        "button",
        {
          class: "btn btn-sm btn-danger-ghost",
          onclick: () =>
            confirmModal(
              `Удаление ${e.dir ? "каталога" : "файла"}`,
              full,
              "УДАЛИТЬ",
              async () => {
                const dq = new URLSearchParams({
                  project: `${section}/${name}`,
                  path: full,
                });
                await api(`/file?${dq}`, { method: "DELETE" });
                toast(`Удалено: ${full}`);
                render();
              },
            ),
        },
        "Удалить",
      ),
    );
    const meta = h(
      "div",
      { class: "fmeta" },
      e.dir
        ? ""
        : `${fmtSize(e.size)} · ${new Date(e.mtime * 1000).toLocaleString("ru-RU")}`,
    );
    return h("div", { class: "frow" }, nameNode, meta, actions);
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
    const ed = makeEditor(data.content, ext);
    const err = h("div", { class: "form-error" });

    /* подсветка — по расширению файла (makeEditor/setLang), без ручного
       выбора представления; «Рендер» — только предпросмотр md/html */

    /* предпросмотр: markdown (marked) и html — оба в sandbox-iframe
       (без allow-scripts); те же стили/кегль/высота, что у «Заметок» */
    const frame = h("iframe", {
      class: "editor-preview-frame preview-adaptive",
      sandbox: "allow-same-origin",
      title: "предпросмотр",
    });
    let mode = "code"; // code | md | html
    const prevBtn = h(
      "button",
      { class: "btn btn-sm btn-ghost", title: "Показать отрендеренный вид" },
      "Рендер",
    );
    function renderPreview() {
      if (mode === "code") return;
      if (mode === "md") {
        const html = window.marked
          ? window.marked.parse(ed.getValue(), {
              mangle: false,
              headerIds: false,
            })
          : "<pre>marked не загружен</pre>";
        frame.srcdoc = mdPreviewSrcdoc(html);
      } else if (mode === "html") {
        frame.srcdoc = wrapPreviewDoc(ed.getValue());
      }
    }
    frame.addEventListener("load", () => fitPreviewFrame(frame));
    function setMode(next) {
      mode = next;
      if (mode === "code") {
        prevBtn.textContent = "Рендер";
        editorHost.style.display = "";
        frame.style.display = "none";
      } else {
        prevBtn.textContent = "Код";
        editorHost.style.display = "none";
        frame.style.display = "block";
        renderPreview();
      }
    }
    prevBtn.addEventListener("click", () => {
      if (mode === "code") {
        setMode(ext === "html" || ext === "htm" ? "html" : "md");
      } else {
        setMode("code");
      }
    });

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

    const findBtn = h("button", { class: "btn btn-sm btn-ghost" }, "Поиск");
    findBtn.addEventListener("click", () => {
      if (ed.isCM) {
        window.CM.openSearchPanel(ed.view);
      } else {
        toast("Поиск доступен в редакторе CodeMirror (Ctrl+F)", "err");
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
        if (mode !== "code") renderPreview();
      }),
      findBtn,
      prevBtn,
      saveBtn,
    );
    const editorHost = h("div", { class: "editor-cm" }, ed.root);
    st._ed = ed;
    return h(
      "div",
      { class: "editor-wrap editor-has-preview" },
      toolbar,
      err,
      editorHost,
      frame,
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
    const ed = (st.editor = st.editor || {
      chapter: null,
      mode: "two", // one | two — по умолчанию две панели (оригинал+перевод)
      left: { type: null, text: null, dirty: false },
      right: { type: null, text: null, dirty: false },
      hl: true, // подсветка терминов глоссария — по умолчанию включена
      ngram: 3, // размер n-граммы нечёткого поиска (аналог --ner_ngram)
      threshold: 0.75, // порог пересечения н-грамм (аналог --ner_threshold)
      ner: null, // кеш {items, matcher} глоссария
      panes: null, // кеш панелей — повторный рендер не теряет правки
      wrap: null, // DOM вкладки: пока открыт проект; F5/другой проект — сброс
    });
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
      ed.chapter = chapters[0].dir;
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
      applyMatcher();
    });
    thresholdInput.addEventListener("change", () => {
      const v = readThreshold(thresholdInput.value);
      ed.threshold = v == null ? 0.75 : v;
      thresholdInput.value = String(ed.threshold);
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
      renderModeLabel();
      rebuildGrid();
    });
    hlBtn.addEventListener("click", () => {
      ed.hl = !ed.hl;
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
      for (const k of Object.keys(it)) if (k !== "__new") knownKeys.add(k);
    }

    const search = h("input", { class: "input", placeholder: "Поиск…" });
    // из «→ Глоссарий» редактора — применяем один раз и сбрасываем,
    // чтобы ручные заходы на вкладку не наследовали чужой поиск
    if (st.search) {
      search.value = st.search;
      st.search = null;
    }
    const table = h("table", { class: "ner-table" });
    const tbody = h("tbody");
    const pager = h("div", { class: "ner-pager" });
    let editing = null; // редактируемая запись (объект из data.items)
    let page = 0; // M6: текущая страница таблицы
    const colLabel = (key) => key; // заголовки — сырые ключи JSON (term, type, …)
    const visibleCols = () => (cols == null ? [...knownKeys] : cols);
    function colsLabel() {
      if (cols == null) return "Все столбцы";
      if (cols.length === 1) return colLabel(cols[0]);
      return `Столбцы (${cols.length})`;
    }
    /* R8-5: значения-объекты/массивы (напр. _votes_pinyin) показываем
       компактным JSON, а не «[object Object]» */
    const cellText = UICore.nerCellText;
    const isStruct = (v) => v != null && typeof v === "object";

    /* сортировка кликом по заголовку; дефолт — count ↓ даже без столбца */
    let sortField = "count";
    let sortDir = "desc";

    const LS_SEARCH_KEY = `nerSearch:${section}/${name}`;
    const LS_TYPES_KEY = `nerTypes:${section}/${name}`;
    let searchFields = null; // null = все поля, [] = ни одного
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

    function visible() {
      const filtered = UICore.filterNerItems(
        data.items,
        search.value,
        searchFields,
        typeFilter,
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
    function searchFieldsLabel() {
      if (searchFields == null) return "Все поля";
      if (searchFields.length === 1) return colLabel(searchFields[0]);
      return `Поля (${searchFields.length})`;
    }
    function typeFilterLabel() {
      if (typeFilter == null) return "Все типы";
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
      allCb.checked = current() == null;
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
      const modal = h(
        "div",
        {
          class: "modal-backdrop",
          onclick: (e) => e.target === modal && close(),
        },
        h(
          "div",
          { class: "modal" },
          h("div", { class: "modal-title" }, opts.title),
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
        ),
      );
      document.body.append(modal);
      function close() {
        modal.remove();
      }
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

    function renderPager(total) {
      if (total === 0) {
        pager.replaceChildren();
        return;
      }
      const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
      pager.replaceChildren(
        h(
          "button",
          {
            class: "btn btn-sm btn-ghost",
            disabled: page <= 0,
            onclick: () => {
              page--;
              renderRows();
            },
          },
          "‹",
        ),
        h(
          "span",
          { class: "ner-pager-info" },
          ` ${page + 1} / ${pages} · всего ${total} `,
        ),
        h(
          "button",
          {
            class: "btn btn-sm btn-ghost",
            disabled: page >= pages - 1,
            onclick: () => {
              page++;
              renderRows();
            },
          },
          "›",
        ),
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
                      page = 0;
                      renderRows();
                    },
                  },
                  colLabel(c),
                  mark ? h("span", { class: "ner-th-dir" }, mark) : "",
                ),
              );
            }),
            h("th", {}, ""),
          ),
        ),
        tbody,
      );
      tbody.replaceChildren();
      const vis = visible(); // отфильтровано и отсортировано
      const pages = Math.max(1, Math.ceil(vis.length / PAGE_SIZE));
      if (page >= pages) page = pages - 1; // M6: страница не убегает
      const slice = vis.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
      for (const it of slice) {
        if (editing === it) {
          tbody.append(editRow(it));
        } else {
          tbody.append(viewRow(it));
        }
      }
      // редактируемая запись гарантированно видна: если она не попала
      // в slice (сортировка count ↓, поиск, фильтр типов) — докидываем
      if (editing && !slice.includes(editing)) {
        tbody.append(editRow(editing));
      }
      if (!slice.length && !editing) {
        tbody.append(
          h(
            "tr",
            {},
            h(
              "td",
              { colspan: String(visibleCols().length + 1) },
              "Нет записей",
            ),
          ),
        );
      }
      renderPager(vis.length);
    }

    function viewRow(it) {
      return h(
        "tr",
        { class: "ner-row" },
        ...visibleCols().map((c) =>
          h(
            "td",
            {
              class: c === "type" ? "ner-type" : "",
              title: isStruct(it[c]) ? cellText(it[c]) : "",
            },
            cellText(it[c]),
          ),
        ),
        h(
          "td",
          { class: "ner-actions" },
          h(
            "button",
            { class: "btn btn-sm btn-ghost", onclick: () => startEdit(it) },
            "✎",
          ),
          h(
            "button",
            {
              class: "btn btn-sm btn-danger-ghost",
              onclick: () =>
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
            },
            "✕",
          ),
        ),
      );
    }

    function editRow(it) {
      const inputs = {};
      const tr = h(
        "tr",
        { class: "ner-row ner-editing" },
        ...visibleCols().map((c) => {
          /* R8-5: значение-объект редактируется как JSON-текст; при
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
      if (idx >= 0) page = Math.floor(idx / PAGE_SIZE); // M6: на свою страницу
      renderRows();
    }

    /* «Столбцы»: чекбоксы всех ключей записи (единая модалка «Все / набор»),
       подпись как у полей/типов: «Все столбцы» / «Столбцы (N)» */
    const colBtn = h(
      "button",
      { class: "btn btn-sm btn-ghost", title: "Какие столбцы показывать" },
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



    /* поля поиска: как «+ Столбец», по умолчанию все ключи записи */
    const searchFieldsBtn = h(
      "button",
      { class: "btn btn-sm btn-ghost", title: "Где искать" },
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
        get: () => searchFields,
        set: (next) => {
          searchFields = next;
          saveSearchFields();
          refreshSearchFieldsBtn();
          page = 0;
          renderRows();
        },
        reset: () => {
          searchFields = null;
          saveSearchFields();
          refreshSearchFieldsBtn();
          page = 0;
          renderRows();
        },
      });
    });
    const typeBtn = h(
      "button",
      { class: "btn btn-sm btn-ghost", title: "Фильтр типов" },
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
          page = 0;
          renderRows();
        },
        reset: () => {
          typeFilter = null;
          saveTypeFilter();
          refreshTypeBtn();
          page = 0;
          renderRows();
        },
      });
    });

    /* «+ Столбец»: добавить новое поле всем терминам глоссария */
    const addColBtn = h(
      "button",
      { class: "btn btn-sm btn-ghost", title: "Добавить столбец ко всем терминам" },
      "+ Столбец",
    );
    addColBtn.addEventListener("click", () => {
      const inp = h("input", {
        class: "input",
        placeholder: "имя столбца (ключ JSON)",
      });
      const err2 = h("div", { class: "form-error" });
      const modal = h(
        "div",
        { class: "modal-backdrop", onclick: (e) => e.target === modal && close() },
        h(
          "div",
          { class: "modal" },
          h("div", { class: "modal-title" }, "Новый столбец"),
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
        ),
      );
      function close() {
        modal.remove();
      }
      document.body.append(modal);
      inp.focus();
    });
    /* «✕ Столбец»: удалить поле из всех терминов (кроме term) */
    const delColBtn = h(
      "button",
      { class: "btn btn-sm btn-ghost", title: "Удалить столбец из всех терминов" },
      "✕ Столбец",
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
      const modal = h(
        "div",
        { class: "modal-backdrop", onclick: (e) => e.target === modal && close() },
        h(
          "div",
          { class: "modal" },
          h("div", { class: "modal-title" }, "Удалить столбец"),
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
        ),
      );
      function close() {
        modal.remove();
      }
      document.body.append(modal);
    });
    /* «✕ По фильтру»: удалить термины по условию (count > N) или все найденные */
    const delFilterBtn = h(
      "button",
      {
        class: "btn btn-sm btn-ghost",
        title: "Удалить термины по условию или все найденные",
      },
      "✕ По фильтру",
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
        // условие применяется ко ВСЕМ терминам глоссария
        const raw = valInp.value.trim();
        if (!raw) return [];
        const num = Number(raw);
        return data.items.filter((it) => {
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
      const modal = h(
        "div",
        { class: "modal-backdrop", onclick: (e) => e.target === modal && close() },
        h(
          "div",
          { class: "modal" },
          h("div", { class: "modal-title" }, "Удалить термины по фильтру"),
          h(
            "div",
            { class: "modal-text" },
            "Условие применяется ко всем терминам глоссария. ",
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
        ),
      );
      function close() {
        modal.remove();
      }
      document.body.append(modal);
    });

    const addTerm = () => {
      const it = { term: "", type: "noun", translation: "", __new: true };
      data.items.unshift(it); // новый термин — сверху
      editing = it;
      // сбросить поиск и фильтр типов — иначе новая запись (count=null,
      // пустой термин) не пройдёт фильтр и не будет видна
      search.value = "";
      typeFilter = null;
      page = 0;
      renderRows();
    };
    // групповая кнопка: открывает меню действий, закрывает по клику вне
    function menuButton(icon, title, items) {
      const box = h("div", { class: "menu-box", hidden: true });
      for (const it of items) {
        box.append(
          h(
            "button",
            {
              class: "btn btn-sm btn-ghost menu-item",
              onclick: () => {
                box.hidden = true;
                it.action();
              },
            },
            it.label,
          ),
        );
      }
      const btn = h(
        "button",
        { class: "btn btn-sm btn-ghost", title, onclick: () => {
          box.hidden = !box.hidden;
        } },
        icon,
      );
      const wrap = h("div", { class: "toolbar-menu" }, btn, box);
      document.addEventListener("click", (e) => {
        if (!wrap.contains(e.target)) box.hidden = true;
      });
      return wrap;
    }
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
    const toolbar = h(
      "div",
      { class: "files-toolbar" },
      h("span", { class: "ner-search" }, search, searchFieldsBtn),
      h("span", { class: "spacer" }),
      typeBtn,
      colBtn,
      // «+»: новый столбец или новый термин (меню вместо двух кнопок)
      menuButton("+", "Добавить столбец или термин", [
        { label: "+ Столбец", action: () => addColBtn.click() },
        { label: "+ Термин", action: addTerm },
      ]),
      // «x»: удалить столбец или термины по фильтру
      menuButton("x", "Удалить столбец или термины по фильтру", [
        { label: "x Столбец", action: () => delColBtn.click() },
        { label: "x По фильтру", action: () => delFilterBtn.click() },
      ]),
      exportBtn,
    );
    search.addEventListener("input", () => {
      page = 0; // M6: фильтр — на первую страницу
      renderRows();
    });
    renderRows();
    return h("div", { class: "files-wrap" }, toolbar, table, pager);
  }
  /* ── Проверка (review ner / translate_check_llm) ── */
  async function reviewView() {
    const wrap = h("div", { class: "review-wrap" });
    const panel = h("div", { class: "review-panel" });
    wrap.append(panel);
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
      const bakBox = h(
        "label",
        { class: "chk rv-bak" },
        h("input", { type: "checkbox" }),
        " не создавать .bak",
      );
      attachTooltip(
        bakBox,
        "Не создавать резервную копию файла перед применением правок",
      );
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
      let page = 0; // текущая страница списка правок
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
      function entryHead(e) {
        if (kind === "ner") return `${e["term"] || "?"} · ${e["field"] || "?"}`;
        const ch = e["chapter"];
        return (
          `Гл.${ch == null ? "?" : ch}` + (e["type"] ? ` · ${e["type"]}` : "")
        );
      }
      function entryMeta(e) {
        const parts = [];
        if (e["stage"]) parts.push(e["stage"]);
        if (e["applied_at"]) parts.push(`применено: ${e["applied_at"]}`);
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
        const modal = h(
          "div",
          {
            class: "modal-backdrop",
            onclick: (ev) => ev.target === modal && close(),
          },
          h(
            "div",
            { class: "modal" },
            h("div", { class: "modal-title" }, `Правка ${i + 1}`),
            h("label", { class: "rv-label" }, "Было"),
            oldIn,
            h(
              "div",
              { class: "field-help" },
              "не редактируется — по этому тексту правка ищется в главе",
            ),
            h("label", { class: "rv-label" }, "Стало"),
            newIn,
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
                      {
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
          ),
        );
        function close() {
          modal.remove();
        }
        document.body.append(modal);
        newIn.focus();
      }
      function entryRow(e, i) {
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
          { class: "rv-row" + (rejected ? " rv-row-reject" : "") },
          h(
            "div",
            { class: "rv-row-head" },
            h("span", { class: "rv-row-title" }, entryHead(e)),
            badge,
          ),
          h(
            "div",
            { class: "rv-diff" },
            h("span", { class: "rv-old" }, e["old"] || ""),
            " → ",
            h("span", { class: "rv-new" }, e["new"] || ""),
          ),
          e["reason"] ? h("div", { class: "rv-reason" }, e["reason"]) : null,
          h("div", { class: "rv-meta" }, entryMeta(e)),
          actions,
        );
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
          const total = parsed.entries.length;
          const pages = Math.max(1, Math.ceil(total / RV_PAGE));
          if (page >= pages) page = pages - 1;
          const slice = parsed.entries.slice(
            page * RV_PAGE,
            (page + 1) * RV_PAGE,
          );
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
            ),
            h(
              "div",
              { class: "rv-list" },
              // индекс записи — глобальный (page * RV_PAGE + i):
              // кнопки правят запись по позиции в parsed.entries
              slice.map((e, i) => entryRow(e, page * RV_PAGE + i)),
            ),
            pages > 1
              ? h(
                  "div",
                  { class: "ner-pager" },
                  h(
                    "button",
                    {
                      class: "btn btn-sm btn-ghost",
                      disabled: page <= 0,
                      onclick: () => {
                        page--;
                        renderList();
                      },
                    },
                    "‹",
                  ),
                  h(
                    "span",
                    { class: "ner-pager-info" },
                    ` ${page + 1} / ${pages} · всего ${total} `,
                  ),
                  h(
                    "button",
                    {
                      class: "btn btn-sm btn-ghost",
                      disabled: page >= pages - 1,
                      onclick: () => {
                        page++;
                        renderList();
                      },
                    },
                    "›",
                  ),
                )
              : null,
          );
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
              no_bak: bakBox.querySelector("input").checked,
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
      const clearBtn = h(
        "button",
        {
          class: "btn btn-sm btn-ghost rv-clear",
          title: "Удалить все правки из файла",
        },
        "Очистить",
      );
      clearBtn.addEventListener("click", () => {
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
      });
      const actionsBar = h(
        "div",
        { class: "review-actions" },
        dryBtn,
        applyBtn,
        bakBox,
        h("span", { class: "spacer" }),
        clearBtn,
        saveBtn,
      );
      function renderEditor() {
        body.replaceChildren();
        if (!ed) {
          ed = makeEditor(
            parsed ? JSON.stringify(parsed.doc, null, 2) : "",
            "json",
          );
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
    /* порядок секций: 1 · глоссарий (LLM) → 2 · проверка перевода
       → 3 · перевод (LLM) — совпадает с нумерацией */
    panel.append(
      makeCard(
        "1 · Проверка глоссария (LLM) — ner_review.json",
        "/ner/review",
        "/ner/review/apply",
        "ner",
      ),
    );
    const sec2 = h(
      "div",
      { class: "review-section" },
      h("div", { class: "review-section-title" }, "2 · Проверка перевода"),
      h(
        "div",
        { class: "review-section-sub" },
        "отчёты стадии translate_check (logs/check_*.txt)",
      ),
    );
    panel.append(sec2);
    sec2.append(await renderCheckReports(section, name));
    panel.append(
      makeCard(
        "3 · Проверка перевода (LLM) — translate_check_llm_review.json",
        "/translate_check_llm/review",
        "/translate_check_llm/review/apply",
        "tcl",
      ),
    );
    /* 4 · Оценка перевода (LLM) — md-отчёты translate_quality */
    /* renderCheckReports/renderQualityReports — глобальные функции
       (см. ниже), принимают (section, name) параметрами — без
       глобалов, иначе вкладка падает с ReferenceError */
    const sec4 = h(
      "div",
      { class: "review-section" },
      h(
        "div",
        { class: "review-section-title" },
        "4 · Оценка перевода (LLM)",
      ),
      h(
        "div",
        { class: "review-section-sub" },
        "md-отчёты стадии translate_quality "
          + "(по умолчанию translation_quality_assessment.md)",
      ),
    );
    panel.append(sec4);
    sec4.append(await renderQualityReports(section, name));
    return wrap;
  }

  /* ── Конфиг: env + metadata ────────────────────── */
  /* ── Конфиг: env + metadata + обложка (W6) ─────── */
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
    const status = h("span", { class: "review-status" });
    const rows = h("div", { class: "chapters-rows" });
    let inputs = {}; // id → {input, orig}

    async function load() {
      st.chaptersType = typeSel.value;
      saveBtn.disabled = true;
      status.textContent = "Загрузка…";
      rows.replaceChildren();
      inputs = {};
      try {
        const r = await api(
          `/projects/${section}/${name}/chapters/titles` +
          `?type=${encodeURIComponent(typeSel.value)}`,
        );
        const titles = r.titles || {};
        const ids = Object.keys(titles)
          .map(Number)
          .sort((a, b) => a - b);
        for (const id of ids) {
          const inp = h("input", {
            class: "input chapters-title",
            value: titles[id],
          });
          inputs[id] = { input: inp, orig: String(titles[id]) };
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
        status.textContent = ids.length
          ? `Глав: ${ids.length} (${typeSel.value})`
          : "Файлы не найдены";
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

    typeSel.addEventListener("change", load);
    load();

    const toolbar = h(
      "div",
      { class: "chapters-toolbar" },
      h("span", { class: "field-label" }, "Тип файлов глав:"),
      typeSel,
      h("span", { class: "spacer" }),
      status,
      saveBtn,
    );
    const hint = h(
      "div",
      { class: "card-hint" },
      "Название главы — первая непустая строка файла. " +
        "Правки сохраняются в соответствующие файлы глав одной кнопкой.",
    );
    return h("div", { class: "files-wrap" }, toolbar, hint, err, rows);
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

    /* — .env: только собственный .env проекта (общий — на главной,
         вкладка «Настройки») — */
    const envCard = h("div", { class: "review-card" });
    const envEd = makeEditor("", "txt");
    const envMeta = h("div", { class: "review-status" });
    const modeSel = h("select", { class: "input" });
    modeSel.append(
      h("option", { value: "shared" }, "Использовать общий .env"),
      h("option", { value: "own" }, "Свой .env проекта"),
    );
    attachTooltip(
      modeSel,
      "Общий .env — системный (read-only, правится на главной); "
        + "собственный .env проекта — перекрывает общий для этой книги",
    );
    const envToolbar = h("div", { class: "files-toolbar" });
    const envBody = h(
      "div",
      { class: "review-card-body" },
      envToolbar,
      h("div", { class: "editor-cm editor-cm-small" }, envEd.root),
      envMeta,
      err,
    );
    envCard.append(
      h("div", { class: "review-card-title" }, "Файл .env"),
      envBody,
    );
    let hasOwn = false;
    let envVisible = false;
    /* select активен — «Общий .env» показывает системный
       (read-only, сохранять из проекта НЕЛЬЗЯ — редактируется на
       главной), «Свой .env» — редактор проекта (создание/правка/
       удаление). loadEnv НЕ трогает modeSel — иначе выбор пользователя
       сбрасывался (баг). */
    async function loadEnv() {
      err.textContent = "";
      try {
        const d = await api(`/env?${q}&scope=project`);
        hasOwn = !!d.exists;
        envVisible = !!d.visible;
        envEd.setValue(hasOwn ? d.content || d.masked || "" : "");
        envEd.setReadOnly(false);
        renderEnvToolbar();
      } catch (ex) {
        err.textContent = ex.message;
      }
    }
    async function loadSharedEnv() {
      try {
        const d = await api(`/env?scope=global`);
        envEd.setValue(d.content || d.masked || "");
        envEd.setReadOnly(true);
        renderEnvToolbar();
      } catch (ex) {
        err.textContent = ex.message;
      }
    }
    function envChangesFromEditor() {
      const changes = {};
      for (const line of envEd.getValue().split("\n")) {
        if (!line || line.startsWith("#")) continue;
        const eq = line.indexOf("=");
        if (eq < 0) continue;
        const key = line.slice(0, eq).trim();
        const val = line.slice(eq + 1).trim();
        if (key && val && val !== "••••") changes[key] = val;
      }
      return changes;
    }
    const dupSharedBtn = h(
      "button",
      { class: "btn btn-sm btn-ghost" },
      "Дублировать из общего",
    );
    dupSharedBtn.addEventListener("click", async () => {
      try {
        const d = await api(`/env?scope=global`);
        envEd.setValue(d.content || d.masked || "");
        envMeta.textContent = "Содержимое .env — сохраните, чтобы создать свой";
      } catch (ex) {
        err.textContent = ex.message;
      }
    });
    const dupTplBtn = h(
      "button",
      { class: "btn btn-sm btn-ghost" },
      "Дублировать из шаблона",
    );
    dupTplBtn.addEventListener("click", async () => {
      try {
        const d = await api(`/env/template`);
        envEd.setValue(d.content || "");
        envMeta.textContent = d.name
          ? `Шаблон ${d.name} — сохраните, чтобы создать свой`
          : "Шаблон templates/.env.example не найден";
      } catch (ex) {
        err.textContent = ex.message;
      }
    });
    const envSave = h("button", { class: "btn btn-sm" }, "Сохранить .env");
    envSave.addEventListener("click", async () => {
      err.textContent = "";
      try {
        if (envVisible) {
          await api("/env", {
            method: "PUT",
            body: {
              project: `${section}/${name}`,
              scope: "project",
              content: envEd.getValue(),
            },
          });
        } else {
          await api("/env", {
            method: "PUT",
            body: {
              project: `${section}/${name}`,
              scope: "project",
              changes: envChangesFromEditor(),
            },
          });
        }
        toast(".env проекта сохранён");
        await loadEnv();
      } catch (ex) {
        err.textContent = ex.message;
      }
    });
    const envDel = h(
      "button",
      { class: "btn btn-sm btn-danger-ghost" },
      "Удалить .env",
    );
    envDel.addEventListener("click", () =>
      confirmModal(
        "Удалить .env проекта",
        "Проект вернётся к системному .env (projects/.env)",
        "УДАЛИТЬ",
        async () => {
          try {
            await api(`/env?${q}&scope=project`, { method: "DELETE" });
            toast(".env проекта удалён");
            await loadEnv();
            // после удаления проект возвращается к системному
            if (!hasOwn) {
              modeSel.value = "shared";
              await loadSharedEnv();
            }
          } catch (ex) {
            err.textContent = ex.message;
          }
        },
      ),
    );
    function renderEnvToolbar() {
      envToolbar.replaceChildren();
      envToolbar.append(modeSel, h("span", { class: "spacer" }));
      if (modeSel.value === "shared") {
        // системный .env — read-only, из проекта сохранять нельзя;
        // «Удалить .env» в этом режиме не показываем: удаление —
        // только из режима «Свой .env проекта»
        envMeta.textContent =
          "Системный .env (projects/.env) — read-only, редактируется на главной, вкладка «Настройки»";
      } else if (hasOwn) {
        envMeta.textContent =
          "собственный .env" +
          (envVisible ? "" : " · значения скрыты (--auth)");
        envToolbar.append(envSave, envDel);
      } else {
        envMeta.textContent =
          "своего .env нет — создайте: дублируйте из общего или шаблона, либо напишите с нуля";
        envToolbar.append(dupSharedBtn, dupTplBtn, envSave);
      }
    }
    modeSel.addEventListener("change", async () => {
      if (modeSel.value === "shared") await loadSharedEnv();
      else await loadEnv();
    });
    async function initEnv() {
      await loadEnv();
      // порядок важен: сначала выбор режима, потом рендер тулбара —
      // иначе кнопки рисуются для старого значения select (баг:
      // при «Своём .env» по умолчанию не было «Сохранить»)
      modeSel.value = hasOwn ? "own" : "shared";
      renderEnvToolbar();
      if (modeSel.value === "shared") await loadSharedEnv();
    }
    await initEnv();

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
          h("div", { class: "review-actions" }, tpl, save),
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
      envCard,
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
    const modal = h(
      "div",
      {
        class: "modal-backdrop",
        onclick: (e) => e.target === modal && close(),
      },
      h(
        "div",
        { class: "modal" },
        h("div", { class: "modal-title" }, `Загрузить ${rel} из шаблона`),
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
      ),
    );
    document.body.append(modal);
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
    function close() {
      modal.remove();
    }
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
    const ed = makeEditor("", "txt");
    const nameLabel = h("div", { class: "prompt-name" });
    let current = null;

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
        nameLabel.textContent = fname;
        ed.setValue(d.content || "");
        if (ed.isCM) ed.setLang(extOf(fname));
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
      const modal = h(
        "div",
        {
          class: "modal-backdrop",
          onclick: (e) => e.target === modal && modal.remove(),
        },
        h(
          "div",
          { class: "modal" },
          h("div", { class: "modal-title" }, "Новый промпт"),
          nameInput,
          cerr,
          h(
            "div",
            { class: "modal-actions" },
            h(
              "button",
              { class: "btn btn-ghost", onclick: () => modal.remove() },
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
                    modal.remove();
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
        ),
      );
      document.body.append(modal);
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
      delBtn,
      saveBtn,
    );
    renderList();
    const editorHost = h("div", { class: "editor-cm" }, ed.root);
    return h("div", { class: "files-wrap" }, toolbar, err, list, editorHost);
  }

  /* Модалка выбора шаблона (W4): наборы templates + имя файла →
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
    const modal = h(
      "div",
      {
        class: "modal-backdrop",
        onclick: (e) => e.target === modal && close(),
      },
      h(
        "div",
        { class: "modal" },
        h("div", { class: "modal-title" }, "Создать промпт из шаблона"),
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
      ),
    );
    syncName();
    document.body.append(modal);
    sel.focus();
    function close() {
      modal.remove();
    }
  }

  /* ── Логи (M8) ─────────────────────────────────── */
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
        st.logPath = "";
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
          st.logPath = target;
          render();
        }),
      );
    }
    const list = h("div", { class: "prompt-list" });
    const pre = h("pre", { class: "log-view" });
    const meta = h("div", { class: "review-status" });
    const LOG_PAGE_SIZE = 20;
    let lPage = 0;
    const lPager = h("div", { class: "ner-pager" });
    const follow = h(
      "button",
      {
        class: "btn btn-sm btn-ghost",
        title: "Догружать хвост файла каждые 1,5 с (автопрокрутка вниз)",
      },
      "Автообновление",
    );
    let timer = null;

    function renderList(selected) {
      list.replaceChildren();
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
      const entries = [
        ...dirs.map((d) => ({ kind: "dir", name: d, mtime: 0, size: 0 })),
        ...files.map((f) => ({ kind: "file", ...f })),
      ];
      const pages = Math.max(1, Math.ceil(entries.length / LOG_PAGE_SIZE));
      lPage = Math.min(lPage, pages - 1);
      const slice = entries.slice(
        lPage * LOG_PAGE_SIZE,
        (lPage + 1) * LOG_PAGE_SIZE,
      );
      for (const e of slice) {
        if (e.kind === "dir") {
          const btn = h(
            "button",
            { class: "btn btn-sm btn-ghost prompt-item" },
            `📁 ${e.name}/`,
          );
          btn.addEventListener("click", () => {
            st.logPath = cur ? `${cur}/${e.name}` : e.name;
            render();
          });
          list.append(btn);
        } else {
          const btn = h(
            "button",
            {
              class:
                "btn btn-sm btn-ghost prompt-item" +
                (selected === e.name ? " prompt-item-active" : ""),
            },
            `${e.name} · ${fmtSize(e.size)}`,
          );
          btn.addEventListener("click", () => loadLog(e.name, false, cur));
          const del = h(
            "button",
            { class: "btn btn-sm btn-ghost log-del-btn", title: "Удалить лог" },
            "🗑",
          );
          del.addEventListener("click", (ev) => {
            ev.stopPropagation();
            const full = cur ? `${cur}/${e.name}` : e.name;
            confirmModal(
              "Удалить лог",
              `Файл ${full} будет удалён`,
              "УДАЛИТЬ",
              async () => {
                try {
                  const sub = cur ? `&dir=${encodeURIComponent(cur)}` : "";
                  await api(`/logs/${encodeURIComponent(e.name)}?${q}${sub}`, {
                    method: "DELETE",
                  });
                  toast(`Лог удалён: ${full}`);
                  st.logPath = ""; // папка могла стать пустой — на корень
                  render();
                } catch (ex) {
                  toast(ex.message, "err");
                }
              },
            );
          });
          list.append(h("div", { class: "prompt-item-row" }, btn, del));
        }
      }
      if (!entries.length) {
        list.append(h("div", { class: "empty" }, "Логов нет"));
      }
      lPager.replaceChildren(
        h(
          "button",
          {
            class: "btn btn-sm btn-ghost",
            disabled: lPage <= 0,
            onclick: () => {
              lPage--;
              renderList(selected);
            },
          },
          "‹",
        ),
        h(
          "span",
          { class: "ner-pager-info" },
          ` ${lPage + 1} / ${pages} · логов: ${files.length} `,
        ),
        h(
          "button",
          {
            class: "btn btn-sm btn-ghost",
            disabled: lPage >= pages - 1,
            onclick: () => {
              lPage++;
              renderList(selected);
            },
          },
          "›",
        ),
      );
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
        renderList(name);
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
            st.logPath = "";
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
    return h("div", { class: "files-wrap" }, toolbar, list, lPager, pre);
  }

  /* ── Отчёты translate_check (W7) ────────────── */
  async function notesView() {
    /* «Заметки» проекта — markdown-файл source/info.md (копируется из
       шаблона General при создании проекта); редактор как у «Заметок»
       приложения: CodeMirror + md-предпросмотр в sandbox-iframe */
    const NOTES_PATH = "source/info.md";
    const err = h("div", { class: "form-error" });
    const ed = makeEditor("", "md");
    const editorHost = h(
      "div",
      { class: "editor-cm editor-cm-small" },
      ed.root,
    );
    const frame = h("iframe", {
      class: "editor-preview-frame preview-adaptive",
      sandbox: "allow-same-origin",
      title: "предпросмотр",
    });
    const status = h("div", { class: "review-status" });
    let mode = "code";
    const prevBtn = h(
      "button",
      { class: "btn btn-sm btn-ghost", title: "Показать отрендеренный вид" },
      "Рендер",
    );
    function renderPreview() {
      if (mode === "code") return;
      const html = window.marked
        ? window.marked.parse(ed.getValue(), {
            mangle: false,
            headerIds: false,
          })
        : "<pre>marked не загружен</pre>";
      frame.srcdoc = mdPreviewSrcdoc(html);
    }
    frame.addEventListener("load", () => fitPreviewFrame(frame));
    function setMode(next) {
      mode = next;
      if (mode === "code") {
        prevBtn.textContent = "Рендер";
        editorHost.style.display = "";
        frame.style.display = "none";
      } else {
        prevBtn.textContent = "Код";
        editorHost.style.display = "none";
        frame.style.display = "block";
        renderPreview();
      }
    }
    prevBtn.addEventListener("click", () => {
      if (mode === "code") setMode("md");
      else setMode("code");
    });
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
    setMode("code");
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
              if (mode !== "code") renderPreview();
            }),
            prevBtn,
            saveBtn,
          ),
          err,
          editorHost,
          frame,
        ),
      ),
    );
  }

  /* ── Отчёты translate_check (W7) — секция 2 «Проверки» ── */
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
        "Нет отчётов — запустите стадию translate_check (экран «Запуски», стадия 4)",
      );
    }
    let current = data.reports[0];
    let page = 0; // M8: страница таблицы текущего отчёта
    let rPage = 0; // страница списка отчётов (10/стр)
    const REPORT_PAGE_SIZE = 10;
    const list = h("div", { class: "prompt-list" });
    const listPager = h("div", { class: "ner-pager" });
    const body = h("div", { class: "review-card" });
    const pager = h("div", { class: "ner-pager" });

    function renderPager(total) {
      if (total === 0) {
        pager.replaceChildren();
        return;
      }
      const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
      pager.replaceChildren(
        h(
          "button",
          {
            class: "btn btn-sm btn-ghost",
            disabled: page <= 0,
            onclick: () => {
              page--;
              renderReport();
            },
          },
          "‹",
        ),
        h(
          "span",
          { class: "ner-pager-info" },
          ` ${page + 1} / ${pages} · всего ${total} `,
        ),
        h(
          "button",
          {
            class: "btn btn-sm btn-ghost",
            disabled: page >= pages - 1,
            onclick: () => {
              page++;
              renderReport();
            },
          },
          "›",
        ),
      );
    }
    function renderListPager(total) {
      const pages = Math.max(1, Math.ceil(total / REPORT_PAGE_SIZE));
      listPager.replaceChildren(
        h(
          "button",
          {
            class: "btn btn-sm btn-ghost",
            disabled: rPage <= 0,
            onclick: () => {
              rPage--;
              renderList();
            },
          },
          "‹",
        ),
        h(
          "span",
          { class: "ner-pager-info" },
          ` ${rPage + 1} / ${pages} · отчётов: ${total} `,
        ),
        h(
          "button",
          {
            class: "btn btn-sm btn-ghost",
            disabled: rPage >= pages - 1,
            onclick: () => {
              rPage++;
              renderList();
            },
          },
          "›",
        ),
      );
    }
    function renderList() {
      list.replaceChildren();
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
          page = 0; // M8: другой отчёт — с первой страницы
          renderList();
          renderReport();
        });
        list.append(btn);
      }
      listPager.replaceChildren();
      renderListPager(data.reports.length);
    }
    function renderReport() {
      body.replaceChildren();
      const r = current;
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
          h("tbody", {}, rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)),
        ),
      );
      body.append(pager);
      renderPager(rows.length);
    }
    renderList();
    renderReport();
    return h(
      "div",
      { class: "files-wrap" },
      h("div", { class: "check-list" }, list, listPager),
      body,
    );
  }

  render();
  return page;
}

/* ── Оценка перевода (LLM): md-отчёты translate_quality ── */
async function renderQualityReports(section, name) {
  /* Секция «Проверки»: выбор md-отчёта из корня проекта + рендер
     markdown в sandbox-iframe; по умолчанию —
     translation_quality_assessment.md. */
  const err = h("div", { class: "form-error" });
  const empty = h("div", { class: "files-empty" });
  const sel = h("select", { class: "input" });
  const frame = h("iframe", {
    class: "editor-preview-frame preview-adaptive",
    sandbox: "allow-same-origin",
    title: "предпросмотр отчёта оценки перевода",
  });
  frame.style.display = "none";

  async function renderFile(name) {
    const q = new URLSearchParams({
      project: `${section}/${name}`,
      path: name,
    });
    try {
      const r = await api(`/file?${q}`);
      const html = window.marked
        ? window.marked.parse(r.content || "", {
            mangle: false,
            headerIds: false,
          })
        : "<pre>marked не загружен</pre>";
      frame.srcdoc = mdPreviewSrcdoc(html);
      frame.style.display = "block";
      empty.textContent = "";
      err.textContent = "";
    } catch (ex) {
      err.textContent = ex.message;
    }
  }

  async function loadFiles() {
    const q = new URLSearchParams({ project: `${section}/${name}`, path: "" });
    let files = [];
    try {
      const r = await api(`/files?${q}`);
      files = (r.entries || [])
        .filter((e) => !e.dir && e.name.toLowerCase().endsWith(".md"))
        .sort((a, b) => b.mtime - a.mtime)
        .map((e) => e.name);
    } catch (ex) {
      err.textContent = ex.message;
    }
    sel.replaceChildren();
    if (!files.length) {
      sel.append(h("option", { value: "" }, "—"));
      sel.disabled = true;
      empty.textContent =
        "Нет отчётов — запустите стадию «Оценка перевода (LLM)» (Запуски)";
      frame.style.display = "none";
      return;
    }
    sel.disabled = false;
    for (const f of files) sel.append(h("option", { value: f }, f));
    const def = files.includes("translation_quality_assessment.md")
      ? "translation_quality_assessment.md"
      : files[0];
    sel.value = def;
    await renderFile(def);
  }

  sel.addEventListener("change", () => {
    if (sel.value) renderFile(sel.value);
  });
  frame.addEventListener("load", () => fitPreviewFrame(frame));

  const wrap = h(
    "div",
    { class: "quality-reports" },
    h(
      "div",
      { class: "check-list" },
      h("label", { class: "quality-label" }, "Отчёт:"),
      sel,
    ),
    empty,
    frame,
  );
  await loadFiles();
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
    [fmtText, "Текст — записи (Термин, Тип, Перевод)"],
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
  const modal = h(
    "div",
    { class: "modal-backdrop", onclick: (e) => e.target === modal && close() },
    h(
      "div",
      { class: "modal" },
      h("div", { class: "modal-title" }, "Экспорт глоссария для анализа"),
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
    ),
  );
  renderExtra();
  document.body.append(modal);
  function close() {
    modal.remove();
  }
  cancelBtn.addEventListener("click", close);
}

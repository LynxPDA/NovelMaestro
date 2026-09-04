  // translate_quality: последняя глава, помещающаяся в бюджет запроса.
  // Размер главы = перевод (тип файлов) + оригинал (chapter.txt);
  // бюджет — ТОЛЬКО на содержимое, промпт НЕ вычитается; доступна
  // как глобал — для node-тестов (vm-контекст), в браузере вызывается
  // из attachQualityRange.
  function qualityEndByBudget(chapters, start, budget, type) {
    const avail = budget || 0;
    if (avail <= 0 || !start || !chapters || !chapters.length) return "";
    const art = type + ".txt";
    const rows = chapters
      .map((ch) => {
        const a = ch.artifacts || {};
        return {
          id: parseInt(ch.id, 10) || 0,
          sz: (a[art] || 0) + (a["chapter.txt"] || 0),
        };
      })
      .filter((x) => x.id >= start && x.sz > 0)
      .sort((a, b) => a.id - b.id);
    let sum = 0;
    let last = "";
    for (const r of rows) {
      if (sum + r.sz > avail) break;
      sum += r.sz;
      last = String(r.id);
    }
    return last;
  }

// глобал для роутера app.js (plain-script: window.viewRun вызывается
// из app.js напрямую); явная привязка — чтобы линтер видел использование
window.viewRun = function viewRun(section, name, attachJobId) {

  const st = {
    stage: null, // выбранная стадия: {key, title, script, spec}
    options: null, // динамические опции {chapters, source, prompts, root}
    job: null, // текущий запуск
    log: [], // строки лога текущего запуска (ТОЛЬКО из SSE-стрима)
    events: [], // события глав конвейера (стадия 3)
    progress: null, // последнее событие прогресса {label, done, total}
    chapterState: null, // фактическое состояние артефактов глав (status API)
    gen: 0, // поколение отрисовки — гасит гонки двух render()
    values: {}, // значения формы по стадиям (данные; синхронизация режимов)
    touched: {}, // изменённые пользователем поля по стадиям (Set имён)
    preview: null, // epub: данные предпросмотра {entries, source, skips}
    previewDirty: true, // epub: настройки менялись после предпросмотра
    brPreview: null, // batch_replace: предпросмотр {segments, stats, …}
    brChapter: null, // batch_replace: выбранная глава предпросмотра
    brSig: "", // batch_replace: сигнатура формы последнего предпросмотра
  };
  const page = h("div", { class: "page" });
  let streamCtrl = null; // AbortController текущего SSE-стрима

  // ── фактическое состояние артефактов глав (для таблицы конвейера) ──
  async function loadChapterState() {
    try {
      const r = await api(`/projects/${section}/${name}/status`);
      const ch = r.status && r.status.chapters;
      st.chapterState = ch && typeof ch === "object" ? ch : null;
    } catch {
      st.chapterState = null;
    }
  }

  // ── прикрепление к конкретному запуску (лог + SSE + управление) ──
  async function attachToJob(jobId) {
    try {
      const r = await api(`/jobs/${jobId}`);
      const job = r.job;
      if (!job) {
        render();
        return;
      }
      if (job.project !== `${section}/${name}`) {
        toast("Запуск не относится к этому проекту", "err");
        render();
        return;
      }
      // лог живёт только на вкладке СВОЕЙ стадии активного запуска —
      // при прикреплении переключаемся на неё; по завершению запуска
      // панель лога закрывается (история — на «Дашборде» и в «Логах»)
      st.stage = job.action;
      st.job = job;
      st.log = [];
      st.events = [];
      st.progress = job.progress || null;
      if (job.events) st.events = job.events;
      await loadChapterState();
      await render();
      attachStream(jobId);
    } catch (ex) {
      toast(ex.message, "err");
      render();
    }
  }

  // ── авто-прикрепление к активному запуску проекта ──
  async function autoAttach() {
    try {
      const r = await api("/jobs");
      const mine = (r.jobs || []).filter(
        (j) => j.status === "running" && j.project === `${section}/${name}`,
      );
      if (mine.length) {
        attachToJob(mine[0].id);
      } else {
        render();
      }
    } catch {
      render(); // без активного запуска — просто пустая страница
    }
  }

  function setStage(key) {
    st.stage = key;
    // B5: кэш опций (файлы/главы) — только для текущей стадии:
    // после запуска ner появился ner.json, wiki должна его увидеть
    st.options = null;
    // значения формы — тоже по стадиям: переключение стадии = свежая
    // форма (внутри стадии значения общие для обоих режимов)
    st.values[key] = null;
    st.touched[key] = null;
    st.preview = null; // epub: свежий предпросмотр для новой стадии
    st.previewDirty = true;
    st.brPreview = null; // batch_replace: предпросмотр замен в главе
    st.brChapter = null;
    st.brSig = "";
    // активный запуск/стрим НЕ сбрасываем — лог привязан к своей
    // стадии и виден на её вкладке, чужие вкладки его не показывают
    render();
  }

  async function render() {
    const gen = ++st.gen;
    page.replaceChildren();
    const header = h(
      "div",
      { class: "page-header" },
      h(
        "div",
        { class: "page-header-main" },
        h("h1", { class: "page-title" }, "Запуски"),
        h(
          "div",
          { class: "page-sub" },
          `${section}/${name} · стадии пайплайна`,
        ),
      ),
      h(
        "a",
        { class: "btn btn-sm btn-ghost", href: `#/project/${section}/${name}` },
        "← Файлы",
      ),
    );
    const body = await runBody();
    if (gen !== st.gen) return; // устаревший рендер — не рисовать
    page.append(header, body);
  }

  async function runBody() {
    // список стадий
    const stages = await api("/stages").catch(() => ({ stages: [] }));
    const cards = stages.stages.map((s) =>
      h(
        "button",
        {
          class:
            "stage-card" + (st.stage === s.key ? " stage-card-active" : ""),
          onclick: () => setStage(s.key),
        },
        h("div", { class: "stage-card-title" }, s.title),
      ),
    );
    const stageList = h(
      "div",
      { class: "run-col run-col-stages" },
      h("div", { class: "run-panel-title" }, "Стадии"),
      h(
        "div",
        { class: "stage-grid" },
        cards.length ? cards : [h("div", { class: "empty" }, "Нет стадий")],
      ),
      await activePanel(),
    );

    // правая панель: форма + лог. Лог — ТОЛЬКО активного (running)
    // запуска и только на вкладке его стадии; по завершению запуска
    // панель закрывается. История — на «Дашборде» и во вкладке «Логи»
    const right = h(
      "div",
      { class: "run-col run-col-form" },
      st.stage ? await formPanel() : emptyRun(),
      st.job &&
      st.job.status === "running" &&
      st.job.action === st.stage
        ? logPanel()
        : h(
            "div",
            { class: "run-empty" },
            h("div", { text: "Запустите стадию — лог появится здесь" }),
            h(
              "div",
              { class: "run-empty-hint" },
              "История запусков — ",
              h("a", { href: "#/dashboard" }, "на Дашборде"),
              " · логи — ",
              h(
                "a",
                { href: `#/project/${section}/${name}/logs` },
                "во вкладке «Логи»",
              ),
            ),
          ),
    );
    return h("div", { class: "run-layout" }, stageList, right);
  }

  // карточка активного запуска ВМЕСТО истории запусков
  async function activePanel() {
    const panel = h("div", { class: "run-panel" });
    panel.append(h("div", { class: "run-panel-title" }, "Активный запуск"));
    let j = st.job && st.job.status === "running" ? st.job : null;
    if (!j) {
      try {
        const r = await api("/jobs");
        j = (r.jobs || []).find(
          (x) => x.status === "running" && x.project === `${section}/${name}`,
        );
      } catch {
        j = null;
      }
    }
    if (!j) {
      panel.append(h("div", { class: "empty" }, "Нет активного запуска"));
      return panel;
    }
    const bar = miniBar(j.progress, j.status);
    const row = h(
      "div",
      { class: "job-row" },
      h("span", { class: "badge badge-" + j.status }, j.status),
      h("span", { class: "job-title" }, j.title || j.action || "запуск"),
      h(
        "span",
        { class: "job-time" },
        new Date(j.created * 1000).toLocaleString("ru-RU"),
      ),
      h(
        "a",
        {
          class: "btn btn-sm btn-primary",
          // id в URL (не голый #/run/…): повторный клик по той же
          // странице перечитает запуск и обновит висящий статус
          href: `#/run/${section}/${name}/${j.id}`,
        },
        "Показать",
      ),
      h(
        "button",
        {
          class: "btn btn-sm btn-danger",
          onclick: async () => {
            try {
              await api(`/jobs/${j.id}/stop`, { method: "POST" });
              toast("Остановка...");
            } catch (ex) {
              toast(ex.message, "err");
            }
          },
        },
        "Стоп",
      ),
    );
    if (bar) row.append(bar);
    panel.append(row);
    return panel;
  }

  // текст мини-бара активного запуска (общий для отрисовки и SSE)
  function miniProgressText(p) {
    if (!p) return "ожидание…";
    const total = p.total ? p.total : 0;
    return (p.label || "") + (total > 0 ? ` ${p.done}/${p.total}` : "");
  }

  function miniBar(p, status) {
    // у running-задачи без событий прогресса бар всё равно
    // виден («ожидание…») — раньше возвращался null и виджет молчал
    if (!p && status !== "running") return null;
    const total = p && p.total ? p.total : 0;
    const done = p ? p.done : 0;
    const pct = UICore.progressPct(done, total);
    return h(
      "div",
      { class: "progress-wrap mini" },
      h(
        "div",
        { class: "progress-track" },
        h("div", { class: "progress-fill", style: "width:" + pct + "%" }),
      ),
      h("span", { class: "progress-text" }, miniProgressText(p)),
    );
  }

  // живое обновление мини-бара из SSE (fill + текст)
  function paintMini(bar) {
    const p = st.progress;
    const total = p && p.total ? p.total : 0;
    const done = p ? p.done : 0;
    const pct = UICore.progressPct(done, total);
    const fill = bar.querySelector(".progress-fill");
    if (fill) fill.style.width = pct + "%";
    const text = bar.querySelector(".progress-text");
    if (text) text.textContent = miniProgressText(p);
  }

  function emptyRun() {
    return h(
      "div",
      { class: "run-empty" },
      "Выберите стадию слева — откроется форма запуска",
    );
  }

  async function formPanel() {
    const key = st.stage;
    let spec;
    try {
      const r = await api(`/stages/${key}/spec?project=${section}/${name}`);
      spec = r.spec;
    } catch (ex) {
      return h("div", { class: "run-empty" }, ex.message);
    }
    // опции перечитываем при КАЖДОМ рендере формы: файлы могли
    // поменяться на диске вне интерфейса (переименование epub/zip,
    // загрузка), а селект исходника должен видеть свежий список
    try {
      const r = await api(
        `/stages/${key}/options?project=${section}/${name}`,
      );
      st.options = r.options || {};
    } catch {
      st.options = {};
    }
    // синхронизация режимов: значения формы — общие (st.values),
    // инициализация один раз на выбор стадии (дефолты + .env-префилл)
    if (!st.values[key]) initFormValues(key, spec);
    // «Простой режим» — только для стадий с пресетом (spec.simple);
    // translate_check/batch_replace/compile — только экспертные,
    // переключатель не показываем
    const hasSimple = (spec.simple || []).length > 0;
    const mode = hasSimple ? UICore.runModeGet(key) : "expert";
    st.mode = mode; // buildField: epub-фильтр расширений по режиму
    const body =
      hasSimple && mode === "simple"
        ? simplePanel(key, spec)
        : expertForm(key, spec);
    const panel = h(
      "div",
      { class: "run-panel" },
      h("div", { class: "run-panel-title" }, `${key} · ${spec.title}`),
      hasSimple ? modeToggle(key, mode) : null,
      body,
    );
    // epub: панель предпросмотра разбивки — в обоих режимах
    if (key === "epub") panel.append(epubPreviewPanel(key, spec, mode));
    // batch_replace: панель предпросмотра замен по главам
    if (key === "batch_replace") {
      const br = batchReplacePreviewPanel(key);
      panel.append(br.el);
      br.mount(panel.querySelector(".run-form") || panel);
    }
    return panel;
  }

  // сегмент-переключатель «Простой режим / Экспертный»
  function modeToggle(key, mode) {
    const btn = (m, label) =>
      h(
        "button",
        {
          class: "mode-btn" + (mode === m ? " mode-btn-active" : ""),
          onclick: () => {
            UICore.runModeSet(key, m);
            if (key === "epub") st.previewDirty = true;
            render();
          },
        },
        label,
      );
    return h(
      "div",
      { class: "run-mode" },
      btn("simple", "Простой режим"),
      btn("expert", "Экспертный"),
    );
  }

  // ── синхронизация режимов: единое хранилище значений формы ──────────
  // st.values[stage] — значения полей (данные), st.touched[stage] —
  // имена полей, которые пользователь менял. Оба режима читают/пишут
  // один объект: переключение «Простой ↔ Экспертный» ничего не теряет,
  // тонкие правки из эксперта применяются в простом и наоборот.

  // ── epub: автосохранение настроек формы (localStorage, по проекту) ──
  // Настройки сохраняются сразу при вводе (без ожидания запуска) и
  // восстанавливаются поверх .env/дефолтов при входе на стадию.
  const EPUB_SAVE_KEY = "epubRunVals";
  function epubSave(key) {
    try {
      localStorage.setItem(
        `${EPUB_SAVE_KEY}:${section}/${name}`,
        JSON.stringify(st.values[key] || {}),
      );
    } catch {
      /* нет localStorage (приватный режим) — не критично */
    }
  }
  function epubLoad() {
    try {
      const raw = localStorage.getItem(`${EPUB_SAVE_KEY}:${section}/${name}`);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  // инициализация значений формы по спеке: дефолты (уже с .env-префиллом
  // сервера) + автоподхват диапазона глав и autofile-файлов
  function initFormValues(key, spec) {
    const opts = st.options || {};
    const vals = {};
    for (const f of spec.fields || []) {
      const def = f.default == null ? "" : String(f.default);
      if (f.type === "bool") {
        vals[f.name] = UICore.boolOn(def);
      } else if (f.type === "files") {
        // C: basename-сравнение с пулом опций (source/prompts/root)
        const dir = f.dir || "";
        const pool =
          dir === "source"
            ? opts.source || []
            : dir === "prompts"
              ? opts.prompts || []
              : opts.root || [];
        const exts = (f.ext || []).map((e) => e.toLowerCase());
        const items = pool.filter(
          (n) =>
            exts.length === 0 || exts.some((e) => n.toLowerCase().endsWith(e)),
        );
        // только реально существующие файлы (pickPoolFile): удалённый
        // промпт не остаётся «подхваченным» из .env-памяти и не ломает
        // автоподхват конвейера (pipeline_prompt.txt)
        vals[f.name] = UICore.pickPoolFile(def, items);
        // pipeline: «Общий промпт-файл» — автоподхват кандидата auto-режима
        // (первый файл с тегами из опций сервера); не перекрываем реальную
        // память из .env — только если выбор пуст
        if (f.name === "prompt_file" && opts.auto_prompt
            && vals[f.name] === "") {
          const auto = UICore.fileBase(opts.auto_prompt);
          if (items.includes(auto)) vals[f.name] = auto;
        }
      } else {
        vals[f.name] = def;
        // автозаполнение диапазона глав из опций (как в CLI)
        if ((f.name === "start" || f.name === "end") && vals[f.name] === "") {
          const ch = opts.chapters || {};
          const d = f.name === "start" ? ch.min : ch.max;
          if (d != null) vals[f.name] = String(d);
        }
        // autofile: автоподхват файла из пула (donate.txt и т.п.)
        if (f.autofile && vals[f.name] === "") {
          const slash = f.autofile.indexOf("/");
          const adir = slash >= 0 ? f.autofile.slice(0, slash) : "";
          const abase = slash >= 0 ? f.autofile.slice(slash + 1) : f.autofile;
          const apool =
            adir === "source"
              ? opts.source || []
              : adir === "prompts"
                ? opts.prompts || []
                : opts.root || [];
          if (apool.includes(abase)) vals[f.name] = f.autofile;
        }
      }
    }
    st.values[key] = vals;
    st.touched[key] = new Set();
    // epub: автосохранённые настройки поверх дефолтов/.env
    if (spec.autosave) {
      const saved = epubLoad();
      if (saved && typeof saved === "object") {
        for (const f of spec.fields || []) {
          if (saved[f.name] !== undefined) {
            vals[f.name] = saved[f.name];
            st.touched[key].add(f.name);
          }
        }
      }
    }
  }

  // файл → путь внутри проекта (R5-G): голое имя + dir → "dir/имя"
  function finalFile(f, v) {
    if (f.type === "files" && f.dir && v && !String(v).includes("/")) {
      return `${f.dir}/${v}`;
    }
    return v;
  }

  // params запуска: экспертный = все непустые значения формы (vals
  // уже включают дефолты + .env-префилл + автоподхваты диапазона/
  // autofile); простой = пресет (дефолты) + поля, которые пользователь
  // менял в ЛЮБОМ режиме (синхронизация режимов: правки из эксперта
  // применяются в простом и наоборот).
  function buildParams(key, spec, mode) {
    const vals = st.values[key] || {};
    const touched = st.touched[key] || new Set();
    const p = {};
    if (mode === "simple") {
      Object.assign(p, (spec.preset || {}).params || {});
    }
    for (const f of spec.fields || []) {
      const v = vals[f.name];
      const use = mode === "expert" || touched.has(f.name);
      if (!use) continue;
      if (f.type === "bool") p[f.name] = Boolean(v);
      else if (v !== "" && v != null) p[f.name] = finalFile(f, v);
    }
    if (key === "epub") {
      // удалённые в предпросмотре секции: seq уходят в параметры
      // запуска, скрипт их пропускает и перенумеровывает
      p.skip = (st.preview && st.preview.skips) || [];
    }
    // ner_check · RAG: в простом режиме поля RAG-режима попадают
    // в параметры как в экспертном (когда выбран режим rag);
    // fields — выбранные чипсами поля записи (идут в RAG-промпт)
    if (key === "ner_check" && mode === "simple"
        && String(p["passes"] ?? "") === "rag") {
      for (const name of ["rag_terms", "rag_source_type",
                          "rag_budget", "fields"]) {
        const f = (spec.fields || []).find((x) => x.name === name);
        if (!f) continue;
        const v = vals[f.name];
        if (v !== "" && v != null) p[f.name] = finalFile(f, v);
      }
    }
    return p;
  }

  // общий построитель поля: label + input, привязанный к st.values[key]
  // (оба режима). Возвращает label-обёртку; сам input — в wrap._input
  // (у files — row, select внутри row._sel).
  function buildField(key, f) {
    if (f.type === "hidden") return null; // параметры виджетов (чипсы)
    const vals = st.values[key];
    const touched = st.touched[key];
    const label = h("div", { class: "field-label" }, f.label);
    let input;
    if (f.type === "bool") {
      input = h("input", { type: "checkbox", class: "checkbox" });
      input.checked = Boolean(vals[f.name]);
      input.addEventListener("change", () => {
        vals[f.name] = input.checked;
        touched.add(f.name);
      });
      // чекбокс СЛЕВА от текста (не снизу): строка checkbox + label;
      // подсказка — тултип при наведении (M9), чтобы не ломала строку
      const wrap = h(
        "label",
        { class: "field field-check" },
        input,
        label,
      );
      if (f.help) attachTooltip(wrap, f.help);
      wrap._input = input;
      if (key === "epub") wireEpubAutosave(wrap);
      return wrap;
    } else if (f.type === "select") {
      input = h("select", { class: "input" });
      const labels = f.labels || {};
      for (const o of f.options || []) {
        const text = labels[o] == null ? (o === "" ? "—" : o) : labels[o];
        input.append(h("option", { value: o }, text));
      }
      input.value = String(vals[f.name] ?? f.default ?? "");
      input.addEventListener("change", () => {
        vals[f.name] = input.value;
        touched.add(f.name);
      });
    } else if (f.type === "textarea") {
      // epub: многострочные regexp (по одному паттерну на строку)
      input = h("textarea", {
        class: "input epub-textarea",
        rows: f.rows || 3,
        text: String(vals[f.name] ?? f.default ?? ""),
      });
      input.addEventListener("input", () => {
        vals[f.name] = input.value;
        touched.add(f.name);
      });
    } else if (f.type === "files") {
      input = h("select", { class: "input" });
      const dir = f.dir || "";
      const pool =
        dir === "source"
          ? st.options.source || []
          : dir === "prompts"
            ? st.options.prompts || []
            : st.options.root || [];
      let exts = (f.ext || []).map((e) => e.toLowerCase());
      // epub: расширения зависят от режима — toc: только epub;
      // regex/chunk: epub + txt; простой режим всегда toc (пресет);
      // zip не принимается ни в одном режиме
      if (key === "epub" && f.name === "input") {
        const m =
          st.mode === "simple" ? "toc" : String(vals["mode"] || "toc");
        exts = m === "toc" ? [".epub"] : [".epub", ".txt"];
      }
      const items = pool.filter(
        (n) =>
          exts.length === 0 || exts.some((e) => n.toLowerCase().endsWith(e)),
      );
      input.append(h("option", { value: "" }, "—"));
      for (const n of items) {
        input.append(h("option", { value: n }, n));
      }
      let chosen = String(vals[f.name] ?? "");
      if (chosen !== "" && !items.includes(chosen)) {
        // файл исчез из пула (удалён) — сбрасываем «мёртвый» выбор
        chosen = "";
        vals[f.name] = "";
      }
      input.value = chosen;
      // НЕ ссылаться на переменную input: ниже она переприсваивается
      // на row (select прячется в row._sel) — замыкание поймало бы div
      const sel = input;
      input.addEventListener("change", () => {
        vals[f.name] = sel.value;
        touched.add(f.name);
      });
      // загрузка своего файла / редактирование выбранного (промпты)
      const row = h("div", { class: "field-row" }, input);
      const upInput = h("input", { type: "file", class: "hidden" });
      // промпт-файлы (dir=prompts, .txt): выбранный промпт редактируется
      // прямо из запуска («Редактировать» вместо «Загрузить»)
      const isPrompt = dir === "prompts"
        && (f.ext || []).includes(".txt");
      const upBtn = h(
        "button",
        { class: "btn btn-sm btn-ghost" },
        "Загрузить",
      );
      function refreshPromptBtn() {
        if (!isPrompt) {
          upBtn.textContent = "Загрузить";
          return;
        }
        upBtn.textContent = sel.value ? "Редактировать" : "Загрузить";
      }
      upBtn.addEventListener("click", () => {
        if (isPrompt && sel.value) {
          editPromptModal(sel.value);
        } else {
          upInput.click();
        }
      });
      sel.addEventListener("change", refreshPromptBtn);
      upInput.addEventListener("change", async () => {
        if (!upInput.files || !upInput.files.length) return;
        const form = new FormData();
        // B3: dir="" (поля корня проекта — ner_file, wiki file) —
        // загружаем в корень проекта, иначе файл не появится в селекте
        form.append("dest", dir || "");
        for (const f2 of upInput.files) {
          form.append("files[]", f2, f2.name);
        }
        try {
          const r = await apiUpload(
            `/upload?project=${section}/${name}`,
            form,
          );
          toast(`Загружено: ${r.saved.length} файл(ов)`);
          st.options = null; // перечитать список файлов
          render();
        } catch (ex) {
          toast(ex.message, "err");
        }
      });
      row.append(upBtn, upInput);
      refreshPromptBtn();
      row._sel = input; // значение — select внутри row
      input = row;
    } else {
      input = h("input", {
        type:
          f.type === "number"
            ? "number"
            : f.type === "password"
              ? "password"
              : "text",
        class: "input",
      });
      // min/max из spec — браузерный стоп-механизм (стрелки/клавиши)
      if (f.min != null) input.min = String(f.min);
      if (f.max != null) input.max = String(f.max);
      input.value = String(vals[f.name] ?? f.default ?? "");
      input.addEventListener("input", () => {
        vals[f.name] = input.value;
        touched.add(f.name);
      });
    }
    const wrap = h("label", { class: "field" }, label, input);
    // M9: сложные контролы (select/textarea/files) — тултип при наведении;
    // inline-подсказка остаётся только у простых text/number
    if (f.help) {
      const complex = f.type === "select" || f.type === "textarea"
        || f.type === "files";
      if (complex) {
        attachTooltip(wrap, f.help);
      } else {
        wrap.append(h("div", { class: "field-help" }, f.help));
      }
    }
    wrap._input = input;
    if (key === "epub") wireEpubAutosave(wrap);
    return wrap;
  }

  // epub: автосохранение настроек при вводе + пометка «предпросмотр
  // устарел». input/change всплывают от вложенного элемента к label.
  function wireEpubAutosave(wrap) {
    const onCh = () => {
      epubSave(st.stage);
      st.previewDirty = true;
    };
    wrap.addEventListener("input", onCh);
    wrap.addEventListener("change", onCh);
  }

  // ── ner_check: чипсы типов из глоссария (вместо select/text) ──
  // Общие для простого и экспертного режимов: чипсы грузят типы из
  // глоссария (GET /api/ner); выбор проходов — обычный select «Проходы».
  // Возвращает {chipsBar, chipsBox, guide, loadTypes}.
  function nerCheckWidgets(key) {

    const chipsBar = h("div", { class: "ner-chips-bar" });
    const chipsInfo = h("div", { class: "field-help" });
    const selAll = h(
      "button",
      { class: "btn btn-xs btn-ghost", type: "button" },
      "Выбрать все",
    );
    const selNone = h(
      "button",
      { class: "btn btn-xs btn-ghost", type: "button" },
      "Снять все",
    );
    const chipsBox = h("div", { class: "ner-chips-box" });
    let typeNames = [];
    let typeCounts = {};
    // null = все типы (пустое значение), список — выбранные
    function curTypes() {
      const v = String(st.values[key]["types"] ?? "");
      return v
        ? v.split(",").map((s) => s.trim()).filter(Boolean)
        : null;
    }
    function renderChips() {
      const cur = curTypes();
      chipsBox.replaceChildren();
      for (const t of typeNames) {
        const cb = h("input", { type: "checkbox", class: "checkbox" });
        cb.checked = cur == null || cur.includes(t);
        cb.addEventListener("change", () => {
          const set = new Set(cur == null ? typeNames : cur);
          if (cb.checked) {
            set.add(t);
            st.values[key]["types"] =
              set.size === typeNames.length ? "" : [...set].join(",");
          } else if (set.size > 1) {
            set.delete(t);
            st.values[key]["types"] = [...set].join(",");
          } else {
            cb.checked = true; // минимум один тип (как в глоссарии)
          }
          st.touched[key].add("types");
          renderChips();
        });
        const n = typeCounts[t];
        chipsBox.append(
          h(
            "label",
            { class: "ner-chip" },
            cb,
            ` ${t}` + (n == null ? "" : ` (${n})`),
          ),
        );
      }
    }
    selAll.addEventListener("click", () => {
      st.values[key]["types"] = "";
      st.touched[key].add("types");
      renderChips();
    });
    selNone.addEventListener("click", () => {
      if (typeNames.length) {
        // минимум один тип (как в глоссарии) — снять все нельзя
        st.values[key]["types"] = typeNames[0];
        st.touched[key].add("types");
        renderChips();
      }
    });
    chipsBar.append(
      selAll,
      selNone,
      h("span", { class: "spacer" }),
      chipsInfo,
    );

    // ── поля записи, передаваемые LLM ──
    // чипсы — ключи полей из реальных данных ner.json: известные в
    // привычном порядке, ЛЮБОЙ новый ключ добавляется в конец сам;
    // «term» — всегда в запросе (чекбокс неотключаемый)
    const FIELD_ORDER = ["type", "translation", "pinyin", "reading",
      "context", "translated_context", "notes", "aliases"];
    const fieldsBar = h("div", { class: "ner-chips-bar" });
    const fieldsInfo = h("div", { class: "field-help" });
    const fieldsBox = h("div", { class: "ner-chips-box" });
    let fieldNames = [];
    function curFields() {
      let v = String(st.values[key]["fields"] ?? "").trim();
      if (!v) {
        // дефолт: term + type/translation/notes/context — те, что есть
        // в реальных данных ner.json (fieldNames уже загружен)
        const base = ["type", "translation", "notes", "context"].filter(
          (n) => fieldNames.includes(n),
        );
        v = ["term", ...base].join(",");
        st.values[key]["fields"] = v;
      }
      const set = new Set(v.split(",").map((s) => s.trim())
        .filter((s) => s && s !== "term"));
      set.add("term"); // термин — всегда в запросе
      return set;
    }
    function renderFields() {
      const cur = curFields();
      fieldsBox.replaceChildren();
      for (const name of ["term", ...fieldNames]) {
        const isTerm = name === "term";
        const cb = h("input", { type: "checkbox", class: "checkbox" });
        cb.checked = isTerm || cur.has(name);
        cb.disabled = isTerm; // неотключаемый
        if (!isTerm) {
          cb.addEventListener("change", () => {
            const set = new Set(curFields());
            if (cb.checked) set.add(name);
            else set.delete(name); // минимум — «Термин» (неотключаемый)
            st.values[key]["fields"] = [...set].join(",");
            st.touched[key].add("fields");
            renderFields();
          });
        }
        fieldsBox.append(
          h(
            "label",
            { class: "ner-chip" + (isTerm ? " ner-chip-term" : "") },
            cb,
            ` ${name}` + (isTerm ? " (всегда)" : ""),
          ),
        );
      }
    }
    const fieldsAll = h(
      "button",
      { class: "btn btn-xs btn-ghost", type: "button" },
      "Выбрать все",
    );
    fieldsAll.addEventListener("click", () => {
      if (fieldNames.length) {
        st.values[key]["fields"] = ["term", ...fieldNames].join(",");
        st.touched[key].add("fields");
        renderFields();
      }
    });
    fieldsBar.append(
      h("span", {}, "Поля в запросе LLM:"),
      fieldsAll,
      h("span", { class: "spacer" }),
      fieldsInfo,
    );

    // подсказка-степпер: правильный порядок проверки и применения
    const guide = h(
      "details",
      { class: "regexp-help ner-guide" },
      h("summary", {}, "Как пользоваться проверкой"),
      h(
        "div",
        { class: "regexp-help-body" },
        h("ol", {},
          h("li", {}, "Запустите проверку — правки LLM запишутся в ",
            h("code", {}, "ner_review.json"), " (к тексту не применяются)"),
          h("li", {}, "Во вкладке «Проверки» отредактируйте правки: ",
            "принять / отклонить"),
          h("li", {}, "Нажмите «Применить» — принятые правки попадут в ",
            h("code", {}, "ner.json"))),
      ),
    );
    // загрузка типов из глоссария — асинхронно, после вставки в DOM
    const loadTypes = async () => {
      chipsInfo.textContent = "Загрузка типов из глоссария…";
      try {
        const d = await api(`/ner?project=${section}/${name}`);
        const byType = d.by_type || {};
        typeNames = Object.keys(byType).sort();
        typeCounts = byType;
        if (!typeNames.length) {
          chipsInfo.textContent =
            "Глоссарий пуст — сначала создайте его стадией «Создание глоссария»";
          return;
        }
        const cur = curTypes();
        if (cur) {
          // устаревшие/удалённые типы не остаются выбранными
          const ok = cur.filter((t) => typeNames.includes(t));
          st.values[key]["types"] =
            ok.length === typeNames.length ? "" : ok.join(",");
          if (!ok.length) st.values[key]["types"] = typeNames[0];
        }
        chipsInfo.textContent =
          `${typeNames.length} тип(ов) в глоссарии · пусто = все`;
        renderChips();
        // поля записей — из реальных ключей ner.json (динамически)
        const present = new Set();
        for (const it of d.items || []) {
          if (!it || typeof it !== "object") continue;
          for (const k of Object.keys(it)) {
            if (!k.startsWith("_")) present.add(k);
          }
        }
        // поля — ключи из реальных данных ner.json: известные идут в
        // привычном порядке, ЛЮБОЙ новый ключ добавляется в конец
        fieldNames = [
          ...FIELD_ORDER.filter(
            (n) => n === "type" || n === "translation" || present.has(n),
          ),
          ...[...present]
            .filter((k) => !FIELD_ORDER.includes(k) && k !== "term")
            .sort(),
        ];
        fieldsInfo.textContent =
          "term — всегда; по умолчанию: type, translation, notes, "
          + "context (если есть в данных)";
        renderFields();
      } catch (ex) {
        chipsInfo.textContent = ex.message;
      }
    };
    return { chipsBar, chipsBox, guide, loadTypes, fieldsBar, fieldsBox };
  }

  // «Редактировать» промпт из запуска: чтение/правка/сохранение
  // выбранного файла prompts/ (GET/PUT /api/prompts/{name})
  function editPromptModal(fileName) {
    const err = h("div", { class: "form-error" });
    const host = h("div", { class: "editor-modal-body" });
    const modal = h(
      "div",
      { class: "modal-backdrop", onclick: (e) => e.target === modal && close() },
      h(
        "div", { class: "modal modal-wide" },
        h("div", { class: "modal-title" }, `Промпт: ${fileName}`),
        host,
        err,
        h(
          "div", { class: "modal-actions" },
          h("button", { class: "btn btn-ghost", onclick: close }, "Отмена"),
          h(
            "button",
            {
              class: "btn btn-primary",
              onclick: async () => {
                err.textContent = "";
                if (!ed) {
                  err.textContent = "Редактор ещё не загружен";
                  return;
                }
                try {
                  await api(`/prompts/${encodeURIComponent(fileName)}`, {
                    method: "PUT",
                    body: { project: `${section}/${name}`,
                            content: ed.getValue() },
                  });
                  toast(`Сохранено: ${fileName}`);
                  close();
                } catch (ex) {
                  err.textContent = ex.message;
                }
              },
            },
            "Сохранить",
          ),
        ),
      ),
    );
    let ed = null;
    (async () => {
      try {
        const d = await api(
          `/prompts/${encodeURIComponent(fileName)}?project=${section}/${name}`,
        );
        // makeEditor/extOf — из app.js (глобальные; к моменту клика
        // уже загружены)
        ed = makeEditor(d.content || "", extOf(fileName));
        host.append(h("div", { class: "editor-cm" }, ed.root));
      } catch (ex) {
        err.textContent = ex.message;
      }
    })();
    function close() {
      modal.remove();
    }
    document.body.append(modal);
  }

  // «Добавить спорные» (RAG): модалка с настройками (поле голосов,
  // типы, коэффициент, порог count) → спорные термины из ner.json
  // добавляются в поле rag_terms построчно. Спорно, если max/второй
  // голос < коэффициента (по выбранному полю _votes_xxx).
  //
  // ВНИМАНИЕ: _votes_* в ner.json — ОБЪЕКТ {вариант: голоса}, а не
  // массив (напр. {"Анлэ": 173, "Аньлэ": 149, ...}); поэтому берём
  // Object.values и сортируем по убыванию.
  function addDisputedTermsModal(key, textareaWrap) {
    const err = h("div", { class: "form-error" });
    const fieldSel = h("select", { class: "input" },
      h("option", { value: "" }, "Все поля голосов"));
    const typesBox = h("div", { class: "ner-chips-box" });
    const typesInfo = h("div", { class: "field-help" });
    let typeNames = [];
    let selTypes = null; // null = все выбранные
    let items = []; // все записи ner.json (для пересчёта счётчиков)
    const ratioInp = h("input", {
      class: "input", type: "number", min: "0.01", step: "0.01",
      value: "1.5",
    });
    const countInp = h("input", {
      class: "input", type: "number", min: "0", value: "0",
    });
    const help = h(
      "div", { class: "field-help" },
      "Поле голосов — спорность считается по выбранному полю ",
      "_votes_xxx (или по любому). Спорно, если разрыв max/2-й МЕНЬШЕ ",
      "коэффициента (напр. 173/149 = 1.16 < 1.5). Порог count — только ",
      "записи с count > X (0 = без порога).",
    );
    // спорность одной записи по выбранному полю (или любому _votes_*)
    function isDisputed(it, ratio, threshold) {
      if (threshold > 0 && !(Number(it.count) > threshold)) return false;
      const field = fieldSel.value;
      const keys = field
        ? [field]
        : Object.keys(it).filter((k) => k.startsWith("_votes_"));
      for (const k of keys) {
        const v = it[k];
        if (!v || typeof v !== "object") continue;
        const nums = Object.values(v)
          .map(Number)
          .filter((n) => Number.isFinite(n) && n > 0);
        if (nums.length < 2) continue;
        nums.sort((a, b) => b - a);
        if (nums[0] / nums[1] < ratio) return true;
      }
      return false;
    }
    function disputedCountFor(type) {
      const ratio = Number(ratioInp.value);
      if (!Number.isFinite(ratio) || ratio <= 0) return 0;
      const threshold = Math.max(0, Number(countInp.value) || 0);
      let n = 0;
      for (const it of items) {
        if (it.type !== type) continue;
        if (isDisputed(it, ratio, threshold)) n++;
      }
      return n;
    }
    function renderTypeChips() {
      typesBox.replaceChildren();
      for (const t of typeNames) {
        const cb = h("input", { type: "checkbox", class: "checkbox" });
        cb.checked = selTypes == null || selTypes.includes(t);
        cb.addEventListener("change", () => {
          const set = new Set(selTypes == null ? typeNames : selTypes);
          if (cb.checked) {
            set.add(t);
            selTypes = set.size === typeNames.length ? null : [...set];
          } else if (set.size > 1) {
            set.delete(t);
            selTypes = [...set];
          } else {
            cb.checked = true; // минимум один тип
          }
          renderTypeChips();
        });
        const n = disputedCountFor(t);
        typesBox.append(
          h("label", { class: "ner-chip" }, cb, ` ${t} (${n})`),
        );
      }
    }
    const selAll = h("button", { class: "btn btn-xs btn-ghost" },
      "Выбрать все");
    selAll.addEventListener("click", () => {
      selTypes = null;
      renderTypeChips();
    });
    const selNone = h("button", { class: "btn btn-xs btn-ghost" },
      "Снять все");
    selNone.addEventListener("click", () => {
      if (typeNames.length) {
        selTypes = [typeNames[0]]; // минимум один тип
        renderTypeChips();
      }
    });
    const modal = h(
      "div",
      { class: "modal-backdrop", onclick: (e) => e.target === modal && close() },
      h(
        "div", { class: "modal" },
        h("div", { class: "modal-title" }, "Добавить спорные термины"),
        h("div", { class: "modal-text" }, "Поле голосов:"),
        fieldSel,
        h("div", { class: "modal-text" }, "Типы:"),
        h("div", { class: "ner-chips-bar" }, selAll, selNone,
          h("span", { class: "spacer" }), typesInfo),
        typesBox,
        h("div", { class: "modal-text" }, "Коэффициент:"),
        ratioInp,
        h("div", { class: "modal-text" }, "Порог count:"),
        countInp,
        help,
        err,
        h(
          "div", { class: "modal-actions" },
          h("button", { class: "btn btn-ghost", onclick: close }, "Отмена"),
          h(
            "button",
            {
              class: "btn btn-primary",
              onclick: async () => {
                const ratio = Number(ratioInp.value);
                if (!Number.isFinite(ratio) || ratio <= 0) {
                  err.textContent = "Коэффициент — число больше 0";
                  return;
                }
                const threshold = Math.max(0, Number(countInp.value) || 0);
                const terms = new Set();
                for (const it of items) {
                  if (selTypes != null && !selTypes.includes(it.type)) {
                    continue;
                  }
                  if (isDisputed(it, ratio, threshold)) terms.add(it.term);
                }
                const cur = String(
                  st.values[key]["rag_terms"] ?? "").trim();
                const lines = cur ? cur.split(/\s*\n+/) : [];
                for (const t of terms) {
                  if (t && !lines.includes(t)) lines.push(t);
                }
                st.values[key]["rag_terms"] = lines.join("\n");
                st.touched[key].add("rag_terms");
                const ta = textareaWrap && textareaWrap.querySelector(
                  "textarea");
                if (ta) ta.value = lines.join("\n");
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
    // поля голосов и типы — из данных ner.json (асинхронно); данные
    // кешируем в items для живого пересчёта счётчиков
    api(`/ner?project=${section}/${name}`).then((d) => {
      items = d.items || [];
      const byType = d.by_type || {};
      typeNames = Object.keys(byType).sort();
      const seen = new Set();
      for (const it of items) {
        if (!it || typeof it !== "object") continue;
        for (const k of Object.keys(it)) {
          if (k.startsWith("_votes_") && !seen.has(k)) {
            seen.add(k);
            fieldSel.append(h("option", { value: k }, k));
          }
        }
      }
      typesInfo.textContent =
        typeNames.length
          ? `${typeNames.length} тип(ов) · пусто = все`
          : "Глоссарий пуст";
      renderTypeChips();
    }).catch(() => {});
    // живой пересчёт счётчиков при изменении параметров
    ratioInp.addEventListener("input", renderTypeChips);
    countInp.addEventListener("input", renderTypeChips);
    fieldSel.addEventListener("change", renderTypeChips);
    document.body.append(modal);
  }

  // ── диапазон глав: ЕДИНАЯ строка «Главы: [start] – [end]» ────────────
  // для обоих режимов (простой/экспертный); значения пишутся в
  // st.values[key].start/end как и раньше (buildParams их собирает).
  // Возвращает null, если стадия не принимает start/end.
  function buildRangeRow(key, spec) {
    const hasRange = (spec.fields || []).some(
      (f) => f.name === "start" || f.name === "end",
    );
    if (!hasRange) return null;
    const start = h("input", {
      type: "number",
      class: "input preset-range",
    });
    const end = h("input", {
      type: "number",
      class: "input preset-range",
    });
    start.value = String(st.values[key]["start"] ?? "");
    end.value = String(st.values[key]["end"] ?? "");
    start.addEventListener("input", () => {
      st.values[key]["start"] = start.value;
      st.touched[key].add("start");
    });
    end.addEventListener("input", () => {
      st.values[key]["end"] = end.value;
      st.touched[key].add("end");
    });
    const rowEl = h(
      "div",
      { class: "preset-range-row" },
      h("span", { class: "preset-range-label" }, "Главы:"),
      start,
      h("span", { class: "preset-range-sep" }, "–"),
      end,
    );
    // ссылки на инпуты — для внешних панелей (предпросмотр замен
    // batch_replace перечитывает диапазон при вводе)
    rowEl._start = start;
    rowEl._end = end;
    st.rangeRow = rowEl;
    return rowEl;
  }

  // translate_quality: «Конечная глава» — бюджет. Простой режим
  // (auto): end readonly, считается интерактивно из start + budget +
  // тип + промпт (tree — размеры глав). Экспертный (manual): end
  // редактируемый; введённая глава остаётся, если влезает в бюджет,
  // иначе пересчитывается до последней влезающей.
  function attachQualityRange(key, inputs, mode) {
    if (key !== "translate_quality") return;
    const row = st.rangeRow;
    if (!row || !row._start || !row._end) return;
    const end = row._end;
    const auto = mode !== "manual";
    // инпуты: в экспертном режиме передаются обёртки .field (wrap),
    // в простом — уже инпуты; у files-полей select спрятан в _sel
    const fieldInput = (x) => {
      if (!x) return null;
      const inp = x._input || x;
      return (inp && inp._sel) || inp;
    };
    const budgetIn = fieldInput(inputs.budget);
    const typeSel = fieldInput(inputs.type);
    let tree = null;
    // последняя глава, помещающаяся в бюджет от start
    const lastFit = async (start) => {
      if (!tree) {
        try {
          const d = await api(`/projects/${section}/${name}/tree`);
          tree = d.chapters || [];
        } catch {
          tree = [];
        }
      }
      const raw =
        budgetIn && budgetIn.value !== "" ? budgetIn.value : "200000";
      const budget = parseInt(raw, 10) || 0;
      const type = (typeSel && typeSel.value) || "polished";
      return qualityEndByBudget(tree, start, budget, type);
    };
    const recalc = async () => {
      const start = parseInt(row._start.value || "", 10) || 0;
      const last = await lastFit(start);
      if (auto) {
        end.value = last;
        st.values[key]["end"] = last;
        st.touched[key].add("end");
        if (!start) end.title = "введите начальную главу";
        else if (last) end.title = "рассчитано по бюджету";
        else end.title =
          "в бюджет не влезает ни одна глава — увеличьте бюджет";
        return;
      }
      const cur = parseInt(end.value || "", 10) || 0;
      if (cur && last && cur > last) {
        // ручная глава не влезает — пересчёт до последней влезающей
        end.value = last;
        st.values[key]["end"] = last;
        st.touched[key].add("end");
        end.title = `не влезает в бюджет — обрезано до главы ${last}`;
      } else if (cur) {
        end.title = "вручную (в бюджет влезает)";
      } else {
        end.title = "пусто = до последней главы (бюджет обрежет)";
      }
    };
    if (auto) {
      end.setAttribute("readonly", "");
      end.classList.add("range-auto");
    }
    row._start.addEventListener("input", recalc);
    if (budgetIn) budgetIn.addEventListener("input", recalc);
    if (typeSel) typeSel.addEventListener("change", recalc);
    if (!auto) end.addEventListener("input", recalc);
    recalc();
  }

  // карточка пресета + простые поля (spec.simple) + диапазон глав:
  // простой режим — «частично показанный экспертный», значения общие
  function simplePanel(key, spec) {
    const preset = spec.preset || {};
    const err = h("div", { class: "form-error" });
    const card = h(
      "div",
      { class: "preset-card" },
      h("div", { class: "preset-title" }, preset.title || "Запуск"),
      h("div", { class: "preset-desc" }, preset.desc || ""),
    );
    const wraps = [];
    let ragApply = null; // ner_check RAG: видимость RAG-полей/диапазона
    const byName = {};
    for (const name of spec.simple || []) {
      // start/end отдельными полями не рисуем: единая строка
      // «Главы: [start] – [end]» (buildRangeRow) идёт первой
      if (name === "start" || name === "end") continue;
      const f = (spec.fields || []).find((x) => x.name === name);
      if (!f) continue;
      const wrap = buildField(key, f);
      if (!wrap) continue; // hidden-поля (чипсы) в простом списке не нужны
      wraps.push(wrap);
      // files-поля: настоящий select спрятан в row._sel (row — обёртка
      // с кнопкой «Загрузить»); без этого srcSel.value/fileSel.value
      // всегда undefined и условная видимость полей не работает
      byName[name] = (wrap._input && wrap._input._sel) || wrap._input;
    }
    // ner: входной файл или сборка глав
    if (key === "ner" && byName["mode"]) {
      const modeSel = byName["mode"];
      const fileSel = byName["file"];
      const applyNerSimple = () => {
        const fw = fileSel && fileSel.closest
          ? fileSel.closest(".field") : null;
        if (fw) fw.classList.remove("hidden");
      };
      modeSel.addEventListener("change", applyNerSimple);
      if (fileSel) fileSel.addEventListener("change", applyNerSimple);
      applyNerSimple();
    }
    // ner_check: чипсы типов из глоссария после select «Проходы»;
    // RAG-поля — строятся всегда, видны только в режиме rag
    if (key === "ner_check" && byName["passes"]) {
      const sel = byName["passes"];
      const wrap = sel.closest ? sel.closest(".field") : null;
      const w = nerCheckWidgets(key);
      if (wrap) {
        const idx = wraps.indexOf(wrap);
        if (idx >= 0) {
          wraps.splice(idx + 1, 0, w.chipsBar, w.chipsBox, w.fieldsBar,
                       w.fieldsBox, w.guide);
        }
      }
      w.loadTypes();
      const ragWraps = [];
      // в RAG прячем только чипсы ТИПОВ: поля записи (fieldsBar) и
      // степпер остаются — выбранные поля уходят в RAG-промпт
      const ragHidden = [w.chipsBar, w.chipsBox];
      for (const name of ["rag_terms", "rag_source_type",
                          "rag_budget"]) {
        const f = (spec.fields || []).find((x) => x.name === name);
        if (!f) continue;
        const rw = buildField(key, f);
        if (!rw) continue;
        if (name === "rag_terms") {
          // кнопка «Добавить спорные» — под списком терминов
          const addBtn = h(
            "button",
            { class: "btn btn-xs btn-ghost", type: "button" },
            "Добавить спорные",
          );
          addBtn.addEventListener("click", () =>
            addDisputedTermsModal(key, rw));
          const row = h("div", { class: "ner-add-disputed" }, addBtn);
          rw.append(row);
        }
        ragWraps.push(rw);
        wraps.push(rw);
      }
      ragApply = () => {
        const isRag = sel.value === "rag";
        for (const rw of ragWraps) {
          rw.classList.toggle("hidden", !isRag);
        }
        for (const el of ragHidden) {
          el.classList.toggle("hidden", isRag);
        }
        const rr = st.rangeRow;
        if (rr) rr.classList.toggle("hidden", !isRag);
      };
      sel.addEventListener("change", ragApply);
    }

    // wiki: чипсы типов из глоссария (как в ner_check; пусто = все)
    if (key === "wiki") {
      const w = nerCheckWidgets(key);
      wraps.push(w.chipsBar, w.chipsBox);
      w.loadTypes();
    }
    // wiki: «Собрать из глав» — прячем входной txt (показываем тип);
    // «Сохранить как главу» — прячем формат, показываем тип файла
    if (key === "wiki" && byName["source"]) {
      const srcSel = byName["source"];
      const asChSel = byName["as_chapter"];
      const fmtSel = byName["format"];
      const stSel = byName["save_type"];
      const applyWikiSimple = () => {
        const chapters = srcSel.value === "chapters";
        const fw = byName["file"];
        const wrap = fw && fw.closest ? fw.closest(".field") : null;
        if (wrap) wrap.classList.toggle("hidden", chapters);
        const tw = byName["type"];
        const twrap = tw && tw.closest ? tw.closest(".field") : null;
        if (twrap) twrap.classList.toggle("hidden", !chapters);
        const asCh = !!(asChSel && asChSel.checked);
        const fwrap = fmtSel && fmtSel.closest
          ? fmtSel.closest(".field") : null;
        if (fwrap) fwrap.classList.toggle("hidden", asCh);
        const swrap = stSel && stSel.closest
          ? stSel.closest(".field") : null;
        if (swrap) swrap.classList.toggle("hidden", !asCh);
      };
      srcSel.addEventListener("change", applyWikiSimple);
      if (asChSel) asChSel.addEventListener("change", applyWikiSimple);
      applyWikiSimple();
    }
    // диапазон глав — единая строка «Главы: [start] – [end]»
    const rangeRow = buildRangeRow(key, spec);
    if (rangeRow) {
      // ner: диапазон нужен только когда входной файл НЕ выбран
      // (сборка глав в память)
      if (key === "ner" && byName["mode"]) {
        const modeSel = byName["mode"];
        const fileSel = byName["file"];
        const applyNerRange = () => {
          const noFile = !(fileSel && fileSel.value);
          rangeRow.classList.toggle("hidden", !noFile);
        };
        modeSel.addEventListener("change", applyNerRange);
        if (fileSel) fileSel.addEventListener("change", applyNerRange);
        applyNerRange();
      }
      // wiki: диапазон — только когда «Собрать из глав»
      if (key === "wiki" && byName["source"]) {
        const srcSel = byName["source"];
        const applyWikiRange = () => {
          rangeRow.classList.toggle("hidden", srcSel.value !== "chapters");
        };
        srcSel.addEventListener("change", applyWikiRange);
        applyWikiRange();
      }
    }
    const runBtn = h("button", { class: "btn btn-primary" }, "Запустить");
    runBtn.addEventListener("click", async () => {
      err.textContent = "";
      const verr = epubValidateInput(key, "simple")
        || numericFieldError(key, spec);
      if (verr) {
        err.textContent = verr;
        return;
      }
      try {
        const r = await api("/jobs", {
          method: "POST",
          body: {
            action: key,
            project: `${section}/${name}`,
            params: buildParams(key, spec, "simple"),
          },
        });
        st.job = r.job;
        st.log = [];
        st.events = [];
        st.progress = r.job.progress || null;
        await loadChapterState();
        await render(); // дождаться DOM лога, иначе attachStream не найдёт его
        attachStream(r.job.id);
      } catch (ex) {
        err.textContent = ex.message;
      }
    });
    // диапазон глав — ПЕРВЫМ в списке (сразу под карточкой пресета)
    const body = [card, ...wraps];
    if (rangeRow) body.splice(1, 0, rangeRow);
    // ner_check RAG: после постройки rangeRow — первая раскладка
    if (ragApply) ragApply();
    // translate_quality: end считает бюджет — простой режим (auto)
    attachQualityRange(key, byName, "auto");
    return h("div", { class: "run-form" }, body, err, runBtn);
  }

  // экспертная форма: все поля спеки (до простого режима).
  // НЕ async — formPanel вставляет результат как DOM-узел; async
  // вернул бы Promise (баг «[object Promise]» при переключении).
  function expertForm(key, spec) {
    const err = h("div", { class: "form-error" });
    const fieldNodes = [];
    const fieldWraps = {}; // name → label-обёртка (промпты pipeline, )
    for (const f of spec.fields || []) {
      // start/end — отдельными полями не рисуем: единая строка
      // «Главы: [start] – [end]» (buildRangeRow) идёт первой
      if (f.name === "start" || f.name === "end") continue;
      const wrap = buildField(key, f);
      if (!wrap) continue; // hidden-поля рисуют свои виджеты (чипсы)
      fieldNodes.push(wrap);
      fieldWraps[f.name] = wrap;
    }
    // диапазон глав — ПЕРВЫМ в списке полей (все стадии с start/end)
    const rangeRow = buildRangeRow(key, spec);
    if (rangeRow) fieldNodes.unshift(rangeRow);
    // translate_quality: ручной end с проверкой бюджета — экспертный
    attachQualityRange(key, fieldWraps, "manual");

    // pipeline — единый общий промпт-файл (теги translate/redact/polish),
    // режим промптов и отдельные файлы на стадию убраны

    // ner — входной файл или сборка глав в память (диапазон виден
    // только когда файл не выбран)
    if (key === "ner") {
      const modeSel = fieldWraps["mode"] && fieldWraps["mode"]._input;
      const fileSel =
        fieldWraps["file"] && fieldWraps["file"]._input
          && fieldWraps["file"]._input._sel;
      function applyNerMode() {
        const noFile = !(fileSel && fileSel.value);
        if (rangeRow) {
          rangeRow.classList.toggle("hidden", !noFile);
        }
      }
      if (modeSel) modeSel.addEventListener("change", applyNerMode);
      if (fileSel) fileSel.addEventListener("change", applyNerMode);
      applyNerMode();
    }

    // wiki — источник текста: «Готовый txt» ↔ «Собрать из глав»;
    // формат: обычный/rulate-md/rulate-html (toc/toc_links — только
    // в обычном режиме)
    if (key === "wiki") {
      // чипсы типов из глоссария (как в ner_check; пусто = все типы)
      const w = nerCheckWidgets(key);
      const nerWrap = fieldWraps["ner_file"];
      const nidx = nerWrap ? fieldNodes.indexOf(nerWrap) : -1;
      if (nidx >= 0) {
        fieldNodes.splice(nidx + 1, 0, w.chipsBar, w.chipsBox);
      }
      w.loadTypes();
      const srcSel = fieldWraps["source"] && fieldWraps["source"]._input;
      const fmtSel = fieldWraps["format"] && fieldWraps["format"]._input;
      const asChSel =
        fieldWraps["as_chapter"] && fieldWraps["as_chapter"]._input;
      const tocFields = ["toc", "toc_links"];
      const fileOnlyFields = ["output", "format", "toc", "toc_links"];
      function applyWikiMode() {
        const src = (srcSel && srcSel.value) || "txt";
        const fmt = (fmtSel && fmtSel.value) || "md";
        const asCh = !!(asChSel && asChSel.checked);
        const fw = fieldWraps["file"];
        if (fw) fw.classList.toggle("hidden", src === "chapters");
        const tw = fieldWraps["type"];
        if (tw) tw.classList.toggle("hidden", src !== "chapters");
        // диапазон — только при «Собрать из глав»
        if (rangeRow) {
          rangeRow.classList.toggle("hidden", src !== "chapters");
        }
        const isMd = fmt === "md";
        for (const name of tocFields) {
          const w = fieldWraps[name];
          if (w) w.classList.toggle("hidden", !isMd || asCh);
        }
        // вики-глава: формат/выходной файл не нужны; тип файла — только
        // при включённой вики-главе
        for (const name of fileOnlyFields) {
          const w = fieldWraps[name];
          if (w) w.classList.toggle("hidden", asCh);
        }
        const sw = fieldWraps["save_type"];
        if (sw) sw.classList.toggle("hidden", !asCh);
      }
      if (srcSel) srcSel.addEventListener("change", applyWikiMode);
      if (fmtSel) fmtSel.addEventListener("change", applyWikiMode);
      if (asChSel) asChSel.addEventListener("change", applyWikiMode);
      applyWikiMode();
    }

    // ner_check — чипсы типов из глоссария после select «Проходы»;
    // карточки-пресеты убраны (названия — в select), степпер остаётся;
    // RAG-поля — только в режиме rag (+ кнопка «Добавить спорные»)
    if (key === "ner_check") {
      const passesWrap = fieldWraps["passes"];
      const w = nerCheckWidgets(key);
      if (passesWrap) {
        const idx = fieldNodes.indexOf(passesWrap);
        if (idx >= 0) {
          fieldNodes.splice(idx + 1, 0, w.chipsBar, w.chipsBox,
                           w.fieldsBar, w.fieldsBox, w.guide);
        }
      }
      w.loadTypes();
      const ragNames = ["rag_terms", "rag_source_type",
                        "rag_budget"];
      const ragWrap = fieldWraps["rag_terms"];
      if (ragWrap) {
        const addBtn = h(
          "button",
          { class: "btn btn-xs btn-ghost", type: "button" },
          "Добавить спорные",
        );
        addBtn.addEventListener("click", () =>
          addDisputedTermsModal(key, ragWrap));
        ragWrap.append(h("div", { class: "ner-add-disputed" }, addBtn));
      }
      const sel = passesWrap && passesWrap._input;
      // в RAG прячем только чипсы ТИПОВ: поля записи (fieldsBar) и
      // степпер остаются — выбранные поля уходят в RAG-промпт
      const ragHidden = [w.chipsBar, w.chipsBox];
      // RAG не использует: бюджет пакета, порог count, потоки
      // (RAG-запрос один, без батчей/типов/потоков)
      const ragNoUse = ["batch_size", "count_threshold", "threads"];
      const applyRagExpert = () => {
        const isRag = sel && sel.value === "rag";
        for (const name of ragNames) {
          const fw = fieldWraps[name];
          if (fw) fw.classList.toggle("hidden", !isRag);
        }
        for (const name of ragNoUse) {
          const fw = fieldWraps[name];
          if (fw) fw.classList.toggle("hidden", isRag);
        }
        for (const el of ragHidden) {
          el.classList.toggle("hidden", isRag);
        }
        if (rangeRow) rangeRow.classList.toggle("hidden", !isRag);
      };
      if (sel) sel.addEventListener("change", applyRagExpert);
      applyRagExpert();
    }

    // epub (экспертный): справка по regexp (collapsible) + перестройка
    // селекта исходника при смене режима (расширения зависят от режима)
    if (key === "epub") {
      const modeSel =
        fieldWraps["mode"] && fieldWraps["mode"]._input;
      const inputWrap = fieldWraps["input"];
      const help = h(
        "details",
        { class: "regexp-help" },
        h("summary", {}, "Справка по regexp"),
        h(
          "div",
          { class: "regexp-help-body" },
          h("p", {}, "Маркер — строка, НАЧИНАЮЩАЯСЯ с паттерна; ",
            "вся строка становится заголовком главы."),
          h("p", {}, "Разделители глав (режим regexp, по одному на строку):"),
          h("ul", {},
            h("li", {}, h("code", {}, "Глава \\d+"), " — «Глава 1», «Глава 22»"),
            h("li", {}, h("code", {}, "^第[0-9]+章"), " — «第1章», «第25章»"),
            h("li", {}, h("code", {}, "^第.章"), " — «第一章», «第二百三十五章»"),
            h("li", {}, h("code", {}, "^(Глава|Часть)\\s\\d+"),
              " — «Глава 3» или «Часть 5»"),
            h("li", {}, h("code", {}, "^\\d+\\.\\s*$"), " — «12.»"),
          ),
          h("p", {}, "Замены и очистки (все режимы, применяются ДО разбивки) — ",
            "по одной паре на строку: ",
            h("code", {}, "паттерн -> замена"),
            "; пустая правая часть — УДАЛЕНИЕ (заменяет «Очистки текста»):"),
          h("ul", {},
            h("li", {}, h("code", {}, "^本章完$ ->"), " — удалить строку «本章完»"),
            h("li", {}, h("code", {}, "[0-9]+ ->"), " — удалить все цифры"),
            h("li", {}, h("code", {}, "\\(未完待续\\) ->"),
              " — убрать «(未完待续)»"),
            h("li", {}, h("code", {}, "第(\\d+)章 -> Глава \\1"),
              " — «第1章» → «Глава 1» (\\1 — группа)"),
            h("li", {}, h("code", {}, "\\s+ -> "), " — сжать пробелы"),
            h("li", {}, h("code", {}, "^  ->"),
              " — убрать 2 пробела в начале строк"),
            h("li", {}, h("code", {}, "^(第\\d+章.*)\\n(?=\\1$) ->"),
              " — убрать строку-дубликат заголовка главы "
              + "(в СЕРЕДИНЕ текста; в начале тела дубль "
              + "убирается автоматически)"),
            h("li", {}, h("code", {}, "(?:他|她) -> 他"), " — унифицировать"),
          ),
          h("p", {}, "Шпаргалка по regexp:"),
          h("ul", {},
            h("li", {}, h("code", {}, "^"), " — начало строки, ",
              h("code", {}, "$"), " — конец"),
            h("li", {}, h("code", {}, "\\d"), " — цифра, ",
              h("code", {}, "\\s"), " — пробел"),
            h("li", {}, h("code", {}, "+"), " — один и больше, ",
              h("code", {}, "*"), " — ноль и больше"),
            h("li", {}, h("code", {}, "(a|b)"), " — a или b, ",
              h("code", {}, "[0-9]"), " — диапазон"),
            h("li", {}, h("code", {}, "\\\\("), " — скобка (экранирование)"),
          ),
          h("p", {}, "Флаги в конце строки правила: ",
            h("code", {}, " |i"), " — не учитывать регистр; ",
            h("code", {}, " |r"), " — regexp (всегда включён); ",
            "пробелы в паттерне значимы."),
        ),
      );
      fieldNodes.push(help);
      if (modeSel && inputWrap) {
        // поля, видимые только в своём режиме: split_patterns — regexp,
        // chunk_size — чанки; маска — чанки ИЛИ переопределение названий;
        // переопределение — во всех режимах кроме чанков
        const wrapSplit = fieldWraps["split_patterns"];
        const wrapChunkSize = fieldWraps["chunk_size"];
        const wrapChunkMask = fieldWraps["chunk_mask"];
        const wrapRename = fieldWraps["rename_chapters"];
        const renameCh = wrapRename && wrapRename._input;
        const applyModeVisibility = () => {
          const m = modeSel.value || "toc";
          if (wrapSplit) wrapSplit.classList.toggle("hidden", m !== "regex");
          if (wrapChunkSize) {
            wrapChunkSize.classList.toggle("hidden", m !== "chunk");
          }
          if (wrapRename) {
            wrapRename.classList.toggle("hidden", m === "chunk");
          }
          if (wrapChunkMask) {
            wrapChunkMask.classList.toggle(
              "hidden",
              !(m === "chunk" || (renameCh && renameCh.checked)),
            );
          }
        };
        if (renameCh) {
          renameCh.addEventListener("change", applyModeVisibility);
        }
        const rebuildInput = () => {
          const m = modeSel.value || "toc";
          const exts = m === "toc" ? [".epub"] : [".epub", ".txt"];
          const sel = inputWrap._input && inputWrap._input._sel;
          if (!sel) return;
          const cur = sel.value;
          const opts = [...sel.options];
          sel.replaceChildren();
          sel.append(h("option", { value: "" }, "—"));
          for (const o of opts) {
            if (o.value === "") continue;
            const ok = exts.some((e) =>
              o.value.toLowerCase().endsWith(e));
            if (ok) sel.append(h("option", { value: o.value }, o.value));
          }
          if (cur && !exts.some((e) => cur.toLowerCase().endsWith(e))) {
            sel.value = "";
            st.values[key]["input"] = "";
            st.touched[key].add("input");
          } else {
            sel.value = cur;
          }
        };
        const onMode = () => {
          applyModeVisibility();
          rebuildInput();
          st.previewDirty = true;
        };
        modeSel.addEventListener("change", onMode);
        onMode();
      }
    }

    if (key === "batch_replace") {
      const help = h(
        "details",
        { class: "regexp-help" },
        h("summary", {}, "Справка по regexp"),
        h(
          "div",
          { class: "regexp-help-body" },
          h("p", {}, "Формат — одна пара на строку: ",
            h("code", {}, "паттерн -> замена"),
            "; пустая правая часть — удаление."),
          h("p", {}, "Замены:"),
          h("ul", {},
            h("li", {}, h("code", {}, "Глава \\d+ -> Глава №\\g<0>"),
              " — «Глава 5» → «Глава №5»"),
            h("li", {}, h("code", {}, "\\s+ -> "), " — сжать пробелы"),
            h("li", {}, h("code", {}, "^  ->"),
              " — убрать 2 пробела в начале строк"),
            h("li", {}, h("code", {}, "^(第\\d+章.*)\\n(?=\\1$) ->"),
              " — убрать строку-дубликат заголовка главы"),
            h("li", {}, h("code", {}, "(?:его|её) -> их"),
              " — «его/её» → «их»"),
            h("li", {}, h("code", {}, "\\bкнязь\\b -> Князь"),
              " — только целое слово"),
          ),
          h("p", {}, "Удаления:"),
          h("ul", {},
            h("li", {}, h("code", {}, "^##? .*$ ->"), " — строки-заголовки"),
            h("li", {}, h("code", {}, "<[^>]+> ->"), " — HTML-теги"),
            h("li", {}, h("code", {}, "\\(\\d+\\) ->"), " — «(12)»"),
          ),
          h("p", {}, "Шпаргалка по regexp:"),
          h("ul", {},
            h("li", {}, h("code", {}, "^"), " — начало строки, ",
              h("code", {}, "$"), " — конец"),
            h("li", {}, h("code", {}, "\\d"), " — цифра, ",
              h("code", {}, "\\s"), " — пробел, ",
              h("code", {}, "\\b"), " — граница слова"),
            h("li", {}, h("code", {}, "\\g<0>"), " — всё совпадение; ",
              h("code", {}, "\\1"), " — первая группа"),
            h("li", {}, h("code", {}, "."), " — любой символ, ",
              h("code", {}, "\\."), " — точка"),
            h("li", {}, h("code", {}, "(a|b)"), " — a или b, ",
              h("code", {}, "[0-9]"), " — диапазон"),
          ),
          h("p", {}, "Флаги в конце строки правила: ",
            h("code", {}, " |i"), " — не учитывать регистр; ",
            h("code", {}, " |r"), " — regexp (всегда включён); ",
            "пробелы в паттерне значимы."),
        ),
      );
      fieldNodes.push(help);
    }

    // translate_check — справка по regexp-проверкам текста главы
    // (всё найденное — ошибка; заголовок главы не считается)
    if (key === "translate_check") {
      fieldNodes.push(
        h(
          "details",
          { class: "regexp-help" },
          h("summary", {}, "Справка по regexp-проверкам"),
          h(
            "div",
            { class: "regexp-help-body" },
            h("p", {}, "Каждая строка — regexp по тексту главы "
              + "(multiline): ВСЁ найденное — ошибка, проверяются ВСЕ "
              + "строки включая заголовок; лишние заголовки «Глава N» — "
              + "в дефолтном наборе (первое совпадение — заголовок "
              + "главы, не ошибка)."),
            h("p", {}, "Дефолтные проверки:"),
            h("ul", {},
              h("li", {}, h("code", {}, "[一-鿿【】「」『』]+"),
                " — иероглифы (остались в переводе)"),
              h("li", {}, h("code", {}, "[a-zA-Z]+"),
                " — латиница"),
              h("li", {}, h("code", {}, "^\\s*Глава\\s+\\d+"),
                " — лишние заголовки «Глава N» (первое совпадение — "
                + "сам заголовок главы)"),
            ),
            h("p", {}, "Свои проверки:"),
            h("ul", {},
              h("li", {}, h("code", {}, "^##? .*$"),
                " — строки-заголовки"),
              h("li", {}, h("code", {}, "<[^>]+>"), " — HTML-теги"),
              h("li", {}, h("code", {}, "\\bкнязь\\b"),
                " — слово «князь» (только целое)"),
            ),
            h("p", {}, "Шпаргалка: ", h("code", {}, "^"), " — начало "
              + "СТРОКИ, ", h("code", {}, "$"), " — конец; ",
              h("code", {}, "\\d"), " — цифра, ",
              h("code", {}, "\\s"), " — пробел, ",
              h("code", {}, "\\b"), " — граница слова; ",
              h("code", {}, "(a|b)"), " — a или b."),
            h("p", {}, "Комментарий в конце строки — ",
              h("code", {}, " # …"), "; пусто в поле = "
              + "дефолтные проверки."),
          ),
        ),
      );
    }

    // compile — предпросмотр обложки/metadata.yaml/страницы поддержки
    // и скрытие неактуальных полей в txt-режимах
    if (key === "compile") {
      const modeSel = fieldWraps["mode"] && fieldWraps["mode"]._input;
      const coverSel =
        fieldWraps["cover"] && fieldWraps["cover"]._input
          && fieldWraps["cover"]._input._sel;
      const metaSel =
        fieldWraps["epub_meta"] && fieldWraps["epub_meta"]._input
          && fieldWraps["epub_meta"]._input._sel;
      const donateSel =
        fieldWraps["donate_file"] && fieldWraps["donate_file"]._input
          && fieldWraps["donate_file"]._input._sel;
      const prevCover = h("img", {
        class: "cover-preview",
        alt: "обложка",
        style: "display:none",
      });
      const prevMeta = h("pre", { class: "compile-prev-text" });
      const prevDonate = h("pre", { class: "compile-prev-text" });
      async function updateCompilePreview() {
        const q = new URLSearchParams({ project: `${section}/${name}` });
        const cover = String(st.values[key]["cover"] || "").trim();
        if (cover) {
          prevCover.src =
            `/api/download?${q}&path=${encodeURIComponent(`source/${cover}`)}`
            + "&inline=1";
          prevCover.style.display = "";
        } else {
          prevCover.style.display = "none";
        }
        // метаданные: пусто = авто source/metadata.yaml (что попадёт
        // в сборку)
        const meta =
          String(st.values[key]["epub_meta"] || "").trim()
          || "metadata.yaml";
        try {
          const d = await api(
            `/file?${q}&path=${encodeURIComponent(`source/${meta}`)}`,
          );
          prevMeta.textContent = d.missing
            ? "Метаданные: файл не найден"
            : `Метаданные (${meta}):\n${(d.content || "").slice(0, 2000)}`;
        } catch {
          prevMeta.textContent = "";
        }
        const donate = String(st.values[key]["donate_file"] || "").trim();
        if (donate) {
          try {
            const d = await api(
              `/file?${q}&path=${encodeURIComponent(`source/${donate}`)}`,
            );
            prevDonate.textContent = d.missing
              ? "Страница поддержки: файл не найден"
              : `Страница поддержки (${donate}):\n`
                + `${(d.content || "").slice(0, 2000)}`;
          } catch {
            prevDonate.textContent = "";
          }
        } else {
          prevDonate.textContent = "";
        }
      }
      const preview = h(
        "details",
        { class: "regexp-help compile-preview" },
        h("summary", {}, "Предпросмотр обложки и файлов"),
        h(
          "div",
          { class: "compile-preview-body" },
          prevCover,
          prevMeta,
          prevDonate,
        ),
      );
      fieldNodes.push(preview);
      function applyCompileMode() {
        const m = (modeSel && modeSel.value) || "txt";
        const book =
          m === "epub" || m === "fb2" || m === "epub-chunks"
          || m === "fb2-chunks";
        for (const name of ["cover", "epub_meta", "donate_file"]) {
          const w = fieldWraps[name];
          if (w) w.classList.toggle("hidden", !book);
        }
        // предпросмотр обложки и файлов — только для EPUB/FB2
        preview.classList.toggle("hidden", !book);
      }
      if (modeSel) modeSel.addEventListener("change", applyCompileMode);
      applyCompileMode();
      for (const sel of [coverSel, metaSel, donateSel]) {
        if (sel) sel.addEventListener("change", updateCompilePreview);
      }
      updateCompilePreview();
    }

    // B4: смена host очищает предзаполненный api_key — иначе старый
    // ключ уедет на чужой сервер (C1 защищает только env-fallback)
    const hostEl = fieldWraps["host"] && fieldWraps["host"]._input;
    const keyEl = fieldWraps["api_key"] && fieldWraps["api_key"]._input;
    if (hostEl && keyEl) {
      hostEl.addEventListener("change", () => {
        keyEl.value = "";
        st.values[key]["api_key"] = "";
        st.touched[key].add("api_key");
      });
    }

    const runBtn = h("button", { class: "btn btn-primary" }, "Запустить");
    runBtn.addEventListener("click", async () => {
      err.textContent = "";
      const verr = epubValidateInput(key, "expert")
        || numericFieldError(key, spec);
      if (verr) {
        err.textContent = verr;
        return;
      }
      try {
        const r = await api("/jobs", {
          method: "POST",
          body: {
            action: key,
            project: `${section}/${name}`,
            params: buildParams(key, spec, "expert"),
          },
        });
        st.job = r.job;
        st.log = [];
        st.events = [];
        st.progress = r.job.progress || null;
        await loadChapterState();
        await render(); // дождаться DOM лога, иначе attachStream не найдёт его
        attachStream(r.job.id);
      } catch (ex) {
        err.textContent = ex.message;
      }
    });

    // обёртки полей текущей формы — для панели предпросмотра замен
    // (batch_replace: реакции на смену типа/диапазона/правил)
    st.curWraps = fieldWraps;

    return h("div", { class: "run-form" }, fieldNodes, err, runBtn);
  }

  // ── epub: панель предпросмотра разбивки ──────────────────────────────
  // Кнопка «Предпросмотр» → POST /stages/epub/preview (папки + размеры);
  // снятие галочки — seq уходит в st.preview.skips БЕЗ перезапуска
  // предпросмотра (строка остаётся с отжатым чекбоксом для наглядности;
  // перенумерация — при реальном запуске со skip); текст главы —
  // GET .../preview/text?num=.
  function epubPreviewPanel(key, spec, mode) {
    const err = h("div", { class: "form-error" });
    const btn = h("button", { class: "btn btn-primary" }, "Предпросмотр");
    btn.addEventListener("click", async () => {
      err.textContent = "";
      const verr = epubValidateInput(key, mode);
      if (verr) {
        err.textContent = verr;
        return;
      }
      try {
        await epubRunPreview(key, spec, mode);
        render();
      } catch (ex) {
        err.textContent = ex.message;
      }
    });
    const title = h(
      "div",
      { class: "run-panel-title" },
      "Предпросмотр разбивки",
      h("span", { class: "spacer" }),
      btn,
    );
    const nodes = [title];
    if (st.preview) {
      if (st.previewDirty) {
        nodes.push(
          h(
            "div",
            { class: "field-help" },
            "Настройки изменены — обновите предпросмотр",
          ),
        );
      }
      nodes.push(epubPreviewList(st.preview, err));
      nodes.push(epubPreviewTextViewer(st.preview, err));
    } else {
      nodes.push(
        h(
          "div",
          { class: "field-help" },
          "Нажмите «Предпросмотр» — покажется список папок глав "
            + "(галочки, размеры, текст)",
        ),
      );
    }
    nodes.push(err);
    return h("div", { class: "run-panel epub-preview" }, nodes);
  }

  // ── batch_replace: предпросмотр замен по главам ────────────────────
  // Выбор типа файлов глав («Тип файлов глав» формы), выбор главы —
  // и подсветка того, что изменится (удалённые/вставленные фрагменты).
  // Правила парсятся и применяются ТЕМ ЖЕ путём, что реальный запуск
  // (parse_replace_lines + apply_rules_segments) — POST
  // /stages/batch_replace/preview.
  function batchReplacePreviewPanel(key) {
    const wraps = st.curWraps || {};
    const err = h("div", { class: "form-error" });
    const note = h("div", { class: "field-help" });
    const dirty = h(
      "div",
      { class: "field-help", style: "display:none" },
    );
    const chSel = h("select", { class: "input" });
    chSel.append(h("option", { value: "" }, "—"));
    const btn = h(
      "button",
      { class: "btn btn-primary", type: "button" },
      "Предпросмотр",
    );
    const box = h("div", { class: "br-box" });

    const inputOf = (w) =>
      (w && w._input && w._input._sel) || (w && w._input) || null;

    // сигнатура формы — для пометки «предпросмотр устарел»
    function sig() {
      const v = st.values[key] || {};
      return [v["type"], v["start"], v["end"], v["replacements"],
              st.brChapter]
        .map((x) => String(x ?? "")).join("\u0000");
    }

    async function loadChapters(autoRun) {
      note.textContent = "Загрузка глав…";
      let tree;
      try {
        tree = await api(`/projects/${section}/${name}/tree`);
      } catch (ex) {
        note.textContent = ex.message;
        return;
      }
      const v = st.values[key] || {};
      const ft = String(v["type"] || "polished");
      const art = `${ft}.txt`;
      const s = parseInt(v["start"] || "", 10);
      const e = parseInt(v["end"] || "", 10);
      const list = (tree.chapters || []).filter((c) => {
        if (!c.artifacts || !c.artifacts[art]) return false;
        if (s > e) return false;
        if (Number.isFinite(s) && c.id < s) return false;
        if (Number.isFinite(e) && c.id > e) return false;
        return true;
      });
      const keep = (st.brChapter != null
        && list.some((c) => c.id === Number(st.brChapter)))
        ? Number(st.brChapter)
        : list.length ? list[0].id : null;
      st.brChapter = keep;
      if (st.brPreview && st.brPreview.num !== keep) st.brPreview = null;
      chSel.replaceChildren();
      if (!list.length) {
        chSel.append(
          h("option", { value: "" },
            s > e
              ? "Диапазон пуст (начало > конца)"
              : `Нет глав типа «${ft}»`),
        );
        chSel.disabled = true;
        note.textContent = s > e
          ? "Исправьте диапазон глав"
          : `В диапазоне нет файлов типа ${ft}`;
        box.replaceChildren();
        return;
      }
      chSel.disabled = false;
      for (const c of list) {
        chSel.append(
          h("option", { value: c.id }, `${c.id} · ${c.dir}`),
        );
      }
      chSel.value = String(keep);
      note.textContent = list.length === 1
        ? "1 глава доступна"
        : `Доступно глав: ${list.length}`;
      if (autoRun) await runPreview();
    }

    async function runPreview() {
      const num = st.brChapter;
      const v = st.values[key] || {};
      const replacements = v["replacements"] || "";
      if (num == null) return;
      err.textContent = "";
      dirty.style.display = "none";
      note.textContent = "Предпросмотр…";
      try {
        const r = await api("/stages/batch_replace/preview", {
          method: "POST",
          body: {
            project: `${section}/${name}`,
            type: v["type"] || "polished",
            chapter: num,
            replacements,
          },
        });
        st.brPreview = r;
        st.brSig = sig();
        renderBrResult();
        note.textContent = r.changed
          ? "Изменения подсвечены: красное — удалено, зелёное — вставлено"
          : (replacements.trim()
            ? "Замен в этой главе нет"
            : "Правил нет — показан текст главы без изменений");
      } catch (ex) {
        st.brPreview = null;
        box.replaceChildren();
        err.textContent = ex.message;
        note.textContent = "";
      }
    }

    function renderBrResult() {
      box.replaceChildren();
      const r = st.brPreview;
      if (!r) return;
      const stats = (r.stats || []).filter((s) => s.count > 0);
      if (stats.length) {
        const head = ["Замен по правилам: "];
        stats.forEach((s, i) => {
          if (i) head.push(" · ");
          head.push(h("code", { class: "br-rule" }, s.label));
          head.push(` ${s.count}`);
        });
        box.append(h("div", { class: "br-stats" }, head));
      }
      if (r.warnings && r.warnings.length) {
        box.append(
          h("div", { class: "field-help" }, "⚠ " + r.warnings.join(" · ")),
        );
      }
      const pre = h("pre", { class: "br-text" });
      for (const [kind, text] of r.segments || []) {
        if (kind === "keep") {
          pre.append(document.createTextNode(text));
        } else {
          pre.append(
            h(
              "span",
              { class: kind === "del" ? "br-del" : "br-ins" },
              text,
            ),
          );
        }
      }
      box.append(pre);
    }

    chSel.addEventListener("change", () => {
      st.brChapter = chSel.value === "" ? null : Number(chSel.value);
      runPreview();
    });
    btn.addEventListener("click", () => {
      err.textContent = "";
      runPreview();
    });

    const el = h(
      "div",
      { class: "run-panel br-panel" },
      h(
        "div",
        { class: "run-panel-title" },
        "Предпросмотр замен",
        h("span", { class: "spacer" }),
        btn,
      ),
      h(
        "div",
        { class: "field" },
        h("div", { class: "field-label" }, "Глава"),
        chSel,
      ),
      note,
      dirty,
      box,
      err,
    );

    // привязка событий формы ПОСЛЕ вставки в DOM: смена типа/диапазона
    // пересобирает список глав (и автопресмотр), набор правил только
    // помечает «устарел» (запуск — кнопкой или выбором главы)
    function mount(formNode) {
      const rangeRow = st.rangeRow || {};
      const onType = inputOf(wraps["type"]);
      const onStart = rangeRow._start || null;
      const onEnd = rangeRow._end || null;
      const onRepl = inputOf(wraps["replacements"]);
      formNode.addEventListener("input", (ev) => {
        if (ev.target === onRepl) {
          if (st.brSig && sig() !== st.brSig) {
            dirty.textContent =
              "Правила изменены — нажмите «Предпросмотр»";
            dirty.style.display = "";
          }
        } else if (ev.target === onStart || ev.target === onEnd) {
          loadChapters(true);
        }
      });
      formNode.addEventListener("change", (ev) => {
        if (ev.target === onType) {
          st.brPreview = null;
          st.brChapter = null;
          loadChapters(true);
        }
      });
      loadChapters(true);
    }

    return { el, mount };
  }

  // валидация исходника epub: обязателен; расширения — по режиму
  // проверка number-полей с min/max из spec: значение вне диапазона
  // блокируется ДО отправки (иначе скрипт упадёт с кодом 2 и «failed»
  // без понятной причины). Пусто/не число — пропускаем (скрипт сам
  // решает); валидны только заполненные значения.
  function numericFieldError(key, spec) {
    const vals = st.values[key] || {};
    for (const f of spec.fields || []) {
      if (f.type !== "number" || f.min == null && f.max == null) continue;
      const raw = String(vals[f.name] ?? "");
      if (raw === "" || raw == null) continue;
      const n = Number(raw);
      if (Number.isNaN(n)) continue;
      const label = (f.label || f.name).replace(/\s*\([^)]*\)\s*$/, "");
      if (f.min != null && n < f.min)
        return `«${label}»: минимум ${f.min}`;
      if (f.max != null && n > f.max)
        return `«${label}»: максимум ${f.max}`;
    }
    return "";
  }

  function epubValidateInput(key, mode) {
    if (key !== "epub") return "";
    const vals = st.values[key] || {};
    const v = String(vals["input"] || "");
    if (!v) return "Выберите исходник";
    const m =
      mode === "simple" ? "toc" : String(vals["mode"] || "toc");
    const ext = v.toLowerCase().split(".").pop();
    const ok =
      m === "toc"
        ? ext === "epub"
        : ext === "epub" || ext === "txt";
    return ok
      ? ""
      : m === "toc"
        ? "В режиме «по TOC» принимается только epub"
        : "Принимаются только epub и txt (zip не поддерживается)";
  }

  // запуск предпросмотра: синхронно гоняет CLI с --preview-json
  async function epubRunPreview(key, spec, mode) {
    const r = await api("/stages/epub/preview", {
      method: "POST",
      body: {
        project: `${section}/${name}`,
        params: buildParams(key, spec, mode),
        skip: (st.preview && st.preview.skips) || [],
      },
    });
    st.preview = {
      entries: r.entries || [],
      source: r.source || "",
      skips: (st.preview && st.preview.skips) || [],
      viewNum: null,
    };
    st.previewDirty = false;
  }

  // список папок: чекбокс (по умолчанию все включены) + имя + размер;
  // снятие галочки — глава пропускается, остальные перенумеровываются
  // (предпросмотр перезапускается со списком skip)
  function epubPreviewList(prev, err) {
    const skips = new Set(prev.skips || []);
    const rows = (prev.entries || []).map((e) => {
      const cb = h("input", { type: "checkbox", class: "checkbox" });
      cb.checked = !skips.has(e.seq);
      cb.addEventListener("change", () => {
        // снятие галочки — seq уходит в skips, НО предпросмотр не
        // перезапускаем: строка остаётся с отжатым чекбоксом для
        // наглядности; перенумерация произойдёт при реальном запуске
        err.textContent = "";
        const s = new Set(prev.skips || []);
        if (cb.checked) {
          s.delete(e.seq);
        } else {
          s.add(e.seq);
        }
        prev.skips = [...s];
        // БЕЗ render(): только визуальное состояние строки — иначе
        // перерисовывается вся страница (форма, поля, предпросмотр)
        row.classList.toggle("epub-prev-skip", !cb.checked);
      });
      const row = h(
        "div",
        { class: "epub-prev-row" },
        h("label", { class: "epub-prev-check" }, cb),
        h("span", { class: "epub-prev-folder", text: e.folder }),
        h(
          "span",
          { class: "epub-prev-size", text: `${e.size_kb} kB` },
        ),
      );
      if (!cb.checked) row.classList.add("epub-prev-skip");
      return row;
    });
    const hint = h(
      "div",
      { class: "field-help" },
      "Снимите галочку, чтобы исключить главу из разбора — "
        + "нумерация остальных сместится при запуске",
    );
    return h("div", { class: "epub-prev-list" }, hint, rows);
  }

  // просмотр текста главы: селектор по номеру → текст с сервера
  function epubPreviewTextViewer(prev, err) {
    const sel = h("select", { class: "input" });
    sel.append(h("option", { value: "" }, "— текст главы —"));
    for (const e of prev.entries || []) {
      sel.append(
        h("option", { value: e.num }, `${e.num} · ${e.heading || e.folder}`),
      );
    }
    const box = h(
      "pre",
      { class: "epub-prev-text", text: "(выберите главу)" },
    );
    sel.addEventListener("change", async () => {
      if (!sel.value) {
        box.textContent = "(выберите главу)";
        prev.viewNum = null;
        return;
      }
      prev.viewNum = Number(sel.value);
      try {
        const r = await api(
          `/stages/epub/preview/text?num=${sel.value}`
            + `&project=${section}/${name}`,
        );
        box.textContent = `${r.heading || ""}\n\n${r.text || ""}`;
      } catch (ex) {
        err.textContent = ex.message;
      }
    });
    if (prev.viewNum != null) {
      const opt = [...sel.options].find((o) => o.value === String(prev.viewNum));
      if (opt) {
        sel.value = opt.value;
        box.textContent = "(обновите текст)";
      }
    }
    return h(
      "div",
      { class: "field" },
      h("div", { class: "field-label" }, "Текст главы"),
      sel,
      box,
    );
  }

  function logPanel() {
    const job = st.job;
    const pre = h("pre", { class: "log-area", text: "" });
    // st.log наполняется ТОЛЬКО SSE-стримом — payload lines
    // не дублируем (иначе хвост приходит дважды: snapshot + бурст)
    pre.textContent = st.log.length ? st.log.join("\n") : "(лог пуст)";
    pre.scrollTop = pre.scrollHeight;
    const status = h(
      "span",
      { class: "badge log-status badge-" + job.status },
      job.status,
    );
    const stopBtn = h("button", { class: "btn btn-sm btn-danger" }, "Стоп");
    stopBtn.addEventListener("click", async () => {
      try {
        await api(`/jobs/${job.id}/stop`, { method: "POST" });
        toast("Остановка...");
      } catch (ex) {
        toast(ex.message, "err");
      }
    });
    const toolbar = h(
      "div",
      { class: "run-panel-title" },
      h("span", { text: "Лог · " }),
      h(
        "span",
        { class: "log-job-title" },
        job.title || job.action || "запуск",
      ),
      h("span", { class: "spacer" }),
      status,
      h("span", { class: "progress-line", text: progressLineText() }),
      stopBtn,
    );
    // прогрессбар из структурированных событий @@PROGRESS@@
    // (label + трек + done/total + %); без событий — скрыт.
    // Класс log-progress — уникальный селектор для SSE: обычный
    // .progress-wrap первым в DOM находит мини-бар «Активный запуск»
    const bar = h(
      "div",
      { class: "progress-wrap log-progress" },
      h("span", { class: "progress-label", text: "" }),
      h(
        "div",
        { class: "progress-track" },
        h("div", { class: "progress-fill", style: "width:0%" }),
      ),
      h("span", { class: "progress-text", text: "" }),
    );
    paintBar(bar);
    const panel = h("div", { class: "run-panel" }, toolbar, bar, pre);
    if (st.stage === "pipeline" && st.job && st.job.action === "pipeline") {
      // конвейер: таблица глав поверх лога
      const table = chapterTable();
      panel.prepend(
        h(
          "div",
          { class: "run-panel-title" },
          "Главы · перевод → редактура → полировка",
        ),
        table,
      );
    }
    return panel;
  }

  // текстовая строка прогресса («📊 12/636») для тулбара лога
  function progressLineText() {
    return UICore.progressText(
      st.progress,
      st.job && st.job.status === "running",
    );
  }

  // отрисовка прогрессбара в переданный узел (лог-панель и SSE).
  // Все querySelector'ы загардены: чужой узел (например мини-бар без
  // .progress-label) не должен уронить SSE-стрим
  function paintBar(bar) {
    const p = st.progress;
    const running = st.job && st.job.status === "running";
    // пока задача работает, бар ВСЕГДА виден — даже до первого
    // события прогресса («ожидание первого результата…»)
    if (!p && !running) {
      bar.classList.add("hidden");
      return;
    }
    bar.classList.remove("hidden");
    const total = p && p.total ? p.total : 0;
    const done = p ? p.done : 0;
    const pct = UICore.progressPct(done, total);
    const label = bar.querySelector(".progress-label");
    if (label) label.textContent = (p && p.label) || (running ? "Запуск" : "");
    const fill = bar.querySelector(".progress-fill");
    if (fill) fill.style.width = pct + "%";
    const text = bar.querySelector(".progress-text");
    if (text)
      text.textContent = p
        ? total > 0
          ? `${p.done}/${total} · ${pct}%`
          : `${p.done} …`
        : running
          ? "ожидание первого результата…"
          : "";
  }

  // таблица глав для конвейера: строки = главы, колонки = стадии 1..3
  function chapterTable() {
    const opts = st.options || {};
    const ch = opts.chapters || {};
    // B10: реальные id из options.chapters.ids (опции стадии); без ids
    // (старый сервер) — диапазон min..max как раньше
    const ids = Array.isArray(ch.ids) && ch.ids.length
      ? ch.ids
      : null;
    const cols = [1, 2, 3];
    const stageSym = { 1: "пер", 2: "ред", 3: "пол" };
    const statusSym = { OK: "✓", ERROR: "✗", SKIP: "⊘" };
    const byKey = UICore.chapterByKey(st.events);
    const cells = [];
    const range = ids || (() => {
      const min = ch.min == null ? 1 : ch.min;
      const max = ch.max == null ? 20 : ch.max;
      const out = [];
      for (let i = min; i <= max; i++) out.push(i);
      return out;
    })();
    const stateByStage = { 1: "translate", 2: "redact", 3: "polish" };
    for (const id of range) {
      const row = [h("th", { class: "ch-num" }, String(id))];
      for (const stage of cols) {
        // событие текущего запуска > фактическое состояние артефактов
        // (статус проекта) > «·»
        let s = byKey[id + ":" + stage];
        if (!s) {
          const stCh = st.chapterState && st.chapterState[id];
          if (stCh && stCh[stateByStage[stage]]) s = "OK";
        }
        const cls = s ? "ch-cell ch-" + s.toLowerCase() : "ch-cell ch-pending";
        row.push(
          h(
            "td",
            { class: cls, title: `${stageSym[stage]}: ${s || "—"}` },
            s ? statusSym[s] || s : "·",
          ),
        );
      }
      cells.push(h("tr", { class: "ch-row", "data-id": id }, row));
    }
    return h(
      "table",
      { class: "ch-table" },
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", {}, "#"),
          h("th", {}, "перевод"),
          h("th", {}, "редактура"),
          h("th", {}, "полировка"),
        ),
      ),
      h("tbody", {}, cells),
    );
  }

  // стрим лога после старта. единственный источник лога —
  // SSE (стартовый бурст сервера уже содержит хвост), payload lines не
  // дублируем; AbortController гасит старый стрим при уходе со страницы.
  // U6: обрыв сети/таймаут — reconnect с backoff (1 c, 2 c, 4 c, …),
  // не больше MAX_ATTEMPTS; при переподключении сервер шлёт весь хвост
  // заново — очищаем st.log, чтобы снапшот не продублировал строки.
  const MAX_STREAM_ATTEMPTS = 5;
  async function attachStream(jobId) {
    if (streamCtrl) streamCtrl.abort(); // один стрим на экземпляр
    streamCtrl = new AbortController();
    const sig = streamCtrl.signal;
    try {
      for (let attempt = 0; ; attempt++) {
        try {
          await streamOnce(jobId, sig);
          break; // стрим завершился штатно (задание закончилось)
        } catch (ex) {
          if (ex && ex.name === "AbortError") return; // ушли со страницы
          const running = st.job && st.job.status === "running";
          if (!running || attempt >= MAX_STREAM_ATTEMPTS) {
            if (running) toast(`SSE-стрим оборван: ${ex.message}`, "err");
            break;
          }
          st.log = []; // снапшот reconnect'а придёт целиком — без дублей
          await new Promise((r) => setTimeout(r, 1000 * 2 ** attempt));
        }
      }
      // конец стрима (задание завершилось): догнать статус/события;
      // панель лога закрывается — status больше не running (лог —
      // только активного запуска)
      try {
        const r = await api(`/jobs/${jobId}`);
        if (r.job) {
          if (r.job.events) st.events = r.job.events;
          if (r.job.status) st.job.status = r.job.status;
        }
      } catch {
        /* статус уже есть */
      }
      // B5: после завершения запуска могли появиться новые файлы
      // (ner.json, wiki.md) — форма перечитает опции при рендере;
      // таблица глав — по свежему состоянию артефактов
      st.options = null;
      await loadChapterState();
      render();
    } finally {
      streamCtrl = null;
    }
  }

  // одна попытка стрима: fetch + чтение до конца (EOF = статус).
  // Выбрасывает исключение при сетевом обрыве — reconnect крутится выше.
  async function streamOnce(jobId, sig) {
    // события конвейера приходят в том же SSE (snapshot + живые);
    // отдельный GET /jobs здесь не нужен — он дублировал бы события
    const res = await fetch(`/api/jobs/${jobId}/stream`, { signal: sig });
    if (!res.ok || !res.body) {
      const err = await res.json().catch(() => ({}));
      throw new Error((err && err.error) || `Ошибка ${res.status}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const events = buf.split("\n\n");
      buf = events.pop();
      for (const ev of events) {
        for (const line of ev.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          let payload;
          try {
            payload = JSON.parse(line.slice(6));
          } catch {
            continue;
          }
          // обработка одного payload — в своём try/catch: кривое
          // событие не должно ронять весь стрим (иначе лог замрёт,
          // а панель не закроется — status так и не придёт)
          try {
            onPayload(payload);
          } catch (ex) {
            console.warn("SSE: событие пропущено:", ex && ex.message);
          }
        }
      }
    }
  }

  // разбор одного SSE-payload: лог, события глав, прогресс, статус.
  // Все querySelector'ы идут по page с гвардами — узел может быть
  // перерисован/отсутствовать (панель перестроена после завершения)
  function onPayload(payload) {
    if (payload.type === "line") {
      st.log.push(payload.text);
      if (st.log.length > 2000) st.log.splice(0, st.log.length - 2000);
      const pre = page.querySelector(".log-area");
      if (pre) {
        pre.textContent = st.log.join("\n");
        pre.scrollTop = pre.scrollHeight;
      }
    } else if (payload.type === "event" && payload.event) {
      const ev = payload.event;
      // таблица глав — только pipeline-события (числовой stage);
      // иначе селектор будет невалидным и уронит стрим
      if (ev && ev.id != null && typeof ev.stage === "number") {
        // дедуп: snapshot стрима повторяет события из attachToJob
        const prev = st.events.findIndex(
          (e) => e.id === ev.id && e.stage === ev.stage,
        );
        if (prev >= 0) st.events[prev] = ev;
        else st.events.push(ev);
        const t = page.querySelector(".ch-table");
        if (t) {
          // B10: строка ищется по data-id (реальные главы), а не
          // nth-child (позиция) — нумерация может быть разреженной
          const cell = t.querySelector(
            `.ch-row[data-id="${ev.id}"] .ch-cell:nth-child(${ev.stage + 1})`,
          );
          if (cell) {
            const statusSym = { OK: "✓", ERROR: "✗", SKIP: "⊘" };
            cell.textContent = statusSym[ev.status] || ev.status;
            cell.className = "ch-cell ch-" + String(ev.status).toLowerCase();
          }
        }
      }
    } else if (payload.type === "progress" && payload.event) {
      st.progress = payload.event;
      // лог-бар — по уникальному классу; мини-бар «Активный запуск»
      // обновляем отдельно (в нём нет .progress-label)
      const bar = page.querySelector(".log-progress");
      if (bar) paintBar(bar);
      const mini = page.querySelector(".progress-wrap.mini");
      if (mini) paintMini(mini);
      const pl = page.querySelector(".progress-line");
      if (pl) pl.textContent = progressLineText();
    } else if (payload.type === "status") {
      st.job.status = payload.status;
      // статус — финальное событие стрима: перерисовать сразу,
      // иначе панель «Активный запуск» и лог остаются со старым
      // статусом (stopped/done/failed должны закрывать панели)
      render();
    }
  }

  // уход со страницы (новый view в #app): гасим стрим, чтобы старый
  // экземпляр не держал SSE и не мутировал detached-узлы
  page.addEventListener(
    "pi-navigate",
    () => {
      if (streamCtrl) streamCtrl.abort();
      streamCtrl = null;
    },
    { once: true },
  );

  // авто-прикрепление: jobId из URL (#/run/sec/name/{jobId}) или
  // активный запуск проекта (вернулись на страницу — управление не потеряно)
  if (attachJobId && /^[0-9a-f]{12}$/.test(attachJobId)) {
    attachToJob(attachJobId);
  } else {
    autoAttach();
  }
  return page;
}

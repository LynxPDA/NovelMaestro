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
          // без job id — autoAttach сам прикрепится к активному
          href: `#/run/${section}/${name}`,
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
    return p;
  }

  // общий построитель поля: label + input, привязанный к st.values[key]
  // (оба режима). Возвращает label-обёртку; сам input — в wrap._input
  // (у files — row, select внутри row._sel).
  function buildField(key, f) {
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
      // загрузка своего файла сразу (без выбора из существующих)
      const row = h("div", { class: "field-row" }, input);
      const upInput = h("input", { type: "file", class: "hidden" });
      const upBtn = h(
        "button",
        { class: "btn btn-sm btn-ghost" },
        "Загрузить",
      );
      upBtn.addEventListener("click", () => upInput.click());
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
      } catch (ex) {
        chipsInfo.textContent = ex.message;
      }
    };
    return { chipsBar, chipsBox, guide, loadTypes };
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
    return h(
      "div",
      { class: "preset-range-row" },
      h("span", { class: "preset-range-label" }, "Главы:"),
      start,
      h("span", { class: "preset-range-sep" }, "–"),
      end,
    );
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
    const byName = {};
    for (const name of spec.simple || []) {
      const f = (spec.fields || []).find((x) => x.name === name);
      if (!f) continue;
      const wrap = buildField(key, f);
      wraps.push(wrap);
      // files-поля: настоящий select спрятан в row._sel (row — обёртка
      // с кнопкой «Загрузить»); без этого srcSel.value/fileSel.value
      // всегда undefined и условная видимость полей не работает
      byName[name] = (wrap._input && wrap._input._sel) || wrap._input;
    }
    // ner: входной файл или сборка глав; «постобработка» — без LLM
    if (key === "ner" && byName["mode"]) {
      const modeSel = byName["mode"];
      const fileSel = byName["file"];
      const applyNerSimple = () => {
        const isPost = modeSel.value === "postprocess";
        for (const name of ["prompt_file", "two_pass"]) {
          const w = byName[name];
          const wrap = w && w.closest ? w.closest(".field") : null;
          if (wrap) wrap.classList.toggle("hidden", isPost);
        }
        const fw = fileSel && fileSel.closest
          ? fileSel.closest(".field") : null;
        if (fw) fw.classList.toggle("hidden", isPost);
      };
      modeSel.addEventListener("change", applyNerSimple);
      if (fileSel) fileSel.addEventListener("change", applyNerSimple);
      applyNerSimple();
    }
    // ner_check: чипсы типов из глоссария после select «Проходы»
    if (key === "ner_check" && byName["passes"]) {
      const sel = byName["passes"];
      const wrap = sel.closest ? sel.closest(".field") : null;
      const w = nerCheckWidgets(key);
      if (wrap) {
        const idx = wraps.indexOf(wrap);
        if (idx >= 0) {
          wraps.splice(idx + 1, 0, w.chipsBar, w.chipsBox, w.guide);
        }
      }
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
      // (сборка глав в память) и режим не «постобработка»
      if (key === "ner" && byName["mode"]) {
        const modeSel = byName["mode"];
        const fileSel = byName["file"];
        const applyNerRange = () => {
          const noFile = !(fileSel && fileSel.value);
          const notPost = modeSel.value !== "postprocess";
          rangeRow.classList.toggle("hidden", !(noFile && notPost));
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
      const verr = epubValidateInput(key, "simple");
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
      fieldNodes.push(wrap);
      fieldWraps[f.name] = wrap;
    }
    // диапазон глав — ПЕРВЫМ в списке полей (все стадии с start/end)
    const rangeRow = buildRangeRow(key, spec);
    if (rangeRow) fieldNodes.unshift(rangeRow);

    // pipeline — единый общий промпт-файл (теги translate/redact/polish),
    // режим промптов и отдельные файлы на стадию убраны

    // ner — режимы: LLM (extract/finetune) и постобработка (без LLM):
    // постобработка прячет LLM-поля, файл и диапазон; входной файл
    // не выбран — сборка глав в память, тогда виден диапазон
    if (key === "ner") {
      const modeSel = fieldWraps["mode"] && fieldWraps["mode"]._input;
      const fileSel =
        fieldWraps["file"] && fieldWraps["file"]._input
          && fieldWraps["file"]._input._sel;
      const llmFields = [
        "host", "model", "api_key", "prompt_file", "threads",
        "chunk_size", "threshold", "ngram", "temperature", "reasoning",
        "two_pass", "keep_fields", "save_interval", "retries", "timeout",
      ];
      function applyNerMode() {
        const m = (modeSel && modeSel.value) || "extract";
        const isPost = m === "postprocess";
        for (const name of llmFields) {
          const wrap = fieldWraps[name];
          if (wrap) wrap.classList.toggle("hidden", isPost);
        }
        const fw = fieldWraps["file"];
        if (fw) fw.classList.toggle("hidden", isPost);
        const noFile = !(fileSel && fileSel.value);
        if (rangeRow) {
          rangeRow.classList.toggle("hidden", isPost || !noFile);
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
    // карточки-пресеты убраны (названия — в select), степпер остаётся
    if (key === "ner_check") {
      const passesWrap = fieldWraps["passes"];
      const w = nerCheckWidgets(key);
      if (passesWrap) {
        const idx = fieldNodes.indexOf(passesWrap);
        if (idx >= 0) {
          fieldNodes.splice(idx + 1, 0, w.chipsBar, w.chipsBox, w.guide);
        }
      }
      w.loadTypes();
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
      const verr = epubValidateInput(key, "expert");
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

  // валидация исходника epub: обязателен; расширения — по режиму
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
      const statusEl = page.querySelector(".log-status");
      if (statusEl) {
        statusEl.textContent = payload.status;
        statusEl.className = "badge badge-" + payload.status;
      }
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

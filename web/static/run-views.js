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
      if (job.status !== "running") {
        // завершённый запуск лог не показывает (Д2) —
        // ведём себя как заход на страницу без job id
        autoAttach();
        return;
      }
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
    // активный запуск/стрим НЕ сбрасываем — лог остаётся
    // под формой, пока стадия не завершилась
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

    // правая панель: форма + лог (лог — только у активного запуска;
    // завершённый не показывается — история во вкладке «Логи»)
    const right = h(
      "div",
      { class: "run-col run-col-form" },
      st.stage ? await formPanel() : emptyRun(),
      st.job && st.job.status === "running"
        ? logPanel()
        : h(
            "div",
            { class: "run-empty" },
            "Запустите стадию — лог появится здесь",
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
    if (!st.options) {
      try {
        const r = await api(
          `/stages/${key}/options?project=${section}/${name}`,
        );
        st.options = r.options || {};
      } catch {
        st.options = {};
      }
    }
    // синхронизация режимов: значения формы — общие (st.values),
    // инициализация один раз на выбор стадии (дефолты + .env-префилл)
    if (!st.values[key]) initFormValues(key, spec);
    // «Простой режим» — только для стадий с пресетом (spec.simple);
    // translate_check/batch_replace/compile — только экспертные,
    // переключатель не показываем
    const hasSimple = (spec.simple || []).length > 0;
    const mode = hasSimple ? UICore.runModeGet(key) : "expert";
    const body =
      hasSimple && mode === "simple"
        ? simplePanel(key, spec)
        : expertForm(key, spec);
    return h(
      "div",
      { class: "run-panel" },
      h("div", { class: "run-panel-title" }, `${key} · ${spec.title}`),
      hasSimple ? modeToggle(key, mode) : null,
      body,
    );
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
      // чекбокс СЛЕВА от текста (не снизу): строка checkbox + label
      const wrap = h("label", { class: "field field-check" }, input, label);
      if (f.help) wrap.append(h("div", { class: "field-help" }, f.help));
      wrap._input = input;
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
    } else if (f.type === "files") {
      input = h("select", { class: "input" });
      const dir = f.dir || "";
      const pool =
        dir === "source"
          ? st.options.source || []
          : dir === "prompts"
            ? st.options.prompts || []
            : st.options.root || [];
      const exts = (f.ext || []).map((e) => e.toLowerCase());
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
      input.addEventListener("change", () => {
        vals[f.name] = input.value;
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
    if (f.help) wrap.append(h("div", { class: "field-help" }, f.help));
    wrap._input = input;
    return wrap;
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
      byName[name] = wrap._input;
    }
    // ner: «постобработка» — без LLM, прячем промпт и двухпроходную схему
    if (key === "ner" && byName["mode"]) {
      const modeSel = byName["mode"];
      const applyNerSimple = () => {
        const hide = modeSel.value === "postprocess";
        for (const name of ["prompt_file", "two_pass"]) {
          const w = byName[name];
          const wrap = w && w.closest ? w.closest(".field") : null;
          if (wrap) wrap.classList.toggle("hidden", hide);
        }
      };
      modeSel.addEventListener("change", applyNerSimple);
      applyNerSimple();
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
    // диапазон глав (всегда виден, если стадия принимает start/end)
    const hasRange = (spec.fields || []).some(
      (f) => f.name === "start" || f.name === "end",
    );
    const rangeNodes = [];
    if (hasRange) {
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
      rangeNodes.push(
        h(
          "div",
          { class: "preset-range-row" },
          h("span", { class: "preset-range-label" }, "Главы:"),
          start,
          h("span", { class: "preset-range-sep" }, "–"),
          end,
        ),
      );
    }
    const runBtn = h("button", { class: "btn btn-primary" }, "Запустить");
    runBtn.addEventListener("click", async () => {
      err.textContent = "";
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
    // wiki: диапазон глав — НАД «Источник текста» (простой режим);
    // остальные стадии — как раньше, после полей
    const body = [card, ...wraps];
    if (key === "wiki" && rangeNodes.length) {
      body.splice(1, 0, ...rangeNodes);
    } else {
      body.push(...rangeNodes);
    }
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
      const wrap = buildField(key, f);
      fieldNodes.push(wrap);
      fieldWraps[f.name] = wrap;
    }

    // pipeline — единый общий промпт-файл (теги translate/redact/polish),
    // режим промптов и отдельные файлы на стадию убраны

    // ner — режимы: LLM (extract/finetune/compile) и постобработка (без LLM):
    // постобработка прячет LLM-поля и входной txt, «собрать главы» —
    // прячет txt и показывает диапазон глав
    if (key === "ner") {
      const modeSel = fieldWraps["mode"] && fieldWraps["mode"]._input;
      const llmFields = [
        "host", "model", "api_key", "prompt_file", "threads",
        "chunk_size", "threshold", "ngram", "temperature", "reasoning",
        "two_pass", "keep_fields", "save_interval", "retries", "timeout",
      ];
      const rangeFields = ["start", "end"];
      const fileField = "file";
      function applyNerMode() {
        const m = (modeSel && modeSel.value) || "extract";
        const isPost = m === "postprocess";
        const isCompile = m === "compile";
        for (const name of llmFields) {
          const wrap = fieldWraps[name];
          if (wrap) wrap.classList.toggle("hidden", isPost);
        }
        for (const name of rangeFields) {
          const wrap = fieldWraps[name];
          if (wrap) wrap.classList.toggle("hidden", !isCompile);
        }
        const fw = fieldWraps[fileField];
        if (fw) fw.classList.toggle("hidden", isPost || isCompile);
      }
      if (modeSel) modeSel.addEventListener("change", applyNerMode);
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
        for (const name of ["type", "start", "end"]) {
          const w = fieldWraps[name];
          if (w) w.classList.toggle("hidden", src !== "chapters");
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
    stopBtn.disabled = job.status !== "running";
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
      // лог скрывается сам — st.job больше не running (Д2)
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
  // перерисован/отсутствовать (лог-панель скрыта после завершения)
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

function viewRun(section, name, attachJobId) {
  const st = {
    stage: null, // выбранная стадия: {key, title, script, spec}
    options: null, // динамические опции {chapters, source, prompts, root}
    job: null, // текущий запуск
    log: [], // строки лога текущего запуска (ТОЛЬКО из SSE-стрима)
    events: [], // события глав конвейера (стадия 3)
    progress: null, // последнее событие прогресса {label, done, total}
    gen: 0, // поколение отрисовки — гасит гонки двух render()
  };
  const page = h("div", { class: "page" });
  let streamCtrl = null; // AbortController текущего SSE-стрима

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

    // правая панель: форма + лог (лог — только у активного запуска)
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
    const err = h("div", { class: "form-error" });
    const fieldNodes = [];
    const fieldWraps = {}; // name → label-обёртка (промпты pipeline, )
    const values = {};

    // C/D: bool из .env (строка "0") и files-default по basename
    const boolOn = UICore.boolOn;
    const fileBase = UICore.fileBase;

    for (const f of spec.fields || []) {
      const label = h("div", { class: "field-label" }, f.label);
      let input;
      if (f.type === "bool") {
        input = h("input", { type: "checkbox", class: "checkbox" });
        input.checked = boolOn(f.default);
        values[f.name] = input;
      } else if (f.type === "select") {
        input = h("select", { class: "input" });
        const labels = f.labels || {};
        for (const o of f.options || []) {
          const text = labels[o] == null ? (o === "" ? "—" : o) : labels[o];
          input.append(h("option", { value: o }, text));
        }
        input.value = f.default == null ? "" : String(f.default);
        values[f.name] = input;
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
        // C: default из .env может быть prompts/ner_prompt.txt —
        // селект наполнен именами файлов, сравниваем basename
        const rawDef = f.default == null ? "" : String(f.default);
        const baseDef = fileBase(rawDef);
        let chosen = "";
        if (baseDef && items.includes(baseDef)) chosen = baseDef;
        else if (rawDef && items.includes(rawDef)) chosen = rawDef;
        else if (baseDef) {
          input.append(h("option", { value: baseDef }, baseDef));
          chosen = baseDef;
        }
        input.value = chosen;
        values[f.name] = input;
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
          form.append("dest", dir || "tmp");
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
        row._sel = input; // submit: значение берём из select внутри row
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
        input.value = f.default == null ? "" : String(f.default);
        // автозаполнение диапазона глав из опций стадии (как в CLI)
        if (f.name === "start" || f.name === "end") {
          const ch = (st.options || {}).chapters || {};
          const def = f.name === "start" ? ch.min : ch.max;
          if (def != null && input.value === "") input.value = String(def);
        }
        values[f.name] = input;
      }
      const wrap = h("label", { class: "field" }, label, input);
      if (f.help) wrap.append(h("div", { class: "field-help" }, f.help));
      fieldNodes.push(wrap);
      fieldWraps[f.name] = wrap;
    }

    // pipeline — режим промптов (auto/separate/combined):
    // показываем только нужные поля по выбранному режиму
    if (key === "pipeline") {
      const modeSel = values["prompt_mode"];
      const promptFields = [
        "prompt_file",
        "translate_prompt",
        "redact_prompt",
        "polish_prompt",
      ];
      const visible = {
        auto: promptFields,
        separate: ["translate_prompt", "redact_prompt", "polish_prompt"],
        combined: ["prompt_file"],
      };
      function applyMode() {
        const m = (modeSel && modeSel.value) || "auto";
        const show = visible[m] || promptFields;
        for (const name of promptFields) {
          const wrap = fieldWraps[name];
          if (wrap) wrap.classList.toggle("hidden", !show.includes(name));
        }
      }
      if (modeSel) modeSel.addEventListener("change", applyMode);
      applyMode();
    }

    const runBtn = h("button", { class: "btn btn-primary" }, "Запустить");
    runBtn.addEventListener("click", async () => {
      err.textContent = "";
      const params = {};
      for (const f of spec.fields || []) {
        const el = values[f.name];
        if (f.type === "bool") {
          params[f.name] = el.checked;
        } else {
          // files: select лежит внутри row (кнопка «Загрузить» рядом)
          const src = el._sel || el;
          const v = src.value.trim();
          if (v === "") continue;
          // R5-G: голое имя файла из select → путь внутри проекта
          // (source/*, prompts/*), иначе скрипт не найдёт файл от cwd.
          let val = v;
          if (f.type === "files" && f.dir && !v.includes("/")) {
            val = `${f.dir}/${v}`;
          }
          params[f.name] = val;
        }
      }
      try {
        const r = await api("/jobs", {
          method: "POST",
          body: { action: key, project: `${section}/${name}`, params },
        });
        st.job = r.job;
        st.log = [];
        st.events = [];
        st.progress = r.job.progress || null;
        await render(); // дождаться DOM лога, иначе attachStream не найдёт его
        attachStream(r.job.id);
      } catch (ex) {
        err.textContent = ex.message;
      }
    });

    return h(
      "div",
      { class: "run-panel" },
      h("div", { class: "run-panel-title" }, `${key} · ${spec.title}`),
      h("div", { class: "run-form" }, fieldNodes, err, runBtn),
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
    const min = ch.min == null ? 1 : ch.min;
    const max = ch.max == null ? 20 : ch.max;
    const cols = [1, 2, 3];
    const stageSym = { 1: "пер", 2: "ред", 3: "пол" };
    const statusSym = { OK: "✓", ERROR: "✗", SKIP: "⊘" };
    const byKey = UICore.chapterByKey(st.events);
    const cells = [];
    for (let id = min; id <= max; id++) {
      const row = [h("th", { class: "ch-num" }, String(id))];
      for (const stage of cols) {
        const s = byKey[id + ":" + stage];
        const cls = s ? "ch-cell ch-" + s.toLowerCase() : "ch-cell ch-pending";
        row.push(
          h(
            "td",
            { class: cls, title: `${stageSym[stage]}: ${s || "—"}` },
            s ? statusSym[s] || s : "·",
          ),
        );
      }
      cells.push(h("tr", { class: "ch-row" }, row));
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
  async function attachStream(jobId) {
    if (streamCtrl) streamCtrl.abort(); // один стрим на экземпляр
    streamCtrl = new AbortController();
    const sig = streamCtrl.signal;
    try {
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
      render();
    } catch (ex) {
      if (ex && ex.name === "AbortError") return; // ушли со страницы — молча
      toast(ex.message, "err");
      render();
    } finally {
      streamCtrl = null;
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
      // иначе селектор nth-child будет невалидным и уронит стрим
      if (ev && ev.id != null && typeof ev.stage === "number") {
        // дедуп: snapshot стрима повторяет события из attachToJob
        const prev = st.events.findIndex(
          (e) => e.id === ev.id && e.stage === ev.stage,
        );
        if (prev >= 0) st.events[prev] = ev;
        else st.events.push(ev);
        const t = page.querySelector(".ch-table");
        if (t) {
          const cell = t.querySelector(
            `.ch-row:nth-child(${ev.id}) .ch-cell:nth-child(${ev.stage + 1})`,
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

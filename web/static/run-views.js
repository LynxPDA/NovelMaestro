  // translate_quality: последняя глава, помещающаяся в бюджет запроса.
  // Размер главы = перевод (тип файлов) + оригинал (chapter.txt);
  // размеры из /tree — СИМВОЛЫ (сервер декодирует utf-8), бюджет —
  // ТОЛЬКО на содержимое, промпт НЕ вычитается; доступна
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

// Вкладка «Запуски» проекта (глобал: window.viewRun вызывается
// из project-views.js, вкладка «run»); явная привязка — чтобы линтер
// видел использование
window.viewRun = function viewRun(section, name, attachJobId) {

  /* Значения форм стадий, чипсы и профиль LLM — состояние КНИГИ
     (tmp/ui_state), а не браузера: смена браузера или домена не должна
     терять «чем и с какого сервера переводим эту книгу». Снимок один на
     книгу и живёт в app.js (UICore.bookState); ключи — те же, что были в
     localStorage, поэтому чтения и записи ниже не изменились. */
  const store = typeof bookState === "function"
    ? bookState(section, name).store : localStorage;

  const st = {
    stage: null, // выбранная стадия: {key, title, script, spec}
    options: null, // динамические опции {chapters, source, prompts, root}
    job: null, // текущий запуск
    log: [], // строки лога текущего запуска (ТОЛЬКО из SSE-стрима, не больше LOG_LIMIT)
    chShown: {}, // что показано в клетках таблицы глав: поток правит только смены
    events: [], // события глав конвейера (стадия 3)
    progress: null, // последнее событие прогресса {label, done, total}
    lastLog: {}, // стадия → последний завершённый запуск (обещание данных)
    gen: 0, // поколение отрисовки — гасит гонки двух render()
    values: {}, // значения формы по стадиям (рабочее состояние книги)
    // значения формы сразу после инициализации (с автоподхватом диапазона
    // глав и prompt_file) — эталон, к которому возвращает «Сбросить настройки»
    baseline: {},
    touched: {}, // менявшиеся пользователем поля по стадиям (Set имён)
    save: {}, // стадия → подпись автосохранения в шапке панели
    preview: null, // epub: данные предпросмотра {entries, source, skips}
    previewDirty: true, // epub: настройки менялись после предпросмотра
    brPreview: null, // batch_replace: предпросмотр {segments, stats, …}
    brChapter: null, // batch_replace: выбранная глава предпросмотра
    brSig: "", // batch_replace: сигнатура формы последнего предпросмотра
    brOn: false, // batch_replace: панель предпросмотра открыта кнопкой
    profiles: [], // профили LLM [{id,name,builtin,…}] — сводка из /api/stages
    pnote: {}, // подпись выбранного профиля по стадиям (узел под селектом)
    promptWarn: {}, // стадия → узел иконки проверки промпта (пуст, пока чист)
    promptReq: {}, // стадия → счётчик проверок: ответ старой проверки не нужен
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
      // лог живёт на вкладке СВОЕЙ стадии запуска — при прикреплении
      // переключаемся на неё; по завершению панель остаётся с финальным
      // статусом (история — на «Дашборде» и в «Логах»)
      st.stage = job.action;
      st.job = job;
      st.log = [];
      st.events = job.events ? [...job.events] : [];
      st.progress = job.progress || null;
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
        await render();
      }
    } catch {
      await render(); // без активного запуска — просто пустая страница
    }
  }

  function setStage(key) {
    st.stage = key;
    // Кэш опций (файлы/главы) — только для текущей стадии:
    // после запуска ner появился ner.json, wiki должна его увидеть
    st.options = null;
    // значения формы — тоже по стадиям: переключение стадии = свежая
    // форма (внутри стадии значения общие для обоих режимов)
    st.values[key] = null;
    st.baseline[key] = null;
    st.touched[key] = null;
    st.preview = null; // epub: свежий предпросмотр для новой стадии
    st.previewDirty = true;
    st.brPreview = null; // batch_replace: предпросмотр замен в главе
    st.brChapter = null;
    st.brSig = "";
    st.brOn = false; // batch_replace: закрытая панель не читает главы
    // активный запуск/стрим НЕ сбрасываем — лог привязан к своей
    // стадии и виден на её вкладке, чужие вкладки его не показывают
    render();
  }

  async function render() {
    const gen = ++st.gen;
    page.replaceChildren();
    // заголовка нет — вкладка живёт в шапке проекта (project-views)
    const body = await runBody();
    if (gen !== st.gen) return; // устаревший рендер — не рисовать
    page.append(body);
  }

  async function runBody() {
    // список стадий + сводка профилей LLM (подпись поля «Профиль LLM»)
    const stages = await api("/stages").catch(() => ({ stages: [] }));
    st.profiles = stages.profiles || [];
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
      h(
        "div",
        { class: "stage-grid" },
        cards.length ? cards : [h("div", { class: "empty" }, "Нет стадий")],
      ),
      await activePanel(),
    );

    // правая панель: форма + лог. Лог — текущий запуск открытой
    // стадии (ЛЮБОЙ статус: после завершения панель остаётся) или
    // последний завершённый запуск ЭТОЙ стадии этого проекта.
    const right = h(
      "div",
      { class: "run-col run-col-form" },
      st.stage ? await formPanel() : emptyRun(),
      await logColumn(),
    );
    return h("div", { class: "run-layout" }, stageList, right);
  }

  // ── профиль LLM стадии ─────────────────────────────────────────────
  // Профиль — ПЕРВОЕ ПОЛЕ формы каждой LLM-стадии: у стадий одного проекта
  // может быть разный выбор (перевод на облачной модели, глоссарий на
  // домашней). Сервер подставляет значения профиля в поля стадии
  // (core.settings.with_llm), своих LLM-полей в форме нет. Выбор живёт в
  // браузере по проекту+стадии, на диске его нет.
  const PROFILE_FIELD = "profile";
  const PROFILE_GENERAL = "general";

  function profileKey(key) {
    return `nmProfile:${section}/${name}/${key}`;
  }

  // память профиля стадии: сохранённый id, если он ещё есть в списке,
  // иначе General (первый вариант) — удалённый профиль не должен оставаться
  // «выбранным про запас»
  function profileStored(key, options) {
    let saved = "";
    try {
      saved = store.getItem(profileKey(key)) || "";
    } catch {
      /* хранилище недоступно — выбор живёт до перезагрузки */
    }
    const list = options && options.length ? options : [PROFILE_GENERAL];
    return list.includes(saved) ? saved : list[0];
  }

  function profileRemember(key, id) {
    try {
      store.setItem(profileKey(key), id);
    } catch {
      /* хранилище недоступно — и нечего сохранять */
    }
  }

  // узел подписи создаётся вместе с полем, обновляется — при выборе
  function profileNote(key) {
    const el = h("div", { class: "run-profile-note" });
    st.pnote[key] = el;
    return el;
  }

  function syncProfileNote(key) {
    const el = st.pnote[key];
    const list = st.profiles || [];
    if (!el || !list.length) return;
    /* /api/stages отдаёт профили сводкой: host/model/reasoning_mode уже
       разложены по именам полей стадии */
    const cur = String((st.values[key] || {})[PROFILE_FIELD] || list[0].id);
    const p = list.find((x) => x.id === cur) || list[0];
    const parts = [p.host || "сервер не задан", p.model || "модель не задана"];
    if (p.reasoning_mode && p.reasoning_mode !== "default") {
      parts.push(`рассуждения: ${p.reasoning_mode}`);
    }
    el.textContent = p.builtin
      ? `${p.name} — значения общего конфига (${parts.join(", ")})`
      : `${p.name} — наследует General (${parts.join(", ")})`;
  }

  // последний завершённый запуск стадии этого проекта (список /jobs
  // отсортирован по created по убыванию — первый подходящий и есть)
  async function lastFinishedJob(key) {
    try {
      const r = await api("/jobs");
      const mine = (r.jobs || []).filter(
        (j) => j.project === `${section}/${name}`
          && j.action === key
          && j.status !== "running",
      );
      return mine.length ? mine[0] : null;
    } catch {
      return null;
    }
  }

  // данные последнего завершённого запуска стадии (кэш по стадии):
  // лог берётся из payload — SSE не нужен, задание уже завершено
  async function lazyLastLog(key) {
    if (!st.lastLog[key]) {
      st.lastLog[key] = (async () => {
        const j = await lastFinishedJob(key);
        if (!j) return null;
        const r = await api(`/jobs/${j.id}`).catch(() => null);
        const job = r && r.job;
        if (!job) return null;
        return {
          job,
          lines: job.lines || [],
          events: job.events || [],
          progress: job.progress || null,
        };
      })();
    }
    return st.lastLog[key];
  }

  // колонка лога: текущий запуск стадии (любой статус) или последний
  // завершённый запуск этой стадии; иначе — заглушка
  async function logColumn() {
    logNode = null; // панель живой стадии ниже вернёт себе узел; чужой — нет
    if (st.stage) {
      if (st.job && st.job.action === st.stage) {
        return logPanel({
          job: st.job,
          lines: st.log,
          events: st.events,
          progress: st.progress,
          live: true,
        });
      }
      const last = await lazyLastLog(st.stage);
      if (last) {
        return logPanel({
          job: last.job,
          lines: last.lines,
          events: last.events,
          progress: last.progress,
          live: false,
        });
      }
    }
    return h(
      "div",
      { class: "run-empty" },
      h("div", { text: "Запустите стадию — лог появится здесь" }),
      h(
        "div",
        { class: "run-empty-hint" },
        "Лог последнего запуска остаётся на вкладке стадии · история — ",
        h("a", { href: "#/dashboard" }, "на Дашборде"),
        " · логи — ",
        h(
          "a",
          { href: `#/project/${section}/${name}/logs` },
          "во вкладке «Логи»",
        ),
      ),
    );
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
    /* Карточка активного запуска: шапка (badge, название, переход/стоп),
       строка «старт …» и строка прогресса под ней. Кнопки — иконки:
       текстовые «Показать» и «Стоп» съедали название запуска, оно уходило
       в многоточие — в колонке 305 px badge, название и две подписанные
       кнопки в одну строку не влезают никогда. */
    const bar = UIC.progressRow({ mini: true });
    st.miniBar = bar;
    bar.paint(UICore.progressParts(j, j.progress));
    const row = h(
      "div",
      { class: "active-run" },
      h(
        "div",
        { class: "active-run-head" },
        h("span", { class: "badge badge-" + j.status }, j.status),
        h("span", { class: "active-run-title" }, j.title || j.action || "запуск"),
        h(
          "span",
          { class: "active-run-actions" },
          h(
            "a",
            {
              class: "btn btn-sm btn-ghost icon-btn",
              // id в URL (не голая вкладка): повторный клик по той же
              // странице перечитает запуск и обновит висящий статус
              href: `#/project/${section}/${name}/run/${j.id}`,
              title: "Показать лог запуска",
              "aria-label": "Показать лог запуска",
            },
            iconEl("eye"),
          ),
          iconBtn(
            "stop",
            "Остановить запуск вместе с потомками",
            async () => {
              try {
                await api(`/jobs/${j.id}/stop`, { method: "POST" });
                toast("Остановка...");
              } catch (ex) {
                toast(ex.message, "err");
              }
            },
            true,
          ),
        ),
      ),
      /* время старта — своей строкой: в шапке оно не мешало бы названию,
         а здесь его всё равно нечем заменить (абсолютное время, как в
         списке логов) */
      h(
        "div",
        { class: "active-run-meta" },
        "старт " + UICore.relTimeAbs(j.created),
      ),
      bar.el,
    );
    panel.append(row);
    return panel;
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
    // значения формы — один раз на выбор стадии (значения реестра и
    // общего конфига + автоподхваты). Пока книга не ответила, форму не
    // собираем: значение, взятое из браузерного зеркала, перекрыло бы
    // чужие свежие правки и уехало бы в запуск (на двух устройствах одна
    // стадия показывала разные диапазоны), а перерисовка уже
    // инициализированную форму не обновляет
    if (!st.values[key]) {
      const carrier = typeof bookState === "function"
        ? bookState(section, name) : null;
      await bookLoaded(carrier);
      initFormValues(key, spec);
    }
    // pipeline: автоподхват «Общего промпт-файла» при КАЖДОМ рендере —
    // файл мог появиться после первой инициализации формы (значения
    // кешируются на страницу); пустой выбор добиваем кандидатом auto
    if (key === "pipeline" && st.options.auto_prompt) {
      const v = st.values[key];
      const pf = (spec.fields || []).find((x) => x.name === "prompt_file");
      const auto = UICore.fileBase(st.options.auto_prompt);
      const pool = (st.options.prompts || []).filter(
        (n) => !(pf && pf.ext || []).length
          || (pf.ext || []).some(
            (e) => n.toLowerCase().endsWith(e.toLowerCase())),
      );
      if (v && pf && !v.prompt_file && pool.includes(auto)) {
        v.prompt_file = auto;
      }
    }
    // files-поля с autofile (epub: исходник; compile: обложка/метаданные/
    // донат): добор кандидата при КАЖДОМ рендере — файл мог появиться после
    // первой инициализации формы (загрузка через «Файлы»); осознанный
    // выбор пользователя (касание поля, вкл. «—») не перекрывается
    if (st.values[key]) {
      const vals = st.values[key];
      const touched = st.touched[key] || new Set();
      for (const f of spec.fields || []) {
        if (f.type !== "files" || !f.autofile || vals[f.name]) continue;
        if (touched.has(f.name)) continue;
        const auto = UICore.autofileValue(f, st.options);
        if (auto) vals[f.name] = auto; // значение — имя файла, не путь
      }
    }
    // Одна форма на стадию: «Простой/Экспертный» и пресеты убраны, все
    // поля идут из реестра настроек (spec.fields)
    const panel = h(
      "div",
      { class: "run-panel" },
      h(
        "div",
        { class: "run-panel-head" },
        h("div", { class: "run-panel-title" }, `${key} · ${spec.title}`),
        h("div", { class: "run-panel-tools" },
          runSaveState(key),
          resetSettingsBtn(key, spec)),
      ),
      stageForm(key, spec),
    );
    // смена поля не перерисовывает форму (иначе теряется фокус), поэтому
    // автосохранение и кнопка «Сбросить настройки» пересчитываются делегатом:
    // слушатель поля срабатывает раньше и уже записал значение в st.values
    st.head = panel.querySelector(".run-panel-head");
    st.curSpec = spec;
    const onEdit = () => {
      scheduleRunSave(key);
      st.previewDirty = true; // epub: панель разбивки помечает себя устаревшей
    };
    panel.addEventListener("input", onEdit);
    panel.addEventListener("change", onEdit);
    syncRunSave(key); // подпись сразу показывает то, что уже сохранено
    promptCheck(key); // проверка промпта при открытии формы стадии
    // epub: панель предпросмотра разбивки
    if (key === "epub") panel.append(epubPreviewPanel(key, spec));
    // batch_replace: панель предпросмотра замен по главам
    if (key === "batch_replace") {
      const br = batchReplacePreviewPanel(key);
      panel.append(br.el);
      br.mount(panel.querySelector(".run-form") || panel);
    }
    return panel;
  }

  // ── значения формы ждут ответа книги ───────────────────────────
  // Форма собирается ОДИН раз, а перерисовка её не обновляет, поэтому
  // стартовать с браузерного зеркала нельзя: на двух устройствах одна и та же
  // стадия показывала разные диапазоны, а выбор «пустоты» уезжал в запуск.
  // Ждём, пока снимок книги ответит (успех или отказ — иначе решение не
  // примет), но не дольше BOOK_WAIT_TIMEOUT: без сети форма открывается по
  // тому, что есть.
  const BOOK_WAIT_TIMEOUT = 5000; // мС ожидания ответа книги
  const BOOK_WAIT_STEP = 25; // мС между проверками готовности

  function bookLoaded(carrier) {
    if (!carrier || typeof carrier.loaded !== "function") {
      return Promise.resolve();
    }
    const until = Date.now() + BOOK_WAIT_TIMEOUT;
    return new Promise((done) => {
      const wait = () => {
        if (carrier.loaded() || Date.now() >= until) done();
        else setTimeout(wait, BOOK_WAIT_STEP);
      };
      wait();
    });
  }

  // ── автосохранение значений формы (состояние книги, по стадии) ──
  // Значения запусков — состояние КНИГИ (tmp/ui_state): своего .env у неё нет, а в
  // общем конфиге им делать нечего (диапазон глав одной книги не должен
  // становиться настройкой всех книг). Кнопки «Сохранить» в форме нет: поле
  // изменили — через паузу ввода значения уходят в снимок книги и на диск, о чём
  // говорит подпись в шапке панели. Храним только изменённые поля, остальное
  // приходит из реестра; «Сбросить настройки» возвращает значения конфига.
  const RUN_SAVE_DELAY = 500; // мС тишины после последнего изменения поля
  const runTimers = {};       // стадия → таймер отложенной записи
  const runPending = {};      // стадия → запись ещё не ушла в память
  const runFailed = {};       // стадия → память браузера недоступна

  function runKey(key) {
    return `runVals:${section}/${name}:${key}`;
  }

  function runSave(key) {
    const t = st.touched[key];
    if (!t) return true;
    try {
      if (!t.size) {
        store.removeItem(runKey(key));
        return true;
      }
      const vals = st.values[key] || {};
      const data = {};
      for (const n of t) if (vals[n] !== undefined) data[n] = vals[n];
      store.setItem(runKey(key), JSON.stringify(data));
      return true;
    } catch {
      return false; // хранилище недоступно — сохранять некуда
    }
  }

  // одна запись на пачку изменений: на каждое нажатие клавиши писать в память
  // нельзя, поэтому отсчёт начинается заново на каждый сигнал. Кнопка
  // «Сбросить настройки» пересчитывается здесь же: форма не пересобирается
  function scheduleRunSave(key) {
    runPending[key] = true;
    syncRunSave(key);
    syncResetBtn(key);
    clearTimeout(runTimers[key]);
    runTimers[key] = setTimeout(() => {
      runFailed[key] = !runSave(key);
      runPending[key] = false;
      syncRunSave(key);
    }, RUN_SAVE_DELAY);
  }

  // узел подписи ПОСТОЯННЫЙ (прячется классом hidden): пересоздавать его
  // нельзя — blur предыдущего поля даёт change между mousedown и mouseup,
  // старый узел отрывается от DOM и клик уходит в родителя
  function runSaveState(key) {
    if (!st.save[key]) {
      st.save[key] = h("span", { class: "run-save-state hidden" });
      attachTooltip(st.save[key],
        "значения запусков — рабочее состояние браузера этой книги: своего .env "
        + "у неё нет, а в общий конфиг они не пишутся");
    }
    return st.save[key];
  }

  function syncRunSave(key) {
    const el = st.save[key];
    if (!el) return;
    // те же отличия, что считает кнопка сброса: правка «в ту же цифру»
    // подписью не мешает (тouched остаётся основой для записи в память)
    const n = st.curSpec ? resetDiffs(key, st.curSpec).length : 0;
    el.textContent = !n ? "" : runPending[key]
      ? `изменено ${n} · сохраняю…`
      : runFailed[key]
        ? "память браузера недоступна — значения живут до перезагрузки"
        : `изменено ${n} · сохранено`;
    el.classList.toggle("hidden", !n);
  }

  function runRestore(key, spec, vals) {
    let saved = null;
    try {
      saved = JSON.parse(store.getItem(runKey(key)) || "null");
    } catch {
      /* повреждённый JSON — значения реестра */
      return;
    }
    if (!saved || typeof saved !== "object") return;
    for (const f of spec.fields || []) {
      if (f.name === PROFILE_FIELD) continue; // память профиля — своя, по стадии
      if (saved[f.name] === undefined) continue;
      vals[f.name] = f.type === "bool"
        ? UICore.boolOn(saved[f.name]) : saved[f.name];
      st.touched[key].add(f.name);
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
        // autofile: автоподхват файла из пула (epub: первый *.epub из source/,
        // donate.txt и т.п.); не перекрываем память из .env — только пустой выбор
        if (vals[f.name] === "") {
          const auto = UICore.autofileValue(f, opts);
          if (items.includes(auto)) vals[f.name] = auto;
        }
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
      }
    }
    st.values[key] = vals;
    st.touched[key] = new Set();
    // эталон «значений конфига» снимается после автоподхвата из ОПЦИЙ
    // КНИГИ (диапазон глав, prompt_file): иначе автозаполненные главы
    // вечно считались бы изменёнными. До восстановления ЛИЧНОЙ памяти
    // (снимка книги): иначе «Сбросить настройки» возвращал бы не значения
    // конфига, а то, что было при прошлом визите (baseline уже включил
    // бы сохранённые значения) — именно поэтому сброс «до предыдущего».
    st.baseline[key] = Object.assign({}, vals);
    // память изменённых полей этой книги+стадии (поверх реестра и .env)
    runRestore(key, spec, vals);
    runPending[key] = false;
    // чипсы полей/типов (hidden noenv) — память по проекту+стадии:
    // без неё перезагрузка страницы молча сбрасывает выбор чипсов
    // на дефолт, и запуск уходит не с теми полями, что показаны
    chipRestore(key, spec, vals);
    // профиль LLM — память по проекту+стадии (у стадий одного проекта выбор
    // свой); снимок эталона снимается ПОСЛЕ него, чтобы память не выглядела
    // «изменённым полем» и не уезжала под кнопку «Сбросить настройки»
    const pf = (spec.fields || []).find((x) => x.name === PROFILE_FIELD);
    if (pf) vals[PROFILE_FIELD] = profileStored(key, pf.options);
  }

  // ── память выбора чипсов (hidden noenv: types/fields/ner_fields) ──
  // снимок книги per стадия: значения «невидимых» полей,
  // которые пишет виджет чипсов; терять их при перезагрузке — тихий
  // расхождение «что видно ≠ что уйдёт в запуск»
  function chipKey(key) {
    return `chips:${section}/${name}:${key}`;
  }

  function chipRestore(key, spec, vals) {
    try {
      const saved = JSON.parse(store.getItem(chipKey(key)) || "{}");
      if (!saved || typeof saved !== "object") return;
      for (const f of spec.fields || []) {
        if (f.type !== "hidden" || !f.noenv) continue;
        const v = saved[f.name];
        if (typeof v === "string" && v) {
          vals[f.name] = v;
          st.touched[key].add(f.name);
        }
      }
    } catch {
      /* повреждённый JSON — дефолты */
    }
  }

  function saveChips(key, names) {
    const vals = st.values[key] || {};
    const data = {};
    for (const n of names) {
      const v = vals[n];
      if (typeof v === "string" && v) data[n] = v;
    }
    try {
      store.setItem(chipKey(key), JSON.stringify(data));
    } catch {
      /* приватный режим браузера — живём без памяти */
    }
  }

  // файл → путь внутри проекта: голое имя + dir → "dir/имя"
  function finalFile(f, v) {
    if (f.type === "files" && f.dir && v && !String(v).includes("/")) {
      return `${f.dir}/${v}`;
    }
    return v;
  }

  // params запуска: все непустые значения формы (vals уже включают значения
  // реестра/общего конфига + автоподхваты диапазона глав и autofile)
  function buildParams(key, spec) {
    const vals = st.values[key] || {};
    const p = {};
    for (const f of spec.fields || []) {
      const v = vals[f.name];
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

  // ── «Сбросить настройки» ───────────────────────────────────────────
  // Поля стадии возвращаются к значениям общего конфига: значения формы
  // живут в браузере, на диске писать нечего. Тултип показывает каждое
  // поле как «сейчас → значение конфига».
  //
  // Узёл КНОПКИ ПОСТОЯННЫЙ (прятется, когда отличий нет): пересоздавать
  // его нельзя — blur предыдущего поля даёт change между mousedown и
  // mouseup, старый узел отрывается от DOM и клик уходит в родителя.
  function resetDiffs(key, spec) {
    const vals = st.values[key] || {};
    // эталон — значения формы сразу после инициализации (они уже включают
    // автоподхват диапазона глав и prompt_file, а не пустые дефолты спеки)
    const base = st.baseline[key] || {};
    const diffs = [];
    for (const f of spec.fields || []) {
      if (f.name === PROFILE_FIELD) continue; // память профиля — своя, по стадии
      const now = vals[f.name];
      const want = base[f.name];
      const same = f.type === "bool"
        ? Boolean(now) === Boolean(want)
        : String(now == null ? "" : now)
          === String(want == null ? "" : want);
      if (!same) {
        diffs.push(`${f.label || f.name}: ${String(now ?? "") || "∅"} → `
          + `${String(want == null ? "" : want) || "∅"}`);
      }
    }
    return diffs;
  }

  function resetSettingsBtn(key, spec) {
    const btn = h("button", { class: "btn btn-ghost btn-sm run-reset hidden" });
    btn.addEventListener("click", () => {
      const diffs = resetDiffs(key, spec);
      if (!diffs.length) return;
      confirmModal(
        "Сбросить настройки",
        `${key}: ${diffs.length} поле(й) вернётся к значениям общего `
          + "конфига:\n" + diffs.join("\n"),
        "СБРОСИТЬ",
        () => {
          try {
            store.removeItem(chipKey(key)); // память чипсов стадии
            store.removeItem(runKey(key)); // память полей стадии
          } catch {
            /* хранилище недоступно — и нечего было чистить */
          }
          delete st.values[key]; // значения формы — заново из реестра
          delete st.touched[key];
          clearTimeout(runTimers[key]); // отложенное сохранение не вернёт стёртое
          initFormValues(key, spec);
          syncRunSave(key);
          toast("Поля стадии возвращены к значениям конфига");
          render(); // поля — заново из реестра, кнопка прячется
        },
      );
    });
    return btn;
  }

  // пересчёт кнопки без перестройки полей (в инпуте держим фокус)
  function syncResetBtn(key) {
    const btn = st.head && st.head.querySelector(".run-reset");
    if (!btn) return;
    const diffs = resetDiffs(key, st.curSpec);
    btn.classList.toggle("hidden", diffs.length === 0);
    btn.textContent = diffs.length ? `Сбросить настройки (${diffs.length})` : "";
    btn.title = diffs.length
      ? `Вернуть поля стадии к значениям общего конфига `
        + `(${diffs.length}):\n${diffs.join("\n")}`
      : "";
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
      // подсказка — тултип при наведении, чтобы не ломала строку
      const wrap = h(
        "label",
        { class: "field field-check" },
        input,
        label,
      );
      if (f.help) attachTooltip(wrap, f.help);
      wrap._input = input;
      wrap._field = f; // видимость подрежимов идёт по fieldWraps[name]
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
        if (f.name === PROFILE_FIELD) {
          profileRemember(key, input.value);
          syncProfileNote(key);
        }
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
      // regex/chunk: epub + txt; zip не принимается ни в одном режиме
      if (key === "epub" && f.name === "input") {
        const m = String(vals["mode"] || "toc");
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
      // загрузка своего файла / редактирование выбранного (текстовые:
      // промпты, метаданные YAML, страница поддержки)
      const row = h("div", { class: "field-row" }, input);
      const upInput = h("input", { type: "file", class: "hidden" });
      // редактируемые файлы: промпты (dir=prompts, .txt) и поля с
      // флагом editable в спеке (compile: метаданные/донат — правка
      // выбранного файла прямо из запуска, «Редактировать» вместо
      // «Загрузить»). Промпта в книге нет — вместо загрузки своего файла
      // предлагается готовый из шаблона: стадия работает без правок, а
      // свои файлы кладут вкладкой «Файлы»
      const isPrompt = dir === "prompts" && (f.ext || []).includes(".txt");
      const isEditable = isPrompt || Boolean(f.editable);
      const upBtn = h(
        "button",
        { class: "btn btn-sm btn-ghost" },
        "Загрузить",
      );
      function refreshPromptBtn() {
        if (!isEditable) {
          upBtn.textContent = "Загрузить";
          return;
        }
        upBtn.textContent = sel.value
          ? "Редактировать"
          : isPrompt ? "Из шаблона" : "Загрузить";
      }
      upBtn.addEventListener("click", () => {
        if (isEditable && sel.value) {
          // промпты: editFileModal ждёт имя файла в prompts/ (API
          // /api/prompts/{name}); прочие — путь внутри проекта
          // (dir/имя, напр. source/metadata.yaml)
          const relPath = dir === "prompts" ? sel.value
            : dir ? `${dir}/${sel.value}` : sel.value;
          editFileModal(relPath, {
            prompt: dir === "prompts",
            title: f.label ? `${f.label}: ${sel.value}` : relPath,
          });
        } else if (isPrompt) {
          // новый промпт — сразу в книге: render перечитает список файлов,
          // а форма перепроверит промпт (promptCheck в formPanel)
          promptFromTemplateModal(key, f, (created) => {
            vals[f.name] = created;
            touched.add(f.name);
            scheduleRunSave(key); // выбор файла — в состояние книги
            toast(`Промпт из шаблона: ${created}`);
            render();
          });
        } else {
          upInput.click();
        }
      });
      sel.addEventListener("change", () => {
        refreshPromptBtn();
        if (isEditable && dir === "prompts") promptCheck(key);
      });
      upInput.addEventListener("change", async () => {
        if (!upInput.files || !upInput.files.length) return;
        const form = new FormData();
        // dir="" (поля корня проекта — wiki file и т.п.) —
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
      if (isEditable && dir === "prompts") {
        // проверка промпт-файла: у узла нет содержимого, пока замечаний нет
        st.promptWarn[key] = h("span", { class: "run-prompt-warn" });
        row.append(st.promptWarn[key]);
      }
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
    // профиль стадии: под селектом — подпись выбранного профиля (сервер,
    // модель, рассуждения), чтобы выбор был виден без раскрытия списка
    if (f.name === PROFILE_FIELD) {
      wrap.classList.add("field-profile");
      wrap.append(profileNote(key));
      syncProfileNote(key);
    }
    // Сложные контролы (select/textarea/files) — тултип при наведении;
    // inline-подсказка остаётся только у простых text/number.
    // brief — короткая подпись под полем, полный help уходит в тултип
    // (длинная справка под полем растягивала форму на пол-экрана).
    if (f.help || f.brief) {
      const complex = f.type === "select" || f.type === "textarea"
        || f.type === "files";
      if (f.brief && f.help) {
        wrap.append(h("div", { class: "field-help" }, f.brief));
        attachTooltip(label, f.help);
      } else if (complex) {
        attachTooltip(wrap, f.help);
      } else if (f.help) {
        wrap.append(h("div", { class: "field-help" }, f.help));
      }
    }
    wrap._input = input;
    wrap._field = f; // видимость подрежимов идёт по fieldWraps[name]
    return wrap;
  }

  // ── ner_check: чипсы типов из глоссария (вместо select/text) ──
  // Чипсы грузят типы из глоссария (GET /api/ner); выбор проходов —
  // обычный select «Проходы».
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
    let allItems = []; // записи ner.json — динамические счётчики (унификация с wiki)
    // настройки счётчиков: ner_check — «Порог count»; wiki —
    // «Мин. частота термина» и «Макс. терминов». Обновляются извне
    // (updateCounts) при изменении полей формы — как в wiki
    let threshold = 0;  // ner_check: записи с count > порога (0 — без фильтра)
    let minCount = 0;   // wiki: записи с count >= порога и translation
    let top = 0;        // wiki: потолок терминов (top-N по count)
    const isWiki = key === "wiki";
    const _intOr = (v, def) => {
      const n = parseInt(v, 10);
      return Number.isFinite(n) ? n : def;
    };
    // базовый тип как в wiki.py::_get_base_type: хвост «(…)» срезается,
    // известные типы нормализуются — выбор чипсов = белый список --types
    const WIKI_TYPE_MAP = {
      "Base Term": "Other",
      "Sect": "Organisation",
      "Cultivation Stage": "Stage",
      "Location (Body)": "Location",
    };
    const baseType = (t) => {
      const s = String((t == null ? "" : t)).trim();
      const b = s.replace(/\s*\(.*?\)\s*$/, "").trim();
      return WIKI_TYPE_MAP[b] || b || "Other";
    };
    // тип записи для чипса: wiki — базовый (как в CLI), ner_check —
    // сырой (точное сравнение в filter_ner_items); «?» — как в /api/ner
    const chipType = (it) =>
      isWiki ? baseType(it.type) : String(it.type || "?");
    // проходит ли запись порог стадии (семантика фильтра скрипта)
    function passFilter(it) {
      const cnt = Number(it.count) || 0;
      if (isWiki) {
        return cnt >= minCount && String(it.translation || "").trim() !== "";
      }
      return threshold <= 0 || cnt > threshold;
    }
    // записи, которые реально уйдут в запрос, — тот же порядок, что в
    // скрипте: белый список типов → порог count → у wiki ещё срез top-N по
    // count (у ner_check потолка нет). Считается один раз на пересчёт:
    // отсюда и числа на чипсах, и строка-счётчик
    function chosenItems() {
      const sel = curTypes();
      let pool = allItems.filter((it) => it && typeof it === "object");
      if (sel) {
        pool = pool.filter((it) =>
          sel.includes(isWiki ? baseType(it.type) : String(it.type || "?")));
      }
      pool = pool.filter(passFilter);
      if (!isWiki) return pool;
      return pool
        .slice()
        .sort((a, b) => (Number(b.count) || 0) - (Number(a.count) || 0))
        .slice(0, top);
    }
    // счётчик чипса: сколько терминов этого типа реально уйдёт в запрос
    function countFor(chosen, t) {
      let n = 0;
      for (const it of chosen) {
        if (chipType(it) === t) n++;
      }
      return n;
    }
    // null = все типы (пустое значение), список — выбранные
    function curTypes() {
      const v = String(st.values[key]["types"] ?? "");
      return v
        ? v.split(",").map((s) => s.trim()).filter(Boolean)
        : null;
    }
    function renderChips() {
      const cur = curTypes();
      const chosen = chosenItems();
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
          saveChips(key, ["types", "fields"]);
          renderChips();
          updateInfo();
        });
        const n = countFor(chosen, t);
        chipsBox.append(
          h(
            "label",
            { class: "ner-chip" },
            cb,
            ` ${t} (${n})`,
          ),
        );
      }
    }
    // строка-счётчик над чипсами: сколько терминов уйдёт в запрос стадии;
    // у wiki числа на чипсах считаются уже срезом top-N, поэтому сюда же
    // вынесены оба влияющих параметра
    function updateInfo() {
      if (!allItems.length) return; // «Глоссарий пуст…» не перезаписываем
      const total = allItems.filter(
        (it) => it && typeof it === "object").length;
      chipsInfo.textContent =
        `${typeNames.length} тип(ов) · ${chosenItems().length} из ${total} `
        + `терминов попадут в ${isWiki ? "запрос" : "проверку"}`
        + (isWiki ? ` (топ-${top} по count, порог ≥${minCount})` : "")
        + ` · пусто = все типы`;
    }
    // настройки изменились (поле формы) — пересчитать чипсы и счётчик
    function updateCounts(opts = {}) {
      if (opts.threshold != null) {
        threshold = Math.max(0, _intOr(opts.threshold, 0));
      }
      if (opts.minCount != null) {
        minCount = Math.max(0, _intOr(opts.minCount, 2));
      }
      if (opts.top != null) {
        top = Math.max(1, _intOr(opts.top, 80));
      }
      renderChips();
      updateInfo();
    }
    selAll.addEventListener("click", () => {
      st.values[key]["types"] = "";
      st.touched[key].add("types");
      saveChips(key, ["types", "fields"]);
      renderChips();
      updateInfo();
    });
    selNone.addEventListener("click", () => {
      if (typeNames.length) {
        // минимум один тип (как в глоссарии) — снять все нельзя
        st.values[key]["types"] = typeNames[0];
        st.touched[key].add("types");
        saveChips(key, ["types", "fields"]);
        renderChips();
        updateInfo();
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
    // term, type и translation — всегда в запросе (чекбоксы
    // неотключаемые, как в {ner_block} конвейера): без типа и перевода
    // проверять нечего
    const FIELD_ORDER = ["type", "translation", "pinyin", "reading",
      "context", "translated_context", "notes", "aliases"];
    const BASE_FIELDS = ["term", "type", "translation"];
    // факультативные поля, включённые по умолчанию (если они есть в данных)
    const DEFAULT_FIELDS = ["notes", "context"];
    const fieldsBar = h("div", { class: "ner-chips-bar" });
    const fieldsInfo = h("div", { class: "field-help" });
    const fieldsBox = h("div", { class: "ner-chips-box" });
    let fieldNames = [];
    // дефолт: базовые поля + те факультативные, что есть в ner.json
    function defaultFields() {
      return [...BASE_FIELDS,
        ...DEFAULT_FIELDS.filter((n) => fieldNames.includes(n))];
    }
    function curFields() {
      let v = String(st.values[key]["fields"] ?? "").trim();
      if (!v) {
        // касание обязательно — в простом режиме уходит именно он
        v = defaultFields().join(",");
        st.values[key]["fields"] = v;
        st.touched[key].add("fields");
      }
      // базовые поля дописываются всегда: снять их нельзя
      const set = new Set(BASE_FIELDS);
      for (const f of v.split(",")) {
        if (f.trim()) set.add(f.trim());
      }
      return set;
    }
    function renderFields() {
      const cur = curFields();
      fieldsBox.replaceChildren();
      for (const name of ["term", ...fieldNames]) {
        const isBase = BASE_FIELDS.includes(name); // неотключаемые
        const cb = h("input", { type: "checkbox", class: "checkbox" });
        cb.checked = isBase || cur.has(name);
        cb.disabled = isBase;
        if (!isBase) {
          cb.addEventListener("change", () => {
            const set = new Set(curFields());
            if (cb.checked) set.add(name);
            else set.delete(name); // минимум — базовые (неотключаемые)
            st.values[key]["fields"] = [...set].join(",");
            st.touched[key].add("fields");
            saveChips(key, ["types", "fields"]);
            renderFields();
          });
        }
        fieldsBox.append(
          h(
            "label",
            { class: "ner-chip" + (isBase ? " ner-chip-term" : "") },
            cb,
            ` ${name}` + (isBase ? " (всегда)" : ""),
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
        saveChips(key, ["types", "fields"]);
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
        allItems = d.items || [];
        // wiki — базовые типы (белый список --types сравнивается
        // с _get_base_type в CLI); ner_check — сырые значения type
        typeNames = isWiki
          ? [...new Set(allItems.map((it) =>
              it && typeof it === "object" ? baseType(it.type) : "Other"))].sort()
          : Object.keys(d.by_type || {}).sort();
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
        renderChips();
        updateInfo();
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
        // сохранённый/устаревший выбор не переживает поля, которых
        // больше нет в ner.json (зеркально ner_block ниже); снятое
        // факультативное поле возвращается только базовым набором
        const v = String(st.values[key]["fields"] ?? "").trim();
        if (v) {
          const ok = v.split(",").map((s) => s.trim())
            .filter((s) => s === "term" || fieldNames.includes(s));
          st.values[key]["fields"] = [...new Set([...BASE_FIELDS, ...ok])]
            .join(",");
          saveChips(key, ["types", "fields"]);
        }
        fieldsInfo.textContent =
          "term, type, translation — всегда; по умолчанию: notes, "
          + "context (если есть в данных)";
        renderFields();
      } catch (ex) {
        chipsInfo.textContent = ex.message;
      }
    };
    return { chipsBar, chipsBox, guide, loadTypes, fieldsBar, fieldsBox,
             updateCounts };
  }

  // чипсы полей {ner_block} (конвейер): term, type, translation — всегда
  // (без них блок теряет смысл); aliases — включён по умолчанию, но
  // отключаемый (алиасы в блоке не всем нужны). Список полей — как в
  // ner_check: базовые всегда, остальные — ключи из реальных данных
  // ner.json проекта
  function nerBlockChips(key) {
    const FIELD_ORDER = ["type", "translation", "aliases", "pinyin",
      "reading", "context", "translated_context", "notes"];
    const bar = h("div", { class: "ner-chips-bar" });
    const info = h("div", { class: "field-help" });
    const selAll = h(
      "button",
      { class: "btn btn-xs btn-ghost", type: "button" },
      "Выбрать все",
    );
    const box = h("div", { class: "ner-chips-box" });
    let fieldNames = [];
    function curFields() {
      const v = String(st.values[key]["ner_fields"] ?? "").trim();
      const set = new Set(v.split(",").map((s) => s.trim())
        .filter((s) => s && s !== "term"));
      set.add("term"); // термин — всегда в блоке
      // type/translation не снимаются: без них блок глоссария теряет смысл;
      // aliases — обычный выбор: включён дефолтом, отжатие сохраняется
      set.add("type");
      set.add("translation");
      return set;
    }
    function render() {
      const cur = curFields();
      box.replaceChildren();
      for (const name of ["term", ...fieldNames]) {
        const isTerm = name === "term" || name === "type"
          || name === "translation";
        const cb = h("input", { type: "checkbox", class: "checkbox" });
        cb.checked = isTerm || cur.has(name);
        cb.disabled = isTerm; // неотключаемые
        if (!isTerm) {
          cb.addEventListener("change", () => {
            const set = new Set(curFields());
            if (cb.checked) set.add(name);
            else set.delete(name); // минимум — базовые (неотключаемые)
            st.values[key]["ner_fields"] = [...set].join(",");
            st.touched[key].add("ner_fields");
            saveChips(key, ["ner_fields"]);
            render();
          });
        }
        box.append(
          h(
            "label",
            { class: "ner-chip" + (isTerm ? " ner-chip-term" : "") },
            cb,
            ` ${name}` + (isTerm ? " (всегда)" : ""),
          ),
        );
      }
    }
    selAll.addEventListener("click", () => {
      if (fieldNames.length) {
        st.values[key]["ner_fields"] = ["term", ...fieldNames].join(",");
        st.touched[key].add("ner_fields");
        saveChips(key, ["ner_fields"]);
        render();
      }
    });
    bar.append(
      h("span", {}, "Поля терминов в {ner_block}:"),
      selAll,
      h("span", { class: "spacer" }),
      info,
    );
    const loadFields = async () => {
      info.textContent = "Загрузка полей из глоссария…";
      try {
        const d = await api(`/ner?project=${section}/${name}`);
        const present = new Set();
        for (const it of d.items || []) {
          if (!it || typeof it !== "object") continue;
          for (const k of Object.keys(it)) {
            if (!k.startsWith("_")) present.add(k);
          }
        }
        // базовые type/translation/aliases — всегда (стандарт глоссария),
        // остальные — ключи из данных; ЛЮБОЙ новый ключ — в конец
        fieldNames = [
          ...FIELD_ORDER.filter(
            (n) => n === "type" || n === "translation"
              || n === "aliases" || present.has(n),
          ),
          ...[...present]
            .filter((k) => !FIELD_ORDER.includes(k) && k !== "term")
            .sort(),
        ];
        // устаревшие значения не остаются выбранными; сохранённый выбор
        // (включая отжатый aliases) не трогаем, пустой — дефолт реестра:
        // term + type + translation + aliases (если есть в глоссарии)
        const v = String(st.values[key]["ner_fields"] ?? "").trim();
        if (v) {
          const ok = v.split(",").map((s) => s.trim())
            .filter((s) => s === "term" || fieldNames.includes(s));
          st.values[key]["ner_fields"] =
            [...new Set([...ok, "term", "type", "translation"])].join(",");
        } else {
          const base = ["term", "type", "translation"]
            .concat(present.has("aliases") ? ["aliases"] : []);
          st.values[key]["ner_fields"] = base.join(",");
        }
        info.textContent =
          "term, type и translation — всегда; aliases включён по умолчанию — "
          + "отожмите, если алиасы в блоке не нужны";
        render();
      } catch (ex) {
        // глоссария нет: базовые поля + aliases (дефолт реестра);
        // отжатие aliases в сохранённом выборе не трогаем
        fieldNames = ["type", "translation", "aliases"];
        const saved = String(st.values[key]["ner_fields"] ?? "").trim();
        st.values[key]["ner_fields"] = saved
          ? [...new Set([...saved.split(",").map((s) => s.trim())
              .filter(Boolean), "term", "type", "translation"])].join(",")
          : ["term", "type", "translation", "aliases"].join(",");
        info.textContent =
          `Глоссарий не загружен (${ex.message}) — базовые поля`;
        render();
      }
    };
    return { bar, box, loadFields };
  }

  // «Предпросмотр запроса» (LLM-стадии): POST
  // /stages/{key}/preview-request — синхронная эмуляция LLM-запросов стадии
  // без сети; модалка показывает messages и статистику символов из payload.
  // У стадии «Оценка перевода» чанков запросов два (чанк и свёртка) — они
  // приезжают в payload.requests, каждый со своими messages и статистикой.
  async function previewRequestModal(key, spec) {
    const err = h("div", { class: "form-error" });
    const host = h("div", { class: "preview-req-body" });
    UIC.modal({
      title: "Предпросмотр запроса",
      wide: true,
      build: (close) => [err, host, h(
        "div", { class: "modal-actions" },
        h("button", { class: "btn btn-ghost", onclick: () => close() },
          "Закрыть"),
      )],
    });
    host.append(h("div", { class: "preview-req-head" }, "Формирование запроса…"));
    try {
      const d = await api(`/stages/${key}/preview-request`, {
        method: "POST",
        body: {
          project: `${section}/${name}`,
          params: buildParams(key, spec),
        },
      });
      host.replaceChildren(previewRequestView(d));
    } catch (ex) {
      err.textContent = ex.message;
      host.replaceChildren();
    }
  }

  // содержимое модалки предпросмотра: сводка (модель, plan), затем каждый
  // запрос своей строкой статистики и сообщениями с полными текстами
  function previewRequestView(d) {
    const nodes = [];
    const reqs = (Array.isArray(d.requests) && d.requests.length)
      ? d.requests
      : [{
        label: d.label || d.stage || "",
        messages: d.messages || [],
        chars: d.chars || {},
        tokens: d.tokens || {},
      }];
    nodes.push(h(
      "div", { class: "preview-req-head" },
      `${d.stage || ""} · модель: ${d.model || "?"}`
      + ` · запросов: ${reqs.length}`,
    ));
    const meta = d.meta && Object.keys(d.meta).length
      ? Object.entries(d.meta).map(([k, v]) => `${k}: ${v}`).join(" · ")
      : null;
    if (meta) {
      nodes.push(h("div", { class: "preview-req-head" }, meta));
    }
    for (const r of reqs) {
      const chars = r.chars || {};
      const tokens = r.tokens || {};
      // своя строка статистики + что именно пошло в запрос (главы чанка,
      // размер, число сводок свёртки)
      const rmeta = r.meta && Object.keys(r.meta).length
        ? Object.entries(r.meta).map(([k, v]) => `${k}: ${v}`).join(" · ")
        : "";
      nodes.push(h(
        "div", { class: "preview-req-msg" },
        h(
          "div", { class: "preview-req-role" },
          `${r.label || ""} · символов: user ${chars.user ?? "?"}`
          + (chars.system ? `, system ${chars.system}` : "")
          + (chars.total == null ? "" : ` (всего ${chars.total})`)
          + (tokens.total == null ? "" : ` · токенов ~${tokens.total}`)
          + (rmeta ? ` · ${rmeta}` : ""),
        ),
        ...(r.messages || []).map((m) => h(
          "div", null,
          h("div", { class: "preview-req-role" }, m.role || "?"),
          // теги секций, подстановки и ключи JSON размечены (UICore.promptParts)
          h(
            "pre", { class: "preview-req-text" },
            ...UICore.promptParts(m.content).map((pt) => (pt.cls
              ? h("span", { class: pt.cls }, pt.text)
              : pt.text)),
          ),
        )),
      ));
    }
    return h("div", null, nodes);
  }

  /* ── проверка промпт-файла (--check-prompt) ───────────────────────────
   * Какие теги и переменные читает стадия, знает её скрипт: сервер запускает
   * его на этой книге с --check-prompt (без сети, без логов запусков и без
   * артефактов стадии) и возвращает вердикт. Иконка у кнопки «Редактировать»
   * — только настоящие проблемы: нет тега режима, нет переменной в тексте,
   * битый JSON. «внешний промпт не выбран» проблемой не считается: стадия
   * честно работает со встроенным промптом. Проверка фоновая и молчаливая:
   * не удалось — иконка просто не появляется. */
  function paintPromptWarn(key, problems, title) {
    const slot = st.promptWarn[key];
    if (!slot) return;
    // replaceChildren, а не textContent = "": узел держит иконку элементом
    slot.replaceChildren();
    const list = (problems || []).filter(Boolean);
    if (!list.length) return;
    // текст тултипа читается один раз при навешивании — узел создаём заново
    const tip = `Проверка промпта «${title || key}», замечаний: ${list.length}`
      + "\n" + list.join("\n");
    const el = iconEl("alert-triangle", "run-prompt-warn-icon");
    el.tabIndex = 0;
    el.setAttribute("aria-label", tip);
    attachTooltip(el, tip);
    slot.append(el);
  }

  // проверка запускается при открытии формы стадии, смене выбранного файла и
  // сохранении в редакторе; сервер держит вердикт по (книга, стадия, mtime).
  // параметры — те же, что у обычного запуска: buildParams (finalFile),
  // иначе скрипт получит голое имя вместо «prompts/имя» и не найдёт файл
  function promptCheck(key) {
    const spec = st.curSpec;
    if (!key || !st.promptWarn[key] || !spec) return;
    const mine = (st.promptReq[key] || 0) + 1;
    st.promptReq[key] = mine;
    api(`/stages/${key}/prompt-check`, {
      method: "POST",
      body: { project: `${section}/${name}`, params: buildParams(key, spec) },
    }).then((d) => {
      if (st.promptReq[key] !== mine) return; // ответ устарел
      paintPromptWarn(key, (d && d.result && d.result.problems) || [],
        spec.title);
    }).catch(() => { /* фоновая проверка не ломает форму */ });
  }

  /* Промпт-файл из шаблона: поле «Файл промпта» пустое (в prompts/ книги
     нужного файла нет) — вместо загрузки своего файла копируем готовый из
     любого набора templates/*. Наборы и их файлы отдаёт GET /api/prompts
     (template_sets), содержимое одного файла — GET /api/prompts/{name}/template
     (по наборам). Колбэк получает имя созданного файла: поле выбирает его. */
  function promptFromTemplateModal(key, f, onDone) {
    const err = h("div", { class: "form-error" });
    const setSel = h("select", { class: "input" });
    const fileSel = h("select", { class: "input" });
    const info = h("div", { class: "card-hint" });
    const existing = new Set((st.options || {}).prompts || []);
    // что выбрать сразу: имя из дефолта поля (его даёт реестр: у глоссария
    // — ner_prompt.txt), затем autofile, затем привычное имя стадии. Список
    // именно кандидатов: у поля без дефолта (pipeline) имя берётся из
    // второго/третьего варианта, а не выдумывается здесь
    const wf = [f.default, ...(f.autofile ? [].concat(f.autofile) : []),
      `${key}_prompt.txt`]
      .map((n) => UICore.fileBase(n || ""))
      .filter((n) => n && !n.includes("*"));
    let sets = [];
    function syncInfo() {
      const set = sets.find((s) => s.name === setSel.value);
      const fname = fileSel.value;
      if (!set || !fname) {
        info.textContent = "Наборы шаблонов не найдены";
        return;
      }
      info.replaceChildren(
        `В книгу будет добавлен prompts/${fname} из набора «${set.name}»`,
        existing.has(fname)
          ? h("div", { class: "prompt-tpl-warn" },
              `Файл ${fname} в книге уже есть — он будет заменён`)
          : null,
      );
    }
    function syncFiles() {
      const set = sets.find((s) => s.name === setSel.value);
      const names = (set && set.prompts) || [];
      const pick = wf.find((n) => names.includes(n));
      fileSel.replaceChildren();
      for (const n of names) fileSel.append(h("option", { value: n }, n));
      if (names.length) fileSel.value = pick || names[0];
      syncInfo();
    }
    setSel.addEventListener("change", syncFiles);
    fileSel.addEventListener("change", syncInfo);
    UIC.modal({
      title: f.label ? `${f.label} из шаблона` : "Промпт из шаблона",
      build: (close) => [
        h("div", { class: "modal-text" }, "Набор шаблонов:"),
        setSel,
        h("div", { class: "modal-text" }, "Промпт:"),
        fileSel,
        info,
        err,
        h(
          "div",
          { class: "modal-actions" },
          h("button", { class: "btn btn-ghost", onclick: () => close() },
            "Отмена"),
          h(
            "button",
            {
              class: "btn btn-primary",
              onclick: async () => {
                err.textContent = "";
                const set = setSel.value;
                const fname = fileSel.value;
                if (!set || !fname) {
                  err.textContent = "Выберите набор и промпт";
                  return;
                }
                try {
                  const d = await api(
                    `/prompts/${encodeURIComponent(fname)}/template`
                      + `?project=${section}/${name}`,
                  );
                  const tpl = (d.templates || []).find((t) => t.set === set);
                  if (!tpl) throw new Error(`В наборе «${set}» нет ${fname}`);
                  await api(`/prompts/${encodeURIComponent(fname)}`, {
                    method: "PUT",
                    body: {
                      project: `${section}/${name}`,
                      content: tpl.content || "",
                    },
                  });
                  close();
                  onDone(fname);
                } catch (ex) {
                  err.textContent = ex.message;
                }
              },
            },
            "Скопировать",
          ),
        ),
      ],
    });
    api(`/prompts?project=${section}/${name}`).then((d) => {
      sets = (d.template_sets || [])
        .filter((s) => (s.prompts || []).length)
        .sort((a, b) => (a.name === "General" ? -1
          : b.name === "General" ? 1 : a.name.localeCompare(b.name)));
      for (const s of sets) {
        setSel.append(h("option", { value: s.name }, s.name));
      }
      // набор, где нужный файл есть, — первым (иначе он мог бы оказаться
      // ниже остальных и выбор начинался бы с чужого промпта)
      const host = sets.find(
        (s) => wf.some((n) => (s.prompts || []).includes(n)));
      if (host) setSel.value = host.name;
      syncFiles();
    }).catch((ex) => {
      err.textContent = ex.message;
    });
  }

  // «Редактировать» файл из запуска (editFileModal ниже): промпт —
  // чтение/правка/сохранение через GET/PUT /api/prompts/{name}; файлы
  // source/ (метаданные YAML, страница поддержки) — через /api/file.
  // Редактор файла в модалке: промпты — через /api/prompts, прочие
  // файлы проекта (метаданные YAML, страница поддержки) — через
  // /api/file (любой относительный путь). makeEditor/extOf — из
  // app.js (глобальные; к моменту клика уже загружены).
  function editFileModal(relPath, opts = {}) {
    const isPromptFile = Boolean(opts.prompt);
    const title = opts.title || relPath;
    const err = h("div", { class: "form-error" });
    const host = h("div", { class: "editor-modal-body" });
    let ed = null;
    /* редактор модалки создаётся асинхронно: кнопка поиска встанет в этот же
       ряд действий, когда редактор появится */
    let actions = null;
    UIC.modal({
      title,
      wide: true,
      build: (close) => [host, err, (actions = h(
        "div", { class: "modal-actions" },
        h("button", { class: "btn btn-ghost", onclick: () => close() },
          "Отмена"),
        h("button", {
          class: "btn btn-primary",
          onclick: async () => {
            err.textContent = "";
            if (!ed) {
              err.textContent = "Редактор ещё не загружен";
              return;
            }
            try {
              if (isPromptFile) {
                await api(`/prompts/${encodeURIComponent(relPath)}`, {
                  method: "PUT",
                  body: { project: `${section}/${name}`,
                          content: ed.getValue() },
                });
              } else {
                await api("/file", {
                  method: "PUT",
                  body: { project: `${section}/${name}`, path: relPath,
                          content: ed.getValue() },
                });
              }
              toast(`Сохранено: ${relPath}`);
              // mtime файла изменился — вердикт перечитывается
              if (isPromptFile) promptCheck(st.stage); // st.stage — ключ стадии
              close();
            } catch (ex) {
              err.textContent = ex.message;
            }
          },
        }, "Сохранить"),
      ))],
    });
    (async () => {
      try {
        const d = isPromptFile
          ? await api(
              `/prompts/${encodeURIComponent(relPath)}?project=${section}/${name}`,
            )
          : await api(
              `/file?project=${section}/${name}`
                + `&path=${encodeURIComponent(relPath)}`,
            );
        ed = makeEditor(d.content || "", UICore.editorLang(relPath, isPromptFile));
        const f = UIC.editorSearch(ed);
        if (f) actions.insertBefore(f, actions.firstChild);
        host.append(h("div", { class: "editor-cm" }, ed.root));
      } catch (ex) {
        err.textContent = ex.message;
      }
    })();
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
      // замок: зафиксированный термин не спорный — его поля не правятся,
      // и в списке RAG-терминов ему делать нечего
      if (UICore.nerIsLocked(it)) return false;
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
    UIC.modal({
      title: "Добавить спорные термины",
      build: (close) => [
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
        h("div", { class: "modal-actions" },
          h("button", { class: "btn btn-ghost", onclick: () => close() },
            "Отмена"),
          h("button", {
            class: "btn btn-primary",
            onclick: () => {
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
              const cur = String(st.values[key]["rag_terms"] ?? "").trim();
              const lines = cur ? cur.split(/\s*\n+/) : [];
              for (const t of terms) {
                if (t && !lines.includes(t)) lines.push(t);
              }
              st.values[key]["rag_terms"] = lines.join("\n");
              st.touched[key].add("rag_terms");
              const ta = textareaWrap && textareaWrap.querySelector("textarea");
              if (ta) ta.value = lines.join("\n");
              close();
            },
          }, "Добавить"),
        ),
      ],
    });
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
  }

  // ── диапазон глав: ЕДИНАЯ строка «Главы: [start] – [end]» ────────────
  // одна на все стадии; значения пишутся в st.values[key].start/end
  // (buildParams собирает их как обычные поля).
  // Возвращает null, если стадия не принимает start/end.
  function buildRangeRow(key, spec) {
    const hasRange = (spec.fields || []).some(
      (f) => f.name === "start" || f.name === "end",
    );
    if (!hasRange) return null;
    const start = h("input", {
      type: "number",
      class: "input run-range",
    });
    const end = h("input", {
      type: "number",
      class: "input run-range",
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
    /* пустое поле при уходе с него (change — blur/Enter, не каждый ввод —
       иначе нельзя стереть и вписать с нуля) — вернуться к автоподхвату:
       «от» → первая глава, «до» → последняя. Пустое и так значит «вся
       книга», но в форме лучше видеть реальный диапазон. Опций нет —
       пусто остаётся (сервер сам решает). */
    const backfill = (input, name) => {
      const d = ((st.options || {}).chapters || {})[name === "start" ? "min" : "max"];
      if (d == null || String(input.value).trim() !== "") return;
      input.value = String(d);
      st.values[key][name] = String(d);
    };
    start.addEventListener("change", () => backfill(start, "start"));
    end.addEventListener("change", () => backfill(end, "end"));
    const rowEl = h(
      "div",
      { class: "run-range-row" },
      h("span", { class: "run-range-label" }, "Главы:"),
      start,
      h("span", { class: "run-range-sep" }, "–"),
      end,
    );
    /* подсказка под строкой: диапазон проверяется сразу, до «Запустить».
       Поле после проверки остаётся рабочим — видно и ошибку, и само значение */
    const note = h("div", { class: "field-help run-range-note hidden" });
    const check = () => {
      const msg = UICore.chapterRangeError(
        start.value, end.value, (st.options || {}).chapters || {});
      note.textContent = msg;
      note.classList.toggle("hidden", !msg);
      // рамка — на виноватом поле; при перепутанном порядке виноваты оба
      const both = msg.startsWith("Начальная глава больше");
      start.classList.toggle("input-bad",
        !!msg && (both || msg.startsWith("«Начальная")));
      end.classList.toggle("input-bad",
        !!msg && (both || msg.startsWith("«Конечная")));
    };
    start.addEventListener("input", check);
    end.addEventListener("input", check);
    start.addEventListener("change", check);
    end.addEventListener("change", check);
    check(); // автоподхват уже мог дать диапазон: подсказка видна сразу
    rowEl.append(note);
    // ссылки на инпуты — для внешних панелей (предпросмотр замен
    // batch_replace перечитывает диапазон при вводе)
    rowEl._start = start;
    rowEl._end = end;
    st.rangeRow = rowEl;
    return rowEl;
  }

  // translate_quality: контроль бюджета по end: поле редактируемое,
  // введённая глава остаётся, если влезает в бюджет, иначе пересчитывается
  // до последней влезающей (tree — размеры глав). В режиме чанков контроля нет:
  // там диапазон — это вся книга, а бюджет режет чанки, а не конец диапазона.
  function attachQualityRange(key, inputs) {
    if (key !== "translate_quality") return;
    const row = st.rangeRow;
    if (!row || !row._start || !row._end) return;
    const end = row._end;
    // инпуты — обёртки .field (wrap); у files-полей select спрятан в _sel
    const fieldInput = (x) => {
      if (!x) return null;
      const inp = x._input || x;
      return (inp && inp._sel) || inp;
    };
    const budgetIn = fieldInput(inputs.request_budget);
    const typeSel = fieldInput(inputs.type);
    const modeSel = fieldInput(inputs.mode);
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
      // пустое поле — бюджет из спеки стадии (реестр настроек), не литерал
      const def = (inputs.request_budget && inputs.request_budget._field
        && inputs.request_budget._field.default) || "";
      const raw =
        budgetIn && budgetIn.value !== "" ? budgetIn.value : String(def);
      const budget = parseInt(raw, 10) || 0;
      const type = (typeSel && typeSel.value) || "polished";
      return qualityEndByBudget(tree, start, budget, type);
    };
    const recalc = async () => {
      if (modeSel && modeSel.value === "chunks") {
        end.title = "пусто = до последней главы (книга уходит в чанки целиком)";
        return;
      }
      const start = parseInt(row._start.value || "", 10) || 0;
      const last = await lastFit(start);
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
    row._start.addEventListener("input", recalc);
    if (budgetIn) budgetIn.addEventListener("input", recalc);
    if (typeSel) typeSel.addEventListener("change", recalc);
    if (modeSel) modeSel.addEventListener("change", recalc);
    end.addEventListener("input", recalc);
    recalc();
  }

  /* ── видимость полей по режиму стадии ───────────────────────────────
   * Единый проход по метаданным реестра (when/when_any/when_set у настроек
   * core/settings.py): значения полей режима читаются из st.values — тот же
   * источник, что у argv запусков, поэтому форма и команда не разъезжаются.
   * Стадия только вешает свои виджеты (чипсы, предпросмотр) на поле-владелец
   * через mirror(); ручных блоков «кто где виден» на стадию больше нет. */
  function attachModeVisibility(key, spec, fieldWraps, rangeRow) {
    const byName = {};
    for (const f of spec.fields || []) byName[f.name] = f;
    const meta = (spec.fields || []).filter((f) => (f.when && f.when.length)
      || (f.when_any && f.when_any.length)
      || (f.when_set && f.when_set.length));
    const watched = new Set();
    for (const f of meta) {
      for (const c of [...(f.when || []), ...(f.when_any || [])]) {
        watched.add(c[0]);
      }
    }
    const extras = []; // [узел, имя поля]: узел повторяет видимость поля
    const apply = () => {
      const vals = st.values[key] || {};
      for (const f of meta) {
        const w = fieldWraps[f.name];
        if (w) w.classList.toggle("hidden", !UICore.fieldApplies(f, vals));
      }
      // строка диапазона своя: она заменяет собой поля start/end
      if (rangeRow) {
        const show = ["start", "end"].some(
          (n) => !byName[n] || UICore.fieldApplies(byName[n], vals));
        rangeRow.classList.toggle("hidden", !show);
      }
      for (const [el, name] of extras) {
        const f = byName[name];
        el.classList.toggle("hidden", !!f && !UICore.fieldApplies(f, vals));
      }
    };
    for (const name of watched) {
      // собственный обработчик поля отрабатывает раньше: он пишет значение
      // в st.values, отсюда видимость считается уже по новому состоянию
      const inp = fieldWraps[name] && (fieldWraps[name]._input || fieldWraps[name]);
      const sel = inp && inp._sel ? inp._sel : inp;
      if (sel) sel.addEventListener("change", apply);
    }
    return {
      apply,
      mirror(el, name) {
        if (el && byName[name]) extras.push([el, name]);
        return el;
      },
    };
  }

  // форма стадии: все поля спеки. НЕ async — formPanel вставляет результат
  // как DOM-узел; async вернул бы Promise (баг «[object Promise]»).
  function stageForm(key, spec) {
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
    // translate_quality: ручной end с проверкой бюджета
    attachQualityRange(key, fieldWraps);
    // видимость полей по режиму — один проход по метаданным реестра
    const modeVis = attachModeVisibility(key, spec, fieldWraps, rangeRow);

    // pipeline — единый общий промпт-файл (теги translate/redact/polish),
    // режим промптов и отдельные файлы на стадию убраны;
    // чипсы полей {ner_block} — после «Мин. count для глоссария»
    if (key === "pipeline") {
      const nb = nerBlockChips(key);
      const nmWrap = fieldWraps["ner_min_count"];
      const idx = nmWrap ? fieldNodes.indexOf(nmWrap) : -1;
      if (idx >= 0) fieldNodes.splice(idx + 1, 0, nb.bar, nb.box);
      nb.loadFields();
      // чипсы следуют за своим скрытым полем: в чистой полировке (действия
      // 3 и 4) блок глоссария не используется
      modeVis.mirror(nb.bar, "ner_fields");
      modeVis.mirror(nb.box, "ner_fields");
    }

    // wiki — источник текста: «Готовый txt» ↔ «Собрать из глав»;
    // формат: обычный/rulate-md/rulate-html (toc/toc_links — только
    // в обычном режиме)
    if (key === "wiki") {
      // чипсы типов из глоссария (как в ner_check; пусто = все типы);
      // счётчик терминов — динамически по top/min_count
      const w = nerCheckWidgets(key);
      const outWrap = fieldWraps["output"];
      const nidx = outWrap ? fieldNodes.indexOf(outWrap) : -1;
      if (nidx >= 0) {
        fieldNodes.splice(nidx + 1, 0, w.chipsBar, w.chipsBox);
      }
      w.loadTypes();
      const recalcTerms = () => w.updateCounts({
        top: fieldWraps["top"] && fieldWraps["top"]._input
          ? fieldWraps["top"]._input.value : "",
        minCount: fieldWraps["min_count"] && fieldWraps["min_count"]._input
          ? fieldWraps["min_count"]._input.value : "",
      });
      for (const nm of ["top", "min_count"]) {
        const inp = fieldWraps[nm] && fieldWraps[nm]._input;
        if (inp) inp.addEventListener("input", recalcTerms);
      }
      recalcTerms();
      // чипсы типов — за своим скрытым полем; поля режима (источник, формат,
      // вики-глава, диапазон) прячет общий проход по when из реестра
      modeVis.mirror(w.chipsBar, "types");
      modeVis.mirror(w.chipsBox, "types");
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
      // счётчики типов/терминов — динамически по «Порог count»
      // (унификация с wiki)
      const recalcCounts = () => w.updateCounts({
        threshold: fieldWraps["count_threshold"]
          && fieldWraps["count_threshold"]._input
          ? fieldWraps["count_threshold"]._input.value : "",
      });
      const thInp = fieldWraps["count_threshold"]
        && fieldWraps["count_threshold"]._input;
      if (thInp) thInp.addEventListener("input", recalcCounts);
      recalcCounts();
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
      // в RAG прячутся только чипсы ТИПОВ: поля записи (fieldsBar) и
      // степпер остаются — выбранные поля уходят в RAG-промпт. Чипсы
      // следуют за скрытым полем types (в реестре оно whole/types)
      modeVis.mirror(w.chipsBar, "types");
      modeVis.mirror(w.chipsBox, "types");
    }

    // epub: справка по regexp (collapsible) + перестройка селекта
    // исходника при смене режима (расширения зависят от режима)
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
            "вся строка становится заголовком главы. Паттерны разбивки:",
            " строка-маркер = начало СТРОКИ (якоря ^ не нужны). Без",
            " комментариев и кастомных флагов («#» — литерал в паттерне)."),
          h("p", {}, "Разделители глав (режим regexp, по одному на строку):"),
          h("ul", {},
            h("li", {}, h("code", {}, "Глава \\d+"), " — «Глава 1», «Глава 22»"),
            h("li", {}, h("code", {}, "^第[0-9]+章"), " — «第1章», «第25章»"),
            h("li", {}, h("code", {}, "^第.章"), " — «第一章», «第二百三十五章»"),
            h("li", {}, h("code", {}, "^(Глава|Часть)\\s\\d+"),
              " — «Глава 3» или «Часть 5»"),
            h("li", {}, h("code", {}, "^\\d+\\.\\s*$"), " — «12.»"),
          ),
          h("p", {}, "Regexp-замены (режим „Ручной“): формат ",
            h("code", {}, "паттерн -> замена"),
            "; значимые пробелы — ", h("code", {}, "^  "), " и ",
            h("code", {}, "  $"), "; пустая правая часть — удаление."),
          h("p", {}, "Предварительная очистка текста — отдельный чекбокс ",
            "формы: пустые строки внутри глав убираются (каждый абзац своей ",
            "строкой), пустая строка остаётся только после первой строки ",
            "файла; пробелы по краям строк (любые, включая U+2003 из ",
            "CJK-исходников) убираются всегда."),
          h("p", {}, "Остальные очистки и замены до разбивки здесь не нужны: ",
            "их делает отдельная стадия «Массовые замены» после перевода; ",
            "в CLI ", h("code", {}, "--clean-re"), " и ",
            h("code", {}, "--replace-re"), " остаются."),
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
          h("p", {}, "Паттерн — чистый стандартный regexp (Python re): "
            + "регистр — inline-флагом ", h("code", {}, "(?i)"),
            "; комментариев и кастомных флагов нет («#» — литерал); "
            + "пробелы в паттерне значимы; ",
            h("code", {}, "[一-鿿]"), " — иероглиф."),
        ),
      );
      fieldNodes.push(help);
      if (modeSel && inputWrap) {
        // шпаргалка нужна там, где есть паттерны разбиения; сами поля режима
        // (паттерны, чанки, маска, переопределение) прячет общий проход
        modeVis.mirror(help, "split_patterns");
        const rebuildInput = () => {
          const m = modeSel.value || "toc";
          const exts = m === "toc" ? [".epub"] : [".epub", ".txt"];
          const sel = inputWrap._input && inputWrap._input._sel;
          if (!sel) return;
          const cur = sel.value;
          // список строится заново по ВСЕМУ пулу source/: фильтр прошлого
          // режима уже выбросил из селекта txt-исходники, возвращать их
          // из отфильтрованного списка нечем
          const pool = st.options.source || [];
          sel.replaceChildren();
          sel.append(h("option", { value: "" }, "—"));
          for (const n of pool) {
            if (exts.some((e) => n.toLowerCase().endsWith(e))) {
              sel.append(h("option", { value: n }, n));
            }
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
          modeVis.apply();
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
          h("p", {}, "Паттерн — чистый стандартный regexp (Python re): "
            + "регистр — inline-флагом ", h("code", {}, "(?i)"),
            "; комментариев и кастомных флагов нет («#» — литерал); "
            + "пробелы в паттерне значимы."),
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
            h("p", {}, "Каждая строка — чистый стандартный regexp "
              + "(Python re, multiline): ВСЁ найденное — ошибка, "
              + "проверяются ВСЕ строки включая заголовок; без "
              + "комментариев и кастомных флагов («#» — литерал в "
              + "паттерне); регистр и прочие режимы — стандартными "
              + "inline-флагами: ",
              h("code", {}, "(?i)"), " — без учёта регистра. "
              + "Поле пусто — работают дефолтные проверки "
              + "(они уже пропускают первое совпадение заголовка)."),
            h("p", {}, "Дефолтные проверки:"),
            h("ul", {},
              h("li", {}, h("code", {}, "[一-鿿【】「」『』]+"),
                " — иероглифы (остались в переводе)"),
              h("li", {}, h("code", {}, "[a-zA-Z]+"),
                " — латиница"),
              h("li", {}, h("code", {}, "(?<=\\n)\\s*Глава\\s+\\d+"),
                " — лишние заголовки «Глава N» (вхождение в 1-й строке "
                + "файла не совпадает)"),
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
              h("code", {}, "(a|b)"), " — a или b; ",
              h("code", {}, "(?<=\\n)паттерн"), " — всё, кроме вхождения "
                + "в самом начале файла."),
            h("p", {}, "Комментарии НЕ поддерживаются: «#» в паттерне — "
              + "обычный символ; пусто в поле = дефолтные проверки."),
          ),
        ),
      );
    }

    // compile — предпросмотр обложки/metadata.yaml/страницы поддержки
    // (виден только в epub/fb2 — следует за полем «Обложка»)
    if (key === "compile") {
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
      // предпросмотр обложки и файлов — только для EPUB/FB2: следует за
      // своим полем, сами поля прячет общий проход по when из реестра
      modeVis.mirror(preview, "cover");
      for (const sel of [coverSel, metaSel, donateSel]) {
        if (sel) sel.addEventListener("change", updateCompilePreview);
      }
      updateCompilePreview();
    }

    // LLM-конфиг (сервер/модель/ключ/потоки) в форме запусков НЕ рисуется:
    // он один на весь конвейер и правится на странице «Настройки»
    const formNodes = fieldNodes;

    const runBtn = h("button", { class: "btn btn-primary" }, "Запустить");
    runBtn.addEventListener("click", async () => {
      err.textContent = "";
      const verr = epubValidateInput(key) || rangeValidate(key, spec)
        || numericFieldError(key, spec);
      if (verr) {
        err.textContent = verr;
        return;
      }
      try {
        UIC.askNotifyPermission(); // финал запуска — в уведомление (см. app.js)
        const r = await api("/jobs", {
          method: "POST",
          body: {
            action: key,
            project: `${section}/${name}`,
            params: buildParams(key, spec),
          },
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

    // «Предпросмотр запроса» — только LLM-стадии (spec.preview)
    const previewBtn = spec.preview
      ? h("button", { class: "btn btn-ghost",
                      title: "Эмулировать ПЕРВЫЙ LLM-запрос стадии "
                            + "(чанк/батч 1) без сети — текст запроса "
                            + "и статистика символов" },
                 "Предпросмотр запроса")
      : null;
    if (previewBtn) {
      previewBtn.addEventListener("click", () =>
        previewRequestModal(key, spec));
    }

    // обёртки полей текущей формы — для панели предпросмотра замен
    // (batch_replace: реакции на смену типа/диапазона/правил)
    st.curWraps = fieldWraps;
    // видимость полей режима — после того как стадия навесила свои виджеты
    modeVis.apply();

    return h("div", { class: "run-form" }, formNodes, err,
             ...(previewBtn ? [previewBtn] : []), runBtn);
  }

  // ── epub: панель предпросмотра разбивки ──────────────────────────────
  // Кнопка «Предпросмотр» → POST /stages/epub/preview (папки + размеры);
  // снятие галочки — seq уходит в st.preview.skips БЕЗ перезапуска
  // предпросмотра (строка остаётся с отжатым чекбоксом для наглядности;
  // перенумерация — при реальном запуске со skip); текст главы —
  // GET .../preview/text?num=.
  function epubPreviewPanel(key, spec) {
    const err = h("div", { class: "form-error" });
    const btn = h("button", { class: "btn btn-primary" }, "Предпросмотр");
    btn.addEventListener("click", async () => {
      err.textContent = "";
      const verr = epubValidateInput(key);
      if (verr) {
        err.textContent = verr;
        return;
      }
      try {
        await epubRunPreview(key, spec);
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
  // Панель закрыта, пока не нажата кнопка «Предпросмотр» (как у разбивки):
  // без нажатия нет ни селектора глав, ни запроса предпросмотра.
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
    const hint = h(
      "div",
      { class: "field-help", style: st.brOn ? "display:none" : "" },
      "Нажмите «Предпросмотр» — покажется выбор главы и подсветка изменений",
    );

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
      const nodes = [];
      nodes.push(
        h("div", { class: "br-stats" },
          `глава ${r.num} · ${r.dir} · ${r.type}.txt — `
          + (r.changed ? "текст изменится" : "текст НЕ изменится")),
      );
      // Какие правила доехали до скрипта и что они делают. Пробелы видимы:
      // без меток «^ + -> » (заменить одним пробелом) неотличим от удаления,
      // а удалённый перенос строки — от пустоты.
      const rules = r.rules || [];
      if (rules.length) {
        nodes.push(
          h("div", { class: "field-help" },
            "Правила (пробел — ·, таб — \\t, перевод строки — ⏎):"),
        );
        for (const x of rules) {
          nodes.push(
            h("div", { class: "br-rule" }, [
              h("code", {
                text: `${x.pattern} → ${x.replacement || "∅ (удаление)"}`,
              }),
              h("span", { class: "br-count", text: x.count ? `×${x.count}` : "не задели текст" }),
            ]),
          );
        }
      }
      if (r.warnings && r.warnings.length) {
        nodes.push(
          h("div", { class: "field-help" }, "⚠ " + r.warnings.join(" · ")),
        );
      }
      // длинный keep между изменениями режем середину: важен контекст вокруг
      // del/ins, а не весь текст главы
      const segs = r.segments || [];
      const EDGE = 12;
      let shown = segs;
      if (segs.length > EDGE * 2) {
        const head = segs.slice(0, EDGE);
        const tail = segs.slice(segs.length - EDGE);
        const len = (arr) => arr.reduce((n, [, t]) => n + String(t).length, 0);
        const mid = len(segs) - len(head) - len(tail);
        shown = head
          .concat([["mid", ` … ещё ${mid} символов без изменений … `]])
          .concat(tail);
      }
      const pre = h("pre", { class: "br-text" });
      for (const [kind, text] of shown) {
        const t = UICore.markWhitespace(text);
        if (kind === "keep") pre.append(document.createTextNode(t));
        else if (kind === "mid") pre.append(h("span", { class: "br-mid", text: t }));
        else pre.append(h("span", { class: kind === "del" ? "br-del" : "br-ins" }, t));
      }
      nodes.push(pre);
      box.append(...nodes);
    }

    chSel.addEventListener("change", () => {
      st.brChapter = chSel.value === "" ? null : Number(chSel.value);
      runPreview();
    });
    btn.addEventListener("click", () => {
      err.textContent = "";
      if (st.brOn) {
        runPreview();
        return;
      }
      // первый заход: только сейчас панель раскрывается и читает главы
      st.brOn = true;
      hint.style.display = "none";
      body.style.display = "";
      loadChapters(true);
    });

    // тело панели закрыто, пока кнопка не нажата: без него ни селектора
    // глав, ни запроса предпросмотра
    const body = h(
      "div",
      { class: "br-body", style: st.brOn ? "" : "display:none" },
      [
        h(
          "div",
          { class: "field" },
          h("div", { class: "field-label" }, "Глава"),
          chSel,
        ),
        note,
        dirty,
        box,
      ],
    );
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
      hint,
      body,
      err,
    );

    // привязка событий формы ПОСЛЕ вставки в DOM: работает только в
    // раскрытой панели; смена типа/диапазона пересобирает список глав (и
    // предпросмотр), набор правил только помечает «устарел»
    function mount(formNode) {
      const rangeRow = st.rangeRow || {};
      const onType = inputOf(wraps["type"]);
      const onStart = rangeRow._start || null;
      const onEnd = rangeRow._end || null;
      const onRepl = inputOf(wraps["replacements"]);
      formNode.addEventListener("input", (ev) => {
        if (!st.brOn) return;
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
        if (st.brOn && ev.target === onType) {
          st.brPreview = null;
          st.brChapter = null;
          loadChapters(true);
        }
      });
      if (st.brOn) loadChapters(true);
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

  // валидация диапазона глав перед запуском: «64 – 3» и главы за последней
  // главой книги до скрипта не доезжают (то же правило — core.settings.
  // chapter_range_error и серверный 400). Режим стадии, которому диапазон не
  // нужен (ner_check без RAG, wiki по готовому txt), не проверяется.
  function rangeValidate(key, spec) {
    const vals = st.values[key] || {};
    // видимость строки диапазона — тот же расчёт, что у attachModeVisibility:
    // она нужна, если применимо хотя бы одно из полей start/end
    const fields = (spec.fields || [])
      .filter((f) => f.name === "start" || f.name === "end");
    if (!fields.length
        || !fields.some((f) => UICore.fieldApplies(f, vals))) return "";
    return UICore.chapterRangeError(vals.start, vals.end,
      (st.options || {}).chapters || {});
  }

  function epubValidateInput(key) {
    if (key !== "epub") return "";
    const vals = st.values[key] || {};
    const v = String(vals["input"] || "");
    if (!v) return "Выберите исходник";
    const m = String(vals["mode"] || "toc");
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
  async function epubRunPreview(key, spec) {
    const r = await api("/stages/epub/preview", {
      method: "POST",
      body: {
        project: `${section}/${name}`,
        params: buildParams(key, spec),
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

  function stopBtn(job) {
    const btn = h("button", { class: "btn btn-sm btn-danger" }, "Стоп");
    btn.addEventListener("click", async () => {
      try {
        await api(`/jobs/${job.id}/stop`, { method: "POST" });
        toast("Остановка...");
      } catch (ex) {
        toast(ex.message, "err");
      }
    });
    return btn;
  }

  /* ── панель лога: окно текста, которое дорисовывается кадром ────────
     Живой лог — ОДНО текстовое окно: SSE дописывает строки в st.log, окно
     получает ОДНУ дорисовку на кадр анимации. Прежняя схема перечитывала и
     заново верстала весь хвост на КАЖДУЮ строку, а сервер присылал их
     отдельными кадрами: прогон на 1300 глав вываливал тысячи перерисовок
     подряд — вкладка «Запуски» висела десятками секунд и с ней весь
     интерфейс. Перезапись окна целиком осталась и после снимка-кадра — на
     живом потоке она стоит квадратично: каждая пачка верстает все
     предыдущие строки (замер на реальном логе глоссария: окно 5000 строк —
     82 мс на сборку и 13 мс на каждый кадр потока). Поэтому строки
     ДОПИСЫВАЮТСЯ отдельными кусками, а старое снимается ЦЕЛЫМИ кусками:
     полная пересборка окна остаётся снимку и перерисовке панели, а кадр
     потока стоит 2 мс вместо 13. Прокрутка вниз — только если панель и так
     упиралась в конец: листавшего лог нельзя выбрасывать в конец. */
  const LOG_LIMIT = 5000; // строк в окне — весь буфер сервера (web/jobs.RING_SIZE)
  // строк в куске при полной сборке окна; живая пачка строк — свой кусок
  const LOG_CHUNK = 250;
  /* отметки состояний глав — одни на панель лога и на таблицу под ней */
  const STATUS_SYM = { OK: "✓", ERROR: "✗", SKIP: "⊘" };
  let logNode = null; // <pre> панели лога живой стадии
  let logFrame = false; // кадр перерисовки уже заказан
  let logSegs = []; // {node, n} — куски окна по порядку
  let logLines = 0; // сколько строк в окне сейчас
  let logPending = 0; // сколько строк пришло после последней дорисовки

  // заглушка пустого лога
  function emptyLog() {
    logNode.textContent = "(лог пуст)";
    logSegs = [];
    logLines = 0;
    logPending = 0;
  }

  // дописать строки одним куском: живой поток отдаёт их пачкой на кадр
  function appendLogSeg(lines) {
    const node = document.createTextNode(
      (logLines ? "\n" : "") + lines.join("\n"),
    );
    logNode.append(node);
    logSegs.push({ node, n: lines.length });
    logLines += lines.length;
  }

  /* Пересобрать окно с нуля: снимок стрима (переподключение присылает хвост
     заново — дописывать его нельзя, строки задвоятся) и перерисовка панели
     после смены запуска. Строки делятся на куски по LOG_CHUNK: целыми
     кусками окно потом и подрезается. */
  function rebuildLog() {
    logNode.textContent = "";
    logSegs = [];
    logLines = 0;
    logPending = 0;
    if (!st.log.length) return emptyLog();
    for (let i = 0; i < st.log.length; i += LOG_CHUNK) {
      appendLogSeg(st.log.slice(i, i + LOG_CHUNK));
    }
  }

  /* Окно показывает хвост st.log (а он не длиннее LOG_LIMIT) с запасом не
     больше одного куска: старое снимается ЦЕЛЫМИ кусками — перевёрстка при
     этом не нарастает */
  function trimLog() {
    while (logSegs.length > 1 && logLines - logSegs[0].n >= st.log.length) {
      const seg = logSegs.shift();
      seg.node.remove();
      logLines -= seg.n;
      // перевод строки снятого куска начал бы окно пустой строкой
      if (logSegs[0].node.textContent.startsWith("\n")) {
        logSegs[0].node.textContent =
          logSegs[0].node.textContent.slice(1);
      }
    }
  }

  function paintLog() {
    if (!logNode) return;
    const stick = logNode.scrollHeight - logNode.clientHeight
      - logNode.scrollTop <= 24;
    // окно неполное (панель только открылась или стрим переподключился) —
    // дешевле собрать заново, чем догадываться, чего в нём нет
    if (logPending >= st.log.length) rebuildLog();
    else if (logPending) {
      const n = logPending;
      logPending = 0;
      appendLogSeg(st.log.slice(st.log.length - n));
      trimLog();
    }
    if (stick) logNode.scrollTop = logNode.scrollHeight;
  }

  function scheduleLog() {
    if (logFrame) return; // пачка событий из SSE — одна перерисовка на кадр
    // флаг снимаем ВНУТРИ колбэка: присваивание результата requestAnimationFrame
    // случилось бы раньше самого колбэка и заказ больше не сбрасывался бы
    logFrame = true;
    requestAnimationFrame(() => {
      logFrame = false;
      paintLog();
    });
  }

  /* Панель лога: шапка (метка, название запуска, badge, «Стоп») и строка
     прогресса под ней. Прогресс — ОДНИМ узлом (UIC.progressRow): раньше
     счётчик дублировался в шапке и в баре, и оба обрезались многоточием
     на узкой колонке. У завершённого запуска без событий прогресса строки
     нет — показывать нечего. */
  function logPanel(view) {
    const job = view.job;
    const pre = h("pre", { class: "log-area", text: "" });
    if (view.live) {
      // лента живой панели живёт в st.log — её дописывает SSE (paintLog);
      // узел новый, кусков окна ещё нет
      logNode = pre;
      rebuildLog();
      paintLog();
    } else {
      pre.textContent = view.lines.length ? view.lines.join("\n") : "(лог пуст)";
      pre.scrollTop = pre.scrollHeight;
    }
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
      h(
        "span",
        { class: "badge badge-" + job.status },
        job.status,
      ),
    );
    if (job.status === "running") {
      toolbar.append(stopBtn(job));
    } else {
      toolbar.append(
        h(
          "span",
          { class: "job-time", text: new Date(
            (job.finished || job.created) * 1000,
          ).toLocaleString("ru-RU") },
        ),
      );
    }
    const nodes = [toolbar];
    if (view.progress || job.status === "running") {
      const bar = UIC.progressRow();
      st.logBar = bar;
      bar.paint(UICore.progressParts(job, view.progress));
      nodes.push(bar.el);
    } else {
      st.logBar = null;
    }
    nodes.push(pre);
    const panel = h("div", { class: "run-panel" }, ...nodes);
    if (job.action === "pipeline") {
      // интерактивная визуализация лога: таблица глав с состоянием
      // стадий ПОД логом (только главы заданного диапазона формы;
      // пустой диапазон = все главы). События приходят из SSE или
      // из истории запуска — таблица отражает и завершённые прогоны
      panel.append(
        h(
          "div",
          { class: "run-panel-title" },
          "Главы · перевод → редактура → полировка",
        ),
        chapterTable(view.events),
      );
    }
    return panel;
  }

  /* живое обновление строк прогресса из SSE: панель лога текущей стадии и
     карточка активного запуска (она видна на любой вкладке). Пока задача
     работает, обе строки видны ВСЕГДА — даже до первого события прогресса
     («ожидание первого результата…») */
  function paintProgress() {
    const parts = UICore.progressParts(st.job, st.progress);
    if (st.logBar) st.logBar.paint(parts);
    if (st.miniBar) st.miniBar.paint(parts);
  }

  // свёртка событий конвейера: {"id:stage" → status} (дедуп в onPayload
  // уже гарантирует одно событие на пару, ключ — строка для клетки)
  function chapterByKey(events) {
    const byKey = {};
    for (const ev of events || []) {
      byKey[ev.id + ":" + ev.stage] = ev.status;
    }
    return byKey;
  }

  // диапазон глав для таблицы конвейера: из формы (st.values.pipeline,
  // с автоподхватом диапазона опций); строки — реальные id глав.
  // Пустой/неполный диапазон = все главы из опций
  function chapterRange() {
    const ch = (st.options || {}).chapters || {};
    const ids = Array.isArray(ch.ids) && ch.ids.length
      ? ch.ids
      : [];
    const v = st.values.pipeline || {};
    const s = parseInt(v.start, 10);
    const e = parseInt(v.end, 10);
    // границы заданы частично/некорректно — игнорируем эту границу
    const lo = Number.isFinite(s) ? s : null;
    const hi = Number.isFinite(e) ? e : (lo == null ? null : lo);
    if (lo == null && hi == null) return ids;
    return ids.filter((id) =>
      (lo == null || id >= lo) && (hi == null || id <= hi));
  }

  // таблица глав конвейера (под логом): строки = главы диапазона,
  // колонки = стадии 1..3; клетки из событий запуска (без событий —
  // «·»: стадии ещё не дошли). Данные из истории — то же самое
  function chapterTable(events) {
    const cols = [1, 2, 3];
    const stageSym = { 1: "пер", 2: "ред", 3: "пол" };
    const byKey = chapterByKey(events || []);
    // что реально показано в клетках: живой поток правит только смены статуса
    st.chShown = byKey;
    const cells = [];
    const range = chapterRange();
    for (const id of range) {
      const row = [h("th", { class: "ch-num" }, String(id))];
      for (const stage of cols) {
        const s = byKey[id + ":" + stage];
        const cls = s
          ? "ch-cell ch-" + String(s).toLowerCase()
          : "ch-cell ch-pending";
        row.push(
          h(
            "td",
            { class: cls, title: `${stageSym[stage]}: ${s || "—"}` },
            s ? STATUS_SYM[s] || s : "·",
          ),
        );
      }
      cells.push(h("tr", { class: "ch-row", "data-id": id }, row));
    }
    if (!cells.length) {
      return h("div", { class: "empty" }, "Главы не найдены");
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
  // SSE (стартовый снимок сервера уже содержит хвост), payload lines не
  // дублируем; AbortController гасит старый стрим при уходе со страницы.
  // обрыв сети/таймаут — reconnect с backoff (1 c, 2 c, 4 c, …),
  // не больше MAX_ATTEMPTS; при переподключении сервер шлёт весь хвост
  // заново — снапшот заменяет st.log целиком, поэтому строк не дублируется.
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
          await new Promise((r) => setTimeout(r, 1000 * 2 ** attempt));
        }
      }
      // конец стрима (задание завершилось): догнать статус/события;
      // панель лога ОСТАЁТСЯ на вкладке стадии с финальным статусом
      // (последний завершённый запуск виден до нового запуска)
      try {
        const r = await api(`/jobs/${jobId}`);
        if (r.job) {
          if (r.job.status) st.job.status = r.job.status;
        }
      } catch {
        /* статус уже есть */
      }
      // После завершения запуска могли появиться новые файлы
      // (ner.json, wiki.md) — форма перечитает опции при рендере
      st.options = null;
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
          } catch {
            /* кривое событие пропущено: статус придёт следующим кадром */
          }
        }
      }
    }
  }

  // разбор одного SSE-payload: снимок буфера, строка лога, событие главы,
  // прогресс, статус. DOM обновляется только когда панель лога принадлежит
  // текущему запуску (st.job.action === st.stage): при открытой чужой стадии
  // строки активного запуска не утекут в панель последнего
  // завершённого; состояние (st.log/st.progress) копится
  function onPayload(payload) {
    const live = !!(st.stage && st.job && st.job.action === st.stage);
    if (payload.type === "snapshot") {
      // снимок — весь хвост разом (переподключение тоже присылает его
      // целиком, поэтому строк не прибавляется): окно собирается заново,
      // а не дописывается
      st.log = (payload.lines || []).slice(-LOG_LIMIT);
      logPending = st.log.length; // «что в окне — неизвестно»: всё заново
      if (Array.isArray(payload.events)) st.events = payload.events.slice();
      if (payload.progress) st.progress = payload.progress;
      if (live) {
        paintLog();
        if (st.logBar || st.miniBar) paintProgress();
      }
      return;
    }
    if (payload.type === "line") {
      st.log.push(payload.text);
      logPending += 1;
      if (st.log.length > LOG_LIMIT) {
        st.log.splice(0, st.log.length - LOG_LIMIT);
      }
      if (live) scheduleLog();
    } else if (payload.type === "progress" && payload.event) {
      st.progress = payload.event;
      // строка прогресса оживает и в панели лога живой стадии, и в
      // карточке активного запуска (она видна на любой вкладке)
      if ((live && st.logBar) || st.miniBar) paintProgress();
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
        if (live && st.chShown[ev.id + ":" + ev.stage] !== ev.status) {
          // интерактивная визуализация: клетка таблицы под логом
          // обновляется сразу (строка ищется по data-id — реальная
          // глава, а не позиция: нумерация может быть разреженной).
          // Главы вне диапазона формы в таблице нет — событие
          // копится в st.events и появится при перерисовке.
          // Конвейер на 1300 глав — это тысячи событий, а поиск строки
          // есть обход всей таблицы, поэтому правится только смена статуса
          const t = page.querySelector(".ch-table");
          const cell = t && t.querySelector(
            `.ch-row[data-id="${ev.id}"] .ch-cell:nth-child(${ev.stage + 1})`,
          );
          if (cell) {
            st.chShown[ev.id + ":" + ev.stage] = ev.status;
            cell.textContent = STATUS_SYM[ev.status] || ev.status;
            cell.className = "ch-cell ch-" + String(ev.status).toLowerCase();
          }
        }
      }
    } else if (payload.type === "status") {
      st.job.status = payload.status;
      UICore.etaForget(st.job.id); // окно ETA больше не нужно
      // кэш последнего запуска этой стадии устарел — при возврате
      // на вкладку данные перечитаются из /jobs/{id}
      delete st.lastLog[st.job.action];
      // статус — финальное событие стрима: перерисовать сразу;
      // панель лога остаётся с финальным статусом (done/failed/stopped)
      render();
    }
  }

  // уход со страницы (смена вкладки проекта или переход на другой
  // view): гасим стрим, чтобы старый экземпляр не держал SSE и не
  // мутировал detached-узлы. Слушаем на document: pi-navigate
  // диспатчится на старом узле (project-views при смене вкладки) или
  // на .page всего вида (app.js) — в обоих событие доходит по bubble
  document.addEventListener(
    "pi-navigate",
    () => {
      if (streamCtrl) streamCtrl.abort();
      streamCtrl = null;
    },
    { once: true },
  );

  // авто-прикрепление: jobId из роута (короткий #/run/…/<id> и
  // «Показать») — конкретный запуск; иначе активный запуск проекта
  // (вернулись на вкладку — управление не потеряно)
  if (attachJobId && /^[0-9a-f]{12}$/.test(attachJobId)) {
    attachToJob(attachJobId);
  } else {
    autoAttach();
  }
  return page;
}

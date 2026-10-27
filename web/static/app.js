/* app.js — SPA web-интерфейса NovelMaestro (ES-module, vanilla JS, без сборки)
 * Каркас, роутер, вход по токену.
 * Hub — разделы, карточки проектов, мастер создания, управление.
 * Файлы — браузер, редактор, загрузка, скачивание. */

const state = { auth: false, host: "", tokenSet: false, update: null };


/* Предпросмотр markdown/html (Заметки и «Правка» файлов).
   Кегль — localStorage previewFontSize (UI-предпочтение, не конфиг).
   srcdoc-iframe не наследует тему страницы — стили задаём явно. */
const PREVIEW_FONT_KEY = "previewFontSizeV2";
const PREVIEW_FONT_DEFAULT = 12;
const PREVIEW_FONT_OPTIONS = [5, 7, 10, 12, 14];

function getPreviewFontSize() {
  const n = parseInt(localStorage.getItem(PREVIEW_FONT_KEY) || "", 10);
  return UICore.clampFont(n, PREVIEW_FONT_OPTIONS, PREVIEW_FONT_DEFAULT);
}

/* ── Внешний вид: UI-предпочтения в localStorage браузера (не конфиг —
   12-factor: клиентские настройки живут на клиенте). Сами значения и выбор
   языка подсветки — в UICore (они чистые); здесь только хранилище. ── */
const UI_LOOK_KEY = "uiLookV1";
const EDITOR_SETTINGS = UICore.EDITOR_SETTINGS;
const EDITOR_LANGS = UICore.EDITOR_LANGS;
function loadUiLook() {
  try {
    const raw = localStorage.getItem(UI_LOOK_KEY);
    if (!raw) return;
    const d = JSON.parse(raw);
    if (d.ui === "light" || d.ui === "dark") EDITOR_SETTINGS.ui = d.ui;
    if (d.editor === "auto" || d.editor === "dark" || d.editor === "light")
      EDITOR_SETTINGS.editor = d.editor;
    const n = parseInt(d.fontSize, 10);
    if (Number.isFinite(n))
      EDITOR_SETTINGS.fontSize = Math.max(8, Math.min(32, n));
    for (const k of ["langPrompt", "langLog"]) {
      if (EDITOR_LANGS.some((o) => o.v === d[k])) EDITOR_SETTINGS[k] = d[k];
    }
  } catch {
    /* битый localStorage — дефолты */
  }
}
loadUiLook();

/* Синтаксические цвета редакторов: вендор не отдаёт HighlightStyle,
   классы подсветки генерирует автоименами (CSS их не переопределит) —
   свои стили по теме через syntaxHighlighting (не-fallback, выигрывает
   у светлого дефолта basicSetup). */
const EDITOR_HIGHLIGHT = (() => {
  const cm = window.CM || {};
  const t = cm.tags;
  if (!t || !cm.HighlightStyle || !cm.syntaxHighlighting) return null;
  const HS = cm.HighlightStyle;
  const base = (extra) => [
    { tag: t.meta, color: extra.meta },
    { tag: t.link, textDecoration: "underline" },
    /* html/xml-файлы: текст между тегами — content, сам тег — tagName, его
       скобки — angleBracket */
    { tag: [t.content, t.bracket, t.separator], color: extra.text },
    { tag: [t.angleBracket, t.documentMeta], color: extra.meta },
    { tag: [t.tagName, t.attributeName], color: extra.type },
    { tag: t.attributeValue, color: extra.string },
    { tag: t.heading, textDecoration: "underline", fontWeight: "bold" },
    { tag: t.emphasis, fontStyle: "italic" },
    { tag: t.strong, fontWeight: "bold" },
    { tag: t.strikethrough, textDecoration: "line-through" },
    { tag: t.keyword, color: extra.keyword },
    {
      tag: [t.atom, t.bool, t.url, t.contentSeparator, t.labelName],
      color: extra.atom,
    },
    { tag: [t.literal, t.inserted], color: extra.string },
    { tag: [t.string, t.deleted], color: extra.string },
    { tag: [t.regexp, t.escape, t.special(t.string)], color: extra.string },
    { tag: t.definition(t.variableName), color: extra.type },
    { tag: t.local(t.variableName), color: extra.text },
    { tag: [t.typeName, t.namespace], color: extra.type },
    { tag: t.className, color: extra.type },
    { tag: [t.special(t.variableName), t.macroName], color: extra.string },
    { tag: t.definition(t.propertyName), color: extra.atom },
    { tag: t.comment, color: extra.comment },
    { tag: t.invalid, color: extra.invalid },
  ];
  return {
    dark: HS.define(
      base({
        meta: "#8b949e",
        comment: "#8b949e",
        text: "#e6edf3",
        keyword: "#ff7b72",
        atom: "#79c0ff",
        string: "#a5d6a7",
        type: "#ffa657",
        invalid: "#f85149",
      }),
    ),
    light: HS.define(
      base({
        meta: "#6e7781",
        comment: "#6e7781",
        text: "#1f2328",
        keyword: "#cf222e",
        atom: "#0550ae",
        string: "#0a3069",
        type: "#953800",
        invalid: "#cf222e",
      }),
    ),
  };
})();

function effectiveEditorTheme() {
  return EDITOR_SETTINGS.editor === "auto"
    ? EDITOR_SETTINGS.ui
    : EDITOR_SETTINGS.editor;
}

function applyEditorSettings() {
  document.documentElement.dataset.uiTheme = EDITOR_SETTINGS.ui;
  document.body.dataset.editorTheme = effectiveEditorTheme();
}

function setPreviewFontSize(n) {
  const next = UICore.clampFont(n, PREVIEW_FONT_OPTIONS, PREVIEW_FONT_DEFAULT);
  localStorage.setItem(PREVIEW_FONT_KEY, String(next));
  return next;
}

function previewCss() {
  /* Внутри iframe всегда 16px: Chrome не даёт рисовать меньше минимума
     (~12–16px), поэтому визуальный кегль задаём zoom на самом iframe. */
  const light = EDITOR_SETTINGS.ui === "light";
  const bg = light ? "#ffffff" : "#0d1117";
  const fg = light ? "#1f2328" : "#e6edf3";
  const panel = light ? "#f6f8fa" : "#161b22";
  const border = light ? "#d0d7de" : "#30363d";
  const link = light ? "#1a7f37" : "#2ea043";
  return (
    "html{-webkit-text-size-adjust:100%;text-size-adjust:100%}" +
    `html,body{background:${bg};color:${fg};` +
    "font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;" +
    "font-size:16px;line-height:1.5;margin:0}" +
    "body{padding:10px}" +
    "h1,h2,h3,h4,h5,h6{font-size:16px;font-weight:600;margin:0.65em 0 0.3em}" +
    "p,li,td,th,blockquote,pre,code{font-size:16px}" +
    /* обычный текст предпросмотра: переносы источника священны, поэтому
       pre-wrap (у markdown одиночный перенос — мягкий, строки склеиваются) */
    `pre{background:${panel};padding:8px;border-radius:6px;overflow:auto;` +
    "white-space:pre-wrap;overflow-wrap:break-word;" +
    "font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}" +
    "code{background:rgba(110,118,129,.2);padding:1px 4px;border-radius:3px}" +
    `table{border-collapse:collapse}th,td{border:1px solid ${border};padding:3px 6px}` +
    `a{color:${link}}`
  );
}

function wrapPreviewDoc(inner) {
  const css = previewCss();
  const src = inner == null ? "" : String(inner);
  if (/<html[\s>]/i.test(src)) {
    if (/<head[\s>]/i.test(src)) {
      return src.replace(
        /<head([^>]*)>/i,
        "<head$1><style>" + css + "</style>",
      );
    }
    return src.replace(
      /<html([^>]*)>/i,
      '<html$1><head><meta charset="utf-8"><style>' + css + "</style></head>",
    );
  }
  return (
    '<!doctype html><html><head><meta charset="utf-8">' +
    "<style>" +
    css +
    "</style></head><body>" +
    src +
    "</body></html>"
  );
}

function mdPreviewSrcdoc(html) {
  return wrapPreviewDoc(html);
}

/* обычный текст (txt/log/json/yaml…): тот же каркас документа, но содержимое
   отдаётся как есть в <pre> — marked по одиночным переносам сворачивает
   собранную книгу в один абзац */
function txtPreviewSrcdoc(text) {
  return wrapPreviewDoc(
    '<pre class="preview-plain">' + UICore.escapeHtml(text) + "</pre>",
  );
}

function fitPreviewFrame(frame) {
  // Скрытый кадр (не в DOM или внутри display:none, напр. неактивная
  // под-вкладка «Проверки») не лэится: scrollHeight=0, и после показа
  // высота так и осталась бы 80px. Откладываем подгон до появления.
  if (frame._fitIO) {
    frame._fitIO.disconnect();
    frame._fitIO = null;
  }
  if (!frame.isConnected || !frame.offsetParent) {
    frame.dataset.fitPending = "1";
    const io = new IntersectionObserver((entries) => {
      if (!frame.isConnected) {
        io.disconnect();
        frame._fitIO = null;
        delete frame.dataset.fitPending;
        return;
      }
      if (entries.some((e) => e.isIntersecting)) {
        io.disconnect();
        frame._fitIO = null;
        delete frame.dataset.fitPending;
        fitPreviewFrame(frame);
      }
    });
    frame._fitIO = io;
    io.observe(frame);
    return;
  }
  try {
    const doc = frame.contentDocument;
    const body = doc && doc.body;
    if (!body) return;
    const zoom = getPreviewFontSize() / 16;
    frame.style.zoom = String(zoom);
    frame.style.height = "auto";
    const h = Math.max(
      body.scrollHeight || 0,
      (doc.documentElement && doc.documentElement.scrollHeight) || 0,
    );
    frame.style.height = Math.max(80, h + 8) + "px";
  } catch {
    /* другой origin */
  }
}

function previewFontSelect(onChange) {
  const sel = h("select", {
    class: "input input-inline",
    title:
      "Визуальный кегль (zoom iframe; Chrome не умеет меньше ~16px напрямую)",
  });
  for (const n of PREVIEW_FONT_OPTIONS) {
    sel.append(h("option", { value: String(n) }, n + " px"));
  }
  sel.value = String(getPreviewFontSize());
  sel.addEventListener("change", () => {
    setPreviewFontSize(sel.value);
    if (onChange) onChange();
  });
  return sel;
}

/* Панель «редактор + предпросмотр» живёт в ui-components.js и зовёт эти же
   обёртки: тема и кегль известны здесь, поэтому регистрируем их один раз тут. */
Object.assign(UIC.docs, {
  md: mdPreviewSrcdoc,
  html: wrapPreviewDoc,
  text: txtPreviewSrcdoc,
  fit: fitPreviewFrame,
});

/* ── состояние книги: снимок на сервере (tmp/ui_state) ─────────────────
 * Один снимок на открытую книгу: книга одна, а состояние у каждой своё.
 * Живёт в app.js, потому что здесь fetch, X-Requested-With и знание адреса
 * проекта; вьюхи берут его отсюда — window.bookState(). Загрузка идёт при
 * смене книги (viewProject), записи копятся в снимке и уходят пачкой после
 * паузы ввода. Пока ответа нет, снимок читает localStorage: старая память
 * браузера работает как раньше, а первый заход после обновления поднимает её
 * ключи в файл книги.
 *
 * Уход со страницы дописывает хвост: вкладку переключают и закрывают сразу,
 * а потерять последнюю правку формы из-за этого нельзя. */
let bookStateCarrier = null;
let bookStateKey = "";

function bookState(section, name) {
  const key = section && name ? `${section}/${name}` : "";
  if (bookStateCarrier && bookStateKey === key) return bookStateCarrier;
  bookStateKey = key;
  bookStateCarrier = UICore.bookState(key
    ? {
      load: () => api(`/projects/${encodeURIComponent(section)}/` +
        `${encodeURIComponent(name)}/ui-state`),
      save: (data) => api(
        `/projects/${encodeURIComponent(section)}/` +
        `${encodeURIComponent(name)}/ui-state`,
        { method: "PUT", body: { data } }),
    }
    : null, section || "", name || "");
  if (key) bookStateCarrier.load();
  return bookStateCarrier;
}
window.bookState = bookState;

/* уход со страницы: хвост состояния дописывается, а не теряется */
window.addEventListener("pagehide", () => {
  if (bookStateCarrier) bookStateCarrier.flush();
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && bookStateCarrier) {
    bookStateCarrier.flush();
  }
});

async function api(path, opts = {}) {
  const headers = { "X-Requested-With": "fetch" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch("/api" + path, {
    ...opts,
    headers,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* не-JSON ответ */
  }
  if (!res.ok || (data && data.ok === false)) {
    throw new Error((data && data.error) || `Ошибка ${res.status}`);
  }
  return data;
}

/* Тосты — обычный DOM (Alpine и toast-host на нём убраны). */
function toast(msg, kind = "ok") {
  const t = h("div", { class: `toast toast-${kind}` }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), 3500);
}

/* ── тултипы: единая всплывающая подсказка ────────────────
 * attachTooltip(el, text) — показ по наведению/фокусу, скрытие по
 * уходу; позиция над элементом (прижата к краю окна, у верхнего
 * края — под элементом). Один общий элемент на страницу. */
const _tooltip = (() => {
  const t = h("div", { class: "tooltip" });
  document.body.append(t);
  return t;
})();
function attachTooltip(el, text) {
  if (!el || !text) return;
  el.setAttribute("data-tip", ""); // маркер для CSS-курсора
  const show = () => {
    _tooltip.textContent = text;
    _tooltip.classList.add("tooltip-show");
    const r = el.getBoundingClientRect();
    const tw = _tooltip.offsetWidth;
    const th = _tooltip.offsetHeight;
    const x = Math.max(
      8,
      Math.min(r.left + r.width / 2 - tw / 2, window.innerWidth - tw - 8),
    );
    const above = r.top - th - 6;
    _tooltip.style.left = x + "px";
    _tooltip.style.top =
      (above >= 4 ? above : r.bottom + 6) + "px";
  };
  const hide = () => _tooltip.classList.remove("tooltip-show");
  el.addEventListener("mouseenter", show);
  el.addEventListener("mouseleave", hide);
  el.addEventListener("focus", show);
  el.addEventListener("blur", hide);
  // действие по кнопке — подсказка уже прочитана, а курсор остаётся на ней:
  // тултип не должен закрывать то, что изменилось после клика
  el.addEventListener("pointerdown", hide);
}
// кросс-файловый хелпер: используется в project-views.js (тултипы
// полей форм) — явный экспорт на window, как у window.viewRun
window.attachTooltip = attachTooltip; 

function confirmModal(title, text, confirmWord, onConfirm) {
  const word = h("input", { class: "input", placeholder: confirmWord });
  const err = h("div", { class: "form-error" });
  // Промис: true — подтверждено (onConfirm выполнен), false — отмена.
  // Старые вызовы тоже валидны — возврат промиса можно игнорировать.
  return new Promise((resolve) => {
    /* «Подтвердить» собирается до модалки: кнопка блокируется на время
       onConfirm, а Enter (тот же клик) не зовёт идущее действие второй раз */
    const go = h(
      "button",
      {
        class: "btn btn-danger",
        onclick: async () => {
          if (go.disabled) return;
          go.disabled = true;
          if (word.value.trim().toUpperCase() !== confirmWord) {
            err.textContent = `Введите слово ${confirmWord}`;
            go.disabled = false;
            return;
          }
          try {
            await onConfirm();
          } catch (ex) {
            err.textContent = ex.message;
            go.disabled = false;
            return;
          }
          close(true);
        },
      },
      "Подтвердить",
    );
    const modal = UIC.modal({
      title,
      onClose: resolve,
      build: () => [
        h("div", { class: "modal-text" }, text),
        word,
        err,
        h(
          "div",
          { class: "modal-actions" },
          h("button", { class: "btn btn-ghost", onclick: () => close(false) }, "Отмена"),
          go,
        ),
      ],
    });
    word.focus();
    // go собран до модалки — close ему отдаём поднятым объявлением
    function close(result) {
      return modal.close(result);
    }
  });
}

/* ── экран входа ─────────────────────────────────────────── */
function viewLogin() {
  const token = h("input", {
    type: "password",
    placeholder: "Токен доступа",
    autocomplete: "current-password",
    class: "input",
  });
  const err = h("div", { class: "form-error" });
  const form = h(
    "form",
    {
      class: "login-card",
      onsubmit: async (e) => {
        e.preventDefault();
        err.textContent = "";
        try {
          await api("/login", { method: "POST", body: { token: token.value } });
          state.auth = true;
          render();
        } catch (ex) {
          err.textContent = ex.message;
        }
      },
    },
    h("div", { class: "login-title" }, "NovelMaestro"),
    h("div", { class: "login-subtitle" }, "Вход в web-интерфейс"),
    token,
    err,
    h(
      "button",
      { type: "submit", class: "btn btn-primary btn-block" },
      "Войти",
    ),
  );
  token.focus();
  return h("div", { class: "login-wrap" }, form);
}

/* ── данные hub ──────────────────────────────────────── */
let hubCache = null;

async function loadHub(force = false) {
  if (hubCache && !force) return hubCache;
  const [sections, templates] = await Promise.all([
    api("/sections"),
    api("/templates"),
  ]);
  const bySection = {};
  // списки проектов по разделам — параллельно, а не по очереди
  // (на медленной сети это экономит N-1 RTT)
  const lists = await Promise.all(
    (sections.sections || []).map(async (s) => {
      const r = await api(`/projects?section=${encodeURIComponent(s.name)}`);
      return [s.name, r.items || (r.projects || []).map((n) => ({ name: n }))];
    }),
  );
  const itemsBySection = {};
  for (const [name, items] of lists) {
    itemsBySection[name] = items;
    bySection[name] = items.map((i) => i.name);
  }
  /* stats для карточек берём из кешируемого /dashboard одним запросом,
     а не дергаем /stats на каждый проект. Список
     рендерится сразу, статистика догружается в фоне — удаление
     проекта не ждёт пересчёта stats всех проектов. */
  const cache = {
    sections: sections.sections,
    templates: templates.templates,
    bySection,
    itemsBySection,
    statsMap: {},
    archiveMap: {},
  };
  hubCache = cache;
  api("/dashboard")
    .then((dash) => {
      if (hubCache !== cache) return; // кеш уже перезапрошен — мимо
      const statsMap = {};
      const archiveMap = {};
      if (dash) {
        for (const s of dash.sections || []) {
          for (const p of s.projects || []) {
            statsMap[`${s.name}/${p.name}`] = p.stats;
            if (p.archive) archiveMap[`${s.name}/${p.name}`] = p.archive;
          }
        }
      }
      cache.statsMap = statsMap;
      cache.archiveMap = archiveMap;
      if (parseRoute().view === "hub") render();
    })
    .catch(() => {});
  return cache;
}

async function projectStats(section, name) {
  return api(`/projects/${section}/${name}/stats`);
}

/* ── мастер создания проекта ─────────────────────────────── */
function createProjectModal() {
  const sectionSel = h("select", { class: "input" });
  const name = h("input", { class: "input", placeholder: "my_book" });
  const title = h("input", { class: "input", placeholder: "Русское название" });
  const author = h("input", { class: "input", placeholder: "Автор" });
  const genres = h("input", { class: "input", placeholder: "жанр1, жанр2" });
  const tplSel = h("select", { class: "input" });
  // опциональные загрузки: обложка и исходник для разбора — оба в source/
  const coverFile = h("input", {
    type: "file",
    class: "input",
    accept: ".jpg,.jpeg,.png",
  });
  const srcFile = h("input", {
    type: "file",
    class: "input",
    accept: ".txt,.md,.epub,.zip",
  });
  const err = h("div", { class: "form-error" });

  return new Promise((resolve) => {
    UIC.modal({
      title: "Создать проект",
      wide: true,
      onClose: resolve,
      build: (close) => [
        h("label", { class: "field" }, "Раздел", sectionSel),
        h("label", { class: "field" }, "Имя (английское)", name),
        h(
          "label",
          { class: "field" },
          "Название (русское, опционально)",
          title,
        ),
        h("label", { class: "field" }, "Автор (опционально)", author),
        h(
          "label",
          { class: "field" },
          "Жанры через запятую (опционально)",
          genres,
        ),
        h("label", { class: "field" }, "Шаблон", tplSel),
        h(
          "label",
          { class: "field" },
          "Обложка (опционально, jpg/png)",
          coverFile,
        ),
        h(
          "label",
          { class: "field" },
          "Исходник txt/md/epub/zip (опционально)",
          srcFile,
        ),
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
                err.textContent = "";
                try {
                  const payload = {
                    section: sectionSel.value,
                    name: name.value.trim(),
                    title: title.value.trim(),
                    author: author.value.trim(),
                    genres: genres.value.trim(),
                    template: tplSel.value,
                  };
                  const r = await api("/projects", {
                    method: "POST",
                    body: payload,
                  });
                  // закрыть модалку сразу, а resolve(true) — только после
                  // опциональных загрузок: viewHub перерисует список, иначе
                  // новый проект не появится до обновления страницы
                  close(true);
                  hubCache = null;
                  const proj = `${r.section}/${r.name}`;
                  // опциональные загрузки: обложка и исходник → source/
                  const uploadErrors = [];
                  const cover = coverFile.files && coverFile.files[0];
                  if (cover) {
                    try {
                      const b64 = await new Promise((resolve, reject) => {
                        const fr = new FileReader();
                        fr.onload = () =>
                          resolve(String(fr.result).split(",", 2)[1] || "");
                        fr.onerror = () =>
                          reject(new Error("не удалось прочитать обложку"));
                        fr.readAsDataURL(cover);
                      });
                      await api("/cover", {
                        method: "PUT",
                        body: {
                          project: proj,
                          name: cover.name,
                          content_base64: b64,
                        },
                      });
                    } catch (ex) {
                      uploadErrors.push(`обложка: ${ex.message}`);
                    }
                  }
                  const src = srcFile.files && srcFile.files[0];
                  if (src) {
                    try {
                      const form = new FormData();
                      form.append("dest", "source");
                      form.append("files[]", src, src.name);
                      await apiUpload(`/upload?project=${proj}`, form);
                    } catch (ex) {
                      uploadErrors.push(`исходник: ${ex.message}`);
                    }
                  }
                  toast(
                    r.renamed
                      ? `Создан ${proj} (имя очищено)`
                      : `Создан ${proj}`,
                  );
                  if (uploadErrors.length) {
                    toast(`Проект создан, но: ${uploadErrors.join("; ")}`,
                      "err");
                  }
                  resolve(true);
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
    // разделы и шаблоны — из свежего hub на момент открытия модалки:
    // hubCache мог быть сброшен операциями «Управления разделами» (null),
    // тогда select'ы оставались пустыми — раздел/шаблон выбрать нельзя
    loadHub()
      .then((hub) => {
        for (const s of hub.sections) {
          sectionSel.append(h("option", { value: s.name }, s.name));
        }
        const gen = hub.templates.find((t) => t.name === "General");
        if (gen || hub.templates.length) {
          const list = gen
            ? [gen, ...hub.templates.filter((t) => t !== gen)]
            : hub.templates;
          for (const t of list) {
            tplSel.append(h("option", { value: t.name }, t.name));
          }
        }
        name.focus();
      })
      .catch((ex) => {
        err.textContent =
          "Не удалось загрузить разделы и шаблоны: " + ex.message;
        name.focus();
      });
  });
}

/* ── управление проектом ─────────────────────────────────── */
/* Массовый перенос выделенных книг между разделами: список имён с их
   разделами (человек видит, ЧТО переезжает), выбор целевого раздела и
   отчёт о результатах — занятое имя или битые данные одной книги не
   отменяют перенос остальных. */
function moveManyModal(items) {
  const err = h("div", { class: "form-error" });
  const dstSel = h("select", { class: "input" });
  const list = h("div", { class: "files-list move-many-list" });
  const bySec = {};
  for (const it of items) (bySec[it.section] = bySec[it.section] || []).push(it.name);
  const choice = packChoice();
  for (const [sec, names] of Object.entries(bySec)) {
    list.append(h("div", { class: "frow move-many-row" },
      h("span", { class: "fname" }, sec),
      h("span", { class: "fmeta" }, `${names.length}`),
    ));
    for (const n of names) {
      list.append(h("div", { class: "frow move-many-row" },
        h("span", { class: "fname" }, n),
      ));
    }
  }
  return new Promise((resolve) => {
    UIC.modal({
      title: `Перенести книги: ${items.length}`,
      onClose: resolve,
      build: (close) => [
        h("div", { class: "modal-text" },
          "Книги переезжают вместе с папкой (упакованные — файлом архива) "
          + "и историей точек. Имя, занятое в целевом разделе, — пропуск."),
        list,
        h("div", { class: "manage-row" },
          h("label", { class: "field" }, "Раздел назначения", dstSel),
        ),
        choice.row,
        err,
        h(
          "div",
          { class: "modal-actions" },
          h("button", { class: "btn btn-ghost", onclick: () => close(false) }, "Отмена"),
          h(
            "button",
            {
              class: "btn btn-primary",
              "data-modal-default": "1",
              onclick: async () => {
                err.textContent = "";
                if (!dstSel.value) {
                  err.textContent = "Выберите раздел назначения";
                  return;
                }
                try {
                  const r = await api("/projects/move-many", {
                    method: "POST",
                    body: { dst: dstSel.value, items, pack: choice.pack },
                  });
                  const moved = (r.moved || []).length;
                  const skipped = (r.skipped || []).length;
                  toast(`Перенесено: ${moved}`
                    + (skipped ? `, пропущено: ${skipped}` : ""));
                  close(true);
                } catch (ex) {
                  err.textContent = ex.message;
                }
              },
            },
            "Перенести",
          ),
        ),
      ],
    });
    loadHub()
      .then((hub) => {
        for (const s of hub.sections) {
          dstSel.append(h("option", { value: s.name }, s.name));
        }
        /* предвыбор — СВОЙ раздел не предлагается по умолчанию: перенос в
           него ничего не делает, а он же стоял первым и выглядел как
           готовый ответ */
        const src = new Set(items.map((i) => i.section));
        const other = hub.sections.find((s) => !src.has(s.name));
        if (other) dstSel.value = other.name;
        syncPackChoice(choice, hub, dstSel.value);
        dstSel.onchange = () => syncPackChoice(choice, hub, dstSel.value);
        dstSel.focus();
      })
      .catch((ex) => {
        err.textContent = "Не удалось загрузить разделы: " + ex.message;
      });
  });
}

function manageProjectModal(section, name) {
  const err = h("div", { class: "form-error" });
  const choice = packChoice();
  const info = h("div", { class: "modal-text" });
  const dstSel = h("select", { class: "input" });
  const newName = h("input", { class: "input", placeholder: "new_name" });

  return new Promise((resolve) => {
    projectStats(section, name)
      .then((r) => {
        info.textContent = r.stats;
      })
      .catch(() => {});
    UIC.modal({
      title: `Управление · ${section}/${name}`,
      onClose: resolve,
      build: (close) => [
        info,
        err,
        h(
          "div",
          { class: "manage-grid" },
          h(
            "div",
            { class: "manage-row" },
            h("label", { class: "field" }, "Перенести в раздел", dstSel),
            h(
              "button",
              {
                class: "btn",
                onclick: async () => {
                  err.textContent = "";
                  try {
                    const r = await api("/projects/move", {
                      method: "POST",
                      body: { section, name, dst: dstSel.value, pack: choice.pack },
                    });
                    toast(`Перенесено → ${r.section}/${r.name}`
                      + (r.pack ? ", упаковано" : ""));
                    close(true);
                  } catch (ex) {
                    err.textContent = ex.message;
                  }
                },
              },
              "Перенести",
            ),
          ),
          choice.row,
          h(
            "div",
            { class: "manage-row" },
            h("label", { class: "field" }, "Новое имя", newName),
            h(
              "button",
              {
                class: "btn",
                onclick: async () => {
                  err.textContent = "";
                  try {
                    const r = await api("/projects/rename", {
                      method: "POST",
                      body: { section, name, new_name: newName.value.trim() },
                    });
                    toast(`Переименовано → ${r.name}`);
                    close(true);
                  } catch (ex) {
                    err.textContent = ex.message;
                  }
                },
              },
              "Переименовать",
            ),
          ),
          h(
            "div",
            { class: "manage-row" },
            h(
              "button",
              {
                class: "btn",
                onclick: async () => {
                  err.textContent = "";
                  try {
                    const r = await api("/projects/copy", {
                      method: "POST",
                      body: { section, name, new_name: `${name}_copy` },
                    });
                    toast(`Копия: ${r.section}/${r.name}`);
                    close(true);
                  } catch (ex) {
                    err.textContent = ex.message;
                  }
                },
              },
              "Дублировать",
            ),
            h(
              "button",
              {
                class: "btn btn-danger",
                onclick: () =>
                  confirmModal(
                    "Удаление проекта",
                    `${section}/${name} — все файлы будут удалены безвозвратно.`,
                    "УДАЛИТЬ",
                    async () => {
                      await api("/projects", {
                        method: "DELETE",
                        body: { section, name, confirm: "УДАЛИТЬ" },
                      });
                      toast(`Удалено: ${section}/${name}`);
                      close(true);
                    },
                  ),
              },
              "Удалить",
            ),
            h("button", { class: "btn btn-ghost", onclick: () => close() }, "Закрыть"),
          ),
        ),
      ],
    });
    for (const s of hubCache.sections.filter((x) => x.name !== section)) {
      dstSel.append(h("option", { value: s.name }, s.name));
    }
    syncPackChoice(choice, hubCache, dstSel.value);
    dstSel.onchange = () => syncPackChoice(choice, hubCache, dstSel.value);
    newName.value = name;
  });
}

/* Экспорт глоссария (модалка настроек + скачивание файла) — в project-views.js */

/* Выделение книг в разделе для массовой архивации: набор «раздел/имя»
   живёт здесь, а не в карточке — панель действий должна видеть все
   отмеченные книги сразу. Сбрасывается при перерисовке хаба. */
let hubSelection = new Set();

function hubSelectedKey(section, name) {
  return `${section}/${name}`;
}

/* Работа с архивом видна не в компоненте, а в состоянии хаба: пока запуск
   упаковки/распаковки идёт, карточки показывают «идёт архивация…», а не
   старое «Открыть/Запуски», и после него хаб перечитывает списки. */
let hubArchiving = new Set();

function archiveBusy(section, name) {
  return hubArchiving.has(hubSelectedKey(section, name));
}

/* Ожидание архивации: опрос /api/jobs/active, пока запуски `archive` не
   освободятся. Упаковка большой книги занимает секунды, и всё это время
   карточка должна говорить правду, а не показывать готовый проект. */
async function waitArchiveJobs() {
  for (;;) {
    let busy = false;
    try {
      const d = await api("/jobs/active");
      busy = (d.jobs || []).some((j) => String(j.action) === "archive");
    } catch {
      /* нет связи — не ждём бесконечно: пусть прозвучит ошибка сервера */
      return;
    }
    if (!busy) return;
    await new Promise((r) => setTimeout(r, 500));
  }
}

/* Перечитать хаб после работы с архивом: списки разделов и архивы
   приходят разными запросами, поэтому сбрасываем кеш целиком. */
function refreshHub() {
  hubCache = null;
  render();
}

function sectionBlock(section, projects, sectionActive, statsMap, hub) {
  const items = (hub && hub.itemsBySection && hub.itemsBySection[section.name]) || [];
  const byName = {};
  for (const it of items) byName[it.name] = it;
  const isArchiveSection = !!section.archive;

  function toggleSel(key, on) {
    if (on) hubSelection.add(key);
    else hubSelection.delete(key);
    render();
  }

  /* карточка целиком открывает книгу — кнопок «Открыть»/«Запуски» в ней
     нет: из карточки всегда приходили в один и тот же проект, а место они
     занимали у всех. Последняя вкладка книги живёт в её же состоянии
     (`nmTab` → `page`), поэтому обычная ссылка на книгу открывает именно её;
     шестерёнка — «Управление книгой» (перенос, копия, удаление). */
  const cards = projects.map((name) => {
    const info = byName[name] || {};
    const archived = !!info.archived;
    const busy = archiveBusy(section.name, name);
    const arcMeta = (hub && hub.archiveMap && hub.archiveMap[hubSelectedKey(section.name, name)]) || null;
    const statsLine = archived
      ? `архив · ${fmtSize((arcMeta && arcMeta.size) || info.size || 0)}` +
        ((arcMeta && arcMeta.packed_at) || info.packed_at
          ? ` · ${((arcMeta && arcMeta.packed_at) || info.packed_at).slice(0, 10)}`
          : "")
      : ((statsMap || {})[`${section.name}/${name}`] ?? "…");
    const stats = h("div", { class: "card-hint", text: busy ? "идёт архивация…" : statsLine });
    const key = hubSelectedKey(section.name, name);
    const cb = h("input", {
      type: "checkbox",
      class: "card-check",
      checked: hubSelection.has(key) ? "" : null,
      "aria-label": `Выбрать ${name}`,
      onchange: (e) => toggleSel(key, e.target.checked),
    });
    if (hubSelection.has(key)) cb.checked = true;
    const gear = h(
      "button",
      {
        class: "btn btn-sm btn-ghost icon-btn card-gear",
        type: "button",
        title: `Управление книгой «${name}»: перенос, копия, переименование, удаление`,
        "aria-label": `Управление книгой «${name}»`,
        onclick: (e) => {
          e.preventDefault();
          e.stopPropagation();
          openManage(section.name, name);
        },
      },
      iconEl("settings"),
    );
    /* У живой книги на карточке действий нет: их место — в самом проекте, а
       открытие делает клик по карточке. У упакованной папки нет, поэтому
       «Разархивировать»/«Скачать» остаются: иначе до книги не добраться. */
    const actions = archived
      ? [
          h(
            "button",
            {
              class: "btn btn-sm btn-ghost",
              title: "Распаковать книгу и вернуть папку проекта",
              disabled: busy ? "" : null,
              onclick: (e) => {
                e.preventDefault();
                e.stopPropagation();
                unarchiveOne(section.name, name);
              },
            },
            iconEl("archive-out"),
            "Разархивировать",
          ),
          h(
            "a",
            {
              class: "btn btn-sm btn-ghost",
              title: "Скачать архив .nmz (открывается любым архиватором)",
              href: `/api/projects/${encodeURIComponent(section.name)}`
                + `/${encodeURIComponent(name)}/archive/download`,
              download: `${name}.nmz`,
              onclick: (e) => e.stopPropagation(),
            },
            iconEl("download"),
            "Скачать",
          ),
        ]
      : [];
    return h(
      "div",
      { class: "card project-card" + (archived ? " card-archived" : "") },
      /* «открыть книгу» — ссылка на всю карточку, а не на имя: попадать
         надо и в пустое место у края. Чекбокс и шестерёнка внутри ссылки
         недопустимы, поэтому она лежит отдельным слоем под содержимым
         (см. .card-open в styles.css). */
      h("a", {
        class: "card-open",
        href: `#/project/${encodeURIComponent(section.name)}/${encodeURIComponent(name)}`,
        title: `Открыть проект «${name}»`,
        "aria-label": `Открыть проект «${name}»`,
      }),
      h("div", { class: "card-head" }, cb, h("div", { class: "card-title" }, name), gear),
      stats,
      actions.length ? h("div", { class: "card-actions" }, ...actions) : null,
    );
  });

  /* Отметить весь раздел — одна кнопка: упаковать или распаковать пачку
     решает панель выделения, где эти действия уже есть. Парные кнопки
     раздела дублировали её и молча выбирали за человека вид операции. */
  const allSelected = projects.length > 0
    && projects.every((n) => hubSelection.has(hubSelectedKey(section.name, n)));
  const header = h(
    "div",
    { class: "section-header" },
    h(
      "h2",
      { class: "section-title" },
      section.name,
      h("span", { class: "badge" }, String(projects.length)),
      isArchiveSection ? h("span", { class: "badge badge-archive", title: "Раздел архивный: книги здесь принято держать упакованными" }, "архив") : null,
    ),
    projects.length
      ? h(
          "button",
          {
            class: "btn btn-sm btn-ghost section-select-all",
            title: allSelected
              ? `Снять выделение всех книг раздела ${section.name}`
              : `Выделить все книги раздела ${section.name}`,
            onclick: () => {
              if (allSelected) {
                for (const n of projects) hubSelection.delete(hubSelectedKey(section.name, n));
              } else {
                for (const n of projects) hubSelection.add(hubSelectedKey(section.name, n));
              }
              render();
            },
          },
          allSelected ? "Снять выделение" : "Выделить все",
        )
      : null,
  );
  if (sectionActive) header.classList.add("section-active");
  return h(
    "section",
    { class: "section" },
    header,
    h("div", { class: "cards" }, cards.length ? cards : [h("div", { class: "empty" }, "Нет проектов")]),
  );
}

/* Открыть «Управление книгой» и перечитать хаб, если что-то изменилось
   (перенос/переименование/удаление закрывают модалку с true). */
async function openManage(section, name) {
  if (await manageProjectModal(section, name)) refreshHub();
}

/* Массовый перенос выделенных книг: ключи набора — «раздел/имя», а имя
   книги само может содержать слэш-подобные символы — режем по ПЕРВОМУ. */
async function openMoveMany() {
  const items = [...hubSelection].map((k) => {
    const i = k.indexOf("/");
    return { section: k.slice(0, i), name: k.slice(i + 1) };
  });
  if (!items.length) return;
  if (await moveManyModal(items)) {
    hubSelection = new Set();
    refreshHub();
  }
}

/* Перенос в архивный раздел: «закрыть книгу и освободить место» или
   «оставить каталогом». Спрашиваем всегда и только когда в разделе
   назначения есть что упаковывать: молчаливой упаковки не бывает, а
   вопрос ни о чём — лишний шаг. Возвращает `true` — упаковать. */
function asksPack(hub, dst) {
  const sec = ((hub && hub.sections) || []).find((s) => s.name === dst);
  return !!(sec && sec.pack_on_move);
}

/* Компактный выбор вместо двух кнопок в `.modal-actions`: в окне уже есть
   «Отмена» и дело, и три кнопки подряд читались бы как три исхода. */
function packChoice() {
  const sel = h(
    "select",
    { class: "input" },
    h("option", { value: "keep" }, "Оставить каталогом"),
    h("option", { value: "pack" }, "Закрыть книгу и освободить место"),
  );
  const row = h(
    "div",
    { class: "manage-row pack-choice", hidden: "" },
    h("label", { class: "field" }, "После переноса", sel),
  );
  return { row, sel, get pack() { return sel.value === "pack"; } };
}

/* Раздел назначения сменили — показать или убрать вопрос об упаковке.
   Свойство `hidden` — булево: пустая строка включает скрытие (как и в
   разметке `<div hidden>`), поэтому «показать» — это именно false. */
function syncPackChoice(choice, hub, dst) {
  choice.row.hidden = !asksPack(hub, dst);
  if (choice.row.hidden) choice.sel.value = "keep";
}

/* Подтверждение обратимого действия: слово-пароль (confirmModal) только
   у необратимого удаления, а упаковку можно отменить распаковкой. */
function askModal(title, text, okLabel, onOk) {
  return new Promise((resolve) => {
    UIC.modal({
      title,
      onClose: resolve,
      build: (close) => [
        h("div", { class: "modal-text" }, text),
        h(
          "div",
          { class: "modal-actions" },
          h("button", { class: "btn btn-ghost", onclick: () => close(false) }, "Отмена"),
          h(
            "button",
            {
              class: "btn btn-primary",
              onclick: async () => {
                close(true);
                await onOk();
              },
            },
            okLabel,
          ),
        ),
      ],
    });
  });
}

/* Массовая архивация пачки: один запрос → один запуск с логом.
   Подтверждения спрашиваем всегда: упаковка книги — изменение на диске,
   даже если она обратима. */
async function archiveSelected(op) {
  const keys = [...hubSelection];
  if (!keys.length) return;
  const items = keys.map((k) => {
    const i = k.indexOf("/");
    return { section: k.slice(0, i), name: k.slice(i + 1) };
  });
  const verb = op === "pack" ? "Упаковать" : "Распаковать";
  return askModal(
    `${verb} выбранные проекты`,
    `${verb} ${items.length} проект(ов)? Архив — обычный tar+zstd, его можно открыть любым архиватором; вернуть книгу можно кнопкой «Разархивировать».`,
    verb,
    async () => {
      const r = await api("/projects/archive", {
        method: "POST",
        body: { op, items },
      });
      hubSelection = new Set();
      await afterArchiveStart(r, items, verb);
    },
  );
}

/* После создания запуска архивации: карточки сразу говорят «идёт
   архивация…», а когда запуск завершён — списки перечитываются.
   Без этого карточка продолжала показывать старое состояние (и «Открыть»,
   и «Запуски») до ручного обновления страницы. */
async function afterArchiveStart(r, items, verb) {
  const skipped = (r.skipped || []).length;
  toast(
    `${verb}: ${items.length}` + (skipped ? `, пропущено: ${skipped}` : ""),
  );
  hubArchiving = new Set(items.map((i) => hubSelectedKey(i.section, i.name)));
  hubCache = null;
  render();
  await waitArchiveJobs();
  hubArchiving = new Set();
  refreshHub();
}

async function unarchiveOne(section, name) {
  await askModal(
    "Разархивировать проект",
    `Вернуть ${section}/${name} в виде папки? На диске снова будет занято место, зато откроются редактор, поиск и запуски.`,
    "Разархивировать",
    async () => {
      let r;
      try {
        r = await api("/projects/archive", {
          method: "POST",
          body: { op: "unpack", items: [{ section, name }] },
        });
      } catch (ex) {
        toast(ex.message, "err");
        return;
      }
      if (!(r.items || []).length) {
        toast((r.skipped && r.skipped[0] && r.skipped[0].reason)
          || "Разархивация не запущена", "err");
        refreshHub();
        return;
      }
      await afterArchiveStart(r, [{ section, name }], "Разархивация");
    },
  );
}

/* ── Настройки: системный .env (projects/.env, ) ── */
/* ── Заметки (markdown) ──────────────────────────────────── */
async function viewNotes() {
  const err = h("div", { class: "form-error" });
  /* редактор — тот же, что у файлов («Правка»): CodeMirror + тёмный
     markdown-предпросмотр в sandbox-iframe (скрипты не выполняются) */
  const ed = makeEditor("", "md");
  const pane = UIC.previewPane(ed, {
    small: true,
    frameClass: "notes-frame",
  });
  const status = h("div", { class: "review-status" });
  const saveBtn = h("button", { class: "btn btn-sm btn-primary" }, "Сохранить");
  saveBtn.addEventListener("click", async () => {
    err.textContent = "";
    try {
      await api("/notes", { method: "PUT", body: { content: ed.getValue() } });
      toast("Заметки сохранены");
      status.textContent = "Заметки";
    } catch (ex) {
      err.textContent = ex.message;
    }
  });
  async function loadNotes() {
    try {
      const d = await api("/notes");
      ed.setValue(d.content || "");
      status.textContent = d.exists
        ? "Заметки"
        : "Заметок нет — сохраните, чтобы создать";
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
          "Общие заметки (markdown, projects/notes.md)",
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
          /* подпись и список кегля — одна группа: при переносе тулбара
             подпись не остаётся в хвосте предыдущей строки */
          h(
            "span",
            { class: "toolbar-group" },
            h("span", { class: "field-help" }, "кегль"),
            previewFontSelect(() => {
              if (pane.mode !== "code") pane.render();
            }),
          ),
          h(
            "span",
            { class: "toolbar-actions" },
            UIC.editorSearch(ed),
            pane.btn,
            saveBtn,
          ),
        ),
        err,
        pane.host,
        pane.frame,
      ),
    ),
  );
}

/* ── Настройки: страница собрана карточками-блоками реестра
   (core/settings.py), сгруппированными в субвкладки; своего знания о
   настройках у SPA нет — метки, типы, варианты, значения и подсказки приходят
   с сервера. Субвкладок пять — только настройки книги: блок веб-сервера из
   payload вырезан на сервере. Шестая, «Внешний вид», локальная:
   UI-предпочтения браузера в реестре не живут. Собственного .env у книги нет:
   изменённые для одной книги поля запусков живут в браузере. Кнопки
   «Сохранить» на странице нет: каждое изменённое поле уходит на диск само,
   а состояние автосохранения показывает подпись в шапке. Профиль тоже
   читается из самого селекта. ── */
async function viewSettings() {
  let d;
  try {
    d = await api("/settings");
  } catch (ex) {
    return h("div", { class: "page" }, h("div", { class: "form-error" }, ex.message));
  }
  // локальная субвкладка: preferences браузера, а не настройки конфига (её в
  // ответе API нет, поэтому каждое перечитывание реестра дописывает её снова)
  const uiGroup = { id: "ui", title: "Внешний вид", blocks: [] };
  const model = {
    groups: [...(d.groups || []), uiGroup],
    profiles: d.profiles || [],
    profile: d.profile || "general",
  };
  let active = "";
  try {
    active = localStorage.getItem("settingsTab") || "";
    model.profile = localStorage.getItem("settingsProfile") || model.profile;
  } catch {
    /* нет localStorage — вкладка по умолчанию */
  }
  if (!model.profiles.some((p) => p.id === model.profile)) {
    model.profile = "general";
  }
  if (!model.groups.some((g) => g.id === active)) {
    active = model.groups.length ? model.groups[0].id : "";
  }

  const ctl = {}; // ключ реестра поля → контрол активной вкладки
  const tabs = h("div", { class: "tabs" });
  const cards = h("div", { class: "settings-cards" });
  const err = h("div", { class: "form-error" });

  // ── автосохранение: кнопки «Сохранить» на странице нет ──────────────
  // Одно изменённое поле — одна запись: значения General уходят в общий .env,
  // LLM-ключи выбранного профиля — в llm_profiles.json. Запись ставится на
  // паузу ввода: дёргать конфиг на каждое нажатие клавиши нельзя, и идущая
  // запись бывает одна. Строка в шапке молчит, пока сохранять нечего
  // (подтверждение — тост); ответ API перечитывает model.groups/model.profiles,
  // а НЕ перерисовывает вкладку — иначе из поля выпадал бы фокус.
  const SAVE_DELAY = 500;  // мС тишины после последнего изменения поля
  const status = h("span", { class: "settings-save-state hidden" });
  /* Очередь записи живёт в UICore: накопленные поля забираются и очищаются
     ОДНИМ движением, поэтому правка, пришедшая пока PUT в полёте, попадёт в
     следующий запрос, а не пропадёт на ответе предыдущего. */
  const queue = UICore.saveQueue(SAVE_DELAY);
  const shown = {};        // ключ реестра → последнее значение на экране
  const labels = {};       // ключ реестра → подпись поля (для строки статуса)
  const envWins = new Set(d.env_wins || []);
  const envBlocked = new Set(); // ключи, перекрытые переменными окружения

  function showStatus() {
    // «Внешний вид» — локальная вкладка: конфиг здесь не сохраняется
    const busy = queue.isSaving() || queue.has();
    const blocked = [...envBlocked].map((k) => labels[k] || k);
    const text = active === "ui" ? ""
      : busy ? "сохраняю…"
      : blocked.length ? `перекрыто окружением: ${blocked.join(", ")}` : "";
    status.textContent = text;
    status.className = "settings-save-state"
      + (!text ? " hidden" : busy ? "" : " is-warn");
  }

  // значение поля уехало в память → через паузу ввода один PUT
  function fieldChanged(id, value) {
    if (id in shown && shown[id] === value) return; // фокус или Tab без правки
    shown[id] = value;
    queue.put(id, value);
    if (envWins.has(id)) envBlocked.add(id);
    showStatus();
  }

  async function savePending() {
    // идущая запись сама отправит хвост своим заходом; два PUT параллельно нельзя
    if (queue.isSaving()) return;
    const sent = queue.take();
    if (!sent.keys.length) return;
    queue.begin();
    err.textContent = "";
    showStatus();
    try {
      const r = await api("/settings", {
        method: "PUT", body: { profile: model.profile, values: sent.values },
      });
      // профили перечитываем тоже: поля нераскрытого профиля рисуются из
      // model.profiles, и без этого сохранённое значение исчезало бы из формы
      model.profiles = r.profiles || model.profiles;
      // ответ API несёт только реестр: без локальной вкладки открытая
      // «Внешний вид» исчезла бы и прыгнула на первую
      model.groups = [...(r.groups || []), uiGroup];
      toast(`Сохранено: ${sent.keys.map((k) => labels[k] || k).join(", ")}`);
    } catch (ex) {
      // значения возвращаются в очередь: следующий заход отправит и их
      queue.back(sent.values);
      err.textContent = ex.message;
    } finally {
      queue.end();
      showStatus();
      // хвост, набранный во время запроса, уходит своим заходом
      if (queue.has()) queue.again();
    }
  }
  queue.due(savePending);

  // ── сброс вкладки: её поля возвращаются к значениям по умолчанию ──────
  // Дефолт приходит с сервера в f.default (form_field реестра) — своего знания
  // о настройках у SPA нет. Запись идёт тем же PUT'ом, что и автосохранение:
  // второго пути записи конфига быть не должно.
  function groupDefaults(g) {
    const out = {};
    for (const b of g.blocks || []) {
      for (const f of b.fields || []) {
        if (f.type === "hidden" || f.noenv) continue; // чипсы и служебное
        const d = f.default === undefined || f.default === null ? "" : f.default;
        // textarea хранит переносы в реестре литералом «\n» (как и в .env)
        out[f.key] = f.type === "textarea"
          ? String(d).replace(/\\n/g, "\n")
          : f.type === "bool" ? Boolean(d) : String(d);
      }
    }
    return out;
  }

  async function resetGroup() {
    const g = model.groups.find((x) => x.id === active);
    if (!g || g.id === "ui") return;
    const defaults = groupDefaults(g);
    const keys = Object.keys(defaults);
    if (!keys.length) return;
    // не записанные ещё правки уходят первыми: сброс делает второй PUT
    // и не должен их затереть (менять поле другой вкладки успевают за паузу)
    queue.again(); // пауза ввода не ждёт: остаток — сейчас
    if (queue.has() || queue.isSaving()) await savePending();
    await queue.idle();
    const secrets = (g.blocks || []).some((b) =>
      (b.fields || []).some((f) => f.secret && f.type !== "hidden" && !f.noenv));
    const ok = await confirmModal(
      "Сбросить настройки",
      `Вкладка «${g.title}»: ${keys.length} поле(й) вернётся к значениям по `+
        `умолчанию из реестра`
        + (model.profile === "general" ? "" : ` профиля «${model.profile}»`) + ".\n"
        + "Другие вкладки останутся как есть"
        + (secrets ? "; API-ключ очистится (в реестре его нет)" : "") + ".",
      "СБРОСИТЬ",
      async () => {},
    );
    if (!ok) return;
    err.textContent = "";
    showStatus();
    try {
      const r = await api("/settings", {
        method: "PUT", body: { profile: model.profile, values: defaults },
      });
      model.profiles = r.profiles || model.profiles;
      model.groups = [...(r.groups || []), uiGroup];
      queue.drop(keys);
      for (const k of keys) {
        shown[k] = defaults[k];
        if (envWins.has(k)) envBlocked.add(k);
      }
      // перерисовка здесь безопасна: фокус не в поле (он в модалке), а поля
      // modes/подписей (напр. строка профиля) пересчитываются сами
      renderTab();
      toast(`Сброшено к значениям по умолчанию: ${g.title} (${keys.length})`);
    } catch (ex) {
      err.textContent = ex.message;
    } finally {
      showStatus();
    }
  }

  function tabTools(g) {
    const btn = h("button", {
      class: "btn btn-sm btn-ghost", type: "button",
      onclick: () => resetGroup(),
    }, "Сбросить настройки");
    attachTooltip(btn, `Поля вкладки «${g.title}» вернутся к значениям по `
      + "умолчанию из реестра; значения других вкладок не изменятся");
    return h("div", { class: "settings-tab-tools" }, btn);
  }

  // контрол поля: тип, варианты и подсказка — из реестра (SPA их не знает)
  // где берут значение поля: строка под контролом, а не подпись и не
  // значение — в `.env` и в argv адрес не едет; подпись — из метки поля
  function docLink(f) {
    return h("a", {
      class: "field-doc",
      href: f.doc,
      target: "_blank",
      rel: "noopener noreferrer",
    }, `Получить ${f.label || "значение"}`);
  }

  function fieldWrap(f) {
    const val = f.value === undefined || f.value === null ? "" : f.value;
    if (f.type === "hidden") return null; // чипсы — состояние UI запусков
    // name контрола = ключ реестра: имена полей стадий не уникальны
    const id = f.key || f.name;
    labels[id] = f.label || f.name;
    if (!(id in shown)) {
      shown[id] = f.type === "bool" ? Boolean(val) : String(val);
    }
    if (f.type === "bool") {
      const cb = h("input", { type: "checkbox", class: "checkbox" });
      cb.checked = Boolean(val);
      if (f.help) attachTooltip(cb, f.help);
      ctl[id] = cb;
      /* чекбокс — такое же двухстрочное поле, как соседи: подпись сверху,
         контрол под ней (строкой чекбокс висел по центру колонки и съезжал);
         состояние подписано — одна галочка без слова ничего не сообщает */
      const state = h(
        "span",
        { class: "field-check-state" },
        cb.checked ? "включено" : "выключено",
      );
      cb.addEventListener("change", () => {
        state.textContent = cb.checked ? "включено" : "выключено";
        fieldChanged(id, cb.checked);
      });
      return h(
        "label",
        { class: "field" },
        h("div", { class: "field-label" }, f.label),
        h("span", { class: "field-row field-check" }, cb, state),
      );
    }
    let input;
    if (f.type === "select") {
      input = h("select", { class: "input input-inline", name: id });
      for (const opt of f.options || []) {
        input.append(h("option", { value: opt },
          (f.labels && f.labels[opt]) || opt || "— (пусто)"));
      }
      input.value = String(val);
    } else if (f.type === "textarea") {
      input = h("textarea", {
        class: "input", name: id, rows: String(f.rows || 4),
      });
      input.value = String(val);
    } else {
      const attrs = { class: "input input-inline", name: id };
      if (f.type === "number") {
        attrs.type = "number";
        attrs.step = String(f.step || 1);
        if (f.min !== undefined && f.min !== null) attrs.min = String(f.min);
        if (f.max !== undefined && f.max !== null) attrs.max = String(f.max);
      } else if (f.type === "password") {
        attrs.type = "password";
        attrs.autocomplete = "off";
      } else {
        attrs.type = "text";
      }
      input = h("input", attrs);
      input.value = String(val);
    }
    const wrap = h(
      "label",
      { class: "field" },
      h("div", { class: "field-label" }, f.label),
      input,
    );
    // гайдлайн §7: у select/textarea/password — тултип, у text/number — inline
    if (f.type === "select" || f.type === "textarea" || f.type === "password") {
      attachTooltip(input, f.help || "");
    } else if (f.help) {
      wrap.append(h("div", { class: "field-help" }, f.help));
    }
    if (f.doc) wrap.append(docLink(f));
    ctl[id] = input;
    /* дробь в целочисленном поле: «262,144» из буфера браузер превращает в
       «262.144», а чтение числа берёт целую часть — 262 токена вместо 262144.
       Поле остаётся рабочим (видно и значение, и предупреждение) */
    let hint = null;
    const stepHint = () => {
      const msg = UICore.numberStepHint(f.type, f.step, input.value);
      if (!msg) {
        input.classList.remove("input-bad");
        if (hint) hint.remove();
        hint = null;
        return;
      }
      input.classList.add("input-bad");
      if (!hint) {
        hint = h("div", { class: "field-help is-warn" });
        wrap.append(hint);
      }
      hint.textContent = msg;
    };
    // автосохранение поля: input — текст/число при вводе, change — select и
    // выход из поля; одинаковое значение (фокус, Tab) записи не начинает
    const onField = () => {
      stepHint();
      fieldChanged(id, input.value);
    };
    input.addEventListener("input", onField);
    input.addEventListener("change", onField);
    return wrap;
  }

  // ── профили LLM: один набор серверных настроек на весь конвейер ──
  // General — встроенный (его значения и есть общий .env); остальные живут в
  // llm_profiles рядом с ним и хранят только переопределения. Выбор профиля —
  // выпадающий список: профилей бывает больше трёх, и строка чипсов превращалась
  // во вторую панель вкладок.
  function profileCard() {
    const sel = h("select", {
      class: "input input-inline settings-profile-select",
      "aria-label": "Профиль LLM",
    });
    for (const p of model.profiles) {
      sel.append(h("option", { value: p.id },
        p.builtin ? `${p.name} (общий конфиг)` : p.name));
    }
    sel.value = model.profile;
    attachTooltip(sel, "настройки модели, которые читает стадия; «General" +
      " (общий конфиг)» — значения общего .env");
    const switchTo = (pid) => {
      model.profile = pid;
      try {
        localStorage.setItem("settingsProfile", pid);
      } catch {
        /* нет localStorage — выбор живёт до перезагрузки */
      }
      renderTab();
    };
    sel.addEventListener("change", () => switchTo(sel.value));
    const actions = h("div", { class: "settings-chips" });
    const cur = model.profiles.find((p) => p.id === model.profile) || {};
    /* имена и удаление профиля — те же системные модалки, что у файлов,
       каталогов и разделов: нативный prompt/confirm рисовал второй слой
       интерфейса, а его ошибкой страницы не покажешь */
    actions.append(h("button", {
      class: "btn btn-sm", type: "button",
      onclick: () =>
        nameModal("Новый профиль LLM", "имя профиля", async (nm) => {
          const r = await api("/settings/profiles", {
            method: "POST",
            body: { action: "create", name: nm, base: model.profile },
          });
          model.profiles = r.profiles || model.profiles;
          // switchTo сам пишет localStorage и перерисовывает вкладку;
          // id берётся из ответа: список идёт алфавитом, и созданный
          // профиль стоит на своём месте, а не последним
          switchTo(r.created || model.profiles[0].id);
          toast("Профиль создан");
        }),
    }, "+ Создать профиль"));
    if (!cur.builtin && model.profiles.length > 1) {
      actions.append(h("button", {
        class: "btn btn-sm", type: "button",
        onclick: () =>
          nameModal(`Переименовать профиль ${cur.name}`, "новое имя",
            async (nm) => {
              const r = await api("/settings/profiles", {
                method: "POST",
                body: { action: "rename", id: cur.id, name: nm },
              });
              model.profiles = r.profiles || model.profiles;
              toast("Профиль переименован");
              renderTab();
            }, cur.name),
      }, "Переименовать"));
      actions.append(h("button", {
        class: "btn btn-sm btn-danger", type: "button",
        onclick: () =>
          confirmModal("Удалить профиль",
            `Профиль «${cur.name}» будет удалён вместе со своими ` +
            `переопределениями`, "УДАЛИТЬ", async () => {
              const r = await api("/settings/profiles", {
                method: "POST",
                body: { action: "delete", id: cur.id },
              });
              model.profiles = r.profiles || model.profiles;
              switchTo(model.profiles[0].id);
              toast("Профиль удалён");
            }),
      }, "Удалить"));
    }
    return h(
      "div",
      { class: "review-card review-card-profiles" },
      h("div", { class: "review-card-title" }, "Профили LLM"),
      h(
        "div",
        { class: "review-card-body" },
        h("div", { class: "settings-fields" },
          h("label", { class: "field" },
            h("div", { class: "field-label" }, "Профиль LLM"), sel)),
        actions,
        h("div", { class: "field-help" },
          "Профиль — полный набор настроек работы с моделью: сервер, ключ, "
          + "модель, потоки, таймауты, ретраи и рассуждения. Профиль "
          + "выбирается в запусках проекта. Новый профиль создаётся копией "
          + "выбранного сейчас (без выбора — General); пустое поле профиля "
          + "наследует General."),
      ),
    );
  }

  /* ── внешний вид: тема интерфейса, тема/кегль редакторов —
     UI-предпочтения в localStorage браузера (не .env) ── */
  const edSel = h(
    "select",
    { class: "input input-inline" },
    h("option", { value: "auto" }, "Авто (как интерфейс)"),
    h("option", { value: "dark" }, "Тёмная"),
    h("option", { value: "light" }, "Светлая"),
  );
  edSel.value = EDITOR_SETTINGS.editor;
  const fontIn = h("input", {
    type: "number",
    class: "input input-inline",
    min: "8",
    max: "32",
    value: String(EDITOR_SETTINGS.fontSize),
  });
  /* язык подсветки — тот же выбор браузера: у промптов расширение .txt, у логов
     его нет вовсе, так что язык для них выбирает человек, а не таблица */
  function langSel(key, hint) {
    const sel = h("select", { class: "input input-inline" });
    for (const o of EDITOR_LANGS) sel.append(h("option", { value: o.v }, o.label));
    sel.value = EDITOR_SETTINGS[key];
    if (hint) attachTooltip(sel, hint);
    return sel;
  }
  const promptSel = langSel("langPrompt",
    "язык редактора промптов: у этих файлов расширение .txt, таблица " +
    "расширений им ничего не даёт");
  const logSel = langSel("langLog",
    "язык редактора логов: у логов расширения нет вовсе");
  const lookCard = h(
    "div",
    { class: "review-card" },
    h("div", { class: "review-card-title" }, "Внешний вид"),
    h(
      "div",
      { class: "review-card-body" },
      h(
        "div",
        { class: "files-toolbar look-toolbar" },
        h(
          "label",
          { class: "field" },
          h("div", { class: "field-label" }, "Тема редакторов"),
          edSel,
        ),
        h(
          "label",
          { class: "field" },
          h("div", { class: "field-label" }, "Подсветка промптов"),
          promptSel,
        ),
        h(
          "label",
          { class: "field" },
          h("div", { class: "field-label" }, "Подсветка логов"),
          logSel,
        ),
        h(
          "label",
          { class: "field" },
          h("div", { class: "field-label" }, "Кегль редакторов, px"),
          fontIn,
        ),
      ),
      h(
        "div",
        { class: "field-help" },
        "хранится в браузере (localStorage) — переживает обновление образа" +
          "и пересоздание контейнера; тема интерфейса — переключатель в " +
          "шапке, он рядом с редакторами и не требует заходить в «Настройки»",
      ),
    ),
  );
  const previewFontCard = h(
    "div",
    { class: "review-card" },
    h("div", { class: "review-card-title" }, "Интерфейс"),
    h(
      "div",
      { class: "review-card-body" },
      h(
        "label",
        { class: "field" },
        h(
          "div",
          { class: "field-label" },
          "Кегль предпросмотра (markdown/html)",
        ),
        previewFontSelect(),
        h(
          "div",
          { class: "field-help" },
          "хранится в браузере (localStorage) — как тема интерфейса",
        ),
      ),
    ),
  );

  // ── проверка LLM-сервера: короткий GET /models, генерации нет ──
  // Кнопка живёт в карточке «Подключение»: проверять нужно то, что видно в
  // форме (возможно, ещё не сохранённое), а не только то, что лежит на диске.
  function checkRow() {
    const state = h("span", { class: "settings-check-state" });
    const btn = h("button", { class: "btn btn-sm", type: "button" },
      "Проверить сервер");
    attachTooltip(btn, "Один короткий запрос /models к серверу из формы: доступен " +
      "ли, сколько моделей отдаёт и есть ли в списке модель из конфига");
    btn.addEventListener("click", async () => {
      const values = {};
      for (const [name, c] of Object.entries(ctl)) {
        values[name] = c.type === "checkbox" ? c.checked : c.value;
      }
      state.className = "settings-check-state";
      state.textContent = "проверяю…";
      btn.disabled = true;
      try {
        const r = await api("/settings/check", {
          method: "POST", body: { profile: model.profile, values },
        });
        // недоступный сервер — нормальный ответ отчёта, а не ошибка запроса
        const c = r.check || {};
        const extra = c.has_model === true ? " · модель из конфига есть"
          : c.has_model === false ? " · МОДЕЛИ ИЗ КОНФИГА В СПИСКЕ НЕТ" : "";
        state.className = `settings-check-state ${
          c.ok ? (c.has_model === false ? "is-warn" : "is-ok") : "is-bad"}`;
        state.textContent = c.ok
          ? `жив · ${c.ms} мс · моделей: ${c.models}${extra}`
          : `не отвечает · ${c.ms} мс · ${c.error}`;
      } catch (ex) {
        state.className = "settings-check-state is-bad";
        state.textContent = ex.message;
      } finally {
        btn.disabled = false;
      }
    });
    return h("div", { class: "settings-check" }, btn, state);
  }

  function renderTab() {
    const g = model.groups.find((x) => x.id === active) || model.groups[0];
    if (!g) return;
    for (const node of tabs.children) {
      node.classList.toggle("tab-active", node.getAttribute("data-id") === g.id);
    }
    for (const k of Object.keys(ctl)) delete ctl[k];
    active = g.id;
    try {
      localStorage.setItem("settingsProfile", model.profile);
    } catch {
      /* нет localStorage — выбор живёт до перезагрузки */
    }
    const prof = model.profiles.find((p) => p.id === model.profile) || null;
    if (g.id === "ui") {
      cards.replaceChildren(lookCard, previewFontCard);
      showStatus(); // локальная вкладка: предпочтения браузера, не конфиг
      return;
    }
    const out = [tabTools(g)];
    if (model.profiles.length && g.blocks.some((b) => b.llm)) out.push(profileCard());
    for (const b of g.blocks || []) {
      const useProf = b.llm && prof && !prof.builtin;
      const fields = (b.fields || []).map((f) => {
        if (!useProf || f.noenv) return f;
        return { ...f, value: (prof.values || {})[f.key] || "" };
      });
      const body = [
        ...(b.note ? [h("div", { class: "review-card-note" }, b.note)] : []),
        h("div", { class: "settings-fields" }, ...fields.map(fieldWrap)),
      ];
      if (b.id === "llm_conn") body.push(checkRow());
      out.push(h(
        "div",
        { class: "review-card" },
        h("div", { class: "review-card-title" }, b.title),
        h("div", { class: "review-card-body" }, ...body),
      ));
    }
    cards.replaceChildren(...out);
    showStatus(); // подпись показывает уже сохранённое
  }

  for (const g of model.groups) {
    tabs.append(h("button", {
      class: "tab", "data-id": g.id, type: "button",
      onclick: () => {
        active = g.id;
        try {
          localStorage.setItem("settingsTab", g.id);
        } catch {
          /* нет localStorage — выбор живёт до перезагрузки */
        }
        renderTab();
      },
    }, g.title));
  }

  // «Внешний вид» — та же участь: под кнопки не прячутся, предпочтения браузера
  // пишутся сразу. Кегль читается при создании редактора, поэтому при его смене
  // открытые вкладки перечитываются перерисовкой; тема живёт в атрибутах body.
  function saveLook() {
    const n = Math.max(8, Math.min(32, parseInt(fontIn.value, 10) || 13));
    const resized = n !== EDITOR_SETTINGS.fontSize;
    fontIn.value = String(n);
    EDITOR_SETTINGS.editor =
      edSel.value === "dark" || edSel.value === "light" ? edSel.value : "auto";
    EDITOR_SETTINGS.fontSize = n;
    EDITOR_SETTINGS.langPrompt = promptSel.value;
    EDITOR_SETTINGS.langLog = logSel.value;
    try {
      localStorage.setItem(UI_LOOK_KEY, JSON.stringify(EDITOR_SETTINGS));
    } catch {
      /* localStorage недоступен — не критично */
    }
    applyEditorSettings();
    toast("Сохранено: внешний вид");
    if (resized) render();
  }
  for (const [el, ev] of [[edSel, "change"], [promptSel, "change"],
                         [logSel, "change"], [fontIn, "change"]]) {
    el.addEventListener(ev, saveLook);
  }
  renderTab();

  return h(
    "div",
    { class: "page" },
    h(
      "div",
      { class: "page-header" },
      h("div", { class: "page-header-main" },
        h("h1", { class: "page-title" }, "Настройки")),
      h("div", { class: "page-header-actions" }, status),
    ),
    tabs,
    cards,
    err,
  );
}

/* ── Дашборд ─────────────────────────────────────────── */
async function viewDashboard() {
  let d;
  try {
    d = await api("/dashboard");
  } catch (ex) {
    return h("div", { class: "files-empty" }, ex.message);
  }
  /* сводка: всего проектов и по разделам (без списка — он во вкладке «Проекты») */
  const statCards = h(
    "div",
    { class: "dash-summary" },
    h(
      "div",
      { class: "stat-card" },
      h("div", { class: "stat-num" }, String(d.total ?? 0)),
      h("div", { class: "stat-label" }, "проектов всего"),
    ),
    ...(d.sections || []).map((s) =>
      h(
        "div",
        { class: "stat-card" },
        h("div", { class: "stat-num" }, String((s.projects || []).length)),
        h("div", { class: "stat-label" }, s.name),
      ),
    ),
  );
  /* Текущие активные запуски: строка та же, что карточка «Запусков»,
     только шире — шапка (статус, название, книга, лог) и строка прогресса
     под ней. Прогресс рисуется общим компонентом UIC.progressRow: счётчик,
     фаза и оценка конца («осталось ≈ … · завершение 22:41») переносятся
     строками вместо обрезки, на узком экране строка уйдёт под шапку. */
  const running = d.running_jobs || [];
  /* у running-запуска без событий прогресса строка видна
     («ожидание первого результата…») — раньше возвращался null и
     запуск выглядел мёртвым */
  function progressBar(job, p) {
    if (!p && job.status !== "running") return null;
    return UIC.progressRow({ mini: true })
      .paint(UICore.progressParts(job, p));
  }
  const runRows = running.map((j) => {
    const logLines = j.lines || [];
    const logPre = h("pre", {
      class: "dash-log hidden",
      text: logLines.join("\n") || "(лог пуст)",
    });
    /* шапка строки: статус, название, книга, лог — и действия в правом
       краю; строка прогресса идёт под ней отдельной строкой */
    const head = h(
      "div",
      { class: "active-run-head" },
      h("span", { class: "badge badge-" + j.status }, j.status),
      h(
        "a",
        {
          class: "dash-run-title",
          // активные запуски — на вкладку «Запуски» проекта
          // (авто-подхват активного запуска), история — там же
          href: `#/project/${j.project}/run`,
        },
        j.title || j.action || "запуск",
      ),
      h("span", { class: "job-time" }, j.project),
      h(
        "span",
        { class: "job-time", title: UICore.relTimeAbs(j.created) },
        `строк: ${logLines.length} · ${UICore.relTime(j.created) || "создан " + new Date((j.created || 0) * 1000).toLocaleTimeString()}`,
      ),
      h(
        "span",
        { class: "active-run-actions" },
        h(
          "button",
          { class: "btn btn-sm btn-ghost", onclick: () => logPre.classList.toggle("hidden") },
          "Лог",
        ),
        h(
          "a",
          { class: "btn btn-sm btn-primary", href: `#/project/${j.project}/run` },
          "Открыть",
        ),
        h(
          "button",
          {
            class: "btn btn-sm btn-danger",
            onclick: async () => {
              try {
                await api(`/jobs/${j.id}/stop`, { method: "POST" });
                toast("Остановка...");
                render();
              } catch (ex) {
                toast(ex.message, "err");
              }
            },
          },
          "Стоп",
        ),
      ),
    );
    const mb = progressBar(j, j.progress);
    const row = h("div", { class: "active-run dash-run" }, head);
    if (mb) row.append(mb);
    row.append(logPre);
    return row;
  });
  const jobsCard = h(
    "div",
    { class: "card dash-jobs" },
    h(
      "div",
      { class: "card-title" },
      `Текущие активные запуски (${running.length})`,
      h(
        "button",
        { class: "btn btn-sm btn-ghost dash-refresh", onclick: () => render() },
        "Обновить",
      ),
    ),
    runRows.length
      ? h("div", { class: "dash-runs" }, runRows)
      : h("div", { class: "card-hint" }, "Активных запусков нет"),
  );
  const recentRows = (d.recent_jobs || []).map((j) =>
    h(
      "tr",
      {
        class: "dash-recent-row",
        // клик по истории — в проект на вкладку «Запуски»,
        // а не в конкретный (возможно завершённый) запуск с логом
        onclick: () => {
          location.hash = `#/project/${j.project}/run`;
        },
      },
      h("td", {}, j.title || j.action),
      h("td", { class: "ner-type" }, j.project),
      h("td", {}, h("span", { class: "badge badge-" + j.status }, j.status)),
      h("td", {}, progressBar(j, j.progress) || "—"),
      h(
        "td",
        { title: UICore.relTimeAbs(j.created) },
        UICore.relTime(j.created) || "—",
      ),
    ),
  );
  const recentCard = h(
    "div",
    { class: "card dash-jobs" },
    h(
      "div",
      { class: "card-title" },
      "Последние запуски",
      h(
        "button",
        {
          class: "btn btn-sm btn-ghost dash-refresh",
          onclick: () =>
            confirmModal(
              "Очистить историю запусков",
              "Все записи о запусках будут удалены",
              "УДАЛИТЬ",
              async () => {
                try {
                  await api("/jobs", { method: "DELETE" });
                  toast("История запусков очищена");
                  render();
                } catch (ex) {
                  toast(ex.message, "err");
                }
              },
            ),
        },
        "Очистить",
      ),
    ),
    recentRows.length
      ? h(
          /* узкое окно: таблица шире карточки — лучше честная
             горизонтальная прокрутка, чем колонка «Прогресс» в шесть
             строк и уползшая за край «Дата» */
          "div",
          { class: "dash-table" },
          h(
            "table",
            { class: "ner-table" },
            h(
              "thead",
              {},
              h(
                "tr",
                {},
                h("th", {}, "Задача"),
                h("th", {}, "Проект"),
                h("th", {}, "Статус"),
                h("th", {}, "Прогресс"),
                h("th", {}, "Дата"),
              ),
            ),
            h("tbody", {}, recentRows),
          ),
        )
      : h("div", { class: "card-hint" }, "Запусков ещё не было"),
  );
  const hub = d.hub || {};
  const lastCard =
    hub.section && hub.project
      ? h(
          "div",
          { class: "card dash-last" },
          h("div", { class: "card-title" }, "Продолжить"),
          h("div", { class: "card-sub" }, `${hub.section}/${hub.project}`),
          h(
            "div",
            { class: "card-actions" },
            h(
              "a",
              {
                class: "btn btn-sm btn-primary",
                href: `#/project/${hub.section}/${hub.project}`,
              },
              "Открыть проект",
            ),
            h(
              "a",
              {
                class: "btn btn-sm btn-ghost",
                href: `#/project/${hub.section}/${hub.project}/run`,
              },
              "Запуски",
            ),
          ),
        )
      : null;
  return h(
    "div",
    { class: "page" },
    h(
      "div",
      { class: "page-header" },
      h(
        "div",
        { class: "page-header-main" },
        h("h1", { class: "page-title" }, "Дашборд"),
        h(
          "div",
          { class: "page-sub" },
          `Проектов: ${d.total ?? "…"} · стадии: ${(d.sections || []).map((s) => `${s.name}: ${(s.projects || []).length}`).join(", ") || "—"}`,
        ),
      ),
    ),
    statCards,
    lastCard,
    jobsCard,
    recentCard,
  );
}

// автообновление дашборда: раз в 20 с перечитываем сводку и запуски.
// таймер НЕ регистрируется один раз навсегда — ensureDashTimer
// пересоздаёт его при каждом входе на дашборд (иначе после первого ухода
// автообновление умирало навсегда).
let dashTimer = null;
function ensureDashTimer() {
  if (!dashTimer) {
    dashTimer = setInterval(() => {
      if (parseRoute().view === "dashboard") render();
    }, 20000);
  }
}
// поколение рендера — устаревший async-рендер не перетирает #app
// (гонка таймер/навигация, та же идея, что st.gen в run-views.js)
let renderGen = 0;

/* Панель выделения — на месте кнопок шапки хаба. Своей строкой она шла
   ПОД шапкой и сдвигала вниз все карточки: раскладка прыгала при каждом
   клике по чекбоксу, хотя выделение ничего не «открывает». */
function selectionBar() {
  const keys = [...hubSelection];
  return h(
    "div",
    { class: "page-header-actions selection-bar" },
    h("span", { class: "selection-count" }, `Выбрано: ${keys.length}`),
    h(
      "button",
      {
        class: "btn btn-sm btn-primary",
        title: "Упаковать выделенные книги в архивы .nmz",
        onclick: () => archiveSelected("pack"),
      },
      iconEl("archive-in"),
      "Архивировать",
    ),
    h(
      "button",
      {
        class: "btn btn-sm btn-ghost",
        title: "Вернуть выделенные книги папками",
        onclick: () => archiveSelected("unpack"),
      },
      iconEl("archive-out"),
      "Разархивировать",
    ),
    h(
      "button",
      {
        class: "btn btn-sm btn-ghost",
        title: "Перенести все выделенные книги в другой раздел",
        onclick: () => openMoveMany(),
      },
      "Перенести",
    ),
    /* Групповые действия одного объекта — здесь, а не на карточке: в строке
       шириной 250 px три подписи не влезают и уезжают за край */
    h(
      "button",
      {
        class: "btn btn-sm btn-ghost",
        title: "Перенести, дублировать или удалить одну книгу",
        disabled: keys.length === 1 ? null : "",
        onclick: () => {
          const [section, ...name] = keys[0].split("/");
          openManage(section, name.join("/"));
        },
      },
      "Управление",
    ),
    h(
      "button",
      {
        class: "btn btn-sm btn-ghost icon-btn",
        title: "Снять выделение",
        "aria-label": "Снять выделение",
        onclick: () => {
          hubSelection = new Set();
          render();
        },
      },
      iconEl("close"),
    ),
  );
}

async function viewHub() {
  const hub = await loadHub();
  const sections = hub.sections;
  const stateApi = await api("/state").catch(() => ({}));
  const activeSection = stateApi.section || "";
  const totalProjects = sections.reduce(
    (n, s) => n + (hub.bySection[s.name] || []).length,
    0,
  );
  return h(
    "div",
    { class: "page" },
    h(
      "div",
      { class: "page-header" },
      h(
        "div",
        { class: "page-header-main" },
        h("h1", { class: "page-title" }, "Проекты"),
        h("div", { class: "page-sub" }, `Проектов: ${totalProjects}`),
      ),
      /* Панель выделения занимает место кнопок тулбара, пока есть отметки:
         раньше она шла отдельной строкой под шапкой и сдвигала вниз все
         карточки — раскладка прыгала на ровном месте. Кнопки шапки при
         выделении не нужны: в проекты и разделы отсюда не ходят. */
      hubSelection.size
        ? selectionBar()
        : h(
            "div",
            { class: "page-header-actions" },
            h(
              "button",
              {
                class: "btn btn-primary",
                onclick: async () => {
                  if (await createProjectModal()) refreshHub();
                },
              },
              "＋ Проект",
            ),
            h(
              "button",
              { class: "btn btn-ghost", onclick: () => sectionsModal() },
              "Управление разделами",
            ),
          ),
    ),
    h(
      "div",
      { class: "hub-sections" },
      ...sections.map((s) =>
        sectionBlock(
          s,
          hub.bySection[s.name] || [],
          s.name === activeSection,
          hub.statsMap || {},
          hub,
        ),
      ),
    ),
  );
}

/* ── файловый менеджер ─────────────────────────────── */

function fmtSize(n) {
  if (n >= 1048576) return (n / 1048576).toFixed(1) + " МБ";
  if (n >= 1024) return (n / 1024).toFixed(1) + " КБ";
  return n + " Б";
}

function crumb(text, fn) {
  return h(
    "a",
    {
      class: "crumb",
      href: "#",
      onclick: (e) => {
        e.preventDefault();
        fn();
      },
    },
    text,
  );
}

async function apiUpload(path, form) {
  const res = await fetch("/api" + path, {
    method: "POST",
    headers: { "X-Requested-With": "fetch" },
    body: form,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* не-JSON */
  }
  if (!res.ok || (data && data.ok === false)) {
    throw new Error((data && data.error) || `Ошибка ${res.status}`);
  }
  return data;
}

/* ── Редактор файлов: CodeMirror c fallback на textarea ── */

const CM_READY =
  typeof window !== "undefined" && window.CM && window.CM.EditorView;

/* Расширение → язык CM; без совпадения — простой текст. Выбор с «Внешнего вида»
   приходИТ уже именем языка, поэтому cmLang сначала пробует его. Языки
   соответствуют тому, что лежит в книге: главы/промпты/логи — текст, данные —
   JSON и YAML, wiki — markdown и HTML, сборки и служебка epub — XML (fb2, opf,
   ncx, xhtml) и .env книги — properties (KEY=VALUE с «#»). */
const CM_LANG_BY_EXT = {
  md: "markdown",
  markdown: "markdown",
  html: "html",
  htm: "html",
  xhtml: "html",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  py: "python",
  python: "python",
  xml: "xml",
  fb2: "xml",
  opf: "xml",
  ncx: "xml",
  env: "properties",
  properties: "properties",
  ini: "properties",
};


function extOf(path) {
  const base =
    String(path || "")
      .split("/")
      .pop() || "";
  const dot = base.lastIndexOf(".");
  return dot < 0 ? "" : base.slice(dot + 1).toLowerCase();
}

/* Язык редактора файла даёт UICore.editorLang (свой выбор или расширение);
   здесь — приведение к имени языка бандла и сама подсветка. */
function cmLang(nameOrExt) {
  const langs = window.CM && window.CM.langs;
  if (!langs) return null;
  // выбор с «Внешнего вида» — имя языка, остальное — расширение файла
  const kind = langs[nameOrExt] ? nameOrExt : CM_LANG_BY_EXT[nameOrExt] || "";
  if (!kind || !langs[kind]) return null;
  return langs[kind]();
}


/* Единая фабрика редактора: { root, getValue, setValue, setLang } */
/* Живые редакторы текущего view: [{view, hlComp}]. Реестр собирается заново на
   каждый render(): тема перекрашивает подсветку на месте, пересоздавать
   редактор нельзя — в нём несохранённый текст. */
const LIVE_EDITORS = [];

/* подсветка по теме редактора: свой HighlightStyle (не-fallback — выигрывает у
   светлого дефолта basicSetup) */
function highlightExt() {
  const sh = window.CM && window.CM.syntaxHighlighting;
  const hl = EDITOR_HIGHLIGHT && EDITOR_HIGHLIGHT[effectiveEditorTheme()];
  return sh && hl ? sh(hl) : [];
}

function makeEditor(initial, langExt, onUpdate) {
  if (CM_READY) {
    const { EditorView, Compartment, basicSetup, search } = window.CM;
    const langComp = new Compartment();
    const roComp = new Compartment();
    const hlComp = new Compartment();
    const ext = [
      basicSetup,
      search({ top: true }),
      langComp.of(cmLang(langExt) || []),
      roComp.of([]),
      hlComp.of(highlightExt()),
      EditorView.lineWrapping, // длинные строки переносятся
      EditorView.theme({
        "&": {
          height: "100%",
          fontSize: EDITOR_SETTINGS.fontSize + "px",
        },
        ".cm-scroller": { fontFamily: "inherit" },
      }),
      // колбэк обновлений CM (doc / viewport / selection)
      ...(onUpdate ? [EditorView.updateListener.of((u) => onUpdate(u))] : []),
    ];
    const view = new EditorView({
      doc: initial,
      extensions: ext,
      parent: null,
    });
    LIVE_EDITORS.push({ view, hlComp });
    return {
      root: view.dom,
      isCM: true,
      getValue: () => view.state.doc.toString(),
      setValue: (text) =>
        view.dispatch({
          changes: { from: 0, to: view.state.doc.length, insert: text },
        }),
      setLang: (ext) =>
        view.dispatch({
          effects: langComp.reconfigure(cmLang(ext) || []),
        }),
      setReadOnly: (ro) =>
        view.dispatch({
          effects: roComp.reconfigure(ro ? EditorView.editable.of(false) : []),
        }),
      view,
    };
  }
  // fallback: обычная textarea (vendor не загрузился)
  const ta = h("textarea", { class: "editor-area", spellcheck: "false" });
  ta.value = initial;
  ta.style.fontSize = EDITOR_SETTINGS.fontSize + "px";
  return {
    root: ta,
    isCM: false,
    getValue: () => ta.value,
    setValue: (text) => (ta.value = text),
    setLang: () => {},
    setReadOnly: (ro) => (ta.readOnly = ro),
  };
}

function viewUnknown(route) {
  return h(
    "div",
    { class: "page" },
    h(
      "div",
      { class: "page-header" },
      h(
        "div",
        { class: "page-header-main" },
        h("h1", { class: "page-title" }, "Неизвестный экран"),
        h("div", { class: "page-sub" }, `#/${route}`),
      ),
    ),
  );
}

/* SVG-иконка как DOM-узел (строку UICore.icon нельзя дать h() —
   экранирует). Разбор XML-парсером (не innerHTML): строка —
   доверенная константа из ui-core, скрипты в xml-режиме не исполняются. */

/* ── выпадающие меню (kebab «⋮» и групповые кнопки тулбара) ────────
   Меню позиционируется position:fixed ОТ ВЬЮПОРТА, а не absolute от
   строки: карточка списка файлов (.files-list) с overflow:hidden
   обрезала dropdown по собственной высоте — у последней строки из
   78px меню оставалось ~4px. Ancestor-overflow fixed-потомка не
   режет, а разворот вверх и зажим по краям окна доводят меню целиком.
   Узел меню всегда живёт в своей строке — в DOM ничего не переносим. */
const MENU_GAP = 6; // зазор кнопка ↔ меню, px
const MENU_EDGE = 8; // минимальный отступ от края окна, px

let _openMenu = null; // { btn, menu } — открыто максимум одно меню

/* Координаты fixed-меню по кнопке — вся геометрия в UICore.menuPlacement
   (чистая функция, юнит-тесты tests/spa/ui-core.test.mjs). */
function placeMenu(btn, menu) {
  if (!menu.isConnected) { // строку перерисовали при открытом меню
    closeMenus();
    return;
  }
  const p = UICore.menuPlacement(
    btn.getBoundingClientRect(),
    { width: menu.offsetWidth, height: menu.offsetHeight },
    { width: window.innerWidth, height: window.innerHeight },
    MENU_GAP,
    MENU_EDGE,
  );
  menu.style.top = `${p.top}px`;
  menu.style.left = `${p.left}px`;
}

function closeMenus() {
  if (!_openMenu) return;
  const { btn, menu } = _openMenu;
  _openMenu = null;
  menu.style.top = "";
  menu.style.left = "";
  menu.classList.add("hidden");
  btn.setAttribute("aria-expanded", "false");
}

function openMenu(btn, menu) {
  closeMenus(); // одно меню на страницу
  menu.classList.remove("hidden");
  btn.setAttribute("aria-expanded", "true");
  _openMenu = { btn, menu };
  placeMenu(btn, menu);
}

/* Переключатель кнопок-меню: та же кнопка — закрыть, другая — открыть. */
function toggleMenu(btn, menu) {
  if (_openMenu && _openMenu.menu === menu) closeMenus();
  else openMenu(btn, menu);
}

// capture: срабатывает раньше обработчика кнопки — повторный клик своей
// же кнопки не закрывается дважды, клик по пункту меню не закрывает раньше действия
document.addEventListener(
  "click",
  (ev) => {
    if (!_openMenu) return;
    const { btn, menu } = _openMenu;
    if (menu.contains(ev.target) || btn.contains(ev.target)) return;
    closeMenus();
  },
  true,
);
document.addEventListener(
  "keydown",
  (ev) => {
    if (ev.key === "Escape") closeMenus();
  },
  true,
);
// fixed-меню не привязано к потоку — едет за кнопкой при скролле/ресайзе
window.addEventListener("resize", () => {
  if (_openMenu) placeMenu(_openMenu.btn, _openMenu.menu);
});
document.addEventListener(
  "scroll",
  () => {
    if (_openMenu) placeMenu(_openMenu.btn, _openMenu.menu);
  },
  true,
);
// кросс-файловый хелпер: групповые кнопки «+»/«x» глоссария рендерит
// project-views.js — общее меню позиционируется одной функцией
window.closeMenus = closeMenus;
window.toggleMenu = toggleMenu;

/* Кнопка «⋮» с выпадающим меню (действия строки, меню пользователя) —
   тонкая обёртка над общим UIC.menuButton: SVG-иконка и aria-метка. */
function menuBtn(items, ariaLabel = "Дополнительные действия") {
  return UIC.menuButton(items, {
    iconName: "kebab",
    aria: ariaLabel,
    btnClass: "kebab-btn",
    wrapClass: "menu-wrap",
    menuClass: "user-menu",
    itemClass: "user-menu-item",
  });
}

/* ── каркас страницы: шапка с глобальной навигацией (сайдбара нет —
   6 статичных пунктов дешевле держать в шапке, контенту нужна ширина) ── */
const NAV_ITEMS = [
  ["dashboard", "Дашборд", "dashboard"],
  ["hub", "Проекты", "folder"],
  ["templates", "Шаблоны", "layers"],
  ["notes", "Заметки", "file-text"],
  ["settings", "Настройки", "sliders"],
  ["help", "Справка", "help"],
];

/* Тема интерфейса — переключатель в шапке: она нужна с любого экрана, а не
   через настройки. Редакторы перекрашиваются на месте (полный
   render() перечитал бы файлы и выбросил несохранённый текст), поэтому здесь
   только CSS-переменные темы + reconfigure подсветки. Кадры предпросмотра,
   уже открытые на экране, перекрасятся при следующем открытии: их srcdoc
   собран в старом цвете. */
function setUiTheme(ui) {
  const next = ui === "light" ? "light" : "dark";
  if (EDITOR_SETTINGS.ui === next) return false;
  EDITOR_SETTINGS.ui = next;
  try {
    localStorage.setItem(UI_LOOK_KEY, JSON.stringify(EDITOR_SETTINGS));
  } catch {
    /* localStorage недоступен — не критично */
  }
  applyEditorSettings();
  const ext = highlightExt();
  for (const e of LIVE_EDITORS) {
    if (!e.view.dom.isConnected) continue; // view уже выбросили
    e.view.dispatch({ effects: e.hlComp.reconfigure(ext) });
  }
  return true;
}

function themeSwitch() {
  const btn = h("button", {
    class: "btn btn-sm btn-ghost theme-switch",
    type: "button",
  });
  const paint = () => {
    const dark = EDITOR_SETTINGS.ui !== "light";
    btn.replaceChildren(iconEl(dark ? "moon" : "sun"));
    const text = "Тема интерфейса: " + (dark ? "тёмная" : "светлая");
    btn.setAttribute("aria-label", text);
    btn.setAttribute("title", text + " — переключить");
  };
  btn.addEventListener("click", () => {
    const next = UICore.toggleUiTheme(EDITOR_SETTINGS.ui);
    if (!setUiTheme(next)) return;
    paint();
    toast("Тема интерфейса: " + (next === "light" ? "светлая" : "тёмная"));
  });
  paint();
  return btn;
}

/* ── обновления: проверка релиза и обновление по кнопке ───

   Способ обновления зависит от установки (update.mode с сервера, core/update.py):
   • command (Docker) — контейнер не заменяет собственный образ: интерфейс
     показывает команду, её выполняет хост;
   • git (обычная установка) — перемотка своей ветки через dulwich: данные книг,
     шаблоны и .env в .gitignore, обновление их не видит;
   • zip (портативная сборка и копия исходников) — файлы архива релиза
     кладутся поверх текущих, ничего не удаляется, данные не тронуты.

   Заметки релиза приходят markdown'ом (GitHub сам пишет «**Full Changelog**:
   …»), поэтому их рендерит marked: текст с «**» внутри панели выглядит
   испорченным, а ссылку на сравнение версий нужно нажать. Ссылки на релиз и
   на сравнение интерфейс показывает отдельными ссылками.

   Проверка проходит при запуске сервера сама (свой поток, одна короткая GET-
   проверка) и по кнопке «Проверить»; сессия отдаёт кеш без сети. Пока обновления
   не проверялись — тихо; после проверки и появления нового релиза — точка-бейдж
   на меню «⋮» и один тост. Долгое обновление идёт в отдельном потоке сервера,
   его ход читает GET /api/update/status.

   Перезапуск — часть обновления, а не отдельный ритуал: в запуске из репозитория
   и в копии исходников сервер сворачивается и поднимается сам (состояние
   `restarting`), страница ждёт его и перезагружается. Портативная сборка и Docker
   перезапускаются руками — текст об этом приходит с сервера (update.restart). */
const UPDATE_POLL_MS = 6 * 60 * 60 * 1000; // фоновая перепроверка раз в 6 ч
const UPDATE_JOB_MS = 1500;                // ход обновления, пока он идёт
const UPDATE_BOOT_MS = 4000;               // забрать проверку запуска сервера
const UPDATE_BOOT_TRIES = 5;               // сколько раз её подождать
let updatePollTimer = null;
let updateJobTimer = null;
let updateBootTries = 0;
let updateChecking = false; // проверка идёт: кнопка не жмётся второй раз
let updateCheckError = "";  // последний отказ проверки: сеть, 500, не-JSON
/* перезапуск делает сервер сам (git, копия исходников) — по нему решается,
   ждать ответа сервера или звать пользователя перезапустить руками */
let updateRestartAuto = false;

/* Панель «⋮» собирается один раз за рендер, а ход обновления меняется между
   рендерами: узел заменяется целиком, состояние живёт в state.update. */
function updateBadgeShow(on) {
  const btn = document.querySelector(".menu-wrap .kebab-btn");
  if (btn) btn.classList.toggle("has-update", !!on);
}

/* строка о перезапуске: её пишет сервер (u.restart), здесь только показ */
function updateRestartBox(u) {
  const note = ((u.restart || {}).note || "").trim();
  if (!note) return null;
  return h("div", { class: "update-how update-restart" }, note);
}

function updateBootBox() {
  return document.querySelector(".user-menu .update-box");
}

function refreshUpdateBox() {
  const old = updateBootBox();
  if (!old) return;
  old.replaceWith(updateMenuItem());
  // панель сменила высоту: открытое меню переставляем по кнопке
  if (_openMenu) placeMenu(_openMenu.btn, _openMenu.menu);
}

/* строка о неудачной проверке: сеть недоступна — это не «обновлений нет»,
   и в панели должен быть виден сам отказ, а не только тост */
function updateErrorBox() {
  if (!updateCheckError) return null;
  return h("div", { class: "update-how update-error" },
    `Проверить не удалось: ${updateCheckError}`);
}

function updateDate(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(d);
}

/* Одна ссылка панели (страница релиза, сравнение версий): наружу и без
   window.opener — клик не должен уводить SPA из этой вкладки */
function updateLink(href, label) {
  return h("a", {
    class: "update-link",
    href,
    target: "_blank",
    rel: "noopener noreferrer",
  }, label);
}

/* Проверка при запуске сервера идёт в фоне: забрать её результат молча, парой
   попыток — пока сервер не ответил, просить его бессмысленно. */
async function updateAdoptBootCheck() {
  if (updateBootTries >= UPDATE_BOOT_TRIES) return;
  updateBootTries += 1;
  setTimeout(async () => {
    if (!state.auth || (state.update && state.update.checked)) return;
    let upd = null;
    try {
      const r = await api("/session");
      upd = r.update || null;
    } catch (ex) {
      return;
    }
    if (!upd) return;
    state.update = upd;
    updateBadgeShow(!!upd.available);
    if (upd.job && (upd.job.state === "running" || upd.job.state === "restarting"))
      updateJobPoll();
    else if (upd.available)
      toast(`Доступна новая версия: v${(upd.release || {}).version || "?"}`);
    refreshUpdateBox();
  }, UPDATE_BOOT_MS);
}

/* Заметки релиза — отрендеренный markdown, а не текст со звёздочками: тот же
   marked и та же очистка, что у справки; голые ссылки marked красит сам. */
function updateNotes(text) {
  const raw = String(text || "").trim();
  if (!raw) return null;
  if (!window.marked) return h("div", { class: "update-notes" }, raw);
  const html = window.marked.parse(raw, {
    mangle: false,
    headerIds: false,
    breaks: true,
  });
  const frag = document.createRange().createContextualFragment(html);
  _helpSanitize(frag);
  // markdown-блоки (заголовки, списки) красит общий md-body — в панели компактно
  const box = h("div", { class: "update-notes md-body" });
  box.append(frag);
  box.querySelectorAll("a[href]").forEach((a) => {
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  });
  return box;
}

function updateCheckBtn(label) {
  return h("button", {
    class: "btn btn-sm btn-ghost update-btn",
    disabled: updateChecking,
    onclick: () => checkUpdates(true),
  }, updateChecking ? "Проверяю…" : label);
}

/* Проверка идёт через сеть (до CHECK_TIMEOUT секунд): панель показывает, что
   запрос принят, и перерисовывается итогом — иначе в открытом меню остаётся
   старое «не проверялось», а вердикт виден только тостом и после перезагрузки. */
async function checkUpdates(showToast) {
  if (!state.auth || updateChecking) return;
  updateChecking = true;
  updateCheckError = "";
  refreshUpdateBox();
  try {
    const r = await api("/update/check", { method: "POST" });
    state.update = r.update || state.update;
    updateBadgeShow(!!(state.update && state.update.available));
    if (showToast) {
      toast(state.update && state.update.available
        ? `Доступна новая версия: v${state.update.release.version}`
        : "Обновлений нет — у вас последняя версия");
    }
    if (state.update && state.update.job
        && (state.update.job.state === "running"
            || state.update.job.state === "restarting")) updateJobPoll();
  } catch (ex) {
    updateCheckError = ex.message;
    if (showToast) toast(`Обновление не проверить: ${ex.message}`, "err");
  } finally {
    updateChecking = false;
    refreshUpdateBox();
  }
}

/* Опросы обновления: редкая фоновая перепроверка и ожидание проверки, которую
   сервер делает при запуске в своём потоке (она обычно приходит позже первой
   отрисовки). */
function ensureUpdatePolling() {
  if (updatePollTimer) return;
  updatePollTimer = setInterval(() => checkUpdates(false), UPDATE_POLL_MS);
  updateAdoptBootCheck();
}

/* Ход обновления: поток сервера пишет лог, здесь он только показан.

   Авто-перезапуск выглядит как ошибка сети: процесс сворачивается и на запросы
   не отвечает. Это часть плана, а не поломка — опрос живёт, пока сервер не
   ответит, и тогда страница перезагружается сама: на диске код уже новый. */
function updateJobPoll() {
  if (updateJobTimer) return;
  updateJobTimer = setInterval(async () => {
    let r = null;
    try {
      r = await api("/update/status");
    } catch (ex) {
      r = null;  // сервер прямо сейчас перезапускается
    }
    if (!r || !r.update) {
      if (state.update && state.update.job) {
        state.update = Object.assign({}, state.update, {
          job: Object.assign({}, state.update.job, { state: "restarting" }),
        });
        refreshUpdateBox();
      }
      return;
    }
    state.update = r.update;
    const job = state.update.job || {};
    updateBadgeShow(!!state.update.available);
    if (job.state === "running" || job.state === "restarting") {
      refreshUpdateBox();
      return;
    }
    clearInterval(updateJobTimer);
    updateJobTimer = null;
    if (job.state === "error") {
      toast(`Обновление не применено: ${job.error || "неизвестная причина"}`, "err");
    } else if (updateRestartAuto) {
      toast(`Готово: версия v${state.update.current}, сервер перезапущен`);
      setTimeout(() => location.reload(), 900);
    } else {
      toast(`Программа обновлена до v${job.to || "?"} — перезапустите сервер`);
    }
    updateRestartAuto = false;
    refreshUpdateBox();
  }, UPDATE_JOB_MS);
}

async function updateApply() {
  try {
    const r = await api("/update/apply", { method: "POST" });
    state.update = r.update || state.update;
    updateRestartAuto = !!(state.update && state.update.restart
      && state.update.restart.auto);
    toast(updateRestartAuto
      ? "Обновление началось: сервер перезапустится сам"
      : "Обновление началось: по окончании сервер перезапускают руками");
  } catch (ex) {
    toast(ex.message, "err");
  }
  refreshUpdateBox();
  updateJobPoll();
}

/* Архив релиза кнопкой: fetch (в window.open нет Content-Disposition),
   имя файла — имя ассета релиза. */
async function updateDownload(version) {
  try {
    const res = await fetch("/api/update/download",
      { headers: { "X-Requested-With": "fetch" } });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new Error((data && data.error) || `Ошибка ${res.status}`);
    }
    const blob = await res.blob();
    const a = h("a", {
      href: URL.createObjectURL(blob),
      download: `novelmaestro-portable-${version || "?"}.zip`,
    });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);
    toast("Архив новой версии скачан");
  } catch (ex) {
    toast(ex.message, "err");
  }
}

/* что делает обновление этой установки; у Docker — только команда хоста.
   Почему команда нужна, говорит строка о перезапуске (u.restart.note
   из core/update.py::RESTART_NOTES): второй подписи здесь быть не может */
function updateHowBox(u) {
  if (u.mode === "command") {
    const box = h("div", { class: "update-how" }, "Команда на хосте:");
    // строку команды отдаёт сервер (core/update.py::HOST_COMMAND): своей копии
    // в SPA нет — разойдётся с тем, что реально работает
    if (u.command) box.append(h("code", { class: "update-cmd" }, u.command));
    return box;
  }
  return h("div", { class: "update-how" }, u.mode === "git"
    ? "Код перемотается на релиз из репозитория: книги, настройки и свои наборы шаблонов останутся как есть."
    : "Файлы программы заменятся из архива релиза: папки projects и logs, файл .env и свои наборы шаблонов не изменятся.");
}

/* Пункт меню «⋮»: способ установки, результат проверки, кнопки и перезапуск.
   Вызывается при каждой сборке layout — рисует по state.update. */
function updateMenuItem() {
  const u = state.update || {};
  const rel = u.release || {};
  const job = u.job || {};
  const box = h("div", { class: "update-box" });
  // версия уже стоит в футере этого же меню (.user-menu-version) — не дублируем
  box.append(h("div", { class: "update-title" }, "Обновления"));
  if (job.state === "running" || job.state === "restarting") {
    box.append(h("div", { class: "update-state update-new" },
      job.state === "running" ? `Обновляется: ${u.mode_label || ""}`
        : `Код обновлён до v${job.to || "?"} — сервер перезапускается…`));
    (job.log || []).slice(-6).forEach((line) =>
      box.append(h("div", { class: "update-log" }, line)));
    if (job.state === "restarting")
      box.append(h("div", { class: "update-how" },
        "Страница обновится сама, как только сервер ответит."));
    return box;
  }
  if (!u.checked) {
    box.append(h("div", { class: "update-state" },
      u.label ? `${u.label}. Обновление не проверялось.`
              : "Обновление не проверялось."));
    const failed = updateErrorBox();
    if (failed) box.append(failed);
    box.append(updateCheckBtn("Проверить обновления"));
    return box;
  }
  /* пояснения идут ДО кнопок: способ обновления и перезапуска — это объяснение
     того, что сделает кнопка, а не итог; у завершённого обновления свой итог */
  const finished = job.state === "done" || job.state === "error";
  const restart = updateRestartBox(u);
  if (finished) {
    box.append(h("div", { class: "update-state update-new" },
      job.state === "done" ? `Программа обновлена до v${job.to || "?"}`
                           : "Обновление не применено"));
    if (job.note) box.append(h("div", { class: "update-log" }, job.note));
    if (restart) box.append(restart);
  } else if (u.available) {
    const date = updateDate(rel.published);
    box.append(h("div", { class: "update-state update-new" },
      `Доступна v${rel.version}${date ? ` от ${date}` : ""}`));
    const notes = updateNotes(rel.notes);
    if (notes) box.append(notes);
    box.append(updateHowBox(u));
    if (restart) box.append(restart);
    // Docker: кнопки обновления не предлагаются — образ меняет хост
    if (u.mode !== "command") {
      box.append(h("button", {
        class: "btn btn-sm btn-primary update-btn",
        onclick: () => updateApply(),
      }, `Обновить до v${rel.version}`));
      box.append(h("button", {
        class: "btn btn-sm btn-ghost update-btn",
        onclick: () => updateDownload(rel.version),
      }, "Скачать архив"));
    }
  } else {
    box.append(h("div", { class: "update-state" }, "У вас последняя версия"));
  }
  const links = [];
  if (rel.url) links.push(updateLink(rel.url, `Релиз v${rel.version}`));
  if (rel.changes) links.push(updateLink(rel.changes, "Список изменений"));
  if (links.length) box.append(h("div", { class: "update-links" }, ...links));
  const failed = updateErrorBox();
  if (failed) box.append(failed);
  box.append(updateCheckBtn("Проверить снова"));
  return box;
}

function layout(content) {
  const route = parseRoute();
  // проект — часть раздела «Проекты»: подсветить соответствующий пункт
  const activeView = route.view === "project" || route.view === "run"
    ? "hub"
    : route.view;
  const nav = ([view, label, ic]) =>
    h(
      "a",
      {
        class: "nav-item" + (activeView === view ? " nav-item-active" : ""),
        href: `#/${view}`,
        // повторный клик по той же вкладке — полный рендер
        // (сброс внутреннего состояния: у «Шаблонов» — выход из набора)
        onclick: (ev) => {
          if (route.view === view) {
            ev.preventDefault();
            render();
          }
        },
      },
      iconEl(ic),
      h("span", { class: "nav-label" }, label),
    );
  const userMenu = menuBtn(
    [
      { el: updateMenuItem() },
      { sep: true },
      { label: "О программе", href: "#/help" },
      {
        label: "Выйти",
        danger: true,
        onclick: () => logout(),
      },
    ],
    "Меню пользователя",
  );
  // точка-бейдж новой версии ставится сразу при сборке кнопки: отдельный
  // рендер (например, приход данных проекта) иначе стирал бы её
  if (state.update && state.update.available)
    userMenu.querySelector(".kebab-btn").classList.add("has-update");
  // версия сборки — из /api/session (едет с CHANGELOG.md при релизе);
  // жила в футере сайдбара — после его сноса переехала в меню «⋮»
  const ver = h(
    "div",
    { class: "user-menu-version", title: "Версия NovelMaestro" },
    "v" + (state.version || "?"),
  );
  userMenu.querySelector(".user-menu").prepend(ver);
  const topRight = h(
    "div",
    { class: "topbar-right" },
    themeSwitch(),
    // место индикатора активного запуска (заполняет app-run-slot)
    h("div", { class: "run-slot", id: "app-run-slot" }),
    userMenu,
  );
  const header = h(
    "header",
    { class: "topbar" },
    h("a", { class: "brand", href: "#/dashboard" }, "NovelMaestro"),
    h(
      "nav",
      { class: "topnav", "aria-label": "Основная навигация" },
      NAV_ITEMS.map(nav),
    ),
    topRight,
  );
  return h("div", { class: "layout" }, header, h("main", { class: "content" }, content));
}

/* ── индикатор активных запусков в шапке ───────────────────
   Лёгкий опрос GET /api/jobs/active (без истории) каждые 10 с и
   после каждого рендера: pill «⟳ Метка N/M» — клик ведёт на вкладку
   «Запуски» проекта. Активный запуск виден с любого экрана. */
let runPollTimer = null;
let runSeen = []; // id активных запусков прошлого опроса — по ним ловим финал

/* Фавикон-счётчик: с любой вкладки видно, что что-то считается. */
function setRunBadge(count) {
  const link = document.querySelector('link[rel="icon"]');
  if (link) link.href = UICore.faviconHref(count);
}

/* Уведомление браузера — только про финал и только когда вкладка скрыта
   (иначе пользователь видитpill и лог). Разрешение спрашиваем на запуске. */
function notifyFinished(ids) {
  if (!ids.length || !("Notification" in window)) return;
  if (!document.hidden || Notification.permission !== "granted") return;
  for (const id of ids) {
    api(`/jobs/${id}`)
      .then((d) => {
        const j = d.job || {};
        const bad = j.status === "error" || j.status === "failed";
        new Notification(`NovelMaestro: ${bad ? "ошибка запуска" : "готово"}`, {
          body: j.title || j.project || String(id),
          tag: `job-${id}`,
        });
      })
      .catch(() => {});
  }
}

function updateRunPill() {
  if (!state.auth) return;
  api("/jobs/active")
    .then((d) => {
      /* слот ищем в момент прихода ответа, а не старта запроса: вызов из
         render() уходит раньше, чем layout с шапкой попадёт в DOM */
      const slot = document.getElementById("app-run-slot");
      if (!slot) return;
      const jobs = (d.jobs || []).filter((j) => j.status === "running");
      const now = jobs.map((j) => String(j.id));
      const finished = runSeen.filter((id) => !now.includes(id));
      runSeen = now;
      setRunBadge(jobs.length);
      notifyFinished(finished);
      slot.replaceChildren();
      if (!jobs.length) return;
      const j = jobs[0];
      /* части считаются тем же progressParts, что и панелям «Запусков»: в
         шапке pill компактный (название, счётчик, трек), поэтому остаток и
         время конца уходят в тултип — на 220 px строки они не влезают */
      const parts = UICore.progressParts(j, j.progress);
      const label = j.title || j.action || "запуск";
      const more = jobs.length > 1 ? ` · ещё ${jobs.length - 1}` : "";
      const pill = h(
        "a",
        {
          class: "run-pill",
          href: `#/project/${j.project}/run`,
          title: `Активных запусков: ${jobs.length} — открыть лог`
            + (parts.eta ? ` · ${parts.eta}` : ""),
          /* клик — через навигацию SPA: вкладки проекта меняются состоянием,
             хэш остаётся тем, чем в проект вошли, и ссылка «на ту же вкладку»
             не меняла бы ничего (клик проходил бы мимо) */
          onclick: (ev) => {
            ev.preventDefault();
            projectNavigate(j.project, "run");
          },
        },
        iconEl("activity"),
        h("span", { class: "run-pill-label" }, label + more),
        parts.count
          ? h("span", { class: "run-pill-count" }, parts.count)
          : null,
        h(
          "span",
          { class: "run-pill-track" },
          h("span", {
            class: "run-pill-fill",
            style: `width:${parts.pct}%`,
          }),
        ),
      );
      slot.append(pill);
    })
    .catch(() => {
      /* нет связи — pill просто не показывается */
    });
}

function ensureRunPolling() {
  if (runPollTimer) return;
  runPollTimer = setInterval(updateRunPill, 10000);
}

/* ── роутер и рендер ─────────────────────────────────────── */
function parseRoute() {
  return UICore.parseRoute(location.hash);
}

async function logout() {
  try {
    await api("/logout", { method: "POST" });
  } catch {
    /* пусто */
  }
  state.auth = false;
  hubCache = null;
  render();
}

function render() {
  const root = document.querySelector("#app");
  // старый view получает сигнал до очистки — гасит SSE-стрим
  // (run-views.js слушает pi-navigate на своём page, once); page лежит
  // глубоко (layout > body > main.content), поэтому ищем по классу
  const oldPage = root.querySelector(".page");
  if (oldPage) {
    oldPage.dispatchEvent(new CustomEvent("pi-navigate", { bubbles: true }));
  }
  root.replaceChildren();
  // реестр живых редакторов собирается заново вместе с новым view
  LIVE_EDITORS.length = 0;
  // уход со страницы: кадры скрытых под-вкладок с отложенным подгоном
  // выбрасываются вместе со старым DOM — снимаем наблюдение (утечка IO)
  document.querySelectorAll("iframe[data-fit-pending]").forEach((f) => {
    if (f._fitIO) f._fitIO.disconnect();
    f._fitIO = null;
    delete f.dataset.fitPending;
  });
  if (!state.auth) {
    if (runPollTimer) {
      clearInterval(runPollTimer);
      runPollTimer = null;
    }
    root.append(viewLogin());
    return;
  }
  ensureRunPolling(); // после логина — pill запусков в шапке живёт
  updateRunPill();
  const route = parseRoute();
  const gen = ++renderGen; // поколение этого рендера
  if (route.view === "dashboard") {
    ensureDashTimer(); // вход на дашборд — таймер жив
  } else if (dashTimer) {
    clearInterval(dashTimer);
    dashTimer = null;
  }
  const views = {
    dashboard: viewDashboard,
    hub: viewHub,
    settings: viewSettings,
    templates: viewTemplates,
    notes: viewNotes,
    help: viewHelp,
    project: () => viewProject(route.rest[0], route.rest[1], route.rest[2]),
    // «Запуски» — вкладка проекта; короткий #/run/… (закладки) —
    // тот же вид внутри проекта
    run: () =>
      viewProject(
        route.rest[0],
        route.rest[1],
        "run",
        route.rest[2],
      ),
  };
  const fn = views[route.view] || (() => viewUnknown(route.view));
  Promise.resolve(fn()).then((node) => {
    if (gen !== renderGen) return; // устаревший рендер — мимо
    root.replaceChildren();
    root.append(layout(node));
    // шапка в DOM — pill запусков можно рисовать не дожидаясь тика опроса
    updateRunPill();
  });
}

/* ── Шаблоны  ──────────────────────────────────── */
function nameModal(title, placeholder, onOk, initial = "") {
  const name = h("input", { class: "input", placeholder });
  if (initial) name.value = initial;
  const err = h("div", { class: "form-error" });
  // защита от двойного клика: второй POST тем же именем = дубль раздела
  // (параллельные запросы проходили проверку «уже существует» вместе)
  const okBtn = h(
    "button",
    {
      class: "btn btn-primary",
      onclick: async () => {
        err.textContent = "";
        if (okBtn.disabled) return;
        okBtn.disabled = true;
        try {
          await onOk(name.value.trim());
          close();
        } catch (ex) {
          err.textContent = ex.message;
          okBtn.disabled = false;
        }
      },
    },
    "ОК",
  );
  const modal = UIC.modal({
    title,
    build: (close) => [
      name,
      err,
      h(
        "div",
        { class: "modal-actions" },
        h("button", { class: "btn btn-ghost", onclick: close }, "Отмена"),
        okBtn,
      ),
    ],
  });
  name.focus();
  if (initial) name.select();
  // okBtn собран до модалки (он же общий для «ОК») — close ему отдаём
  // поднятым объявлением: hoisted function виден из его обработчика
  function close(result) {
    return modal.close(result);
  }
}

/* ── управление разделами  ────────────────────────── */
function sectionsModal() {
  const err = h("div", { class: "form-error" });
  const list = h("div", { class: "hub-sections-modal" });
  /* wide: в узкой карточке строка раздела с тремя подписями упирается в
     край, и имя/счётчик уезжают за окно — ни названия раздела, ни кнопок
     не видно. Действия строки — иконки с подсказкой (как в списках). */
  UIC.modal({
    title: "Управление разделами",
    wide: true,
    build: (close) => [
      h(
        "div",
        { class: "modal-text" },
        "Удалить можно только пустой раздел. Переименование в существующий — перенесёт в него проекты.",
      ),
      list,
      err,
      h(
        "div",
        { class: "modal-actions" },
        /* в этом окне нечего делать по Enter: оно закрывается, а не раскрывает
           модалку создания — кнопка действия помечена вручную */
        h("button", {
          class: "btn btn-ghost", "data-modal-default": "1", onclick: close,
        }, "Закрыть"),
        h(
          "button",
          {
            class: "btn btn-primary",
            onclick: () =>
              nameModal("Новый раздел", "имя раздела", async (nm) => {
                await api("/sections", { method: "POST", body: { name: nm } });
                toast(`Создан раздел: ${nm}`);
                hubCache = null;
                refresh();
                render(); // хаб за модалкой сразу видит новый раздел
              }),
          },
          "＋ Раздел",
        ),
      ),
    ],
  });

  function rowIcon(icon, tip, onclick, cls) {
    const b = h(
      "button",
      {
        class: "btn btn-sm btn-ghost icon-btn" + (cls ? " " + cls : ""),
        title: tip,
        "aria-label": tip,
        onclick,
      },
      iconEl(icon),
    );
    attachTooltip(b, tip);
    return b;
  }

  function renderRow(s) {
    const actions = h("div", { class: "factions" });
    /* Раздел-архив: книгами здесь принято держать упакованные. Флаг
       включает пометку «архив» и вопрос об упаковке при переносе
       (pack_on_move — его следствие); молчаливой упаковки не бывает. */
    actions.append(
      rowIcon(
        "archive",
        s.archive
          ? "Раздел архивный: закрытые книги принято держать упакованными"
          : "Сделать раздел архивным",
        async () => {
          err.textContent = "";
          try {
            await api(`/sections/${encodeURIComponent(s.name)}/props`, {
              method: "POST",
              body: { archive: !s.archive },
            });
            toast(s.archive ? `Раздел ${s.name} больше не архивный` : `Раздел ${s.name} — архивный`);
            hubCache = null;
            refresh();
            render();
          } catch (ex) {
            err.textContent = ex.message;
          }
        },
        s.archive ? "btn-active" : "",
      ),
    );
    actions.append(
      rowIcon("rename", "Переименовать раздел", () =>
        nameModal(
          `Переименовать ${s.name}`,
          "новое имя (совпадает с существующим — проекты перенесутся)",
          async (nm) => {
            await api("/sections/rename", {
              method: "POST",
              body: { src: s.name, dst: nm },
            });
            toast(`Раздел переименован: ${s.name} → ${nm}`);
            hubCache = null;
            refresh();
            render(); // хаб за модалкой сразу видит изменения
          },
          s.name,
        )),
    );
    actions.append(
      rowIcon(
        "trash",
        "Удалить раздел (только пустой)",
        () =>
          confirmModal(
            "Удалить раздел",
            `Раздел ${s.name} будет удалён`,
            "УДАЛИТЬ",
            async () => {
              err.textContent = "";
              try {
                await api(`/sections/${encodeURIComponent(s.name)}`, {
                  method: "DELETE",
                });
                toast(`Удалён раздел: ${s.name}`);
                hubCache = null;
                refresh();
                render(); // хаб за модалкой сразу видит изменения
              } catch (ex) {
                err.textContent = ex.message;
              }
            },
          ),
        "btn-danger-ghost",
      ),
    );
    return h(
      "div",
      { class: "frow" },
      h("span", { class: "fname" }, s.name),
      h("span", { class: "fmeta" }, `проектов: ${s.count}`),
      actions,
    );
  }

  async function refresh() {
    err.textContent = "";
    try {
      const d = await api("/sections");
      list.replaceChildren(...(d.sections || []).map(renderRow));
    } catch (ex) {
      err.textContent = ex.message;
    }
  }

  refresh();
}

async function viewTemplates() {
  const st = { set: null, path: "", edit: null };
  const page = h("div", { class: "page" });

  function openSet(name) {
    st.set = name;
    st.path = "";
    st.edit = null;
    render();
  }
  function setPath(p) {
    st.path = p || "";
    st.edit = null;
    render();
  }
  function openEdit(full) {
    st.edit = full;
    render();
  }

  async function render() {
    page.replaceChildren();
    const header = h(
      "div",
      { class: "page-header" },
      h(
        "div",
        { class: "page-header-main" },
        h("h1", { class: "page-title" }, "Шаблоны"),
        h(
          "div",
          { class: "page-sub" },
          "наборы промптов и исходников для новых проектов",
        ),
      ),
    );
    let body;
    if (st.edit) body = await tplEditor();
    else if (st.set) body = await tplSetFiles();
    else body = await tplList();
    page.append(header, body);
  }

  async function tplList() {
    let data;
    try {
      data = await api("/templates");
    } catch (ex) {
      return h("div", { class: "files-empty" }, ex.message);
    }
    const sets = data.templates || [];
    const cards = sets.map((t) => {
      const btns = [
        h(
          "button",
          { class: "btn btn-sm btn-primary", onclick: () => openSet(t.name) },
          "Открыть",
        ),
        h(
          "button",
          {
            class: "btn btn-sm btn-ghost",
            onclick: () =>
              nameModal(
                `Копировать набор ${t.name}`,
                "имя нового набора",
                async (nm) => {
                  await api(`/templates/${encodeURIComponent(t.name)}/copy`, {
                    method: "POST",
                    body: { dst: nm },
                  });
                  toast(`Скопирован: ${nm}`);
                  render();
                },
              ),
          },
          "Копировать",
        ),
      ];
      if (t.name !== "General") {
        btns.push(
          h(
            "button",
            {
              class: "btn btn-sm btn-danger-ghost",
              onclick: () =>
                confirmModal(
                  "Удаление набора",
                  `Будут удалены все файлы набора ${t.name}`,
                  "УДАЛИТЬ",
                  async () => {
                    await api(`/templates/${encodeURIComponent(t.name)}`, {
                      method: "DELETE",
                    });
                    toast(`Удалён набор: ${t.name}`);
                    render();
                  },
                ),
            },
            "Удалить",
          ),
        );
      }
      return h(
        "div",
        { class: "card tpl-card" },
        h("div", { class: "card-title" }, t.name),
        h(
          "div",
          { class: "card-sub" },
          `файлов: ${(t.files || []).length} · ${
            t.name === "General" ? "системный (только чтение)" : ""
          }`,
        ),
        h("div", { class: "card-actions" }, btns),
      );
    });
    const createBtn = h(
      "button",
      {
        class: "btn btn-primary",
        onclick: () =>
          nameModal("Новый набор шаблонов", "имя набора", async (nm) => {
            await api("/templates", { method: "POST", body: { name: nm } });
            toast(`Создан набор: ${nm}`);
            render();
          }),
      },
      "＋ Создать набор",
    );
    return h(
      "div",
      { class: "files-wrap" },
      h("div", { class: "files-toolbar" }, createBtn),
      cards.length
        ? h("div", { class: "tpl-grid" }, cards)
        : h("div", { class: "files-empty" }, "Наборов нет — создайте первый"),
    );
  }

  async function tplSetFiles() {
    let data;
    try {
      data = await api("/templates");
    } catch (ex) {
      return h("div", { class: "files-empty" }, ex.message);
    }
    const t = (data.templates || []).find((x) => x.name === st.set);
    if (!t) {
      return h("div", { class: "files-empty" }, `Набор не найден: ${st.set}`);
    }
    const files = t.files || [];
    const entries = UICore.dirEntries(files, st.path);
    const crumbs = h("div", { class: "crumbs" });
    // первый крош — СТОРОНА наборов (сброс st.set),
    // второй — корень набора; раньше «Шаблоны · X» оставался внутри
    crumbs.append(
      crumb("Шаблоны", () => {
        st.set = null;
        st.path = "";
        st.edit = null;
        render();
      }),
    );
    crumbs.append(h("span", { class: "crumb-sep" }, " / "));
    crumbs.append(crumb(st.set, () => setPath("")));
    const parts = st.path ? st.path.split("/") : [];
    const walk = [];
    for (const p of parts) {
      walk.push(p);
      const target = walk.join("/");
      crumbs.append(h("span", { class: "crumb-sep" }, " / "));
      crumbs.append(crumb(p, () => setPath(target)));
    }
    const upInput = h("input", {
      type: "file",
      multiple: true,
      class: "hidden",
    });
    const writable = st.set !== "General";
    upInput.addEventListener("change", async () => {
      const form = new FormData();
      if (st.path) form.append("dest", st.path);
      for (const f of upInput.files) form.append("files[]", f, f.name);
      try {
        const r = await apiUpload(
          `/templates/${encodeURIComponent(st.set)}/upload`,
          form,
        );
        toast(`Загружено: ${r.saved.length} файл(ов)`);
        render();
      } catch (ex) {
        toast(ex.message, "err");
      }
    });
    const downloadUrl = (full) =>
      `/api/templates/${encodeURIComponent(st.set)}/download?path=` +
      encodeURIComponent(full);
    /* иконка-действие — как во вкладке «Файлы» (iconBtn project-views):
       подпись живёт в тултипе и aria-label, строка остаётся узкой */
    const rowIcon = (icon, tip, onclick, danger) => {
      const b = h(
        "button",
        {
          class:
            "btn btn-sm btn-ghost icon-btn" +
            (danger ? " btn-danger-ghost" : ""),
          title: tip,
          "aria-label": tip,
          onclick,
        },
        iconEl(icon),
      );
      attachTooltip(b, tip);
      return b;
    };
    const rowDownload = (full, name) => {
      const tip = `Скачать ${name}`;
      const a = h(
        "a",
        {
          class: "btn btn-sm btn-ghost icon-btn",
          href: downloadUrl(full),
          download: name,
          title: tip,
          "aria-label": tip,
        },
        iconEl("download"),
      );
      attachTooltip(a, tip);
      return a;
    };
    const row = (e) => {
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
            iconEl("folder", "fname-icon"),
            e.name,
          )
        : h("span", { class: "fname" }, iconEl("file", "fname-icon"), e.name);
      const actions = h("div", { class: "factions" });
      /* у каталогов шаблонов НИКАКИХ действий — только переход */
      if (!e.dir) {
        /* порядок — как во вкладке «Файлы»: правка → скачать →
           переименовать → удалить; General — только просмотр и скачать */
        if (writable) {
          actions.append(
            rowIcon("pencil", `Править ${e.name}`, () => openEdit(full)),
            rowDownload(full, e.name),
            rowIcon("rename", `Переименовать ${e.name}`, () =>
              nameModal(
                `Переименовать файл ${e.name}`,
                "новое имя файла",
                async (nm) => {
                  const dir = st.path ? st.path + "/" : "";
                  await api(
                    `/templates/${encodeURIComponent(st.set)}/rename`,
                    {
                      method: "POST",
                      body: { src: full, dst: dir + nm },
                    },
                  );
                  toast(`Переименовано: ${e.name} → ${nm}`);
                  render();
                },
                e.name,
              ),
            ),
            rowIcon(
              "trash",
              `Удалить ${e.name}`,
              () =>
                confirmModal("Удаление файла", full, "УДАЛИТЬ", async () => {
                  const q = new URLSearchParams({ path: full });
                  await api(
                    `/templates/${encodeURIComponent(st.set)}/file?${q}`,
                    { method: "DELETE" },
                  );
                  toast(`Удалено: ${full}`);
                  render();
                }),
              true,
            ),
          );
        } else {
          actions.append(
            rowIcon("eye", `Просмотр ${e.name}`, () => openEdit(full)),
            rowDownload(full, e.name),
          );
        }
      }
      return h("div", { class: "frow" }, nameNode, actions);
    };
    const drop = h("div", { class: "files-list" });
    const pager = UIC.listPager({
      list: drop,
      rows: (slice) => slice.map(row),
      info: (total, page, pages) => ` ${page} / ${pages} · записей: ${total} `,
    });
    pager.items = entries;
    if (writable) {
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
        const form = new FormData();
        if (st.path) form.append("dest", st.path);
        for (const f of e.dataTransfer.files) form.append("files[]", f, f.name);
        try {
          const r = await apiUpload(
            `/templates/${encodeURIComponent(st.set)}/upload`,
            form,
          );
          toast(`Загружено: ${r.saved.length} файл(ов)`);
          render();
        } catch (ex) {
          toast(ex.message, "err");
        }
      });
    }
    const addBtn = h(
      "button",
      {
        class: "btn btn-sm",
        onclick: () =>
          nameModal(
            "Новый файл",
            "путь внутри набора, напр. prompts/x.txt",
            async (rel) => {
              await api(`/templates/${encodeURIComponent(st.set)}/file`, {
                method: "PUT",
                body: { path: rel, content: "" },
              });
              toast(`Создан: ${rel}`);
              openEdit(rel);
            },
          ),
      },
      "＋ Файл",
    );
    /* «＋ Каталог» убран — каталоги в шаблонах неизменяемы */
    const toolbar = h(
      "div",
      { class: "files-toolbar" },
      crumbs,
      h("span", { class: "spacer" }),
      ...(writable
        ? [
            h(
              "button",
              { class: "btn btn-sm", onclick: () => upInput.click() },
              "Загрузить",
            ),
            addBtn,
          ]
        : []),
    );
    return h(
      "div",
      { class: "files-wrap" },
      toolbar,
      upInput,
      entries.length
        ? h("div", {}, drop, pager.el)
        : h("div", { class: "files-empty" }, "Папка пуста"),
    );
  }

  async function tplEditor() {
    const full = st.edit;
    let data;
    try {
      const q = new URLSearchParams({ path: full });
      data = await api(`/templates/${encodeURIComponent(st.set)}/file?${q}`);
    } catch (ex) {
      return h("div", { class: "files-empty" }, ex.message);
    }
    const ext = extOf(full);
    const readonly = st.set === "General"; // системный набор — просмотр
    /* язык редактора — по назначению файла: промпты набора читаются как
       промпты (html), а не как пустяковый txt */
    const ed = makeEditor(data.content || "", UICore.editorLang(full));
    ed.setReadOnly(readonly);
    const err = h("div", { class: "form-error" });

    /* превью — общий каркас панели (sandbox-iframe); режим берётся из
       расширения: html как есть, md через marked, остальное — обычный текст */
    const pane = UIC.previewPane(ed, {
      renderMode: UICore.previewMode(ext),
    });

    const saveBtn = h("button", { class: "btn btn-sm" }, "Сохранить");
    saveBtn.addEventListener("click", async () => {
      err.textContent = "";
      try {
        await api(`/templates/${encodeURIComponent(st.set)}/file`, {
          method: "PUT",
          body: { path: full, content: ed.getValue() },
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
        "← К файлам",
      ),
      h(
        "span",
        { class: "editor-meta" },
        `${full} · ${fmtSize(data.size || 0)}`
        + (readonly ? " · только чтение" : ""),
      ),
      h("span", { class: "spacer" }),
      h(
        "span",
        { class: "toolbar-group" },
        h("span", { class: "field-help" }, "кегль"),
        previewFontSelect(() => {
          if (pane.mode !== "code") pane.render();
        }),
      ),
      h(
        "span",
        { class: "toolbar-actions" },
        UIC.editorSearch(ed),
        pane.btn,
        ...(readonly ? [] : [saveBtn]),
      ),
    );
    return h(
      "div",
      { class: "editor-wrap editor-has-preview" },
      toolbar,
      err,
      pane.host,
      pane.frame,
    );
  }

  render();
  return page;
}

/* Санитайзер справки: убирает скрипты, фреймы и инлайновые
   обработчики из отрендеренного markdown (контент статический,
   но защита дешёвая). Обходит subtree, мутирует на месте. */
function _helpSanitize(root) {
  const walker = (node) => {
    const kids = Array.from(node.childNodes);
    for (const el of kids) {
      if (el.nodeType !== Node.ELEMENT_NODE) continue;
      const tag = el.tagName.toLowerCase();
      if (["script", "style", "iframe", "object", "embed"].includes(tag)) {
        el.remove();
        continue;
      }
      for (const attr of Array.from(el.attributes)) {
        const name = attr.name.toLowerCase();
        const val = attr.value.trim().toLowerCase();
        if (name.startsWith("on") || val.startsWith("javascript:")) {
          el.removeAttribute(attr.name);
        }
      }
      walker(el);
    }
  };
  walker(root);
}

/* ── Справка (help.md → markdown-рендер + TOC) ─────────── */
async function viewHelp() {
  const err = h("div", { class: "form-error" });
  const content = h("div", { class: "md-body" });
  const toc = h("nav", { class: "help-toc" });
  const body = h("div", { class: "help-body" }, content);

  try {
    const res = await fetch("/help.md", {
      headers: { "X-Requested-With": "fetch" },
    });
    if (!res.ok) throw new Error(`Ошибка ${res.status}`);
    const md = await res.text();
    const html = window.marked
      ? window.marked.parse(md, { mangle: false, headerIds: false })
      : "<p>marked не загружен</p>";
    /* статическая справка доверенная, но парсим через
       createContextualFragment + санитайзер (без innerHTML): скрипты,
       инлайновые обработчики и javascript: ссылки не попадают в DOM */
    const frag = document
      .createRange()
      .createContextualFragment(html);
    _helpSanitize(frag);
    content.append(frag);
    /* h1 «Справка» дублирует заголовок страницы — убираем */
    const h1 = content.querySelector("h1");
    if (h1) h1.remove();
    /* TOC по h2/h3: свои id (marked их не проставляет), клик —
       плавный скролл без смены hash (роутер бы ушёл на #help-N) */
    const heads = content.querySelectorAll("h2, h3");
    heads.forEach((hd, i) => {
      const id = "help-s" + i;
      hd.id = id;
      const isH2 = hd.tagName === "H2";
      const link = h(
        "a",
        {
          class: "help-toc-link" + (isH2 ? " help-toc-h2" : " help-toc-h3"),
          href: "#" + id,
        },
        hd.textContent,
      );
      link.addEventListener("click", (e) => {
        e.preventDefault();
        hd.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      toc.append(link);
    });
    if (!heads.length) toc.append(h("div", { class: "muted" }, "—"));
  } catch (ex) {
    err.textContent = ex.message || "Не удалось загрузить справку";
    content.append(
      h(
        "p",
        { class: "muted" },
        "Справка хранится в web/static/help.md — проверьте, что файл на месте.",
      ),
    );
  }

  return h(
    "div",
    { class: "page" },
    h(
      "div",
      { class: "page-header" },
      h(
        "div",
        { class: "page-header-main" },
        h("h1", { class: "page-title" }, "Справка"),
        h(
          "div",
          { class: "page-sub" },
          "Инструкции по программе, проектам, стадиям и регулярным выражениям",
        ),
      ),
    ),
    err,
    h("div", { class: "help-layout" }, toc, body),
  );
}

/* ── старт ───────────────────────────────────────────────── */
/* ── command palette (Ctrl+K): навигация без перекомпоновки ──
   Экраны, вкладки проекта и проекты всех разделов. Фильтр —
   подстрока (регистронезависимо), Enter — переход, стрелки/Escape. */
function commandPalette() {
  if (document.querySelector(".palette-backdrop")) return;
  const route = parseRoute();
  const isProject = route.view === "project" || route.view === "run";
  const items = [];
  for (const [view, label, ic] of NAV_ITEMS) {
    items.push({ label, hint: "раздел", ic, href: `#/${view}` });
  }
  if (isProject) {
    const proj = `#/project/${route.rest[0]}/${route.rest[1]}`;
    for (const [key, label] of PROJECT_TABS) {
      items.push({
        label,
        hint: `${route.rest[0]}/${route.rest[1]}`,
        ic: "file-text",
        href: key === "run" ? `${proj}/run` : `${proj}/${key}`,
      });
    }
  }
  (async () => {
    try {
      const hub = await loadHub();
      for (const sec of hub.sections || []) {
        for (const p of hub.bySection[sec.name] || []) {
          // bySection — массив ИМЁН (GET /api/projects?section= → projects)
          items.push({
            label: String(p),
            hint: `проект · ${sec.name}`,
            ic: "folder",
            href: `#/project/${sec.name}/${p}`,
          });
        }
      }
      // проекты пришли после открытия — перевыборка текущего запроса
      if (backdrop.isConnected) match();
    } catch {
      /* проекты недоступны — палитра остаётся с разделами */
    }
  })();
  const input = h("input", {
    class: "input palette-input",
    placeholder: "Переход: экран, вкладка или проект…",
    "aria-label": "Поиск навигации",
  });
  const list = h("div", {
    class: "palette-list",
    role: "listbox",
    "aria-label": "Результаты",
  });
  let sel = 0;
  let shown = [];
  function match() {
    const qq = input.value.trim().toLowerCase();
    shown = items.filter(
      (it) =>
        !qq ||
        it.label.toLowerCase().includes(qq) ||
        it.hint.toLowerCase().includes(qq),
    );
    sel = 0;
    renderList();
  }
  function renderList() {
    list.replaceChildren(
      ...shown.map((it, i) =>
        h(
          "div",
          {
            class: "palette-item" + (i === sel ? " palette-item-active" : ""),
            role: "option",
            "aria-selected": i === sel ? "true" : "false",
            onclick: () => go(i),
            onmouseenter: () => {
              sel = i;
              paint();
            },
          },
          iconEl(it.ic),
          h("span", { class: "palette-item-label" }, it.label),
          h("span", { class: "palette-item-hint" }, it.hint),
        ),
      ),
    );
  }
  function paint() {
    [...list.children].forEach((el, i) => {
      el.classList.toggle("palette-item-active", i === sel);
      el.setAttribute("aria-selected", i === sel ? "true" : "false");
    });
  }
  function go(i) {
    const it = shown[i];
    close();
    if (it) location.hash = it.href;
  }
  function close() {
    backdrop.remove();
    document.removeEventListener("keydown", onKey, true);
  }
  function onKey(e) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      sel = Math.min(shown.length - 1, sel + 1);
      paint();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      sel = Math.max(0, sel - 1);
      paint();
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(sel);
    }
  }
  input.addEventListener("input", match);
  const backdrop = h(
    "div",
    {
      class: "modal-backdrop palette-backdrop",
      onclick: (e) => {
        if (e.target === backdrop) close();
      },
    },
    h(
      "div",
      { class: "palette", role: "dialog", "aria-label": "Навигация" },
      input,
      list,
      h(
        "div",
        { class: "palette-hint" },
        "↑↓ — выбор · Enter — перейти · Esc — закрыть",
      ),
    ),
  );
  document.body.append(backdrop);
  document.addEventListener("keydown", onKey, true);
  match();
  input.focus();
}

async function boot() {
  // Ctrl+K — command palette (переход по экранам/вкладкам/проектам);
  // «л» — та же физическая клавиша в русской раскладке
  window.addEventListener("keydown", (e) => {
    const k = (e.key || "").toLowerCase();
    if ((e.ctrlKey || e.metaKey) && (k === "k" || k === "л")) {
      e.preventDefault();
      if (state.auth) commandPalette();
    }
  });
  // Esc закрывает верхнюю модалку
  window.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const modals = document.querySelectorAll(".modal-backdrop");
    const top = modals[modals.length - 1];
    if (top) {
      e.preventDefault();
      top.remove();
    }
  });
  try {
    const s = await api("/session");
    state.auth = !!s.authenticated;
    state.host = s.host || "";
    state.tokenSet = !!s.token_set;
    state.version = s.version || "";
    state.update = s.update || null;
  } catch {
    state.auth = false;
  }
  applyEditorSettings();
  window.addEventListener("hashchange", render);
  render();
  if (state.auth) {
    ensureUpdatePolling();
    if (state.update && state.update.available) updateBadgeShow(true);
    // обновление могло начаться в другой вкладке (или сервер как раз
    // перезапускается): его ход досчитываем отсюда
    if (state.update && state.update.job
        && (state.update.job.state === "running"
            || state.update.job.state === "restarting")) updateJobPoll();
  }
}

boot();

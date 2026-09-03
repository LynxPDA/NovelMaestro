/* app.js — SPA web-интерфейса NovelMaestro (ES-module, vanilla JS, без сборки)
 * M1: каркас, роутер, вход по токену.
 * M2: hub — разделы, карточки проектов, мастер создания, управление.
 * M3: файлы — браузер, редактор, загрузка, скачивание. */

const state = { auth: false, host: "", tokenSet: false };

/* ── утилиты ─────────────────────────────────────────────── */
function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue; // false — атрибут не ставим
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2), v);
    } else if (k === "value") {
      // textarea: setAttribute("value") НЕ заполняет содержимое —
      // значение только через свойство (баг глоссария, )
      node.value = v;
    } else node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(
      child instanceof Node ? child : document.createTextNode(String(child)),
    );
  }
  return node;
}

/* Предпросмотр markdown/html (Заметки и «Правка» файлов).
   Кегль — localStorage previewFontSize (не .env: UI-предпочтение).
   srcdoc-iframe не наследует тему страницы — стили задаём явно. */
const PREVIEW_FONT_KEY = "previewFontSizeV2"; // старый ключ
// previewFontSize (дефолт 7) игнорируем — новый дефолт 12px должен
// сработать у всех, а не только у тех, кто не менял кегль
const PREVIEW_FONT_DEFAULT = 12;
const PREVIEW_FONT_OPTIONS = [5, 7, 10, 12, 14];

function getPreviewFontSize() {
  const n = parseInt(localStorage.getItem(PREVIEW_FONT_KEY) || "", 10);
  return UICore.clampFont(n, PREVIEW_FONT_OPTIONS, PREVIEW_FONT_DEFAULT);
}

/* ── Внешний вид: UI-предпочтения в localStorage браузера (не .env —
   12-factor: клиентские настройки живут на клиенте). ── */
const UI_LOOK_KEY = "uiLookV1";
const EDITOR_SETTINGS = { ui: "dark", editor: "auto", fontSize: 13 };
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
    `pre{background:${panel};padding:8px;border-radius:6px;overflow:auto}` +
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

function fitPreviewFrame(frame) {
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

/* Тосты — обычный DOM (B8: Alpine и toast-host на нём убраны). */
function toast(msg, kind = "ok") {
  const t = h("div", { class: `toast toast-${kind}` }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), 3500);
}

/* ── тултипы (M9): единая всплывающая подсказка ────────────────
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
    const modal = h(
      "div",
      { class: "modal-backdrop", onclick: (e) => e.target === modal && close(false) },
      h(
        "div",
        { class: "modal" },
        h("div", { class: "modal-title" }, title),
        h("div", { class: "modal-text" }, text),
        word,
        err,
        h(
          "div",
          { class: "modal-actions" },
          h("button", { class: "btn btn-ghost", onclick: () => close(false) }, "Отмена"),
          h(
            "button",
            {
              class: "btn btn-danger",
              onclick: async () => {
                if (word.value.trim().toUpperCase() !== confirmWord) {
                  err.textContent = `Введите слово ${confirmWord}`;
                  return;
                }
                try {
                  await onConfirm();
                } catch (ex) {
                  err.textContent = ex.message;
                  return;
                }
                close(true);
              },
            },
            "Подтвердить",
          ),
        ),
      ),
    );
    document.body.append(modal);
    word.focus();
    function close(result) {
      modal.remove();
      resolve(result);
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

/* ── данные hub (M2) ──────────────────────────────────────── */
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
      return [s.name, r.projects];
    }),
  );
  for (const [name, projects] of lists) bySection[name] = projects;
  /* stats для карточек берём из кешируемого /dashboard одним запросом,
     а не дергаем /stats на каждый проект (R5-K follow-up). Список
     рендерится сразу, статистика догружается в фоне — удаление
     проекта не ждёт пересчёта stats всех проектов. */
  const cache = {
    sections: sections.sections,
    templates: templates.templates,
    bySection,
    statsMap: {},
  };
  hubCache = cache;
  api("/dashboard")
    .then((dash) => {
      if (hubCache !== cache) return; // кеш уже перезапрошен — мимо
      const statsMap = {};
      if (dash) {
        for (const s of dash.sections || []) {
          for (const p of s.projects || []) {
            statsMap[`${s.name}/${p.name}`] = p.stats;
          }
        }
      }
      cache.statsMap = statsMap;
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
    const modal = h(
      "div",
      {
        class: "modal-backdrop",
        onclick: (e) => e.target === modal && close(),
      },
      h(
        "div",
        { class: "modal modal-wide" },
        h("div", { class: "modal-title" }, "Создать проект"),
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
      ),
    );
    document.body.append(modal);
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
    function close(result = false) {
      modal.remove();
      resolve(result);
    }
  });
}

/* ── управление проектом ─────────────────────────────────── */
function manageProjectModal(section, name) {
  const err = h("div", { class: "form-error" });
  const info = h("div", { class: "modal-text" });
  const dstSel = h("select", { class: "input" });
  const newName = h("input", { class: "input", placeholder: "new_name" });

  return new Promise((resolve) => {
    projectStats(section, name)
      .then((r) => {
        info.textContent = r.stats;
      })
      .catch(() => {});
    function close(result = false) {
      modal.remove();
      resolve(result);
    }
    const modal = h(
      "div",
      {
        class: "modal-backdrop",
        onclick: (e) => e.target === modal && close(),
      },
      h(
        "div",
        { class: "modal" },
        h("div", { class: "modal-title" }, `Управление · ${section}/${name}`),
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
                      body: { section, name, dst: dstSel.value },
                    });
                    toast(`Перенесено → ${r.section}/${r.name}`);
                    close(true);
                  } catch (ex) {
                    err.textContent = ex.message;
                  }
                },
              },
              "Перенести",
            ),
          ),
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
      ),
    );
    document.body.append(modal);
    for (const s of hubCache.sections.filter((x) => x.name !== section)) {
      dstSel.append(h("option", { value: s.name }, s.name));
    }
    newName.value = name;
  });
}

/* Экспорт глоссария (модалка настроек + скачивание файла) — в project-views.js */

function sectionBlock(section, projects, sectionActive, statsMap) {
  const cards = projects.map((name) => {
    const stats = h("div", {
      class: "card-hint",
      text: (statsMap || {})[`${section.name}/${name}`] ?? "…",
    });
    return h(
      "div",
      { class: "card project-card" },
      h("div", { class: "card-title" }, name),
      h("div", { class: "card-sub" }, section.name),
      stats,
      h(
        "div",
        { class: "card-actions" },
        h(
          "a",
          { class: "btn btn-sm btn-ghost", href: `#/project/${section.name}/${name}` },
          "Открыть",
        ),
        h(
          "a",
          {
            class: "btn btn-sm btn-ghost",
            href: `#/run/${section.name}/${name}`,
          },
          "▶ Запуски",
        ),
        h(
          "button",
          {
            class: "btn btn-sm btn-ghost",
            onclick: async () => {
              if (await manageProjectModal(section.name, name)) {
                hubCache = null;
                render();
              }
            },
          },
          "Управление",
        ),
      ),
    );
  });
  const header = h(
    "div",
    { class: "section-header" },
    h(
      "h2",
      { class: "section-title" },
      section.name,
      h("span", { class: "badge" }, String(projects.length)),
    ),
  );
  if (sectionActive) {
    header.classList.add("section-active");
  }
  return h(
    "section",
    { class: "section" },
    header,
    h(
      "div",
      { class: "cards" },
      cards.length ? cards : [h("div", { class: "empty" }, "Нет проектов")],
    ),
  );
}

/* ── Настройки: системный .env (projects/.env, ) ── */
/* ── Заметки (markdown) ──────────────────────────────────── */
async function viewNotes() {
  const err = h("div", { class: "form-error" });
  /* редактор — тот же, что у файлов («Правка»): CodeMirror + тёмный
     markdown-предпросмотр в sandbox-iframe (скрипты не выполняются) */
  const ed = makeEditor("", "md");
  const editorHost = h("div", { class: "editor-cm editor-cm-small" }, ed.root);
  const frame = h("iframe", {
    class: "editor-preview-frame preview-adaptive notes-frame",
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

function viewSettings() {
  /* ── внешний вид: тема интерфейса, тема/кегль редакторов —
     UI-предпочтения в localStorage браузера (не .env) ── */
  const uiSel = h(
    "select",
    { class: "input input-inline" },
    h("option", { value: "dark" }, "Тёмная"),
    h("option", { value: "light" }, "Светлая"),
  );
  uiSel.value = EDITOR_SETTINGS.ui;
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
  const lookBtn = h("button", { class: "btn btn-sm" }, "Применить");
  lookBtn.addEventListener("click", () => {
    const n = Math.max(8, Math.min(32, parseInt(fontIn.value, 10) || 13));
    EDITOR_SETTINGS.ui = uiSel.value === "light" ? "light" : "dark";
    EDITOR_SETTINGS.editor =
      edSel.value === "dark" || edSel.value === "light"
        ? edSel.value
        : "auto";
    EDITOR_SETTINGS.fontSize = n;
    try {
      localStorage.setItem(
        UI_LOOK_KEY,
        JSON.stringify(EDITOR_SETTINGS),
      );
    } catch {
      /* localStorage недоступен — не критично */
    }
    applyEditorSettings();
    render();
    toast("Внешний вид сохранён");
  });
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
          h("div", { class: "field-label" }, "Тема интерфейса"),
          uiSel,
        ),
        h(
          "label",
          { class: "field" },
          h("div", { class: "field-label" }, "Тема редакторов"),
          edSel,
        ),
        h(
          "label",
          { class: "field" },
          h("div", { class: "field-label" }, "Кегль редакторов, px"),
          fontIn,
        ),
        h("span", { class: "spacer" }),
        lookBtn,
      ),
      h(
        "div",
        { class: "field-help" },
        "хранится в браузере (localStorage), не в .env — переживает " +
          "обновление и пересоздание контейнера",
      ),
    ),
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
        h("h1", { class: "page-title" }, "Настройки"),
        h(
          "div",
          { class: "page-sub" },
          "Внешний вид и интерфейс — хранятся в браузере (localStorage); " +
            "сервер и LLM-конфиг — в .env / переменных окружения",
        ),
      ),
    ),
    lookCard,
    h(
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
            "хранится в браузере (localStorage), не в .env — как тема и авто-обновление",
          ),
        ),
      ),
    ),
  );
}

/* ── Дашборд (W3) ─────────────────────────────────────────── */
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
  /* текущие активные запуски: статус + строки лога (разворачиваются) */
  const running = d.running_jobs || [];
  // мини-бар прогресса : label + трек + done/total.
  // у running-задачи без событий прогресса бар виден
  // («ожидание…») — раньше возвращался null и запуск выглядел мёртвым
  function miniBar(p, status) {
    if (!p && status !== "running") return null;
    const total = p && p.total ? p.total : 0;
    const done = p ? p.done : 0;
    const pct = total > 0 ? Math.min(100, Math.round((100 * done) / total)) : 0;
    return h(
      "div",
      { class: "progress-wrap mini" },
      h(
        "div",
        { class: "progress-track" },
        h("div", { class: "progress-fill", style: "width:" + pct + "%" }),
      ),
      h(
        "span",
        { class: "progress-text" },
        p
          ? (p.label || "") + (total > 0 ? ` ${p.done}/${p.total}` : "")
          : "ожидание…",
      ),
    );
  }
  const runRows = running.map((j) => {
    const logLines = j.lines || [];
    const logPre = h("pre", {
      class: "dash-log hidden",
      text: logLines.join("\n") || "(лог пуст)",
    });
    const row = h(
      "div",
      { class: "job-row dash-run" },
      h("span", { class: "badge badge-" + j.status }, j.status),
      h(
        "a",
        {
          class: "dash-run-title",
          // активные запуски — на страницу «Запуски» проекта
          // (авто-подхват активного запуска), история — там же
          href: `#/run/${j.project}`,
        },
        j.title || j.action || "запуск",
      ),
      h("span", { class: "job-time" }, j.project),
      h(
        "span",
        { class: "job-time" },
        `строк: ${logLines.length} · создан ${new Date((j.created || 0) * 1000).toLocaleTimeString()}`,
      ),
      h(
        "button",
        {
          class: "btn btn-sm btn-ghost",
          onclick: () => logPre.classList.toggle("hidden"),
        },
        "Лог",
      ),
      h(
        "a",
        {
          class: "btn btn-sm btn-primary",
          href: `#/run/${j.project}`,
        },
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
    );
    const mb = miniBar(j.progress, j.status);
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
        // клик по истории — в проект на вкладку Запуски,
        // а не в конкретный (возможно завершённый) запуск с логом
        onclick: () => {
          location.hash = `#/run/${j.project}`;
        },
      },
      h("td", {}, j.title || j.action),
      h("td", { class: "ner-type" }, j.project),
      h("td", {}, h("span", { class: "badge badge-" + j.status }, j.status)),
      h("td", {}, miniBar(j.progress, j.status) || "—"),
      h("td", {}, new Date((j.created || 0) * 1000).toLocaleString()),
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
                href: `#/run/${hub.section}/${hub.project}`,
              },
              "▶ Запуски",
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
      h(
        "button",
        {
          class: "btn btn-primary",
          onclick: async () => {
            if (await createProjectModal()) {
              hubCache = null;
              render();
            }
          },
        },
        "＋ Создать проект",
      ),
      h(
        "button",
        { class: "btn btn-sm btn-ghost", onclick: () => sectionsModal() },
        "⚙ Управление разделами",
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
        ),
      ),
    ),
  );
}

/* ── файловый менеджер (M3) ─────────────────────────────── */

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

/* ── Редактор файлов: CodeMirror c fallback на textarea (W8) ── */

const CM_READY =
  typeof window !== "undefined" && window.CM && window.CM.EditorView;

/* Расширение → язык CM; по умолчанию — простой текст */
const CM_LANG_BY_EXT = {
  md: "markdown",
  markdown: "markdown",
  html: "html",
  htm: "html",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  py: "python",
};

function extOf(path) {
  const base =
    String(path || "")
      .split("/")
      .pop() || "";
  const dot = base.lastIndexOf(".");
  return dot < 0 ? "" : base.slice(dot + 1).toLowerCase();
}

function cmLang(ext) {
  const kind = CM_LANG_BY_EXT[ext] || "";
  const langs = window.CM && window.CM.langs;
  if (!kind || !langs || !langs[kind]) return null;
  return langs[kind]();
}

/* Единая фабрика редактора: { root, getValue, setValue, setLang } */
function makeEditor(initial, langExt, onUpdate) {
  if (CM_READY) {
    const { EditorView, Compartment, basicSetup, search } = window.CM;
    const langComp = new Compartment();
    const roComp = new Compartment();
    const ext = [
      basicSetup,
      search({ top: true }),
      langComp.of(cmLang(langExt) || []),
      roComp.of([]),
      EditorView.lineWrapping, // длинные строки переносятся (R8-1)
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
    // синтаксическая подсветка по теме редактора: свой HighlightStyle
    // (не-fallback — выигрывает у светлого дефолта basicSetup)
    const sh = window.CM.syntaxHighlighting;
    const hl = EDITOR_HIGHLIGHT && EDITOR_HIGHLIGHT[effectiveEditorTheme()];
    if (sh && hl) ext.push(sh(hl));
    const view = new EditorView({
      doc: initial,
      extensions: ext,
      parent: null,
    });
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

/* ── каркас страницы ─────────────────────────────────────── */
function layout(content) {
  const route = parseRoute();
  const nav = (view, label) =>
    h(
      "a",
      {
        class: "nav-item" + (route.view === view ? " nav-item-active" : ""),
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
      label,
    );
  const header = h(
    "header",
    { class: "topbar" },
    h("div", { class: "brand" }, "NovelMaestro"),
    h(
      "div",
      { class: "topbar-right" },
      h("button", { class: "btn btn-ghost", onclick: logout }, "Выйти"),
    ),
  );
  const sidebar = h(
    "nav",
    { class: "sidebar" },
    nav("dashboard", "Дашборд"),
    nav("hub", "Проекты"),
    nav("settings", "Настройки"),
    nav("templates", "Шаблоны"),
    nav("notes", "Заметки"),
    nav("help", "Справка"),
  );
  return h(
    "div",
    { class: "layout" },
    header,
    h(
      "div",
      { class: "body" },
      sidebar,
      h("main", { class: "content" }, content),
    ),
  );
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
  if (!state.auth) {
    root.append(viewLogin());
    return;
  }
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
    run: () => window.viewRun(route.rest[0], route.rest[1], route.rest[2]),
  };
  const fn = views[route.view] || (() => viewUnknown(route.view));
  Promise.resolve(fn()).then((node) => {
    if (gen !== renderGen) return; // устаревший рендер — мимо
    root.replaceChildren();
    root.append(layout(node));
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
  const modal = h(
    "div",
    { class: "modal-backdrop", onclick: (e) => e.target === modal && close() },
    h(
      "div",
      { class: "modal" },
      h("div", { class: "modal-title" }, title),
      name,
      err,
      h(
        "div",
        { class: "modal-actions" },
        h("button", { class: "btn btn-ghost", onclick: close }, "Отмена"),
        okBtn,
      ),
    ),
  );
  document.body.append(modal);
  name.focus();
  if (initial) name.select();
  function close() {
    modal.remove();
  }
}

/* ── управление разделами  ────────────────────────── */
function sectionsModal() {
  const err = h("div", { class: "form-error" });
  const list = h("div", { class: "hub-sections-modal" });
  const modal = h(
    "div",
    { class: "modal-backdrop", onclick: (e) => e.target === modal && close() },
    h(
      "div",
      { class: "modal" },
      h("div", { class: "modal-title" }, "Управление разделами"),
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
        h("button", { class: "btn btn-ghost", onclick: close }, "Закрыть"),
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
    ),
  );
  document.body.append(modal);

  function renderRow(s) {
    const actions = h("div", { class: "factions" });
    actions.append(
      h(
        "button",
        {
          class: "btn btn-sm btn-ghost",
          onclick: () =>
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
            ),
        },
        "Переименовать",
      ),
      h(
        "button",
        {
          class: "btn btn-sm btn-danger-ghost",
          onclick: () =>
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
        },
        "Удалить",
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
  function close() {
    modal.remove();
  }
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
            "📁 " + e.name,
          )
        : h("span", { class: "fname" }, "📄 " + e.name);
      const actions = h("div", { class: "factions" });
      /* у каталогов шаблонов НИКАКИХ действий — только переход */
      if (!e.dir) {
        /* General — только чтение: «Просмотр» вместо «Правка»,
           Скачать тоже доступен */
        if (!writable) {
          actions.append(
            h(
              "button",
              {
                class: "btn btn-sm btn-ghost",
                onclick: () => openEdit(full),
              },
              "Просмотр",
            ),
          );
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
          );
        } else {
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
          );
          actions.append(
            h(
              "button",
              { class: "btn btn-sm btn-ghost", onclick: () => openEdit(full) },
              "Правка",
            ),
          );
          actions.append(
            h(
              "button",
              {
                class: "btn btn-sm btn-ghost",
                onclick: () =>
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
              },
              "Переим.",
            ),
          );
          actions.append(
            h(
              "button",
              {
                class: "btn btn-sm btn-danger-ghost",
                onclick: () =>
                  confirmModal("Удаление файла", full, "УДАЛИТЬ", async () => {
                    const q = new URLSearchParams({ path: full });
                    await api(
                      `/templates/${encodeURIComponent(st.set)}/file?${q}`,
                      { method: "DELETE" },
                    );
                    toast(`Удалено: ${full}`);
                    render();
                  }),
              },
              "Удалить",
            ),
          );
        }
      }
      return h("div", { class: "frow" }, nameNode, actions);
    };
    const TPL_PAGE_SIZE = 200;
    const pager = h("div", { class: "ner-pager" });
    let page = 0;
    const drop = h("div", { class: "files-list" });
    function renderRows() {
      const pages = Math.max(1, Math.ceil(entries.length / TPL_PAGE_SIZE));
      page = Math.min(page, pages - 1);
      drop.replaceChildren(
        ...entries
          .slice(page * TPL_PAGE_SIZE, (page + 1) * TPL_PAGE_SIZE)
          .map(row),
      );
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
          ` ${page + 1} / ${pages} · записей: ${entries.length} `,
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
    renderRows();
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
        ? h("div", {}, drop, pager)
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
    const ed = makeEditor(data.content || "", ext);
    ed.setReadOnly(readonly);
    const err = h("div", { class: "form-error" });

    /* превью md/html — как в редакторе Файлов проекта (sandbox-iframe) */
    const frame = h("iframe", {
      class: "editor-preview-frame preview-adaptive",
      sandbox: "allow-same-origin",
      title: "предпросмотр",
    });
    let mode = "code";
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
        await api(`/templates/${encodeURIComponent(st.set)}/file`, {
          method: "PUT",
          body: { path: full, content: ed.getValue() },
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
        "← К файлам",
      ),
      h(
        "span",
        { class: "editor-meta" },
        `${full} · ${fmtSize(data.size || 0)}`
        + (readonly ? " · только чтение" : ""),
      ),
      h("span", { class: "spacer" }),
      h("span", { class: "field-help" }, "кегль"),
      previewFontSelect(() => {
        if (mode !== "code") renderPreview();
      }),
      findBtn,
      prevBtn,
      ...(readonly ? [] : [saveBtn]),
    );
    const editorHost = h("div", { class: "editor-cm" }, ed.root);
    return h(
      "div",
      { class: "editor-wrap editor-has-preview" },
      toolbar,
      err,
      editorHost,
      frame,
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
async function boot() {
  // Esc закрывает верхнюю модалку (W9)
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
  } catch {
    state.auth = false;
  }
  applyEditorSettings();
  window.addEventListener("hashchange", render);
  render();
}

boot();

/* Smoke всех вкладок проекта: viewProject(section, name, tab) рендерится
 * в Node с DOM-mock и мок-API без единого ReferenceError.
 * Регрессия «вкладка не открывается, видна только панель»: глобальные
 * функции рендера использовали section/name из замыкания viewProject.
 * Запуск: node --test tests/spa/*.test.mjs */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { createRequire } from "node:module";

const _require = createRequire(import.meta.url);
const UICore = _require("../../web/static/ui-core.js");
const SRC = readFileSync(
  new URL("../../web/static/project-views.js", import.meta.url),
  "utf8",
);
/* общий DOM-слой: в vm он объявляет UIC (и h/iconEl) так же, как в
   браузере — классическим скриптом, до view-файлов */
const UIC_SRC = readFileSync(
  new URL("../../web/static/ui-components.js", import.meta.url),
  "utf8",
);
/* run-views.js — код вкладки «Запуски» (window.viewRun зовётся из
   project-views при st.view === "run") */
const RUN_SRC = readFileSync(
  new URL("../../web/static/run-views.js", import.meta.url),
  "utf8",
);

/* ── минимальный DOM ─────────────────────────────────────────────── */
class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.style = {};
    this.className = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.textContent = "";
    this.dataset = {};
    this._attrs = {};
    this._listeners = {};
  }
  append(...kids) {
    for (const k of kids.flat()) {
      if (k == null) continue;
      k._parent = this;
      this.children.push(k);
    }
  }
  /* поиск по классу — как в DOM: нужен обработчику Space */
  closest(sel) {
    const want = sel.replace(/^\./, "");
    let n = this;
    while (n) {
      if ((n.className || "").split(/\s+/).includes(want)) return n;
      n = n._parent;
    }
    return null;
  }
  appendChild(k) { this.children.push(k); }
  replaceChildren(...kids) { this.children = []; this.append(...kids); }
  addEventListener(ev, fn) { (this._listeners[ev] ||= []).push(fn); }
  setAttribute(k, v) { this._attrs[k] = String(v); }
  getAttribute(k) { return this._attrs[k] ?? null; }
  remove() {}
  removeChild() {}
  /* запрос по классу/тегу по всему поддереву: обработчикам запусков
     (".run-panel-head") нужен настоящий результат, а не null */
  querySelectorAll(sel) {
    const want = String(sel).replace(/^\./, "");
    const out = [];
    const walk = (n) => {
      for (const c of n.children || []) {
        if ((c.className || "").split(/\s+/).includes(want)
            || c.tagName === want.toUpperCase()) out.push(c);
        walk(c);
      }
    };
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  get classList() {
    const s = new Set(this.className.split(/\s+/).filter(Boolean));
    return {
      add: (...c) => { for (const x of c) s.add(x); this.className = [...s].join(" "); },
      remove: (...c) => { for (const x of c) s.delete(x); this.className = [...s].join(" "); },
      toggle: (c, force) => {
        const on = force === undefined ? !s.has(c) : !!force;
        if (on) s.add(c); else s.delete(c);
        this.className = [...s].join(" ");
        return on;
      },
      contains: (c) => s.has(c),
    };
  }
  get firstChild() { return this.children[0] ?? null; }
  get lastChild() { return this.children[this.children.length - 1] ?? null; }
  get childNodes() { return this.children; }
  focus() {}
  click() {}
}

/* ── мок-глобалы (в модуле: функции, созданные вне vm, замыкаются
   на внешний scope, поэтому document/Node/window кладём на globalThis) ── */
globalThis.document = {
  body: null, // ниже: body — обычный El, на него вешают оверлеи
  createElement: (t) => new El(t),
  createTextNode: (t) => {
    const n = new El("#text");
    n.textContent = String(t);
    return n;
  },
  addEventListener() {},
  querySelectorAll: () => [],
};
globalThis.document.body = new El("body");
globalThis.DOMParser = class {
  parseFromString() {
    return { documentElement: null };
  }
};
globalThis.window = {
  marked: { parse: (s) => s },
  CM: {},
  /* иконки: iconEl берёт каталог из window.UICore — как в браузере */
  UICore,
  addEventListener() {},
};
globalThis.Node = El;
globalThis.localStorage = (() => {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(String(k), String(v)),
    removeItem: (k) => m.delete(k),
    clear: () => m.clear(),
  };
})();
globalThis.confirm = () => true;
globalThis.prompt = () => "";
globalThis.location = { hash: "" };

function h(tag, attrs = {}, ...children) {
  const node = globalThis.document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2), v);
    } else if (k === "value") node.value = v;

    else node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    const kid = child instanceof Node ? child : document.createTextNode(String(child));
    kid._parent = node;
    node.append(kid);
  }
  return node;
}

/* ── мок-API: все роуты project-views возвращают пустые структуры ── */
async function api(path, opts = {}) {
  const p = path.split("?")[0];
  if ((opts.method || "GET") !== "GET") {
    // запись запроса: тестам важно, каким телом ушёл перенос/переименование
    (globalThis.__calls || []).push({
      method: opts.method,
      path: p,
      query: path.split("?")[1] || "",
      body: opts.body || null,
    });
    return { ok: true, moved: [], skipped: [] };
  }
  if (p === "/files") {
    return {
      entries: globalThis.__files || [],
      dirs: globalThis.__dirs || [],
    };
  }
  if (p === "/file") return { content: "", missing: true, exists: false, size: 0 };
  if (p === "/ner") return { items: [], too_large: false };
  if (p === "/check") return { reports: [] };
  if (p === "/ner/review" || p === "/translate_check_llm/review") {
    return { exists: false, content: "", size: 0 };
  }
  if (p.endsWith("/tree")) return { chapters: [], artifacts: {} };
  if (p.endsWith("/status")) return { status: { chapters: {}, counts: {} } };
  if (p.endsWith("/chapters/titles")) return { titles: {} };
  /* роуты «Настроек»: текст файла API не отдаёт — только блоки реестра */
  if (p === "/settings") return SETTINGS_PAYLOAD;
  if (p === "/stages/compile/options") return { modes: [] };
  if (p === "/cover") return { files: [] };
  if (p === "/templates") return { templates: [] };
  if (p.startsWith("/prompts")) return { files: [], content: "" };
  if (p.startsWith("/logs")) return { logs: [], content: "" };
  if (p === "/ner/export") return { ok: true, content: "" };
  if (p === "/jobs") return { jobs: [] };
  if (p.startsWith("/jobs/")) return { job: {} };
  if (p === "/stages") {
    return { stages: STAGES, profiles: SETTINGS_PAYLOAD.profiles };
  }
  if (p.startsWith("/stages/")) {
    const key = p.split("/")[2];
    return { spec: { key, title: key, fields: SPEC_FIELDS[key] || [] },
             options: {} };
  }
  return { ok: true };
}

function makeEditor(initial) {
  const ta = new El("textarea");
  ta.value = initial;
  return {
    root: ta, isCM: false,
    getValue: () => ta.value,
    setValue: (t) => { ta.value = t; },
    setLang() {}, setReadOnly() {},
  };
}

/* стадии и профили LLM: поле «Профиль LLM» рисует сводку отсюда */
const STAGES = [
  { key: "ner", title: "Глоссарий", script: "cli/ner.py" },
  { key: "pipeline", title: "Перевод", script: "web/pipeline.py" },
];
/* профиль — ПЕРВОЕ ПОЛЕ формы каждой LLM-стадии (noenv: значение живёт в
   браузере проекта+стадии, в .env и в argv оно не попадает) */
const PROFILE_FIELD = {
  name: "profile", label: "Профиль LLM", type: "select", default: "general",
  options: ["general", "p1"], labels: { general: "General", p1: "Домашний" },
  noenv: true, help: "Настройки модели для ЭТОЙ стадии",
};
const SPEC_FIELDS = {
  ner: [
    { ...PROFILE_FIELD },
    { name: "chunk_size", label: "Размер чанка, ТОКЕНЫ", type: "number",
      default: "8000" },
  ],
  pipeline: [
    { ...PROFILE_FIELD },
    { name: "action", label: "Тип работы", type: "select", default: "all",
      options: ["all"], labels: { all: "полный цикл" } },
  ],
};
const SETTINGS_PAYLOAD = {
  path: "/tmp/shared.env",
  exists: true,
  profile: "general",
  profile_file: "/tmp/llm_profiles.json",
  env_wins: [],
  groups: [],
  profiles: [
    { id: "general", name: "General", builtin: true,
      host: "http://общий:9989", model: "общая-модель",
      reasoning_mode: "default" },
    { id: "p1", name: "Домашний", builtin: false,
      host: "http://дом:9989", model: "дом-модель", reasoning_mode: "on" },
  ],
};

const sandbox = {
  console,
  URLSearchParams,
  FormData,
  setTimeout,
  clearTimeout,
  requestAnimationFrame: (fn) => { fn(); return 1; },
  /* vm-код (project-views) резолвит document/window/localStorage/Node
     в СВОЁМ контексте — кладём те же моки и в sandbox */
  document: globalThis.document,
  window: globalThis.window,
  localStorage: globalThis.localStorage,
  Node: El,
  DOMParser: globalThis.DOMParser,
  h,
  api,
  previewFontSelect: () => new El("select"),
  apiUpload: async () => ({ saved: [] }),
  toast() {},
  fmtSize: (n) => `${n} B`,
  crumb(text, fn) { return h("button", { onclick: fn }, text); },
  /* модалка имени из app.js: поле (с текущим значением) + ОК — ровно то,
     что нужно тестам «Файлов»; в vm app.js не грузится */
  nameModal(title, placeholder, onOk, initial = "") {
    const input = h("input", { class: "input", placeholder });
    if (initial) input.value = initial;
    const ok = h("button", {
      class: "btn btn-primary",
      onclick: async () => { await onOk(input.value.trim()); },
    }, "ОК");
    const box = h("div", { class: "modal" },
      h("div", { class: "modal-title" }, title), input, ok);
    globalThis.document.body.append(h("div", { class: "modal-backdrop" }, box));
    return box;
  },
  attachTooltip() {},
  mdPreviewSrcdoc: (html) => html,
  fitPreviewFrame() {},
  makeEditor,
  extOf: () => "txt",
  UICore,
};
vm.createContext(sandbox);
vm.runInContext(UIC_SRC, sandbox); // globalThis.UIC, h, iconEl
vm.runInContext(RUN_SRC, sandbox); // window.viewRun — вкладка «Запуски»
vm.runInContext(SRC, sandbox);
const viewProject = sandbox.viewProject;

/* все вкладки страницы проекта: viewProject(section, name, tab) */
const TABS = ["files", "run", "editor", "ner", "review", "chapters",
              "status", "config", "prompts", "logs", "notes"];

for (const tab of TABS) {
  test(`вкладка «${tab}» рендерится без ReferenceError`, async () => {
    /* viewProject не async: render() наполняет page микрозадачами —
       даём им завершиться (в браузере это неотличимо от жизни) */
    const page = viewProject("ACTIVE", "Книга", tab);
    await new Promise((r) => setTimeout(r, 10));
    assert.ok(page, `viewProject вернул пусто для ${tab}`);
    assert.ok(page.children.length >= 1, `вкладка ${tab} без контента`);
  });
}

test("reviewView: 4 под-вкладки проверок, активная по умолчанию — первая", async () => {
  const page = viewProject("ACTIVE", "Книга", "review");
  await new Promise((r) => setTimeout(r, 10));
  const texts = [];
  (function walk(n) {
    if (!n || typeof n !== "object") return;
    if (typeof n.textContent === "string" && n.textContent) {
      texts.push(n.textContent);
    }
    for (const c of n.children || []) walk(c);
  })(page);
  const joined = texts.join(" ");
  assert.match(joined, /Глоссарий \(LLM\)/);
  assert.match(joined, /Проверка перевода/);
  assert.match(joined, /Перевод \(LLM\)/);
  assert.match(joined, /Оценка перевода \(LLM\)/);
  const tabs = [];
  (function walk2(n) {
    for (const c of n.children || []) {
      if ((c.className || "").split(/\s+/).includes("subtab")) tabs.push(c);
      walk2(c);
    }
  })(page);
  assert.equal(tabs.length, 4);
  assert.ok(tabs[0].className.includes("subtab-active"));
});

test("reviewView: переключение под-вкладок (клик) и память в localStorage", async () => {
  const page = viewProject("ACTIVE", "Книга", "review");
  await new Promise((r) => setTimeout(r, 10));
  const tabs = [];
  const panes = [];
  (function walk(n) {
    for (const c of n.children || []) {
      const cls = (c.className || "").split(/\s+/);
      if (cls.includes("subtab")) tabs.push(c);
      if (cls.includes("review-pane")) panes.push(c);
      walk(c);
    }
  })(page);
  assert.equal(tabs.length, 4);
  assert.equal(panes.length, 4);
  // клик по третьей вкладке «Перевод (LLM)»
  await tabs[2]._listeners["click"][0]();
  assert.ok(tabs[2].className.includes("subtab-active"));
  assert.ok(!tabs[0].className.includes("subtab-active"));
  assert.equal(panes[2].style.display, "");
  assert.equal(panes[0].style.display, "none");
  // выбор запомнен: повторное открытие вкладки — активна «tcl»
  assert.equal(globalThis.localStorage.getItem("reviewTab"), "tcl");
  const page2 = viewProject("ACTIVE", "Книга", "review");
  await new Promise((r) => setTimeout(r, 10));
  const tabs2 = [];
  (function walk(n) {
    for (const c of n.children || []) {
      if ((c.className || "").split(/\s+/).includes("subtab")) tabs2.push(c);
      walk(c);
    }
  })(page2);
  assert.ok(tabs2[2].className.includes("subtab-active"));
});

/* ── сортировка в списке файлов ────────────────────────────────── */
const FILES = [
  { name: "b.txt", dir: false, size: 10, mtime: 100 },
  { name: "A", dir: true, size: 0, mtime: 300 },
  { name: "a10.txt", dir: false, size: 5, mtime: 200 },
  { name: "a2.txt", dir: false, size: 7, mtime: 150 },
];

function filesRender(page) {
  const names = [];
  const text = (n) =>
    (n.textContent || "") + (n.children || []).map(text).join("");
  (function walk(n) {
    for (const c of n.children || []) {
      if ((c.className || "").split(/\s+/).includes("fname")) {
        names.push(text(c).trim());
      }
      walk(c);
    }
  })(page);
  return names;
}

test("сортировка файлов: каталоги первыми, имена — натурально", async () => {
  globalThis.__files = FILES;
  const page = viewProject("ACTIVE", "Книга", "files");
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(filesRender(page),
    ["A", "a2.txt", "a10.txt", "b.txt"]);
});

test("сортировка файлов: порядок и ключ помнятся (mtime, убыв.)", async () => {
  globalThis.__files = FILES;
  globalThis.localStorage.setItem("filesSort", "mtime");
  globalThis.localStorage.setItem("filesAsc", "0");
  const page = viewProject("ACTIVE", "Книга", "files");
  await new Promise((r) => setTimeout(r, 10));
  // каталог A (300) всё равно первым, файлы — по дате убыв.
  assert.deepEqual(filesRender(page),
    ["A", "a10.txt", "a2.txt", "b.txt"]);
});

test("сортировка файлов: переключатель направления перерисовывает список", async () => {
  globalThis.__files = FILES;
  globalThis.localStorage.setItem("filesSort", "name");
  globalThis.localStorage.removeItem("filesAsc");
  const page = viewProject("ACTIVE", "Книга", "files");
  await new Promise((r) => setTimeout(r, 10));
  const btns = [];
  (function walk(n) {
    for (const c of n.children || []) {
      if ((c.className || "").split(/\s+/).includes("sort-dir")) btns.push(c);
      walk(c);
    }
  })(page);
  assert.equal(btns.length, 1);
  await btns[0]._listeners["click"][0]();
  assert.equal(globalThis.localStorage.getItem("filesAsc"), "0");
});

/* ── быстрый просмотр по Space ───────────────────────────── */
test("quick-look: Space на строке открывает модалку с sandbox-кадром", async () => {
  globalThis.__files = [
    { name: "README.md", dir: false, size: 12, mtime: 100 },
  ];
  globalThis.localStorage.setItem("filesSort", "name");
  globalThis.localStorage.setItem("filesAsc", "1");
  // модалки предыдущих тестов остаются в body — стартуем с чистого листа
  globalThis.document.body.children.length = 0;
  const page = viewProject("ACTIVE", "Книга", "files");
  await new Promise((r) => setTimeout(r, 10));
  const rows = [];
  (function walk(n) {
    for (const c of n.children || []) {
      if ((c.className || "").split(/\s+/).includes("frow")) rows.push(c);
      walk(c);
    }
  })(page);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].getAttribute("data-name"), "README.md");
  assert.equal(globalThis.document.body.children.length, 0, "до Space модалок нет");
  const drop = rows[0]._parent;
  await drop._listeners["keydown"][0]({
    key: " ",
    target: rows[0],
    preventDefault() {},
  });
  await new Promise((r) => setTimeout(r, 10));
  const open = globalThis.document.body.children
    .map((el) => (el.className || "").split(/\s+/).includes("modal-backdrop"))
    .filter(Boolean);
  assert.equal(open.length, 1, "Space открыл ровно одну модалку");
});

const tick = () => new Promise((r) => setTimeout(r, 10));

function collectText(node, out = []) {
  if (node && typeof node === "object") {
    if (typeof node.textContent === "string" && node.textContent) {
      out.push(node.textContent);
    }
    for (const c of node.children || []) collectText(c, out);
  }
  return out;
}

/* ── «Файлы»: выделка вместо построчных меню ──────────────────── */
const FILES_F = [
  { name: "chapters", dir: true, size: 0, mtime: 10 },
  { name: "a.txt", dir: false, size: 5, mtime: 20 },
  { name: "b.png", dir: false, size: 7, mtime: 30 },
];

async function filesPage(dirs = ["chapters", "tmp"]) {
  globalThis.__files = FILES_F;
  globalThis.__dirs = dirs;
  globalThis.__calls = [];
  globalThis.localStorage.setItem("filesSort", "name");
  globalThis.localStorage.setItem("filesAsc", "1");
  // модалки предыдущих тестов остаются в body — стартуем с чистого листа
  globalThis.document.body.children.length = 0;
  const page = viewProject("ACTIVE", "Книга", "files");
  await tick();
  return page;
}
const tips = (node) => findByClass(node, "icon-btn")
  .map((b) => b.getAttribute("aria-label"));

test("файлы: строка — чекбокс и кнопки одного объекта, меню «⋮» нет", async () => {
  const page = await filesPage();
  const rows = findByClass(page, "frow");
  assert.equal(rows.length, 3);
  assert.equal(findByClass(page, "kebab-btn").length, 0, "построчных меню не осталось");
  assert.equal(findByClass(page, "menu-box").length, 0);
  // каталог: чекбокс + одна кнопка; файл: чекбокс + править и переименовать
  assert.equal(findByClass(rows[0], "fsel").length, 1);
  assert.deepEqual(tips(rows[0]), ["Переименовать chapters"]);
  assert.deepEqual(tips(rows[1]), ["Править a.txt", "Переименовать a.txt"]);
});

test("файлы: чекбокс строки включает панель выделения и прячет кнопки", async () => {
  const page = await filesPage();
  const tools = findByClass(page, "files-tools")[0];
  const bar = findByClass(page, "files-sel")[0];
  assert.equal(bar.classList.contains("hidden"), true, "без выделки панели нет");
  assert.equal(tools.classList.contains("hidden"), false);
  const cb = findByClass(findByClass(page, "frow")[1], "fsel")[0];
  cb.checked = true;
  await cb._listeners.change[0]();
  assert.equal(tools.classList.contains("hidden"), true, "кнопки ушли на время выделки");
  assert.equal(bar.classList.contains("hidden"), false);
  assert.equal(collectText(findByClass(bar, "files-sel-count")[0]).join(""),
    "выделено: 1");
  assert.deepEqual(tips(bar), [
    "Переименовать a.txt",
    "Скачать выделенные файлы",
    "Перенести в…",
    "Удалить выделенное",
    "Снять выделение",
  ]);
});

test("файлы: выделка из одних каталогов — скачивания в панели нет", async () => {
  const page = await filesPage();
  const cb = findByClass(findByClass(page, "frow")[0], "fsel")[0];
  cb.checked = true;
  await cb._listeners.change[0]();
  assert.deepEqual(tips(findByClass(page, "files-sel")[0]), [
    "Переименовать chapters",
    "Перенести в…",
    "Удалить выделенное",
    "Снять выделение",
  ]);
});

test("файлы: «выделить всё» берёт папку, «Снять» — очищает", async () => {
  const page = await filesPage();
  const all = findByClass(page, "fsel")[0];
  all.checked = true;
  await all._listeners.change[0]();
  await tick();
  assert.equal(collectText(findByClass(page, "files-sel-count")[0]).join(""),
    "выделено: 3");
  // переименование — только при одном объекте
  assert.equal(tips(findByClass(page, "files-sel")[0]).includes(
    "Переименовать a.txt"), false);
  const clear = findByClass(page, "files-sel")[0]
    .querySelectorAll("button").slice(-1)[0];
  await clear._listeners.click[0]();
  assert.equal(findByClass(page, "files-sel")[0].classList.contains("hidden"), true);
  assert.equal(findByClass(page, "files-tools")[0].classList.contains("hidden"), false);
});

test("файлы: смена папки снимает выделение", async () => {
  const page = await filesPage();
  const cb = findByClass(findByClass(page, "frow")[1], "fsel")[0];
  cb.checked = true;
  await cb._listeners.change[0]();
  const link = findByClass(findByClass(page, "frow")[0], "fname")[0];
  await link._listeners.click[0]({ preventDefault() {} });
  await tick();
  assert.equal(findByClass(page, "files-sel")[0].classList.contains("hidden"), true);
});

test("файлы: перенос выделенного — один POST со всеми путями и dest", async () => {
  const page = await filesPage();
  for (const i of [1, 2]) {
    const cb = findByClass(findByClass(page, "frow")[i], "fsel")[0];
    cb.checked = true;
    await cb._listeners.change[0]();
  }
  const move = findByClass(page, "icon-btn")
    .find((b) => (b.getAttribute("aria-label") || "").startsWith("Перенести"));
  await move._listeners.click[0]();
  await tick();

  const backdrop = globalThis.document.body.children
    .filter((el) => (el.className || "").split(/\s+/).includes("modal-backdrop"))
    .slice(-1)[0];
  assert.ok(backdrop, "модалка переноса открылась");
  const box = findTag(backdrop, "select")[0];
  assert.deepEqual(box.children.map((o) => collectText(o).join("")),
    ["Корень проекта", "chapters", "tmp"]);
  box.value = "tmp";
  const okBtn = findByClass(backdrop, "btn-primary")[0];
  await okBtn._listeners.click[0]();
  await tick();
  const call = globalThis.__calls.find((c) => c.path === "/file/move");
  assert.ok(call, "POST /api/file/move ушёл");
  // тело создано в vm-контексте: сверяем через JSON — иначе deepEqual
  // цепляется на разницу прототипов двух realms при одинаковом содержимом
  assert.deepEqual(JSON.parse(JSON.stringify(call.body)), {
    project: "ACTIVE/Книга",
    paths: ["a.txt", "b.png"],
    dest: "tmp",
  });
});

test("файлы: диалог переноса не предлагает текущую папку", async () => {
  // st живёт внутри одного вызова viewProject: переходим по папке на том же page
  const page = await filesPage(["chapters", "chapters/00000_1_Глава 1", "tmp"]);
  await findByClass(findByClass(page, "frow")[0], "fname")[0]
    ._listeners.click[0]({ preventDefault() {} });
  await tick();
  const cb = findByClass(findByClass(page, "frow")[1], "fsel")[0];
  cb.checked = true;
  await cb._listeners.change[0]();
  const mv = findByClass(page, "icon-btn")
    .find((b) => (b.getAttribute("aria-label") || "").startsWith("Перенести"));
  await mv._listeners.click[0]();
  await tick();
  const bd = globalThis.document.body.children
    .filter((el) => (el.className || "").split(/\s+/).includes("modal-backdrop"))
    .slice(-1)[0];
  assert.ok(bd, "модалка переноса открылась");
  assert.deepEqual(findTag(bd, "select")[0].children
    .map((o) => collectText(o).join("")),
  ["Корень проекта", "chapters/00000_1_Глава 1", "tmp"]);
});

test("файлы: переименовать из панели — POST /file/rename, выделка снимается", async () => {
  const page = await filesPage();
  const cb = findByClass(findByClass(page, "frow")[0], "fsel")[0]; // каталог
  cb.checked = true;
  await cb._listeners.change[0]();
  const btn = findByClass(page, "files-sel")[0].querySelectorAll("button")
    .find((b) => (b.getAttribute("aria-label") || "").startsWith("Переименовать"));
  await btn._listeners.click[0]();
  const bd = globalThis.document.body.children
    .filter((el) => (el.className || "").split(/\s+/).includes("modal-backdrop"))
    .slice(-1)[0];
  assert.ok(bd, "модалка переименования открылась");
  const input = findTag(bd, "input")[0];
  assert.equal(input.value, "chapters", "имя подставлено в поле");
  input.value = "glavy";
  await findByClass(bd, "btn-primary")[0]._listeners.click[0]();
  await tick();
  const call = globalThis.__calls.find((c) => c.path === "/file/rename");
  assert.ok(call, "POST /api/file/rename ушёл");
  assert.deepEqual(JSON.parse(JSON.stringify(call.body)), {
    project: "ACTIVE/Книга", path: "chapters", new_name: "glavy",
  });
  // после операции выделка снимается — панель не висит на новом списке
  assert.equal(findByClass(page, "files-sel")[0].classList.contains("hidden"), true);
});

/* ── профиль LLM: у каждой LLM-стадии свой выбор ───────────────── */
/* форма стадии рисуется только когда стадия открыта — открываем клик */
async function runStage(key) {
  const title = (STAGES.find((s) => s.key === key) || {}).title;
  const page = viewProject("ACTIVE", "Книга", "run");
  await tick();
  const card = findByClass(page, "stage-card")
    .find((c) => collectText(c).includes(title));
  await card._listeners.click[0]();
  await tick();
  return page;
}

function findTag(node, tag, out = []) {
  if (node && typeof node === "object") {
    if (node.tagName === String(tag).toUpperCase()) out.push(node);
    for (const c of node.children || []) findTag(c, tag, out);
  }
  return out;
}

function findByClass(node, cls, out = []) {
  if (node && typeof node === "object") {
    if ((node.className || "").split(/\s+/).includes(cls)) out.push(node);
    for (const c of node.children || []) findByClass(c, cls, out);
  }
  return out;
}

test("запуски: профиль LLM — первое поле формы стадии", async () => {
  const page = await runStage("ner");
  const fields = findByClass(page, "field-profile");
  assert.equal(fields.length, 1, "поле профиля одно");
  const sel = findTag(fields[0], "select")[0];
  assert.ok(sel, "селектор профиля");
  /* _field — на label-обёртке поля (на нём держатся и подрежимы) */
  assert.equal(fields[0]._field.name, "profile");
  /* текст <option> — текстовый узел-ребёнок, а не textContent родителя */
  const optText = (o) => (o.children || [])
    .map((c) => (typeof c === "string" ? c : c.textContent || "")).join("");
  assert.deepEqual(sel.children.map(optText), ["General", "Домашний"]);
  /* выбор браузера пуст → встроенный профиль */
  assert.equal(sel.value, "general");
  const note = findByClass(fields[0], "run-profile-note")[0];
  assert.match(note.textContent, /General — значения общего конфига/);
  assert.match(note.textContent, /http:\/\/общий:9989/);
  assert.match(note.textContent, /общая-модель/);
});

test("запуски: профиль стадии живёт в localStorage проекта и стадии", async () => {
  const page = await runStage("ner");
  const sel = findTag(findByClass(page, "field-profile")[0], "select")[0];
  sel.value = "p1";
  await sel._listeners.change[0]({ target: sel });
  assert.equal(
    globalThis.localStorage.getItem("nmProfile:ACTIVE/Книга/ner"), "p1",
  );
  const note = findByClass(page, "run-profile-note")[0];
  assert.match(note.textContent, /Домашний — наследует General/);
  assert.match(note.textContent, /http:\/\/дом:9989/);
  /* рассуждения профиля — тоже часть сводки */
  assert.match(note.textContent, /рассуждения: on/);
  /* сохранённый выбор переживает перерисовку */
  const page2 = await runStage("ner");
  assert.equal(
    findTag(findByClass(page2, "field-profile")[0], "select")[0].value, "p1");
});

test("запуски: у стадий одного проекта профиль свой", async () => {
  const page = await runStage("ner");
  const nerSel = findTag(findByClass(page, "field-profile")[0], "select")[0];
  nerSel.value = "p1";
  await nerSel._listeners.change[0]({ target: nerSel });
  /* вторая стадия того же проекта — со своим выбором (General) */
  const page2 = await runStage("pipeline");
  const pipeSel = findTag(findByClass(page2, "field-profile")[0], "select")[0];
  assert.equal(pipeSel.value, "general");
  assert.equal(globalThis.localStorage.getItem("nmProfile:ACTIVE/Книга/pipeline"),
    null);
  /* и память ner при этом осталась своей */
  assert.equal(globalThis.localStorage.getItem("nmProfile:ACTIVE/Книга/ner"), "p1");
});

test("запуски: смена профиля — не «изменённая настройка» стадии", async () => {
  /* память профиля живёт отдельно: кнопка сброса настроек на неё не влияет */
  const page = await runStage("ner");
  const sel = findTag(findByClass(page, "field-profile")[0], "select")[0];
  sel.value = "p1";
  await sel._listeners.change[0]({ target: sel });
  const btn = findByClass(page, "run-reset")[0];
  assert.ok(btn.className.split(/\s+/).includes("hidden"),
    "сброшенные настройки не должны считать профиль изменённым полем");
});

test("запуски: профиль уезжает в params запуска вместе с полями формы", () => {
  /* профиль — поле формы: отдельного поля тела запуска больше нет */
  assert.doesNotMatch(RUN_SRC, /profile: st\.profile/);
  assert.match(RUN_SRC, /params: buildParams\(key, spec\)/);
  assert.match(RUN_SRC, /if \(f\.name === PROFILE_FIELD\) continue/);
});

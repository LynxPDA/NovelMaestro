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
    for (const k of kids.flat()) if (k != null) this.children.push(k);
  }
  appendChild(k) { this.children.push(k); }
  replaceChildren(...kids) { this.children = []; this.append(...kids); }
  addEventListener(ev, fn) { (this._listeners[ev] ||= []).push(fn); }
  setAttribute(k, v) { this._attrs[k] = String(v); }
  getAttribute(k) { return this._attrs[k] ?? null; }
  remove() {}
  removeChild() {}
  closest() { return null; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
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
  createElement: (t) => new El(t),
  createTextNode: (t) => {
    const n = new El("#text");
    n.textContent = String(t);
    return n;
  },
  addEventListener() {},
  querySelectorAll: () => [],
};
globalThis.window = {
  marked: { parse: (s) => s },
  CM: {},
  addEventListener() {},
};
globalThis.Node = El;
globalThis.localStorage = (() => {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(String(k), String(v)),
    removeItem: (k) => m.delete(k),
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
    node.append(
      child instanceof Node ? child : document.createTextNode(String(child)),
    );
  }
  return node;
}

/* ── мок-API: все роуты project-views возвращают пустые структуры ── */
async function api(path, opts = {}) {
  const p = path.split("?")[0];
  if ((opts.method || "GET") !== "GET") return { ok: true };
  if (p === "/files") return { entries: [] };
  if (p === "/file") return { content: "", missing: true, exists: false, size: 0 };
  if (p === "/ner") return { items: [], too_large: false };
  if (p === "/check") return { reports: [] };
  if (p === "/ner/review" || p === "/translate_check_llm/review") {
    return { exists: false, content: "", size: 0 };
  }
  if (p.endsWith("/tree")) return { chapters: [], artifacts: {} };
  if (p.endsWith("/status")) return { status: { chapters: {}, counts: {} } };
  if (p.endsWith("/chapters/titles")) return { titles: {} };
  if (p === "/env") return { vars: {}, files: [] };
  if (p === "/env/template") return { content: "" };
  if (p === "/stages/compile/options") return { modes: [] };
  if (p === "/cover") return { files: [] };
  if (p === "/templates") return { templates: [] };
  if (p.startsWith("/prompts")) return { files: [], content: "" };
  if (p.startsWith("/logs")) return { logs: [], content: "" };
  if (p === "/ner/export") return { ok: true, content: "" };
  if (p.startsWith("/jobs")) return { job: {} };
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
  h,
  api,
  previewFontSelect: () => new El("select"),
  apiUpload: async () => ({ saved: [] }),
  toast() {},
  fmtSize: (n) => `${n} B`,
  crumb(text, fn) { return h("button", { onclick: fn }, text); },
  attachTooltip() {},
  mdPreviewSrcdoc: (html) => html,
  fitPreviewFrame() {},
  makeEditor,
  extOf: () => "txt",
  UICore,
};
vm.createContext(sandbox);
vm.runInContext(SRC, sandbox);
const viewProject = sandbox.viewProject;

/* все вкладки страницы проекта: viewProject(section, name, tab) */
const TABS = ["files", "editor", "ner", "review", "chapters", "status",
              "config", "prompts", "logs", "notes"];

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

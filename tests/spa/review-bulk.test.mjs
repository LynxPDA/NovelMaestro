/* «Принять все» / «Отклонить все» в карточках «Проверок»: bulk-статус
 * всем неприменённым правкам (ner_review.json / tcl_review.json).
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

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.style = {};
    this.className = "";
    this.value = "";
    this.checked = false;
    this.textContent = "";
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
  focus() {}
  click() {}
}

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
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
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

/* review-файл глоссария: одна применённая + одна с уже «принять» */
const DOC = {
  created: "2024-01-01 10:00",
  updated: "2024-01-01 10:00",
  input: "ner.json",
  entries: [
    { term: "林凡", field: "type", old: "Person", new: "Person (male)",
      reason: "r", status: "принять", applied: false },
    { term: "青云宗", field: "type", old: "Location", new: "Location (sect)",
      reason: "r2", status: "отклонить", applied: false },
    { term: "火球术", field: "translation", old: "X", new: "Y",
      reason: "r3", status: "принять", applied: true },
    { term: "苏幕遮", field: "type", old: "A", new: "B",
      reason: "r4", status: "", applied: false },
  ],
};
const CONTENT = JSON.stringify(DOC, null, 2);

const putBodies = [];
let REVIEW_CONTENT = CONTENT;
async function api(path, opts = {}) {
  const p = path.split("?")[0];
  if ((opts.method || "GET") === "PUT") {
    putBodies.push(JSON.parse(opts.body.content));
    return { ok: true };
  }
  if (p === "/ner/review") {
    return { exists: true, content: REVIEW_CONTENT,
             size: REVIEW_CONTENT.length };
  }
  if (p === "/translate_check_llm/review") {
    return { exists: false, content: "", size: 0 };
  }
  if (p === "/check") return { reports: [] };
  if (p === "/file") return { content: "", missing: true, exists: false, size: 0 };
  if (p === "/ner") return { items: [], too_large: false };
  if (p === "/env") return { vars: {}, files: [] };
  if (p === "/templates") return { templates: [] };
  return { ok: true };
}

const sandbox = {
  console,
  URLSearchParams,
  FormData,
  setTimeout,
  clearTimeout,
  requestAnimationFrame: (fn) => { fn(); return 1; },
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
  makeEditor() { return { root: new El("textarea"), isCM: false, getValue: () => "", setValue() {}, setLang() {}, setReadOnly() {} }; },
  extOf: () => "txt",
  UICore,
};
vm.createContext(sandbox);
vm.runInContext(SRC, sandbox);
const viewProject = sandbox.viewProject;

function buttons(page) {
  const out = [];
  (function walk(n) {
    for (const c of n.children || []) walk(c);
    if (n.tagName === "BUTTON") out.push(n);
  })(page);
  return out;
}

/* в DOM-mock textContent не агрегируется — ищем по title */
function bulkBtn(page, label) {
  return buttons(page).find((b) =>
    (b._attrs["title"] || "").includes(`статус «${label}»`));
}

async function openReview() {
  putBodies.length = 0;
  const page = viewProject("ACTIVE", "Книга", "review");
  await new Promise((r) => setTimeout(r, 20));
  return page;
}

test("«Принять все»: неприменённые правки получают «принять», применённые — не трогаются", async () => {
  const page = await openReview();
  const btn = bulkBtn(page, "принять");
  assert.ok(btn, "нет кнопки «Принять все»");
  await btn._listeners["click"][0]();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(putBodies.length, 1, "должен быть один PUT");
  const saved = putBodies[0]["entries"];
  assert.equal(saved.length, 4);
  assert.equal(saved[0]["status"], "принять"); // уже была — не менялась
  assert.equal(saved[1]["status"], "принять"); // была «отклонить»
  assert.equal(saved[2]["applied"], true);     // применённая — нетронута
  assert.equal(saved[3]["status"], "принять"); // была пустая
});

test("«Отклонить все»: работает по тому же принципу", async () => {
  const page = await openReview();
  const btn = bulkBtn(page, "отклонить");
  assert.ok(btn, "нет кнопки «Отклонить все»");
  await btn._listeners["click"][0]();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(putBodies.length, 1);
  const saved = putBodies[0]["entries"];
  assert.equal(saved[1]["status"], "отклонить"); // была «отклонить»
  assert.equal(saved[0]["status"], "отклонить"); // была «принять»
  assert.equal(saved[3]["status"], "отклонить"); // была пустая
  assert.equal(saved[2]["applied"], true);       // применённая — нетронута
});

test("правки одного термина группируются: заголовок термина только у первой", async () => {
  REVIEW_CONTENT = JSON.stringify({
    created: "t",
    updated: "t",
    input: "ner.json",
    entries: [
      { term: "林凡", field: "type", old: "A", new: "B",
        status: "", applied: false },
      { term: "林凡", field: "translation", old: "X", new: "Y",
        status: "", applied: false },
      { term: "青云宗", field: "type", old: "C", new: "D",
        status: "", applied: false },
    ],
  }, null, 2);
  const page = await openReview();
  const rows = [];
  (function walk(n) {
    for (const c of n.children || []) walk(c);
    if ((n.className || "").split(/\s+/).includes("rv-row")) rows.push(n);
  })(page);
  assert.equal(rows.length, 3);
  // вторая правка 林凡 — продолжение группы, третья (другой термин) — нет
  assert.ok(!(rows[0].className || "").includes("rv-row-cont"));
  assert.ok((rows[1].className || "").includes("rv-row-cont"));
  assert.ok(!(rows[2].className || "").includes("rv-row-cont"));
  // заголовок: у первой «термин · поле», у продолжения — только поле
  const titleOf = (row) => row.children[0].children[0].children[0].textContent;
  assert.equal(titleOf(rows[0]), "林凡 · type");
  assert.equal(titleOf(rows[1]), "translation");
  assert.equal(titleOf(rows[2]), "青云宗 · type");
});

test("все статусы уже установлены — PUT не уходит", async () => {
  const page = await openReview();
  const btn = bulkBtn(page, "принять");
  assert.ok(btn);
  await btn._listeners["click"][0]();
  await new Promise((r) => setTimeout(r, 20));
  const before = putBodies.length;
  await btn._listeners["click"][0]();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(putBodies.length, before, "повторный клик не должен сохранять");
});

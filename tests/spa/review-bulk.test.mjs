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
/* общий DOM-слой: в vm он объявляет UIC (и h/iconEl) так же, как в
   браузере — классическим скриптом, до view-файлов */
const UIC_SRC = readFileSync(
  new URL("../../web/static/ui-components.js", import.meta.url),
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
    const self = this;
    this.classList = {
      toggle(c, force) {
        const s = new Set(self.className.split(/\s+/).filter(Boolean));
        const on = force === undefined ? !s.has(c) : !!force;
        if (on) s.add(c);
        else s.delete(c);
        self.className = [...s].join(" ");
        return on;
      },
      add: (c) => this.classList.toggle(c, true),
      remove: (c) => this.classList.toggle(c, false),
      contains: (c) => self.className.split(/\s+/).includes(c),
    };
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
/* память браузера по-настоящему: фильтры правок живут в состоянии книги,
   и тест обязан видеть, что они туда доехали */
globalThis.localStorage = (() => {
  const data = {};
  return {
    __data: data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    removeItem: (k) => { delete data[k]; },
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
vm.runInContext(UIC_SRC, sandbox); // globalThis.UIC, h, iconEl
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

/* собранный текст узла: в DOM-mock textContent не агрегируется */
function nodeText(n) {
  if (!n) return "";
  if (n.tagName === "#text") return n.textContent || "";
  return (n.textContent || "") +
    (n.children || []).map(nodeText).join("");
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

test("правки одного термина — одна карточка: строки полей внутри", async () => {
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
  // два термина — две карточки (а не три строки на три правки)
  assert.equal(rows.length, 2);
  const titleOf = (row) => nodeText(row.children[0].children[0]);
  assert.equal(titleOf(rows[0]), "林凡 · полей 2");
  assert.equal(titleOf(rows[1]), "青云宗 · полей 1");
  // внутри первой карточки — по строке на поле, с подписью поля
  const fieldsOf = (row) =>
    (row.children || []).filter((c) =>
      (c.className || "").split(/\s+/).includes("rv-field"));
  const names = (row) =>
    fieldsOf(row).map((f) => nodeText(f.children[0].children[0]));
  assert.deepEqual(names(rows[0]), ["Тип", "Перевод"]);
  assert.deepEqual(names(rows[1]), ["Тип"]);
});

test("«Принять все» в карточке термина: один PUT на всю группу", async () => {
  REVIEW_CONTENT = JSON.stringify({
    created: "t", updated: "t", input: "ner.json",
    entries: [
      { term: "林凡", field: "type", old: "A", new: "B", status: "принять" },
      { term: "林凡", field: "translation", old: "X", new: "Y", status: "" },
      { term: "林凡", field: "notes", old: "n", new: "m", status: "" },
    ],
  }, null, 2);
  const page = await openReview();
  const group = buttons(page).filter((b) =>
    (b._attrs["title"] || "").includes("林凡"));
  assert.ok(group.length >= 3, `кнопок группы: ${group.length}`);
  const acceptAll = group.find((b) =>
    (b._attrs["title"] || "").includes("статус «принять»"));
  assert.ok(acceptAll, "нет «Принять все» у карточки термина");
  await acceptAll._listeners["click"][0]();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(putBodies.length, 1, "три поля — один PUT");
  assert.deepEqual(putBodies[0]["entries"].map((e) => e.status),
    ["принять", "принять", "принять"]);
});

/* узлы по классу (в DOM-mock querySelector нет) */
function byClass(node, cls) {
  const out = [];
  (function walk(n) {
    if ((n.className || "").split(/\s+/).includes(cls)) out.push(n);
    for (const c of n.children || []) walk(c);
  })(node);
  return out;
}

test("фильтр по полю: чипс скрывает значение, «Сбросить» возвращает всё", async () => {
  REVIEW_CONTENT = JSON.stringify({
    created: "t", updated: "t", input: "ner.json",
    entries: [
      { term: "林凡", field: "type", old: "A", new: "B", status: "" },
      { term: "林凡", field: "translation", old: "X", new: "Y", status: "" },
      { term: "火", field: "", action: "удаление", old: "e", new: "",
        status: "" },
    ],
  }, null, 2);
  const page = await openReview();
  const panel = byClass(page, "rv-filters")[0];
  assert.ok(panel, "нет панели фильтров");
  const cardsOf = () => byClass(page, "rv-list")[0].children.length;
  assert.equal(cardsOf(), 2, "два термина — две карточки");
  const chips = byClass(panel, "rv-chip");
  // три значения поля: type, translation, удаление термина
  assert.equal(chips.length, 3);
  // все галочки стоят: фильтр — «видно всё» (в DOM-mock checked — атрибут)
  for (const c of chips) assert.equal(c.children[0]._attrs["checked"], "true");
  // снимаем «type» — карточка «林凡» остаётся только с translation
  const typeChip = chips[0];
  typeChip.children[0]._listeners["change"][0]();
  await new Promise((r) => setTimeout(r, 20));
  const rows = byClass(page, "rv-list")[0];
  assert.equal(rows.children.length, 2);
  assert.equal(byClass(rows.children[0], "rv-field").length, 1,
    "у «林凡» осталось одно поле");
  // состояние фильтра — в книге (переживает перезагрузку)
  const saved = JSON.parse(
    globalThis.localStorage.__data["reviewFilter:ACTIVE/Книга"]);
  assert.deepEqual(saved.field, ["type"]);
  // «Сбросить» — снова всё видно
  const reset = byClass(panel, "rv-filter-reset")[0];
  assert.ok(reset, "нет кнопки «Сбросить»");
  reset._listeners["click"][0]();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(byClass(page, "rv-list")[0].children.length, 2);
  assert.equal(byClass(page, "rv-filter-count").length, 0,
    "без фильтров счётчика «видно из» нет");
  assert.equal(byClass(byClass(page, "rv-filters")[0], "rv-chip-off").length, 0);
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

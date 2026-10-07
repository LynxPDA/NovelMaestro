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
    this._text = "";
    this.dataset = {};
    this._attrs = {};
    this._listeners = {};
  }
  /* как в DOM: не-узел (в т.ч. null) становится текстовым узлом — именно так
     в интерфейс попадает «выделено: 2nullnull», и именно так это видно тестам */
  append(...kids) {
    for (const k of kids.flat()) {
      const kid = k instanceof El ? k : textNode(String(k));
      kid._parent = this;
      this.children.push(kid);
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
  /* textContent в DOM заменяет детей: старая строка не должна оставаться
     текстовым узлом рядом с новой (иначе статусы «сдвигаются» ) */
  get textContent() { return this._text; }
  set textContent(v) {
    this._text = String(v);
    this.children = this.children.filter((c) => c.tagName !== "#TEXT");
  }
  appendChild(k) { this.children.push(k); }
  replaceChildren(...kids) { this.children = []; this.append(...kids); }
  addEventListener(ev, fn) {
    if (!this._listeners[ev]) this._listeners[ev] = [];
    this._listeners[ev].push(fn);
  }
  /* событие «ушли с вкладки» (setView шлёт pi-navigate тело вкладки) */
  dispatchEvent(ev) {
    for (const fn of this._listeners[ev.type] || []) fn({ target: this, type: ev.type });
    return true;
  }
  setAttribute(k, v) {
    this._attrs[k] = String(v);
    // чекбокс в DOM отмечен самим наличием атрибута, а h() его не ставит,
    // когда значение false — свойство должно идти за атрибутом
    if (k === "checked") this.checked = String(v) !== "false";
  }
  getAttribute(k) { return this._attrs[k] ?? null; }
  remove() {}
  removeChild() {}
  /* setView шлёт уходящей вкладке «pi-navigate» */
  dispatchEvent(ev) {
    for (const fn of this._listeners[ev.type] || []) fn({ type: ev.type, target: this });
    return true;
  }
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

class TextNode extends El {}
function textNode(t) {
  const n = new TextNode("#text");
  n.textContent = t;
  return n;
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
globalThis.CustomEvent = class {
  constructor(type, opts) {
    this.type = type;
    Object.assign(this, opts || {});
  }
};
globalThis.CustomEvent = class {
  constructor(type, opts) {
    this.type = type;
    Object.assign(this, opts || {});
  }
};
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
  if ((opts.method || "GET") === "GET" && globalThis.__gets) {
    // GET-вызовы тоже под наблюдением: вкладка «Поиск» их только читает
    globalThis.__gets.push({ path: p, query: path.split("?")[1] || "" });
  }
  if ((opts.method || "GET") !== "GET") {
    // запись запроса: тестам важно, каким телом ушёл перенос/переименование
    (globalThis.__calls || []).push({
      method: opts.method,
      path: p,
      query: path.split("?")[1] || "",
      body: opts.body || null,
    });
    /* предпросмотр запроса стадии: payload отдаёт тест */
    if (p.endsWith("/preview-request")) {
      return { ok: true, ...((globalThis.__preview) || { messages: [] }) };
    }
    return { ok: true, moved: [], skipped: [] };
  }
  if (p === "/files") {
    return {
      entries: globalThis.__files || [],
      dirs: globalThis.__dirs || [],
    };
  }
  if (p === "/file") return { content: "", missing: true, exists: false, size: 0 };
  if (p === "/ner") return globalThis.__ner || { items: [], too_large: false };
  if (p === "/check") return { reports: [] };
  if (p === "/ner/review" || p === "/translate_check_llm/review") {
    return { exists: false, content: "", size: 0 };
  }
  if (p.endsWith("/tree")) {
    return globalThis.__tree || { chapters: [], artifacts: {} };
  }
  if (p.endsWith("/status")) return { status: { chapters: {}, counts: {} } };
  if (p.endsWith("/chapters/titles")) return { titles: {} };
  if (p === "/search") {
    return globalThis.__search || { ...SEARCH_EMPTY, files: [], total: 0 };
  }
  /* роуты «Настроек»: текст файла API не отдаёт — только блоки реестра */
  if (p === "/settings") return SETTINGS_PAYLOAD;
  if (p === "/stages/compile/options") return { modes: [] };
  if (p === "/cover") return { files: [] };
  if (p === "/templates") return { templates: [] };
  if (p.startsWith("/prompts")) return globalThis.__prompts || { prompts: [], templates: [] };
  if (p.startsWith("/logs")) return globalThis.__logs || { logs: [], content: "" };
  if (p === "/ner/export") return { ok: true, content: "" };
  if (p === "/jobs") return { jobs: [] };
  if (p.startsWith("/jobs/")) return { job: {} };
  if (p === "/stages") {
    return { stages: STAGES, profiles: SETTINGS_PAYLOAD.profiles };
  }
  if (p.startsWith("/stages/")) {
    const key = p.split("/")[2];
    /* preview: true — стадия LLM, у неё есть «Предпросмотр запроса» */
    return { spec: { key, title: key, preview: true,
                     fields: SPEC_FIELDS[key] || [] },
             options: {} };
  }
  return { ok: true };
}

function makeEditor(initial, lang) {
  const ta = new El("textarea");
  ta.value = initial;
  ta.lang = lang || ""; // язык редактора — тестам проверять именно его
  return {
    root: ta, isCM: false,
    getValue: () => ta.value,
    setValue: (t) => { ta.value = t; },
    setLang(l) { ta.lang = l || ""; }, setReadOnly() {},
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
  // маршрут читает projectNavigate (вкладки проекта меняются состоянием)
  location: { hash: "" },
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
  CustomEvent: globalThis.CustomEvent,
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
  toggleMenu() {},
  /* диалог подтверждения из app.js: dangerous-действие требует слова-пароля */
  confirmModal(title, text, confirmWord, onConfirm) {
    const word = h("input", { class: "input", placeholder: confirmWord });
    const err = h("div", { class: "form-error" });
    const box = h(
      "div",
      { class: "modal" },
      h("div", { class: "modal-title" }, title),
      h("div", { class: "modal-text" }, text),
      word,
      err,
      h(
        "div",
        { class: "modal-actions" },
        h("button", { class: "btn btn-ghost" }, "Отмена"),
        h(
          "button",
          {
            class: "btn btn-danger",
            onclick: async () => {
              if (word.value.trim().toUpperCase() !== confirmWord) {
                err.textContent = `Введите слово ${confirmWord}`;
                return;
              }
              await onConfirm();
            },
          },
          confirmWord,
        ),
      ),
    );
    globalThis.document.body.append(h("div", { class: "modal-backdrop" }, box));
    return box;
  },
  mdPreviewSrcdoc: (html) => html,
  fitPreviewFrame() {},
  makeEditor,
  extOf: (n) => UICore.extOf(n),
  UICore,
};
vm.createContext(sandbox);
vm.runInContext(UIC_SRC, sandbox); // globalThis.UIC, h, iconEl
vm.runInContext(RUN_SRC, sandbox); // window.viewRun — вкладка «Запуски»
vm.runInContext(SRC, sandbox);
const renderProject = sandbox.viewProject;
/* Каждой проверке нужна книга с чистого листа: состояние вкладок предыдущей
   не должно перетекать в следующую (сортировку файлов и профили стадий тесты
   готовят сами — их не трогаем). reopenProject — повторное открытие БЕЗ
   очистки: так и проверяется, что вкладка помнить начала */
function viewProject(section, name, ...rest) {
  globalThis.localStorage.removeItem(`nmTab:${section}/${name}`);
  return renderProject(section, name, ...rest);
}
const reopenProject = renderProject;

/* все вкладки страницы проекта: viewProject(section, name, tab) */
const TABS = ["files", "run", "editor", "ner", "review", "chapters", "search",
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

test("индикатор запусков: клик ведёт на «Запуски», даже когда хэш уже «.../run»", async () => {
  /* вкладки проекта меняются состоянием и хэш остаётся тем, чем в проект
     вошли: ссылка pill'а ведёт на тот же хэш, hashchange не случается */
  sandbox.location.hash = "#/project/ACTIVE/Книга/run";
  const page = viewProject("ACTIVE", "Книга", "status");
  await new Promise((r) => setTimeout(r, 10));
  const active = () => collectText(page.querySelector(".tab-active")).join("");
  assert.equal(active(), "Статус", "проект открыт вкладкой «Статус»");
  sandbox.projectNavigate("ACTIVE/Книга", "run");
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(active(), "Запуски", "вкладка сменилась без смены хэша");
  assert.equal(sandbox.location.hash, "#/project/ACTIVE/Книга/run",
    "хэш менять не нужно: view тот же");
});

test("индикатор запусков: другой проект или экран — переход ссылкой", () => {
  sandbox.location.hash = "#/hub";
  sandbox.projectNavigate("HOLD/Другая", "run");
  assert.equal(sandbox.location.hash, "#/project/HOLD/Другая/run");
});

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
  /* выбор запомнен по книге (не был одним на интерфейс): повторное открытие —
     активна «tcl» */
  assert.deepEqual(
    JSON.parse(globalThis.localStorage.getItem("nmTab:ACTIVE/Книга")),
    { page: { view: "review" }, review: { tab: "tcl" } },
  );
  const page2 = reopenProject("ACTIVE", "Книга", "review");
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

/* ── «Поиск»: группы приходят с сервера, запрос — одним вызовом ── */
const SEARCH_CLUSTERS = [
  ["chapters", "Файлы глав"],
  ["other", "Прочее"],
];
/* зеркало реестра core/search.py: [id, подпись, кластер, чем открывается клик].
   метки групп глав — те же слаги стадий, что и в остальном интерфейсе;
   глоссарий — обычный файл, но клик по нему ведёт не в редактор */
const SEARCH_GROUPS = [
  ["chapter", "chapter", "chapters", "editor"],
  ["translated", "translated", "chapters", "editor"],
  ["redacted", "redacted", "chapters", "editor"],
  ["polished", "polished", "chapters", "editor"],
  ["ner", "ner.json", "other", "glossary"],
  ["notes", "Заметки книги", "other", "editor"],
];
const SEARCH_EMPTY = {
  ok: true,
  scopes: ["chapter", "polished", "ner", "notes"],
  clusters: SEARCH_CLUSTERS,
  groups: SEARCH_GROUPS,
  scanned: 0,
  skipped: 0,
};

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
  // каталог: чекбокс + одна кнопка; файл: чекбокс + править, скачать, переименовать
  assert.equal(findByClass(rows[0], "fsel").length, 1);
  assert.deepEqual(tips(rows[0]), ["Переименовать chapters"]);
  assert.deepEqual(tips(rows[1]),
    ["Править a.txt", "Скачать a.txt", "Переименовать a.txt"]);
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
/* ── «Поиск»: группы с сервера, один GET, постраничный вывод, редактор ── */
test("поиск: вкладка, поиск и сводка результатов", async () => {
  globalThis.__search = {
    ...SEARCH_EMPTY,
    scopes: ["chapter", "polished"],
    files: [
      {
        group: "chapter",
        path: "chapters/00000_1_Глава 1/chapter.txt",
        name: "chapter.txt",
        chapter: 1,
        count: 1,
        hits: [{ line: 2, start: 0, end: 3, text: "мир" }],
      },
      {
        group: "polished",
        path: "chapters/00000_1_Глава 1/polished.txt",
        name: "polished.txt",
        chapter: 1,
        count: 2,
        hits: [
          { line: 7, start: 6, end: 9, text: "тихий мир" },
          { line: 9, start: 0, end: 3, text: "мир!" },
        ],
      },
    ],
    total: 3,
    scanned: 9,
    skipped: 0,
  };
  globalThis.__gets = [];
  globalThis.localStorage.clear(); // настройки вкладки — с этого теста
  const page = viewProject("ACTIVE", "Книга", "search");
  await tick();
  /* кластеры и группы приходят из ответа сервера: реестр тут не дублируется */
  assert.deepEqual(
    findByClass(page, "search-cluster-label").map((n) => collectText(n).join("")),
    ["Файлы глав:", "Прочее:"],
  );
  const chips = findByClass(page, "search-scope-chip");
  assert.equal(chips.length, SEARCH_GROUPS.length, "чипсы — по одной на группу");
  assert.deepEqual(
    chips.map((n) => collectText(n).join("")).slice(0, 4),
    ["chapter", "translated", "redacted", "polished"],
    "метки групп глав — те же слаги стадий, что в остальном интерфейсе",
  );
  const boxes = findByClass(page, "search-scope");
  assert.equal(
    boxes.filter((b) => b.checked).length,
    2,
    "отмечены группы из серверного значения по умолчанию",
  );
  const ctx = findByClass(page, "search-ctx")[0];
  assert.equal(ctx.getAttribute("max"), "300", "контекст не бывает больше 300");
  assert.deepEqual(
    collectText(findByClass(page, "search-ctx-label")[0]),
    ["Контекст:"],
    "подпись контекста — с двоеточием",
  );
  console.log("DBG bodies:", page.children.length, JSON.stringify(page.children.map((c) => c.className)));
  const st1 = findByClass(page, "search-status");
  assert.equal(st1.length, 1, "сводка — одна строка");
  assert.deepEqual(collectText(st1[0]), ["Поиска не было"]);

  const input = findByClass(page, "search-q")[0];
  input.value = "мир";
  await findByClass(page, "search-run")[0]._listeners.click[0]();
  await tick();
  const call = globalThis.__gets.find(
    (c) => c.path === "/search" && new URLSearchParams(c.query).get("q"),
  );
  assert.ok(call, "поиск ушёл на GET /api/search");
  const q = new URLSearchParams(call.query);
  assert.equal(q.get("project"), "ACTIVE/Книга");
  assert.equal(q.get("q"), "мир");
  assert.equal(q.get("scope"), "chapter,polished");
  assert.equal(q.get("context"), "60");
  assert.notEqual(q.get("case"), "1", "регистр по умолчанию не учитывается");
  {
    const ss = findByClass(page, "search-status");
    console.log("DBG2 count=", ss.length);
    for (const n of ss) {
      const chain = [];
      let x = n;
      while (x) { chain.push(x.tagName + "." + (x.className || "-")); x = x._parent; }
      console.log("DBG2 node", JSON.stringify(n._text), "children", n.children.length,
        JSON.stringify(n.children.map((c) => c.tagName + ":" + c._text)), "chain", chain.join(" < "));
    }
  }
  assert.equal(
    collectText(findByClass(page, "search-status")[0]).join(","),
    "Совпадений: 3 · файлов: 2 · прочитано: 9",
    "сводка — отдельной строкой, без «показаны не все»",
  );
  /* строки результатов — в своём блоке; на одной странице пейджер молчит */
  const list = findByClass(page, "search-rows")[0];
  assert.equal(findByClass(page, "ner-pager").length, 1);
  assert.equal(findByClass(page, "ner-pager-info").length, 0,
    "на одной странице пейджер не показывает ничего лишнего");
  assert.deepEqual(
    list.children.map((n) => collectText(findByClass(n, "search-file-path")[0]).join("")),
    [
      "chapters/00000_1_Глава 1/chapter.txt",
      "chapters/00000_1_Глава 1/polished.txt",
    ],
  );
  const marks = findByClass(page, "search-hit-text").flatMap((n) =>
    findTag(n, "mark").map((m) => collectText(m).join("")),
  );
  assert.deepEqual(marks, ["мир", "мир", "мир"], "совпадения подсвечены");
  assert.deepEqual(
    findByClass(page, "search-hit-line").map((n) => collectText(n).join("")),
    ["2", "7", "9"],
    "номер строки рядом с фрагментом",
  );
});

test("поиск: на нескольких страницах пейджер считает файлы", async () => {
  globalThis.__search = {
    ...SEARCH_EMPTY,
    files: Array.from({ length: 30 }, (_, i) => ({
      group: "chapter",
      path: `chapters/00000_${i + 1}_Глава/chapter.txt`,
      name: "chapter.txt",
      chapter: i + 1,
      count: 1,
      hits: [{ line: 1, start: 0, end: 3, text: "мир" }],
    })),
    total: 30, scanned: 30, skipped: 0,
  };
  globalThis.localStorage.clear(); // настройки вкладки — с этого теста
  const page = viewProject("ACTIVE", "Книга", "search");
  await tick();
  const q = findByClass(page, "search-q")[0];
  q.value = "мир";
  await findByClass(page, "search-run")[0]._listeners.click[0]();
  await tick();
  assert.equal(findByClass(page, "search-rows")[0].children.length, 25,
    "страница — 25 файлов");
  const pager = findByClass(page, "ner-pager")[0];
  const info = findByClass(page, "ner-pager-info")[0];
  assert.deepEqual(collectText(info).join(","), " 1 / 2 · файлов с совпадениями 30 ");
  const next = pager.querySelectorAll("button")[1];
  await next._listeners.click[0]();
  assert.equal(findByClass(page, "search-rows")[0].children.length, 5,
    "вторая страница — остаток");
});

test("поиск: «Только главы» оставляет один кластер", async () => {
  globalThis.__search = { ...SEARCH_EMPTY, files: [], total: 0 };
  globalThis.localStorage.clear(); // настройки вкладки — с этого теста
  const page = viewProject("ACTIVE", "Книга", "search");
  await tick();
  const only = findByClass(page, "search-chips")[0].children
    .filter((b) => collectText(b).join("") === "Только главы")[0];
  await only._listeners.click[0]();
  assert.deepEqual(
    findByClass(page, "search-cluster-label").map((n) => collectText(n).join("")),
    ["Файлы глав:", "Прочее:"],
    "кластеры не прячутся: чипсы — это выбор охвата",
  );
  assert.deepEqual(
    findByClass(page, "search-scope-chip").map((n) => collectText(n).join("")),
    ["chapter", "translated", "redacted", "polished", "ner.json",
      "Заметки книги"],
  );
  assert.deepEqual(
    findByClass(page, "search-scope").map((b) => b.checked),
    [true, true, true, true, false, false],
    "отмечены только артефакты глав: глоссарий и заметки — вне этого выбора",
  );
  assert.deepEqual(
    JSON.parse(globalThis.localStorage.getItem("search:ACTIVE/Книга")).scopes,
    ["chapter", "translated", "redacted", "polished"],
    "охват помнится на проект",
  );
});

test("поиск: клик по файлу главы открывает вкладку «Редактор»", async () => {
  globalThis.__tree = {
    chapters: [{
      number: 1,
      dir: "00000_1_Глава 1",
      artifacts: { "chapter.txt": 12, "polished.txt": 34 },
    }],
  };
  globalThis.__search = {
    ...SEARCH_EMPTY,
    files: [{
      group: "polished",
      path: "chapters/00000_1_Глава 1/polished.txt",
      name: "polished.txt",
      chapter: 1,
      count: 1,
      hits: [{ line: 7, start: 6, end: 9, text: "тихий мир" }],
    }],
    total: 1, scanned: 3, skipped: 0,
  };
  globalThis.__gets = [];
  globalThis.localStorage.clear(); // настройки вкладки — с этого теста
  const page = viewProject("ACTIVE", "Книга", "search");
  await tick();
  await findByClass(page, "search-run")[0]._listeners.click[0]();
  await tick();
  const input = findByClass(page, "search-q")[0];
  input.value = "мир";
  await findByClass(page, "search-run")[0]._listeners.click[0]();
  await tick();
  const link = findByClass(page, "search-file-path")[0];
  assert.ok(link, "строка результата с именем файла");
  await link._listeners.click[0]();
  await tick();
  const call = (globalThis.__gets || [])
    .filter((c) => c.path === "/file")
    .slice(-1)[0];
  assert.ok(call, "клик по имени файла открыл редактор: "
    + (globalThis.__gets || []).map((c) => c.path + "?" + c.query).join(" "));
  assert.equal(new URLSearchParams(call.query).get("path"),
    "chapters/00000_1_Глава 1/polished.txt",
    "глава и артефакт взяты из пути результата");
  const sel = findByClass(page, "ed-type")[0];
  assert.equal(sel.value, "polished.txt", "панель открылась на файле результата");
  assert.deepEqual(collectText(findByClass(page, "ed-meta")[0]).join(","),
    "новый файл · polished.txt");
  /* запрос в панель передаёт openEditor: панель открывается заполненной */
  assert.match(SRC, /ed\.find = find \? \{ type: seg\[2\], q: String\(find\) \} : null/);
  assert.match(SRC, /openFind\(pInfo\.editor, f\.q\)/);
});

test("поиск: ner.json — файл, клик по нему открывает «Глоссарий» со всеми столбцами", async () => {
  globalThis.__ner = {
    items: [{ term: "мир", type: "person", translation: "мир", count: 5,
      note: "тихий" }],
    by_type: { person: 1 },
    too_large: false,
  };
  globalThis.__search = {
    ...SEARCH_EMPTY,
    files: [{
      group: "ner", path: "ner.json", name: "ner.json", chapter: null, count: 1,
      hits: [{ line: 3, start: 0, end: 3, text: "мир" }],
    }],
    total: 1,
    scanned: 4,
    skipped: 0,
  };
  globalThis.localStorage.clear(); // настройки вкладок — с чистого листа
  const page = viewProject("ACTIVE", "Книга", "search");
  await tick();
  // пояснений и отдельной кнопки поиска по глоссарию на вкладке нет
  assert.equal(findByClass(page, "search-ner-btn").length, 0);
  assert.equal(findByClass(page, "card-hint").length, 0);
  const input = findByClass(page, "search-q")[0];
  input.value = "мир";
  await findByClass(page, "search-run")[0]._listeners.click[0]();
  await tick();
  const path = findByClass(page, "search-file-path")[0];
  assert.equal(collectText(path).join(""), "ner.json");
  assert.ok(String(path.getAttribute("title")).includes("Глоссарий"),
    "подсказка обещает глоссарий, а не редактор");
  await path._listeners.click[0]();
  await tick();
  assert.ok(findByClass(page, "ner-search")[0], "открылась вкладка глоссария");
  assert.equal(findByClass(page, "ner-search")[0].querySelectorAll("input")[0].value,
    "мир", "глоссарий открылся с уже подставленным запросом");
  // все столбцы: term/type/translation + count/note, а не три по умолчанию
  assert.equal(findByClass(page, "ner-th").length, 5,
    "из поиска таблица показывается все ключи записи");
});

/* ── «Глоссарий»: поиск по видимым столбцам, панель «⋮», выделение ── */
const NER_SEED = [
  { term: "мир", type: "person", translation: "мир", count: 5, note: "тихий" },
  { term: "Лина", type: "place", translation: "Лина", count: 3 },
  { term: "дракон", type: "thing", translation: "дракон", count: 1, _locked: true },
];

function nerPage(items, prefs) {
  globalThis.localStorage.clear();
  globalThis.__ner = {
    items,
    by_type: items.reduce((acc, it) => {
      acc[it.type] = (acc[it.type] || 0) + 1;
      return acc;
    }, {}),
    too_large: false,
  };
  globalThis.__calls = [];
  for (const [k, v] of Object.entries(prefs || {})) {
    globalThis.localStorage.setItem(k, JSON.stringify(v));
  }
  return viewProject("ACTIVE", "Книга", "ner");
}
const nerRows = (page) => findByClass(page, "ner-row");
const nerHeads = (page) => findByClass(page, "ner-th-btn").map((n) => collectText(n).join(""));
const nerSearch = (page) => findByClass(page, "ner-search")[0].querySelectorAll("input")[0];
const nerFirstCol = (r) => collectText(r).join(" ").split(" ")[0];

test("глоссарий: по умолчанию ищется по отображаемым столбцам", async () => {
  const page = await nerPage(NER_SEED.map((it) => ({ ...it })));
  await tick();
  assert.deepEqual(nerHeads(page), ["term", "type", "translation"]);
  const search = nerSearch(page);
  /* «note» — скрытый столбец: в нём поиск не работает */
  search.value = "тихий";
  await search._listeners.input[0]();
  assert.equal(nerRows(page).length, 0, "совпадения в скрытом столбце не ищутся");
  search.value = "лина";
  await search._listeners.input[0]();
  assert.deepEqual(nerRows(page).map(nerFirstCol), ["Лина"]);
});

test("глоссарий: поля поиска помнятся и ищут по скрытым столбцам", async () => {
  const page = await nerPage(NER_SEED.map((it) => ({ ...it })), {
    "nerSearch:ACTIVE/Книга": ["term", "note"],
  });
  await tick();
  assert.deepEqual(
    collectText(findByClass(page, "ner-fields-btn")[0]).join(","),
    "Поля поиска (2)",
  );
  const search = nerSearch(page);
  search.value = "тихий";
  await search._listeners.input[0]();
  assert.deepEqual(nerRows(page).map(nerFirstCol), ["мир"],
    "скрытый столбец из сохранённого набора снова участвует в поиске");
});

test("глоссарий: всё управление спрятано в одну кнопку «⋮»", async () => {
  const page = await nerPage(NER_SEED.map((it) => ({ ...it })));
  await tick();
  const toolbar = findByClass(page, "files-toolbar")[0];
  assert.deepEqual(
    toolbar.children.map((n) => n.className),
    ["ner-search", "spacer", "files-tools", "files-sel hidden"],
    "в тулбаре остаются только поиск, экспорт и панель выделения",
  );
  assert.deepEqual(
    findByClass(page, "files-tools")[0].children.map((n) => n.className),
    ["toolbar-menu", "btn btn-sm btn-ghost"],
    "из кнопок тулбара остаётся только экспорт",
  );
  const box = findByClass(page, "menu-box")[0];
  assert.deepEqual(
    box.children.map((c) => collectText(c).join("")),
    [
      " только зафиксированные",
      "Столбцы (3)",
      "Типы: все",
      "Поля поиска: отображаемые",
      "",
      "Добавить столбец",
      "Добавить термин",
      "",
      "Удалить столбец",
      "Удалить по фильтру",
    ],
  );
  assert.equal(
    box.children.filter((c) => c.className.includes("menu-sep")).length,
    2,
    "показ, добавление и удаление разделены",
  );
  /* замок — чекбокс: его состояние видно внутри меню */
  assert.equal(findByClass(page, "ner-lock")[0].querySelectorAll("input")[0].checked, false);
});

test("глоссарий: чекбоксы строк, групповой замок и «_locked» не столбец", async () => {
  const items = NER_SEED.map((it) => ({ ...it }));
  const page = await nerPage(items);
  await tick();
  assert.equal(nerRows(page).length, 3);
  assert.deepEqual(nerHeads(page), ["term", "type", "translation"],
    "замок не виден столбцом — он действие в строке");
  const rows = nerRows(page);
  for (const i of [0, 1]) {
    const cb = findByClass(rows[i], "fsel")[0];
    cb.checked = true;
    await cb._listeners.change[0]();
  }
  const selBar = findByClass(page, "files-sel")[0];
  assert.ok(!selBar.className.includes("hidden"), "панель выделения показалась");
  assert.deepEqual(collectText(findByClass(selBar, "files-sel-count")[0]).join(","),
    "выделено: 2");
  assert.ok(findByClass(page, "files-tools")[0].className.includes("hidden"),
    "панель заменяет кнопки тулбара");
  assert.deepEqual(
    selBar.querySelectorAll("button").map((b) => b.getAttribute("aria-label")),
    [
      "Зафиксировать выделенные",
      "Снять замок с выделенных",
      "Удалить выделенные",
      "Снять выделение",
    ],
  );
  const lockBtn = selBar.querySelectorAll("button")[0];
  await lockBtn._listeners.click[0]({ currentTarget: lockBtn });
  assert.deepEqual(
    globalThis.__ner.items.map((it) => it._locked === true),
    [true, true, true],
    "замок поставился на выделенное, зафиксированный остался зафиксированным",
  );
  const saved = globalThis.__calls.filter((c) => c.path === "/ner").slice(-1)[0];
  assert.equal(saved.method, "PUT");
  assert.equal(saved.body.items.filter((it) => it._locked === true).length, 3);
  assert.equal(saved.body.items.filter((it) => it.__new != null).length, 0,
    "служебный ключ __new в файл не пишется");
});

test("глоссарий: удаление выделенного обходит зафиксированные", async () => {
  const items = NER_SEED.map((it) => ({ ...it }));
  const page = await nerPage(items);
  await tick();
  for (const row of nerRows(page)) {
    const cb = findByClass(row, "fsel")[0];
    cb.checked = true;
    await cb._listeners.change[0]();
  }
  const selBar = findByClass(page, "files-sel")[0];
  assert.deepEqual(collectText(findByClass(selBar, "files-sel-count")[0]).join(","),
    "выделено: 3");
  const del = selBar.querySelectorAll("button")
    .find((b) => (b.getAttribute("aria-label") || "").startsWith("Удалить"));
  await del._listeners.click[0]({ currentTarget: del });
  const bd = globalThis.document.body.children
    .filter((el) => (el.className || "").split(/\s+/).includes("modal-backdrop"))
    .slice(-1)[0];
  assert.ok(bd, "диалог удаления открылся");
  const text = collectText(bd).join(" ");
  assert.match(text, /Будет удалено записей: 2/, "зафиксированная не считается");
  assert.match(text, /зафиксированных пропущено: 1/);
  const word = findTag(bd, "input")[0];
  assert.equal(word.getAttribute("placeholder"), "УДАЛИТЬ");
  word.value = "УДАЛИТЬ";
  await findByClass(bd, "btn-danger")[0]._listeners.click[0]();
  await tick();
  const saved = globalThis.__calls.filter((c) => c.path === "/ner").slice(-1)[0];
  assert.deepEqual(saved.body.items.map((it) => it.term), ["дракон"],
    "зафиксированная запись выжила");
  assert.deepEqual(nerRows(page).map(nerFirstCol), ["дракон"]);
});

/* ── редакторы промптов и предпросмотр запроса ─────────────────── */

test("редакторы: у промпта язык по выбору (plain text), не язык промптов", async () => {
  globalThis.localStorage.clear();
  const prompts = viewProject("ACTIVE", "Книга", "prompts");
  await tick();
  const ped = findTag(prompts, "textarea").filter((t) => "lang" in t);
  assert.equal(ped.length, 1, "на вкладке один редактор промпта");
  // отдельного «языка промптов» больше нет: у промптов plain text, а их
  // разметку (<translate>, {ner_block}) показывает предпросмотр запроса
  assert.equal(ped[0].lang, "text", "промту назначен plain text");

  globalThis.__tree = {
    chapters: [{
      number: 1,
      dir: "00000_1_Глава 1",
      artifacts: { "polished.txt": 34 },
    }],
  };
  const editor = viewProject("ACTIVE", "Книга", "editor");
  await tick();
  const eed = findTag(editor, "textarea").filter((t) => "lang" in t);
  assert.ok(eed.length >= 1, "редактор главы создан");
  assert.deepEqual([...new Set(eed.map((t) => t.lang))], ["txt"],
    "файл главы остаётся обычным текстом");
});

test("предпросмотр запроса: токены рядом с символами, промпт размечен", async () => {
  globalThis.localStorage.clear();
  globalThis.__preview = {
    stage: "Глоссарий",
    label: "Pass1 · чанк 1/1",
    model: "gemma/test",
    messages: [
      { role: "system", content: "<system>\nТы переводчик.\n</system>" },
      { role: "user", content: "<pass1>\n{ner_block}\n{original_text}\n" },
    ],
    chars: { system: 25, user: 30, total: 55 },
    tokens: { system: 7, user: 9, total: 16 },
    meta: { главы: "1-3", размер: 1200 },
  };
  const page = await runStage("ner");
  const btn = findTag(page, "button")
    .find((b) => collectText(b).join("").includes("Предпросмотр запроса"));
  assert.ok(btn, "кнопка «Предпросмотр запроса» у LLM-стадии");
  await btn._listeners.click[0]();
  await tick();
  const body = globalThis.document.body;
  const heads = findByClass(body, "preview-req-head")
    .map((n) => collectText(n).join(""));
  assert.ok(heads.some((t) => t.includes("модель: gemma/test")
    && t.includes("запросов: 1")), heads.join(" || "));
  assert.ok(heads.some((t) => t.includes("главы: 1-3")
    && t.includes("размер: 1200")), "meta запроса не показана");
  const roles = findByClass(body, "preview-req-role")
    .map((n) => collectText(n).join(""));
  assert.ok(
    roles.some((t) => /символов: user 30, system 25 \(всего 55\)/.test(t)
      && /токенов ~16/.test(t)),
    `сводка запроса: ${roles.join(" || ")}`,
  );
  const pres = findByClass(body, "preview-req-text");
  assert.equal(pres.length, 2, "по <pre> на сообщение");
  // текст не теряется и не портится разметкой
  assert.equal(collectText(pres[1]).join(""),
    "<pass1>\n{ner_block}\n{original_text}\n");
  const marks = [];
  pres.forEach((pre) => findTag(pre, "span").forEach((sp) => marks
    .push(`${sp.className}=${collectText(sp).join("")}`)));
  assert.deepEqual(marks, [
    "pv-tag=<system>", "pv-tag=</system>", "pv-tag=<pass1>",
    "pv-var={ner_block}", "pv-var={original_text}",
  ]);
});

/* ── состояние вкладок проекта: один ключ на книгу ────────────────────────
 * nmTab:<раздел>/<книга> хранит последнее состояние вкладок: открытую вкладку,
 * главу и панели «Редактора», тип файлов «Глав», открытый промпт и лог.
 * Ссылка с конкретной вкладкой старше памяти; другая книга не наследует. */

const prefsOf = (section = "ACTIVE", name = "Книга") =>
  JSON.parse(globalThis.localStorage.getItem(`nmTab:${section}/${name}`) || "{}");
const allText = (n) => collectText(n).join("");

const EDITOR_TREE = {
  chapters: [
    { dir: "00000_1_Глава 1", artifacts: { "chapter.txt": "1", "translated.txt": "1" } },
    { dir: "00000_2_Глава 2", artifacts: { "chapter.txt": "2", "polished.txt": "2" } },
  ],
};

test("состояние: книга открывается на последней вкладке", async () => {
  viewProject("ACTIVE", "Книга", "logs");
  await tick();
  assert.deepEqual(prefsOf().page, { view: "logs" });
  /* ссылка без вкладки — возвращает последнюю */
  const again = reopenProject("ACTIVE", "Книга");
  await tick();
  assert.equal(collectText(again.querySelector(".tab-active")).join(""), "Логи");
  /* ссылка с вкладкой — старше памяти */
  const routed = reopenProject("ACTIVE", "Книга", "chapters");
  await tick();
  assert.equal(collectText(routed.querySelector(".tab-active")).join(""), "Главы");
  /* другая книга памяти не наследует */
  const other = viewProject("HOLD", "Другая");
  await tick();
  assert.equal(collectText(other.querySelector(".tab-active")).join(""), "Файлы");
});

test("состояние «Редактора»: глава, режим и подсветка возвращаются", async () => {
  globalThis.__tree = EDITOR_TREE;
  const page = viewProject("ACTIVE", "Книга", "editor");
  await tick();
  assert.equal(findByClass(page, "ed-chapter")[0].value, "00000_1_Глава 1");
  assert.equal(findByClass(page, "ed-pane").length, 2, "по умолчанию две панели");
  const chapter = findByClass(page, "ed-chapter")[0];
  chapter.value = "00000_2_Глава 2";
  await chapter._listeners.change[0]();
  const btns = findByClass(page, "btn-ghost");
  const modeBtn = btns.find((b) => allText(b).includes("файла"));
  const hlBtn = btns.find((b) => allText(b).includes("Подсветка"));
  await modeBtn._listeners.click[0]();
  await hlBtn._listeners.click[0]();
  await tick();
  assert.deepEqual(prefsOf().editor, {
    chapter: "00000_2_Глава 2", mode: "one", hl: false, ngram: 3, threshold: 0.75,
    left: "chapter.txt", right: "polished.txt",
  });
  /* повторное открытие — та же глава и одна панель */
  const again = reopenProject("ACTIVE", "Книга", "editor");
  await tick();
  assert.equal(findByClass(again, "ed-chapter")[0].value, "00000_2_Глава 2");
  assert.equal(findByClass(again, "ed-pane").length, 1, "режим «один файл» помнится");
  delete globalThis.__tree;
});

test("состояние «Глав»: тип файлов возвращается", async () => {
  const page = viewProject("ACTIVE", "Книга", "chapters");
  await tick();
  const sel = findByClass(page, "chapters-type")[0];
  assert.equal(sel.value, "polished", "дефолт — полировка");
  sel.value = "translated";
  await sel._listeners.change[0]();
  await tick();
  assert.deepEqual(prefsOf().chapters, { type: "translated" });
  const again = reopenProject("ACTIVE", "Книга", "chapters");
  await tick();
  assert.equal(findByClass(again, "chapters-type")[0].value, "translated");
});

test("состояние «Промптов»: открытый файл возвращается", async () => {
  globalThis.__prompts = {
    prompts: [{ name: "pipeline_prompt.txt", size: 24 }], content: "<system>x</system>",
  };
  const page = viewProject("ACTIVE", "Книга", "prompts");
  await tick();
  // первый вход: память пуста — файл открываем кликом, он и запомнится
  assert.equal(allText(findByClass(page, "prompt-name")[0]).trim(), "");
  await findByClass(page, "prompt-item")[0]._listeners.click[0]();
  await tick();
  assert.deepEqual(prefsOf().prompts, { file: "pipeline_prompt.txt" });
  /* повторное открытие — файл на редакторе сразу, без клика */
  const again = reopenProject("ACTIVE", "Книга", "prompts");
  await tick();
  assert.equal(allText(findByClass(again, "prompt-name")[0]).trim(),
    "pipeline_prompt.txt");
  assert.equal(findByClass(again, "editor-cm").length, 1);
  delete globalThis.__prompts;
});

test("состояние «Логов»: папка и открытый файл возвращаются", async () => {
  globalThis.__logs = {
    logs: [
      { name: "ner.log", path: "ner.log", size: 7, mtime: 90 },
      { name: "pipeline.log", path: "chapters/pipeline.log", size: 5, mtime: 100 },
    ],
    content: "хвост лога", size: 5,
  };
  const page = viewProject("ACTIVE", "Книга", "logs");
  await tick();
  const dir = findByClass(page, "prompt-item").find((b) => allText(b).includes("chapters/"));
  await dir._listeners.click[0]();
  await tick();
  const page2 = findByClass(page, "prompt-item").find((b) => allText(b).includes("pipeline.log"));
  await page2._listeners.click[0]();
  await tick();
  assert.deepEqual(prefsOf().logs, { path: "chapters", file: "pipeline.log" });
  /* повторное открытие — та же папка и тот же лог */
  const again = reopenProject("ACTIVE", "Книга", "logs");
  await tick();
  assert.ok(allText(again.querySelector(".crumbs")).includes("chapters"), "хлебные крошки");
  assert.equal(findByClass(again, "log-view")[0].textContent, "хвост лога");
  delete globalThis.__logs;
});

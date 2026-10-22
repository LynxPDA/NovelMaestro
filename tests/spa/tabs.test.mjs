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
/* один простой селектор («.ch-cell:nth-child(2)», «tr», [data-id="1"]):
   составной разбивается по пробелам, каждая часть проверяется отдельно */
function matchPart(node, part) {
  for (const bit of part.match(/\[[^\]]+\]|:[a-z-]+\([^)]*\)|[.\w-]+/gi) || []) {
    if (bit.startsWith("[")) {
      const m = /^\[([^=\]]+)="?([^"\]]*)"?\]$/.exec(bit);
      if (!m || String(node.getAttribute(m[1])) !== m[2]) return false;
    } else if (bit.startsWith(":")) {
      const m = /^:nth-child\((\d+)\)$/.exec(bit);
      const kids = node._parent ? node._parent.children : [];
      if (m && kids.indexOf(node) + 1 !== Number(m[1])) return false;
    } else if (bit.startsWith(".")) {
      if (!(node.className || "").split(/\s+/).includes(bit.slice(1))) {
        return false;
      }
    } else if (node.tagName !== bit.toUpperCase()) return false;
  }
  return true;
}

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
    /* геометрия прокрутки: панель лога решает «дописывать с прокруткой или
       нет» по scrollTop/scrollHeight, тестам нужен предсказуемый ответ */
    this.scrollTop = 0;
    this.scrollHeight = 1000;
    this.clientHeight = 300;
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
    /* сколько раз узлу ПЕРЕПИСЫВАЛИ текст: тестам живой панели и таблицы
       важно не только «что показано», но и «сколько узлов тронул поток» */
    this._writes = (this._writes || 0) + 1;
    this.children = this.children.filter((c) => c.tagName !== "#TEXT");
  }
  appendChild(k) {
    k._parent = this;
    this.children.push(k);
  }
  replaceChildren(...kids) { this.children = []; this.append(...kids); }
  addEventListener(ev, fn) {
    if (!this._listeners[ev]) this._listeners[ev] = [];
    this._listeners[ev].push(fn);
  }
  /* событие «ушли с вкладки» (setView шлёт pi-navigate тело вкладки):
   { ...ev } — слушателям видны все свойства (bubbles и т.п.); bubbling —
   до document, там run-views гасит SSE-стрим */
  dispatchEvent(ev) {
    const e = { ...ev, target: this };
    for (const fn of this._listeners[ev.type] || []) fn(e);
    if (ev.bubbles) for (const fn of globalThis.document._listeners[ev.type] || []) fn(e);
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
  /* запрос по всему поддереву: обработчикам запусков нужен настоящий
     результат, а не null. Живая таблица глав ищет клетку составным
     селектором («.ch-row[data-id="1"] .ch-cell:nth-child(2)»), поэтому
     поддержаны и атрибут, и позиция среди соседей */
  querySelectorAll(sel) {
    const parts = String(sel).trim().split(/\s+/);
    const out = [];
    const walk = (n) => {
      for (const c of n.children || []) {
        if (matchPart(c, parts[parts.length - 1])
            && parts.slice(0, -1).every((p) => {
              for (let a = c._parent; a; a = a._parent) {
                if (matchPart(a, p)) return true;
              }
              return false;
            })) out.push(c);
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
  /* options — HTMLOptionsCollection: пересборка селекта исходника (epub)
     перечитывает опции именно так (`[...sel.options]`) */
  get options() {
    return this.children.filter((c) => c.tagName === "OPTION");
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
  /* document-слушатели — реальные: run-views гасит SSE-стрим
     по pi-navigate с bubble (всплывшее событие вызывает их в dispatchEvent) */
  _listeners: {},
  addEventListener(ev, fn) {
    (this._listeners[ev] = this._listeners[ev] || []).push(fn);
  },
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
    clear: () => {
      m.clear();
      if (globalThis.__books) globalThis.__books.clear(); // снимок — часть памяти
    },
  };
})();
/* снимок состояния книги для тестов: localStorage в роли сервера (записи
   видны сразу), без сети — так же, как у вкладки до появления снимка */
globalThis.__bookState = (section, name) => {
  const key = `${section}/${name}`;
  if (!globalThis.__books) globalThis.__books = new Map();
  if (!globalThis.__books.has(key)) {
    const carrier = UICore.bookState(null, section, name);
    carrier.load();
    globalThis.__books.set(key, carrier);
  }
  return globalThis.__books.get(key);
};
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
    // тестам важно, каким телом ушёл перенос или переименование записи
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
    /* проверка промпт-файла: вердикт скрипта стадии отдаёт тест */
    if (p.endsWith("/prompt-check")) {
      return { ok: true, result: globalThis.__promptCheck || null };
    }
    return { ok: true, moved: [], skipped: [] };
  }
  if (p === "/files") {
    return {
      entries: globalThis.__files || [],
      dirs: globalThis.__dirs || [],
    };
  }
  /* история точек: как на сервере — страница points + total всех точек;
     mock уважает offset и размер страницы из __historyPage */
  if (p === "/history") {
    const all = globalThis.__history || [];
    const q = new URLSearchParams(path.split("?")[1] || "");
    const from = Number(q.get("offset") || 0);
    const size = globalThis.__historyPage || all.length;
    return { checkpoints: all.slice(from, from + size), total: all.length };
  }
  if (p === "/history/diff") {
    return globalThis.__historyDiff || { files: [], total_files: 0 };
  }
  if (p === "/history/patch") {
    return globalThis.__historyPatch || { patch: "", adds: 0, dels: 0 };
  }
  if (p === "/file") return { content: "", missing: true, exists: false, size: 0 };
  if (p === "/ner") return globalThis.__ner || { items: [], too_large: false };
  if (p === "/check") return { reports: [] };
  if (p === "/ner/review" || p === "/translate_check_llm/review") {
    return globalThis.__review || { exists: false, content: "", size: 0 };
  }
  if (p.endsWith("/tree")) {
    return globalThis.__tree || { chapters: [], artifacts: {} };
  }
  if (p.endsWith("/status")) {
    return { status: globalThis.__status
      || { chapters: {}, counts: {}, missing: {} } };
  }
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
  /* текущий запуск вкладки «Запуски»: тест готовит globalThis.__job */
  if (p.startsWith("/jobs/")) return globalThis.__job || { job: {} };
  if (p === "/stages") {
    return { stages: STAGES, profiles: SETTINGS_PAYLOAD.profiles };
  }
  if (p.startsWith("/stages/")) {
    const key = p.split("/")[2];
    /* preview: true — стадия LLM, у неё есть «Предпросмотр запроса» */
    /* спека без key — как у сервера (spec_for отдаёт title/script/fields),
       название стадии то же, что в /stages: по нему и ищется карточка */
    const meta = STAGES.find((s) => s.key === key) || {};
    return { spec: { title: meta.title || key, preview: true,
                     fields: SPEC_FIELDS[key] || [] },
             options: globalThis.__stageOptions || {} };
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
  { key: "ner_check", title: "Проверка глоссария (LLM)",
    script: "cli/ner_check.py" },
  { key: "pipeline", title: "Перевод", script: "web/pipeline.py" },
  { key: "wiki", title: "Wiki", script: "cli/wiki.py" },
  { key: "epub", title: "Разбор исходника на главы",
    script: "cli/epub_to_chapters.py" },
  /* проверка перевода (LLM): у стадии тоже есть диапазон глав */
  { key: "translate_check_llm", title: "Проверка перевода (LLM)",
    script: "cli/translate_check_llm.py" },
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
    { name: "prompt_file", label: "Файл промпта", type: "files",
      dir: "prompts", ext: [".txt"], default: "", autofile: "ner_prompt.txt",
      help: "Файл промпта глоссария" },
    { name: "chunk_size", label: "Размер чанка, ТОКЕНЫ", type: "number",
      default: "8000" },
    { name: "start", label: "Начальная глава", type: "number", default: "" },
    { name: "end", label: "Конечная глава", type: "number", default: "" },
  ],
  pipeline: [
    { ...PROFILE_FIELD },
    { name: "action", label: "Тип работы", type: "select", default: "all",
      options: ["all"], labels: { all: "полный цикл" } },
  ],
  /* проверка перевода (LLM): диапазон глав + поля проверки */
  translate_check_llm: [
    { ...PROFILE_FIELD },
    { name: "check_type", label: "Тип файлов", type: "select",
      default: "polished", options: ["polished", "redacted"] },
    { name: "start", label: "Начальная глава", type: "number", default: "" },
    { name: "end", label: "Конечная глава", type: "number", default: "" },
  ],
  /* ner_check: режимы одним select, типы и поля записи — скрытые поля
     чипсов (noenv: значение живёт в браузере проекта+стадии) */
  ner_check: [
    { ...PROFILE_FIELD },
    { name: "passes", label: "Режимы", type: "select", default: "whole",
      options: ["whole", "types", "rag"],
      labels: {
        whole: "Выбранные типы (одновременно)",
        types: "Выбранные типы (по отдельности)",
        rag: "Точечно по списку (RAG)",
      } },
    { name: "count_threshold", label: "Порог count", type: "number",
      default: "0", when: [["passes", ["whole", "types"]]] },
    { name: "types", type: "hidden", default: "", noenv: true,
      when: [["passes", ["whole", "types"]]] },
    { name: "fields", type: "hidden", default: "", noenv: true },
  ],
  /* epub: стадия без LLM и без диапазона глав — поля только из реестра;
     «Исходник» — files из source/ с автоподхватом маской «source/*.epub» */
  epub: [
    { name: "input", label: "Исходник", type: "files", dir: "source",
      ext: [".epub", ".txt"], default: "", autofile: ["source/*.epub"],
      help: "epub или txt из source/" },
    { name: "mode", label: "Режим разбивки", type: "select", default: "toc",
      options: ["toc", "regex", "chunk"],
      labels: { toc: "По TOC (epub)", regex: "Ручной (regexp)",
                chunk: "По чанкам" } },
    { name: "split_patterns", label: "Паттерны разбивки", type: "textarea",
      default: "", rows: 4, when: [["mode", ["regex"]]] },
    { name: "clean_text", label: "Предварительная очистка текста глав",
      type: "bool", default: true },
    { name: "clean_output", label: "Очистить папки глав перед записью",
      type: "bool", default: true },
  ],
  /* wiki: тип файлов ОДИН на вход и на вики-главу (when_any), чипсы типов
     скрытым полем types — счётчики считаются срезом top-N по count */
  wiki: [
    { ...PROFILE_FIELD },
    { name: "source", label: "Источник текста", type: "select",
      default: "chapters", options: ["txt", "chapters"],
      labels: { txt: "Готовый txt", chapters: "Собрать из глав" } },
    { name: "type", label: "Тип файлов", type: "select", default: "polished",
      options: ["polished", "chapter", "translated", "redacted"],
      when_any: [["source", ["chapters"]], ["as_chapter", ["1"]]] },
    { name: "output", label: "Выходной файл", type: "text",
      default: "wiki.md", when: [["as_chapter", ["0"]]] },
    { name: "as_chapter", label: "Сохранить как главу", type: "bool",
      default: false },
    { name: "top", label: "Макс. терминов", type: "number", default: "80" },
    { name: "min_count", label: "Мин. частота термина", type: "number",
      default: "2" },
    { name: "types", type: "hidden", default: "", noenv: true },
    { name: "fields", type: "hidden", default: "", noenv: true },
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
  /* SSE-стрим: каждый кадр — своей записью сокета (так честнее: панель
     живой стадии обязана пережить и порционный вывод), с паузой между
     записями, чтобы тест успел поставить шпионы на узлы первого рендера */
  TextDecoder,
  TextEncoder,
  AbortController,
  fetch: async () => {
    const frames = (globalThis.__sse || [])
      .map((o) => `data: ${JSON.stringify(o)}\n\n`);
    let i = 0;
    return {
      ok: true, status: 200,
      body: {
        getReader: () => ({
          async read() {
            if (i >= frames.length) return { done: true };
            // пауза между кадрами: тесты больших логов обнуляют её
            // (__sseDelay = 0), иначе тысячи кадров займут десятки секунд
            await new Promise((r) => setTimeout(r, globalThis.__sseDelay ?? 15));
            const value = new TextEncoder().encode(frames[i++]);
            return { done: false, value };
          },
          cancel() {},
        }),
      },
    };
  },
  /* vm-код (project-views) резолвит document/window/localStorage/Node
     в СВОЁМ контексте — кладём те же моки и в sandbox */
  document: globalThis.document,
  window: globalThis.window,
  localStorage: globalThis.localStorage,
  /* состояние книги — тот же снимок, что в браузере, но с localStorage в
     роли запасного пути: тесты видят ключи (`nmTab:…`, `runVals:…`) там же,
     где их видели раньше, и могут их чистить */
  bookState: (section, name) => globalThis.__bookState(section, name),
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
  if (globalThis.__books) globalThis.__books.delete(`${section}/${name}`);
  globalThis.localStorage.removeItem(`nmTab:${section}/${name}`);
  globalThis.localStorage.removeItem(`nmStateMigrated:${section}/${name}`);
  return renderProject(section, name, ...rest);
}
const reopenProject = renderProject;

/* все вкладки страницы проекта: viewProject(section, name, tab) */
const TABS = ["files", "run", "editor", "ner", "review", "chapters", "search",
              "status", "config", "prompts", "logs", "notes", "history"];

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

test("смена вкладки гасит SSE-стрим: pi-navigate доходит до document (bubbles)", async () => {
  /* регресс: событие без bubble не доходит до document-слушателя run-views —
   стрим живёт, соединения копятся, новый лог остаётся «(лог пуст)» */
  const page = viewProject("ACTIVE", "Книга", "run");
  await new Promise((r) => setTimeout(r, 10)); // вкладка смонтировалась
  const seen = [];
  globalThis.document.addEventListener("pi-navigate", (ev) => seen.push(ev));
  const other = page.querySelectorAll(".tab")
    .find((t) => !String(t.className).includes("tab-active"));
  await other._listeners.click[0]();
  assert.equal(seen.length, 1, "ушедшая вкладка сообщила о pi-navigate");
  assert.equal(seen[0].bubbles, true,
    "событие всплывает до document — иначе слушатель run-views не погасит стрим");
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

/* ── карточка LLM-правок: один режим, файл правок — ссылкой ─────────────
 * У карточки один режим (список), а кнопок «Пробный прогон», «Применить» и
 * «Сохранить файл» внизу больше нет: каждое действие над записью сохраняет
 * файл само, применение запускается кнопкой «Применить» из сводки, а JSON
 * целиком правится на «Файлах» — туда ведёт и строка о файле правок. */
const REVIEW_DOC = {
  created: "t", updated: "t", input: "ner.json",
  entries: [
    { term: "林凡", field: "type", old: "A", new: "B",
      status: "принять", applied: false },
  ],
};

/* вкладка «Проверки» с готовым файлом правок */
function reviewCard() {
  globalThis.__review = {
    exists: true,
    content: JSON.stringify(REVIEW_DOC, null, 2),
    size: 47820,
  };
  return viewProject("ACTIVE", "Книга", "review");
}

test("Проверки: карточка правок — только список, без переключателя и нижних кнопок", async () => {
  const page = reviewCard();
  await tick();
  const text = allText(page);
  for (const gone of ["Список правок", "Редактор JSON", "Пробный прогон",
                      "Сохранить файл", "Применить все принятые"]) {
    assert.ok(!text.includes(gone), `в карточке больше нет «${gone}»`);
  }
  /* обе карточки (глоссарий и перевод) рисуют один и тот же мок-файл,
     но смотреть нужно одну: ner — первая под-вкладка */
  const ner = findByClass(page, "review-pane")[0];
  assert.equal(findByClass(ner, "rv-row").length, 1, "список правок на месте");
  const apply = findByClass(ner, "review-summary")[0]
    .querySelectorAll("button")
    .find((b) => collectText(b).join("") === "Применить");
  assert.ok(apply, "в сводке есть кнопка «Применить»");
  assert.match(apply.getAttribute("title") || "", /все принятые/,
    "о том, что применяются все принятые, говорит подсказка");
  globalThis.__review = null;
});

test("Проверки: строка о файле правок открывает его редактором на этой же вкладке", async () => {
  const page = reviewCard();
  await tick();
  const ner = findByClass(page, "review-pane")[0];
  const link = findByClass(ner, "review-status")[0].children[0];
  assert.match(collectText(link).join(""), /^файл есть · 47820 B$/);
  link._listeners.click[0]({ preventDefault() {} });
  await tick();
  /* редактор открывается ПОВЕРХ вкладки — того же однофайлового, что в
     «Поиске»: никуда уходить не надо, «← Назад» ведёт к списку правок */
  assert.equal(collectText(page.querySelector(".tab-active")).join(""), "Проверки",
    "вкладка не меняется: редактор открыт поверх неё");
  assert.equal(collectText(page.querySelector(".editor-meta")).join(""),
    "tmp/ner_review.json · 0 B", "редактор открыт на файле правок");
  assert.equal(prefsOf().page.view, "review", "вкладка запомнена");
  assert.equal(findByClass(page, "review-pane").length, 0,
    "пока открыт редактор, список правок не рисуется");
  const back = findByClass(page, "files-toolbar")[0].querySelectorAll("button")[0];
  assert.deepEqual(collectText(back).join(""), "← Назад");
  back._listeners.click[0]();
  await tick();
  assert.equal(collectText(page.querySelector(".tab-active")).join(""), "Проверки",
    "назад — та же вкладка");
  assert.equal(findByClass(page, "rv-row").length, 2,
    "«← Назад» вернул к списку правок: обе карточки рисуют один и тот же мок");
  globalThis.__review = null;
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
  // каталог: чекбокс + копировать, переименовать; файл: чекбокс + править,
  // скачать, копировать, переименовать
  assert.equal(findByClass(rows[0], "fsel").length, 1);
  assert.deepEqual(tips(rows[0]),
    ["Копировать chapters", "Переименовать chapters"]);
  assert.deepEqual(tips(rows[1]),
    ["Править a.txt", "Скачать a.txt", "Копировать a.txt", "Переименовать a.txt"]);
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
    "Копировать a.txt",
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
    "Копировать chapters",
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

test("файлы: копировать из панели — POST /file/copy, выделка снимается", async () => {
  const page = await filesPage();
  const cb = findByClass(findByClass(page, "frow")[1], "fsel")[0]; // файл a.txt
  cb.checked = true;
  await cb._listeners.change[0]();
  const btn = findByClass(page, "files-sel")[0].querySelectorAll("button")
    .find((b) => (b.getAttribute("aria-label") || "").startsWith("Копировать"));
  await btn._listeners.click[0]();
  await tick();
  const call = globalThis.__calls.find((c) => c.path === "/file/copy");
  assert.ok(call, "POST /api/file/copy ушёл");
  assert.deepEqual(JSON.parse(JSON.stringify(call.body)), {
    project: "ACTIVE/Книга", path: "a.txt",
  });
  assert.equal(findByClass(page, "files-sel")[0].classList.contains("hidden"), true);
});

/* ── «История»: точки, страницы и сравнение выбранной пары ───── */
const HISTORY_SEED = [
  { sha: "aaa", time: 30, label: "Точка 3", kind: "manual" },
  { sha: "bbb", time: 20, label: "Правка глоссария", kind: "apply" },
  { sha: "ccc", time: 10, label: "Первая точка", kind: "manual" },
];
/* страница diff — как её отдаёт сервер: порция файлов + total_files всех;
   у удалённого файла строки не считаются (None — «много») */
const HISTORY_DIFF = {
  files: [
    { path: "ner.json", status: "M", adds: 2, dels: 1 },
    { path: "chapters/00000_1_Глава 1/polished.txt", status: "A",
      adds: null, dels: null },
    { path: "chapters/00000_2_Глава 2/polished.txt", status: "D",
      adds: null, dels: null },
  ],
  total_files: 4,
};
const HISTORY_PATCH = {
  path: "ner.json", adds: 2, dels: 1,
  patch: "--- a/ner.json\n+++ b/ner.json\n@@ -1,2 +1,3 @@\n-старый термин\n+новый термин",
};

async function historyPage(pageSize) {
  globalThis.__history = HISTORY_SEED;
  globalThis.__historyPage = pageSize || HISTORY_SEED.length;
  globalThis.__historyDiff = HISTORY_DIFF;
  globalThis.__historyPatch = HISTORY_PATCH;
  globalThis.__calls = [];
  globalThis.__gets = [];
  globalThis.document.body.children.length = 0;
  const page = viewProject("ACTIVE", "Книга", "history");
  await tick();
  return page;
}

function historyCheckbox(page, i) {
  return findByClass(findByClass(page, "history-row")[i], "fsel")[0];
}

function selTips(page) {
  return findByClass(page, "files-sel")[0]
    .querySelectorAll("button")
    .map((b) => b.getAttribute("aria-label"));
}

/* открытая модалка сравнения (последняя из висящих на body) */
function diffModal() {
  return globalThis.document.body.children
    .filter((el) => (el.className || "").split(/\s+/).includes("modal-backdrop"))
    .slice(-1)[0];
}

test("история: строка точки — чекбокс и «вернуть», своей кнопки сравнения нет",
  async () => {
    const page = await historyPage();
    const rows = findByClass(page, "history-row");
    assert.equal(rows.length, 3);
    /* «сравнить с предыдущей» из строки убрано: у самой старой точки
       предыдущей точки нет — сравнение живёт в панели выделения */
    assert.deepEqual(tips(rows[1]), ["Вернуть файлы проекта к этой точке"]);
  });

test("история: список догружается страницами, счётчик — про все точки",
  async () => {
    const page = await historyPage(2);
    assert.equal(findByClass(page, "history-row").length, 2, "первая страница");
    const head = collectText(findByClass(page, "history-heading")[0]).join("");
    assert.equal(head, "Контрольные точки · 3", "в шапке — все точки");
    const pager = findByClass(page, "history-more")[0];
    assert.equal(pager.classList.contains("hidden"), false, "есть ещё страницы");
    assert.equal(collectText(pager).join(""), "Показать ещё (осталось 1)");
    await pager._listeners.click[0]();
    await tick();
    assert.equal(findByClass(page, "history-row").length, 3, "вторая страница");
    assert.equal(findByClass(page, "history-more")[0]
      .classList.contains("hidden"), true, "последняя страница");
  });

test("история: одна точка не сравнивается, панель без текста «null»",
  async () => {
    const page = await historyPage();
    const bar = findByClass(page, "files-sel")[0];
    assert.equal(bar.classList.contains("hidden"), true, "без выделки панели нет");
    const cb = historyCheckbox(page, 1);
    cb.checked = true;
    await cb._listeners.change[0]();
    await tick();
    assert.equal(bar.classList.contains("hidden"), false);
    /* null в replaceChildren браузер превращает в текст «null» — панель
       читалась бы «выделено: 1null» */
    assert.equal(collectText(bar).join(""), "выделено: 1",
      "в панели нет текста «null»");
    assert.deepEqual(selTips(page), ["Снять выделение"]);
  });

test("история: третья точка снимает самую ранее выбранную", async () => {
  const page = await historyPage();
  for (const i of [1, 2]) {
    const cb = historyCheckbox(page, i);
    cb.checked = true;
    await cb._listeners.change[0]();
    await tick();
  }
  const cb0 = historyCheckbox(page, 0);
  cb0.checked = true;
  await cb0._listeners.change[0]();
  await tick();
  assert.equal(collectText(findByClass(page, "files-sel-count")[0]).join(""),
    "выделено: 2");
  const on = findByClass(page, "history-row")
    .map((_row, i) => (historyCheckbox(page, i).checked ? i : -1))
    .filter((i) => i >= 0);
  /* выбор — это порядок кликов: снята bbb (выбиралась первой), остались
     ccc (второй клик) и aaa (третий) */
  assert.deepEqual(on, [0, 2], "в выделке последний клик и предыдущий");
});

test("история: «Сравнить выбранные точки» — дифф от старой к новой",
  async () => {
    const page = await historyPage();
    for (const i of [1, 2]) {
      const cb = historyCheckbox(page, i);
      cb.checked = true;
      await cb._listeners.change[0]();
      await tick();
    }
    const cmp = findByClass(page, "files-sel")[0]
      .querySelectorAll("button")[0];
    assert.equal(cmp.getAttribute("aria-label"), "Сравнить выбранные точки");
    await cmp._listeners.click[0]();
    await tick();
    const call = globalThis.__gets.find((c) => c.path === "/history/diff");
    assert.ok(call, "GET /api/history/diff ушёл");
    /* выделены bbb и ccc, ccc старее: from — старая точка, to — новая;
       SPA страницу не просит — сервер отдаёт свою (DIFF_MAX_FILES) */
    assert.equal(call.query,
      "project=ACTIVE%2F%D0%9A%D0%BD%D0%B8%D0%B3%D0%B0&from=ccc&to=bbb"
      + "&offset=0");
    const modal = diffModal();
    assert.ok(modal, "модалка сравнения открылась");
    const head = collectText(modal).join("");
    assert.ok(head.includes("С: Первая точка") && head.includes("По: Правка"),
      "в шапке — метки точек по порядку истории");
    assert.ok(head.includes("изменённых файлов: 4"), "шапка — ещё и счётчик");
    const rowsEl = findByClass(modal, "history-diff-files")[0];
    assert.equal(findByClass(rowsEl, "history-diff-file").length, 3,
      "список файлов сравнения не пуст");
    const text = collectText(rowsEl).join(" ");
    assert.ok(text.includes("ner.json") && text.includes("+2 −1"),
      "изменённый файл — с числами строк");
    /* у большого файла и у удаления строки не считаются: вместо чисел —
       «много» у добавленного и пустая строка у удалённого */
    assert.ok(text.includes("добавлен") && text.includes("много"),
      "большой файл — «много» вместо чисел");
    assert.ok(text.includes("удалён"), "удалённый файл подписан");
  });

test("история: «Показать ещё» догружает файлы под кнопку, дифф файла — один раз",
  async () => {
    const page = await historyPage();
    for (const i of [0, 1]) {
      const cb = historyCheckbox(page, i);
      cb.checked = true;
      await cb._listeners.change[0]();
      await tick();
    }
    const cmp = findByClass(page, "files-sel")[0]
      .querySelectorAll("button")[0];
    await cmp._listeners.click[0]();
    await tick();
    const modal = diffModal();
    const more = findByClass(modal, "history-more")[0];
    assert.equal(collectText(more).join(""), "Показать ещё (осталось 1)");
    await more._listeners.click[0]();
    await tick();
    const rows = findByClass(findByClass(modal, "history-diff-files")[0],
      "history-diff-file");
    assert.equal(rows.length, 6, "порция догружена под кнопку");
    assert.equal(findByClass(modal, "history-more")[0]
      .classList.contains("hidden"), true, "файлы кончились");
    /* клик по строке файла — unified-diff; повторный клик не переспрашивает */
    const summary = findTag(rows[0], "summary")[0];
    await summary._listeners.click[0]();
    await tick();
    await summary._listeners.click[0]();
    await tick();
    const patches = globalThis.__gets.filter((c) => c.path === "/history/patch");
    assert.equal(patches.length, 1, "дифф файла догружен один раз");
    assert.ok(patches[0].query.includes("path=ner.json"), "дифф по пути файла");
    const patch = findByClass(rows[0], "history-patch")[0];
    assert.ok(findTag(patch, "span").length, "строки диффа раскрашены");
  });

test("история: «Снять выделение» чистит чекбоксы и прячет панель", async () => {
  const page = await historyPage();
  const cb = historyCheckbox(page, 0);
  cb.checked = true;
  await cb._listeners.change[0]();
  await tick();
  const clear = findByClass(page, "files-sel")[0]
    .querySelectorAll("button").slice(-1)[0];
  await clear._listeners.click[0]();
  await tick();
  assert.equal(findByClass(page, "files-sel")[0].classList.contains("hidden"), true);
  const cbNow = historyCheckbox(page, 0);
  assert.equal(cbNow.checked, false, "чекбокс строки сброшен");
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

test("запуски epub: исходник автоподхватывается epub из source/", async () => {
  globalThis.__stageOptions = {
    source: ["book.epub", "cover.jpg", "notes.txt", "вторая.epub"],
  };
  const page = await runStage("epub");
  const field = (name) => findByClass(page, "field")
    .find((w) => w._field && w._field.name === name);
  const sel = field("input")._input._sel;
  /* автоподхват: первый epub пула, а не txt и не обложка */
  assert.equal(sel.value, "book.epub");
  /* режим toc принимает только epub — txt в список не попадает */
  assert.deepEqual(sel.children.map((o) => o.value),
    ["", "book.epub", "вторая.epub"]);
  /* чекбоксы стадии: дефолты реестра (очистка текста включена) */
  assert.equal(field("clean_text")._input.checked, true);
  assert.equal(field("clean_output")._input.checked, true);
  /* паттерны разбивки — поле режима regexp: в toc-режиме оно скрыто
     классом (узел живёт в DOM), значения неприменимых полей остаются */
  const sp = field("split_patterns");
  assert.ok(sp, "узел поля режима в DOM");
  assert.equal(sp.classList.contains("hidden"), true, "в toc-режиме скрыто");
  /* смена режима на chunk: txt возвращается в список, выбор не теряется */
  const modeSel = field("mode")._input;
  modeSel.value = "chunk";
  /* ВСЕ слушатели смены режима: первым стоит хранитель значения,
     вторым — epub-пересборка селекта исходника */
  modeSel.dispatchEvent({ type: "change" });
  await tick();
  const sel2 = field("input")._input._sel;
  assert.deepEqual(sel2.children.map((o) => o.value),
    ["", "book.epub", "notes.txt", "вторая.epub"]);
  assert.equal(sel2.value, "book.epub", "подхват сохранён");
  delete globalThis.__stageOptions;
});

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

test("запуски: сброс возвращает поле к значениям конфига, не к прошлому визиту", async () => {
  /* регрессия «сбрасывается до предыдущего»: эталон (baseline) снимал��я
   * ПОСЛЕ восстановления личной памяти (localStorage), и сброс возвращал
   * сохранённое при прошлом визите значение вместо дефолта конфига */
  globalThis.localStorage.clear();
  const page = await runStage("ner");
  const field = findByClass(page, "field")
    .find((f) => f._field && f._field.name === "chunk_size");
  const inp = field._input;
  assert.equal(inp.value, "8000", "старт — дефолт реестра");
  /* правка уезжает в память (localStorage) сама, без кнопки: событие диспатчим
     и на инпут, и на панель (делегат автосохранения) — в моке нет всплытия;
     первой .run-panel в DOM идёт карточка активного запуска, у неё
     слушателей нет */
  const panel = findByClass(page, "run-panel")
    .find((p) => p._listeners.input && p._listeners.input.length);
  const type = (node, ev) => {
    node.dispatchEvent({ type: ev });
    panel.dispatchEvent({ target: node, type: ev });
  };
  /* подпись в шапке панели говорит, что значение ещё не ушло (или уже ушло) */
  const pill = findByClass(page, "run-save-state")[0];
  assert.ok(pill.classList.contains("hidden"),
    "пока поле не меняли, подписи автосохранения нет");
  inp.value = "1234";
  type(inp, "input");
  assert.ok(!pill.classList.contains("hidden"), "изменённое поле подписано");
  assert.match(pill.textContent, /сохраняю…/, "сразу после правки");
  /* запись отложена на паузу ввода: на каждое нажатие клавиши писать нельзя */
  assert.equal(globalThis.localStorage.getItem("runVals:ACTIVE/Книга:ner"), null,
    "до паузы в памяти ничего не появляется");
  await new Promise((r) => setTimeout(r, 600));
  assert.match(pill.textContent, /сохранено/, "после записи");
  assert.equal(
    JSON.parse(globalThis.localStorage.getItem("runVals:ACTIVE/Книга:ner"))
      .chunk_size, "1234");
  /* перерисовка (как после запуска): память вернула 1234, эталон —
     по-прежнему 8000, а не 1234 */
  const page2 = await runStage("ner");
  const field2 = findByClass(page2, "field")
    .find((f) => f._field && f._field.name === "chunk_size");
  assert.equal(field2._input.value, "1234", "память вернула правку");
  /* сброс: поле — дефолт конфига 8000, память чиста */
  const btn = findByClass(page2, "run-reset")[0];
  await btn._listeners.click[0]();
  const bd = globalThis.document.body.children
    .filter((el) => (el.className || "").split(/\s+/).includes("modal-backdrop"))
    .slice(-1)[0];
  const word = findTag(bd, "input")[0];
  word.value = "СБРОСИТЬ";
  const ok = findByClass(bd, "btn-danger")[0];
  await ok._listeners.click[0]();
  await tick();
  const page3 = findByClass(globalThis.document.body, "run-panel")[0]
    ? globalThis.document.body : page2;
  const field3 = findByClass(page3, "field")
    .find((f) => f._field && f._field.name === "chunk_size");
  assert.equal(field3._input.value, "8000",
    "после сброса — дефолт конфига, а не 1234 прошлого визита");
  assert.equal(globalThis.localStorage.getItem("runVals:ACTIVE/Книга:ner"), null);
  /* подпись пересобрана вместе с панелью: менять нечего — она спрятана */
  assert.ok(findByClass(page2, "run-save-state")[0].classList.contains("hidden"),
    "после сброса менять нечего — подпись прячется");
  globalThis.localStorage.clear();
});

test("запуски: сохранение — одна отложенная запись, кнопки «Сохранить» в форме нет", async () => {
  /* отдельного сохранения формы запусков нет: подпись в шапке панели и есть
     весь ответ — значения книги уходят в память браузера сами */
  globalThis.localStorage.clear();
  const page = await runStage("ner");
  assert.ok(!findByClass(page, "run-form")
    .some((f) => collectText(f).includes("Сохранить")),
    "в форме стадии не осталось кнопки сохранения");
  const panel = findByClass(page, "run-panel")
    .find((p) => p._listeners.input && p._listeners.input.length);
  const pill = findByClass(page, "run-save-state")[0];
  const inp = findByClass(page, "field")
    .find((f) => f._field && f._field.name === "chunk_size")._input;
  inp.value = "9000";
  inp.dispatchEvent({ type: "input" });   // числовое поле пишет input
  panel.dispatchEvent({ target: inp, type: "input" });
  assert.equal(globalThis.localStorage.getItem("runVals:ACTIVE/Книга:ner"), null,
    "сразу после правки запись ещё не идёт (пауза ввода)");
  assert.match(pill.textContent, /сохраняю…/);
  await new Promise((r) => setTimeout(r, 600));
  assert.equal(
    JSON.parse(globalThis.localStorage.getItem("runVals:ACTIVE/Книга:ner"))
      .chunk_size, "9000");
  globalThis.localStorage.clear();
});

test("запуски: пустой конец диапазона при уходе с поля подставляет последнюю главу", async () => {
  globalThis.localStorage.clear();
  globalThis.__stageOptions = { chapters: { min: 3, max: 320 } };
  const page = await runStage("ner");
  const row = findByClass(page, "run-range-row")[0];
  const [start, end] = findTag(row, "input");
  assert.equal(start.value, "3", "«от» — автоподхват первой главы");
  assert.equal(end.value, "320", "«до» — автоподхват последней");
  /* стёрли «до» и ушли с поля — вернулась последняя глава */
  end.value = "";
  end.dispatchEvent({ type: "input" });
  await end._listeners.change[0]({ target: end });
  assert.equal(end.value, "320", "пустой «до» — последняя глава книги");
  /* стёрли «от» и ушли с поля — вернулась первая */
  start.value = "";
  start.dispatchEvent({ type: "input" });
  await start._listeners.change[0]({ target: start });
  assert.equal(start.value, "3", "пустой «от» — первая глава книги");
  /* во время ввода (без change) пусто остаётся пустым: стереть и вписать
     с нуля можно */
  end.value = "";
  end.dispatchEvent({ type: "input" });
  assert.equal(end.value, "", "при вводе автоподстановка не мешает");
  delete globalThis.__stageOptions;
  globalThis.localStorage.clear();
});

test("запуски: диапазон глав — конец раньше начала виден до запуска", async () => {
  /* «64 – 3» — не опечатка формы, а запуск без работы: диапазон пуст, стадия
     молчит. Ошибка видна сразу (подсказка + рамка), а кнопка «Запустить»
     такой запуск не отправляет */
  globalThis.localStorage.clear();
  globalThis.__stageOptions = { chapters: { min: 1, max: 320 } };
  const page = await runStage("translate_check_llm");
  const row = findByClass(page, "run-range-row")[0];
  assert.ok(row, "строка диапазона в форме стадии");
  const [start, end] = findTag(row, "input");
  const note = findByClass(row, "run-range-note")[0];
  assert.equal(note.classList.contains("hidden"), true,
    "чистый диапазон — без подсказки");
  /* конец раньше начала */
  start.value = "64";
  start.dispatchEvent({ type: "input" });
  end.value = "3";
  end.dispatchEvent({ type: "input" });
  assert.equal(note.classList.contains("hidden"), false);
  assert.match(note.textContent, /Начальная глава больше конечной/);
  assert.equal(start.classList.contains("input-bad"), true);
  assert.equal(end.classList.contains("input-bad"), true);
  /* потолок — последняя глава книги (номер главы — свой у каждого поля) */
  end.value = "999";
  end.dispatchEvent({ type: "input" });
  assert.equal(start.classList.contains("input-bad"), false);
  assert.equal(end.classList.contains("input-bad"), true);
  assert.match(note.textContent, /«Конечная глава»: в книге всего 320/);
  end.value = "64";
  end.dispatchEvent({ type: "input" });
  assert.equal(note.classList.contains("hidden"), true,
    "годный диапазон — снова чисто");
  /* кнопка «Запустить»: такой диапазон не уходит на сервер */
  end.value = "3";
  end.dispatchEvent({ type: "input" });
  globalThis.__calls = [];
  const runBtn = findByClass(page, "btn-primary")
    .find((b) => collectText(b).join("") === "Запустить");
  assert.ok(runBtn, "кнопка запуска в форме");
  await runBtn._listeners.click[0]();
  assert.equal(globalThis.__calls.filter((c) => c.path === "/jobs").length, 0,
    "диапазон с концом меньше начала не отправлен");
  const err = findByClass(page, "form-error")[0];
  assert.match(err.textContent, /больше конечной/, "причина видна в форме");
  delete globalThis.__stageOptions;
  globalThis.localStorage.clear();
});

/* ── проверка промпт-файла: иконка у поля файла промпта ────────────────
 * вердикт отдаёт скрипт стадии (--check-prompt): SPA показывает его только
 * при настоящих замечаниях и перечитывает на смене выбранного файла */
test("запуски: проверка промпта — иконка только при замечаниях", async () => {
  globalThis.localStorage.clear();
  globalThis.__calls = [];
  globalThis.__stageOptions = { prompts: ["ner_prompt.txt", "ner_alt.txt"] };
  globalThis.__promptCheck = {
    stage: "ner", file: "prompts/ner_prompt.txt", state: "file",
    tags: ["prompt_pass1"],
    problems: ["в <prompt_pass2> нет переменной {ner_json}"],
    notes: ["внешний промпт не выбран"],
  };
  const page = await runStage("ner");
  await tick();
  const wrap = findByClass(page, "field")
    .find((f) => f._field && f._field.name === "prompt_file");
  assert.ok(wrap, "поле файла промпта в форме есть");
  const slot = findByClass(wrap, "run-prompt-warn")[0];
  assert.ok(slot, "у поля промпта есть узел проверки");
  const icon = findByClass(slot, "run-prompt-warn-icon")[0];
  assert.ok(icon, "при замечаниях иконка видна");
  assert.match(icon.getAttribute("aria-label"), /нет переменной \{ner_json\}/);
  assert.match(icon.getAttribute("aria-label"),
    /^Проверка промпта «Глоссарий», замечаний: 1/, "тултип называет стадию");
  /* проверка уходит с полями формы: файл выбран автоподхватом */
  const call = globalThis.__calls
    .filter((c) => c.path === "/stages/ner/prompt-check").pop();
  assert.ok(call, "проверка ушла на сервер при открытии формы");
  assert.equal(call.body.project, "ACTIVE/Книга");
  assert.equal(call.body.params.prompt_file, "prompts/ner_prompt.txt",
    "файл уезжает скрипту путём внутри книги, а не голым именем");
  /* вердикт чист — иконка уходит (пустой выбор промпта замечанием не считается) */
  const sel = findTag(wrap, "select")[0];
  globalThis.__promptCheck = {
    stage: "ner", file: "prompts/ner_alt.txt", state: "file",
    tags: ["prompt_pass1", "prompt_pass2"], problems: [], notes: [],
  };
  sel.value = "ner_alt.txt";
  /* у селекта два слушателя change (значение и проверка) — оба идут по порядку */
  for (const fn of sel._listeners.change) {
    await fn({ target: sel });
  }
  await tick();
  assert.equal(findByClass(slot, "run-prompt-warn-icon").length, 0,
    "чистый промпт — иконки нет");
  const call2 = globalThis.__calls
    .filter((c) => c.path === "/stages/ner/prompt-check").pop();
  assert.equal(call2.body.params.prompt_file, "prompts/ner_alt.txt",
    "смена файла — новая проверка");
  delete globalThis.__stageOptions;
  delete globalThis.__promptCheck;
  globalThis.__calls = [];
  globalThis.localStorage.clear();
});

/* ── промпта в книге нет: поле предлагает «Из шаблона», а не загрузку
   своего файла — стадия работает без правок, файл приезжает из набора
   templates/* вместе с выбором в поле ── */
test("запуски: пустой промпт — «Из шаблона» вместо «Загрузить»", async () => {
  globalThis.localStorage.clear();
  globalThis.__calls = [];
  /* prompts/ книги пуст — автоподхвату нечего выбрать */
  globalThis.__stageOptions = { prompts: [] };
  globalThis.__prompts = {
    prompts: [],
    content: "<prompt_pass1>\n{chunk_text}\n</prompt_pass1>\n",
    templates: [
      { set: "General", name: "ner_prompt.txt",
        content: "<prompt_pass1>\n{chunk_text}\n</prompt_pass1>\n" },
    ],
    template_sets: [
      { name: "Другой", prompts: ["other.txt"] },
      { name: "General", prompts: ["ner_prompt.txt", "wiki_prompt.txt"] },
    ],
  };
  const page = await runStage("ner");
  const wrap = findByClass(page, "field")
    .find((f) => f._field && f._field.name === "prompt_file");
  assert.ok(wrap, "поле файла промпта в форме есть");
  const sel = findTag(wrap, "select")[0];
  assert.equal(sel.value, "", "промпта в книге нет — выбор пуст");
  const btn = wrap.querySelectorAll("button")
    .find((b) => allText(b).trim() === "Из шаблона");
  assert.ok(btn, "при пустом выборе — «Из шаблона»");
  assert.ok(!wrap.querySelectorAll("button")
    .some((b) => allText(b).trim() === "Загрузить"),
  "загрузки своего файла у промпта нет");
  await btn._listeners.click[0]();
  await tick();
  const bd = globalThis.document.body.children
    .filter((el) => (el.className || "").split(/\s+/).includes("modal-backdrop"))
    .slice(-1)[0];
  assert.ok(bd, "модалка «из шаблона» открылась");
  const selects = findTag(bd, "select");
  assert.equal(selects.length, 2, "выбор набора и промпта");
  const [setSel, fileSel] = selects;
  /* набор с нужным файлом выбран сам: имя берётся из дефолта поля */
  assert.equal(setSel.value, "General", "набор с ner_prompt.txt — по умолчанию");
  assert.equal(fileSel.value, "ner_prompt.txt", "файл — по дефолту поля");
  assert.match(allText(findByClass(bd, "card-hint")[0]),
    /prompts\/ner_prompt\.txt/, "видно, что приедет в книгу");
  /* смена набора перестраивает список промптов */
  setSel.value = "Другой";
  await setSel._listeners.change[0]();
  assert.deepEqual(fileSel.children.map((o) => o.value), ["other.txt"]);
  setSel.value = "General";
  await setSel._listeners.change[0]();
  await findByClass(bd, "btn-primary")[0]._listeners.click[0]();
  await tick();
  /* содержимое берётся из набора именно этого файла */
  const tq = globalThis.__gets.filter(
    (g) => g.path === "/prompts/ner_prompt.txt/template").pop();
  assert.ok(tq, "шаблон одного промпта запрошен");
  const put = globalThis.__calls.find((c) => c.method === "PUT"
    && c.path === "/prompts/ner_prompt.txt");
  assert.ok(put, "промпт записан в книгу");
  assert.equal(put.body.project, "ACTIVE/Книга");
  assert.match(put.body.content, /prompt_pass1/, "в книгу ушёл текст набора");
  delete globalThis.__stageOptions;
  delete globalThis.__prompts;
  globalThis.__calls = [];
  globalThis.__gets = [];
  globalThis.localStorage.clear();
});

/* ── wiki: чипсы типов из глоссария — счётчики считают то, что реально
   уйдёт в запрос: порог частоты И срез «Макс. терминов» ── */
const WIKI_NER = [
  { term: "мир", translation: "мир", type: "Location (Body)", count: 7 },
  { term: "клинок", translation: "Клинок", type: "Artifact", count: 5 },
  { term: "секта", translation: "Секта", type: "Sect", count: 4 },
  { term: "облако", translation: "Облако", type: "Base Term", count: 3 },
  { term: "старейшина", translation: "Старейшина", type: "Person", count: 3 },
  { term: "небо", translation: "Небо", type: "Other", count: 2 },
  { term: "меч", translation: "Меч", type: "Artifact", count: 1 },
  { term: "кумир", translation: "", type: "Person", count: 9 },
];

test("wiki: счётчики типов пересчитываются и по «Мин. частота», и по «Макс. терминов»", async () => {
  globalThis.localStorage.clear();
  globalThis.__ner = { items: WIKI_NER };
  const page = await runStage("wiki");
  await tick(); // чипсы рисуются после GET /ner
  const box = findByClass(page, "ner-chips-box")[0];
  const bar = findByClass(page, "ner-chips-bar")[0];
  const chips = () => findByClass(box, "ner-chip")
    .map((c) => collectText(c).join("").trim());
  const setField = (name, value) => {
    const inp = findByClass(page, "field")
      .find((f) => f._field && f._field.name === name)._input;
    inp.value = String(value);
    inp.dispatchEvent({ type: "input" });
  };
  /* дефолты: порог ≥2, потолка не видно — записей меньше 80; у типа
     «Other» оба термина проходят порог, у Person — только старейшина
     (кумир без перевода, меч ниже порога) */
  assert.deepEqual(chips(), [
    "Artifact (1)", "Location (1)", "Organisation (1)", "Other (2)",
    "Person (1)",
  ], "чипсы: базовые типы (как в CLI) со счётчиками после порога");
  assert.match(collectText(bar).join(""), /6 из 8 терминов попадут в запрос/);
  /* «Макс. терминов» = 3: в запрос идут три самых частотных термина,
     счётчики типов обязаны считать уже этот срез */
  setField("top", 3);
  await tick();
  assert.deepEqual(chips(), [
    "Artifact (1)", "Location (1)", "Organisation (1)", "Other (0)",
    "Person (0)",
  ], "топ-3 по count: у Other и Person терминов не осталось");
  assert.match(collectText(bar).join(""),
    /3 из 8 терминов попадут в запрос \(топ-3 по count, порог ≥2\)/);
  /* потолок убран, порог поднят до 5: проходят только мир (7) и клинок (5) */
  setField("top", 80);
  setField("min_count", 5);
  await tick();
  assert.deepEqual(chips(), [
    "Artifact (1)", "Location (1)", "Organisation (0)", "Other (0)",
    "Person (0)",
  ], "порог ≥5: у Organisation, Other и Person терминов нет");
  assert.match(collectText(bar).join(""),
    /2 из 8 терминов попадут в запрос \(топ-80 по count, порог ≥5\)/);
  globalThis.__ner = null;
  globalThis.localStorage.clear();
});

/* ── ner_check: чипсы полей записи — базовые поля неотключаемы ── */
const NER_CHECK_ITEMS = [
  { term: "林凡", translation: "Линь Фан", type: "Person (male)", count: 9 },
  { term: "青云宗", translation: "Секта Цинъюнь", type: "Location",
    context: "青云宗位于山中", notes: "палладия", count: 4 },
];

test("ner_check: term, type и translation в запросе всегда, notes и context — по умолчанию", async () => {
  globalThis.localStorage.clear();
  globalThis.__ner = {
    items: NER_CHECK_ITEMS,
    by_type: { "Person (male)": 1, Location: 1 },
  };
  const page = await runStage("ner_check");
  await tick(); // чипсы рисуются после GET /ner
  /* первый .ner-chips-box — чипсы типов, второй — чипсы полей записи */
  const box = findByClass(page, "ner-chips-box")[1];
  const bar = findByClass(page, "ner-chips-bar")[1];
  const chips = () => findByClass(box, "ner-chip")
    .map((c) => collectText(c).join("").replace(/\s+/g, " ").trim());
  const boxes = () => findTag(box, "input");
  assert.deepEqual(chips(), [
    "term (всегда)", "type (всегда)", "translation (всегда)",
    "context", "notes", "count",
  ], "поля: базовые первыми и с подписью, остальные — ключи из ner.json");
  assert.deepEqual(boxes().map((i) => i.disabled),
    [true, true, true, false, false, false],
    "термин, тип и перевод снять нельзя: чекбоксы неотключаемые");
  assert.deepEqual(boxes().map((i) => i.checked),
    [true, true, true, true, true, false],
    "по умолчанию: базовые поля + notes и context (они есть в данных), count — нет");
  assert.match(collectText(bar).join("").replace(/\s+/g, " "),
    /term, type, translation — всегда; по умолчанию: notes, context \(если есть в данных\)/);
  /* снятие факультативного поля сохраняется браузеру, базовые остаются */
  const notes = boxes()[4];
  notes.checked = false;
  notes.dispatchEvent({ type: "change" });
  await tick();
  assert.deepEqual(
    JSON.parse(globalThis.localStorage.getItem("chips:ACTIVE/Книга:ner_check")),
    { fields: "term,type,translation,context" },
    "в память стадии ушли базовые поля плюс context: notes снят");
  assert.deepEqual(chips(), [
    "term (всегда)", "type (всегда)", "translation (всегда)",
    "context", "notes", "count",
  ], "состояние перерисовано без потери чипсов");
  assert.equal(boxes()[1]._listeners.change, undefined,
    "у неотключаемого чекбокса обработчика смены нет: его не снять");
  globalThis.__ner = null;
  globalThis.localStorage.clear();
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

/* клик по результату — тот же однофайловый редактор, что в «Проверках»: он
   открывается поверх «Поиска», а не уводит во вкладку «Редактор» */
test("поиск: клик по файлу главы открывает его редактором на этой же вкладке", async () => {
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
  assert.equal(String(link.getAttribute("title")),
    "Открыть файл в редакторе с этим запросом",
    "подсказка обещает редактор, а не вкладку «Редактор»");
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
  assert.equal(collectText(page.querySelector(".tab-active")).join(""), "Поиск",
    "вкладка не меняется: редактор открыт поверх «Поиска»");
  assert.equal(collectText(page.querySelector(".editor-meta")).join(","),
    "chapters/00000_1_Глава 1/polished.txt · 0 B",
    "редактор — тот же однофайловый, что в «Проверках»");
  assert.equal(findByClass(page, "ed-type").length, 0,
    "вкладка «Редактор» с её панелями не открывается");
  const back = findByClass(page, "files-toolbar")[0].querySelectorAll("button")[0];
  assert.deepEqual(collectText(back).join(""), "← Назад");
  back._listeners.click[0]();
  await tick();
  assert.equal(collectText(page.querySelector(".tab-active")).join(""), "Поиск",
    "назад — та же вкладка");
  assert.equal(findByClass(page, "search-q")[0].value, "мир",
    "запрос помнится: искать заново не придётся");
  assert.deepEqual(
    findByClass(page, "search-file-path").map((n) => collectText(n).join("")),
    ["chapters/00000_1_Глава 1/polished.txt"],
    "«← Назад» возвращает к результатам последней выборки",
  );
  /* запрос в панель редактора передаёт openEditor: панель открывается заполненной */
  assert.match(SRC, /if \(st\.edit && st\.search\) \{[\s\S]*openFind\(st\._ed, q\);/);
  assert.doesNotMatch(SRC, /applyPaneFind/,
    "предзаполнение поиска панелей «Редактора» больше не нужно");
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

test("редакторы: у промпта язык — выбор с «Внешнего вида» (дефолт — prompts)", async () => {
  globalThis.localStorage.clear();
  const prompts = viewProject("ACTIVE", "Книга", "prompts");
  await tick();
  const ped = findTag(prompts, "textarea").filter((t) => "lang" in t);
  assert.equal(ped.length, 1, "на вкладке один редактор промпта");
  // язык промптов — общий выбор с «Внешнего вида»: дефолт — собственный
  // язык разметки промптов (теги секций, подстановки, комментарии)
  assert.equal(ped[0].lang, "prompt", "промпту назначен язык промптов");

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

test("«Промпты»: кнопки файлов слева от редактора, а сохранение — в редакторе", async () => {
  globalThis.__prompts = {
    prompts: [{ name: "p.txt", size: 3 }],
    templates: [{ name: "p.txt", set: "General" }],
    template_sets: [{ name: "General", prompts: ["p.txt"] }],
  };
  const page = viewProject("ACTIVE", "Книга", "prompts");
  await tick();
  const layout = findByClass(page, "prompts-layout")[0];
  const [side, main] = layout.children;
  const sideText = collectText(side).join(" ");
  assert.match(sideText, /Создать/, "«Создать» — над списком слева");
  assert.match(sideText, /Из шаблона/, "«Из шаблона» — над списком слева");
  assert.match(sideText, /Очистить/, "«Очистить» — над списком слева");
  const mainText = collectText(main).join(" ");
  assert.match(mainText, /Сохранить/, "сохранение осталось в редакторе");
  assert.doesNotMatch(mainText, /Создать/);
  delete globalThis.__prompts;
});

test("«Промпты»: «Очистить» удаляет все промпты книги одним запросом", async () => {
  globalThis.__prompts = {
    prompts: [{ name: "a.txt", size: 3 }, { name: "b.txt", size: 3 }],
    templates: [],
    template_sets: [],
  };
  globalThis.__calls = [];
  const page = viewProject("ACTIVE", "Книга", "prompts");
  await tick();
  const clear = findByClass(page, "prompts-side")[0]
    .querySelectorAll("button").find((b) => allText(b).trim() === "Очистить");
  assert.ok(clear, "кнопка «Очистить» есть");
  clear._listeners.click[0]();
  await tick();
  /* подтверждение — ввод слова-пароля (мок confirmModal требует клика) */
  const bd = globalThis.document.body.children
    .filter((el) => (el.className || "").split(/\s+/).includes("modal-backdrop"))
    .slice(-1)[0];
  assert.ok(bd, "подтверждение очистки открылось");
  findTag(bd, "input")[0].value = "УДАЛИТЬ";
  await findByClass(bd, "btn-danger")[0]._listeners.click[0]();
  await tick();
  const del = globalThis.__calls.filter((c) => c.method === "DELETE"
    && c.path === "/prompts");
  assert.equal(del.length, 1, "очистка — один DELETE /prompts");
  delete globalThis.__prompts;
});

test("«Промпты»: «Из шаблона» → весь набор копируется одним запросом", async () => {
  globalThis.__prompts = {
    prompts: [{ name: "pipeline_prompt.txt", size: 3 }],
    templates: [{ name: "pipeline_prompt.txt", set: "General" }],
    template_sets: [{ name: "General", prompts: ["pipeline_prompt.txt", "ner_prompt.txt"] }],
  };
  globalThis.__calls = [];
  const page = viewProject("ACTIVE", "Книга", "prompts");
  await tick();
  await findByClass(page, "prompts-side")[0].querySelectorAll("button")
    .find((b) => allText(b).trim() === "Из шаблона")._listeners.click[0]();
  await tick();
  const bd = globalThis.document.body.children
    .filter((el) => (el.className || "").split(/\s+/).includes("modal-backdrop"))
    .slice(-1)[0];
  assert.ok(bd, "модалка «Из шаблона» открылась");
  /* второй способ — «весь набор»: видно состав и что будет заменено;
     мок не знает групп radio — снимаем «Один файл» руками */
  const inputs = findTag(bd, "input");
  inputs.find((i) => i.value === "one").checked = false;
  const setRadio = inputs.find((i) => i.value === "set");
  setRadio.checked = true;
  await setRadio._listeners.change[0]();
  /* в DOM первый option выбран сам, заглушка value не выводит — ставим руками */
  const setSel = findTag(bd, "select")[1];
  setSel.value = "General";
  await setSel._listeners.change[0]();
  const hint = findByClass(bd, "card-hint")[0];
  assert.match(allText(hint), /ner_prompt\.txt/, "виден состав набора");
  assert.match(allText(hint), /Заменятся в книге: pipeline_prompt\.txt/,
    "видно, что будет заменено");
  await findByClass(bd, "btn-primary")[0]._listeners.click[0]();
  await tick();
  const call = globalThis.__calls.find((c) => c.path === "/prompts/copy-set");
  assert.ok(call, "POST /api/prompts/copy-set ушёл");
  assert.deepEqual(JSON.parse(JSON.stringify(call.body)), {
    project: "ACTIVE/Книга",
    set: "General",
  });
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

/* ── «Запуски»: живой лог и таблица глав из SSE ────────────────────
   Вкладка монтируется БЕЗ строк: весь хвост и все события приходят
   стримом (снимок одним кадром, живые события — пачками). _writes на
   узлах показывают, что реально перерисовал поток. */
const RUN_JOB = "aabbccddeeff";
/* мок даёт scrollHeight=1000, clientHeight=300: у конца — scrollTop=700 */
const AT_END = 700;

/* открыть «Запуски» с живым запуском и дождаться первого рендера вкладки */
async function runView(job, frames, options) {
  globalThis.__job = { job };
  globalThis.__sse = frames;
  globalThis.__stageOptions = options || {};
  const page = viewProject("ACTIVE", "Книга", "run", RUN_JOB);
  for (let i = 0; i < 20 && !page.querySelector(".log-area"); i++) await tick();
  assert.ok(page.querySelector(".log-area"), "панель лога отрисована");
  return page;
}

/* кадры стрима расходятся по 15 мс на запись, плюс финальный рендер */
async function settled() {
  await new Promise((r) => setTimeout(r, 700));
  delete globalThis.__job;
  delete globalThis.__sse;
  delete globalThis.__stageOptions;
}

/* текст окна лога: собственный текст узла и тексты детей. Настоящий DOM
 * склеивает их в одном textContent, mock — нет, поэтому окно читается
 * обходом (в браузере эта сумма и есть textContent) */
const logText = (n) => (n.textContent || "")
  + (n.children || []).map((c) => c.textContent).join("");

test("живой лог: снимок — весь буфер, поток ДОПИСЫВАЕТ окно", async () => {
  /* регресс: панель держала 500 строк и на каждую пачку переписывала всё
     окно через textContent — на реальном логе глоссария живой поток
     верстал все предыдущие строки заново (82 мс снимка и 13 мс на кадр на
     окне 5000 строк) */
  const job = { id: RUN_JOB, action: "ner", project: "ACTIVE/Книга",
    status: "running", lines: [], events: [], progress: null };
  const lines = Array.from({ length: 503 }, (_, i) => `л${i}`);
  const page = await runView(job, [
    { type: "snapshot", lines, events: [], progress: null },
    { type: "line", text: "новая-1" },
    { type: "line", text: "новая-2" },
    { type: "status", status: "done" },
  ]);
  const pre = page.querySelector(".log-area");
  await settled();
  const shown = logText(pre).split("\n");
  assert.equal(shown.length, 505, "снимок пришёл целиком, живые строки — сверху");
  assert.equal(shown[0], "л0", "шапка прогона остаётся в панели");
  assert.equal(shown[shown.length - 1], "новая-2", "строки не задом наперёд");

  /* живой поток НЕ переписывает окно: пачки ложатся текстовыми узлами.
     Сравнение с прогоном, где строк в 150 раз больше: перезаписей окна
     столько же — их число зависит от кадров, а не от объёма лога */
  const many = await runView(job, [
    { type: "snapshot", lines: ["старт"], events: [], progress: null },
    ...Array.from({ length: 30 }, (_, i) => ({ type: "line", text: `с${i}` })),
    { type: "status", status: "done" },
  ]);
  const preMany = many.querySelector(".log-area");
  await settled();
  const shownMany = logText(preMany).split("\n");
  assert.equal(shownMany.length, 31, "все строки потока — в окне");
  assert.equal(shownMany[0], "старт");
  assert.equal(shownMany[30], "с29");
  assert.equal(preMany._writes, pre._writes,
    "перезапись окна не зависит от числа строк: строки дописываются");
});

test("живой лог: вниз прокручивается только панель и так у конца", async () => {
  const job = { id: RUN_JOB, action: "ner", project: "ACTIVE/Книга",
    status: "running", lines: [], events: [], progress: null };
  const frames = [
    { type: "snapshot", lines: ["a"], events: [], progress: null },
    { type: "line", text: "b" },
    { type: "line", text: "c" },
    { type: "status", status: "done" },
  ];
  const pinned = await runView(job, frames);
  const atEnd = pinned.querySelector(".log-area");
  atEnd.scrollTop = AT_END;
  await settled();
  assert.equal(logText(atEnd), "a\nb\nc");
  assert.equal(atEnd.scrollTop, 1000, "панель у конца — прокрутка дописывается");

  const scrolled = await runView(job, frames);
  const mid = scrolled.querySelector(".log-area");
  mid.scrollTop = 0; // человек отлистал лог вверх
  await settled();
  assert.equal(mid.scrollTop, 0, "листавшего лог не выбрасывает в конец");
  assert.equal(logText(mid), "a\nb\nc");
});

test("таблица глав: клетка перерисовывается только на смене статуса", async () => {
  /* регресс: конвейер на 1300 глав — это ~3900 событий, и каждое искало
     свою строку обходом всей таблицы и перезаписывало клетку заново */
  const job = { id: RUN_JOB, action: "pipeline", project: "ACTIVE/Книга",
    status: "running", lines: ["работа"], events: [], progress: null };
  const ev = (id, stage, status) => ({ type: "event", event: { id, stage, status } });
  const page = await runView(job, [
    { type: "snapshot", lines: ["работа"], events: [], progress: null },
    ev(1, 1, "OK"), ev(1, 1, "OK"), ev(1, 1, "OK"),
    ev(1, 1, "ERROR"), ev(2, 3, "SKIP"),
    { type: "status", status: "done" },
  ], { chapters: { ids: [1, 2, 3] } });
  const cells = findByClass(page, "ch-cell");
  assert.equal(cells.length, 9, "три главы по три стадии");
  await settled();
  assert.equal(cells.reduce((n, c) => n + (c._writes || 0), 0), 3,
    "пять событий — три записи в DOM: повторы статуса клетки не касаются");
  const row = (id) => findByClass(page, "ch-row")
    .find((r) => r.getAttribute("data-id") === id);
  assert.equal(allText(row("1").querySelectorAll(".ch-cell")[0]), "✗");
  assert.equal(allText(row("2").querySelectorAll(".ch-cell")[2]), "⊘");
  assert.equal(allText(row("3").querySelectorAll(".ch-cell")[0]), "·");
});

test("«Статус»: по бару на тип главы и строка отсутствующих диапазоном", async () => {
  /* над таблицей — готовность по типам: процент, трек и «каких глав нет».
     Список приходит с сервера диапазонами (core/projects.py::progress_missing):
     сотни номеров в строку не влезают, поэтому подпись - диапазоны. */
  globalThis.__status = {
    chapters: {
      1: { translate: true, redact: true, polish: true },
      2: { translate: true, redact: false, polish: false },
      3: { translate: false, redact: false, polish: false },
    },
    counts: { chapters: 3, translate: 2, redact: 1, polish: 1 },
    missing: {
      translate: { ids: [3], ranges: "3", count: 1, total: 3, pct: 67 },
      redact: { ids: [2, 3], ranges: "2-3", count: 2, total: 3, pct: 33 },
      polish: { ids: [2, 3], ranges: "2-3", count: 2, total: 3, pct: 33 },
    },
  };
  const page = await reopenProject("ACTIVE", "Книга", "status");
  await settled();
  delete globalThis.__status;

  const stages = findByClass(page, "status-stage");
  assert.equal(stages.length, 3, "три стадии цепочки — три блока");
  const texts = stages.map(allText);
  assert.match(texts[0], /Перевод/, "подпись стадии в баре");
  assert.match(texts[0], /2\/3 · 67%/, "счётчик и процент готовности");
  assert.match(texts[1], /Отсутствуют главы:\s*2-3/);
  // готово всё — диапазона нет, «нет» вместо сплошного ряда номеров
  globalThis.__status = {
    chapters: { 1: { translate: true, redact: true, polish: true } },
    counts: { chapters: 1, translate: 1, redact: 1, polish: 1 },
    missing: {
      translate: { ids: [], ranges: "—", count: 0, total: 1, pct: 100 },
      redact: { ids: [], ranges: "—", count: 0, total: 1, pct: 100 },
      polish: { ids: [], ranges: "—", count: 0, total: 1, pct: 100 },
    },
  };
  const done = await viewProject("ACTIVE", "Книга", "status");
  await settled();
  delete globalThis.__status;
  const labels = findByClass(done, "status-missing-label");
  assert.equal(labels.length, 3);
  assert.ok(labels.every((n) => allText(n) === "Все главы готовы"),
    "готово — подпись, а не ряд номеров");
  assert.equal(findByClass(done, "status-missing-ranges").length, 0,
    "пустого ряда диапазонов на готовом проекте нет");
  assert.equal(findByClass(done, "status-missing-has").length, 0,
    "класс пропусков не липнет на готовый проект");
  // заполнение трека — по проценту сервера
  const fills = findByClass(done, "progress-fill");
  assert.ok(fills.length >= 3);
  assert.equal(fills[0].getAttribute("style"), "width:100%");
});

test("«Статус»: длинный список пропусков — свернут, разворачивается кнопкой", async () => {
  /* сотни диапазонов строкой вытеснили бы таблицу за край экрана: по
     умолчанию две строки, полный перечень — по кнопке; тултип есть у мыши,
     но не у пальца, поэтому кнопка нужна как таковая */
  const many = Array.from({ length: 60 }, (_, i) => i * 2 + 2).join(", ");
  globalThis.__status = {
    chapters: { 1: { translate: true, redact: false, polish: false } },
    counts: { chapters: 3, translate: 3, redact: 1, polish: 1 },
    missing: {
      translate: { ids: [], ranges: "—", count: 0, total: 3, pct: 100 },
      redact: { ids: [2], ranges: many, count: 59, total: 3, pct: 33 },
      polish: { ids: [2], ranges: many, count: 59, total: 3, pct: 33 },
    },
  };
  const page = viewProject("ACTIVE", "Книга", "status");
  await new Promise((r) => setTimeout(r, 30));
  const cards = findByClass(page, "status-stage");
  const more = findByClass(page, "status-missing-more");
  assert.equal(more.length, 2, "кнопка у каждой длинной строки");
  assert.match(allText(more[0]), /весь список \(59\)/);
  const ranges = findByClass(cards[1], "status-missing-ranges")[0];
  assert.ok(!ranges.className.includes("status-missing-open"),
    "по умолчанию список свернут");
  // короткая строка кнопки не получает (у перевода пропусков нет)
  assert.equal(findByClass(cards[0], "status-missing-more").length, 0);
  more[0]._listeners.click[0]();
  await new Promise((r) => setTimeout(r, 10));
  assert.ok(ranges.className.includes("status-missing-open"), "список раскрыт");
  assert.match(allText(more[0]), /свернуть/);
  // состояние переживает перерисовку: вкладка помнит, что список раскрыт
  const again = await reopenProject("ACTIVE", "Книга", "status");
  await new Promise((r) => setTimeout(r, 30));
  delete globalThis.__status;
  const reRanges = findByClass(again, "status-missing-ranges")[0];
  assert.ok(reRanges.className.includes("status-missing-open"),
    "раскрытый список возвращается из состояния вкладки");
});

test("живой лог: окно держит хвост буфера и не растёт с ним", async () => {
  globalThis.__sseDelay = 0; // кадров тысячи — пауза между ними тут не нужна
  /* окно лога равно хвосту буфера (LOG_LIMIT — весь кольцевой буфер сервера).
     Поток длиннее потолка: старое снимается ЦЕЛЫМИ кусками, а не по строке
     на кадр — построчный срез каждый кадр заново верстает всё окно
     (замер: 13 мс против 2 мс на кадр на окне 5000 строк) */
  const LOG_LIMIT = 5000;
  const LOG_CHUNK = 250;
  const LOG_TRIM = 500;
  const snapshot = Array.from({ length: LOG_LIMIT }, (_, i) => `с${i}`);
  const lines = Array.from(
    { length: LOG_TRIM + 2 },
    (_, i) => ({ type: "line", text: `н${i}` }),
  );
  const page = await runView(
    { id: RUN_JOB, action: "ner", project: "ACTIVE/Книга", status: "running",
      lines: [], events: [], progress: null },
    [{ type: "snapshot", lines: snapshot, events: [], progress: null },
     ...lines,
     { type: "status", status: "done" }],
  );
  await settled();
  // узел перечитывается: смена статуса перерисовывает панель целиком
  const shown = logText(page.querySelector(".log-area")).split("\n");
  // окно снимает старое целыми кусками: перебор не больше одного куска
  assert.ok(shown.length <= LOG_LIMIT + LOG_CHUNK,
    `окно не растёт с буфером: строк ${shown.length}`);
  assert.equal(shown[shown.length - 1], `н${LOG_TRIM + 1}`, "хвост — последняя строка");
  /* окно держит ПОСЛЕДНИЕ строки: старые куски сняты целыми, окно непрерывно */
  const first = Number(shown[0].slice(1));
  assert.ok(first > 0, `начало окна сдвинуто вперёд: ${shown[0]}`);
  assert.equal(shown.length, LOG_LIMIT - first + LOG_TRIM + 2,
    "окно непрерывно: ни одной выпавшей строки");
  delete globalThis.__sseDelay;
});

test("живой лог: пустой прогон — заглушка, после первой строки собирается окно", async () => {
  const job = { id: RUN_JOB, action: "ner", project: "ACTIVE/Книга",
    status: "running", lines: [], events: [], progress: null };
  const page = await runView(job, [
    { type: "snapshot", lines: [], events: [], progress: null },
    { type: "status", status: "done" },
  ]);
  await settled();
  assert.equal(page.querySelector(".log-area").textContent, "(лог пуст)");

  /* переподключение снимка: панель пересобирается с нуля, строки не двоятся */
  const again = await runView(job, [
    { type: "snapshot", lines: ["a", "b"], events: [], progress: null },
    { type: "snapshot", lines: ["a", "b", "c"], events: [], progress: null },
    { type: "line", text: "d" },
    { type: "status", status: "done" },
  ]);
  await settled();
  assert.equal(logText(again.querySelector(".log-area")), "a\nb\nc\nd",
    "повторный снимок заменяет окно, а не дописывается к нему");
});

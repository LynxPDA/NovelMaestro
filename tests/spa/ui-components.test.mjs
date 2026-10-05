/* Юнит-тесты web/static/ui-components.js — общий DOM-слой SPA.
 * Запуск: node --test tests/spa/*.test.mjs
 * DOM здесь свой и крошечный (узлы-заглушки): сети нет, браузер не нужен. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const _require = createRequire(import.meta.url); // _: анализатор путает с глобалом

/* ── минимальный DOM ────────────────────────────────────────────────
 * Только то, что реально зовёт ui-components: атрибуты, дети, слушатели,
 * style, remove/focus/select. click() дёргает слушатели с {target: this} —
 * так же, как в браузере проверяется «клик по фону оверлея». */
class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.childNodes = [];
    this.attrs = {};
    this.style = {};
    this.dataset = {};
    this.listeners = {};
    this.isConnected = false;
    this._text = "";
    this._value = "";
    this.offsetWidth = 10;
    this.offsetHeight = 10;
  }

  get className() {
    return this.attrs.class || "";
  }

  set className(v) {
    this.attrs.class = String(v);
  }

  setAttribute(k, v) {
    this.attrs[k] = String(v);
  }

  get srcdoc() {
    return this.attrs.srcdoc || "";
  }

  set srcdoc(v) {
    this.attrs.srcdoc = String(v);
  }

  get textContent() {
    return this._text || this.childNodes.map((c) => c.textContent).join("");
  }

  set textContent(v) {
    this._text = String(v);
    this.childNodes = [];
  }

  get value() {
    return this._value;
  }

  set value(v) {
    this._value = String(v);
  }

  append(...ns) {
    for (const n of ns) {
      n.isConnected = true;
      this.childNodes.push(n);
    }
  }

  replaceChildren(...ns) {
    for (const c of this.childNodes) c.isConnected = false;
    this.childNodes = [];
    this._text = "";
    this.append(...ns);
  }

  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }

  querySelector() {
    return null;
  }

  get firstChild() {
    return this.childNodes[0] || null;
  }

  remove() {
    this.isConnected = false;
  }

  focus() {
    this.focused = true;
  }

  select() {
    this.selected = true;
  }

  click() {
    if (this.attrs.disabled === "true") return; // disabled не кликается
    for (const fn of this.listeners.click || []) fn({ target: this });
  }

  /* обход: найти узел по классу (для проверок каркаса модалки) */
  find(cls) {
    if (this.className.split(/\s+/).includes(cls)) return this;
    for (const c of this.childNodes) {
      if (!(c instanceof El)) continue;
      const r = c.find(cls);
      if (r) return r;
    }
    return null;
  }
}

globalThis.Node = El;
const document = {
  createElement: (t) => new El(t),
  createTextNode: (s) => {
    const n = new El("#text");
    n._text = String(s);
    return n;
  },
  body: new El("body"),
  importNode: (n) => n,
};
globalThis.document = document;
globalThis.DOMParser = class {
  parseFromString() {
    return { documentElement: { nodeName: "svg", querySelector: () => null }, };
  }
};
globalThis.window = globalThis;
globalThis.UICore = _require("../../web/static/ui-core.js");

const UIC = _require("../../web/static/ui-components.js");

/* ── фабрика элементов ────────────────────────────────────────────── */
test("h: attrs = null валиден", () => {
  const n = UIC.h("div", null, "текст");
  assert.equal(n.tagName, "DIV");
  assert.equal(n.textContent, "текст");
});

test("h: false и null в атрибутах не ставятся, 0 — ставится", () => {
  const n = UIC.h("button", { disabled: false, title: null, tabindex: 0 });
  assert.equal(n.attrs.disabled, undefined);
  assert.equal(n.attrs.title, undefined);
  assert.equal(n.attrs.tabindex, "0");
});

test("h: class — className, text — textContent", () => {
  const n = UIC.h("span", { class: "a b", text: "внутри" });
  assert.equal(n.className, "a b");
  assert.equal(n.textContent, "внутри");
});

test("h: on* вешает слушателя, а не атрибут", () => {
  let hits = 0;
  const n = UIC.h("button", { onclick: () => hits++, onmouseover: () => hits++ });
  n.click();
  assert.equal(hits, 1);
  assert.equal(n.attrs.onclick, undefined);
});

test("h: value — свойство (textarea не заполняется атрибутом)", () => {
  const ta = UIC.h("textarea", { value: "глава 1" });
  assert.equal(ta.value, "глава 1");
  assert.equal(ta.attrs.value, undefined);
});

test("h: дети flat, текст — текстовые узлы, null пропускается", () => {
  const n = UIC.h("div", {}, ["a", null, UIC.h("b", {}, "c")]);
  assert.equal(n.childNodes.length, 2); // null пропущен
  assert.equal(n.textContent, "ac");
});

/* ── каркас модалки ───────────────────────────────────────────────── */
test("modal: заголовок, тело, вешается на body", () => {
  const m = UIC.modal({
    title: "Новый столбец",
    build: () => [UIC.h("input", { class: "inp" }), UIC.h("div", { class: "x" })],
  });
  assert.ok(m.isConnected);
  assert.equal(m.className, "modal-backdrop");
  const card = m.firstChild;
  assert.equal(card.className, "modal");
  assert.equal(card.find("modal-title").textContent, "Новый столбец");
  assert.ok(card.find("inp"));
  m.close();
  assert.equal(m.isConnected, false);
});

test("modal: wide-карточка и один ребёнок тела", () => {
  const m = UIC.modal({ wide: true, build: () => UIC.h("i", {}, "раз") });
  assert.equal(m.firstChild.className, "modal modal-wide");
  assert.equal(m.firstChild.textContent, "раз");
});

test("modal: клик по фону закрывает, клик внутри карточки — нет", () => {
  const m = UIC.modal({ title: "T", build: () => UIC.h("i", {}, "x") });
  m.firstChild.click(); // клик по карточке
  assert.equal(m.isConnected, true);
  m.click(); // клик по оверлею
  assert.equal(m.isConnected, false);
});

test("modal: close(result) приходит в onClose результатом", () => {
  let got = "нет";
  const m = UIC.modal({
    title: "T",
    onClose: (r) => (got = r),
    build: (close) =>
      UIC.h("button", { class: "btn", onclick: () => close(true) }, "ОК"),
  });
  m.find("btn").click();
  assert.equal(got, true);
});

test("modal: modal.close — тот же close, что у оверлея", () => {
  const m = UIC.modal({ title: "T", build: () => UIC.h("i", {}, "x") });
  m.close("ок");
  assert.equal(m.isConnected, false);
});

/* Escape вешается на document: в браузере — сам модуль, здесь — тест
   (фиктивный document слушателей не имеет); оверлеи стопятся, и нижний
   закрыт быть не должен — на этом вложенные модалки ломались тише всего */
test("modal: Escape закрывает верхнюю модалку, нижняя живёт", () => {
  const low = UIC.modal({ title: "Разделы", build: () => UIC.h("i", {}, "низ") });
  const top = UIC.modal({ title: "Новый раздел", build: () => UIC.h("i", {}, "верх") });
  assert.ok(low.isConnected && top.isConnected);
  UIC.onKeydown({ key: "Escape" });
  assert.equal(top.isConnected, false);
  assert.equal(low.isConnected, true);
  UIC.onKeydown({ key: "Enter" });
  assert.equal(low.isConnected, true);
  UIC.onKeydown({ key: "Escape" });
  assert.equal(low.isConnected, false);
});

/* ── панель «редактор + предпросмотр» ─────────────────────────────── */
/* doc-обёртки в браузере регистрирует app.js; здесь — заметный фолбэк, чтобы
   видеть, что панель отдаёт им ровно содержимое редактора */
UIC.docs.md = (html) => `DOC(${html})`;
UIC.docs.html = (src) => `DOC(${src})`;

function fakeEditor(text) {
  let v = text;
  return { root: new El("div"), getValue: () => v, setValue: (t) => (v = t) };
}

test("previewPane: код по-прежнему виден, кадр спрятан", () => {
  const pane = UIC.previewPane(fakeEditor("текст"), { small: true });
  assert.equal(pane.mode, "code");
  assert.equal(pane.host.className, "editor-cm editor-cm-small");
  assert.equal(pane.frame.style.display, undefined);
  assert.equal(pane.btn.textContent, "Рендер");
});

test("previewPane: «Рендер» → md (marked), обратно — редактор", () => {
  globalThis.marked = { parse: (s) => `<p>${s}</p>` };
  const pane = UIC.previewPane(fakeEditor("привет"), {});
  pane.btn.click();
  assert.equal(pane.mode, "md");
  assert.equal(pane.host.style.display, "none");
  assert.equal(pane.frame.style.display, "block");
  assert.equal(pane.frame.srcdoc, "DOC(<p>привет</p>)");
  assert.equal(pane.btn.textContent, "Редактор");
  pane.btn.click();
  assert.equal(pane.mode, "code");
  assert.equal(pane.host.style.display, "");
  delete globalThis.marked;
});

test("previewPane: html-файл рендерится как есть, без marked", () => {
  const pane = UIC.previewPane(fakeEditor("<b>x</b>"), { renderMode: "html" });
  pane.btn.click();
  assert.equal(pane.mode, "html");
  assert.equal(pane.frame.srcdoc, "DOC(<b>x</b>)");
});

test("previewPane: без marked — заглушка, не падение", () => {
  const pane = UIC.previewPane(fakeEditor("y"), {});
  pane.setMode("md");
  assert.equal(pane.frame.srcdoc, "DOC(<pre>marked не загружен</pre>)");
});

test("previewPane: повторный setMode не перерисовывает", () => {
  const pane = UIC.previewPane(fakeEditor("z"), {});
  pane.setMode("code");
  assert.equal(pane.btn.textContent, "Рендер");
});

/* ── пейджер списка ───────────────────────────────────────────────── */
test("listPager: страницы и границы", () => {
  const list = new El("ul");
  const pg = UIC.listPager({
    pageSize: 2,
    list,
    rows: (slice) => slice.map((x) => UIC.h("li", {}, String(x))),
  });
  pg.items = [1, 2, 3, 4, 5];
  assert.equal(pg.page, 0);
  assert.equal(list.childNodes.length, 2);
  const next = pg.el.childNodes[2];
  next.click();
  assert.equal(pg.page, 1);
  assert.equal(list.childNodes.length, 2);
  pg.el.childNodes[2].click();
  assert.equal(pg.page, 2);
  assert.equal(list.childNodes.length, 1); // последняя страница неполная
  pg.el.childNodes[2].click(); // дальше идти некуда
  assert.equal(pg.page, 2);
});

test("listPager: одна страница — только счётчик", () => {
  const pg = UIC.listPager({
    pageSize: 200,
    infoOnlySinglePage: true,
    info: (total) => `файлов: ${total}`,
  });
  pg.items = [1, 2, 3];
  assert.equal(pg.el.childNodes.length, 1);
  assert.match(pg.el.textContent, /файлов: 3/);
});

test("listPager: пусто — пусто (списки глав)", () => {
  const pg = UIC.listPager({ pageSize: 50, hideOnEmpty: true });
  pg.items = [];
  assert.equal(pg.el.childNodes.length, 0);
});

test("listPager: подпись счётчика своя", () => {
  const pg = UIC.listPager({
    pageSize: 10,
    info: (total, page, pages) => ` ${page} / ${pages} · отчётов: ${total} `,
  });
  pg.items = new Array(25).fill(0);
  assert.match(pg.el.textContent, /1 \/ 3 · отчётов: 25/);
});

test("listPager: список усох — страница поджимается", () => {
  const pg = UIC.listPager({ pageSize: 10, rows: () => [] });
  pg.items = new Array(100).fill(0);
  pg.page = 9;
  assert.equal(pg.page, 9);
  pg.items = new Array(10).fill(0);
  assert.equal(pg.page, 0);
});

/* ── кнопка-меню ──────────────────────────────────────────────────── */
test("menuButton: пункт закрывает меню и зовёт действие", () => {
  let closed = 0;
  let ran = 0;
  globalThis.closeMenus = () => closed++;
  globalThis.toggleMenu = () => {};
  const el = UIC.menuButton([{ label: "Удалить", action: () => ran++ }],
    { icon: "⋮", title: "Ещё" });
  assert.equal(el.className, "toolbar-menu");
  el.find("menu-item").click();
  assert.equal(closed, 1);
  assert.equal(ran, 1);
  // SVG-иконка вместо текста (меню пользователя) и aria-метка
  const kb = UIC.menuButton([{ label: "Выйти", onclick: () => ran++ }],
    { iconName: "kebab", aria: "Ещё", btnClass: "kebab-btn", wrapClass: "menu-wrap",
      menuClass: "user-menu", itemClass: "user-menu-item" });
  assert.equal(kb.className, "menu-wrap");
  assert.equal(kb.find("kebab-btn").attrs["aria-label"], "Ещё");
  kb.find("user-menu-item").click();
  assert.equal(closed, 2);
});

test("listPager: hideSinglePage убирает панель совсем", () => {
  const list = UIC.h("div", {});
  const pg = UIC.listPager({
    pageSize: 2,
    list,
    hideSinglePage: true,
    rows: (slice) => slice.map((x) => UIC.h("i", {}, x)),
  });
  pg.items = ["a", "b"];
  assert.equal(pg.el.childNodes.length, 0);
  pg.items = ["a", "b", "c"];
  assert.equal(pg.el.childNodes.length, 3);
  pg.page = 1; // присваивание не рисует: рисует render() (его зовёт сам pager)
  pg.render();
  assert.equal(pg.page, 1);
  assert.equal(list.textContent, "c");
});

test("listPager: режим счётчика — items числом, страницу держит компонент", () => {
  let painted = 0;
  const pg = UIC.listPager({
    pageSize: 3,
    info: (total, page, pages) => ` ${page} / ${pages} `,
    onChange: () => (painted += 1),
  });
  pg.items = 7; // не список, а количество: rows не нужны
  assert.equal(pg.el.find("ner-pager-info").textContent, " 1 / 3 ");
  pg.el.childNodes[2].click(); // «›»
  assert.equal(pg.page, 1);
  assert.equal(painted, 1); // перерисовку списка делает вьюха
});

/* ── кнопка поиска редактора (UIC.editorSearch) ───────────────────── */
test("editorSearch: textarea-fallback — кнопки нет вовсе", () => {
  assert.equal(UIC.editorSearch({ isCM: false }), null);
  assert.equal(UIC.editorSearch(null), null);
});

test("editorSearch: кнопка-иконка с подсказкой в тултипе и aria-label", () => {
  const b = UIC.editorSearch({ isCM: true, view: {} });
  assert.ok(b.className.includes("icon-btn"));
  assert.equal(b.attrs.title, "Поиск в тексте (Ctrl+F)");
  assert.equal(b.attrs["aria-label"], "Поиск в тексте (Ctrl+F)");
});

test("editorSearch: клик открывает панель и забирает фокус в поле поиска", () => {
  const opened = [];
  const field = { focused: false, selected: false, focus() { this.focused = true; }, select() { this.selected = true; } };
  const view = { dom: { querySelector: (sel) => (sel.includes("cm-search") ? field : null) } };
  globalThis.CM = { openSearchPanel: (v) => opened.push(v) };
  const b = UIC.editorSearch({ isCM: true, view });
  b.click();
  assert.deepEqual(opened, [view]);
  assert.equal(field.focused, true); // первое открытие панели фокус не отдаёт
  assert.equal(field.selected, true);
});

test("editorSearch: без window.CM клик молчит, редактор не ломается", () => {
  delete globalThis.CM;
  const b = UIC.editorSearch({ isCM: true, view: { dom: { querySelector: () => null } } });
  b.click();
});

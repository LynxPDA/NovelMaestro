/* ui-components.js — общий DOM-слой SPA.
 *
 * Чистые функции (парсинг роута, арифметика прогресса, размещение меню) —
 * в ui-core.js; здесь узлы, которые во всех вьюхах собираются одинаково:
 * фабрика элементов, иконка, каркас модального окна, панель
 * «редактор + предпросмотр», пейджер списка. Классы, aria и поведение
 * (закрытие кликом вне, границы страниц, «одна страница — только счётчик»)
 * существуют в одном месте, а не пересобираются в каждом view.
 *
 * UMD, как ui-core: в Node — module.exports (тесты tests/spa/), в браузере —
 * window.UIC; h и iconEl становятся глобальными, как когда-то в app.js.
 */
((root, factory) => {
  const m = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = m;
    return;
  }
  root.UIC = m;
  root.h = m.h;
  root.iconEl = m.iconEl;
})(typeof self === "undefined" ? this : self, () => {
  /* Фабрика узлов. attrs = null валиден (вызовы вида h("div", null, …));
   * `false`/`null` не ставится вовсе; `on*` — слушатели; `value` — свойство,
   * а не атрибут (textarea: setAttribute("value") содержимое не заполняет);
   * дети flat(), текст идёт через createTextNode. */
  function h(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    // attrs = null валиден (вызовы вида h("div", null, …)) — не падаем
    for (const [k, v] of Object.entries(attrs || {})) {
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

  /* SVG-иконка (24×24, stroke=currentColor) из ui-core.icon(): ответ
   * разбирается DOMParser-ом и импортируется; кривой ответ — пустая обёртка,
   * ряд не рвётся. */
  function iconEl(name, cls) {
    const span = h("span", {
      class: "icon-wrap" + (cls ? " " + cls : ""),
    });
    const doc = new DOMParser().parseFromString(
      window.UICore.icon(name),
      "image/svg+xml",
    );
    const svg = doc.documentElement;
    if (svg && svg.nodeName === "svg" && !svg.querySelector("parsererror")) {
      span.append(document.importNode(svg, true));
    }
    return span;
  }

  /* Каркас модалки: оверлей, .modal/.modal-wide, заголовок и тело. Закрыть
   * можно кликом по фону и кнопками — и там, и там один close(result). Он же
   * возвращается на элементе (modal.close): вьюхе не нужен глобальный указатель
   * на текущий оверлей, а обещание диалога резолвится onClose. */
  function modal(opts) {
    const o = opts || {};
    let backdrop = null;
    function close(result) {
      if (backdrop) backdrop.remove();
      if (o.onClose) o.onClose(result);
      return result;
    }
    const kids = [];
    if (o.title != null) kids.push(h("div", { class: "modal-title" }, o.title));
    const body = o.build ? o.build(close) : [];
    Array.prototype.push.apply(kids, Array.isArray(body) ? body : [body]);
    backdrop = h(
      "div",
      {
        class: "modal-backdrop",
        onclick: (e) => {
          if (e.target === backdrop) close();
        },
      },
      h("div", { class: o.wide ? "modal modal-wide" : "modal" }, ...kids),
    );
    backdrop.close = close;
    document.body.append(backdrop);
    return backdrop;
  }

  /* Кнопка-меню тулбара: .toolbar-menu > кнопка + выпадающий список. Механика
   * открытия/размещения — у app.js (openMenu/toggleMenu/closeMenus), сюда
   * только сборка узлов: пункт закрывает меню и зовёт своё действие. */
  /* Кнопка с выпадающим меню — одна на все случаи: действия строки, меню
   * пользователя, «＋» на панели глоссария. items: [{label, action|onclick,
   * href, danger}]; opts: {icon — текст кнопки, iconName — имя SVG-иконки,
   * title, aria, btnClass, wrapClass, menuClass, itemClass} — дефолты
   * компактные, как в тулбарах. */
  function menuButton(items, opts) {
    const o = opts || {};
    const box = h("div", {
      class: (o.menuClass || "menu-box") + " hidden",
      role: "menu",
    });
    for (const it of items) {
      const cls =
        (o.itemClass || "btn btn-sm btn-ghost menu-item") +
        (it.danger ? " user-menu-danger" : "");
      if (it.href) {
        box.append(h("a", { class: cls, href: it.href, role: "menuitem" }, it.label));
      } else {
        box.append(
          h(
            "button",
            {
              class: cls,
              role: "menuitem",
              onclick: () => {
                window.closeMenus();
                const run = it.action || it.onclick;
                if (run) run();
              },
            },
            it.label,
          ),
        );
      }
    }
    const btn = h(
      "button",
      {
        class: "btn btn-sm btn-ghost" + (o.btnClass ? " " + o.btnClass : ""),
        title: o.title || o.aria || "",
        "aria-label": o.aria || o.title || "",
        "aria-haspopup": "menu",
        "aria-expanded": "false",
        onclick: () => window.toggleMenu(btn, box),
      },
      o.iconName ? iconEl(o.iconName) : String(o.icon == null ? "⋮" : o.icon),
    );
    return h("div", { class: o.wrapClass || "toolbar-menu" }, btn, box);
  }

  /* Уведомление о завершении запуска просит разрешение на пользовательском
   * действии (нажатии «Запустить»): браузеры запрос вне жеста режут. */
  function askNotifyPermission() {
    if (typeof Notification === "undefined") return;
    if (Notification.permission === "default") {
      const p = Notification.requestPermission();
      if (p && typeof p.catch === "function") p.catch(() => {});
    }
  }

  /* Doc-обёртки предпросмотра: тема и кегль живут там, где карточка настроек
   * (app.js), а панель их только зовёт. Регистрация — одна строка в app.js;
   * без неявный fолбэк: кадр получает текст как есть. */
  const docs = {
    md: (html) => html,
    html: (src) => src,
    fit: () => {},
  };

  /* Панель «редактор + предпросмотр»: хост CodeMirror и sandbox-iframe
   * (allow-same-origin — скрипты не выполняются) в одной карточке, режимы
   * переключаются кнопкой: code — редактор, md — marked, html — как есть.
   * renderMode — что показывать кнопкой (у .html-файлов это html). */
  function previewPane(ed, opts) {
    const o = opts || {};
    const pane = {
      mode: "code", // code | md | html
      renderMode: o.renderMode || "md",
      host: h(
        "div",
        { class: "editor-cm" + (o.small ? " editor-cm-small" : "") },
        ed.root,
      ),
      frame: h("iframe", {
        class:
          "editor-preview-frame preview-adaptive" +
          (o.frameClass ? " " + o.frameClass : ""),
        sandbox: "allow-same-origin",
        title: "предпросмотр",
      }),
      btn: h(
        "button",
        { class: "btn btn-sm btn-ghost", title: "Показать отрендеренный вид" },
        "Рендер",
      ),
    };
    function render() {
      if (pane.mode === "code") return;
      if (pane.mode === "md") {
        const html = window.marked
          ? window.marked.parse(ed.getValue(), {
              mangle: false,
              headerIds: false,
            })
          : "<pre>marked не загружен</pre>";
        pane.frame.srcdoc = docs.md(html);
      } else if (pane.mode === "html") {
        pane.frame.srcdoc = docs.html(ed.getValue());
      }
    }
    function setMode(next) {
      if (next === pane.mode) return;
      pane.mode = next;
      const code = pane.mode === "code";
      pane.btn.textContent = code ? "Рендер" : "Код";
      pane.host.style.display = code ? "" : "none";
      pane.frame.style.display = code ? "none" : "block";
      if (!code) render();
    }
    pane.render = render;
    pane.setMode = setMode;
    pane.frame.addEventListener("load", () => docs.fit(pane.frame));
    pane.btn.addEventListener("click", () => {
      setMode(pane.mode === "code" ? pane.renderMode : "code");
    });
    return pane;
  }

  /* Пейджер списка: ‹ n/N · подпись ›. Данные (pg.items) и список (o.list)
   * остаются у вьюхи: компонент режет страницу, зовёт rows(slice, page) и
   * перерисовывает себя. Разные списки отличаются только подписью и тем, что
   * делать на одной странице и на пустом списке: infoOnlySinglePage оставляет
   * один счётчик, hideSinglePage убирает панель совсем, hideOnEmpty — молчит
   * на пустом. */
  function listPager(opts) {
    const o = opts || {};
    const pageSize = Math.max(1, o.pageSize || 200);
    const st = { page: 0, items: [] };
    const el = h("div", { class: "ner-pager" });
    function pages() {
      return Math.max(1, Math.ceil(st.items.length / pageSize));
    }
    function nav(label, disabled, delta) {
      return h(
        "button",
        {
          class: "btn btn-sm btn-ghost",
          disabled,
          onclick: () => {
            st.page = Math.max(0, Math.min(st.page + delta, pages() - 1));
            if (o.onChange) o.onChange(); else render();
          },
        },
        label,
      );
    }
    function info() {
      return h(
        "span",
        { class: "ner-pager-info" },
        o.info
          ? o.info(st.items.length, st.page + 1, pages())
          : ` ${st.page + 1} / ${pages()} · ${o.label || "всего"} ${st.items.length} `,
      );
    }
    /* на одной странице списки молчат про страницы: только счётчик */
    function render() {
      const from = st.page * pageSize;
      if (o.list) o.list.replaceChildren();
      const rows = o.rows
        ? o.rows(st.items.slice(from, from + pageSize), st.page)
        : null;
      if (o.list && rows != null) {
        o.list.append(
          ...(Array.isArray(rows) ? rows : [rows]).filter((x) => x != null),
        );
      }
      if (st.items.length === 0 && o.hideOnEmpty) {
        el.replaceChildren();
      } else if (pages() <= 1 && o.hideSinglePage) {
        el.replaceChildren();
      } else if (o.infoOnlySinglePage && pages() <= 1) {
        el.replaceChildren(info());
      } else {
        el.replaceChildren(
          nav("‹", st.page <= 0, -1),
          info(),
          nav("›", st.page >= pages() - 1, 1),
        );
      }
    }
    return {
      el,
      list: o.list || null,
      get page() {
        return st.page;
      },
      set page(v) {
        st.page = Math.max(0, Math.min(v, pages() - 1));
      },
      /* данные задаются одним присваиванием: страница поджимается под список */
      get items() {
        return st.items;
      },
      /* данные — массив либо число: во втором случае компонент держит только
         счётчик, а строки рисует вьюха (onChange по кнопке страницы) */
      set items(v) {
        st.items = Array.isArray(v) ? v : { length: Number(v) || 0 };
        if (st.page > pages() - 1) st.page = pages() - 1;
        render();
      },
      render,
    };
  }

  return {
    h,
    iconEl,
    modal,
    menuButton,
    previewPane,
    listPager,
    askNotifyPermission,
    docs,
  };
});

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
   * можно кликом по фону, кнопками и Escape — везде один close(result); он же
   * возвращается на элементе (modal.close): вьюхе не нужен глобальный указатель
   * на текущий оверлей, а обещание диалога резолвится onClose. Enter — тот же
   * клик, что по кнопке действия (см. actionButton). */
  const openModals = [];

  /* Классы узла списком: у SVG-элемента className — объект, он в обход не входит. */
  function classes(node) {
    const c = node && typeof node.className === "string" ? node.className : "";
    return c.split(/\s+/).filter(Boolean);
  }

  /* Кнопка действия модалки — та, которую нажимает Enter. Соглашение одно на
   * все вьюхи: в .modal-actions первым идёт «Отмена»/«Закрыть» (btn-ghost),
   * последней — дело (btn-primary/btn-danger). Окну, где это не так, довольно
   * поставить data-modal-default на свою кнопку; у модалки без панели кнопок
   * (просмотр файла) действия нет вовсе. */
  function actionButton(backdrop) {
    let box = null;
    (function walk(n) {
      if (classes(n).includes("modal-actions")) {
        box = n;
        return;
      }
      for (const c of n.childNodes || []) walk(c);
    })(backdrop);
    let mark = null, act = null, last = null;
    for (const b of (box && box.childNodes) || []) {
      const cls = classes(b);
      if (!cls.includes("btn")) continue;
      if (b.getAttribute && b.getAttribute("data-modal-default") != null) mark = b;
      if (cls.includes("btn-primary") || cls.includes("btn-danger")) act = b;
      last = b;
    }
    return mark || act || last;
  }

  /* Escape закрывает ВЕРХНЮЮ модалку: оверлеи стопятся («＋ Раздел» поверх
   * «Управления разделами»), нижняя должна остаться открытой. Enter — тот же
   * клик, что по кнопке действия: поле диалога и так в фокусе, а опасные
   * действия держит слово-пароль confirmModal. Многострочное поле и редактор
   * остаются собой (Enter печатает перенос), как и кнопка/ссылка внутри окна:
   * там работает штатная активация браузера, второй клик не нужен. */
  function onKeydown(e) {
    if (!openModals.length) return;
    const top = openModals[openModals.length - 1];
    if (e.key === "Escape") {
      if (e.stopPropagation) e.stopPropagation();
      if (e.preventDefault) e.preventDefault();
      top.close();
      return;
    }
    if (e.key !== "Enter" || e.isComposing) return;
    const t = e.target;
    const host = t && t.closest ? t.closest(".modal-backdrop") : null;
    // фокус в другом оверлее (палитра, нижняя модалка) — верхняя не при чём
    if (host && host !== top) return;
    if (host) {
      const tag = String(t.tagName || "").toUpperCase();
      const plain = t.isContentEditable || tag === "TEXTAREA";
      if (plain || tag === "BUTTON" || tag === "A") return;
    }
    const btn = actionButton(top);
    // заблокированная кнопка (запись идёт) — Enter молчит, как и клик
    if (!btn || btn.disabled) return;
    if (e.stopPropagation) e.stopPropagation();
    if (e.preventDefault) e.preventDefault();
    btn.click();
  }
  // typeof-гейт, а не try: модуль исполняется и в vm-контексте node-тестов,
  // где document без слушателей (там обработчик вешает сам тест)
  if (typeof document !== "undefined" && document.addEventListener) {
    document.addEventListener("keydown", onKeydown, true);
  }
  function modal(opts) {
    const o = opts || {};
    let backdrop = null;
    function close(result) {
      if (!backdrop) return result;
      backdrop.remove();
      const i = openModals.indexOf(backdrop);
      if (i >= 0) openModals.splice(i, 1);
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
    openModals.push(backdrop);
    document.body.append(backdrop);
    return backdrop;
  }

  /* Кнопка-меню тулбара: .toolbar-menu > кнопка + выпадающий список. Механика
   * открытия/размещения — у app.js (openMenu/toggleMenu/closeMenus), сюда
   * только сборка узлов: пункт закрывает меню и зовёт своё действие. */
  /* Кнопка с выпадающим меню — одна на все случаи: действия строки, меню
   * пользователя, панель настроек вкладки. items: [{label, action|onclick,
   * href, danger}] — кнопка-пункт; {el} — готовый контрол своей вьюхи
   * (чекбокс или кнопка со своим обработчиком: меню его только показывает,
   * состояние живёт у вьюхи); {sep} — разделитель. opts: {icon — текст кнопки, iconName — имя
   * SVG-иконки, title, aria, btnClass, wrapClass, menuClass, itemClass} —
   * дефолты компактные, как в тулбарах. */
  function menuButton(items, opts) {
    const o = opts || {};
    const box = h("div", {
      class: (o.menuClass || "menu-box") + " hidden",
      role: "menu",
    });
    for (const it of items) {
      if (it.sep) {
        box.append(h("div", { class: "menu-sep" }));
        continue;
      }
      // готовый контрол вьюхи: своё состояние он рисует сам
      if (it.el) {
        box.append(it.el);
        continue;
      }
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
   * без неё запасной вариант: кадр получает содержимое как есть (текст — с
   * переносами, markdown — разметкой как есть). */
  const docs = {
    md: (html) => html,
    html: (src) => src,
    text: (src) =>
      "<pre>" +
      String(src == null ? "" : src).replace(/[&<>]/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]) +
      "</pre>",
    fit: () => {},
  };

  /* Строка прогресса запуска: трек + подписи. Один каркас на панель лога
   * «Запусков», карточку активного запуска и строки Дашборда (индикатор
   * шапки компактный и собирается сам, но части берёт те же —
   * UICore.progressParts). Трек тянется до подписей, а подписи (счётчик,
   * фаза, оценка конца) ПЕРЕНОСЯТСЯ строками, а не обрезаются
   * многоточием: на узкой колонке «Запусков» остаток и время конца
   * терялись за краем; не влезает трек — он уходит своей строкой.
   * Мини-вид (mini) — тонкий трек и кегль меньше. Возвращает {el, paint}:
   * узлы подписей постоянные, paint только меняет ширину заполнения и
   * текст (пересоздавать их нельзя — под ногами у SSE). */
  function progressRow(opts) {
    const o = opts || {};
    const fill = h("div", { class: "progress-fill", style: "width:0%" });
    const count = h("span", { class: "progress-count" });
    const step = h("span", { class: "progress-step" });
    const eta = h("span", {
      class: "progress-eta",
      title: "Оценка времени — по текущей скорости выполнения; она зависит "
        + "от размера глав и скорости LLM-сервера",
    });
    const el = h(
      "div",
      { class: "progress-row" + (o.mini ? " progress-mini" : "") },
      h("div", { class: "progress-track" }, fill),
      h("div", { class: "progress-info" }, count, step, eta),
    );
    return {
      el,
      paint(p) {
        const v = p || {};
        /* ширина — атрибутом style: тот же канал, что и при создании
           (в тестах узел-заглушка хранит inline-стиль атрибутом) */
        fill.setAttribute("style", "width:" + (Number(v.pct) || 0) + "%");
        count.textContent = v.count || "";
        step.textContent = v.label || "";
        eta.textContent = v.eta || "";
        /* состояние «ждаём первое событие» — отдельным классом: строка
           видна, но пустых разделителей в ней нет */
        const base = "progress-row" + (o.mini ? " progress-mini" : "");
        el.className = base + (v.waiting ? " progress-waiting" : "");
        return el;
      },
    };
  }

  /* Панель «редактор + предпросмотр»: хост CodeMirror и sandbox-iframe
   * (allow-same-origin — скрипты не выполняются) в одной карточке, режимы
   * переключаются кнопкой: code — редактор, md — marked, html — как есть,
   * text — обычный текст с переносами. renderMode — что показывать кнопкой;
   * его даёт UICore.previewMode(расширение): у markdown одиночные переносы
   * схлопываются в абзац, поэтому текстовым файлам markdown не показывают. */
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
      if (pane.mode === "text") {
        pane.frame.srcdoc = docs.text(ed.getValue());
        return;
      }
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
      // название кнопки — то, что она показывает: «Рендер» или «Редактор»
      pane.btn.textContent = code ? "Рендер" : "Редактор";
      pane.btn.title = code
        ? "Показать отрендеренный вид"
        : "Показать редактор";
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

  /* Кнопка «Поиск» редактора. Панель поиска CodeMirror открывается только
   * Ctrl+F — в узком тулбаре о ней легко забыть, поэтому у каждого редактора
   * своя кнопка-иконка. Первое открытие панели фокус в себя не отдаёт (он
   * остаётся в тексте), поэтому поле поиска фокусируем сами; повторный клик
   * openSearchPanel делает сам. Без CodeMirror (textarea-fallback) возвращает
   * null: своего движка поиска в урезанном режиме нет. */
  function editorSearch(ed, opts) {
    const o = opts || {};
    if (!ed || !ed.isCM) return null;
    const tip = o.tip || "Поиск в тексте (Ctrl+F)";
    const btn = h(
      "button",
      {
        class: "btn btn-sm btn-ghost icon-btn",
        type: "button",
        title: tip,
        "aria-label": tip,
        onclick: () => {
          const CM = window.CM;
          if (!CM || !CM.openSearchPanel || !ed.view) return;
          CM.openSearchPanel(ed.view);
          const field = ed.view.dom.querySelector(
            '.cm-search [main-field="true"]'
          );
          if (field) {
            field.focus();
            field.select();
          }
        },
      },
      iconEl("search"),
    );
    // attachTooltip живёт в app.js; в node-тестах SPA его нет
    if (typeof attachTooltip === "function") attachTooltip(btn, tip);
    return btn;
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
    onKeydown,
    actionButton,
    menuButton,
    previewPane,
    editorSearch,
    progressRow,
    listPager,
    askNotifyPermission,
    docs,
  };
});

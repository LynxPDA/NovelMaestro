/* ui-core.js — чистые функции SPA (раунд 21).
 *
 * Вынесены из app.js / run-views.js / project-views.js для юнит-тестов
 * node --test (tests/spa/ui-core.test.mjs). UMD: в браузере — window.UICore,
 * в Node — module.exports. Без зависимостей и без DOM.
 */
((root, factory) => {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.UICore = factory();
  }
})(typeof self === "undefined" ? this : self, () => {
  var UICore = {
    /* ── роутер: "#/run/a/b" → {view, rest} ── */
    parseRoute: (hash) => {
      var h = String(hash || "")
        .replace(/^#\/?/, "")
        .replace(/^\//, "");
      var parts = h.split("/");
      var view = parts.shift();
      return { view: view || "hub", rest: parts };
    },

    /* ── прогресс: проценты с зажимом 0..100 ── */
    progressPct: (done, total) => {
      var t = total ? total : 0;
      return t > 0
        ? Math.min(100, Math.round((100 * (done ? done : 0)) / t))
        : 0;
    },

    /* ── текстовая строка прогресса (тулбар лога) ── */
    progressText: (p, running) => {
      if (!p) return running ? "📊 ожидание первого результата…" : "";
      var total = p.total || 0;
      var label = p.label || "";
      return total > 0
        ? "📊 " + label + " " + p.done + "/" + total
        : "📊 " + label + " " + p.done + " …";
    },

    /* ── C/D: bool из .env-строк ("0"/"1"/"true"/…) ── */
    boolOn: (v) => {
      if (v === true || v === 1) return true;
      var s = String(v == null ? "" : v)
        .trim()
        .toLowerCase();
      return s === "1" || s === "true" || s === "yes" || s === "on";
    },

    /* ── basename файла (пути с "/" и "\\") ── */
    fileBase: (v) => {
      var s = String(v || "").replace(/\\/g, "/");
      var i = s.lastIndexOf("/");
      return i < 0 ? s : s.slice(i + 1);
    },

    /* ── валидация кегля рендера: значение из опций, иначе дефолт ── */
    clampFont: (n, options, def) => {
      var v = parseInt(n, 10);
      return options.includes(v) ? v : def;
    },

    /* ── свёртка событий конвейера: {id:stage → status} ── */
    chapterByKey: (events) => {
      var byKey = {};
      for (var i = 0; i < (events || []).length; i++) {
        var ev = events[i];
        byKey[ev.id + ":" + ev.stage] = ev.status;
      }
      return byKey;
    },

    /* ── дерево каталога из плоского списка путей (шаблоны) ── */
    dirEntries: (files, prefix) => {
      var entries = [];
      var seen = {};
      for (var i = 0; i < (files || []).length; i++) {
        var f = files[i];
        if (prefix && f.indexOf(prefix + "/") !== 0) continue;
        var rest = prefix ? f.slice(prefix.length + 1) : f;
        if (!rest) continue; // раунд 22: пустой каталог ("source/") при входе в него
        var parts = rest.split("/");
        if (parts.length > 1) {
          var dir = parts[0];
          if (!seen[dir]) {
            seen[dir] = true;
            entries.push({ dir: true, name: dir });
          }
        } else {
          entries.push({ dir: false, name: rest });
        }
      }
      return entries;
    },
  };

  return UICore;
});

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
  /* экранирование regex-спецсимволов (для матчера глоссария) */
  function escapeRe(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

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

    /* ── матчер глоссария (раунд 23): термины → чанки с regex ── */
    buildGlossaryMatcher: (items) => {
      // оба поля: term И translation (NFC при построении); длинные раньше;
      // поиск регистронезависимый («Секта» в начале предложения найдёт «секта»)
      var terms = [];
      for (var i = 0; i < (items || []).length; i++) {
        var it = items[i] || {};
        var t = String(it.term || "")
          .trim()
          .normalize("NFC");
        var tr = String(it.translation || "")
          .trim()
          .normalize("NFC");
        if (t) terms.push({ text: t, key: t.toLowerCase(), item: it });
        if (tr && tr !== t) terms.push({ text: tr, key: tr.toLowerCase(), item: it });
      }
      terms.sort(function (a, b) {
        var d = b.text.length - a.text.length;
        return d !== 0 ? d : a.text < b.text ? -1 : a.text > b.text ? 1 : 0;
      });
      var CHUNK = 2000;
      var chunks = [];
      for (var i = 0; i < terms.length; i += CHUNK) {
        var part = terms.slice(i, i + CHUNK);
        chunks.push({
          re: new RegExp(
            part
              .map(function (t) {
                return escapeRe(t.text);
              })
              .join("|"),
            "gi",
          ),
          terms: part,
        });
      }
      return { chunks: chunks, total: terms.length };
    },

    /* ── совпадения матчера в тексте: [{from, to, item}] ── */
    glossaryMatches: (text, matcher) => {
      var out = [];
      var src = String(text || "");
      if (!matcher || !matcher.chunks) return out;
      for (var c = 0; c < matcher.chunks.length; c++) {
        var chunk = matcher.chunks[c];
        chunk.re.lastIndex = 0;
        var m;
        while ((m = chunk.re.exec(src)) !== null) {
          var hit = m[0];
          var hitKey = hit.toLowerCase();
          var item = null;
          // длинные раньше — первое совпадение и есть искомый термин;
          // сравнение по ключу (регистронезависимо)
          for (var k = 0; k < chunk.terms.length; k++) {
            if (chunk.terms[k].key === hitKey) {
              item = chunk.terms[k].item;
              break;
            }
          }
          out.push({ from: m.index, to: m.index + hit.length, item: item });
          if (m.index === chunk.re.lastIndex) chunk.re.lastIndex++; // пустых нет, но защита
        }
      }
      // дедуп перекрытий между чанками: длиннейшее по позиции (длинные раньше)
      out.sort(function (a, b) {
        return a.from - b.from || b.to - a.to;
      });
      var dedup = [];
      var lastTo = -1;
      for (var i = 0; i < out.length; i++) {
        if (out[i].from < lastTo) continue;
        dedup.push(out[i]);
        lastTo = out[i].to;
      }
      return dedup;
    },
  };

  return UICore;
});

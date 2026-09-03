/* ui-core.js — чистые функции SPA.
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

  /* ── иконки файлов (карта расширений; кросс-файловый хелпер) ── */
  var F_ICONS = {
    txt: "📄",
    json: "🧾",
    md: "📝",
    yaml: "📋",
    yml: "📋",
    png: "🖼",
    jpg: "🖼",
    jpeg: "🖼",
    webp: "🖼",
    gif: "🖼",
    epub: "📚",
    log: "📜",
    csv: "📊",
    py: "🐍",
  };

  function fileIcon(entry) {
    if (!entry) return "📄";
    if (entry.dir) return "📁";
    var ext = (
      String(entry.name || "")
        .split(".")
        .pop() || ""
    ).toLowerCase();
    return F_ICONS[ext] || "📄";
  }

  /* ── порт логики поиска терминов из core.common / translate_book.py ──
   * normalize_for_search: NFC → lower → убрать пробелы/пунктуацию —
   * одинаково к термину и тексту; точное вхождение — подстрока в норм.
   * тексте (как Aho-Corasick/regex load_ner_data); нечёткое — аналог
   * _fuzzy_hit (пересечение n-грамм >= threshold + общая подстрока >= 0.8). */
  var SEARCH_DROP_RE = /[\s\u3000\u200b.,!?;:()«»"'’‘…—–-]+/g;
  var DROP_CHAR_RE = /[\s\u3000\u200b.,!?;:()«»"'’‘…—–-]/;
  function normalizeForSearch(s) {
    return String(s || "")
      .normalize("NFC")
      .toLowerCase()
      .replace(SEARCH_DROP_RE, "");
  }
  /* комбинирующие диакритики — не рвём NFC-пары при построении карты */
  var COMBINING_RE =
    /^[\u0300-\u036f\u0483-\u0489\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]/;
  /* is_cjk (core.common) */
  function isCjkChar(c) {
    var cp = c.codePointAt(0);
    return (
      (cp >= 0x4e00 && cp <= 0x9fff) ||
      (cp >= 0x3400 && cp <= 0x4dbf) ||
      (cp >= 0x20000 && cp <= 0x2a6df) ||
      (cp >= 0x2a700 && cp <= 0x2b73f) ||
      (cp >= 0xf900 && cp <= 0xfaff) ||
      (cp >= 0x2f800 && cp <= 0x2fa1f) ||
      (cp >= 0x3040 && cp <= 0x309f) ||
      (cp >= 0x30a0 && cp <= 0x30ff) ||
      (cp >= 0xac00 && cp <= 0xd7af)
    );
  }
  function isCjkString(s) {
    if (!s) return false;
    // B9: итерация по code points (for...of), а не UTF-16 code units —
    // суррогатные пары (доп. плоскости, U+20000+) не считаются как 2
    var n = 0;
    var len = 0;
    for (var ch of String(s)) {
      len++;
      if (isCjkChar(ch)) n++;
    }
    return len > 0 && n / len > 0.5;
  }
  /* буква/цифра любого письма — для слово-границ не-CJK терминов */
  var WORD_CHAR_RE = /[\p{L}\p{N}]/u;
  /* get_ngrams (core.common): объект-множество */
  function getNgrams(s, n) {
    var out = {};
    if (!s) return out;
    if (s.length < n) {
      out[s] = true;
      return out;
    }
    for (var i = 0; i + n <= s.length; i++) out[s.slice(i, i + n)] = true;
    return out;
  }
  /* самая длинная общая подстрока — проверка из _fuzzy_hit */
  function longestCommonSubstring(a, b) {
    if (!a || !b) return 0;
    var best = 0;
    var prev = new Array(b.length + 1).fill(0);
    for (var i = 1; i <= a.length; i++) {
      var cur = new Array(b.length + 1).fill(0);
      for (var j = 1; j <= b.length; j++) {
        if (a[i - 1] === b[j - 1]) {
          cur[j] = prev[j - 1] + 1;
          if (cur[j] > best) best = cur[j];
        }
      }
      prev = cur;
    }
    return best;
  }

  /* ── таблица глоссария: ячейка / сортировка / поиск ── */
  function nerCellText(v) {
    return v && typeof v === "object"
      ? JSON.stringify(v)
      : String(v == null ? "" : v);
  }
  /* первый клик по столбцу — убывание, повторный — возрастание */
  function nextNerSort(field, dir, clicked) {
    if (clicked && clicked === field) {
      return { field: clicked, dir: dir === "desc" ? "asc" : "desc" };
    }
    return { field: clicked || field || "count", dir: "desc" };
  }
  function nerTypeAllowed(type, typeFilter) {
    if (typeFilter == null || typeFilter === "") return true;
    if (Array.isArray(typeFilter)) {
      if (!typeFilter.length) return false;
      return typeFilter.indexOf(type) !== -1;
    }
    return type === String(typeFilter);
  }
  function filterNerItems(items, query, fields, typeFilter) {
    var list = items || [];
    var qq = String(query || "")
      .trim()
      .toLowerCase();
    var keys = fields == null ? null : fields;
    return list.filter((it) => {
      var type = String(it.type || "");
      if (!nerTypeAllowed(type, typeFilter)) return false;
      if (!qq) return true;
      if (keys && !keys.length) return false;
      var searchKeys = keys || Object.keys(it).filter((k) => k !== "__new");
      for (var i = 0; i < searchKeys.length; i++) {
        if (nerCellText(it[searchKeys[i]]).toLowerCase().includes(qq)) {
          return true;
        }
      }
      return false;
    });
  }
  function sortNerItems(items, field, dir) {
    var list = (items || []).slice();
    if (!field) return list;
    var sign = dir === "asc" ? 1 : -1;
    list.sort((a, b) => {
      var av = a[field];
      var bv = b[field];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      var numA = Number(av);
      var numB = Number(bv);
      var numeric =
        av !== "" &&
        bv !== "" &&
        Number.isFinite(numA) &&
        Number.isFinite(numB);
      var cmp = numeric
        ? numA - numB
        : nerCellText(av).localeCompare(nerCellText(bv), "ru");
      return cmp * sign;
    });
    return list;
  }

  var UICore = {
    /* ── русские названия полей записи глоссария ──
       единый словарь: вкладка «Глоссарий» (колонки и модалки).
       Чипсы «Поля в запросе LLM» (ner_check) используют РАВНЫЕ
       ключам ner.json названия (term, type, …). */
    nerFieldLabels: {
      term: "Термин",
      type: "Тип",
      translation: "Перевод",
      pinyin: "Пиньинь",
      reading: "Чтение",
      context: "Контекст",
      translated_context: "Перевод контекста",
      notes: "Примечания",
      aliases: "Алиасы",
      count: "Частота",
    },
    nerFieldLabel: (key) => UICore.nerFieldLabels[key] || key,

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

    /* ── выбор файла из пула опций: ТОЛЬКО реально существующие ──
     * Дефолт (из спеки или .env) сравнивается с пулом файлов проекта;
     * файла нет в пуле → "" (не подхватываем): удалённый промпт не
     * должен оставаться выбранным — иначе запуск уходит в per-stage
     * режим и игнорирует автоподхват (напр. pipeline_prompt.txt). */
    pickPoolFile: (def, items) => {
      var baseDef = UICore.fileBase(def);
      if (baseDef && items.includes(baseDef)) return baseDef;
      if (def && items.includes(def)) return def;
      return "";
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
        if (!rest) continue; // пустой каталог ("source/") при входе в него
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

    /* предложение вокруг выделения (добавление термина из chapter.txt):
     * границы 。！？.!?… и перевод строки; не длиннее maxLen СИМВОЛОВ. */
    glossarySentence: (text, from, to, maxLen) => {
      var src = String(text || "");
      var a = Math.max(0, Math.min(Number(from) || 0, src.length));
      var b = Math.max(a, Math.min(Number(to) || 0, src.length));
      var lim = Number(maxLen);
      if (!isFinite(lim) || lim <= 0) lim = 200;
      var isEnd = (ch) => /[。！？.!?…\n]/.test(ch);
      var start = a;
      while (start > 0 && !isEnd(src.charAt(start - 1))) start--;
      var end = b;
      while (end < src.length && !isEnd(src.charAt(end))) end++;
      if (
        end < src.length &&
        isEnd(src.charAt(end)) &&
        src.charAt(end) !== "\n"
      ) {
        end++;
      }
      var sent = src.slice(start, end).replace(/\s+/g, " ").trim();
      if (sent.length <= lim) return sent;
      var termLen = b - a;
      if (termLen >= lim) return src.slice(a, a + lim);
      var left = Math.floor((lim - termLen) / 2);
      var l = Math.max(start, a - left);
      var r = Math.min(end, l + lim);
      if (r - l < lim) l = Math.max(start, r - lim);
      return src.slice(l, r).replace(/\s+/g, " ").trim();
    },

    /* ── матчер глоссария : термины → чанки с regex ──
     * ngramSize — размер n-граммы нечёткого поиска (аналог --ner_ngram
     * в translate_book.py, дефолт 3); threshold — порог пересечения н-грамм,
     * выше = строже (аналог --ner_threshold, дефолт 0.75).
     * Термины нормализуются как в core.common.normalize_for_search;
     * длинные раньше. */
    buildGlossaryMatcher: (items, ngramSize, threshold) => {
      var n =
        ngramSize && ngramSize >= 1 && isFinite(ngramSize)
          ? Math.floor(ngramSize)
          : 3;
      var th =
        threshold != null && isFinite(threshold)
          ? Math.min(1, Math.max(0, threshold))
          : 0.75;
      var terms = [];
      var seen = {};
      for (var i = 0; i < (items || []).length; i++) {
        var it = items[i] || {};
        var t = normalizeForSearch(it.term);
        var tr = normalizeForSearch(it.translation);
        if (t && !seen[t]) {
          seen[t] = true;
          terms.push({
            norm: t,
            isCjk: isCjkString(t),
            item: it,
            ngrams: getNgrams(t, n),
          });
        }
        if (tr && tr !== t && !seen[tr]) {
          seen[tr] = true;
          terms.push({
            norm: tr,
            isCjk: isCjkString(tr),
            item: it,
            ngrams: getNgrams(tr, n),
          });
        }
      }
      terms.sort((a, b) => {
        var d = b.norm.length - a.norm.length;
        return d === 0 ? (a.norm < b.norm ? -1 : a.norm > b.norm ? 1 : 0) : d;
      });
      var CHUNK = 2000;
      var chunks = [];
      for (var c = 0; c < terms.length; c += CHUNK) {
        var part = terms.slice(c, c + CHUNK);
        chunks.push({
          re: new RegExp(part.map((t) => escapeRe(t.norm)).join("|"), "g"),
          terms: part,
        });
      }
      return {
        chunks: chunks,
        total: terms.length,
        ngramSize: n,
        threshold: th, // дефолт 0.75; --ner_threshold в translate_book = 0.7
      };
    },

    /* ── совпадения матчера в тексте: [{from, to, item}] ──
     * 1) точные: подстрока в нормализованном тексте (аналог Aho-Corasick);
     *    для не-CJK обязательны слово-границы в ОРИГИНАЛЬНОМ тексте;
     * 2) нечёткие (не-CJK, длина нормы >= 3): пересечение n-грамм >=
     *    threshold + общая подстрока >= 0.8 длины (аналог _fuzzy_hit) —
     *    ловит склонения; диапазон расширяется до целого слова. */
    glossaryMatches: (text, matcher) => {
      var out = [];
      var src = String(text || "");
      if (!matcher || !matcher.chunks || !src) return out;
      var threshold = matcher.threshold == null ? 0.75 : matcher.threshold;
      var n = matcher.ngramSize || 3;

      /* нормализация текста + карта «позиция нормы → индекс оригинала» */
      var norm = "";
      var map = [];
      for (var i = 0; i < src.length; i++) {
        var ch = src[i];
        if (i > 0 && COMBINING_RE.test(ch)) continue; // слилось с предыдущим
        if (DROP_CHAR_RE.test(ch)) continue;
        var nc = ch.normalize("NFC").toLowerCase();
        for (var k = 0; k < nc.length; k++) {
          map.push(i);
          norm += nc[k];
        }
      }
      if (norm.length !== map.length) return out; // защита, не должно случиться
      var wordBound = (of, ot) => {
        // не буква/цифра вокруг в ОРИГИНАЛЕ (пробелы/пунктуация нормализацией убраны)
        if (of > 0 && WORD_CHAR_RE.test(src[of - 1])) return false;
        if (ot < src.length && WORD_CHAR_RE.test(src[ot])) return false;
        return true;
      };

      /* 1) точные совпадения */
      var ci, q;
      for (ci = 0; ci < matcher.chunks.length; ci++) {
        var chunk = matcher.chunks[ci];
        chunk.re.lastIndex = 0;
        var m;
        while ((m = chunk.re.exec(norm)) !== null) {
          var nf = m.index;
          var nt = nf + m[0].length;
          var entry = null;
          for (q = 0; q < chunk.terms.length; q++) {
            if (chunk.terms[q].norm === m[0]) {
              entry = chunk.terms[q];
              break;
            }
          }
          var of = map[nf];
          var ot = map[nt - 1] + 1;
          if (entry && !entry.isCjk && !wordBound(of, ot)) continue;
          out.push({ from: of, to: ot, item: entry ? entry.item : null });
          if (m.index === chunk.re.lastIndex) chunk.re.lastIndex++;
        }
      }

      /* 2) нечёткие: только не-CJK с нормой >= 3 (как _fuzzy_hit в core) */
      var cands = [];
      for (ci = 0; ci < matcher.chunks.length; ci++) {
        var tt = matcher.chunks[ci].terms;
        for (q = 0; q < tt.length; q++) {
          var e = tt[q];
          if (!e.isCjk && e.norm.length >= 3) cands.push(e);
        }
      }
      if (cands.length && norm.length >= n) {
        /* инвертированный индекс n-грамм нормализованного текста */
        var idx = new Map();
        for (var p = 0; p + n <= norm.length; p++) {
          var g = norm.slice(p, p + n);
          var arr = idx.get(g);
          if (arr) {
            if (arr.length < 2000) arr.push(p); // часто повт. граммы — не раздувать
          } else idx.set(g, [p]);
        }
        var budget = 500000; // предохранитель на большой текст/глоссарий
        for (ci = 0; ci < cands.length && budget > 0; ci++) {
          var ent = cands[ci];
          var en = ent.norm;
          if (en.length < n) continue;
          var firstGram = en.slice(0, n);
          var positions = idx.get(firstGram);
          if (!positions) continue;
          var engrams = ent.ngrams || getNgrams(en, n);
          var gramKeys = Object.keys(engrams);
          var limit = Math.min(positions.length, 2000);
          for (var pi = 0; pi < limit && budget > 0; pi++) {
            var s = positions[pi];
            if (s + en.length > norm.length) continue;
            var o1 = map[s];
            var o2 = map[s + en.length - 1] + 1;
            /* окно целиком внутри одного слова: нормализация выбрасывает
               пробелы/пунктуацию, и окно через границу слов («этажа — лишь»
               для «А-Ли») обязано быть отклонено */
            var spanOk = true;
            for (var sc2 = o1; sc2 < o2; sc2++) {
              if (!WORD_CHAR_RE.test(src[sc2])) {
                spanOk = false;
                break;
              }
            }
            if (!spanOk) continue;
            budget--;
            /* расширение до целого слова (падежи: «Хунга» для «Хунг») */
            var f2 = o1;
            var guard = 0;
            while (f2 > 0 && WORD_CHAR_RE.test(src[f2 - 1]) && guard++ < 8)
              f2--;
            var t2 = o2;
            guard = 0;
            while (t2 < src.length && WORD_CHAR_RE.test(src[t2]) && guard++ < 8)
              t2++;
            var wordNorm = normalizeForSearch(src.slice(f2, t2));
            if (!wordNorm) continue;
            /* принять: слово = термин; корень + окончание/падеж (термин —
               префикс слова, не длиннее +3 — иначе «который» матчится на «кот»,
               «морали»/«залил» — на «А-Ли»); либо строгий аналог _fuzzy_hit
               с жёстким ограничением лишних n-грамм слова */
            var hitWord = wordNorm === en;
            if (!hitWord && wordNorm.length <= en.length + 3) {
              hitWord = wordNorm.startsWith(en);
            }
            if (!hitWord) {
              var wgrams = getNgrams(wordNorm, n);
              var inter = 0;
              for (var wg in wgrams) if (engrams[wg]) inter++;
              if (
                inter / gramKeys.length >= threshold &&
                Object.keys(wgrams).length <= gramKeys.length + 1 &&
                /* общая подстрока >= доли длины по порогу (не жёсткие 0.8):
                   «нити» от «нить» (3/4 = 0.75) находится при пороге 0.1
                   и даже 0.75; «который»/«морали» отсекаются лишними
                   n-граммами выше */
                longestCommonSubstring(en, wordNorm) >= en.length * threshold
              ) {
                hitWord = true;
              }
            }
            if (!hitWord) continue;
            out.push({ from: f2, to: t2, item: ent.item });
          }
        }
      }

      /* дедуп перекрытий: сортировка по позиции, длиннейшее раньше */
      out.sort((a, b) => a.from - b.from || b.to - a.to);
      var dedup = [];
      var lastTo = -1;
      for (var di = 0; di < out.length; di++) {
        if (out[di].from < lastTo) continue;
        dedup.push(out[di]);
        lastTo = out[di].to;
      }
      return dedup;
    },

    /* ── review-файлы проверок (ner_review.json / …_llm_review.json) ──
     * Формат: объект {created, updated, input, entries:[…]} или legacy-
     * массив записей. Разбор и точечные правки — без серверного роута
     * (чтение → обновление → PUT целиком). */
    parseReviewContent: (text) => {
      var doc = null;
      try {
        doc = JSON.parse(String(text || "").trim());
      } catch {
        return { ok: false, doc: null, entries: [], isArray: false };
      }
      if (Array.isArray(doc))
        return { ok: true, doc: doc, entries: doc, isArray: true };
      if (doc && typeof doc === "object" && Array.isArray(doc["entries"])) {
        return { ok: true, doc: doc, entries: doc["entries"], isArray: false };
      }
      return { ok: false, doc: null, entries: [], isArray: false };
    },

    /* иммутабельное обновление одной записи: возвращает новый doc
     * (объект с «правки» или массив); «обновлён» проставляется текущим
     * временем. Индекс вне диапазона — null (не меняем файл). */
    updateReviewEntry: (doc, index, patch, isArray) => {
      if (!doc || !patch || typeof index !== "number") return null;
      var entries = isArray ? doc : doc["entries"];
      if (!Array.isArray(entries) || index < 0 || index >= entries.length) {
        return null;
      }
      var next = isArray ? doc.slice() : Object.assign({}, doc);
      var nextEntries = entries.slice();
      nextEntries[index] = Object.assign({}, entries[index], patch);
      if (isArray) {
        return nextEntries;
      }
      next["entries"] = nextEntries;
      next["updated"] = new Date().toISOString().slice(0, 16).replace("T", " ");
      return next;
    },

    /* иконка файла по имени/папке (карта F_ICONS) */
    fileIcon: fileIcon,

    /* иммутабельное удаление записи: новый doc без индекса; «обновлён»
     * проставляется текущим временем. Вне диапазона/без «правки» — null. */
    removeReviewEntry: (doc, index, isArray) => {
      if (!doc || typeof index !== "number") return null;
      var entries = isArray ? doc : doc["entries"];
      if (!Array.isArray(entries) || index < 0 || index >= entries.length) {
        return null;
      }
      var nextEntries = entries
        .slice(0, index)
        .concat(entries.slice(index + 1));
      if (isArray) return nextEntries;
      var next = Object.assign({}, doc);
      next["entries"] = nextEntries;
      next["updated"] = new Date().toISOString().slice(0, 16).replace("T", " ");
      return next;
    },

    /* сводка по записям: всего / принято / отклонено / применено */
    reviewSummary: (entries) => {
      var sum = { total: 0, accepted: 0, rejected: 0, applied: 0 };
      for (var i = 0; i < (entries || []).length; i++) {
        var e = entries[i] || {};
        sum.total++;
        if (e["applied"]) sum.applied++;
        if (e["status"] === "принять") sum.accepted++;
        else if (e["status"] === "отклонить") sum.rejected++;
      }
      return sum;
    },

    nerCellText: nerCellText,
    nextNerSort: nextNerSort,
    isCjkString: isCjkString,
    filterNerItems: filterNerItems,
    sortNerItems: sortNerItems,

    /* ── режим «Простой/Экспертный» в Запусках (localStorage) ── */
    /* выбор запоминается по стадии: runMode = JSON {stage: mode};
       стадия без записи = глобальный дефолт "simple" (новички). */
    runModeGet: (stage) => {
      try {
        var m = JSON.parse(localStorage.getItem("runMode") || "{}");
        if (m && typeof m === "object" && m[stage] === "expert") {
          return "expert";
        }
      } catch (err) { /* нет localStorage/кривой JSON — простой режим */
        void err;
      }
      return "simple";
    },
    runModeSet: (stage, mode) => {
      var m = {};
      try {
        m = JSON.parse(localStorage.getItem("runMode") || "{}");
      } catch (err) { /* перезаписываем */ void err; }
      if (!m || typeof m !== "object") m = {};
      m[stage] = mode === "expert" ? "expert" : "simple";
      try {
        localStorage.setItem("runMode", JSON.stringify(m));
      } catch (err) { /* приватный режим/переполнение — молча */ void err; }
    },
  };

  return UICore;
});

/* Юнит-тесты web/static/ui-core.js.
 * Запуск: node --test tests/spa/ (или npm-скрипт, если появится package.json).
 * Никакой сети и DOM — чистые функции. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const _require = createRequire(import.meta.url); // _: анализатор путает с глобалом
const UICore = _require("../../web/static/ui-core.js");

test("parseRoute: пустой хэш → hub", () => {
  assert.deepEqual(UICore.parseRoute(""), { view: "hub", rest: [] });
  assert.deepEqual(UICore.parseRoute("#"), { view: "hub", rest: [] });
});

test("parseRoute: полный маршрут", () => {
  assert.deepEqual(UICore.parseRoute("#/run/ACTIVE/Book"), {
    view: "run",
    rest: ["ACTIVE", "Book"],
  });
  assert.deepEqual(UICore.parseRoute("/settings"), {
    view: "settings",
    rest: [],
  });
});

test("progressPct: зажим и границы", () => {
  assert.equal(UICore.progressPct(0, 636), 0);
  assert.equal(UICore.progressPct(318, 636), 50);
  assert.equal(UICore.progressPct(636, 636), 100);
  assert.equal(UICore.progressPct(700, 636), 100); // зажим сверху
  assert.equal(UICore.progressPct(0, 0), 0); // нет total
  assert.equal(UICore.progressPct(3, 0), 0);
});

test("progressText: без событий у running-задачи", () => {
  assert.equal(
    UICore.progressText(null, true),
    "📊 ожидание первого результата…",
  );
  assert.equal(UICore.progressText(null, false), "");
});

test("progressText: done/total и label", () => {
  assert.equal(
    UICore.progressText({ done: 12, total: 636, label: "перевод" }, true),
    "📊 перевод 12/636",
  );
  assert.equal(
    UICore.progressText({ done: 5, total: 0, label: "wiki" }, true),
    "📊 wiki 5 …",
  );
});

test("boolOn: строки .env", () => {
  assert.equal(UICore.boolOn("1"), true);
  assert.equal(UICore.boolOn("0"), false);
  assert.equal(UICore.boolOn("true"), true);
  assert.equal(UICore.boolOn("false"), false);
  assert.equal(UICore.boolOn("yes"), true);
  assert.equal(UICore.boolOn("on"), true);
  assert.equal(UICore.boolOn(""), false);
  assert.equal(UICore.boolOn(null), false);
  assert.equal(UICore.boolOn(1), true);
  assert.equal(UICore.boolOn(0), false);
});

test("fileBase: пути со слешами и бэкслешами", () => {
  assert.equal(
    UICore.fileBase("chapters/001/translated.txt"),
    "translated.txt",
  );
  assert.equal(UICore.fileBase("a\\b\\ner.json"), "ner.json");
  assert.equal(UICore.fileBase("plain.txt"), "plain.txt");
  assert.equal(UICore.fileBase(""), "");
});


test("pickPoolFile: только реально существующие файлы", () => {
  const pool = ["pipeline_prompt.txt", "translate_prompt.txt", "ner.json"];
  // basename дефолта есть в пуле → подхват
  assert.equal(
    UICore.pickPoolFile("prompts/translate_prompt.txt", pool),
    "translate_prompt.txt",
  );
  // точное имя (dir="") в пуле
  assert.equal(UICore.pickPoolFile("ner.json", pool), "ner.json");
  // дефолт указывает на файл, которого нет → НЕ подхватываем ("")
  assert.equal(UICore.pickPoolFile("redact_prompt.txt", pool), "");
  assert.equal(UICore.pickPoolFile("prompts/redact_prompt.txt", pool), "");
  // пустой дефолт → ""
  assert.equal(UICore.pickPoolFile("", pool), "");
});

test("clampFont: из опций, иначе дефолт", () => {
  assert.equal(UICore.clampFont(12, [5, 7, 10, 12, 14], 12), 12);
  assert.equal(UICore.clampFont("12", [5, 7, 10, 12, 14], 12), 12);
  assert.equal(UICore.clampFont(9, [5, 7, 10, 12, 14], 12), 12); // не из списка
  assert.equal(UICore.clampFont("", [5, 7, 10, 12, 14], 12), 12);
  assert.equal(UICore.clampFont(7, [5, 7, 10, 12, 14], 12), 7); // легаси 7 → 12
});

test("chapterByKey: свёртка событий", () => {
  const events = [
    { id: 1, stage: 1, status: "OK" },
    { id: 1, stage: 2, status: "ERROR" },
    { id: 2, stage: 1, status: "OK" },
  ];
  assert.deepEqual(UICore.chapterByKey(events), {
    "1:1": "OK",
    "1:2": "ERROR",
    "2:1": "OK",
  });
  assert.deepEqual(UICore.chapterByKey(null), {});
  assert.deepEqual(UICore.chapterByKey([]), {});
});

test("dirEntries: плоский список → дерево каталога", () => {
  const files = [
    "prompts/translate.txt",
    "prompts/redact.txt",
    "prompts/nested/x.txt",
    "metadata.yaml",
    ".env.example",
  ];
  const root = UICore.dirEntries(files, "");
  assert.deepEqual(root.map((e) => `${e.dir ? "d:" : "f:"}${e.name}`).sort(), [
    "d:prompts",
    "f:.env.example",
    "f:metadata.yaml",
  ]);
  const prompts = UICore.dirEntries(files, "prompts");
  assert.deepEqual(
    prompts.map((e) => `${e.dir ? "d:" : "f:"}${e.name}`).sort(),
    ["d:nested", "f:redact.txt", "f:translate.txt"],
  );
  assert.deepEqual(UICore.dirEntries([], ""), []);
});

/* ── матчер глоссария (вкладка «Редактор») ── */

const NER_ITEMS = [
  { term: "Хунг", translation: "Хун", type: "имя", notes: "герой" },
  { term: "секта", translation: "Школа", type: "место", notes: "" },
  { term: "灵草", translation: "灵草", type: "предмет" },
  { term: "Линь", translation: "Линь", type: "имя" },
];

function matcherFor(items) {
  return UICore.buildGlossaryMatcher(items);
}

function hits(text, items) {
  return UICore.glossaryMatches(text, matcherFor(items)).map((m) =>
    text.slice(m.from, m.to),
  );
}

test("buildGlossaryMatcher: оба поля term+translation", () => {
  const m = matcherFor(NER_ITEMS);
  // 4 записи: term Хунг + translation Хун, term секта + translation Школа,
  // термин-дубль (translation === term — не дублируется), Линь
  assert.equal(m.total, 6);
});

test("glossaryMatches: совпадения по обоим полям", () => {
  const text = "Хунг и Школа. Хун улыбнулся.";
  const found = hits(text, NER_ITEMS);
  assert.deepEqual(found, ["Хунг", "Школа", "Хун"]);
});

test("glossaryMatches: регистронезависимо, термин находится", () => {
  const items = [{ term: "Хунг", translation: "секта" }];
  const found = hits("хунг и СЕКТА", items);
  // совпадения — в регистре ИСХОДНОГО текста, поиск регистронезависимый
  assert.deepEqual(found, ["хунг", "СЕКТА"]);
  // item привязан к совпадению несмотря на другой регистр
  const ms = UICore.glossaryMatches("хунг", UICore.buildGlossaryMatcher(items));
  assert.equal(ms.length, 1);
  assert.equal(ms[0].item.term, "Хунг");
});

test("glossaryMatches: дубль термин/перевод не дублирует совпадения", () => {
  const text = "灵草 растёт. Линь собирает 灵草.";
  const found = hits(text, NER_ITEMS);
  // 灵草 и Линь — по одному вхождению каждый, несмотря на дубль полей
  assert.deepEqual(found, ["灵草", "Линь", "灵草"]);
});

test("glossaryMatches: длинные термины раньше коротких", () => {
  const items = [
    { term: "abc", translation: "" },
    { term: "abcd", translation: "" },
  ];
  const text = "abcd";
  const found = hits(text, items);
  assert.deepEqual(found, ["abcd"]); // не "abc" + "d"
});

test("glossaryMatches: спецсимволы экранируются", () => {
  const items = [{ term: "a(b)c", translation: "" }];
  assert.deepEqual(hits("x a(b)c y", items), ["a(b)c"]);
  const items2 = [{ term: "1+1", translation: "" }];
  assert.deepEqual(hits("2 1+1 2", items2), ["1+1"]);
});

test("glossaryMatches: NFC терминов при построении матчера", () => {
  // термин в NFD (e + combining acute) нормализуется в NFC é
  const items = [{ term: "e\u0301", translation: "" }];
  const m = matcherFor(items);
  assert.equal(m.total, 1);
  assert.deepEqual(hits("\u00e9", items), ["\u00e9"]);
});

test("glossaryMatches: пустые/без матчера", () => {
  assert.deepEqual(UICore.glossaryMatches("", matcherFor([])), []);
  assert.deepEqual(UICore.glossaryMatches("x", null), []);
  assert.equal(matcherFor(null).total, 0);
  assert.equal(matcherFor(undefined).total, 0);
});

test("glossaryMatches: слово-границы — нет обрывков внутри слов", () => {
  // короткие термины не матчатся внутри слов (не «от» в «кот»/«кто»)
  const items = [{ term: "от", translation: "" }];
  assert.deepEqual(hits("кот и кто", items), []);
  assert.deepEqual(hits("от кота", items), ["от"]);
  assert.deepEqual(hits("кто от кого", items), ["от"]);
});

test("glossaryMatches: склонения через нечёткий поиск (аналог _fuzzy_hit)", () => {
  // «Хунгу» в тексте: точное «Хунг» отклонено слово-границей,
  // нечёткий поиск (3-граммы, пересечение >= 0.7) ловит слово целиком
  const items = [{ term: "Хунг", translation: "" }];
  const text = "Он встретил Хунгу у реки.";
  const found = hits(text, items);
  assert.deepEqual(found, ["Хунгу"]);
  // CJK-термины без слово-границ — точное вхождение внутри слов допустимо
  const cjk = [{ term: "灵草", translation: "" }];
  assert.deepEqual(hits("这是灵草地", cjk), ["灵草"]);
});

test("glossaryMatches: нормализация — пробелы и пунктуация", () => {
  // normalize_for_search: регистр/пробелы/пунктуация в термине и тексте — как есть;
  // диапазон в оригинале включает пропущенные пробелы и пунктыацию между словами.
  const items = [{ term: "Школа Света", translation: "" }];
  assert.deepEqual(hits("в школа   света, где он учился", items), [
    "школа   света",
  ]);
});

test("glossaryMatches: ngramSize настраивается (аналог --ner_ngram)", () => {
  const items = [{ term: "Хунг", translation: "" }];
  const m2 = UICore.buildGlossaryMatcher(items, 2);
  assert.equal(m2.ngramSize, 2);
  assert.deepEqual(m2.threshold, 0.75); // дефолт порога в редакторе
  const ms = UICore.glossaryMatches("Хунгу", m2);
  assert.deepEqual(
    ms.map((m) => "Хунгу".slice(m.from, m.to)),
    ["Хунгу"],
  );
});

test("glossaryMatches: threshold настраивается (аналог --ner_threshold)", () => {
  const items = [{ term: "хунгамар", translation: "" }];
  // слово «хунгамат»: совпадает 3 из 4 5-грамм = 0.75; общая подстрока 7 из 8.
  // порог 0.7 пропускает, 0.8 — отклоняет; точных вхождений в тексте нет.
  const loose = UICore.buildGlossaryMatcher(items, 5, 0.7);
  assert.equal(loose.threshold, 0.7);
  const msLoose = UICore.glossaryMatches("хунгамат", loose);
  assert.deepEqual(
    msLoose.map((m) => "хунгамат".slice(m.from, m.to)),
    ["хунгамат"],
  );
  const strict = UICore.buildGlossaryMatcher(items, 5, 0.8);
  assert.equal(strict.threshold, 0.8);
  assert.deepEqual(UICore.glossaryMatches("хунгамат", strict), []);
  // clamp: мусорные значения → дефолт 0.75; зажим в [0, 1]
  assert.equal(UICore.buildGlossaryMatcher(items, 5, NaN).threshold, 0.75);
  assert.equal(UICore.buildGlossaryMatcher(items, 5, 5).threshold, 1);
  assert.equal(UICore.buildGlossaryMatcher(items, 5, -2).threshold, 0);
});

test("buildGlossaryMatcher: чанки по ~2000 терминов", () => {
  const many = [];
  for (let i = 0; i < 4500; i++) many.push({ term: "t" + i, translation: "" });
  const m = matcherFor(many);
  assert.equal(m.total, 4500);
  assert.ok(m.chunks.length >= 3);
  // совпадение по термину из последнего чанка
  assert.deepEqual(hits("t4499", many), ["t4499"]);
});

test("dirEntries: пустой каталог (trailing '/') виден и не даёт пустых имён", () => {
  const files = ["prompts/", "prompts/translate.txt"];
  const root = UICore.dirEntries(files, "");
  assert.deepEqual(root.map((e) => `${e.dir ? "d:" : "f:"}${e.name}`).sort(), [
    "d:prompts",
  ]);
  // вход в пустой каталог (только "prompts/") — БЕЗ записи с пустым именем
  const inside = UICore.dirEntries(["prompts/"], "prompts");
  assert.deepEqual(inside, []);
  // только пустой каталог в корне
  const only = UICore.dirEntries(["empty/"], "");
  assert.deepEqual(
    only.map((e) => `${e.dir ? "d:" : "f:"}${e.name}`),
    ["d:empty"],
  );
});

test("glossarySentence: русское предложение вокруг термина", () => {
  const t = "Привет. Это термин здесь. Конец.";
  const from = t.indexOf("термин");
  const to = from + "термин".length;
  assert.equal(UICore.glossarySentence(t, from, to, 200), "Это термин здесь.");
});

test("glossarySentence: китайское предложение (。 — граница)", () => {
  const t = "即便他们真打赢了，苏星宇也不介意。下一句。";
  const from = t.indexOf("苏星宇");
  const to = from + "苏星宇".length;
  assert.equal(
    UICore.glossarySentence(t, from, to, 200),
    "即便他们真打赢了，苏星宇也不介意。",
  );
});

test("glossarySentence: обрезка до maxLen, термин внутри окна", () => {
  const term = "TERM";
  const t = "а".repeat(180) + term + "б".repeat(180);
  const from = 180;
  const to = 184;
  const s = UICore.glossarySentence(t, from, to, 200);
  assert.ok(s.length <= 200);
  assert.ok(s.includes(term));
});

test("glossarySentence: дефолт maxLen 200, пустой текст", () => {
  assert.equal(UICore.glossarySentence("", 0, 0, 200), "");
  assert.equal(UICore.glossarySentence("нет границ", 0, 3), "нет границ");
});

test("nerCellText: объекты как JSON, пустые как строка", () => {
  assert.equal(UICore.nerCellText(null), "");
  assert.equal(UICore.nerCellText(undefined), "");
  assert.equal(UICore.nerCellText("Лин"), "Лин");
  assert.equal(UICore.nerCellText(12), "12");
  assert.equal(UICore.nerCellText({ a: 1 }), '{"a":1}');
});

test("nextNerSort: первый клик — убывание, повтор — возрастание", () => {
  assert.deepEqual(UICore.nextNerSort("count", "desc", "translation"), {
    field: "translation",
    dir: "desc",
  });
  assert.deepEqual(UICore.nextNerSort("translation", "desc", "translation"), {
    field: "translation",
    dir: "asc",
  });
  assert.deepEqual(UICore.nextNerSort("translation", "asc", "translation"), {
    field: "translation",
    dir: "desc",
  });
  assert.deepEqual(UICore.nextNerSort(null, null, null), {
    field: "count",
    dir: "desc",
  });
});

test("sortNerItems: count по убыванию, пустые в конце", () => {
  const items = [
    { term: "a", count: 1 },
    { term: "b", count: 10 },
    { term: "c" },
    { term: "d", count: 5 },
  ];
  assert.deepEqual(
    UICore.sortNerItems(items, "count", "desc").map((x) => x.term),
    ["b", "d", "a", "c"],
  );
  assert.deepEqual(
    UICore.sortNerItems(items, "count", "asc").map((x) => x.term),
    ["a", "d", "b", "c"],
  );
});

test("sortNerItems: строки по возрастанию", () => {
  const items = [{ term: "я" }, { term: "б" }, { term: "а" }];
  assert.deepEqual(
    UICore.sortNerItems(items, "term", "asc").map((x) => x.term),
    ["а", "б", "я"],
  );
});

test("filterNerItems: по выбранным полям и типу", () => {
  const items = [
    { term: "林凡", type: "person", translation: "Лин Фань", notes: "гг" },
    { term: "火", type: "skill", translation: "огонь", notes: "стихия" },
  ];
  assert.equal(UICore.filterNerItems(items, "гг", ["notes"], "").length, 1);
  assert.equal(UICore.filterNerItems(items, "гг", ["term"], "").length, 0);
  assert.equal(UICore.filterNerItems(items, "лин", null, "").length, 1);
  assert.equal(UICore.filterNerItems(items, "", ["term"], "skill").length, 1);
  assert.equal(
    UICore.filterNerItems(items, "огонь", ["translation"], "person").length,
    0,
  );
});

test("filterNerItems: пустые поля/типы и набор типов", () => {
  const items = [
    { term: "林凡", type: "person", translation: "Лин Фань", notes: "гг" },
    { term: "火", type: "skill", translation: "огонь", notes: "стихия" },
  ];
  assert.equal(UICore.filterNerItems(items, "гг", [], "").length, 0);
  assert.equal(UICore.filterNerItems(items, "", null, []).length, 0);
  assert.equal(
    UICore.filterNerItems(items, "", null, ["person", "skill"]).length,
    2,
  );
  assert.equal(UICore.filterNerItems(items, "", null, ["person"]).length, 1);
  assert.equal(UICore.filterNerItems(items, "", [], "").length, 2);
});

/* ── review-файлы проверок (список LLM-правок) ── */

test("parseReviewContent: объект с «правки»", () => {
  const text = JSON.stringify({
    created: "2024-01-01 10:00",
    input: "ner.json",
    entries: [{ term: "林凡", field: "translation", old: "А", new: "Б" }],
  });
  const p = UICore.parseReviewContent(text);
  assert.equal(p.ok, true);
  assert.equal(p.isArray, false);
  assert.equal(p.entries.length, 1);
  assert.equal(p.entries[0].term, "林凡");
  assert.equal(p.doc["input"], "ner.json");
});

test("parseReviewContent: legacy-массив", () => {
  const p = UICore.parseReviewContent('[{"term":"x","field":"type"}]');
  assert.equal(p.ok, true);
  assert.equal(p.isArray, true);
  assert.equal(p.entries.length, 1);
  assert.deepEqual(p.doc, p.entries);
});

test("parseReviewContent: невалидный JSON и не-список", () => {
  const bad = UICore.parseReviewContent("{не json");
  assert.equal(bad.ok, false);
  assert.equal(bad.entries.length, 0);
  const noList = UICore.parseReviewContent('{"created":"t"}');
  assert.equal(noList.ok, false);
  const empty = UICore.parseReviewContent("");
  assert.equal(empty.ok, false);
});

test("updateReviewEntry: точечная правка в объекте, «обновлён» обновляется", () => {
  const doc = {
    created: "2024-01-01 10:00",
    entries: [
      {
        term: "林凡",
        field: "translation",
        old: "А",
        new: "Б",
        status: "принять",
      },
      { term: "火", field: "notes", old: "В", new: "Г", status: "отклонить" },
    ],
  };
  const doc2 = UICore.updateReviewEntry(doc, 0, { status: "отклонить" }, false);
  assert.notEqual(doc2, doc); // иммутабельно
  assert.equal(doc2["entries"][0]["status"], "отклонить");
  assert.equal(doc2["entries"][1]["status"], "отклонить"); // соседняя цела
  assert.equal(doc["entries"][0]["status"], "принять"); // исходник не тронут
  assert.ok(doc2["updated"]); // проставлен текущий момент
  assert.equal(doc["updated"], undefined);
});

test("updateReviewEntry: legacy-массив и границы", () => {
  const arr = [{ term: "x" }];
  const arr2 = UICore.updateReviewEntry(arr, 0, { term: "y" }, true);
  assert.equal(arr2[0].term, "y");
  assert.equal(arr[0].term, "x");
  assert.equal(UICore.updateReviewEntry(arr, 5, { term: "z" }, true), null);
  assert.equal(UICore.updateReviewEntry(arr, -1, { term: "z" }, true), null);
  assert.equal(UICore.updateReviewEntry(null, 0, {}, true), null);
});

test("fileIcon: папка и расширения", () => {
  assert.equal(UICore.fileIcon({ dir: true }), "📁");
  assert.equal(UICore.fileIcon({ name: "ch.txt" }), "📄");
  assert.equal(UICore.fileIcon({ name: "ner.json" }), "🧾");
  assert.equal(UICore.fileIcon({ name: "README.md" }), "📝");
  assert.equal(UICore.fileIcon({ name: "x.png" }), "🖼");
  assert.equal(UICore.fileIcon({ name: "noext" }), "📄");
  assert.equal(UICore.fileIcon(null), "📄");
  assert.equal(UICore.fileIcon({ name: "A.TXT" }), "📄"); // lower
});

test("removeReviewEntry: удаление в объекте, «обновлён» обновляется", () => {
  const doc = {
    created: "2024-01-01 10:00",
    entries: [
      { term: "林凡", field: "translation" },
      { term: "火", field: "notes" },
      { term: "刀", field: "type" },
    ],
  };
  const doc2 = UICore.removeReviewEntry(doc, 1, false);
  assert.notEqual(doc2, doc); // иммутабельно
  assert.equal(doc2["entries"].length, 2);
  assert.equal(doc2["entries"][1].term, "刀");
  assert.equal(doc["entries"].length, 3); // исходник не тронут
  assert.ok(doc2["updated"]);
  assert.equal(doc["updated"], undefined);
  assert.equal(UICore.removeReviewEntry(doc, 5, false), null); // вне диапазона
  assert.equal(UICore.removeReviewEntry(doc, -1, false), null);
  assert.equal(UICore.removeReviewEntry(null, 0, false), null);
});

test("removeReviewEntry: legacy-массив", () => {
  const arr = [{ term: "x" }, { term: "y" }, { term: "z" }];
  const arr2 = UICore.removeReviewEntry(arr, 0, true);
  assert.equal(arr2.length, 2);
  assert.equal(arr2[0].term, "y");
  assert.equal(arr.length, 3); // исходник не тронут
});

test("reviewSummary: подсчёт статусов", () => {
  const entries = [
    { status: "принять", applied: false },
    { status: "принять", applied: true },
    { status: "отклонить" },
    { applied: true }, // legacy без статуса
    { status: "принять" },
  ];
  assert.deepEqual(UICore.reviewSummary(entries), {
    total: 5,
    accepted: 3,
    rejected: 1,
    applied: 2,
  });
  assert.deepEqual(UICore.reviewSummary([]), {
    total: 0,
    accepted: 0,
    rejected: 0,
    applied: 0,
  });
  assert.deepEqual(UICore.reviewSummary(null), {
    total: 0,
    accepted: 0,
    rejected: 0,
    applied: 0,
  });
});

/* ── режим «Простой/Экспертный» в Запусках (localStorage) ── */
function stubLocalStorage() {
  let store = {};
  const ls = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
    clear: () => { store = {}; },
  };
  globalThis.localStorage = ls;
  return ls;
}

test("runModeGet: дефолт — простой режим", () => {
  stubLocalStorage();
  assert.equal(UICore.runModeGet("pipeline"), "simple");
});

test("runModeSet/runModeGet: выбор запоминается по стадии", () => {
  stubLocalStorage();
  UICore.runModeSet("wiki", "expert");
  UICore.runModeSet("pipeline", "simple");
  assert.equal(UICore.runModeGet("wiki"), "expert");
  assert.equal(UICore.runModeGet("pipeline"), "simple");
  // другие стадии — глобальный дефолт
  assert.equal(UICore.runModeGet("ner"), "simple");
});

test("runModeSet: переключение обратно в простой", () => {
  stubLocalStorage();
  UICore.runModeSet("ner", "expert");
  UICore.runModeSet("ner", "simple");
  assert.equal(UICore.runModeGet("ner"), "simple");
});

test("runMode: кривой JSON в localStorage не роняет", () => {
  stubLocalStorage();
  localStorage.setItem("runMode", "not-json{{");
  assert.equal(UICore.runModeGet("ner"), "simple");
});

test("isCjkString: суррогатные пары не считаются за 2 символа (B9)", () => {
  // U+20000 (CJK Ext B) — суррогатная пара в UTF-16
  const s = "𠀀𠀀𠀀" + "abc"; // 3 CJK + 3 латиницы
  assert.equal(UICore.isCjkString(s), false); // ровно 0.5 — не > 0.5
  const s2 = "𠀀𠀀𠀀𠀀" + "ab"; // 4 CJK + 2 латиницы
  assert.equal(UICore.isCjkString(s2), true);
  assert.equal(UICore.isCjkString(""), false);
  assert.equal(UICore.isCjkString(null), false);
});

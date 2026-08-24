/* Юнит-тесты web/static/ui-core.js (раунд 21).
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

/* ── раунд 23: матчер глоссария (вкладка «Редактор») ── */

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
  assert.deepEqual(m2.threshold, 0.7);
  const ms = UICore.glossaryMatches("Хунгу", m2);
  assert.deepEqual(ms.map((m) => "Хунгу".slice(m.from, m.to)), ["Хунгу"]);
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

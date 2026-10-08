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
    "ожидание первого результата…",
  );
  assert.equal(UICore.progressText(null, false), "");
});

test("progressText: done/total и label", () => {
  assert.equal(
    UICore.progressText({ done: 12, total: 636, label: "перевод" }, true),
    "перевод 12/636",
  );
  assert.equal(
    UICore.progressText({ done: 5, total: 0, label: "wiki" }, true),
    "wiki 5 …",
  );
});

test("etaDuration: секунды/минуты/часы", () => {
  assert.equal(UICore.etaDuration(45), "45 с");
  assert.equal(UICore.etaDuration(59.4), "59 с");
  assert.equal(UICore.etaDuration(60), "1 мин");
  assert.equal(UICore.etaDuration(725), "12 мин");
  assert.equal(UICore.etaDuration(3600), "1 ч");
  assert.equal(UICore.etaDuration(3900), "1 ч 5 мин");
  // мусор и отрицательные — «0 с»
  assert.equal(UICore.etaDuration(-5), "0 с");
  assert.equal(UICore.etaDuration(NaN), "0 с");
  assert.equal(UICore.etaDuration(undefined), "0 с");
});

test("etaClock: часы:минуты, две цифры", () => {
  const clock = UICore.etaClock(new Date(2026, 0, 1, 9, 5).getTime());
  assert.match(clock, /^\d{2}:\d{2}$/);
  assert.equal(clock.slice(0, 2), "09");
});

test("etaRemaining: скорость по окну выборок", () => {
  // 10 единиц за 100 c → 0.1/с; осталось 80 → 800 c
  const win = [
    { t: 1000, done: 10, total: 100 },
    { t: 1100, done: 20, total: 100 },
  ];
  assert.equal(UICore.etaRemaining(win, 20, 100, 1100, 900), 800);
});

test("etaRemaining: фолбэк от старта запуска", () => {
  // окно ещё короткое (span=0): 20 сделано за 200 c → остаток 80/0.1 = 800
  const one = [{ t: 1100, done: 20, total: 100 }];
  assert.equal(
    UICore.etaRemaining(one, 20, 100, 1100, 900),
    800,
  );
  // окно с одним приращением скорости не даёт: первая выборка пишется
  // в произвольной фазе шага (открытие страницы посреди шага), и время
  // до следующего события — не длительность шага, а его остаток.
  // 1 приращение за 28 c → «2 ч 29 мин» вместо реальных часов;
  // верим окну только с ≥2 приращениями, иначе — фолбэк от created
  const jump = [
    { t: 1071.6, done: 4, total: 320 },
    { t: 1100, done: 5, total: 320 },
  ];
  // фолбэк: 5 шагов за 200 c → остаток 315/0.025 = 12600
  assert.equal(UICore.etaRemaining(jump, 5, 320, 1100, 900), 12600);
  // два приращения — окну верим: 2 за 100 c → остаток 315/0.02 = 15750
  const two = [
    { t: 1000, done: 3, total: 320 },
    { t: 1100, done: 5, total: 320 },
  ];
  assert.equal(UICore.etaRemaining(two, 5, 320, 1100, 900), 15750);
  // нет выборок, нет created — оценить нечего
  assert.equal(UICore.etaRemaining([], 20, 100, 1100, null), null);
  // стоит на месте (окно без прогресса) и created нет — null
  const stuck = [
    { t: 1000, done: 10, total: 100 },
    { t: 1100, done: 10, total: 100 },
  ];
  assert.equal(UICore.etaRemaining(stuck, 10, 100, 1100, null), null);
});

test("etaRemaining: границы — нет данных для оценки", () => {
  const win = [
    { t: 1000, done: 10, total: 100 },
    { t: 1100, done: 20, total: 100 },
  ];
  assert.equal(UICore.etaRemaining(win, 0, 100, 1100, 900), null); // не начали
  assert.equal(UICore.etaRemaining(win, 100, 100, 1100, 900), null); // всё готово
  assert.equal(UICore.etaRemaining(win, 20, 0, 1100, 900), null); // нет total
  assert.equal(UICore.etaRemaining(null, 20, 100, 1100, 900), 800); // фолбэк
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

test("glossaryMatches: «нити» от «нить» — 1 буква разницы", () => {
  // регрессия: жёсткое LCS >= 0.8 длины отсекало 3/4 = 0.75 даже при
  // пороге 0.1; теперь общая подстрока меряется по порогу
  const items = [{ term: "нить", translation: "" }];
  // n=1, порог 0.1 — пользовательские настройки редактора
  const loose = UICore.buildGlossaryMatcher(items, 1, 0.1);
  const ms = UICore.glossaryMatches("он тянул нити ковра", loose);
  assert.deepEqual(
    ms.map((m) => "он тянул нити ковра".slice(m.from, m.to)),
    ["нити"],
  );
  // и при дефолтном пороге 0.75 (3/4 грамм = 0.75 ровно) тоже
  const def = UICore.buildGlossaryMatcher(items, 1, 0.75);
  assert.deepEqual(
    UICore.glossaryMatches("нити", def).map((m) => "нити".slice(m.from, m.to)),
    ["нити"],
  );
  // «который» на «кот» — по-прежнему не матчится (лишние n-граммы)
  const кот = [{ term: "кот", translation: "" }];
  assert.deepEqual(hits("который", кот), []);
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

test("glossarySentence: закрывающие кавычки — часть предложения", () => {
  const t = "Он сказал: «Привет, Джон!» И ушёл.";
  const from = t.indexOf("Джон");
  const to = from + "Джон".length;
  assert.equal(
    UICore.glossarySentence(t, from, to, 200),
    "Он сказал: «Привет, Джон!»",
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

test("nerCellText: объекты — пары «ключ: значение», пустые как строка", () => {
  assert.equal(UICore.nerCellText(null), "");
  assert.equal(UICore.nerCellText(undefined), "");
  assert.equal(UICore.nerCellText("Лин"), "Лин");
  assert.equal(UICore.nerCellText(12), "12");
  assert.equal(UICore.nerCellText({ a: 1 }), "a: 1");
  // после запятой обязателен пробел: иначе значение не переносится
  assert.equal(
    UICore.nerCellText({ translation: "мир", type: "other" }),
    "translation: мир, type: other",
  );
  assert.equal(UICore.nerCellText(["а", "б"]), "а, б");
  assert.equal(UICore.nerCellText({ a: { b: 2 } }), "a: b: 2");
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

test("fileIcon: имя SVG-иконки по папке и расширению", () => {
  assert.equal(UICore.fileIcon({ dir: true }), "folder");
  assert.equal(UICore.fileIcon({ name: "ch.txt" }), "file-text");
  assert.equal(UICore.fileIcon({ name: "ner.json" }), "braces");
  assert.equal(UICore.fileIcon({ name: "README.md" }), "file-text");
  assert.equal(UICore.fileIcon({ name: "x.png" }), "image");
  assert.equal(UICore.fileIcon({ name: "x.epub" }), "book");
  assert.equal(UICore.fileIcon({ name: "noext" }), "file");
  assert.equal(UICore.fileIcon(null), "file");
  assert.equal(UICore.fileIcon({ name: "A.TXT" }), "file-text"); // lower
});

test("icon: svg-строка с путём, xmlns, aria-hidden и классом", () => {
  const svg = UICore.icon("folder");
  assert.match(svg, /^<svg xmlns="http:\/\/www.w3.org\/2000\/svg" class="icon"/);
  assert.ok(svg.includes('aria-hidden="true"'));
  assert.ok(svg.includes("<path")); // тело иконки не пустое
  assert.match(UICore.icon("folder", "extra"), /class="icon extra"/);
  // неизвестное имя — заглушка, не undefined/исключение
  assert.ok(UICore.icon("нет-такой").includes("<rect"));
});

test("icon: все имена из iconNames рендерятся без исключений", () => {
  for (const name of UICore.iconNames) {
    assert.ok(
      UICore.icon(name).startsWith("<svg"),
      `иконка ${name} не рендерится`,
    );
  }
});

test("relTime: пороги и абсолютное время в tooltip", () => {
  const now = Date.now() / 1000;
  assert.equal(UICore.relTime(now - 5), "только что");
  assert.equal(UICore.relTime(now - 5 * 60), "5 мин назад");
  assert.equal(UICore.relTime(now - 3 * 3600), "3 ч назад");
  assert.equal(UICore.relTime(now - 2 * 86400), "2 дн назад");
  // старше недели — дата ДД.ММ.ГГГГ
  assert.match(UICore.relTime(now - 30 * 86400), /^\d{2}\.\d{2}\.\d{4}$/);
  // мусор — пустая строка
  assert.equal(UICore.relTime(0), "");
  assert.equal(UICore.relTime(undefined), "");
  assert.match(UICore.relTimeAbs(now - 60), /\d{2}\.\d{2}\.\d{4}/);
  assert.equal(UICore.relTimeAbs(null), "");
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

test("isCjkString: суррогатные пары не считаются за 2 символа (B9)", () => {
  // U+20000 (CJK Ext B) — суррогатная пара в UTF-16
  const s = "𠀀𠀀𠀀" + "abc"; // 3 CJK + 3 латиницы
  assert.equal(UICore.isCjkString(s), false); // ровно 0.5 — не > 0.5
  const s2 = "𠀀𠀀𠀀𠀀" + "ab"; // 4 CJK + 2 латиницы
  assert.equal(UICore.isCjkString(s2), true);
  assert.equal(UICore.isCjkString(""), false);
  assert.equal(UICore.isCjkString(null), false);
});

/* ── menuPlacement: координаты выпадающего меню («⋮») ──────────────
   Регрессия «у нижней строки списка меню не влезает»: dropdown был
   position:absolute от строки и обрезался карточкой .files-list
   (overflow:hidden) — из 78px меню оставалось ~4px. Меню теперь
   position:fixed, геометрию считает эта функция. */
const BTN = { top: 100, bottom: 140, left: 900, right: 940 }; // правый край 940

test("menuPlacement: есть место снизу — меню под кнопкой", () => {
  const p = UICore.menuPlacement(BTN, { width: 180, height: 78 }, { width: 1280, height: 800 });
  assert.equal(p.top, 146); // bottom(140) + gap(6)
  assert.equal(p.left, 760); // right(940) - width(180)
});

test("menuPlacement: снизу не влезает — разворот вверх", () => {
  // кнопка у самого низа окна: меню вниз вылезло бы за вьюпорт
  const btn = { top: 640, bottom: 669, left: 900, right: 940 };
  const p = UICore.menuPlacement(btn, { width: 180, height: 78 }, { width: 1280, height: 720 }, 6, 8);
  assert.equal(p.top, 556); // 640 - 6 - 78
  assert.ok(p.top + 78 <= 720, "меню целиком над кнопкой и в окне");
});

test("menuPlacement: узкое окно — меню не вылезает за левый край", () => {
  const btn = { top: 100, bottom: 140, left: 20, right: 60 };
  const p = UICore.menuPlacement(btn, { width: 180, height: 78 }, { width: 200, height: 800 }, 6, 8);
  assert.equal(p.left, 8); // прижато к левому краю (edge)
  assert.ok(p.left + 180 <= 200);
});

test("menuPlacement: меню шире окна — не уходит за правый край", () => {
  const btn = { top: 100, bottom: 140, left: 300, right: 340 };
  const p = UICore.menuPlacement(btn, { width: 500, height: 78 }, { width: 360, height: 800 }, 6, 8);
  assert.equal(p.left, 8);
  assert.ok(p.left + 500 > 360, "объективно шире окна, но левый край в пределах");
});

test("menuPlacement: очень низкое окно — верхний край не отрицательный", () => {
  const btn = { top: 40, bottom: 69, left: 10, right: 50 };
  const p = UICore.menuPlacement(btn, { width: 180, height: 200 }, { width: 400, height: 240 }, 6, 8);
  assert.ok(p.top >= 0 && p.top + 200 <= 240, `меню прижато в окне: ${JSON.stringify(p)}`);
});

test("menuPlacement: дефолтные gap/edge применяются без аргументов", () => {
  const btn = { top: 0, bottom: 100, left: 0, right: 200 };
  const p = UICore.menuPlacement(btn, { width: 100, height: 50 }, { width: 1000, height: 1000 });
  assert.deepEqual(p, { top: 106, left: 100 });
});

test("faviconHref: без запусков — глиф, с запусками — счётчик", () => {
  const plain = decodeURIComponent(UICore.faviconHref(0));
  assert.ok(plain.startsWith("data:image/svg+xml,"), "это data URI");
  assert.ok(plain.includes("⇄"), "покое — глиф");
  assert.ok(decodeURIComponent(UICore.faviconHref(3)).includes(">3<"));
  assert.ok(decodeURIComponent(UICore.faviconHref(42)).includes("9+"));
});

test("parseRoute: браузерный хэш раскодировывается", () => {
  // браузер хранит location.hash закодированным: кириллическое имя книги
  // обязано приходить в вид раскодированным, иначе оно уходит в API дважды
  // закодированным и проект не находится (404)
  assert.deepEqual(UICore.parseRoute("#/project/TMP/%D0%9A%D0%BD%D0%B8%D0%B3%D0%B0/files"), {
    view: "project",
    rest: ["TMP", "Книга", "files"],
  });
  assert.deepEqual(UICore.parseRoute("#/project/TMP/%D0%9A%D0%BD%D0%B8%D0%B3%D0%B0"), {
    view: "project",
    rest: ["TMP", "Книга"],
  });
  // латиника и пробелы
  assert.deepEqual(UICore.parseRoute("#/project/ACTIVE/My%20Book/run"), {
    view: "project",
    rest: ["ACTIVE", "My Book", "run"],
  });
});

test("parseRoute: битую %-последовательность не выдумываем", () => {
  assert.deepEqual(UICore.parseRoute("#/project/TMP/%ZZ/files"), {
    view: "project",
    rest: ["TMP", "%ZZ", "files"],
  });
});

test("toggleUiTheme: тёмная ↔ светлая", () => {
  assert.equal(UICore.toggleUiTheme("dark"), "light");
  assert.equal(UICore.toggleUiTheme("light"), "dark");
  // всё, что не «light», — тёмная (тема по умолчанию)
  assert.equal(UICore.toggleUiTheme(""), "light");
  assert.equal(UICore.toggleUiTheme(undefined), "light");
});

test("nerAction: действие ner-правки — патч поля или удаление термина", () => {
  // старые файлы и правки поля ключа action не имеют
  assert.equal(UICore.nerAction({ term: "A", field: "type" }), "патч");
  assert.equal(UICore.nerAction({}), "патч");
  assert.equal(UICore.nerAction(null), "патч");
  assert.equal(UICore.nerAction({ action: "чушь" }), "патч");
  // удаление: русский канон и английские написания ответа LLM
  assert.equal(UICore.nerAction({ action: "удаление" }), "удаление");
  assert.equal(UICore.nerAction({ action: " Удалить " }), "удаление");
  assert.equal(UICore.nerAction({ action: "delete" }), "удаление");
  assert.equal(UICore.nerAction({ action: "REMOVE" }), "удаление");
});

test("markWhitespace: пробелы видимы, структура строк сохранена", () => {
  assert.equal(UICore.markWhitespace("a  b"), "a··b");
  // таб — текстовая метка: ⇥ часть шрифтов рисует стрелкой
  assert.equal(UICore.markWhitespace("\ttab"), "\\ttab");
  // перевод строки остаётся переводом — метка пишется перед ним
  assert.equal(UICore.markWhitespace("a\n\nb"), "a⏎\n⏎\nb");
  assert.equal(UICore.markWhitespace("a\r\nb"), "a␍⏎\nb");
  assert.equal(UICore.markWhitespace(""), "");
  assert.equal(UICore.markWhitespace(null), "");
  assert.equal(UICore.markWhitespace(7), "7");
});

/* ── замок термина (_locked): состояние, а не столбец таблицы ───────── */
test("nerIsLocked: только «истина», старые файлы без поля — разблокированы", () => {
  assert.equal(UICore.nerIsLocked({ term: "A" }), false);
  assert.equal(UICore.nerIsLocked({ _locked: false }), false);
  assert.equal(UICore.nerIsLocked({ _locked: true }), true);
  assert.equal(UICore.nerIsLocked({ _locked: "1" }), true);
  assert.equal(UICore.nerIsLocked({ _locked: "да" }), true);
  assert.equal(UICore.nerIsLocked({ _locked: "0" }), false);
  assert.equal(UICore.nerIsLocked(null), false);
});

test("nerSetLocked: снятый замок убирает ключ, счётчик считает записи", () => {
  const it = { term: "A" };
  UICore.nerSetLocked(it, true);
  assert.equal(it._locked, true);
  UICore.nerSetLocked(it, false);
  assert.equal("_locked" in it, false);
  assert.equal(
    UICore.nerLockedCount([{ _locked: true }, { term: "B" }, null]),
    1,
  );
});

test("filterNerItems: фильтр по замку и поиск не видит служебное поле", () => {
  const items = [
    { term: "林凡", type: "person", translation: "Лин Фань", _locked: true },
    { term: "火", type: "skill", translation: "огонь" },
  ];
  assert.equal(UICore.filterNerItems(items, "", null, null).length, 2);
  assert.equal(
    UICore.filterNerItems(items, "", null, null, "locked").length,
    1,
  );
  assert.equal(
    UICore.filterNerItems(items, "", null, null, "locked")[0].term,
    "林凡",
  );
  assert.equal(
    UICore.filterNerItems(items, "", null, null, "unlocked")[0].term,
    "火",
  );
  // поиск по всем полям не цепляет значение замка
  assert.equal(UICore.filterNerItems(items, "true", null, "").length, 0);
});

/* ── язык подсветки файла (UICore.editorLang) ── */

test("editorLang: промпты и логи — по выбору, остальное — по расширению", () => {
  // расширение у промптов то же самое .txt: язык для них выбирает человек
  // (дефолт — собственный язык разметки промптов, собирается в бандле CM)
  assert.equal(UICore.EDITOR_SETTINGS.langPrompt, "prompt", "дефолт промптов");
  assert.equal(UICore.editorLang("prompts/ner_prompt.txt"), "prompt");
  assert.equal(UICore.editorLang("ner_prompt.txt", true), "prompt");
  assert.equal(UICore.editorLang("chapters/00000_1_Глава 1/polished.txt"), "txt");
  assert.equal(UICore.editorLang("notes.md"), "md");
  assert.equal(UICore.editorLang("tmp/report.json"), "json");
  assert.equal(UICore.editorLang("meta.yaml"), "yaml");
  // выбор пользователя — уже ИМЯ языка бандла, таблица расширений его минует
  UICore.EDITOR_SETTINGS.langPrompt = "xml";
  assert.equal(UICore.editorLang("prompts/ner_prompt.txt"), "xml",
    "промту дали XML");
  UICore.EDITOR_SETTINGS.langLog = "properties";
  assert.equal(UICore.editorLang("logs/pipeline.log"), "properties",
    "логам дали properties");
  UICore.EDITOR_SETTINGS.langPrompt = "prompt";
  UICore.EDITOR_SETTINGS.langLog = "text";
});

test("EDITOR_LANGS: значения — имена языков бандла, первый — plain text", () => {
  const vals = UICore.EDITOR_LANGS.map((o) => o.v);
  assert.deepEqual(vals, ["text", "prompt", "markdown", "html", "xml", "json",
    "yaml", "properties", "python"]);
  assert.equal(UICore.EDITOR_LANGS[0].label, "plain text");
});

/* ── разметка промпта в предпросмотре запроса (UICore.promptParts) ── */

test("promptParts: теги секций, подстановки и ключи JSON", () => {
  const parts = UICore.promptParts(
    '<system>\nТы переводчик.\n</system>\n'
    + '<user>\n=== ГЛОССАРИЙ ===\n{ner_block}\n{original_text}\n</user>\n'
    + '{"term": "火", "count": 3}\n',
  );
  const marked = parts.filter((p) => p.cls);
  assert.deepEqual(
    marked.map((p) => [p.cls, p.text]),
    [
      ["pv-tag", "<system>"],
      ["pv-tag", "</system>"],
      ["pv-tag", "<user>"],
      ["pv-var", "{ner_block}"],
      ["pv-var", "{original_text}"],
      ["pv-tag", "</user>"],
      ["pv-key", '"term"'],
      ["pv-key", '"count"'],
    ],
  );
  // склейка частей — исходный текст без потерь
  assert.equal(parts.map((p) => p.text).join(""), parts.join("").length
    ? parts.map((p) => p.text).join("") : "");
  assert.equal(
    parts.map((p) => p.text).join(""),
    '<system>\nТы переводчик.\n</system>\n<user>\n=== ГЛОССАРИЙ ===\n'
    + '{ner_block}\n{original_text}\n</user>\n{"term": "火", "count": 3}\n',
  );
});

test("promptParts: что не размечается", () => {
  // одиночная «<» без имени, подстановка с заглавной буквы, значение JSON
  for (const t of ["a < b", "<1>", "{Ner}", '":"', "{}", ""]) {
    const parts = UICore.promptParts(t);
    assert.ok(
      parts.every((p) => !p.cls),
      `лишняя разметка в «${t}»: ${JSON.stringify(parts)}`,
    );
    assert.equal(parts.length ? parts.map((p) => p.text).join("") : "", t);
  }
  // пустой ввод — пустой список, а не [{text:"",cls:""}]
  assert.deepEqual(UICore.promptParts(""), []);
  assert.deepEqual(UICore.promptParts(null), []);
});

test("promptParts: ключ JSON — то, что до двоеточия", () => {
  const parts = UICore.promptParts('{"type": "other"}\nterm: count\n');
  // значение («"other"») ключом не считается: после него нет двоеточия
  assert.deepEqual(
    parts.filter((p) => p.cls).map((p) => [p.cls, p.text]),
    [["pv-key", '"type"']],
  );
});

// ── видимость полей по режиму стадии (те же данные, что у argv запусков) ──
test("fieldApplies: when — значение поля режима из формы", () => {
  const f = { name: "names_min_count", when: [["action", ["3", "4"]]] };
  assert.equal(UICore.fieldApplies(f, { action: "3" }), true);
  assert.equal(UICore.fieldApplies(f, { action: "1" }), false);
  // режим не выбран (пусто или нет поля) — резать поля нечем
  assert.equal(UICore.fieldApplies(f, {}), true);
  assert.equal(UICore.fieldApplies(f, { action: "   " }), true);
});

test("fieldApplies: булево поле режима сравнивается как 1/0", () => {
  const f = {
    name: "chunk_mask",
    when_any: [["mode", ["chunk"]], ["rename_chapters", ["1"]]],
  };
  assert.equal(
    UICore.fieldApplies(f, { mode: "toc", rename_chapters: true }), true);
  assert.equal(
    UICore.fieldApplies(f, { mode: "toc", rename_chapters: false }), false);
  assert.equal(
    UICore.fieldApplies(f, { mode: "chunk", rename_chapters: false }), true);
});

test("fieldApplies: несколько условий when — И", () => {
  const toc = { name: "toc", when: [["format", ["md"]], ["as_chapter", ["0"]]] };
  assert.equal(UICore.fieldApplies(toc, { format: "md", as_chapter: false }), true);
  assert.equal(
    UICore.fieldApplies(toc, { format: "rulate-md", as_chapter: false }), false);
  assert.equal(UICore.fieldApplies(toc, { format: "md", as_chapter: true }), false);
});

test("fieldApplies: when_set — перечисленные поля непустые", () => {
  const co = { name: "co_occurrence_top", when_set: ["co_occurrence_pairs"] };
  assert.equal(UICore.fieldApplies(co, { co_occurrence_pairs: "Person:Person" }), true);
  assert.equal(UICore.fieldApplies(co, { co_occurrence_pairs: "   " }), false);
});

test("fieldApplies: пустые условия — касается всегда", () => {
  assert.equal(UICore.fieldApplies({ name: "x" }, {}), true);
  assert.equal(UICore.fieldApplies({ name: "x", when: [] }, { action: "1" }), true);
  assert.equal(UICore.fieldApplies({ name: "x", when_set: [] }, {}), true);
});

/* ── состояние вкладок проекта: один ключ на книгу ───────────────────── */
function memStore(init) {
  const m = new Map(Object.entries(init || {}));
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(String(k), String(v)),
    removeItem: (k) => m.delete(k),
    _dump: () => Object.fromEntries(m),
  };
}

test("projectPrefKey: ключ — один на проект", () => {
  assert.equal(UICore.projectPrefKey("ACTIVE", "Книга"), "nmTab:ACTIVE/Книга");
});

test("projectPrefs: пустое хранилище — пустая вкладка, запись мержится", () => {
  const store = memStore();
  const p = UICore.projectPrefs(store, "ACTIVE", "Книга");
  assert.deepEqual(p.get("editor"), {});
  p.set("editor", { chapter: "00000_1_x" });
  p.set("editor", { mode: "one" });
  assert.deepEqual(p.get("editor"), { chapter: "00000_1_x", mode: "one" });
  assert.deepEqual(JSON.parse(store._dump()["nmTab:ACTIVE/Книга"]), {
    editor: { chapter: "00000_1_x", mode: "one" },
  });
});

test("projectPrefs: соседняя вкладка и соседний проект не затираются", () => {
  const store = memStore();
  const a = UICore.projectPrefs(store, "ACTIVE", "Книга");
  a.set("chapters", { type: "translated" });
  a.set("page", { view: "chapters" });
  const b = UICore.projectPrefs(store, "HOLD", "Другая");
  b.set("page", { view: "logs" });
  assert.deepEqual(a.get("page"), { view: "chapters" });
  assert.deepEqual(a.get("chapters"), { type: "translated" });
  assert.deepEqual(b.get("page"), { view: "logs" });
  assert.deepEqual(b.get("chapters"), {});
});

test("projectPrefs: битый JSON и недоступное хранилище не мешают", () => {
  const broken = UICore.projectPrefs(
    memStore({ "nmTab:ACTIVE/Книга": "{это не json" }), "ACTIVE", "Книга",
  );
  assert.deepEqual(broken.get("editor"), {});
  broken.set("editor", { mode: "two" });
  assert.deepEqual(broken.get("editor"), { mode: "two" });
  /* хранилище недоступно: чтение — пустое, запись не бросает и не теряется
     совсем — состояние живёт в памяти до перезагрузки страницы */
  let writes = 0;
  const dead = UICore.projectPrefs({
    getItem: () => { throw new Error("SecurityError"); },
    setItem: () => { writes++; throw new Error("QuotaExceeded"); },
  }, "ACTIVE", "Книга");
  assert.deepEqual(dead.get("editor"), {});
  dead.set("editor", { mode: "one" });
  assert.deepEqual(dead.get("editor"), { mode: "one" });
  assert.equal(writes, 1, "попытка записи была — и осталась единственной");
});

// «Оценка перевода (LLM)»: конечная глава считается по бюджету.
// qualityEndByBudget — глобал run-views.js (vm-контекст, как в браузере):
// размер главы = перевод (тип файлов) + оригинал (chapter.txt),
// бюджет — ТОЛЬКО на содержимое (промпт НЕ вычитается), ответ —
// последняя глава, помещающаяся в бюджет (не более максимальной).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const SRC = readFileSync(
  require.resolve("../../web/static/run-views.js"),
  "utf8",
);

function load() {
  const sandbox = { window: {} };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox);
  const fn = sandbox.qualityEndByBudget;
  assert.equal(typeof fn, "function", "qualityEndByBudget не найден");
  return fn;
}

// главы 1..5: перевод 1000 симв., оригинал 500; id — строки как в /tree
const TREE = [1, 2, 3, 4, 5].map((id) => ({
  id: String(id),
  artifacts: { "polished.txt": 1000, "chapter.txt": 500 },
}));

test("все главы помещаются — возвращает последнюю", () => {
  const fn = load();
  assert.equal(fn(TREE, 1, 200000, "polished"), "5");
});

test("обрезка по бюджету до целого количества глав", () => {
  const fn = load();
  // бюджет 7500 = ровно 5 глав по 1500 (на 6-ю не хватает)
  assert.equal(fn(TREE, 1, 7500, "polished"), "5");
  // бюджет 4500 — влезают 3 главы (3×1500)
  assert.equal(fn(TREE, 1, 4500, "polished"), "3");
  // 4501 — та же 3 (четвёртая 1500 не влезает целиком)
  assert.equal(fn(TREE, 1, 4501, "polished"), "3");
});

test("промпт НЕ вычитается из бюджета", () => {
  const fn = load();
  // промпт 500 игнорируется: бюджет 4500 → 3 главы (4500)
  assert.equal(fn(TREE, 1, 4500, "polished"), "3");
});

test("бюджета не хватает на первую главу — пусто", () => {
  const fn = load();
  assert.equal(fn(TREE, 1, 1499, "polished"), "");
});

test("start смещает отсчёт", () => {
  const fn = load();
  // с 3-й главы: 3+4+5 = 4500 — все три влезают
  assert.equal(fn(TREE, 3, 4500, "polished"), "5");
  // с 4-й: 4+5 = 3000; бюджет 4500 — обе
  assert.equal(fn(TREE, 4, 4500, "polished"), "5");
  // с 4-й: бюджет 3000 — ровно 4 и 5 (3000) — обе
  assert.equal(fn(TREE, 4, 3000, "polished"), "5");
});

test("пропуск главы (нет id 4)", () => {
  const fn = load();
  const tree = TREE.filter((c) => c.id !== "4");
  // главы 1,2,3,5: бюджет 4500 — 1..3 (4500), 5-я не влезает
  assert.equal(fn(tree, 1, 4500, "polished"), "3");
  // бюджет 6000 — 1,2,3,5 (6000)
  assert.equal(fn(tree, 1, 6000, "polished"), "5");
});

test("start больше последней главы — пусто", () => {
  const fn = load();
  assert.equal(fn(TREE, 6, 200000, "polished"), "");
});

test("тип файлов влияет на размер главы", () => {
  const fn = load();
  const tree = TREE.map((c) => ({
    ...c,
    artifacts: {
      "chapter.txt": 500,
      "translated.txt": 200,
      "polished.txt": 1000,
    },
  }));
  // translated: глава = 200+500 = 700; бюджет 1400 → 2 главы
  assert.equal(fn(tree, 1, 1400, "translated"), "2");
  // polished: глава = 1500; бюджет 1400 — ни одной
  assert.equal(fn(tree, 1, 1400, "polished"), "");
});

test("нет артефактов — главы не считаются", () => {
  const fn = load();
  const tree = TREE.map((c) => ({ id: c.id, artifacts: {} }));
  assert.equal(fn(tree, 1, 200000, "polished"), "");
});

test("тип chapter — оригинал берётся как перевод", () => {
  const fn = load();
  // chapter.txt 500 + chapter.txt 500 = 1000 на главу; бюджет 2000 → 2
  assert.equal(fn(TREE, 1, 2000, "chapter"), "2");
});

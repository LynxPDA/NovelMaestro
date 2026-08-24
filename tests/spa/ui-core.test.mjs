/* Юнит-тесты web/static/ui-core.js (раунд 21).
 * Запуск: node --test tests/spa/ (или npm-скрипт, если появится package.json).
 * Никакой сети и DOM — чистые функции. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const UICore = require("../../web/static/ui-core.js");

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

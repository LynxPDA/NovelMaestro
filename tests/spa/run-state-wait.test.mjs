/* Форма запусков не собирается, пока книга не ответила.
 *
 * На двух компьютерах одна стадия показывала РАЗНЫЕ диапазоны глав: форма
 * собиралась один раз из браузерного зеркала (`localStorage`), а ответ книги
 * приходил позже и перерисовкой форму не обновлял. Здесь проверяется само
 * ожидание: `bookLoaded` ждёт ответа не дольше своего предела, а
 * `UICore.bookState` честно сообщает `loaded()`.
 *
 * Запуск: node --test tests/spa/
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const _require = createRequire(import.meta.url); // _: анализатор путает с глобалом
const UICore = _require("../../web/static/ui-core.js");

function fakeStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(String(k), String(v)),
    removeItem: (k) => m.delete(k),
    key: (i) => [...m.keys()][i] ?? null,
    get length() { return m.size; },
  };
}

test("bookState: loaded() до ответа — false, после — true", async () => {
  const prev = globalThis.localStorage;
  globalThis.localStorage = fakeStorage();
  try {
    let release = null;
    const book = UICore.bookState({
      load: () => new Promise((r) => { release = () => r({ data: {} }); }),
      save: () => Promise.resolve({ ok: true }),
    }, "ACTIVE", "Книга");
    assert.equal(book.loaded(), false, "до ответа форма ждать обязана");
    const done = book.load();
    release();
    await done;
    assert.equal(book.loaded(), true);
  } finally {
    globalThis.localStorage = prev;
  }
});

test("bookState: отказ сервера тоже снимает ожидание", async () => {
  const prev = globalThis.localStorage;
  globalThis.localStorage = fakeStorage();
  try {
    const book = UICore.bookState({
      load: () => Promise.reject(new Error("нет сети")),
      save: () => Promise.resolve({ ok: true }),
    }, "ACTIVE", "Книга");
    assert.equal(book.loaded(), false);
    await book.load();
    /* решение уже принято (сеть не ответит) — форму ждать нечего, иначе
       вкладка висела бы пустой весь таймаут */
    assert.equal(book.loaded(), true);
    assert.equal(book.failed(), true);
  } finally {
    globalThis.localStorage = prev;
  }
});

test("bookState: чужая правка в книге важнее зеркала браузера", async () => {
  /* сценарий «два компьютера»: здесь в зеркале 1–50, в книге 100–200
     (записал другой компьютер позже). Форма должна показать 100–200. */
  const prev = globalThis.localStorage;
  const storage = fakeStorage();
  globalThis.localStorage = storage;
  try {
    const key = "runVals:ACTIVE/Книга:ner";
    storage.setItem(key, '{"start":"1","end":"50"}');
    storage.setItem("nmStateMigrated:ACTIVE/Книга", "1");
    const book = UICore.bookState({
      load: () => Promise.resolve({ data: { [key]: { start: "100", end: "200" } } }),
      save: () => Promise.resolve({ ok: true }),
    }, "ACTIVE", "Книга");
    const done = book.load();
    await done;
    assert.match(String(book.store.getItem(key)), /"100"/,
      "книга — истина, зеркало до её ответа");
  } finally {
    globalThis.localStorage = prev;
  }
});

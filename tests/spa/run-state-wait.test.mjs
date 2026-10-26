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

/* ── запись только изменённых полей ──────────────────────────────────────
 * Сохранение отправляло ВСЕ тронутые поля стадии, а сервер ставил ключ
 * целиком. Компьютер A, поправив chunk_size, присылал свои старые start/end
 * и затирал диапазон, записанный компьютером B, — две машины видели разные
 * главы. Теперь сравнение идёт с книжным значением: уезжает только то, что
 * действительно изменилось. */
function migrated() {  // отметка переноса: adopt не добавит свой ключ
  globalThis.localStorage.setItem("nmStateMigrated:ACTIVE/Книга", "1");
}

function serverBook(initial) {
  const state = JSON.parse(JSON.stringify(initial || {}));
  const saves = [];
  return {
    state,
    saves,
    api: {
      load: () => Promise.resolve({ data: JSON.parse(JSON.stringify(state)) }),
      save: (patch) => {
        saves.push(patch);
        const merge = (base, part) => {
          const out = { ...base };
          for (const [k, v] of Object.entries(part || {})) {
            if (v === null) delete out[k];
            else if (out[k] && typeof out[k] === "object" && !Array.isArray(out[k])
                && v && typeof v === "object" && !Array.isArray(v)) {
              out[k] = merge(out[k], v);
            } else out[k] = v;
          }
          return out;
        };
        for (const [k, v] of Object.entries(patch)) {
          if (v === null) delete state[k];
          else state[k] = merge(state[k], v);
        }
        return Promise.resolve({ ok: true, data: JSON.parse(JSON.stringify(state)) });
      },
    },
  };
}

test("bookState: запись несёт только изменённое поле формы", async () => {
  const prev = globalThis.localStorage;
  globalThis.localStorage = fakeStorage();
  try {
    const key = "runVals:ACTIVE/Книга:pipeline";
    const srv = serverBook({ [key]: { start: "100", end: "200" } });
    migrated();
    const book = UICore.bookState(srv.api, "ACTIVE", "Книга");
    await book.load();
    const cur = JSON.parse(book.store.getItem(key));
    cur.chunk_size = "9000";               // правим одно поле
    book.store.setItem(key, JSON.stringify(cur));
    await book.flush();
    assert.deepEqual(srv.saves, [{ [key]: { chunk_size: "9000" } }],
      "в книгу уехало только изменённое поле");
    assert.deepEqual(srv.state[key],
      { start: "100", end: "200", chunk_size: "9000" },
      "соседние поля живы");
  } finally {
    globalThis.localStorage = prev;
  }
});

test("bookState: правка без изменений не пишется", async () => {
  const prev = globalThis.localStorage;
  globalThis.localStorage = fakeStorage();
  try {
    const key = "runVals:ACTIVE/Книга:ner";
    const srv = serverBook({ [key]: { start: "1", end: "9" } });
    migrated();
    const book = UICore.bookState(srv.api, "ACTIVE", "Книга");
    await book.load();
    book.store.setItem(key, book.store.getItem(key)); // значение то же
    await book.flush();
    assert.deepEqual(srv.saves, [], "менять нечего — запроса нет");
  } finally {
    globalThis.localStorage = prev;
  }
});

test("bookState: снятое поле уезжает как null", async () => {
  const prev = globalThis.localStorage;
  globalThis.localStorage = fakeStorage();
  try {
    const key = "runVals:ACTIVE/Книга:ner";
    const srv = serverBook({ [key]: { start: "1", two_pass: "1" } });
    migrated();
    const book = UICore.bookState(srv.api, "ACTIVE", "Книга");
    await book.load();
    book.store.setItem(key, JSON.stringify({ start: "1" }));
    await book.flush();
    assert.deepEqual(srv.saves, [{ [key]: { two_pass: null } }]);
    assert.deepEqual(srv.state[key], { start: "1" });
  } finally {
    globalThis.localStorage = prev;
  }
});

test("bookState: ответ записи обновляет снимок книги", async () => {
  /* запись могла лечь поверх чужой правки: сервер отвечает состоянием
     книги, и снимок клиента обязан стать им */
  const prev = globalThis.localStorage;
  globalThis.localStorage = fakeStorage();
  try {
    const key = "runVals:ACTIVE/Книга:pipeline";
    const srv = serverBook({ [key]: { start: "100" } });
    migrated();
    const book = UICore.bookState(srv.api, "ACTIVE", "Книга");
    await book.load();
    // пока страница открыта, диапазон поправил другой браузер
    srv.state[key].start = "300";
    book.store.setItem(key, JSON.stringify({ start: "300", end: "400" }));
    await book.flush();
    assert.equal(JSON.parse(book.store.getItem(key)).start, "300");
  } finally {
    globalThis.localStorage = prev;
  }
});

test("bookState: удалённый на сервере ключ пропадает из снимка", async () => {
  /* «Сбросить настройки» на другом устройстве: если бы снимок держал старое
     значение, сброс «не срабатывал» бы */
  const prev = globalThis.localStorage;
  globalThis.localStorage = fakeStorage();
  try {
    const key = "runVals:ACTIVE/Книга:ner";
    const srv = serverBook({ [key]: { start: "1" } });
    migrated();
    delete srv.state[key];
    const book = UICore.bookState(srv.api, "ACTIVE", "Книга");
    await book.load();
    assert.equal(book.store.getItem(key), null);
  } finally {
    globalThis.localStorage = prev;
  }
});

test("projectPrefs: запись вкладки не воскрешает чужие поля", () => {
  /* вкладка держала разобранную копию с создания и писала её целиком: пока
     она была открыта, книга с другого браузера возвращалась назад */
  const storage = fakeStorage();
  const key = "nmTab:ACTIVE/Книга";
  storage.setItem(key, JSON.stringify({ page: { view: "files" } }));
  const pref = UICore.projectPrefs(storage, "ACTIVE", "Книга");
  // пока вкладка открыта, другой браузер переключил страницу на «Запуски»
  storage.setItem(key, JSON.stringify({ page: { view: "run" } }));
  pref.set("editor", { chapter: "7" });
  assert.deepEqual(JSON.parse(storage.getItem(key)), {
    page: { view: "run" }, editor: { chapter: "7" },
  }, "чужое поле не откатилось");
});

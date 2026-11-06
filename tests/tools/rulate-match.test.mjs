// rulate_reload: сопоставление глав файла с главами сайта и разбор заголовков.
// Юзерскрипт — один IIFE, в node целиком не исполняется (нужны document, fetch,
// FormData), поэтому из артефакта вырезается тело до секции UI и исполняется на
// заглушках: оглавление — фиктивные ссылки, страница главы — фиктивный DOM.
//
// Что здесь ловится: перевод не должен уезжать в ЧУЖУЮ главу. На сайте бывает
// несколько глав с одним номером (второй том, «Глава 1. Дополнительная глава»),
// названия на сайте бывают от прошлого перевода (файл перекомпилировали), а
// лишние главы (примечание переводчика) в файле отсутствуют.
// Запуск: node --test tests/tools/*.test.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const ARTIFACT = path.join(REPO, 'tools/rulate_reload/rulate-bulk-update.user.js');
const raw = fs.readFileSync(ARTIFACT, 'utf8');

const primary = raw.slice(0, raw.indexOf('/* ---------- UI ---------- */'));
assert.ok(primary.length > 1000, 'в артефакте не найдено тело до секции UI');
// баннер ==UserScript== и обёртка IIFE — не код для node: в артефакте они
// открывают файл, а закрываются за секцией UI, поскольку та тоже внутри IIFE
const BODY = primary.slice(primary.indexOf("'use strict';") + "'use strict';".length);
assert.ok(BODY.includes('function run('), 'тело скрипта не вырезано');

const BOOK = 100;

/** Фиктивный сайт: оглавление из ссылок и страницы глав с «моим» переводом. */
function boot(site) {
  const byId = new Map(site.map((c) => [c.id, c]));
  const posts = [];

  const chapterDoc = (ch) => {
    const trId = 900 + ch.id;
    const bodyEl = { innerHTML: ch.text, querySelector: () => ({ innerHTML: ch.text }) };
    const mine = { id: `t${trId}`, className: 'u1' };
    const row = { id: `o${500 + ch.id}`, querySelector: (sel) => (sel.includes('div[id^="t"]') ? mine : null) };
    const saved = { id: `t${trId}`, querySelector: () => bodyEl };
    return {
      querySelectorAll: (sel) => (sel === 'tr[id^="o"]' ? [row] : []),
      querySelector: (sel) => (sel.includes('csrf') ? { content: 'CSRF' } : null),
      getElementById: (id) => (id === `t${trId}` ? saved : null),
    };
  };

  const anchors = site.map((c) => ({
    href: `https://tl.rulate.ru/book/${BOOK}/${c.id}`,
    textContent: c.title,
    getAttribute: () => null,
  }));

  const document = {
    querySelectorAll: (sel) => (sel.includes('a[href') ? anchors : []),
    querySelector: (sel) => (sel.includes('csrf') ? { content: 'CSRF' } : null),
    createElement: () => ({ set innerHTML(_v) {}, get textContent() { return ''; } }),
  };

  const fetchStub = async (url, init) => {
    if (init && init.method === 'POST') {
      const m = String(url).match(new RegExp(`/book/${BOOK}/(\\d+)/`));
      posts.push(m ? m[1] : null);
      return { ok: true, status: 200, text: async () => '' };
    }
    const m = String(url).match(new RegExp(`/book/${BOOK}/(\\d+)$`));
    if (!m) return { ok: false, status: 404, text: async () => '' };
    const ch = byId.get(Number(m[1]));
    return {
      ok: true,
      status: 200,
      text: async () => `<!--CHAP_ID:${ch.id}-->new CUser({ id: 7 });\nChap = { id: ${ch.id}, title: '${ch.title}' };`,
    };
  };

  // DOMParser стенда: запрос CHAP_ID отдаёт страницу главы, любой другой текст
  // (decodeHtml) — просто текст без разметки
  class Parser {
    parseFromString(html) {
      const m = html.match(/CHAP_ID:(\d+)/);
      return m
        ? chapterDoc(byId.get(Number(m[1])))
        : { body: { textContent: html.replace(/<[^>]+>/g, ''), childNodes: [] }, querySelector: () => null, querySelectorAll: () => [] };
    }
  }

  const api = new Function(
    'document', 'location', 'fetch', 'DOMParser', 'FormData', 'Blob',
    BODY + '\nreturn { CFG, parseChaptersFile, buildSiteMap, planMatches, run, chrf, looksLikeSameChapter };',
  )(
    document,
    { pathname: `/book/${BOOK}`, origin: 'https://tl.rulate.ru' },
    fetchStub,
    Parser,
    class { constructor() { this.d = {}; } append(k, v) { this.d[k] = v; } set(k, v) { this.d[k] = v; } },
    class {},
  );
  api.CFG.verify = false; // стенд не сохраняет текст, сверка после записи здесь не про сопоставление
  /* Тексты-заглушки («н1» против «с1») не похожи друг на друга, и сверка
     главы по chrF справедливо отказала бы — но эти тесты про СОПОСТАВЛЕНИЕ,
     а не про неё. Свои проверки chrF есть ниже, отдельными тестами. */
  api.CFG.checkSameChapter = false;
  api.CFG.delayMs = 0;
  api.CFG.intraMs = 0;
  return { api, posts };
}

const run = async (site, fileLines, { fallback = false } = {}) => {
  const { api, posts } = boot(site);
  api.CFG.fallbackByTitle = fallback;
  const parsed = api.parseChaptersFile(fileLines.join('\n'));
  const logs = [];
  await api.run(parsed, { dryRun: false, skipSame: false, rangeStr: '' },
    { aborted: false, log: (m) => logs.push(m), setProgress() {} });
  return { posts, logs, parsed, api };
};

/* ── разбор заголовка: «Глава N» из названия, тег — запасной ── */

test('rulate: номер берётся из «Глава N» в названии, тег лишь запасной', () => {
  const { api } = boot([]);
  const [c] = api.parseChaptersFile('# [Глава 5. Название :|: 42]\nтекст');
  assert.equal(c.num, 5, 'на сайте глава размечена названием, по нему и надо искать');
});

test('rulate: без «Глава N» в названии номер берётся из тега', () => {
  const { api } = boot([]);
  const [c] = api.parseChaptersFile('# [Пролог. Начало :|: 7]\nтекст');
  assert.equal(c.num, 7);
  assert.equal(c.tagged, true);
});

test('rulate: paywall и том не попадают в название', () => {
  const { api } = boot([]);
  const [c] = api.parseChaptersFile('# [Глава 5. Имя :|: 5 :|: 100 :|: 2]\nтекст');
  assert.equal(c.title, 'Глава 5. Имя', 'хвост тега уезжал в название и на сайт');
  assert.equal(c.num, 5);
});

/* ── сопоставление: решает название, порядок не угадывает ── */

test('rulate: две главы с одним номером разводятся полным названием', async () => {
  const site = [
    { id: 11, title: 'Глава 1. История', text: 'с1' },
    { id: 12, title: 'Глава 2. Вторая попытка', text: 'с2' },
    { id: 13, title: 'Глава 3. Дальше', text: 'с3' },
    { id: 14, title: 'Глава 1. Дополнительная глава', text: 'с4' },
  ];
  const { posts } = await run(site, [
    '# [Глава 1. История :|: 1]', 'н1', '',
    '# [Глава 2. Вторая попытка :|: 2]', 'н2', '',
    '# [Глава 1. Дополнительная глава :|: 1]', 'н4',
  ]);
  assert.deepEqual(posts, ['11', '12', '14'],
    'дополнительная глава уезжала в «Историю»: обе строки файла писали в главу 11');
});

test('rulate: название на сайте устарело — отказ, а не запись в догадку', async () => {
  /* После перекомпиляции названия в файле новые, на сайте старые. Номер
     один, название не совпало — писать нельзя: это может быть соседняя
     глава. Такой случай человек решает глазами. */
  const site = [
    { id: 11, title: 'Глава 1. Старое название', text: 'с1' },
    { id: 14, title: 'Глава 1. Тоже старое', text: 'с4' },
  ];
  const { posts, logs } = await run(site, [
    '# [Глава 1. Новое название :|: 1]', 'н1', '',
    '# [Глава 1. Второе новое :|: 1]', 'н4',
  ]);
  assert.deepEqual(posts, [], 'порядок угадывать не должен');
  assert.ok(logs.some((l) => l.includes('НЕОДНОЗНАЧНО')), 'причина отказа должна быть в логе');
});

test('rulate: лишняя глава на сайте (примечание) не мешает своей главе', async () => {
  const site = [
    { id: 11, title: 'Глава 1. История', text: 'с1' },
    { id: 14, title: 'Глава 1. Примечание переводчика', text: 'прим' },
    { id: 15, title: 'Глава 2. Вторая попытка', text: 'с2' },
  ];
  const { posts } = await run(site, [
    '# [Глава 1. История :|: 1]', 'н1', '',
    '# [Глава 2. Вторая попытка :|: 2]', 'н2',
  ]);
  assert.deepEqual(posts, ['11', '15'], 'своя глава находится по названию, примечание не трогаем');
});

test('rulate: одна и та же глава дважды в файле — второй раз не льём', async () => {
  const site = [{ id: 11, title: 'Глава 5. Имя', text: 'с5' }];
  const { posts, logs } = await run(site, [
    '# [Глава 5. Имя :|: 5]', 'н5',
  ]);
  assert.deepEqual(posts, ['11']);
  assert.ok(logs.every((l) => !l.includes('НЕОДНОЗНАЧНО')), 'дубль не должен выглядеть неоднозначностью');
});

/* ── сверка «та ли это глава» по chrF ── */

test('rulate: chrF различает свою главу и чужую', () => {
  const { api } = boot([]);
  const base = ('Слово за словом эта глава рассказывает о человеке, который пришёл в город и нашёл судьбу. ').repeat(20);
  const edited = base.replace(/судьбу/g, 'участь').replace(/город/g, 'град');
  const other = ('Совсем другой текст про море, корабли и шторм, где герой сражается с волнами. ').repeat(20);
  assert.ok(api.chrf(base, edited) > 0.8, 'мелкая правка не должна выглядеть чужой главой');
  assert.ok(api.chrf(base, other) < 0.4, 'чужая глава должна быть далеко');
  assert.equal(api.looksLikeSameChapter(base, edited).ok, true);
  assert.equal(api.looksLikeSameChapter(base, other).ok, false);
});

test('rulate: обрезанный файл не проходит сверку', () => {
  const { api } = boot([]);
  const full = ('Длинная глава с большим количеством текста и подробностями повествования. ').repeat(40);
  const cut = full.slice(0, Math.floor(full.length * 0.1));
  const res = api.looksLikeSameChapter(full, cut);
  assert.equal(res.ok, false, '10% объёма — это обрыв файла, а не та же глава');
  assert.equal('ratio' in res, false, 'объём не мерится вовсе: признак — chrF');
  assert.ok(res.chrf < 0.75);
});

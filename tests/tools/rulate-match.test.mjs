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
    BODY + '\nreturn { CFG, parseChaptersFile, buildSiteMap, planMatches, run, chrf, looksLikeSameChapter,'
      + ' buildPlan, planAssign, planToData, planFromData, planToText, applyOverrides, collectOverrides, writePlan,'
      + ' planSkipSelected, planResetSelected, planAlignByOrder, parseChrfThreshold,'
      + ' parseRange, parseRangeReport, planSetRange, planRowRange, describeRange,'
      + ' titleCore, titleScore, rankCandidates, TITLE_SIMILAR_MIN };',
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
     а не про неё. Порог 0 = сверка выключена. Свои проверки chrF — ниже. */
  api.CFG.minChrf = 0;
  api.CFG.delayMs = 0;
  api.CFG.intraMs = 0;
  return { api, posts };
}

const run = async (site, fileLines) => {
  const { api, posts } = boot(site);
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
  api.CFG.minChrf = 0.75; // стенд по умолчанию сверку выключает — здесь она и проверяется
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
  api.CFG.minChrf = 0.75;
  const full = ('Длинная глава с большим количеством текста и подробностями повествования. ').repeat(40);
  const cut = full.slice(0, Math.floor(full.length * 0.1));
  const res = api.looksLikeSameChapter(full, cut);
  assert.equal(res.ok, false, '10% объёма — это обрыв файла, а не та же глава');
  assert.equal('ratio' in res, false, 'объём не мерится вовсе: признак — chrF');
  assert.ok(res.chrf < 0.75);
});

/* ── план: данные, правка человеком, обмен файлами ── */

const SITE_PLAN = [
  { id: 11, title: 'Глава 1. История', text: 'с1' },
  { id: 12, title: 'Глава 2. Вторая попытка', text: 'с2' },
  { id: 14, title: 'Глава 1. Дополнительная глава', text: 'с4' },
  { id: 15, title: 'Примечание переводчика', text: 'прим' },
];

/** План по фиктивному сайту: оглавление строится тем же fetch-стендом. */
const planFor = async (site, fileLines, mutate) => {
  const { api, posts } = boot(site);
  if (mutate) mutate(api);
  const parsed = api.parseChaptersFile(fileLines.join('\n'));
  const siteData = await api.buildSiteMap(String(BOOK));
  const plan = api.buildPlan(parsed, siteData);
  return { api, posts, parsed, plan, site: siteData };
};

test('rulate: план собирает строки, проблемные главы — отдельно', async () => {
  const { plan, parsed } = await planFor(SITE_PLAN, [
    '# [Глава 1. История :|: 1]', 'н1', '',
    '# [Глава 2.5. Дробная :|: 2]', 'н2', '',
    '# [Глава 2. Вторая попытка :|: 2]', 'н3',
  ]);
  assert.equal(plan.rows.length, 2, 'проблемная глава в строки не попадает');
  assert.deepEqual(plan.problems, [1], 'индекс проблемной главы помнится');
  assert.equal(plan.rows[0].site.chapterId, '11');
  assert.equal(plan.rows[1].site.chapterId, '12',
    '«Глава 2. Вторая попытка» — своя глава, к «Дополнительной» она не имеет отношения');
  assert.equal(plan.rows[0].how, 'по названию');
  assert.equal(parsed[1].problem, 'Дробный номер');
});

test('rulate: спорная строка остаётся без главы, кандидаты — в строке', async () => {
  const { plan } = await planFor(SITE_PLAN, ['# [Глава 1. Новое название :|: 1]', 'н1']);
  const [row] = plan.rows;
  assert.equal(row.site, null, 'название не совпало — не угадываем');
  assert.equal(row.how, 'спорная');
  assert.deepEqual(row.cands.map((c) => c.chapterId).sort(), ['11', '14']);
});

test('rulate: ручное назначение — строку ведёт человек, а не догадка', async () => {
  const { api, parsed, plan } = await planFor(SITE_PLAN,
    ['# [Глава 1. Новое название :|: 1]', 'н1']);
  const res = api.planAssign(plan, 0, '14');
  assert.deepEqual(res, { ok: true });
  assert.equal(plan.rows[0].site.chapterId, '14');
  assert.equal(plan.rows[0].how, 'вручную');
  assert.equal(plan.rows[0].site.siteTitle, 'Глава 1. Дополнительная глава',
    'подпись берётся из оглавления, а не из id');

  assert.equal(api.planAssign(plan, 0, '999').ok, false, 'чужого id в плане быть не может');
  assert.equal(plan.rows[0].site.chapterId, '14', 'неудачное назначение не портит строку');

  const skip = api.planAssign(plan, 0, null);
  assert.equal(skip.ok, true);
  assert.equal(plan.rows[0].site, null);
  assert.equal(plan.rows[0].how, 'пропуск (ваше решение)');

  const overrides = api.collectOverrides(plan, parsed);
  assert.deepEqual(overrides, { '1|глава 1. новое название': null },
    'пропуск запоминается как «не лить», а не как «нет такого ключа»');
});

test('rulate: одна глава сайта в двух строках — видно, кто занял', async () => {
  const { api, plan } = await planFor(SITE_PLAN, [
    '# [Глава 1. История :|: 1]', 'н1', '',
    '# [Глава 2. Вторая попытка :|: 2]', 'н2',
  ]);
  api.planAssign(plan, 1, '11');
  const [, second] = plan.rows;
  assert.equal(second.site.chapterId, '11');
  assert.equal(second.host, '0', 'вторая строка помечена занятой — но выбор человека не отменён');
  assert.equal(plan.rows[0].host, '');
});

test('rulate: план переживает новый файл — ключ это номер и название', async () => {
  const { api, parsed, plan } = await planFor(SITE_PLAN, [
    '# [Глава 1. История :|: 1]', 'н1', '',
    '# [Глава 2. Вторая попытка :|: 2]', 'н2',
  ]);
  api.planAssign(plan, 1, '14');
  const overrides = api.collectOverrides(plan, parsed);
  assert.deepEqual(overrides, { '2|глава 2. вторая попытка': '14' });

  // файл перекомпилировали: появилась глава в начале, индексы строк съехали
  const { api: api2, plan: plan2 } = await planFor(SITE_PLAN, [
    '# [Пролог :|: 0]', 'н0', '',
    '# [Глава 1. История :|: 1]', 'н1', '',
    '# [Глава 2. Вторая попытка :|: 2]', 'н2',
  ], (a) => { a.CFG.titleMode = 'off'; });
  const parsed2 = api2.parseChaptersFile([
    '# [Пролог :|: 0]', 'н0', '',
    '# [Глава 1. История :|: 1]', 'н1', '',
    '# [Глава 2. Вторая попытка :|: 2]', 'н2',
  ].join('\n'));
  const res = api2.applyOverrides(plan2, parsed2, overrides);
  assert.equal(res.assigned, 1, 'решение нашлось по названию, а не по индексу строки');
  const row = plan2.rows.find((r) => r.num === 2);
  assert.equal(row.site.chapterId, '14', 'выбранная вручную глава сохранена');
});

test('rulate: экспорт и импорт плана возвращают то же решение', async () => {
  const { api, parsed, plan, site } = await planFor(SITE_PLAN, [
    '# [Глава 1. Новое название :|: 1]', 'н1', '',
    '# [Глава 2. Вторая попытка :|: 2]', 'н2',
  ]);
  api.planAssign(plan, 0, '14');
  const data = api.planToData(plan, parsed);
  assert.equal(data.app, 'NovelMaestro rulate plan');
  assert.equal(data.rows[0].site.id, '14');
  assert.deepEqual(data.unused.map((g) => g.id).sort(), ['11', '15'],
    'свободные главы сайта выгружаются подсказкой: занятые в план не попадают');

  /* Импорт восстанавливает сопоставление целиком, а не только ручные правки:
     файл плана — это и есть решение «что куда лить». */
  const back = api.planFromData(data, parsed, site);
  assert.deepEqual(back.overrides, {
    '1|глава 1. новое название': '14',
    '2|глава 2. вторая попытка': '12',
  });
  assert.deepEqual(back.notes, []);

  // чужая книга/чужой файл — отчёт, а не молчание
  const foreign = api.planFromData({ book: '999', rows: [] }, parsed, site);
  assert.ok(foreign.notes.length > 0);
  assert.equal(foreign.book, '999');
  const garbage = api.planFromData({ hello: 1 }, parsed, site);
  assert.ok(garbage.notes[0].includes('NovelMaestro rulate plan'));
});

test('rulate: импорт сверяет главы по названию, id лишь подсказка', async () => {
  const { api, parsed, site } = await planFor(SITE_PLAN, ['# [Глава 1. История :|: 1]', 'н1']);
  const data = {
    app: 'NovelMaestro rulate plan', version: 1, book: String(BOOK),
    rows: [{ num: 1, title: 'Глава 1. История', site: { id: '777', title: 'Глава 1. История' } }],
  };
  const res = api.planFromData(data, parsed, site);
  assert.deepEqual(res.overrides, { '1|глава 1. история': '11' },
    'после перезалива сайт выдал новые id — глава нашлась по названию');
  assert.ok(res.notes.some((n) => n.includes('id=777')));
});

test('rulate: заливка идёт строго по плану, догадки не пересматриваются', async () => {
  const { api, posts, parsed, plan } = await planFor(SITE_PLAN, [
    '# [Глава 1. Новое название :|: 1]', 'н1', '',
    '# [Глава 2. Вторая попытка :|: 2]', 'н2',
  ]);
  api.planAssign(plan, 0, '14');
  const logs = [];
  const res = await api.writePlan(plan, parsed,
    { dryRun: false, skipSame: false, rangeStr: '' },
    { aborted: false, log: (m) => logs.push(m), setProgress() {} });
  assert.deepEqual(posts, ['14', '12'], 'спорная глава ушла туда, куда указал человек');
  assert.equal(res.saved, 2);
});

test('rulate: «только выделенные» льёт выбранные строки и ничего больше', async () => {
  const { api, posts, parsed, plan } = await planFor(SITE_PLAN, [
    '# [Глава 1. История :|: 1]', 'н1', '',
    '# [Глава 2. Вторая попытка :|: 2]', 'н2',
  ]);
  const res = await api.writePlan(plan, parsed,
    { dryRun: false, skipSame: false, rangeStr: '', onlyFi: [1] },
    { aborted: false, log: () => {}, setProgress() {} });
  assert.deepEqual(posts, ['12']);
  assert.equal(res.saved, 1);
});

test('rulate: «не найдена» в плане не превращается в запись, chrF останавливает чужую', async () => {
  const site = [{ id: 21, title: 'Глава 7. Совсем другое', text: 'Он плыл по морю, шторм рвал паруса, и берег исчез.' }];
  const { api, posts, parsed } = await planFor(site, ['# [Глава 7. Моя глава :|: 7]', 'Тихий вечер, чай, разговор о книгах и планы на завтра.'],
    (a) => { a.CFG.minChrf = 0.75; });
  const siteData = await api.buildSiteMap(String(BOOK));
  const plan = api.buildPlan(parsed, siteData);
  api.planAssign(plan, 0, '21'); // человек настоял, но глава на сайте — другая
  const logs = [];
  const res = await api.writePlan(plan, parsed,
    { dryRun: false, skipSame: false, rangeStr: '' },
    { aborted: false, log: (m) => logs.push(m), setProgress() {} });
  assert.deepEqual(posts, [], 'chrF — последний заслон, даже для ручного назначения');
  assert.equal(res.fail, 1);
  assert.ok(logs.some((l) => l.includes('ОТКАЗ')));
});

test('rulate: текстовый план читается глазами и содержит решения', async () => {
  const { api, parsed, plan } = await planFor(SITE_PLAN, [
    '# [Глава 1. История :|: 1]', 'н1', '',
    '# [Глава 2. Вторая попытка :|: 2]', 'н2',
  ]);
  api.planAssign(plan, 1, '14');
  const text = api.planToText(plan, parsed);
  assert.ok(text.includes('1. Глава 1. История\t«Глава 1. История» (id=11)'), text);
  assert.ok(text.includes('2. Глава 2. Вторая попытка\t«Глава 1. Дополнительная глава» (id=14)\tвручную'), text);
});

test('rulate: «не найдена» из файла плана не становится «вашим решением»', async () => {
  /* Строка без главы сайта — это НЕ решение человека. Если записать её как
     «пропуск», после импорта ошибочный разбор скрипта закрепится, и строка
     перестанет искаться в следующий раз. */
  const { api, parsed, plan, site } = await planFor(SITE_PLAN,
    ['# [Глава 1. История :|: 1]', 'н1', '', '# [Глава 9. Такой нет :|: 9]', 'н9']);
  const missing = plan.rows.find((r) => r.num === 9);
  assert.equal(missing.site, null);
  assert.equal(missing.how, 'нет');

  const data = api.planToData(plan, parsed);
  const back = api.planFromData(data, parsed, site);
  assert.deepEqual(back.overrides, { '1|глава 1. история': '11' },
    'в файл плана попал только реальный пропуск — «нет» осталось «нет»');
});

test('rulate: явный пропуск строки переживает импорт', async () => {
  const { api, parsed, plan, site } = await planFor(SITE_PLAN,
    ['# [Глава 1. История :|: 1]', 'н1', '', '# [Глава 2. Вторая попытка :|: 2]', 'н2']);
  api.planAssign(plan, 1, null); // человек снял главу
  assert.equal(plan.rows[1].how, 'пропуск (ваше решение)');

  const data = api.planToData(plan, parsed);
  const back = api.planFromData(data, parsed, site);
  assert.deepEqual(back.overrides, {
    '1|глава 1. история': '11',
    '2|глава 2. вторая попытка': null,
  }, 'снятая человеком строка — это решение, его и надо помнить');
});

/* ── пакетные действия и черновое выравнивание ── */

test('rulate: «снять выделенные» не трогает уже снятое и считает сделанное', async () => {
  const { api, plan } = await planFor(SITE_PLAN, [
    '# [Глава 1. История :|: 1]', 'н1', '',
    '# [Глава 2. Вторая попытка :|: 2]', 'н2', '',
  ]);
  assert.equal(api.planSkipSelected(plan, [0, 1]), 2);
  assert.equal(api.planSkipSelected(plan, [0, 1]), 0, 'повторное снятие ничего не делает');
  assert.ok(plan.rows.every((r) => r.how.startsWith('пропуск (ваше')));
});

test('rulate: «сбросить» возвращает строку к решению скрипта', async () => {
  const site = [
    { id: 11, title: 'Глава 1. История', text: 'т' },
    { id: 13, title: 'Глава 3. Дальше', text: 'т' },
  ];
  const { api, parsed, plan } = await planFor(site, [
    '# [Глава 1. История :|: 1]', 'н1', '',
    '# [Глава 3. Новое название :|: 3]', 'н3',
  ]);
  assert.equal(plan.rows[1].how, 'по номеру 3', 'номер один — скрипт решает сам');

  api.planAssign(plan, 1, '11');
  assert.equal(plan.rows[1].how, 'вручную');

  api.planResetSelected(plan, parsed, [1]);
  assert.equal(plan.rows[1].site.chapterId, '13', 'вернулось решение скрипта');
  assert.equal(plan.rows[1].how, 'по номеру 3');
  assert.equal(Object.keys(api.collectOverrides(plan, parsed)).length, 0, 'ручная правка снята');
});

test('rulate: «по порядку» выравнивает промежуток между якорями', async () => {
  // на сайте между якорями две главы, в файле — те же две без названий
  const site = [
    { id: 11, title: 'Глава 1. Начало', text: 'т' },
    { id: 12, title: 'Глава 2. Середина', text: 'т' },
    { id: 13, title: 'Глава 3. Ещё середина', text: 'т' },
    { id: 14, title: 'Глава 4. Конец', text: 'т' },
  ];
  const { api, parsed, plan } = await planFor(site, [
    '# [Глава 1. Начало :|: 1]', 'н1', '',
    '# [Пролог :|: 0]', 'н2', '',
    '# [Интерлюдия :|: 0]', 'н3', '',
    '# [Глава 4. Конец :|: 4]', 'н4',
  ]);
  const res = api.planAlignByOrder(plan, parsed);
  assert.equal(res.suggested, 2, 'две строки без номера встали по порядку между якорями');
  const rows = plan.rows.filter((r) => r.how === 'по порядку (черновик)');
  assert.deepEqual(rows.map((r) => r.site.chapterId), ['12', '13']);
});

test('rulate: «по порядку» молчит, если промежутки не совпадают по длине', async () => {
  const { api, parsed, plan } = await planFor(SITE_PLAN, [
    '# [Глава 1. История :|: 1]', 'н1', '',
    '# [Пролог :|: 0]', 'н2', '',
    '# [Глава 2. Вторая попытка :|: 2]', 'н3',
  ]);
  const res = api.planAlignByOrder(plan, parsed);
  assert.equal(res.suggested, 0, 'одна строка против двух глав сайта — не выравниваем');
  assert.ok(res.note.length > 0);
});

test('rulate: снятая вручную строка с кандидатами не считается неоднозначной', async () => {
  const { api, parsed, plan } = await planFor(SITE_PLAN,
    ['# [Глава 1. Новое название :|: 1]', 'н1']);
  assert.equal(plan.rows[0].cands.length, 2, 'у строки есть кандидаты по номеру');
  api.planAssign(plan, 0, null); // человек решил не лить
  const logs = [];
  await api.writePlan(plan, parsed,
    { dryRun: false, skipSame: false, rangeStr: '' },
    { aborted: false, log: (m) => logs.push(m), setProgress() {} });
  assert.ok(logs.some((l) => l.includes('снята вручную')), logs.join('\n'));
  assert.ok(!logs.some((l) => l.includes('НЕОДНОЗНАЧНО')), 'решение человека — не путаница скрипта');
});

/* ── порог chrF как поле панели ── */

test('rulate: порог chrF читается из поля; пусто и мусор — выключено', () => {
  const { api } = boot([]);
  assert.equal(api.parseChrfThreshold('0.9'), 0.9);
  assert.equal(api.parseChrfThreshold('0'), 0, '0 — сверка выключена');
  assert.equal(api.parseChrfThreshold('0,8'), 0.8, 'десятичная запятая допустима');
  assert.equal(api.parseChrfThreshold(''), 0, 'пустое поле — то же, что 0: выключено');
  assert.equal(api.parseChrfThreshold('   '), 0);
  assert.equal(api.parseChrfThreshold('abc'), 0, 'мусор — выключено, а не потайной порог');
  assert.equal(api.parseChrfThreshold('-1'), 0, 'отрицательное — то же, что 0');
  assert.equal(api.parseChrfThreshold('5'), 1, 'выше единицы порог не бывает');
  /* Дефолт стоит в поле панели (CFG.minChrf), а не в разборе: стенд его
     обнуляет для тестов сопоставления, поэтому смотрим исходник. */
  assert.match(raw, /minChrf: 0\.75/, 'порог по умолчанию пропал из CFG');
  assert.match(raw, /id="ru-chrf" type="number" value="\$\{CFG\.minChrf\}"/,
    'поле chrF обязано быть предзаполнено порогом из CFG');
});

test('rulate: при пороге 0 запись не отказывает, потому что сверять не с чем', async () => {
  const site = [{ id: 21, title: 'Глава 7. Совсем другое', text: 'Он плыл по морю, шторм рвал паруса.' }];
  const { api, posts, parsed } = await planFor(site,
    ['# [Глава 7. Моя глава :|: 7]', 'Тихий вечер, чай, разговор о книгах.'],
    (a) => { a.CFG.minChrf = 0; });
  const siteData = await api.buildSiteMap(String(BOOK));
  const plan = api.buildPlan(parsed, siteData);
  api.planAssign(plan, 0, '21');
  const logs = [];
  await api.writePlan(plan, parsed,
    { dryRun: false, skipSame: false, rangeStr: '' },
    { aborted: false, log: (m) => logs.push(m), setProgress() {} });
  assert.deepEqual(posts, ['21'], 'человек выключил заслон осознанно — заливаем');
  assert.ok(!logs.some((l) => l.includes('ОТКАЗ')), logs.join('\n'));
});

/* ── диапазон глав рядом с коллизиями номеров ── */

/* Сайт с коллизиями: на номер 1 и на номер 3 по две главы, пролог без номера. */
const SITE_DUP = [
  { id: 11, title: 'Глава 1. История', text: 'с1' },
  { id: 14, title: 'Глава 1. Дополнительная глава', text: 'с1' },
  { id: 12, title: 'Глава 2. Вторая попытка', text: 'с2' },
  { id: 13, title: 'Глава 3. Третья', text: 'с3' },
  { id: 15, title: 'Глава 3. Бонус', text: 'с3' },
  { id: 30, title: 'Пролог. Начало', text: 'с0' },
];
const FILE_DUP = [
  '# [Глава 1. Новое название :|: 1]', 'н1', '',
  '# [Глава 2. Вторая попытка :|: 2]', 'н2', '',
  '# [Глава 3. Тоже новое :|: 3]', 'н3', '',
  '# [Пролог. Начало :|: 0]', 'н0',
];

test('rulate: вне диапазона строка не держит главу сайта за собой', async () => {
  /* Строка 1 назначена вручную на главу 12 (та же, что у строки 2), но
     строка 1 — вне диапазона 2: в этом прогоне её не пишут, и «чужой»
     пометка у соседки — это шум от строки, которой в работе нет. */
  const { api, posts, parsed, plan } = await planFor(SITE_DUP, FILE_DUP, (a) => { a.CFG.minChrf = 0; });
  api.planSetRange(plan, '2');
  api.planAssign(plan, 0, '12');
  const rowTwo = plan.rows.find((r) => r.num === 2);
  assert.equal(api.planRowRange(plan.rows[0], plan.rangeSet), 'вне диапазона');
  assert.equal(rowTwo.host, '', 'строка вне диапазона не занимает главу');
  const res = await api.writePlan(plan, parsed,
    { dryRun: false, skipSame: false, rangeStr: '2' },
    { aborted: false, log: () => {}, setProgress() {} });
  assert.deepEqual(posts, ['12'], 'вне диапазона ничего не пишется');
  assert.equal(res.saved, 1);
  assert.equal(res.skipRange, 3, 'остальные строки — вне диапазона, а не ошибки');
});

test('rulate: вне диапазона строка не считается неоднозначной', async () => {
  /* Номер 3 на сайте у двух глав, название не совпало — спорная строка есть,
     но она вне диапазона 2. Раньше проверка диапазона стояла ПОСЛЕ разбора
     спорной строки, и скрипт сообщал о неоднозначности строки, которой
     в этом прогоне нет. */
  const { api, posts, parsed, plan } = await planFor(SITE_DUP, FILE_DUP, (a) => { a.CFG.minChrf = 0; });
  api.planSetRange(plan, '2');
  const logs = [];
  const res = await api.writePlan(plan, parsed,
    { dryRun: false, skipSame: false, rangeStr: '2' },
    { aborted: false, log: (m) => logs.push(m), setProgress() {} });
  assert.deepEqual(posts, ['12']);
  assert.equal(res.ambiguous, 0, 'спорных в работе нет');
  assert.ok(!logs.some((l) => l.includes('НЕОДНОЗНАЧНО')), logs.join('\n'));
  assert.ok(logs.some((l) => l.includes('вне диапазона (№3)')), 'пропуск по диапазону виден в логе');
});

test('rulate: глава без номера диапазоном не режется', async () => {
  /* Пролог без «Глава N» в названии и без числа в теге получает номер null —
     судить о его попадании в диапазон не по чему, и выкидывать его молча
     нельзя: глава из работы выпала бы без единой строки в логе. */
  const lines = ['# [Глава 2. Вторая попытка :|: 2]', 'н2', '', '# [Пролог. Начало :|: —]', 'н0'];
  const { api, posts, plan } = await planFor(SITE_DUP, lines, (a) => { a.CFG.minChrf = 0; });
  api.planSetRange(plan, '2');
  const parsed = api.parseChaptersFile(lines.join('\n'));
  assert.equal(parsed[1].num, null, 'у пролога номера нет — и в теге тоже');
  const pr = await api.writePlan(plan, parsed,
    { dryRun: false, skipSame: false, rangeStr: '2' },
    { aborted: false, log: () => {}, setProgress() {} });
  assert.deepEqual(posts, ['12', '30'], 'пролог остаётся в работе');
  assert.equal(pr.skipRange, 0);
});

test('rulate: «1—2» любой тире — диапазон, а не «вся книга»', async () => {
  /* Раньше принимались только «-» и «–»: человек писал «1—50», скрипт молча
     заливал всю книгу. */
  const { api } = boot([]);
  for (const s of ['1-2', '1–2', '1—2', '1−2']) {
    assert.deepEqual([...api.parseRange(s)].sort((a, b) => a - b), [1, 2], `не разобрано: ${s}`);
  }
  assert.equal(api.parseRange(''), null, 'пусто — вся книга');
  assert.equal(api.parseRange('   '), null);
  assert.deepEqual([...api.parseRange('3-1')].sort((a, b) => a - b), [1, 2, 3],
    'обратный интервал — тот же диапазон');
});

test('rulate: неразобранный кусок диапазона не выбрасывается молча', async () => {
  const { api } = boot([]);
  const rep = api.parseRangeReport('1-2, abc; 7');
  assert.deepEqual([...rep.set].sort((a, b) => a - b), [1, 2, 7], 'распознанное работает');
  assert.deepEqual(rep.invalid, ['abc'], 'непонятное названо, а не проглочено');
  assert.deepEqual(api.parseRangeReport('').invalid, []);
});

test('rulate: диапазон в плане и в заливке — одно правило', async () => {
  /* План показывает «вне диапазона», заливка по тому же плану делает то же
     самое: два места, одна функция inRange. */
  const { api, posts, parsed, plan } = await planFor(SITE_DUP, FILE_DUP, (a) => { a.CFG.minChrf = 0; });
  api.planSetRange(plan, '1-2');
  const marked = plan.rows.filter((r) => api.planRowRange(r, plan.rangeSet) === '').map((r) => r.num);
  assert.deepEqual(marked, [1, 2], 'в работе только 1 и 2');
  const res = await api.writePlan(plan, parsed,
    { dryRun: false, skipSame: false, rangeStr: '1-2' },
    { aborted: false, log: () => {}, setProgress() {} });
  assert.deepEqual(posts, ['12'], 'спорная 1 не залита, 2 — залита');
  assert.equal(res.skipRange, 2, 'вне диапазона — 3 и пролог 0');
});

test('rulate: глава без номера не теряется без «Title fallback»', async () => {
  /* Галка дублировала то же решение: без неё пролог выпадал из плана целиком
     («номер не разобрался, название не совпало» просто не попадало в строки),
     и человек не видел, что глава осталась за бортом. Теперь строка есть
     всегда — со своим решением или с причиной «нет». */
  const { parsed, plan } = await planFor(SITE_DUP,
    ['# [Глава 1. История :|: 1]', 'н1', '', '# [Без название на сайте :|: 0]', 'н0']);
  const odd = plan.rows.find((r) => r.title === 'Без название на сайте');
  assert.ok(odd, 'строка с неразобранным номером есть в плане');
  assert.equal(odd.site, null);
  assert.equal(odd.how, 'нет');
  assert.ok(parsed.length >= 2);
});

/* ── подбор кандидатов по названию (chrF) ── */

test('rulate: ядро названия отбрасывает «Глава N.»', () => {
  const { api } = boot([]);
  assert.equal(api.titleCore('Глава 12. Битва у реки'), 'Битва у реки');
  assert.equal(api.titleCore('Глава 1.'), '', 'только префикс — ядра нет');
  assert.equal(api.titleCore('Том 2: Прибытие'), 'Прибытие');
  assert.equal(api.titleCore('Chapter 5 — The End'), 'The End');
  assert.equal(api.titleCore('Пролог. Начало'), 'Пролог. Начало', 'без префикса — целиком');
});

test('rulate: chrF названий ставит родственное название выше чужого', () => {
  const { api } = boot([]);
  const near = api.titleScore('Глава 1. Битва у реки', 'Глава 1. Битва на реке');
  const far = api.titleScore('Глава 1. Битва у реки', 'Глава 1. Совет старейшин');
  assert.ok(near > far, `родственное (${near.toFixed(2)}) должно быть выше чужого (${far.toFixed(2)})`);
  assert.equal(api.titleScore('Глава 3. Имя', 'Глава 3. Имя'), 1, 'одно название — 1.0');
});

test('rulate: кандидаты отсортированы по похожести, но решает человек', async () => {
  /* Главное свойство: счёт — ПОДСКАЗКА. Автовыбора нет, все кандидаты на
     месте, иначе однажды перевод уедет в чужую главу «по наибольшему счёту». */
  const site = [
    { id: 11, title: 'Глава 1. Совет старейшин', text: 'с1' },
    { id: 12, title: 'Глава 1. Битва на реке', text: 'с2' },
    { id: 13, title: 'Глава 1. Дополнительная глава', text: 'с3' },
  ];
  const { api, plan } = await planFor(site, ['# [Глава 1. Битва у реки :|: 1]', 'н1']);
  const [row] = plan.rows;
  const ranked = api.rankCandidates(row.title, row.cands);
  assert.equal(ranked[0].chapterId, '12', 'ближайшее название — первым');
  assert.equal(ranked.length, 3, 'все кандидаты остаются, никто не выброшен');
  assert.equal(row.site, null, 'подбор по названию не назначает главу сам');
  assert.ok(ranked[0].same, 'родственное название помечено как внятное');
  assert.equal(ranked[ranked.length - 1].same, false, 'чужое название не притворяется похожим');
});

test('rulate: счёт не показывается у непохожих названий', () => {
  const { api } = boot([]);
  const site = [
    { chapterId: '11', siteTitle: 'Глава 9. Совсем о другом' },
    { chapterId: '12', siteTitle: 'Глава 9. Ещё о разном' },
  ];
  const ranked = api.rankCandidates('Глава 9. Битва у реки', site);
  assert.ok(ranked.every((c) => !c.same), 'низкий счёт не выдаётся за похожесть');
  assert.ok(ranked.every((c) => c.score < api.TITLE_SIMILAR_MIN));
});

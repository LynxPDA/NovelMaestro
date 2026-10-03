// NovelMaestro Lite: извлечение терминов (NER) — что уезжает в промпт извлечения,
// кто считает частоты и что происходит с ответом модели, нарушающим контракт
// («не возвращай существующие термины»).
// Части юзерскрипта — один IIFE, целиком в node они не исполняются (нужны DOM и GM_*),
// поэтому подопытные части вырезаются из артефакта по их баннерам и исполняются как
// чистый код на заглушках: LLM — мок, индексной базы нет, страница — фиктивный URL.
// Запуск: node --test tests/tools/*.test.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const ARTIFACT = path.join(REPO, 'tools/NovelMaestro_Lite/novelmaestro-lite.user.js');
const raw = fs.readFileSync(ARTIFACT, 'utf8');

// часть — кусок артефакта от своего баннера до баннера следующей
const part = (banner, next) => {
  const from = raw.indexOf(`// ===== ${banner}`);
  const to = raw.indexOf(`// ===== ${next}`);
  assert.ok(from > 0 && to > from, `в артефакте не найдена часть «${banner}»`);
  // левый отступ частей (4 пробела) в изолированном скоупе не нужен
  return raw.slice(from, to).replace(/^ {4}/gm, '');
};
// конфигурация → поиск терминов → книги и IndexedDB сайта → глоссарии → NER: ровно то,
// без чего извлечение не исполняется (UI, читалка, обучение и HTTP в подборку не входят)
const SOURCE = [
  part('КОНФИГУРАЦИЯ', 'ПОИСК ТЕРМИНОВ'),
  part('ПОИСК ТЕРМИНОВ', 'КНИГИ'),
  part('КНИГИ', 'ГЛОССАРИИ'),
  part('ГЛОССАРИИ', 'СИГНАТУРЫ'),
  part('NER', 'ПЕРЕВОД'),
].join('\n');

const BOOK = 'https://site.test/book/1';
const CH = `${BOOK}/chapter/1`;

const make = new Function('GM_getValue', 'location', 'callLLM', 'cancelRequested', `${SOURCE}
return {
    config, books, siteGlossaries, siteJobs,
    extractTermsFromText, requestNerChunk, findRelevantTerms, findGlossaryEntry,
    formatGlossaryForExtraction, nerViolationNote, splitByNewlines, textHash,
    jobOf, migrateJobs, useBook: (key) => { currentBookKey = key; },
};`);

/**
 * Изолированная Lite-среда: одна книга, её глоссарий и фиктивный LLM, который отдаёт
 * заготовленные ответы по порядку и запоминает, что ему реально послали. IndexedDB в
 * node нет: openDb ловит ReferenceError и отдаёт null, поэтому dbPut и jobPut остаются
 * пусттышками — памятью среды остаются siteGlossaries и siteJobs книги.
 */
function harness({ answers = [], config = {}, glossary = {}, job = null, jobs = null, cancel = false, callLLM: customLLM } = {}) {
  const prompts = [];
  let calls = 0;
  const callLLM = customLLM || (async (messages, temperature, stream, cb = {}) => {
    prompts.push(messages[0].content);
    if (cb.onRetry) cb.onRetry({ message: 'нет ответа', nextAttempt: calls + 1, attemptsTotal: 3 });
    const answer = answers[Math.min(calls, answers.length - 1)];
    calls += 1;
    return typeof answer === 'string' ? { text: answer } : answer;
  });
  const scope = make(
    (key, def) => (key === 'config' ? config : (key === 'books' ? { [BOOK]: { name: 'Тест-книга' } } : def)),
    { href: CH },
    callLLM,
    cancel,
  );
  scope.useBook(BOOK);
  scope.siteGlossaries[BOOK] = glossary;
  if (job) scope.siteJobs[BOOK] = { [CH]: { ...job } };
  if (jobs) scope.siteJobs[BOOK] = jobs;
  return { scope, prompts, calls: () => calls, g: () => scope.siteGlossaries[BOOK] };
}

const run = (h, text) => h.scope.extractTermsFromText(text, BOOK, null);
const item = (term, translation, type = 'Person (male)') => ({ term, translation, type });
const json = (items) => JSON.stringify(items);

// ── промпт извлечения ──────────────────────────────────────────────────────────

test('промпт извлечения объясняет существующие термины и просит их не возвращать', () => {
  const def = harness().scope.config.extractionPrompt;
  assert.match(def, /\{targetLang\}[\s\S]*\{existingGlossary\}[\s\S]*\{text\}/, 'плейсхолдеры промпта потерялись');
  assert.match(def, /СУЩЕСТВУЮЩИЕ ТЕРМИНЫ \(уже есть в глоссарии и встречаются в этом чанке\):\n\{existingGlossary\}/, 'список вставлен не в свой блок');
  assert.match(def, /^- НЕ ВОЗВРАЩАЙ термины из этого списка в своём ответе\.$/m, 'запрещающей инструкции нет');
});

test('существующие термины чанка уходят в промпт JSON-массивом без служебных id', async () => {
  const h = harness({
    answers: ['[]'],
    glossary: {
      k1: { term: 'Ван Линь', translation: 'Wang Lin', type: 'Person (male)', count: 41, aliases: ['Вань Линь'] },
      k2: { term: 'Секта Небесного Облака', translation: 'Heavenly Cloud Sect', type: 'Organisation', count: 14 },
      k3: { term: 'Пустая башня', translation: 'Empty Tower', type: 'Location', count: 7 },
    },
  });
  await run(h, 'Ван Линь вошёл в зал. Секта Небесного Облака молчала.');
  assert.equal(h.prompts.length, 1);
  const block = /СУЩЕСТВУЮЩИЕ ТЕРМИНЫ[^\n]*\n(\[[\s\S]*?\])\n\nИНСТРУКЦИЯ/.exec(h.prompts[0]);
  assert.ok(block, 'в запросе нет JSON-блока существующих терминов');
  const records = JSON.parse(block[1]);
  // только релевантные чанку: «Пустая башня» в тексте не встречалась
  assert.deepEqual(records.map((r) => r.term), ['Ван Линь', 'Секта Небесного Облака']);
  assert.deepEqual(records[0], { term: 'Ван Линь', translation: 'Wang Lin', type: 'Person (male)', count: 41, aliases: ['Вань Линь'] });
  assert.equal(records[1].count, 14);
  assert.ok(!block[1].includes('"id"') && !block[1].includes('k1'), 'служебный ключ записи уехал в модель');
});

test('для чанка без существующих терминов подставляется понятная заглушка', async () => {
  const h = harness({ answers: ['[]'] });
  await run(h, 'Он просто шёл по дороге.');
  assert.match(h.prompts[0], /в этом чанке\):\n\(существующих терминов в этом чанке не найдено\)/);
  assert.ok(!h.prompts[0].includes('{existingGlossary}'), 'плейсхолдер не подставлен');
});

test('свой промпт без {existingGlossary} работает по-прежнему: список в модель не уходит', async () => {
  const h = harness({
    answers: ['[]'],
    config: { extractionPrompt: 'Найди термины.\n{text}' },
    glossary: { k1: { term: 'Ван Линь', translation: 'Wang Lin', type: 'Person (male)', count: 5 } },
  });
  const res = await run(h, 'Ван Линь вошёл в зал.');
  assert.equal(h.prompts.length, 1);
  assert.equal(h.prompts[0], 'Найди термины.\nВан Линь вошёл в зал.');
  // сам промпт старый, а частоты считает код — детерминизм остаётся
  assert.deepEqual([res.added, res.incremented, res.llmViolations], [0, 1, 0]);
  assert.equal(h.g().k1.count, 6);
});

// ── частоты считает код ────────────────────────────────────────────────────────

test('все термины новые: count = 1 в новой записи, алгоритмического ничего нет', async () => {
  const h = harness({
    answers: [json([item('Ван Линь', 'Wang Lin'), item('Секта Небесного Облака', 'Heavenly Cloud Sect', 'Organisation'), item('Меч Божественного Ветра', 'Divine Wind Sword', 'Artifact')])],
  });
  const res = await run(h, 'Ван Линь достал Меч Божественного Ветра. Секта Небесного Облака молчала.');
  assert.deepEqual([res.added, res.incremented, res.llmViolations, res.skipped, res.badItems], [3, 0, 0, 0, 0]);
  assert.deepEqual(Object.values(h.g()).map((t) => t.count), [1, 1, 1]);
});

test('идеальный ответ: частоты алгоритмические, модель дала только новый термин', async () => {
  const h = harness({
    answers: [json([item('Меч Божественного Ветра', 'Divine Wind Sword', 'Artifact')])],
    glossary: {
      k1: { term: 'Ван Линь', translation: 'Wang Lin', type: 'Person (male)', count: 41 },
      k2: { term: 'Секта Небесного Облака', translation: 'Heavenly Cloud Sect', type: 'Organisation', count: 14 },
      k3: { term: 'Пустая башня', translation: 'Empty Tower', type: 'Location', count: 7 },
      broken: null,
    },
  });
  const res = await run(h, 'Ван Линь вошёл в Секту Небесного Облака и достал Меч Божественного Ветра.');
  assert.deepEqual([res.added, res.incremented, res.llmViolations], [1, 2, 0]);
  assert.equal(h.g().k1.count, 42);
  assert.equal(h.g().k2.count, 15);
  assert.equal(h.g().k3.count, 7, 'термин вне чанка частоту не потерял');
  // тип с LLM приходит в свободном написании — нормализуется тем же путём
  const fresh = Object.values(h.g()).find((t) => t && t.term === 'Меч Божественного Ветра');
  assert.deepEqual([fresh.translation, fresh.type, fresh.count], ['Divine Wind Sword', 'Artifact', 1]);
});

test('count — +1 за чанк, сколько раз модель ни прислала термин', async () => {
  const h = harness({
    answers: [json([item('Ван Линь', 'Wang Ling'), item('Ван Линь', 'Wang Ling'), item('Ван Линь', 'Wang Ling')])],
    glossary: { k1: { term: 'Ван Линь', translation: 'Wang Lin', type: 'Person (male)', count: 3 } },
  });
  const res = await run(h, 'Ван Линь молчал.');
  assert.deepEqual([res.added, res.incremented, res.llmViolations], [0, 1, 3]);
  assert.equal(h.g().k1.count, 4, 'частота по-прежнему зависит от ответа модели');
  assert.equal(h.g().k1.translation, 'Wang Lin', 'перевод существующего термина перезаписан');
});

test('нарушение контракта: существующая запись не трогается вообще', async () => {
  const h = harness({
    answers: [json([item('Ван Линь', 'Ван Лин (неправильно)'), item('Меч Божественного Ветра', 'Divine Wind Sword', 'Artifact')])],
    glossary: { k1: { term: 'Ван Линь', translation: 'Wang Lin', type: 'Person (male)', count: 41 } },
  });
  const res = await run(h, 'Ван Линь и Меч Божественного Ветра.');
  assert.deepEqual([res.added, res.incremented, res.llmViolations], [1, 1, 1]);
  assert.deepEqual(Object.values(h.g()).map((t) => t.translation), ['Wang Lin', 'Divine Wind Sword']);
  assert.equal(h.g().k1.type, 'Person (male)');
  assert.equal(h.g().k1.count, 42, 'частота не должна считаться и из ответа модели');
});

test('модель дважды прислала новый термин: второй — тоже нарушение', async () => {
  const h = harness({ answers: [json([item('Меч Божественного Ветра', 'Divine Wind Sword', 'Artifact'), item('Меч Божественного Ветра', 'Divine Wind Sword', 'Artifact')])] });
  const res = await run(h, 'Меч Божественного Ветра сверкнул.');
  assert.deepEqual([res.added, res.incremented, res.llmViolations], [1, 0, 1]);
  assert.equal(Object.keys(h.g()).length, 1);
});

// ── дедупликация, resume и отказ ───────────────────────────────────────────────

test('«термин уже в глоссарии»: точное совпадение важнее нечёткого', () => {
  const { scope } = harness();
  const glossary = { '1': { term: 'Ван Линька', translation: 'Wang Linka' }, '2': { term: 'Ван Линь', translation: 'Wang Lin' } };
  assert.equal(scope.findGlossaryEntry(glossary, 'Ван Линь'), '2', 'нечёткое совпадение оказалось раньше точного');
  assert.equal(scope.findGlossaryEntry(glossary, 'ван  линь'), '2');
  assert.equal(scope.findGlossaryEntry(glossary, 'Меч'), '');
  assert.equal(scope.findGlossaryEntry({ bad: null }, 'Ван Линь'), '');
});

test('resume: уже обработанные чанки не считаются заново', async () => {
  const text = 'Ван Линь вошёл в зал.\n\nСекта Небесного Облака молчала.\n\nМеч Божественного Ветра сверкнул.';
  const h = harness({
    answers: ['[]'],
    config: { chunkSize: 1 },
    glossary: {
      k1: { term: 'Ван Линь', translation: 'Wang Lin', type: 'Person (male)', count: 5 },
      k2: { term: 'Секта Небесного Облака', translation: 'Heavenly Cloud Sect', type: 'Organisation', count: 2 },
    },
  });
  // чанкование по одному абзацу, первый уже обработан прежним прогоном
  assert.equal(h.scope.splitByNewlines(text, 1).length, 3);
  h.scope.siteJobs[BOOK] = { [CH]: { hash: h.scope.textHash(text), nerDone: 1, nerTotal: 3 } };
  const res = await run(h, text);
  assert.deepEqual([res.added, res.incremented, res.llmViolations, res.resumed], [0, 1, 0, 1]);
  assert.equal(h.prompts.length, 2, 'чанк из прошлого прогона ушёл в модель повторно');
  assert.equal(h.g().k1.count, 5, 'частота из пройденного чанка выросла дважды');
  assert.equal(h.g().k2.count, 3);
  assert.ok(h.prompts[0].includes('Heavenly Cloud Sect') && !h.prompts[0].includes('Wang Lin'), 'в промпт уехал нерелевантный термин');
});

// ── частоты считает код (findRelevantTerms): нечёткость — это обработка склонений ──

test('частота по коду работает через морфологию: нечёткий хит считается', async () => {
  const h = harness({
    answers: ['[]'],
    glossary: { k1: { term: 'Секта Небесного Облака', translation: 'Heavenly Cloud Sect', type: 'Organisation', count: 3 } },
  });
  // «в Секту Небесного Облака» — склонение: точного вхождения нет, но это законный хит
  const res = await run(h, 'Он шёл в Секту Небесного Облака.');
  assert.equal(res.incremented, 1);
  assert.equal(h.g().k1.count, 4);
});

test('алиас считается частотой наравне с термином', async () => {
  const h = harness({
    answers: ['[]'],
    glossary: { k1: { term: 'Ван Линь', translation: 'Wang Lin', type: 'Person (male)', count: 5, aliases: ['Вань Линь'] } },
  });
  const res = await run(h, 'Вань Линь молчал.');
  assert.equal(res.incremented, 1);
  assert.equal(h.g().k1.count, 6);
});

test('модель вернула вариант-алиас существующей записи: дубля нет, это нарушение', async () => {
  const h = harness({
    answers: [json([item('Вань Линь', 'Wang Lin')])],
    glossary: { k1: { term: 'Ван Линь', translation: 'Wang Lin', type: 'Person (male)', count: 5, aliases: ['Вань Линь'] } },
  });
  const res = await run(h, 'Вань Линь молчал.');
  assert.deepEqual([res.added, res.llmViolations], [0, 1], 'алиас существующей записи создал дубль');
  assert.equal(Object.keys(h.g()).length, 1);
  assert.equal(h.g().k1.count, 6, 'частота посчитана кодом ровно один раз');
});

// ── реентрант-гвард ─────────────────────────────────────────────────────

test('второй параллельный прогон не стартует: count растёт ровно на один', async () => {
  let arrivals = 0;
  let gate = () => {};
  const h = harness({
    // первый прогон висит в запросе, пока второй не дойдёт до гварда — но второй
    // гардом отклоняется ДО запроса, поэтому первый рано или поздно отпускаем
    callLLM: async (messages) => {
      arrivals += 1;
      h.prompts.push(messages[0].content);
      if (arrivals === 1) setTimeout(gate, 25);
      return { text: '[]' };
    },
    glossary: { k1: { term: 'Ван Линь', translation: 'Wang Lin', type: 'Person (male)', count: 5 } },
  });
  const gatePromise = new Promise((r) => { gate = r; });
  const [a, b] = await Promise.all([run(h, 'Ван Линь молчал.'), run(h, 'Ван Линь молчал.'), gatePromise]);
  assert.equal((a.busy || false) !== (b.busy || false), true, 'ровно один прогон должен быть отклонён гвардом');
  assert.deepEqual([a.incremented + b.incremented], [1]);
  assert.equal(h.prompts.length, 1, 'отклонённый прогон не ходил в модель');
  assert.equal(h.g().k1.count, 6, 'два параллельных прогона удвоили частоту');
});

test('после отклонённого прогона гвард отпущен: следующий запрос уходит', async () => {
  let arrivals = 0;
  let gate = () => {};
  const h = harness({
    callLLM: async (messages) => {
      arrivals += 1;
      h.prompts.push(messages[0].content);
      if (arrivals === 1) setTimeout(gate, 25);
      return { text: '[]' };
    },
    glossary: { k1: { term: 'Ван Линь', translation: 'Wang Lin', type: 'Person (male)', count: 5 } },
  });
  const gatePromise = new Promise((r) => { gate = r; });
  await Promise.all([run(h, 'Ван Линь молчал.'), run(h, 'Ван Линь молчал.'), gatePromise]);
  const res = await run(h, 'Ван Линь снова молчал.');
  assert.equal(res.busy, undefined);
  assert.equal(h.g().k1.count, 7);
});

test('битый ответ чанка — чанк не обработан: ни новых терминов, ни частот', async () => {
  const h = harness({
    answers: ['модель выдала прозу вместо JSON'],
    glossary: { k1: { term: 'Ван Линь', translation: 'Wang Lin', type: 'Person (male)', count: 5 } },
  });
  const res = await run(h, 'Ван Линь молчал.');
  assert.deepEqual([res.added, res.incremented, res.canceled], [0, 0, false]);
  assert.equal(res.skipped, 1);
  assert.match(res.skippedReason, /JSON/);
  assert.equal(h.calls(), 3, 'чанк не был переспрошен NER_PARSE_ATTEMPTS раз');
  assert.equal(h.g().k1.count, 5);
});

test('отмена: прогон не отправляет запросов и не трогает глоссарий', async () => {
  const h = harness({
    answers: ['[]'],
    glossary: { k1: { term: 'Ван Линь', translation: 'Wang Lin', type: 'Person (male)', count: 5 } },
    cancel: true,
  });
  const res = await run(h, 'Ван Линь молчал.');
  assert.deepEqual([res.canceled, res.added, res.incremented], [true, 0, 0]);
  assert.equal(h.prompts.length, 0);
  assert.equal(h.g().k1.count, 5);
});

test('чанк без валидного ответа не продвигает отметку прогресса: страница повторяема', async () => {
  const h = harness({ answers: ['не JSON'] });
  const res = await run(h, 'Просто текст.');
  assert.equal(res.skipped, 1);
  assert.equal(h.scope.siteJobs[BOOK], undefined, 'прерванный прогон помечен завершённым');
});

// ── пропущенные чанки переживают прогон и переобрабатываются ───────────────────

test('не-JSON в середине: nerDone не переезжает через пропущенный чанк', async () => {
  const text = 'Ван Линь вошёл в зал.\n\nСекта Небесного Облака молчала.\n\nМеч Божественного Ветра сверкнул.\n\nПустая башня стояла в стороне.';
  const h = harness({
    config: { chunkSize: 1 },
    // «Секта» стойко отвечает прозой, остальные чанки — валидным JSON
    callLLM: async (m) => {
      h.prompts.push(m[0].content);
      return m[0].content.includes('Секта Небесного Облака')
        ? { text: 'модель выдала прозу вместо JSON' }
        : { text: '[]' };
    },
  });
  const res = await run(h, text);
  assert.equal(res.skipped, 1);
  const job = h.scope.siteJobs[BOOK][CH];
  assert.equal(job.nerDone, 4, 'последний успешный чанк записан');
  assert.deepEqual(job.nerMissed, [1], 'пропущенный чанк запомнен, а не перепрыгнут');
});

test('следующий прогон дообрабатывает пропущенные чанки и снимает отметку', async () => {
  const text = 'Ван Линь вошёл в зал.\n\nСекта Небесного Облака молчала.\n\nМеч Божественного Ветра сверкнул.\n\nПустая башня стояла в стороне.';
  let answerJson = false;
  const h = harness({
    config: { chunkSize: 1 },
    callLLM: async (m) => {
      h.prompts.push(m[0].content);
      // «Секта» в первом прогоне отвечает прозой, во втором — валидным JSON
      if (m[0].content.includes('Секта Небесного Облака') && !answerJson) return { text: 'не JSON' };
      return { text: json([item('Секта Небесного Облака', 'Heavenly Cloud Sect', 'Organisation')]) };
    },
  });
  const first = await run(h, text);
  assert.equal(first.skipped, 1);
  answerJson = true;
  const second = await run(h, text);
  assert.equal(second.resumed, 4, 'готовые чанки не тронуты');
  assert.equal(second.skipped, 0);
  const secta = Object.values(h.g()).find((t) => t && t.term === 'Секта Небесного Облака');
  assert.ok(secta, 'термин из пропущенного чанка извлечён при повторе');
  assert.equal(h.scope.siteJobs[BOOK], undefined, 'после дообработки отметка NER снята');
  assert.ok(!h.prompts.slice(4).some((p) => p.includes('Ван Линь вошёл')), 'готовые чанки повторно не запрашивались');
});

// ── job-записи по страницам: NER не перекрывает перевод другой главы ────────────

test('NER пишется в запись своей страницы и не трогает чужую', async () => {
  const h = harness({
    answers: ['[]'],
    glossary: { k1: { term: 'Ван Линь', translation: 'Wang Lin', type: 'Person (male)', count: 5 } },
    jobs: { [`${BOOK}/chapter/0`]: { parts: ['кусок перевода', ''], total: 2 } },
  });
  await run(h, 'Ван Линь молчал.');
  const jobs = h.scope.siteJobs[BOOK];
  assert.deepEqual(jobs[`${BOOK}/chapter/0`].parts, ['кусок перевода', ''], 'фоновый перевод другой страницы потерян');
  assert.ok(!jobs[CH], 'завершённое задание NER своей страницы снято');
});

test('легаси-запись одной на книгу переносится под свой url, новая форма читается', () => {
  const { scope } = harness();
  // старый формат: одна запись с полем url
  const legacy = scope.migrateJobs({ url: CH, parts: ['а'], nerDone: 2 });
  assert.deepEqual(legacy[CH], { url: CH, parts: ['а'], nerDone: 2 });
  // уже новый формат проходит насквозь; мусор — пустой словарь
  const fresh = { [CH]: { parts: ['б'] } };
  assert.deepEqual(scope.migrateJobs(fresh), fresh);
  assert.deepEqual(scope.migrateJobs(null), {});
  assert.deepEqual(scope.migrateJobs('мусор'), {});
  // jobOf нового формата ищет по url страницы
  scope.siteJobs[BOOK] = fresh;
  assert.deepEqual(scope.jobOf(BOOK, CH), { parts: ['б'] });
  assert.equal(scope.jobOf(BOOK, `${BOOK}/chapter/9`), null);
});

test('статусы прогона говорят о нарушениях только когда они были', () => {
  const { scope } = harness();
  assert.equal(scope.nerViolationNote({ llmViolations: 0 }), '');
  assert.equal(scope.nerViolationNote({ llmViolations: 2 }), ' • нарушений контракта: 2');
});

// ── стражи раскладки ───────────────────────────────────────────────────────────

test('стражи: промпт, метка поля и лог нарушения живут в артефакте', () => {
  assert.ok(raw.includes('Промпт извлечения терминов ({targetLang}, {existingGlossary}, {text}):'), 'метка поля настроек не знает о новом плейсхолдере');
  assert.ok(raw.includes('LLM violation: вернула существующий термин'), 'нарушение контракта не логируется');
  assert.match(raw, /async function requestNerChunk\(chunkText, onChunk, relevantTerms = findRelevantTerms\(chunkText\)\)/, 'requestNerChunk больше не сам считывает релевантные термины');
  assert.match(raw, /const chunkTerms = findRelevantTerms\(chunks\[i\], glossary\);/, 'частоты больше не считаются по копии глоссария книги');
  // правило «такая запись уже есть» живёт в одном месте (040-glossary), а не инлайн-копиями:
  // одно определение нечёткой проверки; алиасы она охватывает, вызовы — легитимное переиспользование
  assert.equal((raw.match(/function termMatchesText\(/g) || []).length, 1, 'правило дедупликации снова продублировали');
});

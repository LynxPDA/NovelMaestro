// NovelMaestro Lite: призрак оригинала в читалке — интервалы терминов по нормализованной
// карте, чанкование с диапазонами абзацев и совпадение модели при проверке сервера.
// Части юзерскрипта — один IIFE, целиком в node они не исполняются (нужны DOM и GM_*),
// поэтому подопытные блоки вырезаются из артефакта по маркерам и исполняются как чистый
// код на заглушках. Запуск: node --test tests/tools/*.test.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const raw = fs.readFileSync(path.join(REPO, 'tools/NovelMaestro_Lite/novelmaestro-lite.user.js'), 'utf8');

// кусок артефакта от одного маркера до другого
const slice = (from, to, why) => {
  const a = raw.indexOf(from);
  const b = raw.indexOf(to, a);
  assert.ok(a > 0 && b > a, `в артефакте не найден блок: ${why}`);
  return raw.slice(a, b).replace(/^ {4}/gm, '');
};

// поиск терминов и чанкование: интервалы строятся по той же нормализации, что и хит
const SEARCH = slice('const SEARCH_DROP_RE', '// ===== КНИГИ', 'поиск терминов');
// призрак: нормализованная карта и интервалы термина — чистые функции (DOM внутри
// остальных, но они в тестах не вызываются)
const GHOST = slice('const GHOST_MARK_LIMIT', '// новую вкладку открываем', 'призрак оригинала');

const api = new Function('readerContent', 'readerMode', 'config', 'progressStatus', 'progressShow',
  `${SEARCH}\n${GHOST}\nreturn { termRanges, normalizedMap, splitByNewlines, splitByNewlinesRanged, paragraphsOf, estimateTokens };`)(
  { classList: { add() {}, remove() {} }, replaceChildren() {}, querySelectorAll: () => [] },
  { addEventListener() {}, removeEventListener() {} },
  { ghostPreview: true, chunkSize: 10000 },
  () => {},
  () => {},
);

// ── интервалы терминов ─────────────────────────────────────────────────────────

test('нормализованная карта возвращает индекс в оригинале', () => {
  const { norm, back } = api.normalizedMap('Ван,  Линь!');
  assert.equal(norm, 'ванлинь', 'нормализация разошлась с поиском терминов');
  // пробел и запятая вычеркнуты — «Ван» всё равно занимает первые три буквы оригинала
  assert.deepEqual(back[0], [0, 1]);
  assert.deepEqual(back[2], [2, 3]);
  assert.deepEqual(back[3], [6, 7], 'после вычеркнутой пунктуации карта потеряла сдвиг');
});

test('термин находится через пунктуацию и регистр, интервал — в оригинале', () => {
  const text = 'Ван, Линь — сказал он.';
  // интервалы полуоткрытые [начало, конец) — годятся для text.slice как есть
  const [span] = api.termRanges(text, ['ван линь']);
  assert.deepEqual(span, [0, 9]);
  assert.equal(text.slice(span[0], span[1]), 'Ван, Линь');
});

test('алиасы и вложенные вхождения схлопываются в один интервал', () => {
  const text = 'Секта Небесного Облака молчала.';
  const ranges = api.termRanges(text, ['Секта Небесного Облака', 'Облака']);
  assert.equal(ranges.length, 1, `вложенный интервал не схлопнулся: ${JSON.stringify(ranges)}`);
  assert.equal(text.slice(ranges[0][0], ranges[0][1]), 'Секта Небесного Облака');
});

test('вхождения одного термина ограничены двадцатью на абзац', () => {
  const text = Array.from({ length: 40 }, () => 'Ван Линь').join(', ');
  assert.equal(api.termRanges(text, ['Ван Линь']).length, 20);
});

test('термин вне текста интервалов не даёт', () => {
  assert.deepEqual(api.termRanges('Он просто шёл по дороге.', ['Ван Линь']), []);
  assert.deepEqual(api.termRanges('', ['Ван Линь']), []);
});

// ── вспышки по потоку ──────────────────────────────────────────────────────────

test('известные термины глоссария вспышек на старте чанка не дают', () => {
  // вспыхивает ровно то, что модель называет в потоке: пачка известных терминов
  // выедала лимит на весь прогон, и записи модели оставались без подсветки
  assert.ok(!/matched/.test(GHOST), 'в призраке осталась пачка известных терминов');
  const progress = GHOST.slice(GHOST.indexOf('function ghostProgress'), GHOST.indexOf('function ghostHide'));
  assert.ok(!/ghostQueueMarks/.test(progress.slice(0, progress.indexOf('st.item'))),
    'старт чанка снова ставит вспышки помимо записей потока');
});

test('перерисовка абзаца продолжает горящую вспышку, а не начинает её заново', () => {
  // вспышка длится GHOST_FLASH_MS; при следующей записи в том же абзаце уже
  // отрисованный интервал получает отрицательную задержку, а отгоревший — null
  const api2 = new Function('readerContent', 'readerMode', 'config', 'progressStatus',
    `${SEARCH}\n${GHOST}\nreturn { ghostFlashDelay, GHOST_FLASH_MS };`)(
    { classList: { add() {}, remove() {} }, replaceChildren() {}, querySelectorAll: () => [] },
    { addEventListener() {}, removeEventListener() {} },
    { ghostPreview: true, chunkSize: 10000 },
    () => {},
  );
  const { ghostFlashDelay, GHOST_FLASH_MS } = api2;
  assert.equal(ghostFlashDelay(undefined, 1000), 0, 'новая вспышка обязана начаться сразу');
  assert.equal(ghostFlashDelay(900, 1000), -100, 'горящая вспышка не продолжается с места остановки');
  assert.equal(ghostFlashDelay(1000 - GHOST_FLASH_MS, 1000), null, 'отгоревшая вспышка не признана отгоревшей');
  assert.ok(GHOST_FLASH_MS === 1400, 'длительность вспышки разошлась с nm-hit-flash в CSS');
});

// ── диапазоны чанков ───────────────────────────────────────────────────────────

test('чанкование с диапазонами совпадает с обычным чанкованием', () => {
  const text = Array.from({ length: 12 }, (_, i) => `Абзац номер ${i} с текстом про ${'字'.repeat(30)}`).join('\n\n');
  for (const size of [30, 120, 400, 10000]) {
    const plain = api.splitByNewlines(text, size);
    const { chunks, ranges } = api.splitByNewlinesRanged(text, size);
    assert.deepEqual(chunks, plain, `чанкование разошлось при chunkSize=${size}`);
    // диапазоны покрывают все абзацы по порядку и не пересекаются
    const paras = api.paragraphsOf(text);
    assert.ok(ranges.length === chunks.length, 'диапазонов должно быть столько же, сколько чанков');
    assert.equal(ranges[0][0], 0);
    assert.equal(ranges[ranges.length - 1][1], paras.length - 1);
    for (let i = 1; i < ranges.length; i++) assert.equal(ranges[i][0], ranges[i - 1][1] + 1);
    // абзацы диапазона собраны в свой чанк — без пропусков и чужих
    for (let i = 0; i < chunks.length; i++) {
      const [a, b] = ranges[i];
      assert.deepEqual(chunks[i].split('\n\n'), paras.slice(a, b + 1), `чанк ${i} не совпал со своим диапазоном`);
    }
  }
});

test('один абзац больше чанка — единственный чанк, диапазон один', () => {
  const { chunks, ranges } = api.splitByNewlinesRanged('字'.repeat(5000), 100);
  assert.equal(chunks.length, 1);
  assert.deepEqual(ranges, [[0, 0]]);
});

// ── потоковый разбор ответа NER ────────────────────────────────────────────────
// записи вспыхивают по мере закрытия объекта в потоке, а не пачкой в конце:
// разбор идёт поверх накопленного текста и продолжается с места остановки
const NER = new Function(`${slice('function takeNerItems', 'async function requestNerChunk', 'потоковый разбор NER')}
return { takeNerItems, parseObjectAt };`)();

const feedNer = (pieces) => {
  const state = { pos: 0, at: 0, depth: 0, inStr: false, esc: false, iter: 0 };
  let text = '';
  const out = [];
  for (const p of pieces) {
    text += p;
    for (const raw of NER.takeNerItems(text, state)) out.push(raw);
  }
  return out;
};

test('запись отдаётся сразу, как закрылась в потоке', () => {
  const pieces = [
    '[{', '"term":"Ван Линь",', '"translation":"Ван Линь",', '"type":"Person (male)"},',
    '{"term":"Секта Небесного Облака","translation":"Секта Небесных Облаков",',
    '"type":"Organization","aliases":["Небесное Облако"]},',
    '{"term":"Три Потока","translation":"Три Потока"}]',
  ];
  const state = { pos: 0, at: 0, depth: 0, inStr: false, esc: false, iter: 0 };
  let text = '';
  const rounds = [];
  for (const p of pieces) {
    text += p;
    rounds.push(NER.takeNerItems(text, state).map((o) => o.term));
  }
  // первая запись закрылась на четвёртом куске — там же и вспыхнула, а не в конце
  assert.deepEqual(rounds.slice(0, 3), [[], [], []]);
  assert.deepEqual(rounds[3], ['Ван Линь']);
  assert.deepEqual(rounds[5], ['Секта Небесного Облака']);
  assert.deepEqual(rounds[6], ['Три Потока']);
});

test('незакрытый объект и скобки внутри строк разбор не путают', () => {
  const one = feedNer(['[{"term":"Знак }{ в термине","translation":"Знак {}"}']);
  assert.equal(one.length, 1, `закрывающая скобка в строке закрыла объект раньше времени: ${JSON.stringify(one)}`);
  assert.equal(one[0].term, 'Знак }{ в термине');
  // экранированная кавычка не заканчивает строку
  const esc = feedNer(['[{"term":"Ван \\" Линь","translation":"Ван Линь"}]']);
  assert.equal(esc.length, 1);
  assert.equal(esc[0].term, 'Ван " Линь');
  // проза вокруг JSON: до массива разбор просто идёт дальше
  const prose = feedNer(['Вот результат:\n```json\n[{"term":"Ван Линь","translation":"Ван Линь"}]']);
  assert.equal(prose.length, 1);
  assert.equal(prose[0].term, 'Ван Линь');
});

test('битый объект не роняет разбор следующего', () => {
  const items = feedNer(['[{"term":"не объект","translation":"ок"},{"term":"ок","translation":"ок"}]']);
  assert.equal(items.length, 2, 'после закрытия объекта разбор обязан продолжиться');
  assert.equal(items[1].term, 'ок');
});

// ── совпадение модели при проверке сервера ─────────────────────────────────────

const CHECK = new Function(`${slice('const CHECK_TIMEOUT_MIN', 'async function checkServer', 'вердикт по /models')}
return { modelMatch, similarModels };`)();

test('модель находится точно (без регистра) и двусторонним includes', () => {
  const ids = ['google/gemma-4-31b-it', 'qwen/qwen3-235b-a22b-2507'];
  assert.equal(CHECK.modelMatch(ids, 'GOOGLE/Gemma-4-31B-IT'), 'google/gemma-4-31b-it');
  assert.equal(CHECK.modelMatch(ids, 'gemma-4-31b'), 'google/gemma-4-31b-it');
  assert.equal(CHECK.modelMatch(ids, 'qwen/qwen3-235b-a22b-2507-instruct'), 'qwen/qwen3-235b-a22b-2507');
  assert.equal(CHECK.modelMatch(ids, 'нет-такой'), '');
  assert.equal(CHECK.modelMatch([], 'gemma'), '');
});

test('подсказка по похожим id: не длиннее пяти и по общей подстроке', () => {
  const ids = ['google/gemma-4-31b-it', 'google/gemma-3-27b-it', 'openai/gpt-5', 'x/y'];
  const similar = CHECK.similarModels(ids, 'google/gemma-4-31b');
  assert.deepEqual(similar, ['google/gemma-4-31b-it', 'google/gemma-3-27b-it']);
  assert.ok(CHECK.similarModels(Array.from({ length: 20 }, (_, i) => `model-gemma-${i}`), 'gemma').length <= 5);
  assert.deepEqual(CHECK.similarModels(ids, 'юпитер'), []);
});

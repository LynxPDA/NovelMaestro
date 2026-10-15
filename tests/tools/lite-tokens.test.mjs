// NovelMaestro Lite: оценка числа токенов — зеркало core.common.estimate_tokens.
// Веса сняты с токенизатора Gemma 4 31b (106 языков, 2,15 млн символов), поэтому
// те же векторы зафиксированы и в Python (tests/test_core_common.py): зеркало
// обязано давать ровно те же числа, иначе чанки Lite и сервера расходятся.
// Части юзерскрипта — один IIFE, целиком в node они не исполняются (нужны DOM и GM_*),
// поэтому подопытный блок вырезается из артефакта по маркерам и исполняется как
// чистый код на заглушках.
// Запуск: node --test tests/tools/*.test.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const raw = fs.readFileSync(
  path.join(REPO, 'tools/NovelMaestro_Lite/novelmaestro-lite.user.js'), 'utf8');

const from = raw.indexOf('// Язык-осведомлённая ОЦЕНКА числа токенов');
const to = raw.indexOf('// chunkSize — ТОКЕНЫ');
assert.ok(from > 0 && to > from, 'в артефакте не найден блок оценки токенов');
// левый отступ частей (4 пробела) в изолированном скоупе не нужен
const SOURCE = raw.slice(from, to).replace(/^ {4}/gm, '').trimEnd();

const { estimateTokens } = new Function(`${SOURCE}
return { estimateTokens };`)();

// те же пары, что в tests/test_core_common.py::test_estimate_tokens_scripts
const VECTORS = [
  ['', 0],
  ['   \n  ', 1],
  ['Привет, мир! Это тестовый русский текст.', 15],
  ['林凡说。', 4],
  ['こんにちは', 3],
  ['안녕하세요', 4],
  ['สวัสดี', 4],
  ['Xin chào', 3],
  ['مرحبا بالعالم', 7],
  ['नमस्ते दुनिया', 6],
  ['Բարև', 4],
  ['2026', 5],
  ['😀🔥', 1],
];

test('оценка токенов Lite совпадает с core.common.estimate_tokens', () => {
  for (const [text, expected] of VECTORS) {
    assert.equal(estimateTokens(text), expected, `расхождение на ${JSON.stringify(text)}`);
  }
});

test('пробелы почти не стоят токенов, иероглиф — почти токен', () => {
  assert.ok(estimateTokens(' '.repeat(50)) < estimateTokens('ж'.repeat(10)));
  assert.ok(estimateTokens('字'.repeat(100)) > 2.5 * estimateTokens('ж'.repeat(100)));
  assert.ok(estimateTokens('Բ'.repeat(100)) > estimateTokens('ж'.repeat(100)));
});

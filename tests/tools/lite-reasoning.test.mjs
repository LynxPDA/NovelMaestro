// NovelMaestro Lite: reasoning/thinking — что реально уходит в тело запроса.
// Части юзерскрипта — один IIFE, целиком в node не исполняется (нужны DOM и GM_*),
// поэтому блок сборки тела запроса вырезается из артефакта по маркерам и
// исполняется как чистая функция на заглушках. Запуск:
//   node --test tests/tools/*.test.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const ARTIFACT = path.join(REPO, 'tools/NovelMaestro_Lite/novelmaestro-lite.user.js');
const OPEN = 'const REASONING_PROFILES = {';
const CLOSE = '    async function callLLM';

const raw = fs.readFileSync(ARTIFACT, 'utf8');
const start = raw.indexOf(OPEN);
const end = raw.indexOf(CLOSE);
assert.ok(start > 0 && end > start, 'в артефакте Lite не находится блок reasoning/thinking');
// левый отступ частей (4 пробела) в изолированном скоупе не нужен
const block = raw.slice(start, end).replace(/^ {4}/gm, '');
const make = new Function('config', 'apiBase', 'apiHeaders', `${block}
return { llmRequestOptions, reasoningFields, reasoningSummary, thinkingMode, normalizeEffort, streamErrorInfo };`);

const build = (over = {}, extra = null) => {
  const config = {
    model: 'Тест-1', thinkingMode: 'default', thinkingProfile: 'openai',
    reasoningEffort: '', thinkingBudget: 0, extraBodyJson: '', ...over
  };
  const api = make(config, () => 'http://127.0.0.1:11434/v1', (j) => (j ? { 'Content-Type': 'application/json' } : {}));
  return { body: JSON.parse(api.llmRequestOptions([{ role: 'user', content: 'ping' }], 0, true, extra).options.body), summary: api.reasoningSummary() };
};

test('по умолчанию reasoning-ключей нет вовсе', () => {
  assert.deepEqual(build().body, { model: 'Тест-1', messages: [{ role: 'user', content: 'ping' }], temperature: 0, stream: true });
  assert.equal(build().summary, '💭 как у модели');
});

test('старое «None» читается как «не отправлять»', () => {
  assert.equal(build({ reasoningEffort: 'None' }).body.reasoning_effort, undefined);
  assert.equal(build({ reasoningEffort: '  none ' }).body.reasoning_effort, undefined);
});

test('профиль openai: уровень как есть, выключено — none', () => {
  assert.equal(build({ reasoningEffort: 'high' }).body.reasoning_effort, 'high');
  assert.equal(build({ thinkingMode: 'off' }).body.reasoning_effort, 'none');
  // «включено» без уровня — решение сервера: ключ не отправляется
  assert.equal(build({ thinkingMode: 'on' }).body.reasoning_effort, undefined);
});

test('профиль anthropic: thinking.type и budget_tokens', () => {
  assert.deepEqual(build({ thinkingMode: 'on', thinkingProfile: 'anthropic', thinkingBudget: 4096 }).body.thinking,
    { type: 'enabled', budget_tokens: 4096 });
  assert.deepEqual(build({ thinkingMode: 'off', thinkingProfile: 'anthropic' }).body.thinking, { type: 'disabled' });
});

test('профиль qwen/vLLM: chat_template_kwargs thinking+enable_thinking', () => {
  assert.deepEqual(build({ thinkingMode: 'on', thinkingProfile: 'qwen' }).body.chat_template_kwargs,
    { thinking: true, enable_thinking: true });
  assert.deepEqual(build({ thinkingMode: 'off', thinkingProfile: 'qwen' }).body.chat_template_kwargs,
    { thinking: false, enable_thinking: false });
});

test('профиль dashscope: enable_thinking и thinking_budget', () => {
  const body = build({ thinkingMode: 'on', thinkingProfile: 'dashscope', thinkingBudget: 1024 }).body;
  assert.equal(body.enable_thinking, true);
  assert.equal(body.thinking_budget, 1024);
});

test('профиль ollama: think', () => {
  assert.equal(build({ thinkingMode: 'off', thinkingProfile: 'ollama' }).body.think, false);
  assert.equal(build({ thinkingMode: 'on', thinkingProfile: 'ollama' }).body.think, true);
});

test('профиль openrouter: effort и max_tokens — только что-то одно', () => {
  // живьём проверено на routerai: оба поля вместе он режет ответом
  // «Only one of "reasoning.effort" and "reasoning.max_tokens" can be specified»
  assert.deepEqual(build({ thinkingMode: 'on', thinkingProfile: 'openrouter', reasoningEffort: 'medium', thinkingBudget: 2048 }).body.reasoning,
    { enabled: true, effort: 'medium' });
  assert.deepEqual(build({ thinkingMode: 'on', thinkingProfile: 'openrouter', thinkingBudget: 2048 }).body.reasoning,
    { enabled: true, max_tokens: 2048 });
  assert.deepEqual(build({ thinkingMode: 'off', thinkingProfile: 'openrouter' }).body.reasoning, { enabled: false });
});

test('профиль all собирает все профили сразу', () => {
  const body = build({ thinkingMode: 'on', thinkingProfile: 'all', reasoningEffort: 'low', thinkingBudget: 512 }).body;
  assert.equal(body.reasoning_effort, 'low');
  assert.deepEqual(body.thinking, { type: 'enabled', budget_tokens: 512 });
  assert.deepEqual(body.chat_template_kwargs, { thinking: true, enable_thinking: true });
  assert.equal(body.think, true);
  assert.deepEqual(body.reasoning, { enabled: true, effort: 'low' });
});

test('свои поля запроса главнее reasoning-полей, битый JSON игнорируется', () => {
  const body = build({ thinkingMode: 'off', extraBodyJson: '{"thinking":{"type":"enabled"},"user":"u1"}' }).body;
  assert.deepEqual(body.thinking, { type: 'enabled' });
  assert.equal(body.user, 'u1');
  assert.equal(body.reasoning_effort, 'none');
  assert.equal(build({ extraBodyJson: '{oops' }).body.user, undefined);
  assert.equal(build({ extraBodyJson: '[1,2]' }).body.length, undefined);
});

test('ошибка в потоке: вложенный JSON разворачивается, код 4xx сохраняется', () => {
  // роутеры кладут ошибку строкой с JSON внутри — без разворота пользователь видел
  // экранированный JSON, а потерянный код 400 повторял заведомо плохой запрос
  const nested = make(
    { model: 'М', thinkingMode: 'default', thinkingProfile: 'openai', reasoningEffort: '', thinkingBudget: 0, extraBodyJson: '' },
    () => 'http://127.0.0.1:11434/v1', () => ({}));
  const info = nested.streamErrorInfo('{"error":{"message":"Only one of \\"reasoning.effort\\" and \\"reasoning.max_tokens\\" can be specified","code":400},"user_id":"u1"}');
  assert.equal(info.message, 'Only one of "reasoning.effort" and "reasoning.max_tokens" can be specified');
  assert.equal(info.status, 400);
  // обычный плоский вариант и просто строка
  assert.deepEqual(nested.streamErrorInfo({ message: 'invalid api key', code: 401 }), { message: 'invalid api key', status: 401 });
  assert.deepEqual(nested.streamErrorInfo('сервер ушёл на техработу'), { message: 'сервер ушёл на техработу', status: 0 });
});

test('сводка reasoning показывает профиль и поля (её печатает проверка сервера)', () => {
  assert.equal(build({ thinkingMode: 'off', thinkingProfile: 'qwen' }).summary,
    '💭 qwen: chat_template_kwargs={"thinking":false,"enable_thinking":false}');
});

test('отмена: флаг отмены живёт только время прогона', () => {
  // иначе каждый следующий запрос (в т.ч. «Проверить сервер») мгновенно падал с
  // «Отменено пользователем (0мс)», и лечилось это перезагрузкой страницы
  const flow = raw.slice(raw.indexOf('async function runTranslationFlow'), raw.indexOf('async function handleExtractTerms'));
  const fin = flow.slice(flow.lastIndexOf('finally {'));
  assert.match(fin, /cancelRequested = false/, 'в finally прогона перевода нет сброса флага отмены');
  assert.match(fin, /activeReader = null/, 'в finally прогона перевода нет сброса активного читателя');
  const extract = raw.slice(raw.indexOf('async function handleExtractTerms'));
  const efin = extract.slice(extract.lastIndexOf('finally {'));
  assert.match(efin, /cancelRequested = false/, 'в finally извлечения терминов нет сброса флага отмены');
});

test('отмена: панель прогресса имеет финальное состояние с кнопкой «Скрыть»', () => {
  assert.ok(raw.includes('id="reader-progress-close"'), 'нет кнопки «Скрыть» на панели прогресса');
  assert.match(raw, /function progressFinish\(title, status\)/, 'нет финального состояния панели прогресса');
  assert.match(raw, /\$\('#reader-cancel'\)\.style\.display = done \? 'none' : ''/, 'кнопка «Отменить» не прячется в финальном состоянии');
  // отменённый прогон обязан называться остановленным, а не «не завершён»
  assert.ok(raw.includes("progressFinish('⏹ Перевод остановлен'"), 'отмена не переводит панель в финальное состояние');
});

test('thinking-модель: размышления считаются живой работой стрима', () => {
  assert.match(raw, /delta\?\.reasoning_content/, 'размышления модели не читаются');
  assert.match(raw, /const beforeLen = text\.length \+ reasoningChars/, 'размышления не сбрасывают сторож «нет токенов»');
  assert.match(raw, /cb\.stopOnFirstContent && \(text\.trim\(\) \|\| reasoningChars\)/, 'пинг проверки не считает ответом чистые размышления');
});

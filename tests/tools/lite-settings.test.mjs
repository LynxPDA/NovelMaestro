// NovelMaestro Lite: валидация числовых полей настроек — что уходит в конфиг и
// что показывается пользователю. Части юзерскрипта — один IIFE, целиком в node
// они не исполняются (нужны DOM и GM_*), поэтому подопытная функция вырезается
// из артефакта по маркерам и исполняется как чистая функция. Запуск:
//   node --test tests/tools/*.test.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const ARTIFACT = path.join(REPO, 'tools/NovelMaestro_Lite/novelmaestro-lite.user.js');
const OPEN = 'function parseNumSetting';
const CLOSE = 'const asText =';

const raw = fs.readFileSync(ARTIFACT, 'utf8');
const start = raw.indexOf(OPEN);
const end = raw.indexOf(CLOSE);
assert.ok(start > 0 && end > start, 'в артефакте Lite не найдена функция валидации настроек');
// левый отступ частей (4 пробела) в изолированном скоупе не нужен
const parseNumSetting = new Function(`${raw.slice(start, end).replace(/^ {4}/gm, '')}
return parseNumSetting;`)();

// спецификации полей — из той же части, что и разметка: их читаем из артефакта
const fields = raw.slice(raw.indexOf('const SETTING_FIELDS = ['), raw.indexOf('];', raw.indexOf('const SETTING_FIELDS = [')));
const SPEC_RE = /\['#([\w-]+)', '([\w-]+)', raw => parseNumSetting\(raw, \{ name: '([^']+)', def: DEFAULT_CONFIG\.([\w-]+), min: (-?[\d.]+)(?:, max: (-?[\d.]+))?(, int: false)?/g;
const specs = [...fields.matchAll(SPEC_RE)].map((m) => ({ id: m[1], key: m[2], name: m[3], defKey: m[4], min: Number(m[5]), max: m[6] === undefined ? undefined : Number(m[6]), int: m[7] === undefined }));
assert.equal(specs.filter((s) => !s.int).map((s) => s.id).join(), 'fuzzy-threshold,reader-line-height,reader-paragraph-spacing', 'дробные поля изменились');
const defaults = raw.slice(raw.indexOf('const DEFAULT_CONFIG = {'), raw.indexOf('};', raw.indexOf('const DEFAULT_CONFIG = {')));
const defaultValue = (key) => Number(new RegExp(`\n\\s+${key}: ([\\d.]+)`).exec(defaults)[1]);

test('числовые поля настроек описаны парсерами (никто не забыт)', () => {
  const ids = specs.map((s) => s.id);
  assert.deepEqual(ids, ['request-timeout', 'max-retries', 'chunk-size', 'fuzzy-threshold', 'reader-font-size', 'reader-line-height', 'reader-paragraph-spacing', 'reader-content-width', 'thinking-budget'], `список изменился: ${ids.join()}`);
  for (const s of specs) {
    // дефолт поля берётся из своего ключа конфига, а не из посторонней цифры
    assert.equal(s.defKey, s.key, `#${s.id}: дефолт спеки ссылается на ${s.defKey}`);
    const def = defaultValue(s.key);
    assert.ok(s.min <= def, `#${s.id}: дефолт ${def} ниже минимума ${s.min}`);
    if (s.max !== undefined) assert.ok(def <= s.max, `#${s.id}: дефолт ${def} выше максимума ${s.max}`);
  }
});

test('границы в разметке полей совпадают со спецификациями парсеров', () => {
  // min/max в разметке браузер показывает человеку, парсер по ним же и правит
  for (const s of specs) {
    const tag = new RegExp(`<input[^>]*id="${s.id}"[^>]*>`).exec(raw);
    assert.ok(tag, `в разметке нет поля #${s.id}`);
    const attr = (name) => { const m = new RegExp(`${name}="(-?[\\d.]+)"`).exec(tag[0]); return m ? Number(m[1]) : undefined; };
    assert.equal(attr('min'), s.min, `#${s.id}: min в разметке (${attr('min')}) ≠ min парсера (${s.min})`);
    if (s.max !== undefined) assert.equal(attr('max'), s.max, `#${s.id}: max в разметке (${attr('max')}) ≠ max парсера (${s.max})`);
  }
});

test('пусто и буквы — значение по умолчанию, проблема с именем поля', () => {
  const chunk = specs.find((s) => s.id === 'chunk-size');
  for (const raw2 of ['', '   ', 'abc', '—']) {
    const res = parseNumSetting(raw2, { ...chunk, def: defaultValue(chunk.key) });
    assert.equal(res.value, defaultValue(chunk.key), `${JSON.stringify(raw2)} должно давать дефолт`);
    assert.match(res.problem, new RegExp(`^${chunk.name}`), 'в тексте проблемы нет имени поля');
    assert.match(res.problem, /цифры/, 'в тексте проблемы нет причины');
    assert.ok(res.problem.includes(String(defaultValue(chunk.key))), 'пользователю обязаны показать, что применилось');
  }
});

test('корректный ввод принимается без единой жалобы', () => {
  const cases = [['10000', 'chunk-size'], ['100', 'chunk-size'], ['30000', 'chunk-size'], ['0', 'request-timeout'], ['0.05', 'fuzzy-threshold'], ['1', 'reader-line-height'], ['3', 'reader-line-height'], ['0.2', 'reader-paragraph-spacing'], ['30', 'reader-content-width'], ['  4000 ', 'chunk-size']];
  for (const [value, id] of cases) {
    const spec = specs.find((s) => s.id === id);
    const res = parseNumSetting(value, spec);
    assert.equal(res.problem, undefined, `${id}: #${id}=${value} не должно ругаться — ${res.problem}`);
    assert.equal(res.value, Number(value), `${id}: значение потерялось`);
  }
});

test('целочисленные поля округляются вниз, дробные сохраняют дробь', () => {
  assert.equal(parseNumSetting('1000.9', specs.find((s) => s.id === 'chunk-size')).value, 1000);
  assert.equal(parseNumSetting('0.75', specs.find((s) => s.id === 'fuzzy-threshold')).value, 0.75);
  assert.equal(parseNumSetting('1.65', specs.find((s) => s.id === 'reader-line-height')).value, 1.65);
});

test('выход за диапазон: применяется граница, поле подсвечивается причиной', () => {
  const chunk = specs.find((s) => s.id === 'chunk-size');
  assert.deepEqual((() => { const r = parseNumSetting('5', chunk); return [r.value, /вне диапазона/.test(r.problem) && r.problem.includes(chunk.name)]; })(), [100, true]);
  assert.deepEqual((() => { const r = parseNumSetting('999999', chunk); return [r.value, /вне диапазона/.test(r.problem)]; })(), [30000, true]);
  const fuzzy = specs.find((s) => s.id === 'fuzzy-threshold');
  assert.equal(parseNumSetting('7', fuzzy).value, 1);
  assert.equal(parseNumSetting('-1', fuzzy).value, 0);
  // у таймаута верхней границы нет: любое положительное — норма
  assert.equal(parseNumSetting('3600', specs.find((s) => s.id === 'request-timeout')).value, 3600);
  // -5 → нижняя граница 0 (таймаут и ретраи не бывают отрицательными)
  assert.equal(parseNumSetting('-5', specs.find((s) => s.id === 'request-timeout')).value, 0);
  assert.equal(parseNumSetting('-5', specs.find((s) => s.id === 'max-retries')).value, 0);
});

test('единицы и смысл параметров объясняются в подсказках (?)', () => {
  // длинные описания переехали в иконки — их обязан содержать артефакт
  for (const tip of [
    // бюджет размышлений — токены, 0 = не отправлять
    /ТОКЕНЫ: thinking\.budget_tokens[\s\S]*0 = не отправлять/,
    // профиль описывает, что реально уходит, а «все сразу» — отдельный режим
    /Профиль отправляет только свои ключи/,
    // транспорт по умолчанию живёт на стороне страницы
    /запросы идут fetch'ом из страницы/,
    // ширина колонки — проценты, а не пиксели
    /ПРОЦЕНТЫ ширины экрана/,
    // таймаут — секунды и пауза между токенами
    /СЕК\. 0 = без таймаута/,
  ]) {
    assert.match(raw, tip, `подсказка потерялась: ${tip}`);
  }
  // а подписи полей остались короткими
  for (const label of ['>Уровень рассуждений:<', '>Профиль API:<', '>Бюджет размышлений:<']) {
    assert.ok(raw.includes(label), `подпись поля изменилась: ${label}`);
  }
  assert.ok(!/\(пусто = не отправлять\)|Профиль API \(как передавать\)|Бюджет размышлений \(токены/.test(raw), 'длинные подписи вернулись в label');
});

test('подсказки показывает один плавающий тултип, а не inline-простыни', () => {
  assert.match(raw, /tip\.id = 'nm-tip'/, 'тултип больше не общий');
  assert.match(raw, /#nm-tip \{ display: none; position: fixed/, 'тултип обязан быть fixed: в модалке его резал бы overflow');
  assert.match(raw, /#nm-tip\.active \{ display: block; \}/, 'без .active тултип остаётся невидимым');
  assert.match(raw, /const touchUI = matchMedia\('\(hover: none\)'\)/, 'тач-режим подсказок потерялся');
  assert.ok(!raw.includes('<small>'), 'inline-подсказки <small> вернулись в разметку');
  assert.match(raw, /function setSettingsSubTab\(name\)/, 'переключатель вторичных вкладок потерян');
  assert.match(raw, /position: sticky; bottom: 0/, 'подвал настроек больше не приклеен');
});

test('вторичные вкладки и их панели идут в одном порядке', () => {
  const tabs = [...raw.slice(raw.indexOf('<div class="nm-subtabs">'), raw.indexOf('</div>', raw.indexOf('<div class="nm-subtabs">'))).matchAll(/data-stab="(\w+)"/g)].map((m) => m[1]);
  const panels = [...raw.matchAll(/class="nm-subtab-content ?\w*" id="stab-(\w+)"/g)].map((m) => m[1]);
  assert.deepEqual(tabs, ['main', 'reader', 'translate', 'advanced'], `порядок вкладок: ${tabs.join()}`);
  assert.deepEqual(panels, tabs, `порядок панелей разошёлся с вкладками: ${panels.join()}`);
  // что живёт где — по плану пользователя
  const where = (id) => panels.indexOf(id);
  for (const field of ['api-host', 'api-key', 'model', 'local-model', 'source-lang', 'target-lang', 'request-timeout', 'max-retries', 'gm-transport']) {
    assert.equal(where('main'), 0, '«Основные» обязаны быть первой вкладкой');
    assert.ok(raw.indexOf(`id="${field}"`) > raw.indexOf('id="stab-main"') && raw.indexOf(`id="${field}"`) < raw.indexOf('id="stab-reader"'), `${field} уехал с вкладки «Основные»`);
  }
  for (const field of ['reader-theme', 'reader-font-family', 'reader-content-width']) {
    assert.ok(raw.indexOf(`id="${field}"`) < raw.indexOf('id="stab-translate"'), `${field} уехал с вкладки «Читалка»`);
  }
  for (const field of ['chunk-size', 'preemptive-translate', 'fuzzy-threshold', 'auto-ner']) {
    assert.ok(raw.indexOf(`id="${field}"`) > raw.indexOf('id="stab-translate"') && raw.indexOf(`id="${field}"`) < raw.indexOf('id="stab-advanced"'), `${field} уехал с вкладки «Перевод»`);
  }
  for (const field of ['thinking-mode', 'reasoning-profile', 'reasoning-effort', 'thinking-budget', 'extra-body-json', 'translation-prompt', 'extraction-prompt']) {
    assert.ok(raw.indexOf(`id="${field}"`) > raw.indexOf('id="stab-advanced"'), `${field} уехал с вкладки «Продвинутое»`);
  }
});

test('чекбоксы настроек — карточками: зона клика — вся строка', () => {
  const cards = [...raw.matchAll(/<div class="nm-check-card">\s*<label for="([\w-]+)"><input type="checkbox" id="\1"/g)].map((m) => m[1]);
  assert.deepEqual(cards, ['local-model', 'gm-transport', 'preemptive-translate', 'auto-ner'], `набор карточек изменился: ${cards.join()}`);
  assert.match(raw, /\.nm-check-card > label \{ display: flex/, 'label карточки не растянут на всю строку');
});

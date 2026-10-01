// NovelMaestro Lite: настройки — валидация числовых полей (что уходит в конфиг и что
// показывается человеку) и структура самой вкладки (вторичные вкладки, подсказки, сброс).
// Части юзерскрипта — один IIFE, целиком в node они не исполняются (нужны DOM и GM_*),
// поэтому подопытная функция вырезается из артефакта по маркерам и исполняется как
// чистая функция. Запуск: node --test tests/tools/*.test.mjs
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
const byId = (id) => specs.find((s) => s.id === id);
const defaults = raw.slice(raw.indexOf('const DEFAULT_CONFIG = {'), raw.indexOf('};', raw.indexOf('const DEFAULT_CONFIG = {')));
const defaultValue = (key) => Number(new RegExp(`\n\\s+${key}: ([\\d.]+)`).exec(defaults)[1]);

test('числовые поля настроек описаны парсерами (никто не забыт)', () => {
  assert.deepEqual(
    specs.map((s) => s.id).sort(),
    ['chunk-size', 'fuzzy-threshold', 'max-retries', 'reader-content-width', 'reader-font-size', 'reader-line-height', 'reader-paragraph-spacing', 'request-timeout', 'thinking-budget'].sort(),
    'набор числовых полей изменился'
  );
  assert.deepEqual(specs.filter((s) => !s.int).map((s) => s.id).sort(), ['fuzzy-threshold', 'reader-line-height', 'reader-paragraph-spacing'], 'дробные поля изменились');
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
  const chunk = byId('chunk-size');
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
    const res = parseNumSetting(value, byId(id));
    assert.equal(res.problem, undefined, `${id}: #${id}=${value} не должно ругаться — ${res.problem}`);
    assert.equal(res.value, Number(value), `${id}: значение потерялось`);
  }
});

test('целочисленные поля округляются вниз, дробные сохраняют дробь', () => {
  assert.equal(parseNumSetting('1000.9', byId('chunk-size')).value, 1000);
  assert.equal(parseNumSetting('0.75', byId('fuzzy-threshold')).value, 0.75);
  assert.equal(parseNumSetting('1.65', byId('reader-line-height')).value, 1.65);
});

test('выход за диапазон: применяется граница, поле подсвечивается причиной', () => {
  const chunk = byId('chunk-size');
  assert.deepEqual((() => { const r = parseNumSetting('5', chunk); return [r.value, /вне диапазона/.test(r.problem) && r.problem.includes(chunk.name)]; })(), [100, true]);
  assert.deepEqual((() => { const r = parseNumSetting('999999', chunk); return [r.value, /вне диапазона/.test(r.problem)]; })(), [30000, true]);
  assert.equal(parseNumSetting('7', byId('fuzzy-threshold')).value, 1);
  assert.equal(parseNumSetting('-1', byId('fuzzy-threshold')).value, 0);
  // у таймаута верхней границы нет: любое положительное — норма
  assert.equal(parseNumSetting('3600', byId('request-timeout')).value, 3600);
  // -5 → нижняя граница 0 (таймаут и ретраи не бывают отрицательными)
  assert.equal(parseNumSetting('-5', byId('request-timeout')).value, 0);
  assert.equal(parseNumSetting('-5', byId('max-retries')).value, 0);
});

test('вторичные вкладки и их панели идут в одном порядке', () => {
  const tabs = [...raw.slice(raw.indexOf('<div class="nm-subtabs">'), raw.indexOf('</div>', raw.indexOf('<div class="nm-subtabs">'))).matchAll(/data-stab="(\w+)"/g)].map((m) => m[1]);
  const panels = [...raw.matchAll(/class="nm-subtab-content ?\w*" id="stab-(\w+)"/g)].map((m) => m[1]);
  assert.deepEqual(tabs, ['main', 'reader', 'translate', 'advanced'], `порядок вкладок: ${tabs.join()}`);
  assert.deepEqual(panels, tabs, `порядок панелей разошёлся с вкладками: ${panels.join()}`);
});

test('блоки настроек живут на своих вкладках (порядок — план пользователя)', () => {
  const panelOf = (id) => {
    const at = raw.indexOf(`id="${id}"`);
    assert.ok(at > 0, `в разметке нет #${id}`);
    for (const p of ['main', 'reader', 'translate', 'advanced']) {
      const from = raw.indexOf(`id="stab-${p}"`);
      const to = p === 'advanced' ? raw.indexOf('<div class="nm-settings-footer">') : raw.indexOf(`id="stab-${['main', 'reader', 'translate', 'advanced'][['main', 'reader', 'translate', 'advanced'].indexOf(p) + 1]}"`);
      if (at > from && at < to) return p;
    }
    return '';
  };
  // 🌐 Основные: сначала Языки, потом API; Сети здесь больше нет
  assert.ok(raw.indexOf('<h3>🗣 Языки</h3>') < raw.indexOf('<h3>🤖 API</h3>'), 'Языки и API поменялись местами обратно');
  for (const id of ['source-lang', 'target-lang', 'api-host', 'api-key', 'model', 'local-model', 'btn-check-server']) {
    assert.equal(panelOf(id), 'main', `${id} уехал с вкладки «Основные»`);
  }
  // 📖 Читалка
  for (const id of ['reader-theme', 'reader-font-family', 'reader-font-size', 'reader-line-height', 'reader-paragraph-spacing', 'reader-content-width']) {
    assert.equal(panelOf(id), 'reader', `${id} уехал с вкладки «Читалка»`);
  }
  // 🔄 Перевод: Текст → Глоссарий → Промпты
  assert.equal(panelOf('chunk-size'), 'translate', 'чанк уехал с вкладки «Перевод»');
  assert.equal(panelOf('preemptive-translate'), 'translate', 'опережающий перевод уехал с вкладки «Перевод»');
  assert.equal(panelOf('fuzzy-threshold'), 'translate', 'глоссарий уехал с вкладки «Перевод»');
  assert.equal(panelOf('translation-prompt'), 'translate', 'промпты уехали с вкладки «Перевод»');
  assert.ok(raw.indexOf('<h3>🔄 Текст</h3>') < raw.indexOf('<h3>✨ Глоссарий</h3>'), 'порядок блоков «Текст/Глоссарий» изменился');
  assert.ok(raw.indexOf('<h3>✨ Глоссарий</h3>') < raw.indexOf('<h3>💬 Промпты</h3>'), 'порядок блоков «Глоссарий/Промпты» изменился');
  // сам блок на вкладке «Перевод» называется «Текст» — иначе тавтология
  assert.ok(!raw.includes('<h3>🔄 Перевод</h3>'), 'блок «Перевод» внутри вкладки «Перевод» вернулся');
  // ⚙️ Продвинутое: Сеть переехала сюда, reasoning рядом, промптов больше нет
  for (const id of ['request-timeout', 'max-retries', 'gm-transport', 'thinking-mode', 'reasoning-profile', 'reasoning-effort', 'thinking-budget', 'extra-body-json']) {
    assert.equal(panelOf(id), 'advanced', `${id} уехал с вкладки «Продвинутое»`);
  }
});

test('чекбоксы настроек — обычной строкой: кликается вся строка, фона нет', () => {
  const rows = [...raw.matchAll(/<label class="nm-input-group nm-check-row"[^>]*><input type="checkbox" id="([\w-]+)"/g)].map((m) => m[1]);
  assert.deepEqual(rows, ['local-model', 'preemptive-translate', 'auto-ner', 'gm-transport'], `набор чекбоксов изменился: ${rows.join()}`);
  assert.match(raw, /\.nm-check-row \{ display: flex/, 'label чекбокса не растянут на всю строку');
  assert.ok(!raw.includes('nm-check-card'), 'карточки-блоки вокруг чекбоксов вернулись');
});

test('подсказка принадлежит самому пункту, а не иконке со знаком вопроса', () => {
  assert.ok(!raw.includes('nm-hint'), 'иконки (?) вернулись в шелл');
  // вешалка ищет строку с data-tip по пути события (событие могло прийти на label/input)
  assert.match(raw, /path\.find\(n => n && n\.dataset && n\.dataset\.tip\)/, 'подсказки больше не ищутся по строке настроек');
  // единицы и смысл параметров обязаны остаться в объяснениях
  for (const tip of [
    /ТОКЕНЫ: thinking\.budget_tokens[\s\S]*0 = не отправлять/,
    /Профиль отправляет только свои ключи/,
    /запросы идут fetch'ом из страницы/,
    /ПРОЦЕНТЫ ширины экрана/,
    /СЕК\. 0 = без таймаута/,
    /Безразмерное отношение 0\.0–1\.0/,
    /Пустой API Key разрешён/,
  ]) {
    assert.match(raw, tip, `объяснение потерялось: ${tip}`);
  }
  // а подписи полей остались короткими
  for (const label of ['>Уровень рассуждений:<', '>Профиль API:<', '>Бюджет размышлений:<', '>Размер чанка:<', '>Ширина колонки:<', '>Таймаут:<']) {
    assert.ok(raw.includes(label), `подпись поля изменилась: ${label}`);
  }
  assert.ok(!/\(пусто = не отправлять\)|Профиль API \(как передавать\)|Бюджет размышлений \(токены|\(токенов, оценка\)/.test(raw), 'длинные подписи вернулись в label');
});

test('подвал настроек: индикатор — вспышка на 3 с, сброс — только своей вкладки', () => {
  // общий плавающий тултип вне скроллящегося тела модалки
  assert.match(raw, /tip\.id = 'nm-tip'/, 'тултип больше не общий');
  assert.match(raw, /#nm-tip \{ display: none; position: fixed/, 'тултип обязан быть fixed: в модалке его резал бы overflow');
  assert.match(raw, /#nm-tip\.active \{ display: block; \}/, 'без .active тултип остаётся невидимым');
  assert.match(raw, /const touchUI = matchMedia\('\(hover: none\)'\)/, 'тач-режим подсказок потерялся');
  assert.ok(!raw.includes('<small>'), 'inline-подсказки <small> вернулись в разметку');
  // шапка и подвал неподвижны, скроллится тело
  assert.match(raw, /function setSettingsSubTab\(name\)/, 'переключатель вторичных вкладок потерян');
  assert.match(raw, /position: sticky; bottom: 0/, 'подвал настроек больше не приклеен');
  // «сохранено» живёт 3 с и гаснет; ошибка — пока поле не починят
  assert.match(raw, /settingsStatusTimer = setTimeout\(\(\) => hideStatus\('status-settings'\), 3000\)/, 'индикатор сохранения снова висит постоянно');
  // сброс — по открытой вкладке, а не по всему конфигу
  assert.match(raw, /Сбросить настройки вкладки/, 'кнопка не говорит, что сбрасывает');
  assert.match(raw, /settingsSubTabOf\(el\) !== tab/, 'сброс снова задевает чужие вкладки');
  assert.ok(!/config = \{ \.\.\.DEFAULT_CONFIG \}/.test(raw), 'тотальный сброс конфига вернулся');
});

/* Версия панели — из meta.js (сборщик подставляет {{VERSION}}): вторая
   копия номера в разметке разъезжается с релизом. */
const APP_VERSION = '{{VERSION}}';

const CFG = {
  delayMs: 2100,
  jitterMs: 0,
  intraMs: 400,
  /* Пауза между удалениями фрагментов: у главы их бывает несколько десятков,
     и залп запросов подряд сервер вправе принять за атаку. Отдельная от
     `intraMs` (там одна пауза внутри главы) — удаление дороже. */
  delMs: 700,
  /* Проверка после записи по умолчанию выключена: это второй проход по
     каждой главе, а сверка chrF перед записью уже отсекает чужую главу. */
  verify: false,
  skipSame: true,
  /* Глава, разбитая на сайте на несколько фрагментов: 'merge' — удалить
     лишние фрагменты и залить текст одним блоком в первый, 'skip' — не
     трогать такие главы вовсе. */
  multiFragment: 'merge',
  diffCtx: 40,
  titleMode: 'off',
  // проверка «та ли это глава»: chrF с уже сохранённым переводом.
  // Калибровка на реальных главах: та же глава — chrF 0.958–0.982,
  // разные главы одной книги — 0.480–0.524. Ноль выключает проверку:
  // переключатель один, и это порог, а не отдельная галка рядом с ним.
  minChrf: 0.75,
  // пауза между главами в dry-run: страницы читаются, но записи нет
  dryMs: 150,
};

/* ---------- утилиты ---------- */
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const $meta = (n) => (document.querySelector(`meta[name="${n}"]`) || {}).content || '';
const bookIdFromUrl = () => (location.pathname.match(/\/book\/(\d+)/) || [])[1] || null;

async function globalPause(baseMs, jitterMs) {
  let d = baseMs;
  if (jitterMs > 0) {
    d += Math.round((Math.random() * 2 - 1) * jitterMs);
    if (d < 100) d = 100;
  }
  await sleep(d);
}

const chapterNum = (t) => {
  const m = (t || '').match(/Глава\s+(\d+)/i);
  return m ? parseInt(m[1], 10) : null;
};

function classifySiteTitle(text) {
  const t = (text || '').trim();
  if (/Глава\s+\d+\.\d+/i.test(t)) return 'fractional';
  if (/Глава\s+\d+\s*[-\u2013\u2014]\s*\d+/i.test(t)) return 'range';
  if (/^Глава\s+\d+/i.test(t)) return 'standard';
  return 'nonstandard';
}

function detectProblematicNum(title) {
  if (/Глава\s+\d+\.\d+/i.test(title)) return 'Дробный номер';
  if (/Глава\s+\d+\s*[-\u2013\u2014]\s*\d+/i.test(title)) return 'Диапазон номеров';
  return null;
}

/* HTML → текст: DOMParser строит документ без innerHTML (в innerHTML сущности
   разворачиваются, но анализатор справедливо считает это путём для XSS;
   здесь же нужен только текст, а не рендер). */
const decodeHtml = (s) => {
  const doc = new DOMParser().parseFromString('<body>' + s + '</body>', 'text/html');
  return (doc.body && doc.body.textContent) || '';
};

function norm(s) {
  return (s || '')
    .replace(/[\u200B-\u200D\uFEFF]/g, '')
    .replace(/\u00A0/g, ' ')
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{2,}/g, '\n')
    .trim();
}

function htmlToRaw(html) {
  return decodeHtml(
    html
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<\/p>/gi, '\n')
      .replace(/<[^>]+>/g, '')
  );
}

function firstDiff(a, b) {
  const n = Math.min(a.length, b.length);
  let i = 0;
  while (i < n && a[i] === b[i]) i++;
  const c = CFG.diffCtx;
  const sl = (s, p) => JSON.stringify(s.slice(Math.max(0, p - c), p + c));
  return { pos: i, site: sl(a, i), file: sl(b, i) };
}

/* chrF: доля совпавших символьных n-грамм (1..6) с β=3. Сравнивать перевод с
   переводом, а не с оригиналом: здесь оба текста — русская проза, и
   «похожесть» значит «та же формулировка», а не «тот же смысл».
   Калибровка на 601 главе книги (translated ↔ polished): медиана 0.983,
   1-й процентиль 0.945, минимум без битых файлов 0.881. ДВЕ РАЗНЫЕ главы той
   же книги: медиана 0.477, максимум 0.543. Грубый повторный перевод (40%
   предложений переписаны) — 0.834-0.873. Порог 0.75 ниже самого тяжёлого
   повторного перевода и выше разных глав: запас есть с обеих сторон.
   Объём сознательно не мерится: у разных глав одного автора длина сходится
   на 0.72-0.92, и как признак «та ли глава» отношение длин не работает;
   обрезанный файл ловится самим chrF (10% текста — около 0.11). */
/* Граммы строки при заданном n. Пробелы выкидываются: для текста на письме
   без пробелов и для названий счёт остаётся одним и тем же. */
function ngramsOf(text, n) {
  const s = String(text || '').replace(/\s+/g, '');
  const m = new Map();
  for (let i = 0; i + n <= s.length; i++) {
    const g = s.slice(i, i + n);
    m.set(g, (m.get(g) || 0) + 1);
  }
  return m;
}

/* Граммы строки по порядку n = 1..nMax через кэш. План сравнивает одну строку
   с каждой главой книги, и без кэша граммы книги пересчитывались на каждую
   пару — это и была основная цена отрисовки (600 строк × 600 глав). */
function gramsCached(text, nMax, cache) {
  const key = String(text || '');
  const hit = cache.get(key);
  if (hit) return hit;
  const out = [];
  for (let k = 1; k <= nMax; k++) out.push(ngramsOf(key, k));
  cache.set(key, out);
  return out;
}

/* chrF двух текстов. Тексты можно передать готовыми граммами (gramsCached) —
   тогда они не считаются заново: план сравнивает одну строку с каждой главой
   книги, и пара «строка × глава» повторяется сотни тысяч раз. */
function chrf(a, b, nMax, beta, readyB) {
  const n = nMax || 6, b2 = (beta || 3) ** 2;
  const ready = Array.isArray(a) ? a : null;
  const readyRight = Array.isArray(readyB) ? readyB : null;
  const ga0 = ready ? '' : String(a || '').replace(/\s+/g, '');
  const gb0 = readyRight ? '' : String(b || '').replace(/\s+/g, '');
  if (!readyRight && !gb0.length) return 0;
  if (!ready && !ga0.length) return 0;
  let total = 0;
  for (let k = 1; k <= n; k++) {
    const ga = ready ? (ready[k - 1] || new Map()) : ngramsOf(ga0, k);
    const gb = readyRight ? (readyRight[k - 1] || new Map()) : ngramsOf(gb0, k);
    let inter = 0;
    for (const [g, c] of ga) if (gb.has(g)) inter += Math.min(c, gb.get(g));
    let totalA = 0, totalB = 0;
    for (const c of ga.values()) totalA += c;
    for (const c of gb.values()) totalB += c;
    const p = inter / Math.max(1, totalA), r = inter / Math.max(1, totalB);
    total += (p + r === 0) ? 0 : (1 + b2) * p * r / (b2 * p + r);
  }
  return total / n;
}

/* Похожа ли глава на уже сохранённый перевод (а не на соседнюю главу).
   Возвращает {ok, chrf} — по ним же пишется объяснение в лог. */
function looksLikeSameChapter(a, b) {
  const x = norm(a), y = norm(b);
  if (!x.length || !y.length) return { ok: false, chrf: 0 };
  const score = chrf(x, y);
  return { ok: score >= CFG.minChrf, chrf: score };
}

async function getDoc(url) {
  const res = await fetch(url, { credentials: 'include' });
  if (!res.ok) throw new Error('GET ' + res.status + ' ' + url);
  const html = await res.text();
  return { html, doc: new DOMParser().parseFromString(html, 'text/html') };
}

const userIdFromHtml = (html) => (html.match(/new\s+CUser\(\{\s*id:\s*(\d+)/) || [])[1] || null;
const csrfFromDoc = (doc) => (doc.querySelector('meta[name="csrf-token"]') || {}).content || '';

/* ---------- парсер файла ---------- */
/* Заголовок — «# [Название :|: Номер (:|: paywall (:|: том))]»: тег пишет
   стадия compile, поэтому номер и прочие поля берутся из НЕГО, а «Глава N»
   в названии — только текст. Название из «Глава N» ненадёжно: в книге
   бывают «Глава 1. Дополнительная глава» или второй том с нумерацией
   с единицы, и по названию такая глава уезжала бы в первую с тем же
   номером. Разделителей бывает несколько, поэтому делим по всем, а не
   по последнему (иначе paywall/том попадали в название и уезжали на сайт). */
function parseChapterHeader(inner) {
  const parts = inner.split(':|:').map(s => s.trim());
  const title = parts[0];
  const num = /^\d+$/.test(parts[1] || '') ? parseInt(parts[1], 10) : null;
  return { title, num };
}

function parseChaptersFile(text) {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const headerRe = /^#\s*\[(.+?)\]\s*$/;
  const out = [];
  let cur = null;
  for (const raw of lines) {
    const m = raw.match(headerRe);
    if (m && m[1].includes(':|:')) {
      if (cur) out.push(cur);
      const h = parseChapterHeader(m[1]);
      cur = { title: h.title, tagNum: h.num, lines: [] };
    } else if (cur) cur.lines.push(raw);
  }
  if (cur) out.push(cur);
  return out.map(c => {
    const body = c.lines.join('\n').replace(/\r\n?/g, '\n').trim();
    /* Номер — из названия «Глава N», как книга размечена на сайте; тег
       `:|:` нужен только когда номера в названии нет вовсе (пролог,
       интерлюдия). Иначе глава из второго тома с названием «Глава 1.
       Дополнительная» съезжала бы в чужое место по тегу. */
    const fromTitle = chapterNum(c.title);
    const num = fromTitle != null ? fromTitle : c.tagNum;
    const problem = detectProblematicNum(c.title);
    return { title: c.title, num, text: body, problem, tagged: c.tagNum != null };
  });
}

/* ---------- парсинг диапазона ---------- */
/* Разделитель — любое тире: дефис, «–» (U+2013), «—» (U+2014) и «−» (U+2212):
   человек ставит то, что удобно, а «1—50» вместо «1-50» иначе молча значит
   «вся книга» — ограничение пропадает незаметно. Нераспознанные куски
   возвращаются отдельно — о них надо сказать, а не выбросить. */
const RANGE_DASH = '[\u002d\u2013\u2014\u2212]';

function parseRangeReport(str) {
  const raw = String(str == null ? '' : str).trim();
  if (!raw) return { set: null, invalid: [], used: false };
  const set = new Set();
  const invalid = [];
  const rangeRe = new RegExp(`^(\\d+)\\s*${RANGE_DASH}\\s*(\\d+)$`);
  for (const part of raw.split(/[,;]\s*/)) {
    const t = part.trim();
    if (!t) continue;
    const m = t.match(rangeRe);
    if (m) {
      const a = parseInt(m[1], 10), b = parseInt(m[2], 10);
      for (let i = Math.min(a, b); i <= Math.max(a, b); i++) set.add(i);
    } else if (/^\d+$/.test(t)) {
      set.add(parseInt(t, 10));
    } else {
      invalid.push(t);
    }
  }
  return { set: set.size > 0 ? set : null, invalid, used: true };
}

const parseRange = (str) => parseRangeReport(str).set;

/* В диапазоне ли глава: пустое поле — да (вся книга), неразобранный номер
   (пролог без «Глава N» в названии и без числа в теге) — тоже да, судить
   не по чему. Правило ОДНО на таблицу плана и на заливку: иначе план
   обещает одно, а скрипт делает другое. */
const inRange = (num, set) => !set || num == null || set.has(num);

/* Диапазон для лога: у широкого — границы и число, а не простыня номеров. */
function describeRange(set) {
  if (!set) return 'все главы';
  const nums = [...set].sort((a, b) => a - b);
  if (nums.length > 12) return `${nums[0]}..${nums[nums.length - 1]} (${nums.length} шт.)`;
  return nums.join(', ');
}

/* ---------- карта сайта ---------- */
/* Карта — «номер → СПИСОК глав сайта», а не одна глава: на сайте бывает
   несколько глав с одним номером («Глава 1» во втором томе, «Глава 1.
   Дополнительная глава»). Одна глава в карте — это молчаливая потеря
   остальных: текст дополнительной главы уезжает в «Историю».
   Заодно собирается список всех глав в порядке оглавления: он нужен
   как последняя подсказка, когда название на сайте устарело (книгу
   перекомпилировали, названия на сайте — от прошлого перевода). */
async function buildSiteMap(bookId) {
  const bookUrl = '/book/' + bookId;
  /* Открытая страница оглавления — это документ, только если мы правда на
     ней: `?Chapter_page=3` при том же пути, что и книга, иначе документ
     считался бы первой страницей и главы 101+ терялись. */
  const curPage = (() => {
    const v = parseInt(new URLSearchParams(location.search).get('Chapter_page') || '1', 10);
    return Number.isFinite(v) && v > 0 ? v : 1;
  })();
  const onBook = (location.pathname === bookUrl || location.pathname === bookUrl + '/');
  const first = onBook ? document : (await getDoc(bookUrl)).doc;

  /* Оглавление постраничное: сколько глав на страницу, решает сервер и
     настройка «Выводить главы по». Без обхода пейджера главы со второй
     страницы не видны, и файл говорит на них «НЕ НАЙДЕНА».

     Номера страниц берутся не из ссылок пейджера, а рядом: у Rulate пейджер
     показывает только крайние и соседние страницы (1, 2, 3, 4, 6 при шести),
     поэтому обход строго по ссылкам терял середину — целые сотни глав.
     Всего страниц — самое большое число в ссылках, а забираются все подряд. */
  const lastNum = (() => {
    let max = curPage;
    first.querySelectorAll('a[href*="Chapter_page="]').forEach(a => {
      const m = (a.getAttribute('href') || '').match(/Chapter_page=(\d+)/);
      if (m) max = Math.max(max, parseInt(m[1], 10));
    });
    return max;
  })();

  const extra = [];
  for (let p = 1; p <= lastNum; p++) {
    if (p === curPage) continue;
    try { extra.push((await getDoc(bookUrl + '?Chapter_page=' + p)).doc); }
    catch (e) { /* страница не отдалась — работаем с тем, что есть */ }
  }
  const docs = [first, ...extra];

  const byNum = new Map();   // номер → [главы сайта с этим номером]
  const titleMap = new Map();
  const nonStandard = [];
  const ordered = [];        // все главы сайта в порядке оглавления

  const junkRe = /^(читать|продолжить чтение|read|скачать|удалить|редактировать|править|\+|\-|×|✕|\d+)$/i;
  const seen = new Set();

  // каждый id — один раз: одна и та же глава бывает слинкована со страницы несколько раз
  for (const doc of docs) doc.querySelectorAll('a[href*="/book/' + bookId + '/"]').forEach(a => {
    let pathname;
    try { pathname = new URL(a.href, location.origin).pathname; } catch (e) { return; }
    const m = pathname.match(new RegExp('^/book/' + bookId + '/(\\d+)(?:/|$)'));
    if (!m) return;

    const text = (a.textContent || '').replace(/\s+/g, ' ').trim();
    if (!text) return;

    if (junkRe.test(text)) return;
    if (text.length <= 3 && !/^Глава/i.test(text)) return;

    const chapterId = m[1];
    if (seen.has(chapterId)) return;
    seen.add(chapterId);

    const info = { url: '/book/' + bookId + '/' + chapterId, chapterId, siteTitle: text };
    ordered.push(info);

    const normKey = text.toLowerCase().replace(/\s+/g, ' ').trim();
    if (normKey && !titleMap.has(normKey)) titleMap.set(normKey, info);

    const type = classifySiteTitle(text);
    if (type !== 'standard') {
      nonStandard.push({ title: text, type, chapterId });
      return;
    }

    const num = chapterNum(text);
    if (num == null) return;
    if (!byNum.has(num)) byNum.set(num, []);
    byNum.get(num).push(info);
  });

  return { byNum, titleMap, nonStandard, ordered };
}

/* ---------- id фрагмента/перевода ---------- */
function readChapterIds(doc, userId) {
  const pairs = [];
  doc.querySelectorAll('tr[id^="o"]').forEach(tr => {
    const mine = tr.querySelector('td.t div[id^="t"].u' + userId);
    if (mine) pairs.push({ origId: tr.id.slice(1), trId: mine.id.slice(1) });
  });
  return pairs;
}

function readSavedText(doc, trId) {
  const el = doc.getElementById('t' + trId);
  if (!el) return null;
  const selectors = ['.translation', '.text', '.tr-text', '.tr_text', '.translate'];
  for (const sel of selectors) {
    const inner = el.querySelector(sel);
    if (inner) { const raw = htmlToRaw(inner.innerHTML); if (raw.trim()) return raw; }
  }
  const firstDiv = el.querySelector('div');
  if (firstDiv) { const raw = htmlToRaw(firstDiv.innerHTML); if (raw.trim()) return raw; }
  const raw = htmlToRaw(el.innerHTML);
  return raw.trim() ? raw : null;
}

/* ---------- POST сохранения перевода ---------- */
async function saveTranslation(bookId, chapterId, origId, trId, body, csrf) {
  const fd = new FormData();
  fd.append('Translation[body]', body);
  fd.append('Translation[new_img]', new Blob([]), '');
  fd.append('ajax', '1');
  fd.append('YII_CSRF_TOKEN', csrf);
  const res = await fetch(`/book/${bookId}/${chapterId}/${origId}/translate?tr_id=${trId}`,
    { method: 'POST', credentials: 'include', body: fd });
  if (!res.ok) throw new Error('POST translate ' + res.status);
  return res;
}

/* ---------- удаление фрагмента оригинала ----------

   Сайт удаляет фрагмент одним POST без тела (`translate-3.js`: `orig.rm`
   шлёт `/book/<книга>/<глава>/<origId>/remove` и ждёт JSON со `status`).
   Фрагмент тянет за собой все свои переводы и комментарии, поэтому вызов
   идёт только по решению человека и только после сверки главы.

   Порядок удаления — С КОНЦА: он не сдвигает `origId` остающихся, и каждый
   удалённый id остаётся годным до своего запроса. */
async function removeFragment(bookId, chapterId, origId) {
  const res = await fetch(`/book/${bookId}/${chapterId}/${origId}/remove`,
    { method: 'POST', credentials: 'include', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
  if (!res.ok) throw new Error('POST remove ' + res.status);
  let data = null;
  try { data = await res.json(); } catch (e) { /* не JSON — решает статус ниже */ }
  if (data && data.error) throw new Error(data.error);
  if (data && data.status && data.status !== 'ok') throw new Error('remove: ' + data.status);
  return data;
}

/* ---------- массовое удаление фрагментов ----------

   У владельца книги на её странице есть форма массовых действий
   (`action=".../massAction"`, чекбоксы `o[]`, кнопка «Удалить фрагменты»):
   один POST удаляет сколько угодно фрагментов. Это единственный путь,
   которым глава из тридцати фрагментов снимается ОДНИМ запросом; удаление
   по одному (`orig.rm`) требует запроса на каждый и на большой книге
   превращается в тысячи обращений.

   Поля формы не хардкодятся, а читаются со страницы: имя кнопки и скрытые
   поля — часть сайта, а промах здесь необратим. Нет формы (не владелец,
   другой шаблон страницы) — вызывающий код уходит в пофрагментное
   удаление, которое умеет страница главы. */
function massDeleteForm(root) {
  for (const f of root.querySelectorAll('form')) {
    const action = f.getAttribute('action') || '';
    if (!/massAction/.test(action)) continue;
    /* Чекбоксы `o[]` в форме искать не нужно: какие фрагменты снимать, знает
       скрипт, — он и перечисляет их сам. Нужны только скрытые поля (CSRF) и
       кнопка. Требование чекбоксов внутри формы отсекало бы форму, где они
       нарисованы в строках таблицы. */
    if (!massDeleteButton(f)) continue;
    return f;
  }
  return null;
}

/* Кнопка удаления оригинала внутри массовой формы. Берётся по подписи, а не
   по имени: подпись человек видит на экране, имя поля может смениться. */
function massDeleteButton(form) {
  const nodes = form.querySelectorAll('button, input[type="submit"], input[type="button"]');
  for (const b of nodes) {
    const name = b.getAttribute('name') || '';
    if (!name) continue;
    const label = (b.textContent || b.getAttribute('value') || '').trim();
    if (/удалить фрагмент/i.test(label)) return { name, value: b.getAttribute('value') || '1' };
  }
  return null;
}

/* Удаление набора фрагментов одной операцией.

   Возвращает true, если массовая форма есть и запрос прошёл, и false, если
   формы нет — тогда решает вызывающий. Скрытые поля формы повторяются как
   есть: там лежит `YII_CSRF_TOKEN` и прочее, без чего сайт запрос отвергнет. */
async function removeFragmentsBulk(root, origIds) {
  const form = massDeleteForm(root);
  if (!form) return false;
  const btn = massDeleteButton(form);
  if (!btn) return false;

  const fd = new FormData();
  form.querySelectorAll('input[type="hidden"]').forEach(i => {
    const n = i.getAttribute('name');
    if (n) fd.append(n, i.value);
  });
  for (const id of origIds) fd.append('o[]', id);
  fd.append(btn.name, btn.value);
  fd.append('ajax', '1');
  const res = await fetch(form.getAttribute('action') || '/orig/massAction',
    { method: 'POST', credentials: 'include', body: fd });
  if (!res.ok) throw new Error('POST massAction ' + res.status);
  return true;
}

/* ---------- POST смены названия главы ---------- */
async function saveChapterTitle(bookId, chapterId, newTitle, csrf) {
  const editUrl = `/book/${bookId}/${chapterId}/edit`;
  const { doc: editDoc } = await getDoc(editUrl);
  const editCsrf = csrfFromDoc(editDoc) || csrf;
  if (!editCsrf) throw new Error('CSRF не найден на edit-странице');

  const titleEl = editDoc.querySelector('input[name="Chapter[title]"]');
  const form = titleEl && (titleEl.form || titleEl.closest('form'));
  if (!form) throw new Error('Не найдена форма редактирования главы');

  const fd = new FormData();
  form.querySelectorAll('input, select, textarea').forEach(el => {
    const name = el.name;
    if (!name) return;
    const t = (el.type || '').toLowerCase();
    if (t === 'submit' || t === 'button') return;
    if (t === 'file') { fd.append(name, new Blob([]), ''); return; }
    if ((t === 'checkbox' || t === 'radio') && !el.checked) return;
    fd.append(name, el.value != null ? el.value : '');
  });

  fd.set('Chapter[title]', newTitle);
  fd.append('yt0', '');
  fd.append('YII_CSRF_TOKEN', editCsrf);

  const res = await fetch(editUrl, { method: 'POST', credentials: 'include', body: fd });
  if (!res.ok) throw new Error('POST edit ' + res.status);
  return res;
}

/* ---------- сравнение ---------- */
function compareAndLog(savedRaw, fileText, ui) {
  const a = norm(savedRaw == null ? '' : savedRaw);
  const b = norm(fileText);
  if (savedRaw == null || a === '') {
    ui.log(`     статус: НА САЙТЕ ПУСТО → «отличается»`, 'warn');
    return false;
  }
  if (a === b) { ui.log(`     статус: СОВПАДАЕТ (${b.length}с)`, 'ok'); return true; }
  const d = firstDiff(a, b);
  ui.log(`     статус: ОТЛИЧАЕТСЯ (сайт ${a.length}с / файл ${b.length}с) @${d.pos}`, 'warn');
  ui.log(`        сайт: ${d.site}`, 'plain');
  ui.log(`        файл: ${d.file}`, 'plain');
  return false;
}

/* ---------- сопоставление файла с сайтом ---------- */
const titleKey = (s) => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
const addTo = (map, k, v) => { if (!map.has(k)) map.set(k, []); map.get(k).push(v); };
const countBy = (arr, key) => {
  const m = new Map();
  for (const x of arr) { const k = key(x); m.set(k, (m.get(k) || 0) + 1); }
  return m;
};

/* План сопоставления файла с сайтом — целиком до первой записи.

   Ключ сопоставления — НОМЕР из названия главы («Глава N»): так книга и
   размечена на сайте, и так её присылает стадия compile. Тег `:|:` — только
   когда в названии номера нет вовсе (пролог «# [Пролог :|: 5]»).

   Разные главы с одним номером (второй том, «Глава 1. Дополнительная глава»)
   разводятся ПОЛНЫМ названием: «Глава 1. История» и «Глава 1. Дополнительная
   глава» — разные главы. Совпало название целиком (с точностью до регистра,
   пробелов и NFC) — глава та. Не совпало ни у одной — отказ, а не порядок:
   порядок угадывает, а ошибка здесь означает запись перевода в чужую главу.

   Возвращает Map: индекс главы файла → {info, how} | {reason, list}. */
function planMatches(parsed, site) {
  const plan = new Map();
  const usedSite = new Map();   // chapterId → индекс главы файла (кто уже занял)

  parsed.forEach((ch, i) => {
    if (ch.problem) return;

    /* 1. по названию целиком — годится и когда номер не разобрался */
    const byTitle = site.titleMap.get(titleKey(ch.title));
    if (byTitle && !usedSite.has(byTitle.chapterId)) {
      plan.set(i, { info: byTitle, how: 'по названию' });
      usedSite.set(byTitle.chapterId, i);
      return;
    }

    /* Не разобрался номер и не совпало название — строке нужен человек.
       Отдельной галочки у поиска по названию нет: строка с причиной есть
       в плане всегда — иначе глава пропадала бы из него молча. */
    if (ch.num == null) {
      plan.set(i, { reason: 'номер не разобрался, название не совпало' });
      return;
    }

    const group = site.byNum.get(ch.num) || [];
    if (!group.length) return;

    /* 2. одна глава с этим номером на сайте — она */
    if (group.length === 1) {
      if (usedSite.has(group[0].chapterId)) return; // дубль в файле — вторую не льём
      plan.set(i, { info: group[0], how: `по номеру ${ch.num}` });
      usedSite.set(group[0].chapterId, i);
      return;
    }

    /* 3. номер повторяется — название уже проверено выше и не подошло */
    const free = group.filter(g => !usedSite.has(g.chapterId));
    plan.set(i, {
      reason: `номер ${ch.num} на сайте у ${group.length} глав, название не совпало`,
      list: free.length ? free : group,
    });
  });

  /* Кандидаты — всем строкам, включая уже сопоставленные. Прежний код
     заполнял список только у спорных, и у решённой строки в <select>
     оставалась одна глава (та, что назначена), а «посмотреть, кто ещё
     похож» было негде. Список здесь — главы с этим номером, отсортированные
     по похожести названия: выбор остаётся за человеком. */
  parsed.forEach((ch, i) => {
    if (ch.problem || ch.num == null) return;
    const b = plan.get(i);
    if (!b || !b.info) return;
    const group = site.byNum.get(ch.num) || [];
    if (group.length > 1) b.list = group;
  });

  return plan;
}

/* Порог сверки из поля панели: в поле стоит значение по умолчанию, поэтому
   пустое (человек стёр) и «0» — одно и то же: сверка выключена. Мусор тоже
   выключает: подставлять за человека потайной порог он не просил. */
function parseChrfThreshold(raw) {
  const t = String(raw == null ? '' : raw).replace(',', '.').trim();
  if (t === '') return 0;
  const v = Number(t);
  if (!Number.isFinite(v)) return 0;
  if (v < 0) return 0;
  if (v > 1) return 1;
  return v;
}

/* Оценка счёта для колонки chrF: при выключенной проверке судить нечем —
   число показываем, но не красим в «хорошо/плохо». */
function chrfVerdict(score) {
  if (CFG.minChrf <= 0) return 'off';
  return score >= CFG.minChrf ? 'good' : 'bad';
}

let progressMarks = [];     // {done, at} — темп заливки для остатка
let running = false;        // идёт заливка: панель не закрывается сама

/* Сколько времени ещё осталось при текущем темпе.

   Темп берётся по факту, а не по настройке паузы: глава — это ещё чтение
   страницы, ожидание ответа сайта и иногда проверка результата, и всё это
   к паузе не сведёшь. Считаем по последнему окну (до 30 обработанных глав),
   а не по среднему с начала: заливка часто начинается с глав, которые
   пропускаются мгновенно («вне диапазона», «нет на сайте»), и среднее с
   начала занижало бы остаток на порядок.

   Без двух отметок времени скорости нет: одна точка не даёт темпа, поэтому
   возвращаем null — «считать не по чему», а не нуль (нуль читался бы как
   «уже готово»). */
function etaParts(done, total, samples) {
  if (samples == null || samples.length < 2) return null;
  const left = total - done;
  if (left <= 0) return { ms: 0, perChapterMs: 0 };
  const first = samples[0].at, last = samples[samples.length - 1].at;
  const chapters = samples[samples.length - 1].done - samples[0].done;
  const span = last - first;
  /* Окно схлопнулось: главы обработаны без задержек (всё пропущено),
     длительности нет — по чему считать темп, тоже нет. */
  if (chapters <= 0 || span <= 0) return null;
  const per = span / chapters;
  return { ms: per * left, perChapterMs: per };
}

/* «3 д 4 ч», «2 ч 15 м», «45 с» — две крупнейшие единицы: в остатке на дни
   секунды не нужны, а «0 д 0 ч 12 м» читается хуже, чем «12 м». */
function formatEta(ms) {
  if (ms == null || !isFinite(ms)) return '';
  /* Остаток меньше секунды — это «меньше секунды», а не «0 с»: ноль
     читается как «уже всё», хотя последняя глава ещё идёт. */
  if (ms > 0 && ms < 1000) return '<1 с';
  const sec = Math.max(0, Math.round(ms / 1000));
  if (sec < 60) return sec + ' с';
  const min = Math.floor(sec / 60), s2 = sec % 60;
  if (min < 60) return s2 ? `${min} м ${s2} с` : `${min} м`;
  const hour = Math.floor(min / 60), m2 = min % 60;
  if (hour < 24) return m2 ? `${hour} ч ${m2} м` : `${hour} ч`;
  const day = Math.floor(hour / 24), h2 = hour % 24;
  return h2 ? `${day} д ${h2} ч` : `${day} д`;
}

/* Подпись под полосой: сколько сделано, сколько осталось и когда конец.
   Пока темпа нет — только счётчик: пустая строка «осталось: —» на старте
   выглядела бы поломкой, а не отсутствием данных. */
function progressText(done, total, samples) {
  const pct = Math.round(done / Math.max(1, total) * 100);
  const head = `${done} из ${total} (${pct}%)`;
  const eta = etaParts(done, total, samples);
  if (!eta) return done >= total ? `${head} · готово` : head;
  if (eta.ms <= 0) return `${head} · готово`;
  const per = eta.perChapterMs >= 1000
    ? `${(eta.perChapterMs / 1000).toFixed(1)} с/глава`
    : `${Math.round(eta.perChapterMs)} мс/глава`;
  return `${head} · осталось ${formatEta(eta.ms)} · ${per}`;
}

/* Похожесть названий — по строке целиком, без вырезания «Глава N.».

   Вырезать префикс нельзя без знания формата книги: «Глава 1 — Битва»,
   «1. Битва», «Том 2» — у каждой книги своё, и правило, верное для одной,
   молча портит счёт для другой. Сравниваем как есть.

   Цена — общий префикс у кандидатов с одним номером даёт примерно 0.3–0.45
   сам по себе (замер на 83 главах реальной книги: разные главы одного номера
   дают медиану 0.45, максимум 0.85, а правка названия той же главы — 0.75
   и выше). Поэтому порог показа поднят: ниже 0.5 счёт не показывается вовсе,
   иначе «0.42» рядом с чужой главой читалось бы как «почти то же самое».

   n до 3: у названия 2–5 слов, и длинные n-граммы делают счёт шумным
   (правка одного предлога при n=6 давала бы 0.37 против 0.60). */
/* Кэш грамм названий общий на все строки плана: одну и ту же строку файла
   сравнивают с каждой главой книги, а названия глав — между собой у разных
   строк. Ключ — нормализованный текст. */
const titleGramCache = new Map();

function titleScore(a, b) {
  /* Обе стороны — из кэша: названия глав сайта сравниваются со всеми строками
     файла, и пересчёт грамм одной стороны на каждую пару и был той самой
     медленной частью. */
  const A = titleNorm(a), B = titleNorm(b);
  return chrf(gramsCached(A, 3, titleGramCache), B, 3, 3, gramsCached(B, 3, titleGramCache));
}

/* Кандидаты для спорной строки, отсортированные по похожести названия.

   Это ПОДСКАЗКА, а не решение: автоматический выбор по счёту запрещён —
   при совпадении номеров ошибка означает запись перевода в чужую главу,
   а chrF названий не различает «Тот, кто пришёл» и «Тот, кто ушёл» (0.87)
   от правки названия той же главы (0.75) с пригодным для автомата запасом.
   Поэтому все кандидаты остаются в списке, а человек видит, кто ближе.
   `score` — для порядка и для числа рядом с названием; порог показа решает
   `optionFor`, второе правило «что считать похожим» здесь не заводится. */
const TITLE_SIMILAR_MIN = 0.5;
function rankCandidates(title, list) {
  return (list || [])
    .map((g) => ({ g, score: titleScore(title, g.siteTitle != null ? g.siteTitle : g.title) }))
    .sort((a, b) => b.score - a.score)
    .map(({ g, score }) => ({ ...g, score }));
}

/* ---------- план и его правка ---------- */
/* План — данные, а не побочный результат заливки: {rows, order, problems, site}.
   Строка — «глава файла (fi) → глава сайта (site)» либо `site: null` с причиной
   (`reason`) и кандидатами (`cands`). Человек правит `site` в панели, `how`
   показывает, откуда взялось значение. Решает сопоставление по-прежнему
   planMatches; здесь только хранение и ручные поправки. */
const titleNorm = (s) => String(s || '').normalize('NFC').toLowerCase().replace(/\s+/g, ' ').trim();

const siteUrl = (bookId, id) => '/book/' + bookId + '/' + id;

function buildPlan(parsed, site, rangeSet) {
  const bookId = bookIdFromUrl();
  const base = planMatches(parsed, site);
  const rows = [];
  const problems = [];
  const order = [];

  parsed.forEach((ch, fi) => {
    if (ch.problem) { problems.push(fi); return; }
    order.push(fi);
    const b = base.get(fi);
    const row = {
      fi, num: ch.num, title: ch.title,
      site: b && b.info ? b.info : null,
      cands: b && b.list ? b.list : [],
      reason: b ? (b.reason || '') : 'нет главы с этим номером',
      how: b && b.info ? (b.how || 'по номеру') : (b && b.list ? 'спорная' : 'нет'),
      host: '',
    };
    rows.push(row);
  });

  const plan = { rows, order, problems, site, manual: {}, bookId, rangeSet: rangeSet || null };
  resolveConflicts(plan, plan.rangeSet);
  return plan;
}

/* Одна глава сайта — одна строка: второй претендент помечается `host`
   («кто занял»), но выбор человека не отменяется — пусть видит, что делает.
   Последним заслоном остаётся chrF перед записью.

   Занятой главу делает только строка В ДИАПАЗОНЕ: строка вне диапазона
   в этом прогоне не пишется, и держать за собой главу она не может —
   иначе её соседка выглядела бы спорной из-за строки, которой в работе нет. */
function resolveConflicts(plan, rangeSet) {
  const owner = new Map();
  for (const r of plan.rows) {
    if (!r.site || !inRange(r.num, rangeSet)) { r.host = ''; continue; }
    const id = String(r.site.chapterId);
    if (!owner.has(id)) { owner.set(id, r); r.host = ''; }
    else r.host = String(owner.get(id).fi);
  }
}

/* Диапазон меняет состав работы — обновить его можно и на готовом плане:
   поле правят, глядя в таблицу, и перестраивать оглавление ради этого
   незачем. Порядок строк (order) тот же, меняется только «что в работе». */
function planSetRange(plan, rangeStr) {
  plan.rangeSet = parseRange(rangeStr);
  resolveConflicts(plan, plan.rangeSet);
  return plan.rangeSet;
}

/* Работа этой строки в текущем диапазоне: пусто — «в работе», иначе причина
   («вне диапазона»). Пустой диапазон = вся книга; глава без номера
   (пролог, интерлюдия) диапазоном не режется — судить о ней не по чему. */
function planRowRange(row, rangeSet) {
  return inRange(row.num, rangeSet) ? '' : 'вне диапазона';
}

const planRow = (plan, fi) => plan.rows.find(r => r.fi === fi) || null;

/* Ручное назначение: `siteId` — глава сайта или null (пропустить строку).
   Возвращает {ok, error}: «нет такой главы» должен сказать интерфейс, а не
   молчаливая подмена. */
function planAssign(plan, fi, siteId) {
  const row = planRow(plan, fi);
  if (!row) return { ok: false, error: 'строка не найдена' };
  if (siteId == null || siteId === '') {
    row.site = null;
    row.how = 'пропуск (ваше решение)';
    resolveConflicts(plan, plan.rangeSet);
    return { ok: true };
  }
  const id = String(siteId);
  const hit = (plan.site.ordered || []).find(g => String(g.chapterId) === id);
  if (!hit) return { ok: false, error: 'на сайте нет главы id=' + id };
  row.site = { url: hit.url || siteUrl(plan.bookId, id), chapterId: id, siteTitle: hit.siteTitle };
  row.how = 'вручную';
  resolveConflicts(plan, plan.rangeSet);
  return { ok: true };
}

/* Оверрайды: что человек решил, помнится между запусками и переживает
   перекомпиляцию файла. Ключ — номер + название главы файла: индекс строки
   после нового разбора уже другой (главы добавились или ушли). */

/* ---------- план в файл и обратно ---------- */
function planToData(plan, parsed) {
  const occupied = new Set(plan.rows.filter(r => r.site).map(r => String(r.site.chapterId)));
  return {
    app: 'NovelMaestro rulate plan',
    version: 1,
    book: String(plan.bookId),
    exported: new Date().toISOString(),
    rows: plan.rows.map(r => ({
      num: r.num,
      title: r.title,
      site: r.site ? { id: String(r.site.chapterId), title: r.site.siteTitle || '' } : null,
      how: r.how,
      cands: r.cands.map(c => ({ id: String(c.chapterId), title: c.siteTitle })),
    })),
    problems: plan.problems.map(fi => ({ num: parsed[fi].num, title: parsed[fi].title, problem: parsed[fi].problem })),
    unused: (plan.site.ordered || []).filter(g => !occupied.has(String(g.chapterId)))
      .map(g => ({ id: String(g.chapterId), title: g.siteTitle })),
  };
}

/* ---------- заливка по плану ---------- */
/* Заливка ровно по плану: решения не пересматриваются, иначе правка человека
   была бы игрушечной. `opts.skipSame` — пропускать совпадающее,
   `opts.rangeStr` — диапазон номеров.

   Диапазон режет работу, а не молча не находит главу: строка вне диапазона
   не идёт ни на страницу сайта, ни в счёт «не найдено». Проверка стоит
   ДО разбора спорной строки: иначе глава вне диапазона попадала бы
   в «неоднозначно» (а спорной она не бывает — её всё равно не льют),
   а глава без номера не проверялась бы вовсе. */
async function writePlan(plan, parsed, opts, ui) {
  const rangeSet = opts.rangeStr === undefined ? (plan.rangeSet || null) : parseRange(opts.rangeStr);
  const rangeReport = opts.rangeStr === undefined ? null : parseRangeReport(opts.rangeStr);
  const entries = plan.order.map(fi => planRow(plan, fi)).filter(Boolean);
  const total = entries.length;
  const st = { saved: 0, wouldSave: 0, skipSame: 0, skipOther: 0, skipRange: 0, skipProblem: 0, fail: 0, titleChanged: 0, ambiguous: 0, fragRemoved: 0 };
  const notFoundNums = [];
  const notFoundTitles = [];
  const reports = [];
  const report = (row, status, chrf) => reports.push({ fi: row.fi, status, chrf: chrf == null ? null : chrf });

  if (rangeReport && rangeReport.invalid.length) {
    ui.log(`⚠ Диапазон: не разобрано «${rangeReport.invalid.join('», «')}» — работают только номера и интервалы.`, 'warn');
  }
  if (rangeSet) ui.log(`Диапазон в работе: ${describeRange(rangeSet)}`, 'plain');

  st.skipProblem = plan.problems.length;
  if (plan.problems.length) ui.log(`Проблемных в файле: ${plan.problems.length} (пропущены)`, 'warn');

  for (let job = 0; job < entries.length; job++) {
    const row = entries[job];
    if (ui.aborted) { ui.log('Остановлено.', 'warn'); break; }
    ui.setProgress(job, total);
    const ch = parsed[row.fi];
    const tick = `[${job + 1}/${total}]`;

    if (!inRange(ch.num, rangeSet)) { st.skipRange++; ui.log(`${tick} «${ch.title}» — вне диапазона (№${ch.num})`, 'plain'); report(row, 'вне диапазона'); continue; }

    if (!row.site) {
      /* Снятая человеком строка — не «неоднозначно» и не «не найдена»:
         скрипт тут ничего не решал, решать было нечего. */
      if (row.how.startsWith('пропуск (ваше')) {
        st.skipOther++;
        ui.log(`${tick} «${ch.title}» — снята вручную.`, 'plain');
        report(row, 'пропуск (ваше решение)');
        continue;
      }
      if (row.cands.length) {
        st.ambiguous++;
        ui.log(`${tick} «${ch.title}» — НЕОДНОЗНАЧНО: №${ch.num} на сайте у ${row.cands.length} глав:`, 'err');
        for (const g of row.cands) ui.log(`     id=${g.chapterId} «${g.siteTitle}»`, 'err');
        ui.log('     → выберите главу в панели (кнопка «План») или допишите номер в тег `:|:`', 'warn');
        report(row, 'неоднозначно');
      } else {
        st.skipOther++;
        if (ch.num != null) notFoundNums.push(ch.num); else notFoundTitles.push(ch.title);
        ui.log(`${tick} «${ch.title}» — НЕ НАЙДЕНА (${row.how})`, 'warn');
        report(row, 'не найдена');
      }
      continue;
    }

    const info = row.site;
    if (row.host && String(row.host) !== String(row.fi)) {
      ui.log(`${tick} ⚠ глава сайта id=${info.chapterId} уже стоит в строке ${row.host} — сверим chrF`, 'warn');
    }

    try {
      /* ===== РЕЖИМ: ТОЛЬКО НАЗВАНИЯ ===== */
      if (CFG.titleMode === 'title-only') {
        const curTitle = info.siteTitle || '';
        const titleSame = titleNorm(curTitle) === titleNorm(ch.title);
        if (opts.skipSame && titleSame) { st.skipSame++; ui.log(`${tick} название совпадает — пропуск.`, 'plain'); report(row, 'пропуск'); continue; }
        if (opts.dryRun) { st.wouldSave++; ui.log(`${tick} [DRY] название было бы обновлено`, 'plain'); report(row, 'будет залито'); continue; }
        await saveChapterTitle(plan.bookId, info.chapterId, ch.title, $meta('csrf-token'));
        st.titleChanged++;
        ui.log(`${tick} название обновлено`, 'ok');
        report(row, 'залито');
        if (job < total - 1 && !ui.aborted) await globalPause(CFG.delayMs, CFG.jitterMs);
        continue;
      }

      /* ===== РЕЖИМЫ: ТОЛЬКО ТЕКСТ / ТЕКСТ + НАЗВАНИЯ ===== */
      const { html, doc } = await getDoc(info.url);
      await globalPause(CFG.intraMs, 0);

      const userId = userIdFromHtml(html);
      if (!userId) throw new Error('Нет данных пользователя — вы залогинены?');
      const csrf = csrfFromDoc(doc) || $meta('csrf-token');
      if (!csrf) throw new Error('CSRF не найден');

      const siteTitle = (html.match(/Chap\s*=\s*\{[\s\S]*?title:\s*'([^']*)'/) || [])[1] || (info.siteTitle || '');
      const pairs = readChapterIds(doc, userId);
      if (!pairs.length) { st.skipOther++; ui.log(`${tick} «${ch.title}» — нет вашего перевода.`, 'warn'); report(row, 'нет перевода'); continue; }
      /* Глава на сайте бывает разбита на несколько фрагментов, и это
         устаревшее хранение: старый перевод делали абзацами, новый текст
         делится совсем иначе, поэтому ни сопоставить абзацы, ни сверить
         текст невозможно. Такая глава сводится к одному блоку: лишние
         фрагменты удаляются, текст целиком идёт в первый. Решение принимает
         человек до запуска — панель показывает такие главы и спрашивает
         согласие: удаление фрагмента необратимо. */
      const merge = pairs.length > 1 && CFG.multiFragment === 'merge';
      if (pairs.length > 1 && CFG.multiFragment === 'skip') {
        st.skipOther++;
        ui.log(`${tick} «${ch.title}» — фрагментов ${pairs.length} (>1), пропуск по настройке.`, 'warn');
        report(row, 'несколько фрагментов');
        continue;
      }
      if (pairs.length > 1 && merge) {
        if (opts.dryRun) {
          ui.log(`${tick} «${ch.title}» — фрагментов ${pairs.length}: [DRY] удалились бы ${pairs.length - 1}, текст лёг бы в первый`, 'plain');
        } else {
          /* Согласие спрашивается один раз за прогон, на первой разбитой
             главе и до первого удаления. Ответ действует до конца прогона:
             спрашивать заново на каждой главе — значит превратить один
             ответ в несколько случайных. */
          const answer = opts.fragAnswer || (opts.askFragmented
            ? await opts.askFragmented({ count: pairs.length, title: ch.title })
            : 'skip');
          opts.fragAnswer = answer;
          if (answer !== 'merge') {
            st.skipOther++;
            ui.log(`${tick} «${ch.title}» — фрагментов ${pairs.length}, свод отключён: пропуск.`, 'warn');
            report(row, 'несколько фрагментов');
            continue;
          }
          ui.log(`${tick} «${ch.title}» — фрагментов ${pairs.length}: свожу к одному`, 'plain');
        }
      }

      const { origId, trId } = pairs[0];
      ui.log(`${tick} «${ch.title}» ↔ «${siteTitle}» | chap=${info.chapterId} orig=${origId} tr=${trId} | ${ch.text.length}с`, 'plain');

      /* Сверка с сохранённым и «пропуск совпадающего» держатся на том, что
         на сайте лежит ПРЕЖНЯЯ версия ЭТОЙ главы. У разбитой главы это не
         так: старый перевод делали абзацами по чужой разбивке, он бывает
         плохого качества, и нумерация могла разъехаться. chrF на нём даёт
         низкий счёт при верной главе и отказывает зря, а «совпадает» не
         значит ничего. Обе проверки отключаются на свод, и в лог уходит
         причина: молча пропущенный заслон читался бы как «проверено».

         Глава при этом не остаётся без защиты: номер и название сверены
         планом до записи, а после свода глава идёт обычным порядком, где
         chrF снова работает. */
      const savedText = merge ? null : (pairs
        .map(p2 => readSavedText(doc, p2.trId))
        .filter(t => t != null)
        .join('\n\n') || null);
      const same = merge ? false : compareAndLog(savedText, ch.text, ui);
      if (merge) ui.log('     сверка chrF пропущена: у разбитой главы прежний текст абзацами и не показателен', 'plain');
      const titleSame = titleNorm(siteTitle) === titleNorm(ch.title);

      /* Последний заслон перед записью: даже при «правильном» номере глава
         на сайте может оказаться ДРУГОЙ (нумерация разъехалась, тег
         поправили, файл от другой версии книги). Сверяем с уже сохранённым
         переводом — это тот же текст, что мы льём, только прошлой версии.
         Не похоже — не пишем, даже если строку назначил человек. */
      let similar = null;
      if (CFG.minChrf > 0 && savedText != null) similar = looksLikeSameChapter(savedText, ch.text);
      if (similar && !similar.ok) {
        st.fail++;
        ui.log(`     ОТКАЗ: глава на сайте не похожа на нашу (chrF ${similar.chrf.toFixed(3)} < ${CFG.minChrf}) — нумерация разъехалась?`, 'err');
        ui.log('     → сверьте главу глазами; запись не выполнена', 'warn');
        report(row, 'отказ chrF', similar.chrf);
        continue;
      }
      if (similar) ui.log(`     сверка с сохранённым: chrF ${similar.chrf.toFixed(3)} — та же глава`, 'plain');

      const doText = !(opts.skipSame && same);
      const doTitle = (CFG.titleMode === 'title-and-body') && !(opts.skipSame && titleSame);

      if (opts.dryRun) {
        if (!doText && !doTitle) { st.skipSame++; ui.log('     [DRY] пропущено бы (всё совпадает)', 'plain'); report(row, 'пропуск'); }
        else {
          st.wouldSave++;
          const parts = [];
          if (doText) parts.push('текст');
          if (doTitle) parts.push('название');
          ui.log(`     [DRY] будет обновлено: ${parts.join(' + ')}`, 'plain');
          report(row, 'будет залито', similar ? similar.chrf : null);
        }
      } else if (!doText && !doTitle) {
        st.skipSame++;
        ui.log('     пропущено (всё совпадает)', 'plain');
        report(row, 'пропуск');
      } else {
        if (doTitle) {
          await saveChapterTitle(plan.bookId, info.chapterId, ch.title, csrf);
          st.titleChanged++;
          ui.log('     название обновлено', 'ok');
          await globalPause(CFG.intraMs, 0);
        } else if (CFG.titleMode === 'title-and-body') {
          ui.log('     название совпадает — переименование пропущено', 'plain');
        }

        if (doText) {
          if (merge) {
            /* Сведение к одному блоку. Сначала текст — в первый фрагмент:
               если оборвётся на удалении, глава останется с полным новым
               текстом и лишними кусками старого (видно и чинимо). Обратный
               порядок при обрыве оставил бы главу пустой.
               Удаляются последние фрагменты, первый (с новым текстом)
               остаётся. */
            await saveTranslation(plan.bookId, info.chapterId, origId, trId, ch.text, csrf);
            ui.log(`     текст залит в первый фрагмент (${ch.text.length}с)`, 'ok');
            await globalPause(CFG.intraMs, CFG.jitterMs);

            /* Лишние фрагменты удаляются ОДНИМ запросом через массовую форму
               страницы книги. Пофрагментный путь нужен только там, где формы
               нет (не владелец книги): он дороже — запрос на каждый
               фрагмент, — но страница главы его умеет всегда. */
            /* С конца главы к началу: первый фрагмент остаётся под новый
               текст, остальные снимаются последними первыми. */
            const extraIds = pairs.slice(1).map(p2 => p2.origId).reverse();
            let bulk = false;
            if (extraIds.length) {
              try {
                bulk = await removeFragmentsBulk(doc, extraIds);
              } catch (e) {
                bulk = false;
                ui.log(`     массовое удаление не прошло (${e.message}), удаляю по одному`, 'warn');
              }
            }
            if (bulk) {
              st.fragRemoved += extraIds.length;
              ui.log(`     фрагментов удалено одним запросом: ${extraIds.length}`, 'ok');
            } else {
              if (extraIds.length) ui.log('     массовой формы нет — удаляю по одному', 'plain');
              /* Порядок — как в `extraIds`: с конца главы к началу. Так
                 лог читается сверху вниз («остаётся» убывает), а обрыв
                 посередине оставляет целой голову главы, а не хвост. */
              for (let i = 0; i < extraIds.length; i++) {
                if (ui.aborted) { ui.log('     остановлено: удаление фрагментов не завершено', 'warn'); break; }
                await removeFragment(plan.bookId, info.chapterId, extraIds[i]);
                st.fragRemoved++;
                ui.log(`     удалён фрагмент ${extraIds[i]} (остаётся ${extraIds.length - i - 1})`, 'plain');
                if (i < extraIds.length - 1) await globalPause(CFG.delMs, CFG.jitterMs);
              }
            }
          } else {
          await saveTranslation(plan.bookId, info.chapterId, origId, trId, ch.text, csrf);
          }
          if (CFG.verify) {
            await globalPause(CFG.intraMs, 0);
            const vDoc = (await getDoc(info.url)).doc;
            const vRaw = readSavedText(vDoc, trId);
            if (norm(vRaw == null ? '' : vRaw) !== norm(ch.text)) {
              st.fail++;
              ui.log('     ВНИМАНИЕ: после сохранения текст не совпал!', 'err');
              if (vRaw != null) { const d = firstDiff(norm(vRaw), norm(ch.text)); ui.log(`        сайт: ${d.site}`, 'plain'); ui.log(`        файл: ${d.file}`, 'plain'); }
              report(row, 'расхождение после записи');
              continue;
            }
            ui.log('     залито + проверка OK', 'ok');
          } else ui.log('     залито (без проверки).', 'ok');
          st.saved++;
        } else {
          ui.log('     текст совпадает — запись пропущена', 'plain');
        }
        report(row, 'залито', similar ? similar.chrf : null);
      }
    } catch (e) { st.fail++; ui.log('     ОШИБКА: ' + e.message, 'err'); report(row, 'ошибка: ' + e.message); }

    if (job < total - 1 && !ui.aborted) await globalPause(opts.dryRun ? CFG.dryMs : CFG.delayMs, CFG.jitterMs);
  }

  ui.setProgress(total, total);
  const summary = opts.dryRun
    ? `=== DRY-RUN: зальётся ${st.wouldSave}, пропущено ${st.skipSame}, не найдено ${st.skipOther}, неоднозначных ${st.ambiguous}, вне диапазона ${st.skipRange}, проблемных ${st.skipProblem} ===`
    : `=== ГОТОВО: залито ${st.saved}, названий ${st.titleChanged}${st.fragRemoved ? `, фрагментов удалено ${st.fragRemoved}` : ''}, пропущено ${st.skipSame}, не найдено ${st.skipOther}, неоднозначных ${st.ambiguous}, вне диапазона ${st.skipRange}, проблемных ${st.skipProblem}, ошибок ${st.fail} ===`;
  ui.log(summary, st.fail ? 'warn' : 'ok');

  if (notFoundNums.length > 0 || notFoundTitles.length > 0) {
    const parts = [];
    if (notFoundNums.length > 0) parts.push(`Главы: ${notFoundNums.sort((a, b) => a - b).join(', ')}`);
    if (notFoundTitles.length > 0) parts.push(`Без номера: ${notFoundTitles.join('; ')}`);
    ui.log(`Не найдены на сайте → ${parts.join(' | ')}`, 'warn');
  }

  return Object.assign({ reports }, st);
}

/* Один вход на обе кнопки: без готового плана он строится заново (старое
   поведение «Старт»), с планом — заливается ровно он. */
async function run(parsed, opts, ui) {
  const bookId = bookIdFromUrl();
  if (!bookId) { ui.log('Не определён id книги из URL.', 'err'); return null; }

  ui.log(`Книга #${bookId} | Глав: ${parsed.length} | dry-run: ${opts.dryRun ? 'ДА' : 'нет'}`, 'ok');
  const tmLabel = CFG.titleMode === 'off' ? 'выкл' : CFG.titleMode === 'title-only' ? 'ТОЛЬКО названия' : 'названия + текст';
  progressMarks.length = 0;
  ui.setProgress(0, parsed.length);
  ui.log(`Названия: ${tmLabel} | Пауза: ${CFG.delayMs}мс ± ${CFG.jitterMs}мс`, 'plain');
  ui.log(opts.dryRun ? 'Режим: DRY-RUN' : (opts.skipSame ? 'Режим: совпадающие ПРОПУСКАЮТСЯ (текст и названия).' : 'Режим: ПРИОРИТЕТ ФАЙЛА — льётся КАЖДАЯ найденная глава.'), 'plain');

  let plan = opts.plan || null;
  if (!plan) {
    let site;
    try { site = await buildSiteMap(bookId); }
    catch (e) { ui.log('Ошибка чтения оглавления: ' + e.message, 'err'); return { error: 'оглавление' }; }
    logSiteReport(site, ui);
    plan = buildPlan(parsed, site, parseRange(opts.rangeStr));
  }
  const res = await writePlan(plan, parsed, opts, ui);
  res.plan = plan;
  return res;
}

/* Отчёт об оглавлении — общий для «Плана» и «Старта»: повторяющиеся номера и
   нестандартные главы человек должен видеть до заливки, а не в конце. */
function logSiteReport(site, ui) {
  const nums = [...site.byNum.keys()].sort((a, b) => a - b);
  ui.log(`Оглавление: глав=${site.ordered.length}, номеров=${site.byNum.size}, диапазон=${nums.length ? nums[0] + '..' + nums[nums.length - 1] : '∅'}.`, site.ordered.length ? 'ok' : 'err');

  const repeated = [...site.byNum].filter(([, g]) => g.length > 1).sort((a, b) => a[0] - b[0]);
  if (repeated.length) {
    ui.log(`⚠ Номер повторяется у ${repeated.length} групп глав на сайте:`, 'warn');
    for (const [num, g] of repeated) {
      ui.log(`   №${num}: ${g.map(x => `id=${x.chapterId} «${x.siteTitle}»`).join(' | ')}`, 'warn');
    }
  }

  if (site.nonStandard.length) {
    const typeLabels = { fractional: 'дробный №', range: 'диапазон', nonstandard: 'без номера' };
    ui.log(`⚠ Нестандартные главы на сайте (${site.nonStandard.length}):`, 'warn');
    for (const item of site.nonStandard) {
      ui.log(`   · [${typeLabels[item.type] || item.type}] «${item.title}» (id=${item.chapterId})`, 'warn');
    }
  }
}

function candOptions(row) {
  /* Один список на всё: выбранная глава и кандидаты по номеру. Оценка
     считается каждой — иначе у назначенной строки числа нет вовсе, и
     сравнить её с соседями нечем (именно так рейтинг и «пропадал»). */
  const list = [];
  const seen = new Set();
  const add = (chapterId, siteTitle) => {
    const id = String(chapterId);
    if (seen.has(id)) return;
    seen.add(id);
    list.push({ chapterId: id, siteTitle });
  };
  if (row.site) add(row.site.chapterId, row.site.siteTitle);
  for (const c of row.cands || []) add(c.chapterId, c.siteTitle != null ? c.siteTitle : c.title);
  return rankCandidates(row.title, list).map((c) => ({
    id: String(c.chapterId), title: c.siteTitle, score: c.score,
  }));
}

/* Весь список книги в этом `<select>`: строится один раз, при первом
   раскрытии. Порядок — как в оглавлении; уже предложенные варианты не
   дублируются. Соседи по оглавлению поднимаются наверх: когда нумерация
   разъехалась, верная глава обычно рядом. */
function fillSelect(select, row) {
  const all = allSiteOptions();
  const have = new Set([...select.querySelectorAll('option')].map(o => o.value));
  /* Счёт считается для КАЖДОЙ главы книги, а не только для тех, что пришли
     кандидатами по номеру: иначе «вся книга» — это список без единого числа,
     и найти по названию главу, у которой на сайте другой номер, нечем.
     Считается один раз, при раскрытии: на книге в тысячу глав это один
     проход по строкам, а не построение элементов. */
  const scored = all.map(it => ({ ...it, score: titleScore(row.title, it.title) }));
  const anchor = row.site ? scored.findIndex(g => g.id === String(row.site.chapterId)) : -1;
  const near = [];
  for (const d of [1, -1, 2, -2]) if (anchor >= 0 && scored[anchor + d] && !have.has(scored[anchor + d].id)) near.push(scored[anchor + d]);
  if (near.length) {
    const g = document.createElement('optgroup');
    g.label = 'рядом по оглавлению';
    for (const it of near) g.appendChild(optionFor(it));
    select.appendChild(g);
  }
  /* «Вся книга» — по убыванию похожести: то, что нужно найти, оказывается
     наверху, а не за сотней чужих глав в порядке оглавления. */
  const rest = scored.filter(it => !have.has(it.id)).sort((a, b2) => b2.score - a.score);
  const gAll = document.createElement('optgroup');
  gAll.label = `вся книга по похожести (${rest.length})`;
  for (const it of rest) gAll.appendChild(optionFor(it));
  select.appendChild(gAll);
}

/* ---------- UI ---------- */
GM_addStyle(`
/* Встроенный блок над таблицей глав. Цвета берутся у сайта: у Rulate тёмная
   тема — это класс dark-theme на html и body плюс набор переменных
   --dark-*; светлая — его отсутствие. Своих значений не задаём, поэтому
   панель меняет цвет вместе со страницей, а не спорит с ней. */
#ru-bulk, #ru-bulk * { box-sizing: border-box !important; }
#ru-bulk {
  display: none; margin: 10px 0; padding: 0;
  border: 1px solid #ddd; border-radius: 6px; overflow: hidden;
  background: #fff; color: #333;
  font: 13px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif !important;
  text-align: left !important;
}
html.dark-theme #ru-bulk, body.dark-theme #ru-bulk {
  background: var(--dark-bg-card, #2a2a2a);
  color: var(--dark-text-primary, #e0e0e0);
  border-color: var(--dark-border-primary, #404040);
}
#ru-bulk.ru-open { display: flex; flex-direction: column; }
/* Панель растёт по содержимому и своей прокрутки не имеет: при выборе файла
   появляется план, и вместо новой полосы прокрутки панель просто становится
   выше. Прокрутка есть только там, где содержимое заведомо безразмерное, —
   в окне лога и в таблице плана. */

#ru-bulk .ru-hd {
  flex: 0 0 auto; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 8px 12px; border-bottom: 1px solid #ddd;
  background: #f5f5f5; font-weight: 600;
}
html.dark-theme #ru-bulk .ru-hd, body.dark-theme #ru-bulk .ru-hd {
  background: var(--dark-bg-secondary, #2d2d2d);
  border-bottom-color: var(--dark-border-primary, #404040);
}
/* Своя строка кнопки: кнопка стоит выше ряда actions сайта, поэтому её
   отступ снизу отделяет её от чужих кнопок. */
#ru-bulk, .ru-btn-row { box-sizing: border-box; }
.ru-btn-row { margin: 6px 0 10px; }
.ru-btn-row > button { cursor: pointer; }
/* Иконка — спрайт сайта (Bootstrap 2): те же 14x14 и отступ справа, что у
   «Добавить главы», иначе кнопка выбивается из ряда. */
#ru-toggle i[class^="icon-"] { margin: 1px 4px 0 0; vertical-align: text-top; }
#ru-toggle > span { vertical-align: middle; }
#ru-bulk .ru-hd b { font-size: 13px; font-weight: 600; }
/* Предупреждение на время запуска: обычный шрифт и обычный цвет, чтобы не
   выглядело ошибкой; заметность даёт жёлтый треугольник, а не красный текст. */
#ru-bulk .ru-run-warn {
  display: inline-flex; align-items: center; gap: 5px;
  margin-left: auto; font-weight: 400; font-size: 12.5px;
  color: #a57300; white-space: nowrap;
}
#ru-bulk .ru-run-warn[hidden] { display: none; }
#ru-bulk .ru-run-warn svg { flex: 0 0 auto; color: #e0a800; }
html.dark-theme #ru-bulk .ru-run-warn, body.dark-theme #ru-bulk .ru-run-warn { color: #ffd166; }
html.dark-theme #ru-bulk .ru-run-warn svg, body.dark-theme #ru-bulk .ru-run-warn svg { color: #ffc107; }
#ru-bulk .ru-hd .ru-ver { font-weight: 400; color: #888; font-size: 11.5px; }
html.dark-theme #ru-bulk .ru-hd .ru-ver, body.dark-theme #ru-bulk .ru-hd .ru-ver { color: var(--dark-text-muted, #888); }

#ru-bulk .ru-bd {
  flex: 1 1 auto;
  padding: 10px 12px; display: flex; flex-direction: column;
}
#ru-bulk .ru-bd > * { flex: 0 0 auto; }

#ru-bulk button {
  background: #e9e9e9; color: #333; border: 1px solid #ccc; border-radius: 4px;
  padding: 5px 11px; cursor: pointer; margin: 0 !important; font: inherit !important;
  user-select: none; white-space: nowrap; line-height: 1.35;
}
#ru-bulk button:hover:not(:disabled) { background: #ddd; }
#ru-bulk button.ru-go { background: #2e9e5b; border-color: #27864c; color: #fff; }
#ru-bulk button.ru-go:hover:not(:disabled) { background: #27864c; }
#ru-bulk button:disabled { opacity: .5; cursor: not-allowed; }
html.dark-theme #ru-bulk button, body.dark-theme #ru-bulk button {
  background: var(--dark-bg-tertiary, #3a3a3a); color: var(--dark-text-primary, #e0e0e0);
  border-color: var(--dark-border-secondary, #555);
}
html.dark-theme #ru-bulk button:hover:not(:disabled), body.dark-theme #ru-bulk button:hover:not(:disabled) { background: var(--dark-bg-hover, #404040); }
html.dark-theme #ru-bulk button.ru-go, body.dark-theme #ru-bulk button.ru-go { background: #2e9e5b; border-color: #27864c; color: #fff; }

#ru-bulk .ru-log-box {
  /* 220px — примерно десять строк лога: шапка запуска и первые главы
     читаются без прокрутки; дальше лог прокручивается сам и выше 320px
     не растёт, иначе длинный прогон растянул бы панель на экран. */
  flex: 0 0 auto; min-height: 220px; max-height: 320px; overflow: auto !important;
  background: #fafafa; border: 1px solid #ddd; border-radius: 4px;
  padding: 7px !important; margin: 8px 0 0 0 !important;
  font: 11.5px/1.45 ui-monospace, Menlo, Consolas, monospace !important;
  white-space: pre-wrap !important; word-break: break-word !important;
}
html.dark-theme #ru-bulk .ru-log-box, body.dark-theme #ru-bulk .ru-log-box {
  background: var(--dark-bg-primary, #1a1a1a); border-color: var(--dark-border-light, #333);
}
#ru-bulk .ru-log-box span { margin: 0 !important; padding: 0 !important; display: inline; }

#ru-bulk .ru-bar { height: 6px; background: #e0e0e0; border-radius: 3px; margin: 8px 0 0 0; overflow: hidden; }
html.dark-theme #ru-bulk .ru-bar, body.dark-theme #ru-bulk .ru-bar { background: var(--dark-bg-tertiary, #3a3a3a); }
#ru-bulk .ru-bar > i { display: block; height: 100%; width: 0; background: #37c46a; transition: width .2s; }
#ru-bulk .ru-prog { margin: 4px 0 0 0 !important; }
#ru-bulk .ru-prog #ru-prog-txt { font: 11.5px/1.4 ui-monospace, Menlo, Consolas, monospace; color: #777; }
html.dark-theme #ru-bulk .ru-prog #ru-prog-txt, body.dark-theme #ru-bulk .ru-prog #ru-prog-txt { color: var(--dark-text-muted, #888); }

#ru-bulk .ru-row { display: flex; gap: 6px 8px; align-items: center; flex-wrap: wrap; margin: 8px 0 0 0 !important; padding: 0 !important; }
#ru-bulk .ru-row:first-child { margin-top: 0 !important; }
#ru-bulk .ru-ok { color: #1e7e45 !important; }
#ru-bulk .ru-err { color: #c0392b !important; }
#ru-bulk .ru-warn { color: #a57300 !important; }
#ru-bulk .ru-plain { color: #777 !important; }
html.dark-theme #ru-bulk .ru-ok, body.dark-theme #ru-bulk .ru-ok { color: #7fe39a !important; }
html.dark-theme #ru-bulk .ru-err, body.dark-theme #ru-bulk .ru-err { color: #ff8585 !important; }
html.dark-theme #ru-bulk .ru-warn, body.dark-theme #ru-bulk .ru-warn { color: #ffd166 !important; }
html.dark-theme #ru-bulk .ru-plain, body.dark-theme #ru-bulk .ru-plain { color: var(--dark-text-muted, #888) !important; }

#ru-bulk label { display: inline-flex; gap: 5px; align-items: center; user-select: none; cursor: pointer; white-space: nowrap; margin: 0 !important; font-weight: normal !important; }
#ru-bulk input[type=number], #ru-bulk input[type=text] {
  width: 72px; background: #fff; color: #333; border: 1px solid #ccc; border-radius: 4px;
  padding: 4px 6px; font: inherit !important; margin: 0 !important; outline: none;
}
#ru-bulk input[type=text].ru-wide { width: 140px; }
#ru-bulk select { background: #fff; color: #333; border: 1px solid #ccc; border-radius: 4px; padding: 4px 6px; font: inherit !important; margin: 0 !important; }
html.dark-theme #ru-bulk input[type=number], body.dark-theme #ru-bulk input[type=number],
html.dark-theme #ru-bulk input[type=text], body.dark-theme #ru-bulk input[type=text],
html.dark-theme #ru-bulk select, body.dark-theme #ru-bulk select {
  background: var(--dark-bg-primary, #1a1a1a); color: var(--dark-text-primary, #e0e0e0);
  border-color: var(--dark-border-secondary, #555);
}
#ru-bulk input[type=checkbox] { margin: 0 !important; }
#ru-bulk .ru-muted { color: #777; }
html.dark-theme #ru-bulk .ru-muted, body.dark-theme #ru-bulk .ru-muted { color: var(--dark-text-muted, #888); }
#ru-bulk .ru-sep { border-top: 1px solid #e5e5e5; margin: 10px 0 !important; padding: 0 !important; }

/* Подтверждение перед сводом разбитых глав. Своё окно нужно потому, что
   решение принимается до первой записи и должно быть видно над страницей, а
   не строкой в логе: удаление фрагментов необратимо. */
#ru-confirm { position: fixed; inset: 0; z-index: 100000; display: none; }
#ru-confirm.ru-open { display: flex; align-items: center; justify-content: center; }
#ru-confirm .ru-cf-back { position: absolute; inset: 0; background: rgba(0, 0, 0, .45); }
#ru-confirm .ru-cf-box {
  position: relative; width: min(560px, calc(100vw - 32px)); max-height: calc(100vh - 80px);
  display: flex; flex-direction: column; border-radius: 6px; overflow: hidden;
  background: #fff; color: #333; border: 1px solid #ccc;
  font: 13px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif !important;
  text-align: left !important;
}
html.dark-theme #ru-confirm .ru-cf-box, body.dark-theme #ru-confirm .ru-cf-box {
  background: var(--dark-bg-card, #2a2a2a); color: var(--dark-text-primary, #e0e0e0);
  border-color: var(--dark-border-primary, #404040);
}
#ru-confirm .ru-cf-hd { padding: 10px 14px; font-weight: 600; border-bottom: 1px solid #ddd; background: #f5f5f5; }
html.dark-theme #ru-confirm .ru-cf-hd, body.dark-theme #ru-confirm .ru-cf-hd {
  background: var(--dark-bg-secondary, #2d2d2d); border-bottom-color: var(--dark-border-primary, #404040);
}
#ru-confirm .ru-cf-bd { padding: 12px 14px; overflow-y: auto; }
#ru-confirm .ru-cf-bd p { margin: 0 0 9px; }
#ru-confirm .ru-cf-list { margin: 0 0 9px; padding-left: 18px; max-height: 150px; overflow-y: auto; }
#ru-confirm .ru-cf-list li { margin: 0 0 3px; }
#ru-confirm .ru-cf-acts { display: flex; justify-content: flex-end; gap: 8px; padding: 10px 14px; border-top: 1px solid #ddd; }
html.dark-theme #ru-confirm .ru-cf-acts, body.dark-theme #ru-confirm .ru-cf-acts { border-top-color: var(--dark-border-primary, #404040); }
#ru-confirm button {
  background: #e9e9e9; color: #333; border: 1px solid #ccc; border-radius: 4px;
  padding: 6px 12px; cursor: pointer; font: inherit !important;
}
#ru-confirm button:hover { background: #ddd; }
#ru-confirm button.ru-danger { background: #c9302c; border-color: #ac2925; color: #fff; }
#ru-confirm button.ru-danger:hover { background: #ac2925; }
html.dark-theme #ru-bulk .ru-sep, body.dark-theme #ru-bulk .ru-sep { border-top-color: var(--dark-border-light, #333); }

/* Таблица плана видна всегда: отдельной кнопки «План» нет, план и есть работа. */
#ru-bulk .ru-plan-box { margin: 0 !important; }
#ru-bulk .ru-plan-title { margin: 0 !important; }
#ru-bulk .ru-plan-title b { font-weight: 600; }
#ru-bulk .ru-plan-hd { gap: 8px; margin-bottom: 10px !important; }
#ru-bulk .ru-plan-list {
  /* Прокрутка своя и невысокая: план — это список на сотни строк, и тянуть
     его на всю страницу незачем, когда рядом таблица глав сайта. */
  max-height: min(42vh, 420px); overflow: auto;
  background: #fff; border: 1px solid #ddd; border-radius: 4px;
  margin: 6px 0 0 0 !important;
  font: 11.5px/1.4 ui-monospace, Menlo, Consolas, monospace !important;
}
html.dark-theme #ru-bulk .ru-plan-list, body.dark-theme #ru-bulk .ru-plan-list {
  background: var(--dark-bg-primary, #1a1a1a); border-color: var(--dark-border-light, #333);
}
#ru-bulk .ru-plan-row { display: flex; flex-wrap: wrap; gap: 4px 6px; align-items: center; padding: 4px 6px; border-bottom: 1px solid #eee; }
html.dark-theme #ru-bulk .ru-plan-row, body.dark-theme #ru-bulk .ru-plan-row { border-bottom-color: #262626; }
#ru-bulk .ru-plan-row:last-child { border-bottom: 0; }
#ru-bulk .ru-plan-row.ru-pr-out { opacity: .5; }
#ru-bulk .ru-plan-row.ru-pr-issue { background: #fff8e1; }
#ru-bulk .ru-plan-row.ru-pr-none { background: #fdeaea; }
#ru-bulk .ru-plan-row.ru-pr-manual { background: #e8f4fb; }
html.dark-theme #ru-bulk .ru-plan-row.ru-pr-issue, body.dark-theme #ru-bulk .ru-plan-row.ru-pr-issue { background: #2a2113; }
html.dark-theme #ru-bulk .ru-plan-row.ru-pr-none, body.dark-theme #ru-bulk .ru-plan-row.ru-pr-none { background: #2a1717; }
html.dark-theme #ru-bulk .ru-plan-row.ru-pr-manual, body.dark-theme #ru-bulk .ru-plan-row.ru-pr-manual { background: #12242a; }
/* Шапка таблицы плана: две колонки названий подписаны, поэтому она стоит на
   сетке строки, а не отдельной вёрсткой. */
#ru-bulk .ru-plan-head {
  position: sticky; top: 0; z-index: 1;
  background: #f0f0f0; font-weight: 600; color: #555; font-size: 11px;
  text-transform: uppercase; letter-spacing: .03em;
}
html.dark-theme #ru-bulk .ru-plan-head, body.dark-theme #ru-bulk .ru-plan-head {
  background: var(--dark-bg-secondary, #2d2d2d); color: var(--dark-text-secondary, #b0b0b0);
}
/* Подписи колонок стоят по центру своей колонки: шапка повторяет сетку
   строки (номер, название, стрелка, колонка сайта), поэтому «В файле» и
   «На сайте» встают ровно над тем, что подписывают. */
#ru-bulk .ru-plan-head .ru-pr-title, #ru-bulk .ru-plan-head .ru-pr-tgt { text-align: center; }
#ru-bulk .ru-plan-head .ru-pr-tgt { display: block; }
#ru-bulk .ru-pr-num { flex: 0 0 42px; color: #777; text-align: right; }
#ru-bulk .ru-pr-title { flex: 1 1 44%; min-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#ru-bulk .ru-pr-arrow { flex: 0 0 auto; color: #999; }
#ru-bulk .ru-pr-tgt { flex: 1 1 38%; min-width: 130px; max-width: 100%; display: flex; flex-direction: column; gap: 2px; }
/* Список глав и весы — одна строка: сверка стоит справа от того, что сверяет,
   а не отдельной колонкой у края. */
#ru-bulk .ru-pr-pick { display: flex; align-items: center; gap: 5px; }
#ru-bulk .ru-pr-pick select { flex: 1 1 auto; min-width: 0; }
/* Подпись под списком: итог сверки и решение строки в одну строку, чтобы
   высота строки не гуляла от того, сверена глава или ещё нет. */
#ru-bulk .ru-pr-meta { display: flex; flex-wrap: wrap; gap: 0 6px; align-items: baseline; min-height: 14px; }
#ru-bulk .ru-pr-status { color: #888; font-size: 10.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#ru-bulk .ru-pr-score { font-size: 10.5px; color: #888; white-space: nowrap; }
#ru-bulk .ru-pr-score.ru-bad { color: #c0392b; }
#ru-bulk .ru-pr-score.ru-good { color: #1e7e45; }
html.dark-theme #ru-bulk .ru-pr-score.ru-bad, body.dark-theme #ru-bulk .ru-pr-score.ru-bad { color: #ff8585; }
html.dark-theme #ru-bulk .ru-pr-score.ru-good, body.dark-theme #ru-bulk .ru-pr-score.ru-good { color: #7fe39a; }
/* Кнопка сверки на строке: только иконка, поэтому квадратная и без рамки. */
#ru-bulk .ru-pr-probe {
  flex: 0 0 auto; padding: 3px 5px !important; line-height: 1 !important;
  color: #666; background: transparent !important; border-color: transparent !important;
}
#ru-bulk .ru-pr-probe:hover:not(:disabled) { background: #e2e2e2 !important; border-color: #ccc !important; }
#ru-bulk .ru-pr-probe svg { display: block; }
html.dark-theme #ru-bulk .ru-pr-probe, body.dark-theme #ru-bulk .ru-pr-probe { color: var(--dark-text-secondary, #b0b0b0); }
html.dark-theme #ru-bulk .ru-pr-probe:hover:not(:disabled), body.dark-theme #ru-bulk .ru-pr-probe:hover:not(:disabled) {
  background: var(--dark-bg-hover, #404040) !important; border-color: var(--dark-border-secondary, #555) !important;
}

/* Мобильный: строки плана в две колонки, поля на всю ширину, кнопки крупнее
   (палец точнее мыши не попадает). Название и номер в одну строку. */
@media (max-width: 640px) {
  #ru-bulk .ru-log-box { max-height: 260px; }
  #ru-bulk .ru-hd { padding: 8px 10px; }
  #ru-bulk .ru-bd { padding: 10px; }
  #ru-bulk .ru-plan-list { max-height: 50vh; }
  #ru-bulk .ru-plan-row { gap: 4px; }
  /* Название и номер в одну строку, список глав с весами под ними. */
  #ru-bulk .ru-pr-title { flex: 1 1 auto; order: 1; }
  #ru-bulk .ru-pr-num { order: 2; flex: 0 0 auto; }
  #ru-bulk .ru-plan-head .ru-pr-tgt { display: none; }
  #ru-bulk .ru-pr-arrow { display: none; }
  #ru-bulk .ru-pr-tgt { order: 3; flex: 1 1 100%; }
  #ru-bulk .ru-row > input[type=text], #ru-bulk .ru-row > input[type=number] { width: 84px; }
  #ru-bulk .ru-row > select { flex: 1 1 100%; }
  #ru-bulk button { padding: 8px 12px; }
}
`)

/* Кнопка встаёт в блок действий над таблицей глав — туда, где сайт сам
   держит «Выбрать диапазон» и «выделить все главы». Это не отдельное окно
   поверх страницы: человек работает с главами, и панель должна быть на том
   же месте, что и они. Нет такого блока (другая разметка, мобильная
   версия) — остаётся тело страницы, панель всё равно доступна. */
/* Кнопка-образец: своя встаёт сразу за ней и берёт её классы, поэтому
   размер шрифта и высота совпадают без подгонки под тему и версию сайта.
   Сначала ищем «Связанные переводы» — там просят место; если её нет (книга
   без связанных переводов), годится «Добавить главы». Обе бывают только у
   автора перевода: у читателя остаётся блок действий над таблицей глав. */
function findSiteButton(patterns) {
  for (const el of document.querySelectorAll('a, button')) {
    const t = (el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
    if (!patterns.some(re => re.test(t))) continue;
    /* Только кнопка рядом с оглавлением, а не ссылка в меню страницы. */
    if (el.closest('#Chapters, .for_table_container, .chapter-controls')) return el;
    if (document.querySelector('#Chapters') && el.parentNode &&
        !el.closest('.navbar, .nav, #header, .menu')) return el;
  }
  return null;
}

function findAddChaptersButton() {
  return findSiteButton([/^связанные переводы/, /^добавить глав/]);
}

/* Скрипты самой страницы одним текстом: в них лежат `new CUser({id})` и
   `new CBook({owner_id})`, и читать их надёжнее, чем обращаться к глобальным
   переменным — в песочнице менеджера скриптов они не видны. */
function pageInlineScripts() {
  let out = '';
  for (const s of document.querySelectorAll('script:not([src])')) out += '\n' + (s.textContent || '');
  return out;
}

function bookOwnerFromHtml(html) {
  const m = String(html || '').match(/new\s+CBook\(\{[\s\S]{0,4000}?owner_id:\s*(\d+)/);
  return m ? m[1] : null;
}

/* Права на книгу: панель пишет переводы, и на чужой книге её место — нигде.
   Два признака, оба со страницы: владелец из `new CBook({owner_id})` и
   кнопки правки, которые сайт рисует только автору перевода и переводчикам
   («Связанные переводы», «Добавить главы»). Не залогинен — писать некому,
   и панель не нужна тоже. */
function canEditBook() {
  const scripts = pageInlineScripts();
  const uid = userIdFromHtml(scripts);
  if (!uid) return false;
  const owner = bookOwnerFromHtml(scripts);
  if (owner && String(owner) === String(uid)) return true;
  return !!findAddChaptersButton();
}

function findChapterAnchor() {
  const table = document.querySelector('#Chapters');
  const box = (table && table.closest('.for_table_container')) || null;
  /* Перед самой таблицей: между переключателем страниц и списком глав. */
  return { table, box };
}

/* Заголовок «Оглавление:» — h2 над списком глав. Ищем его по документу, а
   не по соседям таблицы: между ними стоят переключатель страниц, ряд
   кнопок и форма подписки, и спускаться по ним поштучно значит зависеть от
   их порядка. Берём ближайший сверху — он и есть заголовок оглавления. */
function findTocHeading(table) {
  if (!table || !document.body.contains(table)) return null;
  let best = null, bestDist = Infinity;
  for (const h of document.querySelectorAll('h1, h2, h3')) {
    if (!/оглавлен/i.test(h.textContent || '')) continue;
    if (h.contains(table) || table.contains(h)) continue;
    if (!(h.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
    const r1 = h.getBoundingClientRect(), r2 = table.getBoundingClientRect();
    const dist = Math.abs(r2.top - r1.bottom);
    if (dist < bestDist) { bestDist = dist; best = h; }
  }
  return best;
}

/* На чужой книге панели нет вовсе: скрипт пишет переводы, и предлагать это
   там, где прав нет, значит обещать действие, которого не будет. Проверка
   стоит до построения разметки — тогда не остаётся ни кнопки, ни панели. */
if (canEditBook()) {

const panel = document.createElement('div');
panel.id = 'ru-bulk';
/* Таблица глав лежит внутри <form action="…/subscribe">, поэтому кнопка без
   type по умолчанию отправляет форму: страница уезжает на /subscribe, а
   панель исчезает вместе с ней. Проставляем type всем кнопкам сразу. */
/* Строка разметки → узел SVG. innerHTML здесь не годится не из-за данных
   (их нет — разметка статическая), а из-за правила файла: разметка только
   разбирается DOMParser'ом. */
function svgNode(markup) {
  const d = new DOMParser().parseFromString('<body>' + markup + '</body>', 'text/html');
  return document.adoptNode(d.body.firstChild);
}

function disableFormSubmit(root) {
  for (const b of root.querySelectorAll('button:not([type])')) b.type = 'button';
}
/* Разметку панели разбирает DOMParser, а не innerHTML: подстановка шаблона в
   innerHTML — это путь для HTML, а нужен ровно разобранный DOM (и статические
   значения CFG здесь единственные данные, но правило одно на весь файл). */
const panelMarkup = `<div class="ru-hd"> <b>Обновление глав</b> <span class="ru-ver">v${APP_VERSION}</span>
  <span class="ru-run-warn" id="ru-run-warn" role="status" hidden>
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path fill="currentColor" d="M8 1.5 15.2 14H.8L8 1.5Zm0 3.1L3.1 12.9h9.8L8 4.6Zm-.7 2.3h1.4v3.4H7.3V6.9Zm0 4.2h1.4v1.4H7.3v-1.4Z"/></svg>
    <span>Идёт запись — не закрывайте окно и браузер</span>
  </span>
</div>
<div class="ru-bd">
  <div class="ru-row">
    <input type="file" id="ru-file" accept=".txt,text/plain" style="display:none">
    <button id="ru-pick">Выбрать файл с главами</button>
    <button id="ru-start" class="ru-go" disabled title="Залить перевод по плану. Глава, разбитая на сайте на несколько фрагментов, раскладывается по абзацам: каждый абзац текста идёт в свой фрагмент">Старт</button>
    <button id="ru-stop" disabled>Стоп</button>
  </div>
  <div class="ru-row">
    <span class="ru-muted">Глав:</span> <b id="ru-count">0</b>
    <span class="ru-muted">Диапазон:</span>
    <input id="ru-range" type="text" class="ru-wide" placeholder="1-50, 55" title="Диапазон: 1-50, 55, 60-70. Пусто = все">
    <span class="ru-muted" id="ru-range-hint"></span>
  </div>
  <div class="ru-sep"></div>
  <div class="ru-plan-box" id="ru-plan-box">
    <div class="ru-row ru-plan-title"><b id="ru-plan-stat">Плана нет — выберите файл с главами</b></div>
    <div class="ru-row ru-plan-hd">
      <span class="ru-muted">Показывать:</span>
      <select id="ru-plan-filter" title="Какие строки видны в таблице"><option value="all">Все</option><option value="issues">Только спорные</option></select>
    </div>
    <!-- Шапка таблицы: две колонки названий подписаны, иначе непонятно,
         какое из двух названий чьё. -->
    <div class="ru-plan-row ru-plan-head" aria-hidden="true">
      <span class="ru-pr-num"></span>
      <span class="ru-pr-title">В файле</span>
      <span class="ru-pr-arrow"></span>
      <span class="ru-pr-tgt">На сайте</span>
    </div>
    <div class="ru-plan-list" id="ru-plan-list"></div>
  </div>
  <div class="ru-sep"></div>
  <div class="ru-row">
    <span class="ru-muted">Пауза (мс):</span> <input id="ru-delay" type="number" value="${CFG.delayMs}" min="100" step="100">
    <span class="ru-muted">Рандом ±:</span> <input id="ru-jitter" type="number" value="${CFG.jitterMs}" min="0" step="50">
    <span class="ru-muted" title="Порог сверки главы с уже сохранённым переводом на сайте: 0.75 — обычный, 0 — сверка выключена">chrF:</span>
    <input id="ru-chrf" type="number" value="${CFG.minChrf}" min="0" max="1" step="0.05" title="Порог сверки главы с уже сохранённым переводом на сайте. 1 — тексты обязаны совпасть почти дословно, 0.75 — обычный порог (мелкие правки проходят, чужая глава нет), 0 или пусто — сверка выключена.">
  </div>
  <div class="ru-row">
    <label title="Перечитать главу после записи и сверить текст"><input type="checkbox" id="ru-verify" ${CFG.verify ? 'checked' : ''}> Проверять результат</label>
    <label title="Не лить главы, чей текст и название уже совпадают"><input type="checkbox" id="ru-skip-same" ${CFG.skipSame ? 'checked' : ''}> Пропуск совп.</label>
    <label title="Глава на сайте разбита на несколько фрагментов: лишние фрагменты удаляются, текст ложится одним блоком в первый. Перед работой панель покажет такие главы и спросит согласие — удаление фрагмента необратимо. Снятая галочка оставляет разбитые главы нетронутыми"><input type="checkbox" id="ru-multi-frag" ${CFG.multiFragment === 'merge' ? 'checked' : ''}> Сводить фрагменты в один</label>
  </div>
  <div class="ru-row">
    <span class="ru-muted">Названия глав:</span>
    <select id="ru-title-mode">
      <option value="off">Не обновлять</option>
      <option value="title-only">Обновлять названия</option>
      <option value="title-and-body">Обновлять названия + текст</option>
    </select>
  </div>
  <div class="ru-bar"><i id="ru-bar-fill"></i></div>
  <div class="ru-row ru-prog"><span class="ru-muted" id="ru-prog-txt">0 из 0 (0%)</span></div>
  <div class="ru-log-box" id="ru-log"></div>
</div>`;
{
  const tplDoc = new DOMParser().parseFromString('<body>' + panelMarkup + '</body>', 'text/html');
  for (const node of [...tplDoc.body.childNodes]) panel.appendChild(document.adoptNode(node));
}

/* Окно подтверждения — отдельным слоем в body: внутри панели `position:
   fixed` считается от панели, и окно уезжало бы вместе с прокруткой книги. */
const confirmMarkup = `<div id="ru-confirm" role="dialog" aria-modal="true" aria-labelledby="ru-confirm-hd">
  <div class="ru-cf-back"></div>
  <div class="ru-cf-box">
    <div class="ru-cf-hd" id="ru-confirm-hd">Главы разбиты на фрагменты</div>
    <div class="ru-cf-bd" id="ru-confirm-bd"></div>
    <div class="ru-cf-acts">
      <button type="button" id="ru-confirm-no">Остановить</button>
      <button type="button" id="ru-confirm-skip">Не трогать такие главы</button>
      <button type="button" class="ru-danger" id="ru-confirm-go">Свести к одному</button>
    </div>
  </div>
</div>`;
{
  const tplDoc2 = new DOMParser().parseFromString('<body>' + confirmMarkup + '</body>', 'text/html');
  for (const node of [...tplDoc2.body.childNodes]) document.body.appendChild(document.adoptNode(node));
}

/* Кнопка-переключатель в стиле сайта (btn btn-default — тот же класс, что у
   «Выбрать диапазон» рядом). Второе нажатие прячет панель. */
const toggleBtn = document.createElement('button');
toggleBtn.type = 'button';
toggleBtn.id = 'ru-toggle';
toggleBtn.className = 'btn btn-default';
/* Иконка — спрайт сайта (Bootstrap 2, `icon-*`): те же 14x14, что у
   «Добавить главы» и прочих кнопок, поэтому кнопка читается как своя.
   Текст — в отдельном span: на узком экране он может переноситься, а
   иконка остаётся на месте. */
toggleBtn.append(
  Object.assign(document.createElement('i'), { className: 'icon-refresh' }),
  Object.assign(document.createElement('span'), { textContent: 'Обновление глав' }),
);
toggleBtn.setAttribute('aria-expanded', 'false');
toggleBtn.setAttribute('aria-controls', 'ru-bulk');

{
  /* Кнопка сайта — образец оформления: у неё берутся классы, поэтому шрифт,
     отступы и высота совпадают без подгонки. Место своё — своя строка под
     заголовком «Оглавление:», выше ряда действий сайта: иначе кнопка встала
     бы последней в чужом ряду. */
  const siteBtn = findAddChaptersButton();
  toggleBtn.className = (siteBtn && siteBtn.className) || 'btn btn-default';

  const { table, box } = findChapterAnchor();
  const tocHeading = findTocHeading(table);

  const btnRow = document.createElement('div');
  btnRow.className = 'ru-btn-row';
  btnRow.appendChild(toggleBtn);
  if (tocHeading && tocHeading.parentNode) {
    tocHeading.parentNode.insertBefore(btnRow, tocHeading.nextSibling);
  } else if (siteBtn && siteBtn.parentNode) {
    siteBtn.parentNode.insertBefore(toggleBtn, siteBtn.nextSibling);
  }

  if (table && table.parentNode) {
    /* Панель — прямо перед таблицей глав, в том же контейнере: ширина
       совпадает с шириной таблицы, а не ряда кнопок. */
    table.parentNode.insertBefore(panel, table);
  } else {
    const host = (box && box.parentNode) || document.body;
    host.insertBefore(panel, host.firstChild);
  }

  if (!toggleBtn.parentNode) {
    /* Ни заголовка, ни кнопки сайта (другая разметка): кнопка над панелью. */
    const row = panel.parentNode.insertBefore(document.createElement('div'), panel);
    row.className = 'ru-btn-row';
    row.appendChild(toggleBtn);
  }

  /* Кнопки внутри панели лежат в <form action="…/subscribe">, внутри
     которого стоит таблица глав: кнопка без type отправила бы форму и
     увела страницу. Своей кнопке-переключателю type ставится при создании;
     чужие кнопки сайта не трогаем. */
  disableFormSubmit(panel);
}

const $ = (id) => panel.querySelector('#' + id);
const logEl = $('ru-log');
let logText = '';

const log = (msg, cls) => {
  const t = `[${new Date().toLocaleTimeString()}] ${msg}\n`;
  logText += t;
  const s = document.createElement('span');
  if (cls && cls !== 'plain') s.className = 'ru-' + cls;
  else if (cls === 'plain') s.className = 'ru-plain';
  s.textContent = t;
  logEl.appendChild(s);
  logEl.scrollTop = logEl.scrollHeight;
};

/* ---------- состояние панели ---------- */
let parsed = [];
let aborted = false;
let plan = null;            // готовый план: его же заливают кнопки
let site = null;            // оглавление книги на момент построения плана
let touched = new Set();    // fi строк, назначенных вручную в этом проходе
let reports = new Map();    // fi → {status, chrf} из последнего прогона
const ui = {
  log,
  get aborted() { return aborted; },
  /* Отметки времени на каждую обработанную главу: по ним считается темп, а
     по нему — остаток. Хранится окно последних 30 глав, чтобы старые главы
     (пропуски в начале) не искажали скорость до конца заливки. */
  setProgress: (i, n) => {
    const fill = $('ru-bar-fill');
    if (fill) fill.style.width = Math.round(i / Math.max(1, n) * 100) + '%';
    if (i > 0 && (!progressMarks.length || progressMarks[progressMarks.length - 1].done !== i)) {
      progressMarks.push({ done: i, at: Date.now() });
      if (progressMarks.length > 31) progressMarks.shift();
    }
    const txt = $('ru-prog-txt');
    if (txt) txt.textContent = progressText(i, n, progressMarks.slice(-30));
  },
};

/* План живёт только пока открыта страница: это разбор одного прогона, а не
   свойство книги. Хранить его в localStorage значило бы забивать память
   браузера копией оглавления на каждую книгу — и однажды получить план от
   прошлого файла, ничего об этом не сказав. Ключа rlPlan больше нет; старые
   записи не читаются и безвредны. */

/* Чистка прежних версий: план больше не хранится, а мусор в localStorage
   оставался бы навсегда (по записи на каждую книгу, куда заходили). */
function dropLegacyPlanStore() {
  try {
    const id = 'rlPlan:' + (bookIdFromUrl() || '');
    if (localStorage.getItem(id) != null) localStorage.removeItem(id);
  } catch (e) { /* хранилище недоступно — тем более нечего чистить */ }
}
dropLegacyPlanStore();

/* ---------- таблица плана ---------- */
const allSiteOptions = () => (site ? site.ordered : []).map(g => ({ id: String(g.chapterId), title: g.siteTitle, url: g.url }));

/* Достройка «всей книги» в списке строки.

   Полный список для каждой строки сразу — это (строки × главы) элементов: на
   600 главах 363 тысячи. Строить их при перерисовке бессмысленно вдвойне:
   столько одновременно не открывают, а живут такие элементы до следующей
   перерисовки. Поэтому список достраивается по требованию (фокус, наведение,
   раскрытие) — и только он. */
const pendingFill = new Set();

function scheduleFill(select, row) {
  const fill = () => {
    if (select.dataset.filled === '1') return;
    select.dataset.filled = '1';
    pendingFill.delete(select);
    fillSelect(select, row);
  };
  select.addEventListener('focus', fill, { once: true });
  select.addEventListener('pointerenter', fill, { once: true });
  select.addEventListener('pointerdown', fill, { once: true });
  select.addEventListener('change', fill, { once: true });
}

/* Первые варианты строки: выбранная глава и кандидаты по номеру, ближайшие
   по названию сверху; следом — вся книга (fillSelect).

   Полный список строится только для показанных строк (порциями по 60), а
   номера ищутся в нём средствами браузера, без обхода элементов: `<option>`
   на каждую главу — это (строки × главы) элементов, и на 600 главах разом
   такое построение занимало секунды. */
function optionFor(c) {
  const o = document.createElement('option');
  o.value = c.id;
  const sc = c.score != null && c.score >= TITLE_SIMILAR_MIN ? ` · ${c.score.toFixed(2)}` : '';
  /* Без кавычек: колонка уже подписана «На сайте», и кавычки в каждой
     строке только мешают читать длинные названия. */
  o.textContent = `${c.title} · id=${c.id}${sc}`;
  if (c.score != null) o.style.color = c.score >= 0.85 ? '#7fe39a' : c.score >= 0.65 ? '#ffd166' : '';
  return o;
}

function planRowClass(row, rangeOut) {
  if (rangeOut) return 'ru-pr-out';
  if (row.how === 'вручную') return 'ru-pr-manual';
  if (!row.site) return row.cands.length ? 'ru-pr-issue' : 'ru-pr-none';
  return row.host && String(row.host) !== String(row.fi) ? 'ru-pr-issue' : '';
}

/* Состояние отрисовки: сколько строк показано и открыта ли панель. */
const planState = { shown: 0, total: 0, open: false };

/* Покраска одной строки: статус, chrF и цвет. Отдельно от построения DOM,
   потому что после ручного выбора меняется ровно эта строка. */
function paintRow(row, tr, scoreEl, statusEl, rangeSet) {
  const rep = reports.get(row.fi);
  const verdict = rep && rep.chrf != null ? chrfVerdict(rep.chrf) : 'off';
  /* Похожа ли глава и насколько — одной подписью: «Похожа · 1.00». Число без
     слова и слово без числа порознь заставляли сопоставлять две графы. */
  const score = rep && rep.chrf != null ? ' · ' + rep.chrf.toFixed(2) : '';
  /* Оценка с заглавной: она начинает подпись. Кириллица — потому что латинское
     «chrF (без порога)» остаётся как есть. */
  const label = rep ? rep.status.replace(/^([а-яё])/, (c) => c.toUpperCase()) : '';
  scoreEl.textContent = label + score;
  scoreEl.className = 'ru-pr-score' + (verdict === 'good' ? ' ru-good' : verdict === 'bad' ? ' ru-bad' : '');
  scoreEl.hidden = !rep;
  const rangeOut = planRowRange(row, rangeSet);
  let hint = '';
  if (!row.site && row.cands.length) {
    const best = rankCandidates(row.title, row.cands)[0];
    if (best && best.score >= TITLE_SIMILAR_MIN) hint = ` · ближе «${best.siteTitle || best.title}»`;
  }
  statusEl.textContent = (row.how || '') + hint + (rangeOut ? ' · ' + rangeOut : '');
  statusEl.title = statusEl.textContent;
  tr.className = 'ru-plan-row ' + planRowClass(row, rangeOut !== '');
}

function refreshRow(row, tr, select) {
  if (!tr) return;
  paintRow(row, tr, tr.querySelector('.ru-pr-score'), tr.querySelector('.ru-pr-status'), plan.rangeSet || null);
  if (select) select.title = row.site ? 'Глава на сайте: id=' + row.site.chapterId : 'Глава на сайте не определена — выберите';
}

/* Счётчик под таблицей: сколько строк в текущем фильтре. */
function updateFoot(shownCount) {
  const foot = $('ru-plan-foot');
  if (!foot) return;
  foot.textContent = `строк: ${shownCount != null ? shownCount : (planState.shown || 0)}`;
}

function renderPlan() {
  const box = $('ru-plan-box');
  const list = $('ru-plan-list');
  if (!plan) return;

  const onlyIssues = $('ru-plan-filter').value === 'issues';
  const rangeSet = plan.rangeSet || null;
  /* «только спорные» — строки, которые ещё требуют решения. Только что
     назначенная руками строка остаётся видимой до переключения фильтра:
     иначе выбор исчезает из-под курсора вместе с результатом. Строка вне
     диапазона в этом прогоне не пишется, поэтому в «спорные» не попадает:
     беспокоиться о ней незачем. */
  const needsDecision = (r) => planRowRange(r, rangeSet) === '' && (!r.site || (r.host && String(r.host) !== String(r.fi)));
  const shown = plan.rows.filter(r => (onlyIssues ? needsDecision(r) || touched.has(r.fi) : true));

  /* Счётчики — один проход по плану, а не пять: на семистах строках разница
     не видна, но проходов было ровно столько, сколько чисел в заголовке. */
  const c = { total: plan.rows.length, work: 0, ok: 0, ambiguous: 0, none: 0, manual: 0 };
  for (const r of plan.rows) {
    if (planRowRange(r, rangeSet) !== '') continue;
    c.work++;
    if (r.site) { if (!(r.host && String(r.host) !== String(r.fi))) c.ok++; }
    else if (r.cands.length) c.ambiguous++;
    else c.none++;
    if (r.how === 'вручную') c.manual++;
  }
  $('ru-plan-stat').textContent = `План: сопоставлено ${c.ok}, спорных ${c.ambiguous}, не найдено ${c.none}`
    + (c.manual ? `, вручную ${c.manual}` : '')
    + `, проблемных ${plan.problems.length}`
    + (rangeSet ? ` · в диапазоне ${c.work}, вне ${c.total - c.work}` : '');

  /* Все строки сразу и с полными списками глав. Порции были нужны, пока
     граммы названий считались на каждую пару «строка × глава» — это секунды
     на перерисовку. С кэшем грамм это доли секунды, а прокрутка к концу книги
     ради очередной порции — лишний шаг на каждой правке. */
  const rows = shown;
  planState.total = shown.length;
  planState.shown = rows.length;

  list.textContent = '';
  if (!rows.length) {
    const empty = document.createElement('div');
    empty.className = 'ru-plan-row ru-pr-none';
    empty.textContent = onlyIssues ? 'Строк по фильтру нет.' : 'Строк нет.';
    list.appendChild(empty);
  }

  for (const row of rows) {
    const num = document.createElement('span');
    num.className = 'ru-pr-num';
    num.textContent = row.num != null ? '№' + row.num : '—';

    const title = document.createElement('span');
    title.className = 'ru-pr-title';
    title.textContent = row.title;
    title.title = row.title;

    const arrow = document.createElement('span');
    arrow.className = 'ru-pr-arrow';
    arrow.textContent = '→';

    const cell = document.createElement('div');
    cell.className = 'ru-pr-tgt';
    /* Список и весы — одна полоса: сверка стоит справа от списка, а не
       отдельной колонкой у края строки. */
    const pick = document.createElement('div');
    pick.className = 'ru-pr-pick';
    const select = document.createElement('select');
    select.title = row.site ? 'Глава на сайте: id=' + row.site.chapterId : 'Глава на сайте не определена — выберите';
    const noneOpt = document.createElement('option');
    noneOpt.value = '';
    noneOpt.textContent = '— не выбрана —';
    select.appendChild(noneOpt);

    const cands = candOptions(row);
    const grp = document.createElement('optgroup');
    grp.label = 'кандидаты (ближайшие по названию сверху)';
    for (const c of cands) {
      const o = optionFor(c);
      if (row.site && c.id === String(row.site.chapterId)) o.selected = true;
      grp.appendChild(o);
    }
    if (cands.length) select.appendChild(grp);

    /* Список открывают — тогда и достраивается вся книга. Строить её в каждой
       из сотен строк сразу значит 363 тысячи элементов на 600 главах: столько
       не рисуется, а выбрасывается при первой же перерисовке. Кандидаты (и
       назначенная глава) уже в списке, поэтому обычный путь — выбрать из них,
       а «вся книга» нужна, только когда нужной главы среди них нет. */
    scheduleFill(select, row);

    select.onchange = () => {
      /* «Не выбрана» — тот же результат, что раньше давала кнопка «Снять»:
         строка не льётся. */
      const res = planAssign(plan, row.fi, select.value || null);
      if (!res.ok) log('Не назначено: ' + res.error, 'err');
      else {
        touched.add(row.fi);
        log(`Строка ${row.num != null ? row.num : '«' + row.title + '»'}: ${select.value ? 'глава сайта id=' + select.value : 'пропуск'} (вручную)`, 'plain');
        refreshRow(row, tr, select);
      }
    };
    const status = document.createElement('span');
    status.className = 'ru-pr-status';
    /* Результат сверки печатается в подписи под списком, рядом со статусом
       строки: измеренное относится к этой главе, а не к отдельной колонке. */
    const score = document.createElement('span');
    score.className = 'ru-pr-score';

    /* Проверка одной главы — кнопкой на строке: сверка идёт там, где виден
       результат этой строки, и не требует выделять ничего заранее. Весы
       выбраны как знак сверки двух текстов; подпись в подсказке, потому что
       иконка одна и без текста. */
    const probe = document.createElement('button');
    probe.type = 'button';
    probe.className = 'ru-pr-probe';
    probe.title = 'Сверить эту главу с переводом на сайте (chrF)';
    probe.setAttribute('aria-label', 'Сверить эту главу с переводом на сайте (chrF)');
    /* Весы: стойка, перекладина и две чаши — знак сверки двух текстов.
       Разбирается DOMParser, а не innerHTML: подстановка разметки в
       innerHTML — это путь для HTML, а нужен ровно разобранный узел (то же
       правило, что у разметки панели). */
    probe.appendChild(svgNode('<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">'
      + '<g fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round">'
      + '<path d="M8 3.6v10"/><path d="M4.3 13.6h7.4"/><path d="M1.8 3.6h12.4"/>'
      + '<path d="M3.1 3.6v2.5"/><path d="M12.9 3.6v2.5"/></g>'
      + '<path fill="currentColor" d="M.9 6.1h4.4a2.2 2.2 0 0 1-4.4 0Z"/>'
      + '<path fill="currentColor" d="M10.7 6.1h4.4a2.2 2.2 0 0 1-4.4 0Z"/></svg>'));
    probe.onclick = () => checkRow(row);

    pick.append(select, probe);
    const meta = document.createElement('div');
    meta.className = 'ru-pr-meta';
    meta.append(score, status);
    cell.append(pick, meta);

    const tr = document.createElement('div');
    tr.className = 'ru-plan-row ' + planRowClass(row, planRowRange(row, rangeSet) !== '');
    tr.append(num, title, arrow, cell);
    paintRow(row, tr, score, status, rangeSet);
    list.appendChild(tr);
  }

  const foot = document.createElement('div');
  foot.className = 'ru-plan-row ru-pr-foot';
  foot.id = 'ru-plan-foot';
  list.appendChild(foot);
  updateFoot(rows.length);
  $('ru-start').disabled = parsed.length === 0;
}

/* ---------- построение плана ---------- */
async function refreshPlan() {
  if (!parsed.length) { log('Сначала выберите .txt', 'warn'); return null; }
  const bookId = bookIdFromUrl();
  if (!bookId) { log('Не определён id книги из URL.', 'err'); return null; }
  try {
    site = await buildSiteMap(bookId);
    logSiteReport(site, ui);
    plan = buildPlan(parsed, site, parseRange($('ru-range').value));
    touched = new Set();
    reports = new Map();
    const rr = parseRangeReport($('ru-range').value);
    if (rr.invalid.length) log(`⚠ Диапазон: не разобрано «${rr.invalid.join('», «')}»`, 'warn');
    if (rr.set) log(`Диапазон в работе: ${describeRange(rr.set)}`, 'plain');
    log(`План: строк ${plan.rows.length}, спорных ${plan.rows.filter(r => !r.site && r.cands.length).length}, не найдено ${plan.rows.filter(r => !r.site && !r.cands.length).length}. Смотрите строки с цветом.`, 'ok');
    renderPlan();
    return plan;
  } catch (e) {
    log('Ошибка чтения оглавления: ' + e.message, 'err');
    return null;
  } finally {
  }
}

/* Сверка одной строки плана: читаем страницу главы, достаём сохранённый
   перевод и сравниваем по chrF. Дорого — один запрос на строку, поэтому
   запускается кнопкой на самой строке, а не всем планом сразу. */
async function checkRow(row) {
  if (!plan) { log('Сначала постройте план.', 'warn'); return; }
  if (running) { log('Идёт работа: дождитесь окончания или нажмите «Стоп».', 'warn'); return; }

  /* Проверяем ту главу, которую предлагает план: выбранную, иначе первого
     кандидата. Строку без главы сайта проверять не на чем. */
  const probe = row.site || row.cands[0];
  if (!probe) {
    reports.set(row.fi, { status: 'нечего проверять', chrf: null });
    log(`«${row.title}» — главы на сайте нет, проверять нечего (выберите её в плане).`, 'warn');
    renderPlan();
    return;
  }

  aborted = false;
  setBusy(true);
  ui.setProgress(0, 1);
  try {
    const { html, doc } = await getDoc(siteUrl(bookIdFromUrl(), probe.chapterId));
    const userId = userIdFromHtml(html);
    if (!userId) { log('Нет данных пользователя — вы залогинены?', 'err'); return; }
    const pairs = readChapterIds(doc, userId);
    if (!pairs.length) {
      reports.set(row.fi, { status: 'нет перевода', chrf: null });
      log(`«${row.title}» — нет вашего перевода на этой главе.`, 'warn');
      return;
    }
    /* Разбитая глава: прежний текст лежит абзацами по чужой разбивке, и
       chrF с файлом ничего не значит. Честнее сказать это прямо, чем
       показать низкий счёт и «НЕ похожа» на верной главе. */
    if (pairs.length > 1) {
      reports.set(row.fi, { status: `разбита на ${pairs.length} фрагментов`, chrf: null });
      log(`«${row.title}» — на сайте разбита на ${pairs.length} фрагментов: `
        + 'сверка chrF здесь не показательна, при заливке глава сведётся к одному блоку.', 'warn');
      renderPlan();
      return;
    }
    const savedText = readSavedText(doc, pairs[0].trId);
    if (savedText == null) {
      reports.set(row.fi, { status: 'на сайте пусто', chrf: null });
      log(`«${row.title}» — на сайте пусто.`, 'plain');
      return;
    }
    /* Текст главы из файла лежит в разобранном списке, а не в строке плана:
       строка плана — про сопоставление, а не про содержимое. */
    const src = (parsed[row.fi] && parsed[row.fi].text) || '';
    if (!src) {
      reports.set(row.fi, { status: 'нет текста в файле', chrf: null });
      log(`«${row.title}» — в файле нет текста этой главы, сверять нечего.`, 'warn');
      return;
    }
    const res = looksLikeSameChapter(savedText, src);
    const verdict = chrfVerdict(res.chrf);
    reports.set(row.fi, { status: verdict === 'off' ? 'chrF (без порога)' : verdict === 'good' ? 'похожа' : 'НЕ похожа', chrf: res.chrf });
    log(`«${row.title}» ↔ id=${probe.chapterId}: chrF ${res.chrf.toFixed(3)} — `
      + (verdict === 'off' ? 'порог не задан, судить нечем' : verdict === 'good' ? 'та же глава' : 'НЕ та глава'),
    verdict === 'bad' ? 'err' : 'ok');
  } catch (e) {
    /* Причина человеческим текстом: у сетевого сбоя message бывает пустым. */
    const why = (e && (e.message || e.name)) || String(e) || 'неизвестная причина';
    reports.set(row.fi, { status: 'ошибка', chrf: null });
    log(`«${row.title}» — ошибка: ${why}`, 'err');
  } finally {
    ui.setProgress(1, 1);
    setBusy(false);
    renderPlan();
  }
}

/* ---------- события таблицы ---------- */
$('ru-plan-filter').onchange = () => { touched = new Set(); renderPlan(); };

/* ---------- переключатель панели ---------- */
/* Панель — часть страницы, а не окно поверх неё: она появляется над
   таблицей глав по кнопке и уходит по повторному нажатию. Пока прогон идёт,
   кнопка не закрывает панель — иначе случайное нажатие спрячет прогресс
   и остановить будет нечем (прогон при этом продолжался бы). */
/* Одно место, где прогон начинается и заканчивается: флаг «идёт работа»
   (по нему панель не закрывается), блокировка кнопок и предупреждение в
   шапке включаются вместе — иначе одна из точек запуска про них забудет. */
function setBusy(on) {
  running = on;
  $('ru-start').disabled = on || parsed.length === 0;
  $('ru-stop').disabled = !on;
  $('ru-run-warn').hidden = !on;
  /* Предупреждение уезжает за пределы прокрутки шапки — подтягиваем к верху,
     чтобы его было видно, даже если ленту уже прокрутили вниз. */
  if (on) {
    const bd = panel.querySelector('.ru-bd');
    if (bd) bd.scrollTop = 0;
  }
}

function panelOpen() { return panel.classList.contains('ru-open'); }

function setPanelOpen(open) {
  panel.classList.toggle('ru-open', open);
  toggleBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  toggleBtn.classList.toggle('active', open);
  if (open) {
    /* План строится сразу: отдельной кнопки «План» нет, показывать
       пользователю «Плана нет — нажмите ещё что-то» значит требовать
       лишнего шага. Оглавление читается один раз и запоминается. */
    if (parsed.length && !plan) refreshPlan();
    else if (plan) renderPlan();
  }
}

toggleBtn.onclick = () => {
  if (panelOpen()) {
    if (running) { log('Идёт заливка: панель не закрывается, пока не нажмёте «Стоп».', 'warn'); return; }
    setPanelOpen(false);
    return;
  }
  setPanelOpen(true);
};

$('ru-pick').onclick = () => $('ru-file').click();

$('ru-file').addEventListener('change', (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const rd = new FileReader();
  rd.onload = () => {
    parsed = parseChaptersFile(rd.result);

    /* Дубли названий в файле не выбрасываются: главы разводятся
       номером из тега, а если номер один — порядком внутри группы.
       Иначе обе «Главы 1» исчезают из заливки целиком. */
    const sameName = new Map();
    for (const c of parsed) {
      const key = titleKey(c.title);
      if (!sameName.has(key)) sameName.set(key, []);
      sameName.get(key).push(c);
    }
    const dupNames = [...sameName.values()].filter(arr => arr.length > 1);
    if (dupNames.length) {
      log(`⚠ В ФАЙЛЕ ${dupNames.length} групп глав с одинаковым названием — разводятся по номеру/порядку:`, 'warn');
      for (const arr of dupNames) {
        log(`   · «${arr[0].title}» ×${arr.length} (номера ${arr.map(c => c.num ?? '—').join(', ')})`, 'warn');
      }
    }

    const problems = parsed.filter(c => c.problem);
    const valid = parsed.filter(c => !c.problem);
    $('ru-count').textContent = valid.length;
    $('ru-start').disabled = valid.length === 0;
    plan = null;
    site = null;
    reports = new Map();
    $('ru-plan-filter').value = 'all';
    renderPlan();
    log(`Файл «${f.name}»: глав ${parsed.length} (валидных ${valid.length}).`, valid.length ? 'ok' : 'err');
    /* План строится сразу: кнопки «План» больше нет, и человек не должен
       догадываться, что для сопоставления нужен ещё один шаг. Оглавление
       читается один раз — при повторном «Плане» оно берётся из памяти. */
    if (valid.length) refreshPlan();
    if (problems.length) {
      log(`⚠ Проблемных в файле: ${problems.length} (будут пропущены)`, 'warn');
      problems.forEach(c => log(`   · «${c.title}» — ${c.problem}`, 'warn'));
    }
    valid.slice(0, 3).forEach(c => log(`· Глава ${c.num} "${c.title}" (${c.text.length}с)`));
    if (valid.length > 3) log(`… и ещё ${valid.length - 3}`);
  };
  rd.readAsText(f, 'utf-8');
});

$('ru-stop').onclick = () => { aborted = true; log('Запрошена остановка…', 'warn'); };

/* Диапазон правится ДО запуска, и план должен отвечать на тот вопрос,
   который человек задал: поменял диапазон — перестроиться сразу, а не
   узнать о расхождении при заливке. Перестраивать план целиком незачем:
   меняется только состав работы (rangeSet), решения остаются те же. */
$('ru-range').addEventListener('input', () => {
  const rr = parseRangeReport($('ru-range').value);
  const hint = $('ru-range-hint');
  if (rr.invalid.length) { hint.textContent = 'не разобрано: ' + rr.invalid.join(', '); hint.className = 'ru-warn'; }
  else if (rr.set) { hint.textContent = 'в работе: ' + describeRange(rr.set); hint.className = 'ru-muted'; }
  else { hint.textContent = ''; }
  if (!plan) return;
  planSetRange(plan, $('ru-range').value);
  renderPlan();
});

async function launch() {
  aborted = false;
  CFG.verify = $('ru-verify').checked;
  CFG.skipSame = $('ru-skip-same').checked;
  CFG.minChrf = parseChrfThreshold($('ru-chrf').value);
  CFG.delayMs = parseInt($('ru-delay').value, 10) || 2500;
  CFG.jitterMs = parseInt($('ru-jitter').value, 10) || 0;
  CFG.titleMode = $('ru-title-mode').value;
  CFG.multiFragment = $('ru-multi-frag').checked ? 'merge' : 'skip';
  const rangeStr = $('ru-range').value.trim();
  if (plan) planSetRange(plan, rangeStr);

  /* Строку, которую лить не надо, отмечают «не выбрана» в её списке:
     отдельного выделения строк в плане больше нет. */

  setBusy(true);
  log('=== ЗАПУСК ===', 'ok');
  try {
    const res = await run(parsed, {
      dryRun: false, skipSame: CFG.skipSame, rangeStr, plan,
      /* Согласие спрашивается на первой же разбитой главе — до того, как
         удалён хоть один фрагмент. Предварительный обход всех глав стоил бы
         страницы на каждую главу книги ещё до начала работы. */
      askFragmented: (info) => askFragments(info),
    }, ui);
    if (res && res.reports) {
      reports = new Map(res.reports.map(r => [r.fi, r]));
      plan = res.plan || plan;
      renderPlan();
    }
  } finally {
    setBusy(false);
  }
}

/* Окно подтверждения свода разбитой главы.

   Возвращает обещание с ответом: 'merge' — свести, 'skip' — не трогать
   разбитые главы до конца прогона, 'stop' — прекратить работу. Закрытие
   крестиком, Esc и клик по подложке считаются отказом: согласие на удаление
   не должно получаться случайным нажатием. */
function askFragments(info) {
  return new Promise(resolve => {
    /* `$` ищет внутри панели, а окно лежит слоем в body: у него свои
       ссылки на элементы. */
    const box = document.getElementById('ru-confirm');
    const bd = document.getElementById('ru-confirm-bd');
    while (bd.firstChild) bd.removeChild(bd.firstChild);

    const p1 = document.createElement('p');
    p1.textContent = `«${info.title}» на сайте разбита на фрагменты: их ${info.count}. `
      + 'Такая глава сводится к одному блоку — текст ляжет в первый фрагмент, лишние удалятся '
      + 'вместе со своими переводами и комментариями. Отменить это нельзя.';
    bd.appendChild(p1);

    const p2 = document.createElement('p');
    p2.textContent = 'Сверка chrF и «пропуск совпадающего» на этой главе не работают: прежний текст '
      + 'лежит абзацами по чужой разбивке, и сравнивать его с файлом нечего. '
      + `Остальные главы пойдут обычным порядком. Пауза между удалениями — ${CFG.delMs} мс.`;
    bd.appendChild(p2);

    const go = document.getElementById('ru-confirm-go');
    const no = document.getElementById('ru-confirm-no');
    const skip = document.getElementById('ru-confirm-skip');
    const back = box.querySelector('.ru-cf-back');

    let done = false;
    const finish = (val) => {
      if (done) return;
      done = true;
      box.classList.remove('ru-open');
      go.onclick = null;
      no.onclick = null;
      skip.onclick = null;
      back.onclick = null;
      document.removeEventListener('keydown', onKey, true);
      resolve(val);
    };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); finish('skip'); } };
    document.addEventListener('keydown', onKey, true);
    go.onclick = () => finish('merge');
    no.onclick = () => finish('stop');
    skip.onclick = () => finish('skip');
    back.onclick = () => finish('skip');
    box.classList.add('ru-open');
  });
}

$('ru-start').onclick = () => launch();

log('Готово. Выберите файл с главами — план построится сам.', 'ok');

}  /* canEditBook */

/* Версия панели — из meta.js (сборщик подставляет {{VERSION}}): вторая
   копия номера в разметке разъезжается с релизом. */
const APP_VERSION = '{{VERSION}}';

const CFG = {
  delayMs: 2100,
  jitterMs: 0,
  intraMs: 400,
  /* Проверка после записи по умолчанию выключена: это второй проход по
     каждой главе, а сверка chrF перед записью уже отсекает чужую главу. */
  verify: false,
  skipSame: true,
  multiFragment: 'skip',
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
function chrf(a, b, nMax, beta) {
  const n = nMax || 6, b2 = (beta || 3) ** 2;
  const ga0 = String(a || '').replace(/\s+/g, '');
  const gb0 = String(b || '').replace(/\s+/g, '');
  if (!ga0.length || !gb0.length) return 0;
  let total = 0;
  for (let k = 1; k <= n; k++) {
    const ga = new Map(), gb = new Map();
    for (let i = 0; i + k <= ga0.length; i++) {
      const g = ga0.slice(i, i + k);
      ga.set(g, (ga.get(g) || 0) + 1);
    }
    for (let i = 0; i + k <= gb0.length; i++) {
      const g = gb0.slice(i, i + k);
      gb.set(g, (gb.get(g) || 0) + 1);
    }
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
  const first = (location.pathname === bookUrl || location.pathname === bookUrl + '/')
    ? document
    : (await getDoc(bookUrl)).doc;
  /* Оглавление постраничное (на сайте по 100 глав на страницу): без
     обхода пейджера главы 101+ не видны, и файл на них говорит
     «НЕ НАЙДЕНА». Номера страниц берём из ссылок пейджера, а не
     из размера DOM: сколько глав показать, решает сервер и настройка
     «Выводить главы по». */
  const pageNums = new Set();
  first.querySelectorAll('a[href*="Chapter_page="]').forEach(a => {
    const m = (a.getAttribute('href') || '').match(/Chapter_page=(\d+)/);
    if (m) pageNums.add(parseInt(m[1], 10));
  });
  const extra = [];
  for (const p of [...pageNums].sort((a, b) => a - b)) {
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
function titleScore(a, b) {
  const norm = (t) => String(t || '').normalize('NFC').toLowerCase().trim();
  return chrf(norm(a), norm(b), 3);
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

function buildPlan(parsed, site, overrides, rangeSet) {
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
  if (overrides) applyOverrides(plan, parsed, overrides);
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
    plan.manual[fi] = null;
    resolveConflicts(plan, plan.rangeSet);
    return { ok: true };
  }
  const id = String(siteId);
  const hit = (plan.site.ordered || []).find(g => String(g.chapterId) === id);
  if (!hit) return { ok: false, error: 'на сайте нет главы id=' + id };
  row.site = { url: hit.url || siteUrl(plan.bookId, id), chapterId: id, siteTitle: hit.siteTitle };
  row.how = 'вручную';
  plan.manual[fi] = id;
  resolveConflicts(plan, plan.rangeSet);
  return { ok: true };
}

/* Оверрайды: что человек решил, помнится между запусками и переживает
   перекомпиляцию файла. Ключ — номер + название главы файла: индекс строки
   после нового разбора уже другой (главы добавились или ушли). */
const overrideKey = (ch) => `${ch.num == null ? '-' : ch.num}|${titleNorm(ch.title)}`;

function applyOverrides(plan, parsed, overrides) {
  const applied = { assigned: 0, skipped: 0, lost: 0 };
  /* Что скрипт решил бы сам: если назначение из файла с этим совпадает, это
     не «ручная правка» — иначе после каждого импорта весь план выглядит
     правленым руками и перестаёт что-либо сообщать. */
  const auto = planMatches(parsed, plan.site);
  const autoId = (fi) => {
    const b = auto.get(fi);
    return b && b.info ? String(b.info.chapterId) : null;
  };
  for (const row of plan.rows) {
    const key = overrideKey(parsed[row.fi]);
    if (!Object.prototype.hasOwnProperty.call(overrides, key)) continue;
    const id = overrides[key];
    if (id == null) {
      row.site = null;
      row.how = 'пропуск (ваше решение)';
      plan.manual[row.fi] = null;
      applied.skipped++;
      continue;
    }
    const hit = (plan.site.ordered || []).find(g => String(g.chapterId) === String(id));
    if (!hit) { applied.lost++; continue; }
    row.site = { url: hit.url || siteUrl(plan.bookId, String(id)), chapterId: String(id), siteTitle: hit.siteTitle };
    row.how = autoId(row.fi) === String(id) ? 'из плана' : 'вручную';
    plan.manual[row.fi] = String(id);
    applied.assigned++;
  }
  resolveConflicts(plan, plan.rangeSet);
  return applied;
}

function collectOverrides(plan, parsed) {
  const out = {};
  for (const fi of Object.keys(plan.manual)) {
    const row = planRow(plan, Number(fi));
    if (!row) continue;
    const id = plan.manual[fi];
    out[overrideKey(parsed[row.fi])] = id == null ? null : String(id);
  }
  return out;
}


/* «Сбросить»: вернуть строку к тому, что решил бы скрипт сам. Решение берём
   у planMatches — второго алгоритма сопоставления быть не должно, иначе
   «сбросить» и «План» начнут расходиться. */
function planResetSelected(plan, parsed, fis) {
  const base = planMatches(parsed, plan.site);
  for (const fi of fis) {
    const row = planRow(plan, fi);
    if (!row) continue;
    delete plan.manual[row.fi];
    const b = base.get(row.fi);
    if (b && b.info) {
      row.site = b.info;
      row.cands = [];
      row.how = b.how || 'по номеру';
    } else {
      row.site = null;
      row.cands = (b && b.list) || [];
      row.how = b && b.list ? 'спорная' : 'нет';
    }
  }
  resolveConflicts(plan, plan.rangeSet);
  return fis.length;
}

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
   была бы игрушечной. `opts.onlyFi` — залить только эти строки (выделение),
   `opts.skipSame` — пропускать совпадающее, `opts.rangeStr` — диапазон номеров.

   Диапазон режет работу, а не молча не находит главу: строка вне диапазона
   не идёт ни на страницу сайта, ни в счёт «не найдено». Проверка стоит
   ДО разбора спорной строки: иначе глава вне диапазона попадала бы
   в «неоднозначно» (а спорной она не бывает — её всё равно не льют),
   а глава без номера не проверялась бы вовсе. */
async function writePlan(plan, parsed, opts, ui) {
  const rangeSet = opts.rangeStr === undefined ? (plan.rangeSet || null) : parseRange(opts.rangeStr);
  const rangeReport = opts.rangeStr === undefined ? null : parseRangeReport(opts.rangeStr);
  const only = opts.onlyFi ? new Set(opts.onlyFi) : null;
  const entries = plan.order.map(fi => planRow(plan, fi)).filter(Boolean)
    .filter(r => !only || only.has(r.fi));
  const total = entries.length;
  const st = { saved: 0, wouldSave: 0, skipSame: 0, skipOther: 0, skipRange: 0, skipProblem: 0, fail: 0, titleChanged: 0, ambiguous: 0 };
  const notFoundNums = [];
  const notFoundTitles = [];
  const reports = [];
  const report = (row, status, chrf) => reports.push({ fi: row.fi, status, chrf: chrf == null ? null : chrf });

  if (rangeReport && rangeReport.invalid.length) {
    ui.log(`⚠ Диапазон: не разобрано «${rangeReport.invalid.join('», «')}» — работают только номера и интервалы.`, 'warn');
  }
  if (rangeSet) ui.log(`Диапазон в работе: ${describeRange(rangeSet)}`, 'plain');

  if (!only) {
    st.skipProblem = plan.problems.length;
    if (plan.problems.length) ui.log(`Проблемных в файле: ${plan.problems.length} (пропущены)`, 'warn');
  }

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
      if (pairs.length > 1 && CFG.multiFragment === 'skip') { st.skipOther++; ui.log(`${tick} «${ch.title}» — фрагментов ${pairs.length} (>1), пропуск.`, 'warn'); report(row, 'несколько фрагментов'); continue; }

      const { origId, trId } = pairs[0];
      ui.log(`${tick} «${ch.title}» ↔ «${siteTitle}» | chap=${info.chapterId} orig=${origId} tr=${trId} | ${ch.text.length}с`, 'plain');

      const savedText = readSavedText(doc, trId);
      const same = compareAndLog(savedText, ch.text, ui);
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
          await saveTranslation(plan.bookId, info.chapterId, origId, trId, ch.text, csrf);
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
    : `=== ГОТОВО: залито ${st.saved}, названий ${st.titleChanged}, пропущено ${st.skipSame}, не найдено ${st.skipOther}, неоднозначных ${st.ambiguous}, вне диапазона ${st.skipRange}, проблемных ${st.skipProblem}, ошибок ${st.fail} ===`;
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
  ui.log(`Названия: ${tmLabel} | Пауза: ${CFG.delayMs}мс ± ${CFG.jitterMs}мс`, 'plain');
  ui.log(opts.dryRun ? 'Режим: DRY-RUN' : (opts.skipSame ? 'Режим: совпадающие ПРОПУСКАЮТСЯ (текст и названия).' : 'Режим: ПРИОРИТЕТ ФАЙЛА — льётся КАЖДАЯ найденная глава.'), 'plain');

  let plan = opts.plan || null;
  if (!plan) {
    let site;
    try { site = await buildSiteMap(bookId); }
    catch (e) { ui.log('Ошибка чтения оглавления: ' + e.message, 'err'); return { error: 'оглавление' }; }
    logSiteReport(site, ui);
    plan = buildPlan(parsed, site, null, parseRange(opts.rangeStr));
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

/* ---------- UI ---------- */
GM_addStyle(`#ru-bulk, #ru-bulk * { box-sizing: border-box !important; } #ru-bulk { position: fixed; right: 12px; bottom: 12px; z-index: 999999; width: min(680px, calc(100vw - 24px)); max-height: calc(100vh - 24px); display: flex; flex-direction: column; overflow: hidden; text-align: left !important; background: #1f2430; color: #e6e6e6; border: 1px solid #3a4150; border-radius: 10px; font: 13px/1.4 system-ui, -apple-system, Segoe UI, Roboto, sans-serif !important; box-shadow: 0 8px 28px rgba(0,0,0,.45); margin: 0; padding: 0; } #ru-bulk .ru-hd { flex: 0 0 auto; padding: 8px 10px; background: #2a3142; border-radius: 10px 10px 0 0; display: flex; justify-content: space-between; align-items: center; gap: 8px; cursor: grab; user-select: none; margin: 0; } #ru-bulk .ru-hd:active { cursor: grabbing; } #ru-bulk .ru-hd b { font-size: 13px; font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin: 0; padding: 0; } #ru-bulk .ru-bd { flex: 1 1 auto; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 10px; margin: 0; } #ru-bulk .ru-grip { position: absolute; right: 0; bottom: 0; width: 16px; height: 16px; cursor: nwse-resize; touch-action: none; background: linear-gradient(135deg, transparent 0 45%, #55607a 45% 55%, transparent 55% 70%, #55607a 70% 80%, transparent 80%); border-bottom-right-radius: 10px; z-index: 2; } #ru-bulk .ru-grip-nw { position: absolute; left: 0; top: 0; width: 16px; height: 16px; cursor: nwse-resize; touch-action: none; background: linear-gradient(315deg, transparent 0 45%, #55607a 45% 55%, transparent 55% 70%, #55607a 70% 80%, transparent 80%); border-top-left-radius: 10px; z-index: 2; } #ru-bulk .ru-hd-btns { display: flex; gap: 4px; align-items: center; } #ru-bulk button#ru-close:hover { background: #d9534f !important; } #ru-bulk button { background: #2f81c4; color: #fff; border: 0; border-radius: 5px; padding: 6px 11px; cursor: pointer; margin: 0; font: inherit; user-select: none; white-space: nowrap; } #ru-bulk button.ru-sec { background: #444c5c; } #ru-bulk button.ru-go { background: #2e9e5b; } #ru-bulk button:hover { filter: brightness(1.08); } #ru-bulk button:disabled { opacity: .45; cursor: not-allowed; filter: none; } #ru-bulk .ru-log-box { min-height: 120px; max-height: 40vh; overflow-x: hidden !important; overflow-y: auto !important; background: #11151c; border: 1px solid #333; border-radius: 6px; padding: 7px !important; margin: 8px 0 0 0 !important; font: 11.5px/1.4 ui-monospace, Menlo, Consolas, monospace !important; white-space: pre-wrap !important; word-break: break-word !important; text-align: left !important; } #ru-bulk .ru-log-box span { margin: 0 !important; padding: 0 !important; display: inline; } #ru-bulk .ru-bar { height: 6px; background: #333; border-radius: 3px; margin: 8px 0 0 0; overflow: hidden; } #ru-bulk .ru-bar > i { display: block; height: 100%; width: 0; background: #37c46a; transition: width .2s; } #ru-bulk .ru-row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 6px 0 0 0 !important; padding: 0 !important; width: 100% !important; } #ru-bulk .ru-row:first-child { margin-top: 0 !important; } #ru-bulk .ru-ok { color: #7fe39a !important; } #ru-bulk .ru-err { color: #ff8585 !important; } #ru-bulk .ru-warn { color: #ffd166 !important; } #ru-bulk .ru-plain { color: #9aa3b2 !important; } #ru-bulk label { display: inline-flex; gap: 5px; align-items: center; user-select: none; cursor: pointer; white-space: nowrap; margin: 0 !important; font-weight: normal !important; } #ru-bulk input[type=number], #ru-bulk input[type=text] { width: 70px; background: #11151c; color: #e6e6e6; border: 1px solid #3a4150; border-radius: 4px; padding: 3px 5px; font: inherit; margin: 0; outline: none; } #ru-bulk input[type=text].ru-wide { width: 130px; } #ru-bulk select { background: #11151c; color: #e6e6e6; border: 1px solid #3a4150; border-radius: 4px; padding: 3px 5px; font: inherit; margin: 0; } #ru-bulk input[type=checkbox] { margin: 0 !important; transform: scale(1.1); } #ru-bulk .ru-muted { color: #9aa3b2; } #ru-bulk .ru-sep { border-top: 1px solid #3a4150; margin: 8px 0 !important; padding: 0 !important; } #ru-bulk .ru-plan-box { margin: 8px 0 0 0 !important; } #ru-bulk .ru-plan-title { margin: 0 !important; } #ru-bulk .ru-plan-title b { font-weight: 600; } #ru-bulk .ru-plan-hd { gap: 8px; } #ru-bulk .ru-plan-hd label { white-space: nowrap; } #ru-bulk .ru-plan-list { max-height: 46vh; overflow: auto; background: #11151c; border: 1px solid #333; border-radius: 6px; margin: 6px 0 0 0 !important; font: 11.5px/1.35 ui-monospace, Menlo, Consolas, monospace !important; } #ru-bulk .ru-plan-row { display: flex; flex-wrap: wrap; gap: 4px 6px; align-items: center; padding: 3px 6px; border-bottom: 1px solid #232936; } #ru-bulk .ru-plan-row:last-child { border-bottom: 0; } #ru-bulk .ru-plan-row.ru-pr-out { opacity: .45; } #ru-bulk .ru-plan-row.ru-pr-issue { background: #2a2113; } #ru-bulk .ru-plan-row.ru-pr-none { background: #2a1717; } #ru-bulk .ru-plan-row.ru-pr-manual { background: #12242a; } #ru-bulk .ru-pr-num { flex: 0 0 42px; color: #9aa3b2; text-align: right; } #ru-bulk .ru-pr-title { flex: 1 1 46%; min-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } #ru-bulk .ru-pr-arrow { flex: 0 0 auto; color: #6b7686; } #ru-bulk .ru-pr-sel { flex: 1 1 38%; min-width: 120px; max-width: 100%; } #ru-bulk .ru-pr-chrf { flex: 0 0 46px; text-align: right; color: #9aa3b2; } #ru-bulk .ru-pr-chrf.ru-bad { color: #ff8585; } #ru-bulk .ru-pr-chrf.ru-good { color: #7fe39a; } #ru-bulk .ru-pr-status { flex: 0 0 auto; margin-left: auto; color: #7f8b9c; font-size: 10.5px; }`);

const panel = document.createElement('div');
panel.id = 'ru-bulk';
/* Разметку панели разбирает DOMParser, а не innerHTML: подстановка шаблона в
   innerHTML — это путь для HTML, а нужен ровно разобранный DOM (и статические
   значения CFG здесь единственные данные, но правило одно на весь файл). */
const panelMarkup = `<div class="ru-hd"> <b>RL · обновление глав v${APP_VERSION}</b> <div class="ru-hd-btns"> <button class="ru-sec" id="ru-min" title="Свернуть">—</button> <button class="ru-sec" id="ru-close" title="Закрыть">✕</button> </div> </div> <div class="ru-bd"> <div class="ru-row"> <input type="file" id="ru-file" accept=".txt,text/plain" style="display:none"> <button id="ru-pick">Выбрать .txt</button> <button id="ru-dry" class="ru-sec" disabled>dry-run</button> <button id="ru-start" class="ru-go" disabled>Старт</button> <button id="ru-stop" class="ru-sec" disabled>Стоп</button> <button id="ru-log-dl" class="ru-sec">Лог</button> <button id="ru-plan" class="ru-sec" disabled title="Показать сопоставление глав файла с главами сайта — с правкой вручную">План</button> </div> <div class="ru-sep"></div> <div class="ru-row"> <span class="ru-muted">Глав:</span> <b id="ru-count">0</b> <span class="ru-muted" style="margin-left:8px">Диапазон:</span> <input id="ru-range" type="text" class="ru-wide" placeholder="1-50, 55" title="Диапазон: 1-50, 55, 60-70. Пусто = все"> <span class="ru-muted" id="ru-range-hint"></span> </div> <div class="ru-plan-box" id="ru-plan-box" style="display:none"> <div class="ru-row ru-plan-title"><b id="ru-plan-stat">Плана нет</b></div> <div class="ru-row ru-plan-hd"> <label title="Показать только спорные и ненайденные строки"><input type="checkbox" id="ru-plan-issues"> только спорные</label> <button class="ru-sec" id="ru-plan-reset" title="Снять ручные правки с выделенных строк и вернуть решение скрипта">Сбросить</button> <button class="ru-sec" id="ru-plan-check" title="Прочитать выделенные строки и сверить текст (chrF)">Проверить</button> </div> <div class="ru-plan-list" id="ru-plan-list"></div> </div> <div class="ru-sep"></div> <div class="ru-row"> <span class="ru-muted">Пауза (мс):</span> <input id="ru-delay" type="number" value="${CFG.delayMs}" min="100" step="100"> <span class="ru-muted" style="margin-left:6px">Рандом ±:</span> <input id="ru-jitter" type="number" value="${CFG.jitterMs}" min="0" step="50"> <span class="ru-muted" style="margin-left:6px" title="Порог сверки главы с уже сохранённым переводом на сайте: 0.75 — обычный, 0 — сверка выключена">chrF:</span> <input id="ru-chrf" type="number" value="${CFG.minChrf}" min="0" max="1" step="0.05" title="Порог сверки главы с уже сохранённым переводом на сайте. 1 — тексты обязаны совпасть почти дословно, 0.75 — обычный порог (мелкие правки проходят, чужая глава нет), 0 или пусто — сверка выключена."> </div> <div class="ru-row"> <label title="Перечитать главу после записи и сверить текст"><input type="checkbox" id="ru-verify" ${CFG.verify ? 'checked' : ''}> Проверять результат</label> <label title="Не лить главы, чей текст и название уже совпадают"><input type="checkbox" id="ru-skip-same" ${CFG.skipSame ? 'checked' : ''}> Пропуск совп.</label> </div> <div class="ru-row"> <span class="ru-muted">Названия глав:</span> <select id="ru-title-mode"> <option value="off">Не обновлять</option> <option value="title-only">Обновлять названия</option> <option value="title-and-body">Обновлять названия + текст</option> </select> </div> <div class="ru-bar"><i id="ru-bar-fill"></i></div> <div class="ru-log-box" id="ru-log"></div> </div> <div class="ru-grip ru-grip-nw" id="ru-grip-nw" title="Потяните за угол: размер и положение"></div> <div class="ru-grip" id="ru-grip" title="Потяните, чтобы изменить размер панели"></div>`;
{
  const tplDoc = new DOMParser().parseFromString('<body>' + panelMarkup + '</body>', 'text/html');
  for (const node of [...tplDoc.body.childNodes]) panel.appendChild(document.adoptNode(node));
}

document.body.appendChild(panel);

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
let selected = new Set();   // fi выделенных строк
let touched = new Set();    // fi строк, назначенных вручную в этом проходе
let reports = new Map();    // fi → {status, chrf} из последнего прогона
const ui = {
  log,
  get aborted() { return aborted; },
  setProgress: (i, n) => { $('ru-bar-fill').style.width = Math.round(i / Math.max(1, n) * 100) + '%'; },
};

/* План живёт в браузере для этой книги: перезагрузка страницы Rulate не
   должна забирать разбор шестисот строк. Ключ — id книги, не URL панели. */
const storeKey = () => 'rlPlan:' + (bookIdFromUrl() || '');
function savePlanLocal() {
  if (!plan) return;
  try {
    localStorage.setItem(storeKey(), JSON.stringify({
      data: planToData(plan, parsed),
      manual: collectOverrides(plan, parsed),
      source: parsed.map(c => c.title),
    }));
  } catch (e) { /* переполнилось хранилище — не повод падать */ }
}
function loadPlanLocal() {
  try {
    const raw = localStorage.getItem(storeKey());
    return raw ? JSON.parse(raw) : null;
  } catch (e) { return null; }
}

/* ---------- таблица плана ---------- */
const allSiteOptions = () => (site ? site.ordered : []).map(g => ({ id: String(g.chapterId), title: g.siteTitle, url: g.url }));

/* Первые варианты строки: выбранная глава и кандидаты по номеру, ближайшие
   по названию сверху.

   Полный список книги здесь сознательно НЕ строится: `<option>` на каждую
   главу в каждой строке — это (строки × главы) элементов, у книги на 600
   глав их выходило 360 тысяч, и один клик по чекбоксу занимал семь секунд.
   Весь список появляется при открытии конкретного `<select>` (fillSelect). */
function candOptions(row) {
  const out = [];
  const seen = new Set();
  const push = (g, score) => {
    const id = String(g.chapterId != null ? g.chapterId : g.id);
    if (seen.has(id)) return;
    seen.add(id);
    out.push({ id, title: g.siteTitle != null ? g.siteTitle : g.title, score });
  };
  if (row.site) push({ chapterId: row.site.chapterId, siteTitle: row.site.siteTitle });
  for (const c of rankCandidates(row.title, row.cands)) push(c, c.score);
  return out;
}

/* Весь список книги в этом `<select>`: строится один раз, при первом
   раскрытии. Порядок — как в оглавлении; уже предложенные варианты не
   дублируются. Соседи по оглавлению поднимаются наверх: когда нумерация
   разъехалась, верная глава обычно рядом. */
function fillSelect(select, row) {
  if (select.dataset.filled) return;
  select.dataset.filled = '1';
  const all = allSiteOptions();
  const have = new Set([...select.querySelectorAll('option')].map(o => o.value));
  const anchor = row.site ? all.findIndex(g => g.id === String(row.site.chapterId)) : -1;
  const near = [];
  for (const d of [1, -1, 2, -2]) if (anchor >= 0 && all[anchor + d] && !have.has(all[anchor + d].id)) near.push([all[anchor + d], d > 0 ? 'соседняя дальше' : 'соседняя выше']);
  if (near.length) {
    const g = document.createElement('optgroup');
    g.label = 'рядом по оглавлению';
    for (const [it] of near) g.appendChild(optionFor({ id: it.id, title: it.title }));
    select.appendChild(g);
  }
  const gAll = document.createElement('optgroup');
  gAll.label = `вся книга (${all.length})`;
  for (const it of all) if (!have.has(it.id)) gAll.appendChild(optionFor({ id: it.id, title: it.title }));
  select.appendChild(gAll);
}

function optionFor(c) {
  const o = document.createElement('option');
  o.value = c.id;
  const sc = c.score != null && c.score >= TITLE_SIMILAR_MIN ? ` · ${c.score.toFixed(2)}` : '';
  o.textContent = `«${c.title}» · id=${c.id}${sc}`;
  if (c.score != null) o.style.color = c.score >= 0.85 ? '#7fe39a' : c.score >= 0.65 ? '#ffd166' : '';
  return o;
}

function planRowClass(row, rangeOut) {
  if (rangeOut) return 'ru-pr-out';
  if (row.how === 'вручную') return 'ru-pr-manual';
  if (!row.site) return row.cands.length ? 'ru-pr-issue' : 'ru-pr-none';
  return row.host && String(row.host) !== String(row.fi) ? 'ru-pr-issue' : '';
}

/* Состояние отрисовки: сколько строк материализовано и до какого предела.
   Порция растёт по прокрутке; больше limit не строим — иначе «показать все»
   на книге в тысячу глав снова упрётся в те же секунды. */
const planState = { limit: 60, PAGE: 60, open: false };

/* Покраска одной строки: статус, chrF и цвет. Отдельно от построения DOM,
   потому что после ручного выбора меняется ровно эта строка. */
function paintRow(row, tr, chrfEl, statusEl, rangeSet) {
  const rep = reports.get(row.fi);
  const verdict = rep && rep.chrf != null ? chrfVerdict(rep.chrf) : 'off';
  chrfEl.textContent = rep && rep.chrf != null ? rep.chrf.toFixed(2) : '';
  chrfEl.className = 'ru-pr-chrf' + (verdict === 'good' ? ' ru-good' : verdict === 'bad' ? ' ru-bad' : '');
  const rangeOut = planRowRange(row, rangeSet);
  let hint = '';
  if (!row.site && row.cands.length) {
    const best = rankCandidates(row.title, row.cands)[0];
    if (best && best.score >= TITLE_SIMILAR_MIN) hint = ` · ближе «${best.siteTitle || best.title}»`;
  }
  statusEl.textContent = (rep ? rep.status : row.how) + hint + (rangeOut ? ' · ' + rangeOut : '');
  statusEl.title = statusEl.textContent;
  tr.className = 'ru-plan-row ' + planRowClass(row, rangeOut !== '');
}

function refreshRow(row, tr, select) {
  if (!tr) return;
  paintRow(row, tr, tr.querySelector('.ru-pr-chrf'), tr.querySelector('.ru-pr-status'), plan.rangeSet || null);
  if (select) select.title = row.site ? 'Глава на сайте: id=' + row.site.chapterId : 'Глава на сайте не определена — выберите';
}

/* Счётчик под таблицей: сколько показано, сколько всего и сколько выделено.
   Клик по нему показывает все строки разом — осознанное действие, а не
   побочный эффект открытия панели. */
function updateFoot(shownCount) {
  const foot = $('ru-plan-foot');
  if (!foot) return;
  const shown = shownCount != null ? shownCount : (planState.shown || 0);
  const more = planState.total != null ? Math.max(0, planState.total - shown) : 0;
  foot.textContent = `показано ${shown} из ${planState.total != null ? planState.total : shown} строк`
    + (more ? ` · ещё ${more} — потяните прокрутку` : '')
    + ` · выделено ${selected.size}`;
  foot.title = more ? 'Показать все строки сразу' : '';
  foot.onclick = more ? () => { planState.limit = 0; renderPlan(); } : null;
}

function renderPlan() {
  const box = $('ru-plan-box');
  const list = $('ru-plan-list');
  if (!plan || !planState.open) { box.style.display = 'none'; return; }
  box.style.display = '';

  const onlyIssues = $('ru-plan-issues').checked;
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

  /* Список рисуется порциями: у книги бывает больше тысячи глав, и построить
     их все разом — это секунды на каждое нажатие. Видимые строки появляются
     сразу, остальные — по прокрутке (или целиком по клику на счётчик). */
  const limit = planState.limit;
  const rows = limit ? shown.slice(0, limit) : shown;
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
    const sel = document.createElement('input');
    sel.type = 'checkbox';
    sel.checked = selected.has(row.fi);
    sel.title = 'Выделить строку (для «Проверить» и счётчика)';
    /* Клик по чекбоксу не перерисовывает таблицу: перерисовка снесла бы
       DOM под курсором и на семистах строках стоила бы секунды. Меняются
       только счётчик и собственная строка. */
    sel.onchange = () => {
      if (sel.checked) selected.add(row.fi); else selected.delete(row.fi);
      updateFoot();
    };

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

    const cell = document.createElement('span');
    cell.className = 'ru-pr-sel';
    const select = document.createElement('select');
    select.className = 'ru-pr-sel';
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
    /* Ленивое дополнение: до первого раскрытия в списке только кандидаты.
       Раскрыли — появился весь оглавление книги (fillSelect). */
    let filled = false;
    select.addEventListener('pointerdown', () => { fillSelect(select, row); filled = true; });
    select.addEventListener('keydown', (e) => { if (!filled && (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ')) { fillSelect(select, row); filled = true; } });
    select.onchange = () => {
      /* «Не выбрана» — тот же результат, что раньше давала кнопка «Снять»:
         строка не льётся. */
      const res = planAssign(plan, row.fi, select.value || null);
      if (!res.ok) log('Не назначено: ' + res.error, 'err');
      else {
        touched.add(row.fi);
        log(`Строка ${row.num != null ? row.num : '«' + row.title + '»'}: ${select.value ? 'глава сайта id=' + select.value : 'пропуск'} (вручную)`, 'plain');
        savePlanLocal();
        refreshRow(row, tr, select);
      }
    };
    cell.appendChild(select);

    const chrf = document.createElement('span');
    chrf.className = 'ru-pr-chrf';

    const status = document.createElement('span');
    status.className = 'ru-pr-status';

    const tr = document.createElement('div');
    tr.className = 'ru-plan-row ' + planRowClass(row, planRowRange(row, rangeSet) !== '');
    tr.append(sel, title, num, arrow, cell, chrf, status);
    paintRow(row, tr, chrf, status, rangeSet);
    list.appendChild(tr);
  }

  const foot = document.createElement('div');
  foot.className = 'ru-plan-row ru-pr-foot';
  foot.id = 'ru-plan-foot';
  list.appendChild(foot);
  updateFoot(rows.length);
  $('ru-start').disabled = $('ru-dry').disabled = parsed.length === 0;
}

/* Прокрутка до низа списка дорисовывает следующую порцию: страница
   открывается быстро, а до конца книги человек доходит сам. Слушатель один
   на список и живёт вне renderPlan — иначе их накапливалось бы по одному на
   каждую перерисовку. */
$('ru-plan-list').addEventListener('scroll', (e) => {
  const el = e.currentTarget;
  if (!planState.limit || planState.shown >= planState.total) return;
  if (el.scrollTop + el.clientHeight < el.scrollHeight - 80) return;
  planState.limit = planState.shown + planState.PAGE;
  renderPlan();
});

/* ---------- построение плана ---------- */
async function refreshPlan() {
  if (!parsed.length) { log('Сначала выберите .txt', 'warn'); return null; }
  const bookId = bookIdFromUrl();
  if (!bookId) { log('Не определён id книги из URL.', 'err'); return null; }
  $('ru-plan').disabled = true;
  try {
    site = await buildSiteMap(bookId);
    logSiteReport(site, ui);
    const saved = loadPlanLocal();
    const overrides = saved && saved.manual ? saved.manual : null;
    plan = buildPlan(parsed, site, overrides, parseRange($('ru-range').value));
    selected = new Set();
    touched = new Set();
    reports = new Map();
    if (overrides && Object.keys(overrides).length) {
      const applied = plan.rows.filter(r => r.how === 'вручную' || r.how.startsWith('пропуск (ваше')).length;
      log(`Восстановлено ваших решений из прошлого раза: ${applied} из ${Object.keys(overrides).length}`, 'plain');
    }
    const rr = parseRangeReport($('ru-range').value);
    if (rr.invalid.length) log(`⚠ Диапазон: не разобрано «${rr.invalid.join('», «')}»`, 'warn');
    if (rr.set) log(`Диапазон в работе: ${describeRange(rr.set)}`, 'plain');
    log(`План: строк ${plan.rows.length}, спорных ${plan.rows.filter(r => !r.site && r.cands.length).length}, не найдено ${plan.rows.filter(r => !r.site && !r.cands.length).length}. Смотрите строки с цветом.`, 'ok');
    savePlanLocal();
    renderPlan();
    return plan;
  } catch (e) {
    log('Ошибка чтения оглавления: ' + e.message, 'err');
    return null;
  } finally {
    $('ru-plan').disabled = false;
  }
}

/* Проверка выделенных (или всех спорных): читаем страницу главы, сверяем
   сохранённый перевод через chrF. Дорого — по запросу на строку, поэтому
   только по кнопке и с прогрессом. */
async function checkRows() {
  if (!plan) { log('Сначала постройте план.', 'warn'); return; }
  const rangeSet = plan.rangeSet || null;
  const inWork = (r) => planRowRange(r, rangeSet) === '';
  let targets = plan.rows.filter(r => selected.has(r.fi) && inWork(r));
  if (!targets.length) targets = plan.rows.filter(r => !r.site || (r.host && String(r.host) !== String(r.fi))).filter(inWork);
  if (!targets.length) { log('Нечего проверять: спорных строк нет. Выделите строки в панели.', 'warn'); return; }
  log(`=== ПРОВЕРКА ${targets.length} строк (chrF) ===`, 'ok');
  aborted = false;
  $('ru-start').disabled = $('ru-dry').disabled = true;
  $('ru-stop').disabled = false;

  for (let i = 0; i < targets.length; i++) {
    if (aborted) { log('Проверка остановлена.', 'warn'); break; }
    const row = targets[i];
    ui.setProgress(i, targets.length);
    const ch = parsed[row.fi];
    try {
      /* Проверяем ту главу, которую предлагает план: выбранную, иначе первого
         кандидата по номеру. Строка «не найдена» проверке не подлежит. */
      const probe = row.site || row.cands[0];
      if (!probe) { reports.set(row.fi, { status: 'нечего проверять', chrf: null }); continue; }
      const { html, doc } = await getDoc(siteUrl(bookIdFromUrl(), probe.chapterId));
      await globalPause(CFG.intraMs, 0);
      const userId = userIdFromHtml(html);
      if (!userId) { log('Нет данных пользователя — вы залогинены?', 'err'); break; }
      const pairs = readChapterIds(doc, userId);
      if (!pairs.length) { reports.set(row.fi, { status: 'нет перевода', chrf: null }); log(`[${i + 1}/${targets.length}] «${ch.title}» — нет вашего перевода.`, 'warn'); continue; }
      const savedText = readSavedText(doc, pairs[0].trId);
      if (savedText == null) { reports.set(row.fi, { status: 'на сайте пусто', chrf: null }); log(`[${i + 1}/${targets.length}] «${ch.title}» — на сайте пусто.`, 'plain'); continue; }
      const res = looksLikeSameChapter(savedText, ch.text);
      const verdict = chrfVerdict(res.chrf);
      reports.set(row.fi, { status: verdict === 'off' ? 'chrF (без порога)' : verdict === 'good' ? 'похожа' : 'НЕ похожа', chrf: res.chrf });
      log(`[${i + 1}/${targets.length}] «${ch.title}» ↔ id=${probe.chapterId}: chrF ${res.chrf.toFixed(3)} — ${verdict === 'off' ? 'порог не задан, судить нечем' : verdict === 'good' ? 'та же глава' : 'НЕ та глава'}`, verdict === 'bad' ? 'err' : 'ok');
      renderPlan();
    } catch (e) {
      reports.set(row.fi, { status: 'ошибка', chrf: null });
      log(`[${i + 1}/${targets.length}] «${ch.title}» — ошибка: ${e.message}`, 'err');
    }
    await globalPause(CFG.dryMs, 0);
  }
  ui.setProgress(targets.length, targets.length);
  log('=== ПРОВЕРКА ЗАВЕРШЕНА ===', 'ok');
  $('ru-stop').disabled = true;
  $('ru-start').disabled = $('ru-dry').disabled = parsed.length === 0;
  renderPlan();
}

/* ---------- события таблицы ---------- */
/* «План» — переключатель: первое нажатие строит план, второе его прячет.
   Повторная сборка не нужна ни при скрытии, ни при показе — оглавление уже
   прочитано, а ручные правки живут в самом плане. */
$('ru-plan').onclick = () => {
  if (plan && planState.open) { planState.open = false; renderPlan(); return; }
  planState.open = true;
  if (!plan) refreshPlan(); else renderPlan();
};
$('ru-plan-issues').onchange = () => { touched = new Set(); renderPlan(); };
$('ru-plan-check').onclick = () => checkRows();
$('ru-plan-reset').onclick = () => {
  if (!plan || !selected.size) { log('Выделите строки в таблице.', 'warn'); return; }
  const n = planResetSelected(plan, parsed, [...selected]);
  log(`Возвращено к решению скрипта: ${n}`, 'ok');
  savePlanLocal();
  renderPlan();
};

/* ---------- панель: перетаскивание, размер и память панели ---------- */
/* Размер и положение — предпочтения УСТРОЙСТВА, а не книги: панель
   раскрывают под свой экран, и на узком ноутбуке она не должна открываться
   в размере монитора. Ключ один на устройство, не по книгам. */
const UI_KEY = 'rlUi';

function readUi() {
  try { const raw = localStorage.getItem(UI_KEY); return raw ? JSON.parse(raw) : {}; } catch (e) { return {}; }
}

/* Размер возвращается в границы экрана: окно вчера могло быть шире.
   Минимум — чтобы в панели жили кнопки и таблица, максимум — вьюпорт. */
const UI_MIN_W = 420, UI_MIN_H = 240;

function clampPanel(box) {
  const vw = window.innerWidth, vh = window.innerHeight;
  /* На узком экране минимум не может быть больше экрана: иначе панель шире
     окна, и до её правого края (с кнопками) не добраться ничем. */
  const minW = Math.min(UI_MIN_W, vw - 8);
  const minH = Math.min(UI_MIN_H, vh - 8);
  const w = Math.max(minW, Math.min(vw - 8, box.w || 0));
  const h = Math.max(minH, Math.min(vh - 8, box.h || 0));
  return { w: Math.round(w), h: Math.round(h) };
}

function applyUi() {
  const saved = readUi();
  const size = clampPanel(saved.size || { w: panel.offsetWidth, h: panel.offsetHeight });
  panel.style.width = size.w + 'px';
  /* Свёрнутая панель — одна шапка: высоту ей задаёт содержимое, иначе под
     шапкой осталось бы пустое поле — ровно то, что сворачивали. */
  panel.style.height = panel.classList.contains('ru-min') ? '' : size.h + 'px';
  /* Положение хранится только если панель утащили от угла: по умолчанию
     она прижата вправо-вниз и сама переживает смену размера окна. */
  if (!saved.pos) return;
  const vw = window.innerWidth, vh = window.innerHeight;
  const x = Math.max(0, Math.min(vw - size.w, saved.pos.x));
  const y = Math.max(0, Math.min(vh - size.h, saved.pos.y));
  panel.style.left = x + 'px';
  panel.style.top = y + 'px';
  panel.style.right = 'auto'; panel.style.bottom = 'auto';
}

function saveUi(patch) {
  try { localStorage.setItem(UI_KEY, JSON.stringify(Object.assign(readUi(), patch))); } catch (e) { /* хранилище переполнено — не повод падать */ }
}

applyUi();

/* Окно уменьшили — панель поджимается за ним, а не остаётся вчерашних
   размеров за краем: обработчик только читает, запись делает человек. */
window.addEventListener('resize', () => {
  if (!readUi().size && !readUi().pos) return;
  applyUi();
});

/* Перетаскивание за заголовок и изменение размера за любой из двух углов —
   одним указателем: pointermove работает и мышью, и на тач-экране, а
   setPointerCapture не даёт событию потеряться за пределами панели.

   Углы считаются по-разному: правый-нижний только меняет размер, а
   левый-верхний меняет его от НЕПОДВИЖНОГО правого-нижнего угла — вместе
   с размером едет и положение. Иначе панель росла бы вбок от курсора,
   а тянули бы мы за другой её край. */
(function makeMovable() {
  const handle = panel.querySelector('.ru-hd');
  const grips = [
    { el: panel.querySelector('#ru-grip'), mode: 'resize' },
    { el: panel.querySelector('#ru-grip-nw'), mode: 'resize-nw' },
  ];
  let mode = '', sx = 0, sy = 0, ow = 0, oh = 0, ol = 0, ot = 0;

  const start = (e, m) => {
    /* Кнопки в шапке нажимаются, а не тащат панель. Ручка угла живёт в
       шапке (левый верхний): нажатие на неё — это ресайз, а не перетаскивание. */
    if (e.target.tagName === 'BUTTON') return;
    if (m === 'drag' && e.target.classList.contains('ru-grip')) return;
    mode = m;
    sx = e.clientX; sy = e.clientY;
    const r = panel.getBoundingClientRect();
    ow = r.width; oh = r.height; ol = r.left; ot = r.top;
    panel.style.right = 'auto'; panel.style.bottom = 'auto';
    panel.style.left = ol + 'px'; panel.style.top = ot + 'px';
    try { panel.setPointerCapture(e.pointerId); } catch (_) { /* вне окна событие всё равно приходит на панель */ }
    e.preventDefault();
  };

  const move = (e) => {
    if (!mode) return;
    if (mode === 'drag') {
      const nx = Math.max(0, Math.min(window.innerWidth - 80, ol + (e.clientX - sx)));
      const ny = Math.max(0, Math.min(window.innerHeight - 40, ot + (e.clientY - sy)));
      panel.style.left = nx + 'px'; panel.style.top = ny + 'px';
      return;
    }
    if (mode === 'resize-nw') {
      /* Правый-нижний угол стоит на месте: размер — это расстояние до него,
         а положение — где курсор. Тянуть за левый край можно только пока
         панель не упрётся в свой минимум — дальше край «залипает». */
      const size = clampPanel({ w: ol + ow - e.clientX, h: ot + oh - e.clientY });
      const nx = Math.max(0, ol + ow - size.w);
      const ny = Math.max(0, ot + oh - size.h);
      panel.style.width = size.w + 'px';
      panel.style.height = size.h + 'px';
      panel.style.left = nx + 'px';
      panel.style.top = ny + 'px';
      return;
    }
    const size = clampPanel({ w: ow + (e.clientX - sx), h: oh + (e.clientY - sy) });
    panel.style.width = size.w + 'px';
    panel.style.height = size.h + 'px';
  };

  const stop = () => {
    if (!mode) return;
    const resized = mode !== 'drag';
    mode = '';
    const r = panel.getBoundingClientRect();
    const patch = { pos: { x: Math.round(r.left), y: Math.round(r.top) } };
    if (resized) patch.size = { w: Math.round(r.width), h: Math.round(r.height) };
    saveUi(patch);
  };

  handle.addEventListener('pointerdown', (e) => start(e, 'drag'));
  for (const g of grips) g.el.addEventListener('pointerdown', (e) => start(e, g.mode));
  panel.addEventListener('pointermove', move);
  panel.addEventListener('pointerup', stop);
  panel.addEventListener('pointercancel', stop);
})();

$('ru-min').onclick = () => {
  const b = panel.querySelector('.ru-bd');
  const hidden = b.style.display === 'none';
  b.style.display = hidden ? '' : 'none';
  panel.classList.toggle('ru-min', !hidden);
  $('ru-min').textContent = hidden ? '—' : '▢';
  applyUi();
};
$('ru-close').onclick = () => { aborted = true; panel.remove(); };
$('ru-pick').onclick = () => $('ru-file').click();
$('ru-log-dl').onclick = () => {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([logText], { type: 'text/plain;charset=utf-8' }));
  a.download = 'rulate-bulk-log.txt';
  a.click();
};

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
    $('ru-plan').disabled = valid.length === 0;
    $('ru-start').disabled = $('ru-dry').disabled = valid.length === 0;
    plan = null;
    site = null;
    selected = new Set();
    reports = new Map();
    $('ru-plan-issues').checked = false;
    planState.open = false;
    renderPlan();
    log(`Файл «${f.name}»: глав ${parsed.length} (валидных ${valid.length}).`, valid.length ? 'ok' : 'err');
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

async function launch(dryRun) {
  aborted = false;
  CFG.verify = $('ru-verify').checked;
  CFG.skipSame = $('ru-skip-same').checked;
  CFG.minChrf = parseChrfThreshold($('ru-chrf').value);
  CFG.delayMs = parseInt($('ru-delay').value, 10) || 2500;
  CFG.jitterMs = parseInt($('ru-jitter').value, 10) || 0;
  CFG.titleMode = $('ru-title-mode').value;
  const rangeStr = $('ru-range').value.trim();
  if (plan) planSetRange(plan, rangeStr);

  /* Выделение — это фильтр «что проверять», а не «что лить»: заливка идёт
     по всему плану, а строку, которую лить не надо, отмечают «не выбрана». */
  const onlyFi = null;
  if (selected.size) log(`Выделено ${selected.size} строк (для «Проверить»); заливка идёт по всему плану.`, 'plain');

  $('ru-start').disabled = $('ru-dry').disabled = true;
  $('ru-stop').disabled = false;
  log(`=== ${dryRun ? 'DRY-RUN' : 'ЗАПУСК'} ===`, 'ok');
  try {
    const res = await run(parsed, { dryRun, skipSame: CFG.skipSame, rangeStr, plan, onlyFi }, ui);
    if (res && res.reports) {
      reports = new Map(res.reports.map(r => [r.fi, r]));
      plan = res.plan || plan;
      renderPlan();
      savePlanLocal();
    }
  } finally {
    $('ru-start').disabled = $('ru-dry').disabled = parsed.length === 0;
    $('ru-stop').disabled = true;
  }
}

$('ru-dry').onclick = () => launch(true);
$('ru-start').onclick = () => launch(false);

log('Готово. Панель перетаскивается. Выберите .txt.', 'ok');

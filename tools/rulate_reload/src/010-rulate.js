const CFG = {
  delayMs: 2100,
  jitterMs: 0,
  intraMs: 400,
  verify: true,
  skipSame: true,
  multiFragment: 'skip',
  diffCtx: 40,
  titleMode: 'off',
  fallbackByTitle: false,
  // проверка «та ли это глава»: chrF и отношение длин к уже сохранённому
  // переводу. Калибровка на реальных главах: та же глава — chrF 0.958–0.982,
  // разные главы одной книги — 0.480–0.524
  checkSameChapter: true,
  minChrf: 0.75,
  minLenRatio: 0.35,
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
   Объём (ratio длин) для этой задачи почти бесполезен — у разных глав одного
   автора длина сходится на 0.72-0.92, — поэтому он очень грубый
   предохранитель от обрезанного файла (ratio калибровки для битой главы: 0.17),
   а не решатель. */
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
   Возвращает {ok, chrf, ratio} — по ним же пишется объяснение в лог. */
function looksLikeSameChapter(a, b) {
  const x = norm(a), y = norm(b);
  if (!x.length || !y.length) return { ok: false, chrf: 0, ratio: 0 };
  const ratio = Math.min(x.length, y.length) / Math.max(x.length, y.length);
  const score = chrf(x, y);
  return { ok: score >= CFG.minChrf && ratio >= CFG.minLenRatio, chrf: score, ratio };
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
function parseRange(str) {
  if (!str || !str.trim()) return null;
  const parts = str.trim().split(/[,;]\s*/);
  const set = new Set();
  for (const p of parts) {
    const m = p.match(/^(\d+)\s*[-\u2013]\s*(\d+)$/);
    if (m) {
      const a = parseInt(m[1], 10), b = parseInt(m[2], 10);
      for (let i = Math.min(a, b); i <= Math.max(a, b); i++) set.add(i);
    } else if (/^\d+$/.test(p.trim())) {
      set.add(parseInt(p.trim(), 10));
    }
  }
  return set.size > 0 ? set : null;
}

/* ---------- карта сайта ---------- */
/* Карта — «номер → СПИСОК глав сайта», а не одна глава: на сайте бывает
   несколько глав с одним номером («Глава 1» во втором томе, «Глава 1.
   Дополнительная глава»). Раньше в карте оставалась первая, остальные
   терялись молча — и текст дополнительной главы уезжал в «Историю».
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

    if (ch.num == null) {
      if (!CFG.fallbackByTitle) return;
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

/* ---------- основной цикл ---------- */
async function run(parsed, opts, ui) {
  const bookId = bookIdFromUrl();
  if (!bookId) return ui.log('Не определён id книги из URL.', 'err');
  const globalCsrf = $meta('csrf-token');
  const rangeSet = parseRange(opts.rangeStr);

  const tmLabel = CFG.titleMode === 'off' ? 'выкл' : CFG.titleMode === 'title-only' ? 'ТОЛЬКО названия' : 'названия + текст';
  ui.log(`Книга #${bookId} | Глав: ${parsed.length} | dry-run: ${opts.dryRun ? 'ДА' : 'нет'} | названия: ${tmLabel}`, 'ok');
  if (rangeSet) ui.log(`Диапазон: ${[...rangeSet].sort((a,b)=>a-b).join(', ')}`, 'plain');
  if (CFG.fallbackByTitle) ui.log(`Fallback по названию: ВКЛ`, 'plain');
  ui.log(`Пауза: ${CFG.delayMs}мс ± ${CFG.jitterMs}мс`, 'plain');
  ui.log(opts.skipSame
    ? 'Режим: совпадающие ПРОПУСКАЮТСЯ (текст и названия).'
    : 'Режим: ПРИОРИТЕТ ФАЙЛА — льётся КАЖДАЯ найденная глава.', 'plain');

  let siteData;
  try { siteData = await buildSiteMap(bookId); }
  catch (e) { return ui.log('Ошибка чтения оглавления: ' + e.message, 'err'); }

  const nonStandard = siteData.nonStandard;

  const nums = [...siteData.byNum.keys()].sort((a, b) => a - b);
  ui.log(`Оглавление: глав=${siteData.ordered.length}, номеров=${siteData.byNum.size}, диапазон=${nums.length ? nums[0] + '..' + nums[nums.length-1] : '∅'}.`, siteData.ordered.length ? 'ok' : 'err');

  /* Повторяющийся номер — не повод вычёркивать главы из работы: разведём
     их по названию или порядку (matchChapter), а если не выйдет — откажем
     на этой главе и скажем почему. Прежнее «исключаем всё сомнительное»
     превращало книгу со вторым томом в «не найдено» целиком. */
  const repeated = [...siteData.byNum].filter(([, g]) => g.length > 1)
    .sort((a, b) => a[0] - b[0]);
  if (repeated.length) {
    ui.log(`⚠ Номер повторяется у ${repeated.length} групп глав на сайте:`, 'warn');
    for (const [num, g] of repeated) {
      ui.log(`   №${num}: ${g.map(x => `id=${x.chapterId} «${x.siteTitle}»`).join(' | ')}`, 'warn');
    }
  }

  if (nonStandard.length > 0) {
    ui.log(`⚠ Нестандартные главы на сайте (${nonStandard.length}):`, 'warn');
    const typeLabels = { fractional: 'дробный №', range: 'диапазон', nonstandard: 'без номера' };
    nonStandard.forEach(item => {
      ui.log(`   · [${typeLabels[item.type] || item.type}] «${item.title}» (id=${item.chapterId})`, 'warn');
    });
  }

  let saved = 0, wouldSave = 0, skipSame = 0, skipOther = 0, skipRange = 0, skipProblem = 0, fail = 0, titleChanged = 0, ambiguous = 0;
  const notFoundNums = [];
  const notFoundTitles = [];

  /* План сопоставления — сразу на весь файл, до первой записи: разрешение
     коллизий номеров зависит от всего набора глав (порядок, лишние главы
     на сайте), а не от текущей строки. */
  const plan = planMatches(parsed, siteData);

  for (let i = 0; i < parsed.length; i++) {
    if (ui.aborted) { ui.log('Остановлено.', 'warn'); break; }
    const ch = parsed[i];
    ui.setProgress(i, parsed.length);

    if (ch.problem) {
      skipProblem++;
      ui.log(`[${i+1}/${parsed.length}] «${ch.title}» — ${ch.problem}. ПРОПУСК.`, 'warn');
      continue;
    }

    if (rangeSet && ch.num != null && !rangeSet.has(ch.num)) { skipRange++; continue; }

    const matched = plan.get(i) || { reason: 'нет главы с этим номером' };
    const info = matched.info || null;
    if (!info) {
      if (matched.list) {
        ambiguous++;
        ui.log(`[${i+1}/${parsed.length}] «${ch.title}» — НЕОДНОЗНАЧНО: №${ch.num} на сайте у ${matched.list.length} глав:`, 'err');
        for (const g of matched.list) {
          ui.log(`     id=${g.chapterId} «${g.siteTitle}»`, 'err');
        }
        ui.log('     → допишите номер в тег `:|:` или переименуйте главу. ПРОПУСК.', 'warn');
      } else {
        skipOther++;
        if (ch.num != null) notFoundNums.push(ch.num);
        else notFoundTitles.push(ch.title);
        ui.log(`[${i+1}/${parsed.length}] Глава ${ch.num != null ? ch.num : '«'+ch.title+'»'} — НЕ НАЙДЕНА (${matched.reason}).`, 'warn');
      }
      continue;
    }
    if (matched.how) ui.log(`[${i+1}/${parsed.length}] «${ch.title}» сопоставлена ${matched.how}: id=${info.chapterId} «${info.siteTitle}»`, 'plain');

    try {
      /* ===== РЕЖИМ: ТОЛЬКО НАЗВАНИЯ ===== */
      if (CFG.titleMode === 'title-only') {
        const curTitle = info.siteTitle || '';
        const titleSame = curTitle.trim() === (ch.title || '').trim();

        ui.log(`[${i+1}/${parsed.length}] Глава ${ch.num}: «${ch.title}» ↔ «${curTitle}»`, 'plain');

        if (opts.skipSame && titleSame) {
          skipSame++;
          ui.log('     название совпадает — пропуск.', 'plain');
          continue;
        }

        if (opts.dryRun) {
          wouldSave++;
          ui.log('     [DRY] название было бы обновлено', 'plain');
          continue;
        }

        await saveChapterTitle(bookId, info.chapterId, ch.title, globalCsrf);
        titleChanged++;
        ui.log('     название обновлено', 'ok');
        if (i < parsed.length - 1 && !ui.aborted) await globalPause(CFG.delayMs, CFG.jitterMs);
        continue;
      }

      /* ===== РЕЖИМЫ: ТОЛЬКО ТЕКСТ / ТЕКСТ + НАЗВАНИЯ ===== */
      const { html, doc } = await getDoc(info.url);
      await globalPause(CFG.intraMs, 0);

      const userId = userIdFromHtml(html);
      if (!userId) throw new Error('Нет данных пользователя — вы залогинены?');
      const csrf = csrfFromDoc(doc) || globalCsrf;
      if (!csrf) throw new Error('CSRF не найден');

      const siteTitle = (html.match(/Chap\s*=\s*\{[\s\S]*?title:\s*'([^']*)'/) || [])[1] || ('Глава ' + ch.num);
      const pairs = readChapterIds(doc, userId);
      if (!pairs.length) { skipOther++; ui.log(`[${i+1}/${parsed.length}] Глава ${ch.num} — нет вашего перевода.`, 'warn'); continue; }
      if (pairs.length > 1 && CFG.multiFragment === 'skip') { skipOther++; ui.log(`[${i+1}/${parsed.length}] Глава ${ch.num} — фрагментов ${pairs.length} (>1), пропуск.`, 'warn'); continue; }

      const { origId, trId } = pairs[0];
      ui.log(`[${i+1}/${parsed.length}] Глава ${ch.num}: «${ch.title}» ↔ «${siteTitle}» | chap=${info.chapterId} orig=${origId} tr=${trId} | ${ch.text.length}с`, 'plain');

      const savedText = readSavedText(doc, trId);
      const same = compareAndLog(savedText, ch.text, ui);
      const titleSame = (siteTitle || '').trim() === (ch.title || '').trim();

      /* Последний заслон перед записью: даже при «правильном» номере глава
         на сайте может оказаться ДРУГОЙ (нумерация разъехалась, тег
         поправили, файл от другой версии книги). Сверяем с уже
         сохранённым переводом — это тот же текст, что мы льём, только
         прошлой версии. Не похоже — не пишем. */
      let similar = null;
      if (CFG.checkSameChapter && savedText != null) {
        similar = looksLikeSameChapter(savedText, ch.text);
      }
      if (similar && !similar.ok) {
        fail++;
        ui.log(`     ОТКАЗ: глава на сайте не похожа на нашу (chrF ${similar.chrf.toFixed(3)}, объём ${similar.ratio.toFixed(2)} < ${CFG.minLenRatio}) — нумерация разъехалась?`, 'err');
        ui.log('     → сверьте главу глазами; запись не выполнена', 'warn');
        continue;
      }
      if (similar) {
        ui.log(`     сверка с сохранённым: chrF ${similar.chrf.toFixed(3)}, объём ${similar.ratio.toFixed(2)} — та же глава`, 'plain');
      }

      const doText  = !(opts.skipSame && same);
      const doTitle = (CFG.titleMode === 'title-and-body') && !(opts.skipSame && titleSame);

      if (opts.dryRun) {
        if (!doText && !doTitle) {
          skipSame++;
          ui.log('     [DRY] пропущено бы (всё совпадает)', 'plain');
        } else {
          wouldSave++;
          const parts = [];
          if (doText)  parts.push('текст');
          if (doTitle) parts.push('название');
          ui.log(`     [DRY] будет обновлено: ${parts.join(' + ')}`, 'plain');
        }
      } else {
        if (!doText && !doTitle) {
          skipSame++;
          ui.log('     пропущено (всё совпадает)', 'plain');
        } else {
          if (doTitle) {
            await saveChapterTitle(bookId, info.chapterId, ch.title, csrf);
            titleChanged++;
            ui.log('     название обновлено', 'ok');
            await globalPause(CFG.intraMs, 0);
          } else if (CFG.titleMode === 'title-and-body') {
            ui.log('     название совпадает — переименование пропущено', 'plain');
          }

          if (doText) {
            await saveTranslation(bookId, info.chapterId, origId, trId, ch.text, csrf);
            if (CFG.verify) {
              await globalPause(CFG.intraMs, 0);
              const vDoc = (await getDoc(info.url)).doc;
              const vRaw = readSavedText(vDoc, trId);
              if (norm(vRaw == null ? '' : vRaw) !== norm(ch.text)) {
                fail++;
                ui.log('     ВНИМАНИЕ: после сохранения текст не совпал!', 'err');
                if (vRaw != null) { const d = firstDiff(norm(vRaw), norm(ch.text)); ui.log(`        сайт: ${d.site}`, 'plain'); ui.log(`        файл: ${d.file}`, 'plain'); }
                continue;
              }
              ui.log('     залито + проверка OK', 'ok');
            } else { ui.log('     залито (без проверки).', 'ok'); }
            saved++;
          } else {
            ui.log('     текст совпадает — запись пропущена', 'plain');
          }
        }
      }
    } catch (e) { fail++; ui.log('     ОШИБКА: ' + e.message, 'err'); }

    if (i < parsed.length - 1 && !ui.aborted) await globalPause(opts.dryRun ? 150 : CFG.delayMs, CFG.jitterMs);
  }

  ui.setProgress(parsed.length, parsed.length);

  let summary;
  if (opts.dryRun) {
    summary = `=== DRY-RUN: зальётся ${wouldSave}, пропущено ${skipSame}, не найдено ${skipOther}, неоднозначных ${ambiguous}, вне диапазона ${skipRange}, проблемных ${skipProblem} ===`;
  } else {
    summary = `=== ГОТОВО: залито ${saved}, названий ${titleChanged}, пропущено ${skipSame}, не найдено ${skipOther}, неоднозначных ${ambiguous}, вне диапазона ${skipRange}, проблемных ${skipProblem}, ошибок ${fail} ===`;
  }
  ui.log(summary, fail ? 'warn' : 'ok');

  if (notFoundNums.length > 0 || notFoundTitles.length > 0) {
    const parts = [];
    if (notFoundNums.length > 0) parts.push(`Главы: ${notFoundNums.sort((a,b)=>a-b).join(', ')}`);
    if (notFoundTitles.length > 0) parts.push(`Без номера: ${notFoundTitles.join('; ')}`);
    ui.log(`Не найдены на сайте → ${parts.join(' | ')}`, 'warn');
  }
}

/* ---------- UI ---------- */
GM_addStyle(`#ru-bulk, #ru-bulk * { box-sizing: border-box !important; } #ru-bulk { position: fixed; right: 12px; bottom: 12px; z-index: 999999; width: min(520px, calc(100vw - 24px)); text-align: left !important; background: #1f2430; color: #e6e6e6; border: 1px solid #3a4150; border-radius: 10px; font: 13px/1.4 system-ui, -apple-system, Segoe UI, Roboto, sans-serif !important; box-shadow: 0 8px 28px rgba(0,0,0,.45); margin: 0; padding: 0; } #ru-bulk .ru-hd { padding: 8px 10px; background: #2a3142; border-radius: 10px 10px 0 0; display: flex; justify-content: space-between; align-items: center; gap: 8px; cursor: grab; user-select: none; margin: 0; } #ru-bulk .ru-hd:active { cursor: grabbing; } #ru-bulk .ru-hd b { font-size: 13px; font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin: 0; padding: 0; } #ru-bulk .ru-bd { padding: 10px; margin: 0; } #ru-bulk .ru-hd-btns { display: flex; gap: 4px; align-items: center; } #ru-bulk button#ru-close:hover { background: #d9534f !important; } #ru-bulk button { background: #2f81c4; color: #fff; border: 0; border-radius: 5px; padding: 6px 11px; cursor: pointer; margin: 0; font: inherit; user-select: none; white-space: nowrap; } #ru-bulk button.ru-sec { background: #444c5c; } #ru-bulk button.ru-go { background: #2e9e5b; } #ru-bulk button:hover { filter: brightness(1.08); } #ru-bulk button:disabled { opacity: .45; cursor: not-allowed; filter: none; } #ru-bulk .ru-log-box { min-height: 120px; max-height: 40vh; overflow-x: hidden !important; overflow-y: auto !important; background: #11151c; border: 1px solid #333; border-radius: 6px; padding: 7px !important; margin: 8px 0 0 0 !important; font: 11.5px/1.4 ui-monospace, Menlo, Consolas, monospace !important; white-space: pre-wrap !important; word-break: break-word !important; text-align: left !important; } #ru-bulk .ru-log-box span { margin: 0 !important; padding: 0 !important; display: inline; } #ru-bulk .ru-bar { height: 6px; background: #333; border-radius: 3px; margin: 8px 0 0 0; overflow: hidden; } #ru-bulk .ru-bar > i { display: block; height: 100%; width: 0; background: #37c46a; transition: width .2s; } #ru-bulk .ru-row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 6px 0 0 0 !important; padding: 0 !important; width: 100% !important; } #ru-bulk .ru-row:first-child { margin-top: 0 !important; } #ru-bulk .ru-ok { color: #7fe39a !important; } #ru-bulk .ru-err { color: #ff8585 !important; } #ru-bulk .ru-warn { color: #ffd166 !important; } #ru-bulk .ru-plain { color: #9aa3b2 !important; } #ru-bulk label { display: inline-flex; gap: 5px; align-items: center; user-select: none; cursor: pointer; white-space: nowrap; margin: 0 !important; font-weight: normal !important; } #ru-bulk input[type=number], #ru-bulk input[type=text] { width: 70px; background: #11151c; color: #e6e6e6; border: 1px solid #3a4150; border-radius: 4px; padding: 3px 5px; font: inherit; margin: 0; outline: none; } #ru-bulk input[type=text].ru-wide { width: 130px; } #ru-bulk select { background: #11151c; color: #e6e6e6; border: 1px solid #3a4150; border-radius: 4px; padding: 3px 5px; font: inherit; margin: 0; } #ru-bulk input[type=checkbox] { margin: 0 !important; transform: scale(1.1); } #ru-bulk .ru-muted { color: #9aa3b2; } #ru-bulk .ru-sep { border-top: 1px solid #3a4150; margin: 8px 0 !important; padding: 0 !important; }`);

const panel = document.createElement('div');
panel.id = 'ru-bulk';
/* Разметку панели разбирает DOMParser, а не innerHTML: подстановка шаблона в
   innerHTML — это путь для HTML, а нужен ровно разобранный DOM (и статические
   значения CFG здесь единственные данные, но правило одно на весь файл). */
const panelMarkup = `<div class="ru-hd"> <b>RL · обновление глав v2.2</b> <div class="ru-hd-btns"> <button class="ru-sec" id="ru-min" title="Свернуть">—</button> <button class="ru-sec" id="ru-close" title="Закрыть">✕</button> </div> </div> <div class="ru-bd"> <div class="ru-row"> <input type="file" id="ru-file" accept=".txt,text/plain" style="display:none"> <button id="ru-pick">Выбрать .txt</button> <button id="ru-dry" class="ru-sec" disabled>dry-run</button> <button id="ru-start" class="ru-go" disabled>Старт</button> <button id="ru-stop" class="ru-sec" disabled>Стоп</button> <button id="ru-log-dl" class="ru-sec">Лог</button> </div> <div class="ru-sep"></div> <div class="ru-row"> <span class="ru-muted">Глав:</span> <b id="ru-count">0</b> <span class="ru-muted" style="margin-left:8px">Диапазон:</span> <input id="ru-range" type="text" class="ru-wide" placeholder="1-50, 55" title="Диапазон: 1-50, 55, 60-70. Пусто = все"> </div> <div class="ru-sep"></div> <div class="ru-row"> <span class="ru-muted">Пауза (мс):</span> <input id="ru-delay" type="number" value="${CFG.delayMs}" min="100" step="100"> <span class="ru-muted" style="margin-left:6px">Рандом ±:</span> <input id="ru-jitter" type="number" value="${CFG.jitterMs}" min="0" step="50"> </div> <div class="ru-row"> <label title="Перечитать главу после записи"><input type="checkbox" id="ru-verify" ${CFG.verify ? 'checked' : ''}> Проверять</label> <label title="Не лить главы, чей текст и название уже совпадают"><input type="checkbox" id="ru-skip-same" ${CFG.skipSame ? 'checked' : ''}> Пропуск совп.</label> <label title="Если номер не найден — искать по названию"><input type="checkbox" id="ru-fallback-title"> Title fallback</label> <label title="Сверять главу с уже сохранённым переводом (chrF) — заслон от разъехавшейся нумерации"><input type="checkbox" id="ru-check-same" ${CFG.checkSameChapter ? 'checked' : ''}> Сверка главы</label> </div> <div class="ru-row"> <span class="ru-muted">Названия глав:</span> <select id="ru-title-mode"> <option value="off">Не обновлять</option> <option value="title-only">Обновлять названия</option> <option value="title-and-body">Обновлять названия + текст</option> </select> </div> <div class="ru-bar"><i id="ru-bar-fill"></i></div> <div class="ru-log-box" id="ru-log"></div> </div>`;
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

let parsed = [], aborted = false;
const ui = {
  log,
  get aborted() { return aborted; },
  setProgress: (i, n) => $('ru-bar-fill').style.width = Math.round(i / n * 100) + '%',
};

(function makeDraggable() {
  const handle = panel.querySelector('.ru-hd');
  let drag = false, sx = 0, sy = 0, ox = 0, oy = 0;
  handle.addEventListener('pointerdown', (e) => {
    if (e.target.tagName === 'BUTTON') return;
    drag = true; sx = e.clientX; sy = e.clientY;
    const r = panel.getBoundingClientRect();
    ox = r.left; oy = r.top;
    panel.style.right = 'auto'; panel.style.bottom = 'auto';
    panel.style.left = ox + 'px'; panel.style.top = oy + 'px';
    try { handle.setPointerCapture(e.pointerId); } catch (_) {}
    e.preventDefault();
  });
  handle.addEventListener('pointermove', (e) => {
    if (!drag) return;
    let nx = ox + (e.clientX - sx), ny = oy + (e.clientY - sy);
    nx = Math.max(0, Math.min(window.innerWidth - 60, nx));
    ny = Math.max(0, Math.min(window.innerHeight - 40, ny));
    panel.style.left = nx + 'px'; panel.style.top = ny + 'px';
  });
  const stop = () => { drag = false; };
  handle.addEventListener('pointerup', stop);
  handle.addEventListener('pointercancel', stop);
})();

$('ru-min').onclick = () => {
  const b = panel.querySelector('.ru-bd');
  const hidden = b.style.display === 'none';
  b.style.display = hidden ? '' : 'none';
  $('ru-min').textContent = hidden ? '—' : '▢';
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

    /* Дубли названий в файле больше не выбрасываются: главы разводятся
       номером из тега, а если номер один — порядком внутри группы.
       Раньше обе «Главы 1» исчезали из заливки целиком. */
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
    $('ru-start').disabled = $('ru-dry').disabled = valid.length === 0;
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

async function launch(dryRun) {
  aborted = false;
  CFG.verify = $('ru-verify').checked;
  CFG.skipSame = $('ru-skip-same').checked;
  CFG.fallbackByTitle = $('ru-fallback-title').checked;
  CFG.checkSameChapter = $('ru-check-same').checked;
  CFG.delayMs = parseInt($('ru-delay').value, 10) || 2500;
  CFG.jitterMs = parseInt($('ru-jitter').value, 10) || 0;
  CFG.titleMode = $('ru-title-mode').value;
  const rangeStr = $('ru-range').value.trim();

  $('ru-start').disabled = $('ru-dry').disabled = true;
  $('ru-stop').disabled = false;
  log(`=== ${dryRun ? 'DRY-RUN' : 'ЗАПУСК'} ===`, 'ok');
  try {
    await run(parsed, { dryRun, skipSame: CFG.skipSame, rangeStr }, ui);
  } finally {
    $('ru-start').disabled = $('ru-dry').disabled = parsed.length === 0;
    $('ru-stop').disabled = true;
  }
}

$('ru-dry').onclick = () => launch(true);
$('ru-start').onclick = () => launch(false);

log('Готово. Панель перетаскивается. Выберите .txt.', 'ok');

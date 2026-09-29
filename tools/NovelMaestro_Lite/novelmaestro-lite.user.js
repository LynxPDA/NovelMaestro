// ==UserScript==
// @name         NovelMaestro Lite
// @namespace    https://github.com/LynxPDA/NovelMaestro
// @version      1.40
// @description  Универсальный переводчик новелл с глоссарием по книгам, стримингом и режимом читалки
// @author       NovelMaestro
// @license      MIT
// @homepageURL  https://github.com/LynxPDA/NovelMaestro
// @supportURL   https://github.com/LynxPDA/NovelMaestro/issues
// @noframes
// @match        *://*/*
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_xmlhttpRequest
// @connect      self
// @connect      localhost
// @connect      routerai.ru
// @connect      routerapi.ru
// @connect      zveno.ai
// @connect      api.openai.com
// @connect      openrouter.ai
// @connect      api.anthropic.com
// @connect      generativelanguage.googleapis.com
// @connect      api.githubcopilot.com
// @connect      models.inference.ai.azure.com
// @connect      api-inference.huggingface.co
// @connect      api-inference.router.huggingface.tech
// @connect      api.groq.com
// @connect      api.together.xyz
// @connect      api.fireworks.ai
// @connect      api.deepinfra.com
// @connect      api.cerebras.ai
// @connect      api.sambanova.ai
// @connect      api.mistral.ai
// @connect      api.x.ai
// @connect      api.cohere.com
// @connect      api.ai21.com
// @connect      integrate.api.nvidia.com
// @connect      api.replicate.com
// @connect      api.deepseek.com
// @connect      api.moonshot.cn
// @connect      api.moonshot.ai
// @connect      open.bigmodel.cn
// @connect      api.minimaxi.com
// @connect      dashscope.aliyuncs.com
// @connect      ark.cn-beijing.volces.com
// @connect      qianfan.baidubce.com
// @connect      api.hunyuan.tencentcloud.com
// @connect      api.stepfun.com
// @connect      api.lingyiwanwu.com
// @connect      api.siliconflow.com
// @connect      api.siliconflow.cn
// @connect      api.novita.ai
// @connect      api.ppio.com
// @connect      *
// @run-at       document-idle
// @downloadURL  https://raw.githubusercontent.com/LynxPDA/NovelMaestro/main/tools/NovelMaestro_Lite/novelmaestro-lite.user.js
// @updateURL    https://raw.githubusercontent.com/LynxPDA/NovelMaestro/main/tools/NovelMaestro_Lite/novelmaestro-lite.meta.js
// ==/UserScript==
// СОБРАНО из src/ и meta.js — локальная правка будет перезаписана:
//   python3 tools/build_userscripts.py

(() => {
    // Lite обязана быть единственной на документ: тот же документ может быть
    // проинжектирован повторно (SPA-коммит, второй проход менеджера) — иначе
    // дублируются слушатели, теневой хост и плавающие кнопки; id хоста ставит
    // 080-ui-markup.js
    if (document.getElementById('nm-lite-host')) return;

    const APP_VERSION = '1.40';

    // ===== КОНФИГУРАЦИЯ =====
    // Штатный промпт перевода (редактируемое поле Настроек). Плейсхолдеры те же,
    // что подставляет chunkUserPrompt(): {sourceLang} {targetLang} {glossary} {text}.
    const TRANSLATION_PROMPT = [
        'Ты — профессиональный переводчик, литературный редактор, локализатор и бета-ридер, специализирующийся на художественном переводе. Твоя задача — не просто переводить, а сделать текст литературным, логичным и атмосферным.',
        '',
        '* Никакой отсебятины: Запрещено добавлять новые действия, менять сюжет или искажать характер персонажей.',
        '* Литературная обработка: Сглаживай машинный слог и убирай проявления канцелярита. Текст должен звучать естественно, образно и атмосферно, как если бы книга изначально была написана на хорошем русском языке. Придавай тексту литературную красоту, избегая сухости, но не перегружай его и не ломай изначальную задумку сцены, строго сохраняя оригинальную авторскую интонацию и смысл.',
        '* Написание чисел: В художественных описаниях числа от одного до девяти пиши прописью (один шаг, два меча). Большие числа, уровни, стадии и системные характеристики (Уровень 50, Урон: 1000) оставляй цифрами.',
        '* Критически важно: Пиши правильно, без ошибок.',
        '',
        'Правила оформления речи и текста:',
        '* Диалоги (реплики вслух): Начинай с новой строки, используй длинное тире (—) и пробел. Кавычки НЕ используются.',
        '    Шаблоны:',
        '        — П. / — П? / — П!',
        '        — П, — п. / — П? — п. / — П! — п.',
        '    Примеры:',
        '      — Я обязательно вернусь.',
        '      — Неужели это стадия Зарождающейся Души?',
        '      — Убить его!',
        '      — Этот артефакт принадлежит мне, — спокойно произнес юноша.',
        '      — Ты посмел бросить мне вызов? — холодно усмехнулся старейшина.',
        '      — Невозможно! — в ужасе закричал глава секты.',
        '* Прямая речь внутри текста: Если речь идет не с новой строки, используй кавычки-елочки («»).',
        '    Шаблоны: А: «П». / «П, — а, — п».',
        '    Примеры:',
        '        Старейшина указал на него пальцем и крикнул: «Схватить его!»',
        '        «Запомни, — тихо произнес мастер, — путь меча не терпит сомнений».',
        '        «Не подходи! — взвизгнула демоническая лиса. — Иначе я разрушу ядро!»',
        '        «Кто мог прорвать защитный массив? — нахмурился страж. — Неужели эксперт стадии Формирования Ядра?»',
        '* Мысли персонажа: Оформляй в кавычки-елочки («»).',
        '    Пример: «Какая устрашающая Ци...» — с ужасом подумал Ван Линь.',
        '* Телепатические сообщения: Заключай в квадратные скобки ([ ]).',
        '    Пример: [Младший брат, уходи!] — раздался в его голове голос наставника.',
        '* Системные сообщения (ЛитРПГ/Система): Заключай в квадратные скобки ([ ]).',
        '    Пример: [Внимание! Получен новый навык: Дыхание Дракона].',
        '* Игровые чаты и сообщения: Используй формат «Имя/Ник»: «Сообщение».',
        '    Пример: «Безумный Мечник»: «Кто пойдет в Данж на 50 уровне?»',
        '',
        'ГЛОССАРИЙ ТЕРМИНОВ (обязательно используй эти переводы, сохраняй пол персонажей):',
        '{glossary}',
        '',
        'ВАЖНО:',
        '- Имена и термины переводи точно по глоссарию',
        '- Сохраняй пол персонажей (он/она) согласно глоссарию',
        '- Сохраняй стиль оригинала',
        '- Сохраняй разбивку на абзацы',
        '- Возвращай ТОЛЬКО перевод, без комментариев',
        '',
        'Переведи следующий текст с {sourceLang} на {targetLang}.',
        '',
        'Текст:',
        '{text}',
    ].join('\n');
    // Прежний встроенный промпт — эталон миграции: если в хранилище лежит строка,
    // равная ему, промпт руками не трогали и можно поставить новый.
    const LEGACY_TRANSLATION_PROMPT = 'Переведи следующий текст с {sourceLang} на {targetLang}.\n\nГЛОССАРИЙ ТЕРМИНОВ (обязательно используй эти переводы, сохраняй пол персонажей):\n{glossary}\n\nВАЖНО:\n- Имена и термины переводи точно по глоссарию\n- Сохраняй пол персонажей (он/она) согласно глоссарию\n- Сохраняй стиль оригинала\n- Сохраняй разбивку на абзацы\n- Возвращай ТОЛЬКО перевод, без комментариев\n\nТекст:\n{text}';
    const DEFAULT_CONFIG = {
        apiHost: 'https://routerai.ru/api/v1',
        apiKey: '',
        model: 'google/gemma-4-31b-it',
        sourceLang: 'Авто',
        targetLang: 'Русский',
        reasoningEffort: 'None',
        chunkSize: 30000,
        requestTimeout: 60, // СЕКУНДЫ (0 = без таймаута): у стрима — пауза между токенами, у обычного запроса — ожидание всего ответа
        maxRetries: 3,
        localModel: false,
        gmTransport: 'page',
        translationPrompt: TRANSLATION_PROMPT,
        extractionPrompt: 'Извлеки из текста имена персонажей, места, артефакты, организации и важные термины. Перевод терминов должен быть на {targetLang}.\n\nВерни JSON в формате:\n{\n  "term": "оригинальный термин",\n  "translation": "перевод на {targetLang}. Только 1 вариант перевода!",\n  "type": "Тип записи (Пример: Person (male), Creature (female), Location, Artifact, Organization, Term)"\n}\n\ntype - тип записи. Для живых существ (персонажи, существа) указывай пол в скобках:\n- Person (male) / Person (female) — персонаж мужского/женского пола\n- Person (unknown) — пол неизвестен\n- Creature (male) / Creature (female) — существо\nДля не-персонажей пол не указывай: Location, Artifact, Organization, Term и т.п.\n\nВерни ТОЛЬКО валидный JSON массив объектов. Без дополнительного текста.\n\nТекст:\n{text}',
        fuzzySearchThreshold: 0.7,
        autoNER: true,
        glossaryCurrentPageOnly: false,
        preemptiveTranslation: true, // автоперевод следующей главы в фоне
        // 'auto' — следовать системной теме; 'dark'/'light' — ручной выбор кнопкой в читалке
        readerTheme: 'auto',
        readerFontFamily: 'Georgia, serif',
        readerFontSize: 14,
        readerLineHeight: 1.6,
        readerParagraphSpacing: 1.2,
        readerContentWidth: 80
    };

    // Тема интерфейса и читалки: по умолчанию «как в системе», кнопка в читалке ходит
    // по кольцу auto → тёмная → светлая → auto.
    const THEME_MODE_LABELS = { auto: 'как в системе', dark: 'тёмная', light: 'светлая' };
    const THEME_MODE_CYCLE = { auto: 'dark', dark: 'light', light: 'auto' };

    // В GM-хранилище расширения — только список книг и настройки: глоссарии
    // (мегабайты) и кэш переводов живут в IndexedDB каждого сайта отдельно.
    let config = { ...DEFAULT_CONFIG, ...GM_getValue('config', {}) };
    // Миграция штатного промпта: в GM-хранилище лежит вся конфигурация целиком,
    // поэтому новый встроенный текст дожил бы только до первой сохраненной копии.
    // Кого промпт не касался (равен старому эталону) — переводим на новый;
    // самописные правки не трогаем.
    if (config.translationPrompt === LEGACY_TRANSLATION_PROMPT) {
        config.translationPrompt = DEFAULT_CONFIG.translationPrompt;
        GM_setValue('config', config);
    }
    let books = GM_getValue('books', {});
    let currentBookKey = null;
    let managedBookKey = null;

    let glossarySort = { field: 'count', dir: 'desc' };
    let glossaryPage = 0;
    const PAGE_SIZE = 25;
    let glossaryFilter = '';

    const ngramCache = new Map();
    const MAX_CACHE_SIZE = 1000;

    let readerModeActive = false;
    let readerState = null;
    let elementTrainingMode = false;
    let pendingTranslateAfterTraining = false;
    let trainingHighlightedEl = null;
    let trainingPopupTarget = null;
    // обучение на тач-устройствах — отдельный touch-конвейер: отменяемый touchend
    // подавляет синтетические click/переход по ссылке и long-press-меню браузера,
    // одиночный тап только подсвечивает, двойной тап или удержание открывают попап
    let trainTapEl = null;
    let trainTapTime = 0;
    let trainTouchT = 0;
    let trainTouchXY = null;
    let trainTouchHandledAt = 0;
    const DOUBLE_TAP_DELAY = 350;
    const LONG_PRESS_DELAY = 450;
    const preemptiveRunning = new Set();

    // ===== ПОИСК ТЕРМИНОВ: зеркало core/common.py =====
    // Нормализация единая с конвейером (core normalize_for_search): NFC → lower →
    // пробелы и пунктуация вычёркиваются. Границы слов не требуются: с прежним
    // требованием «не буква рядом» термины не находились внутри CJK-предложений
    // (перед именем стоит иероглиф-буква) и в бесслотных текстах (тай, лаос,
    // кхмер, бирма, тибет).
    const SEARCH_DROP_RE = /[\s　​.,!?;:()«»"'’‘…—–\-]+/g;
    // размер n-грамм совпадает с core (get_ngrams n=3): порог в настройках значит
    // одно и то же в web-конвейере и в Lite
    const NGRAM_SIZE = 3;

    function normalize(str) {
        return String(str ?? '').normalize('NFC').toLowerCase().replace(SEARCH_DROP_RE, '');
    }
    function ngrams(normText, n = NGRAM_SIZE) {
        if (!normText) return new Set();
        if (normText.length < n) return new Set([normText]);
        const grams = new Set();
        for (let i = 0; i + n <= normText.length; i++) grams.add(normText.slice(i, i + n));
        return grams;
    }
    // кэш — только для коротких строк (терминов): n-граммы чанка считаются один раз
    // на вызов и в кэш не кладутся
    function getCachedNgrams(normText) {
        if (ngramCache.has(normText)) return ngramCache.get(normText);
        if (ngramCache.size >= MAX_CACHE_SIZE) ngramCache.clear();
        const g = ngrams(normText);
        ngramCache.set(normText, g);
        return g;
    }
    // CJK-термин (первый символ — иероглиф/кана/хангыль) ищется только точно:
    // нечёткость на иероглифике даёт сплошные ложные срабатывания (core is_cjk)
    function isCjkChar(ch) {
        const cp = ch ? ch.codePointAt(0) : 0;
        return (cp >= 0x4E00 && cp <= 0x9FFF) || (cp >= 0x3400 && cp <= 0x4DBF)
            || (cp >= 0x20000 && cp <= 0x2A6DF) || (cp >= 0x2A700 && cp <= 0x2B73F)
            || (cp >= 0xF900 && cp <= 0xFAFF) || (cp >= 0x2F800 && cp <= 0x2FA1F)
            || (cp >= 0x3040 && cp <= 0x309F) || (cp >= 0x30A0 && cp <= 0x30FF)
            || (cp >= 0xAC00 && cp <= 0xD7AF);
    }
    // длина самой длинной общей подстроки (core: SequenceMatcher.find_longest_match);
    // DP двумя строками — термин короткий, и путь включается только для терминов,
    // уже прошедших n-граммный порог
    function longestCommonRun(term, text) {
        let prev = new Uint16Array(text.length + 1);
        let cur = new Uint16Array(text.length + 1);
        let best = 0;
        for (let i = 1; i <= term.length; i++) {
            cur.fill(0);
            for (let j = 1; j <= text.length; j++) {
                if (term.charCodeAt(i - 1) === text.charCodeAt(j - 1)) {
                    const v = prev[j - 1] + 1;
                    cur[j] = v;
                    if (v > best) best = v;
                }
            }
            const swap = prev; prev = cur; cur = swap;
        }
        return best;
    }
    // «термин (или алиас) есть в тексте»: точное вхождение нормализованных строк,
    // иначе нечёткое — перекрытие n-грамм от термина >= threshold И общая подстрока
    // >= len(термина) * threshold (core _fuzzy_hit). textNorm/textG — уже
    // нормализованный текст и его n-граммы (считаются один раз на чанк)
    function termHitsText(entry, textNorm, textG, threshold) {
        const variants = [entry && entry.term, ...((entry && entry.aliases) || [])]
            .map(v => normalize(v)).filter(v => v);
        if (!variants.length || !textNorm) return false;
        for (const v of variants) {
            if (textNorm.indexOf(v) !== -1) return true;
            if (isCjkChar(v[0])) continue;
            const vg = getCachedNgrams(v);
            if (!vg.size) continue;
            let inter = 0;
            for (const g of vg) { if (textG.has(g)) inter++; }
            if (inter / vg.size >= threshold
                && longestCommonRun(v, textNorm) >= v.length * threshold) { return true; }
        }
        return false;
    }
    // та же семантика для пары «строка ↔ строка» (фильтр и слияние глоссария)
    function termMatchesText(haystack, needle, threshold) {
        const textNorm = normalize(haystack);
        return termHitsText({ term: needle }, textNorm, ngrams(textNorm), threshold);
    }
    function paragraphsOf(text) { return text.split(/\n+/).map(s => s.trim()).filter(s => s.length > 0); }
    function splitByNewlines(text, chunkSize) {
        const paras = paragraphsOf(text);
        const chunks = [];
        let cur = '';
        for (const p of paras) {
            if (cur.length + p.length + 2 > chunkSize && cur) { chunks.push(cur); cur = p; }
            else cur += (cur ? '\n\n' : '') + p;
        }
        if (cur) chunks.push(cur);
        return chunks.length > 0 ? chunks : [text];
    }
    // ===== КНИГИ =====
    function pageCacheKey() { return location.href.split('#')[0]; }
    function suggestBookKeyFromUrl() {
        let path = location.pathname;
        const chapterPattern = /\/(chapter|ch|c|p|page|volume|v|ep|episode|read|detail)(?=\/|$)/i;
        const match = path.match(chapterPattern);
        if (match) path = path.slice(0, match.index);
        path = path.replace(/\/\d+\/?$/, '');
        let search = location.search;
        search = search.replace(/(^|[?&])(chapterNumber|chapter|ch|page|p|ep|episode|num|id)=\d+/gi, '$1');
        search = search.replace(/^\?&/, '?').replace(/&$/, '');
        return (location.origin + path + search).replace(/[?&]$/, '').replace(/\/+$/, '');
    }
    function findBookByUrl() {
        const url = location.href;
        const baseUrl = suggestBookKeyFromUrl();
        for (const key of Object.keys(books)) if (url.includes(key) || key === baseUrl) return key;
        // книга могла быть создана с URL другой главы: соседние главы живут в одном
        // родительском каталоге — точное совпадение с ним, иначе самый глубокий ключ в нём
        const parent = baseUrl.replace(/\/[^/]*\/?$/, '');
        if (parent && parent !== baseUrl && parent.length > 8) {
            if (books[parent]) return parent;
            let best = null;
            for (const key of Object.keys(books)) {
                if (key.startsWith(parent + '/') && (!best || key.length < best.length)) best = key;
            }
            if (best) return best;
        }
        for (const key of Object.keys(books)) if (baseUrl.includes(key) || key.includes(baseUrl)) return key;
        return null;
    }
    function getCurrentBook() {
        if (!currentBookKey) currentBookKey = findBookByUrl();
        if (currentBookKey && books[currentBookKey]) return { key: currentBookKey, book: books[currentBookKey] };
        return null;
    }
    function getBookSelectors() {
        const cur = getCurrentBook();
        return (cur && cur.book.selectors) ? cur.book.selectors : {};
    }
    function isNerDoneForPage(bookKey) { return !!(siteNerDone[bookKey] && siteNerDone[bookKey][pageCacheKey()]); }
    function markNerDone(bookKey) {
        if (!siteNerDone[bookKey]) siteNerDone[bookKey] = {};
        siteNerDone[bookKey][pageCacheKey()] = Date.now();
        dbPut('n/' + bookKey, siteNerDone[bookKey]);
    }
    function clearNerCache(bookKey) {
        siteNerDone[bookKey] = {};
        dbPut('n/' + bookKey, siteNerDone[bookKey]);
    }

    // ===== ДАННЫЕ САЙТА: IndexedDB этого origin =====
    // Глоссарии книг, отметки NER и rolling-кэш переводов живут в IndexedDB того сайта,
    // где стоят книги: GM-хранилище такой объём не тянет, а localStorage ограничен
    // ~5 МБ на origin и сюда не влезает. Кэш — только текущая и следующая главы.
    const DB_NAME = 'NovelMaestroLite';
    const DB_STORE = 'kv';
    const CACHE_CHAPTERS = 2;
    let dbPromise = null;
    function openDb() {
        if (!dbPromise) {
            dbPromise = new Promise((resolve) => {
                try {
                    const req = indexedDB.open(DB_NAME, 1);
                    req.onupgradeneeded = () => { try { req.result.createObjectStore(DB_STORE); } catch { /* уже создан */ } };
                    req.onsuccess = () => resolve(req.result);
                    req.onerror = () => resolve(null);
                } catch { resolve(null); }
            });
        }
        return dbPromise;
    }
    async function dbPut(key, value) {
        const db = await openDb(); if (!db) return;
        try {
            await new Promise((resolve, reject) => {
                const tx = db.transaction(DB_STORE, 'readwrite');
                tx.objectStore(DB_STORE).put(value, key);
                tx.oncomplete = () => resolve();
                tx.onerror = () => reject(tx.error);
            });
        } catch (e) { console.warn('[NovelMaestro] Запись в IndexedDB:', e && e.message); }
    }
    async function dbDelete(key) {
        const db = await openDb(); if (!db) return;
        try {
            await new Promise((resolve, reject) => {
                const tx = db.transaction(DB_STORE, 'readwrite');
                tx.objectStore(DB_STORE).delete(key);
                tx.oncomplete = () => resolve();
                tx.onerror = () => reject(tx.error);
            });
        } catch (e) { console.warn('[NovelMaestro] Удаление из IndexedDB:', e && e.message); }
    }
    // зеркало хранилища origin в памяти: заполняется один раз при загрузке страницы
    let siteGlossaries = {};    // bookKey → объект глоссария
    let siteNerDone = {};       // bookKey → {pageCacheKey: ts}
    let siteChapterCache = {};  // bookKey → [{url, ts, data}] ≤ CACHE_CHAPTERS, в порядке чтения
    async function loadSiteData() {
        const db = await openDb();
        if (!db) return;
        try {
            const pairs = await new Promise((resolve, reject) => {
                const tx = db.transaction(DB_STORE, 'readonly');
                const store = tx.objectStore(DB_STORE);
                const kReq = store.getAllKeys();
                const vReq = store.getAll();
                tx.oncomplete = () => resolve(Array.from(kReq.result || [], (k, i) => [String(k), (vReq.result || [])[i]]));
                tx.onerror = () => reject(tx.error);
            });
            for (const [k, v] of pairs) {
                if (k.startsWith('g/')) siteGlossaries[k.slice(2)] = v || {};
                else if (k.startsWith('n/')) siteNerDone[k.slice(2)] = v || {};
                else if (k.startsWith('c/')) siteChapterCache[k.slice(2)] = v || [];
                else if (k.startsWith('j/')) siteJobs[k.slice(2)] = v || {};
            }
        } catch (e) { console.warn('[NovelMaestro] IndexedDB:', e && e.message); }
    }
    function bookGlossary(bookKey) {
        if (!bookKey) return {};
        if (!siteGlossaries[bookKey]) siteGlossaries[bookKey] = {};
        return siteGlossaries[bookKey];
    }
    // ===== КЭШ ГЛАВ (rolling: текущая + следующая) =====
    function cacheGet(url) {
        for (const list of Object.values(siteChapterCache)) {
            for (const e of list) if (e.url === url) return e.data;
        }
        return null;
    }
    function cacheSet(url, data, bookKey) {
        // пустые записи в кэш не попадают; частичный (прерванный) перевод не кэшируется
        if (!bookKey || !data || !data.text) return;
        const list = (siteChapterCache[bookKey] || []).filter(e => e.url !== url);
        list.push({ url, ts: Date.now(), data });
        // порядок — по времени перевода: при последовательном чтении он совпадает с порядком чтения
        list.sort((a, b) => a.ts - b.ts);
        while (list.length > CACHE_CHAPTERS) list.shift();
        siteChapterCache[bookKey] = list;
        dbPut('c/' + bookKey, list);
    }
    function cacheBookChapterCount(bookKey) { return (siteChapterCache[bookKey] || []).length; }
    function cacheClearBook(bookKey) {
        delete siteChapterCache[bookKey];
        dbDelete('c/' + bookKey);
        jobClear(bookKey);
    }

    // ===== ФОНОВАЯ РАБОТА: ПРОДОЛЖИТЬ, А НЕ НАЧАТЬ ЗАНОВО =====
    // Прерванный фоновый перевод (пользователь ушёл на следующую главу, пока чанки
    // не дошли) и прогресс NER хранятся отдельной записью на книгу — в rolling-кэш
    // частичные главы не попадают и не вытесняют готовые. Поле hash — хэш исходного
    // текста главы: текст изменился — частичные данные не релевантны, начать заново.
    // FNV-1a: длина текста важна, криптостойкость нет.
    function textHash(str) {
        let h = 0x811c9dc5;
        for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
        return h.toString(16);
    }
    let siteJobs = {};
    // запись живёт, пока относится к той же странице книги
    function jobOf(bookKey, url = pageCacheKey()) {
        const job = siteJobs[bookKey];
        return (job && job.url === url) ? job : null;
    }
    // поля дополняют друг друга: NER пишет nerDone/nerTotal, перевод — parts/total;
    // пустые поля не затираются (jobPut вызывается с одного места за раз)
    function jobPut(bookKey, job) {
        if (!bookKey) return;
        const prev = siteJobs[bookKey];
        const next = (prev && prev.url === job.url) ? { ...prev, ...job } : job;
        if (!next.parts) delete next.parts;
        if (!next.nerDone) delete next.nerDone;
        siteJobs[bookKey] = next;
        dbPut('j/' + bookKey, next);
    }
    function jobClear(bookKey) {
        if (!bookKey) return;
        delete siteJobs[bookKey];
        dbDelete('j/' + bookKey);
    }
    // NER закончил все чанки: снимаем его часть записи; если в этой же записи
    // лежит незаконченный перевод — она остаётся
    function jobClearNer(bookKey) {
        const job = siteJobs[bookKey];
        if (!job) return;
        delete job.nerDone;
        delete job.nerTotal;
        if ((job.parts || []).some(p => p)) dbPut('j/' + bookKey, job);
        else jobClear(bookKey);
    }

    // ===== ГЛОССАРИИ =====
    // Глобальный глоссарий убран: книга определяется по URL страницы, её глоссарий
    // лежит в IndexedDB этого сайта (siteGlossaries).
    function getGlossaryForView() {
        return currentBookKey && books[currentBookKey] ? bookGlossary(currentBookKey) : {};
    }
    function saveGlossary(glossary) {
        const key = currentBookKey;
        if (!key || !books[key]) return;
        siteGlossaries[key] = glossary;
        dbPut('g/' + key, glossary);
    }
    function getGlossaryForTranslation() {
        if (!currentBookKey || !books[currentBookKey]) return {};
        return { ...bookGlossary(currentBookKey) };
    }
    function genderOf(typeStr) {
        const t = String(typeStr || '').toLowerCase();
        if (t.includes('(female)')) return 'female';
        if (t.includes('(male)')) return 'male';
        if (t.includes('(unknown)')) return 'unknown';
        return '';
    }
    function withGender(typeStr, gender) {
        const base = String(typeStr || '').replace(/\s*\((?:male|female|unknown)\)\s*$/i, '').trim();
        if (!gender) return base;
        return `${base || 'Person'} (${gender})`;
    }
    const LEGACY_TYPE_MAP = { character: 'Person', creature: 'Creature', location: 'Location', artifact: 'Artifact', organization: 'Organisation', organisation: 'Organisation', term: 'Term', other: 'Other' };
    function migrateEntry(t) {
        if (!t || typeof t !== 'object') return t;
        const rawType = String(t.type || '').trim();
        const legacy = LEGACY_TYPE_MAP[rawType.toLowerCase()];
        let base = legacy || rawType.replace(/\s*\((?:male|female|unknown)\)\s*$/i, '').trim();
        let gender = genderOf(t.type);
        if (!gender && t.gender && t.gender !== 'null') gender = t.gender === 'neutral' ? 'unknown' : t.gender;
        if (!base) base = gender ? 'Person' : 'Term';
        t.type = (gender === 'male' || gender === 'female' || gender === 'unknown') ? `${base} (${gender})` : base;
        delete t.gender;
        return t;
    }
    function glossaryTypes(glossary) {
        const types = new Set();
        for (const t of Object.values(glossary || {})) {
            const ty = String(t && t.type || '').trim();
            if (ty) types.add(ty);
        }
        return [...types].sort((a, b) => a.localeCompare(b, 'ru'));
    }
    function updateTypeDatalist() {
        const dl = $('#nm-type-list');
        if (!dl) return;
        const suggestions = ['Person (male)', 'Person (female)', 'Person (unknown)',
            'Creature (male)', 'Creature (female)', 'Location', 'Artifact', 'Organisation', 'Term'];
        for (const ty of glossaryTypes(getGlossaryForView())) if (!suggestions.includes(ty)) suggestions.push(ty);
        dl.innerHTML = '';
        for (const ty of suggestions) {
            const opt = document.createElement('option');
            opt.value = ty;
            dl.appendChild(opt);
        }
    }
    function findRelevantTerms(text) {
        const glossary = getGlossaryForTranslation();
        const textNorm = normalize(text);
        if (!textNorm) return [];
        // n-граммы текста считаются один раз на чанк, а не на термин
        const textG = ngrams(textNorm);
        const unique = [];
        const seen = new Set();
        for (const [id, t] of Object.entries(glossary)) {
            if (!t || !t.term) continue; // битая запись глоссария не роняет перевод
            if (!termHitsText(t, textNorm, textG, config.fuzzySearchThreshold)) continue;
            const k = normalize(t.term);
            if (seen.has(k)) continue;
            seen.add(k);
            unique.push({ ...t, id });
        }
        return unique;
    }
    function formatGlossaryForPrompt(terms) {
        if (terms.length === 0) return '(глоссарий пуст)';
        return terms.map(t => `- "${t.term}" → "${t.translation}" [${t.type || 'Term'}]`).join('\n');
    }

    // ===== СИГНАТУРЫ ЭЛЕМЕНТОВ =====
    function cssEsc(s) { try { return CSS.escape(s); } catch { return s; } }
    function elementSignature(el) {
        const sig = {
            sel: generateCSSSelector(el),
            tag: el.tagName.toLowerCase(),
            id: el.id || null,
            classes: Array.from(el.classList).slice(0, 6),
            ancestors: []
        };
        let cur = el.parentElement;
        for (let i = 0; i < 4 && cur && cur !== document.body; i++) {
            sig.ancestors.push({ tag: cur.tagName.toLowerCase(), id: cur.id || null, classes: Array.from(cur.classList).slice(0, 6) });
            cur = cur.parentElement;
        }
        return sig;
    }
    function sigOwnSelector(sig) {
        if (sig.id) return `${sig.tag}#${cssEsc(sig.id)}`;
        if (sig.classes && sig.classes.length) return sig.tag + '.' + sig.classes.map(cssEsc).join('.');
        return sig.tag;
    }
    function sigAncestorPart(a) {
        if (a.id) return `${a.tag}#${cssEsc(a.id)}`;
        if (a.classes && a.classes.length) return a.tag + '.' + a.classes.map(cssEsc).join('.');
        return a.tag;
    }
    function tryQuery(root, sel) {
        try { return root.querySelector(sel); } catch { return null; }
    }
    function tryQueryAll(root, sel) {
        try { return Array.from(root.querySelectorAll(sel)); } catch { return []; }
    }
    function findBySignature(root, sig) {
        if (!sig) return null;
        if (typeof sig === 'string') return tryQuery(root, sig);
        if (sig.sel) { const el = tryQuery(root, sig.sel); if (el) return el; }
        const own = sigOwnSelector(sig);
        const el2 = tryQuery(root, own);
        if (el2) return el2;
        if (sig.ancestors && sig.ancestors.length) {
            const chain = sig.ancestors.slice().reverse().map(sigAncestorPart);
            for (let drop = 0; drop < chain.length; drop++) {
                const el3 = tryQuery(root, chain.slice(drop).concat([own]).join(' '));
                if (el3) return el3;
            }
        }
        if (sig.classes && sig.classes.length) {
            const el4 = tryQuery(root, '.' + sig.classes.map(cssEsc).join('.'));
            if (el4) return el4;
        }
        return null;
    }
    function findContainerByAncestors(root, sig) {
        if (!sig || typeof sig === 'string' || !sig.ancestors || !sig.ancestors.length) return null;
        const chain = sig.ancestors.slice().reverse().map(sigAncestorPart);
        for (let drop = 0; drop < chain.length; drop++) {
            const el = tryQuery(root, chain.slice(drop).join(' '));
            if (el) return el;
        }
        return null;
    }
    function textLen(el) {
        if (!el) return 0;
        return (el.innerText || el.textContent || '').length;
    }

    // ===== ИЗВЛЕЧЕНИЕ КОНТЕНТА =====
    const GENERIC_CONTENT_SELECTORS = [
        '.chapter-content', '.chapter-inner', '.txtnav', '.txt-content', '#txt-content',
        '#booktext', '.booktext', '#chaptercontent', '.chaptercontent', '#readcontent',
        '.readcontent', '#chapter-content', '.content-text', '#content-text',
        '.novel-content', '#novel-content', '.article-content', '#article-content',
        'article', '.post-content', '.entry-content', '.text', '.content',
        'main', '#content', '#main', '.chapter', '.reading-content',
        '.story-content', '.reader-content', '.entry', '.post',
        '#TextContent', '.TextContent', '#booktxt', '.booktxt',
        '#chapterbody', '.chapterbody', '#bookcontent', '.bookcontent'
    ];
    function findContentElementIn(root) {
        const sig = getBookSelectors().content;
        if (sig) {
            const candidates = [];
            const push = el => { if (el && !candidates.includes(el)) candidates.push(el); };
            if (typeof sig === 'string') {
                push(tryQuery(root, sig));
            } else {
                if (sig.sel) push(tryQuery(root, sig.sel));
                tryQueryAll(root, sigOwnSelector(sig)).forEach(push);
                if (sig.ancestors && sig.ancestors.length) {
                    const chain = sig.ancestors.slice().reverse().map(sigAncestorPart);
                    for (let drop = 0; drop < chain.length; drop++) {
                        push(tryQuery(root, chain.slice(drop).concat([sigOwnSelector(sig)]).join(' ')));
                    }
                }
            }
            let best = null, bestLen = 0;
            for (const c of candidates) {
                const len = textLen(c);
                if (len > bestLen) { bestLen = len; best = c; }
            }
            if (best && bestLen > 200) return best;
        }
        let bestEl = null, bestLen = 0;
        for (const s of GENERIC_CONTENT_SELECTORS) {
            for (const el of tryQueryAll(root, s)) {
                const len = textLen(el);
                if (len > bestLen) { bestLen = len; bestEl = el; }
            }
        }
        return bestEl || (root.body || root.documentElement);
    }
    function findContentElement() { return findContentElementIn(document); }

    const HIDE_SELECTORS = [
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'script', 'style',
        '.txtinfo', '.txtright', '.contentadv', '.bottom-ad', '.bottom-ad2',
        '.ad', '.ads', '.advertisement', '.hide720', '.tools', '.bread', '.page1',
        '.yueduad1', '.nav-buttons', '.portlet-title', '.actions'
    ];
    function extractMainText(element) {
        const hidden = [];
        for (const sel of HIDE_SELECTORS) {
            element.querySelectorAll(sel).forEach(el => {
                if (el.dataset.nmHidden) return;
                el.dataset.nmHidden = '1';
                el.style.setProperty('display', 'none', 'important');
                hidden.push(el);
            });
        }
        let raw = '';
        try { raw = element.innerText || ''; }
        finally { hidden.forEach(el => { el.style.removeProperty('display'); delete el.dataset.nmHidden; }); }
        return paragraphsOf(raw.replace(/\u00a0/g, ' ')).join('\n\n');
    }
    function extractTextFromDoc(doc, sig) {
        let root = sig ? findBySignature(doc, sig) : null;
        if (!root || textLen(root) < 200) {
            root = null;
            for (const s of GENERIC_CONTENT_SELECTORS) {
                const el = tryQuery(doc, s);
                if (el && textLen(el) > 200) { root = el; break; }
            }
        }
        if (!root) root = doc.body;
        if (!root) return '';
        root.querySelectorAll('script,style,noscript,iframe,form,.ad,.ads,.advertisement').forEach(el => el.remove());
        const ps = root.querySelectorAll('p');
        const paras = [];
        if (ps.length > 3) {
            ps.forEach(p => {
                const t = (p.textContent || '').replace(/\u00a0/g, ' ').trim();
                if (t) paras.push(t);
            });
        } else {
            paras.push(...paragraphsOf((root.textContent || '').replace(/\u00a0/g, ' ')));
        }
        return paras.join('\n\n');
    }

    // ===== НАВИГАЦИЯ =====
    const NAV_TEXT = {
        next: /next|впер[её]д|след|дал[её]е|→|»|>|下一页|下页|下一章|下章|继续|chương\s*sau|tiếp theo/i,
        prev: /prev(ious)?|назад|пред|←|«|<|上一页|上页|上一章|前章|chương\s*trước|trang trước/i,
        toc: /contents?|оглавл[её]н|содерж|index|toc|список глав|каталог|目录/i
    };
    function absHref(a, base) {
        if (!a) return null;
        const h = a.getAttribute && (a.getAttribute('href') || '');
        if (!h || h === '#' || /^javascript:/i.test(h)) return null;
        try { return new URL(h, base).href.split('#')[0]; } catch { return null; }
    }
    function pickLink(links, type) {
        const re = NAV_TEXT[type];
        if (!re) return null;
        for (const a of links) {
            const rel = (a.getAttribute && (a.getAttribute('rel') || '')).toLowerCase();
            if (type === 'next' && rel === 'next') return a;
            if (type === 'prev' && (rel === 'prev' || rel === 'previous')) return a;
        }
        for (const a of links) {
            const t = (a.textContent || '').trim();
            if (t && t.length <= 40 && re.test(t)) return a;
        }
        for (const a of links) {
            const cls = String(a.className || '') + ' ' + String(a.id || '');
            if (re.test(cls)) return a;
        }
        return null;
    }
    function resolveNavHref(root, sig, baseUrl, type) {
        if (sig) {
            const el = findBySignature(root, sig);
            if (el) {
                if (el.tagName === 'A') { const h = absHref(el, baseUrl); if (h) return h; }
                if (el.querySelectorAll) {
                    const links = Array.from(el.querySelectorAll('a'));
                    if (links.length === 1) { const h = absHref(links[0], baseUrl); if (h) return h; }
                    if (links.length > 1) {
                        const pick = pickLink(links, type);
                        if (pick) { const h = absHref(pick, baseUrl); if (h) return h; }
                    }
                }
            }
            const cont = findContainerByAncestors(root, sig);
            if (cont && cont.querySelectorAll) {
                const links = Array.from(cont.querySelectorAll('a'));
                const pick = pickLink(links, type) || (links.length === 1 ? links[0] : null);
                if (pick) { const h = absHref(pick, baseUrl); if (h) return h; }
            }
        }
        // toc — только по выученной сигнатуре: универсальной надёжной эвристики для
        // оглавления нет, а ложная кнопка хуже, чем её отсутствие
        if (type !== 'toc') {
            const rel = tryQuery(root, type === 'next' ? 'a[rel="next"]' : 'a[rel="prev"]');
            if (rel) { const h = absHref(rel, baseUrl); if (h) return h; }
            const pick = pickLink(Array.from(root.querySelectorAll ? root.querySelectorAll('a') : []), type);
            if (pick) { const h = absHref(pick, baseUrl); if (h) return h; }
        }
        return null;
    }
    function resolveNavFromLive() {
        const sel = getBookSelectors();
        return {
            nextUrl: resolveNavHref(document, sel.next, location.href, 'next'),
            prevUrl: resolveNavHref(document, sel.prev, location.href, 'prev'),
            tocUrl: resolveNavHref(document, sel.toc, location.href, 'toc')
        };
    }

    // ===== UI (SHADOW DOM) =====
    const styles = `
        <style>
            #nm-root, #nm-root * { letter-spacing: normal; word-spacing: normal; text-indent: 0; box-sizing: border-box; }
            #nm-root { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; font-size: 14px; color: #111827; }
            /* одна плавающая кнопка — меню; в покое полупрозрачна, чтобы не мешать
               чтению, полный цвет при наведении или фокусе */
            #nm-buttons { position: fixed; bottom: 20px; right: 20px; z-index: 2147483640; display: flex; gap: 8px; opacity: .55; transition: opacity .18s ease; }
            #nm-buttons:hover, #nm-buttons:focus-within { opacity: 1; }
            .nm-btn-float { background: #2563eb; color: white; border: none; padding: 12px 16px; border-radius: 8px; cursor: pointer; font-size: 20px; min-width: 50px; box-shadow: 0 4px 12px rgba(37,99,235,.3); transition: all .2s; }
            .nm-btn-float:hover { background: #1d4ed8; transform: translateY(-2px); }
            .nm-btn-float:disabled { background: #93c5fd; cursor: not-allowed; transform: none; }
            .nm-btn-float.nm-menu { background: #6b7280; }
            .nm-btn-float.nm-menu:hover { background: #4b5563; }
            .nm-menu-wrap { position: relative; }
            .nm-dropdown-menu { position: absolute; bottom: 58px; right: 0; background: white; border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,.25); padding: 6px; display: none; min-width: 240px; flex-direction: column; gap: 2px; }
            .nm-dropdown-menu.active { display: flex; }
            .nm-dropdown-item { padding: 10px 14px; border: none; background: none; text-align: left; cursor: pointer; border-radius: 6px; font-size: 14px; color: #111827; white-space: nowrap; }
            .nm-dropdown-item:hover { background: #f3f4f6; }
            .nm-modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 2147483647; }
            .nm-modal.active { display: flex; align-items: center; justify-content: center; }
            .nm-modal-content { background: white; border-radius: 12px; max-width: 900px; width: 95%; max-height: 90vh; overflow-y: auto; padding: 24px; box-shadow: 0 20px 60px rgba(0,0,0,.3); color: #111827; }
            .nm-tabs { display: flex; border-bottom: 2px solid #e5e7eb; margin-bottom: 20px; }
            .nm-tab { padding: 10px 20px; cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -2px; }
            .nm-tab.active { border-bottom-color: #2563eb; color: #2563eb; font-weight: 600; }
            .nm-tab-content { display: none; }
            .nm-tab-content.active { display: block; }
            .nm-input-group { margin-bottom: 16px; }
            .nm-input-group label { display: block; margin-bottom: 6px; font-weight: 500; color: #374151; }
            .nm-input-group small { display: block; margin-top: 4px; color: #6b7280; font-size: 12px; }
            .nm-input, .nm-textarea, .nm-select { width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; background: white; color: #111827; }
            .nm-textarea { min-height: 110px; resize: vertical; font-family: Consolas, Monaco, monospace; line-height: 1.4; }
            .nm-btn { padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 500; margin-right: 8px; margin-top: 8px; }
            .nm-btn-sm { padding: 6px 12px; font-size: 13px; }
            .nm-btn-primary { background: #2563eb; color: white; }
            .nm-btn-primary:hover { background: #1d4ed8; }
            .nm-btn-primary:disabled { background: #93c5fd; cursor: not-allowed; }
            .nm-btn-secondary { background: #6b7280; color: white; }
            .nm-btn-secondary:hover { background: #4b5563; }
            .nm-btn-danger { background: #dc2626; color: white; }
            .nm-btn-danger:hover { background: #b91c1c; }
            .nm-btn-success { background: #059669; color: white; }
            .nm-btn-success:hover { background: #047857; }
            .nm-glossary-table { width: 100%; border-collapse: collapse; margin-top: 8px; }
            .nm-glossary-table th { background: #f3f4f6; padding: 8px 10px; text-align: left; font-size: 12px; font-weight: 600; color: #374151; border-bottom: 2px solid #e5e7eb; cursor: pointer; user-select: none; white-space: nowrap; }
            .nm-glossary-table th:hover { background: #e5e7eb; }
            .nm-glossary-table th .nm-sort { font-size: 10px; margin-left: 4px; color: #9ca3af; }
            .nm-glossary-table th.active-sort { background: #dbeafe; color: #1e40af; }
            .nm-glossary-table th.active-sort .nm-sort { color: #2563eb; }
            .nm-glossary-table td { padding: 6px 8px; border-bottom: 1px solid #e5e7eb; vertical-align: middle; }
            .nm-glossary-table tr:hover td { background: #f9fafb; }
            .nm-glossary-table input, .nm-glossary-table select { padding: 5px 6px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 13px; background: white; width: 100%; color: #111827; }
            .nm-glossary-table input:focus, .nm-glossary-table select:focus { outline: none; border-color: #2563eb; box-shadow: 0 0 0 2px rgba(37,99,235,.1); }
            .nm-delete-cell { background: #dc2626; color: white; border: none; padding: 5px 8px; border-radius: 4px; cursor: pointer; font-size: 12px; }
            .nm-count-cell { text-align: center; font-weight: 600; color: #6b7280; }
            .nm-count-cell.high { color: #059669; }
            .nm-count-cell.med { color: #d97706; }
            .nm-status { padding: 12px; border-radius: 6px; margin-top: 12px; display: none; }
            .nm-status.success { background: #d1fae5; color: #065f46; display: block; }
            .nm-status.error { background: #fee2e2; color: #991b1b; display: block; }
            .nm-status.info { background: #dbeafe; color: #1e40af; display: block; }
            .nm-close { float: right; background: none; border: none; font-size: 24px; cursor: pointer; color: #6b7280; }
            .nm-help { background: #f3f4f6; padding: 12px; border-radius: 6px; font-size: 13px; color: #6b7280; margin-bottom: 16px; }
            .nm-help code { background: rgba(128,128,128,.15); padding: 1px 4px; border-radius: 3px; }
            .nm-glossary-count { background: #2563eb; color: white; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-left: 8px; }
            .nm-add-form { display: grid; grid-template-columns: 1fr 1fr 1.4fr auto; gap: 8px; padding: 12px; background: #eff6ff; border-radius: 6px; border: 1px solid #bfdbfe; margin-bottom: 12px; }
            .nm-add-form input, .nm-add-form select { padding: 8px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 13px; background: white; color: #111827; }
            .nm-filter-row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
            .nm-filter-row input { flex: 1; padding: 8px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 13px; background: white; color: #111827; }
            .nm-pagination { display: flex; gap: 8px; align-items: center; justify-content: center; margin-top: 12px; flex-wrap: wrap; }
            .nm-pagination button { padding: 6px 12px; border: 1px solid #d1d5db; background: white; border-radius: 4px; cursor: pointer; font-size: 13px; color: #111827; }
            .nm-pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
            .nm-pagination button.active { background: #2563eb; color: white; border-color: #2563eb; }
            .nm-pagination .nm-page-info { color: #6b7280; font-size: 13px; }
            .nm-progress-bar { width: 100%; height: 6px; background: rgba(128,128,128,.25); border-radius: 3px; overflow: hidden; }
            .nm-progress-fill { height: 100%; background: linear-gradient(90deg,#2563eb,#3b82f6); transition: width .3s; width: 0%; }
            .nm-progress-fill.retry { background: repeating-linear-gradient(45deg, #f59e0b 0 10px, #fbbf24 10px 20px); background-size: 28.3px 28.3px; animation: nm-retry-stripes .8s linear infinite; }
            @keyframes nm-retry-stripes { to { background-position: 28.3px 0; } }
            .nm-input:disabled { background: #f3f4f6; color: #9ca3af; cursor: not-allowed; }
            .nm-checkbox-group { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
            .nm-checkbox-group input { width: 18px; height: 18px; cursor: pointer; }
            .nm-checkbox-group label { margin: 0; cursor: pointer; }
            .nm-section { background: #f9fafb; padding: 16px; border-radius: 8px; margin-bottom: 16px; }
            .nm-section h3 { margin: 0 0 12px 0; font-size: 16px; color: #1f2937; }
            .nm-toolbar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
            .nm-toolbar .nm-btn { margin: 0; }
            .nm-book-info { background: #eff6ff; padding: 12px; border-radius: 6px; margin-bottom: 16px; border-left: 4px solid #2563eb; }
            .nm-url-edit { display: flex; gap: 8px; align-items: center; }
            .nm-url-edit input { flex: 1; }
            .nm-url-edit .nm-btn { margin: 0; }
            .nm-radio-group { display: flex; gap: 16px; margin-top: 8px; flex-wrap: wrap; }
            .nm-radio-group label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
            .nm-server-status { margin-top: 8px; padding: 8px 12px; border-radius: 4px; font-size: 13px; display: none; }
            .nm-server-status.show { display: block; }
            .nm-server-status.ok { background: #d1fae5; color: #065f46; }
            .nm-server-status.err { background: #fee2e2; color: #991b1b; }
            .nm-server-status.loading { background: #fef3c7; color: #92400e; }

            /* ===== ТЁМНЫЙ UI (меню, модалки, попапы) ===== */
            #nm-root.nm-ui-dark, #nm-root.nm-ui-dark .nm-modal-content { color: #e2e2dc; }
            #nm-root.nm-ui-dark .nm-dropdown-menu { background: #1f232b; }
            #nm-root.nm-ui-dark .nm-dropdown-item { color: #e2e2dc; }
            #nm-root.nm-ui-dark .nm-dropdown-item:hover { background: #2a2f39; }
            #nm-root.nm-ui-dark .nm-modal-content { background: #1f232b; }
            #nm-root.nm-ui-dark .nm-tabs { border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-tab { color: #b9bdc6; }
            #nm-root.nm-ui-dark .nm-tab.active { color: #7fb0ff; border-bottom-color: #7fb0ff; }
            #nm-root.nm-ui-dark .nm-input-group label { color: #c6c9d0; }
            #nm-root.nm-ui-dark .nm-input-group small { color: #8b909a; }
            #nm-root.nm-ui-dark .nm-input, #nm-root.nm-ui-dark .nm-textarea, #nm-root.nm-ui-dark .nm-select { background: #2a2f39; color: #e2e2dc; border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-input:disabled { background: #23272e; color: #7d828c; }
            #nm-root.nm-ui-dark .nm-section { background: #262b34; }
            #nm-root.nm-ui-dark .nm-section h3 { color: #e2e2dc; }
            #nm-root.nm-ui-dark .nm-help { background: #262b34; color: #9aa0aa; }
            #nm-root.nm-ui-dark .nm-add-form { background: #232833; border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-add-form input, #nm-root.nm-ui-dark .nm-add-form select { background: #2a2f39; color: #e2e2dc; border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-filter-row input { background: #2a2f39; color: #e2e2dc; border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-glossary-table th { background: #2a2f39; color: #c6c9d0; border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-glossary-table th:hover { background: #31363f; }
            #nm-root.nm-ui-dark .nm-glossary-table th.active-sort { background: #1c2c4a; color: #a8c6ff; }
            #nm-root.nm-ui-dark .nm-glossary-table td { border-color: #31363f; }
            #nm-root.nm-ui-dark .nm-glossary-table tr:hover td { background: #262b34; }
            #nm-root.nm-ui-dark .nm-glossary-table input, #nm-root.nm-ui-dark .nm-glossary-table select { background: #2a2f39; color: #e2e2dc; border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-count-cell { color: #9aa0aa; }
            #nm-root.nm-ui-dark .nm-pagination button { background: #2a2f39; color: #e2e2dc; border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-pagination button.active { background: #2563eb; color: white; }
            #nm-root.nm-ui-dark .nm-pagination .nm-page-info { color: #9aa0aa; }
            #nm-root.nm-ui-dark .nm-book-info { background: #232833; }
            #nm-root.nm-ui-dark .nm-close { color: #9aa0aa; }
            #nm-root.nm-ui-dark .nm-status.success { background: #123524; color: #7ee2b8; }
            #nm-root.nm-ui-dark .nm-status.error { background: #3d1d1d; color: #f3b4b4; }
            #nm-root.nm-ui-dark .nm-status.info { background: #1c2c4a; color: #a8c6ff; }
            #nm-root.nm-ui-dark .nm-server-status.ok { background: #123524; color: #7ee2b8; }
            #nm-root.nm-ui-dark .nm-server-status.err { background: #3d1d1d; color: #f3b4b4; }
            #nm-root.nm-ui-dark .nm-server-status.loading { background: #3d3116; color: #e8c37a; }
            #nm-root.nm-ui-dark .nm-training-instructions, #nm-root.nm-ui-dark .nm-training-popup { background: #1f232b; color: #e2e2dc; }
            #nm-root.nm-ui-dark .nm-training-popup h4 { color: #c6c9d0; }

            /* ===== ЧИТАЛКА ===== */
            #nm-reader-mode { display: none; position: fixed; inset: 0; z-index: 2147483640; overflow-y: auto; overflow-x: hidden; }
            #nm-reader-mode.active { display: block; }
            #nm-reader-mode.nm-reader-light { background: #faf7f0; color: #26221c; }
            #nm-reader-mode.nm-reader-dark { background: #16181d; color: #d8d8d3; }
            .nm-reader-topbar { position: fixed; top: 0; left: 0; right: 0; z-index: 5; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 16px; backdrop-filter: blur(6px); }
            #nm-reader-mode.nm-reader-light .nm-reader-topbar { background: rgba(250,247,240,.92); border-bottom: 1px solid #e5ded2; }
            #nm-reader-mode.nm-reader-dark .nm-reader-topbar { background: rgba(22,24,29,.92); border-bottom: 1px solid #2a2d35; }
            .nm-reader-title { font-size: 14px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
            .nm-reader-topbar-buttons { display: flex; gap: 6px; flex-shrink: 0; }
            .nm-reader-topbar-buttons button { border: none; border-radius: 6px; cursor: pointer; font-size: 15px; padding: 6px 10px; }
            #nm-reader-mode.nm-reader-light .nm-reader-topbar-buttons button { background: #e8e2d6; color: #26221c; }
            #nm-reader-mode.nm-reader-dark .nm-reader-topbar-buttons button { background: #2a2d35; color: #d8d8d3; }
            .nm-reader-menu-panel { display: none; position: absolute; top: calc(100% + 2px); right: 10px; flex-direction: column; gap: 4px; padding: 8px; border-radius: 10px; z-index: 6; min-width: 210px; }
            .nm-reader-menu-panel.active { display: flex; }
            .nm-reader-menu-panel button { border: none; border-radius: 6px; cursor: pointer; font-size: 14px; padding: 8px 10px; text-align: left; white-space: nowrap; }
            #nm-reader-mode.nm-reader-light .nm-reader-menu-panel { background: rgba(250,247,240,.97); border: 1px solid #e5ded2; box-shadow: 0 6px 20px rgba(0,0,0,.15); }
            #nm-reader-mode.nm-reader-dark .nm-reader-menu-panel { background: rgba(22,24,29,.97); border: 1px solid #2a2d35; box-shadow: 0 6px 20px rgba(0,0,0,.4); }
            #nm-reader-mode.nm-reader-light .nm-reader-menu-panel button { background: #e8e2d6; color: #26221c; }
            #nm-reader-mode.nm-reader-dark .nm-reader-menu-panel button { background: #2a2d35; color: #d8d8d3; }
            .nm-current-only { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: #6b7280; white-space: nowrap; cursor: pointer; }
            .nm-reader-content { margin: 0 auto; padding: 70px 20px 150px; max-width: var(--nm-content-width, 66%); }
            .nm-reader-content p { text-align: justify; }
            .nm-reader-loading { text-align: center; padding: 60px 0; font-size: 16px; opacity: .7; }
            .nm-reader-bottombar { position: fixed; bottom: 0; left: 0; right: 0; z-index: 5; display: flex; flex-direction: column; gap: 8px; align-items: center; padding: 10px 14px 12px; backdrop-filter: blur(6px); }
            #nm-reader-mode.nm-reader-light .nm-reader-bottombar { background: rgba(250,247,240,.92); border-top: 1px solid #e5ded2; }
            #nm-reader-mode.nm-reader-dark .nm-reader-bottombar { background: rgba(22,24,29,.92); border-top: 1px solid #2a2d35; }
            .nm-reader-progress { width: min(680px, 94%); display: none; flex-direction: column; gap: 6px; font-size: 13px; }
            .nm-reader-progress.active { display: flex; }
            .nm-rp-row { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
            #reader-progress-title { font-weight: 600; }
            #reader-cancel { border: 1px solid rgba(200,80,80,.6); color: #b3403a; background: transparent; border-radius: 6px; padding: 4px 12px; cursor: pointer; font-size: 12px; }
            #nm-reader-mode.nm-reader-dark #reader-cancel { color: #e08585; border-color: rgba(224,133,133,.5); }
            #reader-cancel:hover { background: rgba(200,80,80,.12); }
            #reader-progress-status { opacity: .75; font-size: 12px; }
            .nm-reader-nav { display: flex; gap: 10px; justify-content: center; align-items: center; flex-wrap: wrap; }
            .nm-reader-nav button { padding: 9px 20px; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 500; background: transparent; border: 1px solid; transition: background .15s; }
            #nm-reader-mode.nm-reader-light .nm-reader-nav button { color: #4a443b; border-color: #d3cabb; background: rgba(255,255,255,.45); }
            #nm-reader-mode.nm-reader-light .nm-reader-nav button:hover { background: #efe9dd; }
            #nm-reader-mode.nm-reader-dark .nm-reader-nav button { color: #c6c6bf; border-color: #3a3d46; background: rgba(255,255,255,.04); }
            #nm-reader-mode.nm-reader-dark .nm-reader-nav button:hover { background: #23262e; }
            .nm-reader-nav button:disabled { opacity: .35; cursor: not-allowed; }
            #reader-preload-status { font-size: 12px; opacity: .65; display: none; }

            /* ===== ОБУЧЕНИЕ ===== */
            #nm-element-training { display: none; position: fixed; inset: 0; z-index: 2147483645; pointer-events: none; }
            #nm-element-training.active { display: block; }
            .nm-training-instructions { pointer-events: auto; position: fixed; top: 16px; left: 50%; transform: translateX(-50%); background: white; color: #111827; padding: 14px 20px; border-radius: 10px; box-shadow: 0 6px 24px rgba(0,0,0,.35); font-size: 13px; max-width: 640px; text-align: center; z-index: 10; }
            .nm-training-instructions h3 { margin: 0 0 6px 0; font-size: 15px; }
            .nm-training-instructions .nm-btn { margin-top: 10px; }
            .nm-training-popup { pointer-events: auto; position: fixed; background: white; color: #111827; padding: 14px; border-radius: 10px; box-shadow: 0 8px 28px rgba(0,0,0,.35); z-index: 11; display: none; min-width: 230px; }
            .nm-training-popup.active { display: block; }
            .nm-training-popup h4 { margin: 0 0 10px 0; font-size: 13px; color: #374151; }
            .nm-training-buttons { display: flex; flex-direction: column; gap: 6px; }
            .nm-training-buttons button { padding: 9px 14px; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; text-align: left; color: white; }
            .nm-training-buttons button:hover { opacity: .9; }

            /* ===== МОБИЛЬНАЯ АДАПТАЦИЯ =====
               указатель coarse ловит телефон даже в режиме «полной версии сайта» */
            @media (max-width: 768px), (pointer: coarse) {
                #nm-buttons { bottom: 12px; right: 12px; bottom: calc(12px + env(safe-area-inset-bottom)); }
                /* меньше стала, но осталась в пределах касания (44px) */
                .nm-btn-float { min-width: 44px; min-height: 44px; font-size: 20px; padding: 10px 12px; }
                .nm-dropdown-menu { min-width: 0; width: min(320px, calc(100vw - 24px)); bottom: 66px; }
                .nm-dropdown-item { white-space: normal; padding: 12px 14px; }
                .nm-modal.active { align-items: stretch; justify-content: stretch; }
                .nm-modal-content { width: 100%; max-width: 100%; height: 100%; max-height: 100%; border-radius: 0; padding: 14px; padding: calc(14px + env(safe-area-inset-top)) 14px calc(14px + env(safe-area-inset-bottom)); }
                .nm-tabs { overflow-x: auto; flex-wrap: nowrap; }
                .nm-tab { flex-shrink: 0; white-space: nowrap; padding: 10px 14px; }
                .nm-input, .nm-textarea, .nm-select, .nm-glossary-table input, .nm-glossary-table select, .nm-add-form input, .nm-add-form select, .nm-filter-row input { font-size: 16px; }
                .nm-add-form { grid-template-columns: 1fr; }
                #glossary-list { overflow-x: auto; -webkit-overflow-scrolling: touch; }
                .nm-glossary-table { min-width: 620px; }
                .nm-reader-topbar { padding: 8px 10px; padding: calc(8px + env(safe-area-inset-top)) 10px 8px; }
                .nm-reader-bottombar { padding: 8px 10px 10px; padding: 8px 10px calc(10px + env(safe-area-inset-bottom)); }
                #nm-reader-mode .nm-reader-content { max-width: 100%; padding: 60px 12px 120px; padding-top: calc(60px + env(safe-area-inset-top)); }
                .nm-reader-nav { gap: 6px; }
                /* на телефоне панель — компактнее и только иконками (подписи скрыты) */
                .nm-reader-nav button { min-height: 40px; padding: 8px 14px; font-size: 15px; flex: 0 1 auto; }
                .nm-reader-nav .nm-nav-label { display: none; }
                .nm-training-instructions { max-width: calc(100vw - 16px); top: 8px; padding: 8px 10px; font-size: 11px; }
                .nm-training-instructions h3 { font-size: 13px; margin-bottom: 4px; }
                .nm-training-instructions .nm-btn { padding: 7px 12px; font-size: 12px; margin-top: 6px; }
                .nm-training-popup { max-width: calc(100vw - 16px); min-width: 200px; }
                .nm-training-buttons button { padding: 9px 10px; font-size: 12px; }
            }
        </style>
    `;

    (function injectPageTrainStyle() {
        if (document.getElementById('nm-page-train-style')) return;
        const st = document.createElement('style');
        st.id = 'nm-page-train-style';
        st.textContent = `
            .nm-training-highlight { outline: 3px solid #2563eb !important; outline-offset: 2px; background-color: rgba(37,99,235,.12) !important; cursor: crosshair !important; }
            .nm-training-picked { outline: 3px solid #059669 !important; outline-offset: 2px; }
            body.nm-training-on { -webkit-user-select: none; user-select: none; -webkit-touch-callout: none; }
        `;
        document.head.appendChild(st);
    })();

    const host = document.createElement('div');
    host.id = 'nm-lite-host';
    document.documentElement.appendChild(host);
    const shadow = host.attachShadow({ mode: 'open' });
    const uiFrag = document.createRange().createContextualFragment(`
        ${styles}
        <div id="nm-root">
            <div id="nm-buttons">
                <div class="nm-menu-wrap">
                    <button class="nm-btn-float nm-menu" id="btn-menu" title="NovelMaestro Lite">⋮</button>
                    <div class="nm-dropdown-menu" id="dropdown-menu">
                        <button class="nm-dropdown-item" id="btn-translate">🌐 Перевести / читать</button>
                        <button class="nm-dropdown-item" id="btn-extract-menu">✨ Извлечь термины вручную</button>
                        <button class="nm-dropdown-item" id="btn-book-menu">➕ Добавить книгу</button>
                        <button class="nm-dropdown-item" id="btn-train-menu">🎯 Обучить элементам</button>
                        <button class="nm-dropdown-item" id="btn-settings-menu">⚙️ Настройки</button>
                    </div>
                </div>
            </div>

            <div class="nm-modal" id="nm-modal">
                <div class="nm-modal-content">
                    <button class="nm-close" id="nm-close">&times;</button>
                    <h2 style="margin-top:0;">NovelMaestro Lite <span style="font-size:12px;color:#9ca3af;font-weight:400;">v${APP_VERSION}</span></h2>
                    <div class="nm-tabs">
                        <div class="nm-tab active" data-tab="book">📚 Книга</div>
                        <div class="nm-tab" data-tab="glossary">✨ Глоссарий <span class="nm-glossary-count" id="glossary-count">0</span></div>
                        <div class="nm-tab" data-tab="settings">⚙️ Настройки</div>
                    </div>
                    <div class="nm-tab-content active" id="tab-book">
                        <div class="nm-section">
                            <h3>Управление книгами</h3>
                            <div class="nm-input-group">
                                <label>Выбрать книгу:</label>
                                <select class="nm-select" id="book-select"></select>
                            </div>
                            <div id="book-manage-area"></div>
                        </div>
                        <div class="nm-status" id="status-book"></div>
                    </div>
                    <div class="nm-tab-content" id="tab-glossary">
                        <div class="nm-help" id="glossary-book-hint" style="display:none;">📚 У каждой книги свой глоссарий; он хранится в памяти браузера для сайта книги. Откройте страницу книги — она определяется по заданному URL (например <code>…/fiction/58180/…</code>) — и глоссарий появится здесь.</div>
                        <div id="glossary-body">
                        <div class="nm-add-form">
                            <input type="text" id="new-term" placeholder="Термин">
                            <input type="text" id="new-translation" placeholder="Перевод">
                            <input type="text" id="new-type" list="nm-type-list" placeholder="Тип (Person (male), Location…)" value="Person (male)">
                            <datalist id="nm-type-list"></datalist>
                            <button class="nm-btn nm-btn-primary" id="btn-add-term" style="margin:0;">+ Добавить</button>
                        </div>
                        <div class="nm-toolbar">
                            <button class="nm-btn nm-btn-success" id="btn-extract-terms">✨ Извлечь термины со страницы</button>
                            <button class="nm-btn nm-btn-secondary" id="btn-import">📥 Импорт</button>
                            <button class="nm-btn nm-btn-secondary" id="btn-export">📤 Экспорт</button>
                            <button class="nm-btn nm-btn-danger" id="btn-clear-glossary">🗑 Очистить</button>
                        </div>
                        <div class="nm-filter-row">
                            <input type="text" id="glossary-filter" placeholder="🔍 Фильтр по термину, переводу или типу...">
                            <label class="nm-current-only" title="Показывать только термины, которые встречаются на открытой странице (тот же матчинг, что и при переводе)">
                                <input type="checkbox" id="glossary-current-only"> только текущая страница
                            </label>
                        </div>
                        <div id="glossary-list"></div>
                        <div class="nm-pagination" id="glossary-pagination"></div>
                        <div class="nm-status" id="status-glossary"></div>
                        </div>
                    </div>
                    <div class="nm-tab-content" id="tab-settings">
                        <div class="nm-section">
                            <h3>🤖 API</h3>
                            <div class="nm-input-group"><label>API Host:</label><input type="text" class="nm-input" id="api-host"></div>
                            <div class="nm-input-group"><label>API Key:</label><input type="password" class="nm-input" id="api-key">
                                <small>Пустой API Key разрешён при включённом чекбоксе ниже.</small>
                            </div>
                            <div class="nm-checkbox-group">
                                <input type="checkbox" id="local-model">
                                <label for="local-model">🖥️ Локальная модель без API-ключа</label>
                            </div>
                            <div class="nm-input-group"><label>Модель:</label><input type="text" class="nm-input" id="model"></div>
                            <div class="nm-input-group"><label>Уровень reasoning:</label>
                                <input type="text" class="nm-input" id="reasoning-effort" list="nm-reasoning-list" placeholder="None">
                                <datalist id="nm-reasoning-list">
                                    <option value="None"></option><option value="minimal"></option><option value="low"></option>
                                    <option value="medium"></option><option value="high"></option>
                                </datalist>
                                <small>Пусто или 'None' — параметр не передаётся.</small>
                            </div>
                            <button class="nm-btn nm-btn-sm nm-btn-primary" id="btn-check-server">🔌 Проверить сервер</button>
                            <div class="nm-server-status" id="server-status"></div>
                        </div>
                        <div class="nm-section">
                            <h3>📝 Перевод</h3>
                            <div class="nm-input-group"><label>Исходный язык:</label>
                                <select class="nm-select" id="source-lang">
                                    <option value="Авто">Авто</option><option value="Китайский">Китайский</option>
                                    <option value="Английский">Английский</option><option value="Японский">Японский</option>
                                    <option value="Корейский">Корейский</option><option value="Русский">Русский</option>
                                </select>
                            </div>
                            <div class="nm-input-group"><label>Целевой язык:</label>
                                <select class="nm-select" id="target-lang">
                                    <option value="Русский">Русский</option><option value="Английский">Английский</option>
                                    <option value="Испанский">Испанский</option><option value="Французский">Французский</option>
                                    <option value="Немецкий">Немецкий</option><option value="Японский">Японский</option>
                                    <option value="Корейский">Корейский</option><option value="Китайский">Китайский</option>
                                </select>
                            </div>
                            <div class="nm-input-group"><label>Размер чанка (символов):</label>
                                <input type="number" class="nm-input" id="chunk-size" min="1000" max="100000" step="1000">
                            </div>
                            <div class="nm-checkbox-group">
                                <input type="checkbox" id="preemptive-translate">
                                <label for="preemptive-translate">🚀 Автоперевод следующей главы в фоне</label>
                            </div>
                            <small>Переведённые главы (текущая и следующая) кэшируются в памяти браузера этого сайта — из них работают мгновенное открытие с кэшированной главы и экспорт TXT (кнопка в читалке).</small>
                        </div>
                        <div class="nm-section">
                            <h3>🌐 Сеть</h3>
                            <div class="nm-input-group"><label>Таймаут (с):</label>
                                <input type="number" class="nm-input" id="request-timeout" min="0" step="1">
                                <small>СЕК. 0 = без таймаута. При стриминге это пауза между токенами: ни одного символа за это время — запрос считается зависшим и повторяется. У запроса без стрима (например, «Проверить сервер») это ожидание всего ответа: локальная модель на телефоне легко думает дольше 10 секунд. По умолчанию 60.</small>
                            </div>
                            <div class="nm-input-group"><label>Количество ретраев при ошибке:</label>
                                <input type="number" class="nm-input" id="max-retries" min="0" max="10">
                                <small>Повторные попытки при сетевых ошибках, таймаутах и зависании стриминга (не при HTTP 4xx/5xx).</small>
                            </div>
                            <div class="nm-checkbox-group">
                                <input type="checkbox" id="gm-transport">
                                <label for="gm-transport">Весь трафик только каналом менеджера (GM_xmlhttpRequest)</label>
                                <small>Галочка — старые порядки (всё через менеджер). По умолчанию снята: запросы идут из страницы, канал менеджера подключается сам при CORS-ошибке.</small>
                            </div>
                        </div>
                        <div class="nm-section">
                            <h3>🔍 Глоссарий</h3>
                            <div class="nm-input-group"><label>Порог нечеткого поиска (0.0 - 1.0):</label>
                                <input type="number" class="nm-input" id="fuzzy-threshold" min="0" max="1" step="0.05">
                            </div>
                            <div class="nm-checkbox-group">
                                <input type="checkbox" id="auto-ner">
                                <label for="auto-ner">Автоизвлечение терминов (один раз на страницу)</label>
                            </div>
                        </div>
                        <div class="nm-section">
                            <h3>📖 Читалка</h3>
                            <div class="nm-input-group"><label>Тема:</label>
                                <select class="nm-select" id="reader-theme">
                                    <option value="auto">🌗 Как в системе</option>
                                    <option value="light">☀️ Светлая</option>
                                    <option value="dark">🌙 Тёмная</option>
                                </select>
                            </div>
                            <div class="nm-input-group"><label>Шрифт:</label>
                                <select class="nm-select" id="reader-font-family">
                                    <option value="Georgia, serif">Georgia (serif)</option>
                                    <option value="Arial, sans-serif">Arial (sans-serif)</option>
                                    <option value="'Times New Roman', serif">Times New Roman</option>
                                    <option value="Verdana, sans-serif">Verdana</option>
                                    <option value="'Segoe UI', sans-serif">Segoe UI</option>
                                </select>
                            </div>
                            <div class="nm-input-group"><label>Размер шрифта (px):</label>
                                <input type="number" class="nm-input" id="reader-font-size" min="12" max="32" step="1">
                            </div>
                            <div class="nm-input-group"><label>Межстрочный интервал:</label>
                                <input type="number" class="nm-input" id="reader-line-height" min="1" max="3" step="0.1">
                            </div>
                            <div class="nm-input-group"><label>Отступ между абзацами (em):</label>
                                <input type="number" class="nm-input" id="reader-paragraph-spacing" min="0.2" max="4" step="0.1">
                            </div>
                            <div class="nm-input-group"><label>Ширина колонки (% экрана):</label>
                                <input type="number" class="nm-input" id="reader-content-width" min="30" max="100" step="5">
                            </div>
                        </div>
                        <div class="nm-section">
                            <h3>💬 Промпты</h3>
                            <div class="nm-input-group"><label>Промпт перевода ({sourceLang}, {targetLang}, {glossary}, {text}):</label>
                                <textarea class="nm-textarea" id="translation-prompt"></textarea>
                            </div>
                            <div class="nm-input-group"><label>Промпт извлечения терминов ({targetLang}, {text}):</label>
                                <textarea class="nm-textarea" id="extraction-prompt"></textarea>
                            </div>
                        </div>
                        <div class="nm-help" style="margin-bottom:8px;">ℹ️ Настройки сохраняются автоматически при каждом изменении.</div>
                        <button class="nm-btn nm-btn-secondary" id="btn-reset-settings">Сбросить настройки</button>
                        <div class="nm-status" id="status-settings"></div>
                    </div>
                </div>
            </div>

            <div class="nm-modal" id="nm-book-modal">
                <div class="nm-modal-content" style="max-width:640px;">
                    <h2 style="margin-top:0;">📚 Определение книги</h2>
                    <div class="nm-help">
                        Укажите <b>постоянную часть URL книги</b> — ту, которая НЕ меняется при переходе от главы к главе.<br><br>
                        Примеры:<br>
                        • <code>…/fiction/58180/death-after-death-…/chapter/982968/ch-01-…</code> → <code>https://www.royalroad.com/fiction/58180/death-after-death-roguelike-isekai</code><br>
                        • <code>…/n/cp61433/cpplpnhk?chapterNumber=3</code> → <code>https://czbooks.net/n/cp61433/cpplpnhk</code><br>
                        • <code>…/txt/88724/41021619</code> и <code>…/txt/88724/41021865</code> → <code>https://www.69shuba.com/txt/88724</code>
                    </div>
                    <div class="nm-input-group"><label>URL книги:</label>
                        <div class="nm-url-edit">
                            <input type="text" class="nm-input" id="book-modal-url">
                            <button class="nm-btn nm-btn-secondary" id="btn-autofill-url">🔍 Авто</button>
                        </div>
                    </div>
                    <div class="nm-input-group"><label>Название книги:</label>
                        <input type="text" class="nm-input" id="book-modal-name">
                    </div>
                    <button class="nm-btn nm-btn-primary" id="btn-save-new-book">💾 Сохранить</button>
                    <button class="nm-btn nm-btn-secondary" id="btn-cancel-new-book">Отмена</button>
                </div>
            </div>

            <!-- ЧИТАЛКА -->
            <div id="nm-reader-mode">
                <div class="nm-reader-topbar">
                    <div class="nm-reader-title" id="reader-title"></div>
                    <div class="nm-reader-topbar-buttons">
                        <button id="reader-menu" title="Меню читалки">⋮</button>
                        <button id="reader-close" title="Закрыть читалку">✕</button>
                    </div>
                    <div class="nm-reader-menu-panel" id="reader-menu-panel">
                        <button id="reader-export" title="Сохранить перевод текущей главы в TXT">📄 Экспорт TXT</button>
                        <button id="reader-retranslate" title="Перевести текущую главу заново (игнорирует кэш)">🌐 Перевести заново</button>
                        <button id="reader-theme-toggle" title="Сменить тему">🌓 Тема</button>
                        <button id="reader-settings" title="Настройки">⚙️ Настройки</button>
                    </div>
                </div>
                <div class="nm-reader-content" id="reader-content"></div>
                <div class="nm-reader-bottombar">
                    <div class="nm-reader-progress" id="reader-progress">
                        <div class="nm-rp-row">
                            <span id="reader-progress-title">🔄 Перевод...</span>
                            <button id="reader-cancel">Отменить</button>
                        </div>
                        <div class="nm-progress-bar"><div class="nm-progress-fill" id="reader-progress-fill"></div></div>
                        <div id="reader-progress-status">Подготовка...</div>
                    </div>
                    <div class="nm-reader-nav">
                        <button id="reader-prev" title="Предыдущая глава">←<span class="nm-nav-label"> Предыдущая</span></button>
                        <button id="reader-toc" title="Оглавление">☰<span class="nm-nav-label"> Оглавление</span></button>
                        <button id="reader-next" title="Следующая глава">→<span class="nm-nav-label"> Следующая</span></button>
                        <span id="reader-preload-status"></span>
                    </div>
                </div>
            </div>

            <!-- ОБУЧЕНИЕ -->
            <div id="nm-element-training">
                <div class="nm-training-instructions">
                    <h3>🎯 Режим обучения элементам</h3>
                    <div>
                        Наведите курсор на элемент и кликните по нему, затем выберите тип:<br>
                        <span id="nm-touch-hint" style="display:none;">📱 На телефоне: одиночный тап — только подсветка; <b>двойной тап или удержание ~0,5 с</b> — выбор.<br></span>
                        <b>📄 Блок текста</b> (обязательно) • <b>← Назад</b> • <b>→ Вперёд</b> • <b>☰ Оглавление</b> (необязательно).<br>
                        Обучение работает как эвристика на всю книгу: на других главах элементы будут найдены по структуре страницы.<br>
                        Когда закончите — нажмите «✅ Готово».
                    </div>
                    <button class="nm-btn nm-btn-success" id="btn-finish-training">✅ Готово</button>
                    <button class="nm-btn nm-btn-danger" id="btn-cancel-training">Отмена</button>
                </div>
                <div class="nm-training-popup" id="training-popup">
                    <h4>Назначить тип элемента:</h4>
                    <div class="nm-training-buttons">
                        <button style="background:#2563eb;" data-type="content">📄 Блок основного текста</button>
                        <button style="background:#059669;" data-type="prev">← Кнопка «Назад»</button>
                        <button style="background:#059669;" data-type="next">Кнопка «Вперёд» →</button>
                        <button style="background:#d97706;" data-type="toc">☰ Кнопка «Оглавление»</button>
                        <button style="background:#6b7280;" id="btn-training-cancel-pick">Отмена выбора</button>
                    </div>
                </div>
            </div>
        </div>
    `);
    shadow.appendChild(uiFrag);

    const $ = sel => shadow.querySelector(sel);
    const $$ = sel => shadow.querySelectorAll(sel);

    const modal = $('#nm-modal');
    const bookModal = $('#nm-book-modal');
    const dropdownMenu = $('#dropdown-menu');
    const menuBtn = $('#btn-menu');
    const buttonsBar = $('#nm-buttons');
    const readerMode = $('#nm-reader-mode');
    const readerContent = $('#reader-content');
    const elementTraining = $('#nm-element-training');
    const trainingPopup = $('#training-popup');
    const rootEl = $('#nm-root');

    let isTranslating = false;
    let cancelRequested = false;
    let activeReader = null;

    // ===== МИНИ-ОКНО ИЗВЛЕЧЕНИЯ ТЕРМИНОВ =====
    // Ручной NER живёт в маленьком плавающем окне, а не в читалке
    (function initMiniExtractPopup() {
        const st = document.createElement('style');
        st.textContent = `
            .nm-mini-modal { position: fixed; right: 16px; bottom: 84px; z-index: 2147483646; width: min(380px, calc(100vw - 24px)); background: #ffffff; color: #111827; border-radius: 12px; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3); padding: 12px; display: none; }
            .nm-mini-modal.active { display: block; }
            .nm-mini-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-weight: 600; margin-bottom: 8px; }
            .nm-mini-header button { border: none; background: none; font-size: 18px; cursor: pointer; color: inherit; padding: 4px; line-height: 1; }
            .nm-mini-status { margin-top: 6px; font-size: 12px; opacity: 0.75; word-break: break-word; }
            #nm-root.nm-ui-dark .nm-mini-modal { background: #1f232b; color: #e2e2dc; }
            @media (max-width: 768px), (pointer: coarse) {
                .nm-mini-modal { left: 12px; right: 12px; width: auto; bottom: calc(84px + env(safe-area-inset-bottom, 0px)); }
            }
        `;
        shadow.appendChild(st);
        const popup = document.createElement('div');
        popup.id = 'nm-extract-popup';
        popup.className = 'nm-mini-modal';
        popup.innerHTML = `
            <div class="nm-mini-header">
                <span>✨ Извлечение терминов</span>
                <button type="button" id="nm-extract-close" title="Скрыть">✕</button>
            </div>
            <div class="nm-progress-bar"><div class="nm-progress-fill" id="nm-extract-fill"></div></div>
            <div class="nm-mini-status" id="nm-extract-status">Подготовка...</div>
            <button class="nm-btn nm-btn-danger nm-btn-sm" id="nm-extract-cancel" style="margin-top:10px;">Отменить</button>
        `;
        rootEl.appendChild(popup);
    })();
    function extractMiniShow() {
        $('#nm-extract-popup').classList.add('active');
        const f = $('#nm-extract-fill');
        f.style.width = '0%';
        f.classList.remove('retry');
        $('#nm-extract-status').textContent = 'Подготовка...';
    }
    function extractMiniStatus(text) { $('#nm-extract-status').textContent = text; }
    function extractMiniHide(delay = 2200) {
        setTimeout(() => {
            const popup = $('#nm-extract-popup');
            if (popup) popup.classList.remove('active');
        }, delay);
    }
    function updateExtractionProgressMini(st) {
        const fill = $('#nm-extract-fill');
        if (!fill) return;
        fill.classList.toggle('retry', !!st.retry);
        fill.style.width = st.pct + '%';
        extractMiniStatus(st.retry
            ? `⏱ ${st.retry.message} — повтор ${st.retry.nextAttempt}/${st.retry.attemptsTotal}`
            : `🔍 Термины: чанк ${st.chunk}/${st.total}${st.resumed ? ` (продолжаю с ${st.chunk}/${st.resumed + 1})` : ''} • ~${st.pct}%`);
    }
    $('#nm-extract-cancel').addEventListener('click', () => {
        cancelRequested = true;
        if (activeReader) { try { activeReader.cancel(); } catch {} }
    });
    $('#nm-extract-close').addEventListener('click', () => {
        if (!isTranslating) $('#nm-extract-popup').classList.remove('active');
    });

    // ===== ПРОГРЕСС =====
    function progressShow(title) {
        if (!readerModeActive) openReaderShell(null);
        $('#reader-progress').classList.add('active');
        $('#reader-progress-title').textContent = title;
        $('#reader-progress-status').textContent = 'Подготовка...';
        const f = $('#reader-progress-fill');
        f.style.width = '0%';
        f.classList.remove('retry');
        updateNavButtons();
    }
    function progressStatus(t) { $('#reader-progress-status').textContent = t; }
    function progressFill() { return $('#reader-progress-fill'); }
    function progressHide() {
        $('#reader-progress').classList.remove('active');
        const f = $('#reader-progress-fill');
        f.style.width = '0%';
        f.classList.remove('retry');
        updateNavButtons();
    }

    // ===== UI-СЛУЖЕБНЫЕ =====
    function showStatus(msg, type = 'info', id = 'status-book') {
        const el = $('#' + id);
        if (!el) return;
        el.style.removeProperty('display');
        el.textContent = msg;
        el.className = 'nm-status ' + type;
    }
    function hideStatus(id = 'status-book') {
        const el = $('#' + id);
        if (el) { el.className = 'nm-status'; el.style.display = 'none'; }
    }
    function openModal() {
        modal.classList.add('active');
        dropdownMenu.classList.remove('active');
        refreshBookTab();
        updateGlossaryUI();
        loadSettings();
    }
    function closeModal() {
        modal.classList.remove('active');
        ['status-book', 'status-glossary', 'status-settings'].forEach(hideStatus);
    }
    function openBookModal() {
        $('#book-modal-url').value = suggestBookKeyFromUrl();
        $('#book-modal-name').value = document.title.replace(/\s*[-–—|].*$/, '').trim();
        bookModal.classList.add('active');
    }

    // ===== ВКЛАДКА "КНИГА" =====
    function refreshBookTab() {
        const select = $('#book-select');
        const keys = Object.keys(books);
        if (!managedBookKey || !books[managedBookKey]) managedBookKey = currentBookKey || keys[0] || null;
        select.innerHTML = '';
        if (keys.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = '(нет сохранённых книг)';
            select.appendChild(opt);
        } else {
            for (const key of keys) {
                const opt = document.createElement('option');
                opt.value = key;
                opt.textContent = `${books[key].name || key}${key === currentBookKey ? '  (текущая страница)' : ''}`;
                select.appendChild(opt);
            }
            select.value = managedBookKey;
        }
        renderBookManageArea();
    }
    function renderBookManageArea() {
        const area = $('#book-manage-area');
        const key = managedBookKey;
        if (!key || !books[key]) {
            const help = document.createElement('div');
            help.className = 'nm-help';
            help.textContent = '⚠️ Книг пока нет — откройте страницу книги (она определяется по заданному URL) и привяжите её.';
            const btn = document.createElement('button');
            btn.className = 'nm-btn nm-btn-primary';
            btn.textContent = '📚 Привязать текущую страницу к книге';
            btn.addEventListener('click', openBookModal);
            area.replaceChildren(help, btn);
            return;
        }
        const book = books[key];
        const terms = Object.keys(siteGlossaries[key] || {}).length;
        const nerPages = Object.keys(siteNerDone[key] || {}).length;
        const cachedCount = cacheBookChapterCount(key);
        const info = document.createElement('div');
        info.className = 'nm-book-info';
        const strong = document.createElement('strong');
        strong.textContent = `📖 ${book.name || 'Без названия'}`;
        const urlLine = document.createElement('small');
        urlLine.setAttribute('style', 'color:#6b7280;display:block;');
        urlLine.textContent = `URL: ${key}`;
        const statsLine = document.createElement('small');
        statsLine.setAttribute('style', 'color:#6b7280;display:block;');
        statsLine.textContent = `Терминов: ${terms} | Страниц с извлечёнными терминами: ${nerPages} | Переведённых глав в кэше: ${cachedCount}`;
        const sel = book.selectors || {};
        const trained = [];
        if (sel.content) trained.push('📄 текст');
        if (sel.prev) trained.push('← назад');
        if (sel.next) trained.push('→ вперёд');
        if (sel.toc) trained.push('☰ оглавление');
        const selLine = document.createElement('small');
        selLine.setAttribute('style', `display:block;margin-top:6px;color:${trained.length ? '#059669' : '#b45309'};`);
        selLine.textContent = trained.length ? `Обучено (эвристика на всю книгу): ${trained.join(', ')}` : '⚠️ Элементы не обучены — читалка предложит обучение';
        info.append(strong, urlLine, statsLine, selLine);
        const mkGroup = (labelText, inputId, value) => {
            const group = document.createElement('div');
            group.className = 'nm-input-group';
            const label = document.createElement('label');
            label.textContent = labelText;
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'nm-input';
            input.id = inputId;
            input.value = value;
            group.append(label, input);
            return group;
        };
        // обложка: URL-поле, поиск картинок на странице, предпросмотр и кандидаты
        const coverGroup = document.createElement('div');
        coverGroup.className = 'nm-input-group';
        const coverLabel = document.createElement('label');
        coverLabel.textContent = 'Обложка книги (URL):';
        const coverRow = document.createElement('div');
        coverRow.className = 'nm-url-edit';
        const coverInput = document.createElement('input');
        coverInput.type = 'text';
        coverInput.className = 'nm-input';
        coverInput.id = 'book-cover-edit';
        coverInput.placeholder = 'https://…/cover.jpg';
        coverInput.value = book.coverUrl || '';
        const findCoversBtn = document.createElement('button');
        findCoversBtn.className = 'nm-btn nm-btn-sm nm-btn-secondary';
        findCoversBtn.id = 'btn-find-covers';
        findCoversBtn.textContent = '🖼 Найти на странице';
        coverRow.append(coverInput, findCoversBtn);
        const coverPreview = document.createElement('img');
        coverPreview.id = 'cover-preview';
        coverPreview.setAttribute('style', 'max-width:120px;max-height:180px;border-radius:6px;border:1px solid #d1d5db;margin-top:8px;display:none;');
        // битая ссылка на обложку не должна показывать пустую рамку
        coverPreview.addEventListener('error', () => { coverPreview.style.display = 'none'; });
        if (book.coverUrl) { coverPreview.src = book.coverUrl; coverPreview.style.display = 'block'; }
        const coverCandidates = document.createElement('div');
        coverCandidates.id = 'cover-candidates';
        coverCandidates.setAttribute('style', 'display:none;gap:8px;flex-wrap:wrap;margin-top:8px;');
        coverGroup.append(coverLabel, coverRow, coverPreview, coverCandidates);
        findCoversBtn.addEventListener('click', () => {
            const found = findCoverCandidates();
            if (!found.length) {
                const none = document.createElement('small');
                none.setAttribute('style', 'color:#6b7280;');
                none.textContent = 'На странице не найдено картинок-кандидатов';
                coverCandidates.replaceChildren(none);
            } else {
                coverCandidates.replaceChildren(...found.map(cu => {
                    const img = document.createElement('img');
                    img.src = cu;
                    img.title = cu;
                    img.setAttribute('style', 'max-width:80px;max-height:120px;border-radius:4px;cursor:pointer;border:2px solid transparent;');
                    img.addEventListener('click', () => {
                        coverInput.value = cu;
                        coverPreview.src = cu;
                        coverPreview.style.display = 'block';
                    });
                    return img;
                }));
            }
            coverCandidates.style.display = 'flex';
        });
        coverInput.addEventListener('input', () => {
            const v = coverInput.value.trim();
            if (v) { coverPreview.src = v; coverPreview.style.display = 'block'; }
            else coverPreview.style.display = 'none';
        });
        const saveBtn = document.createElement('button');
        saveBtn.className = 'nm-btn nm-btn-primary';
        saveBtn.id = 'btn-save-book';
        saveBtn.textContent = '💾 Сохранить';
        const openSiteBtn = document.createElement('button');
        openSiteBtn.className = 'nm-btn nm-btn-secondary';
        openSiteBtn.id = 'btn-open-site';
        openSiteBtn.textContent = '🔗 Открыть на сайте';
        const delBtn = document.createElement('button');
        delBtn.className = 'nm-btn nm-btn-danger';
        delBtn.id = 'btn-delete-book';
        delBtn.textContent = '🗑 Удалить книгу';
        area.replaceChildren(info,
            mkGroup('Название книги:', 'book-name-edit', book.name || ''),
            mkGroup('URL книги:', 'book-key-edit', key),
            coverGroup, saveBtn, openSiteBtn, delBtn);
        openSiteBtn.title = 'Оглавление (если обучено) или последняя переведённая глава';
        openSiteBtn.addEventListener('click', () => {
            // адрес открытия хранится в записи книги: выученное оглавление, иначе
            // последняя переведённая глава; если переводов ещё не было — URL книги
            const target = book.openUrl || (/^https?:/i.test(key) ? key : '');
            if (target) openExternalTab(target);
        });
        $('#btn-save-book').addEventListener('click', () => {
            const newName = $('#book-name-edit').value.trim();
            const newKey = $('#book-key-edit').value.trim();
            if (!newKey) { showStatus('URL не может быть пустым', 'error', 'status-book'); return; }
            const cover = coverInput.value.trim();
            if (newKey === key) { book.name = newName; book.coverUrl = cover; }
            else {
                // данные книги привязаны к URL-ключу в IndexedDB этого сайта — переносим
                for (const [p, store] of [['g/', siteGlossaries], ['n/', siteNerDone], ['c/', siteChapterCache]]) {
                    if (key in store) { store[newKey] = store[key]; delete store[key]; dbPut(p + newKey, store[newKey]); }
                    dbDelete(p + key);
                }
                books[newKey] = { ...book, name: newName, coverUrl: cover };
                delete books[key];
                if (currentBookKey === key) currentBookKey = newKey;
                managedBookKey = newKey;
            }
            GM_setValue('books', books);
            showStatus('Сохранено!', 'success', 'status-book');
            refreshBookTab();
            updateGlossaryUI();
        });
        $('#btn-delete-book').addEventListener('click', () => {
            if (confirm(`Удалить книгу "${books[key].name || key}" из списка? Её глоссарий, кэш извлечения и кэш переводов глав в памяти этого браузера тоже будут удалены.`)) {
                cacheClearBook(key);
                delete siteGlossaries[key];
                delete siteNerDone[key];
                dbDelete('g/' + key);
                dbDelete('n/' + key);
                delete books[key];
                if (currentBookKey === key) currentBookKey = null;
                managedBookKey = null;
                GM_setValue('books', books);
                showStatus('Книга удалена', 'success', 'status-book');
                refreshBookTab();
                updateGlossaryUI();
            }
        });
    }

    // ===== ВКЛАДКА "ГЛОССАРИЙ" =====
    // Глоссарий только для книги текущей страницы: вне страницы книги — подсказка вместо формы.
    function sortGlossaryEntries(entries) {
        const { field, dir } = glossarySort;
        if (!field || !dir) return entries;
        const sign = dir === 'desc' ? -1 : 1;
        const valueOf = (e) => field === 'gender' ? genderOf(e[1].type) : e[1][field];
        return [...entries].sort((a, b) => {
            const va = valueOf(a), vb = valueOf(b);
            if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * sign;
            return String(va ?? '').toLowerCase().localeCompare(String(vb ?? '').toLowerCase()) * sign;
        });
    }
    function showGlossaryPlaceholder(container, text) {
        const p = document.createElement('p');
        p.setAttribute('style', 'color:#6b7280;text-align:center;padding:20px;');
        p.textContent = text;
        container.replaceChildren(p);
    }
    function updateGlossaryUI() {
        const noBook = !currentBookKey || !books[currentBookKey];
        $('#glossary-book-hint').style.display = noBook ? '' : 'none';
        $('#glossary-body').style.display = noBook ? 'none' : '';
        if (noBook) return;
        const container = $('#glossary-list');
        const pagination = $('#glossary-pagination');
        const glossary = getGlossaryForView();
        updateTypeDatalist();
        let entries = Object.entries(glossary);
        // «только текущая страница»: те же матчеры, что при переводе
        let pageMode = false;
        if (config.glossaryCurrentPageOnly) {
            const pageText = normalize(extractMainText(findContentElement()));
            if (pageText) {
                pageMode = true;
                entries = entries.filter(([, t]) => termMatchesText(pageText, t.term, config.fuzzySearchThreshold));
            } else {
                $('#status-glossary').className = 'nm-status show error';
                $('#status-glossary').textContent = 'Блок текста страницы не найден — показан весь глоссарий';
                setTimeout(() => { $('#status-glossary').classList.remove('show'); }, 4000);
            }
        }
        if (glossaryFilter) {
            const f = normalize(glossaryFilter);
            entries = entries.filter(([, t]) =>
                normalize(t.term).includes(f) || normalize(t.translation).includes(f) || normalize(t.type || '').includes(f));
        }
        entries = sortGlossaryEntries(entries);
        const totalPages = Math.max(1, Math.ceil(entries.length / PAGE_SIZE));
        if (glossaryPage >= totalPages) glossaryPage = totalPages - 1;
        if (glossaryPage < 0) glossaryPage = 0;
        const start = glossaryPage * PAGE_SIZE;
        const pageEntries = entries.slice(start, start + PAGE_SIZE);
        $('#glossary-count').textContent = pageMode ? `${entries.length}/${Object.keys(glossary).length}` : Object.keys(glossary).length;
        if (Object.keys(glossary).length === 0) { showGlossaryPlaceholder(container, 'Глоссарий пуст'); pagination.replaceChildren(); return; }
        if (entries.length === 0) { showGlossaryPlaceholder(container, pageMode ? 'На этой странице терминов из глоссария нет' : 'Ничего не найдено по фильтру'); pagination.replaceChildren(); return; }
        const sortIcon = (field) => glossarySort.field !== field ? '↕' : (glossarySort.dir === 'asc' ? '↑' : '↓');
        const activeClass = (field) => glossarySort.field === field ? 'active-sort' : '';
        const table = document.createElement('table');
        table.className = 'nm-glossary-table';
        const thead = document.createElement('thead');
        const hrow = document.createElement('tr');
        const makeTh = (label, field, extraClass, extraStyle) => {
            const th = document.createElement('th');
            if (field) th.dataset.sort = field;
            if (extraClass) th.className = extraClass;
            if (extraStyle) th.setAttribute('style', extraStyle);
            th.append(`${label} `);
            if (field) {
                const icon = document.createElement('span');
                icon.className = 'nm-sort';
                icon.textContent = sortIcon(field);
                th.appendChild(icon);
            }
            return th;
        };
        hrow.append(
            makeTh('Термин', 'term', activeClass('term')),
            makeTh('Перевод', 'translation', activeClass('translation')),
            makeTh('Тип', 'type', activeClass('type')),
            makeTh('Пол', 'gender', activeClass('gender')),
            makeTh('Частота', 'count', activeClass('count'), 'text-align:center;'),
            makeTh('Действия', null, null, 'width:60px;')
        );
        thead.appendChild(hrow);
        table.appendChild(thead);
        const tbody = document.createElement('tbody');
        for (const [id, t] of pageEntries) {
            const count = t.count || 0;
            const countClass = count >= 5 ? 'high' : count >= 2 ? 'med' : '';
            const gender = genderOf(t.type);
            const tr = document.createElement('tr');
            tr.dataset.id = id;
            const makeCell = (value, field, list) => {
                const td = document.createElement('td');
                const inp = document.createElement('input');
                inp.type = 'text';
                inp.value = value;
                inp.dataset.field = field;
                if (list) inp.setAttribute('list', list);
                td.appendChild(inp);
                return td;
            };
            tr.appendChild(makeCell(t.term, 'term'));
            tr.appendChild(makeCell(t.translation, 'translation'));
            tr.appendChild(makeCell(t.type || '', 'type', 'nm-type-list'));
            const gTd = document.createElement('td');
            const gSel = document.createElement('select');
            gSel.dataset.field = 'gender';
            gSel.title = 'Пол персонажа — хранится внутри типа';
            for (const [val, label] of [['', '—'], ['male', '♂ муж.'], ['female', '♀ жен.'], ['unknown', '⚖ неопр.']]) {
                const opt = document.createElement('option');
                opt.value = val;
                opt.textContent = label;
                if (gender === val) opt.selected = true;
                gSel.appendChild(opt);
            }
            gTd.appendChild(gSel);
            tr.appendChild(gTd);
            const cTd = document.createElement('td');
            cTd.className = `nm-count-cell ${countClass}`;
            cTd.textContent = count;
            tr.appendChild(cTd);
            const dTd = document.createElement('td');
            const del = document.createElement('button');
            del.className = 'nm-delete-cell';
            del.title = 'Удалить';
            del.textContent = '✕';
            dTd.appendChild(del);
            tr.appendChild(dTd);
            tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        container.replaceChildren(table);
        container.querySelectorAll('th[data-sort]').forEach(th => {
            th.addEventListener('click', () => {
                const field = th.dataset.sort;
                if (glossarySort.field === field) {
                    if (glossarySort.dir === 'desc') glossarySort.dir = 'asc';
                    else { glossarySort.field = 'count'; glossarySort.dir = 'desc'; }
                } else { glossarySort.field = field; glossarySort.dir = 'desc'; }
                glossaryPage = 0;
                updateGlossaryUI();
            });
        });
        container.querySelectorAll('tr[data-id]').forEach(row => {
            const id = row.dataset.id;
            row.querySelectorAll('input, select').forEach(inp => {
                inp.addEventListener('change', function() {
                    const field = this.dataset.field;
                    const g = getGlossaryForView();
                    if (!g[id]) return;
                    if (field === 'gender') {
                        g[id].type = withGender(g[id].type, this.value);
                        const tInp = row.querySelector('input[data-field="type"]');
                        if (tInp) tInp.value = g[id].type;
                    } else {
                        g[id][field] = this.value.trim();
                        if (field === 'type') {
                            const gSel = row.querySelector('select[data-field="gender"]');
                            if (gSel) gSel.value = genderOf(g[id].type);
                        }
                    }
                    saveGlossary(g);
                    updateGlossaryUI();
                });
            });
            row.querySelector('.nm-delete-cell').addEventListener('click', () => {
                const g = getGlossaryForView();
                const termName = g[id] ? g[id].term : id;
                if (confirm(`Удалить термин "${termName}"?`)) {
                    delete g[id];
                    saveGlossary(g);
                    updateGlossaryUI();
                }
            });
        });
        renderPagination(pagination, totalPages, entries.length);
    }
    function renderPagination(container, totalPages, totalItems) {
        const pageInfo = (text) => {
            const span = document.createElement('span');
            span.className = 'nm-page-info';
            span.textContent = text;
            return span;
        };
        if (totalPages <= 1) { container.replaceChildren(pageInfo(`Всего: ${totalItems}`)); return; }
        const maxVisiblePages = 7;
        const pages = [];
        if (totalPages <= maxVisiblePages) {
            for (let i = 0; i < totalPages; i++) pages.push(i);
        } else {
            pages.push(0);
            const start = Math.max(1, glossaryPage - 1);
            const end = Math.min(totalPages - 2, glossaryPage + 1);
            if (start > 1) pages.push(-1);
            for (let i = start; i <= end; i++) pages.push(i);
            if (end < totalPages - 2) pages.push(-1);
            pages.push(totalPages - 1);
        }
        const frag = document.createDocumentFragment();
        const navBtn = (label, cls, disabled) => {
            const b = document.createElement('button');
            b.textContent = label;
            b.className = cls;
            b.disabled = disabled;
            return b;
        };
        frag.appendChild(navBtn('‹', 'nm-prev-btn', glossaryPage === 0));
        for (const p of pages) {
            if (p === -1) {
                const dots = document.createElement('span');
                dots.setAttribute('style', 'padding:0 4px;color:#9ca3af;');
                dots.textContent = '…';
                frag.appendChild(dots);
            } else {
                const b = document.createElement('button');
                b.className = 'nm-page-btn' + (p === glossaryPage ? ' active' : '');
                b.dataset.page = p;
                b.textContent = p + 1;
                frag.appendChild(b);
            }
        }
        frag.appendChild(navBtn('›', 'nm-next-btn', glossaryPage === totalPages - 1));
        frag.appendChild(pageInfo(`Стр. ${glossaryPage + 1} из ${totalPages} • Всего: ${totalItems}`));
        container.replaceChildren(frag);
        container.querySelector('.nm-prev-btn').addEventListener('click', () => { if (glossaryPage > 0) { glossaryPage--; updateGlossaryUI(); } });
        container.querySelector('.nm-next-btn').addEventListener('click', () => { if (glossaryPage < totalPages - 1) { glossaryPage++; updateGlossaryUI(); } });
        container.querySelectorAll('.nm-page-btn').forEach(btn => {
            btn.addEventListener('click', () => { glossaryPage = parseInt(btn.dataset.page); updateGlossaryUI(); });
        });
    }

    // ===== HTTP =====
    function makeAbortError(isTimeout) {
        const e = new Error(isTimeout ? 'Таймаут запроса' : 'Запрос прерван');
        e.name = 'AbortError';
        e.isTimeout = !!isTimeout;
        return e;
    }
    function streamFromBody(text) {
        const encoded = new TextEncoder().encode(text || '');
        return new ReadableStream({ start(c) { c.enqueue(encoded); c.close(); } });
    }
    // Менеднеры реализуют GM_xmlhttpRequest по-разному: Tampermonkey умеет отдать
    // ReadableStream (responseType:'stream'), Violentmonkey сидит на XHR — на
    // неизвестном responseType он только пишет в консоль и отдаёт тело целиком.
    // Режим запоминается в GM-хранилище: 'stream' — токены приходят потоком,
    // 'text' — тело приходит одним куском (или растущим responseText).
    // Режим определяется сразу, а не первым ответом: XHR-менеджер присылает
    // loadstart уже вместе с ответом, и таймер успевает убить долгий запрос.
    // GM_info.scriptHandler — как раз для этого; если менеджер не опознан, первый
    // стрим-запрос идёт терпеливо, режим выяснится по нему.
    const gmHandlerName = (typeof GM_info !== 'undefined' && GM_info && GM_info.scriptHandler) || '';
    let gmStreamMode = GM_getValue('gmStreamMode', null);
    if (/violentmonkey/i.test(gmHandlerName)) gmStreamMode = 'text';
    function gmSetStreamMode(mode) {
        if (gmStreamMode === mode) return;
        gmStreamMode = mode;
        GM_setValue('gmStreamMode', mode);
    }
    function gmFetch(url, options, isStream = false) {
        return new Promise((resolve, reject) => {
            let settled = false;
            let req = null;
            const abort = () => { try { if (req) req.abort(); } catch {} };
            // внешний колбэк: таймаут/отмена могут прервать GM-запрос и без signal
            if (typeof options.getAbort === 'function') { try { options.getAbort(abort); } catch {} }
            const settleResolve = (v) => { if (!settled) { settled = true; resolve(v); } };
            const settleReject = (e) => { if (!settled) { settled = true; reject(e); } };
            const parseInfo = (response) => {
                const info = { status: response.status || 0, statusText: response.statusText || '', headers: new Headers(), abort };
                if (response.responseHeaders) {
                    response.responseHeaders.split(/\r?\n/).forEach(line => {
                        const i = line.indexOf(':');
                        if (i > 0) info.headers.append(line.slice(0, i).trim(), line.slice(i + 1).trim());
                    });
                }
                return info;
            };
            const httpError = (info, body) => {
                const err = new Error(`HTTP ${info.status}`);
                err.status = info.status;
                err.statusText = info.statusText || '';
                err.body = String(body || '').slice(0, 200);
                return err;
            };
            const reqOptions = {
                method: options.method || 'GET',
                url: url,
                headers: options.headers || {},
                data: options.body,
                // у XHR-менеджера 'stream' просят напрасно: он не знает его и пишет
                // в консоль — просим text, тело всё равно придёт тем же потоком
                responseType: (isStream && gmStreamMode !== 'text') ? 'stream' : 'text',
                onerror: () => settleReject(Object.assign(new Error('сеть недоступна'), { isNet: true })),
                onabort: () => settleReject(makeAbortError(false)),
                ontimeout: () => settleReject(makeAbortError(true))
            };
            if (isStream) {
                // У потоковых менеджеров (Tampermonkey) тело приходит ReadableStream.
                // У XHR-менеджеров (Violentmonkey) тело приходит НЕ накопленным:
                // каждое событие несёт очередной кусок — нередко только в response,
                // а responseText пуст; load же приходит уже пустым. Куски собираем
                // сами в accum, повтор того же куска (readystatechange и load по
                // одному readyState) отбрасываем по lastPiece.
                let sink = null, delivered = 0, accum = '', lastPiece = null;
                // что реально передал менеджер: без этой трассы «зависает» неотличить
                // от «молчит» — её печатает строка проверки сервера
                const traceLog = [], t0req = Date.now();
                const absorb = (response) => {
                    const was = accum.length;
                    for (const raw of [response.responseText, response.response]) {
                        const piece = typeof raw === 'string' ? raw : '';
                        if (!piece || piece === lastPiece) continue;
                        lastPiece = piece;
                        accum = (piece.length >= accum.length && piece.startsWith(accum)) ? piece : accum + piece;
                    }
                    return accum.length - was;
                };
                const feed = (done) => {
                    if (!sink) return;
                    if (accum.length > delivered) { try { sink.enqueue(new TextEncoder().encode(accum.slice(delivered))); } catch { /* поток закрыт */ } }
                    delivered = accum.length;
                    if (done) { try { sink.close(); } catch { /* уже закрыт */ } sink = null; }
                };
                // первый же сигнал решает, с каким менеджером имеем дело
                const settleBody = (response) => {
                    if (settled) return;
                    const info = parseInfo(response);
                    const stream = response.response;
                    if (stream && typeof stream.getReader === 'function') {
                        gmSetStreamMode('stream');
                        settleResolve(Object.assign(info, { ok: true, status: info.status || 200, body: stream, trace: traceLog }));
                        return;
                    }
                    gmSetStreamMode('text');
                    const body = new ReadableStream({ start(c) { sink = c; } });
                    settleResolve(Object.assign(info, { ok: true, status: info.status || 200, body, trace: traceLog }));
                };
                const guard = (response) => {
                    const info = parseInfo(response);
                    if (info.status < 400) return true;
                    settleReject(httpError(info, response.responseText));
                    return false;
                };
                const note = (response) => {
                    if (traceLog.length < 8) traceLog.push(`rs${response.readyState} +${absorb(response)}б @${Date.now() - t0req}мс`);
                };
                const grow = (response) => { if (!guard(response)) return; settleBody(response); note(response); feed(false); };
                const finish = (response) => { if (!guard(response)) return; settleBody(response); note(response); feed(true); };
                reqOptions.onloadstart = grow;
                reqOptions.onprogress = grow;
                // XHR-менеджер пишет новые куски именно в readystatechange: без него
                // до скрипта доходит только первый кусок тела
                reqOptions.onreadystatechange = grow;
                reqOptions.onload = finish;
            } else {
                reqOptions.onload = (response) => {
                    const info = parseInfo(response);
                    const body = response.responseText || '';
                    const resp = Object.assign(info, {
                        ok: info.status >= 200 && info.status < 300,
                        text: () => Promise.resolve(body),
                        json: () => { try { return Promise.resolve(JSON.parse(body)); } catch (e) { return Promise.reject(e); } }
                    });
                    if (resp.ok) settleResolve(resp);
                    else settleReject(httpError(info, body));
                };
            }
            req = GM_xmlhttpRequest(reqOptions);
            if (options.signal) {
                if (options.signal.aborted) { abort(); settleReject(makeAbortError(false)); return; }
                options.signal.addEventListener('abort', () => { abort(); settleReject(makeAbortError(false)); });
            }
        });
    }
    /**
     * Транспорт по умолчанию — fetch из страницы: он не зависит от мостика
     * контент↔фон менеджера (у Violentmonkey на части устройств он сломан).
     * Хост закрыт CORS — тот же запрос один раз уходит каналом менеджера.
     */
    async function customFetch(url, options, isStream = false) {
        const mode = config.gmTransport === 'auto' ? 'page' : config.gmTransport;
        const gmAvailable = typeof GM_xmlhttpRequest !== 'undefined';
        if (!gmAvailable || mode === 'page') {
            try {
                return await fetch(url, options);
            } catch (e) {
                if (gmAvailable && e && (e.isNet || e.name === 'TypeError')) return gmFetch(url, options, isStream);
                throw e;
            }
        }
        return gmFetch(url, options, isStream);
    }
/** Куда именно били — без этого с телефона не понять, смотреть на адрес или на модель. */
    function requestTarget(url) {
        try {
            const u = new URL(url);
            const path = u.pathname.replace(/\/+$/, '');
            return u.host + (path && path !== '/' ? path : '');
        } catch { return String(url || ''); }
    }
    /** Одна строка о причине отказа: HTTP-код, таймаут или недоступный адрес. */
    function describeError(e, target, ms, fullUrl) {
        if (!e) return `неизвестная ошибка (${ms}мс)`;
        if (e.status) return `HTTP ${e.status}${e.statusText ? ' ' + e.statusText : ''} • ${e.body || 'тело пустое'}`;
        if (e.isTimeout) return `таймаут ${ms}мс • ответа от ${target} нет` + (e.trace ? ` • события: ${e.trace}` : '');
        if (e.isNet) return `сеть недоступна (${ms}мс) • ${target}`
            + (/^http:/i.test(fullUrl || target) ? ' • http-адрес мог быть отсеян HTTPS-only режимом браузера' : '');
        return `${e.message || 'ошибка'} (${ms}мс)` + (e.trace ? ` • события: ${e.trace}` : '');
    }
    async function fetchAttempt(url, options, isStream, timeoutSec, cb = {}) {
        const timeoutMs = timeoutSec > 0 ? timeoutSec * 1000 : 0;
        const controller = new AbortController();
        let reader = null, timer = null, cancelWatcher = null, underlyingAbort = null, failed = false;
        // трассу ведёт customFetch; здесь она только попадает в ответ и в текст ошибки
        const traceLog = [];
        // Гонка-промиис, который только отвергается: с ним мы гарантированно выходим
        // из await, даже если reader.cancel() не разбудил зависший read потока
        let rejectWatch = null;
        const watchPromise = new Promise((_, rej) => { rejectWatch = rej; });
        const fail = (err) => {
            if (failed) return;
            failed = true;
            if (timer) { clearTimeout(timer); timer = null; }
            if (cancelWatcher) { clearInterval(cancelWatcher); cancelWatcher = null; }
            try { if (reader) reader.cancel(); } catch {}
            try { if (underlyingAbort) underlyingAbort(); } catch {}
            try { controller.abort(); } catch {}
            if (rejectWatch) rejectWatch(err);
        };
        // Таймаут передооружается только на приходе полезных токенов: пустые SSE-пинги
        // без контента его не сбрасывают. В режиме целого ответа (XHR-менеджер)
        // пауза между токенами неотличима от ожидания всего ответа, поэтому таймер
        // не участвует — такой запрос держит менеджер, отмена остаётся кнопкой.
        // Для проверки сервера (cb.deadline) он остаётся общим дедлайном: там ждать
        // нечего, а висящее соединение и есть результат проверки.
        const arm = () => {
            if (failed) return;
            if (!timeoutMs || (isStream && gmStreamMode !== 'stream' && !cb.deadline)) {
                // снимается и уже взведённый таймер: иначе первый же ответ в новом
                // режиме всё равно погибал бы по паузе
                if (timer) { clearTimeout(timer); timer = null; }
                return;
            }
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => fail(makeAbortError(true)), timeoutMs);
        };
        cancelWatcher = setInterval(() => {
            if (cancelRequested) fail(new Error('Отменено пользователем'));
        }, 200);
        if (cancelRequested) fail(new Error('Отменено пользователем'));
        arm();
        const readRespText = async (resp) => {
            try {
                if (resp && typeof resp.text === 'function') return await resp.text();
                if (resp && resp.body) return await new Response(resp.body).text();
            } catch {}
            return '';
        };
        try {
            const fetchPromise = customFetch(url, { ...options, signal: controller.signal, getAbort: (fn) => { underlyingAbort = fn; } }, isStream);
            fetchPromise.catch(() => {});
            const resp = await Promise.race([fetchPromise, watchPromise]);
            if (Array.isArray(resp.trace)) { traceLog.length = 0; traceLog.push(...resp.trace); }
            // режим мог определиться в ходе этого ответа — перезапускаем таймер по
            // его правилам: для потока он нужен, для целого тела — не нужен
            arm();
            if (!resp.ok) {
                const errText = await readRespText(resp);
                const err = new Error(`HTTP ${resp.status}: ${String(errText).slice(0, 200)}`);
                err.status = resp.status;
                err.trace = traceLog.join(' ');
                throw err;
            }
            if (!isStream) return resp;
            if (!resp.body || typeof resp.body.getReader !== 'function') {
                // XHR-реализация GM_xmlhttpRequest (Violentmonkey) отдаёт тело целиком:
                // те же строки SSE, тот же разбор — просто без посимвольной выдачи
                resp.body = streamFromBody(await readRespText(resp));
            }
            reader = resp.body.getReader();
            activeReader = reader;
            const decoder = new TextDecoder();
            let text = '', buffer = '', streamError = null, streamModel = '', sawDone = false;
            const handleLine = (line) => {
                const trimmed = line.trim();
                if (!trimmed.startsWith('data:')) return;
                const payload = trimmed.slice(5).trim();
                if (payload === '[DONE]') { sawDone = true; return; }
                try {
                    const parsed = JSON.parse(payload);
                    if (parsed && parsed.error) {
                        // ошибка в SSE-потоке (неверная модель/ключ) — фатальна, без ретраев
                        const errMsg = parsed.error.message || JSON.stringify(parsed.error);
                        streamError = new Error(errMsg);
                        if (typeof parsed.error.code === 'number') streamError.status = parsed.error.code;
                        streamError.isFatal = /invalid|api key|api_key|authentication|unauthorized|forbidden|model|not found|does not exist|unsupported/i.test(errMsg);
                        return;
                    }
                    if (parsed && typeof parsed.model === 'string' && parsed.model) streamModel = parsed.model;
                    const choice = parsed?.choices?.[0];
                    // finish_reason — второй законный терминатор: сервер мог закрыть
                    // поток без [DONE], но сказать, что закончил осознанно
                    if (choice && choice.finish_reason && choice.finish_reason !== 'null') sawDone = true;
                    const content = choice?.delta?.content || '';
                    if (content) { text += content; if (cb.onDelta) cb.onDelta(content); }
                } catch {}
            };
            while (true) {
                if (failed) break;
                if (cancelRequested) { fail(new Error('Отменено пользователем')); break; }
                const beforeLen = text.length;
                const readPromise = reader.read();
                readPromise.catch(() => {});
                const { done, value } = await Promise.race([readPromise, watchPromise]);
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split(/\r?\n/);
                buffer = lines.pop();
                for (const line of lines) {
                    handleLine(line);
                    if (streamError) { streamError.trace = traceLog.join(' '); throw streamError; }
                }
                if (text.length > beforeLen) {
                    arm();
                    // пингу проверки достаточно одного токена: закрываем соединение,
                    // не дожидаясь, пока сервер оборвёт свой SSE-поток
                    if (cb.stopOnFirstContent) break;
                }
            }
            if (!failed && !cancelRequested) { buffer += decoder.decode(); handleLine(buffer); }
            if (cancelRequested) { const e = new Error('Отменено пользователем'); e.trace = traceLog.join(' '); throw e; }
            if (failed) { const e = makeAbortError(true); e.trace = traceLog.join(' '); throw e; }
            if (cb.stopOnFirstContent && text.trim()) {
                try { if (underlyingAbort) underlyingAbort(); } catch {}
                return { text, model: streamModel, trace: traceLog.join(' ') };
            }
            if (!text.trim()) {
                // сервер мог ответить на stream-запрос обычным JSON-телом: ошибкой —
                // или готовым completion (choices[0].message.content), который тоже
                // нужно принять, а не считать пустым ответом
                const tail = (buffer || '').trim();
                if (tail.startsWith('{')) {
                    try {
                        const parsed = JSON.parse(tail);
                        const one = parsed?.choices?.[0]?.message?.content;
                        if (typeof one === 'string' && one.trim()) return { text: one };
                    } catch { /* не JSON — разбор ниже */ }
                    try {
                        const parsed = JSON.parse(tail);
                        if (parsed && parsed.error) {
                            const err = new Error(parsed.error.message || JSON.stringify(parsed.error));
                            err.isFatal = /invalid|api key|api_key|authentication|unauthorized|forbidden|model|not found|does not exist|unsupported/i.test(err.message);
                            throw err;
                        }
                    } catch (e) { if (e && e.message && !/JSON/i.test(e.message)) throw e; }
                }
                // пустой completion (чаще всего — неверная модель) не считается результатом
                const err = new Error('Пустой ответ модели. Проверьте модель, права и параметры запроса.');
                err.isFatal = true;
                throw err;
            }
            // сторож завершённости — именно здесь: пинг проверки выходит на первом
            // токене (до [DONE]) и пустой ответ свёрнут выше; сюда доходит только
            // полноценный стрим — закрытый без [DONE] и без finish_reason, это обрыв
            if (!sawDone) {
                const e = new Error(`обрыв ответа: соединение закрыто без [DONE] (${text.length} симв.)`);
                e.isCut = true;
                e.trace = traceLog.join(' ');
                throw e;
            }
            return { text };
        } catch (e) {
            if (e && e.name === 'AbortError') e.isTimeout = true;
            throw e;
        } finally {
            if (timer) clearTimeout(timer);
            if (cancelWatcher) clearInterval(cancelWatcher);
            if (reader && activeReader === reader) activeReader = null;
        }
    }
    async function fetchWithRetry(url, options, isStream = false, cb = {}) {
        const timeout = config.requestTimeout > 0 ? config.requestTimeout : 0;
        const attemptsTotal = (config.maxRetries || 0) + 1;
        let lastErr;
        for (let attempt = 1; attempt <= attemptsTotal; attempt++) {
            if (cancelRequested) throw new Error('Отменено пользователем');
            try {
                return await fetchAttempt(url, options, isStream, timeout, cb);
            } catch (e) {
                if (cancelRequested) throw new Error('Отменено пользователем');
                if (e.name === 'AbortError') {
                    e.isTimeout = true;
                    e.message = isStream ? `таймаут ${timeout}с без токенов` : `таймаут ${timeout}с без ответа`;
                }
                lastErr = e;
                if (e.status >= 400 || e.isFatal || cancelRequested || attempt >= attemptsTotal) throw e;
                if (cb.onRetry) cb.onRetry({ nextAttempt: attempt + 1, attemptsTotal, isTimeout: !!e.isTimeout, message: e.message || 'Сетевая ошибка' });
                await new Promise(r => setTimeout(r, Math.min(5000, 500 * 2 ** (attempt - 1))));
            }
        }
        throw lastErr;
    }
    /** Единая точка сборки адреса API: хвостовой слэш в настройках не удваивает //. */
    function apiBase() { return String(config.apiHost || '').trim().replace(/\/+$/, ''); }
    function apiHeaders(json) {
        const h = json ? { 'Content-Type': 'application/json' } : {};
        if (config.apiKey) h.Authorization = `Bearer ${config.apiKey}`;
        return h;
    }
    function llmRequestOptions(messages, temperature, stream) {
        const body = { model: config.model, messages, temperature, stream: !!stream };
        const re = String(config.reasoningEffort ?? '').trim();
        if (re !== '' && re.toLowerCase() !== 'none') body.reasoning_effort = re;
        return {
            url: apiBase() + '/chat/completions',
            options: { method: 'POST', headers: apiHeaders(true), body: JSON.stringify(body) }
        };
    }
    async function callLLM(messages, temperature, stream, cb = {}) {
        const { url, options } = llmRequestOptions(messages, temperature, stream);
        return await fetchWithRetry(url, options, !!stream, cb);
    }
    // GET /models у провайдера весит сотни килобайт (у routerai.ru — 528 моделей,
    // ~876КБ), поэтому он получает собственный запас времени и в проверке участвует
    // только как диагноз. Пользовательский requestTimeout остаётся как есть: он
    // относится к настоящим запросам перевода.
    const CHECK_TIMEOUT_MIN = 60;
    async function checkServer() {
        const statusEl = $('#server-status');
        const gmMode = config.gmTransport === 'auto' ? 'page' : config.gmTransport;
        const transport = typeof GM_xmlhttpRequest === 'undefined' ? 'fetch'
            : (gmMode === 'page' ? 'fetch из страницы'
                : (gmStreamMode === 'text' ? 'менеджер · XHR (тело целиком)' : 'менеджер · поток'));
        const base = apiBase();
        if (!base) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Не указан API Host'; return; }
        if (!config.apiKey && !config.localModel) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Не указан API Key (или включите «Локальная модель без API-ключа»)'; return; }
        if (!config.model) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Не указана модель'; return; }
        const target = requestTarget(base);
        const timeout = config.requestTimeout > 0 ? config.requestTimeout : 0;
        const diagTimeout = Math.max(timeout, CHECK_TIMEOUT_MIN);
        const show = (cls, text) => {
            statusEl.className = `nm-server-status show ${cls}`;
            statusEl.textContent = text;
        };
        show('loading', `🔌 Проверяю ${target}… (${transport}, таймаут ${timeout || '∞'}с)`);
        // Первым идёт короткий пинг чата — это ровно тот запрос, которым Lite переводит.
        // Стримом и с выходом по первому токену: серверы, отвечающие SSE и не спешащие
        // закрывать соединение (на них не-стримовый пинг висел до таймаута), отвечают
        // сразу; соединение закрываем сами, не дожидаясь их вежливости.
        const t0 = Date.now();
        const { url, options } = llmRequestOptions([{ role: 'user', content: 'ping' }], 0, true);
        const payload = JSON.parse(options.body);
        payload.max_tokens = 1;
        try {
            const res = await fetchAttempt(url, { ...options, body: JSON.stringify(payload) }, true, timeout,
                { stopOnFirstContent: true, deadline: true });
            const got = String(res.text || '').trim();
            show('ok', `✅ Сервер доступен • ${target} • пинг ${Date.now() - t0}мс • модель ${res.model || config.model}`
                + (got ? ` • ответ: ${got.slice(0, 24)}` : '') + ` • ${transport}`);
        } catch (e) {
            // Пинг не дошёл — отличаем «адрес недоступен» от «модель думает дольше
            // таймаута». Диагностический GET /models идёт со своим запасом: он тяжелый.
            const main = describeError(e, target, Date.now() - t0, url);
            show('loading', `⏳ ${main} • проверяю /models как диагноз…`);
            const t1 = Date.now();
            let note;
            try {
                const resp = await fetchAttempt(base + '/models', { method: 'GET', headers: apiHeaders(false) }, false, diagTimeout, {});
                const data = await resp.json().catch(() => null);
                const list = Array.isArray(data?.data) ? data.data : (Array.isArray(data) ? data : []);
                note = `/models отвечает за ${Date.now() - t1}мс (${list.length} моделей, `
                    + (list.some(m => m && m.id === config.model) ? `модель «${config.model}» есть` : `модели «${config.model}» в списке НЕТ`)
                    + ') — значит канал есть, смотрите время ответа модели';
            } catch (e2) {
                note = `/models тоже молчит: ${describeError(e2, target, Date.now() - t1, base)}`;
            }
            show('err', `❌ ${main} • ${note} • ${transport}`);
        }
    }
    // ===== NER =====
    const NER_RESPONSE_RATIO = 2;
    // модель обязана вернуть JSON-массив объектов; ответ приходит и в ```json
    // fences, и одним объектом, и в обёртке {"terms": [...]} — всё это разбирается.
    // Битый JSON — не «пустой чанк», а повод переспросить
    const NER_PARSE_ATTEMPTS = 3;

    function extractJsonArray(raw) {
        const text = String(raw || '').replace(/```json/gi, '').replace(/```/g, '').trim();
        const aStart = text.indexOf('[');
        const aEnd = text.lastIndexOf(']');
        if (aStart !== -1 && aEnd > aStart) {
            try {
                const arr = JSON.parse(text.slice(aStart, aEnd + 1));
                if (Array.isArray(arr)) return arr;
            } catch { /* ниже — попытка с объектом */ }
        }
        const oStart = text.indexOf('{');
        const oEnd = text.lastIndexOf('}');
        if (oStart !== -1 && oEnd > oStart) {
            try {
                const obj = JSON.parse(text.slice(oStart, oEnd + 1));
                if (Array.isArray(obj)) return obj;
                for (const v of Object.values(obj)) if (Array.isArray(v)) return v;
                if (typeof obj.term === 'string' || typeof obj.translation === 'string') return [obj];
            } catch { return null; }
        }
        return null;
    }
    // минимально необходимые поля — term и translation; type приводится, aliases
    // сохраняются, если пришли (совместимость с ner.json конвейера)
    function normalizeNerItem(raw) {
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
        const term = String(raw.term ?? '').trim();
        const translation = String(raw.translation ?? '').trim();
        if (!term || !translation) return null;
        const item = { term, translation, type: String(raw.type ?? '').trim() || 'Term', count: 1 };
        const aliases = Array.isArray(raw.aliases) ? raw.aliases.map(a => String(a).trim()).filter(Boolean) : [];
        if (aliases.length) item.aliases = aliases;
        return item;
    }
    // один чанк → записи: сетевые повторы и таймауты уже внутри callLLM, здесь
    // повтор для случая «ответ пришёл, но не годится»: не JSON и пустой completion
    async function requestNerChunk(chunkText, onChunk) {
        const userPrompt = config.extractionPrompt
            .replaceAll('{targetLang}', config.targetLang)
            .replaceAll('{text}', chunkText);
        let lastErr = null;
        for (let attempt = 1; attempt <= NER_PARSE_ATTEMPTS; attempt++) {
            let res = null;
            try {
                res = await callLLM([{ role: 'user', content: userPrompt }], 0.3, true, onChunk);
            } catch (error) {
                // isFatal — настоящий HTTP-ответ (401, нет модели): повторять его
                // бессмысленно; пустой completion — как раз случай «переспросить»
                if (error.isFatal && !/Пустой ответ/.test(error.message || '')) throw error;
                lastErr = error;
            }
            const arr = res ? extractJsonArray(res.text) : null;
            if (arr) {
                const items = [];
                let bad = 0;
                for (const raw of arr) {
                    const item = normalizeNerItem(raw);
                    if (item) items.push(item);
                    else bad++;
                }
                return { items, bad };
            }
            if (cancelRequested) throw new Error('Отменено пользователем');
            lastErr = lastErr || new Error('Модель вернула не JSON-массив');
            // тот же прогресс-хелпер: повтор показывает причину и свой номер
            if (attempt < NER_PARSE_ATTEMPTS && onChunk && onChunk.onRetry) {
                onChunk.onRetry({ message: lastErr.message, nextAttempt: attempt + 1, attemptsTotal: NER_PARSE_ATTEMPTS });
            }
        }
        throw lastErr || new Error('NER: модель не ответила');
    }
    async function extractTermsFromText(text, targetKey, onProgress) {
        if (!books[targetKey]) return { added: 0, incremented: 0, canceled: false, skipped: 0, badItems: 0, resumed: 0, skippedReason: '' };
        const chunks = splitByNewlines(text, config.chunkSize);
        const glossary = { ...bookGlossary(targetKey) };
        const hash = textHash(text);
        const expectedTotal = Math.max(1, text.length * NER_RESPONSE_RATIO);
        let streamed = 0, added = 0, incremented = 0, skipped = 0, badItems = 0, canceled = false, skippedReason = '';
        // прерванный прогон продолжается с того же чанка (та же страница, тот же
        // исходный текст, то же чанкование)
        const stored = jobOf(targetKey);
        let startChunk = 0;
        if (stored && stored.hash === hash && stored.nerTotal === chunks.length && stored.nerDone > 0) {
            startChunk = Math.min(stored.nerDone, chunks.length);
        }
        const emitProgress = (i, retry) => {
            if (onProgress) onProgress({ chunk: i + 1, total: chunks.length, resumed: startChunk, pct: Math.min(99, Math.round((streamed / expectedTotal) * 100)), retry: retry || null });
        };
        for (let i = 0; i < chunks.length; i++) {
            if (cancelRequested) { canceled = true; break; }
            if (i < startChunk) { emitProgress(i); continue; }
            const charsBefore = streamed;
            const onChunk = {
                onDelta: (piece) => { streamed += piece.length; emitProgress(i); },
                onRetry: (info) => { streamed = charsBefore; emitProgress(i, info); }
            };
            try {
                const { items, bad } = await requestNerChunk(chunks[i], onChunk);
                badItems += bad;
                for (const item of items) {
                    let existingId = null;
                    for (const [id, ex] of Object.entries(glossary)) {
                        if (normalize(ex.term) === normalize(item.term)) { existingId = id; break; }
                    }
                    if (!existingId) {
                        for (const [id, ex] of Object.entries(glossary)) {
                            if (termMatchesText(ex.term, item.term, config.fuzzySearchThreshold)) { existingId = id; break; }
                        }
                    }
                    if (existingId) { glossary[existingId].count = (glossary[existingId].count || 0) + 1; incremented++; }
                    else {
                        glossary[`${normalize(item.term)}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`] = migrateEntry(item);
                        added++;
                    }
                }
            } catch (error) {
                if (cancelRequested) { canceled = true; emitProgress(i); break; }
                // битый формат одного чанка не роняет весь прогон, но чанк остаётся
                // необработанным — поэтому отметка «NER сделан» не ставится
                skipped++;
                skippedReason = error.message || 'нет данных';
                emitProgress(i);
                continue;
            }
            // глоссарий и отметка чанка пишется после каждого чанка: при уходе с
            // страницы сделанное не теряется
            siteGlossaries[targetKey] = glossary;
            dbPut('g/' + targetKey, glossary);
            jobPut(targetKey, { url: pageCacheKey(), hash, nerDone: i + 1, nerTotal: chunks.length });
        }
        siteGlossaries[targetKey] = glossary;
        dbPut('g/' + targetKey, glossary);
        if (!skipped && !canceled) jobClearNer(targetKey);
        return { added, incremented, canceled, skipped, badItems, resumed: startChunk, skippedReason };
    }
    // ===== ПЕРЕВОД =====
    // промпт чанка: глоссарий подбирается под сам чанк, плейсхолдеры меняются
    // replaceAll — в промптах плейсхолдер может встречаться несколько раз
    function chunkUserPrompt(chunkText) {
        const glossaryText = formatGlossaryForPrompt(findRelevantTerms(chunkText));
        return config.translationPrompt
            .replaceAll('{sourceLang}', config.sourceLang)
            .replaceAll('{targetLang}', config.targetLang)
            .replaceAll('{glossary}', glossaryText)
            .replaceAll('{text}', chunkText);
    }
    // часть главы по чанкам: '' — чанк не переведён. Прерванная работа сохраняется
    // целиком, повторный запуск продолжает с первого незакрытого чанка
    function jobParts(bookKey, pageUrl, chunks, hash) {
        const stored = bookKey ? jobOf(bookKey, pageUrl) : null;
        if (stored && stored.hash === hash && Array.isArray(stored.parts) && stored.parts.length === chunks.length) {
            return chunks.map((_, i) => String(stored.parts[i] || ''));
        }
        return chunks.map(() => '');
    }
    function jobSave(bookKey, pageUrl, hash, parts) {
        if (!bookKey) return;
        jobPut(bookKey, { url: pageUrl, hash, parts, total: parts.length });
    }
    function joinParts(parts) { return parts.filter(p => p).join('\n\n'); }
    function renderTranslationInto(element, text) {
        const paras = paragraphsOf(text);
        element.innerHTML = '';
        for (const para of paras) {
            const p = document.createElement('p');
            p.textContent = para;
            element.appendChild(p);
        }
    }
    async function translateWithStreaming(element, originalText, job = {}) {
        const chunks = splitByNewlines(originalText, config.chunkSize);
        const totalParas = paragraphsOf(originalText).length;
        if (totalParas === 0) { progressStatus('Текст не найден'); return { text: '', completed: false, resumed: 0 }; }
        const bookKey = job.bookKey;
        const pageUrl = job.url || pageCacheKey();
        const hash = textHash(originalText);
        const parts = jobParts(bookKey, pageUrl, chunks, hash);
        const resumed = parts.filter(p => p).length;
        const fill = progressFill();
        fill.classList.remove('retry');
        let completed = false, errMessage = '';
        const setProgress = (all) => {
            const done = paragraphsOf(all).length;
            const pct = totalParas > 0 ? Math.min(99, Math.round((done / totalParas) * 100)) : 0;
            fill.style.width = pct + '%';
            return pct;
        };
        try {
            element.innerHTML = '';
            if (resumed) renderTranslationInto(element, joinParts(parts));
            for (let i = 0; i < chunks.length; i++) {
                if (cancelRequested) throw new Error('Отменено пользователем');
                if (parts[i]) continue;
                fill.classList.remove('retry');
                progressStatus(`Чанк ${i + 1}/${chunks.length} • абзацев в источнике: ${totalParas}`
                    + (resumed ? ` • продолжаю (${resumed}/${chunks.length} уже готово)` : ''));
                let chunkTranslation = '';
                const res = await callLLM([{ role: 'user', content: chunkUserPrompt(chunks[i]) }], 0.7, true, {
                    onDelta: (content) => {
                        chunkTranslation += content;
                        const all = joinParts(parts.map((p, j) => (j === i ? chunkTranslation : p)));
                        renderTranslationInto(element, all);
                        const pct = setProgress(all);
                        progressStatus(`Чанк ${i + 1}/${chunks.length} • ~${pct}%`);
                    },
                    onRetry: (info) => {
                        chunkTranslation = '';
                        renderTranslationInto(element, joinParts(parts));
                        setProgress(joinParts(parts));
                        fill.classList.add('retry');
                        progressStatus(`⏱ ${info.message} — повтор ${info.nextAttempt}/${info.attemptsTotal}`);
                    }
                });
                parts[i] = res.text;
                jobSave(bookKey, pageUrl, hash, parts);
                fill.classList.remove('retry');
                renderTranslationInto(element, joinParts(parts));
                setProgress(joinParts(parts));
            }
            completed = true;
            progressStatus('✅ Перевод завершён!');
            fill.style.width = '100%';
        } catch (error) {
            errMessage = error.message || 'неизвестная ошибка';
            progressStatus('❌ ' + errMessage);
            // частичный перевод остаётся на экране и в задании — на следующей
            // загрузке страницы он продолжится, а не начнётся заново
            if (joinParts(parts)) renderTranslationInto(element, joinParts(parts) + '\n\n[ПЕРЕВОД ПРЕРВАН: ' + error.message + ']');
            else renderTranslationInto(element, '');
        } finally {
            fill.classList.remove('retry');
            if (bookKey) {
                if (completed) jobClear(bookKey);
                else jobSave(bookKey, pageUrl, hash, parts);
            }
        }
        return { text: joinParts(parts), completed, resumed, error: errMessage };
    }
    // фоновый перевод следующей главы: тот же механизм чанков и того же задания
    async function translateTextBackground(text, job = {}) {
        const chunks = splitByNewlines(text, config.chunkSize);
        const pageUrl = job.url || pageCacheKey();
        const hash = textHash(text);
        const parts = jobParts(job.bookKey, pageUrl, chunks, hash);
        for (let i = 0; i < chunks.length; i++) {
            if (parts[i]) continue;
            const resp = await callLLM([{ role: 'user', content: chunkUserPrompt(chunks[i]) }], 0.7, false);
            const data = await resp.json().catch(() => null);
            const piece = data && data.choices && data.choices[0] && data.choices[0].message ? (data.choices[0].message.content || '') : '';
            if (!piece) throw new Error('Пустой ответ при фоновом переводе');
            parts[i] = piece;
            jobSave(job.bookKey, pageUrl, hash, parts);
        }
        return joinParts(parts);
    }
    function updateExtractionProgress(st) {
        const fill = progressFill();
        fill.classList.toggle('retry', !!st.retry);
        fill.style.width = st.pct + '%';
        progressStatus(st.retry
            ? `⏱ ${st.retry.message} — повтор ${st.retry.nextAttempt}/${st.retry.attemptsTotal}`
            : `🔍 Термины: чанк ${st.chunk}/${st.total}${st.resumed ? ` (продолжаю с ${st.chunk}/${st.resumed + 1})` : ''} • ~${st.pct}%`);
    }
    // ===== ЧИТАЛКА =====
    // Тема «как в системе» должна реагировать на смену системной сразу, без перезагрузки
    // страницы; MediaQueryList.addEventListener появился позже addListener.
    const systemDark = matchMedia('(prefers-color-scheme: dark)');
    function effectiveTheme() {
        return config.readerTheme === 'auto' ? (systemDark.matches ? 'dark' : 'light') : config.readerTheme;
    }
    function applyTheme() {
        const dark = effectiveTheme() === 'dark';
        readerMode.classList.remove('nm-reader-light', 'nm-reader-dark');
        readerMode.classList.add(dark ? 'nm-reader-dark' : 'nm-reader-light');
        rootEl.classList.toggle('nm-ui-dark', dark);
        readerContent.style.fontFamily = config.readerFontFamily;
        readerContent.style.fontSize = config.readerFontSize + 'px';
        readerContent.style.lineHeight = config.readerLineHeight;
        readerContent.style.setProperty('--nm-content-width', config.readerContentWidth + '%');
        let dyn = $('#nm-reader-dyn');
        if (!dyn) {
            dyn = document.createElement('style');
            dyn.id = 'nm-reader-dyn';
            shadow.appendChild(dyn);
        }
        dyn.textContent = `#nm-reader-mode .nm-reader-content p { margin: 0 0 ${config.readerParagraphSpacing}em 0; }`;
        const toggle = $('#reader-theme-toggle');
        if (toggle) {
            // режим прямо в надписи: при светлой системной переход «система→светлая»
            // визуально ничто, без надписи кажется, что кнопка не работает
            toggle.textContent = `🌓 Тема: ${THEME_MODE_LABELS[config.readerTheme] || config.readerTheme}`;
            toggle.title = 'Переключить: как в системе → тёмная → светлая';
        }
    }
    try {
        if (typeof systemDark.addEventListener === 'function') {
            systemDark.addEventListener('change', () => { if (config.readerTheme === 'auto') applyTheme(); });
        } else if (typeof systemDark.addListener === 'function') {
            systemDark.addListener(() => { if (config.readerTheme === 'auto') applyTheme(); });
        }
    } catch { /* браузер без media-слушателя — тема просто не обновится на лету */ }
    function openReaderShell(loadingText) {
        readerModeActive = true;
        readerMode.classList.add('active');
        buttonsBar.style.display = 'none';
        applyTheme();
        if (loadingText) {
            const div = document.createElement('div');
            div.className = 'nm-reader-loading';
            div.textContent = loadingText;
            readerContent.replaceChildren(div);
        }
        updateNavButtons();
    }
    function closeReader() {
        readerModeActive = false;
        readerMode.classList.remove('active');
        buttonsBar.style.display = '';
        readerState = null;
        $('#reader-retranslate').style.display = '';
        progressHide();
    }
    function updateNavButtons() {
        const busy = isTranslating;
        $('#reader-retranslate').disabled = busy;
        $('#reader-prev').disabled = busy || !(readerState && readerState.prevUrl);
        $('#reader-next').disabled = busy || !(readerState && readerState.nextUrl);
        $('#reader-toc').disabled = busy || !(readerState && readerState.tocUrl);
        $('#reader-prev').style.display = readerState && readerState.prevUrl ? '' : 'none';
        $('#reader-next').style.display = readerState && readerState.nextUrl ? '' : 'none';
        $('#reader-toc').style.display = readerState && readerState.tocUrl ? '' : 'none';
    }
    function setReaderState(data, rerender) {
        readerState = data;
        $('#reader-title').textContent = data.title || '';
        if (rerender) {
            renderTranslationInto(readerContent, data.text);
            readerMode.scrollTop = 0;
        }
        updateNavButtons();
    }
    // новую вкладку открываем временным якорем с rel=noopener, а не window.open:
    // переход по главам остаётся той же ссылкой того же сайта, без навигации текущей страницы
    function openExternalTab(href) {
        const a = document.createElement('a');
        a.href = href;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        document.body.append(a);
        a.click();
        a.remove();
    }
    function gotoChapter(url) {
        if (!url || isTranslating) return;
        let target = null;
        try { target = new URL(url, location.href); } catch { return; }
        if (target.protocol !== 'http:' && target.protocol !== 'https:') return;
        // навигация читалки — только по этому же сайту; чужой хост открываем новой вкладкой
        if (target.hostname !== location.hostname) { openExternalTab(target.href); return; }
        sessionStorage.setItem('nm_auto_reader', '1');
        location.assign(target.href);
    }
    // автоперевод одной следующей главы в фоне (чекбокс в настройках);
    // уже закешированная глава пропускается, петля последней главы — тоже
    async function pretranslateNext(nextUrl) {
        if (!nextUrl || !config.preemptiveTranslation) return;
        if (preemptiveRunning.has(nextUrl) || cacheGet(nextUrl)) return;
        const cur = getCurrentBook();
        const sel = getBookSelectors();
        if (!cur || !sel.content) return;
        preemptiveRunning.add(nextUrl);
        const statusBtn = $('#reader-preload-status');
        try {
            statusBtn.style.display = '';
            statusBtn.textContent = '⏳ Следующая глава переводится в фоне…';
            const resp = await customFetch(nextUrl, {}, false);
            const html = await resp.text();
            const doc = new DOMParser().parseFromString(html, 'text/html');
            const text = extractTextFromDoc(doc, sel.content);
            if (!text.trim()) throw new Error('Не найден текст в следующей главе');
            const translated = await translateTextBackground(text, { bookKey: cur.key, url: nextUrl });
            cacheSet(nextUrl, {
                url: nextUrl,
                title: doc.title || '',
                text: translated,
                nextUrl: resolveNavHref(doc, sel.next, nextUrl, 'next'),
                prevUrl: resolveNavHref(doc, sel.prev, nextUrl, 'prev'),
                tocUrl: resolveNavHref(doc, sel.toc, nextUrl, 'toc')
            }, cur.key);
            statusBtn.textContent = '✅ Следующая глава готова';
            setTimeout(() => { statusBtn.style.display = 'none'; }, 4000);
        } catch (e) {
            console.warn('[NovelMaestro] Автоперевод:', e.message);
            statusBtn.style.display = 'none';
        } finally {
            preemptiveRunning.delete(nextUrl);
        }
    }

    // ===== ОСНОВНОЙ ПОТОК =====
    async function handleTranslate() {
        if (isTranslating) return;
        dropdownMenu.classList.remove('active');
        const current = getCurrentBook();
        if (!current) { openBookModal(); return; }
        const sel = current.book.selectors || {};
        if (!sel.content) {
            pendingTranslateAfterTraining = true;
            startElementTraining();
            return;
        }
        await runTranslationFlow(false);
    }
    async function runTranslationFlow(ignoreCache) {
        const current = getCurrentBook();
        if (!current) return;
        if (!config.apiKey && !config.localModel) {
            alert('Укажите API Key в настройках или включите «Локальная модель без API-ключа»');
            openModal();
            return;
        }
        const url = pageCacheKey();
        isTranslating = true;
        cancelRequested = false;
        // «перевести заново» значит начать с чистого листа — фоновое задание сбрасывается
        if (ignoreCache) jobClear(current.key);
        const job = ignoreCache ? null : jobOf(current.key);
        const resumed = job && Array.isArray(job.parts) ? job.parts.filter(p => p).length : 0;
        const title = resumed ? '▶️ Продолжаю фоновый перевод…' : (ignoreCache ? '🔄 Повторный перевод…' : '🔄 Перевод…');
        openReaderShell(title);
        // readerState создаётся СРАЗУ по живой странице — кнопки навигации
        // доступны даже если перевод отменён или не удался
        setReaderState({ url, title: document.title, text: '', ...resolveNavFromLive() }, false);
        let flowCompleted = false;
        // адрес «Открыть на сайте»: выученное оглавление, иначе эта глава
        // (последняя переведённая)
        current.book.openUrl = readerState.tocUrl || url;
        GM_setValue('books', books);
        progressShow(title);
        try {
            const cached = ignoreCache ? null : cacheGet(url);
            if (cached && cached.text) {
                setReaderState({ url, ...cached }, true);
                progressShow('📖 Глава из кэша');
                progressStatus('✅ Перевод уже был готов (опережающий перевод)');
                progressFill().style.width = '100%';
                jobClear(current.key);
                flowCompleted = true;
                if (config.autoNER && !isNerDoneForPage(current.key)) {
                    const liveText = extractMainText(findContentElement());
                    if (liveText.trim()) {
                        extractTermsFromText(liveText, current.key, null).then(res => {
                            if (res && !res.canceled) markNerDone(current.key);
                        }).catch(() => {});
                    }
                }
            } else {
                const element = findContentElement();
                const text = extractMainText(element);
                if (!text.trim()) { alert('Текст не найден на странице'); closeReader(); return; }
                if (config.autoNER) {
                    progressShow('🔍 Извлечение терминов');
                    if (isNerDoneForPage(current.key)) {
                        progressStatus('✨ Термины для этой страницы уже извлекались');
                        await new Promise(r => setTimeout(r, 500));
                    } else {
                        try {
                            const nerResult = await extractTermsFromText(text, current.key, updateExtractionProgress);
                            progressFill().style.width = '100%';
                            if (nerResult.canceled) {
                                progressStatus(`⏹ NER остановлен: +${nerResult.added} новых, обновлено частот: ${nerResult.incremented}`);
                                await new Promise(r => setTimeout(r, 500));
                            } else {
                                // часть чанков могла остаться без валидного JSON —
                                // страницу не помечаем обработанной, чтобы можно было
                                // повторить именно её
                                if (!nerResult.skipped) markNerDone(current.key);
                                progressStatus(`✨ +${nerResult.added} новых, обновлено частот: ${nerResult.incremented}`
                                    + (nerResult.resumed ? ` • продолжен с чанка ${nerResult.resumed + 1}` : '')
                                    + (nerResult.skipped ? ` • ⚠️ чанков без валидного ответа: ${nerResult.skipped} (${nerResult.skippedReason})` : ''));
                                await new Promise(r => setTimeout(r, 800));
                            }
                        } catch (error) {
                            if (!cancelRequested) {
                                progressStatus('⚠️ NER: ' + error.message);
                                await new Promise(r => setTimeout(r, 1500));
                            }
                        }
                    }
                }
                if (cancelRequested) {
                    progressShow('⏹ Отменено');
                    progressStatus('Отменено пользователем');
                    readerContent.innerHTML = '<div class="nm-reader-loading">⏹ Перевод отменён<br><small>Меню ⋮ → «🌐 Перевести» продолжит с сохранённого места</small></div>';
                    return;
                }
                progressShow(title);
                const translationResult = await translateWithStreaming(readerContent, text, { bookKey: current.key });
                const full = translationResult.text || '';
                // навигация обновляется по живой странице независимо от успеха перевода
                const nav = resolveNavFromLive();
                if (readerState) {
                    readerState.text = full;
                    readerState.nextUrl = nav.nextUrl;
                    readerState.prevUrl = nav.prevUrl;
                    readerState.tocUrl = nav.tocUrl;
                }
                updateNavButtons();
                // в кэш — только завершённый перевод (данные собираем из локальных
                // переменных: readerState мог быть обнулён закрытой читалкой);
                // незаконченный остаётся в фоновом задании
                if (translationResult.completed && full) {
                    cacheSet(url, { url, title: document.title, text: full, ...nav }, current.key);
                    flowCompleted = true;
                } else if (full) {
                    progressShow('⏳ Перевод не завершён');
                    progressStatus(`${translationResult.error || 'обрыв связи'} • сохранено ${full.length} зн.`
                        + ' • ⋮ → «🌐 Перевести» продолжит с сохранённого места');
                } else {
                    progressShow('⏳ Перевод не завершён');
                    progressStatus(`${translationResult.error || 'обрыв связи'} • ни один чанк не дошёл целиком`);
                }
            }
            if (!cancelRequested && config.preemptiveTranslation && readerState && readerState.nextUrl) pretranslateNext(readerState.nextUrl);
        } finally {
            isTranslating = false;
            updateNavButtons();
            // оборванный перевод НЕ прячет прогресс: панель с причиной остаётся
            // перед глазами, а не превращается в «всё готово»
            if (flowCompleted) setTimeout(progressHide, 2500);
        }
    }
    async function handleExtractTerms() {
        if (isTranslating) return;
        dropdownMenu.classList.remove('active');
        const current = getCurrentBook();
        if (!current) { alert('Глоссарий привязан к книге — откройте её страницу (книга определяется по заданному URL)'); openBookModal(); return; }
        if (!config.apiKey && !config.localModel) { alert('Укажите API Key в настройках или включите «Локальная модель без API-ключа»'); openModal(); return; }
        const element = findContentElement();
        const text = extractMainText(element);
        if (!text.trim()) { alert('Текст не найден'); return; }
        isTranslating = true;
        cancelRequested = false;
        extractMiniShow();
        try {
            const result = await extractTermsFromText(text, current.key, updateExtractionProgressMini);
            if (!result.canceled && !result.skipped) markNerDone(current.key);
            extractMiniStatus((result.canceled ? '⏹ Остановлено: ' : '✨ ')
                + `+${result.added} новых, обновлено частот: ${result.incremented}`
                + (result.resumed ? ` • продолжен с чанка ${result.resumed + 1}` : '')
                + (result.skipped ? ` • ⚠️ без валидного ответа: ${result.skipped} (${result.skippedReason})` : ''));
            updateGlossaryUI();
            refreshBookTab();
        } catch (error) {
            extractMiniStatus('❌ ' + error.message);
        } finally {
            isTranslating = false;
            extractMiniHide(2600);
        }
    }
    // ===== ОБУЧЕНИЕ =====
    function trainingIgnore(e) {
        const path = typeof e.composedPath === 'function' ? e.composedPath() : [e.target];
        return path.includes(host);
    }
    function trainHighlight(el) {
        if (trainingHighlightedEl && trainingHighlightedEl !== el) trainingHighlightedEl.classList.remove('nm-training-highlight');
        trainingHighlightedEl = el;
        el.classList.add('nm-training-highlight');
    }
    function openTrainPopupFor(el) {
        trainingPopupTarget = el;
        const rect = el.getBoundingClientRect();
        const popupW = Math.min(250, window.innerWidth - 24), popupH = 250;
        let top = rect.bottom + 8;
        if (top + popupH > window.innerHeight) top = Math.max(8, rect.top - popupH - 8);
        let left = Math.min(Math.max(8, rect.left), window.innerWidth - popupW - 8);
        trainingPopup.style.top = top + 'px';
        trainingPopup.style.left = left + 'px';
        trainingPopup.classList.add('active');
    }
    function onTrainTouchStart(e) {
        if (!elementTrainingMode || trainingIgnore(e) || (e.touches && e.touches.length > 1)) return;
        trainTouchHandledAt = Date.now();
        const el = e.target;
        if (!el || el.nodeType !== 1) return;
        trainHighlight(el);
        trainTouchT = Date.now();
        const t = e.touches && e.touches[0];
        trainTouchXY = t ? [t.clientX, t.clientY] : null;
    }
    function onTrainTouchEnd(e) {
        if (!elementTrainingMode || trainingIgnore(e)) return;
        // отменённый touchend подавляет синтетический click и long-press-меню:
        // от тапов обучения сайт не реагирует (ссылки не открываются, выделения нет)
        e.preventDefault();
        e.stopPropagation();
        trainTouchHandledAt = Date.now();
        const el = trainingHighlightedEl || e.target;
        if (!el || el.nodeType !== 1) return;
        const t = e.changedTouches && e.changedTouches[0];
        // свайп (прокрутка) — не выбор элемента
        if (t && trainTouchXY && Math.hypot(t.clientX - trainTouchXY[0], t.clientY - trainTouchXY[1]) > 15) { trainTapEl = null; return; }
        if (Date.now() - trainTouchT >= LONG_PRESS_DELAY) {
            trainTapEl = null;
            openTrainPopupFor(el);
            return;
        }
        const now = Date.now();
        // двойной тап по тому же элементу (или его внутреннему) — выбор
        if (trainTapEl && (trainTapEl === el || trainTapEl.contains(el) || el.contains(trainTapEl)) && now - trainTapTime < DOUBLE_TAP_DELAY) {
            trainTapEl = null;
            openTrainPopupFor(el);
        } else {
            trainTapEl = el;
            trainTapTime = now;
        }
    }
    function onTrainTouchCancel() { trainTapEl = null; }
    function onTrainContextMenu(e) {
        if (elementTrainingMode) e.preventDefault();
    }
    function onTrainMouseOver(e) {
        if (!elementTrainingMode || trainingIgnore(e)) return;
        // на тач-устройствах тапы уже обработаны touch-конвейером (мышь-эмуляция идёт следом)
        if (Date.now() - trainTouchHandledAt < 800) return;
        const el = e.target;
        if (!el || el.nodeType !== 1 || el === document.documentElement) return;
        if (trainingHighlightedEl && trainingHighlightedEl !== el) trainingHighlightedEl.classList.remove('nm-training-highlight');
        trainingHighlightedEl = el;
        el.classList.add('nm-training-highlight');
    }
    function onTrainClick(e) {
        if (!elementTrainingMode || trainingIgnore(e)) return;
        e.preventDefault();
        e.stopPropagation();
        if (Date.now() - trainTouchHandledAt < 800) return;
        const el = trainingHighlightedEl || e.target;
        if (!el || el.nodeType !== 1) return;
        openTrainPopupFor(el);
    }
    function startElementTraining() {
        const cur = getCurrentBook();
        if (!cur) {
            pendingTranslateAfterTraining = false;
            alert('Сначала определите книгу');
            openBookModal();
            return;
        }
        elementTrainingMode = true;
        trainTapEl = null; trainTouchXY = null;
        const touchHint = shadow.querySelector('#nm-touch-hint');
        if (touchHint && window.matchMedia('(pointer: coarse)').matches) touchHint.style.display = 'inline';
        dropdownMenu.classList.remove('active');
        elementTraining.classList.add('active');
        trainingPopup.classList.remove('active');
        document.body.classList.add('nm-training-on');
        document.addEventListener('mouseover', onTrainMouseOver, true);
        document.addEventListener('click', onTrainClick, true);
        document.addEventListener('touchstart', onTrainTouchStart, true);
        document.addEventListener('touchend', onTrainTouchEnd, { capture: true, passive: false });
        document.addEventListener('touchcancel', onTrainTouchCancel, true);
        document.addEventListener('contextmenu', onTrainContextMenu, true);
    }
    function stopElementTraining() {
        elementTrainingMode = false;
        elementTraining.classList.remove('active');
        trainingPopup.classList.remove('active');
        document.removeEventListener('mouseover', onTrainMouseOver, true);
        document.removeEventListener('click', onTrainClick, true);
        document.removeEventListener('touchstart', onTrainTouchStart, true);
        document.removeEventListener('touchend', onTrainTouchEnd, true);
        document.removeEventListener('touchcancel', onTrainTouchCancel, true);
        document.removeEventListener('contextmenu', onTrainContextMenu, true);
        document.body.classList.remove('nm-training-on');
        if (trainingHighlightedEl) { trainingHighlightedEl.classList.remove('nm-training-highlight'); trainingHighlightedEl = null; }
        document.querySelectorAll('.nm-training-picked').forEach(el => el.classList.remove('nm-training-picked'));
        trainingPopupTarget = null;
        trainTapEl = null;
    }
    function generateCSSSelector(element) {
        if (element.id) return '#' + cssEsc(element.id);
        const classes = Array.from(element.classList);
        for (const cls of classes) {
            const sel = '.' + cssEsc(cls);
            try { if (document.querySelectorAll(sel).length === 1) return sel; } catch {}
        }
        const path = [];
        let current = element;
        while (current && current.nodeType === 1 && current !== document.body) {
            let selector = current.tagName.toLowerCase();
            if (current.id) { path.unshift('#' + cssEsc(current.id)); break; }
            const parent = current.parentElement;
            if (parent) {
                const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName);
                if (siblings.length > 1) selector += `:nth-of-type(${siblings.indexOf(current) + 1})`;
            }
            path.unshift(selector);
            current = parent;
        }
        return path.join(' > ');
    }
    function assignElementType(type) {
        if (!trainingPopupTarget) return;
        const cur = getCurrentBook();
        if (!cur) return;
        if (!cur.book.selectors) cur.book.selectors = {};
        cur.book.selectors[type] = elementSignature(trainingPopupTarget);
        GM_setValue('books', books);
        trainingPopupTarget.classList.remove('nm-training-highlight');
        trainingPopupTarget.classList.add('nm-training-picked');
        trainingPopup.classList.remove('active');
        trainingHighlightedEl = null;
        trainingPopupTarget = null;
    }
    function finishTraining() {
        stopElementTraining();
        const sel = getBookSelectors();
        if (pendingTranslateAfterTraining) {
            pendingTranslateAfterTraining = false;
            if (sel.content) runTranslationFlow(false);
            else alert('Не обучен блок текста — читалка не запущена.');
        }
    }

    // ===== КНИГА: ОБЛОЖКА И ЭКСПОРТ TXT =====
    function findCoverCandidates() {
        const candidates = new Set();
        const add = (u) => { try { const abs = new URL(u, location.href).href; if (/^https?:/i.test(abs)) candidates.add(abs); } catch {} };
        const meta = document.querySelector('meta[property="og:image"], meta[name="og:image"], meta[property="twitter:image"], meta[property="twitter:image:src"]');
        if (meta && meta.content) add(meta.content);
        document.querySelectorAll('link[rel="image_src"]').forEach(l => { if (l.href) add(l.href); });
        const seen = new Set();
        const scan = (root) => {
            if (!root || !root.querySelectorAll) return;
            root.querySelectorAll('img').forEach(img => {
                const src = img.currentSrc || img.src;
                if (!src || seen.has(src)) return;
                // layout-размер (атрибуты) честнее natural: lazy-load картинки часто 1x1-заглушки
                const w = img.width || img.naturalWidth || 0, h = img.height || img.naturalHeight || 0;
                // обложка обычно вертикальная; lazy-load картинки без размеров тоже проускаем
                if ((w >= 120 && h >= 160) || (!w && !h)) { seen.add(src); add(src); }
            });
        };
        scan(findContentElement());
        document.querySelectorAll('[class*="cover"], [id*="cover"]').forEach(el => scan(el));
        return [...candidates].slice(0, 12);
    }
    // экспорт TXT — текущая переведённая страница: её кэшированный перевод
    function exportChapterToTxt() {
        const data = cacheGet(pageCacheKey());
        if (!data || !data.text) { showStatus('Текущая страница ещё не переведена — нечего экспортировать', 'error', 'status-book'); return; }
        const bookName = (currentBookKey && books[currentBookKey] && books[currentBookKey].name) || 'chapter';
        const title = String(data.title || document.title || '').trim();
        let fullText = `«${bookName}»${title ? ` — ${title}` : ''}\n${'='.repeat(60)}\n\n${data.text}\n`;
        const blob = new Blob([fullText], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${String(bookName).replace(/[^\wа-яА-ЯёЁ \-]/g, '').trim().slice(0, 60) || 'book'}_translated.txt`;
        a.click();
        URL.revokeObjectURL(url);
        showStatus('TXT экспортирован: текущая переведённая страница', 'success', 'status-book');
        // в читалке статус-блоки модалки невидимы — ответ показываем в её нижней строке
        const rs = $('#reader-preload-status');
        if (rs && readerModeActive) {
            rs.style.display = '';
            rs.textContent = '✅ TXT сохранён';
            setTimeout(() => { rs.style.display = 'none'; }, 3000);
        }
    }

    // ===== ГЛОССАРИЙ CRUD =====
    function addTerm() {
        const term = $('#new-term').value.trim();
        const translation = $('#new-translation').value.trim();
        if (!term || !translation) { showStatus('Заполните термин и перевод', 'error', 'status-glossary'); return; }
        const glossary = getGlossaryForView();
        for (const ex of Object.values(glossary)) {
            if (normalize(ex.term) === normalize(term) || termMatchesText(ex.term, term, config.fuzzySearchThreshold)) {
                showStatus(`Похожий термин уже есть: "${ex.term}"`, 'error', 'status-glossary');
                return;
            }
        }
        glossary[`${normalize(term)}_${Date.now()}`] = { term, translation, type: $('#new-type').value.trim() || 'Term', count: 1 };
        saveGlossary(glossary);
        $('#new-term').value = '';
        $('#new-translation').value = '';
        glossaryPage = 0;
        glossarySort = { field: 'count', dir: 'desc' };
        updateGlossaryUI();
        showStatus('Термин добавлен!', 'success', 'status-glossary');
    }
    function importGlossary() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.json';
        input.onchange = e => {
            const reader = new FileReader();
            reader.onload = ev => {
                try {
                    const imported = JSON.parse(ev.target.result);
                    const srcList = Array.isArray(imported) ? imported : Object.entries(imported).map(([, v]) => v);
                    const glossary = getGlossaryForView();
                    let added = 0, incremented = 0;
                    for (const raw of srcList) {
                        const t = migrateEntry({ ...raw });
                        if (!t || !t.term || !t.translation) continue;
                        let existingId = null;
                        for (const [exId, ex] of Object.entries(glossary)) {
                            if (normalize(ex.term) === normalize(t.term) || termMatchesText(ex.term, t.term, config.fuzzySearchThreshold)) { existingId = exId; break; }
                        }
                        const importedCount = parseInt(t.count, 10);
                        const cnt = Number.isFinite(importedCount) && importedCount > 0 ? importedCount : 1;
                        if (existingId) { glossary[existingId].count = (glossary[existingId].count || 0) + cnt; incremented++; }
                        else { glossary[`${normalize(t.term)}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`] = { ...t, count: cnt }; added++; }
                    }
                    saveGlossary(glossary);
                    updateGlossaryUI();
                    showStatus(`Импортировано ${added} новых, обновлено частот: ${incremented}`, 'success', 'status-glossary');
                } catch (err) { showStatus('Ошибка файла: ' + err.message, 'error', 'status-glossary'); }
            };
            reader.readAsText(e.target.files[0]);
        };
        input.click();
    }
    function exportGlossary() {
        const glossary = getGlossaryForView();
        const name = (currentBookKey && books[currentBookKey] && books[currentBookKey].name) || 'book';
        const blob = new Blob([JSON.stringify(glossary, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `glossary-${name}-${new Date().toISOString().slice(0, 10)}.json`;
        a.click();
        URL.revokeObjectURL(url);
        showStatus('Глоссарий экспортирован!', 'success', 'status-glossary');
    }
    function clearGlossary() {
        const bookKey = currentBookKey;
        if (!bookKey || !books[bookKey]) return;
        if (!confirm(`Очистить глоссарий книги "${books[bookKey].name || bookKey}"? Кэш страниц с извлечёнными терминами тоже будет очищен.`)) return;
        saveGlossary({});
        clearNerCache(bookKey);
        glossaryPage = 0;
        updateGlossaryUI();
        refreshBookTab();
        showStatus('Глоссарий очищен', 'success', 'status-glossary');
    }

    // ===== НАСТРОЙКИ =====
    function loadSettings() {
        $('#api-host').value = config.apiHost;
        $('#api-key').value = config.apiKey;
        $('#model').value = config.model;
        $('#reasoning-effort').value = config.reasoningEffort;
        $('#request-timeout').value = config.requestTimeout;
        $('#max-retries').value = config.maxRetries;
        $('#chunk-size').value = config.chunkSize;
        $('#source-lang').value = config.sourceLang;
        $('#target-lang').value = config.targetLang;
        $('#fuzzy-threshold').value = config.fuzzySearchThreshold;
        $('#auto-ner').checked = !!config.autoNER;
        $('#glossary-current-only').checked = !!config.glossaryCurrentPageOnly;
        $('#local-model').checked = !!config.localModel;
        $('#api-key').disabled = !!config.localModel;
        $('#preemptive-translate').checked = !!config.preemptiveTranslation;
        $('#gm-transport').checked = (config.gmTransport === 'auto' ? 'page' : config.gmTransport) === 'manager';
        $('#reader-theme').value = config.readerTheme;
        $('#reader-font-family').value = config.readerFontFamily;
        $('#reader-font-size').value = config.readerFontSize;
        $('#reader-line-height').value = config.readerLineHeight;
        $('#reader-paragraph-spacing').value = config.readerParagraphSpacing;
        $('#reader-content-width').value = config.readerContentWidth;
        $('#translation-prompt').value = config.translationPrompt;
        $('#extraction-prompt').value = config.extractionPrompt;
    }
    const SETTING_FIELDS = [
        ['#api-host', 'apiHost', v => v.trim()],
        ['#api-key', 'apiKey', v => v.trim()],
        ['#model', 'model', v => v.trim()],
        ['#reasoning-effort', 'reasoningEffort', v => v],
        ['#request-timeout', 'requestTimeout', v => { const n = parseInt(v, 10); return Number.isFinite(n) && n >= 0 ? n : 10; }],
        ['#max-retries', 'maxRetries', v => { const n = parseInt(v, 10); return Number.isFinite(n) ? Math.min(10, Math.max(0, n)) : 3; }],
        ['#chunk-size', 'chunkSize', v => { const n = parseInt(v, 10); return Number.isFinite(n) && n > 0 ? n : DEFAULT_CONFIG.chunkSize; }],
        ['#source-lang', 'sourceLang', v => v],
        ['#target-lang', 'targetLang', v => v],
        ['#fuzzy-threshold', 'fuzzySearchThreshold', v => { const f = parseFloat(v); return Number.isFinite(f) ? f : DEFAULT_CONFIG.fuzzySearchThreshold; }],
        ['#reader-theme', 'readerTheme', v => v],
        ['#reader-font-family', 'readerFontFamily', v => v],
        ['#reader-font-size', 'readerFontSize', v => { const n = parseInt(v, 10); return Number.isFinite(n) && n > 0 ? n : DEFAULT_CONFIG.readerFontSize; }],
        ['#reader-line-height', 'readerLineHeight', v => { const f = parseFloat(v); return Number.isFinite(f) ? f : DEFAULT_CONFIG.readerLineHeight; }],
        ['#reader-paragraph-spacing', 'readerParagraphSpacing', v => { const f = parseFloat(v); return Number.isFinite(f) ? f : DEFAULT_CONFIG.readerParagraphSpacing; }],
        ['#reader-content-width', 'readerContentWidth', v => { const n = parseInt(v, 10); return Number.isFinite(n) ? Math.min(100, Math.max(30, n)) : DEFAULT_CONFIG.readerContentWidth; }],
        ['#translation-prompt', 'translationPrompt', v => v],
        ['#extraction-prompt', 'extractionPrompt', v => v]
    ];
    let settingsSaveTimer = null;
    function persistSettings() {
        GM_setValue('config', config);
        showStatus('✅ Настройки сохранены автоматически', 'success', 'status-settings');
        applyTheme();
    }
    function scheduleSettingsSave() {
        clearTimeout(settingsSaveTimer);
        settingsSaveTimer = setTimeout(persistSettings, 400);
    }
    function bindSettingsAutoSave() {
        for (const [sel, key, parse] of SETTING_FIELDS) {
            const el = $(sel);
            const save = () => { config[key] = parse(el.value); scheduleSettingsSave(); };
            el.addEventListener('input', save);
            el.addEventListener('change', save);
        }
        $('#auto-ner').addEventListener('change', function() { config.autoNER = this.checked; scheduleSettingsSave(); });
        $('#glossary-current-only').addEventListener('change', function() { config.glossaryCurrentPageOnly = this.checked; glossaryPage = 0; scheduleSettingsSave(); updateGlossaryUI(); });
        $('#local-model').addEventListener('change', function() {
            config.localModel = this.checked;
            $('#api-key').disabled = this.checked;
            scheduleSettingsSave();
        });
        $('#preemptive-translate').addEventListener('change', function() { config.preemptiveTranslation = this.checked; scheduleSettingsSave(); });
        $('#gm-transport').addEventListener('change', function() { config.gmTransport = this.checked ? 'manager' : 'page'; scheduleSettingsSave(); });
    }
    function resetSettings() {
        if (!confirm('Сбросить все настройки к значениям по умолчанию?')) return;
        config = { ...DEFAULT_CONFIG };
        GM_setValue('config', config);
        loadSettings();
        applyTheme();
        showStatus('Настройки сброшены!', 'success', 'status-settings');
    }
    function saveNewBook() {
        const url = $('#book-modal-url').value.trim();
        const name = $('#book-modal-name').value.trim();
        if (!url) { alert('Укажите URL книги'); return; }
        if (books[url] && !confirm('Книга с таким URL уже существует. Заменить название (глоссарий сохранится)?')) return;
        // в записи книги — только метаданные; глоссарий и кэш — в IndexedDB её сайта
        books[url] = {
            ...books[url],
            name: name || 'Без названия',
            selectors: books[url] ? books[url].selectors || {} : {},
            coverUrl: books[url] ? books[url].coverUrl || '' : '',
            openUrl: (books[url] && books[url].openUrl) || url
        };
        currentBookKey = url;
        managedBookKey = url;
        GM_setValue('books', books);
        bookModal.classList.remove('active');
        refreshBookTab();
        updateGlossaryUI();
    }


    // ===== СОБЫТИЯ =====
    $('#btn-translate').addEventListener('click', handleTranslate);
    menuBtn.addEventListener('click', (e) => { e.stopPropagation(); dropdownMenu.classList.toggle('active'); });
    document.addEventListener('click', (e) => {
        const path = typeof e.composedPath === 'function' ? e.composedPath() : [e.target];
        if (!path.includes(menuBtn) && !path.includes(dropdownMenu)) dropdownMenu.classList.remove('active');
    }, true);
    $('#btn-book-menu').addEventListener('click', () => { dropdownMenu.classList.remove('active'); openBookModal(); });
    $('#btn-settings-menu').addEventListener('click', openModal);
    $('#btn-extract-menu').addEventListener('click', handleExtractTerms);
    $('#btn-train-menu').addEventListener('click', () => { pendingTranslateAfterTraining = false; startElementTraining(); });
    $('#nm-close').addEventListener('click', closeModal);
    modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
    bookModal.addEventListener('click', e => { if (e.target === bookModal) bookModal.classList.remove('active'); });
    $('#reader-close').addEventListener('click', closeReader);
    $('#reader-menu').addEventListener('click', (e) => { e.stopPropagation(); $('#reader-menu-panel').classList.toggle('active'); });
    $('#reader-export').addEventListener('click', () => exportChapterToTxt());
    // клик мимо панели меню читалки закрывает её (внутри shadow — composedPath)
    shadow.addEventListener('click', (e) => {
        const panel = $('#reader-menu-panel');
        if (panel && panel.classList.contains('active')) {
            const path = e.composedPath();
            if (!path.includes(panel) && !path.includes($('#reader-menu'))) panel.classList.remove('active');
        }
    });
    $('#reader-theme-toggle').addEventListener('click', () => {
        // три состояния: как в системе → тёмная → светлая; кнопка показывает текущее в title
        config.readerTheme = THEME_MODE_CYCLE[config.readerTheme] || 'auto';
        GM_setValue('config', config);
        applyTheme();
    });
    $('#reader-settings').addEventListener('click', openModal);
    $('#reader-retranslate').addEventListener('click', () => {
        if (isTranslating) return;
        runTranslationFlow(true);
    });
    $('#reader-prev').addEventListener('click', () => gotoChapter(readerState && readerState.prevUrl));
    $('#reader-next').addEventListener('click', () => gotoChapter(readerState && readerState.nextUrl));
    $('#reader-toc').addEventListener('click', () => {
        if (!readerState || !readerState.tocUrl) return;
        let target = null;
        try { target = new URL(readerState.tocUrl, location.href); } catch { return; }
        if (target.protocol === 'http:' || target.protocol === 'https:') openExternalTab(target.href);
    });
    $('#reader-cancel').addEventListener('click', () => {
        cancelRequested = true;
        if (activeReader) { try { activeReader.cancel(); } catch {} }
    });
    $('#btn-finish-training').addEventListener('click', finishTraining);
    $('#btn-cancel-training').addEventListener('click', () => { pendingTranslateAfterTraining = false; stopElementTraining(); });
    $('#btn-training-cancel-pick').addEventListener('click', () => { trainingPopup.classList.remove('active'); trainingPopupTarget = null; });
    trainingPopup.querySelectorAll('[data-type]').forEach(btn => {
        btn.addEventListener('click', () => assignElementType(btn.dataset.type));
    });
    $$('.nm-tab').forEach(tab => {
        tab.addEventListener('click', function() {
            $$('.nm-tab').forEach(t => t.classList.remove('active'));
            $$('.nm-tab-content').forEach(c => c.classList.remove('active'));
            this.classList.add('active');
            $('#tab-' + this.dataset.tab).classList.add('active');
        });
    });
    $('#book-select').addEventListener('change', function() { managedBookKey = this.value || null; renderBookManageArea(); });
    $('#glossary-filter').addEventListener('input', function() { glossaryFilter = this.value; glossaryPage = 0; updateGlossaryUI(); });
    $('#btn-extract-terms').addEventListener('click', handleExtractTerms);
    $('#btn-add-term').addEventListener('click', addTerm);
    $('#btn-import').addEventListener('click', importGlossary);
    $('#btn-export').addEventListener('click', exportGlossary);
    $('#btn-clear-glossary').addEventListener('click', clearGlossary);
    $('#btn-reset-settings').addEventListener('click', resetSettings);
    $('#btn-check-server').addEventListener('click', checkServer);
    $('#btn-save-new-book').addEventListener('click', saveNewBook);
    $('#btn-cancel-new-book').addEventListener('click', () => bookModal.classList.remove('active'));
    $('#btn-autofill-url').addEventListener('click', () => { $('#book-modal-url').value = suggestBookKeyFromUrl(); });

    // ===== ИНИЦИАЛИЗАЦИЯ =====
    currentBookKey = findBookByUrl();
    bindSettingsAutoSave();
    applyTheme();
    let cfgMigrated = false;
    // дефолты читалки: 18px/66% → 14px/80% (сбрасываются только старые дефолты,
    // пользовательские размеры остаются); выполняется один раз на установку
    if (!config.readerDefaultsV2) {
        if (config.readerFontSize === 18) config.readerFontSize = 14;
        if (config.readerContentWidth === 66) config.readerContentWidth = 80;
        config.readerDefaultsV2 = true;
        cfgMigrated = true;
    }
    if (typeof config.requestTimeout === 'number' && config.requestTimeout > 1000) {
        config.requestTimeout = Math.max(1, Math.round(config.requestTimeout / 1000));
        cfgMigrated = true;
    }
    // старые значения ширины колонки были в пикселях, новые — проценты ширины экрана (30-100)
    if (typeof config.readerContentWidth === 'number' && config.readerContentWidth > 100) {
        config.readerContentWidth = DEFAULT_CONFIG.readerContentWidth;
        cfgMigrated = true;
    }
    // наследие эпохи GM-кэша: количество опережаемых глав и лимит кэша убраны,
    // смысл количества наследует чекбокс автоперевода следующей главы
    if ('preemptiveCount' in config) {
        if (typeof config.preemptiveTranslation !== 'boolean') config.preemptiveTranslation = config.preemptiveCount > 0;
        delete config.preemptiveCount;
        cfgMigrated = true;
    }
    if ('cacheLimit' in config) { delete config.cacheLimit; cfgMigrated = true; }
    if ('glossarySource' in config) { delete config.glossarySource; cfgMigrated = true; }
    if (cfgMigrated) GM_setValue('config', config);
    // наследие GM-эпохи удаляем совсем (данные не мигрируем — скрипт в разработке)
    for (const k of ['chapterCache', 'globalGlossary']) {
        if (typeof GM_deleteValue === 'function') GM_deleteValue(k);
        else GM_setValue(k, null);
    }
    // записи книг теперь содержат только метаданные: глоссарии и nerDone из GM убираем
    let booksChanged = false;
    for (const b of Object.values(books)) {
        if ('glossary' in b || 'nerDone' in b) { delete b.glossary; delete b.nerDone; booksChanged = true; }
    }
    if (booksChanged) GM_setValue('books', books);
    // данные сайта грузим из IndexedDB асинхронно; автозапуск перевода при переходе
    // с другой главы дожидается, чтобы не потерять кэш и отметки NER
    const autoRun = !!sessionStorage.getItem('nm_auto_reader');
    sessionStorage.removeItem('nm_auto_reader');
    loadSiteData().then(() => {
        if (modal.classList.contains('active')) { refreshBookTab(); updateGlossaryUI(); }
        if (autoRun) setTimeout(() => handleTranslate(), 400);
    });
    console.log(`NovelMaestro Lite v${APP_VERSION} загружен. Книга:`, currentBookKey || 'не определена');
})();

// ==UserScript==
// @name         NovelMaestro Lite
// @namespace    http://tampermonkey.net/
// @version      1.12
// @description  Универсальный переводчик новелл с глоссарием по книгам и стримингом
// @author       NovelMaestro
// @match        *://*/*
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_xmlhttpRequest
// @connect      *
// @run-at       document-idle
// ==/UserScript==

(() => {
    const APP_VERSION = '1.12';

    // ===== КОНФИГУРАЦИЯ =====
    const DEFAULT_CONFIG = {
        apiHost: 'https://routerai.ru/api/v1',
        apiKey: '',
        model: 'google/gemma-4-31b-it',
        sourceLang: 'Авто',
        targetLang: 'Русский',
        reasoningEffort: 'None',
        chunkSize: 30000,
        requestTimeout: 30000,
        maxRetries: 3,
        localModel: false,
        glossarySource: 'book',
        translationPrompt: 'Переведи следующий текст с {sourceLang} на {targetLang}.\n\nГЛОССАРИЙ ТЕРМИНОВ (обязательно используй эти переводы, сохраняй пол персонажей):\n{glossary}\n\nВАЖНО:\n- Имена и термины переводи точно по глоссарию\n- Сохраняй пол персонажей (он/она) согласно глоссарию\n- Сохраняй стиль оригинала\n- Сохраняй разбивку на абзацы\n- Возвращай ТОЛЬКО перевод, без комментариев\n\nТекст:\n{text}',
        extractionPrompt: 'Извлеки из текста имена персонажей, места, артефакты, организации и важные термины.\n\nВерни JSON в формате:\n{\n  "term": "оригинальный термин",\n  "translation": "перевод на {targetLang} Только 1 вариант перевода!",\n  "type": "Тип записи (Пример: Person (male), Creature (female), Location, Artifact, Organization, Term)"\n}\n\ntype - тип записи. Для живых существ (персонажи, существа) указывай пол в скобках:\n- Person (male) / Person (female) — персонаж мужского/женского пола\n- Person (unknown) — пол неизвестен\n- Creature (male) / Creature (female) — существо\nДля не-персонажей пол не указывай: Location, Artifact, Organization, Term и т.п.\n\nВерни ТОЛЬКО валидный JSON массив объектов. Без дополнительного текста.\n\nТекст:\n{text}',
        fuzzySearchThreshold: 0.7,
        autoNER: true
    };

    let config = { ...DEFAULT_CONFIG, ...GM_getValue('config', {}) };
    let books = GM_getValue('books', {});
    let globalGlossary = GM_getValue('globalGlossary', {});
    let currentBookKey = null;
    let currentGlossaryMode = 'book';
    let selectedBookKey = null;
    let managedBookKey = null;

    let glossarySort = { field: 'count', dir: 'desc' };
    let glossaryPage = 0;
    const PAGE_SIZE = 25;
    let glossaryFilter = '';

    const ngramCache = new Map();
    const MAX_CACHE_SIZE = 1000;

    // ===== УТИЛИТЫ ПОИСКА =====
    function normalize(str) { return String(str).trim().toLowerCase(); }
    function ngrams(str, n = 2) {
        const s = normalize(str);
        const grams = new Set();
        for (let i = 0; i <= s.length - n; i++) grams.add(s.slice(i, i + n));
        return grams;
    }
    function getCachedNgrams(str) {
        if (ngramCache.has(str)) return ngramCache.get(str);
        if (ngramCache.size >= MAX_CACHE_SIZE) ngramCache.clear();
        const g = ngrams(str);
        ngramCache.set(str, g);
        return g;
    }
    function ngramSimilarity(a, b) {
        const g1 = getCachedNgrams(a), g2 = getCachedNgrams(b);
        if (g1.size === 0 || g2.size === 0) return 0;
        let inter = 0;
        const [sm, lg] = g1.size < g2.size ? [g1, g2] : [g2, g1];
        sm.forEach(g => { if (lg.has(g)) inter++; });
        const union = g1.size + g2.size - inter;
        return union === 0 ? 0 : inter / union;
    }
    function splitIntoWords(term) {
        const cjk = /[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]/;
        if (cjk.test(term)) return [term];
        return term.split(/\s+/).filter(w => w.length > 0);
    }
    function isLetterOrDigit(ch) { return /[\p{L}\p{N}]/u.test(ch); }
    function matchWordWithBoundary(text, word) {
        const w = normalize(word), t = text.toLowerCase();
        let idx = 0;
        while ((idx = t.indexOf(w, idx)) !== -1) {
            const before = idx > 0 ? t[idx - 1] : ' ';
            const after = idx + w.length < t.length ? t[idx + w.length] : ' ';
            if (!isLetterOrDigit(before) && !isLetterOrDigit(after)) return true;
            idx += 1;
        }
        return false;
    }
    function fuzzyMatchWord(text, word, threshold) {
        for (const w of splitIntoWords(word)) {
            if (matchWordWithBoundary(text, w)) continue;
            const t = text.toLowerCase(), wl = normalize(w);
            let found = false;
            for (let i = 0; i <= t.length - wl.length; i++) {
                if (ngramSimilarity(w, t.slice(i, i + wl.length)) >= threshold) {
                    const before = i > 0 ? t[i - 1] : ' ';
                    const after = i + wl.length < t.length ? t[i + wl.length] : ' ';
                    if (!isLetterOrDigit(before) && !isLetterOrDigit(after)) { found = true; break; }
                }
            }
            if (!found) return false;
        }
        return true;
    }
    function paragraphsOf(text) { return text.split(/\n+/).map(s => s.trim()).filter(s => s.length > 0); }
    function splitByNewlines(text, chunkSize) {
        const paras = paragraphsOf(text);
        const chunks = [];
        let cur = '';
        for (const p of paras) {
            if (cur.length + p.length + 2 > chunkSize && cur) {
                chunks.push(cur);
                cur = p;
            } else {
                cur += (cur ? '\n\n' : '') + p;
            }
        }
        if (cur) chunks.push(cur);
        return chunks.length > 0 ? chunks : [text];
    }

    // ===== КНИГИ И КЭШ NER =====
    function pageCacheKey() { return location.href.split('#')[0]; }
    function suggestBookKeyFromUrl() {
        const key = location.pathname
            .replace(/\/(chapter|ch|c|p|page|volume|v|ep|episode|txt)\/?\d+\/?/gi, '/')
            .replace(/\/\d+\/?$/, '/')
            .replace(/\/+$/, '')
            .replace(/^\/+/, '');
        return location.hostname + (key ? '/' + key : '');
    }
    function findBookByUrl() {
        const url = location.href;
        for (const key of Object.keys(books)) if (url.includes(key)) return key;
        return null;
    }
    function getCurrentBook() {
        if (!currentBookKey) currentBookKey = findBookByUrl();
        if (currentBookKey && books[currentBookKey]) return { key: currentBookKey, book: books[currentBookKey] };
        return null;
    }
    function isNerDoneForPage(bookKey) {
        const b = books[bookKey];
        return !!(b && b.nerDone && b.nerDone[pageCacheKey()]);
    }
    function markNerDone(bookKey) {
        const b = books[bookKey];
        if (!b) return;
        if (!b.nerDone) b.nerDone = {};
        b.nerDone[pageCacheKey()] = Date.now();
        GM_setValue('books', books);
    }
    function clearNerCache(bookKey) {
        const b = books[bookKey];
        if (!b) return;
        b.nerDone = {};
        GM_setValue('books', books);
    }

    // ===== ГЛОССАРИИ =====
    function getGlossaryForView() {
        if (currentGlossaryMode === 'global') return globalGlossary;
        const key = selectedBookKey || currentBookKey;
        return (key && books[key]) ? (books[key].glossary || {}) : {};
    }
    function saveGlossary(glossary, targetKey) {
        if (targetKey === 'global' || (currentGlossaryMode === 'global' && !targetKey)) {
            globalGlossary = glossary;
            GM_setValue('globalGlossary', globalGlossary);
        } else {
            const key = targetKey || selectedBookKey || currentBookKey;
            if (key && books[key]) { books[key].glossary = glossary; GM_setValue('books', books); }
        }
    }
    function getGlossaryForTranslation() {
        if (config.glossarySource === 'global') return { ...globalGlossary };
        const cur = getCurrentBook();
        return cur ? { ...(cur.book.glossary || {}) } : {};
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
        if (!gender && t.gender && t.gender !== 'null') {
            gender = t.gender === 'neutral' ? 'unknown' : t.gender;
        }
        if (!base) base = gender ? 'Person' : 'Term';
        t.type = (gender === 'male' || gender === 'female' || gender === 'unknown')
            ? `${base} (${gender})` : base;
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
            'Creature (male)', 'Creature (female)', 'Location', 'Artifact',
            'Organisation', 'Term'];
        for (const ty of glossaryTypes(getGlossaryForView())) {
            if (!suggestions.includes(ty)) suggestions.push(ty);
        }
        dl.innerHTML = '';
        for (const ty of suggestions) {
            const opt = document.createElement('option');
            opt.value = ty;
            dl.appendChild(opt);
        }
    }
    function findRelevantTerms(text) {
        const relevant = [];
        const glossary = getGlossaryForTranslation();
        for (const [id, t] of Object.entries(glossary)) {
            if (fuzzyMatchWord(text, t.term, config.fuzzySearchThreshold)) relevant.push({ ...t, id });
        }
        const unique = [];
        const seen = new Set();
        for (const item of relevant) {
            const k = normalize(item.term);
            if (!seen.has(k)) { seen.add(k); unique.push(item); }
        }
        return unique;
    }
    function formatGlossaryForPrompt(terms) {
        if (terms.length === 0) return '(глоссарий пуст)';
        return terms.map(t => `- "${t.term}" → "${t.translation}" [${t.type || 'Term'}]`).join('\n');
    }

    // ===== ИЗВЛЕЧЕНИЕ КОНТЕНТА =====
    function findContentElement() {
        const selectors = [
            '.chapter-content', '.chapter-inner', '.txtnav', '.txt-content', '#txt-content',
            '#booktext', '.booktext', '#chaptercontent', '.chaptercontent', '#readcontent',
            '.readcontent', '.chapter-content', '#chapter-content', '.content-text', '#content-text',
            '.novel-content', '#novel-content', '.article-content', '#article-content',
            'article', '.post-content', '.entry-content', '.text', '.content',
            'main', '#content', '#main', '.chapter', '.reading-content',
            '.story-content', '.reader-content', '.entry', '.post',
            '#TextContent', '.TextContent', '#booktxt', '.booktxt',
            '#chapterbody', '.chapterbody', '#bookcontent', '.bookcontent'
        ];
        let bestEl = null, bestLen = 0;
        for (const sel of selectors) {
            for (const el of document.querySelectorAll(sel)) {
                const len = (el.innerText || '').length;
                if (len > bestLen) { bestLen = len; bestEl = el; }
            }
        }
        return bestEl || document.body;
    }
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

    // ===== UI (SHADOW DOM) =====
    const styles = `
        <style>
            #nm-root, #nm-root * { letter-spacing: normal; word-spacing: normal; text-indent: 0; box-sizing: border-box; }
            #nm-root { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; font-size: 14px; color: #111827; }
            #nm-buttons { position: fixed; bottom: 20px; right: 20px; z-index: 2147483647; display: flex; gap: 8px; }
            .nm-btn-float { background: #2563eb; color: white; border: none; padding: 12px 16px; border-radius: 8px; cursor: pointer; font-size: 20px; min-width: 50px; box-shadow: 0 4px 12px rgba(37,99,235,.3); transition: all .2s; }
            .nm-btn-float:hover { background: #1d4ed8; transform: translateY(-2px); }
            .nm-btn-float:disabled { background: #93c5fd; cursor: not-allowed; transform: none; }
            .nm-btn-float.nm-settings { background: #6b7280; }
            .nm-btn-float.nm-settings:hover { background: #4b5563; }
            .nm-modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 2147483647; }
            .nm-modal.active { display: flex; align-items: center; justify-content: center; }
            .nm-modal-content { background: white; border-radius: 12px; max-width: 900px; width: 95%; max-height: 90vh; overflow-y: auto; padding: 24px; box-shadow: 0 20px 60px rgba(0,0,0,.3); }
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
            .nm-glossary-table th {
                background: #f3f4f6; padding: 8px 10px; text-align: left; font-size: 12px;
                font-weight: 600; color: #374151; border-bottom: 2px solid #e5e7eb;
                cursor: pointer; user-select: none; white-space: nowrap;
            }
            .nm-glossary-table th:hover { background: #e5e7eb; }
            .nm-glossary-table th .nm-sort { font-size: 10px; margin-left: 4px; color: #9ca3af; }
            .nm-glossary-table th.active-sort { background: #dbeafe; color: #1e40af; }
            .nm-glossary-table th.active-sort .nm-sort { color: #2563eb; }
            .nm-glossary-table td {
                padding: 6px 8px; border-bottom: 1px solid #e5e7eb; vertical-align: middle;
            }
            .nm-glossary-table tr:hover td { background: #f9fafb; }
            .nm-glossary-table input, .nm-glossary-table select {
                padding: 5px 6px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 13px;
                background: white; width: 100%;
            }
            .nm-glossary-table input:focus, .nm-glossary-table select:focus {
                outline: none; border-color: #2563eb; box-shadow: 0 0 0 2px rgba(37,99,235,.1);
            }
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
            .nm-glossary-count { background: #2563eb; color: white; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-left: 8px; }
            .nm-add-form {
                display: grid; grid-template-columns: 1fr 1fr 1.4fr auto;
                gap: 8px; padding: 12px; background: #eff6ff; border-radius: 6px;
                border: 1px solid #bfdbfe; margin-bottom: 12px;
            }
            .nm-add-form input, .nm-add-form select { padding: 8px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 13px; background: white; }
            .nm-filter-row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
            .nm-filter-row input { flex: 1; padding: 8px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 13px; }
            .nm-pagination { display: flex; gap: 8px; align-items: center; justify-content: center; margin-top: 12px; flex-wrap: wrap; }
            .nm-pagination button { padding: 6px 12px; border: 1px solid #d1d5db; background: white; border-radius: 4px; cursor: pointer; font-size: 13px; }
            .nm-pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
            .nm-pagination button.active { background: #2563eb; color: white; border-color: #2563eb; }
            .nm-pagination .nm-page-info { color: #6b7280; font-size: 13px; }
            #nm-streaming-panel { display: none; position: fixed; top: 20px; right: 20px; background: white; padding: 16px; border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,.2); z-index: 2147483646; max-width: 400px; color: #111827; }
            #nm-streaming-panel.active { display: block; }
            .nm-progress-bar { width: 100%; height: 6px; background: #e5e7eb; border-radius: 3px; overflow: hidden; margin-top: 8px; }
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
            .nm-backup-section {
                margin-top: 24px; padding-top: 20px; border-top: 2px solid #e5e7eb;
            }
            .nm-backup-section h3 { font-size: 15px; color: #1f2937; margin-bottom: 8px; }
        </style>
    `;

    const host = document.createElement('div');
    host.id = 'nm-lite-host';
    document.documentElement.appendChild(host);
    const shadow = host.attachShadow({ mode: 'open' });
    const uiFrag = document.createRange().createContextualFragment(`
        ${styles}
        <div id="nm-root">
            <div id="nm-buttons">
                <button class="nm-btn-float" id="btn-translate" title="Перевести страницу">🌐</button>
                <button class="nm-btn-float nm-settings" id="btn-settings" title="Настройки">⚙️</button>
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
                        <div class="nm-input-group">
                            <label>Работать с:</label>
                            <select class="nm-select" id="glossary-selector">
                                <option value="global">🌍 Глобальный глоссарий</option>
                            </select>
                        </div>

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
                        </div>

                        <div id="glossary-list"></div>
                        <div class="nm-pagination" id="glossary-pagination"></div>

                        <div class="nm-status" id="status-glossary"></div>
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
                                <label for="local-model">🖥️ Локальная модель без API-ключа (заголовок Authorization не отправляется)</label>
                            </div>
                            <div class="nm-input-group"><label>Модель:</label><input type="text" class="nm-input" id="model"></div>
                            <div class="nm-input-group"><label>Уровень reasoning:</label>
                                <input type="text" class="nm-input" id="reasoning-effort" list="nm-reasoning-list" placeholder="None">
                                <datalist id="nm-reasoning-list">
                                    <option value="None"></option>
                                    <option value="minimal"></option>
                                    <option value="low"></option>
                                    <option value="medium"></option>
                                    <option value="high"></option>
                                </datalist>
                                <small>Пусто или 'None' — параметр не передаётся. Любое другое значение передаётся как есть.</small>
                            </div>
                            <button class="nm-btn nm-btn-sm nm-btn-primary" id="btn-check-server">🔌 Проверить сервер</button>
                            <div class="nm-server-status" id="server-status"></div>
                        </div>
                        <div class="nm-section">
                            <h3>🌐 Сеть</h3>
                            <div class="nm-input-group"><label>Таймаут зависания (мс):</label>
                                <input type="number" class="nm-input" id="request-timeout" min="0" step="1000">
                                <small>0 = без таймаута. Обычно — общий лимит запроса; при стриминге — это пауза без данных: не пришло ни одного символа за это время — запрос считается зависшим и повторяется (с ретраями). По умолчанию 30000.</small>
                            </div>
                            <div class="nm-input-group"><label>Количество ретраев при ошибке:</label>
                                <input type="number" class="nm-input" id="max-retries" min="0" max="10">
                                <small>Повторные попытки при сетевых ошибках, таймаутах и зависании стриминга (не при HTTP 4xx/5xx). Незаконченный чанк зависшего запроса при ретрае переводится заново; на прогрессбаре ретрай — оранжевые полосы.</small>
                            </div>
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
                            <div class="nm-input-group"><label>Глоссарий при переводе:</label>
                                <div class="nm-radio-group">
                                    <label><input type="radio" name="glossary-source" value="book"> 📚 Текущей книги</label>
                                    <label><input type="radio" name="glossary-source" value="global"> 🌍 Глобальный</label>
                                </div>
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
                            <h3>💬 Промпты</h3>
                            <div class="nm-input-group"><label>Промпт перевода ({sourceLang}, {targetLang}, {glossary}, {text}):</label>
                                <textarea class="nm-textarea" id="translation-prompt"></textarea>
                            </div>
                            <div class="nm-input-group"><label>Промпт извлечения терминов ({targetLang}, {text}):</label>
                                <textarea class="nm-textarea" id="extraction-prompt"></textarea>
                            </div>
                        </div>
                        <div class="nm-help" style="margin-bottom:8px;">ℹ️ Настройки сохраняются автоматически при каждом изменении — отдельная кнопка сохранения не нужна.</div>
                        <button class="nm-btn nm-btn-secondary" id="btn-reset-settings">Сбросить настройки</button>
                        <div class="nm-status" id="status-settings"></div>

                        <div class="nm-backup-section">
                            <h3>💾 Полный бэкап</h3>
                            <p style="font-size:13px;color:#6b7280;margin:0 0 8px 0;">
                                Экспортирует/импортирует <b>все</b> данные: все книги с глоссариями, кэш NER, глобальный глоссарий, настройки. Удобно для переноса на другой компьютер.
                            </p>
                            <button class="nm-btn nm-btn-secondary" id="btn-full-export">📤 Экспорт всех данных</button>
                            <button class="nm-btn nm-btn-secondary" id="btn-full-import">📥 Импорт всех данных</button>
                            <div class="nm-status" id="status-backup"></div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="nm-modal" id="nm-book-modal">
                <div class="nm-modal-content" style="max-width:600px;">
                    <h2 style="margin-top:0;">📚 Определение книги</h2>
                    <div class="nm-help">Укажите URL книги (без номера главы) и название.</div>
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

            <div id="nm-streaming-panel">
                <div id="stream-title" style="font-weight:600;margin-bottom:8px;">🔄 Перевод...</div>
                <div id="stream-status" style="font-size:13px;color:#6b7280;">Подготовка...</div>
                <div class="nm-progress-bar"><div class="nm-progress-fill" id="progress-fill"></div></div>
                <button class="nm-btn nm-btn-danger" id="btn-cancel" style="margin-top:12px;width:100%;">Отменить</button>
            </div>
        </div>
    `);
    shadow.appendChild(uiFrag);

    const $ = sel => shadow.querySelector(sel);
    const $$ = sel => shadow.querySelectorAll(sel);

    const modal = $('#nm-modal');
    const bookModal = $('#nm-book-modal');
    const streamingPanel = $('#nm-streaming-panel');
    let isTranslating = false;
    let cancelRequested = false;
    let activeReader = null;

    // ===== UI-СЛУЖЕБНЫЕ =====
    function showStatus(msg, type = 'info', id = 'status-book') {
        const el = $('#' + id);
        if (!el) return;
        el.textContent = msg;
        el.className = 'nm-status ' + type;
    }
    function hideStatus(id = 'status-book') {
        const el = $('#' + id);
        if (el) { el.className = 'nm-status'; el.style.display = 'none'; }
    }
    function openModal() {
        modal.classList.add('active');
        refreshBookTab();
        refreshGlossarySelector();
        updateGlossaryUI();
        loadSettings();
    }
    function closeModal() {
        modal.classList.remove('active');
        ['status-book', 'status-glossary', 'status-settings', 'status-backup'].forEach(hideStatus);
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
        if (!managedBookKey || !books[managedBookKey]) {
            managedBookKey = currentBookKey || keys[0] || null;
        }
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
            help.textContent = '⚠️ Текущая страница не привязана к книге.';
            const btn = document.createElement('button');
            btn.className = 'nm-btn nm-btn-primary';
            btn.id = 'btn-define-book';
            btn.textContent = '📚 Привязать текущую страницу к книге';
            btn.addEventListener('click', openBookModal);
            area.replaceChildren(help, btn);
            return;
        }
        const book = books[key];
        const terms = Object.keys(book.glossary || {}).length;
        const nerPages = Object.keys(book.nerDone || {}).length;
        const info = document.createElement('div');
        info.className = 'nm-book-info';
        const strong = document.createElement('strong');
        strong.textContent = `📖 ${book.name || 'Без названия'}`;
        const urlLine = document.createElement('small');
        urlLine.setAttribute('style', 'color:#6b7280;');
        urlLine.textContent = `URL: ${key}`;
        const statsLine = document.createElement('small');
        statsLine.setAttribute('style', 'color:#6b7280;');
        statsLine.textContent = `Терминов: ${terms} | Страниц с извлечёнными терминами: ${nerPages}`;
        info.append(strong, document.createElement('br'), urlLine, document.createElement('br'), statsLine);
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
        const saveBtn = document.createElement('button');
        saveBtn.className = 'nm-btn nm-btn-primary';
        saveBtn.id = 'btn-save-book';
        saveBtn.textContent = '💾 Сохранить';
        const delBtn = document.createElement('button');
        delBtn.className = 'nm-btn nm-btn-danger';
        delBtn.id = 'btn-delete-book';
        delBtn.textContent = '🗑 Удалить книгу';
        area.replaceChildren(info,
            mkGroup('Название книги:', 'book-name-edit', book.name || ''),
            mkGroup('URL книги:', 'book-key-edit', key),
            saveBtn, delBtn);
        $('#btn-save-book').addEventListener('click', () => {
            const newName = $('#book-name-edit').value.trim();
            const newKey = $('#book-key-edit').value.trim();
            if (!newKey) { showStatus('URL не может быть пустым', 'error', 'status-book'); return; }
            if (newKey === key) {
                book.name = newName;
            } else {
                books[newKey] = { ...book, name: newName };
                delete books[key];
                if (currentBookKey === key) currentBookKey = newKey;
                managedBookKey = newKey;
            }
            GM_setValue('books', books);
            showStatus('Сохранено!', 'success', 'status-book');
            refreshBookTab();
            refreshGlossarySelector();
        });
        $('#btn-delete-book').addEventListener('click', () => {
            if (confirm(`Удалить книгу "${books[key].name || key}" вместе с её глоссарием и кэшем извлечения?`)) {
                delete books[key];
                if (currentBookKey === key) currentBookKey = null;
                managedBookKey = null;
                GM_setValue('books', books);
                showStatus('Книга удалена', 'success', 'status-book');
                refreshBookTab();
                refreshGlossarySelector();
                updateGlossaryUI();
            }
        });
    }

    // ===== ВКЛАДКА "ГЛОССАРИЙ" =====
    function refreshGlossarySelector() {
        const selector = $('#glossary-selector');
        selector.innerHTML = '<option value="global">🌍 Глобальный глоссарий</option>';
        for (const [key, book] of Object.entries(books)) {
            const opt = document.createElement('option');
            opt.value = 'book:' + key;
            opt.textContent = `📚 ${book.name || key}${key === currentBookKey ? '  (текущая страница)' : ''}`;
            selector.appendChild(opt);
        }
        const target = selectedBookKey && books[selectedBookKey] ? 'book:' + selectedBookKey
                     : (currentBookKey && books[currentBookKey] ? 'book:' + currentBookKey : 'global');
        selector.value = target;
        applyGlossarySelectorValue(target);
    }
    function applyGlossarySelectorValue(value) {
        if (value === 'global') { currentGlossaryMode = 'global'; selectedBookKey = null; }
        else if (value.startsWith('book:')) { currentGlossaryMode = 'book'; selectedBookKey = value.slice(5); }
    }
    function sortGlossaryEntries(entries) {
        const { field, dir } = glossarySort;
        if (!field || !dir) return entries;
        const sign = dir === 'desc' ? -1 : 1;
        const valueOf = (e) => field === 'gender' ? genderOf(e[1].type) : e[1][field];
        return [...entries].sort((a, b) => {
            const va = valueOf(a);
            const vb = valueOf(b);
            if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * sign;
            const sa = String(va ?? '').toLowerCase();
            const sb = String(vb ?? '').toLowerCase();
            return sa.localeCompare(sb) * sign;
        });
    }
    function showGlossaryPlaceholder(container, text) {
        const p = document.createElement('p');
        p.setAttribute('style', 'color:#6b7280;text-align:center;padding:20px;');
        p.textContent = text;
        container.replaceChildren(p);
    }
    function updateGlossaryUI() {
        const container = $('#glossary-list');
        const pagination = $('#glossary-pagination');
        const glossary = getGlossaryForView();
        updateTypeDatalist();
        let entries = Object.entries(glossary);
        if (glossaryFilter) {
            const f = normalize(glossaryFilter);
            entries = entries.filter(([, t]) =>
                normalize(t.term).includes(f) || normalize(t.translation).includes(f)
                || normalize(t.type || '').includes(f)
            );
        }
        entries = sortGlossaryEntries(entries);
        const totalPages = Math.max(1, Math.ceil(entries.length / PAGE_SIZE));
        if (glossaryPage >= totalPages) glossaryPage = totalPages - 1;
        if (glossaryPage < 0) glossaryPage = 0;
        const start = glossaryPage * PAGE_SIZE;
        const pageEntries = entries.slice(start, start + PAGE_SIZE);
        $('#glossary-count').textContent = Object.keys(glossary).length;
        if (Object.keys(glossary).length === 0) {
            showGlossaryPlaceholder(container, 'Глоссарий пуст');
            pagination.replaceChildren();
            return;
        }
        if (entries.length === 0) {
            showGlossaryPlaceholder(container, 'Ничего не найдено по фильтру');
            pagination.replaceChildren();
            return;
        }
        const sortIcon = (field) => {
            if (glossarySort.field !== field) return '↕';
            return glossarySort.dir === 'asc' ? '↑' : '↓';
        };
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
            gSel.title = 'Пол персонажа — хранится внутри типа: «Person (male)» и т.п.';
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
                    else if (glossarySort.dir === 'asc') { glossarySort.field = 'count'; glossarySort.dir = 'desc'; }
                } else {
                    glossarySort.field = field;
                    glossarySort.dir = 'desc';
                }
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
        if (totalPages <= 1) {
            container.replaceChildren(pageInfo(`Всего: ${totalItems}`));
            return;
        }
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
        container.querySelector('.nm-prev-btn').addEventListener('click', () => {
            if (glossaryPage > 0) { glossaryPage--; updateGlossaryUI(); }
        });
        container.querySelector('.nm-next-btn').addEventListener('click', () => {
            if (glossaryPage < totalPages - 1) { glossaryPage++; updateGlossaryUI(); }
        });
        container.querySelectorAll('.nm-page-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                glossaryPage = parseInt(btn.dataset.page);
                updateGlossaryUI();
            });
        });
    }

    // ===== HTTP с GM_xmlhttpRequest (обход CORS/Mixed Content) =====
    function makeAbortError(isTimeout) {
        const e = new Error(isTimeout ? 'Таймаут запроса' : 'Запрос прерван');
        e.name = 'AbortError';
        e.isTimeout = !!isTimeout;
        return e;
    }

    // менеджер скриптов без стриминга: весь ответ оборачивается в однократный «поток»
    function streamFromBody(text) {
        const encoded = new TextEncoder().encode(text || '');
        return new ReadableStream({ start(c) { c.enqueue(encoded); c.close(); } });
    }

    function customFetch(url, options, isStream = false) {
        if (typeof GM_xmlhttpRequest === 'undefined') {
            return fetch(url, options);
        }

        return new Promise((resolve, reject) => {
            let settled = false;
            let req = null;
            const abort = () => { try { if (req) req.abort(); } catch {} };
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
                const err = new Error(`HTTP ${info.status}: ${String(body || '').slice(0, 200)}`);
                err.status = info.status;
                return err;
            };

            const reqOptions = {
                method: options.method || 'GET',
                url: url,
                headers: options.headers || {},
                data: options.body,
                responseType: isStream ? 'stream' : 'text',
                onerror: () => settleReject(new Error('NetworkError: Failed to fetch')),
                // без onabort прерванный по таймауту GM-запрос не приводил промис в завершённое состояние — запрос висел навсегда
                onabort: () => settleReject(makeAbortError(false)),
                ontimeout: () => settleReject(makeAbortError(true))
            };

            if (isStream) {
                // стриминг: поток приходит в onloadstart, но статус проверяем заранее —
                // ошибочный ответ не должен притворяться «пустым» стримом
                reqOptions.onloadstart = (response) => {
                    const info = parseInfo(response);
                    if (info.status >= 400) { settleReject(httpError(info, response.responseText)); return; }
                    const stream = response.response;
                    if (stream && typeof stream.getReader === 'function') {
                        settleResolve(Object.assign(info, { ok: true, status: info.status || 200, body: stream }));
                    }
                };
                reqOptions.onload = (response) => {
                    const info = parseInfo(response);
                    if (info.status >= 400) { settleReject(httpError(info, response.responseText)); return; }
                    settleResolve(Object.assign(info, { ok: true, status: info.status || 200, body: streamFromBody(response.responseText) }));
                };
            } else {
                reqOptions.onload = (response) => {
                    const info = parseInfo(response);
                    const body = response.responseText || '';
                    const resp = Object.assign(info, {
                        ok: info.status >= 200 && info.status < 300,
                        text: () => Promise.resolve(body),
                        json: () => {
                            try {
                                return Promise.resolve(JSON.parse(body));
                            } catch (e) {
                                return Promise.reject(e);
                            }
                        }
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

    // Одна попытка запроса. Таймаут: обычный запрос — общее время; стриминг — время
    // без единого символа (зависание). AbortController новый на каждую попытку:
    // переиспользованный aborted-сигнал мгновенно убивал все ретраи (старый баг).
    async function fetchAttempt(url, options, isStream, timeout, cb) {
        const controller = timeout > 0 ? new AbortController() : null;
        const attemptOptions = controller ? { ...options, signal: controller.signal } : options;
        let timer = null;
        let abortedByTimer = false;
        let reader = null;
        const arm = () => {
            if (!controller) return;
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => {
                abortedByTimer = true;
                // AbortSignal не доходит до reader'а некоторых менеджеров: закрываем читателя явно
                if (reader) { try { reader.cancel(); } catch {} }
                controller.abort();
            }, timeout);
        };
        const disarm = () => { if (timer) { clearTimeout(timer); timer = null; } };

        arm();
        try {
            const resp = await customFetch(url, attemptOptions, isStream);
            if (!isStream) {
                if (!resp.ok) {
                    const errText = await resp.text().catch(() => '');
                    const err = new Error(`HTTP ${resp.status}: ${errText.slice(0, 200)}`);
                    err.status = resp.status;
                    throw err;
                }
                return resp;
            }

            if (!resp || !resp.body || typeof resp.body.getReader !== 'function') {
                throw new Error('Сервер не поддерживает стриминг');
            }
            reader = resp.body.getReader();
            activeReader = reader;
            const decoder = new TextDecoder();
            let text = '';
            let buffer = '';
            const handleLine = (line) => {
                const trimmed = line.trim();
                if (!trimmed.startsWith('data:')) return;
                const payload = trimmed.slice(5).trim();
                if (payload === '[DONE]') return;
                try {
                    const parsed = JSON.parse(payload);
                    const content = parsed.choices?.[0]?.delta?.content || '';
                    if (content) { text += content; if (cb.onDelta) cb.onDelta(content); }
                } catch { /* не-JSON data-строка — пропускаем */ }
            };

            while (true) {
                if (cancelRequested) break;
                let done, value;
                try {
                    ({ done, value } = await reader.read());
                } catch (e) {
                    // при отмене стрима read() может не завершиться, а отвалиться —
                    // это тот же конец потока: разбор сделает abortedByTimer/cancelRequested
                    if (abortedByTimer || cancelRequested) break;
                    throw e;
                }
                if (cancelRequested) break;
                if (abortedByTimer) break;
                if (done) break;
                arm();
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();
                lines.forEach(handleLine);
            }
            if (!cancelRequested && !abortedByTimer) {
                // сбрасываем декодер и последнюю data-строку без перевода строки в конце
                buffer += decoder.decode();
                handleLine(buffer);
            }
            if (cancelRequested) throw new Error('Отменено пользователем');
            if (abortedByTimer) throw makeAbortError(true);
            return { text };
        } finally {
            disarm();
            if (reader && activeReader === reader) activeReader = null;
        }
    }

    async function fetchWithRetry(url, options, isStream = false, cb = {}) {
        const timeout = config.requestTimeout > 0 ? config.requestTimeout : 0;
        const maxRetries = config.maxRetries || 0;
        const attemptsTotal = maxRetries + 1;
        let lastErr;

        for (let attempt = 1; attempt <= attemptsTotal; attempt++) {
            if (cancelRequested) throw new Error('Отменено пользователем');
            try {
                return await fetchAttempt(url, options, isStream, timeout, cb);
            } catch (e) {
                if (e.name === 'AbortError') {
                    e.isTimeout = true;
                    e.message = `Таймаут ${timeout}мс: запрос завис`;
                }
                lastErr = e;
                if (e.status >= 400 || cancelRequested || attempt >= attemptsTotal) throw e;
                if (cb.onRetry) cb.onRetry({ nextAttempt: attempt + 1, attemptsTotal, isTimeout: !!e.isTimeout, message: e.message || 'Сетевая ошибка' });
                const delay = Math.min(5000, 500 * 2 ** (attempt - 1));
                await new Promise(r => setTimeout(r, delay));
            }
        }
        throw lastErr;
    }

    function llmRequestOptions(messages, temperature, stream) {
        const body = { model: config.model, messages, temperature, stream: !!stream };
        const re = String(config.reasoningEffort ?? '').trim();
        // Не передаём 'None' в API, чтобы избежать ошибок валидации у провайдеров
        if (re !== '' && re.toLowerCase() !== 'none') body.reasoning_effort = re;
        const headers = { 'Content-Type': 'application/json' };
        // локальная модель без ключа: заголовок Authorization не отправляется вовсе
        if (config.apiKey) headers.Authorization = `Bearer ${config.apiKey}`;
        return {
            url: config.apiHost.replace(/\/$/, '') + '/chat/completions',
            options: { method: 'POST', headers, body: JSON.stringify(body) }
        };
    }

    async function callLLM(messages, temperature, stream, cb = {}) {
        const { url, options } = llmRequestOptions(messages, temperature, stream);
        return await fetchWithRetry(url, options, !!stream, cb);
    }

    // ===== ПРОВЕРКА СЕРВЕРА =====
    async function checkServer() {
        const statusEl = $('#server-status');
        statusEl.className = 'nm-server-status show loading';
        statusEl.textContent = '🔌 Проверяю сервер...';

        if (!config.apiHost) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Не указан API Host'; return; }
        if (!config.apiKey && !config.localModel) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Не указан API Key (или включите «Локальная модель без API-ключа»)'; return; }
        if (!config.model) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Не указана модель'; return; }

        const start = Date.now();
        try {
            const { url, options } = llmRequestOptions([{ role: 'user', content: 'ping' }], 0, false);
            const payload = JSON.parse(options.body);
            payload.max_tokens = 5;
            const resp = await fetchWithRetry(url, { ...options, body: JSON.stringify(payload) }, false);
            const elapsed = Date.now() - start;
            const data = await resp.json().catch(() => null);

            if (data && data.error) {
                statusEl.className = 'nm-server-status show err';
                statusEl.textContent = `❌ Ошибка API: ${data.error.message || JSON.stringify(data.error)} (${elapsed}мс)`;
                return;
            }

            if (!data || !data.choices || !data.choices[0]) {
                let preview = '';
                try {
                    preview = await resp.text();
                } catch {}
                statusEl.className = 'nm-server-status show err';
                statusEl.textContent = `❌ Некорректный ответ API: ${(String(preview).trim() || 'Пустой ответ').slice(0, 150)} (${elapsed}мс)`;
                return;
            }

            statusEl.className = 'nm-server-status show ok';
            statusEl.textContent = `✅ Сервер доступен • модель ${config.model} • ${elapsed}мс`;
        } catch (e) {
            const elapsed = Date.now() - start;
            statusEl.className = 'nm-server-status show err';
            statusEl.textContent = `❌ ${e.message} (${elapsed}мс)`;
        }
    }

    // ===== ИЗВЛЕЧЕНИЕ ТЕРМИНОВ (со стримингом и прогресс-баром) =====
    // Эвристика прогресса: ожидаемый размер ответа с терминами ≈ 2× размера
    // исходного чанка (размер чанка — примерно половина ожидаемого размера
    // ответа). Проценты считаются по символам, полученным от LLM стримингом.
    const NER_RESPONSE_RATIO = 2;

    async function extractTermsFromText(text, targetKey, onProgress) {
        const chunks = splitByNewlines(text, config.chunkSize);
        const glossary = targetKey === 'global' ? { ...globalGlossary }
                       : (books[targetKey] ? { ...(books[targetKey].glossary || {}) } : {});
        const expectedTotal = Math.max(1, Math.round(text.length * NER_RESPONSE_RATIO));
        let streamed = 0;
        let added = 0, incremented = 0;
        const emitProgress = (i, retry) => {
            if (onProgress) {
                onProgress({
                    chunk: i + 1,
                    total: chunks.length,
                    pct: Math.min(99, Math.round((streamed / expectedTotal) * 100)),
                    retry: retry || null
                });
            }
        };

        for (let i = 0; i < chunks.length; i++) {
            if (cancelRequested) break;
            const charsBefore = streamed;
            const userPrompt = config.extractionPrompt
                .replace('{targetLang}', config.targetLang)
                .replace('{text}', chunks[i]);

            let result;
            try {
                const res = await callLLM([{ role: 'user', content: userPrompt }], 0.3, true, {
                    onDelta: (piece) => { streamed += piece.length; emitProgress(i); },
                    onRetry: (info) => { streamed = charsBefore; emitProgress(i, info); }
                });
                result = res.text;
            } catch (e) {
                if (cancelRequested) { emitProgress(i); break; }
                throw e;
            }

            result = result.replace(/```json\s*/g, '').replace(/```\s*/g, '').trim();
            const m = result.match(/\[[\s\S]*\]/);
            if (!m) continue;
            let extracted;
            try { extracted = JSON.parse(m[0]); } catch { continue; }

            for (const item of extracted) {
                if (!item || !item.term || !item.translation) continue;

                let existingId = null;
                for (const [id, ex] of Object.entries(glossary)) {
                    if (normalize(ex.term) === normalize(item.term)) { existingId = id; break; }
                }
                if (!existingId) {
                    for (const [id, ex] of Object.entries(glossary)) {
                        if (fuzzyMatchWord(ex.term, item.term, config.fuzzySearchThreshold)) { existingId = id; break; }
                    }
                }

                if (existingId) {
                    glossary[existingId].count = (glossary[existingId].count || 0) + 1;
                    incremented++;
                } else {
                    glossary[`${normalize(item.term)}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`] = migrateEntry({
                        term: item.term,
                        translation: item.translation,
                        type: String(item.type || '').trim() || 'Term',
                        count: 1
                    });
                    added++;
                }
            }
        }

        if (targetKey === 'global') { globalGlossary = glossary; GM_setValue('globalGlossary', globalGlossary); }
        else if (books[targetKey]) { books[targetKey].glossary = glossary; GM_setValue('books', books); }
        return { added, incremented };
    }

    // ===== ПЕРЕВОД =====
    function renderTranslationInto(element, text) {
        const paras = paragraphsOf(text);
        element.innerHTML = '';
        for (const para of paras) {
            const p = document.createElement('p');
            p.textContent = para;
            p.style.margin = '0 0 1em 0';
            element.appendChild(p);
        }
    }

    async function translateWithStreaming(element, originalText) {
        const totalParas = paragraphsOf(originalText).length;
        if (totalParas === 0) { $('#stream-status').textContent = '❌ Текст не найден'; return; }

        const chunks = splitByNewlines(originalText, config.chunkSize);
        const fill = $('#progress-fill');
        fill.classList.remove('retry');

        let fullTranslation = '';
        const setProgress = (all) => {
            const done = paragraphsOf(all).length;
            const pct = totalParas > 0 ? Math.min(99, Math.round((done / totalParas) * 100)) : 0;
            fill.style.width = pct + '%';
            return pct;
        };

        try {
            element.innerHTML = '';
            for (let i = 0; i < chunks.length; i++) {
                if (cancelRequested) throw new Error('Отменено пользователем');
                fill.classList.remove('retry');
                $('#stream-status').textContent = `Чанк ${i + 1}/${chunks.length} • абзацев в источнике: ${totalParas}`;

                const glossaryText = formatGlossaryForPrompt(findRelevantTerms(chunks[i]));
                const userPrompt = config.translationPrompt
                    .replace('{sourceLang}', config.sourceLang)
                    .replace('{targetLang}', config.targetLang)
                    .replace('{glossary}', glossaryText)
                    .replace('{text}', chunks[i]);

                let chunkTranslation = '';
                const res = await callLLM([{ role: 'user', content: userPrompt }], 0.7, true, {
                    onDelta: (content) => {
                        chunkTranslation += content;
                        const all = fullTranslation + (fullTranslation ? '\n\n' : '') + chunkTranslation;
                        renderTranslationInto(element, all);
                        const pct = setProgress(all);
                        $('#stream-status').textContent = `Чанк ${i + 1}/${chunks.length} • ~${pct}%`;
                    },
                    onRetry: (info) => {
                        // при ретрае чанк переводится целиком заново — незакрытый кусок сбрасываем
                        chunkTranslation = '';
                        renderTranslationInto(element, fullTranslation);
                        setProgress(fullTranslation);
                        fill.classList.add('retry');
                        $('#stream-status').textContent = `⏱ ${info.message} — повторная попытка ${info.nextAttempt}/${info.attemptsTotal}`;
                    }
                });
                fullTranslation += (fullTranslation ? '\n\n' : '') + res.text;
                fill.classList.remove('retry');
                renderTranslationInto(element, fullTranslation);
                setProgress(fullTranslation);
            }
            $('#stream-status').textContent = '✅ Перевод завершён!';
            fill.style.width = '100%';
        } catch (error) {
            $('#stream-status').textContent = '❌ ' + error.message;
            if (fullTranslation) renderTranslationInto(element, fullTranslation + '\n\n[ПЕРЕВОД ПРЕРВАН: ' + error.message + ']');
        } finally {
            fill.classList.remove('retry');
        }
    }

    function updateExtractionProgress(st) {
        const fill = $('#progress-fill');
        fill.classList.toggle('retry', !!st.retry);
        fill.style.width = st.pct + '%';
        $('#stream-status').textContent = st.retry
            ? `⏱ ${st.retry.message} — повторная попытка ${st.retry.nextAttempt}/${st.retry.attemptsTotal}`
            : `🔍 Термины: чанк ${st.chunk}/${st.total} • ~${st.pct}%`;
    }

    async function handleTranslate() {
        if (isTranslating) return;
        const current = getCurrentBook();
        if (!current) { openBookModal(); return; }

        const element = findContentElement();
        const text = extractMainText(element);
        if (!text.trim()) { alert('Текст не найден на странице'); return; }
        if (!config.apiKey && !config.localModel) { alert('Укажите API Key в настройках (⚙️) или включите «Локальная модель без API-ключа»'); openModal(); return; }

        isTranslating = true;
        cancelRequested = false;
        streamingPanel.classList.add('active');
        $('#progress-fill').style.width = '0%';
        $('#btn-translate').disabled = true;

        try {
            if (config.autoNER) {
                $('#stream-title').textContent = '🔍 Извлечение терминов';
                if (isNerDoneForPage(currentBookKey)) {
                    $('#stream-status').textContent = '✨ Термины для этой страницы уже извлекались';
                    await new Promise(r => setTimeout(r, 800));
                } else {
                    try {
                        const { added, incremented } = await extractTermsFromText(text, currentBookKey, updateExtractionProgress);
                        markNerDone(currentBookKey);
                        $('#progress-fill').style.width = '100%';
                        $('#stream-status').textContent = `✨ +${added} новых, обновлено частот: ${incremented}`;
                        await new Promise(r => setTimeout(r, 800));
                    } catch (error) {
                        if (!cancelRequested) {
                            $('#stream-status').textContent = '⚠️ NER: ' + error.message;
                            await new Promise(r => setTimeout(r, 1500));
                        }
                    }
                }
            }
            if (cancelRequested) {
                $('#stream-title').textContent = '⏹ Отменено';
                $('#stream-status').textContent = 'Отменено пользователем';
                return;
            }
            $('#stream-title').textContent = '🔄 Перевод...';
            await translateWithStreaming(element, text);
        } finally {
            isTranslating = false;
            $('#btn-translate').disabled = false;
            setTimeout(() => {
                streamingPanel.classList.remove('active');
                $('#progress-fill').style.width = '0%';
                $('#progress-fill').classList.remove('retry');
            }, 2500);
        }
    }

    async function handleExtractTerms() {
        if (isTranslating) return;
        const current = getCurrentBook();
        const targetKey = currentGlossaryMode === 'global' ? 'global'
                        : (selectedBookKey || (current ? current.key : null));
        if (!targetKey) { showStatus('Сначала определите книгу', 'error', 'status-glossary'); return; }
        if (!config.apiKey && !config.localModel) { showStatus('Укажите API Key в настройках или включите «Локальная модель без API-ключа»', 'error', 'status-glossary'); return; }

        const element = findContentElement();
        const text = extractMainText(element);
        if (!text.trim()) { showStatus('Текст не найден', 'error', 'status-glossary'); return; }

        isTranslating = true;
        cancelRequested = false;
        $('#stream-title').textContent = '🔍 Извлечение терминов';
        streamingPanel.classList.add('active');
        $('#progress-fill').style.width = '0%';
        $('#progress-fill').classList.remove('retry');
        showStatus('Извлечение терминов...', 'info', 'status-glossary');
        try {
            const { added, incremented } = await extractTermsFromText(text, targetKey, updateExtractionProgress);
            if (targetKey !== 'global') markNerDone(targetKey);
            $('#progress-fill').style.width = '100%';
            showStatus(cancelRequested
                ? `⏹ Извлечение остановлено: +${added} новых, обновлено частот: ${incremented}`
                : `✨ +${added} новых, обновлено частот: ${incremented}`, 'success', 'status-glossary');
            updateGlossaryUI();
            refreshBookTab();
        } catch (error) {
            showStatus('Ошибка: ' + error.message, 'error', 'status-glossary');
        } finally {
            isTranslating = false;
            setTimeout(() => {
                streamingPanel.classList.remove('active');
                $('#progress-fill').style.width = '0%';
                $('#progress-fill').classList.remove('retry');
            }, 1200);
        }
    }

    // ===== ГЛОССАРИЙ: CRUD =====
    function addTerm() {
        const term = $('#new-term').value.trim();
        const translation = $('#new-translation').value.trim();
        if (!term || !translation) { showStatus('Заполните термин и перевод', 'error', 'status-glossary'); return; }
        const glossary = getGlossaryForView();
        for (const ex of Object.values(glossary)) {
            if (normalize(ex.term) === normalize(term) || fuzzyMatchWord(ex.term, term, config.fuzzySearchThreshold)) {
                showStatus(`Похожий термин уже есть: "${ex.term}"`, 'error', 'status-glossary');
                return;
            }
        }
        glossary[`${normalize(term)}_${Date.now()}`] = {
            term, translation, type: $('#new-type').value.trim() || 'Term', count: 1
        };
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
                            if (normalize(ex.term) === normalize(t.term) || fuzzyMatchWord(ex.term, t.term, config.fuzzySearchThreshold)) {
                                existingId = exId; break;
                            }
                        }
                        if (existingId) {
                            const importedCount = parseInt(t.count, 10);
                            glossary[existingId].count = (glossary[existingId].count || 0) + (Number.isFinite(importedCount) && importedCount > 0 ? importedCount : 1);
                            incremented++;
                        } else {
                            const nid = `${normalize(t.term)}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
                            const importedCount = parseInt(t.count, 10);
                            glossary[nid] = { ...t, count: Number.isFinite(importedCount) && importedCount > 0 ? importedCount : 1 };
                            added++;
                        }
                    }
                    saveGlossary(glossary);
                    updateGlossaryUI();
                    showStatus(`Импортировано ${added} новых, обновлено частот: ${incremented}`, 'success', 'status-glossary');
                } catch (err) {
                    showStatus('Ошибка файла: ' + err.message, 'error', 'status-glossary');
                }
            };
            reader.readAsText(e.target.files[0]);
        };
        input.click();
    }

    function exportGlossary() {
        const glossary = getGlossaryForView();
        const name = currentGlossaryMode === 'global' ? 'global' : (selectedBookKey || 'book');
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
        const isGlobal = currentGlossaryMode === 'global';
        const bookKey = isGlobal ? null : (selectedBookKey || currentBookKey);
        const label = isGlobal ? 'глобальный глоссарий' : `глоссарий книги "${books[bookKey] ? books[bookKey].name : bookKey}"`;
        if (!confirm(`Очистить ${label}?` + (!isGlobal && bookKey ? '\nКэш страниц с извлечёнными терминами для этой книги тоже будет очищен.' : ''))) return;
        saveGlossary({});
        if (!isGlobal && bookKey) clearNerCache(bookKey);
        glossaryPage = 0;
        updateGlossaryUI();
        refreshBookTab();
        showStatus('Глоссарий очищен', 'success', 'status-glossary');
    }

    // ===== НАСТРОЙКИ (автосохранение) =====
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
        $('#local-model').checked = !!config.localModel;
        $('#api-key').disabled = !!config.localModel;
        $$('input[name="glossary-source"]').forEach(r => { r.checked = (r.value === config.glossarySource); });
        $('#translation-prompt').value = config.translationPrompt;
        $('#extraction-prompt').value = config.extractionPrompt;
    }

    // Настройки сохраняются сами: любое изменение поля с дебаунсом пишется в хранилище.
    const SETTING_FIELDS = [
        ['#api-host', 'apiHost', v => v.trim()],
        ['#api-key', 'apiKey', v => v.trim()],
        ['#model', 'model', v => v.trim()],
        ['#reasoning-effort', 'reasoningEffort', v => v],
        ['#request-timeout', 'requestTimeout', v => { const n = parseInt(v, 10); return Number.isFinite(n) && n >= 0 ? n : 0; }],
        ['#max-retries', 'maxRetries', v => { const n = parseInt(v, 10); return Number.isFinite(n) ? Math.min(10, Math.max(0, n)) : 3; }],
        ['#chunk-size', 'chunkSize', v => { const n = parseInt(v, 10); return Number.isFinite(n) && n > 0 ? n : DEFAULT_CONFIG.chunkSize; }],
        ['#source-lang', 'sourceLang', v => v],
        ['#target-lang', 'targetLang', v => v],
        ['#fuzzy-threshold', 'fuzzySearchThreshold', v => { const f = parseFloat(v); return Number.isFinite(f) ? f : DEFAULT_CONFIG.fuzzySearchThreshold; }],
        ['#translation-prompt', 'translationPrompt', v => v],
        ['#extraction-prompt', 'extractionPrompt', v => v]
    ];
    let settingsSaveTimer = null;
    function persistSettings() {
        GM_setValue('config', config);
        showStatus('✅ Настройки сохранены автоматически', 'success', 'status-settings');
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
        const autoNer = $('#auto-ner');
        autoNer.addEventListener('change', () => { config.autoNER = autoNer.checked; scheduleSettingsSave(); });
        const localModel = $('#local-model');
        localModel.addEventListener('change', () => {
            config.localModel = localModel.checked;
            $('#api-key').disabled = localModel.checked;
            scheduleSettingsSave();
        });
        $$('input[name="glossary-source"]').forEach(r => {
            r.addEventListener('change', () => {
                if (r.checked) { config.glossarySource = r.value; scheduleSettingsSave(); }
            });
        });
    }

    function resetSettings() {
        if (!confirm('Сбросить все настройки к значениям по умолчанию?')) return;
        config = { ...DEFAULT_CONFIG };
        GM_setValue('config', config);
        loadSettings();
        showStatus('Настройки сброшены!', 'success', 'status-settings');
    }

    function saveNewBook() {
        const url = $('#book-modal-url').value.trim();
        const name = $('#book-modal-name').value.trim();
        if (!url) { alert('Укажите URL книги'); return; }
        if (books[url] && !confirm('Книга с таким URL уже существует. Заменить название (глоссарий сохранится)?')) return;
        books[url] = {
            name: name || 'Без названия',
            glossary: books[url] ? books[url].glossary || {} : {},
            nerDone: books[url] ? books[url].nerDone || {} : {}
        };
        currentBookKey = url;
        managedBookKey = url;
        GM_setValue('books', books);
        bookModal.classList.remove('active');
        refreshBookTab();
        refreshGlossarySelector();
        updateGlossaryUI();
    }

    // ===== ПОЛНЫЙ БЭКАП =====
    function exportAllData() {
        const backup = {
            version: APP_VERSION,
            exportedAt: new Date().toISOString(),
            config: config,
            globalGlossary: globalGlossary,
            books: books
        };
        const blob = new Blob([JSON.stringify(backup, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `novelmaestro-backup-${new Date().toISOString().slice(0, 10)}.json`;
        a.click();
        URL.revokeObjectURL(url);
        showStatus(`Экспортировано: ${Object.keys(books).length} книг, ${Object.keys(globalGlossary).length} глобальных терминов`, 'success', 'status-backup');
    }

    function importAllData() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.json';
        input.onchange = e => {
            const reader = new FileReader();
            reader.onload = ev => {
                try {
                    const backup = JSON.parse(ev.target.result);
                    if (!backup.version || !backup.config) {
                        throw new Error('Неверный формат файла бэкапа');
                    }

                    const msg = `Импорт бэкапа (v${backup.version} от ${backup.exportedAt || '?'})\n\n`
                              + `Книг: ${Object.keys(backup.books || {}).length}\n`
                              + `Глобальных терминов: ${Object.keys(backup.globalGlossary || {}).length}\n\n`
                              + `ВНИМАНИЕ: текущие настройки и данные будут ЗАМЕНЕНЫ. Продолжить?`;
                    if (!confirm(msg)) return;

                    config = { ...DEFAULT_CONFIG, ...backup.config };
                    globalGlossary = backup.globalGlossary || {};
                    books = backup.books || {};

                    GM_setValue('config', config);
                    GM_setValue('globalGlossary', globalGlossary);
                    GM_setValue('books', books);

                    currentBookKey = findBookByUrl();
                    managedBookKey = null;
                    selectedBookKey = null;
                    glossaryPage = 0;

                    loadSettings();
                    refreshBookTab();
                    refreshGlossarySelector();
                    updateGlossaryUI();

                    showStatus('✅ Бэкап успешно импортирован!', 'success', 'status-backup');
                } catch (err) {
                    showStatus('Ошибка импорта: ' + err.message, 'error', 'status-backup');
                }
            };
            reader.readAsText(e.target.files[0]);
        };
        input.click();
    }

    // ===== СОБЫТИЯ =====
    $('#btn-translate').addEventListener('click', handleTranslate);
    $('#btn-settings').addEventListener('click', openModal);
    $('#nm-close').addEventListener('click', closeModal);
    modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
    bookModal.addEventListener('click', e => { if (e.target === bookModal) bookModal.classList.remove('active'); });

    $$('.nm-tab').forEach(tab => {
        tab.addEventListener('click', function() {
            $$('.nm-tab').forEach(t => t.classList.remove('active'));
            $$('.nm-tab-content').forEach(c => c.classList.remove('active'));
            this.classList.add('active');
            $('#tab-' + this.dataset.tab).classList.add('active');
        });
    });

    $('#book-select').addEventListener('change', function() {
        managedBookKey = this.value || null;
        renderBookManageArea();
    });

    $('#glossary-selector').addEventListener('change', function() {
        applyGlossarySelectorValue(this.value);
        glossaryPage = 0;
        updateGlossaryUI();
    });

    $('#glossary-filter').addEventListener('input', function() {
        glossaryFilter = this.value;
        glossaryPage = 0;
        updateGlossaryUI();
    });

    $('#btn-extract-terms').addEventListener('click', handleExtractTerms);
    $('#btn-add-term').addEventListener('click', addTerm);
    $('#btn-import').addEventListener('click', importGlossary);
    $('#btn-export').addEventListener('click', exportGlossary);
    $('#btn-clear-glossary').addEventListener('click', clearGlossary);
    $('#btn-reset-settings').addEventListener('click', resetSettings);
    $('#btn-check-server').addEventListener('click', checkServer);
    $('#btn-full-export').addEventListener('click', exportAllData);
    $('#btn-full-import').addEventListener('click', importAllData);
    $('#btn-cancel').addEventListener('click', () => {
        cancelRequested = true;
        // при зависании read() флаг сам не срабатывает: будим стрим отменой читателя
        if (activeReader) { try { activeReader.cancel(); } catch {} }
    });
    $('#btn-save-new-book').addEventListener('click', saveNewBook);
    $('#btn-cancel-new-book').addEventListener('click', () => bookModal.classList.remove('active'));
    $('#btn-autofill-url').addEventListener('click', () => { $('#book-modal-url').value = suggestBookKeyFromUrl(); });

    currentBookKey = findBookByUrl();
    bindSettingsAutoSave();

    // разовая миграция: старый дефолт requestTimeout=0 → 30-секундный таймаут зависания
    if (config.requestTimeout === 0 && !config.timeoutMigrated) {
        config.requestTimeout = 30000;
        config.timeoutMigrated = true;
        GM_setValue('config', config);
    }

    let migrated = false;
    const migrateCount = g => {
        for (const t of Object.values(g || {})) {
            const before = JSON.stringify(t);
            migrateEntry(t);
            if (JSON.stringify(t) !== before) migrated = true;
        }
    };
    migrateCount(globalGlossary);
    for (const b of Object.values(books)) migrateCount(b.glossary);
    if (migrated) { GM_setValue('globalGlossary', globalGlossary); GM_setValue('books', books); }

    console.log(`NovelMaestro Lite v${APP_VERSION} загружен. Книга:`, currentBookKey || 'не определена');
})();

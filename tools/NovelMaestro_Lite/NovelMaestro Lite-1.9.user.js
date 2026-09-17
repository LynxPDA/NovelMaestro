// ==UserScript==
// @name         NovelMaestro Lite
// @namespace    http://tampermonkey.net/
// @version      1.9
// @description  Универсальный переводчик новелл с глоссарием по книгам и стримингом
// @author       NovelMaestro
// @match        *://*/*
// @grant        GM_setValue
// @grant        GM_getValue
// @run-at       document-idle
// ==/UserScript==

(function() {
    'use strict';

    const APP_VERSION = '1.9';

    // ===== КОНФИГУРАЦИЯ =====
    const DEFAULT_CONFIG = {
        apiHost: 'https://routerai.ru/api/v1',
        apiKey: '',
        model: 'google/gemma-4-31b-it',
        sourceLang: 'Авто',
        targetLang: 'Русский',
        reasoningEffort: 'None',
        chunkSize: 30000,
        requestTimeout: 0,          // мс; 0 = без таймаута
        maxRetries: 3,
        glossarySource: 'book',
        translationPrompt: 'Переведи следующий текст с {sourceLang} на {targetLang}.\n\nГЛОССАРИЙ ТЕРМИНОВ (обязательно используй эти переводы, сохраняй пол персонажей):\n{glossary}\n\nВАЖНО:\n- Имена и термины переводи точно по глоссарию\n- Сохраняй пол персонажей (он/она) согласно глоссарию\n- Сохраняй стиль оригинала\n- Сохраняй разбивку на абзацы\n- Возвращай ТОЛЬКО перевод, без комментариев\n\nТекст:\n{text}',
        extractionPrompt: 'Извлеки из текста имена персонажей, места, артефакты, организации и важные термины.\n\nВерни JSON в формате:\n{\n  "term": "оригинальный термин",\n  "translation": "перевод на {targetLang} Только 1 вариант перевода!",\n  "type": "character|location|artifact|organization|term",\n  "gender": "male|female|neutral|null"\n}\n\ntype - тип:\n- character: персонаж (живое существо)\n- location: место, город, страна\n- artifact: предмет, артефакт, оружие\n- organization: организация, клан, гильдия\n- term: общий термин, понятие\n\ngender - пол (только для character):\n- male: мужской\n- female: женский\n- neutral: нейтральный/неизвестно\n- null: для не-персонажей\n\nВерни ТОЛЬКО валидный JSON массив объектов. Без дополнительного текста.\n\nТекст:\n{text}',
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

    // Состояние таблицы глоссария
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

    function paragraphsOf(text) {
        return text.split(/\n+/).map(s => s.trim()).filter(s => s.length > 0);
    }

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
        let key = location.pathname
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
        const typeLabels = { character: 'Персонаж', location: 'Место', artifact: 'Артефакт', organization: 'Организация', term: 'Термин' };
        const genderLabels = { male: 'муж.', female: 'жен.', neutral: 'нейтр.' };
        return terms.map(t => {
            let d = `- "${t.term}" → "${t.translation}" [${typeLabels[t.type] || t.type}]`;
            if (t.type === 'character' && t.gender && t.gender !== 'null') d += ` (${genderLabels[t.gender] || t.gender})`;
            return d;
        }).join('\n');
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
            .nm-badge { padding: 3px 7px; border-radius: 4px; font-size: 10px; font-weight: 600; text-transform: uppercase; }
            .nm-badge-character { background: #dbeafe; color: #1e40af; }
            .nm-badge-location { background: #dcfce7; color: #166534; }
            .nm-badge-artifact { background: #fef3c7; color: #92400e; }
            .nm-badge-organization { background: #e0e7ff; color: #3730a3; }
            .nm-badge-term { background: #f3f4f6; color: #374151; }
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
                display: grid; grid-template-columns: 1fr 1fr auto auto auto;
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
    shadow.innerHTML = `
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
                            <select id="new-type">
                                <option value="character">Персонаж</option>
                                <option value="location">Место</option>
                                <option value="artifact">Артефакт</option>
                                <option value="organization">Организация</option>
                                <option value="term" selected>Термин</option>
                            </select>
                            <select id="new-gender">
                                <option value="null">—</option>
                                <option value="male">Мужской</option>
                                <option value="female">Женский</option>
                                <option value="neutral">Нейтральный</option>
                            </select>
                            <button class="nm-btn nm-btn-primary" id="btn-add-term" style="margin:0;">+ Добавить</button>
                        </div>

                        <div class="nm-toolbar">
                            <button class="nm-btn nm-btn-success" id="btn-extract-terms">✨ Извлечь термины со страницы</button>
                            <button class="nm-btn nm-btn-secondary" id="btn-import">📥 Импорт</button>
                            <button class="nm-btn nm-btn-secondary" id="btn-export">📤 Экспорт</button>
                            <button class="nm-btn nm-btn-danger" id="btn-clear-glossary">🗑 Очистить</button>
                        </div>

                        <div class="nm-filter-row">
                            <input type="text" id="glossary-filter" placeholder="🔍 Фильтр по термину или переводу...">
                        </div>

                        <div id="glossary-list"></div>
                        <div class="nm-pagination" id="glossary-pagination"></div>

                        <div class="nm-status" id="status-glossary"></div>
                    </div>

                    <div class="nm-tab-content" id="tab-settings">
                        <div class="nm-section">
                            <h3>🤖 API</h3>
                            <div class="nm-input-group"><label>API Host:</label><input type="text" class="nm-input" id="api-host"></div>
                            <div class="nm-input-group"><label>API Key:</label><input type="password" class="nm-input" id="api-key"></div>
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
                                <small>Пусто — параметр не передаётся. None — выключает рассуждения. Любое другое значение передаётся как есть.</small>
                            </div>
                            <button class="nm-btn nm-btn-sm nm-btn-primary" id="btn-check-server">🔌 Проверить сервер</button>
                            <div class="nm-server-status" id="server-status"></div>
                        </div>
                        <div class="nm-section">
                            <h3>🌐 Сеть</h3>
                            <div class="nm-input-group"><label>Таймаут запроса (мс):</label>
                                <input type="number" class="nm-input" id="request-timeout" min="0" step="1000">
                                <small>0 = без таймаута. Рекомендовано для длинных переводов: 120000–300000.</small>
                            </div>
                            <div class="nm-input-group"><label>Количество ретраев при ошибке:</label>
                                <input type="number" class="nm-input" id="max-retries" min="0" max="10">
                                <small>Повторные попытки при сетевых ошибках (не при 4xx/5xx).</small>
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
                        <button class="nm-btn nm-btn-primary" id="btn-save-settings">💾 Сохранить настройки</button>
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
                <div style="font-weight:600;margin-bottom:8px;">🔄 Перевод...</div>
                <div id="stream-status" style="font-size:13px;color:#6b7280;">Подготовка...</div>
                <div class="nm-progress-bar"><div class="nm-progress-fill" id="progress-fill"></div></div>
                <button class="nm-btn nm-btn-danger" id="btn-cancel" style="margin-top:12px;width:100%;">Отменить</button>
            </div>
        </div>
    `;

    const $ = sel => shadow.querySelector(sel);
    const $$ = sel => shadow.querySelectorAll(sel);

    const modal = $('#nm-modal');
    const bookModal = $('#nm-book-modal');
    const streamingPanel = $('#nm-streaming-panel');
    let isTranslating = false;
    let cancelRequested = false;

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
            area.innerHTML = `
                <div class="nm-help">⚠️ Текущая страница не привязана к книге.</div>
                <button class="nm-btn nm-btn-primary" id="btn-define-book">📚 Привязать текущую страницу к книге</button>
            `;
            $('#btn-define-book').addEventListener('click', openBookModal);
            return;
        }

        const book = books[key];
        const terms = Object.keys(book.glossary || {}).length;
        const nerPages = Object.keys(book.nerDone || {}).length;

        area.innerHTML = `
            <div class="nm-book-info">
                <strong>📖 ${book.name || 'Без названия'}</strong><br>
                <small style="color:#6b7280;">URL: ${key}</small><br>
                <small style="color:#6b7280;">Терминов: ${terms} | Страниц с извлечёнными терминами: ${nerPages}</small>
            </div>
            <div class="nm-input-group"><label>Название книги:</label>
                <input type="text" class="nm-input" id="book-name-edit" value="${(book.name || '').replace(/"/g, '&quot;')}">
            </div>
            <div class="nm-input-group"><label>URL книги:</label>
                <input type="text" class="nm-input" id="book-key-edit" value="${key.replace(/"/g, '&quot;')}">
            </div>
            <button class="nm-btn nm-btn-primary" id="btn-save-book">💾 Сохранить</button>
            <button class="nm-btn nm-btn-danger" id="btn-delete-book">🗑 Удалить книгу</button>
        `;

        $('#btn-save-book').addEventListener('click', () => {
            const newName = $('#book-name-edit').value.trim();
            const newKey = $('#book-key-edit').value.trim();
            if (!newKey) { showStatus('URL не может быть пустым', 'error', 'status-book'); return; }
            if (newKey !== key) {
                books[newKey] = { ...book, name: newName };
                delete books[key];
                if (currentBookKey === key) currentBookKey = newKey;
                managedBookKey = newKey;
            } else {
                book.name = newName;
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
        return [...entries].sort((a, b) => {
            const va = a[1][field];
            const vb = b[1][field];
            if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * sign;
            const sa = String(va ?? '').toLowerCase();
            const sb = String(vb ?? '').toLowerCase();
            return sa.localeCompare(sb) * sign;
        });
    }

    function updateGlossaryUI() {
        const container = $('#glossary-list');
        const pagination = $('#glossary-pagination');
        const glossary = getGlossaryForView();

        // Фильтр
        let entries = Object.entries(glossary);
        if (glossaryFilter) {
            const f = normalize(glossaryFilter);
            entries = entries.filter(([, t]) =>
                normalize(t.term).includes(f) || normalize(t.translation).includes(f)
            );
        }

        // Сортировка
        entries = sortGlossaryEntries(entries);

        // Пагинация
        const totalPages = Math.max(1, Math.ceil(entries.length / PAGE_SIZE));
        if (glossaryPage >= totalPages) glossaryPage = totalPages - 1;
        if (glossaryPage < 0) glossaryPage = 0;
        const start = glossaryPage * PAGE_SIZE;
        const pageEntries = entries.slice(start, start + PAGE_SIZE);

        $('#glossary-count').textContent = Object.keys(glossary).length;

        if (Object.keys(glossary).length === 0) {
            container.innerHTML = '<p style="color:#6b7280;text-align:center;padding:20px;">Глоссарий пуст</p>';
            pagination.innerHTML = '';
            return;
        }

        if (entries.length === 0) {
            container.innerHTML = '<p style="color:#6b7280;text-align:center;padding:20px;">Ничего не найдено по фильтру</p>';
            pagination.innerHTML = '';
            return;
        }

        const sortIcon = (field) => {
            if (glossarySort.field !== field) return '<span class="nm-sort">↕</span>';
            return `<span class="nm-sort">${glossarySort.dir === 'asc' ? '↑' : '↓'}</span>`;
        };
        const activeClass = (field) => glossarySort.field === field ? 'active-sort' : '';

        const typeLabels = { character: 'Персонаж', location: 'Место', artifact: 'Артефакт', organization: 'Организация', term: 'Термин' };

        const rows = pageEntries.map(([id, t]) => {
            const count = t.count || 0;
            const countClass = count >= 5 ? 'high' : count >= 2 ? 'med' : '';
            return `
                <tr data-id="${id}">
                    <td><input type="text" value="${String(t.term).replace(/"/g, '&quot;')}" data-field="term"></td>
                    <td><input type="text" value="${String(t.translation).replace(/"/g, '&quot;')}" data-field="translation"></td>
                    <td>
                        <select data-field="type">
                            <option value="character" ${t.type === 'character' ? 'selected' : ''}>Персонаж</option>
                            <option value="location" ${t.type === 'location' ? 'selected' : ''}>Место</option>
                            <option value="artifact" ${t.type === 'artifact' ? 'selected' : ''}>Артефакт</option>
                            <option value="organization" ${t.type === 'organization' ? 'selected' : ''}>Организация</option>
                            <option value="term" ${t.type === 'term' ? 'selected' : ''}>Термин</option>
                        </select>
                    </td>
                    <td>
                        <select data-field="gender" ${t.type !== 'character' ? 'disabled' : ''}>
                            <option value="null" ${t.gender === 'null' ? 'selected' : ''}>—</option>
                            <option value="male" ${t.gender === 'male' ? 'selected' : ''}>♂</option>
                            <option value="female" ${t.gender === 'female' ? 'selected' : ''}>♀</option>
                            <option value="neutral" ${t.gender === 'neutral' ? 'selected' : ''}>⚥</option>
                        </select>
                    </td>
                    <td class="nm-count-cell ${countClass}">${count}</td>
                    <td><button class="nm-delete-cell" title="Удалить">✕</button></td>
                </tr>
            `;
        }).join('');

        container.innerHTML = `
            <table class="nm-glossary-table">
                <thead>
                    <tr>
                        <th class="${activeClass('term')}" data-sort="term">Термин ${sortIcon('term')}</th>
                        <th class="${activeClass('translation')}" data-sort="translation">Перевод ${sortIcon('translation')}</th>
                        <th class="${activeClass('type')}" data-sort="type">Тип ${sortIcon('type')}</th>
                        <th class="${activeClass('gender')}" data-sort="gender">Пол ${sortIcon('gender')}</th>
                        <th class="${activeClass('count')}" data-sort="count" style="text-align:center;">Частота ${sortIcon('count')}</th>
                        <th style="width:60px;">Действия</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        `;

        // Обработчики сортировки
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

        // Обработчики ячеек
        container.querySelectorAll('tr[data-id]').forEach(row => {
            const id = row.dataset.id;
            row.querySelectorAll('input, select').forEach(inp => {
                inp.addEventListener('change', function() {
                    const field = this.dataset.field;
                    const g = getGlossaryForView();
                    if (!g[id]) return;
                    g[id][field] = this.value;
                    if (field === 'type') {
                        const gSel = row.querySelector('[data-field="gender"]');
                        if (this.value !== 'character') { g[id].gender = 'null'; gSel.disabled = true; gSel.value = 'null'; }
                        else gSel.disabled = false;
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

        // Пагинация
        renderPagination(pagination, totalPages, entries.length);
    }

    function renderPagination(container, totalPages, totalItems) {
        if (totalPages <= 1) {
            container.innerHTML = `<span class="nm-page-info">Всего: ${totalItems}</span>`;
            return;
        }

        const maxVisiblePages = 7;
        let pages = [];
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

        let html = `<button class="nm-prev-btn" ${glossaryPage === 0 ? 'disabled' : ''}>‹</button>`;
        for (const p of pages) {
            if (p === -1) html += `<span style="padding:0 4px;color:#9ca3af;">…</span>`;
            else html += `<button class="nm-page-btn ${p === glossaryPage ? 'active' : ''}" data-page="${p}">${p + 1}</button>`;
        }
        html += `<button class="nm-next-btn" ${glossaryPage === totalPages - 1 ? 'disabled' : ''}>›</button>`;
        html += `<span class="nm-page-info">Стр. ${glossaryPage + 1} из ${totalPages} • Всего: ${totalItems}</span>`;
        container.innerHTML = html;

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

    // ===== LLM / HTTP с ретраями и таймаутом =====

    async function fetchWithRetry(url, options, isStream = false) {
        const timeout = config.requestTimeout > 0 ? config.requestTimeout : 0;
        const maxRetries = isStream ? 0 : (config.maxRetries || 0);
        let lastErr;

        for (let attempt = 0; attempt <= maxRetries; attempt++) {
            let controller = null;
            let timer = null;

            if (timeout > 0) {
                controller = new AbortController();
                timer = setTimeout(() => controller.abort(), timeout);
                options = { ...options, signal: controller.signal };
            }

            try {
                const resp = await fetch(url, options);
                if (timer) clearTimeout(timer);

                // Не ретраим на 4xx/5xx
                if (!resp.ok && !isStream) {
                    const errText = await resp.text().catch(() => '');
                    const err = new Error(`HTTP ${resp.status}: ${errText.slice(0, 200)}`);
                    err.status = resp.status;
                    throw err;
                }
                return resp;
            } catch (e) {
                if (timer) clearTimeout(timer);
                lastErr = e;
                const isAbort = e.name === 'AbortError';
                const isHttpErr = e.status && e.status >= 400;

                if (isHttpErr || attempt >= maxRetries) throw e;
                if (isAbort && timeout > 0 && attempt >= maxRetries) {
                    throw new Error(`Таймаут ${timeout}мс истёк после ${maxRetries + 1} попыток`);
                }
                const delay = Math.min(5000, 500 * Math.pow(2, attempt));
                await new Promise(r => setTimeout(r, delay));
            }
        }
        throw lastErr;
    }

    async function callLLM(messages, temperature, stream) {
        const body = { model: config.model, messages, temperature, stream: !!stream };
        const re = String(config.reasoningEffort ?? '').trim();
        if (re !== '') body.reasoning_effort = re;
        return await fetchWithRetry(config.apiHost.replace(/\/$/, '') + '/chat/completions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${config.apiKey}` },
            body: JSON.stringify(body)
        }, stream);
    }

    // ===== ПРОВЕРКА СЕРВЕРА =====

    async function checkServer() {
        const statusEl = $('#server-status');
        statusEl.className = 'nm-server-status show loading';
        statusEl.textContent = '🔌 Проверяю сервер...';

        if (!config.apiHost) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Не указан API Host'; return; }
        if (!config.apiKey) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Не указан API Key'; return; }
        if (!config.model) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Не указана модель'; return; }

        const start = Date.now();
        try {
            const body = {
                model: config.model,
                messages: [{ role: 'user', content: 'ping' }],
                max_tokens: 5,
                temperature: 0
            };
            const re = String(config.reasoningEffort ?? '').trim();
            if (re !== '') body.reasoning_effort = re;

            const resp = await fetchWithRetry(config.apiHost.replace(/\/$/, '') + '/chat/completions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${config.apiKey}` },
                body: JSON.stringify(body)
            }, false);

            const elapsed = Date.now() - start;
            const data = await resp.json().catch(() => null);

            if (!resp.ok) {
                const msg = data?.error?.message || `HTTP ${resp.status}`;
                statusEl.className = 'nm-server-status show err';
                statusEl.textContent = `❌ Ошибка: ${msg} (${elapsed}мс)`;
                return;
            }

            if (!data || !data.choices || !data.choices[0]) {
                statusEl.className = 'nm-server-status show err';
                statusEl.textContent = `❌ Некорректный ответ API (${elapsed}мс)`;
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

    // ===== ИЗВЛЕЧЕНИЕ ТЕРМИНОВ (со счётчиком) =====

    async function extractTermsFromText(text, targetKey) {
        const chunks = splitByNewlines(text, config.chunkSize);
        const glossary = targetKey === 'global' ? { ...globalGlossary }
                       : (books[targetKey] ? { ...(books[targetKey].glossary || {}) } : {});
        let added = 0, incremented = 0;

        for (let i = 0; i < chunks.length; i++) {
            if (cancelRequested) break;
            const userPrompt = config.extractionPrompt
                .replace('{targetLang}', config.targetLang)
                .replace('{text}', chunks[i]);

            const response = await callLLM([{ role: 'user', content: userPrompt }], 0.3, false);
            const data = await response.json();
            if (!data.choices || !data.choices[0]) {
                if (data.error) throw new Error(data.error.message || 'API error');
                continue;
            }
            let result = data.choices[0].message.content.replace(/```json\s*/g, '').replace(/```\s*/g, '').trim();
            const m = result.match(/\[[\s\S]*\]/);
            if (!m) continue;
            let extracted;
            try { extracted = JSON.parse(m[0]); } catch (e) { continue; }

            for (const item of extracted) {
                if (!item || !item.term || !item.translation) continue;

                // Ищем существующий: точное совпадение -> fuzzy
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
                    glossary[`${normalize(item.term)}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`] = {
                        term: item.term,
                        translation: item.translation,
                        type: item.type || 'term',
                        gender: item.gender || 'null',
                        count: 1
                    };
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

    function setProgressByParagraphs(translatedText, totalParas) {
        const done = paragraphsOf(translatedText).length;
        const pct = totalParas > 0 ? Math.min(99, Math.round((done / totalParas) * 100)) : 0;
        $('#progress-fill').style.width = pct + '%';
        return pct;
    }

    async function translateWithStreaming(element) {
        const originalText = extractMainText(element);
        const totalParas = paragraphsOf(originalText).length;
        if (totalParas === 0) { $('#stream-status').textContent = '❌ Текст не найден'; return; }

        const chunks = splitByNewlines(originalText, config.chunkSize);
        isTranslating = true;
        cancelRequested = false;
        streamingPanel.classList.add('active');
        $('#btn-translate').disabled = true;

        let fullTranslation = '';
        try {
            element.innerHTML = '';
            for (let i = 0; i < chunks.length; i++) {
                if (cancelRequested) throw new Error('Отменено пользователем');
                $('#stream-status').textContent = `Чанк ${i + 1}/${chunks.length} • абзацев в источнике: ${totalParas}`;

                const glossaryText = formatGlossaryForPrompt(findRelevantTerms(chunks[i]));
                const userPrompt = config.translationPrompt
                    .replace('{sourceLang}', config.sourceLang)
                    .replace('{targetLang}', config.targetLang)
                    .replace('{glossary}', glossaryText)
                    .replace('{text}', chunks[i]);

                const response = await callLLM([{ role: 'user', content: userPrompt }], 0.7, true);
                if (!response.body) throw new Error('Сервер не поддерживает стриминг');

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let chunkTranslation = '';
                let buffer = '';

                while (true) {
                    if (cancelRequested) { reader.cancel(); throw new Error('Отменено пользователем'); }
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop();
                    for (const line of lines) {
                        const trimmed = line.trim();
                        if (!trimmed.startsWith('data:')) continue;
                        const payload = trimmed.slice(5).trim();
                        if (payload === '[DONE]') continue;
                        try {
                            const parsed = JSON.parse(payload);
                            const content = parsed.choices?.[0]?.delta?.content || '';
                            if (content) {
                                chunkTranslation += content;
                                const all = fullTranslation + (fullTranslation ? '\n\n' : '') + chunkTranslation;
                                renderTranslationInto(element, all);
                                const pct = setProgressByParagraphs(all, totalParas);
                                $('#stream-status').textContent = `Чанк ${i + 1}/${chunks.length} • ~${pct}%`;
                            }
                        } catch (e) {}
                    }
                }
                fullTranslation += (fullTranslation ? '\n\n' : '') + chunkTranslation;
            }
            $('#stream-status').textContent = '✅ Перевод завершён!';
            $('#progress-fill').style.width = '100%';
        } catch (error) {
            $('#stream-status').textContent = '❌ ' + error.message;
            if (fullTranslation) renderTranslationInto(element, fullTranslation + '\n\n[ПЕРЕВОД ПРЕРВАН: ' + error.message + ']');
        } finally {
            isTranslating = false;
            $('#btn-translate').disabled = false;
            setTimeout(() => {
                streamingPanel.classList.remove('active');
                $('#progress-fill').style.width = '0%';
            }, 2500);
        }
    }

    async function handleTranslate() {
        if (isTranslating) return;
        const current = getCurrentBook();
        if (!current) { openBookModal(); return; }

        const element = findContentElement();
        const text = extractMainText(element);
        if (!text.trim()) { alert('Текст не найден на странице'); return; }
        if (!config.apiKey) { alert('Укажите API Key в настройках (⚙️)'); openModal(); return; }

        streamingPanel.classList.add('active');
        $('#progress-fill').style.width = '0%';

        if (config.autoNER) {
            if (isNerDoneForPage(currentBookKey)) {
                $('#stream-status').textContent = '✨ Термины для этой страницы уже извлекались';
                await new Promise(r => setTimeout(r, 800));
            } else {
                $('#stream-status').textContent = '🔍 Извлечение терминов...';
                try {
                    const { added, incremented } = await extractTermsFromText(text, currentBookKey);
                    markNerDone(currentBookKey);
                    $('#stream-status').textContent = `✨ +${added} новых, обновлено частот: ${incremented}`;
                    await new Promise(r => setTimeout(r, 800));
                } catch (error) {
                    $('#stream-status').textContent = '⚠️ NER: ' + error.message;
                    await new Promise(r => setTimeout(r, 1500));
                }
            }
        }

        await translateWithStreaming(element);
    }

    async function handleExtractTerms() {
        const current = getCurrentBook();
        const targetKey = currentGlossaryMode === 'global' ? 'global'
                        : (selectedBookKey || (current ? current.key : null));
        if (!targetKey) { showStatus('Сначала определите книгу', 'error', 'status-glossary'); return; }
        if (!config.apiKey) { showStatus('Укажите API Key в настройках', 'error', 'status-glossary'); return; }

        const element = findContentElement();
        const text = extractMainText(element);
        if (!text.trim()) { showStatus('Текст не найден', 'error', 'status-glossary'); return; }

        showStatus('Извлечение терминов (игнорирует кэш страниц)...', 'info', 'status-glossary');
        try {
            const { added, incremented } = await extractTermsFromText(text, targetKey);
            if (targetKey !== 'global') markNerDone(targetKey);
            showStatus(`✨ +${added} новых, обновлено частот: ${incremented}`, 'success', 'status-glossary');
            updateGlossaryUI();
            refreshBookTab();
        } catch (error) {
            showStatus('Ошибка: ' + error.message, 'error', 'status-glossary');
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
            term, translation, type: $('#new-type').value, gender: $('#new-gender').value, count: 1
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
                    const glossary = getGlossaryForView();
                    let added = 0, incremented = 0;
                    for (const [id, t] of Object.entries(imported)) {
                        if (!t || !t.term || !t.translation) continue;
                        let existingId = null;
                        for (const [exId, ex] of Object.entries(glossary)) {
                            if (normalize(ex.term) === normalize(t.term) || fuzzyMatchWord(ex.term, t.term, config.fuzzySearchThreshold)) {
                                existingId = exId; break;
                            }
                        }
                        if (existingId) {
                            const importedCount = typeof t.count === 'number' ? t.count : 1;
                            glossary[existingId].count = (glossary[existingId].count || 0) + importedCount;
                            incremented++;
                        } else {
                            glossary[id] = { ...t, count: typeof t.count === 'number' ? t.count : 1 };
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
        $('#auto-ner').checked = config.autoNER;
        $$('input[name="glossary-source"]').forEach(r => { r.checked = (r.value === config.glossarySource); });
        $('#translation-prompt').value = config.translationPrompt;
        $('#extraction-prompt').value = config.extractionPrompt;
    }

    function saveSettings() {
        config.apiHost = $('#api-host').value.trim();
        config.apiKey = $('#api-key').value.trim();
        config.model = $('#model').value.trim();
        config.reasoningEffort = $('#reasoning-effort').value;
        config.requestTimeout = parseInt($('#request-timeout').value) || 0;
        config.maxRetries = Math.max(0, parseInt($('#max-retries').value) || 0);
        config.chunkSize = parseInt($('#chunk-size').value) || 30000;
        config.sourceLang = $('#source-lang').value;
        config.targetLang = $('#target-lang').value;
        config.fuzzySearchThreshold = parseFloat($('#fuzzy-threshold').value);
        config.autoNER = $('#auto-ner').checked;
        const radio = shadow.querySelector('input[name="glossary-source"]:checked');
        config.glossarySource = radio ? radio.value : 'book';
        config.translationPrompt = $('#translation-prompt').value;
        config.extractionPrompt = $('#extraction-prompt').value;
        GM_setValue('config', config);
        showStatus('Настройки сохранены!', 'success', 'status-settings');
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

                    // Сбросить состояние
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
    $('#btn-save-settings').addEventListener('click', saveSettings);
    $('#btn-reset-settings').addEventListener('click', resetSettings);
    $('#btn-check-server').addEventListener('click', checkServer);
    $('#btn-full-export').addEventListener('click', exportAllData);
    $('#btn-full-import').addEventListener('click', importAllData);
    $('#btn-cancel').addEventListener('click', () => { cancelRequested = true; });
    $('#btn-save-new-book').addEventListener('click', saveNewBook);
    $('#btn-cancel-new-book').addEventListener('click', () => bookModal.classList.remove('active'));
    $('#btn-autofill-url').addEventListener('click', () => { $('#book-modal-url').value = suggestBookKeyFromUrl(); });

    currentBookKey = findBookByUrl();
    console.log(`NovelMaestro Lite v${APP_VERSION} загружен. Книга:`, currentBookKey || 'не определена');
})();
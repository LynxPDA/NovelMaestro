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
    }


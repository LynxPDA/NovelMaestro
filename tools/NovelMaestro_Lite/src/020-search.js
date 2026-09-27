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
            if (cur.length + p.length + 2 > chunkSize && cur) { chunks.push(cur); cur = p; }
            else cur += (cur ? '\n\n' : '') + p;
        }
        if (cur) chunks.push(cur);
        return chunks.length > 0 ? chunks : [text];
    }


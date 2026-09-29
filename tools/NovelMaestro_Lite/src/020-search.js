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
    // Язык-осведомлённая ОЦЕНКА числа токенов — зеркало core.common.estimate_tokens:
    // веса «токенов на символ» по скриптам (кириллица/латиница ~0.30, CJK-идеографы
    // и каны ~1.0, хангыль ~0.8, тай/лаос/кхмер/бирма ~0.6, индийские ~0.55,
    // арабский ~0.5, иврит ~0.4, греческий ~0.35); неучтённые буквы и цифры — 0.5,
    // знаки/символы/эмодзи — 0.35; подряд идущие пробелы — 1 токен; итог — вверх +10%.
    const TOKEN_RANGES = [
        [0x0041, 0x005A, 0.30], [0x0061, 0x007A, 0.30], [0x00C0, 0x024F, 0.30],
        [0x1E00, 0x1EFF, 0.30], [0x0400, 0x052F, 0.30],
        [0x0370, 0x03FF, 0.35], [0x1F00, 0x1FFF, 0.35],
        [0x0590, 0x05FF, 0.40], [0xFB1D, 0xFB4F, 0.40],
        [0x0600, 0x06FF, 0.50], [0x0750, 0x077F, 0.50], [0x0870, 0x08FF, 0.50],
        [0xFB50, 0xFDFF, 0.50], [0xFE70, 0xFEFF, 0.50], [0x0900, 0x0DFF, 0.55],
        [0x0E00, 0x0EFF, 0.60], [0x1000, 0x109F, 0.60], [0x1780, 0x17FF, 0.60],
        [0x19E0, 0x19FF, 0.60], [0x1100, 0x11FF, 0.80], [0x3130, 0x318F, 0.80],
        [0xA960, 0xA97F, 0.80], [0xAC00, 0xD7AF, 0.80], [0xD7B0, 0xD7FF, 0.80],
        [0x3040, 0x30FF, 1.00], [0x31F0, 0x31FF, 1.00], [0x3400, 0x4DBF, 1.00],
        [0x4E00, 0x9FFF, 1.00], [0xF900, 0xFAFF, 1.00], [0x20000, 0x2FA1F, 1.00],
    ];
    const TOKEN_RE = /^\s*$/;
    const LETTER_NUM = /\p{L}|\p{N}/u;
    function estimateTokens(text) {
        const s = String(text ?? '');
        if (!s) return 0;
        let total = 0;
        let inWs = false;
        for (const ch of s) {
            if (TOKEN_RE.test(ch)) { if (!inWs) { total += 1; inWs = true; } continue; }
            inWs = false;
            const cp = ch.codePointAt(0);
            let w = null;
            for (const [lo, hi, wgt] of TOKEN_RANGES) {
                if (cp >= lo && cp <= hi) { w = wgt; break; }
            }
            if (w === null) w = LETTER_NUM.test(ch) ? 0.5 : 0.35;
            total += w;
        }
        return Math.ceil(total * 1.1);
    }
    // chunkSize — ТОКЕНЫ (оценка estimateTokens); имена флагов сохранены, единица — токены
    function splitByNewlines(text, chunkSize) {
        const paras = paragraphsOf(text);
        const chunks = [];
        let cur = '';
        let curTokens = 0;
        for (const p of paras) {
            const pt = estimateTokens(p);
            if (cur && curTokens + pt + 1 > chunkSize) { chunks.push(cur); cur = p; curTokens = pt; }
            else { cur += (cur ? '\n\n' : '') + p; curTokens = estimateTokens(cur); }
        }
        if (cur) chunks.push(cur);
        return chunks.length > 0 ? chunks : [text];
    }

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
    // Нормализация типов: карта ловит синонимы и регистровые варианты, которые
    // приходят от LLM (промпт просит «Person (male)», попадаются и другие); пол
    // персонажа хранится в самом type в скобках.
    const TYPE_MAP = { character: 'Person', creature: 'Creature', location: 'Location', artifact: 'Artifact', organization: 'Organisation', organisation: 'Organisation', term: 'Term', other: 'Other' };
    function normalizeTerm(t) {
        if (!t || typeof t !== 'object') return t;
        const rawType = String(t.type || '').trim();
        const base = (TYPE_MAP[rawType.toLowerCase()] || rawType).replace(/\s*\((?:male|female|unknown)\)\s*$/i, '').trim();
        const gender = genderOf(t.type);
        t.type = (gender === 'male' || gender === 'female' || gender === 'unknown') ? `${base} (${gender})` : base;
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


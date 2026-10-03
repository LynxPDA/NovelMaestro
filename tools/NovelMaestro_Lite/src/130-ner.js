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
    // сохраняются, если пришли (совместимость с ner.json конвейера); count = 1:
    // новый термин встретился в этом чанке один раз, частоту дальше считает код
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
    // повтор для случая «ответ пришёл, но не годится»: не JSON и пустой completion.
    // relevantTerms — записи глоссария, которые нашлись в этом чанке: их же считает
    // частотами extractTermsFromText, поэтому проход по чанку один. Свой промпт без
    // {existingGlossary} — штатный случай: подстановки не происходит, список просто
    // не уходит в модель
    async function requestNerChunk(chunkText, onChunk, relevantTerms = findRelevantTerms(chunkText)) {
        const userPrompt = config.extractionPrompt
            .replaceAll('{targetLang}', config.targetLang)
            .replaceAll('{existingGlossary}', formatGlossaryForExtraction(relevantTerms))
            // текст чанка подставляется последним: своё «{existingGlossary}» внутри
            // главы не должно раскрыться второй раз
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
        if (!books[targetKey]) return { added: 0, incremented: 0, llmViolations: 0, canceled: false, skipped: 0, badItems: 0, resumed: 0, skippedReason: '' };
        const chunks = splitByNewlines(text, config.chunkSize);
        const glossary = { ...bookGlossary(targetKey) };
        const hash = textHash(text);
        const expectedTotal = Math.max(1, text.length * NER_RESPONSE_RATIO);
        let streamed = 0, added = 0, incremented = 0, llmViolations = 0, skipped = 0, badItems = 0, canceled = false, skippedReason = '';
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
                // какие термины глоссария реально есть в чанке; сам глоссарий до
                // этого места не менялся, поэтому список не зависит от ответа модели
                const chunkTerms = findRelevantTerms(chunks[i], glossary);
                const { items, bad } = await requestNerChunk(chunks[i], onChunk, chunkTerms);
                badItems += bad;
                // count считает код, а не модель: та могла термина в тексте не
                // заметить или прислать его несколько раз
                for (const t of chunkTerms) {
                    if (!glossary[t.id]) continue;
                    glossary[t.id].count = (glossary[t.id].count || 0) + 1;
                    incremented++;
                }
                for (const item of items) {
                    const existingId = findGlossaryEntry(glossary, item.term);
                    if (existingId) {
                        // нарушение контракта промпта: запись не трогается вообще —
                        // ни полей, ни count (частоты уже посчитаны выше)
                        llmViolations++;
                        console.warn(`[NovelMaestro] LLM violation: вернула существующий термин "${item.term}"`);
                        continue;
                    }
                    glossary[`${normalize(item.term)}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`] = normalizeTerm(item);
                    added++;
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
        return { added, incremented, llmViolations, canceled, skipped, badItems, resumed: startChunk, skippedReason };
    }
    // хвост статуса прогона: модель вернула то, что просила не возвращать; на
    // глоссарий это не повлияло (частоты считает код), но промпт стоит проверить
    const nerViolationNote = res => (res.llmViolations ? ` • нарушений контракта: ${res.llmViolations}` : '');

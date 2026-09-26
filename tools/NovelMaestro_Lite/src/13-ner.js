    // ===== NER =====
    const NER_RESPONSE_RATIO = 2;
    async function extractTermsFromText(text, targetKey, onProgress) {
        const chunks = splitByNewlines(text, config.chunkSize);
        const glossary = { ...bookGlossary(targetKey) };
        const expectedTotal = Math.max(1, Math.round(text.length * NER_RESPONSE_RATIO));
        let streamed = 0, added = 0, incremented = 0, canceled = false;
        const emitProgress = (i, retry) => {
            if (onProgress) onProgress({ chunk: i + 1, total: chunks.length, pct: Math.min(99, Math.round((streamed / expectedTotal) * 100)), retry: retry || null });
        };
        for (let i = 0; i < chunks.length; i++) {
            if (cancelRequested) { canceled = true; break; }
            const charsBefore = streamed;
            // replaceAll: плейсхолдеры в промптах могут встречаться несколько раз
            const userPrompt = config.extractionPrompt.replaceAll('{targetLang}', config.targetLang).replaceAll('{text}', chunks[i]);
            let result;
            try {
                const res = await callLLM([{ role: 'user', content: userPrompt }], 0.3, true, {
                    onDelta: (piece) => { streamed += piece.length; emitProgress(i); },
                    onRetry: (info) => { streamed = charsBefore; emitProgress(i, info); }
                });
                result = res.text;
            } catch (e) {
                if (cancelRequested) { canceled = true; emitProgress(i); break; }
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
                if (existingId) { glossary[existingId].count = (glossary[existingId].count || 0) + 1; incremented++; }
                else {
                    glossary[`${normalize(item.term)}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`] = migrateEntry({
                        term: item.term, translation: item.translation, type: String(item.type || '').trim() || 'Term', count: 1
                    });
                    added++;
                }
            }
        }
        if (books[targetKey]) { siteGlossaries[targetKey] = glossary; dbPut('g/' + targetKey, glossary); }
        return { added, incremented, canceled };
    }


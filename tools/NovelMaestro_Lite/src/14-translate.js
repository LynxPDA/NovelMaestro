    // ===== ПЕРЕВОД =====
    function renderTranslationInto(element, text) {
        const paras = paragraphsOf(text);
        element.innerHTML = '';
        for (const para of paras) {
            const p = document.createElement('p');
            p.textContent = para;
            element.appendChild(p);
        }
    }
    async function translateWithStreaming(element, originalText) {
        const totalParas = paragraphsOf(originalText).length;
        if (totalParas === 0) { progressStatus('❌ Текст не найден'); return { text: '', completed: false }; }
        const chunks = splitByNewlines(originalText, config.chunkSize);
        const fill = progressFill();
        fill.classList.remove('retry');
        let fullTranslation = '', completed = false;
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
                progressStatus(`Чанк ${i + 1}/${chunks.length} • абзацев в источнике: ${totalParas}`);
                const glossaryText = formatGlossaryForPrompt(findRelevantTerms(chunks[i]));
                const userPrompt = config.translationPrompt
                    .replaceAll('{sourceLang}', config.sourceLang)
                    .replaceAll('{targetLang}', config.targetLang)
                    .replaceAll('{glossary}', glossaryText)
                    .replaceAll('{text}', chunks[i]);
                let chunkTranslation = '';
                const res = await callLLM([{ role: 'user', content: userPrompt }], 0.7, true, {
                    onDelta: (content) => {
                        chunkTranslation += content;
                        const all = fullTranslation + (fullTranslation ? '\n\n' : '') + chunkTranslation;
                        renderTranslationInto(element, all);
                        const pct = setProgress(all);
                        progressStatus(`Чанк ${i + 1}/${chunks.length} • ~${pct}%`);
                    },
                    onRetry: (info) => {
                        chunkTranslation = '';
                        renderTranslationInto(element, fullTranslation);
                        setProgress(fullTranslation);
                        fill.classList.add('retry');
                        progressStatus(`⏱ ${info.message} — повтор ${info.nextAttempt}/${info.attemptsTotal}`);
                    }
                });
                fullTranslation += (fullTranslation ? '\n\n' : '') + res.text;
                fill.classList.remove('retry');
                renderTranslationInto(element, fullTranslation);
                setProgress(fullTranslation);
            }
            completed = true;
            progressStatus('✅ Перевод завершён!');
            fill.style.width = '100%';
        } catch (error) {
            progressStatus('❌ ' + error.message);
            // частичный перевод остаётся на экране, но completed=false — в кэш он не попадёт
            if (fullTranslation) renderTranslationInto(element, fullTranslation + '\n\n[ПЕРЕВОД ПРЕРВАН: ' + error.message + ']');
            else renderTranslationInto(element, '');
        } finally {
            fill.classList.remove('retry');
        }
        return { text: fullTranslation, completed };
    }
    async function translateTextBackground(text) {
        const chunks = splitByNewlines(text, config.chunkSize);
        let full = '';
        for (const chunk of chunks) {
            const glossaryText = formatGlossaryForPrompt(findRelevantTerms(chunk));
            const userPrompt = config.translationPrompt
                .replaceAll('{sourceLang}', config.sourceLang)
                .replaceAll('{targetLang}', config.targetLang)
                .replaceAll('{glossary}', glossaryText)
                .replaceAll('{text}', chunk);
            const resp = await callLLM([{ role: 'user', content: userPrompt }], 0.7, false);
            const data = await resp.json().catch(() => null);
            const piece = data && data.choices && data.choices[0] && data.choices[0].message ? (data.choices[0].message.content || '') : '';
            if (!piece) throw new Error('Пустой ответ при фоновом переводе');
            full += (full ? '\n\n' : '') + piece;
        }
        return full;
    }
    function updateExtractionProgress(st) {
        const fill = progressFill();
        fill.classList.toggle('retry', !!st.retry);
        fill.style.width = st.pct + '%';
        progressStatus(st.retry
            ? `⏱ ${st.retry.message} — повтор ${st.retry.nextAttempt}/${st.retry.attemptsTotal}`
            : `🔍 Термины: чанк ${st.chunk}/${st.total} • ~${st.pct}%`);
    }


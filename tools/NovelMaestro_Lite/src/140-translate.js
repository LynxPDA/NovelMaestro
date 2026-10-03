    // ===== ПЕРЕВОД =====
    // промпт чанка: глоссарий подбирается под сам чанк, плейсхолдеры меняются
    // replaceAll — в промптах плейсхолдер может встречаться несколько раз;
    // замена функцией: спец-паттерны замены ($&, $', $`) в тексте главы не
    // должны раскрыться как подстановки
    function chunkUserPrompt(chunkText) {
        const glossaryText = formatGlossaryForPrompt(findRelevantTerms(chunkText));
        return config.translationPrompt
            .replaceAll('{sourceLang}', config.sourceLang)
            .replaceAll('{targetLang}', config.targetLang)
            .replaceAll('{glossary}', glossaryText)
            .replaceAll('{text}', () => chunkText);
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
                let chunkTranslation = '', thoughtShown = false;
                const res = await callLLM([{ role: 'user', content: chunkUserPrompt(chunks[i]) }], 0.7, true, {
                    // thinking-модель: пока приходят только размышления, прогресс
                    // обязан говорить «думает», а не молчать на «Чанк N/M»
                    onReasoning: () => {
                        if (chunkTranslation || thoughtShown) return;
                        thoughtShown = true;
                        progressStatus(`Чанк ${i + 1}/${chunks.length} • 💭 модель размышляет…`);
                    },
                    onDelta: (content) => {
                        chunkTranslation += content;
                        const all = joinParts(parts.map((p, j) => (j === i ? chunkTranslation : p)));
                        renderTranslationInto(element, all);
                        const pct = setProgress(all);
                        progressStatus(`Чанк ${i + 1}/${chunks.length} • ~${pct}%`);
                    },
                    onRetry: (info) => {
                        chunkTranslation = '';
                        thoughtShown = false;
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
            : `🔍 Термины: чанк ${st.chunk}/${st.total}${st.resumed ? ` (продолжаю с ${st.resumed + 1}/${st.total})` : ''} • ~${st.pct}%`);
    }

    // ===== ОСНОВНОЙ ПОТОК =====
    async function handleTranslate() {
        if (isTranslating) return;
        dropdownMenu.classList.remove('active');
        const current = getCurrentBook();
        if (!current) { openBookModal(); return; }
        const sel = current.book.selectors || {};
        if (!sel.content) {
            pendingTranslateAfterTraining = true;
            startElementTraining();
            return;
        }
        await runTranslationFlow(false);
    }
    async function runTranslationFlow(ignoreCache) {
        const current = getCurrentBook();
        if (!current) return;
        if (!config.apiKey && !config.localModel) {
            alert('Укажите API Key в настройках или включите «Локальная модель без API-ключа»');
            openModal();
            return;
        }
        const url = pageCacheKey();
        isTranslating = true;
        cancelRequested = false;
        // «перевести заново» значит начать с чистого листа — фоновое задание сбрасывается
        if (ignoreCache) jobClear(current.key);
        const job = ignoreCache ? null : jobOf(current.key);
        const resumed = job && Array.isArray(job.parts) ? job.parts.filter(p => p).length : 0;
        const title = resumed ? '▶️ Продолжаю фоновый перевод…' : (ignoreCache ? '🔄 Повторный перевод…' : '🔄 Перевод…');
        openReaderShell(title);
        // readerState создаётся СРАЗУ по живой странице — кнопки навигации
        // доступны даже если перевод отменён или не удался
        setReaderState({ url, title: document.title, text: '', ...resolveNavFromLive() }, false);
        let flowCompleted = false, flowCanceled = false;
        // адрес «Открыть на сайте»: выученное оглавление, иначе эта глава
        // (последняя переведённая)
        current.book.openUrl = readerState.tocUrl || url;
        GM_setValue('books', books);
        progressShow(title);
        try {
            const cached = ignoreCache ? null : cacheGet(url);
            if (cached && cached.text) {
                setReaderState({ url, ...cached }, true);
                progressShow('📖 Глава из кэша');
                progressStatus('✅ Перевод уже был готов (опережающий перевод)');
                progressFill().style.width = '100%';
                jobClear(current.key);
                flowCompleted = true;
                if (config.autoNER && !isNerDoneForPage(current.key)) {
                    const liveText = extractMainText(findContentElement());
                    if (liveText.trim()) {
                        extractTermsFromText(liveText, current.key, null).then(res => {
                            if (res && !res.canceled) markNerDone(current.key);
                        }).catch(() => {});
                    }
                }
            } else {
                const element = findContentElement();
                const text = extractMainText(element);
                if (!text.trim()) { alert('Текст не найден на странице'); closeReader(); return; }
                if (config.autoNER) {
                    progressShow('🔍 Извлечение терминов');
                    if (isNerDoneForPage(current.key)) {
                        progressStatus('✨ Термины для этой страницы уже извлекались');
                        await new Promise(r => setTimeout(r, 500));
                    } else {
                        try {
                            const nerResult = await extractTermsFromText(text, current.key, updateExtractionProgress);
                            progressFill().style.width = '100%';
                            if (nerResult.canceled) {
                                progressStatus(`⏹ NER остановлен: +${nerResult.added} новых, обновлено частот: ${nerResult.incremented}`);
                                await new Promise(r => setTimeout(r, 500));
                            } else {
                                // часть чанков могла остаться без валидного JSON —
                                // страницу не помечаем обработанной, чтобы можно было
                                // повторить именно её
                                if (!nerResult.skipped) markNerDone(current.key);
                                progressStatus(`✨ +${nerResult.added} новых, обновлено частот: ${nerResult.incremented}`
                                    + (nerResult.resumed ? ` • продолжен с чанка ${nerResult.resumed + 1}` : '')
                                    + (nerResult.skipped ? ` • ⚠️ чанков без валидного ответа: ${nerResult.skipped} (${nerResult.skippedReason})` : ''));
                                await new Promise(r => setTimeout(r, 800));
                            }
                        } catch (error) {
                            if (!cancelRequested) {
                                progressStatus('⚠️ NER: ' + error.message);
                                await new Promise(r => setTimeout(r, 1500));
                            }
                        }
                    }
                }
                if (cancelRequested) {
                    // отмена до перевода: показывать нечего, кроме причины
                    flowCanceled = true;
                    showReaderNotice('⏹ Перевод остановлен • Меню ⋮ → «🌐 Перевести» продолжит с сохранённого места');
                    progressFinish('⏹ Перевод остановлен', 'Отменено пользователем');
                    return;
                }
                progressShow(title);
                const translationResult = await translateWithStreaming(readerContent, text, { bookKey: current.key });
                const full = translationResult.text || '';
                // навигация обновляется по живой странице независимо от успеха перевода
                const nav = resolveNavFromLive();
                if (readerState) {
                    readerState.text = full;
                    readerState.nextUrl = nav.nextUrl;
                    readerState.prevUrl = nav.prevUrl;
                    readerState.tocUrl = nav.tocUrl;
                }
                updateNavButtons();
                // в кэш — только завершённый перевод (данные собираем из локальных
                // переменных: readerState мог быть обнулён закрытой читалкой);
                // незаконченный остаётся в фоновом задании
                if (translationResult.completed && full) {
                    cacheSet(url, { url, title: document.title, text: full, ...nav }, current.key);
                    flowCompleted = true;
                } else if (cancelRequested) {
                    flowCanceled = true;
                    if (!full) showReaderNotice('⏹ Перевод остановлен • ⋮ → «🌐 Перевести» продолжит с сохранённого места');
                    progressFinish('⏹ Перевод остановлен', `Отменено пользователем${full ? ` • сохранено ${full.length} зн.` : ''}`);
                } else {
                    if (!full) showReaderNotice('⏳ Перевод не завершён • ⋮ → «🌐 Перевести» продолжит с сохранённого места');
                    progressFinish('⏳ Перевод не завершён', `${translationResult.error || 'обрыв связи'}${full ? ` • сохранено ${full.length} зн.` : ' • ни один чанк не дошёл целиком'}`);
                }
            }
            if (!cancelRequested && config.preemptiveTranslation && readerState && readerState.nextUrl) pretranslateNext(readerState.nextUrl);
        } finally {
            isTranslating = false;
            // флаг отмены живёт ровно один прогон: пока он оставался взведённым,
            // каждый следующий запрос (в т.ч. «Проверить сервер») мгновенно падал с
            // «Отменено пользователем (0мс)», и лечилось это перезагрузкой страницы
            cancelRequested = false;
            activeReader = null;
            updateNavButtons();
            // готовая глава и явная отмена гасят панель; оборванный перевод остаётся
            // перед глазами с кнопкой «Скрыть» — но уже не навсегда
            if (flowCompleted) setTimeout(progressHide, 2500);
            else if (flowCanceled) setTimeout(progressHide, 3000);
        }
    }
    async function handleExtractTerms() {
        if (isTranslating) return;
        dropdownMenu.classList.remove('active');
        const current = getCurrentBook();
        if (!current) { alert('Глоссарий привязан к книге — откройте её страницу (книга определяется по заданному URL)'); openBookModal(); return; }
        if (!config.apiKey && !config.localModel) { alert('Укажите API Key в настройках или включите «Локальная модель без API-ключа»'); openModal(); return; }
        const element = findContentElement();
        const text = extractMainText(element);
        if (!text.trim()) { alert('Текст не найден'); return; }
        isTranslating = true;
        cancelRequested = false;
        extractMiniShow();
        try {
            const result = await extractTermsFromText(text, current.key, updateExtractionProgressMini);
            if (!result.canceled && !result.skipped) markNerDone(current.key);
            extractMiniStatus((result.canceled ? '⏹ Остановлено: ' : '✨ ')
                + `+${result.added} новых, обновлено частот: ${result.incremented}`
                + (result.resumed ? ` • продолжен с чанка ${result.resumed + 1}` : '')
                + (result.skipped ? ` • ⚠️ без валидного ответа: ${result.skipped} (${result.skippedReason})` : ''));
            updateGlossaryUI();
            refreshBookTab();
        } catch (error) {
            extractMiniStatus('❌ ' + error.message);
        } finally {
            isTranslating = false;
            // тот же флаг: без сброса отменённое извлечение тихо валило бы каждый
            // следующий запрос до перезагрузки страницы
            cancelRequested = false;
            activeReader = null;
            extractMiniHide(2600);
        }
    }

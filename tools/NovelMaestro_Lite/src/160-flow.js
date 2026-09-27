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
        openReaderShell('⏳ Подготовка главы…');
        // readerState создаётся СРАЗУ по живой странице — кнопки навигации
        // доступны даже если перевод отменён или не удался
        setReaderState({ url, title: document.title, text: '', ...resolveNavFromLive() }, false);
        // адрес «Открыть на сайте»: выученное оглавление, иначе эта глава
        // (последняя переведённая)
        current.book.openUrl = readerState.tocUrl || url;
        GM_setValue('books', books);
        progressShow(ignoreCache ? '🔄 Повторный перевод...' : '🔄 Перевод...');
        $('#btn-translate').disabled = true;
        try {
            const cached = ignoreCache ? null : cacheGet(url);
            if (cached && cached.text) {
                setReaderState({ url, ...cached }, true);
                progressShow('📖 Глава из кэша');
                progressStatus('✅ Перевод уже был готов (опережающий перевод)');
                progressFill().style.width = '100%';
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
                                markNerDone(current.key);
                                progressStatus(`✨ +${nerResult.added} новых, обновлено частот: ${nerResult.incremented}`);
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
                    progressShow('⏹ Отменено');
                    progressStatus('Отменено пользователем');
                    readerContent.innerHTML = '<div class="nm-reader-loading">⏹ Перевод отменён<br><small>Нажмите «🌐 Перевести» внизу, чтобы повторить</small></div>';
                    return;
                }
                progressShow(ignoreCache ? '🔄 Повторный перевод...' : '🔄 Перевод...');
                const translationResult = await translateWithStreaming(readerContent, text);
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
                // частичный остаётся лишь на экране
                if (translationResult.completed && full) cacheSet(url, { url, title: document.title, text: full, ...nav }, current.key);
            }
            if (!cancelRequested && config.preemptiveTranslation && readerState && readerState.nextUrl) pretranslateNext(readerState.nextUrl);
        } finally {
            isTranslating = false;
            $('#btn-translate').disabled = false;
            updateNavButtons();
            setTimeout(progressHide, 2500);
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
            const result = await extractTermsFromText(text, targetKey, updateExtractionProgressMini);
            if (!result.canceled) markNerDone(targetKey);
            extractMiniStatus(result.canceled
                ? `⏹ Остановлено: +${result.added} новых, обновлено частот: ${result.incremented}`
                : `✨ +${result.added} новых, обновлено частот: ${result.incremented}`);
            updateGlossaryUI();
            refreshBookTab();
        } catch (error) {
            extractMiniStatus('❌ ' + error.message);
        } finally {
            isTranslating = false;
            extractMiniHide(2600);
        }
    }


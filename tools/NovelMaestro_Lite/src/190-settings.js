    // ===== НАСТРОЙКИ =====
    function loadSettings() {
        $('#api-host').value = config.apiHost;
        $('#api-key').value = config.apiKey;
        $('#model').value = config.model;
        // уровень — select: старое «None» показывается как «не отправлять»
        $('#reasoning-effort').value = normalizeEffort(config.reasoningEffort);
        $('#thinking-mode').value = thinkingMode();
        $('#reasoning-profile').value = REASONING_PROFILES[config.thinkingProfile] ? config.thinkingProfile : 'openai';
        $('#thinking-budget').value = config.thinkingBudget;
        $('#extra-body-json').value = config.extraBodyJson;
        $('#request-timeout').value = config.requestTimeout;
        $('#max-retries').value = config.maxRetries;
        $('#chunk-size').value = config.chunkSize;
        $('#source-lang').value = config.sourceLang;
        $('#target-lang').value = config.targetLang;
        $('#fuzzy-threshold').value = config.fuzzySearchThreshold;
        $('#auto-ner').checked = !!config.autoNER;
        $('#glossary-current-only').checked = !!config.glossaryCurrentPageOnly;
        $('#local-model').checked = !!config.localModel;
        $('#api-key').disabled = !!config.localModel;
        $('#preemptive-translate').checked = !!config.preemptiveTranslation;
        $('#gm-transport').checked = (config.gmTransport === 'auto' ? 'page' : config.gmTransport) === 'manager';
        $('#reader-theme').value = config.readerTheme;
        $('#reader-font-family').value = config.readerFontFamily;
        $('#reader-font-size').value = config.readerFontSize;
        $('#reader-line-height').value = config.readerLineHeight;
        $('#reader-paragraph-spacing').value = config.readerParagraphSpacing;
        $('#reader-content-width').value = config.readerContentWidth;
        $('#translation-prompt').value = config.translationPrompt;
        $('#extraction-prompt').value = config.extractionPrompt;
    }
    const SETTING_FIELDS = [
        ['#api-host', 'apiHost', v => v.trim()],
        ['#api-key', 'apiKey', v => v.trim()],
        ['#model', 'model', v => v.trim()],
        ['#reasoning-effort', 'reasoningEffort', v => v],
        ['#thinking-mode', 'thinkingMode', v => v],
        ['#reasoning-profile', 'thinkingProfile', v => v],
        ['#thinking-budget', 'thinkingBudget', v => { const n = parseInt(v, 10); return Number.isFinite(n) && n > 0 ? n : 0; }],
        ['#request-timeout', 'requestTimeout', v => { const n = parseInt(v, 10); return Number.isFinite(n) && n >= 0 ? n : 10; }],
        ['#max-retries', 'maxRetries', v => { const n = parseInt(v, 10); return Number.isFinite(n) ? Math.min(10, Math.max(0, n)) : 3; }],
        ['#chunk-size', 'chunkSize', v => { const n = parseInt(v, 10); return Number.isFinite(n) && n > 0 ? n : DEFAULT_CONFIG.chunkSize; }],
        ['#source-lang', 'sourceLang', v => v],
        ['#target-lang', 'targetLang', v => v],
        ['#fuzzy-threshold', 'fuzzySearchThreshold', v => { const f = parseFloat(v); return Number.isFinite(f) ? f : DEFAULT_CONFIG.fuzzySearchThreshold; }],
        ['#reader-theme', 'readerTheme', v => v],
        ['#reader-font-family', 'readerFontFamily', v => v],
        ['#reader-font-size', 'readerFontSize', v => { const n = parseInt(v, 10); return Number.isFinite(n) && n > 0 ? n : DEFAULT_CONFIG.readerFontSize; }],
        ['#reader-line-height', 'readerLineHeight', v => { const f = parseFloat(v); return Number.isFinite(f) ? f : DEFAULT_CONFIG.readerLineHeight; }],
        ['#reader-paragraph-spacing', 'readerParagraphSpacing', v => { const f = parseFloat(v); return Number.isFinite(f) ? f : DEFAULT_CONFIG.readerParagraphSpacing; }],
        ['#reader-content-width', 'readerContentWidth', v => { const n = parseInt(v, 10); return Number.isFinite(n) ? Math.min(100, Math.max(30, n)) : DEFAULT_CONFIG.readerContentWidth; }],
        ['#translation-prompt', 'translationPrompt', v => v],
        ['#extraction-prompt', 'extractionPrompt', v => v]
    ];
    let settingsSaveTimer = null;
    function persistSettings() {
        GM_setValue('config', config);
        showStatus('✅ Настройки сохранены автоматически', 'success', 'status-settings');
        applyTheme();
    }
    function scheduleSettingsSave() {
        clearTimeout(settingsSaveTimer);
        settingsSaveTimer = setTimeout(persistSettings, 400);
    }
    function bindSettingsAutoSave() {
        for (const [sel, key, parse] of SETTING_FIELDS) {
            const el = $(sel);
            const save = () => { config[key] = parse(el.value); scheduleSettingsSave(); };
            el.addEventListener('input', save);
            el.addEventListener('change', save);
        }
        $('#auto-ner').addEventListener('change', function() { config.autoNER = this.checked; scheduleSettingsSave(); });
        $('#glossary-current-only').addEventListener('change', function() { config.glossaryCurrentPageOnly = this.checked; glossaryPage = 0; scheduleSettingsSave(); updateGlossaryUI(); });
        $('#local-model').addEventListener('change', function() {
            config.localModel = this.checked;
            $('#api-key').disabled = this.checked;
            scheduleSettingsSave();
        });
        $('#preemptive-translate').addEventListener('change', function() { config.preemptiveTranslation = this.checked; scheduleSettingsSave(); });
        $('#gm-transport').addEventListener('change', function() { config.gmTransport = this.checked ? 'manager' : 'page'; scheduleSettingsSave(); });
        // свои поля — JSON: битый стоит показать сразу, а не молча игнорировать
        // (текст при этом сохраняется — пользователь не теряет ввод)
        const extra = $('#extra-body-json');
        const saveExtra = () => {
            const raw = extra.value.trim();
            extra.classList.remove('nm-input-bad');
            if (raw) {
                try {
                    const parsed = JSON.parse(raw);
                    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('нужен JSON-объект');
                } catch (e) {
                    // битый JSON показываем причиной, а не «сохранено»: висящий debounce иначе
                    // затирает сообщение через 400 мс, а свои поля тем временем молча не отправляются
                    clearTimeout(settingsSaveTimer);
                    extra.classList.add('nm-input-bad');
                    config.extraBodyJson = raw;
                    GM_setValue('config', config);
                    showStatus(`❌ «Свои поля запроса» не разобраны (${e.message}) — запрос идёт без них`, 'error', 'status-settings');
                    return;
                }
            }
            config.extraBodyJson = raw;
            scheduleSettingsSave();
        };
        extra.addEventListener('input', saveExtra);
        extra.addEventListener('change', saveExtra);
    }
    function resetSettings() {
        if (!confirm('Сбросить все настройки к значениям по умолчанию?')) return;
        config = { ...DEFAULT_CONFIG };
        GM_setValue('config', config);
        loadSettings();
        applyTheme();
        showStatus('Настройки сброшены!', 'success', 'status-settings');
    }
    function saveNewBook() {
        const url = $('#book-modal-url').value.trim();
        const name = $('#book-modal-name').value.trim();
        if (!url) { alert('Укажите URL книги'); return; }
        if (books[url] && !confirm('Книга с таким URL уже существует. Заменить название (глоссарий сохранится)?')) return;
        // в записи книги — только метаданные; глоссарий и кэш — в IndexedDB её сайта
        books[url] = {
            ...books[url],
            name: name || 'Без названия',
            selectors: books[url] ? books[url].selectors || {} : {},
            coverUrl: books[url] ? books[url].coverUrl || '' : '',
            openUrl: (books[url] && books[url].openUrl) || url
        };
        currentBookKey = url;
        managedBookKey = url;
        GM_setValue('books', books);
        bookModal.classList.remove('active');
        refreshBookTab();
        updateGlossaryUI();
    }



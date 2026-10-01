// ===== НАСТРОЙКИ =====
    // Вторичные вкладки: «то, что нужно всем» и «то, что нужно энтузиастам».
    // Открытая вкладка — клиентское предпочтение, но хранилище Lite одно
    // (GM-хранилище расширения), поэтому оно лежит рядом с остальным конфигом.
    function setSettingsSubTab(name) {
        const tab = $('#stab-' + name) ? name : 'main';
        config.settingsSubTab = tab;
        $$('.nm-subtab').forEach(t => t.classList.toggle('active', t.dataset.stab === tab));
        $$('.nm-subtab-content').forEach(c => c.classList.toggle('active', c.id === 'stab-' + tab));
    }

    /**
     * Числовое поле настроек. Молча подменить битый ввод дефолтом нельзя:
     * пользователь должен видеть, что его ввод не принят. problem — причина
     * (поле подсвечивается), value — то, что реально уходит в конфиг.
     */
    function parseNumSetting(raw, { name, def, min, max, int = true }) {
        const text = String(raw ?? '').trim();
        const n = Number(text);
        if (text === '' || !Number.isFinite(n)) {
            return { value: def, problem: `${name}: ${text ? `«${text}»` : 'пусто'} — нужны цифры, применилось ${def}` };
        }
        const v = int ? Math.trunc(n) : n;
        const lo = min !== undefined && v < min;
        const hi = max !== undefined && v > max;
        if (lo || hi) {
            const fixed = lo ? min : max;
            return { value: fixed, problem: `${name}: ${n} вне диапазона ${min}–${max}, применилось ${fixed}` };
        }
        return { value: v };
    }
    const asText = raw => ({ value: raw });

    // [селектор, ключ конфига, читатель значения, (показатель для поля)]
    const SETTING_FIELDS = [
        ['#api-host', 'apiHost', raw => ({ value: raw.trim() })],
        ['#api-key', 'apiKey', raw => ({ value: raw.trim() })],
        ['#model', 'model', raw => ({ value: raw.trim() })],
        ['#source-lang', 'sourceLang', asText],
        ['#target-lang', 'targetLang', asText],
        ['#request-timeout', 'requestTimeout', raw => parseNumSetting(raw, { name: 'Таймаут', def: DEFAULT_CONFIG.requestTimeout, min: 0 })],
        ['#max-retries', 'maxRetries', raw => parseNumSetting(raw, { name: 'Ретраи', def: DEFAULT_CONFIG.maxRetries, min: 0, max: 10 })],
        ['#chunk-size', 'chunkSize', raw => parseNumSetting(raw, { name: 'Размер чанка', def: DEFAULT_CONFIG.chunkSize, min: 100, max: 30000 })],
        ['#fuzzy-threshold', 'fuzzySearchThreshold', raw => parseNumSetting(raw, { name: 'Порог нечёткого поиска', def: DEFAULT_CONFIG.fuzzySearchThreshold, min: 0, max: 1, int: false })],
        ['#reader-theme', 'readerTheme', asText],
        ['#reader-font-family', 'readerFontFamily', asText],
        ['#reader-font-size', 'readerFontSize', raw => parseNumSetting(raw, { name: 'Размер шрифта', def: DEFAULT_CONFIG.readerFontSize, min: 12, max: 32 })],
        ['#reader-line-height', 'readerLineHeight', raw => parseNumSetting(raw, { name: 'Межстрочный интервал', def: DEFAULT_CONFIG.readerLineHeight, min: 1, max: 3, int: false })],
        ['#reader-paragraph-spacing', 'readerParagraphSpacing', raw => parseNumSetting(raw, { name: 'Отступ между абзацами', def: DEFAULT_CONFIG.readerParagraphSpacing, min: 0.2, max: 4, int: false })],
        ['#reader-content-width', 'readerContentWidth', raw => parseNumSetting(raw, { name: 'Ширина колонки', def: DEFAULT_CONFIG.readerContentWidth, min: 30, max: 100 })],
        ['#thinking-mode', 'thinkingMode', asText],
        ['#reasoning-profile', 'thinkingProfile', asText],
        ['#reasoning-effort', 'reasoningEffort', asText, normalizeEffort],
        ['#thinking-budget', 'thinkingBudget', raw => parseNumSetting(raw, { name: 'Бюджет размышлений', def: DEFAULT_CONFIG.thinkingBudget, min: 0 })],
        ['#translation-prompt', 'translationPrompt', asText],
        ['#extraction-prompt', 'extractionPrompt', asText]
    ];
    // Поля с непринятым вводом: селектор → причина. Пока карта не пуста, подвал
    // настроек показывает ошибку, а не «✅ сохранено».
    const settingsProblems = new Map();

    function loadSettings() {
        for (const [sel, key, , show] of SETTING_FIELDS) {
            const el = $(sel);
            el.value = show ? show(config[key]) : config[key];
        }
        $('#auto-ner').checked = !!config.autoNER;
        $('#glossary-current-only').checked = !!config.glossaryCurrentPageOnly;
        $('#local-model').checked = !!config.localModel;
        $('#api-key').disabled = !!config.localModel;
        $('#preemptive-translate').checked = !!config.preemptiveTranslation;
        $('#gm-transport').checked = (config.gmTransport === 'auto' ? 'page' : config.gmTransport) === 'manager';
        setSettingsSubTab(config.settingsSubTab);
        // подсветка переживает закрытие модалки — вместе с ней обязан жить и ответ
        // «почему поле красное»
        if (settingsProblems.size) settingsStatus(); else hideStatus('status-settings');
    }
    function settingsStatus() {
        const all = [...settingsProblems.values()];
        if (!all.length) {
            showStatus('✅ Настройки сохранены автоматически', 'success', 'status-settings');
            return;
        }
        showStatus(`❌ ${all[0]}${all.length > 1 ? ` (и ещё ${all.length - 1})` : ''}`, 'error', 'status-settings');
    }
    function paintSetting(sel, el, problem) {
        if (problem) settingsProblems.set(sel, problem);
        else settingsProblems.delete(sel);
        el.classList.toggle('nm-input-bad', !!problem);
        el.setAttribute('aria-invalid', problem ? 'true' : 'false');
    }
    function persistSettings() {
        GM_setValue('config', config);
        settingsStatus();
        applyTheme();
    }
    let settingsSaveTimer = null;
    function scheduleSettingsSave() {
        clearTimeout(settingsSaveTimer);
        settingsSaveTimer = setTimeout(persistSettings, 400);
    }
    function bindSettingsAutoSave() {
        for (const [sel, key, read] of SETTING_FIELDS) {
            const el = $(sel);
            const save = () => {
                const res = read(el.value);
                paintSetting(sel, el, res.problem);
                config[key] = res.value;
                scheduleSettingsSave();
            };
            el.addEventListener('input', save);
            el.addEventListener('change', save);
            // в number-поле буквы не дают ни значения, ни input-события (браузер их
            // просто глотает) — поле проверяется ещё и на выход из него
            el.addEventListener('blur', save);
        }
        // чекбоксы: значение читается из checked, зона клика — вся карточка (label)
        const check = (sel, key, after) => $(sel).addEventListener('change', function () {
            config[key] = this.checked;
            if (after) after(this);
            scheduleSettingsSave();
        });
        check('#auto-ner', 'autoNER');
        check('#preemptive-translate', 'preemptiveTranslation');
        check('#glossary-current-only', 'glossaryCurrentPageOnly', () => { glossaryPage = 0; updateGlossaryUI(); });
        check('#local-model', 'localModel', el => { $('#api-key').disabled = el.checked; });
        $('#gm-transport').addEventListener('change', function () {
            config.gmTransport = this.checked ? 'manager' : 'page';
            scheduleSettingsSave();
        });
        // свои поля — JSON: битый стоит показать сразу, а не молча игнорировать
        // (текст при этом сохраняется — пользователь не теряет ввод)
        const extra = $('#extra-body-json');
        const saveExtra = () => {
            const raw = extra.value.trim();
            let problem = '';
            if (raw) {
                try {
                    const parsed = JSON.parse(raw);
                    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('нужен JSON-объект');
                } catch (e) {
                    problem = `«Свои поля запроса» не разобраны (${e.message}) — запрос идёт без них`;
                }
            }
            paintSetting('#extra-body-json', extra, problem);
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
        settingsProblems.clear();
        $$('.nm-input-bad').forEach(el => { el.classList.remove('nm-input-bad'); el.setAttribute('aria-invalid', 'false'); });
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

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
     * Открытая вкладка запоминается молча: это не правка настройки, и кричать про
     * неё «✅ Настройки сохранены» — значит врать пользователю.
     */
    function persistSettingsSubTab() {
        GM_setValue('config', config);
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

    // Поля настроек: [селектор, ключ конфига, читатель значения, (показатель для поля)].
    // Порядок = порядок панелей вторичных вкладок: Основные → Читалка → Перевод →
    // Продвинутое. Вкладка поля не хранится в таблице намеренно: она берётся из
    // разметки (closest('.nm-subtab-content')), иначе разъедется с ней.
    const SETTING_FIELDS = [
        // 🌐 Основные: Интерфейс (тема одна на интерфейс и читалку), Языки, API
        ['#reader-theme', 'readerTheme', asText],
        ['#source-lang', 'sourceLang', asText],
        ['#target-lang', 'targetLang', asText],
        ['#api-host', 'apiHost', raw => ({ value: raw.trim() })],
        ['#api-key', 'apiKey', raw => ({ value: raw.trim() })],
        ['#model', 'model', raw => ({ value: raw.trim() })],
        // 📖 Читалка
        ['#reader-font-family', 'readerFontFamily', asText],
        ['#reader-font-size', 'readerFontSize', raw => parseNumSetting(raw, { name: 'Размер шрифта', def: DEFAULT_CONFIG.readerFontSize, min: 12, max: 32 })],
        ['#reader-line-height', 'readerLineHeight', raw => parseNumSetting(raw, { name: 'Межстрочный интервал', def: DEFAULT_CONFIG.readerLineHeight, min: 1, max: 3, int: false })],
        ['#reader-paragraph-spacing', 'readerParagraphSpacing', raw => parseNumSetting(raw, { name: 'Отступ между абзацами', def: DEFAULT_CONFIG.readerParagraphSpacing, min: 0.2, max: 4, int: false })],
        ['#reader-content-width', 'readerContentWidth', raw => parseNumSetting(raw, { name: 'Ширина колонки', def: DEFAULT_CONFIG.readerContentWidth, min: 30, max: 100 })],
        // 🔄 Перевод
        ['#chunk-size', 'chunkSize', raw => parseNumSetting(raw, { name: 'Размер чанка', def: DEFAULT_CONFIG.chunkSize, min: 100, max: 30000 })],
        ['#fuzzy-threshold', 'fuzzySearchThreshold', raw => parseNumSetting(raw, { name: 'Порог нечёткого поиска', def: DEFAULT_CONFIG.fuzzySearchThreshold, min: 0, max: 1, int: false })],
        ['#translation-prompt', 'translationPrompt', asText],
        ['#extraction-prompt', 'extractionPrompt', asText],
        // ⚙️ Продвинутое
        ['#request-timeout', 'requestTimeout', raw => parseNumSetting(raw, { name: 'Таймаут', def: DEFAULT_CONFIG.requestTimeout, min: 0 })],
        ['#max-retries', 'maxRetries', raw => parseNumSetting(raw, { name: 'Ретраи', def: DEFAULT_CONFIG.maxRetries, min: 0, max: 10 })],
        ['#thinking-mode', 'thinkingMode', asText],
        ['#reasoning-profile', 'thinkingProfile', asText],
        ['#reasoning-effort', 'reasoningEffort', asText, normalizeEffort],
        ['#thinking-budget', 'thinkingBudget', raw => parseNumSetting(raw, { name: 'Бюджет размышлений', def: DEFAULT_CONFIG.thinkingBudget, min: 0 })]
    ];
    // Чекбоксы: [селектор, ключ, (значение конфига → состояние), (состояние → значение),
    // (действие после смены)]. gmTransport — не «вкл/выкл», а выбор канала, поэтому у
    // него свои читатель и писатель. «только текущая страница» живёт на вкладке
    // «Глоссарий» — в фильтр-строке, а не во вторичных вкладках настроек.
    const SETTING_CHECKS = [
        ['#local-model', 'localModel', null, null, el => { $('#api-key').disabled = el.checked; }],
        ['#gm-transport', 'gmTransport', v => (v === 'auto' ? 'page' : v) === 'manager', on => (on ? 'manager' : 'page')],
        ['#preemptive-translate', 'preemptiveTranslation'],
        ['#auto-ner', 'autoNER'],
        ['#glossary-current-only', 'glossaryCurrentPageOnly', null, null, () => { glossaryPage = 0; updateGlossaryUI(); }]
    ];
    // Поля с непринятым вводом: селектор → причина. Пока карта не пуста, подвал
    // настроек показывает ошибку, а не «сохранено».
    const settingsProblems = new Map();
    let settingsStatusTimer = null;

    // вторичная вкладка, в панели которой лежит поле ('' — вне настроек)
    const settingsSubTabOf = el => {
        const panel = el.closest('.nm-subtab-content');
        return panel ? panel.id.replace('stab-', '') : '';
    };

    function loadSettings() {
        for (const [sel, key, , show] of SETTING_FIELDS) {
            const el = $(sel);
            el.value = show ? show(config[key]) : config[key];
        }
        for (const [sel, key, read, , after] of SETTING_CHECKS) {
            const el = $(sel);
            el.checked = read ? read(config[key]) : config[key];
            // служебный after — только для полей самих настроек (фильтр глоссария
            // со своей вкладкой не обязан сбрасывать страницу списка при каждом открытии)
            if (after && settingsSubTabOf(el)) after(el);
        }
        setSettingsSubTab(config.settingsSubTab);
        // подсветка переживает закрытие модалки — вместе с ней обязан жить и ответ
        // «почему поле красное»
        if (settingsProblems.size) settingsStatus(); else hideStatus('status-settings');
    }
    /**
     * Индикатор подвала: ошибка висит, пока поле не починят, а «сохранено» —
     * вспышка на 3 с: постоянная плашка на пустом месте только мешает.
     */
    function settingsStatus(msg) {
        clearTimeout(settingsStatusTimer);
        const all = [...settingsProblems.values()];
        if (all.length) {
            showStatus(`❌ ${all[0]}${all.length > 1 ? ` (и ещё ${all.length - 1})` : ''}`, 'error', 'status-settings');
            return;
        }
        showStatus(msg || '✅ Настройки сохранены автоматически', 'success', 'status-settings');
        settingsStatusTimer = setTimeout(() => hideStatus('status-settings'), 3000);
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
            // в number-поле буквы не дают ни значения, ни input-события (браузер их
            // просто глотает) — поле проверяется ещё и на выход из него
            el.addEventListener('input', save);
            el.addEventListener('change', save);
            el.addEventListener('blur', save);
        }
        for (const [sel, key, , write, after] of SETTING_CHECKS) {
            const el = $(sel);
            el.addEventListener('change', () => {
                config[key] = write ? write(el.checked) : el.checked;
                if (after) after(el);
                scheduleSettingsSave();
            });
        }
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
    /**
     * Сброс касается только открытой вторичной вкладки: сбитый кегль в «Читалке»
     * не обязан стирать пользовательские промпты и reasoning-настройки.
     */
    function resetSettings() {
        const tab = config.settingsSubTab;
        const title = ($(`.nm-subtab[data-stab="${tab}"]`) || { textContent: tab }).textContent.trim();
        if (!confirm(`Сбросить настройки вкладки «${title}» к значениям по умолчанию?\nОстальные вкладки останутся как есть.`)) return;
        for (const [sel, key, , show] of SETTING_FIELDS) {
            const el = $(sel);
            if (settingsSubTabOf(el) !== tab) continue;
            config[key] = DEFAULT_CONFIG[key];
            el.value = show ? show(DEFAULT_CONFIG[key]) : DEFAULT_CONFIG[key];
            paintSetting(sel, el, '');
        }
        for (const [sel, key, read, , after] of SETTING_CHECKS) {
            const el = $(sel);
            if (settingsSubTabOf(el) !== tab) continue;
            config[key] = DEFAULT_CONFIG[key];
            el.checked = read ? read(DEFAULT_CONFIG[key]) : DEFAULT_CONFIG[key];
            if (after) after(el);
        }
        GM_setValue('config', config);
        applyTheme();
        settingsStatus(`✅ Вкладка «${title}» сброшена к значениям по умолчанию`);
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

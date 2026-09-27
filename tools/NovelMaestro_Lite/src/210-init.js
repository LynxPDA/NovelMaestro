    // ===== ИНИЦИАЛИЗАЦИЯ =====
    currentBookKey = findBookByUrl();
    bindSettingsAutoSave();
    applyTheme();
    let cfgMigrated = false;
    // дефолты читалки: 18px/66% → 14px/80% (сбрасываются только старые дефолты,
    // пользовательские размеры остаются); выполняется один раз на установку
    if (!config.readerDefaultsV2) {
        if (config.readerFontSize === 18) config.readerFontSize = 14;
        if (config.readerContentWidth === 66) config.readerContentWidth = 80;
        config.readerDefaultsV2 = true;
        cfgMigrated = true;
    }
    if (typeof config.requestTimeout === 'number' && config.requestTimeout > 1000) {
        config.requestTimeout = Math.max(1, Math.round(config.requestTimeout / 1000));
        cfgMigrated = true;
    }
    // старые значения ширины колонки были в пикселях, новые — проценты ширины экрана (30-100)
    if (typeof config.readerContentWidth === 'number' && config.readerContentWidth > 100) {
        config.readerContentWidth = DEFAULT_CONFIG.readerContentWidth;
        cfgMigrated = true;
    }
    // наследие эпохи GM-кэша: количество опережаемых глав и лимит кэша убраны,
    // смысл количества наследует чекбокс автоперевода следующей главы
    if ('preemptiveCount' in config) {
        if (typeof config.preemptiveTranslation !== 'boolean') config.preemptiveTranslation = config.preemptiveCount > 0;
        delete config.preemptiveCount;
        cfgMigrated = true;
    }
    if ('cacheLimit' in config) { delete config.cacheLimit; cfgMigrated = true; }
    if ('glossarySource' in config) { delete config.glossarySource; cfgMigrated = true; }
    if (cfgMigrated) GM_setValue('config', config);
    // наследие GM-эпохи удаляем совсем (данные не мигрируем — скрипт в разработке)
    for (const k of ['chapterCache', 'globalGlossary']) {
        if (typeof GM_deleteValue === 'function') GM_deleteValue(k);
        else GM_setValue(k, null);
    }
    // записи книг теперь содержат только метаданные: глоссарии и nerDone из GM убираем
    let booksChanged = false;
    for (const b of Object.values(books)) {
        if ('glossary' in b || 'nerDone' in b) { delete b.glossary; delete b.nerDone; booksChanged = true; }
    }
    if (booksChanged) GM_setValue('books', books);
    // данные сайта грузим из IndexedDB асинхронно; автозапуск перевода при переходе
    // с другой главы дожидается, чтобы не потерять кэш и отметки NER
    const autoRun = !!sessionStorage.getItem('nm_auto_reader');
    sessionStorage.removeItem('nm_auto_reader');
    loadSiteData().then(() => {
        if (modal.classList.contains('active')) { refreshBookTab(); updateGlossaryUI(); }
        if (autoRun) setTimeout(() => handleTranslate(), 400);
    });
    console.log(`NovelMaestro Lite v${APP_VERSION} загружен. Книга:`, currentBookKey || 'не определена');

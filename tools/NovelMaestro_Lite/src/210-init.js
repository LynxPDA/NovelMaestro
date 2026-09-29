// ===== ИНИЦИАЛИЗАЦИЯ =====
    currentBookKey = findBookByUrl();
    bindSettingsAutoSave();
    applyTheme();
    // данные сайта грузим из IndexedDB асинхронно; автозапуск перевода при переходе
    // с другой главы дожидается, чтобы не потерять кэш и отметки NER
    const autoRun = !!sessionStorage.getItem('nm_auto_reader');
    sessionStorage.removeItem('nm_auto_reader');
    loadSiteData().then(() => {
        if (modal.classList.contains('active')) { refreshBookTab(); updateGlossaryUI(); }
        if (autoRun) setTimeout(() => handleTranslate(), 400);
    });
    console.log(`NovelMaestro Lite v${APP_VERSION} загружен. Книга:`, currentBookKey || 'не определена');

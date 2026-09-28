    // ===== СОБЫТИЯ =====
    $('#btn-translate').addEventListener('click', handleTranslate);
    menuBtn.addEventListener('click', (e) => { e.stopPropagation(); dropdownMenu.classList.toggle('active'); });
    document.addEventListener('click', (e) => {
        const path = typeof e.composedPath === 'function' ? e.composedPath() : [e.target];
        if (!path.includes(menuBtn) && !path.includes(dropdownMenu)) dropdownMenu.classList.remove('active');
    }, true);
    $('#btn-book-menu').addEventListener('click', () => { dropdownMenu.classList.remove('active'); openBookModal(); });
    $('#btn-settings-menu').addEventListener('click', openModal);
    $('#btn-extract-menu').addEventListener('click', handleExtractTerms);
    $('#btn-train-menu').addEventListener('click', () => { pendingTranslateAfterTraining = false; startElementTraining(); });
    $('#nm-close').addEventListener('click', closeModal);
    modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
    bookModal.addEventListener('click', e => { if (e.target === bookModal) bookModal.classList.remove('active'); });
    $('#reader-close').addEventListener('click', closeReader);
    $('#reader-theme-toggle').addEventListener('click', () => {
        // три состояния: как в системе → тёмная → светлая; кнопка показывает текущее в title
        config.readerTheme = THEME_MODE_CYCLE[config.readerTheme] || 'auto';
        GM_setValue('config', config);
        applyTheme();
    });
    $('#reader-settings').addEventListener('click', openModal);
    $('#reader-retranslate').addEventListener('click', () => {
        if (isTranslating) return;
        runTranslationFlow(true);
    });
    $('#reader-prev').addEventListener('click', () => gotoChapter(readerState && readerState.prevUrl));
    $('#reader-next').addEventListener('click', () => gotoChapter(readerState && readerState.nextUrl));
    $('#reader-toc').addEventListener('click', () => {
        if (!readerState || !readerState.tocUrl) return;
        let target = null;
        try { target = new URL(readerState.tocUrl, location.href); } catch { return; }
        if (target.protocol === 'http:' || target.protocol === 'https:') openExternalTab(target.href);
    });
    $('#reader-cancel').addEventListener('click', () => {
        cancelRequested = true;
        if (activeReader) { try { activeReader.cancel(); } catch {} }
    });
    $('#btn-finish-training').addEventListener('click', finishTraining);
    $('#btn-cancel-training').addEventListener('click', () => { pendingTranslateAfterTraining = false; stopElementTraining(); });
    $('#btn-training-cancel-pick').addEventListener('click', () => { trainingPopup.classList.remove('active'); trainingPopupTarget = null; });
    trainingPopup.querySelectorAll('[data-type]').forEach(btn => {
        btn.addEventListener('click', () => assignElementType(btn.dataset.type));
    });
    $$('.nm-tab').forEach(tab => {
        tab.addEventListener('click', function() {
            $$('.nm-tab').forEach(t => t.classList.remove('active'));
            $$('.nm-tab-content').forEach(c => c.classList.remove('active'));
            this.classList.add('active');
            $('#tab-' + this.dataset.tab).classList.add('active');
        });
    });
    $('#book-select').addEventListener('change', function() { managedBookKey = this.value || null; renderBookManageArea(); });
    $('#glossary-filter').addEventListener('input', function() { glossaryFilter = this.value; glossaryPage = 0; updateGlossaryUI(); });
    $('#btn-extract-terms').addEventListener('click', handleExtractTerms);
    $('#btn-add-term').addEventListener('click', addTerm);
    $('#btn-import').addEventListener('click', importGlossary);
    $('#btn-export').addEventListener('click', exportGlossary);
    $('#btn-clear-glossary').addEventListener('click', clearGlossary);
    $('#btn-reset-settings').addEventListener('click', resetSettings);
    $('#btn-check-server').addEventListener('click', checkServer);
    $('#btn-full-export').addEventListener('click', exportAllData);
    $('#btn-full-import').addEventListener('click', importAllData);
    $('#btn-save-new-book').addEventListener('click', saveNewBook);
    $('#btn-cancel-new-book').addEventListener('click', () => bookModal.classList.remove('active'));
    $('#btn-autofill-url').addEventListener('click', () => { $('#book-modal-url').value = suggestBookKeyFromUrl(); });



    const $ = sel => shadow.querySelector(sel);
    const $$ = sel => shadow.querySelectorAll(sel);

    const modal = $('#nm-modal');
    const bookModal = $('#nm-book-modal');
    const dropdownMenu = $('#dropdown-menu');
    const menuBtn = $('#btn-menu');
    const buttonsBar = $('#nm-buttons');
    const readerMode = $('#nm-reader-mode');
    const readerContent = $('#reader-content');
    const elementTraining = $('#nm-element-training');
    const trainingPopup = $('#training-popup');
    const rootEl = $('#nm-root');

    let isTranslating = false;
    let cancelRequested = false;
    let activeReader = null;

    // ===== МИНИ-ОКНО ИЗВЛЕЧЕНИЯ ТЕРМИНОВ =====
    // Ручной NER живёт в маленьком плавающем окне, а не в читалке
    (function initMiniExtractPopup() {
        const st = document.createElement('style');
        st.textContent = `
            .nm-mini-modal { position: fixed; right: 16px; bottom: 84px; z-index: 2147483646; width: min(380px, calc(100vw - 24px)); background: #ffffff; color: #111827; border-radius: 12px; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3); padding: 12px; display: none; }
            .nm-mini-modal.active { display: block; }
            .nm-mini-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-weight: 600; margin-bottom: 8px; }
            .nm-mini-header button { border: none; background: none; font-size: 18px; cursor: pointer; color: inherit; padding: 4px; line-height: 1; }
            .nm-mini-status { margin-top: 6px; font-size: 12px; opacity: 0.75; word-break: break-word; }
            #nm-root.nm-ui-dark .nm-mini-modal { background: #1f232b; color: #e2e2dc; }
            @media (max-width: 768px), (pointer: coarse) {
                .nm-mini-modal { left: 12px; right: 12px; width: auto; bottom: calc(84px + env(safe-area-inset-bottom, 0px)); }
            }
        `;
        shadow.appendChild(st);
        const popup = document.createElement('div');
        popup.id = 'nm-extract-popup';
        popup.className = 'nm-mini-modal';
        popup.innerHTML = `
            <div class="nm-mini-header">
                <span>✨ Извлечение терминов</span>
                <button type="button" id="nm-extract-close" title="Скрыть">✕</button>
            </div>
            <div class="nm-progress-bar"><div class="nm-progress-fill" id="nm-extract-fill"></div></div>
            <div class="nm-mini-status" id="nm-extract-status">Подготовка...</div>
            <button class="nm-btn nm-btn-danger nm-btn-sm" id="nm-extract-cancel" style="margin-top:10px;">Отменить</button>
        `;
        rootEl.appendChild(popup);
    })();
    function extractMiniShow() {
        $('#nm-extract-popup').classList.add('active');
        const f = $('#nm-extract-fill');
        f.style.width = '0%';
        f.classList.remove('retry');
        $('#nm-extract-status').textContent = 'Подготовка...';
    }
    function extractMiniStatus(text) { $('#nm-extract-status').textContent = text; }
    function extractMiniHide(delay = 2200) {
        setTimeout(() => {
            const popup = $('#nm-extract-popup');
            if (popup) popup.classList.remove('active');
        }, delay);
    }
    function updateExtractionProgressMini(st) {
        const fill = $('#nm-extract-fill');
        if (!fill) return;
        fill.classList.toggle('retry', !!st.retry);
        fill.style.width = st.pct + '%';
        extractMiniStatus(st.retry
            ? `⏱ ${st.retry.message} — повтор ${st.retry.nextAttempt}/${st.retry.attemptsTotal}`
            : `🔍 Термины: чанк ${st.chunk}/${st.total}${st.resumed ? ` (продолжаю с ${st.chunk}/${st.resumed + 1})` : ''} • ~${st.pct}%`);
    }
    $('#nm-extract-cancel').addEventListener('click', () => {
        cancelRequested = true;
        if (activeReader) { try { activeReader.cancel(); } catch {} }
    });
    $('#nm-extract-close').addEventListener('click', () => {
        if (!isTranslating) $('#nm-extract-popup').classList.remove('active');
    });

    // ===== ПРОГРЕСС =====
    function progressShow(title) {
        if (!readerModeActive) openReaderShell(null);
        $('#reader-progress').classList.add('active');
        $('#reader-progress-title').textContent = title;
        $('#reader-progress-status').textContent = 'Подготовка...';
        const f = $('#reader-progress-fill');
        f.style.width = '0%';
        f.classList.remove('retry');
        updateNavButtons();
    }
    function progressStatus(t) { $('#reader-progress-status').textContent = t; }
    function progressFill() { return $('#reader-progress-fill'); }
    function progressHide() {
        $('#reader-progress').classList.remove('active');
        const f = $('#reader-progress-fill');
        f.style.width = '0%';
        f.classList.remove('retry');
        updateNavButtons();
    }

    // ===== UI-СЛУЖЕБНЫЕ =====
    function showStatus(msg, type = 'info', id = 'status-book') {
        const el = $('#' + id);
        if (!el) return;
        el.style.removeProperty('display');
        el.textContent = msg;
        el.className = 'nm-status ' + type;
    }
    function hideStatus(id = 'status-book') {
        const el = $('#' + id);
        if (el) { el.className = 'nm-status'; el.style.display = 'none'; }
    }
    function openModal() {
        modal.classList.add('active');
        dropdownMenu.classList.remove('active');
        refreshBookTab();
        updateGlossaryUI();
        loadSettings();
    }
    function closeModal() {
        modal.classList.remove('active');
        ['status-book', 'status-glossary', 'status-settings', 'status-backup'].forEach(hideStatus);
    }
    function openBookModal() {
        $('#book-modal-url').value = suggestBookKeyFromUrl();
        $('#book-modal-name').value = document.title.replace(/\s*[-–—|].*$/, '').trim();
        bookModal.classList.add('active');
    }



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

    // ===== ПОДСКАЗКИ =====
    // Объяснение принадлежит самому пункту настроек (строка с data-tip), а не отдельной
    // иконке: наводишь на пункт — появляется текст. Один плавающий тултип на весь шелл:
    // вложенный в модалку tooltip обрезался бы overflow скроллящегося тела, поэтому
    // fixed и ручной расчёт позиции.
    (function initTooltips() {
        const tip = document.createElement('div');
        tip.id = 'nm-tip';
        tip.setAttribute('role', 'tooltip');
        rootEl.appendChild(tip);
        const touchUI = matchMedia('(hover: none)');
        let owner = null;
        // событие могло прийти на вложенный label/input — цель строка с data-tip
        const ownerOf = (e) => {
            const path = typeof e.composedPath === 'function' ? e.composedPath() : [e.target];
            return path.find(n => n && n.dataset && n.dataset.tip) || null;
        };
        const show = (el) => {
            tip.textContent = el.dataset.tip;
            tip.classList.add('active');
            owner = el;
            const r = el.getBoundingClientRect();
            const w = tip.offsetWidth, h = tip.offsetHeight;
            const left = Math.max(8, Math.min(r.left + r.width / 2 - w / 2, innerWidth - w - 8));
            // под строкой, а не влезла — над ней (нижняя кромка экрана не режет текст)
            const top = r.bottom + 8 + h > innerHeight - 8 ? Math.max(8, r.top - h - 8) : r.bottom + 8;
            tip.style.left = `${left}px`;
            tip.style.top = `${top}px`;
        };
        const hide = () => { owner = null; tip.classList.remove('active'); };
        shadow.addEventListener('pointerover', (e) => { if (e.pointerType !== 'touch') { const t = ownerOf(e); if (t) show(t); } });
        shadow.addEventListener('pointerout', (e) => { if (e.pointerType !== 'touch' && owner === ownerOf(e)) hide(); });
        shadow.addEventListener('focusin', (e) => { const t = ownerOf(e); if (t) show(t); });
        shadow.addEventListener('focusout', hide);
        // на тач-устройстве фокус на поле не всегда приходит — тап по строке переключает
        shadow.addEventListener('click', (e) => {
            const t = ownerOf(e);
            if (t) { if (touchUI.matches) (owner === t ? hide : show)(t); return; }
            hide();
        });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
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
    $('#reader-progress-close').addEventListener('click', () => progressHide());
    $('#nm-extract-close').addEventListener('click', () => {
        if (!isTranslating) $('#nm-extract-popup').classList.remove('active');
    });

    // ===== ПРОГРЕСС =====
    // Панель живёт в двух состояниях: работа (кнопка «Отменить») и итог (кнопка
    // «Скрыть»). Отменённый или оборванный прогон обязан выглядеть итогом, который
    // закрывается одним нажатием, а не вечным «идёт работа» с мёртвой кнопкой.
    function progressState(done) {
        $('#reader-progress').classList.toggle('nm-rp-done', done);
        $('#reader-cancel').style.display = done ? 'none' : '';
        $('#reader-progress-close').style.display = done ? '' : 'none';
    }
    function progressShow(title) {
        if (!readerModeActive) openReaderShell(null);
        $('#reader-progress').classList.add('active');
        progressState(false);
        $('#reader-progress-title').textContent = title;
        $('#reader-progress-status').textContent = 'Подготовка...';
        const f = $('#reader-progress-fill');
        f.style.width = '0%';
        f.classList.remove('retry');
        updateNavButtons();
    }
    /** Финальное состояние панели: прогресс остаётся как есть, кнопка — «Скрыть». */
    function progressFinish(title, status) {
        if (!readerModeActive) openReaderShell(null);
        $('#reader-progress').classList.add('active');
        progressState(true);
        $('#reader-progress-title').textContent = title;
        $('#reader-progress-status').textContent = status;
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
        ['status-book', 'status-glossary', 'status-settings'].forEach(hideStatus);
    }
    function openBookModal() {
        $('#book-modal-url').value = suggestBookKeyFromUrl();
        $('#book-modal-name').value = document.title.replace(/\s*[-–—|].*$/, '').trim();
        bookModal.classList.add('active');
    }


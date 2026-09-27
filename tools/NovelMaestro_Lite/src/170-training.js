    // ===== ОБУЧЕНИЕ =====
    function trainingIgnore(e) {
        const path = typeof e.composedPath === 'function' ? e.composedPath() : [e.target];
        return path.includes(host);
    }
    function trainHighlight(el) {
        if (trainingHighlightedEl && trainingHighlightedEl !== el) trainingHighlightedEl.classList.remove('nm-training-highlight');
        trainingHighlightedEl = el;
        el.classList.add('nm-training-highlight');
    }
    function openTrainPopupFor(el) {
        trainingPopupTarget = el;
        const rect = el.getBoundingClientRect();
        const popupW = Math.min(250, window.innerWidth - 24), popupH = 250;
        let top = rect.bottom + 8;
        if (top + popupH > window.innerHeight) top = Math.max(8, rect.top - popupH - 8);
        let left = Math.min(Math.max(8, rect.left), window.innerWidth - popupW - 8);
        trainingPopup.style.top = top + 'px';
        trainingPopup.style.left = left + 'px';
        trainingPopup.classList.add('active');
    }
    function onTrainTouchStart(e) {
        if (!elementTrainingMode || trainingIgnore(e) || (e.touches && e.touches.length > 1)) return;
        trainTouchHandledAt = Date.now();
        const el = e.target;
        if (!el || el.nodeType !== 1) return;
        trainHighlight(el);
        trainTouchT = Date.now();
        const t = e.touches && e.touches[0];
        trainTouchXY = t ? [t.clientX, t.clientY] : null;
    }
    function onTrainTouchEnd(e) {
        if (!elementTrainingMode || trainingIgnore(e)) return;
        // отменённый touchend подавляет синтетический click и long-press-меню:
        // от тапов обучения сайт не реагирует (ссылки не открываются, выделения нет)
        e.preventDefault();
        e.stopPropagation();
        trainTouchHandledAt = Date.now();
        const el = trainingHighlightedEl || e.target;
        if (!el || el.nodeType !== 1) return;
        const t = e.changedTouches && e.changedTouches[0];
        // свайп (прокрутка) — не выбор элемента
        if (t && trainTouchXY && Math.hypot(t.clientX - trainTouchXY[0], t.clientY - trainTouchXY[1]) > 15) { trainTapEl = null; return; }
        if (Date.now() - trainTouchT >= LONG_PRESS_DELAY) {
            trainTapEl = null;
            openTrainPopupFor(el);
            return;
        }
        const now = Date.now();
        // двойной тап по тому же элементу (или его внутреннему) — выбор
        if (trainTapEl && (trainTapEl === el || trainTapEl.contains(el) || el.contains(trainTapEl)) && now - trainTapTime < DOUBLE_TAP_DELAY) {
            trainTapEl = null;
            openTrainPopupFor(el);
        } else {
            trainTapEl = el;
            trainTapTime = now;
        }
    }
    function onTrainTouchCancel() { trainTapEl = null; }
    function onTrainContextMenu(e) {
        if (elementTrainingMode) e.preventDefault();
    }
    function onTrainMouseOver(e) {
        if (!elementTrainingMode || trainingIgnore(e)) return;
        // на тач-устройствах тапы уже обработаны touch-конвейером (мышь-эмуляция идёт следом)
        if (Date.now() - trainTouchHandledAt < 800) return;
        const el = e.target;
        if (!el || el.nodeType !== 1 || el === document.documentElement) return;
        if (trainingHighlightedEl && trainingHighlightedEl !== el) trainingHighlightedEl.classList.remove('nm-training-highlight');
        trainingHighlightedEl = el;
        el.classList.add('nm-training-highlight');
    }
    function onTrainClick(e) {
        if (!elementTrainingMode || trainingIgnore(e)) return;
        e.preventDefault();
        e.stopPropagation();
        if (Date.now() - trainTouchHandledAt < 800) return;
        const el = trainingHighlightedEl || e.target;
        if (!el || el.nodeType !== 1) return;
        openTrainPopupFor(el);
    }
    function startElementTraining() {
        const cur = getCurrentBook();
        if (!cur) {
            pendingTranslateAfterTraining = false;
            alert('Сначала определите книгу');
            openBookModal();
            return;
        }
        elementTrainingMode = true;
        trainTapEl = null; trainTouchXY = null;
        const touchHint = shadow.querySelector('#nm-touch-hint');
        if (touchHint && window.matchMedia('(pointer: coarse)').matches) touchHint.style.display = 'inline';
        dropdownMenu.classList.remove('active');
        elementTraining.classList.add('active');
        trainingPopup.classList.remove('active');
        document.body.classList.add('nm-training-on');
        document.addEventListener('mouseover', onTrainMouseOver, true);
        document.addEventListener('click', onTrainClick, true);
        document.addEventListener('touchstart', onTrainTouchStart, true);
        document.addEventListener('touchend', onTrainTouchEnd, { capture: true, passive: false });
        document.addEventListener('touchcancel', onTrainTouchCancel, true);
        document.addEventListener('contextmenu', onTrainContextMenu, true);
    }
    function stopElementTraining() {
        elementTrainingMode = false;
        elementTraining.classList.remove('active');
        trainingPopup.classList.remove('active');
        document.removeEventListener('mouseover', onTrainMouseOver, true);
        document.removeEventListener('click', onTrainClick, true);
        document.removeEventListener('touchstart', onTrainTouchStart, true);
        document.removeEventListener('touchend', onTrainTouchEnd, true);
        document.removeEventListener('touchcancel', onTrainTouchCancel, true);
        document.removeEventListener('contextmenu', onTrainContextMenu, true);
        document.body.classList.remove('nm-training-on');
        if (trainingHighlightedEl) { trainingHighlightedEl.classList.remove('nm-training-highlight'); trainingHighlightedEl = null; }
        document.querySelectorAll('.nm-training-picked').forEach(el => el.classList.remove('nm-training-picked'));
        trainingPopupTarget = null;
        trainTapEl = null;
    }
    function generateCSSSelector(element) {
        if (element.id) return '#' + cssEsc(element.id);
        const classes = Array.from(element.classList);
        for (const cls of classes) {
            const sel = '.' + cssEsc(cls);
            try { if (document.querySelectorAll(sel).length === 1) return sel; } catch {}
        }
        const path = [];
        let current = element;
        while (current && current.nodeType === 1 && current !== document.body) {
            let selector = current.tagName.toLowerCase();
            if (current.id) { path.unshift('#' + cssEsc(current.id)); break; }
            const parent = current.parentElement;
            if (parent) {
                const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName);
                if (siblings.length > 1) selector += `:nth-of-type(${siblings.indexOf(current) + 1})`;
            }
            path.unshift(selector);
            current = parent;
        }
        return path.join(' > ');
    }
    function assignElementType(type) {
        if (!trainingPopupTarget) return;
        const cur = getCurrentBook();
        if (!cur) return;
        if (!cur.book.selectors) cur.book.selectors = {};
        cur.book.selectors[type] = elementSignature(trainingPopupTarget);
        GM_setValue('books', books);
        trainingPopupTarget.classList.remove('nm-training-highlight');
        trainingPopupTarget.classList.add('nm-training-picked');
        trainingPopup.classList.remove('active');
        trainingHighlightedEl = null;
        trainingPopupTarget = null;
    }
    function finishTraining() {
        stopElementTraining();
        const sel = getBookSelectors();
        if (pendingTranslateAfterTraining) {
            pendingTranslateAfterTraining = false;
            if (sel.content) runTranslationFlow(false);
            else alert('Не обучен блок текста — читалка не запущена.');
        }
    }


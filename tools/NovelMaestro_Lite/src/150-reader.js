    // ===== ЧИТАЛКА =====
    // Тема «как в системе» должна реагировать на смену системной сразу, без перезагрузки
    // страницы; MediaQueryList.addEventListener появился позже addListener.
    const systemDark = matchMedia('(prefers-color-scheme: dark)');
    function effectiveTheme() {
        return config.readerTheme === 'auto' ? (systemDark.matches ? 'dark' : 'light') : config.readerTheme;
    }
    function applyTheme() {
        const dark = effectiveTheme() === 'dark';
        readerMode.classList.remove('nm-reader-light', 'nm-reader-dark');
        readerMode.classList.add(dark ? 'nm-reader-dark' : 'nm-reader-light');
        rootEl.classList.toggle('nm-ui-dark', dark);
        readerContent.style.fontFamily = config.readerFontFamily;
        readerContent.style.fontSize = config.readerFontSize + 'px';
        readerContent.style.lineHeight = config.readerLineHeight;
        readerContent.style.setProperty('--nm-content-width', config.readerContentWidth + '%');
        let dyn = $('#nm-reader-dyn');
        if (!dyn) {
            dyn = document.createElement('style');
            dyn.id = 'nm-reader-dyn';
            shadow.appendChild(dyn);
        }
        dyn.textContent = `#nm-reader-mode .nm-reader-content p { margin: 0 0 ${config.readerParagraphSpacing}em 0; }`;
        const toggle = $('#reader-theme-toggle');
        if (toggle) {
            // режим прямо в надписи: при светлой системной переход «система→светлая»
            // визуально ничто, без надписи кажется, что кнопка не работает
            toggle.textContent = `🌓 Тема: ${THEME_MODE_LABELS[config.readerTheme] || config.readerTheme}`;
            toggle.title = 'Переключить: как в системе → тёмная → светлая';
        }
    }
    try {
        if (typeof systemDark.addEventListener === 'function') {
            systemDark.addEventListener('change', () => { if (config.readerTheme === 'auto') applyTheme(); });
        } else if (typeof systemDark.addListener === 'function') {
            systemDark.addListener(() => { if (config.readerTheme === 'auto') applyTheme(); });
        }
    } catch { /* браузер без media-слушателя — тема просто не обновится на лету */ }
    // читалка без перевода: пустой экран обязан объяснять состояние прогона, а не
    // выглядеть зависшим (именно так выглядела отмена для главы без единого чанка)
    function showReaderNotice(text) {
        const div = document.createElement('div');
        div.className = 'nm-reader-loading';
        div.textContent = text;
        readerContent.replaceChildren(div);
    }
    function openReaderShell(loadingText) {
        readerModeActive = true;
        readerMode.classList.add('active');
        buttonsBar.style.display = 'none';
        applyTheme();
        if (loadingText) showReaderNotice(loadingText);
        updateNavButtons();
    }
    function closeReader() {
        readerModeActive = false;
        readerMode.classList.remove('active');
        buttonsBar.style.display = '';
        readerState = null;
        // классов nm-ghost* на контенте читалки оставаться не должно: у следующей
        // главы призрак уже чужой (свежий текст и свои абзацы)
        ghostHide(false);
        $('#reader-retranslate').style.display = '';
        progressHide();
    }
    function updateNavButtons() {
        const busy = isTranslating;
        $('#reader-retranslate').disabled = busy;
        $('#reader-prev').disabled = busy || !(readerState && readerState.prevUrl);
        $('#reader-next').disabled = busy || !(readerState && readerState.nextUrl);
        $('#reader-toc').disabled = busy || !(readerState && readerState.tocUrl);
        $('#reader-prev').style.display = readerState && readerState.prevUrl ? '' : 'none';
        $('#reader-next').style.display = readerState && readerState.nextUrl ? '' : 'none';
        $('#reader-toc').style.display = readerState && readerState.tocUrl ? '' : 'none';
    }
    function setReaderState(data, rerender) {
        readerState = data;
        $('#reader-title').textContent = data.title || '';
        if (rerender) {
            // показ готового перевода отменяет призрак: он жил только вместо пустого экрана
            ghostHide(false);
            renderTranslationInto(readerContent, data.text);
            readerMode.scrollTop = 0;
        }
        updateNavButtons();
    }

    // ===== ПРИЗРАК ОРИГИНАЛА =====
    // Пока идёт извлечение терминов, читалка уже открыта, а показывать нечего: пустой
    // экран выглядит зависшим. Призрак — сам оригинал главы бледным текстом; он гаснет
    // по первому токену перевода (или когда призрак показать нечем). Он read-only:
    // ни в глоссарий, ни в книги не пишет и частот не трогает.
    const GHOST_MARK_LIMIT = 200;   // mark на весь прогон: больше — это уже не подсветка
    const GHOST_TERM_LIMIT = 20;    // вхождений одного термина в абзаце
    const GHOST_FADE_MS = 350;
    let ghostParas = [];
    let ghostRanges = [];   // диапазоны чанков: [startPara, endPara]
    let ghostActive = false;
    let ghostAutoScroll = true;
    let ghostFadeTimer = null;
    let ghostFrame = 0;
    let ghostPending = null;        // Set абзацев, ждущих перерисовки в кадре rAF
    let ghostMarkCount = 0;
    const ghostMarks = new Map();   // абзац → интервалы вспышек (отсортированы, без вложений)

    /**
     * Нормализованный текст и карта обратно в оригинал: строится ОДИН раз на абзац,
     * а не на каждый вариант термина — за прогон вариантов бывают сотни.
     */
    function normalizedMap(text) {
        const chars = [];
        const back = [];
        let i = 0;
        for (const ch of String(text)) {
            const piece = ch.normalize('NFC').toLowerCase().replace(SEARCH_DROP_RE, '');
            const at = i + ch.length;
            for (const c of piece) { chars.push(c); back.push([i, at]); }
            i = at;
        }
        return { norm: chars.join(''), back };
    }
    /** Интервалы вхождений термина и его алиасов; вложения схлопываются. */
    function termRanges(text, variants) {
        const { norm, back } = normalizedMap(text);
        if (!norm || !variants.length) return [];
        const spans = [];
        for (const v of variants) {
            const needle = normalize(v);
            if (!needle) continue;
            let from = 0, hits = 0;
            for (;;) {
                const at = norm.indexOf(needle, from);
                if (at < 0) break;
                spans.push([back[at][0], back[at + needle.length - 1][1]]);
                if (++hits >= GHOST_TERM_LIMIT) break;
                from = at + needle.length;
            }
        }
        spans.sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
        const out = [];
        for (const s of spans) {
            const last = out[out.length - 1];
            if (last && s[0] <= last[1]) { if (s[1] > last[1]) last[1] = s[1]; continue; }
            out.push(s);
        }
        return out;
    }
    function ghostLiving() {
        return ghostActive || ghostParas.length > 0 || !!ghostFadeTimer;
    }
    function clearGhost() {
        const had = ghostActive || ghostParas.length > 0;
        ghostActive = false;
        ghostParas = [];
        ghostRanges = [];
        ghostMarks.clear();
        ghostPending = null;
        ghostMarkCount = 0;
        readerContent.classList.remove('nm-ghost', 'nm-ghost-fade');
        // содержимое трогаем только если оно призрака: перевод в читалке — не его работа
        if (had) readerContent.replaceChildren();
    }
    /** Абзац с вспышками: детерминированная пересборка из текста и интервалов. */
    function ghostParagraphNode(text, ranges) {
        const frag = document.createDocumentFragment();
        let pos = 0;
        for (const [a, b] of ranges || []) {
            if (a < pos) continue;
            if (a > pos) frag.append(text.slice(pos, a));
            const mark = document.createElement('mark');
            mark.className = 'nm-hit-new';
            mark.textContent = text.slice(a, b);
            frag.append(mark);
            pos = b;
        }
        if (pos < text.length) frag.append(text.slice(pos));
        return frag;
    }
    const ghostParagraphs = () => readerContent.querySelectorAll('p');
    function renderGhostParagraph(idx) {
        const p = ghostParagraphs()[idx];
        if (p) p.replaceChildren(ghostParagraphNode(ghostParas[idx] || '', ghostMarks.get(idx)));
    }
    /** Фон на абзацах текущего чанка и автодогон к его началу. */
    function highlightGhostChunk(index) {
        const ps = ghostParagraphs();
        ps.forEach(p => p.classList.remove('nm-ghost-chunk'));
        const [a, b] = ghostRanges[index] || [index, index];
        for (let i = a; i <= b && i < ps.length; i++) ps[i].classList.add('nm-ghost-chunk');
        if (ghostAutoScroll && ps[a]) ps[a].scrollIntoView({ block: 'start', behavior: 'smooth' });
    }
    const ghostStopAutoScroll = () => { ghostAutoScroll = false; };
    /**
     * Призрак оригинала: читалка открыта, но перевода ещё нет — вместо пустого экрана
     * виден сам захваченный текст бледным. Показывать его решает вызывающий: призрак
     * включается только на время работы и уходит по первому токену перевода.
     */
    function ghostShow(text) {
        ghostAbort();
        if (!config.ghostPreview) return;
        const { chunks, ranges } = splitByNewlinesRanged(text, config.chunkSize);
        ghostParas = paragraphsOf(text);
        ghostRanges = ranges;
        ghostAutoScroll = true;
        readerContent.classList.add('nm-ghost');
        readerContent.replaceChildren(...ghostParas.map(p => {
            const el = document.createElement('p');
            el.textContent = p;
            return el;
        }));
        ghostActive = true;
        // ручной скролл выключает автодогон: полез дальше по тексту — призрак не тянет назад
        for (const ev of ['wheel', 'touchmove']) readerMode.addEventListener(ev, ghostStopAutoScroll, { passive: true });
        progressStatus(`📄 Захвачено: ${String(text).length} симв. • чанков: ${chunks.length}`);
    }
    /** Прогресс NER: сначала фон текущего чанка, затем вспышки терминов его диапазона. */
    function ghostProgress(st) {
        if (!ghostActive) return;
        // chunkDone — индекс чанка с нуля, приходит только на успешном чанке:
        // проверка на число, а не на истинность (нулевой чанк — не «пустой»)
        if (Number.isInteger(st.chunkDone)) {
            highlightGhostChunk(st.chunkDone);
            const variants = [];
            for (const t of (st.matched || [])) variants.push(t.term, ...(t.aliases || []));
            for (const t of (st.fresh || [])) variants.push(t.term, ...(t.aliases || []));
            if (variants.length && st.range) ghostQueueMarks(st.range, variants);
        }
        if (ghostPending && !ghostFrame) {
            ghostFrame = requestAnimationFrame(() => {
                ghostFrame = 0;
                const byPara = ghostPending;
                ghostPending = null;
                if (!ghostActive || !byPara) return;
                for (const idx of byPara) renderGhostParagraph(idx);
            });
        }
    }
    /**
     * Вспышки копятся до кадра rAF (чанков за секунду бывает несколько), повторное
     * вхождение уже вспыхнувшего интервала — это тихо: Set держит сами интервалы.
     */
    function ghostQueueMarks(range, variants) {
        for (let i = Math.max(0, range[0]); i <= range[1] && i < ghostParas.length; i++) {
            const found = termRanges(ghostParas[i], variants);
            if (!found.length) continue;
            const current = ghostMarks.get(i) || [];
            let changed = false;
            for (const span of found) {
                if (ghostMarkCount >= GHOST_MARK_LIMIT) break;
                const key = `${span[0]}-${span[1]}`;
                if (current.some(r => `${r[0]}-${r[1]}` === key)) continue;
                current.push(span);
                ghostMarkCount++;
                changed = true;
            }
            if (!changed) continue;
            current.sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
            ghostMarks.set(i, current);
            if (!ghostPending) ghostPending = new Set();
            ghostPending.add(i);
        }
    }
    function ghostHide(fade) {
        ghostAbort();
        for (const ev of ['wheel', 'touchmove']) readerMode.removeEventListener(ev, ghostStopAutoScroll);
        if (!ghostLiving()) { clearGhost(); return; }
        if (!fade) { clearGhost(); return; }
        readerContent.classList.add('nm-ghost-fade');
        ghostFadeTimer = setTimeout(() => {
            ghostFadeTimer = null;
            clearGhost();
        }, GHOST_FADE_MS);
    }
    /** Снять незавершённую работу призрака, не трогая его самого (страховка перед новым прогоном). */
    function ghostAbort() {
        if (ghostFrame) { cancelAnimationFrame(ghostFrame); ghostFrame = 0; }
        ghostPending = null;
        if (ghostFadeTimer) { clearTimeout(ghostFadeTimer); ghostFadeTimer = null; }
        readerContent.classList.remove('nm-ghost-fade');
    }

    // новую вкладку открываем временным якорем с rel=noopener, а не window.open:
    // переход по главам остаётся той же ссылкой того же сайта, без навигации текущей страницы
    function openExternalTab(href) {
        const a = document.createElement('a');
        a.href = href;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        document.body.append(a);
        a.click();
        a.remove();
    }
    function gotoChapter(url) {
        if (!url || isTranslating) return;
        let target = null;
        try { target = new URL(url, location.href); } catch { return; }
        if (target.protocol !== 'http:' && target.protocol !== 'https:') return;
        // навигация читалки — только по этому же сайту; чужой хост открываем новой вкладкой
        if (target.hostname !== location.hostname) { openExternalTab(target.href); return; }
        sessionStorage.setItem('nm_auto_reader', '1');
        location.assign(target.href);
    }
    // автоперевод одной следующей главы в фоне (чекбокс в настройках);
    // уже закешированная глава пропускается, петля последней главы — тоже
    async function pretranslateNext(nextUrl) {
        if (!nextUrl || !config.preemptiveTranslation) return;
        if (preemptiveRunning.has(nextUrl) || cacheGet(nextUrl)) return;
        const cur = getCurrentBook();
        const sel = getBookSelectors();
        if (!cur || !sel.content) return;
        preemptiveRunning.add(nextUrl);
        const statusBtn = $('#reader-preload-status');
        try {
            statusBtn.style.display = '';
            statusBtn.textContent = '⏳ Следующая глава переводится в фоне…';
            const resp = await customFetch(nextUrl, {}, false);
            const html = await resp.text();
            const doc = new DOMParser().parseFromString(html, 'text/html');
            const text = extractTextFromDoc(doc, sel.content);
            if (!text.trim()) throw new Error('Не найден текст в следующей главе');
            const translated = await translateTextBackground(text, { bookKey: cur.key, url: nextUrl });
            cacheSet(nextUrl, {
                url: nextUrl,
                title: doc.title || '',
                text: translated,
                nextUrl: resolveNavHref(doc, sel.next, nextUrl, 'next'),
                prevUrl: resolveNavHref(doc, sel.prev, nextUrl, 'prev'),
                tocUrl: resolveNavHref(doc, sel.toc, nextUrl, 'toc')
            }, cur.key);
            statusBtn.textContent = '✅ Следующая глава готова';
            setTimeout(() => { statusBtn.style.display = 'none'; }, 4000);
        } catch (e) {
            console.warn('[NovelMaestro] Автоперевод:', e.message);
            statusBtn.style.display = 'none';
        } finally {
            preemptiveRunning.delete(nextUrl);
        }
    }


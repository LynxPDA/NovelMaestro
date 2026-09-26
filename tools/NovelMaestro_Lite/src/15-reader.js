    // ===== ЧИТАЛКА =====
    function applyTheme() {
        const dark = config.readerTheme === 'dark';
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
    }
    function openReaderShell(loadingText) {
        readerModeActive = true;
        readerMode.classList.add('active');
        buttonsBar.style.display = 'none';
        applyTheme();
        if (loadingText) {
            const div = document.createElement('div');
            div.className = 'nm-reader-loading';
            div.textContent = loadingText;
            readerContent.replaceChildren(div);
        }
        updateNavButtons();
    }
    function closeReader() {
        readerModeActive = false;
        readerMode.classList.remove('active');
        buttonsBar.style.display = '';
        readerState = null;
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
            renderTranslationInto(readerContent, data.text);
            readerMode.scrollTop = 0;
        }
        updateNavButtons();
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
            const translated = await translateTextBackground(text);
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


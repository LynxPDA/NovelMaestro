    // ===== КНИГА: ОБЛОЖКА И ЭКСПОРТ TXT =====
    function findCoverCandidates() {
        const candidates = new Set();
        const add = (u) => { try { const abs = new URL(u, location.href).href; if (/^https?:/i.test(abs)) candidates.add(abs); } catch {} };
        const meta = document.querySelector('meta[property="og:image"], meta[name="og:image"], meta[property="twitter:image"], meta[property="twitter:image:src"]');
        if (meta && meta.content) add(meta.content);
        document.querySelectorAll('link[rel="image_src"]').forEach(l => { if (l.href) add(l.href); });
        const seen = new Set();
        const scan = (root) => {
            if (!root || !root.querySelectorAll) return;
            root.querySelectorAll('img').forEach(img => {
                const src = img.currentSrc || img.src;
                if (!src || seen.has(src)) return;
                // layout-размер (атрибуты) честнее natural: lazy-load картинки часто 1x1-заглушки
                const w = img.width || img.naturalWidth || 0, h = img.height || img.naturalHeight || 0;
                // обложка обычно вертикальная; lazy-load картинки без размеров тоже проускаем
                if ((w >= 120 && h >= 160) || (!w && !h)) { seen.add(src); add(src); }
            });
        };
        scan(findContentElement());
        document.querySelectorAll('[class*="cover"], [id*="cover"]').forEach(el => scan(el));
        return [...candidates].slice(0, 12);
    }
    // экспорт TXT — текущая переведённая страница: её кэшированный перевод
    function exportChapterToTxt() {
        const data = cacheGet(pageCacheKey());
        if (!data || !data.text) { showStatus('Текущая страница ещё не переведена — нечего экспортировать', 'error', 'status-book'); return; }
        const bookName = (currentBookKey && books[currentBookKey] && books[currentBookKey].name) || 'chapter';
        const title = String(data.title || document.title || '').trim();
        let fullText = `«${bookName}»${title ? ` — ${title}` : ''}\n${'='.repeat(60)}\n\n${data.text}\n`;
        const blob = new Blob([fullText], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${String(bookName).replace(/[^\wа-яА-ЯёЁ \-]/g, '').trim().slice(0, 60) || 'book'}_translated.txt`;
        a.click();
        URL.revokeObjectURL(url);
        showStatus('TXT экспортирован: текущая переведённая страница', 'success', 'status-book');
    }

    // ===== ГЛОССАРИЙ CRUD =====
    function addTerm() {
        const term = $('#new-term').value.trim();
        const translation = $('#new-translation').value.trim();
        if (!term || !translation) { showStatus('Заполните термин и перевод', 'error', 'status-glossary'); return; }
        const glossary = getGlossaryForView();
        for (const ex of Object.values(glossary)) {
            if (normalize(ex.term) === normalize(term) || termMatchesText(ex.term, term, config.fuzzySearchThreshold)) {
                showStatus(`Похожий термин уже есть: "${ex.term}"`, 'error', 'status-glossary');
                return;
            }
        }
        glossary[`${normalize(term)}_${Date.now()}`] = { term, translation, type: $('#new-type').value.trim() || 'Term', count: 1 };
        saveGlossary(glossary);
        $('#new-term').value = '';
        $('#new-translation').value = '';
        glossaryPage = 0;
        glossarySort = { field: 'count', dir: 'desc' };
        updateGlossaryUI();
        showStatus('Термин добавлен!', 'success', 'status-glossary');
    }
    function importGlossary() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.json';
        input.onchange = e => {
            const reader = new FileReader();
            reader.onload = ev => {
                try {
                    const imported = JSON.parse(ev.target.result);
                    const srcList = Array.isArray(imported) ? imported : Object.entries(imported).map(([, v]) => v);
                    const glossary = getGlossaryForView();
                    let added = 0, incremented = 0;
                    for (const raw of srcList) {
                        const t = migrateEntry({ ...raw });
                        if (!t || !t.term || !t.translation) continue;
                        let existingId = null;
                        for (const [exId, ex] of Object.entries(glossary)) {
                            if (normalize(ex.term) === normalize(t.term) || termMatchesText(ex.term, t.term, config.fuzzySearchThreshold)) { existingId = exId; break; }
                        }
                        const importedCount = parseInt(t.count, 10);
                        const cnt = Number.isFinite(importedCount) && importedCount > 0 ? importedCount : 1;
                        if (existingId) { glossary[existingId].count = (glossary[existingId].count || 0) + cnt; incremented++; }
                        else { glossary[`${normalize(t.term)}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`] = { ...t, count: cnt }; added++; }
                    }
                    saveGlossary(glossary);
                    updateGlossaryUI();
                    showStatus(`Импортировано ${added} новых, обновлено частот: ${incremented}`, 'success', 'status-glossary');
                } catch (err) { showStatus('Ошибка файла: ' + err.message, 'error', 'status-glossary'); }
            };
            reader.readAsText(e.target.files[0]);
        };
        input.click();
    }
    function exportGlossary() {
        const glossary = getGlossaryForView();
        const name = (currentBookKey && books[currentBookKey] && books[currentBookKey].name) || 'book';
        const blob = new Blob([JSON.stringify(glossary, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `glossary-${name}-${new Date().toISOString().slice(0, 10)}.json`;
        a.click();
        URL.revokeObjectURL(url);
        showStatus('Глоссарий экспортирован!', 'success', 'status-glossary');
    }
    function clearGlossary() {
        const bookKey = currentBookKey;
        if (!bookKey || !books[bookKey]) return;
        if (!confirm(`Очистить глоссарий книги "${books[bookKey].name || bookKey}"? Кэш страниц с извлечёнными терминами тоже будет очищен.`)) return;
        saveGlossary({});
        clearNerCache(bookKey);
        glossaryPage = 0;
        updateGlossaryUI();
        refreshBookTab();
        showStatus('Глоссарий очищен', 'success', 'status-glossary');
    }


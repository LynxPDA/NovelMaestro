    // ===== ВКЛАДКА "КНИГА" =====
    function refreshBookTab() {
        const select = $('#book-select');
        const keys = Object.keys(books);
        if (!managedBookKey || !books[managedBookKey]) managedBookKey = currentBookKey || keys[0] || null;
        select.innerHTML = '';
        if (keys.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = '(нет сохранённых книг)';
            select.appendChild(opt);
        } else {
            for (const key of keys) {
                const opt = document.createElement('option');
                opt.value = key;
                opt.textContent = `${books[key].name || key}${key === currentBookKey ? '  (текущая страница)' : ''}`;
                select.appendChild(opt);
            }
            select.value = managedBookKey;
        }
        renderBookManageArea();
    }
    function renderBookManageArea() {
        const area = $('#book-manage-area');
        const key = managedBookKey;
        if (!key || !books[key]) {
            const help = document.createElement('div');
            help.className = 'nm-help';
            help.textContent = '⚠️ Книг пока нет — откройте страницу книги (она определяется по заданному URL) и привяжите её.';
            const btn = document.createElement('button');
            btn.className = 'nm-btn nm-btn-primary';
            btn.textContent = '📚 Привязать текущую страницу к книге';
            btn.addEventListener('click', openBookModal);
            area.replaceChildren(help, btn);
            return;
        }
        const book = books[key];
        const terms = Object.keys(siteGlossaries[key] || {}).length;
        const nerPages = Object.keys(siteNerDone[key] || {}).length;
        const cachedCount = cacheBookChapterCount(key);
        const info = document.createElement('div');
        info.className = 'nm-book-info';
        const strong = document.createElement('strong');
        strong.textContent = `📖 ${book.name || 'Без названия'}`;
        const urlLine = document.createElement('small');
        urlLine.setAttribute('style', 'color:#6b7280;display:block;');
        urlLine.textContent = `URL: ${key}`;
        const statsLine = document.createElement('small');
        statsLine.setAttribute('style', 'color:#6b7280;display:block;');
        statsLine.textContent = `Терминов: ${terms} | Страниц с извлечёнными терминами: ${nerPages} | Переведённых глав в кэше: ${cachedCount}`;
        const sel = book.selectors || {};
        const trained = [];
        if (sel.content) trained.push('📄 текст');
        if (sel.prev) trained.push('← назад');
        if (sel.next) trained.push('→ вперёд');
        if (sel.toc) trained.push('☰ оглавление');
        const selLine = document.createElement('small');
        selLine.setAttribute('style', `display:block;margin-top:6px;color:${trained.length ? '#059669' : '#b45309'};`);
        selLine.textContent = trained.length ? `Обучено (эвристика на всю книгу): ${trained.join(', ')}` : '⚠️ Элементы не обучены — читалка предложит обучение';
        info.append(strong, urlLine, statsLine, selLine);
        const mkGroup = (labelText, inputId, value) => {
            const group = document.createElement('div');
            group.className = 'nm-input-group';
            const label = document.createElement('label');
            label.textContent = labelText;
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'nm-input';
            input.id = inputId;
            input.value = value;
            group.append(label, input);
            return group;
        };
        // обложка: URL-поле, поиск картинок на странице, предпросмотр и кандидаты
        const coverGroup = document.createElement('div');
        coverGroup.className = 'nm-input-group';
        const coverLabel = document.createElement('label');
        coverLabel.textContent = 'Обложка книги (URL):';
        const coverRow = document.createElement('div');
        coverRow.className = 'nm-url-edit';
        const coverInput = document.createElement('input');
        coverInput.type = 'text';
        coverInput.className = 'nm-input';
        coverInput.id = 'book-cover-edit';
        coverInput.placeholder = 'https://…/cover.jpg';
        coverInput.value = book.coverUrl || '';
        const findCoversBtn = document.createElement('button');
        findCoversBtn.className = 'nm-btn nm-btn-sm nm-btn-secondary';
        findCoversBtn.id = 'btn-find-covers';
        findCoversBtn.textContent = '🖼 Найти на странице';
        coverRow.append(coverInput, findCoversBtn);
        const coverPreview = document.createElement('img');
        coverPreview.id = 'cover-preview';
        coverPreview.setAttribute('style', 'max-width:120px;max-height:180px;border-radius:6px;border:1px solid #d1d5db;margin-top:8px;display:none;');
        // битая ссылка на обложку не должна показывать пустую рамку
        coverPreview.addEventListener('error', () => { coverPreview.style.display = 'none'; });
        if (book.coverUrl) { coverPreview.src = book.coverUrl; coverPreview.style.display = 'block'; }
        const coverCandidates = document.createElement('div');
        coverCandidates.id = 'cover-candidates';
        coverCandidates.setAttribute('style', 'display:none;gap:8px;flex-wrap:wrap;margin-top:8px;');
        coverGroup.append(coverLabel, coverRow, coverPreview, coverCandidates);
        findCoversBtn.addEventListener('click', () => {
            const found = findCoverCandidates();
            if (!found.length) {
                const none = document.createElement('small');
                none.setAttribute('style', 'color:#6b7280;');
                none.textContent = 'На странице не найдено картинок-кандидатов';
                coverCandidates.replaceChildren(none);
            } else {
                coverCandidates.replaceChildren(...found.map(cu => {
                    const img = document.createElement('img');
                    img.src = cu;
                    img.title = cu;
                    img.setAttribute('style', 'max-width:80px;max-height:120px;border-radius:4px;cursor:pointer;border:2px solid transparent;');
                    img.addEventListener('click', () => {
                        coverInput.value = cu;
                        coverPreview.src = cu;
                        coverPreview.style.display = 'block';
                    });
                    return img;
                }));
            }
            coverCandidates.style.display = 'flex';
        });
        coverInput.addEventListener('input', () => {
            const v = coverInput.value.trim();
            if (v) { coverPreview.src = v; coverPreview.style.display = 'block'; }
            else coverPreview.style.display = 'none';
        });
        const saveBtn = document.createElement('button');
        saveBtn.className = 'nm-btn nm-btn-primary';
        saveBtn.id = 'btn-save-book';
        saveBtn.textContent = '💾 Сохранить';
        const exportTxtBtn = document.createElement('button');
        exportTxtBtn.className = 'nm-btn nm-btn-secondary';
        exportTxtBtn.id = 'btn-export-txt';
        exportTxtBtn.textContent = '📄 Экспорт TXT';
        exportTxtBtn.disabled = cachedCount === 0;
        const openSiteBtn = document.createElement('button');
        openSiteBtn.className = 'nm-btn nm-btn-secondary';
        openSiteBtn.id = 'btn-open-site';
        openSiteBtn.textContent = '🔗 Открыть на сайте';
        const delBtn = document.createElement('button');
        delBtn.className = 'nm-btn nm-btn-danger';
        delBtn.id = 'btn-delete-book';
        delBtn.textContent = '🗑 Удалить книгу';
        area.replaceChildren(info,
            mkGroup('Название книги:', 'book-name-edit', book.name || ''),
            mkGroup('URL книги:', 'book-key-edit', key),
            coverGroup, saveBtn, exportTxtBtn, openSiteBtn, delBtn);
        exportTxtBtn.title = 'Экспортирует кэшированные переводы глав (текущая и следующая) в TXT';
        // экспорт TXT — перевод текущей страницы; на чужом origin для книги нечего экспортировать
        exportTxtBtn.title = 'Экспортирует перевод текущей страницы в TXT';
        const cd = key === currentBookKey ? cacheGet(pageCacheKey()) : null;
        exportTxtBtn.disabled = !(cd && cd.text);
        exportTxtBtn.addEventListener('click', exportChapterToTxt);
        openSiteBtn.title = 'Оглавление (если обучено) или последняя переведённая глава';
        openSiteBtn.addEventListener('click', () => {
            // адрес открытия хранится в записи книги: выученное оглавление, иначе
            // последняя переведённая глава; если переводов ещё не было — URL книги
            const target = book.openUrl || (/^https?:/i.test(key) ? key : '');
            if (target) openExternalTab(target);
        });
        $('#btn-save-book').addEventListener('click', () => {
            const newName = $('#book-name-edit').value.trim();
            const newKey = $('#book-key-edit').value.trim();
            if (!newKey) { showStatus('URL не может быть пустым', 'error', 'status-book'); return; }
            const cover = coverInput.value.trim();
            if (newKey === key) { book.name = newName; book.coverUrl = cover; }
            else {
                // данные книги привязаны к URL-ключу в IndexedDB этого сайта — переносим
                for (const [p, store] of [['g/', siteGlossaries], ['n/', siteNerDone], ['c/', siteChapterCache]]) {
                    if (key in store) { store[newKey] = store[key]; delete store[key]; dbPut(p + newKey, store[newKey]); }
                    dbDelete(p + key);
                }
                books[newKey] = { ...book, name: newName, coverUrl: cover };
                delete books[key];
                if (currentBookKey === key) currentBookKey = newKey;
                managedBookKey = newKey;
            }
            GM_setValue('books', books);
            showStatus('Сохранено!', 'success', 'status-book');
            refreshBookTab();
            updateGlossaryUI();
        });
        $('#btn-delete-book').addEventListener('click', () => {
            if (confirm(`Удалить книгу "${books[key].name || key}" из списка? Её глоссарий, кэш извлечения и кэш переводов глав в памяти этого браузера тоже будут удалены.`)) {
                cacheClearBook(key);
                delete siteGlossaries[key];
                delete siteNerDone[key];
                dbDelete('g/' + key);
                dbDelete('n/' + key);
                delete books[key];
                if (currentBookKey === key) currentBookKey = null;
                managedBookKey = null;
                GM_setValue('books', books);
                showStatus('Книга удалена', 'success', 'status-book');
                refreshBookTab();
                updateGlossaryUI();
            }
        });
    }


    // ===== ВКЛАДКА "ГЛОССАРИЙ" =====
    // Глоссарий только для книги текущей страницы: вне страницы книги — подсказка вместо формы.
    function sortGlossaryEntries(entries) {
        const { field, dir } = glossarySort;
        if (!field || !dir) return entries;
        const sign = dir === 'desc' ? -1 : 1;
        const valueOf = (e) => field === 'gender' ? genderOf(e[1].type) : e[1][field];
        return [...entries].sort((a, b) => {
            const va = valueOf(a), vb = valueOf(b);
            if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * sign;
            return String(va ?? '').toLowerCase().localeCompare(String(vb ?? '').toLowerCase()) * sign;
        });
    }
    function showGlossaryPlaceholder(container, text) {
        const p = document.createElement('p');
        p.setAttribute('style', 'color:#6b7280;text-align:center;padding:20px;');
        p.textContent = text;
        container.replaceChildren(p);
    }
    function updateGlossaryUI() {
        const noBook = !currentBookKey || !books[currentBookKey];
        $('#glossary-book-hint').style.display = noBook ? '' : 'none';
        $('#glossary-body').style.display = noBook ? 'none' : '';
        if (noBook) return;
        const container = $('#glossary-list');
        const pagination = $('#glossary-pagination');
        const glossary = getGlossaryForView();
        updateTypeDatalist();
        let entries = Object.entries(glossary);
        if (glossaryFilter) {
            const f = normalize(glossaryFilter);
            entries = entries.filter(([, t]) =>
                normalize(t.term).includes(f) || normalize(t.translation).includes(f) || normalize(t.type || '').includes(f));
        }
        entries = sortGlossaryEntries(entries);
        const totalPages = Math.max(1, Math.ceil(entries.length / PAGE_SIZE));
        if (glossaryPage >= totalPages) glossaryPage = totalPages - 1;
        if (glossaryPage < 0) glossaryPage = 0;
        const start = glossaryPage * PAGE_SIZE;
        const pageEntries = entries.slice(start, start + PAGE_SIZE);
        $('#glossary-count').textContent = Object.keys(glossary).length;
        if (Object.keys(glossary).length === 0) { showGlossaryPlaceholder(container, 'Глоссарий пуст'); pagination.replaceChildren(); return; }
        if (entries.length === 0) { showGlossaryPlaceholder(container, 'Ничего не найдено по фильтру'); pagination.replaceChildren(); return; }
        const sortIcon = (field) => glossarySort.field !== field ? '↕' : (glossarySort.dir === 'asc' ? '↑' : '↓');
        const activeClass = (field) => glossarySort.field === field ? 'active-sort' : '';
        const table = document.createElement('table');
        table.className = 'nm-glossary-table';
        const thead = document.createElement('thead');
        const hrow = document.createElement('tr');
        const makeTh = (label, field, extraClass, extraStyle) => {
            const th = document.createElement('th');
            if (field) th.dataset.sort = field;
            if (extraClass) th.className = extraClass;
            if (extraStyle) th.setAttribute('style', extraStyle);
            th.append(`${label} `);
            if (field) {
                const icon = document.createElement('span');
                icon.className = 'nm-sort';
                icon.textContent = sortIcon(field);
                th.appendChild(icon);
            }
            return th;
        };
        hrow.append(
            makeTh('Термин', 'term', activeClass('term')),
            makeTh('Перевод', 'translation', activeClass('translation')),
            makeTh('Тип', 'type', activeClass('type')),
            makeTh('Пол', 'gender', activeClass('gender')),
            makeTh('Частота', 'count', activeClass('count'), 'text-align:center;'),
            makeTh('Действия', null, null, 'width:60px;')
        );
        thead.appendChild(hrow);
        table.appendChild(thead);
        const tbody = document.createElement('tbody');
        for (const [id, t] of pageEntries) {
            const count = t.count || 0;
            const countClass = count >= 5 ? 'high' : count >= 2 ? 'med' : '';
            const gender = genderOf(t.type);
            const tr = document.createElement('tr');
            tr.dataset.id = id;
            const makeCell = (value, field, list) => {
                const td = document.createElement('td');
                const inp = document.createElement('input');
                inp.type = 'text';
                inp.value = value;
                inp.dataset.field = field;
                if (list) inp.setAttribute('list', list);
                td.appendChild(inp);
                return td;
            };
            tr.appendChild(makeCell(t.term, 'term'));
            tr.appendChild(makeCell(t.translation, 'translation'));
            tr.appendChild(makeCell(t.type || '', 'type', 'nm-type-list'));
            const gTd = document.createElement('td');
            const gSel = document.createElement('select');
            gSel.dataset.field = 'gender';
            gSel.title = 'Пол персонажа — хранится внутри типа';
            for (const [val, label] of [['', '—'], ['male', '♂ муж.'], ['female', '♀ жен.'], ['unknown', '⚖ неопр.']]) {
                const opt = document.createElement('option');
                opt.value = val;
                opt.textContent = label;
                if (gender === val) opt.selected = true;
                gSel.appendChild(opt);
            }
            gTd.appendChild(gSel);
            tr.appendChild(gTd);
            const cTd = document.createElement('td');
            cTd.className = `nm-count-cell ${countClass}`;
            cTd.textContent = count;
            tr.appendChild(cTd);
            const dTd = document.createElement('td');
            const del = document.createElement('button');
            del.className = 'nm-delete-cell';
            del.title = 'Удалить';
            del.textContent = '✕';
            dTd.appendChild(del);
            tr.appendChild(dTd);
            tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        container.replaceChildren(table);
        container.querySelectorAll('th[data-sort]').forEach(th => {
            th.addEventListener('click', () => {
                const field = th.dataset.sort;
                if (glossarySort.field === field) {
                    if (glossarySort.dir === 'desc') glossarySort.dir = 'asc';
                    else { glossarySort.field = 'count'; glossarySort.dir = 'desc'; }
                } else { glossarySort.field = field; glossarySort.dir = 'desc'; }
                glossaryPage = 0;
                updateGlossaryUI();
            });
        });
        container.querySelectorAll('tr[data-id]').forEach(row => {
            const id = row.dataset.id;
            row.querySelectorAll('input, select').forEach(inp => {
                inp.addEventListener('change', function() {
                    const field = this.dataset.field;
                    const g = getGlossaryForView();
                    if (!g[id]) return;
                    if (field === 'gender') {
                        g[id].type = withGender(g[id].type, this.value);
                        const tInp = row.querySelector('input[data-field="type"]');
                        if (tInp) tInp.value = g[id].type;
                    } else {
                        g[id][field] = this.value.trim();
                        if (field === 'type') {
                            const gSel = row.querySelector('select[data-field="gender"]');
                            if (gSel) gSel.value = genderOf(g[id].type);
                        }
                    }
                    saveGlossary(g);
                    updateGlossaryUI();
                });
            });
            row.querySelector('.nm-delete-cell').addEventListener('click', () => {
                const g = getGlossaryForView();
                const termName = g[id] ? g[id].term : id;
                if (confirm(`Удалить термин "${termName}"?`)) {
                    delete g[id];
                    saveGlossary(g);
                    updateGlossaryUI();
                }
            });
        });
        renderPagination(pagination, totalPages, entries.length);
    }
    function renderPagination(container, totalPages, totalItems) {
        const pageInfo = (text) => {
            const span = document.createElement('span');
            span.className = 'nm-page-info';
            span.textContent = text;
            return span;
        };
        if (totalPages <= 1) { container.replaceChildren(pageInfo(`Всего: ${totalItems}`)); return; }
        const maxVisiblePages = 7;
        const pages = [];
        if (totalPages <= maxVisiblePages) {
            for (let i = 0; i < totalPages; i++) pages.push(i);
        } else {
            pages.push(0);
            const start = Math.max(1, glossaryPage - 1);
            const end = Math.min(totalPages - 2, glossaryPage + 1);
            if (start > 1) pages.push(-1);
            for (let i = start; i <= end; i++) pages.push(i);
            if (end < totalPages - 2) pages.push(-1);
            pages.push(totalPages - 1);
        }
        const frag = document.createDocumentFragment();
        const navBtn = (label, cls, disabled) => {
            const b = document.createElement('button');
            b.textContent = label;
            b.className = cls;
            b.disabled = disabled;
            return b;
        };
        frag.appendChild(navBtn('‹', 'nm-prev-btn', glossaryPage === 0));
        for (const p of pages) {
            if (p === -1) {
                const dots = document.createElement('span');
                dots.setAttribute('style', 'padding:0 4px;color:#9ca3af;');
                dots.textContent = '…';
                frag.appendChild(dots);
            } else {
                const b = document.createElement('button');
                b.className = 'nm-page-btn' + (p === glossaryPage ? ' active' : '');
                b.dataset.page = p;
                b.textContent = p + 1;
                frag.appendChild(b);
            }
        }
        frag.appendChild(navBtn('›', 'nm-next-btn', glossaryPage === totalPages - 1));
        frag.appendChild(pageInfo(`Стр. ${glossaryPage + 1} из ${totalPages} • Всего: ${totalItems}`));
        container.replaceChildren(frag);
        container.querySelector('.nm-prev-btn').addEventListener('click', () => { if (glossaryPage > 0) { glossaryPage--; updateGlossaryUI(); } });
        container.querySelector('.nm-next-btn').addEventListener('click', () => { if (glossaryPage < totalPages - 1) { glossaryPage++; updateGlossaryUI(); } });
        container.querySelectorAll('.nm-page-btn').forEach(btn => {
            btn.addEventListener('click', () => { glossaryPage = parseInt(btn.dataset.page); updateGlossaryUI(); });
        });
    }


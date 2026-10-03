    // ===== СИГНАТУРЫ ЭЛЕМЕНТОВ =====
    function cssEsc(s) { try { return CSS.escape(s); } catch { return s; } }
    function elementSignature(el) {
        const sig = {
            sel: generateCSSSelector(el),
            tag: el.tagName.toLowerCase(),
            id: el.id || null,
            classes: Array.from(el.classList).slice(0, 6),
            ancestors: []
        };
        let cur = el.parentElement;
        for (let i = 0; i < 4 && cur && cur !== document.body; i++) {
            sig.ancestors.push({ tag: cur.tagName.toLowerCase(), id: cur.id || null, classes: Array.from(cur.classList).slice(0, 6) });
            cur = cur.parentElement;
        }
        return sig;
    }
    function sigOwnSelector(sig) {
        if (sig.id) return `${sig.tag}#${cssEsc(sig.id)}`;
        if (sig.classes && sig.classes.length) return sig.tag + '.' + sig.classes.map(cssEsc).join('.');
        return sig.tag;
    }
    function sigAncestorPart(a) {
        if (a.id) return `${a.tag}#${cssEsc(a.id)}`;
        if (a.classes && a.classes.length) return a.tag + '.' + a.classes.map(cssEsc).join('.');
        return a.tag;
    }
    function tryQuery(root, sel) {
        try { return root.querySelector(sel); } catch { return null; }
    }
    function tryQueryAll(root, sel) {
        try { return Array.from(root.querySelectorAll(sel)); } catch { return []; }
    }
    function findBySignature(root, sig) {
        if (!sig) return null;
        if (typeof sig === 'string') return tryQuery(root, sig);
        if (sig.sel) { const el = tryQuery(root, sig.sel); if (el) return el; }
        const own = sigOwnSelector(sig);
        const el2 = tryQuery(root, own);
        if (el2) return el2;
        if (sig.ancestors && sig.ancestors.length) {
            const chain = sig.ancestors.slice().reverse().map(sigAncestorPart);
            for (let drop = 0; drop < chain.length; drop++) {
                const el3 = tryQuery(root, chain.slice(drop).concat([own]).join(' '));
                if (el3) return el3;
            }
        }
        if (sig.classes && sig.classes.length) {
            const el4 = tryQuery(root, '.' + sig.classes.map(cssEsc).join('.'));
            if (el4) return el4;
        }
        return null;
    }
    function findContainerByAncestors(root, sig) {
        if (!sig || typeof sig === 'string' || !sig.ancestors || !sig.ancestors.length) return null;
        const chain = sig.ancestors.slice().reverse().map(sigAncestorPart);
        for (let drop = 0; drop < chain.length; drop++) {
            const el = tryQuery(root, chain.slice(drop).join(' '));
            if (el) return el;
        }
        return null;
    }
    function textLen(el) {
        if (!el) return 0;
        return (el.innerText || el.textContent || '').length;
    }

    // ===== ИЗВЛЕЧЕНИЕ КОНТЕНТА =====
    const GENERIC_CONTENT_SELECTORS = [
        '.chapter-content', '.chapter-inner', '.txtnav', '.txt-content', '#txt-content',
        '#booktext', '.booktext', '#chaptercontent', '.chaptercontent', '#readcontent',
        '.readcontent', '#chapter-content', '.content-text', '#content-text',
        '.novel-content', '#novel-content', '.article-content', '#article-content',
        'article', '.post-content', '.entry-content', '.text', '.content',
        'main', '#content', '#main', '.chapter', '.reading-content',
        '.story-content', '.reader-content', '.entry', '.post',
        '#TextContent', '.TextContent', '#booktxt', '.booktxt',
        '#chapterbody', '.chapterbody', '#bookcontent', '.bookcontent'
    ];
    function findContentElementIn(root) {
        const sig = getBookSelectors().content;
        if (sig) {
            const candidates = [];
            const push = el => { if (el && !candidates.includes(el)) candidates.push(el); };
            if (typeof sig === 'string') {
                push(tryQuery(root, sig));
            } else {
                if (sig.sel) push(tryQuery(root, sig.sel));
                tryQueryAll(root, sigOwnSelector(sig)).forEach(push);
                if (sig.ancestors && sig.ancestors.length) {
                    const chain = sig.ancestors.slice().reverse().map(sigAncestorPart);
                    for (let drop = 0; drop < chain.length; drop++) {
                        push(tryQuery(root, chain.slice(drop).concat([sigOwnSelector(sig)]).join(' ')));
                    }
                }
            }
            let best = null, bestLen = 0;
            for (const c of candidates) {
                const len = textLen(c);
                if (len > bestLen) { bestLen = len; best = c; }
            }
            if (best && bestLen > 200) return best;
        }
        let bestEl = null, bestLen = 0;
        for (const s of GENERIC_CONTENT_SELECTORS) {
            for (const el of tryQueryAll(root, s)) {
                const len = textLen(el);
                if (len > bestLen) { bestLen = len; bestEl = el; }
            }
        }
        return bestEl || (root.body || root.documentElement);
    }
    function findContentElement() { return findContentElementIn(document); }

    const HIDE_SELECTORS = [
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'script', 'style',
        '.txtinfo', '.txtright', '.contentadv', '.bottom-ad', '.bottom-ad2',
        '.ad', '.ads', '.advertisement', '.hide720', '.tools', '.bread', '.page1',
        '.yueduad1', '.nav-buttons', '.portlet-title', '.actions'
    ];
    function extractMainText(element) {
        const hidden = [];
        for (const sel of HIDE_SELECTORS) {
            element.querySelectorAll(sel).forEach(el => {
                if (el.dataset.nmHidden) return;
                el.dataset.nmHidden = '1';
                // запоминаем, что вернуть: пред-существующий inline display нельзя
                // просто удалить — скрытый сайтом элемент стал бы видимым, и текст
                // страницы при повторном извлечении менялся бы (ломая resume NER)
                el.dataset.nmPrevDisplay = el.style.getPropertyValue('display');
                el.style.setProperty('display', 'none', 'important');
                hidden.push(el);
            });
        }
        let raw = '';
        try { raw = element.innerText || ''; }
        finally {
            hidden.forEach(el => {
                const prev = el.dataset.nmPrevDisplay;
                if (prev) el.style.setProperty('display', prev);
                else el.style.removeProperty('display');
                delete el.dataset.nmPrevDisplay;
                delete el.dataset.nmHidden;
            });
        }
        return paragraphsOf(raw.replace(/\u00a0/g, ' ')).join('\n\n');
    }
    function extractTextFromDoc(doc, sig) {
        let root = sig ? findBySignature(doc, sig) : null;
        if (!root || textLen(root) < 200) {
            root = null;
            for (const s of GENERIC_CONTENT_SELECTORS) {
                const el = tryQuery(doc, s);
                if (el && textLen(el) > 200) { root = el; break; }
            }
        }
        if (!root) root = doc.body;
        if (!root) return '';
        root.querySelectorAll('script,style,noscript,iframe,form,.ad,.ads,.advertisement').forEach(el => el.remove());
        const ps = root.querySelectorAll('p');
        const paras = [];
        if (ps.length > 3) {
            ps.forEach(p => {
                const t = (p.textContent || '').replace(/\u00a0/g, ' ').trim();
                if (t) paras.push(t);
            });
        } else {
            paras.push(...paragraphsOf((root.textContent || '').replace(/\u00a0/g, ' ')));
        }
        return paras.join('\n\n');
    }


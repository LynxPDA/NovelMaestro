    // ===== НАВИГАЦИЯ =====
    const NAV_TEXT = {
        next: /next|впер[её]д|след|дал[её]е|→|»|>|下一页|下页|下一章|下章|继续|chương\s*sau|tiếp theo/i,
        prev: /prev(ious)?|назад|пред|←|«|<|上一页|上页|上一章|前章|chương\s*trước|trang trước/i,
        toc: /contents?|оглавл[её]н|содерж|index|toc|список глав|каталог|目录/i
    };
    function absHref(a, base) {
        if (!a) return null;
        const h = a.getAttribute && (a.getAttribute('href') || '');
        if (!h || h === '#' || /^javascript:/i.test(h)) return null;
        try { return new URL(h, base).href.split('#')[0]; } catch { return null; }
    }
    function pickLink(links, type) {
        const re = NAV_TEXT[type];
        if (!re) return null;
        for (const a of links) {
            const rel = (a.getAttribute && (a.getAttribute('rel') || '')).toLowerCase();
            if (type === 'next' && rel === 'next') return a;
            if (type === 'prev' && (rel === 'prev' || rel === 'previous')) return a;
        }
        for (const a of links) {
            const t = (a.textContent || '').trim();
            if (t && t.length <= 40 && re.test(t)) return a;
        }
        for (const a of links) {
            const cls = String(a.className || '') + ' ' + String(a.id || '');
            if (re.test(cls)) return a;
        }
        return null;
    }
    function resolveNavHref(root, sig, baseUrl, type) {
        if (sig) {
            const el = findBySignature(root, sig);
            if (el) {
                if (el.tagName === 'A') { const h = absHref(el, baseUrl); if (h) return h; }
                if (el.querySelectorAll) {
                    const links = Array.from(el.querySelectorAll('a'));
                    if (links.length === 1) { const h = absHref(links[0], baseUrl); if (h) return h; }
                    if (links.length > 1) {
                        const pick = pickLink(links, type);
                        if (pick) { const h = absHref(pick, baseUrl); if (h) return h; }
                    }
                }
            }
            const cont = findContainerByAncestors(root, sig);
            if (cont && cont.querySelectorAll) {
                const links = Array.from(cont.querySelectorAll('a'));
                const pick = pickLink(links, type) || (links.length === 1 ? links[0] : null);
                if (pick) { const h = absHref(pick, baseUrl); if (h) return h; }
            }
        }
        // toc — только по выученной сигнатуре: универсальной надёжной эвристики для
        // оглавления нет, а ложная кнопка хуже, чем её отсутствие
        if (type !== 'toc') {
            const rel = tryQuery(root, type === 'next' ? 'a[rel="next"]' : 'a[rel="prev"]');
            if (rel) { const h = absHref(rel, baseUrl); if (h) return h; }
            const pick = pickLink(Array.from(root.querySelectorAll ? root.querySelectorAll('a') : []), type);
            if (pick) { const h = absHref(pick, baseUrl); if (h) return h; }
        }
        return null;
    }
    function resolveNavFromLive() {
        const sel = getBookSelectors();
        return {
            nextUrl: resolveNavHref(document, sel.next, location.href, 'next'),
            prevUrl: resolveNavHref(document, sel.prev, location.href, 'prev'),
            tocUrl: resolveNavHref(document, sel.toc, location.href, 'toc')
        };
    }


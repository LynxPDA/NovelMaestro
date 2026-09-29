    // ===== HTTP =====
    function makeAbortError(isTimeout) {
        const e = new Error(isTimeout ? 'Таймаут запроса' : 'Запрос прерван');
        e.name = 'AbortError';
        e.isTimeout = !!isTimeout;
        return e;
    }
    function streamFromBody(text) {
        const encoded = new TextEncoder().encode(text || '');
        return new ReadableStream({ start(c) { c.enqueue(encoded); c.close(); } });
    }
    // Менеднеры реализуют GM_xmlhttpRequest по-разному: Tampermonkey умеет отдать
    // ReadableStream (responseType:'stream'), Violentmonkey сидит на XHR — на
    // неизвестном responseType он только пишет в консоль и отдаёт тело целиком.
    // Режим запоминается в GM-хранилище: 'stream' — токены приходят потоком,
    // 'text' — тело приходит одним куском (или растущим responseText).
    // Режим определяется сразу, а не первым ответом: XHR-менеджер присылает
    // loadstart уже вместе с ответом, и таймер успевает убить долгий запрос.
    // GM_info.scriptHandler — как раз для этого; если менеджер не опознан, первый
    // стрим-запрос идёт терпеливо, режим выяснится по нему.
    const gmHandlerName = (typeof GM_info !== 'undefined' && GM_info && GM_info.scriptHandler) || '';
    let gmStreamMode = GM_getValue('gmStreamMode', null);
    if (/violentmonkey/i.test(gmHandlerName)) gmStreamMode = 'text';
    function gmSetStreamMode(mode) {
        if (gmStreamMode === mode) return;
        gmStreamMode = mode;
        GM_setValue('gmStreamMode', mode);
    }
    function gmFetch(url, options, isStream = false) {
        return new Promise((resolve, reject) => {
            let settled = false;
            let req = null;
            const abort = () => { try { if (req) req.abort(); } catch {} };
            // внешний колбэк: таймаут/отмена могут прервать GM-запрос и без signal
            if (typeof options.getAbort === 'function') { try { options.getAbort(abort); } catch {} }
            const settleResolve = (v) => { if (!settled) { settled = true; resolve(v); } };
            const settleReject = (e) => { if (!settled) { settled = true; reject(e); } };
            const parseInfo = (response) => {
                const info = { status: response.status || 0, statusText: response.statusText || '', headers: new Headers(), abort };
                if (response.responseHeaders) {
                    response.responseHeaders.split(/\r?\n/).forEach(line => {
                        const i = line.indexOf(':');
                        if (i > 0) info.headers.append(line.slice(0, i).trim(), line.slice(i + 1).trim());
                    });
                }
                return info;
            };
            const httpError = (info, body) => {
                const err = new Error(`HTTP ${info.status}`);
                err.status = info.status;
                err.statusText = info.statusText || '';
                err.body = String(body || '').slice(0, 200);
                return err;
            };
            const reqOptions = {
                method: options.method || 'GET',
                url: url,
                headers: options.headers || {},
                data: options.body,
                // у XHR-менеджера 'stream' просят напрасно: он не знает его и пишет
                // в консоль — просим text, тело всё равно придёт тем же потоком
                responseType: (isStream && gmStreamMode !== 'text') ? 'stream' : 'text',
                onerror: () => settleReject(Object.assign(new Error('сеть недоступна'), { isNet: true })),
                onabort: () => settleReject(makeAbortError(false)),
                ontimeout: () => settleReject(makeAbortError(true))
            };
            if (isStream) {
                // У потоковых менеджеров (Tampermonkey) тело приходит ReadableStream.
                // У XHR-менеджеров (Violentmonkey) тело приходит НЕ накопленным:
                // каждое событие несёт очередной кусок — нередко только в response,
                // а responseText пуст; load же приходит уже пустым. Куски собираем
                // сами в accum, повтор того же куска (readystatechange и load по
                // одному readyState) отбрасываем по lastPiece.
                let sink = null, delivered = 0, accum = '', lastPiece = null;
                // что реально передал менеджер: без этой трассы «зависает» неотличить
                // от «молчит» — её печатает строка проверки сервера
                const traceLog = [], t0req = Date.now();
                const absorb = (response) => {
                    const was = accum.length;
                    for (const raw of [response.responseText, response.response]) {
                        const piece = typeof raw === 'string' ? raw : '';
                        if (!piece || piece === lastPiece) continue;
                        lastPiece = piece;
                        accum = (piece.length >= accum.length && piece.startsWith(accum)) ? piece : accum + piece;
                    }
                    return accum.length - was;
                };
                const feed = (done) => {
                    if (!sink) return;
                    if (accum.length > delivered) { try { sink.enqueue(new TextEncoder().encode(accum.slice(delivered))); } catch { /* поток закрыт */ } }
                    delivered = accum.length;
                    if (done) { try { sink.close(); } catch { /* уже закрыт */ } sink = null; }
                };
                // первый же сигнал решает, с каким менеджером имеем дело
                const settleBody = (response) => {
                    if (settled) return;
                    const info = parseInfo(response);
                    const stream = response.response;
                    if (stream && typeof stream.getReader === 'function') {
                        gmSetStreamMode('stream');
                        settleResolve(Object.assign(info, { ok: true, status: info.status || 200, body: stream, trace: traceLog }));
                        return;
                    }
                    gmSetStreamMode('text');
                    const body = new ReadableStream({ start(c) { sink = c; } });
                    settleResolve(Object.assign(info, { ok: true, status: info.status || 200, body, trace: traceLog }));
                };
                const guard = (response) => {
                    const info = parseInfo(response);
                    if (info.status < 400) return true;
                    settleReject(httpError(info, response.responseText));
                    return false;
                };
                const note = (response) => {
                    if (traceLog.length < 8) traceLog.push(`rs${response.readyState} +${absorb(response)}б @${Date.now() - t0req}мс`);
                };
                const grow = (response) => { if (!guard(response)) return; settleBody(response); note(response); feed(false); };
                const finish = (response) => { if (!guard(response)) return; settleBody(response); note(response); feed(true); };
                reqOptions.onloadstart = grow;
                reqOptions.onprogress = grow;
                // XHR-менеджер пишет новые куски именно в readystatechange: без него
                // до скрипта доходит только первый кусок тела
                reqOptions.onreadystatechange = grow;
                reqOptions.onload = finish;
            } else {
                reqOptions.onload = (response) => {
                    const info = parseInfo(response);
                    const body = response.responseText || '';
                    const resp = Object.assign(info, {
                        ok: info.status >= 200 && info.status < 300,
                        text: () => Promise.resolve(body),
                        json: () => { try { return Promise.resolve(JSON.parse(body)); } catch (e) { return Promise.reject(e); } }
                    });
                    if (resp.ok) settleResolve(resp);
                    else settleReject(httpError(info, body));
                };
            }
            req = GM_xmlhttpRequest(reqOptions);
            if (options.signal) {
                if (options.signal.aborted) { abort(); settleReject(makeAbortError(false)); return; }
                options.signal.addEventListener('abort', () => { abort(); settleReject(makeAbortError(false)); });
            }
        });
    }
    /**
     * Транспорт по умолчанию — fetch из страницы: он не зависит от мостика
     * контент↔фон менеджера (у Violentmonkey на части устройств он сломан).
     * Хост закрыт CORS — тот же запрос один раз уходит каналом менеджера.
     */
    async function customFetch(url, options, isStream = false) {
        const mode = config.gmTransport === 'auto' ? 'page' : config.gmTransport;
        const gmAvailable = typeof GM_xmlhttpRequest !== 'undefined';
        if (!gmAvailable || mode === 'page') {
            try {
                return await fetch(url, options);
            } catch (e) {
                if (gmAvailable && e && (e.isNet || e.name === 'TypeError')) return gmFetch(url, options, isStream);
                throw e;
            }
        }
        return gmFetch(url, options, isStream);
    }
/** Куда именно били — без этого с телефона не понять, смотреть на адрес или на модель. */
    function requestTarget(url) {
        try {
            const u = new URL(url);
            const path = u.pathname.replace(/\/+$/, '');
            return u.host + (path && path !== '/' ? path : '');
        } catch { return String(url || ''); }
    }
    /** Одна строка о причине отказа: HTTP-код, таймаут или недоступный адрес. */
    function describeError(e, target, ms, fullUrl) {
        if (!e) return `неизвестная ошибка (${ms}мс)`;
        if (e.status) return `HTTP ${e.status}${e.statusText ? ' ' + e.statusText : ''} • ${e.body || 'тело пустое'}`;
        if (e.isTimeout) return `таймаут ${ms}мс • ответа от ${target} нет` + (e.trace ? ` • события: ${e.trace}` : '');
        if (e.isNet) return `сеть недоступна (${ms}мс) • ${target}`
            + (/^http:/i.test(fullUrl || target) ? ' • http-адрес мог быть отсеян HTTPS-only режимом браузера' : '');
        return `${e.message || 'ошибка'} (${ms}мс)` + (e.trace ? ` • события: ${e.trace}` : '');
    }
    async function fetchAttempt(url, options, isStream, timeoutSec, cb = {}) {
        const timeoutMs = timeoutSec > 0 ? timeoutSec * 1000 : 0;
        const controller = new AbortController();
        let reader = null, timer = null, cancelWatcher = null, underlyingAbort = null, failed = false;
        // трассу ведёт customFetch; здесь она только попадает в ответ и в текст ошибки
        const traceLog = [];
        // Гонка-промиис, который только отвергается: с ним мы гарантированно выходим
        // из await, даже если reader.cancel() не разбудил зависший read потока
        let rejectWatch = null;
        const watchPromise = new Promise((_, rej) => { rejectWatch = rej; });
        const fail = (err) => {
            if (failed) return;
            failed = true;
            if (timer) { clearTimeout(timer); timer = null; }
            if (cancelWatcher) { clearInterval(cancelWatcher); cancelWatcher = null; }
            try { if (reader) reader.cancel(); } catch {}
            try { if (underlyingAbort) underlyingAbort(); } catch {}
            try { controller.abort(); } catch {}
            if (rejectWatch) rejectWatch(err);
        };
        // Таймаут передооружается только на приходе полезных токенов: пустые SSE-пинги
        // без контента его не сбрасывают. В режиме целого ответа (XHR-менеджер)
        // пауза между токенами неотличима от ожидания всего ответа, поэтому таймер
        // не участвует — такой запрос держит менеджер, отмена остаётся кнопкой.
        // Для проверки сервера (cb.deadline) он остаётся общим дедлайном: там ждать
        // нечего, а висящее соединение и есть результат проверки.
        const arm = () => {
            if (failed) return;
            if (!timeoutMs || (isStream && gmStreamMode !== 'stream' && !cb.deadline)) {
                // снимается и уже взведённый таймер: иначе первый же ответ в новом
                // режиме всё равно погибал бы по паузе
                if (timer) { clearTimeout(timer); timer = null; }
                return;
            }
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => fail(makeAbortError(true)), timeoutMs);
        };
        cancelWatcher = setInterval(() => {
            if (cancelRequested) fail(new Error('Отменено пользователем'));
        }, 200);
        if (cancelRequested) fail(new Error('Отменено пользователем'));
        arm();
        const readRespText = async (resp) => {
            try {
                if (resp && typeof resp.text === 'function') return await resp.text();
                if (resp && resp.body) return await new Response(resp.body).text();
            } catch {}
            return '';
        };
        try {
            const fetchPromise = customFetch(url, { ...options, signal: controller.signal, getAbort: (fn) => { underlyingAbort = fn; } }, isStream);
            fetchPromise.catch(() => {});
            const resp = await Promise.race([fetchPromise, watchPromise]);
            if (Array.isArray(resp.trace)) { traceLog.length = 0; traceLog.push(...resp.trace); }
            // режим мог определиться в ходе этого ответа — перезапускаем таймер по
            // его правилам: для потока он нужен, для целого тела — не нужен
            arm();
            if (!resp.ok) {
                const errText = await readRespText(resp);
                const err = new Error(`HTTP ${resp.status}: ${String(errText).slice(0, 200)}`);
                err.status = resp.status;
                err.trace = traceLog.join(' ');
                throw err;
            }
            if (!isStream) return resp;
            if (!resp.body || typeof resp.body.getReader !== 'function') {
                // XHR-реализация GM_xmlhttpRequest (Violentmonkey) отдаёт тело целиком:
                // те же строки SSE, тот же разбор — просто без посимвольной выдачи
                resp.body = streamFromBody(await readRespText(resp));
            }
            reader = resp.body.getReader();
            activeReader = reader;
            const decoder = new TextDecoder();
            let text = '', buffer = '', streamError = null, streamModel = '', sawDone = false, reasoningChars = 0;
            const handleLine = (line) => {
                const trimmed = line.trim();
                if (!trimmed.startsWith('data:')) return;
                const payload = trimmed.slice(5).trim();
                if (payload === '[DONE]') { sawDone = true; return; }
                try {
                    const parsed = JSON.parse(payload);
                    if (parsed && parsed.error) {
                        // ошибка в SSE-потоке (неверная модель/ключ/парамет) — фатальна, без ретраев
                        const info = streamErrorInfo(parsed.error);
                        streamError = new Error(info.message);
                        streamError.fromStream = true;
                        if (info.status >= 400) { streamError.status = info.status; streamError.isFatal = true; }
                        else streamError.isFatal = /invalid|api key|api_key|authentication|unauthorized|forbidden|model|not found|does not exist|unsupported/i.test(info.message);
                        return;
                    }
                    if (parsed && typeof parsed.model === 'string' && parsed.model) streamModel = parsed.model;
                    const choice = parsed?.choices?.[0];
                    // finish_reason — второй законный терминатор: сервер мог закрыть
                    // поток без [DONE], но сказать, что закончил осознанно
                    if (choice && choice.finish_reason && choice.finish_reason !== 'null') sawDone = true;
                    const content = choice?.delta?.content || '';
                    // thinking-модели пишут размышления отдельными полями (delta
                    // .reasoning_content у DeepSeek/Qwen, .reasoning у OpenRouter): в
                    // перевод они не попадают, но это живая работа сервера — без неё
                    // сторож «нет токенов» убивал бы долгое размышление
                    const thought = choice?.delta?.reasoning_content || choice?.delta?.reasoning || '';
                    if (thought) {
                        reasoningChars += thought.length;
                        if (cb.onReasoning) cb.onReasoning(thought);
                    }
                    if (content) { text += content; if (cb.onDelta) cb.onDelta(content); }
                } catch {}
            };
            while (true) {
                if (failed) break;
                if (cancelRequested) { fail(new Error('Отменено пользователем')); break; }
                const beforeLen = text.length + reasoningChars;
                const readPromise = reader.read();
                readPromise.catch(() => {});
                const { done, value } = await Promise.race([readPromise, watchPromise]);
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split(/\r?\n/);
                buffer = lines.pop();
                for (const line of lines) {
                    handleLine(line);
                    if (streamError) { streamError.trace = traceLog.join(' '); throw streamError; }
                }
                if (text.length + reasoningChars > beforeLen) {
                    arm();
                    // пингу проверки достаточно одного токена: закрываем соединение,
                    // не дожидаясь, пока сервер оборвёт свой SSE-поток
                    if (cb.stopOnFirstContent) break;
                }
            }
            if (!failed && !cancelRequested) { buffer += decoder.decode(); handleLine(buffer); }
            if (cancelRequested) { const e = new Error('Отменено пользователем'); e.trace = traceLog.join(' '); throw e; }
            if (failed) { const e = makeAbortError(true); e.trace = traceLog.join(' '); throw e; }
            // пинг проверки считается ответом и на размышлениях: сервер ответил
            if (cb.stopOnFirstContent && (text.trim() || reasoningChars)) {
                try { if (underlyingAbort) underlyingAbort(); } catch {}
                return { text, reasoning: reasoningChars, model: streamModel, trace: traceLog.join(' ') };
            }
            if (!text.trim()) {
                // сервер мог ответить на stream-запрос обычным JSON-телом: ошибкой —
                // или готовым completion (choices[0].message.content), который тоже
                // нужно принять, а не считать пустым ответом
                const tail = (buffer || '').trim();
                if (tail.startsWith('{')) {
                    try {
                        const parsed = JSON.parse(tail);
                        const one = parsed?.choices?.[0]?.message?.content;
                        if (typeof one === 'string' && one.trim()) return { text: one };
                    } catch { /* не JSON — разбор ниже */ }
                    try {
                        const parsed = JSON.parse(tail);
                        if (parsed && parsed.error) {
                            const info = streamErrorInfo(parsed.error);
                            const err = new Error(info.message);
                            err.fromStream = true;
                            if (info.status >= 400) { err.status = info.status; err.isFatal = true; }
                            else err.isFatal = /invalid|api key|api_key|authentication|unauthorized|forbidden|model|not found|does not exist|unsupported/i.test(info.message);
                            throw err;
                        }
                    } catch (e) { if (e && e.fromStream) throw e; if (e && e.message && !/JSON/i.test(e.message)) throw e; }
                }
                // пустой completion (чаще всего — неверная модель) не считается результатом
                const err = new Error('Пустой ответ модели. Проверьте модель, права и параметры запроса.');
                err.isFatal = true;
                throw err;
            }
            // сторож завершённости — именно здесь: пинг проверки выходит на первом
            // токене (до [DONE]) и пустой ответ свёрнут выше; сюда доходит только
            // полноценный стрим — закрытый без [DONE] и без finish_reason, это обрыв
            if (!sawDone) {
                const e = new Error(`обрыв ответа: соединение закрыто без [DONE] (${text.length} симв.)`);
                e.isCut = true;
                e.trace = traceLog.join(' ');
                throw e;
            }
            return { text };
        } catch (e) {
            if (e && e.name === 'AbortError') e.isTimeout = true;
            throw e;
        } finally {
            if (timer) clearTimeout(timer);
            if (cancelWatcher) clearInterval(cancelWatcher);
            if (reader && activeReader === reader) activeReader = null;
        }
    }
    async function fetchWithRetry(url, options, isStream = false, cb = {}) {
        const timeout = config.requestTimeout > 0 ? config.requestTimeout : 0;
        const attemptsTotal = (config.maxRetries || 0) + 1;
        let lastErr;
        for (let attempt = 1; attempt <= attemptsTotal; attempt++) {
            if (cancelRequested) throw new Error('Отменено пользователем');
            try {
                return await fetchAttempt(url, options, isStream, timeout, cb);
            } catch (e) {
                if (cancelRequested) throw new Error('Отменено пользователем');
                if (e.name === 'AbortError') {
                    e.isTimeout = true;
                    e.message = isStream ? `таймаут ${timeout}с без токенов` : `таймаут ${timeout}с без ответа`;
                }
                lastErr = e;
                if (e.status >= 400 || e.isFatal || cancelRequested || attempt >= attemptsTotal) throw e;
                if (cb.onRetry) cb.onRetry({ nextAttempt: attempt + 1, attemptsTotal, isTimeout: !!e.isTimeout, message: e.message || 'Сетевая ошибка' });
                await new Promise(r => setTimeout(r, Math.min(5000, 500 * 2 ** (attempt - 1))));
            }
        }
        throw lastErr;
    }
    /** Единая точка сборки адреса API: хвостовой слэш в настройках не удваивает //. */
    function apiBase() { return String(config.apiHost || '').trim().replace(/\/+$/, ''); }
    function apiHeaders(json) {
        const h = json ? { 'Content-Type': 'application/json' } : {};
        if (config.apiKey) h.Authorization = `Bearer ${config.apiKey}`;
        return h;
    }
    /**
     * Размышления (thinking/reasoning) у провайдеров не унифицированы: OpenAI
     * знает только reasoning_effort, Anthropic-style серверы — thinking.type,
     * Qwen3 под vLLM/SGLang/llama.cpp — chat_template_kwargs, DashScope и
     * SiliconFlow — enable_thinking, Ollama — think, OpenRouter — reasoning.
     * Профиль отправляет только СВОИ ключи: незнакомый ключ часть серверов
     * считает ошибкой запроса, поэтому «выложить все сразу» — отдельный режим.
     */
    const REASONING_PROFILES = {
        // OpenAI и совместимые: единый reasoning_effort. Без уровня «выключено»
        // отправляется как none, «включено» без уровня — решение сервера
        openai({ mode, effort }) {
            if (effort) return { reasoning_effort: effort };
            return mode === 'off' ? { reasoning_effort: 'none' } : {};
        },
        anthropic({ mode, budget }) {
            if (mode === 'default') return {};
            const thinking = { type: mode === 'on' ? 'enabled' : 'disabled' };
            if (mode === 'on' && budget > 0) thinking.budget_tokens = budget;
            return { thinking };
        },
        // Qwen3/DeepSeek под vLLM/SGLang/llama.cpp: ключ живёт в шаблоне чата
        qwen({ mode }) {
            if (mode === 'default') return {};
            const on = mode === 'on';
            return { chat_template_kwargs: { thinking: on, enable_thinking: on } };
        },
        dashscope({ mode, budget }) {
            if (mode === 'default') return {};
            const body = { enable_thinking: mode === 'on' };
            if (mode === 'on' && budget > 0) body.thinking_budget = budget;
            return body;
        },
        ollama({ mode }) {
            return mode === 'default' ? {} : { think: mode === 'on' };
        },
        // OpenRouter: только ОДНО из effort/max_tokens — роутеры отвечают
        // «Only one of "reasoning.effort" and "reasoning.max_tokens" can be specified»
        openrouter({ mode, effort, budget }) {
            if (mode === 'default') return {};
            const reasoning = { enabled: mode === 'on' };
            if (effort) reasoning.effort = effort;
            else if (mode === 'on' && budget > 0) reasoning.max_tokens = budget;
            return { reasoning };
        },
        // «все сразу» — для серверов, молча игнорирующих чужие ключи; строгие
        // из них отвечают 400, поэтому режим выбирается осознанно
        all(c) {
            const body = {};
            for (const [id, build] of Object.entries(REASONING_PROFILES)) {
                if (id !== 'all') Object.assign(body, build(c));
            }
            return body;
        }
    };
    /** Пусто и старое 'None' — параметр не отправляется (так он понимался всегда). */
    function normalizeEffort(value) {
        const s = String(value ?? '').trim();
        return (!s || s.toLowerCase() === 'none') ? '' : s;
    }
    function thinkingMode() {
        return ['on', 'off'].includes(String(config.thinkingMode)) ? String(config.thinkingMode) : 'default';
    }
    /** Что из reasoning/thinking реально уходит в тело запроса ({} — дефолт сервера). */
    function reasoningFields() {
        const mode = thinkingMode();
        const effort = normalizeEffort(config.reasoningEffort);
        if (mode === 'default' && !effort) return {};
        const build = REASONING_PROFILES[config.thinkingProfile] || REASONING_PROFILES.openai;
        return build({ mode, effort, budget: Number(config.thinkingBudget) || 0 });
    }
    /**
     * Свои поля тела — способ передать то, чего не знает ни один профиль.
     * Битый JSON запрос не ломает: поле помечено ошибкой в настройках.
     */
    function extraBodyFields() {
        const raw = String(config.extraBodyJson || '').trim();
        if (!raw) return {};
        try {
            const parsed = JSON.parse(raw);
            if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
        } catch { /* не разобрано — запрос идёт без своих полей */ }
        return {};
    }
    /** Строка о том, что реально уходит по reasoning: её печатает проверка сервера. */
    function reasoningSummary() {
        const fields = reasoningFields();
        const keys = Object.keys(fields);
        if (!keys.length) return '💭 как у модели';
        return `💭 ${config.thinkingProfile}: ${keys.map(k => `${k}=${JSON.stringify(fields[k])}`).join(' ')}`;
    }
    /** extra — поля, добавляемые к телу поверх типовых (пинг проверки: max_tokens). */
    function llmRequestOptions(messages, temperature, stream, extra = null) {
        const body = { model: config.model, messages, temperature, stream: !!stream };
        Object.assign(body, reasoningFields(), extraBodyFields(), extra || {});
        return {
            url: apiBase() + '/chat/completions',
            options: { method: 'POST', headers: apiHeaders(true), body: JSON.stringify(body) }
        };
    }
    /**
     * Ошибка, пришедшая в теле ответа. Роутеры умеют прятать её строкой с JSON
     * внутри: {"error":"{\"error\":{\"message\":…},\"code\":400}"} — разворачивать
     * надо циклом, иначе пользователь видит экранированный JSON, а потерянный код 400
     * заставляет повторять заведомо плохой запрос maxRetries раз.
     */
    function streamErrorInfo(raw) {
        let node = raw, message = '', status = 0;
        for (let depth = 0; node != null && depth < 4; depth++) {
            if (typeof node === 'string') {
                const s = node.trim();
                let inner = null;
                if (s.startsWith('{')) { try { inner = JSON.parse(s).error ?? null; } catch { inner = null; } }
                if (inner == null) { if (!message) message = s; break; }
                node = inner;
                continue;
            }
            if (typeof node === 'object') {
                if (!message && typeof node.message === 'string' && node.message.trim()) message = node.message;
                if (!status && typeof node.code === 'number') status = node.code;
                node = node.error ?? null;
                continue;
            }
            break;
        }
        return { message: message || (typeof raw === 'string' ? raw : JSON.stringify(raw)), status };
    }
    async function callLLM(messages, temperature, stream, cb = {}) {
        const { url, options } = llmRequestOptions(messages, temperature, stream);
        return await fetchWithRetry(url, options, !!stream, cb);
    }
    // GET /models у провайдера весит сотни килобайт (у routerai.ru — 528 моделей,
    // ~876КБ), поэтому он получает собственный запас времени и в проверке участвует
    // только как диагноз. Пользовательский requestTimeout остаётся как есть: он
    // относится к настоящим запросам перевода.
    const CHECK_TIMEOUT_MIN = 60;
    async function checkServer() {
        const statusEl = $('#server-status');
        const gmMode = config.gmTransport === 'auto' ? 'page' : config.gmTransport;
        const transport = typeof GM_xmlhttpRequest === 'undefined' ? 'fetch'
            : (gmMode === 'page' ? 'fetch из страницы'
                : (gmStreamMode === 'text' ? 'менеджер · XHR (тело целиком)' : 'менеджер · поток'));
        const base = apiBase();
        if (!base) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Не указан API Host'; return; }
        if (!config.apiKey && !config.localModel) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Не указан API Key (или включите «Локальная модель без API-ключа»)'; return; }
        if (!config.model) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Не указана модель'; return; }
        const target = requestTarget(base);
        const timeout = config.requestTimeout > 0 ? config.requestTimeout : 0;
        const diagTimeout = Math.max(timeout, CHECK_TIMEOUT_MIN);
        const show = (cls, text) => {
            statusEl.className = `nm-server-status show ${cls}`;
            statusEl.textContent = text;
        };
        show('loading', `🔌 Проверяю ${target}… (${transport}, таймаут ${timeout || '∞'}с)`);
        // Первым идёт короткий пинг чата — это ровно тот запрос, которым Lite переводит.
        // Стримом и с выходом по первому токену: серверы, отвечающие SSE и не спешащие
        // закрывать соединение (на них не-стримовый пинг висел до таймаута), отвечают
        // сразу; соединение закрываем сами, не дожидаясь их вежливости.
        const t0 = Date.now();
        // max_tokens:1 расходится с включённым thinking: часть серверов требует
        // max_tokens больше бюджета размышлений — тогда лимит просто не шлём
        const extra = thinkingMode() === 'on' ? null : { max_tokens: 1 };
        const { url, options } = llmRequestOptions([{ role: 'user', content: 'ping' }], 0, true, extra);
        try {
            const res = await fetchAttempt(url, options, true, timeout,
                { stopOnFirstContent: true, deadline: true });
            const got = String(res.text || '').trim();
            show('ok', `✅ Сервер доступен • ${target} • пинг ${Date.now() - t0}мс • модель ${res.model || config.model}`
                + (got ? ` • ответ: ${got.slice(0, 24)}` : (res.reasoning ? ` • 💭 только размышления (${res.reasoning} зн.)` : ''))
                + ` • ${reasoningSummary()} • ${transport}`);
        } catch (e) {
            // Пинг не дошёл — отличаем «адрес недоступен» от «модель думает дольше
            // таймаута». Диагностический GET /models идёт со своим запасом: он тяжелый.
            const main = describeError(e, target, Date.now() - t0, url);
            show('loading', `⏳ ${main} • проверяю /models как диагноз…`);
            const t1 = Date.now();
            let note;
            try {
                const resp = await fetchAttempt(base + '/models', { method: 'GET', headers: apiHeaders(false) }, false, diagTimeout, {});
                const data = await resp.json().catch(() => null);
                const list = Array.isArray(data?.data) ? data.data : (Array.isArray(data) ? data : []);
                note = `/models отвечает за ${Date.now() - t1}мс (${list.length} моделей, `
                    + (list.some(m => m && m.id === config.model) ? `модель «${config.model}» есть` : `модели «${config.model}» в списке НЕТ`)
                    + ') — значит канал есть, смотрите время ответа модели';
            } catch (e2) {
                note = `/models тоже молчит: ${describeError(e2, target, Date.now() - t1, base)}`;
            }
            show('err', `❌ ${main} • ${note} • ${transport}`);
        }
    }

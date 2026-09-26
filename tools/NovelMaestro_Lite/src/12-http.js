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
    function customFetch(url, options, isStream = false) {
        if (typeof GM_xmlhttpRequest === 'undefined') return fetch(url, options);
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
                const err = new Error(`HTTP ${info.status}: ${String(body || '').slice(0, 200)}`);
                err.status = info.status;
                return err;
            };
            const reqOptions = {
                method: options.method || 'GET',
                url: url,
                headers: options.headers || {},
                data: options.body,
                responseType: isStream ? 'stream' : 'text',
                onerror: () => settleReject(new Error('NetworkError: Failed to fetch')),
                onabort: () => settleReject(makeAbortError(false)),
                ontimeout: () => settleReject(makeAbortError(true))
            };
            if (isStream) {
                reqOptions.onloadstart = (response) => {
                    const info = parseInfo(response);
                    if (info.status >= 400) { settleReject(httpError(info, response.responseText)); return; }
                    const stream = response.response;
                    if (stream && typeof stream.getReader === 'function') {
                        settleResolve(Object.assign(info, { ok: true, status: info.status || 200, body: stream }));
                    }
                };
                reqOptions.onload = (response) => {
                    const info = parseInfo(response);
                    if (info.status >= 400) { settleReject(httpError(info, response.responseText)); return; }
                    settleResolve(Object.assign(info, { ok: true, status: info.status || 200, body: streamFromBody(response.responseText) }));
                };
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
    async function fetchAttempt(url, options, isStream, timeoutSec, cb = {}) {
        const timeoutMs = timeoutSec > 0 ? timeoutSec * 1000 : 0;
        const controller = new AbortController();
        let reader = null, timer = null, cancelWatcher = null, underlyingAbort = null, failed = false;
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
        // Таймаут передооружается только приходе полезных токенов:
        // пустые SSE-пинги без контента его не сбрасывают
        const arm = () => {
            if (!timeoutMs || failed) return;
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
            if (!resp.ok) {
                const errText = await readRespText(resp);
                const err = new Error(`HTTP ${resp.status}: ${String(errText).slice(0, 200)}`);
                err.status = resp.status;
                throw err;
            }
            if (!isStream) return resp;
            if (!resp.body || typeof resp.body.getReader !== 'function') throw new Error('Сервер не поддерживает стриминг');
            reader = resp.body.getReader();
            activeReader = reader;
            const decoder = new TextDecoder();
            let text = '', buffer = '', streamError = null;
            const handleLine = (line) => {
                const trimmed = line.trim();
                if (!trimmed.startsWith('data:')) return;
                const payload = trimmed.slice(5).trim();
                if (payload === '[DONE]') return;
                try {
                    const parsed = JSON.parse(payload);
                    if (parsed && parsed.error) {
                        // ошибка в SSE-потоке (неверная модель/ключ) — фатальна, без ретраев
                        const errMsg = parsed.error.message || JSON.stringify(parsed.error);
                        streamError = new Error(errMsg);
                        if (typeof parsed.error.code === 'number') streamError.status = parsed.error.code;
                        streamError.isFatal = /invalid|api key|api_key|authentication|unauthorized|forbidden|model|not found|does not exist|unsupported/i.test(errMsg);
                        return;
                    }
                    const content = parsed?.choices?.[0]?.delta?.content || '';
                    if (content) { text += content; if (cb.onDelta) cb.onDelta(content); }
                } catch {}
            };
            while (true) {
                if (failed) break;
                if (cancelRequested) { fail(new Error('Отменено пользователем')); break; }
                const beforeLen = text.length;
                const readPromise = reader.read();
                readPromise.catch(() => {});
                const { done, value } = await Promise.race([readPromise, watchPromise]);
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split(/\r?\n/);
                buffer = lines.pop();
                for (const line of lines) {
                    handleLine(line);
                    if (streamError) throw streamError;
                }
                if (text.length > beforeLen) arm();
            }
            if (!failed && !cancelRequested) { buffer += decoder.decode(); handleLine(buffer); }
            if (cancelRequested) throw new Error('Отменено пользователем');
            if (failed) throw makeAbortError(true);
            if (!text.trim()) {
                // некоторые серверы шлют ошибку в stream-режиме обычным JSON-телом
                const tail = (buffer || '').trim();
                if (tail.startsWith('{')) {
                    try {
                        const parsed = JSON.parse(tail);
                        if (parsed && parsed.error) {
                            const err = new Error(parsed.error.message || JSON.stringify(parsed.error));
                            err.isFatal = /invalid|api key|api_key|authentication|unauthorized|forbidden|model|not found|does not exist|unsupported/i.test(err.message);
                            throw err;
                        }
                    } catch (e) { if (e && e.message && !/JSON/i.test(e.message)) throw e; }
                }
                // пустой completion (чаще всего — неверная модель) не считается результатом
                const err = new Error('Пустой ответ модели. Проверьте модель, права и параметры запроса.');
                err.isFatal = true;
                throw err;
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
                if (e.name === 'AbortError') { e.isTimeout = true; e.message = `Таймаут ${timeout}с: нет полезных данных`; }
                lastErr = e;
                if (e.status >= 400 || e.isFatal || cancelRequested || attempt >= attemptsTotal) throw e;
                if (cb.onRetry) cb.onRetry({ nextAttempt: attempt + 1, attemptsTotal, isTimeout: !!e.isTimeout, message: e.message || 'Сетевая ошибка' });
                await new Promise(r => setTimeout(r, Math.min(5000, 500 * 2 ** (attempt - 1))));
            }
        }
        throw lastErr;
    }
    function llmRequestOptions(messages, temperature, stream) {
        const body = { model: config.model, messages, temperature, stream: !!stream };
        const re = String(config.reasoningEffort ?? '').trim();
        if (re !== '' && re.toLowerCase() !== 'none') body.reasoning_effort = re;
        const headers = { 'Content-Type': 'application/json' };
        if (config.apiKey) headers.Authorization = `Bearer ${config.apiKey}`;
        return {
            url: config.apiHost.replace(/\/$/, '') + '/chat/completions',
            options: { method: 'POST', headers, body: JSON.stringify(body) }
        };
    }
    async function callLLM(messages, temperature, stream, cb = {}) {
        const { url, options } = llmRequestOptions(messages, temperature, stream);
        return await fetchWithRetry(url, options, !!stream, cb);
    }
    async function checkServer() {
        const statusEl = $('#server-status');
        statusEl.className = 'nm-server-status show loading';
        statusEl.textContent = '🔌 Проверяю сервер...';
        if (!config.apiHost) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Не указан API Host'; return; }
        if (!config.apiKey && !config.localModel) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Не указан API Key (или включите «Локальная модель без API-ключа»)'; return; }
        if (!config.model) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Не указана модель'; return; }
        const start = Date.now();
        try {
            const { url, options } = llmRequestOptions([{ role: 'user', content: 'ping' }], 0, false);
            const payload = JSON.parse(options.body);
            payload.max_tokens = 5;
            const resp = await fetchWithRetry(url, { ...options, body: JSON.stringify(payload) }, false);
            const elapsed = Date.now() - start;
            const data = await resp.json().catch(() => null);
            if (data && data.error) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = `❌ Ошибка API: ${data.error.message || JSON.stringify(data.error)} (${elapsed}мс)`; return; }
            if (!data || !data.choices || !data.choices[0]) { statusEl.className = 'nm-server-status show err'; statusEl.textContent = '❌ Некорректный ответ API'; return; }
            statusEl.className = 'nm-server-status show ok';
            statusEl.textContent = `✅ Сервер доступен • модель ${config.model} • ${elapsed}мс`;
        } catch (e) {
            statusEl.className = 'nm-server-status show err';
            statusEl.textContent = `❌ ${e.message} (${Date.now() - start}мс)`;
        }
    }


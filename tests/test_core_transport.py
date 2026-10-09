#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты core/transport.py — единственной HTTP-доставки LLM-запросов.

Транспорт один (httpx), поэтому и моков почти нет: форма запроса и нарезка тела
проверяются на записывателе вызовов stream(), нормализация ошибок — на настоящих
классах исключений httpx (связи классов важнее содержимого: ConnectTimeout —
одновременно ConnectError и TimeoutException), а весь путь целиком — живым
SSE-прогоном против локального сервера из stdlib. Сети наружу тесты не трогают.

Группы: контракт iter_lines, закрытие соединения, соответствие ошибок, форма
запроса и таймаутов, общий клиент пула, живой раунд-трип, недоступный сервер.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "cli"):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

import httpx  # noqa: E402

from core import transport as T  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_client():
    """Общий клиент не протекает между тестами."""
    T.reset_client()
    yield
    T.reset_client()


class _Stream:
    """Заглушка httpx-стрима: контекстный менеджер с iter_bytes()."""

    def __init__(self, chunks=(), exc=None, status=200, headers=None):
        self._chunks = tuple(chunks)
        self._exc = exc
        self.status_code = status
        self.headers = headers if headers is not None else {"X-Trace": "1"}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def close(self):
        self.closed = True

    def iter_bytes(self):
        if self._exc is not None:
            raise self._exc
        yield from self._chunks


class _Recorder:
    """Пишет аргументы stream(): так видно форму вызова транспорта."""

    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply  # не-None — этим телом отвечает заглушка

    def stream(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        return _Stream(chunks=self.reply if self.reply is not None
                       else [b"data: [DONE]\n"])


def _stream(chunks=(), exc=None, status=200, headers=None):
    """Ответ транспорта поверх подставного стрима."""
    return T.ResponseStream(_Stream(chunks, exc, status, headers))


# ══════════════════════════════════════════════════════════════════════
# Контракт ответа: строки SSE
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("chunks,want", [
    ((b"data: 1\ndata: 2\n",), [b"data: 1", b"data: 2"]),
    # строка разорвана между чанками — склеивается, а не режется пополам
    ((b"da", b"ta: 1\nda", b"ta: 2\n"), [b"data: 1", b"data: 2"]),
    ((b"a\r\nb\r\n",), [b"a", b"b"]),
    # хвост без \n (обрыв/конец стрима) обязано видеть читатель
    ((b"a\nb",), [b"a", b"b"]),
    # пустые строки — границы событий, их пропускает читатель
    ((b"\n\n",), [b"", b""]),
    ((), []),
    ((b"", b"data: x\n"), [b"data: x"]),
], ids=["plain", "split-chunk", "crlf", "tail-no-newline", "empty-lines",
        "no-body", "empty-chunk"])
def test_iter_lines_splits_body(chunks, want):
    with _stream(chunks) as resp:
        assert list(resp.iter_lines()) == want


def test_status_and_headers_pass_through():
    with _stream(status=429, headers={"Retry-After": "7"}) as resp:
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After") == "7"

@pytest.mark.parametrize("chunks,want", [
    ((b"PK\x03\x04\r\n\x00", b"PK\x01\x02\n\r"),
     b"PK\x03\x04\r\n\x00PK\x01\x02\n\r"),
    # переводы строк — данные, а не границы: iter_bytes их не трогает
    ((b"a\nb\r\nc",), b"a\nb\r\nc"),
    ((), b""),
], ids=["zip-bytes", "newlines-kept", "empty"])
def test_iter_bytes_keeps_raw_body(chunks, want):
    """Сырое тело для бинарных скачиваний: zip-байты и \n/\r не трогаются
    (iter_lines резал бы \r и склеивал строки — zip бился)."""
    with _stream(chunks) as resp:
        assert b"".join(resp.iter_bytes()) == want


def test_response_closed_on_exit():
    inner = _Stream(chunks=[b"data: [DONE]\n"])
    with T.ResponseStream(inner) as resp:
        list(resp.iter_lines())
    assert inner.closed is True


def test_response_closed_on_early_break():
    """break в середине стрима тоже обязан закрыть соединение."""
    inner = _Stream(chunks=[b"a\nb\nc\n"])
    stream = T.ResponseStream(inner)
    with stream:
        for _ in stream.iter_lines():
            break
    assert inner.closed is True


# ══════════════════════════════════════════════════════════════════════
# Нормализация ошибок: вызывающий не знает про httpx
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("exc,want", [
    (httpx.ConnectTimeout("connect"), T.ConnectTimeout),
    (httpx.ConnectError("connection refused"), T.ConnectTimeout),
    (httpx.ReadTimeout("read"), T.ReadTimeout),
    (httpx.PoolTimeout("pool"), T.ReadTimeout),
    (httpx.RemoteProtocolError("peer closed"), T.BrokenStream),
    (httpx.ReadError("read error"), T.BrokenStream),
    (httpx.UnsupportedProtocol("weird"), T.TransportError),
], ids=["connect-timeout", "connect-error", "read-timeout", "pool-timeout",
        "remote-protocol", "read-error", "other"])
def test_exceptions_are_normalized(exc, want):
    with pytest.raises(want) as got:
        with _stream(exc=exc) as resp:
            list(resp.iter_lines())
    # сообщение исходной ошибки сохраняется
    assert str(exc) in str(got.value)


def test_all_errors_share_one_base():
    for cls in (T.ConnectTimeout, T.ReadTimeout, T.BrokenStream):
        assert issubclass(cls, T.TransportError)


# ══════════════════════════════════════════════════════════════════════
# Форма запроса и таймауты
# ══════════════════════════════════════════════════════════════════════
def test_open_stream_request_shape(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(T, "_client", rec)
    with T.open_stream("http://127.0.0.1:1/v1/chat/completions",
                       headers={"Authorization": "Bearer k"},
                       payload={"model": "m", "stream": True},
                       connect_timeout=7, read_timeout=9) as resp:
        assert resp.status_code == 200
        assert list(resp.iter_lines()) == [b"data: [DONE]"]
    call = rec.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/v1/chat/completions")
    assert call["json"] == {"model": "m", "stream": True}
    assert call["headers"] == {"Authorization": "Bearer k"}
    timeout = call["timeout"]
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == \
        (7, 9, 9, 7)


def test_default_timeouts_match_stage_defaults():
    """Числа по умолчанию — те же, что у параметров stream_chat_completion."""
    timeout = T.client().timeout
    assert timeout.connect == T.DEFAULT_CONNECT_TIMEOUT == 300.0
    assert timeout.read == T.DEFAULT_READ_TIMEOUT == 900.0
    assert timeout.write == T.DEFAULT_READ_TIMEOUT
    assert timeout.pool == T.DEFAULT_CONNECT_TIMEOUT

def test_open_get_request_shape(monkeypatch):
    """GET-проверка: форма вызова и follow_redirects — только по флагу
    (LLM-серверу редиректы не нужны, GitHub без них не отдаёт ассет)."""
    rec = _Recorder()
    monkeypatch.setattr(T, "_client", rec)
    with T.open_get("http://127.0.0.1:1/v1/models",
                    headers={"Authorization": "Bearer k"},
                    timeout=5) as resp:
        assert list(resp.iter_lines()) == [b"data: [DONE]"]
    call = rec.calls[0]
    assert call["method"] == "GET" and call["url"].endswith("/v1/models")
    assert call["follow_redirects"] is False
    timeout = call["timeout"]
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) \
        == (5, 5, 5, 5)
    with T.open_get("http://127.0.0.1:1/x", follow_redirects=True):
        pass
    assert rec.calls[1]["follow_redirects"] is True

def test_open_json_get_ok(monkeypatch):
    """JSON GET: 200 + тело — разобранный словарь."""
    rec = _Recorder(reply=[b'{"a": 1}'])
    monkeypatch.setattr(T, "_client", rec)
    assert T.open_json_get("http://127.0.0.1:1/api") == {"a": 1}
    assert rec.calls[0]["method"] == "GET"

@pytest.mark.parametrize("status,body,want", [
    (404, b'{"message": "Not Found"}', "HTTP 404"),
    (200, b"not json", "Ответ не JSON"),
    (200, b'[1, 2]', "Ответ не JSON-объект"),
], ids=["http-error", "not-json", "not-dict"])
def test_open_json_get_errors(monkeypatch, status, body, want):
    rec = _Recorder()
    rec.stream = lambda method, url, **kw: _Stream(chunks=[body], status=status)
    monkeypatch.setattr(T, "_client", rec)
    with pytest.raises(T.TransportError) as exc:
        T.open_json_get("http://127.0.0.1:1/api")
    assert want in str(exc.value)


def test_client_is_shared():
    first = T.client()
    assert T.client() is first


def test_reset_client_closes_and_forgets():
    inner = _Stream()
    T._client = inner  # noqa: SLF001 — подмена общего клиента в тесте
    T.reset_client()
    assert inner.closed is True
    assert T._client is None


# ══════════════════════════════════════════════════════════════════════
# Живой прогон: локальный SSE-сервер (без сети наружу)
# ══════════════════════════════════════════════════════════════════════
SERVER: dict = {"status": 200, "lines": [b"data: [DONE]\n"], "extra": {},
                "received": {}}


class _SSEHandler(BaseHTTPRequestHandler):
    """Эхо-сервер: отвечает SSE-строками из SERVER, статус — из SERVER."""

    def do_POST(self):  # noqa: N802 — канон http.server
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length)
        SERVER["received"] = {
            "path": self.path,
            "content_type": self.headers.get("Content-Type"),
            "authorization": self.headers.get("Authorization"),
            "payload": json.loads(raw.decode("utf-8")) if raw else None,
        }
        self.send_response(SERVER["status"])
        self.send_header("Content-Type", "text/event-stream")
        for key, value in SERVER["extra"].items():
            self.send_header(key, value)
        self.end_headers()
        for line in SERVER["lines"]:
            # SSE: каждая строка события заканчивается \n
            self.wfile.write(line + b"\n")
            self.wfile.flush()

    def log_message(self, format, *args):  # тишина в тестах
        pass


@pytest.fixture()
def sse_server(srv_port):
    """ThreadingHTTPServer с SSE-ответом; возвращает (url, настройки)."""
    SERVER.update(status=200, lines=[b"data: [DONE]\n"], extra={}, received={})
    srv = ThreadingHTTPServer(("127.0.0.1", srv_port), _SSEHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv_port}/v1/chat/completions", SERVER
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
        T.reset_client()


def _sse_line(content: str, finish: str | None = None) -> bytes:
    """Одна SSE-строка OpenAI-совместимого чанка."""
    choice: dict = {"delta": {"content": content}}
    if finish:
        choice["finish_reason"] = finish
    return ("data: " + json.dumps({"choices": [choice]},
                                  ensure_ascii=False)).encode("utf-8")


def test_live_sse_roundtrip(sse_server):
    """Живой SSE: payload/заголовки доходят, строки режутся по \\n."""
    url, cfg = sse_server
    cfg["lines"] = [_sse_line("при"), b"", _sse_line("вет", "stop")]
    lines: list[bytes] = []
    with T.open_stream(url, headers={"Authorization": "Bearer key"},
                       payload={"model": "m", "stream": True},
                       connect_timeout=10, read_timeout=20) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("Content-Type", "")
        lines = list(resp.iter_lines())
    assert cfg["received"]["payload"] == {"model": "m", "stream": True}
    assert cfg["received"]["content_type"] == "application/json"
    assert cfg["received"]["authorization"] == "Bearer key"
    assert [ln for ln in lines if ln] == [_sse_line("при"),
                                          _sse_line("вет", "stop")]


def test_live_error_status_and_retry_after(sse_server):
    """Нечётные коды не проглатываются: статус и Retry-After видны выше."""
    url, cfg = sse_server
    cfg["status"], cfg["extra"] = 429, {"Retry-After": "7"}
    seen: dict = {"status": 0, "retry_after": None}
    with T.open_stream(url, payload={}) as resp:
        seen["status"] = resp.status_code
        seen["retry_after"] = resp.headers.get("Retry-After")
    assert seen["status"] == 429
    assert seen["retry_after"] == "7"


def test_live_connection_refused_is_connect_timeout(srv_port):
    """Сервер лежит — наружу уходит ConnectTimeout, а не httpx-исключение."""
    with pytest.raises(T.ConnectTimeout):
        with T.open_stream(f"http://127.0.0.1:{srv_port}/v1/chat/completions",
                           payload={}, connect_timeout=1, read_timeout=1):
            pass


def test_backend_is_reported_as_httpx():
    assert T.BACKEND == "httpx"
    assert T.main([]) == 0

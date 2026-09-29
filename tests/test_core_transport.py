#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты core/transport.py — единой HTTP-доставки LLM-запросов.

Группы:
- выбор бэкенда (httpx предпочтён, requests — фолбэк, ничего нет — ошибка);
- контракт ответа: нарезка тела на SSE-строки и закрытие при выходе;
- нормализация исключений обоих бэкендов (подставные модули держат настоящие
  связи классов: ConnectTimeout — подкласс Timeout и т.п.);
- проброс payload/заголовков/таймаутов и живой SSE-прогон против локального
  сервера из stdlib (сети наружу тесты не трогают).
"""
from __future__ import annotations

import importlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from core import transport as T


def _live_backend(name: str):
    """Настоящий бэкенд поверх живого сервера (клиент создастся заново)."""
    T.reset_client()
    try:
        module = importlib.import_module(name)
    except ImportError:
        pytest.skip(f"{name} не установлен")
    T._state.update(backend=name, module=module, client=None)

# ══════════════════════════════════════════════════════════════════════
# Подставные бэкенды
# ══════════════════════════════════════════════════════════════════════
def _fake_httpx():
    """Иерархия исключений httpx (сжатая, но со связями оригинала)."""
    class HTTPError(Exception):
        pass

    class TransportError(HTTPError):
        pass

    class TimeoutException(TransportError):
        pass

    class ConnectTimeout(TimeoutException):
        pass

    class ReadTimeout(TimeoutException):
        pass

    class ProtocolError(TransportError):
        pass

    class RemoteProtocolError(ProtocolError):
        pass

    class ReadError(TransportError):
        pass

    class Timeout:
        """httpx.Timeout: первый позиционный аргумент — read."""

        def __init__(self, read, connect=None, write=None, pool=None):
            self.read, self.connect = read, connect

    class Limits:
        def __init__(self, max_connections=None, max_keepalive_connections=None):
            self.max_connections = max_connections

    class Client:
        def __init__(self, timeout=None, limits=None):
            self.timeout, self.limits = timeout, limits

    return SimpleNamespace(
        HTTPError=HTTPError, TransportError=TransportError,
        TimeoutException=TimeoutException, ConnectTimeout=ConnectTimeout,
        ReadTimeout=ReadTimeout, ProtocolError=ProtocolError,
        RemoteProtocolError=RemoteProtocolError, ReadError=ReadError,
        Timeout=Timeout, Limits=Limits, Client=Client,
    )


def _fake_requests():
    """Иерархия исключений requests: ConnectTimeout — подкласс Timeout."""
    class RequestException(Exception):
        pass

    class Timeout(RequestException):
        pass

    class ConnectTimeout(Timeout):
        pass

    class ReadTimeout(Timeout):
        pass

    class ChunkedEncodingError(RequestException):
        pass

    return SimpleNamespace(exceptions=SimpleNamespace(
        RequestException=RequestException, Timeout=Timeout,
        ConnectTimeout=ConnectTimeout, ReadTimeout=ReadTimeout,
        ChunkedEncodingError=ChunkedEncodingError,
    ))


class _Body:
    """Ответ бэкенда: отдаёт chunks (или бросает exc), помнит о закрытии."""

    def __init__(self, chunks=(), exc=None, status=200, headers=None):
        self._chunks = list(chunks)
        self._exc = exc
        self.status_code = status
        self.headers = dict(headers or {})
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        self.closed = True
        return False

    def iter_bytes(self):
        yield from self._chunks
        if self._exc is not None:
            raise self._exc

    def iter_content(self, chunk_size=None):
        del chunk_size
        yield from self._chunks
        if self._exc is not None:
            raise self._exc


class _StreamCM:
    """Контекстный менеджер client.stream(...) у httpx: при выходе ответ
    закрывается — на этом же флаге проверяется and break в середине стрима."""

    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self._body

    def __exit__(self, *_a):
        self._body.closed = True
        return False


class _FakeSession:
    """requests.Session: пишет аргументы post() и отдаёт _Body."""

    def __init__(self, body):
        self.calls: dict = {}
        self._body = body

    def post(self, url, headers=None, json=None, stream=None, timeout=None):
        self.calls.update(url=url, headers=headers, json=json, stream=stream,
                          timeout=timeout)
        return self._body

    def close(self):
        pass


class _FakeHttpxClient:
    """httpx.Client: пишет аргументы stream() и отдаёт _Body."""

    def __init__(self, body):
        self.calls: dict = {}
        self._body = body

    def stream(self, method, url, headers=None, json=None, timeout=None):
        self.calls.update(method=method, url=url, headers=headers, json=json,
                          timeout=timeout)
        return _StreamCM(self._body)

    def close(self):
        pass


@pytest.fixture()
def fresh_client():
    """Общий клиент процесса не протекает между тестами."""
    yield
    T.reset_client()


def _use_requests(body, monkeypatch, fresh_client):  # noqa: ARG001
    """Активный бэкенд — requests с подставной сессией."""
    T.reset_client()
    T._state.update(backend="requests", module=_fake_requests(),
                    client=_FakeSession(body))


def _use_httpx(body, monkeypatch, fresh_client):  # noqa: ARG001
    """Активный бэкенд — httpx с подставным клиентом."""
    T.reset_client()
    T._state.update(backend="httpx", module=_fake_httpx(),
                    client=_FakeHttpxClient(body))


# ══════════════════════════════════════════════════════════════════════
# Выбор бэкенда
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("present,want", [
    ({"httpx"}, "httpx"),
    ({"requests"}, "requests"),
    ({"httpx", "requests"}, "httpx"),
])
def test_backend_choice(present, want, monkeypatch, fresh_client):
    """«Что установлено» определяет один проб — find_spec; импорт — победителю.
    В окружении теста библиотека может отсутствовать — подставляем заглушку."""
    monkeypatch.setattr(T, "find_spec",
                        lambda name: object() if name in present else None)
    for name in present:
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(name=name))
    T._state.update(backend=None, module=None, client=None)
    assert T.backend() == want
    assert set(T.installed_backends()) == present
    assert T._state["module"].name == want


def test_backend_without_any_library(monkeypatch, fresh_client):
    monkeypatch.setattr(T, "find_spec", lambda name: None)
    T._state.update(backend=None, module=None, client=None)
    with pytest.raises(T.TransportError) as exc:
        T.backend()
    assert "pip install" in str(exc.value)


# ══════════════════════════════════════════════════════════════════════
# Нарезка тела на строки (общий контракт)
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("chunks,want", [
    ([b""], []),
    ([b"a"], [b"a"]),
    ([b"a\n"], [b"a"]),
    ([b"a\nb"], [b"a", b"b"]),
    ([b"a\r\nb\r\n"], [b"a", b"b"]),
    ([b"a\n", b"b\n"], [b"a", b"b"]),
    ([b"ab", b"cd"], [b"abcd"]),              # чанки строку НЕ режут: она одна
    ([b"ab", b"cd\n"], [b"abcd"]),
    ([b"a\nb\n"], [b"a", b"b"]),             # один чанк — несколько строк
    ([b"data: 1\n\n", b"data: [DONE]\n"],
     [b"data: 1", b"", b"data: [DONE]"]),      # пустая строка-разделитель
])
def test_iter_lines_splits_body(chunks, want, monkeypatch, fresh_client):
    _use_requests(_Body(chunks), monkeypatch, fresh_client)
    with T.open_stream("http://x/y") as resp:
        assert list(resp.iter_lines()) == want


def test_response_closed_on_exit(monkeypatch, fresh_client):
    body = _Body([b"data: 1\n"])
    _use_requests(body, monkeypatch, fresh_client)
    with T.open_stream("http://x/y") as resp:
        list(resp.iter_lines())
    assert body.closed is True


def test_response_closed_on_early_break(monkeypatch, fresh_client):
    """break в середине стрима (loop/cut) обязан закрыть соединение."""
    body = _Body([b"data: 1\n", b"data: 2\n"])
    _use_requests(body, monkeypatch, fresh_client)
    with T.open_stream("http://x/y") as resp:
        for _ in resp.iter_lines():
            break
    assert body.closed is True


# ══════════════════════════════════════════════════════════════════════
# Нормализация исключений
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("exc_name,want", [
    ("ConnectTimeout", T.ConnectTimeout),
    ("ReadTimeout", T.ReadTimeout),
    ("TimeoutException", T.ReadTimeout),
    ("RemoteProtocolError", T.BrokenStream),
    ("ReadError", T.BrokenStream),
    ("HTTPError", T.TransportError),
])
def test_httpx_exceptions_are_normalized(exc_name, want, monkeypatch,
                                         fresh_client):
    m = _fake_httpx()
    body = _Body([b"data: 1\n"], exc=getattr(m, exc_name)("сбой"))
    with pytest.raises(T.TransportError) as exc:
        with T._HttpxStream(_StreamCM(body), m) as resp:
            list(resp.iter_lines())
    assert type(exc.value) is want
    assert body.closed is True          # httpx-контекст закрыт на выходе


@pytest.mark.parametrize("exc_name,want", [
    ("ConnectTimeout", T.ConnectTimeout),
    ("ReadTimeout", T.ReadTimeout),
    ("Timeout", T.ConnectTimeout),   # голый Timeout — тоже «коннект», как раньше
    ("ChunkedEncodingError", T.BrokenStream),
    ("RequestException", T.TransportError),
])
def test_requests_exceptions_are_normalized(exc_name, want, monkeypatch,
                                           fresh_client):
    m = _fake_requests()
    body = _Body([b"data: 1\n"], exc=getattr(m.exceptions, exc_name)("сбой"))
    with pytest.raises(T.TransportError) as exc:
        with T._RequestsStream(body, m) as resp:
            list(resp.iter_lines())
    assert type(exc.value) is want
    assert body.closed is True


# ══════════════════════════════════════════════════════════════════════
# Что уходит в запрос (payload, заголовки, таймауты)
# ══════════════════════════════════════════════════════════════════════
def test_requests_backend_request_shape(monkeypatch, fresh_client):
    body = _Body([b"data: [DONE]\n"], status=200, headers={"X-A": "1"})
    session = _FakeSession(body)
    T.reset_client()
    T._state.update(backend="requests", module=_fake_requests(), client=session)
    with T.open_stream("http://x/y", headers={"Authorization": "Bearer k"},
                       payload={"model": "m", "stream": True},
                       connect_timeout=11, read_timeout=22) as resp:
        assert resp.status_code == 200
        assert resp.headers["X-A"] == "1"
        assert list(resp.iter_lines()) == [b"data: [DONE]"]
    assert session.calls["stream"] is True
    assert session.calls["timeout"] == (11, 22)
    assert session.calls["json"] == {"model": "m", "stream": True}
    assert session.calls["headers"] == {"Authorization": "Bearer k"}


def test_httpx_backend_request_shape(monkeypatch, fresh_client):
    body = _Body([b"data: [DONE]\n"], status=201)
    client = _FakeHttpxClient(body)
    T.reset_client()
    T._state.update(backend="httpx", module=_fake_httpx(), client=client)
    with T.open_stream("http://x/y", payload={"p": 1}, connect_timeout=11,
                       read_timeout=22) as resp:
        assert resp.status_code == 201
        assert list(resp.iter_lines()) == [b"data: [DONE]"]
    assert client.calls["method"] == "POST"
    assert client.calls["json"] == {"p": 1}
    assert (client.calls["timeout"].read, client.calls["timeout"].connect) == (22, 11)


def test_default_timeouts_match_stage_defaults(fresh_client):
    """Дефолты транспорта = дефолты stream_chat_completion (300/900)."""
    assert T.DEFAULT_CONNECT_TIMEOUT == 300.0
    assert T.DEFAULT_READ_TIMEOUT == 900.0


def test_reset_client_closes(monkeypatch, fresh_client):
    class Closable:
        closed = False

        def close(self):
            Closable.closed = True

    obj = Closable()
    T.reset_client()
    T._state.update(backend="requests", module=_fake_requests(), client=obj)
    T.reset_client()
    assert obj.closed is True
    assert T._state["client"] is None


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
def sse_server(srv_port):  # noqa: F811 — фикстура conftest (свободный порт)
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


@pytest.mark.parametrize("name", ["requests", "httpx"])
def test_live_sse_roundtrip(name, sse_server, monkeypatch, fresh_client):
    """Живой SSE: payload/заголовки доходят, строки режутся по \\n."""
    url, cfg = sse_server
    cfg["lines"] = [_sse_line("при"), b"", _sse_line("вет", "stop")]
    _live_backend(name)
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


@pytest.mark.parametrize("name", ["requests", "httpx"])
def test_live_error_status_and_retry_after(name, sse_server, monkeypatch,
                                           fresh_client):
    """Нечётные коды не проглатываются: статус и Retry-After видны выше."""
    url, cfg = sse_server
    cfg["status"], cfg["extra"] = 429, {"Retry-After": "7"}
    _live_backend(name)
    status, retry_after = 0, None
    with T.open_stream(url, payload={}) as resp:
        status, retry_after = resp.status_code, resp.headers.get("Retry-After")
    assert status == 429
    assert retry_after == "7"

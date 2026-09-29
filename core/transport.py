#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transport.py — единая HTTP-доставка LLM-запросов (одна точка выхода в сеть).

Внешних библиотек в проекте сознательно мало; эта — единственная, которая
ходит в сеть. Все стадии зовут `core.common.stream_chat_completion`, а она —
этот модуль. Здесь решается только «кем ходить»:

* ``httpx`` — основной транспорт: один клиент на процесс, переиспользование
  соединений (пул) на сотни запросов конвейера, раздельные connect/write/read
  таймауты;
* ``requests`` — фолбэк: ставится почти везде, API почти идентичен.

Контракт для вызывающего ОДИН (см. `open_stream`), поэтому гигиена стрима
([DONE]/finish_reason/loop/cut/empty/min_len_ratio) и политика ретраев живут
в `core.common` и от бэкенда не зависят. Исключения бэкендов нормализуются к
классам `TransportError` — выше по стеку про httpx/requests не вспоминают.

Своего SSE-парсера и своего пула соединений в скриптах быть не может
(архитектура-страж: `requests.post(` в `cli/` запрещён).
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from importlib.util import find_spec
from typing import Any

# Таймауты по умолчанию — те же числа, что у stream_chat_completion:
# connect — сколько ждать установку соединения, read — паузу между байтами
# уже открытого стрима (генерация длинного перевода молчит минутами).
DEFAULT_CONNECT_TIMEOUT = 300.0
DEFAULT_READ_TIMEOUT = 900.0
# Величина пула переиспользуемых соединений: конвейер держит один хост,
# параллельных запускающих потоков столько же, сколько глав в работе.
POOL_MAX_CONNECTIONS = 32

__all__ = [
    "TransportError", "ConnectTimeout", "ReadTimeout", "BrokenStream",
    "ResponseStream", "open_stream", "backend", "installed_backends",
    "reset_client",
]


# ══════════════════════════════════════════════════════════════════════
# Нормализованные ошибки (вызывающий не различает библиотеки)
# ══════════════════════════════════════════════════════════════════════
class TransportError(Exception):
    """Ошибка HTTP-доставки; общий предок остальных."""


class ConnectTimeout(TransportError):
    """Соединение не установлено за connect_timeout (сервер лежит)."""


class ReadTimeout(TransportError):
    """Соединение есть, но ответ не читается за read_timeout."""


class BrokenStream(TransportError):
    """Обрыв тела ответа (chunked/SSE оборвался до конца)."""


# ══════════════════════════════════════════════════════════════════════
# Контракт ответа
# ══════════════════════════════════════════════════════════════════════
class ResponseStream:
    """Стрим-ответ: `status_code`, `headers`, `iter_lines()` — байтовые
    строки по одной SSE-строке (без `\\n`). Контекстный менеджер закрывает
    соединение при выходе — в том числе при `break` в середине стрима."""

    status_code: int = 0
    headers: Any = None

    def __enter__(self) -> ResponseStream:
        return self

    def __exit__(self, exc_type: object = None, exc_value: object = None,
                 traceback: object = None) -> bool:
        return False

    def _chunks(self) -> Iterator[bytes]:
        raise NotImplementedError

    def iter_lines(self) -> Iterator[bytes]:
        """Байтовые строки тела по `\\n` (\\r отрезается); пустые строки
        отдаются как есть — их пропускает читатель."""
        buf = b""
        for chunk in self._chunks():
            if not chunk:
                continue
            buf += chunk
            while True:
                head, sep, rest = buf.partition(b"\n")
                if not sep:
                    break
                yield head.rstrip(b"\r")
                buf = rest
        if buf:
            # хвост без завершающего \n (стрим оборвался или кончился)
            yield buf.rstrip(b"\r")


# ══════════════════════════════════════════════════════════════════════
# Бэкенды
# ══════════════════════════════════════════════════════════════════════
class _HttpxStream(ResponseStream):
    """Обёртка над `client.stream(...)` (контекстный менеджер httpx)."""

    def __init__(self, cm: Any, httpx_mod: Any) -> None:
        self._cm = cm
        self._m = httpx_mod

    def __enter__(self) -> ResponseStream:
        self._resp = self._cm.__enter__()
        self.status_code = self._resp.status_code
        self.headers = self._resp.headers
        return self

    def __exit__(self, exc_type: object = None, exc_value: object = None,
                 traceback: object = None) -> bool:
        return bool(self._cm.__exit__(exc_type, exc_value, traceback))

    def _chunks(self) -> Iterator[bytes]:
        m = self._m
        try:
            # iter_bytes — декодированное тело (gzip/deflate уже разобраны)
            yield from self._resp.iter_bytes()
        except m.ConnectTimeout as exc:
            raise ConnectTimeout(str(exc)) from exc
        except m.ReadTimeout as exc:
            raise ReadTimeout(str(exc)) from exc
        except m.TimeoutException as exc:
            raise ReadTimeout(str(exc)) from exc
        except (m.RemoteProtocolError, m.ReadError) as exc:
            raise BrokenStream(str(exc)) from exc
        except m.HTTPError as exc:
            raise TransportError(str(exc)) from exc


class _RequestsStream(ResponseStream):
    """Обёртка над `Response` requests (он сам контекстный менеджер)."""

    def __init__(self, resp: Any, requests_mod: Any) -> None:
        self._resp = resp
        self._m = requests_mod

    def __enter__(self) -> ResponseStream:
        self._resp.__enter__()
        self.status_code = self._resp.status_code
        self.headers = self._resp.headers
        return self

    def __exit__(self, exc_type: object = None, exc_value: object = None,
                 traceback: object = None) -> bool:
        return bool(self._resp.__exit__(exc_type, exc_value, traceback))

    def _chunks(self) -> Iterator[bytes]:
        m = self._m
        try:
            yield from self._resp.iter_content(chunk_size=65536)
        except m.exceptions.ReadTimeout as exc:
            raise ReadTimeout(str(exc)) from exc
        except m.exceptions.Timeout as exc:
            # ConnectTimeout — подкласс Timeout: «соединения нет» тоже сюда
            raise ConnectTimeout(str(exc)) from exc
        except m.exceptions.ChunkedEncodingError as exc:
            raise BrokenStream(str(exc)) from exc
        except m.exceptions.RequestException as exc:
            raise TransportError(str(exc)) from exc


# ══════════════════════════════════════════════════════════════════════
# Выбор и инициализация бэкенда (лениво, один клиент на процесс)
# ══════════════════════════════════════════════════════════════════════
_lock = threading.Lock()
_state: dict[str, Any] = {"backend": None, "module": None, "client": None}


def installed_backends() -> list[str]:
    """Какие HTTP-библиотеки вообще установлены (в порядке предпочтения)."""
    return [n for n in ("httpx", "requests") if find_spec(n) is not None]


def backend() -> str:
    """Имя активного бэкенда: `httpx`, если он установлен, иначе `requests`."""
    with _lock:
        if _state["backend"] is None:
            _state["backend"] = _detect()
    return _state["backend"]


def _detect() -> str:
    """Пробует httpx, затем requests; ничего нет — ошибка один раз, loudly.
    Кандидаты перебираются тем же `find_spec`, что и `installed_backends`
    (один источник истины «что установлено»), импорт — только победителю."""
    for name in ("httpx", "requests"):
        if find_spec(name) is None:
            continue
        try:
            _state["module"] = __import__(name)
        except ImportError:
            continue
        return name
    raise TransportError(
        "Нет HTTP-библиотеки: pip install httpx requests (или хотя бы requests)")


def _client() -> Any:
    """Общий клиент процесса (пул соединений). Создаётся при первом запросе."""
    with _lock:
        if _state["client"] is None:
            name, module = _state["backend"], _state["module"]
            if name == "httpx":
                _state["client"] = module.Client(
                    timeout=module.Timeout(DEFAULT_READ_TIMEOUT,
                                          connect=DEFAULT_CONNECT_TIMEOUT,
                                          write=DEFAULT_READ_TIMEOUT,
                                          pool=DEFAULT_CONNECT_TIMEOUT),
                    limits=module.Limits(
                        max_connections=POOL_MAX_CONNECTIONS,
                        max_keepalive_connections=POOL_MAX_CONNECTIONS),
                )
            else:
                adapter = module.adapters.HTTPAdapter(
                    pool_connections=POOL_MAX_CONNECTIONS,
                    pool_maxsize=POOL_MAX_CONNECTIONS,
                    # ретраи — политика core.common (H3: только 408/425/429/5xx);
                    # молчаливые транспортные повторы только портят статистику
                    max_retries=0,
                )
                session = module.Session()
                session.mount("http://", adapter)
                session.mount("https://", adapter)
                _state["client"] = session
        return _state["client"]


def reset_client() -> None:
    """Сбросить общий клиент (тесты; в рантайме не нужен)."""
    with _lock:
        client = _state["client"]
        _state["client"] = None
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001 — сброс не должен падать
            pass


def open_stream(url: str, *, headers: dict | None = None, payload: Any = None,
                connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
                read_timeout: float = DEFAULT_READ_TIMEOUT) -> ResponseStream:
    """POST JSON со стримом ответа. Возвращает контекстный менеджер
    `ResponseStream` (`status_code`, `headers`, `iter_lines()`).

    Таймауты передаются на запрос: у стадий они свои (`--timeout` /
    `--stream_timeout`), клиент создаётся с дефолтами и переиспользуется.
    """
    name, module = backend(), _state["module"]
    client = _client()
    if name == "httpx":
        timeout = module.Timeout(read_timeout, connect=connect_timeout,
                                 write=read_timeout, pool=connect_timeout)
        return _HttpxStream(
            client.stream("POST", url, headers=headers or {}, json=payload,
                         timeout=timeout),
            module)
    return _RequestsStream(
        client.post(url, headers=headers or {}, json=payload, stream=True,
                    timeout=(connect_timeout, read_timeout)),
        module)


def main(argv: list[str] | None = None) -> int:
    """Отладка: какая HTTP-библиотека активна и что вообще установлено."""
    print(f"HTTP-транспорт LLM: {backend()} "
          f"(установлено: {', '.join(installed_backends())})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

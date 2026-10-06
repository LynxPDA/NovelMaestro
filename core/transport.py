#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transport.py — единственная точка выхода в сеть: httpx.

HTTP в проекте ходит отсюда и больше ниоткуда: стадии зовут
`core.common.stream_chat_completion`, а она — `open_stream()` этого модуля.
Библиотека одна, второй «запасной клиент» не нужен: он держал бы второй адаптер,
второй путь ошибок и второй набор тестов ради поведения, которое вызывающий всё
равно не видит.

Что даёт httpx и ради чего он здесь единственный:

* один клиент на процесс = переиспользование соединений — конвейер бьёт сотнями
  запросов в один хост, раньше на каждый уходил handshake;
* раздельные connect/write/read таймауты вместо одного числа «на всё»;

Контракт вызывающего: `ResponseStream` (`status_code`, `headers`,
`iter_lines()` — байтовые строки SSE без терминатора). Гигиена стрима
([DONE]/finish_reason/loop/cut/empty/min_len_ratio) и политика ретраев живут в
`core.common` и от транспорта не зависят. Прямой `import httpx` вне этого модуля
запрещён (страж `tests/test_architecture.py`).
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

import httpx

BACKEND = "httpx"

# Таймауты по умолчанию — те же числа, что у stream_chat_completion: connect —
# сколько ждать установку соединения, read — паузу между байтами уже открытого
# стрима (генерация длинного перевода молчит минутами).
DEFAULT_CONNECT_TIMEOUT = 300.0
DEFAULT_READ_TIMEOUT = 900.0
# Величина пула переиспользуемых соединений: конвейер держит один хост,
# параллельных запускающих потоков столько же, сколько глав в работе.
POOL_MAX_CONNECTIONS = 32

__all__ = [
    "BACKEND", "DEFAULT_CONNECT_TIMEOUT", "DEFAULT_READ_TIMEOUT",
    "POOL_MAX_CONNECTIONS", "TransportError", "ConnectTimeout", "ReadTimeout",
    "BrokenStream", "ResponseStream", "client", "open_stream", "open_get",
    "reset_client",
]


# ══════════════════════════════════════════════════════════════════════
# Нормализованные ошибки (про библиотеку выше по стеку не помнят)
# ══════════════════════════════════════════════════════════════════════
class TransportError(Exception):
    """Ошибка HTTP-доставки; общий предок остальных."""


class ConnectTimeout(TransportError):
    """Соединение не установлено: таймаут connect, отказ, недоступен хост."""


class ReadTimeout(TransportError):
    """Соединение есть, но ответ не читается за read_timeout (и нет соединения
    в пуле — это тоже «ждём сервер»)."""


class BrokenStream(TransportError):
    """Обрыв тела ответа (chunked/SSE оборвался до конца, битый gzip/декодер)."""


def _normalize(exc: Exception) -> TransportError:
    """Исключение httpx → наш класс. Порядок важен: ConnectTimeout — потомок
    и ConnectError, и TimeoutException, поэтому проверяется первым."""
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ConnectError)):
        return ConnectTimeout(str(exc))
    if isinstance(exc, httpx.TimeoutException):
        return ReadTimeout(str(exc))
    if isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError)):
        return BrokenStream(str(exc))
    return TransportError(str(exc))


# ══════════════════════════════════════════════════════════════════════
# Контракт ответа
# ══════════════════════════════════════════════════════════════════════
class ResponseStream:
    """Стрим-ответ: `status_code`, `headers`, `iter_lines()` — байтовые строки
    по одной SSE-строке (без `\\n`). Контекстный менеджер закрывает соединение
    при выходе — в том числе при `break` в середине стрима."""

    status_code: int = 0
    headers: Any = None

    def __init__(self, cm: Any) -> None:
        self._cm = cm

    def __enter__(self) -> ResponseStream:
        # запрос уходит здесь: «сервер лежит» прилетает именно на входе
        try:
            self._resp = self._cm.__enter__()
        except Exception as exc:  # noqa: BLE001 — нормализуем, не поглощаем
            raise _normalize(exc) from exc
        self.status_code = self._resp.status_code
        self.headers = self._resp.headers
        return self

    def __exit__(self, exc_type: object = None, exc_value: object = None,
                 traceback: object = None) -> bool:
        try:
            return bool(self._cm.__exit__(exc_type, exc_value, traceback))
        except Exception as exc:  # noqa: BLE001 — закрытие тоже наш случай
            raise _normalize(exc) from exc

    def iter_lines(self) -> Iterator[bytes]:
        """Тело по строкам: `\\n` — граница, `\\r` отрезается, пустые строки
        отдаются как есть (пустая строка SSE = событие закончилось)."""
        buf = b""
        try:
            for chunk in self._resp.iter_bytes():  # декодированное тело
                if not chunk:
                    continue
                buf += chunk
                while True:
                    head, sep, rest = buf.partition(b"\n")
                    if not sep:
                        break
                    yield head.rstrip(b"\r")
                    buf = rest
        except Exception as exc:  # noqa: BLE001 — нормализуем, не поглощаем
            raise _normalize(exc) from exc
        if buf:
            # хвост без завершающего \n (стрим оборвался или кончился)
            yield buf.rstrip(b"\r")


# ══════════════════════════════════════════════════════════════════════
# Общий клиент (лениво, один на процесс)
# ══════════════════════════════════════════════════════════════════════
_lock = threading.Lock()
_client: Any = None


def client() -> Any:
    """Общий `httpx.Client`: пул соединений создаётся при первом запросе."""
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(
                timeout=httpx.Timeout(
                    DEFAULT_READ_TIMEOUT,
                    connect=DEFAULT_CONNECT_TIMEOUT,
                    write=DEFAULT_READ_TIMEOUT,
                    pool=DEFAULT_CONNECT_TIMEOUT),
                limits=httpx.Limits(
                    max_connections=POOL_MAX_CONNECTIONS,
                    max_keepalive_connections=POOL_MAX_CONNECTIONS),
            )
        return _client


def reset_client() -> None:
    """Сбросить общий клиент (тесты; в рантайме не нужен)."""
    global _client
    with _lock:
        stale, _client = _client, None
    if stale is not None:
        try:
            stale.close()
        except Exception:  # noqa: BLE001 — сброс не должен падать
            pass


def open_stream(url: str, *, headers: dict | None = None, payload: Any = None,
                connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
                read_timeout: float = DEFAULT_READ_TIMEOUT) -> ResponseStream:
    """POST JSON со стримом ответа. Возвращает контекстный менеджер
    `ResponseStream` (`status_code`, `headers`, `iter_lines()`).

    Таймауты передаются на запрос: у стадий они свои (`--timeout` /
    `--stream_timeout`); клиент переиспользуется.
    """
    timeout = httpx.Timeout(read_timeout, connect=connect_timeout,
                            write=read_timeout, pool=connect_timeout)
    return ResponseStream(
        client().stream("POST", url, headers=headers or {}, json=payload,
                        timeout=timeout))


def open_get(url: str, *, headers: dict | None = None,
             timeout: float = 15.0) -> ResponseStream:
    """GET с коротким таймаутом: проверка доступности сервера (`/v1/models`).

    Отдача та же, что у рабочего запроса: общий клиент, те же нормализованные
    ошибки. Проверка обязана ходить ровно тем же путём, которым пойдёт работа,
    иначе «зелёная галочка» и падающий конвейер могут быть разные серверы.
    Тело читается целиком — список моделей маленький, стрим тут не нужен.
    """
    tm = httpx.Timeout(timeout, connect=timeout, write=timeout, pool=timeout)
    return ResponseStream(
        client().stream("GET", url, headers=headers or {}, timeout=tm))


def main(argv: list[str] | None = None) -> int:
    """Отладка: какой транспорт активен и с какими настройками пула."""
    print(f"HTTP-транспорт LLM: {BACKEND} "
          f"(пул {POOL_MAX_CONNECTIONS}, connect {DEFAULT_CONNECT_TIMEOUT}s, "
          f"read {DEFAULT_READ_TIMEOUT}s)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

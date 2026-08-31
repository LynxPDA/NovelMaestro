#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
multipart.py — парсер multipart/form-data без cgi (удалён в Python 3.13).

Потоковый: iter_parts() читает тело чанками из любого «read(n)»
источника — файлы не копируются в память целиком (важно при
--max-upload-mb в сотни мегабайт). Boundary признаётся только на
границе строки (перед ним CRLF/LF, после — '--'|CRLF|LF), а не по
случайному совпадению байтов внутри бинарного файла. parse_multipart()
— обёртка для байтового тела (прежний контракт; тесты).
"""
from __future__ import annotations

import io

# Заголовки поля крупнее — битое/злонамеренное тело
MAX_HEADER_BYTES = 65536


class MultipartError(Exception):
    """Некорректное multipart-тело."""


def parse_disposition(value: str) -> dict[str, str]:
    """Разбор Content-Disposition: 'form-data; name="x"; filename="y.txt"'."""
    out: dict[str, str] = {}
    for part in value.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip().lower()] = v.strip().strip('"')
        else:
            out["type"] = part
    return out


class _MultipartParser:
    """Потоковый разбор: начальная граница, заголовки и данные полей."""

    def __init__(self, stream, boundary: str) -> None:
        if not boundary:
            raise MultipartError("Отсутствует boundary")
        self.stream = stream
        self.delim = b"--" + boundary.encode("utf-8")
        self.buf = b""
        self.done = False
        # первый boundary в начале тела (преамбула не поддерживается)
        while len(self.buf) < len(self.delim) + 2:
            chunk = self.stream.read(65536)
            if not chunk:
                break
            self.buf += chunk
        if not self.buf.startswith(self.delim):
            raise MultipartError("Тело не начинается с boundary")
        rest = self.buf[len(self.delim):]
        if rest.startswith(b"--"):
            self.done = True            # пустая форма: --boundary--…
        elif rest.startswith(b"\r\n"):
            rest = rest[2:]
        elif rest.startswith(b"\n"):
            rest = rest[1:]
        else:
            raise MultipartError("boundary не закрыт переводом строки")
        self.buf = rest

    def _more(self) -> bool:
        """Дочитать следующий блок; True — есть данные."""
        chunk = self.stream.read(65536)
        if chunk:
            self.buf += chunk
            return True
        return False

    def next_headers(self) -> dict[str, str]:
        """Заголовки следующего поля (данные предыдущего уже вычитаны)."""
        while True:
            if len(self.buf) > MAX_HEADER_BYTES:
                raise MultipartError("Заголовки поля слишком большие")
            i = self.buf.find(b"\r\n\r\n")
            j = self.buf.find(b"\n\n")
            if i >= 0 and (j < 0 or i <= j):
                head, self.buf = self.buf[:i], self.buf[i + 4:]
                break
            if j >= 0:
                head, self.buf = self.buf[:j], self.buf[j + 2:]
                break
            if not self._more():
                raise MultipartError("Поле без заголовков: тело оборвано")
        headers: dict[str, str] = {}
        for line in head.decode("utf-8", errors="replace").splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
        return headers

    def iter_data(self):
        """Чанки данных текущего поля до следующего валидного boundary.

        После закрывающего boundary (--после него) поток помечается
        завершённым; незакрытое тело (EOF) терпимо отдаёт остаток.
        """
        d = self.delim
        while True:
            if self.done:
                return
            pos = 0
            while True:
                idx = self.buf.find(d, pos)
                if idx < 0:
                    break
                # для суждения о валидности нужны 2 байта после boundary
                if len(self.buf) - (idx + len(d)) < 2:
                    if not self._more():
                        break
                    continue
                before2 = self.buf[idx - 2:idx] if idx >= 2 else b""
                after2 = self.buf[idx + len(d):idx + len(d) + 2]
                lined = idx == 0 or before2 == b"\r\n" \
                    or before2[-1:] == b"\n"
                follows = after2 == b"--" or after2 == b"\r\n" \
                    or after2[:1] == b"\n"
                if lined and follows:
                    end = idx - 2 if before2 == b"\r\n" else (
                        idx - 1 if idx > 0 and before2[-1:] == b"\n" else idx)
                    if end > 0:
                        yield self.buf[:end]
                    final = after2 == b"--"
                    cut = idx + len(d) + (0 if final else
                                          (2 if after2 == b"\r\n" else 1))
                    self.buf = self.buf[cut:]
                    if final:
                        self.done = True
                    return
                pos = idx + 1
            # валидной границы нет: держим хвост под возможную границу,
            # остальное — данные (потоковая отдача крупных файлов)
            keep = len(d) + 2
            if len(self.buf) > keep:
                yield self.buf[:-keep]
                self.buf = self.buf[-keep:]
            if not self._more():
                if self.buf:
                    yield self.buf
                    self.buf = b""
                self.done = True
                return


def iter_parts(stream, boundary: str):
    """Поля multipart-потока: (заголовки, генератор чанков данных).

    Данные поля обязаны быть вычитаны до запроса следующего; порядок
    полей — как в теле. Пустая форма (сразу boundary--) — ноль полей.
    """
    parser = _MultipartParser(stream, boundary)
    while not parser.done:
        headers = parser.next_headers()
        yield headers, parser.iter_data()


def parse_multipart(body: bytes, boundary: str) -> list[dict]:
    """Разбор байтового тела в список полей (прежний контракт).

    Каждый элемент: {"name", "filename", "content_type", "data"}; порядок
    — как в теле. Для крупных тел — iter_parts(), файлы не грузятся в
    память целиком.
    """
    fields: list[dict] = []
    for headers, data in iter_parts(io.BytesIO(body), boundary):
        disp = parse_disposition(headers.get("content-disposition", ""))
        fields.append({
            "name": disp.get("name", ""),
            "filename": disp.get("filename"),
            "content_type": headers.get("content-type", ""),
            "data": b"".join(data),
        })
    return fields


def extract_files(fields: list[dict]) -> list[dict]:
    """Только файловые поля (filename задан) из разобранного multipart."""
    return [f for f in fields if f.get("filename")]


def extract_value(fields: list[dict], name: str) -> str:
    """Значение текстового поля ('' если нет)."""
    for f in fields:
        if f.get("name") == name and not f.get("filename"):
            return f["data"].decode("utf-8", errors="replace").strip()
    return ""


def extract_dest(fields: list[dict]) -> str:
    """Значение поля dest ('' если нет) — папка назначения upload."""
    return extract_value(fields, "dest")


def extract_other(fields: list[dict], name: str) -> str:
    """Алиас extract_value: значение текстового поля по имени."""
    return extract_value(fields, name)
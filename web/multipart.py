#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
multipart.py — парсер multipart/form-data без cgi (удалён в Python 3.13).

Поддерживает text/plain и file-поля; файлы читаются целиком в память
(лимит на стороне вызывающего — max_upload_mb). Только stdlib.
"""
from __future__ import annotations


class MultipartError(Exception):
    """Некорректное multipart-тело."""


def _parse_disposition(value: str) -> dict[str, str]:
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


def parse_multipart(body: bytes, boundary: str) -> list[dict]:
    """Разбирает multipart-тело в список полей.

    Каждый элемент: {"name": str, "filename": str | None,
    "content_type": str, "data": bytes}. Порядок — как в теле.
    """
    if not boundary:
        raise MultipartError("Отсутствует boundary")
    delim = b"--" + boundary.encode("utf-8")
    if not body.startswith(delim):
        raise MultipartError("Тело не начинается с boundary")
    if body.startswith(delim + b"--"):
        return []
    parts: list[bytes] = []
    idx = 0
    while True:
        start = body.find(delim, idx)
        if start < 0:
            break
        end = body.find(delim, start + len(delim))
        if end < 0:
            end = len(body)
        raw = body[start + len(delim):end]
        if raw.endswith(b"--"):
            raw = raw[:-2]
        if raw.endswith(b"\r\n"):
            raw = raw[:-2]
        elif raw.endswith(b"\n"):
            raw = raw[:-1]
        parts.append(raw)
        if end >= len(body):
            break
        idx = end
    fields: list[dict] = []
    for raw in parts:
        if b"\r\n\r\n" in raw:
            head, _, data = raw.partition(b"\r\n\r\n")
        elif b"\n\n" in raw:
            head, _, data = raw.partition(b"\n\n")
        else:
            head, data = raw, b""
        headers: dict[str, str] = {}
        for line in head.decode("utf-8", errors="replace").splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
        disp = _parse_disposition(headers.get("content-disposition", ""))
        name = disp.get("name", "")
        filename = disp.get("filename")
        fields.append({
            "name": name,
            "filename": filename,
            "content_type": headers.get("content-type", ""),
            "data": data,
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

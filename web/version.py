#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
version.py — версия NovelMaestro, единый источник для API, HTTP-сервера и UI.

Приоритет: NOVELMAESTRO_VERSION (окружение) → файл VERSION в корне репо
(обновляется при релизе вместе с CHANGELOG.md) → встроенный фолбэк.
"""
from __future__ import annotations

import os
from pathlib import Path

_FALLBACK = "0.2.9"
_app_version: str | None = None


def app_version() -> str:
    """Версия сборки (кэшируется при первом вызове)."""
    global _app_version
    if _app_version is None:
        v = os.environ.get("NOVELMAESTRO_VERSION", "").strip()
        if not v:
            p = Path(__file__).resolve().parent.parent / "VERSION"
            try:
                v = p.read_text(encoding="utf-8").strip()
            except OSError:
                v = ""
        _app_version = v or _FALLBACK
    return _app_version

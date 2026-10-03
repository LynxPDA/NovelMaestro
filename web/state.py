#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
state.py — состояние web-бэкэнда на диске.

hub_state: projects/.hub_state.json — последний раздел/проект пульта. Файл лежит
в gitignored projects/ и переживает пересоздание контейнера.

Чего здесь нет: история запусков пишется в jobs.py (jobs.json внутри job_logs/),
а параметры форм запусков живут в localStorage браузера: слой книжных .env и
form_state убраны как второй слой тех же значений.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("web")

HUB_STATE_NAME = ".hub_state.json"


def load_hub_state(projects_root: Path) -> dict:
    """Последний раздел/проект (общий с cli). Никогда не бросает."""
    try:
        data = json.loads(
            (projects_root / HUB_STATE_NAME).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:
        log.debug("hub_state не читается: %s", exc)
    return {}


def save_hub_state(projects_root: Path, state: dict) -> None:
    """Пишет hub_state; ошибки записи проглатываются (не критично)."""
    try:
        f = projects_root / HUB_STATE_NAME
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except OSError as exc:
        log.debug("hub_state не пишется: %s", exc)

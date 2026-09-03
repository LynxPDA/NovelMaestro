#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
state.py — состояние web-бэкэнда.

- hub_state: projects/.hub_state.json — ОБЩИЙ с cli-пультом (раздел/проект);
- form_state: projects/.web_form_state.json — «повторить прошлый запуск»
  по (project, action) → последние параметры формы;
- jobs.json — история запусков (см. jobs.py).
Все три файла в gitignored папках (projects/, logs/).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("web")

HUB_STATE_NAME = ".hub_state.json"
FORM_STATE_NAME = ".web_form_state.json"


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


def load_form_state(projects_root: Path) -> dict:
    """{project: {action: form}} — последние параметры форм."""
    try:
        data = json.loads(
            (projects_root / FORM_STATE_NAME).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:
        log.debug("form_state не читается: %s", exc)
    return {}


def save_form_state(projects_root: Path, project: str,
                    action: str, form: dict) -> None:
    """Сохраняет параметры запуска (без секретов — только поля формы)."""
    try:
        f = projects_root / FORM_STATE_NAME
        f.parent.mkdir(parents=True, exist_ok=True)
        data = load_form_state(projects_root)
        data.setdefault(project, {})[action] = form
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except OSError as exc:
        log.debug("form_state не пишется: %s", exc)


def jobs_file(web_dir: Path) -> Path:
    """Путь к jobs.json (история запусков).

    Внутри job_logs/ — в docker этот каталог смонтирован в volume,
    иначе при пересоздании контейнера история теряется."""
    return web_dir / "job_logs" / "jobs.json"


def load_jobs(web_dir: Path) -> list[dict]:
    """История запусков из jobs.json; пустой список при ошибке."""
    try:
        data = json.loads(jobs_file(web_dir).read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception as exc:
        log.debug("jobs.json не читается: %s", exc)
    return []


def save_jobs(web_dir: Path, jobs: list[dict]) -> None:
    """Сохраняет историю запусков."""
    try:
        f = jobs_file(web_dir)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(jobs, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except OSError as exc:
        log.debug("jobs.json не пишется: %s", exc)

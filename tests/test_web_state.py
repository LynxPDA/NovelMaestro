#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты web/state.py — диск state'а web-бэкэнда (hub_state).

hub_state — единственный файл, который модуль теперь ведёт: последний раздел и
проект пульта. Проверяем обе половины контракта: читатель не бросает ни на
каком мусоре (нет файла, битый JSON, не-словарь), а писатель терпит ошибки
записи — состояние пульта не то, ради чего роняет запрос.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from web import state  # noqa: E402

STATE = {"section": "ACTIVE", "project": "Книга"}


def test_hub_state_roundtrip(tmp_path):
    """Записали — прочитали: значения и кириллица без \\u-экранирования."""
    state.save_hub_state(tmp_path, STATE)
    raw = (tmp_path / state.HUB_STATE_NAME).read_text(encoding="utf-8")
    assert "Книга" in raw
    assert state.load_hub_state(tmp_path) == STATE


def test_hub_state_overwrite(tmp_path):
    """Последняя запись побеждает: файл один, накопления нет."""
    state.save_hub_state(tmp_path, {"section": "HOLD"})
    state.save_hub_state(tmp_path, STATE)
    assert state.load_hub_state(tmp_path) == STATE


def test_hub_state_creates_root(tmp_path):
    """Корня projects/ ещё нет — писатель его создаёт."""
    root = tmp_path / "projects"
    state.save_hub_state(root, STATE)
    assert state.load_hub_state(root) == STATE


@pytest.mark.parametrize("body", ["", "   ", "{", "не json", "[]", "null",
                                  "42", '"строка"'])
def test_load_hub_state_gives_empty_on_junk(tmp_path, body):
    """Пусто, мусор и «не словарь» — пустой dict, без исключения."""
    (tmp_path / state.HUB_STATE_NAME).write_text(body, encoding="utf-8")
    assert state.load_hub_state(tmp_path) == {}


def test_load_hub_state_missing_file(tmp_path):
    assert state.load_hub_state(tmp_path / "нет-такого") == {}


def test_save_hub_state_swallows_oserror(tmp_path):
    """Не пиется — не падает: на пути каталога файл (NotADirectoryError)."""
    blocker = tmp_path / "file"
    blocker.write_text("blocker", encoding="utf-8")
    state.save_hub_state(blocker, STATE)          # не бросает
    assert state.load_hub_state(blocker) == {}


def test_hub_state_name_is_dotfile(tmp_path):
    """Имя файла канонично: точка в начале, в gitignored projects/."""
    assert state.HUB_STATE_NAME == ".hub_state.json"

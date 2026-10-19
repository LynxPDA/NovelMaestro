#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/uistate.py — состояние книги на диске: параметры форм, профили стадий,
чипсы и место работы во вкладках.

Состояние интерфейса делится по природе данных: **что описывает книгу** (какие
значения у стадий, чем запускать перевод, где человек остановился во вкладках)
лежит в книге — `tmp/ui_state/`; **что описывает устройство** (тема, кегль,
язык подсветки) остаётся в браузере. Смена браузера, домена или переезд с
телефона на десктоп книжное состояние больше не теряет, а копия книги
наследует его вместе с `tmp/`.

Файлов в каталоге несколько, а не один: у каждого браузера своё состояние, и
общий файл заставлял бы устройства затирать друг друга без следа. `id` — cookie
устройства, выдаётся сервером при первом чтении; `pending.json` — состояние,
не привязанное к cookie (первый визит, старый клиент). Чтение по умолчанию
сливает свежие файлы: устройство, вернувшееся через месяц, получает то, что
оставили другие, а не свою заброшенную копию. Ничего своего у состояния нет —
это словарь ключей интерфейса (`tab`, `runs`, `nerCols`, …), поэтому слой не
знает ни одного имени поля.

`tmp/` исключён из истории контрольных точек (web/history.py): состояние — не
содержимое книги, и возврат к точке не должен откатывать вкладки.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from .common import atomic_write, read_text_safe

__all__ = [
    "UI_STATE_DIR", "UI_STATE_VERSION", "UI_STATE_KEEP", "UI_STATE_STALE",
    "UI_STATE_DEVICE_COOKIE", "ui_state_dir", "ui_state_device",
    "ui_state_read", "ui_state_write", "ui_state_prune",
]

#: каталог состояния внутри проекта (tmp/ — рабочие файлы книги)
UI_STATE_DIR = Path("tmp") / "ui_state"
#: версия формата: поле `version` пишется в каждый файл, подъём сюда сбрасывает
#: кеш на клиенте, а не роняет чтение (незнакомая версия читается как есть)
UI_STATE_VERSION = 1
#: сколько файлов устройств держим в каталоге (хвост по mtime)
UI_STATE_KEEP = 10
#: состояние старше этого срока в слияние не попадает, сек (180 дней):
#: значения полугодовой давности всплывали бы сюрпризом
UI_STATE_STALE = 180 * 24 * 3600
#: cookie устройства (HttpOnly: клиенту id не нужен, сервер сам его читает)
UI_STATE_DEVICE_COOKIE = "nm_device"

# Имя файла устройства — только из наших символов: id приходит из cookie,
# и путь, собранный из него, не должен выйти из каталога состояния
_DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_PENDING = "pending"

# Сервер многопоточный: чтение-слияние-запись идут под общим замком, иначе два
# браузера теряли бы записи друг друга на read-modify-write.
_LOCK = threading.RLock()


def ui_state_dir(pdir: Path) -> Path:
    """Каталог состояния книги (рядом с рабочими файлами стадий)."""
    return Path(pdir) / UI_STATE_DIR


def ui_state_device(raw: str | None) -> str:
    """id устройства из cookie; пустое или чужое значение — новый id."""
    dev = (raw or "").strip()
    return dev if _DEVICE_RE.match(dev) else secrets.token_urlsafe(16)


def _file(ddir: Path, device: str) -> Path:
    name = device if device else _PENDING
    return ddir / f"{name}.json"


def _load(path: Path) -> dict:
    """Объект файла состояния; битый JSON, мусор и нет файла — пусто."""
    try:
        data = json.loads(read_text_safe(path))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _fresh(path: Path) -> bool:
    """Состояние файла не протухло (по mtime; часы диска — единственный закон)."""
    try:
        return time.time() - path.stat().st_mtime <= UI_STATE_STALE
    except OSError:
        return False


def ui_state_read(ddir: Path, device: str = "",
                  merge: bool = True) -> dict:
    """Состояние книги для устройства: {ключ: значение}.

    merge=True — поверх своего файла кладутся свежие файлы других устройств
    (свой важнее: то, что человек только что поменял здесь, не должно
    перебиваться тем, что осталось на другом устройстве). merge=False — только
    своё: так читает клиент, который собирается писать (чтобы не перетащить
    чужое в свой файл молча).
    """
    ddir = Path(ddir)
    with _LOCK:
        own = _load(_file(ddir, device))
        if not merge:
            return _strip(own)
        merged: dict = {}
        try:
            others = sorted((p for p in ddir.glob("*.json") if p.is_file()),
                            key=lambda p: p.stat().st_mtime)
        except OSError:
            others = []
        for path in others:
            if device and path.name == f"{device}.json":
                continue
            if not _fresh(path):
                continue
            merged = _merge(merged, _strip(_load(path)))
        return _merge(merged, _strip(own))


def ui_state_write(ddir: Path, device: str, payload: dict) -> None:
    """Сохранить состояние устройства (merge с уже лежащим на диске).

    Клиент держит состояние в памяти и присылает его целиком: **присланные
    скоупы заменяются**, а не сливаются — иначе нельзя было бы ничего сбросить
    (значение, возвращённое к дефолту конфига, так и осталось бы в файле). При
    чтении файлы устройств сливаются глубоко (`ui_state_read`): устройство,
    пришедшее после другого, видит его правки, а не пустые формы.
    """
    if not isinstance(payload, dict):
        raise ValueError("состояние интерфейса — JSON-объект")
    ddir = Path(ddir)
    with _LOCK:
        ddir.mkdir(parents=True, exist_ok=True)
        target = _file(ddir, device)
        merged = dict(_load(target))
        merged.update(_clean_state(payload))
        merged["version"] = UI_STATE_VERSION
        merged["updated"] = round(time.time(), 3)
        atomic_write(str(target), json.dumps(merged, ensure_ascii=False,
                                             indent=2))
        ui_state_prune(ddir)


def ui_state_prune(ddir: Path, keep: int = UI_STATE_KEEP) -> list[str]:
    """Оставить `keep` самых свежих файлов состояния; вернуть удалённые имена.

    `pending.json` удаляется наравне с прочими: после первой записи устройства
    он больше не нужен, а старое состояние без владельца копилось бы вечно.
    """
    ddir = Path(ddir)
    try:
        files = sorted((p for p in ddir.glob("*.json") if p.is_file()),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    dropped: list[str] = []
    for path in files[keep:]:
        try:
            path.unlink()
            dropped.append(path.name)
        except OSError:
            pass
    return dropped


def _strip(data: dict) -> dict:
    """Только ключи интерфейса: служебные поля формата в состояние не едут."""
    return {k: v for k, v in data.items() if k not in ("version", "updated")}


def _clean_state(state: dict) -> dict:
    """Глубина 2 и JSON-типы: клиент присылает {ключ: объект}, а мусор в теле
    запроса не должен ложиться в файл книги."""
    out: dict = {}
    for key, value in state.items():
        if not isinstance(key, str) or not key:
            continue
        if isinstance(value, dict):
            out[key] = {str(k): v for k, v in value.items()
                        if isinstance(k, (str, int))}
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
    return out


def _merge(base: dict, patch: dict) -> dict:
    """Слияние состояния: объект поверх объекта, скаляр заменяет скаляр."""
    out = dict(base)
    for key, value in patch.items():
        old = out.get(key)
        if isinstance(old, dict) and isinstance(value, dict):
            out[key] = _merge(old, value)
        else:
            out[key] = value
    return out


def _selfcheck() -> None:  # pragma: no cover — ручная проверка слоя
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ddir = ui_state_dir(Path(d))
        ui_state_write(ddir, "dev1", {"runs": {"ner": {"chunk_size": "1"}}})
        ui_state_write(ddir, "dev2", {"runs": {"wiki": {"top": "80"}}})
        got = ui_state_read(ddir, "dev1")
        assert got["runs"]["ner"]["chunk_size"] == "1", got
        assert got["runs"]["wiki"]["top"] == "80", got
        print("ui_state ok:", sorted(p.name for p in ddir.iterdir()))


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()

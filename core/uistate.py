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
сливает свежие файлы, а внутри ключа побеждает запись, сделанная позже в
абсолютном времени: устройство, вернувшееся через месяц, получает актуальное
состояние книги, а не свою заброшенную копию и не чужие старые значения поверх
своих. Ничего своего у состояния нет — это словарь ключей интерфейса (`tab`,
`runs`, `nerCols`, …), поэтому слой не знает ни одного имени поля.

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


def _mtime(path: Path) -> float:
    """mtime файла (0 — нет файла); нужен только для ничьей в слиянии."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _file_stamp(path: Path, data: dict) -> float:
    """Время записи файла: `updated` из данных, иначе mtime.

    Только по mtime ранжировать нельзя: копирование, перенос книги или правка
    часов делают свежим файл, чьё содержимое старше, и значение, записанное
    раньше, перебивало бы позднее (диапазон глав одной стадии показывался на
    двух устройствах по-разному). Файлы прежней версии без отметки читаются по
    mtime — состояние, оставленное до обновления, не теряется."""
    try:
        stamp = float(data.get("updated") or 0)
    except (TypeError, ValueError):
        stamp = 0.0
    return stamp if stamp > 0 else _mtime(path)


def _fresh(path: Path) -> bool:
    """Состояние файла не протухло (по mtime; часы диска — единственный закон)."""
    try:
        return time.time() - path.stat().st_mtime <= UI_STATE_STALE
    except OSError:
        return False


def ui_state_read(ddir: Path, device: str = "",
                  merge: bool = True) -> dict:
    """Состояние книги для устройства: {ключ: значение}.

    merge=True (обычное чтение) — файлы устройств накладываются в порядке
    ЗАПИСИ (по отметке `updated`, у старых файлов — по mtime): сев за другой
    браузер, человек продолжает с того, что оставили позже, а не с того, что
    случайно оказалось свежее на диске. Вложенные объекты сливаются, поэтому
    правка главы на телефоне не стирает панели, выбранные на ноутбуке, а
    совпавший ключ получает все устройства одинаково — последнее значение.
    merge=False — только свой файл.

    Снятый ключ не воскресает из чужого файла: удаление отмечается в файле
    владельца (`_deleted`) и применяется в том же порядке — значение,
    записанное раньше снятия, его не отменяет.
    """
    return _read(ddir, device, merge)[0]


def _read(ddir: Path, device: str, merge: bool) -> tuple[dict, dict]:
    """(значения, надгробия) после слияния: надгробия нужны записи.

    Свой файл участвует в том же порядке, что и чужие, а не накладывается
    последним принудительно: иначе каждое устройство видело бы своё значение
    одного и того же ключа (форма запусков на двух компьютерах показывала
    разные диапазоны глав)."""
    ddir = Path(ddir)
    with _LOCK:
        own_path = _file(ddir, device)
        layers = [(own_path, _load(own_path))]  # своё читается всегда
        if merge:
            try:
                layers += [(p, _load(p)) for p in ddir.glob("*.json")
                           if p.is_file()
                           and (not device or p.name != f"{device}.json")
                           and _fresh(p)]
            except OSError:
                pass
        # у одинаковых отметок решает mtime: порядок записи остаётся единым
        layers.sort(key=lambda d: (_file_stamp(d[0], d[1]), _mtime(d[0])))
        data: dict = {}
        graves: dict = {}
        for path, got in layers:
            _apply(data, graves, got)
        return data, graves


def ui_state_write(ddir: Path, device: str, payload: dict) -> None:
    """Сохранить состояние устройства (ключи книги поверх лежащих на диске).

    Клиент присылает только изменённые ключи (`nmTab:…`, `runVals:…:стадия`,
    `nmProfile:…`); значение `None` снимает ключ и оставляет надгробие — так
    сброс настроек стирает значение и в книге, а не только в памяти страницы.
    Неприсланные ключи остаются: правка одной вкладки не задевает соседние.
    """
    if not isinstance(payload, dict):
        raise ValueError("состояние интерфейса — JSON-объект")
    ddir = Path(ddir)
    with _LOCK:
        ddir.mkdir(parents=True, exist_ok=True)
        target = _file(ddir, device)
        data, graves = _read(ddir, device, merge=False)
        for key, value in _clean_state(payload).items():
            if value is None:
                data.pop(key, None)
                graves[key] = round(time.time(), 3)
            else:
                data[key] = value
                graves.pop(key, None)
        # надгробия старше срока годности не нужны: файлы устройств с такими
        # значениями к этому времени и сами не попадают в слияние
        cutoff = time.time() - UI_STATE_STALE
        graves = {k: v for k, v in graves.items() if v >= cutoff}
        out = dict(data)
        out["version"] = UI_STATE_VERSION
        out["updated"] = round(time.time(), 3)
        if graves:
            out["_deleted"] = graves
        atomic_write(str(target), json.dumps(out, ensure_ascii=False,
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


#: служебные поля файла состояния: в состояние клиента они не едут
_SERVICE = ("version", "updated", "_deleted")


def _strip(data: dict) -> dict:
    """Только ключи интерфейса (служебные поля формата отбрасываются)."""
    return {k: v for k, v in data.items() if k not in _SERVICE}


def _deleted(data: dict) -> dict:
    """Надгробия снятых ключей: {ключ: время снятия}; битые — мимо."""
    raw = data.get("_deleted")
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key, when in raw.items():
        try:
            out[key] = float(when)
        except (TypeError, ValueError):
            continue  # битая отметка не должна ронять чтение
    return out


def _apply(out: dict, graves: dict, data: dict) -> None:
    """Наложить файл на слияние: значения сливаются, надгробия снимают ключи.

    Файлы приходят в порядке записи, поэтому «позже» здесь — просто «следующий
    слой»: совпавший ключ получает последнее записанное значение, а вложенные
    объекты сливаются — чужие правки соседних полей не теряются.
    """
    for key, value in _strip(data).items():
        old = out.get(key)
        if isinstance(old, dict) and isinstance(value, dict):
            out[key] = _merge(old, value)
        else:
            out[key] = value
    for key, when in _deleted(data).items():
        if when >= float(graves.get(key, 0)):
            out.pop(key, None)
            graves[key] = when


def _clean_state(state: dict) -> dict:
    """Только ключи интерфейса и JSON-типы: мусор из тела запроса не должен
    ложиться в файл книги (None = ключ снят, так клиент сбрасывает настройки)."""
    out: dict = {}
    for key, value in state.items():
        if not isinstance(key, str) or not key or len(key) > 200:
            continue
        if isinstance(value, (dict, list)) or value is None or isinstance(
                value, (str, int, float, bool)):
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
        d = Path(d)
        ddir = ui_state_dir(d)
        ui_state_write(ddir, "dev1", {"nmTab:a/b": {"page": {"view": "run"}}})
        ui_state_write(ddir, "dev2", {"nmProfile:a/b/ner": "home"})
        got = ui_state_read(ddir, "dev1")
        assert got["nmTab:a/b"] == {"page": {"view": "run"}}, got
        assert got["nmProfile:a/b/ner"] == "home", got
        ui_state_write(ddir, "dev1", {"nmProfile:a/b/ner": None})
        assert "nmProfile:a/b/ner" not in ui_state_read(ddir, "dev1")
        print("ui_state ok:", sorted(p.name for p in ddir.iterdir()))


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()

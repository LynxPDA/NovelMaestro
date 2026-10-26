#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/uistate.py — состояние книги на диске: параметры форм, профили стадий,
чипсы и место работы во вкладках.

Состояние интерфейса делится по природе данных: **что описывает книгу** (какие
значения у стадий, чем запускать перевод, где человек остановился во вкладках)
лежит в книге — `tmp/ui_state/state.json`; **что описывает устройство** (тема,
кегль, язык подсветки) остаётся в браузере. Смена браузера, домена или переезд с
телефона на десктоп книжное состояние не теряет, а копия книги наследует его
вместе с `tmp/`.

Файл ОДИН на книгу: состояние книги — её свойство, а не свойство устройства.
Файл на устройство делил одну книгу на несколько правд (два компьютера видели
разные диапазоны глав), а каталог рос новым json на каждый браузер, домен и
чистку cookie. Несколько писателей не теряют правки друг друга, потому что
запись читает файл и накладывает только присланные ключи, а вложенные объекты
сливает по полям — под общим замком.

Старые файлы устройств (`<id>.json`, `pending.json`) сливаются в общий при
первом чтении или записи и затем удаляются: состояние, оставленное прежней
версией, не теряется.

Ничего своего у состояния нет — это словарь ключей интерфейса (`nmTab:…`,
`runVals:…`, `nerCols:…`), поэтому слой не знает ни одного имени поля.

`tmp/` исключён из истории контрольных точек (web/history.py): состояние — не
содержимое книги, и возврат к точке не должен откатывать вкладки.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .common import atomic_write, read_text_safe

__all__ = [
    "UI_STATE_DIR", "UI_STATE_FILE", "UI_STATE_VERSION", "ui_state_dir",
    "ui_state_read", "ui_state_write",
]

#: каталог состояния внутри проекта (tmp/ — рабочие файлы книги)
UI_STATE_DIR = Path("tmp") / "ui_state"
#: единственный файл состояния книги
UI_STATE_FILE = "state.json"
#: версия формата: поле `version` пишется в каждый файл, незнакомая версия
#: читается как есть (сброс клиентского кеша — дело клиента)
UI_STATE_VERSION = 2

# Сервер многопоточный: чтение-слияние-запись идут под общим замком, иначе две
# вкладки теряли бы записи друг друга на read-modify-write.
_LOCK = threading.RLock()


def ui_state_dir(pdir: Path) -> Path:
    """Каталог состояния книги (рядом с рабочими файлами стадий)."""
    return Path(pdir) / UI_STATE_DIR


def _file(ddir: Path) -> Path:
    return Path(ddir) / UI_STATE_FILE


def _load(path: Path) -> dict:
    """Объект файла состояния; битый JSON, мусор и нет файла — пусто."""
    try:
        data = json.loads(read_text_safe(path))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _mtime(path: Path) -> float:
    """mtime файла (0 — нет файла); нужен только миграции старых файлов."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _stamp(path: Path, data: dict) -> float:
    """Время записи файла: `updated` из данных, иначе mtime.

    Только по mtime ранжировать нельзя: копирование, перенос книги или правка
    часов делают свежим файл, чьё содержимое старше."""
    try:
        stamp = float(data.get("updated") or 0)
    except (TypeError, ValueError):
        stamp = 0.0
    return stamp if stamp > 0 else _mtime(path)


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


def _merge(base, patch):
    """Слияние состояния: объект поверх объекта, скаляр заменяет скаляр.

    Вложенные объекты сливаются по полям: правка одного поля формы не стирает
    соседние, записанные другим устройством или другой вкладкой. `None` внутри
    объекта снимает поле (чипс, снятый галочкой, не воскресает после
    перезагрузки)."""
    out = dict(base)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _apply(out: dict, graves: dict, data: dict) -> None:
    """Наложить слой старого файла: значения сливаются, надгробия снимают ключи."""
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


def _legacy(ddir: Path) -> list[tuple[Path, dict]]:
    """Файлы прежней версии (по файлу на устройство) в порядке записи.

    Пусто, если каталога нет или он уже переведён на общий файл. Эти файлы
    сливаются в `state.json` при первом чтении или записи: состояние,
    оставленное до обновления, не теряется."""
    try:
        files = [p for p in ddir.glob("*.json")
                 if p.is_file() and p.name != UI_STATE_FILE]
    except OSError:
        return []
    layers = [(p, _load(p)) for p in files]
    # у одинаковых отметок решает mtime: порядок записи остаётся единым
    layers.sort(key=lambda d: (_stamp(d[0], d[1]), _mtime(d[0])))
    return layers


def _legacy_state(ddir: Path) -> tuple[dict, dict]:
    """(значения, надгробия) слияния файлов прежней версии."""
    data: dict = {}
    graves: dict = {}
    for _path, got in _legacy(ddir):
        _apply(data, graves, got)
    return data, graves


def ui_state_read(ddir: Path) -> dict:
    """Состояние книги: {ключ: значение}.

    Читается один общий файл, поэтому все устройства и все домены видят одно и
    то же состояние книги. Файла ещё нет (первый заход после обновления) —
    читаются файлы прежней версии, слитые в порядке записи.
    """
    ddir = Path(ddir)
    with _LOCK:
        target = _file(ddir)
        if target.is_file():
            return _strip(_load(target))
        return _legacy_state(ddir)[0]


def ui_state_write(ddir: Path, payload: dict) -> dict:
    """Сохранить изменённые ключи книги и вернуть состояние целиком.

    Клиент присылает только изменённые ключи (`nmTab:…`, `runVals:…:стадия`,
    `nmProfile:…`); значение `None` снимает ключ, `None` внутри объекта — поле
    (так сброс настроек и снятый чипс стирают значение и в книге, а не только
    в памяти страницы). Неприсланные ключи остаются, а у присланных объектов
    сливаются поля: правка одной вкладки не задевает соседние поля, записанные
    другим устройством.

    Возвращается слитое состояние, а не принятые ключи: клиент держит его
    снимок для показа, и без ответа сервера его поля расходились бы с книгой
    до перезагрузки страницы.
    """
    if not isinstance(payload, dict):
        raise ValueError("состояние интерфейса — JSON-объект")
    ddir = Path(ddir)
    with _LOCK:
        ddir.mkdir(parents=True, exist_ok=True)
        target = _file(ddir)
        if target.is_file():
            raw = _load(target)
            data, graves = _strip(raw), _deleted(raw)
            legacy = False
        else:
            # первый заход после обновления: старое состояние устройств
            # вливается в общий файл, а не теряется
            data, graves = _legacy_state(ddir)
            legacy = True
        for key, value in _clean_state(payload).items():
            if value is None:
                data.pop(key, None)
                graves[key] = round(time.time(), 3)
            else:
                data[key] = _merge(data[key], value) \
                    if isinstance(data.get(key), dict) and isinstance(value, dict) \
                    else value
                graves.pop(key, None)
        out = dict(data)
        out["version"] = UI_STATE_VERSION
        out["updated"] = round(time.time(), 3)
        if graves:
            out["_deleted"] = graves
        atomic_write(str(target), json.dumps(out, ensure_ascii=False,
                                             indent=2))
        if legacy:
            _drop_legacy(ddir)
        return data


def _drop_legacy(ddir: Path) -> list[str]:
    """Удалить файлы прежней версии, уже влитые в общий файл.

    Состояние, оставленное до обновления, к этому мгновению лежит в
    `state.json` целиком (запись выше идёт под тем же замком), поэтому старые
    файлы — только мусор: каталог рос новым json на каждый браузер и домен.
    """
    dropped: list[str] = []
    for path, _data in _legacy(ddir):
        try:
            path.unlink()
            dropped.append(path.name)
        except OSError:
            pass
    return dropped


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


def _selfcheck() -> None:  # pragma: no cover — ручная проверка слоя
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        ddir = ui_state_dir(d)
        ui_state_write(ddir, {"nmTab:a/b": {"page": {"view": "run"}}})
        ui_state_write(ddir, {"nmProfile:a/b/ner": "home"})
        got = ui_state_read(ddir)
        assert got["nmTab:a/b"] == {"page": {"view": "run"}}, got
        assert got["nmProfile:a/b/ner"] == "home", got
        ui_state_write(ddir, {"nmTab:a/b": {"editor": {"chapter": "3"}}})
        assert ui_state_read(ddir)["nmTab:a/b"] == {
            "page": {"view": "run"}, "editor": {"chapter": "3"}}
        ui_state_write(ddir, {"nmProfile:a/b/ner": None})
        assert "nmProfile:a/b/ner" not in ui_state_read(ddir)
        print("ui_state ok:", sorted(p.name for p in ddir.iterdir()))


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты core/uistate.py: состояние книги на диске (tmp/ui_state/state.json).

Слой не знает ни одного имени поля интерфейса — проверяются формат файла,
слияние записей разных писателей, снятие ключей и полей, миграция файлов
прежней версии.
"""
import json
import os
import time

import pytest

from core import uistate as U


@pytest.fixture()
def ddir(tmp_path):
    """Каталог состояния книги (как у проекта: tmp/ui_state)."""
    return U.ui_state_dir(tmp_path)


def _file(ddir):
    return ddir / U.UI_STATE_FILE


def test_one_file_per_book(tmp_path):
    """Состояние книги — одно на книгу, а не на устройство.

    Файл на устройство делил одну книгу на несколько правд: два компьютера
    видели разные диапазоны глав, а каталог рос новым json на каждый браузер,
    домен и чистку cookie."""
    assert U.ui_state_dir(tmp_path) == tmp_path / "tmp" / "ui_state"
    assert U.UI_STATE_FILE == "state.json"


def test_write_read_roundtrip(ddir):
    U.ui_state_write(ddir, {"runVals:ACTIVE/book:pipeline": {"start": "10"}})
    assert U.ui_state_read(ddir) == {
        "runVals:ACTIVE/book:pipeline": {"start": "10"}}
    raw = json.loads(_file(ddir).read_text(encoding="utf-8"))
    assert raw["version"] == U.UI_STATE_VERSION
    assert raw["updated"] > 0


def test_no_extra_files_grow(ddir):
    """Запись не плодит файлов: сколько бы писателей ни было — один файл."""
    for i in range(5):
        U.ui_state_write(ddir, {"nmTab:a/b": {"page": {"view": f"v{i}"}}})
    assert sorted(p.name for p in ddir.iterdir()) == [U.UI_STATE_FILE]


def test_sent_scope_replaces_whole_value(ddir):
    """Присланное значение ставится целиком, кроме `None`-полей.

    Убрать поле формы из памяти можно только так: иначе снятая галочка
    возвращалась бы из книги после перезагрузки."""
    U.ui_state_write(ddir, {"runVals:a/b:ner": {"chunk_size": "1",
                                                "extra": "x"}})
    U.ui_state_write(ddir, {"runVals:a/b:ner": {"extra": None}})
    assert U.ui_state_read(ddir)["runVals:a/b:ner"] == {"chunk_size": "1"}


def test_unsent_keys_and_fields_survive(ddir):
    """Неприсланные ключи остаются, у присланных объектов сливаются поля.

    Иначе правка одного поля формы стирала бы соседние, записанные другим
    устройством."""
    U.ui_state_write(ddir, {"runs": {"ner": {"start": "1"}}})
    U.ui_state_write(ddir, {"runs": {"wiki": {"top": "80"}}})
    assert U.ui_state_read(ddir)["runs"] == {
        "ner": {"start": "1"}, "wiki": {"top": "80"}}
    U.ui_state_write(ddir, {"nmTab:a/b": {"editor": {"chapter": "7"}}})
    assert U.ui_state_read(ddir)["nmTab:a/b"] == {"editor": {"chapter": "7"}}
    assert U.ui_state_read(ddir)["runs"]["ner"] == {"start": "1"}


def test_two_writers_do_not_erase_each_other(ddir):
    """Один писатель отправил своё поле — поле другого остаётся.

    Именно это и давало разные диапазоны на двух компьютерах: правка
    `chunk_size` уезжала в файл вместе со старыми `start`/`end`."""
    key = "runVals:ACTIVE/book:pipeline"
    U.ui_state_write(ddir, {key: {"start": "100", "end": "200"}})
    U.ui_state_write(ddir, {key: {"chunk_size": "9000"}})  # чужое устройство
    assert U.ui_state_read(ddir)[key] == {
        "start": "100", "end": "200", "chunk_size": "9000"}


def test_write_returns_merged_state(ddir):
    """Запись отвечает состоянием книги: клиент живёт его снимком."""
    U.ui_state_write(ddir, {"runs": {"ner": {"start": "1"}}})
    got = U.ui_state_write(ddir, {"runs": {"wiki": {"top": "8"}}})
    assert got == {"runs": {"ner": {"start": "1"}, "wiki": {"top": "8"}}}
    assert U.ui_state_read(ddir) == got


def test_removed_key_does_not_come_back(ddir):
    """Снятый ключ не воскресает: его нет ни в файле, ни в ответе записи."""
    U.ui_state_write(ddir, {"nmProfile:a/b/ner": "home"})
    U.ui_state_write(ddir, {"nmProfile:a/b/ner": None})
    assert U.ui_state_read(ddir) == {}
    assert "nmProfile:a/b/ner" not in U.ui_state_write(ddir, {})


def test_broken_or_missing_file_reads_empty(ddir):
    assert U.ui_state_read(ddir) == {}  # каталога ещё нет
    ddir.mkdir(parents=True, exist_ok=True)
    _file(ddir).write_text("{это не json", encoding="utf-8")
    assert U.ui_state_read(ddir) == {}
    _file(ddir).write_text("[]", encoding="utf-8")
    assert U.ui_state_read(ddir) == {}


def test_write_cleans_payload(ddir):
    """В файл книги едут только ключи интерфейса (JSON-типы, разумная длина)."""
    U.ui_state_write(ddir, {
        "ok": {"a": 1},
        "": {"мусор": 1},
        "x" * 300: 1,
        "list": [1, 2],
        "num": 5,
        "flag": True,
    })
    assert U.ui_state_read(ddir) == {"ok": {"a": 1}, "list": [1, 2],
                                     "num": 5, "flag": True}


def test_write_rejects_non_object(ddir):
    with pytest.raises(ValueError):
        U.ui_state_write(ddir, ["не объект"])  # type: ignore[arg-type]


def test_file_is_atomic_and_readable(tmp_path):
    """Файл состояния — обычный JSON: слой не держит второго формата."""
    ddir = U.ui_state_dir(tmp_path)
    U.ui_state_write(ddir, {"runs": {"ner": {"start": "3"}}})
    text = _file(ddir).read_text(encoding="utf-8")
    assert json.loads(text)["runs"]["ner"]["start"] == "3"
    assert not list(ddir.glob("*.tmp"))  # обломков не остаётся


# ── миграция файлов прежней версии (по файлу на устройство) ──────────────


def _legacy(ddir, dev, data, stamp):
    """Файл прежней версии: <id>.json с отметкой `updated`."""
    ddir.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["version"] = 1
    payload["updated"] = stamp
    path = ddir / f"{dev}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.utime(path, (stamp, stamp))
    return path


def test_legacy_files_merge_into_one(ddir):
    """Старое состояние устройств не теряется: файлы сливаются в общий.

    Устройства писали одно и то же поле в разное время — решает поздняя
    запись; разные поля складываются."""
    now = time.time()
    _legacy(ddir, "dev1", {"runVals:a/b:ner": {"start": "1"}}, now - 100)
    _legacy(ddir, "dev2", {"runVals:a/b:ner": {"end": "50"},
                           "nmTab:a/b": {"page": {"view": "run"}}}, now - 10)
    assert U.ui_state_read(ddir) == {
        "runVals:a/b:ner": {"start": "1", "end": "50"},
        "nmTab:a/b": {"page": {"view": "run"}},
    }


def test_legacy_order_is_record_time_not_mtime(ddir):
    """Свежий mtime при старом содержимом (копия книги, чужие часы, перенос)
    не перебивает более позднюю запись: решает отметка `updated`."""
    now = time.time()
    early = _legacy(ddir, "dev1", {"k": {"v": "позднее"}}, now - 10)
    os.utime(early, (now - 20, now - 20))  # поздняя запись, но старый mtime
    late = _legacy(ddir, "dev2", {"k": {"v": "раннее"}}, now - 100)
    os.utime(late, (now, now))  # ранняя запись, но свежий mtime
    assert U.ui_state_read(ddir)["k"] == {"v": "позднее"}


def test_legacy_without_stamp_reads_by_mtime(ddir):
    """Состояние прежней версии без `updated` тоже не теряется: штамп — mtime."""
    ddir.mkdir(parents=True, exist_ok=True)
    path = ddir / "old.json"
    path.write_text(json.dumps({"runVals:a/b:ner": {"start": "5"}}),
                    encoding="utf-8")
    assert U.ui_state_read(ddir)["runVals:a/b:ner"] == {"start": "5"}


def test_legacy_graves_suppress_values(ddir):
    """Снятый в старом файле ключ остаётся снятым после слияния."""
    now = time.time()
    _legacy(ddir, "dev1", {"nmProfile:a/b/ner": "home"}, now - 100)
    _legacy(ddir, "dev2", {"_deleted": {"nmProfile:a/b/ner": now - 50}}, now - 50)
    assert "nmProfile:a/b/ner" not in U.ui_state_read(ddir)


def test_legacy_files_removed_after_write(ddir):
    """После первой записи старые файлы устройств удаляются: каталог не растёт."""
    now = time.time()
    _legacy(ddir, "dev1", {"runVals:a/b:ner": {"start": "1"}}, now - 100)
    _legacy(ddir, "pending", {"nmTab:a/b": {"page": {"view": "run"}}}, now - 50)
    U.ui_state_write(ddir, {"nmProfile:a/b/ner": "home"})
    assert sorted(p.name for p in ddir.iterdir()) == [U.UI_STATE_FILE]
    # влитое состояние никуда не делось
    got = U.ui_state_read(ddir)
    assert got["runVals:a/b:ner"] == {"start": "1"}
    assert got["nmTab:a/b"] == {"page": {"view": "run"}}
    assert got["nmProfile:a/b/ner"] == "home"


def test_legacy_files_kept_when_write_fails(ddir, monkeypatch):
    """Неудачная запись не должна съесть старое состояние."""
    now = time.time()
    _legacy(ddir, "dev1", {"k": {"v": 1}}, now - 10)

    def boom(*_a, **_k):
        raise OSError("диск полон")

    monkeypatch.setattr(U, "atomic_write", boom)
    with pytest.raises(OSError):
        U.ui_state_write(ddir, {"n": 1})
    monkeypatch.undo()
    assert (ddir / "dev1.json").is_file()
    assert U.ui_state_read(ddir)["k"] == {"v": 1}


def test_legacy_broken_file_is_skipped(ddir):
    """Битый старый файл не роняет чтение: остальные сливаются."""
    now = time.time()
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "dev1.json").write_text("{битый", encoding="utf-8")
    _legacy(ddir, "dev2", {"k": {"v": 2}}, now - 10)
    assert U.ui_state_read(ddir) == {"k": {"v": 2}}


def test_read_is_idempotent(ddir):
    """Повторное чтение не меняет ни файл, ни ответ."""
    _legacy(ddir, "dev1", {"a": {"b": 1}}, time.time() - 10)
    first = U.ui_state_read(ddir)
    assert U.ui_state_read(ddir) == first
    assert (ddir / "dev1.json").is_file()  # чтение ничего не удаляет

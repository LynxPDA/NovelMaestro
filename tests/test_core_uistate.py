#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты core/uistate.py: состояние книги на диске (tmp/ui_state/).

Слой не знает ни одного имени поля интерфейса — проверяются формат файлов,
слияние устройств, срок годности, чистка и защита пути от cookie.
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


def test_dir_lives_in_project_tmp(tmp_path):
    assert U.ui_state_dir(tmp_path) == tmp_path / "tmp" / "ui_state"


@pytest.mark.parametrize("raw", ["../etc", "", None, "a b", "x" * 200,
                                 "dev;rm -rf", "../../tmp/x"])
def test_device_from_foreign_cookie_is_new(raw):
    """id из cookie — часть имени файла: чужое значение не должно задавать путь."""
    dev = U.ui_state_device(raw)
    assert dev != (raw or "")
    assert U._DEVICE_RE.match(dev), dev


@pytest.mark.parametrize("raw", ["dev1-device", "A" * 32, "x_1-2-device"])
def test_device_kept_when_valid(raw):
    assert U.ui_state_device(raw) == raw


def test_write_read_roundtrip(ddir):
    U.ui_state_write(ddir, "dev1", {"runs": {"ner": {"chunk_size": "1234"}}})
    assert U.ui_state_read(ddir, "dev1") == {
        "runs": {"ner": {"chunk_size": "1234"}}}
    data = json.loads((ddir / "dev1.json").read_text(encoding="utf-8"))
    assert data["version"] == U.UI_STATE_VERSION
    assert data["updated"] > 0


def test_write_replaces_sent_scope(ddir):
    """Присланный скоуп заменяется целиком, а не сливается: иначе нельзя было
    бы ничего сбросить (возвращённое к дефолту значение осталось бы в файле)."""
    U.ui_state_write(ddir, "dev1", {"runs": {"ner": {"chunk_size": "1"}}})
    U.ui_state_write(ddir, "dev1", {"runs": {"wiki": {"top": "80"}}})
    assert U.ui_state_read(ddir, "dev1")["runs"] == {"wiki": {"top": "80"}}
    # неприсланный скоуп остаётся: клиент правил только runs
    assert U.ui_state_read(ddir, "dev1")["runs"] == {"wiki": {"top": "80"}}
    U.ui_state_write(ddir, "dev1", {"tab": {"page": {"view": "run"}}})
    got = U.ui_state_read(ddir, "dev1")
    assert got["runs"] == {"wiki": {"top": "80"}}
    assert got["tab"] == {"page": {"view": "run"}}


def test_read_merges_deeper_over_whole_scopes(ddir):
    """Слияние при ЧТЕНИИ глубокое: устройство видит чужие правки в том же
    скоупе (у каждого свой файл, чужие значения не перетираются)."""
    U.ui_state_write(ddir, "dev1", {"runs": {"ner": {"start": "1"}}})
    U.ui_state_write(ddir, "dev2", {"runs": {"wiki": {"top": "8"}}})
    assert U.ui_state_read(ddir, "dev2")["runs"] == {
        "ner": {"start": "1"}, "wiki": {"top": "8"}}


def test_read_merges_other_devices_own_wins(ddir):
    U.ui_state_write(ddir, "dev1", {"tab": {"editor": {"chapter": "5"}}})
    U.ui_state_write(ddir, "dev2", {"tab": {"logs": {"path": "logs"}},
                                    "runs": {"ner": {"start": "1"}}})
    got = U.ui_state_read(ddir, "dev2")
    assert got["tab"] == {"editor": {"chapter": "5"}, "logs": {"path": "logs"}}
    assert got["runs"] == {"ner": {"start": "1"}}
    # своё важнее: то, что поменяли здесь, не перебивается чужим
    assert U.ui_state_read(ddir, "dev1")["tab"]["editor"] == {"chapter": "5"}


def test_read_without_merge_is_own_only(ddir):
    U.ui_state_write(ddir, "dev1", {"runs": {"ner": {"start": "1"}}})
    U.ui_state_write(ddir, "dev2", {"runs": {"ner": {"start": "2"}}})
    assert U.ui_state_read(ddir, "dev2", merge=False)["runs"]["ner"] == {
        "start": "2"}
    assert U.ui_state_read(ddir, "dev3", merge=False) == {}


def test_pending_participates_in_merge(ddir):
    """Состояние без cookie (первый визит) тоже нужное — но не важнее своего."""
    U.ui_state_write(ddir, "", {"page": {"view": "run"}})
    assert U.ui_state_read(ddir, "dev1")["page"] == {"view": "run"}
    U.ui_state_write(ddir, "dev1", {"page": {"view": "ner"}})
    assert U.ui_state_read(ddir, "dev1")["page"] == {"view": "ner"}


def test_stale_state_not_merged(ddir):
    """Полугодовое состояние не всплывает сюрпризом."""
    U.ui_state_write(ddir, "old", {"tab": {"x": 1}})
    old = time.time() - U.UI_STATE_STALE - 60
    os.utime(ddir / "old.json", (old, old))
    assert U.ui_state_read(ddir, "dev1") == {}
    # своё читается в любом случае: человек им пользуется
    assert U.ui_state_read(ddir, "old") == {"tab": {"x": 1}}


def test_empty_or_broken_file_reads_empty(ddir):
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "dev1.json").write_text("{это не json", encoding="utf-8")
    assert U.ui_state_read(ddir, "dev1") == {}
    (ddir / "dev1.json").write_text("[]", encoding="utf-8")
    assert U.ui_state_read(ddir, "dev1") == {}
    assert U.ui_state_read(ddir, "nobody") == {}


def test_write_cleans_payload(ddir):
    """В файл книги едут только ключи интерфейса: глубина 2 и JSON-типы."""
    U.ui_state_write(ddir, "dev1", {
        "ok": {"a": 1}, "bad-list": [1, 2], "deep": {"x": {"y": 1}},
        "": {"z": 1}, "scalar": 5, "none": None,
    })
    got = U.ui_state_read(ddir, "dev1")
    assert got == {"ok": {"a": 1}, "deep": {"x": {"y": 1}}, "scalar": 5,
                   "none": None}
    assert "version" not in got and "updated" not in got


def test_write_rejects_non_object(ddir):
    with pytest.raises(ValueError):
        U.ui_state_write(ddir, "dev1", ["не объект"])  # type: ignore[arg-type]


def test_write_creates_dir(ddir):
    assert not ddir.exists()
    U.ui_state_write(ddir, "dev1", {"a": {"b": 1}})
    assert (ddir / "dev1.json").is_file()


def test_prune_keeps_freshest(ddir):
    base = time.time() - 1000
    for i in range(U.UI_STATE_KEEP + 3):
        U.ui_state_write(ddir, f"dev{i:03d}", {"n": {"v": i}})
        stamp = base + i  # порядок mtime — по порядку записи
        os.utime(ddir / f"dev{i:03d}.json", (stamp, stamp))
    left = sorted(p.name for p in ddir.glob("*.json"))
    assert len(left) == U.UI_STATE_KEEP, left
    assert "dev000.json" not in left, "старое состояние должно уходить"
    # ушедший файл не читается сам; в слияние идут оставшиеся
    assert U.ui_state_read(ddir, "dev000", merge=False) == {}
    assert U.ui_state_read(ddir, "dev000")["n"] == {"v": 12}


def test_pending_removed_by_prune_order(ddir):
    base = time.time() - 1000
    U.ui_state_write(ddir, "", {"a": {"b": 1}})
    assert (ddir / "pending.json").is_file()
    os.utime(ddir / "pending.json", (base, base))
    for i in range(U.UI_STATE_KEEP):
        U.ui_state_write(ddir, f"dev{i:03d}", {"n": {"v": i}})
        stamp = base + 10 + i
        os.utime(ddir / f"dev{i:03d}.json", (stamp, stamp))
    assert not (ddir / "pending.json").exists()


def test_file_is_atomic_and_readable(tmp_path):
    """Файл состояния — обычный JSON: слой не держит второго формата."""
    ddir = U.ui_state_dir(tmp_path)
    U.ui_state_write(ddir, "dev1", {"runs": {"ner": {"start": "3"}}})
    text = (ddir / "dev1.json").read_text(encoding="utf-8")
    assert json.loads(text)["runs"]["ner"]["start"] == "3"
    # обломков временных файлов не остаётся
    assert not list(ddir.glob("*.tmp"))


def test_ui_state_dir_has_no_other_names():
    """Каталог состояния — ровно один: иначе клиент и тесты считают разное."""
    assert U.UI_STATE_DIR.as_posix() == "tmp/ui_state"

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты core/archive.py — упаковка проекта в один файл и обратная распаковка.

Проверяем то, ради чего модуль и появился: круг «упаковать → манифест без
распаковки → распаковать» сохраняет дерево байт в байт (включая пустые
каталоги и имена с CJK), формат остаётся обычным tar (открывается чужим
распаковщиком), а каждый бэкенд сжатия достижим — от pip-пакета до stdlib
фолбэка. Установленное окружение мокается: find_spec подменяется, поэтому
все ветки проверяются на любой машине.
"""
from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core import archive as A  # noqa: E402


# ════════════════════════════════════════════════════════════════════
# хелперы


def _make_project(root: Path, name: str = "Книга") -> Path:
    """Проект с тем, что тяжело упаковывать: CJK-имена, пустая папка, .git."""
    p = root / "DONE" / name
    (p / "chapters" / "00000_1_第1章_开端").mkdir(parents=True)
    (p / "chapters" / "00000_1_第1章_开端" / "chapter.txt").write_text(
        "第一章 开端\n", encoding="utf-8")
    (p / "chapters" / "00000_1_第1章_开端" / "translated.txt").write_text(
        "Глава 1. Начало\n", encoding="utf-8")
    (p / "chapters" / "00000_2_empty").mkdir()
    (p / "ner.json").write_text(json.dumps(
        [{"term": "纪九郎", "translation": "Цзи Цзюлан"}],
        ensure_ascii=False), encoding="utf-8")
    (p / "prompts").mkdir()
    (p / "prompts" / "p.txt").write_text("<translate>…", encoding="utf-8")
    (p / "tmp").mkdir()
    (p / "tmp" / "epub_preview.json").write_text('{"a": 1}', encoding="utf-8")
    (p / ".git" / "objects").mkdir(parents=True)
    (p / ".git" / "HEAD").write_text("ref: refs/heads/master\n",
                                     encoding="utf-8")
    return p


def _tree(root: Path, skip=()) -> dict:
    """Слепок дерева: {относительный путь: содержимое} + каталоги отдельно."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if any(part in skip for part in Path(rel).parts[:1]):
            continue
        if p.is_dir():
            out[rel + "/"] = None
        else:
            out[rel] = p.read_bytes()
    return out


@pytest.fixture(autouse=True)
def _reset_backend(monkeypatch):
    """Бэкенд выбирается один раз за процесс — в тестах каждый раз свой."""
    monkeypatch.setattr(A, "_backend_cache", None)
    yield


@pytest.fixture(params=["zstandard", "stdlib", "gzip"])
def any_backend(request, monkeypatch):
    """По очереди все три бэкенда: pip → stdlib → фолбэк.

    Бэкенд без своего пакета пропускается: ставить `zstandard` ради теста
    не нужно — все три ветки упаковки уже проверены остальными тестами.
    """
    module = {"zstandard": "zstandard", "stdlib": "compression.zstd"}
    if request.param in module and not A._probe(module[request.param]):
        pytest.skip(f"{module[request.param]} в этом окружении нет")
    monkeypatch.setattr(A, "_backend_cache", None)
    factory = {"zstandard": A._backend_zstandard,
               "stdlib": A._backend_stdlib,
               "gzip": A._backend_gzip}[request.param]
    monkeypatch.setattr(A, "backend", lambda: factory())
    return request.param


# ════════════════════════════════════════════════════════════════════
# бэкенды


@pytest.mark.parametrize("name,module", [
    ("zstandard", "zstandard"),
    ("stdlib", "compression.zstd"),
    ("gzip", None),
])
def test_backend_factories_look_like_a_backend(name, module):
    """У каждого бэкенда есть id, ext и обе фабрики потоков."""
    factory = {"zstandard": A._backend_zstandard,
               "stdlib": A._backend_stdlib,
               "gzip": A._backend_gzip}[name]
    if module and not A._probe(module):
        pytest.skip(f"{module} в этом окружении нет")
    got = factory()
    assert got["id"] == name
    assert got["ext"].startswith("tar.")
    assert callable(got["open_write"]) and callable(got["open_read"])


def test_backend_prefers_installed_package(monkeypatch):
    """Выбор идёт по порядку: самый лучший установленный бэкенд."""
    monkeypatch.setattr(A, "_backend_cache", None)
    monkeypatch.setattr(A, "_probe", lambda m: m in ("zstandard",)
                        or m is None)
    # сам пакет может отсутствовать — фабрика тогда бросит, а мы поймаем
    seen = {}

    def fake_factory():
        seen["id"] = "zstandard"
        return {"id": "zstandard", "ext": "tar.zst"}

    monkeypatch.setattr(A, "_BACKENDS", (("zstandard", fake_factory),))
    assert A.backend()["id"] == "zstandard"
    assert seen["id"] == "zstandard"


def test_backend_falls_back_to_gzip(monkeypatch):
    """Ни пакета, ни stdlib — остаётся gzip, кнопка не падает."""
    monkeypatch.setattr(A, "_backend_cache", None)
    monkeypatch.setattr(A, "_probe", lambda m: m is None)
    assert A.backend()["id"] == "gzip"
    assert A.archive_available() is True


def test_backend_skips_broken_package(monkeypatch):
    """Битый пакет (ошибка импорта) не роняет выбор: идём дальше."""
    monkeypatch.setattr(A, "_backend_cache", None)

    def boom():
        raise ImportError("сломанный пакет")

    monkeypatch.setattr(A, "_BACKENDS", (("zstandard", boom),
                                        (None, A._backend_gzip)))
    monkeypatch.setattr(A, "_probe", lambda m: True)
    assert A.backend()["id"] == "gzip"


def test_backend_without_any_raises(monkeypatch):
    """Бэкендов нет вовсе — явная ошибка, а не падение в недрах tarfile."""
    monkeypatch.setattr(A, "_backend_cache", None)
    monkeypatch.setattr(A, "_BACKENDS", ())
    with pytest.raises(A.ArchiveError):
        A.backend()
    assert A.archive_available() is False


@pytest.mark.parametrize("candidate,expected", [
    (None, True),
    ("json", True),
    ("no_such_module_for_archive_test", False),
])
def test_probe(candidate, expected):
    assert A._probe(candidate) is expected


# ════════════════════════════════════════════════════════════════════
# пути


def test_archive_paths(tmp_path):
    """Архив лежит в .archive раздела — не выглядит проектом."""
    sec = tmp_path / "DONE"
    got = A.archive_path(sec, "Книга")
    assert got == sec / ".archive" / "Книга.nmz"
    assert A.archive_path(sec, "Книга").name.endswith(A.ARCHIVE_EXT)
    assert A.archive_dir(sec) == sec / A.ARCHIVE_DIR


def test_archive_size_and_free_space(tmp_path):
    assert A.archive_size(tmp_path / "нет.nmz") == 0
    p = tmp_path / "a.nmz"
    p.write_bytes(b"x" * 10)
    assert A.archive_size(p) == 10
    assert A.archive_free_space(p) > 0


# ════════════════════════════════════════════════════════════════════
# круг упаковать → манифест → распаковать


def test_roundtrip_keeps_tree(tmp_path, any_backend):
    """Распакованное дерево совпадает с исходным байт в байт."""
    src = _make_project(tmp_path)
    before = _tree(src)
    dst = tmp_path / "DONE" / ".archive" / "Книга.nmz"
    man = A.pack_project(src, dst)
    assert dst.is_file() and man["files"] == len(
        [k for k, v in before.items() if v is not None])
    assert man["version"] == A.ARCHIVE_VERSION
    assert man["name"] == "Книга"
    assert man["compression"] == any_backend

    out = tmp_path / "restored"
    A.unpack_project(dst, out)
    assert _tree(out) == before


def test_empty_directories_survive(tmp_path, any_backend):
    """Каркас проекта (пустые chapters/, prompts/) не теряется."""
    src = tmp_path / "Книга"
    (src / "chapters").mkdir(parents=True)
    (src / "prompts").mkdir()
    dst = tmp_path / "a.nmz"
    A.pack_project(src, dst)
    out = tmp_path / "out"
    A.unpack_project(dst, out)
    assert (out / "chapters").is_dir()
    assert (out / "prompts").is_dir()


def test_manifest_read_without_unpacking(tmp_path, any_backend):
    """Манифест читается из головы архива: книги не распаковываются."""
    src = _make_project(tmp_path)
    dst = tmp_path / "a.nmz"
    A.pack_project(src, dst, manifest_extra={"label": "ручная"})
    man = A.archive_manifest(dst)
    assert man["name"] == "Книга"
    assert man["files"] >= 5
    assert man["bytes"] > 0
    assert man["label"] == "ручная"      # extra не перетирает базовые поля
    assert man["packed_at"]


def test_manifest_of_missing_or_broken_is_empty(tmp_path, any_backend):
    """Битый и отсутствующий архив — пустой манифест, а не исключение."""
    assert A.archive_manifest(tmp_path / "нет.nmz") == {}
    bad = tmp_path / "bad.nmz"
    bad.write_bytes("это не архив".encode("utf-8"))
    assert A.archive_manifest(bad) == {}


@pytest.mark.parametrize("payload", [
    b"",                          # пустой файл
    b"\x28\xb5\x2f\xfd",            # правильная сигнатура zstd, обрезанная
    b"\x1f\x8b\x08\x00",            # то же для gzip
    b"\x00\x01\x02\x03" * 64,       # мусор
])
def test_broken_archive_never_leaks_backend_errors(tmp_path, any_backend,
                                                   payload):
    """Ошибка распаковки — всегда ArchiveError, а не исключение бэкенда.

    У каждого бэкенда своё исключение (ZstdError пакета, zlib.error,
    tarfile.TarError), а вызывающие знают только контракт модуля: без
    обёртки битый архив вылетал наружу сырым ZstdError и ронял список
    архивов вместо карточки «архив повреждён».
    """
    bad = tmp_path / "bad.nmz"
    bad.write_bytes(payload)
    man = A.archive_manifest(bad)          # наружу ничего не летит
    assert man == {}
    with pytest.raises(A.ArchiveError):    # а распаковка называет причину
        A.unpack_project(bad, tmp_path / "out")


def test_manifest_extra_cannot_lie_about_counts(tmp_path, any_backend):
    """Дополнительные поля не перетирают посчитанные (files/bytes/name)."""
    src = _make_project(tmp_path)
    dst = tmp_path / "a.nmz"
    man = A.pack_project(src, dst, manifest_extra={
        "files": 0, "bytes": 0, "name": "чужое", "custom": 1})
    assert man["files"] > 0 and man["bytes"] > 0
    assert man["name"] == "Книга"
    assert man["custom"] == 1


def test_skip_top_level_directories(tmp_path, any_backend):
    """skip убирает папки верхнего уровня (логи), остальное остаётся."""
    src = _make_project(tmp_path)
    (src / "logs" / "chapters").mkdir(parents=True)
    (src / "logs" / "chapters" / "x.log").write_text("лог", encoding="utf-8")
    dst = tmp_path / "a.nmz"
    man = A.pack_project(src, dst, skip={"logs", "tmp"})
    assert man["skip"] == ["logs", "tmp"]
    out = tmp_path / "out"
    A.unpack_project(dst, out)
    assert not (out / "logs").exists()
    assert not (out / "tmp").exists()
    assert (out / "ner.json").is_file()
    assert (out / ".git" / "HEAD").is_file()


def test_skip_does_not_touch_nested_names(tmp_path, any_backend):
    """Файл logs внутри главы не исключается: skip — про верхний уровень."""
    src = tmp_path / "Книга"
    (src / "chapters" / "0001_x").mkdir(parents=True)
    (src / "chapters" / "0001_x" / "tmp").write_text("данные", encoding="utf-8")
    dst = tmp_path / "a.nmz"
    A.pack_project(src, dst, skip={"tmp"})
    out = tmp_path / "out"
    A.unpack_project(dst, out)
    assert (out / "chapters" / "0001_x" / "tmp").read_text(
        encoding="utf-8") == "данные"


def test_archive_is_plain_tar(tmp_path, any_backend):
    """Формат — обычный tar: читается любым архиватором, манифест первый."""
    src = _make_project(tmp_path)
    dst = tmp_path / "a.nmz"
    A.pack_project(src, dst)
    be = A.backend()
    with open(dst, "rb") as fh:
        with tarfile.open(fileobj=be["open_read"](fh), mode="r|") as tf:
            names = []
            for m in tf:
                names.append(m.name)
    assert names[0] == A.ARCHIVE_MANIFEST
    assert "ner.json" in names


def test_unpack_refuses_existing_target(tmp_path, any_backend):
    """Занятый каталог — ошибка: проект на месте не теряется молча."""
    src = _make_project(tmp_path)
    dst = tmp_path / "a.nmz"
    A.pack_project(src, dst)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "важное.txt").write_text("не трогать", encoding="utf-8")
    with pytest.raises(A.ArchiveError, match="уже занято"):
        A.unpack_project(dst, occupied)
    assert (occupied / "важное.txt").read_text(encoding="utf-8") == "не трогать"


def test_unpack_missing_archive(tmp_path, any_backend):
    with pytest.raises(A.ArchiveError, match="не найден"):
        A.unpack_project(tmp_path / "нет.nmz", tmp_path / "out")


def test_pack_missing_project(tmp_path, any_backend):
    with pytest.raises(A.ArchiveError, match="не найден"):
        A.pack_project(tmp_path / "нет", tmp_path / "a.nmz")


def test_pack_failure_leaves_no_partial_archive(tmp_path, any_backend,
                                                monkeypatch):
    """Оборванная упаковка: архива нет, мусорный .tmp подчищен."""
    src = _make_project(tmp_path)
    dst = tmp_path / "a.nmz"

    def boom(*a, **kw):
        raise OSError("диск кончился")

    monkeypatch.setattr(A.os, "replace", boom)
    with pytest.raises(OSError):
        A.pack_project(src, dst)
    assert not dst.exists()
    assert not (tmp_path / (dst.name + A._TMP_SUFFIX)).exists()


def test_pack_overwrites_existing_archive(tmp_path, any_backend):
    """Повторная упаковка заменяет прежний архив, а не падает на нём."""
    src = _make_project(tmp_path)
    dst = tmp_path / "a.nmz"
    first = A.pack_project(src, dst)
    (src / "ner.json").write_text("[]", encoding="utf-8")
    second = A.pack_project(src, dst)
    assert second["files"] == first["files"]
    out = tmp_path / "out"
    A.unpack_project(dst, out)
    assert (out / "ner.json").read_text(encoding="utf-8") == "[]"


def test_progress_called_with_real_numbers(tmp_path, any_backend):
    """Прогресс получает возрастающий done и честный total."""
    src = tmp_path / "Книга"
    src.mkdir()
    for i in range(200):
        (src / f"f{i}.txt").write_text("x", encoding="utf-8")
    dst = tmp_path / "a.nmz"
    seen = []
    A.pack_project(src, dst, progress=lambda d, t: seen.append((d, t)))
    assert seen, "прогресс не позван ни разу"
    assert seen[-1] == (200, 200)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)

    unpacked = []
    A.unpack_project(dst, tmp_path / "out",
                     progress=lambda d, t: unpacked.append((d, t)))
    assert unpacked[-1] == (200, 200)


def test_symlinks_are_not_followed(tmp_path, any_backend):
    """Симлинк наружу не уезжает в архив (иначе это побег из книги)."""
    src = tmp_path / "Книга"
    src.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("не публиковать", encoding="utf-8")
    try:
        (src / "утечка.txt").symlink_to(secret)
    except OSError:
        pytest.skip("симлинки недоступны")
    dst = tmp_path / "a.nmz"
    A.pack_project(src, dst)
    out = tmp_path / "out"
    A.unpack_project(dst, out)
    assert not (out / "утечка.txt").exists()


def test_archive_roundtrip_is_smaller_for_text(tmp_path, any_backend):
    """Повторяющийся текст сжимается: смысл архива в размере."""
    src = tmp_path / "Книга"
    src.mkdir()
    text = ("Повторяющийся абзац перевода. " * 200).encode("utf-8")
    for i in range(20):
        (src / f"глава_{i}.txt").write_bytes(text)
    raw = sum(p.stat().st_size for p in src.iterdir())
    dst = tmp_path / "a.nmz"
    A.pack_project(src, dst)
    assert dst.stat().st_size < raw / 4


# ════════════════════════════════════════════════════════════════════
# служебные имена: недоделанная упаковка/распаковка — не книга


@pytest.mark.parametrize("name,expected", [
    (".nm_restore_Книга", True),      # распаковка идёт во временную папку
    ("Книга.nmz.tmp", True),          # упаковка идёт во временный файл
    ("Книга", False),
    ("nm_restore_Книга", False),      # без точки это обычное имя книги
    ("Книга.tmp", False),             # чужой временный файл — не наш
    (".archive", False),              # каталог архивов отсекается отдельно
])
def test_is_temp_name(name, expected):
    """Служебные имена упаковки/распаковки не выглядят книгами."""
    assert A.is_temp_name(name) is expected

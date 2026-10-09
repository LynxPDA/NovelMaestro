#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""web/history.py — локальная история проекта (контрольные точки на git).

Герметично: проект — tmp_path, dulwich пишет в .git проекта; лока истории
общий на процесс — тесты в одном процессе не спорят (xdist разбрасывает
по воркерам, у каждого свой процесс).
"""
import json

import pytest

from web import history as hs


def _make_project(tmp_path, files: dict):
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def _dump(project):
    out = {}
    for rel in ("ner.json", "chapters/1/chapter.txt"):
        p = project / rel
        if p.is_file():
            out[rel] = p.read_text(encoding="utf-8")
    return out


# ── создание точек ────────────────────────────────────────────────

def test_create_first_point(tmp_path):
    _make_project(tmp_path, {"ner.json": "[]",
                             "chapters/1/chapter.txt": "Глава 1"})
    res = hs.create(tmp_path, "Начало")
    assert res["created"] and res["changed"] and res["sha"]
    assert len(res["sha"]) == 40


def test_create_without_changes_skipped(tmp_path):
    _make_project(tmp_path, {"ner.json": "[]"})
    hs.create(tmp_path, "Начало")
    res = hs.create(tmp_path, "Ничего не меняли")
    assert not res["created"] and not res["changed"]


def test_create_requires_label(tmp_path):
    _make_project(tmp_path, {"ner.json": "[]"})
    with pytest.raises(hs.HistoryError):
        hs.create(tmp_path, "   ")


def test_create_unknown_kind(tmp_path):
    _make_project(tmp_path, {"ner.json": "[]"})
    with pytest.raises(hs.HistoryError):
        hs.create(tmp_path, "Точка", kind="чудо")


def test_empty_project_gets_empty_point(tmp_path):
    res = hs.create(tmp_path, "Пустой проект")
    assert res["created"]  # пустое дерево — тоже законная первая точка


# ── список и семантика Confluence ─────────────────────────────────

def _touch(path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_three(project):
    (project / "ner.json").write_text("[]", encoding="utf-8")
    hs.create(project, "Начало")
    (project / "ner.json").write_text("[1]", encoding="utf-8")
    hs.create(project, "Правка глоссария")
    ch = project / "chapters/1"
    ch.mkdir(parents=True, exist_ok=True)
    (ch / "chapter.txt").write_text("Глава 1 v2", encoding="utf-8")
    hs.create(project, "Правка главы")


def test_checkpoints_newest_first(tmp_path):
    _seed_three(tmp_path)
    data = hs.checkpoints(tmp_path)
    labels = [c["label"] for c in data["checkpoints"]]
    assert data["total"] == 3
    assert labels == ["Правка главы", "Правка глоссария", "Начало"]
    kinds = {c["label"]: c["kind"] for c in data["checkpoints"]}
    assert set(kinds.values()) == {"manual"}
    assert all(len(c["sha"]) == 40 for c in data["checkpoints"])
    times = [c["time"] for c in data["checkpoints"]]
    assert times == sorted(times, reverse=True)


def test_checkpoints_paging(tmp_path):
    _seed_three(tmp_path)
    data = hs.checkpoints(tmp_path, limit=2, offset=1)
    assert [c["label"] for c in data["checkpoints"]] == \
        ["Правка глоссария", "Начало"]
    assert data["total"] == 3


def test_checkpoints_without_history(tmp_path):
    with pytest.raises(hs.HistoryError):
        hs.checkpoints(tmp_path)


def test_restore_does_not_drop_intermediate_points(tmp_path):
    """Confluence-семантика: возврат на 2 точки назад не стирает промежуточные."""
    _seed_three(tmp_path)
    first = hs.checkpoints(tmp_path)["checkpoints"][2]["sha"]
    res = hs.restore(tmp_path, first)
    assert res["created"]
    data = hs.checkpoints(tmp_path)
    assert data["total"] == 4  # 3 исходных + точка возврата
    top = data["checkpoints"][0]
    assert top["label"] == "Возврат к «Начало»"
    assert top["kind"] == "restore"
    # файлы соответствуют первой точке
    assert json.loads((tmp_path / "ner.json").read_text(encoding="utf-8")) == []
    assert not (tmp_path / "chapters/1/chapter.txt").exists()
    # а состояние перед возвратом доступно из истории
    prev = hs.diff(tmp_path, data["checkpoints"][1]["sha"],
                   data["checkpoints"][0]["sha"])
    paths = {f["path"] for f in prev["files"]}
    assert "chapters/1/chapter.txt" in paths


def test_restore_to_same_state_noop(tmp_path):
    _seed_three(tmp_path)
    top = hs.checkpoints(tmp_path)["checkpoints"][0]["sha"]
    res = hs.restore(tmp_path, top)
    assert not res["created"]


def test_restore_unknown_sha(tmp_path):
    hs.create(tmp_path, "Начало")
    with pytest.raises(hs.HistoryError):
        hs.restore(tmp_path, "0" * 40)


def test_restore_removes_extra_files(tmp_path):
    hs.create(tmp_path, "Начало")
    _touch(tmp_path / "chapters/2/chapter.txt", "Глава 2")
    hs.create(tmp_path, "Новая глава")
    first = hs.checkpoints(tmp_path)["checkpoints"][1]["sha"]
    hs.restore(tmp_path, first)
    assert not (tmp_path / "chapters/2/chapter.txt").exists()
    assert not (tmp_path / "chapters/2").exists()  # пустой каталог убран


# ── диффы ─────────────────────────────────────────────────────────

def test_diff_statuses(tmp_path):
    _make_project(tmp_path, {"ner.json": "[]",
                             "chapters/1/chapter.txt": "Глава 1"})
    hs.create(tmp_path, "Начало")
    (tmp_path / "ner.json").write_text("[1]", encoding="utf-8")
    _touch(tmp_path / "chapters/2/chapter.txt", "Глава 2")
    (tmp_path / "chapters/1/chapter.txt").unlink()
    hs.create(tmp_path, "Изменения")
    data = hs.checkpoints(tmp_path)
    c_from, c_to = data["checkpoints"][1], data["checkpoints"][0]
    d = hs.diff(tmp_path, c_from["sha"], c_to["sha"])
    statuses = {f["path"]: f["status"] for f in d["files"]}
    assert statuses["ner.json"] == "M"
    assert statuses["chapters/2/chapter.txt"] == "A"
    assert statuses["chapters/1/chapter.txt"] == "D"
    # удалённый файл — без чисел строк (лишний difflib в списке файлов):
    # содержимое покажет дифф самого файла
    deleted = next(f for f in d["files"] if f["status"] == "D")
    assert deleted["adds"] is None and deleted["dels"] is None


def test_diff_line_counts(tmp_path):
    _make_project(tmp_path, {"ner.json": "строка1\nстрока2\nстрока3\n"})
    hs.create(tmp_path, "Начало")
    (tmp_path / "ner.json").write_text(
        "строка1\nстрока2 изменена\nстрока3\nдобавлена\n", encoding="utf-8")
    hs.create(tmp_path, "Правка")
    c_from, c_to = hs.checkpoints(tmp_path)["checkpoints"][::-1]
    d = hs.diff(tmp_path, c_from["sha"], c_to["sha"])
    f = d["files"][0]
    # «строка2 изменена» — плюс и минус, «добавлена» — ещё один плюс
    assert (f["adds"], f["dels"]) == (2, 1)

def test_diff_big_file_stats_skipped(tmp_path):
    """Файл больше LINE_DELTA_MAX_CHARS — None вместо чисел: difflib
    по каждой главе большой книги превращал сравнение точек в десятки
    секунд, а точные числа нужны только при разворачивании диффа."""
    big = "строка\n" * (hs.LINE_DELTA_MAX_CHARS // 6)
    _make_project(tmp_path, {"big.txt": big + "\nхвост\n"})
    hs.create(tmp_path, "Начало")
    (tmp_path / "big.txt").write_text(big + "\nхвост иной\n", encoding="utf-8")
    hs.create(tmp_path, "Правка")
    c_from, c_to = hs.checkpoints(tmp_path)["checkpoints"][::-1]
    f = hs.diff(tmp_path, c_from["sha"], c_to["sha"])["files"][0]
    assert f["adds"] is None and f["dels"] is None
    # точные числа по-прежнему считает дифф самого файла
    p = hs.patch(tmp_path, c_from["sha"], c_to["sha"], "big.txt")
    assert (p["adds"], p["dels"]) == (1, 1)

def test_patch_oversized_truncated(tmp_path, monkeypatch):
    """Дифф огромного файла обрезается (PATCH_MAX_CHARS) — окно
    разницы не должно захлёбываться мегабайтами текста."""
    monkeypatch.setattr(hs, "PATCH_MAX_CHARS", 1000)
    _make_project(tmp_path, {"big.txt": ""})
    hs.create(tmp_path, "Начало")
    (tmp_path / "big.txt").write_text("строка\n" * 2000, encoding="utf-8")
    hs.create(tmp_path, "Правка")
    c_from, c_to = hs.checkpoints(tmp_path)["checkpoints"][::-1]
    p = hs.patch(tmp_path, c_from["sha"], c_to["sha"], "big.txt")
    assert len(p["patch"]) < 1200 and "обрезан" in p["patch"]
    # числа строк считались по ПОЛНОМУ диффу, до обрезки текста
    assert (p["adds"], p["dels"]) == (2000, 0)

def test_diff_files_capped(tmp_path, monkeypatch):
    """Список файлов сравнения ограничен (DIFF_MAX_FILES): тысячи
    раскрывающихся строк вешают окно."""
    monkeypatch.setattr(hs, "DIFF_MAX_FILES", 5)
    files = {f"chapters/{i}/chapter.txt": f"Глава {i}"
             for i in range(20)}
    _make_project(tmp_path, files)
    hs.create(tmp_path, "Начало")
    for i in range(20):
        (tmp_path / f"chapters/{i}/chapter.txt").write_text(
            f"Глава {i} — правка", encoding="utf-8")
    hs.create(tmp_path, "Правка")
    c_from, c_to = hs.checkpoints(tmp_path)["checkpoints"][::-1]
    d = hs.diff(tmp_path, c_from["sha"], c_to["sha"])
    assert len(d["files"]) == 5 and d["total_files"] == 20


def test_diff_unknown_sha(tmp_path):
    hs.create(tmp_path, "Начало")
    with pytest.raises(hs.HistoryError):
        hs.diff(tmp_path, "0" * 40, hs.checkpoints(tmp_path)
                ["checkpoints"][0]["sha"])


def test_patch_and_file_content(tmp_path):
    _make_project(tmp_path, {"ner.json": "старый текст\n"})
    hs.create(tmp_path, "Начало")
    (tmp_path / "ner.json").write_text("новый текст\n", encoding="utf-8")
    hs.create(tmp_path, "Правка")
    c_from, c_to = hs.checkpoints(tmp_path)["checkpoints"][::-1]
    p = hs.patch(tmp_path, c_from["sha"], c_to["sha"], "ner.json")
    assert "-старый текст" in p["patch"]
    assert "+новый текст" in p["patch"]
    assert (p["adds"], p["dels"]) == (1, 1)
    old = hs.file_content(tmp_path, c_from["sha"], "ner.json")
    assert old == "старый текст\n".encode("utf-8")
    with pytest.raises(hs.HistoryError):
        hs.patch(tmp_path, c_from["sha"], c_to["sha"], "нет/такого.txt")


def test_patch_no_changes(tmp_path):
    _make_project(tmp_path, {"ner.json": "текст\n"})
    hs.create(tmp_path, "Начало")
    _touch(tmp_path / "chapters/1/chapter.txt", "Глава 1")
    hs.create(tmp_path, "Глава")
    c_from, c_to = hs.checkpoints(tmp_path)["checkpoints"][::-1]
    with pytest.raises(hs.HistoryError):
        hs.patch(tmp_path, c_from["sha"], c_to["sha"], "ner.json")


# ── исключения рабочих файлов ─────────────────────────────────────

def test_excluded_dirs_not_tracked(tmp_path):
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp/ner_review.json").write_text("{}", encoding="utf-8")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/run.log").write_text("log", encoding="utf-8")
    (tmp_path / "chapters/1/chapter.txt.bak").parent.mkdir(parents=True)
    (tmp_path / "chapters/1/chapter.txt.bak").write_text("bak",
                                                        encoding="utf-8")
    (tmp_path / "chapters/1/chapter.txt").write_text("Глава 1",
                                                    encoding="utf-8")
    hs.create(tmp_path, "Начало")
    # восстановление не должно ничего менять: рабочие файлы вне истории
    top = hs.checkpoints(tmp_path)["checkpoints"][0]["sha"]
    d = hs.diff(tmp_path, top, top)
    assert d["files"] == []  # сравнение с самим собой пусто — ok
    # но и в самой точке только chapter.txt
    content = hs.file_content(tmp_path, top, "chapters/1/chapter.txt")
    assert content == "Глава 1".encode("utf-8")
    with pytest.raises(hs.HistoryError):
        hs.file_content(tmp_path, top, "tmp/ner_review.json")


# ── сообщения точек ───────────────────────────────────────────────

def test_parse_message_roundtrip(tmp_path):
    _make_project(tmp_path, {"ner.json": "[]"})
    hs.create(tmp_path, "Метка с деталями", kind="apply")
    top = hs.checkpoints(tmp_path)["checkpoints"][0]
    assert top["label"] == "Метка с деталями"
    assert top["kind"] == "apply"


def test_parse_message_multiline_label_keeps_first_line(tmp_path):
    """Метка с переносом — берётся первая строка, вид не ломается."""
    _make_project(tmp_path, {"ner.json": "[]"})
    hs.create(tmp_path, "Первая строка Вторая строка", kind="run")
    top = hs.checkpoints(tmp_path)["checkpoints"][0]
    assert top["label"] == "Первая строка Вторая строка"
    assert top["kind"] == "run"


# ── автоточки (web-события) ───────────────────────────────────────

class _FakeJob:
    """Достаточно полей, чтобы on_job_finished отработал."""

    def __init__(self, tmp_path, action, argv, status="done"):
        self.status = status
        self.action = action
        self.argv = argv
        self.cwd = str(tmp_path)
        self.id = "abc123"


def test_on_job_finished_apply_creates_point(tmp_path, monkeypatch):
    from core import settings as core_settings
    monkeypatch.setattr(core_settings, "effective",
                        lambda key: key == "HISTORY_ON_APPLY")
    _make_project(tmp_path, {"ner.json": "[]"})
    job = _FakeJob(tmp_path, "ner_check",
                   ["cli/ner_check.py", "--apply", "--input", "ner.json"])
    hs.on_job_finished(job)
    data = hs.checkpoints(tmp_path)
    assert data["total"] == 1
    top = data["checkpoints"][0]
    assert top["label"] == "Применение правок глоссария"
    assert top["kind"] == "apply"


def test_on_job_finished_run_flag(tmp_path, monkeypatch):
    from core import settings as core_settings
    monkeypatch.setattr(core_settings, "effective",
                        lambda key: key == "HISTORY_ON_RUN")
    _make_project(tmp_path, {"ner.json": "[]"})
    hs.on_job_finished(_FakeJob(tmp_path, "epub", ["cli/epub_to_chapters.py"]))
    data = hs.checkpoints(tmp_path)
    top = data["checkpoints"][0]
    assert top["label"] == "Запуск: Разбор исходника на главы"
    assert top["kind"] == "run"


def test_on_job_finished_flags_off(tmp_path, monkeypatch):
    from core import settings as core_settings
    monkeypatch.setattr(core_settings, "effective", lambda key: False)
    _make_project(tmp_path, {"ner.json": "[]"})
    hs.on_job_finished(_FakeJob(tmp_path, "ner_check", ["--apply"]))
    hs.on_job_finished(_FakeJob(tmp_path, "epub", []))
    with pytest.raises(hs.HistoryError):
        hs.checkpoints(tmp_path)  # точек нет


def test_on_job_finished_dry_run_is_run_not_apply(tmp_path, monkeypatch):
    from core import settings as core_settings
    monkeypatch.setattr(core_settings, "effective",
                        lambda key: key == "HISTORY_ON_APPLY")
    _make_project(tmp_path, {"ner.json": "[]"})
    job = _FakeJob(tmp_path, "translate_check_llm",
                   ["cli/translate_check_llm.py", "--apply", "--dry-run"])
    hs.on_job_finished(job)
    # dry-run не применяет правок → точка применения не создаётся;
    # HISTORY_ON_RUN выключен → истории нет вовсе
    with pytest.raises(hs.HistoryError):
        hs.checkpoints(tmp_path)


def test_on_job_finished_failed_job_skipped(tmp_path, monkeypatch):
    from core import settings as core_settings
    monkeypatch.setattr(core_settings, "effective",
                        lambda key: key == "HISTORY_ON_APPLY")
    _make_project(tmp_path, {"ner.json": "[]"})
    hs.on_job_finished(_FakeJob(tmp_path, "ner_check", ["--apply"],
                                status="failed"))
    with pytest.raises(hs.HistoryError):
        hs.checkpoints(tmp_path)

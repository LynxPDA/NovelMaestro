#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/projects.py — менеджмент проектов: списки, каркас, переносы,
переименование. Всё во временных папках (tmp_path), без сети."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import projects as P  # noqa: E402


def _p(res: str | Path) -> Path:
    """Сузить str|Path из core.projects до Path (ок при ok=True)."""
    assert isinstance(res, Path)
    return res


@pytest.fixture
def root(tmp_path):
    for sec in P.SECTIONS:
        (tmp_path / sec).mkdir()
    return tmp_path


# ══════════════════════════════════════════════════════════════════════
# имена и списки
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("name, ok", [
    ("Моя книга", True), ("Novel_2-я.toml", True), ("a" * 120, True),
    ("", False), ("   ", False), ("a/b", False), ("a\\b", False),
    ("a:b", False), ("a*b?c", False), ('q"x', False), ("a|b", False),
    (".", False), ("..", False), ("a" * 121, False),
])
def test_valid_project_name(name, ok):
    assert P.valid_project_name(name) is ok


def test_list_projects_sorted_and_missing_section(root):
    (root / "ACTIVE" / "b").mkdir()
    (root / "ACTIVE" / "a").mkdir()
    assert P.list_projects(root, "ACTIVE") == ["a", "b"]
    assert P.list_projects(root, "NO_SUCH") == []
    assert P.list_projects(root / "нет_такой_папки", "ACTIVE") == []


# ══════════════════════════════════════════════════════════════════════
# создание
# ══════════════════════════════════════════════════════════════════════
def test_create_project_ok(root):
    ok, res = P.create_project(root, "ACTIVE", "Книга")
    assert ok and _p(res).is_dir()
    for sub in P.PROJECT_SKELETON:
        assert (_p(res) / sub).is_dir()


@pytest.mark.parametrize("section, name", [
    ("NO_SUCH", "Книга"), ("ACTIVE", ""), ("ACTIVE", "a/b"),
])
def test_create_project_rejects(root, section, name):
    ok, res = P.create_project(root, section, name)
    assert not ok and isinstance(res, str)


def test_create_project_duplicate(root):
    assert P.create_project(root, "ACTIVE", "X")[0]
    ok, res = P.create_project(root, "ACTIVE", "X")
    assert not ok and "существует" in str(res)


# ══════════════════════════════════════════════════════════════════════
# перенос между разделами
# ══════════════════════════════════════════════════════════════════════
def test_move_project_ok(root):
    P.create_project(root, "ACTIVE", "Книга")
    (root / "ACTIVE" / "Книга" / "marker.txt").write_text("x")
    ok, res = P.move_project(root, "ACTIVE", "Книга", "DONE")
    assert ok and res == root / "DONE" / "Книга"
    assert (_p(res) / "marker.txt").read_text() == "x"
    assert not (root / "ACTIVE" / "Книга").exists()
    assert P.list_projects(root, "ACTIVE") == []


@pytest.mark.parametrize("src, dst, name", [
    ("NO_SUCH", "DONE", "Книга"),        # неизвестный раздел
    ("ACTIVE", "NO_SUCH", "Книга"),      # неизвестный раздел
    ("ACTIVE", "ACTIVE", "Книга"),       # тот же раздел
    ("ACTIVE", "DONE", "Нет_такой"),     # проект не найден
])
def test_move_project_rejects(root, src, dst, name):
    P.create_project(root, "ACTIVE", "Книга")
    ok, res = P.move_project(root, src, name, dst)
    assert not ok and isinstance(res, str)


def test_move_project_duplicate_in_target(root):
    P.create_project(root, "ACTIVE", "Книга")
    P.create_project(root, "DONE", "Книга")
    ok, res = P.move_project(root, "ACTIVE", "Книга", "DONE")
    assert not ok and "уже есть" in str(res)


# ══════════════════════════════════════════════════════════════════════
# переименование и статистика
# ══════════════════════════════════════════════════════════════════════
def test_rename_project(root):
    P.create_project(root, "HOLD", "Старое")
    ok, res = P.rename_project(root, "HOLD", "Старое", "Новое")
    assert ok and _p(res).name == "Новое"
    assert P.list_projects(root, "HOLD") == ["Новое"]
    # дубль и отсутствующий проект
    P.create_project(root, "HOLD", "Дубль")
    assert not P.rename_project(root, "HOLD", "Дубль", "Новое")[0]
    assert not P.rename_project(root, "HOLD", "нет", "Имя")[0]
    assert not P.rename_project(root, "NO_SUCH", "Новое", "Имя")[0]
    assert not P.rename_project(root, "HOLD", "Новое", "a/b")[0]


def test_project_stats(tmp_path):
    pdir = tmp_path / "Книга"
    s = P.project_stats(pdir)          # несуществующий проект — нули
    assert "глав: 0" in s
    ch = pdir / "chapters"
    (ch / "001").mkdir(parents=True)
    (ch / "001" / "chapter.txt").write_text("zh")
    (ch / "001" / "translated.txt").write_text("ru")
    (ch / "001" / "redacted.txt").write_text("ru")
    (ch / "002").mkdir()
    (ch / "002" / "polished.txt").write_text("ru")
    (pdir / "ner.json").write_text("{}")
    (pdir / "wiki.md").write_text("#")
    tmp = pdir / "tmp"
    tmp.mkdir()
    (tmp / "compiled_1_x.txt").write_text("ok")
    s = P.project_stats(pdir)
    assert "глав: 2" in s and "1/1/1" in s
    assert "transl:" not in s and "polish: 1/2" not in s
    assert "ner: ✓" in s and "compiled: ✓" in s and "wiki: ✓" in s
    # легаси-имена с префиксом главы (старые проекты): chapter770_translated.txt
    (ch / "003").mkdir()
    (ch / "003" / "chapter770_translated.txt").write_text("ru")
    (ch / "003" / "chapter770_redacted.txt").write_text("ru")
    (ch / "003" / "chapter770_polished.txt").write_text("ru")
    s = P.project_stats(pdir)
    assert "глав: 3" in s and "2/2/2" in s
    # канонические и легаси-имена не должны двойно считаться в одной главе
    (ch / "003" / "translated.txt").write_text("ru")
    assert "2/2/2" in P.project_stats(pdir)
    # compiled-файлы в КОРНЕ проекта (clean_and_compile --out по умолчанию)
    pdir2 = tmp_path / "Книга2"
    pdir2.mkdir()
    (pdir2 / "compiled_5_10_txt.txt").write_text("ok")
    assert "compiled: ✓" in P.project_stats(pdir2)
    (pdir2 / "compiled_5_10_txt.txt").unlink()
    (pdir2 / "book_5_10.epub").write_text("ok")
    assert "compiled: ✓" in P.project_stats(pdir2)
    # экспорты по имени проекта {Имя}_{start}_{end}.epub/.fb2
    (pdir2 / "book_5_10.epub").unlink()
    (pdir2 / "Книга2_5_10.epub").write_text("ok")
    assert "compiled: ✓" in P.project_stats(pdir2)
    (pdir2 / "Книга2_5_10.epub").unlink()
    (pdir2 / "Книга2_5_10.fb2").write_text("ok")
    assert "compiled: ✓" in P.project_stats(pdir2)


# ──────────────────────────────────────────────────────────────────────
# sanitize_project_name / ensure_projects_root / шаблоны / metadata
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw, want", [
    ("My Novel", "My_Novel"),
    ("  Some  Name!! ", "Some_Name"),
    ("a__b", "a_b"),
    ("_lead_", "lead"),
    ("My.Novel-2", "My.Novel-2"),
    ("Тест имя", ""),          # кириллица вычищается полностью
    ("...", ""),
    ("", ""),
])
def test_sanitize_project_name(raw, want):
    assert P.sanitize_project_name(raw) == want


def test_ensure_projects_root(tmp_path):
    root = tmp_path / "projects"
    created = P.ensure_projects_root(root)
    assert set(created) == set(P.SECTIONS)
    for sec in P.SECTIONS:
        assert (root / sec).is_dir()
    assert P.ensure_projects_root(root) == []  # идемпотентно


def test_list_template_sets(tmp_path):
    tpl = tmp_path / "templates"
    (tpl / "General" / "prompts").mkdir(parents=True)
    (tpl / "Xianxia" / "prompts").mkdir(parents=True)
    (tpl / "NoPrompts").mkdir(parents=True)
    assert P.list_template_sets(tpl) == ["General", "Xianxia"]
    assert P.list_template_sets(tpl / "нет_такой") == []


META_TEMPLATE = (
    '---\ntitle: ""\nauthor: ""\ndescription: "d"\nrights: ""\n'
    'date: "2020-01-01"\nlanguage: "ru-RU"\nsubject: \n'
    '  - Фантастика\n  - Приключения\nidentifier: ""\n---\n')


def test_render_metadata_fields():
    out = P.render_metadata(META_TEMPLATE, title='Мой "роман"',
                            author="Автор", genres=["Фэнтези", "Боевик"],
                            date="2026-01-01")
    assert 'title: "Мой \\"роман\\""' in out
    assert 'author: "Автор"' in out
    assert 'date: "2026-01-01"' in out
    assert "  - Фэнтези" in out and "  - Боевик" in out
    assert "Фантастика" not in out
    assert 'description: "d"' in out and 'identifier: ""' in out


def test_render_metadata_keep_subject_when_none():
    assert "  - Фантастика" in P.render_metadata(META_TEMPLATE, title="Т")


def test_render_metadata_empty_genres():
    out = P.render_metadata(META_TEMPLATE, genres=[])
    assert "Фантастика" not in out and "subject:" in out


def test_render_metadata_default_date_today():
    import datetime
    assert datetime.date.today().isoformat() in P.render_metadata(META_TEMPLATE)


def test_fill_project_from_template(tmp_path):
    tpl = tmp_path / "T"
    (tpl / "prompts").mkdir(parents=True)
    (tpl / "source").mkdir(parents=True)
    (tpl / "prompts" / "translate_prompt.txt").write_text("p", encoding="utf-8")
    (tpl / "source" / "metadata.yaml").write_text("m", encoding="utf-8")
    pdir = tmp_path / "proj"
    (pdir / "prompts").mkdir(parents=True)
    # существующий файл не перезаписывается
    (pdir / "prompts" / "translate_prompt.txt").write_text("keep", encoding="utf-8")
    copied = P.fill_project_from_template(pdir, tpl)
    assert copied == ["source/metadata.yaml"]
    assert (pdir / "prompts" / "translate_prompt.txt").read_text() == "keep"


def test_fill_template_general_files(tmp_path):
    """info.md/donate.txt/файл из prompts из General попадают в проект."""
    tpl = tmp_path / "T"
    (tpl / "prompts").mkdir(parents=True)
    (tpl / "source").mkdir(parents=True)
    (tpl / "source" / "info.md").write_text("# Инфо", encoding="utf-8")
    (tpl / "source" / "donate.txt").write_text("Донат", encoding="utf-8")
    (tpl / "prompts" / "extra_rules.txt").write_text("A -> B", encoding="utf-8")
    pdir = tmp_path / "proj"
    copied = P.fill_project_from_template(pdir, tpl)
    assert "source/info.md" in copied
    assert "source/donate.txt" in copied
    assert "prompts/extra_rules.txt" in copied
    assert (pdir / "source" / "info.md").read_text(encoding="utf-8") == "# Инфо"
    assert (pdir / "prompts" / "extra_rules.txt").read_text(encoding="utf-8") == "A -> B"


def test_write_project_metadata(tmp_path):
    tpl = tmp_path / "T"
    (tpl / "source").mkdir(parents=True)
    (tpl / "source" / "metadata.yaml").write_text(META_TEMPLATE, encoding="utf-8")
    pdir = tmp_path / "proj"
    assert P.write_project_metadata(pdir, tpl, title="Т", author="А",
                                    genres=["Ж"])
    text = (pdir / "source" / "metadata.yaml").read_text(encoding="utf-8")
    assert 'title: "Т"' in text and "  - Ж" in text
    # повторно не перезаписывает
    assert not P.write_project_metadata(pdir, tpl, title="Другое")


def test_delete_project(tmp_path):
    ok, p = P.create_project(tmp_path, "ACTIVE", "X")
    assert ok and _p(p).is_dir()
    ok, res = P.delete_project(tmp_path, "ACTIVE", "X")
    assert ok and not _p(res).exists()
    ok, msg = P.delete_project(tmp_path, "ACTIVE", "X")
    assert not ok and "не найден" in str(msg)
    ok, msg = P.delete_project(tmp_path, "BAD", "X")
    assert not ok


def test_copy_project(tmp_path):
    ok, p = P.create_project(tmp_path, "ACTIVE", "X")
    (_p(p) / "source" / "a.txt").write_text("1", encoding="utf-8")
    ok, dst = P.copy_project(tmp_path, "ACTIVE", "X", "X_copy")
    assert ok and (_p(dst) / "source" / "a.txt").is_file()
    ok, msg = P.copy_project(tmp_path, "ACTIVE", "X", "X_copy")
    assert not ok and "уже есть" in str(msg)
    ok, msg = P.copy_project(tmp_path, "ACTIVE", "Нет_такого", "Y")
    assert not ok


# ── разделы (создание/переименование/удаление) ──

def test_sections_defaults_and_persist(tmp_path):
    root = tmp_path / "projects"
    P.ensure_projects_root(root)
    assert P.load_sections(root) == ["ACTIVE", "HOLD", "DONE"]
    # персист: файл .sections.json в корне projects/
    assert (root / P.SECTIONS_FILE).is_file()
    # кастомная папка на диске до-обнаруживается (ручные папки)
    (root / "Резерв").mkdir()
    assert "Резерв" in P.load_sections(root)


def test_sections_legacy_migration(tmp_path):
    """DONE_OPEN со старых установок подхватывается как кастомный."""
    root = tmp_path / "projects"
    (root / "ACTIVE").mkdir(parents=True)
    (root / "HOLD").mkdir()
    (root / "DONE").mkdir()
    (root / "DONE_OPEN").mkdir()
    created = P.ensure_projects_root(root)
    assert created == []  # все дефолтные уже есть
    assert P.load_sections(root) == ["ACTIVE", "HOLD", "DONE", "DONE_OPEN"]


def test_create_section(tmp_path):
    root = tmp_path / "projects"
    P.ensure_projects_root(root)
    ok, name = P.create_section(root, "Архив")
    assert ok and name == "Архив"
    assert (root / "Архив").is_dir()
    assert "Архив" in P.load_sections(root)
    # дубль / недопустимое имя
    ok, msg = P.create_section(root, "Архив")
    assert not ok and "уже существует" in str(msg)
    ok, msg = P.create_section(root, "a/b")
    assert not ok


def test_rename_section_plain(tmp_path):
    root = tmp_path / "projects"
    P.ensure_projects_root(root)
    P.create_section(root, "Архив")
    ok, name = P.rename_section(root, "Архив", "Законченные")
    assert ok and name == "Законченные"
    assert (root / "Законченные").is_dir()
    assert not (root / "Архив").exists()
    assert "Архив" not in P.load_sections(root)
    assert "Законченные" in P.load_sections(root)


def test_rename_section_merge(tmp_path):
    """Переименование в существующий раздел — проекты переносятся."""
    root = tmp_path / "projects"
    P.ensure_projects_root(root)
    P.create_section(root, "Архив")
    ok, _ = P.create_project(root, "Архив", "Книга")
    assert ok
    ok, name = P.rename_section(root, "Архив", "DONE")
    assert ok and name == "DONE"
    assert (root / "DONE" / "Книга").is_dir()
    assert not (root / "Архив").exists()
    # коллизия имени проекта → отказ
    P.create_section(root, "Второй")
    ok, _ = P.create_project(root, "Второй", "Книга")
    assert ok
    ok, msg = P.rename_section(root, "Второй", "DONE")
    assert not ok and "уже есть проект" in str(msg)


def test_rename_default_section_allowed(tmp_path):
    root = tmp_path / "projects"
    P.ensure_projects_root(root)
    ok, name = P.rename_section(root, "ACTIVE", "В работе")
    assert ok and name == "В работе"
    assert (root / "В работе").is_dir()
    assert not (root / "ACTIVE").exists()
    assert P.load_sections(root) == ["В работе", "HOLD", "DONE"]


def test_delete_section(tmp_path):
    root = tmp_path / "projects"
    P.ensure_projects_root(root)
    P.create_section(root, "Пустой")
    ok, name = P.delete_section(root, "Пустой")
    assert ok and name == "Пустой"
    assert not (root / "Пустой").exists()
    assert "Пустой" not in P.load_sections(root)
    # непустой — отказ
    P.create_section(root, "Непустой")
    ok, _ = P.create_project(root, "Непустой", "Книга")
    assert ok
    ok, msg = P.delete_section(root, "Непустой")
    assert not ok and "не пуст" in str(msg)
    assert (root / "Непустой").is_dir()
    # неизвестный
    ok, msg = P.delete_section(root, "Нет_такого")
    assert not ok and "не найден" in str(msg)


def test_sections_custom_not_touched_by_bootstrap(tmp_path):
    """ensure_projects_root не трогает кастомные разделы (свежая установка)."""
    root = tmp_path / "projects"
    P.ensure_projects_root(root)
    P.create_section(root, "Архив")
    (root / "Архив" / "Книга").mkdir()
    assert P.ensure_projects_root(root) == []
    assert (root / "Архив" / "Книга").is_dir()
    assert P.load_sections(root) == ["ACTIVE", "HOLD", "DONE", "Архив"]


def test_load_sections_dedupes_file_entries(tmp_path):
    """Дубли в .sections.json (след гонки) показываются один раз."""
    root = tmp_path / "projects"
    P.ensure_projects_root(root)
    P.create_section(root, "Архив")
    f = root / P.SECTIONS_FILE
    data = json.loads(f.read_text(encoding="utf-8"))
    data.append("Архив")
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert P.load_sections(root).count("Архив") == 1


def test_load_sections_drops_phantom_entries(tmp_path):
    """Запись файла без папки на диске — призрак — не показывается."""
    root = tmp_path / "projects"
    P.ensure_projects_root(root)
    f = root / P.SECTIONS_FILE
    data = json.loads(f.read_text(encoding="utf-8"))
    data.append("Призрак")
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert "Призрак" not in P.load_sections(root)
    # реальная папка на диске по-прежнему до-обнаруживается
    (root / "Ручная").mkdir()
    sections = P.load_sections(root)
    assert "Ручная" in sections and sections.count("Призрак") == 0


def test_concurrent_create_section_unique(tmp_path):
    """Параллельные create_section одного имени — одна запись (мьютекс).

    ThreadingHTTPServer гоняет запросы в отдельных тредах; без мьютекса
    read-modify-write .sections.json плодил дубли раздела.
    """
    import threading
    root = tmp_path / "projects"
    P.ensure_projects_root(root)
    results: list = []

    def _worker():
        results.append(P.create_section(root, "Архив"))

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for ok, _ in results if ok) == 1
    assert P.load_sections(root).count("Архив") == 1
    assert (root / "Архив").is_dir()


def test_concurrent_create_project_unique(tmp_path):
    """Параллельные create_project одного имени — один успех (мьютекс).

    ThreadingHTTPServer гоняет запросы в тредах; без мьютекса оба могли
    пройти проверку dst.exists() (TOCTOU).
    """
    import threading
    P.ensure_projects_root(tmp_path)
    results: list = []

    def _worker():
        results.append(P.create_project(tmp_path, "ACTIVE", "Книга"))

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for ok, _ in results if ok) == 1
    assert (tmp_path / "ACTIVE" / "Книга").is_dir()


def test_copy_project_skips_env(tmp_path):
    """Копия проекта не наследует проектный .env (ключи/профили)."""
    P.ensure_projects_root(tmp_path)
    ok, res = P.create_project(tmp_path, "ACTIVE", "X")
    assert ok
    pdir = _p(res)
    (pdir / ".env").write_text("API_KEY=secret", encoding="utf-8")
    (pdir / "source" / "a.txt").write_text("1", encoding="utf-8")
    ok, res = P.copy_project(tmp_path, "ACTIVE", "X", "X_copy")
    assert ok
    dst = _p(res)
    assert not (dst / ".env").exists()
    assert (dst / "source" / "a.txt").read_text(encoding="utf-8") == "1"


# ── шаблоны (CRUD) ────────────────────────────────

def _mk_tpl_set(root: Path, name: str) -> Path:
    s = root / name
    (s / "prompts").mkdir(parents=True)
    (s / "source").mkdir()
    (s / "prompts" / "translate.txt").write_text("t", encoding="utf-8")
    (s / "source" / "metadata.yaml").write_text("m", encoding="utf-8")
    return s


def test_create_template_set(tmp_path):
    assert P.create_template_set(tmp_path, "Mine") == "Mine"
    assert (tmp_path / "Mine" / "prompts").is_dir()
    # каркас как у General — prompts/ + source/
    assert (tmp_path / "Mine" / "source").is_dir()
    # дубль
    assert P.create_template_set(tmp_path, "Mine") is None
    # недопустимое имя / General
    assert P.create_template_set(tmp_path, "../bad") is None
    assert P.create_template_set(tmp_path, "General") is None


def test_copy_template_set(tmp_path):
    _mk_tpl_set(tmp_path, "Src")
    assert P.copy_template_set(tmp_path, "Src", "Dst") == "Dst"
    assert (tmp_path / "Dst" / "prompts" / "translate.txt").is_file()
    assert (tmp_path / "Dst" / "source" / "metadata.yaml").is_file()
    # копирование ИЗ General разрешено
    _mk_tpl_set(tmp_path, "General")
    assert P.copy_template_set(tmp_path, "General", "FromGen") == "FromGen"
    assert (tmp_path / "FromGen" / "prompts").is_dir()
    # дубль / нет исходника
    assert P.copy_template_set(tmp_path, "Src", "Dst") is None
    assert P.copy_template_set(tmp_path, "NoSuch", "New") is None


def test_template_skeleton_copy_repairs_degraded(tmp_path):
    """копия всегда со скелетом, даже если исходник деградировал."""
    s = _mk_tpl_set(tmp_path, "Src")
    # «деградация»: исходник потерял каталог source/
    import shutil
    shutil.rmtree(s / "source")
    assert P.copy_template_set(tmp_path, "Src", "Dst") == "Dst"
    assert (tmp_path / "Dst" / "prompts").is_dir()
    assert (tmp_path / "Dst" / "source").is_dir()
    # скелет не создаётся поверх файла
    (tmp_path / "Dst" / "prompts" / "translate.txt").write_text("t")
    (tmp_path / "Dst" / "prompts" / "translate.txt").unlink()


def test_template_skeleton_ensure(tmp_path):
    """_ensure_template_skeleton идемпотентен и создаёт оба каталога."""
    d = tmp_path / "T"
    d.mkdir()
    P._ensure_template_skeleton(d)
    assert (d / "prompts").is_dir() and (d / "source").is_dir()
    (d / "prompts").rmdir()
    P._ensure_template_skeleton(d)  # повторно — восстанавливает
    assert (d / "prompts").is_dir() and (d / "source").is_dir()


def test_delete_template_set(tmp_path):
    _mk_tpl_set(tmp_path, "Del")
    assert P.delete_template_set(tmp_path, "Del") is True
    assert not (tmp_path / "Del").exists()
    # General защищён
    _mk_tpl_set(tmp_path, "General")
    assert P.delete_template_set(tmp_path, "General") is False
    # нет набора
    assert P.delete_template_set(tmp_path, "NoSuch") is False


def test_templates_files(tmp_path):
    _mk_tpl_set(tmp_path, "T")
    files = P.templates_files(tmp_path, "T")
    assert "prompts/translate.txt" in files
    assert "source/metadata.yaml" in files
    assert P.templates_files(tmp_path, "NoSuch") == []


def test_templates_empty_dirs_visible(tmp_path):
    """пустой каталог не пропадает из списка (trailing '/')."""
    s = _mk_tpl_set(tmp_path, "T")
    (s / "prompts" / "extra").mkdir()
    files = P.templates_files(tmp_path, "T")
    assert "prompts/extra/" in files
    assert "prompts/translate.txt" in files
    # непустой каталог НЕ дублируется (его выводит dirEntries из файлов)
    assert "prompts/" not in files


def test_delete_template_dir_forbidden(tmp_path):
    """каталоги в шаблонах неизменяемы — удаление запрещено."""
    s = _mk_tpl_set(tmp_path, "T")
    (s / "prompts" / "extra").mkdir()
    (s / "prompts" / "extra" / "x.txt").write_text("x", encoding="utf-8")
    err = P.delete_template_file(tmp_path, "T", "prompts/extra")
    assert err and "Каталоги в шаблонах неизменяемы" in err
    assert (s / "prompts" / "extra" / "x.txt").is_file()
    # пустой каталог тоже запрещён
    (s / "prompts" / "empty").mkdir()
    err = P.delete_template_file(tmp_path, "T", "prompts/empty")
    assert err and "Каталоги" in err
    # скелет (prompts/source) тоже каталоги → запрещены
    err = P.delete_template_file(tmp_path, "T", "prompts")
    assert err and "Каталоги" in err
    # файл по-прежнему удаляется
    assert P.delete_template_file(tmp_path, "T", "prompts/translate.txt") is None
    assert not (s / "prompts" / "translate.txt").exists()
    # нет пути / General / нет набора
    err = P.delete_template_file(tmp_path, "T", "prompts/nope.txt")
    assert err is not None and "не найден" in err
    err = P.delete_template_file(tmp_path, "General", "prompts")
    assert err is not None and "General" in err
    err = P.delete_template_file(tmp_path, "NoSuch", "x")
    assert err is not None and "не найден" in err


def test_move_template_dir_forbidden(tmp_path):
    """каталоги в шаблонах неизменяемы — перенос запрещён."""
    s = _mk_tpl_set(tmp_path, "T")
    (s / "prompts" / "extra").mkdir()
    (s / "prompts" / "extra" / "x.txt").write_text("x", encoding="utf-8")
    # переименование каталога
    err = P.move_template_file(tmp_path, "T", "prompts/extra", "prompts/more")
    assert err and "Каталоги в шаблонах неизменяемы" in err
    assert (s / "prompts" / "extra" / "x.txt").is_file()
    assert not (s / "prompts" / "more").exists()
    # скелет тоже каталог
    err = P.move_template_file(tmp_path, "T", "prompts", "prompts2")
    assert err and "Каталоги" in err
    # файлы переименовываются как раньше
    assert P.move_template_file(tmp_path, "T", "prompts/translate.txt",
                                "prompts/main.txt") is None
    assert (s / "prompts" / "main.txt").is_file()


def test_create_template_dir_forbidden(tmp_path):
    """создание каталогов в шаблонах запрещено всегда."""
    s = _mk_tpl_set(tmp_path, "T")
    err = P.create_template_dir(tmp_path, "T", "prompts/extra")
    assert err and "Каталоги в шаблонах неизменяемы" in err
    assert not (s / "prompts" / "extra").exists()
    # General запрещён
    _mk_tpl_set(tmp_path, "General")
    err = P.create_template_dir(tmp_path, "General", "prompts/x")
    assert err and "General" in err
    # нет набора
    assert P.create_template_dir(tmp_path, "NoSuch", "x") == "Набор не найден"


def test_read_write_delete_template_file(tmp_path):
    s = _mk_tpl_set(tmp_path, "T")
    assert P.read_template_file(tmp_path, "T", "prompts/translate.txt") == "t"
    assert P.read_template_file(tmp_path, "T", "prompts/nope.txt") is None
    # запись нового файла (родительский каталог существует)
    assert P.write_template_file(tmp_path, "T", "prompts/new.txt", "x") is None
    assert (s / "prompts" / "new.txt").read_text() == "x"
    # перезапись
    assert P.write_template_file(tmp_path, "T", "prompts/translate.txt",
                                 "t2") is None
    assert (s / "prompts" / "translate.txt").read_text() == "t2"
    # запись в несуществующий каталог запрещена (неявный mkdir)
    err = P.write_template_file(tmp_path, "T", "prompts/extra/x.txt", "x")
    assert err is not None and "Каталог не существует" in err
    assert not (s / "prompts" / "extra").exists()
    # а в существующий вложенный каталог — можно (легаси-каталог на диске)
    (s / "source" / "legacy").mkdir()
    assert P.write_template_file(tmp_path, "T", "source/legacy/x.txt",
                                 "x") is None
    assert (s / "source" / "legacy" / "x.txt").read_text() == "x"
    # удаление
    assert P.delete_template_file(tmp_path, "T", "prompts/new.txt") is None
    assert not (s / "prompts" / "new.txt").exists()


def test_template_escape_protected(tmp_path):
    _mk_tpl_set(tmp_path, "T")
    assert P.read_template_file(tmp_path, "T", "../../evil.txt") is None
    err = P.write_template_file(tmp_path, "T", "../escape.txt", "x")
    assert err is not None and "Недопустимый" in err  # эскейп = нет пути
    err = P.delete_template_file(tmp_path, "T", "../escape.txt")
    assert err is not None and "Файл не найден" in err  # эскейп = нет пути
    assert not (tmp_path / "escape.txt").exists()
    # запись в General запрещена
    _mk_tpl_set(tmp_path, "General")
    err = P.write_template_file(tmp_path, "General", "prompts/x.txt", "x")
    assert err is not None and "General" in err
    err = P.delete_template_file(tmp_path, "General", "prompts/translate.txt")
    assert err is not None and "General" in err


def test_template_file_info(tmp_path):
    _mk_tpl_set(tmp_path, "T")
    info = P.template_file_info(tmp_path, "T", "prompts/translate.txt")
    assert info is not None and info["size"] == 1
    assert isinstance(info["mtime"], int)
    # нет файла / нет набора / эскейп
    assert P.template_file_info(tmp_path, "T", "prompts/nope.txt") is None
    assert P.template_file_info(tmp_path, "NoSuch", "prompts/x.txt") is None
    assert P.template_file_info(tmp_path, "T", "../../evil.txt") is None


def test_move_template_file(tmp_path):
    s = _mk_tpl_set(tmp_path, "T")
    # переименование в той же папке
    assert P.move_template_file(tmp_path, "T", "prompts/translate.txt",
                                "prompts/main.txt") is None
    assert (s / "prompts" / "main.txt").is_file()
    assert not (s / "prompts" / "translate.txt").exists()
    # перенос в несуществующий каталог запрещён
    err = P.move_template_file(tmp_path, "T", "prompts/main.txt",
                               "source/new/deep.txt")
    assert err is not None and "Каталог не существует" in err
    assert not (s / "source" / "new").exists()
    # перенос в существующий каталог — можно
    assert P.move_template_file(tmp_path, "T", "prompts/main.txt",
                                "source/deep.txt") is None
    assert (s / "source" / "deep.txt").is_file()
    # нет исходника
    assert P.move_template_file(tmp_path, "T", "prompts/main.txt",
                                "prompts/x.txt") is not None
    # назначение занято
    assert P.move_template_file(tmp_path, "T", "source/deep.txt",
                                "source/metadata.yaml") is not None
    # эскейпы
    assert P.move_template_file(tmp_path, "T", "source/metadata.yaml",
                                "../evil.txt") is not None
    assert not (tmp_path / "evil.txt").exists()
    # General запрещён
    _mk_tpl_set(tmp_path, "General")
    assert P.move_template_file(tmp_path, "General", "prompts/translate.txt",
                                "prompts/x.txt") is not None


# ── таблица готовности глав ───────────────────────

def test_project_progress_table(tmp_path):
    pdir = tmp_path / "proj"
    (pdir / "chapters" / "001").mkdir(parents=True)
    (pdir / "chapters" / "002").mkdir()
    (pdir / "chapters" / "001" / "translated.txt").write_text("t", encoding="utf-8")
    (pdir / "chapters" / "002" / "translated.txt").write_text("t", encoding="utf-8")
    (pdir / "chapters" / "002" / "polished.txt").write_text("p", encoding="utf-8")
    (pdir / "ner.json").write_text('[{ "term": "x" }]', encoding="utf-8")
    (pdir / "wiki.md").write_text("## Статья\n## Другая\n", encoding="utf-8")
    (pdir / "compiled_book.txt").write_text("c", encoding="utf-8")
    # экспорт {имя_проекта}_{start}_{end}.epub виден в compiled
    (pdir / "proj_1_2.epub").write_text("e", encoding="utf-8")
    (pdir / "proj_5_6.fb2").write_text("f", encoding="utf-8")
    st = P.project_progress_table(pdir)
    assert st["counts"] == {"chapters": 2, "translate": 2, "redact": 0, "polish": 1}
    assert st["chapters"][1]["translate"] is True
    assert st["chapters"][1]["polish"] is False
    assert st["chapters"][2]["polish"] is True
    assert st["ner"] == {"exists": True, "terms": 1}
    assert st["wiki"] == {"exists": True, "articles": 2}
    assert "compiled_book.txt" in st["compiled"]
    assert "proj_1_2.epub" in st["compiled"]
    assert "proj_5_6.fb2" in st["compiled"]


def test_project_progress_table_empty(tmp_path):
    pdir = tmp_path / "empty"
    pdir.mkdir()
    st = P.project_progress_table(pdir)
    assert st["counts"]["chapters"] == 0
    assert st["ner"] == {"exists": False, "terms": 0}
    assert st["wiki"] == {"exists": False, "articles": 0}
    assert st["compiled"] == []


def test_project_progress_table_legacy_names(tmp_path):
    # легаси-артефакты: суффиксы _translated.txt (канон папок — тот же)
    pdir = tmp_path / "legacy"
    (pdir / "chapters" / "000770").mkdir(parents=True)
    (pdir / "chapters" / "000770" / "000770_translated.txt").write_text("t", encoding="utf-8")
    st = P.project_progress_table(pdir)
    assert st["chapters"][770]["translate"] is True
    assert st["chapters"][770]["redact"] is False


def test_project_progress_table_empty_artifact_not_done(tmp_path):
    """Пустой артефакт (0 байт) НЕ считается готовым: галочка только
    при непустом файле (канон и легаси-суффикс)."""
    pdir = tmp_path / "proj"
    (pdir / "chapters" / "001").mkdir(parents=True)
    (pdir / "chapters" / "002").mkdir()
    (pdir / "chapters" / "003").mkdir()
    # 001: пустой translated.txt — не готов; 002: непустой — готов;
    # 003: пустой легаси-файл — не готов, непустой polished — готов
    (pdir / "chapters" / "001" / "translated.txt").write_text("", encoding="utf-8")
    (pdir / "chapters" / "002" / "translated.txt").write_text("t", encoding="utf-8")
    (pdir / "chapters" / "003" / "003_redacted.txt").write_text("", encoding="utf-8")
    (pdir / "chapters" / "003" / "003_polished.txt").write_text("p", encoding="utf-8")
    st = P.project_progress_table(pdir)
    assert st["chapters"][1]["translate"] is False  # пустой файл
    assert st["chapters"][2]["translate"] is True
    assert st["chapters"][3]["redact"] is False  # пустой легаси
    assert st["chapters"][3]["polish"] is True
    assert st["counts"] == {"chapters": 3, "translate": 1, "redact": 0,
                             "polish": 1}

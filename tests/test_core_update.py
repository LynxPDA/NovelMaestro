#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Обновления программы (core/update.py): способ установки, релиз GitHub,
защита данных и применение обновления.

Сеть наружу не ходит: GitHub ответы мокируются на core.transport (один общий
GET и для релиза, и для ассетов). git-установка — настоящий локальный
репозиторий в tmp_path (dulwich, бинарник git не нужен), портативная — zip,
собранный тут же. Перезапуск проверяется заглушками _spawn/_terminate:
настоящий наблюдатель поднимается отдельным процессом на Linux.
"""
import io
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dulwich import porcelain  # noqa: E402
from dulwich.refs import Ref  # noqa: E402
from dulwich.repo import Repo  # noqa: E402

from core import update as U  # noqa: E402

# ══════════════════════════════════════════════════════════════════
# СПОСОБ УСТАНОВКИ
# ══════════════════════════════════════════════════════════════════
def test_install_kind_by_marker(tmp_path):
    """Способ установки по маркерам; порядок: docker важнее portable, тот — git."""
    assert U.install_kind(tmp_path) == "tree"
    (tmp_path / ".git").mkdir()
    assert U.install_kind(tmp_path) == "git"
    (tmp_path / "START.txt").write_text("", encoding="utf-8")
    assert U.install_kind(tmp_path) == "portable"
    (tmp_path / ".docker").write_text("", encoding="utf-8")
    assert U.install_kind(tmp_path) == "docker"


@pytest.mark.parametrize("kind,mode", [
    ("docker", "command"), ("portable", "zip"), ("git", "git"), ("tree", "zip"),
])
def test_update_mode_per_kind(kind, mode):
    """Каждая установка знает свой путь: команда, git или архив."""
    assert U.update_mode(kind) == mode


def test_update_mode_unknown_kind_is_zip():
    """Незнакомый способ — самый безопасный путь: наложение архива."""
    assert U.update_mode("нечто-не-то") == "zip"


def test_installed_version(tmp_path):
    """Версия установленной копии — из файла VERSION (пустого — пусто)."""
    (tmp_path / "VERSION").write_text("0.4.0\n", encoding="utf-8")
    assert U.installed_version(tmp_path) == "0.4.0"
    assert U.installed_version(tmp_path / "нет-такой-папки") == ""


def test_host_command_hint():
    """Docker: текст объяснения обязан называть команду хоста."""
    text = U.host_command_hint()
    assert "docker compose pull" in text and "docker compose up -d" in text


# ══════════════════════════════════════════════════════════════════
# ВЕРСИИ И РЕЛИЗ
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("cur,rel,want", [
    ("0.3.9", "0.3.10", True),
    ("0.3.10", "0.3.9", False),
    ("0.3.6", "0.3.6", False),
    ("0.3", "0.3.1", True),
    ("", "0.1", True),
    ("0.4.0", "", False),
], ids=["older", "newer", "same", "short-current", "empty-current", "no-release"])
def test_version_tuple_compare(cur, rel, want):
    """Сравнение версий без сторонних библиотек: по числам, x.y.z."""
    assert (U.version_tuple(rel) > U.version_tuple(cur)) is want


def test_version_tuple_garbage_is_zero():
    """Мусор в части версии — ноль, не падение (v-префикс и суффиксы)."""
    assert U.version_tuple("v0.4b2") == (0, 4)
    assert U.version_tuple("") == (0,)


def test_update_available_needs_ok_release():
    """Без успешного релиза обновления нет — не падение (и None тоже)."""
    assert not U.update_available("0.1", None)
    assert not U.update_available("0.1", {"ok": False})


@pytest.mark.parametrize("body,notes,changes", [
    ("", "", ""),
    ("**Full Changelog**: https://github.com/a/b/compare/v1...v2", "",
     "https://github.com/a/b/compare/v1...v2"),
    ("- **жирный** текст\n- второй пункт\n\n"
     "**Full Changelog**: https://github.com/a/b/compare/v1...v2",
     "- **жирный** текст\n- второй пункт",
     "https://github.com/a/b/compare/v1...v2"),
    ("обычное описание", "обычное описание", ""),
], ids=["empty", "only-diff-line", "notes-and-diff", "no-diff-line"])
def test_release_notes(body, notes, changes):
    """Заметки остаются markdown'ом, ссылка на сравнение — отдельной ссылкой."""
    assert U.release_notes(body) == (notes, changes)


def test_release_notes_trims_long_body():
    """Длинное описание релиза обрезается, а не тащится в интерфейс целиком."""
    notes, _ = U.release_notes("x" * 5000)
    assert len(notes) == U.MAX_NOTES


def test_latest_release_ok(monkeypatch):
    """Живой ответ GitHub: tag без v, заметки чищены, ссылки пробрасываются."""
    diff = "https://github.com/a/b/compare/v1.2.2...v1.2.3"

    def fake_get(url, *, timeout=None):
        assert "releases/latest" in url
        return {"tag_name": "v1.2.3", "name": "Релиз 1.2.3",
                "html_url": "https://example.com/r",
                "published_at": "2026-01-01T00:00:00Z",
                "body": f"**Full Changelog**: {diff}"}
    monkeypatch.setattr(U.transport, "open_json_get", fake_get)
    out = U.latest_release()
    assert out["ok"] and out["version"] == "1.2.3" and out["tag"] == "v1.2.3"
    assert out["url"] == "https://example.com/r"
    assert out["published"] == "2026-01-01T00:00:00Z"
    assert out["notes"] == "" and out["changes"] == diff


def test_latest_release_network_error(monkeypatch):
    """Сеть лежит — отчёт {ok: False, error}, а не исключение."""
    def fake_get(url, *, timeout=None):
        raise U.transport.ConnectTimeout("нет сети")
    monkeypatch.setattr(U.transport, "open_json_get", fake_get)
    out = U.latest_release()
    assert not out["ok"] and "нет сети" in out["error"]


def test_release_asset_url_picks_portable_zip(monkeypatch):
    """Ассет ищется по имени портативной сборки, чужие ассеты пропускаются."""
    def fake_get(url, *, timeout=None):
        assert "/releases/tags/" in url and "v9.9.0" in url
        return {"assets": [{"name": "другое.txt",
                            "browser_download_url": "https://x/other"},
                           {"name": "novelmaestro-portable-9.9.0.zip",
                            "browser_download_url": "https://x/zip"}]}
    monkeypatch.setattr(U.transport, "open_json_get", fake_get)
    assert U.release_asset_url("v9.9.0") == "https://x/zip"


def test_release_asset_url_without_asset(monkeypatch):
    """В релизе нет архива портативки — это UpdateError, а не падение."""
    monkeypatch.setattr(U.transport, "open_json_get",
                        lambda url, *, timeout=None: {"assets": []})
    with pytest.raises(U.UpdateError) as exc:
        U.release_asset_url("v9.9.0")
    assert "нет архива" in str(exc.value)


def test_download_asset_reports_status(monkeypatch):
    """Не-200 на скачивании — UpdateError с кодом; 200 — сырое тело."""
    class Resp:
        def __init__(self, body, status_code=200):
            self._body, self.status_code = body, status_code

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def iter_bytes(self):
            yield self._body

    monkeypatch.setattr(U.transport, "open_get",
                        lambda url, **kw: Resp(b"nope", 404))
    with pytest.raises(U.UpdateError) as exc:
        U.download_asset("https://x/zip")
    assert "HTTP 404" in str(exc.value)

    monkeypatch.setattr(U.transport, "open_get",
                        lambda url, **kw: Resp(b"PK zip"))
    assert U.download_asset("https://x/zip") == b"PK zip"


# ══════════════════════════════════════════════════════════════════
# ДАННЫЕ ПОЛЬЗОВАТЕЛЯ
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("rel,protected", [
    ("core/common.py", False),
    ("web/static/app.js", False),
    ("projects/ACTIVE/book/chapters/chapter.txt", True),
    ("templates/General/prompts/translate.txt", False),
    ("templates/.env.example", False),
    ("templates/Custom/prompts/translate.txt", True),
    ("templates", True),
    ("web/jobs.json", True),
    ("web/job_logs/12.log", True),
    ("logs/chapters/x.log", True),
    ("llm_profiles.json", True),
    (".env", True),
    ("python/python.exe", True),
    (".venv/lib/httpx/__init__.py", True),
    ("core/__pycache__/common.cpython-314.pyc", True),
    ("web\\job_logs\\12.log", True),
    ("../../etc/passwd", True),
    ("C:/Windows/win.ini", True),
    ("", True),
], ids=["code", "code-spa", "projects", "tpl-general", "tpl-env",
        "tpl-custom", "tpl-dir", "job_queue", "job_logs", "logs", "profiles",
        "env", "interpreter", "venv", "pycache", "backslash", "escape",
        "drive", "empty"])
def test_is_protected(rel, protected):
    """Данные и окружение сборки не перезаписываются; код — перезаписывается."""
    assert U.is_protected(rel) is protected


# ══════════════════════════════════════════════════════════════════
# ПРИМЕНЕНИЕ АРХИВА (ПОРТАТИВНАЯ СБОРКА И КОПИЯ ИСХОДНИКОВ)
# ══════════════════════════════════════════════════════════════════
def _portable_zip(requirements: str = "httpx\n") -> bytes:
    """Архив релиза: один верхний каталог, код — и чужие данные внутри него."""
    buf = io.BytesIO()
    top = "novelmaestro-portable"
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in [
            ("VERSION", "9.9.0"),
            ("requirements.txt", requirements),
            ("core/common.py", "НОВЫЙ КОД\n"),
            ("web/static/app.js", "// новый SPA\n"),
            ("web/job_logs/12.log", "новая история запусков\n"),
            ("web/jobs.json", "новая очередь\n"),
            ("templates/General/prompts/translate.txt", "промпт релиза\n"),
            ("templates/.env.example", "HOST=https://tpl\n"),
            ("templates/Custom/prompts/translate.txt", "промпт из чужой сборки\n"),
            ("projects/ACTIVE/book/chapter.txt", "новая книга\n"),
            ("python/python.exe", "НОВЫЙ ИНТЕРПРЕТАТОР"),
            (".env", "HOST=https://new\n"),
        ]:
            zf.writestr(f"{top}/{name}", body)
        # Compress-Archive из PowerShell 5.1 пишет разделители «\»
        zf.writestr(f"{top}" + "\\tools\\new_tool.py", "# новый инструмент\n")
    return buf.getvalue()


@pytest.fixture()
def portable_root(tmp_path):
    """Установка портативной сборки: старый код, свой интерпретатор, данные."""
    root = tmp_path / "novelmaestro-portable"
    for rel, body in [
        ("START.txt", "Портативная сборка\n"),
        ("VERSION", "0.4.0"),
        ("requirements.txt", "httpx\n"),
        ("core/common.py", "СТАРЫЙ КОД\n"),
        ("core/local_note.py", "# файл пользователя\n"),
        ("web/static/app.js", "// старый SPA\n"),
        ("web/job_logs/12.log", "старая история запусков\n"),
        ("web/jobs.json", "моя очередь\n"),
        ("templates/Custom/prompts/translate.txt", "мой набор\n"),
        ("templates/.env.example", "HOST=https://old-tpl\n"),
        ("python/python.exe", "СТАРЫЙ ИНТЕРПРЕТАТОР"),
        ("projects/ACTIVE/book/chapters/chapter.txt", "моя книга\n"),
        ("templates/General/prompts/translate.txt", "мой промпт\n"),
        ("llm_profiles.json", "{}"),
        (".env", "HOST=https://old\n"),
    ]:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


@pytest.fixture()
def fake_release(monkeypatch):
    """Релиз отдаёт собранный архив: сеть не трогается."""
    calls = {"asset": [], "download": []}

    def fake_asset_url(tag):
        calls["asset"].append(tag)
        return "https://x/zip"

    def fake_download(url):
        calls["download"].append(url)
        return _portable_zip()

    monkeypatch.setattr(U, "release_asset_url", fake_asset_url)
    monkeypatch.setattr(U, "download_asset", fake_download)
    return calls


def test_apply_zip_replaces_code_only(portable_root, fake_release):
    """Архив кладёт код и встроенные шаблоны поверх: данные целые, код новый."""
    res = U.apply_update(portable_root, "v9.9.0")
    assert res["mode"] == "zip" and res["kind"] == "portable"
    assert res["from"] == "0.4.0" and res["to"] == "9.9.0"

    def read(rel):
        return (portable_root / rel).read_text(encoding="utf-8")
    # код заменился, появился новый файл (в том числе с «\» внутри архива)
    assert read("VERSION") == "9.9.0"
    assert read("core/common.py") == "НОВЫЙ КОД\n"
    assert read("web/static/app.js") == "// новый SPA\n"
    assert read("tools/new_tool.py") == "# новый инструмент\n"
    # данные и окружение сборки не тронуты, удалений нет
    assert read("core/local_note.py") == "# файл пользователя\n"
    assert read("web/job_logs/12.log") == "старая история запусков\n"
    assert read("web/jobs.json") == "моя очередь\n"
    assert read("projects/ACTIVE/book/chapters/chapter.txt") == "моя книга\n"
    # встроенные шаблоны обновились, созданный пользователем набор — нет
    assert read("templates/General/prompts/translate.txt") == "промпт релиза\n"
    assert read("templates/.env.example") == "HOST=https://tpl\n"
    assert read("templates/Custom/prompts/translate.txt") == "мой набор\n"
    assert read("python/python.exe") == "СТАРЫЙ ИНТЕРПРЕТАТОР"
    assert read(".env") == "HOST=https://old\n"
    assert read("llm_profiles.json") == "{}"
    assert res["skipped"] > 0


def test_apply_zip_reports_new_dependencies(portable_root, monkeypatch):
    """Сменились зависимости — не ошибка, но отдельная строка итога."""
    monkeypatch.setattr(U, "release_asset_url", lambda tag: "https://x/zip")
    monkeypatch.setattr(U, "download_asset",
                        lambda url: _portable_zip("httpx\ntqdm\n"))
    res = U.apply_update(portable_root, "v9.9.0")
    assert "requirements.txt" in res["note"]
    assert "pip install -r requirements.txt" in res["note"]

    # архив совпадает с тем, что уже лежит: зависимости не менялись — пусто
    monkeypatch.setattr(U, "download_asset",
                        lambda url: _portable_zip("httpx\ntqdm\n"))
    assert U.apply_update(portable_root, "v9.9.0")["note"] == ""


def test_apply_zip_broken_archive(portable_root, monkeypatch):
    """Битый архив — UpdateError с текстом, а не трейс zipfile."""
    monkeypatch.setattr(U, "release_asset_url", lambda tag: "https://x/zip")
    monkeypatch.setattr(U, "download_asset",
                        lambda url: "это не zip".encode("utf-8"))
    with pytest.raises(U.UpdateError) as exc:
        U.apply_update(portable_root, "v9.9.0")
    assert "поврежд" in str(exc.value)


def test_apply_zip_locked_file_reports_path(tmp_path, monkeypatch):
    """Файл кода нельзя заменить — путь попадает в текст ошибки."""
    root = tmp_path / "copy"
    # каталог вместо файла: os.replace правда не сможет его заменить
    (root / "core" / "common.py").mkdir(parents=True)
    monkeypatch.setattr(U, "release_asset_url", lambda tag: "https://x/zip")
    monkeypatch.setattr(U, "download_asset", lambda url: _portable_zip())
    with pytest.raises(U.UpdateError) as exc:
        U.apply_update(root, "v9.9.0")
    assert "core/common.py" in str(exc.value)
    assert (root / "core" / "common.py").is_dir()


# ══════════════════════════════════════════════════════════════════
# ОТКАЗ ОБЫЧНОЙ УСТАНОВКИ НА ЧУЖИХ ПУТЯХ
# ══════════════════════════════════════════════════════════════════
def test_apply_update_docker_refuses(tmp_path):
    """Контейнер не заменяет собственный образ: отказ с командой хоста."""
    (tmp_path / ".docker").write_text("", encoding="utf-8")
    with pytest.raises(U.UpdateError) as exc:
        U.apply_update(tmp_path, "v9.9.0")
    assert "docker compose pull" in str(exc.value)


def test_apply_update_without_tag(tmp_path):
    """Без версии релиза обновлять нечем — просьба нажать «Проверить»."""
    with pytest.raises(U.UpdateError) as exc:
        U.apply_update(tmp_path, "")
    assert "Проверить" in str(exc.value)


# ══════════════════════════════════════════════════════════════════
# ПРИМЕНЕНИЕ GIT (ОБЫЧНАЯ УСТАНОВКА, DULWICH БЕЗ БИНАРНИКА)
# ══════════════════════════════════════════════════════════════════
@pytest.fixture()
def git_install(tmp_path):
    """Клон репозитория (master) и его «сервер»: клон отстаёт на одну версию."""
    src = tmp_path / "origin"
    (src / "core").mkdir(parents=True)
    (src / "VERSION").write_text("0.4.0", encoding="utf-8")
    (src / "core" / "common.py").write_text("СТАРЫЙ КОД\n", encoding="utf-8")
    (src / "core" / "gone.py").write_text("удаляемый модуль\n", encoding="utf-8")
    (src / ".gitignore").write_text("projects/\n.env\nlogs/\n", encoding="utf-8")
    porcelain.init(str(src))
    porcelain.add(str(src), paths=["."])
    porcelain.commit(str(src), message="release 0.4.0".encode("utf-8"))
    dst = tmp_path / "install"
    porcelain.clone(str(src), str(dst))
    # данные пользователя: они в .gitignore, в клоне они свои
    book = dst / "projects/ACTIVE/book/chapters"
    book.mkdir(parents=True)
    (book / "chapter.txt").write_text("моя книга\n", encoding="utf-8")
    return src, dst


def _publish(src, version: str) -> None:
    """Релиз на «сервере»: версия поднята, модуль добавлен, модуль удалён."""
    (src / "VERSION").write_text(version, encoding="utf-8")
    (src / "core" / "common.py").write_text("НОВЫЙ КОД\n", encoding="utf-8")
    (src / "core" / "fresh.py").write_text("новый модуль\n", encoding="utf-8")
    porcelain.add(str(src), paths=["."])
    porcelain.remove(str(src), paths=["core/gone.py"])
    porcelain.commit(str(src), message=f"release {version}".encode("utf-8"))


def test_apply_git_fast_forward(git_install):
    """Обычная установка: ветка перемотана, код новый, книги целые."""
    src, dst = git_install
    _publish(src, "0.5.0")
    res = U.apply_update(dst, "v0.5.0")
    assert res["mode"] == "git" and res["kind"] == "git"
    assert res["branch"] == "master"
    assert res["from"] == "0.4.0" and res["to"] == "0.5.0"
    assert (dst / "VERSION").read_text(encoding="utf-8") == "0.5.0"
    assert (dst / "core" / "common.py").read_text(encoding="utf-8") == "НОВЫЙ КОД\n"
    assert (dst / "core" / "fresh.py").is_file()
    assert not (dst / "core" / "gone.py").exists()
    # данные пользователя вне дерева — не тронуты
    assert (dst / "projects/ACTIVE/book/chapters/chapter.txt").read_text(
        encoding="utf-8") == "моя книга\n"


def test_apply_git_writes_log(git_install):
    """Ход обновления пишется в лог: способ установки и имя ветки."""
    src, dst = git_install
    _publish(src, "0.5.0")
    lines: list[str] = []
    U.apply_update(dst, "v0.5.0", log=lines.append)
    assert any("из репозитория" in ln for ln in lines)
    assert any("master" in ln for ln in lines)


def test_apply_git_detached_head(git_install):
    """Вне ветки (detached HEAD) перемотку не делаем: только вручную."""
    _src, dst = git_install
    repo = Repo(str(dst))
    sha = repo.refs.read_ref(Ref(b"refs/heads/master")) or b""
    (dst / ".git" / "HEAD").write_text(sha.decode(), encoding="utf-8")
    with pytest.raises(U.UpdateError) as exc:
        U.apply_update(dst, "v0.5.0")
    assert "detached HEAD" in str(exc.value)


def test_apply_git_diverged(git_install):
    """Свои коммиты в ветке расходятся с сервером: отказ, а не слияние."""
    src, dst = git_install
    (dst / "core" / "mine.py").write_text("своя правка\n", encoding="utf-8")
    porcelain.add(str(dst), paths=["."])
    porcelain.commit(str(dst), message="своя правка".encode("utf-8"))
    _publish(src, "0.5.0")
    with pytest.raises(U.UpdateError) as exc:
        U.apply_update(dst, "v0.5.0")
    assert "разошлась" in str(exc.value)


def test_apply_git_dirty_worktree(git_install):
    """Несохранённые правки тех же файлов — отказ: их нечем сливать."""
    src, dst = git_install
    (dst / "core" / "common.py").write_text("правка в рабочем каталоге\n",
                                            encoding="utf-8")
    _publish(src, "0.5.0")
    with pytest.raises(U.UpdateError) as exc:
        U.apply_update(dst, "v0.5.0")
    assert "несохран" in str(exc.value)


def test_apply_git_quiet_log(git_install):
    """Служебный шум dulwich не попадает в лог обновления."""
    src, dst = git_install
    _publish(src, "0.5.0")
    lines: list[str] = []
    U.apply_update(dst, "v0.5.0", log=lines.append)
    assert all("delta" not in ln and "pack data" not in ln for ln in lines)
    assert all(len(ln) < 240 for ln in lines)


# ══════════════════════════════════════════════════════════════════
# ПЕРЕЗАПУСК ПОСЛЕ ОБНОВЛЕНИЯ
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("kind,auto", [
    ("git", True), ("tree", True), ("portable", False), ("docker", False),
], ids=["git", "tree", "portable", "docker"])
def test_restart_supported(kind, auto):
    """Сам себя перезапускает только процесс, который можно поднять заново.

    Портативная сборка живёт в окне с занятым python.exe, контейнер перезапускает
    хост.
    """
    assert U.restart_supported(kind) is auto


def test_restart_command_keeps_entry_and_flags():
    """Перезапуск поднимает тот же входной скрипт теми же флагами сервера."""
    cmd = U.restart_command(["run.py", "--host", "0.0.0.0", "--open", "--чужой-флаг"])
    assert cmd[0] == sys.executable
    assert cmd[1].endswith("/run.py") and Path(cmd[1]).is_absolute()
    assert cmd[2:] == ["--host", "0.0.0.0", "--open"]


def test_restart_command_without_entry():
    """Без точки входа (sys.argv[0] пуст) перезапуска не бывает."""
    assert U.restart_command(["", "--port", "8756"]) == []


@pytest.mark.parametrize("kind,marker,auto,word", [
    ("git", ".git", True, "перезапустится сам"),
    ("tree", None, True, "перезапустится сам"),
    ("portable", "START.txt", False, "start.bat"),
    ("docker", ".docker", False, "docker compose pull"),
], ids=["git", "tree", "portable", "docker"])
def test_restart_plan_per_install(kind, marker, auto, word, tmp_path):
    """План перезапуска: способ установки диктует авто и текст предупреждения."""
    root = tmp_path / kind
    root.mkdir()
    if marker:
        (root / marker).touch()
    entry = root / "main.py"
    entry.write_text("", encoding="utf-8")
    plan = U.restart_plan([str(entry), "--port", "8756"], root=root)
    assert plan["kind"] == kind and plan["auto"] is auto
    assert word in plan["note"]
    if auto:
        assert plan["command"][1] == str(entry)
        assert plan["command"][-1] == "8756"


def test_restart_plan_flag_switches_auto_off(tmp_path):
    """--no-restart (dev-сервер): авто-перезапуск выключен, текст — про руки."""
    (tmp_path / ".git").mkdir()
    entry = tmp_path / "main.py"
    entry.write_text("", encoding="utf-8")
    plan = U.restart_plan([str(entry)], root=tmp_path, allowed=False)
    assert plan["auto"] is False and plan["command"] == []
    assert "--no-restart" in plan["note"]


def test_schedule_restart_spawns_watcher_and_stops_process(tmp_path, monkeypatch):
    """Наблюдатель стартует до нашего выхода и ждёт именно выхода процесса.

    Команда наблюдателя: этот же интерпретатор, код наблюдателя, PID, запасной
    таймер и команда сервера.
    """
    (tmp_path / ".git").mkdir()
    entry = tmp_path / "run.py"
    entry.write_text("", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(U, "_spawn", lambda cmd, cwd: seen.update(
        cmd=cmd, cwd=Path(cwd)))
    monkeypatch.setattr(U, "_terminate", lambda pid: seen.update(term=pid))
    plan = U.schedule_restart([str(entry), "--port", "8756"], root=tmp_path,
                              pid=4242, wait=7.5)
    assert plan["auto"] is True
    assert seen["cwd"] == tmp_path and seen["term"] == os.getpid()
    cmd = seen["cmd"]
    assert cmd[0] == sys.executable and cmd[1] == "-c"
    assert cmd[2] == U._WATCHER_CODE
    assert cmd[3:] == ["4242", "7.5", sys.executable, str(entry),
                       "--port", "8756"]


def test_schedule_restart_manual_installation_does_nothing(tmp_path, monkeypatch):
    """Портативной сборке наблюдатель не нужен: перезапуск делает человек."""
    (tmp_path / "START.txt").touch()
    entry = tmp_path / "run.py"
    entry.write_text("", encoding="utf-8")
    calls = []
    monkeypatch.setattr(U, "_spawn", lambda *a: calls.append("spawn"))
    monkeypatch.setattr(U, "_terminate", lambda pid: calls.append("term"))
    plan = U.schedule_restart([str(entry)], root=tmp_path)
    assert plan["auto"] is False and calls == []


@pytest.mark.skipif(not Path("/proc/self").is_dir(),
                    reason="наблюдатель ждёт процесса через /proc")
def test_watcher_starts_server_only_after_process_exits(tmp_path):
    """Живой наблюдатель: пока сервер жив — ничего не поднимает, вышел — поднял."""
    victim = subprocess.Popen([sys.executable, "-c",
                               "import time; time.sleep(0.5)"])
    marker = tmp_path / "server.up"
    watcher = subprocess.Popen(
        [sys.executable, "-c", U._WATCHER_CODE, str(victim.pid), "10",
         sys.executable, "-c", f"open({str(marker)!r}, 'w').write('up')"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert not marker.exists(), "сервер ещё жив — наблюдатель должен ждать"
        victim.wait(timeout=10)
        watcher.wait(timeout=10)
        for _ in range(60):
            if marker.exists():
                break
            time.sleep(0.05)
        assert marker.read_text(encoding="utf-8") == "up"
    finally:
        watcher.kill()
        victim.kill()


def test_watcher_without_proc_starts_immediately(tmp_path):
    """Где /proc нет (Windows портативной сборки), наблюдатель не ждёт ничего.

    Проверка переносимая: PID заведомо несуществующего процесса — это «уже вышел».
    """
    marker = tmp_path / "server.up"
    code = (U._WATCHER_CODE.replace("/proc/", str(tmp_path) + "/proc/")
            .replace("os.path.isdir", "os.path.exists"))
    proc = subprocess.run([sys.executable, "-c", code, "999999", "0.5",
                           sys.executable, "-c",
                           f"open({str(marker)!r}, 'w').write('up')"],
                          timeout=30, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert marker.exists()

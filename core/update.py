#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update.py — как эта копия NovelMaestro установлена и как она обновляется.

Установки бывают четыре, и у каждой свой путь обновления:
  docker   — образ из реестра ghcr: контейнер не может заменить собственный
             образ, поэтому обновление выполняет хост своей командой;
  portable — портативная Windows-сборка (маркер START.txt): интерпретатор и
             код лежат в одной папке, .git в архиве нет — обновление кладёт
             файлы из zip релиза поверх текущих;
  git      — обычный клон репозитория: git сам знает, где код, а данные книг
             лежат в .gitignore, поэтому обновление их не видит;
  tree     — копия исходников без .git: тот же путь, что у портативки.

Общего у них два: релизы живут на GitHub Releases (тег v*, VERSION = тег) и
пользовательские данные не меняются никогда — список в PROTECTED_PATHS.
После обновления сервер обязан перезапуститься: на диске код уже новый, а
процесс до перезапуска продолжает работать старым.
"""
from __future__ import annotations

import io
import os
import re
import zipfile
from pathlib import Path

from . import transport

__all__ = [
    "UpdateError", "GITHUB_REPO", "RELEASES_URL", "ASSET_PREFIX",
    "KIND_DOCKER", "KIND_PORTABLE", "KIND_GIT", "KIND_TREE", "KIND_LABELS",
    "MODE_COMMAND", "MODE_GIT", "MODE_ZIP", "MODE_LABELS", "HOST_COMMAND",
    "PROTECTED_PATHS", "PROTECTED_DIRS", "install_kind", "update_mode",
    "installed_version", "version_tuple", "update_available", "latest_release",
    "release_notes", "release_asset_url", "download_asset", "is_protected",
    "host_command_hint", "apply_update",
]

#: владелец/репозиторий на GitHub — источник релизов (тег v*, zip портативки)
GITHUB_REPO = "LynxPDA/NovelMaestro"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
#: архив портативной сборки публикует воркфлоу windows.yml
ASSET_PREFIX = "novelmaestro-portable-"
#: обновление смотрят редко и молча: тяжёлые ответы не нужны
CHECK_TIMEOUT = 10.0
#: zip портативки и fetch ветки идут к GitHub напрямую, тут таймаут щедрее
DOWNLOAD_TIMEOUT = 180.0
#: заметки релиза: длинное описание релиза интерфейсу не нужно
MAX_NOTES = 4000

# ══════════════════════════════════════════════════════════════════
# СПОСОБ УСТАНОВКИ
# ══════════════════════════════════════════════════════════════════
KIND_DOCKER = "docker"
KIND_PORTABLE = "portable"
KIND_GIT = "git"
KIND_TREE = "tree"
#: подписи способа установки для интерфейса
KIND_LABELS = {
    KIND_DOCKER: "Установка в Docker",
    KIND_PORTABLE: "Портативная сборка (Windows)",
    KIND_GIT: "Запуск из репозитория",
    KIND_TREE: "Копия исходников",
}
#: docker — команда на хосте, portable/tree — архив, git — git
MODE_COMMAND = "command"
MODE_GIT = "git"
MODE_ZIP = "zip"
MODE_LABELS = {
    MODE_COMMAND: "образ обновляет хост",
    MODE_GIT: "из репозитория (git)",
    MODE_ZIP: "файлами из архива релиза",
}
#: образ контейнер сам не заменит: команду выполняет хост
HOST_COMMAND = "docker compose pull && docker compose up -d"

# ══════════════════════════════════════════════════════════════════
# ДАННЫЕ ПОЛЬЗОВАТЕЛЯ: что не перезаписывается никогда
# ══════════════════════════════════════════════════════════════════
#: пути относительно корня кода (в портовом и tree-режиме)
PROTECTED_PATHS = (
    "projects",          # книги: разделы и данные
    "templates",         # наборы шаблонов пользователя
    "logs",              # логи стадий
    "web/job_logs",      # история запусков
    "llm_profiles.json",  # профили LLM
    ".env",              # общий конфиг
    ".docker",           # маркер образа
    "servers", "Images", "backup", "dist",
)
#: каталоги-маркеры: файл под ними — не код программы ни на одном уровне
#: («python» — встроенный интерпретатор портативной сборки: он занят процессом)
PROTECTED_DIRS = (
    ".git", ".venv", ".tmp", ".pytest_cache", ".ruff_cache", "__pycache__",
    "node_modules", "job_logs", "python",
)

#: «**Full Changelog**: https://github.com/…/compare/v0.3.7...v0.4.0» —
#: автозаметка GitHub: ссылку интерфейс показывает отдельной ссылкой
_DIFF_LINE_RE = re.compile(r"(?im)^.*full changelog.*$\n?")
_COMPARE_RE = re.compile(r"https://\S+/compare/\S+")


class UpdateError(Exception):
    """Причина, по которой обновление невозможно, человеческим текстом."""


def host_command_hint() -> str:
    """Объяснение для Docker: контейнер не заменяет собственный образ."""
    return (f"Это {KIND_LABELS[KIND_DOCKER].lower()}: образ обновляет хост "
            f"командой {HOST_COMMAND}")


def install_kind(root=None) -> str:
    """Способ установки по маркерам в корне кода (порядок: docker первый).

    Корень по умолчанию — репозиторий, из которого работает процесс: у
    пакетов core/ и web/ он общий.
    """
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    if (root / ".docker").exists():
        return KIND_DOCKER
    if (root / "START.txt").exists():
        return KIND_PORTABLE
    if (root / ".git").exists():
        return KIND_GIT
    return KIND_TREE


def update_mode(kind: str) -> str:
    """Чем обновлять эту установку: command | git | zip."""
    return {
        KIND_DOCKER: MODE_COMMAND,
        KIND_PORTABLE: MODE_ZIP,
        KIND_GIT: MODE_GIT,
        KIND_TREE: MODE_ZIP,
    }.get(kind, MODE_ZIP)


def installed_version(root=None) -> str:
    """Версия установленной копии из файла VERSION ("" — файла нет)."""
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ══════════════════════════════════════════════════════════════════
# РЕЛИЗ: проверка и скачивание
# ══════════════════════════════════════════════════════════════════
def version_tuple(version: str) -> tuple:
    """Версия в сравнимый кортеж чисел: «0.3.10» > «0.3.9», мусор — нули.

    Простая схема сравнения без сторонних библиотек: достаточно для
    семантики x.y.z, которую ведёт VERSION/теги релизов.
    """
    out = []
    for part in str(version or "").strip().split("."):
        digits = ""
        for ch in part.strip():
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits) if digits else 0)
    return tuple(out or [0])


def update_available(current: str, release: dict | None) -> bool:
    """Есть ли релиз новее текущей версии (сравнение version_tuple).
    release без ok/version (не проверялся, сеть лежала) — обновления нет."""
    if not release or not release.get("ok") or not release.get("version"):
        return False
    return version_tuple(release["version"]) > version_tuple(current)


def release_notes(body: str) -> tuple:
    """Заметки релиза и ссылка на сравнение: (notes, changes).

    Строку «Full Changelog: <ссылка>» автозаметки GitHub вырезает из заметок:
    это не описание, а ссылка — интерфейс отдаёт её отдельной ссылкой, иначе
    в панели она выглядела бы текстом, который не нажать.
    """
    text = str(body or "").strip()
    found = _COMPARE_RE.search(text)
    changes = found.group(0).rstrip(").,;") if found else ""
    notes = _DIFF_LINE_RE.sub("", text).strip()
    return notes[:MAX_NOTES], changes


def latest_release() -> dict:
    """Последний релиз GitHub: {tag, version, name, url, published, notes, changes}.

    Сетевые ошибки НЕ бросаются — отчёт {ok: False, error} (проверка не
    должна ронять страницу и сессию). tag без ведущего «v»; notes —
    описание релиза (markdown, ссылки интерфейс красит сам).
    """
    try:
        data = transport.open_json_get(RELEASES_URL, timeout=CHECK_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — отчёт, не падение
        return {"ok": False, "error": str(exc)}
    tag = str(data.get("tag_name") or "").strip()
    notes, changes = release_notes(str(data.get("body") or ""))
    return {
        "ok": True,
        "tag": tag,
        "version": tag[1:] if tag.startswith("v") else tag,
        "name": str(data.get("name") or "").strip(),
        "url": str(data.get("html_url") or ""),
        "published": str(data.get("published_at") or ""),
        "notes": notes,
        "changes": changes,
    }


def release_asset_url(tag: str) -> str:
    """Ссылка на zip портативной сборки релиза (имя ассета публикует windows.yml).

    Зеркало — прямая ссылка «Downloads» страницы релиза. Без ключа:
    репозиторий публичный.
    """
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{tag}"
    data = transport.open_json_get(url, timeout=CHECK_TIMEOUT)
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.startswith(ASSET_PREFIX) and name.endswith(".zip"):
            link = str(asset.get("browser_download_url") or "")
            if link:
                return link
    raise UpdateError(f"В релизе {tag} нет архива портативной сборки")


def download_asset(url: str) -> bytes:
    """Бинарное скачивание: GitHub отдаёт ассет редиректом на свой CDN.

    Тело читается `iter_bytes()` (сырое: переводы строк zip не трогаются),
    таймаут щедрее проверки, но конечный.
    """
    status, body = 0, b""
    with transport.open_get(url, timeout=DOWNLOAD_TIMEOUT,
                            follow_redirects=True) as resp:
        status = resp.status_code
        body = b"".join(resp.iter_bytes())
    if status != 200:
        raise UpdateError(f"GitHub отдал HTTP {status} на скачивание")
    return body


# ══════════════════════════════════════════════════════════════════
# ПРИМЕНЕНИЕ ОБНОВЛЕНИЯ
# ══════════════════════════════════════════════════════════════════
def is_protected(rel: str) -> bool:
    """True — файл относится к данным пользователя или к окружению сборки.

    Подозрительные пути архива («..», диск) считаются защищёнными: их не
    пишем ни при каком режиме.
    """
    parts = [p for p in str(rel).replace("\\", "/").split("/")
             if p not in ("", ".")]
    if not parts or ".." in parts or any(p.endswith(":") for p in parts):
        return True
    for i, part in enumerate(parts):
        if part in PROTECTED_DIRS:
            return True
        if "/".join(parts[: i + 1]) in PROTECTED_PATHS:
            return True
    return False


def _emit(log, line: str) -> None:
    """Строка лога обновления (вызывается необязательным колбэком)."""
    if log:
        try:
            log(str(line).strip())
        except Exception:  # noqa: BLE001 — лог не должен ронять обновление
            pass


def _tail(text: str, limit: int = 300) -> str:
    """Хвост вывода команды — в него обычно и упирается причина."""
    text = str(text or "").strip()
    return text[-limit:]


def _write_code_file(target: Path, data: bytes, rel: str) -> None:
    """Файл кода: временный файл рядом и os.replace — полубитый не остаётся."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / (target.name + ".nm-new")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    except OSError as exc:
        raise UpdateError(f"Не удалось заменить {rel}: {exc}") from exc


def _entry_relpath(name: str, top: str) -> str:
    """Путь файла архива относительно корня установки ("" — мимо установки)."""
    rel = str(name).replace("\\", "/").lstrip("/")
    if top and rel.startswith(top):
        rel = rel[len(top):].lstrip("/")
    return rel


def _archive_top(names: list) -> str:
    """Верхний каталог архива («novelmaestro-portable/»), если он один."""
    if not names or any("/" not in n for n in names):
        return ""
    tops = {n.split("/", 1)[0] for n in names}
    return tops.pop() + "/" if len(tops) == 1 else ""


def _dulwich():
    """dulwich (обязательная роль core/deps.py) тремя модулями: porcelain, repo, errors.

    Импорт внутри функции: проверка релиза и архивный режим обновления
    работают и без него.
    """
    try:
        from dulwich import errors, porcelain, repo
    except ImportError as exc:
        raise UpdateError(
            "Для обновления из репозитория нужен dulwich: "
            "python3 -m pip install dulwich") from exc
    return porcelain, repo, errors


def _err_text(exc, noise: io.BytesIO) -> str:
    """Текст ошибки dulwich плюс хвост его собственного вывода."""
    extra = noise.getvalue().decode("utf-8", "replace").strip()
    return " ".join(p for p in (str(exc).strip(), extra[-200:].strip()) if p)


def _apply_git(root: Path, tag: str, log=None) -> dict:
    """Обычная установка: перемотка своей ветки через dulwich (без git-бинарника).

    Тот же git-механизм, что у вкладки «История» (web/history.py): dulwich —
    обязательная роль core/deps.py, а бинарника git в Docker и в портативной
    сборке нет. Порядок один: fetch своей ветки, только быстрая перемотка,
    рабочее дерево и индекс приводит к новому коммиту сам dulwich.

    Отказ, а не «разберёмся сами», когда ветка разошлась или правки в рабочем
    каталоге не сохранены: это ровно то «повредить данные», которого
    обновление не терпит. Данные книг, шаблоны и .env в .gitignore — их pull
    не видит вовсе.
    """
    porcelain, repo_mod, errors = _dulwich()
    if not (root / ".git").is_dir():
        raise UpdateError("В этой копии нет каталога .git: обновление "
                          "из репозитория недоступно")
    try:
        repo = repo_mod.Repo(str(root))
    except errors.NotGitRepository as exc:
        raise UpdateError("Каталог .git не читается как репозиторий: "
                          "обновите копию вручную") from exc
    with repo:
        head = repo.refs.read_ref(b"HEAD") or b""
        if not head.startswith(b"ref:"):
            raise UpdateError("Репозиторий не на ветке (detached HEAD): "
                              "обновите его вручную")
        ref = head[4:].strip()
        branch = ref.rsplit(b"/", 1)[-1].decode("utf-8", "replace")
        _emit(log, f"Ветка {branch}: перемотка на релиз {tag} (только ff-only)")
        noise = io.BytesIO()
        try:
            porcelain.pull(repo, refspecs=ref, ff_only=True, outstream=noise,
                           errstream=noise)
        except porcelain.DivergedBranches as exc:
            raise UpdateError(
                f"Ветка {branch} разошлась с сервером: сохраните свои коммиты "
                "и обновите копию вручную") from exc
        except errors.WorkingTreeModifiedError as exc:
            raise UpdateError(
                "В рабочем каталоге есть несохранённые правки тех же файлов: "
                "сохраните или отмените их и повторите") from exc
        except Exception as exc:  # noqa: BLE001 — сеть и битые ссылки отчётом
            raise UpdateError(
                f"Перемотка не удалась: {_tail(_err_text(exc, noise))}") from exc
        _emit(log, f"Ветка {branch}: {installed_version(root)}")
    return {"branch": branch}


def _apply_zip(root: Path, tag: str, log=None) -> dict:
    """Портативная сборка и копия исходников: файлы архива релиза поверх.

    Ничего не удаляется: файлы только перезаписываются, а данные из
    PROTECTED_PATHS пропускаются. Встроенный интерпретатор портативной
    сборки (python/) занят процессом и в список кода не входит вовсе.
    """
    link = release_asset_url(tag)
    _emit(log, f"Скачиваю архив релиза {tag}")
    data = download_asset(link)
    _emit(log, f"Архив: {len(data) // 1024} КиБ")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UpdateError(f"Архив релиза {tag} повреждён") from exc
    with zf:
        items = [(n, _entry_relpath(n, "")) for n in zf.namelist()
                 if not n.replace("\\", "/").endswith("/")]
        top = _archive_top([rel for _, rel in items])
        done = skipped = 0
        for name, rel in items:
            rel = _entry_relpath(name, top)
            if not rel:
                continue
            if is_protected(rel):
                skipped += 1
                continue
            _write_code_file(root / rel, zf.read(name), rel)
            done += 1
    _emit(log, f"Заменено файлов: {done}, данных не тронуто: {skipped}")
    return {"files": done, "skipped": skipped}


def _requirements(root: Path) -> str:
    """Содержимое requirements.txt ("" — файла нет): сверка до и после."""
    return (root / "requirements.txt").read_text(
        encoding="utf-8", errors="replace")


def apply_update(root, tag: str, kind: str | None = None,
                 log=None) -> dict:
    """Обновить программу на месте: {kind, mode, from, to, branch|files, note}.

    Режим выбирается по способу установки, своих переключателей у него нет.
    Docker — отказ с командой хоста: контейнер не заменяет собственный образ.
    Смена requirements.txt — не ошибка, но без установки пакетов новая версия
    может не подняться, поэтому она уходит отдельной строкой `note`.
    """
    root = Path(root).resolve()
    kind = kind or install_kind(root)
    mode = update_mode(kind)
    if mode == MODE_COMMAND:
        raise UpdateError(host_command_hint())
    if not tag:
        raise UpdateError("Не указана версия релиза — нажмите «Проверить»")
    had_reqs = (root / "requirements.txt").is_file()
    req_before = _requirements(root) if had_reqs else ""
    before = installed_version(root)
    _emit(log, f"Обновление {before or '?'} → {tag}: {MODE_LABELS[mode]}")
    if mode == MODE_GIT:
        res = _apply_git(root, tag, log)
    else:
        res = _apply_zip(root, tag, log)
    note = ""
    now_reqs = (root / "requirements.txt").is_file()
    if had_reqs != now_reqs or (now_reqs and _requirements(root) != req_before):
        pip = ("python\\python.exe -m pip install -r requirements.txt"
               if kind == KIND_PORTABLE
               else "python3 -m pip install -r requirements.txt")
        note = f"Изменились зависимости — обновите их: {pip}"
    res.update({"kind": kind, "mode": mode, "from": before,
                "to": installed_version(root), "note": note})
    return res

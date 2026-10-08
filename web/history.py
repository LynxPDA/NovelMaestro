#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
history.py — локальная история проекта: контрольные точки на git (dulwich).

Репозиторий — стандартный .git в корне проекта (проекты не в git репозитория
NovelMaestro, вложенный репозиторий ни на что не влияет). Формат — настоящий
git: объекты, коммиты, деревья; историю можно открыть любым git-клиентом.
Бинарник git не нужен: dulwich — чистый Python (работает в Docker и portable).

Модель (как «история версий» в Confluence):
- точка = коммит; сообщение: «<метка> · <дата>» + строка `Kind: <вид>`;
- виды: manual (вручную), run (запуск прошёл без ошибок), apply (применение
  правок проверки), restore (возврат к точке);
- восстановление НЕ перемещает HEAD: файлы приводятся к состоянию старой
  точки и фиксируются новой точкой «Возврат к …» — линейная история,
  промежуточные точки не теряются;
- точка без изменений не создаётся (одинаковое дерево с HEAD);
- из точек исключены рабочие файлы: tmp/, logs/, backup/, job_logs/, *.bak
  (.git/info/exclude — без .gitignore в корне проекта).

Все операции — под общим локом: сервер однопользовательский, но два
запуска могут завершиться одновременно.
"""
from __future__ import annotations

import difflib
import logging
import re
import threading
import time
from pathlib import Path

from dulwich import porcelain
from dulwich.objects import Blob, Commit, Tree
from dulwich.repo import Repo

log = logging.getLogger("web.history")

#: репозиторий истории — стандартный каталог git в корне проекта
HISTORY_DIRNAME = ".git"
#: автор коммитов (конфиг user.name не нужен)
_AUTHOR = b"NovelMaestro <history@localhost>"
#: что не попадает в точки: рабочие файлы и мусор стадий
_EXCLUDE = "tmp/\nlogs/\nbackup/\njob_logs/\n*.bak\n"
_KINDS = ("manual", "run", "apply", "restore")
_KIND_RE = re.compile(r"^Kind: (\w+)$", re.M)
_LABEL_RE = re.compile(r"\A(.+?) · \d{4}-\d\d-\d\d \d\d:\d\d\Z", re.S)

# сервер один: два одновременных завершения запусков не должны спорить
# за index.lock
_lock = threading.Lock()


class HistoryError(Exception):
    """Ошибка операции истории (не найдена точка, битый репозиторий…)."""


def _as_commit(obj) -> Commit:
    """Объект хранилища сужается до коммита (dulwich типизирует всё как ShaFile)."""
    if not isinstance(obj, Commit):
        raise HistoryError("Объект истории не является точкой")
    return obj


def _as_tree(obj) -> Tree:
    if not isinstance(obj, Tree):
        raise HistoryError("Объект истории не является деревом")
    return obj


def _as_blob(obj) -> Blob:
    if not isinstance(obj, Blob):
        raise HistoryError("Объект истории не является файлом")
    return obj


def _head_sha(repo: Repo) -> bytes | None:
    """sha HEAD или None (история пуста); у dulwich отсутствие HEAD — KeyError."""
    try:
        sha = repo.head()
    except KeyError:
        return None
    return sha if isinstance(sha, bytes) else None


def _repo(project: Path) -> Repo:
    """Открытый Repo проекта; нет истории — HistoryError."""
    if not (Path(project) / HISTORY_DIRNAME).is_dir():
        raise HistoryError("История ещё не ведётся для этого проекта")
    return Repo(str(project))


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def _message(label: str, kind: str, meta: str = "") -> bytes:
    """Сообщение коммита: «метка · дата» + вид (+ доп. строки)."""
    lines = [f"{label} · {_ts()}", "", f"Kind: {kind}"]
    if meta:
        lines.append(str(meta).strip())
    return "\n".join(lines).encode()


def parse_message(commit) -> tuple[str, str]:
    """(метка, вид) из сообщения коммита; незнакомый вид — manual."""
    text = commit.message.decode("utf-8", "replace")
    m = _KIND_RE.search(text)
    kind = m.group(1) if m and m.group(1) in _KINDS else "manual"
    first = text.splitlines()[0] if text else ""
    lm = _LABEL_RE.match(first)
    return (lm.group(1) if lm else first, kind)


def ensure_repo(project: Path) -> Repo:
    """Инициализирует историю проекта (если ещё нет) и возвращает Repo."""
    project = Path(project)
    if not (project / HISTORY_DIRNAME).is_dir():
        porcelain.init(str(project))
        exclude = project / HISTORY_DIRNAME / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(_EXCLUDE, encoding="utf-8")
    return Repo(str(project))


def _stage_all(project: Path) -> None:
    """Индекс = текущее состояние работы: добавление/правка через add,
    удаление — путями индекса, которых нет на диске."""
    project = Path(project)
    repo = _repo(project)
    idx = repo.open_index()
    for path in list(idx):
        if not (project / path.decode("utf-8", "replace")).exists():
            del idx[path]
    idx.write()
    porcelain.add(str(project))


def _commit(project: Path, label: str, kind: str, meta: str = "") -> str:
    sha = porcelain.commit(str(project), _message(label, kind, meta),
                           author=_AUTHOR, committer=_AUTHOR)
    log.info("История %s: точка «%s» (%s)",
             Path(project).name, label, sha.decode()[:8])
    return sha.decode()


def create(project: Path, label: str, kind: str = "manual",
           meta: str = "") -> dict:
    """Создаёт точку; возвращает {created, sha?, changed}.

    Точка без изменений не создаётся (дерево равно HEAD; на пустом
    проекте — пустое дерево). label — человекочитаемая метка («что и
    когда» дату дописывает _message).
    """
    project = Path(project)
    if kind not in _KINDS:
        raise HistoryError(f"Неизвестный вид точки: {kind}")
    label = " ".join(str(label).split())
    if not label:
        raise HistoryError("Метка точки обязательна")
    with _lock:
        repo = ensure_repo(project)
        _stage_all(project)
        tree = repo.open_index().commit(repo.object_store)
        head = _head_sha(repo)
        if head is not None:
            changed = tree != _as_commit(repo[head]).tree
        else:
            changed = bool(len(repo.open_index()))
        if not changed:
            return {"created": False, "changed": False}
        sha = _commit(project, label, kind, meta)
        return {"created": True, "changed": True, "sha": sha}


def _flat_tree(repo: Repo, tree_id: bytes) -> dict[bytes, bytes]:
    """Дерево → плоский словарь {путь: sha блоба} (файлы, рекурсивно)."""
    out: dict[bytes, bytes] = {}

    def walk(tree_id: bytes, prefix: bytes) -> None:
        for entry in _as_tree(repo[tree_id]).items():
            name = prefix + entry.path
            obj = repo[entry.sha]
            if isinstance(obj, Tree):
                walk(entry.sha, name + b"/")
            else:
                out[name] = entry.sha

    walk(tree_id, b"")
    return out


def _commit_or_die(repo: Repo, sha: str) -> Commit:
    try:
        return _as_commit(repo[bytes.fromhex(sha)])
    except (ValueError, KeyError) as exc:
        raise HistoryError(f"Точка не найдена: {sha}") from exc


def checkpoints(project: Path, limit: int = 100, offset: int = 0) -> dict:
    """Список точек от свежей к старой (обход first-parent от HEAD).

    {total, checkpoints: [{sha, time, label, kind}]} — time в unix-секундах.
    """
    with _lock:
        repo = _repo(project)
        items = []
        head = _head_sha(repo)
        while head is not None:
            c = _as_commit(repo[head])
            label, kind = parse_message(c)
            items.append({"sha": head.decode(), "time": c.author_time,
                          "label": label, "kind": kind})
            head = c.parents[0] if c.parents else None
        return {"total": len(items),
                "checkpoints": items[offset:offset + max(0, limit)]}


def diff(project: Path, sha_from: str, sha_to: str) -> dict:
    """Изменения файлов между точками: [{path, status(A/M/D), adds, dels}].

    adds/dels — построчная разница блобов (difflib, без контекста).
    """
    with _lock:
        repo = _repo(project)
        c_from = _commit_or_die(repo, sha_from)
        c_to = _commit_or_die(repo, sha_to)
        f_from = _flat_tree(repo, c_from.tree)
        f_to = _flat_tree(repo, c_to.tree)
        files = []
        for path in sorted(set(f_from) | set(f_to)):
            name = path.decode("utf-8", "replace")
            if path not in f_to:
                adds, dels = 0, _line_delta(repo, f_from[path], b"")[1]
                files.append({"path": name, "status": "D",
                              "adds": adds, "dels": dels})
            elif path not in f_from:
                adds, dels = _line_delta(repo, b"", f_to[path])
                files.append({"path": name, "status": "A",
                              "adds": adds, "dels": dels})
            elif f_from[path] != f_to[path]:
                adds, dels = _line_delta(repo, f_from[path], f_to[path])
                files.append({"path": name, "status": "M",
                              "adds": adds, "dels": dels})
        return {"files": files}


def _line_delta(repo: Repo, sha_old: bytes, sha_new: bytes) -> tuple[int, int]:
    """(+добавлено, −удалено) между блобами; строки — UTF-8 с заменой."""
    old = _as_blob(repo[sha_old]).data.decode("utf-8", "replace").splitlines() \
        if sha_old else []
    new = _as_blob(repo[sha_new]).data.decode("utf-8", "replace").splitlines() \
        if sha_new else []
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    adds = dels = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            dels += i2 - i1
        if tag in ("replace", "insert"):
            adds += j2 - j1
    return adds, dels


def patch(project: Path, sha_from: str, sha_to: str, path: str) -> dict:
    """Unified-дифф одного файла между точками (для разворачивания в UI)."""
    with _lock:
        repo = _repo(project)
        c_from = _commit_or_die(repo, sha_from)
        c_to = _commit_or_die(repo, sha_to)
        f_from = _flat_tree(repo, c_from.tree)
        f_to = _flat_tree(repo, c_to.tree)
        key = path.encode("utf-8")
        old = _as_blob(repo[f_from[key]]).data.decode("utf-8", "replace") \
            if key in f_from else ""
        new = _as_blob(repo[f_to[key]]).data.decode("utf-8", "replace") \
            if key in f_to else ""
        if old == new:
            raise HistoryError(f"В файле {path} между точками нет изменений")
        diff_lines = list(difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile=f"a/{path}", tofile=f"b/{path}",
            lineterm="", n=2))
        adds = sum(1 for l in diff_lines
                   if l.startswith("+") and not l.startswith("+++"))
        dels = sum(1 for l in diff_lines
                   if l.startswith("-") and not l.startswith("---"))
        return {"path": path, "adds": adds, "dels": dels,
                "patch": "\n".join(diff_lines)}


def file_content(project: Path, sha: str, path: str) -> bytes:
    """Содержимое файла в точке (для просмотра старой версии)."""
    with _lock:
        repo = _repo(project)
        c = _commit_or_die(repo, sha)
        flat = _flat_tree(repo, c.tree)
        key = path.encode("utf-8")
        if key not in flat:
            raise HistoryError(f"В точке нет файла: {path}")
        return _as_blob(repo[flat[key]]).data


def restore(project: Path, sha: str) -> dict:
    """Вернуть файлы проекта к состоянию точки (Confluence-семантика).

    HEAD не перемещается: файлы приводятся к дереву точки (лишние —
    удаляются), затем создаётся точка «Возврат к …» (kind=restore).
    Промежуточные точки остаются в истории. Незафиксированные файлы,
    которых нет в точке, не трогаются (как git reset --hard).
    """
    project = Path(project)
    with _lock:
        repo = _repo(project)
        target = _commit_or_die(repo, sha)
        t_files = _flat_tree(repo, target.tree)
        head = _head_sha(repo)
        h_files = (_flat_tree(repo, _as_commit(repo[head]).tree)
                   if head is not None else {})
        changed = False
        for path, blob_sha in t_files.items():
            fp = project / path.decode("utf-8", "replace")
            data = _as_blob(repo[blob_sha]).data
            if fp.is_file() and fp.read_bytes() == data:
                continue
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(data)
            changed = True
        for path in h_files:
            if path in t_files:
                continue
            fp = project / path.decode("utf-8", "replace")
            if fp.is_file():
                fp.unlink()
                changed = True
                # пустые каталоги (без файлов) подбираем тихо
                parent = fp.parent
                while parent != project:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
        if not changed and head is not None \
                and _as_commit(repo[head]).tree == target.tree:
            return {"created": False, "sha": sha}
    label_src, _kind = parse_message(
        _commit_or_die(Repo(str(project)), sha))
    res = create(project, f"Возврат к «{label_src}»", "restore", meta=sha)
    return {"created": res.get("created", False),
            "sha": res.get("sha") or sha}


# ══════════════════════════════════════════════════════════════════
# автоматические точки (события web-сервера)
# ══════════════════════════════════════════════════════════════════

def _enabled(key: str) -> bool:
    from core import settings as core_settings
    return bool(core_settings.effective(key))


def on_job_finished(job) -> None:
    """Точка по завершении запуска (вызывается JobManager'ом).

    - применение правок проверки (ner_check/translate_check_llm с --apply,
      не dry-run) — «Применение правок …» при HISTORY_ON_APPLY (дефолт вкл);
    - любой успешный запуск — «Запуск: <стадия>» при HISTORY_ON_RUN.
    Ошибки глотаются (лог): история не должна ломать отчёт о запуске.
    """
    try:
        if getattr(job, "status", "") != "done":
            return
        action = str(getattr(job, "action", "") or "")
        argv = [str(a) for a in (getattr(job, "argv", None) or [])]
        is_apply = (action in ("ner_check", "translate_check_llm")
                    and "--apply" in argv and "--dry-run" not in argv)
        if is_apply:
            if not _enabled("HISTORY_ON_APPLY"):
                return
            label = ("Применение правок глоссария" if action == "ner_check"
                     else "Применение правок перевода")
        else:
            if not _enabled("HISTORY_ON_RUN"):
                return
            from core.settings import STAGE_TITLES
            label = f"Запуск: {STAGE_TITLES.get(action, action)}"
        cwd = getattr(job, "cwd", None)
        if cwd is None:
            return
        create(Path(cwd), label, "apply" if is_apply else "run",
               meta=f"Job: {getattr(job, 'id', '')}")
    except Exception as exc:  # история не ломает жизненный цикл запуска
        log.warning("Автоточка не создана: %s", exc)

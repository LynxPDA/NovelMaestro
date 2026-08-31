#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/projects.py — менеджмент проектов (разделы ACTIVE/HOLD/DONE/DONE_OPEN).

Общий слой для всех бэкэндов (backends/cli, backends/web):
списки, статистика, создание и перенос проектов между разделами.
Чистые функции без интерактива — подтверждения и ввод остаются в UI-слое.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
import threading
import unicodedata
from pathlib import Path

# Дефолтные разделы пульта (бутстрап новой установки; дальше список —
# в projects/.sections.json, порядок важен — это порядок отображения)
DEFAULT_SECTIONS = ["ACTIVE", "HOLD", "DONE"]
# Исторический алиас (доки/тесты)
SECTIONS = DEFAULT_SECTIONS

# Файл персиста списка разделов (в корне projects/, рядом с hub_state)
SECTIONS_FILE = ".sections.json"

# Мьютекс на read-modify-write списка разделов: web-сервер многопоточный
# (ThreadingHTTPServer), параллельные запросы к /api/sections без мьютекса
# пишут в общий .sections.tmp и теряют/дублируют записи. RLock — load_sections
# вызывается и изнутри мьютексных секций (create/rename/delete_section).
_SECTIONS_LOCK = threading.RLock()

# Подпапки каркаса нового проекта
PROJECT_SKELETON = ("source", "chapters", "prompts", "logs", "tmp")

# Имя проекта: NFC, без путей и спецсимволов; разрешены буквы/цифры/._- и пробел
_BAD_NAME_RE = re.compile(r'[/\\:\*\?"<>\|\x00-\x1f]')


def valid_project_name(name: str) -> bool:
    """Имя проекта допустимо: непустое, NFC, без путей/спецсимволов."""
    name = unicodedata.normalize("NFC", (name or "").strip())
    if not name or name in (".", ".."):
        return False
    if len(name) > 120:
        return False
    return not _BAD_NAME_RE.search(name)


def sanitize_project_name(name: str) -> str:
    """Английское имя проекта из произвольного ввода.

    NFC → пробелы в '_' → остаются только латиница/цифры/._- →
    сжатие повторов '_' → без ведущих/хвостовых '._-'.
    Пустой/недопустимый результат — пустая строка (UI спрашивает заново).
    """
    name = unicodedata.normalize("NFC", name or "").strip()
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^A-Za-z0-9._-]+", "", name)
    name = re.sub(r"_{2,}", "_", name)
    name = name.strip("._-")
    if name in (".", ".."):
        return ""
    return name


def _sections_file(projects_root: Path) -> Path:
    """Путь к файлу персиста списка разделов."""
    return Path(projects_root) / SECTIONS_FILE


def load_sections(projects_root: Path) -> list:
    """Список разделов пульта: дефолты + кастомные из .sections.json.

    Файла нет → дефолты + легаси-папки на диске (миграция, напр. DONE_OPEN
    со старых установок). Папка на диске, которой нет в списке, тоже
    добавляется (ручные папки). Дубли в файле схлопываются (след гонки
    параллельных create_section); запись файла без папки на диске —
    призрак и игнорируется (создание раздела всегда создаёт папку;
    папка исчезает только при удалении раздела). Никогда не бросает.
    """
    with _SECTIONS_LOCK:
        return _load_sections_unlocked(projects_root)


def _load_sections_unlocked(projects_root: Path) -> list:
    """Чтение списка разделов без захвата мьютекса (внутри секций)."""
    root = Path(projects_root)
    sections: list[str] = []
    had_file = False
    f = _sections_file(root)
    if f.is_file():
        had_file = True
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, list):
                seen: set[str] = set()
                for s in data:
                    if (isinstance(s, str) and valid_project_name(s)
                            and s not in seen):
                        seen.add(s)
                        sections.append(s)
        except (OSError, ValueError):
            pass
    if not sections:
        sections = list(DEFAULT_SECTIONS)
    if root.is_dir():
        on_disk = [d.name for d in sorted(root.iterdir())
                   if d.is_dir() and valid_project_name(d.name)]
        # реальные папки без записи — до-обнаруживаем (ручные/легаси)
        from_file = set(sections)
        # записи файла без папки на диске — призраки (гонка/ручное удаление):
        # создание раздела всегда создаёт папку; без файла дефолты — контракт
        # (бутстрап), их отсутствие на диске не мешает create_project
        if had_file:
            sections = [s for s in sections if s in set(on_disk)]
        sections.extend(d for d in on_disk if d not in from_file)
    return sections


def save_sections(projects_root: Path, sections: list) -> bool:
    """Атомарно записать список разделов в .sections.json (tmp+replace).

    False — не удалось записать (папка/раздел при этом уже созданы/удалены;
    load_sections до-обнаружит папки на диске)."""
    root = Path(projects_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        f = _sections_file(root)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(sections), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(f)
    except OSError:
        return False
    return True


def create_section(projects_root: Path, name: str):
    """Создать раздел (папка + запись в .sections.json).

    Возвращает (ok, message_or_path): при успехе второй элемент — имя,
    при ошибке — строка с причиной (текст для UI).
    """
    root = Path(projects_root)
    name = unicodedata.normalize("NFC", (name or "").strip())
    if not valid_project_name(name):
        return False, ("Недопустимое имя раздела (пустое, слишком длинное "
                       "или содержит /\\:*?\"<>|).")
    with _SECTIONS_LOCK:
        sections = _load_sections_unlocked(root)
        if name in sections:
            return False, f"Раздел уже существует: {name}."
        try:
            (root / name).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, f"Не удалось создать раздел: {e}"
        sections.append(name)
        save_sections(root, sections)
        return True, name


def rename_section(projects_root: Path, src: str, dst: str):
    """Переименовать раздел (все, включая дефолтные).

    dst уже существует → merge: проекты переносятся в dst, src удаляется.
    Возвращает (ok, message_or_path): при успехе — имя dst, при ошибке —
    строка с причиной.
    """
    root = Path(projects_root)
    src = unicodedata.normalize("NFC", (src or "").strip())
    dst = unicodedata.normalize("NFC", (dst or "").strip())
    if not valid_project_name(dst):
        return False, "Недопустимое новое имя раздела."
    if src == dst:
        return False, "Имя раздела не изменилось."
    # мьютекс от проверок до записи файла: вариант «одинаковые операции
    # в двух тредах» детерминирован и не плодит дубли/потери записей
    with _SECTIONS_LOCK:
        sections = _load_sections_unlocked(root)
        if src not in sections:
            return False, f"Раздел не найден: {src!r}."
        src_dir = root / src
        if not src_dir.is_dir():
            return False, f"Папка раздела не найдена: {src}."
        merge = dst in sections
        if merge:
            dst_dir = root / dst
            try:
                dst_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                return False, f"Не удалось создать раздел: {e}"
            for p in sorted(src_dir.iterdir()):
                if not p.is_dir():
                    continue
                if (dst_dir / p.name).exists():
                    return False, (f"В разделе {dst} уже есть проект "
                                   f"{p.name!r} — перенос невозможен.")
            try:
                for p in sorted(src_dir.iterdir()):
                    if not p.is_dir():
                        continue
                    shutil.move(str(p), str(dst_dir / p.name))
                shutil.rmtree(src_dir, ignore_errors=True)
            except OSError as e:
                return False, f"Не удалось перенести проекты: {e}"
        else:
            dst_dir = root / dst
            if dst_dir.exists():
                return False, f"Раздел {dst} уже существует."
            try:
                shutil.move(str(src_dir), str(dst_dir))
            except OSError as e:
                return False, f"Не удалось переименовать раздел: {e}"
        if merge:
            # dst уже в списке — просто убираем src (позиция dst сохраняется)
            sections = [s for s in sections if s != src]
        else:
            # переименование на месте: позиция сохраняется
            sections = [dst if s == src else s for s in sections]
        save_sections(root, sections)
    return True, dst


def delete_section(projects_root: Path, name: str):
    """Удалить ПУСТОЙ раздел; непустой — отказ (перенос/удаление проектов).

    Возвращает (ok, message_or_path): при успехе — имя, при ошибке —
    строка с причиной.
    """
    root = Path(projects_root)
    name = unicodedata.normalize("NFC", (name or "").strip())
    with _SECTIONS_LOCK:
        sections = _load_sections_unlocked(root)
        if name not in sections:
            return False, f"Раздел не найден: {name!r}."
        d = root / name
        if d.is_dir() and any(d.iterdir()):
            return False, "Раздел не пуст — сначала перенесите или удалите проекты."
        try:
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
        save_sections(root, [s for s in sections if s != name])
    return True, name


def ensure_projects_root(projects_root: Path) -> list:
    """Каркас projects/: папка и дефолтные разделы (идемпотентно).

    Первый запуск: создаёт дефолтные разделы и пишет .sections.json
    (дефолты + легаси-папки на диске). Возвращает список только что
    созданных разделов (пустой, если всё было).
    """
    projects_root = Path(projects_root)
    projects_root.mkdir(parents=True, exist_ok=True)
    created = []
    for sec in DEFAULT_SECTIONS:
        d = projects_root / sec
        if not d.is_dir():
            d.mkdir(parents=True, exist_ok=True)
            created.append(sec)
    with _SECTIONS_LOCK:
        if not _sections_file(projects_root).is_file():
            save_sections(projects_root, _load_sections_unlocked(projects_root))
    return created


def list_template_sets(templates_dir: Path) -> list:
    """Типы книг в templates/: подпапки, содержащие prompts/.

    Возвращает имена, отсортированные; 'General' всегда первым, если есть.
    """
    templates_dir = Path(templates_dir)
    if not templates_dir.is_dir():
        return []
    names = sorted(p.name for p in templates_dir.iterdir()
                   if p.is_dir() and (p / "prompts").is_dir())
    if "General" in names:
        names.remove("General")
        names.insert(0, "General")
    return names


def render_metadata(template_text: str, title: str = "", author: str = "",
                    genres: list | None = None, date: str = "") -> str:
    """Заполнить шаблон metadata.yaml: title/author/subject/date.

    Остальные поля шаблона не трогаются. date пусто → сегодняшняя дата.
    genres=None → subject шаблона сохраняется; пустой список → subject
    очищается. Кавычки в значениях экранируются.
    """
    def q(v: str) -> str:
        return '"' + (v or "").replace("\\", "\\\\").replace('"', '\\"') + '"'

    date = date or datetime.date.today().isoformat()
    out, in_subject = [], False
    for line in template_text.splitlines():
        if re.match(r"^title:\s", line) or line.strip() == "title:":
            out.append(f"title: {q(title)}")
        elif re.match(r"^author:\s", line) or line.strip() == "author:":
            out.append(f"author: {q(author)}")
        elif re.match(r"^date:\s", line) or line.strip() == "date:":
            out.append(f"date: {q(date)}")
        elif re.match(r"^subject:\s*$", line):
            out.append("subject:")
            in_subject = True
            if genres:
                out.extend(f"  - {g}" for g in genres)
        elif in_subject:
            if re.match(r"^\s+-\s", line):
                if genres is None:  # сохранить subject шаблона
                    out.append(line)
                continue            # genres задан (пустой или список) — уже записан
            in_subject = False
            out.append(line)
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def fill_project_from_template(pdir: Path, template_dir: Path) -> list:
    """Скопировать prompts/ и source/ шаблона в проект (без перезаписи).

    Возвращает список скопированных файлов (относительно папки проекта).
    """
    pdir, template_dir = Path(pdir), Path(template_dir)
    copied = []
    for sub in ("prompts", "source"):
        src_dir = template_dir / sub
        if not src_dir.is_dir():
            continue
        (pdir / sub).mkdir(parents=True, exist_ok=True)
        for f in sorted(src_dir.iterdir()):
            if not f.is_file():
                continue
            dst = pdir / sub / f.name
            if dst.exists():
                continue
            shutil.copy2(f, dst)
            copied.append(f"{sub}/{f.name}")
    return copied


def write_project_metadata(pdir: Path, template_dir: Path,
                           title: str = "", author: str = "",
                           genres: list | None = None) -> bool:
    """Создать source/metadata.yaml проекта из шаблона типа книги.

    False — если metadata.yaml в шаблоне нет или уже существует в проекте.
    """
    pdir, template_dir = Path(pdir), Path(template_dir)
    src = template_dir / "source" / "metadata.yaml"
    dst = pdir / "source" / "metadata.yaml"
    if not src.is_file() or dst.exists():
        return False
    text = render_metadata(src.read_text(encoding="utf-8"),
                           title=title, author=author, genres=genres)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    return True


# ══════════════════════════════════════════════════════════════════
# Шаблоны: CRUD наборов и файлов (вкладка «Шаблоны»)
# ══════════════════════════════════════════════════════════════════

# Системный набор — только чтение: нельзя удалять/перезаписывать;
# копировать ИЗ него можно
TEMPLATE_PROTECTED = "General"


def _tpl_set_dir(templates_dir: Path, name: str) -> Path | None:
    """Папка набора (NFC), если это настоящая подпапка templates/."""
    name = unicodedata.normalize("NFC", (name or "").strip())
    if not name or name in (".", ".."):
        return None
    d = (Path(templates_dir) / name).resolve()
    root = Path(templates_dir).resolve()
    if d == root or root not in d.parents:
        return None
    return d


def _tpl_resolve(set_dir: Path, rel: str) -> Path | None:
    """Путь внутри набора из относительного rel (защита от эскейпов)."""
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel or rel in (".", ".."):
        return None
    p = (set_dir / rel).resolve()
    if p == set_dir or set_dir not in p.parents:
        return None
    return p


TEMPLATE_SKELETON = ("prompts", "source")


def _ensure_template_skeleton(set_dir: Path) -> None:
    """Гарантировать каркас набора: prompts/ + source/ (идемпотентно).

    скелет как у General — инвариант набора. Вызывается при
    создании, копировании и ремонте при чтении (_templates в web/api.py).
    """
    for sub in TEMPLATE_SKELETON:
        (set_dir / sub).mkdir(parents=True, exist_ok=True)


def create_template_set(templates_dir: Path, name: str) -> str | None:
    """Создать новый набор шаблонов с каркасом prompts/ + source/.

    Каркас тот же, что у General : пустые prompts/ и source/,
    чтобы у нового проекта были обе подпапки шаблона. Возвращает имя
    набора (NFC) или None: недопустимое имя, дубликат, попытка
    создать/занять General."""
    name = unicodedata.normalize("NFC", (name or "").strip())
    if not valid_project_name(name) or name == TEMPLATE_PROTECTED:
        return None
    d = _tpl_set_dir(templates_dir, name)
    if d is None or d.exists():
        return None
    d.mkdir(parents=True, exist_ok=True)
    _ensure_template_skeleton(d)
    return name


def copy_template_set(templates_dir: Path, src: str, dst: str) -> str | None:
    """Копировать набор src в новый набор dst (без перезаписи).

    src обязан существовать (General копировать можно — он только для
    чтения); dst — новое допустимое имя, не General и не существующее.
    Возвращает имя нового набора или None."""
    dst = unicodedata.normalize("NFC", (dst or "").strip())
    if not valid_project_name(dst) or dst == TEMPLATE_PROTECTED:
        return None
    sdir = _tpl_set_dir(templates_dir, src)
    ddir = _tpl_set_dir(templates_dir, dst)
    if sdir is None or ddir is None or not sdir.is_dir() or ddir.exists():
        return None
    shutil.copytree(sdir, ddir)
    # копия всегда со скелетом, даже если исходник «деградировал»
    _ensure_template_skeleton(ddir)
    return dst


def delete_template_set(templates_dir: Path, name: str) -> bool:
    """Удалить набор (General — запрещён). False — не найден/запрещён."""
    name = unicodedata.normalize("NFC", (name or "").strip())
    if name == TEMPLATE_PROTECTED:
        return False
    d = _tpl_set_dir(templates_dir, name)
    if d is None or not d.is_dir():
        return False
    try:
        shutil.rmtree(d, ignore_errors=True)
    except OSError:
        return False
    return True


def create_template_dir(templates_dir: Path, name: str, rel: str) -> str | None:
    """Создать каталог в наборе — ЗАПРЕЩЕНО.

    Каталоги в шаблонах неизменяемы: скелет prompts/ + source/ — всё,
    что разрешено. Роут остаётся (всегда ошибка), чтобы не ломать
    публичный API; UI-кнопка убрана. Возвращает строку-ошибку."""
    name = unicodedata.normalize("NFC", (name or "").strip())
    if name == TEMPLATE_PROTECTED:
        return "General — системный набор, изменение запрещено"
    set_dir = _tpl_set_dir(templates_dir, name)
    if set_dir is None or not set_dir.is_dir():
        return "Набор не найден"
    return "Каталоги в шаблонах неизменяемы"


def templates_files(templates_dir: Path, name: str, sub: str = "") -> list[str]:
    """Дерево файлов набора (относительные пути со слэшами).

    Пустые каталоги тоже включаются (путь с завершающим '/') — чтобы
    каталог не «пропадал» из списка после удаления последнего файла.
    sub — подпапка (например "prompts"), пусто = весь набор.
    Возвращает список, отсортированный по пути."""
    set_dir = _tpl_set_dir(templates_dir, name)
    if set_dir is None or not set_dir.is_dir():
        return []
    base = set_dir
    if sub:
        subp = _tpl_resolve(set_dir, sub)
        if subp is None or not subp.is_dir():
            return []
        base = subp
    out: list[str] = []
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(set_dir).as_posix()
        if p.is_file():
            out.append(rel)
        elif p.is_dir() and not any(p.iterdir()):
            out.append(rel + "/")
    return out


def read_template_file(templates_dir: Path, name: str, rel: str) -> str | None:
    """Содержимое файла набора (utf-8, errors=replace) или None."""
    set_dir = _tpl_set_dir(templates_dir, name)
    if set_dir is None:
        return None
    p = _tpl_resolve(set_dir, rel)
    if p is None or not p.is_file():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def write_template_file(templates_dir: Path, name: str, rel: str,
                        content: str) -> str | None:
    """Записать/создать файл в наборе (General — запрещён).

    каталоги неизменяемы — родительский каталог обязан
    существовать (неявное создание каталогов через путь файла запрещено).
    None — успех; строка-ошибка — иначе: набор не найден/General/эскейп/
    каталог не существует."""
    name = unicodedata.normalize("NFC", (name or "").strip())
    if name == TEMPLATE_PROTECTED:
        return "General — системный набор, изменение запрещено"
    set_dir = _tpl_set_dir(templates_dir, name)
    if set_dir is None or not set_dir.is_dir():
        return "Набор не найден"
    p = _tpl_resolve(set_dir, rel)
    if p is None:
        return "Недопустимый путь"
    if not p.parent.is_dir():
        return "Каталог не существует"
    p.write_text(content or "", encoding="utf-8")
    return None


def delete_template_file(templates_dir: Path, name: str, rel: str) -> str | None:
    """Удалить файл из набора (General — запрещён).

    каталоги неизменяемы — удаление каталога отклоняется
    ("Каталоги в шаблонах неизменяемы"); файлы удаляются как раньше.
    None — успех; строка-ошибка — иначе: набор не найден/General/
    пути нет/каталог."""
    name = unicodedata.normalize("NFC", (name or "").strip())
    if name == TEMPLATE_PROTECTED:
        return "General — системный набор, изменение запрещено"
    set_dir = _tpl_set_dir(templates_dir, name)
    if set_dir is None or not set_dir.is_dir():
        return "Набор не найден"
    p = _tpl_resolve(set_dir, rel)
    if p is None or not p.exists():
        return "Файл не найден"
    if p.is_dir():
        return "Каталоги в шаблонах неизменяемы"
    try:
        p.unlink()
    except OSError as exc:
        return f"Не удалось удалить файл: {exc}"
    return None


def template_file_info(templates_dir: Path, name: str,
                       rel: str) -> dict | None:
    """Размер/время файла набора: {size, mtime} или None (нет файла).

    Нужно редактору (мета-строка «путь · размер»), отдельно от чтения
    содержимого — read_template_file не трогаем."""
    set_dir = _tpl_set_dir(templates_dir, name)
    if set_dir is None:
        return None
    p = _tpl_resolve(set_dir, rel)
    if p is None or not p.is_file():
        return None
    try:
        st = p.stat()
        mtime = int(st.st_mtime)
    except OSError:
        return None
    return {"size": st.st_size, "mtime": mtime}


def move_template_file(templates_dir: Path, name: str,
                       src: str, dst: str) -> str | None:
    """Переименовать/перенести файл ИЛИ каталог внутри набора.

    None — успех; строка-ошибка — иначе. General — запрещён; src обязан
    существовать (файл или каталог); dst — новый путь (не должен
    существовать), эскейпы за пределы набора отклоняются."""
    name = unicodedata.normalize("NFC", (name or "").strip())
    if name == TEMPLATE_PROTECTED:
        return "General — системный набор, изменение запрещено"
    set_dir = _tpl_set_dir(templates_dir, name)
    if set_dir is None or not set_dir.is_dir():
        return "Набор не найден"
    sp = _tpl_resolve(set_dir, src)
    dp = _tpl_resolve(set_dir, dst)
    if sp is None or not sp.exists():
        return "Исходный путь не найден"
    if sp.is_dir():
        return "Каталоги в шаблонах неизменяемы"
    if dp is None:
        return "Недопустимый путь назначения"
    if dp.exists():
        return "Путь назначения уже существует"
    if not dp.parent.is_dir():
        return "Каталог не существует"  # каталоги не создаются
    sp.replace(dp)
    return None


def project_dir(projects_root: Path, section: str, name: str) -> Path:
    """Полный путь к папке проекта."""
    return Path(projects_root) / section / unicodedata.normalize("NFC", name.strip())


def list_projects(projects_root: Path, section: str) -> list:
    """Имена проектов раздела (отсортированы); пустой список, если раздела нет."""
    if section not in load_sections(projects_root):
        return []
    d = Path(projects_root) / section
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def _has_compiled(pdir: Path) -> bool:
    """Есть ли скомпилированные файлы (clean_and_compile пишет их в cwd
    проекта по умолчанию, либо в tmp//output/ при --out).

    экспорты EPUB/FB2 — '{имя_проекта}_{start}_{end}.{ext}';
    легаси-паттерн book_* тоже распознаётся."""
    new_pats = (f"{pdir.name}_*.epub", f"{pdir.name}_*.fb2")
    for d in (pdir, pdir / "tmp", pdir / "output"):
        if not d.is_dir():
            continue
        for pat in ("compiled_*.txt", "book_*.epub", "book_*.fb2", *new_pats):
            if any(d.glob(pat)):
                return True
    return False


def _artifact_ready(d: Path, names: set[str], canon: str,
                    legacy_suffix: str) -> bool:
    """Артефакт стадии готов: файл есть И не пустой (> 0 байт)."""
    for n in names:
        if n == canon or n.endswith(legacy_suffix):
            try:
                if (d / n).stat().st_size > 0:
                    return True
            except OSError:
                continue
    return False


def project_stats(pdir: Path) -> str:
    """Краткая строка состояния проекта: главы, артефакты, ner/wiki.

    Счётчик переведено/отредактировано/полировано: N/N/N. Имена файлов —
    канонические (translated.txt) ИЛИ легаси-варианты с префиксом главы
    (chapter770_translated.txt) из старых проектов.
    """
    pdir = Path(pdir)
    ch = pdir / "chapters"
    n_ch = n_tr = n_rd = n_pl = 0
    if ch.is_dir():
        for d in ch.iterdir():
            if not d.is_dir():
                continue
            n_ch += 1
            names = {f.name for f in d.iterdir() if f.is_file()}
            if "translated.txt" in names or any(
                    n.endswith("_translated.txt") for n in names):
                n_tr += 1
            if "redacted.txt" in names or any(
                    n.endswith("_redacted.txt") for n in names):
                n_rd += 1
            if "polished.txt" in names or any(
                    n.endswith("_polished.txt") for n in names):
                n_pl += 1
    ner = "✓" if (pdir / "ner.json").is_file() else "—"
    wiki = "✓" if (pdir / "wiki.md").is_file() else "—"
    comp = "✓" if _has_compiled(pdir) else "—"
    return (f"глав: {n_ch} | {n_tr}/{n_rd}/{n_pl} | "
            f"ner: {ner} | compiled: {comp} | wiki: {wiki}")


def project_progress_table(pdir: Path) -> dict:
    """Таблица готовности глав: по-главные флаги стадий + сводка.

    Возвращает:
      {"chapters": {id: {"translate": bool, "redact": bool,
                         "polish": bool}},
       "counts": {"chapters": N, "translate": N, "redact": N,
                   "polish": N},
       "ner": {"exists": bool, "terms": N},
       "wiki": {"exists": bool, "articles": N},
       "compiled": [имена файлов]}
    Имена артефактов — канонические ИЛИ легаси-суффиксы (как
    project_stats); номера глав — канон parse_chapter_id (core.common).
    """
    from core.common import parse_chapter_id  # лениво: избегаем циклов

    pdir = Path(pdir)
    ch = pdir / "chapters"
    chapters: dict[int, dict] = {}
    counts = {"chapters": 0, "translate": 0, "redact": 0, "polish": 0}
    if ch.is_dir():
        for d in sorted(ch.iterdir()):
            if not d.is_dir():
                continue
            cid = parse_chapter_id(d.name)
            if cid is None:
                continue
            names = {f.name for f in d.iterdir() if f.is_file()}
            tr = _artifact_ready(d, names, "translated.txt",
                                 "_translated.txt")
            rd = _artifact_ready(d, names, "redacted.txt", "_redacted.txt")
            pl = _artifact_ready(d, names, "polished.txt", "_polished.txt")
            chapters[cid] = {"translate": tr, "redact": rd, "polish": pl}
            counts["chapters"] += 1
            if tr:
                counts["translate"] += 1
            if rd:
                counts["redact"] += 1
            if pl:
                counts["polish"] += 1
    ner = {"exists": False, "terms": 0}
    nf = pdir / "ner.json"
    if nf.is_file():
        ner["exists"] = True
        try:
            data = json.loads(nf.read_text(encoding="utf-8"))
            ner["terms"] = len(data) if isinstance(data, list) else 0
        except (OSError, ValueError):
            pass
    wiki = {"exists": False, "articles": 0}
    wf = pdir / "wiki.md"
    if wf.is_file():
        wiki["exists"] = True
        try:
            text = wf.read_text(encoding="utf-8", errors="replace")
            wiki["articles"] = sum(1 for line in text.splitlines()
                                    if line.startswith("## "))
        except OSError:
            pass
    prefix = f"{pdir.name}_"
    compiled = sorted(
        f.name for d in (pdir, pdir / "tmp", pdir / "output")
        if d.is_dir()
        for f in d.iterdir()
        if f.is_file() and (
            f.name.startswith("compiled_") or f.name.startswith("book_")
            or (f.name.startswith(prefix)
                and (f.name.endswith(".epub") or f.name.endswith(".fb2")))))
    return {"chapters": chapters, "counts": counts,
            "ner": ner, "wiki": wiki, "compiled": compiled}


def create_project(projects_root: Path, section: str, name: str):
    """Создать проект с каркасом папок.

    Возвращает (ok: bool, message_or_path). При успехе второй элемент —
    Path созданной папки, при ошибке — строка с причиной (текст для UI).
    """
    projects_root = Path(projects_root)
    sections = load_sections(projects_root)
    if section not in sections:
        return False, f"Неизвестный раздел: {section!r} (доступны: {', '.join(sections)})."
    name = unicodedata.normalize("NFC", (name or "").strip())
    if not valid_project_name(name):
        return False, "Недопустимое имя проекта (пустое, слишком длинное или содержит /\\:*?\"<>|)."
    dst = project_dir(projects_root, section, name)
    if dst.exists():
        return False, f"Проект уже существует: {section}/{name}."
    try:
        for sub in PROJECT_SKELETON:
            (dst / sub).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"Не удалось создать папки проекта: {e}"
    return True, dst


def move_project(projects_root: Path, src_section: str, name: str,
                 dst_section: str):
    """Перенести проект между разделами.

    Возвращает (ok, message_or_path). Ошибки: нет исходного проекта,
    дубль в целевом разделе, недопустимые разделы.
    """
    projects_root = Path(projects_root)
    sections = load_sections(projects_root)
    for sec in (src_section, dst_section):
        if sec not in sections:
            return False, f"Неизвестный раздел: {sec!r}."
    if src_section == dst_section:
        return False, "Проект уже в этом разделе."
    src = project_dir(projects_root, src_section, name)
    if not src.is_dir():
        return False, f"Проект не найден: {src_section}/{name}."
    dst = project_dir(projects_root, dst_section, src.name)
    if dst.exists():
        return False, f"В разделе {dst_section} уже есть проект {dst.name!r}."
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    except OSError as e:
        return False, f"Не удалось перенести проект: {e}"
    return True, dst


def rename_project(projects_root: Path, section: str, name: str, new_name: str):
    """Переименовать проект внутри раздела. Возвращает (ok, message_or_path)."""
    projects_root = Path(projects_root)
    if section not in load_sections(projects_root):
        return False, f"Неизвестный раздел: {section!r}."
    src = project_dir(projects_root, section, name)
    if not src.is_dir():
        return False, f"Проект не найден: {section}/{name}."
    new_name = unicodedata.normalize("NFC", (new_name or "").strip())
    if not valid_project_name(new_name):
        return False, "Недопустимое новое имя проекта."
    dst = project_dir(projects_root, section, new_name)
    if dst.exists():
        return False, f"Проект с именем {new_name!r} уже есть в разделе {section}."
    try:
        shutil.move(str(src), str(dst))
    except OSError as e:
        return False, f"Не удалось переименовать проект: {e}"
    return True, dst


def delete_project(projects_root: Path, section: str, name: str):
    """Удалить папку проекта (без подтверждений — они в UI-слое).

    Возвращает (ok, message_or_path).
    """
    projects_root = Path(projects_root)
    if section not in load_sections(projects_root):
        return False, f"Неизвестный раздел: {section!r}."
    src = project_dir(projects_root, section, name)
    if not src.is_dir():
        return False, f"Проект не найден: {section}/{name}."
    try:
        shutil.rmtree(src)
    except OSError as e:
        return False, f"Не удалось удалить проект: {e}"
    return True, src


def copy_project(projects_root: Path, section: str, name: str, new_name: str):
    """Дублировать проект внутри раздела (shutil.copytree).

    Возвращает (ok, message_or_path).
    """
    projects_root = Path(projects_root)
    if section not in load_sections(projects_root):
        return False, f"Неизвестный раздел: {section!r}."
    src = project_dir(projects_root, section, name)
    if not src.is_dir():
        return False, f"Проект не найден: {section}/{name}."
    new_name = unicodedata.normalize("NFC", (new_name or "").strip())
    if not valid_project_name(new_name):
        return False, "Недопустимое имя копии проекта."
    dst = project_dir(projects_root, section, new_name)
    if dst.exists():
        return False, f"Проект с именем {new_name!r} уже есть в разделе {section}."
    try:
        shutil.copytree(src, dst)
    except OSError as e:
        return False, f"Не удалось скопировать проект: {e}"
    return True, dst

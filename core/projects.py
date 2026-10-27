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

from .archive import ARCHIVE_DIR
from .common import atomic_write

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

# Мьютекс на операции с папками проектов (create/move/rename/copy/delete):
# check-then-act (dst.exists() → move/rmtree) без него даёт TOCTOU — два
# параллельных запроса оба проходят проверку. Отдельный от разделов:
# из проектных секций зовётся load_sections, поэтому порядок захвата всегда
# «проекты → разделы», обратного нет — взаимных блокировок не бывает.
_PROJECTS_LOCK = threading.RLock()

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


# Свойства раздела в .sections.json: имя плюс то, что описывает его целиком.
# `archive` — раздел-архив (проекты живут файлами, а не папками);
# `pack_on_move` — при переносе сюда предлагать упаковку (молчаливой нет).
# Отдельного флага «предлагать упаковку» у архивного раздела нет: пометка
# архивным сама включает предложение — иначе два соседних свойства
# противоречили бы друг другу («архив, но книги держим папками»).
SECTION_PROPS = ("archive", "pack_on_move")
DEFAULT_SECTION_PROPS = {"archive": False, "pack_on_move": False}


def _entry_props(entry) -> tuple[str, dict | None]:
    """Разбор записи раздела: строка (старый формат) или объект.

    Старый файл — список имён; новый — список `{name, archive, pack_on_move}`.
    Оба формата читаются без миграции: установка обновляется молча, а
    `archive: false` у старых записей — важно (первая же DONE-книга уехала
    бы в архив без спроса).

    Второй элемент `None` у строки — «имя без мнения о свойствах»: так
    `save_sections` с списком имён (`rename_section`, `delete_section`) не
    сбрасывает уже сохранённый флаг архива в `False`.
    """
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, dict):
        name = entry.get("name")
        if not isinstance(name, str):
            return "", None
        props = dict(DEFAULT_SECTION_PROPS)
        for key in SECTION_PROPS:
            props[key] = bool(entry.get(key))
        return name, props
    return "", None


def section_entries(projects_root: Path) -> list[dict]:
    """Разделы со свойствами: `[{name, archive, pack_on_move}, …]`.

    Порядок и состав тот же, что у `load_sections` (дефолты, до-обнаруженные
    папки, отброшенные призраки) — это одна и та же вселенная разделов,
    просто со свойствами. Формат свойств читает это место, остальной код
    продолжает работать со списком имён.
    """
    return [{"name": n, **props}
            for n, props in _all_entries(projects_root)]


def _all_entries(projects_root: Path) -> list[tuple[str, dict]]:
    """`[(имя, свойства)]` — одна реализация чтения для всех видов запроса."""
    root = Path(projects_root)
    entries: list[tuple[str, dict]] = []
    had_file = False
    f = _sections_file(root)
    if f.is_file():
        had_file = True
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, list):
                seen: set[str] = set()
                for raw in data:
                    name, props = _entry_props(raw)
                    if name and valid_project_name(name) and name not in seen:
                        seen.add(name)
                        entries.append((name, dict(props or DEFAULT_SECTION_PROPS)))
        except (OSError, ValueError):
            pass
    if not entries:
        entries = [(s, dict(DEFAULT_SECTION_PROPS)) for s in DEFAULT_SECTIONS]
    if root.is_dir():
        on_disk = [d.name for d in sorted(root.iterdir())
                   if d.is_dir() and valid_project_name(d.name)
                   and d.name != ARCHIVE_DIR]
        # реальные папки без записи — до-обнаруживаем (ручные/легаси);
        # записи без папки на диске — призраки (гонка/ручное удаление)
        if had_file:
            disk_set = set(on_disk)
            entries = [e for e in entries if e[0] in disk_set]
        known = {n for n, _ in entries}
        entries.extend((d, dict(DEFAULT_SECTION_PROPS))
                       for d in on_disk if d not in known)
    return entries


def load_sections(projects_root: Path) -> list:
    """Список имён разделов пульта: дефолты + кастомные из .sections.json.

    Файла нет → дефолты + легаси-папки на диске (миграция, напр. DONE_OPEN
    со старых установок). Папка на диске, которой нет в списке, тоже
    добавляется (ручные папки). Дубли в файле схлопываются (след гонки
    параллельных create_section); запись файла без папки на диске —
    призрак и игнорируется (создание раздела всегда создаёт папку;
    папка исчезает только при удалении раздела). Никогда не бросает.

    Свойства раздела (архив) см. `section_entries` — здесь только имена:
    этот список зовут два десятка мест, и каждому из них нужны имена.
    """
    with _SECTIONS_LOCK:
        return [n for n, _ in _all_entries(projects_root)]


def _load_sections_unlocked(projects_root: Path) -> list:
    """Чтение списка разделов без захвата мьютекса (внутри секций)."""
    return [n for n, _ in _all_entries(projects_root)]


def section_props(projects_root: Path, name: str) -> dict:
    """Свойства раздела; неизвестный раздел — дефолтные (не ошибка).

    Неизвестный раздел не бросает: свойства читают и для только что
    созданного имени, и для имени из запроса до проверки существования.
    """
    with _SECTIONS_LOCK:
        for n, props in _all_entries(projects_root):
            if n == name:
                return dict(props)
    return dict(DEFAULT_SECTION_PROPS)


def set_section_props(projects_root: Path, name: str, **props) -> tuple[bool, str]:
    """Изменить свойства раздела (`archive`, `pack_on_move`).

    Незнакомый ключ — отказ (иначе опечатка молча терялась бы): набор
    свойств замкнут. Раздела нет — отказ: свойства без раздела не живут.
    Возвращает `(ok, сообщение_или_имя)` — как остальные операции.

    `pack_on_move` следует за `archive`: пометка раздела архивным сама
    включает предложение упаковки при переносе, снятие — выключает.
    Явно переданный `pack_on_move` при смене `archive` в том же запросе
    игнорируется: два ответа на один вопрос разошлись бы.
    """
    bad = [k for k in props if k not in SECTION_PROPS]
    if bad:
        return False, f"Неизвестное свойство раздела: {', '.join(bad)}."
    wanted = dict(props)
    if "archive" in wanted:
        wanted["pack_on_move"] = bool(wanted["archive"])
    with _SECTIONS_LOCK:
        entries = _all_entries(projects_root)
        names = [n for n, _ in entries]
        if name not in names:
            return False, f"Раздел не найден: {name!r}."
        for n, p in entries:
            if n == name:
                for k, v in wanted.items():
                    p[k] = bool(v)
        save_sections(projects_root, [{"name": n, **p} for n, p in entries])
    return True, name


def archive_sections(projects_root: Path) -> list:
    """Имена разделов-архивов (проекты внутри — файлы `.nmz`)."""
    with _SECTIONS_LOCK:
        return [n for n, p in _all_entries(projects_root) if p.get("archive")]


def is_archive_section(projects_root: Path, name: str) -> bool:
    """Раздел-архив ли этот."""
    return bool(section_props(projects_root, name).get("archive"))


def save_sections(projects_root: Path, sections: list) -> bool:
    """Атомарно записать список разделов в .sections.json (tmp+replace).

    Принимает имена (или готовые объекты). При записи имён свойства уже
    существующих разделов сохраняются: `rename_section` и `delete_section`
    зовут это место со списком имён, и терять из-за этого флаг архива нельзя.

    False — не удалось записать (папка/раздел при этом уже созданы/удалены;
    load_sections до-обнаружит папки на диске)."""
    root = Path(projects_root)
    known: dict[str, dict] = {}
    try:
        if _sections_file(root).is_file():
            known = dict(_all_entries(root))
    except (OSError, ValueError):  # чтение старого файла не должно мешать записи
        known = {}
    out = []
    for item in sections:
        name, props = _entry_props(item)
        if not name:
            continue
        merged = dict(known.get(name, DEFAULT_SECTION_PROPS))
        if props is not None:  # строка — только имя, свойства остаются как были
            merged.update(props)
        out.append({"name": name, **merged})
    try:
        root.mkdir(parents=True, exist_ok=True)
        # общий atomic_write: fsync перед replace (пережили выключение) и
        # уникальный tmp — фиксированное .tmp два запроса затёрли бы друг друга
        atomic_write(_sections_file(root),
                     json.dumps(out, ensure_ascii=False, indent=2))
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
        entries = _all_entries(root)  # до перемещения: свойства src ещё видны
        src_props = next((dict(p) for n, p in entries if n == src),
                         dict(DEFAULT_SECTION_PROPS))
        merge = dst in sections
        if merge:
            dst_dir = root / dst
            try:
                dst_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                return False, f"Не удалось создать раздел: {e}"
            for p in sorted(src_dir.iterdir()):
                if p.name == ARCHIVE_DIR:
                    continue  # архивы сводятся отдельно: внутри лежат файлы
                if not p.is_dir():
                    continue
                if (dst_dir / p.name).exists():
                    return False, (f"В разделе {dst} уже есть проект "
                                   f"{p.name!r} — перенос невозможен.")
            # архивы каждого раздела: два `.archive` простым move не сливаются
            src_arc = Path(src_dir) / ARCHIVE_DIR
            dst_arc = Path(dst_dir) / ARCHIVE_DIR
            if src_arc.is_dir():
                for f in sorted(src_arc.glob("*")):
                    if (dst_arc / f.name).exists():
                        return False, (f"В разделе {dst} уже есть архив "
                                       f"{f.name!r} — перенос невозможен.")
            try:
                for p in sorted(src_dir.iterdir()):
                    if p.name == ARCHIVE_DIR or not p.is_dir():
                        continue
                    shutil.move(str(p), str(dst_dir / p.name))
                if src_arc.is_dir():
                    dst_arc.mkdir(parents=True, exist_ok=True)
                    for f in sorted(src_arc.glob("*")):
                        shutil.move(str(f), str(dst_arc / f.name))
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
            out = [{"name": n, **p} for n, p in entries if n != src]
        else:
            # переименование на месте: позиция и свойства сохраняются
            out = [{"name": dst, **p} if n == src else {"name": n, **p}
                   for n, p in entries]
            if not any(n == src for n, _p in entries):
                out.append({"name": dst, **src_props})
        save_sections(root, out)
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
            return False, ("Раздел не пуст — сначала перенесите или удалите "
                           "проекты (и архивы, если раздел архивный).")
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


def copy_template_prompts(pdir: Path, template_dir: Path) -> dict:
    """Скопировать ВСЕ промпты набора в проект (с заменой существующих).

    Нужно, когда книге нужен полный комплект промптов набора сразу: это
    именно замена (пользователь выбрал «весь набор» сознательно), поэтому
    совпадающие по имени файлы перезаписываются. Возвращает
    {copied, replaced} — имена файлов; replaced — те, что в книге уже были.
    """
    pdir, template_dir = Path(pdir), Path(template_dir)
    src_dir = Path(template_dir) / "prompts"
    copied: list[str] = []
    replaced: list[str] = []
    if not src_dir.is_dir():
        return {"copied": copied, "replaced": replaced}
    dst_dir = Path(pdir) / "prompts"
    dst_dir.mkdir(parents=True, exist_ok=True)
    for f in sorted(src_dir.iterdir()):
        if not f.is_file():
            continue
        dst = dst_dir / f.name
        if dst.exists():
            replaced.append(f.name)
        shutil.copy2(f, dst)
        copied.append(f.name)
    return {"copied": copied, "replaced": replaced}


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
    atomic_write(dst, text)
    return True


# ══════════════════════════════════════════════════════════════════
# Шаблоны: CRUD наборов и файлов (вкладка «Шаблоны»)
# ══════════════════════════════════════════════════════════════════

# Системный набор — только чтение: нельзя удалять/перезаписывать;
# копировать ИЗ него можно
TEMPLATE_PROTECTED = "General"
# шаблон общего конфига — тоже поставка программы (лежит рядом с наборами)
TEMPLATE_ENV_EXAMPLE = ".env.example"


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
    try:
        atomic_write(p, content or "")
    except OSError as exc:
        return f"Не удалось записать: {exc}"
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


def _same_content(a: Path, b: Path) -> bool:
    """Файлы совпадают побайтово (размер + куски; файлы мелкие)."""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        with a.open("rb") as fa, b.open("rb") as fb:
            while True:
                ca, cb = fa.read(65536), fb.read(65536)
                if ca != cb:
                    return False
                if not ca:
                    return True
    except OSError:
        return False


def builtin_template_files(source_dir: Path) -> list:
    """Файлы шаблонов, которые принадлежат программе, а не пользователю.

    Это системный набор `TEMPLATE_PROTECTED` и шаблон конфига
    `TEMPLATE_ENV_EXAMPLE` — те же части, что обновление программы кладёт
    поверх (`core/update.py::is_protected`). Остальное (свои наборы) — данные
    человека.
    """
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        return []
    env_example = source_dir / TEMPLATE_ENV_EXAMPLE
    files = [env_example] if env_example.is_file() else []
    general = source_dir / TEMPLATE_PROTECTED
    if general.is_dir():
        files += sorted(p for p in general.rglob("*") if p.is_file())
    return files


def sync_builtin_templates(templates_dir: Path, source_dir: Path) -> dict:
    """Привести встроенные части templates/ к поставке программы (образу).

    Нужно там, где папка шаблонов лежит вне дерева кода (bind-mount в Docker):
    новый образ приносит свои шаблоны, но смонтированный каталог их не видит,
    и новая стадия молча осталась бы на встроенном промпте. Встроенные части
    (см. `builtin_template_files`) перезаписываются целиком — `General` это
    системный набор (только чтение в интерфейсе, правкой не настраивается:
    свои промпты живут в собственном наборе или в проекте); наборы человека
    не трогаются вовсе, как и у обновления программы.

    Возвращает `{added, copied, unchanged}` — пути относительно templates/:
    файла не было, содержимое отличалось, совпало.
    """
    templates_dir = Path(templates_dir)
    added, copied, unchanged = [], [], []
    for src in builtin_template_files(source_dir):
        rel = src.relative_to(Path(source_dir)).as_posix()
        dst = templates_dir / rel
        try:
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                added.append(rel)
            elif _same_content(src, dst):
                unchanged.append(rel)
            else:
                shutil.copy2(src, dst)
                copied.append(rel)
        except OSError as exc:
            raise OSError(f"{rel}: {exc}") from exc
    return {"added": added, "copied": copied, "unchanged": unchanged}


def project_dir(projects_root: Path, section: str, name: str) -> Path:
    """Полный путь к папке проекта."""
    return Path(projects_root) / section / unicodedata.normalize("NFC", name.strip())


def list_projects(projects_root: Path, section: str) -> list:
    """Имена проектов раздела (отсортированы); пустой список, если раздела нет.

    В архиве проекта — папка, а не файл: имя собирается из обоих.
    Проект без папки и без архива не показывается.
    """
    if section not in load_sections(projects_root):
        return []
    return sorted(p["name"] for p in project_items(projects_root, section))


def project_items(projects_root: Path, section: str) -> list[dict]:
    """Проекты раздела со способом хранения: `[{name, archived, size, packed_at}]`.

    `archived` — проект лежит архивом (`<раздел>/.archive/<имя>.nmz`), папки
    нет; в карточке такой проект показывается как есть, но открыть его
    нельзя — сначала разархивация. `size`/`packed_at` берутся из манифеста
    (читается из головы архива, без распаковки).

    Служебные каталоги не выглядят проектами: `.archive` и незавершённые
    операции упаковки/распаковки (`<имя>.nmz.tmp`, `.nm_restore_<имя>`, см.
    `core/archive.py`) — иначе во время работы человек видел бы две книги с
    одним именем и мог бы упаковать мусор.
    """
    from . import archive as arc  # локально: модуль тянет tarfile/shas

    if section not in load_sections(projects_root):
        return []
    root = Path(projects_root) / section
    live: dict[str, dict] = {}
    if root.is_dir():
        for p in root.iterdir():
            if p.is_dir() and not p.is_symlink() \
                    and p.name != ARCHIVE_DIR and not arc.is_temp_name(p.name):
                live[p.name] = {"name": p.name, "archived": False,
                                "size": 0, "packed_at": ""}
    out = list(live.values())
    for item in list_archives(root):
        # папка сильнее: недораспакованный архив (оба остались) не дублирует
        if item["name"] in live:
            continue
        out.append({"name": item["name"], "archived": True,
                    "size": item["size"], "packed_at": item["packed_at"]})
    return sorted(out, key=lambda p: p["name"])


def list_archives(section_dir: Path) -> list[dict]:
    """Архивы раздела: `[{name, path, size, packed_at, files, error}]`.

    Список строится по файлам `.nmz` в `<раздел>/.archive/`: манифест
    читается из головы каждого — имена и даты видны без распаковки.
    Битый архив попадает в список с `error`: спрятать его хуже, чем показать.
    """
    from . import archive as arc  # локально: модуль тянет tarfile/shas

    d = arc.archive_dir(section_dir)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*" + arc.ARCHIVE_EXT)):
        if not f.is_file():
            continue
        man = arc.archive_manifest(f)
        out.append({
            "name": f.stem,
            "path": f,
            "size": arc.archive_size(f),
            "packed_at": man.get("packed_at", ""),
            "files": man.get("files", 0),
            "error": "" if man else "манифест не читается",
        })
    return out


def archive_candidates(projects_root: Path, section: str, op: str) -> dict:
    """Кого задевает пачка всего раздела: `{todo: [имена], total, done}`.

    `pack` — книги, лежащие папками (архивы не трогаем), `unpack` — лежащие
    архивами. `total` — сколько проектов в разделе вообще, `done` — сколько
    уже в целевом состоянии: по ним интерфейс отличает «всё уже упаковано»
    от «раздел пуст», не собирая список руками.
    """
    if op not in ("pack", "unpack"):
        return {"todo": [], "total": 0, "done": 0}
    items = project_items(projects_root, section)
    packed = op == "pack"
    todo = [i["name"] for i in items if bool(i.get("archived")) != packed]
    return {"todo": todo, "total": len(items),
            "done": len(items) - len(todo)}


def archive_project(projects_root: Path, section: str, name: str,
                    skip=None, progress=None) -> tuple:
    """Упаковать проект в `<раздел>/.archive/<имя>.nmz` и удалить папку.

    Шаги с гарантией порядка: упаковали → проверили манифест → удалили папку.
    Падение любого шага оставляет проект целым (архив — новый файл, он мог
    остаться мусором, но не заменил собой книгу). Возвращает
    `(ok, манифест_или_сообщение)`.
    """
    from . import archive as arc  # локально: тяжёлые импорты не нужны везде

    projects_root = Path(projects_root)
    with _PROJECTS_LOCK:
        if section not in load_sections(projects_root):
            return False, f"Неизвестный раздел: {section!r}."
        src = project_dir(projects_root, section, name)
        if not src.is_dir():
            if arc.archive_path(projects_root / section, name).is_file():
                return False, "Проект уже в архиве."
            return False, f"Проект не найден: {section}/{name}."
        dst = arc.archive_path(projects_root / section, src.name)
        try:
            man = arc.pack_project(src, dst, skip=skip, progress=progress)
        except (arc.ArchiveError, OSError) as exc:
            return False, f"Не удалось упаковать проект: {exc}"
        # архив не должен выглядеть пустым/битым: распаковка — единственный
        # способ узнать наверняка, но манифест уже есть в его голове
        if not arc.archive_manifest(dst):
            return False, "Архив не читается — проект оставлен как был."
        try:
            shutil.rmtree(src)
        except OSError as exc:
            return False, (f"Проект упакован, но папка не удалена: {exc}")
    return True, man


def unarchive_project(projects_root: Path, section: str, name: str,
                      progress=None) -> tuple:
    """Распаковать архив раздела обратно в папку проекта.

    Архив НЕ удаляется: если распаковка прошла, но удаление не удалось,
    проект всё равно есть, а лишний архив — маленькая проблема (его видно
    и можно убрать руками); обратное — потеря книги. Возвращает
    `(ok, путь_или_сообщение)`.
    """
    from . import archive as arc  # локально

    projects_root = Path(projects_root)
    with _PROJECTS_LOCK:
        if section not in load_sections(projects_root):
            return False, f"Неизвестный раздел: {section!r}."
        sec_dir = projects_root / section
        dst = project_dir(projects_root, section, name)
        here = arc.archive_path(sec_dir, dst.name)
        if dst.is_dir():
            return False, f"Проект уже распакован: {section}/{name}."
        if not here.is_file():
            return False, f"Архив не найден: {section}/{name}."
        try:
            arc.unpack_project(here, dst, progress=progress)
        except (arc.ArchiveError, OSError) as exc:
            return False, f"Не удалось распаковать проект: {exc}"
        try:
            here.unlink()
        except OSError:
            pass  # лишний архив не ошибка: проект уже на месте
    return True, dst


def delete_archive(section_dir: Path, name: str) -> tuple:
    """Удалить архив проекта (только файл, подтверждение — в UI-слое).

    Возвращает `(ok, путь_или_сообщение)` — как остальные операции раздела.
    """
    from . import archive as arc  # локально

    path = arc.archive_path(section_dir, name)
    if not path.is_file():
        return False, f"Архив не найден: {name}."
    try:
        path.unlink()
    except OSError as exc:
        return False, f"Не удалось удалить архив: {exc}"
    return True, path


def _has_compiled(pdir: Path) -> bool:
    """Есть ли скомпилированные файлы (clean_and_compile пишет их в
    tmp/ проекта; старые версии писали в корень или output/).

    Паттерны: '{имя_проекта}_{start}_{end}[.epub/.fb2/_txt.txt]';
    легаси compiled_*/book_* тоже распознаются."""
    new_pats = (f"{pdir.name}_*.epub", f"{pdir.name}_*.fb2",
                f"{pdir.name}_*_txt.txt", f"{pdir.name}_*_txt-plain.txt")
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


# стадии таблицы готовности — в порядке цепочки артефактов
PROGRESS_STAGES = ("translate", "redact", "polish")


def progress_missing(chapters: dict, stages=PROGRESS_STAGES) -> dict:
    """Чего не хватает по каждой стадии: диапазоны и счётчики пропусков.

    Вселенная глав — непрерывный ряд от первой до последней существующей
    папки (как all_ids вкладки «Главы»): пропущенная папка и глава без
    файла стадии одинаково видны в пропусках. Главы без файла стадии —
    числами, для подписи «Отсутствуют главы 75, 89, 361-717».

    Возвращает {stage: {"ids": [номера], "ranges": "75, 89, 361-717",
                        "count": N, "total": N, "pct": N}}; готово всё —
    ids пуст, ranges «—»; глав нет вовсе — total 0 и pct 0.
    """
    from .common import format_ranges  # лениво: избегаем циклов

    ids = sorted(chapters)
    all_ids = list(range(ids[0], ids[-1] + 1)) if ids else []
    out: dict[str, dict] = {}
    for stage in stages:
        missing = [i for i in all_ids
                   if not (chapters.get(i) or {}).get(stage)]
        done = len(all_ids) - len(missing)
        out[stage] = {
            "ids": missing,
            "ranges": format_ranges(missing),
            "count": len(missing),
            "total": len(all_ids),
            "pct": round(100 * done / len(all_ids)) if all_ids else 0,
        }
    return out


def project_progress_table(pdir: Path) -> dict:
    """Таблица готовности глав: по-главные флаги стадий + сводка.

    Возвращает:
      {"chapters": {id: {"translate": bool, "redact": bool,
                         "polish": bool}},
       "counts": {"chapters": N, "translate": N, "redact": N,
                   "polish": N},
       "missing": {stage: {"ids", "ranges", "count", "total", "pct"}}
                   — см. progress_missing,
       "ner": {"exists": bool, "terms": N},
       "wiki": {"exists": bool, "articles": N},  # rulate-сборка (###) тоже считается
       "compiled": [имена файлов]}
    Имена артефактов — канонические ИЛИ легаси-суффиксы (как
    project_stats); номера глав — канон parse_chapter_id (core.common).
    """
    from .common import parse_chapter_id  # лениво: избегаем циклов

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
            lines = wf.read_text(encoding="utf-8",
                                  errors="replace").splitlines()
            # Статья — «## Имя» (обычный режим). В rulate-режиме заголовки
            # сдвинуты на уровень глубже (_shift_headings в cli/wiki.py):
            # статейный — «### Имя», подзаголовки — «#### »; «## » там нет.
            # Опознание по наличию «## » вне «## Содержание» — иначе
            # подзаголовки «### Описание» обычного режима дали бы лишнее.
            h2 = [l for l in lines
                  if l.startswith("## ")
                  and not l.startswith("## Содержание")]
            if h2:
                wiki["articles"] = len(h2)
            else:
                wiki["articles"] = sum(
                    1 for l in lines if l.startswith("### "))
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
            "missing": progress_missing(chapters),
            "ner": ner, "wiki": wiki, "compiled": compiled}


def create_project(projects_root: Path, section: str, name: str):
    """Создать проект с каркасом папок.

    Возвращает (ok: bool, message_or_path). При успехе второй элемент —
    Path созданной папки, при ошибке — строка с причиной (текст для UI).
    """
    projects_root = Path(projects_root)
    with _PROJECTS_LOCK:
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
    """Перенести проект между разделами (папку или архив — как есть).

    Архив едет файлом в `.archive` целевого раздела: перенос `cp` не стоит.
    Возвращает (ok, message_or_path). Ошибки: нет исходного проекта,
    дубль в целевом разделе, недопустимые разделы.
    """
    from . import archive as arc  # локально

    projects_root = Path(projects_root)
    with _PROJECTS_LOCK:
        sections = load_sections(projects_root)
        for sec in (src_section, dst_section):
            if sec not in sections:
                return False, f"Неизвестный раздел: {sec!r}."
        if src_section == dst_section:
            return False, "Проект уже в этом разделе."
        src = project_dir(projects_root, src_section, name)
        src_arc = arc.archive_path(projects_root / src_section, src.name)
        packed = not src.is_dir()
        src = src_arc if packed else src
        if not src.exists():
            return False, f"Проект не найден: {src_section}/{name}."
        dst = (arc.archive_path(projects_root / dst_section, name) if packed
               else project_dir(projects_root, dst_section, name))
        if dst.exists():
            return False, f"В разделе {dst_section} уже есть проект {name!r}."
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        except OSError as e:
            return False, f"Не удалось перенести проект: {e}"
    return True, dst


def move_projects(projects_root: Path, src_section: str, names, dst_section: str) -> dict:
    """Перенести пачку проектов в другой раздел: `{moved, skipped}`.

    Один заход берёт блокировку один раз (на сотне книг `move_project`
    дёргал бы её сотню раз), но причины отказов остаются поштучные: занятое
    имя в целевом разделе или битое имя пропускаются с причиной, а не
    отменяют всю пачку (частичный перенос лучше отказа целиком).
    """
    moved: list[dict] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    for name in names or []:
        name = str(name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        ok, res = move_project(projects_root, src_section, name, dst_section)
        if ok:
            moved.append({"section": src_section, "name": name,
                          "dst": dst_section, "path": str(res)})
        else:
            skipped.append({"section": src_section, "name": name,
                            "reason": str(res)})
    return {"moved": moved, "skipped": skipped}


def rename_project(projects_root: Path, section: str, name: str, new_name: str):
    """Переименовать проект внутри раздела (папку или архив).

    Возвращает (ok, message_or_path).
    """
    from . import archive as arc  # локально

    projects_root = Path(projects_root)
    new_name = unicodedata.normalize("NFC", (new_name or "").strip())
    if not valid_project_name(new_name):
        return False, "Недопустимое новое имя проекта."
    with _PROJECTS_LOCK:
        if section not in load_sections(projects_root):
            return False, f"Неизвестный раздел: {section!r}."
        sec_dir = projects_root / section
        src = project_dir(projects_root, section, name)
        src_arc = arc.archive_path(sec_dir, src.name)
        if src.is_dir():
            packed = False
            dst = project_dir(projects_root, section, new_name)
        elif src_arc.is_file():
            packed = True
            dst = arc.archive_path(sec_dir, new_name)
        else:
            return False, f"Проект не найден: {section}/{name}."
        if dst.exists():
            return False, f"Проект с именем {new_name!r} уже есть в разделе {section}."
        try:
            shutil.move(str(src_arc if packed else src), str(dst))
        except OSError as e:
            return False, f"Не удалось переименовать проект: {e}"
    return True, dst


def delete_project(projects_root: Path, section: str, name: str):
    """Удалить проект — папку или архив (подтверждения — в UI-слое).

    Возвращает (ok, message_or_path).
    """
    from . import archive as arc  # локально

    projects_root = Path(projects_root)
    with _PROJECTS_LOCK:
        if section not in load_sections(projects_root):
            return False, f"Неизвестный раздел: {section!r}."
        src = project_dir(projects_root, section, name)
        if not src.is_dir():
            here = arc.archive_path(projects_root / section, src.name)
            if not here.is_file():
                return False, f"Проект не найден: {section}/{name}."
            try:
                here.unlink()
            except OSError as e:
                return False, f"Не удалось удалить архив: {e}"
            return True, here
        try:
            shutil.rmtree(src)
        except OSError as e:
            return False, f"Не удалось удалить проект: {e}"
    return True, src


def copy_project(projects_root: Path, section: str, name: str, new_name: str):
    """Дублировать проект внутри раздела (shutil.copytree / копия архива).

    Проектный .env (ключи/профили книги) в копию НЕ переносится — копия
    не должна наследовать чужие секреты. Возвращает (ok, message_or_path).
    """
    from . import archive as arc  # локально

    projects_root = Path(projects_root)
    new_name = unicodedata.normalize("NFC", (new_name or "").strip())
    if not valid_project_name(new_name):
        return False, "Недопустимое имя копии проекта."
    with _PROJECTS_LOCK:
        if section not in load_sections(projects_root):
            return False, f"Неизвестный раздел: {section!r}."
        sec_dir = projects_root / section
        src = project_dir(projects_root, section, name)
        src_arc = arc.archive_path(sec_dir, src.name)
        if src.is_dir():
            packed = False
            dst = project_dir(projects_root, section, new_name)
        elif src_arc.is_file():
            packed = True
            dst = arc.archive_path(sec_dir, new_name)
        else:
            return False, f"Проект не найден: {section}/{name}."
        if dst.exists():
            return False, f"Проект с именем {new_name!r} уже есть в разделе {section}."
        try:
            if packed:
                shutil.copy2(src_arc, dst)
            else:
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".env"))
        except OSError as e:
            return False, f"Не удалось скопировать проект: {e}"
    return True, dst


if __name__ == "__main__":  # pragma: no cover
    # Вход для docker-entrypoint.sh: встроенные шаблоны образа кладутся в
    # смонтированный каталог шаблонов, наборы пользователя не трогаются.
    #   python3 -m core.projects sync-templates <templates> <templates-dist>
    import sys

    if len(sys.argv) == 4 and sys.argv[1] == "sync-templates":
        res = sync_builtin_templates(Path(sys.argv[2]), Path(sys.argv[3]))
        print(f"Шаблоны из образа: обновлено {len(res['copied'])}, "
              f"добавлено {len(res['added'])}, без изменений "
              f"{len(res['unchanged'])}")
        for rel in res["copied"]:
            print(f"  обновлён: {rel}")
        for rel in res["added"]:
            print(f"  добавлен: {rel}")
    else:
        print("Использование: python3 -m core.projects sync-templates "
              "<templates> <templates-dist>", file=sys.stderr)
        raise SystemExit(2)

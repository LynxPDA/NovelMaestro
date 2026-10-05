#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
search.py — поиск по текстам проекта.

Книга — сотни файлов по несколько килобайт: обычный проход по ним занимает
доли секунды, а FTS5-индекс означал бы второй слой (построение, инвалидация,
разъезд с правками) при нулевом выигрыше. Поэтому поиск простой: белый список
групп файлов, NFC, подстрока, фрагменты с контекстом.

Ограничений на число совпадений нет сознательно: лимит означал бы «показаны не
не все», а лишние совпадения — килобайты JSON, не тормоза. Группы (что искать)
задает реестр SEARCH_GROUPS; группы сгруппированы по кластерам (CLUSTERS), а
их подписи совпадают с полями «Тип файлов глав» форм стадий. Глоссарий здесь
не ищется: у него своя вкладка со своим поиском. Индексов и кешей нет:
результат всегда соответствует файлу.
"""
from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .common import parse_chapter_id, read_text_safe

__all__ = [
    "SearchGroup", "SEARCH_GROUPS", "GROUP_IDS", "GROUP_LABELS",
    "CLUSTERS", "CLUSTER_LABELS", "DEFAULT_SCOPES", "MAX_CONTEXT",
    "iter_project_files", "find_in_text", "search_project",
]

# СИМВОЛЫ: сколько знаков берём до и после совпадения
DEFAULT_CONTEXT = 60
# верхняя граница контекста: больше — это уже «показать файл целиком»
MAX_CONTEXT = 300
# файл больше этого размера (БАЙТЫ) не считается рабочим текстовым артефактом
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024
# текстовые расширения групповых обходов (каталоги)
TEXT_EXT = (".txt", ".md", ".json", ".log")

# кластеры групп: порядок обхода и подписи блоков в интерфейсе
CLUSTERS: tuple = (("chapters", "Файлы глав"), ("other", "Прочее"))
CLUSTER_LABELS: dict = dict(CLUSTERS)


@dataclass(frozen=True)
class SearchGroup:
    """Одна группа поиска: подпись, кластер и что именно берём.

    kind="chapter" — файл артефакта во каждой папке главы (pattern — имя
    файла); kind="file" — конкретные файлы проекта; kind="tree" — обход
    каталога (pattern — сам каталог). Подписи глав-артефактов совпадают с
    полем «Тип файлов глав» (core/settings.py)."""

    id: str
    label: str
    kind: str
    pattern: tuple
    cluster: str = "other"


SEARCH_GROUPS: tuple = (
    SearchGroup("chapter", "chapter", "chapter", ("chapter.txt",), "chapters"),
    SearchGroup("translated", "translated", "chapter", ("translated.txt",),
                "chapters"),
    SearchGroup("redacted", "redacted", "chapter", ("redacted.txt",),
                "chapters"),
    SearchGroup("polished", "polished", "chapter", ("polished.txt",),
                "chapters"),
    SearchGroup("notes", "Заметки книги", "file",
                ("notes.md", "source/info.md")),
    SearchGroup("prompts", "Промпты", "tree", ("prompts",)),
    SearchGroup("reports", "Отчёты проверок", "tree", ("tmp",)),
    SearchGroup("logs", "Логи", "tree", ("logs",)),
)

GROUP_IDS: tuple = tuple(g.id for g in SEARCH_GROUPS)
GROUP_LABELS: dict = {g.id: g.label for g in SEARCH_GROUPS}
# что ищется, если пользователь ничего не выбрал: тексты глав + заметки
DEFAULT_SCOPES: tuple = ("chapter", "polished", "notes")


def iter_project_files(project_dir, scopes=None, *,
                       max_file_bytes: int = DEFAULT_MAX_FILE_BYTES):
    """Файлы проекта по группам: (ключ группы, относительный путь, Path).

    Порядок — порядок реестра: сначала все файлы одной группы, внутри глав —
    по номеру главы, каталоги — обходом вниз по именам. Пустые и oversized
    файлы пропускаются: поиск по тексту, а не по метаданным."""
    root = Path(project_dir)
    ids = tuple(scopes) if scopes else DEFAULT_SCOPES
    wanted = [g for g in SEARCH_GROUPS if g.id in ids]
    for group in wanted:
        if group.kind == "chapter":
            base = root / "chapters"
            if not base.is_dir():
                continue
            name = group.pattern[0]
            dirs = [d for d in base.iterdir() if d.is_dir()]
            dirs.sort(key=lambda d: (parse_chapter_id(d.name) or 10 ** 9,
                                     d.name))
            for d in dirs:
                f = d / name
                if _readable_text(f, max_file_bytes):
                    yield group.id, f.relative_to(root).as_posix(), f
        elif group.kind == "file":
            for rel in group.pattern:
                f = root / rel
                if _readable_text(f, max_file_bytes):
                    yield group.id, rel, f
        else:
            base = root / group.pattern[0]
            if not base.is_dir():
                continue
            for dirpath, dirs, files in os.walk(base, followlinks=False):
                # порядок обхода — по имени: результат не зависит от fs
                dirs.sort()
                for fn in sorted(files):
                    f = Path(dirpath) / fn
                    if (f.suffix.lower() in TEXT_EXT
                            and _readable_text(f, max_file_bytes)):
                        yield (group.id, f.relative_to(root).as_posix(), f)


def _readable_text(path: Path, max_file_bytes: int) -> bool:
    """Обычный текстовый файл разумного размера (ошибки stat — не наш случай)."""
    try:
        st = path.stat()
    except OSError:
        return False
    return path.is_file() and 0 < st.st_size <= max_file_bytes


def find_in_text(text, query, *, context: int = DEFAULT_CONTEXT,
                 case_sensitive: bool = False):
    """Все совпадения подстроки в тексте: [{line, start, end, text}].

    line — номер строки с 1; text — строка, обрезанная до ±context символов
    (обрезок помечен «…»); start/end — границы совпадения внутри этого
    фрагмента, то есть text[start:end] — само совпадение. Лимита нет: часть
    совпадений означала бы «показаны не все». И иголка, и текст — NFC."""
    needle = unicodedata.normalize("NFC", str(query or ""))
    if not needle:
        return []
    try:
        ctx = min(MAX_CONTEXT, max(0, int(context)))
    except (TypeError, ValueError):
        ctx = DEFAULT_CONTEXT
    hay_needle = needle if case_sensitive else needle.casefold()
    hits = []
    for lineno, raw in enumerate(
            unicodedata.normalize("NFC", str(text or "")).splitlines(), 1):
        line = raw if case_sensitive else raw.casefold()
        pos = line.find(hay_needle)
        while pos >= 0:
            a = max(0, pos - ctx)
            b = min(len(raw), pos + len(needle) + ctx)
            pre = "…" if a else ""
            frag = pre + raw[a:b] + ("…" if b < len(raw) else "")
            hits.append({"line": lineno, "start": len(pre) + pos - a,
                         "end": len(pre) + pos - a + len(needle),
                         "text": frag})
            pos = line.find(hay_needle, pos + len(needle))
    return hits


def search_project(project_dir, query, scopes=None, *,
                   context: int = DEFAULT_CONTEXT,
                   case_sensitive: bool = False) -> dict:
    """Поиск по текстам книги: все совпадения, без лимитов.

    Возвращает {query, scopes, groups, clusters, labels, files, total,
    scanned, skipped}: files — [{group, path, name, chapter, count, hits}]
    только с совпадениями (порядок — порядок реестра), scanned — сколько
    файлов прочитано, skipped — сколько не прочиталось."""
    needle = unicodedata.normalize("NFC", str(query or "")).strip()
    wanted = tuple(scopes) if scopes else DEFAULT_SCOPES
    groups = [g for g in SEARCH_GROUPS if g.id in wanted]
    # метаданные для интерфейса: весь реестр групп (с кластерами), сами
    # кластеры и какие группы реально участвовали в прогоне
    meta = {"groups": [[g.id, g.label, g.cluster] for g in SEARCH_GROUPS],
            "clusters": list(CLUSTERS),
            "scopes": [g.id for g in groups]}
    if not needle:
        return {"query": "", "files": [], "total": 0, "scanned": 0,
                "skipped": 0, **meta}
    out_files, total, scanned, skipped = [], 0, 0, 0
    for group_id, rel, path in iter_project_files(project_dir, wanted):
        text = read_text_safe(path)
        scanned += 1
        if not text:
            skipped += 1
            continue
        hits = find_in_text(text, needle, context=context,
                            case_sensitive=case_sensitive)
        if not hits:
            continue
        out_files.append({"group": group_id, "path": rel, "name": path.name,
                          "chapter": parse_chapter_id(path.parent.name),
                          "count": len(hits), "hits": hits})
        total += len(hits)
    return {"query": needle, "files": out_files, "total": total,
            "scanned": scanned, "skipped": skipped, **meta}

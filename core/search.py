#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
search.py — поиск по текстам проекта.

Книга — сотни файлов по несколько килобайт: обычный проход по ним занимает
доли секунды, а FTS5-индекс означал бы второй слой (построение, инвалидация,
разъезд с правками) при нулевом выигрыше. Поэтому поиск простой: белый список
групп файлов, NFC, подстрока, фрагменты с контекстом.

Группы (что именно искать) задаются реестром SEARCH_GROUPS — там же подписи
для интерфейса. Индексов и кешей нет: результат всегда соответствует файлу.
"""
from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .common import parse_chapter_id, read_text_safe

__all__ = [
    "SearchGroup", "SEARCH_GROUPS", "GROUP_IDS", "GROUP_LABELS",
    "DEFAULT_SCOPES", "iter_project_files", "find_in_text", "search_project",
]

# СИМВОЛЫ: столько знаков берём до и после совпадения
DEFAULT_CONTEXT = 60
# совпадений на файл и всего за один прогон (остальное — «показаны не все»)
DEFAULT_MAX_PER_FILE = 20
DEFAULT_MAX_TOTAL = 500
# файл больше этого размера (БАЙТЫ) считается не текстовым рабочим артефактом
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024
# текстовые расширения групповых обходов (каталоги)
TEXT_EXT = (".txt", ".md", ".json", ".log")


@dataclass(frozen=True)
class SearchGroup:
    """Одна группа поиска: подпись и что именно берём.

    kind="chapter" — файл артефакта во каждой папке главы (pattern — имя
    файла); kind="file" — конкретные файлы проекта; kind="tree" — обход
    каталога (pattern — сам каталог)."""

    id: str
    label: str
    kind: str
    pattern: tuple


SEARCH_GROUPS: tuple = (
    SearchGroup("chapter", "Оригинал глав", "chapter", ("chapter.txt",)),
    SearchGroup("translated", "Перевод (черновик)", "chapter",
                ("translated.txt",)),
    SearchGroup("redacted", "Правка перевода", "chapter",
                ("redacted.txt",)),
    SearchGroup("polished", "Полировка", "chapter", ("polished.txt",)),
    SearchGroup("ner", "Глоссарий", "file", ("ner.json",)),
    SearchGroup("notes", "Заметки книги", "file",
                ("notes.md", "source/info.md")),
    SearchGroup("prompts", "Промпты", "tree", ("prompts",)),
    SearchGroup("reports", "Отчёты проверок", "tree", ("tmp",)),
    SearchGroup("logs", "Логи", "tree", ("logs",)),
)

GROUP_IDS: tuple = tuple(g.id for g in SEARCH_GROUPS)
GROUP_LABELS: dict = {g.id: g.label for g in SEARCH_GROUPS}
# что ищется, если пользователь ничего не выбрал: тексты книги + глоссарий
DEFAULT_SCOPES: tuple = ("chapter", "polished", "ner", "notes")


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
                 limit: int = 0, case_sensitive: bool = False):
    """Совпадения подстроки в тексте: [{line, start, end, text}].

    line — номер строки с 1; text — строка, обрезанная до ±context символов
    (обрезок помечен «…»); start/end — границы совпадения внутри этого
    фрагмента, то есть text[start:end] — само совпадение.
    И иголка, и текст — NFC."""
    needle = unicodedata.normalize("NFC", str(query or ""))
    if not needle:
        return []
    try:
        ctx = max(0, int(context))
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
            if limit and len(hits) >= limit:
                return hits
            pos = line.find(hay_needle, pos + len(needle))
    return hits


def search_project(project_dir, query, scopes=None, *,
                   context: int = DEFAULT_CONTEXT,
                   max_per_file: int = DEFAULT_MAX_PER_FILE,
                   max_total: int = DEFAULT_MAX_TOTAL,
                   case_sensitive: bool = False) -> dict:
    """Поиск по текстам книги.

    Возвращает {query, scopes, labels, files, total, scanned, skipped,
    truncated}: files — [{group, path, name, chapter, count, hits}] только с
    совпадениями, scanned — сколько файлов прочитано, skipped — сколько
    не прочиталось, truncated — сработал ли лимит совпадений."""
    needle = unicodedata.normalize("NFC", str(query or "")).strip()
    wanted = tuple(scopes) if scopes else DEFAULT_SCOPES
    out_files, total, scanned, skipped, truncated = [], 0, 0, 0, False
    if not needle:
        return {"query": "", "scopes": list(wanted),
                "labels": {g.id: g.label for g in SEARCH_GROUPS
                           if g.id in wanted},
                "files": [], "total": 0, "scanned": 0, "skipped": 0,
                "truncated": False}
    for group_id, rel, path in iter_project_files(project_dir, wanted):
        text = read_text_safe(path)
        scanned += 1
        if not text:
            skipped += 1
            continue
        # лимит файла режется общим лимитом: total не обязан перекосить
        limit = max_per_file
        if max_total:
            limit = min(max_per_file, max_total - total)
        hits = find_in_text(text, needle, context=context,
                            limit=limit, case_sensitive=case_sensitive)
        if not hits:
            continue
        out_files.append({"group": group_id, "path": rel, "name": path.name,
                          "chapter": parse_chapter_id(path.parent.name),
                          "count": len(hits), "hits": hits})
        total += len(hits)
        if max_total and total >= max_total:
            truncated = True
            break
    return {"query": needle, "scopes": list(wanted),
            "labels": {g.id: g.label for g in SEARCH_GROUPS
                       if g.id in wanted},
            "files": out_files, "total": total, "scanned": scanned,
            "skipped": skipped, "truncated": truncated}

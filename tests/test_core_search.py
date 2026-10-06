#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Поиск по текстам проекта (core/search.py): группы, фрагменты, лимиты.

Только синтетические книги во временной папке: сеть и LLM не нужны,
читаем то, что реально лежит в проекте.
Запуск: python3 -m pytest tests/test_core_search.py -q
"""
from __future__ import annotations

import pytest

from core import search as S


def make_book(root, *, chapters=2, artifacts=("chapter", "polished"),
              ner=True, notes=False, prompts=False, logs=False,
              word="мир"):
    """Книга с главами (word — в артефактах), глоссарием и прочими группами."""
    ch = root / "chapters"
    ch.mkdir(parents=True, exist_ok=True)
    for i in range(1, chapters + 1):
        d = ch / f"0000{i}_{i}_Глава {i}"
        d.mkdir(parents=True, exist_ok=True)
        for a in artifacts:
            (d / f"{a}.txt").write_text(
                f"Глава {i}\ntihiy {word} tishе\nvторой абзац\n",
                encoding="utf-8")
    if ner:
        (root / "ner.json").write_text(
            '[{"term": "%s", "translation": "%s", "type": "other"}]'
            % (word, word), encoding="utf-8")
    if notes:
        (root / "notes.md").write_text(f"# Заметки\n{word}\n", encoding="utf-8")
    if prompts:
        (root / "prompts").mkdir(exist_ok=True)
        (root / "prompts" / "translate.txt").write_text(
            f"переведи {word}\n", encoding="utf-8")
    if logs:
        (root / "logs" / "chapters").mkdir(parents=True, exist_ok=True)
        (root / "logs" / "run.log").write_text(f"{word} в логе\n",
                                               encoding="utf-8")
    return root


# ════════════════════════════════════════════════════════════════════
# find_in_text
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text,query,kw,want", [
    # одно совпадение во всей строке: фрагмент — вся строка, границы в нём
    ("мир", "мир", {}, [(1, 0, 3, "мир")]),
    ("тихий мир", "мир", {}, [(1, 6, 9, "тихий мир")]),
    # регистр по умолчанию не учитывается, фрагмент остаётся исходным
    ("тихий МИР", "мир", {}, [(1, 6, 9, "тихий МИР")]),
    ("тихий мир", "МИР", {"case_sensitive": True}, []),
    ("тихий МИР", "МИР", {"case_sensitive": True}, [(1, 6, 9, "тихий МИР")]),
    # несколько совпадений в строке — отдельный фрагмент на каждое
    ("мир да и мир", "мир", {}, [(1, 0, 3, "мир да и мир"),
                                 (1, 9, 12, "мир да и мир")]),
    # переносы: line — номер строки с 1
    ("раз\nмир\nтри", "мир", {}, [(2, 0, 3, "мир")]),
    # пустой запрос ничего не ищет
    ("мир", "", {}, []),
    ("мир", None, {}, []),
])
def test_find_in_text(text, query, kw, want):
    """Совпадения: номер строки и границы внутри возвращённого фрагмента."""
    got = [(x["line"], x["start"], x["end"], x["text"])
           for x in S.find_in_text(text, query, **kw)]
    assert got == want


@pytest.mark.parametrize("context,want,start,end", [
    # «…» тоже попадает в фрагмент: границы считаются от его начала
    (2, "…й мир т…", 3, 6),
    (4, "…хий мир тиш…", 5, 8),
    (1000, "тихий мир тише", 6, 9),
])
def test_find_in_text_context(context, want, start, end):
    """Контекст (СИМВОЛЫ) режет строку, обрезок помечается «…»."""
    hits = S.find_in_text("тихий мир тише", "мир", context=context)
    assert len(hits) == 1
    assert hits[0]["text"] == want
    # границы указывают на совпадение уже внутри фрагмента
    assert (hits[0]["start"], hits[0]["end"]) == (start, end)
    assert want[start:end] == "мир"


def test_find_in_text_context_zero():
    """Нулевой контекст — только само совпадение (с меткой обрезки)."""
    assert S.find_in_text("тихий мир", "мир", context=0) == [
        {"line": 1, "start": 1, "end": 4, "text": "…мир"}]


def test_find_in_text_returns_all():
    """Совпадения не режутся: из одного файла — все, сколько есть."""
    hits = S.find_in_text("мир 1\nмир 2\nмир 3", "мир")
    assert [x["line"] for x in hits] == [1, 2, 3]


def test_find_in_text_nfc():
    """И текст, и запрос проходят NFC: разложенная «a+\\u0301» находит «á»."""
    assert len(S.find_in_text("в вáлке", "á")) == 1


def test_find_in_text_cjk():
    """CJK ищется так же, как кириллица."""
    hits = S.find_in_text("他的 名字 很好", "名字")
    assert len(hits) == 1 and hits[0]["line"] == 1


# ════════════════════════════════════════════════════════════════════
# iter_project_files
# ════════════════════════════════════════════════════════════════════

def test_iter_chapters_sorted_by_number(tmp_path):
    """Главы идут по номеру, а не по имени папки (10 не раньше 2)."""
    make_book(tmp_path, chapters=3, artifacts=("polished",))
    (tmp_path / "chapters" / "000010_10_Глава 10").mkdir()
    (tmp_path / "chapters" / "000010_10_Глава 10" / "polished.txt").write_text(
        "мир", encoding="utf-8")
    got = [(g, rel) for g, rel, _p in S.iter_project_files(tmp_path,
                                                           ("polished",))]
    assert [rel for _g, rel in got] == [
        "chapters/00001_1_Глава 1/polished.txt",
        "chapters/00002_2_Глава 2/polished.txt",
        "chapters/00003_3_Глава 3/polished.txt",
        "chapters/000010_10_Глава 10/polished.txt",
    ]
    assert {g for g, _ in got} == {"polished"}


def test_iter_group_order(tmp_path):
    """Порядок обхода — порядок реестра: сначала все оригиналы, потом полировка."""
    make_book(tmp_path, chapters=2, artifacts=("chapter", "polished"))
    got = [rel for _g, rel, _p in S.iter_project_files(
        tmp_path, ("chapter", "polished"))]
    assert [rel.rsplit("/", 1)[1] for rel in got] == [
        "chapter.txt", "chapter.txt",
        "polished.txt", "polished.txt",
    ]


def test_iter_scopes_filter(tmp_path):
    """Без scopes — только группы по умолчанию, с scopes — ровно они."""
    make_book(tmp_path, chapters=1, ner=True, notes=True, prompts=True,
              logs=True)
    got = {g for g, _rel, _p in S.iter_project_files(tmp_path)}
    assert got == set(S.DEFAULT_SCOPES)
    got = {g for g, _rel, _p in S.iter_project_files(
        tmp_path, ("prompts", "logs"))}
    assert got == {"prompts", "logs"}


def test_iter_notes_both_files(tmp_path):
    """Заметки книги — notes.md и source/info.md."""
    make_book(tmp_path, chapters=0, ner=False, notes=True)
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "info.md").write_text("о книге", encoding="utf-8")
    got = [rel for _g, rel, _p in S.iter_project_files(tmp_path, ("notes",))]
    assert got == ["notes.md", "source/info.md"]


def test_iter_skips_empty_and_huge(tmp_path):
    """Пустой файл и файл-гигант в обход не попадают."""
    ch = tmp_path / "chapters" / "00000_1_Глава 1"
    ch.mkdir(parents=True)
    (ch / "chapter.txt").write_text("", encoding="utf-8")
    (ch / "polished.txt").write_text("мир", encoding="utf-8")
    got = [rel for _g, rel, _p in S.iter_project_files(
        tmp_path, ("chapter", "polished"), max_file_bytes=16)]
    assert got == ["chapters/00000_1_Глава 1/polished.txt"]


def test_iter_tree_only_text_ext(tmp_path):
    """Каталожные группы берут текстовые расширения и рекурсивны."""
    logs = tmp_path / "logs" / "chapters"
    logs.mkdir(parents=True)
    (tmp_path / "logs" / "run.log").write_text("A", encoding="utf-8")
    (logs / "ch1.log").write_text("B", encoding="utf-8")
    (tmp_path / "logs" / "cover.png").write_bytes(b"\x89PNG")
    got = [rel for _g, rel, _p in S.iter_project_files(tmp_path, ("logs",))]
    assert got == ["logs/run.log", "logs/chapters/ch1.log"]


def test_iter_missing_dirs_ok(tmp_path):
    """Пустой проект — пустой обход, а не ошибка."""
    assert list(S.iter_project_files(tmp_path)) == []


# ════════════════════════════════════════════════════════════════════
# search_project
# ════════════════════════════════════════════════════════════════════

def test_search_project_totals(tmp_path):
    """Файлы идут порядком реестра; total — сумма совпадений по всем."""
    make_book(tmp_path, chapters=2, ner=False, notes=False)
    r = S.search_project(tmp_path, "мир")
    assert r["query"] == "мир"
    assert r["scanned"] == 4            # 2 главы × (chapter + polished)
    assert r["skipped"] == 0
    assert r["total"] == 4
    assert [f["path"] for f in r["files"]] == [
        "chapters/00001_1_Глава 1/chapter.txt",
        "chapters/00002_2_Глава 2/chapter.txt",
        "chapters/00001_1_Глава 1/polished.txt",
        "chapters/00002_2_Глава 2/polished.txt",
    ]
    first = r["files"][0]
    assert first["group"] == "chapter" and first["chapter"] == 1
    assert first["name"] == "chapter.txt" and first["count"] == 1


def test_search_project_glossary_is_plain_file(tmp_path):
    """Глоссарий ищется как обычный текст: ner.json в обходе есть, но клик по
    нему ведёт во вкладку «Глоссарий», а не в редактор."""
    make_book(tmp_path, chapters=1, artifacts=("polished",), ner=True)
    r = S.search_project(tmp_path, "мир")
    assert [f["name"] for f in r["files"]] == ["polished.txt", "ner.json"]
    assert r["files"][0]["chapter"] == 1
    ner = [f for f in r["files"] if f["name"] == "ner.json"][0]
    assert ner["group"] == "ner" and ner["chapter"] is None
    opens = {g[0]: g[3] for g in r["groups"]}
    assert opens["ner"] == "glossary" and opens["polished"] == "editor"


def test_search_project_chapter_number(tmp_path):
    """Для файла главы отдаёт номер главы, для заметок — None."""
    make_book(tmp_path, chapters=1, artifacts=("polished",), ner=False,
              notes=True)
    r = S.search_project(tmp_path, "мир")
    got = {f["name"]: f["chapter"] for f in r["files"]}
    assert got == {"polished.txt": 1, "notes.md": None}


def test_search_project_scoped(tmp_path):
    make_book(tmp_path, chapters=2, prompts=True, logs=True)
    r = S.search_project(tmp_path, "мир", ("prompts",))
    assert r["scanned"] == 1 and r["total"] == 1
    assert r["files"][0]["group"] == "prompts"


def test_search_project_case_flag(tmp_path):
    """По умолчанию регистр не важен; с case_sensitive — точное совпадение."""
    ch = tmp_path / "chapters" / "00000_1_Глава 1"
    ch.mkdir(parents=True)
    (ch / "chapter.txt").write_text("МИР\nмир\n", encoding="utf-8")
    assert S.search_project(tmp_path, "мир", ("chapter",))["total"] == 2
    r = S.search_project(tmp_path, "МИР", ("chapter",), case_sensitive=True)
    assert r["total"] == 1 and r["files"][0]["hits"][0]["line"] == 1


def test_search_project_no_limits(tmp_path):
    """Ни лимита на файл, ни лимита всего: показаны все совпадения."""
    ch = tmp_path / "chapters" / "00000_1_Глава 1"
    ch.mkdir(parents=True)
    (ch / "chapter.txt").write_text("мир\nмир\nмир\n", encoding="utf-8")
    r = S.search_project(tmp_path, "мир", ("chapter",))
    assert r["total"] == 3 and r["files"][0]["count"] == 3
    assert not any(k in r for k in ("truncated", "shown", "per_file"))


@pytest.mark.parametrize("query", ["", "   ", None])
def test_search_project_empty_query(tmp_path, query):
    """Пустой запрос — только реестр групп, без чтения файлов."""
    make_book(tmp_path, chapters=1)
    r = S.search_project(tmp_path, query)
    assert r["total"] == 0 and r["files"] == []
    assert r["scanned"] == 0 and r["skipped"] == 0
    assert r["scopes"] == list(S.DEFAULT_SCOPES)
    # реестр для интерфейса: все группы (с кластером) и сами кластеры
    assert [g[0] for g in r["groups"]] == list(S.GROUP_IDS)
    assert [list(c) for c in r["clusters"]] == [list(c) for c in S.CLUSTERS]


def test_registry_shape():
    """Реестр групп: ключи уникальны, метки глав — слаги стадий, дефолты — из реестра."""
    assert len(set(S.GROUP_IDS)) == len(S.GROUP_IDS)
    assert all(g.label and g.pattern for g in S.SEARCH_GROUPS)
    assert set(S.DEFAULT_SCOPES) <= set(S.GROUP_IDS)
    assert set(S.GROUP_LABELS) == set(S.GROUP_IDS)
    assert S.TEXT_EXT and all(e.startswith(".") for e in S.TEXT_EXT)
    chapter_groups = [g for g in S.SEARCH_GROUPS if g.kind == "chapter"]
    assert [g.id for g in chapter_groups] == ["chapter", "translated",
                                              "redacted", "polished"]
    assert [g.label for g in chapter_groups] == [g.id for g in chapter_groups], \
        "метки файлов глав совпадают с полем «Тип файлов глав» форм стадий"
    assert {g.cluster for g in chapter_groups} == {"chapters"}
    # у каждой группы есть кластер, кластеры описаны и не пустуют
    assert {g.cluster for g in S.SEARCH_GROUPS} == set(S.CLUSTER_LABELS)
    assert len(S.CLUSTERS) == 2

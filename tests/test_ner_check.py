#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ner_check: функции core (фильтр/батчи/патчи/review-файл) + e2e
скрипта с мокнутой LLM: двухэтапное накопление, применение со
статусами, авто-режим. Без сети."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from core.common import (  # noqa: E402
    apply_ner_patches, build_ner_batches, filter_ner_items,
    format_ner_record, glossary_body, merge_review_entries,
    parse_ner_patches, parse_review_doc, review_entry,
)
import ner_check as NC  # noqa: E402

ITEMS = [
    {"term": "林凡", "type": "Person (male)", "translation": "Линь Фан",
     "context": "герой", "count": 5},
    {"term": "青云宗", "type": "Location", "translation": "Секта Цинъюнь",
     "context": "секта", "count": 40, "notes": "палладия"},
    {"term": "火球术", "type": "Skill", "translation": "Огненный шар",
     "context": "навык", "count": 12},
]


# ──────────────────────────────────────────────────────────────────────
# core-функции
# ──────────────────────────────────────────────────────────────────────
def test_filter_ner_items():
    out = filter_ner_items(ITEMS, count_threshold=4)
    assert [i["term"] for i in out] == ["林凡", "青云宗", "火球术"]
    out = filter_ner_items(ITEMS, count_threshold=10)
    assert [i["term"] for i in out] == ["青云宗", "火球术"]
    out = filter_ner_items(ITEMS, types=["Location"])
    assert [i["term"] for i in out] == ["青云宗"]
    out = filter_ner_items(ITEMS, exclude_words=["палладия"])
    assert [i["term"] for i in out] == ["林凡", "火球术"]


def test_format_and_glossary_body():
    lines = format_ner_record(ITEMS[0], 1)
    assert lines[0] == "--- Запись 1 ---"
    assert "term: 林凡" in lines and "context: герой" in lines
    assert not any(l.startswith("notes:") for l in lines)
    body = glossary_body(ITEMS[:2])
    assert "--- Запись 1 ---" in body and "--- Запись 2 ---" in body


def test_build_ner_batches_sorted_by_count_desc():
    batches = build_ner_batches(ITEMS, budget=100000)
    assert len(batches) == 1
    assert [i["count"] for i in batches[0]] == [40, 12, 5]


def test_build_ner_batches_split_by_budget():
    batches = build_ner_batches(ITEMS, budget=200)
    assert len(batches) > 1
    # самые частотные — в первом батче
    assert batches[0][0]["count"] == 40
    flat = [i["term"] for b in batches for i in b]
    assert sorted(flat) == sorted(i["term"] for i in ITEMS)


def test_parse_ner_patches_variants():
    ok = '[{"term": "A", "field": "translation", "old": "а", "new": "б"}]'
    assert parse_ner_patches(ok) is not None
    # код-забор и мусор вокруг
    p = parse_ner_patches("вот:\n```json\n" + ok + "\n```\nконец")
    assert p and p[0]["term"] == "A"
    # неверное поле / мусорные элементы отсеиваются
    bad = '[{"term": "A", "field": "context", "old": "x", "new": "y"}, 5]'
    assert parse_ner_patches(bad) == []
    assert parse_ner_patches("просто текст") is None
    assert parse_ner_patches("") is None
    assert parse_ner_patches("[]") == []


def test_review_entry():
    # legacy-патч → запись со статусом по умолчанию
    e = review_entry({"term": "A", "field": "translation", "old": "а",
                      "new": "б", "reason": "r"}, stage="Весь глоссарий")
    assert e["этап"] == "Весь глоссарий" and e["статус"] == "принять"
    assert e["применено"] is False and e["причина"] == "r"
    # некорректные записи отсеиваются
    assert review_entry({"term": "A", "field": "context",
                         "old": "", "new": ""}) is None
    assert review_entry({"term": "", "field": "translation"}) is None
    assert review_entry(5) is None
    # полная запись: регистр/пробелы статуса, field в lower
    e2 = review_entry({"term": "A", "field": "Type", "old": "x", "new": "y",
                       "статус": " Отклонить ", "этап": "Тип: Skill"})
    assert e2["field"] == "type" and e2["статус"] == "отклонить"
    assert e2["этап"] == "Тип: Skill"
    # неизвестный статус → принять
    e3 = review_entry({"term": "A", "field": "notes", "old": "x",
                       "new": "y", "статус": "непонятно"})
    assert e3["статус"] == "принять"


def test_parse_review_doc_and_merge():
    doc = {"создан": "…", "правки": [
        {"term": "A", "field": "translation", "old": "а", "new": "б",
         "статус": "отклонить"}]}
    entries = parse_review_doc(doc)
    assert len(entries) == 1 and entries[0]["статус"] == "отклонить"
    # legacy-массив тоже понимается
    assert parse_review_doc([{"term": "A", "field": "translation",
                              "old": "а", "new": "б"}])
    assert parse_review_doc({"foo": 1}) is None
    assert parse_review_doc("мусор") is None
    # слияние: дубль (term,field,old,new) не добавляется повторно,
    # статус существующей записи сохраняется
    fresh = [review_entry({"term": "A", "field": "translation",
                           "old": "а", "new": "б"}, stage="Тип: Skill"),
             review_entry({"term": "B", "field": "type",
                           "old": "x", "new": "y"}, stage="Тип: Skill")]
    merged, added = merge_review_entries(entries, fresh)
    assert added == 1 and len(merged) == 2
    assert merged[0]["статус"] == "отклонить"
    assert merged[0]["этап"] == ""          # первый этап не перезаписан


def test_apply_ner_patches():
    items = [dict(ITEMS[0]), dict(ITEMS[2])]
    patches = [
        {"term": "林凡", "field": "translation", "old": "Линь Фан",
         "new": "Лин Фань", "reason": "r"},
        {"term": "林凡", "field": "translation", "old": "Линь Фан",
         "new": "дубль", "reason": "r"},          # old уже не совпал
        {"term": "火球术", "field": "type", "old": "Skill",
         "new": "Technique", "reason": ""},
        {"term": "Неттакого", "field": "translation", "old": "x",
         "new": "y", "reason": ""},               # термин не найден
        {"term": "火球术", "field": "translation", "old": "НЕ СОВПАДАЕТ",
         "new": "y", "reason": ""},               # old != текущее
    ]
    applied, skipped = apply_ner_patches(items, patches)
    assert len(applied) == 2 and skipped == 3
    assert items[0]["translation"] == "Лин Фань"
    assert items[1]["type"] == "Technique"


def test_apply_ner_patches_statuses_and_duplicates():
    # дубль термина: правки ложатся на ОБЕ записи (по совпавшему old)
    items = [
        {"term": "玄", "type": "Skill", "translation": "мрак"},
        {"term": "玄", "type": "Skill", "translation": "тайна"},
    ]
    entries = [
        {"этап": "e", "term": "玄", "field": "translation", "old": "мрак",
         "new": "тьма", "причина": "", "статус": "принять",
         "применено": False},
        {"этап": "e", "term": "玄", "field": "translation", "old": "тайна",
         "new": "тьма", "причина": "", "статус": "принять",
         "применено": False},
        {"этап": "e", "term": "玄", "field": "notes", "old": "",
         "new": "x", "причина": "", "статус": "отклонить",
         "применено": False},                     # отклонено человеком
        {"этап": "e", "term": "玄", "field": "type", "old": "Skill",
         "new": "X", "причина": "", "статус": "принять",
         "применено": True},                      # уже применено
    ]
    applied, skipped = apply_ner_patches(items, entries)
    assert len(applied) == 2 and skipped == 2
    assert items[0]["translation"] == "тьма"
    assert items[1]["translation"] == "тьма"
    assert "notes" not in items[0]               # отклонённое не тронуто
    assert items[0]["type"] == "Skill"           # применённое не тронуто
    assert entries[0]["применено"] is True
    assert "дата применения" in entries[0]
    assert entries[2]["применено"] is False


# ──────────────────────────────────────────────────────────────────────
# e2e скрипта (LLM мок)
# ──────────────────────────────────────────────────────────────────────
def _write_ner(tmp_path):
    (tmp_path / "ner.json").write_text(
        json.dumps(ITEMS, ensure_ascii=False), encoding="utf-8")


def _mock_stream(monkeypatch, response, calls):
    def fake(base_url, model, messages, **kw):
        calls.append(messages[0]["content"])
        return response, None
    monkeypatch.setattr(NC, "stream_chat_completion", fake)


def test_ner_check_main_report_and_review(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    calls = []
    resp = ('```json\n[{"term": "林凡", "field": "translation", '
            '"old": "Линь Фан", "new": "Лин Фань", "reason": "pinyin"}]\n```')
    _mock_stream(monkeypatch, resp, calls)
    rc = NC.main(["--input", "ner.json", "--passes", "all",
                  "--exclude-words", "",
                  "--host", "http://x", "--model", "m"])
    assert rc == 0
    # 1 проход весь список + 3 типа
    assert len(calls) == 4
    # в первом запросе (весь список) записи идут по count по убыванию
    first = calls[0]
    assert first.index("青云宗") < first.index("火球术") < first.index("林凡")
    # префикс «глоссарий уже проверен» — только в типовых проходах
    prefix = NC.TYPES_STAGE_PREFIX.strip()
    assert prefix not in calls[0]
    assert all(prefix in c for c in calls[1:])
    # параметры прогона сохранены в meta review-файла
    # правки — в накопительном ner_review.json (дедуп между проходами)
    doc = json.loads((tmp_path / "ner_review.json")
                     .read_text(encoding="utf-8"))
    assert len(doc["правки"]) == 1
    e = doc["правки"][0]
    assert e["этап"] == "Весь глоссарий"
    assert e["статус"] == "принять" and e["применено"] is False
    assert not (tmp_path / "ner_patches.json").exists()
    params = doc["параметры"]
    assert params["бюджет батча"] == 196608
    assert params["исключения notes"] == ""
    report = (tmp_path / "ner_report.md").read_text(encoding="utf-8")
    assert "Весь глоссарий" in report and "Тип: Skill" in report
    assert "ner_review.json" in report


def test_ner_check_two_stage_accumulation(tmp_path, monkeypatch):
    """Этап 2 дописывает правки в тот же файл; решения человека живут."""
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    resp_whole = ('[{"term": "林凡", "field": "translation", '
                  '"old": "Линь Фан", "new": "Лин Фань", "reason": "p"}]')
    resp_skill = ('[{"term": "林凡", "field": "translation", '
                  '"old": "Линь Фан", "new": "Лин Фань", "reason": "p"},'
                  '{"term": "火球术", "field": "translation", '
                  '"old": "Огненный шар", "new": "Шар огня", '
                  '"reason": "p2"}]')

    calls = []

    def fake(base_url, model, messages, **kw):
        calls.append(messages[0]["content"])
        # этап 2, проход Skill (3-й запрос): повтор старой правки + новая
        if len(calls) >= 3:
            return resp_skill, None
        return resp_whole, None
    monkeypatch.setattr(NC, "stream_chat_completion", fake)

    # этап 1: весь список
    rc = NC.main(["--input", "ner.json", "--passes", "whole",
                  "--exclude-words", "", "--host", "http://x",
                  "--model", "m"])
    assert rc == 0
    # человек отклонил правку
    doc = json.loads((tmp_path / "ner_review.json")
                     .read_text(encoding="utf-8"))
    doc["правки"][0]["статус"] = "отклонить"
    (tmp_path / "ner_review.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    # этап 2: по типам; LLM повторяет старую правку + даёт новую
    rc = NC.main(["--input", "ner.json", "--passes", "types",
                  "--types", "Person (male),Skill",
                  "--exclude-words", "", "--host", "http://x",
                  "--model", "m"])
    assert rc == 0
    doc = json.loads((tmp_path / "ner_review.json")
                     .read_text(encoding="utf-8"))
    entries = doc["правки"]
    assert len(entries) == 2
    assert entries[0]["статус"] == "отклонить"       # решение человека
    assert entries[0]["этап"] == "Весь глоссарий"    # этап не перезаписан
    assert entries[1]["этап"] == "Тип: Skill"
    assert entries[1]["статус"] == "принять"


def test_ner_check_apply_dry_run_and_real(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    doc = {"создан": "t", "вход": "ner.json",
           "параметры": {"бюджет батча": 12345},
           "правки": [
        {"этап": "Весь глоссарий", "term": "林凡", "field": "translation",
         "old": "Линь Фан", "new": "Лин Фань", "причина": "r",
         "статус": "принять", "применено": False},
        {"этап": "Тип: Skill", "term": "林凡", "field": "notes",
         "old": "nope", "new": "x", "причина": "r",
         "статус": "принять", "применено": False},   # old не совпал
        {"этап": "Тип: Skill", "term": "火球术", "field": "translation",
         "old": "Огненный шар", "new": "не должно", "причина": "r",
         "статус": "отклонить", "применено": False},  # отклонено человеком
    ]}
    (tmp_path / "ner_review.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    # dry-run: файлы не меняются
    rc = NC.main(["--apply", "--dry-run", "--input", "ner.json"])
    assert rc == 0
    data = json.loads((tmp_path / "ner.json").read_text(encoding="utf-8"))
    assert data[0]["translation"] == "Линь Фан"
    assert not (tmp_path / "ner.json.bak").exists()
    # реальное применение: бэкап + правка + лог с этапами
    rc = NC.main(["--apply", "--input", "ner.json"])
    assert rc == 0
    data = json.loads((tmp_path / "ner.json").read_text(encoding="utf-8"))
    assert data[0]["translation"] == "Лин Фань"
    assert data[2]["translation"] == "Огненный шар"   # отклонённое цело
    assert (tmp_path / "ner.json.bak").exists()
    changes = (tmp_path / "ner_changes.md").read_text(encoding="utf-8")
    assert "Лин Фань" in changes and "Весь глоссарий" in changes
    assert "Применено всего (все этапы): 1" in changes
    # флаги «применено» сохранены в файл правок
    doc2 = json.loads((tmp_path / "ner_review.json")
                      .read_text(encoding="utf-8"))
    assert doc2["правки"][0]["применено"] is True
    assert "дата применения" in doc2["правки"][0]
    assert doc2["правки"][1]["применено"] is False
    # параметры прошлого прогона пережили применение
    assert doc2["параметры"] == {"бюджет батча": 12345}
    # повторный apply: всё уже применено — нер.json не трогается
    rc = NC.main(["--apply", "--input", "ner.json"])
    assert rc == 0


def test_ner_check_apply_legacy_patches_array(tmp_path, monkeypatch):
    """Старый формат (простой массив патчей) понимается как «принять»."""
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    (tmp_path / "old_patches.json").write_text(json.dumps(
        [{"term": "林凡", "field": "translation", "old": "Линь Фан",
          "new": "Лин Фань", "reason": "r"}], ensure_ascii=False),
        encoding="utf-8")
    rc = NC.main(["--apply", "--review", "old_patches.json",
                  "--input", "ner.json"])
    assert rc == 0
    data = json.loads((tmp_path / "ner.json").read_text(encoding="utf-8"))
    assert data[0]["translation"] == "Лин Фань"
    assert "Лин Фань" in (tmp_path / "ner_changes.md") \
        .read_text(encoding="utf-8")


def test_ner_check_auto_apply_whole(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    resp = ('[{"term": "林凡", "field": "translation", '
            '"old": "Линь Фан", "new": "Лин Фань", "reason": "p"}]')
    calls = []
    _mock_stream(monkeypatch, resp, calls)
    rc = NC.main(["--input", "ner.json", "--passes", "whole",
                  "--exclude-words", "", "--host", "http://x",
                  "--model", "m", "--auto-apply"])
    assert rc == 0
    data = json.loads((tmp_path / "ner.json").read_text(encoding="utf-8"))
    assert data[0]["translation"] == "Лин Фань"
    assert (tmp_path / "ner.json.bak").exists()
    doc = json.loads((tmp_path / "ner_review.json")
                     .read_text(encoding="utf-8"))
    assert doc["правки"][0]["применено"] is True
    changes = (tmp_path / "ner_changes.md").read_text(encoding="utf-8")
    assert "Лин Фань" in changes and "Весь глоссарий" in changes


def test_ner_check_auto_apply_all_sequential(tmp_path, monkeypatch):
    """--passes all --auto-apply: типы видят данные ПОСЛЕ этапа 1."""
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    calls = []

    def fake(base_url, model, messages, **kw):
        calls.append(messages[0]["content"])
        if len(calls) == 1:      # этап 1 (весь список): правим имя
            return ('[{"term": "林凡", "field": "translation", '
                    '"old": "Линь Фан", "new": "Лин Фань", '
                    '"reason": "p"}]'), None
        return "[]", None

    monkeypatch.setattr(NC, "stream_chat_completion", fake)
    rc = NC.main(["--input", "ner.json", "--passes", "all",
                  "--exclude-words", "", "--host", "http://x",
                  "--model", "m", "--auto-apply"])
    assert rc == 0
    # 1 проход весь список + 3 типа
    assert len(calls) == 4
    # типовые проходы шли по обновлённому глоссарию
    later = "\n".join(calls[1:])
    assert "Лин Фань" in later and "Линь Фан" not in later
    data = json.loads((tmp_path / "ner.json").read_text(encoding="utf-8"))
    assert data[0]["translation"] == "Лин Фань"
    assert (tmp_path / "ner.json.bak").exists()


def test_ner_check_auto_apply_fail_fast(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    monkeypatch.setattr(NC, "stream_chat_completion",
                        lambda *a, **kw: (None, "timeout"))
    with pytest.raises(SystemExit):
        NC.main(["--input", "ner.json", "--passes", "whole",
                 "--exclude-words", "", "--host", "http://x",
                 "--model", "m", "--auto-apply"])
    # нер.json не тронут, бэкапа нет
    data = json.loads((tmp_path / "ner.json").read_text(encoding="utf-8"))
    assert data[0]["translation"] == "Линь Фан"
    assert not (tmp_path / "ner.json.bak").exists()


def test_ner_check_llm_error_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)

    def fake(base_url, model, messages, **kw):
        return None, "timeout"

    monkeypatch.setattr(NC, "stream_chat_completion", fake)
    rc = NC.main(["--input", "ner.json", "--passes", "whole",
                  "--host", "http://x", "--model", "m"])
    assert rc == 0
    doc = json.loads((tmp_path / "ner_review.json")
                     .read_text(encoding="utf-8"))
    assert doc["правки"] == []
    report = (tmp_path / "ner_report.md").read_text(encoding="utf-8")
    assert "Ошибка LLM" in report


def test_ner_check_unparsed_raw_saved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    calls = []
    _mock_stream(monkeypatch, "мусор без JSON", calls)
    rc = NC.main(["--input", "ner.json", "--passes", "whole",
                  "--host", "http://x", "--model", "m"])
    assert rc == 0
    report = (tmp_path / "ner_report.md").read_text(encoding="utf-8")
    assert "СЫРЬЁ" in report and "мусор без JSON" in report

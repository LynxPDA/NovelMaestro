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
sys.path.insert(0, str(ROOT / "cli"))

from core.common import (  # noqa: E402
    apply_ner_patches, build_ner_batches, filter_ner_items,
    format_ner_record, glossary_body, merge_review_entries,
    parse_ner_patches, parse_review_doc, review_entry,
)
import ner_check as NC  # noqa: E402
from conftest import SilentLog  # noqa: E402

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
    # exclude_words убран — все записи по порогу/типам остаются
    out = filter_ner_items(ITEMS, count_threshold=4)
    assert len(out) == 3


def test_format_and_glossary_body_fields():
    # по умолчанию — все поля (кроме служебных с «_»); пустые пропущены
    lines = format_ner_record(ITEMS[0], 1)
    assert lines[0] == "--- Запись 1 ---"
    assert "term: 林凡" in lines and "context: герой" in lines
    assert not any(l.startswith("notes:") for l in lines)
    # fields — только выбранные; term всегда; count не показывается
    lines = format_ner_record(ITEMS[0], 1, fields=["term", "type"])
    assert any(l.startswith("type:") for l in lines)
    assert not any(l.startswith("translation:") for l in lines)
    assert not any(l.startswith("context:") for l in lines)
    assert not any(l.startswith("count:") for l in lines)
    # aliases — только когда выбраны; голоса больше не выводятся
    item = dict(ITEMS[1], aliases=["Цинъюнь"],
                _votes_translation={"а": 2, "б": 1})
    body = glossary_body([item], fields=["term", "aliases"])
    assert "aliases: Цинъюнь" in body
    assert "_votes" not in body
    body_all = glossary_body(ITEMS[:2])
    assert "--- Запись 1 ---" in body_all and "--- Запись 2 ---" in body_all
    # новое поле записи (неизвестный ключ ner.json) — рендерится после
    # известных; в выбранный набор — только когда явно выбрано
    item = dict(ITEMS[0], gender_history="устойчиво",
                extra_field=["а", "б"])
    lines = format_ner_record(item, 1)
    assert any(l.startswith("gender_history: ") for l in lines)
    assert 'extra_field: ["а", "б"]' in lines
    lines_sel = format_ner_record(item, 1, fields=["term", "type"])
    assert not any(l.startswith("gender_history") for l in lines_sel)
    assert not any(l.startswith("extra_field") for l in lines_sel)


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
    assert e is not None
    assert e["stage"] == "Весь глоссарий" and e["status"] == "принять"
    assert e["applied"] is False and e["reason"] == "r"
    # некорректные записи отсеиваются
    assert review_entry({"term": "A", "field": "context",
                         "old": "", "new": ""}) is None
    assert review_entry({"term": "", "field": "translation"}) is None
    assert review_entry(5) is None
    # полная запись: регистр/пробелы статуса, field в lower
    e2 = review_entry({"term": "A", "field": "Type", "old": "x", "new": "y",
                       "status": " Отклонить ", "stage": "Тип: Skill"})
    assert e2 is not None
    assert e2["field"] == "type" and e2["status"] == "отклонить"
    assert e2["stage"] == "Тип: Skill"
    # неизвестный статус → принять
    e3 = review_entry({"term": "A", "field": "notes", "old": "x",
                       "new": "y", "status": "непонятно"})
    assert e3 is not None
    assert e3["status"] == "принять"


def test_parse_review_doc_and_merge():
    doc = {"created": "…", "entries": [
        {"term": "A", "field": "translation", "old": "а", "new": "б",
         "status": "отклонить"}]}
    entries = parse_review_doc(doc)
    assert entries is not None
    assert len(entries) == 1 and entries[0]["status"] == "отклонить"
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
    assert merged[0]["status"] == "отклонить"
    assert merged[0]["stage"] == ""          # первый этап не перезаписан


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
        {"stage": "e", "term": "玄", "field": "translation", "old": "мрак",
         "new": "тьма", "reason": "", "status": "принять",
         "applied": False},
        {"stage": "e", "term": "玄", "field": "translation", "old": "тайна",
         "new": "тьма", "reason": "", "status": "принять",
         "applied": False},
        {"stage": "e", "term": "玄", "field": "notes", "old": "",
         "new": "x", "reason": "", "status": "отклонить",
         "applied": False},                     # отклонено человеком
        {"stage": "e", "term": "玄", "field": "type", "old": "Skill",
         "new": "X", "reason": "", "status": "принять",
         "applied": True},                      # уже применено
    ]
    applied, skipped = apply_ner_patches(items, entries)
    assert len(applied) == 2 and skipped == 2
    assert items[0]["translation"] == "тьма"
    assert items[1]["translation"] == "тьма"
    assert "notes" not in items[0]               # отклонённое не тронуто
    assert items[0]["type"] == "Skill"           # применённое не тронуто
    assert entries[0]["applied"] is True
    assert "applied_at" in entries[0]
    assert entries[2]["applied"] is False


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
    rc = NC.main(["--input", "ner.json", "--passes", "whole",
                  "--host", "http://x", "--model", "m"])
    assert rc == 0
    # один проход «Весь глоссарий» (типы — отдельным режимом)
    assert len(calls) == 1
    # в запросе записи идут по count по убыванию
    first = calls[0]
    assert first.index("青云宗") < first.index("火球术") < first.index("林凡")
    # префикс «глоссарий уже проверен» — только в типовых проходах
    prefix = NC.TYPES_STAGE_PREFIX.strip()
    assert prefix not in calls[0]
    # параметры прогона сохранены в meta review-файла
    # правки — в накопительном ner_review.json (дедуп между проходами)
    doc = json.loads((tmp_path / "ner_review.json")
                     .read_text(encoding="utf-8"))
    assert len(doc["entries"]) == 1
    e = doc["entries"][0]
    assert e["stage"] == "Весь глоссарий"
    assert e["status"] == "принять" and e["applied"] is False
    assert not (tmp_path / "ner_patches.json").exists()
    params = doc["params"]
    assert params["бюджет батча"] == 196608
    assert params["поля"] == "term,type,translation"
    # отчёт ner_report.md удалён — файла быть не должно
    assert not (tmp_path / "ner_report.md").exists()


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
                   "--model", "m"])
    assert rc == 0
    # человек отклонил правку
    doc = json.loads((tmp_path / "ner_review.json")
                     .read_text(encoding="utf-8"))
    doc["entries"][0]["status"] = "отклонить"
    (tmp_path / "ner_review.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    # этап 2: по типам; LLM повторяет старую правку + даёт новую
    rc = NC.main(["--input", "ner.json", "--passes", "types",
                  "--types", "Person (male),Skill",
                   "--model", "m"])
    assert rc == 0
    doc = json.loads((tmp_path / "ner_review.json")
                     .read_text(encoding="utf-8"))
    entries = doc["entries"]
    assert len(entries) == 2
    assert entries[0]["status"] == "отклонить"       # решение человека
    assert entries[0]["stage"] == "Весь глоссарий"    # этап не перезаписан
    assert entries[1]["stage"] == "Тип: Skill"
    assert entries[1]["status"] == "принять"


def test_ner_check_apply_dry_run_and_real(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    doc = {"created": "t", "input": "ner.json",
           "params": {"бюджет батча": 12345},
           "entries": [
        {"stage": "Весь глоссарий", "term": "林凡", "field": "translation",
         "old": "Линь Фан", "new": "Лин Фань", "reason": "r",
         "status": "принять", "applied": False},
        {"stage": "Тип: Skill", "term": "林凡", "field": "notes",
         "old": "nope", "new": "x", "reason": "r",
         "status": "принять", "applied": False},   # old не совпал
        {"stage": "Тип: Skill", "term": "火球术", "field": "translation",
         "old": "Огненный шар", "new": "не должно", "reason": "r",
         "status": "отклонить", "applied": False},  # отклонено человеком
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
    # отчёт ner_changes.md удалён (не нужен) — файл не создаётся
    assert not (tmp_path / "ner_changes.md").exists()
    # флаги «применено» сохранены в файл правок
    doc2 = json.loads((tmp_path / "ner_review.json")
                      .read_text(encoding="utf-8"))
    assert doc2["entries"][0]["applied"] is True
    assert "applied_at" in doc2["entries"][0]
    assert doc2["entries"][1]["applied"] is False
    # параметры прошлого прогона пережили применение
    assert doc2["params"] == {"бюджет батча": 12345}
    # повторный apply: всё уже применено — нер.json не трогается
    rc = NC.main(["--apply", "--input", "ner.json"])
    assert rc == 0


def test_ner_check_apply_no_bak(tmp_path, monkeypatch):
    """--no-bak: ner.json обновляется, .bak не создаётся."""
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    doc = {"created": "t", "input": "ner.json", "entries": [
        {"stage": "Весь глоссарий", "term": "林凡", "field": "translation",
         "old": "Линь Фан", "new": "Лин Фань", "reason": "r",
         "status": "принять", "applied": False},
    ]}
    (tmp_path / "ner_review.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    rc = NC.main(["--apply", "--no-bak", "--input", "ner.json"])
    assert rc == 0
    data = json.loads((tmp_path / "ner.json").read_text(encoding="utf-8"))
    assert data[0]["translation"] == "Лин Фань"
    assert not (tmp_path / "ner.json.bak").exists()
    # без --no-bak бэкап создаётся (по умолчанию): новая правка
    doc2 = {"created": "t", "input": "ner.json", "entries": [
        {"stage": "Весь глоссарий", "term": "青云宗", "field": "translation",
         "old": "Секта Цинъюнь", "new": "Секта Цинъюнь (гл.)", "reason": "r",
         "status": "принять", "applied": False},
    ]}
    (tmp_path / "ner_review.json").write_text(
        json.dumps(doc2, ensure_ascii=False), encoding="utf-8")
    rc = NC.main(["--apply", "--input", "ner.json"])
    assert rc == 0
    assert (tmp_path / "ner.json.bak").exists()


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
    assert not (tmp_path / "ner_changes.md").exists()


def test_ner_check_auto_apply_whole(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    resp = ('[{"term": "林凡", "field": "translation", '
            '"old": "Линь Фан", "new": "Лин Фань", "reason": "p"}]')
    calls = []
    _mock_stream(monkeypatch, resp, calls)
    rc = NC.main(["--input", "ner.json", "--passes", "whole",
                   "--model", "m", "--auto-apply"])
    assert rc == 0
    data = json.loads((tmp_path / "ner.json").read_text(encoding="utf-8"))
    assert data[0]["translation"] == "Лин Фань"
    assert (tmp_path / "ner.json.bak").exists()
    doc = json.loads((tmp_path / "ner_review.json")
                     .read_text(encoding="utf-8"))
    assert doc["entries"][0]["applied"] is True
    assert not (tmp_path / "ner_changes.md").exists()


def test_ner_check_auto_apply_whole_only(tmp_path, monkeypatch):
    """Режим all убран: --passes whole --auto-apply делает ОДИН проход
    (весь список) и применяет; типовые проходы не запускаются."""
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    calls = []

    def fake(base_url, model, messages, **kw):
        calls.append(messages[0]["content"])
        return ('[{"term": "林凡", "field": "translation", '
                '"old": "Линь Фан", "new": "Лин Фань", '
                '"reason": "p"}]'), None

    monkeypatch.setattr(NC, "stream_chat_completion", fake)
    rc = NC.main(["--input", "ner.json", "--passes", "whole",
                   "--model", "m", "--auto-apply"])
    assert rc == 0
    # только проход «Весь глоссарий» (типы не идут)
    assert len(calls) == 1
    data = json.loads((tmp_path / "ner.json").read_text(encoding="utf-8"))
    assert data[0]["translation"] == "Лин Фань"
    assert (tmp_path / "ner.json.bak").exists()


def test_ner_check_passes_all_rejected(tmp_path, monkeypatch):
    """--passes all удалён из choices — argparse отказывает."""
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    try:
        NC.main(["--input", "ner.json", "--passes", "all"])
        assert False, "all должен быть отклонён"
    except SystemExit:
        pass


def test_ner_check_auto_apply_fail_fast(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    monkeypatch.setattr(NC, "stream_chat_completion",
                        lambda *a, **kw: (None, "timeout"))
    with pytest.raises(SystemExit):
        NC.main(["--input", "ner.json", "--passes", "whole",
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
    assert doc["entries"] == []
    # отчёт удалён: файла быть не должно, скрипт не падает
    assert not (tmp_path / "ner_report.md").exists()


def test_ner_check_unparsed_does_not_crash(tmp_path, monkeypatch):
    """Непарсибельный ответ LLM — без отчёта и без падения."""
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    calls = []
    _mock_stream(monkeypatch, "мусор без JSON", calls)
    rc = NC.main(["--input", "ner.json", "--passes", "whole",
                  "--host", "http://x", "--model", "m"])
    assert rc == 0
    assert not (tmp_path / "ner_report.md").exists()


def test_ner_check_threads_parallel_types(tmp_path, monkeypatch):
    """--threads > 1: типы идут параллельно, все батчи обработаны,
    правки собраны по проходам (детерминированный порядок)."""
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    calls = []
    resp = ('[{"term": "林凡", "field": "translation", '
            '"old": "Линь Фан", "new": "Лин Фань", "reason": "p"}]')
    _mock_stream(monkeypatch, resp, calls)
    rc = NC.main(["--input", "ner.json", "--passes", "types",
                  "--threads", "2", "--host", "http://x",
                  "--model", "m"])
    assert rc == 0
    # три типа → три батча (по одному на тип) — все запрошены
    assert len(calls) == 3
    doc = json.loads((tmp_path / "ner_review.json")
                     .read_text(encoding="utf-8"))
    stages = [e["stage"] for e in doc["entries"]]
    # правки дедуплицируются (одинаковый ответ) — но этап записан
    assert stages and stages == sorted(stages)
    assert stages[0].startswith("Тип: ")


def test_ner_check_threads_overall_progress(tmp_path, monkeypatch):
    """Общий прогресс по ВСЕМ батчам (не текущий чанк):
    emit_progress доходит до total = число батчей всех типов."""
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    calls = []
    _mock_stream(monkeypatch, "[]", calls)
    events = []
    monkeypatch.setattr(NC, "emit_progress",
                        lambda done, total, label="":
                        events.append((done, total)))
    rc = NC.main(["--input", "ner.json", "--passes", "types",
                  "--threads", "2", "--host", "http://x",
                  "--model", "m"])
    assert rc == 0
    assert events and events[-1] == (3, 3)  # 3 типа = 3 батча, общий итог
    assert events[0] == (0, 3)  # старт — сразу общий total


# ──────────────────────────────────────────────────────────────────────
# RAG-режим (--passes rag)
# ──────────────────────────────────────────────────────────────────────
def test_ner_check_rag_builds_block(tmp_path):
    """build_rag_block: термины из ner.json + релевантные фрагменты
    книги равномерно (не с начала), бюджет соблюдается."""
    from core.common import build_fts_index
    text = "\n\n".join([
        "Линь Фан вошёл в зал. " * 20,
        "Секта Цинъюнь расположена в горах. " * 20,
        "Огненный шар вспыхнул в руке. " * 20,
        "Ничего общего с терминами. " * 20,
    ])
    db = build_fts_index(text, 1000)
    items_by_term = {i["term"]: i for i in ITEMS}
    block = NC.build_rag_block(
        ["林凡", "青云宗", "火球术"], items_by_term, db, 2000, SilentLog())
    assert "林凡" in block and "Секта Цинъюнь" in block
    assert "Линь Фан" in block  # фрагмент по переводу
    # бюджет: суммарная длина фрагментов ≤ бюджет × число терминов
    import re as _re
    frag_lines = [l for l in block.splitlines() if l.startswith("  · ")]
    assert len(frag_lines) > 0
    assert sum(len(l) for l in frag_lines) <= 2000 * 3 + 1000


def test_ner_check_rag_main(tmp_path, monkeypatch):
    """e2e: --passes rag с терминами и файлом книги → review-файл
    с патчами stage=RAG (type/translation, old→new); LLM мокается."""
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    (tmp_path / "novel.txt").write_text(
        "Линь Фан вошёл в зал. " * 50, encoding="utf-8")
    calls = []
    resp = ('[{"term": "林凡", "type": "Person (male)", '
            '"translation": "Линь Фан", "reason": "p"}]')
    _mock_stream(monkeypatch, resp, calls)
    rc = NC.main(["--input", "ner.json", "--passes", "rag",
                  "--rag_terms", "林凡\n青云宗\n",
                  "--rag_novel", "novel.txt",
                  "--rag_budget", "1500",
                  "--host", "http://x", "--model", "m"])
    assert rc == 0
    assert len(calls) == 1
    # в запрос попали и термины, и фрагменты книги
    assert "林凡" in calls[0] and "Линь Фан" in calls[0]
    doc = json.loads((tmp_path / "ner_review.json")
                     .read_text(encoding="utf-8"))
    # 林凡 уже Person (male) и Линь Фан — уточнений нет → пусто
    assert doc["entries"] == []


def test_ner_check_rag_patches_differ(tmp_path, monkeypatch):
    """RAG: если LLM предлагает другое значение — появляется патч
    field=type/translation с old→new."""
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    (tmp_path / "novel.txt").write_text(
        "Линь Фан вошёл в зал. " * 50, encoding="utf-8")
    calls = []
    resp = ('[{"term": "林凡", "type": "Person (female)", '
            '"translation": "Линь Фан", "reason": "по фрагменту"}]')
    _mock_stream(monkeypatch, resp, calls)
    rc = NC.main(["--input", "ner.json", "--passes", "rag",
                  "--rag_terms", "林凡",
                  "--rag_novel", "novel.txt",
                  "--host", "http://x", "--model", "m"])
    assert rc == 0
    doc = json.loads((tmp_path / "ner_review.json")
                     .read_text(encoding="utf-8"))
    entries = doc["entries"]
    assert len(entries) == 1
    e = entries[0]
    assert e["stage"] == "RAG" and e["term"] == "林凡"
    assert e["field"] == "type"
    assert e["old"] == "Person (male)" and e["new"] == "Person (female)"


def test_ner_check_rag_missing_files(tmp_path, monkeypatch):
    """RAG: нет файла книги / пустой список терминов — rc=1, без LLM."""
    monkeypatch.chdir(tmp_path)
    _write_ner(tmp_path)
    calls = []
    _mock_stream(monkeypatch, "[]", calls)
    rc = NC.main(["--input", "ner.json", "--passes", "rag",
                  "--rag_terms", "", "--rag_novel", "novel.txt",
                  "--host", "http://x", "--model", "m"])
    assert rc == 1 and not calls
    rc = NC.main(["--input", "ner.json", "--passes", "rag",
                  "--rag_terms", "林凡", "--rag_novel", "нет.txt",
                  "--host", "http://x", "--model", "m"])
    assert rc == 1 and not calls

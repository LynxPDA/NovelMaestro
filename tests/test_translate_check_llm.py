#!/usr/bin/env python3
"""Тесты потока проверки перевода через LLM (translate_check_llm):
core.common (fix_entry, merge_fix_entries, apply_fix_to_text) и
cli/translate_check_llm.py (накопительный
 translate_check_llm_review.json, применение, авто-режим).
Без сети и без интерактива."""
# pyright: reportMissingImports=false
import json
import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cli"))

import core.common as C  # noqa: E402
import translate_check_llm as FE  # noqa: E402  (бывший fix_errors/redact_errors)
from conftest import SilentLog, feed, fake_env  # noqa: E402


# ══════════════════════════════════════════════════════════════════════
# core.common: fix_entry / merge_fix_entries / apply_fix_to_text
# ══════════════════════════════════════════════════════════════════════

def test_fix_entry_normalizes_and_defaults():
    e = C.fix_entry({"chapter": "12", "fragment": "  было ",
                     "corrected": " стало ", "type": "typo"},
                    stage="Главы 1–12 (polished)")
    assert e is not None
    assert e["chapter"] == 12
    assert e["stage"] == "Главы 1–12 (polished)"
    assert e["old"] == "было" and e["new"] == "стало"
    assert e["type"] == "typo" and e["status"] == "принять"
    assert e["applied"] is False


def test_fix_entry_bad_type_cleared_and_invalid_rejected():
    e = C.fix_entry({"chapter": 1, "fragment": "длинный фрагмент",
                     "corrected": "другой фрагмент", "type": "бред"})
    assert e is not None and e["type"] == ""
    assert C.fix_entry({"chapter": "x", "fragment": "а",
                        "corrected": "б"}) is None
    assert C.fix_entry({"chapter": 1, "fragment": "одно и то же",
                        "corrected": "одно и то же"}) is None
    assert C.fix_entry({"chapter": 1, "fragment": "",
                        "corrected": "новое"}) is None


def test_fix_entry_nfc_and_status():
    frag = "унификация"
    e = C.fix_entry({"chapter": 2, "fragment": frag,
                     "corrected": "новое", "status": C.REVIEW_REJECT})
    assert e is not None
    assert e["old"] == unicodedata.normalize("NFC", frag)
    assert e["status"] == C.REVIEW_REJECT


def test_merge_fix_entries_dedup_and_keep_status():
    base = [C.fix_entry({"chapter": 1, "fragment": "старый текст",
                         "corrected": "новый"})]
    assert base[0] is not None
    base[0]["status"] = C.REVIEW_REJECT
    base[0]["applied"] = True
    same = C.fix_entry({"chapter": 1, "fragment": "старый текст",
                        "corrected": "новый"})
    merged, added = C.merge_fix_entries(base, [same], SilentLog())
    assert merged[0] is not None
    assert added == 0 and merged[0]["status"] == C.REVIEW_REJECT
    assert merged[0]["applied"] is True
    another = [C.fix_entry({"chapter": 1, "fragment": "старый текст",
                            "corrected": "иначе"})]
    merged2, added2 = C.merge_fix_entries(merged, another, SilentLog())
    assert added2 == 1 and len(merged2) == 2


def test_apply_fix_to_text():
    txt = "здесь была ошибка и ещё ошибка ниже"
    new, ok = C.apply_fix_to_text(txt, "была ошибка", "была правка")
    assert ok and new == "здесь была правка и ещё ошибка ниже"  # первое
    nfced = unicodedata.normalize("NFC", "ошибка ниже")
    new2, ok2 = C.apply_fix_to_text(txt, nfced, "правка ниже")
    assert new2 is not None
    assert ok2 and "правка ниже" in new2
    _, ok3 = C.apply_fix_to_text(txt, "нет такого", "x")
    assert ok3 is False


# ══════════════════════════════════════════════════════════════════════
# cli/translate_check_llm.py: LLM-обвязка (промпты, запрос, батчи)
# ══════════════════════════════════════════════════════════════════════

def test_load_prompts(tmp_path):
    p = tmp_path / "prompt.txt"
    p.write_text("<pass1>\nП1\n</pass1>\n<pass2>\nП2\n</pass2>", encoding="utf-8")
    p1, p2 = FE.load_prompts(str(p), SilentLog())
    assert (p1, p2) == ("П1", "П2")
    # без тегов — встроенные + предупреждение
    p.write_text("просто текст", encoding="utf-8")
    p1, p2 = FE.load_prompts(str(p), SilentLog())
    assert (p1, p2) == (FE.PASS1_PROMPT, FE.PASS2_PROMPT)
    # нет файла — встроенные
    p1, p2 = FE.load_prompts(None, SilentLog())
    assert (p1, p2) == (FE.PASS1_PROMPT, FE.PASS2_PROMPT)


def test_query_llm_raw_delegates(monkeypatch):
    seen = {}

    def fake_stream(base_url, model, messages, **kw):
        seen.update(kw=kw, n=len(messages))
        return "ОТВЕТ", ""

    monkeypatch.setattr(FE, "stream_chat_completion", fake_stream)
    out = FE.query_llm_raw("ю", "с", "h", "m", "k", 2, 30, 60, None,
                           None, SilentLog(), "[X]")
    assert out == "ОТВЕТ"
    assert seen["n"] == 2 and seen["kw"]["max_tokens"] == 32768


def test_process_batch_one_pass(monkeypatch):
    calls = []

    def fake_q(user, sysp, *a, **k):
        calls.append(sysp)
        return '[{"chapter": 1, "fragment": "фрагмент длинный", "corrected": "правкa"}]'

    monkeypatch.setattr(FE, "query_llm_raw", fake_q)
    batch = [(1, "f", "d", "Глава 1\nфрагмент длинный"),
             (1, "f", "d", "продолжение главы")]
    out = FE.process_batch(batch, "П1", "П2", False, "h", "m", "k",
                           1, 10, 10, None, None, SilentLog())
    assert len(out) == 1 and out[0]["chapter"] == 1
    assert len(calls) == 1  # только P1


def test_process_batch_two_pass(monkeypatch):
    p1_out = '[{"chapter": 1, "fragment": "ааааа", "corrected": "ббббб"}]'
    p2_out = ('[{"chapter": 1, "fragment": "ааааа", "corrected": "ббббб",'
              ' "status": "confirmed"},'
              ' {"chapter": 2, "fragment": "ввввв", "corrected": "ггггг",'
              ' "status": "rejected"}]')
    answers = iter([p1_out, p2_out])
    monkeypatch.setattr(FE, "query_llm_raw", lambda *a, **k: next(answers))
    batch = [(1, "f", "d", "Глава 1\nааааа")]
    out = FE.process_batch(batch, "П1", "П2", True, "h", "m", "k",
                           1, 10, 10, None, None, SilentLog())
    assert len(out) == 1 and out[0]["status"] == "confirmed"


def test_process_batch_failures(monkeypatch):
    # P1 вернул None → ошибка LLM (None = fail-fast на уровне прогона)
    monkeypatch.setattr(FE, "query_llm_raw", lambda *a, **k: None)
    batch = [(1, "f", "d", "текст")]
    assert FE.process_batch(batch, "П1", "П2", True, "h", "m", "k",
                            1, 10, 10, None, None, SilentLog()) is None
    # P1 вернул не-JSON → retry_empty исчерпан → пусто
    monkeypatch.setattr(FE, "query_llm_raw", lambda *a, **k: "мусор")
    assert FE.process_batch(batch, "П1", "П2", False, "h", "m", "k",
                            1, 10, 10, None, None, SilentLog(),
                            retry_empty=1) == []


def test_atomic_write_and_find(tmp_path):
    f = tmp_path / "вложенный" / "файл.txt"
    FE.atomic_write(str(f), "данные")
    assert f.read_text(encoding="utf-8") == "данные"
    d = tmp_path / "00000_1_x"
    d.mkdir()
    (d / "polished.txt").write_text("контент", encoding="utf-8")
    fp, content = FE.find_chapter_file(1, "polished", {1: [str(d)]})
    assert content == "контент"
    fp, content = FE.find_chapter_file(9, "polished", {1: [str(d)]})
    assert fp is None and content is None


# ══════════════════════════════════════════════════════════════════════
# cli/translate_check_llm.py: review-файл и применение
# ══════════════════════════════════════════════════════════════════════

def _mk_chapters(tmp_path, texts):
    """chapters/00000_<n>_t/polished.txt → (chapters_dir, chapter_map)."""
    ch_dir = tmp_path / "chapters"
    for i, text in texts.items():
        d = ch_dir / f"00000_{i}_t"
        d.mkdir(parents=True)
        (d / "polished.txt").write_text(text, encoding="utf-8")
    return str(ch_dir), C.build_chapter_map(str(ch_dir), SilentLog())


def test_load_review_file_legacy_and_doc(tmp_path):
    p = tmp_path / "rev.json"
    p.write_text(json.dumps([{"chapter": 1, "fragment": "старый фрагмент",
                              "corrected": "новый фрагмент",
                              "type": "typo", "reason": "r"}],
                            ensure_ascii=False), encoding="utf-8")
    meta, entries = FE.load_review_file(str(p), SilentLog())
    assert meta is None and len(entries) == 1
    assert entries[0]["status"] == C.REVIEW_ACCEPT
    # полный документ: статусы, применено и неизвестные записи
    doc = {"created": "x", "entries": [
        {"stage": "Главы 1–1 (polished)", "chapter": 1, "file": "a.txt",
         "old": "а было", "new": "а стало", "type": "typo",
         "reason": "", "status": C.REVIEW_REJECT, "applied": False},
        {"old": None},  # битая запись — отсев
        {"chapter": 2, "old": "б было", "new": "б стало",
         "status": C.REVIEW_ACCEPT, "applied": True,
         "applied_at": "2026-01-01 00:00"},
    ]}
    p2 = tmp_path / "rev2.json"
    p2.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    _, e2 = FE.load_review_file(str(p2), SilentLog())
    assert len(e2) == 2
    assert e2[0]["status"] == C.REVIEW_REJECT
    assert e2[1]["applied"] is True and e2[1]["applied_at"]
    assert FE.load_review_file(str(tmp_path / "нет.json"),
                               SilentLog()) == (None, [])
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert FE.load_review_file(str(bad), SilentLog()) == (None, None)


def test_apply_fix_entries_statuses_backup_and_dry_run(tmp_path):
    ch_dir, cmap = _mk_chapters(tmp_path, {1: "первый текст главы один."})
    entries = [
        {"stage": "s", "chapter": 1, "file": str(Path(ch_dir) /
         "00000_1_t" / "polished.txt"), "old": "первый текст",
         "new": "правленый текст", "type": "typo", "reason": "",
         "status": C.REVIEW_ACCEPT, "applied": False},
        {"stage": "s", "chapter": 1, "file": "", "old": "главы один",
         "new": "главы два", "type": "typo", "reason": "",
         "status": C.REVIEW_REJECT, "applied": False},
    ]
    applied, skipped = FE.apply_fix_entries(entries, "polished", cmap,
                                            SilentLog(), dry_run=True)
    assert (len(applied), skipped) == (1, 1)
    fp = Path(ch_dir) / "00000_1_t" / "polished.txt"
    assert fp.read_text(encoding="utf-8") == "первый текст главы один."
    assert not entries[0]["applied"]  # dry-run флаги не ставит
    assert not (fp.parent / "polished.txt.bak").exists()

    applied, skipped = FE.apply_fix_entries(entries, "polished", cmap,
                                            SilentLog())
    assert (len(applied), skipped) == (1, 1)
    assert "правленый текст" in fp.read_text(encoding="utf-8")
    assert (fp.parent / "polished.txt.bak").exists()
    assert entries[0]["applied"] and entries[0]["applied_at"]


def test_apply_fix_entries_no_bak(tmp_path):
    """no_bak=True: файл обновляется, .bak не создаётся."""
    ch_dir, cmap = _mk_chapters(tmp_path, {1: "первый текст главы один."})
    fp = Path(ch_dir) / "00000_1_t" / "polished.txt"
    entries = [
        {"stage": "s", "chapter": 1, "file": str(fp), "old": "первый текст",
         "new": "правленый текст", "type": "typo", "reason": "",
         "status": C.REVIEW_ACCEPT, "applied": False},
    ]
    applied, skipped = FE.apply_fix_entries(entries, "polished", cmap,
                                            SilentLog(), no_bak=True)
    assert (len(applied), skipped) == (1, 0)
    assert "правленый текст" in fp.read_text(encoding="utf-8")
    assert not (fp.parent / "polished.txt.bak").exists()


def test_apply_fix_entries_sequential_same_file(tmp_path):
    ch_dir, cmap = _mk_chapters(
        tmp_path, {1: "альфа и омега в одной строке текста."})
    fp = str(Path(ch_dir) / "00000_1_t" / "polished.txt")
    mk = lambda o, n: {"stage": "s", "chapter": 1, "file": fp, "old": o,
                       "new": n, "type": "", "reason": "",
                       "status": C.REVIEW_ACCEPT, "applied": False}
    # вторая правка цепляется за результат первой
    entries = [mk("альфа", "бета"), mk("бета и омега", "бета и сигма")]
    applied, _ = FE.apply_fix_entries(entries, "polished", cmap,
                                      SilentLog())
    assert len(applied) == 2
    assert Path(fp).read_text(encoding="utf-8") == \
        "бета и сигма в одной строке текста."


# ══════════════════════════════════════════════════════════════════════
# cli/translate_check_llm.py: main() — поиск, apply, авто-режим (без сети)
# ══════════════════════════════════════════════════════════════════════

def _answer_one(monkeypatch, entries_json, calls=None):
    def fake(*a, **kw):
        if calls is not None:
            calls.append(kw.get("label"))
        return entries_json, None
    monkeypatch.setattr(C, "stream_chat_completion", fake)
    monkeypatch.setattr(FE, "stream_chat_completion", fake)


def test_main_check_writes_review_and_params(tmp_path, monkeypatch):
    ch_dir, _ = _mk_chapters(tmp_path,
                             {1: "Текст с ошибкой здесь и далее идёт."})
    monkeypatch.chdir(tmp_path)
    review = str(tmp_path / "fix_review.json")
    _answer_one(monkeypatch, json.dumps([
        {"chapter": 1, "fragment": "ошибкой здесь",
         "corrected": "правкой здесь", "type": "typo", "reason": "тест"}]))
    rc = FE.main(["--host", "http://x", "--api_key", "k", "--model", "m",
                  "--chapters_dir", ch_dir, "--start", "1", "--end", "1",
                  "--type", "polished", "--review", review])
    assert rc == 0
    doc = json.loads(Path(review).read_text(encoding="utf-8"))
    assert len(doc["entries"]) == 1
    e = doc["entries"][0]
    assert e["old"] == "ошибкой здесь" and e["status"] == "принять"
    assert e["file"].endswith("polished.txt")
    params = doc["params"]
    assert params["тип файлов"] == "polished" and params["потоки"] == 4
    # повторный прогон: дубль не добавляется
    rc = FE.main(["--host", "http://x", "--api_key", "k", "--model", "m",
                  "--chapters_dir", ch_dir, "--start", "1", "--end", "1",
                  "--review", review])
    assert rc == 0
    doc2 = json.loads(Path(review).read_text(encoding="utf-8"))
    assert len(doc2["entries"]) == 1 and doc2["params"] == params


def test_main_apply_statuses_backup_and_log(tmp_path, monkeypatch):
    ch_dir, _ = _mk_chapters(tmp_path, {1: "первый текст главы один."})
    monkeypatch.chdir(tmp_path)
    fp = str(Path(ch_dir) / "00000_1_t" / "polished.txt")
    review = str(tmp_path / "rev.json")
    Path(review).write_text(json.dumps({
        "created": "x", "entries": [
            {"stage": "s", "chapter": 1, "file": fp, "old": "первый текст",
             "new": "правленый текст", "type": "typo", "reason": "р",
             "status": C.REVIEW_ACCEPT, "applied": False},
            {"stage": "s", "chapter": 1, "file": fp, "old": "главы один",
             "new": "главы два", "type": "typo", "reason": "",
             "status": C.REVIEW_REJECT, "applied": False},
        ]}, ensure_ascii=False), encoding="utf-8")
    # dry-run: ничего не меняем
    rc = FE.main(["--apply", "--dry-run", "--review", review,
                  "--chapters_dir", ch_dir, "--type", "polished"])
    assert rc == 0
    assert Path(fp).read_text(encoding="utf-8") == "первый текст главы один."
    # реальное применение
    rc = FE.main(["--apply", "--review", review, "--chapters_dir", ch_dir,
                  "--type", "polished"])
    assert rc == 0
    text = Path(fp).read_text(encoding="utf-8")
    assert "правленый текст" in text and "главы один" in text  # reject жив
    assert (Path(fp).parent / "polished.txt.bak").exists()
    doc = json.loads(Path(review).read_text(encoding="utf-8"))
    assert doc["entries"][0]["applied"] is True
    assert doc["entries"][1]["applied"] is False
    # отчёт changes.md не создаётся — web-интерфейс достаточно
    assert not Path("translate_check_llm_changes.md").exists()


def test_main_apply_legacy_array(tmp_path, monkeypatch):
    ch_dir, _ = _mk_chapters(tmp_path, {2: "второй текст главы два."})
    monkeypatch.chdir(tmp_path)
    fp = Path(ch_dir) / "00000_2_t" / "polished.txt"
    review = str(tmp_path / "legacy.json")
    Path(review).write_text(json.dumps([
        {"chapter": 2, "fragment": "второй текст", "corrected": "новый текст",
         "type": "typo", "reason": "старый формат"}],
        ensure_ascii=False), encoding="utf-8")
    rc = FE.main(["--apply", "--review", review, "--chapters_dir", ch_dir,
                  "--type", "polished"])
    assert rc == 0
    assert "новый текст" in fp.read_text(encoding="utf-8")
    doc = json.loads(Path(review).read_text(encoding="utf-8"))
    assert isinstance(doc, dict) and doc["entries"][0]["applied"] is True


def test_main_apply_nothing_and_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        FE.main(["--apply", "--review", "нет.json"])
    ch_dir, _ = _mk_chapters(tmp_path, {1: "текст."})
    review = str(tmp_path / "rev.json")
    Path(review).write_text(json.dumps({"entries": []}), encoding="utf-8")
    rc = FE.main(["--apply", "--review", review, "--chapters_dir", ch_dir])
    assert rc == 0


def test_main_auto_apply_and_fail_fast(tmp_path, monkeypatch):
    ch_dir, _ = _mk_chapters(tmp_path,
                             {1: "Текст с ошибкой здесь и далее идёт."})
    monkeypatch.chdir(tmp_path)
    review = str(tmp_path / "fix_review.json")
    _answer_one(monkeypatch, json.dumps([
        {"chapter": 1, "fragment": "ошибкой здесь",
         "corrected": "правкой здесь", "type": "typo", "reason": "р"}]))
    rc = FE.main(["--host", "http://x", "--api_key", "k", "--model", "m",
                  "--chapters_dir", ch_dir, "--start", "1", "--end", "1",
                  "--review", review, "--auto-apply"])
    assert rc == 0
    fp = Path(ch_dir) / "00000_1_t" / "polished.txt"
    assert "правкой здесь" in fp.read_text(encoding="utf-8")
    assert (fp.parent / "polished.txt.bak").exists()
    doc = json.loads(Path(review).read_text(encoding="utf-8"))
    assert doc["entries"][0]["applied"] is True
    # авто-режим + dry-run: файлы не меняются
    _mk_chapters(tmp_path / "d2", {1: "другой текст с ошибкой тут."})
    _answer_one(monkeypatch, json.dumps([
        {"chapter": 1, "fragment": "ошибкой тут",
         "corrected": "правкой тут", "type": "typo", "reason": "р"}]))
    rc = FE.main(["--host", "http://x", "--api_key", "k", "--model", "m",
                  "--chapters_dir", str(tmp_path / "d2" / "chapters"),
                  "--start", "1", "--end", "1",
                  "--review", str(tmp_path / "rev2.json"),
                  "--auto-apply", "--dry-run"])
    assert rc == 0
    fp2 = tmp_path / "d2" / "chapters" / "00000_1_t" / "polished.txt"
    assert "ошибкой тут" in fp2.read_text(encoding="utf-8")
    # ошибка LLM → код 1 (fail-fast)
    _answer_one(monkeypatch, None)
    rc = FE.main(["--host", "http://x", "--api_key", "k", "--model", "m",
                  "--chapters_dir", ch_dir, "--start", "1", "--end", "1",
                  "--review", str(tmp_path / "rev3.json")])
    assert rc == 1


def test_main_check_no_chapters(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        FE.main(["--host", "http://x", "--start", "1", "--end", "2",
                 "--chapters_dir", "chapters"])


def test_main_check_no_errors(tmp_path, monkeypatch):
    """LLM ответил пустым списком → правки пустые, код 0."""
    ch_dir, _ = _mk_chapters(tmp_path, {1: "Текст без ошибок."})
    monkeypatch.chdir(tmp_path)
    review = str(tmp_path / "rev.json")
    _answer_one(monkeypatch, "[]")
    rc = FE.main(["--host", "http://x", "--api_key", "k", "--model", "m",
                  "--chapters_dir", ch_dir, "--start", "1", "--end", "1",
                  "--review", review])
    assert rc == 0
    doc = json.loads(Path(review).read_text(encoding="utf-8"))
    assert doc["entries"] == []


# ══════════════════════════════════════════════════════════════════════
# Лаунчер: полное меню (dry-run)
# ══════════════════════════════════════════════════════════════════════

def _write_review_meta(tmp_path, applied=False):
    (tmp_path / "translate_check_llm_review.json").write_text(json.dumps({
        "created": "x",
        "params": {"тип файлов": "polished", "начало": 3, "конец": 9},
        "entries": [{"stage": "Главы 3–9 (polished)", "chapter": 3,
                    "file": "", "old": "старый фрагмент",
                    "new": "новый фрагмент", "type": "typo",
                    "reason": "", "status": "принять",
                    "applied": applied}]},
        ensure_ascii=False), encoding="utf-8")

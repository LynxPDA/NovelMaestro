#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cli/ner.py — извлечение имён: конвейерные функции (pass1/pass2,
merge, finalize), run_two_pass и main() целиком (мок LLM)."""
# pyright: reportMissingImports=false
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cli"))
from conftest import SilentLog  # noqa: E402
from core import common as C  # noqa: E402

import ner as NER  # noqa: E402


@pytest.fixture()
def ner_globals():
    """Сохранить/восстановить глобальный словарь ner.py."""
    old = NER.global_ner_data
    NER.global_ner_data = []
    yield
    NER.global_ner_data = old


@pytest.fixture()
def ner_reset():
    old_data = NER.global_ner_data
    old_chunks = NER.processed_chunks
    NER.global_ner_data = []
    NER.processed_chunks = set()
    yield
    NER.global_ner_data = old_data
    NER.processed_chunks = old_chunks


# ══════════════════════════════════════════════════════════════════════
# конвейерные функции
# ══════════════════════════════════════════════════════════════════════
def test_process_chunk_pass1(monkeypatch):
    ok = '[{"term": "陈阳", "type": "Person", "translation": "Чэнь Ян"}]'
    monkeypatch.setattr(NER, "llm_request", lambda *a, **k: ok)
    idx, ners, err = NER.process_chunk_pass1(
        7, "текст", "h", "m", "k", 1, 10, None, "промпт", SilentLog())
    assert (idx, err) == (7, None) and ners[0]["term"] == "陈阳"
    # LLM не ответил
    monkeypatch.setattr(NER, "llm_request", lambda *a, **k: None)
    idx, ners, err = NER.process_chunk_pass1(
        7, "текст", "h", "m", "k", 3, 10, None, "промпт", SilentLog())
    assert ners == [] and "retries" in err
    # некорректный формат
    monkeypatch.setattr(NER, "llm_request", lambda *a, **k: "не json")
    idx, ners, err = NER.process_chunk_pass1(
        7, "текст", "h", "m", "k", 1, 10, None, "промпт", SilentLog())
    assert ners == [] and err == "Invalid NER format"


def test_process_chunk_pass2(monkeypatch):
    # пустой pass1 → сразу пусто без LLM
    idx, ners, err = NER.process_chunk_pass2(
        1, "текст", [], "h", "m", "k", 1, 10, None, "шаблон {chunk_text} {ner_json}",
        SilentLog())
    assert (idx, ners, err) == (1, [], None)
    ok = '[{"term": "陈阳", "type": "Person", "translation": "Чэнь Ян", "status": "confirmed"}]'
    monkeypatch.setattr(NER, "llm_request", lambda *a, **k: ok)
    idx, ners, err = NER.process_chunk_pass2(
        1, "текст", [{"term": "陈阳"}], "h", "m", "k", 1, 10, None,
        "шаблон {chunk_text} {ner_json}", SilentLog())
    assert err is None and ners[0]["status"] == "confirmed"
    monkeypatch.setattr(NER, "llm_request", lambda *a, **k: None)
    idx, ners, err = NER.process_chunk_pass2(
        1, "текст", [{"term": "陈阳"}], "h", "m", "k", 2, 10, None,
        "шаблон {chunk_text} {ner_json}", SilentLog())
    assert "Pass2 fail" in err


def test_compute_final_ner():
    pass2 = {
        0: [{"term": "陈阳", "type": "Person (male)", "translation": "Чэнь Ян"},
            {"term": "陈阳", "type": "Person (male)", "translation": "Чэнь Ян"},
            {"term": "", "translation": "пустой"}],
        1: [{"term": "陈阳", "type": "Person (female)", "translation": "Чэнь Ян"},
            {"term": "林水", "type": "Person (female)", "translation": "Линь Шуй",
             "notes": "заметка"}],
        2: [{"term": "陈阳", "type": "Person (male)", "translation": "Чэнь Ян"}],
    }
    final = NER._compute_final_ner(pass2, 0.95, 3)
    by_term = {f["term"]: f for f in final}
    cy = by_term["陈阳"]
    # дубли внутри одного чанка схлопываются: чанк0 (1) + чанк1 (1) + чанк2 (1)
    assert cy["count"] == 3
    assert set(cy["_source_chunks"]) == {0, 1, 2}
    assert cy["type"] == "Person (male)"  # голоса: male 2 vs female 1
    # last-write-wins для неvotable полей
    assert by_term["林水"]["notes"] == "заметка"


def test_update_global(ner_globals):
    added, updated = NER.update_global_ner(
        [{"term": "陈阳", "type": "Person"}, {"term": "  "}], 0, 0.95, 3,
        SilentLog())
    assert (added, updated) == (1, 0)
    assert NER.global_ner_data[0]["count"] == 1
    # повтор — обновление
    added, updated = NER.update_global_ner(
        [{"term": "陈阳", "translation": "Чэнь Ян"}], 1, 0.95, 3, SilentLog())
    assert (added, updated) == (0, 1)
    assert NER.global_ner_data[0]["count"] == 2
    assert NER.global_ner_data[0]["_source_chunks"] == [0, 1]


def test_finalize_two_pass(ner_globals, tmp_path):
    out = str(tmp_path / "ner.json")
    pass2 = {0: [{"term": "陈阳", "type": "Person (male)",
                  "translation": "Чэнь Ян"}]}
    NER.finalize_two_pass(pass2, out, 0.95, 3, SilentLog())
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert len(data) == 1 and data[0]["term"] == "陈阳"
    assert len(NER.global_ner_data) == 1


def test_save_ner_snapshot(ner_globals, tmp_path):
    out = str(tmp_path / "snap.json")
    NER.global_ner_data = [{"term": "старый", "count": 1,
                            "_ngrams": C.get_ngrams("старый"), "_len": 5}]
    pass2 = {0: [{"term": "новый", "type": "Person"}]}
    NER.save_ner_snapshot(pass2, out, 0.95, 3, SilentLog())
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    terms = {d["term"] for d in data}
    assert {"старый", "новый"} <= terms


def test_postprocess_ner_file(tmp_path):
    f = tmp_path / "ner.json"
    data = [
        {"term": "陈阳", "count": 5, "pinyin": "Chen Yang",
         "type": "Person (male)", "_votes_type": {"Person (male)": 5}},
        {"term": "陳陽", "count": 2, "pinyin": "chen yang",
         "type": "Person (male)", "_votes_type": {"Person (male)": 2}},
        {"term": "редкий", "count": 1, "_ngrams": []},
    ]
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    NER.postprocess_ner_file(str(f), strip_meta=True, min_count=2,
                             logger=SilentLog())
    out = json.loads(f.read_text(encoding="utf-8"))
    assert len(out) == 1                      # редкий отфильтрован, дубль слит
    assert out[0]["term"] == "陈阳"
    assert out[0]["count"] == 7
    assert "陳陽" in out[0]["aliases"]
    assert not any(k.startswith("_") for k in out[0])  # meta удалена
    # нет файла — не падает
    NER.postprocess_ner_file(str(tmp_path / "нет.json"), False, None, SilentLog())


def test_load_initial_ner(tmp_path, ner_globals):
    f = tmp_path / "start.json"
    f.write_text(json.dumps(
        [{"term": "陈阳", "translation": "Чэнь Ян", "count": 4}],
        ensure_ascii=False), encoding="utf-8")
    NER.load_initial_ner(str(f), 3, SilentLog())
    assert len(NER.global_ner_data) == 1
    assert NER.global_ner_data[0]["_ngrams"]
    # нет файла — без падения
    NER.load_initial_ner(str(tmp_path / "нет.json"), 3, SilentLog())


def test_merge_into(ner_globals):
    target = [{"term": "陈阳", "type": "Person (male)", "count": 3,
               "_ngrams": NER.get_ngrams("陈阳"), "_len": 2,
               "_votes_type": {"Person (male)": 3}}]
    final = [{"term": "陈阳", "type": "Person (female)", "count": 2,
              "_source_chunks": [7], "_ngrams": NER.get_ngrams("陈阳"),
              "_len": 2, "_votes_type": {"Person (female)": 2},
              "notes": "новое"}]
    NER._merge_into(target, final, 0.95, 3)
    assert len(target) == 1
    assert target[0]["count"] == 5
    assert target[0]["_source_chunks"] == [7]
    assert target[0]["_votes_type"] == {"Person (male)": 3,
                                        "Person (female)": 2}
    assert target[0]["type"] == "Person (male)"  # 3 против 2
    assert target[0]["notes"] == "новое"
    # новый термин добавляется
    NER._merge_into(target, [{"term": "совсем другой термин", "count": 1,
                              "_ngrams": set(), "_len": 20}], 0.95, 3)
    assert len(target) == 2


def test_merge_alias_groups():
    data = [
        {"term": "陈阳", "pinyin": "Chen Yang", "count": 10,
         "type": "Person (male)", "_votes_type": {"Person (male)": 10}},
        {"term": "陳陽", "pinyin": "chen yang", "count": 3,
         "type": "Person (female)", "_votes_type": {"Person (female)": 3}},
        {"term": "Без чтения", "count": 9},
    ]
    n = NER.merge_alias_groups(data, SilentLog())
    assert n == 1
    assert len(data) == 2
    primary = next(d for d in data if d["term"] == "陈阳")
    assert primary["count"] == 13
    assert "陳陽" in primary.get("aliases", [])
    # голоса пересуммированы
    assert primary["_votes_type"] == {"Person (male)": 10,
                                      "Person (female)": 3}
    assert primary["type"] == "Person (male)"


# ══════════════════════════════════════════════════════════════════════
# run_two_pass целиком
# ══════════════════════════════════════════════════════════════════════
def test_run_two_pass_fresh(tmp_path, monkeypatch, ner_globals):
    p1_answer = '[{"term": "陈阳", "type": "Person (male)", "translation": "Чэнь Ян"}]'
    p2_answer = ('[{"term": "陈阳", "type": "Person (male)", '
                 '"translation": "Чэнь Ян", "status": "confirmed"}]')

    def fake_llm(system_prompt, user_content, *a, **k):
        return p1_answer if system_prompt == "ПРОМПТ1" else p2_answer

    monkeypatch.setattr(NER, "llm_request", fake_llm)
    out = str(tmp_path / "ner.json")
    NER.run_two_pass(
        all_chunks=["текст чанка один", "текст чанка два"],
        pass1_cache_file=str(tmp_path / "p1.json"),
        pass2_cache_file=str(tmp_path / "p2.json"),
        base_url="http://h", model_name="m", api_key="",
        max_retries=1, timeout=10, temperature=None,
        pass1_prompt="ПРОМПТ1", pass2_prompt="ПРОМПТ2 {chunk_text} {ner_json}",
        max_workers=2, ner_file=out, threshold=0.95, ngram_size=3,
        save_interval=1, logger=SilentLog(),
    )
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert len(data) == 1 and data[0]["term"] == "陈阳"
    assert data[0]["count"] == 2  # найден в обоих чанках
    # кэши удалены после финализации
    assert not (tmp_path / "p1.json").exists()
    assert not (tmp_path / "p2.json").exists()


def test_run_two_pass_all_cached(tmp_path, monkeypatch, ner_globals):
    """Все чанки уже в pass2-кэше — LLM не вызывается."""
    cache = {0: [{"term": "陈阳", "type": "Person (male)",
                  "translation": "Чэнь Ян"}]}
    p2f = tmp_path / "p2.json"
    p2f.write_text(json.dumps({str(k): v for k, v in cache.items()},
                              ensure_ascii=False), encoding="utf-8")

    def boom(*a, **k):
        raise AssertionError("LLM не должен вызываться")

    monkeypatch.setattr(NER, "llm_request", boom)
    out = str(tmp_path / "ner.json")
    NER.run_two_pass(
        all_chunks=["текст"], pass1_cache_file=str(tmp_path / "p1.json"),
        pass2_cache_file=str(p2f), base_url="h", model_name="m", api_key="",
        max_retries=1, timeout=10, temperature=None,
        pass1_prompt="П1", pass2_prompt="П2 {chunk_text} {ner_json}",
        max_workers=1, ner_file=out, threshold=0.95, ngram_size=3,
        save_interval=5, logger=SilentLog(),
    )
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert len(data) == 1


def test_run_two_pass_counts_failures(tmp_path, monkeypatch, ner_globals):
    """H4 (AUDIT): run_two_pass возвращает число упавших чанков."""
    def fake_llm(system_prompt, user_content, *a, **k):
        return None  # LLM не отвечает → чанк упал

    monkeypatch.setattr(NER, "llm_request", fake_llm)
    out = str(tmp_path / "ner.json")
    failed = NER.run_two_pass(
        all_chunks=["чанк один", "чанк два", "чанк три"],
        pass1_cache_file=str(tmp_path / "p1.json"),
        pass2_cache_file=str(tmp_path / "p2.json"),
        base_url="http://h", model_name="m", api_key="",
        max_retries=1, timeout=10, temperature=None,
        pass1_prompt="ПРОМПТ1", pass2_prompt="П2 {chunk_text} {ner_json}",
        max_workers=2, ner_file=out, threshold=0.95, ngram_size=3,
        save_interval=1, logger=SilentLog(),
    )
    assert failed == 3  # все чанки упали
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert data == []


def test_run_two_pass_all_ok_returns_zero(tmp_path, monkeypatch, ner_globals):
    """H4: успешный прогон → 0 упавших."""
    p1_answer = '[{"term": "陈阳", "type": "Person", "translation": "Чэнь Ян"}]'
    def fake_llm(system_prompt, user_content, *a, **k):
        return p1_answer

    monkeypatch.setattr(NER, "llm_request", fake_llm)
    failed = NER.run_two_pass(
        all_chunks=["текст"], pass1_cache_file=str(tmp_path / "p1.json"),
        pass2_cache_file=str(tmp_path / "p2.json"),
        base_url="h", model_name="m", api_key="",
        max_retries=1, timeout=10, temperature=None,
        pass1_prompt="ПРОМПТ1", pass2_prompt="П2 {chunk_text} {ner_json}",
        max_workers=1, ner_file=str(tmp_path / "ner.json"),
        threshold=0.95, ngram_size=3, save_interval=5, logger=SilentLog(),
    )
    assert failed == 0


def test_run_two_pass_pass2_fallback(tmp_path, monkeypatch, ner_globals):
    """Pass2 упал — используется результат pass1."""
    p1_answer = '[{"term": "陈阳", "type": "Person", "translation": "Чэнь Ян"}]'
    calls = []

    def fake_llm(system_prompt, user_content, *a, **k):
        calls.append(system_prompt)
        if system_prompt == "ПРОМПТ1":
            return p1_answer
        return None  # pass2 не отвечает

    monkeypatch.setattr(NER, "llm_request", fake_llm)
    out = str(tmp_path / "ner.json")
    NER.run_two_pass(
        all_chunks=["текст"], pass1_cache_file=str(tmp_path / "p1.json"),
        pass2_cache_file=str(tmp_path / "p2.json"),
        base_url="h", model_name="m", api_key="",
        max_retries=1, timeout=10, temperature=None,
        pass1_prompt="ПРОМПТ1", pass2_prompt="П2 {chunk_text} {ner_json}",
        max_workers=1, ner_file=out, threshold=0.95, ngram_size=3,
        save_interval=5, logger=SilentLog(),
    )
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert len(data) == 1 and data[0]["term"] == "陈阳"


# ══════════════════════════════════════════════════════════════════════
# main() целиком (мок LLM)
# ══════════════════════════════════════════════════════════════════════
P1_ANSWER = '[{"term": "陈阳", "type": "Person (male)", "translation": "Чэнь Ян", "pinyin": "Chen Yang"}]'
P2_ANSWER = ('[{"term": "陈阳", "type": "Person (male)", '
             '"translation": "Чэнь Ян", "pinyin": "Chen Yang", '
             '"status": "confirmed"}]')


def _fake_llm(system_prompt, user_content, *a, **k):
    # pass2 получает особый системный промпт
    if system_prompt == NER.SYSTEM_PROMPT_PASS2_SYS:
        return P2_ANSWER
    return P1_ANSWER


def test_main_two_pass(tmp_path, monkeypatch, ner_reset):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "novel.txt").write_text(
        "陈阳 шёл по дороге. Потом 陈阳 остановился.\n"
        "Это был долгий путь.\n", encoding="utf-8")
    monkeypatch.setattr(NER, "llm_request", _fake_llm)
    monkeypatch.setattr(sys, "argv", [
        "ner.py", "novel.txt", "--host", "http://h", "--model", "m",
        "--threads", "1", "--two-pass", "--ner_file", "ner.json"])
    NER.main()
    data = json.loads((tmp_path / "ner.json").read_text(encoding="utf-8"))
    assert data and data[0]["term"] == "陈阳"
    # кэши и прогресс убраны после успешного завершения
    assert not (tmp_path / "ner_pass1_cache.json").exists()
    assert not (tmp_path / "ner_progress.json").exists()
    # лог извлечения — в logs/, не рядом с txt
    assert (tmp_path / "logs" / "ner_extraction.log").is_file()
    assert not (tmp_path / "ner_extraction.log").exists()


def test_main_single_pass(tmp_path, monkeypatch, ner_reset):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "novel.txt").write_text(
        "陈阳 против 陈阳 — два упоминания в разных чанках.\n",
        encoding="utf-8")
    monkeypatch.setattr(NER, "llm_request", lambda *a, **k: P1_ANSWER)
    monkeypatch.setattr(sys, "argv", [
        "ner.py", "novel.txt", "--host", "http://h", "--model", "m",
        "--threads", "1", "--ner_file", "ner.json",
        "--strip-meta", "--min-count", "1"])
    NER.main()
    data = json.loads((tmp_path / "ner.json").read_text(encoding="utf-8"))
    assert data and data[0]["term"] == "陈阳"
    # постпроцессинг: служебные поля удалены
    assert not any(k.startswith("_") for k in data[0])
    # лог извлечения — в logs/ (основной режим)
    assert (tmp_path / "logs" / "ner_extraction.log").is_file()
    assert not (tmp_path / "ner_extraction.log").exists()


def test_main_postprocess_only(tmp_path, monkeypatch, ner_reset):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ner.json").write_text(json.dumps([
        {"term": "陈阳", "count": 5, "type": "Person (male)",
         "_votes_type": {"Person (male)": 5}},
        {"term": "мелочь", "count": 1},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "ner.py", "--ner_file", "ner.json", "--strip-meta",
        "--min-count", "2"])
    NER.main()
    data = json.loads((tmp_path / "ner.json").read_text(encoding="utf-8"))
    assert len(data) == 1 and data[0]["term"] == "陈阳"
    assert "_votes_type" not in data[0]


def test_main_compile_chapters(tmp_path, monkeypatch, ner_reset):
    """--compile_chapters склеивает chapter.txt и идёт в извлечение."""
    monkeypatch.chdir(tmp_path)
    ch = tmp_path / "chapters" / "00000_1_a"
    ch.mkdir(parents=True)
    (ch / "chapter.txt").write_text("陈阳 в тексте.\n", encoding="utf-8")
    monkeypatch.setattr(NER, "llm_request", lambda *a, **k: P1_ANSWER)
    monkeypatch.setattr(sys, "argv", [
        "ner.py", "--compile_chapters", "--host", "http://h", "--model", "m",
        "--threads", "1", "--ner_file", "ner.json"])
    NER.main()
    compiled = tmp_path / "compiled_chapters.txt"
    assert compiled.is_file() and "陈阳" in compiled.read_text(encoding="utf-8")
    data = json.loads((tmp_path / "ner.json").read_text(encoding="utf-8"))
    assert data and data[0]["term"] == "陈阳"


def test_main_no_args_error(monkeypatch, ner_reset):
    monkeypatch.setattr(sys, "argv", ["ner.py"])
    with pytest.raises(SystemExit):
        NER.main()


def test_main_missing_file(tmp_path, monkeypatch, ner_reset):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "ner.py", "нет_файла.txt", "--host", "http://h", "--model", "m"])
    NER.main()  # возврат без исключения
    assert not (tmp_path / "ner.json").exists()


def test_main_resume_progress(tmp_path, monkeypatch, ner_reset):
    """Все чанки уже в прогрессе → однопроходный режим сразу завершается."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "novel.txt").write_text("короткий текст\n", encoding="utf-8")
    (tmp_path / "ner_progress.json").write_text(
        json.dumps({"processed_indices": [0]}), encoding="utf-8")
    (tmp_path / "ner.json").write_text("[]", encoding="utf-8")

    def boom(*a, **k):
        raise AssertionError("LLM не должен вызываться")

    monkeypatch.setattr(NER, "llm_request", boom)
    monkeypatch.setattr(sys, "argv", [
        "ner.py", "novel.txt", "--host", "http://h", "--model", "m",
        "--threads", "1", "--ner_file", "ner.json"])
    NER.main()

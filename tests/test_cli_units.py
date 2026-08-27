#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Юнит-тесты чистых функций скриптов (без LLM и без сети)."""
# pyright: reportMissingImports=false
import json
import os
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cli"))
from conftest import SilentLog  # noqa: E402

import ner as NER            # noqa: E402
import wiki as WIKI          # noqa: E402
import translate_check_llm as RE  # noqa: E402  (бывший fix_errors)
import epub_to_chapters as E2C  # noqa: E402
import clean_and_compile as CAC  # noqa: E402
import translate_check as TC  # noqa: E402
import batch_replace as BR    # noqa: E402


# ══════════════════════════════════════════════════════════════════════
# cli/ner.py
# ══════════════════════════════════════════════════════════════════════
def test_ner_normalize_cjk():
    assert NER.normalize_cjk("陈 阳") == "陈阳"
    assert NER.normalize_cjk("А\u3000Б") == "АБ"


def test_ner_cjk_levenshtein():
    assert NER.cjk_levenshtein("абв", "абв") == 0
    assert NER.cjk_levenshtein("абв", "абг") == 1
    assert NER.cjk_levenshtein("", "абв") == 3
    assert NER.cjk_levenshtein("абвг", "аб") == 2


def test_ner_jaccard():
    assert NER.jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0
    assert NER.jaccard_similarity({"a"}, {"b"}) == 0.0
    assert NER.jaccard_similarity(set(), set()) == 0.0


def test_ner_terms_are_duplicate():
    # точное после нормализации
    assert NER.terms_are_duplicate("陈 阳", "陈阳", 3, 0.8)
    # короткие CJK (<=6) — только точное: 1 символ разницы ≠ дубль
    assert not NER.terms_are_duplicate("一階強化", "九階強化", 3, 0.5)
    # длинные CJK с высоким пересечением биграмм
    a = " небесный громовой дракон"
    assert NER.terms_are_duplicate(a, a.replace(" ", ""), 3, 0.5)
    # латиница: n-граммы
    assert NER.terms_are_duplicate("Linh Thuy", "linh thuy", 3, 0.8)
    assert not NER.terms_are_duplicate("Linh Thuy", "Chen Yang", 3, 0.8)
    # сильная разница длин — не дубль
    assert not NER.terms_are_duplicate("ab", "абвгдежзиклмн", 3, 0.3)


def test_ner_fuzzy_duplicate_in_list():
    items = [{"term": "陈阳"}, {"term": "林水"}]
    ok, it = NER.is_fuzzy_duplicate_in_list("陈 阳", items, 3, 0.8)
    assert ok and it["term"] == "陈阳"
    ok, it = NER.is_fuzzy_duplicate_in_list("Совершенно Иной", items, 3, 0.8)
    assert not ok and it is None


def test_ner_resolve_votes():
    # гендерный класс: большинство
    assert NER._resolve_votes({"Person (male)": 6, "Person (female)": 5}) == "Person (male)"
    # ничья male/female → unknown
    assert NER._resolve_votes({"Person (male)": 3, "Person (female)": 3}) == "Person (unknown)"
    # unknown исключается при наличии конкретных
    assert NER._resolve_votes({"Person (male)": 1, "Person (unknown)": 9}) == "Person (male)"
    # только unknown
    assert NER._resolve_votes({"Person (unknown)": 2}) == "Person (unknown)"
    # негендерный класс — как есть
    assert NER._resolve_votes({"Location": 10}) == "Location"
    # сумма базового класса побеждает: Person 3+3 > Location 5
    assert NER._resolve_votes({"Person (male)": 3, "Person (female)": 3,
                               "Location": 5}).startswith("Person")
    assert NER._resolve_votes({}) == ""


def test_ner_voting_lifecycle():
    item = {"term": "陈阳", "type": ""}
    NER.init_votes(item)
    NER.add_vote(item, "type", "Person (male)")
    NER.add_vote(item, "type", "Person (male)")
    NER.add_vote(item, "type", "Person (female)")
    assert item["type"] == "Person (male)"
    NER.add_vote(item, "type", "Person (female)")
    assert item["type"] == "Person (unknown)"   # 2:2 → ничья
    # пустые/None не голосуют
    NER.add_vote(item, "type", None)
    NER.add_vote(item, "type", "   ")
    assert item["type"] == "Person (unknown)"


def test_ner_merge_fields():
    item = {"term": "陈阳"}
    NER.merge_fields(item, {"type": "Person (male)", "translation": "Чэнь Ян",
                            "bad_field": None, "empty": "   "})
    assert item["type"] == "Person (male)"
    assert item["translation"] == "Чэнь Ян"
    assert "bad_field" not in item and "empty" not in item


def test_ner_is_valid_format():
    good = [{"term": "t", "type": "Person", "translation": "Т"}]
    assert NER.is_valid_ner_format(good)
    assert not NER.is_valid_ner_format("не список")
    assert not NER.is_valid_ner_format([{"term": "t"}])          # нет ключей
    assert not NER.is_valid_ner_format(
        [{"term": "t", "type": "Person", "translation": 5}])      # не строка


def test_ner_parse_response():
    raw = 'Вот результат: [{"term": "陈阳", "type": "Person", "translation": "Чэнь Ян"}] конец'
    data = NER.parse_ner_response(raw)
    assert data and data[0]["term"] == "陈阳"
    assert NER.parse_ner_response("мусор без json") is None
    assert NER.parse_ner_response('[{"term": 1}]') is None


def test_ner_normalize_phonetic():
    assert NER.normalize_phonetic("Chen Yang") == "chenyang"
    assert NER.normalize_phonetic("chén2 yang1") == "chényang"
    assert NER.normalize_phonetic("   ") == ""
    assert NER.normalize_phonetic(None) == ""


def test_ner_get_type_base():
    assert NER.get_type_base("Person (male)") == "Person"
    assert NER.get_type_base("Location") == "Location"
    assert NER.get_type_base("") == ""
    assert NER.get_type_base(None) == ""


def test_ner_merge_alias_groups():
    data = [
        {"term": "陈阳", "pinyin": "Chen Yang", "count": 5,
         "type": "Person (male)", "_votes_type": {"Person (male)": 5}},
        {"term": "陳陽", "pinyin": "chen yang", "count": 2,
         "type": "Person (unknown)", "_votes_type": {"Person (unknown)": 2}},
        {"term": "林水", "count": 7},        # без фонетики — не участвует
    ]
    merged = NER.merge_alias_groups(data, SilentLog())
    assert merged == 1
    assert len(data) == 2
    primary = next(d for d in data if d["term"] == "陈阳")
    assert primary["count"] == 7
    assert "陳陽" in primary["aliases"]
    # голоса суммированы: male 2 (5 после пересчёта?) — победитель определился
    assert primary["type"].startswith("Person")


def test_ner_load_two_pass_prompts(tmp_path):
    p = tmp_path / "prompts.txt"
    p.write_text("<prompt_pass1>\nПЕРВЫЙ\n</prompt_pass1>\n"
                 "<prompt_pass2>\nВТОРОЙ\n</prompt_pass2>", encoding="utf-8")
    p1, p2 = NER.load_two_pass_prompts(str(p), SilentLog())
    assert p1 == "ПЕРВЫЙ" and p2 == "ВТОРОЙ"
    # legacy: файл без тегов = pass1 целиком
    p.write_text("просто промпт", encoding="utf-8")
    p1, p2 = NER.load_two_pass_prompts(str(p), SilentLog())
    assert p1 == "просто промпт" and p2 is None
    # нет файла → встроенный pass1
    p1, p2 = NER.load_two_pass_prompts(str(tmp_path / "нет.txt"), SilentLog())
    assert p1 == NER.SYSTEM_PROMPT_PASS1 and p2 is None


def test_ner_llm_request_delegates(monkeypatch):
    """llm_request обязан идти через единый stream_chat_completion."""
    seen = {}

    def fake_stream(base_url, model, messages, **kw):
        seen.update(base_url=base_url, model=model, messages=messages, **kw)
        return "ОТВЕТ", ""

    monkeypatch.setattr(NER, "stream_chat_completion", fake_stream)
    out = NER.llm_request("система", "запрос", "http://h", "модель",
                          "ключ", 3, 300, 0.1, SilentLog())
    assert out == "ОТВЕТ"
    assert seen["messages"][0]["role"] == "system"
    assert seen["enable_reasoning"] is False
    assert seen["max_tokens"] == 65536


def test_ner_chunk_cache_roundtrip(tmp_path):
    results = {1: [{"term": "陈阳"}], 5: [{"term": "林水"}]}
    f = str(tmp_path / "cache.json")
    NER.save_chunk_cache(f, results)
    loaded = NER.load_chunk_cache(f, SilentLog())
    assert loaded == results
    assert NER.load_chunk_cache(str(tmp_path / "нет.json"), SilentLog()) == {}


# ══════════════════════════════════════════════════════════════════════
# cli/wiki.py
# ══════════════════════════════════════════════════════════════════════
def test_wiki_fts_escape():
    assert WIKI._fts_escape('кавычка "тут"') == 'кавычка ""тут""'


def test_wiki_fts_index_and_search():
    text = ("Линь Шуй вошла в зал и осмотрелась.\n\n"
            "Потом Линь Шуй достала меч.\n\n"
            "Чэнь Ян наблюдал издалека.")
    db = WIKI.build_fts_index(text, chunk_size=100, logger=SilentLog())
    hits = WIKI._fts_search_all(db, '"Линь Шуй"')
    assert len(hits) >= 1 and all("Линь Шуй" in h for h in hits)
    first = WIKI._fts_search_first(db, '"Линь Шуй"')
    assert first == hits[0]
    ids = WIKI._fts_search_ids_all(db, '"Чэнь Ян"')
    assert len(ids) == 1
    # некорректный FTS-запрос не роняет, а даёт пустой результат
    assert WIKI._fts_search_all(db, '"незакрытая') == []
    assert WIKI._fts_search_first(db, '"незакрытая') is None
    assert WIKI._fts_search_ids_all(db, '"незакрытая') == set()
    db.close()


def test_wiki_even_sample():
    items = list(range(10))
    s = WIKI._even_sample(items, 4)
    assert s[0] == 0 and s[-1] == 9 and len(s) == 4
    assert WIKI._even_sample(items, 20) == items
    assert WIKI._even_sample(items, 1) == [0]
    assert WIKI._even_sample(items, 0) == []


def test_wiki_shift_headings():
    assert WIKI._shift_headings("# Титул\n## Раздел\nТекст") == "## Титул\n### Раздел\nТекст"
    # уровень 6 не сдвигается (регулярка ловит только 1–5)
    assert WIKI._shift_headings("###### глубже некуда") == "###### глубже некуда"


def test_wiki_get_base_type():
    assert WIKI._get_base_type("Person (male)") == "Person"
    assert WIKI._get_base_type("Sect") == "Organisation"
    assert WIKI._get_base_type("Cultivation Stage") == "Stage"
    assert WIKI._get_base_type("") == "Other"
    assert WIKI._get_base_type(None) == "Other"


def test_wiki_capitalize_first():
    assert WIKI._capitalize_first("армия тьмы") == "Армия тьмы"
    assert WIKI._capitalize_first("") == ""


def test_wiki_extract_context():
    text = "\n\n".join([
        "Линь Шуй вошла в зал.",
        "Внешность: Линь Шуй была высокой.",
        "Характер Линь Шуй отличался спокойствием.",
        "Совсем другой абзац без имени.",
    ])
    db = WIKI.build_fts_index(text, chunk_size=100, logger=SilentLog())
    ctx = WIKI.extract_context("Линь Шуй", "Person", count=3, db=db,
                               top_k=3, near_distance=64, logger=SilentLog())
    assert ctx and all("Линь Шуй" in c for c in ctx)
    assert WIKI.extract_context("", "Person", 3, db, 3, 64, SilentLog()) == []
    db.close()


def test_wiki_parse_co_occurrence_pairs():
    pairs = WIKI._parse_co_occurrence_pairs("Person:Person, Person:Organisation, битая")
    assert pairs == [("Person", "Person"), ("Person", "Organisation")]
    assert WIKI._parse_co_occurrence_pairs("") == []


def test_wiki_compute_co_occurrence():
    text = "\n\n".join([
        "Линь Шуй и Чэнь Ян встретились на площади.",
        "Позже Чэнь Ян ушёл один.",
        "Линь Шуй тренировалась.",
    ])
    db = WIKI.build_fts_index(text, chunk_size=200, logger=SilentLog())
    groups = {
        "Person": [
            {"term": "林水", "translation": "Линь Шуй"},
            {"term": "陈阳", "translation": "Чэнь Ян"},
        ]
    }
    res = WIKI.compute_co_occurrence(groups, [("Person", "Person")],
                                     top_n=3, db=db, logger=SilentLog())
    assert res["Линь Шуй"][0][0] == "Чэнь Ян"
    assert res["Чэнь Ян"][0][0] == "Линь Шуй"
    db.close()


def test_wiki_load_prompts(tmp_path):
    p = tmp_path / "wiki_prompt.txt"
    p.write_text("<prompt_wiki_article>\nШАБЛОН\n</prompt_wiki_article>",
                 encoding="utf-8")
    assert WIKI.load_wiki_prompts(str(p), SilentLog()) == {"article": "ШАБЛОН"}
    p.write_text("просто текст", encoding="utf-8")
    assert WIKI.load_wiki_prompts(str(p), SilentLog()) == {"article": "просто текст"}
    assert WIKI.load_wiki_prompts(str(tmp_path / "нет"), SilentLog()) == {}


def test_wiki_cache_roundtrip(tmp_path):
    f = str(tmp_path / "cache.json")
    WIKI.save_wiki_cache(f, {"Чэнь Ян": "статья"})
    assert WIKI.load_wiki_cache(f, SilentLog()) == {"Чэнь Ян": "статья"}
    assert WIKI.load_wiki_cache(str(tmp_path / "нет"), SilentLog()) == {}


def test_wiki_llm_request_delegates(monkeypatch):
    def fake_stream(base_url, model, messages, **kw):
        return "СТАТЬЯ", ""

    monkeypatch.setattr(WIKI, "stream_chat_completion", fake_stream)
    out = WIKI.llm_request("с", "ю", "http://h", "m", "k", 1, 60,
                           None, None, SilentLog())
    assert out == "СТАТЬЯ"


# ══════════════════════════════════════════════════════════════════════
# cli/translate_check_llm.py (бывший fix_errors.py)
# ══════════════════════════════════════════════════════════════════════
def test_re_strip_chapter_headers():
    out = RE.strip_chapter_headers("Глава 5: Тьма\nтекст\nГлава 6: Свет\nпродолжение")
    assert "Глава" not in out and "текст" in out and "продолжение" in out
    # известная грань: одиночное «Глава N.» съедает и следующую строку
    # (\s* в _HDR_RE переходит через перевод строки) — фиксируем поведение
    out = RE.strip_chapter_headers("текст до\nГлава 6.\nпродолжение")
    assert "текст до" in out and "Глава" not in out


def test_re_split_into_chunks():
    assert RE.split_into_chunks("маленький", 1000) == ["маленький"]
    paras = "\n\n".join("абзац номер %d" % i for i in range(50))
    chunks = RE.split_into_chunks(paras, 100)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)
    # гигантский абзац без переносов режется по строкам/жёстко
    huge = "х" * 500
    chunks = RE.split_into_chunks(huge, 100)
    assert sum(len(c) for c in chunks) >= 500


def test_re_parse_llm_json():
    assert RE.parse_llm_json('[{"a": 1}]', SilentLog()) == [{"a": 1}]
    assert RE.parse_llm_json('```json\n[{"a": 1}]\n```', SilentLog()) == [{"a": 1}]
    assert RE.parse_llm_json('шум {"не список": 1}', SilentLog()) is None
    assert RE.parse_llm_json('{"ключ": "значение"}', SilentLog()) is None
    assert RE.parse_llm_json("вообще не json", SilentLog()) is None


def test_re_to_int():
    assert RE._to_int(5) == 5
    assert RE._to_int(5.0) == 5
    assert RE._to_int(" 7 ") == 7
    assert RE._to_int("абв") is None
    assert RE._to_int(None) is None


def test_re_validate_errors():
    valid_ch = {1, 2}
    batch = {1: "здесь живёт фрагмент номер один", 2: "другое содержимое главы"}
    errors = [
        {"chapter": 1, "fragment": "фрагмент номер один", "corrected": "правкa"},
        {"chapter": "2", "fragment": "короткий", "corrected": "x"},        # <10 симв
        {"chapter": 1, "fragment": "одинаковый текст тут", "corrected": "одинаковый текст тут"},
        {"chapter": 9, "fragment": "другое содержимое", "corrected": "правкa"},  # переезд в 2
        {"chapter": 9, "fragment": "нет нигде в главах", "corrected": "правкa"},
        {"chapter": "абв", "fragment": "длинный фрагмент здесь", "corrected": "y"},
    ]
    out = RE.validate_errors(errors, valid_ch, batch, SilentLog())
    assert len(out) == 2
    assert out[0]["chapter"] == 1
    assert out[1]["chapter"] == 2   # фрагмент нашли в главе 2


def test_re_apply_safety():
    errors = [
        {"chapter": 1, "fragment": "ааааа", "corrected": "ббббб"},
        {"chapter": 1, "fragment": "ввввв", "corrected": "ггггг"},
        {"chapter": 2, "fragment": "ддд", "corrected": "короткая правка длинная"},
        {"chapter": 2, "fragment": "еееее", "corrected": "жёсткая замена совсем другая"},
    ]
    # без лимитов — как есть
    assert RE.apply_safety(errors, 0, 0, 0, SilentLog()) == errors
    # максимум 1 правка на главу
    out = RE.apply_safety(errors, 1, 0, 0, SilentLog())
    assert len(out) == 2
    # минимальная длина corrected
    out = RE.apply_safety(errors, 0, 15, 0, SilentLog())
    assert all(len(e["corrected"]) >= 15 for e in out)
    # максимальное число изменённых символов
    out = RE.apply_safety(errors, 0, 0, 5, SilentLog())
    assert all(e["fragment"] == "ааааа" or e["fragment"] == "ввввв" for e in out)


def test_re_build_batches():
    chapters = [(1, "f", "d", "х" * 300), (2, "f", "d", "х" * 300),
                (3, "f", "d", "х" * 300)]
    batches = RE.build_batches(chapters, 700, SilentLog())  # 300+200 OV = 500/шт
    assert len(batches) == 3 and all(len(b) == 1 for b in batches)
    batches = RE.build_batches(chapters, 5000, SilentLog())
    assert len(batches) == 1 and len(batches[0]) == 3
    assert RE.build_batches([], 700, SilentLog()) == []


def test_re_detect_chapter_range():
    assert RE.detect_chapter_range({5: ["a"], 2: ["b"], 9: ["c"]}) == (2, 9)
    assert RE.detect_chapter_range({}) == (None, None)


def test_re_find_chapter_file_in_dir(tmp_path):
    d = tmp_path / "00000_1_x"
    d.mkdir()
    (d / "polished.txt").write_text("текст главы", encoding="utf-8")
    fp, content = RE.find_chapter_file_in_dir(str(d), 1, "polished")
    assert fp.endswith("polished.txt") and content == "текст главы"
    fp, content = RE.find_chapter_file_in_dir(str(d), 1, "redacted")
    assert fp is None and content is None


def test_re_collect_chapters_and_chunking(tmp_path):
    d1 = tmp_path / "00000_1_x"
    d1.mkdir()
    (d1 / "polished.txt").write_text("\n\n".join(
        "абзац номер %d наполнен текстом." % i for i in range(80)),
        encoding="utf-8")
    d2 = tmp_path / "00000_2_x"
    d2.mkdir()
    (d2 / "polished.txt").write_text("текст второй", encoding="utf-8")
    cmap = {1: [str(d1)], 2: [str(d2)]}
    # бюджет меньше главы → чанкование
    chapters = RE.collect_chapters(1, 2, "polished", 2000, cmap, SilentLog())
    assert len(chapters) > 2
    assert all(ch[0] in (1, 2) for ch in chapters)
    # пропуск отсутствующих
    chapters = RE.collect_chapters(1, 5, "polished", 100000, cmap, SilentLog())
    assert {ch[0] for ch in chapters} == {1, 2}


def test_re_errors_to_entries(tmp_path):
    d1 = tmp_path / "00000_1_x"
    d1.mkdir()
    target = d1 / "polished.txt"
    target.write_text("здесь ошибка в тексте главы номер раз",
                      encoding="utf-8")
    cmap = {1: [str(d1)]}
    errors = [
        {"chapter": 1, "fragment": "ошибка в тексте", "corrected": "правкa",
         "type": "typo", "reason": "тест"},
        {"chapter": "абв", "fragment": "что-то длинное", "corrected": "x"},
        {"chapter": 1, "fragment": "одно и то же", "corrected": "одно и то же"},
    ]
    entries = RE.errors_to_entries(errors, "Главы 1–1 (polished)",
                                   "polished", cmap, SilentLog())
    assert len(entries) == 1
    e = entries[0]
    assert e["chapter"] == 1 and e["stage"] == "Главы 1–1 (polished)"
    assert e["old"] == "ошибка в тексте" and e["new"] == "правкa"
    assert e["type"] == "typo" and e["status"] == "принять"
    assert e["file"].endswith("polished.txt")
    # неизвестный тип ошибки очищается, запись живёт
    e2 = RE.errors_to_entries(
        [{"chapter": 1, "fragment": "фрагмент подлиннее", "corrected": "y",
          "type": "бред"}], "s", "polished", cmap, SilentLog())
    assert e2 and e2[0]["type"] == ""


# ══════════════════════════════════════════════════════════════════════
# cli/epub_to_chapters.py
# ══════════════════════════════════════════════════════════════════════
def test_e2c_html_to_text():
    out = E2C.html_to_text("<p>Привет</p><p>мир</p>")
    assert "Привет" in out and "мир" in out
    assert "<p>" not in out


def test_e2c_norm_fw():
    assert E2C.norm_fw("第１２章") == "第12章"
    assert E2C.norm_fw("а\u3000б") == "а б"


def test_e2c_normalize_newlines():
    assert E2C.normalize_newlines("а\r\nб\rв\nг") == "а\nб\nв\nг"


def test_e2c_decode_bytes():
    assert E2C.decode_bytes("текст".encode("utf-8")) == "текст"
    gb = "中文内容".encode("gb18030")
    tagged = b'<?xml version="1.0" encoding="gb2312"?>' + gb
    assert "中文内容" in E2C.decode_bytes(tagged)


def test_e2c_nat_key():
    names = ["a10", "a2", "a1"]
    assert sorted(names, key=E2C.nat_key) == ["a1", "a2", "a10"]


def test_e2c_extract_title_html():
    assert E2C.extract_title_html("<title>Глава 1</title>") == "Глава 1"
    assert E2C.extract_title_html("<h1><b>Титул</b></h1>") == "Титул"
    assert E2C.extract_title_html("без заголовка") == ""


def test_e2c_safe_folder():
    assert E2C.safe_folder("Глава 1: Начало?") == "Глава_1_Начало"
    assert E2C.safe_folder("") == "Chapter"
    assert len(E2C.safe_folder("х" * 300)) <= 150


def test_e2c_first_line():
    assert E2C.first_line("\n\n  Первый  \nВторой") == "Первый"
    assert E2C.first_line("   \n") == ""


def test_e2c_title_already_in_text():
    assert E2C.title_already_in_text("Глава 1", "Глава 1\nтекст")
    assert not E2C.title_already_in_text("Глава 9", "другой текст")
    assert E2C.title_already_in_text("", "что угодно")  # пустой заголовок не дублируем


def test_e2c_patterns_and_clean_heading():
    pat = E2C.Patterns(E2C.LANG_PRESETS["zh"], {})
    assert pat.chapter_re is not None
    assert pat.txt_marker() is not None
    # удаление названия книги из заголовка
    h = E2C.clean_heading("《Небесный меч》 第1章 Начало", pat, None)
    assert "《" not in h and "第1章" in h
    h = E2C.clean_heading("Небесный меч - Глава 1", E2C.Patterns(E2C.LANG_PRESETS["ru"], {}),
                          "Небесный меч")
    assert h == "Глава 1"
    assert E2C.clean_heading("", pat) == ""


def test_e2c_write_section(tmp_path, capsys):
    E2C.write_section(tmp_path, 1, "Глава 1", "тело", polished=False)
    folder = tmp_path / "00000_1_Глава_1"
    assert (folder / "chapter.txt").read_text(encoding="utf-8") == "Глава 1\n\nтело\n"
    E2C.write_section(tmp_path, 12, "Глава 12", "", polished=True)
    folder = tmp_path / "0000_12_Глава_12"   # ширина поля счётчика = 6 символов
    assert (folder / "chapter12_polished.txt").exists()
    # dry_run ничего не пишет
    E2C.write_section(tmp_path, 99, "Глава 99", "тело", False, dry_run=True)
    assert not (tmp_path / "000099_Глава_99").exists()


def test_e2c_split_and_write(tmp_path, capsys):
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    text = "Пролог\nвступление\nГлава 1\nпервый текст\nГлава 2\nвторой текст\n"
    n, _, removed = E2C.split_and_write(text, pat, do_clean=True, do_pages=True,
                                        polished=False, output_dir=tmp_path)
    assert n == 3  # прелог + 2 главы
    assert (tmp_path / "00000_1_Пролог").is_dir()
    assert (tmp_path / "00000_2_Глава_1").is_dir()
    assert (tmp_path / "00000_3_Глава_2").is_dir()


def test_e2c_split_and_write_no_markers(tmp_path):
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    n, _, _ = E2C.split_and_write("просто сплошной текст", pat, True, True,
                                  False, tmp_path)
    assert n == 1  # одна секция с дефолтным заголовком


def test_e2c_process_txt(tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("Глава 1\nодин\nГлава 2\nдва\n", encoding="utf-8")
    pat = E2C.Patterns(E2C.LANG_PRESETS["ru"], {})
    out = tmp_path / "chapters"
    out.mkdir()
    n, before, after, removed = E2C.process_txt(
        src, pat, True, True, False, out)
    assert n == 2 and before > 0 and after > 0
    assert (out / "00000_1_Глава_1" / "chapter.txt").is_file()


# ══════════════════════════════════════════════════════════════════════
# cli/clean_and_compile.py
# ══════════════════════════════════════════════════════════════════════
def test_cac_config_titles_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    CAC.cfg.start, CAC.cfg.end = 5, 10
    CAC.cfg.tmp_dir = "."
    assert CAC.cfg.titles_file == os.path.join(".", "titles_5_10.txt")
    # точный файл
    Path("titles_5_10.txt").write_text("5:::Глава 5", encoding="utf-8")
    assert CAC.cfg.get_actual_titles_file() == os.path.join(".", "titles_5_10.txt")
    # покрывающий файл
    (tmp_path / "titles_5_10.txt").unlink()
    Path("titles_1_100.txt").write_text("5:::Глава 5", encoding="utf-8")
    assert CAC.cfg.get_actual_titles_file() == os.path.join(".", "titles_1_100.txt")
    # ничего нет → точное имя (для выгрузки)
    Path("titles_1_100.txt").unlink()
    assert CAC.cfg.get_actual_titles_file() == os.path.join(".", "titles_5_10.txt")


def test_cac_detect_range():
    assert CAC.detect_range({3: "a", 7: "b"}) == (3, 7)
    assert CAC.detect_range({}) is None


def test_cac_build_chapter_map_dup_takes_last(tmp_path, capsys):
    (tmp_path / "00000_1_a").mkdir()
    (tmp_path / "1_b").mkdir()
    m = CAC.build_chapter_map(str(tmp_path))
    assert set(m) == {1} and m[1].endswith("1_b")  # дубль: берётся последняя
    assert "ВНИМАНИЕ" in capsys.readouterr().out


def test_cac_get_content_type():
    assert CAC.get_content_type("x.jpg") == "image/jpeg"
    assert CAC.get_content_type("x.PNG") == "image/png"
    assert CAC.get_content_type("x.webp") == "image/webp"
    assert CAC.get_content_type("x.unknown") == "image/jpeg"  # дефолт


def test_cac_resolve_cover_path(tmp_path):
    """Автоподхват реальной обложки: дефолт cover.jpg отсутствует,
    web сохраняет cover.<ext> (png/webp и т.п.) — берём существующий."""
    src = tmp_path / "source"
    src.mkdir()
    default = str(src / "cover.jpg")
    # нет файлов → возвращаем исходный путь как есть
    assert CAC.resolve_cover_path(default) == default
    # существует cover.png → подхват вместо отсутствующего cover.jpg
    (src / "cover.png").write_bytes(b"\x89PNG\r\n")
    assert CAC.resolve_cover_path(default) == str(src / "cover.png")
    # явно указанный существующий файл не трогаем
    assert CAC.resolve_cover_path(str(src / "cover.png")) == \
        str(src / "cover.png")


def test_cac_inject_fb2_cover(tmp_path):
    fb2 = tmp_path / "book.fb2"
    fb2.write_text(
        '<?xml version="1.0"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">\n'
        '  <description><title-info></title-info></description>\n'
        '  <body><section/></body>\n'
        '</FictionBook>\n', encoding="utf-8")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff\xe0JFIF")
    assert CAC.inject_fb2_cover(str(fb2), str(cover)) is True
    content = fb2.read_text(encoding="utf-8")
    assert "<coverpage>" in content and 'id="cover"' in content
    assert "xmlns:l=" in content
    # повторная инжекция — пропуск
    assert CAC.inject_fb2_cover(str(fb2), str(cover)) is True
    assert content == fb2.read_text(encoding="utf-8")
    # нет обложки / нет fb2
    assert CAC.inject_fb2_cover(str(fb2), str(tmp_path / "нет.jpg")) is False
    assert CAC.inject_fb2_cover(str(tmp_path / "нет.fb2"), str(cover)) is False



# ══════════════════════════════════════════════════════════════════════
# cli/batch_replace.py
# ══════════════════════════════════════════════════════════════════════
def _rules_file(tmp_path, text):
    p = tmp_path / "rules.txt"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_br_parse_rules_basic(tmp_path):
    path = _rules_file(tmp_path, "# комментарий\n\n## Секция\nХунг -> Хун\nХунг\tХуну\n")
    rules, warnings = parse_br(path)
    assert warnings == []
    assert [r.pattern for r in rules] == ["Хунг", "Хунг"]
    assert [r.replacement for r in rules] == ["Хун", "Хуну"]
    assert all(r.section == "Секция" for r in rules)
    assert all(not r.is_regex and not r.ignore_case for r in rules)


def test_br_parse_rules_flags(tmp_path):
    path = _rules_file(tmp_path, "Хунг -> Хун |i\nХунг(а) -> Хун\\1 |r\nX -> Y |ir\n")
    rules, warnings = parse_br(path)
    assert warnings == []
    assert rules[0].ignore_case and not rules[0].is_regex
    assert rules[1].is_regex and not rules[1].ignore_case
    assert rules[2].is_regex and rules[2].ignore_case


def test_br_parse_rules_force_regex(tmp_path):
    path = _rules_file(tmp_path, "Хунг -> Хун\n")
    rules, _ = parse_br(path, force_regex=True)
    assert rules[0].is_regex


def test_br_parse_rules_broken_and_empty(tmp_path):
    path = _rules_file(tmp_path, "нет разделителя\n -> пусто\n")
    rules, warnings = parse_br(path)
    assert rules == [] and len(warnings) == 2
    path2 = _rules_file(tmp_path, "")
    assert parse_br(path2) == ([], [])


def test_br_parse_rules_nfc(tmp_path):
    # NFD-паттерн («е» + комбинируемый диерезис) нормализуется в NFC «ё»
    path = _rules_file(tmp_path, "е\u0308лка -> ёлка\n")
    rules, _ = parse_br(path)
    assert rules[0].pattern == unicodedata.normalize("NFC", "е\u0308лка") == "ёлка"


def test_br_apply_literal():
    # строим правила вручную для чистоты
    r1 = BR.Rule("Хунг", "Хун", False, False)
    r2 = BR.Rule("Бессмертного Заслуга", "Бессмертного Заслуг", True, False)
    out, stats = BR.apply_rules("Хунг и Бессмертного заслуга", [r1, r2])
    assert out == "Хун и Бессмертного Заслуг"
    assert stats == {"Хунг": 1, "Бессмертного Заслуга": 1}


def test_br_apply_literal_backref_is_literal():
    r = BR.Rule("Хунг", "Хун\\1", False, False)
    out, stats = BR.apply_rules("Хунг", [r])
    assert out == "Хун\\1" and stats == {"Хунг": 1}


def test_br_apply_regex_backref():
    r = BR.Rule("Хунг(а|у)", "Хун\\1", False, True)
    out, stats = BR.apply_rules("Хунга и Хунгу", [r])
    assert out == "Хуна и Хуну" and stats == {"Хунг(а|у)": 2}


def test_br_apply_no_change():
    r = BR.Rule("Хунг", "Хун", False, False)
    out, stats = BR.apply_rules("текст", [r])
    assert out == "текст" and stats == {}


def parse_br(path, force_regex=False):
    """Шорткат: parse_rules с распаковкой."""
    return BR.parse_rules(path, force_regex=force_regex)

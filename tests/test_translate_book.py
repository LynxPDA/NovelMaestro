#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cli/translate_book.py — единый LLM-скрипт: ordered-writer,
process_item, парсер/пресеты режимов и main() целиком (мок LLM)."""
# pyright: reportMissingImports=false
import io
import json
import random
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cli"))
from conftest import SilentLog  # noqa: E402

import translate_book as TB  # noqa: E402


# ══════════════════════════════════════════════════════════════════════
# ordered-writer
# ══════════════════════════════════════════════════════════════════════
def test_save_result_ordered(tmp_path):
    # сброс глобального состояния модуля
    TB._next_idx = 0
    TB._pending.clear()
    TB._trace.clear()
    TB._trace_on = True
    TB._write_mode = "translate"
    fh = io.StringIO()
    # запись не по порядку: сначала chunk 1, потом 0
    TB.save_result_ordered(fh, 1, "второй", "ориг2")
    assert fh.getvalue() == ""               # ждём chunk 0
    TB.save_result_ordered(fh, 0, "первый  ", "ориг1")
    assert fh.getvalue() == "первый  \nвторой\n"
    assert [t["chunk_id"] for t in TB._trace] == [0, 1]
    # redact: rstrip
    TB._next_idx, TB._write_mode = 0, "redact"
    fh2 = io.StringIO()
    TB.save_result_ordered(fh2, 0, "текст   ", "о")
    assert fh2.getvalue() == "текст\n"


def test_ordered_writer_chaos():
    TB._next_idx = 0
    TB._pending.clear()
    TB._trace.clear()
    TB._write_mode = "translate"
    TB._trace_on = True
    buf = io.StringIO()
    lock = threading.Lock()

    class FH:
        def write(self, s):
            with lock:
                buf.write(s)
        def flush(self):
            pass

    ids = list(range(200))
    rnd = ids[:]
    random.shuffle(rnd)
    threads = [threading.Thread(
        target=lambda part: [
            TB.save_result_ordered(FH(), i, f"text-{i}", f"orig-{i}")
            for i in part],
        args=(rnd[k::8],)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert buf.getvalue().splitlines() == [f"text-{i}" for i in ids]
    assert [t["chunk_id"] for t in TB._trace] == ids
    assert all(t["original_text"] == f"orig-{t['chunk_id']}"
               for t in TB._trace)


# ══════════════════════════════════════════════════════════════════════
# process_item / парсер / пресеты
# ══════════════════════════════════════════════════════════════════════
def _ctx(mode):
    return {
        "mode": mode, "ner_data": [], "ner_threshold": 0.7, "ner_ngram": 3,
        "ner_fields": "term,translation,type", "automaton": None,
        "include_aliases": False, "prompt": "{ner_block}|{original_text}|{translated_text}",
        "base_url": "http://h", "model": "m", "api_key": "",
        "max_retries": 1, "timeout": 10, "stream_timeout": 10,
        "temperature": None, "reasoning_effort": None,
        "min_len_ratio": 0.0, "logger": SilentLog(),
    }


def test_process_item_translate_ok(monkeypatch):
    monkeypatch.setattr(TB, "stream_chat_completion",
                        lambda *a, **k: ("ПЕРЕВОД", ""))
    idx, text, msg = TB.process_item(0, "оригинал", None, _ctx("translate"))
    assert idx == 0 and text == "ПЕРЕВОД" and "OK" in msg


def test_process_item_redact_fallback(monkeypatch):
    monkeypatch.setattr(TB, "stream_chat_completion",
                        lambda *a, **k: (None, "Loop detected"))
    idx, text, msg = TB.process_item(3, "оригинал", "черновик", _ctx("redact"))
    assert idx == 3 and "ОШИБКА РЕДАКТУРЫ" in text and "черновик" in text
    assert "FAIL" in msg
    # translate fallback — оригинал между маркерами [FAIL]
    idx, text, msg = TB.process_item(4, "оригинал", None, _ctx("translate"))
    assert text.count("[FAIL: Loop detected]") == 2 and "оригинал" in text


def test_build_parser():
    parser = TB.build_parser()
    args = parser.parse_args(["chapter.txt", "--mode", "translate",
                              "--chunk_size", "5000", "--threads", "2"])
    assert args.mode == "translate" and args.chunk_size == 5000
    # режимы валидируются argparse
    with pytest.raises(SystemExit):
        parser.parse_args(["x", "--mode", "абв"])


def test_parser_legacy():
    p = TB.build_parser()
    a = p.parse_args([
        "ch.txt", "--ner_file", "ner.json", "--prompt_file", "translate_prompt.txt",
        "--host", "h", "--model", "m", "--api_key", "", "--threads", "1",
        "--timeout", "300", "--max_retries", "3", "--out", "o.txt",
        "--ner_fields", "term,type,translation", "--chunk_size", "7000",
        "--ner_threshold", "0.75", "--ner_ngram", "3"])          # вызов run_pipeline
    assert a.mode is None and a.chunk_size == 7000
    r = p.parse_args(["--mode", "redact", "t.json",
                      "--min_len_ratio", "0.9", "--no-aliases"])  # старый redact
    assert r.mode == "redact" and r.no_aliases and r.trace is None


def test_mode_presets():
    assert TB.MODE_PRESETS["translate"]["trace_default"] is True
    assert TB.MODE_PRESETS["polish"]["trace_default"] is False
    assert TB.MODE_PRESETS["redact"]["min_len_ratio"] == 0.9
    assert TB.MODE_PRESETS["polish"]["max_retries"] == 3
    assert TB.MODE_PRESETS["translate"]["max_retries"] == 3
    assert TB.MODE_PRESETS["redact"]["max_retries"] == 3


# ══════════════════════════════════════════════════════════════════════
# main() целиком (мок LLM)
# ══════════════════════════════════════════════════════════════════════
def test_main_translate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "book.txt").write_text("Исходный текст книги.", encoding="utf-8")
    (tmp_path / "ner.json").write_text(json.dumps([
        {"term": "测试", "translation": "Тест", "type": "Person", "count": 3},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(TB, "stream_chat_completion",
                        lambda *a, **k: ("ПЕРЕВЕДЁННЫЙ ТЕКСТ", ""))
    monkeypatch.setattr(TB, "determine_model", lambda *a, **k: "модель-х")
    TB.main(["book.txt", "--host", "http://h", "--model", "m",
             "--threads", "1"])
    out = (tmp_path / "translated_book.txt").read_text(encoding="utf-8")
    assert out == "ПЕРЕВЕДЁННЫЙ ТЕКСТ\n"
    trace = json.loads((tmp_path / "translated_book_trace.json")
                       .read_text(encoding="utf-8"))
    assert trace and trace[0]["chunk_id"] == 0
    assert trace[0]["translated_text"].strip() == "ПЕРЕВЕДЁННЫЙ ТЕКСТ"


def test_main_prompt_missing_tag_falls_back_to_builtin(tmp_path, monkeypatch):
    """промпт-файл с тегами, но без тега стадии → предупреждение и
    ВСТРОЕННЫЙ промпт (не «файл целиком» с чужими тегами)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "chunks.json").write_text(json.dumps([
        {"chunk_id": 0, "original_text": "原文",
         "translated_text": "Перевод"},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "ner.json").write_text("[]", encoding="utf-8")
    (tmp_path / "prompt.txt").write_text(
        "<translate>\nПереведи {original_text}\n</translate>\n",
        encoding="utf-8")
    seen = {}
    def fake_stream(base_url, model, messages, *a, **k):
        seen["content"] = messages[0]["content"]
        return ("ПЕРЕВОД", "")
    monkeypatch.setattr(TB, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(TB, "determine_model", lambda *a, **k: "м")
    # redact: в файле только тег <translate> — тег redact отсутствует
    TB.main(["chunks.json", "--mode", "redact", "--prompt_file",
             "prompt.txt", "--host", "http://h", "--model", "m",
             "--threads", "1"])
    from cli.translate_book import DEFAULT_REDACT_PROMPT
    # встроенный промпт, а НЕ файл целиком с чужим тегом
    assert "<translate>" not in seen["content"]
    assert DEFAULT_REDACT_PROMPT.strip().splitlines()[0] in seen["content"]


def test_main_resolves_server_from_env(tmp_path, monkeypatch):
    """host/model из .env (HOST/MODEL), без --host; отдельные модели
    под режимы убраны — берётся единая модель скрипта."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "book.txt").write_text("Исходный текст книги.",
                                       encoding="utf-8")
    (tmp_path / "ner.json").write_text("[]", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("HOST=http://from-env:9989\nMODEL=общая\n",
                   encoding="utf-8")
    seen: dict = {}
    def fake_stream(base_url, model, *a, **k):
        seen["base_url"] = base_url
        seen["model"] = model
        return ("ПЕРЕВОД", "")
    monkeypatch.setattr(TB, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(TB, "determine_model", lambda m, *a, **k: m)
    TB.main(["book.txt", "--threads", "1", "--env_file", str(env)])
    assert seen["base_url"] == "http://from-env:9989/v1"
    assert seen["model"] == "общая"


def test_main_redact_bad_json_returns_1(tmp_path, monkeypatch):
    """H4 (AUDIT): битый chunks.json в redact — код 1, а не 0."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "chunks.json").write_text("это не json{", encoding="utf-8")
    monkeypatch.setattr(TB, "determine_model", lambda *a, **k: "модель-х")
    rc = TB.main(["chunks.json", "--host", "http://h", "--threads", "1"])
    assert rc == 1


def test_main_redact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "chunks.json").write_text(json.dumps([
        {"original_text": "оригинал", "translated_text": "черновик"},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(TB, "stream_chat_completion",
                        lambda *a, **k: ("ОТРЕДАКТИРОВАНО", ""))
    monkeypatch.setattr(TB, "determine_model", lambda *a, **k: "модель-х")
    TB.main(["chunks.json", "--host", "http://h", "--threads", "1"])
    out = (tmp_path / "edited_book.txt").read_text(encoding="utf-8")
    assert out == "ОТРЕДАКТИРОВАНО\n"


def test_main_fail_fallback_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "book.txt").write_text("Текст для фейла.", encoding="utf-8")
    monkeypatch.setattr(TB, "stream_chat_completion",
                        lambda *a, **k: (None, "Ошибка соединения"))
    monkeypatch.setattr(TB, "determine_model", lambda *a, **k: "модель-х")
    TB.main(["book.txt", "--host", "http://h", "--threads", "1"])
    out = (tmp_path / "translated_book.txt").read_text(encoding="utf-8")
    assert "[FAIL: Ошибка соединения]" in out and "Текст для фейла." in out


def test_main_missing_input(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(TB, "determine_model", lambda *a, **k: "модель-х")
    TB.main(["нет_такого.txt", "--host", "http://h", "--threads", "1"])
    assert not (tmp_path / "translated_book.txt").exists()


def test_main_polish_gender_placeholders(tmp_path, monkeypatch):
    """polish: {female_names}/{male_names} подставляются из ner.json
    (translation + (female)/(male) в type; без term), литералы не утекают."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "redacted.txt").write_text(
        "Ляо Тинъянь увидела Су Синюя. Байху рычал.", encoding="utf-8")
    (tmp_path / "ner.json").write_text(json.dumps([
        {"term": "廖停雁", "translation": "Ляо Тинъянь",
         "type": "Person (female)", "count": 12},
        {"term": "苏星宇", "translation": "Су Синюй",
         "type": "Person (male)", "count": 10},
        {"term": "白虎", "translation": "Байху", "type": "Creature",
         "count": 99},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "prompt.txt").write_text(
        "<polish>\nЖ:\n{female_names}\nМ:\n{male_names}\n</polish>",
        encoding="utf-8")
    captured = {}

    def fake_stream(base_url, model, messages, **kw):
        captured["content"] = messages[0]["content"]
        return ("ОТПОЛИРОВАНО", "")

    monkeypatch.setattr(TB, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(TB, "determine_model", lambda *a, **k: "модель-х")
    TB.main(["redacted.txt", "--mode", "polish", "--host", "http://h",
             "--prompt_file", "prompt.txt", "--threads", "1"])
    out = (tmp_path / "polished_book.txt").read_text(encoding="utf-8")
    assert out == "ОТПОЛИРОВАНО\n"
    content = captured["content"]
    prompt_part = content.split("\n\n")[0]  # до входного текста чанка
    assert "{female_names}" not in content
    assert "{male_names}" not in content
    assert "Ж:\nЛяо Тинъянь\nМ:" in prompt_part      # женский список
    assert "Су Синюй" in prompt_part.split("М:")[1]   # мужской список
    assert "Байху" not in prompt_part                 # без пола — не имя
    # term (китайский) не попадает
    assert "廖停雁" not in prompt_part and "苏星宇" not in prompt_part


def test_main_polish_min_count_filters_names(tmp_path, monkeypatch):
    """--names_min_count: имена с count ниже порога не попадают
    в {female_names}/{male_names} (по умолчанию 10)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "redacted.txt").write_text(
        "Ляо Тинъянь увидела Су Синюя.", encoding="utf-8")
    (tmp_path / "ner.json").write_text(json.dumps([
        {"term": "廖停雁", "translation": "Ляо Тинъянь",
         "type": "Person (female)", "count": 5},
        {"term": "苏星宇", "translation": "Су Синюй",
         "type": "Person (male)", "count": 10},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "prompt.txt").write_text(
        "<polish>\nЖ:\n{female_names}\nМ:\n{male_names}\n</polish>",
        encoding="utf-8")
    captured = {}

    def fake_stream(base_url, model, messages, **kw):
        captured["content"] = messages[0]["content"]
        return ("ОТПОЛИРОВАНО", "")

    monkeypatch.setattr(TB, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(TB, "determine_model", lambda *a, **k: "модель-х")
    TB.main(["redacted.txt", "--mode", "polish", "--host", "http://h",
             "--prompt_file", "prompt.txt", "--threads", "1"])
    content = captured["content"]
    prompt_part = content.split("\n\n")[0]
    assert "Ж:\n(нет)\nМ:\nСу Синюй" in prompt_part  # 5 < 10 — отсечена
    assert "Ляо Тинъянь" not in prompt_part
    # порог 0 — все имена попадают
    captured.clear()
    TB.main(["redacted.txt", "--mode", "polish", "--host", "http://h",
             "--prompt_file", "prompt.txt", "--threads", "1",
             "--names_min_count", "0"])
    content2 = captured["content"]
    prompt_part2 = content2.split("\n\n")[0]
    assert "Ж:\nЛяо Тинъянь\nМ:\nСу Синюй" in prompt_part2


def test_main_translate_original_text_placeholder(tmp_path, monkeypatch):
    """translate: {original_text} в промпте заменяется текстом чанка;
    без тега текст дописывается после промпта (обратная совместимость)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ch.txt").write_text("第一章 神秘道种\n苏星宇睁开了眼。",
                                     encoding="utf-8")
    (tmp_path / "prompt.txt").write_text(
        "<translate>\nПереведи:\n{original_text}\n</translate>",
        encoding="utf-8")
    captured = {}

    def fake_stream(base_url, model, messages, **kw):
        captured["content"] = messages[0]["content"]
        return ("ПЕРЕВОД", "")

    monkeypatch.setattr(TB, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(TB, "determine_model", lambda *a, **k: "модель-х")
    TB.main(["ch.txt", "--mode", "translate", "--host", "http://h",
             "--prompt_file", "prompt.txt", "--threads", "1"])
    out = (tmp_path / "translated_book.txt").read_text(encoding="utf-8")
    assert out == "ПЕРЕВОД\n"
    content = captured["content"]
    # тег заменён ровно один раз, литерала нет
    assert "{original_text}" not in content
    assert content.count("第一章 神秘道种") == 1
    # текст внутри тега, а не дописанным хвостом
    assert content.strip().endswith("苏星宇睁开了眼。")
    # перевод не задваивается: в промпте нет второго вхождения чанка
    assert content.count("苏星宇睁开了眼。") == 1


def test_main_translate_no_placeholder_warns_and_appends(tmp_path, monkeypatch):
    """translate без {original_text}: предупреждение в лог + текст
    дописывается после промпта (чтобы перевод не сломался)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ch.txt").write_text("苏星宇睁开了眼。", encoding="utf-8")
    (tmp_path / "prompt.txt").write_text(
        "<translate>\nПереведи следующий текст:\n</translate>",
        encoding="utf-8")
    captured = {}
    # сбрасываем «уже предупреждено» — иначе предупреждение не повторится
    monkeypatch.setattr(TB, "_warned_missing_text_tag", set())

    def fake_stream(base_url, model, messages, **kw):
        captured["content"] = messages[0]["content"]
        return ("ПЕРЕВОД", "")

    monkeypatch.setattr(TB, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(TB, "determine_model", lambda *a, **k: "модель-х")
    TB.main(["ch.txt", "--mode", "translate", "--host", "http://h",
             "--prompt_file", "prompt.txt", "--threads", "1"])
    content = captured["content"]
    assert "苏星宇睁开了眼。" in content
    # хвост после промпта: чанк дописан после последней строки промпта
    tail = content.split("Переведи следующий текст:")[1]
    assert "苏星宇睁开了眼。" in tail
    # предупреждение в лог-файл (logs/translated_book.log)
    log_file = tmp_path / "logs" / "translated_book.log"
    assert log_file.is_file()
    log_text = log_file.read_text(encoding="utf-8")
    assert "{original_text}" in log_text and "WARNING" in log_text

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
from core import stage as core_stage  # noqa: E402


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
    """Контекст process_item: данные режима + контекст стадии с профилем."""
    profile = core_stage.LlmProfile(base_url="http://h", model="m",
                                   timeout=10, stream_timeout=10,
                                   max_retries=1)
    stage = core_stage.Stage(name="pipeline", logger=SilentLog(),
                             profile=profile)
    return {
        "mode": mode, "ner_data": [], "ner_threshold": 0.7, "ner_ngram": 3,
        "ner_fields": "term,translation,type", "automaton": None,
        "prompt": "{ner_block}|{original_text}|{translated_text}",
        "stage": stage, "logger": SilentLog(),
    }


def test_process_item_translate_ok(monkeypatch):
    monkeypatch.setattr(core_stage, "stream_chat_completion",
                        lambda *a, **k: ("ПЕРЕВОД", ""))
    idx, text, msg = TB.process_item(0, "оригинал", None, _ctx("translate"))
    assert idx == 0 and text == "ПЕРЕВОД" and "OK" in msg


def test_process_item_redact_fallback(monkeypatch):
    monkeypatch.setattr(core_stage, "stream_chat_completion",
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
    r = p.parse_args(["--mode", "redact", "t.json"])  # старый redact
    assert r.mode == "redact" and r.trace is None


def test_mode_presets_are_behavior_only():
    """Пресеты режима — только поведение; LLM-числа ушли в общий реестр."""
    assert TB.MODE_PRESETS["translate"]["trace_default"] is True
    assert TB.MODE_PRESETS["polish"]["trace_default"] is False
    assert TB.MODE_PRESETS["redact"]["threads_cap"] == 64
    for mode in ("translate", "redact", "polish"):
        assert set(TB.MODE_PRESETS[mode]) == {"threads_cap", "trace_default"}


# ══════════════════════════════════════════════════════════════════════
# main() целиком (мок LLM)
# ══════════════════════════════════════════════════════════════════════
def test_main_translate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "book.txt").write_text("Исходный текст книги.", encoding="utf-8")
    (tmp_path / "ner.json").write_text(json.dumps([
        {"term": "测试", "translation": "Тест", "type": "Person", "count": 3},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(core_stage, "stream_chat_completion",
                        lambda *a, **k: ("ПЕРЕВЕДЁННЫЙ ТЕКСТ", ""))
    monkeypatch.setattr(core_stage, "determine_model", lambda *a, **k: "модель-х")
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
        seen["messages"] = messages
        return ("ПЕРЕВОД", "")
    monkeypatch.setattr(core_stage, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(core_stage, "determine_model", lambda *a, **k: "м")
    # redact: в файле только тег <translate> — тег redact отсутствует
    TB.main(["chunks.json", "--mode", "redact", "--prompt_file",
             "prompt.txt", "--host", "http://h", "--model", "m",
             "--threads", "1"])
    from cli.translate_book import DEFAULT_REDACT_PROMPT
    # встроенный промпт, а НЕ файл целиком с чужим тегом
    msgs = seen["messages"]
    # встроенный промпт, а НЕ файл целиком с чужими тегами:
    # правила (тело встроенного) — в system, данные — в user
    assert "=== ГЛОССАРИЙ ===" in msgs[0]["content"]
    user_msg = msgs[-1]["content"]
    assert "<translate>" not in user_msg
    assert "=== ОРИГИНАЛ ===" in user_msg and "原文" in user_msg


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
    monkeypatch.setattr(core_stage, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(core_stage, "determine_model", lambda m, *a, **k: m)
    TB.main(["book.txt", "--threads", "1", "--env_file", str(env)])
    assert seen["base_url"] == "http://from-env:9989/v1"
    assert seen["model"] == "общая"


def test_main_redact_bad_json_returns_1(tmp_path, monkeypatch):
    """Битый chunks.json в redact — код 1, а не 0."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "chunks.json").write_text("это не json{", encoding="utf-8")
    monkeypatch.setattr(core_stage, "determine_model", lambda *a, **k: "модель-х")
    rc = TB.main(["chunks.json", "--host", "http://h", "--threads", "1"])
    assert rc == 1


def test_main_redact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "chunks.json").write_text(json.dumps([
        {"original_text": "оригинал", "translated_text": "черновик"},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(core_stage, "stream_chat_completion",
                        lambda *a, **k: ("ОТРЕДАКТИРОВАНО", ""))
    monkeypatch.setattr(core_stage, "determine_model", lambda *a, **k: "модель-х")
    TB.main(["chunks.json", "--host", "http://h", "--threads", "1"])
    out = (tmp_path / "edited_book.txt").read_text(encoding="utf-8")
    assert out == "ОТРЕДАКТИРОВАНО\n"


def test_main_fail_fallback_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "book.txt").write_text("Текст для фейла.", encoding="utf-8")
    monkeypatch.setattr(core_stage, "stream_chat_completion",
                        lambda *a, **k: (None, "Ошибка соединения"))
    monkeypatch.setattr(core_stage, "determine_model", lambda *a, **k: "модель-х")
    TB.main(["book.txt", "--host", "http://h", "--threads", "1"])
    out = (tmp_path / "translated_book.txt").read_text(encoding="utf-8")
    assert "[FAIL: Ошибка соединения]" in out and "Текст для фейла." in out


def test_main_missing_input(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core_stage, "determine_model", lambda *a, **k: "модель-х")
    TB.main(["нет_такого.txt", "--host", "http://h", "--threads", "1"])
    assert not (tmp_path / "translated_book.txt").exists()


def test_main_broken_utf8_input(tmp_path, monkeypatch, caplog):
    """Битый UTF-8 во входе — понятная ошибка с позицией, а не трейсбек.

    Неполная перезапись двумя процессами рвёт многобайтный символ пополам:
    главы 77/78 книги Abundant_Martial_Prowess падали UnicodeDecodeError
    из <frozen codecs>, и по логу нельзя было понять, что файл испорчен."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core_stage, "determine_model", lambda *a, **k: "модель-х")
    # «Бежать» в UTF-8: последний байт символа отрезан
    (tmp_path / "redacted.txt").write_bytes(
        "Начало.\n".encode() + "Бежать".encode()[:-1]
        + " не удалось.\n".encode())
    rc = TB.main(["redacted.txt", "--mode", "polish", "--host", "http://h",
                  "--model", "m", "--threads", "1"])
    assert rc == 1
    text = caplog.text if hasattr(caplog, "text") else ""
    assert "повреждён" in text or "повреждён" in "\n".join(
        r.getMessage() for r in caplog.records)
    # выход не создан: стадия не выдала мусорный перевод
    assert not (tmp_path / "polished.txt").exists()


def test_main_polish_gets_ner_block(tmp_path, monkeypatch):
    """polish: {ner_block} собирается по chapter.txt (термины глоссария,
    найденные в ОРИГИНАЛЕ этой главы), поля — из --ner_fields; литералы
    плейсхолдеров в запрос не утекают."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "chapter.txt").write_text(
        "廖停雁看见了苏星宇。白虎吼叫。", encoding="utf-8")
    (tmp_path / "redacted.txt").write_text(
        "Ляо Тинъянь увидела Су Синюя. Байху рычал.", encoding="utf-8")
    (tmp_path / "ner.json").write_text(json.dumps([
        {"term": "廖停雁", "translation": "Ляо Тинъянь",
         "type": "Person (female)", "count": 12},
        {"term": "苏星宇", "translation": "Су Синюй",
         "type": "Person (male)", "count": 10},
        {"term": "白虎", "translation": "Байху", "type": "Creature",
         "count": 99},
        {"term": "未出现", "translation": "Не в тексте",
         "type": "Person (male)", "count": 50},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "prompt.txt").write_text(
        "<polish>\nГ: {ner_block}\n{original_text}\n</polish>",
        encoding="utf-8")
    captured = {}

    def fake_stream(base_url, model, messages, **kw):
        captured["content"] = messages[-1]["content"]
        return ("ОТПОЛИРОВАНО", "")

    monkeypatch.setattr(core_stage, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(core_stage, "determine_model", lambda *a, **k: "модель-х")
    TB.main(["redacted.txt", "--mode", "polish", "--host", "http://h",
             "--prompt_file", "prompt.txt", "--threads", "1"])
    out = (tmp_path / "polished_book.txt").read_text(encoding="utf-8")
    assert out == "ОТПОЛИРОВАНО\n"
    content = captured["content"]
    assert "{ner_block}" not in content
    prompt_part = content.split("\n\n")[0]  # до входного текста чанка
    # найдены в chapter.txt: term → translation, type, aliases по выбору
    assert "Ляо Тинъянь" in prompt_part and "Су Синюй" in prompt_part
    assert "廖停雁" in prompt_part           # term — всегда в блоке
    # термин, которого нет в оригинале главы, в блок не попал
    assert "Не в тексте" not in prompt_part
    # сам текст полировки — русский перевод, а не оригинал
    assert "Ляо Тинъянь увидела Су Синюя" in content


def test_main_polish_ner_block_without_chapter(tmp_path, monkeypatch):
    """polish без chapter.txt: глоссарий ищется по переданному тексту
    (полировка из перевода — запасной вариант), стадия не падает."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "translated.txt").write_text(
        "Су Синюй вошёл в зал.", encoding="utf-8")
    (tmp_path / "ner.json").write_text(json.dumps([
        {"term": "苏星宇", "translation": "Су Синюй",
         "type": "Person (male)", "count": 10},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "prompt.txt").write_text(
        "<polish>\nГ: {ner_block}\n{original_text}\n</polish>",
        encoding="utf-8")
    captured = {}

    def fake_stream(base_url, model, messages, **kw):
        captured["content"] = messages[-1]["content"]
        return ("ОТПОЛИРОВАНО", "")

    monkeypatch.setattr(core_stage, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(core_stage, "determine_model", lambda *a, **k: "модель-х")
    assert TB.main(["translated.txt", "--mode", "polish", "--host", "http://h",
                    "--prompt_file", "prompt.txt", "--threads", "1"]) is None
    assert "Су Синюй" in captured["content"]


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
        captured["content"] = messages[-1]["content"]
        return ("ПЕРЕВОД", "")

    monkeypatch.setattr(core_stage, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(core_stage, "determine_model", lambda *a, **k: "модель-х")
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
        captured["content"] = messages[-1]["content"]
        return ("ПЕРЕВОД", "")

    monkeypatch.setattr(core_stage, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(core_stage, "determine_model", lambda *a, **k: "модель-х")
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


# ══════════════════════════════════════════════════════════════════════
# build_user_content / --preview-request
# ══════════════════════════════════════════════════════════════════════

def test_build_user_content_modes():
    """build_user_content: redact — все плейсхолдеры; translate — тег
    {original_text} заменяется ровно один раз."""
    p = "G:{ner_block} O:{original_text} T:{translated_text}"
    out = TB.build_user_content("redact", p, "ориг", "черн", "[]")
    assert "G:[]" in out and "O:ориг" in out and "T:черн" in out
    # translate: {translated_text} не входит в промпт, тег заменён
    out = TB.build_user_content("translate", "П:{original_text}",
                                "текст", None, "[]")
    assert out == "П:текст"
    assert "{original_text}" not in out


def test_build_user_content_missing_tag_appends(monkeypatch):
    """translate без {original_text}: текст дописан после промпта;
    предупреждение — один раз на режим (без логгера — без побочных)."""
    monkeypatch.setattr(TB, "_warned_missing_text_tag", set())
    out = TB.build_user_content("translate", "ПРОМПТ", "ТЕКСТ", None, "[]")
    assert out.startswith("ПРОМПТ") and out.endswith("ТЕКСТ")


def test_build_parser_has_no_extended_context_flags():
    """Расширенного контекста в стадии перевода нет: флагов словаря,
    правил, примеров и few-shot в argparse не остаётся."""
    p = TB.build_parser()
    a = p.parse_args(["x.txt", "--chunk_size", "16000"])
    assert a.chunk_size == 16000
    for flag in ("request_budget", "dict_file", "rules_file", "examples_file",
                 "fewshot_k", "fewshot_threshold"):
        assert not hasattr(a, flag), flag


def test_main_preview_request(tmp_path, monkeypatch):
    """--preview-request: JSON первого запроса без сети; артефакты
    (translated_book.txt, trace) НЕ создаются."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ch.txt").write_text("苏星宇睁开了眼。", encoding="utf-8")
    (tmp_path / "prompt.txt").write_text(
        "<translate>\nПереведи:\n{original_text}\n</translate>",
        encoding="utf-8")
    monkeypatch.setattr(core_stage, "determine_model", lambda *a, **k: "модель-х")
    calls = []
    monkeypatch.setattr(core_stage, "stream_chat_completion",
                        lambda *a, **k: calls.append(1) or ("", ""))

    def fail_open(*a, **k):  # выходной файл не должен открываться
        raise AssertionError("out-файл открыт в режиме предпросмотра")

    pv = tmp_path / "preview.json"
    TB.main(["ch.txt", "--mode", "translate", "--host", "http://h",
             "--prompt_file", "prompt.txt", "--threads", "1",
             "--preview-request", str(pv)])
    data = json.loads(pv.read_text(encoding="utf-8"))
    assert data["stage"] == "pipeline" and data["model"] == "модель-х"
    assert "Переведи" in data["messages"][-1]["content"]
    assert "苏星宇睁开了眼。" in data["messages"][-1]["content"]
    assert data["chars"]["user"] > 0
    assert data["meta"]["chunks"] == 1
    assert data["meta"]["mode"] == "translate"
    # сеть не тронута, артефактов нет
    assert not calls
    assert not (tmp_path / "translated_book.txt").exists()
    assert not (tmp_path / "translated_trace.json").exists()


def test_main_preview_request_empty_input(tmp_path, monkeypatch):
    """Предпросмотр на пустом входе — код 1 с причиной, а не IndexError.

    items[0] падал трейсбеком: человек видел «Ошибка предпросмотра: …»
    с последней строкой traceback вместо «вход пуст»."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ch.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(core_stage, "determine_model",
                        lambda *a, **k: "модель-х")
    pv = tmp_path / "preview.json"
    rc = TB.main(["ch.txt", "--mode", "translate", "--host", "http://h",
                  "--prompt_file", "", "--threads", "1",
                  "--preview-request", str(pv)])
    assert rc == 1
    assert not pv.exists()
    # предпросмотр не заводит ни папки логов, ни файла лога
    assert not (tmp_path / "logs").exists()


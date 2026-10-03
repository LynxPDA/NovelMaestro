#!/usr/bin/env python3
"""Тесты стадии «Оценка перевода (LLM)» (cli/translate_quality.py):
промпт-тег, бюджет-обрезка до целых глав, подстановка плейсхолдеров,
md-отчёт и прогон main() с моками LLM. Режимы: range (один пакет) и chunks
(чанки по целым главам, артефакты tmp/quality/, дерево LLM-сводок,
--summary-only и --preview-request с планом). Без сети."""
# pyright: reportMissingImports=false
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cli"))

import translate_quality as TQ  # noqa: E402
from core import stage as core_stage  # noqa: E402
from conftest import SilentLog  # noqa: E402

LOG = SilentLog()


# ══════════════════════════════════════════════════════════════════════
# Промпт: тег <prompt_assessment>
# ══════════════════════════════════════════════════════════════════════

def test_load_assessment_prompt_tagged(tmp_path):
    """Тег извлекается; комментарии вне тега игнорируются."""
    p = tmp_path / "p.txt"
    p.write_text(
        "# комментарий-справка\n"
        "<prompt_assessment>\nТы — критик.\n"
        "</prompt_assessment>\n"
        "# после тега\n",
        encoding="utf-8")
    got = TQ.load_assessment_prompt(str(p), LOG)
    assert got == "Ты — критик."
    assert "комментарий" not in got


def test_load_assessment_prompt_untagged_fallback(tmp_path):
    """Файл без тегов — целиком; None — встроенный дефолт."""
    p = tmp_path / "p.txt"
    p.write_text("просто промпт\n", encoding="utf-8")
    assert TQ.load_assessment_prompt(str(p), LOG) == "просто промпт"
    assert TQ.load_assessment_prompt(str(tmp_path / "нет.txt"), LOG) \
        == TQ.DEFAULT_PROMPT


# ══════════════════════════════════════════════════════════════════════
# Бюджет: целое количество глав
# ══════════════════════════════════════════════════════════════════════

def test_fit_budget_all_fits():
    ch = [(1, "ааа"), (2, "ббб"), (3, "ввв")]
    orig = {1: "А", 2: "Б", 3: "В"}
    kept, dropped = TQ.fit_budget(ch, orig, "промпт", budget=100)
    assert kept == ch and dropped == 0


def test_fit_budget_trims_to_whole_chapters():
    """Не влезает — первые N целых глав (оригинал+перевод), порядок
    сохраняется. Единица — ТОКЕНЫ (оценка): глава ~40 (33+7)."""
    ch = [(1, "а" * 100), (2, "б" * 100), (3, "в" * 100)]
    orig = {1: "А" * 20, 2: "Б" * 20, 3: "В" * 20}
    kept, dropped = TQ.fit_budget(ch, orig, "промпт", budget=50)
    assert [n for n, _ in kept] == [1]
    assert dropped == 2


def test_fit_budget_counts_original_and_translation():
    """Размер главы = перевод + оригинал (ТОКЕНЫ, оценка ~40), влезают 2."""
    ch = [(1, "а" * 100), (2, "б" * 100), (3, "в" * 100)]
    orig = {1: "А" * 20, 2: "Б" * 20, 3: "В" * 20}
    kept, dropped = TQ.fit_budget(ch, orig, "промпт", budget=90)
    assert [n for n, _ in kept] == [1, 2]
    assert dropped == 1


def test_fit_budget_prompt_ignored():
    """Промпт НЕ вычитается из бюджета (бюджет — только содержимое):
    огромный промпт не мешает влезанию глав."""
    ch = [(1, "ааа"), (2, "ббб")]
    orig = {1: "А", 2: "Б"}
    kept, dropped = TQ.fit_budget(ch, orig, "п" * 500, budget=100)
    assert kept == ch and dropped == 0


# ══════════════════════════════════════════════════════════════════════
# Подстановка плейсхолдеров
# ══════════════════════════════════════════════════════════════════════

def test_build_user_prompt_placeholders():
    tpl = "Оригинал: {original_text}\nПеревод: {translated_text}"
    out = TQ.build_user_prompt(tpl, "林水", "Линь Шуй")
    assert "Оригинал: 林水" in out
    assert "Перевод: Линь Шуй" in out


# ══════════════════════════════════════════════════════════════════════
# Отчёт
# ══════════════════════════════════════════════════════════════════════

def test_build_report_technical_header():
    meta = {
        "date": "2026-01-02 10:00",
        "range_requested": (1, 50),
        "range_included": (1, 20),
        "chapters": 20,
        "dropped": 30,
        "file_type": "polished",
        "budget": 200000,
        "packet_size": 120000,
        "model": "qwen3",
        "host": "http://h/v1",
        "prompt_file": "p.txt",
    }
    r = TQ.build_report(meta, "**9/10** — хорошо")
    assert "# Оценка качества перевода" in r
    assert "| Дата | 2026-01-02 10:00 |" in r
    assert "| Диапазон глав | 1 – 20 (из запрошенных 1 – 50; отсечено 30 глав бюджетом) |" in r
    assert "| Модель | qwen3 |" in r
    assert "**9/10** — хорошо" in r


def test_build_report_no_trimming():
    meta = {
        "date": "d", "range_requested": (1, 3),
        "range_included": (1, 3), "chapters": 3, "dropped": 0,
        "file_type": "redacted", "budget": 100000, "packet_size": 3000,
        "model": "m", "host": "h", "prompt_file": "",
    }
    r = TQ.build_report(meta, "текст")
    assert "| Диапазон глав | 1 – 3 |" in r
    assert "отсечено" not in r


# ══════════════════════════════════════════════════════════════════════
# Режим chunks: чанки по целым главам
# ══════════════════════════════════════════════════════════════════════

def test_build_chunks_whole_chapters():
    """Чанк — целые главы; последний может быть короче."""
    ch = [(i, f"текст {i}") for i in range(1, 6)]
    chunks = TQ.build_chunks(ch, chunk_size=2)
    assert [[n for n, _ in c["items"]] for c in chunks] == [[1, 2], [3, 4], [5]]
    assert [c["id"] for c in chunks] == [1, 2, 3]
    assert all(c.get("part") is None for c in chunks)
    assert TQ.chunk_label(chunks[0]["nums"]) == "главы 1–2"
    assert TQ.chunk_label(chunks[2]["nums"]) == "глава 5"


def test_build_chunks_overlap_steps_by_whole_chapters():
    """Перекрытие (ГЛАВЫ) сдвигает шаг: шаг = size − overlap, главы целые."""
    ch = [(i, f"текст {i}") for i in range(1, 5)]
    chunks = TQ.build_chunks(ch, chunk_size=2, overlap=1)
    assert [[n for n, _ in c["items"]] for c in chunks] == [[1, 2], [2, 3], [3, 4]]


def test_refit_oversize_splits_oversize_chapter_by_paragraphs():
    """Глава крупнее бюджета → части одной главы; id перенумерованы, все влезают."""
    text = "\n\n".join(f"Абзац {i}. " + "слово " * 40 for i in range(6))
    chunks = TQ.build_chunks([(1, text)], chunk_size=1)
    assert TQ.estimate_tokens(text) > 200
    out, split_ch, oversize = TQ.refit_oversize(chunks, {}, 200, LOG)
    assert split_ch == 1 and oversize == []
    assert len(out) > 1
    assert all(c["nums"] == [1] and c["part"] for c in out)
    assert [c["id"] for c in out] == list(range(1, len(out) + 1))
    assert all(TQ.chunk_tokens(c, {}) <= 200 for c in out)
    assert out[1]["part"] == (2, len(out))
    assert TQ.chunk_label([1], (2, len(out))) == f"глава 1, часть 2/{len(out)}"


def test_refit_oversize_pairs_part_with_part_of_original():
    """Часть перевода идёт в запрос со своим куском оригинала, не со всей главой."""
    text = "\n\n".join(f"Перевод {i}. " + "слово " * 40 for i in range(6))
    otext = "\n\n".join(f"Оригинал {i}. " + "字 " * 40 for i in range(6))
    out, _split, _over = TQ.refit_oversize(
        TQ.build_chunks([(1, text)], 1), {1: otext}, 300, LOG)
    assert len(out) > 1
    first_orig, first_trans = TQ.chunk_content(out[0], {1: otext})
    assert first_orig.startswith("Оригинал 0") and "Оригинал 5" not in first_orig
    assert first_trans.startswith("Перевод 0") and "Перевод 5" not in first_trans


def test_refit_oversize_reports_impossible_chapter():
    """Глава без абзацев и предложений не режется: она уходит в oversize."""
    blob = "а" * 3000
    out, split_ch, oversize = TQ.refit_oversize(
        TQ.build_chunks([(7, blob)], 1), {}, 200, LOG)
    assert split_ch == 1 and oversize == [7]
    assert len(out) == 1 and out[0]["part"] is None
    assert TQ.chunk_tokens(out[0], {}) > 200


def test_merge_small_parts_glues_crumbs():
    """Куски меньше 1/10 бюджета вклеиваются в соседа: отдельного запроса на
    5 токенов быть не должно."""
    parts = ["а" * 400, "б" * 5, "в" * 400, "г" * 5]
    out = TQ.merge_small_parts(parts, 500)
    assert out == ["а" * 400 + "б" * 5, "в" * 400 + "г" * 5]
    # склейка не должна выводить кусок за бюджет
    assert TQ.merge_small_parts(["а" * 400, "б" * 200], 500) == ["а" * 400,
                                                                "б" * 200]
    assert TQ.merge_small_parts(["\n\n", "а" * 10], 500) == ["а" * 10]


def test_select_chunks_all_uniform_and_first():
    """0 = все; uniform — равномерно по книге; first — первые по порядку."""
    chunks = TQ.build_chunks([(i, str(i)) for i in range(1, 9)], 1)
    got, note = TQ.select_chunks(chunks, 0, "uniform")
    assert len(got) == 8 and note == "все"
    got, note = TQ.select_chunks(chunks, 4, "uniform")
    assert [c["nums"][0] for c in got] == [1, 3, 6, 8]
    assert note == "равномерно по книге"
    got, note = TQ.select_chunks(chunks, 3, "first")
    assert [c["nums"][0] for c in got] == [1, 2, 3]
    assert note == "первые по порядку"
    got, _note = TQ.select_chunks(chunks, 99, "uniform")
    assert len(got) == 8


# ══════════════════════════════════════════════════════════════════════
# Разбор ответа модели (шкала 0–10 в промпте)
# ══════════════════════════════════════════════════════════════════════

ANSWER = (
    "Вывод: перевод ровный.\n\n- глава 3: калька\n"
    "\n<<<QUALITY>>>\n"
    '{"score": 87, "sections": {"точность": 9, "стиль": "8 из 10"},\n'
    ' "strengths": [{"type": "стиль", "chapter": "глава 3",\n'
    '   "note": "ритм"}],\n'
    ' "issues": [{"type": "опечатки", "chapter": 4,\n'
    '   "quote": "тире", "note": "опечатка"}]}\n'
    "<<<END>>>\n"
)


def test_parse_quality_answer_block():
    """Блок разбирается, текст очищается; шкала >10 приводится к 0–10."""
    text, parsed = TQ.parse_quality_answer(ANSWER)
    assert parsed is not None
    assert "<<<QUALITY>>" not in text and "Вывод: перевод ровный" in text
    assert parsed["score"] == 8.7
    assert parsed["sections"] == {"точность": 9.0, "стиль": 8.0}
    assert parsed["strengths"][0]["chapter"] == 3
    assert parsed["strengths"][0]["note"] == "ритм"
    assert parsed["issues"][0]["chapter"] == 4


def test_parse_quality_answer_no_block_and_broken_json():
    """Нет блока / битый JSON / не dict — только текст, разбора нет."""
    assert TQ.parse_quality_answer("просто текст") == ("просто текст", None)
    assert TQ.parse_quality_answer("") == ("", None)
    text, parsed = TQ.parse_quality_answer(
        "а\n<<<QUALITY>>>{битый\n<<<END>>>")
    assert parsed is None and "битый" in text
    text, parsed = TQ.parse_quality_answer("а\n<<<QUALITY>>>[1, 2]<<<END>>>")
    assert parsed is None and "[1, 2]" in text
    _t, fenced = TQ.parse_quality_answer(
        "<<<QUALITY>>>\n```json\n{\"score\": 7}\n```\n<<<END>>>")
    assert fenced is not None and fenced["score"] == 7.0


def test_score_and_finding_edge_values():
    """Пустое/булево/не число — None; обрезка цитаты по СИМВОЛАМ."""
    assert TQ._score(True) is None and TQ._score("") is None
    assert TQ._score("8,5") == 8.5 and TQ._score(11) == 1.1
    assert TQ._finding(7) is None
    f = TQ._finding({"quote": "ц" * 400})
    assert f is not None
    assert f["quote"].endswith("…") and len(f["quote"]) == TQ.QUOTE_MAX_CHARS


def test_merge_findings_groups_by_quote():
    """Одинаковая цитата (регистр/пробелы) — одно замечание со счётом."""
    def res(items):
        return {"parsed": {"score": None, "sections": {}, "strengths": [],
                           "issues": items}}
    merged = TQ.merge_findings([
        res([{"type": "стиль", "chapter": 3, "quote": "Темнота  упала",
              "note": "повтор"}]),
        res([{"type": "стиль", "chapter": 9, "quote": "темнота упала"},
             {"type": "точность", "chapter": 9, "note": "смысл"}]),
    ], "issues")
    assert len(merged) == 2
    assert merged[0]["count"] == 2 and merged[0]["chapters"] == [3, 9]


# ══════════════════════════════════════════════════════════════════════
# Свёртка отчётов чанков (то же --budget, дерево, каскад сжатия)
# ══════════════════════════════════════════════════════════════════════

def _results(n=6, with_quote=True):
    out = []
    for i in range(1, n + 1):
        out.append({
            "id": i, "nums": [i], "label": f"глава {i}", "error": None,
            "text": "", "model": "m", "tokens": 1, "part": None,
            "parsed": {"score": 5.0 + (i % 5) / 10,
                       "sections": {"точность": 7.0, "стиль": 8.0},
                       "strengths": [],
                       "issues": [{"type": "стиль", "chapter": i,
                                   "quote": "цитата" * 20 if with_quote else "",
                                   "note": "заметка" * 20}]},
        })
    return out


class FakeLlm:
    """Профиль стадии-заглушка: считает запросы, отвечает фиксированный текст."""

    def __init__(self, answer="сводка"):
        self.answer = answer
        self.calls = []
        self.model = "m"

    def quiet(self):
        return self

    def complete(self, prompt, data="", *, label="", **kw):
        self.calls.append((label, prompt))
        return self.answer, None


def test_group_by_budget_batches_fit():
    texts = ["т" * 400] * 5
    batches = TQ.group_by_budget(texts, 200)
    assert len(batches) == 5 and all(len(b) == 1 for b in batches)
    assert TQ.group_by_budget(["а", "б"], 100000) == [["а", "б"]]


def test_fold_reports_tree_of_summaries(tmp_path, monkeypatch):
    """Дерево: батчи ≤ бюджета → промежуточные сводки, счётчики и уровни."""
    monkeypatch.chdir(tmp_path)
    llm = FakeLlm("краткая сводка")
    texts = [f"### глава {i}\nзаметка " + "слова " * 5 for i in range(1, 7)]
    budget = 3 * TQ.estimate_tokens(texts[0])
    out, requests, levels = TQ.fold_reports(llm, texts, budget,
                                            "шаблон {batch_text}", LOG)
    assert requests == 2 and levels == 1
    assert len(out) == 2 and all("краткая сводка" in t for t in out)
    # метка запроса — уровень и номер батча
    assert llm.calls[0][0] == "[свёртка 1:1/2]"
    # артефакт промежуточной сводки
    assert (Path.cwd() / TQ.CHUNK_DIR / "summary-l1-1.json").exists()


def test_reduce_reports_cascades_compression(tmp_path, monkeypatch):
    """Без цитат текст короче: каскад уровней доходит до «только баллы»."""
    monkeypatch.chdir(tmp_path)
    llm = FakeLlm("сводка")
    results = _results(6, with_quote=True)
    text, meta = TQ.reduce_reports(llm, results, 10_000, "п {batch_text}", LOG)
    assert meta["total"] == 6 and meta["used"] == 6
    assert meta["compression"] == TQ.LEVEL_NAMES[0]
    assert meta["trimmed"] is False and "\n\n" in text
    # жёсткий бюджет: каскад доходит до уровня «только баллы», цитат нет
    _text, meta2 = TQ.reduce_reports(llm, results, 1, "п {batch_text}", LOG)
    assert meta2["compression"] == TQ.LEVEL_NAMES[2]
    assert meta2["trimmed"] is True
    text3 = TQ.unit_text(results[0], 2)
    assert "цитата" not in text3 and "общий балл" in text3


def test_unit_text_levels_and_errors():
    r = _results(1)[0]
    assert "цитата" in TQ.unit_text(r, 0)
    assert "цитата" not in TQ.unit_text(r, 1)
    assert "заметка" not in TQ.unit_text(r, 2)
    bad = {"id": 9, "label": "глава 42", "error": "таймаут"}
    assert TQ.unit_text(bad) == "### глава 42\n(оценка не получена: таймаут)"


def test_summarize_user_without_placeholder():
    """Старый внешний промпт без плейсхолдера: данные дописаны в конец."""
    out = TQ.summarize_user("Ты — критик.", "СВОДКА")
    assert "=== СВОДКИ ЧАНКОВ ===" in out and "СВОДКА" in out
    assert "СВОДКА" in TQ.summarize_user("данные: {batch_text}", "СВОДКА")


# ══════════════════════════════════════════════════════════════════════
# Артефакты чанков tmp/quality/
# ══════════════════════════════════════════════════════════════════════

def test_chunk_artifacts_roundtrip_and_wipe(tmp_path):
    """Запись/чтение по id; новый запуск затирает каталог."""
    d = tmp_path / "tmp" / "quality"
    TQ.reset_chunks_dir(str(d), LOG)
    (d / "chunk-002.json").write_text(
        '{"nums": [2], "text": "старьё"}', encoding="utf-8")
    TQ.save_chunk_result({"id": 1, "nums": [1], "label": "глава 1",
                          "text": "раз", "parsed": None, "error": None},
                         str(d))
    TQ.save_chunk_result({"id": 2, "nums": [2], "label": "глава 2",
                          "text": "два", "parsed": None, "error": None},
                         str(d))
    got = TQ.load_chunk_results(str(d))
    assert [r["id"] for r in got] == [1, 2]
    assert [r["text"] for r in got] == ["раз", "два"]
    assert TQ.reset_chunks_dir(str(d), LOG) is True
    assert TQ.load_chunk_results(str(d)) == []
    assert TQ.load_chunk_results(str(tmp_path / "нет")) == []


# ══════════════════════════════════════════════════════════════════════
# main(): сбор глав, бюджет, LLM, отчёт
# ══════════════════════════════════════════════════════════════════════

def make_chapters(tmp_path, n=3):
    """Папки глав 00000_1_x… с chapter.txt + polished.txt."""
    ch = tmp_path / "chapters"
    for i in range(1, n + 1):
        d = ch / f"00000_{i}_x"
        d.mkdir(parents=True)
        (d / "chapter.txt").write_text(
            f"Глава {i}\n\nОригинал {i}.\n", encoding="utf-8")
        (d / "polished.txt").write_text(
            f"Глава {i}\n\nПеревод {i}.\n", encoding="utf-8")
    return ch


def test_main_e2e_report(tmp_path, monkeypatch):
    """Полный прогон: отчёт с технической шапкой и оценкой LLM."""
    make_chapters(tmp_path, 3)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core_stage, "stream_chat_completion",
                        lambda *a, **k: ("**9/10** — отличный перевод", None))
    monkeypatch.setattr(sys, "argv", [
        "translate_quality.py", "--type", "polished",
        "--start", "1", "--end", "3",
        "--host", "http://h", "--model", "m",
        "--budget", "200000"])
    rc = TQ.main()
    assert rc == 0
    out = (tmp_path / "tmp" / "translation_quality_assessment.md")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "Оценка качества перевода" in text
    assert "| Диапазон глав | 1 – 3 |" in text
    assert "| Тип файлов глав | polished |" in text
    assert "**9/10** — отличный перевод" in text


def test_main_budget_trims(tmp_path, monkeypatch):
    """Малый бюджет — в отчёте отсечённые главы."""
    make_chapters(tmp_path, 3)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core_stage, "stream_chat_completion",
                        lambda *a, **k: ("оценка", None))
    monkeypatch.setattr(sys, "argv", [
        "translate_quality.py", "--type", "polished",
        "--start", "1", "--end", "3",
        "--host", "http://h", "--model", "m",
        "--budget", "60"])
    rc = TQ.main()
    assert rc == 0
    text = (tmp_path / "tmp" / "translation_quality_assessment.md").read_text(
        encoding="utf-8")
    assert "отсечено" in text


def test_main_empty_llm_returns_1(tmp_path, monkeypatch):
    """Пустой ответ LLM — код 1, отчёт не пишется."""
    make_chapters(tmp_path, 1)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core_stage, "stream_chat_completion",
                        lambda *a, **k: (None, "пусто"))
    monkeypatch.setattr(sys, "argv", [
        "translate_quality.py", "--start", "1", "--end", "1",
        "--host", "http://h", "--model", "m"])
    assert TQ.main() == 1
    assert not (tmp_path / "tmp" / "translation_quality_assessment.md").exists()


def test_main_no_chapters(tmp_path, monkeypatch):
    """Пустая папка глав — код 1."""
    (tmp_path / "chapters").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "translate_quality.py", "--host", "http://h", "--model", "m"])
    assert TQ.main() == 1


def test_main_custom_output(tmp_path, monkeypatch):
    """CLI: --output задаёт имя отчёта (в web поле убрано — дефолт);
    тег-промпт из файла."""
    make_chapters(tmp_path, 1)
    (tmp_path / "p.txt").write_text(
        "<prompt_assessment>оцени</prompt_assessment>\n",
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core_stage, "stream_chat_completion",
                        lambda *a, **k: ("хорошо", None))
    monkeypatch.setattr(sys, "argv", [
        "translate_quality.py", "--start", "1", "--end", "1",
        "--prompt_file", "p.txt", "--output", "reports/my.md",
        "--host", "http://h", "--model", "m"])
    assert TQ.main() == 0
    assert (tmp_path / "reports" / "my.md").exists()
    assert not (tmp_path / "tmp" / "translation_quality_assessment.md").exists()


# ══════════════════════════════════════════════════════════════════════
# main(): режим chunks
# ══════════════════════════════════════════════════════════════════════

def _chunks_argv(extra=None):
    return (["translate_quality.py", "--mode", "chunks", "--chunk_size", "1",
             "--host", "http://h", "--model", "m"] + list(extra or []))


def test_main_chunks_mode(tmp_path, monkeypatch):
    """Чанки: по запросу на главу, артефакты на диске, отчёт со сводкой."""
    make_chapters(tmp_path, 4)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core_stage, "stream_chat_completion",
                        lambda *a, **k: (ANSWER, None))
    monkeypatch.setattr(sys, "argv", _chunks_argv(["--threads", "2"]))
    assert TQ.main() == 0
    files = sorted(p.name for p in (tmp_path / "tmp" / "quality").iterdir())
    assert files == [f"chunk-{i:03d}.json" for i in range(1, 5)]
    text = (tmp_path / "tmp" / "translation_quality_assessment.md").read_text(
        encoding="utf-8")
    assert "| Режим | чанками (оценка по частям книги) |" in text
    assert "| Чанков | 4 (глав в чанке 1, отбор: все) |" in text
    assert "## Итоговая оценка" in text and "Среднее" in text
    assert "## Приложение: оценки по чанкам" in text and "| 3 | глава 3 |" in text


def test_main_chunks_wipes_stale_artifacts(tmp_path, monkeypatch):
    """Новый запуск затирает старые чанки: диапазоны меняются, старьё мешает."""
    make_chapters(tmp_path, 2)
    stale = tmp_path / "tmp" / "quality"
    stale.mkdir(parents=True)
    (stale / "chunk-099.json").write_text('{"nums": [99]}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core_stage, "stream_chat_completion",
                        lambda *a, **k: (ANSWER, None))
    monkeypatch.setattr(sys, "argv", _chunks_argv())
    assert TQ.main() == 0
    names = sorted(p.name for p in stale.iterdir())
    assert names == ["chunk-001.json", "chunk-002.json"]


def test_main_chunks_partial_failure_keeps_run(tmp_path, monkeypatch):
    """Сбой одного чанка не убивает прогон: он виден в «Чанки с ошибками»."""
    make_chapters(tmp_path, 3)
    monkeypatch.chdir(tmp_path)
    calls = []

    def flaky(*a, **k):
        calls.append(a)
        return (None, "read timeout") if len(calls) == 2 else (ANSWER, None)

    monkeypatch.setattr(core_stage, "stream_chat_completion", flaky)
    monkeypatch.setattr(sys, "argv", _chunks_argv(["--threads", "1"]))
    assert TQ.main() == 0
    text = (tmp_path / "tmp" / "translation_quality_assessment.md").read_text(
        encoding="utf-8")
    assert "### Чанки с ошибками (1)" in text and "read timeout" in text


def test_main_chunks_all_failed_returns_1(tmp_path, monkeypatch):
    """Ни один чанк не оценён — отчёт не пишется, код 1."""
    make_chapters(tmp_path, 2)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core_stage, "stream_chat_completion",
                        lambda *a, **k: (None, "пусто"))
    monkeypatch.setattr(sys, "argv", _chunks_argv())
    assert TQ.main() == 1
    assert not (tmp_path / "tmp" / "translation_quality_assessment.md").exists()


def test_main_chunks_sample_first(tmp_path, monkeypatch):
    """Отбор first + лимит чанков: в дело идут первые N, план — в артефактах."""
    make_chapters(tmp_path, 5)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core_stage, "stream_chat_completion",
                        lambda *a, **k: (ANSWER, None))
    monkeypatch.setattr(sys, "argv",
                        _chunks_argv(["--chunks", "2", "--sample", "first"]))
    assert TQ.main() == 0
    text = (tmp_path / "tmp" / "translation_quality_assessment.md").read_text(
        encoding="utf-8")
    assert "отбор: первые по порядку" in text
    names = sorted(p.name for p in (tmp_path / "tmp" / "quality").iterdir())
    assert names == ["chunk-001.json", "chunk-002.json"]


def test_main_chunks_summary_only(tmp_path, monkeypatch):
    """--summary-only: только свёртка, ни одного запроса по чанкам."""
    make_chapters(tmp_path, 3)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core_stage, "stream_chat_completion",
                        lambda *a, **k: (ANSWER, None))
    monkeypatch.setattr(sys, "argv", _chunks_argv())
    assert TQ.main() == 0

    labels = []

    def only_summary(*args, **kwargs):
        labels.append(kwargs.get("label") or "")
        return "**9/10** — хорошо", None

    monkeypatch.setattr(core_stage, "stream_chat_completion", only_summary)
    monkeypatch.setattr(sys, "argv", _chunks_argv(["--summary-only"]))
    assert TQ.main() == 0
    assert labels == ["[свёртка → заключение]"]
    text = (tmp_path / "tmp" / "translation_quality_assessment.md").read_text(
        encoding="utf-8")
    assert "Свёртка" in text and "**9/10** — хорошо" in text


def test_main_chunks_summary_only_without_artifacts(tmp_path, monkeypatch):
    """--summary-only без архива чанков — код 1, отчёт не пишется."""
    make_chapters(tmp_path, 2)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core_stage, "stream_chat_completion",
                        lambda *a, **k: (ANSWER, None))
    monkeypatch.setattr(sys, "argv", _chunks_argv(["--summary-only"]))
    assert TQ.main() == 1
    assert not (tmp_path / "tmp" / "translation_quality_assessment.md").exists()


def test_main_chunks_preview_request_shows_plan_and_two_requests(tmp_path,
                                                                monkeypatch):
    """--preview-request: план чанков + запрос чанка + запрос свёртки, без сети."""
    import json

    make_chapters(tmp_path, 3)
    monkeypatch.chdir(tmp_path)

    def boom(*a, **k):
        raise AssertionError("предпросмотр не ходит в сеть")

    monkeypatch.setattr(core_stage, "stream_chat_completion", boom)
    monkeypatch.setattr(sys, "argv", _chunks_argv(
        ["--preview-request", "tmp/preview_request.json"]))
    assert TQ.main() == 0
    data = json.loads((tmp_path / "tmp" / "preview_request.json").read_text(
        encoding="utf-8"))
    assert data["stage"] == "translate_quality"
    assert data["meta"]["чанков"] == 3 and data["meta"]["оценивается"] == 3
    assert data["meta"]["отбор"] == "все"
    assert data["meta"]["потоки"] == 4 and data["meta"]["глав в чанке"] == 1
    reqs = data["requests"]
    assert [r["label"] for r in reqs] == ["Оценка · чанк 1/3",
                                          "Свёртка отчётов → заключение"]
    chunk = reqs[0]["messages"][-1]["content"]
    assert "## ОРИГИНАЛ" in chunk and "## ПЕРЕВОД" in chunk
    assert "Перевод 1" in chunk and "Оригинал 1" in chunk
    # свёртка: один батч со сводками всех чанков (заглушка с баллами)
    summary = reqs[1]["messages"][-1]["content"]
    assert "### глава 1" in summary and "общий балл: 8.5" in summary
    assert "{batch_text}" not in summary
    assert not (tmp_path / "tmp" / "translation_quality_assessment.md").exists()


def test_build_chunks_report_sections():
    """Отчёт чанков: шапка, баллы по разделам,merged замечания, приложение."""
    meta = {
        "date": "d", "range_included": (1, 4), "chapters": 4,
        "file_type": "polished", "budget": 200000, "chunk_size": 1,
        "sample": "uniform", "threads": 4, "split_chapters": 0,
        "oversize": [], "model": "m", "host": "h", "prompt_file": "",
    }
    results = _results(2)
    results[1]["error"] = "timeout"
    red = {"requests": 1, "levels": 1, "compression": "без цитат",
           "used": 2, "total": 2, "trimmed": False}
    text = TQ.build_chunks_report(meta, results, "**9/10** — хорошо", red)
    assert "| Режим | чанками (оценка по частям книги) |" in text
    assert "| Раздел | средний балл | чанков |" in text
    assert "| точность | 7.0 | 2 |" in text
    assert "### Замечания (1)" in text
    assert "### Чанки с ошибками (1)" in text and "timeout" in text
    assert "## Заключение" in text and "**9/10** — хорошо" in text
    assert "| 2 | глава 2 |" in text

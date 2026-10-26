#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cli/translate_check.py — адаптивная проверка объёма (медиана+MAD)
и структурные проверки (остальные ветки — в tests/test_cli_e2e.py)."""
# pyright: reportMissingImports=false
import math
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cli"))

import translate_check as TC  # noqa: E402
from core import settings as SET  # noqa: E402
from core.common import count_file_tokens  # noqa: E402


def _write_chapter(d, num, files: dict[str, str]) -> str:
    """Папка главы 00000_{num}_x с файлами; возвращает путь."""
    dd = d / f"00000_{num}_x"
    dd.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (dd / f"{name}.txt").write_text(text, encoding="utf-8")
    return str(dd)


def _body(n: int, header: str = "Глава {n}") -> str:
    """Тело главы с заголовком-числом (для проверки последовательности)."""
    first = header.format(n=n) if "{n}" in header else header
    return f"{first}\n\n" + "русский текст. " * n


# ════════════════════════════════════════════════════════════════════
# Слова-исключения


def test_load_exclusions_default(tmp_path):
    # дефолт убран: пусто = ничего (никаких зашитых VIP, MVP, 【,】, NPC)
    assert TC.load_exclusions() == []
    assert not hasattr(TC, "DEFAULT_EXCLUSIONS")
    # пустой .env → тоже ничего
    f = tmp_path / ".env"
    f.write_text("", encoding="utf-8")
    assert TC.load_exclusions(str(f)) == []


def test_load_exclusions_from_env(tmp_path):
    f = tmp_path / ".env"
    f.write_text("TRANSLATE_CHECK_EXCLUDE_WORDS=АЛЬФА,бета\n",
                 encoding="utf-8")
    assert TC.load_exclusions(str(f)) == ["альфа", "бета"]


def test_check_chapter_exclusions(tmp_path):
    """Слово из exclusions не даёт ошибку; без — ошибка латиницы."""
    d = _write_chapter(tmp_path, 1, {"polished": "Глава 1\n\nслово VIP и нормальный русский текст. " * 60,
                                     "redacted": "Глава 1\n\nслово VIP и нормальный русский текст. " * 60})
    latin = TC.parse_regexp_rule("[a-zA-Z]+")
    # без исключений — VIP даёт ошибку латиницы
    errors, _ = TC.check_chapter(1, d, "polished", [("redacted", "neighbor")],
                                 False, None, exclusions=[],
                                 regexp_checks=[latin])
    assert any("VIP" in e for e in errors)
    # с исключением — чисто
    errors, _ = TC.check_chapter(1, d, "polished", [("redacted", "neighbor")],
                                 False, None, exclusions=["vip"],
                                 regexp_checks=[latin])
    assert not any("VIP" in e for e in errors)


# ════════════════════════════════════════════════════════════════════
# Пары сравнения по цепочке стадий


def test_comparisons_for():
    """Цепочка polished → redacted → translated → chapter.

    Порядок пар — от ближайшей стадии к оригиналу. Запасная стадия (вторая
    в цепочке) нужна там, где соседней у части глав нет; пара с оригиналом
    есть только когда в книге есть chapter.
    """
    assert TC.comparisons_for("polished") == [
        ("redacted", "stage"), ("translated", "stage"),
        ("chapter", "original")]
    assert TC.comparisons_for("redacted") == [
        ("translated", "stage"), ("chapter", "original")]
    assert TC.comparisons_for("translated") == [("chapter", "original")]
    # набора файлов книги не хватает — лишних пар нет
    assert TC.comparisons_for("polished", ("polished", "redacted")) == [
        ("redacted", "stage")]
    assert TC.comparisons_for("polished", ()) == []


def test_present_from_files():
    """Набор типов берётся из собранного прохода, а не из второго обхода."""
    files = {1: {"polished": "p", "chapter": "c"},
             2: {"polished": "p", "redacted": "r", "chapter": "c"}}
    assert TC.present_from(files) == ("polished", "redacted", "chapter")


# ════════════════════════════════════════════════════════════════════
# Статистика пар по всей книге


def _stats(files, counts, tol=5.0, check_type="polished", tol_original=None):
    """Статистика пар: допуск в ПРОЦЕНТАХ (по умолчанию один на обе группы)."""
    comps = TC.comparisons_for(check_type)
    return TC.collect_stats(files, check_type, comps, counts, tol,
                            tol if tol_original is None else tol_original)


def test_collect_stats_log_center_and_sigma():
    """Среднее — медиана отношений, разброс — MAD логарифмов × 1.4826.

    Отношение мультипликативно: 0.9 и 1.111 — одно и то же отклонение,
    поэтому «среднее × tol⁻¹…среднее × tol» одинаково строг в обе стороны.
    """
    files = {i: {"polished": f"p{i}", "redacted": f"r{i}",
                 "chapter": f"c{i}"} for i in range(1, 31)}
    counts = {f"p{i}": 1000 for i in range(1, 31)}
    counts.update({f"r{i}": 1000 for i in range(1, 31)})
    counts.update({f"c{i}": 600 for i in range(1, 31)})
    st = _stats(files, counts)
    assert st["redacted"]["n"] == 30
    assert st["redacted"]["center"] == pytest.approx(1.0)
    assert st["redacted"]["sigma"] == pytest.approx(0.0)
    # допуск — процент из реестра, а не произведение на разброс
    assert st["redacted"]["tol_pct"] == 5.0
    assert st["redacted"]["tol"] == pytest.approx(math.log(1.05))
    assert st["chapter"]["center"] == pytest.approx(1000 / 600)
    lo, hi = TC.bounds(st["chapter"])
    assert (lo, hi) == pytest.approx((1000 / 600 / 1.05, 1000 / 600 * 1.05))


def test_collect_stats_two_knobs():
    """У пар соседних стадий и у пар с оригиналом своя ручка в процентах.

    Разброс книги тут одинаков в обеих группах (иначе тест не про ручки),
    поэтому разница допусков — ровно выбранные проценты.
    """
    files, counts = {}, {}
    for i in range(1, 21):
        files[i] = {"polished": f"p{i}", "redacted": f"r{i}",
                    "chapter": f"c{i}"}
        counts[f"p{i}"] = 1000 + (i % 2) * 150
        counts[f"r{i}"] = 1000
        counts[f"c{i}"] = 1000
    st = _stats(files, counts, tol=3.0, tol_original=12.0)
    assert st["redacted"]["tol_pct"] == 3.0
    assert st["chapter"]["tol_pct"] == 12.0
    assert st["redacted"]["sigma"] == pytest.approx(
        st["chapter"]["sigma"], abs=1e-9)
    assert st["redacted"]["tol"] == pytest.approx(math.log(1.03))
    assert st["chapter"]["tol"] == pytest.approx(math.log(1.12))


def test_collect_stats_tolerance_is_percent_not_spread():
    """Разброс книги на допуск НЕ влияет: он задан процентом.

    Та же книга с широким разбросом отношений и с нулевым — один допуск;
    σ при этом разная и печатается справочно.
    """
    files, counts = {}, {}
    for i in range(1, 21):
        files[i] = {"polished": f"p{i}", "chapter": f"c{i}"}
        counts[f"p{i}"] = 1000 + (i % 2) * 150  # разброс 15%
        counts[f"c{i}"] = 1000
    st = _stats(files, counts)
    assert st["chapter"]["tol"] == pytest.approx(math.log(1.05))
    sigma = st["chapter"]["sigma"]
    assert sigma > 0.05  # разброс книги измерен и напечатается в отчёте
    # «среднее ±5%» далеко не покрывает такую книгу — и это ожидаемо:
    # расширять допуск будет человек, а не статистика
    assert sigma > st["chapter"]["tol"]


def test_collect_stats_n0_n1():
    """n=0 и n=1: пара без статистики, помечается в шапке отчёта."""
    st = _stats({}, {})
    assert st["chapter"]["n"] == 0
    assert not TC.active(st["chapter"])
    assert TC.stats_line("polished", "chapter", st["chapter"]) == ""
    files = {1: {"polished": "p1", "chapter": "c1"}}
    st1 = _stats(files, {"p1": 100, "c1": 60})
    assert st1["chapter"]["n"] == 1
    assert not TC.active(st1["chapter"])


def test_collect_stats_off_when_tolerance_zero():
    """Допуск 0 — пара выключена, но из отчёта не исчезает: шапка показывает
    её выключенной, а не молчит о ней."""
    files = {i: {"polished": f"p{i}", "redacted": f"r{i}"} for i in (1, 2, 3)}
    counts = {f"p{i}": 100 for i in (1, 2, 3)}
    counts.update({f"r{i}": 100 for i in (1, 2, 3)})
    st = TC.collect_stats(files, "polished", TC.comparisons_for("polished"),
                          counts, 0.0, 5.0)
    assert st["redacted"]["off"] is True and not TC.active(st["redacted"])
    assert st["chapter"]["off"] is False
    line = TC.stats_line("polished", "redacted", st["redacted"])
    assert "выключено" in line and "допуск 0%" in line


def test_collect_stats_skips_empty_files():
    """Пустые файлы в базу не попадают."""
    files = {i: {"polished": f"p{i}", "chapter": f"c{i}"} for i in range(1, 11)}
    counts = {f"p{i}": 1000 for i in range(1, 11)}
    counts.update({f"c{i}": 0 for i in range(1, 6)})  # эталон пуст — пара мимо
    counts.update({f"c{i}": 600 for i in range(6, 11)})
    st = _stats(files, counts)
    assert st["chapter"]["n"] == 5


def test_collect_stats_partial_stage():
    """Промежуточной стадии нет у части глав — у пары своя база.

    У главы с redacted соседней служит redacted (translated в базу
    polished↔translated не попадает, хотя файл у главы есть), у главы без
    redacted — translated. Пара с оригиналом набирает всю книгу.
    """
    files = {i: {"polished": f"p{i}", "redacted": f"r{i}", "translated": f"t{i}",
                 "chapter": f"c{i}"} for i in range(1, 21)}
    counts = {f"p{i}": 1000 for i in range(1, 21)}
    counts.update({f"r{i}": 1000 for i in range(1, 11)})  # redact у половины
    counts.update({f"t{i}": 1000 for i in range(1, 21)})
    counts.update({f"c{i}": 600 for i in range(1, 21)})
    st = _stats(files, counts)
    assert st["redacted"]["n"] == 10
    assert st["translated"]["n"] == 10  # только главы без redacted
    assert st["chapter"]["n"] == 20


def test_collect_stats_broken_median_robust():
    """Битые главы не двигают медиану, пока их меньше половины базы."""
    files, counts = {}, {}
    for i in range(1, 21):
        files[i] = {"polished": f"p{i}", "chapter": f"c{i}"}
        counts[f"p{i}"] = 632 if i <= 3 else 1341  # 3 битых из 20
        counts[f"c{i}"] = 1000
    st = _stats(files, counts)
    assert st["chapter"]["center"] == pytest.approx(1.341)


def test_scan_files_reads_whole_chain(tmp_path):
    """Проход ищет все типы цепочки: запасная стадия нужна для глав без
    соседней, а не для отчёта."""
    _write_chapter(tmp_path, 1, {"polished": "текст", "translated": "текст"})
    _write_chapter(tmp_path, 2, {"polished": "текст", "redacted": "текст"})
    cmap = __import__("core.common", fromlist=["build_chapter_map"]) \
        .build_chapter_map(str(tmp_path))
    files = TC.scan_files(cmap, "polished", TC.comparisons_for("polished"),
                          strict=True)
    assert set(files[1]) == {"polished", "translated"}
    assert set(files[2]) == {"polished", "redacted"}
    assert TC.present_from(files) == ("polished", "redacted", "translated")


def test_scan_files_skips_duplicates(tmp_path):
    """Дубли папок в статистику не попадают."""
    _write_chapter(tmp_path, 1, {"polished": "текст", "redacted": "текст"})
    # дубль папки той же главы
    d2 = tmp_path / "00000_1_дубль"
    d2.mkdir()
    (d2 / "polished.txt").write_text("текст", encoding="utf-8")
    (d2 / "redacted.txt").write_text("текст", encoding="utf-8")
    _write_chapter(tmp_path, 2, {"polished": "текст", "redacted": "текст"})
    cmap = __import__("core.common", fromlist=["build_chapter_map"]) \
        .build_chapter_map(str(tmp_path))
    files = TC.scan_files(cmap, "polished", TC.comparisons_for("polished"),
                          strict=True)
    assert 1 not in files and 2 in files


def test_stats_line_shows_bounds():
    """Строка пары называет ГРАНИЦЫ отношения при текущем допуске — не
    проценты, которые приходится пересчитывать в голове; разброс книги
    печатается справочно."""
    st = {"n": 100, "center": 1.0, "sigma": 0.004, "tol_pct": 10.0,
          "tol": math.log(1.1), "off": False}
    line = TC.stats_line("polished", "redacted", st)
    assert "0.909–1.100" in line, line
    assert "допуск ±10%" in line and "разброс книги 0.40%" in line, line
    assert "K=" not in line, line
    # малая база ничем не отличается: допуск тот же процент, подписи о
    # «разброс не учитывается» больше нет
    small = dict(st, n=3)
    assert "допуск ±10%" in TC.stats_line("polished", "chapter", small)


def test_bounds_multiplicative():
    """Границы раздвигаются умножением: 0.9 и 1.111 — одно отклонение."""
    st = {"center": 1.0, "tol": math.log(1.1)}
    lo, hi = TC.bounds(st)
    assert lo == pytest.approx(1 / 1.1) and hi == pytest.approx(1.1)


def _err(st, check_n, ref_n) -> str:
    """Строка объёмной ошибки: тест ждёт её, а не None."""
    got = TC.volume_error("polished", "redacted", check_n, ref_n, st)
    assert got is not None
    return got


def test_volume_error_bounds():
    """Отношение внутри допуска — None; вне — ошибка с числами."""
    st = {"center": 1.0, "sigma": 0.0, "tol_pct": 10.0, "tol": 0.1,
          "n": 30, "off": False}
    assert TC.volume_error("polished", "redacted", 1000, 1000, st) is None
    assert "1.200" in _err(st, 1200, 1000)
    # пустой файл не объёмная ошибка (ловит минимальный размер)
    assert TC.volume_error("polished", "redacted", 0, 1000, st) is None
    assert TC.volume_error("polished", "redacted", 1000, 0, st) is None
    # выключенная пара не проверяется
    assert TC.volume_error("polished", "redacted", 1200, 1000,
                           dict(st, off=True)) is None


def test_volume_error_no_token_noise_floor():
    """Порога «мин. отличие в токенах» больше нет: короткая глава с
    отклонением в несколько токенов тоже флагуется.

    3040 против 3000 — это 1.3% при допуске 1%: раньше такое гасил порог
    150 токенов, теперь — нет.
    """
    st = {"center": 1.0, "sigma": 0.0, "tol": 0.01, "tol_pct": 1.0,
          "n": 30, "off": False}
    err = TC.volume_error("polished", "redacted", 3040, 3000, st)
    assert err is not None and "1.013" in err, err
    # внутри допуска — тишина, как и раньше
    assert TC.volume_error("polished", "redacted", 3020, 3000, st) is None


def test_volume_error_names_bounds_and_tolerance():
    """В строке — границы, допуск в процентах и среднее книги: по ним
    видно, править главу или ослабить допуск."""
    st = {"center": 0.999, "sigma": 0.01, "tol_pct": 10.0,
          "tol": math.log(1.1), "n": 100, "off": False}
    err = _err(st, 1469, 1637)
    assert "вне 0.908–1.099 при допуске ±10%" in err, err
    assert "среднее книги 0.999" in err, err
    assert "K" not in err, err


def test_volume_error_hint_free():
    """Подсказок «пройдёт при K ≥ N» и «правьте главу» больше нет: ручки
    объявлены в процентах, и советовать коэффициент нечем."""
    st = {"center": 1.0, "sigma": 0.001, "tol_pct": 5.0,
          "tol": math.log(1.05), "n": 601, "off": False}
    err = _err(st, 600, 6000)  # глава в 10 раз короче обычного
    assert "пройдёт при" not in err and "правьте главу" not in err, err
    assert "±5%" in err, err


# ════════════════════════════════════════════════════════════════════
# Объёмная проверка главы: допуск пары из статистики


def _row(d: str, types) -> dict:
    """Строка scan_files для папки главы: {тип: путь}."""
    return {t: f"{d}/{t}.txt" for t in types}


def _synth_book(tmp_path, n: int, pol: int = 60, ch: int = 100):
    """Синтетическая книга: n глав, {тип: путь}; счёт — estimate."""
    files = {}
    for i in range(1, n + 1):
        d = _write_chapter(tmp_path, i, {"polished": _body(pol),
                                         "redacted": _body(pol),
                                         "chapter": _body(ch)})
        files[i] = _row(d, ("polished", "redacted", "chapter"))
    counts = count_file_tokens(sorted({p for row in files.values()
                                       for p in row.values()}))
    return files, counts


def test_check_chapter_volume_caught(tmp_path):
    """Обрезанная глава ловится: отношение вне допуска пары."""
    files, counts = _synth_book(tmp_path, 20)
    comps = TC.comparisons_for("polished")
    stats = TC.collect_stats(files, "polished", comps, counts, 5.0, 5.0)
    # глава 21: polished обрезан вдвое
    broken = _write_chapter(tmp_path, 21, {"polished": _body(30),
                                           "redacted": _body(60),
                                           "chapter": _body(100)})
    files[21] = _row(broken, ("polished", "redacted", "chapter"))
    counts.update(count_file_tokens(
        [f"{broken}/polished.txt", f"{broken}/redacted.txt"]))
    errors, _ = TC.check_chapter(21, broken, "polished", comps, False, None,
                                 exclusions=[], counts=counts, stats=stats)
    assert any("отношение объёма" in e for e in errors)


def test_check_chapter_volume_ok(tmp_path):
    """Нормальные главы чисты."""
    files, counts = _synth_book(tmp_path, 10)
    comps = TC.comparisons_for("polished")
    stats = TC.collect_stats(files, "polished", comps, counts, 5.0, 5.0)
    errors, _ = TC.check_chapter(5, "00000_5_x", "polished", comps, False,
                                 None, exclusions=[], counts=counts,
                                 stats=stats)
    assert not any("отношение объёма" in e for e in errors)


def test_check_chapter_volume_off(tmp_path):
    """stats=None (обе чувствительности в нуле): сравнения выключены."""
    d = _write_chapter(tmp_path, 1, {"polished": "коротко",
                                     "redacted": "текст"})
    errors, _ = TC.check_chapter(1, d, "polished",
                                 [("redacted", "stage")], False, None,
                                 exclusions=[], counts=None, stats=None)
    assert not any("отношение объёма" in e for e in errors)


def test_check_chapter_volume_no_stats(tmp_path):
    """Базы нет (n<2): объёмная проверка пропускается, помечена в шапке."""
    d = _write_chapter(tmp_path, 1, {"polished": "текст", "redacted": "текст"})
    errors, _ = TC.check_chapter(1, d, "polished",
                                 [("redacted", "stage")], False, None,
                                 exclusions=[], counts={"x": 1},
                                 stats={"redacted": {"n": 0}})
    assert not any("отношение объёма" in e for e in errors)


def test_check_chapter_no_reference_is_silent(tmp_path):
    """Нет промежуточной стадии у главы — пары просто нет: сообщения
    «эталон не найден» в отчёте не появляется."""
    d = _write_chapter(tmp_path, 1, {"polished": _body(60),
                                     "translated": _body(60)})
    comps = TC.comparisons_for("polished", ("polished", "redacted",
                                           "translated"))
    st = {"n": 30, "center": 1.0, "sigma": 0.0, "tol_pct": 0.5,
          "tol": math.log(1.005), "off": False}
    stats = {"redacted": dict(st), "translated": dict(st)}
    counts = count_file_tokens([f"{d}/polished.txt", f"{d}/translated.txt"])
    errors, _ = TC.check_chapter(1, d, "polished", comps, False, None,
                                 exclusions=[], counts=counts, stats=stats)
    assert not any("не найден" in e or "эталон" in e for e in errors)


def test_check_chapter_fallback_stage_pair(tmp_path):
    """redacted у главы нет — сравнение идёт с translated (запасная стадия),
    и ошибка называет именно эту пару."""
    d = _write_chapter(tmp_path, 1, {"polished": _body(20),
                                     "translated": _body(60)})
    comps = TC.comparisons_for("polished", ("polished", "redacted",
                                           "translated"))
    st = {"n": 30, "center": 1.0, "sigma": 0.0, "tol_pct": 0.5,
          "tol": math.log(1.005), "off": False}
    stats = {"redacted": dict(st), "translated": dict(st)}
    counts = count_file_tokens([f"{d}/polished.txt", f"{d}/translated.txt"])
    errors, _ = TC.check_chapter(1, d, "polished", comps, False, None,
                                 exclusions=[], counts=counts, stats=stats)
    assert any("[polished vs translated]" in e for e in errors), errors


def test_check_chapter_original_not_repeated(tmp_path):
    """Глава разошлась с соседней стадией — про оригинал второй раз не
    сообщаем: сдвиг один, и он уже назван."""
    d = _write_chapter(tmp_path, 1, {"polished": _body(20),
                                     "redacted": _body(60),
                                     "chapter": _body(100)})
    comps = TC.comparisons_for("polished", ("polished", "redacted", "chapter"))
    st = {"n": 30, "center": 1.0, "sigma": 0.0, "tol_pct": 0.5,
          "tol": math.log(1.005), "off": False}
    orig = {"n": 30, "center": 1.0, "sigma": 0.0, "tol_pct": 0.5,
            "tol": math.log(1.005), "off": False}
    stats = {"redacted": dict(st), "chapter": orig}
    counts = count_file_tokens([f"{d}/{t}.txt"
                                for t in ("polished", "redacted", "chapter")])
    errors, _ = TC.check_chapter(1, d, "polished", comps, False, None,
                                 exclusions=[], counts=counts, stats=stats)
    assert any("[polished vs redacted]" in e for e in errors), errors
    assert not any("[polished vs chapter]" in e for e in errors), errors


def test_check_chapter_original_blamed_when_chain_clean(tmp_path):
    """Цепочка ровная, а оригинал вне допуска — так и пишем: правьте
    оригинал (термин, а не текст перевода)."""
    d = _write_chapter(tmp_path, 1, {"polished": _body(60),
                                     "redacted": _body(60),
                                     "chapter": _body(20)})
    comps = TC.comparisons_for("polished", ("polished", "redacted", "chapter"))
    st = {"n": 30, "center": 1.0, "sigma": 0.0, "tol_pct": 0.5,
          "tol": math.log(1.005), "off": False}
    orig = {"n": 30, "center": 1.0, "sigma": 0.0, "tol_pct": 5.0,
            "tol": math.log(1.05), "off": False}
    stats = {"redacted": dict(st), "chapter": orig}
    counts = count_file_tokens([f"{d}/{t}.txt"
                                for t in ("polished", "redacted", "chapter")])
    errors, _ = TC.check_chapter(1, d, "polished", comps, False, None,
                                 exclusions=[], counts=counts, stats=stats)
    hit = [e for e in errors if "[polished vs chapter]" in e]
    assert hit and "правьте оригинал" in hit[0], errors


# ════════════════════════════════════════════════════════════════════
# Regexp-проверки (без изменений)


def test_check_chapter_regexp_checks(tmp_path):
    """regexp-проверки: пусто = без проверок; явный набор — всё
    найденное — ошибка."""
    d = _write_chapter(tmp_path, 2, {"polished": (
        "Глава 2\n\nнормальный русский текст. " * 40
        + "\n\nГлава 99\nещё текст. " * 40)})
    comps = []
    # пусто = без проверок: даже лишняя «Глава 99» не ошибка
    errors, prev = TC.check_chapter(2, d, "polished", comps,
                                    False, None, exclusions=[])
    assert prev == 2
    assert not any("По паттерну" in e for e in errors)
    # явный набор (как в предзаполнении web-формы): лишняя «Глава 99»
    # ловится lookbehind-правилом, заголовок в первой строке — нет
    checks = [TC.parse_regexp_rule(l) for l in
              ("(?<=\\n)\\s*Глава\\s+\\d+", "[一-鿿]+", "[a-zA-Z]+")]
    errors, _ = TC.check_chapter(2, d, "polished", comps,
                                 False, None, exclusions=[],
                                 regexp_checks=[c for c in checks if c])
    assert any("Глава 99" in e for e in errors)
    assert not any(e.rstrip().endswith("Глава 2") for e in errors)
    # кастомный паттерн: только иероглифы
    rx = re.compile(r"[一-鿿]+", re.MULTILINE)
    clean = ("Глава 2\n\nнормальный русский текст. " * 40
             + "\n\n99\nещё текст. " * 40 + "中文")
    (Path(d) / "polished.txt").write_text(clean, encoding="utf-8")
    errors, _ = TC.check_chapter(2, d, "polished", comps,
                                 False, None, exclusions=[],
                                 regexp_checks=[rx])
    assert any("中文" in e for e in errors)


def _rule(line: str):
    """parse_regexp_rule с guard на None (атрибуты читаем у not-None)."""
    rx = TC.parse_regexp_rule(line)
    assert rx is not None, f"правило не распарсилось: {line!r}"
    return rx


def test_parse_regexp_rule():
    """Строка правила → compiled | None: пустая/пробельная строка —
    None, остальное компилируется КАК ЕСТЬ (чистый стандартный
    regexp, MULTILINE; без срезания комментариев и флагов)."""
    rx = _rule("^Глава\\s+\\d+")
    assert rx.pattern == "^Глава\\s+\\d+" and rx.flags & re.MULTILINE
    # « |s» и « # …» больше не special: остаются частью паттерна
    assert _rule("^Глава\\s+\\d+ |s").pattern == "^Глава\\s+\\d+ |s"
    assert _rule("^Глава\\d+ # заголовки").pattern == "^Глава\\d+ # заголовки"
    assert TC.parse_regexp_rule("") is None
    assert TC.parse_regexp_rule("   ") is None


def test_check_chapter_regexp_skip_first(tmp_path):
    """«Пропуск первого вхождения» — стандартным lookbehind (?<=\n):
    лишние заголовки своими руками; вхождение в 1-й строке файла
    не совпадает."""
    d = _write_chapter(tmp_path, 2, {"polished": (
        "Глава 2\n\nнормальный русский текст. " * 40
        + "\n\nГлава 99\nещё текст. " * 40)})
    comps = []
    rx = TC.parse_regexp_rule("(?<=\\n)\\s*Глава\\s+\\d+")
    errors, prev = TC.check_chapter(2, d, "polished", comps,
                                    False, None, exclusions=[],
                                    regexp_checks=[rx])
    assert prev == 2
    assert any("Глава 99" in e for e in errors)
    assert any("По паттерну" in e for e in errors)
    assert not any("Глава 2" in e for e in errors)
    # тот же паттерн без lookbehind: помечается и заголовок главы
    rx2 = TC.parse_regexp_rule("^\\s*Глава\\s+\\d+")
    errors, _ = TC.check_chapter(2, d, "polished", comps,
                                 False, None, exclusions=[],
                                 regexp_checks=[rx2])
    assert any("Глава 2" in e for e in errors)
    assert any("Глава 99" in e for e in errors)


def test_check_chapter_no_header(tmp_path):
    """Проверки заголовка в коде больше нет: «первая строка =
    заголовок» — regexp-правило пользователя (negative lookahead).
    Без правил — ошибок нет; с правилом — есть."""
    d = _write_chapter(tmp_path, 3, {"polished": "просто текст без заголовка. " * 60})
    # без правил — чисто, prev не обновлён (числа в первой строке нет)
    errors, prev = TC.check_chapter(3, d, "polished", [],
                                    False, None, exclusions=[])
    assert not any("По паттерну" in e for e in errors)
    assert prev is None
    # идиома из справки: первая строка не «Глава N» → ошибка
    rx = TC.parse_regexp_rule(r"\A(?!\s*Глава\s+(\d+|\[Номер\])).+")
    errors, _ = TC.check_chapter(3, d, "polished", [],
                                 False, None, exclusions=[],
                                 regexp_checks=[rx])
    assert any("По паттерну" in e for e in errors)


# ════════════════════════════════════════════════════════════════════
# Структурные проверки: --min-file-size / --no-sequence-check


def test_check_chapter_min_file_size(tmp_path):
    """Минимальный размер файла — настраиваемый (БАЙТЫ)."""
    d = _write_chapter(tmp_path, 4, {"polished": "Глава 4\n\nкороткий текст",
                                     "redacted": "Глава 4\n\nкороткий текст"})
    comps = [("redacted", "neighbor")]
    # дефолт 3072 — файл мал → ошибка
    errors, _ = TC.check_chapter(4, d, "polished", comps,
                                 False, None, exclusions=[])
    assert any("слишком мал" in e for e in errors)
    # меньший минимум — ошибки нет
    errors, _ = TC.check_chapter(4, d, "polished", comps,
                                 False, None, exclusions=[],
                                 min_file_size=10)
    assert not any("слишком мал" in e for e in errors)


def test_check_chapter_header_canon_only(tmp_path):
    """Канона заголовка в коде больше нет: нестандартный «Раздел 5» —
    не ошибка (если пользователь не задал своё regexp-правило);
    число из первой строки участвует в последовательности."""
    d = _write_chapter(tmp_path, 5, {"polished": "Раздел 5\n\n" + "русский текст. " * 100,
                                     "redacted": "Раздел 5\n\n" + "русский текст. " * 100})
    comps = [("redacted", "neighbor")]
    errors, prev = TC.check_chapter(5, d, "polished", comps,
                                    False, None, exclusions=[])
    assert not any("Нет «Глава N»" in e for e in errors)
    assert prev == 5  # число из первой строки накапливается


def test_check_chapter_sequence_exact_next(tmp_path):
    """Последовательность: первое число первой непустой строки должно
    быть ровно на 1 больше предыдущего (N+1)."""
    d = tmp_path / "00000_6_x"
    d.mkdir()
    comps = [("redacted", "neighbor")]

    def run(chapter_line, prev):
        body = chapter_line + "\n\n" + "русский текст. " * 100
        (d / "polished.txt").write_text(body, encoding="utf-8")
        (d / "redacted.txt").write_text(body, encoding="utf-8")
        return TC.check_chapter(6, str(d), "polished", comps,
                                False, prev, exclusions=[])

    # 6 после 5 — ровно N+1, ошибки нет
    errors, prev = run("Глава 6", 5)
    assert not any("последовательность" in e for e in errors)
    assert prev == 6
    # пропуск нумерации (7 после 5) — ошибка: ожидалась 6
    errors, prev = run("Глава 7", 5)
    assert any("последовательность" in e for e in errors)
    assert prev == 7
    # 1 после 5 — ошибка
    errors, prev = run("Глава 1", 5)
    assert any("последовательность" in e for e in errors)
    assert prev == 1
    # равный номер — ошибка
    errors, _ = run("Глава 5", 5)
    assert any("последовательность" in e for e in errors)


def test_check_chapter_sequence_off(tmp_path):
    """--no-sequence-check: сбой последовательности не ошибка, номер
    не накапливается (prev остаётся входным)."""
    d = _write_chapter(tmp_path, 7, {"polished": "Глава 1\n\n" + "русский текст. " * 100,
                                     "redacted": "Глава 1\n\n" + "русский текст. " * 100})
    comps = [("redacted", "neighbor")]
    errors, prev = TC.check_chapter(7, d, "polished", comps,
                                    False, 5, exclusions=[],
                                    sequence_check=False)
    assert not any("последовательность" in e for e in errors)
    assert prev == 5  # номер не накапливается


def test_check_chapter_sequence_no_number_in_first_line(tmp_path):
    """Первая непустая строка без числа: prev не обновляется —
    следующая глава сравнивается с последним найденным номером."""
    d = _write_chapter(tmp_path, 8, {"polished": "Пролог\n\n" + "русский текст. " * 100,
                                     "redacted": "Пролог\n\n" + "русский текст. " * 100})
    comps = [("redacted", "neighbor")]
    errors, prev = TC.check_chapter(8, d, "polished", comps,
                                    False, 5, exclusions=[])
    assert not any("последовательность" in e for e in errors)
    assert prev == 5  # числа нет — prev не изменился


# ════════════════════════════════════════════════════════════════════
# Предзаполнение regexp-проверок из реестра: набор обязан ловить ЛЮБУЮ
# чужую азбуку — галлюцинация модели это токен из другого языка внутри
# русской фразы («Он walk домой и думал, чтобы ему sделать там.»), — и
# молчать на русской азбуке. Кириллица чужих раскладок (украинская,
# белорусская, сербская, македонская, старославянская) — тоже чужая:
# і, ї, є, ґ и ў в русскую азбуку не входят и ловятся своим правилом.


def _default_rules() -> list:
    """Правила из дефолта реестра — те же строки, что видит форма."""
    raw = SET.BY_KEY["TRANSLATE_CHECK_REGEXP_CHECKS"].default
    return [_rule(l) for l in raw.splitlines() if l.strip()]


@pytest.mark.parametrize("text, expected", [
    ("Он walk домой и думал, чтобы ему sделать там.", "walk"),
    ("Он ｗａｌｋ домой", "ｗａｌｋ"),              # полноширинная латиница
    ("Он ишёл домой и thinking об этом", "thinking"),
    ("Канат 彼 home и пошёл", "彼"),                   # иероглиф CJK
    ("Канат は home и пошёл", "は"),                   # хирагана
    ("Канат カタカナ home", "カタカナ"),           # катакана
    ("Канат 한국 home и пошёл", "한국"),           # хангыль
    ("Канат και home и пошёл", "και"),                # греческий
    ("Канат שלום home и пошёл", "שלום"),             # еврейский
    ("Канат مرحبا home и пошёл", "مرحبا"),           # арабский
    ("Канат গান home и пошёл", "গান"),           # бенгальский
    ("Канат กาด home และ пошла", "กาด"),         # тайский
    ("Канат გe home", "გe"),                # грузинский в слове латиницей
    ("текст с кавычками 「」", "「"),
    # чужие кириллические раскладки: букв русской азбуки среди них нет
    ("Він ішов додому, їжа є", "і"),
    ("Він ішов додому, їжа є", "ї"),
    ("Він ішов додому, їжа є", "є"),
    ("Українське ґанок ловится", "ґ"),
    ("Белорусский ў і словенščina", "ў"),
    ("Српски: једна љубав, његов ћерка", "ј"),
    ("Македонски: ѓакон, ѕвон, ќерка, џеп", "ѕ"),
    ("Старославянский ѣль и ѵжица", "ѣ"),
])
def test_default_regexp_checks_catch_foreign_letters(text, expected):
    # «Глава 1» сверху — правило «первая строка не заголовок» ловит не её
    hits = [m.group(0) for rx in _default_rules()
            for m in rx.finditer("Глава 1\n" + text)]
    found = "".join(hits)
    # символы чужого слова обязаны быть в совпадениях: смешанное слово
    # («sделать») ловится двумя правилами — кириллицу и латиницу
    assert all(ch in found for ch in expected), \
        f"{expected!r} не поймано: {hits}"


@pytest.mark.parametrize("text", [
    "Он шёл домой и думал, чтобы ему сделать там.",
    "«Привет» — сказал он… №5; 12:30, 3,14; 100% — ок.",
    "Служебные знаки: °±§¶·¤ — «…»",
])
def test_default_regexp_checks_silent_on_russian(text):
    """Русская азбука (33 буквы вместе с Ё), знаки препинания и цифры
    не должны давать ни одного совпадения."""
    hits = [m.group(0) for rx in _default_rules()
            for m in rx.finditer("Глава 1\n" + text)]
    assert hits == [], f"ложные срабатывания: {hits}"


def test_default_regexp_checks_cover_all_scripts():
    """Набор не усох: заголовок, CJK/кана/хангыль, латиница, остальные
    азбуки, чужие кириллические раскладки и «первая строка — заголовок».
    Отдельные строки — потому что каждое правило подписано в отчёте своим
    паттерном."""
    raw = SET.BY_KEY["TRANSLATE_CHECK_REGEXP_CHECKS"].default
    lines = [l for l in raw.splitlines() if l.strip()]
    assert len(lines) == len(_default_rules()) == 6
    assert any("\\u0452" in l for l in lines), "потеряны чужие кириллические раскладки"
    assert any("\\uac00" in l for l in lines), "потерян хангыль"
    assert any("\\uff41" in l for l in lines), "потеряна полноширинная латиница"
    assert any("\\u0370" in l for l in lines), "потеряны остальные азбуки"


def test_tolerance_settings_prefilled():
    """Ручек две, и они проценты из реестра (0 = пара выключена).

    Дефолты — 5% и 20%: соседние стадии сходятся на доли процента, пары с
    оригиналом расходятся на десятки. Снятые ключи чувствительности и
    «мин. отличия» в реестре не появляются: их смысл и единица сменились.
    """
    stages = SET.BY_KEY["TRANSLATE_CHECK_TOLERANCE_STAGES"]
    original = SET.BY_KEY["TRANSLATE_CHECK_TOLERANCE_ORIGINAL"]
    assert stages.type == "number" and stages.default == "5"
    assert original.type == "number" and original.default == "20"
    for gone in ("TRANSLATE_CHECK_SENSITIVITY_STAGES",
                 "TRANSLATE_CHECK_SENSITIVITY_ORIGINAL",
                 "TRANSLATE_CHECK_MIN_DELTA_TOKENS"):
        assert gone not in SET.BY_KEY, gone
    # зеркало дефолтов в скрипте: свидание с реестром сторожит test_docs
    assert (TC.TOLERANCE_STAGES, TC.TOLERANCE_ORIGINAL) == (5.0, 20.0)
    # цепочка стадий — одна на скрипт и на сравнения
    assert TC.CHAIN == ("polished", "redacted", "translated", "chapter")


def test_default_heading_rule_checks_only_not_first_line():
    """Правило «лишние заголовки» ищет их со ВТОРОЙ строки: lookbehind
    `(?<=[\x0a])` требует предыдущую строку, поэтому заголовок главы в
    первой строке файла не ошибка. Перевод внутри паттерна пишется «\x0a»:
    литерал «\n» в значении — граница строки, и правило разрезало бы
    пополам («(?<=» отдельной строкой)."""
    rule = next(rx for rx in _default_rules()
                if rx.pattern.startswith("(?<="))
    assert rule.pattern.startswith("(?<=[\\x0a])"), \
        f"перевод внутри паттерна должен быть «\\x0a»: {rule.pattern}"
    hits = [m.group(0).strip() for m in rule.finditer("Глава 12\n\nтекст\n\nГлава 13")]
    assert hits == ["Глава 13"], f"лишний заголовок не найден: {hits}"
    assert rule.findall("Глава 12\n\nтекст") == []


# ════════════════════════════════════════════════════════════════════
# Предрасчёт для формы запусков (те же числа, что шапка отчёта)


def test_pair_ratios_keyed_by_chapter(tmp_path):
    """Отношения — по номеру главы: по ним предпросмотр находит выбросы."""
    _write_chapter(tmp_path, 1, {"polished": _body(10), "redacted": _body(10)})
    _write_chapter(tmp_path, 2, {"polished": _body(20), "redacted": _body(10)})
    comps = TC.comparisons_for("polished")
    files = TC.scan_files(TC.build_chapter_map(str(tmp_path)), "polished",
                          comps, True)
    comps = TC.comparisons_for("polished", TC.present_from(files))
    counts = TC.count_file_tokens(
        sorted({p for row in files.values() for p in row.values()}))
    ratios = TC.pair_ratios(files, "polished", comps, counts)
    # перевода и оригинала в книге нет — пар по ним не возникает
    assert set(ratios) == {"redacted"}
    assert set(ratios["redacted"]) == {1, 2}
    assert ratios["redacted"][1] == pytest.approx(1.0, abs=0.05)
    assert ratios["redacted"][2] > 1.3  # вдвое больше текста


def test_outliers_gone_with_precalc():
    """Предрасчёт убран вместе со своими функциями: остались только те,
    которыми работает сам прогон (второй реализации счёта нет)."""
    for gone in ("collect_book_stats", "stats_payloads", "stats_payload",
                 "outliers", "volume_hint", "min_k_for", "MIN_DELTA_TOKENS",
                 "SMALL_N", "FLOORS", "SMALL_FLOORS", "MAX_HINT_K"):
        assert not hasattr(TC, gone), gone


def test_pair_ratios_feed_stats(tmp_path):
    """Отношения глав — база статистики: крайние главы не двигают медиану,
    но выходят за узкий допуск (тот же расчёт, что у прогона)."""
    for num, mul in ((1, 1), (2, 1), (3, 1), (4, 3)):
        _write_chapter(tmp_path, num,
                       {"polished": _body(100 * mul), "redacted": _body(100)})
    cmap = TC.build_chapter_map(str(tmp_path))
    comps = TC.comparisons_for("polished")
    files = TC.scan_files(cmap, "polished", comps, True)
    comps = TC.comparisons_for("polished", TC.present_from(files))
    counts = TC.count_file_tokens(
        sorted({p for row in files.values() for p in row.values()}))
    ratios = TC.pair_ratios(files, "polished", comps, counts)
    stats = TC.collect_stats(files, "polished", comps, counts, 10, 20)
    st = stats["redacted"]
    assert st["n"] == 4 and st["center"] == pytest.approx(1.0)
    assert TC.active(st)
    lo, hi = TC.bounds(st)
    assert not (lo <= ratios["redacted"][4] <= hi)  # «толстая» глава мимо
    assert lo <= ratios["redacted"][1] <= hi



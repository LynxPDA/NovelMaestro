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


def _stats(files, counts, k=5.0, check_type="polished", k_original=None):
    comps = TC.comparisons_for(check_type)
    return TC.collect_stats(files, check_type, comps, counts, k,
                            k if k_original is None else k_original)


def test_collect_stats_log_center_and_sigma():
    """Центр — медиана логарифмов, разброс — MAD логарифмов × 1.4826.

    Отношение мультипликативно: 0.9 и 1.111 — одно и то же отклонение,
    поэтому «центр ×/− tol» одинаково строг в обе стороны.
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
    # разброс нулевой — допуск держит пол (0.5% у соседних стадий)
    assert st["redacted"]["tol"] == pytest.approx(math.log(1.005))
    assert st["chapter"]["center"] == pytest.approx(1000 / 600)
    assert st["chapter"]["tol"] == pytest.approx(math.log(1.05))  # пол
    lo, hi = TC.bounds(st["chapter"])
    assert (lo, hi) == pytest.approx((1000 / 600 / 1.05, 1000 / 600 * 1.05))


def test_collect_stats_two_knobs():
    """У пар соседних стадий и у пар с оригиналом своя ручка K.

    Разброс книги тут одинаков в обеих группах (иначе тест не про ручки),
    поэтому разница допусков — ровно выбранные K.
    """
    files, counts = {}, {}
    for i in range(1, 21):
        files[i] = {"polished": f"p{i}", "redacted": f"r{i}",
                    "chapter": f"c{i}"}
        counts[f"p{i}"] = 1000 + (i % 2) * 150
        counts[f"r{i}"] = 1000
        counts[f"c{i}"] = 1000
    st = _stats(files, counts, k=2.0, k_original=10.0)
    assert st["redacted"]["k"] == 2.0 and st["chapter"]["k"] == 10.0
    assert st["redacted"]["sigma"] == pytest.approx(
        st["chapter"]["sigma"], abs=1e-9)
    assert st["chapter"]["tol"] == pytest.approx(
        5 * st["redacted"]["tol"])


def test_collect_stats_adaptive_tolerance():
    """MAD пробивает пол — допуск адаптивный: K·σ в логарифмах."""
    files, counts = {}, {}
    for i in range(1, 21):
        files[i] = {"polished": f"p{i}", "chapter": f"c{i}"}
        counts[f"p{i}"] = 1000 + (i % 2) * 150  # разброс 15%
        counts[f"c{i}"] = 1000
    st = _stats(files, counts)
    sigma = st["chapter"]["sigma"]
    assert st["chapter"]["tol"] == pytest.approx(max(math.log(1.05), 5 * sigma))
    assert st["chapter"]["tol"] > math.log(1.05)  # MAD пробил пол


def test_collect_stats_small_n():
    """Малая база (2–4): MAD не доверяем — допуск расширенного пола."""
    files = {i: {"polished": f"p{i}", "chapter": f"c{i}"} for i in range(1, 3)}
    counts = {f"p{i}": 1000 for i in range(1, 3)}
    counts.update({f"c{i}": 1000 for i in range(1, 3)})
    st = _stats(files, counts)
    assert st["chapter"]["n"] == 2 and st["chapter"]["small"] is True
    assert st["chapter"]["tol"] == pytest.approx(math.log(1.15))


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


def test_collect_stats_off_when_k_zero():
    """K = 0 — пара выключена, но из отчёта не исчезает: шапка показывает
    её выключенной, а не молчит о ней."""
    files = {i: {"polished": f"p{i}", "redacted": f"r{i}"} for i in (1, 2, 3)}
    counts = {f"p{i}": 100 for i in (1, 2, 3)}
    counts.update({f"r{i}": 100 for i in (1, 2, 3)})
    st = TC.collect_stats(files, "polished", TC.comparisons_for("polished"),
                          counts, 0.0, 5.0)
    assert st["redacted"]["off"] is True and not TC.active(st["redacted"])
    assert st["chapter"]["off"] is False
    line = TC.stats_line("polished", "redacted", st["redacted"])
    assert "выключено" in line and "чувствительность 0" in line


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


def _err(st, check_n, ref_n, min_delta=0) -> str:
    """Строка объёмной ошибки: тест ждёт её, а не None."""
    got = TC.volume_error("polished", "redacted", check_n, ref_n, st, min_delta)
    assert got is not None
    return got


def test_volume_error_bounds():
    """Отношение внутри допуска — None; вне — ошибка с числами."""
    st = {"center": 1.0, "sigma": 0.0, "tol": 0.1, "n": 30, "k": 7, "off": False}
    assert TC.volume_error("polished", "redacted", 1000, 1000, st) is None
    assert "1.200" in _err(st, 1200, 1000)
    # пустой файл не объёмная ошибка (ловит минимальный размер)
    assert TC.volume_error("polished", "redacted", 0, 1000, st) is None
    assert TC.volume_error("polished", "redacted", 1000, 0, st) is None
    # выключенная пара не проверяется
    assert TC.volume_error("polished", "redacted", 1200, 1000,
                           dict(st, off=True)) is None


def test_volume_error_min_delta_tokens():
    """Отличие меньше floor'а в токенах — не ошибка: 2% на главе в 200
    токенов это округление оценки, 10% на главе в 3000 — уже нет."""
    st = {"center": 1.0, "sigma": 0.0, "tol": 0.01, "n": 30, "k": 7,
          "off": False}
    # 3050 против 3000: за допуском (1%), но отклонение 50 т. — шум оценки
    assert TC.volume_error("polished", "redacted", 3050, 3000, st) is None
    # 3100 против 3000: отклонение 100 т. — всё ещё шум (порог 150)
    assert TC.volume_error("polished", "redacted", 3100, 3000, st) is None
    # 3200 против 3000 — 200 т., уже ошибка
    assert TC.volume_error("polished", "redacted", 3200, 3000, st) is not None
    # порог снимается нулём или уменьшается вручную
    assert TC.volume_error("polished", "redacted", 3050, 3000, st, 0) is not None
    assert TC.volume_error("polished", "redacted", 3100, 3000, st, 200) is None


def test_stats_line_shows_bounds():
    """Строка пары называет ГРАНИЦЫ отношения при текущем K — не проценты
    допуска, которые приходится пересчитывать в голове."""
    st = {"n": 100, "center": 1.0, "sigma": 0.004,
          "tol": math.log(1.1), "k": 25.0, "small": False, "off": False}
    line = TC.stats_line("polished", "redacted", st)
    assert "0.909–1.100" in line, line
    assert "K=25" in line and "разброс 0.40%" in line, line
    # малая база: границы есть, признак «разброс не учитывается» на месте
    small = dict(st, n=3, small=True, tol=math.log(1.15))
    line = TC.stats_line("polished", "chapter", small)
    assert "0.870–1.150" in line and "мало глав" in line, line
    # K стоит в КАЖДОЙ строке с базой: разбирающий отчёт ищет его тут, а не
    # в догадке «какой из двух ручек сработал»
    assert "K=25" in line, line


def test_bounds_multiplicative():
    """Границы раздвигаются умножением: 0.9 и 1.111 — одно отклонение."""
    st = {"center": 1.0, "tol": math.log(1.1)}
    lo, hi = TC.bounds(st)
    assert lo == pytest.approx(1 / 1.1) and hi == pytest.approx(1.1)


def test_volume_error_hints_next_k():
    """В ошибке — границы при текущем K и сколько K не хватило: видно,
    двигать ли настройку или править главу."""
    st = {"center": 0.999, "sigma": 0.01, "tol": math.log(1.1), "k": 10.0,
          "n": 100, "off": False}
    err = _err(st, 1469, 1637)
    assert "вне 0.908–1.099 при K=10" in err, err
    # отклонение 0.897/0.999 = 10.7σ → K = 11
    assert "K ≥ 11" in err, err
    # тот же допуск, но K уже выше нужного: подсказки «следующий K» нет
    # (K=15 уже покрывает), и строка говорит, что глава внутри допуска


def test_volume_error_hint_when_k_hopeless():
    """Разрыв в разы — это не разброс книги: подсказка «K не спасёт»
    вместо бесконечного «пройдёт при K ≥ 300»."""
    st = {"center": 1.0, "sigma": 0.001, "tol": math.log(1.005), "k": 7.0,
          "n": 601, "off": False}
    err = _err(st, 600, 6000)  # глава в 10 раз короче обычного
    assert "правьте главу" in err and "K=20" in err, err


def test_volume_error_hint_when_floor_holds():
    """Разброс 0 — допуск держит пол, и никакой K не расширит его: так и
    пишем, иначе человек будет крутить настройку без результата."""
    st = {"center": 1.0, "sigma": 0.0, "tol": math.log(1.005), "k": 7.0,
          "n": 100, "off": False}
    err = _err(st, 900, 1000)
    assert "K не поможет" in err and "пол" in err, err
    assert TC.min_k_for(0.9, st) is None


def test_min_k_for_rounds_up():
    """Нужный K округляется вверх: 25.43 → 26 (граница включается)."""
    st = {"center": 0.999, "sigma": 0.01, "tol": math.log(1.1), "k": 10.0}
    assert TC.min_k_for(0.8974, st) == 11
    assert TC.min_k_for(1.0, st) is None  # уже внутри допуска
    assert TC.bounds(st)[0] == pytest.approx(0.999 / 1.1, abs=0.001)


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
    st = {"n": 30, "center": 1.0, "sigma": 0.0, "tol": math.log(1.005),
          "k": 7.0, "off": False}
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
    st = {"n": 30, "center": 1.0, "sigma": 0.0, "tol": math.log(1.005),
          "k": 7.0, "off": False}
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
    st = {"n": 30, "center": 1.0, "sigma": 0.0, "tol": math.log(1.005),
          "k": 7.0, "off": False}
    orig = {"n": 30, "center": 1.0, "sigma": 0.0, "tol": math.log(1.005),
            "k": 3.0, "off": False}
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
    st = {"n": 30, "center": 1.0, "sigma": 0.0, "tol": math.log(1.005),
          "k": 7.0, "off": False}
    orig = {"n": 30, "center": 1.0, "sigma": 0.0, "tol": math.log(1.05),
            "k": 3.0, "off": False}
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


def test_sensitivity_setting_prefilled():
    """Ручек две, и они числа из реестра (0 = пара выключена).

    SMALL_N — не K: это порог «малой базы», ниже которого разброс выборки не
    доверяем и берём расширенный пол. Числа независимы и совпадать не обязаны.
    """
    stages = SET.BY_KEY["TRANSLATE_CHECK_SENSITIVITY_STAGES"]
    original = SET.BY_KEY["TRANSLATE_CHECK_SENSITIVITY_ORIGINAL"]
    assert stages.type == "number" and int(stages.default) > 0
    assert original.type == "number" and int(original.default) > 0
    assert SET.BY_KEY["TRANSLATE_CHECK_MIN_DELTA_TOKENS"].type == "number"
    assert TC.SMALL_N == 5
    assert TC.FLOORS == {"stage": 0.005, "original": 0.05}
    assert TC.SMALL_FLOORS == {"stage": 0.02, "original": 0.15}
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


def test_stats_payload_matches_stats_line(tmp_path):
    """Поля payload — те же числа, что печатает stats_line (один источник).

    Форма показывает допуск до запуска, отчёт — после: разойтись они не
    могут, поэтому оба берут bounds() и те же округления.
    """
    for num, mul in ((1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6)):
        _write_chapter(tmp_path, num,
                       {"polished": _body(10 * mul), "redacted": _body(10)})
    data = TC.collect_book_stats(str(tmp_path), "polished", 7, 3, True)
    assert data["chapters"] == 6
    assert data["start"] == 1 and data["end"] == 6
    pair = next(s for s in data["stats"] if s["ref"] == "redacted")
    assert pair["check"] == "polished" and pair["k"] == 7
    assert pair["n"] == 6 and pair["small"] is False
    assert pair["lo"] < pair["median"] < pair["hi"]
    assert pair["lo"] == pytest.approx(pair["median"] / (1 + pair["tol"] / 100),
                                      abs=0.01)
    # stats_payloads в порядке comparisons; пар без единой полной главы нет
    refs = [s["ref"] for s in data["stats"]]
    assert refs == ["redacted"]


def test_stats_payload_small_base_flag(tmp_path):
    """Малая база (< SMALL_N) помечена: разброс выборки не доверяем."""
    for num in (1, 2, 3):
        _write_chapter(tmp_path, num,
                       {"polished": _body(10), "redacted": _body(10)})
    data = TC.collect_book_stats(str(tmp_path), "polished", 7, 3, True)
    pair = next(s for s in data["stats"] if s["ref"] == "redacted")
    assert pair["n"] == 3 and pair["small"] is True


def test_outliers_respect_range_and_limit(tmp_path):
    """Выбросы — только главы диапазона, по возрастанию номеров.

    База однородная (все главы равны), в главах 4 и 6 текста вдвое больше:
    медиана держится на большинстве, а две «толстые» главы выходят за
    узкий допуск. Главы крупные: иначе сработал бы порог «мин. отличие в
    токенах» и выбросов не было бы вовсе.
    """
    for num in range(1, 9):
        mul = 2 if num in (4, 6) else 1
        _write_chapter(tmp_path, num,
                       {"polished": _body(200 * mul), "redacted": _body(200)})
    data = TC.collect_book_stats(str(tmp_path), "polished", 7, 3, True,
                                 start=3, end=6)
    assert data["start"] == 3 and data["end"] == 6
    nums = [o["chapter"] for o in data["outliers"]]
    assert nums == [4, 6], nums
    for o in data["outliers"]:
        for p in o["pairs"]:
            assert p["ref"] in ("redacted", "chapter")
            assert p["ratio"] < p["lo"] or p["ratio"] > p["hi"]


def test_collect_book_stats_empty_book(tmp_path):
    """Глав нет — пустые списки, а не падение (форма рисует «пар нет»)."""
    data = TC.collect_book_stats(str(tmp_path), "polished", 7, 3, True)
    assert data == {"type": "polished", "chapters": 0, "start": 0, "end": 0,
                    "stats": [], "outliers": []}


def test_collect_book_stats_zero_sensitivity_no_outliers(tmp_path):
    """Обе ручки в нуле — объёмных сравнений нет: выбросов не бывает."""
    for num in range(1, 6):
        _write_chapter(tmp_path, num,
                       {"polished": _body(10 * num), "redacted": _body(10)})
    data = TC.collect_book_stats(str(tmp_path), "polished", 0, 0, True)
    assert data["outliers"] == []
    assert all(s["off"] for s in data["stats"])


def test_collect_book_stats_knobs_apart(tmp_path):
    """Соседние пары можно выключить, а оригинал оставить (и наоборот).

    Это и есть смысл двух ручек: у книги может быть испорченный оригинал,
    и пары с chapter тогда только шумят.
    """
    for num in range(1, 9):
        _write_chapter(tmp_path, num,
                       {"polished": _body(10), "redacted": _body(10),
                        "chapter": _body(20)})
    data = TC.collect_book_stats(str(tmp_path), "polished", 0, 3, True)
    by = {s["ref"]: s for s in data["stats"]}
    assert by["redacted"]["off"] is True
    assert by["chapter"]["off"] is False

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cli/translate_check.py — адаптивная проверка объёма (медиана+MAD)
и структурные проверки (остальные ветки — в tests/test_cli_e2e.py)."""
# pyright: reportMissingImports=false
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
# Пары сравнения по типу файлов глав


def test_comparisons_for():
    assert TC.comparisons_for("polished") == [("redacted", "neighbor"),
                                              ("chapter", "original")]
    assert TC.comparisons_for("redacted") == [("translated", "neighbor"),
                                              ("chapter", "original")]
    assert TC.comparisons_for("translated") == [("chapter", "original")]


# ════════════════════════════════════════════════════════════════════
# Статистика пар по всей книге


def _stats(files, counts, k=5.0, check_type="polished"):
    comps = TC.comparisons_for(check_type)
    return TC.collect_stats(files, check_type, comps, counts, k)


def test_collect_stats_median_mad():
    """Центр — медиана, разброс — MAD; допуск = max(пол, K·MAD/медиана)."""
    files = {i: {"polished": f"p{i}", "redacted": f"r{i}",
                 "chapter": f"c{i}"} for i in range(1, 31)}
    counts = {f"p{i}": 1000 for i in range(1, 31)}
    counts.update({f"r{i}": 1000 for i in range(1, 31)})
    counts.update({f"c{i}": 600 for i in range(1, 31)})
    st = _stats(files, counts)
    assert st["redacted"]["n"] == 30
    assert st["redacted"]["median"] == 1.0
    assert st["redacted"]["mad"] == 0.0
    assert st["redacted"]["tol"] == 0.02  # пол, K·MAD = 0
    assert st["chapter"]["median"] == pytest.approx(1000 / 600)
    assert st["chapter"]["tol"] == pytest.approx(0.05)  # пол


def test_collect_stats_adaptive_tolerance():
    """MAD больше пола — допуск адаптивный: K·MAD/медиана."""
    files, counts = {}, {}
    for i in range(1, 21):
        files[i] = {"polished": f"p{i}", "chapter": f"c{i}"}
        counts[f"p{i}"] = 1000 + (i % 2) * 150  # разброс 15%
        counts[f"c{i}"] = 1000
    st = _stats(files, counts)
    med = st["chapter"]["median"]
    mad = st["chapter"]["mad"]
    assert st["chapter"]["tol"] == pytest.approx(max(0.05, 5 * mad / med))
    assert st["chapter"]["tol"] > 0.05  # MAD пробил пол


def test_collect_stats_small_n():
    """Малая база (2–4): MAD не доверяем — допуск расширенного пола."""
    files = {i: {"polished": f"p{i}", "chapter": f"c{i}"} for i in range(1, 3)}
    counts = {f"p{i}": 1000 for i in range(1, 3)}
    counts.update({f"c{i}": 1000 for i in range(1, 3)})
    st = _stats(files, counts)
    assert st["chapter"]["n"] == 2 and st["chapter"]["small"] is True
    assert st["chapter"]["tol"] == 0.15  # расширенный пол оригинала


def test_collect_stats_n0_n1():
    """n=0 и n=1: пара без статистики, помечается в шапке отчёта."""
    st = _stats({}, {})
    assert st["chapter"]["n"] == 0
    assert TC.stats_line("polished", "chapter", st["chapter"]).startswith(
        "  polished vs chapter: статистики нет")
    files = {1: {"polished": "p1", "chapter": "c1"}}
    st1 = _stats(files, {"p1": 100, "c1": 60})
    assert st1["chapter"]["n"] == 1
    assert "статистики нет" in TC.stats_line("polished", "chapter",
                                             st1["chapter"])


def test_collect_stats_skips_empty_files():
    """Пустые файлы в базу не попадают."""
    files = {i: {"polished": f"p{i}", "chapter": f"c{i}"} for i in range(1, 11)}
    counts = {f"p{i}": 1000 for i in range(1, 11)}
    counts.update({f"c{i}": 0 for i in range(1, 6)})  # эталон пуст — пара мимо
    counts.update({f"c{i}": 600 for i in range(6, 11)})
    st = _stats(files, counts)
    assert st["chapter"]["n"] == 5


def test_collect_stats_partial_stage():
    """Промежуточной стадии нет у части глав — у пары своя база."""
    files = {i: {"polished": f"p{i}", "redacted": f"r{i}", "chapter": f"c{i}"}
             for i in range(1, 21)}
    counts = {f"p{i}": 1000 for i in range(1, 21)}
    counts.update({f"r{i}": 1000 for i in range(1, 11)})  # redact у половины
    counts.update({f"c{i}": 600 for i in range(1, 21)})
    st = _stats(files, counts)
    assert st["redacted"]["n"] == 10
    assert st["chapter"]["n"] == 20


def test_collect_stats_broken_median_robust():
    """Битые главы не двигают медиану, пока их меньше половины базы."""
    files, counts = {}, {}
    for i in range(1, 21):
        files[i] = {"polished": f"p{i}", "chapter": f"c{i}"}
        counts[f"p{i}"] = 632 if i <= 3 else 1341  # 3 битых из 20
        counts[f"c{i}"] = 1000
    st = _stats(files, counts)
    assert st["chapter"]["median"] == pytest.approx(1.341)


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


def test_volume_error_bounds():
    """Отношение внутри допуска — None; вне — ошибка с числами."""
    st = {"median": 1.0, "tol": 0.1, "n": 30}
    assert TC.volume_error("polished", "redacted", 1000, 1000, st) is None
    err = TC.volume_error("polished", "redacted", 1200, 1000, st)
    assert err is not None and "1.20" in err
    # пустой файл не объёмная ошибка (ловит минимальный размер)
    assert TC.volume_error("polished", "redacted", 0, 1000, st) is None
    assert TC.volume_error("polished", "redacted", 1000, 0, st) is None


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
    stats = TC.collect_stats(files, "polished", comps, counts, 5.0)
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
    stats = TC.collect_stats(files, "polished", comps, counts, 5.0)
    errors, _ = TC.check_chapter(5, "00000_5_x", "polished", comps, False,
                                 None, exclusions=[], counts=counts,
                                 stats=stats)
    assert not any("отношение объёма" in e for e in errors)


def test_check_chapter_volume_off(tmp_path):
    """stats=None (--sensitivity 0): объёмные сравнения выключены."""
    d = _write_chapter(tmp_path, 1, {"polished": "коротко",
                                     "redacted": "текст"})
    errors, _ = TC.check_chapter(1, d, "polished",
                                 [("redacted", "neighbor")], False, None,
                                 exclusions=[], counts=None, stats=None)
    assert not any("отношение объёма" in e for e in errors)


def test_check_chapter_volume_no_stats(tmp_path):
    """Базы нет (n<2): объёмная проверка пропускается, помечена в шапке."""
    d = _write_chapter(tmp_path, 1, {"polished": "текст", "redacted": "текст"})
    errors, _ = TC.check_chapter(1, d, "polished",
                                 [("redacted", "neighbor")], False, None,
                                 exclusions=[], counts={"x": 1},
                                 stats={"redacted": {"n": 0}})
    assert not any("отношение объёма" in e for e in errors)


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
    """Чувствительность — число из реестра, дефолт 5, 0 = выключено."""
    s = SET.BY_KEY["TRANSLATE_CHECK_SENSITIVITY"]
    assert s.type == "number" and str(s.default) == "5"
    assert TC.SMALL_N == 5
    assert TC.FLOORS == {"neighbor": 0.02, "original": 0.05}
    assert TC.SMALL_FLOORS == {"neighbor": 0.02, "original": 0.15}


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

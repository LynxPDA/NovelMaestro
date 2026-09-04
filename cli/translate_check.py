#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translate_check.py — проверка перевода по цепочке конвейера.
(рефакторинг: единый парсер глав и поиск файла из core.common)

Проверки:
  • объёмы по цепочке (ratio-эвристики; размеры файлов — БАЙТЫ):
      polished   vs redacted     1.0 ± 0.05  (--neighbor)
      polished   vs chapter      2.1 ± 0.5   (--original)
      redacted   vs translated   1.0 ± 0.05
      translated vs chapter      2.1 ± 0.5
  • минимальный размер файла (--min-file-size, БАЙТЫ; дефолт 3072);
  • regexp-проверки текста главы (--regexp-check, multiline): всё
      найденное — ошибка, проверяются ВСЕ строки включая заголовок;
      по умолчанию — иероглифы (все CJK-блоки), латиница и лишние
      «Глава N» (первое совпадение — заголовок главы, не ошибка);
      исключения — пусто: ничего;
  • заголовок главы (--header-regexp; дефолт «Глава N» без учёта
      регистра) + сквозная последовательность: первое число первой
      непустой строки больше предыдущего (отключается
      --no-sequence-check);
  • дубли папок / файлов (strict → FATAL, глава пропускается).

ЕДИНИЦЫ: размеры файлов — байты; ratio — безразмерная эвристика
(символьные расчёты живут в стадиях обработки, не здесь).
Форматы папок глав — единый канон core.common.parse_chapter_id.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
from datetime import datetime
from pathlib import Path


def _bootstrap_core() -> None:
    d = Path(__file__).resolve().parent
    for _ in range(6):
        if (d / "core" / "common.py").is_file():
            if str(d) not in sys.path:
                sys.path.insert(0, str(d))
            return
        if d.parent == d:
            break
        d = d.parent

_bootstrap_core()

from core.common import (build_chapter_map, find_chapter_file,
                         strip_line_comment)  # noqa: E402

# ──────────────────────────────────────────────
# НАСТРОЙКИ (настраиваемые коэффициенты)
# ──────────────────────────────────────────────
MIN_FILE_SIZE = 3072          # байты
RATIO_NEIGHBOR = 1.0          # безразмерно

# Паттерн заголовка главы: первая непустая строка (проверка
# --header-regexp) + поиск лишних заголовков в тексте (первое
# совпадение — сам заголовок главы, не ошибка; последующие —
# «Лишний заголовок главы»). По умолчанию — без учёта регистра.
TOL_NEIGHBOR = 0.05
RATIO_ORIGINAL = 2.1          # байт ru / байт zh (эвристика)
TOL_ORIGINAL = 0.5

# ──────────────────────────────────────────────
# ИСКЛЮЧЕНИЯ (пусто = ничего не исключается; переопределяется ключом
# TRANSLATE_CHECK_EXCLUDE_WORDS в .env и флагом --exclude-words)
# ──────────────────────────────────────────────


def load_exclusions(env_path=None) -> list[str]:
    """Слова-исключения: --exclude-words > TRANSLATE_CHECK_EXCLUDE_WORDS
    (.env) > пусто (ничего). Возвращает список в нижнем регистре."""
    raw = None
    try:
        from core.common import find_env_file, parse_dotenv
        env = parse_dotenv(env_path or find_env_file())
        raw = env.get("TRANSLATE_CHECK_EXCLUDE_WORDS")
    except Exception:  # noqa: BLE001 — .env необязателен
        raw = None
    return [w.strip().lower() for w in (raw or "").split(",") if w.strip()]

# ──────────────────────────────────────────────
# РЕГУЛЯРНЫЕ ВЫРАЖЕНИЯ
# ──────────────────────────────────────────────
# Иероглифы — ВСЕ блоки CJK (Basic U+4E00–U+9FFF, Ext A
# U+3400–U+4DBF, совместимость U+F900–U+FAFF, Ext B–H астральные)
# + кавычки 【】「」『』: проверка смотрит каждую строку ВКЛЮЧАЯ
# заголовок главы.
CHINESE_REGEX = re.compile(
    r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff'
    r'\U00020000-\U0002ebef【】「」『』]+')
ENGLISH_REGEX = re.compile(r'[a-zA-Z]+')
DEFAULT_HEADER_PATTERN = r'^\s*Глава\s+(\d+|\[Номер\])'

# Дефолтные regexp-проверки текста главы (пусто в --regexp-check =
# эти строки): всё найденное — ошибка; проверяются ВСЕ строки,
# включая заголовок главы. Лишние заголовки «Глава N» — последняя
# строка: первое совпадение — заголовок главы (не ошибка), остальные
# — «Лишний заголовок главы»; паттерн — настраиваемый --header-regexp.
DEFAULT_REGEXP_CHECKS = [
    CHINESE_REGEX.pattern,
    r'[a-zA-Z]+',
    DEFAULT_HEADER_PATTERN,
]


def _write_report(report_path: str, text: str, mode: str = "a") -> None:
    """Запись в отчёт с понятной ошибкой вместо молчаливого падения."""
    try:
        with open(report_path, mode, encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        print(f"Ошибка записи {report_path}: {exc}")

# ──────────────────────────────────────────────
# СРАВНЕНИЯ ПО ТИПУ ФАЙЛОВ ГЛАВ (коэффициенты — эвристики, подбираются
# опытным путём; переопределяются флагами --neighbor/--original)
# ──────────────────────────────────────────────
def parse_ratio_tol(value, def_ratio, def_tol) -> tuple[float, float]:
    """Разбирает «ratio±tol» (например «1.0±0.05») или просто число.
    None/пусто — встроенные дефолты (def_ratio, def_tol)."""
    if value in (None, ""):
        return def_ratio, def_tol
    s = str(value).strip().replace(" ", "")
    if "±" in s:
        parts = s.split("±", 1)
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            pass
    try:
        return float(s), def_tol
    except ValueError:
        sys.exit(f"Ошибка: «{value}» — ожидалось число или «ratio±tol»")


def comparisons_for(check_type, neighbor=None, original=None):
    """Сравнения для типа файлов глав: polished → redacted+chapter,
    redacted → translated+chapter, translated → только chapter.
    neighbor/original — «ratio±tol»; None = встроенные дефолты."""
    rn, tn = parse_ratio_tol(neighbor, RATIO_NEIGHBOR, TOL_NEIGHBOR)
    ro, to = parse_ratio_tol(original, RATIO_ORIGINAL, TOL_ORIGINAL)
    return {
        "polished": [
            ("redacted", rn, tn),
            ("chapter", ro, to),
        ],
        "redacted": [
            ("translated", rn, tn),
            ("chapter", ro, to),
        ],
        "translated": [
            ("chapter", ro, to),
        ],
    }[check_type]

# ──────────────────────────────────────────────
# СРАВНЕНИЕ РАЗМЕРОВ
# ──────────────────────────────────────────────
def compare_sizes(check_label, ref_label, check_size, ref_size,
                  expected_ratio, tolerance):
    """Возвращает строку-ошибку или None, если всё в порядке."""
    if ref_size == 0:
        return (f"  [{check_label} vs {ref_label}]  "
                f"эталон пуст (0 Б), сравнение невозможно.")
    ratio = round(check_size / ref_size, 2)
    lo = expected_ratio - tolerance
    hi = expected_ratio + tolerance
    if lo <= ratio <= hi:
        return None
    return (f"  [{check_label} vs {ref_label}]  "
            f"ratio {ratio}  →  ОШИБКА "
            f"(ожидалось {expected_ratio} ± {tolerance}). "
            f"Проверяемый {check_size} Б, эталон {ref_size} Б.")

# ──────────────────────────────────────────────
# ПРОВЕРКА ОДНОЙ ГЛАВЫ
# ──────────────────────────────────────────────
def check_chapter(chapter_num, dir_path, check_type, comparisons,
                  strict, prev_inner_chapter, exclusions=None,
                  regexp_checks=None, min_file_size=None,
                  header_regexp=None, sequence_check=True):
    """Возвращает (список_ошибок, обновлённый_prev_inner_chapter).
    В список попадают ТОЛЬКО ошибки и предупреждения поиска.
    exclusions — слова-исключения (R9), по умолчанию load_exclusions().
    regexp_checks — список compiled regexp по тексту главы: всё
      найденное — ошибка, проверяются ВСЕ строки включая заголовок;
      None = дефолтные проверки (иероглифы, латиница + лишние
      заголовки «Глава N»: первое совпадение — заголовок главы, не
      ошибка, последующие — «Лишний заголовок главы»).
    min_file_size — минимальный размер файла (БАЙТЫ; None = дефолт).
    header_regexp — compiled regexp заголовка главы (первая непустая
      строка); None = дефолтный «Глава N» (без учёта регистра).
    sequence_check — сквозная последовательность: первое число в
      первой непустой строке должно быть больше предыдущего."""
    errors: list[str] = []
    if exclusions is None:
        exclusions = load_exclusions()
    min_size = MIN_FILE_SIZE if min_file_size is None else min_file_size
    header_re = (header_regexp
                 if header_regexp is not None
                 else re.compile(DEFAULT_HEADER_PATTERN,
                                 re.IGNORECASE | re.MULTILINE))

    # ---- целевой файл (единый поиск из core.common) ----
    file_path, msgs = find_chapter_file(
        dir_path, chapter_num, want=check_type, strict=strict,
        strict_types=True)
    fatal = file_path is None and any(m.startswith("[FATAL]") for m in msgs)
    errors.extend(msgs)
    if fatal:
        return errors, prev_inner_chapter
    if file_path is None or not os.path.isfile(file_path):
        if not msgs:
            errors.append(f"  - Файл типа '{check_type}' не найден.")
        return errors, prev_inner_chapter

    # ---- размер (байты) ----
    size_file = os.path.getsize(file_path)
    if size_file < min_size:
        errors.append(f"  - Размер файла слишком мал: {size_file} Б "
                      f"(минимум {min_size} Б).")

    # ---- цикл сравнений ----
    for ref_type, expected_ratio, tol in comparisons:
        ref_path, ref_msgs = find_chapter_file(
            dir_path, chapter_num, want=ref_type, strict=strict,
            strict_types=True)
        ref_fatal = (ref_path is None
                     and any(m.startswith("[FATAL]") for m in ref_msgs))
        errors.extend(ref_msgs)
        if ref_fatal:
            return errors, prev_inner_chapter
        if ref_path is None or not os.path.isfile(ref_path):
            errors.append(f"  [{check_type} vs {ref_type}]  "
                          f"эталон '{ref_type}' не найден, сравнение пропущено.")
            continue
        size_ref = os.path.getsize(ref_path)
        err = compare_sizes(check_type, ref_type, size_file, size_ref,
                            expected_ratio, tol)
        if err:
            errors.append(err)

    # ---- чтение ----
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        lines = content.splitlines()
    except Exception as e:
        errors.append(f"  - Ошибка чтения файла: {e}")
        return errors, prev_inner_chapter

    # ---- regexp-проверки текста: ВСЕ строки, включая заголовок ----
    #      дефолтный набор — иероглифы, латиница, заголовки «Глава N»;
    #      первое совпадение паттерна заголовка — сам заголовок главы
    #      (не ошибка), последующие — «Лишний заголовок главы»
    regexes = list(regexp_checks) if regexp_checks is not None \
        else [CHINESE_REGEX, ENGLISH_REGEX, header_re]
    for rx in regexes:
        first_skipped = False
        for h in rx.finditer(content):
            bad = h.group(0)
            if rx is header_re and not first_skipped:
                first_skipped = True
                continue
            if bad.lower() in exclusions:
                continue
            if rx is header_re:
                errors.append(
                    f"  - Лишний заголовок главы: {bad.strip()[:80]}")
            else:
                errors.append(f"  - По паттерну {rx.pattern}: {bad}")

    # ---- заголовок главы: первая непустая строка (паттерн
    #      настраиваемый --header-regexp) ----
    non_empty = [l.strip() for l in lines if l.strip()]
    first_line = non_empty[0] if non_empty else ""
    m = header_re.search(first_line)
    if not m:
        snippet = (first_line[:30] + "…") if len(first_line) > 30 else first_line
        errors.append(f"  - Нет «Глава N» в начале (найдено: '{snippet}')")
    elif sequence_check:
        # сквозная последовательность: первое число в первой непустой
        # строке должно быть БОЛЬШЕ предыдущего (не ровно N+1)
        nums = re.findall(r"\d+", first_line)
        try:
            cur = int(nums[0]) if nums else None
        except (ValueError, OverflowError):
            cur = None
        if cur is not None:
            if prev_inner_chapter is not None and cur <= prev_inner_chapter:
                errors.append(f"  - Нарушена последовательность: после Главы "
                              f"{prev_inner_chapter} → Глава {cur} "
                              f"(ожидалось больше {prev_inner_chapter})")
            prev_inner_chapter = cur

    return errors, prev_inner_chapter

# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Скрипт проверки перевода глав.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Единицы: размеры файлов — байты; ratio — безразмерная эвристика.
Сравнения по типу файлов глав:
  POLISHED   vs redacted (1.0±0.05) + chapter (2.1±0.5)
  REDACTED   vs translated (1.0±0.05) + chapter (2.1±0.5)
  TRANSLATED vs chapter (2.1±0.5)
Коэффициенты: --neighbor/--original в формате «ratio±tol».
Regexp-проверки текста (--regexp-check, по одной на строку): всё
найденное — ошибка, проверяются ВСЕ строки, включая заголовок главы;
^/$ — начало/конец СТРОКИ (multiline); комментарий в конце строки —
« # …». Пусто = встроенные дефолты (иероглифы, латиница, лишние
заголовки «Глава N» — первое совпадение не ошибка). Заголовок главы
(первая непустая строка) — --header-regexp (пусто = «Глава N» без
учёта регистра); сквозная последовательность — первое число первой
непустой строки больше предыдущего, отключается --no-sequence-check.
Минимальный размер файла — --min-file-size (БАЙТЫ).
Примеры:
  %(prog)s                                  strict, polished, весь диапазон
  %(prog)s --check-type redacted --start 1 --end 50
  %(prog)s --chapters-dir ./chapters --lenient
""",
    )
    parser.add_argument("--chapters-dir", default="./chapters",
                        help="Путь к папке с главами (по умолчанию: ./chapters)")
    parser.add_argument("--check-type", choices=["polished", "redacted",
                                                 "translated"],
                        default="polished",
                        help="Тип файлов глав: polished (vs redacted+chapter), "
                             "redacted (vs translated+chapter), translated "
                             "(vs chapter); по умолчанию: polished")
    parser.add_argument("--start", type=int, default=None,
                        help="Начальная глава (по умолчанию: минимальная найденная)")
    parser.add_argument("--end", type=int, default=None,
                        help="Конечная глава (по умолчанию: максимальная найденная)")
    parser.add_argument("--strict", action="store_true", default=True,
                        help="Строгий режим: при дублях файлов глава "
                             "пропускается с ошибкой (по умолчанию).")
    parser.add_argument("--lenient", action="store_true", default=False,
                        help="Мягкий режим: при дублях берётся первый "
                             "по алфавиту, выводится предупреждение.")
    parser.add_argument("--exclude-words", default=None,
                        help="Слова-исключения через запятую "
                             "(пусто = ничего; иначе "
                             "TRANSLATE_CHECK_EXCLUDE_WORDS из .env)")
    # ── настраиваемые коэффициенты (эвристики: подбираются опытным
    #    путём под конкретную книгу; дефолты — встроенные)
    parser.add_argument("--neighbor", default=None,
                        help="Выбранная Стадия/Предыдущая Стадия "
                             "(по занимаемому месту): «ratio±tol», напр. "
                             f"«{RATIO_NEIGHBOR}±{TOL_NEIGHBOR}» "
                             "(пусто = встроенный дефолт)")
    parser.add_argument("--original", default=None,
                        help="Выбранная Стадия/Оригинал "
                             "(по занимаемому месту): «ratio±tol», напр. "
                             f"«{RATIO_ORIGINAL}±{TOL_ORIGINAL}» "
                             "(пусто = встроенный дефолт)")
    # ── regexp-проверки текста главы (экспертные)
    parser.add_argument("--regexp-check", action="append", default=[],
                        metavar="RE",
                        help="Regexp по тексту главы (multiline): всё "
                             "найденное — ошибка; можно повторять; "
                             "комментарий в конце строки — « # …»; "
                             "пусто = дефолтные проверки")
    # ── структурные проверки (настраиваемые)
    parser.add_argument("--min-file-size", type=int, default=None,
                        help=f"Минимальный размер файла (БАЙТЫ; по "
                             f"умолчанию {MIN_FILE_SIZE})")
    parser.add_argument("--header-regexp", default=None,
                        help="Regexp заголовка главы (первая непустая "
                             "строка; пусто = «Глава N» без учёта "
                             "регистра; ^ — начало СТРОКИ)")
    parser.add_argument("--no-sequence-check", action="store_true",
                        default=False,
                        help="Отключить проверку сквозной "
                             "последовательности глав")
    args = parser.parse_args()
    strict = not args.lenient
    chapters_dir: str = args.chapters_dir
    # R9: исключения — CLI > .env > встроенный дефолт
    exclusions = load_exclusions()
    if args.exclude_words:
        exclusions = [w.strip().lower()
                      for w in args.exclude_words.split(",") if w.strip()]

    print("======================================")
    print("    Скрипт проверки перевода")
    print("======================================")

    # ── карта глав: единый канон из core.common ──
    chapter_map = build_chapter_map(chapters_dir)
    if not chapter_map:
        print(f"Ошибка: в '{chapters_dir}' не найдено ни одной папки с главами.")
        sys.exit(1)

    dup_folders: list[str] = []
    for num, paths in sorted(chapter_map.items()):
        if len(paths) > 1:
            dup_folders.append(f"  Глава {num}: "
                               f"{', '.join(os.path.basename(p) for p in paths)}")
    if dup_folders:
        print("ВНИМАНИЕ: обнаружены дубли папок:")
        for line in dup_folders:
            print(line)
        if strict:
            print("В строгом режиме эти главы будут пропущены.")
        print()

    auto_min = min(chapter_map)
    auto_max = max(chapter_map)

    check_type = args.check_type
    comparisons = comparisons_for(check_type, args.neighbor, args.original)
    regexp_checks = []
    for line in args.regexp_check:
        line = strip_line_comment(line).strip()
        if line:
            regexp_checks.append(re.compile(line, re.MULTILINE))
    min_file_size = (args.min_file_size if args.min_file_size is not None
                     else MIN_FILE_SIZE)
    header_regex = re.compile(args.header_regexp or DEFAULT_HEADER_PATTERN,
                              re.IGNORECASE | re.MULTILINE)
    sequence_check = not args.no_sequence_check

    print("--------------------------------------")
    print(f"Найден диапазон глав: {auto_min} – {auto_max} "
          f"({len(chapter_map)} папок)")
    start_cap = args.start if args.start is not None else auto_min
    end_cap = args.end if args.end is not None else auto_max
    if start_cap > end_cap:
        print("Ошибка: Начальная глава не может быть больше конечной.")
        sys.exit(1)

    # ---- отчёт ----
    try:
        os.makedirs("./logs", exist_ok=True)
    except OSError as exc:
        print(f"Ошибка: не удалось создать ./logs: {exc}")
        sys.exit(1)
    report_path = f"./logs/check_{check_type}_{start_cap}-{end_cap}.txt"
    comp_desc = "; ".join(f"{rt} ({r}±{t})" for rt, r, t in comparisons)
    # R9: фактическая команда запуска — в отчёт
    _write_report(report_path, (
        f"=== Отчёт о проверке перевода ({check_type}) ===\n"
        f"Запуск        : {shlex.join(sys.argv)}\n"
        f"Диапазон глав : {start_cap} – {end_cap}\n"
        f"Папка глав    : {os.path.abspath(chapters_dir)}\n"
        f"Сравнения     : {comp_desc}\n"
        f"Regexp-проверки: {'; '.join(rx.pattern for rx in regexp_checks)}\n"
        f"Мин. размер   : {min_file_size} Б\n"
        f"Заголовок     : {header_regex.pattern}\n"
        f"Последоват.   : {'вкл' if sequence_check else 'выкл'}\n"
        f"Режим         : {'strict' if strict else 'lenient'}\n"
        f"Исключения    : {', '.join(exclusions)}\n"
        "Единицы       : размеры в байтах; ratio — безразмерная эвристика\n"
        f"Дата          : {datetime.now().strftime('%c')}\n"
        f"Всего папок   : {len(chapter_map)}\n"
        "---------------------------------\n"
    ), "w")

    print("--------------------------------------")
    # R9: фактическая команда запуска
    print(f"Запуск: {shlex.join(sys.argv)}")
    print(f"Запуск проверки ({check_type})…")
    print(f"Сравнения: {comp_desc}")
    print(f"Исключения: {', '.join(exclusions)}\n")

    prev_inner_chapter: int | None = None
    total_checked = 0
    total_errors = 0
    total_skipped = 0

    for i in range(start_cap, end_cap + 1):
        paths = chapter_map.get(i)
        if paths is None:
            total_skipped += 1
            _write_report(report_path, f"{i}. Папка не найдена.\n")
            continue
        if len(paths) > 1:
            total_skipped += 1
            names = ", ".join(os.path.basename(p) for p in paths)
            _write_report(report_path, f"{i}. Дубль папок: {names}\n"
                           "  [FATAL] Глава пропущена.\n")
            continue

        dir_path = paths[0]
        total_checked += 1
        errors, prev_inner_chapter = check_chapter(
            chapter_num=i,
            dir_path=dir_path,
            check_type=check_type,
            comparisons=comparisons,
            strict=strict,
            prev_inner_chapter=prev_inner_chapter,
            exclusions=exclusions,
            regexp_checks=regexp_checks or None,
            min_file_size=min_file_size,
            header_regexp=header_regex,
            sequence_check=sequence_check,
        )

        # ---- консоль: всегда показываем прогресс ----
        if errors:
            total_errors += 1
            print(f"[{i}] ОШИБКИ:")
            for e in errors:
                print(f"  {e}")
        else:
            print(f"[{i}] OK")

        # ---- лог: только ошибки, OK не пишем ----
        if errors:
            _write_report(report_path, f"{i}. Папка: {dir_path}\n"
                           + "".join(f"{e}\n" for e in errors) + "\n")

    # ---- сводка ----
    summary = (
        f"\n--- Сводка ---\n"
        f"Проверено глав : {total_checked}\n"
        f"С ошибками     : {total_errors}\n"
        f"Пропущено      : {total_skipped}\n"
    )
    _write_report(report_path, summary)

    print("\n======================================")
    print("Проверка завершена!")
    print(summary.strip())
    print(f"Отчёт: {report_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nПроверка прервана пользователем.")
        sys.exit(0)

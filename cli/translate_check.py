#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translate_check.py — проверка перевода по цепочке конвейера.
(рефакторинг: единый парсер глав и поиск файла из core.common)

Проверки:
  • объёмы по цепочке (ratio-эвристики; размеры файлов — БАЙТЫ):
      polished   vs redacted     1.0 ± 0.05
      polished   vs chapter      2.1 ± 0.5
      redacted   vs translated   1.0 ± 0.05
      translated vs chapter      2.1 ± 0.5
  • минимальный размер файла (3072 Б);
  • остатки иероглифов / латиницы (исключения — пусто: ничего);
  • заголовок «Глава N» в начале + сквозная последовательность;
  • лишние «Глава N» после 3-й строки;
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

from core.common import build_chapter_map, find_chapter_file  # noqa: E402

# ──────────────────────────────────────────────
# НАСТРОЙКИ (настраиваемые коэффициенты)
# ──────────────────────────────────────────────
MIN_FILE_SIZE = 3072          # байты
RATIO_NEIGHBOR = 1.0          # безразмерно
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
CHINESE_REGEX = re.compile(r'[一-鿿【】「」『』]+')
ENGLISH_REGEX = re.compile(r'[a-zA-Z]+')
CHAPTER_REGEX = re.compile(r'^\s*Глава\s+(\d+|\[Номер\])', re.IGNORECASE)


def _write_report(report_path: str, text: str, mode: str = "a") -> None:
    """Запись в отчёт с понятной ошибкой вместо молчаливого падения."""
    try:
        with open(report_path, mode, encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        print(f"Ошибка записи {report_path}: {exc}")

# ──────────────────────────────────────────────
# ПРЕСЕТЫ (коэффициенты — эвристики, подбираются опытным путём;
# переопределяются флагами --ratio-neighbor/--tol-neighbor/
# --ratio-original/--tol-original)
# ──────────────────────────────────────────────
def presets(ratio_neighbor=None, tol_neighbor=None,
            ratio_original=None, tol_original=None) -> dict:
    """Пресеты сравнений с настраиваемыми коэффициентами.
    None = встроенные дефолты (RATIO_* / TOL_*)."""
    rn = ratio_neighbor if ratio_neighbor is not None else RATIO_NEIGHBOR
    tn = tol_neighbor if tol_neighbor is not None else TOL_NEIGHBOR
    ro = ratio_original if ratio_original is not None else RATIO_ORIGINAL
    to = tol_original if tol_original is not None else TOL_ORIGINAL
    return {
        "1": ("polished", [
            ("redacted", rn, tn),
            ("chapter", ro, to),
        ]),
        "2": ("redacted", [
            ("translated", rn, tn),
            ("chapter", ro, to),
        ]),
        "3": ("translated", [
            ("chapter", ro, to),
        ]),
    }

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
                  check_nonrussian=True, nonrussian_regexes=None,
                  check_chapter_order=True, chapter_regex=None):
    """Возвращает (список_ошибок, обновлённый_prev_inner_chapter).
    В список попадают ТОЛЬКО ошибки и предупреждения поиска.
    exclusions — слова-исключения (R9), по умолчанию load_exclusions().
    check_nonrussian — проверка не-русских символов (китайский/латиница),
      по умолчанию вкл; nonrussian_regexes — список regexp вместо
      дефолтных (CHINESE_REGEX+ENGLISH_REGEX).
    check_chapter_order — проверка последовательности «Глава N»,
      по умолчанию вкл; chapter_regex — свой формат заголовка
      (группа 1 = номер главы)."""
    errors: list[str] = []
    if exclusions is None:
        exclusions = load_exclusions()

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
    if size_file < MIN_FILE_SIZE:
        errors.append(f"  - Размер файла слишком мал: {size_file} Б "
                      f"(минимум {MIN_FILE_SIZE} Б).")

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

    # ---- не-русские символы (по умолчанию вкл; --no-nonrussian
    #      отключает, --nonrussian-regex задаёт свои паттерны) ----
    if check_nonrussian:
        regexes = list(nonrussian_regexes) if nonrussian_regexes \
            else [CHINESE_REGEX, ENGLISH_REGEX]
        for rx in regexes:
            raw = set(rx.findall(content))
            bad = [m for m in raw if m.lower() not in exclusions]
            if bad:
                preview = ", ".join(bad[:10])
                tail = f" … (+{len(bad) - 10})" if len(bad) > 10 else ""
                errors.append(f"  - Не-русские символы: {preview}{tail}")

    # ---- заголовок «Глава N» (проверка последовательности — по
    #      умолчанию вкл; --no-chapter-order отключает, --chapter-regex
    #      задаёт свой формат) ----
    non_empty = [l.strip() for l in lines if l.strip()]
    first_line = non_empty[0] if non_empty else ""
    rx = chapter_regex or CHAPTER_REGEX
    m = rx.search(first_line)
    if not m:
        if check_chapter_order:
            snippet = (first_line[:30] + "…") if len(first_line) > 30 else first_line
            errors.append(f"  - Нет «Глава N» в начале (найдено: '{snippet}')")
    else:
        try:
            cur = int(m.group(1))
        except ValueError:
            cur = 0
        if check_chapter_order and prev_inner_chapter is not None:
            expected = prev_inner_chapter + 1
            if cur != expected:
                errors.append(f"  - Нарушена последовательность: после Главы "
                              f"{prev_inner_chapter} → Глава {cur} "
                              f"(ожидалась {expected})")
        prev_inner_chapter = cur

    # ---- лишние «Глава N» после 3-й строки ----
    if check_chapter_order and len(lines) >= 4:
        for line in lines[3:]:
            if rx.search(line):
                snippet = line.strip()
                txt = (snippet[:40] + "…") if len(snippet) > 40 else snippet
                errors.append(f"  - Лишнее «Глава N» после 3-й строки: '{txt}'")
                break

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
Пресеты сравнений:
  1) POLISHED   vs redacted (1.0±0.05) + chapter (2.1±0.5)
  2) REDACTED   vs translated (1.0±0.05) + chapter (2.1±0.5)
  3) TRANSLATED vs chapter (2.1±0.5)
Примеры:
  %(prog)s                                  strict, пресет 1, весь диапазон
  %(prog)s --preset 2 --start 1 --end 50
  %(prog)s --chapters-dir ./chapters --lenient
Интерактивный выбор — в лаунчере tools/run_translate_check.py.
""",
    )
    parser.add_argument("--chapters-dir", default="./chapters",
                        help="Путь к папке с главами (по умолчанию: ./chapters)")
    parser.add_argument("--preset", choices=sorted(presets()), default="1",
                        help="Тип проверки: 1=POLISHED, 2=REDACTED, "
                             "3=TRANSLATED (по умолчанию: 1)")
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
    # ── настраиваемые коэффициенты пресетов (эвристики: подбираются
    #    опытным путём под конкретную книгу; дефолты — встроенные)
    parser.add_argument("--ratio-neighbor", type=float, default=None,
                        help="Ожидаемый ratio соседней стадии (по умолчанию "
                             f"{RATIO_NEIGHBOR}) — эвристика, подбирается "
                             "опытным путём")
    parser.add_argument("--tol-neighbor", type=float, default=None,
                        help="Допуск ratio соседней стадии (по умолчанию "
                             f"{TOL_NEIGHBOR})")
    parser.add_argument("--ratio-original", type=float, default=None,
                        help="Ожидаемый ratio с оригиналом chapter (по "
                             f"умолчанию {RATIO_ORIGINAL}) — эвристика, "
                             "подбирается опытным путём")
    parser.add_argument("--tol-original", type=float, default=None,
                        help="Допуск ratio с оригиналом (по умолчанию "
                             f"{TOL_ORIGINAL})")
    # ── отключаемые проверки (экспертные)
    parser.add_argument("--no-nonrussian", action="store_true",
                        help="Отключить проверку не-русских символов "
                             "(китайские иероглифы/латиница; по умолчанию "
                             "включена)")
    parser.add_argument("--nonrussian-regex", default=None,
                        help="Свой regexp для поиска не-русских символов "
                             "(по умолчанию: иероглифы + латиница)")
    parser.add_argument("--no-chapter-order", action="store_true",
                        help="Отключить проверку последовательности глав "
                             "(по умолчанию включена)")
    parser.add_argument("--chapter-regex", default=None,
                        help="Свой формат заголовка «Глава N» (regexp с "
                             "группой 1 = номер; по умолчанию: "
                             r"^\s*Глава\s+(\d+|\[Номер\])")
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

    type_choice = args.preset
    check_type, comparisons = presets(
        args.ratio_neighbor, args.tol_neighbor,
        args.ratio_original, args.tol_original)[type_choice]

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
            check_nonrussian=not args.no_nonrussian,
            nonrussian_regexes=(
                [re.compile(args.nonrussian_regex)]
                if args.nonrussian_regex else None),
            check_chapter_order=not args.no_chapter_order,
            chapter_regex=(re.compile(args.chapter_regex)
                           if args.chapter_regex else None),
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

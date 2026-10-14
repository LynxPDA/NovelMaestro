#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translate_check.py — проверка перевода по цепочке конвейера.

Проверки:
  • объём глав (ТОКЕНЫ выбранного токенизатора — настройка TOKENIZER):
    адаптивная статистика вместо магических констант. По ВСЕЙ книге
    (не только по диапазону) собираются отношения объёмов каждой пары;
    центр пары — медиана, разброс — MAD; глава помечается, если её
    отношение вышло за медиану ± max(пол, K·MAD/медиана), K =
    --sensitivity. Полы допуска — измеренный на реальных книгах нижний
    предел разброса: соседние стадии 2% (сидят на 1.000), пара с
    оригиналом 5%. K=0 — объёмные сравнения выключены (работают размер
    файла, regexp и последовательность). Пары по типу файлов глав:
      polished   vs redacted (соседняя) + chapter (оригинал)
      redacted   vs translated (соседняя) + chapter (оригинал)
      translated vs chapter (оригинал)
    Малая база (n < 5): разброс выборки обманчиво узок, допуск —
    расширенный пол (оригинал 15%, соседняя 2%); n < 2 — пара
    пропускается (центру взяться неоткуда).
  • минимальный размер файла (--min-file-size, БАЙТЫ; дефолт 3072);
  • regexp-проверки текста главы (--regexp-check, multiline): чистый
      стандартный regexp (Python re), всё найденное — ошибка,
      проверяются ВСЕ строки включая заголовок; «пропуск первого
      вхождения» — стандартными средствами, напр. (?<=\n)паттерн;
      по умолчанию — лишние «Глава N» (первое совпадение — заголовок
      главы, не ошибка), иероглифы CJK + кана + хангыль, латиница
      (в т.ч. полноширинная), остальные азбуки и чужие кириллические
      раскладки (і, ї, є, ґ, ў, ј, љ, …): в переводе не должно остаться
      ни одного слова и ни одной буквы другого языка; русская азбука
      А-Я Ё не проверяется; исключения — --exclude-words;
  • заголовок главы («Глава N» без учёта регистра) + сквозная
      последовательность: первое число первой непустой строки ровно
      на 1 больше предыдущего (N+1; отключается --no-sequence-check);
  • дубли папок / файлов (strict → FATAL, глава пропускается).

ЕДИНИЦЫ: счёт объёма — ТОКЕНЫ (токенизатор настройки TOKENIZER: оценка
estimate_tokens или файлы папки tokenizers/); размеры файлов — БАЙТЫ.
Форматы папок глав — единый канон core.common.parse_chapter_id.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import statistics
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

from core import settings as core_settings  # noqa: E402
from core import tokenize  # noqa: E402
from core.common import build_chapter_map, find_chapter_file  # noqa: E402


def parse_regexp_rule(line: str):
    """Строка regexp-проверки → compiled | None (пустая строка — None).

    Чистый стандартный regexp (диалект Python re), MULTILINE: «^»/«$» —
    начало/конец СТРОКИ. Без кастомных флагов и комментариев: регистр
    и прочие режимы — стандартными inline-флагами ((?i), (?s)…);
    «#» в паттерне — литеральная решётка, не комментарий.
    """
    line = line.strip()
    if not line:
        return None
    return re.compile(line, re.MULTILINE)


# ──────────────────────────────────────────────
# НАСТРОЙКИ (полы допуска — измеренный нижний предел разброса реальных
# книг: соседние стадии сидят на 1.000 с MAD 0.1–0.4%, пара с оригиналом
# — MAD 2.6–3% при любом способе счёта)
# ──────────────────────────────────────────────
MIN_FILE_SIZE = 3072          # байты
FLOORS = {"neighbor": 0.02, "original": 0.05}
#: малая база: MAD из 2–4 глав обманчиво узок — допуск расширенного пола
SMALL_N = 5
SMALL_FLOORS = {"neighbor": 0.02, "original": 0.15}


def load_exclusions(env_path=None) -> list[str]:
    """Слова-исключения: --exclude-words > TRANSLATE_CHECK_EXCLUDE_WORDS
    (.env) > пусто (ничего). Возвращает список в нижнем регистре."""
    raw = None
    try:
        from core.common import env_overlay, load_env
        env = env_overlay(
            load_env(env_path),
            ["TRANSLATE_CHECK_EXCLUDE_WORDS"])
        raw = env.get("TRANSLATE_CHECK_EXCLUDE_WORDS")
    except Exception:  # noqa: BLE001 — .env необязателен
        raw = None
    return [w.strip().lower() for w in (raw or "").split(",") if w.strip()]

# ──────────────────────────────────────────────
def _write_report(report_path: str, text: str, mode: str = "a") -> None:
    """Запись в отчёт с понятной ошибкой вместо молчаливого падения."""
    try:
        with open(report_path, mode, encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        print(f"Ошибка записи {report_path}: {exc}")

# ──────────────────────────────────────────────
# ПАРЫ СРАВНЕНИЯ И ИХ СТАТИСТИКА ПО ВСЕЙ КНИГЕ
# ──────────────────────────────────────────────
def comparisons_for(check_type: str) -> list[tuple[str, str]]:
    """Сравнения типа файлов глав: (эталон, вид пары); вид задаёт пол
    допуска: neighbor — соседняя стадия, original — оригинал (chapter)."""
    return {
        "polished": [("redacted", "neighbor"), ("chapter", "original")],
        "redacted": [("translated", "neighbor"), ("chapter", "original")],
        "translated": [("chapter", "original")],
    }[check_type]


def scan_files(chapter_map, check_type, comparisons, strict) -> dict:
    """Один проход по ВСЕЙ книге: {номер: {тип: путь}} без дублей папок.

    Дубли пропускаются — проход 2 их тоже пропустит; файл ищется единым
    поиском core.common; FATAL-сообщения поиска файл не открывают.
    """
    files: dict = {}
    wants = (check_type, *(rt for rt, _ in comparisons))
    for num, dirs in sorted(chapter_map.items()):
        if len(dirs) > 1:
            continue
        row = {}
        for want in wants:
            p, msgs = find_chapter_file(dirs[0], num, want=want,
                                        strict=strict, strict_types=True)
            if p and not any(m.startswith("[FATAL]") for m in msgs):
                row[want] = str(p)
        if row:
            files[num] = row
    return files


def collect_stats(files, check_type, comparisons, counts,
                  sensitivity: float) -> dict:
    """Статистика каждой пары по отношениям объёмов ВСЕЙ книги.

    База пары — главы, где есть оба файла и оба непусты (промежуточные
    стадии могли делаться не везде — у каждой пары своя база). Центр —
    медиана, разброс — MAD; допуск = max(пол, K·MAD/медиана); при
    n < SMALL_N разброс выборки не доверяем — допуск расширенного пола.
    Медиана устойчива, пока битых глав в базе меньше половины, — это
    честный предел метода.
    """
    ratios: dict[str, list] = {rt: [] for rt, _ in comparisons}
    for num, row in files.items():
        cn = counts.get(row.get(check_type, ""), 0)
        if not cn:
            continue
        for rt, _kind in comparisons:
            rn = counts.get(row.get(rt, ""), 0)
            if rn:
                ratios[rt].append(cn / rn)
    stats = {}
    for rt, kind in comparisons:
        xs = ratios[rt]
        n = len(xs)
        if n < 2:
            stats[rt] = {"kind": kind, "n": n, "median": 0.0, "mad": 0.0,
                         "tol": 0.0, "k": sensitivity, "small": n < SMALL_N}
            continue
        med = statistics.median(xs)
        mad = statistics.median(abs(x - med) for x in xs)
        floor = FLOORS[kind] if n >= SMALL_N else SMALL_FLOORS[kind]
        stats[rt] = {"kind": kind, "n": n, "median": med, "mad": mad,
                     "tol": max(floor, sensitivity * mad / med),
                     "k": sensitivity, "small": n < SMALL_N}
    return stats


def stats_line(check_type: str, ref_type: str, st: dict) -> str:
    """Строка отчёта о статистике одной пары."""
    if st["n"] < 2:
        return (f"  {check_type} vs {ref_type}: статистики нет "
                f"(полных глав {st['n']}) — сравнение пропускается")
    line = (f"  {check_type} vs {ref_type}: глав {st['n']}, "
            f"медиана {st['median']:.3f}, MAD {st['mad'] * 100:.1f}%, "
            f"допуск ±{st['tol'] * 100:.1f}%")
    if st["small"]:
        return line + " (мало глав — разброс выборки не учитывается)"
    return line + f" (K={st['k']:g})"


def volume_error(check_label, ref_label, check_n, ref_n, st) -> str | None:
    """Отношение объёмов главы вне допуска пары → строка-ошибка."""
    if not ref_n or not check_n:
        return None  # пустой файл ловит минимальный размер
    ratio = check_n / ref_n
    lo = st["median"] - st["tol"]
    hi = st["median"] + st["tol"]
    if lo <= ratio <= hi:
        return None
    return (f"  [{check_label} vs {ref_label}]  отношение объёма "
            f"{ratio:.2f} → ОШИБКА (медиана книги {st['median']:.2f} "
            f"± {st['tol']:.2f}; {check_label} {check_n} т., "
            f"{ref_label} {ref_n} т.)")

# ──────────────────────────────────────────────
# ПРОВЕРКА ОДНОЙ ГЛАВЫ
# ──────────────────────────────────────────────
def check_chapter(chapter_num, dir_path, check_type, comparisons,
                  strict, prev_inner_chapter, exclusions=None,
                  regexp_checks=None, min_file_size=None,
                  sequence_check=True, counts=None, stats=None):
    """Возвращает (список_ошибок, обновлённый_prev_inner_chapter).
    В список попадают ТОЛЬКО ошибки и предупреждения поиска.
    exclusions — слова-исключения, по умолчанию load_exclusions().
    regexp_checks — список compiled regexp по тексту главы: всё
      найденное — ошибка (чистый стандартный regexp, без флагов и
      комментариев; «пропуск первого вхождения» — стандартно, см.
      parse_regexp_rule); пусто = без regexp-проверок.
    min_file_size — минимальный размер файла (БАЙТЫ; None = дефолт).
    sequence_check — сквозная последовательность: первое число в
      первой непустой строке должно быть ровно на 1 больше
      предыдущего (N+1).
    counts — токены файлов (scan_files + tokenize.count_files);
    stats — статистика пар (collect_stats): None — объёмные сравнения
      выключены (--sensitivity 0)."""
    errors: list[str] = []
    if exclusions is None:
        exclusions = load_exclusions()
    min_size = MIN_FILE_SIZE if min_file_size is None else min_file_size
    counts = counts or {}

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

    # ---- цикл сравнений (допуск пары — из статистики всей книги) ----
    for ref_type, _kind in comparisons:
        st = (stats or {}).get(ref_type) or {}
        if st.get("n", 0) < 2:
            continue  # базы нет — отмечено в шапке отчёта
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
        err = volume_error(check_type, ref_type,
                           counts.get(str(file_path), 0),
                           counts.get(str(ref_path), 0), st)
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
    #      правила пользователя — чистый стандартный regexp; пусто =
    #      без проверок (весь набор — в предзаполнении web-формы)
    for rx in (regexp_checks or []):
        for h in rx.finditer(content):
            bad = h.group(0)
            if bad.lower() in exclusions:
                continue
            errors.append(f"  - По паттерну {rx.pattern}: {bad}")

    # ---- последовательность глав (единственный хардкод, отключаемый):
    #      первое число первой непустой строки — ровно на 1 больше
    #      предыдущего (N+1); строки без числа её не обновляют
    non_empty = [l.strip() for l in lines if l.strip()]
    first_line = non_empty[0] if non_empty else ""
    if sequence_check:
        nums = re.findall(r"\d+", first_line)
        try:
            cur = int(nums[0]) if nums else None
        except (ValueError, OverflowError):
            cur = None
        if cur is not None:
            if prev_inner_chapter is not None:
                expected = prev_inner_chapter + 1
                if cur != expected:
                    errors.append(f"  - Нарушена последовательность: после Главы "
                                  f"{prev_inner_chapter} → Глава {cur} "
                                  f"(ожидалась {expected})")
            prev_inner_chapter = cur

    return errors, prev_inner_chapter

# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    """Парсер translate_check: дефолты полей — из реестра (core/settings.py)."""
    parser = argparse.ArgumentParser(
        description="Скрипт проверки перевода глав.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Единицы: счёт объёма — ТОКЕНЫ (токенизатор настройки TOKENIZER);
размеры файлов — байты.
Сравнения по типу файлов глав (--check-type):
  POLISHED   vs redacted (соседняя) + chapter (оригинал)
  REDACTED   vs translated (соседняя) + chapter (оригинал)
  TRANSLATED vs chapter (оригинал)
Объём: по ВСЕЙ книге собираются отношения объёмов каждой пары; глава
помечается, если её отношение вышло за медиану ± max(пол, K·MAD/медиана).
Полы: соседние стадии 2%%, оригинал 5%%; при базе меньше 5 глав разброс
выборки не учитывается (оригинал 15%%, соседняя 2%%). --sensitivity 0 —
объёмные сравнения выключены.
Regexp-проверки текста (--regexp-check, по одной на строку): всё
найденное — ошибка, проверяются ВСЕ строки, включая заголовок главы;
^/$ — начало/конец СТРОКИ (multiline); чистый стандартный regexp
(Python re): регистр — inline-флагом (?i); комментариев и кастомных
флагов нет («#» — литерал в паттерне). Пусто = без проверок;
рекомендуемый набор — в предзаполнении web-формы. Проверка «первая
строка = заголовок главы» выражается как
\\A(?!\\s*Глава\\s+(\\d+|\\[Номер\\])) — отрицательный lookahead
(совпадение нулевой длины); прямой семантики «первая строка НЕ
заголовок → ошибка» встроенной нет. Сквозная последовательность —
первое число первой непустой строки ровно на 1 больше предыдущего
(N+1), отключается --no-sequence-check.
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
                        help="Слова-исключения через запятую: совпадения "
                             "regexp-проверок, равные этим словам, в отчёт "
                             "не попадают (пусто = ничего; иначе "
                             "TRANSLATE_CHECK_EXCLUDE_WORDS из .env)")
    # ── чувствительность объёмной проверки: единственный параметр человека;
    #    центр и допуск пары считает статистика книги
    parser.add_argument("--sensitivity", type=float, default=None, metavar="K",
                        help="K — во сколько раз итоговый допуск больше "
                             "медианного отклонения книги (MAD): статистика "
                             "собирается по ВСЕЙ книге, помечаются только "
                             "главы диапазона; 0 — объёмные сравнения "
                             "выключены (размер файла, regexp и "
                             "последовательность работают)")
    # ── regexp-проверки текста главы (экспертные)
    parser.add_argument("--regexp-check", action="append", default=[],
                        metavar="RE",
                        help="Regexp по тексту главы (multiline): всё "
                             "найденное — ошибка; можно повторять; "
                             "чистый стандартный regexp (Python re); "
                             "регистр — inline-флагом (?i); "
                             "комментариев и кастомных флагов нет; "
                             "пусто = без проверок")
    # ── структурные проверки (настраиваемые)
    parser.add_argument("--min-file-size", type=int, default=None,
                        help=f"Минимальный размер файла (БАЙТЫ; по "
                             f"умолчанию {MIN_FILE_SIZE})")
    parser.add_argument("--no-sequence-check", action="store_true",
                        default=False,
                        help="Отключить проверку сквозной "
                             "последовательности глав")
    core_settings.apply_cli_defaults(parser, "translate_check")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    strict = not args.lenient
    chapters_dir: str = args.chapters_dir
    # Исключения — CLI > .env > встроенный дефолт
    exclusions = load_exclusions()
    if args.exclude_words:
        exclusions = [w.strip().lower()
                      for w in args.exclude_words.split(",") if w.strip()]

    print("======================================")
    print("    Скрипт проверки перевода")
    print("======================================")

    sensitivity = float(args.sensitivity)
    if sensitivity < 0:
        sys.exit("Ошибка: --sensitivity — число ≥ 0 (0 = объёмные "
                 "сравнения выключены).")
    volume_on = sensitivity > 0

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
    comparisons = comparisons_for(check_type)
    regexp_checks = []
    for n, line in enumerate(args.regexp_check, 1):
        try:
            rx = parse_regexp_rule(line)
        except re.error as exc:
            # Битое правило не роняет проверку всего тома: значение из .env
            # могло разрезаться по переносу внутри самого паттерна.
            print(f"ВНИМАНИЕ: regexp-проверка №{n} пропущена ({exc}): "
                  f"{line[:60]}")
            continue
        if rx is not None:
            regexp_checks.append(rx)
    min_file_size = (args.min_file_size if args.min_file_size is not None
                     else MIN_FILE_SIZE)
    sequence_check = not args.no_sequence_check

    # ── счёт объёма: токенизатор — общий слой (реестр → .env → окружение) ──
    tid, _fallback = tokenize.resolve(str(core_settings.effective("TOKENIZER") or ""))
    unit = "токены (оценка)" if tid == tokenize.ESTIMATE_ID else f"токены ({tid})"

    # ── проход 1: статистика пар по ВСЕЙ книге (диапазон не важен) ──
    files = scan_files(chapter_map, check_type, comparisons, strict) \
        if volume_on else {}
    counts = tokenize.count_files(
        sorted({p for row in files.values() for p in row.values()}), tid) \
        if volume_on else {}
    stats = collect_stats(files, check_type, comparisons, counts,
                          sensitivity) if volume_on else {}

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
    # Фактическая команда запуска — в отчёт
    header = (
        f"=== Отчёт о проверке перевода ({check_type}) ===\n"
        f"Запуск        : {shlex.join(sys.argv)}\n"
        f"Диапазон глав : {start_cap} – {end_cap}\n"
        f"Папка глав    : {os.path.abspath(chapters_dir)}\n"
        f"Счёт объёма   : {unit}; чувствительность K={sensitivity:g}"
        f"{'' if volume_on else ' (объёмные сравнения выключены)'}\n"
        f"Regexp-проверки: {'; '.join(rx.pattern for rx in regexp_checks)}\n"
        f"Мин. размер   : {min_file_size} Б\n"
        f"Последоват.   : {'вкл' if sequence_check else 'выкл'}\n"
        f"Режим         : {'strict' if strict else 'lenient'}\n"
        f"Исключения    : {', '.join(exclusions)}\n"
        f"Дата          : {datetime.now().strftime('%c')}\n"
        f"Всего папок   : {len(chapter_map)}\n"
        "---------------------------------\n"
    )
    if volume_on:
        header += (f"Статистика (по всей книге, {len(files)} папок без дублей):\n"
                   + "\n".join(stats_line(check_type, rt, stats[rt])
                              for rt, _k in comparisons) + "\n"
                   "---------------------------------\n")
    _write_report(report_path, header, "w")

    print("--------------------------------------")
    # Фактическая команда запуска
    print(f"Запуск: {shlex.join(sys.argv)}")
    print(f"Запуск проверки ({check_type})…")
    print(f"Счёт объёма: {unit}; K={sensitivity:g}")
    if volume_on:
        print("Статистика (по всей книге):")
        for rt, _k in comparisons:
            print(stats_line(check_type, rt, stats[rt]))
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
            sequence_check=sequence_check,
            counts=counts if volume_on else None,
            stats=stats if volume_on else None,
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

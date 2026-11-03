#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translate_check.py — проверка перевода по цепочке конвейера.

Проверки:
  • объём глав (ТОКЕНЫ — оценка estimate_tokens): по ВСЕЙ книге (не только
    по диапазону) собираются отношения объёмов каждой пары; среднее пары —
    медиана отношений (устойчива к битым главам, пока их меньше половины
    базы), допуск — этот процент от среднего в обе стороны. Отношение —
    величина мультипликативная (0.9 и 1.111 — одно и то же отклонение), но
    допуск в процентах задан человеком и раздвигает среднее умножением:
    границы = среднее × (1 ± Д%). Две ручки в процентах (0 = выключить):
    --tolerance-stages — пары со стадиями (5%), --tolerance-original —
    пары с оригиналом (20%): текст перевода отходит от соседней стадии на
    доли процента, а от оригинала — на десятки, и одна цифра на обе группы
    флагует либо всё, либо ничего.
    Пары строятся от ПРОВЕРЯЕМОГО типа по цепочке polished → redacted →
    translated → chapter: у каждой пары свой эталон и своя база статистики.
      polished   vs redacted, redacted vs translated, polished   vs chapter
      redacted   vs translated, redacted vs chapter
      translated vs chapter
    Проверяемый тип сравнивается с соседней стадией ниже, стадии под ним
    сравниваются между собой, а оригинал висит на самом проверяемом типе:
    судят выбранный файл, поэтому объём книги сравнивают с ним, а не с самой
    нижней стадией. Соседние пары ниже нужны, чтобы был виден адрес потери —
    срез на редактировании (redacted ↔ translated) не прячется за ровной
    парой polished ↔ redacted.
    Нет промежуточной стадии у книги — её пара пропускается, остальные
    остаются: polished без redacted даёт polished ↔ translated и
    polished ↔ chapter.
    Подсказка «пары по стадиям ровные — правьте оригинал» появляется только
    тогда, когда ни одна пара со стадией не сказала своего: сдвиг уже
    назван, и второго сообщения про него не нужно.
    n < 2 — пара пропускается (среднему взяться неоткуда).
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

ЕДИНИЦЫ: счёт объёма — ТОКЕНЫ (встроенная оценка estimate_tokens);
размеры файлов — БАЙТЫ; допуск — ПРОЦЕНТЫ.
Форматы папок глав — единый канон core.common.parse_chapter_id.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import shlex
import statistics
import sys
from math import exp, log
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
from core.common import (  # noqa: E402
    build_chapter_map,
    count_file_tokens,
    ends_sentence,
    find_chapter_file,
)


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
# НАСТРОЙКИ
# ──────────────────────────────────────────────
MIN_FILE_SIZE = 3072          # байты
#: цепочка стадий от самой поздней к оригиналу; вид пары задаёт свою ручку
CHAIN = ("polished", "redacted", "translated", "chapter")
#: допуск пары по умолчанию, ПРОЦЕНТЫ от среднего объёма (зеркало .env)
TOLERANCE_STAGES = 5.0
TOLERANCE_ORIGINAL = 20.0


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
def comparisons_for(check_type: str, present=CHAIN) -> list[tuple[str, str, str]]:
    """Пары проверяемого типа: [(верх, низ, вид)].

    Вид пары задаёт свою ручку: stage — стадия, original — оригинал (chapter).
    Проверяемый тип даёт пару с соседней стадией ниже, стадии под ним — свои
    соседние пары, а оригинал сравнивается с самим проверяемым типом: судят
    выбранный файл, и объём книги в переводе относится к нему, а не к самой
    нижней стадии. Соседние пары ниже называют адрес потери — срез на
    редактировании (redacted ↔ translated) не прячется за ровной парой выше.

    Книга без промежуточной стадии пару пропускает, но остальные не рвёт:
    polished без redacted даёт polished ↔ translated и polished ↔ chapter.
    """
    present = tuple(present)
    stages = [t for t in CHAIN[CHAIN.index(check_type):]
              if t in present and t != "chapter"]
    pairs = [(upper, low, "stage") for upper, low in zip(stages, stages[1:])]
    if "chapter" in present:
        pairs.append((check_type, "chapter", "original"))
    return pairs


def present_from(files: dict) -> tuple[str, ...]:
    """Какие типы файлов глав в книге вообще есть (в порядке CHAIN).

    Набор берётся из уже собранного прохода: второй обход папок ради него
    не нужен.
    """
    return tuple(t for t in CHAIN if any(t in row for row in files.values()))


def scan_files(chapter_map, check_type, comparisons, strict) -> dict:
    """Один проход по ВСЕЙ книге: {номер: {тип: путь}} без дублей папок.

    Ищутся ВСЕ типы цепочки: по их наличию строятся пары главы (нет redacted —
    сравнение идёт с translated и с оригиналом), а база статистики собирается
    по всей книге, а не только по диапазону --start/--end. Дубли пропускаются
    — проход 2 их тоже пропустит; файл ищется единым поиском core.common;
    FATAL-сообщения поиска файл не открывают.
    """
    files: dict = {}
    for num, dirs in sorted(chapter_map.items()):
        if len(dirs) > 1:
            continue
        row = {}
        for want in CHAIN:
            p, msgs = find_chapter_file(dirs[0], num, want=want,
                                        strict=strict, strict_types=True)
            if p and not any(m.startswith("[FATAL]") for m in msgs):
                row[want] = str(p)
        if row:
            files[num] = row
    return files


def pair_ratios(files, comparisons, counts) -> dict:
    """Отношения объёмов по каждой паре: низ пары → {глава: отношение}.

    База пары — главы, где есть ОБА файла пары и оба непусты: сравниваются
    разные тексты (polished ↔ redacted и polished ↔ chapter — разные пары),
    поэтому одна глава может стоять в базе нескольких пар. Пустой файл
    (0 токенов) считается отсутствующим — пустоту ловит --min-file-size.
    """
    ratios: dict[str, dict] = {lower: {} for _u, lower, _k in comparisons}
    for num, row in files.items():
        for upper, lower, _kind in comparisons:
            un = counts.get(row.get(upper, ""), 0)
            ln = counts.get(row.get(lower, ""), 0)
            if un and ln:
                ratios[lower][num] = un / ln
    return ratios


def log_stats(xs) -> tuple[float, float]:
    """Среднее и разброс отношений объёмов.

    Среднее — медиана отношений: битые главы не двигают её, пока их меньше
    половины базы (у арифметического среднего такого предела нет). Разброс
    (σ, MAD логарифмов × 1.4826) считается только для справки в отчёте —
    на допуск он не влияет: допуск задан процентом.
    """
    logs = [log(x) for x in xs]
    center = exp(statistics.median(logs))
    sigma = 1.4826 * statistics.median(abs(v - log(center)) for v in logs)
    return center, sigma


def collect_stats(files, comparisons, counts,
                  tol_stages: float, tol_original: float) -> dict:
    """Статистика каждой пары по отношениям объёмов ВСЕЙ книги.

    Допуск пары — заданный процент от среднего (медианы отношений): своя
    ручка для пар со стадиями и для пар с оригиналом, 0 — пара выключена. От
    разброса книги допуск не зависит: σ остаётся в статистике только для
    справки. Медиана устойчива, пока битых глав в базе меньше половины, —
    это честный предел метода.

    Ключ статистики — НИЗ пары (тип эталонного файла); низы в наборе пар не
    повторяются: оригинал сравнивается с проверяемым типом, поэтому
    единственная пара с chapter — у него. Верх пары лежит в поле check: по
    нему строка отчёта называет пару. Пара с допуском 0 в результат всё равно
    попадает (`off`): шапка отчёта показывает её выключенной, а не молчит.
    """
    ratios = pair_ratios(files, comparisons, counts)
    stats = {}
    for upper, lower, kind in comparisons:
        xs = list(ratios[lower].values())
        n = len(xs)
        tol_pct = tol_stages if kind == "stage" else tol_original
        st = {"check": upper, "kind": kind, "n": n, "tol_pct": tol_pct,
              "off": tol_pct <= 0, "center": 0.0, "sigma": 0.0,
              "tol": 0.0}
        if not st["off"] and n >= 2:
            st["center"], st["sigma"] = log_stats(xs)
            st["tol"] = log(1 + tol_pct / 100)
        stats[lower] = st
    return stats


def active(st) -> bool:
    """Пара вообще участвует в проверке: включена и у неё есть база."""
    return bool(st) and not st.get("off") and st.get("n", 0) >= 2


def bounds(st: dict) -> tuple[float, float]:
    """Границы допуска пары: среднее, раздвинутое на tol в обе стороны."""
    if not st.get("center"):
        return 0.0, 0.0
    return st["center"] * exp(-st["tol"]), st["center"] * exp(st["tol"])


def chain_text(check_type: str, comparisons) -> str:
    """Стадии книги, между которыми строятся пары: «polished → … → chapter».

    Набор берётся из самих пар (проверяемый тип, верхи и низы) и
    выстраивается по CHAIN: строка «Стадии» в шапке отчёта не может
    разойтись с парами ниже — нет translated у книги, нет его и в цепочке.
    """
    used = {check_type}
    for up, low, _kind in comparisons:
        used.update((up, low))
    return " → ".join(t for t in CHAIN if t in used)


def stats_line(ref_type: str, st: dict) -> str:
    """Строка отчёта о статистике одной пары (n = 0 — пустая строка).

    Пара называется полем check самой статистики (верх пары) и ref_type
    (низ): в строке их два, и путать их нечем.

    Главное в ней — ГРАНИЦЫ отношения при текущем допуске: они и означают
    «допуск ±N%». Три знака после запятой, потому что у пар со стадиями
    отношение живёт в третьем знаке (1.000 ± 5%) — двумя его не видно.
    Разброс книги печатается справочно: на допуск он не влияет.
    """
    pair = f"{st.get('check', '?')} vs {ref_type}"
    if st.get("n", 0) < 1:
        return ""  # пара, которой не пользовалась ни одна глава
    if st.get("off"):
        return f"  {pair}: выключено (допуск 0%)"
    if st["n"] < 2:
        return (f"  {pair}: статистики нет "
                f"(полных глав {st['n']}) — сравнение пропускается")
    lo, hi = bounds(st)
    return (f"  {pair}: глав {st['n']}, "
            f"среднее {st['center']:.3f}, допуск ±{st['tol_pct']:g}% → "
            f"отношение {lo:.3f}–{hi:.3f} "
            f"(разброс книги {st['sigma'] * 100:.2f}%)")


def volume_error(check_label, ref_label, check_n, ref_n, st) -> str | None:
    """Отношение объёмов главы вне допуска пары → строка-ошибка.

    В строке — среднее книги, границы при текущем допуске и допуск в
    процентах: по ним видно, править главу или ослабить допуск.
    """
    if not ref_n or not check_n or not active(st):
        return None  # пустой файл ловит минимальный размер
    ratio = check_n / ref_n
    lo, hi = bounds(st)
    if lo <= ratio <= hi:
        return None
    return (f"  [{check_label} vs {ref_label}]  отношение объёма "
            f"{ratio:.3f} → ОШИБКА (вне {lo:.3f}–{hi:.3f} при допуске "
            f"±{st['tol_pct']:g}%, среднее книги {st['center']:.3f}; "
            f"{check_label} {check_n} т., {ref_label} {ref_n} т.)")

# ──────────────────────────────────────────────
# ПРОВЕРКА ОДНОЙ ГЛАВЫ
# ──────────────────────────────────────────────
def check_chapter(chapter_num, dir_path, check_type, comparisons,
                  strict, prev_inner_chapter, exclusions=None,
                  regexp_checks=None, min_file_size=None,
                  sequence_check=True, end_check=True, counts=None,
                  stats=None):
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
    end_check — глава обязана завершаться символом конца предложения
      (core.common.ends_sentence): оборванный ответ модели обрывается
      буквой или цифрой, а знак в конце — признак, не зависящий от языка.
    counts — токены файлов (scan_files + core.common.count_file_tokens);
    stats — статистика пар (collect_stats): None — объёмные сравнения
      выключены (оба допуска в нуле)."""
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

    # ---- цикл сравнений: КАЖДАЯ пара со своим эталоном ----
    #      Допуск пары — из статистики всей книги. Соседняя пара и пара с
    #      оригиналом проверяются обе: обрезка на редактировании
    #      (redacted ↔ translated) не прячется за ровной парой выше, а
    #      оригинал показывает, откуда пришёл сдвиг.
    #
    #      Верх пары — не всегда выбранный тип файлов: у пары redacted ↔
    #      translated верх — файл redacted главы. Поэтому путь каждого типа
    #      ищется один раз и ложится в кэш: пары ссылаются на типы повторно.
    paths: dict[str, str | None] = {}
    for t in [check_type] + [p for pair in comparisons for p in pair[:2]]:
        if t in paths:
            continue
        p, msgs = find_chapter_file(dir_path, chapter_num, want=t,
                                    strict=strict, strict_types=True)
        if p is None and any(m.startswith("[FATAL]") for m in msgs):
            errors.extend(msgs)
            return errors, prev_inner_chapter
        paths[t] = p if p and os.path.isfile(p) else None

    stage_failed = False
    for up, low, kind in comparisons:
        st = (stats or {}).get(low) or {}
        up_path, low_path = paths.get(up), paths.get(low)
        if not active(st) or not up_path or not low_path:
            # пара выключена либо у главы нет одного из её файлов (стадию
            # делали не везде) — остальные пары проверяются как обычно
            continue
        err = volume_error(up, low, counts.get(up_path, 0),
                           counts.get(low_path, 0), st)
        if kind == "original" and err and not stage_failed:
            # пары со стадиями ровные, а оригинал вне допуска — правят оригинал
            err = err[:-1] + "; пары по стадиям ровные — правьте оригинал)"
        if err:
            errors.append(err)
            if kind == "stage":
                stage_failed = True

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

    # ---- конец главы: оборванный ответ модели (finish_reason=stop, текст
    #      оборван посередине фразы) ловится только здесь: файл есть, размер
    #      в норме, сетевых ошибок не было. Знаки всех письменностей —
    #      проверка не зависит от языка перевода
    if end_check and not ends_sentence(content):
        tail = content.rstrip()
        if tail:
            errors.append(
                "  - Глава не завершается символом конца предложения: текст "
                f"обрывается на «…{tail[-40:]}» (последний символ — "
                f"«{tail[-1]}»); похоже на оборванный ответ модели")
        else:
            errors.append("  - Глава пуста или состоит из пробелов")

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
Единицы: счёт объёма — ТОКЕНЫ (встроенная оценка estimate_tokens);
размеры файлов — байты.
Сравнения по цепочке стадий polished → redacted → translated → chapter:
пары задаёт проверяемый тип — он сравнивается со стадиями ниже, а оригинал
с ним самим, у каждой пары свой эталон и база:
  polished   vs redacted, redacted vs translated, polished   vs chapter
  redacted   vs translated, redacted vs chapter
  translated vs chapter
Так обрезка на любой стадии видна своей парой: ровная пара выше не
маскирует сдвиг ниже, и вина не валится на оригинал.
Нет промежуточной стадии у книги — её пара пропускается, остальные
проверяются (polished без redacted: polished ↔ translated, polished ↔
chapter).
Объём: по ВСЕЙ книге собираются отношения объёмов каждой пары; среднее
пары — медиана отношений, глава помечается, если отношение вышло за
среднее × (1 ± Д%%), где Д — допуск пары в ПРОЦЕНТАХ. Ручки две:
--tolerance-stages (пары со стадиями, по умолчанию 5%%) и
--tolerance-original (оригинал, по умолчанию 20%%); 0 выключает свою
группу, обе в нуле — объёмные сравнения выключены. Разброс книги
печатается в отчёте справочно: на допуск он не влияет.
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
                        help="Тип файлов глав: polished (vs redacted, vs "
                             "translated, vs chapter), redacted (vs "
                             "translated, vs chapter), translated (vs "
                             "chapter); по умолчанию: polished")
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
    # ── допуск объёмной проверки: два параметра человека в ПРОЦЕНТАХ (пары
    #    со стадиями и пары с оригиналом расходятся на порядок); среднее
    #    пары считает статистика книги
    parser.add_argument("--tolerance-stages", type=float, default=None,
                        metavar="Д",
                        help="допуск для пар со стадиями "
                             "(polished↔redacted, redacted↔translated), "
                             "ПРОЦЕНТЫ от среднего объёма (по умолчанию 5): "
                             "среднее собирается по ВСЕЙ книге, помечаются "
                             "только главы диапазона; 0 — пары со стадиями "
                             "выключены")
    parser.add_argument("--tolerance-original", type=float, default=None,
                        metavar="Д",
                        help="допуск для пар с оригиналом (chapter), "
                             "ПРОЦЕНТЫ от среднего объёма (по умолчанию 20): "
                             "перевод отходит от оригинала на десятки "
                             "процентов, у пар со стадиями — доли процента; "
                             "0 — пары с оригиналом выключены")
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
    # имя флага — имя поля реестра: дефолт реестра в него и попадает
    parser.add_argument("--sequence-check",
                        action=argparse.BooleanOptionalAction, default=True,
                        help="Проверять сквозную последовательность глав "
                             "(--no-sequence-check — отключить)")
    parser.add_argument("--end-check",
                        action=argparse.BooleanOptionalAction, default=True,
                        help="Проверять, что глава завершается символом "
                             "конца предложения (--no-end-check — отключить): "
                             "оборванный ответ модели кончается буквой или "
                             "цифрой, знаки всех письменностей считаются "
                             "концом")
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

    tol_stages = float(args.tolerance_stages)
    tol_original = float(args.tolerance_original)
    if tol_stages < 0 or tol_original < 0:
        sys.exit("Ошибка: допуски — числа ≥ 0, ПРОЦЕНТЫ "
                 "(0 = пары выключены).")
    volume_on = tol_stages > 0 or tol_original > 0

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
    sequence_check = args.sequence_check
    end_check = args.end_check

    # ── счёт объёма: один счётчик на весь конвейер — встроенная оценка ──
    unit = "токены (оценка)"

    # ── проход 1: статистика пар по ВСЕЙ книге (диапазон не важен); пары —
    #    по фактическому набору файлов, рядом идёт запасная стадия
    files = scan_files(chapter_map, check_type, comparisons, strict) \
        if volume_on else {}
    comparisons = comparisons_for(check_type, present_from(files))
    counts = count_file_tokens(
        sorted({p for row in files.values() for p in row.values()})) \
        if volume_on else {}
    stats = collect_stats(files, comparisons, counts,
                          tol_stages, tol_original) if volume_on else {}

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
        f"Счёт объёма   : {unit}; допуск {tol_stages:g}% (пары со стадиями) "
        f"/ {tol_original:g}% (оригинал)"
        f"{'' if volume_on else ' — объёмные сравнения выключены'}\n"
        f"Regexp-проверки: {'; '.join(rx.pattern for rx in regexp_checks)}\n"
        f"Мин. размер   : {min_file_size} Б\n"
        f"Последоват.   : {'вкл' if sequence_check else 'выкл'}\n"
        f"Конец главы   : {'вкл (знаки всех письменностей)' if end_check else 'выкл'}\n"
        f"Режим         : {'strict' if strict else 'lenient'}\n"
        f"Исключения    : {', '.join(exclusions)}\n"
        f"Дата          : {datetime.now().strftime('%c')}\n"
        f"Всего папок   : {len(chapter_map)}\n"
        "---------------------------------\n"
    )
    if volume_on:
        header += (f"Стадии        : {chain_text(check_type, comparisons)}\n"
                   "Пары          : "
                   + ", ".join(f"{u} vs {low}" for u, low, _k in comparisons)
                   + f"\nСтатистика (по всей книге, {len(files)} папок без дублей):\n"
                   + "\n".join([ln for ln in (stats_line(lower, stats[lower])
                                            for _u, lower, _k in comparisons)
                               if ln])
                   + "\n---------------------------------\n")
    _write_report(report_path, header, "w")

    print("--------------------------------------")
    # Фактическая команда запуска
    print(f"Запуск: {shlex.join(sys.argv)}")
    print(f"Запуск проверки ({check_type})…")
    print(f"Счёт объёма: {unit}; допуск {tol_stages:g}% (пары со "
          f"стадиями), {tol_original:g}% (оригинал)")
    if volume_on:
        print("Статистика (по всей книге):")
        for _upper, lower, _kind in comparisons:
            line = stats_line(lower, stats[lower])
            if line:
                print(line)
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
            end_check=end_check,
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

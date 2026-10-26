#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translate_check.py — проверка перевода по цепочке конвейера.

Проверки:
  • объём глав (ТОКЕНЫ — оценка estimate_tokens): адаптивная статистика
    вместо магических констант. По ВСЕЙ книге (не только по диапазону)
    собираются отношения объёмов каждой пары; центр пары — медиана
    логарифмов отношения, разброс — MAD в тех же логарифмах (× 1.4826 —
    это σ для нормального распределения); глава помечается, если
    отклонение вышло за max(пол, K·σ). Отношение — величина
    мультипликативная (0.9 и 1.111 — одно и то же отклонение), в линейной
    шкале MAD книг с разбросом 4% занижал бы допуск.
    Две ручки K (0 = выключить): --sensitivity-stages — пары соседних
    стадий (разброс книги 0.13%), --sensitivity-original — пары с
    оригиналом (4.4%): одна ручка не может быть одновременно узкой и
    широкой. Полы допуска — измеренный на реальных книгах нижний предел
    разброса: соседние стадии 0.5%, пара с оригиналом 5%.
    Пары строятся по ЦЕПОЧКЕ стадий polished → redacted → translated →
    chapter: тип файлов главы сравнивается с ближайшей предыдущей стадией,
    у которой есть файл, и с оригиналом (chapter). Нет промежуточной
    стадии — пары просто нет, а сравнение идёт с той, что есть:
      polished   vs redacted; polished vs translated, если redacted нет
      redacted   vs translated; translated vs chapter
    Пары с оригиналом осмысленны только тогда, когда цепочка стадий ровная:
    если глава уже разошлась с соседней стадией, второй раз про оригинал не
    сообщается (это тот же сдвиг), а если цепочка ровная, а оригинал вне
    допуска — ошибка так и говорит: правьте оригинал.
    Отличие меньше --min-delta-tokens ТОКЕНОВ не флагуется (2% на главе в
    200 токенов — округление оценки, а не ошибка).
    Малая база (n < 5): разброс выборки обманчиво узок, допуск —
    расширенный пол (оригинал 15%, соседние стадии 2%); n < 2 — пара
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

ЕДИНИЦЫ: счёт объёма — ТОКЕНЫ (встроенная оценка estimate_tokens);
размеры файлов — БАЙТЫ.
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
from math import ceil, exp, log
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
# НАСТРОЙКИ (полы допуска — измеренный нижний предел разброса реальных
# книг: соседние стадии сидят на 1.000 с разбросом 0.13%, пара с оригиналом
# — 4.4%; пол 0.5% у соседних стадий страхует книгу, где разброс нулевой)
# ──────────────────────────────────────────────
MIN_FILE_SIZE = 3072          # байты
#: цепочка стадий от самой поздней к оригиналу; вид пары задаёт пол допуска
CHAIN = ("polished", "redacted", "translated", "chapter")
FLOORS = {"stage": 0.005, "original": 0.05}
#: малая база: MAD из 2–4 глав обманчиво узок — допуск расширенного пола
#: (ширина, измеренная на полной книге, а не разброс трёх глав)
SMALL_N = 5
SMALL_FLOORS = {"stage": 0.02, "original": 0.15}
#: меньшее отличие от ожидаемого объёма не флагуется вовсе: 2% на главе в
#: 200 токенов — округление оценки, а не ошибка (ТОКЕНЫ)
MIN_DELTA_TOKENS = 150
#: K, дальше которого подсказка «пройдёт при K ≥ N» бессмысленна: такой
#: разрыв — не разброс книги, а сломанная глава
MAX_HINT_K = 20


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
def comparisons_for(check_type: str, present=CHAIN) -> list[tuple[str, str]]:
    """Сравнения типа файлов глав по цепочке стадий: [(эталон, вид пары)].

    Вид пары задаёт пол допуска: stage — соседняя стадия цепочки, original —
    оригинал (chapter). Возвращаются ВСЕ кандидаты в порядке близости, но у
    конкретной главы берётся только первый доступный из stage-пар (ближайшая
    предыдущая стадия, у которой есть файл): нет redacted — сравниваем с
    translated, иначе расхождение было бы видно только по оригиналу, а он на
    порядок грубее. Поэтому в базе каждой пары лежат именно те главы, где она и
    проверяется.
    """
    present = tuple(present)
    idx = CHAIN.index(check_type)
    earlier = [t for t in CHAIN[idx + 1:] if t in present]
    out = [(t, "stage") for t in earlier if t != "chapter"]
    if "chapter" in earlier:
        out.append(("chapter", "original"))
    return out


def present_from(files: dict) -> tuple[str, ...]:
    """Какие типы файлов глав в книге вообще есть (в порядке CHAIN).

    Набор берётся из уже собранного прохода: второй обход папок ради него
    не нужен.
    """
    return tuple(t for t in CHAIN if any(t in row for row in files.values()))


def neighbor_of(row: dict, comparisons, counts=None) -> str | None:
    """Ближайшая предыдущая стадия, у которой у ЭТОЙ главы есть файл.

    Пустой файл (0 токенов) считается отсутствующим: объёма у него нет, и
    сравнение с ним не считается (пустоту ловит --min-file-size). Правило
    одно на статистику и на саму проверку — иначе база пары собиралась бы не
    по тем главам, которые она проверяет.
    """
    for rt, kind in comparisons:
        if kind != "stage" or not row.get(rt):
            continue
        if counts is None or counts.get(row[rt], 0):
            return rt
    return None


def scan_files(chapter_map, check_type, comparisons, strict) -> dict:
    """Один проход по ВСЕЙ книге: {номер: {тип: путь}} без дублей папок.

    Ищутся ВСЕ типы цепочки: по их наличию строятся пары главы (без redacted
    сравнение идёт с translated), да и в предрасчёте видны все пары книги, а
    не только выбранные --check-type. Дубли пропускаются — проход 2 их тоже
    пропустит; файл ищется единым поиском core.common; FATAL-сообщения поиска
    файл не открывают.
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


def pair_ratios(files, check_type, comparisons, counts) -> dict:
    """Отношения объёмов по каждой паре: эталон → {номер главы: отношение}.

    База пары — главы, где есть оба файла и оба непусты. Соседняя стадия у
    главы одна (ближайшая доступная) — иначе одна и та же глава попадала бы в
    базу пары, которую для неё не проверяют (glava с redacted тянула бы
    статистику polished↔translated). Промежуточные стадии могли делаться не
    везде — у каждой пары своя база, и её размер печатается отдельно.
    """
    ratios: dict[str, dict] = {rt: {} for rt, _ in comparisons}
    for num, row in files.items():
        cn = counts.get(row.get(check_type, ""), 0)
        if not cn:
            continue
        neigh = neighbor_of(row, comparisons, counts)
        for rt, kind in comparisons:
            if kind == "stage" and rt != neigh:
                continue
            rn = counts.get(row.get(rt, ""), 0)
            if rn:
                ratios[rt][num] = cn / rn
    return ratios


def log_stats(xs) -> tuple[float, float]:
    """Центр и разброс отношений объёмов — в логарифмах.

    Отношение объёмов мультипликативно: 0.9 и 1.111 — одно и то же
    отклонение в разные стороны, поэтому центр — медиана логарифмов, а
    разброс — MAD логарифмов с коэффициентом 1.4826 (у нормального
    распределения он равен σ). Возвращает (центр в единицах отношения, σ).
    """
    logs = [log(x) for x in xs]
    center = statistics.median(logs)
    sigma = 1.4826 * statistics.median(abs(v - center) for v in logs)
    return exp(center), sigma


def collect_stats(files, check_type, comparisons, counts,
                  k_stages: float, k_original: float) -> dict:
    """Статистика каждой пары по отношениям объёмов ВСЕЙ книги.

    Допуск пары — в логарифмах: max(пол, K·σ), K — своя ручка для соседних
    стадий и для оригинала. При n < SMALL_N разброс выборки не доверяем —
    допуск расширенного пола. Медиана устойчива, пока битых глав в базе
    меньше половины, — это честный предел метода.

    Пара с K = 0 в результат всё равно попадает (`off`): шапка отчёта и
    предрасчёт формы показывают её как выключенную, а не молчат о ней.
    """
    ratios = pair_ratios(files, check_type, comparisons, counts)
    stats = {}
    for rt, kind in comparisons:
        xs = list(ratios[rt].values())
        n = len(xs)
        k = k_stages if kind == "stage" else k_original
        small = n < SMALL_N
        st = {"kind": kind, "n": n, "k": k, "small": small,
              "off": k <= 0, "center": 0.0, "sigma": 0.0, "tol": 0.0}
        if not st["off"] and n >= 2:
            st["center"], st["sigma"] = log_stats(xs)
            floor = SMALL_FLOORS[kind] if small else FLOORS[kind]
            st["tol"] = max(log(1 + floor), k * st["sigma"])
        stats[rt] = st
    return stats


def active(st) -> bool:
    """Пара вообще участвует в проверке: включена и у неё есть база."""
    return bool(st) and not st.get("off") and st.get("n", 0) >= 2


def bounds(st: dict) -> tuple[float, float]:
    """Границы допуска пары: центр, раздвинутый на tol в обе стороны."""
    if not st.get("center"):
        return 0.0, 0.0
    return st["center"] * exp(-st["tol"]), st["center"] * exp(st["tol"])


def min_k_for(ratio: float, st: dict) -> int | None:
    """Наименьшее K, при котором отношение главы влезает в допуск пары.

    Допуск = max(пол, K·σ), поэтому пол K не берёт: разброс нулевой —
    None, и подсказка честно говорит, что K не поможет.
    """
    sigma = st.get("sigma", 0.0)
    if not st.get("center") or sigma <= 0:
        return None
    need = abs(log(ratio / st["center"])) / sigma
    return ceil(need) if need > st.get("k", 0) else None


def stats_payload(check_type: str, ref_type: str, st: dict) -> dict:
    """Статистика одной пары полями — для предрасчёта в форме запусков.

    Те же числа, что печатает stats_line, но без разбора текста: форма
    показывает допуск и границы отношения до запуска. `n` и `small` важнее
    коэффициентов: при малой базе MAD выборки не доверяем и допуск держит
    расширенный пол — об этом говорит `small`.
    """
    lo, hi = bounds(st)
    return {"check": check_type, "ref": ref_type, "kind": st["kind"],
            "n": st["n"], "k": st["k"], "small": bool(st["small"]),
            "off": bool(st["off"]),
            "median": round(st["center"], 3),
            "mad": round(st["sigma"] * 100, 2),
            "tol": round((exp(st["tol"]) - 1) * 100, 2),
            "lo": round(lo, 3), "hi": round(hi, 3)}


def stats_payloads(check_type: str, comparisons, stats: dict) -> list[dict]:
    """Статистика ВСЕХ пар типа файлов в порядке comparisons.

    Пара, у которой в книге нет ни одного полного набора файлов (n = 0), не
    отдаётся вовсе: запасная стадия (polished↔translated при наличии
    redacted) не используется ни одной главой — это не «выключенная пара»,
    а «такой пары у книги нет», и нулевой допуск в форме был бы ложью.
    """
    return [stats_payload(check_type, rt, stats[rt])
            for rt, _kind in comparisons
            if rt in stats and stats[rt].get("n", 0) >= 1]


def outliers(comparisons, stats: dict, ratios: dict, counts: dict,
             check_type: str, files: dict, start: int, end: int,
             min_delta: int = MIN_DELTA_TOKENS, limit: int = 50) -> list:
    """Главы диапазона, чьё отношение объёма вышло за допуск пары.

    Тот же расчёт, что у volume_error в основном проходе, но без чтения
    текстов: предпросмотр отвечает на вопрос «сколько глав поймает текущий
    K» до запуска. Возвращает записи по главам в порядке номеров со списком
    пар (`pairs` — эталон, отношение, границы).
    """
    rows: list[dict] = []
    nums = set()
    for rt, _kind in comparisons:
        nums.update(ratios.get(rt, {}))
    for num in sorted(nums):
        if num < start or num > end:
            continue
        hits = []
        stage_failed = False
        for rt, kind in comparisons:
            st = stats.get(rt) or {}
            ratio = ratios.get(rt, {}).get(num)
            if ratio is None or not active(st):
                continue
            lo, hi = bounds(st)
            if lo <= ratio <= hi:
                continue
            # то же правило, что у прогона: расхождение уже названо по
            # соседней стадии — оригинал не повторяет его второй раз
            if kind == "original" and stage_failed:
                continue
            if kind == "stage":
                stage_failed = True
            # отличие в токенах — по тем же числам, что и у прогона
            row = files.get(num) or {}
            check_n = counts.get(row.get(check_type, ""), 0)
            ref_n = counts.get(row.get(rt, ""), 0)
            if abs(check_n - st["center"] * ref_n) < min_delta:
                continue
            hint, need = volume_hint(ratio, st)
            hits.append({"ref": rt, "ratio": round(ratio, 3),
                         "lo": round(lo, 3), "hi": round(hi, 3),
                         "k": need, "hint": hint})
        if hits:
            rows.append({"chapter": num, "pairs": hits})
        if len(rows) >= limit:
            break
    return rows


def stats_line(check_type: str, ref_type: str, st: dict) -> str:
    """Строка отчёта о статистике одной пары (n = 0 — пустая строка).

    Главное в ней — ГРАНИЦЫ отношения при текущем K: они и означают
    «допуск ±N%». Три знака после запятой, потому что у соседних стадий
    отношение живёт в третьем знаке (1.000 ± 0.9%) — двумя его не видно.
    """
    if st.get("n", 0) < 1:
        return ""  # запасная пара, которой не пользовалась ни одна глава
    if st.get("off"):
        return (f"  {check_type} vs {ref_type}: выключено "
                f"(чувствительность 0)")
    if st["n"] < 2:
        return (f"  {check_type} vs {ref_type}: статистики нет "
                f"(полных глав {st['n']}) — сравнение пропускается")
    lo, hi = bounds(st)
    line = (f"  {check_type} vs {ref_type}: глав {st['n']}, "
            f"медиана {st['center']:.3f}, разброс {st['sigma'] * 100:.2f}%, "
            f"допуск ±{(exp(st['tol']) - 1) * 100:.2f}% → отношение "
            f"{lo:.3f}–{hi:.3f} (K={st['k']:g})")
    if st["small"]:
        # K напечатан и здесь: читатель ищет его в каждой строке, а при
        # маленькой базе он ни на что не влияет — поэтому и подпись
        return line + ", мало глав — разброс выборки не учитывается"
    return line


def volume_hint(ratio: float, st: dict) -> tuple[str, int | None]:
    """Подсказка к выбросу и нужный K — ОДНА на отчёт и на предрасчёт.

    Возвращает (текст, K): K — наименьшая ручка, при которой глава влезет
    в допуск (None — не влезет: допуск держит пол); текст готов к печати.
    Разрыв больше MAX_HINT_K сигм — уже не разброс книги, а сломанная
    глава: так и пишем, иначе человек будет крутить настройку до 300.
    """
    need = min_k_for(ratio, st)
    if need is None:
        return "K не поможет: допуск держит пол, а не разброс книги", None
    if need > MAX_HINT_K:
        rel = abs(log(ratio / st["center"]))
        sigma = rel / st["sigma"] if st["sigma"] else 0.0
        return (f"правьте главу: это {sigma:.0f}σ разброса книги "
                f"({(exp(rel) - 1) * 100:+.0f}% объёма), K={MAX_HINT_K} "
                f"не спасёт"), need
    return f"пройдёт при K ≥ {need}", need


def volume_error(check_label, ref_label, check_n, ref_n, st,
                 min_delta: int = MIN_DELTA_TOKENS) -> str | None:
    """Отношение объёмов главы вне допуска пары → строка-ошибка.

    В строке — границы при текущем K и сколько K не хватило: иначе
    человек не понимает, двигать ли настройку или править главу. Отличие
    меньше min_delta ТОКЕНОВ ошибкой не считается вовсе.
    """
    if not ref_n or not check_n or not active(st):
        return None  # пустой файл ловит минимальный размер
    ratio = check_n / ref_n
    lo, hi = bounds(st)
    if lo <= ratio <= hi:
        return None
    dev = abs(check_n - st["center"] * ref_n)
    if dev < min_delta:
        return None  # отличие в токенах ниже шума оценки
    hint, _need = volume_hint(ratio, st)
    return (f"  [{check_label} vs {ref_label}]  отношение объёма "
            f"{ratio:.3f} → ОШИБКА (вне {lo:.3f}–{hi:.3f} при "
            f"K={st['k']:g}, медиана книги {st['center']:.3f}; "
            f"{check_label} {check_n} т., {ref_label} {ref_n} т.; "
            f"{hint})")

# ──────────────────────────────────────────────
# ПРОВЕРКА ОДНОЙ ГЛАВЫ
# ──────────────────────────────────────────────
def check_chapter(chapter_num, dir_path, check_type, comparisons,
                  strict, prev_inner_chapter, exclusions=None,
                  regexp_checks=None, min_file_size=None,
                  sequence_check=True, counts=None, stats=None,
                  min_delta: int = MIN_DELTA_TOKENS):
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
    counts — токены файлов (scan_files + core.common.count_file_tokens);
    stats — статистика пар (collect_stats): None — объёмные сравнения
      выключены (обе чувствительности в нуле)."""
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
    stage_failed = False
    stage_checked = False
    for ref_type, kind in comparisons:
        st = (stats or {}).get(ref_type) or {}
        if not active(st):
            continue  # выключено или мало глав — отмечено в шапке отчёта
        ref_path, ref_msgs = find_chapter_file(
            dir_path, chapter_num, want=ref_type, strict=strict,
            strict_types=True)
        ref_fatal = (ref_path is None
                     and any(m.startswith("[FATAL]") for m in ref_msgs))
        if ref_fatal:
            errors.extend(ref_msgs)
            return errors, prev_inner_chapter
        if ref_path is None or not os.path.isfile(ref_path):
            # промежуточной стадии у части глав нет — пары у этой главы просто
            # нет (цепочка уже выбрала, что сравнивать: справочник по книге)
            continue
        if kind == "stage":
            stage_checked = True
        elif stage_failed:
            continue  # тот же сдвиг уже назван по соседней стадии
        err = volume_error(check_type, ref_type,
                           counts.get(str(file_path), 0),
                           counts.get(str(ref_path), 0), st, min_delta)
        if not err:
            continue
        if kind == "original" and stage_checked:
            # соседняя стадия ровная, а оригинал нет — правят оригинал
            err = err[:-1] + "; цепочка стадий ровная — правьте оригинал)"
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
  POLISHED   vs redacted (соседняя) + translated (если redacted нет) +
             chapter (оригинал)
  REDACTED   vs translated (соседняя) + chapter (оригинал)
  TRANSLATED vs chapter (оригинал)
Нет промежуточной стадии — пары нет; сравнение идёт с той, что есть.
Объём: по ВСЕЙ книге собираются отношения объёмов каждой пары; глава
помечается, если отклонение логарифма отношения вышло за
max(пол, K·σ), где σ — MAD тех же логарифмов (× 1.4826). K свой у пар
соседних стадий и у пар с оригиналом. Полы: соседние стадии 0.5%%,
оригинал 5%%; при базе меньше 5 глав разброс выборки не учитывается
(оригинал 15%%, соседние 2%%). Обе чувствительности в нуле — объёмные
сравнения выключены. Отличие меньше --min-delta-tokens ТОКЕНОВ не
флагуется.
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
                        help="Тип файлов глав: polished (по цепочке vs "
                             "redacted/translated + chapter), redacted (vs "
                             "translated + chapter), translated (vs chapter); "
                             "по умолчанию: polished")
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
    # ── чувствительность объёмной проверки: два параметра человека (пары
    #    соседних стадий и пары с оригиналом различаются по разбросу на
    #    порядок); центр и σ пары считает статистика книги
    parser.add_argument("--sensitivity-stages", type=float, default=None,
                        metavar="K",
                        help="K для пар соседних стадий (polished↔redacted, "
                             "redacted↔translated): допуск = K·σ разброса "
                             "книги, но не уже пола 0.5%%; статистика "
                             "собирается по ВСЕЙ книге, помечаются только "
                             "главы диапазона; 0 — эти пары выключены")
    parser.add_argument("--sensitivity-original", type=float, default=None,
                        metavar="K",
                        help="K для пар с оригиналом (chapter): разброс "
                             "книги тут ~4.4%%, поэтому K свой; пол 5%%; "
                             "0 — пары с оригиналом выключены")
    parser.add_argument("--min-delta-tokens", type=int, default=None,
                        metavar="N",
                        help=f"Отличие от ожидаемого объёма меньше N "
                             f"ТОКЕНОВ ошибкой не считается (по умолчанию "
                             f"{MIN_DELTA_TOKENS}): 2%% на главе в 200 "
                             f"токенов — округление оценки, а не ошибка")
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
    core_settings.apply_cli_defaults(parser, "translate_check")
    return parser


def collect_book_stats(chapters_dir: str, check_type: str,
                       k_stages: float, k_original: float, strict: bool,
                       start: int | None = None,
                       end: int | None = None,
                       min_delta: int = MIN_DELTA_TOKENS) -> dict:
    """Статистика пар по всей книге — одни числа с шапкой отчёта.

    Тот же проход (scan_files → count_file_tokens → collect_stats), но без
    записи отчёта: форма запусков показывает допуск ДО прогона. `outliers` —
    главы диапазона вне допуска: по ним видно, что дадут текущие K. Пары
    строятся по тем типам файлов, что в книге реально есть (цепочка стадий),
    поэтому объём прохода задаёт набор файлов, а не выбранный --check-type.
    """
    chapter_map = build_chapter_map(chapters_dir)
    if not chapter_map:
        return {"type": check_type, "chapters": 0, "start": 0, "end": 0,
                "stats": [], "outliers": []}
    comparisons = comparisons_for(check_type)
    files = scan_files(chapter_map, check_type, comparisons, strict)
    comparisons = comparisons_for(check_type, present_from(files))
    counts = count_file_tokens(
        sorted({p for row in files.values() for p in row.values()}))
    ratios = pair_ratios(files, check_type, comparisons, counts)
    stats = collect_stats(files, check_type, comparisons, counts,
                          k_stages, k_original)
    lo = min(chapter_map) if start is None else start
    hi = max(chapter_map) if end is None else end
    # обе ручки в нуле — пара не считается самим запуском (`active`), значит и
    # выбросов нет: иначе форма показала бы ошибки, которых прогон не найдёт
    found = outliers(comparisons, stats, ratios, counts, check_type, files,
                     lo, hi, min_delta)
    return {"type": check_type, "chapters": len(chapter_map),
            "start": lo, "end": hi,
            "stats": stats_payloads(check_type, comparisons, stats),
            "outliers": found}


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

    k_stages = float(args.sensitivity_stages)
    k_original = float(args.sensitivity_original)
    if k_stages < 0 or k_original < 0:
        sys.exit("Ошибка: чувствительности — числа ≥ 0 (0 = пары выключены).")
    volume_on = k_stages > 0 or k_original > 0
    min_delta = (MIN_DELTA_TOKENS if args.min_delta_tokens is None
                 else max(0, args.min_delta_tokens))

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
    stats = collect_stats(files, check_type, comparisons, counts,
                          k_stages, k_original) if volume_on else {}

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
        f"Счёт объёма   : {unit}; чувствительность K="
        f"{k_stages:g} (соседние стадии) / {k_original:g} (оригинал)"
        f"{'' if volume_on else ' — объёмные сравнения выключены'}\n"
        f"Мин. отличие  : {min_delta} т.\n"
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
        header += (f"Цепочка       : "
                   f"{' → '.join([check_type] + [rt for rt, _k in comparisons])}\n"
                   f"Статистика (по всей книге, {len(files)} папок без дублей):\n"
                   + "\n".join([ln for ln in (stats_line(check_type, rt,
                                                     stats[rt])
                                            for rt, _k in comparisons) if ln])
                   + "\n---------------------------------\n")
    _write_report(report_path, header, "w")

    print("--------------------------------------")
    # Фактическая команда запуска
    print(f"Запуск: {shlex.join(sys.argv)}")
    print(f"Запуск проверки ({check_type})…")
    print(f"Счёт объёма: {unit}; K={k_stages:g} (соседние стадии), "
          f"{k_original:g} (оригинал)")
    if volume_on:
        print("Статистика (по всей книге):")
        for rt, _k in comparisons:
            line = stats_line(check_type, rt, stats[rt])
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
            counts=counts if volume_on else None,
            stats=stats if volume_on else None,
            min_delta=min_delta,
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

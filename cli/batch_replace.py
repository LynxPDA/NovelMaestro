#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch_replace.py — массовые замены по regexp-правилам (чистый CLI).

Без интерактивного меню и без LLM. Правила приходят аргументами
--replace («паттерн -> замена», по одному на аргумент; web-стадия
«Массовые замены» передаёт строки формы). Паттерн — чистый стандартный
regexp (диалект Python re, MULTILINE: «^»/«$» — начало/конец СТРОКИ);
без кастомных флагов и комментариев — регистр и прочие режимы задаются
стандартными inline-флагами ((?i), (?s)…); «#» в паттерне — литерал.

Примеры:
  python batch_replace.py --replace "Хунг -> Хунь" --dry-run
  python batch_replace.py --type redacted --start 1 --end 50 \
      --replace "(?i)бессмертный -> Бессмертный"
  python batch_replace.py --replace "\\s+ -> " --replace "^  ->"

Типы файлов (--type): polished (default) | redacted | translated | chapter.
Единицы: --start/--end — номера глав (канон parse_chapter_id).
"""
import argparse
import os
import re
import sys
import unicodedata
from dataclasses import dataclass

# ── bootstrap: корень репо + обязательные зависимости ──
def _bootstrap_core() -> None:
    """Скрипт запускается из любого cwd: корень репо ищется подъёмом от себя.

    Там же проверяются обязательные зависимости: httpx — транспорт
    core.transport, dotenv — парсер .env; сам скрипт их не зовёт, но
    core.common без них не импортируется (офлайн-установка — wheels из
    vendor/, см. packaging/README.md).
    """
    from importlib.util import find_spec
    from pathlib import Path as _P
    p = _P(os.path.dirname(os.path.abspath(__file__)))
    for _ in range(6):
        if (p / "core" / "common.py").is_file():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
            break
        if p.parent == p:
            break
        p = p.parent
    missing = [m for m in ("httpx", "dotenv") if find_spec(m) is None]
    if missing:
        print("❌ Требуется: " + ", ".join(missing)
              + " — python3 -m pip install -r requirements.txt")
        sys.exit(1)

_bootstrap_core()

from core.common import (  # noqa: E402
    atomic_write,
    build_chapter_map,
    find_chapter_file,
    mark_whitespace,
    read_text_safe,
    trim_rule_left,
    trim_rule_right,
)
from core import settings as core_settings  # noqa: E402
from core.stage import Progress  # noqa: E402

# Допустимые типы файлов → значение want для find_chapter_file
FILE_TYPES = ("polished", "redacted", "translated", "chapter")


@dataclass
class Rule:
    """Одно regexp-правило замены («паттерн -> замена», NFC)."""
    pattern: str          # левая часть (NFC) — для отчёта
    replacement: str      # правая часть (NFC)
    section: str = ""     # источник правил («--replace»)

    @property
    def label(self) -> str:
        """Короткое имя правила для отчёта."""
        pat = self.pattern if len(self.pattern) <= 24 else self.pattern[:21] + "…"
        return f"{self.section}/{pat}" if self.section else pat

    def compile(self):
        """Компилирует matcher. Возвращает re.Pattern.

        MULTILINE: «^»/«$» матчат начало/конец СТРОКИ. Паттерн — чистый
        стандартный regexp: регистр и прочие режимы — inline-флагами
        ((?i), (?s)…) в самом паттерне.
        """
        return re.compile(self.pattern, re.UNICODE | re.MULTILINE)


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


# ══════════════════════════════════════════════════════════════════════
# ПАРСИНГ ФАЙЛА ПРАВИЛ
# ══════════════════════════════════════════════════════════════════════
def parse_replace_lines(lines) -> tuple[list[Rule], list[str]]:
    """Парсит пары «паттерн -> замена» из строк (--replace).

    Каждая строка — одно regexp-правило; пустая правая часть — удаление.
    У стрелки срезается только её собственный пробельный хвост, пробелы
    паттерна значимы и перед ним, и внутри: « +$» — хвостовые пробелы строк,
    «  +» — два пробела, «\\s+ -> » — сжать пробелы в один. Строка из одних
    пробелов — пустой паттерн. Паттерн — чистый стандартный regexp: регистр и
    прочие режимы — inline-флагами ((?i)…); комментариев и кастомных флагов
    нет, «#» — литерал в паттерне.

    Битая строка (нет «->», пустой паттерн, не компилируется паттерн или
    шаблон замены) → предупреждение + пропуск: остальные правила применяются,
    прогон не падает. Возвращает (rules, warnings).
    """
    rules: list[Rule] = []
    warnings: list[str] = []
    for i, raw in enumerate(lines, 1):
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        if "->" not in line:
            warnings.append(f"строка {i}: нет разделителя «->» — пропущена")
            continue
        left, right = line.split("->", 1)
        left = trim_rule_left(left)
        right = trim_rule_right(right)
        if not left:
            warnings.append(f"строка {i}: пустая левая часть — пропущена "
                            "(строка из одних пробелов — не паттерн)")
            continue
        try:
            rx = re.compile(left, re.UNICODE | re.MULTILINE)
            rx.subn(right, "")   # шаблон замены — тем же компилятором
        except re.error as exc:
            warnings.append(
                f"строка {i}: битое правило «{mark_whitespace(line)}» — "
                f"{exc.msg} — пропущена")
            continue
        rules.append(Rule(pattern=left, replacement=right,
                          section="--replace"))
    return rules, warnings


def format_rules(rules: list[Rule]) -> str:
    """Правила для отчёта: по строке на правило, пробелы видимы (·, ⏎, ⇥).

    Без меток правило «^ + -> » читается как «заменено пустотой», а замена из
    одного пробела — как «ничего».
    """
    return "\n".join(
        f"  {mark_whitespace(r.pattern)} -> {mark_whitespace(r.replacement) or '(удаление)'}"
        for r in rules)


# ══════════════════════════════════════════════════════════════════════
# ПРИМЕНЕНИЕ ПРАВИЛ К ФАЙЛУ
# ══════════════════════════════════════════════════════════════════════
def apply_rules(content: str, rules: list[Rule]):
    """Применяет все правила к тексту.

    Возвращает (new_content, stats: {label: count}), где count — число
    совпадений, которые ДЕЙСТВИТЕЛЬНО изменили текст. Текст NFC-нормализуется.
    """
    segments, stats = apply_rules_segments(content, rules)
    return "".join(t for k, t in segments if k != "del"), stats


def _cut_segments(segs, starts, a: int, b: int, edge: bool = False):
    """Кусок текущего текста [a, b) в виде сегментов; встретившиеся внутри
    «del» сохраняются (a <= pos < b; edge — включать pos == b для последнего
    куска).
    """
    out = []
    for i, (kind, t) in enumerate(segs):
        s = starts[i]
        if not t:
            continue
        if kind == "del":
            if a <= s < b or (edge and s == b):
                out.append((kind, t))
            continue
        e = s + len(t)
        if e <= a or s >= b:
            continue
        out.append((kind, t[max(s, a) - s:min(e, b) - s]))
    return out


def _flush_deleted(segs, starts, a: int, b: int):
    """Старые «del» ВНУТРИ совпадения [a, b): хронологически они появились
    раньше и визуально должны идти до нового «del».
    """
    return [(kind, t) for i, (kind, t) in enumerate(segs)
            if kind == "del" and a <= starts[i] < b]


def apply_rules_segments(content: str, rules: list[Rule]):
    """Применяет правила и возвращает (segments, stats) — разметка изменений.

    segments — список пар (kind, text) в порядке итогового текста:
    «keep» — осталось без изменений, «del» — удалено правилом,
    «ins» — вставлено заменой. «del» в итоговом тексте НЕ участвует
    (склейка без него даёт результат apply_rules), но сохраняет позицию
    в потоке — для подсветки удалённого. Правила применяются
    последовательно, следующие замены видят только итоговый текст.
    Совпадение, совпадающее со своей заменой (нулевое совпадение «^ +» на
    строке без отступа), сегментов не создаёт и в stats не попадает.
    Stats: {label: count}, как в apply_rules; текст NFC-нормализуется.
    """
    content = _nfc(content)
    segs = [("keep", content)] if content else []
    stats = {}
    for rule in rules:
        rx = rule.compile()
        # текущий текст — ВСЁ кроме «del» (удалённое в нём не участвует)
        cur = "".join(t for k, t in segs if k != "del")
        # координаты сегментов в текущем тексте; «del» — точка
        starts = []
        pos = 0
        for k, t in segs:
            starts.append(pos)
            if k != "del":
                pos += len(t)

        matches = list(rx.finditer(cur))
        if not matches:
            continue
        new_segs = []
        changed = 0
        last = 0
        for m in matches:
            a, b = m.span()
            rep = m.expand(rule.replacement)
            if cur[a:b] == rep:
                # замена совпала с текстом — не замена: без del/ins и счётчика
                new_segs.extend(_cut_segments(segs, starts, last, b))
                last = b
                continue
            new_segs.extend(_cut_segments(segs, starts, last, a))
            new_segs.extend(_flush_deleted(segs, starts, a, b))
            if a != b:
                new_segs.append(("del", cur[a:b]))
            if rep:
                new_segs.append(("ins", rep))
            changed += 1
            last = b
        new_segs.extend(_cut_segments(segs, starts, last, len(cur), edge=True))
        segs = new_segs
        if changed:
            stats[rule.label] = stats.get(rule.label, 0) + changed
    return segs, stats


def process_file(filepath, rules: list[Rule], dry_run: bool = False):
    """Применяет правила к одному файлу. Возвращает stats или None (без изменений)."""
    content = read_text_safe(filepath)
    new_content, stats = apply_rules(content, rules)
    if new_content != content:
        if not dry_run:
            atomic_write(filepath, new_content)
        return stats
    return None


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════
def build_parser() -> argparse.ArgumentParser:
    """Парсер batch_replace: дефолты полей — из реестра (core/settings.py)."""
    ap = argparse.ArgumentParser(
        description="Массовые замены по regexp-правилам (polished/redacted/translated/chapter).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--type", dest="file_type", choices=FILE_TYPES,
                    default="polished",
                    help="Тип файлов глав (default: polished).")
    ap.add_argument("--chapters-dir", "--chapters_dir", dest="chapters_dir",
                    default="./chapters",
                    help="Директория глав (default: ./chapters).")
    ap.add_argument("--start", type=int, default=None,
                    help="Номер первой главы (default: минимум найденных).")
    ap.add_argument("--end", type=int, default=None,
                    help="Номер последней главы (default: максимум найденных).")
    ap.add_argument("--replace", action="append", default=[],
                    metavar="PAT -> REPL",
                    help="Regexp-замена (можно несколько); PAT -> пусто — "
                         "удаление. Паттерн — чистый стандартный regexp "
                         "(Python re, MULTILINE); регистр — inline-флагом "
                         "(?i); комментариев и кастомных флагов нет. У стрелки "
                         "срезается только её пробельный хвост: пробелы "
                         "паттерна значимы и перед ним, и внутри (« +$ ->» — "
                         "хвостовые пробелы строк, «\\s+ -> » — сжать их в "
                         "один). Осторожно со звёздочкой: она матчит и пустую "
                         "позицию, «^ * -> » вставит пробел в начало КАЖДОЙ "
                         "строки.")
    ap.add_argument("--dry-run", "--dry_run", dest="dry_run",
                    action="store_true",
                    help="Показать замены, не изменяя файлы.")
    core_settings.apply_cli_defaults(ap, "batch_replace")
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    # Фактическая команда запуска
    import shlex as _shlex
    import sys as _sys
    print(f"Запуск: {_shlex.join(_sys.argv)}")

    # ── Правила: только из --replace (файл replacements.txt — deprecated) ──
    rules, warnings = parse_replace_lines(args.replace)
    for w in warnings:
        print(f"⚠ {w}")
    if not rules:
        print("❌ В --replace нет ни одной корректной замены.")
        return 1

    # ── Карта глав ──
    chapter_map = build_chapter_map(args.chapters_dir)
    if not chapter_map:
        print(f"❌ В '{args.chapters_dir}' главы не найдены.")
        return 1
    nums = sorted(chapter_map)
    start = args.start if args.start is not None else nums[0]
    end = args.end if args.end is not None else nums[-1]
    if start > end:
        print(f"❌ Диапазон некорректен: --start {start} > --end {end}.")
        return 1
    selected = [n for n in nums if start <= n <= end]

    want = args.file_type
    print(f"Правил: {len(rules)} | тип: {want} | главы: "
          f"{len(selected)} ({start}–{end})" + (" | DRY-RUN" if args.dry_run else ""))
    print("Правила (пробел — ·, таб — \\t, перевод строки — ⏎):")
    print(format_rules(rules))
    print()

    total_files_changed = 0
    total_replacements = 0
    global_stats = {}
    skipped = 0

    progress = Progress(len(selected), "Массовые замены", unit="глава", bar=True)
    progress.start()
    for num in selected:
        for dir_path in chapter_map[num]:
            filepath, warns = find_chapter_file(dir_path, num, want=want,
                                                strict=True)
            for w in warns:
                progress.log(f"  ⚠ {w}")
            if filepath is None:
                skipped += 1
                continue
            stats = process_file(filepath, rules, dry_run=args.dry_run)
            if stats:
                total_files_changed += 1
                n_file = sum(stats.values())
                total_replacements += n_file
                for label, cnt in stats.items():
                    global_stats[label] = global_stats.get(label, 0) + cnt
                details = ", ".join(f"{l}: {c}" for l, c in sorted(
                    stats.items(), key=lambda x: -x[1]))
                prefix = "[DRY]" if args.dry_run else "[FIX]"
                progress.log(f"  {prefix} Глава {num}: {n_file} замен ({details})")
        progress.step()
    progress.close()

    print()
    print("=" * 50)
    print(f"Глав обработано: {len(selected)} (пропущено: {skipped})")
    print(f"Файлов изменено: {total_files_changed}")
    print(f"Всего замен:     {total_replacements}")
    if global_stats:
        print("По правилам:")
        for label, cnt in sorted(global_stats.items(), key=lambda x: -x[1]):
            print(f"  {label}: {cnt}")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())

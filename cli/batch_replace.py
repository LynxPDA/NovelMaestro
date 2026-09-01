#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch_replace.py — массовые замены по внешнему файлу правил (чистый CLI).

Без интерактивного меню и без LLM. Замены применяются к файлам выбранного
типа в папках глав. Правила — текстовый файл (по умолчанию
`prompts/replacements.txt` в папке проекта), формат описан в шаблоне
`templates/replacements.txt.example`.

Примеры:
  python batch_replace.py --dry-run
  python batch_replace.py --type redacted --start 1 --end 50
  python batch_replace.py --rules-file my_rules.txt --regex

Типы файлов (--type): polished (default) | redacted | translated | chapter.
Единицы: --start/--end — номера глав (канон parse_chapter_id).
"""
import argparse
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple

# ── bootstrap: поиск core/common.py подъёмом от скрипта ──
def _bootstrap_core() -> None:
    from pathlib import Path as _P
    p = _P(os.path.dirname(os.path.abspath(__file__)))
    for _ in range(6):
        if (p / "core" / "common.py").is_file():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
            return
        if p.parent == p:
            break
        p = p.parent

_bootstrap_core()

from core.common import (  # noqa: E402
    atomic_write,
    build_chapter_map,
    emit_progress,
    find_chapter_file,
    read_text_safe,
    strip_rule_flags,
    trim_rule_left,
    trim_rule_right,
)

# Допустимые типы файлов → значение want для find_chapter_file
FILE_TYPES = ("polished", "redacted", "translated", "chapter")


@dataclass
class Rule:
    """Одно правило замены."""
    pattern: str          # исходная левая часть (NFC) — для отчёта
    replacement: str      # правая часть (NFC)
    ignore_case: bool
    is_regex: bool
    section: str = ""     # последний заголовок секции «## …»

    @property
    def label(self) -> str:
        """Короткое имя правила для отчёта."""
        pat = self.pattern if len(self.pattern) <= 24 else self.pattern[:21] + "…"
        return f"{self.section}/{pat}" if self.section else pat

    def compile(self):
        """Компилирует matcher. Возвращает re.Pattern (literal экранируется).

        MULTILINE: «^»/«$» матчат начало/конец СТРОКИ (literal не
        страдает — re.escape экранирует якоря).
        """
        flags = re.UNICODE | re.MULTILINE
        if self.ignore_case:
            flags |= re.IGNORECASE
        src = self.pattern if self.is_regex else re.escape(self.pattern)
        return re.compile(src, flags)

    def sub(self, compiled, content: str) -> Tuple[str, int]:
        """Применяет замену. В literal-режиме правая часть — дословно."""
        if self.is_regex:
            return compiled.subn(self.replacement, content)
        # literal: без обработки обратных ссылок в замене
        repl = self.replacement
        return compiled.subn(lambda _m: repl, content)


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


# ══════════════════════════════════════════════════════════════════════
# ПАРСИНГ ФАЙЛА ПРАВИЛ
# ══════════════════════════════════════════════════════════════════════
def parse_replace_lines(lines) -> tuple[List[Rule], List[str]]:
    """Парсит пары «паттерн -> замена» из строк (--replace).

    Каждая строка — одно regexp-правило; пустая правая часть — удаление.
    Значимые пробелы сохраняются: «^  ->» (отступ строки), «\\s+ -> »
    (сжатие пробелов). Флаги в конце строки (как в файле правил):
    « |i» — не учитывать регистр, « |r» — без эффекта (всегда regexp).
    Возвращает (rules, warnings): битая строка → предупреждение + пропуск.
    """
    rules: List[Rule] = []
    warnings: List[str] = []
    for i, raw in enumerate(lines, 1):
        line = raw.rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        line, flags = strip_rule_flags(line)
        if "->" not in line:
            warnings.append(f"строка {i}: нет разделителя «->» — пропущена")
            continue
        left, right = line.split("->", 1)
        left = trim_rule_left(left)
        right = trim_rule_right(right)
        if not left:
            warnings.append(f"строка {i}: пустая левая часть — пропущена")
            continue
        rules.append(Rule(pattern=left, replacement=right,
                          ignore_case="i" in flags, is_regex=True,
                          section="--replace"))
    return rules, warnings


def parse_rules(rules_file, force_regex: bool = False):
    """Читает файл правил.

    Возвращает (rules: List[Rule], warnings: List[str]).
    Битая строка → предупреждение + пропуск. Ни одного правила → [].
    """
    rules: List[Rule] = []
    warnings: List[str] = []
    try:
        text = read_text_safe(rules_file)
    except OSError as e:
        return [], [f"Не удалось прочитать файл правил: {e}"]

    section = ""
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\n\r")
        stripped = line.strip()
        if not stripped or stripped.startswith("#") and not stripped.startswith("##"):
            continue
        if stripped.startswith("##"):
            section = stripped[2:].strip()
            continue

        # Флаги — в самом конце строки: « |ir» (ровно один пробел перед «|»)
        line, flags = strip_rule_flags(line)

        # Разделитель: табуляция или первое «->»
        if "\t" in line:
            left, right = line.split("\t", 1)
        elif "->" in line:
            left, right = line.split("->", 1)
        else:
            warnings.append(f"строка {lineno}: нет разделителя «->» — пропущена")
            continue

        left = trim_rule_left(left)
        right = trim_rule_right(right)
        if not left:
            warnings.append(f"строка {lineno}: пустая левая часть — пропущена")
            continue

        rules.append(Rule(
            pattern=left,
            replacement=right,
            ignore_case="i" in flags,
            is_regex=force_regex or "r" in flags,
            section=section,
        ))
    return rules, warnings


# ══════════════════════════════════════════════════════════════════════
# ПРИМЕНЕНИЕ ПРАВИЛ К ФАЙЛУ
# ══════════════════════════════════════════════════════════════════════
def apply_rules(content: str, rules: List[Rule]):
    """Применяет все правила к тексту.

    Возвращает (new_content, stats: {label: count}). Текст NFC-нормализуется.
    """
    content = _nfc(content)
    stats = {}
    for rule in rules:
        compiled = rule.compile()
        content, n = rule.sub(compiled, content)
        if n > 0:
            stats[rule.label] = stats.get(rule.label, 0) + n
    return content, stats


def process_file(filepath, rules: List[Rule], dry_run: bool = False):
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
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Массовые замены по правилам из файла (polished/redacted/translated/chapter).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--rules-file", "--rules_file", dest="rules_file",
                    default="prompts/replacements.txt",
                    help="Файл правил (default: prompts/replacements.txt).")
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
                         "удаление. Флаги в конце строки: « |i» — регистр, "
                         "« |r» — без эффекта. Если задан, файл правил не читается.")
    ap.add_argument("--regex", action="store_true",
                    help="Все строки правил трактовать как regex.")
    ap.add_argument("--dry-run", "--dry_run", dest="dry_run",
                    action="store_true",
                    help="Показать замены, не изменяя файлы.")
    args = ap.parse_args(argv)
    # R9: фактическая команда запуска
    import shlex as _shlex
    import sys as _sys
    print(f"Запуск: {_shlex.join(_sys.argv)}")

    # ── Правила ──
    if args.replace:
        # --replace: только regexp-пары из аргументов (файл не читается)
        rules, warnings = parse_replace_lines(args.replace)
        for w in warnings:
            print(f"⚠ {w}")
        if not rules:
            print(f"❌ В --replace нет ни одной корректной замены.")
            return 1
    else:
        rules, warnings = parse_rules(args.rules_file,
                                      force_regex=args.regex)
        for w in warnings:
            print(f"⚠ {w}")
        if not rules:
            if warnings and warnings[0].startswith("Не удалось прочитать"):
                print(f"❌ {warnings[0]}")
            else:
                print(f"❌ Файл правил {args.rules_file!r} не содержит ни одной замены.")
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
    print()

    total_files_changed = 0
    total_replacements = 0
    global_stats = {}
    skipped = 0

    for i, num in enumerate(selected, 1):
        for dir_path in chapter_map[num]:
            filepath, warns = find_chapter_file(dir_path, num, want=want,
                                                strict=True)
            for w in warns:
                print(f"  ⚠ {w}")
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
                print(f"  {prefix} Глава {num}: {n_file} замен ({details})")
        emit_progress(i, len(selected), "Массовые замены")

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

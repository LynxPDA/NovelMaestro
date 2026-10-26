#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
archive.py — упаковка проектов в архивы `.nmz` и обратная распаковка.

Скрипт для запусков web (и ручной работы): пачка проектов упаковывается
одним прогоном, каждая книга — строкой в лог. Никакой интерактивности и
никаких подтверждений: их задаёт интерфейс до запуска (AGENTS §3).

Примеры:
  python cli/archive.py pack DONE/Книга
  python cli/archive.py pack DONE/Книга_1 DONE/Книга_2 --skip logs --skip tmp
  python cli/archive.py unpack DONE/Книга --gc-history

Перед упаковкой история проекта упаковывается (`git gc` через dulwich):
рыхлые объекты боевой книги занимают вдвое больше места, чем сама история,
и без gc архив получается на десятки мегабайт больше.

`--skip` — имена папок верхнего уровня проекта, которые в архив не входят
(по умолчанию ничего не исключается: логи и состояние вкладок — тоже часть
книги; исключение стоит делать осознанно и с --skip).
"""
from __future__ import annotations

import argparse
import os
import sys


# ── bootstrap: корень репо в sys.path (AGENTS §4) ──
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

from core import archive as arc  # noqa: E402
from core.common import emit_progress, web_progress_enabled  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Упаковка проектов в архивы .nmz и распаковка",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Проекты указываются как <раздел>/<имя> относительно --projects-dir.")
    ap.add_argument("op", choices=("pack", "unpack"), help="операция")
    ap.add_argument("projects", nargs="+", metavar="РАЗДЕЛ/ИМЯ",
                    help="проекты (через пробел)")
    ap.add_argument("--projects-dir", default="projects",
                    help="корень проектов (по умолчанию projects)")
    ap.add_argument("--skip", action="append", default=[], metavar="ПАПКА",
                    help="не включать папку верхнего уровня (можно многократно)")
    ap.add_argument("--gc-history", action="store_true",
                    help="упаковать объекты истории перед архивацией")
    ap.add_argument("--dry-run", action="store_true",
                    help="только показать, что было бы сделано")
    return ap


def _split(item: str) -> tuple[str, str] | None:
    """`раздел/имя` → части; без слэша — None (ошибка в логе, а не падение)."""
    section, sep, name = item.partition("/")
    if not sep or not section or not name:
        return None
    return section, name


def _say(msg: str) -> None:
    print(msg, flush=True)


def _progress(done: int, total: int) -> None:
    """Прогресс по файлам: в web — событие, в CLI — редкая строка."""
    if web_progress_enabled():
        emit_progress(done, total)
    elif total and (done == total or done % 512 == 0):
        _say(f"  … {done}/{total}")


def _pack_one(prj, root, section: str, name: str, skip: list[str],
              gc_history: bool) -> bool:
    exists = prj.project_dir(root, section, name).is_dir()
    if not exists:
        _say(f"⊘ {section}/{name}: проекта нет (возможно, уже в архиве)")
        return False
    if gc_history:
        from web import history as hs
        got = hs.pack_objects(prj.project_dir(root, section, name))
        if got["packed"]:
            _say(f"  история упакована: объектов {got['before']} → {got['after']}")
    ok, res = prj.archive_project(root, section, name, skip=skip,
                                  progress=_progress)
    if not ok:
        _say(f"✗ {section}/{name}: {res}")
        return False
    size = arc.archive_size(arc.archive_path(root / section, name))
    _say(f"✓ {section}/{name}: файлов {res.get('files', 0)}, "
         f"{res.get('bytes', 0)} → {size} байт "
         f"(сжатие: {res.get('compression', '?')})")
    return True


def _unpack_one(prj, root, section: str, name: str) -> bool:
    ok, res = prj.unarchive_project(root, section, name, progress=_progress)
    if not ok:
        _say(f"✗ {section}/{name}: {res}")
        return False
    _say(f"✓ {section}/{name}: распакован")
    return True


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    root = arc.Path(args.projects_dir).resolve() if hasattr(arc, "Path") else \
        __import__("pathlib").Path(args.projects_dir).resolve()
    from core import projects as prj

    targets = []
    for item in args.projects:
        parts = _split(item)
        if parts is None:
            _say(f"✗ {item}: ожидается РАЗДЕЛ/ИМЯ")
            continue
        targets.append(parts)
    if not targets:
        _say("❌ Нечего делать: не разобрано ни одного проекта.")
        return 1

    _say(f"Операция: {args.op} | проектов: {len(targets)} | "
         f"корень: {root}")
    if args.skip:
        _say(f"Исключено из архива: {', '.join(sorted(set(args.skip)))}")
    _say(f"Сжатие: {arc.backend()['label']}")
    if args.dry_run:
        for section, name in targets:
            _say(f"  · {section}/{name}")
        return 0

    ok_count = 0
    for section, name in targets:
        if args.op == "pack":
            if _pack_one(prj, root, section, name, args.skip,
                         args.gc_history):
                ok_count += 1
        elif _unpack_one(prj, root, section, name):
            ok_count += 1
    _say("=" * 50)
    _say(f"Готово: {ok_count} из {len(targets)}")
    return 0 if ok_count == len(targets) else 1


if __name__ == "__main__":
    raise SystemExit(main())

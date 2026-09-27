#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка юзерскриптов из частей (src/ → один .user.js).

Юзерскрипты ставятся ОДНИМ файлом (менеджеры скриптов по расширению
`.user.js` предлагают установку, каталоги тоже ждут один файл), поэтому
публикуемый артефакт обязан оставаться единым. Разработку же вести в
каталоге скрипта:

    tools/<скрипт>/<имя>.user.js   — артефакт: генерируется, руками не править
    tools/<скрипт>/meta.js         — баннер ==UserScript==, единственный источник @version
    tools/<скрипт>/src/000-open.js  — открывающая строка IIFE-обёртки
    tools/<скрипт>/src/NNN-slug.js  — части, УЖЕ лежащие на финальном отступе
    tools/<скрипт>/src/900-close.js — закрывающая строка обёртки

Нумерация частей — с шагом 10 (010, 020, 030 …): вставка новой части — это
новый файл 025-*.js, а не переименование всего хвоста. Имена сортируются как
числа, порядок склейки = порядок имён.

Сборщик сознательно тупой: баннер, пустая строка, части в порядке имён —
без переотступов, переносов и минификации. Единственные преобразования —
CRLF→LF и подстановка {{VERSION}}. Благодаря этому собранный артефакт
побайтово равен тому, что было бы написано руками, и git diff артефакта
остаётся читаемым.

Порядок частей в Lite — не косметика: секция шелла создаёт DOM-ссылки,
на которые смотрит остальной UI, а «События» и «Инициализация»
исполняются сразу. Порядок = порядок секций исходного файла, менять его
нельзя.

Единицы измерения: файл — текст, размеров в сборке нет; `@version` —
номер релиза юзерскрипта (не VERSION конвейера).

Запуск: python3 tools/build_userscripts.py --check"""

from __future__ import annotations

import argparse
import difflib
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

VERSION_TOKEN = "{{VERSION}}"
VERSION_RE = re.compile(r"^//[ \t]*@version[ \t]+(\S+)[ \t]*$", re.MULTILINE)
OPEN_PART = "000-open.js"
CLOSE_PART = "900-close.js"
TOOLS_DIR = Path(__file__).resolve().parent

logger = logging.getLogger("userscripts")


def _bootstrap_core() -> None:
    p = Path(os.path.dirname(os.path.abspath(__file__)))
    for _ in range(6):
        if (p / "core" / "common.py").is_file():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
            return
        if p.parent == p:
            break
        p = p.parent


_bootstrap_core()
from core.common import atomic_write, log_argv  # noqa: E402


def setup_logging() -> logging.Logger:
    """Лог в stderr: сборка — редкая ручная команда, лог-файл в репо не нужен."""
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
    logger.addHandler(sh)
    return logger


def read_lf(path: Path) -> str:
    """Текст файла с нормализованным переводом строк (единственное преобразование)."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


def list_script_dirs() -> list[Path]:
    """Каталоги юзерскриптов: есть `meta.js`, `src/` и ровно один `*.user.js`."""
    out = []
    for d in sorted(p for p in TOOLS_DIR.iterdir() if p.is_dir()):
        if not (d / "meta.js").is_file():
            continue
        if not (d / "src").is_dir():
            raise SystemExit(f"{d}: есть meta.js, но нет каталога src/ — раскладка неполная")
        artifacts = sorted(d.glob("*.user.js"))
        if len(artifacts) != 1:
            raise SystemExit(f"{d}: ожидался ровно один *.user.js, найдено {len(artifacts)}")
        out.append(d)
    return out


def script_version(meta: str) -> str:
    """Версия юзерскрипта из баннера — она же подставляется в части."""
    m = VERSION_RE.search(meta)
    if not m:
        raise SystemExit("в meta.js нет строки «// @version <версия>» — версию брать не откуда")
    return m.group(1)


def build_script(d: Path) -> tuple[Path, str, int]:
    """Собрать артефакт каталога: (путь артефакта, собранный текст, число частей)."""
    meta = read_lf(d / "meta.js")
    version = script_version(meta)
    parts = sorted((d / "src").glob("*.js"))
    if not parts:
        raise SystemExit(f"{d}/src: нет ни одной части")
    if parts[0].name != OPEN_PART or parts[-1].name != CLOSE_PART:
        raise SystemExit(f"{d}/src: первым обязан лежать {OPEN_PART}, последним — {CLOSE_PART}")
    body = "".join(read_lf(p).replace(VERSION_TOKEN, version) for p in parts)
    text = meta.rstrip("\n") + "\n\n" + body
    if not text.endswith("\n"):
        text += "\n"
    if VERSION_TOKEN in text:
        raise SystemExit(f"{d}: в собранном артефакте остался {VERSION_TOKEN}")
    return sorted(d.glob("*.user.js"))[0], text, len(parts)


def first_diff(old: str, new: str, context: int = 2, limit: int = 14) -> str:
    """Первые строки различий — про рассинхрон сообщать человекочитаемо."""
    return "\n".join(list(difflib.unified_diff(
        old.splitlines(), new.splitlines(), n=context, lineterm=""))[:limit])


def node_check(path: Path) -> None:
    """Синтаксис проверяется только на артефакте: части по отдельности не парсятся."""
    node = shutil.which("node")
    if not node:
        logger.warning("node не найден — синтаксис %s не проверен", path.name)
        return
    res = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"node --check {path.name}: {res.stderr.strip()}")
    logger.info("node --check: %s — ОК", path.name)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    ap = argparse.ArgumentParser(description="Сборка юзерскриптов из частей (src/ → .user.js)")
    ap.add_argument("--check", action="store_true",
                    help="ничего не писать: сверить собранный текст с закоммиченным артефактом")
    ap.add_argument("--script", metavar="ИМЯ_КАТАЛОГА",
                    help="обработать только этот каталог tools/ (напр. NovelMaestro_Lite)")
    ap.add_argument("--node-check", action="store_true", help="проверить артефакты через node --check")
    ap.add_argument("--list", action="store_true", help="список скриптов с версиями и числом частей")
    args = ap.parse_args(argv)
    log_argv(logger)

    dirs = list_script_dirs()
    if args.script:
        dirs = [d for d in dirs if d.name == args.script]
        if not dirs:
            raise SystemExit(f"каталог tools/{args.script} среди скриптов не найден")

    if args.list:
        for d in dirs:
            art, text, n = build_script(d)
            print(f"{d.name}: {art.name} v{script_version(read_lf(d / 'meta.js'))} "
                  f"({n} частей, {len(text.splitlines())} строк)")
        return 0

    stale = []
    for d in dirs:
        art, text, n = build_script(d)
        version = script_version(read_lf(d / "meta.js"))
        if args.check:
            current = read_lf(art) if art.exists() else ""
            if current == text:
                logger.info("%s: %s актуален (%d частей, v%s)", d.name, art.name, n, version)
            else:
                logger.error("%s: %s расходится со сборкой:\n%s", d.name, art.name,
                             first_diff(current, text))
                stale.append(d.name)
        else:
            atomic_write(str(art), text)
            logger.info("%s: собран %s (%d частей, v%s, %d строк)", d.name, art.name, n,
                        version, len(text.splitlines()))
        if args.node_check:
            node_check(art)

    if stale:
        raise SystemExit(f"артефакты устарели: {', '.join(stale)} — "
                         "запусти python3 tools/build_userscripts.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка юзерскриптов из частей (src/ → один .user.js).

Юзерскрипты ставятся ОДНИМ файлом (менеджеры скриптов по расширению
`.user.js` предлагают установку, каталоги тоже ждут один файл), поэтому
публикуемый артефакт обязан оставаться единым. Разработку же вести в
каталоге скрипта:

    tools/<скрипт>/<имя>.user.js   — артефакт: генерируется, руками не править
    tools/<скрипт>/<имя>.meta.js   — он же для проверки обновлений: тот же баннер
                                     без тела (его и опрашивает менеджер)
    tools/<скрипт>/meta.js         — баннер ==UserScript==, единственный источник @version
    tools/<скрипт>/src/000-open.js  — открывающая строка IIFE-обёртки
    tools/<скрипт>/src/NNN-slug.js  — части, УЖЕ лежащие на финальном отступе
    tools/<скрипт>/src/900-close.js — закрывающая строка обёртки

Нумерация частей — с шагом 10 (010, 020, 030 …): вставка новой части — это
новый файл 025-*.js, а не переименование всего хвоста. Имена сортируются как
числа, порядок склейки = порядок имён.

Ссылки `@downloadURL`/`@updateURL` в баннер дописывает сборщик, и ведут они на
raw GitHub, а не на CDN: jsDelivr отдаёт файлы ветки с
`cache-control: public, max-age=604800`, пуш кэш не снимает, и менеджер может
неделей видеть старый `@version`. raw отдаёт то же самое с `max-age=300`.

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
META_CLOSE_RE = re.compile(r"^//[ \t]*==/UserScript==", re.MULTILINE)
# Ссылка проверки обновлений: raw GitHub той же ветки, в которой живёт репо
RAW_BASE = "https://raw.githubusercontent.com/LynxPDA/NovelMaestro/main"
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


def script_urls(d: Path, artifact: Path) -> tuple[str, str]:
    """(ссылка установки, ссылка проверки обновлений) этого артефакта."""
    rel = artifact.relative_to(TOOLS_DIR.parent).as_posix()
    return f"{RAW_BASE}/{rel}", f"{RAW_BASE}/{rel[:-8]}.meta.js"


def inject_urls(meta: str, urls: tuple[str, str]) -> str:
    """Дописать @downloadURL/@updateURL перед закрывающим тегом блока метаданных.

    В `meta.js` их быть не должно: путь вычисляется из раскладки, и руками его
    держать нельзя — переезд скрипта по дереву молча сломал бы обновления.
    """
    if "@downloadURL" in meta or "@updateURL" in meta:
        raise SystemExit("meta.js: @downloadURL/@updateURL дописывает сборщик "
                         "— уберите их из баннера")
    m = list(META_CLOSE_RE.finditer(meta))
    if not m:
        raise SystemExit("meta.js: не найден закрывающий тег «// ==/UserScript==»")
    start = m[-1].start()
    block = f"// @downloadURL  {urls[0]}\n// @updateURL    {urls[1]}\n"
    return meta[:start] + block + meta[start:]


def build_script(d: Path) -> tuple[Path, str, Path, str, int]:
    """Собрать каталог: (артефакт, текст артефакта, файл метаданных, его текст, части)."""
    meta = read_lf(d / "meta.js")
    version = script_version(meta)
    artifact = sorted(d.glob("*.user.js"))[0]
    urls = script_urls(d, artifact)
    meta = inject_urls(meta.rstrip("\n") + "\n", urls)
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
    meta_file = artifact.with_name(artifact.name[:-8] + ".meta.js")
    meta_text = meta.rstrip("\n") + "\n\n// Служебный файл проверки обновлений: только блок метаданных.\n" \
        "// Не редактировать — собирается tools/build_userscripts.py из meta.js;\n" \
        f"// полный скрипт — {artifact.name}.\n"
    return artifact, text, meta_file, meta_text, len(parts)


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
            art, text, _mf, _mt, n = build_script(d)
            print(f"{d.name}: {art.name} v{script_version(read_lf(d / 'meta.js'))} "
                  f"({n} частей, {len(text.splitlines())} строк)")
        return 0

    stale = []
    for d in dirs:
        art, text, meta_file, meta_text, n = build_script(d)
        version = script_version(read_lf(d / "meta.js"))
        if args.check:
            for path, want in ((art, text), (meta_file, meta_text)):
                current = read_lf(path) if path.exists() else ""
                if current == want:
                    continue
                logger.error("%s: %s расходится со сборкой:\n%s", d.name, path.name,
                             first_diff(current, want))
                stale.append(d.name)
            if d.name not in stale:
                logger.info("%s: %s и %s актуальны (%d частей, v%s)",
                            d.name, art.name, meta_file.name, n, version)
        else:
            atomic_write(str(art), text)
            atomic_write(str(meta_file), meta_text)
            logger.info("%s: собраны %s и %s (%d частей, v%s, %d строк)", d.name, art.name,
                        meta_file.name, n, version, len(text.splitlines()))
        if args.node_check:
            node_check(art)

    if stale:
        raise SystemExit(f"артефакты устарели: {', '.join(stale)} — "
                         "запусти python3 tools/build_userscripts.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())

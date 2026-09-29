#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vendor_assets.py — локальные библиотеки SPA: версии зафиксированы, интернета нет.

Правило проекта: SPA работает полностью офлайн. Ни одного `<script src="http…">`
в рантайме быть не должно — браузер пользователя может быть вообще без сети, а
CDN ещё и подменяет содержимое тихой подменой версии. Поэтому все сторонние
библиотеки лежат в `web/static/vendor/`, а их версии, происхождение и хэши
учтены в манифесте `vendor.lock.json`:

    web/static/vendor/<файл>        — сам локальный файл (раздаётся сервером)
    web/static/vendor/vendor.lock.json — манифест: кто это, зачем, откуда взят

Подкоманды:

    python3 tools/vendor_assets.py check    # гейт: файлы == манифест, SPA локальна
    python3 tools/vendor_assets.py list     # что вендорится и каких версий
    python3 tools/vendor_assets.py lock     # пересписать манифест по факту
    python3 tools/vendor_assets.py fetch --name marked   # обновить файл по URL

`check` — единственная команда, которая нужна пользователю: она ничего не
скачивает и не меняет. `fetch` — инструмент разработки (нужна сеть): он берёт
URL из манифеста, сверяет хэш с ожидаемым и обновляет запись.

Манифест — единственный источник истины о версиях: «обновили библиотеку» —
это diff `vendor.lock.json`, а не молчаливая замена байт в файле.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO / "web" / "static" / "vendor"
LOCK_NAME = "vendor.lock.json"
STATIC_DIR = REPO / "web" / "static"
SCHEMA = 1
# шапка манифеста (копируется в файл как есть — единый источник формулировки)
NOTE = ("Локальные библиотеки SPA: всё раздаётся с этого каталога, интерфейс "
        "работает без сети. Версию, происхождение и sha256 описывает "
        "tools/vendor_assets.py (check/list/lock/fetch).")

# Убрано из вендора навсегда: возвращать файл — регресс, а не «всё равно весит
# копейки». Ключ — имя файла, значение — почему библиотека больше не нужна.
RETIRED = {
    "alpine.min.js": "SPA на ванильном JS: Alpine удалён (B8), реактивность — своя",
}

# Что должно быть в записи манифеста (порядок колонок в JSON).
META_KEYS = ("name", "package", "version", "license", "homepage", "kind", "url")

_ASSET_URL_RE = re.compile(r'(?:src|href)\s*=\s*"([^"]+)"')


# ══════════════════════════════════════════════════════════════════════
# Манифест
# ══════════════════════════════════════════════════════════════════════
def sha256(path: Path) -> str:
    """Хэш файла (hex, 64) — версия ассета и контроль целостности."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def asset_files(vendor: Path = VENDOR_DIR) -> list[Path]:
    """Файлы вендора (без манифеста), в стабильном порядке."""
    if not vendor.is_dir():
        return []
    return sorted(p for p in vendor.iterdir()
                  if p.is_file() and p.name != LOCK_NAME)


def load_lock(vendor: Path = VENDOR_DIR) -> dict:
    """Манифест → dict; нет файла/битый JSON → {} (не падает)."""
    lock = vendor / LOCK_NAME
    if not lock.is_file():
        return {}
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def build_lock(vendor: Path = VENDOR_DIR) -> dict:
    """Манифест по фактическому содержимому каталога.

    Метаданные уже объявленных записей сохраняются (сопоставление по имени
    файла); для нового файла они пустые — их заполняет человек.
    """
    old = {a.get("file"): a for a in load_lock(vendor).get("assets", [])}
    assets = []
    for path in asset_files(vendor):
        prev = old.get(path.name, {})
        row: dict[str, object] = {"file": path.name}
        for key in META_KEYS:
            row[key] = prev.get(key, "")
        row["sha256"] = sha256(path)
        row["bytes"] = path.stat().st_size
        assets.append(row)
    return {"schema": SCHEMA, "note": load_lock(vendor).get("note", NOTE),
            "assets": assets}


def write_lock(lock: dict, vendor: Path = VENDOR_DIR) -> Path:
    """Каноническая запись манифеста (стабильный diff в git)."""
    target = vendor / LOCK_NAME
    target.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return target


# ══════════════════════════════════════════════════════════════════════
# Проверки
# ══════════════════════════════════════════════════════════════════════
def check_lock(vendor: Path = VENDOR_DIR) -> list[str]:
    """Файлы вендора против манифеста. Возвращает список проблем."""
    problems: list[str] = []
    lock = load_lock(vendor)
    if not lock:
        return [f"нет манифеста {vendor / LOCK_NAME} (или он битый)"]
    if lock.get("schema") != SCHEMA:
        problems.append(f"схема манифеста {lock.get('schema')!r}, ожидалось {SCHEMA}")
    declared = {a.get("file"): a for a in lock.get("assets", [])}
    on_disk = {p.name for p in asset_files(vendor)}

    for name in sorted(RETIRED):
        if name in on_disk:
            problems.append(f"вернулся удалённый ассет {name}: {RETIRED[name]}")
    for name in sorted(on_disk - set(declared)):
        problems.append(f"{name} есть в vendor/, но не объявлен в манифесте")
    for name in sorted(set(declared) - on_disk):
        problems.append(f"{name} объявлен в манифесте, но лежит вне vendor/")
    for name, row in sorted(declared.items()):
        if name in on_disk:
            path = vendor / name
            if sha256(path) != row.get("sha256"):
                problems.append(f"{name}: содержимое не совпадает с sha256 из манифеста")
            elif path.stat().st_size != row.get("bytes"):
                problems.append(f"{name}: размер {path.stat().st_size} ≠ {row.get('bytes')}")
        for key in META_KEYS:
            if key == "name" and not row.get("name"):
                problems.append(f"{name}: пустое поле name в манифесте")
    return problems


def check_offline(static: Path = STATIC_DIR) -> list[str]:
    """SPA не тянет ассеты извне: локальные пути или data:-URI."""
    problems: list[str] = []
    index = static / "index.html"
    if not index.is_file():
        return ["нет web/static/index.html"]
    for url in _ASSET_URL_RE.findall(index.read_text(encoding="utf-8")):
        if url.startswith(("http://", "https://", "//")):
            problems.append(f"index.html грузит ассет из сети: {url}")
        elif url.startswith("/") and not url[1:].split("?")[0].startswith("api"):
            target = static / url.lstrip("/").split("?")[0]
            if not target.exists():
                problems.append(f"index.html ссылается на отсутствующий файл: {url}")
        elif not url.startswith("data:"):
            problems.append(f"index.html: непонятный путь ассета: {url}")
    return problems


# ══════════════════════════════════════════════════════════════════════
# Команды
# ══════════════════════════════════════════════════════════════════════
def cmd_check(_args: argparse.Namespace) -> int:
    problems = check_lock() + check_offline()
    if problems:
        print("❌ Вендор ассетов расходится с манифестом:")
        for p in problems:
            print(f"  • {p}")
        return 1
    n = len(asset_files())
    print(f"✅ Вендор чист: {n} локальных библиотек, SPA работает без сети")
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    lock = load_lock()
    rows = lock.get("assets", [])
    if not rows:
        print("Манифест пуст (или отсутствует) — выполните: "
              "python3 tools/vendor_assets.py lock")
        return 1
    for row in rows:
        src = row.get("url") or row.get("kind") or "—"
        print(f"  {row.get('name', '?'):<14} {row.get('version') or '?':<10} "
              f"{row.get('bytes', 0):>8} Б  {row['file']}  [{src}]")
    print(f"\nИтого {len(rows)} файл(ов), "
          f"{sum(int(r.get('bytes', 0)) for r in rows) // 1024} КБ локально")
    return 0


def cmd_lock(_args: argparse.Namespace) -> int:
    lock = build_lock()
    target = write_lock(lock)
    print(f"✅ Манифест перезаписан: {target.relative_to(REPO)} "
          f"({len(lock['assets'])} записей)")
    for row in lock["assets"]:
        if not row.get("name"):
            print(f"  ⚠ {row['file']}: заполните name/package/version/license "
                  f"в манифесте")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """Скачать декларированный ассет и проверить хэш (только разработка)."""
    lock = load_lock()
    row = next((a for a in lock.get("assets", [])
                if a.get("name") == args.name or a.get("file") == args.name), None)
    if row is None:
        print(f"❌ {args.name} нет в манифесте — сначала внесите его туда")
        return 1
    if args.version:
        row["version"] = args.version
    url = row.get("url") or ""
    if not url:
        print(f"❌ {row['file']}: в манифесте нет url (собирается вручную) — "
              f"обновляйте файл руками")
        return 1
    if args.version:
        url = url.replace(row.get("version_old", ""), args.version) \
            if row.get("version_old") else url
    print(f"⬇ {url}")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
            data = resp.read()
    except Exception as exc:  # noqa: BLE001 — сеть вне доверия
        print(f"❌ Скачивание не удалось: {exc}")
        return 1
    target = VENDOR_DIR / row["file"]
    digest = hashlib.sha256(data).hexdigest()
    if row.get("sha256") and digest != row["sha256"] and not args.yes:
        print(f"⚠ Хэш отличается: было {row['sha256'][:16]}…, стало {digest[:16]}…")
        print("  Это смена версии? Перезапишите манифест (lock) и повторите с --yes")
        return 1
    target.write_bytes(data)
    print(f"✅ {target.relative_to(REPO)}: {len(data)} Б, sha256 {digest[:16]}…")
    fresh = build_lock()
    write_lock(fresh)
    print("  манифест обновлён")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="tools/vendor_assets.py",
        description="Локальные библиотеки SPA: манифест версий и офлайн-гейт.",
    )
    p.add_argument("command", nargs="?", default="check",
                   choices=("check", "list", "lock", "fetch"),
                   help="check (по умолчанию) — сверка и офлайн-гейт; "
                        "lock — перезаписать манифест; fetch — скачать по url")
    p.add_argument("--name", help="fetch: имя ассета из манифеста")
    p.add_argument("--version", help="fetch: версия в URL/манифесте")
    p.add_argument("--yes", action="store_true",
                   help="fetch: принимать смену хэша (осознанное обновление)")
    args = p.parse_args(argv)
    if args.command == "fetch" and not args.name:
        p.error("fetch требует --name")
    return {"check": cmd_check, "list": cmd_list, "lock": cmd_lock,
            "fetch": cmd_fetch}[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

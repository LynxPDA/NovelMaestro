#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run.py — запуск NovelMaestro (web-first).

Интерфейс только один — web (сервер + SPA, пакет web/);
терминальный пульт (cli/tui) удалён. run.py — тонкий лаунчер:

  python3 run.py                # web-сервер + открыть браузер
  python3 run.py --host 127.0.0.1 --port 8756 --auth
  python3 run.py --no-open      # без автозапуска браузера

Менеджмент проектов (создание, перенос, переименование, дублирование,
удаление) — в web-интерфейсе; общая логика — core/projects.py.
Конфиг — системный projects/.env (см. templates/.env.example).
stdlib-only.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core import projects as prj          # noqa: E402

PROJECTS = REPO / "projects"


def _force_utf8_io() -> None:
    """Стримы в UTF-8 (errors=replace): лаунчер не падает на битых байтах
    ввода/вывода (обрезанные UTF-8 последовательности, чужая кодировка)."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(  # type: ignore[attr-defined]  # рантайм-метод TextIOWrapper
                encoding="utf-8", errors="replace")
        except Exception:  # noqa: S110 — UTF-8 уже настроен или стрим закрыт
            pass


def bootstrap_projects() -> None:
    """При запуске: каркас projects/ (разделы) и системный
    projects/.env из шаблона."""
    created = prj.ensure_projects_root(PROJECTS)
    if created:
        print(f"  ✔ Созданы разделы: {', '.join(created)}")
    env_dst = PROJECTS / ".env"
    env_tpl = REPO / "templates" / ".env.example"
    if not env_dst.exists() and env_tpl.is_file():
        print("  ℹ Системный .env не найден — копирую шаблон "
              "templates/.env.example (единый конфиг сервера и LLM).")
        shutil.copy2(env_tpl, env_dst)
        print(f"  ✔ Скопировано: {env_dst.name}")


def run_web_backend(args: argparse.Namespace) -> None:
    """Web-интерфейс: python3 web/main.py с пробросом настроек."""
    cmd = [sys.executable, str(REPO / "web" / "main.py")]
    for flag, value in (
        ("--host", args.host), ("--port", args.port), ("--token", args.token),
        ("--max-upload-mb", args.max_upload_mb),
        ("--jobs-limit", args.jobs_limit),
    ):
        if value is not None:
            cmd.append(flag)
            cmd.append(str(value))
    if args.auth:
        cmd.append("--auth")
    if not args.no_open:
        cmd.append("--open")
    subprocess.run(cmd, cwd=str(REPO))


def main() -> None:
    _force_utf8_io()
    ap = argparse.ArgumentParser(description="Запуск NovelMaestro (web)")
    ap.add_argument("--host", help="Адрес прослушивания (по умолчанию 0.0.0.0)")
    ap.add_argument("--port", type=int, help="Порт (по умолчанию 8756)")
    ap.add_argument("--auth", action="store_true",
                    help="Требовать токен доступа (по умолчанию выключено)")
    ap.add_argument("--token", help="Токен доступа (при --auth)")
    ap.add_argument("--max-upload-mb", type=int,
                    help="Лимит загрузки файлов, МБ (по умолчанию 512)")
    ap.add_argument("--jobs-limit", type=int,
                    help="Максимум параллельных задач (по умолчанию 2)")
    ap.add_argument("--no-open", action="store_true",
                    help="Не открывать браузер автоматически")
    args = ap.parse_args()

    bootstrap_projects()
    run_web_backend(args)


if __name__ == "__main__":
    main()

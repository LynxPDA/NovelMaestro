#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — CLI-точка входа web-бэкэнда.

Запуск:  python3 web/main.py [--host 0.0.0.0] [--port 8756]

Режим по умолчанию — локальная сеть: слушаем 0.0.0.0, аутентификация
ВЫКЛЮЧЕНА (доверенная LAN, работа по SSH). Включить токен: --auth
(или WEB_AUTH=1); тогда токен: --token > WEB_TOKEN > projects/.web_secret.
Конфигурация окружением: WEB_HOST, WEB_PORT, WEB_AUTH, WEB_TOKEN,
WEB_MAX_UPLOAD_MB, WEB_JOBS_LIMIT, WEB_PROJECTS_DIR.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import secrets
import socket
import sys
import webbrowser
from pathlib import Path


def _find_repo_root() -> Path:
    """Корень репо: маркер core/common.py, подъём вверх от этого файла."""
    p = Path(os.path.dirname(os.path.abspath(__file__)))
    for _ in range(6):
        if (p / "core" / "common.py").is_file():
            return p
        if p.parent == p:
            break
        p = p.parent
    raise RuntimeError("Корень репозитория не найден")


def _bootstrap_core() -> None:
    """Добавляет корень репо в sys.path (обязательно перед импортом core)."""
    root = _find_repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_bootstrap_core()
from core.projects import ensure_projects_root  # noqa: E402

from web import api, auth, server  # noqa: E402
from web.jobs import JobManager  # noqa: E402

log = logging.getLogger("web")

# Глобальный менеджер задач (jobs) — один на процесс.
JOB_MANAGER = JobManager(Path(__file__).resolve().parent)


def _env_cfg() -> dict:
    """Конфиг из системного корневого .env, os.environ
    приоритетнее."""
    cfg: dict = {}
    try:
        from core.common import find_env_file, parse_dotenv
        cfg.update(parse_dotenv(find_env_file()))
    except Exception as exc:  # noqa: BLE001 — .env необязателен
        log.debug("Системный .env не читается: %s", exc)
    cfg.update({k: v for k, v in os.environ.items() if v})
    return cfg


def _env_int(cfg: dict, name: str, default: int) -> int:
    """Число из конфига (.env/окружение) с фолбэком на default."""
    raw = cfg.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("%s=%r не число, берём %d", name, raw, default)
        return default


def _env_bool(cfg: dict, name: str, default: bool) -> bool:
    """Булево из конфига (1/true/yes) с фолбэком."""
    raw = str(cfg.get(name, "")).strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    cfg = _env_cfg()
    p = argparse.ArgumentParser(
        prog="web/main.py",
        description="Web-интерфейс NovelMaestro (сервер + SPA).",
    )
    p.add_argument("--host", default=cfg.get("WEB_HOST", "0.0.0.0"),
                   help="Адрес прослушивания (по умолчанию 0.0.0.0 — вся локальная сеть)")
    p.add_argument("--port", type=int, default=_env_int(cfg, "WEB_PORT", 8756),
                   help="Порт (по умолчанию 8756)")
    p.add_argument("--auth", action="store_true",
                   default=_env_bool(cfg, "WEB_AUTH", False),
                   help="Включить аутентификацию по токену (по умолчанию выключена)")
    p.add_argument("--no-auth", action="store_true",
                   help="Устарело: аутентификация и так выключена по умолчанию")
    p.add_argument("--token", default=cfg.get("WEB_TOKEN", ""),
                   help="Токен доступа (при --auth; по умолчанию — .web_secret в projects/)")
    p.add_argument("--open", action="store_true",
                   help="Открыть браузер после старта")
    p.add_argument("--max-upload-mb", type=int,
                   default=_env_int(cfg, "WEB_MAX_UPLOAD_MB", 512),
                   help="Лимит загрузки файлов, МБ (по умолчанию 512)")
    p.add_argument("--jobs-limit", type=int,
                   default=_env_int(cfg, "WEB_JOBS_LIMIT", 2),
                   help="Максимум параллельных задач (по умолчанию 2)")
    p.add_argument("--projects-dir",
                   default=cfg.get("WEB_PROJECTS_DIR", ""),
                   help="Папка проектов (по умолчанию <репо>/projects; "
                        "WEB_PROJECTS_DIR)")
    return p.parse_args(argv)


def load_or_create_token(projects_root: Path, explicit: str) -> str:
    """Токен: --token > projects/.web_secret (генерация, chmod 600)."""
    if explicit:
        return explicit
    secret_file = projects_root / ".web_secret"
    try:
        existing = secret_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError as exc:
        log.debug("Не удалось прочитать файл токена: %s", exc)
    token = secrets.token_urlsafe(32)
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(token + "\n", encoding="utf-8")
    try:
        secret_file.chmod(0o600)
    except OSError as exc:
        log.debug("Не удалось установить права на файл токена: %s", exc)
    return token


def _setup_logging() -> None:
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            # B11: ротация web.log по размеру (>5 МБ → .1/.2), иначе
            # файл растёт бесконечно
            logging.handlers.RotatingFileHandler(
                logs_dir / "web.log", maxBytes=5 * 1024 * 1024,
                backupCount=2, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _lan_ip() -> str:
    """Основной LAN-адрес машины (для подсказки в баннере; fallback 127.0.0.1)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))  # UDP: пакет не отправляется
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError as exc:
        log.debug("LAN-адрес через UDP-пробу не найден: %s", exc)
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError as exc:
        log.debug("LAN-адрес через gethostbyname не найден: %s", exc)
        return "127.0.0.1"


def _print_banner(url: str, lan_url: str | None, token: str,
                  use_auth: bool) -> None:
    line = "═" * 47
    print(line)
    print("  NovelMaestro · web-бэкэнд")
    print(f"  URL:   {url}")
    if lan_url:
        print(f"  Локальная сеть: {lan_url}")
    if use_auth:
        print(f"  Токен: {token}")
        print("  (сохранён в projects/.web_secret, chmod 600)")
    else:
        print("  Аутентификация: ВЫКЛЮЧЕНА (доверенная сеть;")
        print("  включить: --auth или WEB_AUTH=1)")
        print("  ⚠ ВНИМАНИЕ: .env и API-ключи видны")
        print("  без пароля любому в сети — только доверенная LAN!")
    print(line)
    print("Остановка: Ctrl+C")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    use_auth = args.auth and not args.no_auth
    _setup_logging()
    projects_root = _find_repo_root() / "projects"
    if args.projects_dir:
        projects_root = Path(args.projects_dir).expanduser().resolve()
    ensure_projects_root(projects_root)
    api._ensure_stats_cache(projects_root)  # дисковый кеш stats → память
    if use_auth:
        token = load_or_create_token(projects_root, args.token)
    else:
        token = args.token  # без --auth файл .web_secret не создаём
    auth_obj = auth.Auth(token, no_auth=not use_auth)
    router = server.Router()
    api.register(router, host=args.host)
    repo_root = _find_repo_root()
    srv = server.make_server(args.host, args.port, auth_obj, router,
                             repo_root=repo_root,
                             projects_root=repo_root / "projects")
    srv.max_upload_mb = args.max_upload_mb
    srv.jobs_limit = args.jobs_limit
    # JobManager живёт на сервере (для _job_manager(ctx)); процессы —
    # в отдельной сессии (start_new_session) и переживают рестарт сервера,
    # поэтому при завершении останавливаем все активные запуски явно.
    srv.job_manager = JOB_MANAGER
    port = srv.server_address[1]
    url = f"http://{args.host}:{port}"
    lan_url = f"http://{_lan_ip()}:{port}" if args.host == "0.0.0.0" else None
    _print_banner(url, lan_url, token, use_auth)
    if args.open:
        # 0.0.0.0 в браузер не откроешь — локально открываем 127.0.0.1
        open_url = f"http://127.0.0.1:{port}" if args.host == "0.0.0.0" else url
        try:
            webbrowser.open(open_url)
        except Exception:  # noqa: BLE001 — headless/SSH: тихо игнорируем
            log.debug("Не удалось открыть браузер", exc_info=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        # остановить активные запуски (иначе процессы-сироты продолжат
        # работать после закрытия сервера — управление потеряно)
        try:
            JOB_MANAGER.shutdown()
        except Exception as exc:  # noqa: BLE001 — сервер уже умирает
            log.warning("Ошибка остановки запусков при завершении: %s", exc)
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — CLI-точка входа web-бэкэнда.

Запуск:  python3 web/main.py [--host 127.0.0.1] [--port 8756]

По умолчанию слушаем ТОЛЬКО 127.0.0.1 (локальный доступ, безопасно без
токена); для доступа с других машин LAN — --host 0.0.0.0 (тогда
обязательно включите токен: --auth или WEB_AUTH=1; токен:
--token > WEB_TOKEN > projects/.web_secret). Если порт занят — сервер
автоматически берёт следующий свободный (port+1 … port+100).
Конфигурация окружением: WEB_HOST, WEB_PORT, WEB_AUTH, WEB_TOKEN,
WEB_MAX_UPLOAD_MB, WEB_JOBS_LIMIT, WEB_PROJECTS_DIR (дефолты — реестр
core/settings.py). Приоритет: флаг командной строки > переменные окружения
процесса > общий .env > дефолт реестра.
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
from core import settings as core_settings  # noqa: E402
from core.deps import format_status as deps_status  # noqa: E402
from core.projects import ensure_projects_root  # noqa: E402

from web import api, auth, server  # noqa: E402
from web.jobs import JobManager  # noqa: E402

log = logging.getLogger("web")

# Глобальный менеджер задач (jobs) — один на процесс.
JOB_MANAGER = JobManager(Path(__file__).resolve().parent)


def _cfg() -> dict:
    """Конфиг запуска сервера: реестр → общий .env → os.environ.

    Дефолты WEB_* живут только в реестре (`core/settings.py`): лаунчер своего
    списка не держит. Флаг командной строки перекрывает всё — именно он в
    Docker выставляет `--host 0.0.0.0`, и правка в браузере его не перешибёт."""
    return core_settings.web_values()


def _int(cfg: dict, name: str) -> int:
    """Число из конфига; мусор в конфиге — warning и дефолт реестра."""
    raw = str(cfg.get(name, ""))
    try:
        return int(raw)
    except ValueError:
        default = core_settings.BY_KEY[name.upper()].default
        log.warning("%s=%r не число, берём дефолт реестра %r", name, raw, default)
        return int(default)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    cfg = _cfg()
    p = argparse.ArgumentParser(
        prog="web/main.py",
        description="Web-интерфейс NovelMaestro (сервер + SPA).",
        epilog="Приоритет значений: флаг командной строки > переменные окружения "
               "процесса > общий .env > встроенный дефолт реестра.",
    )
    p.add_argument("--host", default=cfg["web_host"],
                   help="Адрес прослушивания (по умолчанию 127.0.0.1 — только этот компьютер; "
                        "0.0.0.0 — вся локальная сеть, тогда включите --auth)")
    p.add_argument("--port", type=int, default=_int(cfg, "web_port"),
                   help="Порт (по умолчанию 8756)")
    p.add_argument("--auth", action="store_true", default=bool(cfg["web_auth"]),
                   help="Включить аутентификацию по токену (по умолчанию выключена)")
    p.add_argument("--no-auth", action="store_true",
                   help="Устарело: аутентификация и так выключена по умолчанию")
    p.add_argument("--token", default=cfg["web_token"],
                   help="Токен доступа (при --auth; по умолчанию — .web_secret в projects/)")
    p.add_argument("--open", action="store_true",
                   help="Открыть браузер после старта")
    p.add_argument("--max-upload-mb", type=int,
                   default=_int(cfg, "web_max_upload_mb"),
                   help="Лимит загрузки файлов, МБ (по умолчанию 512)")
    p.add_argument("--jobs-limit", type=int,
                   default=_int(cfg, "web_jobs_limit"),
                   help="Максимум параллельных задач (по умолчанию 2)")
    p.add_argument("--projects-dir", default=cfg["web_projects_dir"],
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
            # Ротация web.log по размеру (>5 МБ → .1/.2), иначе
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


def _bind_server(host: str, port: int, auth_obj: auth.Auth,
                 router: server.Router, repo_root: Path,
                 projects_root: Path,
                 max_port_attempts: int = 100) -> tuple[server.WebServer, int]:
    """Создаёт сервер; если порт занят — берёт следующий свободный
    (port+1 … port+max_port_attempts). Возвращает (srv, фактический порт)."""
    last_exc: OSError | None = None
    for candidate in range(port, port + max_port_attempts + 1):
        try:
            srv = server.make_server(host, candidate, auth_obj, router,
                                     repo_root=repo_root,
                                     projects_root=projects_root)
        except OSError as exc:
            last_exc = exc
            continue
        if candidate != port:
            log.warning("Порт %d занят — использую %d", port, candidate)
        return srv, candidate
    if last_exc is not None:
        raise last_exc
    raise OSError(f"Не удалось найти свободный порт в диапазоне "
                  f"{port}–{port + max_port_attempts}")


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
    elif lan_url is None:
        print("  Аутентификация: ВЫКЛЮЧЕНА (доступ только с этого")
        print("  компьютера — токен не нужен)")
    else:
        print("  Аутентификация: ВЫКЛЮЧЕНА — сервер слушает 0.0.0.0!")
        print("  ⚠ ВНИМАНИЕ: .env и API-ключи видны без пароля")
        print("  любому в сети. Включите: --auth или WEB_AUTH=1")
    print(line)
    print("Остановка: Ctrl+C")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    use_auth = args.auth and not args.no_auth
    _setup_logging()
    # активный стек внешних библиотек: деградация видна сразу, а не после
    # «почему NER такой медленный» (реестр — core/deps.py)
    log.info("Стек зависимостей: %s", deps_status())
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
    srv, port = _bind_server(args.host, args.port, auth_obj, router,
                             repo_root, projects_root)
    srv.max_upload_mb = args.max_upload_mb
    srv.jobs_limit = args.jobs_limit
    # JobManager живёт на сервере (для _job_manager(ctx)); процессы —
    # в отдельной сессии (start_new_session) и переживают рестарт сервера,
    # поэтому при завершении останавливаем все активные запуски явно.
    srv.job_manager = JOB_MANAGER
    if port != args.port:
        print(f"  ⚠ Порт {args.port} занят — сервер работает на порту {port}")
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

#!/usr/bin/env bash
# dev.sh — разработка NovelMaestro в изолированном venv (Linux/macOS/WSL).
#
# Зачем отдельный скрипт:
#   1. свежий системный Python обычно PEP 668 («externally-managed»): pip в него
#      не ставит пакеты, разработка без venv упирается в ошибку окружения;
#   2. все внешние зависимости и их версии живут в одном месте
#      (requirements.txt + requirements-dev.txt) и не трогают систему;
#   3. каталог venv (.venv) уже в .gitignore — в git он не появляется.
#
# При этом команды проекта в коде и документации остаются унифицированными
# (`python3 ...`): скрипт АКТИВИРУЕТ окружение, и внутри него `python3` — это
# интерпретатор venv. Никаких `.venv/bin/python3` в командах и доках.
#
# Windows (PowerShell), вместо этого скрипта:
#   py -m venv .venv ; . .venv\Scripts\Activate.ps1
#   python -m pip install -r requirements-dev.txt
#   python run.py            # или: python -m pytest tests/ -q
#
# .venv уже в .gitignore (venv/, env/, .venv/) — добавлять нечего.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${NOVELMAESTRO_VENV:-$REPO/.venv}"
PY="${PYTHON:-python3}"

log() { printf '  %s\n' "$*"; }

activate() {
    if [ ! -f "$VENV/bin/activate" ]; then
        log "создаю venv: $VENV"
        if ! "$PY" -m venv "$VENV" 2>/dev/null; then
            log "не вышло: в Debian/Ubuntu нужен пакет python3-venv"
            log "  sudo apt install python3-venv"
            exit 1
        fi
    fi
    # shellcheck disable=SC1091  # venv сам генерирует activate
    . "$VENV/bin/activate"
    hash -r
}

cmd_setup() {
    activate
    log "ставлю зависимости (requirements-dev.txt)"
    python3 -m pip install --quiet --upgrade pip
    python3 -m pip install --quiet -r "$REPO/requirements-dev.txt"
    python3 -m core.deps
}

cmd_test() {
    activate
    # без аргументов — весь каталог параллельно (pytest-xdist); с аргументами — ровно то,
    # что попросили (например: ./dev.sh test -n 0 tests/test_ner.py)
    if [ $# -eq 0 ]; then set -- -n auto tests/; fi
    exec python3 -m pytest "$@" -q
}

cmd_run() {
    activate
    exec python3 "$REPO/run.py" "$@"
}

cmd_deps() {
    activate
    exec python3 -m core.deps
}

# UI-пробег SPA headless-браузером: venv активируется здесь, чтобы node-проб
# поднял сервер тем же python3, что и всё остальное (данные — временные).
cmd_probe() {
    activate
    command -v node >/dev/null 2>&1 || { log "нужен node (playwright-core)"; exit 3; }
    if [ ! -d "$HOME/.cache/ms-playwright" ]; then
        log "нет браузеров Playwright: npx playwright-core install chromium"
        exit 3
    fi
    exec node "$REPO/tools/ui_probe.mjs" "$@"
}

# юнит-тесты чистых функций SPA (ui-core) и синтаксис всех view-файлов
cmd_spa() {
    command -v node >/dev/null 2>&1 || { log "нужен node"; exit 3; }
    for f in "$REPO"/web/static/*.js; do node --check "$f" || exit 1; done
    log "node --check: ОК"
    exec node --test tests/spa/*.test.mjs 2>&1 | tail -5
}

cmd_shell() {
    activate
    log "venv активен: $VIRTUAL_ENV (exit — выйти)"
    exec bash
}

cmd_clean() {
    if [ -d "$VENV" ]; then
        rm -rf "$VENV"
        log "venv удалён: $VENV"
    else
        log "venv не создан"
    fi
}

usage() {
    cat <<'EOF'
dev.sh — разработка NovelMaestro в venv (Linux/macOS/WSL)

  ./dev.sh setup        создать .venv и поставить зависимости
  ./dev.sh deps         активный стек зависимостей (что фолбэк, что основа)
  ./dev.sh test [args]  pytest (например: ./dev.sh test tests/test_ner.py)
  ./dev.sh run [args]   web-сервер (args пробрасываются в run.py)
  ./dev.sh probe [args] обход SPA headless-браузером (свой сервер, временные данные)
  ./dev.sh spa          node --check по static/*.js + node --test tests/spa/
  ./dev.sh shell        bash с активированным venv
  ./dev.sh clean        удалить .venv

Переменные окружения:
  NOVELMAESTRO_VENV     путь к venv (по умолчанию <репо>/.venv)
  PYTHON                интерпретатор для создания venv (по умолчанию python3)
EOF
}

command="${1:-help}"
case "$command" in
    setup) shift || true; cmd_setup "$@" ;;
    deps) shift || true; cmd_deps "$@" ;;
    test) shift || true; cmd_test "$@" ;;
    run) shift || true; cmd_run "$@" ;;
    probe) shift || true; cmd_probe "$@" ;;
    spa) shift || true; cmd_spa "$@" ;;
    shell) shift || true; cmd_shell "$@" ;;
    clean) shift || true; cmd_clean "$@" ;;
    help|-h|--help) usage ;;
    *) log "неизвестная команда: $command"; usage; exit 2 ;;
esac

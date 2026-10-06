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
#   python run.py            # или: python -m pytest tests/ -q -n auto
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

# Аргументы pytest: -n auto по умолчанию (число воркеров считает хук
# pytest_xdist_auto_num_workers в tests/conftest.py — бюджет памяти 5 ГБ); если
# среди аргументов нет ни -n, ни целей — гоним tests/ целиком.
normalize_test_args() {
    local has_n=0 has_target=0 a next_is_nval=0
    for a in "$@"; do
        if [ "$next_is_nval" -eq 1 ]; then next_is_nval=0; continue; fi
        case "$a" in
            -n|-n[0-9]*|-n[0-9]*) has_n=1; [ "$a" = "-n" ] && next_is_nval=1 ;;
            -*) ;;
            *) has_target=1 ;;
        esac
    done
    [ "$has_n" -eq 0 ] && set -- -n auto "$@"
    [ "$has_target" -eq 0 ] && set -- "$@" tests/
    TEST_ARGS=("$@")
}

cmd_test() {
    activate
    normalize_test_args "$@"
    exec python3 -m pytest "${TEST_ARGS[@]}" -q
}

# Покрытие: тот же прогон, но вместе с дочерними процессами. Движки стадий
# живут отдельными процессами: без sitecustomize их строки в отчёт не попадали
# вовсе. Конфиг замера генерируется здесь же и держит АБСОЛЮТНЫЕ пути: source из
# конфига в репо резолвится от cwd процесса, а у движка стадии cwd — папка книги,
# поэтому его данные выходили пустыми (web/pipeline.py давал 32% вместо 61%).
# Данные замера — в служебной папке .tmp/ (вместе с кэшем pytest, см. pytest.ini):
# корень репо остаётся чистым.
cmd_cov() {
    activate
    if ! python3 -c "import coverage" >/dev/null 2>&1; then
        log "нужен coverage: ./dev.sh setup"
        exit 3
    fi
    normalize_test_args "$@"
    local site status=0 cache="$REPO/.tmp"
    mkdir -p "$cache"
    site="$(mktemp -d "${TMPDIR:-/tmp}/nm-cov-site.XXXXXX")"
    printf '%s\n' \
        '# Автозапуск сбора покрытия в дочерних процессах (dev.sh cmd_cov).' \
        'try:' \
        '    import coverage' \
        'except ImportError:' \
        '    pass' \
        'else:' \
        '    coverage.process_startup()' > "$site/sitecustomize.py"
    cat > "$site/coveragerc" <<EOF
[run]
branch = True
parallel = True
source =
    $REPO/core
    $REPO/cli
    $REPO/web
    $REPO/run.py
EOF
    local -a rc=("--rcfile=$site/coveragerc" "--data-file=$cache/coverage")
    rm -f "$cache/coverage" "$cache"/coverage.*
    export COVERAGE_PROCESS_START="$site/coveragerc"
    # COVERAGE_FILE тоже абсолютный: иначе дочерний процесс пишет свой файл
    # данных в свой cwd (папку книги) и он до combine не доходит.
    export COVERAGE_FILE="$cache/coverage"
    export PYTHONPATH="$site${PYTHONPATH:+:$PYTHONPATH}"
    log "собираю покрытие: pytest ${TEST_ARGS[*]}"
    python3 -m coverage run --parallel-mode "${rc[@]}" -m pytest "${TEST_ARGS[@]}" -q || status=$?
    python3 -m coverage combine "${rc[@]}" >/dev/null
    python3 -m coverage report "${rc[@]}" | tail -n 22
    rm -rf "$site"
    log "по строкам: python3 -m coverage report --data-file=$REPO/.tmp/coverage --show-missing"
    exit "$status"
}

cmd_run() {
    activate
    exec python3 "$REPO/run.py" "$@"
}

# ── dev-сервер в фоне: PID-файл вместо поиска по имени процесса ────────
# Боевой контейнер поднимает ТОТ ЖЕ web/main.py (run.py запускает его
# подпроцессом), и процесс контейнера виден из общего PID namespace хоста:
# `pkill -f web/main.py` сигналит и боевому серверу, а `restart:
# unless-stopped` поднимает его обратно — в логе это выглядит как «сам
# перезапустился». Поэтому dev-сервер останавливается ТОЛЬКО по PID из
# файла, и только если процесс не из контейнера (у контейнера в cgroup —
# docker/containerd/kubepods).
DEV_PID_FILE="$REPO/.tmp/dev.pid"
DEV_LOG="$REPO/logs/dev_server.log"
#: порт и песочница dev-сервера: боевой сервер на них не слушает и его
#: данные не видит (книги для экспериментов — только во временной папке)
DEV_PORT="${DEV_PORT:-8799}"
DEV_PROJECTS="${DEV_PROJECTS:-/tmp/nm_dbg}"


dev_pid_alive() {
    local pid="${1:-}"
    [ -n "$pid" ] || return 1
    [ -d "/proc/$pid" ] || kill -0 "$pid" 2>/dev/null || return 1
    # zombie (PID 1 контейнера детей не собирает) — процесс мёртв
    local state
    state="$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null)"
    [ "$state" != "Z" ]
}


dev_is_container() {
    grep -qE 'docker|containerd|kubepods|libpod' \
        "/proc/${1:-}/cgroup" 2>/dev/null
}

# «Наш» процесс — это живый процесс ЭТОГО репозитория вне контейнера: у
# dev-сервера cwd совпадает с репо, у контейнера — /app (и в cgroup docker)
dev_pid_is_mine() {
    local pid="${1:-}" cwd
    dev_pid_alive "$pid" || return 1
    dev_is_container "$pid" && return 1
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null)"
    [ -z "$cwd" ] || [ "$cwd" = "$REPO" ]
}


cmd_start() {
    activate
    mkdir -p "$REPO/.tmp" "$REPO/logs" "$DEV_PROJECTS" || exit 3
    if [ -f "$DEV_PID_FILE" ]; then
        local old
        old="$(cat "$DEV_PID_FILE" 2>/dev/null)"
        if dev_pid_is_mine "$old"; then
            log "dev-сервер уже работает: PID $old (http://127.0.0.1:$DEV_PORT)"
            return 0
        fi
        if dev_pid_alive "$old"; then
            # чужой процесс в нашем pid-файле (например боёвой контейнер): его
            # не останавливаем и не считаем своим, файл заменяем
            log "в $DEV_PID_FILE записан чужой PID $old — не dev-сервер, заменяю"
        fi
        rm -f "$DEV_PID_FILE"
    fi
    # сервер запускается напрямую: PID-файл должен хранить именно его, а не
    # родителя (run.py ждёт подпроцесс и умрёт вместе с ним)
    WEB_ENV_FILE="${WEB_ENV_FILE:-$DEV_PROJECTS/.env}" \
        setsid nohup python3 "$REPO/web/main.py" \
        --projects-dir "$DEV_PROJECTS" --host 127.0.0.1 --port "$DEV_PORT" \
        "$@" >"$DEV_LOG" 2>&1 < /dev/null &
    echo $! > "$DEV_PID_FILE"
    log "dev-сервер: PID $(cat "$DEV_PID_FILE"), http://127.0.0.1:$DEV_PORT"
    log "данные: $DEV_PROJECTS · лог: $DEV_LOG"
    log "остановка: ./dev.sh stop"
}


cmd_stop() {
    if [ ! -f "$DEV_PID_FILE" ]; then
        log "pid-файла нет: $DEV_PID_FILE"
        log "dev-сервер запускали не отсюда — процесс ищем по PID вручную:"
        log "  pgrep -af 'web/mai[n].py'   # и проверять /proc/<pid>/cgroup"
        return 1
    fi
    local pid
    pid="$(cat "$DEV_PID_FILE" 2>/dev/null)"
    if ! dev_pid_alive "$pid"; then
        rm -f "$DEV_PID_FILE"
        log "dev-сервер уже остановлен (PID $pid)"
        return 0
    fi
    if ! dev_pid_is_mine "$pid"; then
        log "PID $pid из файла — не dev-сервер этого репозитория — не трогаю"
        return 1
    fi
    kill "$pid" && rm -f "$DEV_PID_FILE"
    log "dev-сервер остановлен: PID $pid"
}


cmd_status() {
    if [ -f "$DEV_PID_FILE" ] && dev_pid_is_mine "$(cat "$DEV_PID_FILE" 2>/dev/null)"; then
        log "dev-сервер работает: PID $(cat "$DEV_PID_FILE") · http://127.0.0.1:$DEV_PORT"
    else
        log "dev-сервер не запущен"
    fi
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
    # служебная папка разработки: кэш pytest и данные покрытия (cmd_cov)
    if [ -d "$REPO/.tmp" ]; then
        rm -rf "$REPO/.tmp"
        log "служебная папка удалена: $REPO/.tmp"
    fi
}

usage() {
    cat <<'EOF'
dev.sh — разработка NovelMaestro в venv (Linux/macOS/WSL)

  ./dev.sh setup        создать .venv и поставить зависимости
  ./dev.sh deps         активный стек зависимостей (что фолбэк, что основа)
  ./dev.sh test [args]  pytest -n auto tests/ (параллельно, pytest-xdist); с аргументами —
                        ровно они (например: ./dev.sh test -n 0 tests/test_ner.py)
  ./dev.sh cov [args]   то же, но под coverage: отчёт покрытия вместе с движками
                        стадий (подпроцессы); аргументы — как у test
                        (данные замера — в .tmp/coverage, кэш pytest — в .tmp/pytest)
  ./dev.sh run [args]   web-сервер (args пробрасываются в run.py)
  ./dev.sh start [args] dev-сервер в фоне: PID в .tmp/dev.pid, лог в logs/dev_server.log,
                        данные — временная папка (/tmp/nm_dbg, DEV_PROJECTS), порт 8799 (DEV_PORT);
                        останавливать его ТОЛЬКО ./dev.sh stop — pkill по имени скрипта
                        сигналит и боевому контейнеру (тот же web/main.py)
  ./dev.sh stop         остановить dev-сервер из .tmp/dev.pid (чужой PID и контейнер не трогает)
  ./dev.sh status       запущен ли dev-сервер
  ./dev.sh probe [args] обход SPA headless-браузером (свой сервер, временные данные);
                        --shot — скриншоты в logs/ui_probe/ (правка UI без прогона не закрыта)
  ./dev.sh spa          node --check по static/*.js + node --test tests/spa/
  ./dev.sh shell        bash с активированным venv
  ./dev.sh clean        удалить .venv и служебную папку .tmp/ (кэш тестов, покрытие)

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
    cov) shift || true; cmd_cov "$@" ;;
    run) shift || true; cmd_run "$@" ;;
    start) shift || true; cmd_start "$@" ;;
    stop) shift || true; cmd_stop "$@" ;;
    status) shift || true; cmd_status "$@" ;;
    probe) shift || true; cmd_probe "$@" ;;
    spa) shift || true; cmd_spa "$@" ;;
    shell) shift || true; cmd_shell "$@" ;;
    clean) shift || true; cmd_clean "$@" ;;
    help|-h|--help) usage ;;
    *) log "неизвестная команда: $command"; usage; exit 2 ;;
esac

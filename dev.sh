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
    probe) shift || true; cmd_probe "$@" ;;
    spa) shift || true; cmd_spa "$@" ;;
    shell) shift || true; cmd_shell "$@" ;;
    clean) shift || true; cmd_clean "$@" ;;
    help|-h|--help) usage ;;
    *) log "неизвестная команда: $command"; usage; exit 2 ;;
esac

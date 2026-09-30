#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Архитектурные стражи (AGENTS.md §3–§4, web-first):
статические проверки по исходникам. Ловят будущие правки, ломающие
слоистость, единую LLM-гигиену (один транспорт — core/transport.py),
политику внешних библиотек и web-канон (без cli/tui)."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPTS = sorted((ROOT / "cli").glob("*.py"))
WEB = sorted((ROOT / "web").glob("*.py"))
CORE = ROOT / "core" / "common.py"


def test_dirs_not_empty():
    assert SCRIPTS and WEB and CORE.is_file()


# ══════════════════════════════════════════════════════════════════════
# cli/tui удалены: никаких следов терминального пульта
# ══════════════════════════════════════════════════════════════════════
def test_cli_backend_and_tui_removed():
    assert not (ROOT / "backends").exists(), "backends/ удалён "
    assert not (ROOT / "core" / "ui.py").exists(), "core/ui.py удалён"
    assert not (ROOT / "core" / "tui.py").exists(), "core/tui.py удалён"


def test_web_layout():
    """web/ = сервер + статика + README (канон web-first)."""
    assert (ROOT / "web" / "main.py").is_file()
    assert (ROOT / "web" / "README.md").is_file()
    assert (ROOT / "web" / "static" / "app.js").is_file()
    assert (ROOT / "web" / "static" / "index.html").is_file()
    # вспомогательные утилиты вне конвейера — в tools/ (userscript Rulate)
    assert (ROOT / "tools").is_dir()
    assert not (ROOT / "cli" / "Other_tools").exists()


API_DOMAINS = ("common", "projects", "files", "glossary", "env", "assets",
               "stage", "templates")


def test_web_api_is_facade():
    """api.py — фасад: хендлеры живут в доменных api_<домен>.py."""
    api = (ROOT / "web" / "api.py").read_text(encoding="utf-8")
    assert len(api.splitlines()) < 150, \
        "api.py распух: хендлеры — в доменные модули, здесь только register()"
    assert "def _session" not in api, "хендлер вернулся в фасад"
    for d in API_DOMAINS:
        assert (ROOT / "web" / f"api_{d}.py").is_file(), f"нет web/api_{d}.py"
    assert "_register_hub(router)" in api and "_register_jobs(router)" in api


def test_web_api_shared_state_defined_once():
    """Общий mutable-контекст web-слоя определён ровно в api_common."""
    for mod in API_DOMAINS:
        if mod == "common":
            continue
        src = (ROOT / "web" / f"api_{mod}.py").read_text(encoding="utf-8")
        for shared in ("_STATS_CACHE", "_STATS_LOCK", "_CACHE_LOADED",
                       "_OPTIONS_CACHE", "_PREVIEW_STAGES", "log = logging"):
            assert not re.search(rf"^{re.escape(shared)}", src, re.M), \
                f"api_{mod}.py: переопределён общий контекст {shared}"


# ══════════════════════════════════════════════════════════════════════
# cli/ = чистый CLI: никакого интерактива
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_has_no_input_calls(script):
    src = script.read_text(encoding="utf-8")
    assert not re.search(r"(?<![\w.])input\s*\(", src), \
        f"{script.name}: input() запрещён в cli/ (только argparse)"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_does_not_import_core_ui(script):
    src = script.read_text(encoding="utf-8")
    assert "core.ui" not in src and "from core import ui" not in src, \
        f"{script.name}: интерактив удалён вместе с cli "


# ══════════════════════════════════════════════════════════════════════
# LLM-гигиена: один стрим и один determine_model
# ══════════════════════════════════════════════════════════════════════
def test_core_common_has_the_single_stream():
    src = CORE.read_text(encoding="utf-8")
    assert "def stream_chat_completion(" in src


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_uses_core_stream_not_raw_http(script):
    """Никто в cli/ не ходит в LLM напрямую: только stream_chat_completion."""
    src = script.read_text(encoding="utf-8")
    assert not re.search(r"(?:requests|httpx|aiohttp)\.(?:post|get|stream)\s*\(", src), \
        f"{script.name}: прямой HTTP-запрос запрещён — только stream_chat_completion"
    assert "iter_lines" not in src, \
        f"{script.name}: свой SSE-обработчик запрещён"


# ══════════════════════════════════════════════════════════════════════
# Внешние библиотеки: HTTP-клиент один (httpx) и импортирует его ТОЛЬКО транспорт
# ══════════════════════════════════════════════════════════════════════
HTTP_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+(?:httpx|requests|aiohttp|urllib3)\b", re.M)
ENTRYPOINTS = [CORE, ROOT / "run.py", *SCRIPTS, *WEB]


@pytest.mark.parametrize("entry", ENTRYPOINTS, ids=lambda p: p.name)
def test_only_transport_imports_http_library(entry):
    """Точка выхода в сеть одна: HTTP-клиент видит только core/transport.py."""
    if entry.name == "transport.py":
        pytest.skip("транспорт — единственное место HTTP-библиотек")
    src = entry.read_text(encoding="utf-8")
    found = HTTP_IMPORT_RE.search(src)
    assert found is None, (
        f"{entry.name}: HTTP-библиотека импортируется напрямую — "
        f"только core/transport.py ({found.group(0).strip()})")


def test_http_client_is_single():
    """Запасного HTTP-клиента нет: это второй адаптер, второй путь ошибок и
    второй набор тестов ради поведения, которое вызывающий не видит."""
    body = "\n".join(line for line in
                      (ROOT / "requirements.txt").read_text(
                          encoding="utf-8").splitlines()
                      if not line.lstrip().startswith("#"))
    names = re.findall(r"^\s*([A-Za-z0-9_.-]+)\s*(?:==|>=|>|$)", body, re.M)
    assert [n.lower() for n in names].count("httpx") == 1, f"транспорт: {names}"
    for extra in ("requests", "aiohttp", "urllib3"):
        assert extra not in [n.lower() for n in names], f"{extra} вернулся в pip-список"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_has_no_local_determine_model(script):
    src = script.read_text(encoding="utf-8")
    assert not re.search(r"def\s+determine_model\s*\(", src), \
        f"{script.name}: локальный determine_model запрещён — только core.common"


# ══════════════════════════════════════════════════════════════════════
# bootstrap + слои
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("entry", SCRIPTS, ids=lambda p: p.name)
def test_entry_has_bootstrap(entry):
    src = entry.read_text(encoding="utf-8")
    if "core.common" not in src:
        pytest.skip("модуль не импортирует core — bootstrap не обязателен")
    assert "_bootstrap_core" in src, f"{entry.name}: нет bootstrap-паттерна (§4)"
    assert "sys.path" in src


@pytest.mark.parametrize("module", WEB, ids=lambda p: p.name)
def test_web_module_does_not_call_http_directly(module):
    """web-слой не ходит в LLM сам: процессы стадий — subprocess, HTTP — там."""
    src = module.read_text(encoding="utf-8")
    assert "iter_lines" not in src, f"{module.name}: свой SSE-обработчик запрещён"


@pytest.mark.parametrize("module", WEB, ids=lambda p: p.name)
def test_web_module_imports_from_web_only(module):
    """web/*.py импортирует свои соседей через from web.* — не копирует
    логику и не ссылается на удалённые backends/cli."""
    src = module.read_text(encoding="utf-8")
    assert "backends" not in src, \
        f"{module.name}: ссылка на удалённый backends/ "
    assert "core.ui" not in src and "core.tui" not in src, \
        f"{module.name}: cli/tui удалены"


# ══════════════════════════════════════════════════════════════════════
# run.py — тонкий лаунчер web
# ══════════════════════════════════════════════════════════════════════
def test_run_py_is_web_launcher():
    src = (ROOT / "run.py").read_text(encoding="utf-8")
    assert "ACTIONS" not in src, "ACTIONS удалён — реестр стадий в web/stages.py"
    assert "BACKENDS" not in src, "BACKENDS удалён — бэкэнд один (web)"
    assert "choose_backend" not in src
    assert "run_web_backend" in src
    assert "web" in src  # путь к web/main.py


def test_web_main_help():
    """main.py --help работает и описывает ключевые флаги."""
    import subprocess
    r = subprocess.run([sys.executable,
                        str(ROOT / "web" / "main.py"), "--help"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    for flag in ("--host", "--port", "--no-auth", "--token", "--jobs-limit"):
        assert flag in r.stdout, f"--help не упоминает {flag}"


def test_web_main_serves(srv_port):
    """main.py поднимает сервер: /api/session отвечает без токена (--no-auth)."""
    import json
    import subprocess
    import time
    import urllib.request
    p = subprocess.Popen(
        [sys.executable, str(ROOT / "web" / "main.py"),
         "--host", "127.0.0.1", "--port", str(srv_port),
         "--no-auth", "--token", "smoke-test"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        url = f"http://127.0.0.1:{srv_port}/api/session"
        deadline = time.time() + 20
        payload = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                break
            except Exception:
                time.sleep(0.2)
        assert payload is not None, "сервер не поднялся за 20 c"
        assert payload["ok"] is True
        assert payload["authenticated"] is True
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=5)

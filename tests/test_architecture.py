#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Архитектурные стражи (AGENTS.md §3–§4, web-first):
статические проверки по исходникам. Ловят будущие правки, ломающие
слоистость, единую LLM-гигиену и web-канон (без cli/tui)."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPTS = sorted((ROOT / "scripts").glob("*.py"))
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
    assert not (ROOT / "scripts" / "Other_tools").exists()


# ══════════════════════════════════════════════════════════════════════
# scripts/ = чистый CLI: никакого интерактива
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_has_no_input_calls(script):
    src = script.read_text(encoding="utf-8")
    assert not re.search(r"(?<![\w.])input\s*\(", src), \
        f"{script.name}: input() запрещён в scripts/ (только argparse)"


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
def test_script_uses_core_stream_not_requests_post(script):
    """Никто в scripts/ не ходит в LLM напрямую (requests.post/iter_lines)."""
    src = script.read_text(encoding="utf-8")
    assert not re.search(r"requests\.post\s*\(", src), \
        f"{script.name}: прямой requests.post запрещён — только stream_chat_completion"
    assert "iter_lines" not in src, \
        f"{script.name}: свой SSE-обработчик запрещён"


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

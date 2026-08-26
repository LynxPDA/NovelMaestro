#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py — тонкий лаунчер web : bootstrap projects/ + корневого
.env, проброс настроек в web/main.py. Без сети, всё в tmp_path:
глобальные REPO/PROJECTS подменяются monkeypatch, subprocess — моком."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import run as RUN              # noqa: E402
from core import projects as P  # noqa: E402


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """Временное репо: templates/ + пустой projects/; глобалы run.py
    переключены на него."""
    repo = tmp_path / "repo"
    (repo / "templates").mkdir(parents=True)
    (repo / "templates" / ".env.example").write_text("HOST=x\n",
                                                     encoding="utf-8")
    projects = repo / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr(RUN, "REPO", repo)
    monkeypatch.setattr(RUN, "PROJECTS", projects)
    return repo


# ──────────────────────────────────────────────────────────────────────
# bootstrap_projects
# ──────────────────────────────────────────────────────────────────────
def test_bootstrap_creates_sections_and_env(fake_repo, capsys):
    RUN.bootstrap_projects()
    for sec in P.SECTIONS:
        assert (RUN.PROJECTS / sec).is_dir()
    assert (RUN.REPO / ".env").is_file()  # системный корневой .env
    out = capsys.readouterr().out
    assert "templates/.env.example" in out


def test_bootstrap_env_copied_only_once(fake_repo, monkeypatch, capsys):
    RUN.bootstrap_projects()
    (RUN.REPO / ".env").write_text("HOST=y\n", encoding="utf-8")
    RUN.bootstrap_projects()  # повтор: .env уже есть — не перезаписываем
    assert (RUN.REPO / ".env").read_text(encoding="utf-8") == "HOST=y\n"


def test_bootstrap_idempotent_when_env_exists(fake_repo, monkeypatch):
    (RUN.REPO / ".env").write_text("x", encoding="utf-8")
    RUN.bootstrap_projects()
    for sec in P.SECTIONS:
        assert (RUN.PROJECTS / sec).is_dir()
    assert (RUN.REPO / ".env").read_text(encoding="utf-8") == "x"


def test_bootstrap_migrates_legacy_projects_env(fake_repo, capsys):
    """Старый projects/.env переносится в корень при первом запуске."""
    (RUN.PROJECTS / ".env").write_text("HOST=legacy\n", encoding="utf-8")
    RUN.bootstrap_projects()
    assert not (RUN.PROJECTS / ".env").exists()
    assert (RUN.REPO / ".env").read_text(encoding="utf-8") == "HOST=legacy\n"


# ──────────────────────────────────────────────────────────────────────
# run_web_backend: проброс аргументов в web/main.py
# ──────────────────────────────────────────────────────────────────────
def test_run_web_backend_default_cmd(fake_repo, monkeypatch):
    captured = {}

    def fake_run(cmd, cwd=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd

    monkeypatch.setattr(RUN.subprocess, "run", fake_run)
    args = RUN.argparse.Namespace(host=None, port=None, auth=False,
                                  token=None, max_upload_mb=None,
                                  jobs_limit=None, no_open=False)
    RUN.run_web_backend(args)
    cmd = captured["cmd"]
    assert cmd[0] == sys.executable
    assert cmd[1] == str(RUN.REPO / "web" / "main.py")
    assert "--open" in cmd                      # браузер по умолчанию
    assert "--auth" not in cmd
    assert captured["cwd"] == str(RUN.REPO)


def test_run_web_backend_passthrough(fake_repo, monkeypatch):
    captured = {}

    def fake_run(cmd, cwd=None):
        captured["cmd"] = cmd

    monkeypatch.setattr(RUN.subprocess, "run", fake_run)
    args = RUN.argparse.Namespace(host="127.0.0.1", port=8899, auth=True,
                                  token="tok", max_upload_mb=100,
                                  jobs_limit=4, no_open=True)
    RUN.run_web_backend(args)
    cmd = captured["cmd"]
    assert "--host" in cmd and "127.0.0.1" in cmd
    assert "--port" in cmd and "8899" in cmd
    assert "--auth" in cmd
    assert "--token" in cmd and "tok" in cmd
    assert "--max-upload-mb" in cmd and "100" in cmd
    assert "--jobs-limit" in cmd and "4" in cmd
    assert "--open" not in cmd                  # --no-open


def test_main_launches_web(fake_repo, monkeypatch):
    called = {}

    def fake_run_web(args):
        called["args"] = args

    monkeypatch.setattr(RUN, "run_web_backend", fake_run_web)
    monkeypatch.setattr(sys, "argv", ["run.py", "--no-open"])
    RUN.main()
    assert called["args"].no_open is True


def test_force_utf8_io_no_crash():
    """_force_utf8_io терпит любые стримы (в т.ч. подмены pytest)."""
    RUN._force_utf8_io()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты песочницы путей web-бэкэнда (web/sandbox.py).

Проверяются: нормальное разрешение, запрет абсолютных путей и `..`,
симлинк-побег, NUL-байт, пустой rel; resolve_repo_path — корень репо.
"""
import pytest

from web.sandbox import SandboxError, resolve_path, resolve_repo_path


def test_resolve_normal(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "a").mkdir()
    out = resolve_path(base, "a/b.txt")
    assert out == (base / "a" / "b.txt").resolve()
    assert out.is_relative_to(base.resolve())


def test_resolve_empty_rel_is_base(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    assert resolve_path(base, "") == base.resolve()


def test_resolve_absolute_rejected(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    with pytest.raises(SandboxError):
        resolve_path(base, str(tmp_path / "outside"))
    with pytest.raises(SandboxError):
        resolve_path(base, "/etc/passwd")


def test_resolve_dotdot_rejected(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    with pytest.raises(SandboxError):
        resolve_path(base, "../x")
    with pytest.raises(SandboxError):
        resolve_path(base, "a/../../x")


def test_resolve_symlink_escape_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    (base / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SandboxError):
        resolve_path(base, "link/x")


def test_resolve_nul_rejected(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    with pytest.raises(SandboxError):
        resolve_path(base, "a\x00b")


def test_resolve_repo_path_known():
    """Известный файл репозитория резолвится; выход за пределы — нет."""
    p = resolve_repo_path("core/common.py")
    assert p.is_file()
    with pytest.raises(SandboxError):
        resolve_repo_path("../outside")
    with pytest.raises(SandboxError):
        resolve_repo_path("/etc/passwd")

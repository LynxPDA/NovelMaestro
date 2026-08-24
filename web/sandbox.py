#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sandbox.py — песочница путей web-бэкэнда.

Все пути в API — относительные. resolve_path() запрещает абсолютные пути,
`..` за пределы базы и симлинк-побег. Базы: проект (rw) и репозиторий (ro).
"""
from __future__ import annotations

from pathlib import Path


class SandboxError(Exception):
    """Запрещённый путь или операция в песочнице."""


def resolve_path(base: Path, rel: str) -> Path:
    """Разрешает относительный путь `rel` внутри `base`.

    Запрещает: абсолютные пути, выход за пределы base через `..`,
    симлинк-побег (resolve + проверка is_relative_to). Пустой `rel`
    возвращает саму базу.
    """
    if "\x00" in rel:
        raise SandboxError("Путь содержит NUL-байт")
    p = Path(rel)
    if p.is_absolute():
        raise SandboxError("Абсолютные пути запрещены")
    if ".." in p.parts:
        raise SandboxError("Выход за пределы песочницы запрещён")
    base_r = base.resolve()
    target = (base_r / p).resolve(strict=False)
    if not target.is_relative_to(base_r):
        raise SandboxError("Путь ведёт за пределы песочницы (симлинк-побег)")
    return target


def resolve_repo_path(rel: str) -> Path:
    """Разрешает путь внутри репозитория (только чтение, для шаблонов)."""
    repo = _find_repo_root()
    return resolve_path(repo, rel)


def _find_repo_root() -> Path:
    """Корень репо: маркер core/common.py, подъём вверх от этого файла."""
    p = Path(__file__).resolve().parent
    for _ in range(6):
        if (p / "core" / "common.py").is_file():
            return p
        if p.parent == p:
            break
        p = p.parent
    raise SandboxError("Корень репозитория не найден")

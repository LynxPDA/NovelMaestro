#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tokenize.py — счёт токенов для проверок объёма: оценка или реальные токенизаторы.

Два источника счёта:
  • `estimate` — встроенная оценка estimate_tokens из core.common: стандартная
    библиотека, работает всегда (запасной вариант роли);
  • файлы формата HF `tokenizers` (tokenizer.json) в папке tokenizers/ корня
    репо: встроенный gemma-4-31b.json (токенизатор Gemma 4 31b, Apache 2.0)
    и любые пользовательские файлы рядом — для кода они неразличимы.

Реальный счёт даёт библиотека `tokenizers` (HF, Rust): без неё, с битым или
удалённым файлом счёт уходит в оценку с предупреждением — запуск не падает.
Проверке объёма это не мешает: она нормируется на медиану книги, поэтому
систематическое смещение оценки (±20–30%) на детекцию не влияет — оно одно
и то же для всех глав.

Токенизатор грузится один раз на процесс; спец-токены (BOS/EOS) в счёт не
добавляются — считается объём текста, а не вход генерации.
"""
# Опциональная зависимость: запасной вариант роли (оценка estimate_tokens)
# обязателен по политике §2, поэтому проверка импорта — по месту, в _lib().
# pyright: reportMissingImports=false
from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import estimate_tokens, read_text_safe

#: id встроенной оценки — первый пункт списка выбора, без файла и библиотеки
ESTIMATE_ID = "estimate"
ESTIMATE_TITLE = "Оценка (встроенная)"
#: папка токенизаторов в корне репо (рядом с core/)
DIR_NAME = "tokenizers"
#: размер пачки encode_batch — ограничение памяти на большой книге
_BATCH = 256

_LIB: Any = None           # класс Tokenizer | False (проверили — библиотеки нет)
_LOADED: dict = {}         # id → Tokenizer | None
_LOAD_ERR: dict = {}       # id → причина (файла нет / не читается)
_RESOLVED: dict = {}       # запрос → (активный id, причина фолбэка)
_CHECKED: dict = {}        # (path, mtime_ns, size) → (ok, error)


def dir_path() -> Path:
    """Папка токенизаторов: <корень репо>/tokenizers."""
    # core/tokenize.py → родитель core/ и есть корень репозитория
    return Path(__file__).resolve().parent.parent / DIR_NAME


def _lib() -> Any:
    """Класс Tokenizer библиотеки tokenizers или None.

    Папка репо tokenizers/ видна импорту как namespace-пакет: без
    библиотеки `from tokenizers import Tokenizer` даёт ImportError — это
    и есть признак «не установлена». С библиотекой настоящий пакет
    побеждает namespace-папку на любом порядке sys.path (PEP 420).
    """
    global _LIB
    if _LIB is None:
        try:
            from tokenizers import Tokenizer
            _LIB = Tokenizer
        except Exception:  # noqa: BLE001 — нет библиотеки или битое окружение
            _LIB = False
    return _LIB or None


def _load(tid: str) -> tuple[Any, str]:
    """Токенизатор файла id → (Tokenizer | None, причина). Кеш на процесс."""
    if tid in _LOADED:
        return _LOADED[tid], _LOAD_ERR.get(tid, "")
    lib = _lib()
    if lib is None:
        reason = "библиотека tokenizers не установлена (pip install tokenizers)"
    else:
        path = dir_path() / f"{tid}.json"
        if not path.is_file():
            reason = f"файла нет: {path}"
        else:
            try:
                _LOADED[tid] = lib.from_file(str(path))
                return _LOADED[tid], ""
            except Exception as exc:  # noqa: BLE001 — битый/чужой файл
                reason = f"файл не читается: {exc}"
    _LOADED[tid] = None
    _LOAD_ERR[tid] = reason
    return None, reason


def resolve(requested: str = "", logger: Any = None) -> tuple[str, str]:
    """Запрошенный id → (активный id, причина фолбэка; '' — работаем напрямую).

    Пустой запрос и `estimate` — встроенная оценка. Недоступный файл или
    библиотека — оценка с ОДНИМ предупреждением на процесс (кеш решения).
    """
    req = str(requested or "").strip() or ESTIMATE_ID
    if req in _RESOLVED:
        return _RESOLVED[req]
    if req == ESTIMATE_ID:
        result = (ESTIMATE_ID, "")
    else:
        tok, reason = _load(req)
        if tok is not None:
            result = (req, "")
        else:
            msg = (f"Токенизатор «{req}» недоступен ({reason}) — "
                   f"счёт встроенной оценкой estimate_tokens.")
            if logger is not None:
                logger.warning(msg)
            else:
                print(f"⚠ {msg}")
            result = (ESTIMATE_ID, reason)
    _RESOLVED[req] = result
    return result


def count_tokens(text: str, tokenizer_id: str = "") -> int:
    """Токены одного текста активным счётчиком; пустой текст — 0."""
    if not text:
        return 0
    tid, _ = resolve(tokenizer_id)
    if tid == ESTIMATE_ID:
        return estimate_tokens(text)
    enc = _LOADED[tid].encode(text, add_special_tokens=False)
    return len(enc.ids)


def count_files(paths, tokenizer_id: str = "", logger: Any = None) -> dict:
    """Токены каждого файла одним пакетом: {path: count}.

    Чтение — read_text_safe (utf-8 → cp1251 → gb18030); пустой и нечитаемый
    файл — 0 (ловится структурной проверкой минимального размера). Числа
    считаются за один прогон и переиспользуются всеми парами сравнений.
    """
    tid, _ = resolve(tokenizer_id, logger=logger)
    out: dict = {}
    live: list[str] = []
    texts: list[str] = []
    for p in paths:
        key = str(p)
        try:
            t = read_text_safe(p)
        except Exception as exc:  # noqa: BLE001 — нечитаемый файл не роняет прогон
            if logger is not None:
                logger.warning("файл не читается: %s (%s)", p, exc)
            out[key] = 0
            continue
        if not t:
            out[key] = 0
            continue
        live.append(key)
        texts.append(t)
    if tid == ESTIMATE_ID:
        for key, t in zip(live, texts):
            out[key] = estimate_tokens(t)
        return out
    tok = _LOADED[tid]
    for i in range(0, len(texts), _BATCH):
        encs = tok.encode_batch(texts[i:i + _BATCH], add_special_tokens=False)
        for key, enc in zip(live[i:i + _BATCH], encs):
            out[key] = len(enc.ids)
    return out


def available() -> list[dict]:
    """Счётчики для списка выбора: estimate всегда первым, затем файлы папки.

    ok=False — библиотеки нет или файл не грузится: выбор такого пункта
    работает на оценке с предупреждением (см. resolve), запуск не падает.
    Проверка файла — полная загрузка, кеш по (путь, mtime, размер).
    """
    out = [{"id": ESTIMATE_ID, "title": ESTIMATE_TITLE, "ok": True, "error": ""}]
    lib = _lib()
    for path in sorted(dir_path().glob("*.json")):
        tid = path.stem
        if lib is None:
            out.append({"id": tid, "title": tid, "ok": False,
                        "error": "библиотека tokenizers не установлена "
                                 "(pip install tokenizers)"})
            continue
        ok, err = _check_file(path)
        out.append({"id": tid, "title": tid, "ok": ok, "error": err})
    return out


def _check_file(path: Path) -> tuple[bool, str]:
    """Грузится ли файл токенизатора: (ok, причина). Кеш по содержимому."""
    try:
        stat = path.stat()
    except OSError as exc:
        return False, str(exc)
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key in _CHECKED:
        return _CHECKED[key]
    lib = _lib()
    try:
        lib.from_file(str(path))
        result = (True, "")
    except Exception as exc:  # noqa: BLE001 — битый/чужой JSON
        result = (False, str(exc))
    _CHECKED[key] = result
    return result

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты tools/vendor_assets.py — локальных библиотек SPA (офлайн-канон).

Что бережётся:
- всё, что раздаётся браузеру, лежит в репо: в index.html нет ни одной ссылки
  на CDN (пользователь без сети получает тот же интерфейс);
- манифест `vendor.lock.json` описывает каждый файл вендора (sha256/размер),
  а не «что-то лежит рядом»; подмена байт или молчаливая смена версии ловится;
- удалённые библиотеки не возвращаются (Alpine SPA не нужен).

Проверки — чистые функции над временными копиями: репозиторий тесты не правят.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "web" / "static" / "vendor"
STATIC = ROOT / "web" / "static"
sys.path.insert(0, str(ROOT))

# tools/ — не пакет: модуль подключается по пути
_spec = importlib.util.spec_from_file_location("vendor_assets",
                                              ROOT / "tools" / "vendor_assets.py")
assert _spec and _spec.loader, "tools/vendor_assets.py не найден"
V = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V)


@pytest.fixture()
def sandbox(tmp_path):
    """Полная копия web/static (включая vendor/) во временной папке:
    ссылки index.html на /app.js и /vendor/* остаются разрешимыми."""
    static = tmp_path / "static"
    for src in sorted(STATIC.rglob("*")):
        rel = src.relative_to(STATIC)
        dst = static / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
    return {"vendor": static / "vendor", "static": static}


# ══════════════════════════════════════════════════════════════════════
# Репозиторий как есть
# ══════════════════════════════════════════════════════════════════════
def test_repo_vendor_matches_manifest():
    assert V.check_lock(VENDOR) == []


def test_repo_spa_has_no_remote_assets():
    assert V.check_offline(STATIC) == []


def test_manifest_is_canonical_json():
    """Манифест побайтово равен канонической пересборке: diff в git — только по делу."""
    raw = (VENDOR / V.LOCK_NAME).read_text(encoding="utf-8")
    assert raw == json.dumps(V.build_lock(VENDOR), ensure_ascii=False, indent=2) + "\n"


def test_every_asset_has_meta():
    """Каждая запись подписана: кто, какой версии, под чем лицензия."""
    for row in V.load_lock(VENDOR).get("assets", []):
        assert row["name"], f"{row['file']}: пустой name"
        assert row["license"], f"{row['file']}: не указана лицензия"
        assert row["homepage"], f"{row['file']}: нет homepage"
        assert row["kind"] in ("url", "bundle"), f"{row['file']}: неизвестный kind"
        if row["kind"] == "url":
            assert row["url"].startswith("https://"), f"{row['file']}: url не https"
            assert row["version"] in row["url"], \
                f"{row['file']}: версия {row['version']} не в URL"


def test_index_html_loads_only_local_assets():
    """index.html: все ассеты — локальные пути (или data:-URI фавиконки)."""
    text = (STATIC / "index.html").read_text(encoding="utf-8")
    for url in V._ASSET_URL_RE.findall(text):
        assert not url.startswith(("http://", "https://", "//")), \
            f"в рантайме дёргается CDN: {url}"


def test_vendored_alpine_stays_removed():
    """SPA на ванильном JS: Alpine удалён — возвращать файл не «мелочь», а регресс."""
    assert "alpine.min.js" in V.RETIRED
    assert not (VENDOR / "alpine.min.js").exists()


def test_locked_versions_are_pinned():
    """Версии зафиксированы (диапазонов «latest» в вендоре быть не может)."""
    for row in V.load_lock(VENDOR).get("assets", []):
        assert row["version"] not in ("", "latest", "*", None), \
            f"{row['file']}: версия не зафиксирована"


# ══════════════════════════════════════════════════════════════════════
# Ловим расхождения на временной копии
# ══════════════════════════════════════════════════════════════════════
def test_reports_absent_declared_file(sandbox):
    (sandbox["vendor"] / "marked.min.js").unlink()
    problems = V.check_lock(sandbox["vendor"])
    assert any("marked.min.js" in p and "лежит вне vendor" in p for p in problems), problems


def test_reports_undeclared_file(sandbox):
    (sandbox["vendor"] / "jquery.min.js").write_text("!function(){}", encoding="utf-8")
    problems = V.check_lock(sandbox["vendor"])
    assert any("jquery.min.js" in p and "не объявлен" in p for p in problems), problems


def test_reports_modified_bytes(sandbox):
    path = sandbox["vendor"] / "marked.min.js"
    path.write_bytes(path.read_bytes() + b"\n/* patch */\n")
    problems = V.check_lock(sandbox["vendor"])
    assert any("sha256" in p for p in problems), problems


def test_reports_retired_asset_return(sandbox):
    (sandbox["vendor"] / "alpine.min.js").write_text("// alpine", encoding="utf-8")
    problems = V.check_lock(sandbox["vendor"])
    assert any("вернулся удалённый ассет" in p for p in problems), problems


def test_reports_missing_manifest(sandbox):
    (sandbox["vendor"] / V.LOCK_NAME).unlink()
    assert any("манифест" in p for p in V.check_lock(sandbox["vendor"]))


def test_reports_broken_manifest(sandbox):
    (sandbox["vendor"] / V.LOCK_NAME).write_text("{", encoding="utf-8")
    assert V.check_lock(sandbox["vendor"])


def test_reports_empty_name_in_manifest(sandbox):
    lock = json.loads((sandbox["vendor"] / V.LOCK_NAME).read_text(encoding="utf-8"))
    lock["assets"][0]["name"] = ""
    (sandbox["vendor"] / V.LOCK_NAME).write_text(
        json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert any("пустое поле name" in p for p in V.check_lock(sandbox["vendor"]))


def test_check_accepts_clean_copy(sandbox):
    assert V.check_lock(sandbox["vendor"]) == []


# ══════════════════════════════════════════════════════════════════════
# Офлайн-гейт index.html
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("tag,expect", [
    ('<script src="https://cdn.example.com/x.min.js"></script>', "из сети"),
    ('<link rel="stylesheet" href="//unpkg.com/x.css">', "из сети"),
    ('<script src="/vendor/нет-такого.js"></script>', "отсутствующий файл"),
    ('<script src="/app.js"></script>', None),
])
def test_offline_gate_on_index(sandbox, tag, expect):
    index = sandbox["static"] / "index.html"
    index.write_text("<!doctype html>\n<html>\n" + tag + "\n</html>\n",
                     encoding="utf-8")
    problems = V.check_offline(sandbox["static"])
    if expect is None:
        assert problems == []
    else:
        assert any(expect in p for p in problems), problems


# ══════════════════════════════════════════════════════════════════════
# SHA-канал: хэш в манифесте — он же версия ассета
# ══════════════════════════════════════════════════════════════════════
def test_sha256_matches_content():
    path = VENDOR / "marked.min.js"
    assert V.sha256(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_lock_rebuild_is_idempotent(tmp_path):
    """lock → lock → lock: манифест не ползёт от повторной записи."""
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "a.min.js").write_text("/*! a v1 */", encoding="utf-8")
    (vendor / "b.css").write_text("x{}", encoding="utf-8")
    first = V.build_lock(vendor)
    V.write_lock(first, vendor)
    second = V.build_lock(vendor)
    V.write_lock(second, vendor)
    assert first == second
    assert (vendor / V.LOCK_NAME).read_text(encoding="utf-8").endswith("\n")
    # метаданные существующих записей сохраняются
    (vendor / "c.min.js").write_text("/*! c */", encoding="utf-8")
    third = V.build_lock(vendor)
    assert third["assets"][0]["name"] == first["assets"][0]["name"]
    assert third["assets"][-1]["name"] == ""


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════
def test_cli_check_ok(capsys):
    assert V.main(["check"]) == 0
    assert "Вендор чист" in capsys.readouterr().out


def test_cli_list_shows_versions(capsys):
    assert V.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "codemirror" in out and "marked" in out


def test_cli_fetch_requires_name():
    with pytest.raises(SystemExit):
        V.main(["fetch"])


def test_cli_fetch_refuses_bundle_without_url(capsys):
    """Самосборный бандл нельзя «перекачать» — у него нет URL."""
    assert V.main(["fetch", "--name", "codemirror"]) == 1
    assert "самосборный бандл" in capsys.readouterr().out


def test_cli_fetch_reports_unknown_asset(capsys):
    assert V.main(["fetch", "--name", "vue"]) == 1
    assert "нет в манифесте" in capsys.readouterr().out


# ══════════════════════════════════════════════════════════════════════
# fetch: обновление ассета (сеть мокана, репозиторий не трогаем)
# ══════════════════════════════════════════════════════════════════════
class _FakeResp:
    """Ответ urllib с готовым телом (контекстный менеджер, как настоящий)."""

    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def fetch_env(sandbox, monkeypatch):
    """Песочница + мок urlopen: возвращает (vendor, список запрошенных URL)."""
    urls = []

    def _fake_urlopen(url, timeout=None, **_kw):
        urls.append(url)
        return _FakeResp(b"/*! marked v13.0.0 - a markdown parser */\n")

    monkeypatch.setattr(V, "VENDOR_DIR", sandbox["vendor"])
    monkeypatch.setattr(V, "STATIC_DIR", sandbox["static"])
    monkeypatch.setattr(V, "urlopen", _fake_urlopen)
    return sandbox["vendor"], urls


def _args(**kw):
    ns = __import__("argparse").Namespace
    return ns(name=kw.get("name"), version=kw.get("version"),
              yes=kw.get("yes", False))


def test_fetch_updates_file_and_manifest(fetch_env):
    vendor, urls = fetch_env
    assert V.main(["fetch", "--name", "marked", "--version", "13.0.0",
                   "--yes"]) == 0
    payload = b"/*! marked v13.0.0 - a markdown parser */\n"
    assert (vendor / "marked.min.js").read_bytes() == payload
    # версия подменена и в URL запроса, и в записи манифеста
    assert "13.0.0" in urls[0] and "12.0.2" not in urls[0]
    row = next(a for a in V.load_lock(vendor)["assets"]
               if a["file"] == "marked.min.js")
    assert row["version"] == "13.0.0"
    assert row["sha256"] == hashlib.sha256(payload).hexdigest()
    assert row["bytes"] == len(payload)
    # и гейт после обновления проходит
    assert V.check_lock(vendor) == []


def test_fetch_refuses_hash_change_without_yes(fetch_env):
    vendor, urls = fetch_env
    before = (vendor / "marked.min.js").read_bytes()
    assert V.main(["fetch", "--name", "marked"]) == 1
    assert (vendor / "marked.min.js").read_bytes() == before
    assert len(urls) == 1

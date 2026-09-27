#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Юзерскрипты tools/: артефакт == сборка из src/, канон метаданных.

Тестирует то, из-за чего раскладка юзерскриптов на части вообще безопасна:
собранный текст обязан побайтово совпадать с закоммиченным артефактом
(иначе «второй источник истины» разъедется), порядок частей обязан
оставаться порядком секций, версия обязана браться из одного места.
node --check по артефактам — только если node есть в PATH.
Запуск: python3 -m pytest tests/test_tools_userscripts.py -q"""
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
BUILDER = TOOLS / "build_userscripts.py"
sys.path.insert(0, str(ROOT))

# tools/ — не пакет, сборщик подключается по пути (bootstrap внутри него сам
# находит корень репо, так что импорт из временной копии тоже работает)
_spec = importlib.util.spec_from_file_location("build_userscripts", BUILDER)
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)

# каталог tools/ → имя публикуемого артефакта
SCRIPTS = {"NovelMaestro_Lite": "novelmaestro-lite.user.js",
           "rulate_reload": "rulate-bulk-update.user.js"}
REPO_SLUG = "LynxPDA/NovelMaestro"
CDN = "https://cdn.jsdelivr.net/gh/"
# канон метаданных юзерскрипта: без них скрипт в каталоге выглядит сырым
REQUIRED_KEYS = ["@name", "@namespace", "@version", "@description", "@author",
                 "@license", "@homepageURL", "@supportURL", "@match", "@grant",
                 "@run-at", "@downloadURL", "@updateURL"]
# нумерация частей с шагом 10: вставка части = новый файл 020-*.js, а не
# переименование всего хвоста
PART_RE = re.compile(r"^\d{2}0-[a-z0-9][a-z0-9-]*\.js$")


def run_builder(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    """Сборщик как процесс: он пишет артефакты, in-process звать его нельзя."""
    return subprocess.run([sys.executable, str(BUILDER), *args],
                          capture_output=True, text=True, cwd=str(cwd))


def run_builder_in(tmp: Path, *args: str) -> subprocess.CompletedProcess:
    """Сборщик из временной копии: core берётся по PYTHONPATH из репо."""
    env = dict(PATH="/usr/local/bin:/usr/bin:/bin", PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, str(tmp / "tools" / BUILDER.name), *args],
                          capture_output=True, text=True, cwd=str(tmp), env=env)


def banner_of(artifact: Path) -> str:
    """Блок метаданных артефакта."""
    text = artifact.read_text(encoding="utf-8")
    start, end = text.find("// ==UserScript=="), text.find("// ==/UserScript==")
    assert start >= 0 and end > start, f"{artifact.name}: нет блока метаданных"
    return text[start:end]


def test_builder_discovers_both_scripts():
    """Оба юзерскрипта находятся по meta.js + src/, --list их печатает."""
    res = run_builder("--list")
    assert res.returncode == 0, res.stderr
    for d, art in SCRIPTS.items():
        assert d in res.stdout and art in res.stdout, f"--list потерял {d}"
    assert {p.name for p in B.list_script_dirs()} == set(SCRIPTS), \
        "раскладка каталогов юзерскриптов изменилась — обнови тест"


@pytest.mark.parametrize("script,artifact", sorted(SCRIPTS.items()))
def test_artifact_matches_build(script, artifact):
    """Главный регресс-гард: закоммиченный артефакт == сборка из src/."""
    res = run_builder("--script", script, "--check")
    assert res.returncode == 0, f"{artifact} устарел: {res.stderr}"


@pytest.mark.parametrize("script,artifact", sorted(SCRIPTS.items()))
def test_artifact_name_follows_userscript_canon(script, artifact):
    """Имя публикуемого файла — kebab-case и обязательно .user.js."""
    assert artifact.endswith(".user.js")
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]*\.user\.js", artifact), artifact


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_wrapper_lives_in_parts_not_in_builder(script):
    """Части нумерованы; IIFE-обёртка — 000-open.js/900-close.js, а не код сборщика."""
    parts = sorted((TOOLS / script / "src").glob("*.js"))
    assert parts, f"{script}: нет частей"
    for p in parts:
        assert PART_RE.match(p.name), f"имя части вне схемы NN0-slug.js: {p.name}"
    assert parts[0].name == B.OPEN_PART and parts[-1].name == B.CLOSE_PART
    assert parts[0].read_text(encoding="utf-8").lstrip().startswith("("), \
        f"{script}/{B.OPEN_PART} обязан открывать IIFE"
    assert parts[-1].read_text(encoding="utf-8").strip() == "})();", \
        f"{script}/{B.CLOSE_PART} обязан закрывать IIFE"


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_parts_are_concatenated_verbatim(script):
    """Части склеиваются побайтово: кроме {{VERSION}} переносов и отступов не меняется."""
    parts = sorted((TOOLS / script / "src").glob("*.js"))
    version = B.script_version(B.read_lf(TOOLS / script / "meta.js"))
    body = "".join(B.read_lf(p) for p in parts).replace(B.VERSION_TOKEN, version)
    artifact = (TOOLS / script / SCRIPTS[script]).read_text(encoding="utf-8")
    assert artifact.endswith(body), f"{script}: артефакт не равен телу из частей"


@pytest.mark.parametrize("script,artifact", sorted(SCRIPTS.items()))
def test_meta_has_canonical_keys(script, artifact):
    """Баннер полон: name/namespace/version/description/author/license/ссылки."""
    banner = banner_of(TOOLS / script / artifact)
    for key in REQUIRED_KEYS:
        assert re.search(rf"^//[ \t]*{key}\b", banner, re.MULTILINE), \
            f"{artifact}: в блоке метаданных нет {key}"
    assert banner.count("@version") == 1, f"{artifact}: строка @version должна быть одна"
    # namespace = URL репо: скрипты ещё не распространялись, менять его поздно
    # нельзя — у уже поставивших менеджер опознаёт скрипт по name+namespace
    ns = re.search(r"^//[ \t]*@namespace[ \t]+(\S+)", banner, re.MULTILINE).group(1)
    assert ns == f"https://github.com/{REPO_SLUG}", \
        f"{artifact}: @namespace должен быть URL репо, сейчас {ns}"


def test_lite_runs_only_in_top_document():
    """Lite обязана нести @noframes: без неё скрипт поднимается в каждом
    совпавшем фрейме (проверено на живых страницах — там дублировались и
    теневой хост, и набор плавающих кнопок)."""
    banner = banner_of(TOOLS / "NovelMaestro_Lite" / SCRIPTS["NovelMaestro_Lite"])
    lines = [ln for ln in banner.splitlines() if "@noframes" in ln]
    assert len(lines) == 1, f"@noframes должна быть одна, сейчас: {lines}"
    # ключ без значения: текст рядом менеджер прочтёт как значение @noframes
    assert re.fullmatch(r"//[ \t]*@noframes[ \t]*", lines[0].rstrip()), \
        f"@noframes должна быть без значения: {lines[0]!r}"


@pytest.mark.parametrize("script,artifact", sorted(SCRIPTS.items()))
def test_update_urls_point_at_the_repo_path(script, artifact):
    """@downloadURL/@updateURL ведут на jsDelivr по фактическому пути артефакта."""
    banner = banner_of(TOOLS / script / artifact)
    for key in ("@downloadURL", "@updateURL"):
        m = re.search(rf"^//[ \t]*{key}[ \t]+(\S+)", banner, re.MULTILINE)
        assert m, f"{artifact}: нет {key}"
        url = m.group(1)
        assert url.startswith(CDN + REPO_SLUG + "@"), \
            f"{artifact}: {key} ведёт не на jsDelivr: {url}"
        assert url.endswith(f"/tools/{script}/{artifact}"), \
            f"{artifact}: {key} разошёлся с путём файла: {url}"


def test_lite_version_has_single_source():
    """@version в meta.js — единственный текст версии: в части только токен."""
    version = B.script_version(B.read_lf(TOOLS / "NovelMaestro_Lite" / "meta.js"))
    parts = sorted((TOOLS / "NovelMaestro_Lite" / "src").glob("*.js"))
    with_token = [p for p in parts if B.VERSION_TOKEN in B.read_lf(p)]
    assert len(with_token) == 1, "токен версии обязан быть ровно в одной части"
    assert with_token[0].name == "010-config.js", "токен версии переехал из 010-config.js"
    assert f"const APP_VERSION = '{version}'" not in B.read_lf(with_token[0]), \
        "в части остался литерал версии"
    artifact = (TOOLS / "NovelMaestro_Lite" / SCRIPTS["NovelMaestro_Lite"]).read_text(encoding="utf-8")
    assert f"const APP_VERSION = '{version}';" in artifact, "версия не доехала в артефакт"


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_no_crlf_anywhere(script):
    """Репо LF: CRLF сборщик нормализует, но в git ему там быть не должно."""
    paths = [TOOLS / script / SCRIPTS[script], TOOLS / script / "meta.js",
             *sorted((TOOLS / script / "src").glob("*.js"))]
    for p in paths:
        assert "\r" not in p.read_text(encoding="utf-8"), f"{p}: перевод строк CRLF"


@pytest.mark.skipif(shutil.which("node") is None, reason="node не установлен")
@pytest.mark.parametrize("script,artifact", sorted(SCRIPTS.items()))
def test_artifact_parses_with_node(script, artifact):
    """Синтаксис проверяется только на артефакте: части по отдельности не парсятся."""
    res = subprocess.run([shutil.which("node"), "--check", str(TOOLS / script / artifact)],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("script,artifact", sorted(SCRIPTS.items()))
def test_check_detects_stale_and_accepts_fresh(tmp_path, script, artifact):
    """--check ловит рассинхрон и молчит на свежем артефакте (смысл всего сбора)."""
    work = tmp_path / "tools"
    work.mkdir()
    shutil.copy2(BUILDER, work / BUILDER.name)
    dest = work / script
    shutil.copytree(TOOLS / script, dest)
    (dest / artifact).write_text("", encoding="utf-8")
    stale = run_builder_in(tmp_path, "--script", script, "--check")
    assert stale.returncode != 0, "пустой артефакт не был распознан как устаревший"
    assert artifact in stale.stderr and "расходится" in stale.stderr, stale.stderr

    body = next(p for p in sorted((dest / "src").glob("*.js")) if p.name != B.OPEN_PART)
    body.write_text(B.read_lf(body) + "\n// правка части без пересборки\n", encoding="utf-8")
    built = run_builder_in(tmp_path, "--script", script)
    assert built.returncode == 0, built.stderr
    fresh = run_builder_in(tmp_path, "--script", script, "--check")
    assert fresh.returncode == 0, f"свежий артефакт помечен устаревшим: {fresh.stderr}"
    assert "правка части без пересборки" in Path(dest / artifact).read_text(encoding="utf-8")


def test_builder_requires_version_in_meta(tmp_path):
    """Без @version сборщик обязан упасть, а не выпустить артефакт с {{VERSION}}."""
    work = tmp_path / "tools" / "broken_script"
    (work / "src").mkdir(parents=True)
    shutil.copy2(BUILDER, tmp_path / "tools" / BUILDER.name)
    meta = B.read_lf(TOOLS / "rulate_reload" / "meta.js")
    (work / "meta.js").write_text(
        "\n".join(ln for ln in meta.splitlines() if "@version" not in ln) + "\n",
        encoding="utf-8")
    for p in sorted((TOOLS / "rulate_reload" / "src").glob("*.js")):
        shutil.copy2(p, work / "src" / p.name)
    (work / "rulate-bulk-update.user.js").write_text("", encoding="utf-8")
    res = run_builder_in(tmp_path, "--script", "broken_script", "--check")
    assert res.returncode != 0, "meta.js без @version промолчан"
    assert "@version" in res.stderr, res.stderr

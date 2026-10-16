#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Юзерскрипты tools/: артефакт == сборка из src/, канон метаданных.

Тестирует то, из-за чего раскладка юзерскриптов на части вообще безопасна:
собранный текст обязан побайтово совпадать с закоммиченным артефактом
(иначе «второй источник истины» разъедется), порядок частей обязан
оставаться порядком секций, версия обязана браться из одного места.
node --check по артефактам — только если node есть в PATH.
Поведение чистых кусков логики Lite (тело LLM-запроса, состояние панели прогресса)
покрывается node --test по tests/tools/*.test.mjs — тоже только при наличии node.
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
# проверка обновлений ходит на raw GitHub: CDN ветки отдаёт файл с
# cache-control: public, max-age=604800, и пуш кэш не снимает — менеджер
# неделями видел бы прежний @version
RAW = "https://raw.githubusercontent.com/"
# @connect: special-значения из спеки + типовые хосты; Lite ходит в LLM-серверы,
# rulate работает только с самим сайтом
CONNECT_SPECIALS = {"self", "localhost", "*"}
CONNECT_COMMON = ["self", "localhost"]
CONNECT_HOST_RE = re.compile(r"[-a-z0-9]+(?:\.[-a-z0-9]+)+")
CONNECT_EXPECT = {
    "NovelMaestro_Lite": ("routerai.ru", "routerapi.ru", "zveno.ai", "api.openai.com",
                          "openrouter.ai", "api.anthropic.com",
                          "generativelanguage.googleapis.com", "api.deepseek.com"),
    "rulate_reload": ("rulate.ru", "tl.rulate.ru"),
}
# канон метаданных юзерскрипта: без них скрипт в каталоге выглядит сырым
REQUIRED_KEYS = ["@name", "@namespace", "@version", "@description", "@author",
                 "@license", "@homepageURL", "@supportURL", "@match", "@grant",
                 "@run-at", "@downloadURL", "@updateURL"]
# нумерация частей с шагом 10: вставка части = новый файл 025-*.js, а не
# переименование всего хвоста (последняя цифра у вставки может быть любой)
PART_RE = re.compile(r"^\d{3}-[a-z0-9][a-z0-9-]*\.js$")


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


def test_lite_reader_ui_invariants():
    """Читалка: топбар — только ⋮ и ✕; порядок панели ⋮; экспорт TXT не в карточке книги;
    тумблер «только текущая страница» и транспорты по умолчанию."""
    art = TOOLS / "NovelMaestro_Lite" / "novelmaestro-lite.user.js"
    text = art.read_text(encoding="utf-8")
    top = re.search(r'nm-reader-topbar-buttons"\s*>\s*<button id="reader-menu".*?✕</button>\s*</div>', text, re.S)
    assert top, "в топбаре читалки остались лишние кнопки (или изменилась разметка)"
    panel = re.search(r'id="reader-menu-panel">(.*?)</div>', text, re.S)
    assert panel, "нет панели ⋮ читалки"
    ids = re.findall(r'id="(reader-[a-z-]+)"', panel.group(1))
    assert ids == ["reader-export", "reader-retranslate", "reader-theme-toggle", "reader-settings"], \
        f"порядок панели ⋮ читалки изменился: {ids}"
    assert 'id="btn-export-txt"' not in text, "экспорт TXT вернулся в карточку книги"
    assert 'btn-full-export' not in text, 'полный бэкап должен быть удалён из настроек'
    assert 'бета-ридер' in text and 'Телепатические сообщения' in text, 'не штатный промпт перевода'
    assert 'Тема: ${THEME_MODE_LABELS' in text, 'кнопка темы читалки не подписывает режим' 
    assert 'id="glossary-current-only"' in text, "тумблер «только текущая страница» пропал"
    assert "gmTransport: 'page'" in text and "glossaryCurrentPageOnly: false" in text, \
        "дефолты транспорта/глоссария изменились — обнови тест"
    assert "sawDone" in text and "без [DONE]" in text, "исчез контроль завершения ответа"


def test_lite_modals_close_only_explicitly():
    """Модалки закрываются только явно: клик по подложке ловил случайные клики и
    отпускание выделения текста, выдернутое за пределы окна."""
    text = (TOOLS / "NovelMaestro_Lite" / "novelmaestro-lite.user.js").read_text(encoding="utf-8")
    assert not re.search(r"(modal|bookModal)\.addEventListener\('click'", text), \
        "вернулся слушатель клика по подложке модалки"
    assert "$('#nm-close').addEventListener('click', closeModal)" in text, "#nm-close не закрывает модалку"
    assert "#btn-cancel-new-book" in text, "модалку книги нечем закрыть явно"
    # дропдаун меню и панель читалки закрываются кликом мимо — это не модалки
    assert "dropdownMenu.classList.remove('active')" in text, "дропдаун ⋮ перестал закрываться"
    assert "panel.classList.remove('active')" in text, "панель меню читалки перестала закрываться"


def test_lite_api_key_is_masked_by_style():
    """Поле ключа — обычный text с маской слоем: браузер считает password-поле
    сохранённым логином и предлагает сохранить ключ на сайтах с формой входа."""
    text = (TOOLS / "NovelMaestro_Lite" / "novelmaestro-lite.user.js").read_text(encoding="utf-8")
    assert '<input type="text" class="nm-input nm-masked" id="api-key"' in text, "поле ключа не text с маской"
    assert not re.search(r'<input[^>]*id="api-key"[^>]*type="password"', text), "парольное поле вернулось"
    assert "-webkit-text-security: disc" in text and ".nm-masked.nm-reveal" in text, "маска ключа или кнопка «глаз» пропали"
    assert "if (!CSS.supports || !CSS.supports('-webkit-text-security', 'disc'))" in text, \
        "фолбэк для браузера без свойства потерян"
    assert "keyInput.type = 'password'" in text and "keyInput.autocomplete = 'new-password'" in text, \
        "фолбэк подставляет сохранённый логин вместо нового пароля"
    assert "if (!el.getAttribute('autocomplete')) el.autocomplete = 'off'" in text, \
        "автосписок не отключён остальным полям"


def test_lite_server_check_verdict_is_about_the_model():
    """Вердикт проверки сервера — по GET /models (есть ли нужная модель), пинг чата —
    только фолбэк; на время проверки кнопка заблокирована."""
    text = (TOOLS / "NovelMaestro_Lite" / "novelmaestro-lite.user.js").read_text(encoding="utf-8")
    assert "const modelsUrl = base + '/models'" in text, "/models больше не первый запрос проверки"
    assert "modelMatch(ids, config.model)" in text, "вердикт не сверяет модель"
    assert "similarModels(ids, config.model)" in text, "нет подсказки по похожим id"
    assert "// /models не поддерживается — канал чата проверяем пингом" in text, "пинг потерял статус фолбэка"
    assert "show('loading', `⚠️ ${target}: /models не поддерживается" in text, \
        "/models без поддержки перестал давать ⚠️"
    assert "if (btn) btn.disabled = true;" in text and "if (btn) btn.disabled = false;" in text, \
        "кнопка проверки не блокируется на время запроса"


def test_lite_ghost_never_outlives_the_work():
    """Призрак живёт только вместо пустого экрана: не поднимается на готовой главе из
    кэша, гаснет по NER, по первому токену перевода и на закрытии читалки."""
    text = (TOOLS / "NovelMaestro_Lite" / "novelmaestro-lite.user.js").read_text(encoding="utf-8")
    assert "if (config.ghostPreview && !cachedNow) ghostShow(text)" in text, \
        "призрак поднимается и на главе, которая уже лежит в кэше"
    assert "const nerFresh = config.autoNER && !isNerDoneForPage(current.key)" in text, \
        "жизнь призрака не связана с тем, идёт ли извлечение терминов"
    assert "if (nerFresh) ghostHide()" in text, "призрак не гаснет по извлечению терминов"
    assert "onFirstDelta: () => ghostHide()" in text, "призраку не передан хук первого токена"
    assert "if (!job.onFirstDelta) ghostHide(false)" in text, \
        "страховка перевода гасит призрак, поставленный этим же прогоном"
    assert "ghostHide(false);" in text.split("function closeReader()")[1].split("function updateNavButtons")[0], \
        "closeReader не зачищает призрак"
    assert "readerContent.classList.remove('nm-ghost', 'nm-ghost-fade')" in text, \
        "классы призрака остаются на контенте читалки"
    # пульс вспышек: chunkDone — индекс с нуля, проверка на число, а не на истинность
    assert "if (Number.isInteger(st.chunkDone))" in text and "highlightGhostChunk(st.chunkDone)" in text, \
        "вспышки первого чанка пропускаются (или фон уезжает на чанк назад)"



# ── призрак оригинала: тайминг в основном прогоне читалки ─────────────────────
# runTranslationFlow в node не исполняется (DOM), но порядок вызовов в его теле и
# есть контракт: где призрак включается, где гаснет и что уходит в перевод
LITE_ARTIFACT = (TOOLS / "NovelMaestro_Lite" / "novelmaestro-lite.user.js").read_text(encoding="utf-8")


def body(name: str, nxt: str) -> str:
    """Тело функции артефакта от заголовка до следующей именованной функции."""
    text = LITE_ARTIFACT
    start = text.index(f"async function {name}") if f"async function {name}" in text else text.index(f"function {name}")
    end = text.index(f"function {nxt}", start)
    return text[start:end]


def test_ghost_shows_only_on_the_live_page_branch():
    """Кэш-ветка призрака не показывает: там уже есть готовый перевод."""
    flow = body("runTranslationFlow(", "handleExtractTerms")
    cache_branch = flow.index("if (cached && cached.text) {")
    live_branch = flow.index("} else {", cache_branch)
    ghost_show = flow.index("ghostShow(text)")
    assert cache_branch < live_branch < ghost_show, "призрак включается вне живой ветки главы"


def test_ghost_goes_before_ner_and_by_first_token_after_it():
    """NER выполняется — призрак гаснет по нему; NER пропущен — до первого токена."""
    flow = body("runTranslationFlow(", "handleExtractTerms")
    ghost_show = flow.index("ghostShow(text)")
    ner = flow.index("if (config.autoNER) {", ghost_show)
    assert ghost_show < ner, "призрак показывается не до извлечения терминов"
    assert "const nerFresh = config.autoNER && !isNerDoneForPage(current.key);" in flow, \
        "условие жизни призрака не связано с извлечением терминов"
    hide = flow.index("if (nerFresh) ghostHide();")
    assert ner < hide, "призрак не гаснет после извлечения терминов"
    title_show = flow.index("progressShow(title);", hide)
    assert hide < title_show, "призрак гаснет после запуска перевода, а не перед ним"
    assert "onFirstDelta: () => ghostHide()" in flow, "призраку не передан хук первого токена"
    assert "bookKey: current.key" in flow, "задание перевода потеряло книгу"
    for notice in ("⏹ Перевод остановлен • Меню ⋮", "⏹ Перевод остановлен • ⋮", "⏳ Перевод не завершён • ⋮"):
        at = flow.index(notice)
        assert flow.rindex("ghostHide(false);", 0, at) > 0, f"перед «{notice}» призрак не убран"


def test_ghost_is_read_only_and_leaves_no_traces():
    """Призрак ничего не пишет в глоссарий, книги и задания — только показывает текст."""
    ghost = body("ghostShow(", "ghostAbort")
    for forbidden in ("bookGlossary", "dbPut", "jobPut", "GM_setValue", "cacheSet", "count"):
        assert forbidden not in ghost, f"призрак пишет в состояние прогона через {forbidden}"


def test_reader_leaves_no_ghost_leftovers():
    """closeReader и повторный перевод убирают призрак: классов nm-ghost* не остаётся."""
    close = body("closeReader(", "updateNavButtons")
    assert "ghostHide(false);" in close, "closeReader не зачищает призрак"
    translate = body("translateWithStreaming(", "translateTextBackground")
    assert "ghostHide(false);" in translate, "перевод не начинается с зачистки чужого призрака"
    assert "readerContent.classList.remove('nm-ghost', 'nm-ghost-fade')" in LITE_ARTIFACT, \
        "классы призрака остаются на контенте читалки"


@pytest.mark.parametrize("script,artifact", sorted(SCRIPTS.items()))
def test_update_urls_point_at_the_repo_path(script, artifact):
    """@downloadURL/@updateURL дописаны сборщиком и ведут на raw по пути файла."""
    banner = banner_of(TOOLS / script / artifact)
    assert B.RAW_BASE == RAW + REPO_SLUG + "/main", \
        f"ссылка обновлений ведёт не в ветку репо: {B.RAW_BASE}"
    meta_name = artifact[: -len(".user.js")] + ".meta.js"
    for key, tail in (("@downloadURL", artifact), ("@updateURL", meta_name)):
        m = re.search(rf"^//[ \t]*{key}[ \t]+(\S+)", banner, re.MULTILINE)
        assert m, f"{artifact}: нет {key}"
        url = m.group(1)
        assert url.startswith(B.RAW_BASE + "/"), f"{artifact}: {key} ведёт не на raw: {url}"
        assert url.endswith(f"/tools/{script}/{tail}"), \
            f"{artifact}: {key} разошёлся с путём файла: {url}"
    # ключи дописывает сборщик — в рукописном баннере им не место
    src_meta = B.read_lf(TOOLS / script / "meta.js")
    assert "@downloadURL" not in src_meta and "@updateURL" not in src_meta, \
        f"{script}/meta.js: ссылки обновлений должны приходить из сборки"


@pytest.mark.parametrize("script,artifact", sorted(SCRIPTS.items()))
def test_connect_block_declares_common_hosts(script, artifact):
    """@connect перечисляет типовые хосты: менеджер не должен выставлять диалог доступа.

    На мобильном Firefox диалог разрешения может вообще не показываться — запрос просто
    висит до таймаута, поэтому частые провайдеры выписаны явно, а `*` оставлен последним
    ради кнопки «Всегда разрешать для всех доменов»."""
    banner = banner_of(TOOLS / script / artifact)
    values = re.findall(r"^//[ \t]*@connect[ \t]+(\S+)", banner, re.MULTILINE)
    assert values, f"{artifact}: блок @connect пуст"
    assert len(values) == len(set(values)), f"{artifact}: в @connect дубли: {values}"
    assert values[-1] == "*", f"{artifact}: @connect * обязан быть последним: {values}"
    for value in values:
        assert value in CONNECT_SPECIALS or CONNECT_HOST_RE.fullmatch(value), \
            f"{artifact}: @connect {value!r} — не домен, не self и не localhost"
    for want in (*CONNECT_COMMON, *CONNECT_EXPECT.get(script, ())):
        assert want in values, f"{artifact}: в @connect нет {want!r}"


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


@pytest.mark.parametrize("script,artifact", sorted(SCRIPTS.items()))
def test_meta_file_is_metadata_only(script, artifact):
    """Спутник .meta.js: тот же баннер, та же версия и ни строки кода — именно его
    опрашивает менеджер, поэтому он обязан оставаться крошечным."""
    meta_name = artifact[: -len(".user.js")] + ".meta.js"
    meta_file = TOOLS / script / meta_name
    assert meta_file.is_file(), f"{meta_name}: файл проверки обновлений не собран"
    body = meta_file.read_text(encoding="utf-8")
    assert banner_of(meta_file) == banner_of(TOOLS / script / artifact), \
        f"{meta_name}: блок метаданных разошёлся с артефактом"
    version = B.script_version(B.read_lf(TOOLS / script / "meta.js"))
    assert body.count("@version") == 1 and version in body, f"{meta_name}: версия разошлась"
    tail = body[body.find("// ==/UserScript==") + len("// ==/UserScript=="):].strip()
    for line in tail.splitlines():
        line = line.strip()
        assert not line or line.startswith("//"), f"{meta_name}: в файле появился код: {line}"
    assert len(body.splitlines()) < 90, f"{meta_name}: разросся — это файл проверки обновлений"


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_no_crlf_anywhere(script):
    """Репо LF: CRLF сборщик нормализует, но в git ему там быть не должно."""
    paths = [TOOLS / script / SCRIPTS[script], TOOLS / script / "meta.js",
             *sorted((TOOLS / script).glob("*.meta.js")),
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
    # у .meta.js своя жизнь — рассинхрон тоже должен ловиться
    (dest / artifact).write_text(next(x for x in (TOOLS / script).glob("*.user.js")).read_text(encoding="utf-8"), encoding="utf-8")
    (dest / (artifact[: -len(".user.js")] + ".meta.js")).write_text("", encoding="utf-8")
    stale_meta = run_builder_in(tmp_path, "--script", script, "--check")
    assert stale_meta.returncode != 0, "пустой .meta.js не был распознан как устаревший"

    body = next(p for p in sorted((dest / "src").glob("*.js")) if p.name != B.OPEN_PART)
    body.write_text(B.read_lf(body) + "\n// правка части без пересборки\n", encoding="utf-8")
    built = run_builder_in(tmp_path, "--script", script)
    assert built.returncode == 0, built.stderr
    fresh = run_builder_in(tmp_path, "--script", script, "--check")
    assert fresh.returncode == 0, f"свежий артефакт помечен устаревшим: {fresh.stderr}"
    assert "правка части без пересборки" in Path(dest / artifact).read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node не установлен")
def test_lite_logic_node_tests():
    """node --test по tests/tools/*.test.mjs: чистая логика Lite без DOM и GM_*.

    Части юзерскрипта — один IIFE, целиком в node они не исполняются, поэтому
    тест вырезает подопытный блок из артефакта по маркерам и исполняет его как
    чистую функцию на заглушках."""
    res = subprocess.run([shutil.which("node"), "--test",
                          str(ROOT / "tests" / "tools" / "*.test.mjs")],
                         capture_output=True, text=True, cwd=str(ROOT))
    assert res.returncode == 0, f"node --test упал (rc={res.returncode}):\n{res.stdout}\n{res.stderr}"
    assert "# fail 0" in res.stdout, f"node --test: есть падающие проверки:\n{res.stdout}"


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

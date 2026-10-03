#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты реестра настроек `core/settings.py` — одного места истины.

Здесь три вещи: целостность реестра (ключи, имена, типы), совпадение его
метаданных и дефолтов с тем, что сегодня описано в `web/stages.py` (иначе
реестр становится вторым источником, а не первым), и чтение/запись общего
.env (слои, санитайзер, удаление пустого файла).
"""
from __future__ import annotations

import pytest

from core import settings as S

# то, что осталось параметрами запуска (не настройки): сверяем полноту
RUN_PARAMS = {
    "start", "end", "input", "file", "action", "replacements", "dict_file",
    "rules_file", "examples_file", "cover", "epub_meta", "donate_file",
    "prompt_file",
}
# старые имена LLM-полей стадий, которые свёрнуты в одну общую настройку
ALIAS = {"retries": "max_retries", "jobs": "threads"}
# поле выбора профиля LLM — не настройка реестра: состояние браузера стадии
PROFILE = "profile"
LLM_FIELDS = {"host", "model", "api_key", "temperature", "timeout",
              "stream_timeout", "max_retries", "retries", "max_tokens",
              "threads", "jobs", "retry_empty"}
# общие LLM-настройки: стадийных ключей больше нет, имена унифицированы
LLM_NAMES = {
    "host", "api_key", "model", "timeout", "stream_timeout", "max_retries",
    "max_tokens", "retry_empty", "temperature", "threads", "min_len_ratio",
    "reasoning_mode", "thinking_profile", "reasoning_effort", "thinking_budget",
    "llm_extra_body_json",
}


def norm(field_type, value) -> str:
    """Значение в форме сравнения: bool → «True»/«False», прочее — строка."""
    if field_type == "bool":
        if isinstance(value, bool):
            return str(value)
        return str(str(value or "").strip().lower() in ("1", "true", "yes", "on"))
    return str(value if value is not None else "")


# ════════════════════════════════════════════════════════════════════
# целостность реестра
# ════════════════════════════════════════════════════════════════════

def test_groups_shape():
    """Субвкладки и блоки: непустые, id уникальны, стадия владельец есть.

    Отдельной субвкладки «Веб-сервер» нет: настройки своего сервера — те же
    настройки машины, что и модель, и живут последними блоками первой
    субвкладки.
    """
    assert [g.id for g in S.groups()] == [
        "llm", "transfer", "glossary", "checks", "book"]
    ids = [b.id for g in S.groups() for b in g.blocks]
    assert len(ids) == len(set(ids)), "id блоков повторяются"
    assert [b.id for b in S.groups()[0].blocks][-2:] == ["server_net", "server_run"]
    for g in S.groups():
        assert g.title and g.blocks
        for b in g.blocks:
            assert b.title and b.settings


def test_payload_marks_llm_blocks():
    """Профиль LLM перекрывает поля только LLM-блоков, а не весь экран: у
    блока стоит флаг, по нему SPA и подставляет значения профиля."""
    payload = S.groups_payload()
    llm = {b["id"]: b.get("llm") for g in payload for b in g["blocks"]}
    assert set(b for b in llm if llm[b]) == set(S.LLM_BLOCKS)
    assert llm["server_net"] is False and llm["server_run"] is False


def test_web_values_layers():
    """web_values(): реестр → общий .env → окружение (тот же порядок, что и у
    остальных настроек); ключи — имена полей, числа и булевы — типизированы."""
    vals = S.web_values()
    assert list(vals) == [s.name for s in S.web_settings()]
    assert vals["web_host"] == "127.0.0.1" and vals["web_port"] == 8756
    assert vals["web_auth"] is False and vals["web_projects_dir"] == ""


def test_keys_unique():
    keys = [s.key for s in S.SETTINGS]
    assert len(keys) == len(set(keys)), "дубль ключа в реестре"


@pytest.mark.parametrize("key", sorted(s.key for s in S.SETTINGS))
def test_setting_key_matches_owner(key):
    """Ключ стадийной настройки — с префиксом стадии, общей — без него."""
    s = S.BY_KEY[key]
    if s.stage:
        assert key.startswith(f"{s.stage.upper()}_")
        assert s.key == f"{s.stage.upper()}_{s.name.upper()}"
    else:
        assert s.key == s.name.upper()


@pytest.mark.parametrize("kind", sorted({s.type for s in S.SETTINGS}))
def test_types_are_known(kind):
    assert kind in ("text", "number", "bool", "select", "textarea", "files",
                    "password", "hidden"), f"неизвестный тип поля {kind}"


def test_selects_have_options():
    for s in S.SETTINGS:
        if s.type == "select":
            assert s.options, f"{s.key}: select без вариантов"


def test_noenv_fields_are_hidden():
    """Чипсы (типы/поля) — состояние UI: скрытый тип и в .env не пишется."""
    assert {s.key for s in S.SETTINGS if s.noenv} == {
        "PIPELINE_NER_FIELDS", "NER_CHECK_TYPES", "NER_CHECK_FIELDS",
        "WIKI_TYPES"}
    for s in S.SETTINGS:
        if s.noenv:
            assert s.type == "hidden", f"{s.key}: noenv без type=hidden"


def test_llm_block_is_global():
    """LLM-настройки — общие: одна модель, одна параллельность, ретраи."""
    assert sorted(S.llm_values()) == sorted(LLM_NAMES)
    for name in LLM_NAMES:
        s = next(x for x in S.SETTINGS if x.name == name and not x.stage)
        assert s.stage == "", f"{name}: у LLM-настройки появился стадийный владелец"


def test_threads_single_value():
    """Потоки — одна величина на конвейер (была: 1 у ner_check, 4 у других)."""
    assert S.BY_KEY["THREADS"].default == "4"
    assert "threads" not in S.defaults("ner_check")


def test_min_len_ratio_off_by_default():
    assert S.BY_KEY["MIN_LEN_RATIO"].default == "0"
    assert float(S.defaults()["min_len_ratio"]) == 0.0


def test_stage_names_are_the_registry_owners():
    assert set(S.STAGES) == {
        "epub", "ner", "ner_check", "pipeline", "translate_check",
        "translate_check_llm", "translate_quality", "wiki", "compile",
        "batch_replace"}


# ════════════════════════════════════════════════════════════════════
# реестр vs спеки стадий: один источник, а не второй
# ════════════════════════════════════════════════════════════════════

# Настройки, которые живут только в реестре: в форме стадии их нет, они
# правятся на странице «Настройки» (у конвейера это порог и n-граммы поиска
# терминов — раньше их можно было задать только руками в .env).
REGISTRY_ONLY = {"pipeline": (9, ["ner_threshold", "ner_ngram"])}


@pytest.mark.parametrize("stage", sorted(S.STAGES))
def test_registry_matches_stage_specs(stage):
    """Поля стадии в реестре равны тому, что описано в спеке."""
    from web.stages import STAGE_SPECS

    spec = STAGE_SPECS[stage]
    spec_names = [f["name"] for f in (spec.get("fields") or [])
                  if f["name"] not in LLM_FIELDS and f["name"] != PROFILE]
    at, extra = REGISTRY_ONLY.get(stage, (len(spec_names), []))
    expected = spec_names[:at] + extra + spec_names[at:]
    assert [s.name for s in S.stage_fields(stage)] == expected, \
        f"{stage}: порядок полей формы разошёлся с реестром"
    reg = {s.name: s for s in S.stage_fields(stage)}
    checked = 0
    for f in spec.get("fields") or []:
        name = f["name"]
        if name in LLM_FIELDS or name == PROFILE:
            continue
        got = reg.get(name)
        assert got is not None, f"{stage}.{name}: поля нет в реестре"
        checked += 1
        for k in ("label", "type", "min", "max", "step", "dir"):
            if k in f:
                assert str(f[k]) == str(getattr(got, k, "")), \
                    f"{stage}.{name}.{k}: спека {f[k]!r} ≠ реестр"
        assert tuple(f.get("options") or ()) == tuple(got.options or ()), \
            f"{stage}.{name}.options"
        assert {str(k): str(v) for k, v in (f.get("labels") or {}).items()} == \
            {str(k): str(v) for k, v in got.labels.items()}, \
            f"{stage}.{name}.labels"
        assert norm(f.get("type"), f.get("default")) == \
            norm(got.type, got.default), \
            f"{stage}.{name}: дефолт спеки {f.get('default')!r} ≠ реестр"
    assert checked + len(extra) == len(reg), (
        f"{stage}: в реестре {len(reg)} полей, в спеке сверено {checked} "
        f"(+{len(extra)} реестровых)")


def test_profile_field_is_first_on_llm_stages():
    """Профиль LLM — первое поле формы LLM-стадии (не настройка реестра)."""
    for stage in sorted(S.STAGE_LLM_FIELDS):
        if not S.STAGE_LLM_FIELDS[stage]:
            continue
        fields = S.form_fields(stage)
        assert fields[0]["name"] == PROFILE, f"{stage}: профиль не первое поле"
        f = fields[0]
        assert f["type"] == "select" and f["noenv"] is True
        assert f["default"] == S.PROFILE_DEFAULT, f"{stage}: профиль не General"
        assert f["options"] and f["options"][0] == S.PROFILE_DEFAULT
        assert set(f["labels"]) == set(f["options"]), f"{stage}: подписи профилей"
        assert f["help"], f"{stage}: у поля профиля нет подсказки"


def test_profile_field_absent_on_plain_stages():
    """У стадий без модели поля профиля нет (эпуб/проверка/компиляция/замены)."""
    for stage, fields in S.STAGE_LLM_FIELDS.items():
        if fields:
            continue
        names = {f["name"] for f in S.form_fields(stage)}
        assert PROFILE not in names, f"{stage}: поле профиля не LLM-стадии"


def test_profile_field_tracks_profiles_file(global_env):
    """Варианты и подписи поля — текущие профили: General + созданные."""
    S.profile_create("Домашний", {"MODEL": "gemma/дом"})
    S.profile_create("Облако", {"MODEL": "gemma/облако"})
    f = S.form_fields("ner")[0]
    assert f["options"] == ("general", "p1", "p2")
    assert f["labels"] == {"general": "General", "p1": "Домашний",
                           "p2": "Облако"}
    # General — всегда первый: без выбора стадия идёт на общий конфиг
    assert f["options"].index(S.PROFILE_DEFAULT) == 0


def test_with_llm_takes_profile_from_form(global_env):
    """with_llm разбирает профиль по полям стадии: свой сервер у стадии — это профиль."""
    S.profile_create("Домашний", {
        "HOST": "http://дом:9989", "MODEL": "дом-модель", "THREADS": "9"})
    form = {"start": "1", "end": "5", "profile": "p1"}
    out = S.with_llm("ner", form)
    assert out["host"] == "http://дом:9989" and out["model"] == "дом-модель"
    assert out["threads"] == 9 and out["start"] == "1"
    # без поля — General (значения общего конфига)
    plain = S.with_llm("ner", {"start": "1"})
    assert plain["model"] == S.effective("MODEL")


def test_run_params_marked_and_never_written(global_env):
    """Главы и входные файлы — параметры запуска: metadata в реестре, но не конфиг."""
    assert {s.name for s in S.SETTINGS if s.run} == RUN_PARAMS
    assert not RUN_PARAMS & set(S.defaults())
    assert {"start", "cover", "donate_file"} <= {s.name for s in
                                                S.stage_fields("compile")}
    S.write_values({"COMPILE_MODE": "epub", "COMPILE_START": "1"})
    text = global_env.read_text(encoding="utf-8")
    assert "COMPILE_MODE=epub" in text
    assert "COMPILE_START" not in text, "параметр запуска — не настройка"


def test_form_field_shape():
    field = S.BY_KEY["NER_CHUNK_SIZE"].form_field()
    assert field["name"] == "chunk_size"
    assert field["type"] == "number"
    assert field["default"] == "5500"
    assert field["label"].startswith("Размер чанка")
    secret = S.BY_KEY["API_KEY"].form_field()
    assert secret["secret"] is True and secret["type"] == "password"


# ════════════════════════════════════════════════════════════════════
# чтение слоёв
# ════════════════════════════════════════════════════════════════════

def test_effective_falls_back_to_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.delenv("NER_CHUNK_SIZE", raising=False)
    assert S.effective("NER_CHUNK_SIZE") == 5500
    assert S.file_values() == {}


def test_effective_file_over_registry(monkeypatch, tmp_path):
    env = tmp_path / "shared.env"
    env.write_text("NER_CHUNK_SIZE=1500\n", encoding="utf-8")
    monkeypatch.setenv("WEB_ENV_FILE", str(env))
    monkeypatch.delenv("NER_CHUNK_SIZE", raising=False)
    assert S.effective("NER_CHUNK_SIZE") == 1500
    assert S.stage_values("ner")["chunk_size"] == 1500


def test_effective_env_over_file(monkeypatch, tmp_path):
    env = tmp_path / "shared.env"
    env.write_text("NER_CHUNK_SIZE=1500\n", encoding="utf-8")
    monkeypatch.setenv("WEB_ENV_FILE", str(env))
    monkeypatch.setenv("NER_CHUNK_SIZE", "2000")
    assert S.effective("NER_CHUNK_SIZE") == 2000


def test_empty_file_value_does_not_shadow(monkeypatch, tmp_path):
    """Пустое значение в общем файле — не значение: остаётся реестр."""
    env = tmp_path / "shared.env"
    env.write_text("NER_CHUNK_SIZE=\n", encoding="utf-8")
    monkeypatch.setenv("WEB_ENV_FILE", str(env))
    monkeypatch.delenv("NER_CHUNK_SIZE", raising=False)
    assert S.effective("NER_CHUNK_SIZE") == 5500


@pytest.mark.parametrize("raw,want", [("1", True), ("true", True), ("ON", True),
                                      ("0", False), ("off", False)])
def test_bool_reading(monkeypatch, tmp_path, raw, want):
    env = tmp_path / "shared.env"
    env.write_text(f"COMPILE_MODE=txt\nNER_CHECK_TYPES=\nWIKI_TOC={raw}\n",
                   encoding="utf-8")
    monkeypatch.setenv("WEB_ENV_FILE", str(env))
    monkeypatch.delenv("WIKI_TOC", raising=False)
    assert S.effective("WIKI_TOC") is want


def test_textarea_newlines_are_literals_in_file(monkeypatch, tmp_path):
    """В .env многострочное — одной строкой с литералом «\\n»."""
    env = tmp_path / "shared.env"
    lit = chr(92) + "n"          # литерал «\n» в одной строке файла
    env.write_text(f"TRANSLATE_CHECK_REGEXP_CHECKS=a{lit}b{lit}\n",
                   encoding="utf-8")
    monkeypatch.setenv("WEB_ENV_FILE", str(env))
    monkeypatch.delenv("TRANSLATE_CHECK_REGEXP_CHECKS", raising=False)
    assert S.effective("TRANSLATE_CHECK_REGEXP_CHECKS") == "a\nb"


# ════════════════════════════════════════════════════════════════════
# запись общего .env
# ════════════════════════════════════════════════════════════════════

@pytest.fixture()
def global_env(monkeypatch, tmp_path):
    """Общий .env во временной папке: писать test-данные в репо нельзя."""
    path = tmp_path / "shared.env"
    monkeypatch.setenv("WEB_ENV_FILE", str(path))
    return path


def test_write_values_keeps_registry_order(global_env):
    S.write_values({"NER_CHUNK_SIZE": "1500", "MODEL": "m/1",
                    "HOST": "http://x/v1"})
    lines = global_env.read_text(encoding="utf-8").splitlines()
    keys = [l.split("=", 1)[0] for l in lines if "=" in l and not l.startswith("#")]
    assert keys == ["HOST", "MODEL", "NER_CHUNK_SIZE"], keys
    assert lines[0].startswith("#")


def test_write_values_empty_removes_file(global_env):
    S.write_values({"MODEL": "m/1"})
    assert global_env.is_file()
    S.write_values({"MODEL": ""})
    assert not global_env.is_file(), "файл без отличий должен быть удалён"


@pytest.mark.parametrize("field,value,want", [
    ("MODEL", "  m/1  ", "m/1"),
    ("MODEL", "a # b", '"a # b"'),
    ("TRANSLATE_CHECK_REGEXP_CHECKS", "a\nb", "a\\nb"),

])
def test_sanitize(global_env, field, value, want):
    assert S.sanitize(S.BY_KEY[field], value) == want


def test_write_skips_noenv_keeps_zero(global_env):
    S.write_values({"WIKI_TOC": True, "WIKI_TOC_LINKS": "",
                    "WIKI_TYPES": ["person"]})
    text = global_env.read_text(encoding="utf-8")
    assert "WIKI_TOC=1" in text
    assert "WIKI_TOC_LINKS" not in text, "пустое значение не пишется"
    assert "WIKI_TYPES" not in text, "чипсы — состояние UI, не конфиг"


def test_groups_payload_masks_secrets(monkeypatch, global_env):
    global_env.write_text("API_KEY=secret-token\nWIKI_TOC=1\n", encoding="utf-8")
    monkeypatch.delenv("API_KEY", raising=False)
    payload = {f["name"]: f for g in S.groups_payload()
               for b in g["blocks"] for f in b["fields"]}
    assert payload["api_key"]["value"] == "••••"
    assert "secret-token" not in repr(payload)
    # модель не задана в файле → встроенный дефолт реестра, а не пустая строка
    assert payload["model"]["value"] == S.BY_KEY["MODEL"].default


# ════════════════════════════════════════════════════════════════════
# профили LLM: файл рядом с общим .env, значения — только переопределения
# ════════════════════════════════════════════════════════════════════


def test_profiles_file_sits_next_to_env(global_env):
    """Файл профилей — сосед общего .env: один каталог, один постоянный том."""
    assert S.profiles_file() == str(global_env.parent / S.PROFILES_NAME)
    assert not S.profiles_file().endswith(".env")


def test_profiles_general_first_and_builtin(global_env):
    """General — всегда первый и встроенный: его значения и есть общий .env."""
    profs = S.profiles()
    assert profs[0]["id"] == S.PROFILE_DEFAULT
    assert profs[0]["name"] == S.PROFILE_DEFAULT_TITLE and profs[0]["builtin"]
    empty = S.profile_get("")
    assert empty is not None and empty["builtin"] is True
    assert S.profile_get("нет-такого") is None


def test_profile_file_format(global_env):
    """Файл профилей — JSON с англоязычными ключами (entries/values/created),
    значения — ключи реестра; порядок списка = порядок в интерфейсе."""
    S.profile_create("Первый", {"HOST": "http://a:1"})
    S.profile_create("Второй", {"MODEL": "b"})
    import json as _json
    data = _json.loads(global_env.parent.joinpath(S.PROFILES_NAME)
                       .read_text(encoding="utf-8"))
    assert [p["id"] for p in data["profiles"]] == ["p1", "p2"]
    assert set(data["profiles"][0]) == {"id", "name", "values", "created",
                                        "updated"}
    assert data["profiles"][1]["values"] == {"MODEL": "b"}


def test_profiles_file_dies_with_last_profile(global_env):
    """Последний удалённый профиль убирает файл: пустой llm_profiles.json —
    не состояние, а мусор (тот же закон, что у пустого общего .env)."""
    prof = S.profile_create("Один")
    assert global_env.parent.joinpath(S.PROFILES_NAME).is_file()
    assert S.profile_delete(prof["id"]) is True
    assert not global_env.parent.joinpath(S.PROFILES_NAME).exists()
    assert S.profile_delete(prof["id"]) is False
    assert [p["id"] for p in S.profiles()] == [S.PROFILE_DEFAULT]


def test_profiles_payload_values(global_env):
    """Payload профилей: у General — эффективные значения конфига, у прочих —
    только их переопределения; секрет отдаётся маской."""
    S.write_values({"HOST": "http://общий:1", "API_KEY": "ключ-general"})
    S.profile_create("Домашний", {"MODEL": "дом-модель", "API_KEY": "ключ-дом"})
    got = {p["id"]: p for p in S.profiles_payload()}
    assert set(got) == {S.PROFILE_DEFAULT, "p1"}
    assert got[S.PROFILE_DEFAULT]["values"]["HOST"] == "http://общий:1"
    assert got["p1"]["values"]["HOST"] == ""  # не задан → наследует General
    assert got["p1"]["values"]["MODEL"] == "дом-модель"
    assert got["p1"]["values"]["API_KEY"] == "••••"
    assert "ключ-дом" not in repr(got) and "ключ-general" not in repr(got)

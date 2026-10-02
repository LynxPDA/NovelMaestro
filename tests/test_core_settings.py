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
    """Субвкладки и блоки: непустые, id уникальны, стадия владельец есть."""
    assert [g.id for g in S.groups()] == [
        "llm", "transfer", "glossary", "checks", "book", "server"]
    ids = [b.id for g in S.groups() for b in g.blocks]
    assert len(ids) == len(set(ids)), "id блоков повторяются"
    for g in S.groups():
        assert g.title and g.blocks
        for b in g.blocks:
            assert b.title and b.settings


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

@pytest.mark.parametrize("stage", sorted(S.STAGES))
def test_registry_matches_stage_specs(stage):
    """Поля стадии в реестре равны тому, что описано в спеке."""
    from web.stages import STAGE_SPECS

    spec = STAGE_SPECS[stage]
    spec_names = [f["name"] for f in (spec.get("fields") or [])
                  if f["name"] not in LLM_FIELDS]
    assert [s.name for s in S.stage_fields(stage)] == spec_names, \
        f"{stage}: порядок полей формы разошёлся с реестром"
    reg = {s.name: s for s in S.stage_fields(stage)}
    checked = 0
    for f in spec.get("fields") or []:
        name = f["name"]
        if name in LLM_FIELDS:
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
    assert checked == len(reg), (
        f"{stage}: в реестре {len(reg)} полей, в спеке сверено {checked}")


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
    assert S.effective("NER_CHUNK_SIZE") == "5500"
    assert S.file_values() == {}


def test_effective_file_over_registry(monkeypatch, tmp_path):
    env = tmp_path / "shared.env"
    env.write_text("NER_CHUNK_SIZE=1500\n", encoding="utf-8")
    monkeypatch.setenv("WEB_ENV_FILE", str(env))
    monkeypatch.delenv("NER_CHUNK_SIZE", raising=False)
    assert S.effective("NER_CHUNK_SIZE") == "1500"
    assert S.values_of_stage("ner")["chunk_size"] == "1500"


def test_effective_env_over_file(monkeypatch, tmp_path):
    env = tmp_path / "shared.env"
    env.write_text("NER_CHUNK_SIZE=1500\n", encoding="utf-8")
    monkeypatch.setenv("WEB_ENV_FILE", str(env))
    monkeypatch.setenv("NER_CHUNK_SIZE", "2000")
    assert S.effective("NER_CHUNK_SIZE") == "2000"


def test_empty_file_value_does_not_shadow(monkeypatch, tmp_path):
    """Пустое значение в общем файле — не значение: остаётся реестр."""
    env = tmp_path / "shared.env"
    env.write_text("NER_CHUNK_SIZE=\n", encoding="utf-8")
    monkeypatch.setenv("WEB_ENV_FILE", str(env))
    monkeypatch.delenv("NER_CHUNK_SIZE", raising=False)
    assert S.effective("NER_CHUNK_SIZE") == "5500"


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
    assert payload["model"]["value"] == ""

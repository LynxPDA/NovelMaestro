#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты core/stage.py — общего слоя стадий конвейера.

Проверяем то, ради чего слой и существовал: имена флагов (контракт с
web/stages.py), порядок источников сервера (CLI > os.environ > .env),
нормализация /v1, один вызов стрима на запрос стадии, предпросмотр вместо
сети и прогресс в двух режимах. Сеть не трогается: stream_chat_completion
подменяется записывателем.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "cli"):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from core import common as C  # noqa: E402
from core import settings as cfg  # noqa: E402
from core import stage as st  # noqa: E402

CANONICAL = ["--host", "--model", "--api_key", "--env_file", "--temperature",
             "--reasoning_effort", "--timeout", "--max_retries"]

#: настоящий логгер: аннотации слоя просят именно logging.Logger
LOG = logging.getLogger("test.core.stage")


def parser_with(**kw) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stage")
    st.add_llm_args(p, **kw)
    return p


@pytest.fixture()
def env_file(tmp_path) -> str:
    """Общий .env: сервер, ключ и модель конвейера."""
    p = tmp_path / "fake.env"
    p.write_text("HOST=http://common:1/v1\nAPI_KEY=common-key\n"
                 "MODEL=common-model\n",
                 encoding="utf-8")
    return str(p)


def resolve(env_file, *, stage="", **overrides):
    args = parser_with(aliases=True).parse_args(["--env_file", env_file])
    for key, value in overrides.items():
        setattr(args, key, value)
    return st.resolve_profile(args, stage=stage, logger=LOG)


# ══════════════════════════════════════════════════════════════════════
# Флаги: имена — контракт форм web
# ══════════════════════════════════════════════════════════════════════
def test_add_llm_args_canonical_names():
    p = parser_with()
    opts = {o for a in p._actions for o in a.option_strings}  # noqa: SLF001
    for flag in CANONICAL:
        assert flag in opts, f"контрактный флаг {flag} пропал из блока"
    # LLM-конфиг один на конвейер: у всех стадий одинаковый блок флагов,
    # включая --stream_timeout и --max_tokens (значения — из общего конфига)
    for flag in ("--stream_timeout", "--max_tokens"):
        assert flag in opts, f"общий флаг {flag} пропал из блока"
    # единицы в help (AGENTS §5): таймаут обязан быть подписан секундами
    assert "сек" in " ".join(a.help or "" for a in p._actions)  # noqa: SLF001


@pytest.mark.parametrize("alias,dest,value", [
    ("--retries", "max_retries", "5"),
    ("--api-key", "api_key", "k"),
    ("--env-file", "env_file", "custom.env"),
    ("--reasoning-effort", "reasoning_effort", "low"),
])
def test_legacy_aliases_map_to_canonical_dest(alias, dest, value):
    got = vars(parser_with(aliases=True).parse_args([alias, value]))
    assert str(got[dest]) == value


def test_aliases_off_by_default():
    with pytest.raises(SystemExit):
        parser_with().parse_args(["--retries", "5"])


def test_llm_flags_default_to_none():
    """Своих LLM-дефолтов у стадии нет: None = «взять из общего конфига»."""
    got = vars(parser_with().parse_args([]))
    for dest in ("timeout", "stream_timeout", "max_retries", "max_tokens",
                 "temperature"):
        assert got[dest] is None, f"{dest} должен быть None (не свой дефолт)"


# ══════════════════════════════════════════════════════════════════════
# Профиль: CLI > os.environ > .env
# ══════════════════════════════════════════════════════════════════════
def test_env_file_is_default_source(tmp_path, env_file):
    prof = resolve(env_file)
    assert prof.base_url == "http://common:1/v1"
    assert prof.api_key == "common-key"
    assert prof.model == "common-model"


def test_stage_prefixed_llm_keys_are_ignored(tmp_path, env_file, monkeypatch):
    """<СТАДИЯ>_HOST/MODEL/API_KEY больше не слой: сервер один на конвейер.

    Ключ в файле остаётся (его мог оставить старый .env книги) — но на
    разрешение конфигурации он не влияет.
    """
    monkeypatch.setenv("NER_CHECK_HOST", "http://stage:2")
    monkeypatch.setenv("NER_CHECK_MODEL", "stage-model")
    prof = resolve(env_file, stage="ner_check")
    assert prof.base_url == "http://common:1/v1"
    assert prof.model == "common-model"


def test_cli_wins_over_everything(tmp_path, env_file, monkeypatch):
    monkeypatch.setenv("HOST", "http://from-env:4")
    prof = resolve(env_file, stage="ner_check", host="http://cli:3",
                   model="cli-model", api_key="cli-key", temperature=0.2,
                   reasoning_effort="high", timeout=11, stream_timeout=22,
                   max_retries=1)
    assert (prof.base_url, prof.model, prof.api_key) == (
        "http://cli:3/v1", "cli-model", "cli-key")
    assert (prof.timeout, prof.stream_timeout) == (11, 22)
    assert prof.temperature == 0.2 and prof.reasoning_effort == "high"
    assert prof.max_retries == 1


def test_env_var_wins_over_file(tmp_path, env_file, monkeypatch):
    """Канон §7: переменная окружения перекрывает ключ из файла."""
    monkeypatch.setenv("HOST", "http://from-env:4")
    assert resolve(env_file).base_url == "http://from-env:4/v1"


@pytest.mark.parametrize("host,want", [
    ("http://h:1", "http://h:1/v1"),
    ("http://h:1/", "http://h:1/v1"),
    ("http://h:1/v1", "http://h:1/v1"),
    ("http://h:1/v1/chat", "http://h:1/v1/chat"),
], ids=["plain", "trailing-slash", "with-v1", "long-path"])
def test_v1_normalization(host, want):
    args = parser_with().parse_args(["--host", host, "--model", "m"])
    assert st.resolve_profile(args, logger=LOG).base_url == want


def test_missing_server_exits_with_hint(tmp_path, capsys):
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    args = parser_with().parse_args(["--env_file", str(empty)])
    with pytest.raises(SystemExit) as got:
        st.resolve_profile(args, logger=LOG)
    # сообщение — в коде выхода, справка по .env — отдельной печатью
    assert "Не задан сервер" in str(got.value)
    assert ".env не найден" in capsys.readouterr().out


def test_llm_api_key_env_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "from-llm-env")
    p = tmp_path / "nokey.env"
    p.write_text("HOST=http://h:1/v1\n", encoding="utf-8")
    args = parser_with().parse_args(["--env_file", str(p), "--model", "m"])
    assert st.resolve_profile(args, logger=LOG).api_key == "from-llm-env"


# ══════════════════════════════════════════════════════════════════════
# Один вызов стрима на запрос стадии
# ══════════════════════════════════════════════════════════════════════
@pytest.fixture()
def recorder(monkeypatch):
    """Пишет аргументы stream_chat_completion вместо сети."""
    calls: list[dict] = []

    def fake(base_url, model, messages, **kw):
        calls.append({"base_url": base_url, "model": model,
                      "messages": messages, **kw})
        return ("ответ", None)

    monkeypatch.setattr(st, "stream_chat_completion", fake)
    return calls


def test_profile_complete_shape(recorder):
    prof = st.LlmProfile(base_url="http://h:1/v1", model="m", api_key="k",
                         timeout=11, stream_timeout=22, max_retries=2,
                         temperature=0.3, reasoning_effort="low",
                         max_tokens=32768, logger=LOG)
    text, err = prof.complete("ПРОМПТ", "данные", label="[p1]")
    assert (text, err) == ("ответ", None)
    call = recorder[0]
    assert call["base_url"] == "http://h:1/v1" and call["model"] == "m"
    # унификация messages (core.common.llm_messages): пустой system + user,
    # промпт и данные — в user через пустую строку
    assert [m["role"] for m in call["messages"]] == ["system", "user"]
    assert call["messages"][0]["content"] == ""
    content = call["messages"][1]["content"]
    assert "ПРОМПТ" in content and "данные" in content
    assert call["api_key"] == "k" and call["max_retries"] == 2
    assert (call["timeout"], call["stream_timeout"]) == (11, 22)
    assert call["max_tokens"] == 32768 and call["label"] == "[p1]"


def test_profile_complete_per_call_overrides(recorder):
    prof = st.LlmProfile(base_url="http://h:1/v1", model="m", max_retries=3)
    prof.complete("п", max_retries=1)
    assert recorder[0]["max_retries"] == 1


def test_stage_complete_adds_stage_label(recorder):
    prof = st.LlmProfile(base_url="http://h:1/v1", model="m")
    st.Stage(name="ner_check", logger=LOG, profile=prof).complete("п")
    assert recorder[0]["label"] == "[ner_check]"


# ══════════════════════════════════════════════════════════════════════
# Предпросмотр запроса
# ══════════════════════════════════════════════════════════════════════
def test_stage_preview_writes_json_and_returns_true(tmp_path):
    out = tmp_path / "preview.json"
    prof = st.LlmProfile(base_url="http://h:1/v1", model="m")
    stage = st.Stage(name="ner_check", logger=LOG, profile=prof,
                     preview_path=str(out))
    assert stage.preview("RAG · термин «сюнь уо»", "ПРОМПТ", "данные",
                         meta={"terms": 3}) is True
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["stage"] == "ner_check"
    assert doc["label"] == "RAG · термин «сюнь уо»"
    assert doc["model"] == "m"
    assert doc["meta"] == {"terms": 3}
    assert doc["chars"]["user"] == len("ПРОМПТ\n\nданные")
    assert doc["tokens"]["user"] > 0


def test_stage_preview_is_noop_without_flag():
    prof = st.LlmProfile(base_url="http://h:1/v1", model="m")
    assert st.Stage(name="ner", logger=LOG, profile=prof).preview("l", "п") \
        is False


# ══════════════════════════════════════════════════════════════════════
# Прогресс: tqdm в CLI, @@PROGRESS@@ в web
# ══════════════════════════════════════════════════════════════════════
def test_progress_web_mode_emits_events(monkeypatch, capsys):
    monkeypatch.setenv("WEB_PROGRESS", "1")
    with st.Progress(3, "Проверка глоссария") as p:
        p.step()
        p.log("термин принят")
        p.step(2)
    out = capsys.readouterr().out
    assert out.count("@@PROGRESS@@") == 3
    assert '"done": 3' in out and '"total": 3' in out
    assert "термин принят" in out


def test_progress_cli_mode_uses_bar(monkeypatch, capsys):
    monkeypatch.delenv("WEB_PROGRESS", raising=False)
    with st.Progress(2, "Перевод", unit="глава", bar=True) as p:
        p.step()
        p.step()
        assert p.done == 2
    assert p._pbar is None  # noqa: SLF001 — бар закрыт выходом из with
    assert "100%" in capsys.readouterr().err


# ══════════════════════════════════════════════════════════════════════
# setup_stage: лог стадии + команда запуска + профиль
# ══════════════════════════════════════════════════════════════════════
def test_setup_stage_log_and_profile(tmp_path, monkeypatch, env_file):
    monkeypatch.chdir(tmp_path)
    args = parser_with().parse_args(["--env_file", env_file, "--model", "m1"])
    stage, logger = st.setup_stage("translate_quality", args)
    assert stage.name == "translate_quality"
    assert stage.profile.model == "m1"
    assert stage.preview_path is None
    assert logger is stage.logger
    log = tmp_path / "logs" / "translate_quality.log"
    assert log.is_file()
    assert "Запуск:" in log.read_text(encoding="utf-8")


def test_setup_stage_custom_log_name(tmp_path, monkeypatch, env_file):
    """ner.py пишет извлечение в logs/ner_extraction.log — имя не всегда стадия."""
    monkeypatch.chdir(tmp_path)
    args = parser_with().parse_args(["--env_file", env_file])
    st.setup_stage("ner", args, log_name="ner_extraction")
    assert (tmp_path / "logs" / "ner_extraction.log").is_file()


# ══════════════════════════════════════════════════════════════════════
# REASONING / THINKING: один режим на весь конвейер
# ══════════════════════════════════════════════════════════════════════
def test_llm_args_include_reasoning_controls():
    """Флаги способа передачи рассуждений — часть общего блока LLM."""
    args = parser_with().parse_args(["--reasoning_mode", "on",
                                     "--thinking_profile", "qwen",
                                     "--thinking_budget", "2048"])
    assert (args.reasoning_mode, args.thinking_profile,
            args.thinking_budget) == ("on", "qwen", 2048)


def test_reasoning_defaults_touch_nothing(env_file):
    """Без настроек payload остаётся чистым: дефолт сервера не трогается."""
    prof = resolve(env_file)
    assert (prof.reasoning_mode, prof.thinking_profile) == ("default", "openai")
    assert prof.thinking_budget == 0 and prof.reasoning_effort is None
    assert st.reasoning_fields(prof.reasoning_mode, prof.thinking_profile,
                               prof.reasoning_effort or "",
                               prof.thinking_budget) == {}


def test_reasoning_env_keys_are_common(tmp_path, monkeypatch):
    """Ключи режима общие: стадийного REASONING_MODE у стадии нет."""
    env = tmp_path / "fake.env"
    env.write_text(
        "HOST=http://h\nMODEL=m\n"
        "REASONING_MODE=on\nTHINKING_PROFILE=anthropic\n"
        "REASONING_EFFORT=medium\nTHINKING_BUDGET=4096\n"
        "NER_CHECK_REASONING_MODE=off\n", encoding="utf-8")
    # стадийный ключ игнорируется: режим рассуждений один на весь запуск
    for k in ("REASONING_MODE", "THINKING_PROFILE", "REASONING_EFFORT",
              "THINKING_BUDGET"):
        monkeypatch.delenv(k, raising=False)
    prof = resolve(str(env), stage="ner_check")
    assert (prof.reasoning_mode, prof.thinking_profile) == ("on", "anthropic")
    assert (prof.reasoning_effort, prof.thinking_budget) == ("medium", 4096)


def test_reasoning_cli_wins_over_env(env_file, monkeypatch):
    """Канон §7: CLI > os.environ > .env — и по рассуждениям."""
    monkeypatch.setenv("REASONING_MODE", "off")
    monkeypatch.delenv("THINKING_PROFILE", raising=False)
    prof = resolve(env_file, reasoning_mode="on", thinking_profile="ollama")
    assert prof.reasoning_mode == "on" and prof.thinking_profile == "ollama"


def test_profile_layers_reasoning_and_provider(tmp_path, env_file, monkeypatch):
    """Профиль LLM — слой между общим файлом и окружением.

    Рассуждения и выбор провайдера в argv стадии не едут намеренно, поэтому
    читаются из env_data: без слоя профиля переключение режима в интерфейсе
    до запроса не доходило (в дележе этих полей формы нет)."""
    Path(env_file).write_text(
        "HOST=http://common:1/v1\nAPI_KEY=common-key\nMODEL=common-model\n"
        "REASONING_MODE=off\nPROVIDER_ONLY=openai\n", encoding="utf-8")
    (tmp_path / cfg.PROFILES_NAME).write_text(json.dumps({"profiles": [{
        "id": "cloud", "name": "Облако", "values": {
            "REASONING_MODE": "on", "REASONING_EFFORT": "high",
            "PROVIDER_ORDER": "DeepInfra, Google",
            "PROVIDER_ALLOW_FALLBACKS": "0"}}]}, ensure_ascii=False),
        encoding="utf-8")
    # General — значения общего конфига
    plain = resolve(env_file)
    assert (plain.reasoning_mode, plain.provider) == (
        "off", {"only": ["openai"]})
    monkeypatch.setenv(cfg.PROFILE_ENV, "cloud")
    prof = resolve(env_file)
    assert (prof.reasoning_mode, prof.reasoning_effort) == ("on", "high")
    # профиль перекрывает только свои поля: only из General остаётся
    assert prof.provider == {"order": ["deepinfra", "google"],
                             "only": ["openai"], "allow_fallbacks": False}
    # гибрид без ключа считает рассуждения выключенными: «включено» шлёт уровень
    assert st.reasoning_fields(prof.reasoning_mode, prof.thinking_profile,
                               prof.reasoning_effort or "",
                               prof.thinking_budget) == {
        "reasoning_effort": "high"}
    assert st.reasoning_fields(plain.reasoning_mode, plain.thinking_profile,
                               plain.reasoning_effort or "",
                               plain.thinking_budget) == {
        "reasoning_effort": "none"}


def test_provider_keys_have_no_stage_prefix(env_file, monkeypatch):
    """Ключи выбора провайдера общие: стадийного PROVIDER_ORDER у стадии нет."""
    for k in C.PROVIDER_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PROVIDER_ORDER", "yandex")
    prof = resolve(env_file, stage="ner")
    assert prof.provider == {"order": ["yandex"]}


def test_profile_complete_sends_profile_keys(recorder):
    """В тело уходит dict выбранного профиля: у qwen — chat_template_kwargs,
    а не reasoning_effort: чужой ключ строгие серверы считают ошибкой."""
    prof = st.LlmProfile(base_url="http://h:1/v1", model="m",
                         reasoning_mode="on", thinking_profile="qwen",
                         reasoning_effort="low", thinking_budget=2048)
    prof.complete("п")
    assert recorder[0]["reasoning"] == {
        "chat_template_kwargs": {"thinking": True, "enable_thinking": True}}


def test_profile_reasoning_extra_body_wins(recorder, monkeypatch):
    """Свои поля тела уходят после ключей профиля и перекрывают их."""
    monkeypatch.delenv("LLM_EXTRA_BODY_JSON", raising=False)
    raw = '{"reasoning_effort": "max", "top_k": 5}'
    prof = st.LlmProfile(base_url="http://h:1/v1", model="m",
                         reasoning_mode="on", thinking_profile="openai",
                         reasoning_effort="low",
                         env_data={"LLM_EXTRA_BODY_JSON": raw})
    prof.complete("п")
    assert recorder[0]["reasoning"] == {"reasoning_effort": "max", "top_k": 5}

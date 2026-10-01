#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/stage.py — общий слой стадий конвейера: параметр → сервер → запрос.

Стадия описывает только своё: вход, сборку промпта и разбор ответа. Всё
остальное у девяти скриптов одинаковое и живёт здесь — блок LLM-флагов,
порядок источников сервера, лог стадии с командой запуска, предпросмотр
запроса и прогресс (tqdm в CLI, @@PROGRESS@@ в web).

Имена флагов (--host / --model / --api_key / --env_file / --timeout /
--max_retries / --temperature / --reasoning_effort) — контракт с web/stages.py
и формами SPA: переименованию не подлежат, старые написания подключаются
алиасами. Единицы в help — как требует AGENTS §5: таймауты в секундах,
размеры запросов в ТОКЕНАХ (оценка estimate_tokens).

Модуль не содержит стадийной логики: только механику запуска.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field, replace
from typing import Any

from .common import (REASONING_MODES, REASONING_PROFILES, determine_model,
                     emit_progress, extra_body_fields, get_server_config,
                     llm_messages, load_env, log_argv,
                     preview_logger, preview_request_payload, print_env_help,
                     reasoning_fields, reasoning_settings, setup_logging,
                     stream_chat_completion, web_progress_enabled,
                     write_preview_request)

# Значения по умолчанию — исторические числа стадий: менять их «красоты» ради
# нельзя, timeout завязан на скорость большой модели, max_tokens — на лимит
# ответа сервера.
DEFAULT_TIMEOUT = 300
DEFAULT_STREAM_TIMEOUT = 900
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_TOKENS = 65536

#: Усилия рассуждения, которые понимает API (пусто = не отправлять).
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh",
                     "max")

#: Терпимые старые написания: dest → дополнительный флаг. Нужны только для
#: ручных команд: web-слой и SPA выдают одно каноническое написание.
LEGACY_ALIASES: dict[str, tuple[str, ...]] = {
    "max_retries": ("--retries",),
    "reasoning_effort": ("--reasoning-effort", "--thinking"),
    "env_file": ("--env-file",),
    "api_key": ("--api-key",),
}


# ══════════════════════════════════════════════════════════════════════
# ПАРАМЕТРЫ
# ══════════════════════════════════════════════════════════════════════
def add_llm_args(parser: argparse.ArgumentParser, *,
                 timeout: int = DEFAULT_TIMEOUT,
                 stream_timeout: int | None = None,
                 max_retries: int | None = DEFAULT_MAX_RETRIES,
                 max_tokens: int | None = None,
                 aliases: bool = False) -> argparse._ArgumentGroup:
    """Добавляет общий блок LLM-параметров стадии.

    stream_timeout=None — стадия не различает connect и read (один --timeout,
    как раньше); иначе появляется отдельный --stream_timeout.
    max_tokens не None — стадия отдаёт серверный предел ответа настраивать
    (значение по умолчанию — её собственный); иначе это константа скрипта.
    max_retries=None — дефолт у стадии в пресете режима, не у слоя.
    aliases=True — старые написания (--retries, --api-key, …) тоже принимаются.
    """
    group = parser.add_argument_group("сервер LLM")

    def flags(dest: str, canonical: str) -> tuple[str, ...]:
        if aliases and dest in LEGACY_ALIASES:
            return (canonical, *LEGACY_ALIASES[dest])
        return (canonical,)

    group.add_argument(*flags("host", "--host"), default=None, metavar="URL",
                       help="URL API-сервера (пусто = HOST из .env).")
    group.add_argument(*flags("model", "--model"), default=None,
                       metavar="NAME",
                       help="Модель: --model или MODEL из .env.")
    group.add_argument(*flags("api_key", "--api_key"), default=None,
                       metavar="KEY",
                       help="Bearer-ключ (пусто = API_KEY из .env).")
    group.add_argument(*flags("env_file", "--env_file"), default=None,
                       metavar="PATH", help="Явный путь к .env.")
    group.add_argument("--temperature", type=float, default=None,
                       metavar="FLOAT",
                       help="Температура (иначе — дефолт сервера).")
    group.add_argument(*flags("reasoning_effort", "--reasoning_effort"),
                       default=None, choices=list(REASONING_EFFORTS),
                       help="Усилия рассуждения модели (пусто = сервер; "
                            "none — отключить).")
    # способ передачи рассуждений — ОБЩИЙ на весь конвейер (REASONING_MODE,
    # THINKING_PROFILE, REASONING_EFFORT, THINKING_BUDGET в .env): флаги
    # нужны, чтобы задать их разово в командной строке, а не на каждую стадию
    group.add_argument("--reasoning_mode", default=None,
                       choices=list(REASONING_MODES),
                       help="Рассуждения модели: default — не трогать, "
                            "on/off — включить/выключить (пусто = .env).")
    group.add_argument("--thinking_profile", default=None,
                       choices=list(REASONING_PROFILES),
                       help="Как передавать рассуждения (у провайдеров общего "
                            "поля нет; пусто = .env).")
    group.add_argument("--thinking_budget", type=int, default=None,
                       metavar="N",
                       help="Бюджет рассуждений, ТОКЕНЫ (0 = не отправлять).")
    group.add_argument("--timeout", type=int, default=int(timeout),
                       metavar="SEC",
                       help=f"Таймаут запроса, сек (default: {int(timeout)}).")
    if stream_timeout is not None:
        group.add_argument("--stream_timeout", type=int,
                           default=int(stream_timeout), metavar="SEC",
                           help="Таймаут чтения стрима, сек "
                                f"(default: {int(stream_timeout)}).")
    # max_retries=None — дефолт у стадии в пресете режима, не у слоя
    group.add_argument(*flags("max_retries", "--max_retries"), type=int,
                       default=(None if max_retries is None
                                else int(max_retries)),
                       metavar="N",
                       help="Повторы при ошибке LLM (пусто — дефолт стадии).")
    if max_tokens is not None:
        group.add_argument(*flags("max_tokens", "--max_tokens"), type=int,
                           default=int(max_tokens), metavar="N",
                           help="Серверный предел ответа, ТОКЕНЫ (не расчёт).")
    return group


# ══════════════════════════════════════════════════════════════════════
# ПРОФИЛЬ ЗАПРОСА
# ══════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class LlmProfile:
    """Профиль LLM-запросов стадии: то, что раньше ехало через семь параметров.

    max_tokens — серверный предохранитель (ТОКЕНЫ, не расчёт); timeout —
    соединение, stream_timeout — молчание сервера в теле ответа.
    """

    base_url: str
    model: str
    api_key: str = ""
    timeout: int = DEFAULT_TIMEOUT
    stream_timeout: int = DEFAULT_STREAM_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    temperature: float | None = None
    reasoning_effort: str | None = None
    # способ передачи рассуждений: профиль (как именно) + режим + бюджет.
    # дефолты = «не трогать»: openai + default ничего в payload не добавляют
    reasoning_mode: str = "default"
    thinking_profile: str = "openai"
    thinking_budget: int = 0
    max_tokens: int = DEFAULT_MAX_TOKENS
    logger: logging.Logger | None = None
    env_data: dict = field(default_factory=dict, compare=False, repr=False)

    def complete(self, prompt: str, data: str = "", *, label: str = "",
                 max_retries: int | None = None,
                 min_len_ratio: float = 0.0,
                 reference_len: int = 0) -> tuple[str | None, str | None]:
        """Единый стрим-запрос стадии: гигиена — в core.common.

        messages собирает llm_messages: промпт + данные (пустой system,
        разметка <system> внутри промпта). Возвращает (text | None, err).
        """
        return stream_chat_completion(
            self.base_url, self.model,
            llm_messages(prompt, data),
            api_key=self.api_key,
            max_retries=self.max_retries if max_retries is None else max_retries,
            timeout=self.timeout,
            stream_timeout=self.stream_timeout,
            temperature=self.temperature,
            # рассуждения — одним dict: ключи профиля, затем свои поля тела
            # (LLM_EXTRA_BODY_JSON перекрывает их — так и задумано)
            reasoning={**reasoning_fields(self.reasoning_mode,
                                          self.thinking_profile,
                                          self.reasoning_effort or "",
                                          self.thinking_budget),
                       **extra_body_fields(self.env_data, self.logger)},
            max_tokens=self.max_tokens,
            min_len_ratio=min_len_ratio,
            reference_len=reference_len,
            logger=self.logger,
            label=label,
        )


def _pick(args: argparse.Namespace, name: str, default: Any) -> Any:
    """Значение параметра стадии: None у флага — значит «взять дефолт слоя»."""
    value = getattr(args, name, None)
    return default if value is None else value


def resolve_profile(args: argparse.Namespace, *, stage: str = "",
                    timeout: int = DEFAULT_TIMEOUT,
                    stream_timeout: int | None = None,
                    max_retries: int | None = DEFAULT_MAX_RETRIES,
                    max_tokens: int = DEFAULT_MAX_TOKENS,
                    require_model: bool = True,
                    logger: logging.Logger | None = None) -> LlmProfile:
    """Сервер стадии: CLI > os.environ > .env > выход с подсказкой.

    stage непуст — схема «одна стадия — свой набор сервер+ключ+модель»
    (<СТАДИЯ>_HOST → HOST); пусто — только общие ключи. max_tokens — константа
    стадии; если у неё есть свой --max_tokens, берётся он.
    """
    env_data = load_env(getattr(args, "env_file", None))
    sc = get_server_config(env_data, stage)
    rs = reasoning_settings(env_data)
    host = getattr(args, "host", None) or sc["host"]
    api_key = getattr(args, "api_key", None)
    if api_key is None:
        api_key = sc["api_key"]
    model = getattr(args, "model", None) or sc["model"]
    if not api_key:
        api_key = os.environ.get("LLM_API_KEY", "")
    if not host:
        print_env_help()
        sys.exit("❌ Не задан сервер: укажите --host или создайте .env (HOST).")
    base_url = host.rstrip("/")
    if "/v1" not in base_url:
        base_url += "/v1"
    if require_model:
        model = determine_model(model, logger)
    return LlmProfile(
        base_url=base_url,
        model=model or "",
        api_key=api_key or "",
        timeout=int(_pick(args, "timeout", timeout)),
        stream_timeout=int(_pick(args, "stream_timeout", _pick(
            args, "timeout",
            timeout if stream_timeout is None else stream_timeout))),
        max_retries=int(_pick(args, "max_retries", max_retries)),
        temperature=_pick(args, "temperature", None),
        reasoning_effort=_pick(args, "reasoning_effort", rs["effort"] or None),
        reasoning_mode=_pick(args, "reasoning_mode", rs["mode"]),
        thinking_profile=_pick(args, "thinking_profile", rs["profile"]),
        thinking_budget=int(_pick(args, "thinking_budget", rs["budget"])),
        max_tokens=int(_pick(args, "max_tokens", max_tokens)),
        logger=logger,
        env_data=env_data,
    )


# ══════════════════════════════════════════════════════════════════════
# СТАДИЯ: ЛОГ + ПРОФИЛЬ + ПРЕДПРОСМОТР
# ══════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class LoggedStage:
    """Стадия без LLM: лог, имя и путь предпросмотра (режимы --apply и т.п.)."""

    name: str
    logger: logging.Logger
    preview_path: str | None = None
    log_path: str = ""


@dataclass(frozen=True)
class Stage:
    """Контекст LLM-стадии: имя (логи и прогресс), профиль, путь предпросмотра."""

    name: str
    logger: logging.Logger
    profile: LlmProfile
    preview_path: str | None = None
    log_path: str = ""

    def complete(self, prompt: str, data: str = "", *, label: str = "",
                 **kw: Any) -> tuple[str | None, str | None]:
        """Один LLM-запрос стадии: метка по умолчанию — имя стадии."""
        return self.profile.complete(prompt, data,
                                     label=label or f"[{self.name}]", **kw)

    def quiet(self) -> "Stage":
        """Копия без логирования: стадия со своим циклом ретраев логирует
        сама — по строке на запрос, а не на каждую попытку."""
        return replace(self, profile=replace(self.profile, logger=None))

    def preview(self, label: str, prompt: str, data: str = "",
                meta: dict | None = None) -> bool:
        """Режим --preview-request: записать первый запрос и не идти в сеть.

        True — предпросмотр выполнен, main() возвращает 0.
        """
        if not self.preview_path:
            return False
        log = preview_logger(self.name)
        log_argv(log)
        write_preview_request(self.preview_path, preview_request_payload(
            self.name, label, self.profile.model,
            llm_messages(prompt, data), meta=meta))
        log.info("✅ Предпросмотр запроса: %s (%d симв. user)",
                 self.preview_path, len(prompt) + len(data))
        return True


def new_stage(name: str, args: argparse.Namespace, *,
              log_dir: str = "logs", log_name: str | None = None,
              log_fatal: bool = True) -> LoggedStage:
    """Лог стадии и команда запуска: то, что каждый скрипт писал сам.

    log_name — имя файла лога (по умолчанию имя стадии, .log подставит
    setup_logging). log_fatal=False — не создать logs/ предупреждением, а
    продолжать: у части стадий лог не критичен.
    """
    log_path = ""
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError as exc:
        if log_fatal:
            print(f"Не удалось создать {log_dir}/: {exc}")
            raise SystemExit(1)
        print(f"⚠ {log_dir}/ не создаётся: {exc}", file=sys.stderr)
        logging.basicConfig(level=logging.INFO)
        logger = logging.getLogger(name)
    else:
        logger, log_path = setup_logging(os.path.join(log_dir,
                                                     log_name or name))
    log_argv(logger)
    return LoggedStage(name=name, logger=logger, log_path=log_path,
                      preview_path=getattr(args, "preview_request", None))


def bind_profile(stage: LoggedStage, args: argparse.Namespace,
                 **profile_kw: Any) -> Stage:
    """Профиль LLM стадии: сервер, ключ, модель, таймауты и ретраи.

    Отдельно от new_stage: режимы без LLM (--apply, --compile-chapters) не
    должны требовать настроенного сервера.
    """
    return Stage(name=stage.name, logger=stage.logger,
                 preview_path=stage.preview_path, log_path=stage.log_path,
                 profile=resolve_profile(args, stage=stage.name,
                                         logger=stage.logger, **profile_kw))


def setup_stage(name: str, args: argparse.Namespace, *, log_dir: str = "logs",
                log_name: str | None = None, log_fatal: bool = True,
                **profile_kw: Any) -> tuple[Stage, logging.Logger]:
    """Одна строка для LLM-стадий: лог + профиль (new_stage + bind_profile)."""
    stage = bind_profile(new_stage(name, args, log_dir=log_dir,
                                   log_name=log_name, log_fatal=log_fatal),
                        args, **profile_kw)
    return stage, stage.logger


# ══════════════════════════════════════════════════════════════════════
# ПРОГРЕСС
# ══════════════════════════════════════════════════════════════════════
def _bar_class() -> Any:
    """Класс tqdm или None: библиотека опциональна (роль в core/deps.py)."""
    try:
        from tqdm import tqdm as cls
    except ImportError:
        return None
    return cls


def _make_bar(total: int, unit: str, label: str) -> Any:
    """Бар CLI: в web-режиме и без tqdm его нет — счётчик живёт строками лога."""
    if web_progress_enabled():
        return None
    cls = _bar_class()
    if cls is None:
        return None
    return cls(total=total, unit=unit, desc=label)


@dataclass
class Progress:
    """Прогресс стадии: tqdm в CLI, @@PROGRESS@@ в web-режиме.

    Счётчик всегда свой: у tqdm при disable=True (web-режим) pbar.n не
    двигается, а стадиям нужно своё done/total — и для снапшотов ner.json,
    и для пауз сохранения.
    """

    total: int
    label: str
    unit: str = "шт."
    bar: bool = False
    logger: logging.Logger | None = None
    log_every: int = 1
    done: int = 0
    _pbar: Any = None

    def __post_init__(self) -> None:
        self._pbar = _make_bar(self.total, self.unit, self.label)

    def __enter__(self) -> Progress:
        self.start()
        return self

    def __exit__(self, exc_type: object = None, exc_value: object = None,
                 traceback: object = None) -> bool:
        self.close()
        return False

    def start(self) -> None:
        emit_progress(0, self.total, self.label)
        self._log_state()

    def step(self, n: int = 1) -> None:
        """+N единиц прогресса: web-событие и, если есть, бар."""
        self.done += n
        if self._pbar is not None:
            self._pbar.update(n)
        emit_progress(self.done, self.total, self.label)
        self._log_state()

    def log_state(self) -> None:
        """Счётчик в лог стадии после внешних событий (снапшот ner.json)."""
        self._log_state()

    def _log_state(self) -> None:
        """Web-режим дублирует счётчик в лог стадии: log_every — как часто
        (длинные прогоны не плодят по строке лога на чанк)."""
        if (self.logger is not None and web_progress_enabled()
                and self.done % max(1, self.log_every) == 0):
            self.logger.info(f"📊 Прогресс: {self.done}/{self.total}")

    def log(self, message: str) -> None:
        """Строка прогресса: tqdm.write не рвёт бар, иначе — обычный вывод."""
        if self._pbar is not None:
            _bar_class().write(message)
        else:
            print(message)

    def close(self) -> None:
        if self._pbar is not None:
            self._pbar.close()
            self._pbar = None

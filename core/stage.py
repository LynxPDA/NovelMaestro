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
import time
from dataclasses import dataclass, field, replace
from typing import Any

from . import settings as cfg
from .common import (EXTRA_BODY_ENV_KEY, REASONING_ENV_KEYS, REASONING_MODES,
                     REASONING_PROFILES, determine_model, emit_progress,
                     extra_body_fields, get_server_config, llm_messages,
                     log_argv, preview_logger, preview_request_payload,
                     print_env_help, prompt_check_result, provider_settings,
                     reasoning_fields, reasoning_settings, server_base_url,
                     setup_logging, stream_chat_completion,
                     web_progress_enabled, write_preview_request,
                     write_prompt_check)

# Значения по умолчанию — из реестра настроек (core/settings.py): одно и то же
# число в двух местах расходится неизбежно, поэтому источник один. Меняя
# дефолт, меняй его в реестре (он же отдаёт его странице «Настройки»).
DEFAULT_TIMEOUT = int(cfg.BY_KEY["TIMEOUT"].default)
DEFAULT_STREAM_TIMEOUT = int(cfg.BY_KEY["STREAM_TIMEOUT"].default)
DEFAULT_MAX_RETRIES = int(cfg.BY_KEY["MAX_RETRIES"].default)
DEFAULT_MAX_TOKENS = int(cfg.BY_KEY["MAX_TOKENS"].default)
#: потоков на весь конвейер (была: 1 у ner_check, 4 у остальных)
DEFAULT_THREADS = int(cfg.BY_KEY["THREADS"].default)
#: верхняя граница потоков — то же число, что у поля формы (max)
DEFAULT_MAX_THREADS = int(cfg.BY_KEY["THREADS"].max or 16)
#: Усилия рассуждения, которые понимает API (пусто = не отправлять).
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh",
                     "max")

#: Режим рассуждений в логе — теми же словами, что поле формы «Настроек».
_REASONING_LABELS = {"default": "решение сервера", "on": "включены",
                     "off": "выключены"}

#: Терпимые старые написания: dest → дополнительный флаг. Нужны только для
#: ручных команд: web-слой и SPA выдают одно каноническое написание.
LEGACY_ALIASES: dict[str, tuple[str, ...]] = {
    "max_retries": ("--retries",),
    "threads": ("--jobs",),
    "reasoning_effort": ("--reasoning-effort", "--thinking"),
    "env_file": ("--env-file",),
    "api_key": ("--api-key",),
}


# ══════════════════════════════════════════════════════════════════════
# ПАРАМЕТРЫ
# ══════════════════════════════════════════════════════════════════════
def add_llm_args(parser: argparse.ArgumentParser, *,
                 aliases: bool = False) -> argparse._ArgumentGroup:
    """Добавляет общий блок LLM-параметров стадии.

    У всех флагов default=None — «значения нет»: сервер, модель, таймауты,
    повторы, max_tokens и рассуждения берются из общего конфига (реестр →
    общий .env → os.environ). Собственных LLM-дефолтов у стадии больше нет:
    шесть пар «свой сервер + своя модель» на один запуск разъезжались.
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
                       help="Температура (пусто = общий TEMPERATURE, а без него "
                            "— дефолт сервера).")
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
    group.add_argument("--timeout", type=int, default=None,
                       help=f"Таймаут запроса, сек (пусто = общий TIMEOUT, "
                            f"встроенный {int(DEFAULT_TIMEOUT)}).")
    group.add_argument("--stream_timeout", type=int, default=None,
                       metavar="SEC",
                       help="Таймаут чтения стрима, сек (пусто = общий "
                            f"STREAM_TIMEOUT, встроенный "
                            f"{int(DEFAULT_STREAM_TIMEOUT)}).")
    group.add_argument(*flags("max_retries", "--max_retries"), type=int,
                       default=None, metavar="N",
                       help="Повторы при ошибке LLM (пусто = общий "
                            f"MAX_RETRIES, встроенный "
                            f"{int(DEFAULT_MAX_RETRIES)}).")
    group.add_argument(*flags("max_tokens", "--max_tokens"), type=int,
                       default=None, metavar="N",
                       help="Серверный предел ответа, ТОКЕНЫ (пусто = общий "
                            f"MAX_TOKENS, встроенный "
                            f"{int(DEFAULT_MAX_TOKENS)}).")
    group.add_argument(*flags("threads", "--threads"), type=int, default=None,
                       metavar="N",
                       help=f"Потоков (1–{DEFAULT_MAX_THREADS}): параллельные "
                            f"запросы стадии (пусто = общий THREADS, встроенный "
                            f"{int(DEFAULT_THREADS)}; у конвейера — суммарно "
                            f"на главы и чанки).")
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
    #: параллельных запросов стадии — тоже одна величина на конвейер
    threads: int = DEFAULT_THREADS
    temperature: float | None = None
    reasoning_effort: str | None = None
    # способ передачи рассуждений: профиль (как именно) + режим + бюджет.
    # дефолты = «не трогать»: openai + default ничего в payload не добавляют
    reasoning_mode: str = "default"
    thinking_profile: str = "openai"
    thinking_budget: int = 0
    # выбор провайдера (роутеры OpenRouter-типа): {} — балансирует сам роутер
    provider: dict = field(default_factory=dict, compare=False)
    max_tokens: int = DEFAULT_MAX_TOKENS
    logger: logging.Logger | None = None
    env_data: dict = field(default_factory=dict, compare=False, repr=False)

    def complete(self, prompt: str, data: str = "", *, label: str = "",
                 max_retries: int | None = None) -> tuple[str | None, str | None]:
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
            provider=self.provider,
            max_tokens=self.max_tokens,
            logger=self.logger,
            label=label,
        )


def _pick(args: argparse.Namespace, name: str, default: Any) -> Any:
    """Значение параметра стадии: None у флага — значит «взять дефолт слоя»."""
    value = getattr(args, name, None)
    return default if value is None else value


def _as_float(value: Any) -> float | None:
    """Числовое поле конфига (TEMPERATURE) → float; пусто или мусор → None."""
    try:
        return float(str(value).strip()) if str(value or "").strip() else None
    except ValueError:
        return None


def describe_profile(profile: LlmProfile) -> list:
    """Строки лога о разрешённом конфиге: одна общая точка на все стадии.

    Пишет их resolve_profile, а не тело стадии: у стадии собственный флаг пуст,
    когда значение пришло из профиля или общего .env, и напечатать
    «reasoning=off» при включённых рассуждениях — её обычное дело. Рассуждения
    и провайдер печатаются как реальные ключи payload: спорить с тем, что ушло
    в запрос, легче, чем с пересказом режима.
    """
    p = profile
    out = [f"Сервер: {p.base_url} | таймаут {p.timeout}с | стрим "
           f"{p.stream_timeout}с | повторы {p.max_retries}"
           f" | ответ ≤{p.max_tokens} ток."]
    if p.temperature is not None:
        out.append(f"Температура: {p.temperature}")
    mode = _REASONING_LABELS.get(p.reasoning_mode, p.reasoning_mode)
    fields = reasoning_fields(p.reasoning_mode, p.thinking_profile,
                              p.reasoning_effort or "", p.thinking_budget)
    if fields:
        keys = " ".join(f"{k}={v}" for k, v in fields.items())
        out.append(f"Рассуждения: {mode} (профиль {p.thinking_profile}) → {keys}")
    else:
        out.append(f"Рассуждения: {mode} (ключей в запросе нет, профиль "
                   f"{p.thinking_profile})")
    extra = extra_body_fields(p.env_data, p.logger)
    if extra:
        out.append(f"Свои поля тела {EXTRA_BODY_ENV_KEY}: "
                   f"{', '.join(sorted(map(str, extra)))}")
    if p.provider:
        out.append("Провайдер: " + " ".join(
            f"{k}={v}" for k, v in p.provider.items()))
    return out


def resolve_profile(args: argparse.Namespace, *, stage: str = "",  # noqa: ARG001
                    require_model: bool = True, require_server: bool = True,
                    logger: logging.Logger | None = None) -> LlmProfile:
    """Профиль LLM стадии: CLI > os.environ > общий .env > дефолт реестра.

    Ключей <СТАДИЯ>_HOST/MODEL/API_KEY больше нет: сервер, ключ и модель у
    конвейера одни на все стадии (arg `stage` оставлен — его передают все
    скрипты, на разрешение конфигурации он больше не влияет). Собственных
    дефолтов таймаутов/ретраев/max_tokens у стадии тоже нет: они приходят из
    эффективного конфига, иначе шесть стадий жили бы шестью наборами чисел.

    Пустой сервер — не ошибка только там, где в сеть идти не нужно
    (require_server=False: режим предпросмотра запроса).
    """
    # Один слой конфига на весь запуск: общий .env (или явный --env_file) →
    # профиль LLM → окружение процесса (AGENTS §7). Из него же и рассуждения, и
    # «свои поля тела»: их в argv намеренно нет, поэтому без профиля
    # переключение режима в интерфейсе до запроса не доходило.
    env_path = str(getattr(args, "env_file", None) or "")
    pid = os.environ.get(cfg.PROFILE_ENV, "").strip()
    env_data = cfg.layered_values(pid, env_path)
    llm = cfg.llm_values(pid, env_path)
    sc = get_server_config(env_data)
    # Дефолты рассуждений — из реестра настроек (их же показывает форма):
    # своей копии в core.common быть не должно
    rs = reasoning_settings(env_data, {
        key: cfg.BY_KEY[key].default for key in REASONING_ENV_KEYS})
    host = getattr(args, "host", None) or sc["host"]
    api_key = getattr(args, "api_key", None)
    if api_key is None:
        api_key = sc["api_key"]
    model = getattr(args, "model", None) or sc["model"]
    if not api_key:
        api_key = os.environ.get("LLM_API_KEY", "")
    if not host and require_server:
        print_env_help()
        sys.exit("❌ Не задан сервер: укажите --host или создайте .env (HOST).")
    # адрес нормализует общий слой: проверка сервера ходит по тому же адресу
    base_url = server_base_url(host)
    if require_model:
        model = determine_model(model, logger)
    timeout = _pick(args, "timeout", llm["timeout"])
    # стрим — своя настройка STREAM_TIMEOUT, а не таймаут соединения: форма
    # запусков показывает одно поле «Таймаут», общий конфиг различает два числа
    stream_timeout = _pick(args, "stream_timeout",
                           llm["stream_timeout"] or timeout)
    profile = LlmProfile(
        base_url=base_url,
        model=model or "",
        api_key=api_key or "",
        timeout=int(timeout),
        stream_timeout=int(stream_timeout),
        max_retries=int(_pick(args, "max_retries", llm["max_retries"])),
        threads=max(1, min(DEFAULT_MAX_THREADS,
                           int(_pick(args, "threads",
                                     llm["threads"] or DEFAULT_THREADS)))),
        temperature=_pick(args, "temperature",
                          _as_float(llm.get("temperature"))),
        reasoning_effort=_pick(args, "reasoning_effort", rs["effort"] or None),
        reasoning_mode=_pick(args, "reasoning_mode", rs["mode"]),
        thinking_profile=_pick(args, "thinking_profile", rs["profile"]),
        thinking_budget=int(_pick(args, "thinking_budget", rs["budget"])),
        # маршрутизация провайдера — тоже послойный конфиг, флагов стадии нет
        provider=provider_settings(env_data),
        max_tokens=int(_pick(args, "max_tokens", llm["max_tokens"])),
        logger=logger,
        env_data=env_data,
    )
    if logger is not None:
        # конфигурация запроса печатается здесь, а не в теле стадии: иначе
        # стадия отчитывается своим пустым флагом вместо реального значения
        for line in describe_profile(profile):
            logger.info(line)
    return profile


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

    def quiet(self) -> Stage:
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


def complete_until(stage: Stage, prompt: str, data: str = "", *,
                   label: str = "", accept=None, attempts: int = 0,
                   note: str = "правок нет") -> tuple[str | None, str | None]:
    """Запрос стадии с повторами, пока ответ не устроит `accept`.

    Политика повторов одна и она не про исключения: максимум
    `1 + attempts` запросов. attempts — число ДОПОЛНИТЕЛЬНЫХ повторов, и оно
    стадийное (`NER_CHECK_RETRY_EMPTY` / `TRANSLATE_CHECK_LLM_RETRY_EMPTY`):
    цена повтора у проверки глоссария (батч, а в RAG — каждый термин) и у
    проверки перевода (пакет глав) разная, общее число здесь было бы чужим.

    `accept(text)` → False — правок не найдено: пустой ответ, пустой список
    `[]` и нераспарсенный ответ равны «ничего не нашлось» и дают повод
    спросить ещё раз. Последний ответ возвращается как есть: «правок нет» —
    допустимый результат. Ошибка LLM (text=None) — сразу (её считает
    stream_chat_completion).
    """
    check = accept or (lambda text: bool((text or "").strip()))
    total = 1 + max(0, int(attempts or 0))
    text = None
    err = None
    for att in range(total):
        text, err = stage.complete(prompt, data, label=label)
        if text is None:
            return None, err
        if check(text):
            return text, None
        if stage.logger is not None and att < total - 1:
            stage.logger.info(f"{label}: {note} — повтор {att + 2}/{total}")
    return text, None


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
# ПРОВЕРКА ПРОМПТ-ФАЙЛА: --check-prompt
# ══════════════════════════════════════════════════════════════════════
def add_check_prompt_arg(ap: argparse.ArgumentParser) -> None:
    """Флаг --check-prompt у стадии с внешним промптом.

    Путь артефакта относительный: web передаёт tmp/prompt_check.json, а cwd
    процесса — папка книги."""
    ap.add_argument("--check-prompt", dest="check_prompt", default=None,
                    metavar="PATH",
                    help="ПРОВЕРКА промпт-файла: сверить теги и переменные "
                         "стадии без запросов к LLM → JSON в PATH.")


def check_prompt(args: argparse.Namespace, name: str, contract,
                 json_tags=(), *, file_attr: str = "prompt_file") -> bool:
    """Режим --check-prompt: вердикт о промпт-файле стадии, до LLM не доходит.

    contract — записи (тег, где читается, (переменные,)), json_tags — служебные
    JSON-теги; проверка ставится первым делом main(), до чтения глав: логи
    запусков и артефакты стадии не создаются, пишется только сам вердикт.
    True — режим выполнен, main() возвращает 0."""
    path = getattr(args, "check_prompt", None)
    if not path:
        return False
    res = prompt_check_result(name, str(getattr(args, file_attr, "") or ""),
                              contract, json_tags)
    write_prompt_check(path, res)
    log = preview_logger(name)
    if res["problems"]:
        for line in res["problems"]:
            log.warning("⚠️ промпт: %s", line)
    for line in res["notes"]:
        log.info("ℹ %s", line)
    log.info("✅ Проверка промпта: %s (замечаний: %d)", path, len(res["problems"]))
    return True


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


# Как часто счётчик стадии дублируется строкой в лог стадии (web-режим):
# счётчик на экране уже рисует полоса прогресса, а каждая строка лога — это
# ещё одно SSE-событие и ещё одна перерисовка панели. Прежний режим «строка на
# каждый шаг» на книге в 1300 глав заливал кольцевой буфер запусков тысячами
# одинаковых «📊 Прогресс».
LOG_EVERY = 50
# …и не чаще одной строки в LOG_EVERY_SECONDS секунд: редкие шаги (чанк на
# минуте) не должны оставлять лог без счётчика полчаса.
LOG_EVERY_SECONDS = 60.0


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
    log_every: int = LOG_EVERY
    log_every_seconds: float = LOG_EVERY_SECONDS
    done: int = 0
    _pbar: Any = None
    _logged: int = -1
    _logged_at: float = 0.0

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

    def _log_state(self, force: bool = False) -> None:
        """Счётчик в логе — редкой строкой: каждые log_every шагов или не чаще
        раза в log_every_seconds (в CLI счётчик ведёт бар, здесь — no-op)."""
        if self.logger is None or not web_progress_enabled():
            return
        now = time.monotonic()
        if (not force and self._logged >= 0
                and self.done - self._logged < max(1, self.log_every)
                and now - self._logged_at < self.log_every_seconds):
            return
        self._logged = self.done
        self._logged_at = now
        self.logger.info(f"📊 Прогресс: {self.done}/{self.total}")

    def log(self, message: str) -> None:
        """Строка прогресса: tqdm.write не рвёт бар, иначе — обычный вывод."""
        if self._pbar is not None:
            _bar_class().write(message)
        else:
            print(message)

    def close(self) -> None:
        """Снять бар и оставить в логе финальный счётчик (иначе хвост файла
        обрывался бы на счёте минутной давности)."""
        if self._pbar is not None:
            self._pbar.close()
            self._pbar = None
        if self.logger is not None and self._logged != self.done:
            self._log_state(force=True)

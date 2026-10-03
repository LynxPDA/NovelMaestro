#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
settings.py — единый реестр настроек NovelMaestro.

Одно место истины: здесь и значение по умолчанию каждой настройки, и то, как
она называется и ведёт себя в интерфейсе. раньше одно значение жило в четырёх
местах (константы core/stage.py, константы и argparse cli/*, литеральные
"default" в web/stages.py, третий экземпляр комментариями в
templates/.env.example) и глобальные ключи вида CHUNK_SIZE/TIMEOUT не читал
вовсе никто — стадийный префикс <STAGE>_<FIELD> был единственной формой записи.

Структура: субвкладка (Group) → блок (Block) → настройка (Setting).
Ключ настройки — её имя в .env: у стадийных настроек это <STAGE>_<FIELD>,
у общих (LLM, рассуждения, WEB_*) — имя само по себе.

Слои конфига: реестр (зашито) → общий .env (то, что выставил пользователь) →
выбранный профиль LLM → окружение процесса. Файл .env в папке книги — рабочее
состояние браузера (localStorage), а не слой конфига; стадийные переопределения
сервера/модели/ключа убраны: модель в конвейере одна, а несколько наборов
серверных настроек — это профили (секция «профили LLM»).
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path

from .common import env_overlay, parse_dotenv, read_text_safe, system_env_file

log = logging.getLogger("nm")

NL = chr(10)  # настоящий перевод строки (значение в форме)
NL_LIT = chr(92) + "n"  # литерал «\n» — так многострочное живёт в .env

# ════════════════════════════════════════════════════════════════════
# модель реестра
# ════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Setting:
    """Одна настройка: ключ .env, метка, тип, дефолт и подсказка."""

    key: str
    label: str = ""
    type: str = "text"          # text|number|bool|select|textarea|files|password|hidden
    default: object = ""
    options: tuple = ()
    labels: dict = field(default_factory=dict)
    min: object = None
    max: object = None
    step: object = None
    dir: str = ""
    ext: tuple = ()
    rows: object = None
    #: автоподхват файла из пула (compile: обложка/метаданные/донат, промпты)
    autofile: object = None
    editable: bool = False      # files-поле правится прямо из формы запуска
    hidden: bool = False        # настройки нет в форме стадии (только «Настройки»)
    help: str = ""
    noenv: bool = False         # состояние UI (чипсы) — в .env не пишется
    run: bool = False           # параметр запуска (главы, входные файлы)
    secret: bool = False        # парольное поле: значение не отдаётся в SPA
    stage: str = ""             # владелец-стадия ('' — общая настройка)

    @property
    def name(self) -> str:
        """Имя поля формы: ключ без стадийного префикса, в нижнем регистре."""
        prefix = f"{self.stage.upper()}_" if self.stage else ""
        return (self.key[len(prefix):] if self.key.startswith(prefix)
                else self.key).lower()

    def form_field(self) -> dict:
        """Поле формы в том же формате, что понимают SPA и spec стадий."""
        out = {"name": self.name, "type": self.type}
        if self.label:
            out["label"] = self.label
        out["default"] = self.default
        for k in ("options", "labels", "min", "max", "step", "dir", "ext",
                  "rows", "autofile", "editable", "help", "noenv"):
            v = getattr(self, k)
            if v not in (None, "", [], {}, ()):
                out[k] = v
        if self.secret:
            out["secret"] = True
        return out


@dataclass(frozen=True)
class Block:
    """Блок настроек — карточка на странице."""

    id: str
    title: str
    settings: tuple


@dataclass(frozen=True)
class Group:
    """Субвкладка страницы настроек."""

    id: str
    title: str
    blocks: tuple


def _s(key: str, label: str = "", type: str = "text",  # noqa: A002,A006
       default: object = "", **kw: object) -> Setting:
    """Сокращённый конструктор настройки (реестр читается как таблица)."""
    return Setting(key, label, type, default, **kw)


def _block(bid: str, title: str, *settings: Setting) -> Block:
    return Block(bid, title, tuple(settings))


def _group(gid: str, title: str, *blocks: Block) -> Group:
    return Group(gid, title, tuple(blocks))


# ════════════════════════════════════════════════════════════════════
# реестр
# ════════════════════════════════════════════════════════════════════

GROUPS: tuple = (
    # ── LLM: одна настройка на весь конвейер ─────────────────────────
    _group("llm", "Модель и сервер",
        _block("llm_conn", "Подключение",
            _s("HOST", "Сервер LLM", "text", "https://routerai.ru/api/v1",
                help="адрес API-сервера; /v1 дописывается, если его нет"),
            _s("API_KEY", "API-ключ", "password", "", secret=True, help="локальный сервер может работать без ключа"),
            _s("MODEL", "Модель", "text", "google/gemma-4-31b-it",
                help="одна модель на весь конвейер: отдельных моделей у стадий больше нет"),
        ),
        _block("llm_net", "Сеть и повторы",
            _s("TIMEOUT", "Таймаут запроса, СЕК", "number", "300", min=0),
            _s("STREAM_TIMEOUT", "Таймаут стрима, СЕК", "number", "900", min=0, help="пауза между строками SSE"),
            _s("MAX_RETRIES", "Повторы при ошибке LLM", "number", "3", min=0, help="ретраи только по 408/425/429/5xx"),
            _s("MAX_TOKENS", "Предел ответа, ТОКЕНЫ", "number", "65536", min=1, help="max_tokens в payload (не расчёт)"),
            _s("RETRY_EMPTY", "Доп. повторы при пустом ответе", "number", "0", min=0),
        ),
        _block("llm_run", "Температура и параллельность",
            _s("TEMPERATURE", "Температура (пусто = сервер)", "text", ""),
            _s("THREADS", "Потоков (1–16)", "number", "4", min=1, max=16, help="одна величина на весь конвейер"),
            _s("MIN_LEN_RATIO", "Мин. отношение длин, СИМВОЛЫ", "text", "0", help="0 — контроль соотношения длин выключен"),
        ),
        _block("llm_reasoning", "Рассуждения модели",
            _s("REASONING_MODE", "Рассуждения", "select", "default", options=("default", "on", "off"), labels={'default': "— (решение сервера)", 'on': "включены", 'off': "выключены"},
                help="у части серверов рассуждения включены по умолчанию; «выключены» для openai-профиля = reasoning_effort=none"),
            _s("THINKING_PROFILE", "Профиль API", "select", "openai", options=("openai", "anthropic", "qwen", "dashscope", "ollama", "openrouter", "all"),
                labels={'openai': "OpenAI-совместимый (reasoning_effort)", 'anthropic': "Anthropic (thinking.budget_tokens)", 'qwen': "Qwen3/DeepSeek (chat_template_kwargs)", 'dashscope': "DashScope (enable_thinking)", 'ollama': "Ollama (think)", 'openrouter': "OpenRouter (reasoning.effort)", 'all': "Все ключи сразу (строгие серверы отвечают 400)"},
                help="как именно передавать рассуждения: каждый профиль отправляет только свои ключи (незнакомый ключ строгий сервер считает ошибкой запроса); «все ключи сразу» — только для серверов, которые молча игнорируют чужие"),
            _s("REASONING_EFFORT", "Уровень рассуждения", "select", "", options=("", "none", "minimal", "low", "medium", "high", "xhigh", "max"),
                labels={'': "— (не отправлять, дефолт сервера)", 'none': "none — выключено", 'minimal': "minimal", 'low': "low", 'medium': "medium", 'high': "high", 'xhigh': "xhigh", 'max': "max"},
                help="пусто — не передаётся; понимают openai, openrouter и «все ключи сразу»"),
            _s("THINKING_BUDGET", "Бюджет рассуждения, ТОКЕНЫ", "number", 0, help="0 — не отправлять; понимают anthropic (thinking.budget_tokens) и dashscope (thinking_budget)"),
            _s("LLM_EXTRA_BODY_JSON", "Свои поля тела (JSON)", "text", "",
                help="JSON-объект ключей, которых не знает ни один профиль (свой сервер); уходят в тело запроса после ключей профиля, то есть перекрывают их; битый JSON запрос не ломает — поле игнорируется с предупреждением в лог"),        ),
        # ── сам веб-сервер: свои блоки едут последними этой субвкладки ──
        _block("server_net", "Веб-сервер: сеть и доступ",
            _s("WEB_HOST", "Адрес прослушивания", "text", "127.0.0.1", help="применяется после перезапуска сервера; 0.0.0.0 — вся локальная сеть, тогда включайте аутентификацию; в Docker адрес задаёт образ (CLI-флаг --host выше любого файла)"),
            _s("WEB_PORT", "Порт", "number", "8756", help="применяется после перезапуска; в Docker наружу пробрасывается порт из compose, этот отвечает за адрес внутри контейнера"),
            _s("WEB_AUTH", "Требовать токен", "bool", False, help="применяется после перезапуска"),
            _s("WEB_TOKEN", "Токен", "password", "", secret=True, help="пусто — файл .web_secret (генерируется один раз и живёт в папке проектов)"),
        ),
        _block("server_run", "Веб-сервер: данные и задачи",
            _s("WEB_MAX_UPLOAD_MB", "Лимит загрузки, МБ", "number", "512", min=1, help="на файл и на всё тело запроса"),
            _s("WEB_JOBS_LIMIT", "Максимум параллельных задач", "number", "2", min=1, help="сколько стадий может идти одновременно"),
            _s("WEB_PROJECTS_DIR", "Папка проектов", "text", "", help="пусто — <репо>/projects; в Docker это том /app/projects, менять негде и незачем"),
        ),
    ),

    # ── Перевод ──
    _group("transfer", "Перевод",
        _block("pipeline", "Перевод (translate → redact → polish)",
            _s("PIPELINE_ACTION", "Тип работы", "select", "8", options=("1", "2", "3", "4", "5", "6", "7", "8", "9"),
                labels={'1': "Перевод", '2': "Редактура (исходник - Перевод)", '3': "Полировка (исходник - Редактура)", '4': "Полировка (исходник - Перевод)", '5': "Сокращенный цикл: Перевод -> Редактура", '6': "Сокращенный цикл: Перевод -> Полировка", '7': "Сокращенный цикл: Редактура -> Полировка", '8': "Полный цикл: Перевод -> Редактура -> Полировка", '9': "Перевод с расширенным контекстом"},
                help="1=перевод, 2=редактура (исходник - перевод), 3=полировка (исходник - редактура), 4=полировка (исходник - перевод), 5=перевод→редактура, 6=перевод→полировка, 7=редактура→полировка, 8=полный цикл, 9=перевод с расширенным контекстом (словарь/правила/примеры из source/; настройка файлов — в экспертном режиме)",
                stage="pipeline", run=True),
            _s("PIPELINE_DICT_FILE", "Словарь перевода (dict.json)", "files", "", dir="source", ext=(".json",),
                help="формат как ner.json: term/translation/type?/aliases?/notes?; найденные в чанке записи (и в примерах) попадают в {dict_block}; направление определяется автоматически — где больше совпадений",
                stage="pipeline", run=True),
            _s("PIPELINE_RULES_FILE", "Правила языка (rules.txt/md)", "files", "", dir="source", ext=(".txt", ".md"),
                help="краткий справочник по языку; целиком в {rules_block} (общий потолок — бюджет запроса)", stage="pipeline", run=True),
            _s("PIPELINE_EXAMPLES_FILE", "Пары оригинал→перевод (examples.json)", "files", "", dir="source", ext=(".json",),
                help="массив {original_text, translated_text} (алиасы source/target); релевантные пары — few-shot {fewshot_block}; направление — автодетект", stage="pipeline", run=True),
            _s("PIPELINE_FEWSHOT_K", "Макс. примеров на чанк", "number", "3", min=0, max=20, help="сколько релевантных пар влезает в few-shot", stage="pipeline"),
            _s("PIPELINE_FEWSHOT_THRESHOLD", "Порог схожести примеров (0–1)", "number", "0.3", min=0, max=1, step="0.05",
                help="доля n-грамм (3-граммы нормализованного текста) стороны примера, найденных в чанке: 1 — все n-граммы примера есть в чанке; примеры ниже порога отбрасываются — лучше без примеров, чем с шумными",
                stage="pipeline"),
            _s("PIPELINE_REQUEST_BUDGET", "Бюджет запроса, ТОКЕНЫ", "number", "24000", min=0,
                help="общий бюджет user-запроса (чанк + все блоки), оценка токенов; 0 = выключено; превышение — ошибка чанка", stage="pipeline"),
            _s("PIPELINE_PROMPT_FILE", "Общий промпт-файл (теги translate/redact/polish)", "files", "", dir="prompts", ext=(".txt",),
                help="один файл с тегами <translate>/<redact>/<polish>; пусто = авто (первый кандидат с тегами из prompts/); недостающий тег стадии — предупреждение + встроенный промпт",
                stage="pipeline", run=True),
            _s("PIPELINE_CHUNK_SIZE", "Размер чанка, ТОКЕНЫ", "number", "7000", min=1,
                help="чанкование текста для перевода и полировки (оценка токенов) — действует в ЛЮБОМ выбранном типе работы; редактура идёт главой целиком; пусто = PIPELINE_CHUNK_SIZE из .env → 7000",
                stage="pipeline"),
            _s("PIPELINE_NER_THRESHOLD", "Порог схожести терминов (0–1)", "number", "0.75", hidden=True,
                help="нечёткий поиск терминов по n-граммам: доля перекрытия; точные вхождения ищутся всегда", stage="pipeline"),
            _s("PIPELINE_NER_NGRAM", "Размер n-грамм поиска терминов", "number", "3", hidden=True,
                help="окно сравнения (символы)", stage="pipeline"),
            _s("PIPELINE_START", "Начальная глава (ГЛАВЫ)", "number", "", stage="pipeline", run=True),
            _s("PIPELINE_END", "Конечная глава", "number", "", stage="pipeline", run=True),
            _s("PIPELINE_NER_MIN_COUNT", "Мин. count для глоссария ({ner_block})", "number", "0", help="термины с count ниже порога НЕ попадают в {ner_block}; 0 — фильтр выключен (все найденные)",
                stage="pipeline"),
            _s("PIPELINE_NER_FIELDS", "", "hidden", "term,type,translation,aliases", noenv=True, stage="pipeline"),
            _s("PIPELINE_NAMES_MIN_COUNT", "Мин. count для имён ({female_names}/{male_names})", "number", "10", help="имена с count ниже порога НЕ попадают в справочник полов; 0 — фильтр выключен",
                stage="pipeline"),
        ),
    ),
    # ── Глоссарий ──
    _group("glossary", "Глоссарий",
        _block("ner", "Глоссарий (NER)",
            _s("NER_START", "Начальная глава (ГЛАВЫ)", "number", "", help="сборка глав chapters/*/chapter.txt в память; пусто = с первой", stage="ner", run=True),
            _s("NER_END", "Конечная глава (ГЛАВЫ)", "number", "", help="сборка глав в память; пусто = до последней", stage="ner", run=True),
            _s("NER_PROMPT_FILE", "Промпт-файл (теги pass1/pass2)", "files", "ner_prompt.txt", dir="prompts", ext=(".txt",), stage="ner", run=True),
            _s("NER_CHUNK_SIZE", "Размер чанка, ТОКЕНЫ", "number", "5500", stage="ner"),
            _s("NER_THRESHOLD", "Порог дедупликации (0–1)", "number", "0.75", stage="ner"),
            _s("NER_NGRAM", "N-граммы для латиницы", "number", "3", stage="ner"),
            _s("NER_TWO_PASS", "Двухпроходная схема", "bool", True, stage="ner"),
            _s("NER_KEEP_FIELDS", "Поля в голосование (через запятую)", "text", "",
                help="Пусто = голосуют translation/type/pinyin; notes, context, translated_context не голосуют. Пример: notes,context", stage="ner"),
            _s("NER_CONTEXT_MAX_LEN", "Максимальная длина \"context\"", "number", "300", help="СИМВОЛЫ: context извлекается из чанка — предложение с термином, не от LLM; 0 — выключено", stage="ner"),
            _s("NER_SAVE_INTERVAL", "Интервал сохранения ner.json", "number", "10",
                help="каждые N чанков — промежуточный снапшот глоссария. Возобновление с места остановки убрано: каждый запуск идёт с первого чанка", stage="ner"),
        ),
        _block("ner_check", "Проверка глоссария",
            _s("NER_CHECK_PROMPT_FILE", "Промпт-файл", "files", "ner_check_prompt.txt", dir="prompts", ext=(".txt",), autofile="prompts/ner_check_prompt.txt",
                help="Теги: <prompt_ner_check> — проверка выбранных типов, <prompt_rag> — точечная RAG-проверка; комментарии вне тегов — через #; автоподхват ner_check_prompt.txt", stage="ner_check",
                run=True),
            _s("NER_CHECK_RAG_BUDGET", "RAG: бюджет на термин, ТОКЕНЫ", "number", "22000",
                help="На ОДИН термин: промпт + фрагменты ≤ бюджету (оценка токенов); каждый термин — отдельный LLM-запрос (параллельно, «Потоков (1–16)»); фрагменты — равномерно по книге (FTS5, чанки 350 токенов), влезают в остаток бюджета после промпта",
                stage="ner_check"),
            _s("NER_CHECK_PASSES", "Режимы", "select", "whole", options=("whole", "types", "rag"),
                labels={'whole': "Выбранные типы (одновременно)", 'types': "Выбранные типы (по отдельности)", 'rag': "Точечно по списку (RAG)"},
                help="одновременно — весь список выбранных типов разом (батчи по бюджету); по отдельности — каждый тип отдельно; rag — точечная проверка списка терминов по FTS5-фрагментам книги",
                stage="ner_check"),
            _s("NER_CHECK_BATCH_SIZE", "Бюджет пакета, ТОКЕНЫ", "number", "65536", stage="ner_check"),
            _s("NER_CHECK_COUNT_THRESHOLD", "Порог count", "number", "0", stage="ner_check"),
            _s("NER_CHECK_RAG_TERMS", "RAG: список терминов", "textarea", "", help="Каждый термин с новой строки; тип/перевод подтягиваются из ner.json; нужен режим «rag»", stage="ner_check"),
            _s("NER_CHECK_RAG_SOURCE_TYPE", "RAG: тип исходного файла", "select", "", options=("", "chapter", "translated", "redacted", "polished"),
                help="Из какого файла главы собирается текст книги для FTS5-поиска (сборка в память, файл не пишется)", stage="ner_check"),
            _s("NER_CHECK_SAVE_INTERVAL", "Сохранять каждые N терминов", "number", "0", help="RAG: review-файл сохраняется каждые N терминов (0 = только в конце)", stage="ner_check"),
            _s("NER_CHECK_START", "Начальная глава (ГЛАВЫ)", "number", "", stage="ner_check", run=True),
            _s("NER_CHECK_END", "Конечная глава", "number", "", stage="ner_check", run=True),
            _s("NER_CHECK_TYPES", "", "hidden", "", noenv=True, stage="ner_check"),
            _s("NER_CHECK_FIELDS", "", "hidden", "term,type,translation", noenv=True, stage="ner_check"),
        ),
    ),
    # ── Проверки ──
    _group("checks", "Проверки",
        _block("translate_check", "Проверка перевода",
            _s("TRANSLATE_CHECK_CHECK_TYPE", "Тип файлов глав", "select", "polished", options=("polished", "redacted", "translated"),
                help="polished → сравнивается с redacted (соседняя стадия) и chapter (оригинал); redacted → с translated и chapter; translated → только с chapter", stage="translate_check"),
            _s("TRANSLATE_CHECK_START", "Начальная глава (ГЛАВЫ)", "number", "", stage="translate_check", run=True),
            _s("TRANSLATE_CHECK_END", "Конечная глава", "number", "", stage="translate_check", run=True),
            _s("TRANSLATE_CHECK_EXCLUDE_WORDS", "Слова-исключения (через запятую)", "text", "",
                help="Пусто = ничего не исключается; если задано TRANSLATE_CHECK_EXCLUDE_WORDS в .env — поле заполняется оттуда", stage="translate_check"),
            _s("TRANSLATE_CHECK_NEIGHBOR", "Выбранная Стадия/Предыдущая Стадия (по занимаемому месту)", "text", "",
                help="Ожидаемый ratio с предыдущей стадией и допуск: «1.0±0.05» (напр. polished/redacted); пусто = встроенный дефолт; дефолт в .env — TRANSLATE_CHECK_NEIGHBOR", stage="translate_check"),
            _s("TRANSLATE_CHECK_ORIGINAL", "Выбранная Стадия/Оригинал (по занимаемому месту)", "text", "",
                help="Ожидаемый ratio с оригиналом и допуск: «2.1±0.5» (напр. polished/chapter); пусто = встроенный дефолт; дефолт в .env — TRANSLATE_CHECK_ORIGINAL", stage="translate_check"),
            _s("TRANSLATE_CHECK_REGEXP_CHECKS", "Regexp-проверки (по одной на строку)", "textarea",
                "(?<=\\n)\\s*Глава\\s+(\\d+|\\[Номер\\])\n[\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff\\U00020000-\\U0002ebef【】「」『』]+\n[a-zA-Z]+\n\\A(?!\\s*Глава\\s+(\\d+|\\[Номер\\])).+", rows=4,
                help="Каждая строка — чистый стандартный regexp (Python re, MULTILINE): всё найденное — ошибка, проверяются ВСЕ строки включая заголовок главы; ^/$ — начало/конец СТРОКИ; регистр — inline-флагом (?i); комментариев и кастомных флагов нет («#» — литерал). Предзаполнен полный набор: лишние заголовки «Глава N» (lookbehind (?<=\\n) пропускает заголовок в первой строке файла), иероглифы CJK (все блоки + кавычки 【】「」『』), латиница, «первая строка не заголовок» (negative lookahead \\A(?!…)); «пропуск первого вхождения» своего правила — «(?<=\\n)паттерн». Пусто = без проверок; TRANSLATE_CHECK_REGEXP_CHECKS в .env — переносы строк как «\n»",
                stage="translate_check"),
            _s("TRANSLATE_CHECK_MIN_FILE_SIZE", "Минимальный размер файла (БАЙТЫ)", "number", "3072", help="Файл меньше этого размера — ошибка «слишком мал»; пусто = встроенный дефолт 3072 Б",
                stage="translate_check"),
            _s("TRANSLATE_CHECK_SEQUENCE_CHECK", "Проверять последовательность глав", "bool", True,
                help="Первое число в первой непустой строке должно быть ровно на 1 больше предыдущей главы (N+1); выключено — проверка пропускается", stage="translate_check"),
        ),
        _block("translate_check_llm", "Проверка перевода LLM",
            _s("TRANSLATE_CHECK_LLM_TYPE", "Тип файлов глав", "select", "polished", options=("polished", "redacted", "translated"), stage="translate_check_llm"),
            _s("TRANSLATE_CHECK_LLM_START", "Начальная глава (ГЛАВЫ)", "number", "", stage="translate_check_llm", run=True),
            _s("TRANSLATE_CHECK_LLM_END", "Конечная глава", "number", "", stage="translate_check_llm", run=True),
            _s("TRANSLATE_CHECK_LLM_TWO_PASS", "Второй проход верификации", "bool", False, stage="translate_check_llm"),
            _s("TRANSLATE_CHECK_LLM_CONTEXT_BUDGET", "Бюджет контекста на пакет, ТОКЕНЫ", "number", "25000", stage="translate_check_llm"),
            _s("TRANSLATE_CHECK_LLM_PROMPT_FILE", "Промпт-файл (теги pass1/pass2)", "files", "translate_check_prompt.txt", dir="prompts", ext=(".txt",), stage="translate_check_llm", run=True),
            _s("TRANSLATE_CHECK_LLM_MAX_FIXES_PER_CHAPTER", "Лимит правок на главу (0 = нет)", "number", "0", stage="translate_check_llm"),
            _s("TRANSLATE_CHECK_LLM_MIN_FIX_LENGTH", "Мин. длина правки, СИМВОЛЫ", "number", "0", stage="translate_check_llm"),
            _s("TRANSLATE_CHECK_LLM_MAX_CHANGED_CHARS", "Макс. изменённых символов, СИМВОЛЫ", "number", "0", stage="translate_check_llm"),
        ),
        _block("translate_quality", "Оценка качества",
            _s("TRANSLATE_QUALITY_START", "Начальная глава (ГЛАВЫ)", "number", "", stage="translate_quality", run=True),
            _s("TRANSLATE_QUALITY_END", "Конечная глава", "number", "", stage="translate_quality", run=True),
            _s("TRANSLATE_QUALITY_TYPE", "Тип файлов глав", "select", "polished", options=("chapter", "translated", "redacted", "polished"),
                help="какой файл главы сравнивается с оригиналом: подставляется в {translated_text} промпта, chapter.txt — в {original_text}", stage="translate_quality"),
            _s("TRANSLATE_QUALITY_PROMPT_FILE", "Промпт-файл", "files", "translate_quality_prompt.txt", dir="prompts", ext=(".txt",), autofile="prompts/translate_quality_prompt.txt",
                help="тег <prompt_assessment> (между тегами — комменты); плейсхолдеры {original_text} и {translated_text}; автоподхват translate_quality_prompt.txt", stage="translate_quality",
                run=True),
            _s("TRANSLATE_QUALITY_BUDGET", "Бюджет запроса, СИМВОЛЫ", "number", "200000",
                help="главы (содержимое, промпт НЕ входит); если не влезает — пакет обрезается до целого количества глав (первые диапазона), отсечённые указываются в отчёте", stage="translate_quality"),
        ),
    ),
    # ── Книга и файлы ──
    _group("book", "Книга и файлы",
        _block("epub", "EPUB → главы",
            _s("EPUB_INPUT", "Исходник", "files", "", dir="source", ext=(".epub", ".txt"), stage="epub", run=True),
            _s("EPUB_MODE", "Режим разбивки", "select", "toc", options=("toc", "regex", "chunk"), labels={'toc': "По TOC (epub)", 'regex': "Ручной (regexp)", 'chunk': "По чанкам"},
                help="toc — только epub, по структуре (TOC/spine/h1-h2); regex/chunk — epub ИЛИ txt (epub перегоняется в текст); zip не принимается", stage="epub"),
            _s("EPUB_SPLIT_PATTERNS", "Паттерны разбивки (regexp, по одному на строку)", "textarea", "", rows=4,
                help="ТОЛЬКО режим regexp. Строка считается маркером, если НАЧИНАЕТСЯ с любого паттерна; вся строка становится заголовком главы; чистый стандартный regexp — без комментариев и флагов; пример: «Глава \\d+»; EPUB_SPLIT_PATTERNS в .env — переносы строк как «\\n»",
                stage="epub"),
            _s("EPUB_CHUNK_SIZE", "Размер чанка, ТОКЕНЫ", "number", "7000", help="ТОЛЬКО режим «по чанкам»; оценка токенов", stage="epub"),
            _s("EPUB_CHUNK_MASK", "Маска названия глав", "text", "Chapter {num}",
                help="названия чанков в режиме «по чанкам»; при включённом «Переопределить названия» — названия ВСЕХ глав; {num} — номер; пример: «Часть {num}» → 00000_1_Часть_1…", stage="epub"),
            _s("EPUB_RENAME_CHAPTERS", "Переопределить названия глав маской", "bool", False,
                help="все заголовки глав заменяются на «Маска названия глав» ({num} — номер). Удобно после разбивки по TOC/паттернам: «Chapter 1», «Chapter 2»…", stage="epub"),
            _s("EPUB_TITLE_LIMIT", "Длина названия каталога, СИМВОЛЫ", "number", "50", help="имя папки обрезается; первая строка файла — полный заголовок", stage="epub"),
            _s("EPUB_NUM_OFFSET", "Смещение нумерации (первый номер)", "number", "1", help="875 → первая папка 000_875_… (нули добивают ширину 6)", stage="epub"),
            _s("EPUB_OUTPUT_TYPE", "Тип выходного файла", "select", "chapter", options=("chapter", "translated", "redacted", "polished"),
                labels={'chapter': "chapter.txt", 'translated': "translated.txt", 'redacted': "redacted.txt", 'polished': "polished.txt"},
                help="какой файл создаётся в папке главы (канон артефактов стадий)", stage="epub"),
            _s("EPUB_CLEAN_OUTPUT", "Очистить папки глав перед записью", "bool", False,
                help="Удалить старые каталоги глав (00000_1_…, 00000_2_…) в chapters/ перед записью. Рекомендуется при повторном разборе — иначе старые главы останутся рядом с новыми и могут попасть в конвейер",
                stage="epub"),
        ),
        _block("compile", "Сборка глав",
            _s("COMPILE_MODE", "Режим", "select", "txt", options=("txt", "txt-plain", "epub", "fb2"), labels={'txt': "TXT (Rulate)", 'txt-plain': "TXT", 'epub': "EPUB", 'fb2': "FB2"},
                help="TXT (Rulate) — заголовки «# [Название :|: N]» для загрузки на rulate; TXT — обычный txt без rulate-форматирования", stage="compile"),
            _s("COMPILE_START", "Начальная глава (ГЛАВЫ)", "number", "", stage="compile", run=True),
            _s("COMPILE_END", "Конечная глава", "number", "", stage="compile", run=True),
            _s("COMPILE_SOURCE_TYPE", "Тип файлов глав", "select", "polished", options=("polished", "redacted", "translated", "chapter"), stage="compile"),
            _s("COMPILE_CHUNK_SIZE", "Глав в части", "number", "", help="указано (>0) — диапазон разбивается на части по столько глав (файл на каждую часть), для любого режима; пусто/0 = без разбивки",
                stage="compile"),
            _s("COMPILE_COVER", "Обложка", "files", "", dir="source", ext=(".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"), autofile=("source/cover.jpg", "source/cover.jpeg", "source/cover.png", "source/cover.webp"),
                help="единая обложка для EPUB и FB2; пусто = без обложки; по умолчанию автоподхват cover.jpg/jpeg/png/webp из source/; варианты обложек загружаются через «Файлы»", stage="compile",
                run=True),
            _s("COMPILE_EPUB_META", "Метаданные (YAML)", "files", "metadata.yaml", dir="source", ext=(".yaml", ".yml"), autofile=("source/metadata.yaml",), editable=True,
                help="пусто = source/metadata.yaml; по умолчанию автоподхват metadata.yaml из source/; кнопка «Редактировать» — правка выбранного файла прямо в запуске; другой yaml/yml из source/ выбирается вручную (несколько наборов метаданных)",
                stage="compile", run=True),
            _s("COMPILE_DONATE_FILE", "Файл страницы поддержки", "files", "", dir="source", ext=(".txt",), autofile=("source/donate.txt",), editable=True,
                help="страница поддержки для EPUB/FB2; пусто = без страницы; по умолчанию автоподхват donate.txt из source/; кнопка «Редактировать» — правка выбранного файла прямо в запуске; новый файл загружается через «Файлы» (source/) или «Загрузить» при пустом выборе",
                stage="compile", run=True),
        ),
        _block("wiki", "Вики книги",
            _s("WIKI_START", "Начальная глава (ГЛАВЫ)", "number", "", help="при источнике «Собрать из глав»; пусто = с первой", stage="wiki", run=True),
            _s("WIKI_END", "Конечная глава (ГЛАВЫ)", "number", "", help="при источнике «Собрать из глав»; пусто = до последней", stage="wiki", run=True),
            _s("WIKI_SOURCE", "Источник текста", "select", "chapters", options=("txt", "chapters"), labels={'txt': "Готовый txt", 'chapters': "Собрать из глав"},
                help="txt — готовый скомпилированный файл; «собрать из глав» — склейка chapters/* в память (как в Создании глоссария)", stage="wiki"),
            _s("WIKI_FILE", "Входной txt новеллы (перевод)", "files", "", ext=(".txt",), help="нужен при источнике «Готовый txt»", stage="wiki", run=True),
            _s("WIKI_TYPE", "Тип файлов глав", "select", "polished", options=("polished", "chapter", "translated", "redacted"), help="при источнике «Собрать из глав»", stage="wiki"),
            _s("WIKI_OUTPUT", "Выходной файл", "text", "wiki.md", stage="wiki"),
            _s("WIKI_AS_CHAPTER", "Сохранить как главу", "bool", False, help="вместо файла — дополнительная последняя глава chapters/00000_{N+1}_Wiki_Новеллы/, название «Wiki Новеллы» простым текстом",
                stage="wiki"),
            _s("WIKI_SAVE_TYPE", "Тип файла вики-главы", "select", "polished", options=("translated", "redacted", "polished"),
                help="для «Сохранить как главу вики»; polished — как компиляция по умолчанию; chapter.txt не пишется", stage="wiki"),
            _s("WIKI_FORMAT", "Формат", "select", "md", options=("md", "rulate-md", "rulate-html"), labels={'md': "Обычный Markdown", 'rulate-md': "Rulate (Markdown)", 'rulate-html': "Rulate (HTML)"},
                help="rulate-html: заголовки — <span style=font-size>, списки <ul>, разделители <hr />", stage="wiki"),
            _s("WIKI_TOC", "Оглавление", "bool", True, help="обычный режим; Rulate — всегда без оглавления", stage="wiki"),
            _s("WIKI_TOC_LINKS", "Якоря-ссылки в оглавлении", "bool", True, help="обычный режим; ссылки [термин](#якорь) на статью", stage="wiki"),
            _s("WIKI_PROMPT_FILE", "Промпт (тег <prompt_wiki_article>)", "files", "wiki_prompt.txt", dir="prompts", ext=(".txt",), stage="wiki", run=True),
            _s("WIKI_TOP", "Макс. терминов", "number", "80", stage="wiki"),
            _s("WIKI_MIN_COUNT", "Мин. частота термина", "number", "2", stage="wiki"),
            _s("WIKI_TYPES", "", "hidden", "", noenv=True, stage="wiki"),
            _s("WIKI_CONTEXT_CHUNKS", "Фрагментов контекста на термин", "number", "12", stage="wiki"),
            _s("WIKI_NEAR_DISTANCE", "NEAR-дистанция, ТОКЕНЫ", "number", "64", stage="wiki"),
            _s("WIKI_CHUNK_SIZE", "Размер чанка FTS5, СИМВОЛЫ", "number", "1000", stage="wiki"),
            _s("WIKI_CO_OCCURRENCE_PAIRS", "Пары типов для связей", "text", "Person:Person,Person:Organisation,Person:Artifact", stage="wiki"),
            _s("WIKI_CO_OCCURRENCE_TOP", "Связей на термин", "number", "5", stage="wiki"),
        ),
        _block("batch_replace", "Массовые замены",
            _s("BATCH_REPLACE_REPLACEMENTS", "Regexp-замены (по одной на строку)", "textarea", "", rows=5,
                help="Формат: паттерн -> замена (чистый стандартный regexp, Python re, MULTILINE: «^»/«$» — начало/конец СТРОКИ). Пустая правая часть — УДАЛЕНИЕ: «<div>.*?</div> ->». Пробелы в паттерне значимы; регистр и прочие режимы — стандартными inline-флагами ((?i)…); комментариев и кастомных флагов нет («#» — литерал в паттерне). Примеры: «Глава \\d+ -> Глава №\\g<0>», «(?i)бессмертный -> Бессмертный», «\\s+ -> » (сжать пробелы), «^  ->» (отступ строки), «^(第\\d+章.*)\\n(?=\\1$) ->» (строка-дубликат заголовка главы). BATCH_REPLACE_REPLACEMENTS в .env — переносы строк как «\\n»",
                stage="batch_replace", run=True),
            _s("BATCH_REPLACE_TYPE", "Тип файлов глав", "select", "polished", options=("polished", "redacted", "translated", "chapter"), stage="batch_replace"),
            _s("BATCH_REPLACE_START", "Начальная глава (ГЛАВЫ)", "number", "", stage="batch_replace", run=True),
            _s("BATCH_REPLACE_END", "Конечная глава", "number", "", stage="batch_replace", run=True),
        ),

    ),
)

# Плоские индексы: порядок реестра — единственный порядок показа.
SETTINGS: tuple = tuple(s for g in GROUPS for b in g.blocks for s in b.settings)
BY_KEY: dict = {s.key: s for s in SETTINGS}
BY_BLOCK: dict = {b.id: b.settings for g in GROUPS for b in g.blocks}
BLOCK_TITLES: dict = {b.id: b.title for g in GROUPS for b in g.blocks}
STAGES: tuple = tuple(dict.fromkeys(s.stage for s in SETTINGS if s.stage))
#: блоки общего LLM-конфига: он один на весь конвейер, стадийных ключей нет
LLM_BLOCKS: tuple = ("llm_conn", "llm_net", "llm_run", "llm_reasoning")
#: блоки настроек самого веб-сервера: их читает web/main.py, профили LLM их
#: не касают (профиль хранит только LLM-ключи)
SERVER_BLOCKS: tuple = ("server_net", "server_run")
#: имена LLM-полей, которые раньше были в формах стадий
LLM_FORM_NAMES = frozenset({"host", "model", "api_key", "temperature", "timeout",
                            "stream_timeout", "max_retries", "retries",
                            "max_tokens", "threads", "jobs", "retry_empty"})
#: старые имена полей стадии → общая настройка
LLM_ALIAS = {"jobs": "threads", "retries": "max_retries"}
#: что из LLM-конфига уходит в argv стадии (исторические имена полей,
#: которые понимает их CLI)
STAGE_LLM_FIELDS: dict = {
    "epub": (),
    "translate_check": (),
    "compile": (),
    "pipeline": ("host", "model", "api_key", "jobs", "threads", "timeout", "max_retries", "temperature",),
    "ner": ("host", "model", "api_key", "threads", "temperature", "retries", "timeout",),
    "ner_check": ("host", "model", "api_key", "threads", "temperature", "max_tokens", "timeout", "max_retries",),
    "translate_check_llm": ("host", "model", "api_key", "temperature", "max_retries", "timeout", "retry_empty", "threads",),
    "translate_quality": ("host", "model", "api_key", "temperature", "max_retries", "timeout",),
    "wiki": ("host", "model", "api_key", "temperature", "retries", "timeout", "threads",),
    "batch_replace": (),
}



def _now_stamp() -> str:
    """Метка времени профиля: ISO без микросекунд."""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def groups() -> tuple:
    """Субвкладки со блоками — в том порядке, в котором их рисовать."""
    return GROUPS


def stage_fields(stage: str) -> tuple:
    """Все поля формы стадии в порядке реестра (настройки + параметры запуска)."""
    return tuple(s for s in SETTINGS if s.stage == stage)


def settings_of(stage: str = "") -> tuple:
    """Настройки (пусто — общие: LLM, рассуждения, web); без параметров запуска."""
    return tuple(s for s in SETTINGS if s.stage == stage and not s.run)


def form_fields(stage: str) -> list:
    """Поля формы стадии: metadata и дефолты — из реестра, в порядке реестра.

    hidden-настройки (NER_THRESHOLD/NER_NGRAM конвейера) в форму не идут: их
    правят на «Настройках», стадия берёт значение из эффективного конфига.
    У LLM-стадии первым идёт поле выбора профиля — тоже noenv-состояние.
    """
    out = [s.form_field() for s in stage_fields(stage) if not s.hidden]
    if is_llm_stage(stage):
        out.insert(0, profile_field())
    return out


def is_llm_stage(stage: str) -> bool:
    """Стадия работает с моделью (значит, у неё есть выбор профиля LLM)."""
    return bool(STAGE_LLM_FIELDS.get(stage))


def _coerce(setting: Setting, value):
    """Значение по типу настройки: number → int/float, bool → bool,
    textarea → текст (литеральный «\\n» разворачивается обратно)."""
    raw = value if isinstance(value, str) else str(value)
    if setting.type == "number" and raw.strip() != "":
        try:
            return float(raw) if "." in raw else int(raw)
        except ValueError:
            return value
    if setting.type == "bool":
        if isinstance(value, bool):
            return value
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if setting.type == "textarea":
        return raw.replace(NL_LIT, NL).rstrip(NL)
    return raw.strip()


def defaults(stage: str = "") -> dict:
    """Значения по умолчанию: имя поля → дефолт (числа — числами).

    Из реестра читают и формы запусков, и argparse скриптов: «number» с
    «7000» строкой заставил бы CLI сравнивать «1 <= "4"».
    """
    return {s.name: _coerce(s, s.default) for s in settings_of(stage)}


def llm_settings() -> tuple:
    """Общие LLM-настройки конвейера (подключение, сеть, рассуждения)."""
    return tuple(s for b in LLM_BLOCKS for s in BY_BLOCK[b])


def stage_values(stage: str) -> dict:
    """Эффективные значения стадии: реестр → общий .env → os.environ.

    Так читают конфиг скрипты-исполнители и конвейер: никакого своего
    чтения <СТАДИЯ>_* и своих словарей дефолтов.
    """
    return {s.name: _coerce(s, effective(s.key)) for s in stage_fields(stage)}


def llm_values(profile: str = "") -> dict:
    """LLM-настройки конвейера (эффективные): подключение, сеть и рассуждения.

    Пустой профиль — General (значения общего .env), иначе значения профиля
    лежат поверх общего файла."""
    layered = layered_values(profile)
    return {s.name: _coerce(s, layered.get(s.key) or s.default)
            for s in llm_settings()}


def web_settings() -> tuple:
    """Настройки самого веб-сервера (блоки server_*)."""
    return tuple(s for b in SERVER_BLOCKS for s in BY_BLOCK[b])


def web_values() -> dict:
    """Эффективный конфиг запуска сервера: реестр → общий .env → os.environ.

    Единственный источник дефолтов WEB_*: лаунчер не держит свой второй
    список. Флаг командной строки перекрывает всё — именно он в Docker
    выставляет --host 0.0.0.0, и никакая правка в браузере его не перешибёт."""
    return {s.name: _coerce(s, effective(s.key)) for s in web_settings()}


def apply_cli_defaults(parser, stage: str):
    """Дефолты argparse стадии — из реестра (один источник с формой web).

    Файловые поля реестра хранят ИМЯ файла (его показывает select SPA), а
    скрипты работают путём относительно папки проекта — префикс добавляется
    здесь же, из metadata поля. Возвращает parser, чтобы вешать вызов на
    построение.
    """
    for s in stage_fields(stage):
        v = s.default
        # пустой дефолт реестра = «не задано»: не затираем собственную
        # семантику unset у скрипта (None/0), иначе argparse с type=int
        # пытается привести строковый "" к числу и падает на parse_args
        if v == "":
            continue
        if s.type == "files" and s.dir and "/" not in str(v):
            v = f"{s.dir}/{v}"
        parser.set_defaults(**{s.name: v})
    return parser


def env_key(stage: str, name: str) -> str:
    """Ключ .env поля стадии: всегда с префиксом <STAGE>_<FIELD>."""
    s = next((x for x in SETTINGS
              if x.stage == stage and x.name == str(name).lower()), None)
    return s.key if s else ""


def env_file() -> str | None:
    """Путь общего .env (WEB_ENV_FILE → корень репо → cwd)."""
    return system_env_file()


def sanitize(setting: Setting, value) -> str:
    """Значение одной строкой .env: strip; у textarea переносы — литералом.

    Решётку вне кавычек парсер считает комментарием, поэтому значение с «#»
    оборачивается в кавычки — иначе из формы вырос бы новый ключ.
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    s = "" if value is None else str(value).strip()
    if setting.type == "textarea":
        s = s.replace(NL, NL_LIT)
    s = s.replace(NL, " ").replace(chr(13), " ")
    quoted = len(s) >= 2 and s[0] == s[-1] and s[0] in "'\""
    if "#" in s and not quoted:
        s = '"' + s.replace('"', '\"') + '"'
    return s


def file_values() -> dict:
    """Что выставлено в общем .env."""
    path = env_file()
    return parse_dotenv(path) if path else {}


def layered_values(profile: str = "") -> dict:
    """Эффективный конфиг: общий .env → выбранный профиль LLM → окружение.

    Профиль — выбор пользователя для конкретного запуска: web-слой отдаёт его
    подпроцессу переменной NM_LLM_PROFILE. Пустой или general — только общий
    файл. Окружение процесса остаётся последним рубежом деплоя."""
    vals = file_values()
    pid = (profile or os.environ.get(PROFILE_ENV, "")).strip()
    if pid and pid != PROFILE_DEFAULT:
        prof = profile_get(pid)
        if prof:
            vals.update({k: str(v) for k, v in (prof.get("values") or {}).items()
                         if str(v).strip()})
    return env_overlay(vals, [s.key for s in SETTINGS])


def effective(key: str) -> object:
    """Эффективное значение настройки: реестр → общий .env → профиль → os.environ.

    Пустое значение в файле — не переопределение: настройка остаётся на
    встроенном дефолте (пустая строка в конфиге книги больше ничего не значит).
    """
    s = BY_KEY.get(key)
    if s is None:
        return str(layered_values().get(key, "")).strip()
    raw = layered_values().get(key, "")
    return _coerce(s, raw if str(raw).strip() else s.default)


def write_values(values: dict) -> list:
    """Перезаписать общий .env значениями реестра (ключ → значение).

    Ключи — именно ключи .env (`NER_CHUNK_SIZE`), а не имена полей: `chunk_size`
    живёт в четырёх стадиях и по имени его не отличить. Файл машиночитаемый:
    шапка и только выставленные ключи в порядке реестра. Пустое значение снимает
    ключ, без ключей файл удаляется: «наследует встроенный дефолт» не должно
    быть строкой в файле.
    """
    path = env_file()
    if not path:
        raise RuntimeError("путь общего .env не определён (WEB_ENV_FILE)")
    from .common import atomic_write
    lines = ["# NovelMaestro — общие настройки.",
             "# Значения по умолчанию зашиты в реестр core/settings.py;",
             "# здесь — только то, что изменено. Файл перезаписывается со",
             "# страницы «Настройки»; отдельного файла книги больше нет."]
    stored = []
    for s in SETTINGS:
        if s.noenv or s.run:
            continue
        v = sanitize(s, values.get(s.key, ""))
        if v:
            lines.append(f"{s.key}={sanitize(s, v)}")
            stored.append(s.key)
    if stored:
        atomic_write(str(path), "\n".join(lines) + "\n")
    elif Path(path).is_file():
        try:
            Path(path).unlink()
        except OSError as exc:
            log.debug("пустой общий .env не удалён: %s", exc)
    return stored


# ════════════════════════════════════════════════════════════════════
# профили LLM: именованные наборы настроек работы с моделью
# ════════════════════════════════════════════════════════════════════
#
# Профиль — полный набор LLM-настроек (сервер, модель, ключ, потоки, таймауты,
# ретраи, температура, рассуждения). Выбирается КАЖДОЙ LLM-стадией своего
# проекта: перевод может идти на облачной модели, глоссарий — на домашней.
# Leжат в ОДНОМ файле рядом с общим .env; в файле — только те ключи, которые
# профиль переопределяет, остальное профиль наследует от General.
#
# General — встроенный профиль: его значения и есть обычный общий .env, поэтому
# данных в двух местах нет и без выбранного профиля система ведёт себя ровно
# как до профилей.
# ════════════════════════════════════════════════════════════════════

#: имя поля формы LLM-стадии: какой профиль её обслуживает
PROFILE_FIELD = "profile"
#: id встроенного профиля — его значения живут в общем .env
PROFILE_DEFAULT = "general"
#: название встроенного профиля (оно же — имя по умолчанию)
PROFILE_DEFAULT_TITLE = "General"
#: переменная окружения подпроцесса: какой профиль выбрал запуск
PROFILE_ENV = "NM_LLM_PROFILE"
#: файл профилей (рядом с общим .env)
PROFILES_NAME = "llm_profiles.json"


def profiles_file() -> str:
    """Путь файла профилей: рядом с общим .env, нет конфига — корень репо."""
    path = env_file()
    if path:
        return str(Path(path).parent / PROFILES_NAME)
    # core/settings.py лежит в core/ — родитель и есть корень репозитория
    return str(Path(__file__).resolve().parent.parent / PROFILES_NAME)


def profile_field() -> dict:
    """Поле выбора профиля LLM — первое поле формы каждой LLM-стадии.

    Профиль выбирает стадия, а не проект: перевод и глоссарий одной книги могут
    делать разные серверы. Значение — состояние браузера (noenv), оно не ездит
    в .env и не попадает в argv: `with_llm` разбирает его по LLM-полям стадии,
    а подпроцесс получает NM_LLM_PROFILE.
    """
    profs = profiles()
    return {
        "name": PROFILE_FIELD,
        "label": "Профиль LLM",
        "type": "select",
        "default": PROFILE_DEFAULT,
        "options": tuple(p["id"] for p in profs),
        "labels": {p["id"]: p["name"] for p in profs},
        "noenv": True,
        "help": "Настройки модели для ЭТОЙ стадии: сервер, модель, потоки и "
                "рассуждения выбранного профиля. Профили задаются на "
                "«Настройках»; General — значения общего конфига.",
    }


def profile_slug(name: object, taken=()) -> str:
    """Устойчивый id профиля: имя остаётся человеческим, ключ — ascii-safe.

    Русское имя в slug не сворачивается, поэтому ему выдаётся p<номер>; id
    пишется в файл один раз и больше не меняется — на него ссылается выбор
    стадии и переменная окружения подпроцесса.
    """
    raw = unicodedata.normalize("NFKD", str(name or "").strip().lower())
    slug = re.sub(r"[^a-z0-9]+", "_", raw.encode("ascii", "ignore").decode()).strip("_")
    if not slug:
        nums = [int(m.group(1)) for t in taken
                if (m := re.fullmatch(r"p(\d+)", str(t)))]
        slug = f"p{max(nums, default=0) + 1}"
    return slug


def profiles_read() -> list:
    """Профили с диска (без General): [{id,name,values,created,updated}, ...]."""
    path = profiles_file()
    if not Path(path).is_file():
        return []
    try:
        data = json.loads(read_text_safe(path) or "[]")
    except (OSError, ValueError) as exc:
        log.warning("файл профилей LLM не читается: %s (%s)", path, exc)
        return []
    if isinstance(data, dict):
        data = data.get("profiles")
    out, taken = [], []
    for raw in data or []:
        if not isinstance(raw, dict):
            continue
        pid = str(raw.get("id") or "").strip() or profile_slug(raw.get("name"), taken)
        if pid == PROFILE_DEFAULT:
            continue
        taken.append(pid)
        out.append({
            "id": pid,
            "name": str(raw.get("name") or pid).strip(),
            "values": {str(k).strip().upper(): str(v)
                       for k, v in (raw.get("values") or {}).items()},
            "created": str(raw.get("created") or ""),
            "updated": str(raw.get("updated") or ""),
        })
    return out


def profiles_write(profiles: list) -> str:
    """Записать профили атомарной заменой: порядок списка = порядок в UI.

    Последний удалённый профиль убирает файл совсем: пустой файл — не
    состояние, а мусор (тот же закон, что у общего .env)."""
    from .common import atomic_write
    path = profiles_file()
    if not profiles:
        if Path(path).is_file():
            try:
                Path(path).unlink()
            except OSError as exc:
                log.debug("пустой файл профилей не удалён: %s", exc)
        return path
    body = {"profiles": [{"id": p["id"], "name": p.get("name") or p["id"],
                          "values": p.get("values") or {},
                          "created": p.get("created") or "",
                          "updated": p.get("updated") or ""}
                        for p in profiles]}
    atomic_write(path, json.dumps(body, ensure_ascii=False, indent=2) + "\n")
    return path


def profiles() -> list:
    """Все профили: встроенный General первым, остальные — с диска."""
    builtin = {"id": PROFILE_DEFAULT, "name": PROFILE_DEFAULT_TITLE,
               "builtin": True, "values": {}}
    return [builtin] + [dict(p, builtin=False) for p in profiles_read()]


def profile_get(profile_id: str) -> dict | None:
    """Профиль по id; пустой id и general — встроенный профиль."""
    pid = str(profile_id or "").strip()
    if not pid or pid == PROFILE_DEFAULT:
        return {"id": PROFILE_DEFAULT, "name": PROFILE_DEFAULT_TITLE,
                "builtin": True, "values": {}}
    return next((p for p in profiles_read() if p["id"] == pid), None)


def profile_values(profile_id: str) -> dict:
    """Значения профиля как есть (ключ .env → значение, без масок)."""
    return dict((profile_get(profile_id) or {}).get("values") or {})


def profile_display(profile_id: str) -> dict:
    """Значения профиля для интерфейса.

    General — эффективные значения конфига (реестр → файл → окружение);
    остальные — только их переопределения, пустое поле значит «наследует
    General». Секрет всегда под маской, настоящий ключ в SPA не ездит."""
    raw = profile_values(profile_id)
    builtin = str(profile_id or "").strip() in ("", PROFILE_DEFAULT)
    out = {}
    for s in llm_settings():
        val = str(raw.get(s.key, ""))
        if builtin:
            out[s.key] = display_value(s)
        else:
            out[s.key] = ("••••" if val.strip() else "") if s.secret else val
    return out


def profile_create(name: str, values: dict | None = None) -> dict:
    """Создать профиль; пустой набор — наследует всё от General."""
    name = str(name or "").strip()
    if not name:
        raise ValueError("имя профиля обязательно")
    stored = profiles_read()
    if any(p["name"].casefold() == name.casefold() for p in stored):
        raise ValueError(f"профиль с именем «{name}» уже есть")
    now = _now_stamp()
    prof = {"id": profile_slug(name, [p["id"] for p in stored]), "name": name,
            "values": _profile_clean(values), "created": now, "updated": now}
    profiles_write(stored + [prof])
    log.info("профиль LLM создан: %s (%s)", prof["name"], prof["id"])
    return dict(prof, builtin=False)


def profile_rename(profile_id: str, name: str) -> dict:
    """Переименовать профиль (id остаётся: на него ссылается выбор стадии)."""
    name = str(name or "").strip()
    if not name:
        raise ValueError("имя профиля обязательно")
    if profile_id == PROFILE_DEFAULT:
        raise ValueError("встроенный профиль General не переименовывается")
    stored = profiles_read()
    if any(p["name"].casefold() == name.casefold() and p["id"] != profile_id
           for p in stored):
        raise ValueError(f"профиль с именем «{name}» уже есть")
    out = None
    for p in stored:
        if p["id"] == profile_id:
            p["name"] = name
            p["updated"] = _now_stamp()
            out = p
    if out is None:
        raise ValueError(f"профиль не найден: {profile_id}")
    profiles_write(stored)
    return dict(out, builtin=False)


def profile_delete(profile_id: str) -> bool:
    """Удалить профиль; General удалить нельзя — это значения общего .env."""
    if profile_id == PROFILE_DEFAULT:
        raise ValueError("встроенный профиль General не удаляется")
    stored = profiles_read()
    left = [p for p in stored if p["id"] != profile_id]
    if len(left) == len(stored):
        return False
    profiles_write(left)
    return True


def profile_save_values(profile_id: str, values: dict) -> dict:
    """Сохранить значения профиля: только LLM-ключи, пустое снимает переопределение.

    Профиль хранит только то, что в нём изменено: всё остальное он берёт из
    General, поэтому смена общего сервера доходит до профилей сама.
    """
    if profile_id == PROFILE_DEFAULT:
        write_values(values or {})
        return {"id": PROFILE_DEFAULT, "name": PROFILE_DEFAULT_TITLE,
                "builtin": True, "values": {}}
    stored = profiles_read()
    prof = next((p for p in stored if p["id"] == profile_id), None)
    if prof is None:
        raise ValueError(f"профиль не найден: {profile_id}")
    prof["values"] = _profile_clean(values, prof.get("values") or {})
    prof["updated"] = _now_stamp()
    profiles_write(stored)
    return dict(prof, builtin=False)


def _profile_clean(values: dict | None, old: dict | None = None) -> dict:
    """Только LLM-ключи реестра, непустые, в порядке реестра.

    Маска «••••» — не значение: секрет остаётся тем, что уже сохранено
    (пустое же значение переопределение снимает — профиль наследует General)."""
    out = {}
    old = old or {}
    for s in llm_settings():
        val = sanitize(s, (values or {}).get(s.key, ""))
        if not val or val == "••••":
            if s.secret and s.key in old:
                out[s.key] = old[s.key]
            continue
        out[s.key] = val
    return out


def profiles_payload() -> list:
    """Профили для SPA: [{id,name,builtin,values:{KEY:значение}}, ...].

    General — встроенный (его значения и есть общий .env); у остальных
    значений показываются только переопределения профиля, секреты — под маской."""
    return [{"id": p["id"], "name": p["name"], "builtin": bool(p.get("builtin")),
             "values": profile_display(p["id"])} for p in profiles()]


def llm_form(stage: str, profile: str = "") -> dict:
    """LLM-значения в именах полей стадии (jobs ← THREADS, retries ← MAX_RETRIES).

    Сборка argv стадии не изменилась: она читает те же имена полей, просто
    значения приходят из профиля (или общего конфига), а не из формы.
    """
    vals = llm_values(profile)
    return {n: vals.get(LLM_ALIAS.get(n, n), "")
            for n in STAGE_LLM_FIELDS.get(stage, ())}


def with_llm(stage: str, form: dict) -> dict:
    """Форма запуска поверх LLM-конфига выбранного профилем.

    Своих LLM-полей в форме запусков нет, но что бы в ней ни лежало (старый
    кэш браузера, старый .env книги), сервер/модель/ключ/потоки/рассуждения
    перезаписываются значениями профиля: «свой сервер у стадии» — это выбор
    профиля, а не набор полей. Профиль читается из поля формы `profile`.
    """
    out = dict(form or {})
    out.update(llm_form(stage, str(out.get(PROFILE_FIELD) or "")))
    return out


def display_value(setting: Setting) -> object:
    """Значение настройки для интерфейса: эффективное; секреты — «••••»."""
    if setting.secret:
        return "••••" if str(layered_values().get(setting.key, "")).strip() else ""
    return effective(setting.key)


def block_payload(block_id: str) -> dict:
    """Одна карточка реестра для SPA: {id, title, fields, values}.

    Поля — в формате форм стадий (name/label/type/…), значения — эффективные,
    в тех же именах. Парольное поле отдаётся как «••••»: значение секрета в SPA
    не ездит, нужен только признак «задано».
    """
    fields, values = [], {}
    for s in BY_BLOCK[block_id]:
        val = display_value(s)
        # key — ключ .env: имена полей стадий не уникальны (chunk_size у
        # четырёх стадий), сохранять страница должна по ключу
        fields.append(dict(s.form_field(), value=val, key=s.key))
        values[s.name] = val
    return {"id": block_id, "title": BLOCK_TITLES[block_id],
            "fields": fields, "values": values}


def groups_payload() -> list:
    """Реестр для SPA: субвкладки → блоки → поля с текущими значениями."""
    return [{"id": g.id, "title": g.title,
             "blocks": [{"id": b.id, "title": b.title,
                         # профиль LLM подставляется только в свои блоки:
                         # веб-сервер живёт в этой же субвкладке, но к LLM
                         # отношения не имеет
                         "llm": b.id in LLM_BLOCKS,
                         "fields": [dict(s.form_field(), value=display_value(s),
                                         key=s.key)
                                    for s in b.settings]}
                        for b in g.blocks]}
            for g in GROUPS]

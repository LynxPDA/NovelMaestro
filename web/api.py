#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api.py — REST-хендлеры web-бэкэнда: фасад над доменными модулями.

Хендлеры живут по доменам: api_common (служебное, ctx, кешы и константы,
сессия и вход), api_projects (пульт и проекты), api_files (файлы),
api_glossary (глоссарий и review), api_env (настройки из реестра,
промпты, metadata), api_assets (обложка W6, логи M8, отчёты W7), api_stage (запуски и
стадии) и api_templates (шаблоны). Имена роутов и таблицы web/README.md
не меняются.

Порядок регистрации роутов исторический и сохраняется: hub → files → m7 →
logs → check → templates → jobs.

Реэкспорт ниже нужен не для красоты: тесты и web/main.py работают через
`api.<имя>`, а общие состояния (кеши статистики и опций стадий, имена файлов
предпросмотра) остаются одними и теми же объектами — мутация через фасад
попадает в доменный модуль.
"""
from __future__ import annotations

from web import (api_assets, api_common, api_env, api_files, api_glossary,
                 api_projects, api_stage, api_templates)
from web.api_common import (  # noqa: F401  фасад
    _CACHE_LOADED,
    _STATS_CACHE,
    _close_multipart_fields,
    _multipart_fields,
    FILE_TEXT_LIMIT,
    _OPTIONS_CACHE,
    EPUB_PREVIEW_FILE,
    PREVIEW_REQUEST_FILE,
)
from web.api_projects import (  # noqa: F401  фасад
    _ensure_stats_cache,
    _dashboard,
)
from web.api_assets import (  # noqa: F401  фасад
    _parse_check_report,
)
from web.api_stage import (  # noqa: F401  фасад
    _jobs_get,
)
from web.server import Router


def register(router: Router, host: str) -> None:
    """Регистрирует все хендлеры web-бэкэнда."""
    router.add("GET", "/api/session", api_common._session)
    router.add("POST", "/api/login", api_common._login)
    router.add("POST", "/api/logout", api_common._logout)
    api_projects._register_hub(router)
    api_files._register_files(router)
    api_glossary._register_m7(router)
    api_env._register_settings(router)
    api_assets._register_logs(router)
    api_assets._register_check(router)
    api_templates._register_templates(router)
    api_stage._register_jobs(router)

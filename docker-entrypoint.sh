#!/bin/sh
# NovelMaestro docker-entrypoint: подготовка каталогов данных перед
# запуском web-сервера (работает от root, затем переключается на app).
#
# Что чинит:
#  1. Каталоги ./projects, ./templates, ./web/job_logs — docker создаёт
#     bind-mount каталоги от root, если их нет на хосте; возвращаем
#     владельца app (uid 1000), иначе сервер не сможет в них писать.
#  2. Шаблоны из образа: bind-mount ./templates ПЕРЕКРЫВАЕТ содержимое
#     образа, поэтому встроенные части (набор General/ и шаблон
#     .env.example) кладёт в него программа — при каждом старте, а не
#     только на первом: иначе обновление образа оставляло бы старые
#     промпты. Наборы пользователя в templates/ не трогаются.
#  3. Системный .env: в томе projects (WEB_ENV_FILE=/app/projects/.env,
#     правится вкладкой «Настройки», переживает обновление образа);
#     первый старт — сид из /app/templates-dist/.env.example.
#
# Конфиг: environment в compose приоритетнее .env-файлов (канон
# «окружение > файл»).

set -eu

# 1. Владелец каталогов данных — app (uid 1000), как в Dockerfile.
for d in /app/projects /app/templates /app/web/job_logs; do
    mkdir -p "$d"
    chown -R app:app "$d"
done

# 2. Встроенные шаблоны образа — в смонтированный каталог шаблонов.
#    Первый запуск (каталог пуст) — полный снимок, как и раньше; дальше
#    синхронизация по файлам: General/ и .env.example приводятся к образу,
#    свои наборы остаются. Сбой синхронизации — предупреждение, а не
#    отказ стартовать: сервер работает и со старыми шаблонами.
if [ -d /app/templates-dist ]; then
    if ! gosu app python3 -m core.projects sync-templates \
            /app/templates /app/templates-dist; then
        echo "⚠ Не удалось обновить встроенные шаблоны из образа —" \
             "используются те, что лежат в смонтированном каталоге" >&2
    fi
    chown -R app:app /app/templates
fi

# 3. Системный .env — в постоянном томе projects (WEB_ENV_FILE в
#    образе указывает на этот файл): правки вкладки «Настройки»
#    переживают обновление образа. Первый старт — сид из снимка шаблонов;
#    ключи LLM внутри — chmod 600, как у .web_secret.
if [ ! -f /app/projects/.env ] && [ -f /app/templates-dist/.env.example ]; then
    cp /app/templates-dist/.env.example /app/projects/.env
    chown app:app /app/projects/.env
    chmod 600 /app/projects/.env
fi

# 4. Запуск сервера от app (не root).
exec gosu app "$@"

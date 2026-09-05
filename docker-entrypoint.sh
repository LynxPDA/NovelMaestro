#!/bin/sh
# NovelMaestro docker-entrypoint: подготовка каталогов данных перед
# запуском web-сервера (работает от root, затем переключается на app).
#
# Что чинит:
#  1. Каталоги ./projects, ./templates, ./web/job_logs — docker создаёт
#     bind-mount каталоги от root, если их нет на хосте; возвращаем
#     владельца app (uid 1000), иначе сервер не сможет в них писать.
#  2. Шаблоны из образа (General/, .env.example): bind-mount ./templates
#     ПЕРЕКРЫВАЕТ содержимое образа — если хостовый каталог пуст,
#     копируем шаблоны из /app/templates-dist (снимок образа), чтобы
#     «Шаблоны» не оказались пустыми.
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

# 2. Шаблоны из образа при пустом хостовом ./templates.
if [ ! -d /app/templates/General ] && [ -d /app/templates-dist ]; then
    cp -a /app/templates-dist/. /app/templates/
    chown -R app:app /app/templates
fi

# 3. Системный .env — в постоянном томе projects (WEB_ENV_FILE в
#    образе указывает на этот файл): правки вкладки «Настройки»
#    переживают обновление образа. Первый старт — сид из снимка шаблонов.
if [ ! -f /app/projects/.env ] && [ -f /app/templates-dist/.env.example ]; then
    cp /app/templates-dist/.env.example /app/projects/.env
    chown app:app /app/projects/.env
fi

# 4. Запуск сервера от app (не root).
exec gosu app "$@"

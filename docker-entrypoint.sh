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

# 3. Запуск сервера от app (не root).
exec gosu app "$@"

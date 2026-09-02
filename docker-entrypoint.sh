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
#  3. Конфиг .env: bind-mount ./env:/app/env.d — docker сам создаёт
#     папку env/, если её нет; файл .env внутри создаём из шаблона
#     (./env/.env — хостовый, правится снаружи; вкладка «Настройки»
#     пишет в него через симлинк /app/.env).

set -eu

# 1. Владелец каталогов данных — app (uid 1000), как в Dockerfile.
for d in /app/projects /app/templates /app/web/job_logs /app/env.d; do
    mkdir -p "$d"
    chown -R app:app "$d"
done

# 2. Шаблоны из образа при пустом хостовом ./templates.
if [ ! -d /app/templates/General ] && [ -d /app/templates-dist ]; then
    cp -a /app/templates-dist/. /app/templates/
    chown -R app:app /app/templates
fi

# 3. Конфиг .env: создаём из шаблона, если файла ещё нет (docker
# смонтировал бы ПУСТУЮ ПАПКУ вместо файла — поэтому монтируем папку
# env.d, а не файл). Пользовательский .env не затираем.
if [ ! -f /app/env.d/.env ] && [ -f /app/templates-dist/.env.example ]; then
    cp /app/templates-dist/.env.example /app/env.d/.env
    chown app:app /app/env.d/.env
fi
# Симлинк /app/.env → /app/env.d/.env: код читает .env из корня репо;
# atomic_write разрешает симлинк (realpath), поэтому вкладка «Настройки»
# пишет прямо в хостовый ./env/.env.
if [ ! -e /app/.env ]; then
    ln -s /app/env.d/.env /app/.env
fi

# 4. Запуск сервера от app (не root).
exec gosu app "$@"

# NovelMaestro — web-сервер (stdlib http.server) + LLM-скрипты как subprocess.
#
# Сборка и запуск:
#   docker build -t novelmaestro .
#   docker run -d -p 8756:8756 -v ./projects:/app/projects novelmaestro
# Или проще — docker compose (см. docker-compose.yml и packaging/README.md).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8 \
    PIP_NO_CACHE_DIR=1

# gosu — переключение root → app в docker-entrypoint.sh (chown каталогов
# данных, созданных docker от root, требует root-прав).
RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

# Зависимости: requests (LLM-клиент), tqdm (прогресс);
# pyahocorasick — опционально (ускоряет NER; без него — regex-fallback).
RUN pip install --no-cache-dir requests tqdm pyahocorasick

# Не-root пользователь с uid=1000 (типичный первый пользователь Linux;
# Docker Desktop виртуализует владельца). Файлы, созданные контейнером
# в bind-mount ./projects, принадлежат uid 1000 — хост-пользователь
# сможет править их вручную. Иной uid хоста: поменяйте -u 1000 здесь.
RUN useradd -m -u 1000 app

WORKDIR /app

# Код приложения (проекты/тесты/логи исключены через .dockerignore).
COPY . .

# Снимок шаблонов образа: bind-mount ./templates:/app/templates перекрывает
# содержимое образа — entrypoint копирует General/ и .env.example в пустой
# хостовый каталог (иначе «Шаблоны» будут пустыми).
RUN mkdir -p /app/templates-dist \
    && cp -a /app/templates/. /app/templates-dist/ \
    && chown -R app:app /app/templates-dist

# Конфиг по умолчанию: копия templates/.env.example → /app/.env
# (значения по умолчанию; корневой .env с секретами в образ НЕ входит —
# .dockerignore). Реальные HOST/API_KEY/MODEL — переменными окружения
# в compose (environment, приоритет окружения над .env-файлом).
RUN cp /app/templates/.env.example /app/.env && chown app:app /app/.env

# USER app НЕ ставится: контейнер стартует от root, docker-entrypoint.sh
# делает chown каталогов данных и сам переключается на app (gosu).
RUN chown -R app:app /app

EXPOSE 8756

# Здоровье: GET /api/session (без сети; работает и с --auth).
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8756/api/session', timeout=4)"

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 0.0.0.0 — дефолт web/main.py; порт можно переопределить WEB_PORT.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python3", "web/main.py", "--host", "0.0.0.0"]

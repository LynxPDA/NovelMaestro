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

# Зависимости: requests (LLM-клиент), tqdm (прогресс);
# pyahocorasick — опционально (ускоряет NER; без него — regex-fallback).
RUN pip install --no-cache-dir requests tqdm pyahocorasick

WORKDIR /app

# Код приложения (проекты/тесты/логи исключены через .dockerignore).
COPY . .

# Контейнер работает от root: bind-mount ./projects на Linux-хосте
# наследует владельца хост-папки, поэтому non-root пользователь
# ломал бы монтирование (см. packaging/README.md → «Безопасность»).

EXPOSE 8756

# Здоровье: GET /api/session (без сети; работает и с --auth).
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8756/api/session', timeout=4)"

# 0.0.0.0 — дефолт web/main.py; порт можно переопределить WEB_PORT.
CMD ["python3", "web/main.py", "--host", "0.0.0.0"]

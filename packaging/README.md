# Сборки для релизов NovelMaestro

Два варианта распространения — **Docker** (сервер) и **портативная сборка
для Windows 10/11** (папка с интерпретатором Python и зависимостями).
Оба варианта используют один и тот же код (`web/` + `core/` + `cli/`),
конфиг — корневой `.env` (см. `templates/.env.example`), данные —
папка проектов (`--projects-dir` / `WEB_PROJECTS_DIR`, по умолчанию
`projects/`).

## Docker

### Быстрый старт

```bash
cp templates/.env.example .env    # конфиг LLM (HOST/API_KEY/MODEL) + WEB_*
docker compose up -d --build
# → http://localhost:8756  (в браузере)
```

Или вручную:

```bash
docker build -t novelmaestro .
docker run -d -p 8756:8756 \
  -v "$PWD/projects:/app/projects" \
  -v "$PWD/web/job_logs:/app/web/job_logs" \
  novelmaestro
```

### Что внутри образа

- `python:3.12-slim` + `requests`, `tqdm`, `pyahocorasick` (опционален;
  без него NER работает через regex-fallback, медленнее);
- весь код приложения в `/app` (исключены `.dockerignore`: `projects/`,
  `tests/`, `logs/`, git и т.п.);
- запуск: `python3 web/main.py --host 0.0.0.0` — тот же CLI, что и локально
  (`--port`, `--auth`, `--token`, `--max-upload-mb`, `--jobs-limit`,
  `--projects-dir` — всё работает);
- HEALTHCHECK: `GET /api/session` раз в 30 с.

### Сеть и порты

- Контейнер слушает `0.0.0.0:8756`; наружу пробрасывается `8756:8756`
  (поменяйте левую часть в `docker-compose.yml`, если порт занят).
- Внутри контейнера никаких других сервисов нет — только web-сервер.
  LLM-запросы идут напрямую из контейнера наружу (нужен интернет/доступ
  до вашего LLM-сервера; если LLM-сервер на хосте — используйте
  `host.docker.internal` или `--network host`).
- `WEB_HOST=0.0.0.0` уже в compose; для доступа с других машин LAN —
  без изменений (0.0.0.0 по умолчанию).

### Данные и конфиг (важно)

| Что | Куда | Назначение |
| --- | --- | --- |
| `./projects` | `/app/projects` | **все проекты** — монтируйте обязательно, иначе потеряются при пересоздании контейнера |
| `./web/job_logs` | `/app/web/job_logs` | история запусков (`jobs.json` + логи), переживает рестарт |
| `./.env` | env-переменные | `env_file` с `required: false` — если `.env` есть, его значения попадают в окружение (приоритет: env > файл); LLM-скрипты в subprocess наследуют окружение |
| токен | `projects/.web_secret` | генерируется при `WEB_AUTH=1`, сохраняется в volume |

Переменные окружения — те же, что локально: `WEB_HOST`, `WEB_PORT`,
`WEB_AUTH`, `WEB_TOKEN`, `WEB_MAX_UPLOAD_MB`, `WEB_JOBS_LIMIT`,
`WEB_PROJECTS_DIR` (не нужен при монтировании в дефолтный путь), а также
`HOST`/`API_KEY`/`MODEL` и `*_MODEL` для LLM.

### Права на файлы и безопасность

- Контейнер работает не-root пользователем **uid 1000**; compose
  дополнительно подставляет `user: "${UID:-1000}:${GID:-1000}"` — на
  Linux файлы, созданные контейнером в `./projects`, принадлежат вашему
  uid и доступны для ручной правки. Если хост-uid ≠ 1000 и переменные
  `UID/GID` не экспортированы — задайте `user: "<ваш-uid>:<ваш-gid>"`
  в compose или `--user $(id -u):$(id -g)` при `docker run`.
- Старые bind-mount-данные, созданные root-контейнером прежних версий:
  `sudo chown -R $(id -u):$(id -g) projects` один раз.
- Без `WEB_AUTH=1` интерфейс открыт для всей сети, куда проброшен порт —
  включайте токен для доступа не из localhost.
- `.env` в репозиторий не коммитится (в `.gitignore`).

### Обновление

```bash
git pull
docker compose up -d --build
```

Данные (volume) не трогаются.

## Портативная сборка Windows 10/11

Сборка делает **`packaging/build_portable_windows.ps1`** (PowerShell,
запускать НА Windows):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_portable_windows.ps1
```

Результат — `dist\novelmaestro-portable\`:

```text
novelmaestro-portable\
├── python\            # интерпретатор Python (embeddable 3.12) + requests/tqdm/pyahocorasick
├── core\  cli\  web\  templates\  run.py  …
├── start.bat          # запуск: web-сервер + браузер
└── README.txt
```

- Установка не нужна: копируете папку на целевую Windows 10/11 и
  запускаете `start.bat`. Браузер открывается автоматически.
- Первый запуск: `run.py` создаст `projects\` (разделы ACTIVE/HOLD/DONE)
  и `.env` из шаблона, если его нет.
- `pyahocorasick` ставится по возможности (wheel-пакет); если нет — сборка
  предупреждает, приложение работает через regex-fallback.
- Обновление версии: перенесите `projects\` и `.env` в новую сборку.

### Что проверить перед публикацией сборки

- `.env` и `projects/` не попадают в zip (исключены в скрипте) — но
  проверьте, что собирали не из папки с реальными данными.
- Антивирус может «не узнать» embeddable-python — это нормально,
  подпишите/заархивируйте и добавьте исключение при необходимости.

## Переход репозитория в публичный доступ

1. **LICENSE** — MIT (файл `LICENSE` в корне репо).
2. **Секреты** — корневой `.env` уже в `.gitignore`; проверьте историю:
   `git log -p --all -S 'API_KEY='` не должен ничего показывать.
   `projects/`, `servers/`, `Images/`, `backup/` тоже игнорируются.
3. **CI/релизы** — автоматика уже в `.github/workflows/`:

   | Workflow | Когда | Результат |
   | --- | --- | --- |
   | `docker.yml` | кнопка «Run workflow» **или** пуш тега `v*` | образ `ghcr.io/<владелец>/<репо>:latest` (+ `:<версия>` по тегу), linux/amd64 + arm64 |
   | `windows.yml` | кнопка «Run workflow» (поля: версия Python, метка) **или** пуш тега `v*` | zip портативной сборки: артефакт Actions + (по тегу) GitHub Release |

   Собирать можно хоть каждый день по кнопке; релизы — вручную через
   тег (`git tag v1.2.3 && git push origin v1.2.3`) или из формы
   workflow_dispatch.
4. **Доки** — README.md и web/README.md уже описывают запуск; в
   публичном описании укажите `templates/.env.example` как образец конфига.

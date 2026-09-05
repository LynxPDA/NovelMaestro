# Сборки для релизов NovelMaestro

Два варианта распространения — **Docker** (сервер) и **портативная сборка
для Windows 10/11** (папка с интерпретатором Python и зависимостями).
Оба варианта используют один и тот же код (`web/` + `core/` + `cli/`),
конфиг — `.env` (см. `templates/.env.example`; в Docker — `projects/.env`
в постоянном томе, WEB_ENV_FILE), данные —
папка проектов (`--projects-dir` / `WEB_PROJECTS_DIR`, по умолчанию
`projects/`).

## Docker

> ⚠ Требуется **Docker Compose v2**: запускайте `docker compose up -d`
> (без тире). Старый `docker-compose` v1 (Python) не поддерживается —
> он не понимает формат `env_file`/`required` и упадёт с ошибкой парсинга.

### Быстрый старт

```bash
docker compose up -d --build
# → http://localhost:8756  (в браузере)
```

Конфиг **наружу не пробрасывается**: в образе лежит копия
`templates/.env.example` → `/app/.env` (значения по умолчанию; корневой
`.env` с секретами в образ НЕ входит — `.dockerignore`). Реальные
Конфиг: environment в compose — приоритетный слой (окружение > файл).
Системный .env в Docker живёт в **постоянном томе**: `/app/projects/.env`
(WEB_ENV_FILE в образе; entrypoint сидирует его из шаблона при первом
старте). Вкладка «Настройки» web-интерфейса правит именно его — правки
переживают обновление образа; `/app/.env` внутри образа — заводские
дефолты-фолбэк. Проектные `pdir/.env` (том projects) перекрывают
системный по ключам. Приоритет: `environment` compose >
`projects/.env` > `/app/.env` > встроенный дефолт.
Правки environment применяются после `docker compose up -d` (пересоздание).

Или вручную (без compose, свои значения — переменными окружения):

```bash
docker build -t novelmaestro .
docker run -d -p 8756:8756 \
  -e HOST=http://192.168.1.8:9989 \
  -e API_KEY=... \
  -e MODEL=... \
  -v "$PWD/projects:/app/projects" \
  -v "$PWD/templates:/app/templates" \
  -v "$PWD/web/job_logs:/app/web/job_logs" \
  novelmaestro
```

### Что внутри образа

- `python:3.12-slim` + `requests`, `tqdm`, `pyahocorasick` (опционален;
  без него NER работает через regex-fallback, медленнее);
- весь код приложения в `/app` (исключены `.dockerignore`: `projects/`,
  `tests/`, `logs/`, git и т.п.);
- запуск: `docker-entrypoint.sh` → `python3 web/main.py --host 0.0.0.0` —
  тот же CLI, что и локально (`--port`, `--auth`, `--token`,
  `--max-upload-mb`, `--jobs-limit`, `--projects-dir` — всё работает);
- entrypoint (от root) чинит владельца bind-mount каталогов (`projects/`,
  `templates/`, `web/job_logs/`) на `app` (uid 1000) — docker создаёт их
  от root, если папок нет на хосте — и переключается на `app` через gosu;
- **шаблоны**: снимок `templates/` образа лежит в `/app/templates-dist`;
  если хостовый `./templates` пуст (первый запуск), entrypoint копирует
  `General/` и `.env.example` в него — «Шаблоны» не будут пустыми;
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
| `./projects` | `/app/projects` | **все проекты и системный конфиг** (`.env` в корне тома) — монтируйте обязательно, иначе потеряются при пересоздании контейнера |
| `./templates` | `/app/templates` | ваши **наборы шаблонов** (вкладка «Шаблоны») — монтируется, иначе изменения потеряются при пересоздании |
| `./web/job_logs` | `/app/web/job_logs` | история запусков (`jobs.json` + логи), переживает рестарт |
| `/app/projects/.env` (в томе) | **системный .env** (WEB_ENV_FILE): правится вкладкой «Настройки», переживает обновление образа; сид из шаблона при первом старте |
| `/app/.env` (в образе) | заводские дефолты-фолбэк | копия `templates/.env.example`; переопределяется `projects/.env` и environment |
| токен | `projects/.web_secret` | генерируется при `WEB_AUTH=1`, сохраняется в volume |

Переменные окружения — те же, что локально: `WEB_HOST`, `WEB_PORT`,
`WEB_AUTH`, `WEB_TOKEN`, `WEB_MAX_UPLOAD_MB`, `WEB_JOBS_LIMIT`,
`WEB_PROJECTS_DIR` (не нужен при монтировании в дефолтный путь), а также
`HOST`/`API_KEY`/`MODEL` и `*_MODEL` для LLM.

**Конфиг в Docker**: `environment` в `docker-compose.yml` — единое место
настройки. В образе `/app/.env` (из `templates/.env.example`) даёт
значения по умолчанию; окружение их переопределяет (приоритет: окружение
> файл). Вкладка «Настройки» web-интерфейса `.env` НЕ правит — внешний
вид (тема/кегль) хранится в localStorage браузера.

### Права на файлы и безопасность

- Контейнер стартует от root, но `docker-entrypoint.sh` сразу чинит
  владельца каталогов данных на **app (uid 1000)** и переключается на
  него (gosu) — сервер работает не-root. На Linux файлы, созданные
  контейнером в `./projects`, принадлежат uid 1000 (типичный
  хост-пользователь) и доступны для ручной правки. Если хост-uid ≠ 1000
  — поменяйте `useradd -u 1000` в Dockerfile.
- Старые bind-mount-данные, созданные root-контейнером прежних версий:
  `sudo chown -R $(id -u):$(id -g) projects templates web/job_logs` один раз.
- Без `WEB_AUTH=1` интерфейс открыт для всей сети, куда проброшен порт —
  включайте токен для доступа не из localhost.
- `.env` в репозиторий не коммитится (в `.gitignore`).

### Обновление

```bash
git pull
docker compose up -d --build
```

- Переживают обновление (живут на хосте, смонтированы): `projects/`
  (все книги), `templates/` (ваши наборы шаблонов), `web/job_logs/`
  (история запусков), `projects/.web_secret` (токен).
- Монтирование `./templates` заменяет встроенную папку. Теперь это не
  страшно: entrypoint копирует шаблоны образа (`General/`, `.env.example`)
  в пустой хостовый каталог при первом запуске. Если хостовый `./templates`
  уже непустой — он используется как есть (ваши наборы приоритетнее).
- Конфиг — `environment` в compose (переопределяет `/app/.env` из образа);
  при пересоздании контейнера применяются текущие значения.
- Образ по умолчанию — готовый из реестра ghcr.io:
  `ghcr.io/lynxpda/novelmaestro` (`:latest` на ветке main, `:<версия>` по
  тегам — публикуется воркфлоу docker.yml автоматически). Обновление
  готового образа: `docker compose pull && docker compose up -d`
  (без `--build`); локальная сборка из исходников —
  `docker compose up -d --build`. Для своего форка поменяйте
  `image:` в compose на `ghcr.io/<владелец>/<репо>:<версия>`.
- После обновления откройте интерфейс и убедитесь, что проекты и
  шаблоны на месте.

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
└── START.txt          # краткая инструкция по запуску и настройке
```

- Установка не нужна: копируете папку на целевую Windows 10/11 и
  запускаете `start.bat`. Браузер открывается автоматически.
- Первый запуск: `run.py` создаст `projects\` (разделы ACTIVE/HOLD/DONE)
  и `.env` из шаблона, если его нет.
- `pyahocorasick` ставится по возможности (wheel-пакет); если нет — сборка
  предупреждает, приложение работает через regex-fallback.
- Обновление версии: перенесите `projects\` и `.env` в новую сборку.

### Сервер в портативной сборке

- По умолчанию сервер слушает **только этот компьютер** (`127.0.0.1:8756`)
  — безопасно, токен не нужен.
- Порт занят (например, уже запущена другая копия) — сервер сам берёт
  следующий свободный (`8757`, `8758`, …) и пишет фактический адрес в
  консоль и в баннер. Браузер открывается на реальном адресе.
- Доступ с других машин сети: `WEB_HOST=0.0.0.0` в `.env` (или
  `python\python.exe run.py --host 0.0.0.0`) — тогда обязателен токен:
  `WEB_AUTH=1` (и `WEB_TOKEN=`, иначе он сгенерируется в
  `projects\.web_secret`). Без токена интерфейс и ключи `.env` видны
  всей сети.
- Полные настройки и решение проблем — DEVELOPERS.md (раздел
  «Настройки web-сервера»).

### Что проверить перед публикацией сборки

- `.env` и `projects/` не попадают в zip (исключены в скрипте) — но
  проверьте, что собирали не из папки с реальными данными.
- Антивирус может «не узнать» embeddable-python — это нормально,
  подпишите/заархивируйте и добавьте исключение при необходимости.

## Публикация релиза (пошагово)

Релиз = тег `v*` на `main`. Пуш тега автоматически запускает оба
воркфлоу (`docker.yml` → образ ghcr, `windows.yml` → zip + GitHub
Release).

```bash
# 1. Проверка перед релизом
python3 -m pytest tests/ -q          # все тесты зелёные
node --check web/static/app.js       # и остальные статики без ошибок

# 2. Закоммитить и запушить всё (незапушенный коммит — не релиз)
git add -A && git commit -m "…" && git push origin

# 3. Тег версии и пуш
git tag v0.2.0
git push origin v0.2.0
# → Actions: Docker image + Portable Windows build (по тегу)

# 4. Дождаться двух зелёных галок (вкладка Actions)
#    windows.yml сам создаст GitHub Release и прикрепит
#    novelmaestro-portable-<версия>.zip (gh release create --generate-notes)

# 5. Проверить:
#    - страница Releases → v0.2.0, ассет zip на месте;
#    - ghcr.io/LynxPDA/novelmaestro:0.2.0 (+ :latest).
```

### Пересобрать релиз с тем же тегом (например, забыли файл)

Тег и релиз уже существуют — `gh release create` не перезапишет.
Удалите и создайте заново:

```bash
git push origin :refs/tags/v0.2.0     # удалить тег на remote
# GitHub → Releases → v0.2.0 → Delete release (кнопка справа)
git tag v0.2.0                        # заново на текущем HEAD
git push origin v0.2.0                # оба воркфлоу пересоберутся
```

### Черновые прогоны без релиза

Actions → нужный воркфлоу → «Run workflow» — собирает без тега и без
релиза (Windows: поля «Версия Python» и «Метка имени zip»). Артефакт
лежит в Actions → run → Artifacts.

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

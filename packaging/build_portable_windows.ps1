# build_portable_windows.ps1 — портативная сборка NovelMaestro для Windows 10/11.
#
# Результат: dist\novelmaestro-portable\ — папка с интерпретатором Python
# (embeddable), зависимостями и приложением. Установка не нужна:
# запуск — start.bat (web-сервер + браузер). Опционально — zip-архив.
#
# Использование (в PowerShell, из папки packaging/):
#   powershell -ExecutionPolicy Bypass -File build_portable_windows.ps1
#
# Параметры:
#   -PythonVersion 3.12.8   версия Python (embeddable-пакет python.org)
#   -OutDir        dist     куда класть результат
#   -Label         дата     суффикс имени zip (по умолчанию ГГГГММДД)
#   -NoZip                  не собирать zip
#
# Требования: интернет (python.org, bootstrap.pypa.io, PyPI), PowerShell 5.1+.
# Сборка делается НА Windows (скрипт PowerShell), на Linux не работает.

param(
    [string]$PythonVersion = "3.12.8",
    [string]$OutDir = "dist",
    [string]$Label = "",
    [switch]$NoZip
)

$ErrorActionPreference = "Stop"

# Харденинг: версия Python — строго X.Y.Z (инпут из GitHub Actions)
if ($PythonVersion -notmatch "^\d+\.\d+\.\d+$") {
    throw "-PythonVersion: ожидается X.Y.Z, получено: $PythonVersion"
}

$root   = Split-Path -Parent $PSScriptRoot          # корень репо
$dist   = Join-Path $root $OutDir
$portable = Join-Path $dist "novelmaestro-portable"
$pyDir  = Join-Path $portable "python"

Write-Host "NovelMaestro: портативная сборка для Windows"
Write-Host "  Python: $PythonVersion (embeddable, amd64)"
Write-Host "  Результат: $portable"
New-Item -ItemType Directory -Force -Path $dist | Out-Null

# ── 1. Python embeddable ────────────────────────────────────────────
$zip = Join-Path $dist "python-$PythonVersion-embed-amd64.zip"
$url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
if (-not (Test-Path $zip)) {
    Write-Host "Скачиваю Python: $url"
    Invoke-WebRequest -Uri $url -OutFile $zip
}
New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
Expand-Archive -Path $zip -DestinationPath $pyDir -Force

# ── 2. Включаем site-packages: python*. _pth, строка "# import site" ──
$pth = Get-ChildItem $pyDir -Filter "python*._pth" | Select-Object -First 1
if ($pth) {
    $content = Get-Content $pth.FullName -Raw
    $content = $content -replace "(?m)^#\s*import site\s*$", "import site"
    Set-Content -Path $pth.FullName -Value $content -Encoding ASCII
    Write-Host "site-packages включён ($($pth.Name))"
} else {
    throw "Не найден python*._pth в $pyDir"
}

# ── 3. pip (get-pip.py) ─────────────────────────────────────────────
$pyExe   = Join-Path $pyDir "python.exe"
$getPip  = Join-Path $dist "get-pip.py"
if (-not (Test-Path $getPip)) {
    Write-Host "Скачиваю get-pip.py"
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip
}
Write-Host "Устанавливаю pip…"
& $pyExe $getPip --no-warn-script-location --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "pip не установился (код $LASTEXITCODE)" }

# ── 4. Зависимости ──────────────────────────────────────────────────
# pyahocorasick — опционально (ускоряет NER). Если колеса нет и нет
# компилятора — сборка упадёт, НО приложение работает: regex-fallback.
Write-Host "Устанавливаю requests, tqdm…"
& $pyExe -m pip install --no-warn-script-location --disable-pip-version-check requests tqdm
if ($LASTEXITCODE -ne 0) { throw "Не удалось установить requests/tqdm" }
Write-Host "Устанавливаю pyahocorasick (опционально)…"
& $pyExe -m pip install --no-warn-script-location --disable-pip-version-check pyahocorasick
if ($LASTEXITCODE -ne 0) {
    Write-Warning "pyahocorasick не установился — будет regex-fallback (медленнее, но работает)"
} else {
    Write-Host "pyahocorasick OK"
}

# ── 5. Копируем репозиторий (без данных/тестов/мусора) ─────────────
Write-Host "Копирую репозиторий…"
robocopy $root $portable /E /XD .git .github projects tests servers Images backup __pycache__ .venv logs job_logs .pytest_cache dist /XF .env *.pyc *.log /NFL /NDL /NJH /NJS /NP
if ($LASTEXITCODE -ge 8) { throw "robocopy: ошибка копирования (код $LASTEXITCODE)" }

# ── 6. start.bat (только ASCII: cmd читает bat в кодовой странице
#         консоли; русский текст — внутри браузера) + README.txt ──
@"
@echo off
cd /d "%~dp0"
echo NovelMaestro: starting web server...
echo Browser will open automatically (http://127.0.0.1:8756).
python\python.exe run.py
if errorlevel 1 (
  echo.
  echo Startup failed - see messages above.
  pause
)
"@ | Set-Content -Path (Join-Path $portable "start.bat") -Encoding ASCII
@"
NovelMaestro — портативная сборка (Windows 10/11)

Запуск: двойной клик по start.bat
  - поднимется web-сервер и откроется браузер (http://127.0.0.1:8756)
  - папка projects\ создастся рядом при первом запуске (разделы ACTIVE/HOLD/DONE)

Конфиг: файл .env рядом с run.py (см. templates\.env.example) —
  LLM-сервер (HOST/API_KEY/MODEL) и WEB_* настройки.
  Если .env нет — он создастся из шаблона при первом запуске.

Обновление версии: перенесите папку projects\ в новую сборку и запустите.
"@ | Set-Content -Path (Join-Path $portable "README.txt") -Encoding UTF8

# ── 7. (опционально) zip ────────────────────────────────────────────
if (-not $NoZip) {
    if (-not $Label) { $Label = Get-Date -Format "yyyyMMdd" }
    $zipOut = Join-Path $dist "novelmaestro-portable-$Label.zip"
    Write-Host "Собираю архив: $zipOut"
    Compress-Archive -Path $portable -DestinationPath $zipOut -Force
}

Write-Host ""
Write-Host "ГОТОВО: $portable"
if (-not $NoZip) { Write-Host "Архив: $zipOut" }
Write-Host "Запуск на Windows: $portable\start.bat"

# robocopy (шаг 5) — последняя внешняя команда; при успешном копировании
# она возвращает код 1, а обёртка powershell в GitHub Actions выходит
# с последним $LASTEXITCODE → успешная сборка «падает» с кодом 1.
# Сбрасываем явно (в интерактиве безвредно).
$global:LASTEXITCODE = 0

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты M6: web-оркестратор конвейера (web/pipeline.py).

- build_pipeline (спека 3) в stages.py;
- pipeline.py e2e: фейковый translate_book.py, фейковые главы, события
  @@CHAPTER@@ в stdout, fail-fast (код 0 + непустой выход + grep);
- JobManager reader парсит события в job.events (без сети, tmp_path).
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from web.jobs import CHAPTER_PREFIX, JobManager
from web.stages import (STAGE_SPECS, build_command, script_path,
                        spec_for)

REPO = Path(__file__).resolve().parent.parent
PIPELINE = REPO / "web" / "pipeline.py"

# ── фейковый translate_book.py: пишет out-файл, код 0 ────────────────
FAKE_TRANSLATE = (
    "import json, sys\n"
    "# argv: in --mode X --out OUT ...\n"
    "args = sys.argv[1:]\n"
    "out = args[args.index('--out') + 1]\n"
    "mode = args[args.index('--mode') + 1]\n"
    "with open(out, 'w', encoding='utf-8') as f:\n"
    "    f.write(f'ok-{mode}\\n')\n"
    "if mode == 'translate':\n"
    "    from pathlib import Path\n"
    "    Path(out).with_name('translated_trace.json').write_text(\n"
    "        '{\"pairs\":[]}', encoding='utf-8')\n"
    "print('done', flush=True)\n"
    "sys.exit(0)\n"
)
FAKE_TRANSLATE_PREVIEW = (
    "import json, sys\n"
    "args = sys.argv[1:]\n"
    "assert '--preview-request' in args\n"
    "pv = args[args.index('--preview-request') + 1]\n"
    "with open(pv, 'w', encoding='utf-8') as f:\n"
    "    json.dump({'stage': 'pipeline', 'label': 't', 'model': 'm',\n"
    "               'messages': [{'role': 'user', 'content': 'x'}],\n"
    "               'chars': {'system': 0, 'user': 1, 'total': 1},\n"
    "               'meta': {}}, f)\n"
    "print('done', flush=True)\n"
    "sys.exit(0)\n"
)
FAKE_TRANSLATE_FAIL = (
    "import sys\n"
    "print('ERROR: boom', flush=True)\n"
    "sys.exit(2)\n"
)


def _make_project(tmp_path, chapters=(1, 2, 3), bad_chapter=None):
    """Фейковый проект: chapters/00001_1/… + fake translate_book.py."""
    proj = tmp_path / "project"
    ch_dir = proj / "chapters"
    ch_dir.mkdir(parents=True)
    fake = tmp_path / "translate_book.py"
    fake.write_text(FAKE_TRANSLATE, encoding="utf-8")
    for n in chapters:
        d = ch_dir / f"{n:05d}_1"
        d.mkdir()
        if n == bad_chapter:
            (d / "chapter.txt").write_text("bad\n", encoding="utf-8")
        else:
            (d / "chapter.txt").write_text("text\n", encoding="utf-8")
    return proj, fake


_PIPELINE_ARGS = ["--host", "http://127.0.0.1:9989", "--model", "m"]


# ════════════════════════════════════════════════════════════════════
# Спека 3


def test_pipeline_stage_in_specs():
    spec = spec_for("pipeline")
    assert spec is not None
    assert spec["script"] == "web/pipeline.py"
    names = [f["name"] for f in spec["fields"]]
    assert "action" in names and "start" in names and "end" in names
    assert "jobs" in names and "host" in names and "api_key" in names
    assert "profile" not in names  # профили убраны
    # единый общий промпт-файл; режим промптов и отдельные файлы
    # на стадию убраны
    assert "prompt_file" in names
    assert "prompt_mode" not in names
    assert "translate_prompt" not in names
    assert "redact_prompt" not in names
    assert "polish_prompt" not in names


def test_pipeline_script_path():
    p = script_path("pipeline", REPO)
    assert p is not None and p.is_file()
    assert p.name == "pipeline.py"


def test_pipeline_action_options():
    """Тип работы: 8 вариантов с подписями исходников/циклов;
    дефолт — полный цикл (8)."""
    from web.stages import _LLM_FIELDS
    spec = spec_for("pipeline")
    assert spec is not None
    action = next(f for f in spec["fields"] if f["name"] == "action")
    assert action["type"] == "select"
    assert action["options"] == ["1", "2", "3", "4", "5", "6", "7", "8"]
    assert action["default"] == "8"
    labels = action["labels"]
    assert labels["1"] == "Перевод"
    assert labels["2"] == "Редактура (исходник - Перевод)"
    assert labels["3"] == "Полировка (исходник - Редактура)"
    assert labels["4"] == "Полировка (исходник - Перевод)"
    assert labels["5"] == "Сокращенный цикл: Перевод -> Редактура"
    assert labels["6"] == "Сокращенный цикл: Перевод -> Полировка"
    assert labels["7"] == "Сокращенный цикл: Редактура -> Полировка"
    assert labels["8"] == "Полный цикл: Перевод -> Редактура -> Полировка"
    # единая модель конвейера: PIPELINE_MODEL → MODEL
    model = next(f for f in spec["fields"] if f["name"] == "model")
    assert model in _LLM_FIELDS
    from web.stages import env_keys_for
    assert env_keys_for("pipeline", "model") == ["PIPELINE_MODEL", "MODEL"]
    assert env_keys_for("pipeline", "host") == ["PIPELINE_HOST", "HOST"]
    assert env_keys_for("pipeline", "api_key") == \
        ["PIPELINE_API_KEY", "API_KEY"]


def test_build_pipeline_argv():
    form = {"action": "translate_check", "start": "1", "end": "5", "jobs": "4",
            "timeout": "300", "max_retries": "7", "host": "http://127.0.0.1:9989",
            "model": "m", "api_key": "k"}
    ctx: dict = {}
    argv = build_command("pipeline", form, ctx)
    assert argv[0] == "web/pipeline.py"
    assert "--action" in argv and "4" in argv
    assert "--start" in argv and "--end" in argv
    assert "--jobs" in argv and "4" in argv
    assert "--host" in argv and "--model" in argv
    # повторы из экспертной формы — отдельным флагом в argv
    assert "--max_retries" in argv
    assert argv[argv.index("--max_retries") + 1] == "7"
    # P1 (AUDIT #2): ключ не в argv, а в ctx для env JobManager
    assert "--api_key" not in argv
    assert ctx.get("_llm_api_key") == "k"


def test_build_pipeline_prompt_argv():
    """единый общий промпт-файл → один --prompt_file; без файла —
    без флагов промптов (встроенные/авто в pipeline.py)."""
    from web.stages import build_command
    ctx: dict = {}
    base = {"action": "4", "host": "http://h", "model": "m",
            "api_key": "k"}
    # задан общий файл → --prompt_file
    form = dict(base, prompt_file="prompts/pipeline_prompt.txt")
    argv = build_command("pipeline", form, ctx)
    assert "--prompt_file" in argv
    assert "prompts/pipeline_prompt.txt" in argv
    # без файла — флагов промптов нет вообще
    argv2 = build_command("pipeline", dict(base), ctx)
    assert "--prompt_file" not in argv2
    assert "--translate_prompt" not in argv2
    assert "--redact_prompt" not in argv2
    assert "--polish_prompt" not in argv2


def test_resolve_prompt_paths(tmp_path, monkeypatch):
    """auto — кандидат с тегами из prompts/ > пустые пути (встроенные);
    отдельные файлы на стадию убраны."""
    from web.pipeline import resolve_prompt_paths
    monkeypatch.chdir(tmp_path)
    # пусто и нет кандидатов — стадии на встроенных промптах
    out = resolve_prompt_paths()
    assert out[1] == out[2] == out[3] == ""
    # кандидат с тегами → один файл на все стадии
    pr = tmp_path / "prompts"
    pr.mkdir()
    (pr / "pipeline_prompt.txt").write_text(
        "<translate>\nПЕРЕВОД\n</translate>\n"
        "<polish>\nПОЛИРОВКА\n</polish>", encoding="utf-8")
    out = resolve_prompt_paths()
    assert out[1] == out[2] == out[3] == "prompts/pipeline_prompt.txt"
    # файл БЕЗ тегов кандидатом не считается → встроенные
    (pr / "pipeline_prompt.txt").write_text(
        "просто текст без тегов", encoding="utf-8")
    out = resolve_prompt_paths()
    assert out[1] == out[2] == out[3] == ""
    # явный общий файл — все стадии им
    out = resolve_prompt_paths("prompts/pipeline_prompt.txt")
    assert out[1] == out[2] == out[3] == "prompts/pipeline_prompt.txt"


def test_warn_missing_prompt_tag(tmp_path, monkeypatch):
    """предупреждение, если в общем промпт-файле нет тега стадии;
    файл без тегов и пустой путь — без предупреждения."""
    from web.pipeline import warn_missing_prompt_tag
    import logging
    monkeypatch.chdir(tmp_path)
    pr = tmp_path / "prompts"
    pr.mkdir()
    pf = "prompts/p.txt"
    (pr / "p.txt").write_text(
        "<translate>\nПЕРЕВОД\n</translate>\n"
        "<polish>\nПОЛИРОВКА\n</polish>", encoding="utf-8")
    msgs = []
    class L:
        def warning(self, *a): msgs.append(a)
    # есть тег translate, нет redact
    warn_missing_prompt_tag(pf, 1, L())
    assert not msgs
    warn_missing_prompt_tag(pf, 2, L())
    assert msgs and msgs[0][2] == "redact"
    # файл без тегов — легальный режим, без предупреждения
    (pr / "p.txt").write_text("просто текст", encoding="utf-8")
    msgs2 = []
    warn_missing_prompt_tag(pf, 2, L())
    assert not msgs2
    # пустой путь — пропуск
    warn_missing_prompt_tag("", 2, L())
    assert not msgs2


def test_build_stage_cmd_prompt_override(tmp_path):
    """переданный prompt_file уходит в команду стадии; пусто =
    без --prompt_file (встроенные промпты)."""
    from web.pipeline import build_stage_cmd
    script = tmp_path / "translate_book.py"
    cmd = build_stage_cmd(1, script, tmp_path / "in", tmp_path / "out",
                          "http://h", "k", "м", 300,
                          prompt_file="prompts/pipeline_prompt.txt")
    assert "--prompt_file" in cmd
    assert cmd[cmd.index("--prompt_file") + 1] == \
        "prompts/pipeline_prompt.txt"
    # без переданного — флага нет вообще (встроенный промпт)
    cmd2 = build_stage_cmd(2, script, tmp_path / "in", tmp_path / "out",
                           "http://h", "k", "м", 300)
    assert "--prompt_file" not in cmd2


def test_build_stage_cmd_ner_fields(tmp_path):
    """поля {ner_block} из формы — во ВСЕ 3 стадии; aliases снят —
    --no-aliases добавляется (авто-алиасы выключены). Имя флага —
    как в translate_book.py: через дефис, иначе argparse стадии 1
    ронял конвейер «unrecognized arguments: --no_aliases»."""
    from web.pipeline import build_stage_cmd
    script = tmp_path / "translate_book.py"
    for stage in (1, 2, 3):
        cmd = build_stage_cmd(stage, script, tmp_path / "in",
                              tmp_path / "out", "http://h", "k", "м", 300,
                              ner_fields="term,type,translation,aliases")
        assert "--ner_fields" in cmd
        assert cmd[cmd.index("--ner_fields") + 1] == \
            "term,type,translation,aliases"
        assert "--no-aliases" not in cmd
    for stage in (1, 2, 3):
        cmd = build_stage_cmd(stage, script, tmp_path / "in",
                              tmp_path / "out", "http://h", "k", "м", 300,
                              ner_fields="term,type", no_aliases=True)
        assert "--no-aliases" in cmd


def test_build_pipeline_ner_fields_argv():
    """hidden-поле ner_fields из формы — в argv оркестратора; пусто —
    без флага (CLI-дефолт)."""
    ctx: dict = {}
    base = {"action": "8", "host": "http://h", "model": "m",
            "api_key": "k"}
    argv = build_command("pipeline",
                         dict(base, ner_fields="term,type,translation"),
                         ctx)
    assert argv[argv.index("--ner_fields") + 1] == "term,type,translation"
    argv2 = build_command("pipeline", dict(base), ctx)
    assert "--ner_fields" not in argv2


def test_build_stage_cmd_single_model(tmp_path):
    """единая модель конвейера — без stage_models: переданная модель
    уходит во все стадии как есть."""
    from web.pipeline import build_stage_cmd
    script = tmp_path / "translate_book.py"
    for stage in (1, 2, 3):
        cmd = build_stage_cmd(stage, script, tmp_path / "in",
                              tmp_path / "out",
                              "http://h", "k", "общая", 300)
        assert "--model общая" in " ".join(cmd)
    # потоки на главу — по умолчанию 1, пробрасываются в argv
    cmd = build_stage_cmd(1, script, tmp_path / "in", tmp_path / "out",
                          "http://h", "k", "м", 300)
    assert "--threads" in cmd and "1" in cmd
    cmd4 = build_stage_cmd(1, script, tmp_path / "in", tmp_path / "out",
                           "http://h", "k", "м", 300, threads=4)
    assert "--threads 4" in " ".join(cmd4)


def test_build_stage_cmd_max_retries(tmp_path):
    """повторы из формы — в команду стадии; пусто — дефолт _DEFAULTS."""
    from web.pipeline import build_stage_cmd, _DEFAULTS
    script = tmp_path / "translate_book.py"
    cmd = build_stage_cmd(1, script, tmp_path / "in", tmp_path / "out",
                          "http://h", "k", "м", 300, max_retries=7)
    i = cmd.index("--max_retries")
    assert cmd[i + 1] == "7"
    cmd2 = build_stage_cmd(1, script, tmp_path / "in", tmp_path / "out",
                           "http://h", "k", "м", 300)
    i2 = cmd2.index("--max_retries")
    assert cmd2[i2 + 1] == str(_DEFAULTS["max_retries"])


def test_grep_errors_ignores_logger_level_names():
    """P0 (AUDIT #1): имя уровня логгера (- WARNING -) не валит главу."""
    from web import pipeline as PL
    ok_text = (
        "2026-01-01 12:00:00 - WARNING - Глава переведена\n"
        "2026-01-01 12:00:00 - INFO - всё хорошо\n"
        "обычный вывод без ошибок\n"
    )
    assert PL.grep_errors(ok_text) == []
    bad_text = "2026-01-01 12:00:00 - ERROR - Translation error, retry\n"
    hits = PL.grep_errors(bad_text)
    assert len(hits) == 1 and "Translation error" in hits[0]
    # голый текст без префикса логгера тоже ловится
    assert PL.grep_errors("Traceback (most recent call last):") != []



def test_pipeline_help_smoke():
    r = subprocess.run([sys.executable, str(PIPELINE), "--help"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    assert "--action" in r.stdout
    # флаги промпт-файлов
    assert "--prompt_file" in r.stdout
    # отдельные файлы на стадию убраны
    assert "--translate_prompt" not in r.stdout


# ════════════════════════════════════════════════════════════════════
# e2e: pipeline.py по фейковым главам


def test_pipeline_full_cycle_events(tmp_path):
    """Полный цикл (действие 8): 3 главы × 3 стадии → 9 OK-событий."""
    proj, fake = _make_project(tmp_path)
    cmd = [sys.executable, str(PIPELINE), "--action", "8",
           "--start", "1", "--end", "3", "--jobs", "2",
           "--script", str(fake), *_PIPELINE_ARGS]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=120, cwd=str(proj))
    assert r.returncode == 0, r.stderr[-500:]
    events = [json.loads(l[len(CHAPTER_PREFIX):])
              for l in r.stdout.splitlines()
              if l.startswith(CHAPTER_PREFIX)]
    ok = [e for e in events if e["status"] == "OK"]
    assert len(ok) == 9, [e for e in events if e["status"] != "OK"]
    # артефакты созданы
    for n in (1, 2, 3):
        d = proj / "chapters" / f"{n:05d}_1"
        assert (d / "translated.txt").is_file()
        assert (d / "redacted.txt").is_file()
        assert (d / "polished.txt").is_file()


def test_pipeline_polish_from_translated(tmp_path):
    """Действие 4 — полировка (исходник - Перевод): вход translated.txt,
    а не redacted.txt (redacted.txt не создаём — иначе был бы SKIP)."""
    proj, fake = _make_project(tmp_path)
    for n in (1, 2, 3):
        d = proj / "chapters" / f"{n:05d}_1"
        (d / "translated.txt").write_text("перевод\n", encoding="utf-8")
    cmd = [sys.executable, str(PIPELINE), "--action", "4",
           "--start", "1", "--end", "3", "--jobs", "3",
           "--script", str(fake), *_PIPELINE_ARGS]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=120, cwd=str(proj))
    assert r.returncode == 0, r.stderr[-500:]
    events = [json.loads(l[len(CHAPTER_PREFIX):])
              for l in r.stdout.splitlines()
              if l.startswith(CHAPTER_PREFIX)]
    ok = [e for e in events if e["status"] == "OK"]
    assert len(ok) == 3, [e for e in events if e["status"] != "OK"]
    for n in (1, 2, 3):
        assert (proj / "chapters" / f"{n:05d}_1" / "polished.txt").is_file()


def test_pipeline_translate_then_polish(tmp_path):
    """Действие 6 — сокращённый цикл перевод→полировка: 2 стадии на главу,
    полировка читает translated.txt (redacted.txt не создаётся)."""
    proj, fake = _make_project(tmp_path)
    cmd = [sys.executable, str(PIPELINE), "--action", "6",
           "--start", "1", "--end", "3", "--jobs", "3",
           "--script", str(fake), *_PIPELINE_ARGS]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=120, cwd=str(proj))
    assert r.returncode == 0, r.stderr[-500:]
    events = [json.loads(l[len(CHAPTER_PREFIX):])
              for l in r.stdout.splitlines()
              if l.startswith(CHAPTER_PREFIX)]
    ok = [e for e in events if e["status"] == "OK"]
    assert len(ok) == 6, [e for e in events if e["status"] != "OK"]
    for n in (1, 2, 3):
        d = proj / "chapters" / f"{n:05d}_1"
        assert (d / "translated.txt").is_file()
        assert (d / "polished.txt").is_file()
        assert not (d / "redacted.txt").exists()


def test_pipeline_single_stage(tmp_path):
    """Одна стадия (редактура): 3 OK + артефакт redacted.txt."""
    proj, fake = _make_project(tmp_path)
    # для редактуры нужен trace от перевода
    for n in (1, 2, 3):
        d = proj / "chapters" / f"{n:05d}_1"
        (d / "translated_trace.json").write_text(
            '{"pairs": []}', encoding="utf-8")
    cmd = [sys.executable, str(PIPELINE), "--action", "2",
           "--start", "1", "--end", "3", "--jobs", "3",
           "--script", str(fake), *_PIPELINE_ARGS]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=120, cwd=str(proj))
    assert r.returncode == 0, r.stderr[-500:]
    events = [json.loads(l[len(CHAPTER_PREFIX):])
              for l in r.stdout.splitlines()
              if l.startswith(CHAPTER_PREFIX)]
    assert sum(1 for e in events if e["status"] == "OK") == 3
    for n in (1, 2, 3):
        assert (proj / "chapters" / f"{n:05d}_1" / "redacted.txt").is_file()


def test_pipeline_common_log_file(tmp_path):
    """Общий лог запуска пишется для ЛЮБОГО типа работы (не только
    полный цикл): logs/{Метка}_{start}-{end}_j{jobs}_{время}.log
    (канон run_pipeline.sh) + в файле есть заголовок ТИП РАБОТЫ."""
    proj, fake = _make_project(tmp_path)
    cmd = [sys.executable, str(PIPELINE), "--action", "1",
           "--start", "1", "--end", "3", "--jobs", "2",
           "--script", str(fake), *_PIPELINE_ARGS]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=120, cwd=str(proj))
    assert r.returncode == 0, r.stderr[-500:]
    logs = list((proj / "logs").glob("Translate_1-3_j2_*.log"))
    assert len(logs) == 1, [p.name for p in (proj / "logs").iterdir()]
    body = logs[0].read_text(encoding="utf-8")
    assert "ТИП РАБОТЫ : Перевод" in body
    # полный цикл — своя метка FullCycle
    cmd = [sys.executable, str(PIPELINE), "--action", "8",
           "--start", "1", "--end", "3", "--jobs", "2",
           "--script", str(fake), *_PIPELINE_ARGS]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=120, cwd=str(proj))
    assert r.returncode == 0, r.stderr[-500:]
    logs = list((proj / "logs").glob("FullCycle_1-3_j2_*.log"))
    assert len(logs) == 1, [p.name for p in (proj / "logs").iterdir()]


def test_pipeline_fail_fast(tmp_path):
    """Ошибка стадии: returncode != 0 → ERROR-событие, exit != 0.
    C2 (AUDIT): провал всех глав — exit 1 (раньше тавтология давала 0)."""
    proj, fake = _make_project(tmp_path)
    fake.write_text(FAKE_TRANSLATE_FAIL, encoding="utf-8")
    cmd = [sys.executable, str(PIPELINE), "--action", "1",
           "--start", "1", "--end", "2", "--jobs", "1",
           "--script", str(fake), *_PIPELINE_ARGS]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=120, cwd=str(proj))
    assert r.returncode == 1  # C2: все главы упали → конвейер = ошибка
    events = [json.loads(l[len(CHAPTER_PREFIX):])
              for l in r.stdout.splitlines()
              if l.startswith(CHAPTER_PREFIX)]
    errs = [e for e in events if e["status"] == "ERROR"]
    assert len(errs) >= 1
    assert "ОШИБКА" in r.stdout or "ERROR" in r.stdout


def test_pipeline_no_chapters(tmp_path):
    """Нет папок глав → exit 1 с понятным сообщением."""
    proj = tmp_path / "empty"
    proj.mkdir()
    (proj / "chapters").mkdir()
    cmd = [sys.executable, str(PIPELINE), "--action", "1",
           "--host", "http://127.0.0.1:9989"]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=60, cwd=str(proj))
    assert r.returncode == 1
    assert "не найдены" in r.stderr or "не найдены" in r.stdout


# ════════════════════════════════════════════════════════════════════
# JobManager: события @@CHAPTER@@ → job.events


def test_reader_parses_chapter_events(tmp_path):
    """Фейковый скрипт печатает @@CHAPTER@@ → events в payload."""
    d = tmp_path / "bin"
    d.mkdir()
    script = (
        "import json, sys\n"
        "print('@@CHAPTER@@' + json.dumps({'type':'chapter','id':3,"
        "'stage':1,'status':'OK'}), flush=True)\n"
        "print('normal line', flush=True)\n"
        "sys.exit(0)\n"
    )
    (d / "ev.py").write_text(script, encoding="utf-8")
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("3", "Конвейер", "ACTIVE/x",
                   [str(d / "ev.py")], tmp_path)
    end = time.time() + 15
    while time.time() < end and job.status == "running":
        time.sleep(0.05)
    assert job.status == "done"
    assert len(job.events) == 1
    assert job.events[0]["id"] == 3 and job.events[0]["status"] == "OK"
    # событие не в буфере строк
    assert all(not l.startswith("@@CHAPTER@@") for l in job.lines)
    assert "normal line" in job.lines
    payload = job.payload()
    assert payload["events"] == job.events


def test_reader_bad_event_line(tmp_path):
    """Кривая строка @@CHAPTER@@ — в буфер как обычная строка."""
    d = tmp_path / "bin"
    d.mkdir()
    script = ("import sys\nprint('@@CHAPTER@@not-json', flush=True)\n"
              "sys.exit(0)\n")
    (d / "bad.py").write_text(script, encoding="utf-8")
    jm = JobManager(tmp_path, python="python3")
    job = jm.start("3", "Конвейер", "ACTIVE/x", [str(d / "bad.py")], tmp_path)
    end = time.time() + 15
    while time.time() < end and job.status == "running":
        time.sleep(0.05)
    assert job.status == "done"
    assert job.events == []
    assert any("@@CHAPTER@@" in l for l in job.lines)


# ══════════════════════════════════════════════════════════════════════
# --preview-request: предпросмотр первого запроса
# ══════════════════════════════════════════════════════════════════════

def test_pipeline_preview_request(tmp_path):
    """--preview-request: rc=0, JSON предпросмотра записан; артефакты
    запуска (translated.txt/redacted.txt/polished.txt, общий лог) не
    создаются; главы не обрабатываются."""
    proj, fake = _make_project(tmp_path, chapters=(1,))
    fake.write_text(FAKE_TRANSLATE_PREVIEW, encoding="utf-8")
    pv = tmp_path / "preview.json"
    cmd = [sys.executable, str(PIPELINE), "--action", "8",
           "--script", str(fake), "--preview-request", str(pv),
           *_PIPELINE_ARGS]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=120, cwd=str(proj))
    assert r.returncode == 0, r.stderr[-500:]
    data = json.loads(pv.read_text(encoding="utf-8"))
    assert data["stage"] == "pipeline"
    assert data["messages"] and data["messages"][0]["role"] == "user"
    # артефакты стадий и общий лог не созданы
    ch = proj / "chapters" / "00001_1"
    assert not (ch / "translated.txt").exists()
    assert not (ch / "redacted.txt").exists()
    assert not (ch / "polished.txt").exists()
    assert not (proj / "logs").exists()
    # stdout сообщает о предпросмотре
    assert "ПРЕДПРОСМОТР" in r.stdout


def test_llm_stages_preview_flag():
    """Спека: флаг preview — ровно у шести LLM-стадий (кнопка
    «Предпросмотр запроса» в SPA, экспертный режим)."""
    expect = {"pipeline", "ner", "ner_check", "translate_check_llm",
              "translate_quality", "wiki"}
    got = {k for k, v in STAGE_SPECS.items() if v.get("preview")}
    assert got == expect

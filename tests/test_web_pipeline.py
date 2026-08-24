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
from web.stages import build_command, script_path, spec_for

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
    assert "profile" not in names  # раунд 12: профили убраны
    # раунд 20: выбор промпт-файлов (auto/separate/combined)
    assert "prompt_mode" in names and "prompt_file" in names
    assert "translate_prompt" in names and "redact_prompt" in names
    assert "polish_prompt" in names


def test_pipeline_script_path():
    p = script_path("pipeline", REPO)
    assert p is not None and p.is_file()
    assert p.name == "pipeline.py"


def test_build_pipeline_argv():
    form = {"action": "translate_check", "start": "1", "end": "5", "jobs": "4",
            "timeout": "300", "host": "http://127.0.0.1:9989",
            "model": "m", "api_key": "k"}
    ctx: dict = {}
    argv = build_command("pipeline", form, ctx)
    assert argv[0] == "web/pipeline.py"
    assert "--action" in argv and "4" in argv
    assert "--start" in argv and "--end" in argv
    assert "--jobs" in argv and "4" in argv
    assert "--host" in argv and "--model" in argv
    # P1 (AUDIT #2): ключ не в argv, а в ctx для env JobManager
    assert "--api_key" not in argv
    assert ctx.get("_llm_api_key") == "k"


def test_build_pipeline_prompt_argv():
    """Раунд 20: режимы промптов pipeline → нужные флаги argv."""
    from web.stages import build_command
    ctx: dict = {}
    base = {"action": "4", "host": "http://h", "model": "m",
            "api_key": "k"}
    # combined → один --prompt_file, без per-stage флагов
    form = dict(base, prompt_mode="combined",
                prompt_file="prompts/pipeline_prompt.txt")
    argv = build_command("pipeline", form, ctx)
    assert "--prompt_file" in argv
    assert "prompts/pipeline_prompt.txt" in argv
    assert "--translate_prompt" not in argv
    # separate → по одному флагу на стадию, без --prompt_file
    form2 = dict(base, prompt_mode="separate",
                 translate_prompt="prompts/t.txt",
                 redact_prompt="prompts/r.txt")
    argv2 = build_command("pipeline", form2, ctx)
    assert "--translate_prompt" in argv2 and "prompts/t.txt" in argv2
    assert "--redact_prompt" in argv2 and "prompts/r.txt" in argv2
    assert "--prompt_file" not in argv2
    # auto: явно заданные пробрасываются, пустые — не мешают
    form3 = dict(base, prompt_mode="auto", polish_prompt="prompts/p.txt")
    argv3 = build_command("pipeline", form3, ctx)
    assert "--polish_prompt" in argv3 and "prompts/p.txt" in argv3
    assert "--translate_prompt" not in argv3


def test_resolve_prompt_paths(tmp_path, monkeypatch):
    """Раунд 20: auto — кандидат с тегами > дефолтные имена по стадиям;
    явные флаги приоритетнее auto."""
    from web.pipeline import resolve_prompt_paths
    monkeypatch.chdir(tmp_path)
    # пусто — дефолтные имена по стадиям (как раунд 19)
    out = resolve_prompt_paths()
    assert out[1] == "prompts/translate_prompt.txt"
    assert out[2] == "prompts/redact_prompt.txt"
    assert out[3] == "prompts/polish_prompt.txt"
    # кандидат с тегами → один файл на все стадии
    pr = tmp_path / "prompts"
    pr.mkdir()
    (pr / "pipeline_prompt.txt").write_text(
        "<translate>\nПЕРЕВОД\n</translate>\n"
        "<polish>\nПОЛИРОВКА\n</polish>", encoding="utf-8")
    out = resolve_prompt_paths()
    assert out[1] == out[2] == out[3] == "prompts/pipeline_prompt.txt"
    # файл БЕЗ тегов кандидатом не считается → дефолты
    (pr / "pipeline_prompt.txt").write_text(
        "просто текст без тегов", encoding="utf-8")
    out = resolve_prompt_paths()
    assert out[1] == "prompts/translate_prompt.txt"
    # явный combined
    out = resolve_prompt_paths("prompts/pipeline_prompt.txt")
    assert out[1] == out[2] == out[3] == "prompts/pipeline_prompt.txt"
    # явный separate: пустые стадии — дефолтные имена
    out = resolve_prompt_paths("", {1: "prompts/t.txt"})
    assert out[1] == "prompts/t.txt"
    assert out[2] == "prompts/redact_prompt.txt"


def test_build_stage_cmd_prompt_override(tmp_path):
    """Раунд 20: переданный prompt_file подменяет дефолт по стадии."""
    from web.pipeline import build_stage_cmd
    script = tmp_path / "translate_book.py"
    cmd = build_stage_cmd(1, script, tmp_path / "in", tmp_path / "out",
                          "http://h", "k", "м", 300,
                          prompt_file="prompts/pipeline_prompt.txt")
    assert "--prompt_file" in cmd
    assert cmd[cmd.index("--prompt_file") + 1] == \
        "prompts/pipeline_prompt.txt"
    # без переданного — дефолт стадии (раунд 19: как было)
    cmd2 = build_stage_cmd(2, script, tmp_path / "in", tmp_path / "out",
                           "http://h", "k", "м", 300)
    assert cmd2[cmd2.index("--prompt_file") + 1] == \
        "prompts/redact_prompt.txt"


def test_build_stage_cmd_stage_models(tmp_path):
    """Раунд 12: модель по стадии (stage_models) приоритетнее общей."""
    from web.pipeline import build_stage_cmd
    script = tmp_path / "translate_book.py"
    cmd = build_stage_cmd(1, script, tmp_path / "in", tmp_path / "out",
                          "http://h", "k", "общая", 300,
                          stage_models={1: "translate-model",
                                        2: "redact-model",
                                        3: "polish-model"})
    joined = " ".join(cmd)
    assert "--model translate-model" in joined
    cmd3 = build_stage_cmd(3, script, tmp_path / "in", tmp_path / "out",
                           "http://h", "k", "общая", 300,
                           stage_models={1: "translate-model",
                                         2: "redact-model",
                                         3: "polish-model"})
    assert "--model polish-model" in " ".join(cmd3)
    # стадия без своей модели → общая
    cmd2 = build_stage_cmd(2, script, tmp_path / "in", tmp_path / "out",
                           "http://h", "k", "общая", 300)
    assert "--model общая" in " ".join(cmd2)
    # раунд 14: потоки на главу — по умолчанию 1, пробрасываются в argv
    assert "--threads" in cmd and "1" in cmd
    cmd4 = build_stage_cmd(1, script, tmp_path / "in", tmp_path / "out",
                           "http://h", "k", "м", 300, threads=4)
    assert "--threads 4" in " ".join(cmd4)


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
    # раунд 20: флаги промпт-файлов
    assert "--prompt_file" in r.stdout
    assert "--translate_prompt" in r.stdout


# ════════════════════════════════════════════════════════════════════
# e2e: pipeline.py по фейковым главам


def test_pipeline_full_cycle_events(tmp_path):
    """Полный цикл: 3 главы × 3 стадии → 9 OK-событий @@CHAPTER@@."""
    proj, fake = _make_project(tmp_path)
    cmd = [sys.executable, str(PIPELINE), "--action", "4",
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

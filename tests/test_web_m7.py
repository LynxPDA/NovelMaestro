#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты M7: NER-вьювер, review-флоу, env-редактор, metadata, промпты.

Все хендлеры — через реальный HTTP-сервер (без сети, tmp_path).
Секреты: env-тесты пишут фейковый .env во временную папку и проверяют,
что значения НЕ возвращаются (только ключи и маска ••••).
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from core import common as core_common
from web import api as web_api
from web.auth import Auth
from web.server import make_server

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def srv(tmp_path):
    """Сервер с projects_root=tmp_path/projects, repo_root=настоящий."""
    servers = []

    def _make(projects_root=None, repo_root=REPO):
        projects_root = projects_root or (tmp_path / "projects")
        auth_obj = Auth("tok", no_auth=True)
        srv = make_server("127.0.0.1", 0, auth_obj,
                          repo_root=repo_root, projects_root=projects_root)
        web_api.register(srv.router, "127.0.0.1")
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        servers.append(srv)
        return srv, srv.server_address[1], projects_root

    yield _make
    for srv in servers:
        # server_close() гасит только слушающий сокет: accept-цикл
        # остаётся крутить select без единого fd — пустой цикл на ядро,
        # и на наборе таких собирались сотни. Флаг остановки ставим
        # фоново: sync shutdown() ждёт выхода цикла до poll_interval
        # (0.5 с) на каждый сервер, а тесту ждать нечего
        threading.Thread(target=srv.shutdown, daemon=True).start()
        srv.server_close()


def _request(port, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    req.add_header("X-Requested-With", "fetch")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"__error__": e.code, "__body__": e.read().decode()}


def _mk_project(root, name="ACTIVE/demo"):
    pdir = root / name
    pdir.mkdir(parents=True)
    (pdir / "chapters").mkdir()
    return pdir


def _q(project, **kw):
    params = {"project": project}
    params.update(kw)
    return urllib.parse.urlencode(params)


# ════════════════════════════════════════════════════════════════════
# NER


def test_ner_get_empty(srv, tmp_path):
    srv, port, root = srv()
    pdir = _mk_project(root)
    r = _request(port, "GET", f"/api/ner?{_q('ACTIVE/demo')}")
    assert r["ok"] and r["exists"] is False and r["total"] == 0


def test_ner_get_parsed(srv, tmp_path):
    srv, port, root = srv()
    pdir = _mk_project(root)
    (pdir / "ner.json").write_text(json.dumps([
        {"term": "龙", "type": "name", "translation": "дракон"},
        {"term": "刀", "type": "noun", "translation": "меч"},
        {"term": "剑", "type": "noun", "translation": "клинок"},
    ]), encoding="utf-8")
    r = _request(port, "GET", f"/api/ner?{_q('ACTIVE/demo')}")
    assert r["total"] == 3
    assert r["by_type"] == {"name": 1, "noun": 2}
    assert r["items"][0]["term"] == "龙"


def test_ner_put_roundtrip(srv, tmp_path):
    srv, port, root = srv()
    pdir = _mk_project(root)
    items = [{"term": "A", "type": "noun", "translation": "Б"}]
    r = _request(port, "PUT", "/api/ner",
                 {"project": "ACTIVE/demo", "items": items})
    assert r["ok"] and r["total"] == 1
    got = json.loads((pdir / "ner.json").read_text(encoding="utf-8"))
    assert got[0]["term"] == "A"


def test_ner_put_rejects_non_list(srv, tmp_path):
    srv, port, root = srv()
    _mk_project(root)
    r = _request(port, "PUT", "/api/ner",
                 {"project": "ACTIVE/demo", "items": {"x": 1}})
    assert "__error__" in r and r["__error__"] == 400


# ════════════════════════════════════════════════════════════════════
# Экспорт глоссария (/api/ner/export)

_EXPORT_NER = [
    {"term": "陈阳", "type": "Person (male)", "translation": "Чэнь Ян",
     "count": 12, "notes": "главный герой"},
    {"term": "林水", "type": "Person (female)", "translation": "Линь Шуй",
     "count": 3, "notes": "палладия: нет"},
    {"term": "青云宗", "type": "Organisation", "translation": "Секта",
     "count": 8, "notes": ""},
]


def _mk_ner(pdir):
    (pdir / "ner.json").write_text(
        json.dumps(_EXPORT_NER, ensure_ascii=False), encoding="utf-8")


def test_ner_export_missing_ner(srv, tmp_path):
    srv, port, root = srv()
    _mk_project(root)
    r = _request(port, "GET", f"/api/ner/export?{_q('ACTIVE/demo')}")
    assert "__error__" in r and r["__error__"] == 404


def test_ner_export_json(srv, tmp_path):
    srv, port, root = srv()
    _mk_ner(_mk_project(root))
    q = _q("ACTIVE/demo") + "&format=json"
    r = _request(port, "GET", f"/api/ner/export?{q}")
    assert r["ok"] and r["name"] == "ner_export.json"
    assert r["total"] == 3
    assert json.loads(r["content"])[0]["term"] == "陈阳"


def test_ner_export_text(srv, tmp_path):
    srv, port, root = srv()
    _mk_ner(_mk_project(root))
    q = _q("ACTIVE/demo") + "&format=text"
    r = _request(port, "GET", f"/api/ner/export?{q}")
    assert r["ok"] and r["name"] == "ner_analysis.jsonl"
    assert "Чэнь Ян" in r["content"]  # JSONL: по записи на строку
    lines = [json.loads(l) for l in r["content"].strip().splitlines()]
    assert {rec["term"] for rec in lines} == {"陈阳", "林水", "青云宗"}
    assert lines[0]["translation"] == "Чэнь Ян"
    # R6-C: aliases/голоса не экспортируются (опции убраны)
    assert "aliases" not in r["content"]


def test_ner_export_names(srv, tmp_path):
    srv, port, root = srv()
    _mk_ner(_mk_project(root))
    q = _q("ACTIVE/demo") + "&format=names"
    r = _request(port, "GET", f"/api/ner/export?{q}")
    assert r["ok"] and r["name"] == "ner_names.txt"
    assert "=== ЖЕНСКИЕ ИМЕНА ===" in r["content"]
    assert "Линь Шуй" in r["content"]
    assert "Чэнь Ян" in r["content"].split("=== МУЖСКИЕ ИМЕНА ===")[1]


def test_ner_export_filters(srv, tmp_path):
    srv, port, root = srv()
    _mk_ner(_mk_project(root))
    q = _q("ACTIVE/demo", format="json", count_threshold="5",
           types="Person (male)")
    r = _request(port, "GET", f"/api/ner/export?{q}")
    assert r["total"] == 1
    assert json.loads(r["content"])[0]["term"] == "陈阳"
    # R6-C: exclude_words/range больше не принимаются — игнорируются
    q = _q("ACTIVE/demo", format="json", count_threshold="0",
           exclude_words="палладия", range="1-1")
    r = _request(port, "GET", f"/api/ner/export?{q}")
    assert r["total"] == 3  # фильтры не применены


def test_ner_export_bad_format(srv, tmp_path):
    srv, port, root = srv()
    _mk_ner(_mk_project(root))
    q = _q("ACTIVE/demo", format="xyz")
    r = _request(port, "GET", f"/api/ner/export?{q}")
    assert "__error__" in r and r["__error__"] == 400
    # R6-C: range больше не валидируется — просто игнорируется
    q = _q("ACTIVE/demo", format="json", range="абв")
    r = _request(port, "GET", f"/api/ner/export?{q}")
    assert r["ok"] and r["total"] == 3


# ════════════════════════════════════════════════════════════════════
# Review


def test_ner_review_get_missing(srv, tmp_path):
    srv, port, root = srv()
    _mk_project(root)
    r = _request(port, "GET", f"/api/ner/review?{_q('ACTIVE/demo')}")
    assert r["ok"] and r["exists"] is False and r["content"] == ""


def test_ner_review_put_get(srv, tmp_path):
    srv, port, root = srv()
    pdir = _mk_project(root)
    doc = {"meta": {"stage": "ner"}, "patches": []}
    r = _request(port, "PUT", "/api/ner/review",
                 {"project": "ACTIVE/demo", "content": json.dumps(doc)})
    assert r["ok"]
    assert (pdir / "tmp" / "ner_review.json").is_file()
    r = _request(port, "GET", f"/api/ner/review?{_q('ACTIVE/demo')}")
    assert r["exists"] is True
    assert json.loads(r["content"]) == doc


def test_tcl_review_roundtrip(srv, tmp_path):
    srv, port, root = srv()
    pdir = _mk_project(root)
    r = _request(port, "PUT", "/api/translate_check_llm/review",
                 {"project": "ACTIVE/demo", "content": "[]"})
    assert r["ok"]
    r = _request(port, "GET",
                 f"/api/translate_check_llm/review?{_q('ACTIVE/demo')}")
    assert r["exists"] and r["content"].strip() == "[]"


def test_review_apply_requires_project(srv, tmp_path):
    srv, port, root = srv()
    _mk_project(root)
    r = _request(port, "POST", "/api/ner/review/apply", {})
    assert "__error__" in r and r["__error__"] == 400


def test_review_apply_creates_job(srv, tmp_path):
    """apply → job через JobManager (фейковый скрипт не нужен — без сети
    job упадёт на запуске, но статус/структура вернутся)."""
    srv, port, root = srv()
    _mk_project(root)
    from web.jobs import JobManager
    srv.job_manager = JobManager(tmp_path / "web", repo_root=REPO)
    r = _request(port, "POST", "/api/ner/review/apply",
                 {"project": "ACTIVE/demo", "dry_run": True})
    assert r["ok"] and "job" in r
    assert r["job"]["action"] == "ner_check"
    # job существует в менеджере
    job = srv.job_manager.get(r["job"]["id"])
    assert job is not None


def test_review_apply_passes_no_bak(srv, tmp_path):
    """no_bak в body → параметр задачи (--no-bak соберётся в argv).
    ner-путь: argv обязан содержать --apply (применение правок, а не
    полный LLM-прогон); флаги применения не пишутся в pdir/.env."""
    srv, port, root = srv()
    pdir = _mk_project(root)
    from web.jobs import JobManager
    srv.job_manager = JobManager(tmp_path / "web", repo_root=REPO)
    r = _request(port, "POST", "/api/translate_check_llm/review/apply",
                 {"project": "ACTIVE/demo", "no_bak": True})
    assert r["ok"]
    job = srv.job_manager.get(r["job"]["id"])
    assert job is not None and "--no-bak" in job.argv
    # ждём завершения первой задачи (per-project лок на запуск)
    for _ in range(50):
        j = srv.job_manager.get(job.id)
        if j is not None and j.status != "running":
            break
        time.sleep(0.05)
    # без флага — бэкапы по умолчанию включены (флага нет)
    r2 = _request(port, "POST", "/api/ner/review/apply",
                  {"project": "ACTIVE/demo"})
    job2 = srv.job_manager.get(r2["job"]["id"])
    assert job2 is not None and "--no-bak" not in job2.argv
    # ner: применение, а не LLM-прогон (регрессия build_ner_check)
    assert "--apply" in job2.argv
    # путь «Проверки» не пишет настройки в pdir/.env (apply в .env — шум)
    env_file = pdir / ".env"
    if env_file.is_file():
        assert "NER_CHECK_APPLY" not in env_file.read_text(encoding="utf-8")


# ════════════════════════════════════════════════════════════════════
# настройки: страница «Настройки» (реестр) и один общий .env
# ════════════════════════════════════════════════════════════════════
def _settings_srv(srv, tmp_path, monkeypatch, text=None):
    """Сервер с общим .env в tmp/repo/.env: WEB_ENV_FILE указывает на него,
    поэтому конфиг машины и реальный корневой .env не мешаются."""
    (tmp_path / "repo").mkdir(parents=True, exist_ok=True)
    env = tmp_path / "repo" / ".env"
    env.write_text(text or "", encoding="utf-8")
    monkeypatch.setenv("WEB_ENV_FILE", str(env))
    return srv(projects_root=tmp_path / "prj", repo_root=tmp_path / "repo")


def _settings_fields(payload):
    """{имя поля: поле} из payload /api/settings."""
    return {f["name"]: f
            for g in payload["groups"] for b in g["blocks"]
            for f in b["fields"]}


def _env_text(tmp_path):
    env = tmp_path / "repo" / ".env"
    return env.read_text(encoding="utf-8") if env.is_file() else ""


def test_settings_get_masked(srv, tmp_path, monkeypatch):
    """GET /api/settings — блоки реестра со значениями; секрет отдаётся
    маской «••••», содержимое файла в SPA не ездит."""
    _srv, port, root = _settings_srv(
        srv, tmp_path, monkeypatch,
        "API_KEY=supersecret\nHOST=http://x:1\n")
    r = _request(port, "GET", "/api/settings")
    assert r["ok"] and r["exists"] is True
    assert r["path"] == str(tmp_path / "repo" / ".env")
    f = _settings_fields(r)
    assert f["api_key"]["secret"] is True
    assert f["api_key"]["key"] == "API_KEY"   # сохран — по ключу, не по имени
    assert f["api_key"]["value"] == "••••"
    assert f["host"]["value"] == "http://x:1"
    dumped = json.dumps(r, ensure_ascii=False)
    assert "supersecret" not in dumped and "content" not in r


def test_settings_get_env_wins(srv, tmp_path, monkeypatch):
    """Переменная окружения процесса перекрывает общий файл — интерфейс про
    это знает (env_wins), иначе правка в форме «не применяется»."""
    _srv, port, root = _settings_srv(srv, tmp_path, monkeypatch,
                                     "HOST=http://from-file:1\n")
    monkeypatch.setenv("HOST", "http://from-env:1")
    r = _request(port, "GET", "/api/settings")
    assert r["env_wins"] == ["HOST"]
    assert _settings_fields(r)["host"]["value"] == "http://from-env:1"


def test_settings_hidden_only_here(srv, tmp_path, monkeypatch):
    """Скрытые настройки конвейера видны только на «Настройках»: в форму
    запусков они не попадают (стадия берёт значение из конфига)."""
    _srv, port, root = _settings_srv(srv, tmp_path, monkeypatch)
    assert {"ner_threshold", "ner_ngram"} <= set(
        _settings_fields(_request(port, "GET", "/api/settings")))
    spec = _request(port, "GET", "/api/stages/pipeline/spec")
    assert not {"ner_threshold", "ner_ngram"} & {
        f["name"] for f in spec["spec"]["fields"]}


def test_settings_put_writes_registry_file(srv, tmp_path, monkeypatch):
    """PUT /api/settings — машиночитаемый файл: шапка реестра и ключи в
    порядке реестра; прежние ключи целы (PAGE PUT не теряет их)."""
    _srv, port, root = _settings_srv(srv, tmp_path, monkeypatch,
                                     "HOST=http://keep:1\n")
    r = _request(port, "PUT", "/api/settings",
                 {"values": {"THREADS": "8", "NER_CHUNK_SIZE": "12345"}})
    assert r["ok"], r
    assert set(r["keys"]) == {"HOST", "THREADS", "NER_CHUNK_SIZE"}
    text = _env_text(tmp_path)
    assert text.startswith("# NovelMaestro — общие настройки.")
    assert "HOST=http://keep:1" in text
    assert "THREADS=8" in text and "NER_CHUNK_SIZE=12345" in text
    assert text.index("HOST=") < text.index("THREADS=") < text.index(
        "NER_CHUNK_SIZE=")


def test_settings_put_empty_removes_key_then_file(srv, tmp_path, monkeypatch):
    """Пустое значение снимает ключ, последний ключ removes файл: «наследует
    встроенный дефолт» не должно оставаться строкой в конфиге."""
    _srv, port, root = _settings_srv(srv, tmp_path, monkeypatch,
                                     "HOST=http://x:1\n")
    r = _request(port, "PUT", "/api/settings", {"values": {"HOST": ""}})
    assert r["ok"] and r["keys"] == [] and r["exists"] is False
    assert not (tmp_path / "repo" / ".env").exists()


def test_settings_put_unknown_key_rejected(srv, tmp_path, monkeypatch):
    """Ключи — только имена реестра: чужое отклоняется, а не дописывается
    в файл (иначе «одно место истины» распадается)."""
    _srv, port, root = _settings_srv(srv, tmp_path, monkeypatch, "A=1\n")
    for bad in ("GLOBAL", "A B", "A.KEY", "АБВ", "A\nB"):
        r = _request(port, "PUT", "/api/settings", {"values": {bad: "x"}})
        assert r.get("__error__") == 400, bad
    assert _env_text(tmp_path) == "A=1\n"


def test_settings_put_run_params_not_persisted(srv, tmp_path, monkeypatch):
    """Параметры запуска (run=True) в общий .env не пишутся: изменённые для
    одной книги поля — рабочее состояние браузера, не конфиг."""
    _srv, port, root = _settings_srv(srv, tmp_path, monkeypatch)
    r = _request(port, "PUT", "/api/settings",
                 {"values": {"NER_PROMPT_FILE": "my.txt", "MODEL": "m"}})
    assert r["ok"] and r["keys"] == ["MODEL"]
    assert "NER_PROMPT_FILE" not in _env_text(tmp_path)


def test_settings_put_secret_mask_skipped(srv, tmp_path, monkeypatch):
    """«••••» прилетело вместо значения — ключ не трогаем (иначе маска
    стёрла бы настоящий ключ)."""
    _srv, port, root = _settings_srv(srv, tmp_path, monkeypatch,
                                     "API_KEY=настоящий\n")
    r = _request(port, "PUT", "/api/settings",
                 {"values": {"API_KEY": "••••", "MODEL": "m"}})
    assert r["ok"]
    text = _env_text(tmp_path)
    assert "API_KEY=настоящий" in text and "••••" not in text


def test_settings_put_hash_value_quoted(srv, tmp_path, monkeypatch):
    """AGENTS §7: «#» вне кавычек начинает комментарий, поэтому значение с
    решёткой пишется в кавычках и читается целиком."""
    _srv, port, root = _settings_srv(srv, tmp_path, monkeypatch)
    r = _request(port, "PUT", "/api/settings", {"values": {"MODEL": "a # b"}})
    assert r["ok"]
    text = _env_text(tmp_path)
    assert 'MODEL="a # b"' in text, text
    assert core_common.parse_dotenv(str(tmp_path / "repo" / ".env"))[
        "MODEL"] == "a # b"


def test_settings_put_textarea_newlines_are_literal(srv, tmp_path, monkeypatch):
    """Переносы textarea-поля — литералом «\\n»: один ключ = одна строка,
    обратно разворачиваются в настоящие переносы."""
    _srv, port, root = _settings_srv(srv, tmp_path, monkeypatch)
    r = _request(port, "PUT", "/api/settings",
                 {"values": {"TRANSLATE_CHECK_REGEXP_CHECKS": "a -> b\nc -> d"}})
    assert r["ok"]
    raw = core_common.parse_dotenv(str(tmp_path / "repo" / ".env"))
    assert raw["TRANSLATE_CHECK_REGEXP_CHECKS"] == "a -> b\\nc -> d"
    f = _settings_fields(_request(port, "GET", "/api/settings"))
    assert f["regexp_checks"]["value"] == "a -> b\nc -> d"


def test_settings_book_env_is_not_a_layer(srv, tmp_path, monkeypatch):
    """Собственного .env у книги больше нет: GET/PUT — только общий файл,
    значение книги не перекрыивает общий конфиг."""
    _srv, port, root = _settings_srv(srv, tmp_path, monkeypatch,
                                     "HOST=http://shared:1\n")
    pdir = _mk_project(root)
    (pdir / ".env").write_text("HOST=http://book\nNER_CHUNK_SIZE=999\n",
                               encoding="utf-8")
    r = _request(port, "GET", "/api/settings")
    assert _settings_fields(r)["host"]["value"] == "http://shared:1"
    spec = _request(port, "GET", f"/api/stages/ner/spec?{_q('ACTIVE/demo')}")
    f = {x["name"]: x.get("default") for x in spec["spec"]["fields"]}
    assert f["chunk_size"] != "999"
    # PUT с project= пишет в общий файл; файл книги остаётся обычным файлом
    r = _request(port, "PUT", "/api/settings",
                 {"project": "ACTIVE/demo", "values": {"MODEL": "m"}})
    assert r["ok"] and "HOST=http://book" in (pdir / ".env").read_text(
        encoding="utf-8")


def test_env_editor_routes_gone(srv, tmp_path, monkeypatch):
    """Редактор текста .env удалён целиком: GET/PUT/DELETE /api/env и
    /api/env/template больше не существуют — только /api/settings."""
    _srv, port, root = _settings_srv(srv, tmp_path, monkeypatch)
    for method, path in (("GET", "/api/env"), ("PUT", "/api/env"),
                         ("DELETE", "/api/env"),
                         ("GET", "/api/env/template")):
        r = _request(port, method, path, {"content": "A=1\n"})
        assert r.get("__error__") in (404, 405), (method, path, r)


# ════════════════════════════════════════════════════════════════════
# metadata


def test_metadata_get_put(srv, tmp_path):
    srv, port, root = srv()
    pdir = _mk_project(root)
    r = _request(port, "GET", f"/api/metadata?{_q('ACTIVE/demo')}")
    assert r["ok"] and r["exists"] is False
    r = _request(port, "PUT", "/api/metadata",
                 {"project": "ACTIVE/demo", "content": "title: \"Книга\"\n"})
    assert r["ok"]
    assert (pdir / "source" / "metadata.yaml").is_file()
    r = _request(port, "GET", f"/api/metadata?{_q('ACTIVE/demo')}")
    assert r["exists"] and "Книга" in r["content"]


# ════════════════════════════════════════════════════════════════════
# prompts


def test_prompts_list_no_tags(srv, tmp_path):
    """Список промптов — без тегов (теги в списке убраны)."""
    srv, port, root = srv()
    pdir = _mk_project(root)
    pr = pdir / "prompts"
    pr.mkdir()
    (pr / "translate_prompt.txt").write_text(
        "<translate>\nПереведи {original_text}\n", encoding="utf-8")
    (pr / "notes.txt").write_text("без тегов\n", encoding="utf-8")
    r = _request(port, "GET", f"/api/prompts?{_q('ACTIVE/demo')}")
    assert r["ok"] and len(r["prompts"]) == 2
    by_name = {p["name"]: p for p in r["prompts"]}
    assert "tags" not in by_name["translate_prompt.txt"]
    assert by_name["translate_prompt.txt"]["size"] > 0


def test_prompts_get_put(srv, tmp_path):
    srv, port, root = srv()
    pdir = _mk_project(root)
    pr = pdir / "prompts"
    pr.mkdir()
    (pr / "p.txt").write_text("old\n", encoding="utf-8")
    r = _request(port, "GET", f"/api/prompts/p.txt?{_q('ACTIVE/demo')}")
    assert r["content"] == "old\n"
    r = _request(port, "PUT", "/api/prompts/p.txt",
                 {"project": "ACTIVE/demo", "content": "new\n"})
    assert r["ok"]
    assert (pr / "p.txt").read_text(encoding="utf-8") == "new\n"


def test_prompts_get_escapes_project(srv, tmp_path):
    srv, port, root = srv()
    pdir = _mk_project(root)
    (pdir / "prompts").mkdir()
    (pdir / "secret.txt").write_text("top\n", encoding="utf-8")
    r = _request(port, "GET",
                 f"/api/prompts/{urllib.parse.quote('../secret.txt', safe='')}"
                 f"?{_q('ACTIVE/demo')}")
    assert "__error__" in r and r["__error__"] == 400


def test_prompts_template_from_repo(srv, tmp_path):
    """Шаблоны берутся из настоящего templates/ репо."""
    srv, port, root = srv()
    _mk_project(root)
    r = _request(port, "GET",
                 f"/api/prompts/pipeline_prompt.txt/template"
                 f"?{_q('ACTIVE/demo')}")
    assert r["ok"] and r["templates"]
    assert any(t["set"] == "General" for t in r["templates"])


def test_prompts_template_missing(srv, tmp_path):
    srv, port, root = srv()
    _mk_project(root)
    r = _request(port, "GET",
                 f"/api/prompts/nope.txt/template?{_q('ACTIVE/demo')}")
    assert "__error__" in r and r["__error__"] == 404


# ════════════════════════════════════════════════════════════════════
# Логи (M8)


def test_logs_list(srv, tmp_path):
    """дерево логов — рекурсивно по logs/, только *.log,
    path — относительный путь от logs/."""
    srv, port, root = srv()
    pdir = _mk_project(root)
    (pdir / "logs").mkdir()
    (pdir / "logs" / "run.log").write_text("line1\n", encoding="utf-8")
    (pdir / "logs" / "readme.txt").write_text("не лог\n", encoding="utf-8")
    (pdir / "logs" / "chapters").mkdir()
    (pdir / "logs" / "chapters" / "ch1.log").write_text("x\n", encoding="utf-8")
    r = _request(port, "GET", f"/api/logs?{_q('ACTIVE/demo')}")
    assert r["ok"]
    by_path = {l["path"]: l for l in r["logs"]}
    assert set(by_path) == {"run.log", "chapters/ch1.log"}
    assert by_path["run.log"]["name"] == "run.log"
    assert by_path["chapters/ch1.log"]["name"] == "ch1.log"


def test_logs_read_tail(srv, tmp_path):
    srv, port, root = srv()
    pdir = _mk_project(root)
    (pdir / "logs").mkdir()
    (pdir / "logs" / "run.log").write_text("AAA\nBBB\nCCC\n", encoding="utf-8")
    # хвост 8 байт: "BBB\nCCC\n"
    r = _request(port, "GET",
                 f"/api/logs/run.log?{_q('ACTIVE/demo', tail=8)}")
    assert r["ok"] and r["content"] == "BBB\nCCC\n"
    assert r["size"] == 12


def test_logs_read_full(srv, tmp_path):
    srv, port, root = srv()
    pdir = _mk_project(root)
    (pdir / "logs").mkdir()
    (pdir / "logs" / "run.log").write_text("ABC\n", encoding="utf-8")
    r = _request(port, "GET", f"/api/logs/run.log?{_q('ACTIVE/demo')}")
    assert r["content"] == "ABC\n" and r["start"] == 0


def test_logs_delete_one(srv, tmp_path):
    """DELETE /api/logs/{name} — один файл (в т.ч. из подпапки)."""
    srv, port, root = srv()
    pdir = _mk_project(root)
    (pdir / "logs").mkdir()
    (pdir / "logs" / "run.log").write_text("A\n", encoding="utf-8")
    (pdir / "logs" / "chapters").mkdir()
    (pdir / "logs" / "chapters" / "ch1.log").write_text("B\n",
                                                            encoding="utf-8")
    r = _request(port, "DELETE",
                 f"/api/logs/run.log?{_q('ACTIVE/demo')}")
    assert r["ok"] and not (pdir / "logs" / "run.log").exists()
    assert (pdir / "logs" / "chapters" / "ch1.log").exists()
    r2 = _request(port, "DELETE",
                  f"/api/logs/ch1.log?{_q('ACTIVE/demo', dir='chapters')}")
    assert r2["ok"] and not (pdir / "logs" / "chapters" / "ch1.log").exists()
    # несуществующий — 404
    r3 = _request(port, "DELETE", f"/api/logs/nope.log?{_q('ACTIVE/demo')}")
    assert "__error__" in r3 and r3["__error__"] == 404


def test_logs_delete_all(srv, tmp_path):
    """DELETE /api/logs — все *.log, не-.log и папки не трогаем."""
    srv, port, root = srv()
    pdir = _mk_project(root)
    (pdir / "logs").mkdir()
    (pdir / "logs" / "run.log").write_text("A\n", encoding="utf-8")
    (pdir / "logs" / "notes.txt").write_text("не лог\n", encoding="utf-8")
    (pdir / "logs" / "chapters").mkdir()
    (pdir / "logs" / "chapters" / "ch1.log").write_text("B\n",
                                                            encoding="utf-8")
    r = _request(port, "DELETE", f"/api/logs?{_q('ACTIVE/demo')}")
    assert r["ok"] and len(r["deleted"]) == 2
    assert not (pdir / "logs" / "run.log").exists()
    assert not (pdir / "logs" / "chapters" / "ch1.log").exists()
    assert (pdir / "logs" / "notes.txt").exists()
    assert (pdir / "logs" / "chapters").is_dir()


def test_logs_delete_escapes(srv, tmp_path):
    """удаление лога не уходит за пределы logs/."""
    srv, port, root = srv()
    pdir = _mk_project(root)
    (pdir / "logs").mkdir()
    (pdir / "secret.txt").write_text("top\n", encoding="utf-8")
    r = _request(port, "DELETE",
                 f"/api/logs/{urllib.parse.quote('../secret.txt', safe='')}"
                 f"?{_q('ACTIVE/demo')}")
    assert "__error__" in r and r["__error__"] == 400
    assert (pdir / "secret.txt").exists()
    # dir=.. / абсолютный dir — база перекрывается ДО join, песочница
    r2 = _request(port, "DELETE",
                  f"/api/logs/secret.txt?{_q('ACTIVE/demo', dir='..')}")
    assert "__error__" in r2 and r2["__error__"] == 400
    assert (pdir / "secret.txt").exists()
    r3 = _request(port, "DELETE",
                  f"/api/logs/secret.txt?{_q('ACTIVE/demo', dir='/etc')}")
    assert "__error__" in r3 and r3["__error__"] == 400
    assert (pdir / "secret.txt").exists()


def test_logs_read_missing(srv, tmp_path):
    srv, port, root = srv()
    _mk_project(root)
    r = _request(port, "GET", f"/api/logs/nope.log?{_q('ACTIVE/demo')}")
    assert "__error__" in r and r["__error__"] == 404


def test_logs_escape_sandbox(srv, tmp_path):
    srv, port, root = srv()
    pdir = _mk_project(root)
    (pdir / "logs").mkdir()
    (pdir / "secret.txt").write_text("top\n", encoding="utf-8")
    r = _request(port, "GET",
                 f"/api/logs/{urllib.parse.quote('../secret.txt', safe='')}"
                 f"?{_q('ACTIVE/demo')}")
    assert "__error__" in r and r["__error__"] == 400


def test_prompts_templates_in_list_and_create(srv, tmp_path):
    """W4: в списке промптов есть шаблоны; промпт создаётся из шаблона."""
    _srv, port, root = srv(projects_root=tmp_path / "prj")
    proj = tmp_path / "prj" / "ACTIVE" / "demo"
    (proj / "prompts").mkdir(parents=True)
    r = _request(port, "GET", f"/api/prompts?{_q('ACTIVE/demo')}")
    assert r.get("ok")
    names = [t["name"] for t in r["templates"]]
    assert names, "шаблоны из templates/ не найдены"
    assert r["prompts"] == []
    # создание из шаблона: PUT с содержимым шаблона
    tpl_name = names[0]
    d = _request(port, "GET",
                 f"/api/prompts/{tpl_name}/template?{_q('ACTIVE/demo')}")
    assert d.get("ok")
    content = d["templates"][0]["content"]
    r2 = _request(port, "PUT", f"/api/prompts/{tpl_name}",
                  body={"project": "ACTIVE/demo", "content": content})
    assert r2.get("ok")
    assert (proj / "prompts" / tpl_name).is_file()


# ════════════════════════════════════════════════════════════════════
# W6: env по канону + видимые значения; обложка
# ════════════════════════════════════════════════════════════════════

def test_files_list_shows_book_env(srv, tmp_path, monkeypatch):
    """Свой .env книги остаётся обычным файлом в «Файлах» (его видно и можно
    править руками) — интерфейсом «Настроек» он не читается."""
    _srv, port, root = _settings_srv(srv, tmp_path, monkeypatch)
    pdir = _mk_project(root)
    (pdir / ".env").write_text("PIPELINE_JOBS=7\n", encoding="utf-8")
    r = _request(port, "GET", f"/api/files?{_q('ACTIVE/demo')}")
    assert r["ok"]
    assert ".env" in {e["name"] for e in r["entries"]}


def test_settings_path_is_repo_root_env(srv, tmp_path, monkeypatch):
    """Общий конфиг — корневой .env репо: projects/.env им не считается."""
    _srv, port, root = _settings_srv(srv, tmp_path, monkeypatch, "HOST=1\n")
    (tmp_path / "prj").mkdir(parents=True, exist_ok=True)
    (tmp_path / "prj" / ".env").write_text("HOST=2\n", encoding="utf-8")
    r = _request(port, "GET", "/api/settings")
    assert r["path"] == str(tmp_path / "repo" / ".env")
    assert _settings_fields(r)["host"]["value"] == "1"


def test_settings_put_creates_file_from_scratch(srv, tmp_path, monkeypatch):
    """Общего файла ещё нет — PUT создаёт его целиком из значений реестра;
    повторный PUT перезаписывает, а не дописывает."""
    _srv, port, root = _settings_srv(srv, tmp_path, monkeypatch)
    r = _request(port, "PUT", "/api/settings",
                 {"values": {"HOST": "http://h:1", "MODEL": "m"}})
    assert r["ok"] and r["exists"] is True
    env = tmp_path / "repo" / ".env"
    text = env.read_text(encoding="utf-8")
    assert "HOST=http://h:1" in text and "MODEL=m" in text
    r2 = _request(port, "PUT", "/api/settings", {"values": {"HOST": "http://h:2"}})
    assert r2["ok"] and set(r2["keys"]) == {"HOST", "MODEL"}
    text2 = env.read_text(encoding="utf-8")
    assert "HOST=http://h:2" in text2 and "MODEL=m" in text2


def test_prompts_delete(srv, tmp_path):
    """DELETE /api/prompts/{name} удаляет файл промпта."""
    _srv, port, root = srv(projects_root=tmp_path / "prj",
                           repo_root=tmp_path / "repo")
    pdir = _mk_project(root)
    (pdir / "prompts").mkdir()
    (pdir / "prompts" / "x.txt").write_text("<translate>\ntxt\n",
                                              encoding="utf-8")
    r = _request(port, "DELETE",
                 f"/api/prompts/x.txt?{_q('ACTIVE/demo')}")
    assert r["ok"] and not (pdir / "prompts" / "x.txt").exists()
    # повторное удаление — 404 (файла уже нет)
    r2 = _request(port, "DELETE",
                  f"/api/prompts/x.txt?{_q('ACTIVE/demo')}")
    assert "__error__" in r2 and r2["__error__"] == 404


def test_cover_roundtrip(srv, tmp_path):
    """W6: обложка — загрузка (base64), статус, удаление."""
    import base64
    _srv, port, root = srv(projects_root=tmp_path / "prj",
                           repo_root=tmp_path / "repo")
    pdir = _mk_project(root)
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-image-data").decode()  # L6: реальная PNG-магия
    r = _request(port, "PUT", "/api/cover",
                 body={"project": "ACTIVE/demo", "name": "pic.png",
                       "content_base64": png})
    assert r["ok"] and r["exists"] and r["name"] == "cover.png"
    assert (pdir / "source" / "cover.png").is_file()
    r2 = _request(port, "GET", f"/api/cover?{_q('ACTIVE/demo')}")
    assert r2["exists"] and r2["size"] > 0
    r3 = _request(port, "DELETE", f"/api/cover?{_q('ACTIVE/demo')}")
    assert r3["ok"] and not r3["exists"]
    assert not (pdir / "source" / "cover.png").exists()


def test_cover_rejects_bad_ext(srv, tmp_path):
    """W6: обложка только jpg/png/jpeg (webp убран — не читается
    в EPUB/FB2)."""
    import base64
    _srv, port, root = srv(projects_root=tmp_path / "prj",
                           repo_root=tmp_path / "repo")
    _mk_project(root)
    for ext in ("x.gif", "x.webp"):
        r = _request(port, "PUT", "/api/cover",
                     body={"project": "ACTIVE/demo", "name": ext,
                           "content_base64":
                               base64.b64encode(b"fake").decode()})
        assert "__error__" in r and r["__error__"] == 400


def test_download_inline_image(srv, tmp_path):
    """W6: download?inline=1 отдаёт картинку без attachment."""
    import http.client
    import urllib.parse
    _srv, port, root = srv(projects_root=tmp_path / "prj",
                           repo_root=tmp_path / "repo")
    pdir = _mk_project(root)
    (pdir / "source").mkdir()
    (pdir / "source" / "cover.jpg").write_bytes(b"\xff\xd8fake")
    qs = urllib.parse.urlencode({"project": "ACTIVE/demo",
                                 "path": "source/cover.jpg", "inline": "1"})
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", f"/api/download?{qs}")
    resp = conn.getresponse()
    body = resp.read()
    cd = resp.getheader("Content-Disposition")
    conn.close()
    assert resp.status == 200
    assert cd is None
    assert resp.getheader("Content-Type") == "image/jpeg"
    assert body.startswith(b"\xff\xd8")


def test_settings_hidden_when_auth_enabled(tmp_path, monkeypatch):
    """W6: при --auth настройки доступны только с сессией, а API-ключ
    по-прежнему скрыт: значения и текст файла наружу не уходят."""
    env = tmp_path / "repo" / ".env"
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text("HOST=http://x:1\nAPI_KEY=xyz\n", encoding="utf-8")
    monkeypatch.setenv("WEB_ENV_FILE", str(env))
    import threading
    auth_obj = Auth("tok", no_auth=False)
    srv = make_server("127.0.0.1", 0, auth_obj,
                      repo_root=REPO, projects_root=tmp_path / "prj")
    web_api.register(srv.router, "127.0.0.1")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        import http.client
        port = srv.server_address[1]
        # без сессии — настройки недоступны вовсе
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/api/settings",
                     headers={"X-Requested-With": "fetch"})
        resp = conn.getresponse()
        body = json.loads(resp.read().decode())
        conn.close()
        assert resp.status == 401, body
        assert "http://x:1" not in json.dumps(body, ensure_ascii=False)
        # вход по токену → сессионная cookie
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", "/api/login", json.dumps({"token": "tok"}),
                     {"Content-Type": "application/json",
                      "X-Requested-With": "fetch"})
        resp = conn.getresponse()
        resp.read()
        cookie = resp.getheader("Set-Cookie") or ""
        conn.close()
        assert resp.status == 200, f"вход не удался: {resp.status}"
        sid = cookie.split("web_session=", 1)[1].split(";", 1)[0]
        # с сессией — значения есть, но ключ — только маской
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/api/settings",
                     headers={"Cookie": f"web_session={sid}",
                              "X-Requested-With": "fetch"})
        resp = conn.getresponse()
        r = json.loads(resp.read().decode())
        conn.close()
        assert r.get("ok"), f"ответ: {r}"
        assert "content" not in r
        assert "xyz" not in json.dumps(r, ensure_ascii=False)
        f = _settings_fields(r)
        assert f["api_key"]["value"] == "\u2022\u2022\u2022\u2022"
        assert f["host"]["value"] == "http://x:1"
    finally:
        threading.Thread(target=srv.shutdown, daemon=True).start()
        srv.server_close()


# ════════════════════════════════════════════════════════════════════
# W7: отчёты translate_check
# ════════════════════════════════════════════════════════════════════

CHECK_FIXTURE = """=== Отчёт о проверке перевода (polished) ===
Диапазон глав : 1 – 3
Папка глав    : /tmp/x/chapters
Сравнения     : redacted (1.0±0.05)
Режим         : strict
Единицы       : размеры в байтах; ratio — безразмерная эвристика
Дата          : Thu Aug 13 09:29:15 2026
Всего папок   : 3
---------------------------------
1. Папка: ./chapters/00000_1_第1章
  - Английский текст: NPC

2. Дубль папок: a, b
  [FATAL] Глава пропущена.

3. Папка: ./chapters/00000_3_第3章
[ВНИМАНИЕ] Глава 3: файл типа 'polished' не найден.


--- Сводка ---
Проверено глав : 3
С ошибками     : 3
Пропущено      : 0
"""


def test_parse_check_report_fixture():
    """W7: парсер отчёта — метаданные, entries, FATAL, ./-обрезка."""
    from web.api import _parse_check_report
    d = _parse_check_report(CHECK_FIXTURE)
    assert d["type"] == "polished"
    assert d["range"] == "1 – 3"
    assert d["checked"] == "3" and d["failed"] == "3"
    assert len(d["entries"]) == 3
    e1 = d["entries"][0]
    assert e1["chapter"] == 1
    assert e1["dir"] == "chapters/00000_1_第1章"  # ./ отрезан
    assert e1["errors"] == ["- Английский текст: NPC"]
    e2 = d["entries"][1]
    assert e2["fatal"] is True
    assert e2["dir"] == ""
    e3 = d["entries"][2]
    assert not e3["fatal"]
    assert any("не найден" in x for x in e3["errors"])


def test_check_reports_endpoint(srv, tmp_path):
    """W7: GET /api/check — список и разбор отчётов проекта."""
    _srv, port, root = srv(projects_root=tmp_path / "prj",
                           repo_root=tmp_path / "repo")
    pdir = _mk_project(root)
    logs = pdir / "logs"
    logs.mkdir()
    (logs / "check_polished_1-3.txt").write_text(CHECK_FIXTURE, encoding="utf-8")
    r = _request(port, "GET", f"/api/check?{_q('ACTIVE/demo')}")
    assert r.get("ok")
    assert len(r["reports"]) == 1
    rep = r["reports"][0]
    assert rep["name"] == "check_polished_1-3.txt"
    assert rep["type"] == "polished"
    assert len(rep["entries"]) == 3


# ════════════════════════════════════════════════════════════════════
# R9: настройки запусков (.env)


def test_stage_spec_prefill_from_shared_env(srv, tmp_path, monkeypatch):
    """R9-A: форма стадии предзаполняется общим конфигом (реестр → общий
    .env → os.environ). Стадийных <СТАДИЯ>_HOST/MODEL и слоя pdir/.env
    больше нет: LLM-полей в форме запусков тоже нет."""
    shared = tmp_path / "shared.env"
    shared.write_text(
        "HOST=http://192.168.1.8:9989\nMODEL=gpt-test\n"
        "NER_CHUNK_SIZE=12345\nNER_MODEL=ner-gpt\n", encoding="utf-8")
    monkeypatch.setenv("WEB_ENV_FILE", str(shared))
    srv, port, root = srv()
    _mk_project(root)
    r = _request(port, "GET", f"/api/stages/ner/spec?{_q('ACTIVE/demo')}")
    assert "__error__" not in r
    fields = {f["name"]: f.get("default", "") for f in r["spec"]["fields"]}
    assert str(fields.get("chunk_size")) == "12345"
    # сервер/модель/ключ/потоки — общие: в форме стадии их больше нет
    assert not {"host", "model", "api_key", "threads"} & set(fields)
    # NER_MODEL мёртв: модель одна на весь конвейер
    assert "ner-gpt" not in json.dumps(r, ensure_ascii=False)


def test_stage_spec_env_no_project(srv, tmp_path):
    """R9-A: без project спека не трогает .env (дефолты из спекуляции)."""
    srv, port, root = srv()
    _mk_project(root)
    r = _request(port, "GET", "/api/stages/ner/spec")
    assert "__error__" not in r
    fields = {f["name"]: f.get("default", "") for f in r["spec"]["fields"]}
    assert fields.get("chunk_size") != "12345"


def test_job_start_does_not_copy_shared_env(srv, tmp_path):
    """Запуск больше НЕ копирует общий .env в книгу: без изменённых
    полей файла книги вообще нет — она целиком наследует общий конфиг
    (и его правки), а секреты в книгу не дублируются."""
    srv, port, root = srv(repo_root=tmp_path / "repo")
    pdir = _mk_project(root)
    (tmp_path / "repo" / "cli").mkdir(parents=True, exist_ok=True)
    (tmp_path / "repo" / "cli" / "translate_check.py").write_text(
        "import sys\nsys.exit(0)\n", encoding="utf-8")
    (tmp_path / "repo" / ".env").write_text(
        "HOST=http://sys\nAPI_KEY=СЕКРЕТ-СИСТЕМНЫЙ\n"
        "REMOTE_API_KEY=другой-секрет\nMODEL=m\n", encoding="utf-8")
    from web.jobs import JobManager
    srv.job_manager = JobManager(tmp_path / "web", repo_root=REPO)
    r = _request(port, "POST", "/api/jobs",
                 {"action": "translate_check", "project": "ACTIVE/demo",
                  "params": {"preset": "polished"}})
    assert "__error__" not in r and r.get("ok")
    assert not (pdir / ".env").exists()


def test_job_start_uses_shared_llm_config(srv, tmp_path, monkeypatch):
    """R9-B: запуск получает LLM-конфиг из общего файла — сервер и модель
    уходят в argv, ключ — в окружение процесса; файл книги не создаётся
    вовсе (изменённые поля запусков живут в браузере)."""
    env = tmp_path / "repo" / ".env"
    monkeypatch.setenv("WEB_ENV_FILE", str(env))
    srv, port, root = srv(repo_root=tmp_path / "repo")
    pdir = _mk_project(root)
    (tmp_path / "repo" / "cli").mkdir(parents=True, exist_ok=True)
    (tmp_path / "repo" / "cli" / "ner.py").write_text(
        "import sys\nsys.exit(0)\n", encoding="utf-8")
    env.write_text(
        "HOST=http://sys\nAPI_KEY=СЕКРЕТ-ОБЩИЙ\nMODEL=m\n"
        "NER_CHUNK_SIZE=8000\n", encoding="utf-8")
    from web.jobs import JobManager
    srv.job_manager = JobManager(tmp_path / "web", repo_root=REPO)
    r = _request(port, "POST", "/api/jobs",
                 {"action": "ner", "project": "ACTIVE/demo",
                  "params": {"chunk_size": "1200"}})
    assert "__error__" not in r and r.get("ok"), r
    assert not (pdir / ".env").exists(), "файл отличий книги больше не пишется"
    job = srv.job_manager.get(r["job"]["id"])
    joined = " ".join(job.argv)
    assert "--chunk_size 1200" in joined      # значение из формы запусков
    assert "--host http://sys" in joined      # сервер — общий, не из формы
    assert "--model m" in joined and "--api_key" not in joined
    assert "СЕКРЕТ-ОБЩИЙ" not in joined      # ключ в argv не едет

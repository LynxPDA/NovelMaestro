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
    assert r["ok"] and r["name"] == "ner_analysis.txt"
    assert "Чэнь Ян" in r["content"]
    # R6-C: aliases/голоса не экспортируются (опции убраны)
    assert "aliases:" not in r["content"]


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
    assert (pdir / "ner_review.json").is_file()
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
    """no_bak в body → параметр задачи (--no-bak соберётся в argv)."""
    srv, port, root = srv()
    _mk_project(root)
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


# ════════════════════════════════════════════════════════════════════
# env


def test_env_get_masked(srv, tmp_path):
    """Значения НЕ утекают: только ключи и маска ••••."""
    srv, port, root = srv()
    pdir = _mk_project(root)
    (pdir / ".env").write_text(
        "# комментарий\nAPI_KEY=supersecret\nHOST=http://x:1\n",
        encoding="utf-8")
    r = _request(port, "GET", f"/api/env?{_q('ACTIVE/demo')}")
    assert r["ok"] and r["exists"] is True
    assert r["keys"] == ["API_KEY", "HOST"]
    assert "supersecret" not in r["masked"]
    assert "http://x:1" not in r["masked"]
    assert "API_KEY=••••" in r["masked"]
    assert "HOST=••••" in r["masked"]
    assert "# комментарий" in r["masked"]


def test_notes_get_put_roundtrip(srv, tmp_path):
    """GET/PUT /api/notes — projects/notes.md."""
    _srv, port, root = srv(projects_root=tmp_path / "prj")
    (tmp_path / "prj").mkdir(parents=True, exist_ok=True)
    r = _request(port, "GET", "/api/notes")
    assert r["ok"] and not r["exists"] and r["content"] == ""
    r = _request(port, "PUT", "/api/notes",
                 {"content": "## Заголовок\n\nТекст заметки.\n"})
    assert r["ok"] and r["exists"]
    r = _request(port, "GET", "/api/notes")
    assert r["ok"] and r["exists"]
    assert "## Заголовок" in r["content"]
    # файл лежит рядом с projects/.env
    assert (tmp_path / "prj" / "notes.md").is_file()


def test_notes_put_rejects_non_string(srv, tmp_path):
    _srv, port, root = srv(projects_root=tmp_path / "prj")
    (tmp_path / "prj").mkdir(parents=True, exist_ok=True)
    r = _request(port, "PUT", "/api/notes", {"content": 123})
    assert "__error__" in r and r["__error__"] == 400


def test_env_get_global(srv, tmp_path):
    """scope=global — системный корневой .env репо."""
    _srv, port, root = srv(projects_root=tmp_path / "prj",
                           repo_root=tmp_path / "repo")
    (tmp_path / "prj").mkdir(parents=True, exist_ok=True)
    (tmp_path / "repo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "repo" / ".env").write_text("GLOBAL=1\n", encoding="utf-8")
    r = _request(port, "GET", "/api/env?scope=global")
    assert r["ok"] and r["exists"] and r["keys"] == ["GLOBAL"]
    assert r["source"] == "shared"
    assert "1" not in r["masked"]


def test_settings_get_removed(srv, tmp_path):
    """GET /api/settings удалён: внешний вид — localStorage браузера,
    не .env (12-factor). Роут больше не существует."""
    _srv, port, root = srv(projects_root=tmp_path / "prj")
    r = _request(port, "GET", "/api/settings")
    assert r.get("ok") is not True  # 404/ошибка — эндпоинта нет


def test_env_put_replace(srv, tmp_path):
    srv, port, root = srv()
    pdir = _mk_project(root)
    (pdir / ".env").write_text(
        "# comment\nAPI_KEY=old\nHOST=keep\n", encoding="utf-8")
    r = _request(port, "PUT", "/api/env",
                 {"project": "ACTIVE/demo", "scope": "project",
                  "changes": {"API_KEY": "new"}})
    assert r["ok"] and "API_KEY" in r["keys"]
    text = (pdir / ".env").read_text(encoding="utf-8")
    assert "API_KEY=new" in text
    assert "HOST=keep" in text
    assert "# comment" in text


def test_env_put_delete_key(srv, tmp_path):
    srv, port, root = srv()
    pdir = _mk_project(root)
    (pdir / ".env").write_text("A=1\nB=2\n", encoding="utf-8")
    r = _request(port, "PUT", "/api/env",
                 {"project": "ACTIVE/demo", "changes": {"A": ""}})
    assert r["ok"] and r["keys"] == ["B"]
    text = (pdir / ".env").read_text(encoding="utf-8")
    assert "A=" not in text and "B=2" in text


def test_env_put_add_key(srv, tmp_path):
    srv, port, root = srv()
    pdir = _mk_project(root)
    (pdir / ".env").write_text("A=1\n", encoding="utf-8")
    r = _request(port, "PUT", "/api/env",
                 {"project": "ACTIVE/demo", "changes": {"NEW": "v"}})
    assert r["ok"]
    text = (pdir / ".env").read_text(encoding="utf-8")
    assert "NEW=v" in text


def test_env_put_seed_from_system(srv, tmp_path):
    """M9: changes-PUT без собственного .env проекта — сид из системного
    корневого .env (без секретов), затем точечные ключи; голый .env не
    затеняет системный конфиг."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text(
        "HOST=http://x\nAPI_KEY=secret\nMODEL=m1\n", encoding="utf-8")
    srv, port, root = srv(repo_root=repo)
    pdir = _mk_project(root)
    res = _request(port, "PUT", "/api/env",
                   {"project": "ACTIVE/demo", "scope": "project",
                    "changes": {"COMPILE_EPUB_COVER": "source/cover2.png"}})
    assert res["ok"]
    text = (pdir / ".env").read_text(encoding="utf-8")
    assert "COMPILE_EPUB_COVER=source/cover2.png" in text
    # сид-ключи системного .env перенесены, секрет — пуст (M1-стиль)
    assert "HOST=http://x" in text
    assert "MODEL=m1" in text
    assert "API_KEY=secret" not in text


def test_env_get_values_non_secret(srv, tmp_path):
    """M9: GET /api/env отдаёт values несекретных ключей (для селектов
    в «Настройках»); секреты — только маской."""
    srv, port, root = srv()
    pdir = _mk_project(root)
    (pdir / ".env").write_text(
        "COMPILE_EPUB_COVER=source/cover2.png\nAPI_KEY=secret\n",
        encoding="utf-8")
    r = _request(port, "GET", f"/api/env?{_q('ACTIVE/demo')}")
    assert r["values"]["COMPILE_EPUB_COVER"] == "source/cover2.png"
    assert "API_KEY" not in r["values"]


def test_env_put_bad_key(srv, tmp_path):
    srv, port, root = srv()
    _mk_project(root)
    r = _request(port, "PUT", "/api/env",
                 {"changes": {"A=B": "x"}})
    assert "__error__" in r and r["__error__"] == 400


def test_env_put_bad_key_chars(srv, tmp_path):
    """M4 (AUDIT): ключи — строго [A-Za-z0-9_]: пробел/точка/кириллица → 400."""
    srv, port, root = srv()
    _mk_project(root)
    for bad in ("A B", "A.KEY", "АБВ", "A\nB"):
        r = _request(port, "PUT", "/api/env", {"changes": {bad: "x"}})
        assert "__error__" in r and r["__error__"] == 400, bad


def test_env_put_value_newline_rejected(srv, tmp_path):
    """M4 (AUDIT): перевод строки в значении — инъекция ключей → 400."""
    srv, port, root = srv()
    pdir = _mk_project(root)
    r = _request(port, "PUT", "/api/env",
                 {"project": "ACTIVE/demo",
                  "changes": {"A": "x\nLOCAL_API_KEY=evil"}})
    assert "__error__" in r and r["__error__"] == 400
    # файл не тронут
    assert not (pdir / ".env").exists() or "evil" not in \
        (pdir / ".env").read_text(encoding="utf-8")


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

def test_env_project_no_fallback_to_shared(srv, tmp_path):
    """scope=project без pdir/.env НЕ отдаёт общий файл —
    exists=False (общий редактируется только с scope=global)."""
    _srv, port, root = srv(projects_root=tmp_path / "prj")
    _mk_project(root)
    (tmp_path / "prj" / ".env").write_text("HOST=http://shared\n", encoding="utf-8")
    r = _request(port, "GET", f"/api/env?{_q('ACTIVE/demo')}&scope=project")
    assert r["ok"] and r["exists"] is False
    assert r["source"] == "project"
    assert "http://shared" not in (r.get("content") or "")


def test_env_global_is_repo_root_env(srv, tmp_path):
    """scope=global — корневой .env репо, projects/.env не читается."""
    _srv, port, root = srv(projects_root=tmp_path / "prj",
                           repo_root=tmp_path / "repo")
    _mk_project(root)
    (tmp_path / "repo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "repo" / ".env").write_text("ROOT=1\n", encoding="utf-8")
    (tmp_path / "prj").mkdir(parents=True, exist_ok=True)
    (tmp_path / "prj" / ".env").write_text("K=v\n", encoding="utf-8")
    r = _request(port, "GET", "/api/env?scope=global")
    assert r["ok"] and r["exists"] and r["source"] == "shared"
    assert r["keys"] == ["ROOT"]
    assert "K" not in r["keys"]


def test_env_project_own_file_wins(srv, tmp_path):
    """W6: pdir/.env приоритетнее общего."""
    _srv, port, root = srv(projects_root=tmp_path / "prj")
    pdir = _mk_project(root)
    (pdir / ".env").write_text("OWN=1\n", encoding="utf-8")
    (tmp_path / "prj" / ".env").write_text("SHARED=1\n", encoding="utf-8")
    r = _request(port, "GET", f"/api/env?{_q('ACTIVE/demo')}&scope=project")
    assert r["source"] == "project"
    assert "OWN=1" in r["content"]


def test_env_put_content_mode_creates(srv, tmp_path):
    """PUT с content — полная замена/создание с нуля."""
    _srv, port, root = srv(projects_root=tmp_path / "prj")
    pdir = _mk_project(root)
    r = _request(port, "PUT", "/api/env",
                 body={"project": "ACTIVE/demo", "scope": "project",
                       "content": "A=1\nB=2\n"})
    assert r["ok"] and r["keys"] == ["A", "B"]
    assert (pdir / ".env").read_text(encoding="utf-8") == "A=1\nB=2\n"
    # повторный PUT целиком перезаписывает
    r2 = _request(port, "PUT", "/api/env",
                  body={"project": "ACTIVE/demo", "scope": "project",
                        "content": "C=3\n"})
    assert r2["ok"] and r2["keys"] == ["C"]
    assert (pdir / ".env").read_text(encoding="utf-8") == "C=3\n"


def test_env_put_global_content(srv, tmp_path):
    """Корневой .env пишется через content (Настройки)."""
    _srv, port, root = srv(projects_root=tmp_path / "prj",
                           repo_root=tmp_path / "repo")
    (tmp_path / "prj").mkdir(parents=True, exist_ok=True)
    (tmp_path / "repo").mkdir(parents=True, exist_ok=True)
    r = _request(port, "PUT", "/api/env",
                 body={"scope": "global", "content": "HOST=x\n"})
    assert r["ok"] and r["keys"] == ["HOST"]
    assert (tmp_path / "repo" / ".env").read_text(encoding="utf-8") == "HOST=x\n"


def test_env_delete_project_only(srv, tmp_path):
    """DELETE /api/env?scope=project удаляет только pdir/.env."""
    _srv, port, root = srv(projects_root=tmp_path / "prj",
                           repo_root=tmp_path / "repo")
    pdir = _mk_project(root)
    (pdir / ".env").write_text("OWN=1\n", encoding="utf-8")
    (tmp_path / "repo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "repo" / ".env").write_text("SHARED=1\n", encoding="utf-8")
    r = _request(port, "DELETE", f"/api/env?{_q('ACTIVE/demo')}&scope=project")
    assert r["ok"] and r["deleted"]
    assert not (pdir / ".env").exists()
    assert (tmp_path / "repo" / ".env").exists()  # общий не тронут


def test_env_template_endpoint_removed(srv, tmp_path):
    """GET /api/env/template удалён вместе с редактором системного .env
    (окно .env убрано из «Настроек»; шаблон — в templates/.env.example)."""
    _srv, port, root = srv(projects_root=tmp_path / "prj")
    r = _request(port, "GET", "/api/env/template")
    assert r.get("ok") is not True  # 404/ошибка — эндпоинта нет


def test_prompts_delete(srv, tmp_path):
    """DELETE /api/prompts/{name} удаляет файл промпта."""
    _srv, port, root = srv(projects_root=tmp_path / "prj")
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
    _srv, port, root = srv(projects_root=tmp_path / "prj")
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
    _srv, port, root = srv(projects_root=tmp_path / "prj")
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
    _srv, port, root = srv(projects_root=tmp_path / "prj")
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


def test_env_hidden_when_auth_enabled(tmp_path):
    """W6: при --auth значения по-прежнему скрыты (маска, без content)."""
    import threading
    auth_obj = Auth("tok", no_auth=False)
    srv = make_server("127.0.0.1", 0, auth_obj,
                      repo_root=REPO, projects_root=tmp_path / "prj")
    web_api.register(srv.router, "127.0.0.1")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        import http.client
        import urllib.parse
        port = srv.server_address[1]
        pdir = tmp_path / "prj" / "ACTIVE" / "demo"
        pdir.mkdir(parents=True)
        (pdir / ".env").write_text("SECRET=xyz\n", encoding="utf-8")
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
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", f"/api/env?{urllib.parse.urlencode({'project': 'ACTIVE/demo'})}",
                     headers={"Cookie": f"web_session={sid}",
                              "X-Requested-With": "fetch"})
        resp = conn.getresponse()
        r = json.loads(resp.read().decode())
        conn.close()
        assert r.get("ok"), f"ответ: {r}"
        assert r["visible"] is False
        assert "content" not in r
        assert "xyz" not in r["masked"]
    finally:
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
    _srv, port, root = srv(projects_root=tmp_path / "prj")
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


def test_stage_spec_env_defaults(srv, tmp_path):
    """R9-A: ?project= предзаполняет поля формы из .env проекта."""
    srv, port, root = srv()
    pdir = _mk_project(root)
    (pdir / ".env").write_text(
        "NER_CHUNK_SIZE=12345\nMODEL=gpt-test\nNER_MODEL=ner-gpt\n"
        "NER_THRESHOLD=0.9\n", encoding="utf-8")
    r = _request(port, "GET", f"/api/stages/ner/spec?{_q('ACTIVE/demo')}")
    assert "__error__" not in r
    fields = {f["name"]: f.get("default", "") for f in r["spec"]["fields"]}
    assert fields.get("chunk_size") == "12345"
    assert fields.get("threshold") == "0.9"
    # стадийная NER_MODEL приоритетнее общей MODEL
    assert fields.get("model") == "ner-gpt"
    assert fields.get("host") == ""  # HOST не задан — пусто


def test_stage_spec_env_no_project(srv, tmp_path):
    """R9-A: без project спека не трогает .env (дефолты из спекуляции)."""
    srv, port, root = srv()
    _mk_project(root)
    r = _request(port, "GET", "/api/stages/ner/spec")
    assert "__error__" not in r
    fields = {f["name"]: f.get("default", "") for f in r["spec"]["fields"]}
    assert fields.get("chunk_size") != "12345"


def test_job_start_copies_env_without_secrets(srv, tmp_path):
    """M1 (AUDIT): копия системного .env в проект — БЕЗ значений API_KEY."""
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
    assert not ("__error__" in r) and r.get("ok")
    env_file = pdir / ".env"
    assert env_file.is_file()
    text = env_file.read_text(encoding="utf-8")
    assert "HOST=http://sys" in text
    assert "СЕКРЕТ-СИСТЕМНЫЙ" not in text and "другой-секрет" not in text
    assert "API_KEY=" in text  # ключ пустой + комментарий


def test_job_start_persists_run_params(srv, tmp_path):
    """R9-B: запуск создаёт/обновляет .env проекта (копия системного),
    api_key пишется как API_KEY , пустые поля пропускаются."""
    srv, port, root = srv(repo_root=tmp_path / "repo")
    pdir = _mk_project(root)
    (tmp_path / "repo" / "cli").mkdir(parents=True, exist_ok=True)
    (tmp_path / "repo" / "cli" / "translate_check.py").write_text(
        "import sys\nsys.exit(0)\n", encoding="utf-8")
    (tmp_path / "repo" / ".env").write_text("HOST=http://sys\n",
                                             encoding="utf-8")
    from web.jobs import JobManager
    srv.job_manager = JobManager(tmp_path / "web", repo_root=REPO)
    r = _request(port, "POST", "/api/jobs",
                 {"action": "translate_check", "project": "ACTIVE/demo",
                  "params": {"preset": "polished", "exclude_words": "VIP,NPC",
                             "api_key": "secret"}})
    assert "__error__" not in r and r.get("ok")
    env_file = pdir / ".env"
    assert env_file.is_file(), "pdir/.env не создан"
    text = env_file.read_text(encoding="utf-8")
    assert "TRANSLATE_CHECK_EXCLUDE_WORDS=VIP,NPC" in text
    assert "HOST=http://sys" in text                # копия системного
    assert "API_KEY=secret" in text                 # ключ хранится в .env

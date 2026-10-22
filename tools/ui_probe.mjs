#!/usr/bin/env node
/* ui_probe.mjs — headless-обход SPA (Playwright) для проверки правок UI.
 *
 * Поднимает web-сервер на временных данных (или берёт уже запущенный --url),
 * проходит все view и модалки, собирает pageerror/console-ошибки/4xx-5xx
 * ответы и пишет скриншоты. Данные — только временные (--projects-dir).
 *
 *   ./dev.sh probe                                # venv активен, всё само
 *   node tools/ui_probe.mjs                       # свой сервер, свои данные
 *   node tools/ui_probe.mjs --url http://127.0.0.1:8877 --keep
 *   node tools/ui_probe.mjs --only project/ner project/run --shot
 *
 * Выход 0 — чистый проход; 1 — найдены ошибки; список — в stdout и --out.
 */
import { spawn } from "node:child_process";
import http from "node:http";
import net from "node:net";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ROUTES = {
  hub: "#/hub",
  dashboard: "#/dashboard",
  templates: "#/templates",
  settings: "#/settings",
  notes: "#/notes",
  help: "#/help",
  unknown: "#/nesuschestvuet",
};
const PROJECT_TABS = [
  "files",
  "run",
  "editor",
  "ner",
  "review",
  "chapters",
  "search",
  "status",
  "config",
  "prompts",
  "logs",
  "notes",
  "history",
];
const SECTION = "TMP";
const BOOK = "Probe";

/* Заглушка проверки обновлений: свои сводки вместо живого GitHub. Заметки
 * релиза GitHub пишет markdown'ом и всегда дописывает строку «Full Changelog:
 * <ссылка>» — оставляем её в mock'е как есть, рендер проверяется на настоящем
 * виде ответа. */
const MOCK_RELEASE = {
  ok: true,
  tag: "v9.9.0",
  version: "9.9.0",
  name: "NovelMaestro 9.9.0",
  url: "https://github.com/LynxPDA/NovelMaestro/releases/tag/v9.9.0",
  published: "2026-01-01T00:00:00Z",
  // строку «Full Changelog: <ссылка>» вырезает core/update.py: сюда заметки
  // приходят уже чищеными, а ссылка на сравнение едет отдельным полем
  notes: "## Что нового\n\n- **жирный** пункт описания\n- второй пункт",
  changes: "https://github.com/LynxPDA/NovelMaestro/compare/v0.4.0...v9.9.0",
};
const MOCK_KIND_LABELS = {
  docker: "Установка в Docker",
  portable: "Портативная сборка (Windows)",
  git: "Запуск из репозитория",
  tree: "Копия исходников",
};
const MOCK_MODE_LABELS = {
  command: "образ обновляет хост",
  git: "из репозитория (git)",
  zip: "файлами из архива релиза",
};
const MOCK_HOST_COMMAND = "docker compose pull && docker compose up -d";
/* тексты о перезапуске пишет сервер (core/update.py::RESTART_NOTES); здесь они
 * копией, чтобы панель показывала то, что показывает в каждой установке */
const MOCK_RESTART = {
  git: { auto: true, note: "После обновления сервер перезапустится сам, окно с консолью закрывать не нужно" },
  tree: { auto: true, note: "После обновления сервер перезапустится сам, окно с консолью закрывать не нужно" },
  portable: { auto: false, note: "Перезапустите сборку сами: закройте окно сервера и запустите start.bat заново. Изнутри это невозможно, а до перезапуска работает старый код" },
  docker: { auto: false, note: "Образ обновляет хост, контейнер сам себя не заменит: интерфейс эту команду не запускает" },
};

/* сводка обновления, как её отдаёт web/api_common.py::_update_state */
function mockUpdate(kind = "git", mode = null, job = null, extra = {}) {
  const modeOf = mode || (kind === "docker" ? "command" : kind === "git" ? "git" : "zip");
  return Object.assign({
    kind,
    label: MOCK_KIND_LABELS[kind],
    mode: modeOf,
    mode_label: MOCK_MODE_LABELS[modeOf],
    command: modeOf === "command" ? MOCK_HOST_COMMAND : "",
    current: "0.4.0",
    release: MOCK_RELEASE,
    checked: 1,
    available: true,
    restart: MOCK_RESTART[kind],
    job: job || {
      state: "idle", kind, mode: modeOf, from: "0.4.0", to: "", note: "",
      error: "", log: [], started: 0, finished: 0,
    },
  }, extra);
}
// раздел на кириллице: браузер хранит location.hash закодированным
const CYR_SECTION = "Черновики";
const CYR_BOOK = "LatinBook";

function arg(name, def = null) {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith("--")
    ? process.argv[i + 1]
    : def;
}
const has = (name) => process.argv.includes(`--${name}`);

const PORT = Number(arg("port", 0)); // 0 — свободный порт сам (чужие сервера мешаются)
const OUT = path.resolve(ROOT, arg("out", "logs/ui_probe"));
const ONLY = process.argv
  .slice(2)
  .filter((a) => ROUTES[a] || PROJECT_TABS.includes(a));
const SHOT = has("shot");

function log(...a) {
  console.log(...a);
}

/* ── временные данные проекта (книга с главами и глоссарием) ───────── */
function seedProjectsDir(dir) {
  const book = path.join(dir, SECTION, BOOK);
  for (const ch of ["00000_1_Глава 1", "00000_2_Глава 2"]) {
    const p = path.join(book, "chapters", ch);
    fs.mkdirSync(p, { recursive: true });
    for (const [f, body] of [
      ["chapter.txt", "Первая глава.\n\nОн сказал: «Привет, мир».\n"],
      ["translated.txt", "Первая глава (перевод).\n"],
      ["redacted.txt", "Первая глава (редактура).\n"],
      // полировка — с отступами и лишними переносами: на ней видно,
      // как предпросмотр массовых замен показывает пробельные правила
      ["polished.txt", "   Первая   глава.\n\n\n   Вторая строка.\n"],
    ]) {
      fs.writeFileSync(path.join(p, f), body, "utf-8");
    }
  }
  fs.writeFileSync(
    path.join(book, "ner.json"),
    JSON.stringify(
      [
        { term: "мир", translation: "мир", type: "other (female)", count: 3 },
        { term: "глава", translation: "глава", type: "other", count: 1 },
      ],
      null,
      2,
    ),
    "utf-8",
  );
  // review-файл глоссария: оба действия правки — патч поля и удаление термина
  fs.mkdirSync(path.join(book, "tmp"), { recursive: true });
  fs.writeFileSync(
    path.join(book, "tmp", "ner_review.json"),
    JSON.stringify(
      {
        created: "2025-01-01 00:00",
        updated: "2025-01-02 00:00",
        input: "ner.json",
        entries: [
          { stage: "Весь глоссарий", action: "патч", term: "мир",
            field: "translation", old: "мир", new: "свет",
            reason: "по контексту", status: "принять", applied: false },
          { stage: "Весь глоссарий", action: "патч", term: "глава",
            field: "type", old: "other", new: "other (female)",
            reason: "род", status: "отклонить", applied: false },
          { stage: "RAG", action: "удаление", term: "мир", field: "",
            old: "type=other (female); translation=мир", new: "",
            reason: "обычное слово, не термин лора", status: "принять",
            applied: false },
        ],
      },
      null, 2,
    ) + "\n",
    "utf-8",
  );
  // отчёт translate_check: на нём проверяется шкала допуска пары —
  // полоса при текущем K, штрих медианы и точки глав вне допуска
  fs.mkdirSync(path.join(book, "logs"), { recursive: true });
  fs.writeFileSync(
    path.join(book, "logs", "check_polished_1-2.txt"),
    [
      "=== Отчёт о проверке перевода (polished) ===",
      "Запуск        : /app/cli/translate_check.py --check-type polished --sensitivity 25",
      "Диапазон глав : 1 – 2",
      "Счёт объёма   : токены (оценка); чувствительность K=25",
      "Дата          : Thu Oct 22 10:32:18 2026",
      "Всего папок   : 2",
      "---------------------------------",
      "Статистика (по всей книге, 2 папок без дублей):",
      "  polished vs redacted: глав 1320, медиана 0.999, MAD 0.4%, допуск ±9.9% → отношение 0.90–1.10 (K=25)",
      "  polished vs chapter: глав 2, медиана 1.356, MAD 4.7%, допуск ±87.3% → отношение 0.48–2.23 (K=25)",
      "---------------------------------",
      "1. Папка: ./chapters/00000_1_Глава 1",
      "  [polished vs redacted]  отношение объёма 0.90 → ОШИБКА (вне 0.90–1.10 при K=25, медиана книги 1.00; polished 1469 т., redacted 1637 т.; пройдёт при K ≥ 26)",
      "  [polished vs chapter]  отношение объёма 1.47 → ОШИБКА (вне 0.48–2.23 при K=25, медиана книги 1.36; polished 1469 т., chapter 1000 т.; пройдёт при K ≥ 32)",
      "",
      "--- Сводка ---",
      "Проверено глав : 2",
      "С ошибками     : 1",
      "Пропущено      : 0",
      "",
    ].join("\n"),
    "utf-8",
  );
  // файл в корне книги: файловый менеджер есть что показать (quick-look)
  fs.writeFileSync(path.join(book, "notes.md"), "# Заметки\n\n- проба\n", "utf-8");
  // логи книги: вкладка «Логи» показывает и папки, и файлы — и то, и другое
  // рисуется одним пейджером
  fs.mkdirSync(path.join(book, "logs", "chapters"), { recursive: true });
  fs.writeFileSync(path.join(book, "logs", "ner.log"), "2025-01-01 00:00 ner: старт\n", "utf-8");
  fs.writeFileSync(path.join(book, "logs", "chapters", "00000_1_Глава 1.log"), "глава: ок\n", "utf-8");
  fs.mkdirSync(path.join(book, "prompts"), { recursive: true });
  fs.writeFileSync(path.join(book, "prompts", "translate.txt"), "<translate>переведи</translate>\n", "utf-8");
  // промпт глоссария назло неполный: только первый проход — проверка
  // --check-prompt обязана показать отсутствующий тег второго режима
  fs.writeFileSync(
    path.join(book, "prompts", "ner_prompt.txt"),
    "<prompt_pass1>\n{chunk_text}\n</prompt_pass1>\n",
    "utf-8",
  );
  fs.writeFileSync(path.join(book, "metadata.yaml"), "title: Проба\nauthor: Probe\n");
  // исходники книги: на них видно автоподхват «Исходника» (первый epub) и
  // фильтр его списка режимом разбивки (toc принимает только epub)
  fs.mkdirSync(path.join(book, "source"), { recursive: true });
  fs.writeFileSync(path.join(book, "source", "book.epub"), "");
  fs.writeFileSync(path.join(book, "source", "notes.txt"), "Глава 1\nтекст\n");
  fs.writeFileSync(
    path.join(dir, "hub_state.json"),
    JSON.stringify({ sections: [SECTION, CYR_SECTION], collapsed: [] }, null, 2),
  );
  // отдельный раздел на кириллице: имена книг санитизируются в латиницу, а
  // разделы — нет, и именно он попадает в hash закодированным
  const cyr = path.join(dir, CYR_SECTION, CYR_BOOK);
  const cyrCh = path.join(cyr, "chapters", "00000_1_Первая");
  fs.mkdirSync(cyrCh, { recursive: true });
  fs.writeFileSync(path.join(cyrCh, "chapter.txt"), "Глава первая.\n", "utf-8");
  fs.writeFileSync(path.join(cyr, "ner.json"), "[]\n", "utf-8");
  // общий .env пробы (WEB_ENV_FILE указывает сюда же): ОДИН файл на все
  // книги — дефолты LLM, стадий и рассуждений
  fs.writeFileSync(
    path.join(dir, ".env"),
    [
      "# общий .env пробы (временные данные probe)",
      "HOST=http://127.0.0.1:9/v1",
      "API_KEY=probe-secret",
      "MODEL=probe-model",
      "NER_CHUNK_SIZE=8000",
      "REASONING_MODE=default",
      "THINKING_PROFILE=openai",
      "REASONING_EFFORT=",
      "THINKING_BUDGET=0",
      "LLM_EXTRA_BODY_JSON=",
    ].join("\n") + "\n",
    "utf-8",
  );

}

/* Сервер поднимается тем же python3, что и всё остальное: probe гоняют из
 * активированного venv (`./dev.sh probe`), иначе PYTHON=... . */
const PYTHON = process.env.PYTHON || "python3";

/* Свободный порт: на фиксированный может висеть чужой сервис — тогда probe
 * мерил бы чужую страницу вместо SPA. */
function freePort() {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => {
      const p = s.address().port;
      s.close(() => resolve(p));
    });
  });
}

async function waitServer(url, ms = 25000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try {
      const r = await fetch(`${url}/api/state`);
      if (r.ok) return true;
    } catch {
      /* сервер ещё поднимается */
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  return false;
}

async function main() {
  let own = null;
  let url = arg("url");
  let seedDir = null;
  if (!url) {
    const dir = (seedDir = fs.mkdtempSync(path.join(os.tmpdir(), "nm-probe-")));
    seedProjectsDir(dir);
    const port = PORT || (await freePort()); // тот же порт — в argv сервера
    url = `http://127.0.0.1:${port}`;
    own = spawn(
      PYTHON,
      [
        path.join(ROOT, "web", "main.py"),
        "--port",
        String(port),
        "--projects-dir",
        dir,
      ],
      { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, WEB_ENV_FILE: path.join(dir, ".env") } },
    );
    own.stdout.on("data", (b) => process.env.PROBE_VERBOSE && process.stdout.write(`[server] ${b}`));
    own.stderr.on("data", (b) => process.env.PROBE_VERBOSE && process.stderr.write(`[server] ${b}`));
    log(`сервер: ${url} (данные: ${dir})`);
  } else {
    log(`сервер: ${url} (внешний)`);
  }
  if (!(await waitServer(url))) {
    log("❌ сервер не поднялся");
    if (own) own.kill("SIGKILL");
    process.exit(1);
  }

  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const problems = [];
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(`console: ${m.text().slice(0, 200)}`);
  });
  page.on("requestfailed", (r) =>
    problems.push(`requestfailed: ${r.url()} ${r.failure()?.errorText}`),
  );
  page.on("response", (r) => {
    if (r.status() >= 400) problems.push(`http ${r.status()}: ${r.url()}`);
  });

  const routes = { ...ROUTES };
  for (const t of PROJECT_TABS) routes[`project/${t}`] = `#/project/${SECTION}/${BOOK}/${t}`;
  const list = ONLY.length
    ? Object.entries(routes).filter(([k]) => ONLY.includes(k))
    : Object.entries(routes);

  for (const [name, hash] of list) {
    const before = problems.length;
    await page.goto(`${url}/${hash}`, { waitUntil: "load" });
    await page.waitForTimeout(600); // асинхронные view перерисовываются после fetch
    const info = await page.evaluate(() => {
      const t = document.querySelector(".page-title");
      const err = document.querySelector(".form-error");
      return {
        title: t ? t.textContent.trim() : "",
        err: err && err.textContent.trim() ? err.textContent.trim() : "",
        nodes: document.querySelectorAll("#app *").length,
        empty: !document.querySelector(".page"),
      };
    });
    const bad = problems.slice(before);
    if (info.err) bad.push(`.form-error: ${info.err}`);
    if (info.empty) bad.push("не отрисован .page");
    if (SHOT) {
      await page.screenshot({
        path: path.join(OUT, `${name.replace(/\W+/g, "_")}.png`),
        fullPage: false,
      });
    }
    log(
      `${bad.length ? "❌" : "✅"} ${name.padEnd(18)} ${String(info.nodes).padStart(5)} узлов  ${info.title}` +
        (bad.length ? `\n     ${bad.join("\n     ")}` : ""),
    );
  }

  /* переключатели панелей: клик по «Рендер»/«Редактор» должен менять, что
   * видно — хост редактора или sandbox-iframe (режим может стартовать с любой
   * стороны: заметки открываются сразу в предпросмотре) */
  const TOGGLE = 'button.btn-ghost[title="Показать редактор"],' +
    ' button.btn-ghost[title="Показать отрендеренный вид"]';
  for (const [name, hash, sel] of [
    ["notes-preview", `#/project/${SECTION}/${BOOK}/notes`, TOGGLE],

  ]) {
    const before = problems.length;
    await page.goto(`${url}/${hash}`, { waitUntil: "load" });
    await page.waitForTimeout(1500); // view асинхронная: панель появляется после fetch
    if (!(await page.locator(sel).count())) {
      log(`⚠️  панель ${name}: кнопка не найдена`);
      continue;
    }
    const look = () =>
      page.evaluate(() => ({
        editor: [...document.querySelectorAll(".editor-cm")].some((x) => x.offsetParent),
        frame: [...document.querySelectorAll(".editor-preview-frame")].some((f) => f.offsetParent),
        srcdoc: !![...document.querySelectorAll(".editor-preview-frame")].find((f) => f.srcdoc),
      }));
    const a = await look();
    await page.locator(sel).first().click();
    await page.waitForTimeout(800);
    const b2 = await look();
    if (a.editor === b2.editor || a.frame === b2.frame)
      problems.push(`${name}: клик не сменил режим (${JSON.stringify(a)} → ${JSON.stringify(b2)})`);
    if (SHOT) await page.screenshot({ path: path.join(OUT, `panel-${name}.png`) });
    log(
      `${problems.length === before ? "✅" : "❌"} панель ${name.padEnd(14)} ${a.editor ? "редактор" : "кадр"} → ${b2.editor ? "редактор" : "кадр"}${b2.srcdoc ? " (srcdoc есть)" : ""}`,
    );
  }

  /* редактор файла открывается по двойному клику по имени: тот же компонент
   * «редактор + предпросмотр», только путь из файлового браузера */
  {
    const before = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/files`, { waitUntil: "load" });
    await page.waitForTimeout(1200);
    // файлы — span.fname (директории — a.fname, у них свой клик)
    const row = page.locator(".files-list span.fname").first();
    if (!(await row.count())) {
      log("⚠️  панель file-preview: нет файлов для открытия");
    } else {
      await row.dblclick();
      await page.waitForTimeout(1500);
      const sel = 'button[title="Показать отрендеренный вид"]';
      if (!(await page.locator(sel).count())) {
        problems.push("file-preview: редактор файла не открылся");
        log("❌ панель file-preview: редактор файла не открылся");
      } else {
        const look = () =>
          page.evaluate(() => ({
            editor: [...document.querySelectorAll(".editor-cm")].some((x) => x.offsetParent),
            frame: [...document.querySelectorAll(".editor-preview-frame")].some((f) => f.offsetParent),
          }));
        const a = await look();
        await page.locator(sel).first().click();
        await page.waitForTimeout(700);
        const b2 = await look();
        if (a.editor === b2.editor || a.frame === b2.frame)
          problems.push(`file-preview: режим не сменился (${JSON.stringify(a)} → ${JSON.stringify(b2)})`);
        if (SHOT) await page.screenshot({ path: path.join(OUT, "panel-file-preview.png") });
        log(`${problems.length === before ? "✅" : "❌"} панель file-preview   ${a.editor ? "редактор" : "кадр"} → ${b2.editor ? "редактор" : "кадр"}`);
      }
    }
  }

  /* быстрый просмотр: фокус на строке списка → Space → ровно один оверлей с
   * sandbox-кадром; Escape его закрывает (иначе «залипший» оверлей на весь экран) */
  {
    const before2 = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/files`, { waitUntil: "load" });
    // сменить вкладку можно и кликом, но надёжнее полная перезагрузка: иначе
    // остаётся вид, открытый предыдущим сценарием
    await page.reload({ waitUntil: "load" });
    await page.waitForTimeout(900);
    // каталоги Space не просматривается — берём первую строку файла
    const row = page.locator('.files-list .frow:not([data-dir="1"])').first();
    if (!(await row.count())) {
      log("⚠️  quick-look: в списке нет файлов");
    } else {
      await row.focus();
      await page.keyboard.press(" ");
      await page.waitForTimeout(900);
      // оверлей position:fixed — offsetParent у него всегда null, считаем напрямую
      const look = await page.evaluate(() => {
        const open = [...document.querySelectorAll(".modal-backdrop")];
        const f = open.length === 1 ? open[0].querySelector("iframe.quick-frame") : null;
        return {
          n: open.length,
          frame: !!f,
          text: f ? (f.getAttribute("srcdoc") || "").trim().slice(0, 24) : "",
        };
      });
      if (look.n !== 1 || !look.frame)
        problems.push(`quick-look: оверлеев ${look.n}, кадр ${look.frame}`);
      await page.keyboard.press("Escape");
      await page.waitForTimeout(500);
      const left = await page.evaluate(
        () => document.querySelectorAll(".modal-backdrop").length,
      );
      if (left !== 0) problems.push(`quick-look: Escape оставил ${left} оверлей(а)`);
      if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-quick-look.png") });
      log(`${problems.length === before2 ? "✅" : "❌"} quick-look Space   кадр: ${look.text ? "есть" : "пусто"}`);
    }
  }

  /* «Поиск»: группы и кластеры приходят с сервера (реестр в браузере не
   * дублируется), один GET на запрос, все результаты постранично. Никаких
   * пояснений и отдельного режима глоссария на вкладке нет: ner.json — такой
   * же файл обхода, но клик по нему открывает вкладку «Глоссарий», а не
   * редактор */
  {
    const beforeS = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/search`,
      { waitUntil: "load" });
    await page.waitForSelector(".search-scope-chip", { timeout: 15000 });
    const chips = await page.evaluate(() =>
      [...document.querySelectorAll(".search-scope-chip")].map((l) => {
        const b = l.querySelector("input");
        return [l.textContent.trim(), !!(b && b.checked)];
      }));
    const clusters = await page.evaluate(() =>
      [...document.querySelectorAll(".search-cluster-label")]
        .map((x) => x.textContent.trim()));
    const hints = await page.evaluate(() =>
      [...document.querySelectorAll(".card-hint")]
        .map((x) => x.textContent.trim()));
    if (hints.length)
      problems.push(`поиск: на вкладке осталось пояснение «${hints[0]}»`);
    if (chips.length !== 9) problems.push(`поиск: групп ${chips.length}`);
    // подписи файлов глав — те же слаги, что у стадий; глоссарий — ner.json
    if (chips.slice(0, 4).map(([t]) => t).join(",")
        !== "chapter,translated,redacted,polished")
      problems.push(`поиск: группы глав «${chips.map(([t]) => t).join(",")}»`);
    if (clusters.join(",") !== "Файлы глав:,Прочее:")
      problems.push(`поиск: кластеры «${clusters.join(",")}»`);
    const on = chips.filter(([, c]) => c).map(([t]) => t);
    if (on.join(",") !== "chapter,polished,ner.json,Заметки книги")
      problems.push(`поиск: отмечены «${on.join(", ")}»`);
    await page.fill(".search-q", "глава");
    await page.click(".search-run");
    await page.waitForTimeout(900);
    const res = await page.evaluate(() => ({
      status: ((document.querySelector(".review-status") || {})
        .textContent || "").trim(),
      files: [...document.querySelectorAll(".search-file-path")]
        .map((b) => b.textContent.trim()),
      titles: [...document.querySelectorAll(".search-file-path")]
        .map((b) => b.getAttribute("title") || ""),
      pagerBtns: document.querySelectorAll(".ner-pager button").length,
      marks: [...document.querySelectorAll(".search-hit-text mark")]
        .map((m) => m.textContent),
      lines: [...document.querySelectorAll(".search-hit-line")]
        .map((x) => x.textContent.trim()),
    }));
    if (!/^Совпадений: (\d+) · файлов: (\d+) · прочитано: (\d+)$/.test(res.status))
      problems.push(`поиск: статус «${res.status}»`);
    if (res.files[0] !== "chapters/00000_1_Глава 1/chapter.txt")
      problems.push(`поиск: первый файл «${res.files[0]}»`);
    if (!res.marks.length || res.marks.some((t) => t !== "глава"))
      problems.push(`поиск: подсветка ${JSON.stringify(res.marks)}`);
    if (res.lines[0] !== "1") problems.push(`поиск: строка ${res.lines[0]}`);
    // результатов меньше страницы — у панели пейджера нет даже кнопок
    if (res.pagerBtns) problems.push(`поиск: pager молчит, кнопок ${res.pagerBtns}`);
    /* «Только главы» — все артефакты стадий (4 типа × 2 главы); глоссарий и
     * заметки уходят из охвата, но чипсы остаются: это панель выбора */
    await page.click('.search-chips button:has-text("Только главы")');
    await page.click(".search-run");
    await page.waitForTimeout(900);
    const onlyChapters = await page.evaluate(() => ({
      files: document.querySelectorAll(".search-file-path").length,
      chips: [...document.querySelectorAll(".search-scope-chip")]
        .filter((l) => l.querySelector("input").checked).length,
      clusters: document.querySelectorAll(".search-cluster-label").length,
    }));
    if (onlyChapters.files !== 8 || onlyChapters.chips !== 4)
      problems.push(`поиск: «Только главы» — ${onlyChapters.files} файлов, `
        + `${onlyChapters.chips} чипсов`);
    if (onlyChapters.clusters !== 2)
      problems.push(`поиск: с «Только главами» кластеров ${onlyChapters.clusters}`);
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "scenario-search.png") });
    /* клик по имени файла — тот же однофайловый редактор, что в «Проверках»:
     * он открывается поверх «Поиска», панель поиска сразу заполнена, «Назад»
     * ведёт обратно к результатам, а не в вкладку «Редактор» */
    await page.click(".search-file-path");
    await page.waitForTimeout(1200);
    const opened = await page.evaluate(() => ({
      tab: [...document.querySelectorAll(".tab-active")]
        .map((x) => x.textContent.trim()).join(""),
      meta: (document.querySelector(".editor-meta") || {}).textContent || "",
      text: [...document.querySelectorAll(".editor-cm")]
        .map((x) => x.textContent).join(" "),
      panes: document.querySelectorAll(".ed-type").length,
      back: [...document.querySelectorAll(".editor-wrap .files-toolbar button")]
        .map((b) => b.textContent.trim()),
      findOpen: !!document.querySelector(
        '.cm-panel.cm-search [main-field="true"]'),
      findValue: (document.querySelector(
        '.cm-panel.cm-search [main-field="true"]') || {}).value || "",
    }));
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "scenario-search-editor.png") });
    if (opened.tab !== "Поиск" || !opened.meta.trim())
      problems.push(`поиск: клик по имени не открыл редактор `
        + `(вкладка «${opened.tab}», файл «${opened.meta}»)`);
    if (opened.meta.split(" · ")[0]
        !== "chapters/00000_1_Глава 1/chapter.txt")
      problems.push(`поиск: редактор открыт на «${opened.meta}»`);
    else if (opened.panes)
      problems.push(`поиск: открылись панели вкладки «Редактор» (${opened.panes})`);
    else if (!/глава/i.test(opened.text))
      problems.push("поиск: в редакторе не тот файл");
    // запрос доезжает до панели поиска редактора: она открыта и заполнена
    else if (!opened.findOpen || !/глава/.test(opened.findValue || ""))
      problems.push(`поиск: панель поиска редактора `
        + `${opened.findOpen ? "пуста" : "не открыта"} (${opened.findValue})`);
    else if (opened.back[0] !== "← Назад")
      problems.push(`поиск: в тулбаре редактора кнопка «${opened.back[0]}»`);
    else {
      await page.click(".editor-wrap .files-toolbar button");
      await page.waitForTimeout(900);
      const back = await page.evaluate(() => ({
        tab: (document.querySelector(".tab-active") || {}).textContent || "",
        files: document.querySelectorAll(".search-file-path").length,
        q: (document.querySelector(".search-q") || {}).value || "",
      }));
      if (back.tab.trim() !== "Поиск" || !back.files
          || !/глава/.test(back.q))
        problems.push(`поиск: «← Назад» не вернул к результатам `
          + `(вкладка «${back.tab}», файлов ${back.files}, запрос «${back.q}»)`);
    }
    /* тот же запрос по глоссарию: оставляем в охвате только ner.json — и
     * смотрим, что клик ведёт на вкладку «Глоссарий» со всеми столбцами.
     * Хэш после клика остаётся «.../search» (вкладку сменил state, не роут) */
    await page.evaluate(() => { location.hash = "#/hub"; });
    await page.waitForTimeout(400);
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/search`,
      { waitUntil: "load" });
    await page.waitForSelector(".search-scope-chip", { timeout: 15000 });
    /* охват помнится на проект, поэтому снимаем главы не сразу: пока не
     * включён глоссарий, снятие последнего чипса guard оставляет последним */
    await page.click('.search-scope-chip:has-text("ner.json")');
    for (const g of ["chapter", "translated", "redacted", "polished"])
      await page.click(`.search-scope-chip:has-text("${g}")`);
    await page.fill(".search-q", "глава");
    await page.click(".search-run");
    await page.waitForTimeout(900);
    const nerRow = await page.evaluate(() => {
      const b = document.querySelector(".search-file-path");
      return b ? { name: b.textContent.trim(), title: b.getAttribute("title") || "" }
               : null;
    });
    if (!nerRow || nerRow.name !== "ner.json")
      problems.push(`поиск: в охвате только глоссарий — строка ${JSON.stringify(nerRow)}`);
    else if (!/Глоссарий/.test(nerRow.title))
      problems.push(`поиск: подсказка строки «${nerRow.title}»`);
    else {
      await page.click(".search-file-path");
      await page.waitForTimeout(1200);
    }
    const toNer = await page.evaluate(() => ({
      tab: [...document.querySelectorAll(".tab-active")]
        .map((x) => x.textContent.trim()).join(""),
      q: (document.querySelector(".ner-q") || {}).value || "",
      cols: [...document.querySelectorAll(".ner-th")]
        .map((x) => x.textContent.trim().replace(/\s*[\u2191\u2193]$/, "")),
    }));
    if (toNer.tab !== "Глоссарий" || !/глава/.test(toNer.q))
      problems.push(`поиск: в глоссарий не перешло (вкладка «${toNer.tab}», `
        + `поле «${toNer.q}»`);
    else if (toNer.cols.join(",") !== "term,type,translation,count")
      problems.push(`поиск: в глоссарии столбцы «${toNer.cols.join(",")}»`);
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "scenario-search-ner.png") });
    log(`${problems.length === beforeS ? "✅" : "❌"} поиск  ${res.status}` +
      ` · «Только главы» ${onlyChapters.files} файлов` +
      ` / ${onlyChapters.chips} чипсов` +
      (problems.length > beforeS
        ? `\n     ${problems.slice(beforeS).join("\n     ")}` : ""));
  }

  /* «Глоссарий»: все настройки вкладки — за одной кнопкой «⋮»; строки — с
   * чекбоксами, групповые замок и удаление — в панели выделения, она заменяет
   * тулбар; замок — служебное поле «_locked», отдельным столбцом не показан */
  {
    const before = problems.length;
    await page.evaluate(() => { location.hash = "#/hub"; });
    await page.waitForTimeout(400);
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/ner`,
      { waitUntil: "load" });
    await page.waitForSelector(".ner-table tbody .ner-row", { timeout: 15000 });
    const read = () => page.evaluate(() => {
      const cls = (s) => ((document.querySelector(s) || {}).className) || "";
      const hidden = (s) => cls(s).split(/\s+/).includes("hidden");
      return {
        tools: !hidden(".project-body .files-tools"),
        sel: !hidden(".project-body .files-sel"),
        selText: ((document.querySelector(".files-sel-count") || {})
          .textContent || "").trim(),
        rows: document.querySelectorAll(".ner-table tbody .ner-row").length,
        boxes: document.querySelectorAll(".ner-td-sel .fsel").length,
        locked: document.querySelectorAll(".ner-row-locked").length,
        cols: [...document.querySelectorAll(".ner-table thead th")]
          .map((x) => x.textContent.trim()),
        acts: Math.min(...[...document.querySelectorAll(".ner-actions")]
          .map((x) => x.querySelectorAll("button").length)),
      };
    });
    const a = await read();
    if (a.rows !== 2 || a.boxes !== 2)
      problems.push(`глоссарий: строк ${a.rows}, чекбоксов ${a.boxes}`);
    if (!a.tools || a.sel)
      problems.push("глоссарий: без выделения виден тулбар, не панель");
    if (a.cols.includes("_locked"))
      problems.push("глоссарий: служебный ключ уехал в столбцы");
    if (a.acts !== 3)
      problems.push(`глоссарий: кнопок в строке ${a.acts} (правка, удаление, замок)`);
    // панель «⋮»: 8 контролов и 2 разделителя — больше на вкладке ничего нет
    await page.locator(".ner-menu-btn").first().click();
    await page.waitForTimeout(300);
    const menu = await page.evaluate(() => {
      const box = document.querySelector(".project-body .toolbar-menu .menu-box");
      if (!box) return { open: false, items: [], seps: 0 };
      return {
        open: !box.className.split(/\s+/).includes("hidden"),
        items: [...box.querySelectorAll("button, label.chk")]
          .map((x) => x.textContent.trim()),
        seps: box.querySelectorAll(".menu-sep").length,
      };
    });
    if (!menu.open) problems.push("глоссарий: меню «⋮» не открылось");
    const want = ["только зафиксированные", "Столбцы", "Типы",
      "Поля поиска", "Добавить столбец", "Добавить термин", "Удалить столбец",
      "Удалить по фильтру"];
    if (menu.items.length !== 8)
      problems.push(`глоссарий: пунктов в меню ${menu.items.length} — `
        + JSON.stringify(menu.items));
    for (const w of want)
      if (!menu.items.some((t) => t.includes(w)))
        problems.push(`глоссарий: в меню нет пункта «${w}»`);
    if (menu.seps !== 2)
      problems.push(`глоссарий: разделителей в меню ${menu.seps}`);
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "scenario-ner-menu.png") });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(200);
    /* выделение всех строк → панель выделения → групповой замок */
    await page.locator(".ner-th-sel .fsel").click();
    await page.waitForTimeout(500);
    const sel = await read();
    if (!sel.sel || sel.tools)
      problems.push("глоссарий: панель выделения не заменила тулбар");
    if (sel.selText !== "выделено: 2")
      problems.push(`глоссарий: панель выделения «${sel.selText}»`);
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "scenario-ner-select.png") });
    await page.locator('.files-sel button[aria-label^="Зафиксировать"]')
      .first().click();
    await page.waitForTimeout(700);
    const locked = await read();
    if (locked.locked !== 2)
      problems.push(`глоссарий: зафиксировано строк ${locked.locked}`);
    if (locked.acts !== 1)
      problems.push(`глоссарий: у зафиксированной строки кнопок ${locked.acts}`);
    // выделение после группового замка остаётся: им же и снимают замок
    if (!locked.sel || locked.tools)
      problems.push("глоссарий: после замка панель выделения пропала");
    const onDisk = (() => {
      try {
        return fs.readFileSync(
          path.join(seedDir || "", SECTION, BOOK, "ner.json"), "utf-8");
      } catch {
        return "";
      }
    })();
    if (!/"_locked": true/.test(onDisk))
      problems.push("глоссарий: замок не доехал в ner.json");
    // снять замок той же панелью: служебный ключ исчезает из файла совсем
    // (после замка выделение живёт — сначала снять его, потом выделить снова)
    await page.locator('.files-sel button[aria-label="Снять выделение"]')
      .first().click();
    await page.waitForTimeout(400);
    await page.locator(".ner-th-sel .fsel").click();
    await page.waitForTimeout(500);
    await page.locator('.files-sel button[aria-label^="Снять замок"]')
      .first().click();
    await page.waitForTimeout(700);
    const after = await read();
    const offDisk = (() => {
      try {
        return fs.readFileSync(
          path.join(seedDir || "", SECTION, BOOK, "ner.json"), "utf-8");
      } catch {
        return "";
      }
    })();
    if (after.locked !== 0)
      problems.push(`глоссарий: замок остался на ${after.locked} строке(ах)`);
    if (/_locked/.test(offDisk))
      problems.push("глоссарий: снятый замок оставил ключ в ner.json");
    log(`${problems.length === before ? "✅" : "❌"} глоссарий: меню+выделение `
      + ` ${menu.items.length} пунктов · ${sel.selText} · замок ${locked.locked}`
      + ` → снят ${after.locked}`
      + (problems.length > before
        ? `\n     ${problems.slice(before).join("\n     ")}` : ""));
  }

  /* «Промпты»: язык редактора выбирает человек. По умолчанию промптам plain
   * text: отдельного «языка промптов» больше нет (у этих файлов расширение
   * .txt). Если же в «Внешнем виде» выбрать XML — теги секций снова видно по
   * подсветке, так что список языков обязан работать end-to-end. */
  {
    const before = problems.length;
    /* классы подсветки CM даёт автоименами («ͼr», «ͼs»…): сверяем не имя, а
       самого факта — закрашены диапазоны или нет */
    const marks = () => page.evaluate(() => {
      const host = document.querySelector(".editor-cm");
      const spans = host
        ? [...host.querySelectorAll(".cm-line span[class]")] : [];
      return {
        text: host ? host.textContent.trim().slice(0, 40) : "",
        spans: spans.length,
        hl: spans.filter((x) => x.className.includes("\u037c")).length,
      };
    });
    const setLang = (v) => page.evaluate((val) => {
      let d = {};
      try { d = JSON.parse(localStorage.getItem("uiLookV1") || "{}"); }
      catch { /* битый JSON — начинаем с пустого */ }
      d.langPrompt = val;
      localStorage.setItem("uiLookV1", JSON.stringify(d));
    }, v);
    const openPrompt = async () => {
      await page.goto(`${url}/#/project/${SECTION}/${BOOK}/prompts`,
        { waitUntil: "load" });
      // смена хэша страницу не перезагружает: pref из localStorage до SPA дойдёт
      // только полным reload
      await page.reload({ waitUntil: "load" });
      await page.waitForSelector(".prompt-item", { timeout: 15000 });
      // не «первый», а нужный: в книге лежат и промпты других стадий
      await page.locator('.prompt-item:has-text("translate.txt")').first().click();
      await page.waitForTimeout(1500);
      return marks();
    };
    await setLang("text");
    const plain = await openPrompt();
    if (!/<translate>/.test(plain.text))
      problems.push(`промпты: в редакторе не тот файл «${plain.text}»`);
    if (plain.hl)
      problems.push(`промпты: ждём plain text, а подсвечено ${plain.hl} span`);
    await setLang("xml");
    const xml = await openPrompt();
    if (!xml.hl)
      problems.push(`промпты: выбор XML не подсветил теги (span ${xml.spans})`);
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "scenario-prompts.png") });
    await setLang("text");
    await page.evaluate(() => { location.hash = "#/hub"; });
    await page.waitForTimeout(400);
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/editor`,
      { waitUntil: "load" });
    await page.waitForTimeout(1500);
    const chapter = await page.evaluate(() => {
      const hosts = [...document.querySelectorAll(".ed-cm")];
      const spans = hosts.flatMap((x) => [...x.querySelectorAll("span[class]")]);
      return {
        hosts: hosts.length,
        hl: spans.filter((x) => x.className.includes("\u037c")).length,
      };
    });
    if (chapter.hl)
      problems.push(`редактор глав: текст файла подсветкой залит `
        + `(span-ов ${chapter.hl})`);
    log(`${problems.length === before ? "✅" : "❌"} промпты: plain text `
      + ` «${plain.text}» · ${plain.hl} → XML ${xml.hl} из ${xml.spans} span, `
      + `редактор глав ${chapter.hl}`
      + (problems.length > before
        ? `\n     ${problems.slice(before).join("\n     ")}` : ""));
  }

  /* «Промпты»: копирование ВСЕГО набора шаблона и очистка папки — действия
   * с составом папки (кнопки слева от редактора), а не с открытым файлом. */
  {
    const before = problems.length;
    const bookPrompts = path.join(seedDir || "", SECTION, BOOK, "prompts");
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/prompts`,
      { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".prompt-item", { timeout: 15000 });
    const side = await page.evaluate(() => {
      const box = document.querySelector(".prompts-side");
      return box
        ? [...box.querySelectorAll("button")].map((b) => b.textContent.trim())
        : [];
    });
    for (const act of ["Создать", "Из шаблона", "Очистить"]) {
      if (!side.includes(act)) problems.push(`промпты: слева нет «${act}» (${side.join(",")})`);
    }
    /* геометрия: кнопки файлов левее редактора, сохранение — над ним */
    const geo = await page.evaluate(() => {
      const box = (s) => document.querySelector(s)?.getBoundingClientRect();
      return {
        side: box(".prompts-side")?.right || 0,
        main: box(".prompts-main")?.left || 0,
        editor: box(".prompts-main .editor-cm")?.top || 0,
        save: [...document.querySelectorAll(".prompts-main button")]
          .filter((b) => b.textContent.trim() === "Сохранить")
          .map((b) => b.getBoundingClientRect().top)[0] || 0,
      };
    });
    if (!(geo.side < geo.main))
      problems.push(`промпты: список не левее редактора (${geo.side} / ${geo.main})`);
    if (!(geo.save && geo.save < geo.editor))
      problems.push(`промпты: «Сохранить» не над редактором (${geo.save} / ${geo.editor})`);
    await page.locator('.prompts-side button:has-text("Из шаблона")').click();
    await page.waitForTimeout(500);
    /* способ «весь набор»: в книгу приезжают все промпты выбранного набора */
    await page.locator('.modal-backdrop label:has-text("Весь набор") input').check();
    await page.waitForTimeout(300);
    const setInfo = await page.evaluate(() =>
      (document.querySelector(".modal-backdrop .card-hint") || {}).textContent || "");
    if (!/General:/.test(setInfo) || !/pipeline_prompt\.txt/.test(setInfo))
      problems.push(`промпты: список файлов набора «${setInfo.trim()}»`);
    await page.locator('.modal-backdrop button:has-text("Скопировать")').click();
    await page.waitForTimeout(1500);
    const copied = fs.existsSync(path.join(bookPrompts, "pipeline_prompt.txt"))
      && fs.existsSync(path.join(bookPrompts, "ner_check_prompt.txt"));
    if (!copied) problems.push("промпты: набор не скопировался в книгу");
    const listed = await page.evaluate(() =>
      [...document.querySelectorAll(".prompt-item")].map((b) => b.textContent));
    if (!listed.some((t) => t.includes("pipeline_prompt.txt")))
      problems.push("промпты: скопированный файл не появился в списке");
    /* очистка: все промпты книги одним действием, каталог остаётся */
    await page.locator('.prompts-side button:has-text("Очистить")').click();
    await page.waitForTimeout(500);
    await page.locator(".modal-backdrop input.input").fill("УДАЛИТЬ");
    await page.locator('.modal-backdrop button:has-text("Подтвердить")').click();
    await page.waitForTimeout(1500);
    const left = fs.existsSync(bookPrompts)
      ? fs.readdirSync(bookPrompts).length : 0;
    if (left) problems.push(`промпты: после очистки осталось ${left} файл(ов)`);
    const empty = await page.evaluate(() =>
      (document.querySelector(".prompt-list .empty") || {}).textContent || "");
    if (!/Нет промптов/.test(empty))
      problems.push(`промпты: после очистки список «${empty.trim()}»`);
    /* данные пробы возвращаются на место: дальнейшие сценарии ждут seeded
       промпты (проверка промпта стадии берёт ner_prompt.txt) */
    fs.writeFileSync(path.join(bookPrompts, "translate.txt"),
      "<translate>переведи</translate>\n", "utf-8");
    fs.writeFileSync(path.join(bookPrompts, "ner_prompt.txt"),
      "<prompt_pass1>\n{chunk_text}\n</prompt_pass1>\n", "utf-8");
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "scenario-prompts-set.png") });
    log(`${problems.length === before ? "✅" : "❌"} промпты: набор`
      + ` слева ${side.length} кнопок · копия ${copied ? "есть" : "НЕТ"}`
      + ` · очистка → файлов ${left}`
      + (problems.length > before
        ? `\n     ${problems.slice(before).join("\n     ")}` : ""));
  }

  /* «Файлы»: строка — чекбокс выделения и кнопки одного объекта (правка,
   * переименовать); групповые действия (скачать/перенести/удалить) — в панели
   * выделения, она заменяет кнопки тулбара; построчных меню «⋮» нет */
  {
    const before3 = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/files`, { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".files-list .frow", { timeout: 15000 });
    const read = () =>
      page.evaluate(() => {
        const tips = (root) => [...(root || document.createElement("div"))
          .querySelectorAll(".icon-btn")]
          .map((b) => b.getAttribute("aria-label") || "");
        const rows = [...document.querySelectorAll(".files-list .frow")];
        const hid = (sel) => {
          const el = document.querySelector(sel);
          return !!el && el.classList.contains("hidden");
        };
        return {
          menus: document.querySelectorAll(".files-list .kebab-btn").length,
          rows: rows.length,
          cbs: rows.filter((r) => r.querySelector(".fsel")).length,
          selRows: rows.filter((r) => r.classList.contains("frow-sel")).length,
          rowTips: rows.map((r) => tips(r)),
          all: !!document.querySelector(".files-toolbar .fsel"),
          tools: !hid(".files-tools"),
          bar: !hid(".files-sel"),
          count: ((document.querySelector(".files-sel-count") || {})
            .textContent || "").trim(),
          barTips: tips(document.querySelector(".files-sel")),
        };
      });
    const f0 = await read();
    if (f0.menus) problems.push(`файлы: в списке ${f0.menus} меню «⋮»`);
    if (!f0.all) problems.push("файлы: в тулбаре нет чекбокса «выделить всё»");
    if (f0.cbs !== f0.rows) problems.push(`файлы: чекбоксов ${f0.cbs} из ${f0.rows}`);
    if (!f0.tools || f0.bar) problems.push("файлы: без выделки видна панель выделения");
    // в строке — только действия одного объекта; опасные и групповые — наверху
    if (f0.rowTips.some((t) => t.some((x) => /Удалить|Перенести/.test(x))))
      problems.push(`файлы: в строке осталось групповое действие (${f0.rowTips[0]})`);
    if (f0.rowTips.some((t) => !t.some((x) => x.startsWith("Переименовать "))))
      problems.push("файлы: не у каждой строки есть «Переименовать»");
    if (!f0.rowTips.some((t) => t.some((x) => x.startsWith("Править "))))
      problems.push("файлы: кнопки «Править» у файлов нет");
    // скачать файл можно и без выделки — кнопка стоит между правкой и именем
    if (!f0.rowTips.some((t) => t.some((x) => x.startsWith("Скачать "))))
      problems.push("файлы: кнопки «Скачать» у файлов нет");
    // выделяем первый файл — панель появляется, кнопки тулбара уходят
    const boxes = page.locator('.files-list .frow:not([data-dir="1"]) .fsel');
    await boxes.first().check();
    await page.waitForTimeout(500);
    const f1 = await read();
    if (f1.tools) problems.push("файлы: кнопки тулбара не скрылись при выделке");
    if (!f1.bar) problems.push("файлы: панель выделения не появилась");
    if (f1.count !== "выделено: 1") problems.push(`файлы: счётчик «${f1.count}»`);
    // выделенная строка должна быть видна: подложка вместо одного чекбокса
    if (f1.selRows !== 1) problems.push(`файлы: выделенных строк ${f1.selRows}`);
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-files-selected.png") });
    const want = ["Скачать выделенные файлы", "Перенести в…", "Удалить выделенное",
      "Снять выделение"];
    if (want.some((w) => !f1.barTips.includes(w)))
      problems.push(`файлы: действия панели (${f1.barTips.join(" | ")})`);
    if (!f1.barTips.some((t) => t.startsWith("Переименовать ")))
      problems.push("файлы: при одном объекте в панели нет «Переименовать»");
    // перенос: один select по всему дереву каталогов проекта
    await page.locator('.files-sel .icon-btn[aria-label^="Перенести"]').first().click();
    await page.waitForTimeout(600);
    const mv = await page.evaluate(() => {
      const box = document.querySelector(".modal-backdrop select");
      return {
        open: document.querySelectorAll(".modal-backdrop").length,
        opts: box ? [...box.options].map((o) => o.textContent.trim()) : [],
      };
    });
    if (mv.open !== 1) problems.push(`файлы: модалка переноса открыта в ${mv.open}`);
    if (mv.opts[0] !== "Корень проекта")
      problems.push(`файлы: первое направление — «${mv.opts[0]}»`);
    if (!mv.opts.includes("prompts"))
      problems.push(`файлы: дерево каталогов короткое (${mv.opts.length})`);
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-files-move.png") });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    // «Снять выделение» — панель уходит, кнопки тулбара возвращаются
    await page.locator('.files-sel .icon-btn[aria-label="Снять выделение"]').first().click();
    await page.waitForTimeout(400);
    const f2 = await read();
    if (f2.bar || !f2.tools)
      problems.push("файлы: после «Снять» панель осталась/кнопки не вернулись");
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-files.png") });
    log(`${problems.length === before3 ? "✅" : "❌"} файлы: выделка   строк ${f0.rows} · панель: ${f1.barTips.length} действий · перенос: ${mv.opts.length} папок`);
  }

  /* «Логи»: список папок и файлов рисует общий пейджер; клик по файлу
   * показывает содержимое, «↑» поднимает на папку выше */
  {
    const before4 = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/logs`, { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".prompt-list .prompt-item", { timeout: 15000 });
    const l0 = await page.evaluate(() => ({
      rows: document.querySelectorAll(".prompt-list .prompt-item").length,
      dirs: [...document.querySelectorAll(".prompt-list .prompt-item")]
        .map((x) => x.textContent.trim()).filter((t) => t.endsWith("/")).length,
      info: ((document.querySelector(".ner-pager-info") || {})
        .textContent || "").trim(),
    }));
    if (l0.rows < 2) problems.push(`логи: строк ${l0.rows} (ожидаем файл + папку)`);
    if (l0.dirs !== 1) problems.push(`логи: папок в списке ${l0.dirs}`);
    if (!/логова?:\s*1/.test(l0.info)) problems.push(`логи: подпись «${l0.info}»`);
    await page.locator('.prompt-list .prompt-item:has-text("ner.log")').first().click();
    await page.waitForTimeout(700);
    const view = await page.evaluate(() => ((document.querySelector(".log-view") || {})
      .textContent || "").trim());
    if (!/ner: старт/.test(view)) problems.push(`логи: содержимое «${view.slice(0, 40)}»`);
    // вложенная папка: заход и подъём наверх
    await page.locator('.prompt-list .prompt-item:has-text("chapters/")').first().click();
    await page.waitForTimeout(600);
    const sub = await page.evaluate(() => document.querySelectorAll(".prompt-list .prompt-item").length);
    if (sub !== 1) problems.push(`логи: в chapters/ строк ${sub}`);
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-logs.png") });
    log(`${problems.length === before4 ? "✅" : "❌"} логи: список     ${l0.rows} строк · вложенно ${sub} · просмотр: ${view ? "есть" : "пусто"}`);
  }

  /* «Статус»: над таблицей — готовность по типам глав: трек, процент и
   * строка отсутствующих глав диапазонами (сервер считает их сам —
   * core/projects.py::progress_missing). Глава без файла сразу и в
   * пропусках, и в таблице — расхождению взяться неоткуда. */
  {
    const before5 = problems.length;
    const chDir = path.join(seedDir || "", SECTION, BOOK, "chapters");
    /* третья глава без редактуры и полировки — пропуск в двух барах; после
       проверки её убираем: дальше идут сценарии на исходном наборе глав */
    const third = path.join(chDir, "00000_3_Глава 3");
    if (seedDir) {
      fs.mkdirSync(third, { recursive: true });
      fs.writeFileSync(path.join(third, "chapter.txt"), "Третья глава.\n", "utf-8");
      fs.writeFileSync(path.join(third, "translated.txt"), "Третья (перевод).\n", "utf-8");
    }
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/status`, { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".status-stage", { timeout: 15000 });
    const st = await page.evaluate(() => ({
      stages: [...document.querySelectorAll(".status-stage")].map((x) => ({
        text: x.textContent.replace(/\s+/g, " ").trim(),
        pct: (x.querySelector(".progress-count") || {}).textContent || "",
        width: (x.querySelector(".progress-fill") || {}).getAttribute
          ? x.querySelector(".progress-fill").getAttribute("style") : "",
      })),
      rows: document.querySelectorAll(".ch-table .ch-row").length,
      missingClass: document.querySelectorAll(".status-missing-has").length,
    }));
    if (st.stages.length !== 3)
      problems.push(`статус: блоков стадий ${st.stages.length}`);
    if (st.rows !== 3)
      problems.push(`статус: строк таблицы ${st.rows} (ожидаем 3 главы)`);
    if (!st.stages[0]?.pct?.includes("3/3 · 100%"))
      problems.push(`статус: у перевода «${st.stages[0]?.pct}»`);
    // обработка и полировка — готово 2 из 3: пропуск главой 3 в обоих барах
    if (st.missingClass !== 2)
      problems.push(`статус: блоков с пропусками ${st.missingClass} (ожидаем 2)`);
    if (!/Отсутствуют главы:\s*3/.test(st.stages[1]?.text || ""))
      problems.push(`статус: пропуски редактуры «${st.stages[1]?.text}»`);
    if (!/width:67%/.test(st.stages[1]?.width || ""))
      problems.push(`статус: трек редактуры «${st.stages[1]?.width}»`);
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "scenario-status.png") });
    if (seedDir) fs.rmSync(third, { recursive: true, force: true });
    log(`${problems.length === before5 ? "✅" : "❌"} статус: типы глав  `
      + `${st.stages.map((x) => x.pct).join(" · ")} · таблица ${st.rows} глав`);
  }

  /* «История»: список точек страницами, сравнение выбранной пары (всегда от
   * старой к новой), догрузка файлов и раскрашенный дифф файла. Точки
   * создаются тем же POST, что и кнопкой вкладки: между ними меняется один
   * файл книги, поэтому сравнение ОБЯЗАНО показывать изменения */
  {
    const before5 = problems.length;
    const project = `${SECTION}/${BOOK}`;
    const call = async (method, p, body) => {
      const res = await fetch(`${url}/api${p}`, {
        method,
        headers: {
          "X-Requested-With": "fetch",
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      const text = await res.text();
      let data = null;
      try {
        data = JSON.parse(text);
      } catch {
        /* не-JSON */
      }
      return { status: res.status, data };
    };
    for (let i = 1; i <= 3; i += 1) {
      await call("PUT", "/file", {
        project,
        path: "notes.md",
        content: `# Заметки пробы · вариант ${i}\n`,
      });
      const r = await call("POST", "/history", { project, label: `Точка ${i}` });
      if (r.status !== 200 || !r.data || r.data.created !== true)
        problems.push(`история: точка ${i} не создана (${r.status})`);
    }
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/history`,
      { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".history-list .history-row", { timeout: 15000 });
    const read = () => page.evaluate(() => {
      const text = (sel) => ((document.querySelector(sel) || {})
        .textContent || "").trim();
      const tips = (root) => [...(root || document.createElement("div"))
        .querySelectorAll(".icon-btn")].map((b) => b.getAttribute("aria-label") || "");
      const shown = (sel) => {
        const el = document.querySelector(sel);
        return el && !el.classList.contains("hidden")
          ? el.textContent.replace(/\s+/g, " ").trim() : "";
      };
      const rows = [...document.querySelectorAll(".history-list .history-row")];
      return {
        rows: rows.length,
        rowTips: rows.map((r) => tips(r)),
        selRows: rows.filter((r) => r.classList.contains("frow-sel")).length,
        headings: [...document.querySelectorAll(".history-heading")]
          .map((x) => x.textContent.trim()),
        /* скрытая кнопка остаётся в DOM со своей подписью — читаем только
           видимую (иначе «осталось 0» читалось бы как лишний пейджер) */
        pager: shown(".history-wrap > .history-more"),
        bar: !((document.querySelector(".files-sel") || { classList: { contains: () => true } })
          .classList.contains("hidden")),
        count: text(".files-sel-count"),
        barTips: tips(document.querySelector(".files-sel")),
      };
    });
    const h0 = await read();
    if (h0.rows !== 3) problems.push(`история: строк ${h0.rows}, ждём 3`);
    if (h0.headings[0] !== "Контрольные точки · 3")
      problems.push(`история: шапка «${h0.headings[0]}»`);
    if (h0.pager) problems.push(`история: все точки на одной странице, а пейджер «${h0.pager}»`);
    if (h0.bar) problems.push("история: панель выделения видна без выделки");
    /* своей кнопки «сравнить с предыдущей» у строки нет: у самой старой
       точки предыдущей точки нет — сравнение живёт в панели выделения */
    if (h0.rowTips.some((t) => t.some((x) => /Сравнить/.test(x))))
      problems.push(`история: в строке осталось сравнение (${h0.rowTips[0]})`);
    if (!h0.rowTips.every((t) => t.length === 1
      && t[0] === "Вернуть файлы проекта к этой точке"))
      problems.push(`история: кнопки строки (${h0.rowTips[0]})`);
    // крайние точки: самая старая и самая свежая — сравнение обязано быть парой
    const boxes = page.locator(".history-list .history-row .fsel");
    await boxes.nth(0).check();
    await page.waitForTimeout(400);
    const h1 = await read();
    if (!h1.bar) problems.push("история: панель выделения не появилась");
    if (h1.count !== "выделено: 1")
      problems.push(`история: счётчик выделки «${h1.count}»`);
    if (h1.barTips.length !== 1 || h1.barTips[0] !== "Снять выделение")
      problems.push(`история: на одной точке панель «${h1.barTips.join("|")}»`);
    await boxes.nth(2).check();
    await page.waitForTimeout(400);
    const h2 = await read();
    if (h2.count !== "выделено: 2") problems.push(`история: счётчик «${h2.count}»`);
    if (!h2.barTips.includes("Сравнить выбранные точки"))
      problems.push(`история: в панели нет сравнения (${h2.barTips.join("|")})`);
    if (h2.selRows !== 2) problems.push(`история: выделенных строк ${h2.selRows}`);
    if (SHOT) {
      await page.screenshot({ path: path.join(OUT, "scenario-history.png") });
    }
    await page.locator('.files-sel .icon-btn[aria-label="Сравнить выбранные точки"]')
      .first().click();
    await page.waitForTimeout(900);
    const diff = await page.evaluate(() => {
      /* скрытая кнопка остаётся в DOM со своей подписью — читаем только
         видимую (иначе «осталось 0» читалось бы как лишний пейджер) */
      const shown = (sel) => {
        const el = document.querySelector(sel);
        return el && !el.classList.contains("hidden")
          ? el.textContent.replace(/\s+/g, " ").trim() : "";
      };
      const box = document.querySelector(".modal-backdrop");
      const rows = [...(box || document.createElement("div"))
        .querySelectorAll(".history-diff-file")];
      return {
        modals: document.querySelectorAll(".modal-backdrop").length,
        head: [...(box || document.createElement("div"))
          .querySelectorAll(".history-diff-head > div")]
          .map((x) => x.textContent.trim()),
        rows: rows.length,
        first: rows.length
          ? (rows[0].querySelector("summary") || {}).textContent?.replace(/\s+/g, " ").trim()
          : "",
        more: shown(".modal-backdrop .history-more"),
        patch: "",
      };
    });
    if (diff.modals !== 1) problems.push(`история: модалок сравнения ${diff.modals}`);
    /* пустой список файлов был главным «не работает»: limit=0 на сервере
       отдавал пустую страницу на любой паре точек */
    if (diff.rows !== 1) problems.push(`история: файлов в сравнении ${diff.rows}`);
    if (!/С: Точка 1/.test(diff.head[0] || "") || !/По: Точка 3/.test(diff.head[1] || ""))
      problems.push(`история: порядок сравнения (${diff.head.join(" / ")})`);
    if (!/изменённых файлов: 1/.test(diff.head[2] || ""))
      problems.push(`история: счётчик файлов «${diff.head[2] || ""}»`);
    if (!/notes\.md/.test(diff.first) || !/изменён/.test(diff.first))
      problems.push(`история: строка файла «${diff.first}»`);
    if (diff.more) problems.push(`история: кнопка догрузки файлов «${diff.more}»`);
    // клик по строке файла — unified-diff; второй клик его не переспрашивает
    await page.locator(".modal-backdrop .history-diff-file summary").first().click();
    await page.waitForTimeout(700);
    const patch = await page.evaluate(() => ({
      text: ((document.querySelector(".history-patch") || {}).textContent || "")
        .replace(/\s+/g, " ").trim(),
      marks: document.querySelectorAll(".history-diff-marker").length,
    }));
    if (!/вариант 1/.test(patch.text) || !/вариант 3/.test(patch.text))
      problems.push(`история: дифф файла «${patch.text.slice(0, 60)}»`);
    if (patch.marks < 2) problems.push(`история: в диффе маркеров ${patch.marks}`);
    if (SHOT) {
      await page.screenshot({ path: path.join(OUT, "scenario-history-diff.png") });
    }
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    // возврат: своя модалка с объяснением; «Отмена» ничего не трогает
    await page.locator('.history-row .icon-btn[aria-label^="Вернуть"]').first().click();
    await page.waitForTimeout(500);
    const restore = await page.evaluate(() => ({
      modals: document.querySelectorAll(".modal-backdrop").length,
      title: ((document.querySelector(".modal-backdrop .modal-title") || {})
        .textContent || "").trim(),
      btns: [...document.querySelectorAll(".modal-backdrop .modal-actions .btn")]
        .map((b) => b.textContent.trim()),
    }));
    if (restore.modals !== 1 || restore.title !== "Возврат к контрольной точке")
      problems.push(`история: модалка возврата «${restore.title}» (${restore.modals})`);
    if (restore.btns.join(",") !== "Отмена,Вернуться")
      problems.push(`история: кнопки возврата (${restore.btns.join("/")})`);
    if (SHOT) {
      await page.screenshot({ path: path.join(OUT, "scenario-history-restore.png") });
    }
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    log(`${problems.length === before5 ? "✅" : "❌"} история: точки    ${h0.rows} шт · сравнение: ${diff.rows} файл(ов) · дифф: ${patch.marks} строк · возврат: ${restore.btns.join("/")}`);
  }

  /* страница «Настройки»: блоки реестра карточками, секрет — маской,
   * сохранение пишет общий конфиг; блоки самого веб-сервера в реестре остались,
   * но на экране их нет; внешний вид — своя субвкладка с localStorage-
   * предпочтениями, включая выбор языка подсветки */
  {
    const before = problems.length;
    await page.goto(`${url}/#/settings`, { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".settings-cards .review-card", { timeout: 15000 });
    const head = await page.evaluate(() => ({
      tabs: [...document.querySelectorAll(".tabs .tab")]
        .map((t) => t.textContent.trim()),
      active: (document.querySelector(".tabs .tab-active") || {})
        .textContent?.trim() || "",
      cards: [...document.querySelectorAll(".settings-cards .review-card-title")]
        .map((x) => x.textContent.trim()),
      // подпись автосохранения: на General сохранять нечего — она спрятана
      status: (document.querySelector(".settings-save-state") || {})
        .textContent?.trim() || "",
      btns: document.querySelectorAll(".page-header-actions button").length,
      html: document.getElementById("app").innerHTML,
    }));
    const wantTabs = ["Модель и сервер", "Перевод", "Глоссарий", "Проверки",
      "Книга и файлы", "Внешний вид"];
    if (head.tabs.join(",") !== wantTabs.join(","))
      problems.push(`настройки: субвкладки (${head.tabs.join("/")})`);
    if (head.active !== head.tabs[0])
      problems.push(`настройки: активна вкладка «${head.active}»`);
    for (const t of ["Профили LLM", "Рассуждения модели"])
      if (!head.cards.includes(t)) problems.push(`настройки: нет карточки «${t}»`);
    // веб-сервер — не книга: его блоки и поля в интерфейс не едут
    for (const t of ["Веб-сервер: сеть и доступ", "Веб-сервер: данные и задачи"])
      if (head.cards.includes(t))
        problems.push(`настройки: карточка веб-сервера «${t}» на экране`);
    if (await page.locator('.settings-cards [name="WEB_PORT"]').count())
      problems.push("настройки: поля веб-сервера рисуются в форме");
    // путь общего конфига экрану не нужен; на General сохранять нечего
    if (head.status) problems.push(`настройки: статус General «${head.status}»`);
    if (head.btns) problems.push(`настройки: в шапке кнопки (${head.btns})`);
    if (/\.env\s*[=:/]/.test(head.html)) problems.push("настройки: путь .env на экране");
    if (head.html.includes("probe-secret"))
      problems.push("настройки: значение API-ключa уехало в SPA");
    // значение секрета в DOM — только маска (value пароля innerHTML не
    // сериализует, поэтому читаем inputValue контрола)
    const masked = await page.locator(".settings-cards .field")
      .filter({ hasText: "API-ключ" }).first()
      .locator("input,select,textarea").first().inputValue();
    if (masked !== "•".repeat("probe-secret".length))
      problems.push(`настройки: секрет «${masked}»`);
    // где взять ключ: ссылка под самым полем (а не подпись, не значение):
    // подпись «Получить API-ключ», наружу и без opener'а, в .env её нет
    const keyDoc = await page.locator(".settings-cards .field")
      .filter({ hasText: "API-ключ" }).first()
      .evaluate((el) => {
        const a = el.querySelector("a.field-doc");
        const input = el.querySelector("input");
        if (!a || !input) return null;
        return {
          text: a.textContent.trim(), href: a.getAttribute("href"),
          target: a.getAttribute("target"), rel: a.getAttribute("rel"),
          // под полем, а не перед ним
          after: !!(input.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING),
        };
      });
    if (!keyDoc) problems.push("настройки: у поля ключа нет ссылки «Получить API-ключ»");
    else if (keyDoc.text !== "Получить API-ключ") problems.push(`настройки: ссылка «${keyDoc.text}»`);
    if (keyDoc && keyDoc.href !== "https://routerai.ru/p/D7G6RUQ5")
      problems.push(`настройки: ссылка ключа ведёт на ${keyDoc.href}`);
    if (keyDoc && (keyDoc.target !== "_blank" || !/noopener/.test(keyDoc.rel || "")))
      problems.push("настройки: ссылка ключа уводит SPA из вкладки");
    if (keyDoc && !keyDoc.after) problems.push("настройки: ссылка ключа выше поля");
    // «Получить API-ключ» — тонкая строка под полем, а не второй заголовок
    const keyDocBox = keyDoc ? await page.locator(".settings-cards a.field-doc").first()
      .evaluate((a) => {
        const cs = getComputedStyle(a);
        return { size: parseFloat(cs.fontSize), color: cs.color,
          deco: cs.textDecorationLine };
      }) : null;
    if (keyDocBox && keyDocBox.size > 12) problems.push(`настройки: ссылка ключа ${keyDocBox.size}px — спорит с полем`);
    if (keyDocBox && keyDocBox.deco !== "none")
      problems.push("настройки: ссылка ключа подчёркнута постоянно — выглядит кнопкой");
    // ссылка живёт внутри своей карточки и не наезжает на соседние поля
    const keyDocGeom = keyDoc ? await page.locator(".settings-cards .field")
      .filter({ hasText: "API-ключ" }).first()
      .evaluate((el) => {
        const card = el.closest(".review-card").getBoundingClientRect();
        const a = el.querySelector("a.field-doc").getBoundingClientRect();
        const input = el.querySelector("input").getBoundingClientRect();
        const own = el.getBoundingClientRect();
        const rows = [...el.parentElement.children]
          .filter((x) => x !== el)
          .map((x) => x.getBoundingClientRect());
        const clash = rows.some((r) => r.width > 0 && a.right > r.left
          && a.left < r.right && a.bottom > r.top && a.top < r.bottom);
        return { inside: a.left >= card.left && a.right <= card.right
          && a.bottom <= card.bottom, below: a.top >= input.bottom - 1,
          own: a.left >= own.left && a.right <= own.right, clash };
      }) : null;
    if (keyDocGeom && !keyDocGeom.inside) problems.push("настройки: ссылка ключа вылезла из карточки");
    if (keyDocGeom && !keyDocGeom.own) problems.push("настройки: ссылка ключа вышла из своей колонки");
    if (keyDocGeom && !keyDocGeom.below) problems.push("настройки: ссылка ключа наехала на поле");
    if (keyDocGeom && keyDocGeom.clash) problems.push("настройки: ссылка ключа перекрывает соседнее поле");
    // «Выбор провайдера»: карточка объясняет себя сама, а чекбокс — такое же
    // двухстрочное поле, как соседи (подпись сверху, контрол под ней)
    const prov = await page
      .locator(".settings-cards .review-card", { hasText: "Выбор провайдера" })
      .first()
      .evaluate((el) => {
        const rows = [...el.querySelectorAll(".settings-fields > .field")];
        const fb = rows.map((x) => ({
          label: (x.querySelector(".field-label") || {}).textContent?.trim() || "",
          row: !!x.querySelector(".field-row.field-check"),
          state: (x.querySelector(".field-check-state") || {}).textContent?.trim() || "",
        })).find((x) => x.label.includes("Запасные"));
        return {
          note: (el.querySelector(".review-card-note") || {}).textContent?.trim() || "",
          fields: rows.length,
          fb: fb || null,
        };
      });
    if (!/RouterAI/.test(prov.note) || !/OpenRouter/.test(prov.note))
      problems.push("настройки: карточка провайдеров не говорит, кому она нужен");
    if (!/не читает/.test(prov.note))
      problems.push("настройки: карточка провайдеров не говорит про локальный сервер");
    if (!prov.fb) problems.push("настройки: чекбокс «Запасные провайдеры» — не поле с подписью сверху");
    else if (!prov.fb.row || !["включено", "выключено"].includes(prov.fb.state))
      problems.push(`настройки: чекбокс «${prov.fb.label}» → ${prov.fb.row ? "строка" : "не строка"}, состояние «${prov.fb.state}»`);
    if (SHOT) {
      await page.screenshot({ path: path.join(OUT, "settings.png") });
      // карточка подключения целиком: видно ссылку «Получить API-ключ»,
      // кнопку проверки сервера и подсказку поля
      await page
        .locator(".settings-cards .review-card", { hasText: "Подключение" })
        .first()
        .screenshot({ path: path.join(OUT, "settings-conn.png") });
      // карточка провайдеров целиком: видно выравнивание чекбокса
      await page
        .locator(".settings-cards .review-card", { hasText: "Выбор провайдера" })
        .first()
        .screenshot({ path: path.join(OUT, "settings-provider.png") });
    }
    // карточка рассуждений: поля, варианты и предзаполнение — из реестра
    const card = page.locator(".settings-cards .review-card",
      { hasText: "Рассуждения модели" }).first();
    const f = await card.evaluate((el) => ({
      names: [...el.querySelectorAll("input,select,textarea")]
        .map((c) => c.getAttribute("name")),
      kinds: [...el.querySelectorAll("input,select,textarea")]
        .map((c) => c.tagName.toLowerCase()),
      opts: [...el.querySelectorAll("select")].map(
        (sel) => [...sel.options].map((o) => o.value).join(",")),
      vals: [...el.querySelectorAll("input,select,textarea")]
        .map((c) => c.value),
    }));
    if (SHOT) {
      await card.screenshot({ path: path.join(OUT, "settings-reasoning.png") });
    }
    // name контрола = КЛЮЧ .env (имена полей стадий не уникальны)
    const want = ["REASONING_MODE", "THINKING_PROFILE", "REASONING_EFFORT",
      "THINKING_BUDGET", "LLM_EXTRA_BODY_JSON"];
    if (f.names.join(",") !== want.join(","))
      problems.push(`reasoning: поля ${f.names.join(",")}`);
    if (f.kinds.join(",") !== "select,select,select,input,input")
      problems.push(`reasoning: контролы ${f.kinds.join(",")}`);
    if (f.opts[0] !== "default,on,off")
      problems.push(`reasoning: режимы «${f.opts[0]}»`);
    if (!f.opts[1].startsWith("openai,anthropic,"))
      problems.push(`reasoning: профили «${f.opts[1]}»`);
    if (f.opts[2] !== ",none,minimal,low,medium,high,xhigh,max")
      problems.push(`reasoning: уровни «${f.opts[2]}»`);
    // предзаполнение = значения реестра: сверяем с ответом сервера, а не с
    // литералами (иначе проба ломается на каждой правке дефолтов)
    if (!f.opts[0].split(",").includes(f.vals[0]) ||
      !f.opts[1].split(",").includes(f.vals[1]) || f.vals[3] !== "0")
      problems.push(`reasoning: предзаполнилось ${f.vals.join("|")}`);
    await card.locator('select[name="REASONING_MODE"]').selectOption("on");
    await card.locator('select[name="THINKING_PROFILE"]').selectOption("qwen");
    await card.locator('select[name="REASONING_EFFORT"]').selectOption("xhigh");
    await card.locator('input[name="THINKING_BUDGET"]').fill("2048");
    await card.locator('input[name="LLM_EXTRA_BODY_JSON"]').fill('{"top_k": 5}');
    // маска секрета — как значение: сервер такой ключ не трогает
    // кнопки «Сохранить» нет: изменённые поля уходят PUT'ом сами
    if (await page.locator(".page-header-actions button").count())
      problems.push("настройки: в шапке осталась кнопка сохранения");
    await page.waitForTimeout(1200);
    if (seedDir) {
      const env = fs.readFileSync(path.join(seedDir, ".env"), "utf-8");
      const lines = env.split("\n");
      for (const line of ["REASONING_MODE=on", "THINKING_PROFILE=qwen",
        "REASONING_EFFORT=xhigh", "THINKING_BUDGET=2048",
        'LLM_EXTRA_BODY_JSON={"top_k": 5}'])
        if (!lines.includes(line)) problems.push(`reasoning: в .env нет ${line}`);
      for (const keep of ["HOST=http://127.0.0.1:9/v1", "MODEL=probe-model",
        "API_KEY=probe-secret"])
        if (!env.includes(keep)) problems.push(`reasoning: .env потерял ${keep}`);
      if (env.includes("••••")) problems.push("reasoning: маска попала в .env");
    } else {
      log("⚠️  reasoning: внешний сервер — файл .env не проверяем");
    }
    /* субвкладка «Внешний вид»: localStorage-карточки, полей реестра нет и
     * кнопок сохранения нет вовсе — предпочтения пишутся по change контрола */
    await page.locator('.tabs .tab:has-text("Внешний вид")').first().click();
    await page.waitForTimeout(400);
    const g2 = await page.evaluate(() => ({
      cards: [...document.querySelectorAll(".settings-cards .review-card-title")]
        .map((x) => x.textContent.trim()),
      fields: document.querySelectorAll(".settings-cards [name]").length,
      btns: document.querySelectorAll(".page-header-actions button, .settings-cards button").length,
      state: (document.querySelector(".settings-save-state") || {}).className || "",
    }));
    if (g2.cards.join(",") !== "Внешний вид,Интерфейс")
      problems.push(`настройки: карточки «${g2.cards.join(" | ")}»`);
    if (g2.fields) problems.push(`настройки: полей реестра на вкладке ${g2.fields}`);
    if (g2.btns) problems.push(`настройки: на странице остались кнопки (${g2.btns})`);
    if (!/hidden/.test(g2.state))
      problems.push(`настройки: статус конфига виден на «Внешнем виде» (${g2.state})`);
    const savedTab = seedDir ? await page.evaluate(
      () => localStorage.getItem("settingsTab") || "") : "";
    if (seedDir && savedTab !== "ui")
      problems.push(`настройки: вкладка не запомнена (${savedTab})`);
    if (SHOT) await page.screenshot({ path: path.join(OUT, "settings-ui.png") });
    // формы стадий рассуждений больше не касаются
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/run`,
      { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForTimeout(1200);
    const leaked = await page.evaluate(
      () => /reasoning|thinking|extra_body/i.test(
        document.getElementById("app").innerHTML));
    if (leaked) problems.push("reasoning: поле стадии осталось в форме запуска");
    log(`${problems.length === before ? "✅" : "❌"} настройки+reasoning ${head.cards.length} блоков · вкладка «${head.active}»`);
  }

  /* «Настройки»: карточка стадии называется ровно как её запуск (название
   * стадии живёт в одном месте — core/settings.py::STAGE_TITLES); поля политики
   * не голосующих полей — на своей карточке */
  {
    const before = problems.length;
    const want = {
      "Перевод": ["Перевод (LLM)"],
      "Глоссарий": ["Создание глоссария (LLM)"],
      "Проверки": ["Проверка глоссария (LLM)", "Проверка перевода",
        "Проверка перевода (LLM)", "Оценка перевода (LLM)"],
      "Книга и файлы": ["Разбор исходника на главы", "Компиляция TXT/EPUB/FB2",
        "Создание Wiki (LLM)", "Массовые замены"],
    };
    const oldNames = ["Глоссарий (NER)", "EPUB → главы", "Сборка глав",
      "Вики книги", "Оценка качества", "Проверка перевода LLM",
      "Перевод (translate"];
    await page.goto(`${url}/#/settings`, { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".tabs .tab", { timeout: 15000 });
    const slugs = { "Перевод": "transfer", "Глоссарий": "glossary",
      "Проверки": "checks", "Книга и файлы": "book" };
    const lines = [];
    let fields = null;
    for (const [tab, titles] of Object.entries(want)) {
      await page.locator(`.tabs .tab:has-text("${tab}")`).first().click();
      await page.waitForTimeout(400);
      const cards = await page.evaluate(() => [...document
        .querySelectorAll(".settings-cards .review-card-title")]
        .map((x) => x.textContent.trim()));
      for (const t of titles)
        if (!cards.includes(t))
          problems.push(`настройки «${tab}»: нет карточки «${t}» (${cards.join("/")})`);
      for (const t of oldNames)
        if (cards.some((c) => c.includes(t)))
          problems.push(`настройки «${tab}»: старое название стадии`);
      if (tab === "Глоссарий") {
        fields = await page.evaluate(() => {
          const card = [...document.querySelectorAll(".settings-cards .review-card")]
            .find((c) => /Создание глоссария/.test(c.textContent));
          if (!card) return null;
          return [...card.querySelectorAll(".field")].map((f) => {
            const label = (f.querySelector(".field-label") || {}).textContent || "";
            const ctl = f.querySelector("input,select,textarea");
            const kind = ctl ? ctl.tagName.toLowerCase() : "?";
            const opts = kind === "select" ? ctl.options.length : 0;
            const tip = (f.querySelector(".field-help") || {}).textContent || "";
            return `${label.trim()}=${kind}${opts ? `(${opts})` : ""}` +
              (ctl && ctl.value ? `:${ctl.value}` : "") +
              (/СИМВОЛ|ТОКЕН/.test(label + tip) ? "·единица" : "");
          });
        });
      }
      lines.push(`«${tab}»: ${cards.length}`);
      if (SHOT)
        await page.screenshot({ path: path.join(OUT, `settings-${slugs[tab]}.png`) });
    }
    // Состав поля важен, конкретное значение — нет: оно приходит из реестра
    const mode = (fields || []).find((x) => /Не голосующие поля/.test(x));
    if (!mode) problems.push("настройки: нет поля выбора значения");
    else if (!/^select\(4\):/.test(mode.replace(/^[^=]*=/, "").replace(/·единица$/, "")))
      problems.push(`настройки: поле режима выглядит так «${mode}»`);
    const cap = (fields || []).find((x) => /Ограничение длины значения/.test(x));
    if (!cap) problems.push("настройки: нет поля потолка длины");
    else if (!/СИМВОЛ/.test(cap) || !/^input:\d+$/.test(
      cap.replace(/^[^=]*=/, "").replace(/·единица$/, "")))
      problems.push(`настройки: потолок длины выглядит так «${cap}»`);
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "settings-glossary-fields.png") });
    log(`${problems.length === before ? "✅" : "❌"} настройки: стадии  ${lines.join(" · ")} | ${mode || "—"} | ${cap || "—"}`);
  }

  /* сброс вкладки: на каждой вкладке конфига — своя кнопка над карточками
   * (на «Внешнем виде» её нет: предпочтения браузера в реестре не живут);
   * подтверждение — словом, после сброса поле = значение по умолчанию */
  {
    const before = problems.length;
    await page.goto(`${url}/#/settings`, { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".tabs .tab", { timeout: 15000 });
    const tabNames = await page.evaluate(() => [...document
      .querySelectorAll(".tabs .tab")].map((x) => x.textContent.trim()));
    for (const tab of tabNames) {
      await page.locator(`.tabs .tab:has-text("${tab}")`).first().click();
      await page.waitForTimeout(300);
      const n = await page.locator(".settings-tab-tools button").count();
      const want = tab === "Внешний вид" ? 0 : 1;
      if (n !== want)
        problems.push(`настройки «${tab}»: кнопок сброса ${n}, ждали ${want}`);
    }
    // «Перевод»: правим поле, ждём автосохранения, сбрасываем вкладку
    await page.locator('.tabs .tab:has-text("Перевод")').first().click();
    await page.waitForTimeout(300);
    const sizeSel = '.settings-cards [name="PIPELINE_CHUNK_SIZE"]';
    const def = await page.locator(sizeSel).first().inputValue();
    await page.locator(sizeSel).first().click();
    await page.locator(sizeSel).first().fill("4321");
    await page.waitForTimeout(900);
    await page.locator(".settings-tab-tools button").first().click();
    await page.waitForTimeout(600);
    const word = page.locator(".modal-backdrop input.input");
    if (!(await word.count())) {
      problems.push("настройки: модалка сброса вкладки не открылась");
    } else {
      const mtext = await page.evaluate(() =>
        (document.querySelector(".modal-backdrop .modal-text") || {})
          .textContent?.trim() || "");
      if (!/Перевод/.test(mtext))
        problems.push(`настройки: в подтверждении не названа вкладка «${mtext}»`);
      if (SHOT) {
        await page.locator(".modal-backdrop .modal").first()
          .screenshot({ path: path.join(OUT, "settings-reset-modal.png") });
      }
      await word.first().fill("СБРОСИТЬ");
      await page.locator('.modal-backdrop button:has-text("Подтвердить")')
        .first().click();
      await page.waitForTimeout(900);
      const after = await page.locator(sizeSel).first().inputValue();
      if (after !== def)
        problems.push(`настройки: после сброса вкладки «${after}», ждали «${def}»`);
      const bad = await page.evaluate(() =>
        (document.querySelector(".page .form-error") || {})
          .textContent?.trim() || "");
      if (bad) problems.push(`настройки: сброс вкладки дал ошибку «${bad}»`);
    }
    if (SHOT) {
      await page.screenshot({ path: path.join(OUT, "settings-reset.png") });
    }
    log(`${problems.length === before ? "✅" : "❌"} настройки: сброс вкладки  ${tabNames.length} вкладок`);
  }

  /* проверка LLM-сервера: карточка «Подключение» спрашивает /v1/models тем же
   * путём, каким пойдёт работа; вердикт считается по значениям ФОРМЫ (в пробе
   * окружение чистое, поэтому HOST из поля и есть проверяемый сервер) */
  {
    const before = problems.length;
    let models = ["probe-model", "other/model"];
    const llm = http.createServer((req, res) => {
      if (!req.url.startsWith("/v1/models")) {
        res.writeHead(404);
        res.end("{}");
        return;
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ data: models.map((id) => ({ id })) }));
    });
    await new Promise((r) => llm.listen(0, "127.0.0.1", r));
    const llmPort = llm.address().port;
    const hostSel = ".settings-cards [name=\"HOST\"]";
    const btn = page.locator(".settings-check button").first();
    const state = page.locator(".settings-check-state").first();
    const verdict = async () =>
      `${(await state.getAttribute("class")).replace(/.*\bis-/, "")} «${((await state.textContent()) || "").trim()}»`;
    try {
      await page.goto(`${url}/#/settings`, { waitUntil: "load" });
      await page.reload({ waitUntil: "load" });
      // вкладка помнится браузеру: без переключения здесь лежала бы «Книга»
      await page.waitForSelector('.tabs .tab:has-text("Модель и сервер")', { timeout: 15000 });
      await page.locator('.tabs .tab:has-text("Модель и сервер")').first().click();
      await page.waitForSelector(hostSel, { timeout: 15000 });
      const seen = [];
      // 1) сервер жив, модель из конфига в списке
      await page.locator(hostSel).first().fill(`http://127.0.0.1:${llmPort}/v1`);
      if (!(await btn.textContent()).includes("Проверить"))
        problems.push("проверка сервера: кнопки нет в карточке «Подключение»");
      await btn.click();
      await page.waitForSelector(".settings-check-state.is-ok", { timeout: 15000 });
      seen.push(await verdict());
      if (!/моделей: 2/.test(seen[0]) || !/модель из конфига есть/.test(seen[0]))
        problems.push(`проверка сервера: вердикт «${seen[0]}»`);
      // 2) сервер жив, модели из конфига нет — отдельный вердикт
      models = ["other/model"];
      await btn.click();
      await page.waitForSelector(".settings-check-state.is-warn", { timeout: 15000 });
      seen.push(await verdict());
      if (!/МОДЕЛИ ИЗ КОНФИГА В СПИСКЕ НЕТ/.test(seen[1]))
        problems.push(`проверка сервера: вердикт «${seen[1]}»`);
      // 3) сервер недоступен — не таймаут, а внятный текст
      await page.locator(hostSel).first().fill("http://127.0.0.1:9/v1");
      await btn.click();
      await page.waitForSelector(".settings-check-state.is-bad", { timeout: 25000 });
      seen.push(await verdict());
      if (!/не отвечает/.test(seen[2]) || !/соединение не установлено/.test(seen[2]))
        problems.push(`проверка сервера: вердикт «${seen[2]}»`);
      if (SHOT)
        await page.screenshot({ path: path.join(OUT, "settings-check.png") });
      log(`${problems.length === before ? "✅" : "❌"} проверка LLM-сервера  ${seen.join(" | ")}`);
    } finally {
      llm.close();
    }
  }

  /* профили LLM: General — значения общего конфига, карточка с одним списком
   * профилей, создание/переименование/удаление и сохранение значатся в
   * llm_profiles.json рядом с общим .env; имя и удаление — те же системные
   * модалки, что у файлов, и они жмутся Enter'ом: мышь в сценарии не нужна */
  {
    const before = problems.length;
    const PF = () => path.join(seedDir || "", "llm_profiles.json");
    /* список профилей и выбранный в нём пункт */
    const chips = () => page.evaluate(() => [...document
      .querySelectorAll(".settings-profile-select option")]
      .map((x) => x.textContent.trim()));
    const chosen = () => page.evaluate(() => {
      const sel = document.querySelector(".settings-profile-select");
      const opt = sel && sel.options[sel.selectedIndex];
      return opt ? opt.textContent.trim() : "";
    });
    const status = () => page.evaluate(() => ((document
      .querySelector(".settings-save-state") || {}).textContent || "").trim());
    /* значение поля «Сервер LLM» активной карточки (name = ключ .env) */
    const fieldValue = (name) => page
      .locator(`.settings-cards [name="${name}"]`).first().inputValue();
    /* диалог имени (он же подтверждения): окно то же, что у файлов — поле
       заполнено, Enter подтверждает, окно закрывается. Возвращает заголовок
       окна и содержимое поля до ввода; stay — окно должно остаться (слово-пароль) */
    const dialogModal = async (value, opts = {}) => {
      const box = page.locator(".modal-backdrop .modal").last();
      await box.waitFor({ state: "visible", timeout: 15000 });
      const title = (await box.locator(".modal-title").first().textContent()).trim();
      const field = box.locator("input.input").first();
      const initial = await field.inputValue();
      if (SHOT && opts.shot) {
        await page.screenshot({ path: path.join(OUT, opts.shot) });
      }
      await field.fill(value);
      await field.press("Enter");
      if (opts.stay) {
        await page.waitForTimeout(400);
      } else {
        await page.waitForSelector(".modal-backdrop",
          { state: "detached", timeout: 15000 });
        await page.waitForTimeout(300); // вкладка перерисована после PUT
      }
      return { title, initial };
    };
    await page.goto(`${url}/#/settings`, { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".tabs .tab", { timeout: 15000 });
    /* предыдущий сценарий оставил «Внешний вид»: профили — на первой вкладке */
    await page.locator('.tabs .tab:has-text("Модель и сервер")').first().click();
    await page.waitForSelector(".settings-profile-select", { timeout: 15000 });
    let list = await chips();
    if (list.join("/") !== "General (общий конфиг)")
      problems.push(`профили: список (${list.join("/")})`);
    if (await status()) problems.push(`профили: статус General «${await status()}»`);
    if (await fieldValue("HOST") !== "http://127.0.0.1:9/v1")
      problems.push(`профили: General сервер «${await fieldValue("HOST")}»`);
    /* создать: окно имени то же, что у файлов, поле пустое */
    await page.locator('button:has-text("Создать профиль")').first().click();
    const created = await dialogModal("Домашний");
    if (created.title !== "Новый профиль LLM" || created.initial !== "")
      problems.push(`профили: окно создания «${created.title}» поле «${created.initial}»`);
    list = await chips();
    if (list.length !== 2 || !list[1].includes("Домашний"))
      problems.push(`профили: после создания (${list.join("/")})`);
    if (!(await chosen()).includes("Домашний"))
      problems.push(`профили: созданный профиль не стал активным (${await chosen()})`);
    if (seedDir) {
      if (!fs.existsSync(PF())) {
        problems.push("профили: llm_profiles.json не создан");
      } else {
        let data = {};
        try {
          data = JSON.parse(fs.readFileSync(PF(), "utf-8"));
        } catch (ex) {
          problems.push(`профили: файл не читается (${ex.message})`);
        }
        const p0 = (data.profiles || [])[0] || {};
        if (p0.name !== "Домашний" || p0.id !== "p1")
          problems.push(`профили: файл ${JSON.stringify(p0)}`);
        if (!("values" in p0) || !("created" in p0) || !("updated" in p0))
          problems.push(`профили: ключи записи ${Object.keys(p0).join(",")}`);
      }
    }
    /* редактируем профиль: карточки LLM показывают его значения; новый профиль
     * создан копией General, поэтому пустых полей LLM в форме нет (секрет —
     * под маской); карточки веб-сервера на экран не выводятся вовсе */
    if (await status())
      problems.push(`профили: статус страницы вернулся «${await status()}»`);
    if (await page.locator('.settings-cards [name="WEB_PORT"]').count())
      problems.push("профили: блок веб-сервера вернулся на страницу");
    if (await fieldValue("HOST") !== "http://127.0.0.1:9/v1")
      problems.push(`профили: новый профиль не копирует сервер (${await fieldValue("HOST")})`);
    if (await fieldValue("THREADS") === "")
      problems.push("профили: новый профиль не копирует потоки");
    if (await fieldValue("API_KEY") !== "•".repeat("probe-secret".length))
      problems.push(`профили: секрет профиля показан не маской (${await fieldValue("API_KEY")})`);
    await page.locator('.settings-cards [name="MODEL"]').first().fill("дом-модель");
    await page.locator('.settings-cards [name="API_KEY"]').first().fill("дом-ключ");
    await page.waitForTimeout(1200); // автосохранение: пауза ввода + PUT
    if (seedDir) {
      let data = {};
      try {
        data = JSON.parse(fs.readFileSync(PF(), "utf-8"));
      } catch (ex) {
        problems.push(`профили: файл не читается (${ex.message})`);
      }
      const v = ((data.profiles || [])[0] || {}).values || {};
      if (v.MODEL !== "дом-модель" || v.API_KEY !== "дом-ключ")
        problems.push(`профили: значения профиля ${JSON.stringify(v)}`);
      /* профиль — копия General: сервер в файле остаётся тем же значением */
      if (v.HOST !== "http://127.0.0.1:9/v1")
        problems.push(`профили: профиль потерял сервер General (${v.HOST})`);
      const env = fs.readFileSync(path.join(seedDir, ".env"), "utf-8");
      if (!env.includes("HOST=http://127.0.0.1:9/v1")
          || !env.includes("MODEL=probe-model")
          || !env.includes("API_KEY=probe-secret"))
        problems.push("профили: сохранение профиля тронуло общий .env");
      if (env.includes("дом-модель") || env.includes("дом-ключ"))
        problems.push("профили: значения профиля попали в общий .env");
    } else {
      log("⚠️  профили: внешний сервер — файл профилей не проверяем");
    }
    /* переименовать: та же система модалка, поле заполнено текущим именем */
    await page.locator('button:has-text("Переименовать")').first().click();
    const renamed = await dialogModal("Дачный",
      { shot: "settings-profile-rename.png" });
    if (!renamed.title.startsWith("Переименовать профиль ")
        || renamed.initial !== "Домашний")
      problems.push(`профили: окно переименования «${renamed.title}» поле «${renamed.initial}»`);
    list = await chips();
    if (!list[1] || !list[1].includes("Дачный"))
      problems.push(`профили: переименование (${list.join("/")})`);
    /* мусор: второй профиль создаём и тут же удаляем; опасное действие
       Enter'ом не проходит: окно держит слово-пароль */
    await page.locator('button:has-text("Создать профиль")').first().click();
    await dialogModal("Временный");
    if ((await chips()).length !== 3)
      problems.push(`профили: второй профиль (${(await chips()).join("/")})`);
    await page.locator('button:has-text("Удалить")').first().click();
    const gate = await dialogModal("", { stay: true });
    if (!gate.title.startsWith("Удалить профиль"))
      problems.push(`профили: окно удаления «${gate.title}»`);
    if (!(await page.locator(".modal-backdrop").count()))
      problems.push("профили: Enter без слова-пароля закрыл окно");
    const tip = (await page.locator(".modal-backdrop .form-error").first()
      .textContent()).trim();
    if (!/Введите слово УДАЛИТЬ/.test(tip))
      problems.push(`профили: Enter без слова-пароля — «${tip}»`);
    await dialogModal("УДАЛИТЬ");
    list = await chips();
    if (list.length !== 2 || !list[0].startsWith("General"))
      problems.push(`профили: после удаления (${list.join("/")})`);
    /* возврат на General: карточки снова показывают общий конфиг */
    await page.selectOption(".settings-profile-select", "general");
    await page.waitForTimeout(500);
    if (await fieldValue("MODEL") !== "probe-model")
      problems.push(`профили: General показывает «${await fieldValue("MODEL")}»`);
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "settings-profiles.png") });
    log(`${problems.length === before ? "✅" : "❌"} профили LLM  ${list.join("/")}`);
  }

  /* «Запуски»: ОДНА форма на стадию (переключателя режимов и пресетов нет),
   * поля — из реестра со значениями общего конфига; LLM-полей подключения
   * нет, вместо них одно поле «Профиль LLM» — и он выбирается СВОЕЙ стадией */
  {
    const before = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/run`,
      { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".stage-card", { timeout: 15000 });
    /* форма стадии рисуется только на открытой стадии */
    const openStage = async (text) => {
      await page.locator(`.stage-card:has-text("${text}")`).first().click();
      await page.waitForTimeout(900);
    };
    const readProfile = () => page.evaluate(() => {
      const el = document.querySelector(".run-form .field-profile");
      if (!el) return null;
      const sel = el.querySelector("select");
      /* панель открытой стадии — первая в правой колонке: у колонок
         «Активный запуск» такой же класс, берём свою */
      const stage = ((document.querySelector(".run-col-form .run-panel-title")
        || {}).textContent || "").trim().split(" ")[0];
      /* поле профиля — первым среди полей формы (строка «Главы» полем
         не считается и живёт перед ними) */
      const fields = [...document.querySelectorAll(".run-form .field")];
      return {
        idx: fields.indexOf(el),
        label: ((el.querySelector(".field-label") || {}).textContent || "").trim(),
        opts: [...((sel || {}).options || [])].map((o) => o.textContent.trim()),
        value: (sel || {}).value || "",
        note: ((el.querySelector(".run-profile-note") || {}).textContent || "").trim(),
        stage,
        stored: localStorage.getItem(`nmProfile:${"TMP"}/${"Probe"}/${stage}`) || "",
      };
    });
    await openStage("Создание глоссария");
    let pr = await readProfile();
    if (!pr) {
      problems.push("запуски: поля «Профиль LLM» в форме стадии нет");
    } else {
      if (pr.idx !== 0)
        problems.push(`запуски: профиль не первое поле формы (индекс ${pr.idx})`);
      if (pr.stage !== "ner")
        problems.push(`запуски: открыта стадия «${pr.stage}»`);
      if (pr.label !== "Профиль LLM")
        problems.push(`запуски: метка поля «${pr.label}»`);
      if (pr.opts.join("/") !== "General/Дачный")
        problems.push(`запуски: профили в поле (${pr.opts.join("/")})`);
      if (pr.value !== "general")
        problems.push(`запуски: выбран «${pr.value}»`);
      if (!/General — значения общего конфига/.test(pr.note)
          || !/probe-model/.test(pr.note))
        problems.push(`запуски: подпись профиля «${pr.note}»`);
      if (pr.stored)
        problems.push(`запуски: выбор появился сам собой (${pr.stored})`);
      /* выбор профиля стадии остаётся в браузере и не считается
         «изменённой настройкой» — сброс настроек до него не касается */
      await page.locator(".run-form .field-profile select")
        .first().selectOption({ index: 1 });
      await page.waitForTimeout(400);
      pr = await readProfile();
      if (pr.value !== "p1" || pr.stored !== "p1")
        problems.push(`запуски: выбор не запомнен (${pr.value}/${pr.stored}) `
          + `ключи: ${pr.keys}`);
      if (!/Дачный — наследует General/.test(pr.note)
          || !/дом-модель/.test(pr.note)
          || !/http:\/\/127\.0\.0\.1:9\/v1/.test(pr.note))
        problems.push(`запуски: подпись выбранного профиля «${pr.note}»`);
      if (pr.reset)
        problems.push(`запуски: профиль попал в сброс («${pr.reset}»)`);
      if (SHOT)
        await page.screenshot({ path: path.join(OUT, "run-profile.png") });
    }
    /* вторая LLM-стадия того же проекта — со своим выбором (General) */
    await openStage("Перевод (LLM)");
    const pr2 = await readProfile();
    if (!pr2) {
      problems.push("запуски: на конвейере поля «Профиль LLM» нет");
    } else if (pr2.value !== "general" || pr2.stored) {
      problems.push(`запуски: стадии наследуют чужой выбор `
        + `(${pr2.value}/${pr2.stored})`);
    }
    await openStage("Создание глоссария");
    if (await page.locator(".run-mode, .mode-btn").count())
      problems.push("запуски: переключатель «Простой/Экспертный» вернулся");
    const readForm = () =>
      page.evaluate(() => ({
        rows: [...document.querySelectorAll(".run-form .field")].map((el) => ({
          label: (el.querySelector(".field-label") || {}).textContent?.trim() || "",
          value: ((el.querySelector("input,select,textarea") || {}).value) || "",
          local: !!el.querySelector(".field-local"),
        })),
        reset: (document.querySelector(".run-reset") || {}).textContent?.trim() || "",
        tip: (document.querySelector(".run-reset") || {}).title || "",
      }));
    let form = await readForm();
    if (form.rows.some((r) => /локально/.test(r.label)))
      problems.push("запуски: пометка «локально» вернулась");
    if (/Сервер LLM|API-ключ|^Модель$/im.test(
      form.rows.map((r) => r.label).join("\n")))
      problems.push("запуски: LLM-поля вернулись в форму стадии");
    const chunk = form.rows.find((r) => /Размер чанка/.test(r.label));
    if (!chunk) problems.push(`запуски: форм нет или нет поля (${form.rows.length} строк)`);
    if (!chunk) {
      problems.push("запуски: поля стадии не отрисованы");
    } else if (chunk.value !== "8000") {
      problems.push(`запуски: «${chunk.label}» = «${chunk.value}», ждали 8000`);
    }
    if (form.reset)
      problems.push(`запуски: кнопка сброса видна без правок («${form.reset}»)`)

    // правим поле → «Сбросить настройки (1)» с тултипом «сейчас → конфиг»
    await page.locator(".run-form .field")
      .filter({ hasText: "Размер чанка" }).first()
      .locator("input,select,textarea").first().fill("1234");
    await page.waitForTimeout(1200);
    form = await readForm();
    const now = form.rows.find((r) => /Размер чанка/.test(r.label)) || {};
    if (now.value !== "1234")
      problems.push(`запуски: поле не изменилось (${now.value})`);
    if (!/^Сбросить настройки \(1\)$/.test(form.reset))
      problems.push(`запуски: кнопка «${form.reset}»`);
    if (!(form.tip.includes("1234") && form.tip.includes("8000")))
      problems.push(`запуски: тултип сброса «${form.tip}»`);
    // модалка подтверждения → поле снова значение общего конфига
    await page.locator(".run-reset").first().click();
    await page.waitForTimeout(600);
    const word = page.locator(".modal-backdrop input.input");
    if (!(await word.count())) {
      problems.push("запуски: модалка сброса не открылась");
    } else {
      const mtitle = await page.evaluate(() =>
        (document.querySelector(".modal-backdrop .modal-title") || {})
          .textContent?.trim() || "");
      await word.first().fill("СБРОСИТЬ");
      await page.locator('.modal-backdrop button:has-text("Подтвердить")')
        .first().click();
      await page.waitForTimeout(1500);
      form = await readForm();
      const after = form.rows.find((r) => /Размер чанка/.test(r.label)) || {};
      if (after.value !== "8000")
        problems.push(`запуски: после сброса «${after.value}», ждали 8000`);
      if (form.reset)
        problems.push(`запуски: после сброса кнопка «${form.reset}»`);
      if (!/^Сбросить настройки/.test(mtitle))
        problems.push(`запуски: модалка «${mtitle}»`);
    }
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "scenario-run-form.png") });

    /* ── проверка промпт-файла: скрипт стадии (--check-prompt) отвечает
       прямо из формы; SPA показывает жёлтый треугольник у «Редактировать».
       Промпт теста намеренно с одним тегом — замечание одно ── */
    {
      const before = problems.length;
      await openStage("Создание глоссария");
      await page.waitForTimeout(1500);
      const warn = page.locator(".run-prompt-warn-icon").first();
      if (!(await warn.count())) {
        problems.push("проверка промпта: иконка не появилась");
      } else {
        const tip = (await warn.getAttribute("aria-label")) || "";
        if (!/замечаний: 1/.test(tip))
          problems.push(`проверка промпта: тултип «${tip}»`);
        if (!tip.includes("<prompt_pass2>"))
          problems.push(`проверка промпта: тултип не про второй режим (${tip})`);
        if (!tip.includes("встроен"))
          problems.push(`проверка промпта: тултип не говорит про встроенный (${tip})`);
        const pf = (await page.locator(".run-form .field")
          .filter({ hasText: "Промпт-файл" }).first()
          .locator("select").first().inputValue()) || "";
        if (pf !== "ner_prompt.txt")
          problems.push(`проверка промпта: выбран «${pf}»`);
        /* вердикт — служебный файл: в книге он не остаётся */
        if (seedDir && fs.existsSync(path.join(seedDir, SECTION, BOOK, "tmp",
                                              "prompt_check.json")))
          problems.push("проверка промпта: вердикт остался в tmp/ книги");
        if (SHOT)
          await page.screenshot({
            path: path.join(OUT, "scenario-prompt-check.png"),
          });
      }
      log(`${problems.length === before ? "✅" : "❌"} проверка промпта  `
        + `--check-prompt → иконка у поля файла`);
    }

    /* ── промпта в книге нет — поле предлагает «Из шаблона»: готовый файл
       копируется из набора templates/* и сразу выбирается в поле ── */
    {
      const before = problems.length;
      const tplPrompt = path.join(seedDir || "", SECTION, BOOK, "prompts",
        "wiki_prompt.txt");
      const hadTpl = fs.existsSync(tplPrompt);
      if (hadTpl) fs.rmSync(tplPrompt);
      await page.goto(`${url}/#/project/${SECTION}/${BOOK}/run`,
        { waitUntil: "load" });
      await page.reload({ waitUntil: "load" });
      await page.waitForSelector(".stage-card", { timeout: 15000 });
      await openStage("Создание Wiki");
      const field = page.locator(".run-form .field")
        .filter({ hasText: "Промпт" }).first();
      const btn = field.locator("button").first();
      const label = ((await btn.textContent()) || "").trim();
      if (label !== "Из шаблона")
        problems.push(`промпт из шаблона: кнопка «${label}» вместо «Из шаблона»`);
      await btn.click();
      await page.waitForTimeout(800);
      const setOpts = await page.evaluate(() => {
        const sels = [...document.querySelectorAll(".modal-backdrop select")];
        return sels.map((s) => ({
          value: s.value,
          opts: [...s.options].map((o) => o.value),
        }));
      });
      if (setOpts.length !== 2) {
        problems.push(`промпт из шаблона: полей выбора ${setOpts.length}`);
      } else if (setOpts[1].value !== "wiki_prompt.txt") {
        problems.push(`промпт из шаблона: выбран «${setOpts[1].value}»`);
      }
      await page.locator('.modal-backdrop button:has-text("Скопировать")')
        .first().click();
      await page.waitForTimeout(1500);
      if (!fs.existsSync(tplPrompt))
        problems.push("промпт из шаблона: файл в книгу не попал");
      const chosen = await page.evaluate(() => {
        const row = [...document.querySelectorAll(".run-form .field")]
          .find((el) => /Промпт/.test(((el.querySelector(".field-label") || {})
            .textContent) || ""));
        const sel = row && row.querySelector("select");
        return sel ? sel.value : "";
      });
      if (chosen !== "wiki_prompt.txt")
        problems.push(`промпт из шаблона: поле выбрало «${chosen}»`);
      if (SHOT)
        await page.screenshot({
          path: path.join(OUT, "scenario-prompt-template.png"),
        });
      log(`${problems.length === before ? "✅" : "❌"} промпт из шаблона  `
        + `пустое поле → шаблон → файл в книге`);
    }

    // вкладка «Настройки» проекта — данные книги, .env-редактора нет
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/config`,
      { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForTimeout(1200);
    const cfg = await page.evaluate(() => document.getElementById("app").innerHTML);
    if (/Файл \.env|Сохранить \.env/.test(cfg))
      problems.push("настройки книги: вкладка по-прежнему редактирует .env");
    if (!/Обложка/.test(cfg))
      problems.push("настройки книги: не осталось карточек");
    log(`${problems.length === before ? "✅" : "❌"} запуски (одна форма)  ${(chunk || {}).label || ""}=${(chunk || {}).value || "?"}`);
  }

  /* Поля режима стадии: одно правило в реестре (when/when_any/when_set)
   * решает, едет ли поле в argv и показывать ли его. Здесь — живой DOM:
   * переключаем режим стадии и проверяем, что поля чужого режима скрыты,
   * а своего — видны (строка «Главы» — тоже поле режима) */
  {
    const before = problems.length;
    const CASES = [
      { stage: "Перевод (LLM)", pick: ["Тип работы", "1"],
        show: ["Размер чанка", "Мин. count для глоссария", "Главы:"],
        hide: ["Словарь перевода", "Мин. count для имён"] },
      // диапазон глав у конвейера не зависит от режима — он всегда виден
      { stage: "Перевод (LLM)", pick: ["Тип работы", "3"],
        show: ["Мин. count для имён", "Главы:"],
        hide: ["Мин. count для глоссария", "Словарь перевода"] },
      { stage: "Перевод (LLM)", pick: ["Тип работы", "9"],
        show: ["Словарь перевода", "Макс. примеров на чанк",
               "Мин. count для глоссария"],
        hide: ["Мин. count для имён"] },
      { stage: "Проверка глоссария", pick: ["Режимы", "whole"],
        show: ["Бюджет запроса", "Порог count"],
        hide: ["RAG: список терминов", "Главы:"] },
      { stage: "Проверка глоссария", pick: ["Режимы", "rag"],
        show: ["RAG: список терминов", "Главы:"],
        hide: ["Бюджет запроса", "Порог count"] },
      { stage: "Разбор исходника", pick: ["Режим разбивки", "toc"],
        show: ["Переопределить названия"],
        hide: ["Паттерны разбивки", "Размер чанка", "Маска названия"] },
      { stage: "Разбор исходника", pick: ["Режим разбивки", "regex"],
        show: ["Паттерны разбивки"],
        hide: ["Размер чанка", "Маска названия"] },
      { stage: "Разбор исходника", pick: ["Режим разбивки", "chunk"],
        show: ["Размер чанка", "Маска названия", "Переопределить названия"],
        hide: ["Паттерны разбивки"] },
      { stage: "Компиляция", pick: ["Режим", "txt"],
        show: [], hide: ["Обложка", "Метаданные", "страницы поддержки"] },
      { stage: "Компиляция", pick: ["Режим", "epub"],
        show: ["Обложка", "Метаданные", "страницы поддержки"] },
      /* wiki: тип файлов ОДИН на вход и на вики-главу — в форме он живёт
         в обоих режимах, где нужен, и пропадает в чистом txt-режиме */
      { stage: "Создание Wiki", pick: ["Источник текста", "txt"],
        show: ["Входной txt новеллы"], hide: ["Тип файлов", "Главы:"] },
      { stage: "Создание Wiki", pick: ["Источник текста", "chapters"],
        show: ["Тип файлов", "Главы:"], hide: ["Входной txt новеллы"] },
      { stage: "Создание Wiki", check: ["Сохранить как главу", true],
        show: ["Тип файлов"], hide: ["Формат", "Выходной файл", "Оглавление"] },
    ];
    const readFields = () =>
      page.evaluate(() => {
        const rows = [...document.querySelectorAll(".run-form .field")]
          .map((el) => ({
            label: ((el.querySelector(".field-label") || {}).textContent || "")
              .replace(/\s+/g, " ").trim(),
            hidden: el.classList.contains("hidden"),
          }));
        // строка «Главы: [start] – [end]» — не .field, но тоже поле режима
        const range = document.querySelector(".run-form .run-range-row");
        if (range) {
          rows.push({ label: "Главы:",
            hidden: range.classList.contains("hidden") });
        }
        return rows;
      });
    const seen = [];
    for (const c of CASES) {
      await page.goto(`${url}/#/project/${SECTION}/${BOOK}/run`,
        { waitUntil: "load" });
      await page.reload({ waitUntil: "load" });
      await page.waitForSelector(".stage-card", { timeout: 15000 });
      await page.locator(`.stage-card:has-text("${c.stage}")`).first().click();
      await page.waitForTimeout(900);
      const ctl = page.locator(".run-form .field")
        .filter({ hasText: (c.pick || c.check)[0] }).first();
      if (c.pick) {
        await ctl.locator("select").first().selectOption({ value: c.pick[1] });
      } else if (c.check[1]) {
        await ctl.locator('input[type="checkbox"]').first().check();
      } else {
        await ctl.locator('input[type="checkbox"]').first().uncheck();
      }
      await page.waitForTimeout(500);
      const rows = await readFields();
      const on = (needle) => rows.some((r) => !r.hidden
        && r.label.includes(needle));
      const any = (needle) => rows.some((r) => r.label.includes(needle));
      const want = (c.show || []).concat(c.hide || []);
      for (const n of want) {
        if (!any(n)) problems.push(`${c.stage}: поля «${n}» в форме нет`);
      }
      for (const n of c.show || []) {
        if (!on(n)) problems.push(`${c.stage}: «${n}» скрыт в этом режиме`);
      }
      for (const n of c.hide || []) {
        if (on(n)) problems.push(`${c.stage}: «${n}» виден в этом режиме`);
      }
      seen.push(`${c.stage.split(" (")[0]}:${(c.pick || c.check)[1]}`);
    }
    if (SHOT) {
      await page.screenshot({ path: path.join(OUT, "scenario-mode-fields.png") });
    }
    log(`${problems.length === before ? "✅" : "❌"} поля режима  ${seen.length} режимов · ${problems.length === before ? "все совпали" : "расхождения"}`);
  }

  /* «Главы»: диапазон с концом меньше начала подписан (тут перестановка
   * полей остаётся — ею и набирают), а последняя глава книги — потолок */
  {
    const before = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/chapters`,
      { waitUntil: "load" });
    await page.waitForSelector(".chapters-toolbar .run-range", { timeout: 15000 });
    await page.waitForTimeout(500);
    const inputs = page.locator(".chapters-toolbar .run-range");
    await inputs.nth(0).fill("2");
    await inputs.nth(1).fill("1");
    await page.waitForTimeout(400);
    const note = await page.evaluate(() => {
      const n = document.querySelector(".chapters-toolbar .run-range-note");
      return { text: ((n || {}).textContent || "").trim(),
               hidden: !n || n.classList.contains("hidden") };
    });
    if (note.hidden || !/больше конечной/.test(note.text)) {
      problems.push(`главы: перепутанный диапазон не подписан («${note.text}»)`);
    }
    await inputs.nth(1).fill("99");
    await page.waitForTimeout(400);
    const over = await page.evaluate(() =>
      ((document.querySelector(".chapters-toolbar .run-range-note") || {})
        .textContent || "").trim());
    if (!/в книге всего/.test(over)) {
      problems.push(`главы: глава за последней не поймана («${over}»)`);
    }
    if (SHOT) {
      await page.screenshot({ path: path.join(OUT, "scenario-chapters-range.png") });
    }
    /* вернуть чистый диапазон: вкладка помнит поля в памяти книги */
    await inputs.nth(0).fill("1");
    await inputs.nth(1).fill("2");
    await page.waitForTimeout(400);
    log(`${problems.length === before ? "✅" : "❌"} главы: диапазон  `
      + `перепутан — «${note.text}» · за книгой — «${over}»`);
  }

  /* «Разбор исходника»: epub из source/ подставляется сам; список селекта
   * строится по ВСЕМУ пулу source/ и фильтруется режимом (txt обязан
   * ВЕРНУТЬСЯ после смены режима); чекбокс предварительной очистки текста —
   * в форме. Локальные значения запусков живут в браузере книги, поэтому
   * режим сценарий выставляет сам, а не надеется на дефолт. */
  {
    const before = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/run`,
      { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".stage-card", { timeout: 15000 });
    await page.locator('.stage-card:has-text("Разбор исходника")').first().click();
    await page.waitForTimeout(900);
    const readInput = () => page.evaluate(() => {
      const label = (el) => ((el.querySelector(".field-label") || {})
        .textContent || "").replace(/\s+/g, " ").trim();
      const rows = [...document.querySelectorAll(".run-form .field")];
      const input = rows.find((el) => label(el).includes("Исходник"));
      const clean = rows.find((el) => label(el).includes("Предварительная"));
      const sel = input && input.querySelector("select");
      const cb = clean && clean.querySelector('input[type="checkbox"]');
      return {
        value: (sel || {}).value || "",
        opts: [...(((sel || {}).options) || [])].map((o) => o.value),
        clean: !!clean,
        cleanOn: !!(cb && cb.checked),
      };
    });
    const setMode = async (m) => {
      await page.locator(".run-form .field")
        .filter({ hasText: "Режим разбивки" }).first()
        .locator("select").first().selectOption({ value: m });
      await page.waitForTimeout(500);
      return readInput();
    };
    const toc = await setMode("toc");
    if (toc.value !== "book.epub") {
      problems.push(`epub: исходник не подхвачен: «${toc.value}» `
        + `из ${JSON.stringify(toc.opts)}`);
    }
    if (toc.opts.includes("notes.txt")) {
      problems.push(`epub: в toc-режиме txt-исходник в списке `
        + `${JSON.stringify(toc.opts)}`);
    }
    if (!toc.clean) problems.push("epub: чекбокса предварительной очистки нет");
    const re = await setMode("regex");
    if (!re.opts.includes("notes.txt")) {
      problems.push(`epub: в regex-режиме txt пропал из списка `
        + `${JSON.stringify(re.opts)}`);
    }
    if (re.value !== toc.value) {
      problems.push(`epub: выбор исходника потерян: «${re.value}»`);
    }
    if (SHOT) {
      await page.screenshot({ path: path.join(OUT, "scenario-epub-input.png") });
    }
    log(`${problems.length === before ? "✅" : "❌"} разбор исходника  подхват «${toc.value}» `
      + `· toc: ${toc.opts.length} позиций · regex: ${re.opts.length} позиций `
      + `· очистка текста ${toc.clean ? (toc.cleanOn ? "вкл" : "выкл") : "нет"}`);
  }

  /* Диапазон глав: «64 – 3» не запускается — подсказка и рамка видны сразу,
   * кнопка отказывает, а сервер отвечает 400 тем же текстом (правило одно —
   * core.settings.chapter_range_error / UICore.chapterRangeError) */
  {
    const before = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/run`,
      { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".stage-card", { timeout: 15000 });
    await page.locator('.stage-card:has-text("Проверка перевода (LLM)")')
      .first().click();
    await page.waitForTimeout(900);
    const inputs = page.locator(".run-form .run-range-row input");
    await inputs.nth(0).fill("64");
    await inputs.nth(1).fill("3");
    await page.waitForTimeout(400);
    const bad = await page.evaluate(() => {
      const note = document.querySelector(".run-form .run-range-note");
      const fields = [...document.querySelectorAll(".run-form .run-range")];
      return {
        text: ((note || {}).textContent || "").trim(),
        hidden: !note || note.classList.contains("hidden"),
        bad: fields.filter((f) => f.classList.contains("input-bad")).length,
      };
    });
    if (bad.hidden || !/больше конечной/.test(bad.text)) {
      problems.push(`диапазон: нет подсказки о порядке («${bad.text}»)`);
    }
    if (bad.bad !== 2) {
      problems.push(`диапазон: помечено полей ${bad.bad}, а не оба`);
    }
    if (SHOT) {
      await page.locator(".run-form").first().screenshot(
        { path: path.join(OUT, "scenario-range-bad.png") });
    }
    /* кнопка запуска такой диапазон не отправляет */
    await page.locator('.run-form button:has-text("Запустить")').first()
      .click();
    await page.waitForTimeout(500);
    const err = await page.evaluate(() =>
      ((document.querySelector(".run-form .form-error") || {}).textContent
        || "").trim());
    const started = await page.evaluate(() =>
      !!document.querySelector(".active-run"));
    if (!/больше конечной/.test(err)) {
      problems.push(`диапазон: запуск не объяснил отказ («${err}»)`);
    }
    if (started) problems.push("диапазон: 64 – 3 всё-таки ушёл в запуск");
    log(`${problems.length === before ? "✅" : "❌"} диапазон глав  `
      + `64 – 3: подсказка + ${bad.bad} поля в рамке · отказ «${err}»`);
  }

  /* Отчёт translate_check: шкала допуска пары — полоса при текущем K,
   * штрих медианы и точки глав вне допуска. Числа берутся из блока
   * «Статистика» (сервер разбирает его в r.stats): без них вьюха падала бы
   * молча, и человек не увидел бы, что значит «допуск ±9.9%» */
  {
    const before = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/review`,
      { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".subtabs .subtab", { timeout: 15000 });
    await page.locator('.subtabs .subtab:text-is("Перевод")').first().click();
    await page.waitForSelector(".check-scale", { timeout: 15000 });
    const sc = await page.evaluate(() =>
      [...document.querySelectorAll(".check-scale")].map((el) => ({
        title: (el.querySelector(".check-scale-title") || {}).textContent?.trim() || "",
        nums: (el.querySelector(".check-scale-nums") || {}).textContent?.trim() || "",
        band: !!el.querySelector(".check-scale-band"),
        mark: !!el.querySelector(".check-scale-mark"),
        dots: [...el.querySelectorAll(".check-scale-dot")].map((d) => ({
          out: d.classList.contains("is-out"),
          title: d.getAttribute("title") || "",
          left: (d.getAttribute("style") || "").replace(/\s/g, ""),
        })),
        legend: (el.querySelector(".check-scale-legend") || {}).textContent?.trim() || "",
      })));
    if (sc.length !== 2) problems.push(`шкала отчёта: полос ${sc.length} (ожидаем 2)`);
    const first = sc[0] || {};
    if (first.title !== "polished vs redacted")
      problems.push(`шкала отчёта: подпись «${first.title}»`);
    if (!/допуск 0\.90–1\.10 при K=25/.test(first.nums || ""))
      problems.push(`шкала отчёта: числа «${first.nums}»`);
    if (!first.band || !first.mark) problems.push("шкала отчёта: нет полосы или медианы");
    if ((first.dots || []).length !== 1)
      problems.push(`шкала отчёта: точек ${(first.dots || []).length} (ожидаем 1)`);
    const dot = (first.dots || [])[0] || {};
    /* точка лежит на нижней границе допуска — угол по медиане домена:
       0.90 при 0.999 и ±0.10 значит «дальше половины шкалы», а не у края */
    if (!/left:\d+(\.\d+)?%/.test(dot.left || ""))
      problems.push(`шкала отчёта: позиция точки «${dot.left}»`);
    if (!/отношение 0\.90 — вне допуска/.test(dot.title || ""))
      problems.push(`шкала отчёта: тултип точки «${dot.title}»`);
    if (!/глав в базе: 1320/.test(first.legend || ""))
      problems.push(`шкала отчёта: подпись «${first.legend}»`);
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-check-scale.png") });
    log(`${problems.length === before ? "✅" : "❌"} шкала отчёта    `
      + `${sc.length} пары · ${(first.dots || []).length} точка`);
    /* подвкладка «Проверки» запоминается книгой: следующие сценарии ждут
       глоссарий — возвращаем её, иначе полигон упадёт на пустом .rv-row */
    await page.locator('.subtabs .subtab:text-is("Глоссарий (LLM)")').first().click();
    await page.waitForTimeout(400);
  }

  /* «Проверка глоссария»: поля записи, уходящие LLM. term, type и
   * translation — всегда (чекбоксы неотключаемые, как в {ner_block}
   * конвейера); остальные — ключи реального ner.json пробы */
  {
    const before = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/run`,
      { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".stage-card", { timeout: 15000 });
    await page.locator('.stage-card:has-text("Проверка глоссария")').first().click();
    await page.waitForTimeout(900); // чипсы рисуются после GET /api/ner
    const f = await page.evaluate(() => {
      // первый .ner-chips-box — чипсы типов, второй — чипсы полей записи
      const boxes = [...document.querySelectorAll(".run-form .ner-chips-box")];
      const bar = document.querySelectorAll(".run-form .ner-chips-bar")[1];
      const chips = [...(boxes[1] || { querySelectorAll: () => [] })
        .querySelectorAll(".ner-chip")]
        .map((c) => ({
          text: (c.textContent || "").replace(/\s+/g, " ").trim(),
          off: !!(c.querySelector("input[type=checkbox]") || {}).disabled,
        }));
      return { chips, info: (bar ? bar.textContent : "").replace(/\s+/g, " ").trim() };
    });
    const want = ["term (всегда)", "type (всегда)", "translation (всегда)",
      "count"];
    const got = f.chips.map((c) => c.text);
    if (got.join("|") !== want.join("|"))
      problems.push(`поля ner_check: чипсы «${got.join(", ")}»`);
    if (f.chips.slice(0, 3).some((c) => !c.off))
      problems.push("поля ner_check: term/type/translation можно снять");
    if (f.chips.length > 3 && f.chips[3].off)
      problems.push(`поле «${want[3]}» оказалось неотключаемым`);
    if (!/term, type, translation — всегда; по умолчанию: notes, context/
      .test(f.info)) problems.push(`поля ner_check: подпись «${f.info}»`);
    if (SHOT) {
      await page.locator(".run-form").first().screenshot(
        { path: path.join(OUT, "scenario-ner-check-fields.png") });
    }
    log(`${problems.length === before ? "✅" : "❌"} поля ner_check  ${got.join(" · ")}`);
  }

  /* Состояние вкладок книги: перезагрузка возвращает на ту же вкладку,
   * «Редактор» — на ту же главу, «Логи» — в ту же папку и на тот же файл
   * (localStorage браузера, один ключ на книгу) */
  {
    const before = problems.length;
    const bare = `${url}/#/project/${SECTION}/${BOOK}`;
    const activeTab = () => page.evaluate(() => ((document.querySelector(".tab-active") || {}).textContent || "").trim());
    await page.goto(`${bare}/editor`, { waitUntil: "load" });
    await page.waitForSelector(".ed-chapter", { timeout: 15000 });
    const first = await page.evaluate(() => (document.querySelector(".ed-chapter") || {}).value || "");
    const opts = await page.evaluate(() => [...document.querySelectorAll(".ed-chapter option")].map((o) => o.value));
    if (opts.length < 2) problems.push("на полигоне меньше двух глав — память редактора не проверить");
    const second = opts.find((v) => v !== first) || first;
    await page.locator(".ed-chapter").selectOption({ value: second });
    await page.waitForTimeout(700);
    await page.goto(bare, { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".ed-chapter", { timeout: 15000 });
    const tab1 = await activeTab();
    const back = await page.evaluate(() => (document.querySelector(".ed-chapter") || {}).value || "");
    if (tab1 !== "Редактор") problems.push(`ссылка без вкладки открыла «${tab1}»`);
    if (back !== second) problems.push(`редактор вернулся на главу «${back}»`);
    /* логи: подпапка и открытый файл тоже помнятся. Помнящаяся папка могла
     * открыть подпапку ещё на прошлом сценарии — начинаем с корня logs/ */
    await page.goto(`${bare}/logs`, { waitUntil: "load" });
    await page.waitForSelector(".crumbs .crumb", { timeout: 15000 });
    await page.locator(".crumbs .crumb").first().click();
    await page.waitForTimeout(700);
    await page.locator('.prompt-item:has-text("chapters/")').first().click();
    await page.waitForTimeout(700);
    await page.locator('.prompt-item:has-text("00000_1_Глава 1.log")').first().click();
    await page.waitForTimeout(700);
    await page.goto(bare, { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".log-view", { timeout: 15000 });
    await page.waitForTimeout(900); // содержимое лога догружается отдельным запросом
    const tab2 = await activeTab();
    const crumbs = await page.evaluate(() => (document.querySelector(".crumbs") || {}).textContent || "");
    const viewed = await page.evaluate(() => (document.querySelector(".log-view") || {}).textContent || "");
    if (tab2 !== "Логи") problems.push(`ссылка без вкладки открыла «${tab2}»`);
    if (!crumbs.includes("chapters")) problems.push(`логи вернулись из корня: «${crumbs.trim()}»`);
    if (!viewed.includes("глава: ок")) problems.push("содержимое лога не вернулось");
    if (SHOT) {
      await page.screenshot({ path: path.join(OUT, "scenario-tab-state.png") });
    }
    log(`${problems.length === before ? "✅" : "❌"} состояние вкладок  редактор ${first} → ${back} · логи: chapters/`);
  }

  /* «Оценка перевода»: единая форма стадии — режим оценки и чанковые поля;
   * в режиме чанков строка «Главы» перестаёт обрезаться по бюджету (диапазон
   * там — вся книга), а предпросмотр показывает план и ОБА запроса прогона:
   * оценку чанка и свёртку отчётов */
  {
    const before = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/run`,
      { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".stage-card", { timeout: 15000 });
    await page.locator('.stage-card:has-text("Оценка перевода")').first().click();
    await page.waitForTimeout(900);
    const qread = () => page.evaluate(() => ({
      rows: [...document.querySelectorAll(".run-form .field")].map((el) => {
        const c = el.querySelector("input,select,textarea") || {};
        return {
          label: (el.querySelector(".field-label") || {}).textContent?.trim() || "",
          value: c.value || "",
          opts: [...(c.options || [])].map((o) => o.textContent.trim()),
        };
      }),
      end: ((document.querySelectorAll(".run-range")[1] || {}).title) || "",
    }));
    const row = (st, text) => st.rows.find((r) => r.label.includes(text)) || {};
    let q = await qread();
    for (const t of ["Тип файлов глав", "Промпт-файл", "Режим оценки",
      "Глав в чанке", "Чанков оценить", "Отбор чанков", "Перекрытие чанков",
      "Бюджет запроса"])
      if (!row(q, t).label) problems.push(`оценка: в форме нет «${t}»`);
    const mode = row(q, "Режим оценки");
    if (mode.opts.join("/") !== "Диапазон одним запросом/Чанками всей книги")
      problems.push(`оценка: режим «${mode.opts.join("/")}»`);
    if (!/бюджет обрежет|в бюджет влезает/.test(q.end))
      problems.push(`оценка: подсказка end «${q.end}»`);
    /* режим чанков: бюджет режет чанки, а не конец диапазона */
    await page.locator(".run-form .field").filter({ hasText: "Режим оценки" })
      .first().locator("select").first().selectOption("chunks");
    await page.waitForTimeout(700);
    q = await qread();
    if (!/чанки целиком/.test(q.end))
      problems.push(`оценка: end в чанках «${q.end}»`);
    const vals = ["Глав в чанке", "Чанков оценить", "Перекрытие чанков",
      "Бюджет запроса"].map((t) => row(q, t).value || "?").join("|");
    // бюджет — из реестра, остальные три — числа формы (1 / 0 / 0)
    if (!/^1\|0\|0\|\d{4,}$/.test(vals)) problems.push(`оценка: значения ${vals}`);
    if (row(q, "Отбор чанков").opts.join("/")
        !== "Равномерно по книге/Первые по порядку")
      problems.push(`оценка: отбор «${row(q, "Отбор чанков").opts.join("/")}»`);
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "scenario-quality-chunks.png") });
    /* предпросмотр запроса: синхронно, без сети — план + чанк + свёртка */
    await page.locator('button:has-text("Предпросмотр запроса")').first().click();
    let pv = { head: [], labels: [], texts: [] };
    try {
      await page.waitForSelector(".preview-req-msg", { timeout: 60000 });
      pv = await page.evaluate(() => ({
        head: [...document.querySelectorAll(".preview-req-head")]
          .map((e) => e.textContent.trim()),
        labels: [...document.querySelectorAll(".preview-req-msg > .preview-req-role")]
          .map((e) => e.textContent.trim()),
        texts: [...document.querySelectorAll(".preview-req-text")]
          .map((e) => e.textContent),
      }));
    } catch {
      problems.push("оценка: модалка предпросмотра не открылась");
    }
    if (pv.head.length === 2) {
      if (!/запросов: 2/.test(pv.head[0]) || !pv.head[0].includes("probe-model"))
        problems.push(`оценка: шапка «${pv.head[0]}»`);
      for (const bit of ["чанков: 2", "оценивается: 2", "отбор: все",
        "глав в чанке: 1", "перекрытие: 0", "потоки: 4", "разрезано глав: 0",
        "не влезли даже частью: нет", "артефакты: tmp/quality"])
        if (!pv.head[1].includes(bit)) problems.push(`оценка: план «${bit}»`);
    } else {
      problems.push(`оценка: строк шапки ${pv.head.length}`);
    }
    if (pv.labels.length !== 2 || !pv.labels[0].includes("Оценка · чанк 1/2")
        || !pv.labels[1].includes("Свёртка отчётов → заключение"))
      problems.push(`оценка: запросы ${pv.labels.join(" || ")}`);
    // статистика и состав каждого запроса: символы, токены, свои meta
    if (!/главы: 1 · размер: \d+ токенов/.test(pv.labels[0] || ""))
      problems.push(`оценка: запрос чанка описан как «${pv.labels[0]}»`);
    if (!/сводок: 2 · уровней: 1/.test(pv.labels[1] || ""))
      problems.push(`оценка: запрос свёртки описан как «${pv.labels[1]}»`);
    for (const l of pv.labels)
      if (!/символов: user \d+, system \d+ \(всего \d+\)/.test(l)
          || !/токенов ~\d+/.test(l)) problems.push(`оценка: статистика «${l}»`);
    // у каждого запроса свои system и user: 4 <pre>, порядок — system, user
    const all = pv.texts.join("\n");
    if (!all.includes("## ОРИГИНАЛ") || !all.includes("## ПЕРЕВОД"))
      problems.push("оценка: запрос чанка без разметки оригинала и перевода");
    if (!all.includes("Он сказал") || !all.includes("Вторая строка"))
      problems.push("оценка: в запросе чанка нет текстов глав");
    if (!all.includes("<<<QUALITY>>>"))
      problems.push("оценка: запрос чанка без хвоста QUALITY");
    if (all.includes("{original_text}") || all.includes("{translated_text}")
        || all.includes("{batch_text}"))
      problems.push("оценка: плейсхолдер промпта дошёл до запроса");
    if (!all.includes("### глава 1") || !all.includes("общий балл: 8.5")
        || !all.includes("точность 9") || !all.includes("### глава 2")
        || !all.includes("(здесь сводка чанка)"))
      problems.push("оценка: в запросе свёртки нет сводок чанков");
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "scenario-quality-preview.png") });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    log(`${problems.length === before ? "✅" : "❌"} оценка чанками  ${(pv.head || [])[1] || "?"}`);
  }

  /* Индикатор запуска в шапке: вкладки проекта меняются состоянием, а хэш
   * остаётся тем, чем в проект вошли («.../run»), — ссылка pill'а вела на тот
   * же хэш, hashchange не случался и клик проходил мимо. Настоящий запуск не
   * нужен: ответ /api/jobs/active подменяем заглушкой */
  {
    const before = problems.length;
    await page.route("**/api/jobs/active", (r) =>
      r.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          jobs: [{
            id: "probe-job", status: "running", title: "Перевод (LLM)",
            project: `${SECTION}/${BOOK}`, progress: { done: 2, total: 3 },
          }],
        }),
      }),
    );
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/run`, { waitUntil: "load" });
    await page.waitForSelector(".tabs .tab-active", { timeout: 15000 });
    // уходим на «Статус»: вкладка меняется состоянием, хэш остаётся «.../run»
    await page.locator(".tabs button", { hasText: "Статус" }).first().click();
    await page.waitForTimeout(600);
    await page.evaluate(() => window.updateRunPill());
    await page.waitForTimeout(700);
    const read = () => page.evaluate(() => ({
      tab: (document.querySelector(".tabs .tab-active") || {}).textContent || "",
      hash: decodeURIComponent(location.hash),
      pill: !!document.querySelector(".run-pill"),
      count: (document.querySelector(".run-pill-count") || {}).textContent || "",
    }));
    const mid = await read();
    if (SHOT) await page.screenshot({ path: path.join(OUT, "run-pill.png") });
    await page.locator(".run-pill").click();
    await page.waitForTimeout(900);
    const after = await read();
    await page.unroute("**/api/jobs/active");
    if (SHOT) await page.screenshot({ path: path.join(OUT, "run-pill-after.png") });
    if (!mid.pill) problems.push("run-pill: индикатор не отрисован");
    if (mid.tab !== "Статус") problems.push(`run-pill: до клика вкладка «${mid.tab}»`);
    if (!after.count.includes("2/3")) problems.push(`run-pill: счётчик «${after.count}»`);
    if (after.tab !== "Запуски") {
      problems.push(`run-pill: клик не переключил вкладку (активна «${after.tab}»)`);
    }
    if (!after.hash.endsWith("/run")) {
      problems.push(`run-pill: хэш изменился — ${after.hash}`);
    }
    log(`${problems.length === before ? "✅" : "❌"} run-pill клик  ${mid.tab} → ${after.tab} · ${after.count}`);
  }

  /* кириллическое имя проекта: сегменты hash-маршрута браузер хранит
   * закодированными — без декода имя книги доезжает до API дважды
   * закодированным, проект не находится, а в заголовке — крокозябры */
  {
    const before = problems.length;
    const href = `#/project/${encodeURIComponent(CYR_SECTION)}/${CYR_BOOK}/files`;
    await page.goto(`${url}/${href}`, { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForTimeout(1200);
    const look = await page.evaluate(() => ({
      title: ((document.querySelector(".page-title") || {}).textContent || "").trim(),
      sub: ((document.querySelector(".page-sub") || {}).textContent || "").trim(),
      files: document.querySelectorAll(".files-list .frow").length,
    }));
    if (look.title !== CYR_BOOK || !look.sub.includes(CYR_SECTION))
      problems.push(`кириллица: заголовок «${look.title}» · ${look.sub}`);
    if (look.files < 2)
      problems.push(`кириллица: в списке файлов ${look.files} (ожидаем 2)`);
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-cyrillic.png") });
    log(`${problems.length === before ? "✅" : "❌"} кириллица-имя   «${look.title}» (${look.sub}) · файлов ${look.files}`);
  }

  /* тема интерфейса — переключатель в шапке: доступен с любого экрана,
   * предпочтение живёт в localStorage, редактор перекрашивается на месте
   * (несохранённый текст не теряется), а карточка «Внешний вид» тему не дублирует */
  {
    const before = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/editor`, {
      waitUntil: "load",
    });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".ed-grid .cm-content", { timeout: 15000 });
    await page.waitForTimeout(1200);
    await page.locator(".ed-grid .cm-content").first().click();
    await page.keyboard.type("ТЕМА");
    const seen = [];
    for (let i = 0; i < 2; i++) {
      seen.push(
        await page.evaluate(() => {
          const b = document.querySelector(".theme-switch");
          return [
            document.documentElement.dataset.uiTheme || "",
            document.body.dataset.editorTheme || "",
            b ? b.getAttribute("aria-label") : "",
            b && b.querySelector("svg") ? "иконка" : "без иконки",
          ].join("/");
        }),
      );
      await page.click(".theme-switch");
      await page.waitForTimeout(500);
    }
    const kept = await page.evaluate(() =>
      (document.querySelector(".ed-grid .cm-content") || {}).textContent || "",
    );
    const stored = await page.evaluate(() => {
      try {
        return { ...JSON.parse(localStorage.getItem("uiLookV1") || "{}") };
      } catch {
        return {};
      }
    });
    const dup = await page.evaluate(() =>
      /Тема интерфейса/.test(
        [...document.querySelectorAll(".field-label")].map((x) =>
          x.textContent,
        ).join(" | "),
      ),
    );
    if (seen[0] !== "dark/dark/Тема интерфейса: тёмная/иконка")
      problems.push(`переключатель темы: ${seen[0]}`);
    if (seen[1] !== "light/light/Тема интерфейса: светлая/иконка")
      problems.push(`переключатель темы: ${seen[1]}`);
    if (!kept.includes("ТЕМА"))
      problems.push("смена темы выбросила несохранённый текст редактора");
    if (stored.ui !== "dark" || stored.editor !== "auto" || !("fontSize" in stored))
      problems.push(`uiLookV1 после двух переключений: ${JSON.stringify(stored)}`);
    if (dup) problems.push("карточка «Внешний вид» дублирует тему интерфейса");
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-theme.png") });
    log(`${problems.length === before ? "✅" : "❌"} тема в шапке     ${seen.join(" → ")} · текст${kept.includes("ТЕМА") ? " цел" : " потерян"}`);
  }

  /* панель «Обновления» в меню «⋮»: заметки релиза — отрендеренный markdown
   * (литеральных «**» в тексте быть не может), ссылки релиза и сравнения
   * версий — настоящие ссылки, Docker видит только команду хоста (кнопок
   * обновления у него нет), а долгое обновление показывает свой лог.
   * Живой GitHub не трогаем: ответы /api/session подменяются заглушкой */
  {
    const before = problems.length;
    let mock = mockUpdate("git");
    /* сводку подменяем и в «ход обновления»: опрос статуса пришёл бы позже и
     * заменил бы mock настоящими данными — панель мигала бы между двумя */
    const inject = async (route) => {
      const res = await route.fetch();
      let body;
      try {
        body = JSON.parse(await res.text());
      } catch {
        return route.continue();
      }
      body.update = mock;
      // response передавать нельзя: с ним Playwright отдаёт исходное тело
      return route.fulfill({
        status: res.status(),
        headers: { "Cache-Control": "no-store" },
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    };
    await page.route("**/api/session", inject);
    await page.route("**/api/update/status", inject);
    /* сама проверка по кнопке: запрос идёт в сеть, ответ кладёт релиз —
     * панель обязана перерисоваться сразу, а не после перезагрузки страницы */
    let checkHits = 0;
    await page.route("**/api/update/check", async (route) => {
      checkHits += 1;
      // ответ приходит не мгновенно: на паузе видна подпись «Проверяю…»
      await new Promise((done) => setTimeout(done, 600));
      mock = mockUpdate("git");
      await route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ ok: true, update: mock }) });
    });
    const readBox = () =>
      page.evaluate(() => {
        const box = document.querySelector(".user-menu .update-box");
        if (!box) return { none: true };
        return {
          title: (box.querySelector(".update-title") || {}).textContent?.trim() || "",
          text: box.textContent.replace(/\s+/g, " ").trim(),
          bold: box.querySelectorAll(".update-notes strong").length,
          heads: box.querySelectorAll(".update-notes h2").length,
          links: [...box.querySelectorAll(".update-link")].map(
            (a) => `${a.textContent.trim()}→${a.getAttribute("href")}[${a.getAttribute("target")}]`,
          ),
          btns: [...box.querySelectorAll("button")].map((b) => b.textContent.trim()),
          code: (box.querySelector(".update-cmd") || {}).textContent?.trim() || "",
          logs: [...box.querySelectorAll(".update-log")].map((x) => x.textContent.trim()),
          restart: [...box.querySelectorAll(".update-restart")].map((x) => x.textContent.trim()),
          badge: !!document.querySelector(".kebab-btn.has-update"),
        };
      });
    /* goto по другому хэшу документ не перезагружает, а сводку обновления SPA
     * читает один раз при загрузке — отсюда reload */
    const openBox = async (next) => {
      mock = next;
      await page.goto(`${url}/#/hub`, { waitUntil: "load" });
      await page.reload({ waitUntil: "load" });
      await page.waitForTimeout(1200);
      await page.locator(".menu-wrap .kebab-btn").first().click();
      await page.waitForTimeout(400);
      return readBox();
    };

    const git = await openBox(mockUpdate("git"));
    if (git.none) {
      problems.push("панель «Обновления» в меню «⋮» не найдена");
    } else {
      if (git.title !== "Обновления")
        problems.push(`заголовок панели «${git.title}»`);
      // версия — одна на панель: её показывает футер меню (.user-menu-version)
      if (git.text.includes("Обновления · v"))
        problems.push("версия задублирована в заголовке панели");
      if (!git.badge) problems.push("новая версия не помечена на кнопке «⋮»");
      if (git.text.includes("**")) problems.push("заметки релиза остались текстом со «**»");
      if (git.text.includes("Full Changelog"))
        problems.push("строка Full Changelog осталась в заметках");
      if (git.bold !== 1) problems.push(`markdown заметок: <strong> ${git.bold} (ожидаем 1)`);
      if (git.heads !== 1) problems.push(`заголовок заметок: h2 ${git.heads} (ожидаем 1)`);
      if (!git.text.includes("второй пункт")) problems.push("описание релиза потерялось");
      const want = [
        `Релиз v9.9.0→${MOCK_RELEASE.url}[_blank]`,
        `Список изменений→${MOCK_RELEASE.changes}[_blank]`,
      ].join(" | ");
      if (git.links.join(" | ") !== want)
        problems.push(`ссылки панели: ${git.links.join(" | ")}`);
      if (!git.btns.some((b) => b.includes("Обновить до v9.9.0")))
        problems.push("в панели нет кнопки обновления");
      if (!git.btns.some((b) => b.includes("Скачать архив")))
        problems.push("в панели нет кнопки скачивания архива");
      if (!git.text.includes("перемотается на релиз"))
        problems.push("в панели не описан способ обновления");
      if (git.restart.length !== 1 || !/перезапустится сам/.test(git.restart[0]))
        problems.push(`строка о перезапуске: ${JSON.stringify(git.restart)}`);
    }
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-update-git.png") });
    log(`${problems.length === before ? "✅" : "❌"} обновления: git      ${git.none ? "нет панели" : `${git.links.length} ссылки · кнопок ${git.btns.length}`}`);

    const docker = await openBox(mockUpdate("docker"));
    const dockerBefore = problems.length;
    if (docker.none) problems.push("панель Docker не найдена");
    else {
      if (docker.code !== MOCK_HOST_COMMAND)
        problems.push(`Docker: команда хоста «${docker.code}»`);
      if (!/образ обновляет хост/i.test(docker.text))
        problems.push("Docker: подпись способа установки потерялась");
      // про «не заменит себя» говорит одна строка — о перезапуске
      const selfReplace = docker.text.match(/сам себя не заменит/g) || [];
      if (selfReplace.length !== 1)
        problems.push(`Docker: причина дублируется ${selfReplace.length} раза`);
      if ((docker.text.match(/docker compose pull/g) || []).length !== 1)
        problems.push("Docker: команда хоста продублирована");
      if (docker.btns.length !== 1 || !/Проверить снова/.test(docker.btns[0]))
        problems.push(`Docker: кнопки обновления предлагаются: ${docker.btns.join(", ")}`);
      if (docker.text.includes("**")) problems.push("Docker: заметки не отрендерены");
      if (docker.restart.length !== 1 || !/контейнер сам себя не заменит/.test(docker.restart[0]))
        problems.push(`Docker: строка о перезапуске ${JSON.stringify(docker.restart)}`);
    }
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-update-docker.png") });
    log(`${problems.length === dockerBefore ? "✅" : "❌"} обновления: docker ${docker.none ? "нет панели" : `кнопок ${docker.btns.length} · ${docker.code}`}`);

    const run = await openBox(mockUpdate("git", "git", {
      state: "running", kind: "git", mode: "git", from: "0.4.0", to: "", note: "",
      error: "", log: ["Обновление 0.4.0 → v9.9.0: из репозитория (git)",
              "Ветка master: перемотка на релиз v9.9.0 (только ff-only)"],
      started: 0, finished: 0,
    }));
    const runBefore = problems.length;
    if (run.none) problems.push("панель идущего обновления не найдена");
    else {
      if (!/Обновляется/.test(run.text)) problems.push("идущее обновление не подписано");
      if (run.logs.length !== 2) problems.push(`строк лога обновления: ${run.logs.length}`);
      if (run.btns.some((b) => /Обновить до|Скачать архив/.test(b)))
        problems.push("во время обновления остались кнопки запуска");
    }
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-update-running.png") });
    log(`${problems.length === runBefore ? "✅" : "❌"} обновления: процесс ${run.none ? "нет панели" : `строк лога ${run.logs.length}`}`);

    /* код на диске новый, процесс сворачивается: панель обязана объяснять, что
     * тишина в сети — часть плана, а не поломка */
    const restarting = await openBox(mockUpdate("git", "git", {
      state: "restarting", kind: "git", mode: "git", from: "0.4.0", to: "9.9.0",
      note: "", error: "", log: ["Ветка master: перемотка на релиз v9.9.0 (только ff-only)"],
      started: 0, finished: 0,
    }));
    const restartBefore = problems.length;
    if (restarting.none) problems.push("панель перезапуска не найдена");
    else {
      if (!/сервер перезапускается/.test(restarting.text))
        problems.push("перезапуск не подписан");
      if (!/Страница обновится сама/.test(restarting.text))
        problems.push("нет ожидания перезапуска");
      if (restarting.logs.length !== 1)
        problems.push(`строк лога перезапуска: ${restarting.logs.length}`);
      if (restarting.btns.length)
        problems.push(`в панели перезапуска остались кнопки: ${restarting.btns.join(", ")}`);
      if (restarting.restart.length)
        problems.push("при перезапуске строка о перезапуске дублируется");
    }
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-update-restarting.png") });
    log(`${problems.length === restartBefore ? "✅" : "❌"} обновления: перезапуск ${restarting.none ? "нет панели" : `строк лога ${restarting.logs.length}`}`);

    /* последняя версия: панель обязана быть короткой — ни способа, ни перезапуска */
    const fresh = await openBox(mockUpdate("git", "git", null,
      { available: false, release: null }));
    const freshBefore = problems.length;
    if (fresh.none) problems.push("панель «последняя версия» не найдена");
    else {
      if (!/последняя версия/.test(fresh.text))
        problems.push(`«последняя версия» не подписана: ${fresh.text.slice(0, 160)}`);
      if (fresh.restart.length) problems.push("при последней версии показан перезапуск");
      if (fresh.links.length) problems.push("при последней версии остались ссылки релиза");
      if (fresh.btns.length !== 1)
        problems.push(`при последней версии кнопок: ${fresh.btns.length}`);
    }
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-update-fresh.png") });
    log(`${problems.length === freshBefore ? "✅" : "❌"} обновления: без обновл. ${fresh.none ? "нет панели" : `кнопок ${fresh.btns.length}`}`);

    /* портативная сборка: перезапуск делает человек — текст приходит с сервера */
    const portable = await openBox(mockUpdate("portable"));
    const portableBefore = problems.length;
    if (portable.none) problems.push("панель портативной сборки не найдена");
    else {
      if (!/start\.bat/.test(portable.text))
        problems.push("портативной сборке не сказано про start.bat");
      if (!portable.btns.some((b) => b.includes("Обновить до v9.9.0")))
        problems.push("портативной сборке нет кнопки обновления");
      if (portable.restart.length !== 1)
        problems.push(`портативная сборка: строка о перезапуске ${JSON.stringify(portable.restart)}`);
    }
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-update-portable.png") });
    log(`${problems.length === portableBefore ? "✅" : "❌"} обновления: portable ${portable.none ? "нет панели" : `кнопок ${portable.btns.length}`}`);

    /* кнопка «Проверить»: до ответа подписана «Проверяю…» и не жмётся второй
     * раз, после ответа панель показывает итог без перезагрузки страницы */
    const unchecked = mockUpdate("git", "git", null,
      { release: null, checked: 0, available: false });
    await openBox(unchecked);
    const checkBefore = problems.length;
    const checkBtn = page.locator(".user-menu .update-box button",
      { hasText: "Проверить обновления" }).first();
    const pending = checkBtn.click();  // не await: смотрим состояние запроса
    await page.waitForTimeout(250);
    const busy = await page.evaluate(() => {
      const btns = [...document.querySelectorAll(".user-menu .update-box button")];
      return btns.map((b) => `${b.textContent.trim()}${b.disabled ? "[disabled]" : ""}`);
    });
    await pending;
    await page.waitForTimeout(900);
    const checkedBox = await readBox();
    if (checkHits !== 1) problems.push(`кнопка проверки отправила ${checkHits} запрос(а)`);
    if (!busy.some((b) => /Проверяю…\[disabled\]/.test(b)))
      problems.push(`на время проверки кнопки: ${busy.join(", ")}`);
    if (/не проверялось/.test(checkedBox.text))
      problems.push("после проверки панель осталась на «не проверялось»");
    if (!/Доступна v9\.9\.0/.test(checkedBox.text))
      problems.push(`итог проверки не показан: ${checkedBox.text.slice(0, 120)}`);
    if (!checkedBox.badge) problems.push("после проверки нет точки-бейджа на «⋮»");
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-update-check.png") });
    log(`${problems.length === checkBefore ? "✅" : "❌"} обновления: проверка  запросов ${checkHits} · ${busy.join(", ")}`);
    await page.unroute("**/api/update/check");

    /* отказ проверки: сеть недоступна — это не «обновлений нет», причина
     * видна в самой панели, а не только в тосте (его можно не заметить) */
    await page.route("**/api/update/check", (route) => route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ ok: false, error: "HTTP 503: сеть недоступна" }),
    }));
    await openBox(unchecked);
    const failBefore = problems.length;
    await page.locator(".user-menu .update-box button",
      { hasText: "Проверить обновления" }).first().click();
    await page.waitForTimeout(700);
    const failedBox = await readBox();
    if (!/Проверить не удалось: HTTP 503/.test(failedBox.text))
      problems.push(`отказ проверки не показан в панели: ${failedBox.text.slice(0, 140)}`);
    if (/последняя версия/.test(failedBox.text))
      problems.push("отказ проверки выдан за «последнюю версию»");
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-update-failed.png") });
    log(`${problems.length === failBefore ? "✅" : "❌"} обновления: отказ     причина в панели`);
    await page.unroute("**/api/update/check");

    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    await page.unroute("**/api/session");
    await page.unroute("**/api/update/status");
  }

  /* правка-удаление в обзоре глоссария: своё действие в заголовке, своё
   * значение в diff (не пустой «Стало») и своя модалка без поля «Стало».
   * Заодно — структура самой карточки: режим один (переключателя режимов и
   * нижних кнопок нет), применение — кнопкой в сводке, файл правок открывается
   * ссылкой строки состояния (она ведёт в «Файлы» с редактором файла) */
  {
    const before = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/review`, {
      waitUntil: "load",
    });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".rv-row", { timeout: 15000 });
    await page.waitForTimeout(900);
    const rows = await page.evaluate(() =>
      [...document.querySelectorAll(".rv-row")].map((r) => ({
        title: (r.querySelector(".rv-row-title") || {}).textContent?.trim() || "",
        del: r.classList.contains("rv-row-del"),
        diff: (r.querySelector(".rv-diff") || {}).textContent?.trim() || "",
      })),
    );
    if (rows.length !== 3) problems.push(`review: строк ${rows.length} (ожидаем 3)`);
    const dels = rows.filter((x) => x.del);
    if (dels.length !== 1) problems.push(`review: строк удаления ${dels.length} (ожидаем 1)`);
    for (const d of dels) {
      if (!/удаление термина/.test(d.title))
        problems.push(`review: заголовок удаления «${d.title}»`);
      if (!d.diff.includes("термин удалится из глоссария"))
        problems.push(`review: diff удаления «${d.diff}»`);
    }
    if (rows.filter((x) => !x.del).some((x) => /удаление/.test(x.title)))
      problems.push("review: метка удаления уехала на правку поля");
    await page
      .locator('.rv-row-del button:has-text("Откорректировать")')
      .first()
      .click();
    await page.waitForTimeout(600);
    const m = await page.evaluate(() => {
      const box = document.querySelector(".modal-backdrop .modal");
      if (!box) return { title: "", labels: [] };
      return {
        title: (box.querySelector(".modal-title") || {}).textContent?.trim() || "",
        labels: [...box.querySelectorAll(".rv-label")].map((x) =>
          x.textContent.trim(),
        ),
      };
    });
    if (!/^Удаление термина/.test(m.title))
      problems.push(`review: модалка озаглавлена «${m.title}»`);
    if (!m.labels.includes("Что уходит") || m.labels.includes("Стало"))
      problems.push(`review: поля модалки удаления: ${m.labels.join(", ")}`);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-review-delete.png") });
    const card = await page.evaluate(() => {
      const c = document.querySelector(".review-pane .review-card");
      return {
        seg: !!document.querySelector(".seg"),
        bottom: [...(c?.querySelectorAll(".review-actions button") || [])]
          .map((b) => b.textContent.trim()),
        summary: [...(c?.querySelectorAll(".review-summary button") || [])]
          .map((b) => [b.textContent.trim(), b.title]),
        link: (c?.querySelector(".review-status a") || {})
          .textContent?.trim() || "",
      };
    });
    if (card.seg) problems.push("review: вернулся переключатель «Список/JSON»");
    if (card.bottom.length)
      problems.push(`review: внизу карточки кнопки: ${card.bottom.join(", ")}`);
    const apply = card.summary.find(([t]) => t === "Применить");
    if (!apply)
      problems.push(`review: в сводке нет «Применить»: ${card.summary.map((x) => x[0]).join(", ")}`);
    else if (!/все принятые/.test(apply[1]))
      problems.push(`review: подсказка «Применить»: «${apply[1]}»`);
    if (!card.link.startsWith("файл есть · "))
      problems.push(`review: строка о файле правок не ссылка: «${card.link}»`);
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-review-list.png") });
    /* редактор открывается ПОВЕРХ самой вкладки (тот же однофайловый, что в
     * «Поиске»): вкладка не меняется, «Назад» ведёт обратно к списку правок */
    await page.locator(".review-pane .review-status a").first().click();
    await page.waitForTimeout(900);
    const opened = await page.evaluate(() => ({
      tab: (document.querySelector(".tab-active") || {}).textContent?.trim() || "",
      meta: (document.querySelector(".editor-meta") || {}).textContent?.trim() || "",
      back: [...(document.querySelectorAll(".editor-wrap .files-toolbar button") || [])]
        .map((b) => b.textContent.trim()),
      rows: document.querySelectorAll(".rv-row").length,
    }));
    if (opened.tab !== "Проверки")
      problems.push(`review: ссылка открыла вкладку «${opened.tab}»`);
    if (!opened.meta.includes("tmp/ner_review.json"))
      problems.push(`review: редактор файла: «${opened.meta}»`);
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-review-file.png") });
    if (opened.back[0] !== "← Назад")
      problems.push(`review: в тулбаре редактора кнопка «${opened.back[0]}»`);
    else {
      await page.locator(".editor-wrap .files-toolbar button").first().click();
      await page.waitForTimeout(900);
      const back = await page.evaluate(() => ({
        tab: (document.querySelector(".tab-active") || {}).textContent?.trim() || "",
        rows: document.querySelectorAll(".rv-row").length,
      }));
      if (back.tab !== "Проверки" || !back.rows)
        problems.push(`review: «← Назад» не вернул к списку правок `
          + `(вкладка «${back.tab}», строк ${back.rows})`);
    }
    log(`${problems.length === before ? "✅" : "❌"} правки глоссария  ${rows.length} строк · «${(dels[0] || {}).title || ""}» · модалка «${m.title}» | режим один · «${apply ? apply[0] : "?"}» · ссылка «${card.link}» → ${opened.tab}: ${opened.meta}`);
  }

  /* предпросмотр массовых замен: пробельные правила должны быть ВИДНЫ —
   * без меток «^ + ->» неотличим от удаления, а удалённый перенос строки —
   * от пустоты; пробелы паттерна значимы целиком; битая строка —
   * предупреждение, а не падение предпросмотра */
  {
    const before = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/run`, {
      waitUntil: "load",
    });
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".stage-card", { timeout: 15000 });
    await page.locator('.stage-card:has-text("Массовые замены")').first().click();
    await page.waitForSelector(".br-panel .br-box", { timeout: 15000 });
    await page.waitForTimeout(900);
    await page
      .locator(".run-form textarea.epub-textarea")
      .first()
      .fill("^ + ->\n\\n{3,} -> \\n\n +$ ->\n( -> x");
    await page.locator('.br-panel button:has-text("Предпросмотр")').first().click();
    await page.waitForTimeout(1500);
    const look = await page.evaluate(() => {
      const box = document.querySelector(".br-panel .br-box");
      const qs = (sel) => [...(box?.querySelectorAll(sel) || [])];
      return {
        stats: box?.querySelector(".br-stats")?.textContent || "",
        warn: qs(".field-help").map((e) => e.textContent).join(" | "),
        rules: qs(".br-rule").map((e) => e.textContent.replace(/\s+/g, " ")),
        dels: qs(".br-del").map((e) => e.textContent),
        text: box?.querySelector(".br-text")?.textContent || "",
      };
    });
    const all = look.rules.join(" || ");
    if (!/текст изменится/.test(look.stats)) {
      problems.push(`br-preview: нет заголовка главы: «${look.stats}»`);
    }
    if (!all.includes("^·+ → ∅")) {
      problems.push(`br-preview: правило отступа без меток: ${all}`);
    }
    // « +$» — целый паттерн, а не битый «+$»; счётчика у него нет (текст не задет)
    if (!all.includes("·+$ → ∅ (удаление)") || !all.includes("не задели текст")) {
      problems.push(`br-preview: пробельное правило искажено: ${all}`);
    }
    if (!look.dels.some((t) => t.includes("···"))) {
      problems.push(`br-preview: удалённые пробелы не видимы: ${JSON.stringify(look.dels)}`);
    }
    if (!look.text.includes("⏎")) {
      problems.push("br-preview: в диффе нет меток переносов строк");
    }
    if (!/битое правило/.test(look.warn) || !/unterminated subpattern/.test(look.warn)) {
      problems.push(`br-preview: битая строка не описана: «${look.warn}»`);
    }
    if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-batch-replace.png") });
    log(`${problems.length === before ? "✅" : "❌"} br-preview     ${look.stats} · ${look.rules.length} правил`);
  }

  /* сценарии на модалках: вложенные оверлеи, promise-результат и обновление
   * экрана за ними — на этом refactor ломался бы тише всего */
  {
    const before = problems.length;
    await page.goto(`${url}/#/hub`, { waitUntil: "load" });
    await page.waitForTimeout(1200);
    await page.locator('button:has-text("Управление разделами")').first().click();
    await page.waitForTimeout(600);
    // вложенная модалка «Новый раздел»
    await page.locator('.modal-backdrop button:has-text("＋ Раздел")').last().click();
    await page.waitForTimeout(500);
    const nested = await page.evaluate(() => document.querySelectorAll(".modal-backdrop").length);
    if (nested < 2) problems.push(`вложенных оверлеев ${nested}, ожидалось 2`);
    await page.locator('.modal-backdrop:last-of-type input.input').last().fill("TMP2");
    // Enter нажимает ОК ВЕРХНЕЙ модалки: нижняя («Управление разделами») живёт
    await page.keyboard.press("Enter");
    await page.waitForTimeout(900);
    const afterEnter = await page.evaluate(() => document.querySelectorAll(".modal-backdrop").length);
    if (afterEnter !== 1)
      problems.push(`Enter: оверлеев ${afterEnter}, ожидался 1 (верхняя подтверждена, нижняя живёт)`);
    const rows = await page.evaluate(() =>
      [...document.querySelectorAll(".hub-sections-modal .fname")].map((x) => x.textContent.trim()));
    if (!rows.includes("TMP2")) problems.push(`раздел не создан, в списке: ${rows.join(",")}`);
    // опасное действие: сначала неверное слово, потом верное
    await page.locator('.hub-sections-modal .frow:has-text("TMP2") button:has-text("Удалить")').last().click();
    await page.waitForTimeout(500);
    const conf = page.locator(".modal-backdrop").last();
    await conf.locator('input.input').fill("не то");
    await conf.locator('button:has-text("Подтвердить")').click();
    await page.waitForTimeout(400);
    const wrong = await page.evaluate(() => {
      const m = document.querySelectorAll(".modal-backdrop");
      const top = m[m.length - 1];
      return { n: m.length, err: top ? (top.querySelector(".form-error") || {}).textContent : "" };
    });
    if (wrong.n !== 2 || !/УДАЛИТЬ/.test(wrong.err || ""))
      problems.push(`проверка слова не сработала: оверлеев ${wrong.n}, ошибка «${(wrong.err || "").trim()}»`);
    await conf.locator('input.input').fill("УДАЛИТЬ");
    await conf.locator('button:has-text("Подтвердить")').click();
    await page.waitForTimeout(1000);
    const rows2 = await page.evaluate(() =>
      [...document.querySelectorAll(".hub-sections-modal .fname")].map((x) => x.textContent.trim()));
    if (rows2.includes("TMP2")) problems.push("раздел не удалился");
    // Escape закрывает верхнюю модалку
    const beforeEsc = await page.evaluate(() => document.querySelectorAll(".modal-backdrop").length);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    const left = await page.evaluate(() => document.querySelectorAll(".modal-backdrop").length);
    if (left !== beforeEsc - 1)
      problems.push(`Escape: оверлеев было ${beforeEsc}, стало ${left}`);
    if (SHOT) await page.screenshot({ path: path.join(OUT, "flow-sections.png") });
    log(`${problems.length === before ? "✅" : "❌"} сценарий sections    создана/удалена TMP2 · экранный список: ${rows.join(",")} → ${rows2.join(",")}`);
  }

  {
    const before = problems.length;
    await page.goto(`${url}/#/hub`, { waitUntil: "load" });
    await page.waitForTimeout(1200);
    await page.locator('button:has-text("Создать проект")').first().click();
    await page.waitForTimeout(600);
    await page.locator('.modal-backdrop input[placeholder="my_book"]').fill("Probe2");
    await page.locator('.modal-backdrop button:has-text("Создать")').last().click();
    await page.waitForTimeout(1500);
    const card = await page.evaluate(() =>
      [...document.querySelectorAll(".project-card")].some((x) => x.textContent.includes("Probe2")));
    if (!card) problems.push("проект Probe2 не появился в хабе");
    const closed = await page.evaluate(() => document.querySelectorAll(".modal-backdrop").length);
    if (closed !== 0) problems.push(`модалка создания не закрылась (${closed} оверлеев)`);
    log(`${problems.length === before ? "✅" : "❌"} сценарий project     Probe2 ${card ? "создан" : "НЕ создан"}, модалка закрыта`);
  }

  /* модалки: открываем и закрываем (Escape), проверяем что каркас живой и что
   * у окна одна кнопка действия — именно её нажимает Enter.
   * У глоссария настройки живут в меню «⋮» — кнопка видна только с ним */
  const modals = [
    ["sections", "#/hub", 'button:has-text("Управление разделами")', null],
    ["create", "#/hub", 'button:has-text("Создать проект")', null],
    ["ner-types", `#/project/${SECTION}/${BOOK}/ner`,
      'button[title="Фильтр типов"]', ".ner-menu-btn"],
    ["ner-cols", `#/project/${SECTION}/${BOOK}/ner`,
      'button[title="Какие столбцы показывать"]', ".ner-menu-btn"],

    ["export", `#/project/${SECTION}/${BOOK}/ner`,
      'button:has-text("Экспорт для анализа")', null],
    /* «Промпты из шаблона»: выбор способа и имя файла в книге */
    ["prompt-tpl", `#/project/${SECTION}/${BOOK}/prompts`,
      '.prompts-side button:has-text("Из шаблона")', null],
  ];
  for (const [name, hash, sel, opener] of modals) {
    const before = problems.length;
    await page.goto(`${url}/${hash}`, { waitUntil: "load" });
    await page.waitForTimeout(500);
    if (opener) {
      if (!(await page.locator(opener).count())) {
        problems.push(`модалка ${name}: кнопка меню ${opener} не найдена`);
        continue;
      }
      await page.locator(opener).first().click();
      await page.waitForTimeout(300);
    }
    const btn = page.locator(sel).first();
    if (!(await btn.count())) {
      log(`⚠️  модалка ${name}: кнопка не найдена (${sel})`);
      continue;
    }
    await btn.click();
    await page.waitForTimeout(400);
    const box = await page.evaluate(() => {
      const m = document.querySelector(".modal-backdrop .modal");
      if (!m) return null;
      const acts = m.querySelector(".modal-actions");
      const def = window.UIC.actionButton(m.parentElement);
      return {
        wide: m.className.includes("modal-wide"),
        title: (m.querySelector(".modal-title") || {}).textContent || "",
        nodes: m.querySelectorAll("*").length,
        // кнопка Enter: одна на окно, подпись — то, что она делает
        act: !acts ? "нет панели" : def ? (def.textContent || "").trim() : "",
        buttons: acts ? acts.querySelectorAll(".btn").length : 0,
      };
    });
    if (!box) problems.push(`оверлей ${name} не появился`);
    else if (box.buttons < 1 || !box.act)
      problems.push(`модалка ${name}: кнопка Enter не найдена (${box.buttons} кнопок)`);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(150);
    const closed = await page.evaluate(
      () => !document.querySelector(".modal-backdrop"),
    );
    if (!closed) problems.push(`модалка ${name} не закрылась по Escape`);
    if (SHOT) await page.screenshot({ path: path.join(OUT, `modal-${name}.png`) });
    log(
      box
        ? `${problems.length === before ? "✅" : "❌"} модалка ${name.padEnd(16)} ${box.wide ? "wide" : "narrow"} · ${String(box.nodes).padStart(4)} узлов · «${box.title}» · Enter: «${box.act}» из ${box.buttons}`
        : `❌ модалка ${name}: оверлей не появился`,
    );
  }

  await browser.close();
  if (own) own.kill("SIGKILL");
  fs.writeFileSync(
    path.join(OUT, "report.json"),
    JSON.stringify({ at: new Date().toISOString(), url, problems }, null, 2),
  );
  log(
    problems.length
      ? `\n❌ проблем: ${problems.length}\n${problems.map((p) => "  · " + p).join("\n")}`
      : `\n✅ чисто; скриншоты: ${OUT}`,
  );
  process.exit(problems.length ? 1 : 0);
}

main().catch((e) => {
  console.error("💥 probe упал:", e.message);
  process.exit(2);
});

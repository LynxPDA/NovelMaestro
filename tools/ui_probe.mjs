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
];
const SECTION = "TMP";
const BOOK = "Probe";
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
  // файл в корне книги: файловый менеджер есть что показать (quick-look)
  fs.writeFileSync(path.join(book, "notes.md"), "# Заметки\n\n- проба\n", "utf-8");
  // логи книги: вкладка «Логи» показывает и папки, и файлы — и то, и другое
  // рисуется одним пейджером
  fs.mkdirSync(path.join(book, "logs", "chapters"), { recursive: true });
  fs.writeFileSync(path.join(book, "logs", "ner.log"), "2025-01-01 00:00 ner: старт\n", "utf-8");
  fs.writeFileSync(path.join(book, "logs", "chapters", "00000_1_Глава 1.log"), "глава: ок\n", "utf-8");
  fs.mkdirSync(path.join(book, "prompts"), { recursive: true });
  fs.writeFileSync(path.join(book, "prompts", "translate.txt"), "<translate>переведи</translate>\n", "utf-8");
  fs.writeFileSync(path.join(book, "metadata.yaml"), "title: Проба\nauthor: Probe\n");
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
   * дублируется), один GET на запрос, все результаты постранично, клик по
   * имени файла главы открывает «Редактор» с уже подставленным запросом */
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
    await page.fill(".search-q", "глава");
    await page.click(".search-run");
    await page.waitForTimeout(900);
    const res = await page.evaluate(() => ({
      status: ((document.querySelector(".review-status") || {})
        .textContent || "").trim(),
      files: [...document.querySelectorAll(".search-file-path")]
        .map((b) => b.textContent.trim()),
      pagerBtns: document.querySelectorAll(".ner-pager button").length,
      marks: [...document.querySelectorAll(".search-hit-text mark")]
        .map((m) => m.textContent),
      lines: [...document.querySelectorAll(".search-hit-line")]
        .map((x) => x.textContent.trim()),
    }));
    if (chips.length !== 8) problems.push(`поиск: групп ${chips.length}`);
    // подписи файлов глав — те же слаги, что у стадий; глоссария в списке нет
    if (chips.slice(0, 4).map(([t]) => t).join(",")
        !== "chapter,translated,redacted,polished")
      problems.push(`поиск: группы глав «${chips.map(([t]) => t).join(",")}»`);
    if (clusters.join(",") !== "Файлы глав:,Прочее:")
      problems.push(`поиск: кластеры «${clusters.join(",")}»`);
    const on = chips.filter(([, c]) => c).map(([t]) => t);
    if (on.join(",") !== "chapter,polished,Заметки книги")
      problems.push(`поиск: отмечены «${on.join(", ")}»`);
    if (!/^Совпадений: (\d+) · файлов: (\d+) · прочитано: (\d+)$/.test(res.status))
      problems.push(`поиск: статус «${res.status}»`);
    if (res.files[0] !== "chapters/00000_1_Глава 1/chapter.txt")
      problems.push(`поиск: первый файл «${res.files[0]}»`);
    if (!res.marks.length || res.marks.some((t) => t !== "глава"))
      problems.push(`поиск: подсветка ${JSON.stringify(res.marks)}`);
    if (res.lines[0] !== "1") problems.push(`поиск: строка ${res.lines[0]}`);
    // результатов меньше страницы — у панели пейджера нет даже кнопок
    if (res.pagerBtns) problems.push(`поиск: pager молчит, кнопок ${res.pagerBtns}`);
    /* «Только главы» — все артефакты стадий (4 типа × 2 главы), заметки
     * уходят из охвата, но чипсы остаются на месте: это панель выбора */
    await page.click('.search-chips button:has-text("Только главы")');
    await page.click(".search-run");
    await page.waitForTimeout(900);
    const onlyChapters = await page.evaluate(() => ({
      files: document.querySelectorAll(".search-file-path").length,
      chips: [...document.querySelectorAll(".search-scope-chip")]
        .filter((l) => l.querySelector("input").checked).length,
      clusters: document.querySelectorAll(".search-cluster-label").length,
      allChips: document.querySelectorAll(".search-scope-chip").length,
    }));
    if (onlyChapters.files !== 8 || onlyChapters.chips !== 4)
      problems.push(`поиск: «Только главы» — ${onlyChapters.files} файлов, `
        + `${onlyChapters.chips} чипсов`);
    if (onlyChapters.clusters !== 2)
      problems.push(`поиск: с «Только главами» кластеров ${onlyChapters.clusters}`);
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "scenario-search.png") });
    await page.click(".search-file-path");
    await page.waitForTimeout(1200);
    const opened = await page.evaluate(() => ({
      tab: [...document.querySelectorAll(".tab-active")]
        .map((x) => x.textContent.trim()).join(""),
      editor: [...document.querySelectorAll(".ed-cm")]
        .some((x) => x.offsetParent),
      text: [...document.querySelectorAll(".ed-cm")]
        .map((x) => x.textContent).join(" "),
      type: [...document.querySelectorAll(".ed-type")]
        .map((x) => x.value).join("|"),
      findOpen: !!document.querySelector(
        '.cm-panel.cm-search [main-field="true"]'),
      findValue: (document.querySelector(
        '.cm-panel.cm-search [main-field="true"]') || {}).value || "",
    }));
    if (opened.tab !== "Редактор" || !opened.editor)
      problems.push(`поиск: клик по имени не открыл редактор главы `
        + `(вкладка «${opened.tab}», редактор ${opened.editor ? "есть" : "нет"})`);
    if (!opened.type.startsWith("chapter.txt"))
      problems.push(`поиск: открыт не оригинал главы — ${opened.type}`);
    if (!/глава/i.test(opened.text))
      problems.push("поиск: в редакторе не тот файл");
    // запрос доезжает до панели поиска редактора: она открыта и заполнена
    // запрос доезжает до панели поиска редактора: она открыта и заполнена
    if (!opened.findOpen || !/глава/.test(opened.findValue || ""))
      problems.push(`поиск: панель поиска редактора `
        + `${opened.findOpen ? "пуста" : "не открыта"} (${opened.findValue})`);
    /* «Искать в глоссарии» — та же строка уводит на вкладку «Глоссарий».
     * Хэш после клика остался «.../search» (вкладку сменил state, не роут),
     * поэтому сначала уводим SPA на хах: goto тем же URL — не навигация */
    await page.evaluate(() => { location.hash = "#/hub"; });
    await page.waitForTimeout(400);
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/search`,
      { waitUntil: "load" });
    await page.waitForSelector(".search-ner-btn", { timeout: 15000 });
    await page.fill(".search-q", "мир");
    await page.click(".search-ner-btn");
    await page.waitForTimeout(1200);
    /* смена вкладки локальная: хэш остаётся «.../search», поэтому смотрим на
       активную вкладку и на поле поиска глоссария */
    const toNer = await page.evaluate(() => ({
      tab: [...document.querySelectorAll(".tab-active")]
        .map((x) => x.textContent.trim()).join(""),
      q: (document.querySelector(".ner-q") || {}).value || "",
      rows: document.querySelectorAll(".ner-table tbody tr.ner-row").length,
    }));
    if (toNer.tab !== "Глоссарий" || !/мир/.test(toNer.q))
      problems.push(`поиск: в глоссарий не перешло (вкладка «${toNer.tab}», `
        + `поле «${toNer.q}»`);
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "scenario-search-file.png") });
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

  /* «Промпты»: html-язык редактора — теги секций видно по подсветке; тот же
   * промпт в предпросмотре запроса размечен span-ами (теги, подстановки) */
  {
    const before = problems.length;
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/prompts`,
      { waitUntil: "load" });
    await page.waitForSelector(".prompt-item", { timeout: 15000 });
    await page.locator(".prompt-item").first().click();
    await page.waitForTimeout(1500);
    /* классы подсветки CM даёт автоименами («ͼr», «ͼs»…): сверяем не имя, а
       самого факта — у промпта закрашенные диапазоны есть, у файла главы нет */
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
    const ed = await marks();
    if (!/<translate>/.test(ed.text))
      problems.push(`промпты: в редакторе не тот файл «${ed.text}»`);
    if (!ed.hl)
      problems.push(`промпты: подсветка тегов не видна (span ${ed.spans})`);
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "scenario-prompts.png") });
    await page.evaluate(() => { location.hash = "#/hub"; });
    await page.waitForTimeout(400);
    await page.goto(`${url}/#/project/${SECTION}/${BOOK}/editor`,
      { waitUntil: "load" });
    await page.waitForTimeout(1500);
    const plain = await page.evaluate(() => {
      const hosts = [...document.querySelectorAll(".ed-cm")];
      const spans = hosts.flatMap((x) => [...x.querySelectorAll("span[class]")]);
      return {
        hosts: hosts.length,
        hl: spans.filter((x) => x.className.includes("\u037c")).length,
      };
    });
    if (plain.hl)
      problems.push(`редактор глав: текст файла подсветкой залит `
        + `(span-ов ${plain.hl})`);
    log(`${problems.length === before ? "✅" : "❌"} промпты: html-язык `
      + ` «${ed.text}» · подсвечено ${ed.hl} из ${ed.spans} span, `
      + `в редакторе глав ${plain.hl}`
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
    if (f0.rowTips.some((t) => t.some((x) => /Удалить|Скачать|Перенести/.test(x))))
      problems.push(`файлы: в строке осталось групповое действие (${f0.rowTips[0]})`);
    if (f0.rowTips.some((t) => !t.some((x) => x.startsWith("Переименовать "))))
      problems.push("файлы: не у каждой строки есть «Переименовать»");
    if (!f0.rowTips.some((t) => t.some((x) => x.startsWith("Править "))))
      problems.push("файлы: кнопки «Править» у файлов нет");
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

  /* страница «Настройки»: блоки реестра карточками, секрет — маской,
   * сохранение пишет общий конфиг; настройки самого веб-сервера — последние
   * блоки первой субвкладки (отдельной вкладки про .env больше нет), а
   * внешний вид — своя субвкладка с localStorage-предпочтениями */
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
      status: (document.querySelector(".review-status") || {})
        .textContent?.trim() || "",
      html: document.getElementById("app").innerHTML,
    }));
    const wantTabs = ["Модель и сервер", "Перевод", "Глоссарий", "Проверки",
      "Книга и файлы", "Внешний вид"];
    if (head.tabs.join(",") !== wantTabs.join(","))
      problems.push(`настройки: субвкладки (${head.tabs.join("/")})`);
    if (head.active !== head.tabs[0])
      problems.push(`настройки: активна вкладка «${head.active}»`);
    for (const t of ["Профили LLM", "Рассуждения модели",
      "Веб-сервер: сеть и доступ", "Веб-сервер: данные и задачи"])
      if (!head.cards.includes(t)) problems.push(`настройки: нет карточки «${t}»`);
    // путь общего конфига экрану не нужен: статус пустой на General
    if (head.status) problems.push(`настройки: статус General «${head.status}»`);
    if (/\.env/.test(head.html)) problems.push("настройки: .env в разметке экрана");
    if (head.html.includes("probe-secret"))
      problems.push("настройки: значение API-ключa уехало в SPA");
    // значение секрета в DOM — только маска (value пароля innerHTML не
    // сериализует, поэтому читаем inputValue контрола)
    const masked = await page.locator(".settings-cards .field")
      .filter({ hasText: "API-ключ" }).first()
      .locator("input,select,textarea").first().inputValue();
    if (masked !== "••••") problems.push(`настройки: секрет «${masked}»`);
    if (SHOT) await page.screenshot({ path: path.join(OUT, "settings.png") });
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
    if (f.vals[0] !== "default" || f.vals[1] !== "openai" || f.vals[3] !== "0")
      problems.push(`reasoning: предзаполнилось ${f.vals.join("|")}`);
    await card.locator('select[name="REASONING_MODE"]').selectOption("on");
    await card.locator('select[name="THINKING_PROFILE"]').selectOption("qwen");
    await card.locator('select[name="REASONING_EFFORT"]').selectOption("xhigh");
    await card.locator('input[name="THINKING_BUDGET"]').fill("2048");
    await card.locator('input[name="LLM_EXTRA_BODY_JSON"]').fill('{"top_k": 5}');
    // маска секрета — как значение: сервер такой ключ не трогает
    await page.locator('.page-header-actions button:has-text("Сохранить")')
      .first().click();
    await page.waitForTimeout(900);
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
    /* субвкладка «Внешний вид»: только localStorage-карточки, ни одного поля
     * реестра и кнопки «Сохранить» — сохранять там нечего */
    await page.locator('.tabs .tab:has-text("Внешний вид")').first().click();
    await page.waitForTimeout(400);
    const g2 = await page.evaluate(() => ({
      cards: [...document.querySelectorAll(".settings-cards .review-card-title")]
        .map((x) => x.textContent.trim()),
      fields: document.querySelectorAll(".settings-cards [name]").length,
      save: !!(document.querySelector(".page-header-actions .btn-primary") || {})
        .classList?.contains("hidden"),
    }));
    if (g2.cards.join(",") !== "Внешний вид,Интерфейс")
      problems.push(`настройки: карточки «${g2.cards.join(" | ")}»`);
    if (g2.fields) problems.push(`настройки: полей реестра на вкладке ${g2.fields}`);
    if (!g2.save) problems.push("настройки: «Сохранить» видна на «Внешнем виде»");
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
      "Глоссарий": ["Создание глоссария (LLM)", "Проверка глоссария (LLM)"],
      "Проверки": ["Проверка перевода", "Проверка перевода (LLM)",
        "Оценка перевода (LLM)"],
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
    const mode = (fields || []).find((x) => /Поле notes/.test(x));
    if (!mode) problems.push("настройки: нет поля выбора значения для notes");
    else if (!/^select\(4\):last$/.test(mode.replace(/^[^=]*=/, "").replace(/·единица$/, "")))
      problems.push(`настройки: поле режима выглядит так «${mode}»`);
    const cap = (fields || []).find((x) => /Ограничение длины значения/.test(x));
    if (!cap) problems.push("настройки: нет поля потолка длины");
    else if (!/СИМВОЛ/.test(cap) || !/:0$/.test(cap.replace(/·единица$/, "")))
      problems.push(`настройки: потолок длины выглядит так «${cap}»`);
    if (SHOT)
      await page.screenshot({ path: path.join(OUT, "settings-glossary-fields.png") });
    log(`${problems.length === before ? "✅" : "❌"} настройки: стадии  ${lines.join(" · ")} | ${mode || "—"} | ${cap || "—"}`);
  }

  /* профили LLM: General — значения общего конфига, карточка с одним списком
   * профилей, создание/переименование/удаление и сохранение значатся в
   * llm_profiles.json рядом с общим .env */
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
      .querySelector(".review-status") || {}).textContent || "").trim());
    /* значение поля «Сервер LLM» активной карточки (name = ключ .env) */
    const fieldValue = (name) => page
      .locator(`.settings-cards [name="${name}"]`).first().inputValue();
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
    /* создать: prompt браузера — Playwright его сам закрывает, нужен handler */
    page.once("dialog", (d) => d.accept("Домашний"));
    await page.locator('button:has-text("Создать профиль")').first().click();
    await page.waitForTimeout(700);
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
     * под маской); карточки веб-сервера — общие, профиль их не перекрывает */
    const st2 = await status();
    if (!/профиль «Домашний»/.test(st2) || /llm_profiles\.json/.test(st2))
      problems.push(`профили: статус «${st2}»`);
    if (await fieldValue("WEB_PORT") !== "8756")
      problems.push(`профили: профиль перекрыл веб-сервер «${await fieldValue("WEB_PORT")}»`);
    if (await fieldValue("HOST") !== "http://127.0.0.1:9/v1")
      problems.push(`профили: новый профиль не копирует сервер (${await fieldValue("HOST")})`);
    if (await fieldValue("THREADS") === "")
      problems.push("профили: новый профиль не копирует потоки");
    if (await fieldValue("API_KEY") !== "••••")
      problems.push(`профили: секрет профиля показан не маской (${await fieldValue("API_KEY")})`);
    await page.locator('.settings-cards [name="MODEL"]').first().fill("дом-модель");
    await page.locator('.settings-cards [name="API_KEY"]').first().fill("дом-ключ");
    await page.locator('.page-header-actions button:has-text("Сохранить")')
      .first().click();
    await page.waitForTimeout(900);
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
    /* переименовать */
    page.once("dialog", (d) => d.accept("Дачный"));
    await page.locator('button:has-text("Переименовать")').first().click();
    await page.waitForTimeout(600);
    list = await chips();
    if (!list[1] || !list[1].includes("Дачный"))
      problems.push(`профили: переименование (${list.join("/")})`);
    /* мусор: второй профиль создаём и тут же удаляем (confirm) */
    page.once("dialog", (d) => d.accept("Временный"));
    await page.locator('button:has-text("Создать профиль")').first().click();
    await page.waitForTimeout(600);
    if ((await chips()).length !== 3)
      problems.push(`профили: второй профиль (${(await chips()).join("/")})`);
    page.once("dialog", (d) => d.accept());
    await page.locator('button:has-text("Удалить")').first().click();
    await page.waitForTimeout(600);
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
    if (vals !== "1|0|0|200000") problems.push(`оценка: значения ${vals}`);
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

  /* правка-удаление в обзоре глоссария: своё действие в заголовке, своё
   * значение в diff (не пустой «Стало») и своя модалка без поля «Стало» */
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
    log(`${problems.length === before ? "✅" : "❌"} удаление термина  ${rows.length} строк · «${(dels[0] || {}).title || ""}» · модалка «${m.title}»`);
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
    await page.locator('.modal-backdrop:last-of-type button:has-text("ОК")').last().click();
    await page.waitForTimeout(900);
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

  /* модалки: открываем и закрываем (Escape), проверяем что каркас живой.
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
      return m
        ? {
            wide: m.className.includes("modal-wide"),
            title: (m.querySelector(".modal-title") || {}).textContent || "",
            nodes: m.querySelectorAll("*").length,
          }
        : null;
    });
    if (!box) problems.push(`оверлей ${name} не появился`);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(150);
    const closed = await page.evaluate(
      () => !document.querySelector(".modal-backdrop"),
    );
    if (!closed) problems.push(`модалка ${name} не закрылась по Escape`);
    if (SHOT) await page.screenshot({ path: path.join(OUT, `modal-${name}.png`) });
    log(
      box
        ? `${problems.length === before ? "✅" : "❌"} модалка ${name.padEnd(16)} ${box.wide ? "wide" : "narrow"} · ${String(box.nodes).padStart(4)} узлов · «${box.title}»`
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

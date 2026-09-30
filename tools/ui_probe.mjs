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
      ["polished.txt", "Первая глава (полировка).\n"],
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
  // файл в корне книги: файловый менеджер есть что показать (quick-look)
  fs.writeFileSync(path.join(book, "notes.md"), "# Заметки\n\n- проба\n", "utf-8");
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
  // системный .env пробы (WEB_ENV_FILE указывает сюда же): дефолты LLM и
  // блок рассуждений — чтобы «Настройки» показывали существующий файл
  fs.writeFileSync(
    path.join(dir, ".env"),
    [
      "# системный .env пробы (временные данные probe)",
      "HOST=http://127.0.0.1:9/v1",
      "API_KEY=",
      "MODEL=probe-model",
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

  /* переключатели панелей: клик по «Рендер/Код» должен менять, что видно —
   * хост редактора или sandbox-iframe (режим может стартовать с любой стороны) */
  for (const [name, hash, sel] of [
    ["notes-preview", `#/project/${SECTION}/${BOOK}/notes`, 'button[title="Показать отрендеренный вид"]'],

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

  /* рассуждения модели: ОДИН глобальный блок на «Настройках» (в полях стадий
   * его больше нет) + точечная запись общих ключей в системный .env */
  {
    const before = problems.length;
    await page.goto(`${url}/#/settings`, { waitUntil: "load" });
    await page.reload({ waitUntil: "load" });
    await page.waitForTimeout(1200);
    const card = page.locator(".review-card[data-reasoning]");
    if (!(await card.count())) {
      problems.push("reasoning: на «Настройках» нет блока рассуждений");
      log("❌ reasoning-блок    карточка не найдена");
    } else {
      const f = await card.evaluate((el) => ({
        labels: [...el.querySelectorAll(".field-label")].map(
          (x) => x.textContent.trim()),
        names: [...el.querySelectorAll(".field .input")].map(
          (c) => c.getAttribute("name")),
        kinds: [...el.querySelectorAll(".field .input")].map(
          (c) => c.tagName.toLowerCase()),
        opts: [...el.querySelectorAll(".field select")].map(
          (sel) => [...sel.options].map((o) => o.value).join(",")),
        vals: [...el.querySelectorAll(".field .input")].map((c) => c.value),
      }));
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
      // предзаполнение — эффективные значения системного .env (сид пробы)
      if (f.vals[0] !== "default" || f.vals[1] !== "openai" || f.vals[3] !== "0")
        problems.push(`reasoning: предзаполнилось ${f.vals.join("|")}`);
      await card.locator('select[name="REASONING_MODE"]').selectOption("on");
      await card.locator('select[name="THINKING_PROFILE"]').selectOption("qwen");
      await card.locator('select[name="REASONING_EFFORT"]').selectOption("xhigh");
      await card.locator('input[name="THINKING_BUDGET"]').fill("2048");
      await card.locator('input[name="LLM_EXTRA_BODY_JSON"]')
        .fill('{"top_k": 5}');
      await card.locator("button").click();
      await page.waitForTimeout(900);
      if (seedDir) {
        const env = fs.readFileSync(path.join(seedDir, ".env"), "utf-8");
        const lines = env.split("\n");
        for (const line of ["REASONING_MODE=on", "THINKING_PROFILE=qwen",
          "REASONING_EFFORT=xhigh", "THINKING_BUDGET=2048",
          'LLM_EXTRA_BODY_JSON={"top_k": 5}'])
          if (!lines.includes(line)) problems.push(`reasoning: в .env нет ${line}`);
        for (const keep of ["HOST=", "MODEL="])
          if (!env.includes(keep)) problems.push(`reasoning: .env потерял ${keep}`);
      } else {
        log("⚠️  reasoning: внешний сервер — файл .env не проверяем");
      }
      // формы стадий рассуждений больше не касаются
      await page.goto(`${url}/#/project/${SECTION}/${BOOK}/run`,
        { waitUntil: "load" });
      await page.reload({ waitUntil: "load" });
      await page.waitForTimeout(1200);
      const leaked = await page.evaluate(
        () => /reasoning|thinking|extra_body/i.test(
          document.getElementById("app").innerHTML),
      );
      if (leaked) problems.push("reasoning: поле стадии осталось в форме запуска");
      if (SHOT) await page.screenshot({ path: path.join(OUT, "scenario-reasoning.png") });
      log(`${problems.length === before ? "✅" : "❌"} reasoning-блок   ${f.labels.join(" · ")}`);
    }
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

  /* модалки: открываем и закрываем (Escape), проверяем что каркас живой */
  const modals = [
    ["sections", "#/hub", 'button:has-text("Управление разделами")'],
    ["create", "#/hub", 'button:has-text("Создать проект")'],
    ["ner-types", `#/project/${SECTION}/${BOOK}/ner`, 'button[title="Фильтр типов"]'],
    ["ner-cols", `#/project/${SECTION}/${BOOK}/ner`, 'button[title="Какие столбцы показывать"]'],

    ["export", `#/project/${SECTION}/${BOOK}/ner`, 'button:has-text("Экспорт для анализа")'],
  ];
  for (const [name, hash, sel] of modals) {
    const before = problems.length;
    await page.goto(`${url}/${hash}`, { waitUntil: "load" });
    await page.waitForTimeout(500);
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

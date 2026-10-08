#!/usr/bin/env node
/* build_codemirror_bundle.mjs — сборка вендорного бандла CodeMirror.

Бандл самосборный (`kind: bundle` в манифесте): интерфейс обязан работать без
сети, поэтому режимы подсветки НЕ подгружаются из CDN — в браузер попадает ровно
то, что лежит в `web/static/vendor/`. Языки в бандл добавляются здесь; список
языков интерфейса живёт в `web/static/ui-core.js::EDITOR_LANGS`, а «расширение →
язык» — в `web/static/app.js::CM_LANG_BY_EXT`.

npm живёт ВНЕ репозитория (временный каталог в /tmp): своей npm-папки и сборки в
репо нет и не будет — здесь только утилита сборки.

    node tools/build_codemirror_bundle.mjs            # собрать и записать бандл
    node tools/build_codemirror_bundle.mjs --check    # собрать во временный
                                                     # каталог, файл не трогать

Версии пинятся в PACKAGES (иначе бандл меняется сам от случая к случаю). Хэш и
размер манифеста обновить руками нельзя: после сборки —
`python3 tools/vendor_assets.py lock`, гейт — `... vendor_assets.py check`.
*/
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const TARGET = path.join(ROOT, "web", "static", "vendor", "codemirror.min.js");
const UMD_NAME = "CMBundle"; // имя было в бандле раньше — менять нельзя смысла нет

/* Пин версий: бандл — артефакт, он обязан быть воспроизводимым.
 *
 * Языки берутся ПОСЧИТАНУЮ с тем, что интерфейс действительно открывает
 * (книги, промпты, логи, JSON-файлы данных, wiki md/html, сборки fb2):
 *   text      — главы, промпты, логи (расширений ни у чего нет режима)
 *   json      — ner.json, llm_profiles.json, jobs.json, review-файлы
 *   yaml      — конфиги шаблона
 *   markdown  — wiki (md, rulate-md)
 *   html      — wiki (rulate-html) + вложенные js/css внутри разметки
 *   xml       — fb2, opf, ncx, xhtml (служебка epub и сборка fb2)
 *   python    — был в списке раньше, оставлен как есть
 *   properties — .env книги во «Файлах»: тот же KEY=VALUE с комментариями «#»
 *   prompts  — язык разметки промптов NovelMaestro: строки-комментарии «#…»
 *              вне тегов — серым (meta), теги секций <system>/<translate> —
 *              tagName, подстановки {ner_block} — переменные, текст внутри
 *              тегов размечается как markdown (встроенный в язык подсветчик).
 *              Раньше разметку промпта показывал только предпросмотр запроса;
 *              редактору промптов «plain text» её не показывал вовсе.
 * Jinja (е и lang-jinja, и legacy jinja2) НЕ добавлен: промпты NovelMaestro —
 * не шаблоны Jinja (`{{ … }}`), а псевдо-XML с подстановками `{имя}`; режим
 * подсветил бы не то. */
const PACKAGES = {
  codemirror: "6.0.2",
  "@codemirror/commands": "6.11.1",
  "@codemirror/language": "6.12.4",
  "@codemirror/legacy-modes": "6.5.4",
  "@codemirror/lang-css": "6.3.1",
  "@codemirror/lang-html": "6.4.12",
  "@codemirror/lang-javascript": "6.2.5",
  "@codemirror/lang-json": "6.0.2",
  "@codemirror/lang-markdown": "6.5.2",
  "@codemirror/lang-python": "6.2.1",
  "@codemirror/lang-xml": "6.1.0",
  "@codemirror/lang-yaml": "6.1.3",
  "@codemirror/search": "6.7.2",
  "@codemirror/state": "6.7.6",
  "@codemirror/view": "6.43.13",
  "@lezer/highlight": "1.2.5",
  esbuild: "0.28.2",
};

/* Точка входа бандла. API — тот, что читает SPA: каркас редактора, поиск и
 * `langs` (имя → функция-расширение), включая язык промптов `prompts`. */
const ENTRY = `import { basicSetup } from "codemirror";
import { Compartment, EditorState } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import {
  HighlightStyle,
  StreamLanguage,
  syntaxHighlighting,
} from "@codemirror/language";
import { openSearchPanel, search } from "@codemirror/search";
import { css } from "@codemirror/lang-css";
import { html as langHtml } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { xml } from "@codemirror/lang-xml";
import { yaml } from "@codemirror/lang-yaml";
import { properties } from "@codemirror/legacy-modes/mode/properties";
import { tags } from "@lezer/highlight";

/* язык промптов NovelMaestro: построчный разбор (StreamLanguage)
 *   «# …» в начале строки вне тегов — комментарий (tags.meta — серый);
 *   <тег> / </тег> полной строкой — тег секции (tags.tagName);
 *   {имя_плейсхолдера} — подстановка (tags.atom), в любом месте;
 *   внутри блока тега текст размечается по-маркдауновски: жирный
 *   двойными звёздочками (tags.strong), встроенный код в обратных
 *   кавычках (tags.escape).
 * Тег пишется только полной строкой (конвенция промптов), поэтому «<» и «{»
 * внутри текста правил подсветку не ломают. Глубина блоков ведётся в
 * state.depth: открытие <тег> +1, закрытие </тег> −1, строки между — md.
 * ВНИМАНИЕ: тело ниже — шаблонная строка, бэкслеши в regexp удвоены. */
const PROMPT_TOKEN_TABLE = {
  promptTag: tags.tagName,
  promptComment: tags.meta,
  promptVar: tags.atom,
  promptStrong: tags.strong,
  promptMono: tags.escape,
};

function promptToken(stream, state) {
  if (stream.sol()) {
    const trimmed = stream.string.trim();
    if (/^<\\/?[A-Za-z_][\\w:-]*>$/.test(trimmed)) {
      if (trimmed.charAt(1) === "/") {
        state.md = state.depth > 0;
        state.depth = Math.max(0, state.depth - 1);
      } else {
        state.depth += 1;
        state.md = true;
      }
      stream.skipToEnd();
      return "promptTag";
    }
    state.md = state.depth > 0;
    if (trimmed.startsWith("#")) {
      stream.skipToEnd();
      return "promptComment";
    }
  }
  if (stream.match(/\\{[a-z_][a-z0-9_]*\\}/)) return "promptVar";
  if (state.md) {
    if (stream.match(/\\*\\*[^*\\n]+\\*\\*/)) return "promptStrong";
    if (stream.match(/\`[^\`\\n]+\`/)) return "promptMono";
  }
  stream.next();
  return null;
}

const promptMode = {
  name: "prompts",
  token: promptToken,
  startState: () => ({ depth: 0, md: false }),
  tokenTable: PROMPT_TOKEN_TABLE,
};

/* вложенные языки html-файла (стили и скрипты внутри разметки) */
const htmlMixed = () => langHtml({ javascript: javascript(), css: css() });

const langs = {
  html: htmlMixed,
  json,
  markdown,
  prompts: () => StreamLanguage.define(promptMode),
  properties: () => StreamLanguage.define(properties),
  python,
  xml,
  yaml,
};

window.CM = {
  EditorView,
  EditorState,
  Compartment,
  basicSetup,
  keymap,
  search,
  openSearchPanel,
  syntaxHighlighting,
  HighlightStyle,
  tags,
  langs: Object.fromEntries(
    Object.entries(langs).map(([name, make]) => [name, () => make()]),
  ),
};
`;

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function run(cmd, args, cwd) {
  execFileSync(cmd, args, { cwd, stdio: "inherit" });
}

function main() {
  const check = process.argv.includes("--check");
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "nm-cm-bundle-"));
  try {
    fs.writeFileSync(
      path.join(work, "package.json"),
      `${JSON.stringify({ name: "nm-cm-bundle", private: true, version: "1.0.0" }, null, 2)}\n`,
    );
    fs.writeFileSync(path.join(work, "entry.js"), ENTRY);
    const specs = Object.entries(PACKAGES).map(([n, v]) => `${n}@${v}`);
    console.log(`сборка бандла CodeMirror (${specs.length} пакетов, npm вне репо)`);
    run("npm", ["install", "--silent", "--no-audit", "--no-fund", ...specs], work);
    run(
      "npm",
      ["exec", "--silent", "--", "esbuild", "entry.js", "--bundle", "--minify",
        "--format=iife", `--global-name=${UMD_NAME}`, "--target=es2020",
        `--outfile=${check ? path.join(work, "codemirror.min.js") : TARGET}`],
      work,
    );
    const built = check ? path.join(work, "codemirror.min.js") : TARGET;
    const bytes = fs.statSync(built).size;
    const hash = sha256(built);
    if (check) {
      const same = fs.existsSync(TARGET) && sha256(TARGET) === hash;
      console.log(`${same ? "✅ бандл совпадает" : "⚠️  бандл отличается"}: ${bytes} байт`);
      console.log(`   собранный sha256: ${hash}`);
      if (!same) console.log(`   в репо       sha256: ${sha256(TARGET)}`);
      return same ? 0 : 1;
    }
    console.log(`✅ бандл: ${path.relative(ROOT, TARGET)} · ${bytes} байт`);
    console.log(`   sha256: ${hash}`);
    console.log("   не забыть: python3 tools/vendor_assets.py lock");
    return 0;
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}

process.exit(main());

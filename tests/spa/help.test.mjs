import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

/* Структурные проверки web/static/help.md — контента вкладки «Справка».
   Запуск: node --test tests/spa/ (через tests/test_spa_js.py). */

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const helpMd = readFileSync(path.join(root, "web/static/help.md"), "utf8");

test("справка: все разделы ТЗ на месте (H2)", () => {
  const h2 = [...helpMd.matchAll(/^## (.+)$/gm)].map((m) => m[1]);
  for (const sec of [
    "О программе",
    "Управление проектами",
    "Работа с проектом",
    "Шаблоны",
    "Запуски",
    "Регулярные выражения",
  ]) {
    assert.ok(h2.includes(sec), `нет раздела «${sec}»`);
  }
});

test("справка: описание каждой вкладки проекта (H3 под «Работа с проектом»)", () => {
  const work = helpMd.split("## Работа с проектом")[1] || "";
  const h3 = [...work.matchAll(/^### (.+)$/gm)].map((m) => m[1]);
  for (const tab of ["Файлы", "Редактор", "Глоссарий", "Проверка", "Главы",
                     "Статус", "Конфиг", "Промпты", "Логи", "Заметки"]) {
    assert.ok(
      h3.some((h) => h.includes(tab)),
      `нет описания вкладки «${tab}»`,
    );
  }
});

test("справка: инструкции по каждому виду запуска (H3 под «Запуски»)", () => {
  const runs = helpMd.split("## Запуски")[1] || "";
  const h3 = [...runs.matchAll(/^### (.+)$/gm)].map((m) => m[1]);
  for (const st of ["Разбор исходника", "Создание глоссария",
                     "Проверка глоссария", "Перевод", "Проверка перевода",
                     "Массовые замены", "Компиляция", "Wiki"]) {
    assert.ok(
      h3.some((h) => h.includes(st)),
      `нет инструкции по запуску «${st}»`,
    );
  }
});

test("справка: код-блоки сбалансированы (нет висячих ```text)", () => {
  const openers = (helpMd.match(/^```text$/gm) || []).length;
  const closers = (helpMd.match(/^```$/gm) || []).length;
  assert.equal(openers, closers, `${openers} открывающих, ${closers} закрывающих`);
});

test("справка: раздел regexp содержит основы, обратные ссылки и примеры", () => {
  const re = helpMd.split("## Регулярные выражения")[1] || "";
  assert.match(re, /### Диалект/);
  assert.match(re, /### Основы/);
  assert.match(re, /### Обратные ссылки/);
  assert.match(re, /### Примеры с разбором/);
  assert.match(re, /\\1/); // обратные ссылки упомянуты
  assert.match(re, /Python `re`/); // диалект назван
});

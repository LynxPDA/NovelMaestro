// ==UserScript==
// @name         Rulate: массовое обновление глав из .txt
// @namespace    https://github.com/LynxPDA/NovelMaestro
// @version      2.1.9
// @description  Льёт перевод глав на tl.rulate.ru из .txt. Смена названий. Глобальная пауза с рандомом. Диапазон глав. Fallback по названию. Обработка дробных/диапазонных номеров. Отчёт по ненайденным и нестандартным.
// @author       NovelMaestro
// @license      MIT
// @homepageURL  https://github.com/LynxPDA/NovelMaestro
// @supportURL   https://github.com/LynxPDA/NovelMaestro/issues
// @match        *://tl.rulate.ru/book/*
// @match        *://rulate.ru/book/*
// @grant        GM_addStyle
// @connect      self
// @connect      rulate.ru
// @connect      tl.rulate.ru
// @connect      localhost
// @connect      *
// @run-at       document-idle
// @downloadURL  https://raw.githubusercontent.com/LynxPDA/NovelMaestro/main/tools/rulate_reload/rulate-bulk-update.user.js
// @updateURL    https://raw.githubusercontent.com/LynxPDA/NovelMaestro/main/tools/rulate_reload/rulate-bulk-update.meta.js
// ==/UserScript==
// СОБРАНО из src/ и meta.js — локальная правка будет перезаписана:
//   python3 tools/build_userscripts.py

// Служебный файл проверки обновлений: только блок метаданных.
// Не редактировать — собирается tools/build_userscripts.py из meta.js;
// полный скрипт — rulate-bulk-update.user.js.

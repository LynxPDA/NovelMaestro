// ==UserScript==
// @name         Rulate: массовое обновление глав из .txt
// @namespace    https://github.com/LynxPDA/NovelMaestro
// @version      2.18.0
// @description  Льёт перевод глав на tl.rulate.ru из .txt. Кнопка под «Оглавление:», панель в тему сайта. План сопоставления с правкой строится сам. Смена названий. Глобальная пауза с рандомом. Диапазон глав. Поиск по названию. Сверка главы (chrF). Обработка дробных/диапазонных номеров. Отчёт по ненайденным и нестандартным. Главы, разбитые на несколько фрагментов, пропускаются.
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
// ==/UserScript==
// СОБРАНО из src/ и meta.js — локальная правка будет перезаписана:
//   python3 tools/build_userscripts.py

/* Юнит-тесты web/static/ui-core.js.
 * Запуск: node --test tests/spa/ (или npm-скрипт, если появится package.json).
 * Никакой сети и DOM — чистые функции. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const _require = createRequire(import.meta.url); // _: анализатор путает с глобалом
const UICore = _require("../../web/static/ui-core.js");

test("parseRoute: пустой хэш → hub", () => {
  assert.deepEqual(UICore.parseRoute(""), { view: "hub", rest: [] });
  assert.deepEqual(UICore.parseRoute("#"), { view: "hub", rest: [] });
});

test("parseRoute: полный маршрут", () => {
  assert.deepEqual(UICore.parseRoute("#/run/ACTIVE/Book"), {
    view: "run",
    rest: ["ACTIVE", "Book"],
  });
  assert.deepEqual(UICore.parseRoute("/settings"), {
    view: "settings",
    rest: [],
  });
});

test("progressPct: зажим и границы", () => {
  assert.equal(UICore.progressPct(0, 636), 0);
  assert.equal(UICore.progressPct(318, 636), 50);
  assert.equal(UICore.progressPct(636, 636), 100);
  assert.equal(UICore.progressPct(700, 636), 100); // зажим сверху
  assert.equal(UICore.progressPct(0, 0), 0); // нет total
  assert.equal(UICore.progressPct(3, 0), 0);
});

test("progressParts: части строки прогресса", () => {
  // running без событий — строка прогресса всё равно видна: запуск
  // не должен выглядеть мёртвым до первого @@PROGRESS@@
  assert.deepEqual(
    UICore.progressParts({ id: "p1", status: "running", created: null }, null),
    {
      label: "",
      count: "ждём",
      eta: "",
      pct: 0,
      waiting: true,
    },
  );

  // подготовка (точка истории на большой книге идёт секунды): процесс
  // ещё не поднят, но запуск уже идёт — счётчик называет эту фазу
  assert.deepEqual(
    UICore.progressParts({ id: "p2", status: "pending" }, null),
    { label: "", count: "подготовка…", eta: "", pct: 0, waiting: true },
  );

  // isActive: подготовка — тот же идущий запуск для шапки и вкладок
  assert.equal(UICore.isActive({ status: "pending" }), true);
  assert.equal(UICore.isActive({ status: "running" }), true);
  assert.equal(UICore.isActive({ status: "done" }), false);
  assert.equal(UICore.isActive({ status: "failed" }), false);
  assert.equal(UICore.isActive(null), false);

  // нет ни запуска, ни событий (не нашли) — пусто: «0/0 · 0%» нигде
  // мелькать не должно
  assert.deepEqual(UICore.progressParts(null, null), {
    label: "",
    count: "",
    eta: "",
    pct: 0,
    waiting: false,
  });

  // завершённый: счётчик с долей есть, оценки конца нет — у
  // завершённого остатка не бывает, а висящая оценка после остановки
  // читалась бы как обещание
  assert.deepEqual(
    UICore.progressParts(
      { id: "p3", status: "done", created: 1000 },
      { done: 36, total: 36, label: "Перевод" },
    ),
    { label: "Перевод", count: "36/36 · 100%", eta: "", pct: 100, waiting: false },
  );

  // стадия без знаменателя — «5 …» вместо доли
  const noTotal = UICore.progressParts(
    { id: "p4", status: "done", created: 1000 },
    { done: 5, total: 0, label: "wiki" },
  );
  assert.equal(noTotal.count, "5 …");
  assert.equal(noTotal.label, "wiki");
  assert.equal(noTotal.eta, "");
});

test("etaOf и etaText: оценка конца — только у работающего запуска", () => {
  /* Виртуальные часы: оценка живёт по времени между событиями, реальные
     setTimeout в тесте ждать нечего. */
  const realNow = Date.now;
  const T0 = 1_700_000_000;
  let vt = 0;
  Date.now = () => (T0 + vt) * 1000;
  const job = { id: "eta-1", status: "running", created: T0 };
  const p = { done: 20, total: 100 };
  UICore.etaForget("eta-1");
  try {
    // первое событие пришло через 90 c после старта: скорость уже есть
    // (двадцать ячеек за полторы минуты), оценка не ждёт накопления окна
    vt = 90;
    assert.ok(UICore.etaOf(job, p) !== null, "оценка с первого события");
    vt = 120;
    UICore.etaForget("eta-1");
    vt = 0;
    UICore.etaOf(job, { done: 20, total: 100 });
    vt = 600;
    const e = UICore.etaOf(job, { done: 24, total: 100 });
    assert.ok(e && e.remaining > 0, "минута и шаги — оценка есть");
    // строка оценки: остаток и часы конца (формат часов зависит от пояса,
    // поэтому сверяем только начало)
    vt = 600;
    const text = UICore.etaText(job, { done: 24, total: 100 });
    assert.match(text, /^осталось ≈ \d+ (мин|ч)/);
    // в строку прогресса оценка попадает только у running
    const run = UICore.progressParts(
      { id: "eta-1", status: "running", created: T0 },
      { done: 24, total: 100 },
    );
    assert.match(run.eta, /^осталось ≈ /);
    const done = UICore.progressParts(
      { id: "eta-1", status: "done", created: T0 },
      { done: 24, total: 100 },
    );
    assert.equal(done.eta, "");
  } finally {
    Date.now = realNow;
  }
  // финал запуска снимает накопленное: следующий прогон считается заново
  UICore.etaForget("eta-1");
  UICore.etaForget("eta-1");
});

test("etaOf: скорость следует за последними минутами, а не с начала", () => {
  /* Память скорости считалась от `last.t` — это Unix-время
   * (`Date.now()/1000`). Деление на ETA_SLOWDOWN давало tau ≈ 4.5e8 c:
   * вес точки часовой давности 0.999992, то есть забывания не было вовсе
   * и скорость оставалась средней С НАЧАЛА ПРОГОНА. Отсюда и скачки
   * остатка на боевом сервере: медленная волна глав тянула оценку вниз,
   * быстрая — вверх, и «11 дней» сменялись «39 днями» без всякой причины
   * в работе.
   *
   * Сценарий: 20 минут быстро (стадия раз в 5 c), затем 40 минут втрое
   * медленнее (раз в 15 c). Правда к концу — по последней скорости.
   * Со средней с начала выходит заметно меньше правды. */
  const realNow = Date.now;
  const T0 = 1_700_000_000;
  let vt = 0;
  Date.now = () => (T0 + vt) * 1000;
  const job = { id: "eta-speed", status: "running", created: T0 };
  const total = 6000;
  UICore.etaForget("eta-speed");
  try {
    let done = 0;
    for (vt = 5; vt <= 1200; vt += 5) {
      done += 1;
      UICore.etaOf(job, { done, total });
    }
    for (let cur = vt; cur <= 3600; cur += 15) {
      vt = cur;
      done += 1;
      UICore.etaOf(job, { done, total });
    }
    const e = UICore.etaOf(job, { done, total });
    assert.ok(e, "оценка есть");
    const truth = (total - done) * 15; // последняя скорость: 15 c на стадию
    const off = Math.abs(e.remaining - truth) / truth;
    assert.ok(
      off < 0.15,
      `оценка ${(e.remaining / 60).toFixed(0)} мин при правде `
        + `${(truth / 60).toFixed(0)} мин (${(off * 100).toFixed(0)}%): `
        + "скорость осталась средней с начала прогона",
    );
  } finally {
    Date.now = realNow;
    UICore.etaForget("eta-speed");
  }
});

test("etaOf: отстающий канал не сбрасывает окно", () => {
  /* Один запуск виден с двух сторон: живой SSE в панели лога и опрос
   * /jobs (шапка, Дашборд) раз в 10 c. Снимок опроса отстаёт от живого
   * кадра — счётчик приходит назад. Это не перезапуск стадии: сброс окна
   * на каждом опросе не давал оценке набраться вовсе. */
  const realNow = Date.now;
  const T0 = 1_700_000_000;
  let vt = 0;
  Date.now = () => (T0 + vt) * 1000;
  try {
    const job = { id: "stale", status: "running", created: T0 };
    UICore.etaForget("stale");
    // ровный темп: 0.1 шага/c — живой канал
    for (let d = 0; d <= 10; d += 1) {
      vt = d * 10;
      UICore.etaOf(job, { done: d, total: 1000 });
    }
    // опрос пришёл с тем же снимком, что и прошлый — новый прогресс не
    // пришёл, и выдумывать по нему нечего
    vt = 110;
    const same = UICore.etaOf(job, { done: 10, total: 1000 });
    assert.ok(same && same.remaining > 9000, String(same && same.remaining));
    // следующий живой кадр: шаг вперёд
    vt = 120;
    const e = UICore.etaOf(job, { done: 11, total: 1000 });
    // скорость та же: остаток ≈ 9890 c, а не часы из отрицательного
    // приращения через откат
    assert.ok(e && e.remaining > 9000 && e.remaining < 13000,
      String(e && e.remaining));
  } finally {
    Date.now = realNow;
    UICore.etaForget("stale");
  }
});

test("etaOf: перезапуск стадии возвращает счётчик", () => {
  /* Стадия, начатая заново, пересчитывает счётчик: done падает с 100 до 3,
   * total остаётся. Считаем продолжением того же бара — скорость та же,
   * остаток 997 шагов, а не часы из отрицательного приращения.
   * Начало бара (t0/d0) — точка отсчёта средней, пока окно не измерено. */
  const realNow = Date.now;
  const T0 = 1_700_000_000;
  let vt = 0;
  Date.now = () => (T0 + vt) * 1000;
  try {
    const job = { id: "rollback", status: "running", created: T0 };
    UICore.etaForget("rollback");
    // старый темп: 0.1 шага/c
    for (let d = 0; d <= 100; d += 10) {
      vt = d * 10;
      UICore.etaOf(job, { done: d, total: 1000 });
    }
    vt = 1000;
    assert.ok(Math.abs(UICore.etaOf(job, { done: 100, total: 1000 }).remaining - 9000) < 100);
    // откат: счётчик вернулся к 3
    vt = 1010;
    UICore.etaOf(job, { done: 3, total: 1000 });
    // тот же темп заново
    for (let d = 6; d <= 60; d += 6) {
      vt = 1010 + d * 10;
      UICore.etaOf(job, { done: d, total: 1000 });
    }
    vt = 1610;
    const e = UICore.etaOf(job, { done: 60, total: 1000 });
    // скорость 0.1 шага/c (две выборки нового участка), остаток 940 → 9400,
    // а не 34000 из приращений через откат
    assert.ok(e && e.remaining > 8000 && e.remaining < 11000, String(e && e.remaining));
  } finally {
    Date.now = realNow;
    UICore.etaForget("rollback");
  }
});

test("etaOf: показанная оценка сглаживается по времени", async () => {
  const now = Date.now() / 1000;
  const job = { id: "smooth-1", status: "running", created: now - 300 };
  UICore.etaForget("smooth-1");
  // до прироста считать нечего: оценка — по сделанному
  const first = UICore.etaOf(job, { done: 0, total: 100 });
  assert.equal(first, null, "ничего не сделано — оценивать нечего");
  await new Promise((r) => setTimeout(r, 60));
  UICore.etaForget("smooth-1");
  const realNow = Date.now;
  let vt = 0;
  Date.now = () => (1_700_000_000 + vt) * 1000;
  const job2 = { id: "smooth-2", status: "running", created: 1_700_000_000 };
  UICore.etaForget("smooth-2");
  vt = 0;
  assert.equal(UICore.etaOf(job2, { done: 0, total: 100 }), null);
  // первая же завершённая ячейка даёт оценку: 1 шаг за 30 c → 99 шагов
  vt = 30;
  const e0 = UICore.etaOf(job2, { done: 1, total: 100 });
  assert.ok(e0 && Math.abs(e0.remaining - 2970) < 60, String(e0 && e0.remaining));
  vt = 70;
  const e1 = UICore.etaOf(job2, { done: 3, total: 100 });
  assert.ok(e1 && e1.remaining > 0, "минута и два приращения — оценка есть");
  // прогресс скакнул: показ сдвигается на долю правки по времени, а не на
  // всё новое значение (ETA_SMOOTH), а не перескакивает одним кадром
  vt = 80;
  const e2 = UICore.etaOf(job2, { done: 6, total: 100 });
  assert.ok(e2.remaining < e1.remaining, "новая скорость видна");
  assert.ok(e2.remaining > e1.remaining * 0.6, "но не целиком за 10 c");
  Date.now = realNow;
  UICore.etaForget("smooth-2");
});

test("runSummary: карточка запуска — проект, проценты, остаток", () => {
  const realNow = Date.now;
  const T0 = 1_700_000_000;
  let vt = 0;
  Date.now = () => (T0 + vt) * 1000;
  try {
    const job = {
      id: "abc123",
      status: "running",
      created: T0,
      title: "Перевод (LLM)",
      project: "ACTIVE/Книга",
    };
    UICore.etaForget("abc123");
    // ровный темп: 20 шагов за 600 c → остаток 80 шагов ≈ 40 мин
    UICore.etaOf(job, { done: 10, total: 100 });
    vt = 300;
    UICore.etaOf(job, { done: 15, total: 100 });
    vt = 600;
    const r = UICore.runSummary(job, { done: 20, total: 100, label: "Глава 3 · Перевод" });
    assert.equal(r.id, "abc123");
    assert.equal(r.title, "Перевод (LLM)");
    assert.equal(r.project, "ACTIVE/Книга");
    assert.equal(r.section, "ACTIVE");
    assert.equal(r.projectName, "Книга");
    assert.equal(r.pct, 20);
    assert.equal(r.pctText, "20%");
    assert.equal(r.count, "20/100 · 20%");
    assert.equal(r.label, "Глава 3 · Перевод");
    // скорость 15 шагов за 300 c → остаток 80/0.05 = 1600 c ≈ 27 мин
    assert.match(r.remaining, /^осталось ≈ \d+ (мин|ч)/);
    assert.ok(r.finish, "часы завершения посчитаны");
    assert.equal(r.waiting, false);
    UICore.etaForget("abc123");

    // без событий прогресса: проценты и остаток пустые, ожидание видно
    const wait = UICore.runSummary(
      { id: "w1", status: "running", created: T0, title: "Глоссарий", project: "HOLD/x" },
      null,
    );
    assert.equal(wait.pctText, "");
    assert.equal(wait.remaining, "");
    assert.equal(wait.waiting, true);
    assert.equal(wait.count, "ждём");
    UICore.etaForget("w1");

    // без total доли нет — «0%» не выдумываем
    const noTotal = UICore.runSummary(
      { id: "n1", status: "running", created: T0, title: "wiki", project: "" },
      { done: 5, total: 0 },
    );
    assert.equal(noTotal.pctText, "");
    assert.equal(noTotal.count, "5 …");
    assert.equal(noTotal.section, "");
    assert.equal(noTotal.projectName, "");
    UICore.etaForget("n1");
  } finally {
    Date.now = realNow;
  }
});

test("runDotTitle: подсказка иконки — по строке на запуск", () => {
  assert.equal(UICore.runDotTitle([]), "");
  assert.equal(UICore.runDotTitle(null), "");
  const realNow = Date.now;
  const T0 = 1_700_000_000;
  let vt = 0;
  Date.now = () => (T0 + vt) * 1000;
  try {
    const jobs = [
      {
        id: "j1", status: "running", created: T0,
        title: "Перевод (LLM)", project: "ACTIVE/Книга",
        progress: { done: 20, total: 100 },
      },
      {
        id: "j2", status: "running", created: T0,
        title: "Проверка (LLM)", project: "HOLD/Другая",
        progress: null,
      },
    ];
    UICore.etaForget("j1");
    UICore.etaForget("j2");
    // набираем темп: 5 шагов за 150 c → остаток 80 шагов ≈ 40 мин
    jobs[0].progress.done = 5;
    UICore.etaOf(jobs[0], jobs[0].progress);
    vt = 150;
    jobs[0].progress.done = 10;
    UICore.etaOf(jobs[0], jobs[0].progress);
    vt = 300;
    jobs[0].progress.done = 20;
    const text = UICore.runDotTitle(jobs);
    const lines = text.split("\n");
    assert.equal(lines[0], "Идёт запусков: 2");
    assert.match(lines[1], /^Перевод \(LLM\) · Книга · 20% · осталось ≈ \d+ (мин|ч)$/);
    // у запуска без событий нет ни процентов, ни остатка — только имя
    assert.equal(lines[2], "Проверка (LLM) · Другая");
    // один запуск — заголовок в единственном числе
    assert.match(UICore.runDotTitle([jobs[0]]).split("\n")[0], /^Идёт запуск$/);
  } finally {
    Date.now = realNow;
  }
  UICore.etaForget("j1");
  UICore.etaForget("j2");
});

test("etaDuration: остаток словами", () => {
  assert.equal(UICore.etaDuration(0), "0 с");
  assert.equal(UICore.etaDuration(45), "45 с");
  assert.equal(UICore.etaDuration(60), "1 мин");
  assert.equal(UICore.etaDuration(150), "3 мин");
  assert.equal(UICore.etaDuration(3600), "1 ч");
  assert.equal(UICore.etaDuration(3660), "1 ч 1 мин");
  assert.equal(UICore.etaDuration(6 * 3600), "6 ч");
  assert.equal(UICore.etaDuration(4000), "1 ч 7 мин");
  // дольше двух суток — в сутках: «277777 ч» в подсказке не читается
  assert.equal(UICore.etaDuration(48 * 3600), "2 дн");
  assert.equal(UICore.etaDuration(50 * 3600), "2 дн 2 ч");
  assert.equal(UICore.etaDuration(24 * 24 * 3600), "24 дн");
  // мусор на входе — не «NaN с» и не исключение
  assert.equal(UICore.etaDuration(NaN), "0 с");
  assert.equal(UICore.etaDuration(null), "0 с");
  assert.equal(UICore.etaDuration(undefined), "0 с");
  assert.equal(UICore.etaDuration(-100), "0 с");
  assert.equal(UICore.etaDuration("120"), "2 мин");
});

test("etaRemaining: скорость — средняя по прогону", () => {
  /* Скорость — по накопленным приращениям, а не по последним минутам:
   * конвейер ведёт главу через все стадии, и окно успевало поймать то
   * дорогой перевод, то пяток дешёвых редактур. Ровный темп: 10 ячеек за
   * 1000 c, осталось 90 → 9000 c. */
  const even = [];
  for (let i = 0; i <= 10; i += 1) even.push({ t: 1000 + i * 100, done: i, total: 100 });
  assert.ok(
    Math.abs(UICore.etaRemaining(even, 10, 100, even[10].t) - 9000) < 60,
    String(UICore.etaRemaining(even, 10, 100, even[10].t)),
  );
  // прогон ускорился вдвое в конце: среднее по всему прогону не
  // перескакивает на новую скорость целиком, но и не игнорирует её —
  // память растёт вместе с прожитым временем (рассчитываем на первой
  // секунде, память ещё минимальная)
  const mixed = even.concat([{ t: even[10].t + 50, done: 20, total: 100 }]);
  const last = mixed[mixed.length - 1];
  const est = UICore.etaRemaining(mixed, last.done, 100, last.t, null);
  assert.ok(est > 0 && est < 9000, String(est));
});

test("etaRemaining: окно меряет скорость с двух приращений за минуту", () => {
  // одна выборка — скорости нет: событие приходит в произвольной фазе
  // шага, и пара выборок подряд меряет не шаг, а его остаток
  assert.equal(
    UICore.etaRemaining([{ t: 1100, done: 20, total: 100 }], 20, 100, 1100),
    null,
  );
  // два приращения на длине меньше минуты — тоже нет
  const short = [
    { t: 1058, done: 3, total: 320 },
    { t: 1100, done: 5, total: 320 },
  ];
  assert.equal(UICore.etaRemaining(short, 5, 320, 1100), null);
  // ровно минута и два приращения — уже оценка: 2 за 100 c, остаток
  // 315 шагов → 15750 c
  const two = [
    { t: 1000, done: 3, total: 320 },
    { t: 1100, done: 5, total: 320 },
  ];
  assert.equal(UICore.etaRemaining(two, 5, 320, 1100), 15750);
  // прогресс встал — приращений нет, считать нечего
  const stuck = [
    { t: 1000, done: 10, total: 100 },
    { t: 1100, done: 10, total: 100 },
  ];
  assert.equal(UICore.etaRemaining(stuck, 10, 100, 1100), null);
});

test("etaRemaining: старое забывается, свежее весит больше", () => {
  /* Долгий прогон: сначала 10 шагов по 100 c, потом 10 шагов по 10 c.
   * Средняя «наивно за всё время» тянула бы старый темп; экспоненциальный
   * вес по времени даёт скорость ближе к свежей. */
  const samples = [];
  let t = 1000;
  let done = 0;
  for (let i = 0; i < 10; i += 1) { t += 100; done += 1; samples.push({ t, done, total: 1000 }); }
  for (let i = 0; i < 10; i += 1) { t += 10; done += 1; samples.push({ t, done, total: 1000 }); }
  const est = UICore.etaRemaining(samples, done, 1000, t);
  const naive = (1000 - done) / (done / (t - 1000));
  // оценка оптимистичнее наивной средней (свежий темп быстрее) и всё
  // ещё остаётся в разумных пределах — не «вся книга за минуту»
  assert.ok(est < naive, `${est} < ${naive}`);
  assert.ok(est > 100, String(est));
});

test("etaRemaining: границы — нет данных для оценки", () => {
  const win = [
    { t: 1000, done: 10, total: 100 },
    { t: 1100, done: 20, total: 100 },
  ];
  assert.equal(UICore.etaRemaining(win, 0, 100, 1100), null); // не начали
  assert.equal(UICore.etaRemaining(win, 100, 100, 1100), null); // всё готово
  assert.equal(UICore.etaRemaining(win, 20, 0, 1100), null); // нет total
  assert.equal(UICore.etaRemaining(null, 20, 100, 1100), null); // нет выборок
});

test("etaRemaining: первая же ячейка даёт оценку", () => {
  /* Главный случай: конвейер на 2199 ячеек, первые результаты — через
   * две минуты. Окна (два приращения за минуту) ещё нет, но средняя с
   * начала бара уже меряет темп, и оценка обязана появиться сразу. */
  const e = UICore.etaRemaining([], 1, 100, 120, { t: 0, done: 0 });
  assert.ok(e && Math.abs(e - 11880) < 60, String(e)); // 1 шаг за 120 c
  // пачка одновременных событий (16 потоков закончили стадию разом)
  // средней с начала бара не обманывает: 16 за 120 c → остаток 14880
  const burst = UICore.etaRemaining([], 16, 100, 120, { t: 0, done: 0 });
  assert.ok(burst && Math.abs(burst - 630) < 10, String(burst));
  // совсем свежий старт (меньше ETA_MIN_RUN) — скорости ещё нет
  assert.equal(UICore.etaRemaining([], 1, 100, 2, { t: 0, done: 0 }), null);
  // перезапуск стадии: начало бара пересчитано, средней нет до прироста
  assert.equal(UICore.etaRemaining([], 3, 100, 1000, { t: 1000, done: 3 }), null);
  // страница открыта посреди прогона (перезагрузка): первого бара своей
  // точки отсчёта не имеет — считаем от старта запуска, а не от загрузки
  const resumed = UICore.etaRemaining([], 3, 2199, 1800, { t: 0, done: 0 });
  assert.ok(resumed > 0, "оценка сразу после перезагрузки страницы");
});

test("boolOn: строки .env", () => {
  assert.equal(UICore.boolOn("1"), true);
  assert.equal(UICore.boolOn("0"), false);
  assert.equal(UICore.boolOn("true"), true);
  assert.equal(UICore.boolOn("false"), false);
  assert.equal(UICore.boolOn("yes"), true);
  assert.equal(UICore.boolOn("on"), true);
  assert.equal(UICore.boolOn(""), false);
  assert.equal(UICore.boolOn(null), false);
  assert.equal(UICore.boolOn(1), true);
  assert.equal(UICore.boolOn(0), false);
});

test("fileBase: пути со слешами и бэкслешами", () => {
  assert.equal(
    UICore.fileBase("chapters/001/translated.txt"),
    "translated.txt",
  );
  assert.equal(UICore.fileBase("a\\b\\ner.json"), "ner.json");
  assert.equal(UICore.fileBase("plain.txt"), "plain.txt");
  assert.equal(UICore.fileBase(""), "");
});


test("pickPoolFile: только реально существующие файлы", () => {
  const pool = ["pipeline_prompt.txt", "translate_prompt.txt", "ner.json"];
  // basename дефолта есть в пуле → подхват
  assert.equal(
    UICore.pickPoolFile("prompts/translate_prompt.txt", pool),
    "translate_prompt.txt",
  );
  // точное имя (dir="") в пуле
  assert.equal(UICore.pickPoolFile("ner.json", pool), "ner.json");
  // дефолт указывает на файл, которого нет → НЕ подхватываем ("")
  assert.equal(UICore.pickPoolFile("redact_prompt.txt", pool), "");
  assert.equal(UICore.pickPoolFile("prompts/redact_prompt.txt", pool), "");
  // пустой дефолт → ""
  assert.equal(UICore.pickPoolFile("", pool), "");
});

test("autofileValue: имя файла, маска и пустой пул", () => {
  const opts = {
    source: ["book.epub", "cover.jpg", "notes.txt"],
    prompts: ["ner_check_prompt.txt"],
    root: ["ner.json"],
  };
  // конкретное имя: файл есть в пуле → подхват именем (не путём)
  assert.equal(
    UICore.autofileValue({ autofile: "source/donate.txt" }, opts), "");
  assert.equal(
    UICore.autofileValue({ autofile: "source/cover.jpg" }, opts),
    "cover.jpg");
  // поле с dir="" ищется в пуле корня проекта
  assert.equal(UICore.autofileValue({ autofile: "ner.json" }, opts),
    "ner.json");
  // маска «source/*.epub»: первый файл пула с этим расширением
  assert.equal(
    UICore.autofileValue({ autofile: ["source/*.epub"] }, opts), "book.epub");
  // несколько кандидатов: выигрывает первый нашедшийся
  assert.equal(
    UICore.autofileValue(
      { autofile: ["source/*.mobi", "source/*.txt"] }, opts), "notes.txt");
  // пул той же папки, но файл другого расширения — не подхват
  assert.equal(
    UICore.autofileValue({ autofile: ["prompts/*.epub"] }, opts), "");
  // autofile нет / пустой пул — ничего не подхватываем
  assert.equal(UICore.autofileValue({ name: "x" }, opts), "");
  assert.equal(UICore.autofileValue({ autofile: ["source/*.epub"] }, {}), "");
  assert.equal(UICore.autofileValue({ autofile: ["source/*.epub"] },
    { source: [] }), "");
});

test("clampFont: из опций, иначе дефолт", () => {
  assert.equal(UICore.clampFont(12, [5, 7, 10, 12, 14], 12), 12);
  assert.equal(UICore.clampFont("12", [5, 7, 10, 12, 14], 12), 12);
  assert.equal(UICore.clampFont(9, [5, 7, 10, 12, 14], 12), 12); // не из списка
  assert.equal(UICore.clampFont("", [5, 7, 10, 12, 14], 12), 12);
  assert.equal(UICore.clampFont(7, [5, 7, 10, 12, 14], 12), 7); // легаси 7 → 12
});

test("chapterRangeError: порядок диапазона и пределы книги", () => {
  const err = UICore.chapterRangeError;
  /* пустые поля — не ошибка: «вся книга», автоподхват делает форма */
  assert.equal(err("", "", {}), "");
  assert.equal(err(null, undefined, {}), "");
  assert.equal(err("", "5", {}), "");
  /* порядок: конец не меньше начала (главный случай — 64 – 3) */
  assert.match(err("64", "3", {}), /Начальная глава больше конечной/);
  assert.match(err("64", "3", {}), /3 – 64/, "подсказка — готовый диапазон");
  assert.equal(err("3", "64", {}), "");
  assert.equal(err("7", "7", {}), "");
  /* нумерация с 1 */
  assert.match(err("0", "5", {}), /с 1/);
  /* пределы книги — оба поля */
  assert.match(err("1", "999", { min: 1, max: 320 }), /всего 320/);
  assert.match(err("999", "9999", { min: 1, max: 320 }), /всего 320/);
  assert.equal(err("1", "320", { min: 1, max: 320 }), "");
  assert.match(err("9", "64", { min: 12, max: 320 }),
    /первая глава книги — 12/);
  /* глав не нашли (опций нет) — проверяется только порядок */
  assert.match(err("64", "3", {}), /больше конечной/);
  /* не число — ошибка поля, а не «пропустить» */
  assert.match(err("abc", "5", {}), /нужно целое число/);
  assert.match(err("1", "2.5", {}), /нужно целое число/);
  assert.match(err("-2", "5", {}), /нужно целое число/);
});

test("dirEntries: плоский список → дерево каталога", () => {
  const files = [
    "prompts/translate.txt",
    "prompts/redact.txt",
    "prompts/nested/x.txt",
    "metadata.yaml",
    ".env.example",
  ];
  const root = UICore.dirEntries(files, "");
  assert.deepEqual(root.map((e) => `${e.dir ? "d:" : "f:"}${e.name}`).sort(), [
    "d:prompts",
    "f:.env.example",
    "f:metadata.yaml",
  ]);
  const prompts = UICore.dirEntries(files, "prompts");
  assert.deepEqual(
    prompts.map((e) => `${e.dir ? "d:" : "f:"}${e.name}`).sort(),
    ["d:nested", "f:redact.txt", "f:translate.txt"],
  );
  assert.deepEqual(UICore.dirEntries([], ""), []);
});

/* ── матчер глоссария (вкладка «Редактор») ── */

const NER_ITEMS = [
  { term: "Хунг", translation: "Хун", type: "имя", notes: "герой" },
  { term: "секта", translation: "Школа", type: "место", notes: "" },
  { term: "灵草", translation: "灵草", type: "предмет" },
  { term: "Линь", translation: "Линь", type: "имя" },
];

function matcherFor(items) {
  return UICore.buildGlossaryMatcher(items);
}

function hits(text, items) {
  return UICore.glossaryMatches(text, matcherFor(items)).map((m) =>
    text.slice(m.from, m.to),
  );
}

test("buildGlossaryMatcher: оба поля term+translation", () => {
  const m = matcherFor(NER_ITEMS);
  // 4 записи: term Хунг + translation Хун, term секта + translation Школа,
  // термин-дубль (translation === term — не дублируется), Линь
  assert.equal(m.total, 6);
});

test("glossaryMatches: совпадения по обоим полям", () => {
  const text = "Хунг и Школа. Хун улыбнулся.";
  const found = hits(text, NER_ITEMS);
  assert.deepEqual(found, ["Хунг", "Школа", "Хун"]);
});

test("glossaryMatches: регистронезависимо, термин находится", () => {
  const items = [{ term: "Хунг", translation: "секта" }];
  const found = hits("хунг и СЕКТА", items);
  // совпадения — в регистре ИСХОДНОГО текста, поиск регистронезависимый
  assert.deepEqual(found, ["хунг", "СЕКТА"]);
  // item привязан к совпадению несмотря на другой регистр
  const ms = UICore.glossaryMatches("хунг", UICore.buildGlossaryMatcher(items));
  assert.equal(ms.length, 1);
  assert.equal(ms[0].item.term, "Хунг");
});

test("glossaryMatches: дубль термин/перевод не дублирует совпадения", () => {
  const text = "灵草 растёт. Линь собирает 灵草.";
  const found = hits(text, NER_ITEMS);
  // 灵草 и Линь — по одному вхождению каждый, несмотря на дубль полей
  assert.deepEqual(found, ["灵草", "Линь", "灵草"]);
});

test("glossaryMatches: длинные термины раньше коротких", () => {
  const items = [
    { term: "abc", translation: "" },
    { term: "abcd", translation: "" },
  ];
  const text = "abcd";
  const found = hits(text, items);
  assert.deepEqual(found, ["abcd"]); // не "abc" + "d"
});

test("glossaryMatches: спецсимволы экранируются", () => {
  const items = [{ term: "a(b)c", translation: "" }];
  assert.deepEqual(hits("x a(b)c y", items), ["a(b)c"]);
  const items2 = [{ term: "1+1", translation: "" }];
  assert.deepEqual(hits("2 1+1 2", items2), ["1+1"]);
});

test("glossaryMatches: NFC терминов при построении матчера", () => {
  // термин в NFD (e + combining acute) нормализуется в NFC é
  const items = [{ term: "e\u0301", translation: "" }];
  const m = matcherFor(items);
  assert.equal(m.total, 1);
  assert.deepEqual(hits("\u00e9", items), ["\u00e9"]);
});

test("glossaryMatches: пустые/без матчера", () => {
  assert.deepEqual(UICore.glossaryMatches("", matcherFor([])), []);
  assert.deepEqual(UICore.glossaryMatches("x", null), []);
  assert.equal(matcherFor(null).total, 0);
  assert.equal(matcherFor(undefined).total, 0);
});

test("glossaryMatches: слово-границы — нет обрывков внутри слов", () => {
  // короткие термины не матчатся внутри слов (не «от» в «кот»/«кто»)
  const items = [{ term: "от", translation: "" }];
  assert.deepEqual(hits("кот и кто", items), []);
  assert.deepEqual(hits("от кота", items), ["от"]);
  assert.deepEqual(hits("кто от кого", items), ["от"]);
});

test("glossaryMatches: склонения через нечёткий поиск (аналог _fuzzy_hit)", () => {
  // «Хунгу» в тексте: точное «Хунг» отклонено слово-границей,
  // нечёткий поиск (3-граммы, пересечение >= 0.7) ловит слово целиком
  const items = [{ term: "Хунг", translation: "" }];
  const text = "Он встретил Хунгу у реки.";
  const found = hits(text, items);
  assert.deepEqual(found, ["Хунгу"]);
  // CJK-термины без слово-границ — точное вхождение внутри слов допустимо
  const cjk = [{ term: "灵草", translation: "" }];
  assert.deepEqual(hits("这是灵草地", cjk), ["灵草"]);
});

test("glossaryMatches: «нити» от «нить» — 1 буква разницы", () => {
  // регрессия: жёсткое LCS >= 0.8 длины отсекало 3/4 = 0.75 даже при
  // пороге 0.1; теперь общая подстрока меряется по порогу
  const items = [{ term: "нить", translation: "" }];
  // n=1, порог 0.1 — пользовательские настройки редактора
  const loose = UICore.buildGlossaryMatcher(items, 1, 0.1);
  const ms = UICore.glossaryMatches("он тянул нити ковра", loose);
  assert.deepEqual(
    ms.map((m) => "он тянул нити ковра".slice(m.from, m.to)),
    ["нити"],
  );
  // и при дефолтном пороге 0.75 (3/4 грамм = 0.75 ровно) тоже
  const def = UICore.buildGlossaryMatcher(items, 1, 0.75);
  assert.deepEqual(
    UICore.glossaryMatches("нити", def).map((m) => "нити".slice(m.from, m.to)),
    ["нити"],
  );
  // «который» на «кот» — по-прежнему не матчится (лишние n-граммы)
  const кот = [{ term: "кот", translation: "" }];
  assert.deepEqual(hits("который", кот), []);
});

test("glossaryMatches: другая основа не считается падежом", () => {
  // регрессия: правило «термин — начало слова, хвост до 3 букв» принимало
  // любую основу — «Пол» находился в «польза»/«польский»/«поле». Хвост
  // обязан быть ОКОНЧАНИЕМ (падеж/число/род), а не новой основой.
  const items = [{ term: "Пол", translation: "" }];
  assert.deepEqual(hits("думая о пользе дела", items), []);
  assert.deepEqual(hits("польский язык", items), []);
  // а настоящие формы того же слова — находятся
  assert.deepEqual(hits("Полем боя стало поле", items), ["Полем", "поле"]);
  assert.deepEqual(hits("полом и полу", items), ["полом", "полу"]);
  // короткий термин не цепляет длинную другую основу
  const вес = [{ term: "вес", translation: "" }];
  assert.deepEqual(hits("весной", вес), []);
  assert.deepEqual(hits("вес и весом", вес), ["вес", "весом"]);
  const мир = [{ term: "мир", translation: "" }];
  assert.deepEqual(hits("мирра благоухала", мир), []);
  assert.deepEqual(hits("миром", мир), ["миром"]);
});

test("glossaryMatches: нормализация — пробелы и пунктуация", () => {
  // normalize_for_search: регистр/пробелы/пунктуация в термине и тексте — как есть;
  // диапазон в оригинале включает пропущенные пробелы и пунктыацию между словами.
  const items = [{ term: "Школа Света", translation: "" }];
  assert.deepEqual(hits("в школа   света, где он учился", items), [
    "школа   света",
  ]);
});

test("glossaryMatches: ngramSize настраивается (аналог --ner_ngram)", () => {
  const items = [{ term: "Хунг", translation: "" }];
  const m2 = UICore.buildGlossaryMatcher(items, 2);
  assert.equal(m2.ngramSize, 2);
  assert.deepEqual(m2.threshold, 0.75); // дефолт порога в редакторе
  const ms = UICore.glossaryMatches("Хунгу", m2);
  assert.deepEqual(
    ms.map((m) => "Хунгу".slice(m.from, m.to)),
    ["Хунгу"],
  );
});

test("glossaryMatches: threshold настраивается (аналог --ner_threshold)", () => {
  const items = [{ term: "хунгамар", translation: "" }];
  // слово «хунгамат»: совпадает 3 из 4 5-грамм = 0.75; общая подстрока 7 из 8.
  // порог 0.7 пропускает, 0.8 — отклоняет; точных вхождений в тексте нет.
  const loose = UICore.buildGlossaryMatcher(items, 5, 0.7);
  assert.equal(loose.threshold, 0.7);
  const msLoose = UICore.glossaryMatches("хунгамат", loose);
  assert.deepEqual(
    msLoose.map((m) => "хунгамат".slice(m.from, m.to)),
    ["хунгамат"],
  );
  const strict = UICore.buildGlossaryMatcher(items, 5, 0.8);
  assert.equal(strict.threshold, 0.8);
  assert.deepEqual(UICore.glossaryMatches("хунгамат", strict), []);
  // clamp: мусорные значения → дефолт 0.75; зажим в [0, 1]
  assert.equal(UICore.buildGlossaryMatcher(items, 5, NaN).threshold, 0.75);
  assert.equal(UICore.buildGlossaryMatcher(items, 5, 5).threshold, 1);
  assert.equal(UICore.buildGlossaryMatcher(items, 5, -2).threshold, 0);
});

test("buildGlossaryMatcher: чанки по ~2000 терминов", () => {
  const many = [];
  for (let i = 0; i < 4500; i++) many.push({ term: "t" + i, translation: "" });
  const m = matcherFor(many);
  assert.equal(m.total, 4500);
  assert.ok(m.chunks.length >= 3);
  // совпадение по термину из последнего чанка
  assert.deepEqual(hits("t4499", many), ["t4499"]);
});

test("dirEntries: пустой каталог (trailing '/') виден и не даёт пустых имён", () => {
  const files = ["prompts/", "prompts/translate.txt"];
  const root = UICore.dirEntries(files, "");
  assert.deepEqual(root.map((e) => `${e.dir ? "d:" : "f:"}${e.name}`).sort(), [
    "d:prompts",
  ]);
  // вход в пустой каталог (только "prompts/") — БЕЗ записи с пустым именем
  const inside = UICore.dirEntries(["prompts/"], "prompts");
  assert.deepEqual(inside, []);
  // только пустой каталог в корне
  const only = UICore.dirEntries(["empty/"], "");
  assert.deepEqual(
    only.map((e) => `${e.dir ? "d:" : "f:"}${e.name}`),
    ["d:empty"],
  );
});

test("glossarySentence: русское предложение вокруг термина", () => {
  const t = "Привет. Это термин здесь. Конец.";
  const from = t.indexOf("термин");
  const to = from + "термин".length;
  assert.equal(UICore.glossarySentence(t, from, to, 200), "Это термин здесь.");
});

test("glossarySentence: китайское предложение (。 — граница)", () => {
  const t = "即便他们真打赢了，苏星宇也不介意。下一句。";
  const from = t.indexOf("苏星宇");
  const to = from + "苏星宇".length;
  assert.equal(
    UICore.glossarySentence(t, from, to, 200),
    "即便他们真打赢了，苏星宇也不介意。",
  );
});

test("glossarySentence: закрывающие кавычки — часть предложения", () => {
  const t = "Он сказал: «Привет, Джон!» И ушёл.";
  const from = t.indexOf("Джон");
  const to = from + "Джон".length;
  assert.equal(
    UICore.glossarySentence(t, from, to, 200),
    "Он сказал: «Привет, Джон!»",
  );
});

test("glossarySentence: обрезка до maxLen, термин внутри окна", () => {
  const term = "TERM";
  const t = "а".repeat(180) + term + "б".repeat(180);
  const from = 180;
  const to = 184;
  const s = UICore.glossarySentence(t, from, to, 200);
  assert.ok(s.length <= 200);
  assert.ok(s.includes(term));
});

test("glossarySentence: дефолт maxLen 200, пустой текст", () => {
  assert.equal(UICore.glossarySentence("", 0, 0, 200), "");
  assert.equal(UICore.glossarySentence("нет границ", 0, 3), "нет границ");
});

test("nerCellText: объекты — пары «ключ: значение», пустые как строка", () => {
  assert.equal(UICore.nerCellText(null), "");
  assert.equal(UICore.nerCellText(undefined), "");
  assert.equal(UICore.nerCellText("Лин"), "Лин");
  assert.equal(UICore.nerCellText(12), "12");
  assert.equal(UICore.nerCellText({ a: 1 }), "a: 1");
  // после запятой обязателен пробел: иначе значение не переносится
  assert.equal(
    UICore.nerCellText({ translation: "мир", type: "other" }),
    "translation: мир, type: other",
  );
  assert.equal(UICore.nerCellText(["а", "б"]), "а, б");
  assert.equal(UICore.nerCellText({ a: { b: 2 } }), "a: b: 2");
});

test("nerStructJson: структура в поле правки — разбираемый JSON", () => {
  /* регресс: поле правки заполнялось подписью ячейки (nerCellText) —
     «Person (male): 5, Person (female): 2» — и коммит падал с «Невалидный
     JSON в «_votes_type»», какую бы запись ни правили */
  assert.equal(UICore.nerStructJson({ a: 1 }), '{"a":1}');
  assert.equal(
    UICore.nerStructJson({ "Person (male)": 5, "Person (unknown)": 2 }),
    '{"Person (male)":5,"Person (unknown)":2}',
  );
  assert.deepEqual(JSON.parse(UICore.nerStructJson(["а", "б"])), ["а", "б"]);
  assert.equal(UICore.nerStructJson({}), "{}");
  /* структуру без JSON-представления функция не выдаёт за текст */
  const cyc = {};
  cyc.self = cyc;
  assert.equal(UICore.nerStructJson(cyc), "");
});

test("nextNerSort: первый клик — убывание, повтор — возрастание", () => {
  assert.deepEqual(UICore.nextNerSort("count", "desc", "translation"), {
    field: "translation",
    dir: "desc",
  });
  assert.deepEqual(UICore.nextNerSort("translation", "desc", "translation"), {
    field: "translation",
    dir: "asc",
  });
  assert.deepEqual(UICore.nextNerSort("translation", "asc", "translation"), {
    field: "translation",
    dir: "desc",
  });
  assert.deepEqual(UICore.nextNerSort(null, null, null), {
    field: "count",
    dir: "desc",
  });
});

test("sortNerItems: count по убыванию, пустые в конце", () => {
  const items = [
    { term: "a", count: 1 },
    { term: "b", count: 10 },
    { term: "c" },
    { term: "d", count: 5 },
  ];
  assert.deepEqual(
    UICore.sortNerItems(items, "count", "desc").map((x) => x.term),
    ["b", "d", "a", "c"],
  );
  assert.deepEqual(
    UICore.sortNerItems(items, "count", "asc").map((x) => x.term),
    ["a", "d", "b", "c"],
  );
});

test("sortNerItems: строки по возрастанию", () => {
  const items = [{ term: "я" }, { term: "б" }, { term: "а" }];
  assert.deepEqual(
    UICore.sortNerItems(items, "term", "asc").map((x) => x.term),
    ["а", "б", "я"],
  );
});

test("filterNerItems: по выбранным полям и типу", () => {
  const items = [
    { term: "林凡", type: "person", translation: "Лин Фань", notes: "гг" },
    { term: "火", type: "skill", translation: "огонь", notes: "стихия" },
  ];
  assert.equal(UICore.filterNerItems(items, "гг", ["notes"], "").length, 1);
  assert.equal(UICore.filterNerItems(items, "гг", ["term"], "").length, 0);
  assert.equal(UICore.filterNerItems(items, "лин", null, "").length, 1);
  assert.equal(UICore.filterNerItems(items, "", ["term"], "skill").length, 1);
  assert.equal(
    UICore.filterNerItems(items, "огонь", ["translation"], "person").length,
    0,
  );
});

test("filterNerItems: пустые поля/типы и набор типов", () => {
  const items = [
    { term: "林凡", type: "person", translation: "Лин Фань", notes: "гг" },
    { term: "火", type: "skill", translation: "огонь", notes: "стихия" },
  ];
  assert.equal(UICore.filterNerItems(items, "гг", [], "").length, 0);
  assert.equal(UICore.filterNerItems(items, "", null, []).length, 0);
  assert.equal(
    UICore.filterNerItems(items, "", null, ["person", "skill"]).length,
    2,
  );
  assert.equal(UICore.filterNerItems(items, "", null, ["person"]).length, 1);
  assert.equal(UICore.filterNerItems(items, "", [], "").length, 2);
});

/* ── review-файлы проверок (список LLM-правок) ── */

test("parseReviewContent: объект с «правки»", () => {
  const text = JSON.stringify({
    created: "2024-01-01 10:00",
    input: "ner.json",
    entries: [{ term: "林凡", field: "translation", old: "А", new: "Б" }],
  });
  const p = UICore.parseReviewContent(text);
  assert.equal(p.ok, true);
  assert.equal(p.isArray, false);
  assert.equal(p.entries.length, 1);
  assert.equal(p.entries[0].term, "林凡");
  assert.equal(p.doc["input"], "ner.json");
});

test("parseReviewContent: legacy-массив", () => {
  const p = UICore.parseReviewContent('[{"term":"x","field":"type"}]');
  assert.equal(p.ok, true);
  assert.equal(p.isArray, true);
  assert.equal(p.entries.length, 1);
  assert.deepEqual(p.doc, p.entries);
});

test("parseReviewContent: невалидный JSON и не-список", () => {
  const bad = UICore.parseReviewContent("{не json");
  assert.equal(bad.ok, false);
  assert.equal(bad.entries.length, 0);
  const noList = UICore.parseReviewContent('{"created":"t"}');
  assert.equal(noList.ok, false);
  const empty = UICore.parseReviewContent("");
  assert.equal(empty.ok, false);
});

test("updateReviewEntry: точечная правка в объекте, «обновлён» обновляется", () => {
  const doc = {
    created: "2024-01-01 10:00",
    entries: [
      {
        term: "林凡",
        field: "translation",
        old: "А",
        new: "Б",
        status: "принять",
      },
      { term: "火", field: "notes", old: "В", new: "Г", status: "отклонить" },
    ],
  };
  const doc2 = UICore.updateReviewEntry(doc, 0, { status: "отклонить" }, false);
  assert.notEqual(doc2, doc); // иммутабельно
  assert.equal(doc2["entries"][0]["status"], "отклонить");
  assert.equal(doc2["entries"][1]["status"], "отклонить"); // соседняя цела
  assert.equal(doc["entries"][0]["status"], "принять"); // исходник не тронут
  assert.ok(doc2["updated"]); // проставлен текущий момент
  assert.equal(doc["updated"], undefined);
});

test("updateReviewEntry: legacy-массив и границы", () => {
  const arr = [{ term: "x" }];
  const arr2 = UICore.updateReviewEntry(arr, 0, { term: "y" }, true);
  assert.equal(arr2[0].term, "y");
  assert.equal(arr[0].term, "x");
  assert.equal(UICore.updateReviewEntry(arr, 5, { term: "z" }, true), null);
  assert.equal(UICore.updateReviewEntry(arr, -1, { term: "z" }, true), null);
  assert.equal(UICore.updateReviewEntry(null, 0, {}, true), null);
});

test("fileIcon: имя SVG-иконки по папке и расширению", () => {
  assert.equal(UICore.fileIcon({ dir: true }), "folder");
  assert.equal(UICore.fileIcon({ name: "ch.txt" }), "file-text");
  assert.equal(UICore.fileIcon({ name: "ner.json" }), "braces");
  assert.equal(UICore.fileIcon({ name: "README.md" }), "file-text");
  assert.equal(UICore.fileIcon({ name: "x.png" }), "image");
  assert.equal(UICore.fileIcon({ name: "x.epub" }), "book");
  assert.equal(UICore.fileIcon({ name: "noext" }), "file");
  assert.equal(UICore.fileIcon(null), "file");
  assert.equal(UICore.fileIcon({ name: "A.TXT" }), "file-text"); // lower
});

test("icon: svg-строка с путём, xmlns, aria-hidden и классом", () => {
  const svg = UICore.icon("folder");
  assert.match(svg, /^<svg xmlns="http:\/\/www.w3.org\/2000\/svg" class="icon"/);
  assert.ok(svg.includes('aria-hidden="true"'));
  assert.ok(svg.includes("<path")); // тело иконки не пустое
  assert.match(UICore.icon("folder", "extra"), /class="icon extra"/);
  // неизвестное имя — заглушка, не undefined/исключение
  assert.ok(UICore.icon("нет-такой").includes("<rect"));
});

test("icon: все имена из iconNames рендерятся без исключений", () => {
  for (const name of UICore.iconNames) {
    assert.ok(
      UICore.icon(name).startsWith("<svg"),
      `иконка ${name} не рендерится`,
    );
  }
});

test("relTime: пороги и абсолютное время в tooltip", () => {
  const now = Date.now() / 1000;
  assert.equal(UICore.relTime(now - 5), "только что");
  assert.equal(UICore.relTime(now - 5 * 60), "5 мин назад");
  assert.equal(UICore.relTime(now - 3 * 3600), "3 ч назад");
  assert.equal(UICore.relTime(now - 2 * 86400), "2 дн назад");
  // старше недели — дата ДД.ММ.ГГГГ
  assert.match(UICore.relTime(now - 30 * 86400), /^\d{2}\.\d{2}\.\d{4}$/);
  // мусор — пустая строка
  assert.equal(UICore.relTime(0), "");
  assert.equal(UICore.relTime(undefined), "");
  assert.match(UICore.relTimeAbs(now - 60), /\d{2}\.\d{2}\.\d{4}/);
  assert.equal(UICore.relTimeAbs(null), "");
});

test("removeReviewEntry: удаление в объекте, «обновлён» обновляется", () => {
  const doc = {
    created: "2024-01-01 10:00",
    entries: [
      { term: "林凡", field: "translation" },
      { term: "火", field: "notes" },
      { term: "刀", field: "type" },
    ],
  };
  const doc2 = UICore.removeReviewEntry(doc, 1, false);
  assert.notEqual(doc2, doc); // иммутабельно
  assert.equal(doc2["entries"].length, 2);
  assert.equal(doc2["entries"][1].term, "刀");
  assert.equal(doc["entries"].length, 3); // исходник не тронут
  assert.ok(doc2["updated"]);
  assert.equal(doc["updated"], undefined);
  assert.equal(UICore.removeReviewEntry(doc, 5, false), null); // вне диапазона
  assert.equal(UICore.removeReviewEntry(doc, -1, false), null);
  assert.equal(UICore.removeReviewEntry(null, 0, false), null);
});

test("removeReviewEntry: legacy-массив", () => {
  const arr = [{ term: "x" }, { term: "y" }, { term: "z" }];
  const arr2 = UICore.removeReviewEntry(arr, 0, true);
  assert.equal(arr2.length, 2);
  assert.equal(arr2[0].term, "y");
  assert.equal(arr.length, 3); // исходник не тронут
});

test("reviewSummary: подсчёт статусов", () => {
  const entries = [
    { status: "принять", applied: false },
    { status: "принять", applied: true },
    { status: "отклонить" },
    { applied: true }, // legacy без статуса
    { status: "принять" },
  ];
  assert.deepEqual(UICore.reviewSummary(entries), {
    total: 5,
    accepted: 3,
    rejected: 1,
    applied: 2,
  });
  assert.deepEqual(UICore.reviewSummary([]), {
    total: 0,
    accepted: 0,
    rejected: 0,
    applied: 0,
  });
  assert.deepEqual(UICore.reviewSummary(null), {
    total: 0,
    accepted: 0,
    rejected: 0,
    applied: 0,
  });
});

/* ── правки проверок: карточки терминов и фильтры ── */

const RV = [
  { term: "林凡", field: "type", stage: "Весь глоссарий",
    old: "A", new: "B", status: "принять" },
  { term: "林凡", field: "translation", stage: "Весь глоссарий",
    old: "X", new: "Y", status: "принять" },
  { term: "青云宗", field: "type", stage: "RAG",
    old: "C", new: "D", status: "отклонить" },
  { term: "火", action: "удаление", field: "", stage: "RAG",
    old: "тип=skill", new: "", applied: true },
];

test("reviewGroups: один термин — одна карточка, у перевода — правка на карточку", () => {
  const g = UICore.reviewGroups(RV, "ner");
  assert.equal(g.length, 3);
  assert.equal(g[0].title, "林凡");
  assert.equal(g[0].entries.length, 2);
  // индекс записи — глобальный, по нему правят файл
  assert.deepEqual(g[0].entries.map((x) => x.index), [0, 1]);
  assert.deepEqual(g[2].entries.map((x) => x.index), [3]);

  // проверка перевода: заголовок у каждой правки свой, терминов там нет
  const tcl = [
    { chapter: 3, type: "typo", old: "а", new: "б" },
    { chapter: 3, type: "typo", old: "в", new: "г" },
  ];
  const tg = UICore.reviewGroups(tcl, "tcl");
  assert.equal(tg.length, 2);
  assert.deepEqual(tg.map((x) => x.entries[0].index), [0, 1]);
});

test("reviewAxisValues: набор чипсов задаёт файл, удаление — своей подписью", () => {
  const axes = UICore.reviewAxes("ner");
  assert.deepEqual(axes.map((a) => a.key), ["field", "stage"]);
  const field = UICore.reviewAxisValues(RV, axes[0]);
  assert.deepEqual(field.map((v) => v.value), ["type", "translation", ""]);
  assert.deepEqual(field.map((v) => v.count), [2, 1, 1]);
  assert.equal(field[2].label, "удаление термина");
  const stage = UICore.reviewAxisValues(RV, axes[1]);
  assert.deepEqual(stage.map((v) => v.value), ["Весь глоссарий", "RAG"]);
});

test("reviewFilterMatch: пустой фильтр — видно всё, спрятанное — нет", () => {
  const e = RV[0];
  assert.equal(UICore.reviewFilterMatch(e, {}, "ner"), true);
  assert.equal(UICore.reviewFilterMatch(e, { field: [] }, "ner"), true);
  // ось хранит СКРЫТЫЕ значения: тип спрятан — правка поля type уходит
  assert.equal(UICore.reviewFilterMatch(e, { field: ["type"] }, "ner"), false);
  assert.equal(UICore.reviewFilterMatch(RV[1], { field: ["type"] }, "ner"),
    true);
  // удаление прячется своим значением — пустой строкой
  assert.equal(UICore.reviewFilterMatch(RV[3], { field: [""] }, "ner"), false);
  // этап — та же механика
  assert.equal(UICore.reviewFilterMatch(e, { stage: ["RAG"] }, "ner"), true);
  assert.equal(UICore.reviewFilterMatch(RV[2], { stage: ["RAG"] }, "ner"),
    false);
});

test("reviewFilterMatch: статус — один выбор, корзины не пересекаются", () => {
  assert.equal(UICore.reviewFilterMatch(RV[0], { status: "принять" }, "ner"),
    true);
  assert.equal(UICore.reviewFilterMatch(RV[2], { status: "принять" }, "ner"),
    false);
  // применённая запись не считается «принятой» — она уже отработана
  assert.equal(UICore.reviewStatusBucket(RV[3]), "применено");
  assert.equal(UICore.reviewStatusBucket({ status: "принять", applied: true }),
    "применено");
  assert.equal(UICore.reviewStatusBucket({ status: "принять" }), "принять");
  assert.equal(UICore.reviewStatusBucket({ status: "отклонить" }), "отклонить");
  assert.equal(UICore.reviewStatusBucket({}), "нерешённые");
  assert.equal(UICore.reviewStatusBucket({ status: "принять " }), "принять");
});

test("reviewVisibleGroups: в карточке остаются только прошедшие фильтр поля", () => {
  const g = UICore.reviewVisibleGroups(RV, { field: ["translation"] }, "ner");
  // «林凡» остаётся, но только с полем type; тип у «青云宗» и удаление «火» —
  // видны (скрыт только translation)
  assert.equal(g[0].entries.length, 1);
  assert.equal(g[0].entries[0].entry.field, "type");
  assert.equal(g[0].all.length, 2); // полный состав термина помнит карточка
  const none = UICore.reviewVisibleGroups(
    RV, { field: ["type", "translation", ""] }, "ner");
  assert.equal(none.length, 0);
});

test("reviewGroupStatus: общий статус термина или «смешанные»", () => {
  assert.equal(UICore.reviewGroupStatus([RV[0], RV[1]]), "принять");
  assert.equal(UICore.reviewGroupStatus([RV[0], RV[2]]), "смешанные");
  assert.equal(UICore.reviewGroupStatus([RV[3]]), "применено");
  assert.equal(UICore.reviewGroupStatus([]), "");
  // у удаления «применено» перевешивает: запись уже отработала
  assert.equal(
    UICore.reviewGroupStatus([
      { status: "принять", applied: true },
      { status: "принять", applied: true },
    ]),
    "применено",
  );
});

test("isCjkString: суррогатные пары не считаются за 2 символа (B9)", () => {
  // U+20000 (CJK Ext B) — суррогатная пара в UTF-16
  const s = "𠀀𠀀𠀀" + "abc"; // 3 CJK + 3 латиницы
  assert.equal(UICore.isCjkString(s), false); // ровно 0.5 — не > 0.5
  const s2 = "𠀀𠀀𠀀𠀀" + "ab"; // 4 CJK + 2 латиницы
  assert.equal(UICore.isCjkString(s2), true);
  assert.equal(UICore.isCjkString(""), false);
  assert.equal(UICore.isCjkString(null), false);
});

/* ── menuPlacement: координаты выпадающего меню («⋮») ──────────────
   Регрессия «у нижней строки списка меню не влезает»: dropdown был
   position:absolute от строки и обрезался карточкой .files-list
   (overflow:hidden) — из 78px меню оставалось ~4px. Меню теперь
   position:fixed, геометрию считает эта функция. */
const BTN = { top: 100, bottom: 140, left: 900, right: 940 }; // правый край 940

test("menuPlacement: есть место снизу — меню под кнопкой", () => {
  const p = UICore.menuPlacement(BTN, { width: 180, height: 78 }, { width: 1280, height: 800 });
  assert.equal(p.top, 146); // bottom(140) + gap(6)
  assert.equal(p.left, 760); // right(940) - width(180)
});

test("menuPlacement: снизу не влезает — разворот вверх", () => {
  // кнопка у самого низа окна: меню вниз вылезло бы за вьюпорт
  const btn = { top: 640, bottom: 669, left: 900, right: 940 };
  const p = UICore.menuPlacement(btn, { width: 180, height: 78 }, { width: 1280, height: 720 }, 6, 8);
  assert.equal(p.top, 556); // 640 - 6 - 78
  assert.ok(p.top + 78 <= 720, "меню целиком над кнопкой и в окне");
});

test("menuPlacement: узкое окно — меню не вылезает за левый край", () => {
  const btn = { top: 100, bottom: 140, left: 20, right: 60 };
  const p = UICore.menuPlacement(btn, { width: 180, height: 78 }, { width: 200, height: 800 }, 6, 8);
  assert.equal(p.left, 8); // прижато к левому краю (edge)
  assert.ok(p.left + 180 <= 200);
});

test("menuPlacement: меню шире окна — не уходит за правый край", () => {
  const btn = { top: 100, bottom: 140, left: 300, right: 340 };
  const p = UICore.menuPlacement(btn, { width: 500, height: 78 }, { width: 360, height: 800 }, 6, 8);
  assert.equal(p.left, 8);
  assert.ok(p.left + 500 > 360, "объективно шире окна, но левый край в пределах");
});

test("menuPlacement: очень низкое окно — верхний край не отрицательный", () => {
  const btn = { top: 40, bottom: 69, left: 10, right: 50 };
  const p = UICore.menuPlacement(btn, { width: 180, height: 200 }, { width: 400, height: 240 }, 6, 8);
  assert.ok(p.top >= 0 && p.top + 200 <= 240, `меню прижато в окне: ${JSON.stringify(p)}`);
});

test("menuPlacement: дефолтные gap/edge применяются без аргументов", () => {
  const btn = { top: 0, bottom: 100, left: 0, right: 200 };
  const p = UICore.menuPlacement(btn, { width: 100, height: 50 }, { width: 1000, height: 1000 });
  assert.deepEqual(p, { top: 106, left: 100 });
});

test("faviconHref: без запусков — глиф, с запусками — счётчик", () => {
  const plain = decodeURIComponent(UICore.faviconHref(0));
  assert.ok(plain.startsWith("data:image/svg+xml,"), "это data URI");
  assert.ok(plain.includes("⇄"), "покое — глиф");
  assert.ok(decodeURIComponent(UICore.faviconHref(3)).includes(">3<"));
  assert.ok(decodeURIComponent(UICore.faviconHref(42)).includes("9+"));
});

test("parseRoute: браузерный хэш раскодировывается", () => {
  // браузер хранит location.hash закодированным: кириллическое имя книги
  // обязано приходить в вид раскодированным, иначе оно уходит в API дважды
  // закодированным и проект не находится (404)
  assert.deepEqual(UICore.parseRoute("#/project/TMP/%D0%9A%D0%BD%D0%B8%D0%B3%D0%B0/files"), {
    view: "project",
    rest: ["TMP", "Книга", "files"],
  });
  assert.deepEqual(UICore.parseRoute("#/project/TMP/%D0%9A%D0%BD%D0%B8%D0%B3%D0%B0"), {
    view: "project",
    rest: ["TMP", "Книга"],
  });
  // латиника и пробелы
  assert.deepEqual(UICore.parseRoute("#/project/ACTIVE/My%20Book/run"), {
    view: "project",
    rest: ["ACTIVE", "My Book", "run"],
  });
});

test("parseRoute: битую %-последовательность не выдумываем", () => {
  assert.deepEqual(UICore.parseRoute("#/project/TMP/%ZZ/files"), {
    view: "project",
    rest: ["TMP", "%ZZ", "files"],
  });
});

test("toggleUiTheme: тёмная ↔ светлая", () => {
  assert.equal(UICore.toggleUiTheme("dark"), "light");
  assert.equal(UICore.toggleUiTheme("light"), "dark");
  // всё, что не «light», — тёмная (тема по умолчанию)
  assert.equal(UICore.toggleUiTheme(""), "light");
  assert.equal(UICore.toggleUiTheme(undefined), "light");
});

test("nerAction: действие ner-правки — патч поля или удаление термина", () => {
  // старые файлы и правки поля ключа action не имеют
  assert.equal(UICore.nerAction({ term: "A", field: "type" }), "патч");
  assert.equal(UICore.nerAction({}), "патч");
  assert.equal(UICore.nerAction(null), "патч");
  assert.equal(UICore.nerAction({ action: "чушь" }), "патч");
  // удаление: русский канон и английские написания ответа LLM
  assert.equal(UICore.nerAction({ action: "удаление" }), "удаление");
  assert.equal(UICore.nerAction({ action: " Удалить " }), "удаление");
  assert.equal(UICore.nerAction({ action: "delete" }), "удаление");
  assert.equal(UICore.nerAction({ action: "REMOVE" }), "удаление");
});

test("markWhitespace: пробелы видимы, структура строк сохранена", () => {
  assert.equal(UICore.markWhitespace("a  b"), "a··b");
  // таб — текстовая метка: ⇥ часть шрифтов рисует стрелкой
  assert.equal(UICore.markWhitespace("\ttab"), "\\ttab");
  // перевод строки остаётся переводом — метка пишется перед ним
  assert.equal(UICore.markWhitespace("a\n\nb"), "a⏎\n⏎\nb");
  assert.equal(UICore.markWhitespace("a\r\nb"), "a␍⏎\nb");
  assert.equal(UICore.markWhitespace(""), "");
  assert.equal(UICore.markWhitespace(null), "");
  assert.equal(UICore.markWhitespace(7), "7");
});

/* ── замок термина (_locked): состояние, а не столбец таблицы ───────── */
test("nerIsLocked: только «истина», старые файлы без поля — разблокированы", () => {
  assert.equal(UICore.nerIsLocked({ term: "A" }), false);
  assert.equal(UICore.nerIsLocked({ _locked: false }), false);
  assert.equal(UICore.nerIsLocked({ _locked: true }), true);
  assert.equal(UICore.nerIsLocked({ _locked: "1" }), true);
  assert.equal(UICore.nerIsLocked({ _locked: "да" }), true);
  assert.equal(UICore.nerIsLocked({ _locked: "0" }), false);
  assert.equal(UICore.nerIsLocked(null), false);
});

test("nerSetLocked: снятый замок убирает ключ, счётчик считает записи", () => {
  const it = { term: "A" };
  UICore.nerSetLocked(it, true);
  assert.equal(it._locked, true);
  UICore.nerSetLocked(it, false);
  assert.equal("_locked" in it, false);
  assert.equal(
    UICore.nerLockedCount([{ _locked: true }, { term: "B" }, null]),
    1,
  );
});

test("filterNerItems: фильтр по замку и поиск не видит служебное поле", () => {
  const items = [
    { term: "林凡", type: "person", translation: "Лин Фань", _locked: true },
    { term: "火", type: "skill", translation: "огонь" },
  ];
  assert.equal(UICore.filterNerItems(items, "", null, null).length, 2);
  assert.equal(
    UICore.filterNerItems(items, "", null, null, "locked").length,
    1,
  );
  assert.equal(
    UICore.filterNerItems(items, "", null, null, "locked")[0].term,
    "林凡",
  );
  assert.equal(
    UICore.filterNerItems(items, "", null, null, "unlocked")[0].term,
    "火",
  );
  // поиск по всем полям не цепляет значение замка
  assert.equal(UICore.filterNerItems(items, "true", null, "").length, 0);
});

/* ── язык подсветки файла (UICore.editorLang) ── */

test("editorLang: промпты и логи — по выбору, остальное — по расширению", () => {
  // расширение у промптов то же самое .txt: язык для них выбирает человек
  // (дефолт — собственный язык разметки промптов, собирается в бандле CM)
  assert.equal(UICore.EDITOR_SETTINGS.langPrompt, "prompt", "дефолт промптов");
  assert.equal(UICore.editorLang("prompts/ner_prompt.txt"), "prompt");
  assert.equal(UICore.editorLang("ner_prompt.txt", true), "prompt");
  assert.equal(UICore.editorLang("chapters/00000_1_Глава 1/polished.txt"), "txt");
  assert.equal(UICore.editorLang("notes.md"), "md");
  assert.equal(UICore.editorLang("tmp/report.json"), "json");
  assert.equal(UICore.editorLang("meta.yaml"), "yaml");
  // выбор пользователя — уже ИМЯ языка бандла, таблица расширений его минует
  UICore.EDITOR_SETTINGS.langPrompt = "xml";
  assert.equal(UICore.editorLang("prompts/ner_prompt.txt"), "xml",
    "промту дали XML");
  UICore.EDITOR_SETTINGS.langLog = "properties";
  assert.equal(UICore.editorLang("logs/pipeline.log"), "properties",
    "логам дали properties");
  UICore.EDITOR_SETTINGS.langPrompt = "prompt";
  UICore.EDITOR_SETTINGS.langLog = "text";
});

test("EDITOR_LANGS: значения — имена языков бандла, первый — plain text", () => {
  const vals = UICore.EDITOR_LANGS.map((o) => o.v);
  assert.deepEqual(vals, ["text", "prompt", "markdown", "html", "xml", "json",
    "yaml", "properties", "python"]);
  assert.equal(UICore.EDITOR_LANGS[0].label, "plain text");
});

/* ── разметка промпта в предпросмотре запроса (UICore.promptParts) ── */

test("promptParts: теги секций, подстановки и ключи JSON", () => {
  const parts = UICore.promptParts(
    '<system>\nТы переводчик.\n</system>\n'
    + '<user>\n=== ГЛОССАРИЙ ===\n{ner_block}\n{original_text}\n</user>\n'
    + '{"term": "火", "count": 3}\n',
  );
  const marked = parts.filter((p) => p.cls);
  assert.deepEqual(
    marked.map((p) => [p.cls, p.text]),
    [
      ["pv-tag", "<system>"],
      ["pv-tag", "</system>"],
      ["pv-tag", "<user>"],
      ["pv-var", "{ner_block}"],
      ["pv-var", "{original_text}"],
      ["pv-tag", "</user>"],
      ["pv-key", '"term"'],
      ["pv-key", '"count"'],
    ],
  );
  // склейка частей — исходный текст без потерь
  assert.equal(parts.map((p) => p.text).join(""), parts.join("").length
    ? parts.map((p) => p.text).join("") : "");
  assert.equal(
    parts.map((p) => p.text).join(""),
    '<system>\nТы переводчик.\n</system>\n<user>\n=== ГЛОССАРИЙ ===\n'
    + '{ner_block}\n{original_text}\n</user>\n{"term": "火", "count": 3}\n',
  );
});

test("promptParts: что не размечается", () => {
  // одиночная «<» без имени, подстановка с заглавной буквы, значение JSON
  for (const t of ["a < b", "<1>", "{Ner}", '":"', "{}", ""]) {
    const parts = UICore.promptParts(t);
    assert.ok(
      parts.every((p) => !p.cls),
      `лишняя разметка в «${t}»: ${JSON.stringify(parts)}`,
    );
    assert.equal(parts.length ? parts.map((p) => p.text).join("") : "", t);
  }
  // пустой ввод — пустой список, а не [{text:"",cls:""}]
  assert.deepEqual(UICore.promptParts(""), []);
  assert.deepEqual(UICore.promptParts(null), []);
});

test("promptParts: ключ JSON — то, что до двоеточия", () => {
  const parts = UICore.promptParts('{"type": "other"}\nterm: count\n');
  // значение («"other"») ключом не считается: после него нет двоеточия
  assert.deepEqual(
    parts.filter((p) => p.cls).map((p) => [p.cls, p.text]),
    [["pv-key", '"type"']],
  );
});

// ── видимость полей по режиму стадии (те же данные, что у argv запусков) ──
test("fieldApplies: when — значение поля режима из формы", () => {
  const f = { name: "names_min_count", when: [["action", ["3", "4"]]] };
  assert.equal(UICore.fieldApplies(f, { action: "3" }), true);
  assert.equal(UICore.fieldApplies(f, { action: "1" }), false);
  // режим не выбран (пусто или нет поля) — резать поля нечем
  assert.equal(UICore.fieldApplies(f, {}), true);
  assert.equal(UICore.fieldApplies(f, { action: "   " }), true);
});

test("fieldApplies: булево поле режима сравнивается как 1/0", () => {
  const f = {
    name: "chunk_mask",
    when_any: [["mode", ["chunk"]], ["rename_chapters", ["1"]]],
  };
  assert.equal(
    UICore.fieldApplies(f, { mode: "toc", rename_chapters: true }), true);
  assert.equal(
    UICore.fieldApplies(f, { mode: "toc", rename_chapters: false }), false);
  assert.equal(
    UICore.fieldApplies(f, { mode: "chunk", rename_chapters: false }), true);
});

test("fieldApplies: несколько условий when — И", () => {
  const toc = { name: "toc", when: [["format", ["md"]], ["as_chapter", ["0"]]] };
  assert.equal(UICore.fieldApplies(toc, { format: "md", as_chapter: false }), true);
  assert.equal(
    UICore.fieldApplies(toc, { format: "rulate-md", as_chapter: false }), false);
  assert.equal(UICore.fieldApplies(toc, { format: "md", as_chapter: true }), false);
});

test("fieldApplies: when_set — перечисленные поля непустые", () => {
  const co = { name: "co_occurrence_top", when_set: ["co_occurrence_pairs"] };
  assert.equal(UICore.fieldApplies(co, { co_occurrence_pairs: "Person:Person" }), true);
  assert.equal(UICore.fieldApplies(co, { co_occurrence_pairs: "   " }), false);
});

test("fieldApplies: пустые условия — касается всегда", () => {
  assert.equal(UICore.fieldApplies({ name: "x" }, {}), true);
  assert.equal(UICore.fieldApplies({ name: "x", when: [] }, { action: "1" }), true);
  assert.equal(UICore.fieldApplies({ name: "x", when_set: [] }, {}), true);
});

/* ── состояние вкладок проекта: один ключ на книгу ───────────────────── */
function memStore(init) {
  const m = new Map(Object.entries(init || {}));
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(String(k), String(v)),
    removeItem: (k) => m.delete(k),
    _dump: () => Object.fromEntries(m),
  };
}

test("projectPrefKey: ключ — один на проект", () => {
  assert.equal(UICore.projectPrefKey("ACTIVE", "Книга"), "nmTab:ACTIVE/Книга");
});

test("projectPrefs: пустое хранилище — пустая вкладка, запись мержится", () => {
  const store = memStore();
  const p = UICore.projectPrefs(store, "ACTIVE", "Книга");
  assert.deepEqual(p.get("editor"), {});
  p.set("editor", { chapter: "00000_1_x" });
  p.set("editor", { mode: "one" });
  assert.deepEqual(p.get("editor"), { chapter: "00000_1_x", mode: "one" });
  assert.deepEqual(JSON.parse(store._dump()["nmTab:ACTIVE/Книга"]), {
    editor: { chapter: "00000_1_x", mode: "one" },
  });
});

test("projectPrefs: соседняя вкладка и соседний проект не затираются", () => {
  const store = memStore();
  const a = UICore.projectPrefs(store, "ACTIVE", "Книга");
  a.set("chapters", { type: "translated" });
  a.set("page", { view: "chapters" });
  const b = UICore.projectPrefs(store, "HOLD", "Другая");
  b.set("page", { view: "logs" });
  assert.deepEqual(a.get("page"), { view: "chapters" });
  assert.deepEqual(a.get("chapters"), { type: "translated" });
  assert.deepEqual(b.get("page"), { view: "logs" });
  assert.deepEqual(b.get("chapters"), {});
});

test("projectPrefs: битый JSON и недоступное хранилище не мешают", () => {
  const broken = UICore.projectPrefs(
    memStore({ "nmTab:ACTIVE/Книга": "{это не json" }), "ACTIVE", "Книга",
  );
  assert.deepEqual(broken.get("editor"), {});
  broken.set("editor", { mode: "two" });
  assert.deepEqual(broken.get("editor"), { mode: "two" });
  /* хранилище недоступно: чтение — пустое, запись не бросает и не теряется
     совсем — состояние живёт в памяти до перезагрузки страницы */
  let writes = 0;
  const dead = UICore.projectPrefs({
    getItem: () => { throw new Error("SecurityError"); },
    setItem: () => { writes++; throw new Error("QuotaExceeded"); },
  }, "ACTIVE", "Книга");
  assert.deepEqual(dead.get("editor"), {});
  dead.set("editor", { mode: "one" });
  assert.deepEqual(dead.get("editor"), { mode: "one" });
  assert.equal(writes, 1, "попытка записи была — и осталась единственной");
});

test("checkScale: полоса допуска, медиана и точки глав", () => {
  /* пара из боевого отчёта: медиана 0.999, MAD 0.4%, K=25 → 0.90–1.10 */
  const stat = { lo: 0.9, hi: 1.1, median: 0.999 };
  const sc = UICore.checkScale(stat, [0.9, 0.87, 1.4], null, null);
  /* домен расширен точками и запасом по краям: выпавшая глава не на рамке */
  assert.ok(sc.min < 0.87 && sc.max > 1.4, `${sc.min}..${sc.max}`);
  /* полоса начинается на нижней границе и тянется до верхней */
  assert.ok(sc.bandLeft > 0 && sc.bandLeft < sc.bandLeft + sc.bandWidth);
  /* ширина полосы = ровно допуск в долях домена */
  assert.ok(
    Math.abs(sc.bandWidth - 0.2 / (sc.max - sc.min)) < 1e-3,
    `${sc.bandWidth} vs ${0.2 / (sc.max - sc.min)}`,
  );
  /* штрих медианы — внутри полосы */
  assert.ok(sc.midPos > sc.bandLeft && sc.midPos < sc.bandLeft + sc.bandWidth);
  /* точки отсортированы слева направо, вне допуска помечены */
  assert.deepEqual(sc.points.map((p) => p.value), [0.87, 0.9, 1.4]);
  assert.deepEqual(sc.points.map((p) => p.out), [true, false, true]);
  assert.ok(sc.points[0].pos < sc.points[1].pos);
});

test("checkScale: без чисел допуска — null, старый отчёт не роняет вкладку", () => {
  assert.equal(UICore.checkScale(null, [1], null, null), null);
  assert.equal(UICore.checkScale({ lo: null, hi: null }, [], null, null), null);
  /* границы схлопнуты или перевёрнуты — рисовать нечего */
  assert.equal(UICore.checkScale({ lo: 1, hi: 1 }, [], null, null), null);
  assert.equal(UICore.checkScale({ lo: 2, hi: 1 }, [], null, null), null);
});

test("checkScale: узкий допуск соседних стадий различим", () => {
  /* нынешний отчёт: polished vs redacted — медиана 1.000, разброс 0.13%,
     K=7 → отношение 0.991–1.009. Допуск уже одного процента, но полоса
     обязана остаться полосой, а не точкой: иначе шкала ничего не значит. */
  const stat = { lo: 0.991, hi: 1.009, median: 1.0 };
  const sc = UICore.checkScale(stat, [1.2], null, null);
  assert.ok(sc.bandWidth > 0.001, `ширина полосы ${sc.bandWidth}`);
  assert.ok(sc.midPos > sc.bandLeft && sc.midPos < sc.bandLeft + sc.bandWidth);
  assert.equal(sc.points.length, 1);
  assert.equal(sc.points[0].out, true, "1.2 вне 0.991–1.009");
});

test("checkScale: пустые точки — шкала из одного допуска", () => {
  const sc = UICore.checkScale({ lo: 0.98, hi: 1.02, median: 1.0 }, [], null, null);
  assert.deepEqual(sc.points, []);
  assert.ok(sc.min < 0.98 && sc.max > 1.02);
  assert.ok(Math.abs(sc.midPos - (sc.bandLeft + sc.bandWidth / 2)) < 0.01);
});

test("checkScale: точка сама называет, мимо ли она (границы округлены)", () => {
  /* в отчёте напечатано «вне 0.90–1.10», а отношение показано как 0.90:
     по числу не отличить промах на 0.2% от попадания ровно на границу */
  const sc = UICore.checkScale(
    { lo: 0.9, hi: 1.1, median: 0.999 },
    [{ value: 0.9, out: true }, { value: 1.0 }],
    null,
    null,
  );
  assert.deepEqual(sc.points.map((p) => p.out), [true, false]);
  /* без явного out работает сравнение с границами */
  const plain = UICore.checkScale({ lo: 0.9, hi: 1.1, median: 1 }, [0.5, 1.0], null, null);
  assert.deepEqual(plain.points.map((p) => p.out), [true, false]);
  /* мусор в точках не ломает шкалу */
  const junk = UICore.checkScale({ lo: 0.9, hi: 1.1, median: 1 }, [null, "x", 1.0], null, null);
  assert.equal(junk.points.length, 1);
});

test("checkScale: пустой min/max не превращается в ноль", () => {
  /* Number(null) === 0: без защиты домен уезжал до 0 и полоса занимала
     правую четверть пустой шкалы (видно на скриншоте прогона) */
  const sc = UICore.checkScale({ lo: 0.9, hi: 1.1, median: 0.999 }, [], null, null);
  assert.ok(sc.min > 0.88, `min=${sc.min}`);
  assert.ok(sc.max < 1.12, `max=${sc.max}`);
  /* полоса занимает почти всю шкалу домена */
  assert.ok(sc.bandWidth > 0.7, `bandWidth=${sc.bandWidth}`);
  /* переданный min/max работает как раньше */
  const wide = UICore.checkScale({ lo: 0.9, hi: 1.1, median: 1 }, [], 0.5, 1.5);
  assert.ok(wide.min < 0.5 && wide.max > 1.5);
});

/* ── снимок состояния книги: до ответа сервера честно «не загружен» ── */
function fakeBookStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(String(k), String(v)),
    removeItem: (k) => m.delete(k),
  };
}

test("bookState: значение из браузера не выдаётся за книгу после ответа", () => {
  /* на двух устройствах форма запусков показывала разные диапазоны глав:
     книга ещё не ответила, форма брала зеркало браузера и собиралась один раз */
  const storage = fakeBookStorage();
  const prev = globalThis.localStorage;
  globalThis.localStorage = storage;
  try {
    storage.setItem("runVals:ACTIVE/Книга:ner", '{"start":"1","end":"9"}');
    const book = UICore.bookState(null, "ACTIVE", "Книга");
    assert.equal(book.loaded(), false, "до ответа снимок не загружен");
    assert.equal(book.load().then !== undefined, true);
  } finally {
    globalThis.localStorage = prev;
  }
});

test("bookState: сервер — истина, зеркало — только до его ответа", async () => {
  const storage = fakeBookStorage();
  const prev = globalThis.localStorage;
  globalThis.localStorage = storage;
  try {
    storage.setItem("runVals:ACTIVE/Книга:ner", '{"start":"1","end":"9"}');
    let release = null;
    const book = UICore.bookState({
      load: () => new Promise((r) => { release = () => r({ data: {} }); }),
      save: () => Promise.resolve({ ok: true }),
    }, "ACTIVE", "Книга");
    const done = book.load();
    // до ответа книги читается зеркало браузера: страница не пустует
    assert.match(String(book.store.getItem("runVals:ACTIVE/Книга:ner")), /"1"/);
    release();
    await done;
    assert.equal(book.loaded(), true);
    /* книга ответила и ключа в ней нет — значит его нет: иначе сброс
       настроек на другом устройстве «не срабатывал» бы */
    assert.equal(book.store.getItem("runVals:ACTIVE/Книга:ner"), null);
  } finally {
    globalThis.localStorage = prev;
  }
});

/* ── очередь автосохранения «Настроек»: правка во время запроса ── */
test("saveQueue: take очищает очередь сразу — правка в полёте не теряется", () => {
  /* поле на экране показывало 262144, а на диск уходило пустое: правка,
     набранная пока PUT в полёте, удалялась на его ответе */
  const q = UICore.saveQueue(10);
  q.put("MAX_TOKENS", "");
  const sent = q.take();          // запрос ушёл с пустым значением
  assert.deepEqual(sent, { values: { MAX_TOKENS: "" }, keys: ["MAX_TOKENS"],
                           profile: undefined });
  q.put("MAX_TOKENS", "262144");  // набор, пока запрос в полёте
  assert.equal(q.has(), true, "правка в полёте осталась в очереди");
  assert.deepEqual(q.take(), { values: { MAX_TOKENS: "262144" },
                              keys: ["MAX_TOKENS"], profile: undefined });
  assert.equal(q.has(), false);
});

test("saveQueue: владелец правки едет вместе с ней", () => {
  /* поле изменили на General, а за паузу ввода переключились в профиль:
     запись уходила в профиль, а общий .env так и оставался прежним */
  const q = UICore.saveQueue(10);
  q.put("MAX_TOKENS", "300", "general");
  assert.equal(q.pending().MAX_TOKENS, "300", "pending отдаёт значения");
  assert.equal(q.take().profile, "general");

  const q2 = UICore.saveQueue(10);
  q2.put("MAX_TOKENS", "300", "general");
  q2.put("MAX_TOKENS", "900", "p1");  // тот же ключ, другой профиль
  assert.deepEqual(q2.take(), { values: { MAX_TOKENS: "900" },
                               keys: ["MAX_TOKENS"], profile: "p1" });

  // две пачки на разные профили — двумя заходами, а не одним запросом
  const q3 = UICore.saveQueue(10);
  q3.put("MAX_TOKENS", "300", "general");
  q3.put("THREADS", "8", "p1");
  const first = q3.take();
  assert.equal(first.profile, "general");
  assert.deepEqual(first.keys, ["MAX_TOKENS"]);
  assert.equal(q3.holds("THREADS"), true, "чужая пачка ждёт своего захода");
  const second = q3.take();
  assert.equal(second.profile, "p1");
  assert.deepEqual(second.values, { THREADS: "8" });
  assert.equal(q3.has(), false);

  // ошибка возвращает значения вместе с их владельцем
  const q4 = UICore.saveQueue(10);
  q4.put("THREADS", "8", "p1");
  const sent = q4.take();
  q4.back(sent.values, sent.profile);
  assert.equal(q4.take().profile, "p1");
});

test("saveQueue: holds отличает занятый ключ от занятой очереди", () => {
  /* перерисовка поля не должна терять из виду его несохранённую правку;
     ключ ищется точный (имена полей стадий не уникальны) */
  const q = UICore.saveQueue(10);
  q.put("MAX_TOKENS", "300", "general");
  assert.equal(q.holds("MAX_TOKENS"), true);
  assert.equal(q.holds("THREADS"), false);
  q.take();
  assert.equal(q.holds("MAX_TOKENS"), false);
});

test("saveQueue: перерисовка вкладки берёт несохранённую правку", () => {
  /* правка набрана перед сменой субвкладки: до ответа PUT в model.groups ещё
     старое значение, и без подмены поле показывало бы прошлое, а набранное
     исчезало с экрана до записи (в .env оно при этом доезжало) */
  const q = UICore.saveQueue(10);
  assert.equal(q.value("TEMPERATURE", "general"), undefined,
    "пустая очередь значения не подменяет");
  q.put("TEMPERATURE", "7777", "general");
  assert.equal(q.value("TEMPERATURE"), "7777");
  assert.equal(q.value("TEMPERATURE", "general"), "7777");
  // чужой профиль не должен видеть правку General
  assert.equal(q.value("TEMPERATURE", "p1"), undefined,
    "правка чужого профиля не появляется в поле");
  const sent = q.take();
  assert.equal(sent.profile, "general");
  // пока идёт запрос, значение уже не в очереди — поле покажет этот же ответ
  assert.equal(q.value("TEMPERATURE", "general"), undefined);
});

test("saveQueue: ошибка возвращает значения, новые правки важнее", () => {
  const q = UICore.saveQueue(10);
  q.put("THREADS", "8");
  const sent = q.take();
  q.back(sent.values);            // запрос не прошёл
  assert.deepEqual(q.pending(), { THREADS: "8" });
  q.put("THREADS", "12");
  q.back({ THREADS: "8" });       // старое значение не затирает новое
  assert.deepEqual(q.pending(), { THREADS: "12" });
});

test("saveQueue: begin терпит одного писца", () => {
  const q = UICore.saveQueue(10);
  assert.equal(q.begin(), true);
  assert.equal(q.begin(), false, "вторая запись не должна идти параллельно");
  q.end();
  assert.equal(q.begin(), true);
});

test("saveQueue: пауза ввода откладывается новой правкой", async () => {
  const q = UICore.saveQueue(30);
  let calls = 0;
  q.due(() => { calls += 1; });
  q.put("A", "1");
  await new Promise((r) => setTimeout(r, 15));
  q.put("A", "2");               // отсчёт начался заново
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(calls, 0, "запись не должна уйти на полпути ввода");
  await new Promise((r) => setTimeout(r, 25));
  assert.equal(calls, 1);
  assert.deepEqual(q.pending(), { A: "2" });
});

/* ── дробь в целочисленном поле «Настроек» ── */
test("numberStepHint: дробь в целочисленном поле предупреждает", () => {
  /* «262,144» из буфера браузер превращает в 262.144, а чтение числа берёт
     целую часть — в MAX_TOKENS тихо оказались бы 262 токена */
  assert.match(UICore.numberStepHint("number", 1, "262.144"), /262/);
  assert.equal(UICore.numberStepHint("number", 1, "262144"), "");
  assert.equal(UICore.numberStepHint("number", null, ""), "");
  // дробное поле (порог схожести 0–1) дробь и ждёт
  assert.equal(UICore.numberStepHint("number", "0.05", "0.75"), "");
  // поле-текст, где «.» — обычный символ, предупреждения не получает
  assert.equal(UICore.numberStepHint("text", 1, "1.5"), "");
});

/* ── загрузка папки на вкладку «Файлы» ── */
test("uploadPath: путь перетащенного файла", () => {
  assert.equal(UICore.uploadPath({ name: "a.txt" }), "a.txt");
  assert.equal(
    UICore.uploadPath({ name: "a.txt", webkitRelativePath: "Книга/sub/a.txt" }),
    "Книга/sub/a.txt",
  );
  assert.equal(UICore.uploadPath(null), "");
});

test("uploadBatches: файлы раскладываются по папкам", () => {
  const f = (rel) => ({ name: rel.split("/").pop(), webkitRelativePath: rel });
  const got = UICore.uploadBatches([
    f("b.txt"),
    f("Книга/x.txt"),
    f("Книга/sub/y.txt"),
    f("Книга/z.txt"),
    null,
    { name: "" },
  ]);
  assert.deepEqual(
    got.map((b) => [b.dest, b.files.map((x) => x.name)]),
    [
      ["", ["b.txt"]],
      ["Книга", ["x.txt", "z.txt"]],
      ["Книга/sub", ["y.txt"]],
    ],
  );
  assert.deepEqual(UICore.uploadBatches(null), []);
});

test("uploadBatches: папка без имени файла не теряется", () => {
  /* браузер отдаёт папку только списком её файлов: пустая папка в
     DataTransfer.files не попадает вовсе — раскладывать нечего */
  const got = UICore.uploadBatches([{ name: "x.txt", webkitRelativePath: "" }]);
  assert.deepEqual(got.map((b) => b.dest), [""]);
});

test("checkScale: точка помнит номер главы — по нему она ведёт в текст", () => {
  /* точка ошибки объёма — адрес главы, а не украшение шкалы: клик по ней
     открывает главу в «Редакторе»; без номера она остаётся подсказкой */
  const sc = UICore.checkScale(
    { lo: 0.9, hi: 1.1, median: 1 },
    [{ value: 0.8, out: true, chapter: 37 }, { value: 1.0 }],
    null,
    null,
  );
  assert.deepEqual(sc.points.map((p) => p.chapter), [37, null]);
  /* номер — подпись, папка — адрес: «Редактор» выбирает главу по имени
     папки в дереве, и номер вместо него открыл бы первую главу книги */
  const withDir = UICore.checkScale(
    { lo: 0.9, hi: 1.1, median: 1 },
    [{ value: 0.8, chapter: 37, dir: "chapters/0000_37_Глава" }, 1.0],
    null,
    null,
  );
  assert.deepEqual(withDir.points.map((p) => p.dir),
    ["chapters/0000_37_Глава", null]);
  /* число вместо объекта — главы нет, шкала та же */
  assert.deepEqual(sc.points.map((p) => p.value), [0.8, 1.0]);
});

test("checkErrorKind/checkErrorKinds: подписи типов и порядок реестра", () => {
  /* подписи — из реестра UICore, имена приходят с сервера
     (web/api_assets.py::_check_error_kind) */
  assert.equal(UICore.checkErrorKind("ratio").label, "Соотношение");
  assert.equal(UICore.checkErrorKind("regexp").label, "Regexp-проверки");
  assert.equal(
    UICore.checkErrorKind("sequence").label,
    "Последовательность глав",
  );
  assert.equal(UICore.checkErrorKind("ending").label, "Конец главы");
  /* незнакомый тип не пропадает из фильтра: подписан «Другое» */
  assert.equal(UICore.checkErrorKind("??").label, "Другое");
  assert.equal(UICore.checkErrorKind("").label, "Другое");
  /* порядок — реестра (Соотношение, Regexp, Последовательность), а не
     порядок строк отчёта: смешанный список должен читаться одинаково */
  const kinds = UICore.checkErrorKinds([
    { error_kinds: ["sequence"] },
    { error_kinds: ["ratio", "regexp"] },
    { error_kinds: ["ending", "ratio"] },
  ]);
  assert.deepEqual(kinds, ["ratio", "regexp", "sequence", "ending"]);
  assert.deepEqual(UICore.checkErrorKinds([]), []);
  assert.deepEqual(UICore.checkErrorKinds([{ errors: ["x"] }]), []);
});

test("checkVisibleErrors: фильтр прячет СТРОКИ, а не главы", () => {
  /* одна глава держит и ошибку объёма, и нарушение последовательности:
     снятая галочка «Соотношение» не должна уносить главу из отчёта */
  const entry = {
    errors: ["[polished vs redacted] отношение объёма 0.90", "- По паттерну x: y"],
    error_kinds: ["ratio", "regexp"],
  };
  assert.deepEqual(
    UICore.checkVisibleErrors(entry, { error: [] }).map((x) => x.kind),
    ["ratio", "regexp"],
  );
  assert.deepEqual(
    UICore.checkVisibleErrors(entry, { error: ["ratio"] }).map((x) => x.message),
    ["- По паттерну x: y"],
  );
  /* индекс — позиция строки в исходном списке: по ней строка открывает текст */
  assert.deepEqual(
    UICore.checkVisibleErrors(entry, { error: ["ratio"] }).map((x) => x.index),
    [1],
  );
  /* кривые данные не роняют вкладку */
  assert.deepEqual(UICore.checkVisibleErrors(null, {}), []);
  assert.deepEqual(
    UICore.checkVisibleErrors({ errors: ["x"] }, { error: ["ratio"] }),
    [{ index: 0, message: "x", kind: undefined }],
  );
});

/* ── «Удалить по фильтру» глоссария: сравнение по значению ────────────
 * Подстрока в «=» уносила чужие записи: условие count = 1 удаляло и
 * count 17, и 931. Числа сравниваются как числа, текст — целиком. */
test("nerFilterHit: числа сравниваются как числа", () => {
  assert.equal(UICore.nerFilterHit(1, "=", "1"), true);
  assert.equal(UICore.nerFilterHit(17, "=", "1"), false, "17 — не 1");
  assert.equal(UICore.nerFilterHit(931, "=", "1"), false);
  assert.equal(UICore.nerFilterHit("1", "=", "1"), true, "строка-число тоже");
  assert.equal(UICore.nerFilterHit(" 1 ", "=", " 1 "), true, "пробелы не мешают");
  assert.equal(UICore.nerFilterHit(1.0, "=", "1.0"), true);
  assert.equal(UICore.nerFilterHit(17, "≠", "1"), true);
  assert.equal(UICore.nerFilterHit(1, "≠", "1"), false);
  assert.equal(UICore.nerFilterHit(18, ">", "17"), true);
  assert.equal(UICore.nerFilterHit(17, ">", "17"), false, "строгое больше");
  assert.equal(UICore.nerFilterHit(17, "≥", "17"), true);
  assert.equal(UICore.nerFilterHit(17, "≤", "18"), true);
  assert.equal(UICore.nerFilterHit(19, "≤", "18"), false);
  assert.equal(UICore.nerFilterHit(2, "<", "18"), true,
    "2 < 18 — сравнение чисел, а не строк");
});

test("nerFilterHit: текст — по целому значению, регистр не важен", () => {
  assert.equal(UICore.nerFilterHit("Person (male)", "=", "Person (male)"), true);
  assert.equal(UICore.nerFilterHit("Person (male)", "=", "Person"), false,
    "подстрока не считается");
  assert.equal(UICore.nerFilterHit("Person", "=", "person"), true);
  assert.equal(UICore.nerFilterHit("Person", "≠", "Location"), true);
  assert.equal(UICore.nerFilterHit("Альфа", ">", "Бета"), false, "строковое сравнение");
  assert.equal(UICore.nerFilterHit("Альфа", "<", "Бета"), true);
  /* порог текста не бывает: запись не считается ни больше, ни меньше */
  assert.equal(UICore.nerFilterHit("Альфа", "≥", "Альфа"), false);
  /* подстрока — только явными операторами, «=» её не подменяет */
  assert.equal(UICore.nerFilterHit("Person (male)", "~", "male"), true);
  assert.equal(UICore.nerFilterHit("Person (male)", "~", "MALE"), true);
  assert.equal(UICore.nerFilterHit("Person (male)", "~", "Location"), false);
  assert.equal(UICore.nerFilterHit("Person (male)", "!~", "male"), false);
  assert.equal(UICore.nerFilterHit("Person (male)", "!~", "Location"), true);
  /* пустое значение ничему не равно и под условие не попадает */
  assert.equal(UICore.nerFilterHit("", "=", ""), false);
  assert.equal(UICore.nerFilterHit(null, "≠", "1"), false);
  assert.equal(UICore.nerFilterHit(1, "=", "  "), false, "пустое условие — ничего");
});

test("nerDeleteFilter: замок не трогается, условие — по выбранному полю", () => {
  const items = [
    { term: "a", count: 1 },
    { term: "b", count: 17 },
    { term: "c", count: 1, _locked: true },
    { term: "d" },
    { term: "e", count: "1" },
  ];
  const terms = (f, op, v) => UICore.nerDeleteFilter(items, f, op, v)
    .map((it) => it.term).join(",");
  assert.equal(terms("count", "=", "1"), "a,e",
    "замок пропущен, запись без count под условие не попадает");
  assert.equal(terms("count", "≤", "1"), "a,e");
  assert.equal(terms("term", "=", "b"), "b");
  assert.equal(terms("", "=", "1"), "", "поля нет — жертв нет");
  /* кривые данные не роняют вкладку */
  assert.deepEqual(UICore.nerDeleteFilter(null, "count", "=", "1"), []);
  assert.deepEqual(
    UICore.nerDeleteFilter([null, 5, "x"], "count", "=", "1"), []);
});

/* ── размер текста в БАЙТАХ, а не в символах ────────────────────────────
 * Подпись размера в «Редакторе» после правки считалась как String.length:
 * кириллица в файле занимает 2 байта на символ, поэтому цифра расходилась
 * с реальным файлом в полтора-два раза. */
test("textBytes: байты UTF-8, а не единицы UTF-16", () => {
  assert.equal(UICore.textBytes("abc"), 3, "латиница — байт на символ");
  assert.equal(UICore.textBytes("привет"), 12, "кириллица — 2 байта на символ");
  assert.equal(UICore.textBytes("«ё»"), 6, "кавычки и буква ё тоже по 2 байта");
  assert.equal(UICore.textBytes("漢字"), 6, "CJK — по 3 байта");
  assert.equal(UICore.textBytes("😀"), 4, "эмодзи вне BMP — 4 байта на пару");
  assert.equal(UICore.textBytes(""), 0);
  assert.equal(UICore.textBytes(null), 0, "нет текста — нет байт");
  assert.equal(UICore.textBytes("a\nб"), 4, "перевод строки — один байт");
  /* правка, которая делает файл МЕНЬШЕ, тоже видна: это не «длина ответа» */
  const long = UICore.textBytes("длинный исходный текст главы");
  const short = UICore.textBytes("короче");
  assert.ok(short < long, `сокращение текста уменьшает размер: ${short} < ${long}`);
});

test("reviewAxes('tcl'): ось только одна — тип находки", () => {
  const axes = UICore.reviewAxes("tcl");
  assert.deepEqual(axes.map((a) => a.key), ["type"]);
  const vals = UICore.reviewAxisValues(
    [{ type: "mistranslation" }, { type: "mistranslation" },
     { type: "typo" }, { type: "manual" }], axes[0]);
  assert.deepEqual(vals.map((v) => [v.label, v.count]),
    [["mistranslation", 2], ["typo", 1], ["manual", 1]]);
  // скрытый тип не проходит фильтр, остальные — проходят
  const hidden = { type: ["mistranslation"] };
  assert.equal(UICore.reviewFilterMatch({ type: "mistranslation" }, hidden,
    "tcl"), false);
  assert.equal(UICore.reviewFilterMatch({ type: "typo" }, hidden, "tcl"), true);
});

test("progressParts: отставший снимок опроса не откатывает счётчик", () => {
  /* Два источника одного прогона: живой поток SSE и опрос /jobs. Снимок
     опроса приходит на кадр позже, и его done меньше уже показанного —
     лента рисовала «3/154», а карточка и pill шапки получив тот же снимок
     показывали «0/154». Счётчик одного запуска обязан идти только вперёд. */
  const job = { id: "j-back", status: "running" };
  const live = UICore.progressParts(
    job, { label: "Проверка перевода LLM", done: 3, total: 154 });
  assert.equal(live.count, "3/154 · 2%");
  // тот же запуск, отставший снимок: done вернулся к нулю
  const stale = UICore.progressParts(
    job, { label: "Проверка перевода LLM", done: 0, total: 154 });
  assert.equal(stale.count, "3/154 · 2%",
    "снимок откатил счётчик назад");
  // смена total — другая шкала (новый бар стадии): откат разрешён
  const other = UICore.progressParts(
    job, { label: "Другая стадия", done: 0, total: 20 });
  assert.equal(other.count, "0/20 · 0%");
});

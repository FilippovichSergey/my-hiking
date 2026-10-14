// Тэсты інтэрфейсу: app.js у jsdom з падмененымі MapLibre і fetch.
// Запуск: node --test tests/js/   (або праз python -m unittest — tests/test_frontend.py)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, before, test } from "node:test";
import { JSDOM } from "jsdom";

import { FakeMap, maplibregl } from "./fakes.js";

const ROOT = new URL("../../", import.meta.url);

// --- Падмены: MapLibre — у fakes.js ------------------------------------------

const day = (date, km) => ({ date, name: "t", distance: km, up: 100, down: 100, duration: 3600, moving: 3000,
  maxAlt: 2000, minAlt: 1000, komoot: [] });
const stats = (distance, up) => ({ distance, up, down: 100, duration: 3600, moving: 3000, maxAlt: 2000, minAlt: 1000 });
const hike = (slug, region, extra = {}) => ({
  slug, date: "2025-01-01", end: "2025-01-01", title: { be: slug, en: slug }, region, difficulty: null,
  stats: { distance: 10, up: 100, down: 100, duration: 3600, moving: 3000, maxAlt: 2000, minAlt: 1000 },
  days: [day("2025-01-01", 10)], point: [42, 41], bbox: [42, 41, 42.1, 41.1], lines: [[[42, 41], [42.1, 41.1]]],
  track: `data/tracks/${slug}.json`, cover: null, photos: [], videos: [], impressions: { be: "", en: "" }, ...extra,
});
const pair = (be = "", en = "") => ({ be, en });
// разлік па трэку і звесткі аўтара для паходу «guria» — у тым выглядзе, як іх запісвае `hike build`
const FACTS = {
  type: "loop", start: { at: [42, 41], alt: 900, time: "08:00" }, finish: { at: [42, 41], alt: 910, time: "12:30" },
  sun: { rise: "06:00", set: "20:00", left: 450 }, speed: 3.2, stops: 1800, gain: 180,
  steepUp: { grade: 21, km: 1.2 }, steepDown: { grade: 18, km: 3.4 },
};
const PRACTICAL = {
  checked: "2025-01-01", weatherNotes: pair("Туман.", "Fog."),
  notes: { surface: pair("Грунт.", "Dirt."), parking: pair("Каля школы.") },
  water: {
    days: [{ drunk: 2, taken: 3, refilled: null, left: null }], drunk: 2, taken: 3, refilled: null, left: null,
    perHour: 0.5, per10km: 4, available: null, note: pair(),
    sources: [{
      type: "spring", name: pair(), water: true, seasonal: true, treat: false, seen: null, photo: null, ref: "km::1",
      note: pair("Труба."), pos: { day: 0, km: 1, at: [42, 41], alt: 950, time: "09:00" },
    }],
  },
  gettingThere: [{
    type: "taxi", from: pair("Батумі", "Batumi"), to: pair("Вёска", "Village"), minutes: 70, km: 42, price: 80,
    priceFor: "car", trip: "one_way", date: "2025-01-01", boarding: pair(), road: pair("Грунтоўка."), note: pair(),
  }],
  places: [{
    type: "shelter", name: pair("Альтанка", "Gazebo"), note: pair(), seen: null, photo: null, ref: "at::41.05, 42.05",
    pos: { day: 0, km: 2.5, at: [42.05, 41.05], alt: 980, time: "10:00", off: 120 },
  }],
};
const DATA = {
  version: "9.8.7",
  share: true, // ёсць старонкі <мова>/<паход>/ з прэв'ю для спасылак
  hikes: [
    hike("slow", { be: "Аджарыя", en: "Adjara" }),
    hike("guria", { be: "Гурыя", en: "Guria" }, { difficulty: "expert", stats: stats(5, 900), facts: FACTS, practical: PRACTICAL }),
    hike("broken", { be: "Аджарыя", en: "Adjara" }, { days: [day("2025-01-01", 5), day("2025-01-02", 5)] }),
    // індэкс з двума днямі, а трэк (TRACK) — з адным: стары кэш браўзера або няўдалая зборка
    hike("mismatch", { be: "Гурыя", en: "Guria" }, { days: [day("2025-01-01", 5), day("2025-01-02", 5)] }),
    hike("late", { be: "Гурыя", en: "Guria" }, { stats: stats(25, 300) }),
    hike("notrack", { be: "Гурыя", en: "Guria" }, { stats: null, days: [], track: null, lines: [], bbox: null }),
    // трохдзённы паход без трэку: працягласць вядомая толькі з дат
    hike("trek3", { be: "Гурыя", en: "Guria" },
      { stats: null, days: [], track: null, lines: [], bbox: null, end: "2025-01-03" }),
  ],
};
const TRACK = { days: [{ segs: [[[42, 41, 1000, 0], [42.1, 41.1, 1100, 1]]] }] };

// fetch: hikes.json адразу, трэкі «slow» і «late» — па камандзе тэста, «broken» — 404.
let releaseSlow, releaseLate;
const fetched = []; // усе запытаныя адрасы
function fakeFetch(url) {
  const json = (data) => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });
  fetched.push(url);
  url = url.split("?")[0]; // адрас даных у index.html — з версіяй (яе ставіць `hike build`)
  if (url.endsWith("data/hikes.json")) return json(DATA);
  if (url.endsWith("slow.json")) return new Promise((resolve) => { releaseSlow = () => resolve({ ok: true, json: () => Promise.resolve(TRACK) }); });
  if (url.endsWith("late.json")) return new Promise((resolve) => { releaseLate = () => resolve({ ok: true, json: () => Promise.resolve(TRACK) }); });
  if (url.endsWith("broken.json")) return Promise.resolve({ ok: false, json: () => Promise.reject(new Error("404")) });
  return json(TRACK);
}

// --- Асяроддзе ----------------------------------------------------------------

const errors = [];
const onRejection = (err) => errors.push(err);
let dom, document, window;

const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));
async function waitFor(check, what) {
  for (let i = 0; i < 100; i++) {
    if (check()) return;
    await tick();
  }
  assert.fail(`не дачакаліся: ${what}`);
}
async function go(hash) {
  window.location.hash = hash;
  await tick(60);
}
const panel = () => document.getElementById("panel-body");

before(async () => {
  process.on("unhandledRejection", onRejection);
  const html = readFileSync(new URL("docs/index.html", ROOT), "utf8").replace(/<script[\s\S]*?<\/script>/g, "");
  dom = new JSDOM(html, { url: "http://localhost/#/be" });
  ({ window } = dom);
  ({ document } = window);
  window.matchMedia = () => ({ matches: false, addEventListener() {} });
  const globals = {
    window, document, location: window.location, localStorage: window.localStorage, Node: window.Node,
    matchMedia: window.matchMedia, getComputedStyle: window.getComputedStyle.bind(window), innerWidth: 1200,
    ResizeObserver: class { observe() {} disconnect() {} }, fetch: fakeFetch, maplibregl,
  };
  for (const [k, v] of Object.entries(globals)) Object.defineProperty(globalThis, k, { value: v, configurable: true, writable: true });
  Object.defineProperty(globalThis, "navigator", { value: window.navigator, configurable: true });
  await import(new URL("docs/assets/app.js", ROOT));
  await waitFor(() => panel().querySelector(".hike-list"), "спіс паходаў");
});

after(() => {
  process.off("unhandledRejection", onRejection);
  dom.window.close();
});

// --- Тэсты --------------------------------------------------------------------

test("позні адказ трэку не вяртае закрытую старонку паходу (review #4)", async () => {
  await go("#/be/slow");
  await go("#/be");
  assert.ok(panel().querySelector(".hike-list"), "пасля вяртання паказваецца спіс");
  releaseSlow();
  await tick(60);
  assert.ok(panel().querySelector(".hike-list"), "спіс застаўся пасля позняга адказу");
  assert.equal(panel().querySelector(".back"), null);
  assert.equal(FakeMap.last.sources.selected.data.features.length, 0, "на карце нічога не выбрана");
});

test("фільтр рэгіёна захоўваецца пры змене мовы (review #5)", async () => {
  await go("#/be");
  const select = panel().querySelector(".filter-row select");
  const option = [...select.options].find((o) => o.textContent === "Аджарыя");
  select.value = option.value;
  select.dispatchEvent(new window.Event("change"));
  assert.equal(panel().querySelectorAll(".hike-list li").length, 2);

  document.querySelector('[data-lang="en"]').click();
  await tick(60);
  const selectEn = panel().querySelector(".filter-row select");
  assert.equal(selectEn.selectedOptions[0].textContent, "Adjara");
  assert.equal(panel().querySelectorAll(".hike-list li").length, 2);

  document.querySelector('[data-lang="be"]').click();
  await tick(60);
  assert.equal(panel().querySelectorAll(".hike-list li").length, 2);
  const reset = panel().querySelector(".filter-row select");
  reset.value = "";
  reset.dispatchEvent(new window.Event("change"));
});

test("дзень, якога няма ў трэку, не ламае старонку (review v1.0.2, №2)", async () => {
  errors.length = 0;
  await go("#/be/mismatch");
  await waitFor(() => panel().querySelector(".back"), "старонка паходу");
  const day2 = [...panel().querySelectorAll(".chip")].find((b) => b.textContent.includes("Дзень 2"));
  day2.click();
  await tick(60);
  assert.deepEqual(errors.map(String), []);
  assert.ok(panel().querySelector(".back"), "старонка паходу засталася");
});

test("выбар дня без загружанага трэку не падае (review #8)", async () => {
  errors.length = 0;
  await go("#/be/broken");
  await waitFor(() => panel().querySelector(".back"), "старонка паходу");
  const dayChip = [...panel().querySelectorAll(".chip")].find((b) => b.textContent.includes("Дзень 1"));
  assert.ok(dayChip, "ёсць кнопка дня");
  dayChip.click();
  await tick(60);
  assert.deepEqual(errors.map(String), []);
  assert.ok(panel().querySelector(".back"), "старонка паходу засталася");
  assert.ok(panel().textContent.includes("Не ўдалося загрузіць трэк"), "паказана паведамленне пра памылку");
});

test("кнопка дня папярэдняга паходу не адмяняе адкрыццё новага (review v1.0.5, №3)", async () => {
  await go("#/be/mismatch");
  await waitFor(() => panel().querySelector("article h1")?.textContent === "mismatch", "старонка mismatch");
  const oldDay = [...panel().querySelectorAll(".chip")].find((b) => b.textContent.includes("Дзень 1"));
  assert.ok(oldDay, "ёсць кнопка дня");
  await go("#/be/late");                       // трэк «late» яшчэ не прыйшоў, на экране старая старонка
  assert.equal(panel().querySelector("article h1").textContent, "mismatch");
  oldDay.click();
  await tick(60);
  releaseLate();
  await waitFor(() => panel().querySelector("article h1")?.textContent === "late", "старонка late");
  assert.equal(window.location.hash, "#/be/late");
  assert.equal(FakeMap.last.sources.selected.data.features.length > 0, true, "на карце выбраны паход");
});

test("складанасць паказваецца іконкай са спрайта і подпісам", async () => {
  await go("#/be");
  const badges = [...panel().querySelectorAll(".hike-list .diff")];
  assert.equal(badges.length, 1, "значок ёсць толькі ў паходу з зададзенай складанасцю");
  const [badge] = badges;
  assert.equal(badge.dataset.difficulty, "expert");
  assert.equal(badge.textContent, "Вельмі цяжка");
  const icon = badge.querySelector("svg.diff-icon");
  assert.equal(icon.getAttribute("aria-hidden"), "true");
  assert.equal(icon.querySelector("use").getAttribute("href"), "assets/difficulty-sprite.svg#difficulty-very-hard");
  const sprite = readFileSync(new URL("docs/assets/difficulty-sprite.svg", ROOT), "utf8");
  for (const id of ["easy", "moderate", "hard", "very-hard"]) assert.ok(sprite.includes(`id="difficulty-${id}"`), id);
});

test("тэлефон: пераключальнік «Спіс / Карта» і выбар паходу на карце", async () => {
  await go("#/be");
  const layout = document.querySelector(".layout");
  const [listBtn, mapBtn] = document.querySelectorAll("[data-view-btn]");
  const pressed = () => [listBtn, mapBtn].map((b) => b.getAttribute("aria-pressed")).join();
  assert.equal(layout.dataset.view, "list", "спачатку паказваецца спіс");
  assert.deepEqual([listBtn.textContent, mapBtn.textContent, pressed()], ["Спіс", "Карта", "true,false"]);

  mapBtn.click();
  assert.equal(layout.dataset.view, "map");
  assert.equal(pressed(), "false,true");

  // націсканне на маркер адкрывае паход і вяртае з карты да яго апісання
  FakeMap.last.handlers.click[0]({ features: [{ properties: { slug: "guria" } }] });
  await tick(60);
  assert.equal(window.location.hash, "#/be/guria");
  assert.equal(layout.dataset.view, "list");
  assert.deepEqual([listBtn.textContent, pressed()], ["Апісанне", "true,false"]);

  // карта паходу; вяртанне да спіса выгляд не мяняе
  mapBtn.click();
  await go("#/be");
  assert.equal(layout.dataset.view, "map");
  assert.equal(listBtn.textContent, "Спіс");

  document.querySelector('[data-lang="en"]').click();
  await tick(60);
  assert.deepEqual([listBtn.textContent, mapBtn.textContent], ["List", "Map"]);
  document.querySelector('[data-lang="be"]').click();
  listBtn.click();
  await tick(60);
  assert.equal(layout.dataset.view, "list");
});

test("сартаванне, фільтры па нагрузцы і «Скінуць фільтры»", async () => {
  await go("#/be");
  const titles = () => [...panel().querySelectorAll(".card-title")].map((el) => el.textContent);
  const choose = (selector, value) => {
    const select = panel().querySelector(selector);
    assert.ok([...select.options].some((o) => o.value === value), `${selector}: ёсць варыянт «${value}»`);
    select.value = value;
    select.dispatchEvent(new window.Event("change"));
  };
  const sortBy = (key) => choose("select.sort", key);
  const DISTANCE = '.filter-row select[aria-label="Любая адлегласць"]';
  const DAYS = '.filter-row select[aria-label="Любая працягласць"]';
  const all = ["slow", "guria", "broken", "mismatch", "late", "notrack", "trek3"];
  assert.deepEqual(titles(), all, "спачатку новыя — парадак з даных");
  assert.equal(panel().querySelector(".reset"), null, "без фільтраў кнопкі скіду няма");

  sortBy("short");
  assert.deepEqual(titles(), ["guria", "slow", "broken", "mismatch", "late", "notrack", "trek3"]);
  sortBy("long");
  assert.deepEqual(titles(), ["late", "slow", "broken", "mismatch", "guria", "notrack", "trek3"], "паходы без трэку — у канцы");
  sortBy("up");
  assert.deepEqual(titles(), ["guria", "late", "slow", "broken", "mismatch", "notrack", "trek3"]);

  choose(DISTANCE, "mid");
  assert.deepEqual(titles(), ["slow", "broken", "mismatch"]);
  choose(DAYS, "multi");
  assert.deepEqual(titles(), ["broken", "mismatch"]);
  assert.equal(panel().querySelector(DAYS).value, "multi", "выбар захоўваецца пасля перамалёўкі");
  choose(DISTANCE, "short");
  assert.deepEqual(titles(), [], "нічога не падыходзіць");
  assert.ok(panel().querySelector(".empty"));

  panel().querySelector(".reset").click();
  assert.deepEqual(titles(), ["guria", "late", "slow", "broken", "mismatch", "notrack", "trek3"], "усе паходы, сартаванне застаецца");
  assert.deepEqual([...panel().querySelectorAll(".filter-row select")].map((el) => el.value), ["", "", "", "", "", ""]);
  assert.equal(panel().querySelector(".reset"), null);
  assert.equal(panel().querySelector("select.sort").value, "up");

  choose(DAYS, "one");
  assert.deepEqual(titles(), ["guria", "late", "slow", "notrack"]);
  panel().querySelector(".reset").click();
  sortBy("new");
  assert.deepEqual(titles(), all);
});

test("шматдзённы паход без трэку лічыцца па датах (review v1.0.6, №3)", async () => {
  await go("#/be");
  const titles = () => [...panel().querySelectorAll(".card-title")].map((el) => el.textContent);
  const chooseDays = (value) => {
    const select = panel().querySelector('.filter-row select[aria-label="Любая працягласць"]');
    select.value = value;
    select.dispatchEvent(new window.Event("change"));
  };
  // 4 аднадзённыя + 2 двухдзённыя з трэкам + 3 дні без трэку
  assert.match(panel().querySelector(".tile-sub").textContent, /^11 дзён/);
  const card = [...panel().querySelectorAll(".card")].find((el) => el.querySelector(".card-title").textContent === "trek3");
  assert.match(card.querySelector(".card-meta").textContent, /^1–3 студзеня 2025/);
  assert.equal(card.querySelector(".card-nums").textContent, "3 дні");

  chooseDays("multi");
  assert.deepEqual(titles(), ["broken", "mismatch", "trek3"]);
  chooseDays("one");
  assert.deepEqual(titles(), ["slow", "guria", "late", "notrack"]);
  panel().querySelector(".reset").click();

  await go("#/be/trek3");
  assert.equal(panel().querySelector(".detail .meta").textContent.includes("3 дні"), true);
  await go("#/be");
});

test("старонка паходу: «Спампаваць GPX» і «Падзяліцца»", async () => {
  const button = (label) => [...panel().querySelectorAll(".actions button")].find((b) => b.textContent === label);
  const blobs = [], clicks = [], copied = [], prompts = [];
  const createObjectURL = URL.createObjectURL, anchorClick = window.HTMLAnchorElement.prototype.click;
  URL.createObjectURL = (blob) => { blobs.push(blob); return "blob:test"; };
  window.HTMLAnchorElement.prototype.click = function () { clicks.push({ href: this.getAttribute("href"), name: this.download }); };
  window.prompt = (message, value) => { prompts.push(value); return null; };
  const clipboard = (writeText) => Object.defineProperty(window.navigator, "clipboard", { value: { writeText }, configurable: true });
  try {
    // аднадзённы паход з трэкам
    await go("#/be/guria");
    await waitFor(() => button("Спампаваць GPX"), "кнопка GPX");
    button("Спампаваць GPX").click();
    assert.deepEqual(clicks, [{ href: "blob:test", name: "guria.gpx" }]);
    assert.equal(blobs[0].type, "application/gpx+xml");
    const xml = await blobs[0].text();
    assert.ok(xml.includes("<metadata><name>guria</name><link href=\"http://localhost/#/be/guria\"/></metadata>"));
    assert.ok(xml.includes("<trk>\n    <name>guria</name>"));
    assert.deepEqual(xml.match(/<trkpt[^\n]*/g), [
      '<trkpt lat="41" lon="42"><ele>1000</ele></trkpt>', '<trkpt lat="41.1" lon="42.1"><ele>1100</ele></trkpt>']);

    // «Падзяліцца» капіюе спасылку на паход і на дзве секунды паведамляе пра гэта
    clipboard(async (text) => { copied.push(text); });
    const share = button("Падзяліцца");
    share.click();
    await waitFor(() => share.textContent === "Спасылка скапіявана", "паведамленне пра капіяванне");
    assert.deepEqual(copied, ["http://localhost/be/guria/"], "спасылка, якой дзеляцца, — старонка з прэв'ю");
    // буфер абмену недаступны — спасылка паказваецца для ручнога капіявання
    clipboard(async () => { throw new Error("denied"); });
    share.click();
    await waitFor(() => prompts.length === 1, "запасны спосаб");
    assert.deepEqual(prompts, ["http://localhost/be/guria/"]);

    // выбраны дзень шматдзённага паходу: файл толькі з гэтым днём; для дня без трэку кнопкі няма
    await go("#/be/mismatch");
    await waitFor(() => button("Спампаваць GPX"), "кнопка GPX шматдзённага паходу");
    const dayChip = (n) => [...panel().querySelectorAll(".chip")].find((c) => c.textContent.startsWith(`Дзень ${n}`));
    dayChip(1).click();
    await waitFor(() => dayChip(1)?.getAttribute("aria-pressed") === "true", "выбар дня 1");
    button("Спампаваць GPX").click();
    assert.equal(clicks[1].name, "mismatch-day-1.gpx");
    assert.ok((await blobs[1].text()).includes("<name>mismatch — Дзень 1, 1 студзеня</name>"));
    dayChip(2).click();
    await waitFor(() => dayChip(2)?.getAttribute("aria-pressed") === "true", "выбар дня 2");
    assert.equal(button("Спампаваць GPX"), undefined);
    assert.ok(button("Падзяліцца"));

    // паход без трэку: толькі «Падзяліцца»
    await go("#/be/notrack");
    await waitFor(() => button("Падзяліцца"), "кнопка падзелу без трэку");
    assert.equal(button("Спампаваць GPX"), undefined);
  } finally {
    URL.createObjectURL = createObjectURL;
    window.HTMLAnchorElement.prototype.click = anchorClick;
    delete window.navigator.clipboard;
    await go("#/be");
  }
});

test("падвал паказвае версію сайта з індэкса", async () => {
  await go("#/be");
  const footer = () => panel().querySelector("footer.site-footer");
  assert.equal(footer().textContent, "Мае паходы · версія 9.8.7");
  assert.equal(panel().lastElementChild, footer(), "падвал — апошні элемент спіса");
  await go("#/be/guria");
  await waitFor(() => panel().querySelector(".detail"), "старонка паходу");
  assert.equal(footer().textContent, "Мае паходы · версія 9.8.7");
  assert.equal(panel().lastElementChild, footer(), "падвал — апошні элемент старонкі паходу");
  await go("#/en");
  assert.equal(footer().textContent, "My hikes · version 9.8.7");
  await go("#/be");
});

test("фільтры і карта: «Паказаць знойдзеныя на карце» і лічбы ў падказцы маркера", async () => {
  await go("#/be");
  const layout = document.querySelector(".layout");
  const showOnMap = () => panel().querySelector(".show-on-map");
  const choose = (label, value) => {
    const select = panel().querySelector(`.filter-row select[aria-label="${label}"]`);
    select.value = value;
    select.dispatchEvent(new window.Event("change"));
  };
  assert.equal(showOnMap(), null, "без фільтраў кнопкі няма");
  assert.deepEqual([...panel().childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent), [],
    "пустыя неабавязковыя вузлы не трапляюць на старонку тэкстам «null»");

  choose("Любая адлегласць", "long"); // застаецца адзін паход — «late»
  assert.equal(showOnMap().textContent, "Паказаць знойдзеныя на карце");
  const fitsBefore = FakeMap.last.fits.length;
  showOnMap().click();
  assert.equal(FakeMap.last.fits.length, fitsBefore + 1, "карта набліжаецца да знойдзеных паходаў");
  assert.equal(layout.dataset.view, "map", "на тэлефоне адкрываецца карта");
  assert.deepEqual(FakeMap.last.sources.hikes.data.features.map((f) => f.properties.slug), ["late"]);

  choose("Любая працягласць", "multi"); // нічога не знойдзена
  assert.equal(panel().querySelectorAll(".card").length, 0);
  assert.equal(showOnMap(), null, "няма чаго паказваць");
  panel().querySelector(".reset").click();
  assert.equal(showOnMap(), null);
  document.querySelector('[data-view-btn="list"]').click();

  // падказка маркера: назва, дата, адлегласць, набор вышыні і складанасць
  const hover = (slug) => {
    FakeMap.last.handlers.mouseenter[0]({ features: [{ properties: { slug }, geometry: { coordinates: [42, 41] } }] });
    return [...maplibregl.Popup.content.children].map((el) => el.textContent);
  };
  assert.deepEqual(hover("guria"), ["guria", "1 студзеня 2025", "5 км↑ 900 мВельмі цяжка"]);
  assert.equal(maplibregl.Popup.content.querySelector(".popup-nums .diff").dataset.difficulty, "expert");
  assert.deepEqual(hover("slow"), ["slow", "1 студзеня 2025", "10 км↑ 100 м"], "без складанасці — толькі лічбы");
  assert.deepEqual(hover("notrack"), ["notrack", "1 студзеня 2025"], "без трэку і складанасці радка няма");
});

test("практычная інфармацыя: разлік па трэку, звесткі аўтара і тып маршруту ў спісе", async () => {
  await go("#/be");
  const titles = () => [...panel().querySelectorAll(".card-title")].map((el) => el.textContent);
  const card = [...panel().querySelectorAll(".card")].find((el) => el.querySelector(".card-title").textContent === "guria");
  assert.ok(card.querySelector(".card-nums").textContent.includes("Кола"), "тып маршруту на картцы");
  const typeSelect = panel().querySelector('.filter-row select[aria-label="Любы тып маршруту"]');
  typeSelect.value = "loop";
  typeSelect.dispatchEvent(new window.Event("change"));
  assert.deepEqual(titles(), ["guria"]);
  panel().querySelector(".reset").click();

  await go("#/be/guria");
  await waitFor(() => panel().querySelector(".p-facts"), "блок практычнай інфармацыі");
  assert.ok([...panel().querySelectorAll(".detail h2")].some((el) => el.textContent === "Практычная інфармацыя"));
  const rows = Object.fromEntries([...panel().querySelectorAll(".p-facts dt")].map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
  assert.equal(rows["Тып маршруту"], "Колавы");
  assert.equal(rows["Старт"], "08:00 · 900 м Маршрут да старту ↗");
  assert.equal(rows["Фініш"], "12:30 · 910 м");
  assert.equal(rows["Светлавы дзень"], "06:00–20:00 · пасля фінішу заставалася 7 г 30 хв святла");
  assert.equal(rows["Тэмп"], "3,2 км/г у руху · прыпынкі 30 хв");
  assert.equal(rows["Рэльеф"], "180 м набору на кіламетр · найкруцейшы пад'ём 21 % на 500 м (ад 1,2 км) · найкруцейшы спуск 18 % на 500 м (ад 3,4 км)");
  assert.equal(panel().querySelector(".p-link").getAttribute("href"), "https://www.google.com/maps/dir/?api=1&destination=41,42");

  // пад кожнай часткай падпісана, адкуль даныя
  const groups = [...panel().querySelectorAll(".p-group")];
  assert.deepEqual(groups.map((g) => g.querySelector("summary").textContent),
    ["Як дабрацца1 варыянт", "ВадаВыпіта 2 л · 1 крыніца", "Асаблівыя месцы1", "Сцежка і ўмовы", "Надвор'е"]);
  assert.equal(panel().querySelector(".p-facts + .p-source").textContent, "Разлік па трэку");
  assert.ok(groups.every((g) => g.querySelector(".p-source").textContent === "Мае назіранні"));
  const text = (i) => [...groups[i].querySelectorAll(".p-title, .p-meta, .p-text, .p-note")].map((el) => el.textContent);
  assert.deepEqual(text(0), ["Таксі · Батумі → Вёска", "1 г 10 хв · 42 км · 80 ₾, за машыну, у адзін бок · цана на 1 студзеня 2025",
    "Дарога: Грунтоўка.", "Паркоўка: Каля школы."]);
  assert.deepEqual(text(1), ["На маршруце ёсць вада.", "Выпіта 2 л (0,5 л за гадзіну на маршруце · 4 л на 10 км)", "узята 3 л",
    "Крыніца", "1 км ад старту · 950 м · 09:00", "вада была · перасыхае ў сухі сезон · можна піць без апрацоўкі", "Труба."]);
  assert.deepEqual(text(2), ["Альтанка або навес: Альтанка", "2,5 км ад старту · 980 м · 10:00 · збоку ад трэку, 120 м"]);
  assert.deepEqual(text(3), ["Пакрыццё: Грунт."]);
  assert.deepEqual(text(4), ["Туман."]);
  assert.equal([...panel().querySelectorAll(".p-source")].at(-1).textContent, "Звесткі правераны 1 студзеня 2025");

  await go("#/en/guria");
  await waitFor(() => panel().querySelector(".p-facts"), "блок па-англійску");
  assert.ok([...panel().querySelectorAll(".detail h2")].some((el) => el.textContent === "Practical information"));
  assert.equal(panel().querySelector(".p-facts dd").textContent, "Loop");
  assert.equal(panel().querySelector(".p-group .p-title").textContent, "Taxi · Batumi → Village");

  // паход без трэку і без звестак аўтара — блока няма
  await go("#/be/notrack");
  await waitFor(() => panel().querySelector(".detail h1")?.textContent === "notrack", "старонка паходу без трэку");
  assert.equal(panel().querySelector(".p-facts, .p-group"), null);
  assert.ok(![...panel().querySelectorAll(".detail h2")].some((el) => el.textContent === "Практычная інфармацыя"));
  await go("#/be");
});

test("кропкі на карце, падказкі OpenStreetMap, дарога на машыне і фільтр па ўмовах", async () => {
  // даныя этапу 3 дадаюцца паходу «guria» толькі на час гэтага тэста
  const guria = DATA.hikes.find((hk) => hk.slug === "guria");
  Object.assign(guria, {
    facts: { ...FACTS, start: { ...FACTS.start, place: pair("Мілісі", "Milisi") } },
    hints: [
      { kind: "water", type: "spring", name: pair("Цёплая", "Warm"), osm: "node/101", pos: { day: 0, km: 0.4, at: [42.01, 41.01], alt: 920, off: 80 } },
      { kind: "place", type: "viewpoint", name: pair(), osm: "way/202", pos: { day: 0, km: 3.1, at: [42.07, 41.07], alt: 990 } },
    ],
    drive: { from: pair("Батумі, парк Качынскіх", "Batumi, Kaczyński Park"), to: { km: 42.1, minutes: 63, off: 1200 }, back: { km: 12.4, minutes: 25 } },
  });
  const slow = DATA.hikes.find((hk) => hk.slug === "slow");
  try {
    await go("#/be/guria");
    await waitFor(() => maplibregl.Marker.all.length === 4, "кропкі паходу на карце");
    const markers = () => maplibregl.Marker.all;
    assert.deepEqual(markers().map((m) => [m.element.className, m.at, m.element.getAttribute("aria-label")]), [
      ["poi poi-water", [42, 41], "Крыніца"],
      ["poi poi-place", [42.05, 41.05], "Альтанка або навес: Альтанка"],
      ["poi poi-water poi-hint", [42.01, 41.01], "Крыніца: Цёплая"],
      ["poi poi-place poi-hint", [42.07, 41.07], "Аглядная пляцоўка"],
    ], "запісы аўтара і падказкі OpenStreetMap адрозніваюцца класам");
    assert.ok(markers().every((m) => m.element.querySelector("svg.poi-icon")), "у кожнай кропкі ёсць значок");
    const popup = (i) => {
      markers()[i].element.dispatchEvent(new window.Event("mouseenter"));
      return [...maplibregl.Popup.content.children].map((el) => el.textContent);
    };
    assert.deepEqual(popup(0), ["Крыніца", "1 км ад старту · 950 м · 09:00", "Мае назіранні"]);
    assert.deepEqual(popup(2), ["Крыніца: Цёплая", "0,4 км ад старту · 920 м · збоку ад трэку, 80 м", "З OpenStreetMap, мной не праверана"]);

    // падказак бывае шмат: на аддаленай карце застаюцца толькі запісы аўтара
    const mapEl = document.getElementById("map");
    FakeMap.last.zoom = 9;
    FakeMap.last.fire("zoom");
    assert.ok(mapEl.classList.contains("hints-far"));
    FakeMap.last.zoom = 13;
    FakeMap.last.fire("zoom");
    assert.ok(!mapEl.classList.contains("hints-far"));

    // старонка паходу: назва населенага пункта, дарога на машыне і падказкі — кожнае са сваёй крыніцай
    const rows = Object.fromEntries([...panel().querySelectorAll(".p-facts dt")].map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
    assert.equal(rows["Старт"], "Мілісі · 08:00 · 900 м Маршрут да старту ↗");
    assert.equal(rows["Фініш"], "12:30 · 910 м", "без назвы — як раней");
    assert.equal(panel().querySelector(".p-facts + .p-source").textContent, "Разлік па трэку; назвы населеных пунктаў — з OpenStreetMap");
    const groups = [...panel().querySelectorAll(".p-group")];
    assert.deepEqual(groups.map((g) => g.querySelector("summary").textContent),
      ["Як дабрацца1 варыянт", "ВадаВыпіта 2 л · 1 крыніца", "Асаблівыя месцы1", "Сцежка і ўмовы", "Надвор'е", "Падказкі з OpenStreetMap2"]);
    const DRIVE = "Разлік сэрвісу маршрутаў OSRM па дарогах OpenStreetMap — без уліку затораў і стану дарогі";
    assert.deepEqual([...groups[0].querySelectorAll(".p-source")].map((el) => el.textContent), ["Мае назіранні", DRIVE]);
    assert.deepEqual([...groups[0].querySelectorAll(".p-part .p-title, .p-part .p-note")].map((el) => el.textContent), [
      "На машыне ад: Батумі, парк Качынскіх",
      "да старту: 42 км, каля 1 г 03 хв (аўтадарога сканчаецца за 1,2 км ад гэтага пункта)",
      "ад фінішу назад: 12,4 км, каля 25 хв",
    ]);
    const hints = groups[5];
    assert.deepEqual([...hints.querySelectorAll(".p-title, .p-meta")].map((el) => el.textContent), [
      "Крыніца: Цёплая", "0,4 км ад старту · 920 м · збоку ад трэку, 80 м", "на карце OSM ↗",
      "Аглядная пляцоўка", "3,1 км ад старту · 990 м", "на карце OSM ↗",
    ]);
    assert.deepEqual([...hints.querySelectorAll("a")].map((a) => a.getAttribute("href")),
      ["https://www.openstreetmap.org/node/101", "https://www.openstreetmap.org/way/202"]);
    assert.deepEqual([...hints.querySelectorAll(".p-source")].map((el) => el.textContent), ["З OpenStreetMap, мной не праверана"]);
    assert.equal(hints.querySelectorAll(".poi.poi-hint").length, 2, "у спісе той жа значок, што на карце");
    assert.equal(groups[1].querySelectorAll(".poi.poi-water:not(.poi-hint)").length, 1, "крыніца аўтара — без пазнакі падказкі");

    await go("#/en/guria");
    await waitFor(() => markers()[2]?.element.getAttribute("aria-label") === "Spring: Warm", "кропкі па-англійску");
    assert.equal(markers().length, 4, "старыя кропкі прыбраныя, а не падвоеныя");
    assert.equal([...panel().querySelectorAll(".p-group summary")].at(-1).textContent, "Hints from OpenStreetMap2");
    assert.equal(popup(3).at(-1), "From OpenStreetMap, not checked by me");

    await go("#/be");
    assert.equal(markers().length, 0, "у спісе паходаў кропак на карце няма");

    // фільтр па ўмовах — толькі паводле звестак аўтара і толькі тыя ўмовы, пра якія ёсць звесткі
    const titles = () => [...panel().querySelectorAll(".card-title")].map((el) => el.textContent);
    const cond = () => panel().querySelector('.filter-row select[aria-label="Любыя ўмовы"]');
    const choose = (value) => {
      cond().value = value;
      cond().dispatchEvent(new window.Event("change"));
    };
    assert.deepEqual([...cond().options].map((o) => [o.value, o.textContent]), [["", "Любыя ўмовы"], ["water", "Ёсць вада на маршруце"]]);
    choose("water");
    assert.deepEqual(titles(), ["guria"]);
    panel().querySelector(".reset").click();

    guria.practical.gettingThere.push({ ...guria.practical.gettingThere[0], type: "marshrutka" });
    slow.hints = guria.hints; // падказкі OpenStreetMap пра ваду ў фільтр не ідуць
    await go("#/be/slow");
    await go("#/be");
    assert.deepEqual([...cond().options].map((o) => o.value), ["", "water", "transit"]);
    choose("transit");
    assert.deepEqual(titles(), ["guria"]);
    choose("water");
    assert.deepEqual(titles(), ["guria"], "паход толькі з падказкамі OpenStreetMap не лічыцца паходам з вадой");
    panel().querySelector(".reset").click();
  } finally {
    guria.facts = FACTS;
    guria.practical.gettingThere.splice(1);
    delete guria.hints;
    delete guria.drive;
    delete slow.hints;
  }
});

test("пры адкрытым паходзе на карце застаецца толькі яго трэк", async () => {
  const shown = () => ["lines", "lines-casing"].map((id) => FakeMap.last.layout[id]?.visibility);
  await go("#/be");
  assert.deepEqual(shown(), ["visible", "visible"], "у спісе відаць трэкі ўсіх паходаў");

  await go("#/be/guria");
  await waitFor(() => panel().querySelector(".detail h1")?.textContent === "guria", "старонка паходу");
  assert.deepEqual(shown(), ["none", "none"], "трэкі астатніх паходаў схаваныя: бягучы не зблытаць з суседнімі");
  assert.equal(FakeMap.last.sources.selected.data.features.length, 1, "трэк бягучага паходу на месцы");
  assert.ok(FakeMap.last.sources.hikes.data.features.length > 1, "астатнія паходы застаюцца маркерамі");

  await go("#/en/guria"); // змена мовы перамалёўвае старонку
  await waitFor(() => document.documentElement.lang === "en", "англійская мова");
  assert.deepEqual(shown(), ["none", "none"]);
  await go("#/be/notrack"); // паход без трэку — суседнія трэкі таксама не паказваюцца
  await waitFor(() => panel().querySelector(".detail h1")?.textContent === "notrack", "паход без трэку");
  assert.deepEqual(shown(), ["none", "none"]);

  await go("#/be");
  assert.deepEqual(shown(), ["visible", "visible"], "пасля вяртання да спіса трэкі зноў відаць");
});

test("сартаванне спіса не мяняе парадак кропак на карце (і склад груп маркераў)", async () => {
  await go("#/be");
  const onMap = (source) => FakeMap.last.sources[source].data.features.map((f) => f.properties.slug);
  const sortBy = (key) => {
    const select = panel().querySelector("select.sort");
    select.value = key;
    select.dispatchEvent(new window.Event("change"));
  };
  const titles = () => [...panel().querySelectorAll(".card-title")].map((el) => el.textContent);
  const stable = [...DATA.hikes.map((hk) => hk.slug)].sort();
  const seen = {};
  for (const key of ["new", "short", "up", "old"]) {
    sortBy(key);
    seen[key] = titles();
    assert.deepEqual(onMap("hikes"), stable, `кропкі пры сартаванні «${key}»`);
    assert.deepEqual(onMap("lines"), stable.filter((slug) => DATA.hikes.find((hk) => hk.slug === slug).lines.length),
      `трэкі пры сартаванні «${key}»`);
  }
  assert.notDeepEqual(seen.new, seen.short, "сам спіс пры гэтым перасартоўваецца");
  sortBy("new");
});

test("руская мова: пераключальнік, адрас #/ru, тэксты і запасная беларуская", async () => {
  await go("#/be");
  const buttons = () => [...document.querySelectorAll("[data-lang]")];
  assert.deepEqual(buttons().map((b) => [b.dataset.lang, b.textContent]), [["be", "BY"], ["ru", "RU"], ["en", "EN"]]);
  // рускія тэксты — толькі на час гэтага тэста: назва ў аднаго паходу і рэгіён Гурыя (у Аджарыі рускай назвы няма)
  const guria = DATA.hikes.find((hk) => hk.slug === "guria");
  const saved = { title: guria.title };
  guria.title = { be: "guria", en: "guria", ru: "Гурийский поход" };
  const gurian = DATA.hikes.filter((hk) => hk.region.en === "Guria");
  for (const hk of gurian) hk.region.ru = "Гурия";
  const titles = () => [...panel().querySelectorAll(".card-title")].map((el) => el.textContent);
  try {
    document.querySelector('[data-lang="ru"]').click();
    await tick(60);
    assert.equal(window.location.hash, "#/ru");
    assert.equal(document.documentElement.lang, "ru");
    assert.deepEqual(buttons().map((b) => b.getAttribute("aria-pressed")), ["false", "true", "false"]);
    assert.equal(document.getElementById("site-title").textContent, "Мои походы");
    assert.equal(panel().querySelector(".result-count").textContent, "7 походов");
    assert.equal(panel().querySelector(".search").placeholder, "Поиск по названию или региону");
    assert.deepEqual(titles(), ["slow", "Гурийский поход", "broken", "mismatch", "late", "notrack", "trek3"],
      "руская назва, а дзе яе няма — беларуская");
    const regions = panel().querySelector('.filter-row select[aria-label="Все регионы"]');
    assert.deepEqual([...regions.options].map((o) => o.textContent), ["Все регионы", "Аджарыя", "Гурия"],
      "рэгіён без рускай назвы — па-беларуску");
    const card = [...panel().querySelectorAll(".card")].find((el) => el.querySelector(".card-title").textContent === "Гурийский поход");
    assert.equal(card.querySelector(".card-meta").textContent, "1 января 2025 · Гурия");
    assert.ok(card.querySelector(".card-nums").textContent.includes("Очень тяжело"));

    // пошук знаходзіць паход па назве на любой мове
    const search = (text) => {
      const input = panel().querySelector(".search");
      input.value = text;
      input.dispatchEvent(new window.Event("input"));
      return titles();
    };
    assert.deepEqual(search("гурийск"), ["Гурийский поход"]);
    assert.deepEqual(search("гурия"), ["Гурийский поход", "mismatch", "late", "notrack", "trek3"], "рэгіён па-руску — усе паходы Гурыі");
    assert.equal(search("").length, 7);

    await go("#/ru/guria");
    await waitFor(() => panel().querySelector(".detail h1")?.textContent === "Гурийский поход", "старонка паходу па-руску");
    assert.equal(document.title, "Гурийский поход · Мои походы");
    assert.ok([...panel().querySelectorAll(".detail h2")].some((el) => el.textContent === "Практическая информация"));
    const rows = Object.fromEntries([...panel().querySelectorAll(".p-facts dt")].map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
    assert.equal(rows["Тип маршрута"], "Кольцевой");
    assert.equal(rows["Световой день"], "06:00–20:00 · после финиша оставалось 7 ч 30 мин света");
    assert.equal(panel().querySelector(".p-facts + .p-source").textContent, "Расчёт по треку");
    const groups = [...panel().querySelectorAll(".p-group summary")].map((el) => el.textContent);
    assert.deepEqual(groups, ["Как добраться1 вариант", "ВодаВыпито 2 л · 1 источник", "Особые места1", "Тропа и условия", "Погода"]);
    assert.equal(panel().querySelector(".p-group .p-title").textContent, "Такси · Батумі → Вёска", "звесткі аўтара без рускага тэксту — па-беларуску");
    assert.equal(panel().querySelector(".back").textContent, "← Все походы");

    // спасылка на паход захоўвае мову; невядомая мова ў адрасе мову не мяняе
    let copied = null;
    Object.defineProperty(window.navigator, "clipboard", { value: { writeText: async (text) => { copied = text; } }, configurable: true });
    [...panel().querySelectorAll(".actions .link-btn")].at(-1).click();
    await tick();
    assert.equal(copied, "http://localhost/ru/guria/");
    await go("#/de/guria");
    assert.equal(document.documentElement.lang, "ru");
    assert.ok(panel().querySelector(".hike-list"), "невядомы адрас — спіс паходаў");

    document.querySelector('[data-lang="be"]').click();
    await tick(60);
    assert.equal(window.location.hash, "#/be");
    assert.equal(document.getElementById("site-title").textContent, "Мае паходы");
    assert.equal(titles()[1], "guria");
  } finally {
    Object.assign(guria, saved);
    for (const hk of gurian) delete hk.region.ru;
    delete window.navigator.clipboard;
    if (document.documentElement.lang !== "be") document.querySelector('[data-lang="be"]').click();
  }
});

test("рэгіён з назвай толькі па-руску ёсць у фільтры (review v1.7.0, №2)", async () => {
  await go("#/be");
  const slow = DATA.hikes.find((hk) => hk.slug === "slow");
  const broken = DATA.hikes.find((hk) => hk.slug === "broken");
  const saved = [slow.region, broken.region];
  slow.region = { be: "", en: "", ru: "Кавказ" };
  broken.region = { be: "Альпы", en: "Alps", ru: "Альпы (ru)" };
  const titles = () => [...panel().querySelectorAll(".card-title")].map((el) => el.textContent);
  const regions = () => panel().querySelector(".filter-row select"); // першы фільтр — рэгіён
  const options = () => [...regions().options].map((o) => [o.value, o.textContent]);
  try {
    await go("#/be/slow"); // перамалёўка спіса з новымі данымі
    await go("#/be");
    assert.deepEqual(options(), [["", "Усе рэгіёны"], ["Alps", "Альпы"], ["Guria", "Гурыя"], ["Кавказ", "Кавказ"]]);
    regions().value = "Кавказ";
    regions().dispatchEvent(new window.Event("change"));
    assert.deepEqual(titles(), ["slow"]);
    assert.equal(panel().querySelector(".card-meta").textContent, "1 студзеня 2025 · Кавказ");

    document.querySelector('[data-lang="en"]').click(); // ключ рэгіёна не залежыць ад мовы: фільтр застаецца
    await tick(60);
    assert.equal(regions().value, "Кавказ");
    assert.deepEqual(titles(), ["slow"]);
    assert.deepEqual(options().map(([, text]) => text), ["All regions", "Alps", "Guria", "Кавказ"]);
    document.querySelector('[data-lang="ru"]').click();
    await tick(60);
    assert.deepEqual(options(), [["", "Все регионы"], ["Alps", "Альпы (ru)"], ["Guria", "Гурыя"], ["Кавказ", "Кавказ"]]);
    regions().value = "Alps";
    regions().dispatchEvent(new window.Event("change"));
    assert.deepEqual(titles(), ["broken"]);
    panel().querySelector(".reset").click();
  } finally {
    [slow.region, broken.region] = saved;
    document.querySelector('[data-lang="be"]').click();
    await tick(60);
  }
});

test("значкі фота на карце адкрытага паходу", async () => {
  const guria = DATA.hikes.find((hk) => hk.slug === "guria");
  const photo = (name, pos) => ({ src: `photos/guria/${name}.webp`, thumb: `photos/guria/${name}-t.webp`, w: 2000, h: 1500, ...(pos ? { pos } : {}) });
  guria.photos = [
    photo("a", { day: 0, km: 1.2, at: [42.0, 41.0], alt: 950, time: "09:10" }),
    photo("b"), // месца невядомае: на карце яго няма
    photo("c", { day: 0, km: 3.4, at: [42.2, 41.2], alt: 990, time: "11:40", off: 60 }),
  ];
  const pins = () => maplibregl.Marker.all.filter((m) => m.element.classList.contains("photo-pin"));
  try {
    await go("#/be/guria");
    await waitFor(() => pins().length === 2, "значкі фота");
    assert.deepEqual(pins().map((m) => [m.at, m.element.getAttribute("aria-label")]),
      [[[42.0, 41.0], "Фота 1: паказаць, дзе зроблена"], [[42.2, 41.2], "Фота 3: паказаць, дзе зроблена"]]);
    pins()[1].element.click();
    const pop = maplibregl.Popup.content;
    assert.equal(pop.querySelector("img").getAttribute("src"), "photos/guria/c-t.webp");
    assert.equal(pop.querySelector("img").getAttribute("alt"), "guria · фота 3");
    assert.equal(pop.querySelector(".popup-meta").textContent, "3,4 км ад старту · 990 м · 11:40 · збоку ад трэку, 60 м");
    assert.equal(pop.querySelector(".photo-pop-nav"), null, "адно фота — без стрэлак");
    assert.equal(panel().querySelectorAll(".gallery a").length, 3, "у галерэі на старонцы — усе фота");

    await go("#/en/guria");
    await waitFor(() => pins()[0]?.element.getAttribute("aria-label") === "Photo 1: show where it was taken", "значкі па-англійску");
    assert.equal(pins().length, 2, "значкі не падвойваюцца пры перамалёўцы");
    await go("#/be");
    assert.equal(pins().length, 0, "у спісе паходаў значкоў фота няма");
  } finally {
    guria.photos = [];
  }
});

test("старонка паходу: «Я хадзіў сюды яшчэ» са спасылкамі на іншыя паходы ў тое самае месца", async () => {
  const find = (slug) => DATA.hikes.find((hk) => hk.slug === slug);
  // спіс `related` лічыць зборка: найноўшыя першымі; «gone» — паход, якога ў індэксе ўжо няма
  find("guria").related = ["late", "gone", "notrack"];
  find("late").date = find("late").end = "2025-03-05";
  find("notrack").related = ["guria"];
  const again = () => [...panel().querySelectorAll("h2")].find((el) => el.textContent === "Я хадзіў сюды яшчэ");
  const cards = () => [...(again()?.nextElementSibling.querySelectorAll(".card") || [])];
  try {
    await go("#/be/guria");
    await waitFor(() => again(), "блок з іншымі паходамі");
    assert.equal(again().nextElementSibling.className, "hike-list");
    assert.deepEqual(cards().map((c) => c.querySelector(".card-title").textContent), ["late", "notrack"]);
    assert.equal(cards()[0].querySelector(".card-meta").textContent, "5 сакавіка 2025 · Гурыя");
    assert.equal(cards()[0].querySelector(".card-nums").textContent.includes("25"), true, "картка тая ж, што ў спісе: з лічбамі паходу");
    const headings = [...panel().querySelectorAll("h2")].map((el) => el.textContent);
    assert.ok(headings.indexOf("Уражанні") < headings.indexOf("Я хадзіў сюды яшчэ"), "блок стаіць пасля ўражанняў");

    // спасылка вядзе на паход, а там — зваротная спасылка
    cards()[1].click();
    await waitFor(() => window.location.hash === "#/be/notrack" && cards().length === 1, "пераход да іншага паходу");
    assert.deepEqual(cards().map((c) => c.querySelector(".card-title").textContent), ["guria"]);
    // іншая мова — іншы загаловак; паход без такіх паходаў — без блока
    await go("#/ru/notrack");
    await waitFor(() => [...panel().querySelectorAll("h2")].some((el) => el.textContent === "Я ходил сюда ещё"), "блок па-руску");
    await go("#/en/notrack");
    await waitFor(() => [...panel().querySelectorAll("h2")].some((el) => el.textContent === "More hikes here"), "блок па-англійску");
    await go("#/be/trek3");
    await waitFor(() => panel().querySelector("h1")?.textContent === "trek3", "паход без паўтораў");
    assert.equal(again(), undefined);
    assert.equal(panel().querySelector(".hike-list"), null);
  } finally {
    delete find("guria").related;
    delete find("notrack").related;
    find("late").date = find("late").end = "2025-01-01";
    await go("#/be");
  }
});

test("статыстыка па гадах і асабістыя рэкорды", async () => {
  const find = (slug) => DATA.hikes.find((hk) => hk.slug === slug);
  const before = DATA.hikes.map((hk) => [hk, hk.date, hk.end, hk.stats]);
  // прабел паміж разрадамі лічбы (1 200) залежыць ад браўзера — у параўнанні яго няма
  const text = (el) => el.textContent.replace(/\s+/g, " ").replace(/(\d) (?=\d)/g, "$1").trim();
  find("late").date = find("late").end = "2024-06-01";
  find("late").stats = stats(45, 300);
  find("guria").date = find("guria").end = "2024-07-01";
  try {
    await go("#/be");
    const link = panel().querySelector(".stats-link");
    assert.deepEqual([text(link), link.getAttribute("href")], ["Статыстыка па гадах", "#/be/stats"]);
    link.click();
    await waitFor(() => panel().querySelector(".stats-table"), "старонка статыстыкі");
    assert.equal(window.location.hash, "#/be/stats");
    assert.equal(document.title, "Статыстыка па гадах · Мае паходы");
    assert.equal(text(panel().querySelector("h1")), "Статыстыка па гадах");
    const rows = (part) => [...panel().querySelectorAll(`.stats-table ${part} tr`)].map((tr) => [...tr.children].map(text));
    assert.deepEqual(rows("thead"), [["Год", "Паходаў", "Дзён", "Км", "Набор, м"]]);
    // 2025: slow, broken (2 дні), mismatch (2 дні), notrack, trek3 (3 дні, без трэку); 2024: late і guria
    assert.deepEqual(rows("tbody"), [["2025", "5", "9", "30", "300"], ["2024", "2", "2", "50", "1200"]]);
    assert.deepEqual(rows("tfoot"), [["Усяго", "7", "11", "80", "1500"]]);
    assert.deepEqual([...panel().querySelectorAll(".stats-bar")].map((b) => b.style.getPropertyValue("--w").trim()), ["60%", "100%"],
      "даўжыня палоскі — кіламетры года адносна найбольшага");

    const records = [...panel().querySelectorAll(".records > li")].map((li) =>
      [text(li.querySelector(".record-head span")), text(li.querySelector(".record-head b")), text(li.querySelector(".card-title"))]);
    assert.deepEqual(records, [
      ["Найдаўжэйшы паход", "45 км", "late"], ["Найбольшы набор вышыні", "900 м", "guria"], ["Найвышэйшы пункт", "2000 м", "slow"],
      ["Найдаўжэй у руху", "50 хв", "slow"], ["Найдаўжэйшы па днях", "3 дні", "trek3"],
    ]);
    // мова мяняецца, а старонка застаецца тая ж
    document.querySelector('[data-lang="ru"]').click();
    await waitFor(() => text(panel().querySelector("h1")) === "Статистика по годам", "статыстыка па-руску");
    assert.equal(window.location.hash, "#/ru/stats");
    assert.equal(text(panel().querySelector(".records > li .record-head span")), "Самый длинный поход");
    document.querySelector('[data-lang="be"]').click();
    await waitFor(() => text(panel().querySelector("h1")) === "Статыстыка па гадах", "зноў па-беларуску");

    // год — спасылка на спіс паходаў гэтага года; рэкорд — картка паходу
    panel().querySelector(".stats-year").click();
    await waitFor(() => window.location.hash === "#/be" && panel().querySelector(".result-count"), "спіс паходаў года");
    assert.equal(text(panel().querySelector(".result-count")), "5 паходаў");
    panel().querySelector(".reset").click();
    await go("#/be/stats");
    panel().querySelector(".records .card").click();
    await waitFor(() => window.location.hash === "#/be/late", "старонка паходу-рэкорду");
  } finally {
    for (const [hk, date, end, st] of before) Object.assign(hk, { date, end, stats: st });
    await go("#/be");
  }
});

test("звычайнае надвор'е па месяцах: табліца, вылучаныя месяцы паходу, крыніца", async () => {
  const guria = DATA.hikes.find((hk) => hk.slug === "guria");
  const months = Array.from({ length: 12 }, (_, i) => ({ max: i - 2, min: i - 9, precip: 100 + i, wet: i }));
  months[5] = { max: null, min: null, precip: null, wet: null };
  const group = () => [...panel().querySelectorAll(".p-group")].find((g) => g.querySelector("summary").textContent.startsWith("Звычайнае надвор'е"));
  const now = () => [...group().querySelectorAll("tbody tr.p-now th")].map((th) => th.textContent);
  try {
    guria.climate = { alt: 2100, years: [2020, 2024], months };
    await go("#/be/guria");
    await waitFor(() => group(), "блок звычайнага надвор'я");
    assert.equal(group().querySelector(".p-hint").textContent.replace(/\s+/g, " ").replace(/(\d) (?=\d)/g, "$1"), "у найвышэйшым пункце, 2100 м");
    assert.deepEqual([...group().querySelectorAll("thead th")].map((th) => th.textContent), ["Удзень, °C", "Уначы, °C", "Ападкі, мм", "Дні з ападкамі"]);
    const rows = [...group().querySelectorAll("tbody tr")].map((tr) => [...tr.children].map((c) => c.textContent));
    assert.equal(rows.length, 12);
    assert.deepEqual(rows[0], ["студз", "−2", "−9", "100", "0"]);
    assert.deepEqual(rows[2], ["сак", "0", "−7", "102", "2"]);
    assert.deepEqual(rows[5], ["чэрв", "—", "—", "—", "—"], "месяц без даных");
    assert.deepEqual(rows[11], ["снеж", "+9", "+2", "111", "11"]);
    assert.deepEqual(now(), ["студз"], "паход 1 студзеня");
    assert.match(group().querySelector(".p-source").textContent, /^Паводле архіва Open-Meteo \(рэаналіз ERA5-Land і ERA5\): сярэдняе за 2020–2024/);

    // паход праз некалькі месяцаў і праз Новы год
    guria.end = "2025-03-02";
    await go("#/be/slow");
    await go("#/be/guria");
    await waitFor(() => group(), "блок пасля змены дат");
    assert.deepEqual(now(), ["студз", "лют", "сак"]);
    guria.date = "2024-12-30";
    guria.end = "2025-01-02";
    await go("#/en/guria");
    await waitFor(() => [...panel().querySelectorAll(".p-group summary")].some((s) => s.textContent.startsWith("Typical weather")), "па-англійску");
    const en = [...panel().querySelectorAll(".p-group")].find((g) => g.querySelector("summary").textContent.startsWith("Typical weather"));
    assert.deepEqual([...en.querySelectorAll("tbody tr.p-now th")].map((th) => th.textContent), ["Jan", "Dec"]);
    // паход без звычайнага надвор'я — без блока
    delete guria.climate;
    await go("#/be/slow");
    await go("#/be/guria");
    await waitFor(() => panel().querySelector("h1")?.textContent === "guria", "паход без звычайнага надвор'я");
    assert.equal(group(), undefined);
  } finally {
    delete guria.climate;
    guria.date = guria.end = "2025-01-01";
    await go("#/be");
  }
});

test("спіс паходаў: маленькая вокладка замест мініяцюры", async () => {
  const guria = DATA.hikes.find((hk) => hk.slug === "guria");
  const img = () => [...panel().querySelectorAll(".card")].find((c) => c.querySelector(".card-title").textContent === "guria")
    .querySelector("img.thumb");
  guria.photos = [{ src: "photos/guria/a.webp", thumb: "photos/guria/a-t.webp", w: 2000, h: 1500 }];
  guria.cover = 0;
  try {
    guria.coverSmall = "covers/guria-0123abcd.webp";
    await go("#/be/slow");
    await go("#/be");
    assert.equal(img().getAttribute("src"), "covers/guria-0123abcd.webp");
    assert.deepEqual([img().getAttribute("width"), img().getAttribute("height"), img().getAttribute("loading")], ["72", "72", "lazy"]);
    // файл вокладкі не загрузіўся — мініяцюра; калі не загрузілася і яна, далей нічога не пытаецца
    img().dispatchEvent(new window.Event("error"));
    assert.equal(img().getAttribute("src"), "photos/guria/a-t.webp");
    img().dispatchEvent(new window.Event("error"));
    assert.equal(img().getAttribute("src"), "photos/guria/a-t.webp");
    // індэкс ранейшай версіі (без маленькай вокладкі) — мініяцюра
    delete guria.coverSmall;
    await go("#/be/slow");
    await go("#/be");
    assert.equal(img().getAttribute("src"), "photos/guria/a-t.webp");
  } finally {
    guria.photos = [];
    guria.cover = null;
    delete guria.coverSmall;
    await go("#/be");
  }
});

test("даныя паходаў бяруцца па адрасе з версіяй з index.html", () => {
  const meta = document.querySelector('meta[name="hikes-data"]');
  assert.match(meta.content, /^data\/hikes\.json\?v=[0-9a-f]{10}$/, "версію даных запісвае `hike build`");
  assert.equal(fetched[0], meta.content);
});

// Тэсты інтэрфейсу: app.js у jsdom з падмененымі MapLibre і fetch.
// Запуск: node --test tests/js/   (або праз python -m unittest — tests/test_frontend.py)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, before, test } from "node:test";
import { JSDOM } from "jsdom";

const ROOT = new URL("../../", import.meta.url);

// --- Падмены ------------------------------------------------------------------

class FakeSource {
  constructor(id) { this.id = id; this.data = null; }
  setData(data) { this.data = data; }
  setTiles() {}
  getClusterExpansionZoom() { return Promise.resolve(10); }
}

class FakeMap {
  static last = null;
  constructor(opts) {
    FakeMap.last = this;
    this.handlers = {};
    this.sources = Object.fromEntries(Object.keys(opts.style.sources).map((id) => [id, new FakeSource(id)]));
    setTimeout(() => this.fire("styledata"), 0);
  }
  on(type, layerOrFn, fn) { (this.handlers[type] ||= []).push(fn || layerOrFn); }
  off(type, fn) { this.handlers[type] = (this.handlers[type] || []).filter((f) => f !== fn); }
  fire(type) { for (const f of [...(this.handlers[type] || [])]) f({}); }
  getSource(id) { return this.sources[id]; }
  getCanvas() { return { style: {} }; }
  getBearing() { return 0; }
  addControl() {} setLayoutProperty() {} setPaintProperty() {} setFeatureState() {}
  fitBounds() {} flyTo() {} easeTo() {} resize() {} setTerrain() {} setSky() {}
}

const maplibregl = {
  Map: FakeMap,
  NavigationControl: class {},
  ScaleControl: class {},
  Popup: class { setLngLat() { return this; } setDOMContent() { return this; } addTo() { return this; } remove() {} },
  LngLatBounds: class { extend() { return this; } },
};

const day = (date, km) => ({ date, name: "t", distance: km, up: 100, down: 100, duration: 3600, moving: 3000,
  maxAlt: 2000, minAlt: 1000, komoot: [] });
const stats = (distance, up) => ({ distance, up, down: 100, duration: 3600, moving: 3000, maxAlt: 2000, minAlt: 1000 });
const hike = (slug, region, extra = {}) => ({
  slug, date: "2025-01-01", end: "2025-01-01", title: { be: slug, en: slug }, region, difficulty: null,
  stats: { distance: 10, up: 100, down: 100, duration: 3600, moving: 3000, maxAlt: 2000, minAlt: 1000 },
  days: [day("2025-01-01", 10)], point: [42, 41], bbox: [42, 41, 42.1, 41.1], lines: [[[42, 41], [42.1, 41.1]]],
  track: `data/tracks/${slug}.json`, cover: null, photos: [], videos: [], impressions: { be: "", en: "" }, ...extra,
});
const DATA = {
  version: "9.8.7",
  hikes: [
    hike("slow", { be: "Аджарыя", en: "Adjara" }),
    hike("guria", { be: "Гурыя", en: "Guria" }, { difficulty: "expert", stats: stats(5, 900) }),
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
function fakeFetch(url) {
  const json = (data) => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });
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
  assert.deepEqual([...panel().querySelectorAll(".filter-row select")].map((el) => el.value), ["", "", "", ""]);
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
    assert.deepEqual(copied, ["http://localhost/#/be/guria"]);
    // буфер абмену недаступны — спасылка паказваецца для ручнога капіявання
    clipboard(async () => { throw new Error("denied"); });
    share.click();
    await waitFor(() => prompts.length === 1, "запасны спосаб");
    assert.deepEqual(prompts, ["http://localhost/#/be/guria"]);

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

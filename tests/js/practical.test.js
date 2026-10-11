// Тэсты блока «Практычная інфармацыя» на краёвых даных: модуль practical.js у jsdom, без карты і сеткі.
// Запуск: node --test tests/js/practical.test.js   (або праз python -m unittest — tests/test_frontend.py)
import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><body></body>");
globalThis.document = dom.window.document;
globalThis.Node = dom.window.Node;
const { renderPractical } = await import(new URL("../../docs/assets/practical.js", import.meta.url));
const { hikePois } = await import(new URL("../../docs/assets/pois.js", import.meta.url));

const end = (time, alt = 500) => ({ at: [42, 41], alt, time });
const hike = (extra) => ({ days: [{}], photos: [], facts: null, practical: null, ...extra });
const rows = (nodes) => {
  const box = document.createElement("div");
  box.append(...nodes);
  return Object.fromEntries([...box.querySelectorAll(".p-facts dt")].map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
};
const texts = (nodes) => {
  const box = document.createElement("div");
  box.append(...nodes);
  return [...box.querySelectorAll(".p-text, .p-meta, .p-hint")].map((el) => el.textContent);
};

test("светлавы дзень: запас, фініш пасля заходу і начны выхад", () => {
  const daylight = (sun) => rows(renderPractical(hike({ facts: { type: "loop", start: end("08:00"), finish: end("12:00"), sun } }), null, () => {}))["Светлавы дзень"];
  assert.equal(daylight({ rise: "05:44", set: "20:39", left: 519 }), "05:44–20:39 · пасля фінішу заставалася 8 г 39 хв святла");
  assert.equal(daylight({ rise: "05:44", set: "20:39", left: -321 }), "05:44–20:39 · фініш праз 5 г 21 хв пасля заходу сонца");
  assert.equal(daylight({ rise: "05:43", set: "20:39", night: true }), "05:43–20:39 · фініш да ўзыходу сонца");
  assert.equal(daylight({ rise: "05:43", set: "20:39" }), "05:43–20:39", "час фінішу невядомы — толькі ўзыход і заход");
});

test("расход вады: невядомая сума не паказваецца як лічба", () => {
  const water = (w) => texts(renderPractical(hike({
    days: [{}, {}],
    practical: { checked: null, weatherNotes: null, notes: {}, gettingThere: [], places: [],
      water: { available: null, note: { be: "", en: "" }, sources: [], taken: null, refilled: null, left: null, ...w } },
  }), null, () => {}));
  // спіс па днях з невядомым другім днём: ні сумы, ні нормаў
  assert.deepEqual(water({ days: [{ drunk: 2 }, { drunk: null }], drunk: null }), ["Няма звестак пра ваду на маршруце."]);
  assert.deepEqual(water({ days: [{ drunk: 2 }, { drunk: 3 }], drunk: 5, perHour: 0.62, per10km: 6.2 }),
    ["Выпіта 5 л", "Няма звестак пра ваду на маршруце.", "Выпіта 5 л (0,62 л за гадзіну на маршруце · 6,2 л на 10 км)"]);
});

test("без разліку і без звестак аўтара блока няма", () => {
  assert.equal(renderPractical(hike({}), null, () => {}), null);
});

test("выбраны дзень: наяўнасць вады — толькі па крыніцах гэтага дня (review v1.4.2, №1)", () => {
  const source = (day, water) => ({
    type: "spring", name: { be: "", en: "" }, note: { be: "", en: "" }, water, seasonal: null, treat: null, seen: null,
    photo: null, ref: `km:${day + 1}:1`, pos: { day, km: 1, at: [42, 41], alt: 900, time: "09:00" },
  });
  const view = (day, sources, available = null) => texts(renderPractical(hike({
    days: [{}, {}],
    practical: { checked: null, weatherNotes: null, notes: {}, gettingThere: [], places: [],
      water: { available, note: { be: "", en: "" }, sources, days: [], drunk: null, taken: null, refilled: null, left: null } },
  }), day, () => {}));
  const yes = "На маршруце ёсць вада.";
  const mixed = [source(0, true), source(1, false)];
  assert.ok(view(null, mixed).includes(yes), "увесь паход: вада пацверджана");
  assert.ok(view(0, mixed).includes(yes), "дзень 1: вада пацверджана");
  assert.ok(!view(1, mixed).includes(yes), "дзень 2: крыніца сухая — наяўнасць вады не сцвярджаецца");
  assert.ok(view(1, mixed).includes("вады не было"));
  assert.deepEqual(view(1, [source(0, true)]), ["Няма звестак пра ваду на маршруце."], "дзень 2 без крыніц");
  assert.ok(view(1, mixed, true).includes("У паходзе ў цэлым вада ёсць; пра гэты дзень асобных звестак няма."));
  assert.ok(view(null, [source(1, false)], true).includes(yes), "агульная пазнака аўтара для ўсяго паходу");
  assert.ok(view(1, [], false).includes("Вады на маршруце няма."));
});

const groupsOf = (nodes) => {
  const box = document.createElement("div");
  box.append(...nodes);
  return [...box.querySelectorAll(".p-group")].map((g) => ({
    title: g.querySelector("summary").textContent,
    lines: [...g.querySelectorAll(".p-title, .p-meta, .p-text, .p-note")].map((el) => el.textContent),
    sources: [...g.querySelectorAll(".p-source")].map((el) => el.textContent),
  }));
};
const MODEL = "Паводле мадэлі Open-Meteo — гэта разлік па сетцы, а не замер на сцежцы";

test("надвор'е: па пунктах для дня, радок на дзень для ўсяго паходу, асобна назіранні аўтара", () => {
  const point = (role, time, alt, extra) => ({ role, time, alt, temp: 14.6, feels: 14.9, precip: 0, snow: 0, wind: 7.4, gusts: 18.4, cloud: 100, code: 3, ...extra });
  const rainy = {
    points: [point("start", "10:33", 867, { precip: 0.6, code: 53 }), point("top", "13:12", 975, { temp: -2.6, feels: -7.4, snow: 1.2, code: 73 }),
      point("finish", "18:52", 882, { cloud: null, gusts: null })],
    span: { tempMin: -3.2, tempMax: 15.4, precip: 5.1, snow: 2, wind: 9.7, gusts: 25.6, code: 73 },
  };
  const dry = { points: [point("start", "08:00", 500, {})], span: { tempMin: 20.2, tempMax: 20.4, precip: 0, snow: 0, wind: 3 } };
  const notes = { checked: null, weatherNotes: { be: "На хрыбце туман.", en: "" }, notes: {}, gettingThere: [], places: [], water: null };

  const [one] = groupsOf(renderPractical(hike({ days: [{ date: "2026-07-12", weather: rainy }], practical: notes }), null, () => {}));
  assert.equal(one.title, "Надвор'еад −3 °C да +15 °C");
  assert.deepEqual(one.lines, [
    "Старт · 10:33 · 867 м", "+15 °C · імжа · вецер 7 км/г, парывы 18 · воблачнасць 100 % · ападкі 0,6 мм за гадзіну",
    "Найвышэйшы пункт · 13:12 · 975 м", "−3 °C, адчуваецца як −7 °C · снег · вецер 7 км/г, парывы 18 · воблачнасць 100 % · снег 1,2 см за гадзіну",
    "Фініш · 18:52 · 882 м", "+15 °C · пахмурна · вецер 7 км/г",
    "За час паходу: ад −3 °C да +15 °C · ападкі да 5,1 мм · снег да 2 см · вецер да 10 км/г, парывы да 26",
    "На хрыбце туман.",
  ]);
  assert.deepEqual(one.sources, [MODEL, "Мае назіранні"], "кожная частка падпісана сваёй крыніцай");

  const two = hike({ days: [{ date: "2026-09-12", weather: rainy }, { date: "2026-09-13", weather: dry }] });
  const [whole] = groupsOf(renderPractical(two, null, () => {}));
  assert.deepEqual(whole.lines, [
    "Дзень 1 · 12 верасня: ад −3 °C да +15 °C · ападкі да 5,1 мм · снег да 2 см · вецер да 10 км/г, парывы да 26",
    "Дзень 2 · 13 верасня: +20 °C · без ападкаў · вецер да 3 км/г",
  ]);
  assert.deepEqual(whole.sources, [MODEL]);
  const [second] = groupsOf(renderPractical(two, 1, () => {}));
  assert.deepEqual(second.lines, ["Старт · 08:00 · 500 м", "+15 °C · пахмурна · вецер 7 км/г, парывы 18 · воблачнасць 100 %",
    "За час паходу: +20 °C · без ападкаў · вецер да 3 км/г"]);

  // сума ападкаў невядомая (у адказе сэрвісу была прапушчаная гадзіна) — пра ападкі нічога не сцвярджаецца (review v1.6.0, №3)
  const unknown = { points: [point("start", "08:00", 500, {})], span: { tempMin: 20.2, tempMax: 20.4, snow: 0, wind: 3 } };
  const [gap] = groupsOf(renderPractical(hike({ days: [{ date: "2026-09-13", weather: unknown }] }), null, () => {}));
  assert.equal(gap.lines.at(-1), "За час паходу: +20 °C · вецер да 3 км/г");

  // без мадэлі — толькі назіранні аўтара; без таго і другога групы няма
  const [own] = groupsOf(renderPractical(hike({ practical: notes }), null, () => {}));
  assert.deepEqual([own.lines, own.sources], [["На хрыбце туман."], ["Мае назіранні"]]);
  assert.equal(renderPractical(hike({ days: [{ date: "2026-07-12" }] }), null, () => {}), null);
});

test("як дабрацца: цана ў лары і разлік у далярах з датай курсу", () => {
  const way = (extra) => ({ type: "taxi", from: { be: "Батумі", en: "" }, to: { be: "Мілісі", en: "" }, minutes: null, km: null,
    price: null, priceFor: null, trip: null, date: null, boarding: { be: "", en: "" }, road: { be: "", en: "" }, note: { be: "", en: "" },
    usd: null, rateDate: null, ...extra });
  const view = (ways) => groupsOf(renderPractical(hike({
    practical: { checked: null, weatherNotes: null, notes: {}, gettingThere: ways, places: [], water: null },
  }), null, () => {}))[0];
  const priced = view([way({ price: 80, priceFor: "car", usd: 30.76, rateDate: "2026-10-10" }), way({ type: "marshrutka", price: 5, usd: 1.92, rateDate: "2026-10-10" })]);
  assert.deepEqual(priced.lines, ["Таксі · Батумі → Мілісі", "80 ₾ (≈ 31 $), за машыну", "Маршрутка · Батумі → Мілісі", "5 ₾ (≈ 1,9 $)"]);
  assert.deepEqual(priced.sources, ["Мае назіранні", "Даляры — разлік па курсе Нацбанка Грузіі на 10 кастрычніка 2026"]);
  const plain = view([way({ price: 80 })]);
  assert.deepEqual([plain.lines[1], plain.sources], ["80 ₾", ["Мае назіранні"]], "без курсу — толькі лары");
});

test("падказкі OpenStreetMap і дарога на машыне: без звестак аўтара, па днях", () => {
  const hint = (day, kind, type, osm) => ({ kind, type, name: { be: "", en: "" }, osm, pos: { day, km: 1.5, at: [42 + day, 41], alt: 800 } });
  const two = hike({
    days: [{}, {}], hints: [hint(0, "water", "tap", "node/1"), hint(1, "place", "camp", "node/2")],
    drive: { from: { be: "Батумі", en: "Batumi" }, to: { km: 8.26, minutes: 14 } },
  });
  const DRIVE = "Разлік сэрвісу маршрутаў OSRM па дарогах OpenStreetMap — без уліку затораў і стану дарогі";
  const OSM = "З OpenStreetMap, мной не праверана";
  const whole = groupsOf(renderPractical(two, null, () => {}));
  assert.deepEqual(whole.map((g) => g.title), ["Як дабрацца8 км, каля 14 хв", "Падказкі з OpenStreetMap2"]);
  assert.deepEqual([whole[0].lines, whole[0].sources], [["На машыне ад: Батумі", "да старту: 8,3 км, каля 14 хв"], [DRIVE]],
    "без звестак аўтара подпісу «Мае назіранні» няма");
  assert.deepEqual(whole[1].sources, [OSM]);
  assert.deepEqual(whole[1].lines.filter((line) => line.includes("км ад старту")),
    ["Дзень 1, 1,5 км ад старту · 800 м", "Дзень 2, 1,5 км ад старту · 800 м"]);

  const second = groupsOf(renderPractical(two, 1, () => {}));
  assert.equal(second[1].title, "Падказкі з OpenStreetMap1", "выбраны дзень — толькі яго падказкі");
  assert.ok(second[1].lines.includes("Месца для начоўкі") && !second[1].lines.includes("Кран"));
  assert.equal(groupsOf(renderPractical(hike({ days: [{}, {}], hints: [hint(0, "water", "tap", "node/1")] }), 1, () => {}) || []).length, 0,
    "у дня без падказак групы няма");

  // кропкі для карты і профілю: запісы без месца на трэку прапускаюцца, падказкі пазначаныя
  const source = (pos) => ({ type: "spring", name: { be: "Халодная", en: "" }, note: { be: "", en: "" }, water: true, pos });
  const full = { ...two, practical: { places: [], water: { sources: [source({ day: 0, km: 2, at: [42.5, 41.5], alt: 700, time: "10:00" }), source(null)] } } };
  assert.deepEqual(hikePois(full, null).map((p) => [p.kind, p.hint, p.day, p.km, p.at, p.title, p.source]), [
    ["water", false, 0, 2, [42.5, 41.5], "Крыніца: Халодная", "Мае назіранні"],
    ["water", true, 0, 1.5, [42, 41], "Кран", OSM],
    ["place", true, 1, 1.5, [43, 41], "Месца для начоўкі", OSM],
  ]);
  assert.deepEqual(hikePois(full, 1).map((p) => p.title), ["Месца для начоўкі"]);
  assert.deepEqual(hikePois(hike({}), null), [], "паход без звестак і падказак — без кропак");
});

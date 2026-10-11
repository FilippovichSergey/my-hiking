// Тэсты блока «Практычная інфармацыя» на краёвых даных: модуль practical.js у jsdom, без карты і сеткі.
// Запуск: node --test tests/js/practical.test.js   (або праз python -m unittest — tests/test_frontend.py)
import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><body></body>");
globalThis.document = dom.window.document;
globalThis.Node = dom.window.Node;
const { renderPractical } = await import(new URL("../../docs/assets/practical.js", import.meta.url));

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

// Тэсты профілю вышыні: кропкі вады і месцаў на лініі профілю. Модуль profile.js у jsdom, без карты і сеткі.
// Запуск: node --test tests/js/profile.test.js   (або праз python -m unittest — tests/test_frontend.py)
import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";

const dom = new JSDOM('<!doctype html><body><div id="profile"></div></body>');
globalThis.document = dom.window.document;
globalThis.Node = dom.window.Node;
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
const { renderProfile } = await import(new URL("../../docs/assets/profile.js", import.meta.url));

// дзень з пунктамі праз кожны кіламетр: [lng, lat, вышыня, км ад пачатку дня]
const day = (alts) => ({ color: "#123456", segs: [alts.map((alt, i) => [42 + i / 100, 41, alt, i])] });

test("профіль вышыні: кропкі вады і месцаў стаяць на лініі, назва — у падказцы", () => {
  const box = document.getElementById("profile");
  Object.defineProperty(box, "clientWidth", { value: 452 }); // поле малюнка 400 пікселяў: 5 км → 80 пікселяў на кіламетр
  const days = [day([1000, 1100, 1200, 1100]), day([1100, 1000, 900])]; // 3 км і 2 км
  const marks = [
    { day: 0, km: 1, kind: "water", hint: false, title: "Крыніца" },
    { day: 1, km: 1, kind: "place", hint: true, title: "Аглядная пляцоўка · OSM" }, // другі дзень: 3 + 1 = 4 км на профілі
    { day: 5, km: 1, kind: "place", hint: false, title: "такога дня ў трэку няма" },
    { day: 0, km: NaN, kind: "place", hint: false, title: "без адлегласці" },
  ];
  const hovered = [];
  const profile = renderProfile(box, days, (p) => hovered.push(p), marks);
  const circles = [...box.querySelectorAll("circle.mark")];
  assert.deepEqual(circles.map((c) => [c.getAttribute("class"), c.getAttribute("cx"), c.getAttribute("cy")]), [
    ["mark mark-water", "124.0", "56.0"],             // 1 км, 1100 м
    ["mark mark-place mark-hint", "364.0", "102.0"],  // 4 км, 1000 м
  ], "кропкі без месца на профілі прапускаюцца");

  const svg = box.querySelector("svg");
  const move = (x) => svg.dispatchEvent(new dom.window.MouseEvent("pointermove", { clientX: x }));
  const markName = () => box.querySelector(".tooltip .tip-mark")?.textContent ?? null;
  move(124);
  assert.equal(markName(), "Крыніца");
  assert.equal(hovered.at(-1).km, 1, "пункт на карце па-ранейшаму паказваецца");
  move(204);
  assert.equal(markName(), null, "далёка ад кропак — толькі вышыня і адлегласць");
  move(362);
  assert.equal(markName(), "Аглядная пляцоўка · OSM");
  profile.destroy();

  // без кропак профіль малюецца як раней
  renderProfile(box, days, null);
  assert.equal(box.querySelectorAll("circle.mark").length, 0);
});

test("спрошчаны трэк: кропка стаіць на сваім кіламетры, а не на бліжэйшым пункце (review v1.6.1, №2)", () => {
  const box = document.getElementById("profile");
  const sparse = (alts) => ({ color: "#123456", segs: [alts.map((alt, i) => [42 + i / 100, 41, alt, i])] });
  const mark = (day, km, title) => ({ day, km, kind: "water", hint: false, title });
  const circles = () => [...box.querySelectorAll("circle.mark")].map((c) => [c.getAttribute("cx"), c.getAttribute("cy")]);
  const svg = () => box.querySelector("svg");
  const markName = () => box.querySelector(".tooltip .tip-mark")?.textContent ?? null;

  // прамы адрэзак у 1 км захаваны двума пунктамі; вышыня расце з 1000 да 1200 м
  renderProfile(box, [sparse([1000, 1200])], null, [mark(0, 0.3, "Крыніца"), mark(0, 0.4, "Кран"), mark(0, 1.04, "Каля фінішу")]);
  assert.deepEqual(circles(), [["164.0", "106.6"], ["204.0", "92.8"], ["444.0", "10.0"]],
    "X — па кіламетры кропкі, вышыня — па прамой паміж пунктамі; кіламетр за канцом трэку — на яго канцы");
  // перакрыжаванне скача па пунктах трэку (0 і 1 км), а назва кропкі шукаецца каля паказальніка
  svg().dispatchEvent(new dom.window.MouseEvent("pointermove", { clientX: 166 }));
  assert.equal(markName(), "Крыніца");
  svg().dispatchEvent(new dom.window.MouseEvent("pointermove", { clientX: 201 }));
  assert.equal(markName(), "Кран");
  svg().dispatchEvent(new dom.window.MouseEvent("pointermove", { clientX: 300 }));
  assert.equal(markName(), null);

  // мяжа дзён з рознымі вышынямі: кропка бярэ вышыню свайго дня
  renderProfile(box, [sparse([1000, 1000]), sparse([2000, 2000])], null, [mark(0, 1, "Канец першага дня"), mark(1, 0, "Пачатак другога"), mark(1, 0.5, "Сярэдзіна другога")]);
  assert.deepEqual(circles(), [["244.0", "148.0"], ["244.0", "10.0"], ["344.0", "10.0"]]);
});

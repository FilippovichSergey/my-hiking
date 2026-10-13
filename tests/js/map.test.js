// Тэсты карты: значкі фота, іх аб'яднанне і акенца з фота. Модуль map.js у jsdom з падмененым MapLibre.
// Запуск: node --test tests/js/map.test.js   (або праз python -m unittest — tests/test_frontend.py)
import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";

import { FakeMap, maplibregl } from "./fakes.js";

const dom = new JSDOM('<!doctype html><body><div id="map"></div></body>', { url: "http://localhost/" });
const globals = {
  window: dom.window, document: dom.window.document, Node: dom.window.Node, localStorage: dom.window.localStorage,
  matchMedia: () => ({ matches: false }), getComputedStyle: dom.window.getComputedStyle.bind(dom.window), innerWidth: 1200,
  ResizeObserver: class { observe() {} disconnect() {} }, maplibregl,
};
for (const [k, v] of Object.entries(globals)) Object.defineProperty(globalThis, k, { value: v, configurable: true, writable: true });
const { createMap } = await import(new URL("../../docs/assets/map.js", import.meta.url));

const tick = () => new Promise((r) => setTimeout(r, 10));
const photo = (index, lng, lat, w = 2000, h = 1500) => ({
  index, at: [lng, lat], thumb: `photos/h/p${index}-t.webp`, w, h, title: `Паход · фота ${index + 1}`, where: `${index + 1} км ад старту`,
});
const pins = () => maplibregl.Marker.all.filter((m) => m.element.classList.contains("photo-pin"));
const labels = () => pins().map((m) => m.element.textContent || "камера");

test("фота на карце: значкі, аб'яднанне блізкіх і акенца з фота", async () => {
  const map = createMap(document.getElementById("map"), { onSelect() {} });
  const opened = [];
  // на маштабе 12 (каля 29 px на 0,01°): фота 0 і 1 побач, 2 — асобна, 3 і 4 — у адным пункце
  const items = [photo(0, 42.000, 41.0), photo(1, 42.004, 41.0), photo(2, 42.1, 41.0), photo(3, 42.3, 41.0, 1500, 2000), photo(4, 42.3, 41.0)];
  map.setPhotos(items, (index) => opened.push(index)); // да загрузкі стылю выклік чакае ў чарзе
  assert.equal(pins().length, 0);
  await tick();
  const fake = FakeMap.last;
  assert.deepEqual(labels(), ["2", "камера", "2"]);
  assert.deepEqual(pins().map((m) => [m.at, m.element.getAttribute("aria-label")]), [
    [[42.0, 41.0], "2 фота, зробленыя побач"], [[42.1, 41.0], "Фота 3: паказаць, дзе зроблена"], [[42.3, 41.0], "2 фота, зробленыя побач"],
  ]);
  assert.ok(pins()[1].element.querySelector("svg path"), "у адзіночнага значка — камера");

  // адно фота: акенца з мініяцюрай і месцам, без стрэлак; націск на фота адкрывае галерэю і закрывае акенца
  pins()[1].element.click();
  let pop = maplibregl.Popup.open.content;
  const img = pop.querySelector(".photo-pop-img img");
  assert.deepEqual([img.getAttribute("src"), img.getAttribute("alt"), img.getAttribute("width"), img.getAttribute("height")],
    ["photos/h/p2-t.webp", "Паход · фота 3", "232", "174"]);
  assert.equal(pop.querySelector(".popup-meta").textContent, "3 км ад старту");
  assert.equal(pop.querySelector(".photo-pop-nav"), null);
  pop.querySelector(".photo-pop-img").click();
  assert.deepEqual(opened, [2]);
  assert.equal(maplibregl.Popup.open, null);

  // некалькі фота ў адным значку: лічыльнік і стрэлкі па крузе; вертыкальнае фота ўпісваецца па вышыні
  pins()[2].element.click();
  pop = () => maplibregl.Popup.open.content;
  const shown = () => [pop().querySelector("img").getAttribute("src"), pop().querySelector(".photo-pop-nav span").textContent];
  assert.deepEqual(shown(), ["photos/h/p3-t.webp", "1 з 2"]);
  assert.deepEqual([pop().querySelector("img").getAttribute("width"), pop().querySelector("img").getAttribute("height")], ["143", "190"]);
  assert.deepEqual([...pop().querySelectorAll(".photo-pop-nav button")].map((b) => b.getAttribute("aria-label")), ["Папярэдняе фота", "Наступнае фота"]);
  pop().querySelectorAll(".photo-pop-nav button")[1].click();
  assert.deepEqual(shown(), ["photos/h/p4-t.webp", "2 з 2"]);
  pop().querySelectorAll(".photo-pop-nav button")[1].click();
  assert.deepEqual(shown(), ["photos/h/p3-t.webp", "1 з 2"], "пасля апошняга — зноў першае");
  pop().querySelectorAll(".photo-pop-nav button")[0].click();
  assert.deepEqual(shown(), ["photos/h/p4-t.webp", "2 з 2"]);
  pop().querySelector(".photo-pop-img").click();
  assert.deepEqual(opened, [2, 4], "адкрываецца менавіта паказанае фота");

  // карта наблізілася: блізкія фота разышліся; фота ў адным пункце застаюцца разам
  pins()[2].element.click();
  fake.zoom = 15;
  fake.fire("zoomend");
  assert.deepEqual(labels(), ["камера", "камера", "камера", "2"]);
  assert.equal(maplibregl.Popup.open, null, "склад значкоў змяніўся — акенца закрылася");
  // той жа склад значкоў пасля яшчэ аднаго набліжэння: значкі і адкрытае акенца не перамалёўваюцца
  const before = pins();
  pins()[3].element.click();
  fake.zoom = 16;
  fake.fire("zoomend");
  assert.deepEqual(pins(), before);
  assert.notEqual(maplibregl.Popup.open, null);
  // карта аддаляецца: значкі зліваюцца, пакуль не застанецца адзін
  fake.zoom = 8;
  fake.fire("zoomend");
  assert.deepEqual(labels(), ["3", "2"]);
  fake.zoom = 6;
  fake.fire("zoomend");
  assert.deepEqual(labels(), ["5"]);

  // іншы паход або спіс паходаў: ранейшых значкоў няма
  map.setPhotos([photo(0, 43, 41)], () => {});
  assert.deepEqual(labels(), ["камера"]);
  map.clearSelection();
  assert.equal(pins().length, 0);
  assert.equal(maplibregl.Popup.open, null);
});

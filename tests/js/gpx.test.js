// Тэсты GPX: чысты модуль без DOM.
// Запуск: node --test tests/js/gpx.test.js   (або праз python -m unittest — tests/test_frontend.py)
import assert from "node:assert/strict";
import { test } from "node:test";

const { gpxText } = await import(new URL("../../docs/assets/gpx.js", import.meta.url));

test("GPX: трэк на дзень, адрэзкі, вышыня і экранаванне", () => {
  const xml = gpxText({
    name: 'Гара & "Возера" <1>',
    link: "https://example.org/#/be/hike?a=1&b=2",
    creator: "Мае паходы",
    tracks: [
      { name: "Дзень 1", segs: [[[42.1, 41.5, 1000, 0], [42.2, 41.6, null, 1]], [[42.3, 41.7, 1100.5, 2]]] },
      { name: "Дзень 2", segs: [[[42.4, 41.8, 900, 0]]] },
    ],
  });
  assert.ok(xml.startsWith('<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="Мае паходы" '));
  assert.ok(xml.includes('xmlns="http://www.topografix.com/GPX/1/1"'));
  assert.ok(xml.includes("<name>Гара &amp; &quot;Возера&quot; &lt;1&gt;</name>"));
  assert.ok(xml.includes('<link href="https://example.org/#/be/hike?a=1&amp;b=2"/>'));
  assert.equal(xml.match(/<trk>/g).length, 2);
  assert.equal(xml.match(/<trkseg>/g).length, 3);
  assert.deepEqual(xml.match(/<trkpt[^\n]*/g), [
    '<trkpt lat="41.5" lon="42.1"><ele>1000</ele></trkpt>',
    '<trkpt lat="41.6" lon="42.2"></trkpt>', // без вышыні
    '<trkpt lat="41.7" lon="42.3"><ele>1100.5</ele></trkpt>',
    '<trkpt lat="41.8" lon="42.4"><ele>900</ele></trkpt>',
  ]);
  assert.ok(xml.trimEnd().endsWith("</gpx>"));
});

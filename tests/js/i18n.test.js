// Тэсты перакладаў: усе мовы маюць адны і тыя ж тэксты, запасная мова, даты і лічбы. Модуль i18n.js без DOM.
// Запуск: node --test tests/js/i18n.test.js   (або праз python -m unittest — tests/test_frontend.py)
import assert from "node:assert/strict";
import { test } from "node:test";

const { LANGS, STRINGS, dateRange, duration, hasText, num, pick, setLang, shortDate, t } =
  await import(new URL("../../docs/assets/i18n.js", import.meta.url));

/** Форма значэння: тып, а для ўкладзеных спісаў — іх ключы. */
function shape(value) {
  if (typeof value === "function") return `function/${value.length}`;
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((k) => [k, shape(value[k])]));
  return typeof value;
}

test("мовы сайта: беларуская, руская, англійская — з аднолькавым наборам тэкстаў", () => {
  assert.deepEqual(LANGS, ["be", "ru", "en"]);
  assert.deepEqual(Object.keys(STRINGS).sort(), [...LANGS].sort());
  const base = shape(STRINGS.be);
  for (const l of LANGS) {
    assert.deepEqual(shape(STRINGS[l]), base, `набор тэкстаў мовы ${l} супадае з беларускім`);
    const empty = Object.entries(STRINGS[l]).flatMap(([key, value]) =>
      (typeof value === "string" ? [[key, value]] : value && typeof value === "object" ? Object.entries(value).map(([k, v]) => [`${key}.${k}`, v]) : []))
      .filter(([, value]) => typeof value === "string" && !value.trim()).map(([key]) => key);
    assert.deepEqual(empty, [], `у мове ${l} няма пустых тэкстаў`);
  }
  // рускія тэксты — сапраўды рускія, а не скапіяваныя беларускія
  const same = Object.keys(STRINGS.be).filter((key) => typeof STRINGS.be[key] === "string" && STRINGS.be[key] === STRINGS.ru[key]);
  assert.deepEqual(same.sort(), ["down", "km", "litre", "m", "map", "osmMark", "start", "up", "viewMap"],
    "аднолькавыя ў дзвюх мовах толькі словы, якія пішуцца аднолькава");
});

test("руская мова: множны лік, даты, лічбы, працягласць", () => {
  setLang("ru");
  assert.deepEqual([1, 2, 5, 11, 21, 22, 25, 34].map((n) => t("found", n)),
    ["1 поход", "2 похода", "5 походов", "11 походов", "21 поход", "22 похода", "25 походов", "34 похода"]);
  assert.deepEqual([1, 3, 42].map((n) => t("statDays", n)), ["1 день в горах", "3 дня в горах", "42 дня в горах"]);
  assert.equal(t("sourcesCount", 3), "3 источника");
  assert.equal(dateRange("2026-10-10", "2026-10-10"), "10 октября 2026");
  assert.equal(dateRange("2026-09-12", "2026-09-13"), "12–13 сентября 2026");
  assert.equal(dateRange("2025-12-31", "2026-01-02"), "31 декабря 2025 – 2 января 2026");
  assert.equal(shortDate("2026-05-09"), "9 мая");
  assert.equal(num(21.46, 1), "21,5");
  assert.equal(duration(3900), "1 ч 05 мин");
  assert.equal(t("steepUp", 21, "1,2", t("day", 2)), "самый крутой подъём 21 % на 500 м (День 2, от 1,2 км)");
  assert.deepEqual(t("altAt", "975", "11,2"), ["975 м", "11,2 км"]);
  setLang("be");
});

test("тэкст з даных: мова інтэрфейсу, а калі яго няма — блізкая мова", () => {
  const full = { be: "Возера", ru: "Озеро", en: "Lake" };
  const seen = (pair) => Object.fromEntries(LANGS.map((l) => { setLang(l); return [l, pick(pair)]; }));
  assert.deepEqual(seen(full), { be: "Возера", ru: "Озеро", en: "Lake" });
  assert.deepEqual(seen({ be: "Возера", en: "Lake" }), { be: "Возера", ru: "Возера", en: "Lake" }, "без рускага — беларускі, а не англійскі");
  assert.deepEqual(seen({ be: "Возера", en: "" }), { be: "Возера", ru: "Возера", en: "Возера" });
  assert.deepEqual(seen({ be: "", en: "", ru: "Озеро" }), { be: "Озеро", ru: "Озеро", en: "Озеро" });
  assert.deepEqual(seen({ be: "", en: "Lake" }), { be: "Lake", ru: "Lake", en: "Lake" });
  assert.deepEqual(seen(null), { be: "", ru: "", en: "" });
  assert.deepEqual([hasText(full), hasText({ be: "", en: "" }), hasText({ be: "", en: "", ru: "Озеро" }), hasText(null)],
    [true, false, true, false]);
  setLang("de");
  assert.equal(pick(full), "Возера", "невядомая мова — беларуская");
  setLang("be");
});

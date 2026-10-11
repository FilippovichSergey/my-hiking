// Блок «Практычная інфармацыя» на старонцы паходу.
// facts — разлік па трэку (тып маршруту, старт і фініш, светлавы дзень, тэмп, рэльеф);
// days[].weather — надвор'е паводле мадэлі Open-Meteo; practical — звесткі аўтара з файла паходу.
// Пад кожнай часткай падпісана, адкуль даныя.
import { h } from "./dom.js";
import { dateRange, duration, num, pick, shortDate, t } from "./i18n.js";

const join = (parts, sep = " · ") => parts.filter(Boolean).join(sep);
const has = (pair) => Boolean(pair && (pair.be || pair.en));
const oneDate = (iso) => dateRange(iso, iso);
const source = (key) => h("p", { class: "p-source" }, t(key));
const row = (label, ...content) => [h("dt", null, label), h("dd", null, content)];
const noteLine = (label, pair) => (has(pair) ? h("p", { class: "p-note" }, h("b", null, `${label}: `), pick(pair)) : null);

/** Згорнутая частка блока; sourceKey = null, калі подпісы крыніц ужо стаяць унутры (некалькі крыніц). */
function group(title, hint, nodes, sourceKey = "srcAuthor") {
  return h("details", { class: "p-group" },
    h("summary", null, title, hint ? h("span", { class: "p-hint" }, hint) : null),
    nodes, sourceKey ? source(sourceKey) : null);
}

/** Разлік па трэку: спіс «назва — значэнне». whole — увесь шматдзённы паход (час і сонца ў яго па днях). */
function factsBlock(facts, whole) {
  const rows = [];
  if (facts.type) {
    rows.push(row(t("routeType"), t("routeTypes")[facts.type],
      facts.typeBy === "author" ? h("span", { class: "p-muted" }, ` · ${t("byAuthor")}`) : null));
  }
  const end = (e) => join([!whole && e.time, e.alt != null && `${num(e.alt)} ${t("m")}`]);
  if (facts.start) {
    const [lng, lat] = facts.start.at;
    rows.push(row(t("start"), end(facts.start), " ", h("a", {
      class: "p-link", target: "_blank", rel: "noopener",
      href: `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`,
    }, t("routeToStart"), " ↗")));
  }
  if (facts.finish && end(facts.finish)) rows.push(row(t("finish"), end(facts.finish)));
  if (facts.sun && !whole) {
    const left = facts.sun.left;
    rows.push(row(t("daylight"), join([`${facts.sun.rise}–${facts.sun.set}`,
      facts.sun.night ? t("daylightNight")
        : left == null ? null : left >= 0 ? t("daylightLeft", duration(left * 60)) : t("daylightOver", duration(-left * 60))])));
  }
  const pace = join([facts.speed != null && t("speed", num(facts.speed, 1)), facts.stops ? t("stops", duration(facts.stops)) : null]);
  if (pace) rows.push(row(t("pace"), pace));
  const steep = (s, key) => (s ? t(key, s.grade, num(s.km, 1), s.day != null ? t("day", s.day + 1) : null) : null);
  const relief = join([facts.gain != null && t("gainPerKm", num(facts.gain)), steep(facts.steepUp, "steepUp"),
    steep(facts.steepDown, "steepDown")]);
  if (relief) rows.push(row(t("relief"), relief));
  return rows.length ? [h("dl", { class: "p-facts" }, rows), source("srcTrack")] : [];
}

/** Запіс з месцам на трэку (крыніца вады або асаблівае месца). */
function placedItem(item, title, flags, hike, openPhoto) {
  const pos = item.pos;
  const where = pos ? join([
    `${hike.days.length > 1 ? `${t("day", pos.day + 1)}, ` : ""}${t("kmFromStart", num(pos.km, 1))}`,
    pos.alt != null && `${num(pos.alt)} ${t("m")}`, pos.time, pos.off ? t("offTrack", num(pos.off)) : null]) : null;
  const photo = item.photo != null ? hike.photos[item.photo] : null;
  return h("li", { class: "p-item" },
    photo ? h("button", { type: "button", class: "p-photo", "aria-label": t("openPhoto"), onclick: () => openPhoto(item.photo) },
      h("img", { src: photo.thumb, alt: "", loading: "lazy", width: 48, height: 48 })) : null,
    h("div", null,
      h("div", { class: "p-title" }, title),
      where ? h("div", { class: "p-meta" }, where) : null,
      flags.length ? h("div", { class: "p-meta" }, join(flags)) : null,
      has(item.note) ? h("div", { class: "p-text" }, pick(item.note)) : null,
      item.seen ? h("div", { class: "p-meta" }, t("seen", oneDate(item.seen))) : null));
}

const onDay = (items, day) => items.filter((it) => day == null || !it.pos || it.pos.day === day);
const named = (label, item) => (has(item.name) ? `${label}: ${pick(item.name)}` : label);

function wayItem(w) {
  // даляры — разлік па курсе на дату: менш за 10 $ з дзясятымі, інакш цэлыя
  const usd = w.usd != null ? ` (≈ ${num(w.usd, w.usd < 10 ? 1 : 0)} $)` : "";
  const price = w.price != null
    ? join([`${num(w.price)} ₾${usd}`, w.priceFor && t("priceFor")[w.priceFor], w.trip && t("trip")[w.trip]], ", ") : null;
  return h("li", { class: "p-item" }, h("div", null,
    h("div", { class: "p-title" }, join([t("transport")[w.type],
      has(w.from) || has(w.to) ? `${pick(w.from) || "…"} → ${pick(w.to) || "…"}` : null])),
    h("div", { class: "p-meta" }, join([w.minutes != null && duration(w.minutes * 60), w.km != null && `${num(w.km, 1)} ${t("km")}`,
      price, w.date && t("priceDate", oneDate(w.date))])),
    noteLine(t("boarding"), w.boarding), noteLine(t("road"), w.road),
    has(w.note) ? h("div", { class: "p-text" }, pick(w.note)) : null));
}

function waterGroup(w, hike, day, openPhoto) {
  const litres = (v) => `${num(v, 2)} ${t("litre")}`;
  const sources = onDay(w.sources, day);
  const d = day != null ? w.days[day] : w; // выбраны дзень або ўвесь паход
  // Для выбранага дня — толькі па яго крыніцах: вада, знойдзеная ў іншы дзень, нічога не кажа пра гэты.
  // Агульная пазнака паходу (available) паказваецца як звестка пра ўвесь маршрут.
  const found = sources.some((s) => s.water === true);
  const known = found ? "waterYes"
    : w.available === true ? (day == null ? "waterYes" : "waterYesHike")
      : w.available === false ? "waterNo" : sources.length ? null : "waterUnknown";
  const balance = d ? join([d.taken != null && `${t("taken")} ${litres(d.taken)}`,
    d.refilled != null && `${t("refilled")} ${litres(d.refilled)}`, d.left != null && `${t("left")} ${litres(d.left)}`]) : "";
  const rates = day == null ? join([w.perHour != null && t("perHour", num(w.perHour, 2)),
    w.per10km != null && t("per10km", num(w.per10km, 1))]) : "";
  const flags = (s) => [t("hadWater")[String(s.water)], s.seasonal ? t("seasonal") : null,
    s.treat === true ? t("treatYes") : s.treat === false ? t("treatNo") : null];
  return group(t("water"), join([d?.drunk != null && t("drunk", litres(d.drunk)), sources.length ? t("sourcesCount", sources.length) : null]), [
    known ? h("p", { class: "p-text" }, t(known)) : null,
    d?.drunk != null ? h("p", { class: "p-text" }, t("drunk", litres(d.drunk)), rates ? ` (${rates})` : "") : null,
    balance ? h("p", { class: "p-meta" }, balance) : null,
    has(w.note) ? h("p", { class: "p-text" }, pick(w.note)) : null,
    sources.length ? h("ul", { class: "p-list" },
      sources.map((s) => placedItem(s, named(t("sourceTypes")[s.type], s), flags(s), hike, openPhoto))) : null,
  ]);
}

// --- Надвор'е ---------------------------------------------------------------

const temp = (v) => `${Math.round(v) > 0 ? "+" : Math.round(v) < 0 ? "−" : ""}${num(Math.abs(Math.round(v)))} °C`;
const whole1 = (v) => (v != null ? num(Math.round(v)) : null);
/** Код надвор'я WMO → назва з'явы. */
function wmoKey(code) {
  if (code == null) return null;
  if (code === 0) return "clear";
  if (code <= 2) return "partly";
  if (code === 3) return "cloudy";
  if (code <= 48) return "fog";
  if (code <= 57) return "drizzle";
  if (code <= 67) return "rain";
  if (code <= 77) return "snow";
  if (code <= 82) return "showers";
  return code <= 86 ? "snow" : "storm";
}

/** Паказчыкі ў пункце: тэмпература, з'ява, вецер, воблачнасць, ападкі. Пустое значэнне прапускаецца. */
function reading(pt) {
  return join([
    pt.temp != null && join([temp(pt.temp),
      pt.feels != null && Math.round(pt.feels) !== Math.round(pt.temp) && t("wFeels", temp(pt.feels))], ", "),
    wmoKey(pt.code) && t("wmo")[wmoKey(pt.code)],
    pt.wind != null && t("wWind", whole1(pt.wind), whole1(pt.gusts)),
    pt.cloud != null && t("wCloud", whole1(pt.cloud)),
    pt.snow ? t("wSnow", num(pt.snow, 1)) : pt.precip ? t("wRain", num(pt.precip, 1)) : null,
  ]);
}

/** Межы за ўвесь час паходу. */
function spanLine(span) {
  return join([
    span.tempMin != null && t("wTemps", temp(span.tempMin), temp(span.tempMax)),
    span.precip ? t("wRainUpTo", num(span.precip, 1)) : span.precip === 0 && !span.snow ? t("wDry") : null,
    span.snow ? t("wSnowUpTo", num(span.snow, 1)) : null,
    span.wind != null && t("wWindUpTo", whole1(span.wind), whole1(span.gusts)),
  ]);
}

/** Надвор'е паводле мадэлі (для дня — па пунктах, для ўсяго шматдзённага паходу — радок на дзень) і назіранні аўтара. */
function weatherGroup(hike, day) {
  const shown = day != null ? [day] : hike.days.map((_, i) => i);
  const days = shown.filter((i) => hike.days[i]?.weather);
  const notes = hike.practical?.weatherNotes;
  if (!days.length && !has(notes)) return null;
  const model = [];
  if (days.length === 1 && shown.length === 1) {
    const w = hike.days[days[0]].weather;
    model.push(h("ul", { class: "p-list" }, w.points.map((pt) => h("li", { class: "p-item" }, h("div", null,
      h("div", { class: "p-title" }, join([t("wRoles")[pt.role], pt.time, pt.alt != null && `${num(pt.alt)} ${t("m")}`])),
      h("div", { class: "p-meta" }, reading(pt)))))));
    if (spanLine(w.span)) model.push(h("p", { class: "p-note" }, h("b", null, `${t("wSpan")}: `), spanLine(w.span)));
  } else {
    for (const i of days) {
      const line = spanLine(hike.days[i].weather.span);
      if (line) model.push(h("p", { class: "p-note" }, h("b", null, `${t("day", i + 1)} · ${shortDate(hike.days[i].date)}: `), line));
    }
  }
  const first = days.length ? hike.days[days[0]].weather.span : null;
  const hint = days.length === 1 && first?.tempMin != null ? t("wTemps", temp(first.tempMin), temp(first.tempMax)) : null;
  return group(t("weather"), hint, [
    model.length ? [model, source("srcWeather")] : null,
    has(notes) ? [h("p", { class: "p-text" }, pick(notes)), source("srcAuthor")] : null,
  ], null);
}

/** Вузлы блока для старонкі паходу; day — выбраны дзень шматдзённага паходу або null. */
export function renderPractical(hike, day, openPhoto) {
  const whole = day == null && hike.days.length > 1;
  const facts = day != null ? hike.days[day]?.facts : hike.facts;
  const p = hike.practical;
  const nodes = facts ? factsBlock(facts, whole) : [];
  if (p) {
    const notes = (keys) => keys.map((k) => noteLine(t("noteLabels")[k], p.notes[k])).filter(Boolean);
    if (p.gettingThere.length || has(p.notes.parking)) {
      const rateDays = [...new Set(p.gettingThere.filter((w) => w.usd != null).map((w) => w.rateDate))];
      nodes.push(group(t("gettingThere"), p.gettingThere.length ? t("waysCount", p.gettingThere.length) : null, [
        p.gettingThere.length ? h("ul", { class: "p-list" }, p.gettingThere.map(wayItem)) : null,
        ...notes(["parking"]),
        source("srcAuthor"),
        rateDays.length ? h("p", { class: "p-source" }, t("srcRate", rateDays.map(oneDate).join(", "))) : null,
      ], null));
    }
    if (p.water) nodes.push(waterGroup(p.water, hike, day, openPhoto));
    const places = onDay(p.places, day);
    if (places.length) {
      nodes.push(group(t("places"), String(places.length), [h("ul", { class: "p-list" },
        places.map((it) => placedItem(it, named(t("placeTypes")[it.type], it), [], hike, openPhoto)))]));
    }
    const trail = notes(["surface", "marking", "obstacles", "signal", "dangers", "gear", "season"]);
    if (trail.length) nodes.push(group(t("trail"), null, trail));
    const stay = notes(["overnight", "after", "suitable"]);
    if (stay.length) nodes.push(group(t("stay"), null, stay));
  }
  const weather = weatherGroup(hike, day);
  if (weather) nodes.push(weather);
  if (p?.checked) nodes.push(h("p", { class: "p-source" }, t("checked", oneDate(p.checked))));
  return nodes.length ? [h("h2", null, t("practical")), ...nodes] : null;
}

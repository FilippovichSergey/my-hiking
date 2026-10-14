// Галоўны модуль: даныя, маршрутызацыя (#/be/<slug>), панэль са спісам і старонкай паходу.
import { copyText, cssVar, downloadFile, h, s } from "./dom.js";
import { gpxText } from "./gpx.js";
import { renderPractical } from "./practical.js";
import { dateRange, duration, durationParts, lang, LANGS, num, pick, setLang, shortDate, t } from "./i18n.js";
import { createMap } from "./map.js";
import { hikePhotos, hikePois } from "./pois.js";
import { renderProfile } from "./profile.js";

const PSWP_URL = "https://cdn.jsdelivr.net/npm/photoswipe@5.4.4/dist/photoswipe.esm.min.js";
// Версія файлаў сайта (яе ставіць `hike build` у адрас гэтага модуля): з ёй бяруцца і астатнія файлы,
// каб пасля публікацыі браўзер не змяшаў новыя файлы са старымі з кэша.
const ASSET_QUERY = new URL(import.meta.url).search;
const DATA_URL = document.querySelector('meta[name="hikes-data"]')?.content || "data/hikes.json";
const DIFF_LEVEL = { easy: 1, medium: 2, hard: 3, expert: 4 };
// узровень → сімвал у assets/difficulty-sprite.svg (сілуэт з 1–4 вяршынямі)
const DIFF_ICON = { easy: "easy", medium: "moderate", hard: "hard", expert: "very-hard" };
const MOUNTAIN_ICON = '<svg viewBox="0 0 32 32" aria-hidden="true"><path d="M2 27 12 9l6 10 4-6 8 14z"/></svg>';

const body = document.getElementById("panel-body");
const state = {
  hikes: [],
  filters: { q: "", year: "", region: "", difficulty: "", distance: "", days: "", type: "", cond: "" },
  sort: "new",
  selected: null,
  day: null,
  view: "list", // тэлефон: што на экране — панэль ("list") ці карта ("map")
  version: "", // версія сайта з індэкса (яе запісвае `hike build` з файла VERSION)
  share: false, // ці ёсць старонкі <мова>/<паход>/ з прэв'ю для спасылак
  stats: false, // ці адкрыта старонка «Статыстыка па гадах»
};
const trackCache = new Map();
let map, profile, renderToken = 0;

// --- Мова і тэма ------------------------------------------------------------

// адрас старонкі: #/<мова>, #/<мова>/<паход> або #/<мова>/stats (статыстыка па гадах; паход так звацца не можа —
// імя файла паходу пачынаецца з даты)
const ROUTE_RE = new RegExp(`^#/(${LANGS.join("|")})(?:/([\\w-]+))?`);
const STATS = "stats";

function initialLang() {
  const fromHash = location.hash.match(ROUTE_RE);
  if (fromHash) return fromHash[1];
  try {
    const saved = localStorage.getItem("lang");
    if (LANGS.includes(saved)) return saved;
  } catch {}
  const browser = navigator.language || "";
  return /^ru/i.test(browser) ? "ru" : /^(be|uk)/i.test(browser) ? "be" : "en";
}

const darkMq = matchMedia("(prefers-color-scheme: dark)");
function pinnedScheme() {
  const c = document.documentElement.classList;
  return c.contains("theme-dark") ? "dark" : c.contains("theme-light") ? "light" : null;
}
function isDark() { return (pinnedScheme() || (darkMq.matches ? "dark" : "light")) === "dark"; }
function setScheme(scheme) {
  const root = document.documentElement;
  root.classList.remove("theme-light", "theme-dark");
  if (scheme) root.classList.add(`theme-${scheme}`);
  document.querySelector('meta[name="color-scheme"]').content = scheme || "light dark";
  try {
    if (scheme) localStorage.setItem("color-scheme", scheme);
    else localStorage.removeItem("color-scheme");
  } catch {}
  onThemeChange();
}
function toggleTheme() {
  const next = isDark() ? "light" : "dark";
  const system = darkMq.matches ? "dark" : "light";
  setScheme(next === system ? null : next); // вяртанне да сістэмнай тэмы здымае замацаванне
}
function onThemeChange() {
  const btn = document.getElementById("theme-toggle");
  btn.setAttribute("aria-label", isDark() ? t("themeToLight") : t("themeToDark"));
  btn.title = btn.getAttribute("aria-label");
  map?.refreshTheme();
  if (state.selected) renderDetail(state.selected, false);
}

// На тэлефоне панэль і карта не змяшчаюцца разам: паказваецца нешта адно (гл. .layout[data-view] у CSS).
function setView(view) {
  state.view = view;
  document.querySelector(".layout").dataset.view = view;
  updateViewToggle();
}
function updateViewToggle() {
  document.getElementById("view-seg").setAttribute("aria-label", t("view"));
  for (const b of document.querySelectorAll("[data-view-btn]")) {
    const view = b.dataset.viewBtn;
    // без перакладу (у кэшы браўзера яшчэ стары i18n.js) застаецца подпіс з разметкі
    b.textContent = (view === "map" ? t("viewMap") : state.selected ? t("viewHike") : t("viewList")) || b.textContent;
    b.setAttribute("aria-pressed", String(view === state.view));
  }
}

function applyLangChrome() {
  document.documentElement.lang = lang;
  document.getElementById("site-title").textContent = t("siteTitle");
  document.getElementById("panel").setAttribute("aria-label", t("panel"));
  document.getElementById("map").setAttribute("aria-label", t("map"));
  document.querySelector('meta[name="description"]').content = t("siteDescription");
  for (const b of document.querySelectorAll("[data-lang]")) b.setAttribute("aria-pressed", String(b.dataset.lang === lang));
  map?.updateControls();
  updateViewToggle();
  onThemeChange();
}

// --- Маршрутызацыя ----------------------------------------------------------

function go(slug) { location.hash = slug ? `#/${lang}/${slug}` : `#/${lang}`; }

function route() {
  const m = location.hash.match(ROUTE_RE);
  const newLang = m ? m[1] : lang;
  if (newLang !== lang) {
    setLang(newLang);
    try { localStorage.setItem("lang", newLang); } catch {}
    applyLangChrome();
  }
  const slug = m?.[2];
  if (slug === STATS) {
    renderStats();
    return;
  }
  const hike = slug && state.hikes.find((hk) => hk.slug === slug);
  if (hike) {
    if (state.selected !== hike) state.day = null;
    renderDetail(hike, state.selected !== hike);
  } else {
    renderList();
  }
}

// --- Фільтры і агульная статыстыка ------------------------------------------

/** Ключ рэгіёна не залежыць ад мовы інтэрфейсу, каб фільтр перажываў пераключэнне BE/EN. */
function regionKey(hk) { return hk.region.en || hk.region.be || hk.region.ru || ""; }

// Нагрузка: агульная адлегласць паходу (км) і колькасць дзён.
const DISTANCES = { short: [0, 10], mid: [10, 20], long: [20, Infinity] };
const DAY_COUNTS = { one: (n) => n <= 1, multi: (n) => n > 1 };
/** Колькасць дзён паходу: дні з трэкам, а без трэку — каляндарныя дні ад пачатку да заканчэння. */
function dayCount(hk) {
  if (hk.days.length) return hk.days.length;
  const span = Math.round((Date.parse(hk.end) - Date.parse(hk.date)) / 864e5) + 1;
  return span > 1 ? span : 1;
}
// Умовы — толькі паводле звестак аўтара: падказкі OpenStreetMap не правераныя і ў фільтр не ідуць.
const CONDITIONS = {
  water: (hk) => hk.practical?.water?.available === true || (hk.practical?.water?.sources || []).some((s) => s.water === true),
  transit: (hk) => (hk.practical?.gettingThere || []).some((w) => ["marshrutka", "bus", "train"].includes(w.type)),
};
// Паходы без трэку (без лічбаў) пры сартаванні па нагрузцы ідуць у канец.
const byStat = (key, dir) => (a, b) =>
  dir * ((a.stats?.[key] ?? dir * Infinity) - (b.stats?.[key] ?? dir * Infinity));
const SORTS = {
  new: (a, b) => b.date.localeCompare(a.date),
  old: (a, b) => a.date.localeCompare(b.date),
  short: byStat("distance", 1),
  long: byStat("distance", -1),
  up: byStat("up", -1),
  low: byStat("up", 1),
};

function filtered() {
  const { q, year, region, difficulty, distance, days, type, cond } = state.filters;
  const needle = q.trim().toLowerCase();
  const [minKm, maxKm] = DISTANCES[distance] || [];
  return state.hikes.filter((hk) =>
    (!year || hk.date.startsWith(year)) &&
    (!region || regionKey(hk) === region) &&
    (!difficulty || hk.difficulty === difficulty) &&
    (!distance || (hk.stats != null && hk.stats.distance >= minKm && hk.stats.distance < maxKm)) &&
    (!days || DAY_COUNTS[days](dayCount(hk))) &&
    (!type || hk.facts?.type === type) &&
    (!cond || CONDITIONS[cond](hk)) &&
    (!needle || LANGS.flatMap((l) => [hk.title[l], hk.region[l]]).some((s) => s?.toLowerCase().includes(needle))));
}

/** Працягласць для пліткі: лічбы буйна, адзінкі дробна, як у астатніх плітках — інакш на тэлефоне
 *  «6 ч 49 мин» і «6 h 49 min» не змяшчаюцца ў плітку. */
function durationValue(sec) {
  return durationParts(sec).flatMap(([value, unit], i) => [`${i ? " " : ""}${value}`, h("small", null, unit)]);
}

function tile(label, value, unit, sub) {
  return h("div", { class: "tile" },
    h("div", { class: "tile-label" }, label),
    h("div", { class: "tile-value" }, value, unit ? h("small", null, unit) : null),
    sub ? h("div", { class: "tile-sub", title: sub }, sub) : null);
}

function summaryTiles(hikes) {
  const days = hikes.reduce((n, hk) => n + dayCount(hk), 0);
  const km = hikes.reduce((n, hk) => n + (hk.stats?.distance || 0), 0);
  const up = hikes.reduce((n, hk) => n + (hk.stats?.up || 0), 0);
  const top = hikes.filter((hk) => hk.stats?.maxAlt).sort((a, b) => b.stats.maxAlt - a.stats.maxAlt)[0];
  return h("div", { class: "stats", style: "grid-template-columns: repeat(2, 1fr)" },
    tile(t("statHikes"), num(hikes.length), null, t("statDays", days)),
    tile(t("statDistance"), num(km), t("km")),
    tile(t("statUp"), num(up), t("m")),
    tile(t("statTop"), top ? num(top.stats.maxAlt) : "—", top ? t("m") : null, top ? pick(top.title) : null));
}

function diffBadge(level) {
  if (!level) return null;
  return h("span", { class: "diff", dataset: { difficulty: level } },
    s("svg", { class: "diff-icon", viewBox: "0 0 32 32", "aria-hidden": "true" },
      s("use", { href: `assets/difficulty-sprite.svg${ASSET_QUERY}#difficulty-${DIFF_ICON[level]}` })),
    t("difficulty")[level]);
}

/** Кароткія лічбы паходу: адлегласць, набор вышыні, колькасць дзён (калі больш за адзін). */
function hikeNums(hk) {
  const nums = [];
  if (hk.stats) nums.push(`${num(hk.stats.distance, 1)} ${t("km")}`, `↑ ${num(hk.stats.up)} ${t("m")}`);
  if (dayCount(hk) > 1) nums.push(t("days", dayCount(hk)));
  if (hk.facts?.type) nums.push(t("routeShort")[hk.facts.type]);
  return nums;
}

/** Радок падказкі маркера на карце: адлегласць, набор вышыні і складанасць. */
function popupNums(hk) {
  const nums = hikeNums(hk).slice(0, 2);
  if (!nums.length && !hk.difficulty) return null;
  return h("div", { class: "popup-nums" }, nums.map((n) => h("span", null, n)), diffBadge(hk.difficulty));
}

function chip(label, pressed, onclick, key) {
  return h("button", { type: "button", class: "chip", "aria-pressed": String(pressed), onclick },
    key ? h("span", { class: "key", style: `--key:${key}` }) : null, label);
}

function select(options, value, allLabel, onchange, label) {
  return h("select", { "aria-label": label, onchange: (e) => onchange(e.target.value) },
    h("option", { value: "" }, allLabel),
    options.map(([v, text]) => h("option", { value: v, selected: v === value }, text)));
}

/** Падвал панэлі: назва сайта і версія. */
function siteFooter() {
  return state.version ? h("footer", { class: "site-footer" }, `${t("siteTitle")} · ${t("version", state.version)}`) : null;
}

// --- Спіс паходаў -----------------------------------------------------------

function renderList() {
  ++renderToken; // незавершаны renderDetail() пасля await убачыць, што ён ужо неактуальны
  profile?.destroy();
  profile = null;
  const wasSelected = !!state.selected;
  state.selected = null;
  state.day = null;
  state.stats = false;
  updateViewToggle();
  document.title = t("siteTitle");
  map.clearSelection();

  const list = filtered().sort(SORTS[state.sort]);
  const years = [...new Set(state.hikes.map((hk) => hk.date.slice(0, 4)))].sort().reverse();
  const regions = [...new Map(state.hikes.filter(regionKey).map((hk) => [regionKey(hk), pick(hk.region)]))]
    .sort((a, b) => a[1].localeCompare(b[1], lang));
  const setFilter = (key, value) => { state.filters[key] = value; renderList(); };
  // Фільтр па ўмовах з'яўляецца, калі хоць у аднаго паходу аўтар запоўніў такія звесткі.
  const conds = Object.keys(CONDITIONS).filter((key) => state.hikes.some(CONDITIONS[key]));
  const reset = Object.values(state.filters).some(Boolean)
    ? h("button", {
      type: "button", class: "reset",
      onclick: () => { for (const key of Object.keys(state.filters)) state.filters[key] = ""; renderList(); },
    }, t("reset"))
    : null;
  // Пасля фільтрацыі карта сама не рухаецца: кнопка набліжае яе да знойдзеных паходаў
  // (на тэлефоне яшчэ і пераключае з панэлі на карту).
  const showOnMap = reset && list.some((hk) => hk.point)
    ? h("button", {
      type: "button", class: "link-btn show-on-map",
      onclick: () => { setView("map"); map.fitHikes(list); },
    }, actionIcon("M12 21s-6.5-6.1-6.5-11a6.5 6.5 0 0 1 13 0c0 4.9-6.5 11-6.5 11zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z"), t("showOnMap"))
    : null;

  const search = h("input", {
    class: "search", type: "search", placeholder: t("search"), "aria-label": t("search"), value: state.filters.q,
    oninput: (e) => {
      state.filters.q = e.target.value;
      const pos = e.target.selectionStart;
      renderList();
      const again = body.querySelector(".search");
      again.focus();
      again.setSelectionRange(pos, pos);
    },
  });

  // replaceChildren ператварае null у тэкст «null», таму неабавязковыя вузлы адсейваюцца
  body.replaceChildren(...[
    summaryTiles(list),
    h("a", { class: "link-btn stats-link", href: `#/${lang}/${STATS}` }, actionIcon(STATS_ICON), t("statsTitle")),
    h("div", { class: "filters" },
      search,
      h("div", { class: "chips", role: "group", "aria-label": t("allYears") },
        chip(t("allYears"), !state.filters.year, () => setFilter("year", "")),
        years.map((y) => chip(y, state.filters.year === y, () => setFilter("year", state.filters.year === y ? "" : y)))),
      h("div", { class: "filter-row" },
        select(regions, state.filters.region, t("allRegions"), (v) => setFilter("region", v), t("allRegions")),
        select(Object.keys(DIFF_LEVEL).map((d) => [d, t("difficulty")[d]]), state.filters.difficulty, t("allDifficulty"),
          (v) => setFilter("difficulty", v), t("allDifficulty"))),
      h("div", { class: "filter-row" },
        select(Object.keys(DISTANCES).map((d) => [d, t("distances")[d]]), state.filters.distance, t("allDistances"),
          (v) => setFilter("distance", v), t("allDistances")),
        select(Object.keys(DAY_COUNTS).map((d) => [d, t("dayCounts")[d]]), state.filters.days, t("allDayCounts"),
          (v) => setFilter("days", v), t("allDayCounts"))),
      h("div", { class: "filter-row" },
        select(Object.keys(t("routeShort")).map((k) => [k, t("routeShort")[k]]), state.filters.type, t("allRouteTypes"),
          (v) => setFilter("type", v), t("allRouteTypes")),
        conds.length ? select(conds.map((k) => [k, t("conditions")[k]]), state.filters.cond, t("allConditions"),
          (v) => setFilter("cond", v), t("allConditions")) : null)),
    h("div", { class: "result-row" },
      h("p", { class: "result-count", "aria-live": "polite" }, t("found", list.length)),
      reset,
      h("select", { class: "sort", "aria-label": t("sort"), onchange: (e) => { state.sort = e.target.value; renderList(); } },
        Object.keys(SORTS).map((key) => h("option", { value: key, selected: key === state.sort }, t("sorts")[key])))),
    showOnMap,
    list.length
      ? h("ul", { class: "hike-list" }, list.map(card))
      : h("p", { class: "empty" }, t("nothing")),
    siteFooter(),
  ].filter(Boolean));
  map.setHikes(list);
  if (wasSelected || !renderList.fitted) {
    map.fitHikes(list);
    renderList.fitted = true;
  }
}

function card(hk) {
  const cover = hk.photos[hk.cover ?? 0];
  const thumb = cover
    ? h("img", { class: "thumb", src: cover.thumb, alt: "", loading: "lazy", width: 72, height: 72 })
    : h("div", { class: "thumb placeholder" });
  if (!cover) thumb.innerHTML = MOUNTAIN_ICON;
  const nums = hikeNums(hk);
  return h("li", null, h("button", {
    type: "button", class: "card",
    onclick: () => go(hk.slug),
    onmouseenter: () => map.highlight(hk.slug),
    onmouseleave: () => map.highlight(null),
  },
  thumb,
  h("span", { class: "card-body" },
    h("span", { class: "card-title" }, pick(hk.title)),
    h("span", { class: "card-meta", style: "display:block" },
      [dateRange(hk.date, hk.end), pick(hk.region)].filter(Boolean).join(" · ")),
    h("span", { class: "card-nums" }, nums.map((n) => h("span", null, n)), diffBadge(hk.difficulty)))));
}

// --- Статыстыка па гадах і асабістыя рэкорды ----------------------------------

const STATS_ICON = "M4 20V11m6 9V5m6 15v-6m4 6H2";
// Рэкорд: які паказчык паходу параўноўваецца і як яго паказаць. Паход без паказчыка ў параўнанні не ўдзельнічае.
const RECORDS = {
  distance: [(hk) => hk.stats?.distance, (v) => `${num(v, 1)} ${t("km")}`],
  up: [(hk) => hk.stats?.up, (v) => `${num(v)} ${t("m")}`],
  alt: [(hk) => hk.stats?.maxAlt, (v) => `${num(v)} ${t("m")}`],
  moving: [(hk) => hk.stats?.moving, (v) => duration(v)],
  days: [(hk) => (dayCount(hk) > 1 ? dayCount(hk) : null), (v) => t("days", v)],
};

/** Паходы, кіламетры і набор вышыні па гадах (па даце пачатку паходу) і асабістыя рэкорды — па ўсіх паходах. */
function renderStats() {
  ++renderToken;
  profile?.destroy();
  profile = null;
  state.selected = null;
  state.day = null;
  state.stats = true;
  updateViewToggle();
  document.title = `${t("statsTitle")} · ${t("siteTitle")}`;
  map.clearSelection();
  map.setHikes(state.hikes);

  const sum = (hikes, value) => hikes.reduce((n, hk) => n + (value(hk) || 0), 0);
  const years = [...new Set(state.hikes.map((hk) => hk.date.slice(0, 4)))].sort().reverse()
    .map((year) => {
      const hikes = state.hikes.filter((hk) => hk.date.startsWith(year));
      return { year, hikes: hikes.length, days: sum(hikes, dayCount), km: sum(hikes, (hk) => hk.stats?.distance), up: sum(hikes, (hk) => hk.stats?.up) };
    });
  const widest = Math.max(1, ...years.map((y) => y.km));
  const cols = t("statsCols");
  const openYear = (year) => { state.filters.year = year; go(null); };
  const line = (head, y) => h("tr", null, head, h("td", null, num(y.hikes)), h("td", null, num(y.days)),
    h("td", { class: "stats-km" }, num(Math.round(y.km)),
      head.tagName === "TH" ? h("span", { class: "stats-bar", style: `--w:${Math.round((y.km / widest) * 100)}%`, "aria-hidden": "true" }) : null),
    h("td", null, num(y.up)));
  const total = { hikes: state.hikes.length, days: sum(state.hikes, dayCount), km: sum(state.hikes, (hk) => hk.stats?.distance), up: sum(state.hikes, (hk) => hk.stats?.up) };

  const records = Object.entries(RECORDS).map(([key, [value, show]]) => {
    const best = state.hikes.filter((hk) => value(hk) != null)
      .reduce((top, hk) => (!top || value(hk) > value(top) ? hk : top), null);
    return best ? h("li", null, h("div", { class: "record-head" }, h("span", null, t("recordNames")[key]), h("b", null, show(value(best)))),
      h("ul", { class: "hike-list" }, card(best))) : null;
  }).filter(Boolean);

  body.replaceChildren(...[
    h("button", { type: "button", class: "back", onclick: () => go(null) }, "← ", t("back")),
    h("article", { class: "detail" },
      h("h1", null, t("statsTitle")),
      state.hikes.length ? h("table", { class: "stats-table" },
        h("thead", null, h("tr", null, ["year", "hikes", "days", "km", "up"].map((k) => h("th", { scope: "col" }, cols[k])))),
        h("tbody", null, years.map((y) => line(h("th", { scope: "row" },
          h("button", { type: "button", class: "stats-year", title: t("statsYear", y.year), onclick: () => openYear(y.year) }, y.year)), y))),
        h("tfoot", null, line(h("td", null, t("statsTotal")), total))) : h("p", { class: "empty" }, t("nothing")),
      records.length ? [h("h2", null, t("records")), h("ul", { class: "records" }, records)] : null),
    siteFooter(),
  ].filter(Boolean));
  body.scrollTop = 0;
}

// --- Старонка паходу --------------------------------------------------------

async function loadTrack(hike) {
  if (!hike.track) return null;
  if (!trackCache.has(hike.slug)) {
    trackCache.set(hike.slug, fetch(hike.track).then((r) => (r.ok ? r.json() : null)).catch(() => null));
  }
  return trackCache.get(hike.slug);
}

/** Сталая спасылка на паход (з бягучай мовай). */
function hikeUrl(hike) { return `${location.origin}${location.pathname}#/${lang}/${hike.slug}`; }

/**
 * Спасылка, якой дзеляцца: старонка <мова>/<паход>/ паказвае ў Telegram і іншых праграмах назву, лічбы
 * і вокладку паходу і адразу пераводзіць на сайт. Калі такіх старонак няма — звычайная спасылка.
 */
function shareUrl(hike) {
  return state.share ? new URL(`${lang}/${hike.slug}/`, new URL(".", location.href)).href : hikeUrl(hike);
}

/** Дні трэку для GPX: увесь паход або выбраны дзень; пусты спіс, калі трэку для яго няма. */
function gpxTracks(hike, track, day) {
  const title = pick(hike.title);
  return (track?.days || []).map((d, i) => ({ i, segs: d.segs }))
    .filter((d) => (day == null || d.i === day) && d.segs.some((seg) => seg.length))
    .map((d) => ({
      segs: d.segs,
      name: hike.days.length > 1
        ? `${title} — ${t("day", d.i + 1)}${hike.days[d.i] ? `, ${shortDate(hike.days[d.i].date)}` : ""}`
        : title,
    }));
}

function downloadGpx(hike, track, day) {
  const text = gpxText({ name: pick(hike.title), link: hikeUrl(hike), creator: t("siteTitle"), tracks: gpxTracks(hike, track, day) });
  downloadFile(`${hike.slug}${day != null ? `-day-${day + 1}` : ""}.gpx`, text, "application/gpx+xml");
}

async function shareHike(hike, btn) {
  const url = shareUrl(hike);
  if (!(await copyText(url))) {
    window.prompt(t("copyLink"), url); // буфер абмену недаступны: спасылку можна скапіяваць уручную
    return;
  }
  const label = btn.querySelector(".btn-label");
  label.textContent = t("linkCopied");
  setTimeout(() => { label.textContent = t("share"); }, 2000);
}

function actionIcon(path) {
  return s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, s("path", { d: path }));
}

async function renderDetail(hike, fit = true) {
  const token = ++renderToken;
  state.selected = hike;
  state.stats = false;
  updateViewToggle();
  document.title = `${pick(hike.title)} · ${t("siteTitle")}`;
  const track = await loadTrack(hike);
  if (token !== renderToken) return;

  profile?.destroy();
  const day = state.day;
  const dayStats = day != null ? hike.days[day] : hike.stats;
  const colors = hike.days.map((_, i) => cssVar(`--series-${(i % 8) + 1}`));

  const sections = [];
  sections.push(h("button", { type: "button", class: "back", onclick: () => go(null) }, "← ", t("back")));

  const cover = hike.photos[hike.cover ?? 0];
  if (cover) {
    sections.push(h("img", {
      class: "cover", src: cover.src, alt: pick(hike.title), width: cover.w, height: cover.h, fetchpriority: "high",
      onclick: () => openGallery(hike, hike.cover ?? 0),
    }));
  }

  sections.push(h("article", { class: "detail" },
    h("h1", null, pick(hike.title)),
    h("div", { class: "meta" },
      h("span", null, dateRange(hike.date, hike.end)),
      pick(hike.region) ? h("span", null, pick(hike.region)) : null,
      dayCount(hike) > 1 ? h("span", null, t("days", dayCount(hike))) : null,
      diffBadge(hike.difficulty)),

    h("div", { class: "actions" },
      gpxTracks(hike, track, day).length ? h("button", {
        type: "button", class: "link-btn", onclick: () => downloadGpx(hike, track, day),
      }, actionIcon("M12 4v11m0 0-4-4m4 4 4-4M5 20h14"), t("downloadGpx")) : null,
      h("button", {
        type: "button", class: "link-btn", "aria-live": "polite", onclick: (e) => shareHike(hike, e.currentTarget),
      }, actionIcon("M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"),
      h("span", { class: "btn-label" }, t("share")))),

    dayStats ? h("div", { class: "stats" },
      tile(t("distance"), num(dayStats.distance, 1), t("km")),
      tile(t("up"), num(dayStats.up), t("m")),
      tile(t("down"), num(dayStats.down), t("m")),
      tile(t("duration"), durationValue(dayStats.duration)),
      tile(t("moving"), durationValue(dayStats.moving)),
      tile(t("maxAlt"), dayStats.maxAlt != null ? num(dayStats.maxAlt) : "—", dayStats.maxAlt != null ? t("m") : null),
    ) : h("p", { class: "notice" }, t("noTrack")),

    hike.days.length > 1 ? h("div", { class: "chips", role: "group", "aria-label": t("allDays") },
      chip(t("allDays"), day == null, () => setDay(hike, null)),
      hike.days.map((d, i) => chip(`${t("day", i + 1)} · ${shortDate(d.date)}`, day === i, () => setDay(hike, i), colors[i])),
    ) : null,

    track ? [h("h2", null, t("profile")), h("div", { class: "profile", id: "profile" })] : null,
    hike.track && !track ? h("p", { class: "notice" }, t("trackError")) : null,

    renderPractical(hike, day, (index) => openGallery(hike, index)),

    hike.photos.length ? [
      h("h2", null, `${t("photos")} · ${hike.photos.length}`),
      h("div", { class: "gallery" }, hike.photos.map((p, i) => h("a", {
        href: p.src,
        onclick: (e) => { e.preventDefault(); openGallery(hike, i); },
      }, h("img", { src: p.thumb, alt: `${pick(hike.title)} · ${i + 1}`, loading: "lazy", width: 160, height: 160 })))),
    ] : null,

    hike.videos.length ? [h("h2", null, t("videos")), h("div", { class: "videos" }, hike.videos.map(videoEmbed))] : null,

    h("h2", null, t("impressions")),
    impressions(hike),

    relatedHikes(hike).length ? [h("h2", null, t("again")), h("ul", { class: "hike-list" }, relatedHikes(hike).map(card))] : null,

    hike.days.some((d) => d.komoot.length) ? [
      h("h2", null, t("links")),
      h("div", { class: "links" }, hike.days.flatMap((d) => d.komoot.map((url) =>
        h("a", { class: "link-btn", href: url, target: "_blank", rel: "noopener" },
          `${t("komoot")}${hike.days.length > 1 ? ` · ${shortDate(d.date)}` : ""} ↗`)))),
    ] : null,
  ));

  sections.push(siteFooter());

  const scrollTop = body.scrollTop;
  body.replaceChildren(...sections.filter(Boolean));
  body.scrollTop = fit ? 0 : scrollTop;

  map.select(hike, track, day, fit);
  map.setHikes(filtered().includes(hike) ? filtered() : [...filtered(), hike]);
  // Крыніцы вады і месцы — на карце і на профілі вышыні; падказкі OpenStreetMap пазначаныя асобна.
  const pois = hikePois(hike, day);
  map.setPois?.(pois); // у кэшы браўзера яшчэ можа быць ранейшы map.js
  // Значкі фота — там, дзе зроблены здымкі; націск на фота ў акенцы адкрывае галерэю.
  map.setPhotos?.(hikePhotos(hike, day), (index) => openGallery(hike, index));

  const profEl = document.getElementById("profile");
  if (profEl && track) {
    const days = track.days.map((d, i) => ({ segs: d.segs, color: colors[i] }));
    const shown = day != null ? (days[day] ? [days[day]] : []) : days;
    const marks = pois.map((p) => ({
      day: day != null ? 0 : p.day, km: p.km, kind: p.kind, hint: p.hint, title: p.hint ? `${p.title} · ${t("osmMark")}` : p.title,
    }));
    profile = renderProfile(profEl, shown, (p) => map.setHoverPoint(p, p ? shown[p.day].color : null), marks);
  }
}

function setDay(hike, day) {
  // Пакуль загружаецца новы паход, на экране яшчэ кнопкі папярэдняга: іх націсканне не павінна
  // адмяняць адкрыццё новага і пакідаць чужую старонку пад яго адрасам.
  if (state.selected !== hike) return;
  state.day = day;
  renderDetail(hike, true);
}

/**
 * Іншыя паходы ў тое самае месца, найноўшыя першымі: з той жа назвай або па тым жа шляху.
 * Спіс `related` лічыць `hike build`; у кэшы браўзера яшчэ можа быць ранейшы індэкс без яго.
 */
function relatedHikes(hike) {
  return (hike.related || []).map((slug) => state.hikes.find((hk) => hk.slug === slug)).filter(Boolean);
}

function impressions(hike) {
  const text = pick(hike.impressions);
  if (!text) return h("p", { class: "notice" }, t("noImpressions"));
  return h("div", { class: "impressions" }, text.split(/\n\s*\n/).map((para) => h("p", null, para.trim())));
}

function videoEmbed(v) {
  const wrap = h("div");
  const btn = h("button", {
    type: "button", class: "yt", "aria-label": `${t("play")}: ${v.title || v.id}`,
    onclick: () => {
      btn.replaceChildren(h("iframe", {
        src: `https://www.youtube-nocookie.com/embed/${encodeURIComponent(v.id)}?autoplay=1&rel=0`,
        title: v.title || "YouTube",
        allow: "accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture; fullscreen",
        allowfullscreen: true,
      }));
    },
  },
  h("img", { src: `https://i.ytimg.com/vi/${encodeURIComponent(v.id)}/hqdefault.jpg`, alt: "", loading: "lazy", width: 480, height: 360 }),
  h("span", { class: "play", "aria-hidden": "true" }));
  wrap.append(btn, h("div", { class: "video-title" },
    v.title ? `${v.title} · ` : "",
    h("a", { href: `https://www.youtube.com/watch?v=${encodeURIComponent(v.id)}`, target: "_blank", rel: "noopener" }, t("youtube"), " ↗")));
  return wrap;
}

let PhotoSwipe;
async function openGallery(hike, index) {
  PhotoSwipe ??= (await import(PSWP_URL)).default;
  new PhotoSwipe({
    dataSource: hike.photos.map((p, i) => ({ src: p.src, msrc: p.thumb, width: p.w, height: p.h, alt: `${pick(hike.title)} · ${i + 1}` })),
    index,
    bgOpacity: 0.94,
    showHideAnimationType: "fade",
  }).init();
}

// --- Старт ------------------------------------------------------------------

async function main() {
  setLang(initialLang());
  // паход, выбраны на карце, адкрываецца старонкай: на тэлефоне — пераход з карты да апісання
  map = createMap(document.getElementById("map"), {
    onSelect: (slug) => { setView("list"); go(slug); },
    popupExtra: popupNums,
  });
  applyLangChrome();

  document.getElementById("theme-toggle").addEventListener("click", toggleTheme);
  darkMq.addEventListener("change", onThemeChange);
  for (const b of document.querySelectorAll("[data-lang]")) {
    b.addEventListener("click", () => {
      const slug = state.selected?.slug || (state.stats ? STATS : null);
      setLang(b.dataset.lang);
      try { localStorage.setItem("lang", b.dataset.lang); } catch {}
      applyLangChrome();
      go(slug);
      if (location.hash === `#/${lang}${slug ? `/${slug}` : ""}`) route();
    });
  }
  document.getElementById("brand").addEventListener("click", (e) => { e.preventDefault(); go(null); });
  for (const b of document.querySelectorAll("[data-view-btn]")) b.addEventListener("click", () => setView(b.dataset.viewBtn));

  const data = await fetch(DATA_URL).then((r) => r.json());
  state.hikes = data.hikes;
  state.version = data.version || "";
  state.share = data.share === true;
  window.addEventListener("hashchange", route);
  route();
}

main().catch((err) => {
  console.error(err);
  body.replaceChildren(h("p", { class: "empty" }, "⚠ ", String(err.message || err)));
});

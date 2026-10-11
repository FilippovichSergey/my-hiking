// Кропкі паходу на карце і профілі вышыні: крыніцы вады і асаблівыя месцы.
// Запісы аўтара (practical) — «Мае назіранні»; hints — аб'екты OpenStreetMap каля трэку, аўтарам не правераныя.
import { s } from "./dom.js";
import { num, pick, t } from "./i18n.js";

const has = (pair) => Boolean(pair && (pair.be || pair.en));

// Контурныя значкі 24×24; радок з аднаго-двух знакаў малюецца тэкстам.
const ICONS = {
  water: "M12 3.5c3.2 3.9 5.5 6.9 5.5 10a5.5 5.5 0 0 1-11 0c0-3.1 2.3-6.1 5.5-10z",
  firepit: "M12 3c1 3.5 5 5.5 5 10.5a5 5 0 0 1-10 0c0-2 .8-3.4 2-4.5.3 1.6 1 2.5 2 3 .2-3-.3-6 1-9z",
  shelter: "M3 11 12 5l9 6M6 11v8M18 11v8M6 15h12",
  swing: "M4 20 8 4h8l4 16M10 4v9M14 4v9M9 13h6",
  camp: "M3 19.5 12 5l9 14.5zM12 14l-3 5.5h6z",
  signpost: "M12 3v18M12 6h6l2 2-2 2h-6M12 12H6l-2 2 2 2h6",
  viewpoint: "M2.5 12S6 6 12 6s9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6zM12 14.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z",
  table: "M4 9h16M6 9l-2 10M18 9l2 10M3 14h18",
  toilet: "WC",
  parking: "P",
  obstacle: "M12 4 21 19.5H3zM12 10v4.5M12 17v.5",
  other: "M12 4.5l2.3 4.7 5.2.7-3.8 3.6.9 5.1L12 16.2l-4.6 2.4.9-5.1L4.5 9.9l5.2-.7z",
};

/** Значок кропкі: для вады — кропля, для месца — паводле тыпу. */
export function poiIcon(kind, type) {
  const shape = kind === "water" ? ICONS.water : ICONS[type] || ICONS.other;
  return s("svg", { class: "poi-icon", viewBox: "0 0 24 24", "aria-hidden": "true" },
    shape.length <= 2
      ? s("text", { x: 12, y: 16.5, "text-anchor": "middle", "font-size": shape.length > 1 ? 11 : 15 }, shape)
      : s("path", { d: shape }));
}

/** Назва запісу: тып («Крыніца») і ўласная назва, калі яна ёсць. */
export function poiTitle(kind, item) {
  const label = t(kind === "water" ? "sourceTypes" : "placeTypes")[item.type];
  return has(item.name) ? `${label}: ${pick(item.name)}` : label;
}

/** Дзе гэта на трэку: дзень (у шматдзённым паходзе), кіламетр, вышыня, час, адлегласць ад трэку. */
export function poiWhere(pos, hike) {
  return [
    `${hike.days.length > 1 ? `${t("day", pos.day + 1)}, ` : ""}${t("kmFromStart", num(pos.km, 1))}`,
    pos.alt != null && `${num(pos.alt)} ${t("m")}`, pos.time, pos.off ? t("offTrack", num(pos.off)) : null,
  ].filter(Boolean).join(" · ");
}

/**
 * Кропкі паходу з месцам на трэку; day — выбраны дзень або null (увесь паход).
 * Кропка: { kind: "water"|"place", type, hint, at: [lng, lat], day, km, title, where, source }.
 */
export function hikePois(hike, day) {
  const p = hike.practical;
  const items = [
    ...(p?.water?.sources || []).map((it) => ["water", it, false]),
    ...(p?.places || []).map((it) => ["place", it, false]),
    ...(hike.hints || []).map((it) => [it.kind, it, true]),
  ];
  return items.filter(([, it]) => it.pos && (day == null || it.pos.day === day)).map(([kind, it, hint]) => ({
    kind, hint, type: it.type, at: it.pos.at, day: it.pos.day, km: it.pos.km,
    title: poiTitle(kind, it), where: poiWhere(it.pos, hike), source: t(hint ? "srcOsm" : "srcAuthor"),
  }));
}

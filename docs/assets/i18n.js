// Пераклады інтэрфейсу і фарматаванне лічбаў/дат.

const STRINGS = {
  be: {
    siteTitle: "Мае паходы",
    siteDescription: "Карта маіх хайкінгаў: трэкі, фота, відэа і ўражанні.",
    panel: "Паходы",
    map: "Карта",
    view: "Выгляд",
    viewList: "Спіс",
    viewHike: "Апісанне",
    viewMap: "Карта",
    themeToDark: "Уключыць цёмную тэму",
    themeToLight: "Уключыць светлую тэму",
    statHikes: "Паходаў",
    statDays: (n) => {
      const word = plural(n, "дзень", "дні", "дзён");
      return `${num(n)} ${word} ${/[аеёіоуыэюя]$/.test(word) ? "ў" : "у"} гарах`; // «дні ў гарах», «дзён у гарах»
    },
    statDistance: "Пройдзена",
    statUp: "Набор вышыні",
    statTop: "Найвышэйшы пункт",
    search: "Пошук па назве ці рэгіёне",
    allYears: "Усе гады",
    allRegions: "Усе рэгіёны",
    allDifficulty: "Любая складанасць",
    found: (n) => `${n} ${plural(n, "паход", "паходы", "паходаў")}`,
    nothing: "Нічога не знойдзена. Паспрабуйце змяніць фільтры.",
    back: "Усе паходы",
    distance: "Адлегласць",
    up: "Набор",
    down: "Спуск",
    duration: "Час",
    moving: "У руху",
    maxAlt: "Макс. вышыня",
    days: (n) => `${n} ${plural(n, "дзень", "дні", "дзён")}`,
    day: (i) => `Дзень ${i}`,
    allDays: "Усе дні",
    profile: "Профіль вышыні",
    impressions: "Уражанні",
    noImpressions: "Уражанні яшчэ не напісаныя.",
    videos: "Відэа",
    photos: "Фота",
    links: "Спасылкі",
    komoot: "Трэк у Komoot",
    youtube: "Глядзець на YouTube",
    play: "Прайграць відэа",
    noTrack: "Трэк для гэтага паходу не запісаны.",
    trackError: "Не ўдалося загрузіць трэк. Абнавіце старонку пазней.",
    km: "км",
    m: "м",
    h: "г",
    min: "хв",
    layers: { topo: "Тапакарта", sat: "Спадарожнік", plain: "Схема" },
    terrain3d: "3D-рэльеф",
    difficulty: { easy: "Лёгка", medium: "Сярэдне", hard: "Цяжка", expert: "Вельмі цяжка" },
    altAt: (alt, km) => [`${alt} м`, `${km} км`],
  },
  en: {
    siteTitle: "My hikes",
    siteDescription: "A map of my hikes: tracks, photos, videos and impressions.",
    panel: "Hikes",
    map: "Map",
    view: "View",
    viewList: "List",
    viewHike: "Details",
    viewMap: "Map",
    themeToDark: "Switch to dark theme",
    themeToLight: "Switch to light theme",
    statHikes: "Hikes",
    statDays: (n) => `${num(n)} ${n === 1 ? "day" : "days"} in the mountains`,
    statDistance: "Distance",
    statUp: "Elevation gain",
    statTop: "Highest point",
    search: "Search by name or region",
    allYears: "All years",
    allRegions: "All regions",
    allDifficulty: "Any difficulty",
    found: (n) => `${n} ${n === 1 ? "hike" : "hikes"}`,
    nothing: "Nothing found. Try changing the filters.",
    back: "All hikes",
    distance: "Distance",
    up: "Ascent",
    down: "Descent",
    duration: "Time",
    moving: "Moving",
    maxAlt: "Max altitude",
    days: (n) => `${n} ${n === 1 ? "day" : "days"}`,
    day: (i) => `Day ${i}`,
    allDays: "All days",
    profile: "Elevation profile",
    impressions: "Impressions",
    noImpressions: "No write-up yet.",
    videos: "Videos",
    photos: "Photos",
    links: "Links",
    komoot: "Track on Komoot",
    youtube: "Watch on YouTube",
    play: "Play video",
    noTrack: "No GPS track was recorded for this hike.",
    trackError: "Couldn't load the track. Please reload the page later.",
    km: "km",
    m: "m",
    h: "h",
    min: "min",
    layers: { topo: "Topo", sat: "Satellite", plain: "Plain" },
    terrain3d: "3D terrain",
    difficulty: { easy: "Easy", medium: "Moderate", hard: "Hard", expert: "Expert" },
    altAt: (alt, km) => [`${alt} m`, `${km} km`],
  },
};

const MONTHS_BE = ["студзеня", "лютага", "сакавіка", "красавіка", "траўня", "чэрвеня", "ліпеня", "жніўня",
  "верасня", "кастрычніка", "лістапада", "снежня"];
const MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
  "October", "November", "December"];

function plural(n, one, few, many) {
  const n10 = n % 10, n100 = n % 100;
  if (n10 === 1 && n100 !== 11) return one;
  if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return few;
  return many;
}

export let lang = "be";
export function setLang(l) { lang = l === "en" ? "en" : "be"; }
export function t(key, ...args) {
  const v = STRINGS[lang][key];
  return typeof v === "function" ? v(...args) : v;
}

/** Тэкст з даных: {be, en} → патрэбная мова з запасным варыянтам. */
export function pick(pair) {
  if (!pair) return "";
  return pair[lang] || pair.be || pair.en || "";
}

export function num(n, digits = 0) {
  return new Intl.NumberFormat(lang === "be" ? ["be", "ru"] : "en", {
    maximumFractionDigits: digits, minimumFractionDigits: 0,
  }).format(n);
}

function parseDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return { y, m: m - 1, d };
}

export function dateRange(startIso, endIso) {
  const s = parseDate(startIso), e = parseDate(endIso || startIso);
  const months = lang === "be" ? MONTHS_BE : MONTHS_EN;
  const same = s.y === e.y && s.m === e.m && s.d === e.d;
  if (same) return `${s.d} ${months[s.m]} ${s.y}`;
  if (s.y === e.y && s.m === e.m) return `${s.d}–${e.d} ${months[s.m]} ${s.y}`;
  if (s.y === e.y) return `${s.d} ${months[s.m]} – ${e.d} ${months[e.m]} ${s.y}`;
  return `${s.d} ${months[s.m]} ${s.y} – ${e.d} ${months[e.m]} ${e.y}`;
}

export function shortDate(iso) {
  const d = parseDate(iso);
  const months = lang === "be" ? MONTHS_BE : MONTHS_EN;
  return `${d.d} ${months[d.m]}`;
}

export function duration(sec) {
  const total = Math.round(sec / 60);
  const h = Math.floor(total / 60), m = total % 60;
  if (!h) return `${m} ${t("min")}`;
  return `${h} ${t("h")} ${String(m).padStart(2, "0")} ${t("min")}`;
}

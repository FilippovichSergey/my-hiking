// Пераклады інтэрфейсу і фарматаванне лічбаў/дат.

const STRINGS = {
  be: {
    siteTitle: "Мае паходы",
    siteDescription: "Карта маіх хайкінгаў: трэкі, фота, відэа і ўражанні.",
    version: (v) => `версія ${v}`,
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
    practical: "Практычная інфармацыя",
    srcTrack: "Разлік па трэку",
    srcAuthor: "Мае назіранні",
    checked: (d) => `Звесткі правераны ${d}`,
    routeType: "Тып маршруту",
    routeTypes: {
      loop: "Колавы", out_and_back: "Туды і назад", point_to_point: "З пункта А ў пункт Б",
      mixed: "Змешаны: кола і ўчастак туды і назад",
    },
    routeShort: { loop: "Кола", out_and_back: "Туды і назад", point_to_point: "А → Б", mixed: "Змешаны" },
    allRouteTypes: "Любы тып маршруту",
    byAuthor: "удакладнена аўтарам",
    start: "Старт",
    finish: "Фініш",
    routeToStart: "Маршрут да старту",
    daylight: "Светлавы дзень",
    daylightLeft: (d) => `пасля фінішу заставалася ${d} святла`,
    daylightOver: (d) => `фініш праз ${d} пасля заходу сонца`,
    daylightNight: "фініш да ўзыходу сонца",
    pace: "Тэмп",
    speed: (v) => `${v} км/г у руху`,
    stops: (d) => `прыпынкі ${d}`,
    relief: "Рэльеф",
    gainPerKm: (m) => `${m} м набору на кіламетр`,
    steepUp: (g, km, day) => `найкруцейшы пад'ём ${g} % на 500 м (${day ? `${day}, ` : ""}ад ${km} км)`,
    steepDown: (g, km, day) => `найкруцейшы спуск ${g} % на 500 м (${day ? `${day}, ` : ""}ад ${km} км)`,
    gettingThere: "Як дабрацца",
    waysCount: (n) => `${n} ${plural(n, "варыянт", "варыянты", "варыянтаў")}`,
    transport: { car: "На машыне", taxi: "Таксі", marshrutka: "Маршрутка", bus: "Аўтобус", train: "Цягнік", other: "Іншы транспарт" },
    priceFor: { car: "за машыну", person: "з чалавека" },
    trip: { one_way: "у адзін бок", round_trip: "туды і назад" },
    priceDate: (d) => `цана на ${d}`,
    boarding: "Пасадка",
    road: "Дарога",
    water: "Вада",
    litre: "л",
    waterYes: "На маршруце ёсць вада.",
    waterYesHike: "У паходзе ў цэлым вада ёсць; пра гэты дзень асобных звестак няма.",
    waterNo: "Вады на маршруце няма.",
    waterUnknown: "Няма звестак пра ваду на маршруце.",
    drunk: (l) => `Выпіта ${l}`,
    perHour: (v) => `${v} л за гадзіну на маршруце`,
    per10km: (v) => `${v} л на 10 км`,
    taken: "узята",
    refilled: "папоўнена",
    left: "засталося",
    sourcesCount: (n) => `${n} ${plural(n, "крыніца", "крыніцы", "крыніц")}`,
    sourceTypes: { spring: "Крыніца", tap: "Кран", well: "Калодзеж", stream: "Ручай", other: "Вада" },
    hadWater: { true: "вада была", false: "вады не было", null: "не вядома, ці была вада" },
    seasonal: "перасыхае ў сухі сезон",
    treatYes: "ваду трэба апрацоўваць",
    treatNo: "можна піць без апрацоўкі",
    places: "Асаблівыя месцы",
    placeTypes: {
      firepit: "Месца для вогнішча", shelter: "Альтанка або навес", swing: "Арэлі", camp: "Месца для начоўкі",
      signpost: "Паказальнік дарог", viewpoint: "Аглядная пляцоўка", table: "Стол і лаўкі", toilet: "Туалет",
      parking: "Паркоўка", obstacle: "Перашкода", other: "Месца",
    },
    kmFromStart: (km) => `${km} км ад старту`,
    offTrack: (m) => `збоку ад трэку, ${m} м`,
    seen: (d) => `бачана ${d}`,
    openPhoto: "Адкрыць фота",
    trail: "Сцежка і ўмовы",
    stay: "Начоўка і пасля паходу",
    weather: "Надвор'е",
    noteLabels: {
      parking: "Паркоўка", surface: "Пакрыццё", marking: "Маркіроўка і навігацыя", obstacles: "Перашкоды",
      signal: "Мабільная сувязь", dangers: "Небяспекі", gear: "Рыштунак", season: "Сезон", overnight: "Начоўка",
      after: "Пасля паходу", suitable: "Каму падыходзіць",
    },
    reset: "Скінуць фільтры",
    showOnMap: "Паказаць знойдзеныя на карце",
    sort: "Сартаванне",
    sorts: {
      new: "Спачатку новыя", old: "Спачатку старыя", short: "Найкарацейшыя", long: "Найдаўжэйшыя",
      up: "Найбольшы набор вышыні", low: "Найменшы набор вышыні",
    },
    allDistances: "Любая адлегласць",
    distances: { short: "Да 10 км", mid: "10–20 км", long: "Больш за 20 км" },
    allDayCounts: "Любая працягласць",
    dayCounts: { one: "1 дзень", multi: "2 дні і больш" },
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
    downloadGpx: "Спампаваць GPX",
    share: "Падзяліцца",
    linkCopied: "Спасылка скапіявана",
    copyLink: "Скапіюйце спасылку на паход:",
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
    version: (v) => `version ${v}`,
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
    practical: "Practical information",
    srcTrack: "Calculated from the track",
    srcAuthor: "My own observations",
    checked: (d) => `Information checked on ${d}`,
    routeType: "Route type",
    routeTypes: {
      loop: "Loop", out_and_back: "Out and back", point_to_point: "Point to point",
      mixed: "Mixed: a loop with an out-and-back section",
    },
    routeShort: { loop: "Loop", out_and_back: "Out and back", point_to_point: "A → B", mixed: "Mixed" },
    allRouteTypes: "Any route type",
    byAuthor: "corrected by the author",
    start: "Start",
    finish: "Finish",
    routeToStart: "Directions to the start",
    daylight: "Daylight",
    daylightLeft: (d) => `${d} of daylight left after the finish`,
    daylightOver: (d) => `finished ${d} after sunset`,
    daylightNight: "finished before sunrise",
    pace: "Pace",
    speed: (v) => `${v} km/h moving`,
    stops: (d) => `stops ${d}`,
    relief: "Terrain",
    gainPerKm: (m) => `${m} m of ascent per kilometre`,
    steepUp: (g, km, day) => `steepest climb ${g}% over 500 m (${day ? `${day}, ` : ""}from km ${km})`,
    steepDown: (g, km, day) => `steepest descent ${g}% over 500 m (${day ? `${day}, ` : ""}from km ${km})`,
    gettingThere: "Getting there",
    waysCount: (n) => `${n} ${n === 1 ? "option" : "options"}`,
    transport: { car: "By car", taxi: "Taxi", marshrutka: "Marshrutka", bus: "Bus", train: "Train", other: "Other transport" },
    priceFor: { car: "per car", person: "per person" },
    trip: { one_way: "one way", round_trip: "round trip" },
    priceDate: (d) => `price as of ${d}`,
    boarding: "Boarding",
    road: "Road",
    water: "Water",
    litre: "l",
    waterYes: "There is water on the route.",
    waterYesHike: "There is water on the hike overall; no separate information for this day.",
    waterNo: "There is no water on the route.",
    waterUnknown: "No information about water on the route.",
    drunk: (l) => `Drank ${l}`,
    perHour: (v) => `${v} l per hour on the route`,
    per10km: (v) => `${v} l per 10 km`,
    taken: "carried",
    refilled: "refilled",
    left: "left over",
    sourcesCount: (n) => `${n} ${n === 1 ? "source" : "sources"}`,
    sourceTypes: { spring: "Spring", tap: "Tap", well: "Well", stream: "Stream", other: "Water" },
    hadWater: { true: "had water", false: "was dry", null: "unknown whether it had water" },
    seasonal: "dries up in the dry season",
    treatYes: "water needs treatment",
    treatNo: "drinkable without treatment",
    places: "Points of interest",
    placeTypes: {
      firepit: "Fire pit", shelter: "Gazebo or shelter", swing: "Swing", camp: "Place to camp",
      signpost: "Signpost", viewpoint: "Viewpoint", table: "Table and benches", toilet: "Toilet",
      parking: "Parking", obstacle: "Obstacle", other: "Place",
    },
    kmFromStart: (km) => `${km} km from the start`,
    offTrack: (m) => `off the track, ${m} m`,
    seen: (d) => `seen on ${d}`,
    openPhoto: "Open photo",
    trail: "Trail and conditions",
    stay: "Overnight and after the hike",
    weather: "Weather",
    noteLabels: {
      parking: "Parking", surface: "Surface", marking: "Waymarking and navigation", obstacles: "Obstacles",
      signal: "Mobile signal", dangers: "Hazards", gear: "Gear", season: "Season", overnight: "Overnight",
      after: "After the hike", suitable: "Suitable for",
    },
    reset: "Reset filters",
    showOnMap: "Show results on the map",
    sort: "Sort",
    sorts: {
      new: "Newest first", old: "Oldest first", short: "Shortest", long: "Longest",
      up: "Most elevation gain", low: "Least elevation gain",
    },
    allDistances: "Any distance",
    distances: { short: "Under 10 km", mid: "10–20 km", long: "Over 20 km" },
    allDayCounts: "Any duration",
    dayCounts: { one: "1 day", multi: "2 days or more" },
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
    downloadGpx: "Download GPX",
    share: "Share",
    linkCopied: "Link copied",
    copyLink: "Copy the link to this hike:",
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

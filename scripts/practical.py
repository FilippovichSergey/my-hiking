"""Практычная частка паходу: разлікі з трэку і звесткі аўтара з блока `practical:` у файле паходу.

Усё лічыцца лакальна, без сеткі: з тураў у cache/komoot, з маніфеста фота і з YAML.
Аўтаматычныя звесткі («разлік па трэку») і звесткі аўтара («мае назіранні») захоўваюцца асобна:
`facts` — разлік, `practical` — тое, што напісаў аўтар, разам з вылічаным месцам на трэку.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from fnmatch import fnmatchcase
from pathlib import Path

from common import CACHE, has_track, iso_date, lat_lng, photo_name, read_json, text_pair, tour_local_datetime
from geo import EARTH_R, haversine

ROUTE_TYPES = ("loop", "out_and_back", "point_to_point", "mixed")
SOURCE_TYPES = ("spring", "tap", "well", "stream", "other")
PLACE_TYPES = ("firepit", "shelter", "swing", "camp", "signpost", "viewpoint", "table", "toilet", "parking",
               "obstacle", "other")
TRANSPORT_TYPES = ("car", "taxi", "marshrutka", "bus", "train", "other")
NOTE_KEYS = ("parking", "surface", "marking", "obstacles", "signal", "dangers", "gear", "season", "overnight",
             "after", "suitable")

# Тып маршруту (парогі падабраныя па рэальных трэках: у замкнёных маршрутаў разрыў старт–фініш да 323 м,
# у астатніх — ад 2 км; доля шляху, пройдзенага двойчы, — ад 0,24 да 0,97).
CLOSED_GAP_M = 500        # старт і фініш «у адным месцы»
CLOSED_GAP_SHARE = 0.25   # …але не больш за чвэрць даўжыні кароткага маршруту
LOOP_MAX_OVERLAP = 0.35   # кола: двойчы пройдзена не больш за траціну (агульны падыход)
BACK_MIN_OVERLAP = 0.70   # туды і назад: двойчы пройдзена амаль усё
NEAR_M, APART_M, STEP_M = 30.0, 500.0, 30.0
DAYS_JOINED_M = 1000      # туры і дні ідуць адзін за адным, калі наступны пачынаецца там, дзе скончыўся папярэдні
STEEP_WINDOW_KM = 0.5
PHOTO_TOLERANCE_S = 1800  # фота, зробленае да паўгадзіны да старту або пасля фінішу, яшчэ адносіцца да трэку


# --- Трэк з часам -----------------------------------------------------------------

def raw_days(tour_ids: list, offset: float) -> list[dict]:
    """Дні паходу з поўнымі пунктамі трэку; туры аднаго дня — адзін дзень (як у build_tracks).

    Дзень: {date, tz, segs}; пункт: (lat, lng, alt, ts, km) — ts: момант часу (POSIX) або None,
    km: адлегласць ад пачатку дня па трэку (тая ж, што на профілі вышыні).
    """
    tours = []
    for tid in tour_ids:
        tour = read_json(CACHE / "komoot" / "tours" / f"{tid}.json")
        if tour and has_track(tour.get("coords")):
            tours.append((tour_local_datetime(tour["date"], offset), tour))
    days: dict[str, dict] = {}
    for local, tour in sorted(tours, key=lambda x: x[0]):
        day = days.setdefault(local.date().isoformat(), {"date": local.date().isoformat(), "tz": local.tzinfo, "segs": []})
        km = day["segs"][-1][-1][4] if day["segs"] else 0.0
        seg, prev = [], None
        for c in tour["coords"]:
            if not c or c[0] is None or c[1] is None:
                continue
            if prev:
                km += haversine(prev[0], prev[1], c[0], c[1]) / 1000
            prev = c
            t = c[3] if len(c) > 3 else None
            ts = local.timestamp() + t / 1000 if isinstance(t, (int, float)) else None
            seg.append((c[0], c[1], c[2] if len(c) > 2 else None, ts, km))
        day["segs"].append(seg)
    return [days[d] for d in sorted(days)]


def _points(day: dict) -> list[tuple]:
    return [p for seg in day["segs"] for p in seg]


def _clock(ts: float | None, tz) -> str | None:
    return datetime.fromtimestamp(ts, tz).strftime("%H:%M") if ts is not None else None


# --- Тып маршруту -----------------------------------------------------------------

def _resample(points: list[tuple]) -> tuple[list[tuple], float]:
    """Выбарка роўна праз STEP_M метраў шляху і канцавы пункт: (lat, lng, адлегласць ад пачатку ў метрах);
    агульная даўжыня. На доўгім адрэзку пункты ставяцца па прамой, рэшта кроку пераходзіць на наступны адрэзак:
    вынік не залежыць ад таго, наколькі часта запісаны трэк.
    """
    out, total, need = [(points[0][0], points[0][1], 0.0)], 0.0, STEP_M  # need — метраў да наступнага пункта
    for a, b in zip(points, points[1:]):
        d = haversine(a[0], a[1], b[0], b[1])
        done = 0.0
        while d - done >= need:
            done += need
            part = done / d
            out.append((a[0] + (b[0] - a[0]) * part, a[1] + (b[1] - a[1]) * part, total + done))
            need = STEP_M
        need -= d - done
        total += d
    if total - out[-1][2] > 1e-6:
        out.append((points[-1][0], points[-1][1], total))
    return out, total


def overlap_share(points: list[tuple]) -> float:
    """Доля шляху, пройдзенага двойчы: побач (да NEAR_M) ёсць іншая частка трэку, далёкая па ходзе (ад APART_M)."""
    samples, _ = _resample(points)
    scale = 111320 * math.cos(math.radians(samples[0][0]))

    def cell(p):
        return int(p[0] * 111320 / NEAR_M), int(p[1] * scale / NEAR_M)

    grid: dict[tuple, list[int]] = {}
    for i, p in enumerate(samples):
        grid.setdefault(cell(p), []).append(i)
    twice = 0
    for p in samples:
        cx, cy = cell(p)
        twice += any(abs(samples[j][2] - p[2]) > APART_M and haversine(p[0], p[1], samples[j][0], samples[j][1]) <= NEAR_M
                     for dx in (-1, 0, 1) for dy in (-1, 0, 1) for j in grid.get((cx + dx, cy + dy), ()))
    return twice / len(samples)


def route_type(points: list[tuple]) -> str | None:
    """loop | out_and_back | point_to_point | mixed; None, калі трэку не хапае для вызначэння."""
    if len(points) < 2:
        return None
    _, length = _resample(points)
    if length < 200:
        return None
    gap = haversine(points[0][0], points[0][1], points[-1][0], points[-1][1])
    if gap > min(CLOSED_GAP_M, CLOSED_GAP_SHARE * length):
        return "point_to_point"
    share = overlap_share(points)
    if share <= LOOP_MAX_OVERLAP:
        return "loop"
    return "out_and_back" if share >= BACK_MIN_OVERLAP else "mixed"


def parts_route_type(parts: list[list[tuple]]) -> str | None:
    """Тып маршруту з некалькіх частак па парадку — тураў дня або ўсіх тураў паходу.

    Частка, якая пачынаецца там, дзе скончылася папярэдняя (да DAYS_JOINED_M: перапынак у запісе, начлег),
    працягвае той жа маршрут. Пасля пераезду пачынаецца асобная вылазка: пераезд — не частка пешага шляху.
    Некалькі вылазак маюць агульны тып, калі ён у іх аднолькавы, інакш — «змешаны».
    """
    walks: list[list[tuple]] = []
    for part in parts:
        if not part:
            continue
        if walks and haversine(walks[-1][-1][0], walks[-1][-1][1], part[0][0], part[0][1]) <= DAYS_JOINED_M:
            walks[-1] = walks[-1] + list(part)
        else:
            walks.append(list(part))
    known = {t for t in map(route_type, walks) if t}
    return known.pop() if len(known) == 1 else ("mixed" if known else None)


def hike_route_type(days: list[dict], day_types: list) -> str | None:
    """Тып усяго паходу. Дні з начлегам у адным месцы — адзін маршрут; асобныя вылазкі — агульны тып."""
    if len(days) == 1:
        return day_types[0]
    return parts_route_type([seg for day in days for seg in day["segs"]])


# --- Сонца ------------------------------------------------------------------------

def sun_moments(day: date, lat: float, lng: float, tz) -> tuple[datetime, datetime] | None:
    """(узыход, заход) як моманты мясцовага часу паводле ўраўнення ўзыходу сонца; None для палярнага дня або ночы.

    Астранамічны разлік без уліку рэльефу: у гарах сонца хаваецца за хрыбет раней.
    """
    n = day.toordinal() - date(2000, 1, 1).toordinal() + 0.0008  # дні ад поўдня 1 студзеня 2000 г.
    j = n - lng / 360
    m = math.radians((357.5291 + 0.98560028 * j) % 360)
    center = 1.9148 * math.sin(m) + 0.02 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
    lam = math.radians((math.degrees(m) + center + 180 + 102.9372) % 360)
    transit = 2451545.0 + j + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    decl = math.asin(math.sin(lam) * math.sin(math.radians(23.4397)))
    phi = math.radians(lat)
    cos_w = (math.sin(math.radians(-0.833)) - math.sin(phi) * math.sin(decl)) / (math.cos(phi) * math.cos(decl))
    if not -1 <= cos_w <= 1:
        return None
    w = math.degrees(math.acos(cos_w)) / 360

    def moment(julian: float) -> datetime:
        return (datetime(2000, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=julian - 2451545.0)).astimezone(tz)

    return moment(transit - w), moment(transit + w)


def _hhmm(moment: datetime) -> str:
    return (moment + timedelta(seconds=30)).strftime("%H:%M")  # да бліжэйшай хвіліны


def sun_times(day: date, lat: float, lng: float, tz) -> tuple[str, str] | None:
    """(узыход, заход) мясцовым часам «ГГ:ХХ»."""
    moments = sun_moments(day, lat, lng, tz)
    return (_hhmm(moments[0]), _hhmm(moments[1])) if moments else None


def _minutes(clock: str) -> int:
    return int(clock[:2]) * 60 + int(clock[3:])


# --- Разлікі па трэку ---------------------------------------------------------------

def _steepest(segs: list[list[tuple]]) -> tuple[dict | None, dict | None]:
    """Найкруцейшыя пад'ём і спуск на адрэзку STEEP_WINDOW_KM: ({grade %, km пачатку}, {…}).

    Акно не пераходзіць з тура ў тур: паміж турамі дня можа быць пераезд, і розніца вышынь там — не ўхіл сцежкі.
    """
    up = down = None
    for seg in segs:
        pts = [(p[4], p[2]) for p in seg if p[2] is not None]
        if len(pts) < 2 or pts[-1][0] - pts[0][0] < STEEP_WINDOW_KM:
            continue  # тур карацейшы за акно
        kms = [km for km, _ in pts]

        def alt_at(km: float) -> float:
            """Вышыня на кіламетры: па прамой паміж суседнімі пунктамі тура."""
            i = min(max(bisect.bisect_right(kms, km), 1), len(pts) - 1)
            (k0, a0), (k1, a1) = pts[i - 1], pts[i]
            return a0 + (a1 - a0) * (km - k0) / (k1 - k0) if k1 > k0 else a1

        # Профіль паміж пунктамі — прамыя, таму найбольшы перапад дае акно, якое пачынаецца або сканчаецца
        # ў пункце. Даўжыня акна заўсёды роўна 500 м: вынік не залежыць ад шчыльнасці запісу.
        first, last = kms[0], kms[-1] - STEEP_WINDOW_KM
        starts = {km for km in kms if km <= last} | {km - STEEP_WINDOW_KM for km in kms if first <= km - STEEP_WINDOW_KM <= last}
        for km in sorted(starts):
            grade = (alt_at(km + STEEP_WINDOW_KM) - alt_at(km)) / (STEEP_WINDOW_KM * 1000) * 100
            if grade > 0 and (up is None or grade > up["grade"] + 1e-9):
                up = {"grade": grade, "km": km}
            if grade < 0 and (down is None or -grade > down["grade"] + 1e-9):
                down = {"grade": -grade, "km": km}
    fmt = lambda s: {"grade": round(s["grade"]), "km": round(s["km"], 1)} if s and round(s["grade"]) >= 1 else None  # noqa: E731
    return fmt(up), fmt(down)


def _end(point: tuple, tz) -> dict:
    return {"at": [round(point[1], 5), round(point[0], 5)], "alt": round(point[2]) if point[2] is not None else None,
            "time": _clock(point[3], tz)}


def _pace(stats: dict) -> dict:
    """Тэмп і рэльеф з лічбаў дня: хуткасць у руху (км/г), час на прыпынкі (с), набор на кіламетр (м)."""
    out = {}
    if stats.get("moving") and stats.get("distance"):
        out["speed"] = round(stats["distance"] / (stats["moving"] / 3600), 1)
    if stats.get("duration") and stats.get("moving") and stats["duration"] - stats["moving"] >= 60:
        out["stops"] = round(stats["duration"] - stats["moving"])  # калі Komoot не вылучыў прыпынкі, поля няма
    if stats.get("distance") and stats.get("up") is not None:
        out["gain"] = round(stats["up"] / stats["distance"])
    return out


def day_facts(day: dict, stats: dict) -> dict:
    """Разлік па трэку для аднаго дня: тып, старт і фініш, светлавы дзень, тэмп, рэльеф."""
    pts = _points(day)
    facts = {"type": parts_route_type(day["segs"]), "start": _end(pts[0], day["tz"]), "finish": _end(pts[-1], day["tz"])}
    sun = sun_moments(date.fromisoformat(day["date"]), pts[0][0], pts[0][1], day["tz"])
    if sun:
        facts["sun"] = {"rise": _hhmm(sun[0]), "set": _hhmm(sun[1])}
        if pts[-1][3] is not None and pts[-1][3] < sun[0].timestamp():
            facts["sun"]["night"] = True  # начны выхад: фініш яшчэ да ўзыходу сонца
        elif pts[-1][3] is not None:
            # Хвілін святла пасля фінішу; мінус — фініш пасля заходу. Параўноўваюцца моманты часу з датай,
            # таму фініш пасля поўначы — гэта «пасля заходу», а не «амаль цэлы дзень святла».
            facts["sun"]["left"] = round((sun[1].timestamp() - pts[-1][3]) / 60)
    facts.update(_pace(stats))
    up, down = _steepest(day["segs"])
    if up:
        facts["steepUp"] = up
    if down:
        facts["steepDown"] = down
    return facts


def hike_facts(days: list[dict], per_day: list[dict], totals: dict) -> dict:
    """Разлік для ўсяго паходу; для аднадзённага — тое ж, што для дня."""
    if len(days) == 1:
        return dict(per_day[0])
    facts = {"type": hike_route_type(days, [f["type"] for f in per_day]),
             "start": per_day[0]["start"], "finish": per_day[-1]["finish"]}
    facts.update(_pace(totals))
    for key in ("steepUp", "steepDown"):
        best = max(((f[key], i) for i, f in enumerate(per_day) if f.get(key)), key=lambda x: x[0]["grade"], default=None)
        if best:
            facts[key] = {**best[0], "day": best[1]}
    return facts


# --- Надвор'е: пункты запыту і паказчыкі з гадзінных шэрагаў Open-Meteo ----------------

WEATHER_VARS = {  # назва паказчыка ў Open-Meteo → поле ў індэксе сайта
    "temperature_2m": "temp", "apparent_temperature": "feels", "precipitation": "precip", "snowfall": "snow",
    "wind_speed_10m": "wind", "wind_gusts_10m": "gusts", "cloud_cover": "cloud", "weather_code": "code",
}
WEATHER_EDGE_S = 1800  # гадзіна ўваходзіць у «час паходу», калі яна не далей за паўгадзіны ад старту або фінішу


def weather_points(day: dict) -> list[dict]:
    """Пункты дня, для якіх патрэбна надвор'е: старт, найвышэйшы пункт і фініш — з момантам, калі аўтар там быў.

    Пусты спіс, калі ў трэку няма часу (імпартаваны GPX): невядома, для якіх гадзін пытацца.
    """
    pts = [p for p in _points(day) if p[3] is not None]
    if len(pts) < 2:
        return []
    with_alt = [p for p in pts if p[2] is not None]
    top = max(with_alt, key=lambda p: p[2]) if with_alt else None
    chosen = [("start", pts[0])]
    if top is not None and top is not pts[0] and top is not pts[-1]:
        chosen.append(("top", top))
    chosen.append(("finish", pts[-1]))
    return [{"role": role, "lat": round(p[0], 3), "lng": round(p[1], 3),
             "alt": round(p[2]) if p[2] is not None else None, "ts": round(p[3])} for role, p in chosen]


WEATHER_FORMAT = 3  # змяняецца разам з правіламі падліку ў summarize_weather: старыя падлікі пытаюцца зноў


def weather_key(points: list[dict]) -> str:
    """Подпіс запыту: калі трэк змяніўся, раней спампаванае надвор'е да яго ўжо не адносіцца."""
    return hashlib.sha1(json.dumps([WEATHER_FORMAT, points], sort_keys=True).encode()).hexdigest()[:12]


def weather_hours(points: list[dict]) -> range:
    """Гадзіны сэрвісу (секунды UTC), патрэбныя для дня: ад паўгадзіны да старту да паўгадзіны пасля фінішу.

    Па іх `hike fetch` выбірае даты запыту, а summarize_weather правярае паўнату адказу: фініш пасля 23:30 UTC
    патрабуе гадзіну 00:00 наступнай даты.
    """
    lo, hi = points[0]["ts"] - WEATHER_EDGE_S, points[-1]["ts"] + WEATHER_EDGE_S
    return range(math.ceil(lo / 3600) * 3600, math.floor(hi) + 1, 3600)


def summarize_weather(points: list[dict], series: list[dict]) -> dict:
    """Паказчыкі дня з гадзінных шэрагаў (па адным на пункт; `time` — секунды UTC).

    points — значэнні бліжэйшай гадзіны да моманту, калі аўтар быў у пункце; span — межы за ўвесь час паходу
    па ўсіх пунктах (ападкі і снег — найбольшая сума сярод пунктаў). Пустое значэнне сэрвісу застаецца пустым,
    а не нулём; тады complete = False і `hike fetch` паспрабуе яшчэ раз. Гэта датычыць і кожнай гадзіны паходу:
    сума ападкаў з прапушчанай гадзінай не публікуецца — інакш сухія вядомыя гадзіны давалі б «без ападкаў».
    Прапушчанай лічыцца і гадзіна, якой у адказе няма зусім; паказчык пункта бярэцца толькі з яго гадзіны.
    """
    def finite(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None

    lo, hi = points[0]["ts"] - WEATHER_EDGE_S, points[-1]["ts"] + WEATHER_EDGE_S
    grid = set(weather_hours(points))
    readings, complete = [], True
    seen = {field: [] for field in WEATHER_VARS.values()}
    totals = {"precip": [], "snow": []}
    holes = set()  # паказчыкі, у якіх за час паходу ёсць прапушчаная гадзіна
    for point, hourly in zip(points, series):
        times = hourly["time"]
        if grid - set(times):
            holes.update(WEATHER_VARS.values())  # у адказе няма цэлай гадзіны паходу
        nearest = min(range(len(times)), key=lambda i: abs(times[i] - point["ts"]))
        own = abs(times[nearest] - point["ts"]) <= WEATHER_EDGE_S  # інакш гэта паказчык іншай гадзіны
        reading = {field: finite(hourly[var][nearest]) if own else None for var, field in WEATHER_VARS.items()}
        complete = complete and all(v is not None for v in reading.values())
        readings.append({"role": point["role"], "alt": point["alt"], "ts": point["ts"], **reading})
        inside = [i for i, t in enumerate(times) if lo <= t <= hi]
        for var, field in WEATHER_VARS.items():
            hours = [finite(hourly[var][i]) for i in inside]
            values = [v for v in hours if v is not None]
            if len(values) < len(hours):
                holes.add(field)
            seen[field] += values
            if field in totals and values:
                totals[field].append(sum(values))
    span = {}
    if seen["temp"]:
        span["tempMin"], span["tempMax"] = min(seen["temp"]), max(seen["temp"])
    for field in ("wind", "gusts", "code"):
        if seen[field]:
            span[field] = max(seen[field])
    for field, sums in totals.items():
        if sums and field not in holes:
            span[field] = round(max(sums), 1)
    return {"points": readings, "span": span, "complete": complete and not holes}


def public_weather(cached: dict, tz) -> dict:
    """Надвор'е дня для індэкса сайта: мясцовы час замест моманту, без службовых палёў кэша."""
    points = [{**{k: v for k, v in p.items() if k != "ts"}, "time": _clock(p["ts"], tz)} for p in cached["points"]]
    return {"points": points, "span": cached.get("span") or {}, "fetched": cached.get("fetched")}


# --- Падказкі OpenStreetMap і дарога на машыне (спампоўвае `hike fetch`) ---------------

OSM_WATER_M, OSM_PLACE_M = 250, 150  # наколькі далёка ад трэку паказваць ваду і іншыя месцы
OSM_SAME_M, OSM_SAME_KM = 60, 0.1    # падказка побач з запісам аўтара таго ж роду (або на тым жа кіламетры) — тое самае
OSM_MAX = 40                         # не больш падказак на паход
OSM_THIN_KM, OSM_THIN_M = 0.05, 60   # грубы адбор: трэк з пунктамі праз ~50 м адыходзіць ад сапраўднага не больш чым на 50 м
DRIVE_BACK_M = 2000                  # фініш далей за гэта ад старту — лічыцца і дарога ад фінішу


def osm_bbox(days: list[dict]) -> list[float] | None:
    """[поўдзень, захад, поўнач, усход] трэку з запасам — вобласць пошуку ў OpenStreetMap.

    Запас — 0,004° (па шыраце каля 440 м), але не менш, чым трэба, каб у вобласць трапіла ўсё бліжэй за OSM_WATER_M
    ад трэку: градус даўгаты тым карацейшы, чым далей ад экватара (на 70° — каля 38 км). Межы акругляюцца да
    0,001°, таму да запасу дададзена і гэтая палова тысячнай. Для шырот Грузіі вобласць — ранейшая.
    """
    pts = [p for day in days for p in _points(day)]
    if not pts:
        return None
    lat_pad = 0.004
    widest = max(abs(p[0]) for p in pts)
    lng_pad = max(lat_pad, OSM_WATER_M / (math.pi / 180 * EARTH_R * max(math.cos(math.radians(widest)), 0.01)) + 0.0005)
    return [round(min(p[0] for p in pts) - lat_pad, 3), round(min(p[1] for p in pts) - lng_pad, 3),
            round(max(p[0] for p in pts) + lat_pad, 3), round(max(p[1] for p in pts) + lng_pad, 3)]


OSM_FORMAT = 2  # змяняецца разам з правіламі адбору ў osm_item: аб'екты адбіраюцца пры спампоўцы


def osm_key(bbox: list[float]) -> str:
    return hashlib.sha1(json.dumps([OSM_FORMAT, bbox, OSM_QUERY]).encode()).hexdigest()[:12]


# Што шукаць: крыніцы вады і месцы, якія аўтар пазначае ў `practical:` (альтанкі, вогнішчы, начоўкі…).
OSM_QUERY = (
    'node["amenity"~"^(drinking_water|water_point)$"]', 'node["natural"="spring"]', 'node["man_made"="water_well"]',
    'nwr["amenity"~"^(shelter|toilets)$"]', 'nwr["tourism"~"^(picnic_site|camp_site|wilderness_hut|alpine_hut|viewpoint)$"]',
    'node["leisure"~"^(firepit|picnic_table)$"]', 'node["information"="guidepost"]',
)


def overpass_query(bboxes: list[list[float]]) -> str:
    """Адзін запыт Overpass на некалькі абласцей (публічны сервер абмяжоўвае колькасць запытаў, а не іх памер)."""
    parts = "".join(f"{sel}({','.join(str(v) for v in box)});" for box in bboxes for sel in OSM_QUERY)
    return f"[out:json][timeout:120];({parts});out center tags;"


def osm_inside(item: dict, bbox: list[float]) -> bool:
    return bbox[0] <= item["lat"] <= bbox[2] and bbox[1] <= item["lng"] <= bbox[3]


def osm_item(element: dict) -> dict | None:
    """Аб'ект OpenStreetMap → падказка {kind, type, name, lat, lng, osm}; None для таго, што не паказваецца."""
    tags = element.get("tags") or {}
    lat = element.get("lat", (element.get("center") or {}).get("lat"))
    lng = element.get("lon", (element.get("center") or {}).get("lon"))
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return None
    if tags.get("access") in ("private", "no", "customers") or tags.get("shelter_type") == "public_transport":
        return None  # прыватнае або прыпынак транспарту
    kinds = {
        ("amenity", "drinking_water"): ("water", "tap"), ("amenity", "water_point"): ("water", "tap"),
        ("natural", "spring"): ("water", "spring"), ("man_made", "water_well"): ("water", "well"),
        ("amenity", "shelter"): ("place", "shelter"), ("amenity", "toilets"): ("place", "toilet"),
        ("tourism", "picnic_site"): ("place", "table"), ("leisure", "picnic_table"): ("place", "table"),
        ("tourism", "camp_site"): ("place", "camp"), ("tourism", "wilderness_hut"): ("place", "camp"),
        ("tourism", "alpine_hut"): ("place", "camp"), ("tourism", "viewpoint"): ("place", "viewpoint"),
        ("leisure", "firepit"): ("place", "firepit"), ("information", "guidepost"): ("place", "signpost"),
    }
    found = next((kinds[(k, tags[k])] for k in ("amenity", "natural", "man_made", "tourism", "leisure", "information")
                  if (k, tags.get(k)) in kinds), None)
    if not found:
        return None
    if found[0] == "water" and tags.get("drinking_water") == "no":
        return None  # вада пазначана як непітная — якім бы ні быў аб'ект
    name = str(tags.get("name") or "").strip()
    names = {"be": str(tags.get("name:be") or name).strip(), "en": str(tags.get("name:en") or name).strip()}
    if str(tags.get("name:ru") or "").strip():
        names["ru"] = str(tags["name:ru"]).strip()
    return {"kind": found[0], "type": found[1], "lat": round(lat, 5), "lng": round(lng, 5), "name": names,
            "osm": f"{element.get('type', 'node')}/{element.get('id')}"}


def _author_places(author: dict | None) -> list[tuple]:
    """Запісы аўтара з месцам: любая яго крыніца вады супадае з вадой з OSM, месца — толькі таго ж тыпу."""
    own = [("water", None, s["pos"]) for s in ((author or {}).get("water") or {}).get("sources") or [] if s.get("pos")]
    return own + [("place", s.get("type"), s["pos"]) for s in (author or {}).get("places") or [] if s.get("pos")]


def _marked_by_author(kind: str, type_: str, pos: dict, own: list[tuple]) -> bool:
    """Аўтар ужо пазначыў гэтае месца сам: запіс таго ж роду побач або на тым жа кіламетры. pos — месца падказкі,
    яго `at` — сам аб'ект OpenStreetMap."""
    return any(mine_kind == kind and mine_type in (None, type_)
               and (haversine(pos["at"][1], pos["at"][0], mine["at"][1], mine["at"][0]) <= OSM_SAME_M
                    or (mine["day"] == pos["day"] and abs(mine["km"] - pos["km"]) <= OSM_SAME_KM))
               for mine_kind, mine_type, mine in own)


def kept_hints(hints: list, author: dict | None) -> list[dict]:
    """Падказкі з папярэдняга індэкса — для паходу, трэк якога застаўся ранейшы (тура няма ў кэшы), — без тых,
    што аўтар з таго часу пазначыў сам. Новых падказак без трэку не знайсці: яны з'явяцца, калі тур вернецца."""
    own = _author_places(author)
    return [hint for hint in hints
            if not (isinstance(hint, dict) and isinstance(hint.get("pos"), dict)
                    and _marked_by_author(hint.get("kind"), hint.get("type"), hint["pos"], own))]


def osm_hints(items: list[dict], days: list[dict], author: dict | None) -> list[dict]:
    """Падказкі каля трэку: з месцам на ім, без тых, што аўтар ужо пазначыў сам. Не праверана аўтарам."""
    if not days or not items:
        return []
    # грубы адбор па разрэджаным трэку (пункт на кожныя ~50 м шляху, канцы тураў застаюцца), потым дакладнае месца
    thin = []
    for day in days:
        segs = []
        for seg in day["segs"]:
            kept: list[tuple] = []
            for i, p in enumerate(seg):
                if not kept or abs(p[4] - kept[-1][4]) >= OSM_THIN_KM or i == len(seg) - 1:
                    kept.append(p)
            segs.append(kept)
        thin.append({"segs": segs})
    own = _author_places(author)
    hints = []
    for item in items:
        limit = OSM_WATER_M if item["kind"] == "water" else OSM_PLACE_M
        near = _project(item["lat"], item["lng"], thin)
        if near is None or near[0] > limit + OSM_THIN_M:
            continue
        pos, _ = locate({"at": [item["lat"], item["lng"]]}, days, {})
        if pos is None or pos.get("off", 0) > limit:
            continue
        if _marked_by_author(item["kind"], item["type"], pos, own):
            continue
        pos.pop("time", None)  # час праходу побач — не час наведвання
        hints.append({"kind": item["kind"], "type": item["type"], "name": item["name"], "osm": item["osm"], "pos": pos})
    hints.sort(key=lambda h: (h["pos"]["day"], h["pos"]["km"], h["osm"]))
    # некалькі аднолькавых аб'ектаў побач (сталы на адной пляцоўцы) — адна падказка; з назвай — важнейшая
    kept: list[dict] = []
    for hint in hints:
        at = hint["pos"]["at"]
        twin = next((k for k in kept if (k["kind"], k["type"]) == (hint["kind"], hint["type"])
                     and haversine(at[1], at[0], k["pos"]["at"][1], k["pos"]["at"][0]) <= OSM_SAME_M), None)
        if twin is None:
            kept.append(hint)
        elif not any(twin["name"].values()) and any(hint["name"].values()):
            kept[kept.index(twin)] = hint
    return kept[:OSM_MAX]


def home_point(home) -> tuple | None:
    """Пункт `home.point` з config.yaml як (шырата, даўгата); None, калі яго няма або ён няправільны."""
    return lat_lng(home.get("point")) if isinstance(home, dict) else None


def drive_key(home: tuple, target: tuple, back: bool = False) -> str:
    """Ключ маршруту на машыне ў кэшы «адкуль>куды»: (шырата, даўгата) дома і пункта трэку; back — ад пункта дадому."""
    a, b = f"{home[0]:.4f},{home[1]:.4f}", f"{target[0]:.3f},{target[1]:.3f}"
    return f"{b}>{a}" if back else f"{a}>{b}"


def drive_targets(days: list[dict]) -> dict:
    """Куды лічыць дарогу: да старту (to), а калі фініш далёка ад яго — яшчэ і ад фінішу (back)."""
    if not days:
        return {}
    first, last = _points(days[0])[0], _points(days[-1])[-1]
    targets = {"to": (first[0], first[1])}
    if haversine(first[0], first[1], last[0], last[1]) > DRIVE_BACK_M:
        targets["back"] = (last[0], last[1])
    return targets


def drive_info(routes: dict, home, days: list[dict]) -> dict | None:
    """Дарога на машыне для індэкса: {from, to: {km, minutes, off?}, back?} з таго, што ёсць у кэшы."""
    point = home_point(home)
    if not point:
        return None
    keys = {name: drive_key(point, target, name == "back") for name, target in drive_targets(days).items()}
    found = {name: routes[key] for name, key in keys.items() if isinstance(routes.get(key), dict)}
    if "to" not in found:
        return None
    return {"from": _text(home.get("name"), "home.name", lambda message: None), **found}


# --- Паходы ў тое самае месца ---------------------------------------------------------

RELATED_NEAR_M = 150   # «па тым жа шляху» — не далей за гэта ад трэку другога паходу
RELATED_STEP_M = 50    # шлях параўноўваецца кавалкамі не даўжэй за гэта
RELATED_SHARE = 0.5    # звязаныя, калі не менш за палову шляху аднаго з двух паходаў ідзе па трэку другога


def related_hikes(hikes: list[dict]) -> dict[str, list[str]]:
    """Іншыя паходы ў тое самае месца для кожнага паходу: {slug: [slug, …]}, найноўшыя першымі.

    Гэта паходы з той жа беларускай назвай (мэта тая ж, нават калі шлях іншы) і паходы, у якіх не менш за палову
    шляху аднаго з двух ідзе па трэку другога: кароткі паход на гару цалкам ляжыць на маршруце доўгага праз яе.
    Параўноўваюцца лініі трэкаў з індэкса сайта (`lines`, пункты [даўгата, шырата]).
    """
    def flat(lines: list, cos0: float) -> list:
        return [[(math.radians(p[0]) * EARTH_R * cos0, math.radians(p[1]) * EARTH_R) for p in line] for line in lines]

    def pieces(lines: list) -> list:
        """Шлях кавалкамі не даўжэй за RELATED_STEP_M: (сярэдзіна кавалка, яго даўжыня ў метрах).

        Кожны кавалак важыць столькі, колькі ён доўгі: шлях, запісаны некалькімі кароткімі турамі, дае тую ж
        долю, што і адзін тур. Паміж лініямі (турамі) шляху няма, адрэзак нулявой даўжыні вагі не мае.
        """
        found = []
        for line in lines:
            for a, b in zip(line, line[1:]):
                length = math.dist(a, b)
                count = math.ceil(length / RELATED_STEP_M)
                found += [((a[0] + (b[0] - a[0]) * (i + 0.5) / count, a[1] + (b[1] - a[1]) * (i + 0.5) / count), length / count)
                          for i in range(count)]
        return found

    def grid(lines: list) -> dict:
        """Адрэзкі ліній па клетках: у клетцы пункта ёсць усе адрэзкі, якія могуць быць да яго бліжэй за RELATED_NEAR_M."""
        cells: dict[tuple, list] = {}
        size = 2 * RELATED_NEAR_M
        for line in lines:
            for a, b in zip(line, line[1:] or line):
                for cx in range(int((min(a[0], b[0]) - RELATED_NEAR_M) // size), int((max(a[0], b[0]) + RELATED_NEAR_M) // size) + 1):
                    for cy in range(int((min(a[1], b[1]) - RELATED_NEAR_M) // size), int((max(a[1], b[1]) + RELATED_NEAR_M) // size) + 1):
                        cells.setdefault((cx, cy), []).append((a, b))
        return cells

    def near(p: tuple, a: tuple, b: tuple) -> bool:
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        part = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2)) if length2 else 0.0
        return (p[0] - a[0] - part * dx) ** 2 + (p[1] - a[1] - part * dy) ** 2 <= RELATED_NEAR_M ** 2

    def share(parts: list, cells: dict) -> float:
        """Доля шляху (па даўжыні), якая ляжыць каля ліній другога паходу."""
        size = 2 * RELATED_NEAR_M
        total = sum(length for _, length in parts)
        hits = sum(length for p, length in parts
                   if any(near(p, a, b) for a, b in cells.get((int(p[0] // size), int(p[1] // size)), ())))
        return hits / total if total else 0.0

    def apart(box: list, other: list) -> bool:
        """Прамавугольнікі трэкаў так далёка, што ніводзін пункт аднаго не бліжэй за RELATED_NEAR_M да другога.
        Запас па даўгаце — для шыраты, дзе градус даўгаты карацейшы за ўсё (бліжэй да полюса)."""
        lat_pad = RELATED_NEAR_M / (math.pi / 180 * EARTH_R) * 1.5  # з запасам на акругленне каардынат
        widest = max(abs(box[1]), abs(box[3]), abs(other[1]), abs(other[3]))
        lng_pad = lat_pad / max(math.cos(math.radians(widest)), 1e-6)
        return (box[2] + lng_pad < other[0] or other[2] + lng_pad < box[0]
                or box[3] + lat_pad < other[1] or other[3] + lat_pad < box[1])

    def same_way(a: dict, b: dict) -> bool:
        box, other = a.get("bbox"), b.get("bbox")
        if not (a.get("lines") and b.get("lines") and box and other) or apart(box, other):
            return False
        cos0 = math.cos(math.radians((box[1] + box[3]) / 2))
        one, two = flat(a["lines"], cos0), flat(b["lines"], cos0)
        return max(share(pieces(one), grid(two)), share(pieces(two), grid(one))) >= RELATED_SHARE

    name = lambda h: str((h.get("title") or {}).get("be") or "").strip().casefold()  # noqa: E731
    found: dict[str, list[dict]] = {h["slug"]: [] for h in hikes}
    for i, a in enumerate(hikes):
        for b in hikes[i + 1:]:
            if (name(a) and name(a) == name(b)) or same_way(a, b):
                found[a["slug"]].append(b)
                found[b["slug"]].append(a)
    return {slug: [h["slug"] for h in sorted(others, key=lambda h: (h.get("date") or "", h["slug"]), reverse=True)]
            for slug, others in found.items()}


# --- Месца на трэку -----------------------------------------------------------------

def _project(lat: float, lng: float, days: list[dict], only: int | None = None) -> tuple | None:
    """Найбліжэйшае да каардынат месца трэку: (адхіленне ў метрах, індэкс дня, пункт (lat, lng, alt, ts, km)).

    Шукаецца праекцыя на адрэзкі паміж суседнімі пунктамі кожнага тура, а не найбліжэйшы пункт: на разрэджаным
    трэку месца проста на сцежцы інакш лічылася б далёкім. Разрыў паміж турамі — не адрэзак маршруту.
    Вышыня, час і кіламетр — па прамой паміж канцамі адрэзка. None, калі ў трэку няма пунктаў.
    """
    best = min(_nearest_on_segments(lat, lng, days, only), key=lambda found: found[0], default=None)
    if best is None:
        return None
    d2, di, _, a, b, part, _ = best
    return math.sqrt(d2), di, _between(a, b, part)


def _nearest_on_segments(lat: float, lng: float, days: list[dict], only: int | None = None):
    """Для кожнага адрэзка трэку па парадку — найбліжэйшае да каардынат месца на ім:
    (квадрат адлегласці ў м², індэкс дня, нумар тура ў дні, пачатак адрэзка, яго канец, доля ад пачатку,
    квадрат адлегласці да канца адрэзка). Першы пункт тура — адрэзак з аднаго пункта."""
    scale = math.pi / 180 * EARTH_R
    cos0 = math.cos(math.radians(lat))
    for di, day in enumerate(days):
        if only is not None and di != only:
            continue
        for si, seg in enumerate(day["segs"]):
            before = None
            for p in seg:
                x, y = (p[1] - lng) * cos0 * scale, (p[0] - lat) * scale  # метры ад шуканага месца
                if before is None:
                    yield x * x + y * y, di, si, p, p, 0.0, x * x + y * y
                else:
                    ax, ay, a = before
                    dx, dy = x - ax, y - ay
                    length2 = dx * dx + dy * dy
                    part = max(0.0, min(1.0, -(ax * dx + ay * dy) / length2)) if length2 else 0.0
                    cx, cy = ax + part * dx, ay + part * dy
                    yield cx * cx + cy * cy, di, si, a, p, part, x * x + y * y
                before = (x, y, p)


PHOTO_PASS_M = 30  # запас у метрах: калі трэк вярнуўся назад (новая праходка) і калі праходка яшчэ «гэтак жа блізка»


def _passes(lat: float, lng: float, days: list[dict], within: float) -> list[tuple]:
    """Праходкі трэку каля каардынат, не далей за `within` метраў: найбліжэйшае месца кожнай (запісы як
    у _nearest_on_segments).

    Пакуль трэк не выйшаў з радыуса `within`, аўтар увесь час побач з месцам — гэта адно знаходжанне. За яго трэк
    можа прайсці каля месца некалькі разоў: туды і назад праз разварот, па суседніх вітках серпанціну. Новая
    праходка пачынаецца, калі трэк вярнуўся назад: шлях па ім ад найбліжэйшага месца бягучай праходкі стаў
    прыкметна даўжэйшы за прамую — больш як на чвэрць (столькі можа дадаць дрыжанне GPS) і яшчэ на PHOTO_PASS_M
    метраў. Такая мера не залежыць ад таго, як далёка ўбок ад сцежкі пункт GPS.
    З праходак аднаго знаходжання застаюцца тыя, што не больш як на PHOTO_PASS_M далейшыя за найбліжэйшую: яны
    ідуць з розніцай у хвіліны, і суседні віток серпанціну за сто метраў — не месца здымкі, нават калі па
    гадзінніку камеры ён бліжэй.
    """
    passes, stay = [], []  # stay: праходкі бягучага знаходжання — (квадрат адлегласці, запіс, найбліжэйшае месца)
    limit, tour = within * within, None

    def leave():
        """Трэк выйшаў з радыуса або тур скончыўся: знаходжанне закрываецца."""
        if stay:
            near = (math.sqrt(min(d2 for d2, _, _ in stay)) + PHOTO_PASS_M) ** 2
            passes.extend(found for d2, found, _ in stay if d2 <= near)
            stay.clear()

    for found in _nearest_on_segments(lat, lng, days):
        if found[1:3] != tour:
            leave()
            tour = found[1:3]
        if found[0] <= limit:
            point = _between(*found[3:6])
            best = stay[-1][2] if stay else None
            along = abs(point[4] - best[4]) * 1000 if best else 0.0  # шлях па трэку ад найбліжэйшага месца праходкі
            if best and along <= 1.25 * haversine(point[0], point[1], best[0], best[1]) + PHOTO_PASS_M:
                if found[0] < stay[-1][0]:
                    stay[-1] = (found[0], found, point)  # тая ж праходка, а месца бліжэйшае
            else:
                stay.append((found[0], found, point))
        if found[6] > limit:
            leave()
    leave()
    return passes


def _project_at_time(lat: float, lng: float, days: list[dict], ts: float, within: float) -> tuple | None:
    """Як _project, але з некалькіх праходак каля каардынат выбіраецца бліжэйшая па часе да моманту `ts`.

    На маршруце «туды і назад» трэк праходзіць праз адно месца двойчы; геаметрычна абедзве праходкі аднолькава
    блізкія, і без часу фота са зваротнага шляху атрымала б кіламетр шляху туды. Што лічыцца праходкай —
    гл. _passes. None, калі трэк не падыходзіць бліжэй за `within` метраў.
    """
    places = [(math.sqrt(found[0]), found[1], _between(*found[3:6])) for found in _passes(lat, lng, days, within)]
    timed = [place for place in places if place[2][3] is not None]
    if timed:
        return min(timed, key=lambda place: (abs(place[2][3] - ts), place[0]))
    return min(places, key=lambda place: place[0], default=None)


def _between(a: tuple, b: tuple, part: float) -> tuple:
    """Пункт (lat, lng, alt, ts, km) на долі `part` адрэзка ад a да b — па прамой."""
    def mix(i: int):
        if a[i] is None or b[i] is None:
            return a[i] if part < 0.5 else b[i]  # значэнне ёсць толькі на адным канцы адрэзка
        return a[i] + (b[i] - a[i]) * part

    return tuple(mix(i) for i in range(5))


def _at_km(day: dict, km: float) -> tuple:
    """Пункт дня на кіламетры `km`: па прамой паміж суседнімі пунктамі аднаго тура — трэк можа быць разрэджаны.

    На мяжы тураў (кіламетр у іх агульны) — канец ранейшага тура; кіламетр за фінішам — апошні пункт.
    """
    for seg in day["segs"]:
        if seg and seg[0][4] <= km <= seg[-1][4]:
            i = bisect.bisect_left([p[4] for p in seg], km)
            a, b = seg[max(i - 1, 0)], seg[i]
            return _between(a, b, (km - a[4]) / (b[4] - a[4]) if b[4] > a[4] else 1.0)
    return min(_points(day), key=lambda p: abs(p[4] - km))


# --- Папраўка гадзінніка камеры (поле photo_clock у файле паходу) ----------------------

_SHIFT_RE = re.compile(r"\s*([+-]?)\s*((?:[0-9]+\s*[dhm]\s*)+)")
_SHIFT_UNITS = {"d": "days", "h": "hours", "m": "minutes"}


def _clock_shift(value) -> timedelta | None:
    """«-31d», «+1h», «-1d 2h 30m» → прамежак часу; None — запіс няправільны."""
    m = _SHIFT_RE.fullmatch(value) if isinstance(value, str) else None
    if not m:
        return None
    total = sum((timedelta(**{_SHIFT_UNITS[unit]: int(n)}) for n, unit in re.findall(r"([0-9]+)\s*([dhm])", m[2])), timedelta())
    return -total if m[1] == "-" else total


def photo_clock(slug: str, rules):
    """Папраўка гадзінніка камеры: функцыя (імя файла, час з фота ISO) → сапраўдны час ISO.

    Правілы з поля `photo_clock` файла паходу — спіс запісаў; для фота дзейнічае першы запіс, які яму падыходзіць:
      date: 2025-11-25       — якія фота: з такой датай у камеры (неабавязкова);
      files: "IMG_07*"       — і/або з такім імем, шаблон з * і ? або спіс шаблонаў (неабавязкова);
      real_date: 2025-10-25  — на самай справе яны зробленыя ў гэты дзень (патрабуе date);
      shift: "-1h"           — і/або перавесці гадзіннік: d — дні, h — гадзіны, m — хвіліны.
    Няправільны запіс прапускаецца з папярэджаннем. Сам час у фота і ў кэшы не мяняецца.
    """
    def warn(message: str) -> None:
        print(f"  ! {slug}: photo_clock: {message}")

    found = []
    if rules is not None and not isinstance(rules, list):
        warn(f"чакаецца спіс запісаў, а не «{rules}»")
        rules = None
    for i, rule in enumerate(rules or [], 1):
        what = f"запіс {i}"
        if not isinstance(rule, dict):
            warn(f"{what}: чакаюцца палі date, files, real_date, shift")
            continue
        unknown = set(rule) - {"date", "files", "real_date", "shift"}
        if unknown:
            warn(f"{what}: невядомыя палі: {', '.join(sorted(map(str, unknown)))}")
        day = iso_date(rule["date"]) if rule.get("date") is not None else None
        if rule.get("date") is not None and not day:
            warn(f"{what}: няправільная дата date: {rule['date']}")
            continue
        files = rule.get("files")
        patterns = [files] if isinstance(files, str) else files
        if patterns is not None and (not isinstance(patterns, list) or not patterns
                                     or not all(isinstance(p, str) and p.strip() for p in patterns)):
            warn(f"{what}: files — шаблон імя або спіс шаблонаў, а не «{files}»")
            continue
        shift = timedelta()
        if rule.get("real_date") is not None:
            real = iso_date(rule["real_date"])
            if not real or not day:
                warn(f"{what}: real_date — сапраўдная дата для фота з датай date (патрэбны абедзве даты)")
                continue
            shift += date.fromisoformat(real) - date.fromisoformat(day)
        if rule.get("shift") is not None:
            extra = _clock_shift(rule["shift"])
            if extra is None:
                warn(f"{what}: shift — напрыклад \"-31d\", \"+1h\" або \"-1d 2h 30m\", а не «{rule['shift']}»")
                continue
            shift += extra
        if rule.get("real_date") is None and rule.get("shift") is None:
            warn(f"{what}: няма ні real_date, ні shift — папраўляць няма чым")
            continue
        found.append((day, [p.strip().lower() for p in patterns] if patterns else None, shift))

    # подпіс правіл: месцы фота, палічаныя пры іншых правілах, з папярэдняга індэкса не бяруцца
    key = hashlib.sha1(json.dumps([[day, patterns, shift.total_seconds()] for day, patterns, shift in found])
                       .encode()).hexdigest()[:10] if found else None

    def clock(name, taken):
        if not taken or not found:
            return taken
        try:
            moment = datetime.fromisoformat(taken)
        except (TypeError, ValueError):
            return taken
        low = str(name).lower()
        for day, patterns, shift in found:
            if day and moment.date().isoformat() != day:
                continue
            if patterns and not any(fnmatchcase(low, p) or fnmatchcase(Path(low).stem, p) for p in patterns):
                continue
            return (moment + shift).isoformat()
        return taken

    clock.key = key
    return clock


def clock_manifest(manifest: dict, clock) -> dict:
    """Маніфест фота з папраўленым часам здымкі (original — для месца на трэку, taken — для парадку).

    Палі, якіх у запісе няма, не дадаюцца: запіс без original — са старога маніфеста, час яго яшчэ не прачытаны.
    """
    return {key: {**m, **{field: clock(key, m[field]) for field in ("original", "taken") if field in m}}
            for key, m in manifest.items()}


PHOTO_GPS_M = 300  # GPS фота далей за гэта ад трэку — не месца на маршруце (стары або памылковы адбітак, фота з дому)


def photo_places(photos: list, manifest: dict, days: list[dict]) -> list:
    """Месца здымкі кожнага апублікаванага фота (у тым жа парадку) або None, калі яно невядомае.

    Калі ў фота ёсць GPS каля трэку, месца — сам пункт GPS (кіламетр і вышыня — ад месца трэку побач; калі трэк
    праходзіць там некалькі разоў — ад праходкі, бліжэйшай па часе да здымкі). Інакш — месца на трэку ў момант
    здымкі паводле часу з EXIF, і толькі ў межах запісу тура: дзе аўтар быў за дваццаць хвілін да старту,
    невядома. Фота без часу і GPS або з няправільнай датай у камеры застаюцца без месца: яно не выдумляецца.
    """
    by_name = {m.get("name"): m for m in manifest.values() if isinstance(m, dict)}
    return [_photo_place(by_name.get(photo_name(p)) or {}, days) for p in photos]


def photo_places_key(days: list[dict], clock_key: str | None) -> str:
    """Подпіс таго, ад чаго залежаць месцы фота апрача саміх фота: «<трэк>.<папраўка гадзінніка>».

    Трэк — усе пункты ўсіх тураў (каардынаты, вышыня, час, кіламетр), падзел на дні і туры і часавы пояс: месца
    фота залежыць ад кожнага з іх. Файл трэку на сайце для гэтага не падыходзіць: у ім няма часу.
    """
    track = [[day["tz"].utcoffset(None).total_seconds(), day["segs"]] for day in days]
    return f"{hashlib.sha1(json.dumps(track).encode()).hexdigest()[:10]}.{clock_key or '-'}"


def photo_clock_kept(places_key, clock_key: str | None) -> bool:
    """Ці палічаны ранейшыя месцы фота з той жа папраўкай гадзінніка — для паходу, трэк якога застаўся ранейшы
    (тура няма ў кэшы, і параўнаць сам трэк няма з чым). Індэкс без подпісу — месцы палічаны без папраўкі."""
    if not isinstance(places_key, str):
        return clock_key is None
    return places_key.rpartition(".")[2] == (clock_key or "-")


def _photo_place(entry: dict, days: list[dict]) -> dict | None:
    if not days:
        return None
    tz, ts = days[0]["tz"], None
    if entry.get("original"):  # час арыгінальнага здымка, а не дата змены файла
        try:
            ts = datetime.fromisoformat(entry["original"]).replace(tzinfo=tz).timestamp()
        except (TypeError, ValueError):
            ts = None
    pos = _by_time(days, ts, tolerance=0) if ts is not None else None  # без допуску: гэта не ручны запіс
    gps = entry.get("gps")  # [даўгата, шырата]
    point = lat_lng([gps[1], gps[0]]) if isinstance(gps, (list, tuple)) and len(gps) == 2 else None
    if point:
        if ts is not None:
            found = _project_at_time(point[0], point[1], days, ts, PHOTO_GPS_M)
        else:
            found = _project(point[0], point[1], days)
            found = found if found and found[0] <= PHOTO_GPS_M else None
        if found:
            pos = _position(days, found[1], found[2], found[0])
            pos["at"] = [round(point[1], 5), round(point[0], 5)]
    if pos is None:
        return None
    pos["time"] = _clock(ts, tz)  # час здымкі; без яго — без часу (час праходу трэку побач — не час фота)
    if pos["time"] is None:
        del pos["time"]
    return pos


def _position(days: list[dict], di: int, point: tuple, off: float | None = None) -> dict:
    pos = {"day": di, "km": round(point[4], 1), "at": [round(point[1], 5), round(point[0], 5)],
           "alt": round(point[2]) if point[2] is not None else None, "time": _clock(point[3], days[di]["tz"])}
    if off is not None and off >= 30:
        pos["off"] = round(off)  # месца збоку ад трэку, метраў па прамой
    return pos


def _by_time(days: list[dict], ts: float, only_day: int | None = None, tolerance: float = PHOTO_TOLERANCE_S) -> dict | None:
    """Месца на трэку ў момант `ts`. Кожны адрэзак (тур) дня разглядаецца асобна: момант у доўгім
    перапынку паміж турамі не мае месца на трэку — дапускаецца толькі `tolerance` секунд ад межаў запісу.

    Унутры тура месца, вышыня і кіламетр бяруцца па прамой паміж суседнімі па часе пунктамі (трэк можа быць
    разрэджаны), час — сам шуканы момант. Момант каля мяжы запісу — яе крайні пункт.
    """
    best = None
    for di, day in enumerate(days):
        if only_day is not None and di != only_day:
            continue
        for seg in day["segs"]:
            pts = [p for p in seg if p[3] is not None]
            if not pts or not pts[0][3] - tolerance <= ts <= pts[-1][3] + tolerance:
                continue
            times = [p[3] for p in pts]
            if ts <= times[0] or ts >= times[-1]:
                point = pts[0] if ts <= times[0] else pts[-1]
                gap = abs(point[3] - ts)
            else:
                i = bisect.bisect_right(times, ts)  # times[i - 1] <= ts < times[i]
                a, b = pts[i - 1], pts[i]
                point, gap = _between(a, b, (ts - a[3]) / (b[3] - a[3])), 0.0
            if best is None or gap < best[0]:
                best = (gap, di, point)
    return _position(days, best[1], best[2]) if best else None


_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")


def locate(spec: dict, days: list[dict], photo_times: dict) -> tuple[dict | None, str | None]:
    """Месца на трэку паводле `photo`, `time`, `km` або `at`: (месца, прычына няўдачы).

    photo — імя фота з тэчкі «Сайт» (месца — па сапраўдным часе здымкі з EXIF); time — «ГГ:ХХ» (і `day`, калі
    дзён некалькі); km — адлегласць ад пачатку дня па трэку; at — [шырата, даўгата] (бліжэйшы пункт трэку).
    Памылковае значэнне не падмяняецца «бліжэйшым магчымым»: запіс застаецца без месца, з папярэджаннем.
    photo_times: імя фота → час здымкі з EXIF або None, калі часу ў фота няма.
    """
    if _ref(spec) is None:
        return None, None  # месца не пазначана — запіс паказваецца без кропкі
    if not days:
        return None, "няма трэку"
    day, only = spec.get("day"), None
    if day is not None and day != "":
        if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= len(days):
            return None, f"няправільны дзень day: {day} (дзён у паходзе: {len(days)})"
        only = day - 1
    tz = days[0]["tz"]
    if spec.get("at") is not None:
        point = lat_lng(spec["at"])
        if point is None:
            return None, f"няправільныя каардынаты at: {spec['at']} (трэба [шырата, даўгата])"
        lat, lng = point
        found = _project(lat, lng, days, only)
        if found is None:
            return None, "няма трэку"
        d, di, point = found
        pos = _position(days, di, point, d)
        pos["at"] = [round(lng, 5), round(lat, 5)]  # кропка на карце — сапраўднае месца, а не праекцыя на трэк
        return pos, None
    if spec.get("photo") is not None:
        key, candidates = find_photo(spec["photo"], photo_times)
        if key is None:
            return None, photo_problem(spec["photo"], candidates)
        if not photo_times[key]:
            return None, f"у фота {spec['photo']} няма часу здымкі (EXIF) — пазначце time, km або at"
        pos = _by_time(days, datetime.fromisoformat(photo_times[key]).replace(tzinfo=tz).timestamp(), only)
        return pos, None if pos else f"фота {spec['photo']} знята па-за часам трэку"
    if spec.get("time") is not None:
        value = spec["time"]
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 24 * 60:
            hh, mm = divmod(value, 60)  # YAML чытае 10:58 без двукосся як лік 658 (хвіліны ад поўначы)
        else:
            m = _TIME_RE.fullmatch(str(value).strip())
            if not m or int(m[1]) > 23 or int(m[2]) > 59:
                return None, f"няправільны час time: {value} (трэба ГГ:ХХ)"
            hh, mm = int(m[1]), int(m[2])
        found = []
        for di, dd in enumerate(days):
            if only is not None and di != only:
                continue
            stamps = [p[3] for p in _points(dd) if p[3] is not None]
            if not stamps:
                continue
            # Запіс мог працягнуцца пасля поўначы: правяраюцца ўсе каляндарныя даты, якія ён ахоплівае.
            first = datetime.fromtimestamp(stamps[0] - PHOTO_TOLERANCE_S, tz).date()
            last = datetime.fromtimestamp(stamps[-1] + PHOTO_TOLERANCE_S, tz).date()
            for n in range((last - first).days + 1):
                moment = datetime.combine(first + timedelta(days=n), datetime.min.time(), tz) + timedelta(hours=hh, minutes=mm)
                pos = _by_time(days, moment.timestamp(), di)
                if pos:
                    found.append(pos)
        if len(found) == 1:
            return found[0], None
        if found:
            return None, f"час {hh:02d}:{mm:02d} ёсць у трэку некалькі разоў — дадайце day або пазначце km"
        return None, f"час {hh:02d}:{mm:02d} па-за часам трэку"
    value = spec["km"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None, f"няправільная адлегласць km: {value}"
    di = only if only is not None else 0
    pts = _points(days[di])
    if not 0 <= value <= pts[-1][4] + 0.3:
        return None, f"кіламетр km: {value} па-за трэкам дня {di + 1} (0–{pts[-1][4]:.1f} км)"
    return _position(days, di, _at_km(days[di], value)), None


def find_photo(name, keys) -> tuple[str | None, list[str]]:
    """Файл фота паводле імя з `photo:` → (імя файла з маніфеста або None, кандыдаты).

    Дакладнае імя файла мае перавагу; імя без пашырэння падыходзіць, толькі калі такое фота адно
    (`A.jpg` і `A.png` — розныя фота: тады трэба поўнае імя). Без кандыдатаў фота невядомае,
    з некалькімі — імя неадназначнае.
    """
    low = str(name).strip().lower()
    exact = [k for k in keys if k.lower() == low]
    if exact:
        return exact[0], exact[:1]
    stems = {low, Path(low).stem}
    same = sorted(k for k in keys if Path(k).stem.lower() in stems)
    return (same[0] if len(same) == 1 else None), same


def photo_problem(name, candidates: list[str]) -> str:
    if candidates:
        return f"імя фота {name} неадназначнае ({', '.join(candidates)}) — пазначце поўнае імя файла"
    return f"фота {name} няма сярод фота паходу"


def _ref(spec: dict) -> str | None:
    """Ключ спосабу, якім пазначана месца: па ім ранейшае месца бярэцца з індэкса, калі трэку няма ў кэшы."""
    for key in ("at", "photo", "time", "km"):
        if spec.get(key) is not None:
            return f"{key}:{spec.get('day') or ''}:{str(spec[key]).lower()}"
    return None


# --- Блок practical: з YAML ---------------------------------------------------------

def _number(value, what: str, warn) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        warn(f"{what}: чакаецца неадмоўны лік, а не «{value}»")
        return None
    return round(float(value), 2)


def _flag(value) -> bool | None:
    """так / не / не вядома: пустое поле — «не вядома», а не «не»."""
    return value if isinstance(value, bool) else None


def _choice(value, allowed: tuple, what: str, warn, default=None):
    if value is None or value == "":
        return default
    key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if key not in allowed:
        warn(f"{what}: невядомае значэнне «{value}» ({' | '.join(allowed)})")
        return default
    return key


_TRAVEL_RE = re.compile(r"\s*([0-9]+):([0-5][0-9])\s*")  # гадзін можа быць больш за 23, хвілін — роўна дзве лічбы, 00–59


def _travel_minutes(value, warn) -> int | None:
    """Час у дарозе: хвіліны лікам або «г:хх». Радок з хвілінамі па-за 00–59 («1:70») — памылка ўводу, а не 130 хвілін."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return round(value)
    m = _TRAVEL_RE.fullmatch(value) if isinstance(value, str) else None
    if m:
        return int(m[1]) * 60 + int(m[2])
    warn(f"getting_there.time: чакаюцца хвіліны або «г:хх» з хвілінамі 00–59, а не «{value}»")
    return None


def _text(value, what: str, warn) -> dict:
    """Тэкст радком або на дзвюх мовах {be, en}; іншае значэнне прапускаецца з папярэджаннем."""
    if value is None or isinstance(value, str):
        return text_pair(value)
    if isinstance(value, dict) and all(v is None or isinstance(v, str) for v in value.values()):
        return text_pair(value)
    warn(f"{what}: чакаецца тэкст або {{be, en}}, а не «{value}»")
    return text_pair(None)


def _items(value, what: str, warn) -> list:
    """Спіс запісаў; іншае значэнне прапускаецца з папярэджаннем."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    warn(f"{what}: чакаецца спіс, а не «{value}»")
    return []


def _amount(value, n_days: int, what: str, warn) -> tuple[list | None, float | None]:
    """Лік за ўвесь паход або спіс па днях → (значэнні па днях або None, сума за паход або None).

    Спіс дапаўняецца невядомымі днямі; сума ёсць, толькі калі лічба вядомая за кожны дзень:
    пустое — «няма звестак», а не нуль.
    """
    if value is None or value == "":
        return None, None
    if not isinstance(value, list):
        return None, _number(value, what, warn)
    days = max(n_days, 1)
    if len(value) != days:
        warn(f"{what}: у спісе {len(value)} значэнняў, а дзён у паходзе {days}")
    rows = [_number(v, what, warn) for v in value[:days]] + [None] * (days - len(value))
    return rows, (round(sum(rows), 2) if all(v is not None for v in rows) else None)


def parse_practical(slug: str, block, *, days: list[dict], stats: dict | None, n_days: int, end: str, manifest: dict,
                    photos: list, prev: dict | None, home=None, prev_photos: list | None = None,
                    rates: dict | None = None, start: str = "", track_kept: bool = False,
                    photo_places_fresh: bool = True) -> tuple[dict | None, str | None]:
    """Блок `practical:` → (звесткі аўтара для індэкса або None, ручное выпраўленне тыпу маршруту або None).

    Няправільнае поле прапускаецца з папярэджаннем і не спыняе зборку.
    track_kept — пунктаў трэку няма (тура няма ў кэшы), але на сайце застаецца ранейшы трэк: месцы запісаў
    бяруцца з папярэдняга індэкса. Калі трэку ў паходу больш няма, няма і месцаў на ім.
    photo_places_fresh — ранейшыя месцы па фота палічаны з цяперашнімі трэкам і папраўкай гадзінніка (подпіс
    photoKey супаў). Інакш ранейшае месца запісу з `photo:` састарэла і з папярэдняга індэкса не бярэцца.
    """
    if block is None:
        return None, None
    if not isinstance(block, dict):
        print(f"  ! {slug}: practical: чакаецца спіс палёў «ключ: значэнне» — блок прапушчаны")
        return None, None

    def warn(message: str) -> None:
        print(f"  ! {slug}: practical: {message}")

    unknown = set(block) - {"checked", "route_type", "weather_notes", "water", "getting_there", "places", *NOTE_KEYS}
    if unknown:
        warn(f"невядомыя палі: {', '.join(sorted(map(str, unknown)))}")

    # Месца па фота — толькі па часе арыгінальнага здымка (`original` з EXIF): запасны час для парадку
    # ў галерэі (`taken`) паставіў бы крыніцу ў выпадковы пункт трэку. Фота без поля `original` — са старога
    # маніфеста, час якога яшчэ не дачытаны (тэчка з фота недаступная).
    photo_times = {key: m["original"] for key, m in manifest.items() if "original" in m}
    unread = set(manifest) - set(photo_times)
    published = {photo_name(p): i for i, p in enumerate(photos)}  # імя без «-t», калі засталася толькі мініяцюра
    photo_index = {key: published.get(m.get("name")) for key, m in manifest.items()}
    # Ранейшыя месца і фота запісаў: калі крыніцы для разліку няма (кэш не захоўваецца ў Git), яны застаюцца.
    old = {}
    for group in (((prev or {}).get("water") or {}).get("sources") or [], (prev or {}).get("places") or []):
        for item in group:
            index = item.get("photo")
            known = isinstance(index, int) and prev_photos and 0 <= index < len(prev_photos)
            if item.get("ref"):
                old[item["ref"]] = (item.get("pos"), photo_name(prev_photos[index]) if known else None)

    has_track = bool(days) or track_kept  # ёсць трэк: пункты для разліку або ранейшы апублікаваны

    def placed(spec: dict, what: str) -> dict:
        """Агульныя палі запісу з месцам: фота, дата назірання, заўвага і месца на трэку."""
        item = {"note": _text(spec.get("note"), f"{what}.note", warn), "seen": None, "photo": None, "ref": _ref(spec),
                "pos": None}
        if spec.get("seen"):
            item["seen"] = iso_date(spec["seen"])
            if not item["seen"]:
                warn(f"{what}: няправільная дата seen: {spec['seen']}")
        named = spec.get("photo") is not None
        key, candidates = find_photo(spec["photo"], manifest) if named else (None, [])
        was_pos, was_photo = old.get(item["ref"], (None, None)) if item["ref"] else (None, None)
        # Фота няма ў маніфесце, але яно ёсць у галерэі пад ранейшым імем: маніфест страчаны, а не фота.
        kept = named and key is None and not candidates and was_photo in published
        if key is not None:
            item["photo"] = photo_index.get(key)
        elif kept:
            item["photo"] = published[was_photo]
        lost = named and key is None and not kept  # фота сапраўды няма або імя неадназначнае
        by_photo = (item["ref"] or "").startswith("photo:")
        waiting = by_photo and (key in unread or kept)
        no_source = (not days and track_kept) or (waiting and has_track)  # пералічыць месца няма з чаго
        if by_photo and lost:
            pos, problem = None, photo_problem(spec["photo"], candidates)  # ні фота, ні месца
        elif was_pos and no_source and (photo_places_fresh or not by_photo):
            pos, problem = was_pos, None  # месца з папярэдняга індэкса
        elif was_pos and no_source:
            pos, problem = None, (f"ранейшае месца па фота {spec['photo']} састарэла (змяніўся трэк або папраўка "
                                  "гадзінніка), а пералічыць яго няма з чаго — падключыце дыск з фота і спампуйце туры")
        elif waiting and has_track:
            pos, problem = None, f"час здымкі фота {spec['photo']} яшчэ не прачытаны (тэчка з фота недаступная)"
        else:
            pos, problem = locate(spec, days, photo_times)
        if lost and not by_photo:
            warn(f"{what}: {photo_problem(spec['photo'], candidates)}")  # месца пазначана іншым спосабам
        if problem:
            warn(f"{what}: месца не вызначана — {problem}")
        item["pos"] = pos
        return item

    out: dict = {}
    notes = {key: _text(block.get(key), key, warn) for key in NOTE_KEYS}
    out["notes"] = {key: pair for key, pair in notes.items() if any(pair.values())}  # на любой мове сайта
    weather = _text(block.get("weather_notes"), "weather_notes", warn)
    out["weatherNotes"] = weather if any(weather.values()) else None

    # --- вада: расход і крыніцы
    water_in = block.get("water")
    water = None
    if isinstance(water_in, dict):
        amounts = {k: _amount(water_in.get(k), n_days, f"water.{k}", warn) for k in ("drunk", "taken", "refilled", "left")}
        rows = max((len(r) for r, _ in amounts.values() if r), default=0)  # па днях — калі хоць адно поле спісам
        water = {"days": [{k: (r[i] if r else None) for k, (r, _) in amounts.items()} for i in range(rows)],
                 "available": _flag(water_in.get("available")), "note": _text(water_in.get("note"), "water.note", warn),
                 "sources": []}
        water.update({k: total for k, (_, total) in amounts.items()})
        if water["drunk"] is not None and stats:
            if stats.get("duration"):
                water["perHour"] = round(water["drunk"] / (stats["duration"] / 3600), 2)  # за гадзіну на маршруце
            if stats.get("distance"):
                water["per10km"] = round(water["drunk"] / stats["distance"] * 10, 1)
        if all(water[k] is not None for k in ("drunk", "taken", "refilled", "left")):
            balance = water["taken"] + water["refilled"] - water["left"]
            if abs(balance - water["drunk"]) > 0.25:
                warn(f"water: узята + папоўнена − засталося = {balance:g} л, а выпіта {water['drunk']:g} л — праверце лічбы")
        for i, src in enumerate(_items(water_in.get("sources"), "water.sources", warn), 1):
            if not isinstance(src, dict):
                warn(f"water.sources[{i}]: чакаецца запіс з палямі")
                continue
            what = f"water.sources[{i}]"
            item = placed(src, what)
            item.update({"type": _choice(src.get("type"), SOURCE_TYPES, f"{what}.type", warn, "other"),
                         "name": _text(src.get("name"), f"{what}.name", warn), "water": _flag(src.get("water")),
                         "seasonal": _flag(src.get("seasonal")), "treat": _flag(src.get("treat"))})
            water["sources"].append(item)
    elif water_in is not None:
        warn("water: чакаецца запіс з палямі (drunk, sources …)")
    out["water"] = water

    # --- як дабрацца
    out["gettingThere"] = []
    no_rate: set[str] = set()
    for i, way in enumerate(_items(block.get("getting_there"), "getting_there", warn), 1):
        what = f"getting_there[{i}]"
        if not isinstance(way, dict):
            warn(f"{what}: чакаецца запіс з палямі")
            continue
        date_ = iso_date(way["date"]) if way.get("date") else None
        if way.get("date") and not date_:
            warn(f"{what}: няправільная дата: {way['date']}")
        price = _number(way.get("price"), f"{what}.price", warn)
        # Даляры — разлік па афіцыйным курсе на дату цаны (без даты — на дзень пачатку паходу)
        rate_day = date_ or start
        rate = (rates or {}).get(rate_day) if price is not None else None
        if not (isinstance(rate, (int, float)) and not isinstance(rate, bool) and math.isfinite(rate) and rate > 0):
            if price is not None and rate_day:
                no_rate.add(rate_day)
            rate = None
        out["gettingThere"].append({
            "usd": round(price / rate, 2) if rate else None, "rateDate": rate_day if rate else None,
            "type": _choice(way.get("type"), TRANSPORT_TYPES, f"{what}.type", warn, "other"),
            "from": _text(way.get("from") or home, f"{what}.from", warn),  # home — з config.yaml
            "to": _text(way.get("to"), f"{what}.to", warn),
            "minutes": _travel_minutes(way.get("time"), warn), "km": _number(way.get("km"), f"{what}.km", warn),
            "price": price,  # лары
            "priceFor": _choice(way.get("price_for"), ("car", "person"), f"{what}.price_for", warn),
            "trip": _choice(way.get("trip"), ("one_way", "round_trip"), f"{what}.trip", warn),
            "date": date_, "boarding": _text(way.get("boarding"), f"{what}.boarding", warn),
            "road": _text(way.get("road"), f"{what}.road", warn), "note": _text(way.get("note"), f"{what}.note", warn),
        })

    if no_rate:
        print(f"  · {slug}: няма курсу лары на {', '.join(sorted(no_rate))} — запусціце `hike fetch`")

    # --- асаблівыя месцы
    out["places"] = []
    for i, place in enumerate(_items(block.get("places"), "places", warn), 1):
        what = f"places[{i}]"
        if not isinstance(place, dict):
            warn(f"{what}: чакаецца запіс з палямі")
            continue
        item = placed(place, what)
        item.update({"type": _choice(place.get("type"), PLACE_TYPES, f"{what}.type", warn, "other"),
                     "name": _text(place.get("name"), f"{what}.name", warn)})
        out["places"].append(item)

    filled = out["notes"] or out["weatherNotes"] or out["water"] or out["gettingThere"] or out["places"]
    checked = iso_date(block["checked"]) if block.get("checked") else None
    if block.get("checked") and not checked:
        warn(f"няправільная дата checked: {block['checked']}")
    out["checked"] = (checked or end or None) if filled else None  # па змаўчанні звесткі правераны ў дзень паходу
    override = _choice(block.get("route_type"), ROUTE_TYPES, "route_type", warn)
    return (out if filled else None), override

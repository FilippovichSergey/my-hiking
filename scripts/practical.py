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
from pathlib import Path

from common import CACHE, has_track, iso_date, photo_name, read_json, text_pair, tour_local_datetime
from geo import haversine

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
DAYS_JOINED_M = 1000      # дні шматдзённага паходу ідуць адзін за адным, калі начлег у тым жа месцы
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
    """Пункты праз кожныя ~STEP_M метраў: (lat, lng, адлегласць ад пачатку ў метрах) і агульная даўжыня."""
    out, since, total = [(points[0][0], points[0][1], 0.0)], 0.0, 0.0
    for a, b in zip(points, points[1:]):
        d = haversine(a[0], a[1], b[0], b[1])
        total += d
        since += d
        if since >= STEP_M:
            out.append((b[0], b[1], total))
            since = 0.0
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


def hike_route_type(days: list[dict], day_types: list) -> str | None:
    """Тып усяго паходу. Дні з начлегам у адным месцы — адзін маршрут; асобныя вылазкі — агульны тып дзён."""
    if len(days) == 1:
        return day_types[0]
    pts = [_points(d) for d in days]
    joined = all(haversine(a[-1][0], a[-1][1], b[0][0], b[0][1]) <= DAYS_JOINED_M for a, b in zip(pts, pts[1:]))
    if joined:
        return route_type([p for day in pts for p in day])
    known = {t for t in day_types if t}
    return known.pop() if len(known) == 1 else ("mixed" if known else None)


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

def _steepest(points: list[tuple]) -> tuple[dict | None, dict | None]:
    """Найкруцейшыя пад'ём і спуск на адрэзку STEEP_WINDOW_KM: ({grade %, km пачатку}, {…})."""
    pts = [(p[4], p[2]) for p in points if p[2] is not None]
    up = down = None
    j = 0
    for i, (km, alt) in enumerate(pts):
        j = max(j, i)
        while j < len(pts) and pts[j][0] - km < STEEP_WINDOW_KM:
            j += 1
        if j >= len(pts):
            break
        grade = (pts[j][1] - alt) / ((pts[j][0] - km) * 1000) * 100
        if grade > 0 and (up is None or grade > up["grade"]):
            up = {"grade": grade, "km": km}
        if grade < 0 and (down is None or -grade > down["grade"]):
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
    facts = {"type": route_type(pts), "start": _end(pts[0], day["tz"]), "finish": _end(pts[-1], day["tz"])}
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
    up, down = _steepest(pts)
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


def weather_key(points: list[dict]) -> str:
    """Подпіс запыту: калі трэк змяніўся, раней спампаванае надвор'е да яго ўжо не адносіцца."""
    return hashlib.sha1(json.dumps(points, sort_keys=True).encode()).hexdigest()[:12]


def summarize_weather(points: list[dict], series: list[dict]) -> dict:
    """Паказчыкі дня з гадзінных шэрагаў (па адным на пункт; `time` — секунды UTC).

    points — значэнні бліжэйшай гадзіны да моманту, калі аўтар быў у пункце; span — межы за ўвесь час паходу
    па ўсіх пунктах (ападкі і снег — найбольшая сума сярод пунктаў). Пустое значэнне сэрвісу застаецца пустым,
    а не нулём; тады complete = False і `hike fetch` паспрабуе яшчэ раз.
    """
    def finite(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None

    lo, hi = points[0]["ts"] - WEATHER_EDGE_S, points[-1]["ts"] + WEATHER_EDGE_S
    readings, complete = [], True
    seen = {field: [] for field in WEATHER_VARS.values()}
    totals = {"precip": [], "snow": []}
    for point, hourly in zip(points, series):
        times = hourly["time"]
        nearest = min(range(len(times)), key=lambda i: abs(times[i] - point["ts"]))
        reading = {field: finite(hourly[var][nearest]) for var, field in WEATHER_VARS.items()}
        complete = complete and all(v is not None for v in reading.values())
        readings.append({"role": point["role"], "alt": point["alt"], "ts": point["ts"], **reading})
        inside = [i for i, t in enumerate(times) if lo <= t <= hi]
        for var, field in WEATHER_VARS.items():
            values = [v for v in (finite(hourly[var][i]) for i in inside) if v is not None]
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
        if sums:
            span[field] = round(max(sums), 1)
    return {"points": readings, "span": span, "complete": complete}


def public_weather(cached: dict, tz) -> dict:
    """Надвор'е дня для індэкса сайта: мясцовы час замест моманту, без службовых палёў кэша."""
    points = [{**{k: v for k, v in p.items() if k != "ts"}, "time": _clock(p["ts"], tz)} for p in cached["points"]]
    return {"points": points, "span": cached.get("span") or {}, "fetched": cached.get("fetched")}


# --- Месца на трэку -----------------------------------------------------------------

def _position(days: list[dict], di: int, point: tuple, off: float | None = None) -> dict:
    pos = {"day": di, "km": round(point[4], 1), "at": [round(point[1], 5), round(point[0], 5)],
           "alt": round(point[2]) if point[2] is not None else None, "time": _clock(point[3], days[di]["tz"])}
    if off is not None and off >= 30:
        pos["off"] = round(off)  # месца збоку ад трэку, метраў па прамой
    return pos


def _by_time(days: list[dict], ts: float, only_day: int | None = None) -> dict | None:
    """Пункт трэку, бліжэйшы па часе. Кожны адрэзак (тур) дня разглядаецца асобна: момант у доўгім
    перапынку паміж турамі не мае месца на трэку — дапускаецца толькі PHOTO_TOLERANCE_S ад межаў запісу."""
    best = None
    for di, day in enumerate(days):
        if only_day is not None and di != only_day:
            continue
        for seg in day["segs"]:
            pts = [p for p in seg if p[3] is not None]
            if not pts or not pts[0][3] - PHOTO_TOLERANCE_S <= ts <= pts[-1][3] + PHOTO_TOLERANCE_S:
                continue
            times = [p[3] for p in pts]
            i = min(bisect.bisect_left(times, ts), len(pts) - 1)
            if i and ts - times[i - 1] < times[i] - ts:
                i -= 1
            if best is None or abs(times[i] - ts) < best[0]:
                best = (abs(times[i] - ts), di, pts[i])
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
        try:
            lat, lng = float(spec["at"][0]), float(spec["at"][1])
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                raise ValueError
        except (TypeError, ValueError, IndexError, KeyError):
            return None, f"няправільныя каардынаты at: {spec['at']} (трэба [шырата, даўгата])"
        d, di, point = min(((haversine(lat, lng, p[0], p[1]), di, p) for di, dd in enumerate(days)
                            if only is None or di == only for p in _points(dd)), key=lambda x: x[0])
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
    return _position(days, di, min(pts, key=lambda p: abs(p[4] - value))), None


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


def _travel_minutes(value, warn) -> int | None:
    """Час у дарозе: хвіліны лікам або «г:хх»."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return round(value)
    parts = str(value).split(":")
    if len(parts) == 2 and all(p.strip().isdigit() for p in parts):
        return int(parts[0]) * 60 + int(parts[1])
    warn(f"getting_there.time: чакаюцца хвіліны або «г:хх», а не «{value}»")
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
                    rates: dict | None = None, start: str = "") -> tuple[dict | None, str | None]:
    """Блок `practical:` → (звесткі аўтара для індэкса або None, ручное выпраўленне тыпу маршруту або None).

    Няправільнае поле прапускаецца з папярэджаннем і не спыняе зборку.
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
        if by_photo and lost:
            pos, problem = None, photo_problem(spec["photo"], candidates)  # ні фота, ні месца
        elif was_pos and (not days or waiting):
            pos, problem = was_pos, None  # крыніцы для разліку няма: месца з папярэдняга індэкса
        elif waiting:
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
    out["notes"] = {key: pair for key, pair in notes.items() if pair["be"] or pair["en"]}
    weather = _text(block.get("weather_notes"), "weather_notes", warn)
    out["weatherNotes"] = weather if weather["be"] or weather["en"] else None

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

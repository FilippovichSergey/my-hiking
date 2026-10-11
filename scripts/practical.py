"""Практычная частка паходу: разлікі з трэку і звесткі аўтара з блока `practical:` у файле паходу.

Усё лічыцца лакальна, без сеткі: з тураў у cache/komoot, з маніфеста фота і з YAML.
Аўтаматычныя звесткі («разлік па трэку») і звесткі аўтара («мае назіранні») захоўваюцца асобна:
`facts` — разлік, `practical` — тое, што напісаў аўтар, разам з вылічаным месцам на трэку.
"""
from __future__ import annotations

import bisect
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from common import CACHE, has_track, iso_date, read_json, text_pair, tour_local_datetime
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

def sun_times(day: date, lat: float, lng: float, tz) -> tuple[str, str] | None:
    """(узыход, заход) мясцовым часам «ГГ:ХХ» паводле ўраўнення ўзыходу сонца; None для палярнага дня або ночы.

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

    def clock(julian: float) -> str:
        utc = datetime(2000, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=julian - 2451545.0)
        return (utc.astimezone(tz) + timedelta(seconds=30)).strftime("%H:%M")  # да бліжэйшай хвіліны

    return clock(transit - w), clock(transit + w)


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
    sun = sun_times(date.fromisoformat(day["date"]), pts[0][0], pts[0][1], day["tz"])
    if sun:
        facts["sun"] = {"rise": sun[0], "set": sun[1]}
        if facts["finish"]["time"]:  # колькі хвілін святла заставалася пасля фінішу (мінус — фініш у цемры)
            facts["sun"]["left"] = _minutes(sun[1]) - _minutes(facts["finish"]["time"])
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


# --- Месца на трэку -----------------------------------------------------------------

def _position(days: list[dict], di: int, point: tuple, off: float | None = None) -> dict:
    pos = {"day": di, "km": round(point[4], 1), "at": [round(point[1], 5), round(point[0], 5)],
           "alt": round(point[2]) if point[2] is not None else None, "time": _clock(point[3], days[di]["tz"])}
    if off is not None and off >= 30:
        pos["off"] = round(off)  # месца збоку ад трэку, метраў па прамой
    return pos


def _by_time(days: list[dict], ts: float, only_day: int | None = None) -> dict | None:
    best = None
    for di, day in enumerate(days):
        if only_day is not None and di != only_day:
            continue
        pts = [p for p in _points(day) if p[3] is not None]
        if not pts or not pts[0][3] - PHOTO_TOLERANCE_S <= ts <= pts[-1][3] + PHOTO_TOLERANCE_S:
            continue
        times = [p[3] for p in pts]
        i = min(bisect.bisect_left(times, ts), len(pts) - 1)
        if i and ts - times[i - 1] < times[i] - ts:
            i -= 1
        if best is None or abs(times[i] - ts) < best[0]:
            best = (abs(times[i] - ts), di, pts[i])
    return _position(days, best[1], best[2]) if best else None


def locate(spec: dict, days: list[dict], photo_times: dict[str, str]) -> tuple[dict | None, str | None]:
    """Месца на трэку паводле `photo`, `time`, `km` або `at`: (месца, прычына няўдачы).

    photo — імя фота з тэчкі «Сайт» (месца — па часе здымкі); time — «ГГ:ХХ» (і `day`, калі дзён некалькі);
    km — адлегласць ад пачатку дня па трэку; at — [шырата, даўгата] (шукаецца бліжэйшы пункт трэку).
    """
    if _ref(spec) is None:
        return None, None  # месца не пазначана — запіс паказваецца без кропкі
    if not days:
        return None, "няма трэку"
    day = spec.get("day")
    only = int(day) - 1 if isinstance(day, int) and 1 <= day <= len(days) else None
    tz = days[0]["tz"]
    if spec.get("at"):
        try:
            lat, lng = float(spec["at"][0]), float(spec["at"][1])
        except (TypeError, ValueError, IndexError):
            return None, f"няправільныя каардынаты at: {spec['at']}"
        d, di, point = min(((haversine(lat, lng, p[0], p[1]), di, p) for di, dd in enumerate(days) for p in _points(dd)),
                           key=lambda x: x[0])
        pos = _position(days, di, point, d)
        pos["at"] = [round(lng, 5), round(lat, 5)]  # кропка на карце — сапраўднае месца, а не праекцыя на трэк
        return pos, None
    if spec.get("photo"):
        taken = photo_times.get(str(spec["photo"]).lower()) or photo_times.get(Path(str(spec["photo"])).stem.lower())
        if not taken:
            return None, f"фота {spec['photo']} няма сярод фота паходу або ў яго няма часу здымкі"
        pos = _by_time(days, datetime.fromisoformat(taken).replace(tzinfo=tz).timestamp())
        return pos, None if pos else f"фота {spec['photo']} знята па-за часам трэку"
    if spec.get("time"):
        try:
            hh, mm = (int(x) for x in str(spec["time"]).split(":")[:2])
        except ValueError:
            return None, f"няправільны час time: {spec['time']} (трэба ГГ:ХХ)"
        for di, dd in enumerate(days):
            if only is not None and di != only:
                continue
            ts = datetime.combine(date.fromisoformat(dd["date"]), datetime.min.time(), tz).timestamp() + hh * 3600 + mm * 60
            pos = _by_time(days, ts, di)
            if pos:
                return pos, None
        return None, f"час {spec['time']} па-за часам трэку"
    if spec.get("km") is not None:
        try:
            km = float(spec["km"])
        except (TypeError, ValueError):
            return None, f"няправільная адлегласць km: {spec['km']}"
        di = only if only is not None else 0
        pts = _points(days[di])
        return _position(days, di, min(pts, key=lambda p: abs(p[4] - km))), None
    return None, None


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
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
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
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return round(value)
    parts = str(value).split(":")
    if len(parts) == 2 and all(p.strip().isdigit() for p in parts):
        return int(parts[0]) * 60 + int(parts[1])
    warn(f"getting_there.time: чакаюцца хвіліны або «г:хх», а не «{value}»")
    return None


def _per_day(value, n_days: int, what: str, warn) -> list | None:
    """Лік за ўвесь паход або спіс па днях → спіс па днях (None — няма звестак)."""
    if value is None or value == "":
        return None
    values = value if isinstance(value, list) else [value]
    if isinstance(value, list) and len(values) != max(n_days, 1):
        warn(f"{what}: у спісе {len(values)} значэнняў, а дзён у паходзе {max(n_days, 1)}")
    return [_number(v, what, warn) for v in values]


def parse_practical(slug: str, block, *, days: list[dict], stats: dict | None, n_days: int, end: str, manifest: dict,
                    photos: list, prev: dict | None, home=None) -> tuple[dict | None, str | None]:
    """Блок `practical:` → (звесткі аўтара для індэкса або None, ручное выпраўленне тыпу маршруту або None)."""
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

    photo_times = {key.lower(): m.get("taken") for key, m in manifest.items() if m.get("taken")}
    photo_times.update({Path(key).stem.lower(): t for key, t in list(photo_times.items())})
    photo_names = {Path(p["src"]).stem: i for i, p in enumerate(photos)}
    photo_index = {key.lower(): photo_names.get(m.get("name")) for key, m in manifest.items()}
    photo_index.update({Path(key).stem.lower(): i for key, i in list(photo_index.items())})
    old = {}
    for group in ((prev or {}).get("water", {}).get("sources") or [], (prev or {}).get("places") or []):
        old.update({item["ref"]: item["pos"] for item in group if item.get("ref") and item.get("pos")})

    def placed(spec: dict, what: str) -> dict:
        """Агульныя палі запісу з месцам: фота, дата назірання, заўвага і месца на трэку."""
        item = {"note": text_pair(spec.get("note")), "seen": None, "photo": None, "ref": _ref(spec), "pos": None}
        if spec.get("seen"):
            item["seen"] = iso_date(spec["seen"])
            if not item["seen"]:
                warn(f"{what}: няправільная дата seen: {spec['seen']}")
        if spec.get("photo"):
            item["photo"] = photo_index.get(str(spec["photo"]).lower(), photo_index.get(Path(str(spec["photo"])).stem.lower()))
        if not days and item["ref"] in old:
            pos, problem = old[item["ref"]], None  # трэку няма ў кэшы: месца з папярэдняга індэкса
        else:
            pos, problem = locate(spec, days, photo_times)
        if problem:
            warn(f"{what}: месца не вызначана — {problem}")
        item["pos"] = pos
        return item

    out: dict = {}
    notes = {key: text_pair(block.get(key)) for key in NOTE_KEYS}
    out["notes"] = {key: pair for key, pair in notes.items() if pair["be"] or pair["en"]}
    weather = text_pair(block.get("weather_notes"))
    out["weatherNotes"] = weather if weather["be"] or weather["en"] else None

    # --- вада: расход і крыніцы
    water_in = block.get("water")
    water = None
    if isinstance(water_in, dict):
        fields = {k: _per_day(water_in.get(k), n_days, f"water.{k}", warn) for k in ("drunk", "taken", "refilled", "left")}
        rows = max((len(v) for v in fields.values() if v), default=0)
        water = {"days": [{k: (v[i] if v and i < len(v) else None) for k, v in fields.items()} for i in range(rows)],
                 "available": _flag(water_in.get("available")), "note": text_pair(water_in.get("note")), "sources": []}
        for k in fields:  # сума за паход — толькі калі лічба ёсць за кожны дзень: пустое не роўна нулю
            vals = [d[k] for d in water["days"]]
            water[k] = round(sum(vals), 2) if vals and all(v is not None for v in vals) else None
        if water["drunk"] is not None and stats:
            if stats.get("duration"):
                water["perHour"] = round(water["drunk"] / (stats["duration"] / 3600), 2)  # за гадзіну на маршруце
            if stats.get("distance"):
                water["per10km"] = round(water["drunk"] / stats["distance"] * 10, 1)
        if all(water[k] is not None for k in ("drunk", "taken", "refilled", "left")):
            balance = water["taken"] + water["refilled"] - water["left"]
            if abs(balance - water["drunk"]) > 0.25:
                warn(f"water: узята + папоўнена − засталося = {balance:g} л, а выпіта {water['drunk']:g} л — праверце лічбы")
        for i, src in enumerate(water_in.get("sources") or [], 1):
            if not isinstance(src, dict):
                warn(f"water.sources[{i}]: чакаецца запіс з палямі")
                continue
            item = placed(src, f"water.sources[{i}]")
            item.update({"type": _choice(src.get("type"), SOURCE_TYPES, f"water.sources[{i}].type", warn, "other"),
                         "name": text_pair(src.get("name")), "water": _flag(src.get("water")),
                         "seasonal": _flag(src.get("seasonal")), "treat": _flag(src.get("treat"))})
            water["sources"].append(item)
    elif water_in is not None:
        warn("water: чакаецца запіс з палямі (drunk, sources …)")
    out["water"] = water

    # --- як дабрацца
    out["gettingThere"] = []
    for i, way in enumerate(block.get("getting_there") or [], 1):
        if not isinstance(way, dict):
            warn(f"getting_there[{i}]: чакаецца запіс з палямі")
            continue
        date_ = iso_date(way["date"]) if way.get("date") else None
        if way.get("date") and not date_:
            warn(f"getting_there[{i}]: няправільная дата: {way['date']}")
        out["gettingThere"].append({
            "type": _choice(way.get("type"), TRANSPORT_TYPES, f"getting_there[{i}].type", warn, "other"),
            "from": text_pair(way.get("from") or home), "to": text_pair(way.get("to")),  # home — з config.yaml
            "minutes": _travel_minutes(way.get("time"), warn), "km": _number(way.get("km"), f"getting_there[{i}].km", warn),
            "price": _number(way.get("price"), f"getting_there[{i}].price", warn),  # лары
            "priceFor": _choice(way.get("price_for"), ("car", "person"), f"getting_there[{i}].price_for", warn),
            "trip": _choice(way.get("trip"), ("one_way", "round_trip"), f"getting_there[{i}].trip", warn),
            "date": date_, "boarding": text_pair(way.get("boarding")), "road": text_pair(way.get("road")),
            "note": text_pair(way.get("note")),
        })

    # --- асаблівыя месцы
    out["places"] = []
    for i, place in enumerate(block.get("places") or [], 1):
        if not isinstance(place, dict):
            warn(f"places[{i}]: чакаецца запіс з палямі")
            continue
        item = placed(place, f"places[{i}]")
        item.update({"type": _choice(place.get("type"), PLACE_TYPES, f"places[{i}].type", warn, "other"),
                     "name": text_pair(place.get("name"))})
        out["places"].append(item)

    filled = out["notes"] or out["weatherNotes"] or out["water"] or out["gettingThere"] or out["places"]
    checked = iso_date(block["checked"]) if block.get("checked") else None
    if block.get("checked") and not checked:
        warn(f"няправільная дата checked: {block['checked']}")
    out["checked"] = (checked or end or None) if filled else None  # па змаўчанні звесткі правераны ў дзень паходу
    override = _choice(block.get("route_type"), ROUTE_TYPES, "route_type", warn)
    return (out if filled else None), override

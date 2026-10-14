"""Спампоўвае знешнія даныя для практычнай часткі ў cache/ (сайт збірае іх `hike build` ужо без сеткі):

  надвор'е падчас паходаў — архіў Open-Meteo па гадзінах   → cache/weather/<паход>.json
  курс лары да даляра на даты цэн — Нацбанк Грузіі          → cache/rates.json
  крыніцы вады і месцы каля трэку — OpenStreetMap (Overpass) → cache/osm/<паход>.json
  дарога на машыне ад home з config.yaml — OSRM            → cache/routes.json
  населеныя пункты старту і фінішу — Nominatim             → cache/geocode.json
  звычайнае надвор'е па месяцах — архіў Open-Meteo, 5 гадоў  → cache/climate/<квадрат>.json

Запуск:  hike fetch            (толькі тое, чаго яшчэ няма)
         hike fetch --refresh  (перакачаць усё нанова)

Пароляў і ключоў не трэба. На сайце гэтыя звесткі падпісаны як даныя сэрвісаў, а не назіранні аўтара.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import math
import time
from datetime import date, datetime, timezone

import requests

import build
import geo
import practical
from common import CACHE, CONTENT, iso_date, load_config, load_yaml, read_json, write_json

API_WEATHER = "https://archive-api.open-meteo.com/v1/archive"
API_RATES = "https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/"
API_OSM = "https://overpass-api.de/api/interpreter"
API_ROUTE = "https://router.project-osrm.org/route/v1/driving/"
ROAD_GAP_M = 300  # аўтадарога сканчаецца далей за гэта ад пункта трэку — пра гэта кажацца на сайце
OSM_BATCH = 8            # паходаў у адным запыце да Overpass: сервер абмяжоўвае частату запытаў
OSM_RETRY_S = (20, 60)   # паўзы перад паўторам, калі сервер заняты
CLIMATE_PAUSE_S = 15     # паміж запытамі звычайнага надвор'я: пяць гадоў даных Open-Meteo лічыць за ~130 запытаў,
                         # а бясплатна можна 600 за хвіліну і 5000 за гадзіну
ERRORS = (requests.RequestException, RuntimeError, ValueError, KeyError, IndexError, TypeError)


def get_json(session: requests.Session, url: str, params: dict):
    r = session.get(url, params=params, timeout=60)
    if r.status_code == 400:  # Open-Meteo тлумачыць памылку запыту ў целе адказу
        raise RuntimeError(r.json().get("reason") or "запыт адхілены")
    r.raise_for_status()
    return r.json()


def fetch_day_weather(session: requests.Session, points: list[dict]) -> dict:
    """Надвор'е аднаго дня паходу: адзін запыт на ўсе яго пункты (старт, найвышэйшы пункт, фініш)."""
    hours = practical.weather_hours(points)  # тыя ж гадзіны, паўнату якіх потым правярае падлік
    first, last = (datetime.fromtimestamp(ts, timezone.utc).date().isoformat() for ts in (hours[0], hours[-1]))
    params = {
        "latitude": ",".join(str(p["lat"]) for p in points), "longitude": ",".join(str(p["lng"]) for p in points),
        "start_date": first, "end_date": last,
        "hourly": ",".join(practical.WEATHER_VARS), "timeformat": "unixtime", "timezone": "GMT",
    }
    if all(p["alt"] is not None for p in points):
        params["elevation"] = ",".join(str(p["alt"]) for p in points)  # тэмпература пералічваецца на вышыню пункта
    data = get_json(session, API_WEATHER, params)
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data.get("reason") or "сэрвіс вярнуў памылку")
    series = [place["hourly"] for place in (data if isinstance(data, list) else [data])]
    if len(series) != len(points):
        raise RuntimeError("нечаканы адказ сэрвісу надвор'я")
    return practical.summarize_weather(points, series)


def fetch_climate(session: requests.Session, lat: float, lng: float) -> dict:
    """Дзённыя тэмпература і ападкі за CLIMATE_YEARS у квадраце сеткі — для звычайнага надвор'я па месяцах."""
    first, last = practical.CLIMATE_YEARS
    data = get_json(session, API_WEATHER, {
        "latitude": round(lat, 1), "longitude": round(lng, 1), "start_date": f"{first}-01-01", "end_date": f"{last}-12-31",
        "daily": ",".join(practical.CLIMATE_DAILY), "timezone": "GMT", "models": practical.CLIMATE_MODEL,
    })
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data.get("reason") or "сэрвіс вярнуў памылку")
    found = {"source": "Open-Meteo Historical Weather API, ERA5-Seamless (ERA5-Land, ERA5)", "model": practical.CLIMATE_MODEL,
             "years": list(practical.CLIMATE_YEARS), "latitude": data.get("latitude"), "longitude": data.get("longitude"),
             "elevation": data.get("elevation"),
             "daily": {k: (data.get("daily") or {}).get(k) for k in ("time", *practical.CLIMATE_DAILY)}}
    if not practical.climate_ok(found):  # іншыя даты, не тыя рады, няма вышыні
        raise RuntimeError("нечаканы адказ сэрвісу надвор'я")
    return found  # асобных значэнняў можа не быць: гл. practical.climate_full


def fetch_rate(session: requests.Session, day: str) -> float | None:
    """Афіцыйны курс: колькі лары за 1 даляр ЗША на дату (у выхадныя дзейнічае апошні ўсталяваны курс)."""
    data = get_json(session, API_RATES, {"currencies": "USD", "date": day})
    currency = data[0]["currencies"][0]
    rate = float(currency["rate"]) / float(currency.get("quantity") or 1)
    return round(rate, 4) if math.isfinite(rate) and rate > 0 else None


def fetch_osm(session: requests.Session, bboxes: list[list[float]]) -> list[dict]:
    """Крыніцы вады і месцы ў абласцях трэкаў з OpenStreetMap (Overpass) адным запытам; заняты сервер — паўторы."""
    query = practical.overpass_query(bboxes)
    for pause in (*OSM_RETRY_S, None):
        r = session.post(API_OSM, data={"data": query}, timeout=180)
        if r.status_code in (429, 502, 503, 504) and pause is not None:
            time.sleep(pause)
            continue
        r.raise_for_status()
        data = r.json()
        if data.get("remark"):  # Overpass адказвае 200 і пры перарваным запыце: вынік няпоўны
            raise RuntimeError(str(data["remark"])[:200])
        return [item for item in map(practical.osm_item, data.get("elements") or []) if item]
    return []


def fetch_route(session: requests.Session, home: tuple, target: tuple, back: bool = False) -> dict | None:
    """Дарога на машыне ад дома да пункта трэку: {km, minutes, off?}; None, калі маршрут не знойдзены.

    back — ад пункта трэку дадому: на дарогах з аднабаковым рухам адлегласць і час у два бакі розныя.
    off — колькі метраў ад аўтадарогі да пункта трэку, калі дарога да яго не даходзіць.
    """
    a, b = (target, home) if back else (home, target)
    data = get_json(session, f"{API_ROUTE}{a[1]},{a[0]};{b[1]},{b[0]}", {"overview": "false"})
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    route = data["routes"][0]
    km, minutes = float(route["distance"]) / 1000, float(route["duration"]) / 60
    if not (math.isfinite(km) and math.isfinite(minutes) and km > 0 and minutes > 0):
        return None
    found = {"km": round(km, 1), "minutes": round(minutes)}
    waypoints = data.get("waypoints")
    near = waypoints[0 if back else -1] if isinstance(waypoints, list) and waypoints else None  # пункт трэку
    snapped = near.get("distance") if isinstance(near, dict) else None
    if isinstance(snapped, (int, float)) and not isinstance(snapped, bool) and math.isfinite(snapped) and snapped > ROAD_GAP_M:
        found["off"] = round(snapped / 100) * 100
    return found


def price_days(h: dict, start: str | None) -> set[str]:
    """Даты, на якія патрэбны курс лары: даты цэн у «Як дабрацца»; цана без даты — на дзень пачатку паходу."""
    block = h.get("practical") if isinstance(h.get("practical"), dict) else {}
    ways = block.get("getting_there") if isinstance(block.get("getting_there"), list) else []
    days = set()
    for way in ways:
        if isinstance(way, dict) and way.get("price") is not None:
            day = (iso_date(way["date"]) if way.get("date") else None) or start
            if day:
                days.add(day)
    return days


def needs(slug: str, h: dict, days: list[dict], start: str | None, home, routes: dict, usd: dict) -> dict:
    """Якія даныя сэрвісаў патрэбны паходу і ці ёсць яны ўжо ў кэшы. Па гэтым спісе `hike fetch` робіць запыты,
    а `hike status` кажа, што яшчэ не спампавана, — правілы адны.

    weather: [(дата, пункты, ключ, ёсць)] — дні з часам у трэку; ёсць — калі звесткі для гэтага трэку поўныя;
    osm: (вобласць, ёсць) або None; routes: [(to | back, пункт, ключ, ёсць)]; names: [(ключ, шырата, даўгата, ёсць)];
    rates: [(дата, ёсць)]; climate: [(квадрат, шырата, даўгата, ёсць)] — звычайнае надвор'е ў найвышэйшым пункце.
    """
    cached = read_json(CACHE / "weather" / f"{slug}.json", {}).get("days") or {}
    weather = []
    for day in days:
        points = practical.weather_points(day)
        if not points:
            continue  # у трэку няма часу (імпартаваны GPX): невядома, калі аўтар быў у пунктах
        key, have = practical.weather_key(points), cached.get(day["date"])
        # `retry` — мінулы раз сэрвіс аддаў няпоўны адказ і засталіся ранейшыя поўныя звесткі: абнаўленне не скончана
        weather.append((day["date"], points, key,
                        bool(have and have.get("key") == key and have.get("complete") and not have.get("retry"))))
    bbox = practical.osm_bbox(days)
    osm = (bbox, read_json(CACHE / "osm" / f"{slug}.json", {}).get("key") == practical.osm_key(bbox)) if bbox else None
    drive = [(name, target, key, key in routes) for name, target in (practical.drive_targets(days).items() if home else ())
             for key in [practical.drive_key(home, target, name == "back")]]
    known = read_json(geo.GEOCODE_CACHE, {})
    spots = {geo.names_key(p[0], p[1]): (p[0], p[1]) for day in days for p in (practical._points(day)[0], practical._points(day)[-1])}
    names = [(key, lat, lng, "ru" in (known.get(key) or {})) for key, (lat, lng) in spots.items()]  # ёсць на ўсіх мовах сайта
    top = practical.climate_point(days)
    climate = []
    if top:
        cell = practical.climate_key(top[0], top[1])
        cached = read_json(CACHE / "climate" / f"{cell}.json")
        # ёсць — калі спампавана ўсё; `retry` — мінулы раз прыйшоў няпоўны адказ і засталіся ранейшыя поўныя звесткі
        climate.append((cell, top[0], top[1], practical.climate_full(cached) and not cached.get("retry")))
    return {"weather": weather, "osm": osm, "routes": drive, "names": names,
            "rates": [(day, day in usd) for day in sorted(price_days(h, start))], "climate": climate}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="hike fetch")
    ap.add_argument("--refresh", action="store_true", help="перакачаць усё нанова")
    args = ap.parse_args(argv)
    if not CONTENT.is_dir():
        raise SystemExit(f"! Тэчка з апісаннямі паходаў не знойдзена: {CONTENT}.")
    cfg = load_config()
    offset = cfg.get("default_utc_offset", 0)
    home = practical.home_point(cfg.get("home"))
    routes_path = CACHE / "routes.json"
    routes = dict(read_json(routes_path, {}).get("routes") or {})
    routes_saved = dict(routes)
    osm_todo: list[tuple[str, list[float]]] = []
    climate_todo: dict[str, tuple[float, float]] = {}  # квадрат → пункт; адзін запыт на квадрат
    places = hints = drives = 0
    session = requests.Session()
    session.headers["User-Agent"] = "my-hiking-site (personal hiking map)"
    today = date.today().isoformat()
    silent = io.StringIO()  # папярэджанні пра туры і даты паказвае `hike build`

    fetched = failed = partial = 0
    usd_known = read_json(CACHE / "rates.json", {}).get("USD") or {}
    rate_days: set[str] = set()
    for path in sorted(CONTENT.glob("*.yaml")):
        slug, h = path.stem, load_yaml(path)
        if h.get("hidden"):
            continue
        with contextlib.redirect_stdout(silent):
            days = practical.raw_days(build.unique_tours(slug, h.get("komoot")), offset)
            start, _ = build.hike_dates(slug, h.get("date"), h.get("end"), days)
        need = needs(slug, h, days, start, home, routes, usd_known)

        # --- надвор'е па днях трэку
        cache_path = CACHE / "weather" / f"{slug}.json"
        cached = read_json(cache_path, {}).get("days") or {}
        # Запісваюцца толькі атрыманыя адказы: дзень, тура якога цяпер няма ў кэшы, застаецца — адсутнасць
        # крыніцы не падстава сціраць спампаванае (ці адносіцца яно да трэку, правярае зборка па ключы).
        fresh = dict(cached)
        for day_date, points, key, have in need["weather"]:
            if have and not args.refresh:
                continue
            try:
                weather = fetch_day_weather(session, points)
            except ERRORS as exc:
                failed += 1
                print(f"  ! {slug}, {day_date}: надвор'е не атрымана — {exc}")
                continue  # ранейшыя звесткі застаюцца
            old = cached.get(day_date) or {}
            if not weather["complete"] and old.get("key") == key and old.get("complete"):
                # Няпоўны адказ не замяняе поўныя звесткі для таго ж трэку (напрыклад, пры --refresh): яны
                # застаюцца, а дзень пазначаецца, каб наступны `hike fetch` паспрабаваў абнавіць яго зноў.
                fresh[day_date] = {**old, "retry": True}
                partial += 1
                print(f"  ! {slug}, {day_date}: надвор'е прыйшло няпоўным — застаюцца ранейшыя поўныя звесткі")
                continue
            fresh[day_date] = {"key": key, "fetched": today, **weather}
            fetched += 1
            partial += not weather["complete"]
            note = "" if weather["complete"] else " (няпоўныя звесткі — паспрабуйце пазней)"
            print(f"  ↓ {day_date}  {slug}: надвор'е для {len(points)} пунктаў{note}")
            time.sleep(0.2)
        if fresh != cached:
            write_json(cache_path, {"source": "Open-Meteo Historical Weather API", "days": fresh})

        # --- падказкі OpenStreetMap у вобласці трэку (самі запыты — пасля цыкла, па некалькі паходаў разам)
        if need["osm"] and (args.refresh or not need["osm"][1]):
            osm_todo.append((slug, need["osm"][0]))

        # --- дарога на машыне ад home да старту (і ад далёкага фінішу)
        for name, target, key, have in need["routes"]:
            back = name == "back"
            if have and not args.refresh:
                continue
            try:
                route = fetch_route(session, home, target, back)
            except ERRORS as exc:
                route = None
                print(f"  ! {slug}: дарога на машыне не разлічана — {exc}")
            if route is None:
                failed += 1
                continue
            routes[key] = route
            drives += 1
            print(f"  ↓ {slug}: на машыне {'ад фінішу' if back else 'да старту'} {route['km']} км, {route['minutes']} хв")
            time.sleep(1.0)
        if routes != routes_saved:  # адразу: перарваны запуск не губляе ўжо разлічанае
            write_json(routes_path, {"source": "OSRM demo server, OpenStreetMap data; car profile", "routes": routes})
            routes_saved = dict(routes)

        # --- населеныя пункты старту і фінішу кожнага дня (назвы не мяняюцца — --refresh іх не перапытвае)
        for key, lat, lng, have in need["names"]:
            if have:
                continue
            if geo.place_names(lat, lng) is None:
                failed += 1
            else:
                places += 1

        # --- даты цэн у «Як дабрацца»
        rate_days |= {day for day, _ in need["rates"]}

        # --- звычайнае надвор'е па месяцах (самі запыты — пасля цыкла, з паўзамі)
        for cell, lat, lng, have in need["climate"]:
            if args.refresh or not have:
                climate_todo.setdefault(cell, (lat, lng))

    # --- падказкі OpenStreetMap
    for i in range(0, len(osm_todo), OSM_BATCH):
        batch = osm_todo[i:i + OSM_BATCH]
        if i:
            time.sleep(5)
        try:
            items = fetch_osm(session, [bbox for _, bbox in batch])
        except ERRORS as exc:
            failed += len(batch)
            print(f"  ! OpenStreetMap не адказаў (паходаў: {len(batch)}) — {exc}")
            continue
        for slug, bbox in batch:
            inside = [item for item in items if practical.osm_inside(item, bbox)]
            write_json(CACHE / "osm" / f"{slug}.json", {"source": "OpenStreetMap contributors, via Overpass API",
                                                        "key": practical.osm_key(bbox), "fetched": today, "items": inside})
            hints += 1
            print(f"  ↓ {slug}: OpenStreetMap — {len(inside)} аб'ектаў у вобласці трэку")

    # --- звычайнае надвор'е па месяцах: адзін запыт на квадрат, з паўзамі (ліміт бясплатнага доступу)
    climates = 0
    for i, (cell, (lat, lng)) in enumerate(sorted(climate_todo.items())):
        if i:
            time.sleep(CLIMATE_PAUSE_S)
        try:
            found = fetch_climate(session, lat, lng)
        except ERRORS as exc:
            failed += 1
            print(f"  ! звычайнае надвор'е {cell} не атрымана — {exc}")
            if "429" in str(exc):  # ліміт на гадзіну або суткі: далей пытаць няма сэнсу
                failed += len(climate_todo) - i - 1
                print("  ! ліміт запытаў Open-Meteo — запусціце `hike fetch` пазней, спампуецца тое, чаго няма")
                break
            continue
        path = CACHE / "climate" / f"{cell}.json"
        old = read_json(path)
        if not practical.climate_full(found):
            partial += 1
            if practical.climate_full(old):
                # Няпоўны адказ не замяняе поўныя звесткі: яны застаюцца, а квадрат пазначаецца для паўтору.
                write_json(path, {**old, "retry": True}, compact=True)
                print(f"  ! {cell}: звычайнае надвор'е прыйшло няпоўным — застаюцца ранейшыя поўныя звесткі")
                continue
        write_json(path, {**found, "fetched": today}, compact=True)
        climates += 1
        note = "" if practical.climate_full(found) else " (няпоўныя звесткі — паспрабуйце пазней)"
        print(f"  ↓ {cell}: звычайнае надвор'е за {practical.CLIMATE_YEARS[0]}–{practical.CLIMATE_YEARS[1]}{note}")

    # --- курс лары на даты цэн
    rates_path = CACHE / "rates.json"
    rates = read_json(rates_path, {})
    usd = dict(rates.get("USD") or {})
    got = 0
    for day in sorted(rate_days if args.refresh else rate_days - set(usd)):
        try:
            rate = fetch_rate(session, day)
        except ERRORS as exc:
            rate = None
            print(f"  ! курс на {day} не атрыманы — {exc}")
        if rate is None:
            failed += 1
            continue
        usd[day] = rate
        got += 1
        print(f"  ↓ {day}  курс Нацбанка Грузіі: {rate} ₾ за 1 $")
        time.sleep(0.2)
    if usd != (rates.get("USD") or {}):
        write_json(rates_path, {"source": "National Bank of Georgia, official rate, GEL per 1 USD", "USD": usd})

    print(f"Надвор'е: спампавана дзён {fetched}. Курсаў: {got}. OpenStreetMap: паходаў {hints}. "
          f"Маршрутаў на машыне: {drives}. Назваў месцаў: {places}. Звычайнае надвор'е: квадратаў {climates}."
          + (f" Не атрымана: {failed}." if failed else "") + (f" Няпоўных адказаў: {partial}." if partial else "")
          + " Гатова → cache/. Далей: hike build")
    if failed or partial:
        # Атрыманае ўжо захавана, ранейшыя звесткі не страчаны. Код выхаду 1 — каб скрыпт або планавальнік
        # бачыў, што абнаўленне не скончана і `hike fetch` трэба паўтарыць.
        raise SystemExit(1)


if __name__ == "__main__":
    main()

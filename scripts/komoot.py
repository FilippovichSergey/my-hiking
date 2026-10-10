"""Спампоўвае твае запісаныя туры з Komoot у cache/komoot/.

Запуск:  hike komoot            (спытае email і пароль)
         hike komoot --refresh  (перакачаць усе трэкі нанова)

Пароль выкарыстоўваецца толькі для гэтага запыту і нідзе не захоўваецца.
Email можна задаць загадзя праз зменную асяроддзя KOMOOT_EMAIL.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from urllib.parse import quote

import requests

from common import CACHE, has_track, load_config, read_json, write_json

API = "https://api.komoot.de"
KOMOOT_DIR = CACHE / "komoot"
TOUR_FIELDS = (
    "id", "name", "sport", "type", "status", "date", "changed_at", "distance", "duration",
    "time_in_motion", "elevation_up", "elevation_down", "start_point",
)


def login(session: requests.Session, email: str, password: str):
    r = session.get(f"{API}/v006/account/email/{quote(email, safe='@')}/", auth=(email, password), timeout=30)
    if r.status_code in (401, 403):
        sys.exit("Komoot адхіліў уваход: праверце email і пароль.")
    r.raise_for_status()
    data = r.json()
    return data["username"], data["password"], data.get("user", {}).get("displayname", "")


def list_recorded_tours(session: requests.Session, user_id: str, auth) -> list[dict]:
    url = f"{API}/v007/users/{user_id}/tours/?type=tour_recorded&limit=100"
    tours: list[dict] = []
    while url:
        r = session.get(url, auth=auth, timeout=60)
        r.raise_for_status()
        j = r.json()
        tours += j.get("_embedded", {}).get("tours", [])
        url = j.get("_links", {}).get("next", {}).get("href")
    return tours


def fetch_coordinates(session: requests.Session, tour_id, auth) -> list[list[float]]:
    r = session.get(f"{API}/v007/tours/{tour_id}?_embedded=coordinates", auth=auth, timeout=60)
    r.raise_for_status()
    items = r.json().get("_embedded", {}).get("coordinates", {}).get("items", [])
    coords = []
    for p in items:
        if isinstance(p, dict):
            coords.append([p["lat"], p["lng"], p.get("alt"), p.get("t")])
        else:  # на выпадак, калі API аддасць масівы
            coords.append(list(p) + [None] * (4 - len(p)))
    return coords


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="hike komoot")
    ap.add_argument("--refresh", action="store_true", help="перакачаць трэкі ўсіх тураў")
    args = ap.parse_args(argv)
    cfg = load_config()
    sports = set(cfg.get("komoot_sports", ["hike"]))

    email = os.environ.get("KOMOOT_EMAIL") or input("Komoot email: ").strip()
    password = getpass.getpass("Komoot пароль (пры ўводзе не адлюстроўваецца): ")

    session = requests.Session()
    session.headers["User-Agent"] = "my-hiking-site/0.1 (personal hiking map)"
    user_id, token, display = login(session, email, password)
    del password
    auth = (user_id, token)
    print(f"Увайшлі як {display or user_id}")

    tours = list_recorded_tours(session, user_id, auth)
    meta = [{k: t.get(k) for k in TOUR_FIELDS} for t in tours]
    meta.sort(key=lambda t: t["date"] or "")
    write_json(KOMOOT_DIR / "tours.json", meta)

    by_sport: dict[str, int] = {}
    for t in meta:
        by_sport[t["sport"]] = by_sport.get(t["sport"], 0) + 1
    print(f"Запісаных тураў: {len(meta)}  " + ", ".join(f"{s}: {n}" for s, n in sorted(by_sport.items())))

    wanted = [t for t in meta if t["sport"] in sports]
    fetched, failed = 0, 0
    for t in wanted:
        path = KOMOOT_DIR / "tours" / f"{t['id']}.json"
        cached = read_json(path)
        if cached and not args.refresh and not cached.get("incomplete") \
                and cached.get("changed_at") == t.get("changed_at"):
            continue
        coords = fetch_coordinates(session, t["id"], auth)
        time.sleep(0.3)
        if not has_track(coords):
            # Няпоўны адказ (без каардынат) — няўдалае абнаўленне: добры трэк у кэшы ён не замяняе.
            # Метка `incomplete` прымушае наступны запуск паспрабаваць зноў, нават калі changed_at той самы.
            failed += 1
            kept = bool(cached) and has_track(cached.get("coords"))
            print(f"  ! {t['date'][:10]}  {t['name']}: Komoot аддаў трэк без каардынат — "
                  + ("у кэшы застаецца ранейшы" if kept else "тур застаецца без трэку"))
            write_json(path, {**(cached if kept else {**t, "coords": coords}), "incomplete": True}, compact=True)
            continue
        write_json(path, {**t, "coords": coords}, compact=True)
        fetched += 1
        print(f"  ↓ {t['date'][:10]}  {t['name']}  ({len(coords)} кропак)")
    print(f"Хайкінг-тураў: {len(wanted)}, спампавана трэкаў: {fetched}"
          + (f", без каардынат: {failed}" if failed else "") + ". Гатова → cache/komoot/")


if __name__ == "__main__":
    main()

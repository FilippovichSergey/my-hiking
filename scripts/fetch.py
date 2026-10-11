"""Спампоўвае знешнія даныя для практычнай часткі ў cache/ (сайт збірае іх `hike build` ужо без сеткі):

  надвор'е падчас паходаў — архіў Open-Meteo па гадзінах   → cache/weather/<паход>.json
  курс лары да даляра на даты цэн — Нацбанк Грузіі          → cache/rates.json

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
import practical
from common import CACHE, CONTENT, iso_date, load_config, load_yaml, read_json, write_json

API_WEATHER = "https://archive-api.open-meteo.com/v1/archive"
API_RATES = "https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/"
ERRORS = (requests.RequestException, RuntimeError, ValueError, KeyError, IndexError, TypeError)


def get_json(session: requests.Session, url: str, params: dict):
    r = session.get(url, params=params, timeout=60)
    if r.status_code == 400:  # Open-Meteo тлумачыць памылку запыту ў целе адказу
        raise RuntimeError(r.json().get("reason") or "запыт адхілены")
    r.raise_for_status()
    return r.json()


def fetch_day_weather(session: requests.Session, points: list[dict]) -> dict:
    """Надвор'е аднаго дня паходу: адзін запыт на ўсе яго пункты (старт, найвышэйшы пункт, фініш)."""
    days = [datetime.fromtimestamp(p["ts"], timezone.utc).date() for p in points]
    params = {
        "latitude": ",".join(str(p["lat"]) for p in points), "longitude": ",".join(str(p["lng"]) for p in points),
        "start_date": min(days).isoformat(), "end_date": max(days).isoformat(),
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


def fetch_rate(session: requests.Session, day: str) -> float | None:
    """Афіцыйны курс: колькі лары за 1 даляр ЗША на дату (у выхадныя дзейнічае апошні ўсталяваны курс)."""
    data = get_json(session, API_RATES, {"currencies": "USD", "date": day})
    currency = data[0]["currencies"][0]
    rate = float(currency["rate"]) / float(currency.get("quantity") or 1)
    return round(rate, 4) if math.isfinite(rate) and rate > 0 else None


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="hike fetch")
    ap.add_argument("--refresh", action="store_true", help="перакачаць надвор'е і курсы нанова")
    args = ap.parse_args(argv)
    if not CONTENT.is_dir():
        raise SystemExit(f"! Тэчка з апісаннямі паходаў не знойдзена: {CONTENT}.")
    offset = load_config().get("default_utc_offset", 0)
    session = requests.Session()
    session.headers["User-Agent"] = "my-hiking-site (personal hiking map)"
    today = date.today().isoformat()
    silent = io.StringIO()  # папярэджанні пра туры і даты паказвае `hike build`

    fetched = failed = 0
    price_days: set[str] = set()
    for path in sorted(CONTENT.glob("*.yaml")):
        slug, h = path.stem, load_yaml(path)
        if h.get("hidden"):
            continue
        with contextlib.redirect_stdout(silent):
            days = practical.raw_days(build.unique_tours(slug, h.get("komoot")), offset)
            start, _ = build.hike_dates(slug, h.get("date"), h.get("end"), days)

        # --- надвор'е па днях трэку
        cache_path = CACHE / "weather" / f"{slug}.json"
        cached = read_json(cache_path, {}).get("days") or {}
        fresh = {}
        for day in days:
            points = practical.weather_points(day)
            if not points:
                continue  # у трэку няма часу (імпартаваны GPX): невядома, калі аўтар быў у пунктах
            key, have = practical.weather_key(points), cached.get(day["date"])
            if have and have.get("key") == key and have.get("complete") and not args.refresh:
                fresh[day["date"]] = have
                continue
            try:
                weather = fetch_day_weather(session, points)
            except ERRORS as exc:
                failed += 1
                print(f"  ! {slug}, {day['date']}: надвор'е не атрымана — {exc}")
                if have and have.get("key") == key:
                    fresh[day["date"]] = have  # ранейшыя (няпоўныя) звесткі лепш, чым нічога
                continue
            fresh[day["date"]] = {"key": key, "fetched": today, **weather}
            fetched += 1
            note = "" if weather["complete"] else " (няпоўныя звесткі — паспрабуйце пазней)"
            print(f"  ↓ {day['date']}  {slug}: надвор'е для {len(points)} пунктаў{note}")
            time.sleep(0.2)
        if fresh != cached:
            write_json(cache_path, {"source": "Open-Meteo Historical Weather API", "days": fresh})

        # --- даты цэн у «Як дабрацца»
        block = h.get("practical") if isinstance(h.get("practical"), dict) else {}
        ways = block.get("getting_there") if isinstance(block.get("getting_there"), list) else []
        for way in ways:
            if isinstance(way, dict) and way.get("price") is not None:
                day = (iso_date(way["date"]) if way.get("date") else None) or start
                if day:
                    price_days.add(day)

    # --- курс лары на даты цэн
    rates_path = CACHE / "rates.json"
    rates = read_json(rates_path, {})
    usd = dict(rates.get("USD") or {})
    got = 0
    for day in sorted(price_days if args.refresh else price_days - set(usd)):
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

    print(f"Надвор'е: спампавана дзён {fetched}. Курсаў: {got}." + (f" Не атрымана: {failed}." if failed else "")
          + " Гатова → cache/weather, cache/rates.json. Далей: hike build")


if __name__ == "__main__":
    main()

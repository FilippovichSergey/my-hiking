"""Геаметрыя трэкаў і вызначэнне рэгіёна па каардынатах (з кэшам)."""
from __future__ import annotations

import math
import time

import requests

from common import CACHE, read_json, write_json

# Рэгіёны Грузіі па кодзе ISO 3166-2 (Nominatim часта не мае беларускіх назваў): беларуская, англійская, руская.
REGIONS = {
    "GE-AB": ("Абхазія", "Abkhazia", "Абхазия"),
    "GE-AJ": ("Аджарыя", "Adjara", "Аджария"),
    "GE-GU": ("Гурыя", "Guria", "Гурия"),
    "GE-IM": ("Імерэці", "Imereti", "Имеретия"),
    "GE-KA": ("Кахеці", "Kakheti", "Кахетия"),
    "GE-KK": ("Квема-Картлі", "Kvemo Kartli", "Квемо-Картли"),
    "GE-MM": ("Мцхета-Мтыянеці", "Mtskheta-Mtianeti", "Мцхета-Мтианети"),
    "GE-RL": ("Рача-Лечхумі і Квема-Сванеці", "Racha-Lechkhumi and Kvemo Svaneti", "Рача-Лечхуми и Квемо-Сванети"),
    "GE-SJ": ("Самцхе-Джавахеці", "Samtskhe-Javakheti", "Самцхе-Джавахети"),
    "GE-SK": ("Шыда-Картлі", "Shida Kartli", "Шида-Картли"),
    "GE-SZ": ("Самегрэла-Зема-Сванеці", "Samegrelo-Zemo Svaneti", "Самегрело-Земо Сванети"),
    "GE-TB": ("Тбілісі", "Tbilisi", "Тбилиси"),
}


def region_with_ru(region: dict) -> dict:
    """Рэгіён з рускай назвай: калі яе не напісалі ў файле паходу, яна бярэцца з табліцы па англійскай або беларускай."""
    if region.get("ru"):
        return region
    ru = next((r for be, en, r in REGIONS.values() if region.get("en") == en or region.get("be") == be), "")
    return {**region, "ru": ru} if ru else region
GEOCODE_CACHE = CACHE / "geocode.json"
_last_request = 0.0


def _nominatim(lat: float, lng: float, lang: str, zoom: int = 5) -> dict:
    global _last_request
    wait = 1.1 - (time.time() - _last_request)  # ліміт Nominatim: 1 запыт у секунду
    if wait > 0:
        time.sleep(wait)
    r = requests.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={"lat": lat, "lon": lng, "zoom": zoom, "format": "jsonv2", "addressdetails": 1, "accept-language": lang},
        headers={"User-Agent": "my-hiking-site/0.1 (personal hiking map)"},
        timeout=30,
    )
    _last_request = time.time()
    r.raise_for_status()
    return r.json().get("address", {})


def region_for_point(lat: float, lng: float) -> dict:
    key = f"{lat:.2f},{lng:.2f}"
    cache = read_json(GEOCODE_CACHE, {})
    if key not in cache:
        try:
            addr_en = _nominatim(lat, lng, "en")
            code = addr_en.get("ISO3166-2-lvl4", "")
            if code in REGIONS:
                be, en, ru = REGIONS[code]
            else:
                addr_be = _nominatim(lat, lng, "be")
                addr_ru = _nominatim(lat, lng, "ru")
                en = addr_en.get("state") or addr_en.get("country", "")
                be = addr_be.get("state") or addr_be.get("country", "") or en
                ru = addr_ru.get("state") or addr_ru.get("country", "") or en
        except requests.RequestException as exc:
            print(f"  ! рэгіён для {key} не вызначаны: {exc}")
            return {"be": "", "en": "", "ru": ""}
        cache[key] = {"be": be, "en": en, "ru": ru}
        write_json(GEOCODE_CACHE, cache)
    found = region_with_ru(cache[key])  # запісы кэша, зробленыя да рускай мовы, — без ru
    return {"be": found["be"], "en": found["en"], "ru": found.get("ru", "")}


def place_name(lat: float, lng: float) -> str:
    """Назва бліжэйшага населенага пункта (для паходаў без назвы тэчкі)."""
    key = f"place:{lat:.3f},{lng:.3f}"
    cache = read_json(GEOCODE_CACHE, {})
    if key not in cache:
        try:
            r = requests.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"lat": lat, "lon": lng, "zoom": 14, "format": "jsonv2", "addressdetails": 1,
                        "accept-language": "be,ru,en"},
                headers={"User-Agent": "my-hiking-site/0.1 (personal hiking map)"},
                timeout=30,
            )
            r.raise_for_status()
            addr = r.json().get("address", {})
            time.sleep(1.1)
        except requests.RequestException as exc:
            print(f"  ! назва месца для {key} не вызначана: {exc}")
            return ""
        cache[key] = next((addr[k] for k in ("village", "hamlet", "town", "city", "suburb", "municipality",
                                              "county") if addr.get(k)), "")
        write_json(GEOCODE_CACHE, cache)
    return cache[key]


_SETTLEMENT = ("village", "hamlet", "town", "city", "suburb")  # муніцыпалітэт — не населены пункт: рэгіён ужо паказаны


def names_key(lat: float, lng: float) -> str:
    """Ключ назвы населенага пункта ў кэшы геакадавання (каля 100 м дакладнасці)."""
    return f"names:{lat:.3f},{lng:.3f}"


def place_names(lat: float, lng: float) -> dict | None:
    """Бліжэйшы населены пункт {be, en, ru} — для старту і фінішу. Патрэбна сетка; вынік у кэшы.

    Беларускай назвы ў OpenStreetMap часта няма — тады бярэцца англійская (а не руская). Руская назва — толькі
    сапраўды руская: калі яе няма, ru пусты і сайт паказвае назву па-беларуску або па-англійску.
    Запіс кэша без ru (зроблены да рускай мовы) дапаўняецца адным запытам. None — сэрвіс не адказаў.
    """
    key = names_key(lat, lng)
    # Пытаецца сам пункт ключа, а не дакладныя каардынаты: розныя пункты адной клеткі (~100 м) маглі б даць
    # розныя населеныя пункты, і назвы на розных мовах, спампаваныя ў розны час, не супалі б.
    lat, lng = float(f"{lat:.3f}"), float(f"{lng:.3f}")
    cache = read_json(GEOCODE_CACHE, {})
    entry = cache.get(key)
    if entry is None or "ru" not in entry:
        asked = (["be,en", "en"] if entry is None else []) + ["ru,en"]
        try:
            found = [str(next((addr[k] for k in _SETTLEMENT if addr.get(k)), "")).strip()
                     for addr in (_nominatim(lat, lng, lang, 14) for lang in asked)]
        except (requests.RequestException, ValueError, AttributeError) as exc:
            print(f"  ! назва месца для {lat:.3f}, {lng:.3f} не вызначана: {exc}")
            return None
        if entry is None:
            entry = {"be": found[0] or found[1], "en": found[1] or found[0]}
        ru = found[-1]
        entry = {**entry, "ru": ru if ru not in (entry["en"], entry["be"]) or ru == "" else ""}
        cache = read_json(GEOCODE_CACHE, {})
        cache[key] = entry
        write_json(GEOCODE_CACHE, cache)
    return entry


# --- Геаметрыя ---------------------------------------------------------------

EARTH_R = 6371008.8


def haversine(lat1, lng1, lat2, lng2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def simplify(points: list[tuple], tolerance_m: float, use_alt: bool = True) -> list[int]:
    """Дуглас-Пекер у лакальнай праекцыі (метры); з вышынёй, каб не губляць профіль.

    points: [(lat, lng, alt), ...]; вяртае індэксы кропак, якія застаюцца.
    """
    n = len(points)
    if n <= 2:
        return list(range(n))
    lat0 = math.radians(sum(p[0] for p in points) / n)
    k = math.pi / 180 * EARTH_R
    xyz = [(p[1] * k * math.cos(lat0), p[0] * k, (p[2] or 0) if use_alt else 0) for p in points]

    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    tol2 = tolerance_m ** 2
    while stack:
        i, j = stack.pop()
        ax, ay, az = xyz[i]
        bx, by, bz = xyz[j]
        dx, dy, dz = bx - ax, by - ay, bz - az
        seg2 = dx * dx + dy * dy + dz * dz
        best, best_d2 = -1, tol2
        for m in range(i + 1, j):
            px, py, pz = xyz[m]
            if seg2 == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2 + (pz - az) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy + (pz - az) * dz) / seg2))
                d2 = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2 + (pz - az - t * dz) ** 2
            if d2 > best_d2:
                best, best_d2 = m, d2
        if best >= 0:
            keep[best] = True
            stack += [(i, best), (best, j)]
    return [i for i, k_ in enumerate(keep) if k_]

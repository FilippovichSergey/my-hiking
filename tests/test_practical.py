"""Тэсты практычнай часткі: разлікі па трэку і блок `practical:` (без сеткі, усё ў часовых тэчках).

Запуск з кораня праекта:  python -B -m unittest discover -s tests -q
"""
import hashlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import yaml  # noqa: E402
from PIL import Image  # noqa: E402

import build  # noqa: E402
import common  # noqa: E402
import fetch  # noqa: E402
import geo  # noqa: E402
import komoot  # noqa: E402
import pages  # noqa: E402
import practical  # noqa: E402
import scaffold  # noqa: E402
import status  # noqa: E402
import youtube  # noqa: E402

TZ = timezone(timedelta(hours=4))
CFG = {"site_photos_subfolders": ["Сайт"], "default_utc_offset": 4, "komoot_sports": ["hike"],
       "photo": {"max_size": 400, "quality": 70, "thumb_size": 100, "thumb_quality": 60},
       "home": {"name": {"be": "Батумі, парк Качынскіх", "en": "Batumi, Kaczyński Park"}, "point": [41.62692, 41.59822]}}
M = 1 / 111320  # градусаў шыраты ў метры


def path(*corners, step=20.0):
    """Ломаная праз вуглы [(поўнач_м, усход_м), …] → пункты (lat, lng) прыкладна праз кожныя `step` метраў."""
    pts = []
    for (n0, e0), (n1, e1) in zip(corners, corners[1:]):
        count = max(1, round(((n1 - n0) ** 2 + (e1 - e0) ** 2) ** 0.5 / step))
        pts += [(n0 + (n1 - n0) * i / count, e0 + (e1 - e0) * i / count) for i in range(count)]
    pts.append(corners[-1])
    return [(41.0 + n * M, 42.0 + e * M / 0.7547) for n, e in pts]  # cos(41°) ≈ 0,7547


SQUARE = [(0, 0), (1000, 0), (1000, 1000), (0, 1000), (0, 0)]


def coords(points, minutes=240.0, climb=400.0):
    """Пункты тура Komoot [lat, lng, alt, t_мс]: роўны ход, пад'ём у першай палове і спуск у другой."""
    n = len(points) - 1
    return [[lat, lng, round(1000 + climb * (1 - abs(2 * i / n - 1)), 1), round(i / n * minutes * 60000)]
            for i, (lat, lng) in enumerate(points)]


class TestRouteMath(unittest.TestCase):
    def test_route_types_by_shape(self):
        shapes = {
            "loop": path(*SQUARE),
            "out_and_back": path((0, 0), (2000, 0), (0, 0)),
            "point_to_point": path((0, 0), (3000, 0)),
            "mixed": path((0, 0), (1000, 0), (1500, 0), (1500, 500), (1000, 500), (1000, 0), (0, 0)),  # ножка 1 км + кола 2 км
            "loop with a short shared approach": path((0, 0), (300, 0), (1300, 0), (1300, 1000), (300, 1000), (300, 0), (0, 0)),
        }
        got = {name: practical.route_type(pts) for name, pts in shapes.items()}
        self.assertEqual(got, {"loop": "loop", "out_and_back": "out_and_back", "point_to_point": "point_to_point",
                               "mixed": "mixed", "loop with a short shared approach": "loop"})
        self.assertIsNone(practical.route_type(path((0, 0), (100, 0))), "занадта кароткі трэк")
        self.assertIsNone(practical.route_type([(41.0, 42.0)]))
        self.assertEqual(practical.route_type(path((0, 0), (600, 0))), "point_to_point", "кароткі шлях у адзін бок — не кола")
        self.assertAlmostEqual(practical.overlap_share(shapes["out_and_back"]), 1.0, delta=0.3)
        self.assertLess(practical.overlap_share(shapes["loop"]), 0.05)

    def test_sun_times_match_reference(self):
        # Open-Meteo для 41.85, 42.33 на 12.07.2026 (UTC+4): узыход 05:46, заход 20:45
        rise, set_ = practical.sun_times(date(2026, 7, 12), 41.85, 42.33, TZ)
        for got, want in ((rise, "05:46"), (set_, "20:45")):
            self.assertLessEqual(abs(practical._minutes(got) - practical._minutes(want)), 3, (got, want))
        self.assertIsNone(practical.sun_times(date(2026, 6, 21), 78.0, 15.0, TZ), "палярны дзень")


class PracticalProject(unittest.TestCase):
    """Часовы праект: кэш, сайт, апісанні паходаў і тэчка з фота."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.photos_root = self.root / "photos"
        self.content = self.root / "content" / "hikes"
        self.content.mkdir(parents=True)
        self.cfg = {**CFG, "photos_root": str(self.photos_root)}
        self._saved = (build.CACHE, build.DOCS, build.CONTENT, build.load_config, practical.CACHE,
                       scaffold.CONTENT, scaffold.CACHE, scaffold.load_config, geo.GEOCODE_CACHE)
        build.CACHE = practical.CACHE = scaffold.CACHE = self.root / "cache"
        geo.GEOCODE_CACHE = self.root / "cache" / "geocode.json"
        build.DOCS = self.root / "docs"
        build.CONTENT = scaffold.CONTENT = self.content
        build.load_config = scaffold.load_config = lambda: self.cfg

    def tearDown(self):
        (build.CACHE, build.DOCS, build.CONTENT, build.load_config, practical.CACHE,
         scaffold.CONTENT, scaffold.CACHE, scaffold.load_config, geo.GEOCODE_CACHE) = self._saved
        self.tmp.cleanup()

    def add_tour(self, tid, iso, points, **stats):
        tour = {"id": tid, "name": f"t{tid}", "date": iso, "status": "public", "distance": 4000, "elevation_up": 400,
                "elevation_down": 400, "duration": 14400, "time_in_motion": 10800, "coords": coords(points), **stats}
        common.write_json(build.CACHE / "komoot" / "tours" / f"{tid}.json", tour)

    def photo(self, folder, name, taken):
        exif = Image.Exif()
        exif.get_ifd(0x8769)[36867] = taken
        target = self.photos_root / folder / "Сайт" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (80, 60), (200, 100, 50)).save(target, "JPEG", exif=exif)

    def build(self, argv=()):
        out = io.StringIO()
        with unittest.mock.patch("sys.stdout", out):
            build.main(list(argv))
        index = {h["slug"]: h for h in common.read_json(build.DOCS / "data" / "hikes.json")["hikes"]}
        return index, out.getvalue()


class TestPracticalBuild(PracticalProject):
    def test_facts_from_track(self):
        self.photos_root.mkdir()
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))  # старт 08:00 па мясцовым часе, 4 гадзіны
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\n", encoding="utf-8")
        index, _ = self.build()
        h = index["h"]
        facts = h["facts"]
        self.assertEqual(facts, h["days"][0]["facts"], "для аднадзённага паходу разлік супадае з днём")
        self.assertEqual((facts["type"], facts["start"]["time"], facts["finish"]["time"]), ("loop", "08:00", "12:00"))
        self.assertEqual((facts["start"]["at"], facts["start"]["alt"]), ([42.0, 41.0], 1000))
        self.assertEqual((facts["speed"], facts["stops"], facts["gain"]), (1.3, 3600, 100))  # 4 км за 3 г у руху
        self.assertEqual(facts["sun"]["left"], practical._minutes(facts["sun"]["set"]) - 12 * 60)
        self.assertEqual((facts["steepUp"]["grade"], facts["steepDown"]["grade"]), (20, 20))  # 400 м на 2 км
        self.assertLess(facts["steepUp"]["km"], 2)
        self.assertGreaterEqual(facts["steepDown"]["km"], 1.5)
        self.assertIsNone(h["practical"])

    def test_no_stops_field_when_komoot_has_no_pauses(self):
        self.photos_root.mkdir()
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (3000, 0)), duration=7200, time_in_motion=7200)
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\n", encoding="utf-8")
        facts = self.build()[0]["h"]["facts"]
        self.assertEqual(facts["type"], "point_to_point")
        self.assertNotIn("stops", facts)

    def test_multi_day_types(self):
        self.photos_root.mkdir()
        # паход з начлегам: дзень 1 туды, дзень 2 назад тым жа шляхам
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (3000, 0)))
        self.add_tour(2, "2025-06-02T04:00:00Z", path((3000, 0), (0, 0)))
        (self.content / "trek.yaml").write_text("date: 2025-06-01\ntitle: {be: T}\nkomoot: [1, 2]\n", encoding="utf-8")
        # дзве асобныя вылазкі з розных месцаў: кола і кола
        self.add_tour(3, "2025-07-01T04:00:00Z", path(*SQUARE))
        self.add_tour(4, "2025-07-02T04:00:00Z", path(*[(n + 20000, e) for n, e in SQUARE]))
        (self.content / "base.yaml").write_text("date: 2025-07-01\ntitle: {be: B}\nkomoot: [3, 4]\n", encoding="utf-8")
        index, _ = self.build()
        trek, base = index["trek"], index["base"]
        self.assertEqual([d["facts"]["type"] for d in trek["days"]], ["point_to_point", "point_to_point"])
        self.assertEqual(trek["facts"]["type"], "out_and_back")
        self.assertNotIn("sun", trek["facts"], "светлавы дзень — толькі па днях")
        self.assertEqual((trek["facts"]["start"]["time"], trek["facts"]["finish"]["time"]), ("08:00", "12:00"))
        self.assertEqual(base["facts"]["type"], "loop")
        self.assertIn("day", base["facts"]["steepUp"])

    def practical_hike(self):
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))
        self.photo("20250601_H", "IMG_1.jpg", "2025:06:01 09:00:00")   # праз гадзіну пасля старту — 1 км
        self.photo("20250601_H", "IMG_2.jpg", "2025:06:01 11:00:00")   # 3 км
        self.photo("20250601_H", "IMG_3.jpg", "2025:06:01 20:00:00")   # пасля паходу
        (self.content / "h.yaml").write_text("""\
date: 2025-06-01
title: {be: H}
photos_folder: 20250601_H
komoot: [1]
practical:
  route_type: mixed
  weather_notes: {be: "Туман.", en: "Fog."}
  water:
    drunk: 2
    taken: 3
    refilled: 0.5
    left: 1.5
    sources:
      - {type: spring, photo: IMG_1, water: true, seasonal: true, treat: false, seen: 2025-06-01, note: "Труба."}
      - {type: stream, time: "10:00"}
      - {type: tap, km: 3.5, water: false}
      - {type: well}
  getting_there:
    - {type: taxi, to: "Вёска", time: "1:10", km: 42, price: 80, price_for: car, trip: one_way, date: 2025-06-01, road: "Грунтоўка."}
    - {type: marshrutka, from: "Кеда", time: 25, price: 5, price_for: person}
  parking: "Каля школы."
  places:
    - {type: shelter, name: "Альтанка", photo: IMG_2.jpg, note: "Стол і лаўкі."}
    - {type: viewpoint, at: [41.0045, 42.0030]}
  surface: "Грунт."
  dangers: {be: "Сабакі.", en: "Dogs."}
""", encoding="utf-8")

    def test_practical_block(self):
        self.practical_hike()
        index, out = self.build()
        h = index["h"]
        self.assertNotIn("practical:", out, "правільны блок не дае папярэджанняў")
        self.assertEqual((h["facts"]["type"], h["facts"]["typeBy"]), ("mixed", "author"))
        self.assertEqual(h["days"][0]["facts"]["type"], "loop", "разлік па трэку захоўваецца асобна")
        p = h["practical"]
        self.assertEqual((p["checked"], p["weatherNotes"]), ("2025-06-01", {"be": "Туман.", "en": "Fog."}))
        self.assertEqual(p["notes"], {"parking": {"be": "Каля школы.", "en": ""}, "surface": {"be": "Грунт.", "en": ""},
                                      "dangers": {"be": "Сабакі.", "en": "Dogs."}})

        water = p["water"]
        self.assertEqual((water["drunk"], water["taken"], water["refilled"], water["left"]), (2.0, 3.0, 0.5, 1.5))
        self.assertEqual((water["perHour"], water["per10km"], water["available"]), (0.5, 5.0, None))  # 4 гадзіны, 4 км
        spring, stream, tap, well = water["sources"]
        self.assertEqual((spring["type"], spring["water"], spring["seasonal"], spring["treat"], spring["seen"]),
                         ("spring", True, True, False, "2025-06-01"))
        self.assertEqual((spring["pos"]["km"], spring["pos"]["time"], spring["pos"]["day"]), (1.0, "09:00", 0))
        self.assertEqual(h["photos"][spring["photo"]]["src"].split("/")[-1][:5], "img-1")
        self.assertEqual((stream["pos"]["km"], stream["pos"]["time"], stream["water"]), (2.0, "10:00", None))
        self.assertEqual((tap["pos"]["km"], tap["water"]), (3.5, False))
        self.assertEqual((well["pos"], well["ref"], well["water"]), (None, None, None), "месца не пазначана — без кропкі")

        taxi, bus = p["gettingThere"]
        self.assertEqual((taxi["type"], taxi["from"]["be"], taxi["to"]["be"], taxi["minutes"], taxi["km"], taxi["price"]),
                         ("taxi", "Батумі, парк Качынскіх", "Вёска", 70, 42.0, 80.0))
        self.assertEqual((taxi["priceFor"], taxi["trip"], taxi["date"], taxi["road"]["be"]),
                         ("car", "one_way", "2025-06-01", "Грунтоўка."))
        self.assertEqual((bus["from"]["be"], bus["minutes"], bus["price"], bus["priceFor"], bus["trip"], bus["km"]),
                         ("Кеда", 25, 5.0, "person", None, None))

        shelter, view = p["places"]
        self.assertEqual((shelter["type"], shelter["name"]["be"], shelter["pos"]["km"], shelter["pos"]["time"]),
                         ("shelter", "Альтанка", 3.0, "11:00"))
        self.assertEqual(h["photos"][shelter["photo"]]["src"].split("/")[-1][:5], "img-2")
        self.assertEqual(view["pos"]["at"], [42.003, 41.0045])
        self.assertAlmostEqual(view["pos"]["off"], 252, delta=15)  # 0,003° даўгаты на шыраце 41° — каля 252 м
        self.assertAlmostEqual(view["pos"]["km"], 0.5, delta=0.1)

    def test_bad_values_warn_and_do_not_stop_the_build(self):
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))
        self.photo("20250601_H", "IMG_3.jpg", "2025:06:01 20:00:00")
        (self.content / "h.yaml").write_text("""\
date: 2025-06-01
title: {be: H}
photos_folder: 20250601_H
komoot: [1]
practical:
  route_type: circle
  colour: red
  water:
    drunk: lots
    taken: 3
    sources:
      - {type: geyser, photo: IMG_9}
      - {type: spring, photo: IMG_3}
      - {type: spring, time: "25 гадзін"}
  getting_there:
    - {type: rocket, time: soon, price: -5, price_for: crew, date: "2025-02-31"}
  places:
    - "альтанка"
""", encoding="utf-8")
        index, out = self.build()
        for message in ("route_type: невядомае значэнне «circle»", "невядомыя палі: colour", "water.drunk: чакаецца неадмоўны лік",
                        "water.sources[1].type: невядомае значэнне «geyser»", "фота IMG_9 няма сярод фота паходу",
                        "фота IMG_3 знята па-за часам трэку", "няправільны час time: 25 гадзін",
                        "getting_there[1].type: невядомае значэнне «rocket»", "getting_there.time: чакаюцца хвіліны",
                        "getting_there[1].price: чакаецца неадмоўны лік", "getting_there[1].price_for: невядомае значэнне «crew»",
                        "getting_there[1]: няправільная дата: 2025-02-31", "places[1]: чакаецца запіс з палямі"):
            self.assertIn(message, out)
        h = index["h"]
        self.assertEqual(h["facts"]["type"], "loop", "няправільнае выпраўленне не замяняе разлік")
        p = h["practical"]
        self.assertEqual((p["water"]["drunk"], p["water"]["taken"], p["water"].get("perHour")), (None, 3.0, None),
                         "пустое не роўна нулю")
        self.assertEqual([(s["type"], s["pos"]) for s in p["water"]["sources"]],
                         [("other", None), ("spring", None), ("spring", None)])
        self.assertEqual((p["gettingThere"][0]["type"], p["gettingThere"][0]["price"], p["gettingThere"][0]["date"]),
                         ("other", None, None))
        self.assertEqual(p["places"], [])

    def test_water_balance_and_per_day(self):
        self.photos_root.mkdir()
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (3000, 0)))
        self.add_tour(2, "2025-06-02T04:00:00Z", path((3000, 0), (0, 0)))
        head = "date: 2025-06-01\ntitle: {be: T}\nkomoot: [1, 2]\npractical:\n  water:\n"
        (self.content / "t.yaml").write_text(head + "    drunk: [2, 3]\n    taken: [3, 2]\n    refilled: [0, 2]\n    left: [1, 1]\n",
                                             encoding="utf-8")
        index, out = self.build()
        water = index["t"]["practical"]["water"]
        self.assertEqual((water["days"][1], water["drunk"], water["perHour"]),
                         ({"drunk": 3.0, "taken": 2.0, "refilled": 2.0, "left": 1.0}, 5.0, 0.62))
        self.assertNotIn("праверце лічбы", out)  # узята 5 + папоўнена 2 − засталося 2 = выпіта 5
        # лік за ўвесь паход (не па днях) і баланс, які не сыходзіцца
        (self.content / "t.yaml").write_text(head + "    drunk: 2\n    taken: 6\n    refilled: 0\n    left: 1\n", encoding="utf-8")
        index, out = self.build()
        water = index["t"]["practical"]["water"]
        self.assertEqual((water["days"], water["drunk"], water["perHour"]), ([], 2.0, 0.25))
        self.assertIn("узята + папоўнена − засталося = 5 л, а выпіта 2 л", out)

    def test_r13_3_incomplete_water_list_is_unknown_total(self):
        self.photos_root.mkdir()
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (3000, 0)))
        self.add_tour(2, "2025-06-02T04:00:00Z", path((3000, 0), (0, 0)))
        head = "date: 2025-06-01\ntitle: {be: T}\nkomoot: [1, 2]\npractical:\n  water:\n"
        for value, warns in (("[2]", True), ("[2, null]", False), ("[2, 3, 4]", True)):
            (self.content / "t.yaml").write_text(head + f"    drunk: {value}\n", encoding="utf-8")
            index, out = self.build()
            water = index["t"]["practical"]["water"]
            self.assertEqual(len(water["days"]), 2, value)
            self.assertEqual(water["days"][0]["drunk"], 2.0, value)
            if value == "[2, 3, 4]":  # лішні дзень адкідаецца, першыя два вядомыя
                self.assertEqual((water["drunk"], water["perHour"]), (5.0, 0.62), value)
            else:
                self.assertEqual((water["days"][1]["drunk"], water["drunk"]), (None, None), value)
                self.assertNotIn("perHour", water, value)
                self.assertNotIn("per10km", water, value)
            self.assertEqual("у спісе" in out, warns, value)

    def test_positions_kept_when_tour_cache_is_missing(self):
        self.practical_hike()
        before, _ = self.build()
        (build.CACHE / "komoot" / "tours" / "1.json").unlink()
        after, out = self.build()
        self.assertIn("застаецца апублікаваны раней трэк", out)
        self.assertNotIn("месца не вызначана", out)
        for key in ("facts", "practical", "days"):
            self.assertEqual(after["h"][key], before["h"][key], key)

    def test_draft_template_is_a_valid_practical_block(self):
        (self.photos_root / "20250601_H" / "Сайт").mkdir(parents=True)
        common.write_json(build.CACHE / "komoot" / "tours.json", [])
        common.write_json(build.CACHE / "youtube.json", [])
        with unittest.mock.patch.multiple(scaffold, region_for_point=lambda *a: {"be": "", "en": ""}, place_name=lambda *a: ""), \
                unittest.mock.patch("sys.stdout", io.StringIO()):
            scaffold.main([])
        text = next(self.content.glob("*.yaml")).read_text(encoding="utf-8")
        self.assertNotIn("practical", yaml.safe_load(text), "у чарнавіку блок закаментаваны")
        opened = "\n".join(line[2:] if line.startswith("# ") else line for line in scaffold.PRACTICAL_TEMPLATE[1:])
        block = yaml.safe_load(opened)["practical"]
        out = io.StringIO()
        with unittest.mock.patch("sys.stdout", out):
            parsed, override = practical.parse_practical("h", block, days=[], stats=None, n_days=1, end="2025-06-01",
                                                         manifest={}, photos=[], prev=None, rates={"2025-05-17": 2.5})
        problems = [line for line in out.getvalue().splitlines() if "месца не вызначана" not in line]
        self.assertEqual(problems, [], "шаблон з README і чарнавіка разбіраецца без заўваг")
        self.assertEqual((parsed["gettingThere"][0]["usd"], parsed["gettingThere"][0]["rateDate"]), (32.0, "2025-05-17"))
        self.assertIsNone(override)
        self.assertEqual(set(parsed["notes"]), set(practical.NOTE_KEYS))
        self.assertEqual((parsed["water"]["drunk"], parsed["gettingThere"][0]["minutes"], parsed["places"][0]["type"]),
                         (2.5, 70, "shelter"))


class TestReview140(PracticalProject):
    """Рэгрэсійныя тэсты па review_2026-10-11_v1.4.0.md."""

    def one_day(self, practical_yaml=""):
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))
        (self.content / "h.yaml").write_text(
            "date: 2025-06-01\ntitle: {be: H}\nphotos_folder: 20250601_H\nkomoot: [1]\n" + practical_yaml, encoding="utf-8")

    def test_r13_1_wrong_field_types_warn_and_keep_the_build_going(self):
        (self.photos_root / "20250601_H" / "Сайт").mkdir(parents=True)
        self.add_tour(2, "2025-06-05T04:00:00Z", path((0, 0), (3000, 0)))
        (self.content / "good.yaml").write_text("date: 2025-06-05\ntitle: {be: G}\nkomoot: [2]\n", encoding="utf-8")
        cases = {
            "places: 42": "places: чакаецца спіс",
            "getting_there: 42": "getting_there: чакаецца спіс",
            "water: {sources: 42}": "water.sources: чакаецца спіс",
            "surface: 123": "surface: чакаецца тэкст",
            "weather_notes: [a, b]": "weather_notes: чакаецца тэкст",
            "places: [{type: shelter, name: 5, note: {be: 7}}]": "places[1].name: чакаецца тэкст",
            "getting_there: [{type: taxi, to: 9}]": "getting_there[1].to: чакаецца тэкст",
        }
        for field, message in cases.items():
            self.one_day(f"practical:\n  dangers: Сабакі.\n  {field}\n")
            index, out = self.build()
            self.assertIn(message, out, field)
            self.assertEqual(set(index), {"h", "good"}, field)
            self.assertEqual(index["h"]["practical"]["notes"]["dangers"]["be"], "Сабакі.", "астатнія звесткі захаваныя")
            self.assertEqual(index["h"]["facts"]["type"], "loop", field)

    def test_r13_2_file_time_is_not_a_shooting_time(self):
        folder = self.photos_root / "20250601_H" / "Сайт"
        folder.mkdir(parents=True)
        Image.new("RGB", (80, 60), (10, 200, 10)).save(folder / "NoExif.jpg", "JPEG")
        at_nine = 1748754000  # 2025-06-01 09:00 па мясцовым часе (UTC+4) — унутры часу трэку
        os.utime(folder / "NoExif.jpg", (at_nine, at_nine))
        self.photo("20250601_H", "Real.jpg", "2025:06:01 10:00:00")
        self.one_day("practical:\n  water:\n    sources:\n      - {type: spring, photo: NoExif}\n      - {type: tap, photo: Real}\n")
        index, out = self.build()
        no_exif, real = index["h"]["practical"]["water"]["sources"]
        self.assertIsNone(no_exif["pos"], "час змены файла — не час здымкі")
        self.assertIsNotNone(no_exif["photo"], "фота застаецца ў запісе")
        self.assertIn("у фота NoExif няма часу здымкі (EXIF) — пазначце time, km або at", out)
        self.assertEqual((real["pos"]["km"], real["pos"]["time"]), (2.0, "10:00"))
        manifest = common.read_json(build.CACHE / "photos" / "h.json")
        self.assertEqual((manifest["NoExif.jpg"]["original"], manifest["Real.jpg"]["original"]), (None, "2025-06-01T10:00:00"))

        # маніфест старой версіі (без original): час здымкі дачытваецца, фота не пераціскаюцца
        for entry in manifest.values():
            del entry["original"]
        common.write_json(build.CACHE / "photos" / "h.json", manifest)
        index, out = self.build()
        self.assertNotIn("фота Real.jpg →", out)
        self.assertEqual(index["h"]["practical"]["water"]["sources"][1]["pos"]["km"], 2.0)
        self.assertEqual(common.read_json(build.CACHE / "photos" / "h.json")["Real.jpg"]["original"], "2025-06-01T10:00:00")

        # стары маніфест і недаступная тэчка з фота: месца застаецца з папярэдняга індэкса
        common.write_json(build.CACHE / "photos" / "h.json", manifest)
        self.cfg["photos_root"] = str(self.root / "missing-disk")
        index, out = self.build()
        self.assertEqual(index["h"]["practical"]["water"]["sources"][1]["pos"]["km"], 2.0)
        self.assertIn("час здымкі фота NoExif яшчэ не прачытаны", out)
        self.assertNotIn("фота Real", out)

    def test_r13_4_finish_after_midnight_is_after_sunset(self):
        self.photos_root.mkdir()
        self.add_tour(1, "2025-06-01T18:00:00Z", path(*SQUARE))  # мясцовы старт 22:00, фініш 02:00 наступнага дня
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\n", encoding="utf-8")
        facts = self.build()[0]["h"]["facts"]
        self.assertEqual((facts["start"]["time"], facts["finish"]["time"]), ("22:00", "02:00"))
        after_sunset = 24 * 60 + 2 * 60 - practical._minutes(facts["sun"]["set"])
        self.assertAlmostEqual(facts["sun"]["left"], -after_sunset, delta=1)
        self.assertLess(facts["sun"]["left"], -300)

    def test_r13_5_removed_route_type_override_is_dropped_without_cache(self):
        self.photos_root.mkdir()
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))
        head = "date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\n"
        (self.content / "h.yaml").write_text(head + "practical: {route_type: mixed}\n", encoding="utf-8")
        facts = self.build()[0]["h"]["facts"]
        self.assertEqual((facts["type"], facts["typeBy"], facts["typeCalc"]), ("mixed", "author", "loop"))
        (build.CACHE / "komoot" / "tours" / "1.json").unlink()
        facts = self.build()[0]["h"]["facts"]  # кэша няма, выпраўленне яшчэ ў YAML
        self.assertEqual((facts["type"], facts["typeBy"], facts["typeCalc"]), ("mixed", "author", "loop"))
        (self.content / "h.yaml").write_text(head, encoding="utf-8")
        facts = self.build()[0]["h"]["facts"]
        self.assertEqual(facts["type"], "loop")
        self.assertFalse({"typeBy", "typeCalc"} & set(facts))
        (self.content / "h.yaml").write_text(head + "practical: {route_type: out_and_back}\n", encoding="utf-8")
        facts = self.build()[0]["h"]["facts"]
        self.assertEqual((facts["type"], facts["typeCalc"]), ("out_and_back", "loop"))

    def test_r13_6_wrong_day_time_and_km_get_no_place(self):
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (4000, 0)))
        self.add_tour(2, "2025-06-02T04:00:00Z", path((4000, 0), (0, 0)))
        days = practical.raw_days([1, 2], 4)
        self.assertEqual(len(days), 2)
        bad = {
            "day 3": ({"time": "10:00", "day": 3}, "няправільны дзень day: 3 (дзён у паходзе: 2)"),
            "day text": ({"km": 1, "day": "2"}, "няправільны дзень day: 2"),
            "km beyond": ({"km": 1000, "day": 2}, "кіламетр km: 1000 па-за трэкам дня 2 (0–4.0 км)"),
            "km negative": ({"km": -1}, "па-за трэкам дня 1"),
            "km text": ({"km": "сем"}, "няправільная адлегласць km: сем"),
            "minutes 90": ({"time": "09:90"}, "няправільны час time: 09:90"),
            "hours 25": ({"time": "25:00"}, "няправільны час time: 25:00"),
            "time text": ({"time": "каля дзесяці"}, "няправільны час time"),
            "time out of track": ({"time": "20:00"}, "час 20:00 па-за часам трэку"),
            "time on both days": ({"time": "09:00"}, "час 09:00 ёсць у трэку некалькі разоў — дадайце day"),
            "at text": ({"at": "тут"}, "няправільныя каардынаты at"),
            "at range": ({"at": [141.0, 42.0]}, "няправільныя каардынаты at"),
        }
        for name, (spec, message) in bad.items():
            pos, problem = practical.locate(spec, days, {})
            self.assertIsNone(pos, name)
            self.assertIn(message, problem or "", name)
        good = {
            "day 2 time": ({"time": "10:00", "day": 2}, (1, 2.0)),
            "time on day 1": ({"time": "09:00", "day": 1}, (0, 1.0)),
            "unquoted yaml time": ({"time": 9 * 60 + 30, "day": 1}, (0, 1.5)),   # YAML чытае 9:30 як 570
            "km on day 2": ({"km": 3.5, "day": 2}, (1, 3.5)),
            "km at the finish": ({"km": 4.1}, (0, 4.0)),
        }
        for name, (spec, want) in good.items():
            pos, problem = practical.locate(spec, days, {})
            self.assertIsNone(problem, name)
            self.assertEqual((pos["day"], pos["km"]), want, name)


class TestReview141(PracticalProject):
    """Рэгрэсійныя тэсты па review_2026-10-11_v1.4.1.md."""

    HEAD = "date: 2025-06-01\ntitle: {be: H}\nphotos_folder: 20250601_H\nkomoot: [1]\n"

    def one_day(self, practical_yaml=""):
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))
        (self.content / "h.yaml").write_text(self.HEAD + practical_yaml, encoding="utf-8")

    def strict_index(self):
        def refuse(name):
            raise AssertionError(f"у індэксе не-JSON значэнне {name}")
        return json.loads((build.DOCS / "data" / "hikes.json").read_text(encoding="utf-8"), parse_constant=refuse)

    def test_r14_1_non_finite_numbers_are_skipped_and_index_stays_valid_json(self):
        (self.photos_root / "20250601_H" / "Сайт").mkdir(parents=True)
        self.one_day("""\
practical:
  dangers: Сабакі.
  water: {drunk: .nan, taken: .inf, left: -.inf}
  getting_there:
    - {type: taxi, km: .inf, time: .inf, price: .nan}
""")
        index, out = self.build()
        for message in ("water.drunk: чакаецца неадмоўны лік", "water.taken: чакаецца неадмоўны лік",
                        "water.left: чакаецца неадмоўны лік", "getting_there[1].km: чакаецца неадмоўны лік",
                        "getting_there.time: чакаюцца хвіліны", "getting_there[1].price: чакаецца неадмоўны лік"):
            self.assertIn(message, out)
        p = self.strict_index()["hikes"][0]["practical"]
        self.assertEqual((p["water"]["drunk"], p["water"]["taken"], p["water"]["left"]), (None, None, None))
        way = p["gettingThere"][0]
        self.assertEqual((way["km"], way["minutes"], way["price"]), (None, None, None))
        self.assertEqual(p["notes"]["dangers"]["be"], "Сабакі.")
        # запіс JSON ніколі не пакідае не-JSON значэнняў і не псуе ранейшы файл
        target = self.root / "x.json"
        common.write_json(target, {"ok": 1})
        with self.assertRaises(ValueError):
            common.write_json(target, {"bad": float("nan")})
        self.assertEqual(common.read_json(target), {"ok": 1})

    def test_r14_2_trackless_multi_day_hike_counts_days_from_dates(self):
        self.photos_root.mkdir()
        head = "date: 2025-06-01\nend: 2025-06-02\ntitle: {be: T}\nlocation: [41, 42]\npractical:\n  water:\n"
        results = {}
        for value in ("[2, 3]", "[2]", "[2, null]"):
            (self.content / "t.yaml").write_text(head + f"    drunk: {value}\n", encoding="utf-8")
            index, out = self.build()
            water = index["t"]["practical"]["water"]
            results[value] = ([d["drunk"] for d in water["days"]], water["drunk"], "у спісе" in out)
            self.assertNotIn("perHour", water, "без трэку нормаў няма")
        self.assertEqual(results, {"[2, 3]": ([2.0, 3.0], 5.0, False), "[2]": ([2.0, None], None, True),
                                   "[2, null]": ([2.0, None], None, False)})

    def test_r14_3_ambiguous_photo_name_needs_the_full_file_name(self):
        self.photo("20250601_H", "A.jpg", "2025:06:01 09:00:00")
        self.photo("20250601_H", "A.jpeg", "2025:06:01 11:00:00")
        got = {}
        for name in ("A", "A.jpg", "A.jpeg", "a.JPEG"):
            self.one_day(f"practical:\n  places:\n    - {{type: shelter, photo: {name}}}\n")
            index, out = self.build()
            place = index["h"]["practical"]["places"][0]
            src = index["h"]["photos"][place["photo"]]["src"] if place["photo"] is not None else None
            taken = {"09": "A.jpg", "11": "A.jpeg"}.get((place["pos"] or {}).get("time", "")[:2])
            got[name] = (taken, place["photo"] is not None, "неадназначнае" in out)
            if src:  # кнопка адкрывае тое самае фота, па якім вызначана месца
                manifest = common.read_json(build.CACHE / "photos" / "h.json")
                self.assertEqual(manifest[taken]["name"], src.split("/")[-1][:-5], name)
        self.assertEqual(got, {"A": (None, False, True), "A.jpg": ("A.jpg", True, False),
                               "A.jpeg": ("A.jpeg", True, False), "a.JPEG": ("A.jpeg", True, False)})
        self.assertIn("імя фота A неадназначнае (A.jpeg, A.jpg) — пазначце поўнае імя файла",
                      self.build_with("practical:\n  places:\n    - {type: shelter, photo: A}\n"))

    def build_with(self, practical_yaml):
        self.one_day(practical_yaml)
        return self.build()[1]

    def test_r14_4_thumbnail_fallback_keeps_the_photo_of_the_record(self):
        self.photo("20250601_H", "A.jpg", "2025:06:01 09:00:00")
        self.photo("20250601_H", "C-t.jpg", "2025:06:01 11:00:00")  # сапраўднае фота з імем на «-t»
        self.one_day("practical:\n  places:\n    - {type: shelter, photo: A.jpg}\n  water:\n    sources:\n      - {type: spring, photo: C-t}\n")
        before, _ = self.build()
        place, source = before["h"]["practical"]["places"][0], before["h"]["practical"]["water"]["sources"][0]
        self.assertEqual((place["photo"], source["photo"]), (0, 1))
        (build.DOCS / before["h"]["photos"][0]["src"]).unlink()  # ад фота A засталася толькі мініяцюра
        self.cfg["photos_root"] = str(self.root / "missing-disk")
        after, out = self.build()
        photos = after["h"]["photos"]
        self.assertEqual(photos[0]["src"], photos[0]["thumb"])
        self.assertEqual(photos[1], before["h"]["photos"][1])
        after_place, after_source = after["h"]["practical"]["places"][0], after["h"]["practical"]["water"]["sources"][0]
        self.assertEqual((after_place["photo"], after_place["pos"]), (0, place["pos"]))
        self.assertEqual((after_source["photo"], after_source["pos"]), (1, source["pos"]))

    def test_r14_prev2_image_modify_date_is_not_a_shooting_time(self):
        folder = self.photos_root / "20250601_H" / "Сайт"
        folder.mkdir(parents=True)
        exif = Image.Exif()
        exif[306] = "2025:06:01 09:00:00"  # ModifyDate: час захавання ў рэдактары, а не здымкі
        Image.new("RGB", (80, 60), (10, 10, 200)).save(folder / "Edited.jpg", "JPEG", exif=exif)
        exif = Image.Exif()
        exif.get_ifd(0x8769)[36868] = "2025:06:01 10:00:00"  # DateTimeDigitized без DateTimeOriginal
        Image.new("RGB", (80, 60), (200, 10, 10)).save(folder / "Scan.jpg", "JPEG", exif=exif)
        self.one_day("practical:\n  places:\n    - {type: shelter, photo: Edited}\n    - {type: table, photo: Scan}\n")
        index, out = self.build()
        edited, scan = index["h"]["practical"]["places"]
        self.assertIsNone(edited["pos"])
        self.assertIn("у фота Edited няма часу здымкі (EXIF)", out)
        self.assertEqual((scan["pos"]["km"], scan["pos"]["time"]), (2.0, "10:00"))
        manifest = common.read_json(build.CACHE / "photos" / "h.json")
        self.assertEqual((manifest["Edited.jpg"]["original"], manifest["Edited.jpg"]["taken"]), (None, "2025-06-01T09:00:00"))
        self.assertEqual(manifest["Scan.jpg"]["original"], "2025-06-01T10:00:00")
        # маніфест версіі 1.4.1 з полем shot (у ім магла быць дата змены выявы) перачытваецца
        for entry in manifest.values():
            entry["shot"] = entry.pop("original") or "2025-06-01T09:00:00"
        common.write_json(build.CACHE / "photos" / "h.json", manifest)
        index, out = self.build()
        self.assertIsNone(index["h"]["practical"]["places"][0]["pos"])
        manifest = common.read_json(build.CACHE / "photos" / "h.json")
        self.assertEqual((manifest["Edited.jpg"]["original"], "shot" in manifest["Edited.jpg"]), (None, False))

    def test_r14_prev4_night_tour_finishing_before_sunrise_has_no_daylight_margin(self):
        self.photos_root.mkdir()
        self.add_tour(1, "2025-06-01T20:00:00Z", path(*SQUARE))  # мясцовы час 00:00–04:00 2 чэрвеня
        (self.content / "h.yaml").write_text("date: 2025-06-02\ntitle: {be: H}\nkomoot: [1]\n", encoding="utf-8")
        h = self.build()[0]["h"]
        sun = h["facts"]["sun"]
        self.assertEqual((h["days"][0]["date"], h["facts"]["start"]["time"], h["facts"]["finish"]["time"]),
                         ("2025-06-02", "00:00", "04:00"))
        self.assertEqual((sun.get("night"), "left" in sun), (True, False))
        self.assertLess(sun["rise"], sun["set"])

    def test_r14_prev6_coordinates_with_a_day_stay_on_that_day(self):
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))
        self.add_tour(2, "2025-06-02T04:00:00Z", path(*SQUARE))  # той самы маршрут назаўтра
        days = practical.raw_days([1, 2], 4)
        spot = [41.0 + 1000 * M, 42.0]  # паўночна-заходні вугал квадрата, 1 км ад старту
        for spec, day in (({"at": spot}, 0), ({"at": spot, "day": 1}, 0), ({"at": spot, "day": 2}, 1)):
            pos, problem = practical.locate(spec, days, {})
            self.assertIsNone(problem, spec)
            self.assertEqual((pos["day"], pos["km"]), (day, 1.0), spec)

    def test_r14_broken_gps_in_exif_is_no_gps(self):
        from PIL.TiffImagePlugin import IFDRational
        folder = self.photos_root / "20250601_H" / "Сайт"
        folder.mkdir(parents=True)
        nan = IFDRational(0, 0)
        for name, lat, lng in (("Broken.jpg", (nan, nan, nan), (nan, nan, nan)),
                               ("Mars.jpg", (IFDRational(123), IFDRational(0), IFDRational(0)), (IFDRational(42), IFDRational(0), IFDRational(0)))):
            exif = Image.Exif()
            exif.get_ifd(0x8825).update({1: "N", 2: lat, 3: "E", 4: lng})
            Image.new("RGB", (80, 60), (5, 5, 5)).save(folder / name, "JPEG", exif=exif)
            with Image.open(folder / name) as im:
                self.assertEqual(build._exif_info(im), (None, None), name)
        # паход без трэку: пункт на карце з такога фота не бярэцца, індэкс застаецца правільным JSON
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nphotos_folder: 20250601_H\n", encoding="utf-8")
        index, out = self.build()
        self.assertIsNone(index["h"]["point"])
        self.assertEqual(len(self.strict_index()["hikes"][0]["photos"]), 2)
        # маніфест ранейшай версіі з NaN чытаецца, а запісваецца ўжо без яго
        manifest_file = build.CACHE / "photos" / "h.json"
        manifest_file.write_text(manifest_file.read_text(encoding="utf-8").replace('"gps": null', '"gps": [NaN, NaN]'), encoding="utf-8")
        self.assertIn("NaN", manifest_file.read_text(encoding="utf-8"))
        for root in (self.photos_root, self.root / "missing-disk"):  # звычайная зборка і зборка без тэчкі з фота
            self.cfg["photos_root"] = str(root)
            index, out = self.build()
            self.assertIsNone(index["h"]["point"], root.name)
            self.strict_index()
        self.cfg["photos_root"] = str(self.photos_root)
        self.build()
        self.assertNotIn("NaN", manifest_file.read_text(encoding="utf-8"))


class TestReview142(PracticalProject):
    """Рэгрэсійныя тэсты па review_2026-10-11_v1.4.2.md."""

    def test_r15_2_lost_manifest_keeps_the_place_and_photo_of_a_record(self):
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))
        self.photo("20250601_H", "A.jpg", "2025:06:01 09:00:00")
        self.photo("20250601_H", "B.jpg", "2025:06:01 11:00:00")
        (self.content / "h.yaml").write_text("""\
date: 2025-06-01
title: {be: H}
photos_folder: 20250601_H
komoot: [1]
practical:
  places:
    - {type: shelter, photo: A.jpg}
  water:
    sources:
      - {type: spring, photo: B, water: true}
""", encoding="utf-8")
        before, _ = self.build()
        place, source = before["h"]["practical"]["places"][0], before["h"]["practical"]["water"]["sources"][0]
        self.assertEqual((place["photo"], place["pos"]["km"], source["photo"], source["pos"]["km"]), (0, 1.0, 1, 3.0))
        # праект скланаваны: кэша няма (ні маніфеста, ні тураў), дыск з фота не падключаны
        (build.CACHE / "photos" / "h.json").unlink()
        (build.CACHE / "komoot" / "tours" / "1.json").unlink()
        self.cfg["photos_root"] = str(self.root / "missing-disk")
        for _ in range(2):
            after, out = self.build()
            self.assertNotIn("practical:", out)
            for key in ("photos", "track", "practical"):
                self.assertEqual(after["h"][key], before["h"][key], key)

    def test_r15_2_reordered_gallery_and_really_deleted_photo(self):
        a, b = ({"src": f"photos/h/{n}-11111111.webp", "thumb": f"photos/h/{n}-11111111-t.webp", "w": 4, "h": 3} for n in "ab")
        pos = {"day": 0, "km": 1.0, "at": [42.0, 41.009], "alt": 1200, "time": "09:00"}
        prev = {"places": [{"ref": "photo::a.jpg", "pos": pos, "photo": 0}], "water": None}
        block = {"places": [{"type": "shelter", "photo": "A.jpg"}]}

        def parse(photos):
            out = io.StringIO()
            with unittest.mock.patch("sys.stdout", out):
                parsed, _ = practical.parse_practical("h", block, days=[], stats=None, n_days=1, end="2025-06-01",
                                                      manifest={}, photos=photos, prev=prev, prev_photos=[a, b],
                                                      track_kept=True)
            return parsed["places"][0], out.getvalue()

        place, out = parse([b, a])  # парадак галерэі змяніўся
        self.assertEqual((place["photo"], place["pos"], out), (1, pos, ""))
        place, out = parse([b])     # фота сапраўды прыбрана з галерэі
        self.assertEqual((place["photo"], place["pos"]), (None, None))
        self.assertIn("фота A.jpg няма сярод фота паходу", out)

    def test_r15_3_time_in_a_long_break_between_tours_has_no_place(self):
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (2000, 0)), duration=3600)    # 08:00–12:00
        self.add_tour(2, "2025-06-01T13:00:00Z", path((20000, 0), (22000, 0)))              # 17:00–21:00
        days = practical.raw_days([1, 2], 4)
        self.assertEqual((len(days), len(days[0]["segs"])), (1, 2))
        shot = {"Gap.jpg": "2025-06-01T14:30:00", "In.jpg": "2025-06-01T18:00:00", "Edge.jpg": "2025-06-01T12:20:00"}
        for spec in ({"time": "14:30"}, {"photo": "Gap.jpg"}):
            pos, problem = practical.locate(spec, days, shot)
            self.assertIsNone(pos, spec)
            self.assertIn("па-за часам трэку", problem, spec)
        good = {"first tour": ({"time": "10:00"}, 1.0), "second tour": ({"time": "18:00"}, 2.5),
                "photo in the second tour": ({"photo": "In.jpg"}, 2.5),
                "20 minutes after the first tour": ({"photo": "Edge.jpg"}, 2.0),
                "20 minutes before the second tour": ({"time": "16:40"}, 2.0)}
        for name, (spec, km) in good.items():
            pos, problem = practical.locate(spec, days, shot)
            self.assertIsNone(problem, name)
            self.assertAlmostEqual(pos["km"], km, delta=0.05, msg=name)

    def test_r15_4_time_after_midnight_on_a_night_route(self):
        self.add_tour(1, "2025-06-01T18:00:00Z", path(*SQUARE))  # мясцовы час 22:00–02:00 наступнага дня
        days = practical.raw_days([1], 4)
        self.assertEqual([d["date"] for d in days], ["2025-06-01"])
        for spec, km, clock in (({"time": "01:00", "day": 1}, 3.0, "01:00"), ({"time": "23:00"}, 1.0, "23:00"),
                                ({"time": 60}, 3.0, "01:00")):  # YAML чытае 1:00 без двукосся як 60
            pos, problem = practical.locate(spec, days, {})
            self.assertIsNone(problem, spec)
            self.assertEqual((pos["km"], pos["time"], pos["day"]), (km, clock, 0), spec)
        for spec in ({"time": "05:00"}, {"time": "20:00", "day": 1}):
            pos, problem = practical.locate(spec, days, {})
            self.assertIsNone(pos, spec)
            self.assertIn("па-за часам трэку", problem, spec)


class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise fetch.requests.HTTPError(f"HTTP {self.status_code}")


def hourly_answer(params, hole=None):
    """Адказ архіва Open-Meteo: па гадзінах для кожнага пункта; тэмпература — нумар гадзіны UTC мінус вышыня/100."""
    first = datetime.fromisoformat(params["start_date"]).replace(tzinfo=timezone.utc)
    last = datetime.fromisoformat(params["end_date"]).replace(tzinfo=timezone.utc) + timedelta(hours=23)
    times = [int(first.timestamp()) + 3600 * i for i in range(int((last - first).total_seconds() // 3600) + 1)]
    places = []
    for alt in (float(a) for a in params["elevation"].split(",")):
        hours = [datetime.fromtimestamp(t, timezone.utc).hour for t in times]
        hourly = {"time": times, "temperature_2m": [h - alt / 100 for h in hours],
                  "apparent_temperature": [h - alt / 100 - 1 for h in hours], "precipitation": [0.5 if h == 6 else 0.0 for h in hours],
                  "snowfall": [0.0] * len(times), "wind_speed_10m": [float(h) for h in hours],
                  "wind_gusts_10m": [2.0 * h for h in hours], "cloud_cover": [50] * len(times),
                  "weather_code": [61 if h == 6 else 2 for h in hours]}
        if hole:
            hourly[hole] = [None] * len(times)
        places.append({"elevation": alt, "hourly": hourly})
    return places if len(places) > 1 else places[0]


def daily_answer(params, elevation=1000.0, hole=None):
    """Адказ архіва Open-Meteo з дзённымі данымі: удзень 10 + нумар месяца °C, уначы нумар месяца − 10 °C,
    ападкі 3 мм кожны трэці дзень месяца (іначай 0,5 мм). hole — паказчык, якога няма."""
    first, last = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    daily = {"time": [d.isoformat() for d in days],
             "temperature_2m_max": [10.0 + d.month for d in days], "temperature_2m_min": [d.month - 10.0 for d in days],
             "precipitation_sum": [3.0 if d.day % 3 == 0 else 0.5 for d in days]}
    if hole:
        daily[hole] = [None] * len(days)
    return {"latitude": float(params["latitude"]), "longitude": float(params["longitude"]), "elevation": elevation, "daily": daily}


ROUTE = {"code": "Ok", "routes": [{"distance": 42060.0, "duration": 3760.0}],  # Батумі → Мілісі паводле OSRM
         "waypoints": [{"distance": 12.0}, {"distance": 40.0}]}
NAMES = {"be": "Мілісі", "en": "Milisi"}


class FetchProject(PracticalProject):
    """Часовы праект для `hike fetch`: знешнія сэрвісы падмененыя, сеткі няма."""

    def setUp(self):
        super().setUp()
        patcher = unittest.mock.patch.multiple(fetch, CACHE=build.CACHE, CONTENT=self.content, load_config=lambda: self.cfg)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.photos_root.mkdir()

    def fetch(self, argv=(), weather=hourly_answer, rate=2.5, osm=(), route=ROUTE, names=NAMES, climate=None):
        """Запуск `hike fetch` з падмененымі сэрвісамі. Вяртае запыты надвор'я і курсаў і тое, што надрукавана;
        усе запыты (з Overpass, OSRM, Nominatim і звычайнага надвор'я) — у self.calls як (сэрвіс, падрабязнасці).
        climate — адказ на запыт звычайнага надвор'я; па змаўчанні — daily_answer."""
        calls, self.calls, self.sleeps = [], [], []  # sleeps — паўзы паміж запытамі (самі паўзы не чакаюцца)

        def respond(answer, *args):
            answer = answer(*args) if callable(answer) else answer
            return answer if isinstance(answer, FakeResponse) else FakeResponse(answer)

        def fake_get(session, url, params=None, timeout=None):
            if url.startswith(fetch.API_ROUTE):
                self.calls.append(("route", url[len(fetch.API_ROUTE):]))
                return respond(route, url)
            if "daily" in (params or {}):  # звычайнае надвор'е па месяцах — той жа архіў, дзённыя даныя
                self.calls.append(("climate", dict(params)))
                return respond(climate or daily_answer, params)
            calls.append((url, dict(params or {})))
            self.calls.append(("rate" if url == fetch.API_RATES else "weather", dict(params or {})))
            if url == fetch.API_RATES:
                return respond(rate if isinstance(rate, FakeResponse) else
                               [{"currencies": [{"code": "USD", "quantity": 1, "rate": rate}]}])
            return respond(weather, params)

        def fake_post(session, url, data=None, timeout=None):
            self.calls.append(("osm", data["data"]))
            return respond({"elements": list(osm)} if isinstance(osm, (list, tuple)) else osm, data["data"])

        def fake_nominatim(lat, lng, lang, zoom=5):
            self.calls.append(("names", (round(lat, 3), round(lng, 3), lang, zoom)))
            answer = names(lat, lng, lang) if callable(names) else names
            if not answer:
                return {}
            name = (answer.get("ru") or answer["en"]) if lang.startswith("ru") else answer["en" if lang == "en" else "be"]
            return {"village": name}

        out = io.StringIO()
        with unittest.mock.patch("requests.Session.get", fake_get), unittest.mock.patch("requests.Session.post", fake_post), \
                unittest.mock.patch.object(geo, "_nominatim", fake_nominatim), \
                unittest.mock.patch("time.sleep", lambda seconds: self.sleeps.append(seconds)), unittest.mock.patch("sys.stdout", out):
            try:
                fetch.main(list(argv))
                self.exit_code = 0
            except SystemExit as stop:  # штосьці не атрымана: каманда завяршаецца з кодам 1
                self.exit_code = stop.code
        return calls, out.getvalue()

    def hike(self, extra=""):
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))  # 08:00–12:00 мясцовага, 04:00–08:00 UTC
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\n" + extra, encoding="utf-8")



class TestWeatherAndRates(FetchProject):
    """Этап 2: надвор'е з Open-Meteo і курс Нацбанка Грузіі — без сеткі, з падмененымі адказамі."""

    def test_weather_points_and_summary(self):
        self.hike()
        day = practical.raw_days([1], 4)[0]
        points = practical.weather_points(day)
        self.assertEqual([(q["role"], q["alt"]) for q in points], [("start", 1000), ("top", 1400), ("finish", 1000)])
        start = datetime(2025, 6, 1, 4, tzinfo=timezone.utc).timestamp()
        self.assertEqual([q["ts"] - start for q in points], [0, 7200, 14400])
        self.assertEqual(practical.weather_key(points), practical.weather_key([dict(q) for q in points]))
        self.assertNotEqual(practical.weather_key(points), practical.weather_key(points[:2]))
        # трэк без часу або без вышыні
        self.assertEqual(practical.weather_points({"segs": [[(41.0, 42.0, 1000, None, 0.0), (41.01, 42.0, 1100, None, 1.0)]]}), [])
        flat = practical.weather_points({"segs": [[(41.0, 42.0, None, 0.0, 0.0), (41.01, 42.0, None, 60.0, 1.0)]]})
        self.assertEqual([q["role"] for q in flat], ["start", "finish"])

        series = hourly_answer({"start_date": "2025-06-01", "end_date": "2025-06-01", "elevation": "1000,1400,1000"})
        summary = practical.summarize_weather(points, [place["hourly"] for place in series])
        self.assertTrue(summary["complete"])
        self.assertEqual([(q["role"], q["temp"], q["feels"], q["wind"], q["gusts"], q["code"]) for q in summary["points"]],
                         [("start", -6.0, -7.0, 4.0, 8.0, 2), ("top", -8.0, -9.0, 6.0, 12.0, 61), ("finish", -2.0, -3.0, 8.0, 16.0, 2)])
        # за час паходу — гадзіны 04:00–08:00 UTC па ўсіх пунктах; ападкі — найбольшая сума сярод пунктаў
        self.assertEqual(summary["span"], {"tempMin": -10.0, "tempMax": -2.0, "wind": 8.0, "gusts": 16.0, "code": 61,
                                           "precip": 0.5, "snow": 0.0})
        holed = hourly_answer({"start_date": "2025-06-01", "end_date": "2025-06-01", "elevation": "1000,1400,1000"}, "wind_gusts_10m")
        summary = practical.summarize_weather(points, [place["hourly"] for place in holed])
        self.assertEqual((summary["complete"], summary["points"][0]["gusts"], "gusts" in summary["span"]), (False, None, False))
        self.assertEqual(summary["points"][0]["temp"], -6.0, "астатнія паказчыкі застаюцца")

    def test_fetch_caches_weather_and_build_publishes_it(self):
        self.hike()
        calls, out = self.fetch()
        self.assertEqual(len(calls), 1)
        url, params = calls[0]
        self.assertEqual((url, params["start_date"], params["end_date"], params["timezone"], params["timeformat"]),
                         (fetch.API_WEATHER, "2025-06-01", "2025-06-01", "GMT", "unixtime"))
        self.assertEqual((params["latitude"].count(","), params["elevation"]), (2, "1000,1400,1000"))
        self.assertIn("спампавана дзён 1", out)
        self.assertEqual(self.fetch()[0], [], "другі запуск нічога не пытае")
        self.assertEqual(len(self.fetch(["--refresh"])[0]), 1)

        index, out = self.build()
        day = index["h"]["days"][0]
        self.assertNotIn("надвор'я няма", out)
        self.assertEqual([(q["role"], q["time"], q["alt"], q["temp"]) for q in day["weather"]["points"]],
                         [("start", "08:00", 1000, -6.0), ("top", "10:00", 1400, -8.0), ("finish", "12:00", 1000, -2.0)])
        self.assertEqual((day["weather"]["span"]["tempMax"], day["weather"]["fetched"]), (-2.0, date.today().isoformat()))
        self.assertNotIn("ts", day["weather"]["points"][0])
        self.assertNotIn("key", day["weather"])

        # трэк змяніўся: старое надвор'е да яго не адносіцца
        self.add_tour(1, "2025-06-01T05:00:00Z", path(*SQUARE))
        index, out = self.build()
        self.assertNotIn("weather", index["h"]["days"][0])
        self.assertIn("надвор'я няма для паходаў: 1 — запусціце `hike fetch`", out)
        self.assertEqual(len(self.fetch()[0]), 1, "для новага трэку надвор'е пытаецца зноў")
        self.assertEqual(self.build()[0]["h"]["days"][0]["weather"]["points"][0]["time"], "09:00")

        # тура няма ў кэшы: разам з ранейшым трэкам застаецца і яго надвор'е
        before = self.build()[0]["h"]["days"]
        (build.CACHE / "komoot" / "tours" / "1.json").unlink()
        self.assertEqual(self.build()[0]["h"]["days"], before)

    def test_fetch_failures_and_incomplete_answers(self):
        self.hike()
        refused = lambda params: FakeResponse({"error": True, "reason": "Parameter 'start_date' is out of allowed range"}, 400)  # noqa: E731
        calls, out = self.fetch(weather=refused)
        self.assertIn("надвор'е не атрымана — Parameter 'start_date' is out of allowed range", out)
        self.assertIn("Не атрымана: 1.", out)
        self.assertFalse((build.CACHE / "weather" / "h.json").exists())
        self.assertIn("надвор'я няма для паходаў: 1", self.build()[1])

        calls, out = self.fetch(weather=lambda params: hourly_answer(params, "cloud_cover"))
        self.assertIn("няпоўныя звесткі", out)
        cached = common.read_json(build.CACHE / "weather" / "h.json")["days"]["2025-06-01"]
        self.assertEqual((cached["complete"], cached["points"][0]["cloud"]), (False, None))
        self.assertIsNone(self.build()[0]["h"]["days"][0]["weather"]["points"][0]["cloud"], "пустое не становіцца нулём")
        self.assertEqual(len(self.fetch()[0]), 1, "няпоўныя звесткі пытаюцца зноў")
        self.assertTrue(common.read_json(build.CACHE / "weather" / "h.json")["days"]["2025-06-01"]["complete"])
        self.assertEqual(self.fetch()[0], [])

        # збой сеткі не сцірае ўжо спампаванае
        def offline(params):
            raise fetch.requests.ConnectionError("няма сеткі")
        calls, out = self.fetch(["--refresh"], weather=offline)
        self.assertIn("надвор'е не атрымана — няма сеткі", out)
        self.assertTrue(common.read_json(build.CACHE / "weather" / "h.json")["days"]["2025-06-01"]["complete"])

    def test_prices_get_dollars_at_the_rate_of_their_date(self):
        self.hike("""\
practical:
  getting_there:
    - {type: taxi, price: 80, date: 2025-05-30}
    - {type: marshrutka, price: 5}
    - {type: car, km: 40}
""")
        index, out = self.build()
        self.assertIn("няма курсу лары на 2025-05-30, 2025-06-01 — запусціце `hike fetch`", out)
        self.assertEqual([w["usd"] for w in index["h"]["practical"]["gettingThere"]], [None, None, None])

        calls, out = self.fetch()
        rate_calls = [params for url, params in calls if url == fetch.API_RATES]
        self.assertEqual(rate_calls, [{"currencies": "USD", "date": "2025-05-30"}, {"currencies": "USD", "date": "2025-06-01"}])
        self.assertIn("Курсаў: 2.", out)
        self.assertEqual(common.read_json(build.CACHE / "rates.json")["USD"], {"2025-05-30": 2.5, "2025-06-01": 2.5})
        self.assertEqual([url for url, _ in self.fetch()[0] if url == fetch.API_RATES], [], "курсы ўжо ёсць")

        index, out = self.build()
        self.assertNotIn("няма курсу", out)
        taxi, bus, car = index["h"]["practical"]["gettingThere"]
        self.assertEqual((taxi["price"], taxi["usd"], taxi["rateDate"]), (80.0, 32.0, "2025-05-30"))
        self.assertEqual((bus["usd"], bus["rateDate"]), (2.0, "2025-06-01"), "без даты — курс на дзень паходу")
        self.assertEqual((car["usd"], car["rateDate"]), (None, None), "без цаны даляраў няма")

    def test_bad_rate_answers_are_not_stored(self):
        self.hike("practical:\n  getting_there:\n    - {type: taxi, price: 80}\n")
        for answer in (FakeResponse([], 200), FakeResponse([{"currencies": [{"code": "USD", "rate": 0}]}]),
                       FakeResponse([{"currencies": [{"code": "USD", "rate": "nan"}]}]), FakeResponse({}, 500)):
            calls, out = self.fetch(rate=answer)
            self.assertIn("Не атрымана: 1.", out)
            self.assertFalse((build.CACHE / "rates.json").exists())
        common.write_json(build.CACHE / "rates.json", {"USD": {"2025-06-01": float("inf") if False else -3}})
        index, out = self.build()
        self.assertEqual(index["h"]["practical"]["gettingThere"][0]["usd"], None, "няправільны курс з кэша не выкарыстоўваецца")


def osm_node(i, north, east, **tags):
    """Аб'ект OpenStreetMap за `north` і `east` метраў ад пачатку тэставых трэкаў."""
    return {"type": "node", "id": i, "lat": 41.0 + north * M, "lon": 42.0 + east * M / 0.7547, "tags": tags}


SPRING = osm_node(1, 500, -100, natural="spring", name="Цёплая", **{"name:en": "Warm"})  # 100 м збоку ад 0,5 км
VIEW = osm_node(2, 1000, 500, tourism="viewpoint")                                       # на трэку, 1,5 км
NO_ROUTE = {"code": "NoRoute", "routes": []}


class TestHintsAndDrive(FetchProject):
    """Этап 3: падказкі OpenStreetMap, дарога на машыне і назвы населеных пунктаў."""

    def kinds(self):
        return [kind for kind, _ in self.calls]

    def test_osm_objects_become_typed_hints(self):
        item = lambda **tags: practical.osm_item({"type": "node", "id": 7, "lat": 41.123456789, "lon": 42.0, "tags": tags})  # noqa: E731
        kind = lambda **tags: (lambda it: it and (it["kind"], it["type"]))(item(**tags))  # noqa: E731
        self.assertEqual(item(amenity="drinking_water", name="Кран", **{"name:en": "Tap", "name:be": "Крынічка"}),
                         {"kind": "water", "type": "tap", "lat": 41.12346, "lng": 42.0, "osm": "node/7",
                          "name": {"be": "Крынічка", "en": "Tap"}})
        self.assertEqual(item(natural="spring")["name"], {"be": "", "en": ""})
        self.assertEqual(item(natural="spring", name="წყარო")["name"], {"be": "წყარო", "en": "წყარო"}, "без перакладу — мясцовая назва")
        self.assertEqual([kind(natural="spring"), kind(man_made="water_well"), kind(amenity="water_point"), kind(amenity="shelter"),
                          kind(tourism="camp_site"), kind(tourism="wilderness_hut"), kind(leisure="firepit"),
                          kind(leisure="picnic_table"), kind(information="guidepost", tourism="information"), kind(amenity="toilets")],
                         [("water", "spring"), ("water", "well"), ("water", "tap"), ("place", "shelter"), ("place", "camp"),
                          ("place", "camp"), ("place", "firepit"), ("place", "table"), ("place", "signpost"), ("place", "toilet")])
        # не паказваецца: прыватнае, прыпынак транспарту, непітная вада, іншыя аб'екты, аб'ект без каардынат
        for tags in ({"natural": "spring", "access": "private"}, {"amenity": "toilets", "access": "customers"},
                     {"amenity": "shelter", "shelter_type": "public_transport"}, {"natural": "spring", "drinking_water": "no"},
                     {"amenity": "bench"}, {}):
            self.assertIsNone(item(**tags), tags)
        self.assertIsNone(practical.osm_item({"type": "way", "id": 1, "tags": {"tourism": "camp_site"}}))
        way = practical.osm_item({"type": "way", "id": 9, "center": {"lat": 41.5, "lon": 42.5}, "tags": {"tourism": "camp_site"}})
        self.assertEqual((way["lat"], way["lng"], way["osm"]), (41.5, 42.5, "way/9"), "плошча — па цэнтры")

    def test_fetch_stores_hints_drive_and_names_and_build_publishes_them(self):
        self.hike()
        index, out = self.build()
        self.assertEqual((index["h"]["hints"], index["h"]["drive"]), ([], None))
        self.assertNotIn("place", index["h"]["facts"]["start"])
        self.assertIn("падказак OpenStreetMap або дарогі на машыне няма для паходаў: 1 — запусціце `hike fetch`", out)

        far = osm_node(3, 500, -300, amenity="drinking_water")  # у вобласці запыту, але за 300 м ад трэку
        stop = osm_node(4, 0, 10, amenity="shelter", shelter_type="public_transport")
        _, out = self.fetch(osm=[VIEW, far, stop, SPRING])
        self.assertEqual(sorted(self.kinds()), ["climate", "names", "names", "names", "osm", "route", "weather"])
        calls = dict(self.calls)
        bbox = practical.osm_bbox(practical.raw_days([1], 4))
        self.assertEqual(bbox, [40.996, 41.996, 41.013, 42.016])
        self.assertIn('node["natural"="spring"](40.996,41.996,41.013,42.016);', calls["osm"])
        self.assertTrue(calls["osm"].startswith("[out:json]") and calls["osm"].endswith("out center tags;"))
        self.assertEqual(calls["route"], "41.59822,41.62692;42.0,41.0", "ад парку да старту: даўгата,шырата")
        self.assertEqual([c for kind, c in self.calls if kind == "names"],
                         [(41.0, 42.0, "be,en", 14), (41.0, 42.0, "en", 14), (41.0, 42.0, "ru,en", 14)])
        for line in ("OpenStreetMap — 3 аб'ектаў у вобласці трэку", "на машыне да старту 42.1 км, 63 хв",
                     "OpenStreetMap: паходаў 1. Маршрутаў на машыне: 1. Назваў месцаў: 1."):
            self.assertIn(line, out)
        self.assertNotIn("Не атрымана", out)
        self.fetch()
        self.assertEqual(self.calls, [], "другі запуск нічога не пытае")
        self.fetch(["--refresh"], osm=[VIEW, SPRING])
        self.assertEqual(sorted(self.kinds()), ["climate", "osm", "route", "weather"], "назвы месцаў не перапытваюцца")

        index, out = self.build()
        h = index["h"]
        self.assertNotIn("запусціце `hike fetch`", out)
        self.assertEqual(h["hints"], [
            {"kind": "water", "type": "spring", "name": {"be": "Цёплая", "en": "Warm"}, "osm": "node/1",
             "pos": {"day": 0, "km": 0.5, "at": [round(42.0 - 100 * M / 0.7547, 5), round(41.0 + 500 * M, 5)], "alt": 1100, "off": 100}},
            {"kind": "place", "type": "viewpoint", "name": {"be": "", "en": ""}, "osm": "node/2",
             "pos": {"day": 0, "km": 1.5, "at": [round(42.0 + 500 * M / 0.7547, 5), round(41.0 + 1000 * M, 5)], "alt": 1300}},
        ], "па парадку ўздоўж трэку, без часу праходу")
        self.assertEqual(h["drive"], {"from": {"be": "Батумі, парк Качынскіх", "en": "Batumi, Kaczyński Park"},
                                      "to": {"km": 42.1, "minutes": 63}})
        for facts in (h["facts"], h["days"][0]["facts"]):
            self.assertEqual((facts["start"]["place"], facts["finish"]["place"]), (NAMES, NAMES))
            self.assertEqual(facts["start"]["at"], [42.0, 41.0], "астатні разлік не зменены")
        self.assertEqual(h["bbox"][:2], [42.0, 41.0], "вобласць пошуку падказак не падмяняе межы трэку")

    def test_authors_records_replace_matching_hints(self):
        shelter = osm_node(5, 1000, 520, amenity="shelter")  # за 20 м ад агляднай пляцоўкі, але іншага тыпу
        self.hike("""\
practical:
  water:
    sources:
      - {type: tap, km: 0.5}
  places:
      - {type: viewpoint, at: [%.6f, %.6f]}
""" % (41.0 + 1010 * M, 42.0 + 500 * M / 0.7547))
        self.fetch(osm=[SPRING, VIEW, shelter])
        hints = self.build()[0]["h"]["hints"]
        self.assertEqual([(q["kind"], q["type"]) for q in hints], [("place", "shelter")],
                         "крыніца на тым жа кіламетры і пляцоўка побач ужо пазначаныя аўтарам")
        # без запісаў аўтара — усе тры
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\n", encoding="utf-8")
        self.assertEqual(len(self.build()[0]["h"]["hints"]), 3)

    def test_hints_follow_the_track(self):
        self.hike()
        self.fetch(osm=[SPRING, VIEW])
        before = self.build()[0]["h"]
        self.assertEqual(len(before["hints"]), 2)

        # тура няма ў кэшы: разам з ранейшым трэкам застаюцца яго падказкі і дарога
        tour = build.CACHE / "komoot" / "tours" / "1.json"
        saved = tour.read_text(encoding="utf-8")
        tour.unlink()
        kept = self.build()[0]["h"]
        self.assertEqual((kept["hints"], kept["drive"], kept["facts"]), (before["hints"], before["drive"], before["facts"]))
        tour.write_text(saved, encoding="utf-8")

        # трэк у іншым месцы: спампаванае да яго не адносіцца
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*[(n + 5000, e) for n, e in SQUARE]))
        index, out = self.build()
        self.assertEqual((index["h"]["hints"], index["h"]["drive"]), ([], None))
        self.assertNotIn("place", index["h"]["facts"]["start"])
        self.assertIn("падказак OpenStreetMap або дарогі на машыне няма для паходаў: 1", out)
        self.fetch(osm=[osm_node(6, 5500, 0, natural="spring")])
        self.assertEqual(sorted(self.kinds()), ["climate", "names", "names", "names", "osm", "route", "weather"],
                         "для новага трэку ўсё пытаецца зноў")
        index, out = self.build()
        self.assertEqual([(q["osm"], q["pos"]["km"]) for q in index["h"]["hints"]], [("node/6", 0.5)])
        self.assertIsNotNone(index["h"]["drive"])

    def test_long_route_gets_drive_back_and_days_keep_their_hints(self):
        # два дні: 3 км на поўнач і яшчэ 3 км далей; фініш за 6 км ад старту
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (3000, 0)))
        self.add_tour(2, "2025-06-02T04:00:00Z", path((3000, 0), (6000, 0)))
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: [1, 2]\n", encoding="utf-8")
        names = lambda lat, lng, lang: {"be": f"Вёска {round((lat - 41.0) / M / 1000)}", "en": f"Village {round((lat - 41.0) / M / 1000)}"}  # noqa: E731
        routes = iter([ROUTE, {"code": "Ok", "routes": [{"distance": 12449.0, "duration": 1500.0}], "waypoints": [{"distance": 1234.0}, {"distance": 5.0}]}])
        self.fetch(osm=[osm_node(1, 1000, 50, natural="spring"), osm_node(2, 5000, -40, leisure="firepit")],
                   route=lambda url: next(routes), names=names)
        self.assertEqual(self.kinds().count("route"), 2, "да старту і ад далёкага фінішу")
        self.assertEqual(self.kinds().count("names"), 9, "тры розныя пункты (старт, начлег, фініш) на трох мовах")
        h = self.build()[0]["h"]
        self.assertEqual(h["drive"]["to"], {"km": 42.1, "minutes": 63})
        self.assertEqual(h["drive"]["back"], {"km": 12.4, "minutes": 25, "off": 1200}, "дарога не даходзіць да фінішу 1,2 км")
        self.assertEqual([(q["type"], q["pos"]["day"], q["pos"]["km"], q["pos"]["off"]) for q in h["hints"]],
                         [("spring", 0, 1.0, 50), ("firepit", 1, 2.0, 40)])
        self.assertEqual([(d["facts"]["start"]["place"]["be"], d["facts"]["finish"]["place"]["en"]) for d in h["days"]],
                         [("Вёска 0", "Village 3"), ("Вёска 3", "Village 6")])
        self.assertEqual((h["facts"]["start"]["place"]["be"], h["facts"]["finish"]["place"]["be"]), ("Вёска 0", "Вёска 6"))

    def test_several_hikes_share_one_osm_request(self):
        self.hike()
        self.add_tour(2, "2025-07-01T04:00:00Z", path(*[(n + 20000, e) for n, e in SQUARE]))
        (self.content / "k.yaml").write_text("date: 2025-07-01\ntitle: {be: K}\nkomoot: [2]\n", encoding="utf-8")
        there = osm_node(8, 20500, 0, amenity="drinking_water")
        self.fetch(osm=[SPRING, there])
        self.assertEqual(self.kinds().count("osm"), 1, "сервер абмяжоўвае частату запытаў: паходы пытаюцца разам")
        self.assertEqual(dict(self.calls)["osm"].count('node["natural"="spring"]('), 2)
        index = self.build()[0]
        self.assertEqual([[q["osm"] for q in index[slug]["hints"]] for slug in ("h", "k")], [["node/1"], ["node/8"]],
                         "кожнаму паходу — толькі аб'екты з яго вобласці")
        with unittest.mock.patch.object(fetch, "OSM_BATCH", 1):
            self.fetch(["--refresh"], osm=[SPRING, there])
        self.assertEqual(self.kinds().count("osm"), 2)

    def test_service_failures_keep_what_was_fetched(self):
        self.hike()
        busy = FakeResponse({}, 429)
        _, out = self.fetch(osm=busy, route=NO_ROUTE, names=lambda *a: (_ for _ in ()).throw(fetch.requests.ConnectionError("няма сеткі")))
        self.assertEqual(self.kinds().count("osm"), 3, "заняты сервер пытаецца яшчэ двойчы")
        for line in ("OpenStreetMap не адказаў (паходаў: 1) — HTTP 429", "назва месца для 41.000, 42.000 не вызначана: няма сеткі",
                     "Не атрымана: 3."):
            self.assertIn(line, out)
        self.assertFalse((build.CACHE / "osm" / "h.json").exists())
        self.assertFalse((build.CACHE / "routes.json").exists())
        self.assertFalse(geo.GEOCODE_CACHE.exists())
        index, out = self.build()
        self.assertEqual((index["h"]["hints"], index["h"]["drive"]), ([], None))

        # перарваны запыт Overpass адказвае 200 з няпоўным вынікам — ён не захоўваецца
        _, out = self.fetch(osm={"elements": [SPRING], "remark": "runtime error: Query timed out"}, route=NO_ROUTE, names=None)
        self.assertIn("OpenStreetMap не адказаў (паходаў: 1) — runtime error: Query timed out", out)
        self.assertFalse((build.CACHE / "osm" / "h.json").exists())
        self.assertEqual(common.read_json(geo.GEOCODE_CACHE), {"names:41.000,42.000": {"be": "", "en": "", "ru": ""}},
                         "населенага пункта побач няма — гэта таксама адказ, ён не перапытваецца")

        # паўтор пасля занятага сервера ўдаецца
        answers = iter([busy, {"elements": [SPRING, VIEW]}])
        _, out = self.fetch(osm=lambda query: next(answers))
        self.assertEqual(self.kinds(), ["route", "osm", "osm"], "назва месца ўжо ёсць")
        self.assertNotIn("Не атрымана", out)
        index, out = self.build()
        self.assertEqual((len(index["h"]["hints"]), index["h"]["drive"]["to"]["km"]), (2, 42.1))
        self.assertNotIn("place", index["h"]["facts"]["start"], "пустая назва не паказваецца")

        # збой пры --refresh не сцірае спампаванае
        def offline(*args):
            raise fetch.requests.ConnectionError("няма сеткі")
        _, out = self.fetch(["--refresh"], weather=offline, osm=offline, route=offline)
        self.assertIn("дарога на машыне не разлічана — няма сеткі", out)
        self.assertEqual(len(common.read_json(build.CACHE / "osm" / "h.json")["items"]), 2)
        self.assertEqual(self.build()[0]["h"]["drive"]["to"], {"km": 42.1, "minutes": 63})

        # няправільныя лічбы сэрвісу маршрутаў не трапляюць на сайт
        (build.CACHE / "routes.json").unlink()
        for bad in ({"code": "Ok", "routes": [{"distance": 0, "duration": 0}]}, {"code": "Ok", "routes": [{"distance": "nan", "duration": 10}]},
                    {"code": "Ok", "routes": [{}]}, FakeResponse({}, 500)):
            self.fetch(route=bad)
            self.assertFalse((build.CACHE / "routes.json").exists(), bad)

    def test_place_names_are_settlements_in_three_languages(self):
        answers = {"be,en": {"hamlet": " Хутар ", "municipality": "Раён"}, "en": {"county": "County", "municipality": "District"},
                   "ru,en": {"hamlet": "Хутор"}}
        asked = []

        def nominatim(lat, lng, lang, zoom=5):
            asked.append(lang)
            return answers[lang]

        with unittest.mock.patch.object(geo, "_nominatim", nominatim):
            self.assertEqual(geo.place_names(41.0004, 42.0), {"be": "Хутар", "en": "Хутар", "ru": "Хутор"},
                             "без англійскай назвы — беларуская; руская — асобна")
            self.assertEqual(asked, ["be,en", "en", "ru,en"])
            answers["be,en"] = {"municipality": "Раён"}
            self.assertEqual(geo.place_names(41.0004, 42.0)["be"], "Хутар", "з кэша")
            self.assertEqual(len(asked), 3, "без новага запыту")
            answers["ru,en"] = {"municipality": "Район"}
            self.assertEqual(geo.place_names(41.5, 42.0), {"be": "", "en": "", "ru": ""}, "муніцыпалітэт — не населены пункт")
            # рускай назвы ў OpenStreetMap няма: сэрвіс аддае англійскую — яна рускай не лічыцца
            answers.update({"be,en": {"village": "Kedkedi"}, "en": {"village": "Kedkedi"}, "ru,en": {"village": "Kedkedi"}})
            self.assertEqual(geo.place_names(41.7, 42.0), {"be": "Kedkedi", "en": "Kedkedi", "ru": ""})
            # запіс кэша, зроблены да рускай мовы, дапаўняецца адным запытам
            cache = common.read_json(geo.GEOCODE_CACHE)
            cache["names:41.900,42.000"] = {"be": "Мілісі", "en": "Milisi"}
            common.write_json(geo.GEOCODE_CACHE, cache)
            answers["ru,en"] = {"village": "Милиси"}
            del asked[:]
            self.assertEqual(geo.place_names(41.9, 42.0), {"be": "Мілісі", "en": "Milisi", "ru": "Милиси"})
            self.assertEqual(asked, ["ru,en"])
            self.assertEqual(geo.place_names(41.9, 42.0)["ru"], "Милиси")
            self.assertEqual(asked, ["ru,en"], "другі раз — з кэша")
        self.assertEqual(sorted(common.read_json(geo.GEOCODE_CACHE)),
                         ["names:41.000,42.000", "names:41.500,42.000", "names:41.700,42.000", "names:41.900,42.000"])

    def test_without_home_there_is_no_drive(self):
        self.hike()
        for home in (None, {"name": "Дом"}, {"point": "Батумі"}, {"point": [91, 0]}, {"point": [float("nan"), 41]}):
            self.cfg = {**CFG, "photos_root": str(self.photos_root), "home": home}
            self.fetch(osm=[SPRING])
            self.assertNotIn("route", self.kinds(), home)
            index, out = self.build()
            self.assertIsNone(index["h"]["drive"], home)
            self.assertNotIn("запусціце `hike fetch`", out, "дарога без home не чакаецца")

    def test_hints_are_limited_and_tied_to_the_track(self):
        self.hike()
        days = practical.raw_days([1], 4)
        item = lambda i, north, east, kind="water": {"kind": kind, "type": "spring" if kind == "water" else "viewpoint",  # noqa: E731
                                                     "lat": 41.0 + north * M, "lng": 42.0 + east * M / 0.7547,
                                                     "name": {"be": "", "en": ""}, "osm": f"node/{i}"}
        many = [item(i, 100 * (10 - i), 0) for i in range(11)]  # праз кожныя 100 м першага кіламетра, у адваротным парадку
        self.assertEqual([q["pos"]["km"] for q in practical.osm_hints(many, days, None)], [i / 10 for i in range(11)])
        with unittest.mock.patch.object(practical, "OSM_MAX", 4):
            self.assertEqual([q["osm"] for q in practical.osm_hints(many, days, None)], ["node/10", "node/9", "node/8", "node/7"])
        # некалькі аднолькавых аб'ектаў на адной пляцоўцы — адна падказка, з назвай — важнейшая
        tables = [item(1, 500, 0, "place"), {**item(2, 520, 0, "place"), "name": {"be": "Пляцоўка", "en": "Site"}},
                  item(3, 540, 0, "place"), item(4, 530, 0), item(5, 700, 0, "place")]
        self.assertEqual([(q["osm"], q["kind"]) for q in practical.osm_hints(tables, days, None)],
                         [("node/2", "place"), ("node/4", "water"), ("node/5", "place")])
        near = lambda east, kind: len(practical.osm_hints([item(1, 500, east, kind)], days, None))  # noqa: E731
        self.assertEqual([near(-240, "water"), near(-260, "water"), near(-140, "place"), near(-160, "place")], [1, 0, 1, 0],
                         "вада — да 250 м ад трэку, іншыя месцы — да 150 м")
        self.assertEqual(practical.osm_hints(many, [], None), [])
        self.assertEqual(practical.osm_hints([], days, None), [])
        self.assertIsNone(practical.osm_bbox([]))


class TestReview160(FetchProject):
    """Рэгрэсіі па review_2026-10-11_v1.6.0.md."""

    def kinds(self):
        return [kind for kind, _ in self.calls]

    def two_days(self):
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))
        self.add_tour(2, "2025-06-02T04:00:00Z", path(*SQUARE))
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: [1, 2]\n", encoding="utf-8")

    def test_fetch_keeps_weather_of_days_whose_tour_is_missing(self):  # №1
        self.two_days()
        self.fetch()
        weather = lambda: common.read_json(build.CACHE / "weather" / "h.json")["days"]  # noqa: E731
        both = weather()
        self.assertEqual(sorted(both), ["2025-06-01", "2025-06-02"])
        published = [d["weather"] for d in self.build()[0]["h"]["days"]]

        tour = build.CACHE / "komoot" / "tours" / "2.json"
        saved = tour.read_text(encoding="utf-8")
        tour.unlink()
        self.fetch()
        self.assertEqual(weather(), both, "дзень, тура якога няма ў кэшы, застаецца")
        self.fetch(["--refresh"])
        self.assertEqual(self.kinds().count("weather"), 1, "перапытваецца толькі дзень, які ёсць у трэку")
        self.assertEqual(weather(), both)

        # тур без каардынат (няпоўны адказ Komoot) — тое самае
        common.write_json(tour, {**json.loads(saved), "coords": []})
        self.fetch()
        self.assertEqual(weather(), both)
        # паход увогуле без даступных тураў
        (build.CACHE / "komoot" / "tours" / "1.json").rename(build.CACHE / "komoot" / "tours" / "1.bak")
        self.fetch()
        self.assertEqual(weather(), both)
        (build.CACHE / "komoot" / "tours" / "1.bak").rename(build.CACHE / "komoot" / "tours" / "1.json")

        tour.write_text(saved, encoding="utf-8")
        index, out = self.build()
        self.assertEqual([d["weather"] for d in index["h"]["days"]], published, "пасля вяртання тура надвор'е на месцы")
        self.assertNotIn("надвор'я няма", out)

    def test_drive_back_is_routed_from_the_finish(self):  # №2
        line = path((0, 0), (3000, 0))
        self.add_tour(1, "2025-06-01T04:00:00Z", line)
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\n", encoding="utf-8")
        home = "41.59822,41.62692"
        there = {"code": "Ok", "routes": [{"distance": 10000.0, "duration": 600.0}], "waypoints": [{"distance": 5.0}, {"distance": 40.0}]}
        back = {"code": "Ok", "routes": [{"distance": 20000.0, "duration": 1200.0}], "waypoints": [{"distance": 900.0}, {"distance": 5.0}]}
        # у кэшы ўжо ёсць дарога дом → фініш (так яе лічыла версія 1.6.0): для звароту яна не падыходзіць
        old_key = "41.6269,41.5982>41.027,42.000"
        common.write_json(build.CACHE / "routes.json", {"routes": {old_key: {"km": 1.0, "minutes": 1}}})
        index, out = self.build()
        self.assertIsNone(index["h"]["drive"])

        self.fetch(route=lambda url: there if url.startswith(f"{fetch.API_ROUTE}{home};") else back)
        self.assertEqual([c for kind, c in self.calls if kind == "route"],
                         [f"{home};42.0,41.0", f"42.0,{line[-1][0]};{home}"], "другі запыт — ад фінішу дадому")
        routes = common.read_json(build.CACHE / "routes.json")["routes"]
        self.assertEqual(routes, {old_key: {"km": 1.0, "minutes": 1}, "41.6269,41.5982>41.000,42.000": {"km": 10.0, "minutes": 10},
                                  "41.027,42.000>41.6269,41.5982": {"km": 20.0, "minutes": 20, "off": 900}})
        index, out = self.build()
        self.assertEqual(index["h"]["drive"]["to"], {"km": 10.0, "minutes": 10})
        self.assertEqual(index["h"]["drive"]["back"], {"km": 20.0, "minutes": 20, "off": 900},
                         "лічбы і адхіленне ад дарогі — для напрамку ад фінішу")
        self.assertNotIn("запусціце `hike fetch`", out)
        self.fetch()
        self.assertNotIn("route", self.kinds(), "абодва напрамкі ўжо ў кэшы")

        # дарогі ад фінішу яшчэ няма: зборка кажа запусціць fetch, а не паказвае дарогу ў другі бок
        del routes["41.027,42.000>41.6269,41.5982"]
        common.write_json(build.CACHE / "routes.json", {"routes": routes})
        index, out = self.build()
        self.assertNotIn("back", index["h"]["drive"])
        self.assertIn("падказак OpenStreetMap або дарогі на машыне няма для паходаў: 1", out)

    def test_missing_hour_makes_weather_incomplete(self):  # №3
        self.hike()  # 04:00–08:00 UTC; пункты — на 04:00, 06:00 і 08:00

        def answer(hole):
            def weather(params):
                places = hourly_answer(params)
                for place in places:
                    hours = [datetime.fromtimestamp(t, timezone.utc).hour for t in place["hourly"]["time"]]
                    place["hourly"]["precipitation"] = [None if h == hole else 0.0 for h in hours]
                    place["hourly"]["weather_code"] = [2] * len(hours)
                return places
            return weather

        cached = lambda: common.read_json(build.CACHE / "weather" / "h.json")["days"]["2025-06-01"]  # noqa: E731
        _, out = self.fetch(weather=answer(5))
        self.assertIn("няпоўныя звесткі", out)
        self.assertEqual((cached()["complete"], "precip" in cached()["span"]), (False, False),
                         "сума з прапушчанай гадзінай — не «без ападкаў»")
        self.assertEqual([q["precip"] for q in cached()["points"]], [0.0, 0.0, 0.0], "самі пункты вядомыя")
        self.assertEqual(cached()["span"]["tempMax"], -2.0, "астатнія паказчыкі застаюцца")
        self.assertNotIn("precip", self.build()[0]["h"]["days"][0]["weather"]["span"])
        self.assertEqual(self.fetch(weather=answer(5))[0][0][0], fetch.API_WEATHER, "няпоўны адказ пытаецца зноў")

        self.fetch(weather=answer(None))
        self.assertEqual((cached()["complete"], cached()["span"]["precip"]), (True, 0.0), "поўны сухі адказ")
        self.assertEqual(self.fetch(weather=answer(None))[0], [])
        # прапушчаная гадзіна па-за часам паходу нічога не псуе
        self.fetch(["--refresh"], weather=answer(12))
        self.assertEqual((cached()["complete"], cached()["span"]["precip"]), (True, 0.0))

        # надвор'е, падлічанае па ранейшых правілах, да трэку ўжо не адносіцца і пытаецца зноў
        points = practical.weather_points(practical.raw_days([1], 4)[0])
        old_key = hashlib.sha1(json.dumps(points, sort_keys=True).encode()).hexdigest()[:12]
        self.assertNotEqual(practical.weather_key(points), old_key)
        common.write_json(build.CACHE / "weather" / "h.json", {"days": {"2025-06-01": {**cached(), "key": old_key}}})
        self.assertIn("надвор'я няма для паходаў: 1", self.build()[1])
        self.assertEqual(len(self.fetch()[0]), 1)

    def test_removed_track_drops_hints_and_drive(self):  # №4
        self.hike()
        self.fetch(osm=[SPRING])
        before = self.build()[0]["h"]
        self.assertEqual((len(before["hints"]), before["drive"]["to"]["km"]), (1, 42.1))
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: []\nlocation: [40, 43]\n", encoding="utf-8")
        for attempt in (1, 2):  # дзве зборкі запар: старое не вяртаецца з папярэдняга індэкса
            index, out = self.build()
            h = index["h"]
            self.assertEqual((h["track"], h["days"], h["point"], h["facts"]), (None, [], [43.0, 40.0], None), attempt)
            self.assertEqual((h["hints"], h["drive"]), ([], None), "без трэку няма ні падказак, ні дарогі да яго")
            self.assertNotIn("запусціце `hike fetch`", out)

    def test_non_potable_water_is_never_a_hint(self):  # №5
        item = lambda **tags: practical.osm_item({"type": "node", "id": 7, "lat": 41.0, "lon": 42.0, "tags": tags})  # noqa: E731
        for tags in ({"amenity": "water_point"}, {"amenity": "drinking_water"}, {"natural": "spring"}, {"man_made": "water_well"}):
            self.assertIsNone(item(**tags, drinking_water="no"), tags)
            self.assertEqual(item(**tags, drinking_water="yes")["kind"], "water", tags)
            self.assertEqual(item(**tags)["kind"], "water", tags)
        self.assertEqual(item(amenity="shelter", drinking_water="no")["type"], "shelter", "навес без вады — усё роўна навес")

        self.hike()
        bad = osm_node(99, 500, -100, amenity="water_point", drinking_water="no")
        self.fetch(osm=[bad])
        self.assertEqual(self.build()[0]["h"]["hints"], [])

        # кэш, сабраны па ранейшых правілах адбору, не выкарыстоўваецца і пытаецца зноў
        bbox = practical.osm_bbox(practical.raw_days([1], 4))
        old_key = hashlib.sha1(json.dumps([bbox, practical.OSM_QUERY]).encode()).hexdigest()[:12]
        self.assertNotEqual(practical.osm_key(bbox), old_key)
        common.write_json(build.CACHE / "osm" / "h.json", {"key": old_key, "items": [
            {"kind": "water", "type": "tap", "lat": bad["lat"], "lng": bad["lon"], "name": {"be": "", "en": ""}, "osm": "node/99"}]})
        index, out = self.build()
        self.assertEqual(index["h"]["hints"], [])
        self.assertIn("падказак OpenStreetMap або дарогі на машыне няма для паходаў: 1", out)
        self.fetch(osm=[bad, SPRING])
        self.assertEqual(self.kinds(), ["osm"])
        self.assertEqual([q["osm"] for q in self.build()[0]["h"]["hints"]], ["node/1"])


class TestReview161(FetchProject):
    """Рэгрэсіі па review_2026-10-11_v1.6.1.md."""

    def flat_tour(self, tid, iso, points, alt):
        """Тур на пастаяннай вышыні: хвіліна на кожны пункт."""
        self.add_tour(tid, iso, points, coords=[[lat, lng, alt, i * 60000] for i, (lat, lng) in enumerate(points)])

    def test_removed_track_drops_positions_of_authors_records(self):  # №1
        block = "practical:\n  places:\n    - {type: shelter, km: 1}\n  water:\n    sources:\n      - {type: spring, time: \"09:00\"}\n"
        self.hike(block)
        placed = lambda index: (index["h"]["practical"]["places"][0]["pos"], index["h"]["practical"]["water"]["sources"][0]["pos"])  # noqa: E731
        before = self.build()[0]
        self.assertEqual([q["km"] for q in placed(before)], [1.0, 1.0])

        # тура няма ў кэшы: трэк застаецца ранейшы, і месцы запісаў разам з ім
        tour = build.CACHE / "komoot" / "tours" / "1.json"
        saved = tour.read_text(encoding="utf-8")
        tour.unlink()
        self.assertEqual(placed(self.build()[0]), placed(before))
        tour.write_text(saved, encoding="utf-8")

        # трэк прыбраны з паходу: запісы застаюцца, але без месца на трэку, якога больш няма
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: []\nlocation: [40, 43]\n" + block, encoding="utf-8")
        for attempt in (1, 2):
            index, out = self.build()
            self.assertEqual((index["h"]["track"], index["h"]["point"]), (None, [43.0, 40.0]), attempt)
            self.assertEqual(placed(index), (None, None), "месца на выдаленым трэку не вяртаецца з папярэдняга індэкса")
            self.assertEqual(index["h"]["practical"]["places"][0]["type"], "shelter", "сам запіс застаецца")
            self.assertEqual(out.count("месца не вызначана — няма трэку"), 2)

    def test_place_on_a_sparse_track_is_projected_onto_the_segment(self):  # №3
        side = lambda east: {"kind": "water", "type": "spring", "lat": 41.0 + 500 * M, "lng": 42.0 + east * M / 0.7547,  # noqa: E731
                             "name": {"be": "", "en": ""}, "osm": f"node/{east}"}
        found = {}
        for name, step in (("sparse", 1000.0), ("dense", 20.0)):
            line = path((0, 0), (1000, 0), step=step)
            n = len(line) - 1  # роўны пад'ём з 1000 да 1400 м за 4 гадзіны — аднолькавы для абодвух запісаў
            self.add_tour(1, "2025-06-01T04:00:00Z", line,
                          coords=[[lat, lng, 1000 + 400 * i / n, i / n * 14400000] for i, (lat, lng) in enumerate(line)])
            days = practical.raw_days([1], 4)
            self.assertEqual(len(practical._points(days[0])), 2 if name == "sparse" else 51)
            pos, problem = practical.locate({"at": [41.0 + 500 * M, 42.0]}, days, {})
            self.assertIsNone(problem)
            found[name] = (pos["km"], pos.get("off"), pos["alt"], pos["time"],
                           [q["osm"] for q in practical.osm_hints([side(0), side(-240), side(-260)], days, None)])
        self.assertEqual(found["sparse"], (0.5, None, 1200, "10:00", ["node/-240", "node/0"]),
                         "месца на сцежцы паміж двума пунктамі: кіламетр, вышыня і час — па прамой")
        self.assertEqual(found["sparse"], found["dense"], "адбор не залежыць ад таго, наколькі часта запісаны пункты")
        off = practical.locate({"at": [41.0 + 500 * M, 42.0 - 240 * M / 0.7547]}, days, {})[0]["off"]
        self.assertAlmostEqual(off, 240, delta=2)

        # разрыў паміж турамі дня — не адрэзак маршруту
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (1000, 0), step=1000.0), duration=3600)
        self.add_tour(2, "2025-06-01T08:00:00Z", path((1000, 2000), (2000, 2000), step=1000.0))
        days = practical.raw_days([1, 2], 4)
        self.assertEqual(len(days[0]["segs"]), 2)
        gap = {**side(1000), "lat": 41.0 + 1000 * M}  # пасярэдзіне паміж фінішам першага тура і стартам другога
        self.assertEqual(practical.osm_hints([gap], days, None), [])
        pos = practical.locate({"at": [gap["lat"], gap["lng"]]}, days, {})[0]
        self.assertAlmostEqual(pos["off"], 1000, delta=5)
        self.assertEqual(practical._project(41.0, 42.0, []), None)

    def test_steepest_grade_does_not_span_separate_tours(self):  # №4
        self.flat_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (500, 0)), 1000)
        self.flat_tour(2, "2025-06-01T12:00:00Z", path((30000, 0), (30500, 0)), 2000)  # пасля пераезду, на 1000 м вышэй
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: [1, 2]\n", encoding="utf-8")
        h = self.build()[0]["h"]
        self.assertEqual(len(h["days"]), 1)
        for facts in (h["facts"], h["days"][0]["facts"]):
            self.assertNotIn("steepUp", facts, "розніца вышынь паміж турамі — не пад'ём")
            self.assertNotIn("steepDown", facts)
        # ухіл унутры аднаго тура па-ранейшаму знаходзіцца, і кіламетр пачатку лічыцца ад пачатку дня
        climb = path((30000, 0), (31000, 0))
        self.add_tour(2, "2025-06-01T12:00:00Z", climb,  # першыя 500 м — пад'ём 10 %, далей роўна
                      coords=[[lat, lng, 2000 + 2 * min(i, 25), i * 60000] for i, (lat, lng) in enumerate(climb)])
        facts = self.build()[0]["h"]["facts"]
        self.assertEqual((facts["steepUp"], "steepDown" in facts), ({"grade": 10, "km": 0.5}, False))

    def test_absent_hour_makes_weather_incomplete(self):  # працяг №3 з review 1.6.0
        self.hike()  # 04:00–08:00 UTC; пункты — на 04:00, 06:00 і 08:00

        def without(hour):
            def weather(params):
                places = hourly_answer(params)
                for place in places:
                    hourly = place["hourly"]
                    keep = [i for i, t in enumerate(hourly["time"]) if datetime.fromtimestamp(t, timezone.utc).hour != hour]
                    for name in hourly:
                        hourly[name] = [hourly[name][i] for i in keep]
                    hourly["precipitation"] = [0.0] * len(keep)
                return places
            return weather

        cached = lambda: common.read_json(build.CACHE / "weather" / "h.json")["days"]["2025-06-01"]  # noqa: E731
        _, out = self.fetch(weather=without(5))  # гадзіны 05:00 у адказе няма зусім
        self.assertIn("няпоўныя звесткі", out)
        self.assertEqual((cached()["complete"], "precip" in cached()["span"], "snow" in cached()["span"]), (False, False, False))
        self.assertEqual([q["temp"] for q in cached()["points"]], [-6.0, -8.0, -2.0], "паказчыкі ў пунктах ёсць")
        self.assertEqual(len(self.fetch(weather=without(5))[0]), 1, "няпоўны адказ пытаецца зноў")

        # няма гадзіны самога пункта: паказчык суседняй гадзіны за яго не выдаецца
        self.fetch(weather=without(6))
        self.assertEqual([q["temp"] for q in cached()["points"]], [-6.0, None, -2.0])
        self.assertFalse(cached()["complete"])

        self.fetch(weather=without(12))  # гадзіна па-за часам паходу не патрэбная
        self.assertEqual((cached()["complete"], cached()["span"]["precip"]), (True, 0.0))
        self.assertEqual(self.fetch(weather=without(12))[0], [])


class TestReview162(FetchProject):
    """Рэгрэсіі па review_2026-10-11_v1.6.2.md."""

    def rising_tour(self, tid, iso, points, low=1000, high=1400, minutes=240):
        """Тур з роўным пад'ёмам і роўным ходам — аднолькавы для любой шчыльнасці пунктаў."""
        n = len(points) - 1
        self.add_tour(tid, iso, points, coords=[[lat, lng, low + (high - low) * i / n, i / n * minutes * 60000]
                                                for i, (lat, lng) in enumerate(points)])

    def test_km_is_placed_on_the_track_not_on_the_nearest_recorded_point(self):  # №1
        block = "practical:\n  places:\n    - {type: shelter, km: 0.4}\n    - {type: table, km: 1.2}\n    - {type: camp, km: 1.5}\n" \
                "  water:\n    sources:\n      - {type: spring, km: 0.3}\n"
        found = {}
        for name, step in (("sparse", 1000.0), ("dense", 20.0)):
            self.rising_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (1000, 0), step=step))
            (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\n" + block, encoding="utf-8")
            index, out = self.build()
            p = index["h"]["practical"]
            spring, (shelter, table, camp) = p["water"]["sources"][0]["pos"], [q["pos"] for q in p["places"]]
            for pos, metres in ((spring, 300), (shelter, 400)):
                self.assertAlmostEqual(pos["at"][1], 41.0 + metres * M, delta=3e-5, msg=name)  # да 3 м
                self.assertEqual(pos["at"][0], 42.0, name)
            found[name] = [(q["km"], q["alt"], q["time"]) for q in (spring, shelter, table)]
            self.assertIsNone(camp, name)
            self.assertIn("кіламетр km: 1.5 па-за трэкам дня 1 (0–1.0 км)", out)
        self.assertEqual(found["sparse"], [(0.3, 1120, "09:12"), (0.4, 1160, "09:36"), (1.0, 1400, "12:00")],
                         "зададзены кіламетр, вышыня і час — па прамой; кіламетр адразу за фінішам — фініш")
        self.assertEqual(found["sparse"], found["dense"], "вынік не залежыць ад шчыльнасці запісу")

        # мяжа тураў дня: кіламетр у іх агульны, а вышыня — свайго тура
        self.rising_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (1000, 0), step=1000.0), 1000, 1000, 60)
        self.rising_tour(2, "2025-06-01T12:00:00Z", path((30000, 0), (31000, 0), step=1000.0), 2000, 2000, 60)
        day = practical.raw_days([1, 2], 4)[0]
        edge = day["segs"][0][-1][4]
        self.assertEqual(day["segs"][1][0][4], edge)
        self.assertEqual([practical._at_km(day, km)[2] for km in (edge / 2, edge, edge * 1.5, edge * 2)], [1000, 1000, 2000, 2000])

    def test_route_type_ignores_the_transfer_between_tours(self):  # №2
        def day_type(*tours):
            for tid, line in enumerate(tours, 1):
                self.add_tour(tid, f"2025-06-01T{4 * tid:02d}:00:00Z", line, duration=3600)
            ids = list(range(1, len(tours) + 1))
            (self.content / "h.yaml").write_text(f"date: 2025-06-01\ntitle: {{be: H}}\nkomoot: {ids}\n", encoding="utf-8")
            h = self.build()[0]["h"]
            self.assertEqual((len(h["days"]), h["facts"]["type"]), (1, h["days"][0]["facts"]["type"]))
            return h["facts"]["type"]

        there_and_back = lambda north: path((north, 0), (north + 1000, 0), (north, 0))  # noqa: E731
        self.assertEqual(day_type(there_and_back(0), there_and_back(30000)), "out_and_back",
                         "дзве вылазкі туды і назад пасля пераезду — не шлях з пункта А ў пункт Б")
        self.assertEqual(day_type(there_and_back(0), path(*[(n + 30000, e) for n, e in SQUARE])), "mixed", "вылазкі розных тыпаў")
        self.assertEqual(day_type(path((0, 0), (1000, 0)), path((1000, 0), (0, 0))), "out_and_back",
                         "перапынак у запісе без пераезду: гэта адзін маршрут")
        self.assertEqual(day_type(path((0, 0), (1000, 0)), path((1300, 0), (2300, 0))), "point_to_point",
                         "працяг за 300 м ад папярэдняга фінішу — той жа маршрут")

        # тое ж для дзён паходу: начлег на месцы злучае дні, пераезд — раздзяляе
        days = lambda *lines: [{"segs": [[(lat, lng, None, None, 0.0) for lat, lng in line]]} for line in lines]  # noqa: E731
        hike_type = lambda *lines: practical.hike_route_type(days(*lines), [practical.route_type(line) for line in lines])  # noqa: E731
        self.assertEqual(hike_type(path((0, 0), (3000, 0)), path((3000, 0), (0, 0))), "out_and_back")
        self.assertEqual(hike_type(there_and_back(0), there_and_back(30000)), "out_and_back")
        self.assertEqual(hike_type(there_and_back(0), path(*[(n + 30000, e) for n, e in SQUARE])), "mixed")
        self.assertIsNone(practical.parts_route_type([[], [(41.0, 42.0)]]))

    def test_route_type_does_not_depend_on_point_density(self):  # №3
        shapes = {
            "out_and_back": [(0, 0), (1000, 0), (0, 0)],
            "loop": SQUARE,
            "mixed": [(0, 0), (1500, 0), (2000, 0), (2000, 500), (1500, 500), (1500, 0), (0, 0)],  # ножка 1,5 км + кола 2 км
            "point_to_point": [(0, 0), (1500, 0), (1500, 1500)],
        }

        def uneven(corners):  # частка адрэзкаў запісана часта, частка — толькі канцамі
            line = []
            for i, (a, b) in enumerate(zip(corners, corners[1:])):
                line += path(a, b, step=(20.0, 5000.0, 170.0)[i % 3])[:-1]
            return line + path(corners[-1], corners[-1])[-1:]

        for name, corners in shapes.items():
            variants = {"dense": path(*corners), "sparse": path(*corners, step=5000.0), "uneven": uneven(corners)}
            self.assertEqual(len(variants["sparse"]), len(corners), "толькі вуглы")
            self.assertEqual({density: practical.route_type(line) for density, line in variants.items()},
                             dict.fromkeys(variants, name), name)
            shares = [practical.overlap_share(line) for line in variants.values()]
            self.assertLess(max(shares) - min(shares), 0.03, f"{name}: доля паўторнага шляху {shares}")
        # выбарка — роўна праз 30 м шляху з канцавым пунктам, незалежна ад запісаных пунктаў
        for line in (path((0, 0), (1000, 0)), path((0, 0), (1000, 0), step=1000.0)):
            samples, length = practical._resample(line)
            self.assertEqual(len(samples), 35)
            self.assertAlmostEqual(samples[1][2], 30.0, places=6)
            self.assertAlmostEqual(samples[-1][2], length, places=6)
            self.assertEqual(samples[-1][:2], line[-1])

    def test_weather_request_covers_the_hours_around_midnight_utc(self):  # №4
        for start, finish_hour in (("2025-06-01T19:30:00Z", "23:30"), ("2025-06-01T19:50:00Z", "23:50"), ("2025-06-01T20:10:00Z", "00:10")):
            self.add_tour(1, start, path(*SQUARE))  # чатыры гадзіны: фініш каля поўначы UTC
            (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\n", encoding="utf-8")
            calls, out = self.fetch()
            self.assertEqual([(c[1]["start_date"], c[1]["end_date"]) for c in calls], [("2025-06-01", "2025-06-02")], finish_hour)
            self.assertNotIn("няпоўныя звесткі", out, finish_hour)
            day = common.read_json(build.CACHE / "weather" / "h.json")["days"]["2025-06-01"]
            self.assertTrue(day["complete"], finish_hour)
            self.assertEqual((day["points"][-1]["temp"] is None, "precip" in day["span"]), (False, True), finish_hour)
            self.assertEqual(self.fetch()[0], [], f"фініш у {finish_hour} UTC: поўны адказ не пытаецца зноў")
            self.assertIn("weather", self.build()[0]["h"]["days"][0])
        # старт адразу пасля поўначы UTC папярэдняй даты не патрабуе
        self.add_tour(1, "2025-06-02T00:10:00Z", path(*SQUARE))
        calls, _ = self.fetch()
        self.assertEqual([(c[1]["start_date"], c[1]["end_date"]) for c in calls], [("2025-06-02", "2025-06-02")])
        points = practical.weather_points(practical.raw_days([1], 4)[0])
        hours = practical.weather_hours(points)
        self.assertEqual((hours[0] % 3600, hours[0] <= points[0]["ts"] + 1800, hours[-1] >= points[-1]["ts"] - 1800), (0, True, True))


class TestRussian(FetchProject):
    """Руская мова: тэкст {be, en, ru}, рэгіёны, назвы населеных пунктаў і падказак, чарнавік паходу."""

    def test_text_in_three_languages(self):
        self.assertEqual(common.text_pair({"be": " А ", "en": "A", "ru": " Б "}), {"be": "А", "en": "A", "ru": "Б"})
        self.assertEqual(common.text_pair({"be": "А", "ru": "  "}), {"be": "А", "en": ""}, "пустога рускага тэксту няма")
        self.assertEqual(common.text_pair("А"), {"be": "А", "en": ""})
        self.assertEqual(common.text_pair({"ru": "Б"}), {"be": "", "en": "", "ru": "Б"})
        with self.assertRaises(AttributeError):
            common.text_pair({"be": "А", "ru": 5})

    def test_build_publishes_russian_texts_and_regions(self):
        self.cfg = {**self.cfg, "home": {"name": {"be": "Батумі", "ru": "Батуми", "en": "Batumi"}, "point": [41.62692, 41.59822]}}
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))
        (self.content / "h.yaml").write_text("""\
date: 2025-06-01
komoot: [1]
title: {be: Возера, ru: Озеро, en: Lake}
region: {be: Аджарыя, en: Adjara}
impressions: {be: Добра., ru: Хорошо.}
practical:
  surface: {be: Грунт., ru: Грунт и камни.}
  getting_there:
    - {type: taxi, to: {be: Мілісі, ru: Милиси}}
  places:
    - {type: shelter, km: 1, name: {be: Альтанка, ru: Беседка}, note: {be: Стол., ru: Стол и скамейки.}}
""", encoding="utf-8")
        self.fetch(names={"be": "Мілісі", "en": "Milisi", "ru": "Милиси"},
                   osm=[osm_node(1, 500, -100, natural="spring", name="Tsqaro", **{"name:ru": "Тёплый"}), VIEW])
        h = self.build()[0]["h"]
        self.assertEqual(h["title"], {"be": "Возера", "en": "Lake", "ru": "Озеро"})
        self.assertEqual(h["region"], {"be": "Аджарыя", "en": "Adjara", "ru": "Аджария"}, "руская назва рэгіёна — з табліцы")
        self.assertEqual(h["impressions"], {"be": "Добра.", "en": "", "ru": "Хорошо."})
        p = h["practical"]
        self.assertEqual(p["notes"]["surface"]["ru"], "Грунт и камни.")
        self.assertEqual((p["gettingThere"][0]["to"]["ru"], p["gettingThere"][0]["from"]["ru"]), ("Милиси", "Батуми"),
                         "адкуль — па змаўчанні home з config.yaml")
        self.assertEqual((p["places"][0]["name"]["ru"], p["places"][0]["note"]["ru"]), ("Беседка", "Стол и скамейки."))
        self.assertEqual(h["drive"]["from"], {"be": "Батумі", "en": "Batumi", "ru": "Батуми"})
        for facts in (h["facts"], h["days"][0]["facts"]):
            self.assertEqual(facts["start"]["place"], {"be": "Мілісі", "en": "Milisi", "ru": "Милиси"})
        self.assertEqual([q["name"] for q in h["hints"]],
                         [{"be": "Tsqaro", "en": "Tsqaro", "ru": "Тёплый"}, {"be": "", "en": ""}], "руская назва — толькі калі яна ёсць")

        # рэгіён з рускай назвай у файле — яна і застаецца; невядомы рэгіён — без рускай
        self.assertEqual(geo.region_with_ru({"be": "Аджарыя", "en": "Adjara", "ru": "Аджарская АР"})["ru"], "Аджарская АР")
        self.assertEqual(geo.region_with_ru({"be": "Гурыя", "en": ""}), {"be": "Гурыя", "en": "", "ru": "Гурия"})
        self.assertEqual(geo.region_with_ru({"be": "Альпы", "en": "Alps"}), {"be": "Альпы", "en": "Alps"})
        self.assertEqual(geo.region_with_ru({"be": "", "en": ""}), {"be": "", "en": ""})

    def test_old_name_cache_gets_russian_names_with_one_request(self):
        self.hike()
        common.write_json(geo.GEOCODE_CACHE, {"names:41.000,42.000": {"be": "Мілісі", "en": "Milisi"}})
        self.assertNotIn("ru", self.build()[0]["h"]["facts"]["start"]["place"], "да `hike fetch` рускай назвы няма")
        _, out = self.fetch(names={"be": "Мілісі", "en": "Milisi", "ru": "Милиси"})
        self.assertEqual([c for kind, c in self.calls if kind == "names"], [(41.0, 42.0, "ru,en", 14)])
        self.assertIn("Назваў месцаў: 1.", out)
        self.assertEqual(self.build()[0]["h"]["facts"]["start"]["place"], {"be": "Мілісі", "en": "Milisi", "ru": "Милиси"})
        self.fetch()
        self.assertNotIn("names", [kind for kind, _ in self.calls])
        # рускай назвы няма — пытанне не паўтараецца, на сайце яе таксама няма
        common.write_json(geo.GEOCODE_CACHE, {"names:41.000,42.000": {"be": "Kedkedi", "en": "Kedkedi"}})
        self.fetch(names={"be": "Kedkedi", "en": "Kedkedi"})
        self.assertEqual(common.read_json(geo.GEOCODE_CACHE)["names:41.000,42.000"], {"be": "Kedkedi", "en": "Kedkedi", "ru": ""})
        self.fetch(names={"be": "Kedkedi", "en": "Kedkedi"})
        self.assertNotIn("names", [kind for kind, _ in self.calls])
        self.assertEqual(self.build()[0]["h"]["facts"]["start"]["place"], {"be": "Kedkedi", "en": "Kedkedi"})

    def test_region_and_draft_have_russian(self):
        with unittest.mock.patch.object(geo, "_nominatim", lambda lat, lng, lang, zoom=5: {"ISO3166-2-lvl4": "GE-GU"}):
            self.assertEqual(geo.region_for_point(41.9, 42.1), {"be": "Гурыя", "en": "Guria", "ru": "Гурия"})
        # рэгіён у кэшы з часоў да рускай мовы
        common.write_json(geo.GEOCODE_CACHE, {"41.60,41.60": {"be": "Аджарыя", "en": "Adjara"}})
        self.assertEqual(geo.region_for_point(41.6, 41.6), {"be": "Аджарыя", "en": "Adjara", "ru": "Аджария"})
        abroad = {"en": {"state": "Tyrol", "country": "Austria"}, "be": {"country": "Аўстрыя"}, "ru": {"state": "Тироль"}}
        with unittest.mock.patch.object(geo, "_nominatim", lambda lat, lng, lang, zoom=5: abroad[lang]):
            self.assertEqual(geo.region_for_point(47.2, 11.4), {"be": "Аўстрыя", "en": "Tyrol", "ru": "Тироль"})
        self.assertEqual(len({names[2] for names in geo.REGIONS.values()}), len(geo.REGIONS), "у кожнага рэгіёна свая руская назва")

        draft = scaffold.render_yaml({"start": date(2025, 1, 2), "title": "Гара", "folder": None, "tours": [], "videos": [],
                                      "region": {"be": "Гурыя", "en": "Guria", "ru": "Гурия"}}, "2025-01-03")
        parsed = yaml.safe_load(draft)
        self.assertEqual(parsed["title"], {"be": "Гара", "ru": "", "en": ""})
        self.assertEqual(parsed["region"], {"be": "Гурыя", "ru": "Гурия", "en": "Guria"})
        self.assertEqual(sorted(parsed["impressions"]), ["be", "en", "ru"])
        old = scaffold.render_yaml({"start": date(2025, 1, 2), "title": "Гара", "folder": None, "tours": [], "videos": [],
                                    "region": {"be": "Гурыя", "en": "Guria"}}, "2025-01-03")
        self.assertEqual(yaml.safe_load(old)["region"]["ru"], "", "рэгіён без рускай назвы не ламае чарнавік")


class TestReview170(FetchProject):
    """Рэгрэсіі па review_2026-10-13_v1.7.0.md."""

    def profile_tour(self, points, alt, minutes=240):
        """Тур з роўным ходам і вышынёй `alt(метраў на поўнач ад пачатку)` у кожным пункце."""
        n = len(points) - 1
        self.add_tour(1, "2025-06-01T04:00:00Z", points,
                      coords=[[lat, lng, alt((lat - 41.0) / M), i / n * minutes * 60000] for i, (lat, lng) in enumerate(points)])

    def test_notes_written_only_in_russian_are_published(self):  # №1
        self.hike()
        keys = practical.NOTE_KEYS
        notes = "".join(f"  {key}: {{ru: 'Заметка {key}.'}}\n" for key in keys)
        (self.content / "h.yaml").write_text(
            "date: 2025-06-01\ntitle: {ru: Поход}\nkomoot: [1]\npractical:\n  weather_notes: {ru: Туман.}\n" + notes, encoding="utf-8")
        index, out = self.build()
        p = index["h"]["practical"]
        self.assertIsNotNone(p, "звесткі толькі па-руску — таксама звесткі")
        self.assertEqual(sorted(p["notes"]), sorted(keys))
        self.assertEqual(p["notes"]["dangers"], {"be": "", "en": "", "ru": "Заметка dangers."})
        self.assertEqual(p["weatherNotes"], {"be": "", "en": "", "ru": "Туман."})
        self.assertEqual(index["h"]["title"], {"be": "", "en": "", "ru": "Поход"})
        self.assertNotIn("practical:", out, "папярэджанняў няма")

        # пустыя пераклады тэкстам не лічацца: без іншых звестак блока няма
        (self.content / "h.yaml").write_text(
            "date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\npractical:\n  weather_notes: {be: '', ru: '  '}\n"
            "  dangers: {ru: ''}\n  surface: {en: ' '}\n", encoding="utf-8")
        self.assertIsNone(self.build()[0]["h"]["practical"])
        # адно поле на адной мове — і блок ёсць
        (self.content / "h.yaml").write_text(
            "date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\npractical:\n  dangers: {ru: Брод после дождя.}\n", encoding="utf-8")
        self.assertEqual(self.build()[0]["h"]["practical"]["notes"], {"dangers": {"be": "", "en": "", "ru": "Брод после дождя."}})

        # назва населенага пункта толькі па-руску таксама паказваецца
        common.write_json(geo.GEOCODE_CACHE, {"names:41.000,42.000": {"be": "", "en": "", "ru": "Милиси"}})
        self.assertEqual(self.build()[0]["h"]["facts"]["start"]["place"], {"be": "", "en": "", "ru": "Милиси"})

    def test_time_and_photo_are_placed_between_recorded_points(self):  # №3
        found = {}
        for name, step in (("sparse", 1000.0), ("dense", 20.0)):
            self.profile_tour(path((0, 0), (1000, 0), step=step), lambda north: 1000 + 0.4 * north)  # 08:00–12:00, 1000 → 1400 м
            days = practical.raw_days([1], 4)
            shots = {"A.jpg": "2025-06-01T09:36:00", "Early.jpg": "2025-06-01T07:45:00", "Late.jpg": "2025-06-01T12:20:00"}
            got = []
            for spec in ({"time": "09:12"}, {"photo": "A.jpg"}, {"photo": "Early.jpg"}, {"photo": "Late.jpg"}):
                pos, problem = practical.locate(spec, days, shots)
                self.assertIsNone(problem, (name, spec))
                got.append((pos["km"], pos["alt"], pos["time"], round((pos["at"][1] - 41.0) / M / 10) * 10))
            found[name] = got
        self.assertEqual(found["sparse"], [(0.3, 1120, "09:12", 300), (0.4, 1160, "09:36", 400),
                                           (0.0, 1000, "08:00", 0), (1.0, 1400, "12:00", 1000)],
                         "месца, вышыня і кіламетр — на шуканы момант; каля мяжы запісу — яе крайні пункт")
        self.assertEqual(found["sparse"], found["dense"], "вынік не залежыць ад шчыльнасці запісу")

        # поўная зборка на разрэджаным трэку
        self.profile_tour(path((0, 0), (1000, 0), step=1000.0), lambda north: 1000 + 0.4 * north)
        (self.content / "h.yaml").write_text(
            "date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\npractical:\n  places:\n"
            "    - {type: shelter, time: \"09:12\"}\n    - {type: table, time: \"09:36\"}\n", encoding="utf-8")
        index, out = self.build()
        self.assertEqual([(q["pos"]["km"], q["pos"]["alt"], q["pos"]["time"]) for q in index["h"]["practical"]["places"]],
                         [(0.3, 1120, "09:12"), (0.4, 1160, "09:36")])
        self.assertNotIn("месца не вызначана", out)

        # паміж рознымі турамі месца не выдумляецца
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (1000, 0), step=1000.0), duration=3600)   # 08:00–12:00
        self.add_tour(2, "2025-06-01T12:00:00Z", path((30000, 0), (31000, 0), step=1000.0))               # 16:00–20:00
        days = practical.raw_days([1, 2], 4)
        pos, problem = practical.locate({"time": "14:00"}, days, {})
        self.assertEqual((pos, "па-за часам трэку" in problem), (None, True))
        self.assertEqual(practical.locate({"time": "18:00"}, days, {})[0]["km"], 1.5, "сярэдзіна другога тура")

    def test_steepest_grade_uses_an_exact_500_m_window(self):  # №4
        climb = lambda north: 1000 + 200 * min(north, 250) / 250            # 200 м набору на першых 250 м, далей роўна  # noqa: E731
        drop = lambda north: 1200 - 200 * max(north - 750, 0) / 250          # роўна, на апошніх 250 м — спуск на 200 м  # noqa: E731
        found = {}
        for name, step in (("sparse", 5000.0), ("dense", 10.0)):
            got = []
            for corners, alt in (([(0, 0), (250, 0), (1000, 0)], climb), ([(0, 0), (750, 0), (1000, 0)], drop)):
                self.profile_tour(path(*corners, step=step), alt)
                day = practical.raw_days([1], 4)[0]
                self.assertEqual(len(practical._points(day)), 3 if name == "sparse" else 101)
                got.append(practical._steepest(day["segs"]))
            found[name] = got
        self.assertEqual(found["sparse"], [({"grade": 40, "km": 0.0}, None), (None, {"grade": 40, "km": 0.5})],
                         "перапад 200 м на 500 м — 40 %, які б рэдкі ні быў запіс")
        self.assertEqual(found["sparse"], found["dense"])

        # тур карацейшы за 500 м ухілу «на 500 м» не мае; у зборцы — тое ж
        self.profile_tour(path((0, 0), (400, 0)), lambda north: 1000 + north)
        day = practical.raw_days([1], 4)[0]
        self.assertEqual(practical._steepest(day["segs"]), (None, None))
        self.profile_tour(path((0, 0), (250, 0), (1000, 0), step=5000.0), climb)
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\n", encoding="utf-8")
        facts = self.build()[0]["h"]["facts"]
        self.assertEqual((facts["steepUp"], "steepDown" in facts), ({"grade": 40, "km": 0.0}, False))


class TestReview171(FetchProject):
    """Рэгрэсіі па review_2026-10-13_v1.7.1.md."""

    def test_wrongly_written_coordinates_are_not_guessed(self):  # №1
        bad = ['"41.53, 41.82"', "[true, false]", "[41.005]", "[41.005, 42.0, 900]", '["41.005", "42.0"]', "[91, 42]",
               "[41, 181]", "[.nan, 42]", "{lat: 41.005, lng: 42.0}", "41.005"]
        self.hike("practical:\n  places:\n" + "".join(f"    - {{type: shelter, at: {value}}}\n" for value in bad)
                  + "    - {type: table, at: [41.005, 42.0]}\n    - {type: camp, at: [41, 42]}\n"
                  "  water:\n    sources:\n      - {type: spring, at: \"41.53, 41.82\"}\n      - {type: tap, at: [true, false]}\n")
        index, out = self.build()
        p = index["h"]["practical"]
        self.assertEqual(len(p["places"]), len(bad) + 2, "запісы застаюцца")
        self.assertEqual([q["pos"] for q in p["places"][:len(bad)]], [None] * len(bad), "няправільна запісаныя каардынаты — без месца")
        self.assertEqual([q["pos"] for q in p["water"]["sources"]], [None, None])
        self.assertEqual(out.count("няправільныя каардынаты at:"), len(bad) + 2, "пра кожны такі запіс ёсць папярэджанне")
        self.assertIn("places[1]: месца не вызначана — няправільныя каардынаты at: 41.53, 41.82 (трэба [шырата, даўгата])", out)
        good, whole = p["places"][-2]["pos"], p["places"][-1]["pos"]
        self.assertEqual((good["at"], good["km"], whole["at"], whole["km"]), ([42.0, 41.005], 0.6, [42.0, 41.0], 0.0),
                         "правільная пара — і цэлыя лікі — працуюць як раней")
        # сама праверка формы запісу
        for value in bad:
            self.assertIsNone(common.lat_lng(yaml.safe_load(value)), value)
        self.assertEqual([common.lat_lng(v) for v in ([41.005, 42.0], (41, 42), [-90, 180], [0, 0])],
                         [(41.005, 42.0), (41.0, 42.0), (-90.0, 180.0), (0.0, 0.0)])

    def test_home_point_and_location_must_be_a_pair_of_numbers(self):
        self.hike()
        for point in ("41.62692, 41.59822", [True, False], [41.62692], [41.62692, 41.59822, 0], ["41.6", "41.5"]):
            self.assertIsNone(practical.home_point({"point": point}), point)
            self.cfg = {**CFG, "photos_root": str(self.photos_root), "home": {"name": "Дом", "point": point}}
            self.fetch()
            self.assertNotIn("route", [kind for kind, _ in self.calls], point)
            self.assertIsNone(self.build()[0]["h"]["drive"], point)
        self.assertEqual(practical.home_point({"point": [41.62692, 41.59822]}), (41.62692, 41.59822))

        # паход без трэку: няправільны location не становіцца кропкай за тысячы кіламетраў і не спыняе зборку
        for value in ('"41.65, 41.87"', "[true, false]", "[41.65]", "[41.65, 41.87, 5]"):
            (self.content / "h.yaml").write_text(f"date: 2025-06-01\ntitle: {{be: H}}\nlocation: {value}\n", encoding="utf-8")
            index, out = self.build()
            self.assertIsNone(index["h"]["point"], value)
            self.assertIn("няправільны location:", out, value)
            self.assertIn("паход не з'явіцца на карце", out, value)
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nlocation: [41.65, 41.87]\n", encoding="utf-8")
        index, out = self.build()
        self.assertEqual(index["h"]["point"], [41.87, 41.65])
        self.assertNotIn("location", out)


class TestReview172(FetchProject):
    """Рэгрэсіі па review_2026-10-13_v1.7.2.md."""

    def test_wrong_travel_time_is_not_published_as_a_duration(self):  # №1
        bad = ['"1:70"', '"0:99"', '"1:5"', '"1:²5"', '"1:10:00"', '"-1:10"', '"каля гадзіны"', "-5", "true", "[1, 10]"]
        good = {'"1:10"': 70, "70": 70, "1:10": 70, '"24:00"': 1440, '"0:05"': 5, '" 2:30 "': 150, "0": 0, "45.4": 45}
        ways = "".join(f"    - {{type: taxi, to: Мілісі, time: {value}}}\n" for value in [*bad, *good])
        self.hike("practical:\n  getting_there:\n" + ways)
        index, out = self.build()
        got = index["h"]["practical"]["gettingThere"]
        self.assertEqual(len(got), len(bad) + len(good), "запісы застаюцца")
        self.assertEqual([w["minutes"] for w in got[:len(bad)]], [None] * len(bad), "няправільны час не становіцца працягласцю")
        self.assertEqual([w["minutes"] for w in got[len(bad):]], list(good.values()))
        self.assertEqual(out.count("getting_there.time: чакаюцца хвіліны або «г:хх» з хвілінамі 00–59"), len(bad),
                         "пра кожны няправільны час ёсць папярэджанне")
        self.assertIn("а не «1:70»", out)
        self.assertEqual({w["to"]["be"] for w in got}, {"Мілісі"}, "астатнія палі запісу на месцы")


class TestPhotoPlaces(PracticalProject):
    """Месцы здымкі фота для значкоў на карце: па GPS фота каля трэку або па часе здымкі на трэку."""

    FOLDER = "20250601_Паход"

    def hike(self, extra=""):
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))  # 08:00–12:00 мясцовага часу, квадрат 4 км
        (self.content / "h.yaml").write_text(
            f"date: 2025-06-01\ntitle: {{be: H}}\nkomoot: [1]\nphotos_folder: {self.FOLDER}\n{extra}", encoding="utf-8")

    def test_places_by_shooting_time_and_gps(self):
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))
        days = practical.raw_days([1], 4)
        gps = lambda north, east: [round(42.0 + east * M / 0.7547, 5), round(41.0 + north * M, 5)]  # [даўгата, шырата]  # noqa: E731
        entry = lambda name, original=None, at=None: (f"{name}.jpg", {"name": name, "original": original, "gps": at})  # noqa: E731
        manifest = dict([
            entry("time", "2025-06-01T09:00:00"),                      # гадзіна ад старту — 1 км
            entry("gps", "2025-06-01T09:00:00", gps(500, 1050)),       # GPS за 50 м ад трэку каля 2,5 км: дакладнейшы за час
            entry("far", "2025-06-01T10:00:00", gps(9000, 9000)),      # GPS далёка ад трэку (стары адбітак) — месца па часе
            entry("camp", None, gps(-40, 0)),                          # без часу, GPS каля старту
            entry("home", None, gps(50000, 0)),                        # без часу, GPS далёка — не на маршруце
            entry("early", "2025-06-01T06:00:00"),                     # за дзве гадзіны да старту
            entry("eve", "2025-05-31T19:00:00"),                       # вечар напярэдадні
            entry("month", "2025-07-01T09:00:00"),                     # у камеры не той месяц
            entry("plain"),                                            # ні часу, ні GPS
            entry("bad", "учора", [200, 95]),                          # няправільныя значэнні
        ])
        photos = [{"src": f"photos/h/{m['name']}.webp", "thumb": f"photos/h/{m['name']}-t.webp", "w": 4, "h": 3} for m in manifest.values()]
        places = dict(zip((m["name"] for m in manifest.values()), practical.photo_places(photos, manifest, days)))
        self.assertEqual(places["time"], {"day": 0, "km": 1.0, "at": [42.0, round(41.0 + 1000 * M, 5)], "alt": 1200, "time": "09:00"})
        self.assertEqual(places["gps"], {"day": 0, "km": 2.5, "at": gps(500, 1050), "alt": 1300, "time": "09:00", "off": 50},
                         "месца — сам пункт GPS, кіламетр — бліжэйшага месца трэку, час — час здымкі")
        self.assertEqual((places["far"]["km"], places["far"]["time"], "off" in places["far"]), (2.0, "10:00", False))
        self.assertEqual(places["camp"], {"day": 0, "km": 0.0, "at": gps(-40, 0), "alt": 1000, "off": 40}, "без часу здымкі — без часу")
        self.assertEqual([places[name] for name in ("home", "early", "eve", "month", "plain", "bad")], [None] * 6,
                         "месца не выдумляецца")
        self.assertEqual(practical.photo_places(photos, manifest, []), [None] * len(photos), "без трэку месцаў няма")
        self.assertEqual(practical.photo_places(photos, {}, days), [None] * len(photos), "без маніфеста — таксама")

        # шматдзённы паход «туды і назад»: дзень бярэцца з часу здымкі, а не з бліжэйшага па GPS дня
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (3000, 0)))
        self.add_tour(2, "2025-06-02T04:00:00Z", path((3000, 0), (0, 0)))
        two = practical.raw_days([1, 2], 4)
        back = {"b.jpg": {"name": "b", "original": "2025-06-02T09:00:00", "gps": gps(2200, 20)}}
        pos = practical.photo_places([{"src": "photos/h/b.webp", "thumb": "photos/h/b-t.webp"}], back, two)[0]
        self.assertEqual((pos["day"], pos["km"], pos["time"]), (1, 0.8, "09:00"))

    def test_build_publishes_photo_places_and_keeps_them_with_the_track(self):
        self.hike()
        for name, taken in (("A.jpg", "2025:06:01 09:00:00"), ("B.jpg", "2025:06:01 11:00:00"), ("Eve.jpg", "2025:05:31 19:00:00")):
            self.photo(self.FOLDER, name, taken)
        index, out = self.build()
        photos = index["h"]["photos"]
        self.assertEqual([Path(p["src"]).stem.split("-")[0] for p in photos], ["eve", "a", "b"], "парадак галерэі — па часе")
        self.assertEqual([p.get("pos") and (p["pos"]["km"], p["pos"]["time"]) for p in photos], [None, (1.0, "09:00"), (3.0, "11:00")])
        self.assertEqual(set(photos[1]), {"src", "thumb", "w", "h", "pos"})
        self.assertEqual(set(photos[0]), {"src", "thumb", "w", "h"}, "фота без месца — без поля pos")
        placed = [p.get("pos") for p in photos]

        # дыск з фота недаступны: месцы лічацца з маніфеста, як і раней
        self.cfg = {**self.cfg, "photos_root": str(self.root / "няма")}
        self.assertEqual([p.get("pos") for p in self.build()[0]["h"]["photos"]], placed)
        # тура няма ў кэшы: на сайце застаецца ранейшы трэк, а з ім і месцы фота
        tour = build.CACHE / "komoot" / "tours" / "1.json"
        saved = tour.read_text(encoding="utf-8")
        tour.unlink()
        for attempt in (1, 2):
            self.assertEqual([p.get("pos") for p in self.build()[0]["h"]["photos"]], placed, attempt)
        tour.write_text(saved, encoding="utf-8")
        self.cfg = {**self.cfg, "photos_root": str(self.photos_root)}

        # трэк змяніўся: месцы пералічваюцца (старт на гадзіну пазней — фота А цяпер на самым старце)
        self.add_tour(1, "2025-06-01T05:00:00Z", path(*SQUARE))
        self.assertEqual([p.get("pos") and p["pos"]["km"] for p in self.build()[0]["h"]["photos"]], [None, 0.0, 2.0])
        # трэку ў паходу больш няма: фота застаюцца ў галерэі, але без месцаў
        (self.content / "h.yaml").write_text(
            f"date: 2025-06-01\ntitle: {{be: H}}\nkomoot: []\nlocation: [41, 42]\nphotos_folder: {self.FOLDER}\n", encoding="utf-8")
        for attempt in (1, 2):
            photos = self.build()[0]["h"]["photos"]
            self.assertEqual((len(photos), [p.get("pos") for p in photos]), (3, [None, None, None]), attempt)


class TestPhotoClock(PracticalProject):
    """Папраўка гадзінніка камеры (поле photo_clock): месцы фота, запісы па фота і парадак галерэі."""

    FOLDER = "20250601_Паход"

    def rules(self, text):
        out = io.StringIO()
        with unittest.mock.patch("sys.stdout", out):
            clock = practical.photo_clock("h", yaml.safe_load(text))
        return clock, out.getvalue()

    def test_rules_shift_the_shooting_time(self):
        clock, out = self.rules("""
- {date: 2025-07-01, real_date: 2025-06-01}
- {files: "DJI_*", shift: "+1h 30m"}
- {files: ["IMG_1*", "img_2?.jpg"], date: 2025-06-02, shift: "-1d 2h"}
""")
        self.assertEqual(out, "")
        got = lambda name, taken: clock(name, taken)  # noqa: E731
        self.assertEqual(got("A.jpg", "2025-07-01T09:05:07"), "2025-06-01T09:05:07", "не той месяц у камеры")
        self.assertEqual(got("A.jpg", "2025-07-02T09:00:00"), "2025-07-02T09:00:00", "іншая дата — без змен")
        self.assertEqual(got("dji_0518.JPG", "2025-06-01T08:00:00"), "2025-06-01T09:30:00", "імя — без уліку рэгістра")
        self.assertEqual(got("DJI_1.jpg", "2025-07-01T08:00:00"), "2025-06-01T08:00:00", "дзейнічае першы запіс, які падыходзіць")
        self.assertEqual(got("IMG_123.JPG", "2025-06-02T10:00:00"), "2025-06-01T08:00:00")
        self.assertEqual(got("IMG_21.jpg", "2025-06-02T10:00:00"), "2025-06-01T08:00:00", "імя без пашырэння таксама падыходзіць шаблону")
        self.assertEqual(got("IMG_123.JPG", "2025-06-03T10:00:00"), "2025-06-03T10:00:00", "патрэбны і імя, і дата")
        self.assertEqual([got("A.jpg", None), got("A.jpg", ""), got("A.jpg", "учора")], [None, "", "учора"])
        self.assertEqual(practical.photo_clock("h", None)("A.jpg", "2025-07-01T09:00:00"), "2025-07-01T09:00:00")

        # няправільныя запісы прапускаюцца з папярэджаннем, правільныя побач з імі працуюць
        clock, out = self.rules("""
- {date: 2025-07-01}
- {real_date: 2025-06-01}
- {date: "31 чэрвеня", real_date: 2025-06-01}
- {files: 5, shift: "+1h"}
- {files: "A*", shift: "гадзіну назад"}
- {files: "A*", shift: 60}
- "мінус месяц"
- {files: "B*", shift: "-2h", camera: Canon}
""")
        self.assertEqual(out.count("! h: photo_clock:"), 8, out)
        for text in ("запіс 1: няма ні real_date, ні shift", "запіс 2: real_date — сапраўдная дата для фота з датай date",
                     "запіс 3: няправільная дата date: 31 чэрвеня", "запіс 4: files — шаблон імя або спіс шаблонаў, а не «5»",
                     "а не «гадзіну назад»", "а не «60»", "запіс 7: чакаюцца палі", "запіс 8: невядомыя палі: camera"):
            self.assertIn(text, out)
        self.assertEqual((clock("A.jpg", "2025-07-01T09:00:00"), clock("B.jpg", "2025-07-01T09:00:00")),
                         ("2025-07-01T09:00:00", "2025-07-01T07:00:00"))
        _, out = self.rules("{date: 2025-07-01}")
        self.assertIn("чакаецца спіс запісаў", out)
        self.assertEqual(practical.clock_manifest({"a.jpg": {"name": "a", "taken": "2025-07-01T09:00:00"}}, self.rules(
            "[{date: 2025-07-01, real_date: 2025-06-01}]")[0]), {"a.jpg": {"name": "a", "taken": "2025-06-01T09:00:00"}},
            "поле, якога няма ў маніфесце, не дадаецца")

    def test_build_uses_the_corrected_time(self):
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))  # 08:00–12:00
        yaml_text = (f"date: 2025-06-01\ntitle: {{be: H}}\nkomoot: [1]\nphotos_folder: {self.FOLDER}\n"
                     "practical:\n  places:\n    - {type: shelter, photo: Cam2}\n")
        (self.content / "h.yaml").write_text(yaml_text, encoding="utf-8")
        for name, taken in (("Phone.jpg", "2025:06:01 10:00:00"), ("Cam1.jpg", "2025:07:01 09:00:00"), ("Cam2.jpg", "2025:07:01 11:00:00")):
            self.photo(self.FOLDER, name, taken)
        names = lambda index: [Path(p["src"]).stem.split("-")[0] for p in index["h"]["photos"]]  # noqa: E731
        placed = lambda index: [p.get("pos") and (p["pos"]["km"], p["pos"]["time"]) for p in index["h"]["photos"]]  # noqa: E731
        index, out = self.build()
        self.assertEqual((names(index), placed(index)), (["phone", "cam1", "cam2"], [(2.0, "10:00"), None, None]))
        self.assertIsNone(index["h"]["practical"]["places"][0]["pos"])
        self.assertIn("фота Cam2 знята па-за часам трэку", out)

        # у камеры стаяў не той месяц: з папраўкай фота стаяць па часе здымкі і трапляюць на трэк
        (self.content / "h.yaml").write_text(yaml_text + "photo_clock:\n  - {date: 2025-07-01, real_date: 2025-06-01}\n", encoding="utf-8")
        index, out = self.build()
        self.assertEqual(names(index), ["cam1", "phone", "cam2"], "парадак галерэі — па сапраўдным часе")
        self.assertEqual(placed(index), [(1.0, "09:00"), (2.0, "10:00"), (3.0, "11:00")])
        self.assertEqual((index["h"]["practical"]["places"][0]["pos"]["km"], index["h"]["practical"]["places"][0]["photo"]), (3.0, 2))
        self.assertNotIn("па-за часам трэку", out)
        self.assertNotIn("фота Cam1.jpg →", out, "фота не пераціскаюцца")
        manifest = common.read_json(build.CACHE / "photos" / "h.json")
        self.assertEqual(manifest["Cam1.jpg"]["original"], "2025-07-01T09:00:00", "у кэшы застаецца час з фота")
        # дыск з фота недаступны: папраўка па-ранейшаму дзейнічае (час бярэцца з маніфеста)
        self.cfg = {**self.cfg, "photos_root": str(self.root / "няма")}
        self.assertEqual(placed(self.build()[0]), [(1.0, "09:00"), (2.0, "10:00"), (3.0, "11:00")])


class TestSitePages(PracticalProject):
    """Версіі файлаў у index.html і старонкі <мова>/<паход>/ з прэв'ю для спасылак."""

    INDEX = """<!doctype html>
<html lang="be">
<head>
  <title>Мае паходы</title>
  <link rel="stylesheet" href="assets/style.css">
  <script src="https://example.org/maplibre-gl.js" defer></script>
  <script type="module" src="assets/app.js"></script>
</head>
<body></body>
</html>
"""

    def site(self):
        assets = build.DOCS / "assets"
        assets.mkdir(parents=True)
        for name, text in (("app.js", 'import { h } from "./dom.js";\n'), ("dom.js", "export const h = 1;\n"), ("style.css", "body {}\n")):
            (assets / name).write_text(text, encoding="utf-8")
        (build.DOCS / "index.html").write_text(self.INDEX, encoding="utf-8", newline="\n")
        (build.DOCS / "data").mkdir()
        (build.DOCS / "data" / "hikes.json").write_text("{}", encoding="utf-8")

    def test_index_html_gets_one_version_for_all_site_files(self):
        self.site()
        docs, index = build.DOCS, build.DOCS / "index.html"
        self.assertTrue(pages.stamp_index(docs))
        text, version = index.read_text(encoding="utf-8"), pages.assets_version(docs)
        self.assertRegex(version, r"^[0-9a-f]{10}$")
        self.assertIn(f'<link rel="stylesheet" href="assets/style.css?v={version}">', text)
        self.assertIn(f'<script type="module" src="assets/app.js?v={version}"></script>', text)
        self.assertIn(f'<script type="importmap">{{"imports":{{"./assets/app.js":"./assets/app.js?v={version}",'
                      f'"./assets/dom.js":"./assets/dom.js?v={version}"}}}}</script>', text)
        self.assertIn(f'<meta name="hikes-data" content="data/hikes.json?v={pages.file_version(docs / "data" / "hikes.json")}">', text)
        self.assertLess(text.index("importmap"), text.index('type="module"'), "карта імпартаў стаіць перад модулем")
        self.assertNotIn("og:", text, "без адраса сайта прэв'ю не запісваецца")
        self.assertIn('<script src="https://example.org/maplibre-gl.js" defer></script>', text, "астатняе не закранута")

        # паўторны запуск нічога не мяняе; змена любога файла мяняе версію ўсюды, блок не падвойваецца
        stamp = index.stat().st_mtime_ns
        self.assertTrue(pages.stamp_index(docs))
        self.assertEqual((index.read_text(encoding="utf-8"), index.stat().st_mtime_ns), (text, stamp))
        (docs / "assets" / "dom.js").write_text("export const h = 2;\n", encoding="utf-8")
        (docs / "data" / "hikes.json").write_text('{"hikes": []}', encoding="utf-8")
        pages.stamp_index(docs, {"url": "https://example.org/hikes/", "image": "https://example.org/hikes/og/a.jpg"})
        again, new = index.read_text(encoding="utf-8"), pages.assets_version(docs)
        self.assertNotEqual(new, version)
        self.assertEqual((again.count(new), again.count(version), again.count("importmap"), again.count(pages.START)), (5, 0, 1, 1))
        self.assertIn(f'content="data/hikes.json?v={pages.file_version(docs / "data" / "hikes.json")}"', again)
        for tag in ('<meta property="og:url" content="https://example.org/hikes/">', '<meta property="og:title" content="Мае паходы">',
                    '<meta property="og:image" content="https://example.org/hikes/og/a.jpg">', '<meta name="twitter:card" content="summary_large_image">'):
            self.assertIn(tag, again)
        pages.stamp_index(docs)
        self.assertNotIn("og:", index.read_text(encoding="utf-8"), "прэв'ю прыбіраецца разам з адрасам сайта")

        # index.html нечаканага выгляду не чапаецца; без index.html зборка проста ідзе далей
        index.write_text("<html><head></head></html>", encoding="utf-8")
        out = io.StringIO()
        with unittest.mock.patch("sys.stdout", out):
            self.assertFalse(pages.stamp_index(docs))
        self.assertEqual(index.read_text(encoding="utf-8"), "<html><head></head></html>")
        self.assertIn("версіі файлаў не запісаныя", out.getvalue())
        index.unlink()
        self.assertFalse(pages.stamp_index(docs))

    def test_build_makes_share_pages_with_previews(self):
        self.site()
        self.cfg = {**self.cfg, "site_url": "https://example.org/hikes", "photo": {**CFG["photo"], "max_size": 2000}}
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE), distance=21460, elevation_up=1007)
        self.add_tour(2, "2025-06-02T04:00:00Z", path(*SQUARE), distance=4000, elevation_up=400)
        (self.content / "lake.yaml").write_text(
            'date: 2025-06-01\ntitle: {be: "Возера <Тбікелі> & \\"К°\\"", ru: Озеро, en: Lake}\nregion: {be: Аджарыя, en: Adjara}\n'
            "difficulty: hard\nkomoot: [1, 2]\nphotos_folder: 20250601_Возера\ncover: B\n", encoding="utf-8")
        (self.content / "plain.yaml").write_text("date: 2024-01-05\ntitle: {be: Проста}\nlocation: [41, 42]\n", encoding="utf-8")
        for name, size in (("A.jpg", (80, 60)), ("B.jpg", (3000, 1000))):
            target = self.photos_root / "20250601_Возера" / "Сайт" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", size, (30, 120, 200)).save(target, "JPEG")
        index, out = self.build()
        docs = build.DOCS
        self.assertTrue(common.read_json(docs / "data" / "hikes.json")["share"])
        ru = (docs / "ru" / "lake" / "index.html").read_text(encoding="utf-8")
        image = sorted((docs / "og").glob("lake-*.jpg"))
        self.assertEqual(len(image), 1)
        with Image.open(image[0]) as img:
            self.assertEqual((img.size, img.format), ((1200, 630), "JPEG"), "вокладка абрэзаная да памеру прэв'ю")
        for tag in ('<html lang="ru">', "<title>Озеро · Мои походы</title>", '<meta property="og:title" content="Озеро">',
                    '<meta property="og:description" content="1–2 июня 2025 · Аджария · 25,5 км · ↑ 1\u00a0407 м · 2 дня · Тяжело">',
                    '<meta property="og:url" content="https://example.org/hikes/ru/lake/">',
                    f'<meta property="og:image" content="https://example.org/hikes/og/{image[0].name}">',
                    '<meta property="og:image:width" content="1200">', '<meta property="og:locale" content="ru_RU">',
                    '<meta name="twitter:card" content="summary_large_image">',
                    '<meta http-equiv="refresh" content="0; url=../../#/ru/lake">', 'location.replace("../../#/ru/lake");'):
            self.assertIn(tag, ru)
        be = (docs / "be" / "lake" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<meta property="og:title" content="Возера &lt;Тбікелі&gt; &amp; &quot;К°&quot;">', be, "назва экранаваная")
        self.assertIn("1–2 чэрвеня 2025 · Аджарыя · 25,5 км · ↑ 1\u00a0407 м · 2 дні · Цяжка", be)
        self.assertIn("1–2 June 2025 · Adjara · 25.5 km · ↑ 1,407 m · 2 days · Hard", (docs / "en" / "lake" / "index.html").read_text(encoding="utf-8"))
        # паход без трэку і фота: кароткі радок, прэв'ю без карцінкі; руская назва — запасная беларуская
        plain = (docs / "ru" / "plain" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<meta property="og:description" content="5 января 2024">', plain)
        self.assertIn('<meta property="og:title" content="Проста">', plain)
        self.assertNotIn("og:image", plain)
        self.assertIn('<meta name="twitter:card" content="summary">', plain)
        # старонка мовы і сам сайт: прэв'ю з вокладкай найноўшага паходу
        home = (docs / "en" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<meta http-equiv="refresh" content="0; url=../#/en">', home)
        self.assertIn(f"og/{image[0].name}", home)
        root = (docs / "index.html").read_text(encoding="utf-8")
        self.assertIn('<meta property="og:url" content="https://example.org/hikes/">', root)
        self.assertIn(f'<meta property="og:image" content="https://example.org/hikes/og/{image[0].name}">', root)
        self.assertIn(f'content="data/hikes.json?v={pages.file_version(docs / "data" / "hikes.json")}"', root, "версія даных — ужо новага індэкса")

        # другая зборка нічога не перастварае; новая вокладка — новая карцінка, старая прыбіраецца
        stamps = {p: p.stat().st_mtime_ns for p in [image[0], docs / "ru" / "lake" / "index.html"]}
        self.build()
        self.assertEqual({p: p.stat().st_mtime_ns for p in stamps}, stamps)
        text = (self.content / "lake.yaml").read_text(encoding="utf-8").replace("cover: B", "cover: A")
        (self.content / "lake.yaml").write_text(text, encoding="utf-8")
        self.build()
        small = sorted((docs / "og").glob("lake-*.jpg"))
        self.assertEqual((len(small), small[0].name != image[0].name), (1, True))
        with Image.open(small[0]) as img:
            self.assertEqual(img.size, (80, 42), "малая вокладка не расцягваецца, толькі абразаецца")
        self.assertIn('<meta property="og:image:width" content="80">', (docs / "be" / "lake" / "index.html").read_text(encoding="utf-8"))

        # схаваны паход: яго старонкі і карцінка прыбіраюцца; без адраса сайта прыбіраецца ўсё
        (self.content / "lake.yaml").write_text(text + "hidden: true\n", encoding="utf-8")
        self.build()
        self.assertEqual(sorted(p.name for p in (docs / "ru").iterdir()), ["index.html", "plain"])
        self.assertEqual(list((docs / "og").iterdir()), [])
        self.cfg = {k: v for k, v in self.cfg.items() if k != "site_url"}
        self.build()
        self.assertFalse(any((docs / name).exists() for name in ("be", "ru", "en", "og")))
        self.assertFalse(common.read_json(docs / "data" / "hikes.json")["share"])
        self.assertNotIn("og:", (docs / "index.html").read_text(encoding="utf-8"))
        # няправільны адрас сайта: папярэджанне, старонак няма
        self.cfg = {**self.cfg, "site_url": "мой сайт"}
        self.assertIn("site_url у config.yaml: чакаецца адрас", self.build()[1])
        self.assertFalse((docs / "ru").exists())

    def test_preview_texts(self):
        self.assertEqual([pages.num(v, lang, d) for v, lang, d in ((1007, "be", 0), (21.46, "ru", 1), (10.04, "be", 1), (1007, "en", 0), (21.46, "en", 1))],
                         ["1\u00a0007", "21,5", "10", "1,007", "21.5"])
        self.assertEqual([pages.plural(n, ("день", "дня", "дней"), "ru") for n in (1, 2, 5, 11, 21, 22)],
                         ["день", "дня", "дней", "дней", "день", "дня"])
        self.assertEqual(pages.date_range("2025-12-31", "2026-01-02", "be"), "31 снежня 2025 – 2 студзеня 2026")
        self.assertEqual(pages.date_range("2025-05-30", "2025-06-01", "en"), "30 May – 1 June 2025")
        self.assertEqual([pages.pick({"be": "Б", "en": "E"}, lang) for lang in ("be", "ru", "en")], ["Б", "Б", "E"])
        self.assertEqual(pages.pick(None, "ru"), "")


class TestReview190(PracticalProject):
    """Рэгрэсіі па review_2026-10-14_v1.9.0.md."""

    FOLDER = "20250601_Паход"

    def hike(self, extra=""):
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))  # 08:00–12:00 мясцовага часу, квадрат 4 км
        (self.content / "h.yaml").write_text(
            f"date: 2025-06-01\ntitle: {{be: H}}\nkomoot: [1]\nphotos_folder: {self.FOLDER}\n{extra}", encoding="utf-8")

    def shots(self, *photos):
        for name, taken in photos:
            self.photo(self.FOLDER, name, taken)

    def published(self, index):
        return [(Path(p["src"]).stem.split("-")[0], p.get("pos") and (p["pos"]["km"], p["pos"].get("time"))) for p in index["h"]["photos"]]

    def test_gps_photo_takes_the_pass_made_at_shooting_time(self):  # №1
        gps = lambda north, east=0: [round(42.0 + east * M / 0.7547, 5), round(41.0 + north * M, 5)]  # noqa: E731
        place = lambda days, original, at: practical.photo_places(  # noqa: E731
            [{"src": "photos/h/p.webp", "thumb": "photos/h/p-t.webp"}], {"p.jpg": {"name": "p", "original": original, "gps": at}}, days)[0]
        km = lambda pos: (pos["day"], pos["km"], pos.get("time"), pos.get("off"))  # noqa: E731

        # туды і назад адной сцежкай: 2 км на поўнач і назад за 08:00–12:00
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (2000, 0), (0, 0)))
        days = practical.raw_days([1], 4)
        self.assertEqual(km(place(days, "2025-06-01T09:00:00", gps(1000))), (0, 1.0, "09:00", None), "шлях туды")
        self.assertEqual(km(place(days, "2025-06-01T11:00:00", gps(1000))), (0, 3.0, "11:00", None), "тое ж месца на шляху назад")
        self.assertEqual(km(place(days, "2025-06-01T11:00:00", gps(1000, 40))), (0, 3.0, "11:00", 40), "GPS збоку ад сцежкі")
        self.assertEqual(km(place(days, "2025-06-01T11:04:00", gps(1000))), (0, 3.0, "11:04", None),
                         "гадзіннік крыху не сыходзіцца: кіламетр — месца GPS на гэтай праходцы")
        self.assertEqual(km(place(days, "2025-06-01T07:00:00", gps(1000))), (0, 1.0, "07:00", None), "да старту — бліжэйшая па часе праходка")
        self.assertEqual(km(place(days, "2025-06-01T13:00:00", gps(1000))), (0, 3.0, "13:00", None), "пасля фінішу")
        self.assertEqual(km(place(days, None, gps(1000))), (0, 1.0, None, None), "без часу — проста бліжэйшае месца")
        self.assertEqual(km(place(days, "2025-06-01T11:00:00", gps(1000, 400))), (0, 3.0, "11:00", None), "GPS далёка — месца па часе")

        # два туры аднаго дня па той жа сцежцы: туды зранку, назад удзень
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (2000, 0)), duration=7200)
        self.add_tour(2, "2025-06-01T08:00:00Z", path((2000, 0), (0, 0)))
        days = practical.raw_days([1, 2], 4)
        self.assertEqual(len(days[0]["segs"]), 2)
        self.assertEqual(km(place(days, "2025-06-01T10:00:00", gps(1000))), (0, 1.0, "10:00", None))
        self.assertEqual(km(place(days, "2025-06-01T14:00:00", gps(1000))), (0, 3.0, "14:00", None))

    def test_photos_outside_the_recording_get_no_marker(self):  # №2
        self.hike("practical:\n  places:\n    - {type: shelter, photo: Before20}\n")
        self.shots(("Before20.jpg", "2025:06:01 07:40:00"), ("Before1.jpg", "2025:06:01 07:59:00"), ("Start.jpg", "2025:06:01 08:00:00"),
                   ("Mid.jpg", "2025:06:01 10:00:00"), ("Finish.jpg", "2025:06:01 12:00:00"), ("After1.jpg", "2025:06:01 12:01:00"),
                   ("After20.jpg", "2025:06:01 12:20:00"))
        index, out = self.build()
        self.assertEqual(self.published(index), [
            ("before20", None), ("before1", None), ("start", (0.0, "08:00")), ("mid", (2.0, "10:00")), ("finish", (4.0, "12:00")),
            ("after1", None), ("after20", None)], "дзе аўтар быў да старту і пасля фінішу запісу, невядома")
        # ручны запіс практычнай часткі па тым жа фота — па-ранейшаму з допускам паўгадзіны
        self.assertEqual(index["h"]["practical"]["places"][0]["pos"]["km"], 0.0)

    def test_missing_manifest_keeps_known_photo_places(self):  # №3
        self.hike()
        self.shots(("A.jpg", "2025:06:01 09:00:00"), ("B.jpg", "2025:06:01 11:00:00"), ("Eve.jpg", "2025:05:31 19:00:00"))
        placed = self.published(self.build()[0])
        self.assertEqual(placed, [("eve", None), ("a", (1.0, "09:00")), ("b", (3.0, "11:00"))])

        # праект скланаваны на іншы камп'ютар: кэша фота няма, дыск з арыгіналамі недаступны, туры спампаваныя нанова
        manifest = build.CACHE / "photos" / "h.json"
        saved = manifest.read_text(encoding="utf-8")
        manifest.unlink()
        self.cfg = {**self.cfg, "photos_root": str(self.root / "няма")}
        for attempt in (1, 2):
            self.assertEqual(self.published(self.build()[0]), placed, f"зборка {attempt}: вядомыя месцы застаюцца")
        # папраўка гадзінніка змянілася, а пералічыць няма з чаго: ранейшыя месцы да яе ўжо не адносяцца
        text = (self.content / "h.yaml").read_text(encoding="utf-8")
        (self.content / "h.yaml").write_text(text + "photo_clock:\n  - {shift: \"+1h\"}\n", encoding="utf-8")
        self.assertEqual([pos for _, pos in self.published(self.build()[0])], [None, None, None])
        (self.content / "h.yaml").write_text(text, encoding="utf-8")
        self.assertEqual([pos for _, pos in self.published(self.build()[0])], [None, None, None], "страчанае само не вяртаецца")

        # з маніфестам месцы лічацца нанова; пасля змены трэку без маніфеста ранейшыя месцы не падыходзяць
        manifest.write_text(saved, encoding="utf-8")
        self.assertEqual(self.published(self.build()[0]), placed)
        manifest.unlink()
        self.add_tour(1, "2025-06-01T05:00:00Z", path(*SQUARE))
        self.assertEqual([pos for _, pos in self.published(self.build()[0])], [None, None, None], "трэк іншы — ранейшыя месцы састарэлі")
        # кантроль: трэк прыбраны з паходу — месцаў няма і з маніфестам
        manifest.write_text(saved, encoding="utf-8")
        self.assertEqual(self.published(self.build()[0])[1][1], (0.0, "09:00"))
        (self.content / "h.yaml").write_text(
            f"date: 2025-06-01\ntitle: {{be: H}}\nkomoot: []\nlocation: [41, 42]\nphotos_folder: {self.FOLDER}\n", encoding="utf-8")
        self.assertEqual([pos for _, pos in self.published(self.build()[0])], [None, None, None])

    def test_clock_correction_reorders_the_gallery_without_originals(self):  # №4
        def result(yaml_extra, offline):
            """Парадак галерэі, вокладка і месцы пасля дадання папраўкі — з арыгіналамі і без іх."""
            self.tearDown()
            self.setUp()
            self.hike()
            self.shots(("Phone.jpg", "2025:06:01 10:00:00"), ("Cam.jpg", "2025:07:01 09:00:00"), ("Cam2.jpg", "2025:07:01 11:30:00"))
            first = self.build()[0]["h"]
            self.assertEqual(([n for n, _ in self.published({"h": first})], first["cover"]), (["phone", "cam", "cam2"], 0))
            (self.content / "h.yaml").write_text(
                (self.content / "h.yaml").read_text(encoding="utf-8") + yaml_extra + "photo_clock:\n  - {date: 2025-07-01, real_date: 2025-06-01}\n",
                encoding="utf-8")
            if offline:
                self.cfg = {**self.cfg, "photos_root": str(self.root / "няма")}
            h = self.build()[0]["h"]
            names = [n for n, _ in self.published({"h": h})]
            return names, names[h["cover"]], [pos for _, pos in self.published({"h": h})]

        online = result("", offline=False)
        self.assertEqual(online, (["cam", "phone", "cam2"], "cam", [(1.0, "09:00"), (2.0, "10:00"), (3.5, "11:30")]))
        self.assertEqual(result("", offline=True), online, "без арыгіналаў — той жа парадак і тая ж вокладка")
        chosen = result("cover: Phone\n", offline=False)
        self.assertEqual((chosen[0], chosen[1]), (["cam", "phone", "cam2"], "phone"))
        self.assertEqual(result("cover: Phone\n", offline=True), chosen, "зададзеная вокладка застаецца тым жа фота")

        # пра адно з фота ў маніфесце нічога няма: парадак застаецца ранейшы
        self.tearDown()
        self.setUp()
        self.hike()
        self.shots(("Phone.jpg", "2025:06:01 10:00:00"), ("Cam.jpg", "2025:07:01 09:00:00"))
        self.build()
        manifest = common.read_json(build.CACHE / "photos" / "h.json")
        del manifest["Phone.jpg"]
        common.write_json(build.CACHE / "photos" / "h.json", manifest)
        (self.content / "h.yaml").write_text(
            (self.content / "h.yaml").read_text(encoding="utf-8") + "photo_clock:\n  - {date: 2025-07-01, real_date: 2025-06-01}\n", encoding="utf-8")
        self.cfg = {**self.cfg, "photos_root": str(self.root / "няма")}
        self.assertEqual([n for n, _ in self.published(self.build()[0])], ["phone", "cam"])


class TestReview191(PracticalProject):
    """Рэгрэсіі па review_2026-10-14_v1.9.1.md."""

    FOLDER, hike, shots, published = TestReview190.FOLDER, TestReview190.hike, TestReview190.shots, TestReview190.published

    def fresh(self, extra, *photos):
        """Новы часовы праект з адным паходам (квадрат 4 км, 08:00–12:00) і фота; вяртае індэкс першай зборкі."""
        self.tearDown()
        self.setUp()
        self.hike(extra)
        self.shots(*photos)
        return self.build()[0]

    def offline(self, manifest=True):
        """Дыск з арыгіналамі адключаны; без маніфеста — як у праекце, скланаваным на іншы камп'ютар."""
        self.cfg = {**self.cfg, "photos_root": str(self.root / "няма")}
        if not manifest:
            (build.CACHE / "photos" / "h.json").unlink()

    def edit(self, old, new):
        text = (self.content / "h.yaml").read_text(encoding="utf-8")
        (self.content / "h.yaml").write_text(text.replace(old, new) if old else text + new, encoding="utf-8")

    def test_gps_photo_near_a_turnaround_takes_the_pass_made_at_shooting_time(self):  # працяг №1 з review 1.9.0
        gps = lambda north, east=0: [round(42.0 + east * M / 0.7547, 5), round(41.0 + north * M, 5)]  # noqa: E731
        place = lambda days, original, at: practical.photo_places(  # noqa: E731
            [{"src": "photos/h/p.webp", "thumb": "photos/h/p-t.webp"}], {"p.jpg": {"name": "p", "original": original, "gps": at}}, days)[0]
        km = lambda pos: (pos["km"], pos.get("time"), pos.get("off"))  # noqa: E731

        # туды і назад адной сцежкай, разварот на 2 км у 10:00; звычайны трэк і разрэджаны (тры пункты на ўвесь шлях)
        for step, count in ((20.0, 201), (5000.0, 3)):
            self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (2000, 0), (0, 0), step=step))
            days = practical.raw_days([1], 4)
            self.assertEqual(len(days[0]["segs"][0]), count)
            # фота за 200 і за 100 м да развароту: увесь шлях паміж дзвюма праходкамі бліжэй за 300 м ад фота
            self.assertEqual(km(place(days, "2025-06-01T09:48:00", gps(1800))), (1.8, "09:48", None), count)
            self.assertEqual(km(place(days, "2025-06-01T10:12:00", gps(1800))), (2.2, "10:12", None), count)
            self.assertEqual(km(place(days, "2025-06-01T09:54:00", gps(1900))), (1.9, "09:54", None), count)
            self.assertEqual(km(place(days, "2025-06-01T10:06:00", gps(1900))), (2.1, "10:06", None), count)
            self.assertEqual(km(place(days, "2025-06-01T10:12:00", gps(1800, 40))), (2.2, "10:12", 40), "GPS збоку ад сцежкі")
            self.assertEqual(km(place(days, "2025-06-01T10:00:00", gps(2000))), (2.0, "10:00", None), "сам разварот")
            self.assertEqual(km(place(days, None, gps(1800))), (1.8, None, None), "без часу — проста бліжэйшае месца")

        # серпанцін: 1 км на поўнач, 20 м убок і назад; фота за 100 м да павароту аднолькава блізка да абодвух
        # віткоў, і трэк паміж імі не выходзіць з радыуса 300 м — віток выбірае час
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (1000, 0), (1000, 20), (0, 20)))
        days = practical.raw_days([1], 4)
        self.assertEqual(km(place(days, "2025-06-01T09:47:00", gps(900, 10))), (0.9, "09:47", None))
        self.assertEqual(km(place(days, "2025-06-01T10:13:00", gps(900, 10))), (1.1, "10:13", None))
        # віткі за 100 м адзін ад аднаго, фота на першым: другі віток — не месца здымкі, нават калі па гадзінніку
        # камеры ён бліжэй (гадзіннік мог спяшацца на паўгадзіны)
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (1000, 0), (1000, 100), (0, 100)))
        days = practical.raw_days([1], 4)
        self.assertEqual(km(place(days, "2025-06-01T09:43:00", gps(900))), (0.9, "09:43", None))
        self.assertEqual(km(place(days, "2025-06-01T10:17:00", gps(900))), (0.9, "10:17", None))
        # туды і назад рознымі сцежкамі за 200 м: паміж праходкамі трэк выходзіць з радыуса — гэта два асобныя
        # знаходжанні каля месца з розніцай у гадзіны, і выбірае час, як і раней
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (2000, 0), (2000, 200), (0, 200)))
        days = practical.raw_days([1], 4)
        self.assertEqual(km(place(days, "2025-06-01T09:00:00", gps(1000))), (1.0, "09:00", None))
        self.assertEqual(km(place(days, "2025-06-01T11:03:00", gps(1000))), (3.2, "11:03", 200))

    def test_known_photo_places_do_not_survive_any_change_of_the_track(self):  # №1
        square = coords(path(*SQUARE))
        changes = {
            "геаметрыя": [[lat, lng + 0.1, alt, t] for lat, lng, alt, t in square],
            "вышыня": [[lat, lng, alt + 100, t] for lat, lng, alt, t in square],
            "час паміж стартам і фінішам": [[lat, lng, alt, round((t / 14400000) ** 2 * 14400000)] for lat, lng, alt, t in square],
        }
        for what, changed in changes.items():
            self.assertEqual((len(changed), changed[0][3], changed[-1][3]), (len(square), square[0][3], square[-1][3]),
                             "колькасць пунктаў і межы тура па часе тыя ж")
            placed = self.published(self.fresh("", ("A.jpg", "2025:06:01 09:00:00"), ("B.jpg", "2025:06:01 11:00:00")))
            self.assertEqual(placed, [("a", (1.0, "09:00")), ("b", (3.0, "11:00"))])
            key = self.build()[0]["h"]["photoKey"]
            self.offline(manifest=False)
            self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))  # тур спампаваны нанова, без змен
            index = self.build()[0]
            self.assertEqual((self.published(index), index["h"]["photoKey"]), (placed, key), "нязменны тур: вядомыя месцы застаюцца")
            self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE), coords=changed)
            index = self.build()[0]
            self.assertEqual(self.published(index), [("a", None), ("b", None)], what)
            self.assertNotEqual(index["h"]["photoKey"], key, what)

    def test_practical_places_by_photo_follow_the_clock_and_the_track(self):  # №2
        notes = ("practical:\n  water:\n    sources:\n      - {type: spring, photo: A}\n"
                 "  places:\n    - {type: shelter, photo: A}\n    - {type: table, km: 2.5}\n")
        known = [(1.0, "09:00"), (1.0, "09:00"), (2.5, "10:30")]  # крыніца і альтанка — па фота, стол — па кіламетры

        def where(index):
            block = index["h"]["practical"]
            return [item["pos"] and (item["pos"]["km"], item["pos"].get("time")) for item in block["water"]["sources"] + block["places"]]

        def cloned():
            """Праект без маніфеста і арыгіналаў: пакуль нічога не змянілася, месцы застаюцца."""
            self.assertEqual(where(self.fresh(notes, ("A.jpg", "2025:06:01 09:00:00"))), known)
            self.offline(manifest=False)
            for attempt in (1, 2):
                self.assertEqual(where(self.build()[0]), known, f"зборка {attempt}")

        # дададзена папраўка гадзінніка: месца па фота палічана яшчэ без яе
        cloned()
        self.edit("", 'photo_clock:\n  - {shift: "+1h"}\n')
        index, out = self.build()
        self.assertEqual(where(index), [None, None, known[2]])
        self.assertEqual(out.count("ранейшае месца па фота A састарэла"), 2, out)
        self.assertEqual([item["photo"] for item in index["h"]["practical"]["places"]], [0, None], "само фота ў запісе застаецца")
        self.assertEqual(where(self.build()[0]), [None, None, known[2]], "страчанае само не вяртаецца")
        self.cfg = {**self.cfg, "photos_root": str(self.photos_root)}  # арыгіналы зноў даступныя
        self.assertEqual(where(self.build()[0]), [(2.0, "10:00"), (2.0, "10:00"), known[2]])

        # змяніўся трэк: той жа маршрут запісаны на гадзіну пазней
        cloned()
        self.add_tour(1, "2025-06-01T05:00:00Z", path(*SQUARE))
        index, out = self.build()
        self.assertEqual(where(index), [None, None, (2.5, "11:30")])
        self.assertEqual(out.count("ранейшае месца па фота A састарэла"), 2, out)

        # тура няма ў кэшы, на сайце застаецца ранейшы трэк: месцы ранейшыя, пакуль папраўка гадзінніка тая ж
        self.assertEqual(where(self.fresh(notes, ("A.jpg", "2025:06:01 09:00:00"))), known)
        (build.CACHE / "komoot" / "tours" / "1.json").unlink()
        for attempt in (1, 2):
            index, out = self.build()
            self.assertIn("застаецца апублікаваны раней трэк", out)
            self.assertEqual((where(index), self.published(index)), (known, [("a", (1.0, "09:00"))]), f"зборка {attempt}")
        self.edit("", 'photo_clock:\n  - {shift: "+1h"}\n')
        index, out = self.build()
        self.assertEqual((where(index), self.published(index)), ([None, None, known[2]], [("a", None)]))
        self.assertIn("месцы фота на карце прыбраны", out)
        self.assertEqual(out.count("ранейшае месца па фота A састарэла"), 2, out)
        self.edit('photo_clock:\n  - {shift: "+1h"}\n', "")
        index = self.build()[0]
        self.assertEqual((where(index), self.published(index)), ([None, None, known[2]], [("a", None)]), "страчанае само не вяртаецца")

    def test_removed_cover_gives_the_first_photo_without_originals(self):  # №3
        def covers(change):
            first = self.fresh("cover: B\n", ("A.jpg", "2025:06:01 09:00:00"), ("B.jpg", "2025:06:01 11:00:00"))
            self.assertEqual(([name for name, _ in self.published(first)], first["h"]["cover"]), (["a", "b"], 1))
            self.edit("cover: B\n", "")
            change()
            found = []
            for attempt in (1, 2):
                h = self.build()[0]["h"]
                found.append(([Path(p["src"]).stem.split("-")[0] for p in h["photos"]], h["cover"]))
            return found

        def broken():  # крыніца ёсць, але фота не чытаюцца, а маніфеста няма: фота аднаўляюцца з індэкса
            (build.CACHE / "photos" / "h.json").unlink()
            for name in ("A.jpg", "B.jpg"):
                (self.photos_root / self.FOLDER / "Сайт" / name).write_bytes(b"broken")

        auto = [(["a", "b"], 0)] * 2
        self.assertEqual(covers(lambda: None), auto, "з арыгіналамі")
        self.assertEqual(covers(self.offline), auto, "без арыгіналаў — тое ж першае фота па часе")
        self.assertEqual(covers(lambda: self.offline(manifest=False)), auto, "і без маніфеста")
        self.assertEqual(covers(broken), auto, "фота не чытаюцца")


class TestReview192(FetchProject):
    """Рэгрэсіі па review_2026-10-14_v1.9.2.md."""

    FOLDER = "20250601_Паход"

    def with_photos(self, cover):
        """Новы часовы праект: квадрат 4 км і фота A (09:00) і B (11:00); вяртае паход з індэкса першай зборкі."""
        self.tearDown()
        self.setUp()
        self.hike(f"photos_folder: {self.FOLDER}\ncover: {cover}\n")
        self.photo(self.FOLDER, "A.jpg", "2025:06:01 09:00:00")
        self.photo(self.FOLDER, "B.jpg", "2025:06:01 11:00:00")
        return self.build()[0]["h"]

    def set_cover(self, cover):
        path_ = self.content / "h.yaml"
        lines = [line for line in path_.read_text(encoding="utf-8").splitlines() if not line.startswith("cover:")]
        path_.write_text("\n".join(lines + [f"cover: {cover}"]) + "\n", encoding="utf-8")

    def test_chosen_cover_is_found_when_the_index_holds_a_thumbnail(self):  # №1
        for cover in ("A.jpg", "A"):  # імя з пашырэннем і без яго
            first = self.with_photos("B")
            self.assertEqual(([Path(p["src"]).stem.split("-")[0] for p in first["photos"]], first["cover"]), (["a", "b"], 1))
            self.cfg = {**self.cfg, "photos_root": str(self.root / "няма")}
            (build.DOCS / first["photos"][0]["src"]).unlink()  # страчана поўная копія першага фота
            h = self.build()[0]["h"]
            self.assertEqual((h["photos"][0]["src"], h["cover"]), (first["photos"][0]["thumb"], 1), "у індэксе замест яе мініяцюра")
            self.set_cover(cover)
            for manifest in ("з маніфестам", "без маніфеста"):
                if manifest == "без маніфеста":
                    (build.CACHE / "photos" / "h.json").unlink()
                for attempt in (1, 2):
                    h = self.build()[0]["h"]
                    self.assertEqual((h["cover"], h["photos"][0]["src"]), (0, first["photos"][0]["thumb"]), (cover, manifest, attempt))
            self.set_cover("B")
            self.assertEqual(self.build()[0]["h"]["cover"], 1, "і назад")

        # фота, якое само завецца на «-t» (імя без хэша — са старой версіі) і засталося без мініяцюры: у індэксе
        # яно выглядае гэтак жа, як мініяцюра фота «x», але яго імя ёсць у маніфесце
        self.tearDown()
        self.setUp()
        self.cfg = {**self.cfg, "photos_root": str(self.root / "няма")}
        self.hike(f"photos_folder: {self.FOLDER}\ncover: X-T.JPG\n")
        out = build.DOCS / "photos" / "h"
        out.mkdir(parents=True)
        for name in ("x-t", "b", "b-t"):
            Image.new("RGB", (40, 30)).save(out / f"{name}.webp", "WEBP")
        common.write_json(build.CACHE / "photos" / "h.json", {
            key: {"name": name, "w": 40, "h": 30, "taken": taken, "original": taken, "mtime": 0, "size": 0, "gps": None}
            for key, name, taken in (("B.JPG", "b", "2025-06-01T09:00:00"), ("X-T.JPG", "x-t", "2025-06-01T11:00:00"))})
        photos = [{"src": "photos/h/b.webp", "thumb": "photos/h/b-t.webp", "w": 40, "h": 30},
                  {"src": "photos/h/x-t.webp", "thumb": "photos/h/x-t.webp", "w": 40, "h": 30}]
        common.write_json(build.DOCS / "data" / "hikes.json", {"hikes": [
            {"slug": "h", "photos": photos, "cover": 0, "point": None, "lines": [], "track": None, "days": []}]})
        for attempt in (1, 2):
            h = self.build()[0]["h"]
            self.assertEqual(([p["src"] for p in h["photos"]], h["cover"]), (["photos/h/b.webp", "photos/h/x-t.webp"], 1), attempt)

    def test_kept_hints_give_way_to_the_places_marked_by_the_author(self):  # №2
        self.hike("practical:\n  places:\n    - {type: swing, km: 1}\n")
        shelter = osm_node(77, 1000, 0, amenity="shelter")
        self.fetch(osm=[shelter, VIEW, SPRING])
        hints = lambda index: [(h["type"], h["pos"]["km"]) for h in index["h"]["hints"]]  # noqa: E731
        self.assertEqual(hints(self.build()[0]), [("spring", 0.5), ("shelter", 1.0), ("viewpoint", 1.5)])

        # тура больш няма ў кэшы: на сайце застаюцца ранейшыя трэк і падказкі
        (build.CACHE / "komoot" / "tours" / "1.json").unlink()
        index, out = self.build()
        self.assertIn("застаецца апублікаваны раней трэк", out)
        self.assertEqual(hints(index), [("spring", 0.5), ("shelter", 1.0), ("viewpoint", 1.5)], "арэлі — не альтанка")
        # аўтар пазначыў альтанку сам — падказка пра яе больш не патрэбная; астатнія застаюцца
        text = (self.content / "h.yaml").read_text(encoding="utf-8")
        (self.content / "h.yaml").write_text(text.replace("type: swing", "type: shelter"), encoding="utf-8")
        for attempt in (1, 2):
            index = self.build()[0]
            self.assertEqual(hints(index), [("spring", 0.5), ("viewpoint", 1.5)], attempt)
            self.assertEqual([(p["type"], p["pos"]["km"]) for p in index["h"]["practical"]["places"]], [("shelter", 1.0)])
        # тур вярнуўся ў кэш: вынік той самы, што і пры поўным разліку
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))
        self.assertEqual(hints(self.build()[0]), [("spring", 0.5), ("viewpoint", 1.5)])

    def test_gps_photo_aside_of_a_turnaround_takes_the_pass_made_at_shooting_time(self):  # працяг №1 з review 1.9.0
        gps = lambda north, east=0: [round(42.0 + east * M / 0.7547, 5), round(41.0 + north * M, 5)]  # noqa: E731
        place = lambda days, original, at: practical.photo_places(  # noqa: E731
            [{"src": "photos/h/p.webp", "thumb": "photos/h/p-t.webp"}], {"p.jpg": {"name": "p", "original": original, "gps": at}}, days)[0]
        km = lambda pos: (pos["km"], pos.get("time"), pos.get("off"))  # noqa: E731
        exact = lambda days, hour, minute, at: round(practical._project_at_time(  # noqa: E731
            at[1], at[0], days, datetime(2025, 6, 1, hour, minute, tzinfo=TZ).timestamp(), practical.PHOTO_GPS_M)[2][4], 2)

        # туды і назад адной сцежкай, разварот на 2 км у 10:00; звычайны трэк і разрэджаны (тры пункты на ўвесь шлях)
        for step, count in ((20.0, 201), (5000.0, 3)):
            self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (2000, 0), (0, 0), step=step))
            days = practical.raw_days([1], 4)
            self.assertEqual(len(days[0]["segs"][0]), count)
            # GPS за 250 м убок ад сцежкі, за 100 м да развароту: у развароце адлегласць да яго расце толькі з 250 да 269 м
            self.assertEqual(km(place(days, "2025-06-01T09:54:00", gps(1900, 250))), (1.9, "09:54", 250), count)
            self.assertEqual(km(place(days, "2025-06-01T10:06:00", gps(1900, 250))), (2.1, "10:06", 250), count)
            self.assertEqual(km(place(days, "2025-06-01T09:00:00", gps(1000, 290)))[:2], (1.0, "09:00"), count)
            self.assertEqual(km(place(days, "2025-06-01T11:00:00", gps(1000, 290)))[:2], (3.0, "11:00"), count)
            # фота за 50 і за 20 м да развароту — таксама дзве праходкі; бліжэй за 15 м — ужо адна
            self.assertEqual((exact(days, 9, 57, gps(1950)), exact(days, 10, 3, gps(1950))), (1.95, 2.05), count)
            self.assertEqual((exact(days, 9, 59, gps(1980)), exact(days, 10, 1, gps(1980))), (1.98, 2.02), count)
            self.assertIn((exact(days, 9, 59, gps(1990)), exact(days, 10, 1, gps(1990))), ((1.99, 1.99), (2.01, 2.01)), count)

        # трэк, які дрыжыць на 3 м управа і ўлева на кожным кроку: праходак па-ранейшаму дзве, месцы тыя ж
        shaky = [(lat, lng + (3 if i % 2 else -3) * M / 0.7547) for i, (lat, lng) in enumerate(path((0, 0), (2000, 0), (0, 0)))]
        self.add_tour(1, "2025-06-01T04:00:00Z", shaky)
        days = practical.raw_days([1], 4)
        for at, there, back in ((gps(1000), (9, 0), (11, 0)), (gps(1000, 40), (9, 0), (11, 0)), (gps(1900, 250), (9, 54), (10, 6))):
            self.assertEqual(len(practical._passes(at[1], at[0], days, practical.PHOTO_GPS_M)), 2, at)
            for real in (there, back):  # калі аўтар сапраўды быў каля гэтага месца
                moment = datetime(2025, 6, 1, *real)
                want = practical._by_time(days, moment.replace(tzinfo=TZ).timestamp(), tolerance=0)["km"]
                for clock_error in (0, 4):  # гадзіннік камеры дакладны або спяшаецца на чатыры хвіліны
                    shot = (moment + timedelta(minutes=clock_error)).isoformat()
                    self.assertEqual(place(days, shot, at)["km"], want, (at, real, clock_error))


class StatusProject(FetchProject):
    """Часовы праект для `hike status` і свайго слоўніка назваў (content/places.yaml)."""

    FOLDER = "20250601_Возера"
    KEDA = {"be": "Keda", "en": "Keda", "ru": "Кеда"}  # беларускай назвы ў OpenStreetMap няма

    def setUp(self):
        super().setUp()
        patcher = unittest.mock.patch.multiple(status, CACHE=build.CACHE, CONTENT=self.content, DOCS=build.DOCS,
                                               load_config=lambda: self.cfg)
        patcher.start()
        self.addCleanup(patcher.stop)

    def places(self, text):
        (self.content.parent / "places.yaml").write_text(text, encoding="utf-8")

    def names(self, index, slug="h"):
        """Назвы старту і фінішу паходу і яго дзён: усе аднолькавыя — вяртаецца адна."""
        h = index[slug]
        found = [item[edge]["place"] for item in [h["facts"]] + [d["facts"] for d in h["days"]] for edge in ("start", "finish")]
        self.assertTrue(all(place == found[0] for place in found), found)
        return found[0]

    def report(self, *argv):
        out = io.StringIO()
        with unittest.mock.patch("sys.stdout", out):
            status.main(list(argv))
        return out.getvalue()

    def todo(self, *argv):
        """Радкі раздзела «Што зрабіць» без водступаў."""
        text = self.report(*argv)
        return [line.strip() for line in text.split("Што зрабіць\n")[1].splitlines()] if "Што зрабіць\n" in text else []


BUILD_CHANGED = ".\\hike build  — пасля зборкі змяніліся файл паходу, слоўнік назваў або спампаваныя звесткі: "
BUILD_PHOTOS = ".\\hike build  — фота на сайце не тыя, што ў тэчцы: ёсць новыя, змененыя, выдаленыя або зборка не скончылася: "
ALL_DONE = ["нічога: усё спампавана і сабрана"]


class TestPlacesAndStatus(StatusProject):
    """Свае назвы населеных пунктаў (content/places.yaml) і каманда `hike status`."""

    def test_own_place_names_replace_the_names_from_openstreetmap(self):
        self.hike()
        self.fetch(names=self.KEDA)
        self.assertEqual(self.names(self.build()[0]), self.KEDA)

        self.places('Keda: "Кеда"\n')
        index, out = self.build()
        self.assertEqual(self.names(index), {"be": "Кеда", "en": "Keda", "ru": "Кеда"})
        self.assertNotIn("places.yaml", out)
        self.places('# рэгістр назвы не важны\nkeda: {be: "Кеда", ru: "Кэда"}\nTago:\nBako: ""\n')
        index, out = self.build()
        self.assertEqual(self.names(index), {"be": "Кеда", "en": "Keda", "ru": "Кэда"})
        self.assertNotIn("places.yaml", out, "пусты запіс — яшчэ не запоўнены, не памылка")

        # няправільны запіс прапускаецца з папярэджаннем — застаецца назва з OpenStreetMap
        for bad in ("Keda: 5", "Keda: [Кеда]", 'Keda: {en: "Keda!"}', 'Keda: {be: "Кеда", ru: 7}', 'Keda: {be: " "}'):
            self.places(bad + "\n")
            index, out = self.build()
            self.assertEqual(self.names(index), self.KEDA, bad)
            self.assertIn("places.yaml: «Keda»", out, bad)
        # памылка YAML спыняе зборку, пакуль нічога не зменена
        before = (build.DOCS / "data" / "hikes.json").read_bytes()
        self.places('Keda: "Кеда\n')
        with self.assertRaises(SystemExit) as stop:
            self.build()
        self.assertIn("places.yaml", str(stop.exception))
        self.assertEqual((build.DOCS / "data" / "hikes.json").read_bytes(), before)

        # тура няма ў кэшы: трэк і назвы ранейшыя, але слоўнік дзейнічае
        self.places('Keda: "Кеда"\n')
        self.build()
        (build.CACHE / "komoot" / "tours" / "1.json").unlink()
        index, out = self.build()
        self.assertIn("застаецца апублікаваны раней трэк", out)
        self.assertEqual(self.names(index), {"be": "Кеда", "en": "Keda", "ru": "Кеда"})
        self.places('Keda: {be: "Кэда", ru: "Кеда-2"}\n')
        self.assertEqual(self.names(self.build()[0]), {"be": "Кэда", "en": "Keda", "ru": "Кеда-2"})

        # назва без запісу ў слоўніку — як і была; пункт без назвы застаецца без назвы
        self.assertEqual(geo.place_label({"be": "Мілісі", "en": "Milisi"}, {"keda": {"be": "Кеда"}}), {"be": "Мілісі", "en": "Milisi"})
        self.assertEqual(geo.place_label({"be": "", "en": "", "ru": ""}, {"": {"be": "Нешта"}}), None)
        self.assertEqual(geo.place_label(None, {}), None)

    def project(self):
        """Тры паходы: «Возера» амаль без звестак, «Хрыбет» запоўнены, трэці схаваны."""
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))
        self.add_tour(2, "2025-06-02T04:00:00Z", path(*SQUARE))
        (self.content / "2025-06-01-lake.yaml").write_text(
            f"date: 2025-06-01\ntitle: Возера\nkomoot: [1]\nphotos_folder: {self.FOLDER}\n", encoding="utf-8")
        (self.content / "2025-06-02-ridge.yaml").write_text(
            "date: 2025-06-02\ntitle: {be: Хрыбет, ru: Хребет, en: Ridge}\ndifficulty: easy\nkomoot: [2]\nyoutube: [v1]\n"
            "impressions: {be: Прыгожа., ru: Красиво.}\n"
            "practical:\n  surface: грунт\n  marking: ''\n  places: []\n"
            "  water: {taken: 2, left: 0.5, drunk: 1.5, available: false, sources: []}\n", encoding="utf-8")
        (self.content / "2025-06-03-secret.yaml").write_text(
            "date: 2025-06-03\ntitle: Таямніца\nhidden: true\nkomoot: [3]\n", encoding="utf-8")
        self.photo(self.FOLDER, "A.jpg", "2025:06:01 09:00:00")
        self.photo(self.FOLDER, "B.jpg", "2025:05:31 19:00:00")  # напярэдадні: месца на трэку няма

    def test_status_shows_what_is_missing(self):
        self.assertEqual({path for path, _ in status.PRACTICAL} - {"water.taken", "water.left", "water.drunk", "water.refilled",
                         "water.available", "water.sources", "getting_there", "places", "weather_notes"}, set(practical.NOTE_KEYS))
        self.assertEqual(self.report(), "Паходаў пакуль няма: content/hikes пустая.\n")
        self.project()

        # сайт яшчэ не сабраны
        rows = {r["slug"][11:]: r for r in status.collect()[0]}
        self.assertEqual([r["slug"][11:] for r in status.collect()[0]], ["secret", "ridge", "lake"], "найноўшыя першымі")
        lake, ridge, secret = rows["lake"], rows["ridge"], rows["secret"]
        self.assertEqual((lake["title"], lake["names"], lake["difficulty"], lake["story"], len(lake["gaps"]), lake["videos"], lake["track"]),
                         ("Возера", {"be": True, "ru": False, "en": False}, False, {"be": False, "ru": False, "en": False}, 20, 0, True))
        self.assertEqual((ridge["names"], ridge["difficulty"], ridge["story"], ridge["videos"]),
                         ({"be": True, "ru": True, "en": True}, True, {"be": True, "ru": True, "en": False}, 1))
        self.assertEqual(sorted(set(dict(status.PRACTICAL)) - {path for path, _ in ridge["gaps"]}),
                         ["surface", "water.available", "water.drunk", "water.left", "water.taken"], "false і лік — гэта адказ")
        self.assertEqual((lake["photos"], [(command, what) for command, what, _ in lake["todo"]]), (None, [
            ("fetch", "няма надвор'я або яно няпоўнае"), ("fetch", "няма падказак OpenStreetMap"), ("fetch", "няма дарогі на машыне"),
            ("fetch", "няма назваў населеных пунктаў"), ("fetch", "няма звычайнага надвор'я па месяцах або яно няпоўнае"),
            ("build", "яшчэ не на сайце")]))
        self.assertEqual((secret["hidden"], secret["todo"]), (True, []), "схаваны паход на сайт не ідзе")
        text = self.report()
        self.assertIn(".\\hike build  — яшчэ не на сайце: 2025-06-02-ridge, 2025-06-01-lake", text)
        self.assertIn("2025-06-03  Таямніца (схаваны)", text)

        # сабраны без `hike fetch`: звестак сэрвісаў няма, а збіраць нанова няма чаго
        both = "2025-06-02-ridge, 2025-06-01-lake"
        self.build()
        self.assertEqual(self.todo(), [".\\hike fetch  — няма надвор'я або яно няпоўнае: " + both,
                                       ".\\hike fetch  — няма падказак OpenStreetMap: " + both,
                                       ".\\hike fetch  — няма дарогі на машыне: " + both,
                                       ".\\hike fetch  — няма назваў населеных пунктаў: " + both,
                                       ".\\hike fetch  — няма звычайнага надвор'я па месяцах або яно няпоўнае: " + both])
        # спампавана, але яшчэ не сабрана
        self.fetch(names=self.KEDA)
        self.assertEqual(self.todo()[0], BUILD_CHANGED + both)
        self.build()
        text = self.report()
        for line in ("паход                            назва  скл  уражанні  практ  фота  на карце  відэа",
                     "                                 ru en       be ru en",
                     "2025-06-03  Таямніца (схаваны)   –  –   –    –  –  –       –     ?                –",
                     "2025-06-02  Хрыбет               +  +   +    +  +  –    5/20     –                1",
                     "2025-06-01  Возера               –  –   –    –  –  –       –     2       1/2      –",
                     "Зводка: паходаў 2 (і схаваных: 1)", "  назва па-руску         1 з 2", "  складанасць            1 з 2",
                     "  уражанні               1 з 2  (па-руску 1, па-англійску 0)", "  практычная частка      1 з 2",
                     "  фота                   1 з 2  (месца на карце ёсць у 1 фота з 2)", "  відэа                  1 з 2",
                     "  content\\places.yaml — няма беларускай назвы (на сайце лацінкай): Keda", '   Keda: "…"'):
            self.assertIn(line + "\n", text)
        self.assertNotIn(".\\hike", text, "спампоўваць і збіраць больш няма чаго")
        self.places('Keda: "Кеда"\n')
        self.assertEqual(self.todo(), [BUILD_CHANGED + both], "назва ў слоўніку ёсць, але сайт яшчэ не перасабраны")
        self.build()
        self.assertEqual(self.todo(), ALL_DONE)
        common.write_json(build.CACHE / "komoot" / "tours.json", [{"id": 1, "status": "private"}, {"id": 2, "status": "public"}])
        self.build()
        self.assertEqual(self.todo(), ["Komoot — туры не публічныя, спасылкі «Трэк у Komoot» на сайце няма: паходаў 1"])

        # падрабязнасці — па тэксце з імя файла або назвы на любой мове
        text = self.report("lake")
        self.assertNotIn("Хрыбет", text)
        self.assertNotIn("Зводка", text)
        self.assertIn("2025-06-01-lake.yaml — Возера\n"
                      "  дапісаць: назва па-руску, назва па-англійску, складанасць (difficulty), уражанні (impressions)\n"
                      "  практычная частка (practical:) — не запоўнена. Што можна запісаць:\n"
                      "    вада: узята на старце (water.taken)\n", text)
        self.assertIn("  фота без месца на карце: 1 з 2 (запіс трэку: 2025-06-01 08:00–12:00)\n"
                      "    B.jpg — знята 31.05.2025 19:00 — не падчас запісу трэку\n", text)
        text = self.report("хреб")
        self.assertIn("2025-06-02-ridge.yaml — Хрыбет\n  дапісаць: уражанні па-англійску\n"
                      "  практычная частка (practical:) — запоўнена 5 з 20. Няма:\n    вада: даліта на маршруце (water.refilled)\n", text)
        self.assertIn("    маркіроўка (marking)\n", text)
        self.assertNotIn("(surface)", text)
        self.assertNotIn("фота без месца", text)
        self.assertEqual(self.report("няма", "такога"), "Паходаў з «няма такога» у назве або імені файла няма.\n")

        # файл зменены пасля зборкі; новы паход без тура ў кэшы; файл выдалены, а паход яшчэ на сайце
        ridge_file = self.content / "2025-06-02-ridge.yaml"
        ridge_file.write_text(ridge_file.read_text(encoding="utf-8").replace("Красиво.", "Очень красиво."), encoding="utf-8")
        (self.content / "2025-06-04-new.yaml").write_text("date: 2025-06-04\ntitle: Новы\nkomoot: [9, 2]\n", encoding="utf-8")
        (self.content / "2025-06-01-lake.yaml").unlink()
        text = self.report()
        for line in ("  .\\hike komoot — няма тураў у кэшы: 2025-06-04-new (9)",
                     "  .\\hike fetch  — няма надвор'я або яно няпоўнае: 2025-06-04-new",
                     "  .\\hike build  — яшчэ не на сайце: 2025-06-04-new",
                     "  " + BUILD_CHANGED + "2025-06-02-ridge",
                     "  .\\hike build  — на сайце ёсць, а файла няма або паход схаваны: 2025-06-01-lake"):
            self.assertIn(line + "\n", text)
        self.assertLess(text.index("hike komoot"), text.index("hike fetch"), "каманды — у парадку запуску")
        self.assertLess(text.index("hike fetch"), text.index("hike build"))
        self.assertIn("2025-06-04  Новы", text)
        self.assertIn("?", text.split("2025-06-04  Новы")[1].splitlines()[0], "колькі фота на сайце, яшчэ невядома")

        import hike
        self.assertEqual(hike.COMMANDS["status"], "status")
        self.assertIn("status ", hike.__doc__)


class TestReview1100(StatusProject):
    """Рэгрэсіі па review_2026-10-14_v1.10.0.md."""

    def built(self, extra="", **fetched):
        """Адзін паход h (квадрат 4 км), усё спампавана і сабрана."""
        self.hike(extra)
        self.fetch(names=self.KEDA, **fetched)
        return self.build()[0]

    def edit(self, old, new):
        text = (self.content / "h.yaml").read_text(encoding="utf-8")
        self.assertIn(old, text)
        (self.content / "h.yaml").write_text(text.replace(old, new), encoding="utf-8")

    def test_status_asks_to_build_after_any_change_of_the_inputs(self):  # №1
        own = ["content\\places.yaml — няма беларускай назвы (на сайце лацінкай): Keda", 'дапішыце радкі выгляду   Keda: "…"']
        index = self.built(f"photos_folder: {self.FOLDER}\npractical:\n  water: {{available: false}}\n")
        self.photo(self.FOLDER, "A.jpg", "2025:06:01 09:00:00")
        self.assertEqual(self.todo(), [BUILD_PHOTOS + "h"] + own)
        index = self.build()[0]
        self.assertEqual((self.todo(), len(index["h"]["inputs"])), (own, 12))

        # практычная частка
        self.edit("available: false", "available: true")
        self.assertEqual(self.todo(), [BUILD_CHANGED + "h"] + own)
        self.assertIs(common.read_json(build.DOCS / "data" / "hikes.json")["hikes"][0]["practical"]["water"]["available"], False)
        self.assertIs(self.build()[0]["h"]["practical"]["water"]["available"], True)
        self.assertEqual(self.todo(), own, "пасля зборкі падказкі няма")
        # каментарый і парадак радкоў у файле сайт не мяняюць — лішняй падказкі няма
        self.edit("title: {be: H}", "title: {be: H}   # назва")
        self.assertEqual(self.todo(), own)
        # любое іншае поле: папраўка гадзінніка камеры
        self.edit("practical:", 'photo_clock:\n  - {shift: "+1h"}\npractical:')
        self.assertEqual(self.todo(), [BUILD_CHANGED + "h"] + own)
        self.build()

        # слоўнік назваў: запіс пра іншую вёску паходу не датычыць
        self.places('Tago: "Таго"\n')
        self.assertEqual(self.todo(), own)
        self.places('Tago: "Таго"\nKeda: "Кеда"\n')
        self.assertEqual(self.todo(), [BUILD_CHANGED + "h"])
        self.assertEqual(common.read_json(build.DOCS / "data" / "hikes.json")["hikes"][0]["facts"]["start"]["place"]["be"], "Keda")
        self.assertEqual(self.build()[0]["h"]["facts"]["start"]["place"]["be"], "Кеда")
        self.assertEqual(self.todo(), ALL_DONE)

        # спампаваныя звесткі: новыя падказкі OpenStreetMap ужо ў кэшы, але яшчэ не на сайце
        (build.CACHE / "osm" / "h.json").unlink()
        self.assertEqual(self.todo(), [".\\hike fetch  — няма падказак OpenStreetMap: h", BUILD_CHANGED + "h"])
        self.fetch(names=self.KEDA, osm=[VIEW])
        self.assertEqual(self.todo(), [BUILD_CHANGED + "h"])
        self.assertEqual(len(self.build()[0]["h"]["hints"]), 1)
        self.assertEqual(self.todo(), ALL_DONE)

        # фота: зменена, выдалена; дыск адключаны — невядома, падказкі няма
        self.photo(self.FOLDER, "B.jpg", "2025:06:01 10:00:00")
        self.assertEqual(self.todo(), [BUILD_PHOTOS + "h"])
        self.build()
        self.assertEqual(self.todo(), ALL_DONE)
        (self.photos_root / self.FOLDER / "Сайт" / "B.jpg").unlink()
        self.assertEqual(self.todo(), [BUILD_PHOTOS + "h"])
        saved, self.cfg = self.cfg, {**self.cfg, "photos_root": str(self.root / "няма")}
        self.assertEqual(self.todo(), [BUILD_CHANGED + "h"], "змяніліся налады, а пра фота без дыска невядома")
        self.cfg = saved
        self.build()
        self.assertEqual(self.todo(), ALL_DONE)

        # сайт сабраны іншай версіяй скрыптоў
        with unittest.mock.patch.object(status, "read_version", lambda: "9.9.9"):
            self.assertEqual(self.todo(), [f".\\hike build  — сайт сабраны версіяй {common.read_version()}, а скрыпты цяпер версіі 9.9.9"])

        # тура няма ў кэшы: падказка спампаваць, а збіраць нанова няма чаго, пакуль не змяніўся файл
        (build.CACHE / "komoot" / "tours" / "1.json").unlink()
        self.assertEqual(self.todo(), [".\\hike komoot — няма тураў у кэшы: h (1)", BUILD_CHANGED + "h"])
        self.build()
        self.assertEqual(self.todo(), [".\\hike komoot — няма тураў у кэшы: h (1)"])
        self.places('Keda: "Кэда"\n')
        self.assertEqual(self.todo(), [".\\hike komoot — няма тураў у кэшы: h (1)", BUILD_CHANGED + "h"])
        self.assertEqual(self.build()[0]["h"]["facts"]["start"]["place"]["be"], "Кэда")
        self.assertEqual(self.todo(), [".\\hike komoot — няма тураў у кэшы: h (1)"])

    def test_status_asks_to_fetch_incomplete_weather_and_a_missing_way_back(self):  # №2
        self.add_tour(1, "2025-06-01T04:00:00Z", path((0, 0), (3000, 0)))  # 3 км у адзін бок: патрэбна і дарога ад фінішу
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\n", encoding="utf-8")
        self.places('Keda: "Кеда"\n')
        home = f"{fetch.API_ROUTE}41.59822,41.62692;"
        self.fetch(names=self.KEDA, weather=lambda params: hourly_answer(params, hole="precipitation"),
                   route=lambda url: ROUTE if url.startswith(home) else NO_ROUTE)
        h = self.build()[0]["h"]
        self.assertEqual((common.read_json(build.CACHE / "weather" / "h.json")["days"]["2025-06-01"]["complete"], sorted(h["drive"])),
                         (False, ["from", "to"]))
        self.assertIn("weather", h["days"][0])
        self.assertEqual(self.todo(), [".\\hike fetch  — няма надвор'я або яно няпоўнае: h", ".\\hike fetch  — няма дарогі на машыне: h"])
        # тое ж самае запытвае і сам `hike fetch`
        self.fetch(names=self.KEDA)
        self.assertEqual(sorted(kind for kind, _ in self.calls), ["route", "weather"])
        self.assertEqual(self.todo(), [BUILD_CHANGED + "h"], "спампавана, засталося сабраць")
        h = self.build()[0]["h"]
        self.assertEqual(sorted(h["drive"]), ["back", "from", "to"])
        self.assertEqual(self.todo(), ALL_DONE)

        # трэк без часу (імпартаваны GPX): надвор'е для яго не пытаецца ні `hike fetch`, ні status
        tour = common.read_json(build.CACHE / "komoot" / "tours" / "1.json")
        tour["coords"] = [c[:3] for c in tour["coords"]]
        common.write_json(build.CACHE / "komoot" / "tours" / "1.json", tour)
        (build.CACHE / "weather" / "h.json").unlink()
        self.assertEqual(self.todo(), [BUILD_CHANGED + "h"])
        self.fetch(names=self.KEDA)
        self.assertNotIn("weather", [kind for kind, _ in self.calls])
        self.build()
        self.assertEqual(self.todo(), ALL_DONE)

    def test_removed_own_names_give_back_the_names_from_openstreetmap(self):  # №3
        self.places('Keda: {be: "Кэда", ru: "НЯПРАВІЛЬНА"}\n')
        index = self.built()
        self.assertEqual(self.names(index), {"be": "Кэда", "en": "Keda", "ru": "НЯПРАВІЛЬНА"})
        self.assertEqual(index["h"]["facts"]["start"]["placeOsm"], self.KEDA, "назва з OpenStreetMap захоўваецца побач")

        (build.CACHE / "komoot" / "tours" / "1.json").unlink()  # далей — без тура: трэк і назвы ранейшыя
        (build.CACHE / "geocode.json").unlink()                 # і без кэша назваў
        self.places('Keda: "Кеда"\n')
        for attempt in (1, 2):
            index, out = self.build()
            self.assertIn("застаецца апублікаваны раней трэк", out)
            self.assertEqual(self.names(index), {"be": "Кеда", "en": "Keda", "ru": "Кеда"}, f"ru прыбраны са слоўніка, зборка {attempt}")
        self.places("{}\n")
        for attempt in (1, 2):
            index = self.build()[0]
            self.assertEqual(self.names(index), self.KEDA, f"запісу ў слоўніку больш няма, зборка {attempt}")
            self.assertNotIn("placeOsm", index["h"]["facts"]["start"])
            self.assertNotIn("placeOsm", index["h"]["days"][0]["facts"]["finish"])
        self.places('Keda: {ru: "Кэда"}\n')
        self.assertEqual(self.names(self.build()[0]), {"be": "Keda", "en": "Keda", "ru": "Кэда"}, "і зноў дадаць можна")
        (self.content.parent / "places.yaml").unlink()
        self.assertEqual(self.names(self.build()[0]), self.KEDA)

    def test_status_reports_a_deleted_hike_when_no_files_are_left(self):  # №4
        self.built()
        self.places('Keda: "Кеда"\n')
        self.build()
        self.assertEqual(self.todo(), ALL_DONE)
        (self.content / "h.yaml").unlink()
        gone = "\nШто зрабіць\n  .\\hike build  — на сайце ёсць, а файла няма або паход схаваны: h\n"
        self.assertEqual(self.report(), "Паходаў пакуль няма: content/hikes пустая.\n" + gone)
        self.assertEqual(self.report("h"), "Паходаў з «h» у назве або імені файла няма.\n" + gone)
        self.assertEqual(self.report("няма такога"), "Паходаў з «няма такога» у назве або імені файла няма.\n")
        self.assertEqual(self.build()[0], {})
        self.assertEqual(self.report(), "Паходаў пакуль няма: content/hikes пустая.\n")
        self.assertEqual(self.report("h"), "Паходаў з «h» у назве або імені файла няма.\n")


class TestReview1101(StatusProject):
    """Рэгрэсіі па review_2026-10-14_v1.10.1.md."""

    def test_status_asks_to_build_until_the_photos_are_really_on_the_site(self):  # №1
        self.hike(f"photos_folder: {self.FOLDER}\n")
        self.places('Keda: "Кеда"\n')
        self.photo(self.FOLDER, "A.jpg", "2025:06:01 09:00:00")
        self.fetch(names=self.KEDA)
        was = self.build()[0]["h"]["photos"][0]["src"]
        self.assertEqual(self.todo(), ALL_DONE)

        # арыгінал заменены іншай выявай; зборка апрацавала фота і спынілася да запісу індэкса
        source = self.photos_root / self.FOLDER / "Сайт" / "A.jpg"
        exif = Image.Exif()
        exif.get_ifd(0x8769)[36867] = "2025:06:01 09:00:00"
        Image.new("RGB", (80, 60), (10, 200, 90)).save(source, "JPEG", exif=exif)
        self.assertEqual(self.todo(), [BUILD_PHOTOS + "h"])
        with unittest.mock.patch.object(pages, "share_pages", side_effect=OSError("simulated locked output")):
            with self.assertRaises(OSError):
                self.build()
        index = common.read_json(build.DOCS / "data" / "hikes.json")["hikes"][0]
        manifest = common.read_json(build.CACHE / "photos" / "h.json")
        self.assertEqual(index["photos"][0]["src"], was, "на сайце яшчэ старое фота")
        self.assertNotEqual(f"photos/h/{manifest['A.jpg']['name']}.webp", was, "а маніфест ужо пра новае")
        for attempt in (1, 2):
            self.assertEqual(self.todo(), [BUILD_PHOTOS + "h"], f"зборка не скончылася, праверка {attempt}")
        now = self.build()[0]["h"]["photos"][0]
        self.assertNotEqual(now["src"], was)
        self.assertEqual(self.todo(), ALL_DONE)

        # апублікаваныя копіі страчаны, а арыгінал ёсць: зборка іх адновіць
        for lost in ("thumb", "src"):
            (build.DOCS / now[lost]).unlink()
            self.assertEqual(self.todo(), [BUILD_PHOTOS + "h"], lost)
        self.assertEqual(self.build()[0]["h"]["photos"][0], now)
        self.assertEqual(self.todo(), ALL_DONE)
        # без дыска з арыгіналамі аднавіць няма з чаго — і падказкі няма (застаецца толькі змена налад)
        self.cfg = {**self.cfg, "photos_root": str(self.root / "няма")}
        self.build()
        (build.DOCS / now["thumb"]).unlink()
        self.assertEqual(self.todo(), ALL_DONE)

    def test_status_and_fetch_use_the_same_days_when_a_tour_is_missing(self):  # №2
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))
        self.add_tour(2, "2025-06-02T04:00:00Z", path(*SQUARE))
        # дата ў файле пазнейшая за першы дзень трэку; цана без даты — на дзень пачатку паходу (1 чэрвеня)
        (self.content / "h.yaml").write_text(
            "date: 2025-06-02\ntitle: {be: H}\nkomoot: [1, 2]\npractical:\n  getting_there:\n    - {type: taxi, price: 80}\n",
            encoding="utf-8")
        self.places('Keda: "Кеда"\n')
        self.fetch(names=self.KEDA)
        index = self.build()[0]
        self.assertEqual((index["h"]["date"], index["h"]["practical"]["gettingThere"][0]["rateDate"]), ("2025-06-01", "2025-06-01"))
        self.assertEqual(sorted(common.read_json(build.CACHE / "rates.json")["USD"]), ["2025-06-01"])
        self.assertEqual(self.todo(), ALL_DONE)

        # тура другога дня і кэша надвор'я больш няма; на сайце застаецца ранейшы трэк
        (build.CACHE / "komoot" / "tours" / "2.json").unlink()
        (build.CACHE / "weather" / "h.json").unlink()
        self.assertIn("застаецца апублікаваны раней трэк", self.build()[1])
        need = [".\\hike komoot — няма тураў у кэшы: h (2)"]
        self.assertEqual(self.todo(), need + [".\\hike fetch  — няма надвор'я або яно няпоўнае: h"],
                         "тое, што спампуе `hike fetch` для тура, які ёсць; курс на 2 чэрвеня не патрэбны")
        self.fetch(names=self.KEDA)
        self.assertEqual([kind for kind, _ in self.calls], ["weather"], "і сам `hike fetch` пытае тое ж: адзін дзень надвор'я")
        self.assertEqual(self.todo(), need + [BUILD_CHANGED + "h"])
        self.build()
        self.assertEqual(self.todo(), need)
        self.fetch(names=self.KEDA)
        self.assertEqual(self.calls, [], "больш пытаць няма чаго")


class TestReview1102(StatusProject):
    """Рэгрэсіі па review_2026-10-14_v1.10.2.md."""

    def test_removed_photo_subfolder_clears_the_photo_cache(self):  # №1
        place = "practical:\n  places:\n    - {type: shelter, photo: A.jpg}\n    - {type: table, km: 2.5}\n"

        def state():
            index, out = self.build()
            h = index["h"]
            return ([Path(p["src"]).stem.split("-")[0] for p in h["photos"]],
                    [(item["photo"], item["pos"] and item["pos"]["km"]) for item in h["practical"]["places"]],
                    "месца не вызначана — фота A.jpg няма сярод фота паходу" in out, common.read_json(build.CACHE / "photos" / "h.json"))

        def project():
            self.tearDown()
            self.setUp()
            self.hike(f"photos_folder: {self.FOLDER}\n" + place)
            self.places('Keda: "Кеда"\n')
            self.photo(self.FOLDER, "A.jpg", "2025:06:01 09:00:00")
            self.fetch(names=self.KEDA)
            photos, places, warned, manifest = state()
            self.assertEqual((photos, places, warned, list(manifest)), (["a"], [(0, 1.0), (None, 2.5)], False, ["A.jpg"]))
            self.assertEqual(self.todo(), ALL_DONE)

        # выдалена ўся падтэчка «Сайт»; тэчка паходу і дыск на месцы
        project()
        shutil.rmtree(self.photos_root / self.FOLDER / "Сайт")
        for attempt in (1, 2):
            self.assertEqual(state(), ([], [(None, None), (None, 2.5)], True, {}), f"зборка {attempt}: фота няма — няма і месца па ім")
            self.assertEqual(self.todo(), ALL_DONE, f"зборка {attempt}: збіраць больш няма чаго")
        self.assertFalse((build.DOCS / "photos" / "h").exists())
        # фота вярнулі — вяртаецца і месца
        self.photo(self.FOLDER, "A.jpg", "2025:06:01 09:00:00")
        self.assertEqual(self.todo(), [BUILD_PHOTOS + "h"])
        self.assertEqual(state()[:3], (["a"], [(0, 1.0), (None, 2.5)], False))
        self.assertEqual(self.todo(), ALL_DONE)

        # прыбрана поле photos_folder
        project()
        text = (self.content / "h.yaml").read_text(encoding="utf-8")
        (self.content / "h.yaml").write_text(text.replace(f"photos_folder: {self.FOLDER}\n", ""), encoding="utf-8")
        self.assertEqual(self.todo(), [BUILD_CHANGED + "h"])
        for attempt in (1, 2):
            self.assertEqual(state(), ([], [(None, None), (None, 2.5)], True, {}), f"зборка {attempt}")
            self.assertEqual(self.todo(), ALL_DONE, f"зборка {attempt}")

        # дыск з фота недаступны — гэта не выдаленне: фота, кэш і месца застаюцца
        project()
        self.cfg = {**self.cfg, "photos_root": str(self.root / "няма")}
        photos, places, warned, manifest = state()
        self.assertEqual((photos, places, warned, list(manifest)), (["a"], [(0, 1.0), (None, 2.5)], False, ["A.jpg"]))

    def komoot(self, meta, answers, argv=()):
        """`hike komoot` без сеткі: уваход, спіс тураў і каардынаты падмененыя."""
        out = io.StringIO()
        with unittest.mock.patch.multiple(komoot, KOMOOT_DIR=build.CACHE / "komoot", load_config=lambda: self.cfg,
                                          login=lambda *a: ("u", "t", "D"), list_recorded_tours=lambda *a: meta,
                                          fetch_coordinates=lambda session, tid, auth: answers[tid]), \
                unittest.mock.patch("builtins.input", lambda *a: "e@example.com"), \
                unittest.mock.patch("getpass.getpass", lambda *a: "p"), \
                unittest.mock.patch("time.sleep", lambda *a: None), unittest.mock.patch("sys.stdout", out):
            try:
                komoot.main(list(argv))
                self.exit_code = 0
            except SystemExit as stop:
                self.exit_code = stop.code
        return out.getvalue()

    def test_status_asks_for_komoot_until_the_tour_is_really_updated(self):  # №2
        tour = lambda revision, metres: {  # noqa: E731
            "id": 1, "name": "t1", "sport": "hike", "type": "tour_recorded", "status": "public", "date": "2025-06-01T04:00:00Z",
            "changed_at": revision, "distance": metres, "duration": 14400, "time_in_motion": 10800, "elevation_up": 400,
            "elevation_down": 400, "start_point": None}
        square = coords(path(*SQUARE))
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\n", encoding="utf-8")
        self.places('Keda: "Кеда"\n')
        self.assertIn("спампавана трэкаў: 1", self.komoot([tour("first", 4000)], {1: square}))
        self.fetch(names=self.KEDA)
        self.assertEqual(self.build()[0]["h"]["stats"]["distance"], 4.0)
        self.assertEqual(self.todo(), ALL_DONE)

        # тур змянілі ў Komoot, але адказ з каардынатамі прыйшоў пусты: у кэшы застаецца ранейшы трэк
        old = ".\\hike komoot — тур у кэшы стары: мінулы раз Komoot аддаў яго няпоўным або тур змяніўся: h (1)"
        self.assertIn("спампавана трэкаў: 0, без каардынат: 1", self.komoot([tour("second", 9000)], {1: []}))
        self.assertIs(common.read_json(build.CACHE / "komoot" / "tours" / "1.json")["incomplete"], True)
        self.assertEqual(self.todo(), [old, BUILD_CHANGED + "h"])
        self.assertEqual(self.build()[0]["h"]["stats"]["distance"], 4.0, "на сайце старыя лічбы")
        for attempt in (1, 2):
            self.assertEqual(self.todo(), [old], f"імпарт яшчэ трэба паўтарыць, праверка {attempt}")
        # наступны звычайны імпарт атрымаў тур цалкам: засталося толькі сабраць
        self.assertIn("спампавана трэкаў: 1", self.komoot([tour("second", 9000)], {1: square}))
        self.assertEqual(self.todo(), [BUILD_CHANGED + "h"])
        self.assertEqual(self.build()[0]["h"]["stats"]["distance"], 9.0)
        self.assertEqual(self.todo(), ALL_DONE)

        # імпарт перарваўся пасля запісу спіса тураў: спіс ужо новы, а сам тур яшчэ ранейшы
        common.write_json(build.CACHE / "komoot" / "tours.json", [tour("third", 9000)])
        self.assertEqual(self.todo(), [old, BUILD_CHANGED + "h"])
        self.assertIn("спампавана трэкаў: 1", self.komoot([tour("third", 9000)], {1: square}))
        self.build()
        self.assertEqual(self.todo(), ALL_DONE)
        self.assertIn("спампавана трэкаў: 0.", self.komoot([tour("third", 9000)], {}), "тур актуальны — паўторна не пытаецца")
        # тура няма ў спісе тураў (спіс страчаны): параўнаць няма з чым — падказкі няма, пакуль няма меткі
        (build.CACHE / "komoot" / "tours.json").unlink()
        self.build()
        self.assertEqual(self.todo(), ALL_DONE)
        cached = common.read_json(build.CACHE / "komoot" / "tours" / "1.json")
        common.write_json(build.CACHE / "komoot" / "tours" / "1.json", {**cached, "incomplete": True})
        self.assertEqual(self.todo(), [old, BUILD_CHANGED + "h"])


class TestRelatedHikes(PracticalProject):
    """Іншыя паходы ў тое самае месца: з той жа назвай або па тым жа шляху."""

    @staticmethod
    def entry(slug, day, title, *corners):
        """Запіс паходу, як у індэксе сайта: лінія трэку [даўгата, шырата] праз вуглы ў метрах ад пачатку."""
        line = [[lng, lat] for lat, lng in path(*corners)] if corners else []
        box = [min(p[0] for p in line), min(p[1] for p in line), max(p[0] for p in line), max(p[1] for p in line)] if line else None
        return {"slug": slug, "date": day, "title": {"be": title, "en": ""}, "lines": [line] if line else [], "bbox": box}

    def test_same_title_or_same_way(self):
        e = self.entry
        hikes = [
            e("square-1", "2025-01-01", "Возера", *SQUARE),
            e("square-2", "2025-02-01", "Возера, другі раз", *SQUARE),                      # той самы шлях, іншая назва
            e("side", "2025-03-01", "Заходні бок", (0, 0), (1000, 0), (0, 0)),              # увесь ляжыць на квадраце
            e("half", "2025-04-01", "Палова", (0, 0), (1000, 0), (1000, -1000)),            # 2 км, з іх 1 км па квадраце
            e("third", "2025-05-01", "Траціна", (0, 0), (1000, 0), (1000, -2000)),          # 3 км, з іх 1 км па квадраце
            e("cross", "2025-06-01", "Упоперак", (500, -2000), (500, 3000)),                # толькі перасякае квадрат
            e("near", "2025-07-01", "Побач", (0, -400), (1000, -400)),                      # паралельна за 400 м
            e("far", "2025-08-01", " ВОЗЕРА ", (0, 9000), (1000, 9000)),                    # тая ж назва, іншае месца
            e("no-track", "2025-09-01", "Возера"),                                          # без трэку — толькі па назве
            e("alone", "2025-10-01", "Асобна", (20000, 0), (21000, 0)),
            e("untitled-1", "2025-11-01", ""), e("untitled-2", "2025-12-01", ""),           # пустыя назвы не «аднолькавыя»
        ]
        related = practical.related_hikes(hikes)
        self.assertEqual(set(related), {h["slug"] for h in hikes})
        self.assertEqual(related["square-1"], ["no-track", "far", "half", "side", "square-2"], "найноўшыя першымі")
        self.assertEqual(related["square-2"], ["half", "side", "square-1"])
        self.assertEqual(related["side"], ["third", "half", "square-2", "square-1"], "кароткі паход ляжыць на шляху даўжэйшых")
        self.assertEqual(related["half"], ["third", "side", "square-2", "square-1"], "палова шляху агульная з квадратам")
        self.assertEqual(related["third"], ["half", "side"],
                         "з квадратам агульная толькі траціна — не звязаны; «Палова» і «Заходні бок» ляжаць на ім цалкам")
        self.assertEqual([related[slug] for slug in ("cross", "near", "alone", "untitled-1", "untitled-2")], [[], [], [], [], []])
        self.assertEqual(related["far"], ["no-track", "square-1"], "назва параўноўваецца без уліку рэгістра і прабелаў")
        self.assertEqual(related["no-track"], ["far", "square-1"])
        self.assertEqual(practical.related_hikes([]), {})
        # парадак паходаў у спісе на вынік не ўплывае
        self.assertEqual(practical.related_hikes(hikes[::-1]), related)
        # трэк з некалькіх ліній (два дні) і лінія з аднаго пункта
        two = {**e("two-days", "2026-01-01", "Два дні", (0, 0), (1000, 0)), "lines": [
            [[lng, lat] for lat, lng in path((0, 0), (1000, 0))], [[lng, lat] for lat, lng in path((1000, 0), (1000, 1000))], [[42.0, 41.0]]]}
        self.assertEqual(practical.related_hikes([hikes[0], two])["two-days"], ["square-1"])

    def test_build_writes_related_hikes_to_the_index(self):
        self.photos_root.mkdir()
        tours = {"a": (1, "2025-06-01", "Возера", SQUARE), "b": (2, "2025-07-01", "Возера, другі раз", SQUARE),
                 "c": (3, "2025-08-01", "Іншае", [(20000, 0), (21000, 0)]), "d": (4, "2025-09-01", "Возера", [(40000, 0), (41000, 0)])}
        for slug, (tid, day, title, corners) in tours.items():
            self.add_tour(tid, f"{day}T04:00:00Z", path(*corners))
            (self.content / f"{slug}.yaml").write_text(f"date: {day}\ntitle: {{be: \"{title}\"}}\nkomoot: [{tid}]\n", encoding="utf-8")
        (self.content / "hidden.yaml").write_text("date: 2025-10-01\ntitle: {be: Возера}\nkomoot: [1]\nhidden: true\n", encoding="utf-8")
        index = self.build()[0]
        self.assertEqual({slug: h.get("related") for slug, h in index.items()},
                         {"a": ["d", "b"], "b": ["a"], "c": None, "d": ["a"]}, "схаваны паход у спасылкі не трапляе")
        # тура няма ў кэшы: застаецца ранейшы трэк — і спасылкі па ім
        (build.CACHE / "komoot" / "tours" / "2.json").unlink()
        index, out = self.build()
        self.assertIn("застаецца апублікаваны раней трэк", out)
        self.assertEqual((index["a"]["related"], index["b"]["related"]), (["d", "b"], ["a"]))
        # паход перайменавалі і перанеслі ў іншае месца: спасылкі знікаюць у абодвух
        self.add_tour(4, "2025-09-01T04:00:00Z", path((40000, 0), (41000, 0)))
        (self.content / "d.yaml").write_text("date: 2025-09-01\ntitle: {be: Гара}\nkomoot: [4]\n", encoding="utf-8")
        index = self.build()[0]
        self.assertEqual((index["a"]["related"], index["d"].get("related")), (["b"], None))


class TestReview1110(PracticalProject):
    """Рэгрэсіі па review_2026-10-14_v1.11.0.md."""

    @staticmethod
    def entry(slug, lines, lat0=42.0, lng0=41.0):
        """Запіс паходу, як у індэксе сайта: лініі з пунктаў (усход, поўнач) у метрах ад [lng0, lat0], акругленыя
        да 5 знакаў, як іх запісвае `hike build`."""
        k = math.pi / 180 * practical.EARTH_R
        made = [[[round(lng0 + e / (k * math.cos(math.radians(lat0))), 5), round(lat0 + n / k, 5)] for e, n in line] for line in lines]
        pts = [p for line in made for p in line]
        box = [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]
        return {"slug": slug, "date": "2025-01-01", "title": {"be": slug, "en": ""}, "lines": made, "bbox": box}

    def related(self, *hikes):
        return {slug: bool(others) for slug, others in practical.related_hikes(list(hikes)).items()}

    def test_short_tours_do_not_inflate_the_shared_way(self):  # №1
        b = self.entry("b", [[(0, 0), (2000, 0)]])
        # A: 60 м уздоўж B і 500 м убаку ад яго — агульная толькі дзясятая частка шляху
        whole = self.entry("a", [[(0, 0), (60, 0)], [(0, 1000), (500, 1000)]])
        split = self.entry("a", [[(10 * i, 0), (10 * i + 10, 0)] for i in range(6)] + [[(0, 1000), (500, 1000)]])
        dense = self.entry("a", [[(10 * i, 0) for i in range(7)], [(0, 1000), (500, 1000)]])
        for a in (whole, split, dense):
            self.assertEqual(self.related(a, b), {"a": False, "b": False}, len(a["lines"]))
        # тое ж з паўторамі пунктаў і турамі з аднаго пункта: вагі ў іх няма
        points = self.entry("a", [[(0, 0), (0, 0), (60, 0)], [(30, 0)], [(30, 0)], [(0, 1000), (500, 1000)]])
        self.assertEqual(self.related(points, b), {"a": False, "b": False})

        # сапраўды больш за палову: 600 м з 1000 ідуць па B — і з адным турам, і кароткімі турамі
        majority = self.entry("a", [[(0, 0), (600, 0)], [(0, 1000), (400, 1000)]])
        pieces = self.entry("a", [[(37 * i, 0), (min(37 * i + 37, 600), 0)] for i in range(17)] + [[(0, 1000), (130, 1000)], [(130, 1000), (400, 1000)]])
        for a in (majority, pieces):
            self.assertEqual(self.related(a, b), {"a": True, "b": True}, len(a["lines"]))
        # мяжа: 45 % і 55 % — апошнія няпоўныя кавалкі (130 м = 50 + 50 + 30) улічваюцца па іх даўжыні
        self.assertEqual(self.related(self.entry("a", [[(0, 0), (450, 0)], [(0, 1000), (550, 1000)]]), b), {"a": False, "b": False})
        self.assertEqual(self.related(self.entry("a", [[(0, 0), (550, 0)], [(0, 1000), (450, 1000)]]), b), {"a": True, "b": True})
        self.assertEqual(self.related(self.entry("a", [[(0, 0), (130, 0)], [(0, 1000), (100, 1000)]]), b), {"a": True, "b": True})
        self.assertEqual(self.related(self.entry("a", [[(0, 0), (130, 0)], [(0, 1000), (140, 1000)]]), b), {"a": False, "b": False})

    def test_parallel_ways_far_north(self):  # №2
        for lat in (42.0, 70.0, 80.0, -70.0):
            for gap, together in ((130, True), (200, False)):
                one = self.entry("a", [[(0, 0), (0, 1000)]], lat0=lat)
                two = self.entry("b", [[(gap, 0), (gap, 1000)]], lat0=lat)
                self.assertEqual(self.related(one, two), {"a": together, "b": together}, (lat, gap))
                # трэк, які ідзе ўздоўж шыраты, — з тым жа вынікам
                one = self.entry("a", [[(0, 0), (1000, 0)]], lat0=lat)
                two = self.entry("b", [[(0, gap), (1000, gap)]], lat0=lat)
                self.assertEqual(self.related(one, two), {"a": together, "b": together}, (lat, gap, "уздоўж шыраты"))

        # поўная зборка на 70° — з лініямі, акругленымі ў індэксе
        self.photos_root.mkdir()
        k = math.pi / 180 * practical.EARTH_R
        for tid, east in ((1, 0), (2, 130), (3, 400)):
            pts = [(70.0 + n / k, 41.0 + east / (k * math.cos(math.radians(70.0)))) for n in range(0, 1001, 20)]
            self.add_tour(tid, "2025-06-01T04:00:00Z", pts)
            (self.content / f"t{tid}.yaml").write_text(f"date: 2025-06-01\ntitle: {{be: Шлях {tid}}}\nkomoot: [{tid}]\n", encoding="utf-8")
        index = self.build()[0]
        self.assertEqual({slug: h.get("related") for slug, h in index.items()}, {"t1": ["t2"], "t2": ["t1"], "t3": None})


class TestReview1111(FetchProject):
    """Рэгрэсіі па review_2026-10-14_v1.11.1.md."""

    def test_osm_area_covers_the_hint_radius_at_any_latitude(self):  # №1
        k = math.pi / 180 * practical.EARTH_R
        nodes, wanted = [], {}
        for tid, lat0 in ((1, 42.0), (2, 70.0), (3, -70.0)):
            east = lambda metres: 41.0 + metres / (k * math.cos(math.radians(lat0)))  # noqa: E731
            self.add_tour(tid, "2025-06-01T04:00:00Z", [(lat0 + n / k, 41.0) for n in range(0, 1001, 20)])
            (self.content / f"m{tid}.yaml").write_text(f"date: 2025-06-01\ntitle: {{be: M{tid}}}\nkomoot: [{tid}]\n", encoding="utf-8")
            # крыніцы ўздоўж мерыдыянальнага трэку: за 200 і 240 м на ўсход і захад — і за 300 м, далей за мяжу
            # (у розных месцах трэку: аднолькавыя аб'екты бліжэй за 60 м адзін ад аднаго — адна падказка)
            for i, (north, metres) in enumerate(((200, 200), (200, -200), (600, 240), (600, -240), (400, 300))):
                node = osm_node(tid * 10 + i, 0, 0, natural="spring")
                nodes.append({**node, "lat": lat0 + north / k, "lon": east(metres)})
            wanted[f"m{tid}"] = [200, 200, 240, 240]
        _, out = self.fetch(osm=nodes)
        self.assertEqual(self.exit_code, 0, out)
        index = self.build()[0]
        found = {slug: sorted(h["pos"]["off"] for h in index[slug]["hints"]) for slug in wanted}
        self.assertEqual(found, wanted, "усе крыніцы ў радыусе 250 м; за 300 м — адкінутая")
        # для шырот Грузіі вобласць пошуку ранейшая: спампаваныя раней падказкі не састарэлі
        day = practical.raw_days([1], 4)
        self.assertEqual(practical.osm_bbox(day), [41.996, 40.996, 42.013, 41.004])
        far = practical.osm_bbox(practical.raw_days([2], 4))
        reach = 250 / (k * math.cos(math.radians(70.009)))  # 250 м па даўгаце на поўначы трэку
        self.assertTrue(far[3] - 41.0 >= reach and 41.0 - far[1] >= reach, (far, reach))
        # кэш з ранейшай вузкай вобласцю на поўначы — састарэлы: `hike fetch` пытае OSM нанова
        cached = common.read_json(build.CACHE / "osm" / "m2.json")
        common.write_json(build.CACHE / "osm" / "m2.json", {**cached, "key": practical.osm_key([69.996, 40.996, 70.013, 41.004])})
        self.fetch(osm=nodes)
        self.assertEqual([kind for kind, _ in self.calls].count("osm"), 1)
        self.fetch(osm=nodes)
        self.assertNotIn("osm", [kind for kind, _ in self.calls], "актуальны кэш — без запытаў")

    def test_fetch_and_komoot_report_failures_with_exit_code(self):  # №2
        import hike
        self.hike("practical:\n  getting_there:\n    - {type: taxi, price: 80}\n")
        offline = lambda *a, **k: (_ for _ in ()).throw(fetch.requests.ConnectionError("offline"))  # noqa: E731

        # сэрвісы не адказваюць: надвор'е, OSM, дарога, назва месца, курс — пяць няўдач, код 1, нічога не запісана
        _, out = self.fetch(weather=offline, rate=fetch_failure(), osm=fetch_failure(), route=offline, names=offline)
        self.assertEqual(self.exit_code, 1, out)
        self.assertIn("Не атрымана: 5.", out)
        self.assertFalse((build.CACHE / "weather" / "h.json").exists())

        # усё атрымана: код 0; ужо актуальны кэш — без запытаў, таксама 0
        _, out = self.fetch()
        self.assertEqual(self.exit_code, 0, out)
        _, out = self.fetch()
        self.assertEqual((self.exit_code, self.calls), (0, []), out)

        # сэрвіс надвор'я зноў не адказаў: астатняе спампаванае не кранаецца, код 1
        files = lambda: {str(p.relative_to(build.CACHE)): p.read_bytes() for p in build.CACHE.rglob("*.json")  # noqa: E731
                         if "komoot" not in p.parts and "weather" not in p.parts}
        kept = files()
        common.write_json(build.CACHE / "weather" / "h.json", {"days": {}})
        _, out = self.fetch(weather=offline)
        self.assertEqual(self.exit_code, 1, out)
        self.assertEqual(common.read_json(build.CACHE / "weather" / "h.json"), {"days": {}}, "няўдалы адказ нічога не перазапісвае")
        self.assertEqual(files(), kept)
        # няпоўнае надвор'е — таксама «не скончана»
        _, out = self.fetch(weather=lambda params: hourly_answer(params, hole="precipitation"))
        self.assertEqual(self.exit_code, 1, out)
        self.assertIn("Няпоўных адказаў: 1.", out)
        _, out = self.fetch()
        self.assertEqual(self.exit_code, 0, out)

        # праз дыспетчар hike — тое ж: выключэнне SystemExit(1) становіцца кодам выхаду працэсу
        with unittest.mock.patch.object(sys, "argv", ["hike", "fetch"]), unittest.mock.patch.object(fetch, "main", lambda argv: (_ for _ in ()).throw(SystemExit(1))):
            with self.assertRaises(SystemExit) as stop:
                hike.main()
        self.assertEqual(stop.exception.code, 1)

        # Komoot: тур без каардынат — код 1, добры трэк застаецца; поўны адказ — код 0
        tour = {"id": 1, "name": "t1", "sport": "hike", "type": "tour_recorded", "status": "public", "date": "2025-06-01T04:00:00Z",
                "changed_at": "new", "distance": 4000, "duration": 14400, "time_in_motion": 10800, "elevation_up": 400,
                "elevation_down": 400, "start_point": None}
        before = common.read_json(build.CACHE / "komoot" / "tours" / "1.json")["coords"]
        out = TestReview1102.komoot(self, [tour], {1: []})
        self.assertEqual(self.exit_code, 1, out)
        self.assertEqual(common.read_json(build.CACHE / "komoot" / "tours" / "1.json")["coords"], before)
        out = TestReview1102.komoot(self, [tour], {1: coords(path(*SQUARE))})
        self.assertEqual(self.exit_code, 0, out)
        out = TestReview1102.komoot(self, [tour], {})
        self.assertEqual((self.exit_code, "спампавана трэкаў: 0." in out), (0, True), "усё актуальна — код 0")


class TestReview1112(StatusProject):
    """Рэгрэсіі па review_2026-10-14_v1.11.2.md."""

    def test_incomplete_weather_does_not_replace_complete_weather(self):  # №1
        holey = lambda params: hourly_answer(params, hole="precipitation")  # noqa: E731
        cache = lambda: common.read_json(build.CACHE / "weather" / "h.json")["days"]["2025-06-01"]  # noqa: E731
        weather = lambda: self.build()[0]["h"]["days"][0]["weather"]  # noqa: E731
        self.hike()
        self.places('Keda: "Кеда"\n')
        self.fetch(names=self.KEDA)
        full = cache()
        self.assertTrue(full["complete"])
        published = weather()
        self.assertEqual(published["span"]["precip"], 0.5)
        self.assertEqual(self.todo(), ALL_DONE)

        # --refresh: сэрвіс адказаў, але без ападкаў — поўныя звесткі для таго ж трэку застаюцца
        _, out = self.fetch(["--refresh"], names=self.KEDA, weather=holey)
        self.assertEqual(self.exit_code, 1, out)
        self.assertEqual(cache(), {**full, "retry": True})
        self.assertIn("надвор'е прыйшло няпоўным — застаюцца ранейшыя поўныя звесткі", out)
        self.assertEqual(weather(), published, "на сайце — ранейшыя поўныя звесткі")
        self.assertEqual(self.todo(), [".\\hike fetch  — няма надвор'я або яно няпоўнае: h"], "абнаўленне не скончана")
        # наступны звычайны `hike fetch` спрабуе зноў: зноў няпоўны — усё як было; поўны — метка знікае
        _, out = self.fetch(names=self.KEDA, weather=holey)
        self.assertEqual(([kind for kind, _ in self.calls], self.exit_code, cache()), (["weather"], 1, {**full, "retry": True}), out)
        _, out = self.fetch(names=self.KEDA)
        self.assertEqual(([kind for kind, _ in self.calls], self.exit_code, cache()["complete"], "retry" in cache()), (["weather"], 0, True, False), out)
        self.build()
        self.assertEqual(self.todo(), ALL_DONE)

        # пусты кэш: няпоўныя звесткі лепш, чым ніякіх — захоўваюцца, як і раней
        (build.CACHE / "weather" / "h.json").unlink()
        _, out = self.fetch(names=self.KEDA, weather=holey)
        self.assertEqual((self.exit_code, cache()["complete"]), (1, False), out)
        self.assertNotIn("precip", weather()["span"])
        _, out = self.fetch(names=self.KEDA)
        self.assertEqual((self.exit_code, cache()["complete"]), (0, True), out)

        # трэк змяніўся: поўныя звесткі старога трэку не падыходзяць — захоўваецца няпоўны адказ для новага
        old_key = cache()["key"]
        self.add_tour(1, "2025-06-01T05:00:00Z", path(*SQUARE))
        _, out = self.fetch(names=self.KEDA, weather=holey)
        self.assertEqual((self.exit_code, cache()["complete"], "retry" in cache()), (1, False, False), out)
        self.assertNotEqual(cache()["key"], old_key)
        _, out = self.fetch(names=self.KEDA)
        self.assertEqual((self.exit_code, cache()["complete"]), (0, True), out)


class TestClimate(FetchProject):
    """Звычайнае надвор'е па месяцах у найвышэйшым пункце трэку (архіў Open-Meteo, CLIMATE_YEARS)."""

    PARAMS = {"latitude": 41.0, "longitude": 42.0, "start_date": "2020-01-01", "end_date": "2024-12-31"}

    def test_months_at_the_altitude_of_the_point(self):
        cached = {"years": [2020, 2024], "model": practical.CLIMATE_MODEL, **daily_answer(self.PARAMS)}
        self.assertTrue(practical.climate_ok(cached))
        months = practical.climate_months(cached, 1000.0)
        # тэмпература — як у даных (на вышыні 1000 м), ападкі: 10 або 11 дзён па 3 мм і астатнія па 0,5 мм
        self.assertEqual([(m["max"], m["min"]) for m in months], [(10 + n, n - 10) for n in range(1, 13)])
        self.assertEqual([m["wet"] for m in months], [10, 9, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10])
        self.assertEqual(months[0]["precip"], round((10 * 3.0 + 21 * 0.5)))  # студзень: 10 «мокрых» дзён і 21 сухі
        # на 1500 м — на 3,25 °C халадней; ападкі тыя ж
        high = practical.climate_months(cached, 1500.0)
        self.assertEqual([(m["max"], m["min"]) for m in high[:2]], [(round(11 - 3.25), round(-9 - 3.25)), (round(12 - 3.25), round(-8 - 3.25))])
        self.assertEqual([m["precip"] for m in high], [m["precip"] for m in months])

        # прапушчаныя даныя: менш за палову дзён месяца — без лічбаў; без ападкаў — тэмпература застаецца
        holey = {"years": [2020, 2024], "model": practical.CLIMATE_MODEL, **daily_answer(self.PARAMS, hole="precipitation_sum")}
        self.assertEqual({(m["precip"], m["wet"]) for m in practical.climate_months(holey, 1000.0)}, {(None, None)})
        self.assertEqual(practical.climate_months(holey, 1000.0)[6]["max"], 17)
        broken = {"years": [2020, 2024], "model": practical.CLIMATE_MODEL, **daily_answer(self.PARAMS)}
        broken["daily"]["temperature_2m_max"] = [None if day.startswith(("2020-03", "2021-03", "2022-03")) else v
                                                 for day, v in zip(broken["daily"]["time"], broken["daily"]["temperature_2m_max"])]
        self.assertEqual((practical.climate_months(broken, 1000.0)[2]["max"], practical.climate_months(broken, 1000.0)[2]["min"]), (None, -7))
        empty = {"years": [2020, 2024], "model": practical.CLIMATE_MODEL, **daily_answer(self.PARAMS)}
        for key in practical.CLIMATE_DAILY:
            empty["daily"][key] = [None] * len(empty["daily"]["time"])
        self.assertIsNone(practical.climate_months(empty, 1000.0))
        # кэш іншых гадоў, без вышыні або з няпоўнымі радамі не падыходзіць
        self.assertFalse(practical.climate_ok({**cached, "years": [2019, 2023]}))
        self.assertFalse(practical.climate_ok({**cached, "elevation": None}))
        self.assertFalse(practical.climate_ok({**cached, "daily": {**cached["daily"], "precipitation_sum": [1.0]}}))
        self.assertFalse(practical.climate_ok(None))

        # найвышэйшы пункт трэку і квадрат сеткі
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))  # вышыня расце да сярэдзіны шляху (1400 м)
        top = practical.climate_point(practical.raw_days([1], 4))
        self.assertEqual((round(top[0], 5), round(top[1], 5), top[2]), (round(41.0 + 1000 * M, 5), round(42.0 + 1000 * M / 0.7547, 5), 1400.0))
        self.assertEqual(practical.climate_key(top[0], top[1]), "41.0_42.0")
        self.assertIsNone(practical.climate_point([]))

    def test_fetch_and_build(self):
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))
        self.add_tour(2, "2025-07-01T04:00:00Z", path((0, 0), (500, 0), (0, 0)))           # той жа квадрат сеткі
        self.add_tour(3, "2025-08-01T04:00:00Z", path((30000, 0), (31000, 0), (30000, 0)))  # іншы квадрат
        for slug, tid in (("a", 1), ("b", 2), ("c", 3)):
            (self.content / f"{slug}.yaml").write_text(f"date: 2025-0{tid + 5}-01\ntitle: {{be: {slug}}}\nkomoot: [{tid}]\n", encoding="utf-8")
        _, out = self.fetch(climate=lambda params: daily_answer(params, elevation=900.0))
        asked = [(c["latitude"], c["longitude"], c["start_date"], c["end_date"], c["daily"]) for kind, c in self.calls if kind == "climate"]
        self.assertEqual(asked, [(41.0, 42.0, "2020-01-01", "2024-12-31", "temperature_2m_max,temperature_2m_min,precipitation_sum"),
                                 (41.3, 42.0, "2020-01-01", "2024-12-31", "temperature_2m_max,temperature_2m_min,precipitation_sum")],
                         "адзін запыт на квадрат 0,1°")
        self.assertEqual(self.sleeps.count(fetch.CLIMATE_PAUSE_S), 1, "паміж запытамі — паўза (ліміт бясплатнага доступу)")
        self.assertIn("Звычайнае надвор'е: квадратаў 2.", out)
        self.assertEqual(self.exit_code, 0, out)

        index, out = self.build()
        self.assertNotIn("звычайнага надвор'я", out)
        a, b = index["a"]["climate"], index["b"]["climate"]
        self.assertEqual((a["alt"], a["years"], len(a["months"])), (1400, [2020, 2024], 12))
        self.assertEqual(a["months"][6], {"max": round(17 - 500 * 0.0065), "min": round(-3 - 500 * 0.0065), "precip": 40, "wet": 10},
                         "ліпень на 1400 м: даныя на 900 м пералічаныя на вышыню")
        self.assertEqual(b["alt"], 1400)
        self.assertEqual(index["c"]["climate"]["months"][0]["max"], round(11 - 500 * 0.0065))
        self.fetch()
        self.assertNotIn("climate", [kind for kind, _ in self.calls], "у кэшы ўжо ёсць")

        # кэша для квадрата няма: на сайце звычайнага надвор'я няма, зборка і status раяць `hike fetch`
        (build.CACHE / "climate" / "41.3_42.0.json").unlink()
        index, out = self.build()
        self.assertIsNone(index["c"]["climate"])
        self.assertIn("звычайнага надвор'я па месяцах няма для паходаў: 1 — запусціце `hike fetch`", out)
        need = fetch.needs("c", {}, practical.raw_days([3], 4), None, None, {}, {})
        self.assertEqual([(cell, have) for cell, _, _, have in need["climate"]], [("41.3_42.0", False)])
        # ліміт сэрвісу (HTTP 429): астатнія квадраты не пытаюцца, каманда завяршаецца з кодам 1
        (build.CACHE / "climate" / "41.0_42.0.json").unlink()
        _, out = self.fetch(climate=FakeResponse({"error": True, "reason": "limit"}, status=429))
        self.assertEqual(([kind for kind, _ in self.calls].count("climate"), self.exit_code), (1, 1), out)
        self.assertIn("ліміт запытаў Open-Meteo", out)
        self.assertIn("Не атрымана: 2.", out)
        # іншая памылка аднаго квадрата не спыняе астатнія
        _, out = self.fetch(climate=lambda params: daily_answer(params) if params["latitude"] == 41.3 else FakeResponse({}, status=500))
        self.assertEqual(([kind for kind, _ in self.calls].count("climate"), self.exit_code), (2, 1), out)
        self.assertTrue((build.CACHE / "climate" / "41.3_42.0.json").exists())

        # тура няма ў кэшы, застаецца ранейшы трэк — і ранейшае звычайнае надвор'е
        self.fetch()
        before = self.build()[0]["a"]["climate"]
        (build.CACHE / "komoot" / "tours" / "1.json").unlink()
        self.assertEqual(self.build()[0]["a"]["climate"], before)


class TestReview1120(StatusProject):
    """Рэгрэсіі па review_2026-10-14_v1.12.0.md."""

    CELL = build.CACHE  # падмяняецца ў setUp: шлях да кэша квадрата паходу

    def setUp(self):
        super().setUp()
        self.hike()
        self.places('Keda: "Кеда"\n')
        self.CELL = build.CACHE / "climate" / "41.0_42.0.json"

    def january(self):
        return self.build()[0]["h"]["climate"]

    def asked(self):
        return [kind for kind, _ in self.calls].count("climate")

    def test_incomplete_climate_does_not_count_as_fetched(self):  # №1
        need = [".\\hike fetch  — няма звычайнага надвор'я па месяцах або яно няпоўнае: h"]
        holey = lambda params: daily_answer(params, hole="precipitation_sum")  # noqa: E731
        self.fetch(names=self.KEDA)
        full = self.january()
        self.assertEqual(full["months"][0], {"max": 8, "min": -12, "precip": 40, "wet": 10})
        self.assertEqual(self.todo(), ALL_DONE)

        # --refresh, адказ без ападкаў: поўныя звесткі застаюцца, код 1, status раіць fetch, наступны fetch пытае зноў
        saved = common.read_json(self.CELL)
        _, out = self.fetch(["--refresh"], names=self.KEDA, climate=holey)
        self.assertEqual(self.exit_code, 1, out)
        self.assertIn("звычайнае надвор'е прыйшло няпоўным — застаюцца ранейшыя поўныя звесткі", out)
        self.assertEqual(common.read_json(self.CELL), {**saved, "retry": True})
        self.assertEqual(self.january(), full, "на сайце — ранейшыя поўныя звесткі")
        self.assertEqual(self.todo(), need)
        _, out = self.fetch(names=self.KEDA, climate=holey)
        self.assertEqual((self.asked(), self.exit_code), (1, 1), out)
        _, out = self.fetch(names=self.KEDA)
        self.assertEqual((self.asked(), self.exit_code, "retry" in common.read_json(self.CELL)), (1, 0, False), out)
        self.build()
        self.assertEqual(self.todo(), ALL_DONE)

        # пусты кэш, адказ без ападкаў: вядомае паказваецца, але спампаваным не лічыцца
        self.CELL.unlink()
        _, out = self.fetch(names=self.KEDA, climate=holey)
        self.assertEqual(self.exit_code, 1, out)
        self.assertIn("(няпоўныя звесткі — паспрабуйце пазней)", out)
        self.assertEqual(self.january()["months"][0], {"max": 8, "min": -12, "precip": None, "wet": None})
        self.assertEqual(self.todo(), need)
        # усе паказчыкі пустыя: паказваць няма чаго, а fetch пытае зноў
        empty = lambda params: {**daily_answer(params), "daily": {"time": daily_answer(params)["daily"]["time"],  # noqa: E731
                                                                  **{k: [None] * 1827 for k in practical.CLIMATE_DAILY}}}
        _, out = self.fetch(names=self.KEDA, climate=empty)
        self.assertEqual((self.asked(), self.exit_code), (1, 1), out)
        self.assertIsNone(self.january())
        self.assertEqual(self.todo(), need)
        _, out = self.fetch(names=self.KEDA, climate=empty)
        self.assertEqual(self.asked(), 1, "і зноў пытае")
        # адказ не за тыя дні (зрушаны на дзень) — гэта памылка, кэш не мяняецца
        shifted = lambda params: daily_answer({**params, "start_date": "2020-01-02"})  # noqa: E731
        before = self.CELL.read_bytes()
        _, out = self.fetch(names=self.KEDA, climate=shifted)
        self.assertEqual(self.exit_code, 1, out)
        self.assertIn("нечаканы адказ сэрвісу надвор'я", out)
        self.assertEqual(self.CELL.read_bytes(), before)
        # поўны адказ: усё на месцы
        _, out = self.fetch(names=self.KEDA)
        self.assertEqual(self.exit_code, 0, out)
        self.assertEqual(self.january(), full)
        self.assertEqual(self.todo(), ALL_DONE)

    def test_climate_model_is_chosen_and_stored(self):  # №2
        self.fetch(names=self.KEDA)
        (params,) = [c for kind, c in self.calls if kind == "climate"]
        self.assertEqual(params["models"], "era5_seamless")
        cached = common.read_json(self.CELL)
        self.assertEqual((cached["model"], cached["source"]),
                         ("era5_seamless", "Open-Meteo Historical Weather API, ERA5-Seamless (ERA5-Land, ERA5)"))
        self.build()
        self.fetch(names=self.KEDA)
        self.assertEqual(self.asked(), 0, "актуальны кэш выкарыстоўваецца")
        # кэш ранейшай версіі (без мадэлі — Best Match сэрвісу) не падыходзіць: квадрат пытаецца нанова
        common.write_json(self.CELL, {k: v for k, v in cached.items() if k != "model"}, compact=True)
        self.assertIsNone(self.january(), "звесткі іншай мадэлі на сайт не трапляюць")
        self.assertEqual(self.todo(), [".\\hike fetch  — няма звычайнага надвор'я па месяцах або яно няпоўнае: h"])
        self.fetch(names=self.KEDA)
        self.assertEqual(self.asked(), 1)
        self.assertEqual(common.read_json(self.CELL)["model"], "era5_seamless")


class TestReview1121(PracticalProject):
    """Рэгрэсіі па review_2026-10-14_v1.12.1.md: код выхаду імпарту YouTube."""

    def youtube(self, listing, videos):
        """`hike youtube` з падмененым yt-dlp: listing — id відэа на канале (або выключэнне), videos — id → назва і дата
        або выключэнне. Вяртае (код выхаду, stdout, stderr, кэш)."""
        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=False):
                if url.endswith("/videos"):
                    if isinstance(listing, Exception):
                        raise listing
                    return {"entries": [{"id": vid, "title": f"t-{vid}"} for vid in listing]}
                answer = videos[url.rsplit("=", 1)[1]]
                if isinstance(answer, Exception):
                    raise answer
                return answer

        out, err = io.StringIO(), io.StringIO()
        code = 0
        with unittest.mock.patch.multiple(youtube, YoutubeDL=FakeYoutubeDL, YT_PATH=build.CACHE / "youtube.json",
                                          load_config=lambda: {"youtube_channel": "https://www.youtube.com/@test"}), \
                unittest.mock.patch("sys.stdout", out), unittest.mock.patch("sys.stderr", err):
            try:
                youtube.main([])
            except SystemExit as stop:
                code = stop.code if isinstance(stop.code, int) else 1
                err.write(str(stop.code) if not isinstance(stop.code, int) else "")
        return code, out.getvalue(), err.getvalue(), common.read_json(build.CACHE / "youtube.json")

    def test_youtube_import_reports_missing_videos(self):
        old = {"id": "old", "title": "Стары", "upload_date": "20250101", "duration": 60}
        common.write_json(build.CACHE / "youtube.json", [old])
        new = lambda vid, day: {"title": f"Відэа {vid}", "upload_date": day, "duration": 100}  # noqa: E731

        # новае відэа не атрымана: старое застаецца, новага ў кэшы няма, код 1
        code, out, err, cache = self.youtube(["new_video_1", "old"], {"new_video_1": RuntimeError("temporary HTTP 503")})
        self.assertEqual(code, 1, out)
        self.assertIn("! new_video_1: temporary HTTP 503", err)
        self.assertIn("Не атрымана: 1", out)
        self.assertEqual(cache, [{**old, "title": "t-old"}])

        # з двух новых атрымана адно: яно захоўваецца, код усё роўна 1
        code, out, err, cache = self.youtube(["a", "b", "old"], {"a": new("a", "20250301"), "b": RuntimeError("Private video")})
        self.assertEqual((code, [v["id"] for v in cache]), (1, ["a", "old"]), out)
        # паўтор атрымаў прапушчанае: код 0
        code, out, err, cache = self.youtube(["a", "b", "old"], {"b": new("b", "20250401")})
        self.assertEqual((code, [v["id"] for v in cache], err), (0, ["b", "a", "old"], ""), out)
        self.assertNotIn("Не атрымана", out)
        # новага няма: нічога не пытаецца, код 0
        code, out, err, cache = self.youtube(["a", "b", "old"], {})
        self.assertEqual((code, len(cache)), (0, 3), out)

        # спіс канала не атрыманы: паведамленне, код 1, кэш не мяняецца
        before = (build.CACHE / "youtube.json").read_bytes()
        code, out, err, cache = self.youtube(RuntimeError("HTTP 500"), {})
        self.assertEqual(code, 1)
        self.assertIn("Спіс відэа канала не атрыманы — HTTP 500", err)
        self.assertEqual((build.CACHE / "youtube.json").read_bytes(), before)


def fetch_failure():
    """Адказ сэрвісу з памылкай HTTP 503."""
    return FakeResponse({}, status=503)


if __name__ == "__main__":
    unittest.main()

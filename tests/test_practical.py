"""Тэсты практычнай часткі: разлікі па трэку і блок `practical:` (без сеткі, усё ў часовых тэчках).

Запуск з кораня праекта:  python -B -m unittest discover -s tests -q
"""
import io
import json
import os
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
import practical  # noqa: E402
import scaffold  # noqa: E402

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
                       scaffold.CONTENT, scaffold.CACHE, scaffold.load_config)
        build.CACHE = practical.CACHE = scaffold.CACHE = self.root / "cache"
        build.DOCS = self.root / "docs"
        build.CONTENT = scaffold.CONTENT = self.content
        build.load_config = scaffold.load_config = lambda: self.cfg

    def tearDown(self):
        (build.CACHE, build.DOCS, build.CONTENT, build.load_config, practical.CACHE,
         scaffold.CONTENT, scaffold.CACHE, scaffold.load_config) = self._saved
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
                                                      manifest={}, photos=photos, prev=prev, prev_photos=[a, b])
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


class TestWeatherAndRates(PracticalProject):
    """Этап 2: надвор'е з Open-Meteo і курс Нацбанка Грузіі — без сеткі, з падмененымі адказамі."""

    def setUp(self):
        super().setUp()
        patcher = unittest.mock.patch.multiple(fetch, CACHE=build.CACHE, CONTENT=self.content, load_config=lambda: self.cfg)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.photos_root.mkdir()

    def fetch(self, argv=(), weather=hourly_answer, rate=2.5):
        calls = []

        def fake_get(session, url, params=None, timeout=None):
            calls.append((url, dict(params or {})))
            if url == fetch.API_RATES:
                return rate if isinstance(rate, FakeResponse) else FakeResponse(
                    [{"currencies": [{"code": "USD", "quantity": 1, "rate": rate}]}])
            answer = weather(params)
            return answer if isinstance(answer, FakeResponse) else FakeResponse(answer)

        out = io.StringIO()
        with unittest.mock.patch("requests.Session.get", fake_get), unittest.mock.patch("time.sleep", lambda *a: None), \
                unittest.mock.patch("sys.stdout", out):
            fetch.main(list(argv))
        return calls, out.getvalue()

    def hike(self, extra=""):
        self.add_tour(1, "2025-06-01T04:00:00Z", path(*SQUARE))  # 08:00–12:00 мясцовага, 04:00–08:00 UTC
        (self.content / "h.yaml").write_text("date: 2025-06-01\ntitle: {be: H}\nkomoot: [1]\n" + extra, encoding="utf-8")

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


if __name__ == "__main__":
    unittest.main()

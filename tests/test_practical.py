"""Тэсты практычнай часткі: разлікі па трэку і блок `practical:` (без сеткі, усё ў часовых тэчках).

Запуск з кораня праекта:  python -B -m unittest discover -s tests -q
"""
import io
import sys
import tempfile
import unittest
import unittest.mock
from datetime import date, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import yaml  # noqa: E402
from PIL import Image  # noqa: E402

import build  # noqa: E402
import common  # noqa: E402
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
        (self.content / "t.yaml").write_text(head + "    drunk: [2]\n    taken: 6\n    refilled: 0\n    left: 1\n", encoding="utf-8")
        index, out = self.build()
        self.assertIn("water.drunk: у спісе 1 значэнняў, а дзён у паходзе 2", out)
        self.assertIn("узята + папоўнена − засталося = 5 л, а выпіта 2 л", out)

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
                                                         manifest={}, photos=[], prev=None)
        problems = [line for line in out.getvalue().splitlines() if "месца не вызначана" not in line]
        self.assertEqual(problems, [], "шаблон з README і чарнавіка разбіраецца без заўваг")
        self.assertIsNone(override)
        self.assertEqual(set(parsed["notes"]), set(practical.NOTE_KEYS))
        self.assertEqual((parsed["water"]["drunk"], parsed["gettingThere"][0]["minutes"], parsed["places"][0]["type"]),
                         (2.5, 70, "shelter"))


if __name__ == "__main__":
    unittest.main()

"""Юніт-тэсты асноўнай логікі: без сеткі і без рэальных фота/кэша (усё ў часовых тэчках).

Запуск з кораня праекта:  python -B -m unittest discover -s tests -q
"""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import yaml  # noqa: E402
from PIL import Image  # noqa: E402

import build  # noqa: E402
import common  # noqa: E402
import geo  # noqa: E402
import scaffold  # noqa: E402

CFG = {"site_photos_subfolders": ["Сайт", "для сайту"],
       "photo": {"max_size": 400, "quality": 70, "thumb_size": 100, "thumb_quality": 60}}


class TempProject(unittest.TestCase):
    """Падмяняе CACHE і DOCS у build на часовыя тэчкі."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._saved = (build.CACHE, build.DOCS)
        build.CACHE, build.DOCS = self.root / "cache", self.root / "docs"

    def tearDown(self):
        build.CACHE, build.DOCS = self._saved
        self.tmp.cleanup()

    def add_tour(self, tid, iso_date, coords, status="private", distance=None):
        tour = {"id": tid, "name": f"t{tid}", "date": iso_date, "status": status,
                "distance": distance, "elevation_up": 100, "elevation_down": 90,
                "duration": 3600, "time_in_motion": 3000, "coords": coords}
        common.write_json(build.CACHE / "komoot" / "tours" / f"{tid}.json", tour)
        return tour


class TestFolders(unittest.TestCase):
    def test_parse_folder_variants(self):
        self.assertEqual(common.parse_folder("20230902-04_Казбегі"),
                         (date(2023, 9, 2), date(2023, 9, 4), "Казбегі"))
        self.assertEqual(common.parse_folder("20231216_ Архангел")[2], "Архангел")
        self.assertEqual(common.parse_folder("20220917"), (date(2022, 9, 17), date(2022, 9, 17), ""))
        self.assertEqual(common.parse_folder("20231130-1202_Паход")[1], date(2023, 12, 2))
        self.assertIsNone(common.parse_folder("202501-03_Лыжы"))
        self.assertIsNone(common.parse_folder("Instagram"))

    def test_slugify(self):
        self.assertEqual(common.slugify("Зялёнае возера"), "zyalyonae-vozera")
        self.assertEqual(common.slugify("Казбегі"), "kazbegi")
        self.assertEqual(common.slugify("Кан'ёны"), "kanyony")
        self.assertEqual(common.hike_slug(date(2025, 5, 17), "Мцірала"), "2025-05-17-mtsirala")

    def test_tour_local_datetime_converts_utc(self):
        dt = common.tour_local_datetime("2024-09-14T22:30:00.000Z", 4)
        self.assertEqual(dt.date(), date(2024, 9, 15))  # пасля поўначы па мясцовым часе

    def test_site_photos_dir_priority_and_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.assertIsNone(common.site_photos_dir(folder, CFG))
            (folder / "Для сайту").mkdir()
            self.assertEqual(common.site_photos_dir(folder, CFG).name, "Для сайту")
            (folder / "САЙТ").mkdir()
            self.assertEqual(common.site_photos_dir(folder, CFG).name, "САЙТ")


class TestGeo(unittest.TestCase):
    def test_simplify_straight_line_and_peak(self):
        line = [(41.0 + i * 0.001, 42.0, 100.0) for i in range(50)]
        self.assertEqual(geo.simplify(line, 3), [0, 49])
        peak = [(41.0 + i * 0.001, 42.0, 100.0 + (300 if i == 25 else 0)) for i in range(50)]
        self.assertIn(25, geo.simplify(peak, 3))          # вышыня ўлічваецца
        self.assertNotIn(25, geo.simplify(peak, 3, False))

    def test_haversine(self):
        self.assertAlmostEqual(geo.haversine(41.0, 42.0, 42.0, 42.0), 111195, delta=50)


class TestScaffoldMatching(unittest.TestCase):
    def test_word_key_matches_transliterations(self):
        self.assertEqual(scaffold.word_key("Мцірала"), scaffold.word_key("Mtirala"))
        self.assertEqual(scaffold.word_key("Казбегі"), scaffold.word_key("Kazbegi"))
        self.assertTrue(scaffold.keywords("Снежная Мцірала ўзімку") & scaffold.keywords("Mtirala"))

    def test_dates_in_title(self):
        self.assertEqual(scaffold.dates_in_title("Габранэці, 9 траўня 2025"), [date(2025, 5, 9)])
        self.assertEqual(scaffold.dates_in_title("20231231 Нядзельны забег"), [date(2023, 12, 31)])
        self.assertEqual(scaffold.dates_in_title("31 лютага 2025"), [])

    def test_pick_folder_prefers_site_then_title_and_allows_one_day(self):
        f = lambda name, s, e, title, site: {"name": name, "start": s, "end": e, "title": title, "has_site": site}
        d = date(2024, 7, 21)
        folders = [f("20240721", d, d, "", True), f("20240721_Толя", d, d, "Толя", False)]
        self.assertEqual(scaffold.pick_folder(d, folders)["name"], "20240721")
        tb = [f("20240915-16_Тбікелі", date(2024, 9, 15), date(2024, 9, 16), "Тбікелі", True)]
        self.assertEqual(scaffold.pick_folder(date(2024, 9, 14), tb)["name"], "20240915-16_Тбікелі")
        self.assertIsNone(scaffold.pick_folder(date(2024, 9, 12), tb))

    def test_render_yaml_is_valid_with_quotes(self):
        h = {"start": date(2025, 1, 2), "title": 'Гара "Кабан"', "region": {"be": "Аджарыя", "en": "Adjara"},
             "folder": "20250102_Кабан", "tours": [], "videos": [], "location": [41.5, 42.1]}
        data = yaml.safe_load(scaffold.render_yaml(h, "2025-01-03"))
        self.assertEqual(data["title"]["be"], 'Гара "Кабан"')
        self.assertEqual(data["location"], [41.5, 42.1])
        self.assertEqual(data["impressions"]["be"].strip(), "")


class TestBuildTracks(TempProject):
    def test_same_day_tours_merge_into_one_day(self):
        a = [[41.0 + i * 0.001, 42.0, 1000.0 + i, i * 1000] for i in range(20)]
        b = [[41.1 + i * 0.001, 42.0, 1100.0 + i, i * 1000] for i in range(20)]
        c = [[41.2 + i * 0.001, 42.0, 900.0, i * 1000] for i in range(20)]
        self.add_tour(1, "2024-08-08T06:00:00.000Z", a, distance=2000)
        self.add_tour(2, "2024-08-08T11:00:00.000Z", b, status="public", distance=2000)
        self.add_tour(3, "2024-08-10T06:00:00.000Z", c, distance=2000)
        days, lines, track = build.build_tracks("x", [3, 2, 1], 4, {})
        self.assertEqual([d["date"] for d in days], ["2024-08-08", "2024-08-10"])
        self.assertEqual(days[0]["distance"], 4.0)
        self.assertEqual(days[0]["komoot"], ["https://www.komoot.com/tour/2"])
        self.assertEqual(days[0]["maxAlt"], 1119)
        self.assertEqual(len(lines), 3)
        detail = common.read_json(build.DOCS / track)
        segs = detail["days"][0]["segs"]
        self.assertEqual(len(segs), 2)
        self.assertGreater(segs[1][0][3], segs[0][-1][3] - 1e-6)  # км працягваецца ў другім адрэзку

    def test_missing_tour_is_skipped(self):
        days, lines, track = build.build_tracks("x", [404], 4, {})
        self.assertEqual((days, lines, track), ([], [], None))


class TestBuildPhotos(TempProject):
    def make_photo(self, folder, name, size=(800, 600)):
        folder.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, (120, 160, 200)).save(folder / name, "JPEG")

    def test_photos_converted_cover_by_stem_and_cleanup(self):
        src = self.root / "photos" / "Сайт"
        self.make_photo(src, "IMG_1.JPG")
        self.make_photo(src, "IMG_2.jpg", (600, 800))
        photos, cover, gps = build.build_photos("hike", src, "img_2", CFG, False)
        self.assertEqual(len(photos), 2)
        self.assertEqual(photos[cover]["src"], "photos/hike/img-2.webp")
        self.assertTrue(all(max(p["w"], p["h"]) <= 400 for p in photos))
        self.assertIsNone(gps)
        (src / "IMG_1.JPG").unlink()
        photos, _, _ = build.build_photos("hike", src, None, CFG, False)
        self.assertEqual(len(photos), 1)
        self.assertEqual(sorted(f.name for f in (build.DOCS / "photos" / "hike").iterdir()),
                         ["img-2-t.webp", "img-2.webp"])

    def test_no_site_folder_removes_old_output(self):
        out = build.DOCS / "photos" / "hike"
        out.mkdir(parents=True)
        (out / "old.webp").write_bytes(b"x")
        self.assertEqual(build.build_photos("hike", None, None, CFG, False), ([], 0, None))
        self.assertFalse(out.exists())


class TestReviewFixes(TempProject):
    """Рэгрэсійныя тэсты па review_2026-10-07_v1.0.0.md."""

    def setUp(self):
        super().setUp()
        self.photos_root = self.root / "photos"
        self.content = self.root / "content" / "hikes"
        self.content.mkdir(parents=True)
        self.cfg = {**CFG, "photos_root": str(self.photos_root), "default_utc_offset": 4,
                    "komoot_sports": ["hike"]}
        self._saved_mod = (scaffold.CONTENT, scaffold.CACHE, scaffold.load_config, scaffold.region_for_point,
                           scaffold.place_name, build.CONTENT, build.load_config)
        scaffold.CONTENT = build.CONTENT = self.content
        scaffold.CACHE = build.CACHE
        scaffold.load_config = build.load_config = lambda: self.cfg
        scaffold.region_for_point = lambda lat, lng: {"be": "Аджарыя", "en": "Adjara"}
        scaffold.place_name = lambda lat, lng: "Вёска"

    def tearDown(self):
        (scaffold.CONTENT, scaffold.CACHE, scaffold.load_config, scaffold.region_for_point,
         scaffold.place_name, build.CONTENT, build.load_config) = self._saved_mod
        super().tearDown()

    def kaban_tour(self):
        (self.photos_root / "20250101_Kaban" / "Сайт").mkdir(parents=True)
        common.write_json(build.CACHE / "komoot" / "tours.json", [{
            "id": 7, "name": "Hike", "sport": "hike", "date": "2025-01-01T06:00:00.000Z",
            "start_point": {"lat": 41.7, "lng": 42.1}}])
        common.write_json(build.CACHE / "youtube.json", [])

    def test_1_drafts_never_overwrite_existing_files(self):
        self.kaban_tour()
        taken = ("2025-01-01-kaban.yaml", "2025-01-01-kaban-2.yaml", "2025-01-01-kaban-7.yaml")
        for name in taken:  # асноўнае імя і ўсе запасныя варыянты (з нумарам і з id тура)
            (self.content / name).write_text("impressions: USER_TEXT\n", encoding="utf-8")
        scaffold.main([])
        for name in taken:
            self.assertEqual((self.content / name).read_text(encoding="utf-8"), "impressions: USER_TEXT\n")
        new = {p.name for p in self.content.glob("*.yaml")} - set(taken)
        self.assertEqual(len(new), 1)
        draft = yaml.safe_load((self.content / new.pop()).read_text(encoding="utf-8"))
        self.assertEqual(draft["komoot"], [7])

    def test_6_ignored_folder_with_tour_creates_no_draft(self):
        self.kaban_tour()
        (self.content.parent / "ignore.yaml").write_text("folders: [20250101_Kaban]\n", encoding="utf-8")
        scaffold.main([])
        self.assertEqual(list(self.content.glob("*.yaml")), [])

    def write_hike(self, slug="h", folder="20250101_H", cover=None):
        text = f"date: 2025-01-01\ntitle: {{be: H}}\nphotos_folder: {folder}\n"
        if cover:
            text += f"cover: {cover}\n"
        (self.content / f"{slug}.yaml").write_text(text, encoding="utf-8")

    def publish_photos(self, names, with_manifest=True):
        out = build.DOCS / "photos" / "h"
        out.mkdir(parents=True)
        manifest = {}
        for n in names:
            Image.new("RGB", (40, 30)).save(out / f"{n}.webp", "WEBP")
            Image.new("RGB", (8, 6)).save(out / f"{n}-t.webp", "WEBP")
            manifest[f"{n.upper()}.JPG"] = {"name": n, "w": 40, "h": 30, "taken": f"2025-01-01T0{len(manifest)}",
                                            "mtime": 0, "size": 0, "gps": None}
        if with_manifest:
            common.write_json(build.CACHE / "photos" / "h.json", manifest)
        return out

    def test_2_unavailable_photos_root_keeps_published_photos(self):
        self.write_hike(cover="IMG_2")
        out = self.publish_photos(["img-1", "img-2"])
        build.main([])
        self.assertEqual(sorted(f.name for f in out.iterdir()),
                         ["img-1-t.webp", "img-1.webp", "img-2-t.webp", "img-2.webp"])
        hike = common.read_json(build.DOCS / "data" / "hikes.json")["hikes"][0]
        self.assertEqual([p["src"] for p in hike["photos"]], ["photos/h/img-1.webp", "photos/h/img-2.webp"])
        self.assertEqual(hike["cover"], 1)

    def test_2_missing_folder_without_cache_restores_from_files(self):
        self.photos_root.mkdir()
        self.write_hike()
        # Так build_photos называе A.jpg і A-t.jpg: «a-t» (+«a-t-t») і «a-1» (+«a-1-t»).
        self.publish_photos(["a-t", "a-1"], with_manifest=False)
        build.main([])
        hike = common.read_json(build.DOCS / "data" / "hikes.json")["hikes"][0]
        self.assertEqual(sorted(p["src"] for p in hike["photos"]), ["photos/h/a-1.webp", "photos/h/a-t.webp"])

    def test_3_thumbnail_name_does_not_clash_with_other_photo(self):
        src = self.root / "src"
        src.mkdir()
        Image.new("RGB", (800, 600), (255, 0, 0)).save(src / "A.jpg", "JPEG")
        Image.new("RGB", (800, 600), (0, 0, 255)).save(src / "A-t.jpg", "JPEG")
        photos, _, _ = build.build_photos("h", src, None, CFG, False)
        out = build.DOCS / "photos" / "h"
        self.assertEqual(len(list(out.iterdir())), 4)
        for p in photos:
            with Image.open(build.DOCS / p["src"]) as im:
                self.assertEqual((im.width, im.height), (p["w"], p["h"]))
                big_color = max(range(3), key=im.convert("RGB").getpixel((5, 5)).__getitem__)
            with Image.open(build.DOCS / p["thumb"]) as im:
                self.assertEqual(max(im.size), 100)
                self.assertEqual(max(range(3), key=im.convert("RGB").getpixel((5, 5)).__getitem__), big_color)

    def test_7_mixed_timezones_give_chronological_days(self):
        pts = [[41.0 + i * 0.001, 42.0, 1000.0, i] for i in range(5)]
        self.add_tour(1, "2024-12-31T22:00:00Z", pts)
        self.add_tour(2, "2024-12-31T23:00:00+04:00", pts)
        self.add_tour(3, "2025-01-01T03:00:00+04:00", pts)
        days, _, track = build.build_tracks("h", [1, 2, 3], 4, {})
        self.assertEqual([d["date"] for d in days], ["2024-12-31", "2025-01-01"])
        self.assertEqual(len(common.read_json(build.DOCS / track)["days"][1]["segs"]), 2)

    # --- review_2026-10-07_v1.0.1.md ---

    def index(self):
        return {h["slug"]: h for h in common.read_json(build.DOCS / "data" / "hikes.json")["hikes"]}

    def referenced_files_exist(self):
        for h in self.index().values():
            for p in h["photos"]:
                for k in ("src", "thumb"):
                    self.assertTrue((build.DOCS / p[k]).exists(), p[k])
            if h["track"]:
                self.assertTrue((build.DOCS / h["track"]).exists(), h["track"])

    def test_r2_1_missing_tour_cache_keeps_published_track(self):
        self.photos_root.mkdir()
        pts = [[41.0 + i * 0.001, 42.0, 1000.0 + i, i] for i in range(10)]
        self.add_tour(1, "2025-01-01T06:00:00Z", pts, distance=3000)
        self.add_tour(2, "2025-01-02T06:00:00Z", pts, distance=2000)
        (self.content / "h.yaml").write_text("date: 2025-01-01\ntitle: {be: H}\nkomoot: [1, 2]\n", encoding="utf-8")
        build.main([])
        before = self.index()["h"]
        self.assertEqual(len(before["days"]), 2)
        for tid in (2, 1):  # спачатку няма аднаго тура з двух, потым — абодвух
            (build.CACHE / "komoot" / "tours" / f"{tid}.json").unlink()
            build.main([])
            after = self.index()["h"]
            for k in ("days", "lines", "track", "point", "bbox", "stats"):
                self.assertEqual(after[k], before[k], k)
            self.referenced_files_exist()

    def make_hike_with_photo(self, slug, photo, color):
        folder = self.photos_root / f"2025010{len(slug)}_{slug}" / "Сайт"
        folder.mkdir(parents=True)
        Image.new("RGB", (80, 60), color).save(folder / photo, "JPEG")
        (self.content / f"{slug}.yaml").write_text(
            f"date: 2025-01-01\ntitle: {{be: {slug}}}\nphotos_folder: {folder.parent.name}\n", encoding="utf-8")
        return folder

    def test_r2_2_failure_in_later_hike_keeps_published_site_consistent(self):
        a = self.make_hike_with_photo("a", "A.jpg", (255, 0, 0))
        self.make_hike_with_photo("bb", "B.jpg", (0, 0, 255))
        build.main([])
        index_before = (build.DOCS / "data" / "hikes.json").read_bytes()
        (a / "A.jpg").unlink()
        Image.new("RGB", (80, 60), (0, 255, 0)).save(a / "C.jpg", "JPEG")
        original = build.build_tracks

        def failing(slug, *args):
            if slug == "bb":
                raise RuntimeError("сімуляваная памылка")
            return original(slug, *args)

        build.build_tracks = failing
        try:
            with self.assertRaises(RuntimeError):
                build.main([])
        finally:
            build.build_tracks = original
        self.assertEqual((build.DOCS / "data" / "hikes.json").read_bytes(), index_before)
        self.referenced_files_exist()  # стары індэкс па-ранейшаму спасылаецца на існуючыя файлы
        build.main([])
        self.assertEqual([p["src"] for p in self.index()["a"]["photos"]], ["photos/a/c.webp"])
        self.assertFalse((build.DOCS / "photos" / "a" / "a.webp").exists())  # прыбрана пасля новага індэкса

    def test_r2_2_corrupt_photo_does_not_stop_build(self):
        self.make_hike_with_photo("a", "A.jpg", (255, 0, 0))
        b = self.make_hike_with_photo("bb", "B.jpg", (0, 0, 255))
        build.main([])
        (b / "B.jpg").write_bytes(b"not a jpeg")             # пашкоджана пасля публікацыі
        (b / "D.jpg").write_bytes(b"also broken")            # новае і адразу пашкоджанае
        build.main([])
        self.assertEqual([p["src"] for p in self.index()["bb"]["photos"]], ["photos/bb/b.webp"])
        self.referenced_files_exist()

    def test_r2_3_restore_without_cache_uses_previous_index(self):
        self.write_hike()  # photos_root недаступны, трэку і location няма
        out = self.publish_photos(["a", "b"], with_manifest=False)
        photos = [{"src": f"photos/h/{n}.webp", "thumb": f"photos/h/{n}-t.webp", "w": 40, "h": 30} for n in ("b", "a")]
        common.write_json(build.DOCS / "data" / "hikes.json", {"hikes": [
            {"slug": "h", "photos": photos, "cover": 1, "point": [42.01, 41.01], "lines": [], "track": None}]})
        build.main([])
        hike = self.index()["h"]
        self.assertEqual([p["src"] for p in hike["photos"]], ["photos/h/b.webp", "photos/h/a.webp"])
        self.assertEqual(hike["cover"], 1)
        self.assertEqual(hike["point"], [42.01, 41.01])
        self.assertTrue(out.is_dir())

    # --- review_2026-10-07_v1.0.2.md ---

    def test_r3_1_corrupt_source_keeps_photo_with_force_and_without_manifest(self):
        a = self.make_hike_with_photo("a", "A.jpg", (255, 0, 0))
        build.main([])
        out = build.DOCS / "photos" / "a"
        before = {f.name: f.read_bytes() for f in out.iterdir()}
        entry = self.index()["a"]["photos"]
        (a / "A.jpg").write_bytes(b"broken")
        (a / "N.jpg").write_bytes(b"new and broken")
        for argv, drop_manifest in ((["--force"], False), ([], True)):
            if drop_manifest:
                (build.CACHE / "photos" / "a.json").unlink()
            build.main(argv)
            self.assertEqual(self.index()["a"]["photos"], entry, argv)
            self.assertEqual({f.name: f.read_bytes() for f in out.iterdir()}, before, argv)

    def test_r3_2_failure_after_track_change_keeps_index_and_track_consistent(self):
        self.photos_root.mkdir()
        pts = [[41.0 + i * 0.001, 42.0, 1000.0 + i, i] for i in range(10)]
        self.add_tour(1, "2025-01-01T06:00:00Z", pts)
        self.add_tour(2, "2025-01-02T06:00:00Z", pts)
        (self.content / "a.yaml").write_text("date: 2025-01-01\ntitle: {be: A}\nkomoot: [1, 2]\n", encoding="utf-8")
        (self.content / "b.yaml").write_text("date: 2025-01-01\ntitle: {be: B}\n", encoding="utf-8")
        build.main([])
        (self.content / "a.yaml").write_text("date: 2025-01-01\ntitle: {be: A}\nkomoot: [1]\n", encoding="utf-8")
        (self.content / "b.yaml").write_text("date: 2025-01-01\ntitle: [bad, yaml, value]\n", encoding="utf-8")
        with self.assertRaises(AttributeError):
            build.main([])
        a = self.index()["a"]
        track = common.read_json(build.DOCS / a["track"])
        self.assertEqual(len(track["days"]), len(a["days"]))  # стары індэкс і яго трэк узгодненыя
        (self.content / "b.yaml").write_text("date: 2025-01-01\ntitle: {be: B}\n", encoding="utf-8")
        build.main([])
        a = self.index()["a"]
        self.assertEqual(len(a["days"]), 1)
        self.assertEqual(len(common.read_json(build.DOCS / a["track"])["days"]), 1)
        self.assertEqual(len(list((build.DOCS / "data" / "tracks").glob("*.json"))), 1)  # стары прыбраны

    def test_write_json_is_atomic(self):
        path = self.root / "x.json"
        common.write_json(path, {"a": 1})
        self.assertEqual(common.read_json(path), {"a": 1})
        self.assertFalse(path.with_name("x.json.tmp").exists())


class TestTextPair(unittest.TestCase):
    def test_text_pair(self):
        self.assertEqual(build.text_pair({"be": " а ", "en": None}), {"be": "а", "en": ""})
        self.assertEqual(build.text_pair("тэкст"), {"be": "тэкст", "en": ""})
        self.assertEqual(build.text_pair(None), {"be": "", "en": ""})


if __name__ == "__main__":
    unittest.main()

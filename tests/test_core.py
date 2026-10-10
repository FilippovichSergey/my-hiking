"""Юніт-тэсты асноўнай логікі: без сеткі і без рэальных фота/кэша (усё ў часовых тэчках).

Запуск з кораня праекта:  python -B -m unittest discover -s tests -q
"""
import io
import json
import re
import sys
import tempfile
import unittest
import unittest.mock
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import yaml  # noqa: E402
from PIL import Image  # noqa: E402

import build  # noqa: E402
import common  # noqa: E402
import geo  # noqa: E402
import komoot  # noqa: E402
import scaffold  # noqa: E402

CFG = {"site_photos_subfolders": ["Сайт", "для сайту"],
       "photo": {"max_size": 400, "quality": 70, "thumb_size": 100, "thumb_quality": 60}}


def stem(src):
    """'photos/h/img-2-1a2b3c4d.webp' → 'img-2': імя фота без хэша версіі."""
    return re.sub(r"-[0-9a-f]{8}(-\d+)?$", "", Path(src).stem)


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
        self.assertEqual(stem(photos[cover]["src"]), "img-2")
        self.assertTrue(all(max(p["w"], p["h"]) <= 400 for p in photos))
        self.assertIsNone(gps)
        (src / "IMG_1.JPG").unlink()
        photos, _, _ = build.build_photos("hike", src, None, CFG, False)
        self.assertEqual(len(photos), 1)
        files = sorted(f.name for f in (build.DOCS / "photos" / "hike").iterdir())
        self.assertEqual(files, [Path(photos[0]["thumb"]).name, Path(photos[0]["src"]).name])
        self.assertEqual(stem(photos[0]["src"]), "img-2")

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
        self.assertEqual([stem(p["src"]) for p in self.index()["a"]["photos"]], ["c"])
        self.assertEqual(len(list((build.DOCS / "photos" / "a").iterdir())), 2)  # «a» прыбрана пасля індэкса

    def test_r2_2_corrupt_photo_does_not_stop_build(self):
        self.make_hike_with_photo("a", "A.jpg", (255, 0, 0))
        b = self.make_hike_with_photo("bb", "B.jpg", (0, 0, 255))
        build.main([])
        (b / "B.jpg").write_bytes(b"not a jpeg")             # пашкоджана пасля публікацыі
        (b / "D.jpg").write_bytes(b"also broken")            # новае і адразу пашкоджанае
        build.main([])
        self.assertEqual([stem(p["src"]) for p in self.index()["bb"]["photos"]], ["b"])
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

    # --- review_2026-10-09_v1.0.3.md ---

    @staticmethod
    def exif_jpeg(path, color, size=(80, 60), taken=None, gps=None):
        """JPEG з сапраўднымі EXIF: час здымкі і GPS [шырата, даўгата]."""
        exif = Image.Exif()
        if taken:
            exif.get_ifd(0x8769)[36867] = taken
        if gps:
            dms = lambda v: (float(int(v)), round((v - int(v)) * 60, 6), 0.0)
            exif.get_ifd(0x8825).update({1: "N", 2: dms(gps[0]), 3: "E", 4: dms(gps[1])})
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, color).save(path, "JPEG", exif=exif)

    def published_files(self, slug):
        return {f.name: f.read_bytes() for f in (build.DOCS / "photos" / slug).iterdir()}

    def test_r4_1_same_slug_deleted_and_corrupt_keeps_its_own_photo(self):
        folder = self.make_hike_with_photo("a", "A t.jpg", (255, 0, 0))         # чырвонае, slug «a-t»
        Image.new("RGB", (90, 70), (0, 0, 255)).save(folder / "A-t.jpg", "JPEG")  # сіняе, той жа slug
        build.main([])
        blue = [p for p in self.index()["a"]["photos"] if p["w"] == 90]
        self.assertEqual(len(blue), 1)
        blue_files = {Path(blue[0][k]).name for k in ("src", "thumb")}
        blue_bytes = {n: b for n, b in self.published_files("a").items() if n in blue_files}
        (folder / "A t.jpg").unlink()
        (folder / "A-t.jpg").write_bytes(b"broken")
        for argv in ([], ["--force"]):
            build.main(argv)
            self.assertEqual(self.index()["a"]["photos"], blue, argv)
            self.assertEqual(self.published_files("a"), blue_bytes, argv)  # чырвонае прыбрана, сіняе цэлае
            with Image.open(build.DOCS / blue[0]["src"]) as im:
                self.assertEqual(im.size, (90, 70))
                self.assertEqual(max(range(3), key=im.convert("RGB").getpixel((5, 5)).__getitem__), 2)

    def test_r4_2_missing_content_folder_stops_and_keeps_site(self):
        self.make_hike_with_photo("a", "A.jpg", (255, 0, 0))
        build.main([])
        before = {p: p.read_bytes() for p in build.DOCS.rglob("*") if p.is_file()}
        self.content.rename(self.content.with_name("hikes-renamed"))
        with self.assertRaises(SystemExit) as ctx:
            build.main([])
        self.assertNotIn(ctx.exception.code, (0, None))
        self.assertEqual({p: p.read_bytes() for p in build.DOCS.rglob("*") if p.is_file()}, before)

    def test_r4_3_corrupt_photo_does_not_stop_drafts(self):
        common.write_json(build.CACHE / "komoot" / "tours.json", [])
        common.write_json(build.CACHE / "youtube.json", [])
        h = self.photos_root / "20250101_Hora" / "Сайт"
        self.exif_jpeg(h / "B.jpg", (0, 0, 255), gps=[42.01, 41.01])
        (h / "A.jpg").write_bytes(b"broken")                     # першае па парадку
        g = self.photos_root / "20250201_Hlyb" / "Сайт"
        g.mkdir(parents=True)
        (g / "A.jpg").write_bytes(b"broken")                     # усе фота пашкоджаныя
        scaffold.main([])
        drafts = {p.name: yaml.safe_load(p.read_text(encoding="utf-8")) for p in self.content.glob("*.yaml")}
        self.assertEqual(sorted(d["photos_folder"] for d in drafts.values()), ["20250101_Hora", "20250201_Hlyb"])
        by_folder = {d["photos_folder"]: d for d in drafts.values()}
        self.assertEqual(by_folder["20250101_Hora"]["location"], [42.01, 41.01])
        self.assertFalse(by_folder["20250201_Hlyb"].get("location"))

    def test_r4_failed_build_after_photo_change_keeps_photo_sizes_consistent(self):
        a = self.make_hike_with_photo("a", "A.jpg", (255, 0, 0))   # 80×60
        self.make_hike_with_photo("bb", "B.jpg", (0, 0, 255))
        build.main([])
        index_before = (build.DOCS / "data" / "hikes.json").read_bytes()
        Image.new("RGB", (60, 80), (255, 0, 0)).save(a / "A.jpg", "JPEG")
        (self.content / "bb.yaml").write_text("date: 2025-01-01\ntitle: [bad, yaml, value]\n", encoding="utf-8")
        with self.assertRaises(AttributeError):
            build.main([])
        self.assertEqual((build.DOCS / "data" / "hikes.json").read_bytes(), index_before)
        for h in self.index().values():  # стары індэкс адпавядае фактычным файлам
            for p in h["photos"]:
                with Image.open(build.DOCS / p["src"]) as im:
                    self.assertEqual(list(im.size), [p["w"], p["h"]])
        (self.content / "bb.yaml").write_text("date: 2025-01-01\ntitle: {be: bb}\n", encoding="utf-8")
        build.main([])
        p = self.index()["a"]["photos"][0]
        self.assertEqual((p["w"], p["h"]), (60, 80))
        self.assertEqual(len(list((build.DOCS / "photos" / "a").iterdir())), 2)  # старая версія прыбрана

    def test_r4_restore_without_manifest_keeps_order_cover_and_point(self):
        folder = self.photos_root / "20250101_H" / "Сайт"
        self.exif_jpeg(folder / "B.jpg", (0, 0, 255), taken="2025:01:01 09:00:00")
        self.exif_jpeg(folder / "A.jpg", (255, 0, 0), taken="2025:01:01 10:00:00", gps=[42.01, 41.01])
        self.write_hike()
        build.main([])
        before = self.index()["h"]
        self.assertEqual([stem(p["src"]) for p in before["photos"]], ["b", "a"])
        self.assertEqual((before["cover"], before["point"]), (0, [41.01, 42.01]))
        (build.CACHE / "photos" / "h.json").unlink()
        for f in ("A.jpg", "B.jpg"):
            (folder / f).write_bytes(b"broken")
        build.main([])
        after = self.index()["h"]
        for k in ("photos", "cover", "point"):
            self.assertEqual(after[k], before[k], k)
        self.referenced_files_exist()

    # --- review_2026-10-09_v1.0.4.md ---

    def test_r5_1_multiline_tour_and_video_names_give_valid_draft(self):
        self.photos_root.mkdir()
        common.write_json(build.CACHE / "komoot" / "tours.json", [
            {"id": 1, "name": "Hill\nSecond line", "sport": "hike", "date": "2025-01-02T06:00:00.000Z",
             "start_point": {"lat": 41.7, "lng": 42.1}},
            {"id": 2, "name": "Dol\r\nkey: value", "sport": "hike", "date": "2025-03-05T06:00:00.000Z",
             "start_point": {"lat": 41.7, "lng": 42.1}}])
        common.write_json(build.CACHE / "youtube.json", [
            {"id": "v1", "title": "Відэа 2 студзеня 2025\nkomoot: [99]", "upload_date": "20250110"}])
        scaffold.main([])
        drafts = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(self.content.glob("*.yaml"))]
        self.assertEqual(len(drafts), 2)
        by_tour = {d["komoot"][0]: d for d in drafts}
        self.assertEqual(by_tour[1]["title"]["be"], "Hill\nSecond line")
        self.assertEqual(by_tour[1]["youtube"], ["v1"])
        self.assertEqual(by_tour[2]["komoot"], [2])
        self.assertNotIn("key", by_tour[2])
        scaffold.main([])  # наступныя запускі чытаюць чарнавікі без памылак
        self.assertEqual(len(list(self.content.glob("*.yaml"))), 2)
        build.main([])

    def test_r5_2_exact_cover_name_wins_over_same_slug(self):
        folder = self.photos_root / "20250101_H" / "Сайт"
        self.exif_jpeg(folder / "A t.jpg", (255, 0, 0), taken="2025:01:01 09:00:00")
        self.exif_jpeg(folder / "A-t.jpg", (0, 0, 255), taken="2025:01:01 10:00:00")
        for cover, channel in (("A t.jpg", 0), ("A-t.jpg", 2), ("A t", 0)):
            self.write_hike(cover=f'"{cover}"')
            for argv in ([], ["--force"], []):
                build.main(argv)
                h = self.index()["h"]
                with Image.open(build.DOCS / h["photos"][h["cover"]]["src"]) as im:
                    px = im.convert("RGB").getpixel((5, 5))
                self.assertEqual(max(range(3), key=px.__getitem__), channel, (cover, argv))

    def test_r5_3_folder_across_new_year_is_one_hike(self):
        self.assertEqual(common.parse_folder("20251231-0102_NewYear")[:2], (date(2025, 12, 31), date(2026, 1, 2)))
        self.assertEqual(common.parse_folder("20230930-02_X")[1], date(2023, 10, 2))
        self.assertEqual(common.parse_folder("20231231-03_Y")[1], date(2024, 1, 3))
        (self.photos_root / "20251231-0102_NewYear" / "Сайт").mkdir(parents=True)
        common.write_json(build.CACHE / "komoot" / "tours.json", [
            {"id": i, "name": "Hike", "sport": "hike", "date": d, "start_point": {"lat": 41.7, "lng": 42.1}}
            for i, d in ((1, "2025-12-31T06:00:00.000Z"), (2, "2026-01-02T06:00:00.000Z"))])
        common.write_json(build.CACHE / "youtube.json", [])
        scaffold.main([])
        drafts = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in self.content.glob("*.yaml")]
        self.assertEqual(len(drafts), 1)
        self.assertEqual((drafts[0]["photos_folder"], drafts[0]["komoot"]), ("20251231-0102_NewYear", [1, 2]))

    def test_r5_prev1_same_slug_without_manifest_keeps_both_candidates(self):
        folder = self.make_hike_with_photo("a", "A t.jpg", (255, 0, 0))
        Image.new("RGB", (90, 70), (0, 0, 255)).save(folder / "A-t.jpg", "JPEG")
        build.main([])
        before = self.published_files("a")
        (folder / "A t.jpg").unlink()
        (folder / "A-t.jpg").write_bytes(b"broken")
        (build.CACHE / "photos" / "a.json").unlink()
        for argv in ([], ["--force"]):
            build.main(argv)
            self.assertEqual(self.index()["a"]["photos"], [], argv)  # не прывязваем адвольнае фота
            self.assertEqual(self.published_files("a"), before, argv)  # і не выдаляем магчыма патрэбнае
        Image.new("RGB", (90, 70), (0, 0, 255)).save(folder / "A-t.jpg", "JPEG")
        build.main([])
        photos = self.index()["a"]["photos"]
        self.assertEqual([(p["w"], p["h"]) for p in photos], [(90, 70)])
        self.assertEqual(len(self.published_files("a")), 2)

    # --- review_2026-10-09_v1.0.5.md ---

    def test_r6_1_invalid_exif_date_keeps_gps(self):
        folder = self.photos_root / "20250101_H" / "Сайт"
        self.exif_jpeg(folder / "A.jpg", (0, 0, 255), taken="0000:00:00 00:00:00", gps=[42.01, 41.01])
        with Image.open(folder / "A.jpg") as im:
            self.assertEqual(build._exif_info(im), (None, [41.01, 42.01]))
        common.write_json(build.CACHE / "komoot" / "tours.json", [])
        common.write_json(build.CACHE / "youtube.json", [])
        scaffold.main([])
        draft = yaml.safe_load(next(self.content.glob("*.yaml")).read_text(encoding="utf-8"))
        self.assertEqual(draft["location"], [42.01, 41.01])
        draft_path = next(self.content.glob("*.yaml"))
        draft_path.write_text(draft_path.read_text(encoding="utf-8").replace("location: [42.01, 41.01]", "location:"),
                              encoding="utf-8")  # пункт павінен узяцца з GPS фота і пры зборцы
        build.main([])
        self.assertEqual(next(iter(self.index().values()))["point"], [41.01, 42.01])

    def test_r6_2_deleted_cover_gives_valid_cover_index(self):
        folder = self.photos_root / "20250101_H" / "Сайт"
        for n, hour in (("A", 9), ("B", 10), ("C", 11)):
            self.exif_jpeg(folder / f"{n}.jpg", (255, 0, 0), taken=f"2025:01:01 {hour:02d}:00:00")
        self.write_hike(cover="C.jpg")
        build.main([])
        self.assertEqual(self.index()["h"]["cover"], 2)
        (build.CACHE / "photos" / "h.json").unlink()
        (folder / "C.jpg").unlink()
        for n in ("A", "B"):
            (folder / f"{n}.jpg").write_bytes(b"broken")
        for argv in ([], ["--force"]):
            build.main(argv)
            h = self.index()["h"]
            self.assertEqual([stem(p["src"]) for p in h["photos"]], ["a", "b"], argv)
            self.assertEqual(h["cover"], 0, argv)

    def test_r6_prev2_exact_cover_kept_when_photos_disk_unavailable(self):
        folder = self.photos_root / "20250101_H" / "Сайт"
        self.exif_jpeg(folder / "A t.jpg", (255, 0, 0), taken="2025:01:01 09:00:00")
        self.exif_jpeg(folder / "A-t.jpg", (0, 0, 255), taken="2025:01:01 10:00:00")
        self.write_hike(cover='"A-t.jpg"')
        build.main([])
        self.assertEqual(self.index()["h"]["cover"], 1)
        self.cfg["photos_root"] = str(self.root / "missing-disk")
        build.main([])
        self.assertEqual(self.index()["h"]["cover"], 1)
        (build.CACHE / "photos" / "h.json").unlink()  # без маніфеста застаецца ранейшая вокладка
        build.main([])
        self.assertEqual(self.index()["h"]["cover"], 1)

    def test_r6_prev3_new_year_range_ending_on_leap_day(self):
        self.assertEqual(common.parse_folder("20231231-0229_Leap")[:2], (date(2023, 12, 31), date(2024, 2, 29)))
        self.assertEqual(common.parse_folder("20231231-0228_X")[1], date(2024, 2, 28))
        self.assertEqual(common.parse_folder("20240228-0229_Y")[1], date(2024, 2, 29))
        self.assertIsNone(common.parse_folder("20241231-0229_Bad"))  # 2025 не высакосны

    # --- review_2026-10-11_v1.0.6.md ---

    def cover_channel(self, h):
        """Нумар пераважнага колеру вокладкі: 0 — чырвоны, 2 — сіні."""
        with Image.open(build.DOCS / h["photos"][h["cover"]]["src"]) as im:
            px = im.convert("RGB").getpixel((5, 5))
        return max(range(3), key=px.__getitem__)

    def test_r7_2_same_name_jpg_and_png_are_separate_photos(self):
        folder = self.photos_root / "20250101_H" / "Сайт"
        self.exif_jpeg(folder / "A.jpg", (255, 0, 0), taken="2025:01:01 09:00:00")
        Image.new("RGB", (80, 60), (0, 0, 255)).save(folder / "A.png", "PNG")
        self.exif_jpeg(folder / "IMG_1.jpg", (0, 255, 0), taken="2025:01:01 08:00:00")
        (folder / "IMG_1.CR3").write_bytes(b"raw")  # RAW побач з гатовай копіяй — тое ж фота, яго не чытаюць
        for cover, channel in (("A.png", 2), ("A.jpg", 0)):
            self.write_hike(cover=cover)
            for argv in ([], ["--force"]):
                out = io.StringIO()
                with unittest.mock.patch("sys.stdout", out):
                    build.main(argv)
                h = self.index()["h"]
                self.assertEqual(sorted(stem(p["src"]) for p in h["photos"]), ["a", "a", "img-1"], (cover, argv))
                self.assertEqual(self.cover_channel(h), channel, (cover, argv))
                self.assertNotIn("не чытаецца", out.getvalue())
        self.referenced_files_exist()

    def test_r7_3_multiday_folder_without_track_keeps_end_date(self):
        self.exif_jpeg(self.photos_root / "20250101-03_Trip" / "Сайт" / "A.jpg", (255, 0, 0), gps=[42.01, 41.01])
        self.exif_jpeg(self.photos_root / "20250105_Day" / "Сайт" / "B.jpg", (0, 0, 255), gps=[42.01, 41.01])
        common.write_json(build.CACHE / "komoot" / "tours.json", [])
        common.write_json(build.CACHE / "youtube.json", [])
        scaffold.main([])
        drafts = {p.stem: yaml.safe_load(p.read_text(encoding="utf-8")) for p in self.content.glob("*.yaml")}
        trip, day = (next(d for s, d in drafts.items() if s.startswith(prefix)) for prefix in ("2025-01-01", "2025-01-05"))
        self.assertEqual((trip["date"], trip["end"]), (date(2025, 1, 1), date(2025, 1, 3)))
        self.assertNotIn("end", day)  # аднадзённаму паходу поле не патрэбнае
        build.main([])
        by_date = {h["date"]: h for h in self.index().values()}
        self.assertEqual(by_date["2025-01-01"]["end"], "2025-01-03")
        self.assertEqual(by_date["2025-01-05"]["end"], "2025-01-05")
        self.assertEqual(by_date["2025-01-01"]["days"], [])

    def test_r7_3_end_before_start_is_ignored_and_tracks_win(self):
        self.photos_root.mkdir()
        (self.content / "bad.yaml").write_text("date: 2025-01-05\nend: 2025-01-03\ntitle: {be: Bad}\nlocation: [41, 42]\n",
                                               encoding="utf-8")
        pts = [[41.0 + i * 0.001, 42.0, 1000.0 + i, i] for i in range(10)]
        self.add_tour(1, "2025-02-01T06:00:00Z", pts, distance=3000)
        (self.content / "track.yaml").write_text("date: 2025-02-01\nend: 2025-02-09\ntitle: {be: T}\nkomoot: [1]\n",
                                                 encoding="utf-8")
        out = io.StringIO()
        with unittest.mock.patch("sys.stdout", out):
            build.main([])
        index = self.index()
        self.assertEqual(index["bad"]["end"], "2025-01-05")
        self.assertIn("дата заканчэння", out.getvalue())
        self.assertEqual(index["track"]["end"], "2025-02-01")  # даты паходу з трэкам — з тураў

    def test_r7_prev_restore_without_manifest_mixed_with_new_photo(self):
        folder = self.photos_root / "20250101_H" / "Сайт"
        self.exif_jpeg(folder / "B.jpg", (0, 0, 255), taken="2025:01:01 09:00:00")
        self.exif_jpeg(folder / "A.jpg", (255, 0, 0), taken="2025:01:01 10:00:00", gps=[42.01, 41.01])
        self.write_hike()
        build.main([])
        before = self.index()["h"]
        self.assertEqual(([stem(p["src"]) for p in before["photos"]], before["cover"], before["point"]),
                         (["b", "a"], 0, [41.01, 42.01]))
        (build.CACHE / "photos" / "h.json").unlink()
        for f in ("A.jpg", "B.jpg"):
            (folder / f).write_bytes(b"broken")
        # новае фота са сваім GPS: пункт на карце застаецца ранейшы (review v1.1.0, працяг)
        self.exif_jpeg(folder / "C.jpg", (0, 255, 0), taken="2025:01:01 08:00:00", gps=[43.01, 44.01])
        build.main([])
        after = self.index()["h"]
        self.assertEqual(after["photos"][:2], before["photos"])  # ранейшыя фота — у ранейшым парадку
        self.assertEqual([stem(p["src"]) for p in after["photos"]], ["b", "a", "c"])
        self.assertEqual((after["cover"], after["point"]), (0, [41.01, 42.01]))
        self.referenced_files_exist()

    # --- review_2026-10-11_v1.1.0.md ---

    def run_komoot(self, tours, responses, argv=("--refresh",)):
        """`hike komoot` без сеткі: уваход і спіс тураў падменены, адказы з каардынатамі — з `responses`."""
        class Response:
            def __init__(self, data):
                self.data = data

            def raise_for_status(self):
                pass

            def json(self):
                return self.data

        def fake_get(session, url, **kwargs):
            return Response(responses[int(re.search(r"/tours/(\d+)", url).group(1))])

        out = io.StringIO()
        with unittest.mock.patch.multiple(komoot, KOMOOT_DIR=build.CACHE / "komoot", load_config=lambda: self.cfg,
                                          login=lambda *a: ("u", "t", "D"), list_recorded_tours=lambda *a: tours), \
                unittest.mock.patch("requests.Session.get", fake_get), \
                unittest.mock.patch("builtins.input", lambda *a: "e@example.com"), \
                unittest.mock.patch("getpass.getpass", lambda *a: "p"), \
                unittest.mock.patch("time.sleep", lambda *a: None), \
                unittest.mock.patch("sys.stdout", out):
            komoot.main(list(argv))
        return out.getvalue()

    def test_r8_1_komoot_answer_without_coordinates_keeps_cache_and_track(self):
        self.photos_root.mkdir()
        pts = [[41.0 + i * 0.001, 42.0, 1000.0 + i, i] for i in range(10)]
        self.add_tour(1, "2025-01-01T06:00:00Z", pts, distance=3000)
        (self.content / "h.yaml").write_text("date: 2025-01-01\ntitle: {be: H}\nkomoot: [1]\n", encoding="utf-8")
        build.main([])
        before = self.index()["h"]
        self.assertTrue(before["track"] and before["stats"])
        meta = {"id": 1, "name": "t1", "sport": "hike", "date": "2025-01-01T06:00:00Z", "changed_at": "same",
                "status": "private", "distance": 3000}
        tour_file = build.CACHE / "komoot" / "tours" / "1.json"
        common.write_json(tour_file, {**common.read_json(tour_file), "changed_at": "same"})  # кэш актуальны
        full = lambda n: {"_embedded": {"coordinates": {"items": [  # noqa: E731
            {"lat": 41.0 + i * 0.002, "lng": 42.0, "alt": 1000.0, "t": i} for i in range(n)]}}}

        def unchanged():
            out = io.StringIO()
            with unittest.mock.patch("sys.stdout", out):
                build.main([])
            after = self.index()["h"]
            for k in ("track", "days", "lines", "stats", "point", "bbox"):
                self.assertEqual(after[k], before[k], k)
            self.referenced_files_exist()
            return out.getvalue()

        one_point = [{"lat": 41.0, "lng": 42.0, "alt": 1.0, "t": 0}]
        for answer in ({"id": 1}, {"_embedded": {"coordinates": {"items": []}}},
                       {"_embedded": {"coordinates": {"items": one_point}}}):
            out = self.run_komoot([meta], {1: answer})
            self.assertIn("без каардынат — у кэшы застаецца ранейшы", out)
            self.assertIn("спампавана трэкаў: 0, без каардынат: 1", out)
            self.assertEqual(common.read_json(tour_file)["coords"], pts)
            unchanged()

        # пасля няўдалага refresh звычайны запуск спрабуе зноў, хоць changed_at той самы (review v1.1.1)
        out = self.run_komoot([meta], {1: full(5)}, argv=())
        self.assertIn("спампавана трэкаў: 1.", out)
        cached = common.read_json(tour_file)
        self.assertEqual((len(cached["coords"]), "incomplete" in cached), (5, False))
        self.assertIn("спампавана трэкаў: 0.", self.run_komoot([meta], {1: {"id": 1}}, argv=()))  # кэш актуальны

        # кэша не было (праект скланаваны), а першы адказ няпоўны: апублікаваны трэк не выдаляецца
        tour_file.unlink()
        no_marker = {**meta, "changed_at": None}  # Komoot мог не аддаць changed_at
        out = self.run_komoot([no_marker], {1: {"id": 1}}, argv=())
        self.assertIn("тур застаецца без трэку", out)
        self.assertEqual(common.read_json(tour_file)["coords"], [])
        self.assertIn("туры [1] без каардынат — застаецца апублікаваны раней трэк", unchanged())

        # наступны запуск (і без --refresh) спрабуе зноў; поўны адказ абнаўляе кэш
        out = self.run_komoot([no_marker], {1: full(7)}, argv=())
        self.assertIn("спампавана трэкаў: 1.", out)
        self.assertEqual(len(common.read_json(tour_file)["coords"]), 7)

    def test_r8_2_hike_never_starts_after_its_first_track_day(self):
        pts = [[41.0 + i * 0.001, 42.0, 1000.0 + i, i] for i in range(10)]
        tours = ((1, "2025-01-01T06:00:00.000Z"), (2, "2025-12-30T06:00:00.000Z"), (3, "2026-01-03T06:00:00.000Z"))
        for tid, iso in tours:
            self.add_tour(tid, iso, pts, distance=3000)
        # тур напярэдадні даты тэчкі; тэчка праз Новы год з турамі ў суседнія дні
        (self.photos_root / "20250102_Trip" / "Сайт").mkdir(parents=True)
        (self.photos_root / "20251231-0102_NewYear" / "Сайт").mkdir(parents=True)
        common.write_json(build.CACHE / "komoot" / "tours.json", [
            {"id": tid, "name": "Hike", "sport": "hike", "date": iso, "start_point": {"lat": 41.7, "lng": 42.1}}
            for tid, iso in tours])
        common.write_json(build.CACHE / "youtube.json", [])
        scaffold.main([])
        drafts = {d["photos_folder"]: d for d in
                  (yaml.safe_load(p.read_text(encoding="utf-8")) for p in self.content.glob("*.yaml"))}
        self.assertEqual(drafts["20250102_Trip"]["date"], date(2025, 1, 1))
        self.assertEqual(drafts["20251231-0102_NewYear"]["date"], date(2025, 12, 30))
        build.main([])
        got = {h["days"][0]["date"]: (h["date"], h["end"]) for h in self.index().values()}
        self.assertEqual(got, {"2025-01-01": ("2025-01-01", "2025-01-01"), "2025-12-30": ("2025-12-30", "2026-01-03")})

        # ранейшы чарнавік з датай тэчкі і трэк без даты (імпартаваны GPX)
        for p in self.content.glob("*.yaml"):
            p.unlink()
        self.add_tour(9, "1970-01-01T00:00:00Z", pts, distance=3000)
        (self.content / "late.yaml").write_text("date: 2025-01-02\ntitle: {be: L}\nkomoot: [1]\n", encoding="utf-8")
        (self.content / "gpx.yaml").write_text("date: 2025-03-05\ntitle: {be: G}\nkomoot: [9]\n", encoding="utf-8")
        (self.content / "early.yaml").write_text("date: 2025-12-28\ntitle: {be: E}\nkomoot: [2, 3]\n", encoding="utf-8")
        build.main([])
        index = self.index()
        self.assertEqual((index["late"]["date"], index["late"]["end"]), ("2025-01-01", "2025-01-01"))
        self.assertEqual((index["gpx"]["date"], index["gpx"]["end"]), ("2025-03-05", "2025-03-05"))
        self.assertEqual((index["early"]["date"], index["early"]["end"]), ("2025-12-28", "2026-01-03"))

    def test_r8_3_nonexistent_dates_are_rejected(self):
        self.photos_root.mkdir()
        cases = (("2025-02-01", "2025-02-29", "2025-02-01"), ("2025-02-01", "2025-02-31", "2025-02-01"),
                 ("2025-02-01", "2025-13-01", "2025-02-01"), ("2024-02-27", "2024-02-29", "2024-02-29"))
        for i, (start, end, _) in enumerate(cases):
            (self.content / f"h{i}.yaml").write_text(
                f'date: {start}\nend: "{end}"\ntitle: {{be: H}}\nlocation: [41, 42]\n', encoding="utf-8")
        (self.content / "bad.yaml").write_text('date: "2025-02-31"\ntitle: {be: B}\nlocation: [41, 42]\n',
                                               encoding="utf-8")
        out = io.StringIO()
        with unittest.mock.patch("sys.stdout", out):
            build.main([])
        index = self.index()
        self.assertEqual([index[f"h{i}"]["end"] for i in range(len(cases))], [c[2] for c in cases])
        self.assertEqual(out.getvalue().count("дата заканчэння"), 3)
        self.assertEqual((index["bad"]["date"], index["bad"]["end"]), ("", ""))
        self.assertIn("bad: дата '2025-02-31' няправільная", out.getvalue())
        self.assertEqual(common.iso_date(date(2025, 5, 17)), "2025-05-17")

    def test_r8_4_missing_thumbnail_is_not_referenced(self):
        folder = self.photos_root / "20250101_H" / "Сайт"
        self.exif_jpeg(folder / "A.jpg", (255, 0, 0), taken="2025:01:01 09:00:00")
        self.exif_jpeg(folder / "B.jpg", (0, 0, 255), taken="2025:01:01 10:00:00")
        self.write_hike(cover="B")
        build.main([])
        before = self.index()["h"]
        (build.DOCS / before["photos"][0]["thumb"]).unlink()
        self.cfg["photos_root"] = str(self.root / "missing-disk")
        # 1) з папярэднім індэксам; 2) без індэкса, па маніфесце; 3) без маніфеста, па файлах
        for step in ("index", "manifest", "files"):
            if step == "manifest":
                (build.DOCS / "data" / "hikes.json").unlink()
            if step == "files":
                (build.DOCS / "data" / "hikes.json").unlink()
                (build.CACHE / "photos" / "h.json").unlink()
            out = io.StringIO()
            with unittest.mock.patch("sys.stdout", out):
                build.main([])
            after = self.index()["h"]
            self.assertEqual([p["src"] for p in after["photos"]], [p["src"] for p in before["photos"]], step)
            self.assertEqual(after["photos"][0]["thumb"], after["photos"][0]["src"], step)
            self.assertEqual(after["photos"][1], before["photos"][1], step)
            self.assertEqual(after["cover"], 1, step)
            self.assertIn("няма мініяцюр (1)", out.getvalue(), step)
            self.referenced_files_exist()

    # --- review_2026-10-11_v1.1.1.md ---

    def test_r9_1_corrupt_source_and_missing_thumbnail_keep_full_photo(self):
        originals = {}
        variants = [(m, argv) for m in (True, False) for argv in ([], ["--force"])]
        for i, (with_manifest, argv) in enumerate(variants):
            slug, name = f"h{i}", f"2025010{i + 1}_H{i}"
            source = self.photos_root / name / "Сайт" / "A.jpg"
            self.exif_jpeg(source, (255, 0, 0), taken="2025:01:01 09:00:00")
            self.write_hike(slug=slug, folder=name)
            build.main([])
            photo = self.index()[slug]["photos"][0]
            originals[slug] = (photo["src"], (build.DOCS / photo["src"]).read_bytes())
            (build.DOCS / photo["thumb"]).unlink()
            source.write_bytes(b"broken")
            if not with_manifest:
                (build.CACHE / "photos" / f"{slug}.json").unlink()
            build.main(argv)
            index = self.index()
            for done, (src, data) in originals.items():  # і паходы з ранейшых варыянтаў застаюцца цэлымі
                photos = index[done]["photos"]
                self.assertEqual([(p["src"], p["thumb"]) for p in photos], [(src, src)], (done, variants[i]))
                self.assertEqual((build.DOCS / src).read_bytes(), data, (done, variants[i]))
            self.referenced_files_exist()

    def test_r9_2_videos_are_matched_by_tour_dates(self):
        (self.photos_root / "20250102_Trip" / "Сайт").mkdir(parents=True)
        (self.photos_root / "20250210-11_Two" / "Сайт").mkdir(parents=True)
        tours = ((1, "2025-01-01T06:00:00.000Z"), (2, "2025-02-10T06:00:00.000Z"), (3, "2025-02-12T06:00:00.000Z"))
        common.write_json(build.CACHE / "komoot" / "tours.json", [
            {"id": tid, "name": "Hike", "sport": "hike", "date": iso, "start_point": {"lat": 41.7, "lng": 42.1}}
            for tid, iso in tours])
        common.write_json(build.CACHE / "youtube.json", [
            {"id": "v1", "title": "Video 1 студзеня 2025", "upload_date": "20250105"},
            {"id": "v2", "title": "Video 2 студзеня 2025", "upload_date": "20250105"},
            {"id": "v3", "title": "Video 12 лютага 2025", "upload_date": "20250220"},
            {"id": "v4", "title": "Video 14 лютага 2025", "upload_date": "20250220"}])
        scaffold.main([])
        drafts = {d["photos_folder"]: d for d in
                  (yaml.safe_load(p.read_text(encoding="utf-8")) for p in self.content.glob("*.yaml"))}
        trip, two = drafts["20250102_Trip"], drafts["20250210-11_Two"]
        self.assertEqual((trip["date"], sorted(trip["youtube"])), (date(2025, 1, 1), ["v1", "v2"]))
        self.assertEqual((two["date"], two["komoot"], two["youtube"]), (date(2025, 2, 10), [2, 3], ["v3"]))

    def test_r9_3_partial_loss_of_published_photos_keeps_order_cover_and_point(self):
        folder = self.photos_root / "20250101_H" / "Сайт"
        self.exif_jpeg(folder / "C.jpg", (0, 255, 0), taken="2025:01:01 09:00:00", gps=[42.01, 41.01])
        self.exif_jpeg(folder / "B.jpg", (0, 0, 255), taken="2025:01:01 10:00:00")
        self.exif_jpeg(folder / "A.jpg", (255, 0, 0), taken="2025:01:01 11:00:00")
        self.write_hike()
        build.main([])
        before = self.index()["h"]
        self.assertEqual(([stem(p["src"]) for p in before["photos"]], before["cover"], before["point"]),
                         (["c", "b", "a"], 0, [41.01, 42.01]))
        (build.CACHE / "photos" / "h.json").unlink()
        self.cfg["photos_root"] = str(self.root / "missing-disk")

        def lose(photo):
            for k in ("src", "thumb"):
                (build.DOCS / photo[k]).unlink()
            out = io.StringIO()
            with unittest.mock.patch("sys.stdout", out):
                build.main([])
            self.assertIn("няма файлаў 1 апублікаваных фота", out.getvalue())
            self.referenced_files_exist()
            return self.index()["h"]

        after = lose(before["photos"][2])  # страчана апошняе фота
        self.assertEqual((after["photos"], after["cover"], after["point"]), (before["photos"][:2], 0, before["point"]))
        after = lose(before["photos"][0])  # страчана сама вокладка
        self.assertEqual((after["photos"], after["cover"], after["point"]), (before["photos"][1:2], 0, before["point"]))

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

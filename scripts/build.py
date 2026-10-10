"""Збірае даныя сайта ў docs/: фота (WebP), трэкі і docs/data/hikes.json.

Запуск:  hike build            (апрацоўвае толькі новыя/змененыя фота)
         hike build --force    (перагенераваць усе фота)
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps

from common import (CACHE, CONTENT, DOCS, has_track, iso_date, load_config, read_version, load_yaml, read_json, site_photos_dir, slugify,
                    tour_local_datetime, write_json)
from geo import haversine, simplify

PILLOW_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
MAGICK_EXT = {".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".heic", ".heif"}
DIFFICULTIES = {"easy", "medium", "hard", "expert"}
DETAIL_TOLERANCE_M = 3     # трэк для профілю і карты пры выбары паходу
OVERVIEW_TOLERANCE_M = 30  # лініі ўсіх паходаў на агульнай карце


# --- Трэкі ---------------------------------------------------------------------

def build_tracks(slug: str, tour_ids: list, offset: float, tours_meta: dict):
    """Вяртае (days, lines, track_rel_path) або ([], [], None), калі трэкаў няма."""
    lines = []
    tours = []
    for tid in tour_ids:
        tour = read_json(CACHE / "komoot" / "tours" / f"{tid}.json")
        if tour:
            tours.append((tour_local_datetime(tour["date"], offset), tid, tour))
        else:
            print(f"  ! {slug}: няма тура {tid} у cache/komoot — запусціце `hike komoot`")
    # Даты з рознымі паясамі (Z / +04:00) параўноўваюцца як моманты часу, а не як радкі.
    by_date: dict[str, tuple[dict, dict]] = {}
    for local, tid, tour in sorted(tours, key=lambda x: x[0]):
        meta = tours_meta.get(int(tid), tour)
        if not has_track(tour.get("coords")):
            print(f"  ! {slug}: у туры {tid} няма каардынат — прапушчаны (запусціце `hike komoot --refresh`)")
            continue
        pts = [(c[0], c[1], c[2]) for c in tour["coords"] if c and c[0] is not None and c[1] is not None]
        keep = set(simplify(pts, DETAIL_TOLERANCE_M))
        dist, prev, coords = 0.0, None, []
        for i, p in enumerate(pts):
            if prev:
                dist += haversine(prev[0], prev[1], p[0], p[1])
            prev = p
            if i in keep:
                coords.append([round(p[1], 5), round(p[0], 5), round(p[2]) if p[2] is not None else None,
                               round(dist / 1000, 3)])
        lines.append([[round(pts[i][1], 4), round(pts[i][0], 4)] for i in simplify(pts, OVERVIEW_TOLERANCE_M, False)])
        alts = [p[2] for p in pts if p[2] is not None]
        part = {
            "date": local.date().isoformat(),
            "distance": (tour.get("distance") or dist) / 1000,
            "up": tour.get("elevation_up") or 0,
            "down": tour.get("elevation_down") or 0,
            "duration": tour.get("duration") or 0,
            "moving": tour.get("time_in_motion") or 0,
            "maxAlt": max(alts) if alts else None,
            "minAlt": min(alts) if alts else None,
            "komoot": [f"https://www.komoot.com/tour/{tid}"] if meta.get("status") == "public" else [],
        }

        # Некалькі тураў за адзін дзень (паўза ў запісе) — гэта адзін дзень з некалькімі адрэзкамі.
        if part["date"] in by_date:
            day, det = by_date[part["date"]]
            km0 = det["segs"][-1][-1][3]
            det["segs"].append([c[:3] + [round(c[3] + km0, 3)] for c in coords])
            for k in ("distance", "up", "down", "duration", "moving"):
                day[k] += part[k]
            day["maxAlt"] = max((a for a in (day["maxAlt"], part["maxAlt"]) if a is not None), default=None)
            day["minAlt"] = min((a for a in (day["minAlt"], part["minAlt"]) if a is not None), default=None)
            day["komoot"] += part["komoot"]
        else:
            by_date[part["date"]] = (part, {"segs": [coords]})
    ordered = [by_date[d] for d in sorted(by_date)]
    days, detail = [d for d, _ in ordered], [det for _, det in ordered]
    if not days:
        return [], [], None
    for day in days:
        day["distance"] = round(day["distance"], 2)
        for k in ("up", "down", "duration", "moving", "maxAlt", "minAlt"):
            day[k] = round(day[k]) if day[k] is not None else None
    # Імя з хэшам змесціва: новая версія трэку не перазапісвае файл, на які спасылаецца апублікаваны
    # індэкс (зборка можа ўпасці да запісу новага), і кэш браўзера не аддае стары трэк.
    data = {"days": detail}
    digest = hashlib.sha1(json.dumps(data, separators=(",", ":")).encode()).hexdigest()[:10]
    rel = f"data/tracks/{slug}-{digest}.json"
    if not (DOCS / rel).exists():
        write_json(DOCS / rel, data, compact=True)
    return days, lines, rel


# --- Фота ----------------------------------------------------------------------

def _exif_info(img: Image.Image):
    """(дата здымкі ISO або None, [lng, lat] або None)."""
    taken, gps = None, None
    try:
        exif = img.getexif()
    except Exception:
        return taken, gps
    try:  # дата і GPS разбіраюцца незалежна: памылковая дата не павінна губляць каардынаты
        dt = exif.get_ifd(0x8769).get(36867) or exif.get(306)
        if dt:
            taken = datetime.strptime(str(dt).strip("\x00")[:19], "%Y:%m:%d %H:%M:%S").isoformat()
    except Exception:
        taken = None
    try:
        g = exif.get_ifd(0x8825)
        if g and 2 in g and 4 in g:
            def deg(v):
                return float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
            lat, lng = deg(g[2]), deg(g[4])
            if g.get(1) == "S":
                lat = -lat
            if g.get(3) == "W":
                lng = -lng
            if lat or lng:
                gps = [round(lng, 5), round(lat, 5)]
    except Exception:
        pass
    return taken, gps


def _open_image(src: Path, tmp_dir: Path) -> Image.Image:
    if src.suffix.lower() in PILLOW_EXT:
        return Image.open(src)
    if not shutil.which("magick"):
        raise RuntimeError("для RAW/HEIC патрэбны ImageMagick (magick)")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / (src.stem + ".tif")
    subprocess.run(["magick", str(src), "-auto-orient", str(tmp)], check=True, capture_output=True)
    img = Image.open(tmp)
    img.load()  # чытаем у памяць, каб адразу выдаліць вялікі часовы TIFF
    tmp.unlink()
    return img


def _webp_bytes(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=quality, method=5)
    return buf.getvalue()


def _write_bytes(path: Path, data: bytes) -> None:
    """Атамарны запіс: перапынены запіс не пакідае паўфайла."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _pair_exists(out_dir: Path, name: str) -> bool:
    return (out_dir / f"{name}.webp").is_file() and (out_dir / f"{name}-t.webp").is_file()


def _is_thumb(f: Path) -> bool:
    """`x-t.webp` побач з `x.webp` — мініяцюра фота `x`, а не асобнае фота."""
    return f.stem.endswith("-t") and f.with_name(f"{f.stem[:-2]}.webp").is_file()


def _save_pair(out_dir: Path, base: str, big: bytes, small: bytes, run_used: set) -> str:
    """Запісвае новую версію фота пад імем з хэшам змесціва і вяртае імя (без .webp).

    Існуючы файл ніколі не перазапісваецца іншымі байтамі: на яго можа спасылацца апублікаваны
    індэкс, а зборка можа ўпасці да запісу новага. Старая версія выдаляецца пасля запісу індэкса.
    """
    stem = f"{base}-{hashlib.sha1(big + small).hexdigest()[:8]}"
    n = 0
    while True:
        name = stem if n == 0 else f"{stem}-{n}"
        full, thumb = out_dir / f"{name}.webp", out_dir / f"{name}-t.webp"
        n += 1
        if {full.name, thumb.name} & run_used:
            continue
        if full.exists() or thumb.exists():
            if _pair_exists(out_dir, name) and full.read_bytes() == big and thumb.read_bytes() == small:
                break  # гэтая ж версія ўжо апублікаваная
            continue
        _write_bytes(full, big)
        _write_bytes(thumb, small)
        break
    run_used |= {full.name, thumb.name}
    return name


def _remove(paths) -> None:
    for p in paths:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)


def _published_entry(out_dir: Path, key: str, old_manifest: dict, run_used: set, protect: set) -> dict | None:
    """Ранейшая апублікаваная версія фота `key`, калі яго крыніца больш не чытаецца.

    Імя бярэцца з маніфеста менавіта гэтай крыніцы: файл з падобным імем можа належаць іншаму фота
    (напрыклад, выдаленаму, якое мела той жа slug). Без маніфеста шукаецца фота WebP з тым жа slug,
    якое не належыць іншай крыніцы. Калі такіх некалькі (два фота з адным slug), прывязаць
    нельга: усе яны дадаюцца ў `protect` і не выдаляюцца, пакуль крыніца не стане чытэльнай.
    Дастаткова поўнага фота: адсутную мініяцюру ў індэксе замяняе яно само (гл. _existing_thumbs).
    """
    old = old_manifest.get(key)
    if old and old.get("name"):
        name = old["name"]
        if (out_dir / f"{name}.webp").is_file() and not {f"{name}.webp", f"{name}-t.webp"} & run_used:
            return dict(old)
        return None
    if not out_dir.is_dir():
        return None
    base = slugify(Path(key).stem)
    others = {m.get("name") for k, m in old_manifest.items() if k != key}
    pattern = re.compile(re.escape(base) + r"(-[0-9a-f]{8})?(-\d+)*")
    cands = sorted(f.stem for f in out_dir.glob("*.webp")
                   if pattern.fullmatch(f.stem) and f.stem not in others and not _is_thumb(f)
                   and not {f.name, f"{f.stem}-t.webp"} & run_used)
    if len(cands) > 1:
        print(f"  ! невядома, якое з апублікаваных фота {', '.join(cands)} належыць {key} — файлы захаваныя")
        protect |= {f"{n}{s}.webp" for n in cands for s in ("", "-t")}
        return None
    if not cands:
        return None
    name = cands[0]
    with Image.open(out_dir / f"{name}.webp") as im:
        w, h = im.size
    return {"mtime": None, "size": None, "name": name, "w": w, "h": h, "taken": "", "gps": None, "restored": True}


def build_photos(slug: str, src_dir: Path | None, cover: str | None, cfg: dict, force: bool,
                 pending: list | None = None, prev: dict | None = None):
    """Вяртае (photos, cover_index, gps_першага_фота_з_GPS).

    Састарэлыя файлы дадаюцца ў `pending` (main выдаляе іх толькі пасля запісу новага індэкса);
    без `pending` яны выдаляюцца адразу. `prev` — запіс паходу з папярэдняга hikes.json.
    """
    obsolete = [] if pending is None else pending
    out_dir = DOCS / "photos" / slug
    manifest_path = CACHE / "photos" / f"{slug}.json"
    old_manifest = read_json(manifest_path, {})
    manifest = {} if force else dict(old_manifest)  # --force толькі адключае пропуск пераканвертацыі
    if not src_dir:
        if out_dir.exists():
            obsolete.append(out_dir)
        if pending is None:
            _remove(obsolete)
        return [], 0, None

    files = [f for f in sorted(src_dir.iterdir()) if f.is_file() and f.suffix.lower() in PILLOW_EXT | MAGICK_EXT]
    # RAW/HEIC побач з гатовай копіяй (IMG_1.CR3 + IMG_1.jpg) — адно фота: бярэцца гатовая копія.
    # Гатовыя файлы з адным імем (A.jpg і A.png) — розныя фота.
    ready = {f.stem.lower() for f in files if f.suffix.lower() in PILLOW_EXT}
    sources = [f for f in files if f.suffix.lower() in PILLOW_EXT or f.stem.lower() not in ready]

    prev_names = [Path(p["src"]).stem for p in (prev or {}).get("photos") or []]
    pc = cfg["photo"]
    entries, run_used, protect = [], set(), set()
    # Спачатку фота без змен: іх файлы застаюцца пад сваімі імёнамі, і новыя версіі іншых фота
    # не могуць іх заняць.
    todo = []
    for src in sources:
        st = src.stat()
        m = manifest.get(src.name)
        if m and m["mtime"] == st.st_mtime and m["size"] == st.st_size and _pair_exists(out_dir, m["name"]) \
                and not {f"{m['name']}.webp", f"{m['name']}-t.webp"} & run_used:
            run_used |= {f"{m['name']}.webp", f"{m['name']}-t.webp"}
            entries.append((m["taken"], src.name, m))
        else:
            todo.append((src, st))
    for src, st in todo:
        key = src.name
        try:
            with _open_image(src, CACHE / "tmp") as raw:
                taken, gps = _exif_info(raw)
                img = ImageOps.exif_transpose(raw).convert("RGB")
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError,
                Image.DecompressionBombError) as exc:
            # Адно пашкоджанае фота не павінна спыняць зборку ўсяго сайта.
            kept = _published_entry(out_dir, key, old_manifest, run_used, protect)
            if kept:
                print(f"  ! фота {src.name} не чытаецца ({exc}) — застаецца папярэдняя версія")
                run_used |= {f"{kept['name']}.webp", f"{kept['name']}-t.webp"}
                manifest[key] = kept
                entries.append((kept["taken"], key, kept))
            else:
                print(f"  ! фота {src.name} не чытаецца ({exc}) — прапушчана")
            continue
        big = img.copy()
        big.thumbnail((pc["max_size"], pc["max_size"]), Image.LANCZOS)
        small = img.copy()
        small.thumbnail((pc["thumb_size"], pc["thumb_size"]), Image.LANCZOS)
        big_bytes, small_bytes = _webp_bytes(big, pc["quality"]), _webp_bytes(small, pc["thumb_quality"])
        out_dir.mkdir(parents=True, exist_ok=True)
        name = _save_pair(out_dir, slugify(src.stem), big_bytes, small_bytes, run_used)
        m = {"mtime": st.st_mtime, "size": st.st_size, "name": name, "w": big.width, "h": big.height,
             "taken": taken or datetime.fromtimestamp(st.st_mtime).isoformat(), "gps": gps}
        manifest[key] = m
        print(f"    фота {src.name} → {name}.webp ({len(big_bytes) // 1024} КБ)")
        entries.append((m["taken"], key, m))

    # Маніфест захоўвае толькі тое, што вядома пра крыніцу: аднаўленне без маніфеста ў яго не трапляе.
    write_json(manifest_path, {k: m for _, k, m in entries if not m.get("restored")})
    keep_files = {f"{m['name']}.webp" for *_, m in entries} | {f"{m['name']}-t.webp" for *_, m in entries}
    keep_files |= protect
    if out_dir.is_dir():
        obsolete += [f for f in out_dir.iterdir() if f.name not in keep_files]
    if pending is None:
        _remove(obsolete)
    order = {n: i for i, n in enumerate(prev_names)}
    if any(m.get("restored") for *_, m in entries):
        # Без маніфеста час здымкі і GPS адноўленых фота невядомыя: фота з папярэдняга індэкса застаюцца
        # ў ранейшым парадку, новыя ідуць пасля іх па часе здымкі; вокладка і пункт — таксама ранейшыя.
        prev = prev or {}
        known = sorted((e for e in entries if e[2]["name"] in order), key=lambda e: order[e[2]["name"]])
        fresh = sorted((e for e in entries if e[2]["name"] not in order), key=lambda e: (e[0] or "", e[1]))
        entries = known + fresh
        photos, cover_idx, gps = photo_list(slug, entries, cover, keep_order=True)
        if cover_idx is None:  # ранейшая вокладка — па імені файла, а не па нумары ў старым спісе
            old = (prev.get("photos") or [])[prev.get("cover") or 0:][:1]
            srcs = [p["src"] for p in photos]
            cover_idx = srcs.index(old[0]["src"]) if old and old[0]["src"] in srcs else 0
        # Ранейшы пункт (ён браўся з GPS аднаго з ранейшых фота) важнейшы за GPS новага фота.
        gps = (prev.get("point") if not prev.get("lines") else None) or gps
        return _existing_thumbs(slug, photos), cover_idx, gps
    photos, cover_idx, gps = photo_list(slug, entries, cover)
    return _existing_thumbs(slug, photos), cover_idx or 0, gps


def photo_list(slug: str, entries: list, cover: str | None, keep_order: bool = False):
    """entries: [(дата здымкі, імя крыніцы, запіс маніфеста)] → (photos, cover_index або None, gps)."""
    if not keep_order:
        entries = sorted(entries, key=lambda e: (e[0] or "", e[1]))
    photos, gps, exact, by_slug = [], None, [], []
    for i, (_, key, m) in enumerate(entries):
        photos.append({"src": f"photos/{slug}/{m['name']}.webp", "thumb": f"photos/{slug}/{m['name']}-t.webp",
                       "w": m["w"], "h": m["h"]})
        if cover and str(cover).lower() in (key.lower(), Path(key).stem.lower()):
            exact.append(i)
        elif cover and _cover_matches_name(cover, m["name"]):
            by_slug.append(i)
        gps = gps or m.get("gps")
    # Дакладнае імя файла важнейшае за супадзенне slug: `A t.jpg` і `A-t.jpg` даюць адзін slug.
    if not exact and len(by_slug) > 1:
        print(f"  ! {slug}: вокладцы «{cover}» адпавядае некалькі фота — пазначце поўнае імя файла")
    match = exact or by_slug
    return photos, (match[0] if match else None), gps


def _cover_matches_name(cover, name: str) -> bool:
    """Вокладка `IMG_2` адпавядае файлу `img-2.webp` і новай версіі `img-2-<хэш>.webp`."""
    wanted = slugify(Path(str(cover)).stem)
    return name == wanted or re.fullmatch(re.escape(wanted) + r"-[0-9a-f]{8}(-\d+)?", name) is not None


def previous_photos(slug: str, cover: str | None, prev: dict | None = None):
    """Фота, ужо апублікаваныя ў docs/photos/<slug> — калі крыніца недаступная, сайт іх не губляе.

    Парадак, вокладка і пункт на карце бяруцца з папярэдняга hikes.json; калі яго няма —
    з маніфеста фота; калі няма і маніфеста — з саміх файлаў WebP.
    """
    prev_photos = (prev or {}).get("photos") or []
    usable = [(i, q) for i, q in ((i, _usable_photo(p)) for i, p in enumerate(prev_photos)) if q]
    if usable:
        # Фота паказваюцца з тымі файламі, якія засталіся: без мініяцюры — само фота, без поўнай копіі —
        # мініяцюра; цалкам страчаныя прыбіраюцца. Парадак, вокладка і пункт астатніх — ранейшыя.
        photos = [q for _, q in usable]
        for count, text in ((len(prev_photos) - len(usable), "няма файлаў {} апублікаваных фота — прыбраныя з галерэі"),
                            (sum(q["src"] != prev_photos[i]["src"] for i, q in usable),
                             "няма поўных копій ({}) — замест іх паказваюцца мініяцюры"),
                            (sum(q["thumb"] != prev_photos[i]["thumb"] for i, q in usable),
                             "няма мініяцюр ({}) — замест іх паказваюцца поўныя фота")):
            if count:
                print(f"  ! {slug}: {text.format(count)}")
        names = [Path(prev_photos[i]["src"]).stem for i, _ in usable]  # імёны ранейшых поўных фота
        # ранейшая вокладка — па месцы ў ранейшым спісе, а не па нумары ў новым
        prev_cover = next((n for n, (i, _) in enumerate(usable) if i == (prev.get("cover") or 0)), None)
        manifest = read_json(CACHE / "photos" / f"{slug}.json", {})
        exact = [names.index(m["name"]) for key, m in manifest.items() if cover and m.get("name") in names
                 and str(cover).lower() in (key.lower(), Path(key).stem.lower())]
        match = [i for i, n in enumerate(names) if cover and _cover_matches_name(cover, n)]
        # Дакладнае імя з маніфеста; без яго — ранейшая вокладка, калі яна адпавядае `cover` (адзін slug
        # можа быць у некалькіх фота); інакш першае супадзенне.
        if exact:
            cover_idx = exact[0]
        elif prev_cover is not None and (not match or prev_cover in match):
            cover_idx = prev_cover
        else:
            cover_idx = match[0] if match else 0
        gps = prev.get("point") if not prev.get("lines") else None  # пункт без трэку браўся з GPS фота
        return photos, cover_idx, gps
    out_dir = DOCS / "photos" / slug
    manifest = read_json(CACHE / "photos" / f"{slug}.json", {})
    entries = [(m["taken"], key, m) for key, m in manifest.items() if (out_dir / f"{m['name']}.webp").exists()]
    if not entries and out_dir.is_dir():  # кэша няма (напрыклад, іншы камп'ютар) — адноўліваем па файлах
        for f in sorted(out_dir.glob("*.webp")):
            if _is_thumb(f):
                continue
            with Image.open(f) as im:
                entries.append(("", f.stem, {"name": f.stem, "w": im.width, "h": im.height}))
    photos, cover_idx, gps = photo_list(slug, entries, cover)
    return _existing_thumbs(slug, photos), cover_idx or 0, gps


def _usable_photo(p: dict) -> dict | None:
    """Апублікаванае фота з тымі файламі, якія засталіся на дыску; None, калі няма ні аднаго."""
    has_src, has_thumb = (DOCS / p["src"]).is_file(), (DOCS / p["thumb"]).is_file()
    if has_src:
        return p if has_thumb else {**p, "thumb": p["src"]}
    if not has_thumb:
        return None
    with Image.open(DOCS / p["thumb"]) as im:  # поўнай копіі няма: паказваецца мініяцюра з яе памерамі
        return {**p, "src": p["thumb"], "w": im.width, "h": im.height}


def _existing_thumbs(slug: str, photos: list) -> list:
    """Індэкс не спасылаецца на мініяцюру, якой няма: замест яе паказваецца само фота."""
    lost = [p for p in photos if not (DOCS / p["thumb"]).exists()]
    if lost:
        print(f"  ! {slug}: няма мініяцюр ({len(lost)}) — замест іх паказваюцца поўныя фота")
    return [{**p, "thumb": p["src"]} if p in lost else p for p in photos]


# --- Зборка ----------------------------------------------------------------------

def unique_tours(slug: str, raw) -> list:
    """Туры з поля `komoot:` без паўтораў (у ранейшым парадку): паўтор падвоіў бы трэк і статыстыку."""
    seen, tours = set(), []
    for tid in raw or []:
        key = str(tid).strip()
        key = int(key) if key.isdigit() else key  # кананічны ID («01» і 1 — адзін тур): па ім шукаецца файл у кэшы
        if key in seen:
            print(f"  ! {slug}: тур {key} пазначаны ў komoot: некалькі разоў — улічаны адзін раз")
            continue
        seen.add(key)
        tours.append(key)
    return tours


def current_links(days: list, tours_meta: dict) -> list:
    """Копія ранейшых дзён без спасылак на туры, якія паводле актуальных метаданых ужо не публічныя."""
    def is_public(url: str) -> bool:
        tid = url.rstrip("/").rsplit("/", 1)[-1]
        meta = tours_meta.get(int(tid)) if tid.isdigit() else None
        return meta is None or meta.get("status") == "public"  # без метаданых спасылка застаецца ранейшай
    return [{**d, "komoot": [url for url in d.get("komoot") or [] if is_public(url)]} for d in days]


def hike_dates(slug: str, raw_start, raw_end, days: list) -> tuple[str, str]:
    """(пачатак, канец) паходу ў ISO: `date` і `end` з YAML разам з днямі трэку; заўсёды пачатак <= канец."""
    start = iso_date(raw_start) if raw_start else None
    if raw_start and not start:
        print(f"  ! {slug}: дата '{raw_start}' няправільная — не ўлічваецца")
    # Трэк без даты (імпартаваны GPX дае 1970 год) на даты паходу не ўплывае.
    dated = [d["date"] for d in days if d["date"] >= "2000"]
    if dated and (not start or dated[0] < start):
        # Паход пачынаецца не пазней за першы дзень трэку: назва тэчкі магла «з'ехаць» на дзень.
        if start:
            print(f"  · {slug}: date {start} пазней за першы дзень трэку — пачатак паходу {dated[0]}")
        start = dated[0]
    start = start or ""
    if dated:
        return start, max(dated[-1], start)  # даты паходу з трэкам — з тураў
    end = iso_date(raw_end) if raw_end else None
    if raw_end and not (end and end >= start):
        print(f"  ! {slug}: дата заканчэння '{raw_end}' няправільная або раней за пачатак — не ўлічваецца")
        end = None
    return start, end or start


def text_pair(value) -> dict:
    if isinstance(value, dict):
        return {"be": (value.get("be") or "").strip(), "en": (value.get("en") or "").strip()}
    return {"be": (value or "").strip(), "en": ""}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="hike build")
    ap.add_argument("--force", action="store_true", help="перагенераваць усе фота")
    args = ap.parse_args(argv)
    if not CONTENT.is_dir():
        # Адсутная тэчка (перайменавана, не адноўлена з копіі) — не тое ж, што пустая: без яе зборка
        # запісала б пусты індэкс і выдаліла ўсе апублікаваныя фота і трэкі.
        raise SystemExit(f"! Тэчка з апісаннямі паходаў не знойдзена: {CONTENT}. Сайт не змяняўся.")
    cfg = load_config()
    photos_root = Path(cfg["photos_root"])
    offset = cfg.get("default_utc_offset", 0)
    tours_meta = {int(t["id"]): t for t in read_json(CACHE / "komoot" / "tours.json", [])}
    videos_by_id = {v["id"]: v for v in read_json(CACHE / "youtube.json", [])}
    published = read_json(DOCS / "data" / "hikes.json", None) or {}
    previous = {ph["slug"]: ph for ph in published.get("hikes", [])}
    pending: list[Path] = []  # састарэлыя файлы выдаляюцца толькі пасля запісу новага індэкса
    photos_available = photos_root.is_dir()
    if not photos_available:
        print(f"! Тэчка з фота недаступная: {photos_root}. Фота не абнаўляюцца — застаюцца апублікаваныя раней.")

    # Усе апісанні чытаюцца да пачатку працы: памылка ў адным файле спыняе зборку, пакуль нічога не зменена.
    sources = [(path.stem, load_yaml(path)) for path in sorted(CONTENT.glob("*.yaml"))]
    hikes = []
    for slug, h in sources:
        if h.get("hidden"):
            continue
        print(f"• {slug}")
        prev = previous.get(slug)
        tour_ids = unique_tours(slug, h.get("komoot"))
        cached = {t: read_json(CACHE / "komoot" / "tours" / f"{t}.json") for t in tour_ids}
        missing = [t for t, tour in cached.items() if not tour]
        empty = [t for t, tour in cached.items() if tour and not has_track(tour.get("coords"))]
        if (missing or empty) and prev and prev.get("track") and (DOCS / prev["track"]).exists():
            # Адсутны файл тура або тур без каардынат (няпоўны адказ Komoot) не выдаляе апублікаваны трэк.
            what = " і ".join(w for w in (f"няма тураў {missing}" if missing else "",
                                          f"туры {empty} без каардынат" if empty else "") if w)
            print(f"  ! {slug}: у кэшы {what} — застаецца апублікаваны раней трэк "
                  f"(запусціце `hike komoot{' --refresh' if empty else ''}`)")
            # Геаметрыя ранейшая, а спасылкі — толькі на туры, якія дагэтуль публічныя.
            days, lines, track = current_links(prev["days"], tours_meta), prev["lines"], prev["track"]
        else:
            days, lines, track = build_tracks(slug, tour_ids, offset, tours_meta)

        start, end = hike_dates(slug, h.get("date"), h.get("end"), days)
        if not start:
            # Без даты паход нельга ні паказаць, ні адсартаваць; пропуск выдаліў бы яго з сайта.
            raise SystemExit(f"! {slug}: няма сапраўднай даты паходу (поле date) і трэку з датай. "
                             f"Выпраўце {slug}.yaml і запусціце зноў — індэкс сайта не змяняўся.")

        folder = h.get("photos_folder")
        if folder and not (photos_available and (photos_root / folder).is_dir()):
            if photos_available:
                print(f"  ! {slug}: тэчка {folder} не знойдзена — застаюцца апублікаваныя раней фота")
            photos, cover_idx, photo_gps = previous_photos(slug, h.get("cover"), prev)
        else:
            src_dir = site_photos_dir(photos_root / folder, cfg) if folder else None
            photos, cover_idx, photo_gps = build_photos(slug, src_dir, h.get("cover"), cfg, args.force, pending,
                                                        prev)

        point, bbox = None, None
        if lines:
            all_pts = [p for line in lines for p in line]
            point = lines[0][0]
            bbox = [min(p[0] for p in all_pts), min(p[1] for p in all_pts),
                    max(p[0] for p in all_pts), max(p[1] for p in all_pts)]
        elif h.get("location"):
            point = [float(h["location"][1]), float(h["location"][0])]
        elif photo_gps:
            point = photo_gps
        if not point:
            print(f"  ! {slug}: няма ні трэку, ні location — паход не з'явіцца на карце")

        difficulty = (h.get("difficulty") or "").strip().lower() or None
        if difficulty and difficulty not in DIFFICULTIES:
            print(f"  ! {slug}: невядомая складанасць '{difficulty}' (easy|medium|hard|expert)")
            difficulty = None

        alts_max = [d["maxAlt"] for d in days if d["maxAlt"] is not None]
        alts_min = [d["minAlt"] for d in days if d["minAlt"] is not None]
        videos = []
        for vid in h.get("youtube") or []:
            v = videos_by_id.get(vid, {"id": vid})
            videos.append({"id": vid, "title": v.get("title") or ""})

        hikes.append({
            "slug": slug,
            "date": start,
            "end": end,
            "title": text_pair(h.get("title")),
            "region": text_pair(h.get("region")),
            "difficulty": difficulty,
            "stats": {
                "distance": round(sum(d["distance"] for d in days), 2),
                "up": sum(d["up"] for d in days),
                "down": sum(d["down"] for d in days),
                "duration": sum(d["duration"] for d in days),
                "moving": sum(d["moving"] for d in days),
                "maxAlt": max(alts_max) if alts_max else None,
                "minAlt": min(alts_min) if alts_min else None,
            } if days else None,
            "days": days,
            "point": point,
            "bbox": bbox,
            "lines": lines,
            "track": track,
            "cover": cover_idx if photos else None,
            "photos": photos,
            "videos": videos,
            "impressions": text_pair(h.get("impressions")),
        })

    slugs = {h["slug"] for h in hikes}
    pending += [d for d in (DOCS / "photos").glob("*") if d.is_dir() and d.name not in slugs]
    referenced = {Path(h["track"]).name for h in hikes if h["track"]}
    pending += [f for f in (DOCS / "data" / "tracks").glob("*.json") if f.name not in referenced]

    hikes.sort(key=lambda h: h["date"], reverse=True)
    write_json(DOCS / "data" / "hikes.json",
               {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "version": read_version(),
                "hikes": hikes}, compact=True)
    # Новы індэкс запісаны — цяпер можна прыбраць файлы, на якія ён ужо не спасылаецца.
    _remove(pending)
    (DOCS / ".nojekyll").touch()
    n_photos = sum(len(h["photos"]) for h in hikes)
    print(f"Гатова: паходаў {len(hikes)}, фота {n_photos} → docs/data/hikes.json")


if __name__ == "__main__":
    main()

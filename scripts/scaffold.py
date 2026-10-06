"""Стварае чарнавікі content/hikes/<slug>.yaml для паходаў, якіх яшчэ няма.

Запуск:  hike new

Крыніцы:
  * хайкінг-туры з cache/komoot (hike komoot) — туры аднаго паходу групуюцца
    па тэчцы з фота, у якую трапляе іх дата (кожны тур = адзін дзень);
  * тэчкі з падтэчкай «Сайт» / «для сайту», для якіх няма тура (паход без трэку);
  * відэа з cache/youtube.json (hike youtube) — падбіраюцца па даце ў назве
    або па супадзенні назваў месцаў.
Існуючыя файлы ніколі не перазапісваюцца. Туры, якія не трэба ператвараць
у паходы, можна дадаць у content/ignore.yaml.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from common import (CACHE, CONTENT, hike_slug, load_config, load_yaml, parse_folder, read_json, site_photos_dir,
                    slugify, tour_local_datetime)
from build import PILLOW_EXT, _exif_info
from geo import place_name, region_for_point
from PIL import Image

MONTHS = {
    "студзеня": 1, "лютага": 2, "сакавіка": 3, "красавіка": 4, "траўня": 5, "мая": 5, "чэрвеня": 6,
    "ліпеня": 7, "жніўня": 8, "верасня": 9, "кастрычніка": 10, "лістапада": 11, "снежня": 12,
}
DATE_WORDS_RE = re.compile(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", re.IGNORECASE)
DATE_DIGITS_RE = re.compile(r"\b(20\d{2})(\d{2})(\d{2})\b")
STOP = {
    "паход", "паходзе", "паходы", "разважанні", "возера", "возеры", "азёра", "гара", "гару", "гары", "горы",
    "вяршыні", "вяршыня", "першы", "другі", "раз", "годзе", "года", "hike", "hiking", "morning", "afternoon",
    "evening", "walk", "loop", "trail", "tour", "пешы", "прагулка", "ранішні", "вячэрні", "дзённы",
}
VIDEO_WINDOW_DAYS = 200
GENERIC_NAME_RE = re.compile(r"^(hiking tour|hike|hiking|tour|пешы паход|паход|прагулка|wanderung)$", re.IGNORECASE)


def word_key(word: str) -> str:
    """Грубы «фанетычны» ключ, каб 'Мцірала' ≈ 'Mtirala', 'Казбегі' ≈ 'Kazbegi'."""
    s = slugify(word).replace("-", "")
    for a, b in (("ts", "t"), ("kh", "h"), ("g", "h"), ("y", "i"), ("w", "u")):
        s = s.replace(a, b)
    return s[:5]


def keywords(*texts: str) -> set[str]:
    keys = set()
    for text in texts:
        for w in re.findall(r"[^\W\d_]+", text or ""):
            if len(w) >= 4 and w.lower() not in STOP:
                keys.add(word_key(w))
    return keys


def dates_in_title(title: str) -> list[date]:
    found = []
    for d, m, y in DATE_WORDS_RE.findall(title):
        try:
            found.append(date(int(y), MONTHS[m.lower()], int(d)))
        except ValueError:
            pass
    for y, m, d in DATE_DIGITS_RE.findall(title):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    return found


def list_folders(root, cfg):
    folders = []
    for p in root.iterdir():
        if not p.is_dir():
            continue
        parsed = parse_folder(p.name)
        if parsed:
            start, end, title = parsed
            folders.append({"name": p.name, "start": start, "end": end, "title": title,
                            "has_site": site_photos_dir(p, cfg) is not None})
    return folders


def pick_folder(day: date, folders):
    cands = [f for f in folders if f["start"] <= day <= f["end"]]
    if not cands:  # назва тэчкі магла «з'ехаць» на дзень (паход пачаўся напярэдадні)
        one = timedelta(days=1)
        cands = [f for f in folders if f["start"] - one <= day <= f["end"] + one and f["title"]]
    if not cands:
        return None
    # Лепш тэчка з «Сайт», потым з назвай, потым вузейшы дыяпазон дат.
    cands.sort(key=lambda f: (not f["has_site"], not f["title"], (f["end"] - f["start"]).days))
    return cands[0]


def photo_location(folder: Path, cfg: dict):
    """[lat, lng] першага фота з GPS у падтэчцы «Сайт» (для паходаў без трэку)."""
    site = site_photos_dir(folder, cfg)
    if not site:
        return None
    for f in sorted(site.iterdir()):
        if f.suffix.lower() in PILLOW_EXT:
            with Image.open(f) as img:
                _, gps = _exif_info(img)
            if gps:
                return [gps[1], gps[0]]
    return None


def existing_hikes():
    hikes = {}
    for path in CONTENT.glob("*.yaml"):
        hikes[path.stem] = load_yaml(path)
    return hikes


def yaml_str(s: str) -> str:
    return '"' + (s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_yaml(h: dict, today: str) -> str:
    lines = [
        f"# Чарнавік створаны аўтаматычна {today}: праверце і дапоўніце.",
        f"date: {h['start'].isoformat()}",
        "title:",
        f"  be: {yaml_str(h['title'])}",
        '  en: ""                    # калі пуста, паказваецца беларуская назва',
        "region:",
        f"  be: {yaml_str(h['region']['be'])}",
        f"  en: {yaml_str(h['region']['en'])}",
        "difficulty:                 # easy | medium | hard | expert",
        f"photos_folder: {yaml_str(h['folder'] or '')}   # фота з падтэчкі «Сайт» або «для сайту»",
        "cover:                      # імя файла вокладкі; калі пуста, першае фота",
    ]
    if h["tours"]:
        lines.append("komoot:                     # туры Komoot; туры аднаго дня аб'ядноўваюцца ў адзін дзень")
        for t in h["tours"]:
            lines.append(f"  - {t['id']}   # {t['local'].date().isoformat()}  {t['name']}")
    else:
        lines.append("komoot: []")
    if h["videos"]:
        lines.append("youtube:                    # падабрана аўтаматычна, праверце")
        for v in h["videos"]:
            lines.append(f"  - {v['id']}   # {v['title']}")
    else:
        lines.append("youtube: []")
    if not h["tours"]:
        loc = f"[{h['location'][0]}, {h['location'][1]}]   # з GPS фота" if h.get("location") else ""
        lines.append(f"location: {loc}                  # [шырата, даўгата]: патрэбна, бо няма трэку")
    lines += [
        "impressions:",
        "  be: |",
        "    ",
        "  en: |",
        "    ",
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> None:
    cfg = load_config()
    photos_root = Path(cfg["photos_root"])
    offset = cfg.get("default_utc_offset", 0)
    sports = set(cfg.get("komoot_sports", ["hike"]))

    existing = existing_hikes()
    ignore = load_yaml(CONTENT.parent / "ignore.yaml") if (CONTENT.parent / "ignore.yaml").exists() else {}
    used_tours = {int(t) for h in existing.values() for t in (h.get("komoot") or [])}
    used_tours |= {int(t) for t in (ignore.get("komoot") or [])}
    folder_owner = {h.get("photos_folder"): slug for slug, h in existing.items() if h.get("photos_folder")}
    ignored_folders = set(ignore.get("folders") or [])
    used_videos = {v for h in existing.values() for v in (h.get("youtube") or [])}

    folders = list_folders(photos_root, cfg)
    tours = [t for t in read_json(CACHE / "komoot" / "tours.json", []) if t["sport"] in sports]

    # 1. Групуем новыя туры па тэчках.
    groups: dict[str, dict] = {}
    for t in tours:
        if int(t["id"]) in used_tours:
            continue
        t["local"] = tour_local_datetime(t["date"], offset)
        if t["local"].year < 2000:
            print(f"  ! тур {t['id']} «{t['name']}» без даты (імпартаваны GPX?) — прапушчаны")
            continue
        folder = pick_folder(t["local"].date(), folders)
        if folder and folder["name"] in folder_owner:
            print(f"  ! тур {t['id']} ({t['local'].date()}, {t['name']}) адносіцца да тэчкі {folder['name']}, "
                  f"якая ўжо ёсць у {folder_owner[folder['name']]}.yaml — дадайце яго ў komoot: уручную")
            continue
        key = folder["name"] if folder else f"day-{t['local'].date()}"
        g = groups.setdefault(key, {"folder": folder["name"] if folder else None,
                                    "start": folder["start"] if folder else t["local"].date(),
                                    "end": folder["end"] if folder else t["local"].date(),
                                    "title": (folder and folder["title"]) or t["name"],
                                    "tours": []})
        g["tours"].append(t)

    # 2. Тэчкі з «Сайт» без тураў — паходы без трэку.
    for f in folders:
        if f["has_site"] and f["name"] not in groups and f["name"] not in folder_owner and f["name"] not in ignored_folders:
            groups[f["name"]] = {"folder": f["name"], "start": f["start"], "end": f["end"],
                                 "title": f["title"] or f["name"], "tours": []}

    if not groups:
        print("Новых паходаў не знойдзена.")
        return

    # 3. Падбіраем відэа: дата ў назве > супадзенне назваў месцаў.
    videos = [v for v in read_json(CACHE / "youtube.json", []) if v["id"] not in used_videos]
    for g in groups.values():
        g["videos"] = []
        g["keys"] = keywords(g["title"], *[t["name"] for t in g["tours"]])
    for v in videos:
        upload = datetime.strptime(v["upload_date"], "%Y%m%d").date() if v.get("upload_date") else None
        best, best_score = None, 0
        for g in groups.values():
            score = 0
            if any(g["start"] <= d <= g["end"] for d in dates_in_title(v["title"])):
                score = 100
            elif g["keys"] & keywords(v["title"]) and upload and \
                    g["start"] <= upload <= g["end"] + timedelta(days=VIDEO_WINDOW_DAYS):
                score = 50 - (upload - g["end"]).days / VIDEO_WINDOW_DAYS  # бліжэйшы па часе
            if score > best_score:
                best, best_score = g, score
        if best:
            best["videos"].append(v)

    # 4. Рэгіён па стартавай кропцы і запіс файлаў.
    CONTENT.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    for g in sorted(groups.values(), key=lambda g: g["start"]):
        g["tours"].sort(key=lambda t: t["local"])
        if (not g["title"] or GENERIC_NAME_RE.match(g["title"].strip())) and g["tours"]:
            sp = g["tours"][0].get("start_point") or {}
            if sp:
                g["title"] = place_name(sp["lat"], sp["lng"]) or g["title"]
        g["title"] = g["title"][:1].upper() + g["title"][1:]
        g["region"] = {"be": "", "en": ""}
        if g["tours"] and g["tours"][0].get("start_point"):
            sp = g["tours"][0]["start_point"]
            g["region"] = region_for_point(sp["lat"], sp["lng"])
        elif g["folder"]:
            g["location"] = photo_location(photos_root / g["folder"], cfg)
            if g["location"]:
                g["region"] = region_for_point(*g["location"])
        slug = hike_slug(g["start"], g["title"])
        path = CONTENT / f"{slug}.yaml"
        if path.exists():
            slug = f"{slug}-{g['tours'][0]['id']}" if g["tours"] else slug + "-2"
            path = CONTENT / f"{slug}.yaml"
        path.write_text(render_yaml(g, today), encoding="utf-8", newline="\n")
        print(f"  + {path.name}  (дзён з трэкам: {len(g['tours'])}, відэа: {len(g['videos'])})")
    print(f"Створана чарнавікоў: {len(groups)} → content/hikes/")


if __name__ == "__main__":
    main()

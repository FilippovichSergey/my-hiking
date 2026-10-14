"""hike status — чаго не хапае ў паходах: што дапісаць у файлы і якую каманду запусціць.

  hike status          табліца па ўсіх паходах, зводка і што зрабіць
  hike status кабан    тое ж і падрабязнасці для паходаў, у назве або імені файла якіх ёсць «кабан»

Нічога не мяняе і не патрабуе сеткі: чытае content/hikes, content/places.yaml, апублікаваны індэкс сайта і кэш.
Што спампаваць, вызначаецца па тых жа правілах, што ў `hike fetch`; што сабраць — па подпісе ўваходных даных,
які `hike build` запісвае ў індэкс для кожнага паходу.
"""
from __future__ import annotations

import argparse
import contextlib
import io
from datetime import datetime

import build
import fetch
import komoot
import practical
from common import CACHE, CONTENT, DOCS, iso_date, load_config, load_yaml, photo_name, read_json, read_version
from geo import place_label

LANGS = ("be", "ru", "en")
LANG_WORD = {"be": "па-беларуску", "ru": "па-руску", "en": "па-англійску"}
DIFFICULTIES = {"easy", "medium", "hard", "expert"}
NOTE_LABELS = {
    "parking": "дзе пакінуў машыну", "surface": "пакрыццё сцежкі", "marking": "маркіроўка", "obstacles": "перашкоды",
    "signal": "мабільная сувязь", "dangers": "небяспекі", "gear": "рыштунак", "season": "найлепшы сезон",
    "overnight": "дзе стаць з палаткай", "after": "пасля паходу: ежа, крама, начлег, кошт", "suitable": "каму падыходзіць",
}
# Палі блока practical: (шлях, што гэта). Пустое поле на сайце значыць «няма звестак», таму запаўняць усе не абавязкова.
PRACTICAL = (
    ("water.taken", "вада: узята на старце"), ("water.left", "вада: засталося на фінішы"), ("water.drunk", "вада: выпіта"),
    ("water.refilled", "вада: даліта на маршруце"), ("water.available", "ці ёсць вада на маршруце"),
    ("water.sources", "крыніцы вады"), ("getting_there", "як дабіраўся і колькі каштавала"), ("places", "асаблівыя месцы"),
    ("weather_notes", "надвор'е, якога не бачыць мадэль"),
) + tuple((key, NOTE_LABELS[key]) for key in practical.NOTE_KEYS)


PHOTOS_CHANGED = "фота на сайце не тыя, што ў тэчцы: ёсць новыя, змененыя, выдаленыя або зборка не скончылася"


def filled(value) -> bool:
    """Ці ёсць у полі звесткі. Пусты радок, спіс або запіс без значэнняў — няма; `false` і 0 — гэта адказ."""
    if isinstance(value, dict):
        return any(filled(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(filled(v) for v in value)
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def texts(value) -> dict:
    """Тэкст па мовах — толькі запоўненыя; просты радок — гэта беларускі тэкст."""
    if isinstance(value, dict):
        return {lang: value[lang].strip() for lang in LANGS if isinstance(value.get(lang), str) and value[lang].strip()}
    return {"be": value.strip()} if isinstance(value, str) and value.strip() else {}


def practical_gaps(block) -> list[tuple]:
    """Незапоўненыя палі блока practical: [(шлях, што гэта)]."""
    def at(path: str):
        value = block
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        return value

    return [(path, label) for path, label in PRACTICAL if not filled(at(path))]


def services_todo(slug: str, need: dict) -> list[tuple]:
    """Чаго з даных сэрвісаў няма ў кэшы — тое, што спампуе `hike fetch` (спіс з fetch.needs)."""
    missing = lambda items: any(not item[-1] for item in items)  # noqa: E731
    found = [("няма надвор'я або яно няпоўнае", missing(need["weather"])),
             ("няма падказак OpenStreetMap", bool(need["osm"]) and not need["osm"][1]),
             ("няма дарогі на машыне", missing(need["routes"])),
             ("няма назваў населеных пунктаў", missing(need["names"])),
             ("няма курсу лары", missing(need["rates"])),
             ("няма звычайнага надвор'я па месяцах або яно няпоўнае", missing(need["climate"]))]
    return [("fetch", what, slug) for what, wanted in found if wanted]


def hike_status(slug: str, h: dict, site: dict | None, shared: dict) -> dict:
    """Стан аднаго паходу: што запоўнена ў файле, што ёсць на сайце і што засталося зрабіць."""
    title, story = texts(h.get("title")), texts(h.get("impressions"))
    hidden = bool(h.get("hidden"))
    published = None if hidden else site
    photos = (published or {}).get("photos") or []
    status = {
        "slug": slug, "hidden": hidden, "title": next(iter(title.values()), slug),
        "date": iso_date(h["date"]) if h.get("date") else None, "names": {lang: lang in title for lang in LANGS},
        "difficulty": str(h.get("difficulty") or "").strip().lower() in DIFFICULTIES,
        "story": {lang: lang in story for lang in LANGS},
        "gaps": practical_gaps(h.get("practical")),
        "photos": len(photos) if published else None, "placed": sum(1 for p in photos if p.get("pos")),
        "videos": len(h["youtube"]) if isinstance(h.get("youtube"), list) else 0,
        "track": isinstance(h.get("komoot"), list) and bool(h["komoot"]),
        "todo": [], "private": False, "unnamed": [],
    }
    status["date"] = status["date"] or (site or {}).get("date") or slug[:10]
    if hidden:
        return status
    cfg = shared["cfg"]
    with contextlib.redirect_stdout(io.StringIO()):  # папярэджанні пра туры і даты паказвае `hike build`
        tours = build.unique_tours(slug, h["komoot"] if isinstance(h.get("komoot"), list) else [])
        missing, empty, kept = build.tour_state(tours, site)
        # `hike fetch` бярэ дні тых тураў, якія ёсць у кэшы; `hike build`, калі хоць аднаго тура няма і застаецца
        # ранейшы трэк, пунктаў трэку не бярэ зусім. Таму для спампоўкі і для подпісу зборкі дні розныя.
        days = practical.raw_days(tours, cfg.get("default_utc_offset", 0))
        start, _ = build.hike_dates(slug, h.get("date"), h.get("end"), days)
        raw = [] if kept else days
    if missing:
        status["todo"].append(("komoot", "няма тураў у кэшы", f"{slug} ({', '.join(map(str, missing))})"))
    if empty:
        status["todo"].append(("komoot --refresh", "тур у кэшы без каардынат", f"{slug} ({', '.join(map(str, empty))})"))
    # Трэк у кэшы ёсць, але імпарт яго не абнавіў: Komoot аддаў тур без каардынат (застаўся ранейшы трэк) або
    # спіс тураў ужо новы, а сам тур яшчэ стары. Без метаданых тура параўноўваць няма з чым — застаецца метка.
    files = {t: read_json(CACHE / "komoot" / "tours" / f"{t}.json") for t in tours if t not in missing and t not in empty}
    stale = [t for t, cached in files.items() if komoot.needs_fetch(cached, shared["tours"].get(t) or cached)]
    if stale:
        status["todo"].append(("komoot", "тур у кэшы стары: мінулы раз Komoot аддаў яго няпоўным або тур змяніўся",
                               f"{slug} ({', '.join(map(str, stale))})"))
    status["private"] = any(t in shared["tours"] and shared["tours"][t].get("status") != "public" for t in tours)
    status["todo"] += services_todo(slug, fetch.needs(slug, h, days, start, practical.home_point(cfg.get("home")),
                                                      shared["routes"], shared["rates"]))
    if site is None:
        status["todo"].append(("build", "яшчэ не на сайце", slug))
        return status
    if site.get("inputs") != build.inputs_key(slug, h, tours, raw, site if kept else None, shared):
        status["todo"].append(("build", "пасля зборкі змяніліся файл паходу, слоўнік назваў або спампаваныя звесткі", slug))
    if build.photos_changed(slug, h, cfg, site):
        status["todo"].append(("build", PHOTOS_CHANGED, slug))
    for item in [site.get("facts") or {}] + [day.get("facts") or {} for day in site.get("days") or []]:
        for edge in ("start", "finish"):
            was = item.get(edge) or {}
            place = place_label(was.get("placeOsm") or was.get("place"), shared["own"])
            if place and place["be"] == place["en"] and place["be"].isascii() and place["en"] not in status["unnamed"]:
                status["unnamed"].append(place["en"])  # беларускай назвы няма: паказваецца лацінкай
    return status


def photo_gaps(slug: str, h: dict, site: dict) -> list[str]:
    """Фота без месца на карце і прычына — па тым, што вядома з кэша фота."""
    without = [p for p in site.get("photos") or [] if not p.get("pos")]
    if not without or not site.get("lines"):
        return []
    manifest = practical.clock_manifest(read_json(CACHE / "photos" / f"{slug}.json", {}),
                                        practical.photo_clock(slug, h.get("photo_clock")))
    known = {m.get("name"): (key, m) for key, m in manifest.items() if isinstance(m, dict)}
    lines = []
    for photo in without:
        key, m = known.get(photo_name(photo), (None, None))
        if m is None:
            lines.append(f"{photo_name(photo)} — пра фота нічога не вядома: няма кэша фота (падключыце дыск і запусціце hike build)")
            continue
        try:
            shot = datetime.fromisoformat(m["original"]).strftime("%d.%m.%Y %H:%M") if m.get("original") else None
        except (TypeError, ValueError):
            shot = None
        if shot:
            why = f"знята {shot} — не падчас запісу трэку" + (", а GPS далей за 300 м ад трэку" if m.get("gps") else "")
        elif m.get("gps"):
            why = "часу здымкі няма, а GPS далей за 300 м ад трэку"
        else:
            why = "у файле няма ні часу здымкі, ні GPS"
        lines.append(f"{key} — {why}")
    return lines


def details(status: dict, h: dict, site: dict | None) -> list[str]:
    """Падрабязнасці пра адзін паход: што дапісаць у файл і чаму ў фота няма месца на карце."""
    lines = ["", f"{status['slug']}.yaml — {status['title']}"]
    need = [f"назва {LANG_WORD[lang]}" for lang in LANGS if not status["names"][lang]]
    need += [] if status["difficulty"] else ["складанасць (difficulty)"]
    need += ["уражанні (impressions)"] if not any(status["story"].values()) else \
        [f"уражанні {LANG_WORD[lang]}" for lang in LANGS if not status["story"][lang]]
    lines.append("  дапісаць: " + (", ".join(need) if need else "нічога — назвы, складанасць і ўражанні ёсць"))
    gaps = status["gaps"]
    if len(gaps) == len(PRACTICAL):
        lines.append("  практычная частка (practical:) — не запоўнена. Што можна запісаць:")
    else:
        lines.append(f"  практычная частка (practical:) — запоўнена {len(PRACTICAL) - len(gaps)} з {len(PRACTICAL)}. Няма:")
    lines += [f"    {label} ({path})" for path, label in gaps]
    if site and not status["hidden"]:
        without = photo_gaps(status["slug"], h, site)
        if without:
            spans = [f"{day['date']} {day['facts']['start'].get('time')}–{day['facts']['finish'].get('time')}"
                     for day in site.get("days") or [] if (day.get("facts") or {}).get("start")]
            lines.append(f"  фота без месца на карце: {len(without)} з {status['photos']} (запіс трэку: {'; '.join(spans) or 'час невядомы'})")
            lines += [f"    {line}" for line in without]
            lines.append("    калі ў камеры стаялі не тая дата або час — папраўка photo_clock у файле паходу (гл. README)")
        elif status["photos"] and not site.get("lines"):
            lines.append("  фота без месца на карце: усе — у паходу няма трэку")
    return lines


def table(rows: list[dict]) -> list[str]:
    mark = lambda ok: "+" if ok else "–"  # noqa: E731
    width = min(max((len(r["title"]) + 11 * r["hidden"] for r in rows), default=5), 44)
    head = [("паход", width + 12), ("назва", 5), ("скл", 3), ("уражанні", 8), ("практ", 5), ("фота", 4), ("на карце", 8), ("відэа", 5)]
    lines = ["  ".join(f"{name:<{size}}" for name, size in head).rstrip(),
             "  ".join(f"{name:<{size}}" for name, (_, size) in zip(("", "ru en", "", "be ru en", "", "", "", ""), head)).rstrip()]
    for r in rows:
        title = (r["title"] + (" (схаваны)" if r["hidden"] else ""))[:width]
        done = len(PRACTICAL) - len(r["gaps"])
        photos = "?" if r["photos"] is None else (str(r["photos"]) if r["photos"] else "–")
        placed = f"{r['placed']}/{r['photos']}" if r["photos"] else ""
        cells = [f"{r['date']}  {title:<{width}}", f"{mark(r['names']['ru']):<2} {mark(r['names']['en']):<2}", f"{mark(r['difficulty']):<3}",
                 " ".join(f"{mark(r['story'][lang]):<2}" for lang in LANGS), f"{(f'{done}/{len(PRACTICAL)}' if done else '–'):>5}",
                 f"{photos:>4}", f"{placed:>8}", f"{(str(r['videos']) if r['videos'] else '–'):>5}"]
        lines.append("  ".join(cells).rstrip())
    lines.append("")
    lines.append(f"+ ёсць, – няма, ? невядома (паходу няма на сайце). практ — колькі палёў practical запоўнена з {len(PRACTICAL)}; "
                 "на карце — фота з месцам / усе фота.")
    return lines


def summary(rows: list[dict]) -> list[str]:
    shown = [r for r in rows if not r["hidden"]]
    n = len(shown)
    count = lambda test: sum(1 for r in shown if test(r))  # noqa: E731
    photos, placed = sum(r["photos"] or 0 for r in shown), sum(r["placed"] for r in shown)
    items = [
        ("назва па-руску", count(lambda r: r["names"]["ru"]), ""), ("назва па-англійску", count(lambda r: r["names"]["en"]), ""),
        ("складанасць", count(lambda r: r["difficulty"]), ""),
        ("уражанні", count(lambda r: any(r["story"].values())),
         f"па-руску {count(lambda r: r['story']['ru'])}, па-англійску {count(lambda r: r['story']['en'])}"),
        ("практычная частка", count(lambda r: len(r["gaps"]) < len(PRACTICAL)), ""),
        ("фота", count(lambda r: r["photos"]), f"месца на карце ёсць у {placed} фота з {photos}"),
        ("відэа", count(lambda r: r["videos"]), ""), ("трэк", count(lambda r: r["track"]), ""),
    ]
    hidden = len(rows) - n
    lines = ["", f"Зводка: паходаў {n}" + (f" (і схаваных: {hidden})" if hidden else "")]
    lines += [f"  {name:<20} {have:>3} з {n}" + (f"  ({extra})" if extra else "") for name, have, extra in items]
    return lines


def todo(rows: list[dict], orphans: list[str], built: str | None = None) -> list[str]:
    """Што зрабіць, каб сайт адпавядаў файлам: па камандах, у парадку запуску. Пуста — рабіць няма чаго.

    built — версія скрыптоў, якой сабраны сайт (None — індэкса сайта няма).
    """
    found: dict[tuple, list] = {}
    for r in rows:
        for command, what, who in r["todo"]:
            found.setdefault((command, what), []).append(who)
    if orphans:
        found.setdefault(("build", "на сайце ёсць, а файла няма або паход схаваны"), []).extend(orphans)
    order = ("komoot", "komoot --refresh", "fetch", "build")
    everyone = {r["slug"] for r in rows if not r["hidden"]}
    names = lambda who: f"усе паходы ({len(who)})" if len(who) > 3 and set(who) == everyone else ", ".join(who)  # noqa: E731
    lines = [f"  .\\hike {command:<6} — {what}: {names(who)}"
             for (command, what), who in sorted(found.items(), key=lambda x: order.index(x[0][0]))]
    if built is not None and built != read_version():
        lines.append(f"  .\\hike build  — сайт сабраны версіяй {built or 'без нумара'}, а скрыпты цяпер версіі {read_version()}")
    unnamed = sorted({name for r in rows for name in r["unnamed"]})
    if unnamed:
        lines.append(f"  content\\places.yaml — няма беларускай назвы (на сайце лацінкай): {', '.join(unnamed)}")
        lines.append("    дапішыце радкі выгляду   " + f'{unnamed[0]}: "…"')
    private = sum(1 for r in rows if r["private"])
    if private:
        lines.append(f"  Komoot — туры не публічныя, спасылкі «Трэк у Komoot» на сайце няма: паходаў {private}")
    return ["", "Што зрабіць"] + lines if lines else []


def collect(wanted: str = "") -> tuple[list[dict], dict, dict, list[str], str | None]:
    """Стан паходаў, найноўшыя першымі: (радкі, файлы паходаў, паходы на сайце, паходы на сайце без файла,
    версія скрыптоў, якой сабраны сайт).

    wanted — толькі паходы, у назве (на любой мове) або імені файла якіх ёсць гэты тэкст.
    """
    index = read_json(DOCS / "data" / "hikes.json", None)
    site = {h.get("slug"): h for h in (index or {}).get("hikes") or []}
    shared = build.shared_sources(load_config())
    sources = {path.stem: load_yaml(path) for path in sorted(CONTENT.glob("*.yaml"))}
    rows = [hike_status(slug, h, site.get(slug), shared) for slug, h in sources.items()]
    orphans = sorted(slug for slug in site if slug not in sources or sources[slug].get("hidden"))
    wanted = wanted.strip().lower()
    if wanted:
        rows = [r for r in rows if wanted in r["slug"].lower()
                or any(wanted in text.lower() for text in texts(sources[r["slug"]].get("title")).values())]
        orphans = [slug for slug in orphans if wanted in slug.lower()]
    rows.sort(key=lambda r: (r["date"], r["slug"]), reverse=True)
    return rows, sources, site, orphans, (index.get("version") or "") if isinstance(index, dict) else None


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="hike status", description="Чаго не хапае ў паходах. Нічога не мяняе.")
    ap.add_argument("text", nargs="*", help="паказаць падрабязна паходы, у назве або імені файла якіх ёсць гэты тэкст")
    args = ap.parse_args(argv)
    if not CONTENT.is_dir():
        raise SystemExit(f"! Тэчка з апісаннямі паходаў не знойдзена: {CONTENT}.")
    wanted = " ".join(args.text).strip()
    rows, sources, site, orphans, built = collect(wanted)
    if not rows:  # паходаў няма, але на сайце маглі застацца выдаленыя: пра іх кажа «Што зрабіць»
        lines = [f"Паходаў з «{wanted}» у назве або імені файла няма." if wanted else "Паходаў пакуль няма: content/hikes пустая."]
        print("\n".join(lines + todo(rows, orphans, None if wanted else built)))
        return
    lines = table(rows)
    if wanted:  # падрабязнасці замест зводкі: паходаў звычайна адзін-два
        for r in rows:
            lines += details(r, sources[r["slug"]], site.get(r["slug"]))
    else:
        lines += summary(rows)
    print("\n".join(lines + (todo(rows, orphans, built) or ["", "Што зрабіць", "  нічога: усё спампавана і сабрана"])))


if __name__ == "__main__":
    main()

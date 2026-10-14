"""Тое, што `hike build` запісвае ў сам сайт апрача даных: версіі файлаў у index.html і старонкі для спасылак.

Версіі файлаў. GitHub Pages дазваляе браўзеру 10 хвілін трымаць файл у кэшы, а модулі сайта спасылаюцца адзін
на аднаго без версіі: пасля публікацыі браўзер мог узяць новы app.js разам са старым pois.js, і сайт не
адкрываўся. Таму ў index.html запісваецца адзін подпіс усіх файлаў assets/ — у адрасе стыляў і app.js і ў карце
імпартаў (import map) для астатніх модуляў: новы index.html бярэ ўсе файлы па новых адрасах, стары — па старых.

Старонкі для спасылак. Адрас паходу на сайце — усё пасля знака # (#/be/<паход>), а яго не бачаць ні Telegram,
ні іншыя праграмы, якія малююць прэв'ю спасылкі: усе паходы выглядалі аднолькава. Таму для кожнага паходу
і мовы ствараецца маленькая старонка <мова>/<паход>/ з назвай, лічбамі і вокладкай, якая адразу пераводзіць
на сайт. Кнопка «Падзяліцца» капіюе яе адрас.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date
from pathlib import Path

from PIL import Image

LANGS = ("be", "ru", "en")
FALLBACK = {"be": ("be", "ru", "en"), "ru": ("ru", "be", "en"), "en": ("en", "be", "ru")}  # як у i18n.js
OG_SIZE = (1200, 630)  # звыклы памер карцінкі для прэв'ю спасылкі
TEXT = {
    "be": {"site": "Мае паходы", "about": "Карта маіх хайкінгаў: трэкі, фота, відэа і ўражанні.", "km": "км", "m": "м",
           "locale": "be_BY", "open": "Адкрыць паход на карце",
           "months": ("студзеня", "лютага", "сакавіка", "красавіка", "траўня", "чэрвеня", "ліпеня", "жніўня", "верасня",
                      "кастрычніка", "лістапада", "снежня"),
           "days": ("дзень", "дні", "дзён"),
           "difficulty": {"easy": "Лёгка", "medium": "Сярэдне", "hard": "Цяжка", "expert": "Вельмі цяжка"}},
    "ru": {"site": "Мои походы", "about": "Карта моих хайкингов: треки, фото, видео и впечатления.", "km": "км", "m": "м",
           "locale": "ru_RU", "open": "Открыть поход на карте",
           "months": ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
                      "ноября", "декабря"),
           "days": ("день", "дня", "дней"),
           "difficulty": {"easy": "Легко", "medium": "Средне", "hard": "Тяжело", "expert": "Очень тяжело"}},
    "en": {"site": "My hikes", "about": "A map of my hikes: tracks, photos, videos and impressions.", "km": "km", "m": "m",
           "locale": "en_US", "open": "Open the hike on the map",
           "months": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                      "November", "December"),
           "days": ("day", "days", "days"),
           "difficulty": {"easy": "Easy", "medium": "Moderate", "hard": "Hard", "expert": "Expert"}},
}
START, END = "<!-- hike build: пачатак — гэты блок запісвае `hike build` -->", "<!-- hike build: канец -->"


# --- Тэксты -------------------------------------------------------------------------

def pick(pair: dict | None, lang: str) -> str:
    """Тэкст на мове старонкі, а калі яго няма — на блізкай мове (тое ж правіла, што на сайце)."""
    return next((pair[code] for code in FALLBACK[lang] if (pair or {}).get(code)), "")


def num(value: float, lang: str, digits: int = 0) -> str:
    """Лік як на сайце: «1 007» і «21,5» па-беларуску і па-руску, «1,007» і «21.5» па-англійску."""
    text = f"{value:,.{digits}f}"
    if digits:
        text = text.rstrip("0").rstrip(".")
    return text if lang == "en" else text.replace(",", " ").replace(".", ",")


def plural(n: int, forms: tuple, lang: str) -> str:
    if lang == "en":
        return forms[0] if n == 1 else forms[1]
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    return forms[1] if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else forms[2]


def date_range(start: str, end: str | None, lang: str) -> str:
    a, b, months = date.fromisoformat(start), date.fromisoformat(end or start), TEXT[lang]["months"]
    if a == b:
        return f"{a.day} {months[a.month - 1]} {a.year}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day} {months[a.month - 1]} {a.year}"
    if a.year == b.year:
        return f"{a.day} {months[a.month - 1]} – {b.day} {months[b.month - 1]} {a.year}"
    return f"{a.day} {months[a.month - 1]} {a.year} – {b.day} {months[b.month - 1]} {b.year}"


def summary(hike: dict, lang: str) -> str:
    """Радок пра паход для прэв'ю: дата, рэгіён, адлегласць, набор вышыні, колькасць дзён, складанасць."""
    words, parts = TEXT[lang], [date_range(hike["date"], hike.get("end"), lang), pick(hike.get("region"), lang)]
    stats = hike.get("stats")
    if stats:
        parts += [f"{num(stats['distance'], lang, 1)} {words['km']}", f"↑ {num(stats['up'], lang)} {words['m']}"]
    days = len(hike.get("days") or [])
    if days > 1:
        parts.append(f"{days} {plural(days, words['days'], lang)}")
    parts.append(words["difficulty"].get(hike.get("difficulty"), ""))
    return " · ".join(p for p in parts if p)


# --- Версіі файлаў у index.html -------------------------------------------------------

def assets_version(docs: Path) -> str:
    """Подпіс усіх файлаў docs/assets: мяняецца, калі змяніўся хоць адзін з іх."""
    digest = hashlib.sha1()
    for path in sorted((docs / "assets").iterdir()):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


def file_version(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:10]


def _meta(key: str, value: str, prop: bool = True) -> str:
    return f'<meta {"property" if prop else "name"}="{key}" content="{html.escape(value, quote=True)}">'


def stamp_index(docs: Path, site: dict | None = None) -> bool:
    """Запісвае ў docs/index.html версіі файлаў і (калі зададзены адрас сайта) прэв'ю спасылкі на сам сайт.

    site: {url, image?} — адрас сайта і карцінка для прэв'ю. Вяртае False, калі index.html няма або ён мае
    нечаканы выгляд: тады файл не змяняецца.
    """
    index = docs / "index.html"
    if not index.is_file() or not (docs / "assets").is_dir():
        return False
    text = index.read_text(encoding="utf-8")
    version = assets_version(docs)
    style = re.compile(r'(<link rel="stylesheet" href="assets/style\.css)(?:\?v=[0-9a-f]+)?(")')
    script = re.compile(r'^([ \t]*)(<script type="module" src="assets/app\.js)(?:\?v=[0-9a-f]+)?("></script>)[ \t]*$', re.M)
    if len(style.findall(text)) != 1 or len(script.findall(text)) != 1:
        print("  ! docs/index.html: не знойдзены радкі з assets/style.css і assets/app.js — версіі файлаў не запісаныя")
        return False
    modules = sorted(p.name for p in (docs / "assets").glob("*.js"))
    lines = [START, _meta("site-version", version, prop=False)]
    data = docs / "data" / "hikes.json"
    if data.is_file():
        lines.append(_meta("hikes-data", f"data/hikes.json?v={file_version(data)}", prop=False))
    if site:
        words = TEXT["be"]
        lines += [_meta("og:type", "website"), _meta("og:site_name", words["site"]), _meta("og:title", words["site"]),
                  _meta("og:description", words["about"]), _meta("og:url", site["url"]),
                  _meta("og:locale", words["locale"])]
        lines += [_meta("og:image", site["image"]), _meta("twitter:card", "summary_large_image", prop=False)] \
            if site.get("image") else [_meta("twitter:card", "summary", prop=False)]
    imports = {f"./assets/{name}": f"./assets/{name}?v={version}" for name in modules}
    lines += [f'<script type="importmap">{json.dumps({"imports": imports}, separators=(",", ":"))}</script>', END]

    text = style.sub(rf"\g<1>?v={version}\2", text)
    block = re.compile(r"^[ \t]*" + re.escape(START) + r".*?" + re.escape(END) + r"[ \t]*\n", re.S | re.M)
    text = block.sub("", text)  # ранейшы блок прыбіраецца, новы ставіцца перад app.js: карта імпартаў павінна быць раней
    indent = script.search(text)[1]
    text = script.sub(lambda m: "".join(f"{indent}{line}\n" for line in lines) + f"{m[1]}{m[2]}?v={version}{m[3]}", text)
    if text != index.read_text(encoding="utf-8"):
        index.write_text(text, encoding="utf-8", newline="\n")
    return True


# --- Старонкі для спасылак -------------------------------------------------------------

def og_image(docs: Path, slug: str, photo: dict | None) -> str | None:
    """Карцінка прэв'ю з вокладкі паходу: JPEG 1200×630 у docs/og (WebP разумеюць не ўсе праграмы).

    Вяртае шлях ад кораня сайта або None, калі вокладкі няма. Імя змяшчае подпіс вокладкі: новая вокладка —
    новы адрас, і праграмы не паказваюць старую карцінку з кэша.
    """
    source = docs / photo["src"] if photo else None
    if not source or not source.is_file():
        return None
    name = f"{slug}-{hashlib.sha1(source.read_bytes() + repr(OG_SIZE).encode()).hexdigest()[:8]}.jpg"
    target = docs / "og" / name
    if not target.is_file():
        with Image.open(source) as raw:
            img = raw.convert("RGB")
        ratio = OG_SIZE[0] / OG_SIZE[1]
        width, height = img.size
        if width / height > ratio:  # занадта шырокая: абразаюцца бакі
            crop = round(height * ratio)
            img = img.crop(((width - crop) // 2, 0, (width - crop) // 2 + crop, height))
        else:                       # занадта высокая: абразаюцца верх і ніз
            crop = round(width / ratio)
            img = img.crop((0, (height - crop) // 2, width, (height - crop) // 2 + crop))
        if img.width > OG_SIZE[0]:
            img = img.resize(OG_SIZE, Image.LANCZOS)
        target.parent.mkdir(parents=True, exist_ok=True)
        img.save(target, "JPEG", quality=82, optimize=True, progressive=True)
    return f"og/{name}"


def _page(lang: str, title: str, about: str, url: str, target: str, image: tuple | None, kind: str) -> str:
    """HTML старонкі для спасылкі: звесткі для прэв'ю і пераход на сайт (target — адносны адрас на сайце)."""
    words, esc = TEXT[lang], lambda value: html.escape(value, quote=True)
    head = [
        '<meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{esc(title if title == words['site'] else f'{title} · ' + words['site'])}</title>",
        _meta("description", about, prop=False), _meta("og:type", kind), _meta("og:site_name", words["site"]),
        _meta("og:title", title), _meta("og:description", about), _meta("og:url", url), _meta("og:locale", words["locale"]),
    ]
    if image:
        head += [_meta("og:image", image[0]), _meta("og:image:width", str(image[1])), _meta("og:image:height", str(image[2])),
                 _meta("twitter:card", "summary_large_image", prop=False)]
    else:
        head.append(_meta("twitter:card", "summary", prop=False))
    head += [f'<meta http-equiv="refresh" content="0; url={esc(target)}">',
             f"<script>location.replace({json.dumps(target)});</script>"]
    return (f'<!doctype html>\n<html lang="{lang}">\n<head>\n' + "".join(f"  {line}\n" for line in head) + "</head>\n<body>\n"
            f'  <p><a href="{esc(target)}">{esc(title)}</a> — {esc(words["open"].lower())}</p>\n</body>\n</html>\n')


def share_pages(docs: Path, hikes: list[dict], site_url: str | None) -> tuple[dict | None, list[Path]]:
    """Старонкі <мова>/<паход>/index.html і <мова>/index.html з прэв'ю спасылак.

    Вяртае ({url, image} для прэв'ю самога сайта або None, калі адрас сайта не зададзены, файлы і тэчкі, якія
    сталі лішнімі). Без адраса сайта старонкі не ствараюцца, а раней створаныя лічацца лішнімі: у прэв'ю
    патрэбны поўныя адрасы.
    """
    base = site_url.strip() if isinstance(site_url, str) else ""
    if base and not re.fullmatch(r"https?://\S+", base):
        print(f"  ! site_url у config.yaml: чакаецца адрас накшталт https://…/, а не «{site_url}» — старонкі для спасылак не створаныя")
        base = ""
    base = base.rstrip("/") + "/" if base else ""
    keep: set[Path] = set()
    newest = None
    if base:
        for hike in hikes:  # спіс ужо ад новых да старых
            path = og_image(docs, hike["slug"], (hike.get("photos") or [None])[hike.get("cover") or 0] if hike.get("photos") else None)
            image = None
            if path:
                keep.add(docs / path)
                with Image.open(docs / path) as img:
                    image = (base + path, img.width, img.height)
                newest = newest or image
            for lang in LANGS:
                page = docs / lang / hike["slug"] / "index.html"
                text = _page(lang, pick(hike.get("title"), lang) or hike["slug"], summary(hike, lang), f"{base}{lang}/{hike['slug']}/",
                             f"../../#/{lang}/{hike['slug']}", image, "article")
                _write(page, text)
                keep.add(page.parent)
        for lang in LANGS:
            page = docs / lang / "index.html"
            _write(page, _page(lang, TEXT[lang]["site"], TEXT[lang]["about"], f"{base}{lang}/", f"../#/{lang}", newest, "website"))
            keep.add(page)
    if not base:  # старонак больш няма — прыбіраюцца і самі тэчкі
        return None, [p for p in [*(docs / lang for lang in LANGS), docs / "og"] if p.exists()]
    stale = [p for lang in LANGS if (docs / lang).is_dir() for p in (docs / lang).iterdir() if p not in keep]
    stale += [p for p in (docs / "og").iterdir() if p not in keep] if (docs / "og").is_dir() else []
    return {"url": base, "image": newest[0] if newest else None}, stale


def _write(path: Path, text: str) -> None:
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

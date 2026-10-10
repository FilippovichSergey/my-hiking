"""Агульныя шляхі, налады і дапаможныя функцыі для ўсіх скрыптоў."""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
CONTENT = ROOT / "content" / "hikes"
DOCS = ROOT / "docs"


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def site_photos_dir(folder: Path, cfg: dict) -> Path | None:
    """Падтэчка з фота для сайта ўнутры тэчкі паходу (без уліку рэгістра)."""
    if not folder.is_dir():
        return None
    subdirs = {p.name.lower(): p for p in folder.iterdir() if p.is_dir()}
    for name in cfg.get("site_photos_subfolders") or ["Сайт"]:
        if name.lower() in subdirs:
            return subdirs[name.lower()]
    return None


def read_version() -> str:
    """Версія праекта з файла VERSION (паказваецца ў падвале сайта); пусты радок, калі файла няма."""
    path = ROOT / "VERSION"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data, compact: bool = False) -> None:
    """Запіс праз часовы файл і os.replace: перапынены запіс не пакідае паўфайла."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
    os.replace(tmp, path)


def load_yaml(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, ValueError) as exc:
        # ValueError дае, напрыклад, неісная дата без двукосся (end: 2025-02-31)
        reason = " ".join(str(exc).split())
        raise SystemExit(f"! {path.name}: памылка ў YAML — {reason}. Выпраўце файл і запусціце зноў.") from None
    if not isinstance(data, dict):
        raise SystemExit(f"! {path.name}: чакаецца спіс палёў «ключ: значэнне». Выпраўце файл і запусціце зноў.")
    return data


def has_track(coords) -> bool:
    """Ці ёсць у туры трэк: хаця б два пункты з каардынатамі."""
    return sum(1 for c in coords or [] if c and c[0] is not None and c[1] is not None) >= 2


def iso_date(value) -> str | None:
    """Дата з YAML (аб'ект date або радок ГГГГ-ММ-ДД) → ISO-радок; None, калі такога дня не існуе."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        return None


# --- Дата тура ---------------------------------------------------------------

def tour_local_datetime(iso: str, default_offset_h: float) -> datetime:
    """Komoot аддае дату ў ISO; калі пояс UTC, пераводзім у лакальны час паходу."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() == timedelta(0):
        dt = dt.replace(tzinfo=dt.tzinfo or timezone.utc)
        dt = dt.astimezone(timezone(timedelta(hours=default_offset_h)))
    return dt


# --- Тэчкі з фота ------------------------------------------------------------

FOLDER_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:-(\d{2})(?:(\d{2}))?)?[ _]*(.*)$")


def parse_folder(name: str):
    """'20230902-04_Казбегі' -> (date(2023,9,2), date(2023,9,4), 'Казбегі').

    Таксама разумее канец у іншым месяцы ('20231130-1202_...', ММДД) і праз Новы год
    ('20251231-0102_...'). Канец ДД, меншы за дзень пачатку, — гэта наступны месяц
    ('20230930-02' → 2 кастрычніка). Вяртае None, калі назва не пачынаецца з поўнай даты.
    """
    m = FOLDER_RE.match(name)
    if not m:
        return None
    y, mo, d, end_a, end_b, title = m.groups()
    try:
        start = date(int(y), int(mo), int(d))
        if end_a and end_b:  # ММДД; канец раней за пачатак — паход праз Новы год
            end_md = (int(end_a), int(end_b))
            end = date(int(y) + (end_md < (start.month, start.day)), *end_md)
        elif end_a:
            end = date(int(y), int(mo), int(end_a)) if int(end_a) >= start.day else                 (date(start.year + start.month // 12, start.month % 12 + 1, int(end_a)))
        else:
            end = start
    except ValueError:
        return None
    return start, end, title.replace("_", " ").strip()


# --- Транслітарацыя для адрасоў (slug) ------------------------------------------

_TR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "і": "i", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ў": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y",
    "ь": "", "э": "e", "ю": "yu", "я": "ya", "'": "", "’": "", "ʼ": "",
}


def slugify(text: str) -> str:
    out = "".join(_TR.get(ch, ch) for ch in text.lower())
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out or "hike"


def hike_slug(start: date, title: str) -> str:
    return f"{start.isoformat()}-{slugify(title)}" if title else start.isoformat()

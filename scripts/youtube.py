"""Абнаўляе спіс відэа YouTube-канала ў cache/youtube.json (без API-ключа, праз yt-dlp).

Запуск:  hike youtube
"""
from __future__ import annotations

import sys

from yt_dlp import YoutubeDL

from common import CACHE, load_config, read_json, write_json

YT_PATH = CACHE / "youtube.json"


def main(argv=None) -> None:
    cfg = load_config()
    channel = cfg["youtube_channel"].rstrip("/") + "/videos"
    known = {v["id"]: v for v in read_json(YT_PATH, [])}

    try:
        with YoutubeDL({"extract_flat": True, "quiet": True, "skip_download": True}) as ydl:
            listing = ydl.extract_info(channel, download=False)
    except Exception as exc:  # сетка, змены на YouTube: кэш не кранаецца
        raise SystemExit(f"! Спіс відэа канала не атрыманы — {exc}. cache/youtube.json не змяняўся.") from None
    entries = [e for e in (listing or {}).get("entries") or [] if e and e.get("id")]

    # Дата публікацыі ёсць толькі ў поўнай інфармацыі пра відэа: дапытваем толькі новыя.
    new_ids = [e["id"] for e in entries if not known.get(e["id"], {}).get("upload_date")]
    failed = 0
    if new_ids:
        with YoutubeDL({"quiet": True, "skip_download": True, "no_warnings": True}) as ydl:
            for vid in new_ids:
                try:
                    info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
                except Exception as exc:  # сэрвіс не адказаў або відэа пакуль недаступнае (прэм'ера, прыватнае)
                    failed += 1
                    print(f"  ! {vid}: {exc}", file=sys.stderr)
                    continue
                known[vid] = {
                    "id": vid,
                    "title": info.get("title"),
                    "upload_date": info.get("upload_date"),
                    "duration": info.get("duration"),
                }
                print(f"  + {info.get('upload_date')}  {info.get('title')}")

    for e in entries:  # назвы маглі змяніцца
        if e["id"] in known:
            known[e["id"]]["title"] = e.get("title") or known[e["id"]]["title"]
    videos = sorted(known.values(), key=lambda v: v.get("upload_date") or "", reverse=True)
    write_json(YT_PATH, videos)
    print(f"Відэа на канале: {len(videos)}" + (f". Не атрымана: {failed}" if failed else "") + " → cache/youtube.json")
    if failed:
        # Атрыманае захавана. Код выхаду 1 — каб скрыпт або планавальнік бачыў, што імпарт не скончаны і `hike youtube`
        # трэба паўтарыць: чарнавік, створаны `hike new` да гэтага, відэа без даты не атрымае.
        raise SystemExit(1)


if __name__ == "__main__":
    main()

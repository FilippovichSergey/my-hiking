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

    with YoutubeDL({"extract_flat": True, "quiet": True, "skip_download": True}) as ydl:
        listing = ydl.extract_info(channel, download=False)
    entries = [e for e in listing.get("entries", []) if e and e.get("id")]

    # Дата публікацыі ёсць толькі ў поўнай інфармацыі пра відэа: дапытваем толькі новыя.
    new_ids = [e["id"] for e in entries if not known.get(e["id"], {}).get("upload_date")]
    if new_ids:
        with YoutubeDL({"quiet": True, "skip_download": True, "no_warnings": True}) as ydl:
            for vid in new_ids:
                try:
                    info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
                except Exception as exc:  # відэа можа быць недаступным
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
    print(f"Відэа на канале: {len(videos)} → cache/youtube.json")


if __name__ == "__main__":
    main()

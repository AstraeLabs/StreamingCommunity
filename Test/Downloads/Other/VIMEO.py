# 25.07.26
# ruff: noqa: E402

import os
import sys
import json
import argparse

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.append(src_path)

import httpx
from rich.console import Console

from VibraVid.utils import config_manager, setup_logger
from VibraVid.core.downloader import Generic_Downloader
from VibraVid.cli.command.limits import add_limit_arguments, apply_limits


setup_logger()
conf_extension = config_manager.config.get("PROCESS", "extension")
console = Console()

OUTPUT_PATH = rf".\Video\Vimeo.{conf_extension}"

HEADERS = {
    "accept": "*/*",
    "origin": "https://player.vimeo.com",
    "referer": "https://player.vimeo.com/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0"
    ),
}


def build_manifest(playlist_url: str) -> dict:
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        resp = client.get(playlist_url)
        if resp.status_code != 200:
            raise RuntimeError(
                f"playlist.json -> HTTP {resp.status_code}: expired URL or invalid signature, regenerate it from the player."
            )
        data = resp.json()

    # base_url is relative to the playlist URL
    playlist_dir = playlist_url.split("?")[0].rsplit("/", 1)[0] + "/"
    base = httpx.URL(playlist_dir).join(data.get("base_url", ""))

    videos, audios = data.get("video", []), data.get("audio", [])
    if not videos:
        raise RuntimeError("No video track found in the playlist.")

    def to_track(t: dict, kind: str) -> dict:
        track = {
            "type": kind,
            "id": t["id"],
            "codecs": t.get("codecs", ""),
            "bitrate": t.get("bitrate", 0),
            "duration": t.get("duration", 0),
            "init": {"data": t["init_segment"]},
            "segments": {
                "list": [
                    {
                        "url": str(base.join(s["url"])),
                        "size": s.get("size", 0),
                        "duration": round(s["end"] - s["start"], 3),
                    }
                    for s in t["segments"]
                ]
            },
        }
        if kind == "video":
            track.update(width=t.get("width", 0), height=t.get("height", 0), fps=str(t.get("framerate", "")))
        else:
            track.update(language=t.get("language") or "und", channels=str(t.get("channels", "")))
        return track

    tracks = [to_track(t, "video") for t in sorted(videos, key=lambda t: t.get("bitrate", 0), reverse=True)]
    tracks += [to_track(t, "audio") for t in sorted(audios, key=lambda t: t.get("bitrate", 0), reverse=True)]

    return {
        "vibravid_manifest": 1,
        "source": "vimeo",
        "clip_id": data.get("clip_id", ""),
        "duration": max((t.get("duration", 0) for t in videos), default=0),
        "tracks": tracks,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Download a Vimeo clip from its playlist.json")
    ap.add_argument("url", help="Full URL of the playlist.json (signed, expires)")
    ap.add_argument("--write-only", metavar="PATH", help="write only the custom manifest, without downloading")
    add_limit_arguments(ap)
    args = ap.parse_args()

    # Same channel the main CLI uses: the downloader reads these off context_tracker.
    apply_limits(args)

    try:
        manifest = build_manifest(args.url)
    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)

    if args.write_only:
        with open(args.write_only, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        console.print(f"[green]custom manifest written:[/green] {args.write_only}")
        return

    generic = Generic_Downloader(
        sources=[
            {
                "url": args.url,
                "protocol": "custom",
                "headers": HEADERS,
                "manifest_content": json.dumps(manifest),
            }
        ],
        output_path=OUTPUT_PATH,
    )

    out_path, need_stop, error = generic.start()
    if error:
        console.print(f"[red]Output path: {out_path}, Need stop: {need_stop}, error: {error}[/red]")
    else:
        console.print(f"[green]Output path: {out_path}, Need stop: {need_stop}, error: {error}[/green]")


if __name__ == "__main__":
    main()

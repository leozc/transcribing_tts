#!/usr/bin/env python
"""Rebuild the test-set audio from manifest.json (reproducible).

Downloads each YouTube item via yt-dlp and writes a 16 kHz mono WAV to its
`audio` path. Skips items that already exist and non-YouTube items (e.g. the
private Google Drive sample).

    python benchmark/testset/build_testset.py [--force] [--only id1,id2]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("manifest.json")


def _clip_args(clip: str) -> list[str]:
    if not clip or clip == "full":
        return []
    start, _, end = clip.partition("-")
    args = []
    if start:
        args += ["-ss", start]
    if end:
        args += ["-to", end]
    return args


def build_item(item: dict, force: bool) -> str:
    iid = item["id"]
    if item["source_type"] != "youtube":
        return f"SKIP {iid} (source_type={item['source_type']}, not rebuildable)"
    out = ROOT / item["audio"]
    if out.exists() and not force:
        return f"OK   {iid} (exists: {out.name})"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".src")
    subprocess.run(
        [sys.executable, "-m", "yt_dlp", "-f", "140/bestaudio/best", "--no-playlist",
         "-o", str(tmp), item["url"]],
        check=True, capture_output=True,
    )
    src = next(tmp.parent.glob(tmp.name + "*"), tmp)
    cmd = ["ffmpeg", "-y", *_clip_args(item.get("clip", "full")),
           "-i", str(src), "-ar", "16000", "-ac", "1", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
    src.unlink(missing_ok=True)
    return f"BUILT {iid} -> {out.name}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="rebuild even if audio exists")
    ap.add_argument("--only", help="comma-separated item ids to build")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text())
    only = set(args.only.split(",")) if args.only else None
    for item in manifest["items"]:
        if only and item["id"] not in only:
            continue
        try:
            print(build_item(item, args.force), flush=True)
        except subprocess.CalledProcessError as e:
            print(f"FAIL {item['id']}: {e.stderr.decode()[-300:] if e.stderr else e}", flush=True)


if __name__ == "__main__":
    main()

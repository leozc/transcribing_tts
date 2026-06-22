"""tts-serve CLI — transcribe a file / YouTube / Google Drive / S3 source.

Examples:
    tts-serve transcribe meeting.mp3
    tts-serve transcribe 'https://youtu.be/3Amlu4y94Ho' --hotwords "Chamath,Sacks"
    tts-serve transcribe s3://bucket/call.m4a --aws-profile work --out ./out
    tts-serve transcribe 'https://drive.google.com/file/d/<ID>/view' \
        --gdrive-credentials sa.json
    tts-serve transcribe call.wav --stdout json | jq '.segments[0]'

    tts-serve watch        # resident worker draining data/inbox/
"""
from __future__ import annotations

import os

# Reduce cross-file GPU fragmentation (must precede torch import).
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def cmd_transcribe(args) -> int:
    from tts_serve import core
    from tts_serve.sources import SourceOpts
    from tts_serve.outputs import render_format, write_outputs

    workdir = Path(tempfile.mkdtemp(prefix="tts_serve_"))
    opts = SourceOpts(
        aws_profile=args.aws_profile,
        aws_access_key_id=args.aws_access_key_id,
        aws_secret_access_key=args.aws_secret_access_key,
        aws_region=args.aws_region,
        gdrive_credentials=args.gdrive_credentials,
        gdrive_public=args.gdrive_public,
        cookies=args.cookies,
    )
    doc = core.transcribe_source(
        args.source, workdir=workdir, opts=opts,
        hotwords=args.hotwords, speakers=args.speakers,
        reid=args.reid, names=args.names, clip=args.clip,
        model=args.model, max_new_tokens=args.max_new_tokens, name=args.name,
        progress=lambda st: _eprint(f"[{st}]"),
    )
    names = doc.get("speaker_names")
    if names:
        for spk, info in names.items():
            _eprint(f"  {spk} -> {info['name']} (conf {info['confidence']:.2f})")

    if args.stdout:
        print(render_format(doc, args.stdout))
        _eprint(f"[done] {doc['n_segments']} segments, speakers={doc['speakers']}, "
                f"{doc.get('gen_seconds', 0):.0f}s")
        return 0

    out_dir = Path(args.out) if args.out else Path("out") / f"{doc['meeting_name']}"
    paths = write_outputs(out_dir, doc)
    _eprint(f"[done] {doc['n_segments']} segments, speakers={doc['speakers']}, "
            f"{doc.get('gen_seconds', 0):.0f}s -> {out_dir}")
    for p in paths.values():
        _eprint(f"  {p}")
    return 0


def cmd_watch(args) -> int:
    from tts_serve.pipeline import main as pipeline_main
    sys.argv = ["tts-serve", "--once" if args.once else "--watch"]
    pipeline_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="tts-serve", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    t = sub.add_parser("transcribe", help="transcribe one source")
    t.add_argument("source", help="file path, YouTube/Drive/S3/http URL")
    t.add_argument("--out", help="output directory (default: out/<name>/)")
    t.add_argument("--stdout", choices=["json", "txt", "srt"],
                   help="print to stdout in this format instead of writing files")
    t.add_argument("--name", help="meeting name (default: derived from source)")
    t.add_argument("--hotwords", help="comma-separated names/terms to bias ASR")
    t.add_argument("--speakers", type=int, help="expected speaker count hint")
    t.add_argument("--reid", action="store_true",
                   help="voiceprint speaker re-id post-process (use with --speakers)")
    t.add_argument("--names", action="store_true",
                   help="suggest speaker names from self-intros via LLM (needs DEEPSEEK_API_KEY)")
    t.add_argument("--clip", help="clip START-END seconds, e.g. 0-600 or 30-")
    t.add_argument("--model", default="microsoft/VibeVoice-ASR")
    t.add_argument("--max-new-tokens", type=int, default=16384, dest="max_new_tokens")
    # auth
    t.add_argument("--aws-profile")
    t.add_argument("--aws-access-key-id")
    t.add_argument("--aws-secret-access-key")
    t.add_argument("--aws-region")
    t.add_argument("--gdrive-credentials", help="service-account JSON for private Drive files")
    t.add_argument("--gdrive-public", action="store_true",
                   help="fetch a public 'Anyone with the link' Drive file/folder via gdown")
    t.add_argument("--cookies", help="cookies file for restricted YouTube videos")
    t.set_defaults(func=cmd_transcribe)

    w = sub.add_parser("watch", help="resident worker draining data/inbox/")
    w.add_argument("--once", action="store_true", help="drain once and exit")
    w.set_defaults(func=cmd_watch)
    return ap


def main() -> None:
    args = build_parser().parse_args()
    try:
        sys.exit(args.func(args))
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        _eprint(f"error: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()

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


def _clip_and_normalize(src: Path, dst: Path, clip: str | None) -> None:
    """ffmpeg -> 16kHz mono wav, optionally clipping START-END (seconds)."""
    cmd = ["ffmpeg", "-y"]
    if clip:
        start, _, end = clip.partition("-")
        if start:
            cmd += ["-ss", start]
        if end:
            cmd += ["-to", end]
    cmd += ["-i", str(src), "-ar", "16000", "-ac", "1", str(dst)]
    subprocess.run(cmd, check=True, capture_output=True)


def cmd_transcribe(args) -> int:
    from tts_serve.sources import SourceOpts, resolve
    from tts_serve.asr import VibeVoiceASR
    from tts_serve.outputs import (
        build_document, normalize_segments, render_format, write_outputs,
    )

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
    rs = resolve(args.source, workdir, opts)

    wav = workdir / "audio_16k.wav"
    _eprint(f"[preprocess] -> 16kHz mono{' (clip ' + args.clip + ')' if args.clip else ''}")
    _clip_and_normalize(rs.local_path, wav, args.clip)

    hotwords = args.hotwords
    if args.speakers:
        # The model only takes free-text context_info; fold the speaker hint in.
        hint = f"There are {args.speakers} speakers."
        hotwords = f"{hotwords}. {hint}" if hotwords else hint

    _eprint(f"[load] {args.model}")
    asr = VibeVoiceASR(model=args.model)
    _eprint(f"[transcribe] hotwords={hotwords or '(none)'}")
    res = asr.transcribe(str(wav), max_new_tokens=args.max_new_tokens, hotwords=hotwords)

    segments = normalize_segments(res.segments)
    doc = build_document(
        segments, source=rs.label, model=args.model,
        meeting_name=args.name or rs.name,
        gen_seconds=round(res.gen_seconds, 1),
        peak_vram_gb=round(res.peak_vram_gb, 2),
    )

    if args.stdout:
        print(render_format(doc, args.stdout))
        _eprint(f"[done] {doc['n_segments']} segments, speakers={doc['speakers']}, "
                f"{res.gen_seconds:.0f}s")
        return 0

    out_dir = Path(args.out) if args.out else Path("out") / f"{doc['meeting_name']}"
    paths = write_outputs(out_dir, doc)
    _eprint(f"[done] {doc['n_segments']} segments, speakers={doc['speakers']}, "
            f"{res.gen_seconds:.0f}s -> {out_dir}")
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

"""MVP meeting-transcription pipeline.

A single resident worker: load VibeVoice-ASR once, then process audio files
dropped into ``data/inbox``. For each file:

  1. preprocess  -> 16kHz mono WAV (ffmpeg)
  2. transcribe  -> VibeVoice-ASR (ASR + diarization + timestamps)
  3. postprocess -> results/<name>/{transcript.txt, subtitle.srt, segments.json}
  4. move source -> data/done (or data/failed on error)

Metadata (hotwords, meeting name) is read from an optional sidecar JSON next
to the audio file: ``meeting.wav`` + ``meeting.json`` with keys
{"meeting_name", "hotwords", "expected_speakers"}.

sha256 of the audio bytes is used for dedup: a file whose hash already has a
results dir is skipped.

Usage:
    python -m tts_serve.pipeline --once          # drain inbox and exit
    python -m tts_serve.pipeline --watch          # keep polling inbox
    python -m tts_serve.pipeline --file a.wav     # process one file directly
"""
from __future__ import annotations

import os

# Reduce GPU memory fragmentation across files (must be set before torch loads).
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

from tts_serve.asr import VibeVoiceASR
from tts_serve.outputs import build_document, normalize_segments, write_outputs

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
INBOX = DATA / "inbox"
DONE = DATA / "done"
FAILED = DATA / "failed"
RESULTS = DATA / "results"
OTHER = DATA / "other"   # non-audio files dropped into the inbox (e.g. images)

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".mp4", ".webm", ".mov", ".mkv", ".aac"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_16k_mono(src: Path, dst: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-ar", "16000", "-ac", "1", str(dst)],
        check=True, capture_output=True,
    )


def _sidecar(src: Path) -> dict:
    sc = src.with_suffix(".json")
    if sc.exists():
        try:
            return json.loads(sc.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def process_file(asr: VibeVoiceASR, src: Path) -> dict:
    meta = _sidecar(src)
    name = meta.get("meeting_name") or src.stem
    sha = _sha256(src)
    out_dir = RESULTS / f"{name}__{sha[:12]}"

    if (out_dir / "segments.json").exists():
        print(f"[skip] {src.name}: already transcribed ({out_dir.name})")
        return {"file": src.name, "status": "skipped", "out_dir": str(out_dir)}

    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "audio_16k.wav"
    print(f"[preprocess] {src.name} -> 16k mono")
    _to_16k_mono(src, work)

    hotwords = meta.get("hotwords")
    if isinstance(hotwords, list):
        hotwords = ",".join(hotwords)
    print(f"[transcribe] {src.name}  hotwords={hotwords or '(none)'}")
    res = asr.transcribe(str(work), hotwords=hotwords)

    segments = normalize_segments(res.segments)
    doc = build_document(
        segments, source=src.name, model="microsoft/VibeVoice-ASR",
        meeting_name=name, sha256=sha, hotwords=hotwords,
        expected_speakers=meta.get("expected_speakers"),
        gen_seconds=round(res.gen_seconds, 1),
        out_tokens=res.out_tokens, peak_vram_gb=round(res.peak_vram_gb, 2),
    )
    write_outputs(out_dir, doc)
    print(
        f"[done] {src.name}: {doc['n_segments']} segments, speakers={doc['speakers']}, "
        f"{res.gen_seconds:.0f}s gen, peak {res.peak_vram_gb:.1f}GB -> {out_dir.name}"
    )
    return {
        "file": src.name, "status": "done", "out_dir": str(out_dir),
        "segments": doc["n_segments"], "speakers": doc["speakers"],
        "gen_seconds": res.gen_seconds,
    }


def _drain(asr: VibeVoiceASR) -> list[dict]:
    results = []
    for src in sorted(INBOX.iterdir()):
        if src.is_dir():
            continue
        if src.suffix.lower() not in AUDIO_EXTS:
            # Move non-audio (sidecar .json handled per-file; images, etc.) out of
            # the inbox so a path-triggered drain doesn't loop on it forever.
            if src.suffix.lower() != ".json":
                OTHER.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), OTHER / src.name)
                print(f"[skip-nonaudio] {src.name} -> {OTHER.name}/")
            continue
        try:
            r = process_file(asr, src)
            shutil.move(str(src), DONE / src.name)
            sc = src.with_suffix(".json")
            if sc.exists():
                shutil.move(str(sc), DONE / sc.name)
            results.append(r)
        except Exception as e:  # noqa: BLE001
            print(f"[FAILED] {src.name}: {e}")
            shutil.move(str(src), FAILED / src.name)
            results.append({"file": src.name, "status": "failed", "error": str(e)})
    return results


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true", help="drain inbox once and exit")
    g.add_argument("--watch", action="store_true", help="poll inbox forever")
    g.add_argument("--file", help="process a single file directly")
    ap.add_argument("--poll", type=float, default=5.0, help="watch poll interval (s)")
    args = ap.parse_args()

    for d in (INBOX, DONE, FAILED, RESULTS):
        d.mkdir(parents=True, exist_ok=True)

    print("Loading VibeVoice-ASR (resident) ...")
    asr = VibeVoiceASR()
    print(f"Model ready in {asr.load_seconds:.1f}s\n")

    if args.file:
        process_file(asr, Path(args.file))
        return
    if args.once:
        _drain(asr)
        return
    print(f"Watching {INBOX} (poll {args.poll}s). Drop audio files in.\n")
    while True:
        _drain(asr)
        time.sleep(args.poll)


if __name__ == "__main__":
    main()

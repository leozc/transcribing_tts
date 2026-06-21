#!/usr/bin/env python
"""Long-form transcription by chunking, for audio too long for a single pass.

A full 41-min pass OOMs on a 24GB card (weights 17.4GB + long-sequence
activations). This splits the audio into fixed chunks, transcribes each (model
loaded once), offsets timestamps, and concatenates.

Caveat: speaker ids are assigned per-chunk and may not correspond across chunk
boundaries (a known limitation of chunked diarization).

    python benchmark/transcribe_longform.py <wav> --out DIR --chunk 900
"""
import argparse
import os
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tts_serve.asr import VibeVoiceASR  # noqa: E402
from tts_serve.outputs import build_document, normalize_segments, write_outputs  # noqa: E402


def _duration(wav: str) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", wav]
    )
    return float(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--chunk", type=float, default=900.0, help="chunk length (s)")
    ap.add_argument("--max-new-tokens", type=int, default=14336, dest="max_new_tokens")
    args = ap.parse_args()

    wav = args.wav
    total = _duration(wav)
    starts = [i * args.chunk for i in range(int(total // args.chunk) + 1) if i * args.chunk < total]
    print(f"duration {total:.0f}s -> {len(starts)} chunk(s) of {args.chunk:.0f}s", flush=True)

    work = Path(args.out); work.mkdir(parents=True, exist_ok=True)
    asr = VibeVoiceASR()
    print(f"model loaded in {asr.load_seconds:.1f}s", flush=True)

    all_segments: list[dict] = []
    for i, start in enumerate(starts):
        chunk_wav = work / f"chunk_{i:02d}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(start), "-t", str(args.chunk),
             "-i", wav, "-ar", "16000", "-ac", "1", str(chunk_wav)],
            check=True, capture_output=True,
        )
        res = asr.transcribe(str(chunk_wav), max_new_tokens=args.max_new_tokens)
        segs = normalize_segments(res.segments)
        # TODO(voiceprint): speaker ids are chunk-local and do NOT correspond across
        # chunks, inflating the global speaker count. Fix via CAM++/3D-Speaker
        # embeddings: cluster per-segment fingerprints across chunks and remap to a
        # single global identity. See TODO.md.
        for s in segs:  # offset to absolute time
            s["start"] += start
            s["end"] += start
        all_segments.extend(segs)
        print(f"  chunk {i} [{start:.0f}-{min(start+args.chunk,total):.0f}s]: "
              f"{len(segs)} segs, {res.gen_seconds:.0f}s, peak {res.peak_vram_gb:.1f}GB", flush=True)
        chunk_wav.unlink(missing_ok=True)

    doc = build_document(
        all_segments, source=Path(wav).name,
        model="microsoft/VibeVoice-ASR", meeting_name=args.name or Path(wav).stem,
        chunked=True, chunk_seconds=args.chunk,
    )
    write_outputs(work, doc)
    print(f"\n[done] {doc['n_segments']} segments, speakers={doc['speakers']}, "
          f"duration {doc['duration_s']:.0f}s -> {work}", flush=True)


if __name__ == "__main__":
    main()

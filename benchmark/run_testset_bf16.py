#!/usr/bin/env python
"""Run the full test-set through the accurate bf16 transformers path.

Priority is accuracy (full bf16 precision, no quantization). Long audio that
won't fit a single pass on 24GB is chunked (chunking is acceptable for speed).
Model is loaded once and reused across all items.

    python benchmark/run_testset_bf16.py [--only id1,id2] [--outdir out/testset]
"""
import argparse
import json
import os
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tts_serve.asr import VibeVoiceASR  # noqa: E402
from tts_serve.outputs import build_document, normalize_segments, write_outputs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmark/testset/manifest.json"
SINGLE_PASS_MAX_S = 1600.0   # >this -> chunk (26-min gd1 fits single pass; 41-min v7 chunks)
CHUNK_S = 820.0


def _duration(wav: str) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", wav])
    return float(out)


def transcribe_any(asr: VibeVoiceASR, wav: str, work: Path) -> tuple[list[dict], float]:
    """Single pass if short enough, else chunk. Returns (canonical_segments, gen_s)."""
    dur = _duration(wav)
    t0 = time.time()
    if dur <= SINGLE_PASS_MAX_S:
        res = asr.transcribe(wav, max_new_tokens=16384)
        return normalize_segments(res.segments), time.time() - t0
    # chunked
    starts = [i * CHUNK_S for i in range(int(dur // CHUNK_S) + 1) if i * CHUNK_S < dur]
    all_segs: list[dict] = []
    for i, start in enumerate(starts):
        cw = work / f"_chunk_{i:02d}.wav"
        subprocess.run(["ffmpeg", "-y", "-ss", str(start), "-t", str(CHUNK_S),
                        "-i", wav, "-ar", "16000", "-ac", "1", str(cw)],
                       check=True, capture_output=True)
        res = asr.transcribe(str(cw), max_new_tokens=14336)
        segs = normalize_segments(res.segments)
        # TODO(voiceprint): chunk-local speaker ids don't align across chunks ->
        # inflated speaker count. Remap via CAM++ embeddings. See TODO.md.
        for s in segs:
            s["start"] += start
            s["end"] += start
        all_segs.extend(segs)
        cw.unlink(missing_ok=True)
    return all_segs, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated item ids")
    ap.add_argument("--outdir", default="out/testset")
    args = ap.parse_args()

    items = json.loads(MANIFEST.read_text())["items"]
    only = set(args.only.split(",")) if args.only else None
    todo = [it for it in items if it.get("audio") and (ROOT / it["audio"]).exists()
            and (not only or it["id"] in only)]
    print(f"items to run: {[it['id'] for it in todo]}", flush=True)

    asr = VibeVoiceASR()
    print(f"model loaded (bf16) in {asr.load_seconds:.1f}s\n", flush=True)

    summary = []
    for it in todo:
        wav = str(ROOT / it["audio"])
        dur = _duration(wav)
        mode = "single" if dur <= SINGLE_PASS_MAX_S else "chunked"
        out_dir = ROOT / args.outdir / it["id"]
        print(f"=== {it['id']} ({it['language']}, true_spk={it['expected_speakers']}, "
              f"{dur:.0f}s, {mode}) ===", flush=True)
        segs, gen = transcribe_any(asr, wav, out_dir)
        doc = build_document(segs, source=it["url"], model="microsoft/VibeVoice-ASR",
                             meeting_name=it["id"], backend="transformers-bf16",
                             mode=mode, expected_speakers=it["expected_speakers"],
                             gen_seconds=round(gen, 1),
                             realtime_factor=round(dur / gen, 1) if gen else None)
        write_outputs(out_dir, doc)
        nspk = len(doc["speakers"])
        match = "OK " if nspk == it["expected_speakers"] else "DIFF"
        rt = dur / gen if gen else 0
        print(f"  -> {doc['n_segments']} segs | spk {nspk} (true {it['expected_speakers']}) {match} "
              f"| {gen:.0f}s gen | {rt:.1f}x RT\n", flush=True)
        summary.append((it["id"], it["language"], doc["n_segments"], nspk,
                        it["expected_speakers"], match, round(rt, 1)))

    print("===== TEST-SET SUMMARY (bf16, accuracy-first) =====")
    print(f"{'id':20} {'lang':12} {'segs':>5} {'spk':>4} {'true':>5} {'':>5} {'xRT':>6}")
    for r in summary:
        print(f"{r[0]:20} {r[1]:12} {r[2]:>5} {r[3]:>4} {r[4]:>5} {r[5]:>5} {r[6]:>6}")


if __name__ == "__main__":
    main()

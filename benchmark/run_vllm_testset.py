#!/usr/bin/env python
"""Run the test-set dataset through the VibeVoice vLLM server (OpenAI API).

Unlike the transformers path (asr.py), this hits the resident vLLM server, which
uses PagedAttention — so long audio (e.g. the 41-min v7) transcribes in a SINGLE
pass without the OOM/chunking the transformers path needs.

    python benchmark/run_vllm_testset.py [--url http://localhost:8777] [--only v7_eric_schmidt]
"""
import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tts_serve.postprocess import parse_segments  # noqa: E402
from tts_serve.outputs import build_document, write_outputs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmark/testset/manifest.json"
KEYS = ["Start time", "End time", "Speaker ID", "Content"]


def _duration(wav: str) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", wav])
    return float(out)


def transcribe(wav: str, url: str, hotwords: str | None = None, timeout: int = 3600) -> tuple[str, float, float]:
    dur = _duration(wav)
    b64 = base64.b64encode(Path(wav).read_bytes()).decode()
    if hotwords:
        prompt = (f"This is a {dur:.2f} seconds audio, with extra info: {hotwords}\n\n"
                  f"Please transcribe it with these keys: " + ", ".join(KEYS))
    else:
        prompt = (f"This is a {dur:.2f} seconds audio, please transcribe it with these keys: "
                  + ", ".join(KEYS))
    payload = {
        "model": "vibevoice",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant that transcribes audio input into text output in JSON format."},
            {"role": "user", "content": [
                {"type": "audio_url", "audio_url": {"url": f"data:audio/wav;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ]},
        ],
        "max_tokens": 32768, "temperature": 0.0, "top_p": 1.0, "stream": False,
    }
    t0 = time.time()
    r = requests.post(f"{url}/v1/chat/completions", json=payload, timeout=timeout)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return content, dur, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8777")
    ap.add_argument("--only", help="comma-separated item ids")
    ap.add_argument("--outdir", default="out/vllm_testset")
    args = ap.parse_args()

    items = json.loads(MANIFEST.read_text())["items"]
    only = set(args.only.split(",")) if args.only else None
    summary = []
    for item in items:
        if only and item["id"] not in only:
            continue
        if not item.get("audio"):
            print(f"SKIP {item['id']} (no local audio)", flush=True); continue
        wav = ROOT / item["audio"]
        if not wav.exists():
            print(f"SKIP {item['id']} (missing {wav})", flush=True); continue
        print(f"\n=== {item['id']} ({item['language']}, true_spk={item['expected_speakers']}) ===", flush=True)
        try:
            content, dur, gen = transcribe(str(wav), args.url)
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED: {type(e).__name__}: {str(e)[:200]}", flush=True)
            summary.append((item["id"], "FAILED", None, None, None)); continue
        segs = parse_segments(content)
        canonical = [{"start": s.start, "end": s.end, "speaker": s.speaker, "text": s.text} for s in segs]
        doc = build_document(canonical, source=item["url"], model="vibevoice-vllm",
                             meeting_name=item["id"], backend="vllm",
                             gen_seconds=round(gen, 1), realtime_factor=round(dur / gen, 1) if gen else None)
        out_dir = ROOT / args.outdir / item["id"]
        write_outputs(out_dir, doc)
        n_spk = len(doc["speakers"])
        rt = dur / gen if gen else 0
        ok = "OK " if n_spk == item["expected_speakers"] else "SPK"
        print(f"  {ok} {doc['n_segments']} segs, {n_spk} spk (true {item['expected_speakers']}), "
              f"{gen:.0f}s gen, {rt:.1f}x realtime, {dur:.0f}s audio", flush=True)
        summary.append((item["id"], doc["n_segments"], n_spk, item["expected_speakers"], round(rt, 1)))

    print("\n===== SUMMARY =====")
    print(f"{'id':22} {'segs':>5} {'spk':>4} {'true':>5} {'xRT':>6}")
    for row in summary:
        print(f"{row[0]:22} {str(row[1]):>5} {str(row[2]):>4} {str(row[3]):>5} {str(row[4]):>6}")


if __name__ == "__main__":
    main()

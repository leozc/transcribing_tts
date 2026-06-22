#!/usr/bin/env python
"""Measure transcription accuracy against each YouTube video's own captions.

Reference = YouTube captions (json3) for the same clip window.
Hypothesis = our transcript (out/testset/<id>/segments.json).
Metric = WER for English, CER for Chinese / code-switching (char-level is more
meaningful when word boundaries are ambiguous).

NOTE: YouTube auto-captions are themselves imperfect (~5-15% WER) and lack
punctuation/casing, so this is a PROXY reference — good for catching gross errors
and relative comparison, not a gold standard.

    python benchmark/accuracy_eval.py [--only id1,id2]
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import jiwer

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmark/testset/manifest.json"
CAPS = ROOT / "benchmark/captions"
RESULTS = ROOT / "out/testset"

EN_TRANSFORM = jiwer.Compose([
    jiwer.ToLowerCase(),
    jiwer.RemovePunctuation(),
    jiwer.RemoveMultipleSpaces(),
    jiwer.Strip(),
    jiwer.ReduceToListOfListOfWords(),
])


def fetch_captions(url: str, lang_pref: list[str]) -> Path | None:
    CAPS.mkdir(parents=True, exist_ok=True)
    vid = re.search(r"[?&]v=([\w-]+)|youtu\.be/([\w-]+)", url)
    vid = (vid.group(1) or vid.group(2)) if vid else None
    subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--skip-download", "--write-auto-subs",
         "--write-subs", "--sub-langs", ",".join(f"{l}.*" for l in lang_pref),
         "--sub-format", "json3", "-o", str(CAPS / "%(id)s"), url],
        capture_output=True,
    )
    for lang in lang_pref:  # prefer manual/orig order as given
        for cand in sorted(CAPS.glob(f"{vid}.{lang}*.json3")):
            return cand
    return None


def ref_from_json3(path: Path, clip_end: float | None) -> str:
    d = json.loads(path.read_text())
    parts = []
    for e in d.get("events", []):
        if not e.get("segs"):
            continue
        if clip_end is not None and e.get("tStartMs", 0) >= clip_end * 1000:
            continue
        parts.append("".join(s.get("utf8", "") for s in e["segs"]))
    return " ".join(parts)


def hyp_from_segments(item_id: str) -> str:
    p = RESULTS / item_id / "segments.json"
    if not p.exists():
        return ""
    d = json.loads(p.read_text())
    return " ".join(s["text"] for s in d["segments"])


def _strip_cjk(s: str) -> str:
    # char-level: drop whitespace + punctuation, keep CJK + alnum
    return re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE).lower()


def score(ref: str, hyp: str, lang: str) -> dict:
    if lang.startswith("zh") or "code-switch" in lang or "mix" in lang:
        r, h = _strip_cjk(ref), _strip_cjk(hyp)
        return {"metric": "CER", "value": jiwer.cer(r, h),
                "ref_units": len(r), "hyp_units": len(h)}
    out = jiwer.process_words(ref, hyp, EN_TRANSFORM, EN_TRANSFORM)
    return {"metric": "WER", "value": out.wer, "ref_units": sum(len(x) for x in out.references),
            "hyp_units": sum(len(x) for x in out.hypotheses)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    args = ap.parse_args()
    items = json.loads(MANIFEST.read_text())["items"]
    only = set(args.only.split(",")) if args.only else None

    rows = []
    for it in items:
        if only and it["id"] not in only:
            continue
        if it["source_type"] != "youtube":
            print(f"SKIP {it['id']} (not youtube)", flush=True); continue
        lang_pref = ["zh-Hans", "zh", "zh-Hant"] if it["language"].startswith("zh") or "mix" in it["language"] or "code-switch" in it["language"] else ["en"]
        clip = it.get("clip", "full")
        clip_end = float(clip.split("-")[1]) if clip not in ("full", "") and "-" in clip else None
        print(f"=== {it['id']} ({it['language']}) ===", flush=True)
        cap = fetch_captions(it["url"], lang_pref)
        if not cap:
            print(f"  no captions ({lang_pref})", flush=True)
            rows.append((it["id"], it["language"], "n/a", None, None)); continue
        ref = ref_from_json3(cap, clip_end)
        hyp = hyp_from_segments(it["id"])
        if not hyp:
            print(f"  no hypothesis (run the test set first)", flush=True)
            rows.append((it["id"], it["language"], "no-hyp", None, None)); continue
        s = score(ref, hyp, it["language"])
        print(f"  {s['metric']} {s['value']*100:.1f}%  (ref {s['ref_units']} / hyp {s['hyp_units']} units, cap={cap.name})", flush=True)
        rows.append((it["id"], it["language"], s["metric"], round(s["value"] * 100, 1), s["ref_units"]))

    print("\n===== ACCURACY vs YouTube captions (proxy reference) =====")
    print(f"{'id':20} {'lang':22} {'metric':6} {'err%':>6} {'ref_units':>10}")
    for r in rows:
        print(f"{r[0]:20} {r[1]:22} {str(r[2]):6} {str(r[3]):>6} {str(r[4]):>10}")
    print("\nLower err% = closer to YouTube captions. Captions are imperfect (~5-15% WER)"
          " and unpunctuated, so absolute numbers are a proxy, not ground truth.")


if __name__ == "__main__":
    main()

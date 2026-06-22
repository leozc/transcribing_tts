#!/usr/bin/env python
"""Speaker re-identification eval: speaker-count error before/after voiceprint re-id.

True DER needs reference speaker turns (we don't have them), so we measure the
objective proxy we DO have: |predicted speakers - true speakers|, before vs after
re-clustering segment voiceprints with the known speaker count.

    python benchmark/reid_eval.py [--only id1,id2]

Writes re-id'd outputs to out/testset_reid/<id>/.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tts_serve.diarize import SpeakerReID  # noqa: E402
from tts_serve.outputs import build_document, write_outputs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmark/testset/manifest.json"
RESULTS = ROOT / "out/testset"
OUT = ROOT / "out/testset_reid"


def n_spk(segs: list[dict]) -> int:
    return len({s["speaker"] for s in segs if s.get("speaker") not in (None, "?")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    args = ap.parse_args()
    items = json.loads(MANIFEST.read_text())["items"]
    only = set(args.only.split(",")) if args.only else None

    reid = SpeakerReID()
    rows = []
    for it in items:
        if only and it["id"] not in only:
            continue
        if not it.get("audio") or not (ROOT / it["audio"]).exists():
            continue
        seg_path = RESULTS / it["id"] / "segments.json"
        if not seg_path.exists():
            continue
        segs = json.loads(seg_path.read_text())["segments"]
        true_n = it["expected_speakers"]
        before = n_spk(segs)
        fixed = reid.relabel(str(ROOT / it["audio"]), [dict(s) for s in segs], n_speakers=true_n)
        after = n_spk(fixed)
        doc = build_document(fixed, source=it["url"], model="microsoft/VibeVoice-ASR+reid",
                             meeting_name=it["id"], reid="ecapa-tdnn", n_speakers_hint=true_n)
        write_outputs(OUT / it["id"], doc)
        err_b, err_a = abs(before - true_n), abs(after - true_n)
        print(f"{it['id']:20} true={true_n}  before={before} (err {err_b})  "
              f"after={after} (err {err_a})  {'FIXED' if err_a < err_b else ('OK' if err_a==0 else '')}",
              flush=True)
        rows.append((it["id"], true_n, before, after, err_b, err_a))

    tot_b = sum(r[4] for r in rows)
    tot_a = sum(r[5] for r in rows)
    print(f"\n===== SPEAKER-COUNT ERROR (proxy for DER) =====")
    print(f"{'id':20} {'true':>4} {'before':>7} {'after':>6}")
    for r in rows:
        print(f"{r[0]:20} {r[1]:>4} {r[2]:>7} {r[3]:>6}")
    print(f"\ntotal |error|: before={tot_b}  after={tot_a}  (exact matches: "
          f"{sum(1 for r in rows if r[5]==0)}/{len(rows)})")


if __name__ == "__main__":
    main()

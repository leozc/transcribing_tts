#!/usr/bin/env python
"""Gold-reference accuracy: real WER/CER vs human transcripts.

Streams N utterances from standard ASR benchmarks (human ground-truth, not
YouTube-caption proxy), transcribes each with VibeVoice-ASR, and computes
aggregate WER (English) / CER (Chinese, char-level).

Datasets (streamed, decode bytes via soundfile — avoids torchcodec):
  - LibriSpeech test-clean (English, canonical)
  - FLEURS en_us / cmn_hans_cn / ... (multilingual read speech, gold transcripts)

    python benchmark/gold_eval.py [--n 50] [--langs librispeech_en,fleurs_zh,...]
"""
import argparse
import io
import json
import re
import sys
import tempfile
from pathlib import Path

import jiwer
import soundfile as sf
from datasets import Audio, load_dataset

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tts_serve.asr import VibeVoiceASR  # noqa: E402

# key -> (hf_name, config, split, text_field, metric)
DATASETS = {
    "librispeech_en": ("openslr/librispeech_asr", "clean", "test", "text", "wer"),
    "fleurs_en": ("google/fleurs", "en_us", "test", "transcription", "wer"),
    "fleurs_zh": ("google/fleurs", "cmn_hans_cn", "test", "transcription", "cer"),
    "fleurs_ja": ("google/fleurs", "ja_jp", "test", "transcription", "cer"),
    "fleurs_es": ("google/fleurs", "es_419", "test", "transcription", "wer"),
}

EN_TF = jiwer.Compose([jiwer.ToLowerCase(), jiwer.RemovePunctuation(),
                       jiwer.RemoveMultipleSpaces(), jiwer.Strip(),
                       jiwer.ReduceToListOfListOfWords()])


def _strip_tags(s: str) -> str:
    # drop non-speech tags like [Silence], [Music] (whole bracketed span)
    return re.sub(r"\[[^\]]*\]", " ", s)


def _cjk(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", _strip_tags(s), flags=re.UNICODE).lower()


def run_dataset(asr: VibeVoiceASR, key: str, n: int, workdir: Path) -> dict:
    name, conf, split, tf, metric = DATASETS[key]
    ds = load_dataset(name, conf, split=split, streaming=True).cast_column("audio", Audio(decode=False))
    refs, hyps = [], []
    for i, ex in enumerate(ds):
        if i >= n:
            break
        a = ex["audio"]
        data, sr = sf.read(io.BytesIO(a["bytes"])) if a.get("bytes") else sf.read(a["path"])
        wav = workdir / f"{key}_{i}.wav"
        sf.write(str(wav), data, sr)
        res = asr.transcribe(str(wav), max_new_tokens=1024)
        hyp = " ".join(s.get("text", "") for s in res.segments)
        refs.append(str(ex[tf]))
        hyps.append(_strip_tags(hyp) if metric == "wer" else hyp)  # cer strips in _cjk
        wav.unlink(missing_ok=True)
    if metric == "cer":
        r = [_cjk(x) for x in refs]
        h = [_cjk(x) for x in hyps]
        val = jiwer.cer(r, h)
    else:
        val = jiwer.process_words(refs, hyps, EN_TF, EN_TF).wer
    return {"key": key, "metric": metric.upper(), "value": round(val * 100, 1), "n": len(refs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--langs", default="librispeech_en,fleurs_en,fleurs_zh,fleurs_ja,fleurs_es")
    ap.add_argument("--out", default="benchmark/gold_results.json")
    args = ap.parse_args()
    keys = [k.strip() for k in args.langs.split(",") if k.strip() in DATASETS]

    asr = VibeVoiceASR()
    print(f"model loaded in {asr.load_seconds:.1f}s\n", flush=True)
    workdir = Path(tempfile.mkdtemp(prefix="gold_"))
    rows = []
    for k in keys:
        print(f"=== {k} (n={args.n}) ===", flush=True)
        try:
            row = run_dataset(asr, k, args.n, workdir)
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED: {type(e).__name__}: {str(e)[:160]}", flush=True)
            continue
        print(f"  {row['metric']} {row['value']}%  (n={row['n']})", flush=True)
        rows.append(row)

    print("\n===== GOLD-REFERENCE ACCURACY (human transcripts) =====")
    print(f"{'dataset':18} {'metric':6} {'err%':>6} {'n':>4}")
    for r in rows:
        print(f"{r['key']:18} {r['metric']:6} {r['value']:>6} {r['n']:>4}")
    Path(args.out).write_text(json.dumps(rows, indent=2))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Transcribe an audio file with VibeVoice-ASR via transformers (no vLLM).

Used as the Phase-0 benchmark because the vLLM image's CUDA 12.9 build is
incompatible with the host driver 535 (CUDA 12.2 ceiling). transformers +
torch cu121 runs natively on the 4090.

Writes <out>.raw.txt (model output) and <out>.segments.json, and prints
timing + peak VRAM.
"""
import argparse
import json
import time

import torch

from vibevoice.modular.modeling_vibevoice_asr import (
    VibeVoiceASRForConditionalGeneration,
)
from vibevoice.processor.vibevoice_asr_processor import VibeVoiceASRProcessor


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="microsoft/VibeVoice-ASR")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-new-tokens", type=int, default=16384)
    args = ap.parse_args()

    t_load = time.time()
    print(f"Loading processor + model from {args.model} ...", flush=True)
    processor = VibeVoiceASRProcessor.from_pretrained(
        args.model, language_model_pretrained_name="Qwen/Qwen2.5-7B"
    )
    model = VibeVoiceASRForConditionalGeneration.from_pretrained(
        args.model,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        trust_remote_code=True,
    ).to("cuda")
    model.eval()
    load_s = time.time() - t_load
    print(f"Model loaded in {load_s:.1f}s", flush=True)
    print(f"VRAM after load: {torch.cuda.memory_allocated()/1e9:.2f} GB", flush=True)

    inputs = processor(
        audio=[args.audio],
        sampling_rate=None,
        return_tensors="pt",
        padding=True,
        add_generation_prompt=True,
    )
    inputs = {k: (v.to("cuda") if isinstance(v, torch.Tensor) else v)
              for k, v in inputs.items()}
    n_in = inputs["input_ids"].shape[1]
    print(f"Input tokens: {n_in}  speech tensor: {tuple(inputs['speech_tensors'].shape)}", flush=True)

    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=args.max_new_tokens,
            do_sample=False,
            pad_token_id=processor.pad_id,
            eos_token_id=processor.tokenizer.eos_token_id,
        )
    gen_s = time.time() - t0

    gen_ids = out[0, n_in:]
    eos = (gen_ids == processor.tokenizer.eos_token_id).nonzero(as_tuple=True)[0]
    if len(eos) > 0:
        gen_ids = gen_ids[: eos[0] + 1]
    raw = processor.decode(gen_ids, skip_special_tokens=True)

    try:
        segs = processor.post_process_transcription(raw)
    except Exception as e:  # noqa: BLE001
        print(f"parse warning: {e}", flush=True)
        segs = []

    with open(args.out + ".raw.txt", "w", encoding="utf-8") as f:
        f.write(raw)
    with open(args.out + ".segments.json", "w", encoding="utf-8") as f:
        json.dump(segs, f, ensure_ascii=False, indent=2)

    peak = torch.cuda.max_memory_allocated() / 1e9
    n_out = gen_ids.shape[0]
    print("\n===== BENCHMARK =====", flush=True)
    print(f"load_s={load_s:.1f}  gen_s={gen_s:.1f}  out_tokens={n_out}", flush=True)
    print(f"tokens/s={n_out/gen_s:.1f}  peak_VRAM={peak:.2f}GB  segments={len(segs)}", flush=True)
    print(f"raw chars={len(raw)}", flush=True)
    print(f"wrote {args.out}.raw.txt and {args.out}.segments.json", flush=True)


if __name__ == "__main__":
    main()

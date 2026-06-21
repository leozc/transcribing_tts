# Phase 0 — VibeVoice-ASR benchmark on RTX 4090 (go/no-go)

**Verdict: ✅ GO.** VibeVoice-ASR runs on the single 4090 with large margin on
both throughput and VRAM, and transcribes Chinese, English, and code-switching
meetings with correct speaker diarization and full coverage.

## Setup
- **Model:** `microsoft/VibeVoice-ASR` (Qwen2.5-7B backbone + acoustic encoder,
  ~9B total, 17.3GB bf16 weights, MIT). Released 2026-01-27.
- **Serving:** transformers + torch **cu121** (native), `attn_implementation=sdpa`.
  - vLLM serving (the planned path) is **blocked on this host**: the
    `vllm/vllm-openai:v0.14.1` image ships torch built for **CUDA 12.9**, but the
    host driver is **535.309.01 (CUDA 12.2 ceiling)** → `CUDA error 804:
    forward compatibility was attempted on non supported HW` at engine init.
    Fix for vLLM = upgrade host driver to ≥555 (system-wide; not done here).
- **Hardware:** 1× RTX 4090 (23.0 GB), driver 535, 440 GB RAM.
- **Sample length:** first 20 min of each video, 16 kHz mono WAV.

## Throughput & memory (6 × 20-min clips)

| clip | segments | speakers | gen time | out tokens | peak VRAM |
|------|----------|----------|----------|------------|-----------|
| v1 Chinese-mix      | 97  | 2 | 146.5s | 6214 | 21.4 GB |
| v2 English          | 90  | 2 | 147.0s | 6232 | 21.3 GB |
| v3 All-In (4 ppl)   | 103 | 4 | 167.7s | 7147 | 21.3 GB |
| v4 3-person         | 71  | 5*| 151.9s | 6464 | 21.3 GB |
| v5 2-person         | 55  | 2 | 128.5s | 5454 | 21.4 GB |
| v6 pure-Chinese     | 37  | 1 | 135.9s | 5773 | 21.4 GB |

- **Mean 146s to transcribe 20 min of audio → ~8× faster than realtime.**
- **Peak VRAM ~21.4 GB** (fits the 24 GB card; ~1.5 GB headroom).
- Model load: ~4s (warm HF cache). Resident worker loads once.

### Capacity vs. the 10 meetings/day × 1 hr requirement
- 10 hr/day of audio ÷ 8× realtime ≈ **1.2 GPU-hours/day**.
- One 4090 clears a full day's meetings in well under 2 hours — huge headroom
  for retries, hotword reruns, and an LLM summary pass (v2).

## Quality (subjective, spot-checked)
- **English (v2, v5):** accurate, clean 2-speaker diarization.
- **Chinese-mix (v1):** excellent — flawless 中英 code-switching
  ("我是小俊。今天的嘉宾是 Google DeepMind 的研究员…OpenAI…Anthropic…startup…make bet"),
  correct host/guest split.
- **Pure Chinese (v6):** excellent on a dense, technical monologue
  (防火长城 / 网信办 / VPN 中转机场), single speaker correctly held throughout.
- **All-In 4-person (v3):** correct **4 speakers**, full coverage, even caught
  the intro song + `[Music]` tags.
- **Coverage:** every clip transcribed the full 0→1200s.

## Known weaknesses observed
- **Speaker over-count (v4):** a 3-person video was split into **5** speakers
  (`expected_spk=3`). Diarization tends to over-segment when voices are similar
  or interjections are short — matches the design-doc risk. Mitigations:
  (a) wire `expected_speakers` into the prompt `context_info` (currently only
  hotwords are injected); (b) post-hoc speaker merging; (c) manual UI fixup (v1).
- `[Music]`/non-speech segments carry a null speaker id — handled in postprocess.

## Engineering notes (bugs found & fixed during the run)
1. **Cross-file CUDA OOM** in the resident worker: weights (17.4 GB) + an unfreed
   ~3 GB reserved cache from the previous file fragmented the GPU; the next
   `generate()` OOM'd trying to allocate ~2.5 GB. **Fixed** with
   `torch.cuda.empty_cache()` + `gc.collect()` after each file and
   `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`. After the fix: 6 files
   back-to-back, **0 OOM**.
2. **`sorted({...None...})`** on null speaker ids → `TypeError`. Fixed (guard None).

## Settings
- `max_new_tokens=16384` (enough for 20-min clips; raise toward 32k for 60-min).
- Greedy decoding (`do_sample=False`). Resident, single-GPU, serial.

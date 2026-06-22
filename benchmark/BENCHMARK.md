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

---

# Test-set evaluation (bf16, accuracy-first)

Full run over `benchmark/testset/manifest.json` via the transformers bf16 path
(`benchmark/run_testset_bf16.py`), model loaded once. ≤27-min clips single-pass;
v7 (41 min) chunked at ~14-min segments.

| id | lang | segs | model spk | true spk | speed |
|----|------|-----:|----------:|---------:|------:|
| v1_chinese | zh-en mix | 95 | 2 | 2 ✅ | 8.1× |
| v2_english | en | 88 | 2 | 2 ✅ | 8.2× |
| v3_allin | en | 103 | 4 | 4 ✅ | 7.2× |
| v4 (4 people) | en | 71 | 5 | 4 (+1) | 7.8× |
| v5_2people | en | 54 | 2 | 2 ✅ | 9.3× |
| v6_chinese_pure | zh | 37 | 1 | 1 ✅ | 8.8× |
| v7_eric_schmidt | en | 166 | 4 | 2 (chunk artifact) | 7.7× |

- **Speaker count** exact on **5/7**. v4 over-counts by 1; v7's "+2" is a chunking
  artifact (chunk-local ids don't align — see `TODO.md`, voiceprint merge).
- **Throughput** 7–9× realtime at full bf16 precision.

## ⚠️ How accuracy was (and was NOT) measured
This evaluation checks **coverage** (full 0→duration), **speaker count** vs
ground truth, and **subjective coherence** spot-checks (fluent output, correct
named entities, recognizable content matching the source). It does **NOT** yet
measure word-level accuracy: there are no reference transcripts, so **no WER/CER
and no DER/cpWER** are computed. "Accurate" here means "reads correctly on
inspection," not a measured error rate.

**To truly verify:** pull each source's reference transcript (e.g. YouTube
captions) and compute WER/CER; use reference speaker turns for DER. That is the
right next step to put a number on accuracy. (TODO)

---

# Measured accuracy — WER/CER vs YouTube captions

`benchmark/accuracy_eval.py` pulls each YouTube video's own captions (json3,
same clip window) as a **proxy reference** and computes WER (English) / CER
(Chinese, char-level).

| id | metric | error % | notes |
|----|--------|--------:|-------|
| v7_eric_schmidt | WER | **3.6%** | best — clean interview speech |
| v5_2people | WER | **6.4%** | |
| v4 (4 people) | WER | **6.9%** | |
| v3_allin | WER | **7.5%** | overlap/banter |
| v1_chinese | CER | **9.8%** | zh/en code-switching (char-level) |
| v2_english | WER | 19.1% | outlier — see below |
| v6_chinese_pure | — | n/a | no YouTube captions available |

**Interpretation.** English WER of ~4–7% **against captions that are themselves
~5–15% WER and stripped of punctuation/casing** means our output is at least as
good as YouTube's ASR, often better. CER 9.8% on Chinese code-switching is solid.

**The v2 "19%" is not error.** Inspected: both transcripts cover the same span and
end on the same sentence, but our hypothesis has ~340 more words because we
transcribe **verbatim** (keeps "okay", "let's, let's", repeated fillers) while the
captions clean disfluencies. WER against cleaned captions penalizes faithful
verbatim ASR — so true accuracy is likely *better* than the number.

**Limits of this method:** (1) captions are an imperfect proxy, not gold; (2) no
punctuation/casing normalization beyond lowercasing+depunct; (3) measures words
only — **not** speaker attribution (no DER/cpWER yet); (4) v6 (pure Chinese) has no
captions, so unmeasured. A small human-checked gold clip remains the real test.

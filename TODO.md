# TODO

## Cross-chunk speaker alignment (voice fingerprinting) — DONE (with caveat)
Implemented in `src/tts_serve/diarize.py` (`SpeakerReID`): embeds each segment
with ECAPA-TDNN (CAM++ is a drop-in alternative) and re-clusters across ALL
segments — including across chunk boundaries — to global speaker ids. Enabled via
`tts-serve transcribe --reid --speakers N`. Measured: speaker-count error 3→0
across the test set (v7 4→2, v4 5→4 fixed). See `benchmark/reid_eval.py`.

**Remaining:** auto speaker-count estimation is unreliable (threshold clustering
over-splits same-recording speakers) — re-id currently needs the count via
`--speakers` / `expected_speakers`. TODO: robust auto-count via **spectral
clustering + eigengap** (the standard diarization estimator), and proper **DER**
measurement (needs reference speaker turns we don't have yet).

## Speaker name suggestion from self-intros (post-processing)
After transcription, speakers frequently **self-introduce** ("I'm Skyler, I've been
an investor at Pelion for four years..."). A post-processing pass can scan each
speaker's segments for self-introduction patterns (name + role/affiliation) and
**suggest a real name** for each `Speaker N`. Combine with the voiceprint DB:
once a name is inferred for a voiceprint, it auto-applies to future meetings.
Implementation: regex/NER heuristics first ("I'm <Name>", "my name is <Name>",
"this is <Name>"), then an LLM pass over each speaker's longest early segments for
higher recall. Surface as *suggestions* the user confirms (don't auto-rename).

## Other follow-ups
- **Diarization over-count (+1)** on some single-pass multi-speaker clips (v4): consider
  post-hoc speaker merging or injecting expected-speaker count more forcefully.
- **expected_speakers hint** is currently passed via prompt `context_info` but the
  model doesn't honor it strongly — investigate stronger conditioning.
- **vLLM serving**: now unblocked (driver 580 / CUDA 13). bf16 doesn't fit the 9B
  weights + KV pool + audio encoder on a single 24GB 4090; fp8 fits but costs
  accuracy. Revisit on a larger GPU, or if an accuracy-neutral quant becomes viable.
- **Speaker naming**: enroll known voices → auto-label `Speaker N` with real names.

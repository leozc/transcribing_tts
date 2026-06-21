# TODO

## Cross-chunk speaker alignment (voice fingerprinting)
When long audio is chunked (`transcribe_longform.py`, and the chunked path in
`benchmark/run_testset_bf16.py`), each chunk is diarized independently, so
`Speaker 0/1/...` ids do **not** correspond across chunk boundaries. This inflates
the total speaker count (e.g. test-set v7: model reported 4 speakers for a 2-person
talk — a chunking artifact, not a single-pass error).

**Fix:** compute a voice fingerprint per speaker segment (e.g. **CAM++ / 3D-Speaker
embeddings**), cluster across all chunks, and remap chunk-local speaker ids to a
single global identity. This also enables named-speaker enrollment (the v2
voiceprint feature). Single-pass runs (≤ ~27 min) don't need this.

## Other follow-ups
- **Diarization over-count (+1)** on some single-pass multi-speaker clips (v4): consider
  post-hoc speaker merging or injecting expected-speaker count more forcefully.
- **expected_speakers hint** is currently passed via prompt `context_info` but the
  model doesn't honor it strongly — investigate stronger conditioning.
- **vLLM serving**: now unblocked (driver 580 / CUDA 13). bf16 doesn't fit the 9B
  weights + KV pool + audio encoder on a single 24GB 4090; fp8 fits but costs
  accuracy. Revisit on a larger GPU, or if an accuracy-neutral quant becomes viable.
- **Speaker naming**: enroll known voices → auto-label `Speaker N` with real names.

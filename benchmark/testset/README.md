# tts_serve test-set dataset

Evaluation set for VibeVoice-ASR transcription + diarization across languages,
speaker counts, and lengths. Defined in `manifest.json`; audio is **not** checked
in (rebuild it from sources).

## Build
```bash
python benchmark/testset/build_testset.py            # download + make 16k mono wavs
python benchmark/testset/build_testset.py --only v7_eric_schmidt
python benchmark/testset/build_testset.py --force    # re-download everything
```
(The `gd1_crescent_key` item is a private Google Drive sample and is not rebuildable without access.)

## Items

| id | lang | true spk | model spk | len | notes |
|----|------|---------:|----------:|-----|-------|
| v1_chinese | zh-en | 2 | 2 | 20m | CN/EN code-switching interview |
| v2_english | en | 2 | 2 | 20m | English conversation |
| v3_allin | en | 4 | 4 | 20m | All-In podcast, 4 hosts |
| v4_3people | en | 4 | **5** | 20m | ⚠️ over-counts by 1 (id kept "3people") |
| v5_2people | en | 2 | 2 | 20m | investor interview |
| v6_chinese_pure | zh | 1 | 1 | 20m | dense Chinese monologue |
| v7_eric_schmidt | en | 2 | — | **41m** | full long-form pass |
| gd1_crescent_key | en | 2 | **3** | 26m | ⚠️ private Drive; over-counts |

`true spk` = ground-truth speaker count; `model spk` = what VibeVoice produced.
Mismatches (v4, gd1) are the known diarization over-counting weakness.

## Run the set
```bash
# transcribe one item (path from manifest), e.g.
tts-serve transcribe benchmark/sample/v7_eric_schmidt_full_16k.wav \
  --name v7_eric_schmidt --out out/testset/v7_eric_schmidt
```

## Coverage
- **Languages:** pure Chinese, pure English, CN/EN code-switching.
- **Speakers:** 1, 2, 3, 4 (+ two over-count failure cases).
- **Length:** 20-min clips + one full 41-min long-form pass.
- **Sources:** YouTube (public, reproducible) + Google Drive (private, auth path).

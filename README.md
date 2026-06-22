# tts_serve — Meeting Transcription (MVP)

Self-hosted, offline meeting transcription on a single RTX 4090. Produces
speaker-attributed, timestamped transcripts (txt + SRT) from audio, using
**Microsoft VibeVoice-ASR** (ASR + diarization + timestamps in one model,
MIT license, native Chinese-English code-switching).

## Status: MVP (Phase 0 + Phase 1)

- **Phase 0 (benchmark gate): PASSED.** 20-min audio transcribes in ~168s on a
  4090 (**~7× realtime**), peak **21.4GB** VRAM. → 10 hr/day of audio needs only
  ~85 min/day of GPU. See `benchmark/BENCHMARK.md`.
- **Phase 1 (MVP pipeline): working.** Folder-watch → preprocess → transcribe →
  txt/SRT/segments, with sha256 dedup.

## Important: serving path

The plan assumed `vllm serve`, but the official vLLM image ships a **CUDA 12.9**
torch, while this host's driver (**535 / CUDA 12.2**) cannot run it
(`CUDA error 804: forward compatibility ... non supported HW`). We therefore run
VibeVoice-ASR via **transformers + torch cu121** natively (`src/tts_serve/asr.py`).
To use vLLM serving (for higher throughput / continuous batching), upgrade the
host NVIDIA driver to ≥ 555, then swap `VibeVoiceASR` for an HTTP client.

## Setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv torch==2.5.1 torchaudio==2.5.1 \
    --index-url https://download.pytorch.org/whl/cu121
uv pip install --python .venv -e ./VibeVoice        # the vibevoice package
uv pip install --python .venv -e .                  # this package
```

`./VibeVoice` is a clone of https://github.com/microsoft/VibeVoice (model code).
The 17GB model auto-downloads from HuggingFace on first run.

## CLI

Installed as `tts-serve` (entry point `tts_serve.cli:main`). Two subcommands:
`transcribe` (one source) and `watch` (folder worker).

```bash
# local file
tts-serve transcribe meeting.mp3

# YouTube (auto-downloaded via yt-dlp); bias with hotwords; clip for a quick test
tts-serve transcribe 'https://youtu.be/3Amlu4y94Ho' --hotwords "Chamath,Sacks" --clip 0-600

# S3 (auth via AWS cred chain / profile / explicit keys)
tts-serve transcribe s3://bucket/call.m4a --aws-profile work --out ./out

# Google Drive file or folder (auth: gcloud ADC or service account; see below)
tts-serve transcribe 'https://drive.google.com/file/d/<ID>/view'
tts-serve transcribe 'https://drive.google.com/drive/folders/<FID>'   # picks the lone media file

# pipe machine-readable JSON to other tools (logs go to stderr)
tts-serve transcribe call.wav --stdout json | jq '.segments[0]'
```

### Input — `SOURCE` is auto-detected
| Source | Example | Backend |
|---|---|---|
| Local file | `meeting.mp3`, `/abs/a.wav` | direct |
| YouTube | `https://youtu.be/…`, `…youtube.com/watch?v=…` | `yt-dlp` (+ `--cookies`) |
| Google Drive | `…/file/d/<ID>/…`, `…/drive/folders/<FID>`, `gdrive://<ID>` | Drive API / `gdown` |
| S3 | `s3://bucket/key.m4a` | `boto3` |
| Direct URL | `https://…/clip.m4a` | streamed download |

Common flags: `--out DIR`, `--stdout json|txt|srt`, `--hotwords "A,B"`,
`--speakers N`, `--reid` (voiceprint speaker re-id; fixes over-count & long-audio
cross-chunk drift — use with `--speakers`), `--names` (suggest real speaker names
from self-intros via LLM; needs `DEEPSEEK_API_KEY`), `--clip START-END` (seconds),
`--name`, `--model`, `--max-new-tokens`.

### Output
Canonical machine format is `segments.json`:
```json
{"source":"youtube:3Amlu4y94Ho","meeting_name":"…","model":"microsoft/VibeVoice-ASR",
 "duration_s":1200.0,"n_segments":104,"speakers":["Speaker 0","Speaker 1"],
 "segments":[{"start":0.0,"end":10.46,"speaker":"Speaker 0","text":"…"}]}
```
Default writes `transcript.txt` + `subtitle.srt` + `segments.json` + `meta.json` to
`--out` (default `out/<name>/`). `--stdout FMT` prints one format to stdout instead.

### Google Drive auth
Three modes (first that works wins):
1. **gcloud ADC** (recommended here — gcloud is installed). One-time:
   ```bash
   gcloud auth application-default login \
     --scopes=https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/cloud-platform
   ```
2. **Service account:** `--gdrive-credentials service-account.json` (share the file/folder with the SA email).
3. **Public link:** "Anyone with the link" → `gdown` fallback, no creds.

Install Drive/S3 backends: `uv pip install -e ".[gdrive,s3]"`.

## Folder worker (batch / watch)

```bash
tts-serve watch --once     # drain data/inbox/ once and exit
tts-serve watch            # keep polling data/inbox/
```

Drop audio into `data/inbox/` with an optional sidecar `<name>.json`:
```json
{"meeting_name": "weekly-sync", "hotwords": "Kubernetes,Istio,Alice,Bob", "expected_speakers": 4}
```
Outputs land in `data/results/<meeting>__<hash>/` (sha256 dedup).

## Tests

```bash
uv pip install -e .          # installs pytest config via pyproject
.venv/bin/python -m pytest   # 80 unit + adversarial tests, no GPU needed
```
Tests cover source classification, Drive/S3 URL parsing, segment
normalization, and transcript/SRT rendering — including adversarial cases
(malformed/truncated/fenced model output, null speakers, string timestamps,
private-folder auth errors).

## Layout

```
src/tts_serve/
  asr.py          # resident VibeVoice-ASR backend (model loaded once)
  postprocess.py  # segments -> readable txt + SRT
  pipeline.py     # folder-watch worker: preprocess -> transcribe -> write
VibeVoice/        # upstream model code (cloned)
benchmark/        # Phase-0 benchmark scripts, samples, results, BENCHMARK.md
data/{inbox,done,failed,results}/
```

## Roadmap (not in MVP)
- v1: FastAPI upload + Web UI + Postgres + MinIO; metadata at ingest.
- v2: CAM++ voiceprint auto-naming, LLM meeting summaries, Slack/email, multi-user, monitoring.

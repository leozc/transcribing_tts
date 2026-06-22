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
| Bilibili | `…bilibili.com/video/BV…`, `b23.tv/…` | `yt-dlp` (TLS-impersonate via `curl_cffi`; needs `--cookies` or a residential IP — Bilibili 412-blocks datacenter IPs) |
| Google Drive | `…/file/d/<ID>/…`, `…/drive/folders/<FID>`, `gdrive://<ID>` | Drive API / `gdown` |
| S3 | `s3://bucket/key.m4a` | `boto3` |
| Direct URL | `https://…/clip.m4a` | streamed download |

**Bilibili login** (datacenter IPs get HTTP 412; a logged-in session fixes it):
```bash
uv pip install -e ".[bilibili]"
python scripts/bili_login.py            # shows a QR -> scan with the Bilibili app
# -> writes data/bili_cookies.txt (gitignored). CLI: --cookies data/bili_cookies.txt
# The service worker auto-uses data/bili_cookies.txt (or $YT_COOKIES).
```

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

## Service (HTTP API)

Queue a transcription, poll it, download the artifact zip. Two processes share a
SQLite queue (`data/tasks.db`) + `data/tasks/<id>/`: a light **API** and a single
**GPU worker** that loads the model once and drains the queue serially.

```bash
uv pip install -e ".[service]"
tts-serve-worker &                       # resident GPU worker (loads model once)
TTS_SERVE_PORT=39999 tts-serve-api        # FastAPI on :39999
```

**Register once** to get a secret **`client_key`**. Send it as `X-Client-Key` to
enqueue and to **list your own jobs** — it's your authenticated identity (the body
`client_id` is just a label that must match it). Create also returns a per-task
**`pull_token`** — an unguessable capability that reaches that one task (header
`X-Task-Token` or `?token=`), handy for sharing a single result.

```bash
# 0. register -> {"client_id","client_key"}  (client_key shown once — SAVE IT)
curl -H 'content-type: application/json' -d '{"client_id":"alice"}' localhost:39999/v1/clients

# queue a file upload (multipart) -> {"task_id", "status", "pull_token"}
curl -H 'X-Client-Key: <client_key>' \
     -F file=@meeting.wav -F client_id=alice -F speakers=2 localhost:39999/v1/tasks/upload

# queue a URL (YouTube / Bilibili / Google Drive / S3 / http) — JSON
curl -H 'content-type: application/json' -H 'X-Client-Key: <client_key>' \
     -d '{"source":"https://youtu.be/<id>","client_id":"alice","clip":"0-600","names":true}' \
     localhost:39999/v1/tasks
# -> {"task_id":"...","status":"queued","pull_token":"<SAVE THIS>"}

# list YOUR OWN jobs (only yours)
curl -H 'X-Client-Key: <client_key>' localhost:39999/v1/tasks

# poll (with the pull_token, or your X-Client-Key)
curl -H 'X-Task-Token: <pull_token>' localhost:39999/v1/tasks/<task_id>

# download artifacts (zip of transcript.txt + subtitle.srt + segments.json + meta.json)
curl -H 'X-Task-Token: <pull_token>' -OJ localhost:39999/v1/tasks/<task_id>/artifact
```

Task options (form fields or JSON): `hotwords`, `speakers`, `reid`, `names`,
`clip`, `name`. Status lifecycle: `queued → downloading → preprocessing →
transcribing → postprocessing → done | failed | cancelled`. **One task runs at a
time** (single resident GPU worker, FIFO; the store enforces ≤1 `running`).
**Access control:** enqueue and "list my jobs" require a registered client's
`X-Client-Key`; a single task is reachable by its owner (`X-Client-Key`) or its
per-task `pull_token` (`X-Task-Token`) — fail-closed, so a task with neither match
is inaccessible. `GET /v1/tasks` with a client key returns **only that client's
tasks**; the cross-client admin view (all tasks, `/v1/queue`) is gated by the
`TTS_SERVE_API_KEY` bearer when set (open otherwise, for localhost dev). Client keys
are stored only as SHA-256 hashes. **Storage:** durable SQLite (`data/tasks.db`, WAL)
— schema auto-migrates on `init()`; terminal tasks older than `TTS_SERVE_RETENTION_DAYS`
(default 7) are purged on startup + hourly by the worker.

All endpoints are typed (Pydantic) → `/openapi.json` (OpenAPI 3.1) yields a typed
generated client via `openapi-generator-cli` / `openapi-python-client` / `openapi-typescript`.

**Endpoints**
- `POST /v1/clients` — register a `client_id` → `{client_id, client_key}` (key shown once)
- `POST /v1/tasks` — queue a URL (JSON `{source, ...opts}` + `X-Client-Key`) → `{task_id, pull_token}`
- `POST /v1/tasks/upload` — queue a file (multipart `file=@audio` + opts + `X-Client-Key`) → `{task_id, pull_token}`
- `GET /v1/tasks/{id}` — status/stage; `GET /v1/tasks` — **your jobs** (`X-Client-Key`) or all (admin)
- `GET /v1/tasks/{id}/artifact` — zip (200 done / 409 not-ready / 404)
- `GET /v1/queue` — admin: what's running + the pending queue + counts
- `DELETE /v1/tasks/{id}` — remove a queued/done/failed task (409 if running)
- `POST /v1/tasks/{id}/retry` — requeue a failed/cancelled task
- `GET /agent_info` — concise, agent-facing API guide; full spec at `/openapi.json` + `/docs`
- `GET /healthz`

### For agents / programmatic use

**TL;DR** — async transcription: **register once → get a secret key → enqueue → poll
until `done` → download the artifact zip.** A single FIFO GPU worker runs one task at
a time. The service self-describes at `GET /agent_info` (prose) and `GET /openapi.json`
(machine spec). *(This flow was validated by handing a fresh agent nothing but the base
URL — it self-served the entire loop from `/agent_info` alone.)*

> **Auth is intentionally NOT in the OpenAPI `securitySchemes`.** The headers appear as
> *optional* params in the spec — that is misleading; **send them**. Read this section
> for the contract, not `/openapi.json` alone. Two credentials, different scopes:
> - **`X-Client-Key`** — your account identity. Required to **enqueue** and to **list
>   your own jobs**; also grants access to any task you own.
> - **`pull_token`** (sent as header `X-Task-Token` or `?token=`) — a single-task
>   capability, returned **only** in the create response. Use it to share/poll one task
>   without handing over your account key.
> Header names are case-insensitive; the secret values are exact.

```bash
B=http://localhost:39999

# 0. Register ONCE -> 201 {client_id, client_key}. client_key is shown only here and
#    cannot be re-fetched — PERSIST IT NOW. Re-registering the same id returns 409.
KEY=$(curl -s -H 'content-type: application/json' -d '{"client_id":"agent-42"}' \
        $B/v1/clients | jq -r .client_key)

# 1. Enqueue -> 200 (not 201) {task_id, status, pull_token}. The body client_id MUST
#    equal the id that owns your key (else 403). Save the pull_token — only returned here.
read TID TOKEN < <(curl -s -H 'content-type: application/json' -H "X-Client-Key: $KEY" \
  -d '{"source":"https://youtu.be/ID","client_id":"agent-42","clip":"0-600"}' \
  $B/v1/tasks | jq -r '.task_id + " " + .pull_token')

# 2. Poll (X-Client-Key owner, OR X-Task-Token). status: queued->running->done|failed|cancelled
curl -s -H "X-Client-Key: $KEY" $B/v1/tasks/$TID            # poll with backoff (jobs serialize)

# 3. When status=='done', download the zip (transcript.txt, subtitle.srt, segments.json, meta.json)
curl -s -H "X-Task-Token: $TOKEN" -OJ $B/v1/tasks/$TID/artifact

# list ONLY your jobs (always send the key — don't rely on anonymous listing)
curl -s -H "X-Client-Key: $KEY" $B/v1/tasks
```

**Options** (JSON fields or upload form fields): `hotwords` (comma list), `speakers`
(int), `reid` (bool, pair with `speakers`), `names` (bool, LLM name guess from
self-intros), `clip` (`"START-END"` seconds, e.g. `"0-600"`), `name` (label).

**Status map** — branch on the code, don't treat non-2xx uniformly:

| code | meaning |
|------|---------|
| `200` | success (incl. task **create/upload/retry**) |
| `201` | client registered |
| `401` | missing/garbage `X-Client-Key` |
| `403` | valid key but not the task owner, **or** body `client_id` ≠ your key |
| `404` | unknown task id |
| `409` | `client_id` already taken / delete-while-running / artifact not ready |
| `422` | request validation |

**Gotchas worth stating up front** (each cost a real agent in testing):
1. **Save `client_key` and `pull_token` the instant you see them** — both are
   unrecoverable, and a burned `client_id` can't be re-registered (409).
2. The spec marks auth headers optional — **ignore that and send them**; expect
   `401`/`403` (undocumented in the spec) on omission/mismatch.
3. Cross-client listing/`/v1/queue` are **open in unconfigured dev but locked in prod**
   (when `TTS_SERVE_API_KEY` is set) — never build on seeing other clients' tasks; always
   scope `GET /v1/tasks` with your key.
4. **Track the `task_id`s you create** rather than rediscovering them via the list.
5. No documented upload-size/format/retention limits — poll with backoff and download
   artifacts promptly once `done` (they're purged after `TTS_SERVE_RETENTION_DAYS`).

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

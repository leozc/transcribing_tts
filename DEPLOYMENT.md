# Deployment architecture

Single-machine, single-GPU, two long-running processes that share a SQLite queue
and a `data/` directory. No external services (no Redis/Postgres) — matches the
barebone goal.

```
                 ┌───────────────────────────── host (RTX 4090) ─────────────────────────────┐
   clients ──▶   │  tts-serve-api (FastAPI/uvicorn, :8088)        tts-serve-worker (1 process) │
  (HTTP)         │    • POST /v1/tasks  → enqueue, return id        • owns the GPU              │
                 │    • GET  /v1/tasks/{id}  (poll)                 • loads VibeVoice once (~17GB)│
                 │    • GET  .../artifact    (zip)                  • claims 1 task at a time    │
                 │    • /v1/queue, DELETE, retry, /agent_info       • resolve→16k→ASR→reid→names │
                 │            │                                         │  write results         │
                 │            └──────────────┬──────────────────────────┘                       │
                 │              data/tasks.db (SQLite, WAL)   data/tasks/<id>/{input.*,results/} │
                 └───────────────────────────────────────────────────────────────────────────────┘
```

## Processes
- **API** (`tts-serve-api`): light, no GPU, stateless except the shared DB/FS. Enqueues
  tasks and serves status/artifacts. Safe to run **N replicas** behind a reverse proxy
  (they all share the same SQLite + `data/`).
- **Worker** (`tts-serve-worker`): exactly **one** process. Owns the GPU and the model
  (resident or on-demand — see *Model allocation*); drains the FIFO queue. **Global
  concurrency = 1** is enforced in the store
  (`claim_next_queued` only claims when no task is `running`), so even an accidental
  second worker can't run two tasks at once.
- **Shared state**: `data/tasks.db` (WAL = concurrent API reads + worker writes) and
  `data/tasks/<id>/` for input + `results/`. Both processes must see the same `data/`
  (same host, or a shared volume).

## Running it (host / venv — current)
```bash
uv pip install -e ".[service]"
tts-serve-worker          # process 1 (GPU)
TTS_SERVE_PORT=8088 tts-serve-api   # process 2
```
Recommended supervision: **systemd user services** (linger already enabled on this host),
e.g. `~/.config/systemd/user/tts-worker.service` and `tts-api.service` with
`Restart=on-failure`. Env: `TTS_SERVE_API_KEY` (auth), `DEEPSEEK_API_KEY` (names),
`TTS_SERVE_DATA` (data dir), AWS/`GDRIVE_CREDENTIALS` for cloud sources.

> The venv is **uv-managed** (`uv venv` + `uv pip install`; no pip inside it). Use
> `VIRTUAL_ENV=.venv uv pip install ...` to add packages. Package build backend is
> setuptools (`pyproject.toml`); there is no `uv.lock` (uv is the installer, not a
> locked project manager).

## Binary distribution (standalone API, no venv)
The **API** has no ML deps (FastAPI + uvicorn + SQLite + Pydantic), so it ships as a
single self-contained executable — handy for deploying the front end without a Python
environment:
```bash
VIRTUAL_ENV=.venv uv pip install pyinstaller   # one-time
bash scripts/build_api_binary.sh               # -> dist/tts-serve-api  (~39 MB)
TTS_SERVE_PORT=8088 TTS_SERVE_DATA=/srv/tts ./dist/tts-serve-api   # runs with NO venv
```
The build follows the API's real import graph and **excludes** torch/transformers/
yt-dlp/etc. (using `--collect-submodules tts_serve` instead pulls in `asr.py`→torch and
bloats the binary to ~3 GB). The **GPU worker is intentionally not binarized** — it needs
torch/CUDA and downloads the ~17 GB model at runtime — so run it from the installed env
(`tts-serve-worker`) on the same host/`data/`. Verified: the binary runs under `env -i`
(no PATH/venv) and drives the full register → upload → poll → artifact flow.

## Run as a service (systemd user units) — recommended
`scripts/install_systemd.sh` writes two **user** units (linger is on, so they survive
logout/reboot): `tts-api` runs the standalone **binary**, `tts-worker` runs the venv
GPU worker. Both `Restart=on-failure`.
```bash
bash scripts/build_api_binary.sh          # produce dist/tts-serve-api
bash scripts/install_systemd.sh           # install + daemon-reload (data=./data, port 8088)
systemctl --user enable --now tts-api tts-worker
systemctl --user status tts-api tts-worker
```
Secrets/config (optional): put `DEEPSEEK_API_KEY=…`, `AWS_*`, `YT_COOKIES=…`,
`TTS_SERVE_API_KEY=…` in `./.env_service` (gitignored; read via `EnvironmentFile=-`).
Stop/remove: `systemctl --user disable --now tts-worker tts-api` (this frees the GPU).

## Model allocation (GPU) — resident vs on-demand
The worker can keep the model **resident** (loaded at startup, hot forever) or load it
**on demand** and free the GPU when idle — set `TTS_SERVE_IDLE_UNLOAD` (seconds; `0` =
resident):

| | resident (`=0`) | on-demand (`=600`, the unit default) |
|---|---|---|
| VRAM when idle | ~17 GB pinned 24/7 | freed (only ~0.5 GB CUDA context remains) |
| first-task latency | ~0 (always hot) | + model load (≈4 s warm / page-cached, longer cold) |
| best for | a **dedicated** GPU box | a **shared** GPU (reclaim it between bursts) |

This is async, latency-tolerant batch work (~10×1 h meetings/day ≈ ~75 min GPU/day),
so the cold-start cost is negligible and on-demand is the default here. The worker loads
the model on the first queued task (status shows `stage=loading_model`), processes the
FIFO queue, and after `TTS_SERVE_IDLE_UNLOAD` seconds with an empty queue it drops the
model and calls `empty_cache()` — freeing the ~17 GB of weights. A small CUDA context
(~hundreds of MB) lingers until the process exits; if you need the GPU **100%** free,
recycle the worker process on idle instead (a follow-up). Verified on this host: idle
GPU **1 MiB** → task loads the model → idle-unload returns it to **~518 MiB**.

## Logging (for debugging)
Both processes use `service/logconf.py`: component-tagged, leveled lines to **stdout**
(captured by journald) **and** a rotating file under `<DATA>/logs/{api,worker}.log`
(10 MB × 5). Level via `TTS_SERVE_LOG_LEVEL` (default INFO; set `DEBUG` for verbose);
disable the file with `TTS_SERVE_LOG_DIR=none` or redirect with `TTS_SERVE_LOG_DIR=<dir>`.
```bash
journalctl --user -u tts-worker -f     # live worker log (claim -> stages -> done/fail + timing)
journalctl --user -u tts-api -f        # live API log (startup config, enqueue/delete/retry, access)
tail -f data/logs/worker.log           # same, on disk (works for the standalone binary too)
```
The worker logs each task's lifecycle — `claimed <id> (client=… type=…)`, every
`stage=…` transition, `DONE: N segs, speakers=…`, `finished in Ns`, or `FAILED … ` with
a full traceback (`exc_info`). uvicorn's access/error logs are reformatted through the
same handler so everything is one consistent stream.

## State on disk — where things live
- **SQLite queue/state**: `<DATA>/tasks.db` (+ `-wal`, `-shm` from WAL mode). Default
  `<repo>/data/tasks.db`; override the whole data root with `TTS_SERVE_DATA`.
- **Per-task files**: `<DATA>/tasks/<id>/` — `input.*` (upload/download) + `results/`
  (`transcript.txt`, `subtitle.srt`, `segments.json`, `meta.json`).
- **Logs**: `<DATA>/logs/{api,worker}.log`. **Registered clients**: a `clients` table in
  the same DB (only SHA-256 key hashes). Terminal tasks are purged after
  `TTS_SERVE_RETENTION_DAYS` (default 7).

## Reliability
- WAL SQLite handles concurrent access; `BEGIN IMMEDIATE` makes the claim atomic.
- **Crash recovery**: worker calls `reclaim_stale()` on startup → any task left `running`
  by a crashed worker is re-queued (safe for the single-worker model).
- `Restart=on-failure` (systemd) restarts a crashed worker; the resident model reloads in ~4s.

## Security
- Optional bearer token (`TTS_SERVE_API_KEY`). Bind the API to localhost or put it
  behind nginx/Caddy for TLS. The worker has no inbound network surface.

## Scaling (when needed)
- **Vertical**: bigger GPU → larger batches / vLLM (driver already supports CUDA 13).
- **Horizontal, multi-GPU (same host)**: relax the guard to concurrency = N (claim when
  `running < N`) and run N workers, each pinned via `CUDA_VISIBLE_DEVICES`. API/queue unchanged.
- **Multi-host**: swap the SQLite store for Postgres or Redis/RQ — `service/store.py` is the
  only module that changes; API and worker keep the same interface.

## Follow-ups
- Containerize (CUDA Dockerfile + `docker-compose.yml`: api + GPU worker) — see TODO.md.
- Artifact retention/cleanup (cron or TTL); webhooks/SSE for push status.

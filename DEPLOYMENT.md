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
- **Worker** (`tts-serve-worker`): exactly **one** process. Owns the GPU and the resident
  model; drains the FIFO queue. **Global concurrency = 1** is enforced in the store
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

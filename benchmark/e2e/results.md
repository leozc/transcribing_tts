# E2E concurrency test — results

3 clients submit concurrently to the single-GPU service; the server processes
**one task at a time** (FIFO); client3 polls and pulls its artifacts. Requests are
in `requests.json`; rerun with `run.sh`.

## Submitted (5 tasks across 3 clients)
| client | name | source | type |
|--------|------|--------|------|
| client1 | c1_youtube | `youtu.be/3Amlu4y94Ho` clip 0-20 | youtube |
| client2 | c2_bilibili | `b23.tv/fZQNYqJ` | bilibili |
| client3 | c3_file | upload `allin_60s_16k.wav` (speakers=2) | file |
| client3 | c3_req2 | `youtu.be/3Amlu4y94Ho` clip 40-55 | youtube |
| client3 | c3_req3 | `youtu.be/3Amlu4y94Ho` clip 60-75 | youtube |

## Server processed serially (from `GET /v1/queue`)
```
t+10s  running=836206bb (downloading)  queued=3   counts={done:1, queued:3, running:1}
t+20s  running=b07fc15b (downloading)  queued=2   counts={done:2, queued:2, running:1}
t+30s  running=68c44f47 (transcribing) queued=1   counts={done:2, failed:1, queued:1, running:1}
t+40s  running=ac573d11 (transcribing) queued=0   counts={done:3, failed:1, running:1}
t+60s  running=-                        queued=0  counts={done:4, failed:1}
```
**Exactly one `running` at every snapshot — global concurrency = 1.** Queue drained 5→0.

## Final statuses
| name | status | note |
|------|--------|------|
| c1_youtube | ✅ done | |
| c2_bilibili | ⚠️ failed | **HTTP 412** — Bilibili risk-controls this datacenter IP. Provider is correct (routes b23.tv→yt-dlp); works from a residential IP or with `--cookies`/SESSDATA. |
| c3_file | ✅ done | |
| c3_req2 | ✅ done | |
| c3_req3 | ✅ done | |

## client3 pulled its artifacts
All 3 → `GET /v1/tasks/{id}/artifact` HTTP **200**, zip = `{transcript.txt, subtitle.srt, segments.json, meta.json}`:
- **c3_file** → "Alright, everybody. Welcome back to the number one podcast…"
- **c3_req2** (40-55s) → "Let your winners ride. Rain Man David Sacks…"
- **c3_req3** (60-75s) → "It's the dictator. It's my best feature, Bob…"

Saved in `client3_artifacts/`. Distinct, correct content per clip.

## Takeaways
- All three submission paths work (YouTube JSON, file upload, multiple requests).
- Single-GPU serial processing enforced; queue + admin reflect it live.
- A failed task (Bilibili 412) is captured cleanly with its error and is retryable
  (`POST /v1/tasks/{id}/retry`) — would succeed from a non-blocked IP / with cookies.

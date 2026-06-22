# E2E concurrency test — results

3 clients each **register** (→ secret `client_key`), then submit concurrently; the
single-GPU server processes **one task at a time** (FIFO); a client lists/fetches its
own jobs with its `X-Client-Key`, and per-task `pull_token`s gate individual pulls.
Requests in `requests.json`; rerun with `run.sh`.

## "List my jobs" — client3 sees ONLY its own (3 of the 5 total)
```
# GET /v1/tasks  (header X-Client-Key: <client3 key>)
client3 sees 3 jobs: ['client3', 'client3', 'client3']
```
client1/client2's tasks are invisible to client3 — the key authenticates the scope.

## Submitted (5 tasks across 3 clients) — each create returned a pull_token
| client | name | source | type |
|--------|------|--------|------|
| client1 | c1_youtube | `youtu.be/3Amlu4y94Ho` clip 0-20 | youtube |
| client2 | c2_bilibili | `b23.tv/fZQNYqJ` clip 0-60 | **bilibili** |
| client3 | c3_file | upload `allin_60s_16k.wav` (speakers=2) | file |
| client3 | c3_req2 | `youtu.be/3Amlu4y94Ho` clip 40-55 | youtube |
| client3 | c3_req3 | `youtu.be/3Amlu4y94Ho` clip 60-75 | youtube |

## Server processed serially (from `GET /v1/queue`, admin view)
```
running -         queued 5   {queued:5}
running 3b96b185  queued 4   {queued:4, running:1}
running e72cf718  queued 3   {done:1, queued:3, running:1}
running d9717488  queued 2   {done:2, queued:2, running:1}
running 44197ee9  queued 1   {done:3, queued:1, running:1}
running 076302ce  queued 0   {done:4, running:1}
running -         queued 0   {done:5}
```
**Exactly one `running` at every snapshot — global concurrency = 1.** Queue drained 5→0.

## Final statuses — all done (Bilibili now succeeds via login cookies)
| client_id | type | status | name |
|-----------|------|--------|------|
| client1 | youtube | ✅ done | c1_youtube |
| client2 | bilibili | ✅ done | c2_bilibili |
| client3 | file | ✅ done | c3_file |
| client3 | youtube | ✅ done | c3_req2 |
| client3 | youtube | ✅ done | c3_req3 |

Bilibili transcript (CN/EN code-switching), source `bilibili:bili_BV1PfjC66EMZ_p1`:
> [00s] 西方媒体的洗脑包到底"喂"藏了多少没见过世面的美国人？今天这位老外主角是个地道的美国黑人博主…
> [41s] Someone in the comment section had the audacity to ask me, did I see…

## client3 pulled its 3 artifacts (with each task's pull_token)
All `GET /v1/tasks/{id}/artifact` (header `X-Task-Token: <pull_token>`) → HTTP **200**,
zip = `{transcript.txt, subtitle.srt, segments.json, meta.json}`. Saved in `client3_artifacts/`.

## Takeaways
- All four submission paths work: YouTube, **Bilibili** (logged-in cookies clear the 412),
  file upload, and a client firing multiple requests.
- Single-GPU serial processing enforced; `/v1/queue` (admin) reflects it live.
- **Clients see their own jobs**: `GET /v1/tasks` with `X-Client-Key` returns only that
  client's tasks (authenticated identity — `client_id` alone can't be spoofed to list).
- A single task is reachable by its owner (`X-Client-Key`) or its per-task `pull_token`.

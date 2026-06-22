"""FastAPI front end: queue a transcription, poll it, download the artifact zip.

    POST /v1/tasks          JSON {source, ...options}              -> TaskRef
    POST /v1/tasks/upload   multipart file=@audio + form options   -> TaskRef
    GET  /v1/tasks/{id}                                            -> TaskStatus
    GET  /v1/tasks/{id}/artifact   zip (200 done / 409 / 404)
    GET  /v1/tasks          recent                                 -> TaskList
    GET  /v1/queue          running + pending + counts             -> QueueStatus
    DELETE /v1/tasks/{id}   remove queued/done/failed (409 running)-> DeleteResult
    POST /v1/tasks/{id}/retry  requeue failed/cancelled            -> TaskRef
    GET  /agent_info        agent-facing guide ; GET /healthz ; /openapi.json ; /docs

Typed via Pydantic so /openapi.json yields a real typed client. The GPU worker
(service/worker.py) does the transcription. Optional bearer auth via TTS_SERVE_API_KEY.
"""
from __future__ import annotations

import hmac
import io
import os
import secrets
import zipfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from tts_serve.service import store
from tts_serve.service.models import (
    CreateTaskRequest, DeleteResult, Health, QueuedItem, QueueStatus,
    RunningInfo, TaskList, TaskRef, TaskStatus,
)

_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".mp4", ".webm", ".mov", ".mkv", ".aac"}

app = FastAPI(title="tts_serve", version="0.1.0",
              description="Self-hosted meeting transcription — queue / poll / artifact.")


@app.on_event("startup")
def _startup() -> None:
    store.init()
    # lifecycle maintenance: purge terminal tasks older than retention (0 disables)
    days = float(os.environ.get("TTS_SERVE_RETENTION_DAYS", "7"))
    n = store.purge_old(days)
    if n:
        print(f"[api] purged {n} task(s) older than {days}d", flush=True)


async def _auth(request: Request) -> None:
    key = os.environ.get("TTS_SERVE_API_KEY")
    if key and request.headers.get("authorization", "") != f"Bearer {key}":
        raise HTTPException(401, "missing/invalid bearer token")


def _is_admin(request: Request) -> bool:
    """Admin = presented the configured bearer key. Used to gate cross-client
    listing/queue. If no key is configured, admin views are open (dev/localhost)."""
    key = os.environ.get("TTS_SERVE_API_KEY")
    return (not key) or request.headers.get("authorization", "") == f"Bearer {key}"


def _pull_token(x_task_token: str | None = Header(default=None),
                token: str | None = Query(default=None)) -> str | None:
    """The per-task capability the creator received. Declared as Header+Query so it
    shows up in /openapi.json (generated clients can send it)."""
    return x_task_token or token


def _owned(tid: str, token: str | None) -> dict:
    """Fetch a task and enforce the per-task pull token. Fails CLOSED: a task with
    no token (e.g. a legacy/migrated row) is inaccessible. 404 unknown, 403 otherwise.
    Constant-time compare so the token can't be guessed by timing."""
    t = store.get(tid)
    if not t:
        raise HTTPException(404, "unknown task id")
    stored = t.get("token")
    if not stored or not token or not hmac.compare_digest(str(token), str(stored)):
        raise HTTPException(403, "missing/invalid task token (send X-Task-Token from create)")
    return t


def _save_upload(filename: str, data: bytes, opts: dict, client_id: str, token: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in _AUDIO_EXTS:
        ext = ".bin"
    tid = store.create("", "file", opts, client_id=client_id, token=token)
    dst = store.task_dir(tid) / f"input{ext}"
    dst.write_bytes(data)
    store.update(tid, source=str(dst))
    return tid


@app.get("/healthz", response_model=Health)
def healthz() -> Health:
    return Health(ok=True)


@app.post("/v1/tasks", response_model=TaskRef)
def create_task(req: CreateTaskRequest, _=Depends(_auth)) -> TaskRef:
    from tts_serve.sources import classify
    token = secrets.token_urlsafe(24)
    tid = store.create(req.source, classify(req.source), req.options(),
                       client_id=req.client_id, token=token)
    return TaskRef(task_id=tid, status="queued", pull_token=token)


@app.post("/v1/tasks/upload", response_model=TaskRef)
async def upload_task(
    file: UploadFile = File(...),
    client_id: str = Form(...),
    hotwords: str | None = Form(None),
    speakers: int | None = Form(None),
    reid: bool = Form(False),
    names: bool = Form(False),
    clip: str | None = Form(None),
    name: str | None = Form(None),
    _=Depends(_auth),
) -> TaskRef:
    client_id = (client_id or "").strip()
    if not client_id:  # 422 here, else manual model construction would 500
        raise HTTPException(422, "client_id must not be blank")
    req = CreateTaskRequest(source="upload", client_id=client_id, hotwords=hotwords,
                            speakers=speakers, reid=reid, names=names, clip=clip, name=name)
    token = secrets.token_urlsafe(24)
    # use the VALIDATED/stripped client_id, not the raw form value
    tid = _save_upload(file.filename, await file.read(), req.options(), req.client_id, token)
    return TaskRef(task_id=tid, status="queued", pull_token=token)


@app.get("/v1/tasks/{tid}", response_model=TaskStatus)
def get_task(tid: str, token: str | None = Depends(_pull_token), _=Depends(_auth)) -> TaskStatus:
    t = _owned(tid, token)
    return TaskStatus(task_id=t["id"], status=t["status"], stage=t["stage"],
                      client_id=t["client_id"], source_type=t["source_type"], error=t["error"],
                      created_at=t["created_at"], updated_at=t["updated_at"],
                      options=t["options"])


@app.get("/v1/tasks", response_model=TaskList)
def list_tasks(request: Request, client_id: str | None = None, _=Depends(_auth)) -> TaskList:
    # admin-only cross-task listing (a regular client tracks its own task ids + tokens)
    if not _is_admin(request):
        raise HTTPException(403, "admin only (requires TTS_SERVE_API_KEY bearer)")
    return TaskList(tasks=[QueuedItem(**r) for r in store.list_tasks(client_id=client_id)])


@app.get("/v1/queue", response_model=QueueStatus)
def queue(request: Request, _=Depends(_auth)) -> QueueStatus:
    if not _is_admin(request):
        raise HTTPException(403, "admin only (requires TTS_SERVE_API_KEY bearer)")
    r = store.running_task()
    return QueueStatus(
        running=RunningInfo(task_id=r["id"], stage=r["stage"],
                            source_type=r["source_type"], updated_at=r["updated_at"]) if r else None,
        queued=[QueuedItem(**q) for q in store.list_tasks(status="queued")],
        counts=store.counts(),
    )


@app.delete("/v1/tasks/{tid}", response_model=DeleteResult)
def delete_task(tid: str, token: str | None = Depends(_pull_token), _=Depends(_auth)) -> DeleteResult:
    t = _owned(tid, token)
    if t["status"] == "running":
        raise HTTPException(409, "cannot remove a running task")
    if not store.delete(tid):  # raced to 'running' between the check and here
        raise HTTPException(409, "cannot remove a running task")
    return DeleteResult(deleted=tid, was=t["status"])


@app.post("/v1/tasks/{tid}/retry", response_model=TaskRef)
def retry_task(tid: str, token: str | None = Depends(_pull_token), _=Depends(_auth)) -> TaskRef:
    t = _owned(tid, token)
    if t["status"] not in ("failed", "cancelled"):
        raise HTTPException(409, f"can only retry failed/cancelled (status={t['status']})")
    store.update(tid, status="queued", stage=None, error=None)
    return TaskRef(task_id=tid, status="queued")


@app.get("/v1/tasks/{tid}/artifact",
         responses={200: {"content": {"application/zip": {}}}, 409: {}, 404: {}})
def get_artifact(tid: str, token: str | None = Depends(_pull_token), _=Depends(_auth)):
    t = _owned(tid, token)
    if t["status"] != "done":
        raise HTTPException(409, f"task not done (status={t['status']})")
    rdir = store.results_dir(tid)
    files = [p for p in rdir.glob("*") if p.is_file()] if rdir.exists() else []
    if not files:
        raise HTTPException(404, "no artifacts found")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, arcname=p.name)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{tid}.zip"'})


@app.get("/agent_info")
def agent_info() -> dict:
    """Concise, agent-facing guide. Full machine spec: /openapi.json and /docs."""
    return {
        "service": "tts_serve",
        "summary": "Transcribe audio/video (file, YouTube, Bilibili, Google Drive, S3, URL) into a "
                   "speaker-attributed, timestamped transcript. Async: queue a task, poll, "
                   "download a zip of artifacts.",
        "workflow": [
            "1. POST /v1/tasks (JSON {source, client_id}) for a URL, or POST /v1/tasks/upload "
            "(multipart file + client_id) -> {task_id, pull_token}. SAVE the pull_token: it is "
            "returned only here and is required for every later call about this task.",
            "2. GET /v1/tasks/{task_id} with the token (header 'X-Task-Token: <pull_token>' or "
            "?token=<pull_token>); poll 'status' until 'done' (stop on 'failed'/'cancelled'); "
            "'stage' shows progress while status=='running'",
            "3. GET /v1/tasks/{task_id}/artifact (same token) -> zip "
            "(transcript.txt, subtitle.srt, segments.json, meta.json)",
        ],
        "identity": "client_id (required at enqueue) is attribution only — it records WHO enqueued "
                    "and is not a secret. ACCESS to poll/pull/delete/retry a task requires the "
                    "per-task pull_token returned at create (send as 'X-Task-Token' header or "
                    "?token=). Unknown/no-token tasks are inaccessible (fail closed).",
        "concurrency": "One task runs at a time (single resident GPU worker, FIFO queue).",
        "status_values": ["queued", "running", "done", "failed", "cancelled"],
        "stage_values": ["downloading", "preprocessing", "transcribing", "postprocessing", "done"],
        "task_options": {
            "hotwords": "comma-separated names/terms to bias ASR",
            "speakers": "int, expected speaker count (improves diarization / re-id)",
            "reid": "bool, voiceprint speaker re-identification (use with speakers)",
            "names": "bool, suggest real speaker names from self-intros (LLM)",
            "clip": "START-END seconds, e.g. '0-600'",
            "name": "meeting name (default derived from source)",
        },
        "endpoints": [
            {"method": "POST", "path": "/v1/tasks", "body": "JSON CreateTaskRequest {source, client_id, ...options}",
             "returns": "{task_id, status, pull_token}",
             "example": "curl -H 'content-type: application/json' -d '{\"source\":\"https://youtu.be/ID\",\"client_id\":\"alice\",\"clip\":\"0-600\",\"names\":true}' <base>/v1/tasks"},
            {"method": "POST", "path": "/v1/tasks/upload", "body": "multipart file=@audio + client_id + option form fields",
             "returns": "{task_id, status, pull_token}",
             "example": "curl -F file=@meeting.wav -F client_id=alice -F speakers=2 <base>/v1/tasks/upload"},
            {"method": "GET", "path": "/v1/tasks/{id}", "returns": "TaskStatus (needs X-Task-Token)"},
            {"method": "GET", "path": "/v1/tasks/{id}/artifact", "returns": "application/zip (200/409/404; needs X-Task-Token)"},
            {"method": "GET", "path": "/v1/tasks", "returns": "TaskList (admin: requires bearer)"},
            {"method": "GET", "path": "/v1/queue", "returns": "QueueStatus (admin: requires bearer)"},
            {"method": "DELETE", "path": "/v1/tasks/{id}", "returns": "remove queued/done/failed (needs X-Task-Token; 409 if running)"},
            {"method": "POST", "path": "/v1/tasks/{id}/retry", "returns": "requeue failed/cancelled (needs X-Task-Token)"},
        ],
        "auth": "Per-task pull_token (X-Task-Token) gates task access. Admin listing (/v1/tasks, "
                "/v1/queue) requires the TTS_SERVE_API_KEY bearer when configured.",
        "spec": {"openapi": "/openapi.json", "swagger_ui": "/docs"},
    }


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=os.environ.get("TTS_SERVE_HOST", "0.0.0.0"),
                port=int(os.environ.get("TTS_SERVE_PORT", "8080")))


if __name__ == "__main__":
    main()

"""FastAPI front end: queue a transcription, poll it, download the artifact zip.

    POST /v1/clients        JSON {client_id}  -> ClientCredentials {client_id, client_key}
    POST /v1/tasks          JSON {source, client_id} + X-Client-Key  -> TaskRef
    POST /v1/tasks/upload   multipart file + client_id + X-Client-Key -> TaskRef
    GET  /v1/tasks/{id}                                            -> TaskStatus
    GET  /v1/tasks/{id}/artifact   zip (200 done / 409 / 404)
    GET  /v1/tasks          your tasks (X-Client-Key) / all (admin) -> TaskList
    GET  /v1/queue          running + pending + counts             -> QueueStatus
    DELETE /v1/tasks/{id}   remove queued/done/failed (409 running)-> DeleteResult
    POST /v1/tasks/{id}/retry  requeue failed/cancelled            -> TaskRef
    GET  /agent_info        agent-facing guide ; GET /healthz ; /openapi.json ; /docs

Access model: a registered client's X-Client-Key authenticates enqueue and lists that
client's own tasks; a single task is reachable by its owner (X-Client-Key) or its
per-task pull_token (X-Task-Token). Typed via Pydantic so /openapi.json yields a real
typed client. The GPU worker (service/worker.py) does the transcription. Optional
global bearer via TTS_SERVE_API_KEY (also gates cross-client admin views).
"""
from __future__ import annotations

import hmac
import io
import logging
import os
import secrets
import zipfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from tts_serve.service import store
from tts_serve.service.logconf import setup_logging
from tts_serve.service.models import (
    ClientCreate, ClientCredentials, CreateTaskRequest, DeleteResult, Health,
    QueuedItem, QueueStatus, RunningInfo, TaskList, TaskRef, TaskStatus,
)

_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".mp4", ".webm", ".mov", ".mkv", ".aac"}

log = logging.getLogger("tts_serve.api")

app = FastAPI(title="tts_serve", version="0.1.0",
              description="Self-hosted meeting transcription — queue / poll / artifact.")


@app.on_event("startup")
def _startup() -> None:
    setup_logging("api")
    store.init()
    auth_on = bool(os.environ.get("TTS_SERVE_API_KEY"))
    days = float(os.environ.get("TTS_SERVE_RETENTION_DAYS", "7"))
    log.info("api starting | data=%s db=%s admin_bearer=%s retention=%.0fd",
             store.DATA, store.DB, "on" if auth_on else "OFF(dev-open)", days)
    # lifecycle maintenance: purge terminal tasks older than retention (0 disables)
    n = store.purge_old(days)
    if n:
        log.info("purged %d task(s) older than %.0fd on startup", n, days)


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


def _client_auth(x_client_key: str | None = Header(default=None)) -> str | None:
    """Resolve X-Client-Key (issued by POST /v1/clients) to the authenticated
    client_id, or None if absent/invalid. Declared as a Header so it surfaces in
    /openapi.json. This is the identity that lets a client list + fetch its own
    tasks — unlike the body client_id, it cannot be spoofed."""
    return store.client_for_key(x_client_key)


def _require_client(authed: str | None, declared: str | None) -> str:
    """Enqueue must be done as a registered client: the X-Client-Key must resolve,
    and any declared client_id must match it. Returns the authenticated client_id
    (what gets stored as the task owner)."""
    if not authed:
        raise HTTPException(401, "register a client (POST /v1/clients) and send its key as X-Client-Key")
    if declared and declared != authed:
        raise HTTPException(403, "client_id does not match the authenticated X-Client-Key")
    return authed


def _owned(tid: str, token: str | None, client_id: str | None) -> dict:
    """Fetch a task and authorize access two ways: the per-task pull token
    (X-Task-Token, for sharing a single task) OR the owning client's authenticated
    key (X-Client-Key, so a client reaches all of its own tasks). Fails CLOSED: a
    task with neither a token match nor an owning-client match is inaccessible.
    404 unknown id, 403 otherwise. Constant-time token compare."""
    t = store.get(tid)
    if not t:
        raise HTTPException(404, "unknown task id")
    stored = t.get("token")
    if stored and token and hmac.compare_digest(str(token), str(stored)):
        return t
    owner = t.get("client_id")
    if client_id and owner and client_id == owner:
        return t
    raise HTTPException(403, "provide the task's X-Task-Token, or the owning client's X-Client-Key")


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


@app.post("/v1/clients", response_model=ClientCredentials, status_code=201)
def register_client(req: ClientCreate, _=Depends(_auth)) -> ClientCredentials:
    """Register a client_id and receive a secret client_key (shown ONCE). Send the
    key as 'X-Client-Key' to enqueue tasks and to list/fetch your own tasks. The
    client_id is first-come-first-served; 409 if already taken."""
    key = store.create_client(req.client_id)
    if key is None:
        log.info("register REJECTED client_id=%s (already taken)", req.client_id)
        raise HTTPException(409, "client_id already registered")
    log.info("registered client_id=%s", req.client_id)
    return ClientCredentials(client_id=req.client_id, client_key=key)


@app.post("/v1/tasks", response_model=TaskRef)
def create_task(req: CreateTaskRequest, client: str | None = Depends(_client_auth),
                _=Depends(_auth)) -> TaskRef:
    from tts_serve.sources import classify
    owner = _require_client(client, req.client_id)
    token = secrets.token_urlsafe(24)
    stype = classify(req.source)
    tid = store.create(req.source, stype, req.options(), client_id=owner, token=token)
    log.info("enqueued task %s (client=%s type=%s opts=%s)", tid, owner, stype, req.options())
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
    client: str | None = Depends(_client_auth),
    _=Depends(_auth),
) -> TaskRef:
    owner = _require_client(client, (client_id or "").strip() or None)
    req = CreateTaskRequest(source="upload", client_id=owner, hotwords=hotwords,
                            speakers=speakers, reid=reid, names=names, clip=clip, name=name)
    token = secrets.token_urlsafe(24)
    tid = _save_upload(file.filename, await file.read(), req.options(), owner, token)
    log.info("enqueued upload task %s (client=%s file=%s opts=%s)", tid, owner, file.filename, req.options())
    return TaskRef(task_id=tid, status="queued", pull_token=token)


@app.get("/v1/tasks/{tid}", response_model=TaskStatus)
def get_task(tid: str, token: str | None = Depends(_pull_token),
             client: str | None = Depends(_client_auth), _=Depends(_auth)) -> TaskStatus:
    t = _owned(tid, token, client)
    return TaskStatus(task_id=t["id"], status=t["status"], stage=t["stage"],
                      client_id=t["client_id"], source_type=t["source_type"], error=t["error"],
                      created_at=t["created_at"], updated_at=t["updated_at"],
                      options=t["options"])


@app.get("/v1/tasks", response_model=TaskList)
def list_tasks(request: Request, client_id: str | None = Query(default=None),
               client: str | None = Depends(_client_auth), _=Depends(_auth)) -> TaskList:
    # A registered client (X-Client-Key) sees ONLY its own tasks. Admin (bearer)
    # sees all, optionally filtered by ?client_id. Anyone else is refused.
    if client:
        return TaskList(tasks=[QueuedItem(**r) for r in store.list_tasks(client_id=client)])
    if _is_admin(request):
        return TaskList(tasks=[QueuedItem(**r) for r in store.list_tasks(client_id=client_id)])
    raise HTTPException(403, "send X-Client-Key to list your tasks (or the admin bearer for all)")


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
def delete_task(tid: str, token: str | None = Depends(_pull_token),
                client: str | None = Depends(_client_auth), _=Depends(_auth)) -> DeleteResult:
    t = _owned(tid, token, client)
    if t["status"] == "running":
        raise HTTPException(409, "cannot remove a running task")
    if not store.delete(tid):  # raced to 'running' between the check and here
        raise HTTPException(409, "cannot remove a running task")
    log.info("deleted task %s (was %s)", tid, t["status"])
    return DeleteResult(deleted=tid, was=t["status"])


@app.post("/v1/tasks/{tid}/retry", response_model=TaskRef)
def retry_task(tid: str, token: str | None = Depends(_pull_token),
               client: str | None = Depends(_client_auth), _=Depends(_auth)) -> TaskRef:
    t = _owned(tid, token, client)
    if t["status"] not in ("failed", "cancelled"):
        raise HTTPException(409, f"can only retry failed/cancelled (status={t['status']})")
    store.update(tid, status="queued", stage=None, error=None)
    log.info("retried task %s (was %s)", tid, t["status"])
    return TaskRef(task_id=tid, status="queued")


@app.get("/v1/tasks/{tid}/artifact",
         responses={200: {"content": {"application/zip": {}}}, 409: {}, 404: {}})
def get_artifact(tid: str, token: str | None = Depends(_pull_token),
                 client: str | None = Depends(_client_auth), _=Depends(_auth)):
    t = _owned(tid, token, client)
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
            "0. POST /v1/clients (JSON {client_id}) ONCE -> {client_id, client_key}. SAVE the "
            "client_key (shown once); send it as header 'X-Client-Key' to enqueue and to list/"
            "fetch your own tasks.",
            "1. POST /v1/tasks (JSON {source, client_id}, header X-Client-Key) for a URL, or POST "
            "/v1/tasks/upload (multipart file + client_id, header X-Client-Key) -> {task_id, "
            "pull_token}. SAVE the pull_token: returned only here; it shares a single task.",
            "2. GET /v1/tasks/{task_id} with EITHER the token (header 'X-Task-Token: <pull_token>' "
            "or ?token=) OR your 'X-Client-Key'; poll 'status' until 'done' (stop on 'failed'/"
            "'cancelled'); 'stage' shows progress while status=='running'",
            "3. GET THE RESULT: once status=='done', GET /v1/tasks/{task_id}/artifact (same "
            "auth) -> a .zip (HTTP 409 if you fetch before it is done, 404 if expired). Unzip "
            "it: transcript.txt = readable transcript; subtitle.srt = SRT captions; "
            "segments.json = machine format [{start,end,speaker,text}]; meta.json = run info.",
            "List your own jobs anytime: GET /v1/tasks with header 'X-Client-Key' -> only your tasks.",
        ],
        "result": "The deliverable is the artifact zip from GET /v1/tasks/{task_id}/artifact, "
                  "available ONLY when status=='done' (poll step 2 first; 409 before done, 404 "
                  "after retention expiry). Authorize with the same X-Task-Token or owner "
                  "X-Client-Key. The zip contains: transcript.txt (human-readable), subtitle.srt "
                  "(captions), segments.json (canonical: {source, duration_s, speakers[], "
                  "segments[]{start,end,speaker,text}}), meta.json. curl example: "
                  "curl -H 'X-Task-Token: <pull_token>' -OJ <base>/v1/tasks/<task_id>/artifact",
        "identity": "Register a client (POST /v1/clients) to get a secret client_key. Send it as "
                    "'X-Client-Key' to enqueue (the task is owned by that authenticated client) and "
                    "to GET /v1/tasks (lists ONLY your tasks). The body client_id is a label and "
                    "must match your key. ACCESS to a single task (poll/pull/delete/retry) takes "
                    "EITHER your X-Client-Key (owner) OR the per-task pull_token from create "
                    "(X-Task-Token / ?token=, for sharing one task). Fails closed otherwise.",
        "concurrency": "One task runs at a time (single GPU worker, FIFO queue; the model "
                       "loads on demand, so the first task after an idle period adds a brief "
                       "load — watch for stage=loading_model).",
        "status_values": ["queued", "running", "done", "failed", "cancelled"],
        "stage_values": ["loading_model", "downloading", "preprocessing", "transcribing", "postprocessing", "done"],
        "task_options": {
            "hotwords": "comma-separated names/terms to bias ASR",
            "speakers": "int, expected speaker count (improves diarization / re-id)",
            "reid": "bool, voiceprint speaker re-identification (use with speakers)",
            "names": "bool, suggest real speaker names from self-intros (LLM)",
            "clip": "START-END seconds, e.g. '0-600'",
            "name": "meeting name (default derived from source)",
        },
        "endpoints": [
            {"method": "POST", "path": "/v1/clients", "body": "JSON {client_id}",
             "returns": "{client_id, client_key}  (client_key shown once)",
             "example": "curl -H 'content-type: application/json' -d '{\"client_id\":\"alice\"}' <base>/v1/clients"},
            {"method": "POST", "path": "/v1/tasks", "body": "JSON CreateTaskRequest {source, client_id, ...options} + header X-Client-Key",
             "returns": "{task_id, status, pull_token}",
             "example": "curl -H 'content-type: application/json' -H 'X-Client-Key: <key>' -d '{\"source\":\"https://youtu.be/ID\",\"client_id\":\"alice\",\"clip\":\"0-600\",\"names\":true}' <base>/v1/tasks"},
            {"method": "POST", "path": "/v1/tasks/upload", "body": "multipart file=@audio + client_id + options; header X-Client-Key",
             "returns": "{task_id, status, pull_token}",
             "example": "curl -H 'X-Client-Key: <key>' -F file=@meeting.wav -F client_id=alice -F speakers=2 <base>/v1/tasks/upload"},
            {"method": "GET", "path": "/v1/tasks/{id}", "returns": "TaskStatus (needs X-Task-Token or owner X-Client-Key)"},
            {"method": "GET", "path": "/v1/tasks/{id}/artifact", "returns": "application/zip (200/409/404; needs X-Task-Token or owner X-Client-Key)"},
            {"method": "GET", "path": "/v1/tasks", "returns": "TaskList — your tasks (X-Client-Key) or all (admin bearer)",
             "example": "curl -H 'X-Client-Key: <key>' <base>/v1/tasks"},
            {"method": "GET", "path": "/v1/queue", "returns": "QueueStatus (admin: requires bearer)"},
            {"method": "DELETE", "path": "/v1/tasks/{id}", "returns": "remove queued/done/failed (needs X-Task-Token or owner X-Client-Key; 409 if running)"},
            {"method": "POST", "path": "/v1/tasks/{id}/retry", "returns": "requeue failed/cancelled (needs X-Task-Token or owner X-Client-Key)"},
        ],
        "auth": "Enqueue + 'list my tasks' require a registered client's X-Client-Key. A single "
                "task is reachable by its owner (X-Client-Key) or its per-task pull_token "
                "(X-Task-Token). Cross-client listing (/v1/tasks all, /v1/queue) requires the "
                "TTS_SERVE_API_KEY bearer when configured.",
        "spec": {"openapi": "/openapi.json", "swagger_ui": "/docs"},
    }


def main() -> None:
    import uvicorn
    setup_logging("api")  # configure before uvicorn so its loggers use our handlers
    level = os.environ.get("TTS_SERVE_LOG_LEVEL", "INFO").lower()
    uvicorn.run(app, host=os.environ.get("TTS_SERVE_HOST", "0.0.0.0"),
                port=int(os.environ.get("TTS_SERVE_PORT", "39999")),
                log_config=None, log_level=level)


if __name__ == "__main__":
    main()

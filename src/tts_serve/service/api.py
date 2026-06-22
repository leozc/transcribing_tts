"""FastAPI front end: queue a transcription, poll it, download the artifact zip.

    POST /v1/tasks         multipart file=@audio  OR  json {"source": "<url>", ...opts}
    GET  /v1/tasks/{id}    status / stage / error
    GET  /v1/tasks/{id}/artifact   zip of results (200 done / 409 not-ready / 404)
    GET  /v1/tasks         recent tasks
    GET  /healthz

Optional bearer auth: set env TTS_SERVE_API_KEY to require Authorization: Bearer <key>.
The GPU worker (service/worker.py) does the actual transcription.
"""
from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from tts_serve.service import store

_OPT_KEYS = ("hotwords", "speakers", "reid", "names", "clip", "name")
_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".mp4", ".webm", ".mov", ".mkv", ".aac"}

app = FastAPI(title="tts_serve", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    store.init()


async def _auth(request: Request) -> None:
    key = os.environ.get("TTS_SERVE_API_KEY")
    if not key:
        return
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {key}":
        raise HTTPException(401, "missing/invalid bearer token")


def _coerce_opts(raw: dict) -> dict:
    opts = {}
    for k in _OPT_KEYS:
        if k not in raw or raw[k] in (None, ""):
            continue
        v = raw[k]
        if k == "speakers":
            v = int(v)
        elif k in ("reid", "names"):
            v = str(v).lower() in ("1", "true", "yes", "on") if not isinstance(v, bool) else v
        opts[k] = v
    return opts


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/v1/tasks")
async def create_task(request: Request, _=Depends(_auth)) -> dict:
    ct = request.headers.get("content-type", "")
    if ct.startswith("multipart/"):
        form = await request.form()
        opts = _coerce_opts({k: form.get(k) for k in _OPT_KEYS})
        upload = form.get("file")
        source_field = form.get("source")
        # duck-type: form may yield starlette's UploadFile (not the fastapi subclass)
        if upload is not None and hasattr(upload, "read") and getattr(upload, "filename", None):
            ext = Path(upload.filename).suffix.lower() or ".bin"
            if ext not in _AUDIO_EXTS:
                ext = ".bin"
            tid = store.create("", "file", opts)
            dst = store.task_dir(tid) / f"input{ext}"
            dst.write_bytes(await upload.read())
            store.update(tid, source=str(dst))
            return {"task_id": tid, "status": "queued"}
        if source_field:
            return _create_url(str(source_field), opts)
        raise HTTPException(400, "provide a 'file' upload or a 'source' URL")
    # JSON body
    body = await request.json()
    source = body.get("source")
    if not source:
        raise HTTPException(400, "json body needs 'source' (use multipart to upload a file)")
    return _create_url(str(source), _coerce_opts(body))


def _create_url(source: str, opts: dict) -> dict:
    from tts_serve.sources import classify
    tid = store.create(source, classify(source), opts)
    return {"task_id": tid, "status": "queued"}


@app.get("/v1/tasks/{tid}")
def get_task(tid: str, _=Depends(_auth)) -> dict:
    t = store.get(tid)
    if not t:
        raise HTTPException(404, "unknown task id")
    return {
        "task_id": t["id"], "status": t["status"], "stage": t["stage"],
        "source_type": t["source_type"], "error": t["error"],
        "created_at": t["created_at"], "updated_at": t["updated_at"],
        "options": t["options"],
    }


@app.get("/v1/tasks")
def list_tasks(_=Depends(_auth)) -> dict:
    return {"tasks": store.list_tasks()}


@app.get("/v1/tasks/{tid}/artifact")
def get_artifact(tid: str, _=Depends(_auth)):
    t = store.get(tid)
    if not t:
        raise HTTPException(404, "unknown task id")
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
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{tid}.zip"'},
    )


@app.get("/v1/queue")
def queue(_=Depends(_auth)) -> dict:
    """Admin: what's running now + the pending queue + status counts."""
    running = store.running_task()
    return {
        "running": {"task_id": running["id"], "stage": running["stage"],
                    "source_type": running["source_type"], "updated_at": running["updated_at"]}
        if running else None,
        "queued": store.list_tasks(status="queued"),
        "counts": store.counts(),
    }


@app.delete("/v1/tasks/{tid}")
def delete_task(tid: str, _=Depends(_auth)) -> dict:
    """Admin: remove a task (cancel a queued one / clean up done/failed). A
    running task can't be removed mid-flight (409)."""
    t = store.get(tid)
    if not t:
        raise HTTPException(404, "unknown task id")
    if t["status"] == "running":
        raise HTTPException(409, "cannot remove a running task")
    store.delete(tid)
    return {"deleted": tid, "was": t["status"]}


@app.post("/v1/tasks/{tid}/retry")
def retry_task(tid: str, _=Depends(_auth)) -> dict:
    """Admin: requeue a failed/cancelled task."""
    t = store.get(tid)
    if not t:
        raise HTTPException(404, "unknown task id")
    if t["status"] not in ("failed", "cancelled"):
        raise HTTPException(409, f"can only retry failed/cancelled (status={t['status']})")
    store.update(tid, status="queued", stage=None, error=None)
    return {"task_id": tid, "status": "queued"}


@app.get("/agent_info")
def agent_info() -> dict:
    """Concise, agent-facing description of how to drive this API.
    (For the full machine spec see /openapi.json and the Swagger UI at /docs.)"""
    return {
        "service": "tts_serve",
        "summary": "Transcribe audio/video (file, YouTube, Google Drive, S3, or URL) into a "
                   "speaker-attributed, timestamped transcript. Async: queue a task, poll it, "
                   "download a zip of artifacts.",
        "workflow": [
            "1. POST /v1/tasks with a file upload OR a source URL -> {task_id}",
            "2. GET /v1/tasks/{task_id} and poll until status == 'done' (or 'failed')",
            "3. GET /v1/tasks/{task_id}/artifact -> zip (transcript.txt, subtitle.srt, segments.json, meta.json)",
        ],
        "concurrency": "One task runs at a time (single resident GPU worker, FIFO queue).",
        "status_lifecycle": ["queued", "downloading", "preprocessing", "transcribing",
                              "postprocessing", "done", "failed", "cancelled"],
        "task_options": {
            "hotwords": "comma-separated names/terms to bias ASR",
            "speakers": "int, expected speaker count (improves diarization / re-id)",
            "reid": "bool, voiceprint speaker re-identification (use with speakers)",
            "names": "bool, suggest real speaker names from self-intros (LLM)",
            "clip": "START-END seconds, e.g. '0-600'",
            "name": "meeting name (default derived from source)",
        },
        "endpoints": [
            {"method": "POST", "path": "/v1/tasks",
             "body": "multipart form with file=@audio + option fields, OR JSON {source, ...options}",
             "returns": "{task_id, status}",
             "examples": [
                 "curl -F file=@meeting.wav -F speakers=2 -F reid=true <base>/v1/tasks",
                 "curl -H 'content-type: application/json' -d '{\"source\":\"https://youtu.be/ID\",\"names\":true}' <base>/v1/tasks",
             ]},
            {"method": "GET", "path": "/v1/tasks/{id}", "returns": "{status, stage, error, ...}"},
            {"method": "GET", "path": "/v1/tasks/{id}/artifact", "returns": "application/zip (200 done / 409 not-ready / 404)"},
            {"method": "GET", "path": "/v1/tasks", "returns": "recent tasks"},
            {"method": "GET", "path": "/v1/queue", "returns": "{running, queued[], counts}"},
            {"method": "DELETE", "path": "/v1/tasks/{id}", "returns": "remove a queued/done/failed task (409 if running)"},
            {"method": "POST", "path": "/v1/tasks/{id}/retry", "returns": "requeue a failed/cancelled task"},
        ],
        "auth": "If TTS_SERVE_API_KEY is set, send 'Authorization: Bearer <key>'.",
        "spec": {"openapi": "/openapi.json", "swagger_ui": "/docs"},
    }


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=os.environ.get("TTS_SERVE_HOST", "0.0.0.0"),
                port=int(os.environ.get("TTS_SERVE_PORT", "8080")))


if __name__ == "__main__":
    main()

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

import io
import os
import zipfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
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


async def _auth(request: Request) -> None:
    key = os.environ.get("TTS_SERVE_API_KEY")
    if key and request.headers.get("authorization", "") != f"Bearer {key}":
        raise HTTPException(401, "missing/invalid bearer token")


def _save_upload(filename: str, data: bytes, opts: dict) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in _AUDIO_EXTS:
        ext = ".bin"
    tid = store.create("", "file", opts)
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
    tid = store.create(req.source, classify(req.source), req.options())
    return TaskRef(task_id=tid, status="queued")


@app.post("/v1/tasks/upload", response_model=TaskRef)
async def upload_task(
    file: UploadFile = File(...),
    hotwords: str | None = Form(None),
    speakers: int | None = Form(None),
    reid: bool = Form(False),
    names: bool = Form(False),
    clip: str | None = Form(None),
    name: str | None = Form(None),
    _=Depends(_auth),
) -> TaskRef:
    req = CreateTaskRequest(source="upload", hotwords=hotwords, speakers=speakers,
                            reid=reid, names=names, clip=clip, name=name)
    tid = _save_upload(file.filename, await file.read(), req.options())
    return TaskRef(task_id=tid, status="queued")


@app.get("/v1/tasks/{tid}", response_model=TaskStatus)
def get_task(tid: str, _=Depends(_auth)) -> TaskStatus:
    t = store.get(tid)
    if not t:
        raise HTTPException(404, "unknown task id")
    return TaskStatus(task_id=t["id"], status=t["status"], stage=t["stage"],
                      source_type=t["source_type"], error=t["error"],
                      created_at=t["created_at"], updated_at=t["updated_at"],
                      options=t["options"])


@app.get("/v1/tasks", response_model=TaskList)
def list_tasks(_=Depends(_auth)) -> TaskList:
    return TaskList(tasks=[QueuedItem(**r) for r in store.list_tasks()])


@app.get("/v1/queue", response_model=QueueStatus)
def queue(_=Depends(_auth)) -> QueueStatus:
    r = store.running_task()
    return QueueStatus(
        running=RunningInfo(task_id=r["id"], stage=r["stage"],
                            source_type=r["source_type"], updated_at=r["updated_at"]) if r else None,
        queued=[QueuedItem(**q) for q in store.list_tasks(status="queued")],
        counts=store.counts(),
    )


@app.delete("/v1/tasks/{tid}", response_model=DeleteResult)
def delete_task(tid: str, _=Depends(_auth)) -> DeleteResult:
    t = store.get(tid)
    if not t:
        raise HTTPException(404, "unknown task id")
    if t["status"] == "running":
        raise HTTPException(409, "cannot remove a running task")
    store.delete(tid)
    return DeleteResult(deleted=tid, was=t["status"])


@app.post("/v1/tasks/{tid}/retry", response_model=TaskRef)
def retry_task(tid: str, _=Depends(_auth)) -> TaskRef:
    t = store.get(tid)
    if not t:
        raise HTTPException(404, "unknown task id")
    if t["status"] not in ("failed", "cancelled"):
        raise HTTPException(409, f"can only retry failed/cancelled (status={t['status']})")
    store.update(tid, status="queued", stage=None, error=None)
    return TaskRef(task_id=tid, status="queued")


@app.get("/v1/tasks/{tid}/artifact",
         responses={200: {"content": {"application/zip": {}}}, 409: {}, 404: {}})
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
            "1. POST /v1/tasks (JSON {source}) for a URL, or POST /v1/tasks/upload (multipart file) -> {task_id}",
            "2. GET /v1/tasks/{task_id}; poll the 'status' field until 'done' (stop on 'failed'/'cancelled'); "
            "'stage' shows progress while status=='running'",
            "3. GET /v1/tasks/{task_id}/artifact -> zip (transcript.txt, subtitle.srt, segments.json, meta.json)",
        ],
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
            {"method": "POST", "path": "/v1/tasks", "body": "JSON CreateTaskRequest {source, ...options}",
             "example": "curl -H 'content-type: application/json' -d '{\"source\":\"https://youtu.be/ID\",\"clip\":\"0-600\",\"names\":true}' <base>/v1/tasks"},
            {"method": "POST", "path": "/v1/tasks/upload", "body": "multipart file=@audio + option form fields",
             "example": "curl -F file=@meeting.wav -F speakers=2 -F reid=true <base>/v1/tasks/upload"},
            {"method": "GET", "path": "/v1/tasks/{id}", "returns": "TaskStatus"},
            {"method": "GET", "path": "/v1/tasks/{id}/artifact", "returns": "application/zip (200/409/404)"},
            {"method": "GET", "path": "/v1/tasks", "returns": "TaskList"},
            {"method": "GET", "path": "/v1/queue", "returns": "QueueStatus {running, queued[], counts}"},
            {"method": "DELETE", "path": "/v1/tasks/{id}", "returns": "remove queued/done/failed (409 if running)"},
            {"method": "POST", "path": "/v1/tasks/{id}/retry", "returns": "requeue failed/cancelled"},
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

"""Pydantic request/response models — make /openapi.json fully typed so generated
clients get real types (not bare dicts)."""
from __future__ import annotations

from pydantic import BaseModel

_OPT_FIELDS = ("hotwords", "speakers", "reid", "names", "clip", "name")


class CreateTaskRequest(BaseModel):
    source: str                       # file path / YouTube / Drive / S3 / http URL
    hotwords: str | None = None
    speakers: int | None = None
    reid: bool = False
    names: bool = False
    clip: str | None = None           # "START-END" seconds, e.g. "0-600"
    name: str | None = None

    def options(self) -> dict:
        out = {}
        for k in _OPT_FIELDS:
            v = getattr(self, k)
            if v in (None, False, ""):
                continue
            out[k] = v
        return out


class TaskRef(BaseModel):
    task_id: str
    status: str


class TaskStatus(BaseModel):
    task_id: str
    status: str
    stage: str | None = None
    source_type: str | None = None
    error: str | None = None
    created_at: float | None = None
    updated_at: float | None = None
    options: dict = {}


class QueuedItem(BaseModel):
    id: str
    status: str
    stage: str | None = None
    source_type: str | None = None
    created_at: float | None = None
    updated_at: float | None = None


class RunningInfo(BaseModel):
    task_id: str
    stage: str | None = None
    source_type: str | None = None
    updated_at: float | None = None


class QueueStatus(BaseModel):
    running: RunningInfo | None = None
    queued: list[QueuedItem] = []
    counts: dict = {}


class TaskList(BaseModel):
    tasks: list[QueuedItem] = []


class DeleteResult(BaseModel):
    deleted: str
    was: str


class Health(BaseModel):
    ok: bool

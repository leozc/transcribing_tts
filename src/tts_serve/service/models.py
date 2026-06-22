"""Pydantic request/response models — make /openapi.json fully typed so generated
clients get real types (not bare dicts)."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

_OPT_FIELDS = ("hotwords", "speakers", "reid", "names", "clip", "name")


class CreateTaskRequest(BaseModel):
    source: str                       # file path / YouTube / Drive / S3 / http URL
    client_id: str = Field(min_length=1)   # caller identity; required, and used to pull
    hotwords: str | None = None

    @field_validator("client_id", "source")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v
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
    pull_token: str | None = None   # returned ONLY at create; required to poll/pull/delete/retry


class TaskStatus(BaseModel):
    task_id: str
    status: str
    stage: str | None = None
    client_id: str | None = None
    source_type: str | None = None
    error: str | None = None
    created_at: float | None = None
    updated_at: float | None = None
    options: dict = {}


class QueuedItem(BaseModel):
    id: str
    status: str
    stage: str | None = None
    client_id: str | None = None
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

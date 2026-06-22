"""Service tests — store + API, no GPU (transcription is simulated)."""
import io
import json
import zipfile

import pytest


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    # point the store at a temp data dir BEFORE importing it
    monkeypatch.setenv("TTS_SERVE_DATA", str(tmp_path))
    import importlib
    from tts_serve.service import store as store_mod
    importlib.reload(store_mod)
    from tts_serve.service import api as api_mod
    importlib.reload(api_mod)
    api_mod.store = store_mod
    store_mod.init()
    from fastapi.testclient import TestClient
    return store_mod, api_mod, TestClient(api_mod.app)


# ---------- store ----------

def test_store_crud_and_claim(svc):
    store, _, _ = svc
    a = store.create("s3://b/a.wav", "s3", {"reid": True})
    b = store.create("https://youtu.be/x", "youtube", {})
    assert store.get(a)["status"] == "queued"
    assert store.get(a)["options"] == {"reid": True}
    # FIFO claim: a before b
    c1 = store.claim_next_queued()
    assert c1["id"] == a and c1["status"] == "running"
    c2 = store.claim_next_queued()
    assert c2["id"] == b
    assert store.claim_next_queued() is None  # nothing left queued
    store.update(a, status="done", stage="done")
    assert store.get(a)["status"] == "done"


# ---------- API ----------

def test_create_via_url_and_status(svc):
    _, _, client = svc
    r = client.post("/v1/tasks", json={"source": "https://youtu.be/abc123", "speakers": 2, "reid": True})
    assert r.status_code == 200
    tid = r.json()["task_id"]
    assert r.json()["status"] == "queued"
    s = client.get(f"/v1/tasks/{tid}").json()
    assert s["status"] == "queued" and s["source_type"] == "youtube"
    assert s["options"]["speakers"] == 2 and s["options"]["reid"] is True


def test_create_via_upload(svc):
    store, _, client = svc
    r = client.post("/v1/tasks", files={"file": ("meeting.wav", b"RIFFxxxx", "audio/wav")})
    assert r.status_code == 200
    tid = r.json()["task_id"]
    t = store.get(tid)
    assert t["source_type"] == "file"
    assert t["source"].endswith("input.wav")


def test_create_requires_input(svc):
    _, _, client = svc
    assert client.post("/v1/tasks", json={}).status_code == 400


def test_unknown_task_404(svc):
    _, _, client = svc
    assert client.get("/v1/tasks/nope").status_code == 404
    assert client.get("/v1/tasks/nope/artifact").status_code == 404


def test_artifact_409_until_done_then_zip(svc):
    store, _, client = svc
    tid = client.post("/v1/tasks", json={"source": "https://youtu.be/x"}).json()["task_id"]
    # not done yet
    assert client.get(f"/v1/tasks/{tid}/artifact").status_code == 409
    # simulate the worker finishing
    rdir = store.results_dir(tid)
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "transcript.txt").write_text("hello world")
    (rdir / "segments.json").write_text('{"segments":[]}')
    store.update(tid, status="done", stage="done")
    resp = client.get(f"/v1/tasks/{tid}/artifact")
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    assert set(z.namelist()) == {"transcript.txt", "segments.json"}
    assert z.read("transcript.txt") == b"hello world"


def test_bearer_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("TTS_SERVE_DATA", str(tmp_path))
    monkeypatch.setenv("TTS_SERVE_API_KEY", "secret")
    import importlib
    from tts_serve.service import store as store_mod
    importlib.reload(store_mod)
    from tts_serve.service import api as api_mod
    importlib.reload(api_mod)
    api_mod.store = store_mod
    store_mod.init()
    from fastapi.testclient import TestClient
    client = TestClient(api_mod.app)
    assert client.get("/v1/tasks/x").status_code == 401            # no token
    assert client.get("/v1/tasks/x", headers={"Authorization": "Bearer secret"}).status_code == 404  # ok auth, unknown id
    assert client.get("/healthz").status_code == 200               # health is open

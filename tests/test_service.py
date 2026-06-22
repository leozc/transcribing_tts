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

def test_store_single_concurrency_fifo(svc):
    store, _, _ = svc
    a = store.create("s3://b/a.wav", "s3", {"reid": True})
    b = store.create("https://youtu.be/x", "youtube", {})
    assert store.get(a)["options"] == {"reid": True}
    # FIFO: a claimed first
    assert store.claim_next_queued()["id"] == a
    # single concurrency: nothing else claimed while a is running
    assert store.claim_next_queued() is None
    store.update(a, status="done", stage="done")
    # now b can run
    assert store.claim_next_queued()["id"] == b
    assert store.claim_next_queued() is None


def test_reclaim_stale(svc):
    store, _, _ = svc
    a = store.create("s3://b/a.wav", "s3", {})
    store.claim_next_queued()
    assert store.get(a)["status"] == "running"
    assert store.reclaim_stale() == 1            # orphaned running -> queued
    assert store.get(a)["status"] == "queued"
    assert store.claim_next_queued()["id"] == a  # claimable again


def test_queue_admin_and_delete(svc):
    store, _, client = svc
    a = client.post("/v1/tasks", json={"source": "https://youtu.be/a"}).json()["task_id"]
    b = client.post("/v1/tasks", json={"source": "https://youtu.be/b"}).json()["task_id"]
    q = client.get("/v1/queue").json()
    assert q["running"] is None and len(q["queued"]) == 2 and q["counts"]["queued"] == 2
    store.claim_next_queued()  # simulate worker taking a
    q = client.get("/v1/queue").json()
    assert q["running"]["task_id"] == a and len(q["queued"]) == 1
    assert client.delete(f"/v1/tasks/{b}").status_code == 200       # remove queued b
    assert client.get(f"/v1/tasks/{b}").status_code == 404
    assert client.delete(f"/v1/tasks/{a}").status_code == 409       # can't delete running


def test_retry(svc):
    store, _, client = svc
    tid = client.post("/v1/tasks", json={"source": "https://youtu.be/x"}).json()["task_id"]
    store.update(tid, status="failed", error="boom")
    assert client.post(f"/v1/tasks/{tid}/retry").status_code == 200
    assert client.get(f"/v1/tasks/{tid}").json()["status"] == "queued"
    assert client.post(f"/v1/tasks/{tid}/retry").status_code == 409  # not failed/cancelled now


def test_agent_info_and_openapi(svc):
    _, _, client = svc
    r = client.get("/agent_info").json()
    assert r["service"] == "tts_serve" and r["workflow"] and r["spec"]["openapi"] == "/openapi.json"
    assert client.get("/openapi.json").status_code == 200


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
    r = client.post("/v1/tasks/upload",
                    files={"file": ("meeting.wav", b"RIFFxxxx", "audio/wav")},
                    data={"speakers": "2", "reid": "true"})
    assert r.status_code == 200
    tid = r.json()["task_id"]
    t = store.get(tid)
    assert t["source_type"] == "file" and t["source"].endswith("input.wav")
    assert t["options"]["speakers"] == 2 and t["options"]["reid"] is True


def test_create_requires_source(svc):
    _, _, client = svc
    assert client.post("/v1/tasks", json={}).status_code == 422  # pydantic: source required


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
